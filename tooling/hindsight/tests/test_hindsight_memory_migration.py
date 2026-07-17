import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import stat
import subprocess
import threading
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from hindsight_memory_control_plane.adapters import AdapterError, FakeAdapter
from hindsight_memory_control_plane.canonical import digest
from hindsight_memory_control_plane.harnesses import render_harness
from hindsight_memory_control_plane.http_adapter import HttpAdapter
from hindsight_memory_control_plane.ledger import LedgerError, validate_record
from hindsight_memory_control_plane.migration import (
    MigrationError,
    discover_migration_state,
    verify_shadow_plan,
)
from hindsight_memory_control_plane.model import BankRef, EndpointIdentity
import hindsight_memory_control_plane.migration as migration_module


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SOURCE = BankRef("example", "engineering")
CANDIDATE = BankRef("example", "historical-candidate")
CONTENT_SENTINEL = "payload-sentinel-that-must-never-enter-the-shadow-plan"
CLI = ROOT / "bin/hindsight-memory"


def bank_surface(bank: BankRef, document_id: str, content_digest: str, *, invalidated=False):
    scopes = ["repo:dotfiles"]
    return {
        "bank_ref": bank.to_dict(),
        "config": {"mission": f"mission-{bank.bank_id}"},
        "stats": {"documents": 1, "memories": 2},
        "scopes": scopes,
        "scopes_digest": digest({"scopes": scopes}),
        "tags": ["agent:codex", "repo:dotfiles", "scope:active"],
        "documents": [{
            "document_id": document_id,
            "updated_at": "2026-07-12T12:00:00Z",
            "content_digest": content_digest,
            "created_at": "2026-07-12T11:00:00Z",
            "text_length": 42,
            "memory_unit_count": 2,
            "tags": ["repo:dotfiles"],
            "document_metadata": {
                "content_digest": digest(
                    CONTENT_SENTINEL if bank == SOURCE else "candidate-metadata"
                )
            },
            "retain_params": {"content_digest": digest({})},
        }],
        "models": [{
            "model_id": f"model-{bank.bank_id}",
            "content_digest": digest("content-bearing prompt"),
        }],
        "directives": [{
            "directive_id": f"directive-{bank.bank_id}",
            "content_digest": digest("content-bearing directive"),
        }],
        "invalidated_memories": ([{
            "item_id": f"invalid-{document_id}",
            "source_document_id": document_id,
            "reason_digest": SHA_C,
            "content_digest": content_digest,
        }] if invalidated else []),
    }


def migration_inventory():
    return {
        "schema_version": 1,
        "endpoint": {
            "profile_id": "example",
            "scheme": "http",
            "host": "127.0.0.1",
            "port": 7979,
            "tenant": "default",
        },
        "provider_identity": {
            "llm": "claude-code",
            "embedding": "local-default",
            "reranking": "jina-mlx",
        },
        "versions": {
            "hindsight": "0.8.4",
            "adapter": "1",
            "providers": {"llm": "current", "embedding": "current", "reranking": "current"},
        },
        "banks": {
            "source": bank_surface(SOURCE, "source-1", SHA_A, invalidated=True),
            "candidate": bank_surface(CANDIDATE, "candidate-1", SHA_B, invalidated=True),
        },
        "operations": {"idle": True, "active": []},
        "hooks": [{
            "bank_role": "source",
            "hook_id": "codex",
            "registration_digest": digest({
                "target_digest": SHA_B,
                "activation_digest": SHA_C,
                "config_digest": SHA_D,
            }),
            "registration": {
                "target_digest": SHA_B,
                "activation_digest": SHA_C,
                "config_digest": SHA_D,
            },
        }],
        "schedules": [{
            "bank_role": "source",
            "model_id": "refresh",
            "trigger_digest": SHA_E,
        }],
    }


def repository_catalog():
    return {
        "canonical": ["repo:dotfiles", "repo:agents"],
        "aliases": {"github:dotfiles": "repo:dotfiles"},
        "drop_aliases": ["legacy-repository"],
    }


def package_manifest():
    value = {
        "schema_version": 1,
        "artifact_digest": SHA_E,
        "projection_digest": SHA_D,
        "tag_mapping_digest": digest(repository_catalog()),
        "candidate_provenance_digest": SHA_A,
        "candidate_curation_digest": SHA_B,
        "invalidation_dispositions": [
            {
                "bank_role": "source",
                "item_id": "invalid-source-1",
                "disposition": "exclude",
                "reason": "source-invalidated",
                "reapply_content_digest": None,
            },
            {
                "bank_role": "candidate",
                "item_id": "invalid-candidate-1",
                "disposition": "reapply",
                "reason": "candidate-curation-preserved",
                "reapply_content_digest": SHA_B,
            },
        ],
    }
    return bind_package_manifest(value)


def bind_package_manifest(value):
    body = copy.deepcopy(value)
    body.pop("approved_manifest_digest", None)
    body["invalidation_dispositions"].sort(
        key=lambda item: json.dumps(item, sort_keys=True)
    )
    value["approved_manifest_digest"] = digest(body)
    return value


def write_gate_files(root: Path):
    marker = root / "distillation-complete.marker"
    proposal = root / "proposal-log.md"
    marker.write_text("run=offline\nartifact=" + SHA_E + "\n", encoding="utf-8")
    proposal.write_text("## Migration pending\n", encoding="utf-8")
    return {
        "artifact_dir": str(root / "artifacts"),
        "completion_marker": str(marker),
        "proposal_log": str(proposal),
    }


class SequenceAdapter(FakeAdapter):
    def __init__(self, inventories, generations=None):
        super().__init__(
            endpoint=migration_inventory()["endpoint"],
            state={"migration_inventory": copy.deepcopy(inventories[0])},
        )
        self.inventories = [copy.deepcopy(value) for value in inventories]
        self.generations = list(
            ["generation-1", "generation-1"]
            if generations is None
            else generations
        )

    def read_migration_generation(self):
        self._record("read_migration_generation")
        if not self.generations:
            raise AdapterError("migration generation is unavailable")
        return self.generations.pop(0)

    def read_migration_inventory(self, source_bank, candidate_bank):
        self._record(
            "read_migration_inventory",
            {"source_bank": source_bank.to_dict(), "candidate_bank": candidate_bank.to_dict()},
        )
        if not self.inventories:
            raise AdapterError("migration inventory is unavailable")
        return copy.deepcopy(self.inventories.pop(0))


