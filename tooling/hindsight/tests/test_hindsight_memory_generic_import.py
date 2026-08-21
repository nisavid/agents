from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from hindsight_memory_control_plane.adapters import AdapterError  # noqa: E402
from hindsight_memory_control_plane.canonical import digest  # noqa: E402
from hindsight_memory_control_plane.generic_import import (  # noqa: E402
    GenericImportError,
    apply_generic_import_plan,
    create_generic_import_plan,
    generic_import_status,
    inspect_import_target,
    verify_generic_import_plan,
    verify_generic_import_receipts,
)
from hindsight_memory_control_plane.importing import (  # noqa: E402
    ImportValidationError,
    import_plan_from_dict,
    inspect_items,
    project_import,
    projection_from_dict,
    record_novelty_review,
)
from hindsight_memory_control_plane.model import (  # noqa: E402
    BankRef,
    EndpointIdentity,
)


TARGET = BankRef("systalyze", "engineering")


def source_record(native_id, content):
    return {
        "source_locator": "/private/tmp/serena/MEMORY.md",
        "source_native_id": native_id,
        "timestamp": "2026-07-01T00:00:00Z",
        "line_start": 1,
        "line_end": 1,
        "content": content,
        "kind": "rule",
        "intended_scope": "repo:agents",
        "relationships": ["repo:agents"],
        "coverage_disposition": "review_pending",
        "coverage_reason": "requires-target-review",
    }


def closeout_receipt():
    body = {
        "schema_version": 1,
        "closeout_plan_digest": "a" * 64,
        "deleted_bank_id": "codex",
        "deleted_count": 5,
        "pre_delete_generation": "systalyze:public:9",
        "post_delete_generation": "systalyze:public:10",
        "remaining_bank_set_digest": "b" * 64,
        "cleanup_status": "completed",
    }
    return {**body, "closeout_receipt_digest": digest(body)}


def backup_evidence():
    return {
        "schema_version": 1,
        "kind": "generic-import-backup",
        "target_bank": TARGET.to_dict(),
        "target_generation": "systalyze:public:10",
        "backup_digest": "c" * 64,
        "restore_tested": True,
    }


class FakeAdapter:
    def __init__(self):
        self.generation = 10
        self.bank_ids = ["engineering"]
        self.documents = {
            "existing": {
                "id": "existing",
                "original_text": "Already present.",
                "tags": ["existing"],
                "document_metadata": {},
                "memory_unit_count": 1,
            }
        }
        self.operations = {}
        self.operation_snapshot = {"idle": True, "active": []}

    def read_migration_generation(self):
        return f"systalyze:public:{self.generation}"

    @staticmethod
    def inventory_identity():
        return {
            "inventory_digest": "e" * 64,
            "artifact_digest": "f" * 64,
        }

    @staticmethod
    def endpoint_identity():
        return EndpointIdentity(
            "systalyze",
            "http",
            "127.0.0.1",
            7979,
            "default",
        )

    def list_replay_bank_ids(self):
        return sorted(self.bank_ids)

    def read_operations(self):
        return self.operation_snapshot

    def list_replay_document_ids(self, bank):
        self.assert_target(bank)
        return sorted(self.documents)

    def read_replay_document(self, bank, document_id):
        self.assert_target(bank)
        return self.documents[document_id]

    def find_replay_document(self, bank, document_id):
        self.assert_target(bank)
        return self.documents.get(document_id)

    def submit_replay_document(self, bank, item):
        self.assert_target(bank)
        operation_id = "00000000-0000-4000-8000-000000000001"
        self.documents[item["document_id"]] = {
            "id": item["document_id"],
            "original_text": item["content"],
            "tags": item["tags"],
            "document_metadata": item["metadata"],
            "retain_params": {"event_date": item["timestamp"]},
            "memory_unit_count": 1,
        }
        self.operations[operation_id] = "completed"
        self.generation += 1
        return {"operation_id": operation_id}

    def submit_generic_import_document(
        self,
        bank,
        item,
        *,
        expected_generation,
        expected_bank_set_digest,
        plan_digest,
        item_digest,
    ):
        before = self.read_migration_generation()
        if (
            expected_generation != before
            or expected_bank_set_digest != digest(sorted(self.bank_ids))
            or not item["document_id"].startswith("generic-import:")
            or item["document_id"] in self.documents
            or item["metadata"]["generic_import_plan_digest"] != plan_digest
            or item["metadata"]["generic_import_item_digest"] != item_digest
            or self.read_migration_generation() != before
        ):
            raise AdapterError("generic import conditional retain conflict")
        return self.submit_replay_document(
            bank,
            {**item, "update_mode": "replace"},
        )

    def read_replay_operation(self, bank, operation_id):
        self.assert_target(bank)
        return {
            "operation_id": operation_id,
            "status": self.operations.get(operation_id, "not_found"),
        }

    def read_generic_import_processing_evidence(self, bank, document_ids):
        self.assert_target(bank)
        documents = [
            {
                "target_document_id": document_id,
                "memory_unit_count":
                    self.documents[document_id]["memory_unit_count"],
                "embedded_memory_unit_count":
                    self.documents[document_id]["memory_unit_count"],
            }
            for document_id in document_ids
        ]
        body = {
            "schema_version": 1,
            "generation_before": self.read_migration_generation(),
            "generation_after": self.read_migration_generation(),
            "documents": documents,
            "representative_recall": {
                "target_document_id": document_ids[0],
                "query_digest": "1" * 64,
                "result_count": 1,
                "result_projection_digest": "2" * 64,
            },
        }
        return {**body, "processing_evidence_digest": digest(body)}

    @staticmethod
    def assert_target(bank):
        if bank != TARGET:
            raise AssertionError("wrong target bank")


