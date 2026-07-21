import json
import hashlib
import fcntl
import importlib.metadata as metadata
from io import BytesIO, StringIO
from contextlib import contextmanager, redirect_stdout
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import re
import ssl
import stat
import subprocess
from urllib.error import HTTPError
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TRUSTED_TEST_PYTHON = "/usr/bin/python3"
MIGRATION_ARTIFACT_DIGEST = "3" * 64
SOURCE_BANK_REF = {"profile_id": "core", "bank_id": "historical-candidate"}
TARGET_BANK_REF = {"profile_id": "core", "bank_id": "engineering"}
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from hindsight_memory_control_plane.adapters import AdapterError, AuthenticationError, FakeAdapter, RollbackBundle
from hindsight_memory_control_plane.http_adapter import (
    EndpointNotFoundError,
    HttpAdapter,
    SUPPORTED_RUNTIME_VERSION,
)
from hindsight_memory_control_plane.inventory import _resolved_artifact
from hindsight_memory_control_plane.file_evidence import (
    FileEvidenceError,
    read_file_evidence,
    reject_symlink_components,
    verified_file_snapshot,
)
from hindsight_memory_control_plane.migration_adapter import (
    AdminMigrationAdapter,
    MigrationAdapterError,
    MigrationApplyAdapter,
    RUNTIME_FILES_PROBE,
    hindsight_admin_argv,
)
import hindsight_memory_control_plane.migration_adapter as migration_adapter_module
import hindsight_memory_control_plane.ledger as ledger_module
import hindsight_memory_control_plane.reconcile as reconcile_module
from hindsight_memory_control_plane.canonical import canonical_bytes, digest
from hindsight_memory_control_plane.model import Action, BankRef, EndpointIdentity, Inventory, OperationSnapshot, Plan
from hindsight_memory_control_plane.planning import PlanError, _compatibility
from hindsight_memory_control_plane.reconcile import (
    ApplyError,
    _compatibility_satisfied,
    apply_plan,
    bind_migration_gate,
    capture_migration_gate,
    create_rollback_bundle,
    parse_migration_gate,
)


CONCURRENCY_TIMEOUT_SECONDS = 5
RUNTIME_OPERATION_ID = "00000000-0000-4000-8000-000000000001"
RUNTIME_OPERATION_ID_2 = "00000000-0000-4000-8000-000000000002"
RUNTIME_VERSION_RESPONSE = {
    "api_version": SUPPORTED_RUNTIME_VERSION,
    "features": {
        "worker": True, "audit_log": False, "llm_trace": False,
    },
}


def plan_for(state, *actions):
    endpoint = EndpointIdentity("core", "http", "127.0.0.1", 7979, "default")
    values = tuple(actions or (Action("01-create", "create_bank", {"bank": {"profile_id": "core", "bank_id": "engineering"}}),))
    body = {
        "schema_version": 1, "inventory_digest": "1" * 64, "artifact_digest": "2" * 64,
        "target_profile": "core", "target_endpoint": endpoint.to_dict(), "live_state_digest": digest(state),
        "operations": {"idle": True, "active": []}, "compatibility": [{
            "check": "fake-adapter-contract", "compatible": True,
            "status": "pass",
        }],
        "actions": [action.to_dict() for action in values], "destructive": False,
    }
    return Plan(1, "1" * 64, "2" * 64, "core", endpoint, digest(state), OperationSnapshot(True), tuple(body["compatibility"]), values, False, digest(body))


def empty_plan_for(state):
    plan = plan_for(state)
    body = plan.to_dict()
    body.pop("plan_digest")
    body["actions"] = []
    return replace(plan, actions=(), plan_digest=digest(body))


def mutation_action(identifier="migrate-1", artifact_digest=MIGRATION_ARTIFACT_DIGEST,
                    archive_digest="4" * 64):
    evidence = restore_evidence(archive_digest)
    return {
        "id": identifier,
        "kind": "migrate_bank",
        "artifact_digest": artifact_digest,
        "archive_digest": archive_digest,
        "restore_evidence_digest": digest(evidence),
        "source_bank": SOURCE_BANK_REF,
        "target_bank": TARGET_BANK_REF,
    }


def restore_evidence(artifact_digest, receipt_digest="7" * 64):
    return {
        "schema_version": 1,
        "artifact_digest": artifact_digest,
        "verification_receipt_digest": receipt_digest,
    }


def admin_argv(executable, operation, archive, bank_id):
    return {
        "export-bank": [
            executable, "export-bank", "--bank", bank_id,
            "--output", archive,
        ],
        "import-bank": [
            executable, "import-bank", "--archive", archive,
            "--target-bank", bank_id,
        ],
        "backup": [
            executable, "backup", archive, "--schema", "public",
        ],
        "restore": [
            executable, "restore", archive, "--schema", "public", "--yes",
        ],
    }[operation]


def inventory_for(port, *, scheme="http", host="127.0.0.1", tenant="default", approved_tls=False):
    endpoint = {"profile_id": "core", "scheme": scheme, "host": host, "port": port, "tenant": tenant}
    raw = {
        "schema_version": 1, "machine": {"base_port": port}, "archetype": {},
        "profiles": [{"id": "core", "slot": 0, "port": port, "scheme": scheme, "host": host, "tenant": tenant}],
        "providers": [],
        "banks": [{
            "id": "engineering",
            "profile_id": "core",
            "data_class": "engineering",
            "authority": "authoritative",
            "writable": True,
        }],
        "harnesses": [{
            "id": "codex",
            "profile_id": "core",
            "home_bank": {"profile_id": "core", "bank_id": "engineering"},
            "write_bank": {"profile_id": "core", "bank_id": "engineering"},
        }],
        "migration": {},
        "policy": {"approved_tls_endpoints": [endpoint] if approved_tls else []},
    }
    artifact = _resolved_artifact(
        raw, base_port=port, machine_engineering_enabled=False
    )
    return Inventory(
        1,
        raw["machine"],
        raw["archetype"],
        tuple(raw["profiles"]),
        (),
        tuple(raw["banks"]),
        tuple(raw["harnesses"]),
        raw["migration"],
        raw["policy"],
        digest(raw),
        digest(artifact),
    )


def write_migration_gate(root, run_id, artifact_digest):
    artifact_dir = root / "artifacts"
    artifact_dir.mkdir(parents=True)
    marker = artifact_dir / "distillation-complete.marker"
    proposal = root / "proposal-log.md"
    marker.write_text(f"run={run_id}\nartifact={artifact_digest}\n", encoding="utf-8")
    proposal.write_text(
        f"# Migration proposals\n\n## Migration complete\nrun={run_id}\nartifact={artifact_digest}\n",
        encoding="utf-8",
    )
    return capture_migration_gate(marker, proposal), marker, proposal