class MigrationDiscoveryContractTest(unittest.TestCase):
    def test_persisted_endpoint_hosts_follow_the_bare_host_contract(self):
        record = {
            "schema_version": 1,
            "action_id": "migration-discovery",
            "correlation_id": "session-1",
            "source_bank": {
                "profile_id": "example",
                "bank_id": "engineering",
                "endpoint": migration_inventory()["endpoint"],
            },
            "target_bank": {
                "profile_id": "example",
                "bank_id": "historical-candidate",
                "endpoint": migration_inventory()["endpoint"],
            },
            "policy_digest": SHA_A,
            "artifact_digest": SHA_B,
            "decision": "deny",
            "reason_code": "READ_ONLY",
            "timestamp": "2026-07-16T12:00:00Z",
            "reversible_record_id": None,
        }
        for host in ("user@example.com", "example.com/path", "[::1]", "bad host"):
            contaminated = copy.deepcopy(record)
            contaminated["source_bank"]["endpoint"]["host"] = host
            with self.subTest(host=host), self.assertRaisesRegex(
                LedgerError, "bare DNS name or IP literal"
            ):
                validate_record(contaminated)

    def test_harness_socket_locator_rejects_nul(self):
        with self.assertRaisesRegex(ValueError, "Unix socket"):
            render_harness(
                {},
                harness_id="codex",
                adapter="hindsight-codex",
                socket_path="/tmp/broker.sock\x00suffix",
            )

    def test_http_adapter_plain_http_requires_exact_loopback_literal(self):
        for host in ("localhost", "127.0.0.2", "0:0:0:0:0:0:0:1"):
            adapter = object.__new__(HttpAdapter)
            adapter.endpoint = EndpointIdentity(
                "example", "http", host, 7979, "default"
            )
            adapter._inventory = SimpleNamespace(policy={})
            with self.subTest(host=host), self.assertRaisesRegex(
                AdapterError, "loopback"
            ):
                adapter._validate_endpoint()

        for host in ("127.0.0.1", "::1"):
            adapter = object.__new__(HttpAdapter)
            adapter.endpoint = EndpointIdentity(
                "example", "http", host, 7979, "default"
            )
            adapter._inventory = SimpleNamespace(policy={})
            adapter._validate_endpoint()

    def test_provider_identity_extracts_current_and_desired_mapping_values(self):
        adapter = object.__new__(HttpAdapter)
        adapter.endpoint = EndpointIdentity(
            "example", "http", "127.0.0.1", 7979, "default"
        )
        providers = [
            {"id": "current-llm", "role": "llm"},
            {"id": "desired-llm", "role": "llm"},
        ]
        adapter._inventory = SimpleNamespace(
            profiles=[{
                "id": "example",
                "roles": {
                    "llm": {
                        "current": "current-llm",
                        "desired": "desired-llm",
                    }
                },
            }],
            providers=providers,
            inventory_digest=SHA_A,
        )

        identity = adapter._declared_provider_identity()

        self.assertEqual(
            [entry["provider_id"] for entry in identity["roles"]["llm"]],
            ["current-llm", "desired-llm"],
        )

    def test_provider_identity_rejects_mapping_keys_as_provider_ids(self):
        adapter = object.__new__(HttpAdapter)
        adapter.endpoint = EndpointIdentity(
            "example", "http", "127.0.0.1", 7979, "default"
        )
        adapter._inventory = SimpleNamespace(
            profiles=[{
                "id": "example",
                "roles": {"llm": {"current": {"id": "current-llm"}}},
            }],
            providers=[{"id": "unexpected", "role": "llm"}],
            inventory_digest=SHA_A,
        )
        with self.assertRaisesRegex(AdapterError, "provider identity"):
            adapter._declared_provider_identity()

    def test_normalization_rejects_non_string_mapping_keys(self):
        for value in ({1: "value"}, {"nested": {None: "value"}}):
            with self.subTest(value=value), self.assertRaisesRegex(
                MigrationError, "mapping keys must be strings"
            ):
                migration_module._normalized(value)

    def test_snapshot_normalization_sorts_document_tags(self):
        left = migration_inventory()
        right = copy.deepcopy(left)
        left_tags = ["scope:active", "repo:dotfiles"]
        left["banks"]["source"]["documents"][0]["tags"] = left_tags
        right["banks"]["source"]["documents"][0]["tags"] = list(
            reversed(left_tags)
        )

        normalized = migration_module._normalized_snapshot(left)
        self.assertEqual(
            normalized,
            migration_module._normalized_snapshot(right),
        )
        self.assertEqual(
            normalized["banks"]["source"]["documents"][0]["tags"],
            ["repo:dotfiles", "scope:active"],
        )

    def test_discovery_snapshots_each_adapter_inventory_before_reuse(self):
        shared = migration_inventory()

        class ReusingAdapter(SequenceAdapter):
            def __init__(self):
                super().__init__([shared, shared])
                self.shared = shared
                self.reads = 0

            def read_migration_inventory(self, source_bank, candidate_bank):
                self.reads += 1
                if self.reads == 2:
                    self.shared["endpoint"]["port"] += 1
                return self.shared

        with tempfile.TemporaryDirectory() as directory:
            _adapter, result = self.discover(
                Path(directory), adapter=ReusingAdapter()
            )
        self.assertFalse(result.complete)
        self.assertIn("drift:identity", result.blockers)

    def test_conflicting_repository_scopes_are_rejected(self):
        with self.assertRaisesRegex(MigrationError, "conflicting repository scopes"):
            migration_module._coverage_scope(
                {"scopes": ["repo:one", "repo:two"], "tags": []},
                {"tags": []},
                {
                    "canonical": ["repo:one", "repo:two"],
                    "aliases": {},
                    "drop_aliases": [],
                },
            )

    def test_mixed_semantic_scope_classes_are_rejected(self):
        for scopes in (
            ["repo:one", "workflow:release"],
            ["repo:one", "personal"],
            ["workflow:release", "global"],
        ):
            with self.subTest(scopes=scopes), self.assertRaisesRegex(
                MigrationError, "conflicting semantic scopes"
            ):
                migration_module._coverage_scope(
                    {"scopes": scopes, "tags": []},
                    {"tags": []},
                    {
                        "canonical": ["repo:one"],
                        "aliases": {},
                        "drop_aliases": [],
                    },
                )

    def test_malformed_semantic_scope_is_omitted_before_global_fallback(self):
        self.assertEqual(
            migration_module._coverage_scope(
                {"scopes": ["workflow:"], "tags": []},
                {"tags": []},
                {
                    "canonical": ["repo:one"],
                    "aliases": {},
                    "drop_aliases": [],
                },
            ),
            ("omit", "unknown-semantic-scope", None),
        )

    def test_snapshot_rejects_duplicate_and_noncanonical_scope_digest(self):
        snapshot = migration_inventory()
        snapshot["banks"]["source"]["scopes"] = ["repo:dotfiles", "repo:dotfiles"]
        snapshot["banks"]["source"]["scopes_digest"] = digest(
            {"scopes": ["repo:dotfiles", "repo:dotfiles"]}
        )
        with tempfile.TemporaryDirectory() as directory:
            _adapter, result = self.discover(
                Path(directory), inventories=[snapshot, snapshot]
            )
        self.assertIn("invalid:source.scopes.duplicate", result.blockers)

        snapshot = migration_inventory()
        snapshot["banks"]["source"]["scopes"] = ["repo:z", "repo:a"]
        snapshot["banks"]["source"]["scopes_digest"] = digest(
            {"scopes": ["repo:a", "repo:z"]}
        )
        with tempfile.TemporaryDirectory() as directory:
            _adapter, result = self.discover(
                Path(directory), inventories=[snapshot, snapshot]
            )
        self.assertNotIn("invalid:source.scopes_digest", result.blockers)

    def discover(
        self,
        root: Path,
        *,
        inventories=None,
        package=None,
        approved_digest=None,
        catalog=None,
        watermarks=None,
        generations=None,
        adapter=None,
    ):
        manifest = copy.deepcopy(package or package_manifest())
        adapter = adapter or SequenceAdapter(
            inventories or [migration_inventory(), migration_inventory()],
            generations=generations,
        )
        watermark_values = iter(
            copy.deepcopy(
                watermarks
                or [
                    {"codex": {"document_id": "source-1", "epoch": 4, "checkpoint": 9}},
                    {"codex": {"document_id": "source-1", "epoch": 4, "checkpoint": 9}},
                ]
            )
        )
        result = discover_migration_state(
            adapter,
            source_bank=SOURCE,
            candidate_bank=CANDIDATE,
            offline_package_manifest=manifest,
            approved_offline_package_digest=approved_digest or manifest["approved_manifest_digest"],
            migration_paths=write_gate_files(root),
            retain_watermark_reader=lambda: next(watermark_values),
            private_catalog_digests=catalog or {
                "catalog": SHA_A,
                "bank_archetypes": SHA_B,
                "tag_aliases": SHA_C,
            },
            repository_catalog=repository_catalog(),
            timestamp="20260713T120000Z",
        )
        return adapter, result

    def test_complete_discovery_writes_private_content_inventory_and_redacted_unapproved_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter, result = self.discover(Path(directory))
            self.assertTrue(result.complete)
            self.assertEqual(result.blockers, ())
            self.assertEqual(
                [call["method"] for call in adapter.calls],
                [
                    "read_migration_generation",
                    "read_migration_inventory",
                    "read_migration_inventory",
                    "read_migration_generation",
                ],
            )
            run_dir = Path(result.run_dir)
            inventory_path = run_dir / "inventory.json"
            plan_path = run_dir / "shadow-plan.json"
            self.assertEqual(stat.S_IMODE(run_dir.stat().st_mode), 0o700)
            self.assertEqual(run_dir.stat().st_uid, os.geteuid())
            self.assertEqual(stat.S_IMODE(inventory_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(plan_path.stat().st_mode), 0o600)
            self.assertEqual(list(run_dir.glob(".*.tmp")), [])
            inventory_text = inventory_path.read_text(encoding="utf-8")
            plan_text = plan_path.read_text(encoding="utf-8")
            self.assertNotIn(CONTENT_SENTINEL, inventory_text)
            self.assertNotIn(CONTENT_SENTINEL, plan_text)
            plan = json.loads(plan_text)
            self.assertFalse(plan["approved"])
            self.assertEqual(plan["mutation_authority"], "none")
            self.assertTrue(plan["complete"])
            self.assertFalse(plan["legacy_observations_imported"])
            verify_shadow_plan(plan, repository_catalog=repository_catalog())

    def test_discovery_records_high_water_invalidations_and_every_required_surface(self):
        with tempfile.TemporaryDirectory() as directory:
            _, result = self.discover(Path(directory))
            inventory = json.loads((Path(result.run_dir) / "inventory.json").read_text())
            self.assertEqual(inventory["adapter_generation"], "generation-1")
            self.assertEqual(
                inventory["high_water_manifest"],
                [
                    {"bank_role": "candidate", "content_digest": SHA_B, "document_id": "candidate-1", "updated_at": "2026-07-12T12:00:00Z"},
                    {"bank_role": "source", "content_digest": SHA_A, "document_id": "source-1", "updated_at": "2026-07-12T12:00:00Z"},
                ],
            )
            self.assertEqual(
                {item["item_id"] for item in inventory["invalidation_manifest"]},
                {"invalid-source-1", "invalid-candidate-1"},
            )
            for key in ("endpoint", "provider_identity", "versions", "banks", "operations", "hooks", "schedules", "retain_watermarks"):
                self.assertIn(key, inventory["snapshot"])

    def test_each_missing_required_surface_returns_explicit_incomplete_result_without_artifacts(self):
        required = ("endpoint", "provider_identity", "versions", "banks", "operations", "hooks", "schedules")
        for key in required:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                broken = migration_inventory()
                del broken[key]
                _, result = self.discover(Path(directory), inventories=[broken, broken])
                self.assertFalse(result.complete)
                self.assertIn(f"missing:{key}", result.blockers)
                self.assertIsNone(result.run_dir)
                self.assertFalse((Path(directory) / "artifacts").exists())

    def test_retain_watermarks_are_separate_and_drift_blocks_planning(self):
        before = {"codex": {"document_id": "source-1", "epoch": 4, "checkpoint": 9}}
        after = {"codex": {"document_id": "source-1", "epoch": 4, "checkpoint": 10}}
        with tempfile.TemporaryDirectory() as directory:
            _, result = self.discover(Path(directory), watermarks=[before, after])
            self.assertFalse(result.complete)
            self.assertIn("drift:retain_watermarks", result.blockers)
            self.assertIsNone(result.run_dir)

        broken = migration_inventory()
        broken["retain_watermarks"] = before
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(MigrationError, "must not contain retain watermarks"):
                self.discover(Path(directory), inventories=[broken, broken])

    def test_explicit_empty_collections_are_complete_but_missing_record_fields_are_not(self):
        empty = migration_inventory()
        for bank in empty["banks"].values():
            bank["documents"] = []
            bank["invalidated_memories"] = []
        empty["hooks"] = []
        empty["schedules"] = []
        manifest = package_manifest()
        manifest["invalidation_dispositions"] = []
        bind_package_manifest(manifest)
        with tempfile.TemporaryDirectory() as directory:
            _, result = self.discover(Path(directory), inventories=[empty, empty], package=manifest)
            self.assertTrue(result.complete)

        for field in ("document_id", "updated_at", "content_digest"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                broken = migration_inventory()
                del broken["banks"]["source"]["documents"][0][field]
                _, result = self.discover(Path(directory), inventories=[broken, broken])
                self.assertFalse(result.complete)
                self.assertIn(f"missing:source.documents.{field}", result.blockers)

    def test_unhashable_record_ids_fail_closed_without_crashing_duplicate_checks(self):
        broken = migration_inventory()
        broken["banks"]["source"]["documents"][0]["document_id"] = ["unsafe"]
        broken["banks"]["source"]["invalidated_memories"][0]["item_id"] = {
            "unsafe": True
        }
        with tempfile.TemporaryDirectory() as directory:
            _, result = self.discover(
                Path(directory), inventories=[broken, broken]
            )
        self.assertFalse(result.complete)
        self.assertIn("invalid:source.documents.document_id", result.blockers)
        self.assertIn(
            "invalid:source.invalidated_memories.item_id", result.blockers
        )

    def test_valid_duplicate_record_ids_remain_explicit_blockers(self):
        broken = migration_inventory()
        broken["banks"]["source"]["documents"].append(
            copy.deepcopy(broken["banks"]["source"]["documents"][0])
        )
        with tempfile.TemporaryDirectory() as directory:
            _, result = self.discover(
                Path(directory), inventories=[broken, broken]
            )
        self.assertFalse(result.complete)
        self.assertIn("invalid:source.documents.duplicate", result.blockers)

    def test_document_and_invalidation_records_are_closed_before_persistence(self):
        mutations = []
        document = migration_inventory()
        document["banks"]["source"]["documents"][0]["unexpected"] = CONTENT_SENTINEL
        mutations.append((document, "invalid:source.documents.keys"))
        invalidation = migration_inventory()
        invalidation["banks"]["source"]["invalidated_memories"][0]["unexpected"] = CONTENT_SENTINEL
        mutations.append((invalidation, "invalid:source.invalidated_memories.keys"))
        for snapshot, blocker in mutations:
            with self.subTest(blocker=blocker), tempfile.TemporaryDirectory() as directory:
                _, result = self.discover(
                    Path(directory), inventories=[snapshot, snapshot]
                )
                self.assertFalse(result.complete)
                self.assertIn(blocker, result.blockers)
                self.assertIsNone(result.run_dir)

    def test_optional_document_tags_must_be_unique_known_bank_tags(self):
        for tags in (
            ["repo:dotfiles", "repo:dotfiles"],
            ["repo:Not-Canonical"],
            ["attacker:directive"],
            [7],
        ):
            with self.subTest(tags=tags), tempfile.TemporaryDirectory() as directory:
                snapshot = migration_inventory()
                snapshot["banks"]["source"]["documents"][0]["tags"] = tags
                _, result = self.discover(
                    Path(directory), inventories=[snapshot, snapshot]
                )
                self.assertFalse(result.complete)
                self.assertIn("invalid:source.documents.tags", result.blockers)
                self.assertIsNone(result.run_dir)

    def test_optional_document_inventory_fields_are_typed_before_persistence(self):
        cases = (
            ("created_at", "2026-07-12T11:00:00", "created_at"),
            ("created_at", 7, "created_at"),
            ("text_length", True, "text_length"),
            ("text_length", -1, "text_length"),
            ("memory_unit_count", "2", "memory_unit_count"),
            ("document_metadata", [], "document_metadata"),
            ("retain_params", "unsafe", "retain_params"),
        )
        for field, value, blocker_field in cases:
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as directory:
                snapshot = migration_inventory()
                snapshot["banks"]["source"]["documents"][0][field] = value
                _, result = self.discover(
                    Path(directory), inventories=[snapshot, snapshot]
                )
                self.assertFalse(result.complete)
                self.assertIn(
                    f"invalid:source.documents.{blocker_field}",
                    result.blockers,
                )
                self.assertIsNone(result.run_dir)

    def test_document_timestamps_require_actual_aware_strings(self):
        class TimestampLike:
            def __str__(self):
                return "2026-07-12T12:00:00Z"

        for field, value in (
            ("updated_at", TimestampLike()),
            ("updated_at", "2026-07-12T12:00:00"),
            ("created_at", TimestampLike()),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                snapshot = migration_inventory()
                snapshot["banks"]["source"]["documents"][0][field] = value
                _, result = self.discover(
                    Path(directory), inventories=[snapshot, snapshot]
                )
                self.assertFalse(result.complete)
                self.assertIn(
                    f"invalid:source.documents.{field}", result.blockers
                )
                self.assertIsNone(result.run_dir)

    def test_inventory_rejects_non_mapping_collection_entries(self):
        for path in (
            ("hooks",),
            ("schedules",),
            ("banks", "source", "models"),
            ("banks", "source", "directives"),
        ):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as directory:
                snapshot = migration_inventory()
                target = snapshot
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = ["invalid"]
                _, result = self.discover(
                    Path(directory), inventories=[snapshot, snapshot]
                )
                self.assertFalse(result.complete)
                blocker = ".".join(path[1:]) if path[0] == "banks" else path[0]
                expected = (
                    f"invalid:{blocker}.keys"
                    if path[0] == "banks"
                    else f"invalid:{blocker}"
                )
                self.assertIn(expected, result.blockers)
                self.assertIsNone(result.run_dir)

    def test_shadow_plan_binds_exact_coverage_scope_curation_and_cutover_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            _, result = self.discover(Path(directory))
            plan = result.plan.to_dict()
            self.assertEqual(
                {item["item_id"] for item in plan["coverage"]["source"]},
                {"source-1"},
            )
            self.assertEqual(
                {item["item_id"] for item in plan["coverage"]["candidate"]},
                {"candidate-1"},
            )
            self.assertEqual(
                [item["semantic_scope"] for role in ("source", "candidate") for item in plan["coverage"][role]],
                ["repo:dotfiles", "repo:dotfiles"],
            )
            self.assertEqual(
                {item["item_id"] for item in plan["invalidation_dispositions"]},
                {"invalid-source-1", "invalid-candidate-1"},
            )
            self.assertEqual(plan["bindings"]["candidate_provenance_digest"], SHA_A)
            self.assertEqual(plan["bindings"]["candidate_curation_digest"], SHA_B)
            self.assertTrue(plan["operations"]["idle"])
            self.assertIn("full_schema_backup", plan["rollback_requirements"])
            self.assertEqual(plan["cutover"]["on_drift"], "restart_verification")
            self.assertTrue(plan["cutover"]["freeze_retain_paths"])
            self.assertTrue(plan["cutover"]["final_catch_up"])
            self.assertEqual(plan["closeout"]["authority"], "separate_digest_bound_approval")
            self.assertEqual(plan["archive_retirement"]["authority"], "separate_digest_bound_approval")
            self.assertNotEqual(plan["closeout"]["kind"], plan["archive_retirement"]["kind"])

    def test_migration_requirement_contracts_are_frozen_and_serialize_mutably(self):
        with self.assertRaises(TypeError):
            migration_module.ROLLBACK_REQUIREMENTS[0] = "weakened"
        with self.assertRaises(TypeError):
            migration_module.CUTOVER_REQUIREMENTS["freeze_retain_paths"] = False
        with self.assertRaises(TypeError):
            migration_module.CLOSEOUT_REQUIREMENTS["authority"] = "none"
        with self.assertRaises(TypeError):
            migration_module.ARCHIVE_RETIREMENT_REQUIREMENTS[
                "requires_accepted_cutover"
            ] = False

        with tempfile.TemporaryDirectory() as directory:
            _, result = self.discover(Path(directory))
            plan = result.plan.to_dict()
        self.assertIsInstance(plan["rollback_requirements"], list)
        self.assertIsInstance(plan["cutover"], dict)
        self.assertIsInstance(plan["closeout"], dict)
        self.assertIsInstance(plan["archive_retirement"], dict)


    def test_shadow_plan_rejects_noncanonical_repository_scope_slugs(self):
        with tempfile.TemporaryDirectory() as directory:
            _, result = self.discover(Path(directory))
            for semantic_scope in (
                "repo:agent_tools",
                "repo:agent.tools",
            ):
                plan = copy.deepcopy(result.plan.to_dict())
                plan["coverage"]["source"][0][
                    "semantic_scope"
                ] = semantic_scope
                plan["bindings"]["source_coverage_digest"] = digest(
                    plan["coverage"]["source"]
                )
                plan["plan_digest"] = digest(
                    {
                        key: value
                        for key, value in plan.items()
                        if key != "plan_digest"
                    }
                )
                with self.subTest(
                    semantic_scope=semantic_scope
                ), self.assertRaisesRegex(
                    MigrationError, "semantic scope is invalid"
                ):
                    verify_shadow_plan(
                        plan,
                        repository_catalog=repository_catalog(),
                    )

    def test_bank_roles_preserve_identical_document_and_invalidation_ids(self):
        snapshot = migration_inventory()
        snapshot["banks"]["candidate"]["documents"][0]["document_id"] = "source-1"
        snapshot["banks"]["candidate"]["invalidated_memories"][0][
            "item_id"
        ] = "invalid-source-1"
        manifest = package_manifest()
        manifest["invalidation_dispositions"][1]["item_id"] = (
            "invalid-source-1"
        )
        bind_package_manifest(manifest)
        with tempfile.TemporaryDirectory() as directory:
            _, result = self.discover(
                Path(directory),
                inventories=[snapshot, snapshot],
                package=manifest,
            )
        self.assertTrue(result.complete)
        plan = result.plan.to_dict()
        self.assertEqual(
            [
                (item["bank_role"], item["item_id"])
                for role in ("source", "candidate")
                for item in plan["coverage"][role]
            ],
            [("source", "source-1"), ("candidate", "source-1")],
        )
        self.assertEqual(
            {
                (item["bank_role"], item["item_id"])
                for item in plan["invalidation_dispositions"]
            },
            {
                ("source", "invalid-source-1"),
                ("candidate", "invalid-source-1"),
            },
        )

    def test_shadow_plan_verifier_rejects_rehashed_semantic_weakening(self):
        with tempfile.TemporaryDirectory() as directory:
            _, result = self.discover(Path(directory))
            original = result.plan.to_dict()

        mutations = (
            lambda plan: plan["rollback_requirements"].remove("full_schema_backup"),
            lambda plan: plan["cutover"].update({"freeze_retain_paths": False}),
            lambda plan: plan["operations"].update({"idle": False}),
            lambda plan: plan["closeout"].update({"archive_deletion_authority": True}),
            lambda plan: plan["archive_retirement"].update({"authority": "implicit"}),
            lambda plan: plan["bindings"].update({"inventory_digest": "invalid"}),
            lambda plan: plan["semantic_diff"].update({"proposed_retains": 0}),
            lambda plan: plan.update({"schema_version": True}),
            lambda plan: plan["semantic_diff"].update({"source_items": True}),
            lambda plan: plan["invalidation_dispositions"].reverse(),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                plan = copy.deepcopy(original)
                mutate(plan)
                body = {key: value for key, value in plan.items() if key != "plan_digest"}
                plan["plan_digest"] = digest(body)
                with self.assertRaises(MigrationError):
                    verify_shadow_plan(
                        plan, repository_catalog=repository_catalog()
                    )

    def test_shadow_plan_accepts_and_validates_endpoint_bearing_bank_refs(self):
        with tempfile.TemporaryDirectory() as directory:
            _, result = self.discover(Path(directory))
            plan = result.plan.to_dict()
        endpoint = EndpointIdentity(
            "example", "http", "127.0.0.1", 7979, "default"
        ).to_dict()
        plan["source_bank"]["endpoint"] = endpoint
        plan["candidate_bank"]["endpoint"] = copy.deepcopy(endpoint)
        body = {key: value for key, value in plan.items() if key != "plan_digest"}
        plan["plan_digest"] = digest(body)
        verify_shadow_plan(plan, repository_catalog=repository_catalog())

        plan["candidate_bank"]["endpoint"]["profile_id"] = "other"
        body = {key: value for key, value in plan.items() if key != "plan_digest"}
        plan["plan_digest"] = digest(body)
        with self.assertRaisesRegex(MigrationError, "bank endpoint"):
            verify_shadow_plan(plan, repository_catalog=repository_catalog())

    def test_legacy_package_coverage_and_invalid_curation_fail_closed(self):
        mutations = []
        legacy = package_manifest()
        legacy["source_coverage"] = []
        mutations.append(legacy)
        duplicate = package_manifest()
        duplicate["invalidation_dispositions"].append(
            copy.deepcopy(duplicate["invalidation_dispositions"][0])
        )
        mutations.append(duplicate)
        for manifest in mutations:
            with self.subTest(manifest=manifest), tempfile.TemporaryDirectory() as directory:
                _, result = self.discover(Path(directory), package=manifest)
                self.assertFalse(result.complete)
                self.assertTrue(result.blockers)
                self.assertIsNone(result.run_dir)

    def test_live_coverage_is_controller_derived_with_global_fallback(self):
        snapshot = migration_inventory()
        snapshot["banks"]["source"]["scopes"] = []
        snapshot["banks"]["source"]["scopes_digest"] = digest({"scopes": []})
        snapshot["banks"]["source"]["tags"] = []
        snapshot["banks"]["source"]["documents"][0]["tags"] = []
        with tempfile.TemporaryDirectory() as directory:
            _, result = self.discover(
                Path(directory), inventories=[snapshot, snapshot]
            )
            self.assertTrue(result.complete)
            source = result.plan.to_dict()["coverage"]["source"]
            self.assertEqual(source[0]["item_id"], "source-1")
            self.assertEqual(source[0]["content_digest"], SHA_A)
            self.assertEqual(source[0]["disposition"], "retain")
            self.assertEqual(source[0]["semantic_scope"], "global")

    def test_shadow_plan_uses_the_import_semantic_scope_vocabulary(self):
        with tempfile.TemporaryDirectory() as directory:
            _, result = self.discover(Path(directory))
            for semantic_scope in (
                "global",
                "personal",
                "workflow:git.publication",
            ):
                plan = copy.deepcopy(result.plan.to_dict())
                plan["coverage"]["source"][0][
                    "semantic_scope"
                ] = semantic_scope
                plan["bindings"]["source_coverage_digest"] = digest(
                    plan["coverage"]["source"]
                )
                plan["plan_digest"] = digest(
                    {
                        key: value
                        for key, value in plan.items()
                        if key != "plan_digest"
                    }
                )
                with self.subTest(semantic_scope=semantic_scope):
                    verify_shadow_plan(
                        plan,
                        repository_catalog=repository_catalog(),
                    )

    def test_malformed_bank_scope_and_tag_entries_block_before_coverage(self):
        for field in ("scopes", "tags"):
            snapshot = migration_inventory()
            snapshot["banks"]["source"][field] = [
                "repo:dotfiles",
                {"private": "content"},
            ]
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                _, result = self.discover(
                    Path(directory), inventories=[snapshot, snapshot]
                )
                self.assertFalse(result.complete)
                self.assertIn(
                    f"invalid:source.{field}", result.blockers
                )
                self.assertIsNone(result.plan)
                self.assertIsNone(result.run_dir)

    def test_scope_digest_must_bind_normalized_scope_ids(self):
        before = migration_inventory()
        after = copy.deepcopy(before)
        after["banks"]["source"]["scopes_digest"] = digest({
            "scopes": [{
                "scope": "repo:dotfiles",
                "count": 2,
                "description": "redacted runtime metadata",
            }]
        })
        with tempfile.TemporaryDirectory() as directory:
            _, result = self.discover(
                Path(directory), inventories=[before, after]
            )
            self.assertFalse(result.complete)
            self.assertIn("invalid:source.scopes_digest", result.blockers)
            self.assertIsNone(result.run_dir)

    def test_live_coverage_resolves_aliases_and_omits_unknown_or_dropped_repositories(self):
        alias = migration_inventory()
        for surface in alias["banks"].values():
            surface["scopes"] = ["github:dotfiles"]
            surface["scopes_digest"] = digest({"scopes": ["github:dotfiles"]})
            surface["tags"] = []
            surface["documents"][0]["tags"] = []
        with tempfile.TemporaryDirectory() as directory:
            _, result = self.discover(
                Path(directory), inventories=[alias, alias]
            )
            self.assertTrue(result.complete)
            self.assertEqual(
                result.plan.to_dict()["coverage"]["source"][0]["semantic_scope"],
                "repo:dotfiles",
            )

        for candidate, reason in (
            ("repo:unknown", "unknown-repository-scope"),
            ("github:unknown", "unknown-repository-scope"),
            ("legacy-repository", "dropped-repository-scope"),
        ):
            with self.subTest(candidate=candidate), tempfile.TemporaryDirectory() as directory:
                snapshot = migration_inventory()
                for surface in snapshot["banks"].values():
                    surface["scopes"] = [candidate]
                    surface["scopes_digest"] = digest({"scopes": [candidate]})
                    surface["tags"] = []
                    surface["documents"][0]["tags"] = []
                _, result = self.discover(
                    Path(directory), inventories=[snapshot, snapshot]
                )
                coverage = result.plan.to_dict()["coverage"]["source"][0]
                self.assertEqual(coverage["disposition"], "omit")
                self.assertEqual(coverage["reason"], reason)
                self.assertIsNone(coverage["semantic_scope"])

    def test_repository_catalog_is_digest_bound_and_not_persisted(self):
        malformed = repository_catalog()
        malformed["aliases"]["github:dotfiles"] = "repo:unknown"
        with self.assertRaisesRegex(MigrationError, "aliases"):
            migration_module._validate_repository_catalog(
                malformed, digest(malformed)
            )

        malformed_target = repository_catalog()
        malformed_target["aliases"]["github:dotfiles"] = ["repo:dotfiles"]
        with self.assertRaisesRegex(MigrationError, "aliases"):
            migration_module._validate_repository_catalog(
                malformed_target, digest(malformed_target)
            )

        malformed_canonical = repository_catalog()
        malformed_canonical["canonical"].append("repo:agent_tools.v2")
        with self.assertRaisesRegex(MigrationError, "canonical tags"):
            migration_module._validate_repository_catalog(
                malformed_canonical, digest(malformed_canonical)
            )

        for alias in ("github:agent_tools", "github:agent.tools"):
            malformed_alias = repository_catalog()
            malformed_alias["aliases"][alias] = (
                malformed_alias["canonical"][0]
            )
            with self.subTest(alias=alias), self.assertRaisesRegex(
                MigrationError, "aliases"
            ):
                migration_module._validate_repository_catalog(
                    malformed_alias, digest(malformed_alias)
                )

        with tempfile.TemporaryDirectory() as directory:
            _, result = self.discover(Path(directory))
            serialized = json.dumps(result.plan.to_dict())
            self.assertNotIn("github:dotfiles", serialized)
            self.assertNotIn("legacy-repository", serialized)

    def test_busy_operations_package_digest_mismatch_and_drift_block(self):
        busy = migration_inventory()
        busy["operations"] = {"idle": False, "active": [{"operation_id": "retain-1"}]}
        with tempfile.TemporaryDirectory() as directory:
            _, result = self.discover(Path(directory), inventories=[busy, busy])
            self.assertFalse(result.complete)
            self.assertIn("operations:not_idle", result.blockers)

        with tempfile.TemporaryDirectory() as directory:
            _, result = self.discover(Path(directory), approved_digest="0" * 64)
            self.assertFalse(result.complete)
            self.assertIn("offline_package:digest_mismatch", result.blockers)

        non_json_package = package_manifest()
        non_json_package["invalidation_dispositions"][0]["reason"] = object()
        blockers = migration_module._package_blockers(
            non_json_package, non_json_package["approved_manifest_digest"]
        )
        self.assertIn("offline_package:invalid_manifest", blockers)
        self.assertIn("offline_package:digest_mismatch", blockers)

        before = migration_inventory()
        after = migration_inventory()
        after["banks"]["source"]["stats"]["documents"] = 2
        with tempfile.TemporaryDirectory() as directory:
            _, result = self.discover(Path(directory), inventories=[before, after])
            self.assertFalse(result.complete)
            self.assertIn("drift:bank_stats", result.blockers)

        drift_cases = {
            "bank_configuration": lambda value: value["banks"]["source"]["config"].update({"changed": True}),
            "bank_documents": lambda value: value["banks"]["source"]["documents"][0]["document_metadata"].update({"content_digest": SHA_E}),
            "bank_scope": lambda value: value["banks"]["source"]["tags"].append("repo:other"),
            "bank_models_and_directives": lambda value: value["banks"]["source"]["models"][0].update({"content_digest": SHA_E}),
            "invalidated_memories": lambda value: value["banks"]["source"]["invalidated_memories"][0].update({"reason_digest": SHA_B}),
            "hooks": lambda value: value["hooks"].append({
                "bank_role": "source", "hook_id": "hook-2",
                "registration_digest": digest({
                    "target_digest": SHA_A,
                    "activation_digest": SHA_B,
                    "config_digest": SHA_C,
                }),
                "registration": {
                    "target_digest": SHA_A,
                    "activation_digest": SHA_B,
                    "config_digest": SHA_C,
                },
            }),
            "schedules": lambda value: value["schedules"].append({
                "bank_role": "source", "model_id": "schedule-2",
                "trigger_digest": SHA_E,
            }),
        }
        for label, mutate in drift_cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                before = migration_inventory()
                after = copy.deepcopy(before)
                mutate(after)
                _, result = self.discover(
                    Path(directory), inventories=[before, after]
                )
                self.assertFalse(result.complete)
                self.assertIn(f"drift:{label}", result.blockers)

    def test_malformed_curation_record_types_fail_closed(self):
        malformed_values = (
            ("bank_role", {"not": "hashable"}),
            ("item_id", {"not": "hashable"}),
            ("reason", ["not", "an", "identifier"]),
            ("disposition", ["reapply"]),
            ("reapply_content_digest", {"not": "a digest"}),
        )
        for field, value in malformed_values:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                manifest = package_manifest()
                manifest["invalidation_dispositions"][0][field] = value
                bind_package_manifest(manifest)
                _, result = self.discover(Path(directory), package=manifest)
                self.assertFalse(result.complete)
                self.assertTrue(
                    any(
                        blocker.startswith("curation:")
                        or blocker == "offline_package:invalid_invalidation_dispositions"
                        for blocker in result.blockers
                    )
                )
                self.assertIsNone(result.run_dir)

    def test_generation_drift_blocks_transient_or_reverted_live_writes(self):
        stable = migration_inventory()
        with tempfile.TemporaryDirectory() as directory:
            _, result = self.discover(
                Path(directory),
                inventories=[stable, stable],
                generations=["generation-before", "generation-after"],
            )
            self.assertFalse(result.complete)
            self.assertIn("drift:adapter_generation", result.blockers)
            self.assertIsNone(result.run_dir)

    def test_missing_adapter_generation_contract_fails_closed(self):
        adapter = SequenceAdapter([migration_inventory(), migration_inventory()])
        adapter.read_migration_generation = None
        with tempfile.TemporaryDirectory() as directory:
            _, result = self.discover(Path(directory), adapter=adapter)
            self.assertFalse(result.complete)
            self.assertEqual(
                result.blockers,
                ("adapter:migration_generation_unavailable",),
            )
            self.assertIsNone(result.run_dir)

    def test_offline_package_digest_binds_every_manifest_body_field(self):
        approved = package_manifest()
        with tempfile.TemporaryDirectory() as directory:
            _, result = self.discover(Path(directory), package=approved)
            self.assertTrue(result.complete)
            self.assertEqual(
                result.plan.to_dict()["bindings"]["offline_package_manifest_digest"],
                approved["approved_manifest_digest"],
            )

        mutations = {
            "schema_version": lambda value: value.update({"schema_version": 2}),
            "artifact_digest": lambda value: value.update({"artifact_digest": SHA_A}),
            "projection_digest": lambda value: value.update({"projection_digest": SHA_A}),
            "tag_mapping_digest": lambda value: value.update({"tag_mapping_digest": SHA_A}),
            "candidate_provenance_digest": lambda value: value.update(
                {"candidate_provenance_digest": SHA_B}
            ),
            "candidate_curation_digest": lambda value: value.update(
                {"candidate_curation_digest": SHA_A}
            ),
            "invalidation_dispositions": lambda value: value[
                "invalidation_dispositions"
            ][0].update({"reason": "changed-after-approval"}),
        }
        for field, mutate in mutations.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                changed = copy.deepcopy(approved)
                mutate(changed)
                _, result = self.discover(
                    Path(directory),
                    package=changed,
                    approved_digest=approved["approved_manifest_digest"],
                )
                self.assertFalse(result.complete)
                self.assertIn("offline_package:digest_mismatch", result.blockers)
                self.assertIsNone(result.run_dir)

    def test_equivalent_reordering_produces_the_same_semantic_digests(self):
        first = migration_inventory()
        registration = {
            "target_digest": SHA_C,
            "activation_digest": SHA_D,
            "config_digest": SHA_E,
        }
        first["hooks"].insert(0, {
            "bank_role": "candidate",
            "hook_id": "claude",
            "registration_digest": digest(registration),
            "registration": registration,
        })
        second = copy.deepcopy(first)
        second["hooks"].reverse()
        for role in ("source", "candidate"):
            for collection in (
                "scopes",
                "tags",
                "documents",
                "models",
                "directives",
                "invalidated_memories",
            ):
                second["banks"][role][collection].reverse()
        manifest = package_manifest()
        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
            _, left_result = self.discover(Path(left), inventories=[first, first], package=manifest)
            reordered = copy.deepcopy(manifest)
            reordered["invalidation_dispositions"].reverse()
            _, right_result = self.discover(Path(right), inventories=[second, second], package=reordered)
            self.assertTrue(left_result.complete)
            self.assertTrue(right_result.complete)
            self.assertEqual(left_result.inventory_digest, right_result.inventory_digest)
            self.assertEqual(left_result.shadow_plan_digest, right_result.shadow_plan_digest)

    def test_artifact_directory_symlink_and_existing_run_are_refused_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real"
            real.mkdir()
            link = root / "artifacts"
            link.symlink_to(real, target_is_directory=True)
            manifest = package_manifest()
            paths = write_gate_files(root)
            paths["artifact_dir"] = str(link)
            adapter = SequenceAdapter([migration_inventory(), migration_inventory()])
            with self.assertRaises(MigrationError):
                discover_migration_state(
                    adapter,
                    source_bank=SOURCE,
                    candidate_bank=CANDIDATE,
                    offline_package_manifest=manifest,
                    approved_offline_package_digest=manifest["approved_manifest_digest"],
                    migration_paths=paths,
                    retain_watermark_reader=lambda: {"codex": {"epoch": 1}},
                    private_catalog_digests={"catalog": SHA_A},
                    repository_catalog=repository_catalog(),
                    timestamp="20260713T120000Z",
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = write_gate_files(root)
            run = Path(paths["artifact_dir"]) / "controller-discovery-20260713T120000Z"
            run.mkdir(parents=True)
            sentinel = run / "inventory.json"
            sentinel.write_text("do-not-overwrite", encoding="utf-8")
            manifest = package_manifest()
            adapter = SequenceAdapter([migration_inventory(), migration_inventory()])
            with self.assertRaises(MigrationError):
                discover_migration_state(
                    adapter,
                    source_bank=SOURCE,
                    candidate_bank=CANDIDATE,
                    offline_package_manifest=manifest,
                    approved_offline_package_digest=manifest["approved_manifest_digest"],
                    migration_paths=paths,
                    retain_watermark_reader=lambda: {"codex": {"epoch": 1}},
                    private_catalog_digests={"catalog": SHA_A},
                    repository_catalog=repository_catalog(),
                    timestamp="20260713T120000Z",
                )
            self.assertEqual(sentinel.read_text(), "do-not-overwrite")

    def test_artifact_directory_inside_git_worktree_is_refused_before_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = write_gate_files(root)
            fake_worktree = root / "fake-worktree"
            fake_worktree.mkdir()
            (fake_worktree / ".git").mkdir()
            forbidden = fake_worktree / "migration-artifacts-must-not-exist"
            paths["artifact_dir"] = str(forbidden)
            manifest = package_manifest()
            adapter = SequenceAdapter(
                [migration_inventory(), migration_inventory()]
            )
            with self.assertRaisesRegex(MigrationError, "outside a Git worktree"):
                discover_migration_state(
                    adapter,
                    source_bank=SOURCE,
                    candidate_bank=CANDIDATE,
                    offline_package_manifest=manifest,
                    approved_offline_package_digest=manifest[
                        "approved_manifest_digest"
                    ],
                    migration_paths=paths,
                    retain_watermark_reader=lambda: {
                        "codex": {"epoch": 1}
                    },
                    private_catalog_digests={"catalog": SHA_A},
                    repository_catalog=repository_catalog(),
                    timestamp="20260713T120000Z",
                )
            self.assertFalse(forbidden.exists())

    def test_artifact_entry_replacement_is_rejected_without_deleting_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            directory_descriptor = os.open(root, os.O_RDONLY)
            original_fsync = os.fsync
            swapped = False

            def replace_after_file_sync(descriptor):
                nonlocal swapped
                original_fsync(descriptor)
                if descriptor != directory_descriptor and not swapped:
                    swapped = True
                    os.unlink("inventory.json", dir_fd=directory_descriptor)
                    replacement = os.open(
                        "inventory.json",
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=directory_descriptor,
                    )
                    try:
                        os.write(replacement, b"replacement")
                        original_fsync(replacement)
                    finally:
                        os.close(replacement)

            try:
                with patch.object(
                    migration_module.os,
                    "fsync",
                    side_effect=replace_after_file_sync,
                ):
                    with self.assertRaisesRegex(MigrationError, "identity changed"):
                        migration_module._write_exclusive(
                            directory_descriptor,
                            "inventory.json",
                            {"trusted": True},
                        )
                self.assertEqual(
                    (root / "inventory.json").read_bytes(), b"replacement"
                )
            finally:
                os.close(directory_descriptor)

    def test_failed_discovery_cleanup_preserves_replaced_artifact_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original_write = migration_module._write_exclusive
            writes = 0

            def replace_first_then_fail(directory_descriptor, name, value):
                nonlocal writes
                writes += 1
                if writes == 1:
                    identity = original_write(directory_descriptor, name, value)
                    os.unlink(name, dir_fd=directory_descriptor)
                    replacement = os.open(
                        name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=directory_descriptor,
                    )
                    try:
                        os.write(replacement, b"replacement")
                    finally:
                        os.close(replacement)
                    return identity
                raise MigrationError("injected second artifact failure")

            with patch.object(
                migration_module,
                "_write_exclusive",
                side_effect=replace_first_then_fail,
            ):
                with self.assertRaisesRegex(
                    MigrationError, "injected second artifact failure"
                ):
                    self.discover(root)
            artifact_root = root / "artifacts"
            run = artifact_root / "controller-discovery-20260713T120000Z"
            self.assertFalse(run.exists())
            staging = list(
                artifact_root.glob(
                    ".controller-discovery-20260713T120000Z.*.tmp"
                )
            )
            self.assertEqual(len(staging), 1)
            self.assertEqual(
                (staging[0] / "inventory.json").read_bytes(), b"replacement"
            )

    def test_post_yield_root_validation_failure_rolls_back_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original_validate = migration_module._validate_private_directory
            calls = 0

            def fail_final_root_validation(metadata):
                nonlocal calls
                calls += 1
                if calls == 4:
                    raise MigrationError("injected post-yield validation failure")
                return original_validate(metadata)

            with patch.object(
                migration_module,
                "_validate_private_directory",
                side_effect=fail_final_root_validation,
            ):
                with self.assertRaisesRegex(
                    MigrationError, "post-yield validation failure"
                ):
                    self.discover(root)
            artifact_root = root / "artifacts"
            self.assertFalse(
                (
                    artifact_root
                    / "controller-discovery-20260713T120000Z"
                ).exists()
            )
            self.assertEqual(list(artifact_root.iterdir()), [])

    def test_ancestor_swap_after_validation_cannot_redirect_artifact_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            configured_parent = root / "configured"
            configured_parent.mkdir()
            redirected = root / "redirected"
            redirected.mkdir()
            artifact_dir = configured_parent / "artifacts"
            paths = write_gate_files(root)
            paths["artifact_dir"] = str(artifact_dir)
            manifest = package_manifest()
            adapter = SequenceAdapter(
                [migration_inventory(), migration_inventory()]
            )
            original_reject = migration_module._reject_symlink_components
            swapped = False

            def swap_after_validation(path, label):
                nonlocal swapped
                original_reject(path, label)
                if not swapped:
                    swapped = True
                    configured_parent.rename(root / "validated-original")
                    configured_parent.symlink_to(
                        redirected, target_is_directory=True
                    )

            with patch.object(
                migration_module,
                "_reject_symlink_components",
                side_effect=swap_after_validation,
            ):
                with self.assertRaises(MigrationError):
                    discover_migration_state(
                        adapter,
                        source_bank=SOURCE,
                        candidate_bank=CANDIDATE,
                        offline_package_manifest=manifest,
                        approved_offline_package_digest=manifest[
                            "approved_manifest_digest"
                        ],
                        migration_paths=paths,
                        retain_watermark_reader=lambda: {
                            "codex": {"epoch": 1}
                        },
                        private_catalog_digests={"catalog": SHA_A},
                        repository_catalog=repository_catalog(),
                        timestamp="20260713T120000Z",
                    )
            self.assertFalse((redirected / "artifacts").exists())
            validated_artifacts = root / "validated-original" / "artifacts"
            self.assertFalse(
                (
                    validated_artifacts
                    / "controller-discovery-20260713T120000Z"
                ).exists()
            )


class MigrationCliContractTest(unittest.TestCase):
    def run_cli(self, *args, env=None):
        with tempfile.TemporaryDirectory(
            prefix="hindsight-migration-cli-state-"
        ) as state_directory:
            return subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "--state-dir",
                    state_directory,
                    *args,
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                env=env,
                timeout=10,
            )

    def test_migration_discover_requires_explicit_profile_and_read_only_flag(self):
        missing_profile = self.run_cli("migration", "discover", "--read-only")
        self.assertNotEqual(missing_profile.returncode, 0)
        self.assertIn("--profile", missing_profile.stderr)

        missing_read_only = self.run_cli("migration", "discover", "--profile", "example")
        self.assertNotEqual(missing_read_only.returncode, 0)
        self.assertIn("--read-only", missing_read_only.stderr)

    def test_migration_discover_uses_get_only_and_reports_unapproved_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seen = []
            handler_errors = []
            surfaces = {
                "engineering": bank_surface(BankRef("core", "engineering"), "source-1", SHA_A, invalidated=True),
                "historical-candidate": bank_surface(
                    BankRef("core", "historical-candidate"),
                    "candidate-1",
                    SHA_B,
                    invalidated=True,
                ),
            }

            def response_for(raw_path):
                parsed = urlparse(raw_path)
                if parsed.path == "/version":
                    return {"api_version": "0.8.4", "features": {"observations": True}}
                if parsed.path == "/v1/migration/generation":
                    return {"generation": "commit-42"}
                parts = parsed.path.split("/")
                bank_id = parts[4]
                suffix = "/" + "/".join(parts[5:])
                surface = surfaces[bank_id]
                query = parse_qs(parsed.query)
                if suffix == "/config":
                    return {"bank_id": bank_id, "config": surface["config"], "overrides": {}}
                if suffix == "/stats":
                    return {
                        "bank_id": bank_id,
                        "total_documents": surface["stats"]["documents"],
                        "total_memories": surface["stats"]["memories"],
                    }
                if suffix == "/observations/scopes":
                    return {"scopes": surface["scopes"]}
                if suffix == "/tags":
                    return {"items": surface["tags"], "total": len(surface["tags"]), "limit": 1000, "offset": 0}
                if suffix == "/documents":
                    items = [
                        {
                            "id": item["document_id"],
                            "updated_at": item["updated_at"],
                            "content_hash": item["content_digest"],
                            "created_at": item["updated_at"],
                            "text_length": len(item.get("content", "")),
                            "memory_unit_count": 1,
                            "tags": [],
                            "document_metadata": {},
                            "retain_params": {},
                        }
                        for item in surface["documents"]
                    ]
                    return {"items": items, "total": len(items), "limit": 1000, "offset": 0}
                if suffix == "/mental-models":
                    if query.get("offset") not in (None, ["0"]):
                        return {"items": []}
                    return {
                        "items": [
                            {
                                "id": item["model_id"],
                                "content": CONTENT_SENTINEL,
                                "trigger": None,
                            }
                            for item in surface["models"]
                        ]
                    }
                if suffix == "/directives":
                    if query.get("offset") not in (None, ["0"]):
                        return {"items": []}
                    return {"items": surface["directives"]}
                if suffix == "/webhooks":
                    return {"items": []}
                if suffix == "/memories/list":
                    items = [
                        {
                            "id": item["item_id"],
                            "document_id": item["source_document_id"],
                            "text": CONTENT_SENTINEL,
                            "invalidation_reason": "test-curation",
                        }
                        for item in surface["invalidated_memories"]
                    ]
                    return {"items": items, "total": len(items), "limit": 1000, "offset": 0}
                if suffix == "/operations" and query.get("status") in (["pending"], ["processing"]):
                    return {
                        "bank_id": bank_id,
                        "operations": [],
                        "total": 0,
                        "limit": int(query["limit"][0]),
                        "offset": int(query.get("offset", ["0"])[0]),
                    }
                raise AssertionError(f"unexpected read path: {raw_path}")

            class Handler(BaseHTTPRequestHandler):
                def record_request(handler):
                    seen.append((
                        handler.command,
                        handler.path,
                        handler.headers.get("Authorization"),
                    ))

                def do_GET(handler):
                    handler.record_request()
                    try:
                        raw = json.dumps(response_for(handler.path)).encode()
                    except Exception as error:
                        handler_errors.append(repr(error))
                        handler.send_response(500)
                        handler.send_header("Content-Length", "0")
                        handler.end_headers()
                    else:
                        handler.send_response(200)
                        handler.send_header("Content-Length", str(len(raw)))
                        handler.end_headers()
                        handler.wfile.write(raw)

                def send_error(handler, code, message=None, explain=None):
                    if code == 501:
                        handler.record_request()
                    super().send_error(code, message, explain)

                def log_message(self, *_args):
                    pass

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(thread.join, 2)
            self.addCleanup(server.shutdown)

            paths = write_gate_files(root)
            inventory_path = root / "controller-inventory.json"
            inventory_path.write_text(json.dumps({
                "schema_version": 1,
                "machine": {"base_port": server.server_port},
                "archetype": {},
                "profiles": [{
                    "id": "core",
                    "enabled": True,
                    "host": "127.0.0.1",
                    "port": server.server_port,
                    "tenant": "default",
                    "roles": {},
                    "data_classes": [],
                }],
                "providers": [],
                "banks": [
                    {"id": "engineering", "profile_id": "core", "data_class": "engineering", "authority": "none", "writable": False},
                    {"id": "historical-candidate", "profile_id": "core", "data_class": "engineering", "authority": "none", "writable": False},
                ],
                "harnesses": [],
                "migration": {"artifact_dir": paths["artifact_dir"], "proposal_log": paths["proposal_log"]},
                "policy": {"engineering_memory_enabled": False},
            }), encoding="utf-8")
            manifest = package_manifest()
            manifest_path = root / "offline-package.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            catalog_path = root / "catalog-digests.json"
            catalog_path.write_text(
                json.dumps({
                    "catalog": SHA_A,
                    "repository_catalog": repository_catalog(),
                }),
                encoding="utf-8",
            )
            watermark_path = root / "retain-watermarks.json"
            watermark_path.write_text(
                json.dumps({"codex": {"document_id": "source-1", "epoch": 4, "checkpoint": 9}}),
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["TEST_HINDSIGHT_TOKEN"] = "read-only-test-token"
            result = self.run_cli(
                "migration", "discover", "--read-only",
                "--inventory", str(inventory_path),
                "--profile", "core",
                "--source-bank", "engineering",
                "--candidate-bank", "historical-candidate",
                "--offline-package-manifest", str(manifest_path),
                "--approved-offline-package-digest", manifest["approved_manifest_digest"],
                "--private-catalog-digests", str(catalog_path),
                "--retain-watermarks", str(watermark_path),
                "--completion-marker", paths["completion_marker"],
                "--token-env", "TEST_HINDSIGHT_TOKEN",
                "--timestamp", "20260713T120000Z",
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout + repr(handler_errors) + repr(seen))
            output = json.loads(result.stdout)
            self.assertTrue(output["complete"])
            self.assertFalse(output["approved"])
            self.assertRegex(output["inventory_digest"], r"^[0-9a-f]{64}$")
            self.assertRegex(output["shadow_plan_digest"], r"^[0-9a-f]{64}$")
            self.assertGreater(len(seen), 2)
            self.assertTrue(all(method == "GET" for method, _path, _auth in seen))
            self.assertFalse(any(path.startswith("/v1/migrations/") for _method, path, _auth in seen))
            self.assertTrue(all(auth == "Bearer read-only-test-token" for _method, _path, auth in seen))
            artifact_bytes = b"".join(
                path.read_bytes()
                for path in Path(paths["artifact_dir"]).rglob("*")
                if path.is_file()
            )
            self.assertNotIn(CONTENT_SENTINEL.encode(), artifact_bytes)


if __name__ == "__main__":
    unittest.main()
