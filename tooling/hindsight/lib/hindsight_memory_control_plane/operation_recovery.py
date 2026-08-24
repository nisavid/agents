"""Closed-schema planning for detached async-operation recovery."""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from datetime import datetime
import re
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from .canonical import StrictJsonError, canonical_bytes, digest, strict_json_loads


class OperationRecoveryError(RuntimeError):
    """The detached operation-recovery contract was not satisfied."""


EXPECTED_OPERATION_COUNTS = {
    "retain": 42,
    "refresh_mental_model": 4,
    "consolidation": 2,
}
FAILURE_CAUSE_FAMILIES = frozenset(
    {
        "operation_attempt_deadline",
        "phase_one_deadline",
        "database_statement_timeout",
        "provider_queue_timeout",
        "provider_execution_timeout",
        "provider_authentication",
        "provider_capacity",
        "provider_bad_request",
        "provider_transport",
        "structured_output_validation",
        "upstream_timeout",
        "database_integrity",
        "cancellation",
        "unknown",
    }
)
EXPECTED_CLAIM_RELEASE_ROW_COUNT = 43
EXPECTED_CLAIM_RELEASE_STATUS_COUNTS = {"failed": 43}
EXPECTED_CLAIM_RELEASE_BANK_COUNTS = {"codex": 37, "engineering": 6}
EXPECTED_CLAIM_RELEASE_TYPE_COUNTS = {
    "refresh_mental_model": 6,
    "retain": 37,
}
EXPECTED_CLAIM_RELEASE_PAIR_COUNTS = {
    ("codex", "retain"): 37,
    ("engineering", "refresh_mental_model"): 6,
}
EXACT_DRAIN_WORKER_MAX_RETRIES = 3
EXACT_DRAIN_WORKER_MAX_ATTEMPTS = 4
EXACT_DRAIN_APPROVAL_LIFETIME_SECONDS = 86_400
EXACT_DRAIN_EVIDENCE_MAX_AGE_SECONDS = 3_600
EXACT_DRAIN_TRANSACTION_TIMEOUT_SECONDS = 120
EXACT_DRAIN_EXECUTION_LEASE_SECONDS = 86_400
EXACT_DRAIN_EXECUTION_WINDOW_MAX_SECONDS = 14 * 24 * 60 * 60
EXACT_DRAIN_EXECUTION_EFFECTIVE_CONCURRENCY = 1
EXACT_DRAIN_LEGACY_HATCHERY_MAX_CONCURRENT = 1
EXACT_DRAIN_HATCHERY_MAX_CONCURRENT = 2
EXACT_DRAIN_PHASE_ONE_STATEMENT_TIMEOUT_SECONDS = 120
EXACT_DRAIN_PHASE_ONE_CLIENT_TIMEOUT_SECONDS = 125
EXACT_DRAIN_PHASE_ONE_TIMEOUT_SECONDS = 3_600
EXACT_DRAIN_OPERATION_ATTEMPT_TIMEOUT_SECONDS = 3_600
EXACT_DRAIN_PROVIDER_QUEUE_TIMEOUT_SECONDS = 3_600
EXACT_DRAIN_MAXIMUM_RETRY_DELAY_SECONDS = 3_600
EXACT_DRAIN_STARTUP_MARGIN_SECONDS = (
    EXACT_DRAIN_WORKER_MAX_ATTEMPTS * EXACT_DRAIN_PHASE_ONE_TIMEOUT_SECONDS
)
EXACT_DRAIN_PHASE_REPAIR_CONTRACT = {
    "schema_version": 1,
    "candidate_runtime_snapshot_schema_version": 2,
    "candidate_projection": "id-canonical-name-last-seen",
    "cooccurrence_scope": "candidate-induced-graph",
    "breadcrumbs": "ordinal-candidates-cooccurrence-scoring",
    "statement_timeout_seconds": 120,
    "phase_one_timeout_seconds": 3_600,
}
EXACT_DRAIN_PHASE_REPAIR_CONTRACT_DIGEST = digest(
    EXACT_DRAIN_PHASE_REPAIR_CONTRACT
)
EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V2 = {
    **EXACT_DRAIN_PHASE_REPAIR_CONTRACT,
    "schema_version": 2,
    "candidate_runtime_snapshot_schema_version": 3,
    "database_schema_compatibility": "pre-entity-kind",
    "terminal_generation_serialization": "singleton-row-lock",
}
EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V2_DIGEST = digest(
    EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V2
)
EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V3 = {
    **EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V2,
    "schema_version": 3,
    "candidate_runtime_snapshot_schema_version": 4,
    "fuzzy_candidate_query_batch_size": 10,
    "transient_failure_disposition": "plan-bounded-retry",
}
EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V3_DIGEST = digest(
    EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V3
)
EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V4 = {
    **EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V3,
    "schema_version": 4,
    "candidate_runtime_snapshot_schema_version": 5,
    "client_query_timeout_seconds": 125,
    "typed_task_failure_messages": True,
    "http_400_disposition": "non-retryable",
}
EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V4_DIGEST = digest(
    EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V4
)
EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V5 = {
    **EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V4,
    "schema_version": 5,
    "candidate_runtime_snapshot_schema_version": 6,
    "cooccurrence_query_batch_size": 128,
    "cooccurrence_partition": "indexed-first-endpoint-full-candidate-second",
}
EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V5_DIGEST = digest(
    EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V5
)
EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V6 = {
    **EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V5,
    "schema_version": 6,
    "candidate_runtime_snapshot_schema_version": 7,
    "full_candidate_projection": (
        "id-canonical-name-last-seen-mention-count"
    ),
    "full_cooccurrence_scope": "bank-induced-graph",
    "provider_timeout_semantics": "total-wall-clock",
    "worker_signal_owner": "worker-main",
    "intrabatch_name_codepoint_limit": 4096,
    "intrabatch_total_codepoint_limit": 65536,
}
EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V6_DIGEST = digest(
    EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V6
)
EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V7 = {
    **EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V6,
    "schema_version": 7,
    "operation_attempt_timeout_seconds": 3_600,
    "phase_one_deadline_anchor": "first-phase-one-entry",
    "phase_one_nested_stage_prefixes": ["llm."],
    "provider_timeout_semantics": "split-queue-and-execution",
}
EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V7_DIGEST = digest(
    EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V7
)
EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V8 = {
    **EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V7,
    "schema_version": 8,
    "operation_attempt_timeout_disposition": "task-retry-after-quiescence",
    "deadline_completion_boundary": "completed-before-observation-wins",
    "provider_cancellation_semantics": (
        "queue-or-execution-cancelled-not-provider-failure"
    ),
    "retry_ceiling_cause_preservation": True,
}
EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V8_DIGEST = digest(
    EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V8
)
EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V9 = {
    **EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V8,
    "schema_version": 9,
    "candidate_runtime_snapshot_schema_version": 8,
    "missing_mental_model_refresh_disposition": "idempotent-completion",
}
EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V9_DIGEST = digest(
    EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V9
)
EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT = {
    "schema_version": 1,
    "progress_schema_version": 2,
    "failure_projection": (
        "category-retryable-http-status-error-digest-failure-stage"
    ),
    "checkpoint_projection": (
        "facts-committed-document-count-unit-count-stage-processed-total"
    ),
    "raw_error_text_exposed": False,
}
EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT_DIGEST = digest(
    EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT
)
EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT_V2 = {
    **EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT,
    "schema_version": 2,
    "progress_schema_version": 3,
    "worker_failure_projection": (
        "worker-status-stage-exit-code-category-retryable-http-status-"
        "error-digest"
    ),
    "preclaim_failure_evidence": True,
}
EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT_V2_DIGEST = digest(
    EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT_V2
)
EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT_V3 = {
    **EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT_V2,
    "schema_version": 3,
    "progress_schema_version": 4,
    "provider_timeout_categories": [
        "provider_queue_timeout",
        "provider_execution_timeout",
        "operation_attempt_timeout",
    ],
    "provider_progress_projection": (
        "queued-executing-queue-age-execution-age"
    ),
}
EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT_V3_DIGEST = digest(
    EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT_V3
)
EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT_V4 = {
    **EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT_V3,
    "schema_version": 4,
    "progress_schema_version": 5,
    "provider_cancellation_projection": (
        "queue-cancelled-execution-cancelled"
    ),
    "task_timeout_categories": [
        "database_statement_timeout",
        "upstream_timeout",
    ],
    "database_failure_projection": (
        "cause-family-error-digest-occurrence-count"
    ),
}
EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT_V4_DIGEST = digest(
    EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT_V4
)
EXACT_DRAIN_PROVIDER_TIMEOUT_CONTRACT = {
    "schema_version": 1,
    "kind": "operation-recovery-exact-drain-provider-timeouts",
    "members": [
        {
            "provider_id": provider_id,
            "queue_timeout_seconds": 3_600,
            "execution_timeout_seconds": (
                1_200 if provider_id == "hatchery" else 3_600
            ),
            "max_concurrent": (
                EXACT_DRAIN_HATCHERY_MAX_CONCURRENT
                if provider_id == "hatchery"
                else None
            ),
        }
        for provider_id in (
            "work-codex",
            "personal-codex",
            "alt1-codex",
            "alt2-codex",
            "hatchery",
        )
    ],
}
POST_ABORT_PLAN_LIFETIME_SECONDS = 86_400
POST_ABORT_EVIDENCE_MAX_AGE_SECONDS = 3_600
POST_ABORT_TRANSACTION_TIMEOUT_SECONDS = 120
POST_ABORT_SELECTED_STATUS_COUNTS = {"failed": 1, "processing": 14}
POST_ABORT_SELECTED_TYPE_COUNTS = {
    "refresh_mental_model": 2,
    "retain": 13,
}
POST_ABORT_PRESERVED_STATUS_COUNTS = {"completed": 5, "pending": 28}
POST_ABORT_V2_SELECTED_STATUS_COUNTS = {"processing": 4}
POST_ABORT_V2_SELECTED_TYPE_COUNTS = {"retain": 4}
POST_ABORT_V2_PRESERVED_STATUS_COUNTS = {"completed": 5, "pending": 39}
POST_ABORT_V3_SELECTED_STATUS_COUNTS = {"processing": 3}
POST_ABORT_V3_SELECTED_TYPE_COUNTS = {"retain": 3}
POST_ABORT_V3_PRESERVED_STATUS_COUNTS = {"completed": 5, "pending": 40}
POST_ABORT_V4_SELECTED_STATUS_COUNTS = {"processing": 2}
POST_ABORT_V4_SELECTED_TYPE_COUNTS = {"consolidation": 1, "retain": 1}
POST_ABORT_V4_SELECTED_RETRY_COUNTS = {"consolidation": 3, "retain": 0}
POST_ABORT_V4_PRESERVED_STATUS_COUNTS = {"completed": 5, "pending": 41}
POST_ABORT_V5_SELECTED_STATUS_COUNTS = {"failed": 4, "processing": 1}
POST_ABORT_V5_SELECTED_TYPE_COUNTS = {"retain": 5}
POST_ABORT_V5_FAILED_RETRY_COUNTS = (0, 2, 2, 3)
POST_ABORT_V5_PRESERVED_STATUS_COUNTS = {"completed": 6, "pending": 37}
POST_ABORT_V6_SELECTED_STATUS_COUNTS = {"failed": 3, "processing": 1}
POST_ABORT_V6_SELECTED_TYPE_COUNTS = {"retain": 4}
POST_ABORT_V6_FAILED_RETRY_COUNTS = (3, 3, 3)
POST_ABORT_V6_PRESERVED_STATUS_COUNTS = {"completed": 6, "pending": 38}
POST_ABORT_V7_SELECTED_STATUS_COUNTS = {
    "failed": 3,
    "pending": 1,
    "processing": 1,
}
POST_ABORT_V7_SELECTED_TYPE_COUNTS = {"retain": 5}
POST_ABORT_V7_FAILED_RETRY_COUNTS = (3, 3, 3)
POST_ABORT_V7_PENDING_RETRY_COUNTS = (1,)
POST_ABORT_V7_PRESERVED_STATUS_COUNTS = {"completed": 6, "pending": 37}
POST_ABORT_V8_SELECTED_STATUS_COUNTS = {"failed": 1, "processing": 1}
POST_ABORT_V8_SELECTED_TYPE_COUNTS = {"retain": 2}
POST_ABORT_V8_FAILED_RETRY_COUNTS = (3,)
POST_ABORT_V8_PROCESSING_RETRY_COUNTS = (1,)
POST_ABORT_V8_PRESERVED_STATUS_COUNTS = {"completed": 6, "pending": 40}
POST_ABORT_V9_SELECTED_STATUS_COUNTS = {
    "failed": 1,
    "pending": 1,
    "processing": 1,
}
POST_ABORT_V9_SELECTED_TYPE_COUNTS = {"retain": 3}
POST_ABORT_V9_FAILED_RETRY_COUNTS = POST_ABORT_V8_FAILED_RETRY_COUNTS
POST_ABORT_V9_PENDING_RETRY_COUNTS = (3,)
POST_ABORT_V9_PROCESSING_RETRY_COUNTS = (3,)
POST_ABORT_V9_PRESERVED_STATUS_COUNTS = {"completed": 7, "pending": 38}
POST_ABORT_V10_SELECTION_CONTRACT = {
    "schema_version": 1,
    "reference_scope": "reference-plan-selected-operation-ids",
    "selected_states": ["failed", "owned-pending", "processing"],
    "selected_owner": "reference-worker-digest-and-claim",
    "preserved_states": [
        "reference-completed",
        "reference-worker-completed",
        "unowned-pending",
    ],
    "identity_projection": [
        "operation_id",
        "operation_type",
        "task_payload_digest",
    ],
    "checkpoint_projection": [
        "operation_id",
        "result_metadata_digest",
    ],
    "preserved_projection": ["operation_id", "row_digest"],
    "retry_mutation": "failed-zero-pending-processing-preserve",
}
POST_ABORT_V10_SELECTION_CONTRACT_DIGEST = digest(
    POST_ABORT_V10_SELECTION_CONTRACT
)
POST_ABORT_CONTRACTS = {
    1: (
        POST_ABORT_SELECTED_STATUS_COUNTS,
        POST_ABORT_SELECTED_TYPE_COUNTS,
        POST_ABORT_PRESERVED_STATUS_COUNTS,
    ),
    2: (
        POST_ABORT_V2_SELECTED_STATUS_COUNTS,
        POST_ABORT_V2_SELECTED_TYPE_COUNTS,
        POST_ABORT_V2_PRESERVED_STATUS_COUNTS,
    ),
    3: (
        POST_ABORT_V3_SELECTED_STATUS_COUNTS,
        POST_ABORT_V3_SELECTED_TYPE_COUNTS,
        POST_ABORT_V3_PRESERVED_STATUS_COUNTS,
    ),
    4: (
        POST_ABORT_V4_SELECTED_STATUS_COUNTS,
        POST_ABORT_V4_SELECTED_TYPE_COUNTS,
        POST_ABORT_V4_PRESERVED_STATUS_COUNTS,
    ),
    5: (
        POST_ABORT_V5_SELECTED_STATUS_COUNTS,
        POST_ABORT_V5_SELECTED_TYPE_COUNTS,
        POST_ABORT_V5_PRESERVED_STATUS_COUNTS,
    ),
    6: (
        POST_ABORT_V6_SELECTED_STATUS_COUNTS,
        POST_ABORT_V6_SELECTED_TYPE_COUNTS,
        POST_ABORT_V6_PRESERVED_STATUS_COUNTS,
    ),
    7: (
        POST_ABORT_V7_SELECTED_STATUS_COUNTS,
        POST_ABORT_V7_SELECTED_TYPE_COUNTS,
        POST_ABORT_V7_PRESERVED_STATUS_COUNTS,
    ),
    8: (
        POST_ABORT_V8_SELECTED_STATUS_COUNTS,
        POST_ABORT_V8_SELECTED_TYPE_COUNTS,
        POST_ABORT_V8_PRESERVED_STATUS_COUNTS,
    ),
    9: (
        POST_ABORT_V9_SELECTED_STATUS_COUNTS,
        POST_ABORT_V9_SELECTED_TYPE_COUNTS,
        POST_ABORT_V9_PRESERVED_STATUS_COUNTS,
    ),
}
OPERATION_STATUSES = (
    "pending",
    "processing",
    "completed",
    "failed",
    "cancelled",
)


def exact_drain_progress_archive_path(
    progress_path: Path,
    worker_attempt: int,
) -> Path:
    """Derive the reserved immutable evidence path for one prior attempt."""
    if type(worker_attempt) is not int or worker_attempt < 1:
        raise OperationRecoveryError("exact drain prior attempt is invalid")
    return progress_path.with_name(
        f"{progress_path.stem}.attempt-{worker_attempt}{progress_path.suffix}"
    )


def _exact_drain_archive_paths(progress_path: str) -> set[str]:
    path = Path(progress_path)
    return {
        str(exact_drain_progress_archive_path(path, attempt))
        for attempt in range(1, EXACT_DRAIN_WORKER_MAX_ATTEMPTS + 1)
    }
ERROR_CATEGORIES = frozenset(
    {
        "none",
        "authentication",
        "provider_capacity",
        "provider_transport",
        "internal",
        "unknown",
    }
)
MAX_PLAN_LIFETIME_SECONDS = 3600
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
SOURCE_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\Z")

BACKUP_KEYS = frozenset(
    {
        "schema_version",
        "artifact_sha256",
        "restore_identity_digest",
        "postgres_system_identifier",
        "source_authority",
        "source_authority_digest",
        "toolchain_digest",
        "full_schema",
        "restore_tested",
        "plaintext_disposed",
    }
)
HISTORICAL_SOURCE_AUTHORITY_KEYS = frozenset(
    {
        "kind",
        "artifact_path",
        "artifact_sha256",
        "postgres_system_identifier",
        "generation_before",
        "generation_after",
    }
)
LIVE_SOURCE_AUTHORITY_KEYS = frozenset(
    {
        "kind",
        "postgres_system_identifier",
        "data_identity_digest",
        "generation_before",
        "generation_after",
        "binding",
    }
)
PG0_BINDING_KEYS = frozenset(
    {
        "instance",
        "data_dir",
        "data_device",
        "data_inode",
        "port",
        "pid",
        "started_at",
        "socket_dir",
        "socket_path",
        "database",
        "user",
    }
)
SAFE_ROW_KEYS = frozenset(
    {
        "operation_id",
        "bank_id",
        "operation_type",
        "status",
        "created_at",
        "updated_at",
        "completed_at",
        "retry_count",
        "next_retry_at",
        "worker_id_present",
        "worker_id_digest",
        "claimed_at",
        "task_payload_present",
        "task_payload_digest",
        "result_metadata_digest",
        "error_category",
        "error_digest",
    }
)
COHORT_OPERATION_KEYS = frozenset(
    {
        "operation_id",
        "operation_type",
        "baseline_status",
        "created_at",
        "updated_at",
        "retry_count",
        "task_payload_present",
        "task_payload_digest",
        "result_metadata_digest",
        "row_digest",
    }
)
COHORT_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "profile_id",
        "schema",
        "bank_id",
        "generation",
        "backup",
        "expected_operation_counts",
        "operations",
        "created_at",
        "cohort_digest",
    }
)
INSTALLATION_AUTHORITY_KEYS = frozenset(
    {
        "consumer_id",
        "profile_id",
        "schema",
        "bank_id",
        "install_state_digest",
        "binding_generation_digest",
        "installed_release_version",
        "current_release_digest",
        "recorded_data_identity_digest",
        "observed_data_identity_digest",
        "postgres_system_identifier",
    }
)
DATA_IDENTITY_REBIND_HANDOFF_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "plan_digest",
        "authorization_receipt_digest",
        "application_receipt_digest",
        "verification_receipt_digest",
        "rollback_bundle_digest",
        "installation_state_digest_before",
        "installation_state_digest_after",
        "binding_generation_digest",
        "current_release_digest",
        "old_data_identity_digest",
        "reference_observed_data_identity_digest",
        "new_data_identity_digest",
        "postgres_system_identifier",
        "database_continuity_digest",
        "post_evidence_digest",
        "verified_at",
        "handoff_digest",
    }
)
INSTALLATION_AUTHORITY_V2_KEYS = INSTALLATION_AUTHORITY_KEYS | frozenset(
    {"schema_version", "data_identity_rebind_handoff"}
)
LIVE_OPERATION_KEYS = frozenset(
    {
        "operation_id",
        "operation_type",
        "current_status",
        "created_at",
        "updated_at",
        "completed_at",
        "retry_count",
        "next_retry_at",
        "worker_id_present",
        "worker_id_digest",
        "claimed_at",
        "task_payload_present",
        "task_payload_digest",
        "result_metadata_digest",
        "error_category",
        "error_digest",
        "row_digest",
    }
)
LIVE_SNAPSHOT_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "cohort_digest",
        "installation_authority",
        "generation_before",
        "generation_after",
        "status_counts",
        "operations",
        "observed_at",
        "snapshot_digest",
    }
)
RELEASE_IDENTITY_KEYS = frozenset(
    {"source_commit", "version", "release_digest"}
)
ROLLBACK_ENCRYPTION_KEYS = frozenset(
    {"recipient", "age_path", "age_sha256"}
)
POLICY = {
    "pending": "preserve",
    "processing": "reject",
    "completed": "preserve",
    "failed": "requeue",
    "cancelled": "requeue",
}
SELECTED_OPERATION_KEYS = frozenset(
    {
        "operation_id",
        "operation_type",
        "expected_status",
        "row_digest",
        "task_payload_digest",
    }
)
PLAN_KEYS = frozenset(
    {
        "schema_version",
        "action",
        "candidate_release",
        "installation_authority",
        "cohort",
        "live_snapshot",
        "cohort_digest",
        "snapshot_digest",
        "cohort_backup_artifact_digest",
        "rollback_backup",
        "rollback_encryption",
        "pre_generation",
        "policy",
        "selected_operations",
        "selected_operation_count",
        "preserved_status_counts",
        "rollback_backup_path",
        "rollback_bundle_path",
        "authorization_receipt_path",
        "application_receipt_path",
        "verification_receipt_path",
        "rollback_receipt_path",
        "created_at",
        "expires_at",
        "plan_digest",
    }
)
EXACT_DRAIN_PLAN_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "action",
        "authority",
        "mutation_authorized",
        "candidate_release",
        "installation_authority",
        "cohort",
        "live_snapshot",
        "cohort_digest",
        "snapshot_digest",
        "pre_generation",
        "selected_operations",
        "selected_operation_count",
        "selected_status_counts",
        "selected_type_counts",
        "selected_row_set_digest",
        "preserved_status_counts",
        "rollback_backup",
        "rollback_backup_path",
        "provider_policy_digest",
        "effective_profile_digest",
        "worker_runtime_digest",
        "worker_max_attempts",
        "worker_max_retries",
        "authorization_receipt_path",
        "application_receipt_path",
        "progress_artifact_path",
        "status_artifact_path",
        "verification_receipt_path",
        "created_at",
        "expires_at",
        "plan_digest",
    }
)
EXACT_DRAIN_PLAN_V2_KEYS = EXACT_DRAIN_PLAN_KEYS | frozenset(
    {
        "evidence_observed_at",
        "evidence_max_age_seconds",
        "transaction_timeout_seconds",
        "execution_lease_seconds",
    }
)
EXACT_DRAIN_PLAN_V3_KEYS = EXACT_DRAIN_PLAN_V2_KEYS | frozenset(
    {
        "phase_one_statement_timeout_seconds",
        "phase_one_timeout_seconds",
        "phase_repair_contract_digest",
    }
)
EXACT_DRAIN_PLAN_V4_KEYS = EXACT_DRAIN_PLAN_V3_KEYS
EXACT_DRAIN_PLAN_V5_KEYS = EXACT_DRAIN_PLAN_V4_KEYS
EXACT_DRAIN_PLAN_V6_KEYS = EXACT_DRAIN_PLAN_V5_KEYS | frozenset(
    {
        "phase_one_client_timeout_seconds",
        "progress_schema_version",
        "failure_evidence_contract_digest",
    }
)
EXACT_DRAIN_PLAN_V7_KEYS = EXACT_DRAIN_PLAN_V6_KEYS
EXACT_DRAIN_PLAN_V8_KEYS = EXACT_DRAIN_PLAN_V7_KEYS
EXACT_DRAIN_PLAN_V9_KEYS = EXACT_DRAIN_PLAN_V8_KEYS
EXACT_DRAIN_EXECUTION_WINDOW_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "anchor",
        "renewable",
        "selected_operation_count",
        "remaining_attempt_count",
        "retry_wait_count",
        "effective_concurrency",
        "phase_one_timeout_seconds",
        "transaction_timeout_seconds",
        "maximum_retry_delay_seconds",
        "startup_margin_seconds",
        "transaction_margin_seconds",
        "shutdown_attempt_count",
        "shutdown_margin_seconds",
        "calculated_seconds",
        "maximum_seconds",
    }
)
EXACT_DRAIN_EXECUTION_WINDOW_V2_KEYS = (
    EXACT_DRAIN_EXECUTION_WINDOW_KEYS
    - frozenset({"phase_one_timeout_seconds"})
) | frozenset({"operation_attempt_timeout_seconds"})
EXACT_DRAIN_PROVIDER_TIMEOUT_CONTRACT_KEYS = frozenset(
    {"schema_version", "kind", "members"}
)
EXACT_DRAIN_PROVIDER_TIMEOUT_MEMBER_KEYS = frozenset(
    {
        "provider_id",
        "queue_timeout_seconds",
        "execution_timeout_seconds",
        "max_concurrent",
    }
)
EXACT_DRAIN_RECOVERY_CONTEXT_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "origin",
        "generation",
        "recovery_epoch",
        "candidate_release_digest",
        "selected_operation_ids_digest",
        "initial_origin_digest",
        "post_abort_selected_operation_ids_digest",
        "post_abort_plan_digest",
        "post_abort_application_receipt_digest",
        "post_abort_verification_receipt_digest",
        "retry_recovery_digest",
        "selected_checkpoint_set_digest",
        "preserved_row_set_digest",
    }
)
EXACT_DRAIN_RECOVERY_CONTEXT_V4_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "origin",
        "generation",
        "recovery_epoch",
        "reconciliation_cycle",
        "candidate_release_digest",
        "selected_operation_ids_digest",
        "initial_origin_digest",
        "post_terminal_reconciliation_plan_digest",
        "post_terminal_reconciliation_application_receipt_digest",
        "post_terminal_reconciliation_verification_receipt_digest",
        "terminal_plan_digest",
        "terminal_authorization_receipt_digest",
        "terminal_application_receipt_digest",
        "terminal_progress_digest",
        "terminal_status_digest",
        "retry_recovery_digest",
        "selected_checkpoint_set_digest",
        "preserved_row_set_digest",
    }
)
# A checkpoint-continuation handoff is deliberately separate from the exact
# drain plan schemas above.  It is a read-only, payload-free proof that a
# pending operation already has committed retain side effects and may be
# resumed idempotently by a future plan implementation.  Keeping this
# contract separate prevents callers from treating a candidate-repair handoff
# (which is valid only before provider work starts) as permission to retry a
# partially executed task.
CHECKPOINT_CONTINUATION_CHECKPOINT_KEYS = frozenset(
    {
        "facts_committed",
        "committed_document_count",
        "unit_ids_count",
        "stage",
        "processed",
        "total",
    }
)
CHECKPOINT_CONTINUATION_OPERATION_KEYS = frozenset(
    {
        "operation_id",
        "operation_type",
        "current_status",
        "row_digest",
        "task_payload_digest",
        "result_metadata_digest",
        "checkpoint",
        "retry_count",
        "attempts_consumed",
        "attempts_remaining",
        "worker_id_present",
        "worker_id_digest",
        "claimed_at",
        "next_retry_at",
        "error_category",
        "error_digest",
    }
)
CHECKPOINT_CONTINUATION_AUDIT_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "operation_id",
        "generation",
        "row_digest",
        "result_metadata_digest",
        "checkpoint",
        "document_count",
        "unit_count",
        "document_set_digest",
        "unit_set_digest",
        "idempotent_resume",
        "audit_digest",
    }
)
CHECKPOINT_CONTINUATION_CONTEXT_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "origin",
        "generation",
        "recovery_epoch",
        "reconciliation_cycle",
        "source_plan_digest",
        "source_recovery_context_digest",
        "source_reconciliation_plan_digest",
        "source_terminal_status_digest",
        "source_candidate_release_digest",
        "candidate_release_digest",
        "selected_operation_ids_digest",
        "selected_checkpoint_set_digest",
        "side_effect_audit_digest",
        "context_digest",
    }
)
CHECKPOINT_CONTINUATION_HANDOFF_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "action",
        "authority",
        "mutation_authorized",
        "continuation_context",
        "continuation_context_digest",
        "source_candidate_release",
        "candidate_release",
        "operations",
        "operation_count",
        "side_effect_audit",
        "side_effect_audit_digest",
        "attempt_ledger_digest",
        "created_at",
        "expires_at",
        "handoff_digest",
    }
)
CHECKPOINT_CONTINUATION_LIFETIME_SECONDS = 3_600
CHECKPOINT_CONTINUATION_MAX_CUMULATIVE_ATTEMPTS = 20
EXACT_DRAIN_PLAN_V10_KEYS = (
    EXACT_DRAIN_PLAN_V9_KEYS - frozenset({"execution_lease_seconds"})
) | frozenset(
    {
        "execution_window",
        "recovery_context",
        "recovery_context_digest",
    }
)
EXACT_DRAIN_PLAN_V11_KEYS = EXACT_DRAIN_PLAN_V10_KEYS | frozenset(
    {
        "operation_attempt_timeout_seconds",
        "phase_one_deadline_anchor",
        "phase_one_nested_stage_prefixes",
        "provider_timeout_contract",
    }
)
EXACT_DRAIN_PLAN_V12_KEYS = EXACT_DRAIN_PLAN_V11_KEYS | frozenset(
    {"operation_attempt_timeout_disposition"}
)
EXACT_DRAIN_PLAN_V13_KEYS = EXACT_DRAIN_PLAN_V12_KEYS | frozenset(
    {"hatchery_capability_receipt", "hatchery_capability_receipt_digest"}
)
EXACT_DRAIN_PLAN_V14_KEYS = EXACT_DRAIN_PLAN_V13_KEYS | frozenset(
    {
        "checkpoint_continuation_handoff",
        "checkpoint_continuation_handoff_digest",
    }
)
HATCHERY_CAPABILITY_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "provider_id",
        "provider_policy_digest",
        "provider_identity_digest",
        "model_digest",
        "observed_at",
        "successful",
        "receipt_digest",
    }
)
POST_ABORT_PLAN_V1_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "action",
        "authority",
        "mutation_authorized",
        "candidate_release",
        "installation_authority",
        "reference_plan",
        "reference_plan_digest",
        "reference_worker_id_digest",
        "live_snapshot",
        "cohort_digest",
        "snapshot_digest",
        "pre_generation",
        "evidence_observed_at",
        "evidence_max_age_seconds",
        "transaction_timeout_seconds",
        "selected_operations",
        "selected_operation_count",
        "selected_status_counts",
        "selected_type_counts",
        "selected_row_set_digest",
        "preserved_status_counts",
        "rollback_backup",
        "rollback_encryption",
        "rollback_backup_path",
        "rollback_bundle_path",
        "authorization_receipt_path",
        "application_receipt_path",
        "verification_receipt_path",
        "rollback_receipt_path",
        "created_at",
        "expires_at",
        "plan_digest",
    }
)
POST_ABORT_PLAN_V2_KEYS = POST_ABORT_PLAN_V1_KEYS | frozenset(
    {
        "reference_application_authorization",
        "reference_application_authorization_digest",
        "reference_application_journal",
        "reference_application_journal_digest",
    }
)
POST_ABORT_PLAN_V3_KEYS = POST_ABORT_PLAN_V2_KEYS | frozenset(
    {"reference_application_progress_digest"}
)
POST_ABORT_PLAN_V4_KEYS = POST_ABORT_PLAN_V3_KEYS
POST_ABORT_PLAN_V5_KEYS = POST_ABORT_PLAN_V4_KEYS
POST_ABORT_PLAN_V6_KEYS = POST_ABORT_PLAN_V5_KEYS
POST_ABORT_PLAN_V7_KEYS = POST_ABORT_PLAN_V6_KEYS
POST_ABORT_PLAN_V8_KEYS = POST_ABORT_PLAN_V7_KEYS
POST_ABORT_PLAN_V9_KEYS = POST_ABORT_PLAN_V8_KEYS
POST_ABORT_PLAN_V10_KEYS = POST_ABORT_PLAN_V9_KEYS | frozenset(
    {
        "selection_contract_digest",
        "selected_checkpoint_set_digest",
        "preserved_row_set_digest",
        "retry_recovery",
        "retry_recovery_digest",
    }
)
POST_ABORT_PLAN_V11_KEYS = POST_ABORT_PLAN_V10_KEYS
POST_ABORT_PLAN_V12_KEYS = POST_ABORT_PLAN_V11_KEYS
POST_ABORT_PLAN_V13_KEYS = POST_ABORT_PLAN_V12_KEYS | frozenset(
    {
        "reference_application_receipt_digest",
        "reference_terminal_status",
        "reference_terminal_status_digest",
        "reference_worker_exit",
        "reference_worker_exit_digest",
    }
)
POST_ABORT_V10_RETRY_RECOVERY_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "recovery_epoch_before",
        "recovery_epoch_after",
        "recovery_epoch_ceiling",
        "ordinary_retry_ceiling",
        "ordinary_attempt_ceiling",
        "maximum_cumulative_attempts",
        "operation_count",
        "failed_reset_count",
        "operations",
        "operation_set_digest",
    }
)
POST_ABORT_V10_RETRY_OPERATION_KEYS = frozenset(
    {
        "operation_id",
        "expected_status",
        "retry_count_before",
        "retry_count_after",
        "attempts_consumed_before",
        "attempts_available_after",
        "cumulative_attempt_ceiling",
        "reset_applied",
    }
)
POST_ABORT_V11_RETRY_RECOVERY_KEYS = (
    POST_ABORT_V10_RETRY_RECOVERY_KEYS
    | frozenset(
        {
            "prior_retry_recovery",
            "prior_retry_recovery_digest",
        }
    )
)
POST_ABORT_V11_RETRY_OPERATION_KEYS = frozenset(
    {
        "operation_id",
        "expected_status",
        "reference_retry_count",
        "retry_count_before",
        "retry_count_after",
        "prior_attempts_consumed",
        "attempts_consumed_during_reference",
        "attempts_consumed_before",
        "attempts_available_after",
        "cumulative_attempt_ceiling",
        "reset_applied",
    }
)
POST_ABORT_V12_RETRY_RECOVERY_KEYS = POST_ABORT_V11_RETRY_RECOVERY_KEYS
POST_ABORT_V12_RETRY_OPERATION_KEYS = POST_ABORT_V11_RETRY_OPERATION_KEYS
POST_ABORT_V13_RETRY_RECOVERY_KEYS = (
    POST_ABORT_V12_RETRY_RECOVERY_KEYS
    | frozenset(
        {
            "reconciliation_cycle_before",
            "reconciliation_cycle_after",
            "reconciliation_cycle_ceiling",
        }
    )
)
POST_ABORT_V13_RETRY_OPERATION_KEYS = POST_ABORT_V12_RETRY_OPERATION_KEYS
POST_ABORT_REFERENCE_WORKER_EXIT_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "worker_pid",
        "worker_start_time",
        "observed_at",
        "state",
        "evidence_digest",
    }
)
POST_ABORT_REFERENCE_JOURNAL_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "plan_digest",
        "authorization_receipt_digest",
        "started_at",
        "worker_pid",
        "worker_start_time",
        "worker_attempt",
        "receipt_digest",
    }
)
QUEUE_BLOCKER_INPUT_KEYS = frozenset(
    {
        "operation_id",
        "bank_id",
        "operation_type",
        "status",
        "created_at",
        "updated_at",
        "completed_at",
        "retry_count",
        "next_retry_at",
        "worker_id_present",
        "worker_id_digest",
        "claimed_at",
        "task_payload_present",
        "task_payload_digest",
        "in_reference_cohort",
        "in_reference_selected_set",
        "blocker_reason",
    }
)
QUEUE_BLOCKER_KEYS = QUEUE_BLOCKER_INPUT_KEYS | {"row_digest"}
QUEUE_BLOCKER_REASONS = {
    "processing": "processing",
    "pending": "claimed_pending",
    "failed": "claimed_failed",
    "cancelled": "claimed_cancelled",
}
QUEUE_BLOCKER_SCOPE = {
    "bank_ids": "all",
    "operation_types": "all",
    "statuses": list(QUEUE_BLOCKER_REASONS),
    "completed_claims": "excluded",
    "unclaimed_terminal_or_pending": "excluded",
    "selected_failed_or_cancelled_claims": "excluded",
    "processing_selection_exception": "none",
}
QUEUE_BLOCKER_CLASSIFICATION_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "authority",
        "mutation_authorized",
        "classifier_candidate_release",
        "guard_reference_candidate_release",
        "guard_contract_version",
        "guard_contract_digest",
        "reference_plan_digest",
        "reference_plan_expired",
        "reference_cohort_digest",
        "reference_snapshot_digest",
        "reference_selected_operation_ids_digest",
        "installation_authority",
        "profile_id",
        "schema",
        "generation_before",
        "generation_after",
        "scope",
        "blocker_count",
        "status_counts",
        "bank_counts",
        "operation_type_counts",
        "blockers",
        "observed_at",
        "expires_at",
        "classification_digest",
    }
)
CLAIM_RELEASE_ROW_KEYS = QUEUE_BLOCKER_KEYS | {"nonclaim_state_digest"}
CLAIM_RELEASE_PERMITTED_BLOCKER_ROW_KEYS = CLAIM_RELEASE_ROW_KEYS | {
    "reference_row_digest"
}
CLAIM_RELEASE_PLAN_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "authority",
        "mutation_authorized",
        "candidate_release",
        "installation_authority",
        "predecessor_classification_digest",
        "live_classification_digest",
        "reference_plan_digest",
        "reference_cohort_operation_ids",
        "reference_cohort_operation_ids_digest",
        "reference_selected_operation_ids_digest",
        "guard_contract_version",
        "guard_contract_digest",
        "profile_id",
        "schema",
        "pre_generation",
        "selected_rows",
        "selected_row_count",
        "selected_row_set_digest",
        "permitted_blocker_rows",
        "permitted_blocker_count",
        "permitted_blocker_row_set_digest",
        "guard_exclusion_set_digest",
        "status_counts",
        "bank_counts",
        "operation_type_counts",
        "rollback_encryption",
        "rollback_bundle_path",
        "authorization_receipt_path",
        "application_receipt_path",
        "verification_receipt_path",
        "rollback_receipt_path",
        "created_at",
        "expires_at",
        "plan_digest",
    }
)


