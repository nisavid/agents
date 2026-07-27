from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hindsight_api.extensions.tenant import AuthenticationError
except ModuleNotFoundError:
    FastAPI = None
    TestClient = None
    AuthenticationError = RuntimeError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hindsight_memory_control_plane.accidental_replay import (  # noqa: E402
    _document_descriptor,
)


class _TenantContext:
    schema_name = "public"


class _Tenant:
    schema = "public"


class _TenantExtension:
    async def authenticate(self, context):
        if context.api_key != "test-token":
            raise AuthenticationError("authentication failed")
        return _TenantContext()

    async def list_tenants(self):
        return [_Tenant()]


class _Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _Acquire(self.connection)


class _Connection:
    def __init__(self):
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self.generation = 7
        self.controller_state: dict[str, str] = {}
        self.controller_state_raw_override = None
        self.replay_closeout_receipts = {}
        self.missing_trigger_count = 0
        self.snapshot_scope_count = 0
        self.snapshot_webhooks = []
        self.transactions: list[dict[str, object]] = []
        self.banks = [
            "codex",
            "codex-memory-migration-v1-20260708",
            "engineering",
        ]
        self.source_documents = []

    class _Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    def transaction(self, **options):
        self.transactions.append(options)
        return self._Transaction()

    async def execute(self, statement, *arguments):
        self.statements.append((statement, arguments))
        if "replay_closeout_receipts =" in statement:
            self.replay_closeout_receipts[arguments[0]] = json.loads(
                arguments[1]
            )
            return "UPDATE 1"
        if statement.lstrip().startswith("DELETE FROM"):
            self.generation += 1
        return "UPDATE 1"

    async def fetch(self, statement, *arguments):
        self.statements.append((statement, arguments))
        if (
            'FROM "public".banks' in statement
            and "SELECT bank_id" in statement
        ):
            return [{"bank_id": bank_id} for bank_id in self.banks]
        if (
            'FROM "public".documents' in statement
            and "original_text" in statement
            and "bank_id = 'codex'" in statement
        ):
            return list(self.source_documents)
        if "SELECT DISTINCT scope" in statement:
            return [
                {"scope": f"scope-{index}"}
                for index in range(self.snapshot_scope_count)
            ]
        if "SELECT DISTINCT tag" in statement:
            return []
        if "FROM \"public\".documents AS document" in statement:
            return []
        if "FROM \"public\".mental_models AS model" in statement:
            return []
        if "FROM \"public\".directives AS directive" in statement:
            return []
        if "FROM \"public\".invalidated_memory_units" in statement:
            return []
        if "FROM \"public\".webhooks" in statement:
            return list(self.snapshot_webhooks)
        if "FROM \"public\".async_operations" in statement:
            return []
        if "pg_catalog.pg_class" in statement:
            return [
                {"table_name": "banks"},
                {"table_name": "documents"},
                {"table_name": "async_operations"},
            ]
        raise AssertionError(f"unexpected fetch statement: {statement}")

    async def fetchrow(self, statement, *arguments):
        self.statements.append((statement, arguments))
        if "FOR UPDATE" in statement and "SELECT generation" in statement:
            return {
                "generation": self.generation,
                "replay_closeout_receipts":
                    self.replay_closeout_receipts,
            }
        if (
            "AS memory_units" in statement
            and "bank_id = 'codex'" in statement
        ):
            return {
                "memory_units": 0,
                "entities": 0,
                "documents": len(self.source_documents),
            }
        if (
            "SELECT generation" in statement
            and "WHERE singleton" in statement
            and "missing_trigger_count" not in statement
            and "controller_state" not in statement
        ):
            return {"generation": self.generation}
        if "SELECT bank_id, config" in statement:
            return {"bank_id": arguments[0], "config": {}}
        if "AS total_documents" in statement:
            return {
                "bank_id": arguments[0],
                "total_documents": 0,
                "total_memories": 0,
                "total_observations": 0,
                "total_entities": 0,
                "total_mental_models": 0,
                "total_directives": 0,
                "total_invalidated_memories": 0,
            }
        if "RETURNING generation" in statement:
            state = json.loads(arguments[0])
            changed = state != self.controller_state
            if changed:
                self.controller_state = state
                self.generation += 1
            return {"generation": self.generation, "changed": changed}
        if (
            "missing_trigger_count" in statement
            or "SELECT controller_state" in statement
        ):
            return {
                "generation": self.generation,
                "controller_state": (
                    self.controller_state_raw_override
                    if self.controller_state_raw_override is not None
                    else json.dumps(self.controller_state)
                ),
                "missing_trigger_count": self.missing_trigger_count,
            }
        raise AssertionError(f"unexpected fetchrow statement: {statement}")

    async def fetchval(self, statement, *arguments):
        self.statements.append((statement, arguments))
        if (
            'DELETE FROM "public".banks' in statement
            and "bank_id = 'codex'" in statement
        ):
            self.banks.remove("codex")
            self.generation += 1
            return "00000000-0000-4000-8000-000000000001"
        raise AssertionError(f"unexpected fetchval statement: {statement}")