def start_http_server(test_case, handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def cleanup():
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    test_case.addCleanup(cleanup)
    return server


class AdapterContractMixin:
    def assert_operation(self, method, path):
        raise NotImplementedError

    def test_schema_version(self):
        self.assertEqual(self.adapter.schema_version(), 1)
        self.assert_operation("GET", "/v1/schema")

    def test_endpoint_identity(self):
        self.assertEqual(self.adapter.endpoint_identity(), self.endpoint)
        self.assert_operation("GET", "/v1/identity")

    def test_snapshot(self):
        self.assertEqual(
            self.adapter.snapshot(),
            {
                "endpoint": self.endpoint.to_dict(),
                "state": self.state,
                "operations": self.operations,
                "compatibility": self.compatibility,
            },
        )

    def test_read_config(self):
        self.assertEqual(self.adapter.read_config(), {"mode": "safe"})
        self.assert_operation("GET", "/v1/config")

    def test_read_stats(self):
        self.assertEqual(self.adapter.read_stats(), {"count": 2})
        self.assert_operation("GET", "/v1/stats")

    def test_read_tags(self):
        self.assertEqual(self.adapter.read_tags(), {"tags": ["a"]})
        self.assert_operation("GET", "/v1/tags")

    def test_read_scopes(self):
        self.assertEqual(self.adapter.read_scopes(), {"scopes": ["s"]})
        self.assert_operation("GET", "/v1/scopes")

    def test_read_documents(self):
        self.assertEqual(self.adapter.read_documents(), {"documents": [{"id": "d"}]})
        self.assert_operation("GET", "/v1/documents")

    def test_read_models(self):
        self.assertEqual(self.adapter.read_models(), {"models": [{"id": "m0"}]})
        self.assert_operation("GET", "/v1/models")

    def test_read_directives(self):
        self.assertEqual(self.adapter.read_directives(), {"directives": [{"id": "r0"}]})
        self.assert_operation("GET", "/v1/directives")

    def test_read_operations(self):
        self.assertEqual(self.adapter.read_operations(), self.operations)
        self.assert_operation("GET", "/v1/operations")

    def test_template_dry_run(self):
        value = {"template": "t"}
        self.assertEqual(self.adapter.template_dry_run(value), {"valid": True, "digest": digest(value)})
        self.assert_operation("POST", "/v1/templates/dry-run")

    def test_export_template(self):
        self.assertEqual(self.adapter.export_template(), {"template": "exported"})
        self.assert_operation("GET", "/v1/templates/export")

    def test_import_template(self):
        self.assertEqual(self.adapter.import_template({"template": "new"}), {"imported": True})
        self.assert_operation("POST", "/v1/templates/import")

    def test_patch_config(self):
        self.assertEqual(self.adapter.patch_config({"mode": "active"}), {"mode": "active"})
        self.assert_operation("PATCH", "/v1/config")

    def test_upsert_model(self):
        self.assertEqual(self.adapter.upsert_model({"id": "m"}), {"upserted": "m"})
        self.assert_operation("PUT", "/v1/models")

    def test_upsert_directive(self):
        self.assertEqual(self.adapter.upsert_directive({"id": "r"}), {"upserted": "r"})
        self.assert_operation("PUT", "/v1/directives")

    def test_transfer_documents(self):
        self.assertEqual(self.adapter.transfer_documents({"count": 2}), {"transferred": 2})
        self.assert_operation("POST", "/v1/documents/transfer")

    def test_invalidated_memory_inventory(self):
        self.assertEqual(self.adapter.read_invalidated_memories(), {"invalidated_memories": [{"id": "i"}]})
        self.assert_operation("GET", "/v1/memories/invalidated")

    def test_reapply_invalidated_memories(self):
        self.assertEqual(self.adapter.reapply_invalidated_memories({"count": 2}), {"reapplied": 2})
        self.assert_operation("POST", "/v1/memories/invalidated/reapply")

    def test_delete_bank(self):
        self.assertEqual(self.adapter.delete_bank({"bank_id": "b"}), {"deleted": True})
        self.assert_operation("DELETE", "/v1/banks")

    def test_runtime_memory_reads(self):
        self.assertEqual(self.adapter.recall({"query": "q", "limit": 2}), {"memories": [{"id": "m1"}]})
        self.assert_operation("POST", "/v1/runtime/recall")
        self.assertEqual(self.adapter.mental_model_fetch({"model_id": "model1"}), {"models": [{"id": "model1"}]})
        self.assert_operation("POST", "/v1/runtime/mental-model")
        self.assertEqual(self.adapter.session_status({"session_id": "session-1"}), {"status": "ready"})
        self.assert_operation("POST", "/v1/runtime/session-status")

    def test_runtime_memory_writes_are_idempotent(self):
        checkpoint = {
            "session_id": "session-1", "document_id": "d",
            "epoch": 1, "checkpoint": 2,
            "content": "complete cleaned transcript",
            "idempotency_key": "a" * 64,
        }
        retain = {
            "session_id": "session-1",
            "document_id": checkpoint["document_id"],
            "epoch": checkpoint["epoch"],
            "checkpoint": checkpoint["checkpoint"],
            "outcome": "done", "idempotency_key": "c" * 64,
        }
        reflection = {"reflection": "note"}
        self.assertEqual(self.adapter.transcript_checkpoint(checkpoint), {"applied": True})
        self.assert_operation("PUT", "/v1/runtime/transcript-checkpoint")
        self.assertEqual(self.adapter.retain_outcome(retain), {"retained": True})
        self.assert_operation("PUT", "/v1/runtime/outcome")
        self.assertEqual(self.adapter.reflect(reflection), {"reflection": "note"})
        self.assert_operation("PUT", "/v1/runtime/reflection")
        before = len(getattr(self.adapter, "calls", getattr(self, "seen", [])))
        self.assertEqual(self.adapter.retain_outcome(retain), {"retained": True})
        after = len(getattr(self.adapter, "calls", getattr(self, "seen", [])))
        self.assertEqual(after, before)
        with self.assertRaisesRegex(AdapterError, "digest drift"):
            self.adapter.retain_outcome({**retain, "outcome": "changed"})

    def test_runtime_memory_payloads_are_closed(self):
        with self.assertRaisesRegex(AdapterError, "schema"):
            self.adapter.recall({"query": "q", "endpoint": "http://forbidden"})
        with self.assertRaisesRegex(AdapterError, "schema"):
            self.adapter.retain_outcome({
                "session_id": "session-1", "document_id": "d",
                "epoch": 1, "checkpoint": 1, "outcome": "done",
                "idempotency_key": "a" * 64, "token": "forbidden",
            })

    def test_create_verify_and_restore_rollback(self):
        bundle = self.adapter.create_rollback_bundle("a" * 64, ("action-1",))
        self.assertIsInstance(bundle, RollbackBundle)
        self.assertTrue(self.adapter.verify_rollback_bundle(bundle))
        self.adapter.restore(bundle)
        self.assert_rollback_contract(bundle)


class FakeAdapterContractTest(AdapterContractMixin, unittest.TestCase):
    def setUp(self):
        self.endpoint = EndpointIdentity("core", "http", "127.0.0.1", 7979, "default")
        self.operations = {"idle": True, "active": []}
        self.compatibility = [{
            "check": "fake-adapter-contract",
            "compatible": True,
            "status": "pass",
        }]
        self.state = {
            "config": {"mode": "safe"}, "stats": {"count": 2}, "tags": {"tags": ["a"]},
            "scopes": {"scopes": ["s"]}, "documents": {"documents": [{"id": "d"}]},
            "models": {"models": [{"id": "m0"}]}, "directives": {"directives": [{"id": "r0"}]},
            "invalidated_memories": {"invalidated_memories": [{"id": "i"}]}, "template": {"template": "exported"},
            "migration_inventory": {"schema_version": 1, "banks": ["engineering", "historical-candidate"]},
        }
        self.adapter = FakeAdapter(endpoint=self.endpoint.to_dict(), state=self.state, operations=self.operations)

    def test_upsert_into_fresh_state_keeps_collections_list_shaped(self):
        adapter = FakeAdapter(endpoint=self.endpoint.to_dict())
        adapter.upsert_model({"id": "summary", "revision": "v1"})
        adapter.upsert_directive(
            {"id": "grounded", "text": "Stay grounded."}
        )
        self.assertEqual(
            adapter.state["models"], [{"id": "summary", "revision": "v1"}]
        )
        self.assertEqual(
            adapter.state["directives"],
            [{"id": "grounded", "text": "Stay grounded."}],
        )

    def test_read_migration_inventory_is_bank_scoped_and_read_only(self):
        source = BankRef("core", "engineering")
        candidate = BankRef("core", "historical-candidate")
        self.assertEqual(
            self.adapter.read_migration_inventory(source, candidate),
            {"schema_version": 1, "banks": ["engineering", "historical-candidate"]},
        )
        self.assertEqual(self.adapter.calls[-1]["method"], "read_migration_inventory")
        self.assertEqual(
            self.adapter.calls[-1]["metadata"],
            {"source_bank": source.to_dict(), "candidate_bank": candidate.to_dict()},
        )

    def test_committed_mutations_advance_migration_generation(self):
        adapter = FakeAdapter(
            endpoint=self.endpoint.to_dict(),
            state={"migration_generation": "generation-1"},
        )
        generations = [adapter.read_migration_generation()]
        mutations = (
            lambda: adapter.import_template({"template": "value"}),
            lambda: adapter.patch_config({"mode": "active"}),
            lambda: adapter.upsert_model({"id": "model-1"}),
            lambda: adapter.upsert_directive({"id": "directive-1"}),
            lambda: adapter.transfer_documents({"count": 1}),
            lambda: adapter.reapply_invalidated_memories({"count": 1}),
            lambda: adapter.delete_bank({"bank_id": "retired"}),
        )
        for mutate in mutations:
            mutate()
            generations.append(adapter.read_migration_generation())

        runtime_writes = (
            (
                adapter.transcript_checkpoint,
                {
                    "session_id": "session-1",
                    "document_id": "document-1",
                    "epoch": 1,
                    "checkpoint": 1,
                    "content": "complete cleaned transcript",
                    "idempotency_key": "a" * 64,
                },
            ),
            (
                adapter.retain_outcome,
                {
                    "session_id": "session-1",
                    "document_id": "document-1",
                    "epoch": 1,
                    "checkpoint": 1,
                    "outcome": "done",
                    "idempotency_key": "b" * 64,
                },
            ),
        )
        for mutate, request in runtime_writes:
            mutate(request)
            committed_generation = adapter.read_migration_generation()
            generations.append(committed_generation)
            mutate(request)
            self.assertEqual(
                adapter.read_migration_generation(), committed_generation
            )

        generation_before_reflect = adapter.read_migration_generation()
        adapter.reflect({"reflection": "note"})
        self.assertEqual(
            adapter.read_migration_generation(), generation_before_reflect
        )

        self.assertEqual(len(generations), len(set(generations)))

    def test_rollback_snapshot_excludes_generation_but_restore_advances_it(self):
        adapter = FakeAdapter(
            endpoint=self.endpoint.to_dict(),
            state={"config": {"mode": "safe"}, "migration_generation": "g"},
        )
        plan = plan_for({"config": {"mode": "safe"}})
        bundle = create_rollback_bundle(plan, adapter)
        self.assertEqual(bundle.prestate_digest, digest({"config": {"mode": "safe"}}))
        self.assertNotIn("migration_generation", adapter.snapshot()["state"])
        adapter.patch_config({"mode": "changed"})
        before_restore = adapter.read_migration_generation()
        adapter.restore(bundle)
        self.assertEqual(adapter.state["config"], {"mode": "safe"})
        self.assertNotEqual(adapter.read_migration_generation(), before_restore)

    def test_fake_apply_binding_requires_its_exact_verified_rollback(self):
        bundle = self.adapter.create_rollback_bundle(
            "a" * 64, ("action-1",)
        )
        self.assertFalse(
            self.adapter._rollbacks[bundle.rollback_id]["verified"]
        )
        self.assertTrue(self.adapter.verify_rollback_bundle(bundle))
        self.adapter.bind_apply_plan(bundle)

        forged = RollbackBundle(
            "unknown",
            bundle.plan_digest,
            bundle.action_ids,
            bundle.prestate_digest,
            bundle.endpoint_digest,
            bundle.bundle_digest,
            bundle.restore_proof_digest,
        )
        with self.assertRaisesRegex(AdapterError, "exact verified rollback"):
            self.adapter.bind_apply_plan(forged)

        unverified = FakeAdapter(
            endpoint=self.endpoint.to_dict(),
            state={"documents": [{"id": "document-1"}]},
            restore_proof_valid=False,
        )
        rejected = unverified.create_rollback_bundle(
            "b" * 64, ("action-1",)
        )
        with self.assertRaisesRegex(AdapterError, "exact verified rollback"):
            unverified.bind_apply_plan(rejected)

    def test_fake_rollback_eligibility_matches_restore_proof(self):
        adapter = FakeAdapter(
            endpoint=self.endpoint.to_dict(),
            state={"config": {"mode": "safe"}},
            restore_proof_valid=False,
        )
        bundle = adapter.create_rollback_bundle("a" * 64, ("action-1",))
        self.assertFalse(adapter.verify_rollback_bundle(bundle))

    def test_fake_binding_tracks_pending_action_until_successful_verification(self):
        action = Action("action-1", "create_bank", {})
        bundle = self.adapter.create_rollback_bundle(
            "a" * 64, (action.id,)
        )
        self.assertTrue(self.adapter.verify_rollback_bundle(bundle))
        self.adapter.bind_apply_plan(bundle)
        with self.assertRaisesRegex(AdapterError, "applied action"):
            self.adapter.verify_postcondition(action)

        self.adapter.apply_action(action)
        with self.assertRaisesRegex(AdapterError, "already active"):
            self.adapter.bind_apply_plan(bundle)
        self.adapter.fail_postcondition_for = action.id
        self.assertFalse(self.adapter.verify_postcondition(action))
        with self.assertRaisesRegex(AdapterError, "already active"):
            self.adapter.bind_apply_plan(bundle)

        self.adapter.fail_postcondition_for = None
        self.assertTrue(self.adapter.verify_postcondition(action))
        self.adapter.bind_apply_plan(bundle)

    def test_fake_binding_detects_operation_drift_without_restoring_operations(self):
        action = Action("action-1", "create_bank", {})
        bundle = self.adapter.create_rollback_bundle(
            "a" * 64, (action.id,)
        )
        self.assertTrue(self.adapter.verify_rollback_bundle(bundle))
        self.adapter.operations = {
            "idle": False,
            "active": [
                {"id": "external", "kind": "retain", "status": "running"}
            ],
        }
        with self.assertRaisesRegex(AdapterError, "exact verified rollback"):
            self.adapter.bind_apply_plan(bundle)

        self.adapter.operations = {"idle": True, "active": []}
        self.adapter.bind_apply_plan(bundle)
        self.adapter.operations = {
            "idle": False,
            "active": [
                {"id": "external", "kind": "retain", "status": "running"}
            ],
        }
        with self.assertRaisesRegex(AdapterError, "approved plan state"):
            self.adapter.preflight_action(action)
        self.adapter.restore(bundle)
        self.assertFalse(self.adapter.operations["idle"])

    def test_external_action_is_consumed_and_pending_before_callback(self):
        action = Action("action-1", "migrate_bank", {})
        bundle = self.adapter.create_rollback_bundle(
            "a" * 64, (action.id,)
        )
        self.assertTrue(self.adapter.verify_rollback_bundle(bundle))
        self.adapter.bind_apply_plan(bundle)
        observed = []

        def mutation():
            observed.append(
                (tuple(self.adapter._apply_action_ids), self.adapter._pending_action_id)
            )
            with self.assertRaisesRegex(AdapterError, "approved plan state"):
                self.adapter.attest_external_action(action, lambda: None)

        self.adapter.attest_external_action(action, mutation)

        self.assertEqual(observed, [((), action.id)])
        self.assertTrue(self.adapter.verify_postcondition(action))

    def test_fake_restore_clears_all_apply_transaction_state(self):
        first = Action("action-1", "create_bank", {})
        second = Action("action-2", "reload_profile", {})
        bundle = self.adapter.create_rollback_bundle(
            "a" * 64, (first.id, second.id)
        )
        self.assertTrue(self.adapter.verify_rollback_bundle(bundle))
        self.adapter.bind_apply_plan(bundle)
        self.adapter.apply_action(first)
        self.assertTrue(self.adapter.verify_postcondition(first))
        self.adapter.apply_action(second)

        self.adapter.restore(bundle)

        self.assertIsNone(self.adapter._apply_binding)
        self.assertEqual(self.adapter._apply_action_ids, [])
        self.assertIsNone(self.adapter._apply_expected_state_digest)
        self.assertIsNone(self.adapter._pending_action_id)
        self.assertEqual(self.adapter._verified_action_ids, set())

    def test_fake_restore_rejects_invalid_restore_proof_before_state_lookup(self):
        adapter = FakeAdapter(
            endpoint=self.endpoint.to_dict(),
            state={"config": {"mode": "safe"}},
            restore_proof_valid=False,
        )
        bundle = adapter.create_rollback_bundle("a" * 64, ("action-1",))
        adapter.state["config"] = {"mode": "changed"}

        with self.assertRaisesRegex(AdapterError, "restore proof"):
            adapter.restore(bundle)

        self.assertEqual(adapter.state["config"], {"mode": "changed"})

    def assert_operation(self, method, path):
        expected = {
            "/v1/schema": "schema_version", "/v1/identity": "endpoint_identity", "/v1/config": "read_config" if method == "GET" else "patch_config",
            "/v1/stats": "read_stats", "/v1/tags": "read_tags", "/v1/scopes": "read_scopes", "/v1/documents": "read_documents",
            "/v1/models": "read_models" if method == "GET" else "upsert_model", "/v1/directives": "read_directives" if method == "GET" else "upsert_directive",
            "/v1/operations": "read_operations", "/v1/templates/dry-run": "template_dry_run", "/v1/templates/export": "export_template",
            "/v1/templates/import": "import_template", "/v1/documents/transfer": "transfer_documents",
            "/v1/memories/invalidated": "read_invalidated_memories", "/v1/memories/invalidated/reapply": "reapply_invalidated_memories",
            "/v1/banks": "delete_bank",
            "/v1/runtime/recall": "recall", "/v1/runtime/mental-model": "mental_model_fetch",
            "/v1/runtime/session-status": "session_status", "/v1/runtime/transcript-checkpoint": "transcript_checkpoint",
            "/v1/runtime/outcome": "retain_outcome", "/v1/runtime/reflection": "reflect",
        }[path]
        self.assertEqual(self.adapter.calls[-1]["method"], expected)
        self.assertNotIn("top-secret", json.dumps(self.adapter.calls))

    def assert_rollback_contract(self, bundle):
        self.assertEqual([call["method"] for call in self.adapter.calls[-3:]],
                         ["create_rollback_bundle", "verify_rollback_bundle", "restore"])
        self.assertNotIn("documents", json.dumps(self.adapter.calls[-3:]))


class HttpAdapterContractTest(AdapterContractMixin, unittest.TestCase):
    def setUp(self):
        self.seen = []
        self.operations = {"idle": True, "active": []}
        self.compatibility = []
        state = {
            "config": {"mode": "safe"}, "stats": {"count": 2}, "tags": {"tags": ["a"]},
            "scopes": {"scopes": ["s"]}, "documents": {"documents": [{"id": "d"}]},
            "models": {"models": [{"id": "m0"}]}, "directives": {"directives": [{"id": "r0"}]},
            "invalidated_memories": {"invalidated_memories": [{"id": "i"}]}, "template": {"template": "exported"},
        }
        snapshot_state = {
            **state,
            "profile_artifact_digest": "a" * 64,
            "banks": [{
                "id": "engineering",
                "profile_id": "core",
                "artifact_digest": "b" * 64,
                "enable_auto_consolidation": True,
                "memory_defense": "sensitive_data",
                "models": [{
                    "id": "summary",
                    "revision": "v1",
                    "artifact_digest": "c" * 64,
                    "prompt": "private-bank-model",
                }],
                "directives": [{
                    "id": "privacy",
                    "artifact_digest": "d" * 64,
                    "mission": "private-bank-directive",
                }],
                "private_bank_value": "private-bank-value",
            }],
            "api_token": "private-top-level-token",
            "nested": {"client_secret": "private-nested-secret"},
            "tags": {"tags": ["repo:dotfiles"]},
            "models": {"models": [{"id": "m0", "prompt": "private-model"}]},
            "directives": {"directives": [{"id": "r0", "mission": "private-directive"}]},
            "documents": {
                "documents": [
                    {"id": "d", "document_metadata": {"token": "private"}}
                ]
            },
        }
        responses = {
            ("GET", "/v1/schema"): {"schema_version": 1}, ("GET", "/v1/state"): snapshot_state,
            ("GET", "/v1/compatibility"): {"compatibility": []},
            ("GET", "/v1/config"): state["config"], ("GET", "/v1/stats"): state["stats"],
            ("GET", "/v1/tags"): state["tags"], ("GET", "/v1/scopes"): state["scopes"],
            ("GET", "/v1/documents"): state["documents"], ("GET", "/v1/models"): state["models"],
            ("GET", "/v1/directives"): state["directives"], ("GET", "/v1/operations"): self.operations,
            ("GET", "/v1/memories/invalidated"): state["invalidated_memories"], ("GET", "/v1/templates/export"): state["template"],
            ("POST", "/v1/templates/dry-run"): {"valid": True, "digest": digest({"template": "t"})},
            ("POST", "/v1/templates/import"): {"imported": True}, ("PATCH", "/v1/config"): {"mode": "active"},
            ("PUT", "/v1/models"): {"upserted": "m"}, ("PUT", "/v1/directives"): {"upserted": "r"},
            ("POST", "/v1/documents/transfer"): {"transferred": 2},
            ("POST", "/v1/memories/invalidated/reapply"): {"reapplied": 2}, ("DELETE", "/v1/banks"): {"deleted": True},
            ("POST", "/v1/default/banks/engineering/memories/recall"): {
                "results": [{"id": "m1", "text": "remembered", "type": "world"}],
                "trace": None,
                "entities": {
                    "entity": {
                        "entity_id": "entity-1",
                        "canonical_name": "Entity",
                        "observations": [{"text": "observed"}],
                    },
                },
                "chunks": {
                    "chunk-1": {
                        "id": "chunk-1", "text": "source chunk",
                        "chunk_index": 0, "truncated": False,
                    },
                },
                "source_facts": {
                    "source-1": {
                        "id": "source-1", "text": "source fact",
                        "type": "experience",
                    },
                },
            },
            ("GET", "/v1/default/banks/engineering/mental-models/model1?detail=content"): {
                "id": "model1", "bank_id": "engineering", "name": "Model",
                "content": "model content", "tags": [],
            },
            ("GET", "/v1/default/banks/engineering/operations?status=pending&limit=1&offset=0"): {
                "bank_id": "engineering", "operations": [], "total": 0,
                "limit": 1, "offset": 0,
            },
            ("GET", "/v1/default/banks/engineering/operations?status=processing&limit=1&offset=0"): {
                "bank_id": "engineering", "operations": [], "total": 0,
                "limit": 1, "offset": 0,
            },
            ("GET", f"/v1/default/banks/engineering/operations/{RUNTIME_OPERATION_ID}"): {
                "operation_id": RUNTIME_OPERATION_ID, "status": "completed",
            },
            ("POST", "/v1/default/banks/engineering/memories"): {
                "success": True, "bank_id": "engineering", "items_count": 1,
                "async": True, "operation_id": RUNTIME_OPERATION_ID,
            },
            ("POST", "/v1/default/banks/engineering/reflect"): {
                "text": "reflected answer",
                "based_on": {
                    "memories": [{"id": "memory-1", "text": "private"}],
                    "mental_models": [{"id": "model-1", "text": "private"}],
                    "directives": [{
                        "id": "directive-1", "name": "private",
                        "content": "private",
                    }],
                },
            },
        }
        responses[("GET", "/version")] = {
            "api_version": SUPPORTED_RUNTIME_VERSION,
            "features": {
                "observations": True,
                "worker": True,
                "audit_log": False,
                "llm_trace": False,
                "private_feature": "private-feature-content",
            },
            "release_notes": "private-version-content",
        }
        responses[("GET", "/v1/migration/generation")] = {"generation": "commit-42"}
        for index, bank_id in enumerate(("engineering", "historical-candidate"), start=1):
            base = f"/v1/default/banks/{bank_id}"
            responses[("GET", f"{base}/config")] = {
                "bank_id": bank_id,
                "config": {
                    "recall_max_tokens": 4096,
                    "api_key": "top-secret",
                    "provider": {"api_key": "nested-secret", "model": "safe-model"},
                },
                "overrides": {},
            }
            responses[("GET", f"{base}/stats")] = {
                "bank_id": bank_id,
                "total_documents": 1,
                "private_summary": "private-stats-content",
            }
            responses[("GET", f"{base}/observations/scopes")] = {
                "scopes": [{
                    "scope": "repo:dotfiles",
                    "count": 1,
                    "description": "private-scope-content",
                }],
                "private_summary": "private-scope-summary",
            }
            responses[("GET", f"{base}/tags?limit=1000&offset=0")] = {
                "items": [{"tag": "repo:dotfiles", "count": 1}], "total": 1, "limit": 1000, "offset": 0,
            }
            responses[("GET", f"{base}/documents?limit=1000&offset=0")] = {
                "items": [{
                    "id": f"document-{index}", "updated_at": "2026-07-13T12:00:00Z",
                    "content_hash": str(index) * 64, "created_at": "2026-07-13T11:00:00Z",
                    "text_length": 12, "memory_unit_count": 1, "tags": [],
                    "document_metadata": {"token": "private-document"},
                    "retain_params": {"mission": "private-retain"},
                }],
                "total": 1, "limit": 1000, "offset": 0,
            }
            responses[("GET", f"{base}/mental-models?detail=full&limit=1000&offset=0")] = {
                "items": [{"id": f"model-{index}", "prompt": "private-model"}]
            }
            responses[("GET", f"{base}/mental-models?detail=full&limit=1000&offset=1")] = {
                "items": []
            }
            responses[("GET", f"{base}/directives?active_only=false&limit=1000&offset=0")] = {
                "items": [{"id": f"directive-{index}", "mission": "private-directive"}]
            }
            responses[("GET", f"{base}/directives?active_only=false&limit=1000&offset=1")] = {
                "items": []
            }
            responses[("GET", f"{base}/webhooks")] = {
                "items": [{
                    "id": f"hook-{index}",
                    "target": {
                        "url": f"https://hooks.example/{index}",
                        "authorization": "private-target-token",
                    },
                    "activation": {"enabled": True, "events": ["retain"]},
                    "config": {"timeout": 5, "secret": "private-config"},
                    "bank_role": "forged-role",
                    "hook_id": "forged-id",
                }]
            }
            responses[("GET", f"{base}/memories/list?state=invalidated&limit=1000&offset=0")] = {
                "items": [], "total": 0, "limit": 1000, "offset": 0,
            }
            for status in ("pending", "processing"):
                responses[("GET", f"{base}/operations?status={status}&limit=100&offset=0")] = {
                    "bank_id": bank_id, "operations": [], "total": 0, "limit": 100, "offset": 0,
                }
        self.responses = responses

        class Handler(BaseHTTPRequestHandler):
            def _serve(handler):
                length = int(handler.headers.get("Content-Length", "0"))
                request_body = json.loads(handler.rfile.read(length) or b"{}")
                self.seen.append((handler.command, handler.path, handler.headers.get("Authorization"), request_body))
                if handler.path == "/v1/identity":
                    value = self.endpoint.to_dict()
                elif handler.path == "/v1/rollbacks":
                    body = {"rollback_id": "server-rb-1", "plan_digest": request_body["plan_digest"], "action_ids": request_body["action_ids"],
                            "prestate_digest": digest(state), "endpoint_digest": digest(self.endpoint.to_dict())}
                    bundle_digest = digest(body)
                    value = {**body, "bundle_digest": bundle_digest, "restore_proof_digest": digest({"bundle_digest": bundle_digest})}
                elif handler.path.endswith("/verify"):
                    value = {
                        "verified": True,
                        "rollback_id": request_body["rollback_id"],
                        "bundle_digest": request_body["bundle_digest"],
                        "prestate_digest": request_body["prestate_digest"],
                    }
                elif handler.path.endswith("/restore"):
                    value = {
                        "restored": True,
                        "rollback_id": request_body["rollback_id"],
                        "bundle_digest": request_body["bundle_digest"],
                        "prestate_digest": request_body["prestate_digest"],
                    }
                else:
                    value = responses[(handler.command, handler.path)]
                raw = json.dumps(value).encode()
                handler.send_response(200)
                handler.send_header("Content-Length", str(len(raw)))
                handler.end_headers()
                handler.wfile.write(raw)
            do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = _serve
            def log_message(self, *_args): pass

        self.server = start_http_server(self, Handler)
        self.endpoint = EndpointIdentity("core", "http", "127.0.0.1", self.server.server_port, "default")
        self.state = state
        self.adapter = HttpAdapter(inventory=inventory_for(self.server.server_port), profile_id="core", token_resolver=lambda: "contract-token")

    def test_runtime_memory_reads(self):
        self.assertEqual(
            self.adapter.recall({"query": "q", "limit": 2}),
            {
                "memories": [{
                    "id": "m1", "text": "remembered", "type": "world",
                }],
                "entities": [{
                    "id": "entity-1", "name": "Entity",
                    "observations": [{"text": "observed"}],
                }],
                "chunks": [{
                    "id": "chunk-1", "text": "source chunk",
                    "chunk_index": 0, "truncated": False,
                }],
                "source_facts": [{
                    "id": "source-1", "text": "source fact",
                    "type": "experience",
                }],
            },
        )
        self.assert_operation(
            "POST", "/v1/default/banks/engineering/memories/recall"
        )
        self.assertEqual(self.seen[-1][3], {
            "query": "q",
            "types": ["world", "experience", "observation"],
            "prefer_observations": True,
            "budget": "mid",
            "max_tokens": 10_000,
            "include": {
                "entities": {"max_tokens": 2_000},
                "chunks": {"max_tokens": 4_000},
                "source_facts": {
                    "max_tokens": 4_000,
                    "max_tokens_per_observation": 1_000,
                },
            },
        })
        self.assertEqual(
            self.adapter.mental_model_fetch({"model_id": "model1"}),
            {"models": [{
                "id": "model1", "name": "Model",
                "content": "model content", "tags": [],
            }]},
        )
        self.assert_operation(
            "GET",
            "/v1/default/banks/engineering/mental-models/model1?detail=content",
        )
        self.assertEqual(
            self.adapter.session_status({"session_id": "session-1"}),
            {"status": "ready"},
        )
        self.assert_operation(
            "GET",
            "/v1/default/banks/engineering/operations?status=processing&limit=1&offset=0",
        )
        self.assertEqual(
            self.adapter.operation_status({"operation_id": RUNTIME_OPERATION_ID}),
            {"status": "completed"},
        )
        self.assert_operation(
            "GET", f"/v1/default/banks/engineering/operations/{RUNTIME_OPERATION_ID}"
        )

    def test_runtime_session_status_rejects_a_misrouted_bank_response(self):
        path = (
            "/v1/default/banks/engineering/operations?"
            "status=pending&limit=1&offset=0"
        )
        self.responses[("GET", path)] = {
            "bank_id": "other", "operations": [], "total": 0,
        }

        with self.assertRaisesRegex(
            AdapterError, "runtime operation status response is invalid"
        ):
            self.adapter.session_status({"session_id": "session-1"})

    def test_runtime_deep_recall_uses_the_compiled_deep_policy(self):
        self.adapter.recall({"query": "deep question", "depth": "deep"})

        self.assertEqual(self.seen[-1][3], {
            "query": "deep question",
            "types": ["world", "experience", "observation"],
            "prefer_observations": True,
            "budget": "high",
            "max_tokens": 20_000,
            "include": {
                "entities": {"max_tokens": 4_000},
                "chunks": {"max_tokens": 8_000},
                "source_facts": {
                    "max_tokens": 8_000,
                    "max_tokens_per_observation": 2_000,
                },
            },
        })

    def test_runtime_recall_rejects_malformed_primary_results(self):
        path = ("POST", "/v1/default/banks/engineering/memories/recall")
        for results in (
            [None], [{}], [{"id": "memory-1"}],
            [{"id": 1, "text": "memory"}],
        ):
            with self.subTest(results=results):
                self.responses[path] = {"results": results}
                with self.assertRaisesRegex(
                    AdapterError, "runtime recall response is invalid"
                ):
                    self.adapter.recall({"query": "question"})
        for depth in ([], {}):
            with self.subTest(depth=depth), self.assertRaisesRegex(
                AdapterError, "runtime request schema is invalid"
            ):
                self.adapter.recall({"query": "question", "depth": depth})

    def test_runtime_reflect_rejects_malformed_or_oversized_provenance(self):
        path = ("POST", "/v1/default/banks/engineering/reflect")
        invalid = (
            {"text": "answer", "based_on": []},
            {
                "text": "answer",
                "based_on": {
                    "memories": [{"id": "m"}] * 129,
                    "mental_models": [], "directives": [],
                },
            },
            {
                "text": "answer",
                "based_on": {
                    "memories": [],
                    "mental_models": [{"id": "\nunsafe"}],
                    "directives": [],
                },
            },
        )
        for response in invalid:
            with self.subTest(response=response):
                self.responses[path] = response
                adapter = HttpAdapter(
                    inventory=inventory_for(self.server.server_port),
                    profile_id="core", token_resolver=lambda: "contract-token",
                )
                with self.assertRaisesRegex(
                    AdapterError, "runtime reflect response is invalid"
                ):
                    adapter.reflect({
                        "reflection": "note",
                    })

    def test_runtime_compatibility_probe_attests_the_bound_bank(self):
        calls = []
        adapter = HttpAdapter(
            inventory=inventory_for(self.server.server_port),
            profile_id="core", token_resolver=lambda: "contract-token",
        )

        def request(method, path, body=None, **kwargs):
            calls.append((method, path, body, kwargs))
            if "_authorization_token" in kwargs:
                raise AuthenticationError(
                    "endpoint authentication failed (HTTP 401)"
                )
            if path == "/version":
                return RUNTIME_VERSION_RESPONSE
            return {"bank_id": "engineering", "total": 0}

        adapter._request = request
        adapter.verify_runtime_compatibility()
        self.assertEqual(calls[1], (
            "GET",
            "/v1/default/banks/engineering/operations?"
            "status=pending&limit=1&offset=0",
            None,
            {},
        ))
        self.assertIsNone(calls[2][3]["_authorization_token"])
        self.assertNotEqual(
            calls[3][3]["_authorization_token"], "contract-token"
        )

        adapter._request = lambda _method, path, _body=None: (
            RUNTIME_VERSION_RESPONSE
            if path == "/version"
            else {"bank_id": "other", "total": 0}
        )
        with self.assertRaisesRegex(
            AdapterError, "runtime bank probe response is invalid"
        ):
            adapter.verify_runtime_compatibility()

        for error in (
            EndpointNotFoundError("endpoint request failed (HTTP 404)"),
            AuthenticationError("endpoint authentication failed"),
        ):
            with self.subTest(error=type(error).__name__):
                def rejected(_method, path, _body=None, selected=error):
                    if path == "/version":
                        return RUNTIME_VERSION_RESPONSE
                    raise selected

                adapter._request = rejected
                with self.assertRaises(type(error)):
                    adapter.verify_runtime_compatibility()

    def test_runtime_compatibility_rejects_an_unprotected_bank_endpoint(self):
        adapter = HttpAdapter(
            inventory=inventory_for(self.server.server_port),
            profile_id="core", token_resolver=lambda: "contract-token",
        )

        def unprotected(method, path, body=None, **_kwargs):
            if path == "/version":
                return RUNTIME_VERSION_RESPONSE
            return {"bank_id": "engineering", "total": 0}

        adapter._request = unprotected
        with self.assertRaisesRegex(
            AdapterError, "does not enforce authentication"
        ):
            adapter.verify_runtime_compatibility()

    def test_runtime_compatibility_probes_real_transport_without_credentials(self):
        adapter = HttpAdapter(
            inventory=inventory_for(self.server.server_port),
            profile_id="core", token_resolver=lambda: "contract-token",
        )

        with self.assertRaisesRegex(
            AdapterError, "does not enforce authentication"
        ):
            adapter.verify_runtime_compatibility()
        self.assertIsNone(self.seen[-1][2])

    def test_runtime_adapter_fails_closed_on_unsupported_hindsight_version(self):
        adapter = HttpAdapter(
            inventory=inventory_for(self.server.server_port),
            profile_id="core",
            token_resolver=lambda: "contract-token",
        )
        adapter._request = lambda *_args: {"api_version": "0.8.5"}
        with self.assertRaisesRegex(
            AdapterError, "version, worker, or privacy features are unsupported"
        ):
            adapter.recall({"query": "q"})

    def test_runtime_adapter_fails_closed_when_async_worker_is_disabled(self):
        adapter = HttpAdapter(
            inventory=inventory_for(self.server.server_port),
            profile_id="core", token_resolver=lambda: "contract-token",
            runtime_bank_id="engineering", runtime_harness_id="codex",
        )
        adapter._request = lambda *_args: {
            "api_version": SUPPORTED_RUNTIME_VERSION,
            "features": {
                "worker": False, "audit_log": False, "llm_trace": False,
            },
        }

        with self.assertRaisesRegex(
            AdapterError, "version, worker, or privacy features are unsupported"
        ):
            adapter.verify_runtime_compatibility()

    def test_runtime_adapter_fails_closed_when_native_content_logging_is_enabled(self):
        adapter = HttpAdapter(
            inventory=inventory_for(self.server.server_port),
            profile_id="core", token_resolver=lambda: "contract-token",
            runtime_bank_id="engineering", runtime_harness_id="codex",
        )
        for feature in ("audit_log", "llm_trace"):
            with self.subTest(feature=feature):
                adapter._request = lambda *_args, selected=feature: {
                    "api_version": SUPPORTED_RUNTIME_VERSION,
                    "features": {
                        "worker": True,
                        "audit_log": selected == "audit_log",
                        "llm_trace": selected == "llm_trace",
                    },
                }
                with self.assertRaisesRegex(
                    AdapterError,
                    "version, worker, or privacy features are unsupported",
                ):
                    adapter.verify_runtime_compatibility()

    def test_runtime_adapter_requires_explicit_native_content_logging_flags(self):
        adapter = HttpAdapter(
            inventory=inventory_for(self.server.server_port),
            profile_id="core", token_resolver=lambda: "contract-token",
            runtime_bank_id="engineering", runtime_harness_id="codex",
        )
        adapter._request = lambda *_args: {
            "api_version": SUPPORTED_RUNTIME_VERSION,
            "features": {"worker": True},
        }
        with self.assertRaisesRegex(
            AdapterError,
            "version, worker, or privacy features are unsupported",
        ):
            adapter.verify_runtime_compatibility()

    def test_runtime_adapter_revalidates_version_before_each_operation(self):
        adapter = HttpAdapter(
            inventory=inventory_for(self.server.server_port),
            profile_id="core", token_resolver=lambda: "contract-token",
            runtime_bank_id="engineering", runtime_harness_id="codex",
        )
        calls = []

        def request(method, path, body=None):
            calls.append((method, path, body))
            if path == "/version":
                version = SUPPORTED_RUNTIME_VERSION if sum(
                    call[1] == "/version" for call in calls
                ) == 1 else "0.8.5"
                return {
                    "api_version": version,
                    "features": {
                        "worker": True,
                        "audit_log": False,
                        "llm_trace": False,
                    },
                }
            return {"results": []}

        adapter._request = request
        self.assertEqual(adapter.recall({"query": "first"}), {
            "memories": [], "entities": [], "chunks": [],
            "source_facts": [],
        })
        with self.assertRaisesRegex(
            AdapterError, "version, worker, or privacy features are unsupported"
        ):
            adapter.recall({"query": "second"})

    def test_operation_status_does_not_treat_version_404_as_not_found(self):
        adapter = HttpAdapter(
            inventory=inventory_for(self.server.server_port),
            profile_id="core", token_resolver=lambda: "contract-token",
            runtime_bank_id="engineering", runtime_harness_id="codex",
        )
        adapter._request = lambda *_args: (_ for _ in ()).throw(
            EndpointNotFoundError("endpoint request failed (HTTP 404)")
        )

        with self.assertRaisesRegex(
            AdapterError, r"endpoint request failed \(HTTP 404\)"
        ):
            adapter.operation_status({"operation_id": RUNTIME_OPERATION_ID})

    def test_runtime_routes_use_the_inventory_tenant(self):
        adapter = HttpAdapter(
            inventory=inventory_for(self.server.server_port, tenant="team"),
            profile_id="core", token_resolver=lambda: "contract-token",
            runtime_bank_id="engineering", runtime_harness_id="codex",
        )
        paths = []

        def request(_method, path, _body=None):
            paths.append(path)
            return (
                RUNTIME_VERSION_RESPONSE
                if path == "/version"
                else {"results": []}
            )

        adapter._request = request
        adapter.recall({"query": "tenant-bound"})
        self.assertEqual(
            paths[-1], "/v1/team/banks/engineering/memories/recall"
        )

    def test_runtime_responses_attest_bound_bank_and_operation(self):
        adapter = HttpAdapter(
            inventory=inventory_for(self.server.server_port),
            profile_id="core", token_resolver=lambda: "contract-token",
            runtime_bank_id="engineering", runtime_harness_id="codex",
        )
        adapter._request = lambda _method, path, _body=None: (
            RUNTIME_VERSION_RESPONSE
            if path == "/version"
            else {
                "id": "model1", "bank_id": "other",
                "content": "cross-bank content",
            }
        )
        with self.assertRaisesRegex(AdapterError, "mental model response"):
            adapter.mental_model_fetch({"model_id": "model1"})

        adapter._request = lambda _method, path, _body=None: (
            RUNTIME_VERSION_RESPONSE
            if path == "/version"
            else {
                "operation_id": RUNTIME_OPERATION_ID_2,
                "status": "completed",
            }
        )
        with self.assertRaisesRegex(AdapterError, "operation status response"):
            adapter.operation_status({"operation_id": RUNTIME_OPERATION_ID})

    def test_runtime_retain_requires_exact_async_bank_scoped_response(self):
        request = {
            "session_id": "session-1", "document_id": "session",
            "epoch": 1, "checkpoint": 1,
            "content": "clean transcript", "idempotency_key": "a" * 64,
        }
        valid = {
            "success": True, "bank_id": "engineering", "items_count": 1,
            "async": True, "operation_id": RUNTIME_OPERATION_ID,
        }
        for changed in (
            {**valid, "bank_id": "other"},
            {**valid, "async": False},
            {**valid, "operation_id": "not-a-uuid"},
            {**valid, "operation_ids": [RUNTIME_OPERATION_ID]},
            {**valid, "usage": {}},
            {**valid, "unexpected": True},
        ):
            with self.subTest(changed=changed):
                adapter = HttpAdapter(
                    inventory=inventory_for(self.server.server_port),
                    profile_id="core", token_resolver=lambda: "contract-token",
                    runtime_bank_id="engineering", runtime_harness_id="codex",
                )
                adapter._request = lambda _method, path, _body=None: (
                    RUNTIME_VERSION_RESPONSE
                    if path == "/version"
                    else changed
                )
                with self.assertRaisesRegex(
                    AdapterError, "runtime retain response is invalid"
                ):
                    adapter.transcript_checkpoint(request)

    def test_failed_operation_evicts_cached_submission_for_safe_resubmit(self):
        adapter = HttpAdapter(
            inventory=inventory_for(self.server.server_port),
            profile_id="core", token_resolver=lambda: "contract-token",
            runtime_bank_id="engineering", runtime_harness_id="codex",
        )
        submissions = []

        def mutate(_method, _request):
            operation_id = (
                RUNTIME_OPERATION_ID
                if not submissions
                else RUNTIME_OPERATION_ID_2
            )
            submissions.append(operation_id)
            return {"operation_id": operation_id}

        adapter._runtime_mutation = mutate
        adapter._request = lambda _method, path, _body=None: (
            RUNTIME_VERSION_RESPONSE
            if path == "/version"
            else {
                "operation_id": RUNTIME_OPERATION_ID,
                "status": "failed",
            }
        )
        request = {
            "session_id": "session-1", "document_id": "session",
            "epoch": 1, "checkpoint": 1,
            "content": "clean transcript", "idempotency_key": "a" * 64,
        }
        self.assertEqual(
            adapter.transcript_checkpoint(request),
            {"operation_id": RUNTIME_OPERATION_ID},
        )
        self.assertEqual(
            adapter.operation_status({"operation_id": RUNTIME_OPERATION_ID}),
            {"status": "failed"},
        )
        self.assertEqual(
            adapter.transcript_checkpoint(request),
            {"operation_id": RUNTIME_OPERATION_ID_2},
        )

    def test_missing_operation_is_terminal_and_evicts_cached_submission(self):
        adapter = HttpAdapter(
            inventory=inventory_for(self.server.server_port),
            profile_id="core", token_resolver=lambda: "contract-token",
            runtime_bank_id="engineering", runtime_harness_id="codex",
        )
        adapter._runtime_mutation = lambda *_args: {
            "operation_id": RUNTIME_OPERATION_ID
        }
        request = {
            "session_id": "session-1", "document_id": "session",
            "epoch": 1, "checkpoint": 1,
            "content": "clean transcript", "idempotency_key": "a" * 64,
        }
        adapter.transcript_checkpoint(request)
        adapter._request = lambda _method, path, _body=None: (
            RUNTIME_VERSION_RESPONSE
            if path == "/version"
            else (_ for _ in ()).throw(
                EndpointNotFoundError("endpoint request failed (HTTP 404)")
            )
        )
        self.assertEqual(
            adapter.operation_status({"operation_id": RUNTIME_OPERATION_ID}),
            {"status": "not_found"},
        )
        self.assertNotIn("a" * 64, adapter._runtime_results)

    def test_runtime_mental_model_fetch_rejects_mismatched_or_malformed_models(self):
        for response in (
            {"id": "other", "content": "model content"},
            {"id": "model1", "content": {"unsafe": True}},
            {"id": "model1"},
        ):
            with self.subTest(response=response):
                adapter = HttpAdapter(
                    inventory=inventory_for(self.server.server_port),
                    profile_id="core",
                    token_resolver=lambda: "contract-token",
                )
                replies = iter((RUNTIME_VERSION_RESPONSE, response))
                adapter._request = lambda *_args: next(replies)
                with self.assertRaisesRegex(
                    AdapterError, "mental model response is invalid"
                ):
                    adapter.mental_model_fetch({"model_id": "model1"})

    def test_runtime_memory_writes_are_idempotent(self):
        checkpoint = {
            "session_id": "session-1", "document_id": "d",
            "epoch": 1, "checkpoint": 2,
            "content": "clean transcript",
            "idempotency_key": "a" * 64,
        }
        retain = {
            "session_id": "session-1", "document_id": "outcome-d",
            "epoch": 1, "checkpoint": 2,
            "outcome": "done", "idempotency_key": "c" * 64,
        }
        reflection = {"reflection": "note"}
        self.assertEqual(
            self.adapter.transcript_checkpoint(checkpoint),
            {"operation_id": RUNTIME_OPERATION_ID},
        )
        self.assert_operation(
            "POST", "/v1/default/banks/engineering/memories"
        )
        self.assertEqual(self.seen[-1][3], {
            "items": [{
                "content": "clean transcript",
                "document_id": "hindsight-transcript-" + digest({
                    "harness_id": "codex",
                    "kind": "transcript",
                    "logical_document_id": "d",
                    "epoch": 1,
                    "session_id": "session-1",
                })[:48],
                "update_mode": "replace",
                "tags": [
                    "agent:codex", "source:codex-hook", "scope:active",
                ],
                "observation_scopes": [["scope:active"]],
                "metadata": {
                    "kind": "transcript", "session_id": "session-1",
                    "epoch": "1", "checkpoint": "2",
                },
            }],
            "async": True,
        })
        transcript_document_id = self.seen[-1][3]["items"][0]["document_id"]
        self.assertEqual(
            self.adapter.retain_outcome(retain),
            {"operation_id": RUNTIME_OPERATION_ID},
        )
        self.assert_operation(
            "POST", "/v1/default/banks/engineering/memories"
        )
        outcome_document_id = self.seen[-1][3]["items"][0]["document_id"]
        self.assertTrue(outcome_document_id.startswith("hindsight-outcome-"))
        self.assertNotEqual(outcome_document_id, transcript_document_id)
        self.assertEqual(
            self.adapter.reflect(reflection),
            {
                "reflection": "reflected answer",
                "based_on": {
                    "memory_ids": ["memory-1"],
                    "mental_model_ids": ["model-1"],
                    "directive_ids": ["directive-1"],
                    "source_resolution_required": True,
                    "unresolved_memory_items": 0,
                },
            },
        )
        self.assert_operation(
            "POST", "/v1/default/banks/engineering/reflect"
        )
        self.assertEqual(self.seen[-1][3], {
            "query": "note", "budget": "mid", "max_tokens": 10_000,
            "include": {"facts": {}},
        })
        before = len(self.seen)
        self.assertEqual(
            self.adapter.retain_outcome(retain),
            {"operation_id": RUNTIME_OPERATION_ID},
        )
        self.assertEqual(len(self.seen), before)
        with self.assertRaisesRegex(AdapterError, "digest drift"):
            self.adapter.retain_outcome({**retain, "outcome": "changed"})

    def test_runtime_retain_requires_an_exact_integer_item_count(self):
        self.responses[(
            "POST", "/v1/default/banks/engineering/memories"
        )]["items_count"] = True
        with self.assertRaisesRegex(
            AdapterError, "runtime retain response is invalid"
        ):
            self.adapter.transcript_checkpoint({
                "session_id": "session-1", "document_id": "d",
                "epoch": 1, "checkpoint": 1,
                "content": "clean transcript",
                "idempotency_key": "e" * 64,
            })

    def test_runtime_retain_document_identity_is_session_scoped(self):
        document_ids = []
        for session_id, key in (
            ("session-one", "1" * 64),
            ("session-two", "2" * 64),
        ):
            self.adapter.transcript_checkpoint({
                "session_id": session_id,
                "document_id": "shared-document",
                "epoch": 1,
                "checkpoint": 1,
                "content": f"content for {session_id}",
                "idempotency_key": key,
            })
            document_ids.append(
                self.seen[-1][3]["items"][0]["document_id"]
            )

        self.assertNotEqual(document_ids[0], document_ids[1])

    def test_snapshot(self):
        snapshot = self.adapter.snapshot()
        self.assertEqual(snapshot["endpoint"], self.endpoint.to_dict())
        self.assertEqual(snapshot["operations"], self.operations)
        self.assertEqual(snapshot["compatibility"], [])
        self.assertEqual(snapshot["state"]["tags"], ["repo:dotfiles"])
        self.assertEqual(snapshot["state"]["profile_artifact_digest"], "a" * 64)
        self.assertEqual(
            snapshot["state"]["banks"],
            [{
                "id": "engineering",
                "profile_id": "core",
                "artifact_digest": "b" * 64,
                "enable_auto_consolidation": True,
                "memory_defense": "sensitive_data",
                "unknown_fields_digest": digest({
                    "private_bank_value": "private-bank-value",
                }),
                "models": [{
                    "id": "summary", "revision": "v1",
                    "artifact_digest": "c" * 64,
                    "unknown_fields_digest": digest({
                        "prompt": "private-bank-model",
                    }),
                }],
                "directives": [{
                    "id": "privacy", "artifact_digest": "d" * 64,
                    "unknown_fields_digest": digest({
                        "mission": "private-bank-directive",
                    }),
                }],
            }],
        )
        self.assertEqual(
            snapshot["state"]["unknown_fields_digest"],
            digest({
                "api_token": "private-top-level-token",
                "nested": {"client_secret": "private-nested-secret"},
            }),
        )
        self.assertEqual(
            snapshot["state"]["models"],
            [{"model_id": "m0", "content_digest": digest({"id": "m0", "prompt": "private-model"})}],
        )
        self.assertEqual(
            snapshot["state"]["directives"],
            [
                {
                    "directive_id": "r0",
                    "content_digest": digest({"id": "r0", "mission": "private-directive"}),
                }
            ],
        )
        self.assertNotIn("private", json.dumps(snapshot))

    def test_snapshot_rejects_unsafe_tags_instead_of_omitting_them(self):
        original_request = self.adapter._request

        def request(method, path, payload=None, **kwargs):
            if method == "GET" and path == "/v1/state":
                return {"tags": {"tags": ["repo:dotfiles", "token=private"]}}
            return original_request(method, path, payload, **kwargs)

        with patch.object(self.adapter, "_request", side_effect=request):
            with self.assertRaisesRegex(AdapterError, "tags response is invalid"):
                self.adapter.snapshot()

    def test_read_migration_inventory_composes_documented_get_surfaces(self):
        before = len(self.seen)
        result = self.adapter.read_migration_inventory(
            BankRef("core", "engineering"),
            BankRef("core", "historical-candidate"),
        )
        self.assertEqual(result["schema_version"], 1)
        self.assertTrue(result["operations"]["idle"])
        self.assertEqual(result["versions"]["hindsight"], "0.8.4")
        self.assertEqual(
            result["versions"]["features"], {"observations": True}
        )
        self.assertEqual(
            set(result["versions"]),
            {
                "adapter", "hindsight", "hindsight_digest", "features",
                "features_digest", "response_digest",
            },
        )
        self.assertEqual(result["provider_identity"]["profile_id"], "core")

        self.assertEqual(result["banks"]["source"]["bank_ref"]["bank_id"], "engineering")
        self.assertEqual(result["banks"]["candidate"]["bank_ref"]["bank_id"], "historical-candidate")
        self.assertEqual(result["banks"]["source"]["config"]["config"]["recall_max_tokens"], 4096)
        self.assertNotIn("api_key", result["banks"]["source"]["config"]["config"])
        self.assertNotIn("api_key", result["banks"]["source"]["config"]["config"]["provider"])
        self.assertEqual(result["banks"]["source"]["config"]["config"]["provider"]["model"], "safe-model")
        hooks = {item["hook_id"]: item for item in result["hooks"]}
        self.assertEqual(hooks["hook-1"]["bank_role"], "source")
        self.assertEqual(hooks["hook-2"]["bank_role"], "candidate")
        self.assertTrue(
            all(
                set(item)
                == {
                    "bank_role", "hook_id", "registration_digest",
                    "registration",
                }
                for item in hooks.values()
            )
        )
        self.assertTrue(all(
            set(item["registration"])
            == {"target_digest", "activation_digest", "config_digest"}
            for item in hooks.values()
        ))
        self.assertNotIn("private-target-token", json.dumps(result))
        self.assertNotIn("private-config", json.dumps(result))
        self.assertIn("config.provider.api_key", result["banks"]["source"]["config"]["redacted_keys"])
        self.assertEqual(result["banks"]["source"]["tags"], ["repo:dotfiles"])
        self.assertEqual(
            result["banks"]["source"]["scopes"], ["repo:dotfiles"]
        )
        self.assertEqual(
            set(result["banks"]["source"]["stats"]),
            {"bank_id", "total_documents", "response_digest"},
        )
        document = result["banks"]["source"]["documents"][0]
        self.assertEqual(
            set(document["document_metadata"]), {"content_digest"}
        )
        self.assertEqual(set(document["retain_params"]), {"content_digest"})
        self.assertEqual(
            set(result["banks"]["source"]["models"][0]),
            {"model_id", "content_digest"},
        )
        self.assertEqual(
            set(result["banks"]["source"]["directives"][0]),
            {"directive_id", "content_digest"},
        )
        self.assertNotIn("private", json.dumps(result))
        calls = self.seen[before:]
        self.assertTrue(calls)
        self.assertTrue(all(method == "GET" for method, _path, _auth, _body in calls))
        self.assertFalse(any(path.startswith("/v1/migrations/") for _method, path, _auth, _body in calls))
        self.assertTrue(all(auth == "Bearer contract-token" for _method, _path, auth, _body in calls))

    def test_migration_inventory_rejects_duplicate_operation_ids_across_statuses(self):
        base = "/v1/default/banks/engineering"
        operation = {
            "id": "operation-1",
            "updated_at": "2026-07-13T12:00:00Z",
        }
        for status in ("pending", "processing"):
            self.responses[(
                "GET", f"{base}/operations?status={status}&limit=100&offset=0"
            )] = {
                "bank_id": "engineering",
                "operations": [operation],
                "total": 1,
                "limit": 100,
                "offset": 0,
            }
        with self.assertRaisesRegex(AdapterError, "operation response is invalid"):
            self.adapter.read_migration_inventory(
                BankRef("core", "engineering"),
                BankRef("core", "historical-candidate"),
            )

    def test_migration_inventory_rejects_duplicate_webhook_ids(self):
        path = "/v1/default/banks/engineering/webhooks"
        item = {
            "id": "hook-duplicate",
            "target": {"url": "https://hooks.example"},
            "activation": {"enabled": True},
            "config": {"timeout": 5},
        }
        self.responses[("GET", path)] = {"items": [item, dict(item)]}
        with self.assertRaisesRegex(AdapterError, "webhook response is invalid"):
            self.adapter.read_migration_inventory(
                BankRef("core", "engineering"),
                BankRef("core", "historical-candidate"),
            )

    def test_migration_inventory_closed_surfaces_fail_on_malformed_safe_fields(self):
        version_cases = (
            {},
            {"api_version": "0.8.4", "features": {"observations": "yes"}},
        )
        for value in version_cases:
            with self.subTest(version=value), self.assertRaises(AdapterError):
                self.adapter._migration_versions(value)

        stats_cases = (
            {"bank_id": "other", "total_documents": 1},
            {"bank_id": "engineering"},
            {"bank_id": "engineering", "total_documents": -1},
        )
        for value in stats_cases:
            with self.subTest(stats=value), self.assertRaises(AdapterError):
                self.adapter._migration_stats(
                    value, expected_bank_id="engineering"
                )

        scope_cases = (
            {"scopes": ["scope:unknown"]},
            {"scopes": [{"scope": "repo:dotfiles", "count": -1}]},
            {"scopes": [{"description": "private"}]},
        )
        for value in scope_cases:
            with self.subTest(scopes=value), self.assertRaises(AdapterError):
                self.adapter._migration_scopes(value)

        document = {
            "id": "document-1",
            "updated_at": "2026-07-13T12:00:00Z",
            "content_hash": "a" * 64,
        }
        for changes in (
            {"id": "x" * 129},
            {"updated_at": "2026-07-13T12:00:00"},
            {"updated_at": 7},
            {"created_at": "2026-07-13T11:00:00"},
            {"text_length": True},
            {"memory_unit_count": -1},
        ):
            with self.subTest(document=changes), self.assertRaises(AdapterError):
                self.adapter._migration_document({**document, **changes})

    def test_migration_inventory_operation_shape_is_closed(self):
        value = self.adapter.read_migration_inventory(
            BankRef("core", "engineering"),
            BankRef("core", "historical-candidate"),
        )
        value["operations"]["idle"] = False
        value["operations"]["active"] = [{
            "bank_role": "source",
            "operation_id": "operation-1",
            "status": "pending",
            "updated_at": "2026-07-13T12:00:00Z",
            "task_type": {"private": "payload"},
        }]
        with self.assertRaisesRegex(AdapterError, "operation inventory"):
            self.adapter._validate_migration_inventory(value)

    def test_migration_inventory_digests_unsafe_versions_and_normalizes_scopes(self):
        private_version = "private version content"
        versions = self.adapter._migration_versions(
            {"api_version": private_version, "features": {}}
        )
        self.assertIsNone(versions["hindsight"])
        self.assertEqual(versions["hindsight_digest"], digest(private_version))
        self.assertNotIn(private_version, json.dumps(versions))

        first_scopes, first_digest = self.adapter._migration_scopes(
            {"scopes": [{
                "scope": "repo:dotfiles",
                "count": 1,
                "description": "private first scope content",
            }]}
        )
        second_scopes, second_digest = self.adapter._migration_scopes(
            {"scopes": [{
                "scope": "repo:dotfiles",
                "count": 2,
                "description": "private second scope content",
            }]}
        )
        self.assertEqual(first_scopes, second_scopes)
        self.assertEqual(first_digest, second_digest)
        self.assertEqual(first_digest, digest({"scopes": ["repo:dotfiles"]}))

    def test_migration_generation_is_read_from_the_server(self):
        self.assertEqual(self.adapter.read_migration_generation(), "commit-42")
        self.assert_operation("GET", "/v1/migration/generation")

    def test_migration_bank_paths_use_the_selected_endpoint_tenant(self):
        selected = inventory_for(
            self.server.server_port,
            tenant="tenant with/slash",
        )
        adapter = HttpAdapter(
            inventory=selected,
            profile_id="core",
            token_resolver=lambda: "contract-token",
        )
        self.assertEqual(
            adapter._bank_path(BankRef("core", "bank with/slash")),
            "/v1/tenant%20with%2Fslash/banks/bank%20with%2Fslash",
        )

    def test_migration_bank_config_is_bound_to_requested_bank(self):
        with patch.object(
            self.adapter,
            "_request",
            return_value={"bank_id": "other", "config": {}, "overrides": {}},
        ), self.assertRaisesRegex(AdapterError, "identity drifted"):
            self.adapter._read_migration_bank(
                "source",
                BankRef("core", "engineering"),
                deadline=time.monotonic() + 5,
            )

    def test_destructive_migration_actions_have_no_direct_http_route(self):
        before = len(self.seen)
        for kind in ("import_bank", "migrate_bank", "replace_canonical_bank"):
            with self.subTest(kind=kind), self.assertRaisesRegex(
                AdapterError, "unsupported apply action"
            ):
                self.adapter.apply_action(Action("migration", kind, {}))
        self.assertEqual(len(self.seen), before)

    def test_runtime_result_cache_is_ttl_and_lru_bounded(self):
        clock = [10.0]
        adapter = HttpAdapter(
            inventory=inventory_for(self.server.server_port),
            profile_id="core",
            token_resolver=lambda: "contract-token",
            runtime_result_ttl_seconds=5,
            max_runtime_results=2,
            runtime_clock=lambda: clock[0],
        )
        checkpoint = {
            "session_id": "session-1", "document_id": "d",
            "epoch": 1, "checkpoint": 1,
            "content": "complete cleaned transcript",
            "idempotency_key": "a" * 64,
        }
        retain = {
            "session_id": "session-1", "document_id": "d",
            "epoch": 1, "checkpoint": 1,
            "outcome": "done", "idempotency_key": "b" * 64,
        }
        reflection = {"reflection": "note"}
        first = adapter.transcript_checkpoint(checkpoint)
        first["operation_id"] = "changed"
        self.assertEqual(
            adapter.transcript_checkpoint(checkpoint),
            {"operation_id": RUNTIME_OPERATION_ID},
        )
        adapter.retain_outcome(retain)
        adapter.transcript_checkpoint(checkpoint)
        adapter.reflect(reflection)
        self.assertEqual(tuple(adapter._runtime_results), ("b" * 64, "a" * 64))
        before = len(self.seen)
        adapter.retain_outcome(retain)
        self.assertEqual(len(self.seen), before)
        clock[0] += 6
        before = len(self.seen)
        adapter.retain_outcome(retain)
        self.assertEqual(len(self.seen), before + 2)

    def test_runtime_result_cache_enforces_total_byte_budget(self):
        adapter = HttpAdapter(
            inventory=inventory_for(self.server.server_port),
            profile_id="core",
            token_resolver=lambda: "contract-token",
            max_runtime_results=10,
            max_runtime_result_bytes=48,
        )
        adapter._runtime_mutation = lambda _method, request: {
            "value": request["idempotency_key"][:24]
        }
        for key in ("a" * 64, "b" * 64):
            adapter.transcript_checkpoint({
                "session_id": "session-1", "document_id": key[0],
                "epoch": 1, "checkpoint": 1,
                "content": "complete cleaned transcript",
                "idempotency_key": key,
            })
        self.assertLessEqual(adapter._runtime_result_bytes, 48)
        self.assertEqual(len(adapter._runtime_results), 1)

        adapter._runtime_mutation = lambda *_args: {"value": "x" * 100}
        adapter.transcript_checkpoint({
            "session_id": "session-1", "document_id": "oversized",
            "epoch": 1, "checkpoint": 1,
            "content": "complete cleaned transcript",
            "idempotency_key": "c" * 64,
        })
        self.assertNotIn("c" * 64, adapter._runtime_results)
        self.assertLessEqual(adapter._runtime_result_bytes, 48)

    def test_runtime_result_cache_coalesces_only_matching_keys_without_holding_global_lock(self):
        adapter = HttpAdapter(
            inventory=inventory_for(self.server.server_port),
            profile_id="core",
            token_resolver=lambda: "contract-token",
        )
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def request(method, payload):
            calls.append((method, payload["idempotency_key"]))
            if payload["idempotency_key"] == "d" * 64:
                entered.set()
                if not release.wait(CONCURRENCY_TIMEOUT_SECONDS):
                    raise TimeoutError("test release was not signaled")
            return {"applied": True}

        adapter._runtime_mutation = request
        first_request = {
            "session_id": "session-1", "document_id": "d",
            "epoch": 1, "checkpoint": 1,
            "content": "complete cleaned transcript",
            "idempotency_key": "d" * 64,
        }
        other_request = {
            "session_id": "session-1", "document_id": "other",
            "epoch": 1, "checkpoint": 1,
            "content": "complete cleaned transcript",
            "idempotency_key": "e" * 64,
        }
        results = []
        first = threading.Thread(
            target=lambda: results.append(
                adapter.transcript_checkpoint(first_request)
            )
        )
        duplicate = threading.Thread(
            target=lambda: results.append(
                adapter.transcript_checkpoint(first_request)
            )
        )
        first.start()
        self.assertTrue(entered.wait(CONCURRENCY_TIMEOUT_SECONDS))
        duplicate.start()
        self.assertEqual(adapter.transcript_checkpoint(other_request), {"applied": True})
        self.assertEqual(len(calls), 2)
        release.set()
        first.join(CONCURRENCY_TIMEOUT_SECONDS)
        duplicate.join(CONCURRENCY_TIMEOUT_SECONDS)
        self.assertFalse(first.is_alive())
        self.assertFalse(duplicate.is_alive())
        self.assertEqual(len(results), 2)
        self.assertEqual(calls.count(("transcript_checkpoint", "d" * 64)), 1)

    def test_runtime_result_inflight_preserves_digest_drift_and_leader_error(self):
        adapter = HttpAdapter(
            inventory=inventory_for(self.server.server_port),
            profile_id="core",
            token_resolver=lambda: "contract-token",
        )
        entered = threading.Event()
        follower_attached = threading.Event()
        release = threading.Event()
        failure = AdapterError("leader failed")
        requests = []

        def request(method, _payload):
            requests.append(method)
            entered.set()
            if not release.wait(CONCURRENCY_TIMEOUT_SECONDS):
                raise TimeoutError("test release was not signaled")
            raise failure

        adapter._runtime_mutation = request
        base = {
            "session_id": "session-1", "document_id": "d",
            "epoch": 1, "checkpoint": 1,
            "content": "complete cleaned transcript",
            "idempotency_key": "f" * 64,
        }
        errors = []
        leader = threading.Thread(
            target=lambda: self._capture_error(
                errors, lambda: adapter.transcript_checkpoint(base)
            )
        )
        follower = threading.Thread(
            target=lambda: self._capture_error(
                errors, lambda: adapter.transcript_checkpoint(base)
            )
        )
        leader.start()
        self.assertTrue(entered.wait(CONCURRENCY_TIMEOUT_SECONDS))
        with adapter._runtime_results_lock:
            in_flight = adapter._runtime_inflight[base["idempotency_key"]]
            original_event = in_flight.event

            class AttachedEvent:
                def wait(self, timeout=None):
                    follower_attached.set()
                    return original_event.wait(timeout)

                def set(self):
                    return original_event.set()

            in_flight.event = AttachedEvent()
        follower.start()
        self.assertTrue(
            follower_attached.wait(CONCURRENCY_TIMEOUT_SECONDS)
        )
        with self.assertRaisesRegex(AdapterError, "digest drift"):
            adapter.transcript_checkpoint({**base, "checkpoint": 2})
        release.set()
        leader.join(CONCURRENCY_TIMEOUT_SECONDS)
        follower.join(CONCURRENCY_TIMEOUT_SECONDS)
        self.assertFalse(leader.is_alive())
        self.assertFalse(follower.is_alive())
        self.assertEqual(errors, [failure, failure])

    def test_runtime_result_follower_wait_uses_its_absolute_deadline(self):
        adapter = HttpAdapter(
            inventory=inventory_for(self.server.server_port),
            profile_id="core",
            token_resolver=lambda: "contract-token",
            timeout=0.1,
        )
        entered = threading.Event()
        release = threading.Event()
        requests = []
        self.addCleanup(release.set)

        def request(method, _payload):
            requests.append(method)
            entered.set()
            release.wait(CONCURRENCY_TIMEOUT_SECONDS)
            return {"applied": True}

        adapter._runtime_mutation = request
        value = {
            "session_id": "session-1", "document_id": "bounded",
            "epoch": 1, "checkpoint": 1,
            "content": "complete cleaned transcript",
            "idempotency_key": "9" * 64,
        }
        leader = threading.Thread(
            target=lambda: adapter.transcript_checkpoint(value), daemon=True
        )
        leader.start()
        self.assertTrue(entered.wait(CONCURRENCY_TIMEOUT_SECONDS))
        started = time.monotonic()
        with self.assertRaisesRegex(AdapterError, "timed out"):
            adapter.transcript_checkpoint(value)
        self.assertLess(time.monotonic() - started, 0.5)
        release.set()
        leader.join(CONCURRENCY_TIMEOUT_SECONDS)
        self.assertFalse(leader.is_alive())
        self.assertEqual(requests, ["transcript_checkpoint"])

    @staticmethod
    def _capture_error(errors, operation):
        try:
            operation()
        except Exception as error:
            errors.append(error)

    def test_ipv6_endpoint_urls_use_bracketed_authority(self):
        adapter = HttpAdapter(
            inventory=inventory_for(7979, host="::1"),
            profile_id="core", token_resolver=lambda: "contract-token",
        )

        class Response:
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, _limit): return b"{}"

        with patch.object(
            adapter._opener, "open", return_value=Response()
        ) as opened:
            adapter._request("GET", "/health")
        self.assertEqual(
            opened.call_args.args[0].full_url,
            "http://[::1]:7979/health",
        )

    def assert_operation(self, method, path):
        self.assertEqual(self.seen[-1][:3], (method, path, "Bearer contract-token"))

    def assert_rollback_contract(self, bundle):
        self.assertEqual([(item[0], item[1]) for item in self.seen[-3:]], [
            ("POST", "/v1/rollbacks"),
            ("POST", f"/v1/rollbacks/{bundle.rollback_id}/verify"),
            ("POST", f"/v1/rollbacks/{bundle.rollback_id}/restore"),
        ])
        self.assertEqual(self.seen[-1][3], bundle.to_dict())