def _normalized(value: Any) -> Any:
    try:
        return strict_json_loads(canonical_bytes(value))
    except (RecursionError, StrictJsonError, TypeError, ValueError):
        raise OperationRecoveryError(
            "operation-recovery value is not canonical JSON"
        ) from None


def _closed(value: Any, keys: Set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise OperationRecoveryError(f"{label} is invalid")
    return value


def _text(value: Any, label: str, *, maximum: int = 4096) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or any(character in value for character in "\r\n\0")
    ):
        raise OperationRecoveryError(f"{label} is invalid")
    return value


def _sha(value: Any, label: str) -> str:
    text = _text(value, label, maximum=64)
    if SHA256.fullmatch(text) is None:
        raise OperationRecoveryError(f"{label} is invalid")
    return text


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise OperationRecoveryError(f"{label} is invalid")
    return value


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _text(value, label)


def _operation_id(value: Any) -> str:
    text = _text(value, "operation ID", maximum=36)
    try:
        parsed = uuid.UUID(text)
    except (AttributeError, ValueError):
        raise OperationRecoveryError("operation ID is invalid") from None
    if str(parsed) != text or parsed.version != 4:
        raise OperationRecoveryError("operation ID is invalid")
    return text


def _absolute_path(value: Any, label: str) -> str:
    text = _text(value, label)
    path = Path(text)
    if (
        not path.is_absolute()
        or not path.name
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise OperationRecoveryError(f"{label} is invalid")
    try:
        return str(path.resolve(strict=False))
    except OSError as error:
        raise OperationRecoveryError(f"{label} is invalid") from error


def normalize_pg0_binding(value: Any, label: str) -> dict[str, Any]:
    binding = _closed(_normalized(value), PG0_BINDING_KEYS, label)
    checked = {
        "instance": _text(
            binding["instance"],
            f"{label} instance",
            maximum=128,
        ),
        "data_dir": _absolute_path(
            binding["data_dir"],
            f"{label} data directory",
        ),
        "data_device": _integer(
            binding["data_device"],
            f"{label} data device",
        ),
        "data_inode": _integer(
            binding["data_inode"],
            f"{label} data inode",
            minimum=1,
        ),
        "port": _integer(
            binding["port"],
            f"{label} port",
            minimum=1,
        ),
        "pid": _integer(
            binding["pid"],
            f"{label} PID",
            minimum=1,
        ),
        "started_at": _integer(
            binding["started_at"],
            f"{label} start time",
            minimum=1,
        ),
        "socket_dir": _absolute_path(
            binding["socket_dir"],
            f"{label} socket directory",
        ),
        "socket_path": _absolute_path(
            binding["socket_path"],
            f"{label} socket path",
        ),
        "database": _text(
            binding["database"],
            f"{label} database",
            maximum=63,
        ),
        "user": _text(
            binding["user"],
            f"{label} user",
            maximum=63,
        ),
    }
    if (
        checked["instance"] != "hindsight-embed-systalyze"
        or checked["port"] > 65535
        or checked["database"] != "hindsight"
        or checked["user"] != "hindsight"
        or Path(checked["socket_path"])
        != Path(checked["socket_dir"]) / f".s.PGSQL.{checked['port']}"
    ):
        raise OperationRecoveryError(f"{label} is invalid")
    return checked


def _source_authority(
    value: Any,
    label: str,
    *,
    expected_kind: str,
) -> dict[str, Any]:
    normalized = _normalized(value)
    if not isinstance(normalized, Mapping):
        raise OperationRecoveryError(f"{label} is invalid")
    kind = normalized.get("kind")
    if kind != expected_kind:
        raise OperationRecoveryError(f"{label} is invalid")
    if kind == "approved-historical-backup":
        authority = _closed(
            normalized,
            HISTORICAL_SOURCE_AUTHORITY_KEYS,
            label,
        )
        checked = {
            "kind": kind,
            "artifact_path": _absolute_path(
                authority["artifact_path"],
                f"{label} artifact path",
            ),
            "artifact_sha256": _sha(
                authority["artifact_sha256"],
                f"{label} artifact digest",
            ),
            "postgres_system_identifier": _text(
                authority["postgres_system_identifier"],
                f"{label} PostgreSQL system identifier",
                maximum=32,
            ),
            "generation_before": _text(
                authority["generation_before"],
                f"{label} generation before",
            ),
            "generation_after": _text(
                authority["generation_after"],
                f"{label} generation after",
            ),
        }
    elif kind == "verified-live-pg0-backup":
        authority = _closed(
            normalized,
            LIVE_SOURCE_AUTHORITY_KEYS,
            label,
        )
        checked = {
            "kind": kind,
            "postgres_system_identifier": _text(
                authority["postgres_system_identifier"],
                f"{label} PostgreSQL system identifier",
                maximum=32,
            ),
            "data_identity_digest": _sha(
                authority["data_identity_digest"],
                f"{label} data identity digest",
            ),
            "generation_before": _text(
                authority["generation_before"],
                f"{label} generation before",
            ),
            "generation_after": _text(
                authority["generation_after"],
                f"{label} generation after",
            ),
            "binding": normalize_pg0_binding(
                authority["binding"],
                f"{label} pg0 binding",
            ),
        }
    else:
        raise OperationRecoveryError(f"{label} is invalid")
    if checked["generation_before"] != checked["generation_after"]:
        raise OperationRecoveryError(
            "operation-recovery source authority generation changed"
        )
    return checked


def _backup(
    value: Any,
    label: str,
    *,
    expected_source_kind: str,
) -> dict[str, Any]:
    backup = _closed(_normalized(value), BACKUP_KEYS, label)
    if (
        backup["schema_version"] != 1
        or backup["full_schema"] is not True
        or backup["restore_tested"] is not True
        or backup["plaintext_disposed"] is not True
    ):
        raise OperationRecoveryError(f"{label} is invalid")
    checked = {
        "schema_version": 1,
        "artifact_sha256": _sha(
            backup["artifact_sha256"],
            f"{label} artifact digest",
        ),
        "restore_identity_digest": _sha(
            backup["restore_identity_digest"],
            f"{label} restore identity digest",
        ),
        "postgres_system_identifier": _text(
            backup["postgres_system_identifier"],
            f"{label} PostgreSQL system identifier",
            maximum=32,
        ),
        "source_authority": _source_authority(
            backup["source_authority"],
            f"{label} source authority",
            expected_kind=expected_source_kind,
        ),
        "source_authority_digest": _sha(
            backup["source_authority_digest"],
            f"{label} source authority digest",
        ),
        "toolchain_digest": _sha(
            backup["toolchain_digest"],
            f"{label} toolchain digest",
        ),
        "full_schema": True,
        "restore_tested": True,
        "plaintext_disposed": True,
    }
    authority = checked["source_authority"]
    if (
        authority["postgres_system_identifier"]
        != checked["postgres_system_identifier"]
        or (
            authority["kind"] == "approved-historical-backup"
            and authority["artifact_sha256"] != checked["artifact_sha256"]
        )
    ):
        raise OperationRecoveryError(f"{label} source authority differs")
    if checked["source_authority_digest"] != digest(authority):
        raise OperationRecoveryError(f"{label} source authority digest differs")
    return checked


def _rollback_encryption(value: Any) -> dict[str, str]:
    encryption = _closed(
        _normalized(value),
        ROLLBACK_ENCRYPTION_KEYS,
        "operation-recovery rollback encryption",
    )
    recipient = _text(
        encryption["recipient"],
        "operation-recovery rollback recipient",
        maximum=4096,
    )
    if (
        not recipient.startswith("age1")
        or not recipient.isascii()
        or any(
        character in recipient for character in "\r\n\0"
        )
    ):
        raise OperationRecoveryError(
            "operation-recovery rollback recipient is invalid"
        )
    return {
        "recipient": recipient,
        "age_path": _absolute_path(
            encryption["age_path"],
            "operation-recovery rollback age path",
        ),
        "age_sha256": _sha(
            encryption["age_sha256"],
            "operation-recovery rollback age digest",
        ),
    }


def _safe_row(value: Any) -> dict[str, Any]:
    row = _closed(_normalized(value), SAFE_ROW_KEYS, "operation evidence")
    status = _text(row["status"], "operation status", maximum=32)
    operation_type = _text(
        row["operation_type"],
        "operation type",
        maximum=128,
    )
    error_category = _text(
        row["error_category"],
        "operation error category",
        maximum=64,
    )
    if status not in OPERATION_STATUSES:
        raise OperationRecoveryError("operation status is invalid")
    if operation_type not in EXPECTED_OPERATION_COUNTS:
        raise OperationRecoveryError("operation type is invalid")
    if error_category not in ERROR_CATEGORIES:
        raise OperationRecoveryError("operation error category is invalid")
    if type(row["worker_id_present"]) is not bool:
        raise OperationRecoveryError("operation worker evidence is invalid")
    worker_id_digest = row["worker_id_digest"]
    if row["worker_id_present"]:
        worker_id_digest = _sha(
            worker_id_digest,
            "operation worker ID digest",
        )
    elif worker_id_digest is not None:
        raise OperationRecoveryError("operation worker evidence is invalid")
    if type(row["task_payload_present"]) is not bool:
        raise OperationRecoveryError("operation payload evidence is invalid")
    payload_digest = row["task_payload_digest"]
    if row["task_payload_present"]:
        payload_digest = _sha(payload_digest, "operation payload digest")
    elif payload_digest is not None:
        raise OperationRecoveryError("operation payload evidence is invalid")
    error_digest = row["error_digest"]
    if error_category == "none":
        if error_digest is not None:
            raise OperationRecoveryError("operation error evidence is invalid")
    else:
        error_digest = _sha(error_digest, "operation error digest")
    return {
        "operation_id": _operation_id(row["operation_id"]),
        "bank_id": _text(row["bank_id"], "operation bank ID", maximum=256),
        "operation_type": operation_type,
        "status": status,
        "created_at": _text(row["created_at"], "operation created-at"),
        "updated_at": _text(row["updated_at"], "operation updated-at"),
        "completed_at": _optional_text(
            row["completed_at"],
            "operation completed-at",
        ),
        "retry_count": _integer(
            row["retry_count"],
            "operation retry count",
        ),
        "next_retry_at": _optional_text(
            row["next_retry_at"],
            "operation next-retry-at",
        ),
        "worker_id_present": row["worker_id_present"],
        "worker_id_digest": worker_id_digest,
        "claimed_at": _optional_text(row["claimed_at"], "operation claimed-at"),
        "task_payload_present": row["task_payload_present"],
        "task_payload_digest": payload_digest,
        "result_metadata_digest": _sha(
            row["result_metadata_digest"],
            "operation result metadata digest",
        ),
        "error_category": error_category,
        "error_digest": error_digest,
    }


def _cohort_operation(value: Any) -> dict[str, Any]:
    item = _closed(
        _normalized(value),
        COHORT_OPERATION_KEYS,
        "operation cohort entry",
    )
    body = {
        "operation_id": _operation_id(item["operation_id"]),
        "operation_type": _text(
            item["operation_type"],
            "operation type",
            maximum=128,
        ),
        "baseline_status": _text(
            item["baseline_status"],
            "operation baseline status",
            maximum=32,
        ),
        "created_at": _text(item["created_at"], "operation created-at"),
        "updated_at": _text(item["updated_at"], "operation updated-at"),
        "retry_count": _integer(item["retry_count"], "operation retry count"),
        "task_payload_present": item["task_payload_present"],
        "task_payload_digest": item["task_payload_digest"],
        "result_metadata_digest": _sha(
            item["result_metadata_digest"],
            "operation result metadata digest",
        ),
    }
    if (
        body["operation_type"] not in EXPECTED_OPERATION_COUNTS
        or body["baseline_status"] not in {"pending", "processing"}
        or body["task_payload_present"] is not True
    ):
        raise OperationRecoveryError("operation cohort entry is invalid")
    body["task_payload_digest"] = _sha(
        body["task_payload_digest"],
        "operation payload digest",
    )
    if _sha(item["row_digest"], "operation row digest") != digest(body):
        raise OperationRecoveryError("operation cohort row digest differs")
    return {**body, "row_digest": item["row_digest"]}


def create_cohort_manifest(
    rows: Sequence[Mapping[str, Any]],
    *,
    profile_id: str,
    schema: str,
    bank_id: str,
    generation: str,
    backup: Mapping[str, Any],
    created_at: int | None = None,
) -> Mapping[str, Any]:
    """Freeze the exact pre-drain operation set without exposing payloads."""
    profile_id = _text(profile_id, "operation-recovery profile ID", maximum=128)
    schema = _text(schema, "operation-recovery schema", maximum=63)
    bank_id = _text(bank_id, "operation-recovery bank ID", maximum=256)
    if (
        profile_id != "systalyze"
        or schema != "public"
        or bank_id != "engineering"
        or isinstance(rows, (str, bytes))
        or not isinstance(rows, Sequence)
    ):
        raise OperationRecoveryError("operation-recovery authority is invalid")
    normalized_rows = [_safe_row(row) for row in rows]
    if any(
        row["bank_id"] != bank_id
        or row["status"] not in {"pending", "processing"}
        or row["task_payload_present"] is not True
        for row in normalized_rows
    ):
        raise OperationRecoveryError("operation cohort contains invalid rows")
    counts = {
        operation_type: sum(
            row["operation_type"] == operation_type for row in normalized_rows
        )
        for operation_type in EXPECTED_OPERATION_COUNTS
    }
    if counts != EXPECTED_OPERATION_COUNTS:
        raise OperationRecoveryError(
            "operation cohort differs from authorized operation counts"
        )
    identifiers = [row["operation_id"] for row in normalized_rows]
    if len(identifiers) != len(set(identifiers)):
        raise OperationRecoveryError("operation cohort contains duplicate IDs")
    operations = []
    for row in sorted(
        normalized_rows,
        key=lambda item: (item["created_at"], item["operation_id"]),
    ):
        body = {
            "operation_id": row["operation_id"],
            "operation_type": row["operation_type"],
            "baseline_status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "retry_count": row["retry_count"],
            "task_payload_present": row["task_payload_present"],
            "task_payload_digest": row["task_payload_digest"],
            "result_metadata_digest": row["result_metadata_digest"],
        }
        operations.append({**body, "row_digest": digest(body)})
    checked_generation = _text(
        generation,
        "operation cohort generation",
    )
    checked_backup = _backup(
        backup,
        "operation cohort backup",
        expected_source_kind="approved-historical-backup",
    )
    if (
        checked_backup["source_authority"]["generation_before"]
        != checked_generation
    ):
        raise OperationRecoveryError(
            "operation cohort backup generation differs"
        )
    body = {
        "schema_version": 1,
        "kind": "operation-recovery-cohort",
        "profile_id": profile_id,
        "schema": schema,
        "bank_id": bank_id,
        "generation": checked_generation,
        "backup": checked_backup,
        "expected_operation_counts": dict(EXPECTED_OPERATION_COUNTS),
        "operations": operations,
        "created_at": (
            int(time.time())
            if created_at is None
            else _integer(created_at, "operation cohort created-at")
        ),
    }
    return {**body, "cohort_digest": digest(body)}


def verify_cohort_manifest(value: Any) -> Mapping[str, Any]:
    cohort = _closed(
        _normalized(value),
        COHORT_KEYS,
        "operation cohort manifest",
    )
    operations = cohort.get("operations")
    if (
        cohort.get("schema_version") != 1
        or cohort.get("kind") != "operation-recovery-cohort"
        or cohort.get("profile_id") != "systalyze"
        or cohort.get("schema") != "public"
        or cohort.get("bank_id") != "engineering"
        or cohort.get("expected_operation_counts") != EXPECTED_OPERATION_COUNTS
        or not isinstance(operations, list)
        or len(operations) != sum(EXPECTED_OPERATION_COUNTS.values())
    ):
        raise OperationRecoveryError("operation cohort manifest is invalid")
    checked_operations = [_cohort_operation(item) for item in operations]
    identifiers = [item["operation_id"] for item in checked_operations]
    counts = {
        operation_type: sum(
            item["operation_type"] == operation_type
            for item in checked_operations
        )
        for operation_type in EXPECTED_OPERATION_COUNTS
    }
    checked_generation = _text(
        cohort["generation"],
        "operation cohort generation",
    )
    checked_backup = _backup(
        cohort["backup"],
        "operation cohort backup",
        expected_source_kind="approved-historical-backup",
    )
    if (
        checked_backup["source_authority"]["generation_before"]
        != checked_generation
    ):
        raise OperationRecoveryError(
            "operation cohort backup generation differs"
        )
    body = {
        "schema_version": 1,
        "kind": "operation-recovery-cohort",
        "profile_id": cohort["profile_id"],
        "schema": cohort["schema"],
        "bank_id": cohort["bank_id"],
        "generation": checked_generation,
        "backup": checked_backup,
        "expected_operation_counts": dict(EXPECTED_OPERATION_COUNTS),
        "operations": checked_operations,
        "created_at": _integer(
            cohort["created_at"],
            "operation cohort created-at",
        ),
    }
    if (
        counts != EXPECTED_OPERATION_COUNTS
        or len(identifiers) != len(set(identifiers))
        or checked_operations
        != sorted(
            checked_operations,
            key=lambda item: (item["created_at"], item["operation_id"]),
        )
        or _sha(cohort["cohort_digest"], "operation cohort digest")
        != digest(body)
    ):
        raise OperationRecoveryError("operation cohort manifest digest differs")
    return {**body, "cohort_digest": cohort["cohort_digest"]}


def _data_identity_rebind_handoff(value: Any) -> dict[str, Any]:
    handoff = _closed(
        _normalized(value),
        DATA_IDENTITY_REBIND_HANDOFF_KEYS,
        "operation-recovery data-identity rebind handoff",
    )
    body = {
        "schema_version": _integer(
            handoff["schema_version"],
            "data-identity rebind handoff schema version",
        ),
        "kind": _text(
            handoff["kind"],
            "data-identity rebind handoff kind",
            maximum=128,
        ),
        "plan_digest": _sha(
            handoff["plan_digest"],
            "data-identity rebind plan digest",
        ),
        "authorization_receipt_digest": _sha(
            handoff["authorization_receipt_digest"],
            "data-identity rebind authorization receipt digest",
        ),
        "application_receipt_digest": _sha(
            handoff["application_receipt_digest"],
            "data-identity rebind application receipt digest",
        ),
        "verification_receipt_digest": _sha(
            handoff["verification_receipt_digest"],
            "data-identity rebind verification receipt digest",
        ),
        "rollback_bundle_digest": _sha(
            handoff["rollback_bundle_digest"],
            "data-identity rebind rollback bundle digest",
        ),
        "installation_state_digest_before": _sha(
            handoff["installation_state_digest_before"],
            "data-identity rebind pre-state digest",
        ),
        "installation_state_digest_after": _sha(
            handoff["installation_state_digest_after"],
            "data-identity rebind post-state digest",
        ),
        "binding_generation_digest": _sha(
            handoff["binding_generation_digest"],
            "data-identity rebind binding generation digest",
        ),
        "current_release_digest": _sha(
            handoff["current_release_digest"],
            "data-identity rebind current release digest",
        ),
        "old_data_identity_digest": _sha(
            handoff["old_data_identity_digest"],
            "data-identity rebind old identity digest",
        ),
        "reference_observed_data_identity_digest": _sha(
            handoff["reference_observed_data_identity_digest"],
            "data-identity rebind reference observed identity digest",
        ),
        "new_data_identity_digest": _sha(
            handoff["new_data_identity_digest"],
            "data-identity rebind new identity digest",
        ),
        "postgres_system_identifier": _text(
            handoff["postgres_system_identifier"],
            "data-identity rebind PostgreSQL system identifier",
            maximum=32,
        ),
        "database_continuity_digest": _sha(
            handoff["database_continuity_digest"],
            "data-identity rebind database continuity digest",
        ),
        "post_evidence_digest": _sha(
            handoff["post_evidence_digest"],
            "data-identity rebind post-evidence digest",
        ),
        "verified_at": _integer(
            handoff["verified_at"],
            "data-identity rebind verified-at",
        ),
    }
    handoff_digest = _sha(
        handoff["handoff_digest"],
        "data-identity rebind handoff digest",
    )
    if (
        body["schema_version"] != 1
        or body["kind"]
        != "operation-recovery-verified-data-identity-rebind-handoff"
        or body["old_data_identity_digest"]
        == body["new_data_identity_digest"]
        or body["verified_at"] < 0
        or handoff_digest != digest(body)
    ):
        raise OperationRecoveryError(
            "operation-recovery data-identity rebind handoff is invalid"
        )
    return {**body, "handoff_digest": handoff_digest}


def _installation_authority(value: Any) -> dict[str, Any]:
    normalized = _normalized(value)
    if not isinstance(normalized, Mapping):
        raise OperationRecoveryError(
            "operation-recovery installation authority must be an object"
        )
    is_v2 = set(normalized) == INSTALLATION_AUTHORITY_V2_KEYS
    authority = _closed(
        normalized,
        INSTALLATION_AUTHORITY_V2_KEYS if is_v2 else INSTALLATION_AUTHORITY_KEYS,
        "operation-recovery installation authority",
    )
    checked = {
        "consumer_id": _text(authority["consumer_id"], "consumer ID", maximum=128),
        "profile_id": _text(authority["profile_id"], "profile ID", maximum=128),
        "schema": _text(authority["schema"], "database schema", maximum=63),
        "bank_id": _text(authority["bank_id"], "bank ID", maximum=256),
        "install_state_digest": _sha(
            authority["install_state_digest"],
            "install-state digest",
        ),
        "binding_generation_digest": _sha(
            authority["binding_generation_digest"],
            "binding generation digest",
        ),
        "current_release_digest": _sha(
            authority["current_release_digest"],
            "current release digest",
        ),
        "installed_release_version": _text(
            authority["installed_release_version"],
            "installed release version",
            maximum=128,
        ),
        "recorded_data_identity_digest": _sha(
            authority["recorded_data_identity_digest"],
            "recorded data identity digest",
        ),
        "observed_data_identity_digest": _sha(
            authority["observed_data_identity_digest"],
            "observed data identity digest",
        ),
        "postgres_system_identifier": _text(
            authority["postgres_system_identifier"],
            "PostgreSQL system identifier",
            maximum=32,
        ),
    }
    if is_v2:
        rebind_handoff = _data_identity_rebind_handoff(
            authority["data_identity_rebind_handoff"]
        )
        checked = {
            "schema_version": _integer(
                authority["schema_version"],
                "installation authority schema version",
            ),
            **checked,
            "data_identity_rebind_handoff": rebind_handoff,
        }
    if (
        checked["profile_id"] != "systalyze"
        or checked["schema"] != "public"
        or checked["bank_id"] != "engineering"
        or VERSION.fullmatch(checked["installed_release_version"]) is None
        or (
            not is_v2
            and checked["recorded_data_identity_digest"]
            == checked["observed_data_identity_digest"]
        )
        or (
            is_v2
            and (
                checked["schema_version"] != 2
                or checked["recorded_data_identity_digest"]
                != checked["observed_data_identity_digest"]
                or checked["install_state_digest"]
                != rebind_handoff["installation_state_digest_after"]
                or checked["binding_generation_digest"]
                != rebind_handoff["binding_generation_digest"]
                or checked["current_release_digest"]
                != rebind_handoff["current_release_digest"]
                or checked["recorded_data_identity_digest"]
                != rebind_handoff["new_data_identity_digest"]
                or checked["postgres_system_identifier"]
                != rebind_handoff["postgres_system_identifier"]
            )
        )
    ):
        raise OperationRecoveryError(
            "operation-recovery installation authority is invalid"
        )
    return checked


def _post_abort_installation_authority_matches(
    reference: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    schema_version: int,
) -> bool:
    if current == reference:
        return True
    if schema_version != 11 or current.get("schema_version") != 2:
        return False
    handoff = current.get("data_identity_rebind_handoff")
    if not isinstance(handoff, Mapping):
        return False
    return (
        all(
            reference[key] == current[key]
            for key in (
                "consumer_id",
                "profile_id",
                "schema",
                "bank_id",
                "binding_generation_digest",
                "installed_release_version",
                "current_release_digest",
                "postgres_system_identifier",
            )
        )
        and reference["install_state_digest"]
        == handoff["installation_state_digest_before"]
        and current["install_state_digest"]
        == handoff["installation_state_digest_after"]
        and reference["binding_generation_digest"]
        == handoff["binding_generation_digest"]
        and reference["current_release_digest"]
        == handoff["current_release_digest"]
        and reference["recorded_data_identity_digest"]
        == handoff["old_data_identity_digest"]
        and reference["observed_data_identity_digest"]
        == handoff["reference_observed_data_identity_digest"]
        and current["recorded_data_identity_digest"]
        == handoff["new_data_identity_digest"]
        and current["observed_data_identity_digest"]
        == handoff["new_data_identity_digest"]
        and reference["postgres_system_identifier"]
        == handoff["postgres_system_identifier"]
    )


def _assert_installation_authority_schema(
    authority: Mapping[str, Any],
    *,
    plan_schema_version: int,
) -> None:
    if "schema_version" in authority and plan_schema_version not in {11, 12, 13, 14}:
        raise OperationRecoveryError(
            "operation-recovery verified rebind authority requires "
            "schema 11"
        )


def _live_operation(value: Any) -> dict[str, Any]:
    item = _closed(
        _normalized(value),
        LIVE_OPERATION_KEYS,
        "live operation entry",
    )
    body = {
        "operation_id": _operation_id(item["operation_id"]),
        "operation_type": _text(
            item["operation_type"],
            "operation type",
            maximum=128,
        ),
        "current_status": _text(
            item["current_status"],
            "operation current status",
            maximum=32,
        ),
        "created_at": _text(item["created_at"], "operation created-at"),
        "updated_at": _text(item["updated_at"], "operation updated-at"),
        "completed_at": _optional_text(
            item["completed_at"],
            "operation completed-at",
        ),
        "retry_count": _integer(item["retry_count"], "operation retry count"),
        "next_retry_at": _optional_text(
            item["next_retry_at"],
            "operation next-retry-at",
        ),
        "worker_id_present": item["worker_id_present"],
        "worker_id_digest": item["worker_id_digest"],
        "claimed_at": _optional_text(item["claimed_at"], "operation claimed-at"),
        "task_payload_present": item["task_payload_present"],
        "task_payload_digest": item["task_payload_digest"],
        "result_metadata_digest": _sha(
            item["result_metadata_digest"],
            "operation result metadata digest",
        ),
        "error_category": _text(
            item["error_category"],
            "operation error category",
            maximum=64,
        ),
        "error_digest": item["error_digest"],
    }
    if (
        body["operation_type"] not in EXPECTED_OPERATION_COUNTS
        or body["current_status"] not in OPERATION_STATUSES
        or type(body["worker_id_present"]) is not bool
        or body["task_payload_present"] is not True
        or body["error_category"] not in ERROR_CATEGORIES
    ):
        raise OperationRecoveryError("live operation entry is invalid")
    if body["worker_id_present"]:
        body["worker_id_digest"] = _sha(
            body["worker_id_digest"],
            "operation worker ID digest",
        )
    elif body["worker_id_digest"] is not None:
        raise OperationRecoveryError("live operation worker evidence is invalid")
    body["task_payload_digest"] = _sha(
        body["task_payload_digest"],
        "operation payload digest",
    )
    if body["error_category"] == "none":
        if body["error_digest"] is not None:
            raise OperationRecoveryError("live operation error evidence is invalid")
    else:
        body["error_digest"] = _sha(
            body["error_digest"],
            "operation error digest",
        )
    if _sha(item["row_digest"], "live operation row digest") != digest(body):
        raise OperationRecoveryError("live operation row digest differs")
    return {**body, "row_digest": item["row_digest"]}


def create_live_snapshot(
    cohort_value: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    *,
    generation_before: str,
    generation_after: str,
    installation_authority: Mapping[str, Any],
    observed_at: int | None = None,
) -> Mapping[str, Any]:
    """Classify the frozen cohort from one generation-stable live snapshot."""
    cohort = verify_cohort_manifest(cohort_value)
    if generation_before != generation_after:
        raise OperationRecoveryError(
            "operation-recovery generation changed during live snapshot"
        )
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise OperationRecoveryError("live operation evidence is invalid")
    safe_rows = [_safe_row(row) for row in rows]
    cohort_by_id = {
        item["operation_id"]: item for item in cohort["operations"]
    }
    if (
        len(safe_rows) != len(cohort_by_id)
        or {item["operation_id"] for item in safe_rows} != set(cohort_by_id)
    ):
        raise OperationRecoveryError("live snapshot does not cover exact cohort")
    operations = []
    for row in safe_rows:
        baseline = cohort_by_id[row["operation_id"]]
        if (
            row["bank_id"] != cohort["bank_id"]
            or row["operation_type"] != baseline["operation_type"]
            or row["task_payload_present"] is not True
            or row["task_payload_digest"] != baseline["task_payload_digest"]
        ):
            raise OperationRecoveryError(
                "live operation identity or payload digest differs"
            )
        body = {
            "operation_id": row["operation_id"],
            "operation_type": row["operation_type"],
            "current_status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
            "retry_count": row["retry_count"],
            "next_retry_at": row["next_retry_at"],
            "worker_id_present": row["worker_id_present"],
            "worker_id_digest": row["worker_id_digest"],
            "claimed_at": row["claimed_at"],
            "task_payload_present": row["task_payload_present"],
            "task_payload_digest": row["task_payload_digest"],
            "result_metadata_digest": row["result_metadata_digest"],
            "error_category": row["error_category"],
            "error_digest": row["error_digest"],
        }
        operations.append({**body, "row_digest": digest(body)})
    operations.sort(key=lambda item: item["operation_id"])
    counts = {
        status: sum(item["current_status"] == status for item in operations)
        for status in OPERATION_STATUSES
    }
    body = {
        "schema_version": 1,
        "kind": "operation-recovery-live-snapshot",
        "cohort_digest": cohort["cohort_digest"],
        "installation_authority": _installation_authority(
            installation_authority
        ),
        "generation_before": _text(
            generation_before,
            "live snapshot generation",
        ),
        "generation_after": _text(
            generation_after,
            "live snapshot generation",
        ),
        "status_counts": counts,
        "operations": operations,
        "observed_at": (
            int(time.time())
            if observed_at is None
            else _integer(observed_at, "live snapshot observed-at")
        ),
    }
    return {**body, "snapshot_digest": digest(body)}


def verify_live_snapshot(value: Any) -> Mapping[str, Any]:
    snapshot = _closed(
        _normalized(value),
        LIVE_SNAPSHOT_KEYS,
        "operation-recovery live snapshot",
    )
    operations = snapshot.get("operations")
    if (
        snapshot.get("schema_version") != 1
        or snapshot.get("kind") != "operation-recovery-live-snapshot"
        or not isinstance(operations, list)
        or len(operations) != sum(EXPECTED_OPERATION_COUNTS.values())
        or snapshot.get("generation_before") != snapshot.get("generation_after")
    ):
        raise OperationRecoveryError(
            "operation-recovery live snapshot is invalid"
        )
    checked_operations = [_live_operation(item) for item in operations]
    identifiers = [item["operation_id"] for item in checked_operations]
    status_counts = {
        status: sum(
            item["current_status"] == status for item in checked_operations
        )
        for status in OPERATION_STATUSES
    }
    body = {
        "schema_version": 1,
        "kind": "operation-recovery-live-snapshot",
        "cohort_digest": _sha(
            snapshot["cohort_digest"],
            "operation cohort digest",
        ),
        "installation_authority": _installation_authority(
            snapshot["installation_authority"]
        ),
        "generation_before": _text(
            snapshot["generation_before"],
            "live snapshot generation",
        ),
        "generation_after": _text(
            snapshot["generation_after"],
            "live snapshot generation",
        ),
        "status_counts": status_counts,
        "operations": checked_operations,
        "observed_at": _integer(
            snapshot["observed_at"],
            "live snapshot observed-at",
        ),
    }
    if (
        snapshot.get("status_counts") != status_counts
        or len(identifiers) != len(set(identifiers))
        or checked_operations
        != sorted(checked_operations, key=lambda item: item["operation_id"])
        or _sha(snapshot["snapshot_digest"], "live snapshot digest")
        != digest(body)
    ):
        raise OperationRecoveryError(
            "operation-recovery live snapshot digest differs"
        )
    return {**body, "snapshot_digest": snapshot["snapshot_digest"]}


def _candidate_release(value: Any) -> dict[str, str]:
    release = _closed(
        _normalized(value),
        RELEASE_IDENTITY_KEYS,
        "operation-recovery candidate release",
    )
    source_commit = _text(
        release["source_commit"],
        "candidate source commit",
        maximum=40,
    )
    version = _text(release["version"], "candidate version", maximum=128)
    if (
        SOURCE_COMMIT.fullmatch(source_commit) is None
        or VERSION.fullmatch(version) is None
    ):
        raise OperationRecoveryError(
            "operation-recovery candidate release is invalid"
        )
    return {
        "source_commit": source_commit,
        "version": version,
        "release_digest": _sha(
            release["release_digest"],
            "candidate release digest",
        ),
    }


def _checkpoint_continuation_checkpoint(value: Any) -> dict[str, Any]:
    checkpoint = _closed(
        _normalized(value),
        CHECKPOINT_CONTINUATION_CHECKPOINT_KEYS,
        "checkpoint continuation checkpoint",
    )
    facts_committed = checkpoint["facts_committed"]
    if type(facts_committed) is not bool or not facts_committed:
        raise OperationRecoveryError(
            "checkpoint continuation requires committed facts"
        )
    body = {
        "facts_committed": facts_committed,
        "committed_document_count": _integer(
            checkpoint["committed_document_count"],
            "checkpoint continuation committed document count",
            minimum=1,
        ),
        "unit_ids_count": _integer(
            checkpoint["unit_ids_count"],
            "checkpoint continuation unit count",
            minimum=1,
        ),
        "stage": _text(
            checkpoint["stage"],
            "checkpoint continuation stage",
            maximum=128,
        ),
        "processed": _integer(
            checkpoint["processed"],
            "checkpoint continuation processed count",
        ),
        "total": _integer(
            checkpoint["total"],
            "checkpoint continuation total count",
            minimum=1,
        ),
    }
    if body["processed"] > body["total"]:
        raise OperationRecoveryError(
            "checkpoint continuation progress is invalid"
        )
    return body


def _checkpoint_continuation_operation(value: Any) -> dict[str, Any]:
    operation = _closed(
        _normalized(value),
        CHECKPOINT_CONTINUATION_OPERATION_KEYS,
        "checkpoint continuation operation",
    )
    body = {
        "operation_id": _operation_id(operation["operation_id"]),
        "operation_type": _text(
            operation["operation_type"],
            "checkpoint continuation operation type",
            maximum=128,
        ),
        "current_status": _text(
            operation["current_status"],
            "checkpoint continuation operation status",
            maximum=32,
        ),
        "row_digest": _sha(
            operation["row_digest"],
            "checkpoint continuation row digest",
        ),
        "task_payload_digest": _sha(
            operation["task_payload_digest"],
            "checkpoint continuation payload digest",
        ),
        "result_metadata_digest": _sha(
            operation["result_metadata_digest"],
            "checkpoint continuation metadata digest",
        ),
        "checkpoint": _checkpoint_continuation_checkpoint(
            operation["checkpoint"]
        ),
        "retry_count": _integer(
            operation["retry_count"],
            "checkpoint continuation retry count",
        ),
        "attempts_consumed": _integer(
            operation["attempts_consumed"],
            "checkpoint continuation consumed attempts",
            minimum=1,
        ),
        "attempts_remaining": _integer(
            operation["attempts_remaining"],
            "checkpoint continuation remaining attempts",
            minimum=1,
        ),
        "worker_id_present": operation["worker_id_present"],
        "worker_id_digest": operation["worker_id_digest"],
        "claimed_at": _optional_text(
            operation["claimed_at"],
            "checkpoint continuation claimed-at",
        ),
        "next_retry_at": _optional_text(
            operation["next_retry_at"],
            "checkpoint continuation next-retry-at",
        ),
        "error_category": _text(
            operation["error_category"],
            "checkpoint continuation error category",
            maximum=64,
        ),
        "error_digest": operation["error_digest"],
    }
    if (
        body["operation_type"] != "retain"
        or body["current_status"] != "pending"
        or body["retry_count"] > EXACT_DRAIN_WORKER_MAX_RETRIES
        or type(body["worker_id_present"]) is not bool
        or body["worker_id_present"]
        or body["worker_id_digest"] is not None
        or body["claimed_at"] is not None
        or body["next_retry_at"] is not None
        or body["error_category"] != "none"
        or body["error_digest"] is not None
        or body["attempts_consumed"] + body["attempts_remaining"]
        > CHECKPOINT_CONTINUATION_MAX_CUMULATIVE_ATTEMPTS
        or body["attempts_remaining"] < EXACT_DRAIN_WORKER_MAX_ATTEMPTS
    ):
        raise OperationRecoveryError(
            "checkpoint continuation operation state is invalid"
        )
    return body


def _checkpoint_continuation_audit(value: Any) -> dict[str, Any]:
    audit = _closed(
        _normalized(value),
        CHECKPOINT_CONTINUATION_AUDIT_KEYS,
        "checkpoint continuation side-effect audit",
    )
    body = {
        "schema_version": _integer(
            audit["schema_version"],
            "checkpoint continuation audit schema version",
        ),
        "kind": _text(
            audit["kind"],
            "checkpoint continuation audit kind",
        ),
        "operation_id": _operation_id(audit["operation_id"]),
        "generation": _text(
            audit["generation"],
            "checkpoint continuation audit generation",
        ),
        "row_digest": _sha(
            audit["row_digest"],
            "checkpoint continuation audit row digest",
        ),
        "result_metadata_digest": _sha(
            audit["result_metadata_digest"],
            "checkpoint continuation audit metadata digest",
        ),
        "checkpoint": _checkpoint_continuation_checkpoint(
            audit["checkpoint"]
        ),
        "document_count": _integer(
            audit["document_count"],
            "checkpoint continuation audit document count",
            minimum=1,
        ),
        "unit_count": _integer(
            audit["unit_count"],
            "checkpoint continuation audit unit count",
            minimum=1,
        ),
        "document_set_digest": _sha(
            audit["document_set_digest"],
            "checkpoint continuation document set digest",
        ),
        "unit_set_digest": _sha(
            audit["unit_set_digest"],
            "checkpoint continuation unit set digest",
        ),
        "idempotent_resume": audit["idempotent_resume"],
    }
    if (
        body["schema_version"] != 1
        or body["kind"]
        != "operation-recovery-checkpoint-continuation-side-effect-audit"
        or type(body["idempotent_resume"]) is not bool
        or not body["idempotent_resume"]
        or body["document_count"]
        != body["checkpoint"]["committed_document_count"]
        # The checkpoint count is per attempt.  A retry can reuse a document
        # id that already owns facts from an earlier successful attempt, so
        # the durable count is a lower bound, not an equality requirement.
        or body["unit_count"] < body["checkpoint"]["unit_ids_count"]
    ):
        raise OperationRecoveryError(
            "checkpoint continuation side-effect audit is invalid"
        )
    audit_digest = _sha(
        audit["audit_digest"],
        "checkpoint continuation audit digest",
    )
    if audit_digest != digest(body):
        raise OperationRecoveryError(
            "checkpoint continuation audit digest differs"
        )
    return {**body, "audit_digest": audit_digest}


def _checkpoint_continuation_context(value: Any) -> dict[str, Any]:
    context = _closed(
        _normalized(value),
        CHECKPOINT_CONTINUATION_CONTEXT_KEYS,
        "checkpoint continuation context",
    )
    body = {
        "schema_version": _integer(
            context["schema_version"],
            "checkpoint continuation context schema version",
        ),
        "kind": _text(
            context["kind"],
            "checkpoint continuation context kind",
        ),
        "origin": _text(
            context["origin"],
            "checkpoint continuation context origin",
        ),
        "generation": _text(
            context["generation"],
            "checkpoint continuation context generation",
        ),
        "recovery_epoch": _integer(
            context["recovery_epoch"],
            "checkpoint continuation context recovery epoch",
        ),
        "reconciliation_cycle": _integer(
            context["reconciliation_cycle"],
            "checkpoint continuation context reconciliation cycle",
        ),
        "source_plan_digest": _sha(
            context["source_plan_digest"],
            "checkpoint continuation source plan digest",
        ),
        "source_recovery_context_digest": _sha(
            context["source_recovery_context_digest"],
            "checkpoint continuation source recovery context digest",
        ),
        "source_reconciliation_plan_digest": _sha(
            context["source_reconciliation_plan_digest"],
            "checkpoint continuation source reconciliation plan digest",
        ),
        "source_terminal_status_digest": _sha(
            context["source_terminal_status_digest"],
            "checkpoint continuation source terminal status digest",
        ),
        "source_candidate_release_digest": _sha(
            context["source_candidate_release_digest"],
            "checkpoint continuation source candidate release digest",
        ),
        "candidate_release_digest": _sha(
            context["candidate_release_digest"],
            "checkpoint continuation candidate release digest",
        ),
        "selected_operation_ids_digest": _sha(
            context["selected_operation_ids_digest"],
            "checkpoint continuation operation IDs digest",
        ),
        "selected_checkpoint_set_digest": _sha(
            context["selected_checkpoint_set_digest"],
            "checkpoint continuation checkpoint set digest",
        ),
        "side_effect_audit_digest": _sha(
            context["side_effect_audit_digest"],
            "checkpoint continuation side-effect digest",
        ),
    }
    if (
        body["schema_version"] != 1
        or body["kind"]
        != "operation-recovery-checkpoint-continuation-context"
        or body["origin"] != "committed-checkpoint"
        or body["recovery_epoch"] != 3
        or body["reconciliation_cycle"] != 1
    ):
        raise OperationRecoveryError(
            "checkpoint continuation context is invalid"
        )
    context_digest = _sha(
        context["context_digest"],
        "checkpoint continuation context digest",
    )
    if context_digest != digest(body):
        raise OperationRecoveryError(
            "checkpoint continuation context digest differs"
        )
    return {**body, "context_digest": context_digest}


def _checkpoint_continuation_selected_checkpoint_digest(
    operations: Sequence[Mapping[str, Any]],
) -> str:
    return digest(
        [
            {
                "operation_id": operation["operation_id"],
                "row_digest": operation["row_digest"],
                "result_metadata_digest": operation[
                    "result_metadata_digest"
                ],
                "checkpoint_digest": digest(operation["checkpoint"]),
            }
            for operation in operations
        ]
    )


def _checkpoint_continuation_attempt_ledger_digest(
    operations: Sequence[Mapping[str, Any]],
) -> str:
    return digest(
        [
            {
                "operation_id": operation["operation_id"],
                "attempts_consumed": operation["attempts_consumed"],
                "attempts_remaining": operation["attempts_remaining"],
            }
            for operation in operations
        ]
    )


def _checkpoint_continuation_audit_digest(
    audits: Sequence[Mapping[str, Any]],
) -> str:
    return digest(list(audits))


def create_checkpoint_continuation_handoff(
    live_snapshot_value: Mapping[str, Any],
    *,
    continuation_operations: Sequence[Mapping[str, Any]],
    side_effect_audits: Sequence[Mapping[str, Any]],
    source_plan_digest: str,
    source_recovery_context_digest: str,
    source_reconciliation_plan_digest: str,
    source_terminal_status_digest: str,
    source_candidate_release: Mapping[str, Any],
    candidate_release: Mapping[str, Any],
    generation: str,
    created_at: int | None = None,
) -> Mapping[str, Any]:
    """Build a payload-free proof for a future checkpoint continuation.

    This function only validates supplied snapshots and digest projections.  It
    never changes operation rows, claims work, resets retry state, or starts a
    worker.  Callers must source the checkpoint and side-effect projections
    from an authenticated payload-free verifier; this constructor does not
    infer them from arbitrary metadata.  A future executable schema must
    separately consume this handoff and implement the idempotent runtime path.
    """
    snapshot = verify_live_snapshot(live_snapshot_value)
    source_release = _candidate_release(source_candidate_release)
    release = _candidate_release(candidate_release)
    generation_value = _text(
        generation,
        "checkpoint continuation generation",
    )
    if (
        generation_value != snapshot["generation_before"]
        or generation_value != snapshot["generation_after"]
    ):
        raise OperationRecoveryError(
            "checkpoint continuation generation differs"
        )
    if isinstance(continuation_operations, (str, bytes)) or not isinstance(
        continuation_operations, Sequence
    ):
        raise OperationRecoveryError(
            "checkpoint continuation operations are invalid"
        )
    if isinstance(side_effect_audits, (str, bytes)) or not isinstance(
        side_effect_audits, Sequence
    ):
        raise OperationRecoveryError(
            "checkpoint continuation audits are invalid"
        )
    operations = [
        _checkpoint_continuation_operation(item)
        for item in continuation_operations
    ]
    audits = [
        _checkpoint_continuation_audit(item)
        for item in side_effect_audits
    ]
    operations.sort(key=lambda item: item["operation_id"])
    audits.sort(key=lambda item: item["operation_id"])
    operation_ids = [item["operation_id"] for item in operations]
    audit_ids = [item["operation_id"] for item in audits]
    if (
        not operations
        or len(operations) > sum(EXPECTED_OPERATION_COUNTS.values())
        or len(operation_ids) != len(set(operation_ids))
        or audit_ids != operation_ids
        or len(audit_ids) != len(set(audit_ids))
    ):
        raise OperationRecoveryError(
            "checkpoint continuation operation set is invalid"
        )
    snapshot_by_id = {
        item["operation_id"]: item for item in snapshot["operations"]
    }
    for operation in operations:
        row = snapshot_by_id.get(operation["operation_id"])
        if row is None:
            raise OperationRecoveryError(
                "checkpoint continuation operation is outside snapshot"
            )
        if any(
            operation[key] != row[row_key]
            for key, row_key in (
                ("operation_type", "operation_type"),
                ("current_status", "current_status"),
                ("row_digest", "row_digest"),
                ("task_payload_digest", "task_payload_digest"),
                ("result_metadata_digest", "result_metadata_digest"),
                ("retry_count", "retry_count"),
                ("worker_id_present", "worker_id_present"),
                ("worker_id_digest", "worker_id_digest"),
                ("claimed_at", "claimed_at"),
                ("next_retry_at", "next_retry_at"),
                ("error_category", "error_category"),
                ("error_digest", "error_digest"),
            )
        ):
            raise OperationRecoveryError(
                "checkpoint continuation operation differs from snapshot"
            )
    audits_by_id = {item["operation_id"]: item for item in audits}
    for operation in operations:
        audit = audits_by_id[operation["operation_id"]]
        if (
            audit["generation"] != generation_value
            or audit["row_digest"] != operation["row_digest"]
            or audit["result_metadata_digest"]
            != operation["result_metadata_digest"]
            or audit["checkpoint"] != operation["checkpoint"]
        ):
            raise OperationRecoveryError(
                "checkpoint continuation audit differs from operation"
            )
    source_digests = {
        "source_plan_digest": _sha(
            source_plan_digest,
            "checkpoint continuation source plan digest",
        ),
        "source_recovery_context_digest": _sha(
            source_recovery_context_digest,
            "checkpoint continuation source recovery context digest",
        ),
        "source_reconciliation_plan_digest": _sha(
            source_reconciliation_plan_digest,
            "checkpoint continuation source reconciliation plan digest",
        ),
        "source_terminal_status_digest": _sha(
            source_terminal_status_digest,
            "checkpoint continuation source terminal status digest",
        ),
    }
    selected_operation_ids_digest = digest(operation_ids)
    selected_checkpoint_set_digest = (
        _checkpoint_continuation_selected_checkpoint_digest(operations)
    )
    side_effect_audit_digest = _checkpoint_continuation_audit_digest(audits)
    context_body = {
        "schema_version": 1,
        "kind": "operation-recovery-checkpoint-continuation-context",
        "origin": "committed-checkpoint",
        "generation": generation_value,
        "recovery_epoch": 3,
        "reconciliation_cycle": 1,
        **source_digests,
        "source_candidate_release_digest": source_release["release_digest"],
        "candidate_release_digest": release["release_digest"],
        "selected_operation_ids_digest": selected_operation_ids_digest,
        "selected_checkpoint_set_digest": selected_checkpoint_set_digest,
        "side_effect_audit_digest": side_effect_audit_digest,
    }
    context = {**context_body, "context_digest": digest(context_body)}
    planned_at = (
        int(time.time())
        if created_at is None
        else _integer(
            created_at,
            "checkpoint continuation handoff created-at",
        )
    )
    body = {
        "schema_version": 1,
        "kind": "operation-recovery-checkpoint-continuation-handoff",
        "action": "resume-committed-checkpointed-operations",
        "authority": "unapproved-plan",
        "mutation_authorized": False,
        "continuation_context": context,
        "continuation_context_digest": context["context_digest"],
        "source_candidate_release": source_release,
        "candidate_release": release,
        "operations": operations,
        "operation_count": len(operations),
        "side_effect_audit": audits,
        "side_effect_audit_digest": side_effect_audit_digest,
        "attempt_ledger_digest": _checkpoint_continuation_attempt_ledger_digest(
            operations
        ),
        "created_at": planned_at,
        "expires_at": planned_at + CHECKPOINT_CONTINUATION_LIFETIME_SECONDS,
    }
    return {**body, "handoff_digest": digest(body)}


def verify_checkpoint_continuation_handoff(
    value: Any,
    *,
    live_snapshot: Mapping[str, Any] | None = None,
    now: int | None = None,
    allow_expired: bool = False,
) -> Mapping[str, Any]:
    """Verify a checkpoint handoff without applying it or touching the DB."""
    handoff = _closed(
        _normalized(value),
        CHECKPOINT_CONTINUATION_HANDOFF_KEYS,
        "checkpoint continuation handoff",
    )
    context = _checkpoint_continuation_context(handoff["continuation_context"])
    source_release = _candidate_release(handoff["source_candidate_release"])
    release = _candidate_release(handoff["candidate_release"])
    if (
        _sha(
            handoff["continuation_context_digest"],
            "checkpoint continuation context digest",
        )
        != context["context_digest"]
        or context["source_candidate_release_digest"]
        != source_release["release_digest"]
        or context["candidate_release_digest"] != release["release_digest"]
    ):
        raise OperationRecoveryError(
            "checkpoint continuation context binding differs"
        )
    operations_value = handoff["operations"]
    audits_value = handoff["side_effect_audit"]
    if not isinstance(operations_value, list) or not isinstance(
        audits_value, list
    ):
        raise OperationRecoveryError(
            "checkpoint continuation handoff entries are invalid"
        )
    operations = [
        _checkpoint_continuation_operation(item) for item in operations_value
    ]
    audits = [_checkpoint_continuation_audit(item) for item in audits_value]
    operation_ids = [item["operation_id"] for item in operations]
    audit_ids = [item["operation_id"] for item in audits]
    if (
        not operations
        or len(operations) > sum(EXPECTED_OPERATION_COUNTS.values())
        or operations != sorted(operations, key=lambda item: item["operation_id"])
        or audits != sorted(audits, key=lambda item: item["operation_id"])
        or len(operation_ids) != len(set(operation_ids))
        or audit_ids != operation_ids
        or len(audit_ids) != len(set(audit_ids))
        or _integer(
            handoff["operation_count"],
            "checkpoint continuation operation count",
        )
        != len(operations)
    ):
        raise OperationRecoveryError(
            "checkpoint continuation handoff operation set is invalid"
        )
    audits_by_id = {item["operation_id"]: item for item in audits}
    for operation in operations:
        audit = audits_by_id[operation["operation_id"]]
        if (
            audit["generation"] != context["generation"]
            or audit["row_digest"] != operation["row_digest"]
            or audit["result_metadata_digest"]
            != operation["result_metadata_digest"]
            or audit["checkpoint"] != operation["checkpoint"]
        ):
            raise OperationRecoveryError(
                "checkpoint continuation handoff audit differs"
            )
    side_effect_audit_digest = _checkpoint_continuation_audit_digest(audits)
    body = {
        "schema_version": 1,
        "kind": "operation-recovery-checkpoint-continuation-handoff",
        "action": "resume-committed-checkpointed-operations",
        "authority": "unapproved-plan",
        "mutation_authorized": False,
        "continuation_context": context,
        "continuation_context_digest": context["context_digest"],
        "source_candidate_release": source_release,
        "candidate_release": release,
        "operations": operations,
        "operation_count": len(operations),
        "side_effect_audit": audits,
        "side_effect_audit_digest": side_effect_audit_digest,
        "attempt_ledger_digest": _checkpoint_continuation_attempt_ledger_digest(
            operations
        ),
        "created_at": _integer(
            handoff["created_at"],
            "checkpoint continuation handoff created-at",
        ),
        "expires_at": _integer(
            handoff["expires_at"],
            "checkpoint continuation handoff expires-at",
        ),
    }
    if (
        handoff["schema_version"] != 1
        or body["authority"] != handoff["authority"]
        or handoff["action"] != body["action"]
        or handoff["kind"] != body["kind"]
        or type(handoff["mutation_authorized"]) is not bool
        or handoff["mutation_authorized"]
        or body["expires_at"]
        != body["created_at"] + CHECKPOINT_CONTINUATION_LIFETIME_SECONDS
        or context["selected_operation_ids_digest"] != digest(operation_ids)
        or context["selected_checkpoint_set_digest"]
        != _checkpoint_continuation_selected_checkpoint_digest(operations)
        or context["side_effect_audit_digest"] != side_effect_audit_digest
        or _sha(
            handoff["side_effect_audit_digest"],
            "checkpoint continuation side-effect digest",
        )
        != side_effect_audit_digest
        or _sha(
            handoff["attempt_ledger_digest"],
            "checkpoint continuation attempt ledger digest",
        )
        != body["attempt_ledger_digest"]
        or _sha(
            handoff["handoff_digest"],
            "checkpoint continuation handoff digest",
        )
        != digest(body)
    ):
        raise OperationRecoveryError(
            "checkpoint continuation handoff digest or policy differs"
        )
    observed_at = (
        int(time.time())
        if now is None
        else _integer(now, "checkpoint continuation verification time")
    )
    if not allow_expired and observed_at >= body["expires_at"]:
        raise OperationRecoveryError(
            "checkpoint continuation handoff expired"
        )
    if live_snapshot is not None:
        snapshot = verify_live_snapshot(live_snapshot)
        if snapshot["generation_before"] != context["generation"]:
            raise OperationRecoveryError(
                "checkpoint continuation live generation differs"
            )
        snapshot_by_id = {
            item["operation_id"]: item for item in snapshot["operations"]
        }
        for operation in operations:
            row = snapshot_by_id.get(operation["operation_id"])
            if row is None or any(
                operation[key] != row[row_key]
                for key, row_key in (
                    ("operation_type", "operation_type"),
                    ("current_status", "current_status"),
                    ("row_digest", "row_digest"),
                    ("task_payload_digest", "task_payload_digest"),
                    ("result_metadata_digest", "result_metadata_digest"),
                    ("retry_count", "retry_count"),
                    ("worker_id_present", "worker_id_present"),
                    ("worker_id_digest", "worker_id_digest"),
                    ("claimed_at", "claimed_at"),
                    ("next_retry_at", "next_retry_at"),
                    ("error_category", "error_category"),
                    ("error_digest", "error_digest"),
                )
            ):
                raise OperationRecoveryError(
                    "checkpoint continuation live snapshot differs"
                )
    return {**body, "handoff_digest": handoff["handoff_digest"]}


def create_requeue_plan(
    cohort_value: Mapping[str, Any],
    live_snapshot_value: Mapping[str, Any],
    *,
    candidate_release: Mapping[str, Any],
    rollback_backup: Mapping[str, Any],
    rollback_encryption: Mapping[str, Any],
    rollback_backup_path: str,
    rollback_bundle_path: str,
    authorization_receipt_path: str,
    application_receipt_path: str,
    verification_receipt_path: str,
    rollback_receipt_path: str,
    created_at: int | None = None,
) -> Mapping[str, Any]:
    """Build an expiring plan; never requeue or start a worker."""
    cohort = verify_cohort_manifest(cohort_value)
    snapshot = verify_live_snapshot(live_snapshot_value)
    if snapshot["cohort_digest"] != cohort["cohort_digest"]:
        raise OperationRecoveryError("live snapshot cohort digest differs")
    if snapshot["status_counts"]["processing"]:
        raise OperationRecoveryError(
            "operation-recovery plan refuses processing operations"
        )
    cohort_by_id = {
        item["operation_id"]: item for item in cohort["operations"]
    }
    snapshot_ids = {
        item["operation_id"] for item in snapshot["operations"]
    }
    if (
        set(cohort_by_id) != snapshot_ids
        or any(
            item["operation_type"]
            != cohort_by_id[item["operation_id"]]["operation_type"]
            or item["task_payload_digest"]
            != cohort_by_id[item["operation_id"]]["task_payload_digest"]
            for item in snapshot["operations"]
        )
    ):
        raise OperationRecoveryError("live snapshot does not cover exact cohort")
    selected = [
        {
            "operation_id": item["operation_id"],
            "operation_type": item["operation_type"],
            "expected_status": item["current_status"],
            "row_digest": item["row_digest"],
            "task_payload_digest": item["task_payload_digest"],
        }
        for item in snapshot["operations"]
        if item["current_status"] in {"failed", "cancelled"}
    ]
    if not selected:
        raise OperationRecoveryError(
            "operation-recovery plan has no retryable operations"
        )
    authority = snapshot["installation_authority"]
    _assert_installation_authority_schema(
        authority,
        plan_schema_version=1,
    )
    backup = _backup(
        rollback_backup,
        "operation-recovery rollback backup",
        expected_source_kind="verified-live-pg0-backup",
    )
    encryption = _rollback_encryption(rollback_encryption)
    source_authority = backup["source_authority"]
    if (
        backup["postgres_system_identifier"]
        != authority["postgres_system_identifier"]
        or source_authority["data_identity_digest"]
        != authority["observed_data_identity_digest"]
        or source_authority["generation_before"]
        != snapshot["generation_before"]
    ):
        raise OperationRecoveryError(
            "operation-recovery rollback backup identity differs"
        )
    planned_at = (
        int(time.time())
        if created_at is None
        else _integer(created_at, "operation-recovery plan created-at")
    )
    artifact_paths = {
        "rollback_backup_path": _absolute_path(
            rollback_backup_path,
            "operation-recovery rollback backup path",
        ),
        "rollback_bundle_path": _absolute_path(
            rollback_bundle_path,
            "operation-recovery rollback bundle path",
        ),
        "authorization_receipt_path": _absolute_path(
            authorization_receipt_path,
            "operation-recovery authorization receipt path",
        ),
        "application_receipt_path": _absolute_path(
            application_receipt_path,
            "operation-recovery application receipt path",
        ),
        "verification_receipt_path": _absolute_path(
            verification_receipt_path,
            "operation-recovery verification receipt path",
        ),
        "rollback_receipt_path": _absolute_path(
            rollback_receipt_path,
            "operation-recovery rollback receipt path",
        ),
    }
    if (
        len(
            {
                unicodedata.normalize("NFD", value.casefold())
                for value in artifact_paths.values()
            }
        )
        != len(artifact_paths)
    ):
        raise OperationRecoveryError(
            "operation-recovery artifact paths must be distinct"
        )
    body = {
        "schema_version": 1,
        "action": "requeue-operation-cohort",
        "candidate_release": _candidate_release(candidate_release),
        "installation_authority": authority,
        "cohort": cohort,
        "live_snapshot": snapshot,
        "cohort_digest": cohort["cohort_digest"],
        "snapshot_digest": snapshot["snapshot_digest"],
        "cohort_backup_artifact_digest": cohort["backup"]["artifact_sha256"],
        "rollback_backup": backup,
        "rollback_encryption": encryption,
        "pre_generation": snapshot["generation_before"],
        "policy": dict(POLICY),
        "selected_operations": selected,
        "selected_operation_count": len(selected),
        "preserved_status_counts": {
            "pending": snapshot["status_counts"]["pending"],
            "completed": snapshot["status_counts"]["completed"],
        },
        **artifact_paths,
        "created_at": planned_at,
        "expires_at": planned_at + MAX_PLAN_LIFETIME_SECONDS,
    }
    return {**body, "plan_digest": digest(body)}


def verify_requeue_plan(
    value: Any,
    *,
    now: int | None = None,
    allow_expired: bool = False,
) -> Mapping[str, Any]:
    plan = _closed(
        _normalized(value),
        PLAN_KEYS,
        "operation-recovery requeue plan",
    )
    cohort = verify_cohort_manifest(plan["cohort"])
    snapshot = verify_live_snapshot(plan["live_snapshot"])
    selected = plan.get("selected_operations")
    preserved = plan.get("preserved_status_counts")
    if (
        plan.get("schema_version") != 1
        or plan.get("action") != "requeue-operation-cohort"
        or plan.get("policy") != POLICY
        or not isinstance(selected, list)
        or not selected
        or not isinstance(preserved, Mapping)
        or set(preserved) != {"pending", "completed"}
    ):
        raise OperationRecoveryError(
            "operation-recovery requeue plan is invalid"
        )
    checked_selected = []
    for item_value in selected:
        item = _closed(
            _normalized(item_value),
            SELECTED_OPERATION_KEYS,
            "selected operation",
        )
        status = _text(
            item["expected_status"],
            "selected operation status",
            maximum=32,
        )
        if status not in {"failed", "cancelled"}:
            raise OperationRecoveryError("selected operation status is invalid")
        operation_type = _text(
            item["operation_type"],
            "selected operation type",
            maximum=128,
        )
        if operation_type not in EXPECTED_OPERATION_COUNTS:
            raise OperationRecoveryError("selected operation type is invalid")
        checked_selected.append(
            {
                "operation_id": _operation_id(item["operation_id"]),
                "operation_type": operation_type,
                "expected_status": status,
                "row_digest": _sha(
                    item["row_digest"],
                    "selected operation row digest",
                ),
                "task_payload_digest": _sha(
                    item["task_payload_digest"],
                    "selected operation payload digest",
                ),
            }
        )
    identifiers = [item["operation_id"] for item in checked_selected]
    cohort_by_id = {
        item["operation_id"]: item for item in cohort["operations"]
    }
    snapshot_ids = {
        item["operation_id"] for item in snapshot["operations"]
    }
    expected_selected = [
        {
            "operation_id": item["operation_id"],
            "operation_type": item["operation_type"],
            "expected_status": item["current_status"],
            "row_digest": item["row_digest"],
            "task_payload_digest": item["task_payload_digest"],
        }
        for item in snapshot["operations"]
        if item["current_status"] in {"failed", "cancelled"}
    ]
    if (
        len(identifiers) != len(set(identifiers))
        or checked_selected
        != sorted(checked_selected, key=lambda item: item["operation_id"])
        or plan.get("selected_operation_count") != len(checked_selected)
        or checked_selected != expected_selected
        or set(cohort_by_id) != snapshot_ids
        or snapshot["cohort_digest"] != cohort["cohort_digest"]
        or snapshot["status_counts"]["processing"] != 0
        or any(
            item["operation_type"]
            != cohort_by_id[item["operation_id"]]["operation_type"]
            or item["task_payload_digest"]
            != cohort_by_id[item["operation_id"]]["task_payload_digest"]
            for item in snapshot["operations"]
        )
    ):
        raise OperationRecoveryError(
            "operation-recovery selected operations are invalid"
        )
    created_at = _integer(plan["created_at"], "requeue plan created-at")
    expires_at = _integer(plan["expires_at"], "requeue plan expires-at")
    if expires_at - created_at != MAX_PLAN_LIFETIME_SECONDS:
        raise OperationRecoveryError(
            "operation-recovery requeue plan lifetime is invalid"
        )
    observed_at = (
        int(time.time())
        if now is None
        else _integer(now, "operation-recovery verification time")
    )
    if not allow_expired and observed_at >= expires_at:
        raise OperationRecoveryError("operation-recovery requeue plan expired")
    authority = _installation_authority(plan["installation_authority"])
    _assert_installation_authority_schema(
        authority,
        plan_schema_version=1,
    )
    backup = _backup(
        plan["rollback_backup"],
        "operation-recovery rollback backup",
        expected_source_kind="verified-live-pg0-backup",
    )
    encryption = _rollback_encryption(plan["rollback_encryption"])
    source_authority = backup["source_authority"]
    if (
        authority != snapshot["installation_authority"]
        or backup["postgres_system_identifier"]
        != authority["postgres_system_identifier"]
        or source_authority["data_identity_digest"]
        != authority["observed_data_identity_digest"]
        or source_authority["generation_before"]
        != snapshot["generation_before"]
        or plan["cohort_digest"] != cohort["cohort_digest"]
        or plan["snapshot_digest"] != snapshot["snapshot_digest"]
        or plan["cohort_backup_artifact_digest"]
        != cohort["backup"]["artifact_sha256"]
        or plan["pre_generation"] != snapshot["generation_before"]
        or preserved
        != {
            "pending": snapshot["status_counts"]["pending"],
            "completed": snapshot["status_counts"]["completed"],
        }
    ):
        raise OperationRecoveryError(
            "operation-recovery plan evidence differs"
        )
    artifact_paths = {
        "rollback_backup_path": _absolute_path(
            plan["rollback_backup_path"],
            "operation-recovery rollback backup path",
        ),
        "rollback_bundle_path": _absolute_path(
            plan["rollback_bundle_path"],
            "operation-recovery rollback bundle path",
        ),
        "authorization_receipt_path": _absolute_path(
            plan["authorization_receipt_path"],
            "operation-recovery authorization receipt path",
        ),
        "application_receipt_path": _absolute_path(
            plan["application_receipt_path"],
            "operation-recovery application receipt path",
        ),
        "verification_receipt_path": _absolute_path(
            plan["verification_receipt_path"],
            "operation-recovery verification receipt path",
        ),
        "rollback_receipt_path": _absolute_path(
            plan["rollback_receipt_path"],
            "operation-recovery rollback receipt path",
        ),
    }
    if (
        len(
            {
                unicodedata.normalize("NFD", value.casefold())
                for value in artifact_paths.values()
            }
        )
        != len(artifact_paths)
    ):
        raise OperationRecoveryError(
            "operation-recovery artifact paths must be distinct"
        )
    body = {
        "schema_version": 1,
        "action": "requeue-operation-cohort",
        "candidate_release": _candidate_release(plan["candidate_release"]),
        "installation_authority": authority,
        "cohort": cohort,
        "live_snapshot": snapshot,
        "cohort_digest": _sha(
            plan["cohort_digest"],
            "operation cohort digest",
        ),
        "snapshot_digest": _sha(
            plan["snapshot_digest"],
            "live snapshot digest",
        ),
        "cohort_backup_artifact_digest": _sha(
            plan["cohort_backup_artifact_digest"],
            "cohort backup artifact digest",
        ),
        "rollback_backup": backup,
        "rollback_encryption": encryption,
        "pre_generation": _text(
            plan["pre_generation"],
            "operation-recovery pre-generation",
        ),
        "policy": dict(POLICY),
        "selected_operations": checked_selected,
        "selected_operation_count": len(checked_selected),
        "preserved_status_counts": {
            "pending": _integer(
                preserved["pending"],
                "preserved pending count",
            ),
            "completed": _integer(
                preserved["completed"],
                "preserved completed count",
            ),
        },
        **artifact_paths,
        "created_at": created_at,
        "expires_at": expires_at,
    }
    if _sha(plan["plan_digest"], "operation-recovery plan digest") != digest(body):
        raise OperationRecoveryError("operation-recovery plan digest differs")
    return {**body, "plan_digest": plan["plan_digest"]}


def _exact_drain_selected(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "operation_id": item["operation_id"],
            "operation_type": item["operation_type"],
            "expected_status": item["current_status"],
            "row_digest": item["row_digest"],
            "task_payload_digest": item["task_payload_digest"],
        }
        for item in snapshot["operations"]
        if item["current_status"] == "pending"
    ]


