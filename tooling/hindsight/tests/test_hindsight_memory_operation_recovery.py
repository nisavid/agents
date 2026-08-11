from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from hindsight_memory_control_plane.canonical import digest  # noqa: E402
import hindsight_memory_control_plane.operation_recovery as recovery_contract  # noqa: E402
from hindsight_memory_control_plane.operation_recovery import (  # noqa: E402
    OperationRecoveryError,
    create_claim_release_plan,
    create_cohort_manifest,
    create_global_queue_blocker_classification,
    create_live_snapshot,
    create_post_abort_recovery_plan,
    create_requeue_plan,
    normalize_pg0_binding,
    verify_cohort_manifest,
    verify_claim_release_plan,
    verify_exact_drain_authorization_receipt,
    verify_global_queue_blocker_classification,
    verify_live_snapshot,
    verify_post_abort_recovery_plan,
    verify_requeue_plan,
)


EXPECTED_COUNTS = {
    "retain": 42,
    "refresh_mental_model": 4,
    "consolidation": 2,
}
PERMITTED_POSITIONS = frozenset(
    {
        0,
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        42,
        43,
        46,
        47,
    }
)


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


def drain_backup_evidence() -> dict:
    evidence = deepcopy(rollback_backup_evidence())
    evidence["source_authority"]["generation_before"] = (
        "systalyze:public:124"
    )
    evidence["source_authority"]["generation_after"] = (
        "systalyze:public:124"
    )
    evidence["source_authority_digest"] = digest(
        evidence["source_authority"]
    )
    return evidence


def release_identity() -> dict:
    return {
        "source_commit": "3" * 40,
        "version": "2026.07.30+3333333",
        "release_digest": "e" * 64,
    }


def exact_drain_authorization(plan: dict, *, authorized_at: int | None = None) -> dict:
    body = {
        "schema_version": 1,
        "kind": "operation-recovery-exact-drain-authorization-receipt",
        "plan_digest": plan["plan_digest"],
        "approval_digest": plan["plan_digest"],
        "candidate_release": plan["candidate_release"],
        "provider_policy_digest": plan["provider_policy_digest"],
        "worker_runtime_digest": plan["worker_runtime_digest"],
        "authorized_at": (
            plan["created_at"] if authorized_at is None else authorized_at
        ),
    }
    return {**body, "receipt_digest": digest(body)}


