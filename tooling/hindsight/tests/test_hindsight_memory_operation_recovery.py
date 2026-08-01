from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from hindsight_memory_control_plane.canonical import digest  # noqa: E402
from hindsight_memory_control_plane.operation_recovery import (  # noqa: E402
    OperationRecoveryError,
    create_claim_release_plan,
    create_cohort_manifest,
    create_global_queue_blocker_classification,
    create_live_snapshot,
    create_requeue_plan,
    normalize_pg0_binding,
    verify_cohort_manifest,
    verify_claim_release_plan,
    verify_global_queue_blocker_classification,
    verify_live_snapshot,
    verify_requeue_plan,
)


EXPECTED_COUNTS = {
    "retain": 42,
    "refresh_mental_model": 4,
    "consolidation": 2,
}


def historical_source_authority() -> dict:
    return {
        "kind": "approved-historical-backup",
        "artifact_path": (
            "/Users/ivan/.local/state/hindsight-control-plane/"
            "operation-recovery/source/historical.dump.age"
        ),
        "artifact_sha256": "a" * 64,
        "postgres_system_identifier": "7659746962107358086",
        "generation_before": "systalyze:public:90",
        "generation_after": "systalyze:public:90",
    }


def live_source_authority() -> dict:
    return {
        "kind": "verified-live-pg0-backup",
        "postgres_system_identifier": "7659746962107358086",
        "data_identity_digest": "5" * 64,
        "generation_before": "systalyze:public:123",
        "generation_after": "systalyze:public:123",
        "binding": {
            "instance": "hindsight-embed-systalyze",
            "data_dir": (
                "/Users/ivan/.pg0/instances/"
                "hindsight-embed-systalyze/data"
            ),
            "data_device": 16777233,
            "data_inode": 1234567,
            "port": 54329,
            "pid": 12345,
            "started_at": 1_785_399_000,
            "socket_dir": "/private/tmp",
            "socket_path": "/private/tmp/.s.PGSQL.54329",
            "database": "hindsight",
            "user": "hindsight",
        },
    }


def backup_evidence() -> dict:
    source_authority = historical_source_authority()
    return {
        "schema_version": 1,
        "artifact_sha256": "a" * 64,
        "restore_identity_digest": "b" * 64,
        "postgres_system_identifier": "7659746962107358086",
        "source_authority": source_authority,
        "source_authority_digest": digest(source_authority),
        "toolchain_digest": "7" * 64,
        "full_schema": True,
        "restore_tested": True,
        "plaintext_disposed": True,
    }


def rollback_backup_evidence() -> dict:
    source_authority = live_source_authority()
    return {
        "schema_version": 1,
        "artifact_sha256": "c" * 64,
        "restore_identity_digest": "d" * 64,
        "postgres_system_identifier": "7659746962107358086",
        "source_authority": source_authority,
        "source_authority_digest": digest(source_authority),
        "toolchain_digest": "7" * 64,
        "full_schema": True,
        "restore_tested": True,
        "plaintext_disposed": True,
    }


def release_identity() -> dict:
    return {
        "source_commit": "3" * 40,
        "version": "2026.07.30+3333333",
        "release_digest": "e" * 64,
    }


def rollback_encryption() -> dict:
    return {
        "recipient": "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
        "age_path": "/opt/homebrew/Cellar/age/1.3.1/bin/age",
        "age_sha256": "8" * 64,
    }


def installation_authority() -> dict:
    return {
        "consumer_id": "stlz-ivan-mbp",
        "profile_id": "systalyze",
        "schema": "public",
        "bank_id": "engineering",
        "install_state_digest": "f" * 64,
        "binding_generation_digest": "1" * 64,
        "installed_release_version": "2026.07.28+fc7dfa9",
        "current_release_digest": "2" * 64,
        "recorded_data_identity_digest": "4" * 64,
        "observed_data_identity_digest": "5" * 64,
        "postgres_system_identifier": "7659746962107358086",
    }


def operation_rows() -> list[dict]:
    rows = []
    position = 0
    for operation_type, count in EXPECTED_COUNTS.items():
        for _ in range(count):
            position += 1
            operation_id = f"00000000-0000-4000-8000-{position:012d}"
            row = {
                "operation_id": operation_id,
                "bank_id": "engineering",
                "operation_type": operation_type,
                "status": "pending",
                "created_at": f"2026-07-29T12:{position % 60:02d}:00Z",
                "updated_at": f"2026-07-29T12:{position % 60:02d}:01Z",
                "completed_at": None,
                "retry_count": 0,
                "next_retry_at": None,
                "worker_id_present": False,
                "worker_id_digest": None,
                "claimed_at": None,
                "task_payload_present": True,
                "task_payload_digest": f"{position:064x}",
                "result_metadata_digest": f"{position + 100:064x}",
                "error_category": "none",
                "error_digest": None,
            }
            rows.append(row)
    return rows