class _Backend:
    def __init__(self, connection):
        self.connection = connection
        self.ops = object()

    def acquire(self):
        return _Acquire(self.connection)


class _BankStatsCache:
    def __init__(self):
        self.invalidations = []

    async def invalidate(self, schema, bank_id):
        self.invalidations.append((schema, bank_id))


class _Memory:
    def __init__(self):
        self.connection = _Connection()
        self.tenant_extension = _TenantExtension()
        self._bank_stats_cache = _BankStatsCache()

    async def _get_pool(self):
        return _Pool(self.connection)

    async def _get_backend(self):
        return _Backend(self.connection)


@unittest.skipUnless(
    FastAPI is not None and TestClient is not None,
    "managed Hindsight API dependencies are unavailable",
)
class MigrationGenerationHttpExtensionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = importlib.import_module(
            "hindsight_memory_control_plane.migration_generation_extension"
        )

    def setUp(self):
        self.memory = _Memory()
        self.extension = self.module.MigrationGenerationHttpExtension(
            {"profile_id": "systalyze"}
        )
        app = FastAPI()
        app.include_router(self.extension.get_root_router(self.memory))
        self.client = TestClient(app)

    def test_constructor_rejects_missing_or_invalid_profile_id(self):
        for config in ({}, {"profile_id": "bad profile"}, {"profile_id": "-bad"}):
            with self.subTest(config=config), self.assertRaisesRegex(
                RuntimeError,
                "profile ID is invalid",
            ):
                self.module.MigrationGenerationHttpExtension(config)

    def test_generation_endpoint_is_authenticated_and_profile_scoped(self):
        denied = self.client.get("/v1/migration/generation")
        self.assertEqual(denied.status_code, 401)

        response = self.client.get(
            "/v1/migration/generation",
            headers={"Authorization": "Bearer test-token"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"generation": "systalyze:public:7"},
        )

    def test_authentication_infrastructure_failure_is_not_mapped_to_401(self):
        with patch.object(
            self.memory.tenant_extension,
            "authenticate",
            new=AsyncMock(side_effect=RuntimeError("database offline")),
        ), self.assertRaisesRegex(RuntimeError, "database offline"):
            asyncio.run(
                self.extension._authenticated_schema("Bearer test-token")
            )

    def test_controller_state_update_is_digest_bound_and_idempotent(self):
        payload = {
            "configuration_digest": "1" * 64,
            "hook_digest": "2" * 64,
            "schedule_digest": "3" * 64,
        }
        first = self.client.post(
            "/v1/migration/controller-state",
            headers={"Authorization": "Bearer test-token"},
            json=payload,
        )
        second = self.client.post(
            "/v1/migration/controller-state",
            headers={"Authorization": "Bearer test-token"},
            json=payload,
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["generation"], "systalyze:public:8")
        self.assertTrue(first.json()["changed"])
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["generation"], "systalyze:public:8")
        self.assertFalse(second.json()["changed"])

        for invalid_payload in (
            {**payload, "unexpected": "4" * 64},
            {**payload, "hook_digest": "not-a-digest"},
        ):
            response = self.client.post(
                "/v1/migration/controller-state",
                headers={"Authorization": "Bearer test-token"},
                json=invalid_payload,
            )
            self.assertEqual(response.status_code, 422)

        for authorization in ("Basic x", "Bearer "):
            response = self.client.post(
                "/v1/migration/controller-state",
                headers={"Authorization": authorization},
                json=payload,
            )
            self.assertEqual(response.status_code, 401)

        response = self.client.post(
            "/v1/migration/controller-state",
            headers={"Authorization": "Bearer wrong-token"},
            json=payload,
        )
        self.assertEqual(response.status_code, 401)

    def test_startup_commits_generation_schema_before_trigger_ddl(self):
        asyncio.run(self.extension.on_startup())

        statements = "\n".join(
            statement for statement, _arguments in self.memory.connection.statements
        )
        self.assertIn("hindsight_migration_generation", statements)
        self.assertIn("hindsight_bump_migration_generation", statements)
        self.assertIn('"banks"', statements)
        self.assertIn('"documents"', statements)
        self.assertIn('"async_operations"', statements)
        self.assertIn("c.relname = ANY($3::text[])", statements)
        self.assertEqual(
            self.memory.connection.transactions,
            [{}, {}, {}, {}],
        )

    def test_generation_is_unavailable_when_trigger_coverage_is_incomplete(self):
        self.memory.connection.missing_trigger_count = 1

        with self.assertRaisesRegex(RuntimeError, "coverage is incomplete"):
            asyncio.run(self.extension._read_generation("public"))

    def test_snapshot_uses_one_repeatable_read_read_only_transaction(self):
        self.memory.connection.controller_state = {
            "configuration_digest": "1" * 64,
            "hook_digest": "2" * 64,
            "schedule_digest": "3" * 64,
        }
        bank_snapshot = {
            "config": {},
            "stats": {},
            "scopes": {},
            "tags": [],
            "documents": [],
            "models": [],
            "directives": [],
            "invalidated_memories": [],
            "webhooks": [],
            "operations": [],
        }

        with patch.object(
            self.extension,
            "_snapshot_bank",
            new=AsyncMock(return_value=bank_snapshot),
        ) as snapshot_bank:
            value = asyncio.run(
                self.extension._read_snapshot(
                    "public",
                    "engineering",
                    "historical-candidate",
                )
            )

        self.assertEqual(
            self.memory.connection.transactions,
            [{"isolation": "repeatable_read", "readonly": True}],
        )
        self.assertEqual(
            snapshot_bank.await_args_list[0].args[2],
            "engineering",
        )
        self.assertEqual(
            snapshot_bank.await_args_list[1].args[2],
            "historical-candidate",
        )
        self.assertEqual(
            value["generation_before"],
            "systalyze:public:7",
        )
        self.assertEqual(
            value["generation_after"],
            "systalyze:public:7",
        )

    def test_replay_closeout_is_atomic_and_manifest_bound(self):
        created_at = datetime(
            2026,
            7,
            13,
            12,
            tzinfo=timezone.utc,
        )
        source_row = {
            "id": "source-1",
            "bank_id": "codex",
            "original_text": "frozen source text",
            "content_hash": None,
            "created_at": created_at,
            "updated_at": created_at,
            "tags": ["agent:codex"],
            "retain_params": {
                "metadata": {"source": "codex-hook"},
                "context": "session",
            },
        }
        self.memory.connection.source_documents = [source_row]
        descriptor = _document_descriptor(
            {
                **source_row,
                "created_at": created_at.isoformat(),
                "updated_at": created_at.isoformat(),
                "document_metadata": {"source": "codex-hook"},
                "observation_scopes": None,
            },
            source_bank_id="codex",
        )
        payload = {
            "schema_version": 1,
            "expected_generation": "systalyze:public:7",
            "expected_bank_ids": list(self.memory.connection.banks),
            "source_documents": [
                {
                    "source_document_id": "source-1",
                    "record_digest": descriptor["record_digest"],
                }
            ],
            "replay_plan_digest": "1" * 64,
            "verification_digest": "2" * 64,
            "backup_evidence_digest": "3" * 64,
            "closeout_plan_digest": "4" * 64,
        }

        with patch(
            "hindsight_api.engine.retain.bank_utils."
            "drop_bank_vector_indexes",
            new=AsyncMock(),
        ) as drop_indexes:
            response = self.client.post(
                "/v1/migration/replay-closeout",
                headers={"Authorization": "Bearer test-token"},
                json=payload,
            )
            retry = self.client.post(
                "/v1/migration/replay-closeout",
                headers={"Authorization": "Bearer test-token"},
                json=payload,
            )

        self.assertEqual(response.status_code, 200, response.text)
        value = response.json()
        self.assertEqual(value["deleted_bank_id"], "codex")
        self.assertNotIn("codex", value["remaining_bank_ids"])
        self.assertEqual(
            value["pre_delete_generation"],
            "systalyze:public:7",
        )
        self.assertNotEqual(
            value["post_delete_generation"],
            value["pre_delete_generation"],
        )
        self.assertIn(
            {"isolation": "serializable"},
            self.memory.connection.transactions,
        )
        drop_indexes.assert_awaited_once()
        self.assertEqual(
            self.memory._bank_stats_cache.invalidations,
            [("public", "codex")],
        )
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json(), value)
        drop_indexes.assert_awaited_once()

    def test_replay_closeout_rejects_generation_drift_before_delete(self):
        response = self.client.post(
            "/v1/migration/replay-closeout",
            headers={"Authorization": "Bearer test-token"},
            json={
                "schema_version": 1,
                "expected_generation": "systalyze:public:6",
                "expected_bank_ids": self.memory.connection.banks,
                "source_documents": [
                    {
                        "source_document_id": "source-1",
                        "record_digest": "1" * 64,
                    }
                ],
                "replay_plan_digest": "2" * 64,
                "verification_digest": "3" * 64,
                "backup_evidence_digest": "4" * 64,
                "closeout_plan_digest": "5" * 64,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("codex", self.memory.connection.banks)
        self.assertFalse(
            any(
                statement.lstrip().startswith("DELETE FROM")
                for statement, _arguments
                in self.memory.connection.statements
            )
        )

    def test_snapshot_route_rejects_invalid_banks_and_requires_auth(self):
        headers = {"Authorization": "Bearer test-token"}
        for source_bank, candidate_bank in (
            ("engineering", "engineering"),
            ("bad bank", "candidate"),
            ("-leading", "candidate"),
        ):
            response = self.client.get(
                "/v1/migration/snapshot",
                params={
                    "source_bank": source_bank,
                    "candidate_bank": candidate_bank,
                },
                headers=headers,
            )
            self.assertEqual(response.status_code, 422)

        response = self.client.get(
            "/v1/migration/snapshot",
            params={
                "source_bank": "engineering",
                "candidate_bank": "candidate",
            },
        )
        self.assertEqual(response.status_code, 401)

    def test_snapshot_rejects_malformed_controller_state_json(self):
        self.memory.connection.controller_state_raw_override = "{"

        with self.assertRaisesRegex(RuntimeError, "controller state is invalid"):
            asyncio.run(
                self.extension._read_snapshot(
                    "public",
                    "engineering",
                    "historical-candidate",
                )
            )

    def test_snapshot_rejects_incomplete_controller_state(self):
        for state in (
            {"configuration_digest": "1" * 64},
            {
                "configuration_digest": "1" * 64,
                "hook_digest": "not-a-digest",
                "schedule_digest": "3" * 64,
            },
        ):
            with self.subTest(state=state):
                self.memory.connection.controller_state = state
                with self.assertRaisesRegex(
                    RuntimeError,
                    "controller state is incomplete",
                ):
                    asyncio.run(
                        self.extension._read_snapshot(
                            "public",
                            "engineering",
                            "historical-candidate",
                        )
                    )

    def test_snapshot_rejects_an_inventory_above_the_item_limit(self):
        self.memory.connection.snapshot_scope_count = (
            self.module.MAX_SNAPSHOT_ITEMS + 1
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "scope inventory exceeds snapshot item limit",
        ):
            asyncio.run(
                self.extension._snapshot_bank(
                    self.memory.connection,
                    "public",
                    "engineering",
                )
            )

    def test_snapshot_webhooks_are_digest_only(self):
        self.memory.connection.snapshot_webhooks = [
            {
                "id": "hook-1",
                "event_types": ["retain"],
                "enabled": True,
                "target_digest": "1" * 64,
                "config_digest": "2" * 64,
            }
        ]

        snapshot = asyncio.run(
            self.extension._snapshot_bank(
                self.memory.connection,
                "public",
                "engineering",
            )
        )

        self.assertEqual(
            snapshot["webhooks"],
            self.memory.connection.snapshot_webhooks,
        )
        self.assertNotIn("url", snapshot["webhooks"][0])
        self.assertNotIn("http_config", snapshot["webhooks"][0])


if __name__ == "__main__":
    unittest.main()
