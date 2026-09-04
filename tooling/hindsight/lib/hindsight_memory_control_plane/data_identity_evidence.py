"""Supported payload-free evidence collection for data-identity rebinds."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import stat
from typing import Any

from .canonical import digest
from .data_identity_rebind import (
    DataIdentityRebindError,
    database_continuity_projection,
    verify_rebind_evidence,
)
from .operation_recovery_runtime import (
    live_row_digest,
    read_generation,
    read_safe_operation_rows,
)


def _positive_integer(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise DataIdentityRebindError(f"{label} is invalid")
    return value


def build_postgres_evidence(
    binding: Mapping[str, Any],
    *,
    system_identifier: str,
) -> dict[str, Any]:
    """Bind one verified pg0 registration to its filesystem identity."""
    if (
        not isinstance(system_identifier, str)
        or not system_identifier.isascii()
        or not system_identifier.isdecimal()
    ):
        raise DataIdentityRebindError(
            "PostgreSQL system identifier is invalid"
        )
    try:
        postgres_root = Path(binding["data_dir"]).resolve(strict=True)
        data_root = postgres_root.parent.resolve(strict=True)
        postgres_metadata = postgres_root.lstat()
        data_metadata = data_root.lstat()
        declared_device = _positive_integer(
            binding["data_device"],
            "PostgreSQL data device",
        )
        declared_inode = _positive_integer(
            binding["data_inode"],
            "PostgreSQL data inode",
        )
        postmaster_pid = _positive_integer(
            binding["pid"],
            "PostgreSQL postmaster PID",
        )
        postmaster_start_time = _positive_integer(
            binding["started_at"],
            "PostgreSQL postmaster start time",
        )
    except DataIdentityRebindError:
        raise
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise DataIdentityRebindError(
            "PostgreSQL connection identity is unavailable"
        ) from error
    if (
        postgres_root.is_symlink()
        or data_root.is_symlink()
        or not stat.S_ISDIR(postgres_metadata.st_mode)
        or not stat.S_ISDIR(data_metadata.st_mode)
        or postgres_metadata.st_dev != declared_device
        or postgres_metadata.st_ino != declared_inode
        or postgres_metadata.st_uid not in {0, os.geteuid()}
        or data_metadata.st_uid not in {0, os.geteuid()}
    ):
        raise DataIdentityRebindError(
            "PostgreSQL connection identity differs"
        )
    body = {
        "system_identifier": system_identifier,
        "data_root": str(data_root),
        "data_root_device": data_metadata.st_dev,
        "data_root_inode": data_metadata.st_ino,
        "postgres_data_root": str(postgres_root),
        "postgres_data_device": postgres_metadata.st_dev,
        "postgres_data_inode": postgres_metadata.st_ino,
        "postmaster_pid": postmaster_pid,
        "postmaster_start_time": postmaster_start_time,
    }
    return {**body, "connection_identity_digest": digest(body)}


def _seal_database_evidence(
    postgres: Mapping[str, Any],
    database: Mapping[str, Any],
) -> dict[str, Any]:
    body = dict(database)
    try:
        snapshot = {
            "postgres": dict(postgres),
            "generation": body["generation_before"],
            "observed_at": body["observed_at"],
            "bank_set_digest": body["bank_set_digest"],
            "codex_document_count": body["codex_document_count"],
            "codex_manifest_digest": body["codex_manifest_digest"],
            "schema_digest": body["schema_digest"],
        }
        if "pending_operation_set_digest" in body:
            snapshot["pending_operation_set_digest"] = body[
                "pending_operation_set_digest"
            ]
    except (KeyError, TypeError) as error:
        raise DataIdentityRebindError(
            "database evidence is invalid"
        ) from error
    body["snapshot_digest"] = digest(snapshot)
    return body


def build_rebind_evidence(
    *,
    profile_id: str,
    collected_at: int,
    expires_at: int,
    postgres: Mapping[str, Any],
    database: Mapping[str, Any],
    backup: Mapping[str, Any],
    restore: Mapping[str, Any],
    safety: Mapping[str, Any],
    now: int,
) -> dict[str, Any]:
    """Seal and verify one complete rebind evidence artifact."""
    value = {
        "schema_version": 2,
        "profile_id": profile_id,
        "collected_at": collected_at,
        "expires_at": expires_at,
        "postgres": dict(postgres),
        "database": _seal_database_evidence(postgres, database),
        "backup": dict(backup),
        "restore": dict(restore),
        "safety": dict(safety),
    }
    return dict(verify_rebind_evidence(value, now=now))


def refresh_rebind_evidence(
    base_evidence: Mapping[str, Any],
    *,
    postgres: Mapping[str, Any],
    database: Mapping[str, Any],
    now: int,
) -> dict[str, Any]:
    """Refresh live identity and observation without renewing authority."""
    base = verify_rebind_evidence(base_evidence, now=now)
    try:
        database_body = (
            dict(database)
            if base["schema_version"] == 2
            else {
                key: database[key]
                for key in base["database"]
                if key != "snapshot_digest"
            }
        )
    except (KeyError, TypeError) as error:
        raise DataIdentityRebindError(
            "database evidence is invalid"
        ) from error
    sealed_database = _seal_database_evidence(postgres, database_body)
    if (
        database_continuity_projection(base["database"])
        != database_continuity_projection(sealed_database)
        or type(sealed_database.get("observed_at")) is not int
        or sealed_database["observed_at"] < base["database"]["observed_at"]
    ):
        raise DataIdentityRebindError("database continuity differs")
    value = {
        **base,
        "postgres": dict(postgres),
        "database": sealed_database,
    }
    return dict(verify_rebind_evidence(value, now=now))


async def read_database_evidence(
    connection: Any,
    *,
    postgres: Mapping[str, Any],
    profile_id: str,
    schema: str = "public",
) -> dict[str, Any]:
    """Read a payload-free projection in one repeatable-read transaction."""
    if profile_id != "systalyze" or schema != "public":
        raise DataIdentityRebindError("database evidence scope is invalid")
    async with connection.transaction(
        isolation="repeatable_read",
        readonly=True,
    ):
        generation_before = await read_generation(
            connection,
            schema,
            profile_id,
        )
        observed_at = await connection.fetchval(
            "SELECT floor(extract(epoch FROM clock_timestamp()))::bigint"
        )
        bank_rows = await connection.fetch(
            "SELECT bank_id FROM public.banks ORDER BY bank_id"
        )
        manifest = await connection.fetchrow(
            """
            SELECT count(*)::bigint AS document_count,
                   encode(
                       sha256(
                           convert_to(
                               coalesce(
                                   jsonb_agg(
                                       jsonb_build_object(
                                           'id', id,
                                           'created_at', to_char(
                                               created_at AT TIME ZONE 'UTC',
                                               'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                                           ),
                                           'updated_at', to_char(
                                               updated_at AT TIME ZONE 'UTC',
                                               'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                                           ),
                                           'text_digest', encode(
                                               sha256(convert_to(original_text, 'UTF8')),
                                               'hex'
                                           ),
                                           'retain_params', coalesce(
                                               retain_params,
                                               '{}'::jsonb
                                           ),
                                           'tags', to_jsonb(tags)
                                       ) ORDER BY created_at, id
                                   )::text,
                                   '[]'
                               ),
                               'UTF8'
                           )
                       ),
                       'hex'
                   ) AS manifest_digest
            FROM public.documents
            WHERE bank_id = 'codex'
            """
        )
        pending_operation_count = await connection.fetchval(
            """
            SELECT count(*)::bigint
            FROM public.async_operations
            WHERE status IN ('pending', 'processing')
            """
        )
        generic_import_receipt_count = await connection.fetchval(
            """
            SELECT count(*)::bigint
            FROM public.documents
            WHERE id::text LIKE 'generic-import:%'
            """
        )
        pending_rows = await read_safe_operation_rows(
            connection,
            schema=schema,
            bank_id="engineering",
            operation_ids=None,
            statuses=("pending", "processing"),
        )
        schema_rows = await connection.fetch(
            """
            SELECT table_name,
                   column_name,
                   data_type,
                   udt_schema,
                   udt_name,
                   is_nullable,
                   coalesce(column_default, '') AS column_default,
                   is_identity,
                   is_generated
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, column_name
            """
        )
        generation_after = await read_generation(
            connection,
            schema,
            profile_id,
        )
    if generation_before != generation_after:
        raise DataIdentityRebindError(
            "database generation changed during evidence read"
        )
    try:
        bank_ids = [str(row["bank_id"]) for row in bank_rows]
        document_count = manifest["document_count"]
        manifest_digest = manifest["manifest_digest"]
        schema_projection = [dict(row) for row in schema_rows]
    except (KeyError, TypeError) as error:
        raise DataIdentityRebindError(
            "database evidence is invalid"
        ) from error
    if type(pending_operation_count) is not int or pending_operation_count != len(
        pending_rows
    ):
        raise DataIdentityRebindError(
            "database pending operation inventory differs"
        )
    pending_operations = []
    for row in pending_rows:
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
        pending_operations.append(
            {
                "bank_id": row["bank_id"],
                **body,
                "row_digest": live_row_digest(row),
            }
        )
    pending_operations.sort(key=lambda item: item["operation_id"])
    body = {
        "observed_at": observed_at,
        "generation_before": generation_before,
        "generation_after": generation_after,
        "bank_ids": bank_ids,
        "bank_set_digest": digest({"bank_ids": bank_ids}),
        "codex_document_count": document_count,
        "codex_manifest_digest": manifest_digest,
        "pending_operation_count": pending_operation_count,
        "pending_operations": pending_operations,
        "pending_operation_set_digest": digest(
            {"operations": pending_operations}
        ),
        "generic_import_receipt_count": generic_import_receipt_count,
        "schema_digest": digest(schema_projection),
    }
    return _seal_database_evidence(postgres, body)