def exact_drain_application_journal(plan: dict) -> dict:
    authorization = exact_drain_authorization(plan)
    body = {
        "schema_version": 1,
        "kind": "operation-recovery-exact-drain-application-journal",
        "plan_digest": plan["plan_digest"],
        "authorization_receipt_digest": authorization["receipt_digest"],
        "started_at": authorization["authorized_at"],
        "worker_pid": 4242,
        "worker_start_time": "dead-exact-drain-worker",
        "worker_attempt": 1,
    }
    return {**body, "receipt_digest": digest(body)}


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

    def live_snapshot(
        self,
        *,
        cancelled_positions: frozenset[int] = frozenset(),
        failed_positions: frozenset[int] = PERMITTED_POSITIONS,
    ) -> dict:
        rows = operation_rows()
        failed = set(failed_positions)
        if not cancelled_positions.issubset(failed):
            raise ValueError("cancelled test positions must be selected")
        completed = {20, 21, 22, 23, 24, 25}
        for index, row in enumerate(rows):
            if index in failed:
                row["status"] = (
                    "cancelled" if index in cancelled_positions else "failed"
                )
                row["completed_at"] = "2026-07-29T13:00:00Z"
                row["error_category"] = "provider_capacity"
                row["error_digest"] = f"{index + 500:064x}"
                row["worker_id_present"] = True
                row["worker_id_digest"] = f"{index + 800:064x}"
                row["claimed_at"] = "2026-07-29T12:30:00.000000Z"
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

    def requeue_plan(self, snapshot=None) -> dict:
        return dict(
            create_requeue_plan(
                self.cohort(),
                snapshot or self.live_snapshot(),
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

    def drain_snapshot(
        self,
        *,
        completed_positions: set[int] | None = None,
        observed_at: int = 1_785_461_000,
    ) -> dict:
        rows = operation_rows()
        completed_positions = (
            {0, 1, 42, 43, 46}
            if completed_positions is None
            else completed_positions
        )
        for index, row in enumerate(rows):
            if index in completed_positions:
                row["status"] = "completed"
                row["completed_at"] = "2026-07-29T13:00:02Z"
        return dict(
            create_live_snapshot(
                self.cohort(),
                rows,
                generation_before="systalyze:public:124",
                generation_after="systalyze:public:124",
                installation_authority=installation_authority(),
                observed_at=observed_at,
            )
        )

    def drain_plan(
        self,
        *,
        snapshot: dict | None = None,
        created_at: int = 1_785_462_000,
    ) -> dict:
        return dict(
            recovery_contract.create_exact_drain_plan(
                self.cohort(),
                snapshot or self.drain_snapshot(),
                candidate_release=release_identity(),
                rollback_backup=drain_backup_evidence(),
                rollback_backup_path="/private/tmp/drain-backup.age",
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path=(
                    "/private/tmp/drain-authorization.json"
                ),
                application_receipt_path=(
                    "/private/tmp/drain-application.json"
                ),
                status_artifact_path="/private/tmp/drain-status.json",
                verification_receipt_path=(
                    "/private/tmp/drain-verification.json"
                ),
                created_at=created_at,
            )
        )

    def test_exact_drain_plan_separates_approval_evidence_transaction_and_lease(self):
        snapshot = self.drain_snapshot()
        planned_at = 1_785_462_000

        plan = self.drain_plan(snapshot=snapshot, created_at=planned_at)

        self.assertEqual(plan["schema_version"], 3)
        self.assertEqual(plan["expires_at"], planned_at + 86_400)
        self.assertEqual(plan["evidence_observed_at"], snapshot["observed_at"])
        self.assertEqual(plan["evidence_max_age_seconds"], 3_600)
        self.assertEqual(plan["transaction_timeout_seconds"], 120)
        self.assertEqual(plan["execution_lease_seconds"], 86_400)
        self.assertEqual(plan["phase_one_statement_timeout_seconds"], 120)
        self.assertEqual(plan["phase_one_timeout_seconds"], 3_600)
        self.assertEqual(
            plan["phase_repair_contract_digest"],
            recovery_contract.EXACT_DRAIN_PHASE_REPAIR_CONTRACT_DIGEST,
        )
        self.assertEqual(
            recovery_contract.verify_exact_drain_plan(plan, now=planned_at),
            plan,
        )

    def test_exact_drain_verifier_preserves_prior_v2_contract(self):
        current = self.drain_plan()
        body = {
            key: value
            for key, value in current.items()
            if key
            not in {
                "plan_digest",
                "phase_one_statement_timeout_seconds",
                "phase_one_timeout_seconds",
                "phase_repair_contract_digest",
            }
        }
        body["schema_version"] = 2
        prior = {**body, "plan_digest": digest(body)}

        self.assertEqual(
            recovery_contract.verify_exact_drain_plan(
                prior,
                now=prior["created_at"],
            ),
            prior,
        )

    def test_exact_drain_v3_phase_repair_contract_is_closed(self):
        plan = self.drain_plan()
        for key, value in (
            ("phase_one_statement_timeout_seconds", 121),
            ("phase_one_timeout_seconds", 3_601),
            ("phase_repair_contract_digest", "0" * 64),
        ):
            with self.subTest(key=key):
                changed = dict(plan)
                changed[key] = value
                body = {
                    item_key: item_value
                    for item_key, item_value in changed.items()
                    if item_key != "plan_digest"
                }
                changed["plan_digest"] = digest(body)
                with self.assertRaises(OperationRecoveryError):
                    recovery_contract.verify_exact_drain_plan(
                        changed,
                        now=changed["created_at"],
                    )

    def test_exact_drain_consumed_authorization_survives_approval_expiry(self):
        plan = self.drain_plan()
        authorization = exact_drain_authorization(
            plan,
            authorized_at=plan["expires_at"] - 1,
        )

        with self.assertRaisesRegex(OperationRecoveryError, "plan expired"):
            recovery_contract.verify_exact_drain_plan(
                plan,
                now=plan["expires_at"],
            )

        self.assertEqual(
            verify_exact_drain_authorization_receipt(
                authorization,
                plan=plan,
            ),
            authorization,
        )

    def test_exact_drain_plan_rejects_stale_planning_evidence(self):
        snapshot = self.drain_snapshot()

        with self.assertRaisesRegex(OperationRecoveryError, "evidence is stale"):
            self.drain_plan(
                snapshot=snapshot,
                created_at=snapshot["observed_at"] + 3_601,
            )

    def test_exact_drain_plan_accepts_evidence_at_the_3600_second_boundary(self):
        snapshot = self.drain_snapshot()

        plan = self.drain_plan(
            snapshot=snapshot,
            created_at=snapshot["observed_at"] + 3_600,
        )

        self.assertEqual(plan["evidence_max_age_seconds"], 3_600)

    def test_exact_drain_plan_rejects_future_planning_evidence(self):
        snapshot = self.drain_snapshot()

        with self.assertRaisesRegex(OperationRecoveryError, "evidence is stale"):
            self.drain_plan(
                snapshot=snapshot,
                created_at=snapshot["observed_at"] - 1,
            )

    def test_exact_drain_verifier_accepts_the_deployed_legacy_plan_shape(self):
        fixtures = json.loads(
            (
                ROOT
                / "tests"
                / "fixtures"
                / "legacy_exact_drain_plans.json"
            ).read_text(encoding="utf-8")
        )
        legacy = fixtures["exact"]
        post_abort = fixtures["post_abort"]

        self.assertEqual(
            recovery_contract.verify_exact_drain_plan(
                legacy,
                now=legacy["created_at"],
            ),
            legacy,
        )
        self.assertEqual(post_abort["reference_plan"], legacy)
        self.assertEqual(
            verify_post_abort_recovery_plan(
                post_abort,
                now=post_abort["created_at"],
            ),
            post_abort,
        )

    def test_exact_drain_verifier_rejects_non_objects_and_boolean_schema(self):
        for value in ([], None, 17, "exact-drain-plan"):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(
                    OperationRecoveryError,
                    "exact drain plan is invalid",
                ),
            ):
                recovery_contract.verify_exact_drain_plan(value)

        legacy = json.loads(
            (
                ROOT
                / "tests"
                / "fixtures"
                / "legacy_exact_drain_plans.json"
            ).read_text(encoding="utf-8")
        )["exact"]
        legacy["schema_version"] = True
        legacy["plan_digest"] = digest(
            {key: value for key, value in legacy.items() if key != "plan_digest"}
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "exact drain plan is invalid",
        ):
            recovery_contract.verify_exact_drain_plan(
                legacy,
                now=legacy["created_at"],
            )

    def post_abort_snapshot(
        self,
        reference_plan=None,
        *,
        current_interrupted_subset: bool = False,
        interrupted_processing_count: int | None = None,
        observed_at: int = 1_786_390_181,
    ) -> dict:
        reference_plan = reference_plan or self.drain_plan()
        rows = operation_rows()
        completed = {0, 1, 42, 43, 46}
        selected_ids = {
            item["operation_id"]
            for item in reference_plan["selected_operations"]
        }
        worker_id = (
            "operation-recovery-exact-drain-"
            f"{reference_plan['plan_digest'][:12]}"
        )
        worker_digest = hashlib.sha256(worker_id.encode("utf-8")).hexdigest()
        selected_positions = [
            index
            for index, row in enumerate(rows)
            if row["operation_id"] in selected_ids
        ]
        selected_retain_positions = [
            index
            for index in selected_positions
            if rows[index]["operation_type"] == "retain"
        ]
        selected_refresh_positions = [
            index
            for index in selected_positions
            if rows[index]["operation_type"] == "refresh_mental_model"
        ]
        failed_position = (
            None
            if current_interrupted_subset
            or interrupted_processing_count is not None
            else selected_retain_positions[-1]
        )
        processing_positions = set(
            selected_retain_positions[
                : (
                    interrupted_processing_count
                    if interrupted_processing_count is not None
                    else 3
                )
            ]
            if current_interrupted_subset
            or interrupted_processing_count is not None
            else selected_retain_positions[:12] + selected_refresh_positions
        )
        for index, row in enumerate(rows):
            if index in completed:
                row["status"] = "completed"
                row["completed_at"] = "2026-07-29T13:00:02Z"
            elif index in processing_positions:
                row["status"] = "processing"
                row["worker_id_present"] = True
                row["worker_id_digest"] = worker_digest
                row["claimed_at"] = "2026-08-10T10:01:15.000000Z"
            elif index == failed_position:
                row["status"] = "failed"
                row["completed_at"] = "2026-08-10T15:36:45.000000Z"
                row["retry_count"] = 3
                row["error_category"] = "unknown"
                row["error_digest"] = "6" * 64
        return dict(
            create_live_snapshot(
                self.cohort(),
                rows,
                generation_before="systalyze:public:81678",
                generation_after="systalyze:public:81678",
                installation_authority=installation_authority(),
                observed_at=observed_at,
            )
        )

    def prior_v2_post_abort_plan(self) -> dict:
        reference = self.drain_plan()
        snapshot = self.post_abort_snapshot(
            reference,
            interrupted_processing_count=4,
        )
        authorization = exact_drain_authorization(reference)
        journal = exact_drain_application_journal(reference)
        selected = [
            {
                "operation_id": item["operation_id"],
                "operation_type": item["operation_type"],
                "expected_status": item["current_status"],
                "row_digest": item["row_digest"],
                "task_payload_digest": item["task_payload_digest"],
            }
            for item in snapshot["operations"]
            if item["current_status"] == "processing"
        ]
        backup = rollback_backup_evidence()
        backup["source_authority"]["generation_before"] = snapshot[
            "generation_before"
        ]
        backup["source_authority"]["generation_after"] = snapshot[
            "generation_after"
        ]
        backup["source_authority_digest"] = digest(
            backup["source_authority"]
        )
        body = {
            "schema_version": 2,
            "kind": "operation-recovery-exact-drain-post-abort-plan",
            "action": "recover-exact-drain-post-abort",
            "authority": "unapproved-plan",
            "mutation_authorized": False,
            "candidate_release": release_identity(),
            "installation_authority": snapshot["installation_authority"],
            "reference_plan": reference,
            "reference_plan_digest": reference["plan_digest"],
            "reference_worker_id_digest": next(
                item["worker_id_digest"]
                for item in snapshot["operations"]
                if item["current_status"] == "processing"
            ),
            "reference_application_authorization": authorization,
            "reference_application_authorization_digest": authorization[
                "receipt_digest"
            ],
            "reference_application_journal": journal,
            "reference_application_journal_digest": journal["receipt_digest"],
            "live_snapshot": snapshot,
            "cohort_digest": snapshot["cohort_digest"],
            "snapshot_digest": snapshot["snapshot_digest"],
            "pre_generation": snapshot["generation_before"],
            "evidence_observed_at": snapshot["observed_at"],
            "evidence_max_age_seconds": 3_600,
            "transaction_timeout_seconds": 120,
            "selected_operations": selected,
            "selected_operation_count": 4,
            "selected_status_counts": {"processing": 4},
            "selected_type_counts": {"retain": 4},
            "selected_row_set_digest": digest(
                [
                    {
                        "operation_id": item["operation_id"],
                        "row_digest": item["row_digest"],
                        "task_payload_digest": item[
                            "task_payload_digest"
                        ],
                    }
                    for item in selected
                ]
            ),
            "preserved_status_counts": {"completed": 5, "pending": 39},
            "rollback_backup": backup,
            "rollback_encryption": rollback_encryption(),
            "rollback_backup_path": "/private/tmp/v2-backup.age",
            "rollback_bundle_path": "/private/tmp/v2-bundle.age",
            "authorization_receipt_path": "/private/tmp/v2-auth.json",
            "application_receipt_path": "/private/tmp/v2-app.json",
            "verification_receipt_path": "/private/tmp/v2-verify.json",
            "rollback_receipt_path": "/private/tmp/v2-rollback.json",
            "created_at": 1_786_390_500,
            "expires_at": 1_786_476_900,
        }
        return {**body, "plan_digest": digest(body)}

    def prior_v3_post_abort_plan(self) -> dict:
        reference = self.drain_plan()
        snapshot = self.post_abort_snapshot(
            reference,
            interrupted_processing_count=3,
        )
        authorization = exact_drain_authorization(reference)
        journal = exact_drain_application_journal(reference)
        selected = [
            {
                "operation_id": item["operation_id"],
                "operation_type": item["operation_type"],
                "expected_status": item["current_status"],
                "row_digest": item["row_digest"],
                "task_payload_digest": item["task_payload_digest"],
            }
            for item in snapshot["operations"]
            if item["current_status"] == "processing"
        ]
        backup = rollback_backup_evidence()
        backup["source_authority"]["generation_before"] = snapshot[
            "generation_before"
        ]
        backup["source_authority"]["generation_after"] = snapshot[
            "generation_after"
        ]
        backup["source_authority_digest"] = digest(
            backup["source_authority"]
        )
        body = {
            "schema_version": 3,
            "kind": "operation-recovery-exact-drain-post-abort-plan",
            "action": "recover-exact-drain-post-abort",
            "authority": "unapproved-plan",
            "mutation_authorized": False,
            "candidate_release": release_identity(),
            "installation_authority": snapshot["installation_authority"],
            "reference_plan": reference,
            "reference_plan_digest": reference["plan_digest"],
            "reference_worker_id_digest": next(
                item["worker_id_digest"]
                for item in snapshot["operations"]
                if item["current_status"] == "processing"
            ),
            "reference_application_authorization": authorization,
            "reference_application_authorization_digest": authorization[
                "receipt_digest"
            ],
            "reference_application_journal": journal,
            "reference_application_journal_digest": journal["receipt_digest"],
            "reference_application_progress_digest": "c" * 64,
            "live_snapshot": snapshot,
            "cohort_digest": snapshot["cohort_digest"],
            "snapshot_digest": snapshot["snapshot_digest"],
            "pre_generation": snapshot["generation_before"],
            "evidence_observed_at": snapshot["observed_at"],
            "evidence_max_age_seconds": 3_600,
            "transaction_timeout_seconds": 120,
            "selected_operations": selected,
            "selected_operation_count": 3,
            "selected_status_counts": {"processing": 3},
            "selected_type_counts": {"retain": 3},
            "selected_row_set_digest": digest(
                [
                    {
                        "operation_id": item["operation_id"],
                        "row_digest": item["row_digest"],
                        "task_payload_digest": item[
                            "task_payload_digest"
                        ],
                    }
                    for item in selected
                ]
            ),
            "preserved_status_counts": {"completed": 5, "pending": 40},
            "rollback_backup": backup,
            "rollback_encryption": rollback_encryption(),
            "rollback_backup_path": "/private/tmp/v3-backup.age",
            "rollback_bundle_path": "/private/tmp/v3-bundle.age",
            "authorization_receipt_path": "/private/tmp/v3-auth.json",
            "application_receipt_path": "/private/tmp/v3-app.json",
            "verification_receipt_path": "/private/tmp/v3-verify.json",
            "rollback_receipt_path": "/private/tmp/v3-rollback.json",
            "created_at": 1_786_390_500,
            "expires_at": 1_786_476_900,
        }
        return {**body, "plan_digest": digest(body)}

    def test_prior_post_abort_v2_plan_remains_verifiable(self):
        plan = self.prior_v2_post_abort_plan()

        self.assertEqual(
            verify_post_abort_recovery_plan(
                plan,
                now=1_786_390_500,
            ),
            plan,
        )

    def test_prior_post_abort_v3_plan_remains_verifiable(self):
        plan = self.prior_v3_post_abort_plan()

        self.assertEqual(
            verify_post_abort_recovery_plan(
                plan,
                now=1_786_390_500,
            ),
            plan,
        )

    def test_post_abort_v4_plan_derives_exact_two_interrupted_retains(self):
        reference = self.drain_plan()
        snapshot = self.post_abort_snapshot(
            reference,
            interrupted_processing_count=2,
        )
        backup = rollback_backup_evidence()
        backup["source_authority"]["generation_before"] = (
            "systalyze:public:81678"
        )
        backup["source_authority"]["generation_after"] = (
            "systalyze:public:81678"
        )
        backup["source_authority_digest"] = digest(
            backup["source_authority"]
        )

        plan = create_post_abort_recovery_plan(
            reference,
            snapshot,
            candidate_release=release_identity(),
            rollback_backup=backup,
            rollback_encryption=rollback_encryption(),
            rollback_backup_path="/private/tmp/current-post-abort-backup.age",
            rollback_bundle_path="/private/tmp/current-post-abort-bundle.age",
            authorization_receipt_path="/private/tmp/current-post-abort-auth.json",
            application_receipt_path="/private/tmp/current-post-abort-app.json",
            verification_receipt_path="/private/tmp/current-post-abort-verify.json",
            rollback_receipt_path="/private/tmp/current-post-abort-rollback.json",
            reference_application_authorization=(
                exact_drain_authorization(reference)
            ),
            reference_application_journal=(
                exact_drain_application_journal(reference)
            ),
            reference_application_progress_digest="c" * 64,
            created_at=1_786_390_500,
        )

        expected_ids = {
            item["operation_id"]
            for item in snapshot["operations"]
            if item["current_status"] == "processing"
        }
        self.assertEqual(plan["schema_version"], 4)
        self.assertEqual(
            plan["reference_application_authorization"],
            exact_drain_authorization(reference),
        )
        self.assertEqual(
            plan["reference_application_authorization_digest"],
            exact_drain_authorization(reference)["receipt_digest"],
        )
        self.assertEqual(
            plan["reference_application_progress_digest"],
            "c" * 64,
        )
        self.assertEqual(
            {item["operation_id"] for item in plan["selected_operations"]},
            expected_ids,
        )
        self.assertEqual(plan["selected_operation_count"], 2)
        self.assertEqual(plan["selected_status_counts"], {"processing": 2})
        self.assertEqual(plan["selected_type_counts"], {"retain": 2})
        self.assertEqual(
            plan["preserved_status_counts"],
            {"completed": 5, "pending": 41},
        )
        self.assertEqual(
            verify_post_abort_recovery_plan(plan, now=plan["created_at"]),
            plan,
        )

    def test_post_abort_plan_rejects_a_different_processing_owner(self):
        reference = self.drain_plan()
        snapshot = self.post_abort_snapshot(
            reference,
            interrupted_processing_count=2,
        )
        processing = next(
            item
            for item in snapshot["operations"]
            if item["current_status"] == "processing"
        )
        processing["worker_id_digest"] = "0" * 64
        processing["row_digest"] = digest(
            {
                key: value
                for key, value in processing.items()
                if key != "row_digest"
            }
        )
        snapshot_body = {
            key: value
            for key, value in snapshot.items()
            if key != "snapshot_digest"
        }
        snapshot["snapshot_digest"] = digest(snapshot_body)
        backup = rollback_backup_evidence()
        backup["source_authority"]["generation_before"] = (
            "systalyze:public:81678"
        )
        backup["source_authority"]["generation_after"] = (
            "systalyze:public:81678"
        )
        backup["source_authority_digest"] = digest(
            backup["source_authority"]
        )

        with self.assertRaisesRegex(
            OperationRecoveryError,
            "post-abort row set is invalid",
        ):
            create_post_abort_recovery_plan(
                reference,
                snapshot,
                candidate_release=release_identity(),
                rollback_backup=backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/post-abort-backup.age",
                rollback_bundle_path="/private/tmp/post-abort-bundle.age",
                authorization_receipt_path="/private/tmp/post-abort-auth.json",
                application_receipt_path="/private/tmp/post-abort-app.json",
                verification_receipt_path="/private/tmp/post-abort-verify.json",
                rollback_receipt_path="/private/tmp/post-abort-rollback.json",
                reference_application_authorization=(
                    exact_drain_authorization(reference)
                ),
                reference_application_journal=(
                    exact_drain_application_journal(reference)
                ),
                reference_application_progress_digest="c" * 64,
                created_at=1_786_390_500,
            )

    def test_post_abort_v4_planner_rejects_non_exact_current_shapes(self):
        reference = self.drain_plan()
        base = self.post_abort_snapshot(
            reference,
            interrupted_processing_count=2,
        )
        worker_digest = next(
            item["worker_id_digest"]
            for item in base["operations"]
            if item["current_status"] == "processing"
        )

        def reseal(snapshot, *changed_rows):
            for row in changed_rows:
                row_body = {
                    key: value
                    for key, value in row.items()
                    if key != "row_digest"
                }
                row["row_digest"] = digest(row_body)
            snapshot["operations"].sort(
                key=lambda item: item["operation_id"]
            )
            snapshot["status_counts"] = {
                status: sum(
                    item["current_status"] == status
                    for item in snapshot["operations"]
                )
                for status in recovery_contract.OPERATION_STATUSES
            }
            snapshot_body = {
                key: value
                for key, value in snapshot.items()
                if key != "snapshot_digest"
            }
            snapshot["snapshot_digest"] = digest(snapshot_body)
            return snapshot

        fourth = deepcopy(base)
        fourth_row = next(
            item
            for item in fourth["operations"]
            if item["current_status"] == "pending"
        )
        fourth_row.update(
            {
                "current_status": "processing",
                "worker_id_present": True,
                "worker_id_digest": worker_digest,
                "claimed_at": "2026-08-10T10:02:00.000000Z",
            }
        )

        second = deepcopy(base)
        second_row = next(
            item
            for item in second["operations"]
            if item["current_status"] == "processing"
        )
        second_row.update(
            {
                "current_status": "pending",
                "worker_id_present": False,
                "worker_id_digest": None,
                "claimed_at": None,
            }
        )

        failed = deepcopy(base)
        failed_row = next(
            item
            for item in failed["operations"]
            if item["current_status"] == "processing"
        )
        failed_row.update(
            {
                "current_status": "failed",
                "completed_at": "2026-08-10T10:03:00.000000Z",
                "retry_count": 3,
                "worker_id_present": False,
                "worker_id_digest": None,
                "claimed_at": None,
                "error_category": "unknown",
                "error_digest": "6" * 64,
            }
        )

        refresh = deepcopy(base)
        refresh_row = next(
            item
            for item in refresh["operations"]
            if item["current_status"] == "processing"
        )
        refresh_row["operation_type"] = "refresh_mental_model"

        completed = deepcopy(base)
        completed_row = next(
            item
            for item in completed["operations"]
            if item["current_status"] == "completed"
        )
        completed_row["updated_at"] = "2026-08-10T10:04:00.000000Z"

        claimed_pending = deepcopy(base)
        claimed_pending_row = next(
            item
            for item in claimed_pending["operations"]
            if item["current_status"] == "pending"
        )
        claimed_pending_row.update(
            {
                "worker_id_present": True,
                "worker_id_digest": worker_digest,
                "claimed_at": "2026-08-10T10:05:00.000000Z",
            }
        )

        nonreference = deepcopy(base)
        nonreference_row = nonreference["operations"][0]
        nonreference_row["operation_id"] = (
            "ffffffff-ffff-4fff-8fff-ffffffffffff"
        )

        wrong_cohort = deepcopy(base)
        wrong_cohort["cohort_digest"] = "0" * 64
        wrong_generation = deepcopy(base)
        wrong_generation["generation_after"] = "systalyze:public:81679"

        cases = {
            "fourth-processing": reseal(fourth, fourth_row),
            "second-processing": reseal(second, second_row),
            "failed": reseal(failed, failed_row),
            "refresh": reseal(refresh, refresh_row),
            "changed-completed": reseal(completed, completed_row),
            "claimed-pending": reseal(
                claimed_pending,
                claimed_pending_row,
            ),
            "nonreference-membership": reseal(
                nonreference,
                nonreference_row,
            ),
            "wrong-cohort": reseal(wrong_cohort),
            "wrong-generation": reseal(wrong_generation),
        }
        for label, snapshot in cases.items():
            backup = rollback_backup_evidence()
            backup["source_authority"]["generation_before"] = snapshot[
                "generation_before"
            ]
            backup["source_authority"]["generation_after"] = snapshot[
                "generation_after"
            ]
            backup["source_authority_digest"] = digest(
                backup["source_authority"]
            )
            with (
                self.subTest(shape=label),
                self.assertRaises(OperationRecoveryError),
            ):
                create_post_abort_recovery_plan(
                    reference,
                    snapshot,
                    candidate_release=release_identity(),
                    rollback_backup=backup,
                    rollback_encryption=rollback_encryption(),
                    rollback_backup_path="/private/tmp/reject-backup.age",
                    rollback_bundle_path="/private/tmp/reject-bundle.age",
                    authorization_receipt_path="/private/tmp/reject-auth.json",
                    application_receipt_path="/private/tmp/reject-app.json",
                    verification_receipt_path="/private/tmp/reject-verify.json",
                    rollback_receipt_path="/private/tmp/reject-rollback.json",
                    reference_application_authorization=(
                        exact_drain_authorization(reference)
                    ),
                    reference_application_journal=(
                        exact_drain_application_journal(reference)
                    ),
                    reference_application_progress_digest="c" * 64,
                    created_at=1_786_390_500,
                )

    def test_post_abort_v4_verifier_rejects_schema_and_bound_set_tampering(self):
        reference = self.drain_plan()
        snapshot = self.post_abort_snapshot(
            reference,
            interrupted_processing_count=2,
        )
        backup = rollback_backup_evidence()
        backup["source_authority"]["generation_before"] = snapshot[
            "generation_before"
        ]
        backup["source_authority"]["generation_after"] = snapshot[
            "generation_after"
        ]
        backup["source_authority_digest"] = digest(
            backup["source_authority"]
        )
        plan = dict(
            create_post_abort_recovery_plan(
                reference,
                snapshot,
                candidate_release=release_identity(),
                rollback_backup=backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/tamper-backup.age",
                rollback_bundle_path="/private/tmp/tamper-bundle.age",
                authorization_receipt_path="/private/tmp/tamper-auth.json",
                application_receipt_path="/private/tmp/tamper-app.json",
                verification_receipt_path="/private/tmp/tamper-verify.json",
                rollback_receipt_path="/private/tmp/tamper-rollback.json",
                reference_application_authorization=(
                    exact_drain_authorization(reference)
                ),
                reference_application_journal=(
                    exact_drain_application_journal(reference)
                ),
                reference_application_progress_digest="c" * 64,
                created_at=1_786_390_500,
            )
        )

        def reseal(candidate):
            body = {
                key: value
                for key, value in candidate.items()
                if key != "plan_digest"
            }
            candidate["plan_digest"] = digest(body)
            return candidate

        boolean_schema = deepcopy(plan)
        boolean_schema["schema_version"] = True
        unsupported_schema = deepcopy(plan)
        unsupported_schema["schema_version"] = 5
        unknown_key = deepcopy(plan)
        unknown_key["unexpected"] = True
        selected = deepcopy(plan)
        selected["selected_operations"][0]["row_digest"] = "0" * 64
        count = deepcopy(plan)
        count["selected_operation_count"] = 5
        row_set = deepcopy(plan)
        row_set["selected_row_set_digest"] = "0" * 64
        plan_digest = deepcopy(plan)
        plan_digest["plan_digest"] = "0" * 64
        cases = {
            "boolean-schema": boolean_schema,
            "unsupported-schema": unsupported_schema,
            "unknown-key": reseal(unknown_key),
            "selected": reseal(selected),
            "count": reseal(count),
            "row-set-digest": reseal(row_set),
            "plan-digest": plan_digest,
        }
        for label, candidate in cases.items():
            with (
                self.subTest(tamper=label),
                self.assertRaises(OperationRecoveryError),
            ):
                verify_post_abort_recovery_plan(
                    candidate,
                    now=plan["created_at"],
                )

    def test_post_abort_v2_verifier_rejects_forged_reference_authority(self):
        reference = self.drain_plan()
        snapshot = self.post_abort_snapshot(
            reference,
            interrupted_processing_count=2,
        )
        backup = rollback_backup_evidence()
        backup["source_authority"]["generation_before"] = snapshot[
            "generation_before"
        ]
        backup["source_authority"]["generation_after"] = snapshot[
            "generation_after"
        ]
        backup["source_authority_digest"] = digest(
            backup["source_authority"]
        )
        plan = dict(
            create_post_abort_recovery_plan(
                reference,
                snapshot,
                candidate_release=release_identity(),
                rollback_backup=backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/authority-backup.age",
                rollback_bundle_path="/private/tmp/authority-bundle.age",
                authorization_receipt_path="/private/tmp/authority-auth.json",
                application_receipt_path="/private/tmp/authority-app.json",
                verification_receipt_path="/private/tmp/authority-verify.json",
                rollback_receipt_path="/private/tmp/authority-rollback.json",
                reference_application_authorization=(
                    exact_drain_authorization(reference)
                ),
                reference_application_journal=(
                    exact_drain_application_journal(reference)
                ),
                reference_application_progress_digest="c" * 64,
                created_at=1_786_390_500,
            )
        )

        def reseal(candidate, *, authorization=False):
            journal = candidate["reference_application_journal"]
            if authorization:
                receipt = candidate["reference_application_authorization"]
                receipt["receipt_digest"] = digest(
                    {
                        key: value
                        for key, value in receipt.items()
                        if key != "receipt_digest"
                    }
                )
                candidate["reference_application_authorization_digest"] = (
                    receipt["receipt_digest"]
                )
                journal["authorization_receipt_digest"] = receipt[
                    "receipt_digest"
                ]
            journal["receipt_digest"] = digest(
                {
                    key: value
                    for key, value in journal.items()
                    if key != "receipt_digest"
                }
            )
            candidate["reference_application_journal_digest"] = journal[
                "receipt_digest"
            ]
            candidate["plan_digest"] = digest(
                {
                    key: value
                    for key, value in candidate.items()
                    if key != "plan_digest"
                }
            )
            return candidate

        forged_authorization = deepcopy(plan)
        forged_authorization["reference_application_authorization"][
            "candidate_release"
        ]["release_digest"] = "0" * 64
        started_at = deepcopy(plan)
        started_at["reference_application_journal"]["started_at"] += 1
        whitespace_start = deepcopy(plan)
        whitespace_start["reference_application_journal"][
            "worker_start_time"
        ] = " dead-exact-drain-worker  "
        invalid_pid = deepcopy(plan)
        invalid_pid["reference_application_journal"]["worker_pid"] = (
            2_147_483_648
        )
        invalid_attempt = deepcopy(plan)
        invalid_attempt["reference_application_journal"]["worker_attempt"] = 6
        invalid_receipt = deepcopy(plan)
        invalid_receipt["reference_application_journal"][
            "receipt_digest"
        ] = "0" * 64
        invalid_receipt["reference_application_journal_digest"] = "0" * 64
        invalid_receipt["plan_digest"] = digest(
            {
                key: value
                for key, value in invalid_receipt.items()
                if key != "plan_digest"
            }
        )
        cases = {
            "forged-authorization": reseal(
                forged_authorization,
                authorization=True,
            ),
            "started-at": reseal(started_at),
            "worker-start-time": reseal(whitespace_start),
            "worker-pid": reseal(invalid_pid),
            "worker-attempt": reseal(invalid_attempt),
            "journal-receipt": invalid_receipt,
        }
        for label, candidate in cases.items():
            with (
                self.subTest(forgery=label),
                self.assertRaises(OperationRecoveryError),
            ):
                verify_post_abort_recovery_plan(
                    candidate,
                    now=plan["created_at"],
                )

    def test_exact_drain_plan_binds_the_43_pending_operations(self):
        planned_at = 1_785_462_000
        plan = recovery_contract.create_exact_drain_plan(
            self.cohort(),
            self.drain_snapshot(),
            candidate_release=release_identity(),
            rollback_backup=drain_backup_evidence(),
            rollback_backup_path="/private/tmp/drain-backup.age",
            provider_policy_digest="9" * 64,
            effective_profile_digest="7" * 64,
            worker_runtime_digest="8" * 64,
            authorization_receipt_path="/private/tmp/drain-authorization.json",
            application_receipt_path="/private/tmp/drain-application.json",
            status_artifact_path="/private/tmp/drain-status.json",
            verification_receipt_path="/private/tmp/drain-verification.json",
            created_at=planned_at,
        )

        self.assertEqual(plan["kind"], "operation-recovery-exact-drain-plan")
        self.assertEqual(plan["selected_operation_count"], 43)
        self.assertEqual(
            plan["selected_type_counts"],
            {"retain": 40, "refresh_mental_model": 2, "consolidation": 1},
        )
        self.assertEqual(plan["effective_profile_digest"], "7" * 64)
        self.assertEqual(plan["worker_max_attempts"], 4)
        self.assertEqual(plan["worker_max_retries"], 3)
        self.assertEqual(plan["selected_status_counts"], {"pending": 43})
        self.assertEqual(plan["preserved_status_counts"], {"completed": 5})
        self.assertEqual(plan["pre_generation"], "systalyze:public:124")
        self.assertEqual(
            plan["progress_artifact_path"],
            "/private/tmp/drain-progress.json",
        )
        self.assertEqual(
            recovery_contract.verify_exact_drain_plan(plan, now=planned_at),
            plan,
        )

    def test_exact_drain_plan_binds_only_the_pending_remainder(self):
        planned_at = 1_785_462_000
        plan = recovery_contract.create_exact_drain_plan(
            self.cohort(),
            self.drain_snapshot(completed_positions={0, 1, 2, 42, 43, 46}),
            candidate_release=release_identity(),
            rollback_backup=drain_backup_evidence(),
            rollback_backup_path="/private/tmp/remainder-backup.age",
            provider_policy_digest="9" * 64,
            effective_profile_digest="7" * 64,
            worker_runtime_digest="8" * 64,
            authorization_receipt_path="/private/tmp/remainder-authorization.json",
            application_receipt_path="/private/tmp/remainder-application.json",
            status_artifact_path="/private/tmp/remainder-status.json",
            verification_receipt_path="/private/tmp/remainder-verification.json",
            created_at=planned_at,
        )

        self.assertEqual(plan["selected_operation_count"], 42)
        self.assertEqual(plan["selected_status_counts"], {"pending": 42})
        self.assertEqual(plan["preserved_status_counts"], {"completed": 6})
        self.assertEqual(
            recovery_contract.verify_exact_drain_plan(plan, now=planned_at),
            plan,
        )
        serialized = json.dumps(plan, sort_keys=True)
        self.assertNotIn('"task_payload":', serialized)
        self.assertNotIn('"worker_id":', serialized)
        self.assertNotIn('"error_message":', serialized)
        self.assertNotIn('"result_metadata":', serialized)

    def test_exact_drain_worker_requires_the_exact_authorization_receipt(self):
        now = int(__import__("time").time())
        snapshot = self.drain_snapshot(observed_at=now)
        plan = recovery_contract.create_exact_drain_plan(
            self.cohort(),
            snapshot,
            candidate_release=release_identity(),
            rollback_backup=drain_backup_evidence(),
            rollback_backup_path="/private/tmp/drain-backup.age",
            provider_policy_digest="9" * 64,
            effective_profile_digest="7" * 64,
            worker_runtime_digest="8" * 64,
            authorization_receipt_path=(
                "/private/tmp/drain-authorization.json"
            ),
            application_receipt_path="/private/tmp/drain-application.json",
            status_artifact_path="/private/tmp/drain-status.json",
            verification_receipt_path=(
                "/private/tmp/drain-verification.json"
            ),
            created_at=now,
        )
        body = {
            "schema_version": 1,
            "kind": "operation-recovery-exact-drain-authorization-receipt",
            "plan_digest": plan["plan_digest"],
            "approval_digest": plan["plan_digest"],
            "candidate_release": plan["candidate_release"],
            "provider_policy_digest": plan["provider_policy_digest"],
            "worker_runtime_digest": plan["worker_runtime_digest"],
            "authorized_at": now,
        }
        receipt = {**body, "receipt_digest": digest(body)}
        self.assertEqual(
            verify_exact_drain_authorization_receipt(receipt, plan=plan),
            receipt,
        )
        changed = deepcopy(receipt)
        changed["approval_digest"] = "0" * 64
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "authorization receipt is invalid",
        ):
            verify_exact_drain_authorization_receipt(changed, plan=plan)

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
        reference_plan=None,
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
        reference_plan = reference_plan or self.requeue_plan()
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

    def permitted_blocker_rows(self, reference_plan=None) -> list[dict]:
        reference_plan = reference_plan or self.requeue_plan()
        live_by_id = {
            row["operation_id"]: row
            for row in reference_plan["live_snapshot"]["operations"]
        }
        rows = []
        for index, selected in enumerate(reference_plan["selected_operations"]):
            live = live_by_id[selected["operation_id"]]
            body = {
                "operation_id": selected["operation_id"],
                "bank_id": "engineering",
                "operation_type": selected["operation_type"],
                "status": selected["expected_status"],
                "created_at": live["created_at"],
                "updated_at": live["updated_at"],
                "completed_at": live["completed_at"],
                "retry_count": live["retry_count"],
                "next_retry_at": live["next_retry_at"],
                "worker_id_present": live["worker_id_present"],
                "worker_id_digest": live["worker_id_digest"],
                "claimed_at": live["claimed_at"],
                "task_payload_present": True,
                "task_payload_digest": selected["task_payload_digest"],
                "in_reference_cohort": True,
                "in_reference_selected_set": True,
                "blocker_reason": f"claimed_{selected['expected_status']}",
            }
            rows.append(
                {
                    **body,
                    "row_digest": digest(body),
                    "nonclaim_state_digest": f"{index + 900:064x}",
                    "reference_row_digest": selected["row_digest"],
                }
            )
        rows.sort(key=lambda row: (row["created_at"], row["operation_id"]))
        return rows

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
        self.assertEqual(
            snapshot["status_counts"]["failed"],
            len(PERMITTED_POSITIONS),
        )
        self.assertEqual(snapshot["status_counts"]["cancelled"], 0)
        self.assertEqual(snapshot["status_counts"]["completed"], 6)
        self.assertEqual(
            snapshot["status_counts"]["pending"],
            sum(EXPECTED_COUNTS.values()) - len(PERMITTED_POSITIONS) - 6,
        )
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

    def test_plan_requeues_the_exact_failed_rows(self):
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

        self.assertEqual(
            len(plan["selected_operations"]),
            len(PERMITTED_POSITIONS),
        )
        self.assertEqual(
            {item["expected_status"] for item in plan["selected_operations"]},
            {"failed"},
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

    def test_claim_release_permits_a_bound_cancelled_reference_row(self):
        planned_at = 1_785_460_800
        reference_plan = self.requeue_plan(
            self.live_snapshot(cancelled_positions=frozenset({7}))
        )
        predecessor, live, nonclaim_digests = self.claim_release_inputs(
            planned_at=planned_at,
            reference_plan=reference_plan,
        )
        permitted_rows = self.permitted_blocker_rows(reference_plan)

        plan = create_claim_release_plan(
            predecessor,
            live,
            reference_plan=reference_plan,
            permitted_blocker_rows=permitted_rows,
            nonclaim_state_digests=nonclaim_digests,
            candidate_release=release_identity(),
            installation_authority=installation_authority(),
            rollback_encryption=rollback_encryption(),
            rollback_bundle_path="/private/tmp/cancelled.bundle.json",
            authorization_receipt_path="/private/tmp/cancelled.authorization.json",
            application_receipt_path="/private/tmp/cancelled.application.json",
            verification_receipt_path="/private/tmp/cancelled.verification.json",
            rollback_receipt_path="/private/tmp/cancelled.rollback.json",
            created_at=planned_at,
        )

        self.assertEqual(
            {row["status"] for row in plan["permitted_blocker_rows"]},
            {"failed", "cancelled"},
        )
        self.assertEqual(
            verify_claim_release_plan(plan, now=planned_at),
            plan,
        )

    def test_claim_release_plan_is_closed_unapproved_and_exactly_bound(self):
        predecessor, live, nonclaim_digests = self.claim_release_inputs(
            live_generation="systalyze:public:123"
        )
        candidate = release_identity()
        reference_plan = self.requeue_plan()
        permitted_rows = self.permitted_blocker_rows(reference_plan)

        plan = create_claim_release_plan(
            predecessor,
            live,
            reference_plan=reference_plan,
            permitted_blocker_rows=permitted_rows,
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
        self.assertEqual(
            plan["permitted_blocker_count"],
            len(PERMITTED_POSITIONS),
        )
        self.assertEqual(
            plan["reference_plan_digest"],
            reference_plan["plan_digest"],
        )
        self.assertEqual(plan["permitted_blocker_rows"], permitted_rows)
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

    def test_claim_release_plan_derives_permitted_count_from_reference(self):
        planned_at = 1_785_460_800
        reduced_positions = frozenset({0, 42})
        reference_plan = self.requeue_plan(
            self.live_snapshot(failed_positions=reduced_positions)
        )
        predecessor, live, nonclaim_digests = self.claim_release_inputs(
            planned_at=planned_at,
            live_generation="systalyze:public:123",
            reference_plan=reference_plan,
        )
        permitted_rows = self.permitted_blocker_rows(reference_plan)

        plan = create_claim_release_plan(
            predecessor,
            live,
            reference_plan=reference_plan,
            permitted_blocker_rows=permitted_rows,
            nonclaim_state_digests=nonclaim_digests,
            candidate_release=release_identity(),
            installation_authority=installation_authority(),
            rollback_encryption=rollback_encryption(),
            rollback_bundle_path="/private/tmp/dynamic.bundle.json",
            authorization_receipt_path="/private/tmp/dynamic.authorization.json",
            application_receipt_path="/private/tmp/dynamic.application.json",
            verification_receipt_path="/private/tmp/dynamic.verification.json",
            rollback_receipt_path="/private/tmp/dynamic.rollback.json",
            created_at=planned_at,
        )

        self.assertEqual(plan["permitted_blocker_count"], len(reduced_positions))
        self.assertEqual(
            verify_claim_release_plan(plan, now=planned_at),
            plan,
        )

    def test_claim_release_plan_binds_fresh_generation_not_predecessor(self):
        planned_at = 1_785_460_800
        predecessor, live, nonclaim_digests = self.claim_release_inputs(
            planned_at=planned_at
        )

        plan = create_claim_release_plan(
            predecessor,
            live,
            reference_plan=self.requeue_plan(),
            permitted_blocker_rows=self.permitted_blocker_rows(),
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

    def test_claim_release_plan_rejects_permitted_blocker_set_drift(self):
        planned_at = 1_785_460_800
        predecessor, live, nonclaim_digests = self.claim_release_inputs(
            planned_at=planned_at
        )
        reference_plan = self.requeue_plan()
        base_rows = self.permitted_blocker_rows(reference_plan)
        kwargs = {
            "reference_plan": reference_plan,
            "nonclaim_state_digests": nonclaim_digests,
            "candidate_release": release_identity(),
            "installation_authority": installation_authority(),
            "rollback_encryption": rollback_encryption(),
            "rollback_bundle_path": "/private/tmp/claim-release.bundle.json",
            "authorization_receipt_path": (
                "/private/tmp/claim-release.authorization.json"
            ),
            "application_receipt_path": (
                "/private/tmp/claim-release.application.json"
            ),
            "verification_receipt_path": (
                "/private/tmp/claim-release.verification.json"
            ),
            "rollback_receipt_path": (
                "/private/tmp/claim-release.rollback.json"
            ),
            "created_at": planned_at,
        }

        duplicate_rows = deepcopy(base_rows)
        duplicate_rows[-1] = deepcopy(duplicate_rows[0])
        outside_reference_rows = deepcopy(base_rows)
        outside_reference_rows[0]["operation_id"] = live["blockers"][0][
            "operation_id"
        ]
        outside_reference_body = {
            key: value
            for key, value in outside_reference_rows[0].items()
            if key
            not in {
                "row_digest",
                "nonclaim_state_digest",
                "reference_row_digest",
            }
        }
        outside_reference_rows[0]["row_digest"] = digest(
            outside_reference_body
        )
        reference_drift_rows = deepcopy(base_rows)
        reference_drift_rows[0]["reference_row_digest"] = "0" * 64

        for label, rows in (
            ("duplicate", duplicate_rows),
            ("outside reference set", outside_reference_rows),
            ("reference digest", reference_drift_rows),
        ):
            with self.subTest(label=label), self.assertRaisesRegex(
                OperationRecoveryError,
                "permitted blocker evidence differs",
            ):
                create_claim_release_plan(
                    predecessor,
                    live,
                    permitted_blocker_rows=rows,
                    **kwargs,
                )

    def test_claim_release_plan_verification_rejects_tampering_and_expiry(self):
        planned_at = 1_785_460_800
        predecessor, live, nonclaim_digests = self.claim_release_inputs(
            planned_at=planned_at
        )
        plan = dict(
            create_claim_release_plan(
                predecessor,
                live,
                reference_plan=self.requeue_plan(),
                permitted_blocker_rows=self.permitted_blocker_rows(),
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

        permitted_tampered = deepcopy(plan)
        permitted_row = permitted_tampered["permitted_blocker_rows"][0]
        permitted_row["operation_id"] = (
            "ffffffff-ffff-4fff-8fff-ffffffffffff"
        )
        permitted_row_body = {
            key: value
            for key, value in permitted_row.items()
            if key
            not in {
                "row_digest",
                "nonclaim_state_digest",
                "reference_row_digest",
            }
        }
        permitted_row["row_digest"] = digest(permitted_row_body)
        permitted_tampered["permitted_blocker_row_set_digest"] = digest(
            [
                {
                    "operation_id": row["operation_id"],
                    "row_digest": row["row_digest"],
                    "nonclaim_state_digest": row["nonclaim_state_digest"],
                    "reference_row_digest": row["reference_row_digest"],
                }
                for row in permitted_tampered["permitted_blocker_rows"]
            ]
        )
        permitted_body = {
            key: value
            for key, value in permitted_tampered.items()
            if key != "plan_digest"
        }
        permitted_tampered["plan_digest"] = digest(permitted_body)
        with self.assertRaisesRegex(OperationRecoveryError, "plan is invalid"):
            verify_claim_release_plan(permitted_tampered, now=planned_at)


if __name__ == "__main__":
    unittest.main()
