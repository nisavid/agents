from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from hindsight_memory_control_plane.canonical import digest  # noqa: E402
import hindsight_memory_control_plane.operation_recovery as recovery_contract  # noqa: E402
from hindsight_memory_control_plane.operation_recovery import (  # noqa: E402
    OperationRecoveryError,
    create_claim_release_plan,
    create_checkpoint_continuation_handoff,
    create_cohort_manifest,
    create_global_queue_blocker_classification,
    create_live_snapshot,
    create_post_abort_recovery_plan,
    create_requeue_plan,
    normalize_pg0_binding,
    verify_cohort_manifest,
    verify_claim_release_plan,
    verify_checkpoint_continuation_handoff,
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


def rebound_installation_authority() -> dict:
    body = {
        "schema_version": 1,
        "kind": "operation-recovery-verified-data-identity-rebind-handoff",
        "plan_digest": "a" * 64,
        "authorization_receipt_digest": "b" * 64,
        "application_receipt_digest": "c" * 64,
        "verification_receipt_digest": "d" * 64,
        "rollback_bundle_digest": "e" * 64,
        "installation_state_digest_before": "f" * 64,
        "installation_state_digest_after": "6" * 64,
        "binding_generation_digest": "1" * 64,
        "current_release_digest": "2" * 64,
        "old_data_identity_digest": "4" * 64,
        "reference_observed_data_identity_digest": "5" * 64,
        "new_data_identity_digest": "7" * 64,
        "postgres_system_identifier": "7659746962107358086",
        "database_continuity_digest": "8" * 64,
        "post_evidence_digest": "9" * 64,
        "verified_at": 1_786_820_204,
    }
    authority = installation_authority()
    authority.update(
        schema_version=2,
        install_state_digest=body["installation_state_digest_after"],
        recorded_data_identity_digest=body["new_data_identity_digest"],
        observed_data_identity_digest=body["new_data_identity_digest"],
        data_identity_rebind_handoff={
            **body,
            "handoff_digest": digest(body),
        },
    )
    return authority


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
        schema_version: int = 10,
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
                schema_version=schema_version,
            )
        )

    def standing_grant_fixture(
        self,
        *,
        maximum_plan_claims: int = 3,
        maximum_worker_attempts: int | None = None,
        maximum_execution_seconds: int | None = None,
        created_at: int = 1_785_462_000,
        expires_at: int | None = None,
        artifact_root: str = "/private/tmp",
        snapshot: dict | None = None,
    ) -> tuple[dict, dict, dict, object]:
        grant_expires_at = (
            created_at + 172_800 if expires_at is None else expires_at
        )
        capability = recovery_contract.create_hatchery_capability_receipt(
            provider_policy_digest="9" * 64,
            provider_identity_digest="6" * 64,
            model_digest="5" * 64,
            observed_at=created_at,
            successful=True,
        )

        def create_plan(
            *,
            grant=None,
            predecessor=None,
            planned_at=created_at,
            candidate_release=None,
            provider_policy_digest="9" * 64,
            worker_runtime_digest="8" * 64,
        ):
            return dict(
                recovery_contract.create_exact_drain_plan(
                    self.cohort(),
                    (
                        self.drain_snapshot(observed_at=planned_at)
                        if snapshot is None
                        else snapshot
                    ),
                    candidate_release=(
                        release_identity()
                        if candidate_release is None
                        else candidate_release
                    ),
                    rollback_backup=drain_backup_evidence(),
                    rollback_backup_path="/private/tmp/drain-backup.age",
                    provider_policy_digest=provider_policy_digest,
                    effective_profile_digest="7" * 64,
                    worker_runtime_digest=worker_runtime_digest,
                    authorization_receipt_path=(
                        f"{artifact_root}/drain-{planned_at}-authorization.json"
                    ),
                    application_receipt_path=(
                        f"{artifact_root}/drain-{planned_at}-application.json"
                    ),
                    status_artifact_path=(
                        f"{artifact_root}/drain-{planned_at}-status.json"
                    ),
                    verification_receipt_path=(
                        f"{artifact_root}/drain-{planned_at}-verification.json"
                    ),
                    hatchery_capability_receipt=(
                        capability
                        if provider_policy_digest == "9" * 64
                        else recovery_contract.create_hatchery_capability_receipt(
                            provider_policy_digest=provider_policy_digest,
                            provider_identity_digest="6" * 64,
                            model_digest="5" * 64,
                            observed_at=created_at,
                            successful=True,
                        )
                    ),
                    authorization_grant=grant,
                    grant_predecessor_plan_digest=predecessor,
                    created_at=planned_at,
                    schema_version=15 if grant is not None else 13,
                )
            )

        reference = create_plan()
        worker_budget = (
            (reference["worker_max_attempts"] + 1) * maximum_plan_claims
            if maximum_worker_attempts is None
            else maximum_worker_attempts
        )
        execution_budget = (
            recovery_contract.EXACT_DRAIN_EXECUTION_WINDOW_MAX_SECONDS
            * maximum_plan_claims
            if maximum_execution_seconds is None
            else maximum_execution_seconds
        )
        grant_plan = (
            recovery_contract.create_exact_drain_authorization_grant_plan(
                reference,
                grant_id="44444444-4444-4444-8444-444444444444",
                maximum_recovery_epoch=3,
                maximum_reconciliation_cycle=1,
                maximum_plan_claims=maximum_plan_claims,
                maximum_worker_attempts=worker_budget,
                maximum_execution_seconds=execution_budget,
                maximum_concurrent_drains=1,
                created_at=created_at,
                expires_at=grant_expires_at,
            )
        )
        grant = dict(
            recovery_contract.activate_exact_drain_authorization_grant(
                grant_plan,
                approval_digest=grant_plan["grant_plan_digest"],
                approved_at=created_at,
            )
        )
        first = create_plan(
            grant=grant,
            predecessor=reference["plan_digest"],
        )
        return reference, grant, first, create_plan

    def legacy_drain_plan(
        self,
        *,
        snapshot: dict | None = None,
        created_at: int = 1_785_462_000,
    ) -> dict:
        current = self.drain_plan(
            snapshot=snapshot,
            created_at=created_at,
        )
        body = {
            key: deepcopy(value)
            for key, value in current.items()
            if key
            not in {
                "plan_digest",
                "execution_window",
                "recovery_context",
                "recovery_context_digest",
            }
        }
        body["schema_version"] = 9
        body["execution_lease_seconds"] = (
            recovery_contract.EXACT_DRAIN_EXECUTION_LEASE_SECONDS
        )
        return {**body, "plan_digest": digest(body)}

    def test_exact_drain_grant_plan_is_closed_and_operator_approved_once(self):
        reference, _prior_grant, _prior_plan, _create_plan = (
            self.standing_grant_fixture()
        )
        created_at = reference["created_at"]

        with self.assertRaisesRegex(
            OperationRecoveryError,
            "grant reference is invalid",
        ):
            recovery_contract.create_exact_drain_authorization_grant_plan(
                self.drain_plan(schema_version=12),
                grant_id="00000000-0000-4000-8000-000000000001",
                maximum_recovery_epoch=3,
                maximum_reconciliation_cycle=1,
                maximum_plan_claims=1,
                maximum_worker_attempts=5,
                maximum_execution_seconds=reference["execution_window"][
                    "calculated_seconds"
                ],
                maximum_concurrent_drains=1,
                created_at=created_at,
                expires_at=created_at + 172_800,
            )

        grant_plan = dict(
            recovery_contract.create_exact_drain_authorization_grant_plan(
                reference,
                grant_id="11111111-1111-4111-8111-111111111111",
                maximum_recovery_epoch=3,
                maximum_reconciliation_cycle=1,
                maximum_plan_claims=3,
                maximum_worker_attempts=12,
                maximum_execution_seconds=(
                    reference["execution_window"]["calculated_seconds"] * 3
                ),
                maximum_concurrent_drains=1,
                created_at=created_at,
                expires_at=created_at + 172_800,
            )
        )

        self.assertEqual(grant_plan["authority"], "unapproved-plan")
        self.assertFalse(grant_plan["mutation_authorized"])
        self.assertEqual(grant_plan["scope"]["operation"], "exact-drain")
        self.assertEqual(
            grant_plan["scope"]["initial_reference_plan_digest"],
            reference["plan_digest"],
        )
        self.assertEqual(
            grant_plan["scope"]["installation_authority_digest"],
            digest(reference["installation_authority"]),
        )
        self.assertEqual(
            grant_plan["scope"]["cohort_digest"],
            reference["cohort_digest"],
        )
        self.assertEqual(
            recovery_contract.verify_exact_drain_authorization_grant_plan(
                grant_plan,
                now=created_at,
            ),
            grant_plan,
        )

        grant = dict(
            recovery_contract.activate_exact_drain_authorization_grant(
                grant_plan,
                approval_digest=grant_plan["grant_plan_digest"],
                approved_at=created_at,
            )
        )
        self.assertEqual(grant["grant_id"], grant_plan["grant_id"])
        self.assertEqual(
            grant["approval_digest"], grant_plan["grant_plan_digest"]
        )
        self.assertEqual(
            recovery_contract.verify_exact_drain_authorization_grant(
                grant,
                now=created_at,
            ),
            grant,
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "exact drain grant approval differs",
        ):
            recovery_contract.activate_exact_drain_authorization_grant(
                grant_plan,
                approval_digest="0" * 64,
                approved_at=created_at,
            )

    def test_schema_fifteen_plan_binds_grant_and_rejects_scope_drift(self):
        created_at = 1_785_462_000
        capability = recovery_contract.create_hatchery_capability_receipt(
            provider_policy_digest="9" * 64,
            provider_identity_digest="6" * 64,
            model_digest="5" * 64,
            observed_at=created_at,
            successful=True,
        )

        def create_plan(
            *,
            candidate_release,
            grant=None,
            predecessor=None,
            provider_policy_digest="9" * 64,
            worker_runtime_digest="8" * 64,
        ):
            return dict(
                recovery_contract.create_exact_drain_plan(
                    self.cohort(),
                    self.drain_snapshot(),
                    candidate_release=candidate_release,
                    rollback_backup=drain_backup_evidence(),
                    rollback_backup_path="/private/tmp/drain-backup.age",
                    provider_policy_digest=provider_policy_digest,
                    effective_profile_digest="7" * 64,
                    worker_runtime_digest=worker_runtime_digest,
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
                    hatchery_capability_receipt=(
                        capability
                        if provider_policy_digest == "9" * 64
                        else recovery_contract.create_hatchery_capability_receipt(
                            provider_policy_digest=provider_policy_digest,
                            provider_identity_digest="6" * 64,
                            model_digest="5" * 64,
                            observed_at=created_at,
                            successful=True,
                        )
                    ),
                    authorization_grant=grant,
                    grant_predecessor_plan_digest=predecessor,
                    created_at=created_at,
                    schema_version=15 if grant is not None else 13,
                )
            )

        reference = create_plan(candidate_release=release_identity())
        grant_plan = (
            recovery_contract.create_exact_drain_authorization_grant_plan(
                reference,
                grant_id="22222222-2222-4222-8222-222222222222",
                maximum_recovery_epoch=3,
                maximum_reconciliation_cycle=1,
                maximum_plan_claims=3,
                maximum_worker_attempts=12,
                maximum_execution_seconds=(
                    reference["execution_window"]["calculated_seconds"] * 3
                ),
                maximum_concurrent_drains=1,
                created_at=created_at,
                expires_at=created_at + 172_800,
            )
        )
        grant = recovery_contract.activate_exact_drain_authorization_grant(
            grant_plan,
            approval_digest=grant_plan["grant_plan_digest"],
            approved_at=created_at,
        )

        plan = create_plan(
            candidate_release=release_identity(),
            grant=grant,
            predecessor=reference["plan_digest"],
        )
        self.assertEqual(plan["schema_version"], 15)
        self.assertEqual(plan["grant_id"], grant["grant_id"])
        self.assertEqual(plan["grant_digest"], grant["grant_digest"])
        self.assertEqual(
            plan["grant_predecessor_plan_digest"],
            reference["plan_digest"],
        )
        self.assertEqual(
            recovery_contract.verify_exact_drain_plan(plan, now=created_at),
            plan,
        )

        drifted_release = {**release_identity(), "release_digest": "f" * 64}
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "exact drain grant scope differs",
        ):
            create_plan(
                candidate_release=drifted_release,
                grant=grant,
                predecessor=reference["plan_digest"],
            )
        for changed in (
            {"provider_policy_digest": "0" * 64},
            {"worker_runtime_digest": "1" * 64},
        ):
            with self.assertRaisesRegex(
                OperationRecoveryError,
                "exact drain grant scope differs",
            ):
                create_plan(
                    candidate_release=release_identity(),
                    grant=grant,
                    predecessor=reference["plan_digest"],
                    **changed,
                )

        for context_change in (
            {"recovery_epoch": 4},
            {"reconciliation_cycle": 2},
        ):
            overflow = deepcopy(plan)
            overflow["recovery_context"].update(context_change)
            with self.assertRaisesRegex(
                OperationRecoveryError,
                "exact drain grant scope differs",
            ):
                recovery_contract._exact_drain_assert_grant_scope(
                    overflow,
                    grant,
                    predecessor_plan_digest=reference["plan_digest"],
                )

    def test_schema_fifteen_separates_request_and_operation_deadlines(self):
        reference, _grant, plan, _create_plan = self.standing_grant_fixture()

        self.assertEqual(reference["schema_version"], 13)
        self.assertEqual(reference["phase_one_timeout_seconds"], 3_600)
        self.assertEqual(reference["operation_attempt_timeout_seconds"], 3_600)
        self.assertEqual(plan["schema_version"], 15)
        self.assertEqual(plan["phase_one_timeout_seconds"], 7_200)
        self.assertEqual(plan["operation_attempt_timeout_seconds"], 7_200)
        self.assertEqual(
            plan["execution_window"]["operation_attempt_timeout_seconds"],
            7_200,
        )
        self.assertEqual(
            plan["execution_window"],
            {
                "schema_version": 2,
                "kind": "operation-recovery-exact-drain-execution-window",
                "anchor": "authorization-receipt-authorized-at",
                "renewable": False,
                "selected_operation_count": 43,
                "remaining_attempt_count": 172,
                "retry_wait_count": 129,
                "effective_concurrency": 2,
                "operation_attempt_timeout_seconds": 7_200,
                "transaction_timeout_seconds": 120,
                "maximum_retry_delay_seconds": 3_600,
                "startup_margin_seconds": 28_800,
                "transaction_margin_seconds": 41_280,
                "shutdown_attempt_count": 4,
                "shutdown_margin_seconds": 480,
                "calculated_seconds": 1_154_160,
                "maximum_seconds": 1_209_600,
            },
        )
        self.assertEqual(
            plan["provider_timeout_contract"]["members"][-1],
            {
                "provider_id": "hatchery",
                "queue_timeout_seconds": 3_600,
                "execution_timeout_seconds": 3_600,
                "max_concurrent": 2,
            },
        )
        self.assertEqual(
            recovery_contract.verify_exact_drain_plan(
                plan,
                now=plan["created_at"],
            ),
            plan,
        )
        for key in (
            "phase_one_timeout_seconds",
            "operation_attempt_timeout_seconds",
        ):
            with self.subTest(key=key):
                changed = deepcopy(plan)
                changed[key] = 3_600
                changed["plan_digest"] = digest(
                    {
                        item_key: item_value
                        for item_key, item_value in changed.items()
                        if item_key != "plan_digest"
                    }
                )
                with self.assertRaises(OperationRecoveryError):
                    recovery_contract.verify_exact_drain_plan(
                        changed,
                        now=changed["created_at"],
                    )

        downgraded = deepcopy(plan)
        downgraded["provider_timeout_contract"] = deepcopy(
            recovery_contract.EXACT_DRAIN_PROVIDER_TIMEOUT_CONTRACT
        )
        downgraded["plan_digest"] = digest(
            {
                key: value
                for key, value in downgraded.items()
                if key != "plan_digest"
            }
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "exact drain plan is invalid",
        ):
            recovery_contract.verify_exact_drain_plan(
                downgraded,
                now=downgraded["created_at"],
            )

    def test_schema_fifteen_claim_obeys_execution_budget(self):
        _reference, grant, plan, _create_plan = self.standing_grant_fixture(
            maximum_plan_claims=1,
            maximum_execution_seconds=1_154_159,
        )
        ledger = recovery_contract.create_exact_drain_grant_ledger(
            grant,
            ledger_nonce="1" * 64,
            created_at=plan["created_at"],
        )

        with self.assertRaisesRegex(
            OperationRecoveryError,
            "exact drain grant budget exhausted",
        ):
            recovery_contract.claim_exact_drain_grant(
                ledger,
                plan,
                expected_ledger_digest=ledger["ledger_digest"],
                claim_nonce="2" * 64,
                ledger_nonce="3" * 64,
                claimed_at=plan["created_at"],
            )

    def test_exact_drain_grant_claim_is_atomic_and_crash_idempotent(self):
        created_at = 1_785_462_000
        capability = recovery_contract.create_hatchery_capability_receipt(
            provider_policy_digest="9" * 64,
            provider_identity_digest="6" * 64,
            model_digest="5" * 64,
            observed_at=created_at,
            successful=True,
        )

        def create_plan(*, grant=None, predecessor=None):
            return dict(
                recovery_contract.create_exact_drain_plan(
                    self.cohort(),
                    self.drain_snapshot(),
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
                    hatchery_capability_receipt=capability,
                    authorization_grant=grant,
                    grant_predecessor_plan_digest=predecessor,
                    created_at=created_at,
                    schema_version=15 if grant is not None else 13,
                )
            )

        reference = create_plan()
        grant_plan = (
            recovery_contract.create_exact_drain_authorization_grant_plan(
                reference,
                grant_id="33333333-3333-4333-8333-333333333333",
                maximum_recovery_epoch=3,
                maximum_reconciliation_cycle=1,
                maximum_plan_claims=3,
                maximum_worker_attempts=12,
                maximum_execution_seconds=(
                    reference["execution_window"]["calculated_seconds"] * 3
                ),
                maximum_concurrent_drains=1,
                created_at=created_at,
                expires_at=created_at + 172_800,
            )
        )
        grant = recovery_contract.activate_exact_drain_authorization_grant(
            grant_plan,
            approval_digest=grant_plan["grant_plan_digest"],
            approved_at=created_at,
        )
        plan = create_plan(
            grant=grant,
            predecessor=reference["plan_digest"],
        )
        ledger = recovery_contract.create_exact_drain_grant_ledger(
            grant,
            ledger_nonce="a" * 64,
            created_at=created_at,
        )

        claimed, use = recovery_contract.claim_exact_drain_grant(
            ledger,
            plan,
            expected_ledger_digest=ledger["ledger_digest"],
            claim_nonce="b" * 64,
            ledger_nonce="c" * 64,
            claimed_at=created_at,
        )

        self.assertEqual(claimed["revision"], 1)
        self.assertEqual(len(claimed["use_records"]), 1)
        self.assertEqual(use["plan_digest"], plan["plan_digest"])
        self.assertEqual(use["predecessor_plan_digest"], reference["plan_digest"])
        self.assertEqual(
            recovery_contract.verify_exact_drain_grant_ledger(
                claimed,
                now=created_at,
            ),
            claimed,
        )

        reloaded, duplicate = recovery_contract.claim_exact_drain_grant(
            claimed,
            plan,
            expected_ledger_digest=claimed["ledger_digest"],
            claim_nonce="d" * 64,
            ledger_nonce="e" * 64,
            claimed_at=created_at + 1,
        )
        self.assertEqual(reloaded, claimed)
        self.assertEqual(duplicate, use)

    def test_exact_drain_grant_allows_legal_descendants_until_budget_exhaustion(self):
        created_at = 1_785_462_000
        reference, grant, first, create_plan = self.standing_grant_fixture(
            maximum_plan_claims=2,
        )
        ledger = recovery_contract.create_exact_drain_grant_ledger(
            grant,
            ledger_nonce="1" * 64,
            created_at=created_at,
        )
        ledger, first_use = recovery_contract.claim_exact_drain_grant(
            ledger,
            first,
            expected_ledger_digest=ledger["ledger_digest"],
            claim_nonce="2" * 64,
            ledger_nonce="3" * 64,
            claimed_at=created_at,
        )
        ledger, _close = recovery_contract.close_exact_drain_grant_claim(
            ledger,
            plan_digest=first["plan_digest"],
            claim_record_digest=first_use["record_digest"],
            application_receipt_digest="4" * 64,
            expected_ledger_digest=ledger["ledger_digest"],
            close_nonce="5" * 64,
            ledger_nonce="6" * 64,
            closed_at=created_at + 1,
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "exact drain grant claim replayed",
        ):
            recovery_contract.claim_exact_drain_grant(
                ledger,
                first,
                expected_ledger_digest=ledger["ledger_digest"],
                claim_nonce="7" * 64,
                ledger_nonce="8" * 64,
                claimed_at=created_at + 2,
            )

        descendant = create_plan(
            grant=grant,
            predecessor=first["plan_digest"],
            planned_at=created_at + 2,
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "exact drain grant claim replayed",
        ):
            recovery_contract.claim_exact_drain_grant(
                ledger,
                descendant,
                expected_ledger_digest=ledger["ledger_digest"],
                claim_nonce="2" * 64,
                ledger_nonce="7" * 64,
                claimed_at=created_at + 2,
            )
        ledger, descendant_use = recovery_contract.claim_exact_drain_grant(
            ledger,
            descendant,
            expected_ledger_digest=ledger["ledger_digest"],
            claim_nonce="7" * 64,
            ledger_nonce="8" * 64,
            claimed_at=created_at + 2,
        )
        self.assertEqual(
            descendant_use["predecessor_plan_digest"],
            first["plan_digest"],
        )
        ledger, _close = recovery_contract.close_exact_drain_grant_claim(
            ledger,
            plan_digest=descendant["plan_digest"],
            claim_record_digest=descendant_use["record_digest"],
            application_receipt_digest="9" * 64,
            expected_ledger_digest=ledger["ledger_digest"],
            close_nonce="a" * 64,
            ledger_nonce="b" * 64,
            closed_at=created_at + 3,
        )

        over_budget = create_plan(
            grant=grant,
            predecessor=descendant["plan_digest"],
            planned_at=created_at + 4,
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "exact drain grant budget exhausted",
        ):
            recovery_contract.claim_exact_drain_grant(
                ledger,
                over_budget,
                expected_ledger_digest=ledger["ledger_digest"],
                claim_nonce="c" * 64,
                ledger_nonce="d" * 64,
                claimed_at=created_at + 4,
            )

    def test_exact_drain_grant_revocation_and_expiry_fail_closed(self):
        created_at = 1_785_462_000
        _reference, grant, plan, _create_plan = self.standing_grant_fixture()
        ledger = recovery_contract.create_exact_drain_grant_ledger(
            grant,
            ledger_nonce="e" * 64,
            created_at=created_at,
        )
        revoked, revocation = recovery_contract.revoke_exact_drain_grant(
            ledger,
            approval_digest=grant["grant_digest"],
            expected_ledger_digest=ledger["ledger_digest"],
            revocation_nonce="f" * 64,
            ledger_nonce="0" * 64,
            revoked_at=created_at + 1,
        )
        self.assertEqual(revocation["grant_digest"], grant["grant_digest"])
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "exact drain authorization grant revoked",
        ):
            recovery_contract.claim_exact_drain_grant(
                revoked,
                plan,
                expected_ledger_digest=revoked["ledger_digest"],
                claim_nonce="1" * 64,
                ledger_nonce="2" * 64,
                claimed_at=created_at + 2,
            )

        fresh = recovery_contract.create_exact_drain_grant_ledger(
            grant,
            ledger_nonce="3" * 64,
            created_at=created_at,
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "exact drain authorization grant expired",
        ):
            recovery_contract.claim_exact_drain_grant(
                fresh,
                plan,
                expected_ledger_digest=fresh["ledger_digest"],
                claim_nonce="4" * 64,
                ledger_nonce="5" * 64,
                claimed_at=grant["expires_at"],
            )
        expired_revoked, _record = recovery_contract.revoke_exact_drain_grant(
            fresh,
            approval_digest=grant["grant_digest"],
            expected_ledger_digest=fresh["ledger_digest"],
            revocation_nonce="6" * 64,
            ledger_nonce="7" * 64,
            revoked_at=grant["expires_at"],
        )
        self.assertIsNotNone(expired_revoked["revocation"])

    def test_exact_drain_grant_derives_short_lived_receipt_and_keeps_legacy(self):
        created_at = 1_785_462_000
        _reference, grant, plan, _create_plan = self.standing_grant_fixture()
        ledger = recovery_contract.create_exact_drain_grant_ledger(
            grant,
            ledger_nonce="6" * 64,
            created_at=created_at,
        )
        _ledger, use = recovery_contract.claim_exact_drain_grant(
            ledger,
            plan,
            expected_ledger_digest=ledger["ledger_digest"],
            claim_nonce="7" * 64,
            ledger_nonce="8" * 64,
            claimed_at=created_at,
        )

        receipt = recovery_contract.create_exact_drain_grant_authorization_receipt(
            plan,
            use,
        )

        self.assertEqual(receipt["schema_version"], 2)
        self.assertEqual(receipt["grant_id"], grant["grant_id"])
        self.assertEqual(receipt["grant_digest"], grant["grant_digest"])
        self.assertEqual(receipt["expires_at"], use["expires_at"])
        self.assertEqual(
            recovery_contract.verify_exact_drain_authorization_receipt(
                receipt,
                plan=plan,
            ),
            receipt,
        )

        legacy_plan = self.drain_plan(schema_version=12)
        legacy_receipt = exact_drain_authorization(legacy_plan)
        self.assertEqual(
            recovery_contract.verify_exact_drain_authorization_receipt(
                legacy_receipt,
                plan=legacy_plan,
            ),
            legacy_receipt,
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "exact drain authorization receipt is invalid",
        ):
            recovery_contract.verify_exact_drain_authorization_receipt(
                exact_drain_authorization(plan),
                plan=plan,
            )

    def test_exact_drain_plan_separates_approval_evidence_transaction_and_window(self):
        snapshot = self.drain_snapshot()
        planned_at = 1_785_462_000

        plan = self.drain_plan(
            snapshot=snapshot,
            created_at=planned_at,
            schema_version=11,
        )

        self.assertEqual(plan["schema_version"], 11)
        self.assertEqual(plan["expires_at"], planned_at + 86_400)
        self.assertEqual(plan["evidence_observed_at"], snapshot["observed_at"])
        self.assertEqual(plan["evidence_max_age_seconds"], 3_600)
        self.assertEqual(plan["transaction_timeout_seconds"], 120)
        self.assertNotIn("execution_lease_seconds", plan)
        self.assertEqual(
            plan["execution_window"],
            {
                "schema_version": 2,
                "kind": "operation-recovery-exact-drain-execution-window",
                "anchor": "authorization-receipt-authorized-at",
                "renewable": False,
                "selected_operation_count": 43,
                "remaining_attempt_count": 172,
                "retry_wait_count": 129,
                "effective_concurrency": 1,
                "operation_attempt_timeout_seconds": 3_600,
                "transaction_timeout_seconds": 120,
                "maximum_retry_delay_seconds": 3_600,
                "startup_margin_seconds": 14_400,
                "transaction_margin_seconds": 41_280,
                "shutdown_attempt_count": 4,
                "shutdown_margin_seconds": 480,
                "calculated_seconds": 1_139_760,
                "maximum_seconds": 1_209_600,
            },
        )
        self.assertEqual(plan["recovery_context"]["origin"], "initial-snapshot")
        self.assertEqual(plan["recovery_context"]["recovery_epoch"], 0)
        self.assertRegex(
            plan["recovery_context"]["initial_origin_digest"],
            r"^[0-9a-f]{64}$",
        )
        self.assertEqual(
            plan["recovery_context_digest"],
            digest(plan["recovery_context"]),
        )
        self.assertEqual(plan["phase_one_statement_timeout_seconds"], 120)
        self.assertEqual(plan["phase_one_client_timeout_seconds"], 125)
        self.assertEqual(plan["phase_one_timeout_seconds"], 3_600)
        self.assertEqual(plan["operation_attempt_timeout_seconds"], 3_600)
        self.assertEqual(
            plan["phase_one_deadline_anchor"],
            "first-phase-one-entry",
        )
        self.assertEqual(plan["phase_one_nested_stage_prefixes"], ["llm."])
        self.assertEqual(
            plan["provider_timeout_contract"],
            recovery_contract.EXACT_DRAIN_PROVIDER_TIMEOUT_CONTRACT_REPAIRED,
        )
        self.assertEqual(
            plan["provider_timeout_contract"]["members"][-1],
            {
                "provider_id": "hatchery",
                "queue_timeout_seconds": 3_600,
                "execution_timeout_seconds": 3_600,
                "max_concurrent": 2,
            },
        )
        self.assertEqual(
            plan["phase_repair_contract_digest"],
            recovery_contract.EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V7_DIGEST,
        )
        self.assertEqual(
            recovery_contract.EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V7[
                "worker_signal_owner"
            ],
            "worker-main",
        )
        self.assertEqual(plan["progress_schema_version"], 4)

        legacy = deepcopy(plan)
        legacy["provider_timeout_contract"] = deepcopy(
            recovery_contract.EXACT_DRAIN_PROVIDER_TIMEOUT_CONTRACT
        )
        legacy["plan_digest"] = digest(
            {
                key: value
                for key, value in legacy.items()
                if key != "plan_digest"
            }
        )
        self.assertEqual(
            recovery_contract.verify_exact_drain_plan(
                legacy,
                now=legacy["created_at"],
            ),
            legacy,
        )
        self.assertEqual(
            plan["failure_evidence_contract_digest"],
            recovery_contract.EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT_V3_DIGEST,
        )
        self.assertEqual(
            recovery_contract.verify_exact_drain_plan(plan, now=planned_at),
            plan,
        )

    def test_exact_drain_execution_window_budgets_claim_and_outcome_transactions(self):
        window = self.drain_plan()["execution_window"]

        transaction_slots_per_attempt = 2
        expected_transaction_margin = (
            window["remaining_attempt_count"]
            * transaction_slots_per_attempt
            * window["transaction_timeout_seconds"]
        )

        self.assertEqual(
            window["transaction_margin_seconds"],
            expected_transaction_margin,
        )

    def test_schema_twelve_binds_retry_after_quiescence_and_closed_causes(self):
        plan = self.drain_plan(schema_version=12)

        self.assertEqual(plan["schema_version"], 12)
        self.assertEqual(
            plan["operation_attempt_timeout_disposition"],
            "task-retry-after-quiescence",
        )
        self.assertEqual(plan["progress_schema_version"], 5)
        self.assertEqual(
            plan["phase_repair_contract_digest"],
            recovery_contract.EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V8_DIGEST,
        )
        self.assertEqual(
            plan["failure_evidence_contract_digest"],
            recovery_contract.EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT_V4_DIGEST,
        )
        self.assertEqual(
            recovery_contract.EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V8[
                "provider_cancellation_semantics"
            ],
            "queue-or-execution-cancelled-not-provider-failure",
        )
        self.assertEqual(
            recovery_contract.verify_exact_drain_plan(
                plan,
                now=plan["created_at"],
            ),
            plan,
        )

        tampered = deepcopy(plan)
        tampered["operation_attempt_timeout_disposition"] = "worker-fail-stop"
        tampered["plan_digest"] = digest(
            {
                key: value
                for key, value in tampered.items()
                if key != "plan_digest"
            }
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "exact drain plan is invalid",
        ):
            recovery_contract.verify_exact_drain_plan(
                tampered,
                now=tampered["created_at"],
            )

    def test_schema_twelve_initial_post_abort_preserves_retry_checkpoint(self):
        reference_snapshot = self.drain_snapshot()
        reference_snapshot["installation_authority"] = (
            rebound_installation_authority()
        )
        reference_snapshot["snapshot_digest"] = digest(
            {
                key: value
                for key, value in reference_snapshot.items()
                if key != "snapshot_digest"
            }
        )
        reference_backup = drain_backup_evidence()
        reference_backup["source_authority"]["data_identity_digest"] = (
            reference_snapshot["installation_authority"][
                "observed_data_identity_digest"
            ]
        )
        reference_backup["source_authority_digest"] = digest(
            reference_backup["source_authority"]
        )
        reference = dict(
            recovery_contract.create_exact_drain_plan(
                self.cohort(),
                reference_snapshot,
                candidate_release=release_identity(),
                rollback_backup=reference_backup,
                rollback_backup_path="/private/tmp/v12-reference-backup.age",
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path="/private/tmp/v12-reference-auth.json",
                application_receipt_path="/private/tmp/v12-reference-app.json",
                status_artifact_path="/private/tmp/v12-reference-status.json",
                verification_receipt_path="/private/tmp/v12-reference-verify.json",
                created_at=1_785_462_000,
                schema_version=12,
            )
        )
        released_id = reference["selected_operations"][0]["operation_id"]
        owned_id = reference["selected_operations"][1]["operation_id"]
        snapshot = deepcopy(reference_snapshot)
        released = next(
            item for item in snapshot["operations"]
            if item["operation_id"] == released_id
        )
        released.update(
            current_status="pending",
            updated_at="2026-07-29T14:00:00Z",
            completed_at=None,
            retry_count=1,
            next_retry_at="2026-07-29T13:30:00Z",
            worker_id_present=False,
            worker_id_digest=None,
            claimed_at=None,
            result_metadata_digest="a" * 64,
            error_category="provider_transport",
            error_digest="b" * 64,
        )
        released["row_digest"] = digest(
            {
                key: value
                for key, value in released.items()
                if key != "row_digest"
            }
        )
        owned = next(
            item for item in snapshot["operations"]
            if item["operation_id"] == owned_id
        )
        owned.update(
            current_status="failed",
            updated_at="2026-07-29T14:00:00Z",
            completed_at="2026-07-29T14:00:00Z",
            retry_count=1,
            worker_id_present=True,
            worker_id_digest=recovery_contract._post_abort_worker_digest(
                reference["plan_digest"]
            ),
            claimed_at="2026-07-29T13:59:00Z",
            error_category="provider_transport",
            error_digest="c" * 64,
        )
        owned["row_digest"] = digest(
            {
                key: value
                for key, value in owned.items()
                if key != "row_digest"
            }
        )
        snapshot["status_counts"] = {
            "pending": 42,
            "processing": 0,
            "completed": 5,
            "failed": 1,
            "cancelled": 0,
        }
        snapshot["observed_at"] += 1_000
        snapshot["snapshot_digest"] = digest(
            {
                key: value
                for key, value in snapshot.items()
                if key != "snapshot_digest"
            }
        )
        backup = drain_backup_evidence()
        backup["source_authority"]["data_identity_digest"] = (
            snapshot["installation_authority"][
                "observed_data_identity_digest"
            ]
        )
        backup["source_authority_digest"] = digest(
            backup["source_authority"]
        )
        recovery = create_post_abort_recovery_plan(
            reference,
            snapshot,
            candidate_release=release_identity(),
            rollback_backup=backup,
            rollback_encryption=rollback_encryption(),
            rollback_backup_path="/private/tmp/v12-initial-backup.age",
            rollback_bundle_path="/private/tmp/v12-initial-bundle.age",
            authorization_receipt_path="/private/tmp/v12-initial-auth.json",
            application_receipt_path="/private/tmp/v12-initial-app.json",
            verification_receipt_path="/private/tmp/v12-initial-verify.json",
            rollback_receipt_path="/private/tmp/v12-initial-rollback.json",
            reference_application_authorization=(
                exact_drain_authorization(reference)
            ),
            reference_application_journal=exact_drain_application_journal(
                reference
            ),
            reference_application_progress_digest="c" * 64,
            schema_version=12,
            created_at=1_785_463_100,
        )

        self.assertEqual(recovery["schema_version"], 12)
        self.assertNotIn(
            released_id,
            {
                item["operation_id"]
                for item in recovery["selected_operations"]
            },
        )
        self.assertEqual(recovery["retry_recovery"]["schema_version"], 1)
        self.assertEqual(
            recovery_contract.verify_post_abort_recovery_plan(
                recovery,
                now=recovery["created_at"],
            ),
            recovery,
        )

    def test_schema_twelve_post_abort_accepts_released_checkpoint_metadata(self):
        """A released worker row may advance only its durable checkpoint."""
        reference_snapshot = self.drain_snapshot()
        reference_snapshot["installation_authority"] = (
            rebound_installation_authority()
        )
        reference_snapshot["snapshot_digest"] = digest(
            {
                key: value
                for key, value in reference_snapshot.items()
                if key != "snapshot_digest"
            }
        )
        reference_backup = drain_backup_evidence()
        reference_backup["source_authority"]["data_identity_digest"] = (
            reference_snapshot["installation_authority"][
                "observed_data_identity_digest"
            ]
        )
        reference_backup["source_authority_digest"] = digest(
            reference_backup["source_authority"]
        )
        reference = recovery_contract.create_exact_drain_plan(
            self.cohort(),
            reference_snapshot,
            candidate_release=release_identity(),
            rollback_backup=reference_backup,
            rollback_backup_path="/private/tmp/v12-release-reference-backup.age",
            provider_policy_digest="9" * 64,
            effective_profile_digest="7" * 64,
            worker_runtime_digest="8" * 64,
            authorization_receipt_path=(
                "/private/tmp/v12-release-reference-auth.json"
            ),
            application_receipt_path=(
                "/private/tmp/v12-release-reference-app.json"
            ),
            status_artifact_path=(
                "/private/tmp/v12-release-reference-status.json"
            ),
            verification_receipt_path=(
                "/private/tmp/v12-release-reference-verify.json"
            ),
            created_at=1_785_462_000,
            schema_version=12,
        )
        checkpoint_only_id = reference["selected_operations"][2][
            "operation_id"
        ]
        owned_id = reference["selected_operations"][3]["operation_id"]
        snapshot = deepcopy(reference_snapshot)
        checkpoint_only = next(
            item
            for item in snapshot["operations"]
            if item["operation_id"] == checkpoint_only_id
        )
        checkpoint_only.update(
            updated_at="2026-07-29T14:00:00Z",
            result_metadata_digest="d" * 64,
        )
        checkpoint_only["row_digest"] = digest(
            {
                key: value
                for key, value in checkpoint_only.items()
                if key != "row_digest"
            }
        )
        owned = next(
            item
            for item in snapshot["operations"]
            if item["operation_id"] == owned_id
        )
        owned.update(
            current_status="failed",
            updated_at="2026-07-29T14:00:00Z",
            completed_at="2026-07-29T14:00:00Z",
            retry_count=1,
            worker_id_present=True,
            worker_id_digest=recovery_contract._post_abort_worker_digest(
                reference["plan_digest"]
            ),
            claimed_at="2026-07-29T13:59:00Z",
            error_category="provider_transport",
            error_digest="e" * 64,
        )
        owned["row_digest"] = digest(
            {
                key: value
                for key, value in owned.items()
                if key != "row_digest"
            }
        )
        snapshot["status_counts"] = {
            "pending": 42,
            "processing": 0,
            "completed": 5,
            "failed": 1,
            "cancelled": 0,
        }
        snapshot["observed_at"] += 1_000
        snapshot["snapshot_digest"] = digest(
            {
                key: value
                for key, value in snapshot.items()
                if key != "snapshot_digest"
            }
        )
        backup = drain_backup_evidence()
        backup["source_authority"]["data_identity_digest"] = (
            snapshot["installation_authority"]["observed_data_identity_digest"]
        )
        backup["source_authority"]["generation_before"] = (
            snapshot["generation_before"]
        )
        backup["source_authority"]["generation_after"] = (
            snapshot["generation_after"]
        )
        backup["source_authority_digest"] = digest(
            backup["source_authority"]
        )

        with patch.object(
            recovery_contract,
            "_post_abort_released_operation_ids",
            return_value=frozenset({checkpoint_only_id}),
        ):
            recovery = create_post_abort_recovery_plan(
                reference,
                snapshot,
                candidate_release=release_identity(),
                rollback_backup=backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/v12-release-backup.age",
                rollback_bundle_path="/private/tmp/v12-release-bundle.age",
                authorization_receipt_path="/private/tmp/v12-release-auth.json",
                application_receipt_path="/private/tmp/v12-release-app.json",
                verification_receipt_path="/private/tmp/v12-release-verify.json",
                rollback_receipt_path="/private/tmp/v12-release-rollback.json",
                reference_application_authorization=(
                    exact_drain_authorization(reference)
                ),
                reference_application_journal=exact_drain_application_journal(
                    reference
                ),
                reference_application_progress_digest="f" * 64,
                schema_version=12,
                created_at=1_785_463_100,
            )
            self.assertEqual(recovery["selected_operation_count"], 1)
            self.assertNotIn(
                checkpoint_only_id,
                {
                    item["operation_id"]
                    for item in recovery["selected_operations"]
                },
            )
            self.assertEqual(
                recovery_contract.verify_post_abort_recovery_plan(
                    recovery,
                    now=recovery["created_at"],
                ),
                recovery,
            )

    def test_schema_twelve_epoch_one_retry_accepts_authenticated_release_subset(self):
        """Epoch one may omit rows released by the stopped worker."""
        initial = self.drain_plan(schema_version=12)
        selected = initial["selected_operations"]
        current = {
            item["operation_id"]: item
            for item in initial["live_snapshot"]["operations"]
        }
        prior = recovery_contract._post_abort_v10_retry_recovery(
            initial,
            selected,
            current,
        )
        reference = deepcopy(initial)
        rebound_authority = rebound_installation_authority()
        reference["installation_authority"] = rebound_authority
        reference["live_snapshot"]["installation_authority"] = (
            rebound_authority
        )
        released_id = selected[0]["operation_id"]
        retained_id = selected[1]["operation_id"]
        reference["recovery_context"] = {
            "schema_version": 1,
            "kind": "operation-recovery-exact-drain-recovery-context",
            "origin": "post-abort",
            "generation": "systalyze:public:124",
            "recovery_epoch": 1,
            "candidate_release_digest": release_identity()["release_digest"],
            "selected_operation_ids_digest": digest(
                sorted(item["operation_id"] for item in selected)
            ),
            "initial_origin_digest": None,
            "post_abort_selected_operation_ids_digest": digest(
                sorted(item["operation_id"] for item in selected)
            ),
            "post_abort_plan_digest": initial["plan_digest"],
            "post_abort_application_receipt_digest": "a" * 64,
            "post_abort_verification_receipt_digest": "b" * 64,
            "retry_recovery_digest": digest(prior),
            "selected_checkpoint_set_digest": "c" * 64,
            "preserved_row_set_digest": "d" * 64,
        }
        with patch.object(
            recovery_contract,
            "_post_abort_released_operation_ids",
            return_value=frozenset({released_id}),
        ):
            retry = recovery_contract._post_abort_v11_retry_recovery(
                reference,
                [
                    {
                        **selected[1],
                        "expected_status": "pending",
                    }
                ],
                current,
                prior,
            )
        self.assertEqual(retry["schema_version"], 2)
        self.assertEqual(retry["recovery_epoch_before"], 1)
        self.assertEqual(retry["recovery_epoch_after"], 2)
        self.assertEqual(
            [item["operation_id"] for item in retry["operations"]],
            [retained_id],
        )

        released_current = deepcopy(current)
        released_row = released_current[released_id]
        released_row.update(
            updated_at="2026-07-29T14:00:00Z",
            result_metadata_digest="d" * 64,
        )
        released_row["row_digest"] = digest(
            {
                key: value
                for key, value in released_row.items()
                if key != "row_digest"
            }
        )
        retained_row = released_current[retained_id]
        retained_row.update(
            worker_id_present=True,
            worker_id_digest=recovery_contract._post_abort_worker_digest(
                reference["plan_digest"]
            ),
            claimed_at="2026-07-29T13:59:00Z",
        )
        retained_row["row_digest"] = digest(
            {
                key: value
                for key, value in retained_row.items()
                if key != "row_digest"
            }
        )
        chained_snapshot = deepcopy(reference["live_snapshot"])
        chained_snapshot["operations"] = list(released_current.values())
        chained_snapshot["snapshot_digest"] = digest(
            {
                key: value
                for key, value in chained_snapshot.items()
                if key != "snapshot_digest"
            }
        )
        with patch.object(
            recovery_contract,
            "_post_abort_released_operation_ids",
            return_value=frozenset({released_id}),
        ):
            chained = recovery_contract._post_abort_v10_contract(
                reference,
                chained_snapshot,
                schema_version=11,
                prior_retry_recovery=prior,
            )
        self.assertEqual(len(chained[0]), 1)
        self.assertEqual(
            chained[5]["retry_recovery"]["schema_version"],
            2,
        )

    def test_schema_twelve_status_closes_payload_free_failure_classification(self):
        plan = self.drain_plan(schema_version=12)
        body = {
            "schema_version": 2,
            "kind": "operation-recovery-exact-drain-status",
            "plan_digest": plan["plan_digest"],
            "generation_before": plan["pre_generation"],
            "generation_after": plan["pre_generation"],
            "selected_operation_count": plan["selected_operation_count"],
            "selected_status_counts": plan["selected_status_counts"],
            "preserved_status_counts": plan["preserved_status_counts"],
            "outside_nonterminal_counts": [],
            "failure_classifications": [
                {
                    "cause_family": "provider_execution_timeout",
                    "error_digest": "a" * 64,
                    "occurrence_count": 1,
                },
                {
                    "cause_family": "upstream_timeout",
                    "error_digest": "b" * 64,
                    "occurrence_count": 2,
                },
            ],
            "observed_at": plan["created_at"],
        }
        status = {**body, "status_digest": digest(body)}

        self.assertEqual(
            recovery_contract.verify_exact_drain_status(status, plan=plan),
            status,
        )
        self.assertNotIn("error_message", repr(status))

        tampered = deepcopy(status)
        tampered["failure_classifications"][0]["cause_family"] = (
            "raw_exception_text"
        )
        tampered["status_digest"] = digest(
            {
                key: value
                for key, value in tampered.items()
                if key != "status_digest"
            }
        )
        with self.assertRaises(OperationRecoveryError):
            recovery_contract.verify_exact_drain_status(tampered, plan=plan)

        non_integer_schema = deepcopy(status)
        non_integer_schema["schema_version"] = 2.0
        non_integer_schema["status_digest"] = digest(
            {
                key: value
                for key, value in non_integer_schema.items()
                if key != "status_digest"
            }
        )
        with self.assertRaises(OperationRecoveryError):
            recovery_contract.verify_exact_drain_status(
                non_integer_schema,
                plan=plan,
            )

    def test_exact_drain_execution_window_is_closed_and_recomputed(self):
        plan = self.drain_plan(schema_version=11)
        cases = {}
        for label, key, value in (
            (
                "remaining-attempt-count",
                "remaining_attempt_count",
                171,
            ),
            ("effective-concurrency", "effective_concurrency", 2),
            (
                "operation-attempt-timeout",
                "operation_attempt_timeout_seconds",
                3_599,
            ),
            (
                "maximum-retry-delay",
                "maximum_retry_delay_seconds",
                3_599,
            ),
            ("startup-margin", "startup_margin_seconds", 14_399),
            (
                "transaction-margin",
                "transaction_margin_seconds",
                41_279,
            ),
            ("shutdown-margin", "shutdown_margin_seconds", 479),
            ("calculated-seconds", "calculated_seconds", 1_139_759),
            ("maximum-seconds", "maximum_seconds", 1_209_601),
            ("anchor", "anchor", "plan-created-at"),
            ("renewable", "renewable", True),
            ("schema-version-bool", "schema_version", True),
            (
                "effective-concurrency-bool",
                "effective_concurrency",
                True,
            ),
            ("renewable-int", "renewable", 0),
        ):
            candidate = deepcopy(plan)
            candidate["execution_window"][key] = value
            candidate["plan_digest"] = digest(
                {
                    item_key: item_value
                    for item_key, item_value in candidate.items()
                    if item_key != "plan_digest"
                }
            )
            cases[label] = candidate
        extra = deepcopy(plan)
        extra["execution_window"]["extra"] = 1
        extra["plan_digest"] = digest(
            {key: value for key, value in extra.items() if key != "plan_digest"}
        )
        cases["extra-key"] = extra
        missing = deepcopy(plan)
        missing["execution_window"].pop("retry_wait_count")
        missing["plan_digest"] = digest(
            {
                key: value
                for key, value in missing.items()
                if key != "plan_digest"
            }
        )
        cases["missing-key"] = missing

        for label, candidate in cases.items():
            with self.subTest(label=label), self.assertRaises(
                OperationRecoveryError
            ):
                recovery_contract.verify_exact_drain_plan(
                    candidate,
                    now=candidate["created_at"],
                )

    def test_exact_drain_v10_initial_origin_covers_completed_rows(self):
        rows = operation_rows()
        for index in {0, 1, 42, 43, 46}:
            rows[index]["status"] = "completed"
            rows[index]["completed_at"] = "2026-07-29T13:00:02Z"
        rows[0]["updated_at"] = "2026-08-15T21:00:00.000000Z"
        snapshot = dict(
            create_live_snapshot(
                self.cohort(),
                rows,
                generation_before="systalyze:public:124",
                generation_after="systalyze:public:124",
                installation_authority=installation_authority(),
                observed_at=1_785_461_000,
            )
        )

        with self.assertRaisesRegex(
            OperationRecoveryError,
            "recovery context is required",
        ):
            self.drain_plan(snapshot=snapshot)

    def test_exact_drain_rejects_forged_selected_id_without_raw_key_error(self):
        plan = self.drain_plan()
        plan["selected_operations"][0]["operation_id"] = (
            "ffffffff-ffff-4fff-8fff-ffffffffffff"
        )

        with self.assertRaises(OperationRecoveryError):
            recovery_contract.verify_exact_drain_plan(
                plan,
                now=plan["created_at"],
            )

    def test_exact_drain_rejects_an_execution_window_over_fourteen_days(self):
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "execution window exceeds maximum",
        ):
            self.drain_plan(
                snapshot=self.drain_snapshot(completed_positions=set())
            )

    def test_exact_drain_execution_window_accepts_only_through_its_maximum(self):
        calculated_seconds = self.drain_plan()["execution_window"][
            "calculated_seconds"
        ]

        with patch.object(
            recovery_contract,
            "EXACT_DRAIN_EXECUTION_WINDOW_MAX_SECONDS",
            calculated_seconds,
        ):
            exact = self.drain_plan()
        self.assertEqual(
            exact["execution_window"]["calculated_seconds"],
            exact["execution_window"]["maximum_seconds"],
        )

        with (
            patch.object(
                recovery_contract,
                "EXACT_DRAIN_EXECUTION_WINDOW_MAX_SECONDS",
                calculated_seconds - 1,
            ),
            self.assertRaisesRegex(
                OperationRecoveryError,
                "execution window exceeds maximum",
            ),
        ):
            self.drain_plan()

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
                "phase_one_client_timeout_seconds",
                "progress_schema_version",
                "failure_evidence_contract_digest",
                "execution_window",
                "recovery_context",
                "recovery_context_digest",
            }
        }
        body["schema_version"] = 2
        body["execution_lease_seconds"] = 86_400
        prior = {**body, "plan_digest": digest(body)}

        self.assertEqual(
            recovery_contract.verify_exact_drain_plan(
                prior,
                now=prior["created_at"],
            ),
            prior,
        )

    def test_exact_drain_verifier_preserves_prior_v3_contract(self):
        current = self.drain_plan()
        body = {
            key: value
            for key, value in current.items()
            if key
            not in {
                "plan_digest",
                "phase_one_client_timeout_seconds",
                "progress_schema_version",
                "failure_evidence_contract_digest",
                "execution_window",
                "recovery_context",
                "recovery_context_digest",
            }
        }
        body["schema_version"] = 3
        body["execution_lease_seconds"] = 86_400
        body["phase_repair_contract_digest"] = (
            recovery_contract.EXACT_DRAIN_PHASE_REPAIR_CONTRACT_DIGEST
        )
        prior = {**body, "plan_digest": digest(body)}

        self.assertEqual(
            recovery_contract.verify_exact_drain_plan(
                prior,
                now=prior["created_at"],
            ),
            prior,
        )

    def test_exact_drain_verifier_preserves_prior_v4_contract(self):
        current = self.drain_plan()
        body = {
            key: value
            for key, value in current.items()
            if key
            not in {
                "plan_digest",
                "phase_one_client_timeout_seconds",
                "progress_schema_version",
                "failure_evidence_contract_digest",
                "execution_window",
                "recovery_context",
                "recovery_context_digest",
            }
        }
        body["schema_version"] = 4
        body["execution_lease_seconds"] = 86_400
        body["phase_repair_contract_digest"] = (
            recovery_contract.EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V2_DIGEST
        )
        prior = {**body, "plan_digest": digest(body)}

        self.assertEqual(
            recovery_contract.verify_exact_drain_plan(
                prior,
                now=prior["created_at"],
            ),
            prior,
        )

    def test_exact_drain_verifier_preserves_prior_v5_contract(self):
        current = self.drain_plan()
        body = {
            key: value
            for key, value in current.items()
            if key
            not in {
                "plan_digest",
                "phase_one_client_timeout_seconds",
                "progress_schema_version",
                "failure_evidence_contract_digest",
                "execution_window",
                "recovery_context",
                "recovery_context_digest",
            }
        }
        body["schema_version"] = 5
        body["execution_lease_seconds"] = 86_400
        body["phase_repair_contract_digest"] = (
            recovery_contract.EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V3_DIGEST
        )
        prior = {**body, "plan_digest": digest(body)}

        self.assertEqual(
            recovery_contract.verify_exact_drain_plan(
                prior,
                now=prior["created_at"],
            ),
            prior,
        )

    def test_exact_drain_verifier_preserves_prior_v6_authorization_deadline(self):
        current = self.drain_plan()
        body = {
            key: value
            for key, value in current.items()
            if key
            not in {
                "plan_digest",
                "execution_window",
                "recovery_context",
                "recovery_context_digest",
            }
        }
        body["schema_version"] = 6
        body["execution_lease_seconds"] = 86_400
        body["phase_repair_contract_digest"] = (
            recovery_contract.EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V4_DIGEST
        )
        body["progress_schema_version"] = 2
        body["failure_evidence_contract_digest"] = (
            recovery_contract.EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT_DIGEST
        )
        prior = {**body, "plan_digest": digest(body)}
        authorization = exact_drain_authorization(
            prior,
            authorized_at=prior["created_at"] + 123,
        )

        self.assertEqual(
            recovery_contract.verify_exact_drain_plan(
                prior,
                now=prior["created_at"],
            ),
            prior,
        )
        self.assertEqual(
            recovery_contract.exact_drain_execution_window_seconds(prior),
            86_400,
        )
        self.assertEqual(
            recovery_contract.exact_drain_execution_deadline(
                prior,
                authorization,
            ),
            authorization["authorized_at"] + 86_400,
        )

    def test_exact_drain_verifier_preserves_prior_v7_contract(self):
        current = self.drain_plan()
        body = {
            key: value
            for key, value in current.items()
            if key
            not in {
                "plan_digest",
                "execution_window",
                "recovery_context",
                "recovery_context_digest",
            }
        }
        body["schema_version"] = 7
        body["execution_lease_seconds"] = 86_400
        body["phase_repair_contract_digest"] = (
            recovery_contract.EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V5_DIGEST
        )
        body["progress_schema_version"] = 2
        body["failure_evidence_contract_digest"] = (
            recovery_contract.EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT_DIGEST
        )
        prior = {**body, "plan_digest": digest(body)}

        self.assertEqual(
            recovery_contract.verify_exact_drain_plan(
                prior,
                now=prior["created_at"],
            ),
            prior,
        )

    def test_exact_drain_verifier_preserves_prior_v8_contract(self):
        current = self.drain_plan()
        body = {
            key: value
            for key, value in current.items()
            if key
            not in {
                "plan_digest",
                "execution_window",
                "recovery_context",
                "recovery_context_digest",
            }
        }
        body["schema_version"] = 8
        body["execution_lease_seconds"] = 86_400
        body["phase_repair_contract_digest"] = (
            recovery_contract.EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V5_DIGEST
        )
        prior = {**body, "plan_digest": digest(body)}

        self.assertEqual(
            recovery_contract.verify_exact_drain_plan(
                prior,
                now=prior["created_at"],
            ),
            prior,
        )

    def test_exact_drain_verifier_preserves_prior_v9_authorization_deadline(self):
        current = self.drain_plan()
        body = {
            key: value
            for key, value in current.items()
            if key
            not in {
                "plan_digest",
                "execution_window",
                "recovery_context",
                "recovery_context_digest",
            }
        }
        body["schema_version"] = 9
        body["execution_lease_seconds"] = 86_400
        prior = {**body, "plan_digest": digest(body)}
        authorization = exact_drain_authorization(
            prior,
            authorized_at=prior["created_at"] + 123,
        )

        self.assertEqual(
            recovery_contract.verify_exact_drain_plan(
                prior,
                now=prior["created_at"],
            ),
            prior,
        )
        self.assertEqual(
            recovery_contract.exact_drain_execution_window_seconds(prior),
            86_400,
        )
        self.assertEqual(
            recovery_contract.exact_drain_execution_deadline(
                prior,
                authorization,
            ),
            authorization["authorized_at"] + 86_400,
        )

    def test_exact_drain_v11_timeout_and_failure_contracts_are_closed(self):
        plan = self.drain_plan(schema_version=11)
        for key, value in (
            ("phase_one_statement_timeout_seconds", 121),
            ("phase_one_client_timeout_seconds", 124),
            ("phase_one_timeout_seconds", 3_601),
            ("operation_attempt_timeout_seconds", 3_601),
            ("phase_one_deadline_anchor", "most-recent-phase-one-entry"),
            ("phase_one_nested_stage_prefixes", ["provider."]),
            ("provider_timeout_contract", {}),
            ("phase_repair_contract_digest", "0" * 64),
            ("progress_schema_version", 3),
            ("failure_evidence_contract_digest", "0" * 64),
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

        for label, mutate in (
            (
                "schema-version-bool",
                lambda value: value["provider_timeout_contract"].update(
                    schema_version=True
                ),
            ),
            (
                "max-concurrent-bool",
                lambda value: value["provider_timeout_contract"]["members"][-1].update(
                    max_concurrent=True
                ),
            ),
        ):
            with self.subTest(label=label):
                changed = deepcopy(plan)
                mutate(changed)
                changed["plan_digest"] = digest(
                    {
                        key: value
                        for key, value in changed.items()
                        if key != "plan_digest"
                    }
                )
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
        interrupted_operation_types: tuple[str, ...] | None = None,
        observed_at: int = 1_786_390_181,
    ) -> dict:
        reference_plan = reference_plan or self.drain_plan()
        rows = operation_rows()
        reference_snapshot = {
            item["operation_id"]: item
            for item in reference_plan["live_snapshot"]["operations"]
        }
        completed = {
            index
            for index, row in enumerate(rows)
            if reference_snapshot[row["operation_id"]]["current_status"]
            == "completed"
        }
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
            or interrupted_operation_types is not None
            else selected_retain_positions[-1]
        )
        if interrupted_operation_types is None:
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
                else selected_retain_positions[:12]
                + selected_refresh_positions
            )
        else:
            remaining_positions = list(selected_positions)
            processing_positions = set()
            for operation_type in interrupted_operation_types:
                position = next(
                    position
                    for position in remaining_positions
                    if rows[position]["operation_type"] == operation_type
                )
                remaining_positions.remove(position)
                processing_positions.add(position)
        for index, row in enumerate(rows):
            if index in completed:
                row["status"] = "completed"
                row["completed_at"] = "2026-07-29T13:00:02Z"
            elif index in processing_positions:
                row["status"] = "processing"
                row["worker_id_present"] = True
                row["worker_id_digest"] = worker_digest
                row["claimed_at"] = "2026-08-10T10:01:15.000000Z"
                if (
                    interrupted_operation_types
                    == ("retain", "consolidation")
                    and row["operation_type"] == "consolidation"
                ):
                    row["retry_count"] = 3
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
        reference = self.legacy_drain_plan()
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

    def post_abort_v5_snapshot(
        self,
        reference_plan=None,
        *,
        observed_at: int = 1_786_390_181,
    ) -> dict:
        reference_plan = reference_plan or self.drain_plan()
        snapshot = self.post_abort_snapshot(
            reference_plan,
            interrupted_processing_count=1,
            observed_at=observed_at,
        )
        reference_selected = {
            item["operation_id"]
            for item in reference_plan["selected_operations"]
        }
        completed_consolidation = next(
            item
            for item in snapshot["operations"]
            if item["operation_id"] in reference_selected
            and item["operation_type"] == "consolidation"
            and item["current_status"] == "pending"
        )
        completed_consolidation.update(
            {
                "current_status": "completed",
                "completed_at": "2026-08-13T03:00:00.000000Z",
                "retry_count": 3,
                "result_metadata_digest": "a" * 64,
                "worker_id_present": True,
                "worker_id_digest": hashlib.sha256(
                    (
                        "operation-recovery-exact-drain-"
                        f"{reference_plan['plan_digest'][:12]}"
                    ).encode("utf-8")
                ).hexdigest(),
                "claimed_at": "2026-08-12T18:42:14.000000Z",
            }
        )
        failed_rows = [
            item
            for item in snapshot["operations"]
            if item["operation_id"] in reference_selected
            and item["operation_type"] == "retain"
            and item["current_status"] == "pending"
        ][:4]
        for position, (item, retry_count) in enumerate(
            zip(failed_rows, (0, 3, 2, 2), strict=True),
            start=1,
        ):
            item.update(
                {
                    "current_status": "failed",
                    "completed_at": "2026-08-13T03:10:00.000000Z",
                    "retry_count": retry_count,
                    "result_metadata_digest": f"{700 + position:064x}",
                    "error_category": "provider_transport",
                    "error_digest": f"{800 + position:064x}",
                    "worker_id_present": True,
                    "worker_id_digest": completed_consolidation[
                        "worker_id_digest"
                    ],
                    "claimed_at": "2026-08-13T02:14:10.000000Z",
                }
            )
        for item in [completed_consolidation, *failed_rows]:
            item["row_digest"] = digest(
                {key: value for key, value in item.items() if key != "row_digest"}
            )
        snapshot["operations"].sort(key=lambda item: item["operation_id"])
        snapshot["status_counts"] = {
            status: sum(
                item["current_status"] == status
                for item in snapshot["operations"]
            )
            for status in recovery_contract.OPERATION_STATUSES
        }
        snapshot["snapshot_digest"] = digest(
            {
                key: value
                for key, value in snapshot.items()
                if key != "snapshot_digest"
            }
        )
        return snapshot

    def post_abort_v6_snapshot(
        self,
        reference_plan,
        *,
        observed_at: int = 1_786_390_181,
    ) -> dict:
        snapshot = self.post_abort_snapshot(
            reference_plan,
            interrupted_processing_count=1,
            observed_at=observed_at,
        )
        worker_digest = hashlib.sha256(
            (
                "operation-recovery-exact-drain-"
                f"{reference_plan['plan_digest'][:12]}"
            ).encode("utf-8")
        ).hexdigest()
        failed_rows = [
            item
            for item in snapshot["operations"]
            if item["operation_id"]
            in {
                selected["operation_id"]
                for selected in reference_plan["selected_operations"]
            }
            and item["operation_type"] == "retain"
            and item["current_status"] == "pending"
        ][:3]
        for item in failed_rows:
            item.update(
                {
                    "current_status": "failed",
                    "completed_at": "2026-08-13T15:20:00.000000Z",
                    "retry_count": 3,
                    "result_metadata_digest": "b" * 64,
                    "error_category": "none",
                    "error_digest": None,
                    "worker_id_present": True,
                    "worker_id_digest": worker_digest,
                    "claimed_at": "2026-08-13T08:00:00.000000Z",
                }
            )
            item["row_digest"] = digest(
                {key: value for key, value in item.items() if key != "row_digest"}
            )
        snapshot["operations"].sort(key=lambda item: item["operation_id"])
        snapshot["status_counts"] = {
            status: sum(
                item["current_status"] == status
                for item in snapshot["operations"]
            )
            for status in recovery_contract.OPERATION_STATUSES
        }
        snapshot["snapshot_digest"] = digest(
            {
                key: value
                for key, value in snapshot.items()
                if key != "snapshot_digest"
            }
        )
        return snapshot

    def post_abort_v7_snapshot(
        self,
        reference_plan,
        *,
        observed_at: int = 1_786_390_181,
    ) -> dict:
        snapshot = self.post_abort_v6_snapshot(
            reference_plan,
            observed_at=observed_at,
        )
        worker_digest = hashlib.sha256(
            (
                "operation-recovery-exact-drain-"
                f"{reference_plan['plan_digest'][:12]}"
            ).encode("utf-8")
        ).hexdigest()
        selected_ids = {
            item["operation_id"]
            for item in reference_plan["selected_operations"]
        }
        retrying = next(
            item
            for item in snapshot["operations"]
            if item["operation_id"] in selected_ids
            and item["current_status"] == "pending"
            and item["operation_type"] == "retain"
            and not item["worker_id_present"]
        )
        retrying.update(
            {
                "retry_count": 1,
                "worker_id_present": True,
                "worker_id_digest": worker_digest,
                "claimed_at": "2026-08-13T18:11:57.247329Z",
            }
        )
        retrying["row_digest"] = digest(
            {key: value for key, value in retrying.items() if key != "row_digest"}
        )
        snapshot["snapshot_digest"] = digest(
            {
                key: value
                for key, value in snapshot.items()
                if key != "snapshot_digest"
            }
        )
        return snapshot

    def post_abort_v8_snapshot(
        self,
        reference_plan,
        *,
        observed_at: int = 1_786_390_181,
    ) -> dict:
        snapshot = self.post_abort_snapshot(
            reference_plan,
            interrupted_processing_count=1,
            observed_at=observed_at,
        )
        worker_digest = hashlib.sha256(
            (
                "operation-recovery-exact-drain-"
                f"{reference_plan['plan_digest'][:12]}"
            ).encode()
        ).hexdigest()
        processing = next(
            item
            for item in snapshot["operations"]
            if item["current_status"] == "processing"
        )
        processing.update(
            {
                "retry_count": 1,
                "error_category": "provider_transport",
                "error_digest": "1" * 64,
                "result_metadata_digest": "2" * 64,
            }
        )
        failed = next(
            item
            for item in snapshot["operations"]
            if item["operation_id"]
            in {
                selected["operation_id"]
                for selected in reference_plan["selected_operations"]
            }
            and item["current_status"] == "pending"
            and item["operation_type"] == "retain"
        )
        failed.update(
            {
                "current_status": "failed",
                "retry_count": 3,
                "worker_id_present": True,
                "worker_id_digest": worker_digest,
                "claimed_at": "2026-08-14T01:49:50.000000Z",
                "completed_at": "2026-08-14T02:49:32.000000Z",
                "error_category": "unknown",
                "error_digest": "3" * 64,
                "result_metadata_digest": "4" * 64,
            }
        )
        for item in (processing, failed):
            item["row_digest"] = digest(
                {key: value for key, value in item.items() if key != "row_digest"}
            )
        snapshot["operations"].sort(key=lambda item: item["operation_id"])
        snapshot["status_counts"] = {
            status: sum(
                item["current_status"] == status
                for item in snapshot["operations"]
            )
            for status in recovery_contract.OPERATION_STATUSES
        }
        snapshot["snapshot_digest"] = digest(
            {
                key: value
                for key, value in snapshot.items()
                if key != "snapshot_digest"
            }
        )
        return snapshot

    def post_abort_v9_snapshot(
        self,
        reference_plan,
        *,
        observed_at: int = 1_786_390_181,
    ) -> dict:
        snapshot = self.post_abort_v8_snapshot(
            reference_plan,
            observed_at=observed_at,
        )
        worker_digest = hashlib.sha256(
            (
                "operation-recovery-exact-drain-"
                f"{reference_plan['plan_digest'][:12]}"
            ).encode()
        ).hexdigest()
        reference_selected = {
            item["operation_id"]
            for item in reference_plan["selected_operations"]
        }
        processing = next(
            item
            for item in snapshot["operations"]
            if item["current_status"] == "processing"
        )
        processing["retry_count"] = 3
        processing["row_digest"] = digest(
            {
                key: value
                for key, value in processing.items()
                if key != "row_digest"
            }
        )
        completed = next(
            item
            for item in snapshot["operations"]
            if item["operation_id"] in reference_selected
            and item["current_status"] == "pending"
            and item["operation_type"] == "retain"
        )
        completed.update(
            {
                "current_status": "completed",
                "retry_count": 3,
                "worker_id_present": True,
                "worker_id_digest": worker_digest,
                "claimed_at": "2026-08-14T10:51:06.925194Z",
                "completed_at": "2026-08-14T11:10:50.017320Z",
                "error_category": "provider_transport",
                "error_digest": "5" * 64,
                "result_metadata_digest": "6" * 64,
            }
        )
        completed["row_digest"] = digest(
            {
                key: value
                for key, value in completed.items()
                if key != "row_digest"
            }
        )
        retrying = next(
            item
            for item in snapshot["operations"]
            if item["operation_id"] in reference_selected
            and item["current_status"] == "pending"
            and item["operation_type"] == "retain"
        )
        retrying.update(
            {
                "retry_count": 3,
                "worker_id_present": True,
                "worker_id_digest": worker_digest,
                "claimed_at": "2026-08-14T13:44:36.057801Z",
                "error_category": "provider_transport",
                "error_digest": "7" * 64,
                "result_metadata_digest": "8" * 64,
            }
        )
        retrying["row_digest"] = digest(
            {
                key: value
                for key, value in retrying.items()
                if key != "row_digest"
            }
        )
        snapshot["operations"].sort(key=lambda item: item["operation_id"])
        snapshot["status_counts"] = {
            status: sum(
                item["current_status"] == status
                for item in snapshot["operations"]
            )
            for status in recovery_contract.OPERATION_STATUSES
        }
        snapshot["snapshot_digest"] = digest(
            {
                key: value
                for key, value in snapshot.items()
                if key != "snapshot_digest"
            }
        )
        return snapshot

    def post_abort_v10_snapshot(
        self,
        reference_plan,
        *,
        observed_at: int = 1_786_390_181,
    ) -> dict:
        rows = operation_rows()
        reference_snapshot = {
            item["operation_id"]: item
            for item in reference_plan["live_snapshot"]["operations"]
        }
        reference_selected = {
            item["operation_id"]
            for item in reference_plan["selected_operations"]
        }
        worker_digest = hashlib.sha256(
            (
                "operation-recovery-exact-drain-"
                f"{reference_plan['plan_digest'][:12]}"
            ).encode()
        ).hexdigest()
        selected_rows = [
            row for row in rows if row["operation_id"] in reference_selected
        ]
        self.assertEqual(len(selected_rows), 41)
        failed_ids = {
            row["operation_id"] for row in selected_rows[:22]
        }
        pending_ids = {
            row["operation_id"] for row in selected_rows[22:38]
        }
        processing_id = selected_rows[38]["operation_id"]
        completed_ids = {
            row["operation_id"] for row in selected_rows[39:]
        }
        for index, row in enumerate(rows):
            reference_row = reference_snapshot[row["operation_id"]]
            if reference_row["current_status"] == "completed":
                row["status"] = "completed"
                row["completed_at"] = reference_row["completed_at"]
            elif row["operation_id"] in failed_ids:
                row.update(
                    status="failed",
                    completed_at="2026-08-15T20:15:00.000000Z",
                    retry_count=3,
                    worker_id_present=True,
                    worker_id_digest=worker_digest,
                    claimed_at="2026-08-15T19:15:00.000000Z",
                    error_category=(
                        "provider_transport" if index % 2 else "unknown"
                    ),
                    error_digest=f"{index + 900:064x}",
                    result_metadata_digest=f"{index + 1000:064x}",
                )
            elif row["operation_id"] in pending_ids:
                row.update(
                    retry_count=3,
                    next_retry_at="2026-08-15T20:16:00.000000Z",
                    worker_id_present=True,
                    worker_id_digest=worker_digest,
                    claimed_at="2026-08-15T19:15:00.000000Z",
                    error_category="provider_transport",
                    error_digest=f"{index + 1100:064x}",
                    result_metadata_digest=f"{index + 1200:064x}",
                )
            elif row["operation_id"] == processing_id:
                row.update(
                    status="processing",
                    retry_count=3,
                    worker_id_present=True,
                    worker_id_digest=worker_digest,
                    claimed_at="2026-08-15T19:15:00.000000Z",
                    error_category="provider_transport",
                    error_digest=f"{index + 1300:064x}",
                    result_metadata_digest=f"{index + 1400:064x}",
                )
            elif row["operation_id"] in completed_ids:
                row.update(
                    status="completed",
                    completed_at="2026-08-15T20:14:00.000000Z",
                    retry_count=3,
                    worker_id_present=True,
                    worker_id_digest=worker_digest,
                    claimed_at="2026-08-15T19:15:00.000000Z",
                    result_metadata_digest=f"{index + 1500:064x}",
                )
        return dict(
            create_live_snapshot(
                self.cohort(),
                rows,
                generation_before="systalyze:public:81699",
                generation_after="systalyze:public:81699",
                installation_authority=installation_authority(),
                observed_at=observed_at,
            )
        )

    def prior_v3_post_abort_plan(self) -> dict:
        reference = self.legacy_drain_plan()
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

    def test_post_abort_v4_plan_derives_exact_retain_and_consolidation(self):
        reference = self.legacy_drain_plan()
        snapshot = self.post_abort_snapshot(
            reference,
            interrupted_operation_types=("retain", "consolidation"),
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
            schema_version=4,
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
        self.assertEqual(
            plan["selected_type_counts"],
            {"retain": 1, "consolidation": 1},
        )
        self.assertEqual(
            {
                item["operation_type"]: item["retry_count"]
                for item in plan["live_snapshot"]["operations"]
                if item["current_status"] == "processing"
            },
            {"retain": 0, "consolidation": 3},
        )
        self.assertEqual(
            plan["preserved_status_counts"],
            {"completed": 5, "pending": 41},
        )
        self.assertEqual(
            verify_post_abort_recovery_plan(plan, now=plan["created_at"]),
            plan,
        )
    def test_post_abort_v5_plan_derives_four_failed_and_one_processing_retain(self):
        reference = self.legacy_drain_plan()
        snapshot = self.post_abort_snapshot(
            reference,
            interrupted_processing_count=1,
        )
        reference_selected = {
            item["operation_id"] for item in reference["selected_operations"]
        }
        completed_consolidation = next(
            item
            for item in snapshot["operations"]
            if item["operation_id"] in reference_selected
            and item["operation_type"] == "consolidation"
            and item["current_status"] == "pending"
        )
        completed_consolidation.update(
            {
                "current_status": "completed",
                "completed_at": "2026-08-13T03:00:00.000000Z",
                "retry_count": 3,
                "result_metadata_digest": "a" * 64,
                "worker_id_present": True,
                "worker_id_digest": hashlib.sha256(
                    (
                        "operation-recovery-exact-drain-"
                        f"{reference['plan_digest'][:12]}"
                    ).encode("utf-8")
                ).hexdigest(),
                "claimed_at": "2026-08-12T18:42:14.000000Z",
            }
        )
        failed_retries = (0, 3, 2, 2)
        failed_rows = [
            item
            for item in snapshot["operations"]
            if item["operation_id"] in reference_selected
            and item["operation_type"] == "retain"
            and item["current_status"] == "pending"
        ][:4]
        for position, (item, retry_count) in enumerate(
            zip(failed_rows, failed_retries, strict=True),
            start=1,
        ):
            item.update(
                {
                    "current_status": "failed",
                    "completed_at": "2026-08-13T03:10:00.000000Z",
                    "retry_count": retry_count,
                    "result_metadata_digest": f"{700 + position:064x}",
                    "error_category": "provider_transport",
                    "error_digest": f"{800 + position:064x}",
                    "worker_id_present": True,
                    "worker_id_digest": completed_consolidation[
                        "worker_id_digest"
                    ],
                    "claimed_at": "2026-08-13T02:14:10.000000Z",
                }
            )
        for item in [completed_consolidation, *failed_rows]:
            item["row_digest"] = digest(
                {
                    key: value
                    for key, value in item.items()
                    if key != "row_digest"
                }
            )
        snapshot["operations"].sort(key=lambda item: item["operation_id"])
        snapshot["status_counts"] = {
            status: sum(
                item["current_status"] == status
                for item in snapshot["operations"]
            )
            for status in recovery_contract.OPERATION_STATUSES
        }
        snapshot["snapshot_digest"] = digest(
            {
                key: value
                for key, value in snapshot.items()
                if key != "snapshot_digest"
            }
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

        plan = create_post_abort_recovery_plan(
            reference,
            snapshot,
            candidate_release=release_identity(),
            rollback_backup=backup,
            rollback_encryption=rollback_encryption(),
            rollback_backup_path="/private/tmp/v5-backup.age",
            rollback_bundle_path="/private/tmp/v5-bundle.age",
            authorization_receipt_path="/private/tmp/v5-auth.json",
            application_receipt_path="/private/tmp/v5-app.json",
            verification_receipt_path="/private/tmp/v5-verify.json",
            rollback_receipt_path="/private/tmp/v5-rollback.json",
            reference_application_authorization=(
                exact_drain_authorization(reference)
            ),
            reference_application_journal=(
                exact_drain_application_journal(reference)
            ),
            reference_application_progress_digest="c" * 64,
            schema_version=5,
            created_at=1_786_390_500,
        )

        self.assertEqual(plan["schema_version"], 5)
        self.assertEqual(plan["selected_operation_count"], 5)
        self.assertEqual(
            plan["selected_status_counts"],
            {"failed": 4, "processing": 1},
        )
        self.assertEqual(plan["selected_type_counts"], {"retain": 5})
        self.assertEqual(
            plan["preserved_status_counts"],
            {"completed": 6, "pending": 37},
        )
        self.assertEqual(
            verify_post_abort_recovery_plan(plan, now=plan["created_at"]),
            plan,
        )

    def test_post_abort_v5_rejects_failure_and_completion_shape_drift(self):
        reference = self.legacy_drain_plan()

        def create(snapshot):
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
            return create_post_abort_recovery_plan(
                reference,
                snapshot,
                candidate_release=release_identity(),
                rollback_backup=backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/v5-drift-backup.age",
                rollback_bundle_path="/private/tmp/v5-drift-bundle.age",
                authorization_receipt_path="/private/tmp/v5-drift-auth.json",
                application_receipt_path="/private/tmp/v5-drift-app.json",
                verification_receipt_path="/private/tmp/v5-drift-verify.json",
                rollback_receipt_path="/private/tmp/v5-drift-rollback.json",
                reference_application_authorization=(
                    exact_drain_authorization(reference)
                ),
                reference_application_journal=(
                    exact_drain_application_journal(reference)
                ),
                reference_application_progress_digest="c" * 64,
                schema_version=5,
                created_at=1_786_390_500,
            )

        cases = {}
        retry_drift = self.post_abort_v5_snapshot(reference)
        retry_row = next(
            item
            for item in retry_drift["operations"]
            if item["current_status"] == "failed" and item["retry_count"] == 0
        )
        retry_row["retry_count"] = 1
        cases["retry-vector"] = retry_drift
        category_drift = self.post_abort_v5_snapshot(reference)
        next(
            item
            for item in category_drift["operations"]
            if item["current_status"] == "failed"
        )["error_category"] = "internal"
        cases["error-category"] = category_drift
        completion_drift = self.post_abort_v5_snapshot(reference)
        completed = next(
            item
            for item in completion_drift["operations"]
            if item["operation_type"] == "consolidation"
            and item["operation_id"]
            in {row["operation_id"] for row in reference["selected_operations"]}
            and item["current_status"] == "completed"
        )
        completed.update(
            {
                "current_status": "pending",
                "completed_at": None,
                "retry_count": 0,
            }
        )
        cases["completed-consolidation"] = completion_drift
        failed_owner_drift = self.post_abort_v5_snapshot(reference)
        next(
            item
            for item in failed_owner_drift["operations"]
            if item["current_status"] == "failed"
        )["worker_id_digest"] = "0" * 64
        cases["failed-owner"] = failed_owner_drift
        completed_owner_drift = self.post_abort_v5_snapshot(reference)
        next(
            item
            for item in completed_owner_drift["operations"]
            if item["operation_type"] == "consolidation"
            and item["current_status"] == "completed"
            and item["operation_id"]
            in {
                row["operation_id"]
                for row in reference["selected_operations"]
            }
        )["claimed_at"] = None
        cases["completed-owner"] = completed_owner_drift

        for label, snapshot in cases.items():
            for item in snapshot["operations"]:
                item["row_digest"] = digest(
                    {
                        key: value
                        for key, value in item.items()
                        if key != "row_digest"
                    }
                )
            snapshot["status_counts"] = {
                status: sum(
                    item["current_status"] == status
                    for item in snapshot["operations"]
                )
                for status in recovery_contract.OPERATION_STATUSES
            }
            snapshot["snapshot_digest"] = digest(
                {
                    key: value
                    for key, value in snapshot.items()
                    if key != "snapshot_digest"
                }
            )
            with (
                self.subTest(drift=label),
                self.assertRaisesRegex(
                    OperationRecoveryError,
                    "post-abort row set is invalid",
                ),
            ):
                create(snapshot)

    def test_post_abort_v6_plan_binds_three_blank_failures_and_one_processing(self):
        reference = self.legacy_drain_plan(
            snapshot=self.drain_snapshot(
                completed_positions={0, 1, 42, 43, 46, 47},
                observed_at=1_786_390_000,
            ),
            created_at=1_786_390_001,
        )
        snapshot = self.post_abort_v6_snapshot(reference)
        backup = rollback_backup_evidence()
        backup["source_authority"]["generation_before"] = snapshot[
            "generation_before"
        ]
        backup["source_authority"]["generation_after"] = snapshot[
            "generation_after"
        ]
        backup["source_authority_digest"] = digest(backup["source_authority"])

        def create(value):
            return create_post_abort_recovery_plan(
                reference,
                value,
                candidate_release=release_identity(),
                rollback_backup=backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/v6-backup.age",
                rollback_bundle_path="/private/tmp/v6-bundle.age",
                authorization_receipt_path="/private/tmp/v6-auth.json",
                application_receipt_path="/private/tmp/v6-app.json",
                verification_receipt_path="/private/tmp/v6-verify.json",
                rollback_receipt_path="/private/tmp/v6-rollback.json",
                reference_application_authorization=(
                    exact_drain_authorization(reference)
                ),
                reference_application_journal=(
                    exact_drain_application_journal(reference)
                ),
                reference_application_progress_digest="d" * 64,
                schema_version=6,
                created_at=1_786_390_500,
            )

        plan = create(snapshot)
        self.assertEqual(plan["schema_version"], 6)
        self.assertEqual(plan["reference_plan"]["selected_operation_count"], 42)
        self.assertEqual(plan["selected_operation_count"], 4)
        self.assertEqual(
            plan["selected_status_counts"],
            {"failed": 3, "processing": 1},
        )
        self.assertEqual(plan["selected_type_counts"], {"retain": 4})
        self.assertEqual(
            plan["preserved_status_counts"],
            {"completed": 6, "pending": 38},
        )
        self.assertEqual(
            verify_post_abort_recovery_plan(plan, now=plan["created_at"]),
            plan,
        )

        for label, updates in (
            ("retry-count", {"retry_count": 2}),
            (
                "error-evidence",
                {
                    "error_category": "provider_transport",
                    "error_digest": "f" * 64,
                },
            ),
        ):
            with self.subTest(drift=label):
                changed = deepcopy(snapshot)
                failed = next(
                    item
                    for item in changed["operations"]
                    if item["current_status"] == "failed"
                )
                failed.update(updates)
                failed["row_digest"] = digest(
                    {
                        key: item_value
                        for key, item_value in failed.items()
                        if key != "row_digest"
                    }
                )
                changed["snapshot_digest"] = digest(
                    {
                        key: item_value
                        for key, item_value in changed.items()
                        if key != "snapshot_digest"
                    }
                )
                with self.assertRaisesRegex(
                    OperationRecoveryError,
                    "post-abort row set is invalid",
                ):
                    create(changed)

    def test_post_abort_v7_plan_binds_owned_pending_retry(self):
        reference = self.legacy_drain_plan(
            snapshot=self.drain_snapshot(
                completed_positions={0, 1, 42, 43, 46, 47},
                observed_at=1_786_390_000,
            ),
            created_at=1_786_390_001,
        )
        snapshot = self.post_abort_v7_snapshot(reference)
        backup = rollback_backup_evidence()
        backup["source_authority"]["generation_before"] = snapshot[
            "generation_before"
        ]
        backup["source_authority"]["generation_after"] = snapshot[
            "generation_after"
        ]
        backup["source_authority_digest"] = digest(backup["source_authority"])

        def create(value):
            return create_post_abort_recovery_plan(
                reference,
                value,
                candidate_release=release_identity(),
                rollback_backup=backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/v7-backup.age",
                rollback_bundle_path="/private/tmp/v7-bundle.age",
                authorization_receipt_path="/private/tmp/v7-auth.json",
                application_receipt_path="/private/tmp/v7-app.json",
                verification_receipt_path="/private/tmp/v7-verify.json",
                rollback_receipt_path="/private/tmp/v7-rollback.json",
                reference_application_authorization=(
                    exact_drain_authorization(reference)
                ),
                reference_application_journal=(
                    exact_drain_application_journal(reference)
                ),
                reference_application_progress_digest="d" * 64,
                schema_version=7,
                created_at=1_786_390_500,
            )

        plan = create(snapshot)
        self.assertEqual(plan["schema_version"], 7)
        self.assertEqual(plan["selected_operation_count"], 5)
        self.assertEqual(
            plan["selected_status_counts"],
            {"failed": 3, "pending": 1, "processing": 1},
        )
        self.assertEqual(plan["selected_type_counts"], {"retain": 5})
        self.assertEqual(
            plan["preserved_status_counts"],
            {"completed": 6, "pending": 37},
        )
        self.assertEqual(
            verify_post_abort_recovery_plan(plan, now=plan["created_at"]),
            plan,
        )

        changed = deepcopy(snapshot)
        retrying = next(
            item
            for item in changed["operations"]
            if item["current_status"] == "pending"
            and item["worker_id_present"]
        )
        retrying["retry_count"] = 2
        retrying["row_digest"] = digest(
            {key: value for key, value in retrying.items() if key != "row_digest"}
        )
        changed["snapshot_digest"] = digest(
            {
                key: value
                for key, value in changed.items()
                if key != "snapshot_digest"
            }
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "post-abort row set is invalid",
        ):
                    create(changed)

    def test_post_abort_v8_plan_binds_failed_and_processing_retries(self):
        reference = self.legacy_drain_plan(
            snapshot=self.drain_snapshot(
                completed_positions={0, 1, 42, 43, 46, 47},
                observed_at=1_786_390_000,
            ),
            created_at=1_786_390_001,
        )
        snapshot = self.post_abort_v8_snapshot(reference)
        backup = rollback_backup_evidence()
        backup["source_authority"]["generation_before"] = snapshot[
            "generation_before"
        ]
        backup["source_authority"]["generation_after"] = snapshot[
            "generation_after"
        ]
        backup["source_authority_digest"] = digest(backup["source_authority"])

        def create(value):
            return create_post_abort_recovery_plan(
                reference,
                value,
                candidate_release=release_identity(),
                rollback_backup=backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/v8-backup.age",
                rollback_bundle_path="/private/tmp/v8-bundle.age",
                authorization_receipt_path="/private/tmp/v8-auth.json",
                application_receipt_path="/private/tmp/v8-app.json",
                verification_receipt_path="/private/tmp/v8-verify.json",
                rollback_receipt_path="/private/tmp/v8-rollback.json",
                reference_application_authorization=(
                    exact_drain_authorization(reference)
                ),
                reference_application_journal=(
                    exact_drain_application_journal(reference)
                ),
                reference_application_progress_digest="d" * 64,
                schema_version=8,
                created_at=1_786_390_500,
            )

        plan = create(snapshot)

        self.assertEqual(plan["schema_version"], 8)
        self.assertEqual(plan["selected_operation_count"], 2)
        self.assertEqual(
            plan["selected_status_counts"],
            {"failed": 1, "processing": 1},
        )
        self.assertEqual(plan["selected_type_counts"], {"retain": 2})
        self.assertEqual(
            plan["preserved_status_counts"],
            {"completed": 6, "pending": 40},
        )
        self.assertEqual(
            verify_post_abort_recovery_plan(plan, now=plan["created_at"]),
            plan,
        )

        for label, status, updates in (
            ("processing-retry", "processing", {"retry_count": 0}),
            (
                "processing-category",
                "processing",
                {"error_category": "unknown"},
            ),
            ("processing-error-digest", "processing", {"error_digest": None}),
            ("failed-retry", "failed", {"retry_count": 2}),
            (
                "failed-category",
                "failed",
                {"error_category": "provider_transport"},
            ),
            ("failed-error-digest", "failed", {"error_digest": None}),
        ):
            with self.subTest(drift=label):
                changed = deepcopy(snapshot)
                row = next(
                    item
                    for item in changed["operations"]
                    if item["current_status"] == status
                )
                row.update(updates)
                row["row_digest"] = digest(
                    {key: value for key, value in row.items() if key != "row_digest"}
                )
                changed["snapshot_digest"] = digest(
                    {
                        key: value
                        for key, value in changed.items()
                        if key != "snapshot_digest"
                    }
                )
                with self.assertRaisesRegex(
                    OperationRecoveryError,
                    "invalid",
                ):
                    create(changed)

    def test_post_abort_v9_plan_preserves_completed_checkpoint(self):
        reference = self.legacy_drain_plan(
            snapshot=self.drain_snapshot(
                completed_positions={0, 1, 42, 43, 46, 47},
                observed_at=1_786_390_000,
            ),
            created_at=1_786_390_001,
        )
        snapshot = self.post_abort_v9_snapshot(reference)
        backup = rollback_backup_evidence()
        backup["source_authority"]["generation_before"] = snapshot[
            "generation_before"
        ]
        backup["source_authority"]["generation_after"] = snapshot[
            "generation_after"
        ]
        backup["source_authority_digest"] = digest(backup["source_authority"])

        def create(value):
            return create_post_abort_recovery_plan(
                reference,
                value,
                candidate_release=release_identity(),
                rollback_backup=backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/v9-backup.age",
                rollback_bundle_path="/private/tmp/v9-bundle.age",
                authorization_receipt_path="/private/tmp/v9-auth.json",
                application_receipt_path="/private/tmp/v9-app.json",
                verification_receipt_path="/private/tmp/v9-verify.json",
                rollback_receipt_path="/private/tmp/v9-rollback.json",
                reference_application_authorization=(
                    exact_drain_authorization(reference)
                ),
                reference_application_journal=(
                    exact_drain_application_journal(reference)
                ),
                reference_application_progress_digest="e" * 64,
                schema_version=9,
                created_at=1_786_390_500,
            )

        plan = create(snapshot)
        self.assertEqual(plan["schema_version"], 9)
        self.assertEqual(plan["selected_operation_count"], 3)
        self.assertEqual(
            plan["selected_status_counts"],
            {"failed": 1, "pending": 1, "processing": 1},
        )
        self.assertEqual(plan["selected_type_counts"], {"retain": 3})
        self.assertEqual(
            plan["preserved_status_counts"],
            {"completed": 7, "pending": 38},
        )
        self.assertEqual(
            verify_post_abort_recovery_plan(plan, now=plan["created_at"]),
            plan,
        )

        for label, status, updates in (
            ("processing-retry", "processing", {"retry_count": 1}),
            ("pending-retry", "pending", {"retry_count": 2}),
            (
                "pending-category",
                "pending",
                {"error_category": "unknown"},
            ),
            ("pending-error-digest", "pending", {"error_digest": None}),
        ):
            with self.subTest(drift=label):
                changed = deepcopy(snapshot)
                row = next(
                    item
                    for item in changed["operations"]
                    if item["current_status"] == status
                    and (status != "pending" or item["worker_id_present"])
                )
                row.update(updates)
                row["row_digest"] = digest(
                    {
                        key: value
                        for key, value in row.items()
                        if key != "row_digest"
                    }
                )
                changed["snapshot_digest"] = digest(
                    {
                        key: value
                        for key, value in changed.items()
                        if key != "snapshot_digest"
                    }
                )
                with self.assertRaisesRegex(
                    OperationRecoveryError,
                    "invalid",
                ):
                    create(changed)

        for label, updates in (
            ("completed-retry", {"retry_count": 2}),
            ("completed-category", {"error_category": "unknown"}),
            ("completed-error-digest", {"error_digest": None}),
            ("completed-owner", {"worker_id_digest": "7" * 64}),
            ("completed-at", {"completed_at": None}),
        ):
            with self.subTest(drift=label):
                changed = deepcopy(snapshot)
                row = next(
                    item
                    for item in changed["operations"]
                    if item["current_status"] == "completed"
                    and item["worker_id_digest"]
                    == hashlib.sha256(
                        (
                            "operation-recovery-exact-drain-"
                            f"{reference['plan_digest'][:12]}"
                        ).encode()
                    ).hexdigest()
                )
                row.update(updates)
                row["row_digest"] = digest(
                    {
                        key: value
                        for key, value in row.items()
                        if key != "row_digest"
                    }
                )
                changed["snapshot_digest"] = digest(
                    {
                        key: value
                        for key, value in changed.items()
                        if key != "snapshot_digest"
                    }
                )
                with self.assertRaisesRegex(
                    OperationRecoveryError,
                    "invalid",
                ):
                    create(changed)

    def test_post_abort_v10_derives_interrupted_rows_from_invariants(self):
        reference = self.drain_plan(
            snapshot=self.drain_snapshot(
                completed_positions=set(range(7)),
                observed_at=1_786_390_000,
            ),
            created_at=1_786_390_001,
        )
        snapshot = self.post_abort_v10_snapshot(reference)
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

        def create(value):
            return create_post_abort_recovery_plan(
                reference,
                value,
                candidate_release=release_identity(),
                rollback_backup=backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/v10-backup.age",
                rollback_bundle_path="/private/tmp/v10-bundle.age",
                authorization_receipt_path="/private/tmp/v10-auth.json",
                application_receipt_path="/private/tmp/v10-app.json",
                verification_receipt_path="/private/tmp/v10-verify.json",
                rollback_receipt_path="/private/tmp/v10-rollback.json",
                reference_application_authorization=(
                    exact_drain_authorization(reference)
                ),
                reference_application_journal=(
                    exact_drain_application_journal(reference)
                ),
                reference_application_progress_digest="f" * 64,
                schema_version=10,
                created_at=1_786_390_500,
            )

        plan = create(snapshot)

        self.assertEqual(plan["schema_version"], 10)
        self.assertEqual(plan["selected_operation_count"], 39)
        self.assertEqual(
            plan["selected_status_counts"],
            {"failed": 22, "pending": 16, "processing": 1},
        )
        self.assertEqual(
            plan["preserved_status_counts"],
            {"completed": 9},
        )
        self.assertEqual(plan["retry_recovery"]["recovery_epoch_before"], 0)
        self.assertEqual(plan["retry_recovery"]["recovery_epoch_after"], 1)
        self.assertEqual(plan["retry_recovery"]["recovery_epoch_ceiling"], 1)
        self.assertEqual(plan["retry_recovery"]["failed_reset_count"], 22)
        self.assertEqual(
            sum(
                item["reset_applied"]
                for item in plan["retry_recovery"]["operations"]
            ),
            22,
        )
        self.assertEqual(
            verify_post_abort_recovery_plan(plan, now=plan["created_at"]),
            plan,
        )
        selected_ids = {
            item["operation_id"] for item in plan["selected_operations"]
        }
        self.assertEqual(
            plan["selected_checkpoint_set_digest"],
            digest(
                [
                    {
                        "operation_id": item["operation_id"],
                        "result_metadata_digest": item[
                            "result_metadata_digest"
                        ],
                    }
                    for item in snapshot["operations"]
                    if item["operation_id"] in selected_ids
                ]
            ),
        )

        varied = deepcopy(snapshot)
        varied_failed = next(
            item
            for item in varied["operations"]
            if item["current_status"] == "failed"
        )
        varied_failed.update(
            retry_count=2,
            error_category="authentication",
        )
        varied_failed["row_digest"] = digest(
            {
                key: value
                for key, value in varied_failed.items()
                if key != "row_digest"
            }
        )
        varied["snapshot_digest"] = digest(
            {
                key: value
                for key, value in varied.items()
                if key != "snapshot_digest"
            }
        )
        varied_plan = create(varied)
        varied_retry = next(
            item
            for item in varied_plan["retry_recovery"]["operations"]
            if item["operation_id"] == varied_failed["operation_id"]
        )
        self.assertEqual(varied_retry["retry_count_before"], 2)
        self.assertEqual(varied_retry["cumulative_attempt_ceiling"], 7)

        one_unowned = deepcopy(snapshot)
        pending_index = next(
            index
            for index, item in enumerate(one_unowned["operations"])
            if item["current_status"] == "pending"
            and item["worker_id_present"]
        )
        operation_id = one_unowned["operations"][pending_index]["operation_id"]
        one_unowned["operations"][pending_index] = deepcopy(
            next(
                item
                for item in reference["live_snapshot"]["operations"]
                if item["operation_id"] == operation_id
            )
        )
        one_unowned["status_counts"] = {
            status: sum(
                item["current_status"] == status
                for item in one_unowned["operations"]
            )
            for status in recovery_contract.OPERATION_STATUSES
        }
        one_unowned["snapshot_digest"] = digest(
            {
                key: value
                for key, value in one_unowned.items()
                if key != "snapshot_digest"
            }
        )
        one_unowned_plan = create(one_unowned)
        self.assertEqual(one_unowned_plan["selected_operation_count"], 38)
        self.assertEqual(
            one_unowned_plan["preserved_status_counts"],
            {"completed": 9, "pending": 1},
        )

        wrong_owner = deepcopy(snapshot)
        row = next(
            item
            for item in wrong_owner["operations"]
            if item["current_status"] == "failed"
        )
        row["worker_id_digest"] = "0" * 64
        row["row_digest"] = digest(
            {key: value for key, value in row.items() if key != "row_digest"}
        )
        wrong_owner["snapshot_digest"] = digest(
            {
                key: value
                for key, value in wrong_owner.items()
                if key != "snapshot_digest"
            }
        )
        with self.assertRaisesRegex(OperationRecoveryError, "row set is invalid"):
            create(wrong_owner)

        excessive_retry = deepcopy(snapshot)
        row = next(
            item
            for item in excessive_retry["operations"]
            if item["current_status"] == "failed"
        )
        row["retry_count"] = 4
        row["row_digest"] = digest(
            {key: value for key, value in row.items() if key != "row_digest"}
        )
        excessive_retry["snapshot_digest"] = digest(
            {
                key: value
                for key, value in excessive_retry.items()
                if key != "snapshot_digest"
            }
        )
        with self.assertRaisesRegex(OperationRecoveryError, "row set is invalid"):
            create(excessive_retry)

        tampered = deepcopy(plan)
        tampered["retry_recovery"]["recovery_epoch_after"] = 2
        tampered["retry_recovery_digest"] = digest(tampered["retry_recovery"])
        tampered["plan_digest"] = digest(
            {key: value for key, value in tampered.items() if key != "plan_digest"}
        )
        with self.assertRaisesRegex(OperationRecoveryError, "invalid"):
            verify_post_abort_recovery_plan(
                tampered,
                now=tampered["created_at"],
            )

        retry_operation_index = next(
            index
            for index, item in enumerate(
                plan["retry_recovery"]["operations"]
            )
            if item["reset_applied"]
        )
        for label, key, value in (
            ("integer-for-boolean", "reset_applied", 1),
            ("float-for-boolean", "reset_applied", 1.0),
            ("boolean-for-integer", "retry_count_after", False),
        ):
            with self.subTest(retry_recovery_type_confusion=label):
                confused = deepcopy(plan)
                confused["retry_recovery"]["operations"][
                    retry_operation_index
                ][key] = value
                confused["plan_digest"] = digest(
                    {
                        item_key: item_value
                        for item_key, item_value in confused.items()
                        if item_key != "plan_digest"
                    }
                )
                with self.assertRaisesRegex(
                    OperationRecoveryError,
                    "retry recovery is invalid",
                ):
                    verify_post_abort_recovery_plan(
                        confused,
                        now=confused["created_at"],
                    )

        for nested_digest in (
            "operation_set_digest",
            "retry_recovery_digest",
        ):
            with self.subTest(retry_recovery_digest=nested_digest):
                mismatched = deepcopy(plan)
                if nested_digest == "operation_set_digest":
                    mismatched["retry_recovery"][nested_digest] = "0" * 64
                    mismatched["retry_recovery_digest"] = digest(
                        mismatched["retry_recovery"]
                    )
                else:
                    mismatched[nested_digest] = "0" * 64
                mismatched["plan_digest"] = digest(
                    {
                        item_key: item_value
                        for item_key, item_value in mismatched.items()
                        if item_key != "plan_digest"
                    }
                )
                with self.assertRaisesRegex(
                    OperationRecoveryError,
                    "retry recovery is invalid",
                ):
                    verify_post_abort_recovery_plan(
                        mismatched,
                        now=mismatched["created_at"],
                    )

    def test_post_abort_v10_preserves_changed_unowned_pending_row(self):
        reference_snapshot = self.drain_snapshot(
            completed_positions=set(range(7)),
            observed_at=1_786_390_000,
        )
        reference = self.legacy_drain_plan(
            snapshot=reference_snapshot,
            created_at=1_786_390_001,
        )
        preliminary_interrupted = self.post_abort_v10_snapshot(
            reference
        )
        operation_id = next(
            item["operation_id"]
            for item in preliminary_interrupted["operations"]
            if item["current_status"] == "pending"
            and item["worker_id_present"]
        )
        reference_row = next(
            item
            for item in reference["live_snapshot"]["operations"]
            if item["operation_id"] == operation_id
        )
        reference_row["retry_count"] = 1
        reference_row["row_digest"] = digest(
            {
                key: value
                for key, value in reference_row.items()
                if key != "row_digest"
            }
        )
        reference["live_snapshot"]["snapshot_digest"] = digest(
            {
                key: value
                for key, value in reference["live_snapshot"].items()
                if key != "snapshot_digest"
            }
        )
        reference["snapshot_digest"] = reference["live_snapshot"][
            "snapshot_digest"
        ]
        selected_reference_row = next(
            item
            for item in reference["selected_operations"]
            if item["operation_id"] == operation_id
        )
        selected_reference_row["row_digest"] = reference_row["row_digest"]
        reference["selected_row_set_digest"] = (
            recovery_contract._exact_drain_row_set_digest(
                reference["selected_operations"]
            )
        )
        reference["plan_digest"] = digest(
            {
                key: value
                for key, value in reference.items()
                if key != "plan_digest"
            }
        )
        self.assertEqual(
            recovery_contract.verify_exact_drain_plan(
                reference,
                now=reference["created_at"],
            ),
            reference,
        )
        self.assertIn(
            operation_id,
            {
                item["operation_id"]
                for item in reference["selected_operations"]
            },
        )

        snapshot = self.post_abort_v10_snapshot(
            reference,
            observed_at=1_786_825_000,
        )
        reference_row = next(
            item
            for item in reference["live_snapshot"]["operations"]
            if item["operation_id"] == operation_id
        )
        released_row = next(
            item
            for item in snapshot["operations"]
            if item["operation_id"] == operation_id
        )
        released_row.update(
            worker_id_present=False,
            worker_id_digest=None,
            claimed_at=None,
            updated_at="2026-08-15T20:16:00.000000Z",
            result_metadata_digest=reference_row["result_metadata_digest"],
        )
        released_row["row_digest"] = digest(
            {
                key: value
                for key, value in released_row.items()
                if key != "row_digest"
            }
        )
        snapshot["snapshot_digest"] = digest(
            {
                key: value
                for key, value in snapshot.items()
                if key != "snapshot_digest"
            }
        )
        self.assertEqual(reference_row["retry_count"], 1)
        self.assertEqual(released_row["retry_count"], 3)
        self.assertIsNone(released_row["completed_at"])
        self.assertNotEqual(
            released_row["error_digest"],
            reference_row["error_digest"],
        )
        self.assertNotEqual(
            released_row["next_retry_at"],
            reference_row["next_retry_at"],
        )
        self.assertEqual(
            released_row["result_metadata_digest"],
            reference_row["result_metadata_digest"],
        )
        self.assertEqual(
            released_row["created_at"],
            reference_row["created_at"],
        )
        self.assertGreater(
            released_row["updated_at"],
            reference_row["updated_at"],
        )

        backup = rollback_backup_evidence()
        for key in ("generation_before", "generation_after"):
            backup["source_authority"][key] = snapshot[
                "generation_before"
            ]
        backup["source_authority_digest"] = digest(
            backup["source_authority"]
        )

        def create(value):
            return create_post_abort_recovery_plan(
                reference,
                value,
                candidate_release=release_identity(),
                rollback_backup=backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/v10-release-backup.age",
                rollback_bundle_path="/private/tmp/v10-release-bundle.age",
                authorization_receipt_path=(
                    "/private/tmp/v10-release-auth.json"
                ),
                application_receipt_path=(
                    "/private/tmp/v10-release-app.json"
                ),
                verification_receipt_path=(
                    "/private/tmp/v10-release-verify.json"
                ),
                rollback_receipt_path=(
                    "/private/tmp/v10-release-rollback.json"
                ),
                reference_application_authorization=(
                    exact_drain_authorization(reference)
                ),
                reference_application_journal=(
                    exact_drain_application_journal(reference)
                ),
                reference_application_progress_digest="f" * 64,
                schema_version=10,
                created_at=1_786_825_100,
            )

        plan = create(snapshot)
        selected_ids = {
            item["operation_id"] for item in plan["selected_operations"]
        }
        self.assertNotIn(operation_id, selected_ids)
        self.assertNotIn(
            operation_id,
            {
                item["operation_id"]
                for item in plan["retry_recovery"]["operations"]
            },
        )
        self.assertEqual(plan["selected_operation_count"], 38)
        self.assertEqual(
            plan["preserved_status_counts"],
            {"completed": 9, "pending": 1},
        )
        self.assertEqual(
            plan["preserved_row_set_digest"],
            digest(
                [
                    {
                        "operation_id": item["operation_id"],
                        "row_digest": item["row_digest"],
                    }
                    for item in snapshot["operations"]
                    if item["operation_id"] not in selected_ids
                ]
            ),
        )
        self.assertEqual(
            verify_post_abort_recovery_plan(plan, now=plan["created_at"]),
            plan,
        )

        offset_equivalent = deepcopy(snapshot)
        offset_row = next(
            item
            for item in offset_equivalent["operations"]
            if item["operation_id"] == operation_id
        )
        offset_row.update(
            updated_at="2026-08-15T16:16:00.000000-04:00",
            next_retry_at="2026-08-15T16:16:00.000000-04:00",
        )
        offset_row["row_digest"] = digest(
            {
                key: value
                for key, value in offset_row.items()
                if key != "row_digest"
            }
        )
        offset_equivalent["snapshot_digest"] = digest(
            {
                key: value
                for key, value in offset_equivalent.items()
                if key != "snapshot_digest"
            }
        )
        offset_plan = create(offset_equivalent)
        self.assertNotIn(
            operation_id,
            {
                item["operation_id"]
                for item in offset_plan["selected_operations"]
            },
        )

        for label, updates in (
            (
                "completed-at",
                {"completed_at": "2026-08-15T20:17:00.000000Z"},
            ),
            (
                "retry-count-decrease",
                {"retry_count": reference_row["retry_count"] - 1},
            ),
            (
                "retry-count-over-ceiling",
                {"retry_count": reference["worker_max_retries"] + 1},
            ),
            (
                "result-metadata",
                {"result_metadata_digest": "0" * 64},
            ),
            (
                "created-at",
                {"created_at": "2026-07-29T00:00:00Z"},
            ),
            (
                "non-advancing-updated-at",
                {"updated_at": reference_row["updated_at"]},
            ),
            (
                "lexically-later-chronologically-earlier-updated-at",
                {"updated_at": "2026-07-29T13:59:01.000000+14:00"},
            ),
            (
                "future-next-retry-at",
                {"next_retry_at": "2026-08-15T21:00:00.000000Z"},
            ),
            (
                "naive-next-retry-at",
                {"next_retry_at": "2026-08-15T20:16:00.000000"},
            ),
        ):
            with self.subTest(invalid_unowned_pending=label):
                invalid = deepcopy(snapshot)
                invalid_row = next(
                    item
                    for item in invalid["operations"]
                    if item["operation_id"] == operation_id
                )
                invalid_row.update(updates)
                invalid_row["row_digest"] = digest(
                    {
                        key: value
                        for key, value in invalid_row.items()
                        if key != "row_digest"
                    }
                )
                invalid["snapshot_digest"] = digest(
                    {
                        key: value
                        for key, value in invalid.items()
                        if key != "snapshot_digest"
                    }
                )
                with self.assertRaisesRegex(
                    OperationRecoveryError,
                    "row set is invalid",
                ):
                    create(invalid)

    def test_post_abort_v11_supports_verified_rebind_epoch_zero_to_one(self):
        reference = self.drain_plan(
            snapshot=self.drain_snapshot(
                completed_positions=set(range(7)),
                observed_at=1_786_390_000,
            ),
            created_at=1_786_390_001,
        )
        interrupted = self.post_abort_v10_snapshot(reference)
        interrupted["installation_authority"] = (
            rebound_installation_authority()
        )
        interrupted["snapshot_digest"] = digest(
            {
                key: value
                for key, value in interrupted.items()
                if key != "snapshot_digest"
            }
        )
        backup = rollback_backup_evidence()
        backup["source_authority"]["data_identity_digest"] = interrupted[
            "installation_authority"
        ]["observed_data_identity_digest"]
        for key in ("generation_before", "generation_after"):
            backup["source_authority"][key] = interrupted[key]
        backup["source_authority_digest"] = digest(
            backup["source_authority"]
        )

        plan = create_post_abort_recovery_plan(
            reference,
            interrupted,
            candidate_release=release_identity(),
            rollback_backup=backup,
            rollback_encryption=rollback_encryption(),
            rollback_backup_path="/private/tmp/v11-epoch1-backup.age",
            rollback_bundle_path="/private/tmp/v11-epoch1-bundle.age",
            authorization_receipt_path="/private/tmp/v11-epoch1-auth.json",
            application_receipt_path="/private/tmp/v11-epoch1-app.json",
            verification_receipt_path="/private/tmp/v11-epoch1-verify.json",
            rollback_receipt_path="/private/tmp/v11-epoch1-rollback.json",
            reference_application_authorization=(
                exact_drain_authorization(reference)
            ),
            reference_application_journal=(
                exact_drain_application_journal(reference)
            ),
            reference_application_progress_digest="d" * 64,
            schema_version=11,
            created_at=1_786_390_500,
        )

        retry = plan["retry_recovery"]
        self.assertEqual(plan["schema_version"], 11)
        self.assertEqual(retry["schema_version"], 1)
        self.assertEqual(retry["recovery_epoch_before"], 0)
        self.assertEqual(retry["recovery_epoch_after"], 1)
        self.assertEqual(retry["recovery_epoch_ceiling"], 1)
        self.assertNotIn("prior_retry_recovery", retry)
        self.assertNotIn("prior_retry_recovery_digest", retry)
        self.assertEqual(
            verify_post_abort_recovery_plan(plan, now=plan["created_at"]),
            plan,
        )

        selected = {
            item["operation_id"]: item
            for item in plan["selected_operations"]
        }
        retry_after = {
            item["operation_id"]: item["retry_count_after"]
            for item in retry["operations"]
        }
        recovered_rows = deepcopy(interrupted["operations"])
        for row in recovered_rows:
            operation_id = row["operation_id"]
            if operation_id not in selected:
                continue
            expected_status = selected[operation_id]["expected_status"]
            row.update(
                current_status="pending",
                updated_at="2026-08-20T14:00:00.000000Z",
                retry_count=retry_after[operation_id],
                worker_id_present=False,
                worker_id_digest=None,
                claimed_at=None,
            )
            if expected_status == "failed":
                row.update(
                    completed_at=None,
                    next_retry_at=None,
                    error_category="none",
                    error_digest=None,
                )
            row["row_digest"] = digest(
                {
                    key: value
                    for key, value in row.items()
                    if key != "row_digest"
                }
            )
        recovered_snapshot = {
            **interrupted,
            "generation_before": "systalyze:public:81710",
            "generation_after": "systalyze:public:81710",
            "operations": recovered_rows,
            "observed_at": 1_786_820_400,
            "status_counts": {
                status: sum(
                    item["current_status"] == status
                    for item in recovered_rows
                )
                for status in recovery_contract.OPERATION_STATUSES
            },
        }
        recovered_snapshot["snapshot_digest"] = digest(
            {
                key: value
                for key, value in recovered_snapshot.items()
                if key != "snapshot_digest"
            }
        )
        pending_ids = {
            item["operation_id"]
            for item in recovered_rows
            if item["current_status"] == "pending"
        }
        recovery_context = {
            "schema_version": 1,
            "kind": "operation-recovery-exact-drain-recovery-context",
            "origin": "post-abort",
            "generation": recovered_snapshot["generation_before"],
            "recovery_epoch": 1,
            "candidate_release_digest": release_identity()["release_digest"],
            "selected_operation_ids_digest": digest(sorted(pending_ids)),
            "initial_origin_digest": None,
            "post_abort_selected_operation_ids_digest": digest(
                sorted(selected)
            ),
            "post_abort_plan_digest": plan["plan_digest"],
            "post_abort_application_receipt_digest": "a" * 64,
            "post_abort_verification_receipt_digest": "b" * 64,
            "retry_recovery_digest": plan["retry_recovery_digest"],
            "selected_checkpoint_set_digest": plan[
                "selected_checkpoint_set_digest"
            ],
            "preserved_row_set_digest": plan["preserved_row_set_digest"],
        }
        fresh_backup = drain_backup_evidence()
        fresh_backup["source_authority"]["data_identity_digest"] = (
            recovered_snapshot["installation_authority"][
                "observed_data_identity_digest"
            ]
        )
        for key in ("generation_before", "generation_after"):
            fresh_backup["source_authority"][key] = recovered_snapshot[key]
        fresh_backup["source_authority_digest"] = digest(
            fresh_backup["source_authority"]
        )
        fresh = recovery_contract.create_exact_drain_plan(
            self.cohort(),
            recovered_snapshot,
            candidate_release=release_identity(),
            rollback_backup=fresh_backup,
            rollback_backup_path="/private/tmp/v11-epoch1-drain-backup.age",
            provider_policy_digest="9" * 64,
            effective_profile_digest="7" * 64,
            worker_runtime_digest="8" * 64,
            authorization_receipt_path=(
                "/private/tmp/v11-epoch1-drain-auth.json"
            ),
            application_receipt_path=(
                "/private/tmp/v11-epoch1-drain-app.json"
            ),
            status_artifact_path=(
                "/private/tmp/v11-epoch1-drain-status.json"
            ),
            verification_receipt_path=(
                "/private/tmp/v11-epoch1-drain-verify.json"
            ),
            recovery_context=recovery_context,
            schema_version=11,
            created_at=1_786_820_500,
        )
        self.assertEqual(fresh["schema_version"], 11)
        self.assertEqual(fresh["recovery_context"]["schema_version"], 1)
        self.assertEqual(fresh["recovery_context"]["recovery_epoch"], 1)

        second_rows = deepcopy(recovered_rows)
        second_worker_digest = hashlib.sha256(
            (
                "operation-recovery-exact-drain-"
                f"{fresh['plan_digest'][:12]}"
            ).encode()
        ).hexdigest()
        fresh_selected_ids = {
            item["operation_id"] for item in fresh["selected_operations"]
        }
        for row in second_rows:
            if row["operation_id"] not in fresh_selected_ids:
                continue
            row.update(
                current_status="failed",
                updated_at="2026-08-20T15:00:00.000000Z",
                completed_at="2026-08-20T15:00:00.000000Z",
                retry_count=3,
                next_retry_at=None,
                worker_id_present=True,
                worker_id_digest=second_worker_digest,
                claimed_at="2026-08-20T14:00:00.000000Z",
                error_category="provider_transport",
                error_digest="c" * 64,
            )
            row["row_digest"] = digest(
                {
                    key: value
                    for key, value in row.items()
                    if key != "row_digest"
                }
            )
        second_snapshot = {
            **recovered_snapshot,
            "generation_before": "systalyze:public:81711",
            "generation_after": "systalyze:public:81711",
            "operations": second_rows,
            "status_counts": {
                status: sum(
                    item["current_status"] == status for item in second_rows
                )
                for status in recovery_contract.OPERATION_STATUSES
            },
            "observed_at": 1_786_824_000,
        }
        second_snapshot["snapshot_digest"] = digest(
            {
                key: value
                for key, value in second_snapshot.items()
                if key != "snapshot_digest"
            }
        )
        second_backup = rollback_backup_evidence()
        second_backup["source_authority"]["data_identity_digest"] = (
            second_snapshot["installation_authority"][
                "observed_data_identity_digest"
            ]
        )
        for key in ("generation_before", "generation_after"):
            second_backup["source_authority"][key] = second_snapshot[key]
        second_backup["source_authority_digest"] = digest(
            second_backup["source_authority"]
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "retry recovery is invalid",
        ):
            create_post_abort_recovery_plan(
                fresh,
                second_snapshot,
                candidate_release=release_identity(),
                rollback_backup=second_backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/v11-omit-backup.age",
                rollback_bundle_path="/private/tmp/v11-omit-bundle.age",
                authorization_receipt_path="/private/tmp/v11-omit-auth.json",
                application_receipt_path="/private/tmp/v11-omit-app.json",
                verification_receipt_path=(
                    "/private/tmp/v11-omit-verify.json"
                ),
                rollback_receipt_path=(
                    "/private/tmp/v11-omit-rollback.json"
                ),
                reference_application_authorization=(
                    exact_drain_authorization(fresh)
                ),
                reference_application_journal=(
                    exact_drain_application_journal(fresh)
                ),
                reference_application_progress_digest="d" * 64,
                schema_version=11,
                created_at=1_786_824_100,
            )
        epoch_two = create_post_abort_recovery_plan(
            fresh,
            second_snapshot,
            candidate_release=release_identity(),
            rollback_backup=second_backup,
            rollback_encryption=rollback_encryption(),
            rollback_backup_path="/private/tmp/v11-chain-backup.age",
            rollback_bundle_path="/private/tmp/v11-chain-bundle.age",
            authorization_receipt_path="/private/tmp/v11-chain-auth.json",
            application_receipt_path="/private/tmp/v11-chain-app.json",
            verification_receipt_path="/private/tmp/v11-chain-verify.json",
            rollback_receipt_path="/private/tmp/v11-chain-rollback.json",
            reference_application_authorization=(
                exact_drain_authorization(fresh)
            ),
            reference_application_journal=(
                exact_drain_application_journal(fresh)
            ),
            reference_application_progress_digest="d" * 64,
            prior_retry_recovery=retry,
            schema_version=11,
            created_at=1_786_824_100,
        )
        self.assertEqual(
            epoch_two["retry_recovery"]["recovery_epoch_after"],
            2,
        )
        self.assertEqual(
            verify_post_abort_recovery_plan(
                epoch_two,
                now=epoch_two["created_at"],
            ),
            epoch_two,
        )

    def test_schema_fifteen_grant_does_not_authorize_post_abort(self):
        created_at = 1_786_390_001
        reference_snapshot = self.drain_snapshot(
            completed_positions=set(range(7)),
            observed_at=created_at - 1,
        )
        _reference, grant, reference, _create_plan = (
            self.standing_grant_fixture(
                created_at=created_at,
                snapshot=reference_snapshot,
            )
        )
        ledger = recovery_contract.create_exact_drain_grant_ledger(
            grant,
            ledger_nonce="8" * 64,
            created_at=created_at,
        )
        _ledger, use = recovery_contract.claim_exact_drain_grant(
            ledger,
            reference,
            expected_ledger_digest=ledger["ledger_digest"],
            claim_nonce="9" * 64,
            ledger_nonce="a" * 64,
            claimed_at=created_at,
        )
        authorization = (
            recovery_contract.create_exact_drain_grant_authorization_receipt(
                reference,
                use,
            )
        )
        legacy_journal = exact_drain_application_journal(reference)
        journal_body = {
            key: value
            for key, value in legacy_journal.items()
            if key != "receipt_digest"
        }
        journal_body.update(
            schema_version=2,
            authorization_receipt_digest=authorization["receipt_digest"],
            grant_id=grant["grant_id"],
            grant_digest=grant["grant_digest"],
        )
        journal = {**journal_body, "receipt_digest": digest(journal_body)}
        interrupted = self.post_abort_v10_snapshot(reference)
        interrupted["installation_authority"] = (
            rebound_installation_authority()
        )
        interrupted["snapshot_digest"] = digest(
            {
                key: value
                for key, value in interrupted.items()
                if key != "snapshot_digest"
            }
        )
        backup = rollback_backup_evidence()
        backup["source_authority"]["data_identity_digest"] = interrupted[
            "installation_authority"
        ]["observed_data_identity_digest"]
        for key in ("generation_before", "generation_after"):
            backup["source_authority"][key] = interrupted[key]
        backup["source_authority_digest"] = digest(
            backup["source_authority"]
        )

        post_abort = create_post_abort_recovery_plan(
            reference,
            interrupted,
            candidate_release=release_identity(),
            rollback_backup=backup,
            rollback_encryption=rollback_encryption(),
            rollback_backup_path="/private/tmp/v15-post-abort-backup.age",
            rollback_bundle_path="/private/tmp/v15-post-abort-bundle.age",
            authorization_receipt_path="/private/tmp/v15-post-abort-auth.json",
            application_receipt_path="/private/tmp/v15-post-abort-app.json",
            verification_receipt_path="/private/tmp/v15-post-abort-verify.json",
            rollback_receipt_path="/private/tmp/v15-post-abort-rollback.json",
            reference_application_authorization=authorization,
            reference_application_journal=journal,
            reference_application_progress_digest="d" * 64,
            schema_version=11,
            created_at=created_at + 500,
        )

        self.assertEqual(post_abort["authority"], "unapproved-plan")
        self.assertFalse(post_abort["mutation_authorized"])
        self.assertEqual(
            verify_post_abort_recovery_plan(
                post_abort,
                now=post_abort["created_at"],
            ),
            post_abort,
        )

    def test_exact_drain_binds_verified_post_abort_retry_lineage(self):
        reference = self.drain_plan(
            snapshot=self.drain_snapshot(
                completed_positions=set(range(7)),
                observed_at=1_786_390_000,
            ),
            created_at=1_786_390_001,
        )
        interrupted = self.post_abort_v10_snapshot(reference)
        reference_rows = {
            item["operation_id"]: item
            for item in reference["live_snapshot"]["operations"]
        }
        interrupted["operations"] = [
            deepcopy(reference_rows[item["operation_id"]])
            if item["current_status"] == "pending"
            else item
            for item in interrupted["operations"]
        ]
        interrupted["status_counts"] = {
            status: sum(
                item["current_status"] == status
                for item in interrupted["operations"]
            )
            for status in recovery_contract.OPERATION_STATUSES
        }
        interrupted["snapshot_digest"] = digest(
            {
                key: value
                for key, value in interrupted.items()
                if key != "snapshot_digest"
            }
        )
        recovery_backup = rollback_backup_evidence()
        for key in ("generation_before", "generation_after"):
            recovery_backup["source_authority"][key] = interrupted[
                "generation_before"
            ]
        recovery_backup["source_authority_digest"] = digest(
            recovery_backup["source_authority"]
        )
        recovery_plan = create_post_abort_recovery_plan(
            reference,
            interrupted,
            candidate_release=release_identity(),
            rollback_backup=recovery_backup,
            rollback_encryption=rollback_encryption(),
            rollback_backup_path="/private/tmp/v10-handoff-backup.age",
            rollback_bundle_path="/private/tmp/v10-handoff-bundle.age",
            authorization_receipt_path="/private/tmp/v10-handoff-auth.json",
            application_receipt_path="/private/tmp/v10-handoff-app.json",
            verification_receipt_path="/private/tmp/v10-handoff-verify.json",
            rollback_receipt_path="/private/tmp/v10-handoff-rollback.json",
            reference_application_authorization=(
                exact_drain_authorization(reference)
            ),
            reference_application_journal=(
                exact_drain_application_journal(reference)
            ),
            reference_application_progress_digest="f" * 64,
            created_at=1_786_390_500,
        )
        selected_ids = {
            item["operation_id"]
            for item in recovery_plan["selected_operations"]
        }
        self.assertEqual(len(selected_ids), 23)
        retry_after = {
            item["operation_id"]: item["retry_count_after"]
            for item in recovery_plan["retry_recovery"]["operations"]
        }
        recovered_rows = []
        for item in interrupted["operations"]:
            row = {
                "operation_id": item["operation_id"],
                "bank_id": "engineering",
                "operation_type": item["operation_type"],
                "status": item["current_status"],
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
                "completed_at": item["completed_at"],
                "retry_count": item["retry_count"],
                "next_retry_at": item["next_retry_at"],
                "worker_id_present": item["worker_id_present"],
                "worker_id_digest": item["worker_id_digest"],
                "claimed_at": item["claimed_at"],
                "task_payload_present": item["task_payload_present"],
                "task_payload_digest": item["task_payload_digest"],
                "result_metadata_digest": item[
                    "result_metadata_digest"
                ],
                "error_category": item["error_category"],
                "error_digest": item["error_digest"],
            }
            if item["operation_id"] in selected_ids:
                was_failed = row["status"] == "failed"
                row.update(
                    status="pending",
                    updated_at="2026-08-15T21:00:00.000000Z",
                    completed_at=None if was_failed else row["completed_at"],
                    retry_count=retry_after[item["operation_id"]],
                    next_retry_at=None if was_failed else row["next_retry_at"],
                    worker_id_present=False,
                    worker_id_digest=None,
                    claimed_at=None,
                    error_category="none" if was_failed else row["error_category"],
                    error_digest=None if was_failed else row["error_digest"],
                )
            recovered_rows.append(row)
        recovered_snapshot = dict(
            create_live_snapshot(
                self.cohort(),
                recovered_rows,
                generation_before="systalyze:public:81700",
                generation_after="systalyze:public:81700",
                installation_authority=installation_authority(),
                observed_at=1_786_390_700,
            )
        )
        fresh_selected_ids = {
            item["operation_id"]
            for item in recovered_snapshot["operations"]
            if item["current_status"] == "pending"
        }
        self.assertEqual(len(fresh_selected_ids), 39)
        recovery_context = {
            "schema_version": 1,
            "kind": "operation-recovery-exact-drain-recovery-context",
            "origin": "post-abort",
            "generation": recovered_snapshot["generation_before"],
            "recovery_epoch": 1,
            "candidate_release_digest": release_identity()["release_digest"],
            "selected_operation_ids_digest": digest(
                sorted(fresh_selected_ids)
            ),
            "initial_origin_digest": None,
            "post_abort_selected_operation_ids_digest": digest(
                sorted(selected_ids)
            ),
            "post_abort_plan_digest": recovery_plan["plan_digest"],
            "post_abort_application_receipt_digest": "a" * 64,
            "post_abort_verification_receipt_digest": "b" * 64,
            "retry_recovery_digest": recovery_plan[
                "retry_recovery_digest"
            ],
            "selected_checkpoint_set_digest": recovery_plan[
                "selected_checkpoint_set_digest"
            ],
            "preserved_row_set_digest": recovery_plan[
                "preserved_row_set_digest"
            ],
        }
        drain_backup = drain_backup_evidence()
        for key in ("generation_before", "generation_after"):
            drain_backup["source_authority"][key] = recovered_snapshot[
                "generation_before"
            ]
        drain_backup["source_authority_digest"] = digest(
            drain_backup["source_authority"]
        )
        plan = recovery_contract.create_exact_drain_plan(
            self.cohort(),
            recovered_snapshot,
            candidate_release=release_identity(),
            rollback_backup=drain_backup,
            rollback_backup_path="/private/tmp/v10-resume-backup.age",
            provider_policy_digest="9" * 64,
            effective_profile_digest="7" * 64,
            worker_runtime_digest="8" * 64,
            authorization_receipt_path="/private/tmp/v10-resume-auth.json",
            application_receipt_path="/private/tmp/v10-resume-app.json",
            status_artifact_path="/private/tmp/v10-resume-status.json",
            verification_receipt_path="/private/tmp/v10-resume-verify.json",
            recovery_context=recovery_context,
            created_at=1_786_390_701,
            schema_version=10,
        )

        self.assertEqual(plan["selected_operation_count"], 39)
        plan_selected_ids = {
            item["operation_id"] for item in plan["selected_operations"]
        }
        self.assertEqual(
            plan["execution_window"]["remaining_attempt_count"],
            sum(
                plan["worker_max_retries"] - item["retry_count"] + 1
                for item in plan["live_snapshot"]["operations"]
                if item["operation_id"] in plan_selected_ids
            ),
        )
        window = plan["execution_window"]
        self.assertEqual(
            window["calculated_seconds"],
            window["remaining_attempt_count"]
            * window["phase_one_timeout_seconds"]
            + window["retry_wait_count"]
            * window["maximum_retry_delay_seconds"]
            + window["startup_margin_seconds"]
            + window["transaction_margin_seconds"]
            + window["shutdown_margin_seconds"],
        )
        self.assertEqual(plan["recovery_context"], recovery_context)
        self.assertEqual(
            recovery_contract.verify_exact_drain_plan(
                plan,
                now=plan["created_at"],
            ),
            plan,
        )

        second_rows = deepcopy(recovered_rows)
        second_worker_digest = hashlib.sha256(
            (
                "operation-recovery-exact-drain-"
                f"{plan['plan_digest'][:12]}"
            ).encode()
        ).hexdigest()
        for second_failed in second_rows:
            if second_failed["operation_id"] not in plan_selected_ids:
                continue
            second_failed.update(
                status="failed",
                updated_at="2026-08-20T14:10:00.000000Z",
                completed_at="2026-08-20T14:10:00.000000Z",
                retry_count=3,
                next_retry_at=None,
                worker_id_present=True,
                worker_id_digest=second_worker_digest,
                claimed_at="2026-08-20T14:05:00.000000Z",
                error_category="provider_transport",
                error_digest="c" * 64,
            )
        second_interrupted = dict(
            create_live_snapshot(
                self.cohort(),
                second_rows,
                generation_before="systalyze:public:81701",
                generation_after="systalyze:public:81701",
                installation_authority=installation_authority(),
                observed_at=1_786_829_500,
            )
        )
        second_backup = rollback_backup_evidence()
        for key in ("generation_before", "generation_after"):
            second_backup["source_authority"][key] = second_interrupted[key]
        second_backup["source_authority_digest"] = digest(
            second_backup["source_authority"]
        )
        rebound_second_interrupted = dict(
            create_live_snapshot(
                self.cohort(),
                second_rows,
                generation_before="systalyze:public:81701",
                generation_after="systalyze:public:81701",
                installation_authority=rebound_installation_authority(),
                observed_at=1_786_829_500,
            )
        )
        rebound_second_backup = deepcopy(second_backup)
        rebound_second_backup["source_authority"][
            "data_identity_digest"
        ] = rebound_second_interrupted["installation_authority"][
            "observed_data_identity_digest"
        ]
        rebound_second_backup["source_authority_digest"] = digest(
            rebound_second_backup["source_authority"]
        )
        schema_eleven_release_rows = deepcopy(second_rows)
        release_only_id = min(plan_selected_ids)
        for item in schema_eleven_release_rows:
            if item["operation_id"] == release_only_id:
                item.update(
                    status="pending",
                    completed_at=None,
                    next_retry_at="2026-08-20T14:09:00.000000Z",
                )
                break
        schema_eleven_release_snapshot = dict(
            create_live_snapshot(
                self.cohort(),
                schema_eleven_release_rows,
                generation_before="systalyze:public:81701",
                generation_after="systalyze:public:81701",
                installation_authority=rebound_installation_authority(),
                observed_at=1_786_829_500,
            )
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "post-abort retry recovery is invalid",
        ):
            create_post_abort_recovery_plan(
                plan,
                schema_eleven_release_snapshot,
                candidate_release=release_identity(),
                rollback_backup=rebound_second_backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/v11-release-backup.age",
                rollback_bundle_path="/private/tmp/v11-release-bundle.age",
                authorization_receipt_path="/private/tmp/v11-release-auth.json",
                application_receipt_path="/private/tmp/v11-release-app.json",
                verification_receipt_path="/private/tmp/v11-release-verify.json",
                rollback_receipt_path="/private/tmp/v11-release-rollback.json",
                reference_application_authorization=(
                    exact_drain_authorization(plan)
                ),
                reference_application_journal=(
                    exact_drain_application_journal(plan)
                ),
                reference_application_progress_digest="d" * 64,
                prior_retry_recovery=recovery_plan["retry_recovery"],
                schema_version=11,
                created_at=1_786_829_600,
            )
        tampered_rebind = deepcopy(rebound_second_interrupted)
        tampered_rebind["installation_authority"][
            "data_identity_rebind_handoff"
        ]["post_evidence_digest"] = "0" * 64
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "data-identity rebind handoff is invalid",
        ):
            verify_live_snapshot(tampered_rebind)
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "retry recovery is invalid",
        ):
            create_post_abort_recovery_plan(
                plan,
                second_interrupted,
                candidate_release=release_identity(),
                rollback_backup=second_backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/v10-replay-backup.age",
                rollback_bundle_path="/private/tmp/v10-replay-bundle.age",
                authorization_receipt_path="/private/tmp/v10-replay-auth.json",
                application_receipt_path="/private/tmp/v10-replay-app.json",
                verification_receipt_path="/private/tmp/v10-replay-verify.json",
                rollback_receipt_path="/private/tmp/v10-replay-rollback.json",
                reference_application_authorization=(
                    exact_drain_authorization(plan)
                ),
                reference_application_journal=(
                    exact_drain_application_journal(plan)
                ),
                reference_application_progress_digest="d" * 64,
                created_at=1_786_829_600,
            )

        with self.assertRaisesRegex(
            OperationRecoveryError,
            "verified rebind authority requires schema 11",
        ):
            create_post_abort_recovery_plan(
                plan,
                rebound_second_interrupted,
                candidate_release=release_identity(),
                rollback_backup=rebound_second_backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/v10-rebind-backup.age",
                rollback_bundle_path="/private/tmp/v10-rebind-bundle.age",
                authorization_receipt_path="/private/tmp/v10-rebind-auth.json",
                application_receipt_path="/private/tmp/v10-rebind-app.json",
                verification_receipt_path="/private/tmp/v10-rebind-verify.json",
                rollback_receipt_path="/private/tmp/v10-rebind-rollback.json",
                reference_application_authorization=(
                    exact_drain_authorization(plan)
                ),
                reference_application_journal=(
                    exact_drain_application_journal(plan)
                ),
                reference_application_progress_digest="d" * 64,
                schema_version=10,
                created_at=1_786_829_600,
            )

        with self.assertRaisesRegex(
            OperationRecoveryError,
            "schema-11 recovery requires verified rebind authority",
        ):
            create_post_abort_recovery_plan(
                plan,
                second_interrupted,
                candidate_release=release_identity(),
                rollback_backup=second_backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/v11-legacy-backup.age",
                rollback_bundle_path="/private/tmp/v11-legacy-bundle.age",
                authorization_receipt_path="/private/tmp/v11-legacy-auth.json",
                application_receipt_path="/private/tmp/v11-legacy-app.json",
                verification_receipt_path="/private/tmp/v11-legacy-verify.json",
                rollback_receipt_path="/private/tmp/v11-legacy-rollback.json",
                reference_application_authorization=(
                    exact_drain_authorization(plan)
                ),
                reference_application_journal=(
                    exact_drain_application_journal(plan)
                ),
                reference_application_progress_digest="d" * 64,
                prior_retry_recovery=recovery_plan["retry_recovery"],
                schema_version=11,
                created_at=1_786_829_600,
            )

        mismatched_authority = rebound_installation_authority()
        mismatched_handoff = mismatched_authority[
            "data_identity_rebind_handoff"
        ]
        mismatched_handoff[
            "reference_observed_data_identity_digest"
        ] = "0" * 64
        mismatched_handoff["handoff_digest"] = digest(
            {
                key: value
                for key, value in mismatched_handoff.items()
                if key != "handoff_digest"
            }
        )
        mismatched_snapshot = dict(
            create_live_snapshot(
                self.cohort(),
                second_rows,
                generation_before="systalyze:public:81701",
                generation_after="systalyze:public:81701",
                installation_authority=mismatched_authority,
                observed_at=1_786_829_500,
            )
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "post-abort row set is invalid",
        ):
            create_post_abort_recovery_plan(
                plan,
                mismatched_snapshot,
                candidate_release=release_identity(),
                rollback_backup=rebound_second_backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/v11-mismatch-backup.age",
                rollback_bundle_path="/private/tmp/v11-mismatch-bundle.age",
                authorization_receipt_path="/private/tmp/v11-mismatch-auth.json",
                application_receipt_path="/private/tmp/v11-mismatch-app.json",
                verification_receipt_path="/private/tmp/v11-mismatch-verify.json",
                rollback_receipt_path="/private/tmp/v11-mismatch-rollback.json",
                reference_application_authorization=(
                    exact_drain_authorization(plan)
                ),
                reference_application_journal=(
                    exact_drain_application_journal(plan)
                ),
                reference_application_progress_digest="d" * 64,
                prior_retry_recovery=recovery_plan["retry_recovery"],
                schema_version=11,
                created_at=1_786_829_600,
            )

        with self.assertRaisesRegex(
            OperationRecoveryError,
            "schema does not match recovery epoch",
        ):
            create_post_abort_recovery_plan(
                plan,
                rebound_second_interrupted,
                candidate_release=release_identity(),
                rollback_backup=rebound_second_backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/v12-epoch2-backup.age",
                rollback_bundle_path="/private/tmp/v12-epoch2-bundle.age",
                authorization_receipt_path="/private/tmp/v12-epoch2-auth.json",
                application_receipt_path="/private/tmp/v12-epoch2-app.json",
                verification_receipt_path="/private/tmp/v12-epoch2-verify.json",
                rollback_receipt_path="/private/tmp/v12-epoch2-rollback.json",
                reference_application_authorization=(
                    exact_drain_authorization(plan)
                ),
                reference_application_journal=(
                    exact_drain_application_journal(plan)
                ),
                reference_application_progress_digest="d" * 64,
                prior_retry_recovery=recovery_plan["retry_recovery"],
                schema_version=12,
                created_at=1_786_829_600,
            )

        epoch_two_recovery = create_post_abort_recovery_plan(
            plan,
            rebound_second_interrupted,
            candidate_release=release_identity(),
            rollback_backup=rebound_second_backup,
            rollback_encryption=rollback_encryption(),
            rollback_backup_path="/private/tmp/v11-epoch2-backup.age",
            rollback_bundle_path="/private/tmp/v11-epoch2-bundle.age",
            authorization_receipt_path="/private/tmp/v11-epoch2-auth.json",
            application_receipt_path="/private/tmp/v11-epoch2-app.json",
            verification_receipt_path="/private/tmp/v11-epoch2-verify.json",
            rollback_receipt_path="/private/tmp/v11-epoch2-rollback.json",
            reference_application_authorization=(
                exact_drain_authorization(plan)
            ),
            reference_application_journal=(
                exact_drain_application_journal(plan)
            ),
            reference_application_progress_digest="d" * 64,
            prior_retry_recovery=recovery_plan["retry_recovery"],
            schema_version=11,
            created_at=1_786_829_600,
        )
        self.assertEqual(epoch_two_recovery["schema_version"], 11)
        self.assertEqual(
            epoch_two_recovery["selected_status_counts"],
            {"failed": 39},
        )
        epoch_two_retry = epoch_two_recovery["retry_recovery"]
        self.assertEqual(epoch_two_retry["schema_version"], 2)
        self.assertEqual(epoch_two_retry["recovery_epoch_before"], 1)
        self.assertEqual(epoch_two_retry["recovery_epoch_after"], 2)
        self.assertEqual(epoch_two_retry["recovery_epoch_ceiling"], 2)
        self.assertEqual(epoch_two_retry["failed_reset_count"], 39)
        self.assertEqual(
            epoch_two_retry["prior_retry_recovery_digest"],
            recovery_plan["retry_recovery_digest"],
        )
        self.assertEqual(
            epoch_two_retry["prior_retry_recovery"],
            recovery_plan["retry_recovery"],
        )
        self.assertEqual(
            verify_post_abort_recovery_plan(
                epoch_two_recovery,
                now=epoch_two_recovery["created_at"],
            ),
            epoch_two_recovery,
        )
        first_cycle_by_id = {
            item["operation_id"]: item
            for item in recovery_plan["retry_recovery"]["operations"]
        }
        reference_retry_by_id = {
            item["operation_id"]: item["retry_count"]
            for item in plan["live_snapshot"]["operations"]
        }
        for item in epoch_two_retry["operations"]:
            self.assertEqual(
                item["prior_attempts_consumed"],
                (
                    first_cycle_by_id[item["operation_id"]][
                        "attempts_consumed_before"
                    ]
                    if item["operation_id"] in first_cycle_by_id
                    else reference_retry_by_id[item["operation_id"]]
                ),
            )
            self.assertEqual(
                item["attempts_consumed_before"],
                item["prior_attempts_consumed"]
                + item["attempts_consumed_during_reference"],
            )
            self.assertLessEqual(item["cumulative_attempt_ceiling"], 12)

        for label, path, value in (
            (
                "prior-digest",
                ("prior_retry_recovery_digest",),
                "0" * 64,
            ),
            (
                "attempt-type",
                ("operations", 0, "prior_attempts_consumed"),
                False,
            ),
        ):
            with self.subTest(epoch_two_tamper=label):
                tampered = deepcopy(epoch_two_recovery)
                target = tampered["retry_recovery"]
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                tampered["retry_recovery_digest"] = digest(
                    tampered["retry_recovery"]
                )
                tampered["plan_digest"] = digest(
                    {
                        key: item
                        for key, item in tampered.items()
                        if key != "plan_digest"
                    }
                )
                with self.assertRaisesRegex(
                    OperationRecoveryError,
                    "retry recovery is invalid",
                ):
                    verify_post_abort_recovery_plan(
                        tampered,
                        now=tampered["created_at"],
                    )

        epoch_two_selected_ids = {
            item["operation_id"]
            for item in epoch_two_recovery["selected_operations"]
        }
        epoch_two_recovered_rows = []
        for item in rebound_second_interrupted["operations"]:
            row = {
                "operation_id": item["operation_id"],
                "bank_id": "engineering",
                "operation_type": item["operation_type"],
                "status": item["current_status"],
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
                "completed_at": item["completed_at"],
                "retry_count": item["retry_count"],
                "next_retry_at": item["next_retry_at"],
                "worker_id_present": item["worker_id_present"],
                "worker_id_digest": item["worker_id_digest"],
                "claimed_at": item["claimed_at"],
                "task_payload_present": item["task_payload_present"],
                "task_payload_digest": item["task_payload_digest"],
                "result_metadata_digest": item[
                    "result_metadata_digest"
                ],
                "error_category": item["error_category"],
                "error_digest": item["error_digest"],
            }
            if item["operation_id"] in epoch_two_selected_ids:
                row.update(
                    status="pending",
                    updated_at="2026-08-20T14:20:00.000000Z",
                    completed_at=None,
                    retry_count=0,
                    next_retry_at=None,
                    worker_id_present=False,
                    worker_id_digest=None,
                    claimed_at=None,
                    error_category="none",
                    error_digest=None,
                )
            epoch_two_recovered_rows.append(row)
        epoch_two_recovered_snapshot = dict(
            create_live_snapshot(
                self.cohort(),
                epoch_two_recovered_rows,
                generation_before="systalyze:public:81702",
                generation_after="systalyze:public:81702",
                installation_authority=rebound_installation_authority(),
                observed_at=1_786_829_700,
            )
        )
        epoch_two_context = {
            "schema_version": 2,
            "kind": "operation-recovery-exact-drain-recovery-context",
            "origin": "post-abort",
            "generation": epoch_two_recovered_snapshot[
                "generation_before"
            ],
            "recovery_epoch": 2,
            "candidate_release_digest": release_identity()[
                "release_digest"
            ],
            "selected_operation_ids_digest": digest(
                sorted(epoch_two_selected_ids)
            ),
            "initial_origin_digest": None,
            "post_abort_selected_operation_ids_digest": digest(
                sorted(epoch_two_selected_ids)
            ),
            "post_abort_plan_digest": epoch_two_recovery["plan_digest"],
            "post_abort_application_receipt_digest": "e" * 64,
            "post_abort_verification_receipt_digest": "f" * 64,
            "retry_recovery_digest": epoch_two_recovery[
                "retry_recovery_digest"
            ],
            "selected_checkpoint_set_digest": epoch_two_recovery[
                "selected_checkpoint_set_digest"
            ],
            "preserved_row_set_digest": epoch_two_recovery[
                "preserved_row_set_digest"
            ],
        }
        epoch_two_drain_backup = drain_backup_evidence()
        epoch_two_drain_backup["source_authority"][
            "data_identity_digest"
        ] = epoch_two_recovered_snapshot["installation_authority"][
            "observed_data_identity_digest"
        ]
        for key in ("generation_before", "generation_after"):
            epoch_two_drain_backup["source_authority"][key] = (
                epoch_two_recovered_snapshot[key]
            )
        epoch_two_drain_backup["source_authority_digest"] = digest(
            epoch_two_drain_backup["source_authority"]
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "verified rebind authority requires schema 11",
        ):
            recovery_contract.create_exact_drain_plan(
                self.cohort(),
                epoch_two_recovered_snapshot,
                candidate_release=release_identity(),
                rollback_backup=epoch_two_drain_backup,
                rollback_backup_path=(
                    "/private/tmp/v10-rebind-drain-backup.age"
                ),
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path=(
                    "/private/tmp/v10-rebind-drain-auth.json"
                ),
                application_receipt_path=(
                    "/private/tmp/v10-rebind-drain-app.json"
                ),
                status_artifact_path=(
                    "/private/tmp/v10-rebind-drain-status.json"
                ),
                verification_receipt_path=(
                    "/private/tmp/v10-rebind-drain-verify.json"
                ),
                recovery_context=epoch_two_context,
                created_at=1_786_829_701,
                schema_version=10,
            )
        epoch_two_plan = recovery_contract.create_exact_drain_plan(
            self.cohort(),
            epoch_two_recovered_snapshot,
            candidate_release=release_identity(),
            rollback_backup=epoch_two_drain_backup,
            rollback_backup_path="/private/tmp/v11-epoch2-drain-backup.age",
            provider_policy_digest="9" * 64,
            effective_profile_digest="7" * 64,
            worker_runtime_digest="8" * 64,
            authorization_receipt_path="/private/tmp/v11-epoch2-drain-auth.json",
            application_receipt_path="/private/tmp/v11-epoch2-drain-app.json",
            status_artifact_path="/private/tmp/v11-epoch2-drain-status.json",
            verification_receipt_path="/private/tmp/v11-epoch2-drain-verify.json",
            recovery_context=epoch_two_context,
            created_at=1_786_829_701,
            schema_version=11,
        )
        self.assertEqual(epoch_two_plan["recovery_context"], epoch_two_context)
        self.assertEqual(
            recovery_contract.verify_exact_drain_plan(
                epoch_two_plan,
                now=epoch_two_plan["created_at"],
            ),
            epoch_two_plan,
        )

        epoch_three_worker_digest = hashlib.sha256(
            (
                "operation-recovery-exact-drain-"
                f"{epoch_two_plan['plan_digest'][:12]}"
            ).encode()
        ).hexdigest()
        epoch_three_rows = deepcopy(epoch_two_recovered_rows)
        owned_pending_id = min(epoch_two_selected_ids)
        released_pending_id = min(
            epoch_two_selected_ids - {owned_pending_id}
        )
        for item in epoch_three_rows:
            if item["operation_id"] not in epoch_two_selected_ids:
                continue
            if item["operation_id"] == owned_pending_id:
                item.update(
                    status="pending",
                    updated_at="2026-08-20T14:30:00.000000Z",
                    completed_at=None,
                    retry_count=1,
                    next_retry_at="2026-08-20T14:29:00.000000Z",
                    worker_id_present=True,
                    worker_id_digest=epoch_three_worker_digest,
                    claimed_at="2026-08-20T14:25:00.000000Z",
                    error_category="provider_transport",
                    error_digest="2" * 64,
                )
            elif item["operation_id"] == released_pending_id:
                item.update(
                    status="pending",
                    updated_at="2026-08-20T14:30:00.000000Z",
                    completed_at=None,
                    retry_count=3,
                    next_retry_at="2026-08-15T21:37:00.000000Z",
                    worker_id_present=False,
                    worker_id_digest=None,
                    claimed_at=None,
                    result_metadata_digest="4" * 64,
                    error_category="provider_transport",
                    error_digest="3" * 64,
                )
            else:
                item.update(
                    status="failed",
                    updated_at="2026-08-20T14:30:00.000000Z",
                    completed_at="2026-08-20T14:30:00.000000Z",
                    retry_count=3,
                    worker_id_present=True,
                    worker_id_digest=epoch_three_worker_digest,
                    claimed_at="2026-08-20T14:25:00.000000Z",
                    error_category="provider_transport",
                    error_digest="1" * 64,
                )
        epoch_three_interrupted = dict(
            create_live_snapshot(
                self.cohort(),
                epoch_three_rows,
                generation_before="systalyze:public:81703",
                generation_after="systalyze:public:81703",
                installation_authority=rebound_installation_authority(),
                observed_at=1_786_829_900,
            )
        )
        epoch_three_backup = rollback_backup_evidence()
        epoch_three_backup["source_authority"][
            "data_identity_digest"
        ] = epoch_three_interrupted["installation_authority"][
            "observed_data_identity_digest"
        ]
        for key in ("generation_before", "generation_after"):
            epoch_three_backup["source_authority"][key] = (
                epoch_three_interrupted[key]
            )
        epoch_three_backup["source_authority_digest"] = digest(
            epoch_three_backup["source_authority"]
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "schema does not match recovery epoch",
        ):
            create_post_abort_recovery_plan(
                epoch_two_plan,
                epoch_three_interrupted,
                candidate_release=release_identity(),
                rollback_backup=epoch_three_backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/v11-epoch3-backup.age",
                rollback_bundle_path="/private/tmp/v11-epoch3-bundle.age",
                authorization_receipt_path="/private/tmp/v11-epoch3-auth.json",
                application_receipt_path="/private/tmp/v11-epoch3-app.json",
                verification_receipt_path="/private/tmp/v11-epoch3-verify.json",
                rollback_receipt_path="/private/tmp/v11-epoch3-rollback.json",
                reference_application_authorization=(
                    exact_drain_authorization(epoch_two_plan)
                ),
                reference_application_journal=(
                    exact_drain_application_journal(epoch_two_plan)
                ),
                reference_application_progress_digest="2" * 64,
                prior_retry_recovery=epoch_two_retry,
                schema_version=11,
                created_at=1_786_830_000,
            )

        def create_epoch_three_recovery(value):
            return create_post_abort_recovery_plan(
                epoch_two_plan,
                value,
                candidate_release=release_identity(),
                rollback_backup=epoch_three_backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/v12-epoch3-backup.age",
                rollback_bundle_path="/private/tmp/v12-epoch3-bundle.age",
                authorization_receipt_path="/private/tmp/v12-epoch3-auth.json",
                application_receipt_path="/private/tmp/v12-epoch3-app.json",
                verification_receipt_path=(
                    "/private/tmp/v12-epoch3-verify.json"
                ),
                rollback_receipt_path=(
                    "/private/tmp/v12-epoch3-rollback.json"
                ),
                reference_application_authorization=(
                    exact_drain_authorization(epoch_two_plan)
                ),
                reference_application_journal=(
                    exact_drain_application_journal(epoch_two_plan)
                ),
                reference_application_progress_digest="2" * 64,
                prior_retry_recovery=epoch_two_retry,
                schema_version=12,
                created_at=1_786_830_000,
            )

        epoch_three_recovery = create_epoch_three_recovery(
            epoch_three_interrupted
        )
        epoch_three_retry = epoch_three_recovery["retry_recovery"]
        self.assertEqual(epoch_three_recovery["schema_version"], 12)
        self.assertIn(
            owned_pending_id,
            {
                item["operation_id"]
                for item in epoch_three_recovery["selected_operations"]
            },
        )
        self.assertNotIn(
            released_pending_id,
            {
                item["operation_id"]
                for item in epoch_three_recovery["selected_operations"]
            },
        )
        self.assertEqual(
            epoch_three_recovery["selected_status_counts"],
            {"failed": len(epoch_two_selected_ids) - 2, "pending": 1},
        )
        self.assertEqual(
            epoch_three_recovery["preserved_status_counts"],
            {"completed": 9, "pending": 1},
        )
        self.assertEqual(epoch_three_retry["schema_version"], 3)
        self.assertEqual(epoch_three_retry["recovery_epoch_before"], 2)
        self.assertEqual(epoch_three_retry["recovery_epoch_after"], 3)
        self.assertEqual(epoch_three_retry["recovery_epoch_ceiling"], 3)
        self.assertEqual(epoch_three_retry["maximum_cumulative_attempts"], 16)
        owned_pending_retry = next(
            item
            for item in epoch_three_retry["operations"]
            if item["operation_id"] == owned_pending_id
        )
        self.assertFalse(owned_pending_retry["reset_applied"])
        self.assertEqual(owned_pending_retry["retry_count_before"], 1)
        self.assertEqual(owned_pending_retry["retry_count_after"], 1)
        self.assertEqual(
            verify_post_abort_recovery_plan(
                epoch_three_recovery,
                now=epoch_three_recovery["created_at"],
            ),
            epoch_three_recovery,
        )

        reference_released_row = next(
            item
            for item in epoch_two_plan["live_snapshot"]["operations"]
            if item["operation_id"] == released_pending_id
        )
        for label, updates in (
            (
                "unchanged-result-checkpoint",
                {
                    "result_metadata_digest": reference_released_row[
                        "result_metadata_digest"
                    ]
                },
            ),
            (
                "unchanged-retry-count",
                {"retry_count": reference_released_row["retry_count"]},
            ),
            (
                "unchanged-updated-at",
                {"updated_at": reference_released_row["updated_at"]},
            ),
            ("missing-due-time", {"next_retry_at": None}),
            (
                "future-due-time",
                {"next_retry_at": "2026-08-21T00:00:00.000000Z"},
            ),
            (
                "missing-error-evidence",
                {"error_category": "none", "error_digest": None},
            ),
        ):
            with self.subTest(invalid_released_checkpoint=label):
                invalid = deepcopy(epoch_three_interrupted)
                invalid_row = next(
                    item
                    for item in invalid["operations"]
                    if item["operation_id"] == released_pending_id
                )
                invalid_row.update(updates)
                invalid_row["row_digest"] = digest(
                    {
                        key: value
                        for key, value in invalid_row.items()
                        if key != "row_digest"
                    }
                )
                invalid["snapshot_digest"] = digest(
                    {
                        key: value
                        for key, value in invalid.items()
                        if key != "snapshot_digest"
                    }
                )
                with self.assertRaisesRegex(
                    OperationRecoveryError,
                    "row set is invalid",
                ):
                    create_epoch_three_recovery(invalid)

        replay = deepcopy(epoch_three_recovery)
        replay["retry_recovery"]["prior_retry_recovery_digest"] = "0" * 64
        replay["retry_recovery_digest"] = digest(replay["retry_recovery"])
        replay["plan_digest"] = digest(
            {
                key: value
                for key, value in replay.items()
                if key != "plan_digest"
            }
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "retry recovery is invalid",
        ):
            verify_post_abort_recovery_plan(
                replay,
                now=replay["created_at"],
            )

        epoch_three_selected_ids = {
            item["operation_id"]
            for item in epoch_three_recovery["selected_operations"]
        }
        epoch_three_recovered_rows = []
        for item in epoch_three_interrupted["operations"]:
            row = {
                "operation_id": item["operation_id"],
                "bank_id": "engineering",
                "operation_type": item["operation_type"],
                "status": item["current_status"],
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
                "completed_at": item["completed_at"],
                "retry_count": item["retry_count"],
                "next_retry_at": item["next_retry_at"],
                "worker_id_present": item["worker_id_present"],
                "worker_id_digest": item["worker_id_digest"],
                "claimed_at": item["claimed_at"],
                "task_payload_present": item["task_payload_present"],
                "task_payload_digest": item["task_payload_digest"],
                "result_metadata_digest": item["result_metadata_digest"],
                "error_category": item["error_category"],
                "error_digest": item["error_digest"],
            }
            if item["operation_id"] in epoch_three_selected_ids:
                if item["operation_id"] == owned_pending_id:
                    row.update(
                        status="pending",
                        updated_at="2026-08-20T14:40:00.000000Z",
                        worker_id_present=False,
                        worker_id_digest=None,
                        claimed_at=None,
                    )
                else:
                    row.update(
                        status="pending",
                        updated_at="2026-08-20T14:40:00.000000Z",
                        completed_at=None,
                        retry_count=0,
                        next_retry_at=None,
                        worker_id_present=False,
                        worker_id_digest=None,
                        claimed_at=None,
                        error_category="none",
                        error_digest=None,
                    )
            epoch_three_recovered_rows.append(row)
        epoch_three_recovered_snapshot = dict(
            create_live_snapshot(
                self.cohort(),
                epoch_three_recovered_rows,
                generation_before="systalyze:public:81704",
                generation_after="systalyze:public:81704",
                installation_authority=rebound_installation_authority(),
                observed_at=1_786_830_100,
            )
        )
        epoch_three_context = {
            "schema_version": 3,
            "kind": "operation-recovery-exact-drain-recovery-context",
            "origin": "post-abort",
            "generation": epoch_three_recovered_snapshot["generation_before"],
            "recovery_epoch": 3,
            "candidate_release_digest": release_identity()["release_digest"],
            "selected_operation_ids_digest": digest(
                sorted(epoch_two_selected_ids)
            ),
            "initial_origin_digest": None,
            "post_abort_selected_operation_ids_digest": digest(
                sorted(epoch_three_selected_ids)
            ),
            "post_abort_plan_digest": epoch_three_recovery["plan_digest"],
            "post_abort_application_receipt_digest": "3" * 64,
            "post_abort_verification_receipt_digest": "4" * 64,
            "retry_recovery_digest": epoch_three_recovery[
                "retry_recovery_digest"
            ],
            "selected_checkpoint_set_digest": epoch_three_recovery[
                "selected_checkpoint_set_digest"
            ],
            "preserved_row_set_digest": epoch_three_recovery[
                "preserved_row_set_digest"
            ],
        }
        epoch_three_drain_backup = drain_backup_evidence()
        epoch_three_drain_backup["source_authority"][
            "data_identity_digest"
        ] = epoch_three_recovered_snapshot["installation_authority"][
            "observed_data_identity_digest"
        ]
        for key in ("generation_before", "generation_after"):
            epoch_three_drain_backup["source_authority"][key] = (
                epoch_three_recovered_snapshot[key]
            )
        epoch_three_drain_backup["source_authority_digest"] = digest(
            epoch_three_drain_backup["source_authority"]
        )
        epoch_three_plan = recovery_contract.create_exact_drain_plan(
            self.cohort(),
            epoch_three_recovered_snapshot,
            candidate_release=release_identity(),
            rollback_backup=epoch_three_drain_backup,
            rollback_backup_path="/private/tmp/v12-epoch3-drain-backup.age",
            provider_policy_digest="9" * 64,
            effective_profile_digest="7" * 64,
            worker_runtime_digest="8" * 64,
            authorization_receipt_path="/private/tmp/v12-epoch3-drain-auth.json",
            application_receipt_path="/private/tmp/v12-epoch3-drain-app.json",
            status_artifact_path="/private/tmp/v12-epoch3-drain-status.json",
            verification_receipt_path="/private/tmp/v12-epoch3-drain-verify.json",
            recovery_context=epoch_three_context,
            created_at=1_786_830_101,
            schema_version=12,
        )
        epoch_four_worker_digest = hashlib.sha256(
            (
                "operation-recovery-exact-drain-"
                f"{epoch_three_plan['plan_digest'][:12]}"
            ).encode()
        ).hexdigest()
        terminal_selected_ids = {
            item["operation_id"]
            for item in epoch_three_plan["selected_operations"]
        }
        epoch_four_rows = deepcopy(epoch_three_recovered_rows)
        for item in epoch_four_rows:
            if item["operation_id"] not in terminal_selected_ids:
                continue
            item.update(
                status="failed",
                updated_at="2026-08-20T14:50:00.000000Z",
                completed_at="2026-08-20T14:50:00.000000Z",
                retry_count=3,
                worker_id_present=True,
                worker_id_digest=epoch_four_worker_digest,
                claimed_at="2026-08-20T14:45:00.000000Z",
                error_category="provider_transport",
                error_digest="5" * 64,
            )
        epoch_four_interrupted = dict(
            create_live_snapshot(
                self.cohort(),
                epoch_four_rows,
                generation_before="systalyze:public:81705",
                generation_after="systalyze:public:81705",
                installation_authority=rebound_installation_authority(),
                observed_at=1_786_830_200,
            )
        )
        epoch_four_backup = rollback_backup_evidence()
        epoch_four_backup["source_authority"]["data_identity_digest"] = (
            epoch_four_interrupted["installation_authority"][
                "observed_data_identity_digest"
            ]
        )
        for key in ("generation_before", "generation_after"):
            epoch_four_backup["source_authority"][key] = (
                epoch_four_interrupted[key]
            )
        epoch_four_backup["source_authority_digest"] = digest(
            epoch_four_backup["source_authority"]
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "schema does not match recovery epoch",
        ):
            create_post_abort_recovery_plan(
                epoch_three_plan,
                epoch_four_interrupted,
                candidate_release=release_identity(),
                rollback_backup=epoch_four_backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/v12-epoch4-backup.age",
                rollback_bundle_path="/private/tmp/v12-epoch4-bundle.age",
                authorization_receipt_path="/private/tmp/v12-epoch4-auth.json",
                application_receipt_path="/private/tmp/v12-epoch4-app.json",
                verification_receipt_path="/private/tmp/v12-epoch4-verify.json",
                rollback_receipt_path="/private/tmp/v12-epoch4-rollback.json",
                reference_application_authorization=(
                    exact_drain_authorization(epoch_three_plan)
                ),
                reference_application_journal=(
                    exact_drain_application_journal(epoch_three_plan)
                ),
                reference_application_progress_digest="6" * 64,
                prior_retry_recovery=epoch_three_retry,
                schema_version=12,
                created_at=1_786_830_300,
            )

        terminal_status_body = {
            "schema_version": 2,
            "kind": "operation-recovery-exact-drain-status",
            "plan_digest": epoch_three_plan["plan_digest"],
            "generation_before": epoch_four_interrupted[
                "generation_before"
            ],
            "generation_after": epoch_four_interrupted["generation_after"],
            "selected_operation_count": len(terminal_selected_ids),
            "selected_status_counts": {
                "failed": len(terminal_selected_ids)
            },
            "preserved_status_counts": epoch_three_plan[
                "preserved_status_counts"
            ],
            "outside_nonterminal_counts": [],
            "failure_classifications": [
                {
                    "cause_family": "provider_transport",
                    "error_digest": "5" * 64,
                    "occurrence_count": len(terminal_selected_ids),
                }
            ],
            "observed_at": epoch_four_interrupted["observed_at"],
        }
        terminal_status = {
            **terminal_status_body,
            "status_digest": digest(terminal_status_body),
        }
        terminal_journal = exact_drain_application_journal(epoch_three_plan)
        worker_exit_body = {
            "schema_version": 1,
            "kind": "operation-recovery-exact-drain-worker-exit-evidence",
            "worker_pid": terminal_journal["worker_pid"],
            "worker_start_time": terminal_journal["worker_start_time"],
            "observed_at": epoch_four_interrupted["observed_at"],
            "state": "inactive",
        }
        worker_exit = {
            **worker_exit_body,
            "evidence_digest": digest(worker_exit_body),
        }
        def create_reconciliation(
            snapshot=epoch_four_interrupted,
            *,
            status=terminal_status,
            exit_evidence=worker_exit,
        ):
            return create_post_abort_recovery_plan(
                epoch_three_plan,
                snapshot,
                candidate_release=release_identity(),
                rollback_backup=epoch_four_backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/v13-reconcile-backup.age",
                rollback_bundle_path="/private/tmp/v13-reconcile-bundle.age",
                authorization_receipt_path="/private/tmp/v13-reconcile-auth.json",
                application_receipt_path="/private/tmp/v13-reconcile-app.json",
                verification_receipt_path=(
                    "/private/tmp/v13-reconcile-verify.json"
                ),
                rollback_receipt_path=(
                    "/private/tmp/v13-reconcile-rollback.json"
                ),
                reference_application_authorization=(
                    exact_drain_authorization(epoch_three_plan)
                ),
                reference_application_journal=terminal_journal,
                reference_application_progress_digest="6" * 64,
                reference_application_receipt_digest="7" * 64,
                reference_terminal_status=status,
                reference_worker_exit=exit_evidence,
                prior_retry_recovery=epoch_three_retry,
                schema_version=13,
                created_at=1_786_830_300,
            )

        reconciliation = create_reconciliation()
        self.assertEqual(reconciliation["schema_version"], 13)
        self.assertEqual(
            reconciliation["kind"],
            "operation-recovery-exact-drain-post-terminal-reconciliation-plan",
        )
        self.assertEqual(
            reconciliation["action"],
            "reconcile-exact-drain-post-terminal",
        )
        self.assertEqual(
            reconciliation["selected_status_counts"],
            {"failed": len(terminal_selected_ids)},
        )
        retry = reconciliation["retry_recovery"]
        self.assertEqual(retry["schema_version"], 4)
        self.assertEqual(retry["recovery_epoch_before"], 3)
        self.assertEqual(retry["recovery_epoch_after"], 3)
        self.assertEqual(retry["recovery_epoch_ceiling"], 3)
        self.assertEqual(retry["reconciliation_cycle_before"], 0)
        self.assertEqual(retry["reconciliation_cycle_after"], 1)
        self.assertEqual(retry["reconciliation_cycle_ceiling"], 1)
        self.assertEqual(retry["maximum_cumulative_attempts"], 20)
        self.assertEqual(
            verify_post_abort_recovery_plan(
                reconciliation,
                now=reconciliation["created_at"],
            ),
            reconciliation,
        )

        nonterminal = deepcopy(epoch_four_interrupted)
        nonterminal_row = next(
            item
            for item in nonterminal["operations"]
            if item["operation_id"] in terminal_selected_ids
        )
        nonterminal_row.update(
            current_status="pending",
            completed_at=None,
            worker_id_present=False,
            worker_id_digest=None,
            claimed_at=None,
        )
        nonterminal_row["row_digest"] = digest(
            {
                key: value
                for key, value in nonterminal_row.items()
                if key != "row_digest"
            }
        )
        nonterminal["status_counts"] = {
            **nonterminal["status_counts"],
            "failed": len(terminal_selected_ids) - 1,
            "pending": 1,
        }
        nonterminal["snapshot_digest"] = digest(
            {
                key: value
                for key, value in nonterminal.items()
                if key != "snapshot_digest"
            }
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "row set is invalid",
        ):
            create_reconciliation(nonterminal)

        active_worker_body = {
            **worker_exit_body,
            "state": "running",
        }
        active_worker = {
            **active_worker_body,
            "evidence_digest": digest(active_worker_body),
        }
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "worker exit is invalid",
        ):
            create_reconciliation(exit_evidence=active_worker)

        reconciled_rows = []
        for item in epoch_four_interrupted["operations"]:
            row = {
                "operation_id": item["operation_id"],
                "bank_id": "engineering",
                "operation_type": item["operation_type"],
                "status": item["current_status"],
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
                "completed_at": item["completed_at"],
                "retry_count": item["retry_count"],
                "next_retry_at": item["next_retry_at"],
                "worker_id_present": item["worker_id_present"],
                "worker_id_digest": item["worker_id_digest"],
                "claimed_at": item["claimed_at"],
                "task_payload_present": item["task_payload_present"],
                "task_payload_digest": item["task_payload_digest"],
                "result_metadata_digest": item["result_metadata_digest"],
                "error_category": item["error_category"],
                "error_digest": item["error_digest"],
            }
            if item["operation_id"] in terminal_selected_ids:
                row.update(
                    status="pending",
                    updated_at="2026-08-20T15:00:00.000000Z",
                    completed_at=None,
                    retry_count=0,
                    next_retry_at=None,
                    worker_id_present=False,
                    worker_id_digest=None,
                    claimed_at=None,
                    error_category="none",
                    error_digest=None,
                )
            reconciled_rows.append(row)
        reconciled_snapshot = dict(
            create_live_snapshot(
                self.cohort(),
                reconciled_rows,
                generation_before="systalyze:public:81706",
                generation_after="systalyze:public:81706",
                installation_authority=rebound_installation_authority(),
                observed_at=1_786_830_400,
            )
        )
        reconciliation_context = {
            "schema_version": 4,
            "kind": "operation-recovery-exact-drain-recovery-context",
            "origin": "post-terminal-reconciliation",
            "generation": reconciled_snapshot["generation_before"],
            "recovery_epoch": 3,
            "reconciliation_cycle": 1,
            "candidate_release_digest": release_identity()["release_digest"],
            "selected_operation_ids_digest": digest(
                sorted(terminal_selected_ids)
            ),
            "initial_origin_digest": None,
            "post_terminal_reconciliation_plan_digest": reconciliation[
                "plan_digest"
            ],
            "post_terminal_reconciliation_application_receipt_digest": (
                "8" * 64
            ),
            "post_terminal_reconciliation_verification_receipt_digest": (
                "9" * 64
            ),
            "terminal_plan_digest": epoch_three_plan["plan_digest"],
            "terminal_authorization_receipt_digest": (
                exact_drain_authorization(epoch_three_plan)["receipt_digest"]
            ),
            "terminal_application_receipt_digest": "7" * 64,
            "terminal_progress_digest": "6" * 64,
            "terminal_status_digest": terminal_status["status_digest"],
            "retry_recovery_digest": reconciliation[
                "retry_recovery_digest"
            ],
            "selected_checkpoint_set_digest": reconciliation[
                "selected_checkpoint_set_digest"
            ],
            "preserved_row_set_digest": reconciliation[
                "preserved_row_set_digest"
            ],
        }
        reconciled_backup = drain_backup_evidence()
        reconciled_backup["source_authority"]["data_identity_digest"] = (
            reconciled_snapshot["installation_authority"][
                "observed_data_identity_digest"
            ]
        )
        for key in ("generation_before", "generation_after"):
            reconciled_backup["source_authority"][key] = (
                reconciled_snapshot[key]
            )
        reconciled_backup["source_authority_digest"] = digest(
            reconciled_backup["source_authority"]
        )
        capability = recovery_contract.create_hatchery_capability_receipt(
            provider_policy_digest="9" * 64,
            provider_identity_digest="a" * 64,
            model_digest="b" * 64,
            observed_at=1_786_830_400,
            successful=True,
        )
        schema_thirteen_plan = recovery_contract.create_exact_drain_plan(
            self.cohort(),
            reconciled_snapshot,
            candidate_release=release_identity(),
            rollback_backup=reconciled_backup,
            rollback_backup_path="/private/tmp/v13-drain-backup.age",
            provider_policy_digest="9" * 64,
            effective_profile_digest="7" * 64,
            worker_runtime_digest="8" * 64,
            authorization_receipt_path="/private/tmp/v13-drain-auth.json",
            application_receipt_path="/private/tmp/v13-drain-app.json",
            status_artifact_path="/private/tmp/v13-drain-status.json",
            verification_receipt_path="/private/tmp/v13-drain-verify.json",
            recovery_context=reconciliation_context,
            hatchery_capability_receipt=capability,
            created_at=1_786_830_401,
            schema_version=13,
        )
        self.assertEqual(
            schema_thirteen_plan["recovery_context"],
            reconciliation_context,
        )
        self.assertEqual(
            schema_thirteen_plan["phase_repair_contract_digest"],
            recovery_contract.EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V9_DIGEST,
        )
        self.assertEqual(
            recovery_contract.verify_exact_drain_plan(
                schema_thirteen_plan,
                now=schema_thirteen_plan["created_at"],
            ),
            schema_thirteen_plan,
        )

        unsuccessful = deepcopy(schema_thirteen_plan)
        unsuccessful_capability = (
            recovery_contract.create_hatchery_capability_receipt(
                provider_policy_digest="9" * 64,
                provider_identity_digest="a" * 64,
                model_digest="b" * 64,
                observed_at=1_786_830_400,
                successful=False,
            )
        )
        unsuccessful["hatchery_capability_receipt"] = (
            unsuccessful_capability
        )
        unsuccessful["hatchery_capability_receipt_digest"] = (
            unsuccessful_capability["receipt_digest"]
        )
        unsuccessful["plan_digest"] = digest(
            {
                key: value
                for key, value in unsuccessful.items()
                if key != "plan_digest"
            }
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "exact drain plan is invalid",
        ):
            recovery_contract.verify_exact_drain_plan(
                unsuccessful,
                now=unsuccessful["created_at"],
            )

        stale_authorization = exact_drain_authorization(
            schema_thirteen_plan
        )
        stale_authorization["authorized_at"] = (
            capability["observed_at"]
            + recovery_contract.EXACT_DRAIN_EVIDENCE_MAX_AGE_SECONDS
            + 1
        )
        stale_authorization["receipt_digest"] = digest(
            {
                key: value
                for key, value in stale_authorization.items()
                if key != "receipt_digest"
            }
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "authorization receipt is invalid",
        ):
            verify_exact_drain_authorization_receipt(
                stale_authorization,
                plan=schema_thirteen_plan,
            )

        with self.assertRaisesRegex(
            OperationRecoveryError,
            "schema does not match recovery epoch",
        ):
            create_post_abort_recovery_plan(
                schema_thirteen_plan,
                epoch_four_interrupted,
                candidate_release=release_identity(),
                rollback_backup=epoch_four_backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/v13-cycle2-backup.age",
                rollback_bundle_path="/private/tmp/v13-cycle2-bundle.age",
                authorization_receipt_path="/private/tmp/v13-cycle2-auth.json",
                application_receipt_path="/private/tmp/v13-cycle2-app.json",
                verification_receipt_path="/private/tmp/v13-cycle2-verify.json",
                rollback_receipt_path="/private/tmp/v13-cycle2-rollback.json",
                reference_application_authorization=(
                    exact_drain_authorization(schema_thirteen_plan)
                ),
                reference_application_journal=(
                    exact_drain_application_journal(schema_thirteen_plan)
                ),
                reference_application_progress_digest="c" * 64,
                prior_retry_recovery=reconciliation["retry_recovery"],
                reference_application_receipt_digest="d" * 64,
                reference_terminal_status=terminal_status,
                reference_worker_exit=worker_exit,
                schema_version=13,
                created_at=1_786_830_500,
            )

        for legacy_schema_version in range(4, 10):
            with (
                self.subTest(
                    creation_schema_version=legacy_schema_version,
                ),
                self.assertRaisesRegex(
                    OperationRecoveryError,
                    "legacy post-abort schema cannot reference schema 10",
                ),
            ):
                create_post_abort_recovery_plan(
                    plan,
                    second_interrupted,
                    candidate_release=release_identity(),
                    rollback_backup=second_backup,
                    rollback_encryption=rollback_encryption(),
                    rollback_backup_path="/private/tmp/v9-replay-backup.age",
                    rollback_bundle_path="/private/tmp/v9-replay-bundle.age",
                    authorization_receipt_path="/private/tmp/v9-replay-auth.json",
                    application_receipt_path="/private/tmp/v9-replay-app.json",
                    verification_receipt_path="/private/tmp/v9-replay-verify.json",
                    rollback_receipt_path="/private/tmp/v9-replay-rollback.json",
                    reference_application_authorization=(
                        exact_drain_authorization(plan)
                    ),
                    reference_application_journal=(
                        exact_drain_application_journal(plan)
                    ),
                    reference_application_progress_digest="d" * 64,
                    schema_version=legacy_schema_version,
                    created_at=1_786_829_600,
                )

        schema_eleven_plan = recovery_contract.create_exact_drain_plan(
            self.cohort(),
            recovered_snapshot,
            candidate_release=release_identity(),
            rollback_backup=drain_backup,
            rollback_backup_path="/private/tmp/v11-resume-backup.age",
            provider_policy_digest="9" * 64,
            effective_profile_digest="7" * 64,
            worker_runtime_digest="8" * 64,
            authorization_receipt_path="/private/tmp/v11-resume-auth.json",
            application_receipt_path="/private/tmp/v11-resume-app.json",
            status_artifact_path="/private/tmp/v11-resume-status.json",
            verification_receipt_path="/private/tmp/v11-resume-verify.json",
            recovery_context=recovery_context,
            created_at=1_786_390_701,
            schema_version=11,
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "retry recovery is invalid",
        ):
            create_post_abort_recovery_plan(
                schema_eleven_plan,
                second_interrupted,
                candidate_release=release_identity(),
                rollback_backup=second_backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/v10-v11-replay-backup.age",
                rollback_bundle_path="/private/tmp/v10-v11-replay-bundle.age",
                authorization_receipt_path="/private/tmp/v10-v11-replay-auth.json",
                application_receipt_path="/private/tmp/v10-v11-replay-app.json",
                verification_receipt_path="/private/tmp/v10-v11-replay-verify.json",
                rollback_receipt_path="/private/tmp/v10-v11-replay-rollback.json",
                reference_application_authorization=(
                    exact_drain_authorization(schema_eleven_plan)
                ),
                reference_application_journal=(
                    exact_drain_application_journal(schema_eleven_plan)
                ),
                reference_application_progress_digest="d" * 64,
                schema_version=10,
                created_at=1_786_829_600,
            )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "legacy post-abort schema cannot reference schema 10",
        ):
            create_post_abort_recovery_plan(
                schema_eleven_plan,
                second_interrupted,
                candidate_release=release_identity(),
                rollback_backup=second_backup,
                rollback_encryption=rollback_encryption(),
                rollback_backup_path="/private/tmp/v11-replay-backup.age",
                rollback_bundle_path="/private/tmp/v11-replay-bundle.age",
                authorization_receipt_path="/private/tmp/v11-replay-auth.json",
                application_receipt_path="/private/tmp/v11-replay-app.json",
                verification_receipt_path="/private/tmp/v11-replay-verify.json",
                rollback_receipt_path="/private/tmp/v11-replay-rollback.json",
                reference_application_authorization=(
                    exact_drain_authorization(schema_eleven_plan)
                ),
                reference_application_journal=(
                    exact_drain_application_journal(schema_eleven_plan)
                ),
                reference_application_progress_digest="d" * 64,
                schema_version=9,
                created_at=1_786_829_600,
            )

        legacy_key_sets = {
            version: getattr(
                recovery_contract,
                f"POST_ABORT_PLAN_V{version}_KEYS",
            )
            for version in range(1, 10)
        }
        for legacy_schema_version, key_set in legacy_key_sets.items():
            downgraded_recovery = {
                key: deepcopy(value)
                for key, value in recovery_plan.items()
                if key in key_set
            }
            downgraded_recovery.update(
                schema_version=legacy_schema_version,
                reference_plan=plan,
                reference_plan_digest=plan["plan_digest"],
            )
            downgraded_recovery["plan_digest"] = digest(
                {
                    key: value
                    for key, value in downgraded_recovery.items()
                    if key != "plan_digest"
                }
            )
            with (
                self.subTest(
                    verification_schema_version=legacy_schema_version,
                ),
                self.assertRaisesRegex(
                    OperationRecoveryError,
                    "legacy post-abort schema cannot reference schema 10",
                ),
            ):
                verify_post_abort_recovery_plan(
                    downgraded_recovery,
                    now=downgraded_recovery["created_at"],
                )

        with self.assertRaisesRegex(
            OperationRecoveryError,
            "recovery context is required",
        ):
            recovery_contract.create_exact_drain_plan(
                self.cohort(),
                recovered_snapshot,
                candidate_release=release_identity(),
                rollback_backup=drain_backup,
                rollback_backup_path="/private/tmp/v10-omitted-backup.age",
                provider_policy_digest="9" * 64,
                effective_profile_digest="7" * 64,
                worker_runtime_digest="8" * 64,
                authorization_receipt_path="/private/tmp/v10-omitted-auth.json",
                application_receipt_path="/private/tmp/v10-omitted-app.json",
                status_artifact_path="/private/tmp/v10-omitted-status.json",
                verification_receipt_path="/private/tmp/v10-omitted-verify.json",
                created_at=1_786_390_701,
            )

        replayed_epoch = deepcopy(plan)
        replayed_epoch["recovery_context"]["recovery_epoch"] = 0
        replayed_epoch["recovery_context_digest"] = digest(
            replayed_epoch["recovery_context"]
        )
        replayed_epoch["plan_digest"] = digest(
            {
                key: value
                for key, value in replayed_epoch.items()
                if key != "plan_digest"
            }
        )
        with self.assertRaisesRegex(OperationRecoveryError, "context is invalid"):
            recovery_contract.verify_exact_drain_plan(
                replayed_epoch,
                now=replayed_epoch["created_at"],
            )

    def test_post_abort_v10_consumes_epoch_for_processing_only_cleanup(self):
        reference = self.drain_plan()
        snapshot = self.post_abort_snapshot(
            reference,
            current_interrupted_subset=True,
            interrupted_processing_count=1,
            observed_at=1_786_391_000,
        )
        backup = rollback_backup_evidence()
        for key in ("generation_before", "generation_after"):
            backup["source_authority"][key] = snapshot[key]
        backup["source_authority_digest"] = digest(
            backup["source_authority"]
        )

        plan = create_post_abort_recovery_plan(
            reference,
            snapshot,
            candidate_release=release_identity(),
            rollback_backup=backup,
            rollback_encryption=rollback_encryption(),
            rollback_backup_path="/private/tmp/v10-cleanup-backup.age",
            rollback_bundle_path="/private/tmp/v10-cleanup-bundle.age",
            authorization_receipt_path="/private/tmp/v10-cleanup-auth.json",
            application_receipt_path="/private/tmp/v10-cleanup-app.json",
            verification_receipt_path="/private/tmp/v10-cleanup-verify.json",
            rollback_receipt_path="/private/tmp/v10-cleanup-rollback.json",
            reference_application_authorization=(
                exact_drain_authorization(reference)
            ),
            reference_application_journal=(
                exact_drain_application_journal(reference)
            ),
            reference_application_progress_digest="e" * 64,
            created_at=1_786_391_100,
        )

        self.assertEqual(plan["selected_status_counts"], {"processing": 1})
        self.assertEqual(plan["retry_recovery"]["failed_reset_count"], 0)
        self.assertEqual(plan["retry_recovery"]["recovery_epoch_before"], 0)
        self.assertEqual(plan["retry_recovery"]["recovery_epoch_after"], 1)
        self.assertEqual(
            verify_post_abort_recovery_plan(plan, now=plan["created_at"]),
            plan,
        )

    def test_post_abort_plan_rejects_a_different_processing_owner(self):
        reference = self.legacy_drain_plan()
        snapshot = self.post_abort_snapshot(
            reference,
            interrupted_operation_types=("retain", "consolidation"),
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
                schema_version=4,
                created_at=1_786_390_500,
            )

    def test_post_abort_v4_planner_rejects_non_exact_current_shapes(self):
        reference = self.legacy_drain_plan()
        base = self.post_abort_snapshot(
            reference,
            interrupted_operation_types=("retain", "consolidation"),
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

        two_retains = deepcopy(base)
        consolidation_row = next(
            item
            for item in two_retains["operations"]
            if item["current_status"] == "processing"
            and item["operation_type"] == "consolidation"
        )
        replacement_retain = next(
            item
            for item in two_retains["operations"]
            if item["current_status"] == "pending"
            and item["operation_type"] == "retain"
        )
        consolidation_row.update(
            {
                "current_status": "pending",
                "worker_id_present": False,
                "worker_id_digest": None,
                "claimed_at": None,
            }
        )
        replacement_retain.update(
            {
                "current_status": "processing",
                "worker_id_present": True,
                "worker_id_digest": worker_digest,
                "claimed_at": "2026-08-10T10:05:30.000000Z",
            }
        )

        retain_and_refresh = deepcopy(base)
        replaced_consolidation = next(
            item
            for item in retain_and_refresh["operations"]
            if item["current_status"] == "processing"
            and item["operation_type"] == "consolidation"
        )
        replacement_refresh = next(
            item
            for item in retain_and_refresh["operations"]
            if item["current_status"] == "pending"
            and item["operation_type"] == "refresh_mental_model"
        )
        replaced_consolidation.update(
            {
                "current_status": "pending",
                "worker_id_present": False,
                "worker_id_digest": None,
                "claimed_at": None,
            }
        )
        replacement_refresh.update(
            {
                "current_status": "processing",
                "worker_id_present": True,
                "worker_id_digest": worker_digest,
                "claimed_at": "2026-08-10T10:05:45.000000Z",
            }
        )

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
            "two-retains": reseal(
                two_retains,
                consolidation_row,
                replacement_retain,
            ),
            "retain-and-refresh": reseal(
                retain_and_refresh,
                replaced_consolidation,
                replacement_refresh,
            ),
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
                    schema_version=4,
                    created_at=1_786_390_500,
                )

    def test_post_abort_v4_verifier_rejects_schema_and_bound_set_tampering(self):
        reference = self.legacy_drain_plan()
        snapshot = self.post_abort_snapshot(
            reference,
            interrupted_operation_types=("retain", "consolidation"),
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
                schema_version=4,
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

    def test_post_abort_v4_requires_exact_type_specific_retry_vector(self):
        reference = self.legacy_drain_plan()
        snapshot = self.post_abort_snapshot(
            reference,
            interrupted_operation_types=("retain", "consolidation"),
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

        def create(candidate_snapshot):
            return dict(
                create_post_abort_recovery_plan(
                    reference,
                    candidate_snapshot,
                    candidate_release=release_identity(),
                    rollback_backup=backup,
                    rollback_encryption=rollback_encryption(),
                    rollback_backup_path="/private/tmp/retry-backup.age",
                    rollback_bundle_path="/private/tmp/retry-bundle.age",
                    authorization_receipt_path="/private/tmp/retry-auth.json",
                    application_receipt_path="/private/tmp/retry-app.json",
                    verification_receipt_path="/private/tmp/retry-verify.json",
                    rollback_receipt_path="/private/tmp/retry-rollback.json",
                    reference_application_authorization=(
                        exact_drain_authorization(reference)
                    ),
                    reference_application_journal=(
                        exact_drain_application_journal(reference)
                    ),
                    reference_application_progress_digest="c" * 64,
                    schema_version=4,
                    created_at=1_786_390_500,
                )
            )

        valid_plan = create(snapshot)

        def resealed_snapshot(retry_by_type):
            changed = deepcopy(snapshot)
            for row in changed["operations"]:
                if row["current_status"] != "processing":
                    continue
                row["retry_count"] = retry_by_type[row["operation_type"]]
                row["row_digest"] = digest(
                    {
                        key: value
                        for key, value in row.items()
                        if key != "row_digest"
                    }
                )
            changed["snapshot_digest"] = digest(
                {
                    key: value
                    for key, value in changed.items()
                    if key != "snapshot_digest"
                }
            )
            return changed

        def resealed_plan(changed_snapshot):
            changed = deepcopy(valid_plan)
            changed["live_snapshot"] = changed_snapshot
            changed["snapshot_digest"] = changed_snapshot["snapshot_digest"]
            rows = {
                row["operation_id"]: row
                for row in changed_snapshot["operations"]
            }
            for item in changed["selected_operations"]:
                item["row_digest"] = rows[item["operation_id"]]["row_digest"]
            changed["selected_row_set_digest"] = digest(
                [
                    {
                        "operation_id": item["operation_id"],
                        "row_digest": item["row_digest"],
                        "task_payload_digest": item[
                            "task_payload_digest"
                        ],
                    }
                    for item in changed["selected_operations"]
                ]
            )
            changed["plan_digest"] = digest(
                {
                    key: value
                    for key, value in changed.items()
                    if key != "plan_digest"
                }
            )
            return changed

        cases = {
            "retain-retry-one": {"retain": 1, "consolidation": 3},
            "consolidation-retry-two": {"retain": 0, "consolidation": 2},
            "swapped": {"retain": 3, "consolidation": 0},
        }
        for label, retry_by_type in cases.items():
            changed_snapshot = resealed_snapshot(retry_by_type)
            with (
                self.subTest(contract="create", vector=label),
                self.assertRaisesRegex(
                    OperationRecoveryError,
                    "post-abort row set is invalid",
                ),
            ):
                create(changed_snapshot)
            with (
                self.subTest(contract="verify", vector=label),
                self.assertRaises(OperationRecoveryError),
            ):
                verify_post_abort_recovery_plan(
                    resealed_plan(changed_snapshot),
                    now=valid_plan["created_at"],
                )

    def test_post_abort_v2_verifier_rejects_forged_reference_authority(self):
        reference = self.legacy_drain_plan()
        snapshot = self.post_abort_snapshot(
            reference,
            interrupted_operation_types=("retain", "consolidation"),
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
                schema_version=4,
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

    def test_legacy_queue_guard_artifacts_reject_verified_rebind_authority(self):
        classification = self.queue_blocker_classification()
        tampered_classification = deepcopy(classification)
        tampered_classification["installation_authority"] = (
            rebound_installation_authority()
        )
        tampered_classification["classification_digest"] = digest(
            {
                key: value
                for key, value in tampered_classification.items()
                if key != "classification_digest"
            }
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "verified rebind authority requires schema 11",
        ):
            verify_global_queue_blocker_classification(
                tampered_classification,
                now=tampered_classification["observed_at"],
            )

        predecessor, live, nonclaim_digests = self.claim_release_inputs(
            live_generation="systalyze:public:123"
        )
        reference_plan = self.requeue_plan()
        plan = create_claim_release_plan(
            predecessor,
            live,
            reference_plan=reference_plan,
            permitted_blocker_rows=self.permitted_blocker_rows(
                reference_plan
            ),
            nonclaim_state_digests=nonclaim_digests,
            candidate_release=release_identity(),
            installation_authority=installation_authority(),
            rollback_encryption=rollback_encryption(),
            rollback_bundle_path="/private/tmp/rebind-guard.bundle.json",
            authorization_receipt_path=(
                "/private/tmp/rebind-guard.authorization.json"
            ),
            application_receipt_path=(
                "/private/tmp/rebind-guard.application.json"
            ),
            verification_receipt_path=(
                "/private/tmp/rebind-guard.verification.json"
            ),
            rollback_receipt_path=(
                "/private/tmp/rebind-guard.rollback.json"
            ),
            created_at=live["observed_at"],
        )
        tampered_plan = deepcopy(plan)
        tampered_plan["installation_authority"] = (
            rebound_installation_authority()
        )
        tampered_plan["plan_digest"] = digest(
            {
                key: value
                for key, value in tampered_plan.items()
                if key != "plan_digest"
            }
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "verified rebind authority requires schema 11",
        ):
            verify_claim_release_plan(
                tampered_plan,
                now=tampered_plan["created_at"],
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

    def _checkpoint_continuation_handoff(
        self,
        *,
        snapshot=None,
        attempts_consumed=1,
        attempts_remaining=19,
        unit_count=21,
        created_at=1_785_402_000,
    ):
        snapshot = self.drain_snapshot() if snapshot is None else snapshot
        row = next(
            item
            for item in snapshot["operations"]
            if item["current_status"] == "pending"
            and item["operation_type"] == "retain"
        )
        checkpoint = {
            "facts_committed": True,
            "committed_document_count": 1,
            "unit_ids_count": 21,
            "stage": "storing",
            "processed": 3,
            "total": 10,
        }
        operation = {
            "operation_id": row["operation_id"],
            "operation_type": row["operation_type"],
            "current_status": row["current_status"],
            "row_digest": row["row_digest"],
            "task_payload_digest": row["task_payload_digest"],
            "result_metadata_digest": row["result_metadata_digest"],
            "checkpoint": checkpoint,
            "retry_count": row["retry_count"],
            "attempts_consumed": attempts_consumed,
            "attempts_remaining": attempts_remaining,
            "worker_id_present": row["worker_id_present"],
            "worker_id_digest": row["worker_id_digest"],
            "claimed_at": row["claimed_at"],
            "next_retry_at": row["next_retry_at"],
            "error_category": row["error_category"],
            "error_digest": row["error_digest"],
        }
        audit_body = {
            "schema_version": 1,
            "kind": (
                "operation-recovery-checkpoint-continuation-"
                "side-effect-audit"
            ),
            "operation_id": row["operation_id"],
            "generation": snapshot["generation_before"],
            "row_digest": row["row_digest"],
            "result_metadata_digest": row["result_metadata_digest"],
            "checkpoint": checkpoint,
            "document_count": 1,
            "unit_count": unit_count,
            "document_set_digest": "a" * 64,
            "unit_set_digest": "b" * 64,
            "idempotent_resume": True,
        }
        audit = {
            **audit_body,
            "audit_digest": digest(audit_body),
        }
        target_release = {
            "source_commit": "4" * 40,
            "version": "2026.08.22+4444444",
            "release_digest": "f" * 64,
        }
        return dict(
            create_checkpoint_continuation_handoff(
                snapshot,
                continuation_operations=[operation],
                side_effect_audits=[audit],
                source_plan_digest="1" * 64,
                source_recovery_context_digest="2" * 64,
                source_reconciliation_plan_digest="3" * 64,
                source_terminal_status_digest="4" * 64,
                source_candidate_release=release_identity(),
                candidate_release=target_release,
                generation=snapshot["generation_before"],
                created_at=created_at,
            )
        )

    def test_checkpoint_continuation_handoff_is_payload_free_and_bound(self):
        snapshot = self.drain_snapshot()
        handoff = self._checkpoint_continuation_handoff(snapshot=snapshot)

        self.assertEqual(
            handoff["kind"],
            "operation-recovery-checkpoint-continuation-handoff",
        )
        self.assertEqual(
            handoff["continuation_context"]["origin"],
            "committed-checkpoint",
        )
        self.assertEqual(
            handoff["continuation_context"]["recovery_epoch"],
            3,
        )
        self.assertEqual(
            handoff["continuation_context"]["reconciliation_cycle"],
            1,
        )
        self.assertNotIn("content", handoff)
        self.assertEqual(
            verify_checkpoint_continuation_handoff(
                handoff,
                live_snapshot=snapshot,
                now=1_785_402_000,
            ),
            handoff,
        )

    def test_checkpoint_continuation_accepts_prior_units_for_reused_document(self):
        snapshot = self.drain_snapshot()
        handoff = self._checkpoint_continuation_handoff(
            snapshot=snapshot,
            unit_count=42,
        )

        self.assertEqual(
            verify_checkpoint_continuation_handoff(
                handoff,
                live_snapshot=snapshot,
                now=1_785_402_000,
            ),
            handoff,
        )

        with self.assertRaisesRegex(OperationRecoveryError, "side-effect audit"):
            self._checkpoint_continuation_handoff(
                snapshot=snapshot,
                unit_count=20,
            )

    def test_checkpoint_continuation_rejects_uncommitted_or_tampered_audit(self):
        snapshot = self.drain_snapshot()
        handoff = self._checkpoint_continuation_handoff(snapshot=snapshot)

        uncommitted = deepcopy(handoff)
        uncommitted["operations"][0]["checkpoint"]["facts_committed"] = False
        with self.assertRaisesRegex(OperationRecoveryError, "committed facts"):
            verify_checkpoint_continuation_handoff(
                uncommitted,
                now=1_785_402_000,
            )

        tampered_audit = deepcopy(handoff)
        tampered_audit["side_effect_audit"][0]["unit_set_digest"] = "c" * 64
        with self.assertRaisesRegex(OperationRecoveryError, "audit digest"):
            verify_checkpoint_continuation_handoff(
                tampered_audit,
                now=1_785_402_000,
            )

    def test_checkpoint_continuation_rejects_snapshot_claim_or_metadata_drift(self):
        snapshot = self.drain_snapshot()
        handoff = self._checkpoint_continuation_handoff(snapshot=snapshot)

        claimed_snapshot = deepcopy(snapshot)
        claimed_snapshot["operations"][2]["worker_id_present"] = True
        claimed_row = claimed_snapshot["operations"][2]
        claimed_row["worker_id_digest"] = "e" * 64
        claimed_row_body = {
            key: value
            for key, value in claimed_row.items()
            if key != "row_digest"
        }
        claimed_row["row_digest"] = digest(claimed_row_body)
        claimed_snapshot_body = {
            key: value
            for key, value in claimed_snapshot.items()
            if key != "snapshot_digest"
        }
        claimed_snapshot["snapshot_digest"] = digest(claimed_snapshot_body)
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "live snapshot differs",
        ):
            verify_checkpoint_continuation_handoff(
                handoff,
                live_snapshot=claimed_snapshot,
                now=1_785_402_000,
            )

        stale_snapshot = deepcopy(snapshot)
        stale_snapshot["operations"][2]["result_metadata_digest"] = "d" * 64
        stale_row = stale_snapshot["operations"][2]
        stale_row_body = {
            key: value
            for key, value in stale_row.items()
            if key != "row_digest"
        }
        stale_row["row_digest"] = digest(stale_row_body)
        stale_snapshot_body = {
            key: value
            for key, value in stale_snapshot.items()
            if key != "snapshot_digest"
        }
        stale_snapshot["snapshot_digest"] = digest(stale_snapshot_body)
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "live snapshot differs",
        ):
            verify_checkpoint_continuation_handoff(
                handoff,
                live_snapshot=stale_snapshot,
                now=1_785_402_000,
            )

    def test_checkpoint_continuation_expires_without_renewal(self):
        handoff = self._checkpoint_continuation_handoff()
        with self.assertRaisesRegex(OperationRecoveryError, "expired"):
            verify_checkpoint_continuation_handoff(
                handoff,
                now=handoff["expires_at"],
            )
        self.assertEqual(
            verify_checkpoint_continuation_handoff(
                handoff,
                now=handoff["expires_at"],
                allow_expired=True,
            ),
            handoff,
        )

    def test_checkpoint_continuation_rejects_less_than_worker_attempt_budget(
        self,
    ):
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "attempt|state is invalid",
        ):
            self._checkpoint_continuation_handoff(
                attempts_consumed=19,
                attempts_remaining=1,
            )


if __name__ == "__main__":
    unittest.main()