class GenericImportControllerTest(unittest.TestCase):
    def setUp(self):
        pending = inspect_items(
            "portable-markdown",
            [
                source_record("duplicate", "Already present."),
                source_record("novel", "New durable guidance."),
            ],
        )
        self.pending_projection = project_import(pending)
        self.adapter = FakeAdapter()
        self.inspection = inspect_import_target(
            self.adapter,
            self.pending_projection,
            TARGET,
        )
        reviewed = []
        for item in pending:
            if item.content == "Already present.":
                reviewed.append(
                    replace(
                        item,
                        coverage_disposition="omitted",
                        coverage_reason=(
                            "exact-target-duplicate:"
                            f"{self.inspection['inspection_digest']}"
                        ),
                    )
                )
            else:
                reviewed.append(
                    record_novelty_review(
                        item,
                        review_evidence_digest="d" * 64,
                    )
                )
        self.projection = project_import(reviewed)

    def test_controller_inspection_is_payload_free_and_generation_bound(self):
        self.assertEqual(
            [finding["status"] for finding in self.inspection["findings"]],
            ["exact_duplicate", "novelty_candidate"],
        )
        encoded = json.dumps(self.inspection)
        self.assertNotIn("Already present.", encoded)
        self.assertNotIn("New durable guidance.", encoded)
        self.assertEqual(
            self.inspection["controller_snapshot"]["target_generation"],
            "systalyze:public:10",
        )

    def test_plan_apply_status_and_verify_are_digest_bound(self):
        plan = create_generic_import_plan(
            self.adapter,
            self.projection,
            self.inspection,
            closeout_receipt(),
            backup_evidence(),
        )
        self.assertEqual(
            verify_generic_import_plan(plan)["plan_digest"],
            plan["plan_digest"],
        )
        self.assertEqual(len(plan["import_plan"]["actions"]), 1)
        with self.assertRaisesRegex(GenericImportError, "exact digest-bound"):
            apply_generic_import_plan(
                self.adapter,
                plan,
                self.projection,
                closeout_receipt(),
                backup_evidence(),
                approval_digest="0" * 64,
                poll_interval_seconds=0,
            )
        checkpoints = []
        receipts = apply_generic_import_plan(
            self.adapter,
            plan,
            self.projection,
            closeout_receipt(),
            backup_evidence(),
            approval_digest=plan["plan_digest"],
            poll_interval_seconds=0,
            receipt_writer=lambda value: checkpoints.append(list(value)),
        )
        self.assertEqual(len(receipts), 1)
        self.assertEqual(len(checkpoints), 1)
        status = generic_import_status(plan, receipts)
        self.assertTrue(status["complete"])
        verification = verify_generic_import_receipts(
            self.adapter,
            plan,
            self.projection,
            receipts,
        )
        self.assertTrue(verification["complete"])
        self.assertEqual(verification["verified_item_count"], 1)

    def test_plan_refuses_open_replay_gate_and_unresolved_review(self):
        self.adapter.bank_ids.append("codex")
        with self.assertRaisesRegex(GenericImportError, "gate is open"):
            create_generic_import_plan(
                self.adapter,
                self.projection,
                self.inspection,
                closeout_receipt(),
                backup_evidence(),
            )
        self.adapter.bank_ids.remove("codex")
        with self.assertRaisesRegex(
            GenericImportError,
            "explicitly omitted|review remains",
        ):
            create_generic_import_plan(
                self.adapter,
                self.pending_projection,
                self.inspection,
                closeout_receipt(),
                backup_evidence(),
            )

    def test_plan_refuses_busy_operations_and_identity_substitution(self):
        self.adapter.operation_snapshot = {
            "idle": False,
            "active": [{"operation_id": "retain-1"}],
        }
        with self.assertRaisesRegex(GenericImportError, "not idle"):
            create_generic_import_plan(
                self.adapter,
                self.projection,
                self.inspection,
                closeout_receipt(),
                backup_evidence(),
            )
        self.adapter.operation_snapshot = {"idle": True, "active": []}
        original = self.adapter.inventory_identity
        self.adapter.inventory_identity = lambda: {
            **original(),
            "inventory_digest": "0" * 64,
        }
        with self.assertRaisesRegex(GenericImportError, "drifted"):
            create_generic_import_plan(
                self.adapter,
                self.projection,
                self.inspection,
                closeout_receipt(),
                backup_evidence(),
            )

    def test_apply_refuses_generation_drift_and_target_collision(self):
        plan = create_generic_import_plan(
            self.adapter,
            self.projection,
            self.inspection,
            closeout_receipt(),
            backup_evidence(),
        )
        self.adapter.generation += 1
        with self.assertRaisesRegex(
            GenericImportError,
            "authority drifted",
        ):
            apply_generic_import_plan(
                self.adapter,
                plan,
                self.projection,
                closeout_receipt(),
                backup_evidence(),
                approval_digest=plan["plan_digest"],
                poll_interval_seconds=0,
            )
        self.adapter.generation -= 1
        target_id = (
            "generic-import:"
            f"{plan['import_plan']['actions'][0]['item_id']}"
        )
        self.adapter.documents[target_id] = {
            "original_text": "collision",
            "tags": [],
            "document_metadata": {},
            "memory_unit_count": 1,
        }
        with self.assertRaisesRegex(GenericImportError, "target collision"):
            apply_generic_import_plan(
                self.adapter,
                plan,
                self.projection,
                closeout_receipt(),
                backup_evidence(),
                approval_digest=plan["plan_digest"],
                poll_interval_seconds=0,
            )

    def test_projection_artifact_round_trips_and_rejects_tampering(self):
        loaded = projection_from_dict(self.projection.to_dict())
        self.assertEqual(loaded.to_dict(), self.projection.to_dict())
        tampered = self.projection.to_dict()
        tampered["items"][0]["content"] = "changed"
        with self.assertRaises(ImportValidationError):
            projection_from_dict(tampered)

        plan = create_generic_import_plan(
            self.adapter,
            self.projection,
            self.inspection,
            closeout_receipt(),
            backup_evidence(),
        )["import_plan"]
        self.assertEqual(import_plan_from_dict(plan).to_dict(), plan)
        tampered_plan = json.loads(json.dumps(plan))
        tampered_plan["actions"][0]["item_digest"] = "0" * 64
        with self.assertRaises(ImportValidationError):
            import_plan_from_dict(tampered_plan)
        extra_key_plan = json.loads(json.dumps(plan))
        extra_key_plan["unexpected"] = True
        with self.assertRaises(ImportValidationError):
            import_plan_from_dict(extra_key_plan)

    def test_resume_revalidates_live_receipt_and_verification_projection(self):
        plan = create_generic_import_plan(
            self.adapter,
            self.projection,
            self.inspection,
            closeout_receipt(),
            backup_evidence(),
        )
        receipts = apply_generic_import_plan(
            self.adapter,
            plan,
            self.projection,
            closeout_receipt(),
            backup_evidence(),
            approval_digest=plan["plan_digest"],
            poll_interval_seconds=0,
        )
        target_id = receipts[0]["target_document_id"]
        self.adapter.documents[target_id]["document_metadata"]["generic_import_scope"] = (
            "repo:tampered"
        )
        with self.assertRaisesRegex(GenericImportError, "receipt target"):
            apply_generic_import_plan(
                self.adapter,
                plan,
                self.projection,
                closeout_receipt(),
                backup_evidence(),
                approval_digest=plan["plan_digest"],
                existing_receipts=receipts,
                poll_interval_seconds=0,
            )
        with self.assertRaisesRegex(
            GenericImportError,
            "processing verification failed",
        ):
            verify_generic_import_receipts(
                self.adapter,
                plan,
                self.projection,
                receipts,
            )

    def test_wait_policy_and_controller_binding_tampering_are_rejected(self):
        plan = create_generic_import_plan(
            self.adapter,
            self.projection,
            self.inspection,
            closeout_receipt(),
            backup_evidence(),
        )
        with self.assertRaisesRegex(GenericImportError, "wait policy"):
            apply_generic_import_plan(
                self.adapter,
                plan,
                self.projection,
                closeout_receipt(),
                backup_evidence(),
                approval_digest=plan["plan_digest"],
                timeout_seconds=float("nan"),
            )
        tampered = json.loads(json.dumps(plan))
        tampered["controller_binding"]["controller_snapshot"][
            "inventory_digest"
        ] = "0" * 64
        body = {
            key: value
            for key, value in tampered.items()
            if key != "plan_digest"
        }
        tampered["plan_digest"] = digest(body)
        with self.assertRaisesRegex(
            GenericImportError,
            "controller plan digest",
        ):
            verify_generic_import_plan(tampered)

    def test_apply_rebuilds_exact_inner_actions_before_mutation(self):
        plan = create_generic_import_plan(
            self.adapter,
            self.projection,
            self.inspection,
            closeout_receipt(),
            backup_evidence(),
        )
        tampered = json.loads(json.dumps(plan))
        tampered["import_plan"]["actions"] = []
        inner_body = {
            key: value
            for key, value in tampered["import_plan"].items()
            if key != "plan_digest"
        }
        tampered["import_plan"]["plan_digest"] = digest(inner_body)
        outer_body = {
            key: value
            for key, value in tampered.items()
            if key != "plan_digest"
        }
        tampered["plan_digest"] = digest(outer_body)
        with self.assertRaisesRegex(
            GenericImportError,
            "actions do not match",
        ):
            apply_generic_import_plan(
                self.adapter,
                tampered,
                self.projection,
                closeout_receipt(),
                backup_evidence(),
                approval_digest=tampered["plan_digest"],
                poll_interval_seconds=0,
            )

    def test_checkpoint_failure_carries_verified_receipt_prefix(self):
        plan = create_generic_import_plan(
            self.adapter,
            self.projection,
            self.inspection,
            closeout_receipt(),
            backup_evidence(),
        )
        with self.assertRaisesRegex(
            GenericImportError,
            "checkpoint failed",
        ) as raised:
            apply_generic_import_plan(
                self.adapter,
                plan,
                self.projection,
                closeout_receipt(),
                backup_evidence(),
                approval_digest=plan["plan_digest"],
                poll_interval_seconds=0,
                receipt_writer=lambda _receipts: (
                    (_ for _ in ()).throw(OSError("disk full"))
                ),
            )
        self.assertEqual(len(raised.exception.receipt_prefix), 1)

    def test_all_omitted_plan_verifies_without_processing_evidence(self):
        omitted = project_import(
            [
                replace(
                    item,
                    coverage_disposition="omitted",
                    coverage_reason="operator-reviewed-omission",
                )
                for item in self.pending_projection.items
            ]
        )
        plan = create_generic_import_plan(
            self.adapter,
            omitted,
            self.inspection,
            closeout_receipt(),
            backup_evidence(),
        )
        receipts = apply_generic_import_plan(
            self.adapter,
            plan,
            omitted,
            closeout_receipt(),
            backup_evidence(),
            approval_digest=plan["plan_digest"],
            poll_interval_seconds=0,
        )
        self.assertEqual(receipts, [])
        verification = verify_generic_import_receipts(
            self.adapter,
            plan,
            omitted,
            receipts,
        )
        self.assertTrue(verification["complete"])
        self.assertEqual(verification["verified_item_count"], 0)


if __name__ == "__main__":
    unittest.main()
