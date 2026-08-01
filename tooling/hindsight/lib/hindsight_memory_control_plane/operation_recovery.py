"""Closed-schema planning for detached async-operation recovery."""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
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
OPERATION_STATUSES = (
    "pending",
    "processing",
    "completed",
    "failed",
    "cancelled",
)
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


def _installation_authority(value: Any) -> dict[str, Any]:
    authority = _closed(
        _normalized(value),
        INSTALLATION_AUTHORITY_KEYS,
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
    if (
        checked["profile_id"] != "systalyze"
        or checked["schema"] != "public"
        or checked["bank_id"] != "engineering"
        or VERSION.fullmatch(checked["installed_release_version"]) is None
        or checked["recorded_data_identity_digest"]
        == checked["observed_data_identity_digest"]
    ):
        raise OperationRecoveryError(
            "operation-recovery installation authority is invalid"
        )
    return checked


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


def _queue_blocker(value: Any, *, include_digest: bool) -> dict[str, Any]:
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
        or (in_selected and status != "processing")
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


def _count_map(value: Any, label: str) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise OperationRecoveryError(f"{label} is invalid")
    checked = {}
    for key, count in value.items():
        text = _text(key, f"{label} key", maximum=256)
        checked[text] = _integer(count, f"{label} count", minimum=1)
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
        "installation_authority": _installation_authority(
            classification["installation_authority"]
        ),
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