class HttpAdapterSecurityTest(unittest.TestCase):
    @staticmethod
    def bind_verified(adapter, rollback, state):
        adapter.snapshot = lambda: {
            "endpoint": adapter.endpoint.to_dict(),
            "state": state,
            "operations": {"idle": True, "active": []},
        }
        with adapter._apply_binding_lock:
            adapter._verified_rollbacks[rollback.rollback_id] = rollback
        adapter.bind_apply_plan(rollback)

    def test_endpoint_host_rejects_userinfo_and_authority_delimiters(self):
        for host in (
            "user@example.com",
            "example.com/path",
            "example.com?query",
            "example.com#fragment",
            "[::1]",
        ):
            with self.subTest(host=host), self.assertRaisesRegex(
                AdapterError, "bare DNS name or IP literal"
            ):
                HttpAdapter(
                    inventory=inventory_for(
                        443,
                        scheme="https",
                        host=host,
                        approved_tls=True,
                    ),
                    profile_id="core",
                    token_resolver=lambda: "contract-token",
                )

    def test_migration_inventory_uses_one_aggregate_deadline(self):
        adapter = HttpAdapter(
            inventory=inventory_for(7979),
            profile_id="core",
            token_resolver=lambda: "token",
        )
        observed = []

        def request(_method, _path, _payload=None, *, deadline=None):
            observed.append(deadline)
            return {}

        def read_bank(_role, bank, *, deadline):
            observed.append(deadline)
            return ({"bank_ref": bank.to_dict()}, [], [], [])

        with (
            patch.object(adapter, "_request", side_effect=request),
            patch.object(adapter, "_migration_versions", return_value={}),
            patch.object(adapter, "_read_migration_bank", side_effect=read_bank),
            patch.object(adapter, "_declared_provider_identity", return_value={}),
            patch.object(adapter, "_validate_migration_inventory"),
        ):
            adapter.read_migration_inventory(
                BankRef("core", "source"),
                BankRef("core", "candidate"),
            )
        self.assertEqual(len(observed), 3)
        self.assertIsNotNone(observed[0])
        self.assertEqual(observed, [observed[0]] * len(observed))

    def test_bearer_token_rejects_header_injection_and_header_value_errors(self):
        injected = HttpAdapter(
            inventory=inventory_for(7979), profile_id="core",
            token_resolver=lambda: "secret\r\nX-Injected: yes",
        )
        with (
            patch.object(injected._opener, "open") as opened,
            self.assertRaises(AuthenticationError),
        ):
            injected._request("GET", "/health")
        opened.assert_not_called()

        adapter = HttpAdapter(
            inventory=inventory_for(7979), profile_id="core",
            token_resolver=lambda: "private-token-value",
        )
        with (
            patch.object(
                adapter._opener,
                "open",
                side_effect=ValueError("invalid Authorization header"),
            ),
            self.assertRaises(AuthenticationError) as failure,
        ):
            adapter._request("GET", "/health")
        self.assertNotIn("private-token-value", str(failure.exception))

    def test_secret_redaction_is_content_independent_and_camel_case_aware(self):
        for key in ("apiKey", "accessToken", "clientSecret"):
            with self.subTest(key=key):
                self.assertTrue(HttpAdapter._secret_config_key(key))
                self.assertEqual(
                    HttpAdapter._redact_snapshot_value("first", key),
                    {"redacted": True},
                )
                self.assertEqual(
                    HttpAdapter._redact_snapshot_value("second", key),
                    {"redacted": True},
                )

    def test_snapshot_redaction_never_emits_unknown_scalar_content(self):
        redacted = HttpAdapter._redact_snapshot_value({
            "unknown": "private-arbitrary-content",
            "nested": ["private-list-content"],
            "api_token": "private-token",
        })
        encoded = json.dumps(redacted)
        self.assertNotIn("private", encoded)
        self.assertEqual(
            redacted,
            {
                "unknown_fields_digest": digest({
                    "api_token": "private-token",
                    "nested": ["private-list-content"],
                    "unknown": "private-arbitrary-content",
                })
            },
        )

    def test_snapshot_redaction_never_emits_unknown_mapping_keys(self):
        value = {
            "private-arbitrary-key": "value",
            "api_token=private-key-material": "private-value",
        }

        redacted = HttpAdapter._redact_snapshot_value(value)

        self.assertEqual(
            redacted, {"unknown_fields_digest": digest(value)}
        )
        self.assertNotIn("private", json.dumps(redacted))
    def test_safe_config_omits_fields_outside_closed_disclosure_schema(self):
        safe = HttpAdapter._safe_config({
            "bank_id": "engineering",
            "config": {"model": "safe-model", "private_note": "do-not-disclose"},
            "overrides": {},
        })
        self.assertEqual(safe["config"], {"model": "safe-model"})
        self.assertIn("config.private_note", safe["redacted_keys"])

    def test_safe_config_redacts_endpoint_credentials_and_query_secrets(self):
        safe = HttpAdapter._safe_config({
            "bank_id": "engineering",
            "config": {
                "base_url": "https://operator:secret@example.invalid/v1?token=x",
                "host": "operator@example.invalid",
                "provider": "safe-provider",
            },
            "overrides": {"base_url": "https://example.invalid/"},
        })
        self.assertEqual(safe["config"], {"provider": "safe-provider"})
        self.assertEqual(
            safe["overrides"], {"base_url": "https://example.invalid/"}
        )
        self.assertEqual(
            safe["redacted_keys"], ["config.base_url", "config.host"]
        )

    def test_unbounded_pagination_is_refused(self):
        adapter = object.__new__(HttpAdapter)
        adapter._request = lambda *_args: {
            "items": [{}] * HttpAdapter.PAGE_LIMIT,
            "offset": int(_args[1].rsplit("offset=", 1)[1]),
            "limit": HttpAdapter.PAGE_LIMIT,
        }
        with self.assertRaisesRegex(AdapterError, "item limit"):
            adapter._read_items("/items", total_required=False)

    def test_non_total_pagination_continues_after_a_short_nonempty_page(self):
        adapter = object.__new__(HttpAdapter)
        paths = []
        pages = {
            0: {"items": ["first"], "offset": 0, "limit": 3},
            1: {"items": ["second"], "offset": 1, "limit": 3},
            2: {"items": [], "offset": 2, "limit": 3},
        }

        def request(_method, path):
            paths.append(path)
            return pages[int(path.rsplit("offset=", 1)[1])]

        adapter._request = request
        self.assertEqual(
            adapter._read_items("/items", total_required=False, limit=3),
            ["first", "second"],
        )
        self.assertEqual(len(paths), 3)

    def test_pagination_enforces_page_and_cumulative_byte_budgets(self):
        adapter = object.__new__(HttpAdapter)
        adapter._request = lambda *_args: {
            "items": ["x" * 32],
            "offset": 0,
            "limit": 1,
        }
        with patch.object(
            HttpAdapter, "MAX_DISCOVERY_BYTES", 8
        ), self.assertRaisesRegex(AdapterError, "byte limit"):
            adapter._read_items("/items", total_required=False, limit=1)

        paths = []
        adapter._request = lambda _method, path: (
            paths.append(path)
            or {"items": ["x"], "offset": len(paths) - 1, "limit": 1}
        )
        with patch.object(
            HttpAdapter, "MAX_DISCOVERY_BYTES", len(canonical_bytes(["x"]))
        ), self.assertRaisesRegex(AdapterError, "byte limit"):
            adapter._read_items("/items", total_required=False, limit=1)
        self.assertEqual(len(paths), 1)

        paths = []
        adapter._request = lambda _method, path: (
            paths.append(path)
            or {"items": ["x"], "offset": len(paths) - 1, "limit": 1}
        )
        with patch.object(
            HttpAdapter, "MAX_DISCOVERY_PAGES", 1
        ), self.assertRaisesRegex(AdapterError, "page limit"):
            adapter._read_items("/items", total_required=False, limit=1)
        self.assertEqual(len(paths), 1)

    def test_ambient_http_proxy_is_ignored(self):
        direct_requests = []
        proxy_requests = []

        class DirectHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                direct_requests.append(self.path)
                body = b'{"mode":"direct"}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args): pass

        class ProxyHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                proxy_requests.append(self.path)
                body = b'{"mode":"proxied"}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args): pass

        direct = start_http_server(self, DirectHandler)
        proxy = start_http_server(self, ProxyHandler)
        proxy_url = f"http://127.0.0.1:{proxy.server_port}"
        with patch.dict(
            os.environ,
            {"HTTP_PROXY": proxy_url, "http_proxy": proxy_url, "NO_PROXY": "", "no_proxy": ""},
            clear=False,
        ), patch("urllib.request.proxy_bypass", return_value=False):
            adapter = HttpAdapter(
                inventory=inventory_for(direct.server_port), profile_id="core",
                token_resolver=lambda: "token",
            )
            self.assertEqual(adapter.read_config(), {"mode": "direct"})
        self.assertEqual(direct_requests, ["/v1/config"])
        self.assertEqual(proxy_requests, [])

    def test_redirect_is_rejected_before_bearer_token_reaches_another_hop(self):
        redirected_headers = []

        class TargetHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                redirected_headers.append(self.headers.get("Authorization"))
                body = b"{}"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args): pass

        target = start_http_server(self, TargetHandler)

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{target.server_port}/redirected")
                self.end_headers()
            def log_message(self, *_args): pass

        source = start_http_server(self, RedirectHandler)
        adapter = HttpAdapter(
            inventory=inventory_for(source.server_port), profile_id="core",
            token_resolver=lambda: "do-not-forward",
        )
        with self.assertRaisesRegex(AdapterError, "redirect"):
            adapter.read_config()
        self.assertEqual(redirected_headers, [])

    def test_http_response_json_rejects_duplicate_keys_and_non_finite_numbers(self):
        bodies = iter((b'{"mode":"safe","mode":"changed"}', b'{"value":NaN}'))

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = next(bodies)
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args): pass

        server = start_http_server(self, Handler)
        adapter = HttpAdapter(
            inventory=inventory_for(server.server_port), profile_id="core",
            token_resolver=lambda: "token",
        )
        for _ in range(2):
            with self.assertRaisesRegex(AdapterError, "invalid JSON"):
                adapter.read_config()

    def test_http_request_json_rejects_non_finite_numbers(self):
        adapter = HttpAdapter(
            inventory=inventory_for(7979),
            profile_id="core",
            token_resolver=lambda: "token",
        )
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaisesRegex(
                AdapterError, "JSON request is invalid"
            ):
                adapter._encode({"value": value})

    def test_dedicated_tls_context_requires_certificate_and_hostname_validation(self):
        adapter = HttpAdapter(
            inventory=inventory_for(443, scheme="https", host="example.com", approved_tls=True),
            profile_id="core", token_resolver=lambda: "token",
        )
        context = adapter._tls_context
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_migration_generation_rejects_missing_or_malformed_server_tokens(self):
        responses = iter((
            b"{}",
            b'{"generation":""}',
            b'{"generation":"ok","extra":1}',
            b'{"generation":"\\ud800"}',
            b'{"generation":"control\\u001f"}',
        ))

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = next(responses)
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args): pass

        server = start_http_server(self, Handler)
        adapter = HttpAdapter(
            inventory=inventory_for(server.server_port), profile_id="core",
            token_resolver=lambda: "token",
        )
        for _ in range(5):
            with self.assertRaisesRegex(AdapterError, "migration generation"):
                adapter.read_migration_generation()

    def test_endpoint_identity_must_equal_selected_inventory_endpoint(self):
        adapter = HttpAdapter(
            inventory=inventory_for(7979), profile_id="core",
            token_resolver=lambda: "token",
        )
        with patch.object(adapter, "_request", return_value={
            **adapter.endpoint.to_dict(), "port": 7980,
        }):
            with self.assertRaisesRegex(AdapterError, "selected inventory"):
                adapter.endpoint_identity()

    def test_rollback_archive_bindings_are_both_or_neither(self):
        adapter = HttpAdapter(
            inventory=inventory_for(7979), profile_id="core",
            token_resolver=lambda: "token",
        )
        with self.assertRaisesRegex(AdapterError, "supplied together"):
            adapter.create_rollback_bundle(
                "a" * 64, ("action",), archive_digest="b" * 64,
            )

    def test_apply_binding_requires_exact_verified_current_rollback(self):
        adapter = HttpAdapter(
            inventory=inventory_for(7979), profile_id="core",
            token_resolver=lambda: "token",
        )
        state = {"banks": []}
        rollback = RollbackBundle(
            "rollback", "a" * 64, ("action",), digest(state),
            digest(adapter.endpoint.to_dict()), "d" * 64, "e" * 64,
        )
        def snapshot():
            self.assertTrue(adapter._apply_binding_lock.locked())
            return {
                "endpoint": adapter.endpoint.to_dict(), "state": state,
                "operations": {"idle": True, "active": []},
            }

        adapter.snapshot = snapshot
        with self.assertRaisesRegex(AdapterError, "exact verified"):
            adapter.bind_apply_plan(rollback)

        with adapter._apply_binding_lock:
            adapter._verified_rollbacks[rollback.rollback_id] = rollback
        adapter.bind_apply_plan(rollback)

        with self.assertRaisesRegex(AdapterError, "already active"):
            adapter.bind_apply_plan(rollback)

        drifted_state = {"banks": ["changed"]}
        with adapter._apply_binding_lock:
            adapter._apply_binding = None
        adapter.snapshot = lambda: {
            "endpoint": adapter.endpoint.to_dict(), "state": drifted_state,
            "operations": {"idle": True, "active": []},
        }
        with self.assertRaisesRegex(AdapterError, "current rollback"):
            adapter.bind_apply_plan(rollback)
        forged = replace(rollback, bundle_digest="f" * 64)
        with self.assertRaisesRegex(AdapterError, "exact verified"):
            adapter.bind_apply_plan(forged)

    def test_restore_clears_apply_state_only_after_verified_success(self):
        adapter = HttpAdapter(
            inventory=inventory_for(7979), profile_id="core",
            token_resolver=lambda: "token",
        )
        rollback = RollbackBundle(
            "rollback", "a" * 64, ("action",), "b" * 64,
            digest(adapter.endpoint.to_dict()), "d" * 64, "e" * 64,
        )
        binding = {"rollback": rollback, "action_ids": ["action"]}
        attestation = {"action_id": "action"}
        with adapter._apply_binding_lock:
            adapter._apply_binding = binding
            adapter._last_action_attestation = attestation
        with patch.object(adapter, "_request", return_value={"restored": False}):
            with self.assertRaisesRegex(AdapterError, "attestation"):
                adapter.restore(rollback)
        self.assertIs(adapter._apply_binding, binding)
        self.assertIs(adapter._last_action_attestation, attestation)

        response = {
            "restored": True,
            "rollback_id": rollback.rollback_id,
            "bundle_digest": rollback.bundle_digest,
            "prestate_digest": rollback.prestate_digest,
        }
        with patch.object(adapter, "_request", return_value=response):
            adapter.restore(rollback)
        self.assertIsNone(adapter._apply_binding)
        self.assertIsNone(adapter._last_action_attestation)

    def test_artifact_action_resolves_and_verifies_desired_payload(self):
        desired = {"mode": "active", "limits": {"recall": 10}}
        action = Action(
            "configure-core", "configure_profile",
            {"profile_id": "core", "artifact_digest": digest(desired)},
        )
        adapter = HttpAdapter(
            inventory=inventory_for(7979), profile_id="core",
            token_resolver=lambda: "token", artifact_resolver=lambda _action: desired,
        )
        plan_digest = "a" * 64
        prestate = {}
        prestate_digest = digest(prestate)
        endpoint_digest = digest(adapter.endpoint.to_dict())
        rollback = RollbackBundle(
            "rollback", plan_digest, (action.id,), prestate_digest,
            endpoint_digest, "d" * 64, "e" * 64,
        )
        self.bind_verified(adapter, rollback, prestate)

        def attested(details):
            return {
                "control_attestation": {
                    **details["control"], "poststate_digest": "f" * 64,
                }
            }

        with patch.object(adapter, "patch_config", side_effect=attested) as mutate:
            adapter.apply_action(action)
        mutate.assert_called_once_with({
            "profile_id": "core",
            "desired": desired,
            "control": {
                "schema_version": 1,
                "plan_digest": plan_digest,
                "action_id": action.id,
                "action_kind": action.kind,
                "expected_state_digest": prestate_digest,
                "expected_endpoint_digest": endpoint_digest,
            },
        })

    def test_artifact_action_refuses_missing_or_mismatched_resolution(self):
        desired = {"mode": "active"}
        action = Action(
            "configure-core", "configure_profile",
            {"profile_id": "core", "artifact_digest": digest(desired)},
        )
        adapter = HttpAdapter(
            inventory=inventory_for(7979), profile_id="core", token_resolver=lambda: "token",
        )
        with self.assertRaisesRegex(AdapterError, "resolver is required"):
            adapter.apply_action(action)
        mismatched = HttpAdapter(
            inventory=inventory_for(7979), profile_id="core", token_resolver=lambda: "token",
            artifact_resolver=lambda _action: {"mode": "different"},
        )
        with self.assertRaisesRegex(AdapterError, "digest does not match"):
            mismatched.apply_action(action)

    def test_artifact_action_uses_the_snapshot_that_was_digest_verified(self):
        desired = {"mode": "active", "limits": {"recall": 10}}
        expected = digest(desired)
        action = Action(
            "configure-core", "configure_profile",
            {"profile_id": "core", "artifact_digest": expected},
        )
        adapter = HttpAdapter(
            inventory=inventory_for(7979), profile_id="core",
            token_resolver=lambda: "token",
            artifact_resolver=lambda _action: desired,
        )
        prestate = {}
        rollback = RollbackBundle(
            "rollback", "a" * 64, (action.id,), digest(prestate),
            digest(adapter.endpoint.to_dict()), "d" * 64, "e" * 64,
        )
        self.bind_verified(adapter, rollback, prestate)

        def mutate_after_digest(value):
            verified = digest(value)
            desired["limits"]["recall"] = 999
            return verified

        def attested(details):
            self.assertEqual(details["desired"]["limits"]["recall"], 10)
            return {
                "control_attestation": {
                    **details["control"], "poststate_digest": "f" * 64,
                }
            }

        with (
            patch(
                "hindsight_memory_control_plane.http_adapter.digest",
                side_effect=mutate_after_digest,
            ),
            patch.object(adapter, "patch_config", side_effect=attested),
        ):
            adapter.apply_action(action)

    def test_http_actions_and_postconditions_are_plan_and_state_bound(self):
        actions = (
            Action("create", "create_bank", {"bank_id": "engineering"}),
            Action("reload", "reload_profile", {"profile_id": "core"}),
        )
        adapter = HttpAdapter(
            inventory=inventory_for(7979), profile_id="core",
            token_resolver=lambda: "token",
        )
        prestate = {}
        rollback = RollbackBundle(
            "rollback", "a" * 64, tuple(action.id for action in actions),
            digest(prestate), digest(adapter.endpoint.to_dict()),
            "d" * 64, "e" * 64,
        )
        self.bind_verified(adapter, rollback, prestate)
        requests = []
        poststates = iter(("1" * 64, "2" * 64))

        def request(method, path, payload):
            requests.append((method, path, payload))
            if path == "/v1/postconditions/verify":
                return {
                    "verified": True,
                    "control_attestation": payload["control"],
                }
            return {
                "control_attestation": {
                    **payload["control"],
                    "poststate_digest": next(poststates),
                }
            }

        adapter._request = request
        for action in actions:
            adapter.apply_action(action)
            self.assertTrue(adapter.verify_postcondition(action))

        mutation_controls = [
            payload["control"]
            for _method, path, payload in requests
            if path != "/v1/postconditions/verify"
        ]
        self.assertEqual(
            [item["expected_state_digest"] for item in mutation_controls],
            [digest(prestate), "1" * 64],
        )
        self.assertEqual(
            [(item["action_id"], item["action_kind"]) for item in mutation_controls],
            [("create", "create_bank"), ("reload", "reload_profile")],
        )
        self.assertTrue(all(
            item["plan_digest"] == "a" * 64 for item in mutation_controls
        ))

    def test_http_action_rejects_operation_drift_before_mutation(self):
        action = Action("create", "create_bank", {"bank_id": "engineering"})
        adapter = HttpAdapter(
            inventory=inventory_for(7979), profile_id="core",
            token_resolver=lambda: "token",
        )
        prestate = {}
        rollback = RollbackBundle(
            "rollback", "a" * 64, (action.id,), digest(prestate),
            digest(adapter.endpoint.to_dict()), "d" * 64, "e" * 64,
        )
        self.bind_verified(adapter, rollback, prestate)
        adapter.snapshot = lambda: {
            "endpoint": adapter.endpoint.to_dict(),
            "state": prestate,
            "operations": {
                "idle": False,
                "active": [{"operation_id": "unexpected"}],
            },
        }

        with (
            patch.object(adapter, "_request") as request,
            self.assertRaisesRegex(AdapterError, "operations drifted"),
        ):
            adapter.apply_action(action)
        request.assert_not_called()

    def test_external_admin_action_is_attested_against_http_poststate(self):
        action = Action("migrate", "migrate_bank", {})
        adapter = HttpAdapter(
            inventory=inventory_for(7979), profile_id="core",
            token_resolver=lambda: "token",
            external_action_reserver=lambda _control: None,
        )
        prestate = {"banks": [], "profile_artifact_digest": "f" * 64}
        state = {"banks": [], "profile_artifact_digest": "e" * 64}
        rollback = RollbackBundle(
            "rollback", "a" * 64, (action.id,), digest(prestate),
            digest(adapter.endpoint.to_dict()), "d" * 64, "e" * 64,
        )
        mutated = False
        adapter.snapshot = lambda: {
            "endpoint": adapter.endpoint.to_dict(),
            "state": state if mutated else prestate,
            "operations": {"idle": True, "active": []},
        }
        with adapter._apply_binding_lock:
            adapter._verified_rollbacks[rollback.rollback_id] = rollback
        adapter.bind_apply_plan(rollback)
        seen = []

        def request(_method, path, payload):
            seen.append((path, payload))
            return {
                "verified": True,
                "control_attestation": payload["control"],
            }

        adapter._request = request
        def mutate():
            nonlocal mutated
            mutated = True

        adapter.attest_external_action(action, mutate)

        self.assertTrue(adapter.verify_postcondition(action))
        control = seen[0][1]["control"]
        self.assertEqual(control["action_id"], "migrate")
        self.assertEqual(control["action_kind"], "migrate_bank")
        self.assertEqual(control["expected_state_digest"], digest(prestate))
        self.assertEqual(control["poststate_digest"], digest(state))

    def test_external_admin_action_is_terminally_reserved_before_mutation(self):
        action = Action("migrate", "migrate_bank", {})
        reservations = []
        events = []

        def reserve(control):
            reservations.append(dict(control))
            events.append("reserved")

        adapter = HttpAdapter(
            inventory=inventory_for(7979), profile_id="core",
            token_resolver=lambda: "token",
            external_action_reserver=reserve,
        )
        prestate = {"banks": [], "profile_artifact_digest": "f" * 64}
        rollback = RollbackBundle(
            "rollback", "a" * 64, (action.id,), digest(prestate),
            digest(adapter.endpoint.to_dict()), "d" * 64, "e" * 64,
        )
        adapter.snapshot = lambda: {
            "endpoint": adapter.endpoint.to_dict(), "state": prestate,
            "operations": {"idle": True, "active": []},
        }
        with adapter._apply_binding_lock:
            adapter._verified_rollbacks[rollback.rollback_id] = rollback
        adapter.bind_apply_plan(rollback)
        mutations = []

        def mutate():
            mutations.append("called")
            events.append("mutated")
            adapter.snapshot = lambda: {"endpoint": adapter.endpoint.to_dict()}

        with self.assertRaisesRegex(AdapterError, "poststate is invalid"):
            adapter.attest_external_action(action, mutate)
        self.assertEqual(mutations, ["called"])
        self.assertEqual(len(reservations), 1)
        self.assertEqual(events, ["reserved", "mutated"])
        with self.assertRaisesRegex(AdapterError, "approved plan sequence"):
            adapter.attest_external_action(action, mutate)
        self.assertEqual(mutations, ["called"])

    def test_http_error_response_is_closed_after_authentication_failure(self):
        adapter = HttpAdapter(
            inventory=inventory_for(7979),
            profile_id="core",
            token_resolver=lambda: "token",
        )
        response_body = BytesIO(b'{}')
        failure = HTTPError(
            "http://127.0.0.1:7979/v1/schema",
            401,
            "Unauthorized",
            {},
            response_body,
        )
        with patch.object(adapter._opener, "open", side_effect=failure):
            with self.assertRaises(AuthenticationError):
                adapter.schema_version()
        self.assertTrue(response_body.closed)

    def assert_rollback_id_rejected(self, rollback_id):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append(self.path)
                length = int(self.headers.get("Content-Length", "0"))
                request = json.loads(self.rfile.read(length))
                value = {
                    "rollback_id": rollback_id,
                    "plan_digest": request["plan_digest"],
                    "action_ids": request["action_ids"],
                    "prestate_digest": "b" * 64,
                    "endpoint_digest": "c" * 64,
                    "bundle_digest": "d" * 64,
                    "restore_proof_digest": "e" * 64,
                }
                body = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args): pass

        server = start_http_server(self, Handler)
        adapter = HttpAdapter(inventory=inventory_for(server.server_port), profile_id="core", token_resolver=lambda: "token")
        with self.assertRaisesRegex(AdapterError, "rollback attestation"):
            adapter.create_rollback_bundle("a" * 64, ("action-1",))
        forged = RollbackBundle(rollback_id, "a" * 64, ("action-1",), "b" * 64, "c" * 64, "d" * 64, "e" * 64)
        with self.assertRaisesRegex(AdapterError, "rollback attestation"):
            adapter.verify_rollback_bundle(forged)
        with self.assertRaisesRegex(AdapterError, "rollback attestation"):
            adapter.restore(forged)
        self.assertEqual(requests, ["/v1/rollbacks"])

    def test_rollback_id_rejects_slash(self):
        self.assert_rollback_id_rejected("safe/escape")

    def test_rollback_id_rejects_query(self):
        self.assert_rollback_id_rejected("safe?query=1")

    def test_rollback_id_rejects_fragment(self):
        self.assert_rollback_id_rejected("safe#fragment")

    def test_rollback_id_rejects_control_character(self):
        self.assert_rollback_id_rejected("safe\nprivate")

    def test_rollback_id_rejects_oversized_value(self):
        self.assert_rollback_id_rejected("a" * 129)

    def test_endpoint_must_be_derived_from_inventory_and_scheme_policy(self):
        with self.assertRaisesRegex(AdapterError, "digests"):
            HttpAdapter(inventory=replace(inventory_for(7979), inventory_digest="0" * 64), profile_id="core", token_resolver=lambda: "token")
        with self.assertRaisesRegex(AdapterError, "loopback"):
            HttpAdapter(inventory=inventory_for(80, host="example.com"), profile_id="core", token_resolver=lambda: "token")
        with self.assertRaisesRegex(AdapterError, "scheme"):
            HttpAdapter(inventory=inventory_for(80, scheme="ftp"), profile_id="core", token_resolver=lambda: "token")
        with self.assertRaisesRegex(AdapterError, "approved"):
            HttpAdapter(inventory=inventory_for(443, scheme="https", host="example.com"), profile_id="core", token_resolver=lambda: "token")
        approved = HttpAdapter(inventory=inventory_for(443, scheme="https", host="example.com", approved_tls=True), profile_id="core", token_resolver=lambda: "token")
        self.assertEqual(approved.endpoint.host, "example.com")

    def test_iterencoded_request_is_stopped_at_byte_bound_before_network(self):
        class Handler(BaseHTTPRequestHandler):
            def do_PATCH(self):
                self.send_response(500)
                self.end_headers()
            def log_message(self, *_args): pass
        server = start_http_server(self, Handler)
        adapter = HttpAdapter(inventory=inventory_for(server.server_port), profile_id="core", token_resolver=lambda: "token", max_json_bytes=32)
        with self.assertRaisesRegex(AdapterError, "request exceeds"):
            adapter.patch_config({"value": "x" * 100_000})
        self.assertEqual(adapter.recordings, [])

    def test_recordings_are_bounded_and_can_be_disabled(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"{}"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = start_http_server(self, Handler)
        bounded = HttpAdapter(
            inventory=inventory_for(server.server_port),
            profile_id="core",
            token_resolver=lambda: "token",
            max_recordings=2,
        )
        bounded.read_config()
        bounded.read_stats()
        bounded.read_tags()
        self.assertEqual(
            [recording["path"] for recording in bounded.recordings],
            ["/v1/stats", "/v1/tags"],
        )

        disabled = HttpAdapter(
            inventory=inventory_for(server.server_port),
            profile_id="core",
            token_resolver=lambda: "token",
            max_recordings=0,
        )
        disabled.read_config()
        self.assertEqual(disabled.recordings, [])

    def test_recording_buffer_size_is_validated(self):
        for invalid in (True, -1, 10_001):
            with self.subTest(max_recordings=invalid), self.assertRaisesRegex(
                AdapterError, "recording buffer size"
            ):
                HttpAdapter(
                    inventory=inventory_for(7979),
                    profile_id="core",
                    token_resolver=lambda: "token",
                    max_recordings=invalid,
                )

    def test_invalid_content_length_is_normalized(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "invalid")
                self.end_headers()
                self.wfile.write(b"{}")
            def log_message(self, *_args): pass
        server = start_http_server(self, Handler)
        adapter = HttpAdapter(inventory=inventory_for(server.server_port), profile_id="core", token_resolver=lambda: "token")
        with self.assertRaisesRegex(AdapterError, "Content-Length"):
            adapter.read_config()

    def test_non_object_json_is_normalized(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"[]"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args): pass
        server = start_http_server(self, Handler)
        adapter = HttpAdapter(inventory=inventory_for(server.server_port), profile_id="core", token_resolver=lambda: "token")
        with self.assertRaisesRegex(AdapterError, "non-object"):
            adapter.read_config()

    def test_uses_resolved_bearer_token_without_recording_or_exposing_it(self):
        token = "top-secret-token"
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, self.headers.get("Authorization")))
                body = json.dumps({"mode": "safe"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = start_http_server(self, Handler)
        adapter = HttpAdapter(inventory=inventory_for(server.server_port), profile_id="core", token_resolver=lambda: token)

        self.assertEqual(adapter.read_config(), {"mode": "safe"})
        self.assertEqual(seen, [("/v1/config", f"Bearer {token}")])
        self.assertNotIn(token, repr(adapter))
        self.assertNotIn(token, json.dumps(adapter.recordings))

    def test_preserves_401_redacts_token_and_rejects_oversized_json(self):
        token = "never-print-this-token"
        status = {"code": 401}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"{}" if status["code"] == 401 else b"x" * 2048
                self.send_response(status["code"])
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args): pass

        server = start_http_server(self, Handler)
        adapter = HttpAdapter(inventory=inventory_for(server.server_port), profile_id="core", token_resolver=lambda: token, max_json_bytes=1024, timeout=100)
        with self.assertRaises(AuthenticationError) as auth:
            adapter.read_config()
        self.assertNotIn(token, str(auth.exception))
        status["code"] = 200
        with self.assertRaisesRegex(AdapterError, "size limit"):
            adapter.read_config()
        self.assertEqual(adapter.timeout, 30.0)

    def test_enforces_request_timeout_without_leaking_credentials(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                time.sleep(0.25)
                try:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"{}")
                except BrokenPipeError:
                    pass
            def log_message(self, *_args): pass

        server = start_http_server(self, Handler)
        adapter = HttpAdapter(inventory=inventory_for(server.server_port), profile_id="core", token_resolver=lambda: "timeout-secret", timeout=0.1)
        with self.assertRaises(AdapterError) as failure:
            adapter.read_config()
        self.assertNotIn("timeout-secret", str(failure.exception))

    def test_request_timeout_is_one_absolute_deadline_across_response_reads(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "7")
                self.end_headers()
                for byte in b'{"x":1}':
                    time.sleep(0.03)
                    try:
                        self.wfile.write(bytes((byte,)))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        break

            def log_message(self, *_args):
                pass

        server = start_http_server(self, Handler)
        adapter = HttpAdapter(
            inventory=inventory_for(server.server_port),
            profile_id="core",
            token_resolver=lambda: "timeout-secret",
            timeout=0.1,
        )
        started = time.monotonic()
        with self.assertRaisesRegex(AdapterError, "request"):
            adapter.read_config()
        self.assertLess(time.monotonic() - started, 1.0)

    def test_http_protocol_failures_are_redacted_as_adapter_errors(self):
        from http.client import HTTPException

        adapter = HttpAdapter(
            inventory=inventory_for(7979), profile_id="core",
            token_resolver=lambda: "protocol-secret",
        )
        with patch.object(adapter._opener, "open", side_effect=HTTPException("bad protocol")):
            with self.assertRaises(AdapterError) as failure:
                adapter.read_config()
        self.assertNotIn("protocol-secret", str(failure.exception))

    def test_response_deadline_does_not_require_urllib_private_socket(self):
        adapter = HttpAdapter(
            inventory=inventory_for(7979), profile_id="core",
            token_resolver=lambda: "token", timeout=0.05,
        )
        released = threading.Event()

        class WrappedResponse:
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

            def read(self, _limit):
                released.wait(1)
                return b"{}"

            def close(self):
                released.set()

        started = time.monotonic()
        with patch.object(
            adapter._opener, "open", return_value=WrappedResponse()
        ):
            with self.assertRaisesRegex(AdapterError, "timed out"):
                adapter.read_config()
        self.assertLess(time.monotonic() - started, 0.5)

    def test_migration_config_redacts_and_digests_missions(self):
        safe = HttpAdapter._safe_config({
            "bank_id": "engineering",
            "config": {
                "mission": "private mission",
                "retain_mission": "private retention policy",
            },
            "overrides": {},
        })
        self.assertNotIn("mission", safe["config"])
        self.assertNotIn("retain_mission", safe["config"])
        self.assertEqual(
            safe["config"]["mission_digest"], digest("private mission")
        )
        self.assertEqual(
            safe["config"]["retain_mission_digest"],
            digest("private retention policy"),
        )
        self.assertEqual(
            safe["redacted_keys"],
            ["config.mission", "config.retain_mission"],
        )


class AdminMigrationAdapterContractTest(unittest.TestCase):
    ARCHIVE_PAYLOAD = b"verified hindsight admin archive\n"
    ARCHIVE_DIGEST = hashlib.sha256(ARCHIVE_PAYLOAD).hexdigest()

    def private_archive(self, name="bank.zip"):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        return str(root / name)

    def test_exact_runtime_copy_rejects_truncation_and_one_byte_growth(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        source = root / "source"
        destination = root / "destination"
        source.write_bytes(b"runtime")
        source_descriptor = os.open(source, os.O_RDONLY)
        destination_descriptor = os.open(
            destination, os.O_WRONLY | os.O_CREAT, 0o600
        )
        try:
            with self.assertRaisesRegex(FileEvidenceError, "size changed"):
                migration_adapter_module._copy_exact_descriptor(
                    source_descriptor, destination_descriptor, 6, "runtime"
                )
        finally:
            os.close(source_descriptor)
            os.close(destination_descriptor)

        source_descriptor = os.open(source, os.O_RDONLY)
        try:
            with self.assertRaisesRegex(FileEvidenceError, "size changed"):
                migration_adapter_module._copy_exact_descriptor(
                    source_descriptor, None, 8, "runtime"
                )
        finally:
            os.close(source_descriptor)

    def test_process_group_kill_has_a_bounded_final_wait(self):
        class Process:
            pid = 1234

            def poll(self):
                return None

            def wait(self, timeout):
                raise subprocess.TimeoutExpired("admin", timeout)

            def kill(self):
                return None

        with (
            patch.object(os, "killpg", return_value=None),
            patch.object(time, "sleep", return_value=None),
            patch.object(time, "monotonic", side_effect=(0.0, 2.0)),
            self.assertRaisesRegex(MigrationAdapterError, "did not terminate"),
        ):
            AdminMigrationAdapter._terminate_process_group(Process())

    def test_verified_rollback_identity_cache_is_lru_bounded(self):
        adapter = object.__new__(MigrationApplyAdapter)
        adapter._verified_rollback_identities = migration_adapter_module.OrderedDict()
        adapter._verified_rollback_identities_lock = threading.Lock()
        with patch.object(
            migration_adapter_module,
            "MAX_VERIFIED_ROLLBACK_IDENTITIES",
            2,
        ):
            adapter._remember_verified_rollback_identity("first")
            adapter._remember_verified_rollback_identity("second")
            self.assertTrue(adapter._rollback_identity_is_verified("first"))
            adapter._remember_verified_rollback_identity("third")

        self.assertEqual(
            tuple(adapter._verified_rollback_identities),
            ("first", "third"),
        )

    @staticmethod
    def bound_admin_args(argv):
        descriptor_index = next(
            index
            for index, value in enumerate(argv)
            if re.fullmatch(r"/dev/fd/[0-9]+", value)
        )
        return argv[descriptor_index], argv[descriptor_index + 1:]

    @classmethod
    def write_admin_output(cls, argv):
        _binding, admin_args = cls.bound_admin_args(argv)
        operation = admin_args[0]
        if operation == "backup":
            Path(admin_args[1]).write_bytes(cls.ARCHIVE_PAYLOAD)
        elif operation == "export-bank":
            Path(admin_args[4]).write_bytes(cls.ARCHIVE_PAYLOAD)

    def trusted_admin(self, root):
        executable = Path(root) / "hindsight-admin"
        executable.write_text(
            f"#!{TRUSTED_TEST_PYTHON}\nraise SystemExit('test seam only')\n",
            encoding="utf-8",
        )
        executable.chmod(0o700)
        return executable

    @staticmethod
    def runtime_manifest(path):
        path = Path(path).absolute()
        return {
            "root": str(path.parent),
            "files": [{"path": str(path), "relative": path.name}],
        }

    @staticmethod
    def probe_response(argv, *, version="0.8.4"):
        if "HINDSIGHT_INTERPRETER_PREFIX_V1" in argv[-1]:
            root = Path(__file__).resolve().parent
            values = (root, root, root, root, Path(__file__).resolve())
            return subprocess.CompletedProcess(
                argv,
                0,
                "\n".join(
                    str(value).encode("utf-8").hex() for value in values
                ) + "\n",
                "",
            )
        if "HINDSIGHT_RUNTIME_MANIFEST_V1" in argv[-1]:
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps(
                    AdminMigrationAdapterContractTest.runtime_manifest(
                        Path(__file__).resolve()
                    )
                ),
                "",
            )
        if "importlib.metadata" in argv[-1]:
            return subprocess.CompletedProcess(argv, 0, version + "\n", "")
        return None

    @staticmethod
    def versioned_runner(calls, *, version="0.8.4", operation=None):
        def run(argv, **kwargs):
            calls.append((list(argv), kwargs))
            probe = AdminMigrationAdapterContractTest.probe_response(
                argv, version=version
            )
            if probe is not None:
                return probe
            AdminMigrationAdapterContractTest.write_admin_output(argv)
            if operation is not None:
                operation(argv)
            return subprocess.CompletedProcess(argv, 0, "Complete", "")
        return run

    def make_admin(self, runner, *, argv_factory=admin_argv, version="0.8.4"):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        executable = self.trusted_admin(root)

        def routed(argv, **_kwargs):
            probe = self.probe_response(argv, version=version)
            if probe is not None:
                return probe
            self.write_admin_output(argv)
            return runner(argv)

        return AdminMigrationAdapter(
            admin_executable=str(executable),
            argv_factory=argv_factory,
            runner=routed,
        ), str(executable)

    def test_binds_trusted_executable_probes_version_and_sanitizes_process_context(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        executable = self.trusted_admin(root)
        calls = []
        adapter = AdminMigrationAdapter(
            admin_executable=str(executable),
            argv_factory=hindsight_admin_argv,
            runner=self.versioned_runner(calls),
            environment={
                "HINDSIGHT_API_DATABASE_URL": "postgresql://approved",
                "PATH": "/attacker/bin",
                "PYTHONPATH": "/attacker/python",
                "UNRELATED_SECRET": "must-not-flow",
            },
        )
        adapter.backup(self.private_archive(), self.ARCHIVE_DIGEST)

        self.assertEqual(adapter.admin_version, "0.8.4")
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0][0][1:4], ["-I", "-S", "-c"])
        self.assertEqual(calls[1][0][1:4], ["-I", "-S", "-c"])
        operation_argv, operation_kwargs = calls[3]
        self.assertEqual(operation_argv[0], TRUSTED_TEST_PYTHON)
        binding, admin_args = self.bound_admin_args(operation_argv)
        self.assertRegex(binding, r"^/dev/fd/[0-9]+$")
        self.assertEqual(admin_args[0], "backup")
        self.assertRegex(
            admin_args[1],
            r"/\.hindsight-admin-output-[^/]+/archive\.zip$",
        )
        self.assertEqual(admin_args[2:], ["--schema", "public"])
        for index, (_argv, kwargs) in enumerate(calls):
            self.assertEqual(kwargs["cwd"], "/")
            self.assertIs(kwargs["start_new_session"], True)
            self.assertEqual(
                kwargs["env"].get("HINDSIGHT_API_DATABASE_URL"),
                "postgresql://approved",
            )
            self.assertNotIn("/attacker", json.dumps(kwargs["env"]))
            if index == 0:
                self.assertNotIn("PYTHONPATH", kwargs["env"])
                self.assertNotIn("PYTHONHOME", kwargs["env"])
            else:
                self.assertRegex(
                    kwargs["env"]["PYTHONPATH"],
                    r"/\.hindsight-admin-runtime-[^/]+$",
                )
                self.assertEqual(kwargs["env"]["PYTHONNOUSERSITE"], "1")
                self.assertRegex(
                    kwargs["env"]["PYTHONHOME"],
                    r"/\.hindsight-admin-runtime-[^/]+/\.python-home-0$",
                )

    def test_runtime_snapshot_execution_disables_user_sitecustomize(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        user_site = (
            root
            / "user-base"
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}"
            / "site-packages"
        )
        user_site.mkdir(parents=True)
        marker = root / "sitecustomize-ran"
        (user_site / "sitecustomize.py").write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('unsafe', encoding='utf-8')\n",
            encoding="utf-8",
        )
        runtime_root = root / "runtime"
        runtime_root.mkdir()
        adapter = object.__new__(AdminMigrationAdapter)
        adapter._environment = {"PYTHONUSERBASE": str(root / "user-base")}
        adapter.runner = subprocess.run

        result = adapter._invoke(
            [
                sys.executable,
                "-c",
                "import site; print(site.ENABLE_USER_SITE)",
            ],
            timeout=10,
            runtime_root=str(runtime_root),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "False")
        self.assertFalse(marker.exists())

    def test_revalidates_executable_identity_before_each_operation(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        executable = self.trusted_admin(root)
        calls = []
        adapter = AdminMigrationAdapter(
            admin_executable=str(executable), argv_factory=hindsight_admin_argv,
            runner=self.versioned_runner(calls),
        )
        replacement = root / "replacement"
        replacement.write_text(
            f"#!{TRUSTED_TEST_PYTHON}\nraise SystemExit('replacement')\n",
            encoding="utf-8",
        )
        replacement.chmod(0o700)
        os.replace(replacement, executable)

        with self.assertRaisesRegex(MigrationAdapterError, "identity changed"):
            adapter.backup(self.private_archive(), "a" * 64)
        self.assertEqual(len(calls), 3)

    def test_revalidates_runtime_file_identity_before_each_operation(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        executable = self.trusted_admin(root)
        runtime_file = root / "runtime.py"
        runtime_file.write_text("VALUE = 1\n", encoding="utf-8")
        runtime_file.chmod(0o600)
        calls = []

        def runner(argv, **kwargs):
            calls.append((list(argv), kwargs))
            if "HINDSIGHT_INTERPRETER_PREFIX_V1" in argv[-1]:
                return self.probe_response(argv)
            if "HINDSIGHT_RUNTIME_MANIFEST_V1" in argv[-1]:
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    json.dumps(self.runtime_manifest(runtime_file)),
                    "",
                )
            if "importlib.metadata" in argv[-1]:
                return subprocess.CompletedProcess(argv, 0, "0.8.4\n", "")
            raise AssertionError("mutated runtime must not execute")

        adapter = AdminMigrationAdapter(
            admin_executable=str(executable),
            argv_factory=hindsight_admin_argv,
            runner=runner,
        )
        replacement = root / "replacement-runtime.py"
        replacement.write_text("VALUE = 2\n", encoding="utf-8")
        replacement.chmod(0o600)
        os.replace(replacement, runtime_file)

        with self.assertRaisesRegex(MigrationAdapterError, "identity changed"):
            adapter.backup(self.private_archive(), "a" * 64)
        self.assertEqual(len(calls), 3)

    def test_revalidates_interpreter_prefix_before_each_operation(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        executable = self.trusted_admin(root)
        prefix_file = root / "stdlib.py"
        prefix_file.write_text("VALUE = 1\n", encoding="utf-8")
        prefix_file.chmod(0o600)
        calls = []

        def runner(argv, **kwargs):
            calls.append((list(argv), kwargs))
            if "HINDSIGHT_INTERPRETER_PREFIX_V1" in argv[-1]:
                values = (root, root, root, root, prefix_file)
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    "\n".join(
                        str(value).encode("utf-8").hex()
                        for value in values
                    ) + "\n",
                    "",
                )
            if "HINDSIGHT_RUNTIME_MANIFEST_V1" in argv[-1]:
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    json.dumps(self.runtime_manifest(Path(__file__).resolve())),
                    "",
                )
            if "importlib.metadata" in argv[-1]:
                return subprocess.CompletedProcess(argv, 0, "0.8.4\n", "")
            raise AssertionError("mutated interpreter prefix must not execute")

        adapter = AdminMigrationAdapter(
            admin_executable=str(executable),
            argv_factory=hindsight_admin_argv,
            runner=runner,
        )
        replacement = root / "replacement-stdlib.py"
        replacement.write_text("VALUE = 2\n", encoding="utf-8")
        replacement.chmod(0o600)
        os.replace(replacement, prefix_file)

        with self.assertRaisesRegex(MigrationAdapterError, "identity changed"):
            adapter.backup(self.private_archive(), "a" * 64)
        self.assertEqual(len(calls), 3)

    def test_revalidates_runtime_manifest_symlink_identity_before_operation(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        executable = self.trusted_admin(root)
        first = root / "runtime-first.py"
        second = root / "runtime-second.py"
        for path in (first, second):
            path.write_text("VALUE = 1\n", encoding="utf-8")
            path.chmod(0o600)
        runtime_link = root / "runtime.py"
        runtime_link.symlink_to(first)
        calls = []

        def runner(argv, **kwargs):
            calls.append((list(argv), kwargs))
            if "HINDSIGHT_INTERPRETER_PREFIX_V1" in argv[-1]:
                return self.probe_response(argv)
            if "HINDSIGHT_RUNTIME_MANIFEST_V1" in argv[-1]:
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    json.dumps(self.runtime_manifest(runtime_link)),
                    "",
                )
            if "importlib.metadata" in argv[-1]:
                return subprocess.CompletedProcess(argv, 0, "0.8.4\n", "")
            raise AssertionError("retargeted runtime must not execute")

        adapter = AdminMigrationAdapter(
            admin_executable=str(executable),
            argv_factory=hindsight_admin_argv,
            runner=runner,
        )
        runtime_link.unlink()
        runtime_link.symlink_to(second)

        with self.assertRaisesRegex(MigrationAdapterError, "identity changed"):
            adapter.backup(self.private_archive(), "a" * 64)
        self.assertEqual(len(calls), 3)

    def test_version_probe_and_operation_use_private_verified_runtime_snapshots(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        executable = self.trusted_admin(root)
        runtime_file = root / "runtime.py"
        runtime_file.write_text("VALUE = 1\n", encoding="utf-8")
        runtime_file.chmod(0o600)
        observed = {}

        def runner(argv, **kwargs):
            if "HINDSIGHT_INTERPRETER_PREFIX_V1" in argv[-1]:
                return self.probe_response(argv)
            if "HINDSIGHT_RUNTIME_MANIFEST_V1" in argv[-1]:
                return subprocess.CompletedProcess(
                    argv, 0, json.dumps(self.runtime_manifest(runtime_file)), ""
                )
            snapshot = Path(kwargs["env"]["PYTHONPATH"]) / runtime_file.name
            if "importlib.metadata" in argv[-1]:
                observed["version_payload"] = snapshot.read_bytes()
                observed["version_mode"] = snapshot.stat().st_mode & 0o777
                observed["version_parent_mode"] = snapshot.parent.stat().st_mode & 0o777
                return subprocess.CompletedProcess(argv, 0, "0.8.4\n", "")
            replacement = root / "replacement-runtime.py"
            replacement.write_text("VALUE = 2\n", encoding="utf-8")
            replacement.chmod(0o600)
            os.replace(replacement, runtime_file)
            observed["operation_payload"] = snapshot.read_bytes()
            self.write_admin_output(argv)
            return subprocess.CompletedProcess(argv, 0, "Complete", "")

        adapter = AdminMigrationAdapter(
            admin_executable=str(executable),
            argv_factory=hindsight_admin_argv,
            runner=runner,
        )
        adapter.backup(self.private_archive(), self.ARCHIVE_DIGEST)

        self.assertEqual(observed["version_payload"], b"VALUE = 1\n")
        self.assertEqual(observed["operation_payload"], b"VALUE = 1\n")
        self.assertEqual(observed["version_mode"], 0o400)
        self.assertEqual(observed["version_parent_mode"], 0o700)

    def test_runtime_probe_preserves_absolute_symlink_names(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        target = root / "runtime-target.py"
        target.write_text("VALUE = 1\n", encoding="utf-8")
        link = root / "runtime-link.py"
        link.symlink_to(target)

        class Distribution:
            files = (Path(link.name),)
            metadata = {"Name": "hindsight-api"}
            requires = ()

            @staticmethod
            def locate_file(item):
                return root / item

            @staticmethod
            def read_text(_name):
                return ""

        output = StringIO()
        with (
            patch("importlib.metadata.distribution", return_value=Distribution()),
            redirect_stdout(output),
        ):
            exec(RUNTIME_FILES_PROBE, {})
        self.assertIn(
            {"path": str(link), "relative": link.name},
            json.loads(output.getvalue())["files"],
        )

    def test_runtime_probe_snapshots_transitive_distribution_files(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        primary_file = root / "hindsight_api.py"
        dependency_file = root / "transitive_dependency.py"
        primary_file.write_text("VALUE = 1\n", encoding="utf-8")
        dependency_file.write_text("VALUE = 2\n", encoding="utf-8")

        class Distribution:
            def __init__(self, name, path, requires=()):
                self.metadata = {"Name": name}
                self.path = path
                self.files = (Path(path.name),)
                self.requires = requires

            def locate_file(self, item):
                return root / item

            @staticmethod
            def read_text(_name):
                return ""

        primary = Distribution(
            "hindsight-api", primary_file, ("transitive-dependency>=1",)
        )
        dependency = Distribution("transitive-dependency", dependency_file)
        output = StringIO()
        with (
            patch(
                "importlib.metadata.distribution",
                side_effect=lambda name: {
                    "hindsight-api": primary,
                    "transitive-dependency": dependency,
                }[name],
            ),
            redirect_stdout(output),
        ):
            exec(RUNTIME_FILES_PROBE, {})

        self.assertEqual(
            {item["relative"] for item in json.loads(output.getvalue())["files"]},
            {primary_file.name, dependency_file.name},
        )

    def test_runtime_probe_ignores_only_dependencies_with_false_pep508_markers(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        primary_file = root / "hindsight_api.py"
        primary_file.write_text("VALUE = 1\n", encoding="utf-8")

        class Distribution:
            metadata = {"Name": "hindsight-api"}
            files = (Path(primary_file.name),)
            requires = ("missing-dependency; python_version < '1'",)

            @staticmethod
            def locate_file(item):
                return root / item

            @staticmethod
            def read_text(_name):
                return ""

        def distribution(name):
            if name == "hindsight-api":
                return Distribution()
            raise metadata.PackageNotFoundError(name)

        output = StringIO()
        with (
            patch("importlib.metadata.distribution", side_effect=distribution),
            redirect_stdout(output),
        ):
            exec(RUNTIME_FILES_PROBE, {})

        self.assertEqual(
            [item["relative"] for item in json.loads(output.getvalue())["files"]],
            [primary_file.name],
        )

    def test_runtime_probe_adds_runtime_site_paths_before_marker_parser_import(self):
        self.assertLess(
            RUNTIME_FILES_PROBE.index("sys.path.append(package_root)"),
            RUNTIME_FILES_PROBE.index(
                "from packaging.requirements import InvalidRequirement"
            ),
        )

    def test_runtime_probe_rejects_missing_dependency_with_true_pep508_marker(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))

        class Distribution:
            metadata = {"Name": "hindsight-api"}
            files = ()
            requires = ("missing-dependency; python_version >= '1'",)

            @staticmethod
            def locate_file(item):
                return root / item

            @staticmethod
            def read_text(_name):
                return ""

        def distribution(name):
            if name == "hindsight-api":
                return Distribution()
            raise metadata.PackageNotFoundError(name)

        with (
            patch("importlib.metadata.distribution", side_effect=distribution),
            self.assertRaisesRegex(SystemExit, "active dependency"),
        ):
            exec(RUNTIME_FILES_PROBE, {})

    def test_advisory_lock_contention_uses_nonblocking_monotonic_timeout(self):
        operations = []

        def contended(_descriptor, operation):
            operations.append(operation)
            raise BlockingIOError

        with (
            patch.object(fcntl, "flock", side_effect=contended),
            patch.object(
                migration_adapter_module.time,
                "monotonic",
                side_effect=(10.0, 10.0, 15.0),
            ),
            patch.object(migration_adapter_module.time, "sleep"),
            self.assertRaisesRegex(MigrationAdapterError, "bounded lock unavailable"),
        ):
            migration_adapter_module._acquire_advisory_lock(
                1, "bounded lock unavailable"
            )

        self.assertEqual(
            operations,
            [fcntl.LOCK_EX | fcntl.LOCK_NB] * 2,
        )

    def test_runtime_execution_uses_only_the_verified_complete_runtime_snapshot(self):
        calls = []
        adapter, _ = self.make_admin(
            lambda _argv: {"returncode": 0, "stdout": "Complete"},
        )
        adapter.runner = self.versioned_runner(calls)

        adapter.backup(self.private_archive(), self.ARCHIVE_DIGEST)

        runtime_calls = [
            (argv, options)
            for argv, options in calls
            if options.get("env", {}).get("PYTHONPATH")
        ]
        self.assertTrue(runtime_calls)
        for argv, options in runtime_calls:
            self.assertEqual(argv[0], TRUSTED_TEST_PYTHON)
            self.assertEqual(argv[1], "-S")
            self.assertRegex(argv[2], r"^/dev/fd/[0-9]+$|^-c$")
            self.assertEqual(options["env"]["PYTHONNOUSERSITE"], "1")

    def test_restore_evidence_rejects_boolean_schema_version(self):
        archive_digest = "a" * 64
        evidence = restore_evidence(archive_digest)
        evidence["schema_version"] = True
        with self.assertRaisesRegex(MigrationAdapterError, "not verified"):
            AdminMigrationAdapter._restore_evidence(
                evidence, archive_digest, digest(evidence)
            )

    def test_executes_through_the_validated_descriptor_binding(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        executable = self.trusted_admin(root)
        original = executable.read_bytes()
        observed = {}

        def runner(argv, **kwargs):
            probe = self.probe_response(argv)
            if probe is not None:
                return probe
            replacement = root / "replacement"
            replacement.write_text(
                f"#!{TRUSTED_TEST_PYTHON}\nraise SystemExit('replacement')\n",
                encoding="utf-8",
            )
            replacement.chmod(0o700)
            os.replace(replacement, executable)
            observed["argv"] = list(argv)
            observed["binding"] = argv[2]
            observed["pass_fds"] = kwargs["pass_fds"]
            observed["payload"] = Path(argv[2]).read_bytes()
            self.write_admin_output(argv)
            return subprocess.CompletedProcess(argv, 0, "Complete", "")

        adapter = AdminMigrationAdapter(
            admin_executable=str(executable),
            argv_factory=hindsight_admin_argv,
            runner=runner,
        )
        archive = self.private_archive()
        adapter.backup(archive, self.ARCHIVE_DIGEST)

        self.assertEqual(observed["argv"][0], TRUSTED_TEST_PYTHON)
        self.assertEqual(observed["argv"][3], "backup")
        self.assertEqual(observed["payload"], original)
        self.assertEqual(
            observed["binding"], f"/dev/fd/{observed['pass_fds'][0]}"
        )
        Path(archive).unlink()
        with self.assertRaisesRegex(MigrationAdapterError, "identity changed"):
            adapter.backup(archive, "a" * 64)

    def test_descriptor_binding_executes_with_the_real_subprocess_runner(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        executable = root / "hindsight-admin"
        executable.write_text(
            f"#!{TRUSTED_TEST_PYTHON}\n"
            "from pathlib import Path\n"
            f"Path(__import__('sys').argv[2]).write_bytes({self.ARCHIVE_PAYLOAD!r})\n",
            encoding="utf-8",
        )
        executable.chmod(0o700)

        def runner(argv, **kwargs):
            if "HINDSIGHT_INTERPRETER_PREFIX_V1" in argv[-1]:
                return subprocess.run(argv, **kwargs)
            probe = self.probe_response(argv)
            if probe is not None:
                return probe
            return subprocess.run(argv, **kwargs)

        adapter = AdminMigrationAdapter(
            admin_executable=str(executable),
            argv_factory=hindsight_admin_argv,
            runner=runner,
        )

        self.assertEqual(
            adapter.backup(self.private_archive(), self.ARCHIVE_DIGEST),
            {"completed": True},
        )

    def test_input_archive_subprocess_consumes_read_only_snapshot_descriptor(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        marker = root / "consumed"
        executable = root / "hindsight-admin"
        executable.write_text(
            f"#!{TRUSTED_TEST_PYTHON}\n"
            "from pathlib import Path\n"
            "import sys\n"
            f"Path({str(marker)!r}).write_bytes(Path(sys.argv[3]).read_bytes())\n",
            encoding="utf-8",
        )
        executable.chmod(0o700)

        def runner(argv, **kwargs):
            if "HINDSIGHT_INTERPRETER_PREFIX_V1" in argv[-1]:
                return subprocess.run(argv, **kwargs)
            probe = self.probe_response(argv)
            if probe is not None:
                return probe
            return subprocess.run(argv, **kwargs)

        adapter = AdminMigrationAdapter(
            admin_executable=str(executable),
            argv_factory=hindsight_admin_argv,
            runner=runner,
        )
        archive = Path(self.private_archive())
        archive.write_bytes(self.ARCHIVE_PAYLOAD)
        archive.chmod(0o600)
        evidence = restore_evidence(self.ARCHIVE_DIGEST)
        original_snapshot = verified_file_snapshot

        @contextmanager
        def swapped_snapshot(value, label, expected_digest, **kwargs):
            with original_snapshot(
                value, label, expected_digest, **kwargs
            ) as snapshot:
                if label == "hindsight-admin input archive":
                    self.assertNotIsInstance(snapshot, (str, Path))
                    with self.assertRaises(OSError):
                        os.write(snapshot.fileno(), b"attacker-controlled archive")
                yield snapshot

        with (
            patch(
                "hindsight_memory_control_plane.migration_adapter.verified_file_snapshot",
                swapped_snapshot,
            ),
        ):
            adapter.import_bank(
                str(archive), self.ARCHIVE_DIGEST, "engineering",
                digest(evidence), evidence,
            )
        self.assertEqual(marker.read_bytes(), self.ARCHIVE_PAYLOAD)

    def test_execution_snapshot_descriptor_is_not_writable(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        executable = self.trusted_admin(root)

        def runner(argv, **kwargs):
            probe = self.probe_response(argv)
            if probe is not None:
                return probe
            descriptor = kwargs["pass_fds"][0]
            with self.assertRaises(OSError):
                os.write(descriptor, b"replacement")
            self.assertEqual(argv[1], "-S")
            self.assertEqual(Path(argv[2]).read_bytes(), executable.read_bytes())
            self.write_admin_output(argv)
            return subprocess.CompletedProcess(argv, 0, "Complete", "")

        adapter = AdminMigrationAdapter(
            admin_executable=str(executable),
            argv_factory=hindsight_admin_argv,
            runner=runner,
        )
        self.assertEqual(
            adapter.backup(self.private_archive(), self.ARCHIVE_DIGEST),
            {"completed": True},
        )

    def test_transient_source_mutation_cannot_enter_the_execution_snapshot(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        executable = self.trusted_admin(root)

        def runner(argv, **_kwargs):
            probe = self.probe_response(argv)
            if probe is not None:
                return probe
            raise AssertionError("a mismatched snapshot must not execute")

        adapter = AdminMigrationAdapter(
            admin_executable=str(executable),
            argv_factory=hindsight_admin_argv,
            runner=runner,
        )
        original_pread = os.pread
        reads_from_zero = 0

        def transient_pread(descriptor, size, offset):
            nonlocal reads_from_zero
            payload = original_pread(descriptor, size, offset)
            if (
                offset == 0
                and os.fstat(descriptor).st_ino == executable.stat().st_ino
            ):
                reads_from_zero += 1
                if reads_from_zero == 2 and payload:
                    return bytes([payload[0] ^ 1]) + payload[1:]
            return payload

        with patch.object(os, "pread", side_effect=transient_pread):
            with self.assertRaisesRegex(
                MigrationAdapterError, "snapshot changed"
            ):
                adapter.backup(self.private_archive(), "a" * 64)

    def test_reports_admin_operation_timeout_without_process_output(self):
        def timeout(_argv):
            raise subprocess.TimeoutExpired(
                cmd=["hindsight-admin"], timeout=300, output="private output"
            )

        adapter, _ = self.make_admin(timeout)
        with self.assertRaisesRegex(MigrationAdapterError, "operation timed out"):
            adapter.backup(self.private_archive(), "a" * 64)

    def test_rejects_relative_symlink_untrusted_and_unknown_version_executables(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        executable = self.trusted_admin(root)
        runner = self.versioned_runner([])
        with self.assertRaisesRegex(MigrationAdapterError, "absolute"):
            AdminMigrationAdapter(
                admin_executable="hindsight-admin", argv_factory=hindsight_admin_argv,
                runner=runner,
            )
        symlink = root / "admin-link"
        symlink.symlink_to(executable)
        with self.assertRaisesRegex(MigrationAdapterError, "symlink"):
            AdminMigrationAdapter(
                admin_executable=str(symlink), argv_factory=hindsight_admin_argv,
                runner=runner,
            )
        executable.chmod(0o722)
        with self.assertRaisesRegex(MigrationAdapterError, "writable"):
            AdminMigrationAdapter(
                admin_executable=str(executable), argv_factory=hindsight_admin_argv,
                runner=runner,
            )
        executable.chmod(0o700)
        with self.assertRaisesRegex(MigrationAdapterError, "unsupported"):
            AdminMigrationAdapter(
                admin_executable=str(executable), argv_factory=hindsight_admin_argv,
                runner=self.versioned_runner([], version="0.9.0"),
            )

    def test_mutation_apply_adapter_imports_the_digest_selected_archive(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        approved_archive = root / "approved-bank.zip"
        approved_payload = b"approved migration archive"
        approved_archive.write_bytes(approved_payload)
        approved_archive.chmod(0o600)
        archive_digest = hashlib.sha256(approved_payload).hexdigest()
        evidence = restore_evidence(archive_digest)
        rollback_digest = "f" * 64
        rollback_evidence = restore_evidence(rollback_digest, "8" * 64)
        observed = {}

        def run_admin(argv):
            snapshot = Path(argv[5])
            observed.update({
                "path": snapshot,
                "payload": snapshot.read_bytes(),
                "mode": snapshot.stat().st_mode & 0o777,
            })
            return {"returncode": 0, "stdout": "Import complete"}

        admin, _ = self.make_admin(run_admin)
        data_plane = FakeAdapter(
            endpoint={"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"},
        )
        attested_actions = []

        def attest_external(action, mutation):
            mutation()
            attested_actions.append(action)

        data_plane.attest_external_action = attest_external
        adapter = MigrationApplyAdapter(
            data_plane=data_plane, admin=admin,
            archives={archive_digest: str(approved_archive)},
            restore_evidence={archive_digest: evidence},
            rollback_archive=str(root / "pre-state-backup.zip"),
            rollback_archive_digest=rollback_digest,
            rollback_restore_evidence_digest=digest(rollback_evidence),
            archive_verifier=lambda _path, _digest: True,
        )
        action = Action(
            "migrate", "migrate_bank",
            {
                "artifact_digest": "b" * 64,
                "archive_digest": archive_digest,
                "restore_evidence_digest": digest(evidence),
                "source_bank": SOURCE_BANK_REF,
                "target_bank": TARGET_BANK_REF,
            },
        )

        adapter.apply_action(action)

        self.assertEqual(admin.calls, [{
            "operation": "import-bank",
            "archive_digest": archive_digest,
            "bank_id": "engineering",
        }])
        self.assertNotEqual(observed["path"], approved_archive)
        self.assertRegex(str(observed["path"]), r"^/dev/fd/[0-9]+$")
        self.assertEqual(observed["payload"], approved_payload)
        self.assertEqual(observed["mode"], 0o400)
        self.assertFalse(observed["path"].exists())
        self.assertEqual(attested_actions, [action])
        self.assertNotIn("migrate_bank", [call["method"] for call in data_plane.calls])
        missing = MigrationApplyAdapter(
            data_plane=data_plane, admin=admin, archives={}, restore_evidence={},
            rollback_archive=str(root / "pre-state-backup.zip"),
            rollback_archive_digest=rollback_digest,
            rollback_restore_evidence_digest=digest(rollback_evidence),
            archive_verifier=lambda _path, _digest: True,
        )
        with self.assertRaisesRegex(MigrationAdapterError, "unavailable"):
            missing.apply_action(action)

        unverified = MigrationApplyAdapter(
            data_plane=data_plane, admin=admin,
            archives={archive_digest: str(approved_archive)},
            restore_evidence={archive_digest: evidence},
            rollback_archive=str(root / "pre-state-backup.zip"),
            rollback_archive_digest=rollback_digest,
            rollback_restore_evidence_digest=digest(rollback_evidence),
            archive_verifier=lambda _path, _digest: False,
        )
        with self.assertRaisesRegex(MigrationAdapterError, "archive digest"):
            unverified.apply_action(action)

    def test_migration_import_preflight_requires_closed_bank_id_and_attestation(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        archive = root / "approved.zip"
        archive.write_bytes(self.ARCHIVE_PAYLOAD)
        archive.chmod(0o600)
        evidence = restore_evidence(self.ARCHIVE_DIGEST)
        admin, _ = self.make_admin(
            lambda _argv: {"returncode": 0, "stdout": "Complete"}
        )
        data_plane = FakeAdapter(endpoint={
            "profile_id": "core", "scheme": "http", "host": "127.0.0.1",
            "port": 7979, "tenant": "default",
        })
        adapter = MigrationApplyAdapter(
            data_plane=data_plane,
            admin=admin,
            archives={self.ARCHIVE_DIGEST: str(archive)},
            restore_evidence={self.ARCHIVE_DIGEST: evidence},
            rollback_archive=str(root / "rollback.zip"),
            rollback_archive_digest="f" * 64,
            rollback_restore_evidence_digest="e" * 64,
            archive_verifier=lambda _path, _digest: True,
        )
        details = {
            "archive_digest": self.ARCHIVE_DIGEST,
            "restore_evidence_digest": digest(evidence),
            "target_bank": TARGET_BANK_REF,
        }
        action = Action("migrate", "migrate_bank", details)
        data_plane.attest_external_action = None
        with self.assertRaisesRegex(MigrationAdapterError, "preflight"):
            adapter.preflight_action(action)

        data_plane.attest_external_action = lambda _action, mutation: mutation()
        for bank_id in ("", "../engineering", "engineering/other"):
            invalid = Action(
                "migrate", "migrate_bank",
                {**details, "target_bank": {**TARGET_BANK_REF, "bank_id": bank_id}},
            )
            with self.subTest(bank_id=bank_id), self.assertRaisesRegex(
                MigrationAdapterError, "preflight"
            ):
                adapter.preflight_action(invalid)
        adapter.preflight_action(action)

    def test_rollback_parent_open_failures_are_translated(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        admin, _ = self.make_admin(
            lambda _argv: {"returncode": 0, "stdout": "Complete"}
        )
        archive_digest = "f" * 64
        evidence_digest = "e" * 64
        adapter = MigrationApplyAdapter(
            data_plane=FakeAdapter(endpoint={
                "profile_id": "core", "scheme": "http",
                "host": "127.0.0.1", "port": 7979,
                "tenant": "default",
            }),
            admin=admin,
            archives={},
            restore_evidence={},
            rollback_archive=str(root / "rollback.zip"),
            rollback_archive_digest=archive_digest,
            rollback_restore_evidence_digest=evidence_digest,
            archive_verifier=lambda _path, _digest: True,
            restore_lock_dir=str(root / "locks"),
        )
        rollback = RollbackBundle(
            "rollback", "a" * 64, ("restore",), "b" * 64,
            "c" * 64, "d" * 64, "9" * 64,
            archive_digest, evidence_digest,
        )
        with patch(
            "hindsight_memory_control_plane.migration_adapter.open_trusted_parent",
            side_effect=OSError("unavailable"),
        ):
            with self.assertRaisesRegex(
                MigrationAdapterError, "lock is unavailable"
            ):
                with adapter._restore_guard(rollback):
                    pass
            with self.assertRaisesRegex(
                MigrationAdapterError, "receipt is invalid"
            ):
                adapter._read_restore_receipt(rollback)
            with patch.object(
                adapter, "_read_restore_receipt", return_value=None
            ):
                with self.assertRaisesRegex(
                    MigrationAdapterError, "could not be persisted"
                ):
                    adapter._write_restore_receipt(rollback, "authorized")

    def test_rollback_bundle_validates_restore_evidence_before_side_effects(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        admin, _ = self.make_admin(
            lambda _argv: {"returncode": 0, "stdout": "Complete"}
        )
        data_plane = FakeAdapter(endpoint={
            "profile_id": "core", "scheme": "http",
            "host": "127.0.0.1", "port": 7979, "tenant": "default",
        })
        adapter = MigrationApplyAdapter(
            data_plane=data_plane,
            admin=admin,
            archives={},
            restore_evidence={},
            rollback_archive=str(root / "rollback.zip"),
            rollback_archive_digest="f" * 64,
            rollback_restore_evidence_digest="e" * 64,
            archive_verifier=lambda _path, _digest: True,
        )

        with (
            patch.object(admin, "backup") as backup,
            patch.object(data_plane, "create_rollback_bundle") as create_bundle,
            self.assertRaisesRegex(MigrationAdapterError, "evidence is required"),
        ):
            adapter.create_rollback_bundle("a" * 64, ("restore",))

        backup.assert_not_called()
        create_bundle.assert_not_called()

    def test_migration_restore_does_not_replay_indeterminate_admin_mutation(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        rollback_archive = root / "rollback.zip"
        payload = b"rollback archive"
        rollback_archive.write_bytes(payload)
        rollback_archive.chmod(0o600)
        archive_digest = hashlib.sha256(payload).hexdigest()
        evidence = restore_evidence(archive_digest)
        admin, _ = self.make_admin(
            lambda _argv: {"returncode": 0, "stdout": "Restore complete"}
        )
        data_plane = FakeAdapter(
            endpoint={"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"},
        )

        def make_apply_adapter():
            return MigrationApplyAdapter(
                data_plane=data_plane,
                admin=admin,
                archives={},
                restore_evidence={archive_digest: evidence},
                rollback_archive=str(rollback_archive),
                rollback_archive_digest=archive_digest,
                rollback_restore_evidence_digest=digest(evidence),
                archive_verifier=lambda _path, _digest: True,
            )

        adapter = make_apply_adapter()
        rollback = adapter.create_rollback_bundle("a" * 64, ("restore",))
        data_plane.state["changed"] = True

        with patch.object(
            admin,
            "restore",
            side_effect=MigrationAdapterError("simulated crash"),
        ) as restore:
            with self.assertRaisesRegex(MigrationAdapterError, "simulated crash"):
                adapter.restore(rollback)
            self.assertEqual(
                adapter._read_restore_receipt(rollback)["phase"], "admin_started"
            )

            retry = make_apply_adapter()
            with self.assertRaisesRegex(
                MigrationAdapterError, "outcome is indeterminate"
            ):
                retry.restore(rollback)

        self.assertEqual(restore.call_count, 1)
        self.assertEqual(
            retry._read_restore_receipt(rollback)["phase"], "admin_started"
        )

    def test_migration_restore_treats_admin_receipt_crash_as_indeterminate(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        rollback_archive = root / "rollback.zip"
        payload = b"rollback archive"
        rollback_archive.write_bytes(payload)
        rollback_archive.chmod(0o600)
        archive_digest = hashlib.sha256(payload).hexdigest()
        evidence = restore_evidence(archive_digest)
        data_plane = FakeAdapter(
            endpoint={"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"},
        )

        def run_admin(_argv):
            data_plane.state = {}
            return {"returncode": 0, "stdout": "Restore complete"}

        admin, _ = self.make_admin(run_admin)

        def make_apply_adapter():
            return MigrationApplyAdapter(
                data_plane=data_plane,
                admin=admin,
                archives={},
                restore_evidence={archive_digest: evidence},
                rollback_archive=str(rollback_archive),
                rollback_archive_digest=archive_digest,
                rollback_restore_evidence_digest=digest(evidence),
                archive_verifier=lambda _path, _digest: True,
            )

        adapter = make_apply_adapter()
        rollback = adapter.create_rollback_bundle("a" * 64, ("restore",))
        data_plane.state["changed"] = True
        original_write = adapter._write_restore_receipt

        def crash_before_admin_receipt(value, phase):
            if phase == "admin_restored":
                raise MigrationAdapterError("simulated receipt crash")
            return original_write(value, phase)

        with patch.object(
            adapter,
            "_write_restore_receipt",
            side_effect=crash_before_admin_receipt,
        ), self.assertRaisesRegex(MigrationAdapterError, "simulated receipt crash"):
            adapter.restore(rollback)
        self.assertEqual(
            adapter._read_restore_receipt(rollback)["phase"], "admin_started"
        )

        retry = make_apply_adapter()
        with self.assertRaisesRegex(
            MigrationAdapterError, "outcome is indeterminate"
        ):
            retry.restore(rollback)

        self.assertEqual(
            retry._read_restore_receipt(rollback)["phase"], "admin_started"
        )
        self.assertEqual(
            [call["operation"] for call in admin.calls].count("restore"), 1
        )

    def test_restore_artifact_names_are_fixed_size(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        rollback_archive = root / (("r" * 220) + ".zip")
        payload = b"rollback archive"
        rollback_archive.write_bytes(payload)
        rollback_archive.chmod(0o600)
        archive_digest = hashlib.sha256(payload).hexdigest()
        evidence = restore_evidence(archive_digest)
        admin, _ = self.make_admin(
            lambda _argv: {"returncode": 0, "stdout": "Restore complete"}
        )
        data_plane = FakeAdapter(
            endpoint={"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"},
        )
        adapter = MigrationApplyAdapter(
            data_plane=data_plane,
            admin=admin,
            archives={},
            restore_evidence={archive_digest: evidence},
            rollback_archive=str(rollback_archive),
            rollback_archive_digest=archive_digest,
            rollback_restore_evidence_digest=digest(evidence),
            archive_verifier=lambda _path, _digest: True,
        )
        rollback = adapter.create_rollback_bundle("a" * 64, ("restore",))

        self.assertEqual(len(adapter._restore_receipt_name(rollback)), 88)
        with adapter._restore_guard(rollback):
            adapter._write_restore_receipt(rollback, "authorized")

    def test_restore_guard_serializes_distinct_bundles_for_one_endpoint(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        rollback_archive = root / "rollback.zip"
        payload = b"rollback archive"
        rollback_archive.write_bytes(payload)
        rollback_archive.chmod(0o600)
        archive_digest = hashlib.sha256(payload).hexdigest()
        evidence = restore_evidence(archive_digest)
        admin, _ = self.make_admin(
            lambda _argv: {"returncode": 0, "stdout": "Restore complete"}
        )
        data_plane = FakeAdapter(
            endpoint={
                "profile_id": "core", "scheme": "http",
                "host": "127.0.0.1", "port": 7979, "tenant": "default",
            },
        )

        def make_adapter():
            return MigrationApplyAdapter(
                data_plane=data_plane, admin=admin, archives={},
                restore_evidence={archive_digest: evidence},
                rollback_archive=str(rollback_archive),
                rollback_archive_digest=archive_digest,
                rollback_restore_evidence_digest=digest(evidence),
                archive_verifier=lambda _path, _digest: True,
            )

        first = make_adapter()
        second = make_adapter()
        rollback = first.create_rollback_bundle("a" * 64, ("restore",))
        distinct = replace(
            rollback, rollback_id="distinct", bundle_digest="f" * 64
        )
        entered = threading.Event()

        def enter_distinct_guard():
            with second._restore_guard(distinct):
                entered.set()

        with first._restore_guard(rollback):
            thread = threading.Thread(target=enter_distinct_guard)
            thread.start()
            self.assertFalse(entered.wait(0.1))
        thread.join(CONCURRENCY_TIMEOUT_SECONDS)
        self.assertFalse(thread.is_alive())
        self.assertTrue(entered.is_set())

    def test_restart_verifies_only_receipted_data_plane_rollback_bundle(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        rollback_archive = root / "rollback.zip"
        payload = b"rollback archive"
        rollback_archive.write_bytes(payload)
        rollback_archive.chmod(0o600)
        archive_digest = hashlib.sha256(payload).hexdigest()
        evidence = restore_evidence(archive_digest)
        admin, _ = self.make_admin(
            lambda _argv: {"returncode": 0, "stdout": "Restore complete"}
        )
        data_plane = FakeAdapter(
            endpoint={
                "profile_id": "core", "scheme": "http",
                "host": "127.0.0.1", "port": 7979,
                "tenant": "default",
            },
        )

        def make_apply_adapter(restore_material=evidence):
            return MigrationApplyAdapter(
                data_plane=data_plane,
                admin=admin,
                archives={},
                restore_evidence={archive_digest: restore_material},
                rollback_archive=str(rollback_archive),
                rollback_archive_digest=archive_digest,
                rollback_restore_evidence_digest=digest(evidence),
                archive_verifier=lambda _path, _digest: True,
            )

        original = make_apply_adapter()
        rollback = original.create_rollback_bundle(
            "a" * 64, ("restore",)
        )
        self.assertEqual(
            original._read_restore_receipt(rollback)["phase"],
            "authorized",
        )

        restarted = make_apply_adapter()
        self.assertTrue(restarted.verify_rollback_bundle(rollback))
        self.assertFalse(
            restarted.verify_rollback_bundle(
                replace(rollback, bundle_digest="f" * 64)
            )
        )
        invalid_evidence = restore_evidence(archive_digest, "9" * 64)
        self.assertFalse(
            make_apply_adapter(invalid_evidence).verify_rollback_bundle(
                rollback
            )
        )

    def test_migration_restore_resumes_persisted_phases_without_repeating_admin(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        rollback_archive = root / "rollback.zip"
        payload = b"rollback archive"
        rollback_archive.write_bytes(payload)
        rollback_archive.chmod(0o600)
        archive_digest = hashlib.sha256(payload).hexdigest()
        evidence = restore_evidence(archive_digest)
        admin_operations = []

        def run_admin(argv):
            admin_operations.append(argv[3])
            return {"returncode": 0, "stdout": "Restore complete"}

        admin, _ = self.make_admin(run_admin)
        data_plane = FakeAdapter(
            endpoint={"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"},
        )
        def make_apply_adapter():
            return MigrationApplyAdapter(
                data_plane=data_plane,
                admin=admin,
                archives={},
                restore_evidence={archive_digest: evidence},
                rollback_archive=str(rollback_archive),
                rollback_archive_digest=archive_digest,
                rollback_restore_evidence_digest=digest(evidence),
                archive_verifier=lambda _path, _digest: True,
            )

        adapter = make_apply_adapter()
        rollback = adapter.create_rollback_bundle("a" * 64, ("restore",))
        data_plane.state["changed"] = True
        data_plane.fail_restore = True

        with self.assertRaisesRegex(AdapterError, "rollback failed"):
            adapter.restore(rollback)

        data_plane.fail_restore = False
        first_retry = make_apply_adapter()
        concurrent_retry = make_apply_adapter()
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        errors = []
        original_restore = data_plane.restore

        def slow_restore(value):
            entered.set()
            if not release.wait(CONCURRENCY_TIMEOUT_SECONDS):
                raise TimeoutError("concurrent restore was not released")
            return original_restore(value)

        def run_restore(target):
            try:
                target.restore(rollback)
            except Exception as error:
                errors.append(error)

        actual_flock = fcntl.flock
        exclusive_attempts = 0
        attempts_lock = threading.Lock()

        def observed_flock(descriptor, operation):
            nonlocal exclusive_attempts
            if operation & fcntl.LOCK_EX:
                with attempts_lock:
                    exclusive_attempts += 1
                    if exclusive_attempts == 2:
                        second_attempted.set()
            return actual_flock(descriptor, operation)

        with (
            patch.object(data_plane, "restore", side_effect=slow_restore),
            patch(
                "hindsight_memory_control_plane.migration_adapter.fcntl.flock",
                side_effect=observed_flock,
            ),
        ):
            first = threading.Thread(target=run_restore, args=(first_retry,))
            second_attempted = threading.Event()
            second = threading.Thread(
                target=run_restore,
                args=(concurrent_retry,),
            )
            first.start()
            self.assertTrue(entered.wait(CONCURRENCY_TIMEOUT_SECONDS))
            second.start()
            self.assertTrue(
                second_attempted.wait(CONCURRENCY_TIMEOUT_SECONDS)
            )
            self.assertTrue(second.is_alive())
            release.set()
            first.join(CONCURRENCY_TIMEOUT_SECONDS)
            second.join(CONCURRENCY_TIMEOUT_SECONDS)
            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())

        first_retry.restore(rollback)

        self.assertEqual(errors, [])
        self.assertEqual(admin_operations, ["restore"])
        self.assertEqual(
            [call["method"] for call in data_plane.calls].count("restore"),
            2,
        )
        with first_retry._restore_guard(rollback):
            first_retry._write_restore_receipt(
                rollback, "admin_restored"
            )
            self.assertEqual(
                first_retry._read_restore_receipt(rollback)["phase"],
                "completed",
            )
        changed_bundle = replace(rollback, bundle_digest="f" * 64)
        with self.assertRaisesRegex(
            MigrationAdapterError, "not bound"
        ):
            concurrent_retry.restore(changed_bundle)

    def test_mutation_apply_adapter_creates_and_uses_admin_rollback(self):
        from hindsight_memory_control_plane.reconcile import build_mutation_plan

        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        incoming_archive = root / "incoming-bank.zip"
        incoming_payload = b"incoming migration archive"
        incoming_archive.write_bytes(incoming_payload)
        incoming_archive.chmod(0o600)
        rollback_archive = root / "pre-state-backup.zip"
        rollback_payload = b"rollback schema archive"
        incoming_digest = hashlib.sha256(incoming_payload).hexdigest()
        rollback_digest = hashlib.sha256(rollback_payload).hexdigest()
        evidence = {
            incoming_digest: restore_evidence(incoming_digest),
            rollback_digest: restore_evidence(rollback_digest),
        }
        operations = []
        def run_admin(argv):
            operations.append(argv[3])
            if argv[3] == "backup":
                Path(argv[4]).write_bytes(rollback_payload)
                Path(argv[4]).chmod(0o600)
            return {"returncode": 0, "stdout": "Complete"}

        admin, _ = self.make_admin(run_admin)
        data_plane = FakeAdapter(
            endpoint={"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"},
        )
        data_plane.fail_postcondition_for = "migrate"
        adapter = MigrationApplyAdapter(
            data_plane=data_plane, admin=admin,
            archives={incoming_digest: str(incoming_archive)},
            restore_evidence=evidence,
            rollback_archive=str(rollback_archive),
            rollback_archive_digest=rollback_digest,
            rollback_restore_evidence_digest=digest(evidence[rollback_digest]),
            archive_verifier=lambda _path, _digest: True,
        )
        base = empty_plan_for({})
        plan = build_mutation_plan(
            base, migration_run_id="run-1",
            migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST,
            rollback_archive_digest=rollback_digest,
            rollback_restore_evidence_digest=digest(evidence[rollback_digest]),
            actions=[mutation_action("migrate", archive_digest=incoming_digest)],
        )
        with tempfile.TemporaryDirectory() as temporary:
            descriptor, _, _ = write_migration_gate(
                Path(temporary), "run-1", MIGRATION_ARTIFACT_DIGEST,
            )
            plan = bind_migration_gate(plan, descriptor)
            rollback = create_rollback_bundle(plan, adapter)
            result = apply_plan(
                plan, adapter, plan.plan_digest,
                {"rollback_bundle": rollback, "migration_gate": descriptor},
            )

        self.assertEqual(result.status, "rolled_back")
        self.assertEqual(operations, ["backup", "import-bank", "restore"])

        retry_archive = root / "retry-pre-state-backup.zip"
        retry_adapter = MigrationApplyAdapter(
            data_plane=data_plane,
            admin=admin,
            archives={incoming_digest: str(incoming_archive)},
            restore_evidence=evidence,
            rollback_archive=str(retry_archive),
            rollback_archive_digest=rollback_digest,
            rollback_restore_evidence_digest=digest(
                evidence[rollback_digest]
            ),
            archive_verifier=lambda _path, _digest: True,
        )
        original_create = data_plane.create_rollback_bundle
        attempts = 0

        def fail_once(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("partial rollback attestation failure")
            return original_create(*args, **kwargs)

        with patch.object(
            data_plane, "create_rollback_bundle", side_effect=fail_once
        ):
            with self.assertRaisesRegex(RuntimeError, "partial"):
                retry_adapter.create_rollback_bundle(
                    plan.plan_digest, ("migrate",)
                )
            retry_adapter.create_rollback_bundle(
                plan.plan_digest, ("migrate",)
            )
        self.assertEqual(operations.count("backup"), 2)

        changed_evidence = dict(evidence)
        changed_evidence[rollback_digest] = restore_evidence(
            rollback_digest, "9" * 64,
        )
        changed_adapter = MigrationApplyAdapter(
            data_plane=FakeAdapter(
                endpoint={
                    "profile_id": "core", "scheme": "http",
                    "host": "127.0.0.1", "port": 7979, "tenant": "default",
                },
            ),
            admin=admin,
            archives={incoming_digest: str(incoming_archive)},
            restore_evidence=changed_evidence,
            rollback_archive=self.private_archive("changed-pre-state-backup.zip"),
            rollback_archive_digest=rollback_digest,
            rollback_restore_evidence_digest=digest(evidence[rollback_digest]),
            archive_verifier=lambda _path, _digest: True,
        )
        with self.assertRaisesRegex(
            MigrationAdapterError, "evidence digest does not match plan"
        ):
            changed_adapter.create_rollback_bundle(
                plan.plan_digest, ("migrate",),
            )

    def test_accepts_only_digest_bound_argv_and_requires_restore_evidence(self):
        calls = []
        archive_digest = self.ARCHIVE_DIGEST
        adapter, _ = self.make_admin(
            lambda argv: calls.append(argv) or {"returncode": 0, "stdout": "Complete"},
        )

        archive = self.private_archive()
        adapter.backup(archive, archive_digest)
        self.assertEqual(calls[0][0], TRUSTED_TEST_PYTHON)
        self.assertEqual(calls[0][1], "-S")
        self.assertRegex(calls[0][2], r"^/dev/fd/[0-9]+$")
        self.assertEqual(calls[0][3], "backup")
        with self.assertRaisesRegex(MigrationAdapterError, "disposable restore evidence"):
            adapter.restore(
                archive,
                archive_digest,
                digest(restore_evidence(archive_digest)),
            )

    def test_subprocess_output_capture_is_bounded(self):
        adapter, _ = self.make_admin(
            lambda argv: {
                "returncode": 0,
                "stdout": "x" * 32,
                "stderr": "",
            }
        )
        adapter.runner = lambda _argv, **_kwargs: {
            "returncode": 0, "stdout": "x" * 32, "stderr": ""
        }
        with (
            patch(
                "hindsight_memory_control_plane.migration_adapter.ADMIN_OUTPUT_MAX_CHARS",
                8,
            ),
            self.assertRaisesRegex(
                MigrationAdapterError, "capture limit"
            ),
        ):
            adapter._invoke(["probe"], timeout=1)

    def test_import_and_restore_verify_raw_archive_bytes_before_admin_execution(self):
        calls = []
        adapter, _ = self.make_admin(
            lambda argv: calls.append(argv)
            or {"returncode": 0, "stdout": "Complete"},
        )
        archive = Path(self.private_archive())
        archive.write_bytes(b"not the approved archive")
        archive.chmod(0o600)
        evidence = restore_evidence(self.ARCHIVE_DIGEST)

        for operation in ("import", "restore"):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(
                    MigrationAdapterError, "input archive verification"
                ):
                    if operation == "import":
                        adapter.import_bank(
                            str(archive),
                            self.ARCHIVE_DIGEST,
                            "engineering",
                            digest(evidence),
                            evidence,
                        )
                    else:
                        adapter.restore(
                            str(archive),
                            self.ARCHIVE_DIGEST,
                            digest(evidence),
                            evidence,
                        )
        self.assertEqual(calls, [])

    def test_failed_output_verification_never_publishes_or_leaks_temporary_data(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        target = root / "rollback.zip"
        target.write_bytes(b"previous verified archive")

        calls = []

        def corrupt_output(argv):
            calls.append(argv)
            Path(argv[4]).write_bytes(b"corrupt output")
            return {"returncode": 0, "stdout": "Complete"}

        adapter, _ = self.make_admin(corrupt_output)
        with self.assertRaisesRegex(MigrationAdapterError, "verification failed"):
            adapter.backup(str(target), self.ARCHIVE_DIGEST)

        self.assertEqual(target.read_bytes(), b"previous verified archive")
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            list(root.glob(".hindsight-admin-output-*")), []
        )

    def test_matching_existing_output_is_idempotent_before_subprocess(self):
        target = Path(self.private_archive("existing-output.zip"))
        target.write_bytes(self.ARCHIVE_PAYLOAD)
        target.chmod(0o400)
        calls = []
        adapter, _ = self.make_admin(
            lambda argv: calls.append(argv)
            or {"returncode": 0, "stdout": "Complete"}
        )

        self.assertEqual(
            adapter.backup(str(target), self.ARCHIVE_DIGEST),
            {"completed": True},
        )
        self.assertEqual(calls, [])
        self.assertEqual(target.read_bytes(), self.ARCHIVE_PAYLOAD)

    def test_output_publication_never_replaces_a_racing_destination(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        target = root / "rollback.zip"
        adapter, _ = self.make_admin(
            lambda _argv: {"returncode": 0, "stdout": "Complete"}
        )
        real_link = os.link

        def race_destination(source, destination, **kwargs):
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=kwargs["dst_dir_fd"],
            )
            try:
                os.write(descriptor, b"concurrent archive")
            finally:
                os.close(descriptor)
            return real_link(source, destination, **kwargs)

        with (
            patch.object(os, "link", side_effect=race_destination),
            self.assertRaisesRegex(MigrationAdapterError, "verification failed"),
        ):
            adapter.backup(str(target), self.ARCHIVE_DIGEST)

        self.assertEqual(target.read_bytes(), b"concurrent archive")
        self.assertEqual(list(root.glob(".hindsight-admin-output-*")), [])

    def test_output_publication_recovers_after_crash_before_destination_link(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        target = root / "rollback.zip"
        calls = []
        adapter, _ = self.make_admin(
            lambda argv: calls.append(argv)
            or {"returncode": 0, "stdout": "Complete", "stderr": ""}
        )
        with (
            patch.object(os, "link", side_effect=OSError("simulated crash")),
            self.assertRaisesRegex(
                MigrationAdapterError, "verification failed"
            ),
        ):
            adapter.backup(str(target), self.ARCHIVE_DIGEST)

        self.assertFalse(target.exists())
        self.assertEqual(
            len(list(root.glob(".hindsight-archive-*.recovery"))), 1
        )
        adapter.backup(str(target), self.ARCHIVE_DIGEST)
        self.assertEqual(target.read_bytes(), self.ARCHIVE_PAYLOAD)
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            list(root.glob(".hindsight-archive-*.recovery")), []
        )

    def test_output_recovery_rejects_unexpected_hardlink_counts(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        target = root / "rollback.zip"
        adapter, _ = self.make_admin(
            lambda _argv: {"returncode": 0, "stdout": "Complete"}
        )
        recovery = root / adapter._archive_recovery_name(
            target, self.ARCHIVE_DIGEST
        )
        recovery.write_bytes(self.ARCHIVE_PAYLOAD)
        recovery.chmod(0o400)
        extra = root / "unexpected-link"
        extra.hardlink_to(recovery)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self.assertRaisesRegex(MigrationAdapterError, "unsafe"):
                adapter._recover_output_archive(
                    directory, target, self.ARCHIVE_DIGEST
                )
        finally:
            os.close(directory)

        target.hardlink_to(recovery)
        second_extra = root / "second-unexpected-link"
        second_extra.hardlink_to(recovery)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self.assertRaisesRegex(
                MigrationAdapterError, "already exists"
            ):
                adapter._recover_output_archive(
                    directory, target, self.ARCHIVE_DIGEST
                )
        finally:
            os.close(directory)

    def test_output_publication_is_serialized_per_destination(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        target = root / "rollback.zip"
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def publish(argv):
            calls.append(list(argv))
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("publication was not released")
            return {"returncode": 0, "stdout": "Complete", "stderr": ""}

        adapter, _ = self.make_admin(publish)
        results = []
        errors = []

        def backup():
            try:
                results.append(
                    adapter.backup(str(target), self.ARCHIVE_DIGEST)
                )
            except BaseException as error:
                errors.append(error)

        first = threading.Thread(target=backup, daemon=True)
        second = threading.Thread(target=backup, daemon=True)
        first.start()
        self.assertTrue(entered.wait(timeout=5))
        second.start()
        second.join(timeout=0.1)
        self.assertTrue(second.is_alive())
        self.assertEqual(len(calls), 1)
        release.set()
        first.join(timeout=5)
        second.join(timeout=5)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results, [{"completed": True}, {"completed": True}])
        self.assertEqual(len(calls), 1)
        self.assertEqual(target.read_bytes(), self.ARCHIVE_PAYLOAD)

    def test_output_publication_rejects_replaced_parent_directory(self):
        workspace = Path(self.enterContext(tempfile.TemporaryDirectory()))
        root = workspace / "archives"
        root.mkdir(mode=0o700)
        displaced = workspace / "displaced"
        target = root / "rollback.zip"

        def replace_parent(argv):
            Path(argv[4]).write_bytes(self.ARCHIVE_PAYLOAD)
            root.rename(displaced)
            root.mkdir(mode=0o700)
            return {"returncode": 0, "stdout": "Complete"}

        adapter, _ = self.make_admin(replace_parent)
        with self.assertRaisesRegex(MigrationAdapterError, "parent changed"):
            adapter.backup(str(target), self.ARCHIVE_DIGEST)

        self.assertFalse(target.exists())
        self.assertEqual(list(displaced.glob(".hindsight-archive-*")), [])

    def test_output_publication_reattests_destination_after_link(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        target = root / "rollback.zip"
        def write_archive(argv):
            Path(argv[4]).write_bytes(self.ARCHIVE_PAYLOAD)
            return {"returncode": 0, "stdout": "Complete"}

        adapter, _ = self.make_admin(write_archive)
        real_link = os.link

        def replace_after_link(source, destination, **kwargs):
            real_link(source, destination, **kwargs)
            directory = kwargs["dst_dir_fd"]
            os.unlink(destination, dir_fd=directory)
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o400,
                dir_fd=directory,
            )
            try:
                os.write(descriptor, b"replacement")
            finally:
                os.close(descriptor)

        with (
            patch.object(os, "link", side_effect=replace_after_link),
            self.assertRaisesRegex(MigrationAdapterError, "identity changed"),
        ):
            adapter.backup(str(target), self.ARCHIVE_DIGEST)
        self.assertEqual(target.read_bytes(), b"replacement")

    def test_interrupted_publication_never_unlinks_replacement_recovery_name(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        target = root / "rollback.zip"
        adapter, _ = self.make_admin(
            lambda _argv: {"returncode": 0, "stdout": "Complete"}
        )

        def replace_recovery(source, _destination, **kwargs):
            directory = kwargs["src_dir_fd"]
            os.unlink(source, dir_fd=directory)
            descriptor = os.open(
                source,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=directory,
            )
            try:
                os.write(descriptor, b"not owned by the invocation")
            finally:
                os.close(descriptor)
            raise KeyboardInterrupt("interrupted publication")

        with (
            patch.object(os, "link", side_effect=replace_recovery),
            self.assertRaisesRegex(KeyboardInterrupt, "interrupted publication"),
        ):
            adapter.backup(str(target), self.ARCHIVE_DIGEST)

        recovery = list(root.glob(".hindsight-archive-*.recovery"))
        self.assertEqual(len(recovery), 1)
        self.assertEqual(recovery[0].read_bytes(), b"not owned by the invocation")
        self.assertFalse(target.exists())

    def test_output_staging_rejects_nonsticky_writable_parent(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        untrusted = root / "shared"
        untrusted.mkdir(mode=0o700)
        untrusted.chmod(0o777)
        adapter, _ = self.make_admin(
            lambda _argv: {"returncode": 0, "stdout": "Complete"}
        )
        with self.assertRaisesRegex(MigrationAdapterError, "untrusted"):
            adapter.backup(
                str(untrusted / "rollback.zip"), self.ARCHIVE_DIGEST
            )
        self.assertEqual(list(untrusted.iterdir()), [])

    def test_rechecks_rollback_archive_immediately_before_restore(self):
        rollback_digest = self.ARCHIVE_DIGEST
        evidence = restore_evidence(rollback_digest)
        checks = iter((True, False))
        calls = []
        admin, _ = self.make_admin(
            lambda argv: calls.append(argv[3])
            or {"returncode": 0, "stdout": "Complete"},
        )
        adapter = MigrationApplyAdapter(
            data_plane=FakeAdapter(
                endpoint={
                    "profile_id": "core", "scheme": "http",
                    "host": "127.0.0.1", "port": 7979, "tenant": "default",
                },
            ),
            admin=admin,
            archives={},
            restore_evidence={rollback_digest: evidence},
            rollback_archive=self.private_archive("pre-state-backup.tar"),
            rollback_archive_digest=rollback_digest,
            rollback_restore_evidence_digest=digest(evidence),
            archive_verifier=lambda _path, _digest: next(checks),
        )
        rollback = adapter.create_rollback_bundle("6" * 64, ())

        with self.assertRaisesRegex(MigrationAdapterError, "archive digest"):
            adapter.restore(rollback)

        self.assertEqual(calls, ["backup"])

    def test_restore_requires_successful_bundle_verification_even_when_created_here(self):
        rollback_digest = self.ARCHIVE_DIGEST
        evidence = restore_evidence(rollback_digest)
        admin, _ = self.make_admin(
            lambda _argv: {"returncode": 0, "stdout": "Complete"}
        )
        data_plane = FakeAdapter(
            endpoint={
                "profile_id": "core", "scheme": "http",
                "host": "127.0.0.1", "port": 7979, "tenant": "default",
            },
            restore_proof_valid=False,
        )
        adapter = MigrationApplyAdapter(
            data_plane=data_plane,
            admin=admin,
            archives={},
            restore_evidence={rollback_digest: evidence},
            rollback_archive=self.private_archive("verify-first.tar"),
            rollback_archive_digest=rollback_digest,
            rollback_restore_evidence_digest=digest(evidence),
            archive_verifier=lambda _path, _digest: True,
        )
        rollback = adapter.create_rollback_bundle("6" * 64, ("action-1",))

        self.assertFalse(adapter.verify_rollback_bundle(rollback))
        with self.assertRaisesRegex(MigrationAdapterError, "not bound"):
            adapter.restore(rollback)

    def test_rejects_unknown_versions_shell_strings_missing_digests_and_bad_argv(self):
        with self.assertRaisesRegex(MigrationAdapterError, "unsupported"):
            self.make_admin(lambda _argv: None, version="0.9.0")
        adapter, _ = self.make_admin(
            lambda _argv: None, argv_factory=lambda *_: "hindsight-admin backup",
        )
        with self.assertRaisesRegex(MigrationAdapterError, "argument vector"):
            adapter.backup(self.private_archive(), "a" * 64)
        adapter, _ = self.make_admin(
            lambda _argv: None,
            argv_factory=lambda executable, op, path, bank: [
                *admin_argv(executable, op, path, bank),
                "--database-url", "secret",
            ],
        )
        with self.assertRaisesRegex(MigrationAdapterError, "argv shape"):
            adapter.backup(self.private_archive(), "a" * 64)
        with self.assertRaisesRegex(MigrationAdapterError, "digest"):
            adapter.backup("/tmp/bank.zip", "")
        with self.assertRaisesRegex(MigrationAdapterError, "bank ID"):
            hindsight_admin_argv("/trusted/hindsight-admin", "import-bank", "/tmp/bank.zip", None)
        with self.assertRaisesRegex(MigrationAdapterError, "bank ID"):
            hindsight_admin_argv("/trusted/hindsight-admin", "export-bank", "/tmp/bank.zip", "bad bank")

    def test_permits_all_four_exact_operations_with_verified_restore_evidence(self):
        calls = []
        artifact = self.ARCHIVE_DIGEST
        evidence = restore_evidence(artifact)
        adapter, _ = self.make_admin(
            lambda argv: calls.append(argv) or {"returncode": 0, "stdout": "Complete"},
        )
        bank_archive = self.private_archive("bank.zip")
        schema_archive = self.private_archive("postgres-bank.zip")
        adapter.export_bank(bank_archive, artifact, "historical-candidate")
        adapter.backup(schema_archive, artifact)
        adapter.import_bank(
            bank_archive, artifact, "engineering", digest(evidence), evidence,
        )
        adapter.restore(
            schema_archive, artifact, digest(evidence), evidence,
        )
        self.assertEqual([argv[3] for argv in calls], ["export-bank", "backup", "import-bank", "restore"])
        self.assertTrue(all(argv[0] == TRUSTED_TEST_PYTHON for argv in calls))
        self.assertTrue(
            all(argv[1] == "-S" and re.fullmatch(r"/dev/fd/[0-9]+", argv[2]) for argv in calls)
        )
        self.assertEqual(
            calls[0][3:7],
            ["export-bank", "--bank", "historical-candidate", "--output"],
        )
        self.assertRegex(
            calls[0][7], r"/\.hindsight-admin-output-[^/]+/archive\.zip$"
        )
        self.assertEqual(calls[1][3], "backup")
        self.assertRegex(
            calls[1][4], r"/\.hindsight-admin-output-[^/]+/archive\.zip$"
        )
        self.assertEqual(calls[1][5:], ["--schema", "public"])
        self.assertEqual(calls[2][3:5], ["import-bank", "--archive"])
        self.assertRegex(
            calls[2][5],
            r"^/dev/fd/[0-9]+$",
        )
        self.assertEqual(calls[2][6:], ["--target-bank", "engineering"])
        self.assertEqual(calls[3][3], "restore")
        self.assertRegex(
            calls[3][4],
            r"^/dev/fd/[0-9]+$",
        )
        self.assertEqual(calls[3][5:], ["--schema", "public", "--yes"])
        changed = {**evidence, "verification_receipt_digest": "8" * 64}
        with self.assertRaisesRegex(MigrationAdapterError, "evidence digest"):
            adapter.import_bank(
                bank_archive, artifact, "engineering", digest(evidence), changed,
            )


class GuardedApplyTest(unittest.TestCase):
    def test_migration_gate_pin_closes_for_bind_and_preflight_interruptions(self):
        from hindsight_memory_control_plane.reconcile import build_mutation_plan

        with tempfile.TemporaryDirectory() as temporary:
            descriptor, _, _ = write_migration_gate(
                Path(temporary), "run-1", MIGRATION_ARTIFACT_DIGEST
            )
            base = empty_plan_for({})
            mutation = build_mutation_plan(
                base,
                migration_run_id="run-1",
                migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST,
                rollback_archive_digest="5" * 64,
                rollback_restore_evidence_digest=digest(
                    restore_evidence("5" * 64)
                ),
                actions=[mutation_action()],
            )
            mutation = bind_migration_gate(mutation, descriptor)

            for seam in ("bind", "preflight"):
                with self.subTest(seam=seam):
                    class Interrupted(FakeAdapter):
                        def bind_apply_plan(self, rollback):
                            super().bind_apply_plan(rollback)
                            if seam == "bind":
                                raise KeyboardInterrupt("bind interrupted")

                        def preflight_action(self, action):
                            if seam == "preflight":
                                raise KeyboardInterrupt("preflight interrupted")
                            return super().preflight_action(action)

                    adapter = Interrupted(
                        endpoint=base.target_endpoint.to_dict()
                    )
                    rollback = create_rollback_bundle(mutation, adapter)

                    class Pin:
                        closed = 0

                        def close(self):
                            self.closed += 1

                    pin = Pin()
                    with (
                        patch.object(
                            reconcile_module,
                            "_pin_migration_gate",
                            return_value=pin,
                        ),
                        self.assertRaisesRegex(
                            KeyboardInterrupt, f"{seam} interrupted"
                        ),
                    ):
                        apply_plan(
                            mutation,
                            adapter,
                            mutation.plan_digest,
                            {
                                "rollback_bundle": rollback,
                                "migration_gate": descriptor,
                            },
                        )
                    self.assertEqual(pin.closed, 1)

    def test_apply_rejects_empty_planned_compatibility_evidence(self):
        base = plan_for({})
        body = base.body()
        body["compatibility"] = []
        plan = replace(
            base, compatibility=(), plan_digest=digest(body)
        )
        adapter = self.adapter()
        rollback = create_rollback_bundle(plan, adapter)
        result = apply_plan(
            plan, adapter, plan.plan_digest, {"rollback_bundle": rollback}
        )
        self.assertEqual(result.reason, "compatibility_not_satisfied")

    def test_provider_compatibility_results_convert_without_losing_state(self):
        result = _compatibility([{
            "provider_id": "provider-1",
            "role": "embedding",
            "state": "blocked_candidate",
            "compatible": False,
            "activatable": False,
            "blocked_by": ["revision_switch_not_approved"],
            "fallback_provider_id": None,
            "placement": "local",
            "artifact_id": "embedding-model",
            "revision": "rev-2",
        }])[0]
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["provider_state"], "blocked_candidate")
        self.assertEqual(result["provider_role"], "embedding")
        self.assertEqual(result["blocked_by"], ("revision_switch_not_approved",))

    def test_provider_compatibility_rejects_exact_and_normalized_contradictions(self):
        contradictory = (
            {
                "provider_id": "provider-1",
                "role": "embedding",
                "state": "current",
                "compatible": True,
                "activatable": True,
                "blocked_by": ["revision_switch_not_approved"],
                "fallback_provider_id": None,
                "placement": "local",
                "artifact_id": "embedding-model",
                "revision": "rev-2",
            },
            {
                "check": "provider:provider-1",
                "provider_id": "provider-1",
                "model_id": "embedding-model",
                "compatible": True,
                "status": "pass",
                "provider_state": "blocked_candidate",
                "provider_role": "embedding",
                "activatable": False,
                "blocked_by": ["revision_switch_not_approved"],
                "placement": "local",
                "revision": "rev-2",
            },
        )
        for result in contradictory:
            with self.subTest(result=result), self.assertRaises(PlanError):
                _compatibility([result])

    def test_apply_satisfaction_rejects_provider_blockers_and_nonactivatable_state(self):
        for result in (
            {
                "check": "provider:provider-1",
                "compatible": True,
                "status": "pass",
                "activatable": False,
            },
            {
                "check": "provider:provider-1",
                "compatible": True,
                "status": "pass",
                "blocked_by": ["revision_switch_not_approved"],
            },
        ):
            with self.subTest(result=result):
                self.assertFalse(_compatibility_satisfied((result,)))

    def test_apply_refuses_unsatisfied_compatibility_results(self):
        base = plan_for({})
        compatibility = ({"check": "provider-contract", "compatible": False, "status": "blocked"},)
        body = base.body()
        body["compatibility"] = [dict(value) for value in compatibility]
        plan = replace(base, compatibility=compatibility, plan_digest=digest(body))
        adapter = self.adapter()
        rollback = create_rollback_bundle(plan, adapter)
        result = apply_plan(
            plan,
            adapter,
            plan.plan_digest,
            {"rollback_bundle": rollback},
        )
        self.assertEqual(result.reason, "compatibility_not_satisfied")
        self.assertNotIn("create_bank", [call["method"] for call in adapter.calls])

    def test_apply_refuses_fresh_blocked_or_malformed_compatibility(self):
        plan = plan_for({})
        for compatibility, reason in (
            (
                [
                    {
                        "check": "provider-contract",
                        "compatible": False,
                        "status": "blocked",
                    }
                ],
                "compatibility_not_satisfied",
            ),
            ({"check": "not-an-array"}, "fresh_compatibility_unavailable"),
            (
                [
                    {
                        "check": "provider-contract",
                        "compatible": True,
                        "private_payload": "forbidden",
                    }
                ],
                "fresh_compatibility_unavailable",
            ),
        ):
            with self.subTest(reason=reason, compatibility=compatibility):
                adapter = self.adapter()
                adapter.compatibility = compatibility
                bundle = create_rollback_bundle(plan, adapter)

                result = apply_plan(
                    plan,
                    adapter,
                    plan.plan_digest,
                    {"rollback_bundle": bundle},
                )

                self.assertEqual(result.reason, reason)
                self.assertNotIn(
                    "create_bank", [call["method"] for call in adapter.calls]
                )

    def test_apply_binds_fresh_compatibility_check_identities(self):
        base = plan_for({})
        compatibility = (
            {
                "check": "provider-contract",
                "provider_id": "provider-1",
                "compatible": True,
                "status": "pass",
            },
        )
        body = base.body()
        body["compatibility"] = [dict(value) for value in compatibility]
        plan = replace(
            base,
            compatibility=compatibility,
            plan_digest=digest(body),
        )
        for fresh in (
            [],
            [
                {
                    "check": "provider-contract",
                    "provider_id": "provider-2",
                    "compatible": True,
                    "status": "pass",
                }
            ],
        ):
            with self.subTest(fresh=fresh):
                adapter = self.adapter()
                adapter.compatibility = fresh
                bundle = create_rollback_bundle(plan, adapter)

                result = apply_plan(
                    plan,
                    adapter,
                    plan.plan_digest,
                    {"rollback_bundle": bundle},
                )

                self.assertEqual(
                    result.reason,
                    "compatibility_not_satisfied"
                    if fresh == [] else "fresh_compatibility_changed",
                )
                self.assertNotIn(
                    "create_bank", [call["method"] for call in adapter.calls]
                )

    def test_apply_refuses_non_mapping_fresh_snapshot(self):
        class MalformedSnapshotAdapter(FakeAdapter):
            def snapshot(self):
                return []

        plan = plan_for({})
        adapter = MalformedSnapshotAdapter(
            endpoint=plan.target_endpoint.to_dict()
        )
        bundle = create_rollback_bundle(plan, adapter)

        result = apply_plan(
            plan,
            adapter,
            plan.plan_digest,
            {"rollback_bundle": bundle},
        )

        self.assertEqual(result.reason, "fresh_state_unavailable")
        self.assertNotIn(
            "create_bank", [call["method"] for call in adapter.calls]
        )

    def test_adapter_attested_stale_prestate_bundle_is_refused_before_mutation(self):
        plan = plan_for({"version": "fresh"})
        adapter = self.adapter(state={"version": "stale"})
        bundle = create_rollback_bundle(plan, adapter)
        adapter.state = {"version": "fresh"}

        result = apply_plan(plan, adapter, plan.plan_digest, {"rollback_bundle": bundle})

        self.assertEqual(result.reason, "rollback_prestate_mismatch")
        self.assertNotIn("create_bank", [call["method"] for call in adapter.calls])

    def test_adapter_attested_stale_endpoint_bundle_is_refused_before_mutation(self):
        plan = plan_for({})
        adapter = self.adapter()
        adapter.endpoint = EndpointIdentity("core", "http", "127.0.0.1", 7980, "default")
        bundle = create_rollback_bundle(plan, adapter)
        adapter.endpoint = plan.target_endpoint

        result = apply_plan(plan, adapter, plan.plan_digest, {"rollback_bundle": bundle})

        self.assertEqual(result.reason, "rollback_endpoint_mismatch")
        self.assertNotIn("create_bank", [call["method"] for call in adapter.calls])

    def test_fake_applies_digest_verified_migration_only_with_matching_gate(self):
        from hindsight_memory_control_plane.reconcile import build_mutation_plan

        with tempfile.TemporaryDirectory() as temporary:
            adapter = self.adapter()
            base = empty_plan_for({})
            mutation = build_mutation_plan(
                base,
                migration_run_id="run-1",
                migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST,
                rollback_archive_digest="5" * 64,
                rollback_restore_evidence_digest=digest(
                    restore_evidence("5" * 64)
                ),
                actions=[mutation_action()],
            )
            descriptor, _, _ = write_migration_gate(Path(temporary), "run-1", MIGRATION_ARTIFACT_DIGEST)
            mutation = bind_migration_gate(mutation, descriptor)
            rollback = create_rollback_bundle(mutation, adapter)
            self.assertEqual(rollback.archive_digest, mutation.rollback_archive_digest)
            self.assertEqual(
                rollback.restore_evidence_digest,
                mutation.rollback_restore_evidence_digest,
            )
            matching = {"rollback_bundle": rollback, "migration_gate": descriptor}

            self.assertEqual(apply_plan(mutation, adapter, mutation.plan_digest, matching).status, "applied")
            self.assertEqual(apply_plan(mutation, adapter, mutation.plan_digest, {"rollback_bundle": rollback}).reason, "migration_gate_required")
            forged = replace(rollback, archive_digest="6" * 64)
            self.assertEqual(
                apply_plan(
                    mutation,
                    adapter,
                    mutation.plan_digest,
                    {"rollback_bundle": forged, "migration_gate": descriptor},
                ).reason,
                "rollback_bundle_mismatch",
            )

    def test_mutation_plan_rejects_ordinary_base_actions_at_construction(self):
        from hindsight_memory_control_plane.reconcile import build_mutation_plan

        base = plan_for({})
        with self.assertRaisesRegex(ApplyError, "base plan.*actions"):
            build_mutation_plan(
                base,
                migration_run_id="run-1",
                migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST,
                rollback_archive_digest="5" * 64,
                rollback_restore_evidence_digest=digest(
                    restore_evidence("5" * 64)
                ),
                actions=[mutation_action()],
            )

    def test_mutation_plan_deserialization_is_closed_and_digest_verified(self):
        from hindsight_memory_control_plane.reconcile import build_mutation_plan, mutation_plan_from_dict
        base = empty_plan_for({})
        mutation = build_mutation_plan(base, migration_run_id="run-1", migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST, rollback_archive_digest="5" * 64, rollback_restore_evidence_digest=digest(restore_evidence("5" * 64)), actions=[
            mutation_action(),
        ])
        self.assertEqual(mutation_plan_from_dict(mutation.to_dict()), mutation)
        unknown = {**mutation.to_dict(), "payload": "forbidden"}
        with self.assertRaisesRegex(ApplyError, "closed"):
            mutation_plan_from_dict(unknown)
        tampered = {**mutation.to_dict(), "migration_run_id": "run-2"}
        with self.assertRaisesRegex(ApplyError, "digest"):
            mutation_plan_from_dict(tampered)

    def test_mutation_plan_digest_binds_trusted_migration_gate_evidence(self):
        from hindsight_memory_control_plane.reconcile import (
            build_mutation_plan,
            mutation_plan_from_dict,
        )

        with tempfile.TemporaryDirectory() as temporary:
            base = empty_plan_for({})
            unbound = build_mutation_plan(
                base, migration_run_id="run-1",
                migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST,
                rollback_archive_digest="5" * 64,
                rollback_restore_evidence_digest=digest(restore_evidence("5" * 64)),
                actions=[mutation_action()],
            )
            descriptor, _, _ = write_migration_gate(
                Path(temporary), "run-1", MIGRATION_ARTIFACT_DIGEST
            )
            bound = bind_migration_gate(unbound, descriptor)

            self.assertNotEqual(unbound.plan_digest, bound.plan_digest)
            self.assertEqual(mutation_plan_from_dict(bound.to_dict()), bound)
            self.assertEqual(
                bound.migration_gate_evidence["proposal_log_digest"],
                descriptor.proposal_log_digest,
            )

    def test_mutation_action_id_rejects_oversized_identifier(self):
        from hindsight_memory_control_plane.reconcile import build_mutation_plan
        base = empty_plan_for({})
        with self.assertRaisesRegex(ApplyError, "action id"):
            build_mutation_plan(base, migration_run_id="run-1", migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST, rollback_archive_digest="5" * 64, rollback_restore_evidence_digest=digest(restore_evidence("5" * 64)), actions=[
                {"id": "a" * 129, "kind": "migrate_bank", "artifact_digest": base.artifact_digest, "archive_digest": "4" * 64},
            ])

    def test_mutation_actions_require_plan_bound_source_and_target_banks(self):
        from hindsight_memory_control_plane.reconcile import build_mutation_plan

        base = empty_plan_for({})
        for missing in ("source_bank", "target_bank"):
            with self.subTest(missing=missing):
                action = mutation_action()
                del action[missing]
                with self.assertRaisesRegex(ApplyError, "source and target bank"):
                    build_mutation_plan(
                        base,
                        migration_run_id="run-1",
                        migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST,
                        rollback_archive_digest="5" * 64,
                        rollback_restore_evidence_digest=digest(
                            restore_evidence("5" * 64)
                        ),
                        actions=[action],
                    )

        wrong_profile = mutation_action()
        wrong_profile["target_bank"] = {
            "profile_id": "other", "bank_id": "engineering",
        }
        with self.assertRaisesRegex(ApplyError, "target profile"):
            build_mutation_plan(
                base,
                migration_run_id="run-1",
                migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST,
                rollback_archive_digest="5" * 64,
                rollback_restore_evidence_digest=digest(
                    restore_evidence("5" * 64)
                ),
                actions=[wrong_profile],
            )

        endpoint_bound = mutation_action()
        endpoint = base.target_endpoint.to_dict()
        endpoint_bound["source_bank"] = {
            **endpoint_bound["source_bank"],
            "endpoint": endpoint,
        }
        endpoint_bound["target_bank"] = {
            **endpoint_bound["target_bank"],
            "endpoint": endpoint,
        }
        mutation = build_mutation_plan(
            base,
            migration_run_id="run-1",
            migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST,
            rollback_archive_digest="5" * 64,
            rollback_restore_evidence_digest=digest(
                restore_evidence("5" * 64)
            ),
            actions=[endpoint_bound],
        )
        self.assertEqual(
            mutation.actions[0].details["source_bank"]["endpoint"],
            endpoint,
        )

        logical_collision = mutation_action()
        logical_collision["target_bank"] = {
            **logical_collision["source_bank"],
            "endpoint": endpoint,
        }
        with self.assertRaisesRegex(ApplyError, "must be distinct"):
            build_mutation_plan(
                base,
                migration_run_id="run-1",
                migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST,
                rollback_archive_digest="5" * 64,
                rollback_restore_evidence_digest=digest(
                    restore_evidence("5" * 64)
                ),
                actions=[logical_collision],
            )

        wrong_endpoint = {
            **endpoint_bound,
            "source_bank": {
                **endpoint_bound["source_bank"],
                "endpoint": {**endpoint, "port": endpoint["port"] + 1},
            },
        }
        with self.assertRaisesRegex(ApplyError, "target endpoint"):
            build_mutation_plan(
                base,
                migration_run_id="run-1",
                migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST,
                rollback_archive_digest="5" * 64,
                rollback_restore_evidence_digest=digest(
                    restore_evidence("5" * 64)
                ),
                actions=[wrong_endpoint],
            )

        missing_evidence = mutation_action()
        del missing_evidence["restore_evidence_digest"]
        with self.assertRaisesRegex(ApplyError, "restore evidence digest"):
            build_mutation_plan(
                base,
                migration_run_id="run-1",
                migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST,
                rollback_archive_digest="5" * 64,
                rollback_restore_evidence_digest=digest(
                    restore_evidence("5" * 64)
                ),
                actions=[missing_evidence],
            )

        wrong_artifact = mutation_action()
        wrong_artifact["artifact_digest"] = "2" * 64
        with self.assertRaisesRegex(ApplyError, "migration artifact"):
            build_mutation_plan(
                base,
                migration_run_id="run-1",
                migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST,
                rollback_archive_digest="5" * 64,
                rollback_restore_evidence_digest=digest(
                    restore_evidence("5" * 64)
                ),
                actions=[wrong_artifact],
            )

        rollback_collision = mutation_action(archive_digest="5" * 64)
        with self.assertRaisesRegex(ApplyError, "rollback archive"):
            build_mutation_plan(
                base,
                migration_run_id="run-1",
                migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST,
                rollback_archive_digest="5" * 64,
                rollback_restore_evidence_digest=digest(
                    restore_evidence("5" * 64)
                ),
                actions=[rollback_collision],
            )

        evidence_collision = mutation_action()
        evidence_collision["restore_evidence_digest"] = digest(
            restore_evidence("5" * 64)
        )
        with self.assertRaisesRegex(ApplyError, "rollback evidence"):
            build_mutation_plan(
                base,
                migration_run_id="run-1",
                migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST,
                rollback_archive_digest="5" * 64,
                rollback_restore_evidence_digest=digest(
                    restore_evidence("5" * 64)
                ),
                actions=[evidence_collision],
            )

    def test_mutation_action_id_rejects_payload_like_identifier(self):
        from hindsight_memory_control_plane.reconcile import build_mutation_plan
        base = empty_plan_for({})
        with self.assertRaisesRegex(ApplyError, "action id"):
            build_mutation_plan(base, migration_run_id="run-1", migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST, rollback_archive_digest="5" * 64, rollback_restore_evidence_digest=digest(restore_evidence("5" * 64)), actions=[
                {"id": "payload={secret}", "kind": "migrate_bank", "artifact_digest": base.artifact_digest, "archive_digest": "4" * 64},
            ])

    def test_mutation_action_id_rejects_control_characters(self):
        from hindsight_memory_control_plane.reconcile import build_mutation_plan
        base = empty_plan_for({})
        with self.assertRaisesRegex(ApplyError, "action id"):
            build_mutation_plan(base, migration_run_id="run-1", migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST, rollback_archive_digest="5" * 64, rollback_restore_evidence_digest=digest(restore_evidence("5" * 64)), actions=[
                {"id": "migration\nprivate", "kind": "migrate_bank", "artifact_digest": base.artifact_digest, "archive_digest": "4" * 64},
            ])

    def test_mutation_bank_id_accepts_colons_and_rejects_path_separators(self):
        from hindsight_memory_control_plane.reconcile import build_mutation_plan
        base = empty_plan_for({})
        action = mutation_action()
        action["target_bank"] = {
            "profile_id": "core", "bank_id": "engineering:archive",
        }
        mutation = build_mutation_plan(
            base, migration_run_id="run-1",
            migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST,
            rollback_archive_digest="5" * 64,
            rollback_restore_evidence_digest=digest(restore_evidence("5" * 64)),
            actions=[action],
        )
        self.assertEqual(
            mutation.actions[0].details["target_bank"]["bank_id"],
            "engineering:archive",
        )
        action["target_bank"]["bank_id"] = "engineering/archive"
        with self.assertRaisesRegex(ApplyError, "bank reference is invalid"):
            build_mutation_plan(
                base, migration_run_id="run-1",
                migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST,
                rollback_archive_digest="5" * 64,
                rollback_restore_evidence_digest=digest(restore_evidence("5" * 64)),
                actions=[action],
            )

    def test_valid_mutation_action_id_remains_ledger_safe(self):
        from hindsight_memory_control_plane.reconcile import build_mutation_plan
        with tempfile.TemporaryDirectory() as temporary:
            base = empty_plan_for({})
            adapter = self.adapter()
            mutation = build_mutation_plan(base, migration_run_id="run-1", migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST, rollback_archive_digest="5" * 64, rollback_restore_evidence_digest=digest(restore_evidence("5" * 64)), actions=[
                mutation_action("migration-01.safe"),
            ])
            descriptor, _, _ = write_migration_gate(Path(temporary), "run-1", MIGRATION_ARTIFACT_DIGEST)
            mutation = bind_migration_gate(mutation, descriptor)
            rollback = create_rollback_bundle(mutation, adapter)
            gate = {"rollback_bundle": rollback, "migration_gate": descriptor}
            result = apply_plan(mutation, adapter, mutation.plan_digest, gate)
            self.assertEqual(result.status, "applied")
            self.assertEqual(result.ledger, (
                {"action_id": "migration-01.safe", "status": "started"},
                {"action_id": "migration-01.safe", "status": "applied"},
                {"action_id": "migration-01.safe", "status": "verified"},
            ))
    def adapter(self, state=None, **kwargs):
        return FakeAdapter(
            endpoint={"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"},
            state=state or {}, **kwargs,
        )

    def test_noop_plan_does_not_bind_an_apply_transaction(self):
        class NoopAdapter(FakeAdapter):
            def bind_apply_plan(self, _rollback):
                raise AssertionError("no-op plan must not bind")

        plan = empty_plan_for({})
        adapter = NoopAdapter(endpoint=plan.target_endpoint.to_dict())
        bundle = create_rollback_bundle(plan, adapter)

        result = apply_plan(
            plan, adapter, plan.plan_digest, {"rollback_bundle": bundle}
        )

        self.assertEqual(result.status, "applied")
        self.assertEqual(result.applied_action_ids, ())

    def test_apply_executes_actions_from_a_canonical_plan_rebuild(self):
        original = plan_for({})
        observed = []

        class CanonicalAdapter(FakeAdapter):
            def preflight_action(self, action):
                observed.append(action)
                return super().preflight_action(action)

        adapter = CanonicalAdapter(endpoint=original.target_endpoint.to_dict())
        bundle = create_rollback_bundle(original, adapter)
        result = apply_plan(
            original,
            adapter,
            original.plan_digest,
            {"rollback_bundle": bundle},
        )
        self.assertEqual(result.status, "applied")
        self.assertEqual(len(observed), 1)
        self.assertIsNot(observed[0], original.actions[0])

    def test_mutating_plan_refuses_adapter_without_atomic_binding_capability(self):
        class UnboundAdapter(FakeAdapter):
            bind_apply_plan = None

        plan = plan_for({})
        adapter = UnboundAdapter(endpoint=plan.target_endpoint.to_dict())
        bundle = create_rollback_bundle(plan, adapter)

        result = apply_plan(
            plan, adapter, plan.plan_digest, {"rollback_bundle": bundle}
        )

        self.assertEqual(
            (result.status, result.reason),
            ("refused", "apply_plan_binding_unavailable"),
        )
        self.assertEqual(result.applied_action_ids, ())

    def test_post_apply_provider_attestation_drift_blocks_activation(self):
        class DriftAfterApply(FakeAdapter):
            def apply_action(self, action):
                super().apply_action(action)
                self.compatibility = [{
                    "check": "fake-adapter-contract",
                    "compatible": False,
                    "status": "fail",
                }]

        plan = plan_for({})
        adapter = DriftAfterApply(endpoint=plan.target_endpoint.to_dict())
        bundle = create_rollback_bundle(plan, adapter)

        result = apply_plan(
            plan, adapter, plan.plan_digest, {"rollback_bundle": bundle}
        )

        self.assertEqual(result.status, "operator_blocked")
        self.assertFalse(adapter.activation_enabled)

    def test_commit_then_raise_action_is_tracked_before_rollback(self):
        class CommitThenRaise(FakeAdapter):
            def apply_action(self, action):
                self._require_bound_action(action)
                self.state["committed"] = action.id
                raise AdapterError("transport failed after commit")

        plan = plan_for({})
        adapter = CommitThenRaise(endpoint=plan.target_endpoint.to_dict())
        bundle = create_rollback_bundle(plan, adapter)

        result = apply_plan(
            plan, adapter, plan.plan_digest, {"rollback_bundle": bundle}
        )

        self.assertEqual(result.status, "rolled_back")
        self.assertEqual(result.applied_action_ids, ())
        self.assertEqual(
            result.recovery_action_ids, (plan.actions[0].id,)
        )
        self.assertEqual(
            result.ledger[0],
            {"action_id": plan.actions[0].id, "status": "started"},
        )
        self.assertNotIn("committed", adapter.state)

    def test_tree_insertion_is_depth_safe_for_adversarial_valid_chain(self):
        nodes = {}

        def write(_index, node):
            nodes[node["key"]] = dict(node)
            return digest(node)

        def read(_index, key, expected_digest):
            node = dict(nodes[key])
            self.assertEqual(digest(node), expected_digest)
            return node

        root_key = root_digest = None
        with (
            patch.object(ledger_module, "_write_tree_node", side_effect=write),
            patch.object(ledger_module, "_read_tree_node", side_effect=read),
            patch.object(ledger_module, "_tree_priority", side_effect=lambda key: key),
        ):
            for number in range(1100, 0, -1):
                key = f"{number:064x}"
                root_key, root_digest = ledger_module._tree_put(
                    0, root_key, root_digest, key, "f" * 64
                )

        self.assertEqual(root_key, f"{1:064x}")

    def test_schema_six_append_extends_chain_without_rescanning_prefix(self):
        from hindsight_memory_control_plane.ledger import append_record_once

        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        root.chmod(0o700)
        ledger = root / "decisions.jsonl"

        def record(action_id):
            endpoint = {
                "profile_id": "core", "scheme": "http",
                "host": "127.0.0.1", "port": 7979, "tenant": "default",
            }
            bank = {
                "profile_id": "core", "bank_id": "engineering",
                "endpoint": endpoint,
            }
            return {
                "schema_version": 1, "action_id": action_id,
                "correlation_id": action_id, "source_bank": bank,
                "target_bank": {**bank, "bank_id": "archive"},
                "policy_digest": "1" * 64,
                "artifact_digest": "2" * 64, "decision": "apply",
                "reason_code": "APPROVED",
                "timestamp": "2026-07-17T00:00:00Z",
                "reversible_record_id": None,
            }

        self.assertTrue(append_record_once(ledger, record("first")))
        with patch.object(
            ledger_module, "_ledger_chain_digest",
            side_effect=AssertionError("prefix rescan"),
        ):
            self.assertTrue(append_record_once(ledger, record("second")))

    def test_apply_interruption_attempts_emergency_cleanup_and_reraises_original(self):
        class InterruptedApply(FakeAdapter):
            def apply_action(self, action):
                self._require_bound_action(action)
                self.state["committed"] = action.id
                raise KeyboardInterrupt("apply interrupted")

            def restore(self, bundle):
                raise SystemExit("restore interrupted")

        plan = plan_for({})
        adapter = InterruptedApply(endpoint=plan.target_endpoint.to_dict())
        bundle = create_rollback_bundle(plan, adapter)

        with self.assertRaisesRegex(KeyboardInterrupt, "apply interrupted"):
            apply_plan(
                plan, adapter, plan.plan_digest, {"rollback_bundle": bundle}
            )
        self.assertFalse(adapter.activation_enabled)

    def test_apply_interruption_reraises_after_successful_rollback(self):
        class InterruptedApply(FakeAdapter):
            def apply_action(self, action):
                self._require_bound_action(action)
                self.state["committed"] = action.id
                raise KeyboardInterrupt("apply interrupted")

        plan = plan_for({})
        adapter = InterruptedApply(endpoint=plan.target_endpoint.to_dict())
        bundle = create_rollback_bundle(plan, adapter)

        with self.assertRaisesRegex(KeyboardInterrupt, "apply interrupted"):
            apply_plan(
                plan, adapter, plan.plan_digest, {"rollback_bundle": bundle}
            )
        self.assertNotIn("committed", adapter.state)
        self.assertTrue(adapter.activation_enabled)

    def test_post_apply_active_operation_blocks_activation(self):
        class ActiveAfterApply(FakeAdapter):
            def apply_action(self, action):
                super().apply_action(action)
                self.operations = {
                    "idle": False,
                    "active": [{"id": "late-operation"}],
                }

        plan = plan_for({})
        adapter = ActiveAfterApply(endpoint=plan.target_endpoint.to_dict())
        bundle = create_rollback_bundle(plan, adapter)

        result = apply_plan(
            plan, adapter, plan.plan_digest, {"rollback_bundle": bundle}
        )

        self.assertEqual(result.status, "operator_blocked")
        self.assertFalse(adapter.activation_enabled)

    def test_rollback_operations_snapshot_must_be_closed_and_idle(self):
        class BusyAfterRestore(FakeAdapter):
            def restore(self, rollback):
                super().restore(rollback)
                self.operations = {
                    "idle": True,
                    "active": [],
                    "unexpected": "field",
                }

        plan = plan_for({})
        adapter = BusyAfterRestore(endpoint=plan.target_endpoint.to_dict())
        bundle = create_rollback_bundle(plan, adapter)
        adapter.fail_postcondition_for = "01-create"

        result = apply_plan(
            plan, adapter, plan.plan_digest, {"rollback_bundle": bundle}
        )

        self.assertEqual(result.status, "operator_blocked")
        self.assertFalse(adapter.activation_enabled)

    def test_rollback_provider_identity_drift_blocks_activation(self):
        class DriftAfterRestore(FakeAdapter):
            def restore(self, rollback):
                super().restore(rollback)
                self.endpoint = EndpointIdentity(
                    "core", "http", "127.0.0.1", 7980, "default"
                )

        plan = plan_for({})
        adapter = DriftAfterRestore(endpoint=plan.target_endpoint.to_dict())
        bundle = create_rollback_bundle(plan, adapter)
        adapter.fail_postcondition_for = "01-create"

        result = apply_plan(
            plan, adapter, plan.plan_digest, {"rollback_bundle": bundle}
        )

        self.assertEqual(result.status, "operator_blocked")
        self.assertFalse(adapter.activation_enabled)

    def test_requires_exact_approval_and_action_specific_rollback(self):
        adapter = self.adapter()
        plan = plan_for({})
        bundle = create_rollback_bundle(plan, adapter)

        wrong = apply_plan(plan, adapter, "f" * 64, {"rollback_bundle": bundle})
        missing = apply_plan(plan, adapter, plan.plan_digest, {})

        self.assertEqual(wrong.status, "refused")
        self.assertEqual(wrong.reason, "approval_digest_mismatch")
        self.assertEqual(missing.reason, "rollback_bundle_required")

    def test_postcondition_failure_rolls_back_and_rollback_failure_blocks_operator(self):
        adapter = self.adapter()
        plan = plan_for({})
        bundle = create_rollback_bundle(plan, adapter)
        adapter.fail_postcondition_for = "01-create"
        adapter.fail_restore = True

        result = apply_plan(plan, adapter, plan.plan_digest, {"rollback_bundle": bundle})

        self.assertEqual(result.status, "operator_blocked")
        self.assertTrue(result.rollback_attempted)
        self.assertIs(result.activation_enabled, False)
        self.assertFalse(adapter.activation_enabled)

    def test_refuses_fresh_state_operation_endpoint_and_restore_proof_failures(self):
        plan = plan_for({})

        drifted = self.adapter(state={"changed": True})
        drift_bundle = create_rollback_bundle(plan, drifted)
        self.assertEqual(apply_plan(plan, drifted, plan.plan_digest, {"rollback_bundle": drift_bundle}).reason, "live_state_drift")

        busy = self.adapter(operations={"idle": False, "active": [{"id": "op"}]})
        busy_bundle = create_rollback_bundle(plan, busy)
        self.assertEqual(apply_plan(plan, busy, plan.plan_digest, {"rollback_bundle": busy_bundle}).reason, "operations_not_idle")

        endpoint = self.adapter()
        endpoint.endpoint = EndpointIdentity("core", "http", "127.0.0.1", 7980, "default")
        endpoint_bundle = create_rollback_bundle(plan, endpoint)
        self.assertEqual(apply_plan(plan, endpoint, plan.plan_digest, {"rollback_bundle": endpoint_bundle}).reason, "endpoint_identity_drift")

        data_plan = plan_for({"documents": []})
        unproved = self.adapter(state={"documents": []}, restore_proof_valid=False)
        unproved_bundle = create_rollback_bundle(data_plan, unproved)
        self.assertEqual(apply_plan(data_plan, unproved, data_plan.plan_digest, {"rollback_bundle": unproved_bundle}).reason, "disposable_restore_proof_required")

    def test_applies_in_order_and_rolls_back_on_first_failed_postcondition(self):
        actions = (
            Action("01", "create_bank", {"bank": {"profile_id": "core", "bank_id": "engineering"}}),
            Action("02", "reload_profile", {"profile_id": "core", "reason_code": "config_changed"}),
        )
        adapter = self.adapter()
        plan = plan_for({}, *actions)
        bundle = create_rollback_bundle(plan, adapter)
        adapter.fail_postcondition_for = "02"

        result = apply_plan(plan, adapter, plan.plan_digest, {"rollback_bundle": bundle})

        self.assertEqual(result.status, "rolled_back")
        self.assertEqual(result.applied_action_ids, ("01", "02"))
        self.assertEqual(
            [entry["status"] for entry in result.ledger],
            [
                "started", "applied", "verified", "started", "applied",
                "rollback_started", "rollback_succeeded",
            ],
        )

    def test_migration_gate_reads_matching_external_marker_and_proposal(self):
        artifact = "a" * 64
        with tempfile.TemporaryDirectory() as temporary:
            descriptor, _, _ = write_migration_gate(Path(temporary), "run-1", artifact)
            self.assertEqual(parse_migration_gate(descriptor), ("run-1", artifact))

    def test_migration_gate_requires_present_well_formed_files(self):
        artifact = "a" * 64
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_dir = root / "artifacts"
            artifact_dir.mkdir()
            proposal = root / "proposal-log.md"
            proposal.write_text(
                f"## Migration complete\nrun=run-1\nartifact={artifact}\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ApplyError, "completion marker"):
                capture_migration_gate(artifact_dir / "distillation-complete.marker", proposal)

            marker = artifact_dir / "distillation-complete.marker"
            marker.write_text("not a gate\n", encoding="utf-8")
            with self.assertRaisesRegex(ApplyError, "completion marker"):
                capture_migration_gate(marker, proposal)

            marker.write_text(f"run=run-1\nartifact={artifact}\n", encoding="utf-8")
            proposal.write_text("## Migration complete\nrun=run-1\n", encoding="utf-8")
            with self.assertRaisesRegex(ApplyError, "proposal log"):
                capture_migration_gate(marker, proposal)

    def test_migration_gate_rejects_mismatched_run_or_artifact(self):
        artifact = "a" * 64
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor, _, proposal = write_migration_gate(root, "run-1", artifact)
            self.assertEqual(parse_migration_gate(descriptor), ("run-1", artifact))
            proposal.write_text(
                f"## Migration complete\nrun=run-2\nartifact={artifact}\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ApplyError, "changed"):
                parse_migration_gate(descriptor)
            with self.assertRaisesRegex(ApplyError, "do not match"):
                capture_migration_gate(root / "artifacts/distillation-complete.marker", proposal)

            proposal.write_text(
                f"## Migration complete\nrun=run-1\nartifact={'b' * 64}\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ApplyError, "do not match"):
                capture_migration_gate(root / "artifacts/distillation-complete.marker", proposal)

    def test_migration_gate_rejects_symlink_and_non_regular_sources(self):
        artifact = "a" * 64
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_dir = root / "artifacts"
            artifact_dir.mkdir()
            real_marker = root / "real.marker"
            real_marker.write_text(f"run=run-1\nartifact={artifact}\n", encoding="utf-8")
            (artifact_dir / "distillation-complete.marker").symlink_to(real_marker)
            proposal = root / "proposal-log.md"
            proposal.write_text(
                f"## Migration complete\nrun=run-1\nartifact={artifact}\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ApplyError, "symlink"):
                capture_migration_gate(artifact_dir / "distillation-complete.marker", proposal)

            (artifact_dir / "distillation-complete.marker").unlink()
            (artifact_dir / "distillation-complete.marker").mkdir()
            with self.assertRaisesRegex(ApplyError, "regular file"):
                capture_migration_gate(artifact_dir / "distillation-complete.marker", proposal)

    def test_migration_gate_rejects_writable_files_and_ancestors(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, marker, proposal = write_migration_gate(
                root, "run-1", MIGRATION_ARTIFACT_DIGEST,
            )
            marker.chmod(0o666)
            with self.assertRaisesRegex(ApplyError, "group or world writable"):
                capture_migration_gate(marker, proposal)
            marker.chmod(0o600)
            linked_marker = root / "linked-marker"
            linked_marker.hardlink_to(marker)
            with self.assertRaisesRegex(ApplyError, "hard links"):
                capture_migration_gate(linked_marker, proposal)

        with tempfile.TemporaryDirectory() as temporary:
            unsafe = Path(temporary) / "unsafe"
            unsafe.mkdir()
            unsafe.chmod(0o777)
            marker = unsafe / "artifacts" / "distillation-complete.marker"
            marker.parent.mkdir()
            marker.write_text(
                f"run=run-1\nartifact={MIGRATION_ARTIFACT_DIGEST}\n",
                encoding="utf-8",
            )
            proposal = unsafe / "proposal-log.md"
            proposal.write_text(
                f"## Migration complete\nrun=run-1\nartifact={MIGRATION_ARTIFACT_DIGEST}\n",
                encoding="utf-8",
            )
            marker.chmod(0o600)
            proposal.chmod(0o600)
            with self.assertRaisesRegex(ApplyError, "writable ancestor"):
                capture_migration_gate(marker, proposal)

    def test_verified_file_snapshot_binds_bytes_to_descriptor_stream(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "approved-bank.zip"
            approved = b"approved migration archive"
            source.write_bytes(approved)
            source.chmod(0o600)
            expected = hashlib.sha256(approved).hexdigest()

            with verified_file_snapshot(
                source, "migration archive", expected,
            ) as snapshot_value:
                self.assertEqual(
                    os.pread(snapshot_value.fileno(), len(approved), 0),
                    approved,
                )
                self.assertNotIsInstance(snapshot_value, (str, Path))
                self.assertEqual(
                    os.fstat(snapshot_value.fileno()).st_mode & 0o777,
                    0o400,
                )
                source.write_bytes(b"changed after verification")
                self.assertEqual(
                    os.pread(snapshot_value.fileno(), len(approved), 0),
                    approved,
                )

            self.assertTrue(snapshot_value.closed)
            source.write_bytes(approved)
            source.chmod(0o600)
            with self.assertRaisesRegex(OSError, "consumer failure"):
                with verified_file_snapshot(
                    source, "migration archive", expected,
                ) as failed_snapshot_value:
                    raise OSError("consumer failure")
            self.assertTrue(failed_snapshot_value.closed)

            with self.assertRaisesRegex(FileEvidenceError, "absolute"):
                with verified_file_snapshot(
                    "~/approved-bank.zip", "migration archive", expected,
                ):
                    pass

            with self.assertRaisesRegex(FileEvidenceError, "absolute"):
                with verified_file_snapshot(
                    None, "migration archive", expected,
                ):
                    pass

            with self.assertRaisesRegex(FileEvidenceError, "too large"):
                with verified_file_snapshot(
                    source,
                    "migration archive",
                    expected,
                    max_bytes=len(approved) - 1,
                ):
                    pass

    @unittest.skipUnless(sys.platform == "darwin", "macOS ACL contract")
    def test_file_evidence_rejects_allow_acls_on_files_and_ancestors(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / "private"
            parent.mkdir(mode=0o700)
            source = parent / "evidence.zip"
            content = b"approved"
            source.write_bytes(content)
            source.chmod(0o600)
            expected = hashlib.sha256(content).hexdigest()

            subprocess.run(
                ["chmod", "+a", "everyone allow read", str(source)],
                check=True,
            )
            try:
                with self.assertRaisesRegex(FileEvidenceError, "ACL"):
                    with verified_file_snapshot(source, "evidence", expected):
                        pass
            finally:
                subprocess.run(["chmod", "-N", str(source)], check=True)

            subprocess.run(
                ["chmod", "+a", "everyone allow read", str(parent)],
                check=True,
            )
            try:
                with self.assertRaisesRegex(
                    FileEvidenceError, "untrusted or writable ancestor"
                ):
                    with verified_file_snapshot(source, "evidence", expected):
                        pass
            finally:
                subprocess.run(["chmod", "-N", str(parent)], check=True)

    def test_evidence_open_and_restat_remain_bound_to_pinned_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            approved_parent = root / "approved"
            approved_parent.mkdir(mode=0o700)
            source = approved_parent / "evidence.zip"
            approved = b"approved"
            source.write_bytes(approved)
            source.chmod(0o600)
            protected_parent = root / "protected"
            protected_parent.mkdir(mode=0o700)
            (protected_parent / source.name).write_bytes(b"substituted")
            (protected_parent / source.name).chmod(0o600)
            moved_parent = root / "approved-pinned"
            original_open = os.open
            swapped = False

            def swap_parent_before_basename_open(path, *args, **kwargs):
                nonlocal swapped
                if (
                    not swapped
                    and path == source.name
                    and kwargs.get("dir_fd") is not None
                ):
                    approved_parent.rename(moved_parent)
                    approved_parent.symlink_to(
                        protected_parent, target_is_directory=True
                    )
                    swapped = True
                return original_open(path, *args, **kwargs)

            with patch(
                "hindsight_memory_control_plane.file_evidence.os.open",
                side_effect=swap_parent_before_basename_open,
            ):
                value = read_file_evidence(source, "evidence")
            self.assertTrue(swapped)
            self.assertEqual(value, (approved, hashlib.sha256(approved).hexdigest()))

    def test_file_evidence_rejects_invalid_size_limits_before_path_access(self):
        for invalid in (-1, 0, True, 1.5, "1024", None):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(FileEvidenceError, "size limit"):
                    read_file_evidence(
                        None, "bounded evidence", max_bytes=invalid
                    )
                with self.assertRaisesRegex(FileEvidenceError, "size limit"):
                    with verified_file_snapshot(
                        None,
                        "bounded evidence",
                        "a" * 64,
                        max_bytes=invalid,
                    ):
                        pass

    def test_file_evidence_rejects_parent_traversal_before_filesystem_access(self):
        path = Path("/private/tmp/approved/../evidence.json")
        with (
            patch(
                "hindsight_memory_control_plane.file_evidence.os.lstat",
                side_effect=AssertionError("filesystem must not be inspected"),
            ),
            patch(
                "hindsight_memory_control_plane.file_evidence.os.open",
                side_effect=AssertionError("filesystem must not be opened"),
            ),
            self.assertRaisesRegex(FileEvidenceError, "absolute"),
        ):
            read_file_evidence(path, "bounded evidence")

    def test_root_owned_root_symlink_checks_resolved_target_ancestors(self):
        original_lstat = Path.lstat
        original_resolve = Path.resolve
        root_link = Path("/virtual-evidence-root")

        with tempfile.TemporaryDirectory() as temporary:
            unsafe_target = Path(temporary) / "unsafe"
            unsafe_target.mkdir()
            unsafe_target.chmod(0o777)

            def fake_lstat(path):
                if path == root_link:
                    return type(
                        "RootLinkMetadata", (),
                        {"st_mode": stat.S_IFLNK | 0o777, "st_uid": 0},
                    )()
                return original_lstat(path)

            def fake_resolve(path, *args, **kwargs):
                if path == root_link:
                    return unsafe_target
                return original_resolve(path, *args, **kwargs)

            with patch.object(Path, "lstat", fake_lstat), patch.object(
                Path, "resolve", fake_resolve,
            ):
                with self.assertRaisesRegex(
                    FileEvidenceError, "writable ancestor",
                ):
                    reject_symlink_components(
                        root_link / "evidence.json",
                        "evidence",
                        allow_missing=False,
                    )

            trusted_target = Path(temporary) / "trusted"
            trusted_target.mkdir(mode=0o700)
            trusted_evidence = trusted_target / "evidence.json"
            trusted_evidence.write_text("{}", encoding="utf-8")
            trusted_evidence.chmod(0o600)
            requested_evidence = root_link / "evidence.json"

            def trusted_lstat(path):
                if path == root_link:
                    return type(
                        "RootLinkMetadata", (),
                        {"st_mode": stat.S_IFLNK | 0o777, "st_uid": 0},
                    )()
                if path == requested_evidence:
                    return trusted_evidence.lstat()
                return original_lstat(path)

            def trusted_resolve(path, *args, **kwargs):
                if path == root_link:
                    return trusted_target
                return original_resolve(path, *args, **kwargs)

            with patch.object(Path, "lstat", trusted_lstat), patch.object(
                Path, "resolve", trusted_resolve,
            ):
                self.assertIsNone(
                    reject_symlink_components(
                        requested_evidence,
                        "evidence",
                        allow_missing=False,
                    )
                )

    def test_root_symlink_resolution_errors_fail_closed_when_missing_is_allowed(self):
        original_lstat = Path.lstat
        root_link = Path("/virtual-broken-evidence-root")

        def fake_lstat(path):
            if path == root_link:
                return type(
                    "RootLinkMetadata", (),
                    {"st_mode": stat.S_IFLNK | 0o777, "st_uid": 0},
                )()
            return original_lstat(path)

        for error, message in (
            (PermissionError("denied"), "unavailable"),
            (RuntimeError("cycle"), "symlink cycle"),
        ):
            with self.subTest(error=type(error).__name__):
                with patch.object(Path, "lstat", fake_lstat), patch.object(
                    Path, "resolve", side_effect=error,
                ):
                    with self.assertRaisesRegex(FileEvidenceError, message):
                        reject_symlink_components(
                            root_link / "evidence.json",
                            "evidence",
                            allow_missing=True,
                        )

    def test_optional_missing_evidence_does_not_retry_after_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            protected = root / "protected"
            protected.mkdir()
            (protected / "evidence.json").write_text(
                "private", encoding="utf-8"
            )
            missing_parent = root / "missing"
            requested = missing_parent / "evidence.json"

            def disappear_then_link(_path, _label, *, allow_missing):
                self.assertTrue(allow_missing)
                missing_parent.symlink_to(protected, target_is_directory=True)
                return False

            with patch(
                "hindsight_memory_control_plane.file_evidence."
                "_path_has_no_symlink_components",
                side_effect=disappear_then_link,
            ):
                self.assertIsNone(
                    read_file_evidence(
                        requested, "optional evidence", allow_missing=True
                    )
                )

    def test_evidence_rejects_foreign_owned_read_only_ancestor(self):
        original_lstat = Path.lstat
        foreign_ancestor = Path("/virtual-foreign-evidence")

        def fake_lstat(path):
            if path == foreign_ancestor:
                return type(
                    "ForeignDirectoryMetadata", (),
                    {
                        "st_mode": stat.S_IFDIR | 0o755,
                        "st_uid": os.geteuid() + 1,
                    },
                )()
            return original_lstat(path)

        with patch.object(Path, "lstat", fake_lstat):
            with self.assertRaisesRegex(FileEvidenceError, "untrusted"):
                reject_symlink_components(
                    foreign_ancestor / "evidence.json",
                    "evidence",
                    allow_missing=False,
                )

    def test_apply_rechecks_gate_files_and_refuses_absent_or_changed_evidence(self):
        from hindsight_memory_control_plane.reconcile import build_mutation_plan

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = empty_plan_for({})
            adapter = self.adapter()
            mutation = build_mutation_plan(base, migration_run_id="run-1", migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST, rollback_archive_digest="5" * 64, rollback_restore_evidence_digest=digest(restore_evidence("5" * 64)), actions=[
                mutation_action(),
            ])
            descriptor, marker, proposal = write_migration_gate(root, "run-1", MIGRATION_ARTIFACT_DIGEST)
            mutation = bind_migration_gate(mutation, descriptor)
            rollback = create_rollback_bundle(mutation, adapter)
            gate = {"rollback_bundle": rollback, "migration_gate": descriptor}

            proposal.write_text(proposal.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            self.assertEqual(
                apply_plan(mutation, adapter, mutation.plan_digest, gate).reason,
                "migration_gate_mismatch",
            )
            self.assertNotIn("migrate_bank", [call["method"] for call in adapter.calls])

            descriptor, marker, _ = write_migration_gate(root / "second", "run-1", MIGRATION_ARTIFACT_DIGEST)
            marker.unlink()
            missing_gate = {"rollback_bundle": rollback, "migration_gate": descriptor}
            self.assertEqual(
                apply_plan(mutation, adapter, mutation.plan_digest, missing_gate).reason,
                "migration_gate_mismatch",
            )

    def test_apply_rechecks_gate_again_immediately_before_mutation(self):
        from hindsight_memory_control_plane.reconcile import build_mutation_plan

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = empty_plan_for({})
            descriptor, _, proposal = write_migration_gate(root, "run-1", MIGRATION_ARTIFACT_DIGEST)

            class GateChangingAdapter(FakeAdapter):
                def verify_rollback_bundle(self, rollback):
                    verified = super().verify_rollback_bundle(rollback)
                    proposal.write_text(
                        proposal.read_text(encoding="utf-8") + "\n",
                        encoding="utf-8",
                    )
                    return verified

            adapter = GateChangingAdapter(
                endpoint={"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"},
            )
            mutation = build_mutation_plan(base, migration_run_id="run-1", migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST, rollback_archive_digest="5" * 64, rollback_restore_evidence_digest=digest(restore_evidence("5" * 64)), actions=[
                mutation_action(),
            ])
            mutation = bind_migration_gate(mutation, descriptor)
            rollback = create_rollback_bundle(mutation, adapter)
            gate = {"rollback_bundle": rollback, "migration_gate": descriptor}

            result = apply_plan(mutation, adapter, mutation.plan_digest, gate)

            self.assertEqual(result.reason, "migration_gate_mismatch")
            self.assertNotIn("migrate_bank", [call["method"] for call in adapter.calls])

    def test_refuses_an_ordinary_plan_marked_destructive(self):
        adapter = self.adapter()
        plan = plan_for({})
        body = plan.to_dict()
        body.pop("plan_digest")
        body["destructive"] = True
        plan = replace(plan, destructive=True, plan_digest=digest(body))
        result = apply_plan(plan, adapter, plan.plan_digest, {"rollback_bundle": {}})
        self.assertEqual(result.reason, "invalid_or_destructive_plan")

    def test_migration_gate_mismatch_is_refused_through_apply_boundary(self):
        from hindsight_memory_control_plane.reconcile import build_mutation_plan
        with tempfile.TemporaryDirectory() as temporary:
            adapter = self.adapter()
            base = empty_plan_for({})
            mutation = build_mutation_plan(base, migration_run_id="run-1", migration_artifact_digest=MIGRATION_ARTIFACT_DIGEST, rollback_archive_digest="5" * 64, rollback_restore_evidence_digest=digest(restore_evidence("5" * 64)), actions=[
                mutation_action(),
            ])
            descriptor, _, _ = write_migration_gate(
                Path(temporary), "run-1", MIGRATION_ARTIFACT_DIGEST
            )
            mutation = bind_migration_gate(mutation, descriptor)
            rollback = create_rollback_bundle(mutation, adapter)
            gate = {
                "rollback_bundle": rollback,
                "migration_gate": {
                    "export": {"run_id": "run-1", "artifact_digest": base.artifact_digest},
                    "import": {"run_id": "run-2", "artifact_digest": base.artifact_digest},
                },
            }
            self.assertEqual(apply_plan(mutation, adapter, mutation.plan_digest, gate).reason, "migration_gate_mismatch")

    def test_caller_cannot_forge_restore_proof(self):
        adapter = self.adapter(state={"documents": []}, restore_proof_valid=True)
        plan = plan_for({"documents": []})
        bundle = create_rollback_bundle(plan, adapter)
        forged = replace(bundle, restore_proof_digest="f" * 64)
        result = apply_plan(plan, adapter, plan.plan_digest, {"rollback_bundle": forged})
        self.assertEqual(result.reason, "disposable_restore_proof_required")

    def test_any_ordinary_exception_after_mutation_attempts_rollback(self):
        class ExplodingFake(FakeAdapter):
            def apply_action(self, action):
                self.state["partial"] = True
                raise ValueError("private payload must not escape")
        adapter = ExplodingFake(endpoint={"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"})
        plan = plan_for({})
        bundle = create_rollback_bundle(plan, adapter)
        result = apply_plan(plan, adapter, plan.plan_digest, {"rollback_bundle": bundle})
        self.assertEqual(result.status, "rolled_back")
        self.assertEqual(adapter.state, {})
        self.assertNotIn("private payload", result.reason)

    def test_rollback_success_requires_post_restore_snapshot_verification(self):
        class UnverifiableRestore(FakeAdapter):
            def restore(self, bundle):
                super().restore(bundle)
                self.state["unrestored-residue"] = True

        adapter = UnverifiableRestore(
            endpoint={
                "profile_id": "core", "scheme": "http",
                "host": "127.0.0.1", "port": 7979,
                "tenant": "default",
            }
        )
        plan = plan_for({})
        bundle = create_rollback_bundle(plan, adapter)
        adapter.fail_postcondition_for = "01-create"
        result = apply_plan(
            plan, adapter, plan.plan_digest, {"rollback_bundle": bundle}
        )
        self.assertEqual(result.status, "operator_blocked")
        self.assertEqual(result.reason, "rollback_failed")
        self.assertFalse(result.rollback_succeeded)

    def test_rollback_success_requires_restored_operations_to_be_idle(self):
        class BusyAfterRestore(FakeAdapter):
            def restore(self, bundle):
                super().restore(bundle)
                self.operations = {
                    "idle": False,
                    "active": [{"id": "restore-still-running"}],
                }

        adapter = BusyAfterRestore(
            endpoint={
                "profile_id": "core", "scheme": "http",
                "host": "127.0.0.1", "port": 7979,
                "tenant": "default",
            }
        )
        plan = plan_for({})
        bundle = create_rollback_bundle(plan, adapter)
        adapter.fail_postcondition_for = "01-create"

        result = apply_plan(
            plan, adapter, plan.plan_digest, {"rollback_bundle": bundle}
        )

        self.assertEqual(result.status, "operator_blocked")
        self.assertEqual(result.reason, "rollback_failed")
        self.assertFalse(result.rollback_succeeded)
        self.assertFalse(adapter.activation_enabled)

    def test_rollback_and_activation_disable_failure_leave_activation_unknown(self):
        adapter = self.adapter()
        plan = plan_for({})
        bundle = create_rollback_bundle(plan, adapter)
        adapter.fail_postcondition_for = "01-create"
        adapter.fail_restore = True
        adapter.fail_disable_activation = True
        result = apply_plan(plan, adapter, plan.plan_digest, {"rollback_bundle": bundle})
        self.assertEqual(result.status, "operator_blocked")
        self.assertEqual(result.reason, "emergency_disable_failed")
        self.assertIsNone(result.activation_enabled)


if __name__ == "__main__":
    unittest.main()
