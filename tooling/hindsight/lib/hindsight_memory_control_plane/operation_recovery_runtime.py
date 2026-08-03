"""Privileged PostgreSQL seam for detached async-operation recovery.

This module deliberately has no Hindsight API, provider, or HTTP dependency.
It accepts an already authenticated local PostgreSQL connection and emits only
the closed, payload-free evidence consumed by :mod:`operation_recovery`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import time
import uuid
from typing import Any

from .operation_recovery import (
    EXPECTED_CLAIM_RELEASE_PERMITTED_BLOCKER_COUNT,
    EXPECTED_CLAIM_RELEASE_ROW_COUNT,
    EXPECTED_OPERATION_COUNTS,
    OperationRecoveryError,
)
from .canonical import digest


IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\Z")
PLANNING_STATE_TABLES = (
    "async_operations",
    "banks",
    "directives",
    "documents",
    "entities",
    "invalidated_memory_units",
    "memory_units",
    "mental_models",
    "webhooks",
)
GENERATION_TABLE = "hindsight_migration_generation"
GENERATION_TRIGGER = "hindsight_migration_generation_bump"
SAFE_OPERATION_QUERY = """
SELECT
    operation_id::text AS operation_id,
    bank_id,
    operation_type,
    status,
    to_char(created_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS created_at,
    to_char(updated_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS updated_at,
    CASE WHEN completed_at IS NULL THEN NULL
         ELSE to_char(completed_at AT TIME ZONE 'UTC',
                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END AS completed_at,
    retry_count,
    CASE WHEN next_retry_at IS NULL THEN NULL
         ELSE to_char(next_retry_at AT TIME ZONE 'UTC',
                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END AS next_retry_at,
    (worker_id IS NOT NULL) AS worker_id_present,
    CASE WHEN worker_id IS NULL THEN NULL
         ELSE encode(sha256(convert_to(worker_id, 'UTF8')), 'hex')
    END AS worker_id_digest,
    CASE WHEN claimed_at IS NULL THEN NULL
         ELSE to_char(claimed_at AT TIME ZONE 'UTC',
                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END AS claimed_at,
    (task_payload IS NOT NULL) AS task_payload_present,
    CASE WHEN task_payload IS NULL THEN NULL
         ELSE encode(sha256(convert_to(task_payload::text, 'UTF8')), 'hex')
    END AS task_payload_digest,
    encode(
        sha256(
            convert_to(
                COALESCE(result_metadata, '{{}}'::jsonb)::text,
                'UTF8'
            )
        ),
        'hex'
    ) AS result_metadata_digest,
    CASE
        WHEN error_message IS NULL OR error_message = '' THEN 'none'
        WHEN lower(error_message) ~
             '(auth|credential|token|unauthori[sz]ed|forbidden|401|403)'
            THEN 'authentication'
        WHEN lower(error_message) ~
             '(capacity|quota|rate.?limit|usage.?limit|429|exhaust)'
            THEN 'provider_capacity'
        WHEN lower(error_message) ~
             '(connect|network|timeout|transport|unavailable|hatchery|502|503|504)'
            THEN 'provider_transport'
        WHEN lower(error_message) ~
             '(internal|traceback|exception|500)'
            THEN 'internal'
        ELSE 'unknown'
    END AS error_category,
    CASE WHEN error_message IS NULL OR error_message = '' THEN NULL
         ELSE encode(
             sha256(convert_to(error_message, 'UTF8')),
             'hex'
         )
    END AS error_digest
FROM {schema}.async_operations
WHERE bank_id = $1
  AND operation_type = ANY($2::text[])
  AND ($3::uuid[] IS NULL OR operation_id = ANY($3::uuid[]))
  AND ($4::text[] IS NULL OR status = ANY($4::text[]))
ORDER BY created_at, operation_id
"""
QUEUE_BLOCKER_PREDICATE = """(
    status = 'processing'
    OR (
        status IN ('pending', 'failed', 'cancelled')
        AND (worker_id IS NOT NULL OR claimed_at IS NOT NULL)
        AND NOT (operation_id = ANY($1::uuid[]))
    )
)"""
QUEUE_BLOCKER_GUARD_CONTRACT_VERSION = 1
QUEUE_BLOCKER_GUARD_CONTRACT_DIGEST = digest(
    {
        "version": QUEUE_BLOCKER_GUARD_CONTRACT_VERSION,
        "predicate": QUEUE_BLOCKER_PREDICATE,
    }
)
GLOBAL_QUEUE_BLOCKER_QUERY = f"""
SELECT
    operation_id::text AS operation_id,
    bank_id,
    operation_type,
    status,
    to_char(created_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS created_at,
    to_char(updated_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS updated_at,
    CASE WHEN completed_at IS NULL THEN NULL
         ELSE to_char(completed_at AT TIME ZONE 'UTC',
                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END AS completed_at,
    retry_count,
    CASE WHEN next_retry_at IS NULL THEN NULL
         ELSE to_char(next_retry_at AT TIME ZONE 'UTC',
                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END AS next_retry_at,
    (worker_id IS NOT NULL) AS worker_id_present,
    CASE WHEN worker_id IS NULL THEN NULL
         ELSE encode(sha256(convert_to(worker_id, 'UTF8')), 'hex')
    END AS worker_id_digest,
    CASE WHEN claimed_at IS NULL THEN NULL
         ELSE to_char(claimed_at AT TIME ZONE 'UTC',
                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END AS claimed_at,
    (task_payload IS NOT NULL) AS task_payload_present,
    CASE WHEN task_payload IS NULL THEN NULL
         ELSE encode(sha256(convert_to(task_payload::text, 'UTF8')), 'hex')
    END AS task_payload_digest,
    (operation_id = ANY($2::uuid[])) AS in_reference_cohort,
    (operation_id = ANY($1::uuid[])) AS in_reference_selected_set,
    CASE status
        WHEN 'processing' THEN 'processing'
        WHEN 'pending' THEN 'claimed_pending'
        WHEN 'failed' THEN 'claimed_failed'
        WHEN 'cancelled' THEN 'claimed_cancelled'
    END AS blocker_reason
FROM {{schema}}.async_operations
WHERE {QUEUE_BLOCKER_PREDICATE}
ORDER BY created_at, operation_id
"""
CLAIM_RELEASE_NONCLAIM_DIGEST_SQL = """
encode(
    sha256(
        convert_to(
            jsonb_build_object(
                'operation_id', operation_id::text,
                'bank_id', bank_id,
                'operation_type', operation_type,
                'status', status,
                'created_at', to_char(
                    created_at AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                ),
                'updated_at', to_char(
                    updated_at AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                ),
                'completed_at', CASE WHEN completed_at IS NULL THEN NULL
                    ELSE to_char(
                        completed_at AT TIME ZONE 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                    ) END,
                'retry_count', retry_count,
                'next_retry_at', CASE WHEN next_retry_at IS NULL THEN NULL
                    ELSE to_char(
                        next_retry_at AT TIME ZONE 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                    ) END,
                'task_payload', task_payload,
                'result_metadata', result_metadata,
                'error_message', error_message
            )::text,
            'UTF8'
        )
    ),
    'hex'
)
""".strip()
CLAIM_RELEASE_EVIDENCE_QUERY = f"""
SELECT
    operation_id::text AS operation_id,
    bank_id,
    operation_type,
    status,
    to_char(created_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS created_at,
    to_char(updated_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS updated_at,
    CASE WHEN completed_at IS NULL THEN NULL
         ELSE to_char(completed_at AT TIME ZONE 'UTC',
                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END AS completed_at,
    retry_count,
    CASE WHEN next_retry_at IS NULL THEN NULL
         ELSE to_char(next_retry_at AT TIME ZONE 'UTC',
                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END AS next_retry_at,
    (worker_id IS NOT NULL) AS worker_id_present,
    CASE WHEN worker_id IS NULL THEN NULL
         ELSE encode(sha256(convert_to(worker_id, 'UTF8')), 'hex')
    END AS worker_id_digest,
    CASE WHEN claimed_at IS NULL THEN NULL
         ELSE to_char(claimed_at AT TIME ZONE 'UTC',
                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END AS claimed_at,
    (task_payload IS NOT NULL) AS task_payload_present,
    CASE WHEN task_payload IS NULL THEN NULL
         ELSE encode(sha256(convert_to(task_payload::text, 'UTF8')), 'hex')
    END AS task_payload_digest,
    (operation_id = ANY($2::uuid[])) AS in_reference_cohort,
    (operation_id = ANY($3::uuid[])) AS in_reference_selected_set,
    CASE status
        WHEN 'processing' THEN 'processing'
        WHEN 'pending' THEN 'claimed_pending'
        WHEN 'failed' THEN 'claimed_failed'
        WHEN 'cancelled' THEN 'claimed_cancelled'
    END AS blocker_reason,
    {CLAIM_RELEASE_NONCLAIM_DIGEST_SQL} AS nonclaim_state_digest
FROM {{schema}}.async_operations
WHERE operation_id = ANY($1::uuid[])
ORDER BY created_at, operation_id
{{lock_clause}}
"""
CLAIM_RELEASE_PREIMAGE_QUERY = f"""
SELECT
    operation_id::text AS operation_id,
    worker_id,
    CASE WHEN claimed_at IS NULL THEN NULL
         ELSE to_char(claimed_at AT TIME ZONE 'UTC',
                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END AS claimed_at,
    {CLAIM_RELEASE_NONCLAIM_DIGEST_SQL} AS nonclaim_state_digest
FROM {{schema}}.async_operations
WHERE operation_id = ANY($1::uuid[])
ORDER BY operation_id
{{lock_clause}}
"""
CLAIM_RELEASE_BLOCKER_KEYS = (
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
)


def _quoted_identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        raise OperationRecoveryError(f"{label} is invalid")
    return f'"{value}"'


def _mapping(row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    try:
        return dict(row.items())
    except (AttributeError, TypeError, ValueError):
        raise OperationRecoveryError(
            "database returned malformed operation evidence"
        ) from None


def _read_private_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_absolute():
        raise OperationRecoveryError(f"{label} must be absolute")
    try:
        before = path.lstat()
        parent = path.parent.lstat()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or not stat.S_ISREG(opened.st_mode)
                or before.st_uid not in {0, os.geteuid()}
                or opened.st_uid not in {0, os.geteuid()}
                or stat.S_IMODE(opened.st_mode) & 0o022
                or not stat.S_ISDIR(parent.st_mode)
                or parent.st_uid not in {0, os.geteuid()}
                or stat.S_IMODE(parent.st_mode) & 0o077
                or opened.st_nlink != 1
                or (before.st_dev, before.st_ino)
                != (opened.st_dev, opened.st_ino)
                or opened.st_size > 65536
            ):
                raise OperationRecoveryError(f"{label} is not private")
            payload = os.read(descriptor, opened.st_size + 1)
            if len(payload) != opened.st_size:
                raise OperationRecoveryError(f"{label} changed while reading")
        finally:
            os.close(descriptor)
        value = json.loads(payload)
    except OperationRecoveryError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise OperationRecoveryError(f"{label} is unreadable") from error
    if not isinstance(value, dict):
        raise OperationRecoveryError(f"{label} is invalid")
    return value


def read_pg0_registration(profile_id: str) -> dict[str, Any]:
    """Read and pin the private pg0 registration for one live profile."""
    if profile_id != "systalyze":
        raise OperationRecoveryError("operation-recovery profile is invalid")
    registration = (
        Path.home() / ".pg0" / "instances" / "hindsight-embed-systalyze"
    )
    data_dir = registration / "data"
    manifest = _read_private_json(
        registration / "instance.json",
        "pg0 registration",
    )
    try:
        pid_lines = (data_dir / "postmaster.pid").read_text(
            encoding="ascii"
        ).splitlines()
        data_metadata = data_dir.lstat()
        registration_metadata = registration.lstat()
    except (OSError, UnicodeError) as error:
        raise OperationRecoveryError("pg0 process identity is unavailable") from error
    if (
        len(pid_lines) < 5
        or not pid_lines[0].isdigit()
        or not pid_lines[2].isdigit()
        or not pid_lines[3].isdigit()
        or not stat.S_ISDIR(data_metadata.st_mode)
        or not stat.S_ISDIR(registration_metadata.st_mode)
        or data_dir.is_symlink()
        or registration.is_symlink()
    ):
        raise OperationRecoveryError("pg0 process identity is invalid")
    required = {
        "data_dir",
        "port",
        "username",
        "password",
        "database",
        "pid",
    }
    if not required.issubset(manifest):
        raise OperationRecoveryError("pg0 registration is invalid")
    try:
        registered_data_dir = Path(manifest["data_dir"]).resolve(strict=True)
    except (OSError, TypeError, ValueError) as error:
        raise OperationRecoveryError("pg0 registration is invalid") from error
    port = int(pid_lines[3])
    pid = int(pid_lines[0])
    started_at = int(pid_lines[2])
    socket_dir = Path(pid_lines[4])
    socket_path = socket_dir / f".s.PGSQL.{port}"
    try:
        socket_metadata = socket_path.lstat()
    except OSError as error:
        raise OperationRecoveryError(
            "pg0 Unix socket identity is unavailable"
        ) from error
    if (
        registered_data_dir != data_dir.resolve(strict=True)
        or manifest["port"] != port
        or manifest["pid"] != pid
        or manifest["username"] != "hindsight"
        or manifest["database"] != "hindsight"
        or not isinstance(manifest["password"], str)
        or not manifest["password"]
        or not 1 <= port <= 65535
        or pid <= 0
        or started_at <= 0
        or not socket_dir.is_absolute()
        or "," in pid_lines[4]
        or not stat.S_ISSOCK(socket_metadata.st_mode)
        or socket_metadata.st_uid not in {0, os.geteuid()}
    ):
        raise OperationRecoveryError("pg0 registration is invalid")
    return {
        "instance": "hindsight-embed-systalyze",
        "data_dir": str(registered_data_dir),
        "data_device": data_metadata.st_dev,
        "data_inode": data_metadata.st_ino,
        "port": port,
        "pid": pid,
        "started_at": started_at,
        "socket_dir": str(socket_dir),
        "socket_path": str(socket_path),
        "database": manifest["database"],
        "user": manifest["username"],
        # The password is a local capability and is intentionally consumed by
        # the caller without being copied into evidence or diagnostics.
        "_password": manifest["password"],
    }


async def connect_verified_local_postgres(
    asyncpg: Any,
    binding: Mapping[str, Any],
    *,
    password: str,
    readonly: bool,
) -> Any:
    """Authenticate only after the connected Unix peer proves its exact PID.

    The capsule is intentionally macOS-specific. ``LOCAL_PEERPID`` is a Darwin
    socket option (level ``SOL_LOCAL`` 0, option 2) and is checked on the same
    connected descriptor that asyncpg uses for its authentication exchange.
    """
    if (
        not isinstance(password, str)
        or not password
        or type(binding.get("pid")) is not int
        or not isinstance(binding.get("socket_dir"), str)
        or not isinstance(binding.get("socket_path"), str)
    ):
        raise OperationRecoveryError("pg0 connection authority is invalid")
    expected_path = binding["socket_path"]
    expected_pid = binding["pid"]
    loop = asyncio.get_running_loop()
    original_connector = loop.create_unix_connection
    observed_peer_pids: list[int] = []

    async def verified_connector(
        protocol_factory: Any,
        path: str,
        *arguments: Any,
        **keywords: Any,
    ) -> Any:
        if (
            path != expected_path
            or arguments
            or keywords
            or observed_peer_pids
        ):
            raise OperationRecoveryError(
                "asyncpg Unix connection boundary changed"
            )
        peer_socket: socket.socket | None = socket.socket(
            socket.AF_UNIX,
            socket.SOCK_STREAM,
        )
        peer_socket.setblocking(False)
        try:
            await loop.sock_connect(peer_socket, path)
            peer_pid = struct.unpack(
                "i",
                peer_socket.getsockopt(0, 2, struct.calcsize("i")),
            )[0]
            if peer_pid != expected_pid:
                raise OperationRecoveryError(
                    "PostgreSQL Unix peer PID differs"
                )
            observed_peer_pids.append(peer_pid)
            connected = await loop.create_connection(
                protocol_factory,
                sock=peer_socket,
            )
            peer_socket = None
            return connected
        except (OSError, struct.error) as error:
            raise OperationRecoveryError(
                "PostgreSQL Unix peer identity is unavailable"
            ) from error
        finally:
            if peer_socket is not None:
                peer_socket.close()

    loop.create_unix_connection = verified_connector  # type: ignore[method-assign]
    connection = None
    try:
        connection = await asyncpg.connect(
            host=binding["socket_dir"],
            port=binding["port"],
            user=binding["user"],
            password=password,
            database=binding["database"],
            timeout=10,
            server_settings={
                "application_name": "hindsight-operation-recovery",
                "default_transaction_read_only": "on" if readonly else "off",
            },
        )
    finally:
        loop.create_unix_connection = original_connector  # type: ignore[method-assign]
    if observed_peer_pids != [expected_pid]:
        if connection is not None:
            await connection.close()
        raise OperationRecoveryError("PostgreSQL Unix peer was not verified")
    return connection


async def assert_connected_live_database(
    connection: Any,
    binding: Mapping[str, Any],
    *,
    expected_system_identifier: str,
) -> None:
    values = await connection.fetchrow(
        """
        SELECT current_database() AS database,
               current_user AS database_user,
               current_setting('data_directory') AS data_directory,
               current_setting('port')::integer AS port,
               inet_server_addr()::text AS address,
               (SELECT system_identifier::text
                FROM pg_control_system()) AS system_identifier
        """
    )
    row = _mapping(values)
    try:
        connected_data_dir = Path(row["data_directory"]).resolve(strict=True)
    except (KeyError, OSError, TypeError, ValueError):
        connected_data_dir = None
    if (
        row.get("database") != binding["database"]
        or row.get("database_user") != binding["user"]
        or connected_data_dir != Path(str(binding["data_dir"]))
        or row.get("port") != binding["port"]
        or "address" not in row
        or row["address"] is not None
        or row.get("system_identifier") != expected_system_identifier
    ):
        raise OperationRecoveryError(
            "connected database does not match the pinned live pg0 identity"
        )


async def read_generation(connection: Any, schema: str, profile_id: str) -> str:
    quoted_schema = _quoted_identifier(schema, "database schema")
    quoted_table = _quoted_identifier(
        GENERATION_TABLE,
        "migration generation table",
    )
    row = await connection.fetchrow(
        f"""
        SELECT generation,
               (
                   SELECT count(*)
                   FROM pg_catalog.pg_class AS c
                   JOIN pg_catalog.pg_namespace AS n
                     ON n.oid = c.relnamespace
                   WHERE n.nspname = $1
                     AND c.relkind IN ('r', 'p')
                     AND c.relname = ANY($3::text[])
                     AND NOT EXISTS (
                         SELECT 1
                         FROM pg_catalog.pg_trigger AS t
                         WHERE t.tgrelid = c.oid
                           AND t.tgname = $2
                           AND t.tgenabled <> 'D'
                     )
               ) AS missing_trigger_count,
               0::bigint AS reserved_guard_count
        FROM {quoted_schema}.{quoted_table}
        WHERE singleton
        """,
        schema,
        GENERATION_TRIGGER,
        list(PLANNING_STATE_TABLES),
    )
    if row is None:
        raise OperationRecoveryError("migration generation is unavailable")
    value = _mapping(row)
    generation = value.get("generation")
    if (
        type(generation) is not int
        or generation < 1
        or value.get("missing_trigger_count") != 0
    ):
        raise OperationRecoveryError(
            "migration generation trigger coverage is incomplete"
        )
    return f"{profile_id}:{schema}:{generation}"


async def read_safe_operation_rows(
    connection: Any,
    *,
    schema: str,
    bank_id: str,
    operation_ids: Sequence[str] | None,
    statuses: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Return safe metadata; payload and error text never leave PostgreSQL."""
    if bank_id != "engineering":
        raise OperationRecoveryError("operation-recovery bank is invalid")
    quoted_schema = _quoted_identifier(schema, "database schema")
    identifiers: list[uuid.UUID] | None = None
    if operation_ids is not None:
        try:
            identifiers = [uuid.UUID(value) for value in operation_ids]
        except (AttributeError, TypeError, ValueError) as error:
            raise OperationRecoveryError("operation ID set is invalid") from error
        if len(identifiers) != len(set(identifiers)):
            raise OperationRecoveryError("operation ID set contains duplicates")
    rows = await connection.fetch(
        SAFE_OPERATION_QUERY.format(schema=quoted_schema),
        bank_id,
        list(EXPECTED_OPERATION_COUNTS),
        identifiers,
        None if statuses is None else list(statuses),
    )
    return [_mapping(row) for row in rows]


async def read_global_queue_blockers(
    connection: Any,
    *,
    profile_id: str,
    schema: str,
    reference_cohort_operation_ids: Sequence[str],
    reference_selected_operation_ids: Sequence[str],
) -> tuple[str, str, list[dict[str, Any]]]:
    """Read every global apply-guard blocker without projecting payloads."""
    quoted_schema = _quoted_identifier(schema, "database schema")
    try:
        cohort_identifiers = [
            uuid.UUID(value) for value in reference_cohort_operation_ids
        ]
        selected_identifiers = [
            uuid.UUID(value) for value in reference_selected_operation_ids
        ]
    except (AttributeError, TypeError, ValueError) as error:
        raise OperationRecoveryError("operation ID set is invalid") from error
    if (
        len(cohort_identifiers) != len(set(cohort_identifiers))
        or len(selected_identifiers) != len(set(selected_identifiers))
    ):
        raise OperationRecoveryError("operation ID set contains duplicates")
    if not selected_identifiers:
        raise OperationRecoveryError("selected operation ID set is empty")
    if not set(selected_identifiers).issubset(cohort_identifiers):
        raise OperationRecoveryError(
            "selected operation ID set is not a cohort subset"
        )
    generation_before = await read_generation(
        connection,
        schema,
        profile_id,
    )
    async with connection.transaction(
        isolation="repeatable_read",
        readonly=True,
    ):
        rows = await connection.fetch(
            GLOBAL_QUEUE_BLOCKER_QUERY.format(schema=quoted_schema),
            selected_identifiers,
            cohort_identifiers,
        )
    generation_after = await read_generation(
        connection,
        schema,
        profile_id,
    )
    if generation_before != generation_after:
        raise OperationRecoveryError(
            "migration generation changed during queue blocker classification"
        )
    return generation_before, generation_after, [_mapping(row) for row in rows]


def _operation_identifiers(values: Sequence[str]) -> list[uuid.UUID]:
    try:
        identifiers = [uuid.UUID(value) for value in values]
    except (AttributeError, TypeError, ValueError) as error:
        raise OperationRecoveryError("operation ID set is invalid") from error
    if not identifiers or len(identifiers) != len(set(identifiers)):
        raise OperationRecoveryError(
            "operation ID set is empty or contains duplicates"
        )
    return identifiers


async def _fetch_claim_release_evidence(
    connection: Any,
    *,
    schema: str,
    identifiers: Sequence[uuid.UUID],
    reference_cohort_identifiers: Sequence[uuid.UUID],
    reference_selected_identifiers: Sequence[uuid.UUID],
    for_update: bool,
) -> list[dict[str, Any]]:
    quoted_schema = _quoted_identifier(schema, "database schema")
    rows = await connection.fetch(
        CLAIM_RELEASE_EVIDENCE_QUERY.format(
            schema=quoted_schema,
            lock_clause="FOR UPDATE" if for_update else "",
        ),
        list(identifiers),
        list(reference_cohort_identifiers),
        list(reference_selected_identifiers),
    )
    return [_mapping(row) for row in rows]


async def read_claim_release_evidence(
    connection: Any,
    *,
    profile_id: str,
    schema: str,
    operation_ids: Sequence[str],
    reference_cohort_operation_ids: Sequence[str],
    reference_selected_operation_ids: Sequence[str],
    expected_generation: str,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Read exact claim-cleanup evidence without projecting protected text."""
    identifiers = _operation_identifiers(operation_ids)
    try:
        reference_cohort_identifiers = [
            uuid.UUID(value) for value in reference_cohort_operation_ids
        ]
        reference_selected_identifiers = [
            uuid.UUID(value) for value in reference_selected_operation_ids
        ]
    except (AttributeError, TypeError, ValueError) as error:
        raise OperationRecoveryError("operation ID set is invalid") from error
    if (
        len(reference_cohort_identifiers)
        != len(set(reference_cohort_identifiers))
        or len(reference_selected_identifiers)
        != len(set(reference_selected_identifiers))
        or not set(reference_selected_identifiers).issubset(
            reference_cohort_identifiers
        )
        or not set(reference_selected_identifiers).issubset(identifiers)
    ):
        raise OperationRecoveryError("operation ID set is invalid")
    generation_before = await read_generation(connection, schema, profile_id)
    async with connection.transaction(
        isolation="repeatable_read",
        readonly=True,
    ):
        rows = await _fetch_claim_release_evidence(
            connection,
            schema=schema,
            identifiers=identifiers,
            reference_cohort_identifiers=reference_cohort_identifiers,
            reference_selected_identifiers=reference_selected_identifiers,
            for_update=False,
        )
    generation_after = await read_generation(connection, schema, profile_id)
    if (
        generation_before != generation_after
        or generation_before != expected_generation
    ):
        raise OperationRecoveryError(
            "migration generation changed during claim-release planning"
        )
    if {row.get("operation_id") for row in rows} != {
        str(identifier) for identifier in identifiers
    } or len(rows) != len(identifiers):
        raise OperationRecoveryError(
            "operation-recovery claim-release row set changed"
        )
    return generation_before, generation_after, rows


def _claim_release_row_digest(row: Mapping[str, Any]) -> str:
    return digest({key: row[key] for key in CLAIM_RELEASE_BLOCKER_KEYS})


def _claim_release_expected(plan: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    selected = plan.get("selected_rows")
    if (
        not isinstance(selected, list)
        or len(selected) != EXPECTED_CLAIM_RELEASE_ROW_COUNT
    ):
        raise OperationRecoveryError(
            "operation-recovery claim-release selected set is invalid"
        )
    expected = {}
    for item in selected:
        if not isinstance(item, Mapping):
            raise OperationRecoveryError(
                "operation-recovery claim-release selected set is invalid"
            )
        operation_id = item.get("operation_id")
        if not isinstance(operation_id, str) or operation_id in expected:
            raise OperationRecoveryError(
                "operation-recovery claim-release selected set is invalid"
            )
        expected[operation_id] = dict(item)
    _operation_identifiers(list(expected))
    return expected


def _claim_release_permitted_expected(
    plan: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    permitted_rows = plan.get("permitted_blocker_rows")
    if (
        not isinstance(permitted_rows, list)
        or len(permitted_rows)
        != EXPECTED_CLAIM_RELEASE_PERMITTED_BLOCKER_COUNT
    ):
        raise OperationRecoveryError(
            "operation-recovery claim-release permitted blocker set is invalid"
        )
    expected = {}
    for item in permitted_rows:
        if not isinstance(item, Mapping):
            raise OperationRecoveryError(
                "operation-recovery claim-release permitted blocker set is invalid"
            )
        operation_id = item.get("operation_id")
        if (
            not isinstance(operation_id, str)
            or operation_id in expected
            or item.get("in_reference_cohort") is not True
            or item.get("in_reference_selected_set") is not True
            or item.get("status") not in {"failed", "cancelled"}
            or item.get("worker_id_present") is not True
            or item.get("claimed_at") is None
        ):
            raise OperationRecoveryError(
                "operation-recovery claim-release permitted blocker set is invalid"
            )
        expected[operation_id] = dict(item)
    _operation_identifiers(list(expected))
    return expected


def _claim_release_expected_sets(
    plan: Mapping[str, Any],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    list[uuid.UUID],
]:
    selected = _claim_release_expected(plan)
    permitted = _claim_release_permitted_expected(plan)
    cohort_value = plan.get("reference_cohort_operation_ids")
    if not isinstance(cohort_value, list):
        raise OperationRecoveryError(
            "operation-recovery claim-release reference cohort is invalid"
        )
    try:
        cohort = [uuid.UUID(value) for value in cohort_value]
    except (AttributeError, TypeError, ValueError) as error:
        raise OperationRecoveryError(
            "operation-recovery claim-release reference cohort is invalid"
        ) from error
    if set(selected) & set(permitted):
        raise OperationRecoveryError(
            "operation-recovery claim-release blocker sets overlap"
        )
    if (
        len(cohort) != sum(EXPECTED_OPERATION_COUNTS.values())
        or len(cohort) != len(set(cohort))
        or not {
            uuid.UUID(value) for value in permitted.keys()
        }.issubset(cohort)
    ):
        raise OperationRecoveryError(
            "operation-recovery claim-release reference cohort is invalid"
        )
    return selected, permitted, cohort


def _claim_release_before_matches(
    row: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> bool:
    return (
        {key: row.get(key) for key in CLAIM_RELEASE_BLOCKER_KEYS}
        == {key: expected.get(key) for key in CLAIM_RELEASE_BLOCKER_KEYS}
        and row.get("nonclaim_state_digest")
        == expected.get("nonclaim_state_digest")
        and _claim_release_row_digest(row) == expected.get("row_digest")
    )


def _claim_release_after_matches(
    row: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> bool:
    preserved_keys = set(CLAIM_RELEASE_BLOCKER_KEYS) - {
        "worker_id_present",
        "worker_id_digest",
        "claimed_at",
    }
    return (
        row.get("worker_id_present") is False
        and row.get("worker_id_digest") is None
        and row.get("claimed_at") is None
        and {key: row.get(key) for key in preserved_keys}
        == {key: expected.get(key) for key in preserved_keys}
        and row.get("nonclaim_state_digest")
        == expected.get("nonclaim_state_digest")
    )


def _claim_release_groups_match(
    rows_by_id: Mapping[str, Mapping[str, Any]],
    expected: Mapping[str, Mapping[str, Any]],
    permitted: Mapping[str, Mapping[str, Any]],
    *,
    selected_matches: Callable[
        [Mapping[str, Any], Mapping[str, Any]],
        bool,
    ],
) -> bool:
    return (
        set(rows_by_id) == set(expected) | set(permitted)
        and all(
            selected_matches(rows_by_id[operation_id], item)
            for operation_id, item in expected.items()
        )
        and all(
            _claim_release_before_matches(rows_by_id[operation_id], item)
            for operation_id, item in permitted.items()
        )
    )


async def read_claim_release_preimage(
    connection: Any,
    *,
    schema: str,
    plan: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Read only raw claim fields for immediate encrypted rollback capture."""
    expected = _claim_release_expected(plan)
    identifiers = _operation_identifiers(list(expected))
    quoted_schema = _quoted_identifier(schema, "database schema")
    async with connection.transaction(
        isolation="repeatable_read",
        readonly=True,
    ):
        rows = await connection.fetch(
            CLAIM_RELEASE_PREIMAGE_QUERY.format(
                schema=quoted_schema,
                lock_clause="",
            ),
            identifiers,
        )
    preimage = [_mapping(row) for row in rows]
    if len(preimage) != len(expected):
        raise OperationRecoveryError(
            "operation-recovery claim-release preimage is incomplete"
        )
    for row in preimage:
        item = expected.get(row.get("operation_id"))
        worker_id = row.get("worker_id")
        if (
            item is None
            or not isinstance(worker_id, str)
            or not worker_id
            or hashlib.sha256(worker_id.encode("utf-8")).hexdigest()
            != item.get("worker_id_digest")
            or row.get("claimed_at") != item.get("claimed_at")
            or row.get("nonclaim_state_digest")
            != item.get("nonclaim_state_digest")
        ):
            raise OperationRecoveryError(
                "operation-recovery claim-release preimage drifted"
            )
    return sorted(preimage, key=lambda row: row["operation_id"])


def live_row_digest(row: Mapping[str, Any]) -> str:
    """Calculate the row digest used by a live-snapshot operation entry."""
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
    return digest(body)


async def read_selected_preimage(
    connection: Any,
    *,
    schema: str,
    selected_operations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Capture only fields changed by upstream retry semantics.

    The returned error text is rollback material and must be streamed directly
    into the encrypted rollback bundle by the caller; it must never be logged.
    """
    quoted_schema = _quoted_identifier(schema, "database schema")
    identifiers = [uuid.UUID(item["operation_id"]) for item in selected_operations]
    async with connection.transaction(
        isolation="repeatable_read",
        readonly=True,
    ):
        rows = await connection.fetch(
            f"""
            SELECT operation_id::text AS operation_id,
                   status,
                   error_message,
                   completed_at,
                   next_retry_at,
                   worker_id,
                   claimed_at,
                   retry_count,
                   updated_at,
                   encode(
                       sha256(convert_to(task_payload::text, 'UTF8')),
                       'hex'
                   ) AS task_payload_digest
            FROM {quoted_schema}.async_operations
            WHERE operation_id = ANY($1::uuid[])
            ORDER BY operation_id
            """,
            identifiers,
        )
    preimage = [_mapping(row) for row in rows]
    if len(preimage) != len(identifiers):
        raise OperationRecoveryError("selected operation preimage is incomplete")
    expected = {item["operation_id"]: item for item in selected_operations}
    for row in preimage:
        item = expected.get(row["operation_id"])
        if (
            item is None
            or row["status"] != item["expected_status"]
            or row["task_payload_digest"] != item["task_payload_digest"]
        ):
            raise OperationRecoveryError("selected operation preimage drifted")
        for key in ("completed_at", "next_retry_at", "claimed_at", "updated_at"):
            value = row[key]
            if value is not None:
                row[key] = (
                    value.astimezone(timezone.utc)
                    .isoformat(timespec="microseconds")
                    .replace("+00:00", "Z")
                )
    return preimage


async def apply_requeue_transaction(
    connection: Any,
    *,
    profile_id: str,
    schema: str,
    bank_id: str,
    plan: Mapping[str, Any],
    on_mutation_attempt: Callable[[], None] | None = None,
) -> tuple[str, str]:
    """CAS-requeue the exact selected cohort in one serializable transaction."""
    quoted_schema = _quoted_identifier(schema, "database schema")
    quoted_generation = _quoted_identifier(
        GENERATION_TABLE,
        "migration generation table",
    )
    selected = list(plan["selected_operations"])
    identifiers = [uuid.UUID(item["operation_id"]) for item in selected]
    expected = {item["operation_id"]: item for item in selected}
    expires_at = plan.get("expires_at")
    _assert_transaction_deadline(expires_at)
    async with connection.transaction(isolation="serializable"):
        await _configure_transaction_deadline(
            connection,
            expires_at,
            start_transaction_timeout=True,
        )
        generation_value = await connection.fetchval(
            f"""
            SELECT generation
            FROM {quoted_schema}.{quoted_generation}
            WHERE singleton
            FOR UPDATE
            """
        )
        if type(generation_value) is not int:
            raise OperationRecoveryError("migration generation is unavailable")
        generation_before = f"{profile_id}:{schema}:{generation_value}"
        if generation_before != plan["pre_generation"]:
            raise OperationRecoveryError(
                "operation-recovery apply generation drifted"
            )
        verified_generation = await read_generation(
            connection,
            schema,
            profile_id,
        )
        if verified_generation != generation_before:
            raise OperationRecoveryError(
                "operation-recovery generation authority differs"
            )
        competing_connections = await connection.fetchval(
            """
            SELECT count(*)
            FROM pg_catalog.pg_stat_activity
            WHERE datname = current_database()
              AND pid <> pg_backend_pid()
            """
        )
        if competing_connections != 0:
            raise OperationRecoveryError(
                "operation-recovery apply requires exclusive database access"
            )
        claimed = await connection.fetchval(
            f"""
            SELECT EXISTS (
                SELECT 1
                FROM {quoted_schema}.async_operations
                WHERE {QUEUE_BLOCKER_PREDICATE}
            )
            """,
            identifiers,
        )
        if claimed is not False:
            raise OperationRecoveryError(
                "operation-recovery apply requires an unclaimed queue"
            )
        rows = await connection.fetch(
            SAFE_OPERATION_QUERY.format(schema=quoted_schema) + " FOR UPDATE",
            bank_id,
            list(EXPECTED_OPERATION_COUNTS),
            identifiers,
            None,
        )
        safe_rows = [_mapping(row) for row in rows]
        if len(safe_rows) != len(selected):
            raise OperationRecoveryError(
                "operation-recovery selected row set changed"
            )
        for row in safe_rows:
            item = expected.get(row["operation_id"])
            if (
                item is None
                or row["bank_id"] != bank_id
                or row["operation_type"] != item["operation_type"]
                or row["status"] != item["expected_status"]
                or row["task_payload_digest"] != item["task_payload_digest"]
                or live_row_digest(row) != item["row_digest"]
            ):
                raise OperationRecoveryError(
                    "operation-recovery selected row drifted"
                )
        before_by_id = {row["operation_id"]: row for row in safe_rows}
        await _configure_transaction_deadline(connection, expires_at)
        if on_mutation_attempt is not None:
            on_mutation_attempt()
        result = await connection.execute(
            f"""
            UPDATE {quoted_schema}.async_operations
            SET status = 'pending',
                error_message = NULL,
                completed_at = NULL,
                next_retry_at = NULL,
                worker_id = NULL,
                claimed_at = NULL,
                retry_count = 0,
                updated_at = NOW()
            WHERE operation_id = ANY($1::uuid[])
              AND bank_id = $2
              AND status = ANY($3::text[])
            """,
            identifiers,
            bank_id,
            ["failed", "cancelled"],
        )
        if result != f"UPDATE {len(selected)}":
            raise OperationRecoveryError(
                "operation-recovery apply row count differs"
            )
        generation_after_value = await connection.fetchval(
            f"""
            SELECT generation
            FROM {quoted_schema}.{quoted_generation}
            WHERE singleton
            """
        )
        if generation_after_value != generation_value + 1:
            raise OperationRecoveryError(
                "operation-recovery generation did not advance exactly once"
            )
        post_rows = await read_safe_operation_rows(
            connection,
            schema=schema,
            bank_id=bank_id,
            operation_ids=[item["operation_id"] for item in selected],
        )
        post = {row["operation_id"]: row for row in post_rows}
        if len(post) != len(selected):
            raise OperationRecoveryError(
                "operation-recovery post-state is incomplete"
            )
        for item in selected:
            row = post[item["operation_id"]]
            before = before_by_id[item["operation_id"]]
            if (
                row["status"] != "pending"
                or row["completed_at"] is not None
                or row["retry_count"] != 0
                or row["next_retry_at"] is not None
                or row["worker_id_present"]
                or row["worker_id_digest"] is not None
                or row["claimed_at"] is not None
                or row["error_category"] != "none"
                or row["error_digest"] is not None
                or row["task_payload_digest"] != item["task_payload_digest"]
                or row["updated_at"] == before["updated_at"]
            ):
                raise OperationRecoveryError(
                    "operation-recovery post-state differs"
                )
        await _configure_transaction_deadline(connection, expires_at)
        _assert_transaction_deadline(expires_at)
    return (
        generation_before,
        f"{profile_id}:{schema}:{generation_after_value}",
    )


async def apply_claim_release_transaction(
    connection: Any,
    *,
    profile_id: str,
    schema: str,
    plan: Mapping[str, Any],
    on_mutation_attempt: Callable[[], None] | None = None,
) -> tuple[str, str]:
    """Clear only claim metadata from the exact 43 planned failed rows."""
    quoted_schema = _quoted_identifier(schema, "database schema")
    quoted_generation = _quoted_identifier(
        GENERATION_TABLE,
        "migration generation table",
    )
    expected, permitted, reference_cohort_identifiers = (
        _claim_release_expected_sets(plan)
    )
    identifiers = _operation_identifiers(list(expected))
    permitted_identifiers = _operation_identifiers(list(permitted))
    guard_identifiers = identifiers + permitted_identifiers
    expires_at = plan.get("expires_at")
    _assert_transaction_deadline(expires_at)
    async with connection.transaction(isolation="serializable"):
        await _configure_transaction_deadline(
            connection,
            expires_at,
            start_transaction_timeout=True,
        )
        generation_value = await connection.fetchval(
            f"""
            SELECT generation
            FROM {quoted_schema}.{quoted_generation}
            WHERE singleton
            FOR UPDATE
            """
        )
        if type(generation_value) is not int:
            raise OperationRecoveryError("migration generation is unavailable")
        generation_before = f"{profile_id}:{schema}:{generation_value}"
        if generation_before != plan.get("pre_generation"):
            raise OperationRecoveryError(
                "operation-recovery claim-release apply generation drifted"
            )
        verified_generation = await read_generation(
            connection,
            schema,
            profile_id,
        )
        if verified_generation != generation_before:
            raise OperationRecoveryError(
                "operation-recovery claim-release generation authority differs"
            )
        competing_connections = await connection.fetchval(
            """
            SELECT count(*)
            FROM pg_catalog.pg_stat_activity
            WHERE datname = current_database()
              AND pid <> pg_backend_pid()
            """
        )
        if competing_connections != 0:
            raise OperationRecoveryError(
                "operation-recovery claim-release requires exclusive database access"
            )
        outside_blocker = await connection.fetchval(
            f"""
            SELECT EXISTS (
                SELECT 1
                FROM {quoted_schema}.async_operations
                WHERE {QUEUE_BLOCKER_PREDICATE}
            )
            """,
            guard_identifiers,
        )
        if outside_blocker is not False:
            raise OperationRecoveryError(
                "operation-recovery claim-release queue guard differs"
            )
        rows = await _fetch_claim_release_evidence(
            connection,
            schema=schema,
            identifiers=guard_identifiers,
            reference_cohort_identifiers=reference_cohort_identifiers,
            reference_selected_identifiers=permitted_identifiers,
            for_update=True,
        )
        by_id = {row.get("operation_id"): row for row in rows}
        if not _claim_release_groups_match(
            by_id,
            expected,
            permitted,
            selected_matches=_claim_release_before_matches,
        ):
            raise OperationRecoveryError(
                "operation-recovery claim-release selected row drifted"
            )
        await _configure_transaction_deadline(connection, expires_at)
        if on_mutation_attempt is not None:
            on_mutation_attempt()
        result = await connection.execute(
            f"""
            UPDATE {quoted_schema}.async_operations
            SET worker_id = NULL,
                claimed_at = NULL
            WHERE operation_id = ANY($1::uuid[])
              AND status = 'failed'
              AND worker_id IS NOT NULL
              AND claimed_at IS NOT NULL
            """,
            identifiers,
        )
        if result != f"UPDATE {len(expected)}":
            raise OperationRecoveryError(
                "operation-recovery claim-release row count differs"
            )
        generation_after_value = await connection.fetchval(
            f"""
            SELECT generation
            FROM {quoted_schema}.{quoted_generation}
            WHERE singleton
            """
        )
        if generation_after_value != generation_value + 1:
            raise OperationRecoveryError(
                "operation-recovery claim-release generation did not advance exactly once"
            )
        post_rows = await _fetch_claim_release_evidence(
            connection,
            schema=schema,
            identifiers=guard_identifiers,
            reference_cohort_identifiers=reference_cohort_identifiers,
            reference_selected_identifiers=permitted_identifiers,
            for_update=False,
        )
        post = {row.get("operation_id"): row for row in post_rows}
        if not _claim_release_groups_match(
            post,
            expected,
            permitted,
            selected_matches=_claim_release_after_matches,
        ):
            raise OperationRecoveryError(
                "operation-recovery claim-release post-state differs"
            )
        await _configure_transaction_deadline(connection, expires_at)
        _assert_transaction_deadline(expires_at)
    return (
        generation_before,
        f"{profile_id}:{schema}:{generation_after_value}",
    )


def _assert_transaction_deadline(expires_at: Any) -> float:
    observed_at = time.time()
    if type(expires_at) is not int or observed_at >= expires_at:
        raise OperationRecoveryError("operation-recovery requeue plan expired")
    return observed_at


async def _configure_transaction_deadline(
    connection: Any,
    expires_at: int,
    *,
    start_transaction_timeout: bool = False,
) -> None:
    """Bound every potentially blocking statement to the approval lifetime."""
    observed_at = _assert_transaction_deadline(expires_at)
    remaining_ms = int((expires_at - observed_at) * 1000)
    if remaining_ms <= 0:
        raise OperationRecoveryError("operation-recovery requeue plan expired")
    timeout = f"{remaining_ms}ms"
    if start_transaction_timeout:
        await connection.fetchval(
            "SELECT pg_catalog.set_config('transaction_timeout', $1, true)",
            timeout,
        )
    await connection.fetchval(
        "SELECT pg_catalog.set_config('lock_timeout', $1, true)",
        timeout,
    )
    await connection.fetchval(
        "SELECT pg_catalog.set_config('statement_timeout', $1, true)",
        timeout,
    )


async def rollback_requeue_transaction(
    connection: Any,
    *,
    profile_id: str,
    schema: str,
    bank_id: str,
    plan: Mapping[str, Any],
    application: Mapping[str, Any],
    rollback_record: Mapping[str, Any],
    preimage: Sequence[Mapping[str, Any]],
) -> tuple[str, str]:
    """Restore retry-mutated columns iff no selected row was consumed."""
    quoted_schema = _quoted_identifier(schema, "database schema")
    quoted_generation = _quoted_identifier(
        GENERATION_TABLE,
        "migration generation table",
    )
    selected = {
        item["operation_id"]: item for item in plan["selected_operations"]
    }
    preimage_by_id = {item["operation_id"]: dict(item) for item in preimage}
    if set(preimage_by_id) != set(selected):
        raise OperationRecoveryError("rollback preimage row set differs")
    rollback_rows = []
    for operation_id in sorted(selected):
        row = preimage_by_id[operation_id]
        if (
            row.get("status") != selected[operation_id]["expected_status"]
            or row.get("task_payload_digest")
            != selected[operation_id]["task_payload_digest"]
            or row.get("status") not in {"failed", "cancelled"}
        ):
            raise OperationRecoveryError("rollback preimage differs")
        rollback_rows.append(
            {
                "operation_id": operation_id,
                "status": row["status"],
                "error_message": row.get("error_message"),
                "completed_at": row.get("completed_at"),
                "next_retry_at": row.get("next_retry_at"),
                "worker_id": row.get("worker_id"),
                "claimed_at": row.get("claimed_at"),
                "retry_count": row.get("retry_count"),
                "updated_at": row.get("updated_at"),
            }
        )
    async with connection.transaction(isolation="serializable"):
        generation_value = await connection.fetchval(
            f"""
            SELECT generation
            FROM {quoted_schema}.{quoted_generation}
            WHERE singleton
            FOR UPDATE
            """
        )
        generation_before = f"{profile_id}:{schema}:{generation_value}"
        if (
            rollback_record.get("pre_generation")
            != application.get("post_generation")
            or generation_before
            not in {
                rollback_record.get("pre_generation"),
                rollback_record.get("post_generation"),
            }
        ):
            raise OperationRecoveryError(
                "operation-recovery rollback generation drifted"
            )
        rows = await connection.fetch(
            SAFE_OPERATION_QUERY.format(schema=quoted_schema) + " FOR UPDATE",
            bank_id,
            list(EXPECTED_OPERATION_COUNTS),
            [uuid.UUID(value) for value in selected],
            None,
        )
        safe_rows = [_mapping(row) for row in rows]
        if len(safe_rows) != len(selected):
            raise OperationRecoveryError(
                "operation-recovery rollback row set differs"
            )
        if generation_before == rollback_record.get("post_generation"):
            for row in safe_rows:
                item = selected[row["operation_id"]]
                restored = preimage_by_id[row["operation_id"]]
                error_message = restored.get("error_message")
                error_digest = (
                    None
                    if error_message in {None, ""}
                    else hashlib.sha256(
                        str(error_message).encode("utf-8")
                    ).hexdigest()
                )
                worker_id = restored.get("worker_id")
                worker_id_digest = (
                    None
                    if worker_id is None
                    else hashlib.sha256(
                        str(worker_id).encode("utf-8")
                    ).hexdigest()
                )
                if (
                    row["bank_id"] != bank_id
                    or row["operation_type"] != item["operation_type"]
                    or row["status"] != restored["status"]
                    or row["completed_at"] != restored.get("completed_at")
                    or row["next_retry_at"] != restored.get("next_retry_at")
                    or row["worker_id_present"]
                    != (restored.get("worker_id") is not None)
                    or row["worker_id_digest"] != worker_id_digest
                    or row["claimed_at"] != restored.get("claimed_at")
                    or row["retry_count"] != restored.get("retry_count")
                    or row["updated_at"] != restored.get("updated_at")
                    or row["task_payload_digest"]
                    != item["task_payload_digest"]
                    or row["error_digest"] != error_digest
                ):
                    raise OperationRecoveryError(
                        "operation-recovery rollback post-state differs"
                    )
            return (
                rollback_record["pre_generation"],
                rollback_record["post_generation"],
            )
        for row in safe_rows:
            item = selected[row["operation_id"]]
            if (
                row["status"] != "pending"
                or row["retry_count"] != 0
                or row["worker_id_present"]
                or row["worker_id_digest"] is not None
                or row["claimed_at"] is not None
                or row["completed_at"] is not None
                or row["next_retry_at"] is not None
                or row["error_category"] != "none"
                or row["task_payload_digest"] != item["task_payload_digest"]
            ):
                raise OperationRecoveryError(
                    "operation-recovery rollback is no longer safe"
                )
        result = await connection.execute(
            f"""
            UPDATE {quoted_schema}.async_operations AS target
            SET status = source.status,
                error_message = source.error_message,
                completed_at = source.completed_at,
                next_retry_at = source.next_retry_at,
                worker_id = source.worker_id,
                claimed_at = source.claimed_at,
                retry_count = source.retry_count,
                updated_at = source.updated_at
            FROM jsonb_to_recordset($1::jsonb) AS source(
                operation_id uuid,
                status text,
                error_message text,
                completed_at timestamptz,
                next_retry_at timestamptz,
                worker_id text,
                claimed_at timestamptz,
                retry_count integer,
                updated_at timestamptz
            )
            WHERE target.operation_id = source.operation_id
              AND target.bank_id = $2
              AND target.status = 'pending'
            """,
            json.dumps(rollback_rows),
            bank_id,
        )
        if result != f"UPDATE {len(selected)}":
            raise OperationRecoveryError(
                "operation-recovery rollback row count differs"
            )
        generation_after_value = await connection.fetchval(
            f"""
            SELECT generation
            FROM {quoted_schema}.{quoted_generation}
            WHERE singleton
            """
        )
        if generation_after_value != generation_value + 1:
            raise OperationRecoveryError(
                "operation-recovery rollback generation did not advance once"
            )
        generation_after = f"{profile_id}:{schema}:{generation_after_value}"
        if generation_after != rollback_record.get("post_generation"):
            raise OperationRecoveryError(
                "operation-recovery rollback generation differs"
            )
    return (
        generation_before,
        generation_after,
    )


async def rollback_claim_release_transaction(
    connection: Any,
    *,
    profile_id: str,
    schema: str,
    plan: Mapping[str, Any],
    application: Mapping[str, Any],
    rollback_record: Mapping[str, Any],
    preimage: Sequence[Mapping[str, Any]],
) -> tuple[str, str]:
    """Restore only the claim fields cleared by claim-release apply."""
    quoted_schema = _quoted_identifier(schema, "database schema")
    quoted_generation = _quoted_identifier(
        GENERATION_TABLE,
        "migration generation table",
    )
    expected, permitted, reference_cohort_identifiers = (
        _claim_release_expected_sets(plan)
    )
    identifiers = _operation_identifiers(list(expected))
    permitted_identifiers = _operation_identifiers(list(permitted))
    guard_identifiers = identifiers + permitted_identifiers
    preimage_by_id: dict[str, dict[str, Any]] = {}
    for raw in preimage:
        if not isinstance(raw, Mapping):
            raise OperationRecoveryError(
                "operation-recovery claim-release rollback preimage is invalid"
            )
        row = dict(raw)
        operation_id = row.get("operation_id")
        item = expected.get(operation_id)
        worker_id = row.get("worker_id")
        if (
            set(row)
            != {
                "operation_id",
                "worker_id",
                "claimed_at",
                "nonclaim_state_digest",
            }
            or item is None
            or operation_id in preimage_by_id
            or not isinstance(worker_id, str)
            or not worker_id
            or hashlib.sha256(worker_id.encode("utf-8")).hexdigest()
            != item.get("worker_id_digest")
            or row.get("claimed_at") != item.get("claimed_at")
            or row.get("nonclaim_state_digest")
            != item.get("nonclaim_state_digest")
        ):
            raise OperationRecoveryError(
                "operation-recovery claim-release rollback preimage differs"
            )
        preimage_by_id[operation_id] = row
    if set(preimage_by_id) != set(expected):
        raise OperationRecoveryError(
            "operation-recovery claim-release rollback preimage differs"
        )
    async with connection.transaction(isolation="serializable"):
        generation_value = await connection.fetchval(
            f"""
            SELECT generation
            FROM {quoted_schema}.{quoted_generation}
            WHERE singleton
            FOR UPDATE
            """
        )
        if type(generation_value) is not int:
            raise OperationRecoveryError("migration generation is unavailable")
        generation_before = f"{profile_id}:{schema}:{generation_value}"
        if generation_before not in {
            application.get("post_generation"),
            rollback_record.get("post_generation"),
        } or rollback_record.get("pre_generation") != application.get(
            "post_generation"
        ):
            raise OperationRecoveryError(
                "operation-recovery claim-release rollback generation drifted"
            )
        verified_generation = await read_generation(
            connection,
            schema,
            profile_id,
        )
        if verified_generation != generation_before:
            raise OperationRecoveryError(
                "operation-recovery claim-release rollback authority differs"
            )
        competing_connections = await connection.fetchval(
            """
            SELECT count(*)
            FROM pg_catalog.pg_stat_activity
            WHERE datname = current_database()
              AND pid <> pg_backend_pid()
            """
        )
        if competing_connections != 0:
            raise OperationRecoveryError(
                "operation-recovery claim-release rollback requires exclusive database access"
            )
        outside_blocker = await connection.fetchval(
            f"""
            SELECT EXISTS (
                SELECT 1
                FROM {quoted_schema}.async_operations
                WHERE {QUEUE_BLOCKER_PREDICATE}
            )
            """,
            guard_identifiers,
        )
        if outside_blocker is not False:
            raise OperationRecoveryError(
                "operation-recovery claim-release rollback queue guard differs"
            )
        rows = await _fetch_claim_release_evidence(
            connection,
            schema=schema,
            identifiers=guard_identifiers,
            reference_cohort_identifiers=reference_cohort_identifiers,
            reference_selected_identifiers=permitted_identifiers,
            for_update=True,
        )
        by_id = {row.get("operation_id"): row for row in rows}
        if generation_before == rollback_record.get("post_generation"):
            if not _claim_release_groups_match(
                by_id,
                expected,
                permitted,
                selected_matches=_claim_release_before_matches,
            ):
                raise OperationRecoveryError(
                    "operation-recovery claim-release rollback post-state differs"
                )
            return (
                rollback_record["pre_generation"],
                rollback_record["post_generation"],
            )
        if not _claim_release_groups_match(
            by_id,
            expected,
            permitted,
            selected_matches=_claim_release_after_matches,
        ):
            raise OperationRecoveryError(
                "operation-recovery claim-release rollback state differs"
            )
        restore_rows = [preimage_by_id[key] for key in sorted(preimage_by_id)]
        result = await connection.execute(
            f"""
            WITH restored AS (
                SELECT operation_id::uuid AS operation_id,
                       worker_id,
                       claimed_at::timestamptz AS claimed_at
                FROM jsonb_to_recordset($1::jsonb) AS value(
                    operation_id text,
                    worker_id text,
                    claimed_at text
                )
            )
            UPDATE {quoted_schema}.async_operations AS operations
            SET worker_id = restored.worker_id,
                claimed_at = restored.claimed_at
            FROM restored
            WHERE operations.operation_id = restored.operation_id
              AND operations.status = 'failed'
              AND operations.worker_id IS NULL
              AND operations.claimed_at IS NULL
            """,
            json.dumps(restore_rows, sort_keys=True),
        )
        if result != f"UPDATE {len(expected)}":
            raise OperationRecoveryError(
                "operation-recovery claim-release rollback row count differs"
            )
        generation_after_value = await connection.fetchval(
            f"""
            SELECT generation
            FROM {quoted_schema}.{quoted_generation}
            WHERE singleton
            """
        )
        if generation_after_value != generation_value + 1:
            raise OperationRecoveryError(
                "operation-recovery claim-release rollback generation did not advance exactly once"
            )
        post_rows = await _fetch_claim_release_evidence(
            connection,
            schema=schema,
            identifiers=guard_identifiers,
            reference_cohort_identifiers=reference_cohort_identifiers,
            reference_selected_identifiers=permitted_identifiers,
            for_update=False,
        )
        post = {row.get("operation_id"): row for row in post_rows}
        if not _claim_release_groups_match(
            post,
            expected,
            permitted,
            selected_matches=_claim_release_before_matches,
        ):
            raise OperationRecoveryError(
                "operation-recovery claim-release rollback verification differs"
            )
        post_generation = f"{profile_id}:{schema}:{generation_after_value}"
        if rollback_record.get("post_generation") != post_generation:
            raise OperationRecoveryError(
                "operation-recovery claim-release rollback receipt generation differs"
            )
    return generation_before, post_generation


async def read_snapshot(
    connection: Any,
    *,
    profile_id: str,
    schema: str,
    bank_id: str,
    operation_ids: Sequence[str] | None,
    statuses: Sequence[str] | None = None,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Read rows between equal generations in one read-only snapshot."""
    async with connection.transaction(
        isolation="repeatable_read",
        readonly=True,
    ):
        generation_before = await read_generation(
            connection,
            schema,
            profile_id,
        )
        rows = await read_safe_operation_rows(
            connection,
            schema=schema,
            bank_id=bank_id,
            operation_ids=operation_ids,
            statuses=statuses,
        )
        generation_after = await read_generation(
            connection,
            schema,
            profile_id,
        )
    if generation_before != generation_after:
        raise OperationRecoveryError(
            "migration generation changed during operation snapshot"
        )
    return generation_before, generation_after, rows


async def read_restore_identity(
    connection: Any,
    *,
    schema: str,
) -> dict[str, Any]:
    """Capture payload-free restored-database identity and table counts."""
    quoted_schema = _quoted_identifier(schema, "database schema")
    banks = await connection.fetch(
        f"SELECT bank_id FROM {quoted_schema}.banks ORDER BY bank_id"
    )
    tables = await connection.fetch(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = $1 AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """,
        schema,
    )
    counts: dict[str, int] = {}
    for name in (
        "async_operations",
        "banks",
        "documents",
        "memory_units",
    ):
        value = await connection.fetchval(
            f"SELECT count(*)::bigint FROM {quoted_schema}.{_quoted_identifier(name, 'table')}"
        )
        if type(value) is not int or value < 0:
            raise OperationRecoveryError("restored table count is invalid")
        counts[name] = value
    return {
        "schema": schema,
        "bank_ids": [_mapping(row)["bank_id"] for row in banks],
        "tables": [_mapping(row)["table_name"] for row in tables],
        "table_counts": counts,
    }


def utc_now() -> int:
    return int(datetime.now(timezone.utc).timestamp())