class OperationRecoveryContractTest(unittest.TestCase):
    def cohort(self) -> dict:
        return dict(
            create_cohort_manifest(
                operation_rows(),
                profile_id="systalyze",
                schema="public",
                bank_id="engineering",
                generation="systalyze:public:90",
                backup=backup_evidence(),
                created_at=1_785_400_000,
            )
        )

    def live_snapshot(self) -> dict:
        rows = operation_rows()
        failed = {0, 1, 2, 42, 46}
        completed = {6, 7, 8, 9, 10, 11}
        for index, row in enumerate(rows):
            if index in failed:
                row["status"] = "failed"
                row["completed_at"] = "2026-07-29T13:00:00Z"
                row["error_category"] = "provider_capacity"
                row["error_digest"] = f"{index + 500:064x}"
            elif index == 5:
                row["status"] = "cancelled"
                row["completed_at"] = "2026-07-29T13:00:01Z"
            elif index in completed:
                row["status"] = "completed"
                row["completed_at"] = "2026-07-29T13:00:02Z"
        return dict(
            create_live_snapshot(
                self.cohort(),
                rows,
                generation_before="systalyze:public:123",
                generation_after="systalyze:public:123",
                installation_authority=installation_authority(),
                observed_at=1_785_401_000,
            )
        )

    def requeue_plan(self) -> dict:
        return dict(
            create_requeue_plan(
                self.cohort(),
                self.live_snapshot(),
                candidate_release=release_identity(),
                rollback_backup=rollback_backup_evidence(),
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/queue-blocker-backup.age",
                rollback_bundle_path="/private/tmp/queue-blocker-bundle.age",
                authorization_receipt_path=(
                    "/private/tmp/queue-blocker-authorization.json"
                ),
                application_receipt_path=(
                    "/private/tmp/queue-blocker-application.json"
                ),
                verification_receipt_path=(
                    "/private/tmp/queue-blocker-verification.json"
                ),
                rollback_receipt_path=(
                    "/private/tmp/queue-blocker-rollback.json"
                ),
                created_at=1_785_402_000,
            )
        )

    @staticmethod
    def queue_blocker_row() -> dict:
        return {
            "operation_id": "00000000-0000-4000-8000-000000000099",
            "bank_id": "unexpected-bank",
            "operation_type": "unexpected-operation",
            "status": "pending",
            "created_at": "2026-07-29T12:00:00.000000Z",
            "updated_at": "2026-07-29T13:00:00.000000Z",
            "completed_at": None,
            "retry_count": 2,
            "next_retry_at": None,
            "worker_id_present": True,
            "worker_id_digest": "6" * 64,
            "claimed_at": "2026-07-29T12:30:00.000000Z",
            "task_payload_present": True,
            "task_payload_digest": "7" * 64,
            "in_reference_cohort": False,
            "in_reference_selected_set": False,
            "blocker_reason": "claimed_pending",
        }

    def selected_queue_blocker_row(self, reference_plan, status) -> dict:
        selected = reference_plan["selected_operations"][0]
        return {
            **self.queue_blocker_row(),
            "operation_id": selected["operation_id"],
            "bank_id": "engineering",
            "operation_type": selected["operation_type"],
            "status": status,
            "completed_at": (
                None
                if status == "processing"
                else "2026-07-29T13:00:00.000000Z"
            ),
            "in_reference_cohort": True,
            "in_reference_selected_set": True,
            "blocker_reason": (
                "processing" if status == "processing" else f"claimed_{status}"
            ),
        }

    def queue_blocker_classification(
        self,
        rows=None,
        *,
        reference_plan=None,
        authority=None,
    ):
        reference_plan = reference_plan or self.requeue_plan()
        return create_global_queue_blocker_classification(
            [self.queue_blocker_row()] if rows is None else rows,
            classifier_candidate_release={
                "source_commit": "9" * 40,
                "version": "2026.07.31+9999999.operation-recovery.6",
                "release_digest": "8" * 64,
            },
            reference_plan=reference_plan,
            installation_authority=authority or installation_authority(),
            generation_before="systalyze:public:123",
            generation_after="systalyze:public:123",
            guard_contract_version=1,
            guard_contract_digest="a" * 64,
            observed_at=reference_plan["expires_at"] + 1,
        )

    def claim_release_inputs(
        self,
        *,
        planned_at: int = 1_785_460_800,
        live_generation: str = "systalyze:public:124",
    ) -> tuple[dict, dict, dict[str, str]]:
        blockers = []
        for index in range(43):
            blockers.append(
                {
                    **self.queue_blocker_row(),
                    "operation_id": (
                        f"00000000-0000-4000-8000-{index + 100:012x}"
                    ),
                    "bank_id": "codex" if index < 37 else "engineering",
                    "operation_type": (
                        "retain" if index < 37 else "refresh_mental_model"
                    ),
                    "status": "failed",
                    "completed_at": "2026-07-29T13:00:00.000000Z",
                    "blocker_reason": "claimed_failed",
                }
            )
        reference_plan = self.requeue_plan()
        predecessor = create_global_queue_blocker_classification(
            blockers,
            classifier_candidate_release={
                "source_commit": "9" * 40,
                "version": "2026.08.01+9999999.operation-recovery.6",
                "release_digest": "8" * 64,
            },
            reference_plan=reference_plan,
            installation_authority=installation_authority(),
            generation_before="systalyze:public:123",
            generation_after="systalyze:public:123",
            guard_contract_version=1,
            guard_contract_digest="a" * 64,
            observed_at=planned_at - 7200,
        )
        live = create_global_queue_blocker_classification(
            blockers,
            classifier_candidate_release=release_identity(),
            reference_plan=reference_plan,
            installation_authority=installation_authority(),
            generation_before=live_generation,
            generation_after=live_generation,
            guard_contract_version=1,
            guard_contract_digest="a" * 64,
            observed_at=planned_at,
        )
        nonclaim_digests = {
            row["operation_id"]: f"{index + 1:064x}"
            for index, row in enumerate(live["blockers"])
        }
        return dict(predecessor), dict(live), nonclaim_digests

    def test_global_queue_blocker_classification_is_closed_and_read_only(self):
        row = self.queue_blocker_row()
        classification = self.queue_blocker_classification([row])

        self.assertEqual(
            verify_global_queue_blocker_classification(
                classification,
                now=classification["observed_at"],
            ),
            classification,
        )
        self.assertEqual(classification["authority"], "read-only-classification")
        self.assertIs(classification["mutation_authorized"], False)
        self.assertIs(classification["reference_plan_expired"], True)
        self.assertEqual(
            classification["expires_at"],
            classification["observed_at"] + 3600,
        )
        self.assertEqual(classification["blocker_count"], 1)
        self.assertEqual(classification["status_counts"], {"pending": 1})
        self.assertEqual(classification["bank_counts"], {"unexpected-bank": 1})
        self.assertEqual(
            classification["operation_type_counts"],
            {"unexpected-operation": 1},
        )
        self.assertEqual(
            classification["blockers"][0]["row_digest"],
            digest(row),
        )
        blocker = classification["blockers"][0]
        self.assertNotIn("task_payload", blocker)
        self.assertNotIn("error_message", blocker)
        self.assertNotIn("worker_id", blocker)
        with self.assertRaises(OperationRecoveryError):
            verify_requeue_plan(classification, allow_expired=True)

    def test_global_queue_blocker_classification_rejects_authority_drift(self):
        drifted_authority = installation_authority()
        drifted_authority["install_state_digest"] = "0" * 64
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "authority differs",
        ):
            self.queue_blocker_classification(
                authority=drifted_authority,
            )

    def test_global_queue_blocker_classification_rejects_selected_terminal_row(self):
        reference_plan = self.requeue_plan()
        selected_row = self.selected_queue_blocker_row(reference_plan, "failed")
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "queue blocker is invalid",
        ):
            self.queue_blocker_classification(
                [selected_row],
                reference_plan=reference_plan,
            )

    def test_global_queue_blocker_classification_accepts_selected_processing(self):
        reference_plan = self.requeue_plan()
        selected_processing = self.selected_queue_blocker_row(
            reference_plan,
            "processing",
        )
        classification = self.queue_blocker_classification(
            [selected_processing],
            reference_plan=reference_plan,
        )
        self.assertEqual(
            classification["status_counts"],
            {"processing": 1},
        )

    def test_global_queue_blocker_classification_rejects_invalid_row_evidence(self):
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "live operation evidence is invalid",
        ):
            self.queue_blocker_classification(
                "not-row-evidence",
            )

    def test_global_queue_blocker_verification_rejects_duplicate_ids(self):
        classification = self.queue_blocker_classification()
        duplicate = deepcopy(classification)
        duplicate["blockers"].append(deepcopy(duplicate["blockers"][0]))
        duplicate["blocker_count"] = 2
        duplicate["status_counts"]["pending"] = 2
        duplicate["bank_counts"]["unexpected-bank"] = 2
        duplicate["operation_type_counts"]["unexpected-operation"] = 2
        duplicate["classification_digest"] = digest(
            {
                key: value
                for key, value in duplicate.items()
                if key != "classification_digest"
            }
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "contains duplicates",
        ):
            verify_global_queue_blocker_classification(
                duplicate,
                allow_expired=True,
            )

    def test_global_queue_blocker_verification_rejects_digest_tampering(self):
        tampered = deepcopy(self.queue_blocker_classification())
        tampered["guard_contract_version"] = 2
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "classification digest differs",
        ):
            verify_global_queue_blocker_classification(
                tampered,
                allow_expired=True,
            )

    def test_global_queue_blocker_verification_rejects_row_digest_tampering(self):
        tampered = deepcopy(self.queue_blocker_classification())
        tampered["blockers"][0]["row_digest"] = "0" * 64
        tampered["classification_digest"] = digest(
            {
                key: value
                for key, value in tampered.items()
                if key != "classification_digest"
            }
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "row digest differs",
        ):
            verify_global_queue_blocker_classification(
                tampered,
                allow_expired=True,
            )

    def test_global_queue_blocker_verification_rejects_reordered_rows(self):
        first = self.queue_blocker_row()
        second = {
            **first,
            "operation_id": "00000000-0000-4000-8000-000000000098",
            "created_at": "2026-07-29T13:00:00.000000Z",
        }
        reordered = deepcopy(self.queue_blocker_classification([first, second]))
        reordered["blockers"].reverse()
        reordered["classification_digest"] = digest(
            {
                key: value
                for key, value in reordered.items()
                if key != "classification_digest"
            }
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "are not ordered",
        ):
            verify_global_queue_blocker_classification(
                reordered,
                allow_expired=True,
            )

    def test_global_queue_blocker_verification_rejects_expired_artifact(self):
        classification = self.queue_blocker_classification()
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "classification expired",
        ):
            verify_global_queue_blocker_classification(
                classification,
                now=classification["expires_at"],
            )
        self.assertEqual(
            verify_global_queue_blocker_classification(
                classification,
                now=classification["expires_at"],
                allow_expired=True,
            ),
            classification,
        )

    def test_global_queue_blocker_scope_isolation(self):
        classification = self.queue_blocker_classification()
        classification["scope"]["statuses"].append("mutated")
        fresh = self.queue_blocker_classification()
        self.assertEqual(
            fresh["scope"]["statuses"],
            ["processing", "pending", "failed", "cancelled"],
        )

    def test_backup_manifest_freezes_exact_authorized_cohort_without_payloads(self):
        cohort = self.cohort()

        self.assertEqual(cohort["expected_operation_counts"], EXPECTED_COUNTS)
        self.assertEqual(len(cohort["operations"]), 48)
        self.assertEqual(
            cohort["backup"]["source_authority"],
            historical_source_authority(),
        )
        self.assertEqual(verify_cohort_manifest(cohort), cohort)
        for operation in cohort["operations"]:
            self.assertNotIn("task_payload", operation)
            self.assertNotIn("error_message", operation)

        wrong_count = operation_rows()[:-1]
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "authorized operation counts",
        ):
            create_cohort_manifest(
                wrong_count,
                profile_id="systalyze",
                schema="public",
                bank_id="engineering",
                generation="systalyze:public:90",
                backup=backup_evidence(),
                created_at=1_785_400_000,
            )

    def test_backup_source_authority_is_closed_stable_and_digest_bound(self):
        backup = backup_evidence()
        backup["source_authority"]["unexpected"] = True
        backup["source_authority_digest"] = digest(backup["source_authority"])
        with self.assertRaisesRegex(OperationRecoveryError, "source authority"):
            create_cohort_manifest(
                operation_rows(),
                profile_id="systalyze",
                schema="public",
                bank_id="engineering",
                generation="systalyze:public:90",
                backup=backup,
                created_at=1_785_400_000,
            )

        backup = backup_evidence()
        backup["source_authority_digest"] = "6" * 64
        with self.assertRaisesRegex(OperationRecoveryError, "authority digest"):
            create_cohort_manifest(
                operation_rows(),
                profile_id="systalyze",
                schema="public",
                bank_id="engineering",
                generation="systalyze:public:90",
                backup=backup,
                created_at=1_785_400_000,
            )

        backup = backup_evidence()
        backup["source_authority"]["generation_after"] = "systalyze:public:91"
        backup["source_authority_digest"] = digest(backup["source_authority"])
        with self.assertRaisesRegex(OperationRecoveryError, "generation changed"):
            create_cohort_manifest(
                operation_rows(),
                profile_id="systalyze",
                schema="public",
                bank_id="engineering",
                generation="systalyze:public:90",
                backup=backup,
                created_at=1_785_400_000,
            )

        with self.assertRaisesRegex(OperationRecoveryError, "source authority"):
            create_cohort_manifest(
                operation_rows(),
                profile_id="systalyze",
                schema="public",
                bank_id="engineering",
                generation="systalyze:public:90",
                backup=rollback_backup_evidence(),
                created_at=1_785_400_000,
            )

        backup = rollback_backup_evidence()
        backup["source_authority"]["binding"]["unexpected"] = True
        backup["source_authority_digest"] = digest(
            backup["source_authority"]
        )
        with self.assertRaisesRegex(OperationRecoveryError, "source authority"):
            create_requeue_plan(
                self.cohort(),
                self.live_snapshot(),
                candidate_release=release_identity(),
                rollback_backup=backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/rollback.dump.age",
                rollback_bundle_path="/private/tmp/rollback.json",
                authorization_receipt_path="/private/tmp/authorization.json",
                application_receipt_path="/private/tmp/application.json",
                verification_receipt_path="/private/tmp/verification.json",
                rollback_receipt_path="/private/tmp/rollback-receipt.json",
                created_at=1_785_401_100,
            )

    def test_live_snapshot_requires_same_ids_payload_digests_and_generation(self):
        snapshot = self.live_snapshot()
        self.assertEqual(snapshot["status_counts"]["failed"], 5)
        self.assertEqual(snapshot["status_counts"]["cancelled"], 1)
        self.assertEqual(snapshot["status_counts"]["completed"], 6)
        self.assertEqual(snapshot["status_counts"]["pending"], 36)
        self.assertEqual(verify_live_snapshot(snapshot), snapshot)

        rows = operation_rows()
        rows[0]["task_payload_digest"] = "9" * 64
        with self.assertRaisesRegex(OperationRecoveryError, "payload digest"):
            create_live_snapshot(
                self.cohort(),
                rows,
                generation_before="systalyze:public:123",
                generation_after="systalyze:public:123",
                installation_authority=installation_authority(),
                observed_at=1_785_401_000,
            )

        with self.assertRaisesRegex(OperationRecoveryError, "generation changed"):
            create_live_snapshot(
                self.cohort(),
                operation_rows(),
                generation_before="systalyze:public:123",
                generation_after="systalyze:public:124",
                installation_authority=installation_authority(),
                observed_at=1_785_401_000,
            )

    def test_plan_requeues_only_failed_and_cancelled_rows(self):
        snapshot = self.live_snapshot()
        plan = dict(
            create_requeue_plan(
                self.cohort(),
                snapshot,
                candidate_release=release_identity(),
                rollback_backup=rollback_backup_evidence(),
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/rollback.dump.age",
                rollback_bundle_path=(
                    "/Users/ivan/.local/state/hindsight-control-plane/"
                    "operation-recovery/rollback/plan.json"
                ),
                authorization_receipt_path=(
                    "/Users/ivan/.local/state/hindsight-control-plane/"
                    "operation-recovery/receipts/plan.json"
                ),
                application_receipt_path=(
                    "/Users/ivan/.local/state/hindsight-control-plane/"
                    "operation-recovery/applications/plan.json"
                ),
                verification_receipt_path=(
                    "/Users/ivan/.local/state/hindsight-control-plane/"
                    "operation-recovery/verification/plan.json"
                ),
                rollback_receipt_path=(
                    "/Users/ivan/.local/state/hindsight-control-plane/"
                    "operation-recovery/rollbacks/plan.json"
                ),
                created_at=1_785_401_100,
            )
        )

        self.assertEqual(len(plan["selected_operations"]), 6)
        self.assertEqual(
            {item["expected_status"] for item in plan["selected_operations"]},
            {"failed", "cancelled"},
        )
        self.assertEqual(plan["policy"]["pending"], "preserve")
        self.assertEqual(plan["policy"]["completed"], "preserve")
        self.assertEqual(plan["policy"]["processing"], "reject")
        self.assertEqual(
            plan["rollback_backup"]["source_authority"],
            live_source_authority(),
        )
        self.assertEqual(verify_requeue_plan(plan, now=1_785_401_101), plan)

        changed = deepcopy(plan)
        changed["candidate_release"]["release_digest"] = "9" * 64
        with self.assertRaisesRegex(OperationRecoveryError, "plan digest"):
            verify_requeue_plan(changed, now=1_785_401_101)

        changed = deepcopy(plan)
        changed["rollback_encryption"]["recipient"] = (
            "age1changedrecipient"
        )
        with self.assertRaisesRegex(OperationRecoveryError, "plan digest"):
            verify_requeue_plan(changed, now=1_785_401_101)

        changed = deepcopy(plan)
        changed["selected_operations"][0]["operation_id"] = (
            "ffffffff-ffff-4fff-8fff-ffffffffffff"
        )
        changed_body = {
            key: value for key, value in changed.items() if key != "plan_digest"
        }
        changed["plan_digest"] = digest(changed_body)
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "selected operations",
        ):
            verify_requeue_plan(changed, now=1_785_401_101)

        changed = deepcopy(plan)
        changed_row = changed["live_snapshot"]["operations"][-1]
        changed_row["task_payload_digest"] = "8" * 64
        changed_row["row_digest"] = digest(
            {
                key: value
                for key, value in changed_row.items()
                if key != "row_digest"
            }
        )
        snapshot_body = {
            key: value
            for key, value in changed["live_snapshot"].items()
            if key != "snapshot_digest"
        }
        changed["live_snapshot"]["snapshot_digest"] = digest(snapshot_body)
        changed_body = {
            key: value for key, value in changed.items() if key != "plan_digest"
        }
        changed["plan_digest"] = digest(changed_body)
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "selected operations",
        ):
            verify_requeue_plan(changed, now=1_785_401_101)

        changed = deepcopy(plan)
        changed["rollback_backup"]["postgres_system_identifier"] = (
            "7659746962107358999"
        )
        changed["rollback_backup"]["source_authority"][
            "postgres_system_identifier"
        ] = "7659746962107358999"
        changed["rollback_backup"]["source_authority_digest"] = digest(
            changed["rollback_backup"]["source_authority"]
        )
        changed_body = {
            key: value for key, value in changed.items() if key != "plan_digest"
        }
        changed["plan_digest"] = digest(changed_body)
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "plan evidence",
        ):
            verify_requeue_plan(changed, now=1_785_401_101)

        changed = rollback_backup_evidence()
        changed["source_authority"]["generation_after"] = (
            "systalyze:public:124"
        )
        changed["source_authority_digest"] = digest(changed["source_authority"])
        with self.assertRaisesRegex(OperationRecoveryError, "generation changed"):
            create_requeue_plan(
                self.cohort(),
                snapshot,
                candidate_release=release_identity(),
                rollback_backup=changed,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/rollback.dump.age",
                rollback_bundle_path="/private/tmp/rollback.json",
                authorization_receipt_path="/private/tmp/authorization.json",
                application_receipt_path="/private/tmp/application.json",
                verification_receipt_path="/private/tmp/verification.json",
                rollback_receipt_path="/private/tmp/rollback-receipt.json",
                created_at=1_785_401_100,
            )

        with self.assertRaisesRegex(OperationRecoveryError, "expired"):
            verify_requeue_plan(plan, now=plan["expires_at"])

        with self.assertRaisesRegex(
            OperationRecoveryError,
            "verification time",
        ):
            verify_requeue_plan(plan, now="invalid")

        with self.assertRaisesRegex(
            OperationRecoveryError,
            "must be distinct",
        ):
            create_requeue_plan(
                self.cohort(),
                snapshot,
                candidate_release=release_identity(),
                rollback_backup=rollback_backup_evidence(),
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/rollback.dump.age",
                rollback_bundle_path="/private/tmp/shared.json",
                authorization_receipt_path="/private/tmp/shared.json",
                application_receipt_path="/private/tmp/application.json",
                verification_receipt_path="/private/tmp/verification.json",
                rollback_receipt_path="/private/tmp/rollback.json",
                created_at=1_785_401_100,
            )

        with self.assertRaisesRegex(
            OperationRecoveryError,
            "must be distinct",
        ):
            create_requeue_plan(
                self.cohort(),
                snapshot,
                candidate_release=release_identity(),
                rollback_backup=rollback_backup_evidence(),
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/rollback.dump.age",
                rollback_bundle_path="/private/tmp/rollback.json",
                authorization_receipt_path="/private/tmp/authorization.json",
                application_receipt_path=(
                    "/private/tmp/Operation-Recovery-Receipt.json"
                ),
                verification_receipt_path="/private/tmp/verification.json",
                rollback_receipt_path=(
                    "/private/tmp/operation-recovery-receipt.json"
                ),
                created_at=1_785_401_100,
            )

        with self.assertRaisesRegex(
            OperationRecoveryError,
            "must be distinct",
        ):
            create_requeue_plan(
                self.cohort(),
                snapshot,
                candidate_release=release_identity(),
                rollback_backup=rollback_backup_evidence(),
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/rollback.dump.age",
                rollback_bundle_path="/private/tmp/rollback.json",
                authorization_receipt_path="/private/tmp/authorization.json",
                application_receipt_path="/private/tmp/caf\u00e9.json",
                verification_receipt_path="/private/tmp/verification.json",
                rollback_receipt_path="/private/tmp/cafe\u0301.json",
                created_at=1_785_401_100,
            )

        if sys.platform == "darwin":
            with self.assertRaisesRegex(
                OperationRecoveryError,
                "must be distinct",
            ):
                create_requeue_plan(
                    self.cohort(),
                    snapshot,
                    candidate_release=release_identity(),
                    rollback_backup=rollback_backup_evidence(),
                    rollback_encryption=rollback_encryption(),
                    rollback_backup_path="/private/tmp/rollback.dump.age",
                    rollback_bundle_path="/private/tmp/rollback.json",
                    authorization_receipt_path="/private/tmp/authorization.json",
                    application_receipt_path="/tmp/equivalent.json",
                    verification_receipt_path="/private/tmp/verification.json",
                    rollback_receipt_path="/private/tmp/equivalent.json",
                    created_at=1_785_401_100,
                )

    @unittest.skipUnless(
        sys.platform == "darwin",
        "requires the macOS /tmp to /private/tmp symlink",
    )
    def test_live_backup_binding_alias_round_trips_into_plan(self):
        backup = rollback_backup_evidence()
        binding = backup["source_authority"]["binding"]
        binding["socket_dir"] = "/tmp"
        binding["socket_path"] = "/tmp/.s.PGSQL.54329"
        backup["source_authority"]["binding"] = normalize_pg0_binding(
            binding,
            "operation-recovery live backup pg0 binding",
        )
        backup["source_authority_digest"] = digest(
            backup["source_authority"]
        )

        plan = create_requeue_plan(
            self.cohort(),
            self.live_snapshot(),
            candidate_release=release_identity(),
            rollback_backup=backup,
            rollback_encryption=rollback_encryption(),
            rollback_backup_path="/private/tmp/rollback.dump.age",
            rollback_bundle_path="/private/tmp/rollback.json",
            authorization_receipt_path="/private/tmp/authorization.json",
            application_receipt_path="/private/tmp/application.json",
            verification_receipt_path="/private/tmp/verification.json",
            rollback_receipt_path="/private/tmp/rollback-receipt.json",
            created_at=1_785_401_100,
        )
        self.assertEqual(
            plan["rollback_backup"]["source_authority"]["binding"][
                "socket_dir"
            ],
            "/private/tmp",
        )

    def test_plan_rejects_processing_or_missing_cohort_rows(self):
        snapshot = self.live_snapshot()
        snapshot["operations"][0]["current_status"] = "processing"
        snapshot["operations"][0]["row_digest"] = digest(
            {
                key: value
                for key, value in snapshot["operations"][0].items()
                if key != "row_digest"
            }
        )
        snapshot["status_counts"]["failed"] -= 1
        snapshot["status_counts"]["processing"] += 1
        snapshot_body = {
            key: value for key, value in snapshot.items() if key != "snapshot_digest"
        }
        snapshot["snapshot_digest"] = digest(snapshot_body)
        with self.assertRaisesRegex(OperationRecoveryError, "processing"):
            create_requeue_plan(
                self.cohort(),
                snapshot,
                candidate_release=release_identity(),
                rollback_backup=rollback_backup_evidence(),
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/rollback.dump.age",
                rollback_bundle_path="/private/tmp/rollback.json",
                authorization_receipt_path="/private/tmp/authorization.json",
                application_receipt_path="/private/tmp/application.json",
                verification_receipt_path="/private/tmp/verification.json",
                rollback_receipt_path="/private/tmp/rollback-receipt.json",
                created_at=1_785_401_100,
            )

        snapshot = self.live_snapshot()
        snapshot["operations"].pop()
        snapshot_body = {
            key: value for key, value in snapshot.items() if key != "snapshot_digest"
        }
        snapshot["snapshot_digest"] = digest(snapshot_body)
        with self.assertRaisesRegex(OperationRecoveryError, "live snapshot"):
            create_requeue_plan(
                self.cohort(),
                snapshot,
                candidate_release=release_identity(),
                rollback_backup=rollback_backup_evidence(),
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/rollback.dump.age",
                rollback_bundle_path="/private/tmp/rollback.json",
                authorization_receipt_path="/private/tmp/authorization.json",
                application_receipt_path="/private/tmp/application.json",
                verification_receipt_path="/private/tmp/verification.json",
                rollback_receipt_path="/private/tmp/rollback-receipt.json",
                created_at=1_785_401_100,
            )

    def test_claim_release_plan_is_closed_unapproved_and_exactly_bound(self):
        predecessor, live, nonclaim_digests = self.claim_release_inputs(
            live_generation="systalyze:public:123"
        )
        candidate = release_identity()

        plan = create_claim_release_plan(
            predecessor,
            live,
            nonclaim_state_digests=nonclaim_digests,
            candidate_release=candidate,
            installation_authority=installation_authority(),
            rollback_encryption=rollback_encryption(),
            rollback_bundle_path="/private/tmp/claim-release.bundle.json",
            authorization_receipt_path=(
                "/private/tmp/claim-release.authorization.json"
            ),
            application_receipt_path=(
                "/private/tmp/claim-release.application.json"
            ),
            verification_receipt_path=(
                "/private/tmp/claim-release.verification.json"
            ),
            rollback_receipt_path="/private/tmp/claim-release.rollback.json",
            created_at=live["observed_at"],
        )

        self.assertEqual(
            verify_claim_release_plan(plan, now=plan["created_at"]),
            plan,
        )
        self.assertEqual(plan["kind"], "operation-recovery-claim-release-plan")
        self.assertEqual(plan["authority"], "unapproved-plan")
        self.assertIs(plan["mutation_authorized"], False)
        self.assertEqual(plan["selected_row_count"], 43)
        self.assertEqual(plan["status_counts"], {"failed": 43})
        self.assertEqual(plan["bank_counts"], {"codex": 37, "engineering": 6})
        self.assertEqual(
            plan["operation_type_counts"],
            {"refresh_mental_model": 6, "retain": 37},
        )
        self.assertEqual(plan["pre_generation"], "systalyze:public:123")
        self.assertEqual(plan["expires_at"], plan["created_at"] + 3600)
        self.assertEqual(
            plan["predecessor_classification_digest"],
            predecessor["classification_digest"],
        )
        self.assertEqual(
            plan["live_classification_digest"],
            live["classification_digest"],
        )
        serialized = json.dumps(plan, sort_keys=True)
        self.assertNotIn('"task_payload":', serialized)
        self.assertNotIn('"worker_id":', serialized)
        self.assertNotIn('"error_message":', serialized)

    def test_claim_release_plan_binds_fresh_generation_not_predecessor(self):
        planned_at = 1_785_460_800
        predecessor, live, nonclaim_digests = self.claim_release_inputs(
            planned_at=planned_at
        )

        plan = create_claim_release_plan(
            predecessor,
            live,
            nonclaim_state_digests=nonclaim_digests,
            candidate_release=release_identity(),
            installation_authority=installation_authority(),
            rollback_encryption=rollback_encryption(),
            rollback_bundle_path="/private/tmp/claim-release-rollback.json",
            authorization_receipt_path="/private/tmp/claim-release-authorization.json",
            application_receipt_path="/private/tmp/claim-release-application.json",
            verification_receipt_path="/private/tmp/claim-release-verification.json",
            rollback_receipt_path="/private/tmp/claim-release-rollback-receipt.json",
            created_at=planned_at,
        )

        self.assertEqual(plan["pre_generation"], "systalyze:public:124")

    def test_claim_release_plan_verification_rejects_tampering_and_expiry(self):
        planned_at = 1_785_460_800
        predecessor, live, nonclaim_digests = self.claim_release_inputs(
            planned_at=planned_at
        )
        plan = dict(
            create_claim_release_plan(
                predecessor,
                live,
                nonclaim_state_digests=nonclaim_digests,
                candidate_release=release_identity(),
                installation_authority=installation_authority(),
                rollback_encryption=rollback_encryption(),
                rollback_bundle_path=(
                    "/private/tmp/claim-release.bundle.json"
                ),
                authorization_receipt_path=(
                    "/private/tmp/claim-release.authorization.json"
                ),
                application_receipt_path=(
                    "/private/tmp/claim-release.application.json"
                ),
                verification_receipt_path=(
                    "/private/tmp/claim-release.verification.json"
                ),
                rollback_receipt_path=(
                    "/private/tmp/claim-release.rollback.json"
                ),
                created_at=planned_at,
            )
        )

        guard_tampered = deepcopy(plan)
        guard_tampered["guard_contract_version"] = 2
        with self.assertRaisesRegex(OperationRecoveryError, "digest differs"):
            verify_claim_release_plan(guard_tampered, now=planned_at)

        with self.assertRaisesRegex(OperationRecoveryError, "expired"):
            verify_claim_release_plan(plan, now=plan["expires_at"])

        alias_tampered = deepcopy(plan)
        alias_tampered["authorization_receipt_path"] = plan[
            "rollback_bundle_path"
        ].upper()
        alias_body = {
            key: value
            for key, value in alias_tampered.items()
            if key != "plan_digest"
        }
        alias_tampered["plan_digest"] = digest(alias_body)
        with self.assertRaisesRegex(OperationRecoveryError, "must be distinct"):
            verify_claim_release_plan(alias_tampered, now=planned_at)

        distribution_tampered = deepcopy(plan)
        first = distribution_tampered["selected_rows"][0]
        first["bank_id"] = "engineering"
        first_body = {
            key: value
            for key, value in first.items()
            if key not in {"row_digest", "nonclaim_state_digest"}
        }
        first["row_digest"] = digest(first_body)
        distribution_tampered["selected_row_set_digest"] = digest(
            [
                {
                    "operation_id": row["operation_id"],
                    "row_digest": row["row_digest"],
                    "nonclaim_state_digest": row["nonclaim_state_digest"],
                }
                for row in distribution_tampered["selected_rows"]
            ]
        )
        distribution_body = {
            key: value
            for key, value in distribution_tampered.items()
            if key != "plan_digest"
        }
        distribution_tampered["plan_digest"] = digest(distribution_body)
        with self.assertRaisesRegex(OperationRecoveryError, "plan is invalid"):
            verify_claim_release_plan(
                distribution_tampered,
                now=planned_at,
            )

        pair_tampered = deepcopy(plan)
        codex_row = pair_tampered["selected_rows"][0]
        engineering_row = pair_tampered["selected_rows"][-1]
        codex_row["operation_type"] = "refresh_mental_model"
        engineering_row["operation_type"] = "retain"
        for row in (codex_row, engineering_row):
            row_body = {
                key: value
                for key, value in row.items()
                if key not in {"row_digest", "nonclaim_state_digest"}
            }
            row["row_digest"] = digest(row_body)
        pair_tampered["selected_row_set_digest"] = digest(
            [
                {
                    "operation_id": row["operation_id"],
                    "row_digest": row["row_digest"],
                    "nonclaim_state_digest": row["nonclaim_state_digest"],
                }
                for row in pair_tampered["selected_rows"]
            ]
        )
        pair_body = {
            key: value
            for key, value in pair_tampered.items()
            if key != "plan_digest"
        }
        pair_tampered["plan_digest"] = digest(pair_body)
        with self.assertRaisesRegex(OperationRecoveryError, "plan is invalid"):
            verify_claim_release_plan(pair_tampered, now=planned_at)


if __name__ == "__main__":
    unittest.main()