def _exact_drain_type_counts(
    selected: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    return {
        operation_type: sum(
            item["operation_type"] == operation_type for item in selected
        )
        for operation_type in EXPECTED_OPERATION_COUNTS
        if any(
            item["operation_type"] == operation_type for item in selected
        )
    }


def _exact_drain_row_set_digest(
    selected: Sequence[Mapping[str, Any]],
) -> str:
    return digest(
        [
            {
                "operation_id": item["operation_id"],
                "row_digest": item["row_digest"],
                "task_payload_digest": item["task_payload_digest"],
            }
            for item in selected
        ]
    )


def _exact_drain_execution_window(
    snapshot: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
    *,
    schema_version: int = 12,
) -> dict[str, Any]:
    retry_counts = {
        item["operation_id"]: item["retry_count"]
        for item in snapshot["operations"]
    }
    selected_operation_count = len(selected)
    remaining_attempt_count = 0
    for item in selected:
        retry_count = retry_counts[item["operation_id"]]
        if not 0 <= retry_count <= EXACT_DRAIN_WORKER_MAX_RETRIES:
            raise OperationRecoveryError(
                "operation-recovery exact drain retry count is invalid"
            )
        remaining_attempt_count += (
            EXACT_DRAIN_WORKER_MAX_RETRIES - retry_count + 1
        )
    retry_wait_count = remaining_attempt_count - selected_operation_count
    execution_waves = (
        remaining_attempt_count
        + EXACT_DRAIN_EXECUTION_EFFECTIVE_CONCURRENCY
        - 1
    ) // EXACT_DRAIN_EXECUTION_EFFECTIVE_CONCURRENCY
    transaction_margin_seconds = (
        2
        * remaining_attempt_count
        * EXACT_DRAIN_TRANSACTION_TIMEOUT_SECONDS
    )
    shutdown_margin_seconds = (
        EXACT_DRAIN_WORKER_MAX_ATTEMPTS
        * EXACT_DRAIN_TRANSACTION_TIMEOUT_SECONDS
    )
    attempt_timeout_seconds = (
        EXACT_DRAIN_OPERATION_ATTEMPT_TIMEOUT_SECONDS
        if schema_version in {11, 12, 13, 14}
        else EXACT_DRAIN_PHASE_ONE_TIMEOUT_SECONDS
    )
    calculated_seconds = (
        execution_waves * attempt_timeout_seconds
        + retry_wait_count * EXACT_DRAIN_MAXIMUM_RETRY_DELAY_SECONDS
        + EXACT_DRAIN_STARTUP_MARGIN_SECONDS
        + transaction_margin_seconds
        + shutdown_margin_seconds
    )
    if calculated_seconds > EXACT_DRAIN_EXECUTION_WINDOW_MAX_SECONDS:
        raise OperationRecoveryError(
            "operation-recovery exact drain execution window exceeds maximum"
        )
    return {
        "schema_version": 2 if schema_version in {11, 12, 13, 14} else 1,
        "kind": "operation-recovery-exact-drain-execution-window",
        "anchor": "authorization-receipt-authorized-at",
        "renewable": False,
        "selected_operation_count": selected_operation_count,
        "remaining_attempt_count": remaining_attempt_count,
        "retry_wait_count": retry_wait_count,
        "effective_concurrency": (
            EXACT_DRAIN_EXECUTION_EFFECTIVE_CONCURRENCY
        ),
        **(
            {
                "operation_attempt_timeout_seconds": (
                    EXACT_DRAIN_OPERATION_ATTEMPT_TIMEOUT_SECONDS
                )
            }
            if schema_version in {11, 12, 13, 14}
            else {
                "phase_one_timeout_seconds": (
                    EXACT_DRAIN_PHASE_ONE_TIMEOUT_SECONDS
                )
            }
        ),
        "transaction_timeout_seconds": (
            EXACT_DRAIN_TRANSACTION_TIMEOUT_SECONDS
        ),
        "maximum_retry_delay_seconds": (
            EXACT_DRAIN_MAXIMUM_RETRY_DELAY_SECONDS
        ),
        "startup_margin_seconds": EXACT_DRAIN_STARTUP_MARGIN_SECONDS,
        "transaction_margin_seconds": transaction_margin_seconds,
        "shutdown_attempt_count": EXACT_DRAIN_WORKER_MAX_ATTEMPTS,
        "shutdown_margin_seconds": shutdown_margin_seconds,
        "calculated_seconds": calculated_seconds,
        "maximum_seconds": EXACT_DRAIN_EXECUTION_WINDOW_MAX_SECONDS,
    }


def _verified_exact_drain_execution_window(value: Any) -> dict[str, Any]:
    normalized = _normalized(value)
    if not isinstance(normalized, Mapping):
        raise OperationRecoveryError(
            "exact drain execution window is invalid"
        )
    schema_version = normalized.get("schema_version")
    window = _closed(
        normalized,
        (
            EXACT_DRAIN_EXECUTION_WINDOW_V2_KEYS
            if schema_version == 2
            else EXACT_DRAIN_EXECUTION_WINDOW_KEYS
        ),
        "exact drain execution window",
    )
    renewable = window["renewable"]
    if type(renewable) is not bool:
        raise OperationRecoveryError(
            "exact drain execution window renewable is invalid"
        )
    return {
        "schema_version": _integer(
            schema_version,
            "exact drain execution window schema version",
        ),
        "kind": _text(
            window["kind"],
            "exact drain execution window kind",
            maximum=128,
        ),
        "anchor": _text(
            window["anchor"],
            "exact drain execution window anchor",
            maximum=128,
        ),
        "renewable": renewable,
        "selected_operation_count": _integer(
            window["selected_operation_count"],
            "exact drain execution window selected operation count",
        ),
        "remaining_attempt_count": _integer(
            window["remaining_attempt_count"],
            "exact drain execution window remaining attempt count",
        ),
        "retry_wait_count": _integer(
            window["retry_wait_count"],
            "exact drain execution window retry wait count",
        ),
        "effective_concurrency": _integer(
            window["effective_concurrency"],
            "exact drain execution window effective concurrency",
        ),
        **(
            {
                "operation_attempt_timeout_seconds": _integer(
                    window["operation_attempt_timeout_seconds"],
                    "exact drain execution window operation-attempt timeout",
                )
            }
            if schema_version == 2
            else {
                "phase_one_timeout_seconds": _integer(
                    window["phase_one_timeout_seconds"],
                    "exact drain execution window phase-one timeout",
                )
            }
        ),
        "transaction_timeout_seconds": _integer(
            window["transaction_timeout_seconds"],
            "exact drain execution window transaction timeout",
        ),
        "maximum_retry_delay_seconds": _integer(
            window["maximum_retry_delay_seconds"],
            "exact drain execution window maximum retry delay",
        ),
        "startup_margin_seconds": _integer(
            window["startup_margin_seconds"],
            "exact drain execution window startup margin",
        ),
        "transaction_margin_seconds": _integer(
            window["transaction_margin_seconds"],
            "exact drain execution window transaction margin",
        ),
        "shutdown_attempt_count": _integer(
            window["shutdown_attempt_count"],
            "exact drain execution window shutdown attempt count",
        ),
        "shutdown_margin_seconds": _integer(
            window["shutdown_margin_seconds"],
            "exact drain execution window shutdown margin",
        ),
        "calculated_seconds": _integer(
            window["calculated_seconds"],
            "exact drain execution window calculated seconds",
        ),
        "maximum_seconds": _integer(
            window["maximum_seconds"],
            "exact drain execution window maximum seconds",
        ),
    }


def _verified_exact_drain_provider_timeout_contract(
    value: Any,
) -> dict[str, Any]:
    contract = _closed(
        _normalized(value),
        EXACT_DRAIN_PROVIDER_TIMEOUT_CONTRACT_KEYS,
        "exact drain provider timeout contract",
    )
    raw_members = contract["members"]
    if not isinstance(raw_members, list):
        raise OperationRecoveryError(
            "exact drain provider timeout contract is invalid"
        )
    members = []
    for value in raw_members:
        member = _closed(
            value,
            EXACT_DRAIN_PROVIDER_TIMEOUT_MEMBER_KEYS,
            "exact drain provider timeout member",
        )
        max_concurrent = member["max_concurrent"]
        members.append(
            {
                "provider_id": _text(
                    member["provider_id"],
                    "exact drain provider timeout member ID",
                    maximum=128,
                ),
                "queue_timeout_seconds": _integer(
                    member["queue_timeout_seconds"],
                    "exact drain provider queue timeout",
                ),
                "execution_timeout_seconds": _integer(
                    member["execution_timeout_seconds"],
                    "exact drain provider execution timeout",
                ),
                "max_concurrent": (
                    None
                    if max_concurrent is None
                    else _integer(
                        max_concurrent,
                        "exact drain provider concurrency",
                    )
                ),
            }
        )
    return {
        "schema_version": _integer(
            contract["schema_version"],
            "exact drain provider timeout schema version",
        ),
        "kind": _text(
            contract["kind"],
            "exact drain provider timeout kind",
            maximum=128,
        ),
        "members": members,
    }


def create_hatchery_capability_receipt(
    *,
    provider_policy_digest: str,
    provider_identity_digest: str,
    model_digest: str,
    observed_at: int | None = None,
    successful: bool,
) -> Mapping[str, Any]:
    """Create payload-free evidence for one exact Hatchery route probe."""
    if type(successful) is not bool:
        raise OperationRecoveryError(
            "operation-recovery Hatchery capability result is invalid"
        )
    body = {
        "schema_version": 1,
        "kind": "operation-recovery-hatchery-capability-receipt",
        "provider_id": "hatchery",
        "provider_policy_digest": _sha(
            provider_policy_digest,
            "Hatchery capability provider policy digest",
        ),
        "provider_identity_digest": _sha(
            provider_identity_digest,
            "Hatchery capability provider identity digest",
        ),
        "model_digest": _sha(
            model_digest,
            "Hatchery capability model digest",
        ),
        "observed_at": (
            int(time.time())
            if observed_at is None
            else _integer(
                observed_at,
                "Hatchery capability observed-at",
            )
        ),
        "successful": successful,
    }
    return {**body, "receipt_digest": digest(body)}


def verify_hatchery_capability_receipt(value: Any) -> Mapping[str, Any]:
    receipt = _closed(
        _normalized(value),
        HATCHERY_CAPABILITY_RECEIPT_KEYS,
        "Hatchery capability receipt",
    )
    body = {
        "schema_version": _integer(
            receipt["schema_version"],
            "Hatchery capability schema version",
        ),
        "kind": _text(
            receipt["kind"],
            "Hatchery capability kind",
        ),
        "provider_id": _text(
            receipt["provider_id"],
            "Hatchery capability provider ID",
        ),
        "provider_policy_digest": _sha(
            receipt["provider_policy_digest"],
            "Hatchery capability provider policy digest",
        ),
        "provider_identity_digest": _sha(
            receipt["provider_identity_digest"],
            "Hatchery capability provider identity digest",
        ),
        "model_digest": _sha(
            receipt["model_digest"],
            "Hatchery capability model digest",
        ),
        "observed_at": _integer(
            receipt["observed_at"],
            "Hatchery capability observed-at",
        ),
        "successful": receipt["successful"],
    }
    if (
        type(body["successful"]) is not bool
        or body["schema_version"] != 1
        or body["kind"]
        != "operation-recovery-hatchery-capability-receipt"
        or body["provider_id"] != "hatchery"
        or _sha(
            receipt["receipt_digest"],
            "Hatchery capability receipt digest",
        )
        != digest(body)
    ):
        raise OperationRecoveryError(
            "operation-recovery Hatchery capability receipt is invalid"
        )
    return {**body, "receipt_digest": receipt["receipt_digest"]}


def _exact_drain_recovery_context(
    value: Mapping[str, Any] | None,
    *,
    cohort: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
    candidate_release: Mapping[str, Any],
    plan_schema_version: int,
) -> dict[str, Any]:
    selected_operation_ids_digest = digest(
        sorted(item["operation_id"] for item in selected)
    )
    cohort_rows = {
        item["operation_id"]: item for item in cohort["operations"]
    }
    snapshot_rows = {
        item["operation_id"]: item for item in snapshot["operations"]
    }
    if plan_schema_version == 14:
        context = _checkpoint_continuation_context(value)
        if (
            context["generation"] != snapshot["generation_before"]
            or context["candidate_release_digest"]
            != candidate_release["release_digest"]
        ):
            raise OperationRecoveryError(
                "operation-recovery checkpoint continuation context differs"
            )
        return context
    initial_projection = [
        {
            key: item[key]
            for key in (
                "operation_id",
                "current_status",
                "created_at",
                "updated_at",
                "completed_at",
                "retry_count",
                "next_retry_at",
                "worker_id_present",
                "worker_id_digest",
                "claimed_at",
                "task_payload_present",
                "task_payload_digest",
                "result_metadata_digest",
                "error_category",
                "error_digest",
            )
        }
        for item in sorted(
            snapshot_rows.values(),
            key=lambda item: item["operation_id"],
        )
    ]

    def initial_retry_evidence_is_valid(item: Mapping[str, Any]) -> bool:
        """Permit a fresh plan to bind a due, retryable pending operation.

        A stopped migration can leave a pending row carrying its already
        recorded retry checkpoint.  The checkpoint is part of the signed
        initial-origin projection; accepting it here avoids inventing a new
        recovery epoch merely to clear evidence that the next worker can
        safely consume.
        """
        retry_at = _post_abort_timestamp(item["next_retry_at"])
        return (
            item["current_status"] == "pending"
            and item["retry_count"] > 0
            and item["error_category"] in FAILURE_CAUSE_FAMILIES
            and item["error_digest"] is not None
            and retry_at is not None
            and retry_at <= snapshot["observed_at"]
        )

    def terminal_completed_evidence_is_valid(
        item: Mapping[str, Any],
    ) -> bool:
        """Preserve historical terminal metadata outside the pending set.

        A continuation snapshot may include rows completed by earlier
        migrations.  Their claim identity and retry checkpoint are durable
        history, not evidence that this worker owns them.  Keep that history
        in the signed initial projection while rejecting incomplete or future
        retry state.
        """
        if (
            item["current_status"] != "completed"
            or item["completed_at"] is None
            or item["worker_id_present"]
            != (item["worker_id_digest"] is not None)
            or item["claimed_at"] is None
            and item["worker_id_present"]
            or item["claimed_at"] is not None
            and not item["worker_id_present"]
        ):
            return False
        retry_at = _post_abort_timestamp(item["next_retry_at"])
        if retry_at is not None and retry_at > snapshot["observed_at"]:
            return False
        if item["retry_count"] == 0:
            return (
                item["next_retry_at"] is None
                and item["error_category"] == "none"
                and item["error_digest"] is None
            )
        return (
            retry_at is not None
            and (
                (
                    item["error_category"] == "none"
                    and item["error_digest"] is None
                )
                or (
                    item["error_category"] in FAILURE_CAUSE_FAMILIES
                    and item["error_digest"] is not None
                )
            )
        )

    initial_origin_valid = (
        set(snapshot_rows) == set(cohort_rows)
        and all(
            item["current_status"]
            in {
                cohort_rows[item["operation_id"]]["baseline_status"],
                "completed",
            }
            and item["created_at"]
            == cohort_rows[item["operation_id"]]["created_at"]
            and item["updated_at"]
            == cohort_rows[item["operation_id"]]["updated_at"]
            and item["retry_count"]
            == cohort_rows[item["operation_id"]]["retry_count"]
            and item["task_payload_present"]
            is cohort_rows[item["operation_id"]]["task_payload_present"]
            and item["task_payload_digest"]
            == cohort_rows[item["operation_id"]]["task_payload_digest"]
            and item["result_metadata_digest"]
            == cohort_rows[item["operation_id"]]["result_metadata_digest"]
            and (
                (item["current_status"] == "completed")
                == (item["completed_at"] is not None)
            )
            and (
                (
                    item["next_retry_at"] is None
                    and item["error_category"] == "none"
                    and item["error_digest"] is None
                )
                or initial_retry_evidence_is_valid(item)
                or terminal_completed_evidence_is_valid(item)
            )
            for item in snapshot_rows.values()
        )
    )
    initial_origin_digest = digest(initial_projection)
    if value is None:
        if not initial_origin_valid:
            raise OperationRecoveryError(
                "operation-recovery exact drain recovery context is required"
            )
        return {
            "schema_version": 1,
            "kind": "operation-recovery-exact-drain-recovery-context",
            "origin": "initial-snapshot",
            "generation": snapshot["generation_before"],
            "recovery_epoch": 0,
            "candidate_release_digest": candidate_release["release_digest"],
            "selected_operation_ids_digest": (
                selected_operation_ids_digest
            ),
            "initial_origin_digest": initial_origin_digest,
            "post_abort_selected_operation_ids_digest": None,
            "post_abort_plan_digest": None,
            "post_abort_application_receipt_digest": None,
            "post_abort_verification_receipt_digest": None,
            "retry_recovery_digest": None,
            "selected_checkpoint_set_digest": None,
            "preserved_row_set_digest": None,
        }
    normalized_value = _normalized(value)
    if (
        isinstance(normalized_value, Mapping)
        and normalized_value.get("schema_version") == 4
    ):
        context = _closed(
            normalized_value,
            EXACT_DRAIN_RECOVERY_CONTEXT_V4_KEYS,
            "exact drain recovery context",
        )
        digest_keys = (
            "post_terminal_reconciliation_plan_digest",
            "post_terminal_reconciliation_application_receipt_digest",
            "post_terminal_reconciliation_verification_receipt_digest",
            "terminal_plan_digest",
            "terminal_authorization_receipt_digest",
            "terminal_application_receipt_digest",
            "terminal_progress_digest",
            "terminal_status_digest",
            "retry_recovery_digest",
            "selected_checkpoint_set_digest",
            "preserved_row_set_digest",
        )
        body = {
            "schema_version": 4,
            "kind": _text(
                context["kind"],
                "exact drain recovery context kind",
            ),
            "origin": _text(
                context["origin"],
                "exact drain recovery context origin",
            ),
            "generation": _text(
                context["generation"],
                "exact drain recovery context generation",
            ),
            "recovery_epoch": _integer(
                context["recovery_epoch"],
                "exact drain recovery epoch",
            ),
            "reconciliation_cycle": _integer(
                context["reconciliation_cycle"],
                "exact drain reconciliation cycle",
            ),
            "candidate_release_digest": _sha(
                context["candidate_release_digest"],
                "exact drain recovery candidate release digest",
            ),
            "selected_operation_ids_digest": _sha(
                context["selected_operation_ids_digest"],
                "exact drain selected operation ids digest",
            ),
            "initial_origin_digest": context["initial_origin_digest"],
            **{
                key: _sha(context[key], f"exact drain recovery {key}")
                for key in digest_keys
            },
        }
        if (
            plan_schema_version != 13
            or body["kind"]
            != "operation-recovery-exact-drain-recovery-context"
            or body["origin"] != "post-terminal-reconciliation"
            or body["generation"] != snapshot["generation_before"]
            or body["recovery_epoch"] != 3
            or body["reconciliation_cycle"] != 1
            or body["candidate_release_digest"]
            != candidate_release["release_digest"]
            or body["selected_operation_ids_digest"]
            != selected_operation_ids_digest
            or body["initial_origin_digest"] is not None
        ):
            raise OperationRecoveryError(
                "operation-recovery exact drain recovery context is invalid"
            )
        return body
    context = _closed(
        normalized_value,
        EXACT_DRAIN_RECOVERY_CONTEXT_KEYS,
        "exact drain recovery context",
    )
    origin = _text(
        context["origin"],
        "exact drain recovery context origin",
    )
    provenance_keys = (
        "post_abort_plan_digest",
        "post_abort_application_receipt_digest",
        "post_abort_verification_receipt_digest",
        "retry_recovery_digest",
        "selected_checkpoint_set_digest",
        "preserved_row_set_digest",
        "post_abort_selected_operation_ids_digest",
    )
    provenance = (
        {key: None for key in provenance_keys}
        if origin == "initial-snapshot"
        and all(context[key] is None for key in provenance_keys)
        else {
            key: _sha(context[key], f"exact drain recovery {key}")
            for key in provenance_keys
        }
    )
    body = {
        "schema_version": _integer(
            context["schema_version"],
            "exact drain recovery context schema version",
        ),
        "kind": _text(
            context["kind"],
            "exact drain recovery context kind",
        ),
        "origin": origin,
        "generation": _text(
            context["generation"],
            "exact drain recovery context generation",
        ),
        "recovery_epoch": _integer(
            context["recovery_epoch"],
            "exact drain recovery epoch",
        ),
        "candidate_release_digest": _sha(
            context["candidate_release_digest"],
            "exact drain recovery candidate release digest",
        ),
        "selected_operation_ids_digest": _sha(
            context["selected_operation_ids_digest"],
            "exact drain selected operation ids digest",
        ),
        "initial_origin_digest": (
            _sha(
                context["initial_origin_digest"],
                "exact drain initial origin digest",
            )
            if context["initial_origin_digest"] is not None
            else None
        ),
        **provenance,
    }
    common_invalid = (
        body["kind"]
        != "operation-recovery-exact-drain-recovery-context"
        or body["generation"] != snapshot["generation_before"]
        or body["candidate_release_digest"]
        != candidate_release["release_digest"]
        or body["selected_operation_ids_digest"]
        != selected_operation_ids_digest
    )
    origin_invalid = (
        body["origin"] == "initial-snapshot"
        and (
            body["schema_version"] != 1
            or body["recovery_epoch"] != 0
            or any(provenance.values())
            or not initial_origin_valid
            or body["initial_origin_digest"] != initial_origin_digest
        )
    ) or (
        body["origin"] == "post-abort"
        and (
            (body["schema_version"], body["recovery_epoch"])
            not in {(1, 1), (2, 2), (3, 3)}
            or (
                body["schema_version"] == 2
                and plan_schema_version not in {11, 12}
            )
            or (
                body["schema_version"] == 3
                and plan_schema_version != 12
            )
            or not all(provenance.values())
            or body["initial_origin_digest"] is not None
        )
    )
    if common_invalid or origin_invalid or body["origin"] not in {
        "initial-snapshot",
        "post-abort",
    }:
        raise OperationRecoveryError(
            "operation-recovery exact drain recovery context is invalid"
        )
    return body


def exact_drain_execution_window_seconds(
    plan: Mapping[str, Any],
) -> int | None:
    """Return the fixed authorized execution window for any plan schema."""
    schema_version = plan.get("schema_version")
    if schema_version == 1:
        return None
    if schema_version in {2, 3, 4, 5, 6, 7, 8, 9}:
        return _integer(
            plan.get("execution_lease_seconds"),
            "exact drain legacy execution lease",
        )
    if schema_version in {10, 11, 12, 13, 14}:
        return _verified_exact_drain_execution_window(
            plan.get("execution_window")
        )["calculated_seconds"]
    raise OperationRecoveryError(
        "operation-recovery exact drain plan is invalid"
    )


def exact_drain_execution_deadline(
    plan: Mapping[str, Any],
    authorization: Mapping[str, Any],
) -> int | None:
    """Anchor the nonrenewing execution window to its authorization receipt."""
    window_seconds = exact_drain_execution_window_seconds(plan)
    if window_seconds is None:
        return None
    return _integer(
        authorization.get("authorized_at"),
        "exact drain authorization time",
    ) + window_seconds


def create_exact_drain_plan(
    cohort_value: Mapping[str, Any],
    live_snapshot_value: Mapping[str, Any],
    *,
    candidate_release: Mapping[str, Any],
    rollback_backup: Mapping[str, Any],
    rollback_backup_path: str,
    provider_policy_digest: str,
    effective_profile_digest: str,
    worker_runtime_digest: str,
    authorization_receipt_path: str,
    application_receipt_path: str,
    status_artifact_path: str,
    verification_receipt_path: str,
    recovery_context: Mapping[str, Any] | None = None,
    hatchery_capability_receipt: Mapping[str, Any] | None = None,
    checkpoint_continuation_handoff: Mapping[str, Any] | None = None,
    candidate_runtime_snapshot_schema_version: int | None = None,
    created_at: int | None = None,
    schema_version: int = 12,
) -> Mapping[str, Any]:
    """Plan an exact-ID worker drain without starting a worker."""
    if type(schema_version) is not int or schema_version not in {
        10,
        11,
        12,
        13,
        14,
    }:
        raise OperationRecoveryError(
            "operation-recovery exact drain plan schema is invalid"
        )
    if candidate_runtime_snapshot_schema_version is not None and (
        type(candidate_runtime_snapshot_schema_version) is not int
        or candidate_runtime_snapshot_schema_version not in {7, 8}
    ):
        raise OperationRecoveryError(
            "operation-recovery candidate runtime snapshot schema is invalid"
        )
    cohort = verify_cohort_manifest(cohort_value)
    snapshot = verify_live_snapshot(live_snapshot_value)
    selected = _exact_drain_selected(snapshot)
    selected_type_counts = _exact_drain_type_counts(selected)
    cohort_by_id = {
        item["operation_id"]: item for item in cohort["operations"]
    }
    if (
        snapshot["cohort_digest"] != cohort["cohort_digest"]
        or not selected
        or snapshot["status_counts"].get("pending") != len(selected)
        or snapshot["status_counts"].get("processing")
        or snapshot["status_counts"].get("failed")
        or snapshot["status_counts"].get("cancelled")
        or snapshot["status_counts"].get("completed", 0) + len(selected)
        != sum(EXPECTED_OPERATION_COUNTS.values())
        or any(
            count > EXPECTED_OPERATION_COUNTS[operation_type]
            for operation_type, count in selected_type_counts.items()
        )
        or set(cohort_by_id)
        != {item["operation_id"] for item in snapshot["operations"]}
        or any(
            item["operation_type"]
            != cohort_by_id[item["operation_id"]]["operation_type"]
            or item["task_payload_digest"]
            != cohort_by_id[item["operation_id"]]["task_payload_digest"]
            for item in snapshot["operations"]
        )
        or any(item["expected_status"] != "pending" for item in selected)
    ):
        raise OperationRecoveryError(
            "operation-recovery exact drain pending set is invalid"
        )
    authority = snapshot["installation_authority"]
    _assert_installation_authority_schema(
        authority,
        plan_schema_version=schema_version,
    )
    backup = _backup(
        rollback_backup,
        "operation-recovery exact drain backup",
        expected_source_kind="verified-live-pg0-backup",
    )
    source_authority = backup["source_authority"]
    if (
        backup["postgres_system_identifier"]
        != authority["postgres_system_identifier"]
        or source_authority["data_identity_digest"]
        != authority["observed_data_identity_digest"]
        or source_authority["generation_before"]
        != snapshot["generation_before"]
        or source_authority["generation_after"]
        != snapshot["generation_after"]
    ):
        raise OperationRecoveryError(
            "operation-recovery exact drain backup identity differs"
        )
    status_path = _absolute_path(
        status_artifact_path,
        "operation-recovery exact drain status path",
    )
    status_name = Path(status_path).name
    if "status" not in status_name or "progress" in status_name:
        raise OperationRecoveryError(
            "operation-recovery exact drain status path is invalid"
        )
    progress_path = str(
        Path(status_path).with_name(status_name.replace("status", "progress", 1))
    )
    artifact_paths = {
        "rollback_backup_path": _absolute_path(
            rollback_backup_path,
            "operation-recovery exact drain backup path",
        ),
        "authorization_receipt_path": _absolute_path(
            authorization_receipt_path,
            "operation-recovery exact drain authorization path",
        ),
        "application_receipt_path": _absolute_path(
            application_receipt_path,
            "operation-recovery exact drain application path",
        ),
        "progress_artifact_path": _absolute_path(
            progress_path,
            "operation-recovery exact drain progress path",
        ),
        "status_artifact_path": status_path,
        "verification_receipt_path": _absolute_path(
            verification_receipt_path,
            "operation-recovery exact drain verification path",
        ),
    }
    normalized_artifacts = {
        unicodedata.normalize("NFD", value.casefold())
        for value in artifact_paths.values()
    }
    normalized_archives = {
        unicodedata.normalize("NFD", value.casefold())
        for value in _exact_drain_archive_paths(progress_path)
    }
    if (
        len(normalized_artifacts) != len(artifact_paths)
        or normalized_artifacts & normalized_archives
    ):
        raise OperationRecoveryError(
            "operation-recovery exact drain artifact paths must be distinct"
        )
    planned_at = (
        int(time.time())
        if created_at is None
        else _integer(created_at, "exact drain plan created-at")
    )
    checked_continuation_handoff = None
    if schema_version == 14:
        if checkpoint_continuation_handoff is None:
            raise OperationRecoveryError(
                "operation-recovery checkpoint continuation handoff is required"
            )
        checked_continuation_handoff = verify_checkpoint_continuation_handoff(
            checkpoint_continuation_handoff,
            live_snapshot=snapshot,
            now=planned_at,
        )
        if (
            checked_continuation_handoff["candidate_release"]
            != _candidate_release(candidate_release)
            or checked_continuation_handoff["continuation_context"]
            != recovery_context
            or not {
                item["operation_id"]
                for item in checked_continuation_handoff["operations"]
            }.issubset({item["operation_id"] for item in selected})
        ):
            raise OperationRecoveryError(
                "operation-recovery checkpoint continuation handoff differs"
            )
    elif checkpoint_continuation_handoff is not None:
        raise OperationRecoveryError(
            "operation-recovery checkpoint continuation handoff is unsupported"
        )
    evidence_observed_at = snapshot["observed_at"]
    if (
        evidence_observed_at > planned_at
        or planned_at - evidence_observed_at
        > EXACT_DRAIN_EVIDENCE_MAX_AGE_SECONDS
    ):
        raise OperationRecoveryError(
            "operation-recovery exact drain evidence is stale"
        )
    release = _candidate_release(candidate_release)
    execution_window = _exact_drain_execution_window(
        snapshot,
        selected,
        schema_version=schema_version,
    )
    checked_recovery_context = _exact_drain_recovery_context(
        recovery_context,
        cohort=cohort,
        snapshot=snapshot,
        selected=selected,
        candidate_release=release,
        plan_schema_version=schema_version,
    )
    capability_fields = {}
    if schema_version in {13, 14}:
        if hatchery_capability_receipt is None:
            raise OperationRecoveryError(
                "operation-recovery Hatchery capability receipt is required"
            )
        capability = verify_hatchery_capability_receipt(
            hatchery_capability_receipt
        )
        if (
            not capability["successful"]
            or capability["provider_policy_digest"]
            != _sha(provider_policy_digest, "provider policy digest")
            or capability["observed_at"] > planned_at
            or planned_at - capability["observed_at"]
            > EXACT_DRAIN_EVIDENCE_MAX_AGE_SECONDS
        ):
            raise OperationRecoveryError(
                "operation-recovery Hatchery capability receipt is invalid"
            )
        capability_fields = {
            "hatchery_capability_receipt": capability,
            "hatchery_capability_receipt_digest": capability[
                "receipt_digest"
            ],
        }
    body = {
        "schema_version": schema_version,
        "kind": "operation-recovery-exact-drain-plan",
        "action": "drain-exact-operation-cohort",
        "authority": "unapproved-plan",
        "mutation_authorized": False,
        "candidate_release": release,
        "installation_authority": authority,
        "cohort": cohort,
        "live_snapshot": snapshot,
        "cohort_digest": cohort["cohort_digest"],
        "snapshot_digest": snapshot["snapshot_digest"],
        "pre_generation": snapshot["generation_before"],
        "selected_operations": selected,
        "selected_operation_count": len(selected),
        "selected_status_counts": {"pending": len(selected)},
        "selected_type_counts": selected_type_counts,
        "selected_row_set_digest": _exact_drain_row_set_digest(selected),
        "preserved_status_counts": {
            "completed": snapshot["status_counts"]["completed"]
        },
        "rollback_backup": backup,
        **artifact_paths,
        "provider_policy_digest": _sha(
            provider_policy_digest,
            "provider policy digest",
        ),
        "effective_profile_digest": _sha(
            effective_profile_digest,
            "effective profile digest",
        ),
        "worker_runtime_digest": _sha(
            worker_runtime_digest,
            "worker runtime digest",
        ),
        "worker_max_attempts": EXACT_DRAIN_WORKER_MAX_ATTEMPTS,
        "worker_max_retries": EXACT_DRAIN_WORKER_MAX_RETRIES,
        "evidence_observed_at": evidence_observed_at,
        "evidence_max_age_seconds": EXACT_DRAIN_EVIDENCE_MAX_AGE_SECONDS,
        "transaction_timeout_seconds": EXACT_DRAIN_TRANSACTION_TIMEOUT_SECONDS,
        "execution_window": execution_window,
        "recovery_context": checked_recovery_context,
        "recovery_context_digest": digest(checked_recovery_context),
        **(
            {
                "checkpoint_continuation_handoff": (
                    checked_continuation_handoff
                ),
                "checkpoint_continuation_handoff_digest": digest(
                    checked_continuation_handoff
                ),
            }
            if schema_version == 14
            else {}
        ),
        **capability_fields,
        "phase_one_statement_timeout_seconds": (
            EXACT_DRAIN_PHASE_ONE_STATEMENT_TIMEOUT_SECONDS
        ),
        "phase_one_client_timeout_seconds": (
            EXACT_DRAIN_PHASE_ONE_CLIENT_TIMEOUT_SECONDS
        ),
        "phase_one_timeout_seconds": EXACT_DRAIN_PHASE_ONE_TIMEOUT_SECONDS,
        **(
            {}
            if schema_version == 10
            else {
                "operation_attempt_timeout_seconds": (
                    EXACT_DRAIN_OPERATION_ATTEMPT_TIMEOUT_SECONDS
                ),
                "phase_one_deadline_anchor": "first-phase-one-entry",
                "phase_one_nested_stage_prefixes": ["llm."],
                "provider_timeout_contract": (
                    _normalized(EXACT_DRAIN_PROVIDER_TIMEOUT_CONTRACT)
                ),
                **(
                    {
                        "operation_attempt_timeout_disposition": (
                            "task-retry-after-quiescence"
                        )
                    }
                    if schema_version in {12, 13, 14}
                    else {}
                ),
            }
        ),
        "phase_repair_contract_digest": (
            EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V9_DIGEST
            if schema_version in {13, 14}
            or (
                schema_version == 12
                and candidate_runtime_snapshot_schema_version == 8
            )
            else (
                EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V8_DIGEST
                if schema_version == 12
                else (
                    EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V7_DIGEST
                    if schema_version == 11
                    else EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V6_DIGEST
                )
            )
        ),
        "progress_schema_version": (
            5
            if schema_version in {12, 13, 14}
            else (4 if schema_version == 11 else 3)
        ),
        "failure_evidence_contract_digest": (
            EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT_V4_DIGEST
            if schema_version in {12, 13, 14}
            else (
                EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT_V3_DIGEST
                if schema_version == 11
                else EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT_V2_DIGEST
            )
        ),
        "created_at": planned_at,
        "expires_at": planned_at + EXACT_DRAIN_APPROVAL_LIFETIME_SECONDS,
    }
    return {**body, "plan_digest": digest(body)}


def verify_exact_drain_plan(
    value: Any,
    *,
    now: int | None = None,
    allow_expired: bool = False,
) -> Mapping[str, Any]:
    normalized = _normalized(value)
    if not isinstance(normalized, Mapping):
        raise OperationRecoveryError(
            "operation-recovery exact drain plan is invalid"
        )
    schema_version = normalized.get("schema_version")
    if type(schema_version) is not int or schema_version not in {
        1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14
    }:
        raise OperationRecoveryError(
            "operation-recovery exact drain plan is invalid"
        )
    plan = _closed(
        normalized,
        {
            1: EXACT_DRAIN_PLAN_KEYS,
            2: EXACT_DRAIN_PLAN_V2_KEYS,
            3: EXACT_DRAIN_PLAN_V3_KEYS,
            4: EXACT_DRAIN_PLAN_V4_KEYS,
            5: EXACT_DRAIN_PLAN_V5_KEYS,
            6: EXACT_DRAIN_PLAN_V6_KEYS,
            7: EXACT_DRAIN_PLAN_V7_KEYS,
            8: EXACT_DRAIN_PLAN_V8_KEYS,
            9: EXACT_DRAIN_PLAN_V9_KEYS,
            10: EXACT_DRAIN_PLAN_V10_KEYS,
            11: EXACT_DRAIN_PLAN_V11_KEYS,
            12: EXACT_DRAIN_PLAN_V12_KEYS,
            13: EXACT_DRAIN_PLAN_V13_KEYS,
            14: EXACT_DRAIN_PLAN_V14_KEYS,
        }[schema_version],
        "operation-recovery exact drain plan",
    )
    cohort = verify_cohort_manifest(plan["cohort"])
    snapshot = verify_live_snapshot(plan["live_snapshot"])
    selected_value = plan.get("selected_operations")
    if not isinstance(selected_value, list):
        raise OperationRecoveryError(
            "operation-recovery exact drain plan is invalid"
        )
    selected = []
    for item_value in selected_value:
        item = _closed(
            _normalized(item_value),
            SELECTED_OPERATION_KEYS,
            "exact drain selected operation",
        )
        selected.append(
            {
                "operation_id": _operation_id(item["operation_id"]),
                "operation_type": _text(
                    item["operation_type"],
                    "exact drain operation type",
                    maximum=128,
                ),
                "expected_status": _text(
                    item["expected_status"],
                    "exact drain operation status",
                    maximum=32,
                ),
                "row_digest": _sha(
                    item["row_digest"],
                    "exact drain row digest",
                ),
                "task_payload_digest": _sha(
                    item["task_payload_digest"],
                    "exact drain payload digest",
                ),
            }
        )
    expected_selected = _exact_drain_selected(snapshot)
    selected_type_counts = _exact_drain_type_counts(selected)
    status_counts = _count_map(
        plan["selected_status_counts"],
        "exact drain selected status counts",
    )
    type_counts = _count_map(
        plan["selected_type_counts"],
        "exact drain selected type counts",
    )
    preserved = _count_map(
        plan["preserved_status_counts"],
        "exact drain preserved status counts",
        minimum=0,
    )
    created_at = _integer(plan["created_at"], "exact drain plan created-at")
    expires_at = _integer(plan["expires_at"], "exact drain plan expires-at")
    observed_at = (
        int(time.time())
        if now is None
        else _integer(now, "exact drain verification time")
    )
    if not allow_expired and observed_at >= expires_at:
        raise OperationRecoveryError("operation-recovery exact drain plan expired")
    if schema_version in {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14}:
        evidence_observed_at = _integer(
            plan["evidence_observed_at"],
            "exact drain evidence observed-at",
        )
        evidence_max_age_seconds = _integer(
            plan["evidence_max_age_seconds"],
            "exact drain evidence maximum age",
        )
        transaction_timeout_seconds = _integer(
            plan["transaction_timeout_seconds"],
            "exact drain transaction timeout",
        )
        execution_lease_seconds = (
            _integer(
                plan["execution_lease_seconds"],
                "exact drain execution lease",
            )
            if schema_version in {2, 3, 4, 5, 6, 7, 8, 9}
            else None
        )
    else:
        evidence_observed_at = None
        evidence_max_age_seconds = None
        transaction_timeout_seconds = None
        execution_lease_seconds = None
    if schema_version in {3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14}:
        phase_one_statement_timeout_seconds = _integer(
            plan["phase_one_statement_timeout_seconds"],
            "exact drain phase-one statement timeout",
        )
        phase_one_timeout_seconds = _integer(
            plan["phase_one_timeout_seconds"],
            "exact drain phase-one timeout",
        )
        phase_repair_contract_digest = _sha(
            plan["phase_repair_contract_digest"],
            "exact drain phase repair contract digest",
        )
    else:
        phase_one_statement_timeout_seconds = None
        phase_one_timeout_seconds = None
        phase_repair_contract_digest = None
    if schema_version in {6, 7, 8, 9, 10, 11, 12, 13, 14}:
        phase_one_client_timeout_seconds = _integer(
            plan["phase_one_client_timeout_seconds"],
            "exact drain phase-one client timeout",
        )
        progress_schema_version = _integer(
            plan["progress_schema_version"],
            "exact drain progress schema version",
        )
        failure_evidence_contract_digest = _sha(
            plan["failure_evidence_contract_digest"],
            "exact drain failure evidence contract digest",
        )
    else:
        phase_one_client_timeout_seconds = None
        progress_schema_version = None
        failure_evidence_contract_digest = None
    release = _candidate_release(plan["candidate_release"])
    if schema_version in {10, 11, 12, 13, 14}:
        execution_window = _verified_exact_drain_execution_window(
            plan["execution_window"]
        )
        expected_execution_window = _exact_drain_execution_window(
            snapshot,
            expected_selected,
            schema_version=schema_version,
        )
        recovery_context = _exact_drain_recovery_context(
            plan["recovery_context"],
            cohort=cohort,
            snapshot=snapshot,
            selected=selected,
            candidate_release=release,
            plan_schema_version=schema_version,
        )
        recovery_context_digest = _sha(
            plan["recovery_context_digest"],
            "exact drain recovery context digest",
        )
        if schema_version == 14:
            checkpoint_continuation_handoff = (
                verify_checkpoint_continuation_handoff(
                    plan["checkpoint_continuation_handoff"],
                    live_snapshot=snapshot,
                    now=created_at,
                    allow_expired=True,
                )
            )
            checkpoint_continuation_handoff_digest = _sha(
                plan["checkpoint_continuation_handoff_digest"],
                "checkpoint continuation handoff digest",
            )
        else:
            checkpoint_continuation_handoff = None
            checkpoint_continuation_handoff_digest = None
    else:
        execution_window = None
        expected_execution_window = None
        recovery_context = None
        recovery_context_digest = None
        checkpoint_continuation_handoff = None
        checkpoint_continuation_handoff_digest = None
    if schema_version in {11, 12, 13, 14}:
        operation_attempt_timeout_seconds = _integer(
            plan["operation_attempt_timeout_seconds"],
            "exact drain operation-attempt timeout",
        )
        phase_one_deadline_anchor = _text(
            plan["phase_one_deadline_anchor"],
            "exact drain phase-one deadline anchor",
            maximum=128,
        )
        phase_one_nested_stage_prefixes = _normalized(
            plan["phase_one_nested_stage_prefixes"]
        )
        provider_timeout_contract = _verified_exact_drain_provider_timeout_contract(
            plan["provider_timeout_contract"]
        )
        operation_attempt_timeout_disposition = (
            _text(
                plan["operation_attempt_timeout_disposition"],
                "exact drain operation-attempt timeout disposition",
                maximum=128,
            )
            if schema_version in {12, 13, 14}
            else None
        )
    else:
        operation_attempt_timeout_seconds = None
        phase_one_deadline_anchor = None
        phase_one_nested_stage_prefixes = None
        provider_timeout_contract = None
        operation_attempt_timeout_disposition = None
    capability_fields = {}
    if schema_version in {13, 14}:
        capability = verify_hatchery_capability_receipt(
            plan["hatchery_capability_receipt"]
        )
        capability_digest = _sha(
            plan["hatchery_capability_receipt_digest"],
            "Hatchery capability receipt digest",
        )
        capability_fields = {
            "hatchery_capability_receipt": capability,
            "hatchery_capability_receipt_digest": capability_digest,
        }
    backup = _backup(
        plan["rollback_backup"],
        "operation-recovery exact drain backup",
        expected_source_kind="verified-live-pg0-backup",
    )
    authority = _installation_authority(plan["installation_authority"])
    _assert_installation_authority_schema(
        authority,
        plan_schema_version=schema_version,
    )
    source_authority = backup["source_authority"]
    artifact_paths = {
        "rollback_backup_path": _absolute_path(
            plan["rollback_backup_path"],
            "operation-recovery exact drain backup path",
        ),
        "authorization_receipt_path": _absolute_path(
            plan["authorization_receipt_path"],
            "operation-recovery exact drain authorization path",
        ),
        "application_receipt_path": _absolute_path(
            plan["application_receipt_path"],
            "operation-recovery exact drain application path",
        ),
        "progress_artifact_path": _absolute_path(
            plan["progress_artifact_path"],
            "operation-recovery exact drain progress path",
        ),
        "status_artifact_path": _absolute_path(
            plan["status_artifact_path"],
            "operation-recovery exact drain status path",
        ),
        "verification_receipt_path": _absolute_path(
            plan["verification_receipt_path"],
            "operation-recovery exact drain verification path",
        ),
    }
    status_name = Path(artifact_paths["status_artifact_path"]).name
    expected_progress_path = (
        None
        if "status" not in status_name or "progress" in status_name
        else str(
            Path(artifact_paths["status_artifact_path"]).with_name(
                status_name.replace("status", "progress", 1)
            )
        )
    )
    if (
        schema_version
        not in {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14}
        or plan.get("kind") != "operation-recovery-exact-drain-plan"
        or plan.get("action") != "drain-exact-operation-cohort"
        or plan.get("authority") != "unapproved-plan"
        or plan.get("mutation_authorized") is not False
        or selected != expected_selected
        or not selected
        or len({item["operation_id"] for item in selected}) != len(selected)
        or selected
        != sorted(selected, key=lambda item: item["operation_id"])
        or plan.get("selected_operation_count") != len(selected)
        or status_counts != {"pending": len(selected)}
        or type_counts != selected_type_counts
        or preserved
        != {"completed": snapshot["status_counts"].get("completed", 0)}
        or snapshot["status_counts"].get("pending") != len(selected)
        or snapshot["status_counts"].get("processing")
        or snapshot["status_counts"].get("failed")
        or snapshot["status_counts"].get("cancelled")
        or snapshot["status_counts"].get("completed", 0) + len(selected)
        != sum(EXPECTED_OPERATION_COUNTS.values())
        or any(
            count > EXPECTED_OPERATION_COUNTS[operation_type]
            for operation_type, count in type_counts.items()
        )
        or plan.get("selected_row_set_digest")
        != _exact_drain_row_set_digest(selected)
        or plan.get("worker_max_retries")
        != EXACT_DRAIN_WORKER_MAX_RETRIES
        or plan.get("worker_max_attempts")
        != EXACT_DRAIN_WORKER_MAX_ATTEMPTS
        or artifact_paths["progress_artifact_path"]
        != expected_progress_path
        or plan.get("cohort_digest") != cohort["cohort_digest"]
        or plan.get("snapshot_digest") != snapshot["snapshot_digest"]
        or snapshot["cohort_digest"] != cohort["cohort_digest"]
        or authority != snapshot["installation_authority"]
        or plan.get("pre_generation") != snapshot["generation_before"]
        or backup["postgres_system_identifier"]
        != authority["postgres_system_identifier"]
        or source_authority["data_identity_digest"]
        != authority["observed_data_identity_digest"]
        or source_authority["generation_before"]
        != snapshot["generation_before"]
        or source_authority["generation_after"]
        != snapshot["generation_after"]
        or expires_at - created_at
        != (
            MAX_PLAN_LIFETIME_SECONDS
            if schema_version == 1
            else EXACT_DRAIN_APPROVAL_LIFETIME_SECONDS
        )
        or (
            schema_version in {
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
            }
            and (
                evidence_observed_at != snapshot["observed_at"]
                or evidence_max_age_seconds
                != EXACT_DRAIN_EVIDENCE_MAX_AGE_SECONDS
                or transaction_timeout_seconds
                != EXACT_DRAIN_TRANSACTION_TIMEOUT_SECONDS
                or (
                    schema_version in {2, 3, 4, 5, 6, 7, 8, 9}
                    and execution_lease_seconds
                    != EXACT_DRAIN_EXECUTION_LEASE_SECONDS
                )
                or evidence_observed_at > created_at
                or created_at - evidence_observed_at
                > evidence_max_age_seconds
            )
        )
        or (
            schema_version in {10, 11, 12, 13, 14}
            and (
                execution_window != expected_execution_window
                or recovery_context_digest != digest(recovery_context)
            )
        )
        or (
            schema_version == 14
            and (
                checkpoint_continuation_handoff_digest
                != digest(checkpoint_continuation_handoff)
                or checkpoint_continuation_handoff["candidate_release"]
                != release
                or checkpoint_continuation_handoff["continuation_context"]
                != recovery_context
                or not {
                    item["operation_id"]
                    for item in checkpoint_continuation_handoff["operations"]
                }.issubset(
                    {item["operation_id"] for item in selected}
                )
                or checkpoint_continuation_handoff["created_at"] > created_at
                or checkpoint_continuation_handoff["expires_at"] <= created_at
            )
        )
        or (
            schema_version in {11, 12, 13, 14}
            and (
                operation_attempt_timeout_seconds
                != EXACT_DRAIN_OPERATION_ATTEMPT_TIMEOUT_SECONDS
                or phase_one_deadline_anchor != "first-phase-one-entry"
                or phase_one_nested_stage_prefixes != ["llm."]
                or provider_timeout_contract
                != EXACT_DRAIN_PROVIDER_TIMEOUT_CONTRACT
                or (
                    schema_version in {12, 13, 14}
                    and operation_attempt_timeout_disposition
                    != "task-retry-after-quiescence"
                )
            )
        )
        or (
            schema_version in {
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
            }
            and (
                phase_one_statement_timeout_seconds
                != EXACT_DRAIN_PHASE_ONE_STATEMENT_TIMEOUT_SECONDS
                or phase_one_timeout_seconds
                != EXACT_DRAIN_PHASE_ONE_TIMEOUT_SECONDS
                or phase_repair_contract_digest
                not in (
                    (
                        EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V8_DIGEST,
                        EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V9_DIGEST,
                    )
                    if schema_version == 12
                    else (
                        {
                            3: EXACT_DRAIN_PHASE_REPAIR_CONTRACT_DIGEST,
                            4: EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V2_DIGEST,
                            5: EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V3_DIGEST,
                            6: EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V4_DIGEST,
                            7: EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V5_DIGEST,
                            8: EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V5_DIGEST,
                            9: EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V6_DIGEST,
                            10: EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V6_DIGEST,
                            11: EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V7_DIGEST,
                            13: EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V9_DIGEST,
                            14: EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V9_DIGEST,
                        }[schema_version],
                    )
                )
            )
        )
        or (
            schema_version in {
                6,
                7,
                8,
                9,
                10,
                11,
                12,
                13,
                14,
            }
            and (
                phase_one_client_timeout_seconds
                != EXACT_DRAIN_PHASE_ONE_CLIENT_TIMEOUT_SECONDS
                or phase_one_client_timeout_seconds
                <= phase_one_statement_timeout_seconds
                or progress_schema_version
                != (
                    5
                    if schema_version in {12, 13, 14}
                    else (
                        4
                        if schema_version == 11
                        else (3 if schema_version in {8, 9, 10} else 2)
                    )
                )
                or failure_evidence_contract_digest
                != (
                    EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT_V4_DIGEST
                    if schema_version in {12, 13, 14}
                    else (
                        EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT_V3_DIGEST
                        if schema_version == 11
                        else (
                            EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT_V2_DIGEST
                            if schema_version in {8, 9, 10}
                            else EXACT_DRAIN_FAILURE_EVIDENCE_CONTRACT_DIGEST
                        )
                    )
                )
            )
        )
        or (
            schema_version in {13, 14}
            and (
                capability_fields["hatchery_capability_receipt_digest"]
                != capability_fields["hatchery_capability_receipt"][
                    "receipt_digest"
                ]
                or capability_fields["hatchery_capability_receipt"][
                    "provider_policy_digest"
                ]
                != plan["provider_policy_digest"]
                or not capability_fields["hatchery_capability_receipt"][
                    "successful"
                ]
                or capability_fields["hatchery_capability_receipt"][
                    "observed_at"
                ]
                > created_at
                or created_at
                - capability_fields["hatchery_capability_receipt"][
                    "observed_at"
                ]
                > EXACT_DRAIN_EVIDENCE_MAX_AGE_SECONDS
            )
        )
        or len(
            {
                unicodedata.normalize("NFD", value.casefold())
                for value in artifact_paths.values()
            }
        )
        != len(artifact_paths)
        or {
            unicodedata.normalize("NFD", value.casefold())
            for value in artifact_paths.values()
        }
        & {
            unicodedata.normalize("NFD", value.casefold())
            for value in _exact_drain_archive_paths(
                artifact_paths["progress_artifact_path"]
            )
        }
    ):
        raise OperationRecoveryError(
            "operation-recovery exact drain plan is invalid"
        )
    body = {
        "schema_version": schema_version,
        "kind": "operation-recovery-exact-drain-plan",
        "action": "drain-exact-operation-cohort",
        "authority": "unapproved-plan",
        "mutation_authorized": False,
        "candidate_release": release,
        "installation_authority": authority,
        "cohort": cohort,
        "live_snapshot": snapshot,
        "cohort_digest": cohort["cohort_digest"],
        "snapshot_digest": snapshot["snapshot_digest"],
        "pre_generation": snapshot["generation_before"],
        "selected_operations": selected,
        "selected_operation_count": len(selected),
        "selected_status_counts": status_counts,
        "selected_type_counts": type_counts,
        "selected_row_set_digest": _sha(
            plan["selected_row_set_digest"],
            "exact drain row-set digest",
        ),
        "preserved_status_counts": preserved,
        "rollback_backup": backup,
        **artifact_paths,
        "provider_policy_digest": _sha(
            plan["provider_policy_digest"],
            "provider policy digest",
        ),
        "effective_profile_digest": _sha(
            plan["effective_profile_digest"],
            "effective profile digest",
        ),
        "worker_runtime_digest": _sha(
            plan["worker_runtime_digest"],
            "worker runtime digest",
        ),
        "worker_max_attempts": EXACT_DRAIN_WORKER_MAX_ATTEMPTS,
        "worker_max_retries": EXACT_DRAIN_WORKER_MAX_RETRIES,
        **(
            {}
            if schema_version == 1
            else {
                "evidence_observed_at": evidence_observed_at,
                "evidence_max_age_seconds": evidence_max_age_seconds,
                "transaction_timeout_seconds": transaction_timeout_seconds,
                **(
                    {"execution_lease_seconds": execution_lease_seconds}
                    if schema_version in {2, 3, 4, 5, 6, 7, 8, 9}
                    else {
                        "execution_window": execution_window,
                        "recovery_context": recovery_context,
                        "recovery_context_digest": (
                            recovery_context_digest
                        ),
                        **(
                            {
                                "checkpoint_continuation_handoff": (
                                    plan["checkpoint_continuation_handoff"]
                                ),
                                "checkpoint_continuation_handoff_digest": (
                                    _sha(
                                        plan[
                                            "checkpoint_continuation_handoff_digest"
                                        ],
                                        "checkpoint continuation handoff digest",
                                    )
                                ),
                            }
                            if schema_version == 14
                            else {}
                        ),
                    }
                ),
            }
        ),
        **(
            {}
            if schema_version
            not in {3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14}
            else {
                "phase_one_statement_timeout_seconds": (
                    phase_one_statement_timeout_seconds
                ),
                "phase_one_timeout_seconds": phase_one_timeout_seconds,
                "phase_repair_contract_digest": (
                    phase_repair_contract_digest
                ),
            }
        ),
        **(
            {}
            if schema_version
            not in {6, 7, 8, 9, 10, 11, 12, 13, 14}
            else {
                "phase_one_client_timeout_seconds": (
                    phase_one_client_timeout_seconds
                ),
                "progress_schema_version": progress_schema_version,
                "failure_evidence_contract_digest": (
                    failure_evidence_contract_digest
                ),
            }
        ),
        **(
            {}
            if schema_version not in {11, 12, 13, 14}
            else {
                "operation_attempt_timeout_seconds": (
                    operation_attempt_timeout_seconds
                ),
                "phase_one_deadline_anchor": phase_one_deadline_anchor,
                "phase_one_nested_stage_prefixes": (
                    phase_one_nested_stage_prefixes
                ),
                "provider_timeout_contract": provider_timeout_contract,
                **(
                    {
                        "operation_attempt_timeout_disposition": (
                            operation_attempt_timeout_disposition
                        )
                    }
                    if schema_version in {12, 13, 14}
                    else {}
                ),
            }
        ),
        **capability_fields,
        "created_at": created_at,
        "expires_at": expires_at,
    }
    if _sha(plan["plan_digest"], "exact drain plan digest") != digest(body):
        raise OperationRecoveryError(
            "operation-recovery exact drain plan digest differs"
        )
    return {**body, "plan_digest": plan["plan_digest"]}


def _post_abort_worker_digest(reference_plan_digest: str) -> str:
    import hashlib

    return hashlib.sha256(
        (
            "operation-recovery-exact-drain-"
            f"{reference_plan_digest[:12]}"
        ).encode("utf-8")
    ).hexdigest()


def _post_abort_reference_application_journal(
    value: Any,
    reference_plan: Mapping[str, Any],
    reference_authorization: Mapping[str, Any],
) -> dict[str, Any]:
    journal = _closed(
        _normalized(value),
        POST_ABORT_REFERENCE_JOURNAL_KEYS,
        "post-abort reference application journal",
    )
    body = {
        "schema_version": _integer(
            journal["schema_version"],
            "post-abort reference journal schema version",
        ),
        "kind": _text(
            journal["kind"],
            "post-abort reference journal kind",
            maximum=128,
        ),
        "plan_digest": _sha(
            journal["plan_digest"],
            "post-abort reference journal plan digest",
        ),
        "authorization_receipt_digest": _sha(
            journal["authorization_receipt_digest"],
            "post-abort reference authorization digest",
        ),
        "started_at": _integer(
            journal["started_at"],
            "post-abort reference journal started-at",
        ),
        "worker_pid": _integer(
            journal["worker_pid"],
            "post-abort reference journal worker pid",
        ),
        "worker_start_time": _text(
            journal["worker_start_time"],
            "post-abort reference journal worker start time",
            maximum=128,
        ),
        "worker_attempt": _integer(
            journal["worker_attempt"],
            "post-abort reference journal worker attempt",
        ),
    }
    receipt_digest = _sha(
        journal["receipt_digest"],
        "post-abort reference journal digest",
    )
    if (
        body["schema_version"] != 1
        or body["kind"]
        != "operation-recovery-exact-drain-application-journal"
        or body["plan_digest"] != reference_plan["plan_digest"]
        or body["authorization_receipt_digest"]
        != reference_authorization["receipt_digest"]
        or body["started_at"] != reference_authorization["authorized_at"]
        or not 1 <= body["worker_pid"] <= (1 << 31) - 1
        or not body["worker_start_time"]
        or body["worker_start_time"]
        != " ".join(body["worker_start_time"].split())
        or not 1
        <= body["worker_attempt"]
        <= reference_plan["worker_max_attempts"] + 1
        or receipt_digest != digest(body)
    ):
        raise OperationRecoveryError(
            "operation-recovery post-abort reference journal is invalid"
        )
    return {**body, "receipt_digest": receipt_digest}


def _post_abort_reference_application_authorization(
    value: Any,
    reference_plan: Mapping[str, Any],
) -> dict[str, Any]:
    return dict(
        verify_exact_drain_authorization_receipt(
            value,
            plan=reference_plan,
        )
    )


def _post_abort_selected(
    snapshot: Mapping[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "operation_id": item["operation_id"],
            "operation_type": item["operation_type"],
            "expected_status": item["current_status"],
            "row_digest": item["row_digest"],
            "task_payload_digest": item["task_payload_digest"],
        }
        for item in snapshot["operations"]
        if item["current_status"] in {"processing", "failed"}
    ]


def _post_abort_type_counts(
    selected: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    return {
        operation_type: sum(
            item["operation_type"] == operation_type for item in selected
        )
        for operation_type in EXPECTED_OPERATION_COUNTS
        if any(
            item["operation_type"] == operation_type for item in selected
        )
    }


def _post_abort_v5_completed_selected_matches(
    item: Mapping[str, Any],
    *,
    worker_digest: str,
) -> bool:
    return (
        item["current_status"] == "completed"
        and item["operation_type"] == "consolidation"
        and item["retry_count"] == 3
        and item["worker_id_present"] is True
        and item["worker_id_digest"] == worker_digest
        and item["claimed_at"] is not None
        and item["completed_at"] is not None
        and item["error_category"] == "none"
        and item["error_digest"] is None
    )


def _post_abort_v9_completed_selected_matches(
    item: Mapping[str, Any],
    *,
    worker_digest: str,
) -> bool:
    return (
        item["current_status"] == "completed"
        and item["operation_type"] == "retain"
        and item["retry_count"] == 3
        and item["worker_id_present"] is True
        and item["worker_id_digest"] == worker_digest
        and item["claimed_at"] is not None
        and item["completed_at"] is not None
        and item["error_category"] == "provider_transport"
        and item["error_digest"] is not None
    )


def _post_abort_v10_retry_recovery(
    reference_plan: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
    current: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    recovery_epoch_before = (
        reference_plan["recovery_context"]["recovery_epoch"]
        if reference_plan["schema_version"] in {10, 11, 12}
        else 0
    )
    if recovery_epoch_before != 0:
        raise OperationRecoveryError(
            "operation-recovery post-abort retry recovery is invalid"
        )
    ordinary_retry_ceiling = reference_plan["worker_max_retries"]
    ordinary_attempt_ceiling = reference_plan["worker_max_attempts"]
    maximum_cumulative_attempts = ordinary_attempt_ceiling * 2
    operations = []
    for item in selected:
        row = current[item["operation_id"]]
        retry_count_before = row["retry_count"]
        reset_applied = item["expected_status"] == "failed"
        retry_count_after = 0 if reset_applied else retry_count_before
        attempts_consumed_before = retry_count_before + int(
            item["expected_status"] in {"processing", "failed"}
        )
        attempts_available_after = (
            ordinary_attempt_ceiling - retry_count_after
        )
        cumulative_attempt_ceiling = (
            attempts_consumed_before + attempts_available_after
        )
        if (
            not 0 <= retry_count_before <= ordinary_retry_ceiling
            or attempts_available_after < 1
            or cumulative_attempt_ceiling > maximum_cumulative_attempts
        ):
            raise OperationRecoveryError(
                "operation-recovery post-abort retry recovery is invalid"
            )
        operations.append(
            {
                "operation_id": item["operation_id"],
                "expected_status": item["expected_status"],
                "retry_count_before": retry_count_before,
                "retry_count_after": retry_count_after,
                "attempts_consumed_before": attempts_consumed_before,
                "attempts_available_after": attempts_available_after,
                "cumulative_attempt_ceiling": cumulative_attempt_ceiling,
                "reset_applied": reset_applied,
            }
        )
    failed_reset_count = sum(item["reset_applied"] for item in operations)
    body = {
        "schema_version": 1,
        "kind": "operation-recovery-post-abort-retry-recovery",
        "recovery_epoch_before": recovery_epoch_before,
        "recovery_epoch_after": 1,
        "recovery_epoch_ceiling": 1,
        "ordinary_retry_ceiling": ordinary_retry_ceiling,
        "ordinary_attempt_ceiling": ordinary_attempt_ceiling,
        "maximum_cumulative_attempts": maximum_cumulative_attempts,
        "operation_count": len(operations),
        "failed_reset_count": failed_reset_count,
        "operations": operations,
        "operation_set_digest": digest(operations),
    }
    return body


def _verified_post_abort_retry_recovery(
    value: Any,
    *,
    expected_schema_version: int,
) -> dict[str, Any]:
    if expected_schema_version not in {1, 2, 3, 4}:
        raise OperationRecoveryError(
            "operation-recovery post-abort retry recovery is invalid"
        )
    retry_recovery_value = _closed(
        _normalized(value),
        (
            POST_ABORT_V10_RETRY_RECOVERY_KEYS
            if expected_schema_version == 1
            else (
                POST_ABORT_V11_RETRY_RECOVERY_KEYS
                if expected_schema_version == 2
                else (
                    POST_ABORT_V12_RETRY_RECOVERY_KEYS
                    if expected_schema_version == 3
                    else POST_ABORT_V13_RETRY_RECOVERY_KEYS
                )
            )
        ),
        "post-abort retry recovery",
    )
    operations_value = retry_recovery_value.get("operations")
    if not isinstance(operations_value, list):
        raise OperationRecoveryError(
            "operation-recovery post-abort retry recovery is invalid"
        )
    operations = []
    operation_keys = (
        POST_ABORT_V10_RETRY_OPERATION_KEYS
        if expected_schema_version == 1
        else (
            POST_ABORT_V11_RETRY_OPERATION_KEYS
            if expected_schema_version == 2
            else (
                POST_ABORT_V12_RETRY_OPERATION_KEYS
                if expected_schema_version == 3
                else POST_ABORT_V13_RETRY_OPERATION_KEYS
            )
        )
    )
    for item_value in operations_value:
        item = _closed(
            _normalized(item_value),
            operation_keys,
            "post-abort retry operation",
        )
        reset_applied = item["reset_applied"]
        if type(reset_applied) is not bool:
            raise OperationRecoveryError(
                "operation-recovery post-abort retry recovery is invalid"
            )
        normalized_item = {
            "operation_id": _operation_id(item["operation_id"]),
            "expected_status": _text(
                item["expected_status"],
                "post-abort retry operation status",
                maximum=32,
            ),
            **(
                {}
                if expected_schema_version == 1
                else {
                    "reference_retry_count": _integer(
                        item["reference_retry_count"],
                        "post-abort reference retry count",
                    )
                }
            ),
            "retry_count_before": _integer(
                item["retry_count_before"],
                "post-abort retry count before",
            ),
            "retry_count_after": _integer(
                item["retry_count_after"],
                "post-abort retry count after",
            ),
            **(
                {}
                if expected_schema_version == 1
                else {
                    "prior_attempts_consumed": _integer(
                        item["prior_attempts_consumed"],
                        "post-abort prior attempts consumed",
                    ),
                    "attempts_consumed_during_reference": _integer(
                        item["attempts_consumed_during_reference"],
                        "post-abort attempts consumed during reference",
                    ),
                }
            ),
            "attempts_consumed_before": _integer(
                item["attempts_consumed_before"],
                "post-abort attempts consumed before",
            ),
            "attempts_available_after": _integer(
                item["attempts_available_after"],
                "post-abort attempts available after",
            ),
            "cumulative_attempt_ceiling": _integer(
                item["cumulative_attempt_ceiling"],
                "post-abort cumulative attempt ceiling",
            ),
            "reset_applied": reset_applied,
        }
        operations.append(normalized_item)
    body = {
        "schema_version": _integer(
            retry_recovery_value["schema_version"],
            "post-abort retry recovery schema version",
        ),
        "kind": _text(
            retry_recovery_value["kind"],
            "post-abort retry recovery kind",
        ),
        "recovery_epoch_before": _integer(
            retry_recovery_value["recovery_epoch_before"],
            "post-abort recovery epoch before",
        ),
        "recovery_epoch_after": _integer(
            retry_recovery_value["recovery_epoch_after"],
            "post-abort recovery epoch after",
        ),
        "recovery_epoch_ceiling": _integer(
            retry_recovery_value["recovery_epoch_ceiling"],
            "post-abort recovery epoch ceiling",
        ),
        **(
            {}
            if expected_schema_version != 4
            else {
                "reconciliation_cycle_before": _integer(
                    retry_recovery_value["reconciliation_cycle_before"],
                    "post-terminal reconciliation cycle before",
                ),
                "reconciliation_cycle_after": _integer(
                    retry_recovery_value["reconciliation_cycle_after"],
                    "post-terminal reconciliation cycle after",
                ),
                "reconciliation_cycle_ceiling": _integer(
                    retry_recovery_value["reconciliation_cycle_ceiling"],
                    "post-terminal reconciliation cycle ceiling",
                ),
            }
        ),
        "ordinary_retry_ceiling": _integer(
            retry_recovery_value["ordinary_retry_ceiling"],
            "post-abort ordinary retry ceiling",
        ),
        "ordinary_attempt_ceiling": _integer(
            retry_recovery_value["ordinary_attempt_ceiling"],
            "post-abort ordinary attempt ceiling",
        ),
        "maximum_cumulative_attempts": _integer(
            retry_recovery_value["maximum_cumulative_attempts"],
            "post-abort maximum cumulative attempts",
        ),
        "operation_count": _integer(
            retry_recovery_value["operation_count"],
            "post-abort retry operation count",
        ),
        "failed_reset_count": _integer(
            retry_recovery_value["failed_reset_count"],
            "post-abort failed reset count",
        ),
        **(
            {}
            if expected_schema_version == 1
            else {
                "prior_retry_recovery": _verified_post_abort_retry_recovery(
                    retry_recovery_value["prior_retry_recovery"],
                    expected_schema_version=expected_schema_version - 1,
                ),
                "prior_retry_recovery_digest": _sha(
                    retry_recovery_value["prior_retry_recovery_digest"],
                    "post-abort prior retry recovery digest",
                ),
            }
        ),
        "operations": operations,
        "operation_set_digest": _sha(
            retry_recovery_value["operation_set_digest"],
            "post-abort retry operation set digest",
        ),
    }
    invalid = (
        body["schema_version"] != expected_schema_version
        or body["operation_count"] != len(operations)
        or body["failed_reset_count"]
        != sum(item["reset_applied"] for item in operations)
        or len({item["operation_id"] for item in operations})
        != len(operations)
        or body["operation_set_digest"] != digest(operations)
    )
    if expected_schema_version in {2, 3, 4}:
        invalid = invalid or body["prior_retry_recovery_digest"] != digest(
            body["prior_retry_recovery"]
        )
    if expected_schema_version == 4:
        invalid = invalid or (
            body["kind"]
            != "operation-recovery-post-terminal-reconciliation-retry-recovery"
            or body["recovery_epoch_before"] != 3
            or body["recovery_epoch_after"] != 3
            or body["recovery_epoch_ceiling"] != 3
            or body["reconciliation_cycle_before"] != 0
            or body["reconciliation_cycle_after"] != 1
            or body["reconciliation_cycle_ceiling"] != 1
        )
    else:
        invalid = invalid or body["kind"] != (
            "operation-recovery-post-abort-retry-recovery"
        )
    if invalid:
        raise OperationRecoveryError(
            "operation-recovery post-abort retry recovery is invalid"
        )
    return body


def _post_abort_v11_retry_recovery(
    reference_plan: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
    current: Mapping[str, Mapping[str, Any]],
    prior_retry_recovery_value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    context = reference_plan.get("recovery_context")
    if (
        reference_plan["schema_version"] not in {10, 11, 12}
        or not isinstance(context, Mapping)
        or context.get("origin") != "post-abort"
        or context.get("schema_version") not in {1, 2}
        or context.get("recovery_epoch") != context.get("schema_version")
        or (
            context.get("schema_version") == 2
            and reference_plan["schema_version"] not in {11, 12}
        )
        or prior_retry_recovery_value is None
    ):
        raise OperationRecoveryError(
            "operation-recovery post-abort retry recovery is invalid"
        )
    prior_retry_recovery = _verified_post_abort_retry_recovery(
        prior_retry_recovery_value,
        expected_schema_version=context["schema_version"],
    )
    prior_digest = digest(prior_retry_recovery)
    reference_selected = {
        item["operation_id"]: item
        for item in reference_plan["selected_operations"]
    }
    reference_snapshot = {
        item["operation_id"]: item
        for item in reference_plan["live_snapshot"]["operations"]
    }
    prior_by_id = {
        item["operation_id"]: item
        for item in prior_retry_recovery["operations"]
    }
    selected_ids = {item["operation_id"] for item in selected}
    ordinary_retry_ceiling = reference_plan["worker_max_retries"]
    ordinary_attempt_ceiling = reference_plan["worker_max_attempts"]
    recovery_epoch_before = context["recovery_epoch"]
    recovery_epoch_after = recovery_epoch_before + 1
    release_only_recovery = recovery_epoch_before == 2
    maximum_cumulative_attempts = (
        ordinary_attempt_ceiling * (recovery_epoch_after + 1)
    )
    if (
        context.get("retry_recovery_digest") != prior_digest
        or prior_retry_recovery["recovery_epoch_before"]
        != recovery_epoch_before - 1
        or prior_retry_recovery["recovery_epoch_after"]
        != recovery_epoch_before
        or prior_retry_recovery["recovery_epoch_ceiling"]
        != recovery_epoch_before
        or prior_retry_recovery["ordinary_retry_ceiling"]
        != ordinary_retry_ceiling
        or prior_retry_recovery["ordinary_attempt_ceiling"]
        != ordinary_attempt_ceiling
        or prior_retry_recovery["maximum_cumulative_attempts"]
        != ordinary_attempt_ceiling * (recovery_epoch_before + 1)
        or (
            release_only_recovery
            and not selected_ids.issubset(reference_selected)
        )
        or (
            not release_only_recovery
            and selected_ids != set(reference_selected)
        )
        or not set(prior_by_id).issubset(reference_selected)
        or digest(sorted(prior_by_id))
        != context.get("post_abort_selected_operation_ids_digest")
        or digest(sorted(reference_selected))
        != context.get("selected_operation_ids_digest")
        or any(
            item["expected_status"]
            not in (
                {"failed", "pending", "processing"}
                if release_only_recovery
                else {"failed"}
            )
            for item in selected
        )
    ):
        raise OperationRecoveryError(
            "operation-recovery post-abort retry recovery is invalid"
        )
    operations = []
    for item in selected:
        operation_id = item["operation_id"]
        row = current[operation_id]
        prior = prior_by_id.get(operation_id)
        reference_retry_count = reference_snapshot[operation_id][
            "retry_count"
        ]
        retry_count_before = row["retry_count"]
        prior_attempts_consumed = (
            prior["attempts_consumed_before"]
            if prior is not None
            else reference_retry_count
        )
        reset_applied = item["expected_status"] == "failed"
        attempts_consumed_during_reference = (
            retry_count_before
            - reference_retry_count
            + int(item["expected_status"] in {"processing", "failed"})
        )
        attempts_consumed_before = (
            prior_attempts_consumed
            + attempts_consumed_during_reference
        )
        retry_count_after = 0 if reset_applied else retry_count_before
        attempts_available_after = (
            ordinary_attempt_ceiling - retry_count_after
        )
        cumulative_attempt_ceiling = (
            attempts_consumed_before + attempts_available_after
        )
        if (
            (
                prior is not None
                and (
                    prior["retry_count_after"] != reference_retry_count
                    or prior["cumulative_attempt_ceiling"]
                    > ordinary_attempt_ceiling
                    * (recovery_epoch_before + 1)
                )
            )
            or not 0
            <= reference_retry_count
            <= retry_count_before
            <= ordinary_retry_ceiling
            or attempts_consumed_during_reference
            < int(item["expected_status"] in {"processing", "failed"})
            or attempts_available_after < 1
            or cumulative_attempt_ceiling > maximum_cumulative_attempts
        ):
            raise OperationRecoveryError(
                "operation-recovery post-abort retry recovery is invalid"
            )
        operations.append(
            {
                "operation_id": operation_id,
                "expected_status": item["expected_status"],
                "reference_retry_count": reference_retry_count,
                "retry_count_before": retry_count_before,
                "retry_count_after": retry_count_after,
                "prior_attempts_consumed": prior_attempts_consumed,
                "attempts_consumed_during_reference": (
                    attempts_consumed_during_reference
                ),
                "attempts_consumed_before": attempts_consumed_before,
                "attempts_available_after": attempts_available_after,
                "cumulative_attempt_ceiling": cumulative_attempt_ceiling,
                "reset_applied": reset_applied,
            }
        )
    return {
        "schema_version": recovery_epoch_after,
        "kind": "operation-recovery-post-abort-retry-recovery",
        "recovery_epoch_before": recovery_epoch_before,
        "recovery_epoch_after": recovery_epoch_after,
        "recovery_epoch_ceiling": recovery_epoch_after,
        "ordinary_retry_ceiling": ordinary_retry_ceiling,
        "ordinary_attempt_ceiling": ordinary_attempt_ceiling,
        "maximum_cumulative_attempts": maximum_cumulative_attempts,
        "operation_count": len(operations),
        "failed_reset_count": sum(
            item["reset_applied"] for item in operations
        ),
        "prior_retry_recovery": prior_retry_recovery,
        "prior_retry_recovery_digest": prior_digest,
        "operations": operations,
        "operation_set_digest": digest(operations),
    }


def _retry_lineage_operation(
    retry_recovery: Mapping[str, Any],
    operation_id: str,
) -> Mapping[str, Any] | None:
    """Return the newest recorded attempt ledger entry for one operation."""
    match = next(
        (
            item
            for item in retry_recovery["operations"]
            if item["operation_id"] == operation_id
        ),
        None,
    )
    if match is not None:
        return match
    prior = retry_recovery.get("prior_retry_recovery")
    return (
        _retry_lineage_operation(prior, operation_id)
        if isinstance(prior, Mapping)
        else None
    )


def _post_terminal_reconciliation_retry_recovery(
    reference_plan: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
    current: Mapping[str, Mapping[str, Any]],
    prior_retry_recovery_value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Authorize one bounded cycle without inventing recovery epoch four."""
    context = reference_plan.get("recovery_context")
    if (
        reference_plan.get("schema_version") != 12
        or not isinstance(context, Mapping)
        or context.get("schema_version") != 3
        or context.get("origin") != "post-abort"
        or context.get("recovery_epoch") != 3
        or prior_retry_recovery_value is None
    ):
        raise OperationRecoveryError(
            "operation-recovery post-terminal reconciliation is invalid"
        )
    prior = _verified_post_abort_retry_recovery(
        prior_retry_recovery_value,
        expected_schema_version=3,
    )
    prior_digest = digest(prior)
    reference_selected = {
        item["operation_id"]: item
        for item in reference_plan["selected_operations"]
    }
    reference_snapshot = {
        item["operation_id"]: item
        for item in reference_plan["live_snapshot"]["operations"]
    }
    selected_ids = {item["operation_id"] for item in selected}
    ordinary_retry_ceiling = reference_plan["worker_max_retries"]
    ordinary_attempt_ceiling = reference_plan["worker_max_attempts"]
    maximum_cumulative_attempts = ordinary_attempt_ceiling * 5
    if (
        context.get("retry_recovery_digest") != prior_digest
        or prior["recovery_epoch_before"] != 2
        or prior["recovery_epoch_after"] != 3
        or prior["recovery_epoch_ceiling"] != 3
        or prior["ordinary_retry_ceiling"] != ordinary_retry_ceiling
        or prior["ordinary_attempt_ceiling"] != ordinary_attempt_ceiling
        or prior["maximum_cumulative_attempts"]
        != ordinary_attempt_ceiling * 4
        or selected_ids != set(reference_selected)
        or any(item["expected_status"] != "failed" for item in selected)
    ):
        raise OperationRecoveryError(
            "operation-recovery post-terminal reconciliation is invalid"
        )
    operations = []
    for item in selected:
        operation_id = item["operation_id"]
        row = current[operation_id]
        reference_retry_count = reference_snapshot[operation_id][
            "retry_count"
        ]
        retry_count_before = row["retry_count"]
        lineage = _retry_lineage_operation(prior, operation_id)
        prior_attempts_consumed = reference_retry_count
        if lineage is not None:
            prior_attempts_consumed = lineage["attempts_consumed_before"] + (
                reference_retry_count - lineage["retry_count_after"]
            )
        attempts_consumed_during_reference = (
            retry_count_before - reference_retry_count + 1
        )
        attempts_consumed_before = (
            prior_attempts_consumed + attempts_consumed_during_reference
        )
        attempts_available_after = ordinary_attempt_ceiling
        cumulative_attempt_ceiling = (
            attempts_consumed_before + attempts_available_after
        )
        if (
            not 0
            <= reference_retry_count
            <= retry_count_before
            <= ordinary_retry_ceiling
            or prior_attempts_consumed < 0
            or attempts_consumed_during_reference < 1
            or cumulative_attempt_ceiling > maximum_cumulative_attempts
        ):
            raise OperationRecoveryError(
                "operation-recovery post-terminal reconciliation is invalid"
            )
        operations.append(
            {
                "operation_id": operation_id,
                "expected_status": "failed",
                "reference_retry_count": reference_retry_count,
                "retry_count_before": retry_count_before,
                "retry_count_after": 0,
                "prior_attempts_consumed": prior_attempts_consumed,
                "attempts_consumed_during_reference": (
                    attempts_consumed_during_reference
                ),
                "attempts_consumed_before": attempts_consumed_before,
                "attempts_available_after": attempts_available_after,
                "cumulative_attempt_ceiling": cumulative_attempt_ceiling,
                "reset_applied": True,
            }
        )
    return {
        "schema_version": 4,
        "kind": (
            "operation-recovery-post-terminal-reconciliation-retry-recovery"
        ),
        "recovery_epoch_before": 3,
        "recovery_epoch_after": 3,
        "recovery_epoch_ceiling": 3,
        "reconciliation_cycle_before": 0,
        "reconciliation_cycle_after": 1,
        "reconciliation_cycle_ceiling": 1,
        "ordinary_retry_ceiling": ordinary_retry_ceiling,
        "ordinary_attempt_ceiling": ordinary_attempt_ceiling,
        "maximum_cumulative_attempts": maximum_cumulative_attempts,
        "operation_count": len(operations),
        "failed_reset_count": len(operations),
        "prior_retry_recovery": prior,
        "prior_retry_recovery_digest": prior_digest,
        "operations": operations,
        "operation_set_digest": digest(operations),
    }


def _post_abort_timestamp(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.timestamp()
    except (OSError, OverflowError, TypeError, ValueError):
        return None


def _post_abort_v10_contract(
    reference_plan: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    schema_version: int,
    prior_retry_recovery: Mapping[str, Any] | None,
) -> tuple[
    list[dict[str, Any]],
    str,
    dict[str, int],
    dict[str, int],
    dict[str, int],
    dict[str, Any],
]:
    worker_digest = _post_abort_worker_digest(reference_plan["plan_digest"])
    reference_selected = {
        item["operation_id"]: item
        for item in reference_plan["selected_operations"]
    }
    reference_snapshot = {
        item["operation_id"]: item
        for item in reference_plan["live_snapshot"]["operations"]
    }
    current = {
        item["operation_id"]: item for item in snapshot["operations"]
    }
    if (
        schema_version in {11, 12, 13}
        and snapshot["installation_authority"].get("schema_version") != 2
    ):
        raise OperationRecoveryError(
            "operation-recovery schema-11 recovery requires verified "
            "rebind authority"
        )
    reference_preserved_ids = set(reference_snapshot) - set(reference_selected)
    if (
        snapshot["cohort_digest"] != reference_plan["cohort_digest"]
        or not _post_abort_installation_authority_matches(
            reference_plan["installation_authority"],
            snapshot["installation_authority"],
            schema_version=schema_version,
        )
        or snapshot["generation_before"] != snapshot["generation_after"]
        or set(current) != set(reference_snapshot)
        or snapshot["status_counts"].get("cancelled", 0)
        or any(
            current[operation_id]["operation_type"]
            != reference_snapshot[operation_id]["operation_type"]
            or current[operation_id]["task_payload_digest"]
            != reference_snapshot[operation_id]["task_payload_digest"]
            for operation_id in current
        )
        or any(
            current[operation_id]["current_status"] != "completed"
            or current[operation_id]["row_digest"]
            != reference_snapshot[operation_id]["row_digest"]
            for operation_id in reference_preserved_ids
        )
    ):
        raise OperationRecoveryError(
            "operation-recovery post-abort row set is invalid"
        )

    selected = []
    for operation_id in sorted(reference_selected):
        row = current[operation_id]
        status = row["current_status"]
        owned = (
            row["worker_id_present"] is True
            and row["worker_id_digest"] == worker_digest
            and row["claimed_at"] is not None
        )
        wholly_unowned = (
            row["worker_id_present"] is False
            and row["worker_id_digest"] is None
            and row["claimed_at"] is None
        )
        if status in {"failed", "pending", "processing"} and owned:
            if (
                (status == "failed") != (row["completed_at"] is not None)
                or not 0
                <= row["retry_count"]
                <= reference_plan["worker_max_retries"]
            ):
                raise OperationRecoveryError(
                    "operation-recovery post-abort row set is invalid"
                )
            selected.append(
                {
                    "operation_id": operation_id,
                    "operation_type": row["operation_type"],
                    "expected_status": status,
                    "row_digest": row["row_digest"],
                    "task_payload_digest": row["task_payload_digest"],
                }
            )
        elif status == "pending" and wholly_unowned:
            reference_row = reference_snapshot[operation_id]
            reference_wholly_unowned = (
                reference_row["worker_id_present"] is False
                and reference_row["worker_id_digest"] is None
                and reference_row["claimed_at"] is None
            )
            reference_updated_at = _post_abort_timestamp(
                reference_row["updated_at"]
            )
            current_updated_at = _post_abort_timestamp(row["updated_at"])
            next_retry_at = (
                None
                if row["next_retry_at"] is None
                else _post_abort_timestamp(row["next_retry_at"])
            )
            released_retry_checkpoint_advanced = (
                schema_version == 12
                and row["result_metadata_digest"] is not None
                and row["result_metadata_digest"]
                != reference_row["result_metadata_digest"]
                and reference_row["retry_count"] < row["retry_count"]
                and row["error_category"] != "none"
                and row["error_digest"] is not None
                and row["next_retry_at"] is not None
                and next_retry_at is not None
                and next_retry_at <= snapshot["observed_at"]
                and reference_updated_at is not None
                and current_updated_at is not None
                and current_updated_at > reference_updated_at
            )
            if (
                reference_row["current_status"] != "pending"
                or not reference_wholly_unowned
                or reference_row["completed_at"] is not None
                or row["completed_at"] is not None
                or row["created_at"] != reference_row["created_at"]
                or (
                    schema_version == 12
                    and row["row_digest"] != reference_row["row_digest"]
                    and not released_retry_checkpoint_advanced
                )
                or (
                    row["result_metadata_digest"]
                    != reference_row["result_metadata_digest"]
                    and not released_retry_checkpoint_advanced
                )
                or row["task_payload_present"]
                is not reference_row["task_payload_present"]
                or not 0
                <= reference_row["retry_count"]
                <= row["retry_count"]
                <= reference_plan["worker_max_retries"]
                or reference_updated_at is None
                or current_updated_at is None
                or (
                    row["row_digest"] != reference_row["row_digest"]
                    and current_updated_at <= reference_updated_at
                )
                or (
                    row["next_retry_at"] is not None
                    and (
                        next_retry_at is None
                        or next_retry_at > snapshot["observed_at"]
                    )
                )
            ):
                raise OperationRecoveryError(
                    "operation-recovery post-abort row set is invalid"
                )
        elif status == "completed" and owned:
            if (
                row["completed_at"] is None
                or not 0
                <= row["retry_count"]
                <= reference_plan["worker_max_retries"]
            ):
                raise OperationRecoveryError(
                    "operation-recovery post-abort row set is invalid"
                )
        else:
            raise OperationRecoveryError(
                "operation-recovery post-abort row set is invalid"
            )
    if not selected:
        raise OperationRecoveryError(
            "operation-recovery post-abort row set is invalid"
        )
    selected_ids = {item["operation_id"] for item in selected}
    preserved_ids = set(current) - selected_ids
    selected_status_counts = {
        status: sum(item["expected_status"] == status for item in selected)
        for status in ("failed", "pending", "processing")
    }
    selected_status_counts = {
        status: count
        for status, count in selected_status_counts.items()
        if count
    }
    selected_type_counts = _post_abort_type_counts(selected)
    preserved_status_counts = {
        status: sum(
            current[operation_id]["current_status"] == status
            for operation_id in preserved_ids
        )
        for status in ("completed", "pending")
    }
    preserved_status_counts = {
        status: count
        for status, count in preserved_status_counts.items()
        if count
    }
    retry_recovery = (
        _post_terminal_reconciliation_retry_recovery(
            reference_plan,
            selected,
            current,
            prior_retry_recovery,
        )
        if schema_version == 13
        else _post_abort_v10_retry_recovery(
            reference_plan,
            selected,
            current,
        )
        if schema_version == 10 or prior_retry_recovery is None
        else _post_abort_v11_retry_recovery(
            reference_plan,
            selected,
            current,
            prior_retry_recovery,
        )
    )
    derived = {
        "selection_contract_digest": (
            POST_ABORT_V10_SELECTION_CONTRACT_DIGEST
        ),
        "selected_checkpoint_set_digest": digest(
            [
                {
                    "operation_id": operation_id,
                    "result_metadata_digest": current[operation_id][
                        "result_metadata_digest"
                    ],
                }
                for operation_id in sorted(selected_ids)
            ]
        ),
        "preserved_row_set_digest": digest(
            [
                {
                    "operation_id": operation_id,
                    "row_digest": current[operation_id]["row_digest"],
                }
                for operation_id in sorted(preserved_ids)
            ]
        ),
        "retry_recovery": retry_recovery,
        "retry_recovery_digest": digest(retry_recovery),
    }
    return (
        selected,
        worker_digest,
        selected_status_counts,
        selected_type_counts,
        preserved_status_counts,
        derived,
    )


def _post_abort_contract(
    reference_plan: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    schema_version: int,
    prior_retry_recovery: Mapping[str, Any] | None = None,
) -> tuple[
    list[dict[str, Any]],
    str,
    dict[str, int],
    dict[str, int],
    dict[str, int],
    dict[str, Any],
]:
    recovery_context = reference_plan.get("recovery_context")
    recovery_epoch = (
        recovery_context.get("recovery_epoch")
        if isinstance(recovery_context, Mapping)
        else None
    )
    recovery_context_schema = (
        recovery_context.get("schema_version")
        if isinstance(recovery_context, Mapping)
        else None
    )
    if (
        schema_version == 13
        and (
            prior_retry_recovery is None
            or reference_plan.get("schema_version") != 12
            or recovery_context_schema != 3
            or recovery_epoch != 3
            or recovery_context.get("origin") != "post-abort"
        )
    ) or (
        schema_version == 12
        and (
            prior_retry_recovery is None
            or recovery_context_schema != 2
            or recovery_epoch != 2
        )
    ) or (
        schema_version == 11
        and prior_retry_recovery is not None
        and (recovery_context_schema != 1 or recovery_epoch != 1)
    ):
        raise OperationRecoveryError(
            "operation-recovery post-abort schema does not match recovery epoch"
        )
    if (
        schema_version not in {10, 11, 12, 13}
        and reference_plan["schema_version"] in {10, 11, 12, 13}
    ):
        raise OperationRecoveryError(
            "operation-recovery legacy post-abort schema cannot reference "
            "schema 10 or later"
        )
    if schema_version in {10, 11, 12, 13}:
        if schema_version == 10 and (
            prior_retry_recovery is not None
            or reference_plan["schema_version"] in {11, 12}
        ):
            raise OperationRecoveryError(
                "operation-recovery post-abort retry recovery is invalid"
            )
        return _post_abort_v10_contract(
            reference_plan,
            snapshot,
            schema_version=schema_version,
            prior_retry_recovery=prior_retry_recovery,
        )
    worker_digest = _post_abort_worker_digest(reference_plan["plan_digest"])
    selected = _post_abort_selected(snapshot)
    if schema_version in {7, 9}:
        selected.extend(
            {
                "operation_id": item["operation_id"],
                "operation_type": item["operation_type"],
                "expected_status": item["current_status"],
                "row_digest": item["row_digest"],
                "task_payload_digest": item["task_payload_digest"],
            }
            for item in snapshot["operations"]
            if item["current_status"] == "pending"
            and item["worker_id_present"] is True
            and item["worker_id_digest"] == worker_digest
            and item["claimed_at"] is not None
        )
        selected.sort(key=lambda item: item["operation_id"])
    selected_status_counts = {
        status: sum(item["expected_status"] == status for item in selected)
        for status in (
            ("failed", "pending", "processing")
            if schema_version in {7, 9}
            else ("failed", "processing")
        )
    }
    if schema_version != 1:
        selected_status_counts = {
            status: count
            for status, count in selected_status_counts.items()
            if count
        }
    selected_type_counts = _post_abort_type_counts(selected)
    reference_selected = {
        item["operation_id"]: item
        for item in reference_plan["selected_operations"]
    }
    reference_snapshot = {
        item["operation_id"]: item
        for item in reference_plan["live_snapshot"]["operations"]
    }
    current = {
        item["operation_id"]: item for item in snapshot["operations"]
    }
    selected_ids = {item["operation_id"] for item in selected}
    processing = [
        item
        for item in snapshot["operations"]
        if item["current_status"] == "processing"
    ]
    failed = [
        item
        for item in snapshot["operations"]
        if item["current_status"] == "failed"
    ]
    pending = [
        item
        for item in snapshot["operations"]
        if item["operation_id"] in selected_ids
        and item["current_status"] == "pending"
    ]
    preserved_ids = set(current) - selected_ids
    reference_preserved_ids = set(reference_snapshot) - set(reference_selected)
    preserved_status_counts = {
        status: snapshot["status_counts"].get(status, 0)
        for status in ("completed", "pending")
        if snapshot["status_counts"].get(status, 0)
    }
    if schema_version in {7, 9}:
        preserved_status_counts["pending"] -= len(pending)
    try:
        (
            expected_status_counts,
            expected_type_counts,
            expected_preserved_status_counts,
        ) = POST_ABORT_CONTRACTS[schema_version]
    except KeyError as error:
        raise OperationRecoveryError(
            "operation-recovery post-abort schema is invalid"
        ) from error
    if (
        reference_plan["selected_operation_count"]
        != (42 if schema_version in {6, 7, 8, 9} else 43)
        or reference_plan["selected_status_counts"]
        != {"pending": 42 if schema_version in {6, 7, 8, 9} else 43}
        or reference_plan["preserved_status_counts"]
        != {"completed": 6 if schema_version in {6, 7, 8, 9} else 5}
        or snapshot["cohort_digest"] != reference_plan["cohort_digest"]
        or set(current) != set(reference_snapshot)
        or not selected
        or selected_status_counts != expected_status_counts
        or selected_type_counts != expected_type_counts
        or (
            schema_version == 4
            and any(
                item["retry_count"]
                != POST_ABORT_V4_SELECTED_RETRY_COUNTS.get(
                    item["operation_type"]
                )
                for item in processing
            )
        )
        or (
            schema_version == 5
            and (
                any(item["retry_count"] != 0 for item in processing)
                or tuple(sorted(item["retry_count"] for item in failed))
                != POST_ABORT_V5_FAILED_RETRY_COUNTS
                or any(
                    item["operation_type"] != "retain"
                    or item["error_category"] != "provider_transport"
                    or item["error_digest"] is None
                    for item in failed
                )
            )
        )
        or (
            schema_version == 6
            and (
                any(item["retry_count"] != 0 for item in processing)
                or tuple(sorted(item["retry_count"] for item in failed))
                != POST_ABORT_V6_FAILED_RETRY_COUNTS
                or any(
                    item["operation_type"] != "retain"
                    or item["error_category"] != "none"
                    or item["error_digest"] is not None
                    for item in failed
                )
            )
        )
        or (
            schema_version == 7
            and (
                any(item["retry_count"] != 0 for item in processing)
                or tuple(sorted(item["retry_count"] for item in failed))
                != POST_ABORT_V7_FAILED_RETRY_COUNTS
                or tuple(sorted(item["retry_count"] for item in pending))
                != POST_ABORT_V7_PENDING_RETRY_COUNTS
                or any(
                    item["operation_type"] != "retain"
                    or item["error_category"] != "none"
                    or item["error_digest"] is not None
                    for item in [*failed, *pending]
                )
            )
        )
        or (
            schema_version in {8, 9}
            and (
                tuple(sorted(item["retry_count"] for item in processing))
                != (
                    POST_ABORT_V8_PROCESSING_RETRY_COUNTS
                    if schema_version == 8
                    else POST_ABORT_V9_PROCESSING_RETRY_COUNTS
                )
                or tuple(sorted(item["retry_count"] for item in failed))
                != (
                    POST_ABORT_V8_FAILED_RETRY_COUNTS
                    if schema_version == 8
                    else POST_ABORT_V9_FAILED_RETRY_COUNTS
                )
                or any(
                    item["operation_type"] != "retain"
                    or item["error_category"] != "provider_transport"
                    or item["error_digest"] is None
                    for item in processing
                )
                or any(
                    item["operation_type"] != "retain"
                    or item["error_category"] != "unknown"
                    or item["error_digest"] is None
                    for item in failed
                )
            )
        )
        or (
            schema_version == 9
            and (
                tuple(sorted(item["retry_count"] for item in pending))
                != POST_ABORT_V9_PENDING_RETRY_COUNTS
                or any(
                    item["operation_type"] != "retain"
                    or item["error_category"] != "provider_transport"
                    or item["error_digest"] is None
                    for item in pending
                )
            )
        )
        or preserved_status_counts != expected_preserved_status_counts
        or snapshot["status_counts"].get("cancelled", 0)
        or snapshot["generation_before"] != snapshot["generation_after"]
        or any(item["operation_id"] not in reference_selected for item in selected)
        or any(
            item["worker_id_present"] is not True
            or item["worker_id_digest"] != worker_digest
            or item["claimed_at"] is None
            or item["completed_at"] is not None
            or (
                schema_version not in {8, 9}
                and (
                    item["error_category"] != "none"
                    or item["error_digest"] is not None
                )
            )
            for item in processing
        )
        or any(
            item["worker_id_present"] is not True
            or item["worker_id_digest"] != worker_digest
            or item["claimed_at"] is None
            or item["completed_at"] is not None
            for item in pending
        )
        or any(
            (
                item["worker_id_present"] is not True
                or item["worker_id_digest"] != worker_digest
                or item["claimed_at"] is None
                or item["completed_at"] is None
            )
            if schema_version in {5, 6, 7, 8, 9}
            else (
                item["worker_id_present"]
                or item["worker_id_digest"] is not None
                or item["claimed_at"] is not None
                or item["completed_at"] is None
            )
            for item in failed
        )
        or preserved_ids
        != (set(reference_selected) - selected_ids) | reference_preserved_ids
        or any(
            current[operation_id]["current_status"] != "completed"
            or current[operation_id]["row_digest"]
            != reference_snapshot[operation_id]["row_digest"]
            for operation_id in reference_preserved_ids
        )
        or any(
            (
                current[operation_id]["current_status"] != "pending"
                or (
                    schema_version != 1
                    and (
                        current[operation_id]["worker_id_present"]
                        or current[operation_id]["worker_id_digest"] is not None
                        or current[operation_id]["claimed_at"] is not None
                        or current[operation_id]["completed_at"] is not None
                    )
                )
            )
            if not (
                (
                    schema_version == 5
                    and _post_abort_v5_completed_selected_matches(
                        current[operation_id],
                        worker_digest=worker_digest,
                    )
                )
                or (
                    schema_version == 9
                    and _post_abort_v9_completed_selected_matches(
                        current[operation_id],
                        worker_digest=worker_digest,
                    )
                )
            )
            else False
            for operation_id in set(reference_selected) - selected_ids
        )
        or (
            schema_version == 5
            and sum(
                current[operation_id]["current_status"] == "completed"
                for operation_id in set(reference_selected) - selected_ids
            )
            != 1
        )
        or (
            schema_version == 9
            and sum(
                _post_abort_v9_completed_selected_matches(
                    current[operation_id],
                    worker_digest=worker_digest,
                )
                for operation_id in set(reference_selected) - selected_ids
            )
            != 1
        )
        or any(
            current[operation_id]["operation_type"]
            != reference_snapshot[operation_id]["operation_type"]
            or current[operation_id]["task_payload_digest"]
            != reference_snapshot[operation_id]["task_payload_digest"]
            for operation_id in current
        )
    ):
        raise OperationRecoveryError(
            "operation-recovery post-abort row set is invalid"
        )
    return (
        selected,
        worker_digest,
        selected_status_counts,
        selected_type_counts,
        preserved_status_counts,
        {},
    )


def _post_terminal_worker_exit(
    value: Any,
    *,
    journal: Mapping[str, Any],
) -> dict[str, Any]:
    exit_value = _closed(
        _normalized(value),
        POST_ABORT_REFERENCE_WORKER_EXIT_KEYS,
        "post-terminal reference worker exit",
    )
    body = {
        "schema_version": _integer(
            exit_value["schema_version"],
            "post-terminal worker-exit schema version",
        ),
        "kind": _text(
            exit_value["kind"],
            "post-terminal worker-exit kind",
        ),
        "worker_pid": _integer(
            exit_value["worker_pid"],
            "post-terminal worker PID",
            minimum=1,
        ),
        "worker_start_time": _text(
            exit_value["worker_start_time"],
            "post-terminal worker start time",
            maximum=128,
        ),
        "observed_at": _integer(
            exit_value["observed_at"],
            "post-terminal worker-exit observed-at",
        ),
        "state": _text(
            exit_value["state"],
            "post-terminal worker-exit state",
            maximum=32,
        ),
    }
    if (
        body["schema_version"] != 1
        or body["kind"]
        != "operation-recovery-exact-drain-worker-exit-evidence"
        or body["worker_pid"] != journal["worker_pid"]
        or body["worker_start_time"] != journal["worker_start_time"]
        or body["state"] != "inactive"
        or _sha(
            exit_value["evidence_digest"],
            "post-terminal worker-exit digest",
        )
        != digest(body)
    ):
        raise OperationRecoveryError(
            "operation-recovery post-terminal worker exit is invalid"
        )
    return {**body, "evidence_digest": exit_value["evidence_digest"]}


def create_post_abort_recovery_plan(
    reference_plan_value: Mapping[str, Any],
    live_snapshot_value: Mapping[str, Any],
    *,
    candidate_release: Mapping[str, Any],
    rollback_backup: Mapping[str, Any],
    rollback_encryption: Mapping[str, Any],
    rollback_backup_path: str,
    rollback_bundle_path: str,
    authorization_receipt_path: str,
    application_receipt_path: str,
    verification_receipt_path: str,
    rollback_receipt_path: str,
    reference_application_authorization: Mapping[str, Any],
    reference_application_journal: Mapping[str, Any],
    reference_application_progress_digest: str,
    prior_retry_recovery: Mapping[str, Any] | None = None,
    reference_application_receipt_digest: str | None = None,
    reference_terminal_status: Mapping[str, Any] | None = None,
    reference_worker_exit: Mapping[str, Any] | None = None,
    schema_version: int = 10,
    created_at: int | None = None,
) -> Mapping[str, Any]:
    """Plan the exact stopped-worker cleanup without mutating operations."""
    reference = verify_exact_drain_plan(
        reference_plan_value,
        allow_expired=True,
    )
    snapshot = verify_live_snapshot(live_snapshot_value)
    reference_authorization = _post_abort_reference_application_authorization(
        reference_application_authorization,
        reference,
    )
    reference_journal = _post_abort_reference_application_journal(
        reference_application_journal,
        reference,
        reference_authorization,
    )
    reference_progress_digest = _sha(
        reference_application_progress_digest,
        "post-abort reference application progress digest",
    )
    if schema_version not in {4, 5, 6, 7, 8, 9, 10, 11, 12, 13}:
        raise OperationRecoveryError(
            "operation-recovery post-abort creation schema is invalid"
        )
    _assert_installation_authority_schema(
        snapshot["installation_authority"],
        plan_schema_version=schema_version,
    )
    (
        selected,
        worker_digest,
        selected_status_counts,
        selected_type_counts,
        preserved_status_counts,
        derived_fields,
    ) = _post_abort_contract(
        reference,
        snapshot,
        schema_version=schema_version,
        prior_retry_recovery=prior_retry_recovery,
    )
    authority = snapshot["installation_authority"]
    backup = _backup(
        rollback_backup,
        "operation-recovery post-abort backup",
        expected_source_kind="verified-live-pg0-backup",
    )
    encryption = _rollback_encryption(rollback_encryption)
    source_authority = backup["source_authority"]
    if (
        backup["postgres_system_identifier"]
        != authority["postgres_system_identifier"]
        or source_authority["data_identity_digest"]
        != authority["observed_data_identity_digest"]
        or source_authority["generation_before"]
        != snapshot["generation_before"]
        or source_authority["generation_after"]
        != snapshot["generation_after"]
    ):
        raise OperationRecoveryError(
            "operation-recovery post-abort backup identity differs"
        )
    artifact_paths = {
        "rollback_backup_path": _absolute_path(
            rollback_backup_path,
            "operation-recovery post-abort backup path",
        ),
        "rollback_bundle_path": _absolute_path(
            rollback_bundle_path,
            "operation-recovery post-abort rollback bundle path",
        ),
        "authorization_receipt_path": _absolute_path(
            authorization_receipt_path,
            "operation-recovery post-abort authorization path",
        ),
        "application_receipt_path": _absolute_path(
            application_receipt_path,
            "operation-recovery post-abort application path",
        ),
        "verification_receipt_path": _absolute_path(
            verification_receipt_path,
            "operation-recovery post-abort verification path",
        ),
        "rollback_receipt_path": _absolute_path(
            rollback_receipt_path,
            "operation-recovery post-abort rollback path",
        ),
    }
    if len(
        {
            unicodedata.normalize("NFD", value.casefold())
            for value in artifact_paths.values()
        }
    ) != len(artifact_paths):
        raise OperationRecoveryError(
            "operation-recovery post-abort artifact paths must be distinct"
        )
    planned_at = (
        int(time.time())
        if created_at is None
        else _integer(created_at, "post-abort plan created-at")
    )
    if (
        planned_at < snapshot["observed_at"]
        or planned_at - snapshot["observed_at"]
        > POST_ABORT_EVIDENCE_MAX_AGE_SECONDS
    ):
        raise OperationRecoveryError(
            "operation-recovery post-abort evidence is stale"
        )
    terminal_fields = {}
    if schema_version == 13:
        if (
            reference_terminal_status is None
            or reference_worker_exit is None
            or reference_application_receipt_digest is None
        ):
            raise OperationRecoveryError(
                "operation-recovery post-terminal evidence is required"
            )
        terminal_status = verify_exact_drain_status(
            reference_terminal_status,
            plan=reference,
        )
        worker_exit = _post_terminal_worker_exit(
            reference_worker_exit,
            journal=reference_journal,
        )
        application_receipt_digest = _sha(
            reference_application_receipt_digest,
            "post-terminal reference application receipt digest",
        )
        if (
            terminal_status["generation_before"]
            != snapshot["generation_before"]
            or terminal_status["selected_status_counts"]
            != {"failed": reference["selected_operation_count"]}
            or terminal_status["preserved_status_counts"]
            != {"completed": snapshot["status_counts"].get("completed", 0)}
            or terminal_status["status_digest"]
            != digest(
                {
                    key: value
                    for key, value in terminal_status.items()
                    if key != "status_digest"
                }
            )
            or worker_exit["observed_at"] > planned_at
        ):
            raise OperationRecoveryError(
                "operation-recovery post-terminal evidence is invalid"
            )
        terminal_fields = {
            "reference_application_receipt_digest": (
                application_receipt_digest
            ),
            "reference_terminal_status": terminal_status,
            "reference_terminal_status_digest": terminal_status[
                "status_digest"
            ],
            "reference_worker_exit": worker_exit,
            "reference_worker_exit_digest": worker_exit[
                "evidence_digest"
            ],
        }
    body = {
        "schema_version": schema_version,
        "kind": (
            "operation-recovery-exact-drain-post-terminal-reconciliation-plan"
            if schema_version == 13
            else "operation-recovery-exact-drain-post-abort-plan"
        ),
        "action": (
            "reconcile-exact-drain-post-terminal"
            if schema_version == 13
            else "recover-exact-drain-post-abort"
        ),
        "authority": "unapproved-plan",
        "mutation_authorized": False,
        "candidate_release": _candidate_release(candidate_release),
        "installation_authority": authority,
        "reference_plan": reference,
        "reference_plan_digest": reference["plan_digest"],
        "reference_worker_id_digest": worker_digest,
        "reference_application_authorization": reference_authorization,
        "reference_application_authorization_digest": (
            reference_authorization["receipt_digest"]
        ),
        "reference_application_journal": reference_journal,
        "reference_application_journal_digest": reference_journal[
            "receipt_digest"
        ],
        "reference_application_progress_digest": reference_progress_digest,
        **terminal_fields,
        "live_snapshot": snapshot,
        "cohort_digest": snapshot["cohort_digest"],
        "snapshot_digest": snapshot["snapshot_digest"],
        "pre_generation": snapshot["generation_before"],
        "evidence_observed_at": snapshot["observed_at"],
        "evidence_max_age_seconds": POST_ABORT_EVIDENCE_MAX_AGE_SECONDS,
        "transaction_timeout_seconds": POST_ABORT_TRANSACTION_TIMEOUT_SECONDS,
        "selected_operations": selected,
        "selected_operation_count": len(selected),
        "selected_status_counts": selected_status_counts,
        "selected_type_counts": selected_type_counts,
        "selected_row_set_digest": _exact_drain_row_set_digest(selected),
        "preserved_status_counts": preserved_status_counts,
        **derived_fields,
        "rollback_backup": backup,
        "rollback_encryption": encryption,
        **artifact_paths,
        "created_at": planned_at,
        "expires_at": planned_at + POST_ABORT_PLAN_LIFETIME_SECONDS,
    }
    return {**body, "plan_digest": digest(body)}


def verify_post_abort_recovery_plan(
    value: Any,
    *,
    now: int | None = None,
    allow_expired: bool = False,
) -> Mapping[str, Any]:
    normalized = _normalized(value)
    if not isinstance(normalized, Mapping):
        raise OperationRecoveryError(
            "operation-recovery post-abort plan is invalid"
        )
    schema_version = normalized.get("schema_version")
    if type(schema_version) is not int or schema_version not in {
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
    }:
        raise OperationRecoveryError(
            "operation-recovery post-abort plan is invalid"
        )
    plan = _closed(
        normalized,
        {
            1: POST_ABORT_PLAN_V1_KEYS,
            2: POST_ABORT_PLAN_V2_KEYS,
            3: POST_ABORT_PLAN_V3_KEYS,
            4: POST_ABORT_PLAN_V4_KEYS,
            5: POST_ABORT_PLAN_V5_KEYS,
            6: POST_ABORT_PLAN_V6_KEYS,
            7: POST_ABORT_PLAN_V7_KEYS,
            8: POST_ABORT_PLAN_V8_KEYS,
            9: POST_ABORT_PLAN_V9_KEYS,
            10: POST_ABORT_PLAN_V10_KEYS,
            11: POST_ABORT_PLAN_V11_KEYS,
            12: POST_ABORT_PLAN_V12_KEYS,
            13: POST_ABORT_PLAN_V13_KEYS,
        }[schema_version],
        "operation-recovery post-abort plan",
    )
    reference = verify_exact_drain_plan(
        plan["reference_plan"],
        allow_expired=True,
    )
    if (
        schema_version not in {10, 11, 12, 13}
        and reference["schema_version"] in {10, 11, 12, 13}
    ):
        raise OperationRecoveryError(
            "operation-recovery legacy post-abort schema cannot reference "
            "schema 10 or later"
        )
    snapshot = verify_live_snapshot(plan["live_snapshot"])
    _assert_installation_authority_schema(
        snapshot["installation_authority"],
        plan_schema_version=schema_version,
    )
    reference_authorization = (
        None
        if schema_version == 1
        else _post_abort_reference_application_authorization(
            plan["reference_application_authorization"],
            reference,
        )
    )
    reference_journal = (
        None
        if schema_version == 1
        else _post_abort_reference_application_journal(
            plan["reference_application_journal"],
            reference,
            reference_authorization,
        )
    )
    reference_progress_digest = (
        None
        if schema_version not in {3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13}
        else _sha(
            plan["reference_application_progress_digest"],
            "post-abort reference application progress digest",
        )
    )
    (
        expected_selected,
        worker_digest,
        expected_status_counts,
        expected_type_counts,
        expected_preserved_status_counts,
        expected_derived_fields,
    ) = _post_abort_contract(
        reference,
        snapshot,
        schema_version=schema_version,
        prior_retry_recovery=(
            plan["retry_recovery"].get("prior_retry_recovery")
            if schema_version in {11, 12, 13}
            and isinstance(plan["retry_recovery"], Mapping)
            else None
        ),
    )
    selected_value = plan["selected_operations"]
    if not isinstance(selected_value, list):
        raise OperationRecoveryError(
            "operation-recovery post-abort plan is invalid"
        )
    selected = []
    for item_value in selected_value:
        item = _closed(
            _normalized(item_value),
            SELECTED_OPERATION_KEYS,
            "post-abort selected operation",
        )
        selected.append(
            {
                "operation_id": _operation_id(item["operation_id"]),
                "operation_type": _text(
                    item["operation_type"],
                    "post-abort operation type",
                    maximum=128,
                ),
                "expected_status": _text(
                    item["expected_status"],
                    "post-abort operation status",
                    maximum=32,
                ),
                "row_digest": _sha(item["row_digest"], "post-abort row digest"),
                "task_payload_digest": _sha(
                    item["task_payload_digest"],
                    "post-abort payload digest",
                ),
            }
        )
    status_counts = _count_map(
        plan["selected_status_counts"],
        "post-abort selected status counts",
    )
    type_counts = _count_map(
        plan["selected_type_counts"],
        "post-abort selected type counts",
    )
    preserved = _count_map(
        plan["preserved_status_counts"],
        "post-abort preserved status counts",
    )
    derived_fields = {}
    if schema_version in {10, 11, 12, 13}:
        try:
            retry_recovery = _verified_post_abort_retry_recovery(
                plan["retry_recovery"],
                expected_schema_version=(
                    1
                    if schema_version == 10
                    else (
                        4
                        if schema_version == 13
                        else (
                        plan["retry_recovery"].get("schema_version")
                        if isinstance(plan["retry_recovery"], Mapping)
                        else -1
                        )
                    )
                ),
            )
            retry_recovery_digest = _sha(
                plan["retry_recovery_digest"],
                "post-abort retry recovery digest",
            )
        except OperationRecoveryError as error:
            raise OperationRecoveryError(
                "operation-recovery post-abort retry recovery is invalid"
            ) from error
        if retry_recovery_digest != digest(retry_recovery):
            raise OperationRecoveryError(
                "operation-recovery post-abort retry recovery is invalid"
            )
        derived_fields = {
            "selection_contract_digest": _sha(
                plan["selection_contract_digest"],
                "post-abort selection contract digest",
            ),
            "selected_checkpoint_set_digest": _sha(
                plan["selected_checkpoint_set_digest"],
                "post-abort selected checkpoint set digest",
            ),
            "preserved_row_set_digest": _sha(
                plan["preserved_row_set_digest"],
                "post-abort preserved row set digest",
            ),
            "retry_recovery": retry_recovery,
            "retry_recovery_digest": retry_recovery_digest,
        }
    terminal_fields = {}
    if schema_version == 13:
        terminal_status = verify_exact_drain_status(
            plan["reference_terminal_status"],
            plan=reference,
        )
        worker_exit = _post_terminal_worker_exit(
            plan["reference_worker_exit"],
            journal=reference_journal,
        )
        terminal_fields = {
            "reference_application_receipt_digest": _sha(
                plan["reference_application_receipt_digest"],
                "post-terminal reference application receipt digest",
            ),
            "reference_terminal_status": terminal_status,
            "reference_terminal_status_digest": _sha(
                plan["reference_terminal_status_digest"],
                "post-terminal reference status digest",
            ),
            "reference_worker_exit": worker_exit,
            "reference_worker_exit_digest": _sha(
                plan["reference_worker_exit_digest"],
                "post-terminal reference worker-exit digest",
            ),
        }
    authority = _installation_authority(plan["installation_authority"])
    backup = _backup(
        plan["rollback_backup"],
        "operation-recovery post-abort backup",
        expected_source_kind="verified-live-pg0-backup",
    )
    encryption = _rollback_encryption(plan["rollback_encryption"])
    source_authority = backup["source_authority"]
    artifact_paths = {
        key: _absolute_path(plan[key], f"post-abort {key}")
        for key in (
            "rollback_backup_path",
            "rollback_bundle_path",
            "authorization_receipt_path",
            "application_receipt_path",
            "verification_receipt_path",
            "rollback_receipt_path",
        )
    }
    created_at = _integer(plan["created_at"], "post-abort plan created-at")
    expires_at = _integer(plan["expires_at"], "post-abort plan expires-at")
    observed_at = (
        int(time.time())
        if now is None
        else _integer(now, "post-abort verification time")
    )
    if not allow_expired and observed_at >= expires_at:
        raise OperationRecoveryError(
            "operation-recovery post-abort plan expired"
        )
    evidence_observed_at = _integer(
        plan["evidence_observed_at"],
        "post-abort evidence observed-at",
    )
    if (
        plan["schema_version"] != schema_version
        or plan["kind"]
        != (
            "operation-recovery-exact-drain-post-terminal-reconciliation-plan"
            if schema_version == 13
            else "operation-recovery-exact-drain-post-abort-plan"
        )
        or plan["action"]
        != (
            "reconcile-exact-drain-post-terminal"
            if schema_version == 13
            else "recover-exact-drain-post-abort"
        )
        or plan["authority"] != "unapproved-plan"
        or plan["mutation_authorized"] is not False
        or plan["candidate_release"]
        != _candidate_release(plan["candidate_release"])
        or authority != snapshot["installation_authority"]
        or plan["reference_plan_digest"] != reference["plan_digest"]
        or plan["reference_worker_id_digest"] != worker_digest
        or (
            schema_version != 1
            and plan["reference_application_authorization_digest"]
            != reference_authorization["receipt_digest"]
        )
        or (
            schema_version != 1
            and plan["reference_application_journal_digest"]
            != reference_journal["receipt_digest"]
        )
        or plan["cohort_digest"] != snapshot["cohort_digest"]
        or plan["snapshot_digest"] != snapshot["snapshot_digest"]
        or plan["pre_generation"] != snapshot["generation_before"]
        or evidence_observed_at != snapshot["observed_at"]
        or plan["evidence_max_age_seconds"]
        != POST_ABORT_EVIDENCE_MAX_AGE_SECONDS
        or plan["transaction_timeout_seconds"]
        != POST_ABORT_TRANSACTION_TIMEOUT_SECONDS
        or created_at < evidence_observed_at
        or created_at - evidence_observed_at
        > POST_ABORT_EVIDENCE_MAX_AGE_SECONDS
        or snapshot["generation_before"] != snapshot["generation_after"]
        or selected != expected_selected
        or plan["selected_operation_count"] != len(selected)
        or status_counts != expected_status_counts
        or type_counts != expected_type_counts
        or preserved != expected_preserved_status_counts
        or derived_fields != expected_derived_fields
        or (
            schema_version == 13
            and (
                terminal_fields["reference_terminal_status_digest"]
                != terminal_fields["reference_terminal_status"][
                    "status_digest"
                ]
                or terminal_fields["reference_worker_exit_digest"]
                != terminal_fields["reference_worker_exit"][
                    "evidence_digest"
                ]
                or terminal_fields["reference_terminal_status"][
                    "generation_before"
                ]
                != snapshot["generation_before"]
                or terminal_fields["reference_terminal_status"][
                    "selected_status_counts"
                ]
                != {"failed": reference["selected_operation_count"]}
                or terminal_fields["reference_worker_exit"]["observed_at"]
                > created_at
            )
        )
        or plan["selected_row_set_digest"]
        != _exact_drain_row_set_digest(selected)
        or backup["postgres_system_identifier"]
        != authority["postgres_system_identifier"]
        or source_authority["data_identity_digest"]
        != authority["observed_data_identity_digest"]
        or source_authority["generation_before"] != plan["pre_generation"]
        or source_authority["generation_after"] != plan["pre_generation"]
        or expires_at - created_at != POST_ABORT_PLAN_LIFETIME_SECONDS
        or len(
            {
                unicodedata.normalize("NFD", value.casefold())
                for value in artifact_paths.values()
            }
        )
        != len(artifact_paths)
    ):
        raise OperationRecoveryError(
            "operation-recovery post-abort plan is invalid"
        )
    body = {
        "schema_version": schema_version,
        "kind": (
            "operation-recovery-exact-drain-post-terminal-reconciliation-plan"
            if schema_version == 13
            else "operation-recovery-exact-drain-post-abort-plan"
        ),
        "action": (
            "reconcile-exact-drain-post-terminal"
            if schema_version == 13
            else "recover-exact-drain-post-abort"
        ),
        "authority": "unapproved-plan",
        "mutation_authorized": False,
        "candidate_release": _candidate_release(plan["candidate_release"]),
        "installation_authority": authority,
        "reference_plan": reference,
        "reference_plan_digest": reference["plan_digest"],
        "reference_worker_id_digest": worker_digest,
        **(
            {}
            if schema_version == 1
            else {
                "reference_application_authorization": (
                    reference_authorization
                ),
                "reference_application_authorization_digest": _sha(
                    plan["reference_application_authorization_digest"],
                    "post-abort reference application authorization digest",
                ),
                "reference_application_journal": reference_journal,
                "reference_application_journal_digest": _sha(
                    plan["reference_application_journal_digest"],
                    "post-abort reference application journal digest",
                ),
                **(
                    {}
                    if schema_version not in {
                        3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13
                    }
                    else {
                        "reference_application_progress_digest": (
                            reference_progress_digest
                        )
                    }
                ),
            }
        ),
        **terminal_fields,
        "live_snapshot": snapshot,
        "cohort_digest": snapshot["cohort_digest"],
        "snapshot_digest": snapshot["snapshot_digest"],
        "pre_generation": snapshot["generation_before"],
        "evidence_observed_at": snapshot["observed_at"],
        "evidence_max_age_seconds": POST_ABORT_EVIDENCE_MAX_AGE_SECONDS,
        "transaction_timeout_seconds": POST_ABORT_TRANSACTION_TIMEOUT_SECONDS,
        "selected_operations": selected,
        "selected_operation_count": len(selected),
        "selected_status_counts": status_counts,
        "selected_type_counts": type_counts,
        "selected_row_set_digest": _sha(
            plan["selected_row_set_digest"],
            "post-abort row-set digest",
        ),
        "preserved_status_counts": preserved,
        **derived_fields,
        "rollback_backup": backup,
        "rollback_encryption": encryption,
        **artifact_paths,
        "created_at": created_at,
        "expires_at": expires_at,
    }
    if _sha(plan["plan_digest"], "post-abort plan digest") != digest(body):
        raise OperationRecoveryError(
            "operation-recovery post-abort plan digest differs"
        )
    return {**body, "plan_digest": plan["plan_digest"]}


def verify_exact_drain_authorization_receipt(
    value: Any,
    *,
    plan: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Validate the approval handoff consumed by the detached worker."""
    verified_plan = verify_exact_drain_plan(plan, allow_expired=True)
    keys = frozenset(
        {
            "schema_version",
            "kind",
            "plan_digest",
            "approval_digest",
            "candidate_release",
            "provider_policy_digest",
            "worker_runtime_digest",
            "authorized_at",
            "receipt_digest",
        }
    )
    receipt = _closed(
        _normalized(value),
        keys,
        "operation-recovery exact drain authorization receipt",
    )
    authorized_at = _integer(
        receipt["authorized_at"],
        "exact drain authorization time",
    )
    body = {
        "schema_version": 1,
        "kind": "operation-recovery-exact-drain-authorization-receipt",
        "plan_digest": _sha(receipt["plan_digest"], "exact drain plan digest"),
        "approval_digest": _sha(
            receipt["approval_digest"],
            "exact drain approval digest",
        ),
        "candidate_release": _candidate_release(
            receipt["candidate_release"]
        ),
        "provider_policy_digest": _sha(
            receipt["provider_policy_digest"],
            "provider policy digest",
        ),
        "worker_runtime_digest": _sha(
            receipt["worker_runtime_digest"],
            "worker runtime digest",
        ),
        "authorized_at": authorized_at,
    }
    if (
        body["plan_digest"] != verified_plan["plan_digest"]
        or body["approval_digest"] != verified_plan["plan_digest"]
        or body["candidate_release"] != verified_plan["candidate_release"]
        or body["provider_policy_digest"]
        != verified_plan["provider_policy_digest"]
        or body["worker_runtime_digest"]
        != verified_plan["worker_runtime_digest"]
        or authorized_at < verified_plan["created_at"]
        or authorized_at >= verified_plan["expires_at"]
        or (
            verified_plan["schema_version"] in {13, 14}
            and authorized_at
            - verified_plan["hatchery_capability_receipt"]["observed_at"]
            > EXACT_DRAIN_EVIDENCE_MAX_AGE_SECONDS
        )
        or (
            verified_plan["schema_version"] == 14
            and authorized_at
            >= verified_plan["checkpoint_continuation_handoff"]["expires_at"]
        )
        or _sha(
            receipt["receipt_digest"],
            "exact drain authorization receipt digest",
        )
        != digest(body)
    ):
        raise OperationRecoveryError(
            "operation-recovery exact drain authorization receipt is invalid"
        )
    return {**body, "receipt_digest": receipt["receipt_digest"]}


def verify_exact_drain_status(
    value: Any,
    *,
    plan: Mapping[str, Any],
) -> Mapping[str, Any]:
    verified_plan = verify_exact_drain_plan(plan, allow_expired=True)
    status_schema_version = _integer(
        value.get("schema_version") if isinstance(value, Mapping) else None,
        "exact drain status schema version",
    )
    keys = frozenset(
        {
            "schema_version",
            "kind",
            "plan_digest",
            "generation_before",
            "generation_after",
            "selected_operation_count",
            "selected_status_counts",
            "preserved_status_counts",
            "outside_nonterminal_counts",
            "observed_at",
            "status_digest",
        }
    )
    if status_schema_version == 2:
        keys |= frozenset({"failure_classifications"})
    status = _closed(
        _normalized(value),
        keys,
        "operation-recovery exact drain status",
    )
    selected_counts = _count_map(
        status["selected_status_counts"],
        "exact drain selected status counts",
    )
    preserved_counts = _count_map(
        status["preserved_status_counts"],
        "exact drain preserved status counts",
        minimum=0,
    )
    outside_value = status["outside_nonterminal_counts"]
    if not isinstance(outside_value, list):
        raise OperationRecoveryError(
            "operation-recovery exact drain status is invalid"
        )
    outside = []
    for item_value in outside_value:
        item = _closed(
            _normalized(item_value),
            frozenset(
                {"bank_id", "operation_type", "status", "operation_count"}
            ),
            "exact drain outside nonterminal count",
        )
        checked = {
            "bank_id": _text(item["bank_id"], "outside bank ID", maximum=256),
            "operation_type": _text(
                item["operation_type"],
                "outside operation type",
                maximum=128,
            ),
            "status": _text(item["status"], "outside status", maximum=32),
            "operation_count": _integer(
                item["operation_count"],
                "outside operation count",
                minimum=1,
            ),
        }
        if checked["status"] not in {"pending", "processing"}:
            raise OperationRecoveryError(
                "operation-recovery exact drain status is invalid"
            )
        outside.append(checked)
    generation_before = _text(
        status["generation_before"],
        "exact drain status generation",
    )
    generation_after = _text(
        status["generation_after"],
        "exact drain status generation",
    )
    observed_at = _integer(status["observed_at"], "exact drain observed-at")
    failure_classifications = []
    if status_schema_version == 2:
        classifications_value = status["failure_classifications"]
        if not isinstance(classifications_value, list):
            raise OperationRecoveryError(
                "operation-recovery exact drain status is invalid"
            )
        for item_value in classifications_value:
            item = _closed(
                _normalized(item_value),
                frozenset(
                    {"cause_family", "error_digest", "occurrence_count"}
                ),
                "exact drain failure classification",
            )
            checked = {
                "cause_family": _text(
                    item["cause_family"],
                    "exact drain failure cause family",
                    maximum=128,
                ),
                "error_digest": _sha(
                    item["error_digest"],
                    "exact drain failure error digest",
                ),
                "occurrence_count": _integer(
                    item["occurrence_count"],
                    "exact drain failure occurrence count",
                    minimum=1,
                ),
            }
            if checked["cause_family"] not in FAILURE_CAUSE_FAMILIES:
                raise OperationRecoveryError(
                    "operation-recovery exact drain status is invalid"
                )
            failure_classifications.append(checked)
    body = {
        "schema_version": status_schema_version,
        "kind": "operation-recovery-exact-drain-status",
        "plan_digest": _sha(status["plan_digest"], "exact drain plan digest"),
        "generation_before": generation_before,
        "generation_after": generation_after,
        "selected_operation_count": _integer(
            status["selected_operation_count"],
            "exact drain selected operation count",
        ),
        "selected_status_counts": selected_counts,
        "preserved_status_counts": preserved_counts,
        "outside_nonterminal_counts": outside,
        **(
            {"failure_classifications": failure_classifications}
            if status_schema_version == 2
            else {}
        ),
        "observed_at": observed_at,
    }
    if (
        status_schema_version
        != (2 if verified_plan["schema_version"] in {12, 13, 14} else 1)
        or body["plan_digest"] != verified_plan["plan_digest"]
        or generation_before != generation_after
        or body["selected_operation_count"]
        != verified_plan["selected_operation_count"]
        or set(selected_counts) - set(OPERATION_STATUSES)
        or sum(selected_counts.values())
        != verified_plan["selected_operation_count"]
        or preserved_counts != verified_plan["preserved_status_counts"]
        or failure_classifications
        != sorted(
            failure_classifications,
            key=lambda item: (
                item["cause_family"],
                item["error_digest"],
            ),
        )
        or len(
            {
                (item["cause_family"], item["error_digest"])
                for item in failure_classifications
            }
        )
        != len(failure_classifications)
        or sum(
            item["occurrence_count"] for item in failure_classifications
        )
        > body["selected_operation_count"]
        or outside
        != sorted(
            outside,
            key=lambda item: (
                item["bank_id"],
                item["operation_type"],
                item["status"],
            ),
        )
        or _sha(status["status_digest"], "exact drain status digest")
        != digest(body)
    ):
        raise OperationRecoveryError(
            "operation-recovery exact drain status is invalid"
        )
    return {**body, "status_digest": status["status_digest"]}


def _queue_blocker(
    value: Any,
    *,
    include_digest: bool,
    allow_terminal_reference_selected: bool = False,
) -> dict[str, Any]:
    keys = QUEUE_BLOCKER_KEYS if include_digest else QUEUE_BLOCKER_INPUT_KEYS
    row = _closed(
        _normalized(value),
        keys,
        "operation-recovery queue blocker",
    )
    status = _text(row["status"], "queue blocker status", maximum=32)
    reason = _text(row["blocker_reason"], "queue blocker reason", maximum=32)
    worker_present = row["worker_id_present"]
    payload_present = row["task_payload_present"]
    in_cohort = row["in_reference_cohort"]
    in_selected = row["in_reference_selected_set"]
    if (
        status not in QUEUE_BLOCKER_REASONS
        or reason != QUEUE_BLOCKER_REASONS[status]
        or type(worker_present) is not bool
        or type(payload_present) is not bool
        or type(in_cohort) is not bool
        or type(in_selected) is not bool
        or (in_selected and not in_cohort)
        or (
            in_selected
            and status != "processing"
            and not (
                allow_terminal_reference_selected
                and status in {"failed", "cancelled"}
            )
        )
        or (status != "processing" and not worker_present and row["claimed_at"] is None)
        or (not worker_present and row["worker_id_digest"] is not None)
        or (not payload_present and row["task_payload_digest"] is not None)
    ):
        raise OperationRecoveryError(
            "operation-recovery queue blocker is invalid"
        )
    checked = {
        "operation_id": _operation_id(row["operation_id"]),
        "bank_id": _text(row["bank_id"], "queue blocker bank", maximum=256),
        "operation_type": _text(
            row["operation_type"],
            "queue blocker operation type",
            maximum=128,
        ),
        "status": status,
        "created_at": _text(row["created_at"], "queue blocker created-at"),
        "updated_at": _text(row["updated_at"], "queue blocker updated-at"),
        "completed_at": _optional_text(
            row["completed_at"],
            "queue blocker completed-at",
        ),
        "retry_count": _integer(row["retry_count"], "queue blocker retry count"),
        "next_retry_at": _optional_text(
            row["next_retry_at"],
            "queue blocker next-retry-at",
        ),
        "worker_id_present": worker_present,
        "worker_id_digest": (
            _sha(row["worker_id_digest"], "queue blocker worker digest")
            if worker_present
            else None
        ),
        "claimed_at": _optional_text(
            row["claimed_at"],
            "queue blocker claimed-at",
        ),
        "task_payload_present": payload_present,
        "task_payload_digest": (
            _sha(row["task_payload_digest"], "queue blocker payload digest")
            if payload_present
            else None
        ),
        "in_reference_cohort": in_cohort,
        "in_reference_selected_set": in_selected,
        "blocker_reason": reason,
    }
    row_digest = digest(checked)
    if include_digest and _sha(
        row["row_digest"],
        "queue blocker row digest",
    ) != row_digest:
        raise OperationRecoveryError("queue blocker row digest differs")
    return {**checked, "row_digest": row_digest}


def _count_map(
    value: Any,
    label: str,
    *,
    minimum: int = 1,
) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise OperationRecoveryError(f"{label} is invalid")
    checked = {}
    for key, count in value.items():
        text = _text(key, f"{label} key", maximum=256)
        checked[text] = _integer(count, f"{label} count", minimum=minimum)
    if list(checked) != sorted(checked):
        raise OperationRecoveryError(f"{label} is invalid")
    return checked


def _queue_blocker_counts(
    blockers: Sequence[Mapping[str, Any]],
    key: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for blocker in blockers:
        value = str(blocker[key])
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _queue_blocker_scope() -> dict[str, Any]:
    return {
        **QUEUE_BLOCKER_SCOPE,
        "statuses": list(QUEUE_BLOCKER_SCOPE["statuses"]),
    }


def create_global_queue_blocker_classification(
    rows: Sequence[Mapping[str, Any]],
    *,
    classifier_candidate_release: Mapping[str, Any],
    reference_plan: Mapping[str, Any],
    installation_authority: Mapping[str, Any],
    generation_before: str,
    generation_after: str,
    guard_contract_version: int,
    guard_contract_digest: str,
    observed_at: int | None = None,
) -> Mapping[str, Any]:
    """Create expiring read-only evidence for the exact apply queue guard."""
    observed = (
        int(time.time())
        if observed_at is None
        else _integer(observed_at, "queue blocker observation time")
    )
    plan = verify_requeue_plan(
        reference_plan,
        now=observed,
        allow_expired=True,
    )
    before = _text(generation_before, "queue blocker pre-generation")
    after = _text(generation_after, "queue blocker post-generation")
    if before != after:
        raise OperationRecoveryError(
            "migration generation changed during queue blocker classification"
        )
    authority = _installation_authority(installation_authority)
    _assert_installation_authority_schema(
        authority,
        plan_schema_version=1,
    )
    if authority != plan["installation_authority"]:
        raise OperationRecoveryError(
            "operation-recovery queue blocker authority differs"
        )
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise OperationRecoveryError("live operation evidence is invalid")
    blockers = sorted(
        (_queue_blocker(row, include_digest=False) for row in rows),
        key=lambda item: (item["created_at"], item["operation_id"]),
    )
    identifiers = [item["operation_id"] for item in blockers]
    if len(identifiers) != len(set(identifiers)):
        raise OperationRecoveryError(
            "operation-recovery queue blocker set contains duplicates"
        )
    reference_cohort_ids = {
        item["operation_id"] for item in plan["cohort"]["operations"]
    }
    reference_selected_ids = {
        item["operation_id"] for item in plan["selected_operations"]
    }
    if any(
        item["in_reference_cohort"]
        != (item["operation_id"] in reference_cohort_ids)
        or item["in_reference_selected_set"]
        != (item["operation_id"] in reference_selected_ids)
        for item in blockers
    ):
        raise OperationRecoveryError(
            "operation-recovery queue blocker reference membership differs"
        )
    body = {
        "schema_version": 1,
        "kind": "operation-recovery-global-queue-blocker-classification",
        "authority": "read-only-classification",
        "mutation_authorized": False,
        "classifier_candidate_release": _candidate_release(
            classifier_candidate_release
        ),
        "guard_reference_candidate_release": plan["candidate_release"],
        "guard_contract_version": _integer(
            guard_contract_version,
            "queue blocker guard contract version",
            minimum=1,
        ),
        "guard_contract_digest": _sha(
            guard_contract_digest,
            "queue blocker guard contract digest",
        ),
        "reference_plan_digest": plan["plan_digest"],
        "reference_plan_expired": observed >= plan["expires_at"],
        "reference_cohort_digest": plan["cohort_digest"],
        "reference_snapshot_digest": plan["snapshot_digest"],
        "reference_selected_operation_ids_digest": digest(
            sorted(reference_selected_ids)
        ),
        "installation_authority": authority,
        "profile_id": "systalyze",
        "schema": "public",
        "generation_before": before,
        "generation_after": after,
        "scope": _queue_blocker_scope(),
        "blocker_count": len(blockers),
        "status_counts": _queue_blocker_counts(blockers, "status"),
        "bank_counts": _queue_blocker_counts(blockers, "bank_id"),
        "operation_type_counts": _queue_blocker_counts(
            blockers,
            "operation_type",
        ),
        "blockers": blockers,
        "observed_at": observed,
        "expires_at": observed + MAX_PLAN_LIFETIME_SECONDS,
    }
    return {**body, "classification_digest": digest(body)}


def verify_global_queue_blocker_classification(
    value: Any,
    *,
    now: int | None = None,
    allow_expired: bool = False,
) -> Mapping[str, Any]:
    classification = _closed(
        _normalized(value),
        QUEUE_BLOCKER_CLASSIFICATION_KEYS,
        "operation-recovery queue blocker classification",
    )
    blockers_value = classification["blockers"]
    if not isinstance(blockers_value, list):
        raise OperationRecoveryError(
            "operation-recovery queue blocker classification is invalid"
        )
    blockers = [_queue_blocker(row, include_digest=True) for row in blockers_value]
    identifiers = [item["operation_id"] for item in blockers]
    if len(identifiers) != len(set(identifiers)):
        raise OperationRecoveryError(
            "operation-recovery queue blocker set contains duplicates"
        )
    if blockers != sorted(
        blockers,
        key=lambda item: (item["created_at"], item["operation_id"]),
    ):
        raise OperationRecoveryError(
            "operation-recovery queue blockers are not ordered"
        )
    observed = _integer(
        classification["observed_at"],
        "queue blocker observation time",
    )
    expires = _integer(
        classification["expires_at"],
        "queue blocker expiry time",
    )
    verified_at = (
        int(time.time())
        if now is None
        else _integer(now, "queue blocker verification time")
    )
    if not allow_expired and verified_at >= expires:
        raise OperationRecoveryError(
            "operation-recovery queue blocker classification expired"
        )
    before = _text(
        classification["generation_before"],
        "queue blocker pre-generation",
    )
    after = _text(
        classification["generation_after"],
        "queue blocker post-generation",
    )
    status_counts = _count_map(
        classification["status_counts"],
        "queue blocker status counts",
    )
    bank_counts = _count_map(
        classification["bank_counts"],
        "queue blocker bank counts",
    )
    type_counts = _count_map(
        classification["operation_type_counts"],
        "queue blocker operation type counts",
    )
    if (
        classification["schema_version"] != 1
        or classification["kind"]
        != "operation-recovery-global-queue-blocker-classification"
        or classification["authority"] != "read-only-classification"
        or classification["mutation_authorized"] is not False
        or type(classification["reference_plan_expired"]) is not bool
        or classification["profile_id"] != "systalyze"
        or classification["schema"] != "public"
        or before != after
        or classification["scope"] != _queue_blocker_scope()
        or classification["blocker_count"] != len(blockers)
        or expires - observed != MAX_PLAN_LIFETIME_SECONDS
        or status_counts != _queue_blocker_counts(blockers, "status")
        or bank_counts != _queue_blocker_counts(blockers, "bank_id")
        or type_counts != _queue_blocker_counts(blockers, "operation_type")
    ):
        raise OperationRecoveryError(
            "operation-recovery queue blocker classification is invalid"
        )
    authority = _installation_authority(
        classification["installation_authority"]
    )
    _assert_installation_authority_schema(
        authority,
        plan_schema_version=1,
    )
    body = {
        "schema_version": 1,
        "kind": "operation-recovery-global-queue-blocker-classification",
        "authority": "read-only-classification",
        "mutation_authorized": False,
        "classifier_candidate_release": _candidate_release(
            classification["classifier_candidate_release"]
        ),
        "guard_reference_candidate_release": _candidate_release(
            classification["guard_reference_candidate_release"]
        ),
        "guard_contract_version": _integer(
            classification["guard_contract_version"],
            "queue blocker guard contract version",
            minimum=1,
        ),
        "guard_contract_digest": _sha(
            classification["guard_contract_digest"],
            "queue blocker guard contract digest",
        ),
        "reference_plan_digest": _sha(
            classification["reference_plan_digest"],
            "queue blocker reference plan digest",
        ),
        "reference_plan_expired": classification["reference_plan_expired"],
        "reference_cohort_digest": _sha(
            classification["reference_cohort_digest"],
            "queue blocker reference cohort digest",
        ),
        "reference_snapshot_digest": _sha(
            classification["reference_snapshot_digest"],
            "queue blocker reference snapshot digest",
        ),
        "reference_selected_operation_ids_digest": _sha(
            classification["reference_selected_operation_ids_digest"],
            "queue blocker selected operation IDs digest",
        ),
        "installation_authority": authority,
        "profile_id": "systalyze",
        "schema": "public",
        "generation_before": before,
        "generation_after": after,
        "scope": _queue_blocker_scope(),
        "blocker_count": len(blockers),
        "status_counts": status_counts,
        "bank_counts": bank_counts,
        "operation_type_counts": type_counts,
        "blockers": blockers,
        "observed_at": observed,
        "expires_at": expires,
    }
    if _sha(
        classification["classification_digest"],
        "queue blocker classification digest",
    ) != digest(body):
        raise OperationRecoveryError(
            "operation-recovery queue blocker classification digest differs"
        )
    return {**body, "classification_digest": classification["classification_digest"]}


def _claim_release_row(value: Any) -> dict[str, Any]:
    row = _closed(
        _normalized(value),
        CLAIM_RELEASE_ROW_KEYS,
        "operation-recovery claim-release row",
    )
    blocker = _queue_blocker(
        {key: row[key] for key in QUEUE_BLOCKER_KEYS},
        include_digest=True,
    )
    if (
        blocker["status"] != "failed"
        or blocker["blocker_reason"] != "claimed_failed"
        or blocker["in_reference_cohort"]
        or blocker["in_reference_selected_set"]
        or not blocker["worker_id_present"]
        or blocker["claimed_at"] is None
    ):
        raise OperationRecoveryError(
            "operation-recovery claim-release row is invalid"
        )
    return {
        **blocker,
        "nonclaim_state_digest": _sha(
            row["nonclaim_state_digest"],
            "operation-recovery nonclaim state digest",
        ),
    }


def _claim_release_permitted_blocker_row(value: Any) -> dict[str, Any]:
    row = _closed(
        _normalized(value),
        CLAIM_RELEASE_PERMITTED_BLOCKER_ROW_KEYS,
        "operation-recovery claim-release permitted blocker row",
    )
    blocker = _queue_blocker(
        {key: row[key] for key in QUEUE_BLOCKER_KEYS},
        include_digest=True,
        allow_terminal_reference_selected=True,
    )
    if (
        blocker["bank_id"] != "engineering"
        or blocker["operation_type"] not in EXPECTED_OPERATION_COUNTS
        or blocker["status"] not in {"failed", "cancelled"}
        or blocker["in_reference_cohort"] is not True
        or blocker["in_reference_selected_set"] is not True
        or blocker["worker_id_present"] is not True
        or blocker["claimed_at"] is None
    ):
        raise OperationRecoveryError(
            "operation-recovery claim-release permitted blocker row is invalid"
        )
    return {
        **blocker,
        "nonclaim_state_digest": _sha(
            row["nonclaim_state_digest"],
            "operation-recovery permitted blocker nonclaim state digest",
        ),
        "reference_row_digest": _sha(
            row["reference_row_digest"],
            "operation-recovery permitted blocker reference row digest",
        ),
    }


def _claim_release_row_set_digest(
    rows: Sequence[Mapping[str, Any]],
) -> str:
    return digest(
        [
            {
                "operation_id": row["operation_id"],
                "row_digest": row["row_digest"],
                "nonclaim_state_digest": row["nonclaim_state_digest"],
            }
            for row in rows
        ]
    )


def _claim_release_permitted_blocker_row_set_digest(
    rows: Sequence[Mapping[str, Any]],
) -> str:
    return digest(
        [
            {
                "operation_id": row["operation_id"],
                "row_digest": row["row_digest"],
                "nonclaim_state_digest": row["nonclaim_state_digest"],
                "reference_row_digest": row["reference_row_digest"],
            }
            for row in rows
        ]
    )


def _claim_release_guard_exclusion_set_digest(
    selected_rows: Sequence[Mapping[str, Any]],
    permitted_rows: Sequence[Mapping[str, Any]],
) -> str:
    return digest(
        sorted(
            [row["operation_id"] for row in selected_rows]
            + [row["operation_id"] for row in permitted_rows]
        )
    )


def _claim_release_pair_counts(
    rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        pair = (row["bank_id"], row["operation_type"])
        counts[pair] = counts.get(pair, 0) + 1
    return counts


def _claim_release_artifact_paths(values: Mapping[str, Any]) -> dict[str, str]:
    labels = {
        "rollback_bundle_path": "claim-release rollback bundle path",
        "authorization_receipt_path": (
            "claim-release authorization receipt path"
        ),
        "application_receipt_path": "claim-release application receipt path",
        "verification_receipt_path": (
            "claim-release verification receipt path"
        ),
        "rollback_receipt_path": "claim-release rollback receipt path",
    }
    paths = {
        key: _absolute_path(values[key], f"operation-recovery {label}")
        for key, label in labels.items()
    }
    if (
        len(
            {
                unicodedata.normalize("NFD", value.casefold())
                for value in paths.values()
            }
        )
        != len(paths)
    ):
        raise OperationRecoveryError(
            "operation-recovery claim-release artifact paths must be distinct"
        )
    return paths


def create_claim_release_plan(
    predecessor_classification_value: Mapping[str, Any],
    live_classification_value: Mapping[str, Any],
    *,
    reference_plan: Mapping[str, Any],
    permitted_blocker_rows: Sequence[Mapping[str, Any]],
    nonclaim_state_digests: Mapping[str, Any],
    candidate_release: Mapping[str, Any],
    installation_authority: Mapping[str, Any],
    rollback_encryption: Mapping[str, Any],
    rollback_bundle_path: str,
    authorization_receipt_path: str,
    application_receipt_path: str,
    verification_receipt_path: str,
    rollback_receipt_path: str,
    created_at: int | None = None,
) -> Mapping[str, Any]:
    """Build a read-only, unapproved plan for exact terminal-claim cleanup."""
    planned_at = (
        int(time.time())
        if created_at is None
        else _integer(created_at, "claim-release plan created-at")
    )
    predecessor = verify_global_queue_blocker_classification(
        predecessor_classification_value,
        now=planned_at,
        allow_expired=True,
    )
    live = verify_global_queue_blocker_classification(
        live_classification_value,
        now=planned_at,
    )
    candidate = _candidate_release(candidate_release)
    authority = _installation_authority(installation_authority)
    _assert_installation_authority_schema(
        authority,
        plan_schema_version=2,
    )
    reference = verify_requeue_plan(
        reference_plan,
        now=planned_at,
        allow_expired=True,
    )
    reference_selected = reference["selected_operations"]
    reference_cohort_ids = sorted(
        row["operation_id"] for row in reference["cohort"]["operations"]
    )
    reference_selected_ids = [
        row["operation_id"] for row in reference_selected
    ]
    if (
        live["classifier_candidate_release"] != candidate
        or predecessor["classification_digest"]
        == live["classification_digest"]
        or predecessor["observed_at"] >= live["observed_at"]
        or predecessor["installation_authority"] != authority
        or live["installation_authority"] != authority
        or predecessor["blockers"] != live["blockers"]
        or predecessor["reference_plan_digest"] != reference["plan_digest"]
        or live["reference_plan_digest"] != reference["plan_digest"]
        or predecessor["reference_selected_operation_ids_digest"]
        != digest(sorted(reference_selected_ids))
        or live["reference_selected_operation_ids_digest"]
        != digest(sorted(reference_selected_ids))
        or not reference_selected
        or len(reference_selected) > len(reference_cohort_ids)
        or predecessor["guard_contract_version"]
        != live["guard_contract_version"]
        or predecessor["guard_contract_digest"]
        != live["guard_contract_digest"]
        or predecessor["blocker_count"]
        != EXPECTED_CLAIM_RELEASE_ROW_COUNT
        or predecessor["status_counts"]
        != EXPECTED_CLAIM_RELEASE_STATUS_COUNTS
        or predecessor["bank_counts"]
        != EXPECTED_CLAIM_RELEASE_BANK_COUNTS
        or predecessor["operation_type_counts"]
        != EXPECTED_CLAIM_RELEASE_TYPE_COUNTS
        or _claim_release_pair_counts(predecessor["blockers"])
        != EXPECTED_CLAIM_RELEASE_PAIR_COUNTS
    ):
        raise OperationRecoveryError(
            "operation-recovery claim-release classification differs"
        )
    if not isinstance(nonclaim_state_digests, Mapping):
        raise OperationRecoveryError(
            "operation-recovery claim-release nonclaim evidence is invalid"
        )
    blocker_ids = [row["operation_id"] for row in live["blockers"]]
    if set(nonclaim_state_digests) != set(blocker_ids):
        raise OperationRecoveryError(
            "operation-recovery claim-release nonclaim evidence differs"
        )
    rows = [
        _claim_release_row(
            {
                **row,
                "nonclaim_state_digest": nonclaim_state_digests[
                    row["operation_id"]
                ],
            }
        )
        for row in live["blockers"]
    ]
    if rows != sorted(
        rows,
        key=lambda row: (row["created_at"], row["operation_id"]),
    ):
        raise OperationRecoveryError(
            "operation-recovery claim-release rows are not ordered"
        )
    if isinstance(permitted_blocker_rows, (str, bytes)) or not isinstance(
        permitted_blocker_rows,
        Sequence,
    ):
        raise OperationRecoveryError(
            "operation-recovery claim-release permitted blocker evidence is invalid"
        )
    permitted = [
        _claim_release_permitted_blocker_row(row)
        for row in permitted_blocker_rows
    ]
    permitted_by_id = {row["operation_id"]: row for row in permitted}
    reference_by_id = {
        row["operation_id"]: row for row in reference_selected
    }
    if (
        len(permitted) != len(reference_selected)
        or len(permitted_by_id) != len(permitted)
        or set(permitted_by_id) != set(reference_by_id)
        or permitted
        != sorted(
            permitted,
            key=lambda row: (row["created_at"], row["operation_id"]),
        )
        or any(
            permitted_by_id[operation_id]["operation_type"]
            != item["operation_type"]
            or permitted_by_id[operation_id]["status"]
            != item["expected_status"]
            or permitted_by_id[operation_id]["task_payload_digest"]
            != item["task_payload_digest"]
            or permitted_by_id[operation_id]["reference_row_digest"]
            != item["row_digest"]
            for operation_id, item in reference_by_id.items()
        )
        or set(permitted_by_id) & {row["operation_id"] for row in rows}
    ):
        raise OperationRecoveryError(
            "operation-recovery claim-release permitted blocker evidence differs"
        )
    paths = _claim_release_artifact_paths(
        {
            "rollback_bundle_path": rollback_bundle_path,
            "authorization_receipt_path": authorization_receipt_path,
            "application_receipt_path": application_receipt_path,
            "verification_receipt_path": verification_receipt_path,
            "rollback_receipt_path": rollback_receipt_path,
        }
    )
    body = {
        "schema_version": 2,
        "kind": "operation-recovery-claim-release-plan",
        "authority": "unapproved-plan",
        "mutation_authorized": False,
        "candidate_release": candidate,
        "installation_authority": authority,
        "predecessor_classification_digest": predecessor[
            "classification_digest"
        ],
        "live_classification_digest": live["classification_digest"],
        "reference_plan_digest": reference["plan_digest"],
        "reference_cohort_operation_ids": reference_cohort_ids,
        "reference_cohort_operation_ids_digest": digest(reference_cohort_ids),
        "reference_selected_operation_ids_digest": digest(
            sorted(reference_selected_ids)
        ),
        "guard_contract_version": live["guard_contract_version"],
        "guard_contract_digest": live["guard_contract_digest"],
        "profile_id": "systalyze",
        "schema": "public",
        "pre_generation": live["generation_before"],
        "selected_rows": rows,
        "selected_row_count": len(rows),
        "selected_row_set_digest": _claim_release_row_set_digest(rows),
        "permitted_blocker_rows": permitted,
        "permitted_blocker_count": len(permitted),
        "permitted_blocker_row_set_digest": (
            _claim_release_permitted_blocker_row_set_digest(permitted)
        ),
        "guard_exclusion_set_digest": _claim_release_guard_exclusion_set_digest(
            rows,
            permitted,
        ),
        "status_counts": dict(live["status_counts"]),
        "bank_counts": dict(live["bank_counts"]),
        "operation_type_counts": dict(live["operation_type_counts"]),
        "rollback_encryption": _rollback_encryption(rollback_encryption),
        **paths,
        "created_at": planned_at,
        "expires_at": planned_at + MAX_PLAN_LIFETIME_SECONDS,
    }
    return {**body, "plan_digest": digest(body)}


def verify_claim_release_plan(
    value: Any,
    *,
    now: int | None = None,
    allow_expired: bool = False,
) -> Mapping[str, Any]:
    plan = _closed(
        _normalized(value),
        CLAIM_RELEASE_PLAN_KEYS,
        "operation-recovery claim-release plan",
    )
    rows_value = plan["selected_rows"]
    permitted_value = plan["permitted_blocker_rows"]
    if not isinstance(rows_value, list) or not isinstance(permitted_value, list):
        raise OperationRecoveryError(
            "operation-recovery claim-release rows are invalid"
        )
    rows = [_claim_release_row(row) for row in rows_value]
    permitted = [
        _claim_release_permitted_blocker_row(row) for row in permitted_value
    ]
    identifiers = [row["operation_id"] for row in rows]
    permitted_identifiers = [row["operation_id"] for row in permitted]
    cohort_identifiers_value = plan["reference_cohort_operation_ids"]
    if not isinstance(cohort_identifiers_value, list):
        raise OperationRecoveryError(
            "operation-recovery claim-release reference cohort is invalid"
        )
    cohort_identifiers = [
        _operation_id(value) for value in cohort_identifiers_value
    ]
    created_at = _integer(plan["created_at"], "claim-release plan created-at")
    expires_at = _integer(plan["expires_at"], "claim-release plan expires-at")
    observed_at = (
        int(time.time())
        if now is None
        else _integer(now, "claim-release plan verification time")
    )
    status_counts = _count_map(plan["status_counts"], "claim-release status counts")
    bank_counts = _count_map(plan["bank_counts"], "claim-release bank counts")
    type_counts = _count_map(
        plan["operation_type_counts"],
        "claim-release operation type counts",
    )
    row_status_counts = _queue_blocker_counts(rows, "status")
    row_bank_counts = _queue_blocker_counts(rows, "bank_id")
    row_type_counts = _queue_blocker_counts(rows, "operation_type")
    row_pair_counts = _claim_release_pair_counts(rows)
    if (
        plan["schema_version"] != 2
        or plan["kind"] != "operation-recovery-claim-release-plan"
        or plan["authority"] != "unapproved-plan"
        or plan["mutation_authorized"] is not False
        or plan["profile_id"] != "systalyze"
        or plan["schema"] != "public"
        or len(rows) != EXPECTED_CLAIM_RELEASE_ROW_COUNT
        or len(identifiers) != len(set(identifiers))
        or not permitted
        or len(permitted) > len(cohort_identifiers)
        or len(permitted_identifiers) != len(set(permitted_identifiers))
        or len(cohort_identifiers) != sum(EXPECTED_OPERATION_COUNTS.values())
        or len(cohort_identifiers) != len(set(cohort_identifiers))
        or cohort_identifiers != sorted(cohort_identifiers)
        or not set(permitted_identifiers).issubset(cohort_identifiers)
        or set(identifiers) & set(permitted_identifiers)
        or rows
        != sorted(rows, key=lambda row: (row["created_at"], row["operation_id"]))
        or permitted
        != sorted(
            permitted,
            key=lambda row: (row["created_at"], row["operation_id"]),
        )
        or plan["selected_row_count"] != EXPECTED_CLAIM_RELEASE_ROW_COUNT
        or plan["selected_row_set_digest"]
        != _claim_release_row_set_digest(rows)
        or plan["permitted_blocker_count"] != len(permitted)
        or plan["permitted_blocker_row_set_digest"]
        != _claim_release_permitted_blocker_row_set_digest(permitted)
        or plan["reference_cohort_operation_ids_digest"]
        != digest(cohort_identifiers)
        # Plan creation proves that the permitted IDs exactly equal the
        # reference requeue plan's selected IDs. Recomputing the digest here
        # preserves that binding without reopening the reference artifact.
        or plan["reference_selected_operation_ids_digest"]
        != digest(sorted(permitted_identifiers))
        or plan["guard_exclusion_set_digest"]
        != _claim_release_guard_exclusion_set_digest(rows, permitted)
        or status_counts != EXPECTED_CLAIM_RELEASE_STATUS_COUNTS
        or bank_counts != EXPECTED_CLAIM_RELEASE_BANK_COUNTS
        or type_counts != EXPECTED_CLAIM_RELEASE_TYPE_COUNTS
        or status_counts != row_status_counts
        or bank_counts != row_bank_counts
        or type_counts != row_type_counts
        or row_pair_counts != EXPECTED_CLAIM_RELEASE_PAIR_COUNTS
        or expires_at - created_at != MAX_PLAN_LIFETIME_SECONDS
    ):
        raise OperationRecoveryError(
            "operation-recovery claim-release plan is invalid"
        )
    if not allow_expired and observed_at >= expires_at:
        raise OperationRecoveryError(
            "operation-recovery claim-release plan expired"
        )
    paths = _claim_release_artifact_paths(plan)
    authority = _installation_authority(plan["installation_authority"])
    _assert_installation_authority_schema(
        authority,
        plan_schema_version=2,
    )
    body = {
        "schema_version": 2,
        "kind": "operation-recovery-claim-release-plan",
        "authority": "unapproved-plan",
        "mutation_authorized": False,
        "candidate_release": _candidate_release(plan["candidate_release"]),
        "installation_authority": authority,
        "predecessor_classification_digest": _sha(
            plan["predecessor_classification_digest"],
            "predecessor classification digest",
        ),
        "live_classification_digest": _sha(
            plan["live_classification_digest"],
            "live classification digest",
        ),
        "reference_plan_digest": _sha(
            plan["reference_plan_digest"],
            "claim-release reference plan digest",
        ),
        "reference_cohort_operation_ids": cohort_identifiers,
        "reference_cohort_operation_ids_digest": _sha(
            plan["reference_cohort_operation_ids_digest"],
            "claim-release reference cohort operation IDs digest",
        ),
        "reference_selected_operation_ids_digest": _sha(
            plan["reference_selected_operation_ids_digest"],
            "claim-release reference selected operation IDs digest",
        ),
        "guard_contract_version": _integer(
            plan["guard_contract_version"],
            "claim-release guard contract version",
            minimum=1,
        ),
        "guard_contract_digest": _sha(
            plan["guard_contract_digest"],
            "claim-release guard contract digest",
        ),
        "profile_id": "systalyze",
        "schema": "public",
        "pre_generation": _text(
            plan["pre_generation"],
            "claim-release pre-generation",
        ),
        "selected_rows": rows,
        "selected_row_count": EXPECTED_CLAIM_RELEASE_ROW_COUNT,
        "selected_row_set_digest": _sha(
            plan["selected_row_set_digest"],
            "claim-release selected-row-set digest",
        ),
        "permitted_blocker_rows": permitted,
        "permitted_blocker_count": len(permitted),
        "permitted_blocker_row_set_digest": _sha(
            plan["permitted_blocker_row_set_digest"],
            "claim-release permitted blocker row-set digest",
        ),
        "guard_exclusion_set_digest": _sha(
            plan["guard_exclusion_set_digest"],
            "claim-release guard exclusion set digest",
        ),
        "status_counts": status_counts,
        "bank_counts": bank_counts,
        "operation_type_counts": type_counts,
        "rollback_encryption": _rollback_encryption(
            plan["rollback_encryption"]
        ),
        **paths,
        "created_at": created_at,
        "expires_at": expires_at,
    }
    if _sha(plan["plan_digest"], "claim-release plan digest") != digest(body):
        raise OperationRecoveryError(
            "operation-recovery claim-release plan digest differs"
        )
    return {**body, "plan_digest": plan["plan_digest"]}
