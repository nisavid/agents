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
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import sys
import time
import uuid
from typing import Any

from .operation_recovery import (
    EXACT_DRAIN_WORKER_MAX_RETRIES,
    EXPECTED_CLAIM_RELEASE_ROW_COUNT,
    EXPECTED_OPERATION_COUNTS,
    OperationRecoveryError,
    verify_exact_drain_plan,
    verify_exact_drain_status,
)
from .canonical import StrictJsonError, digest, strict_json_loads
from .provider_runtime import ProviderRuntimePolicy


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
EXACT_DRAIN_PROFILE_ENVIRONMENT_KEYS = frozenset(
    {
        "HINDSIGHT_API_AUDIT_LOG_ENABLED",
        "HINDSIGHT_API_EMBEDDINGS_PROVIDER",
        "HINDSIGHT_API_FAIL_ON_EXTRACTION_ERRORS",
        "HINDSIGHT_API_LLM_API_KEY",
        "HINDSIGHT_API_LLM_MODEL",
        "HINDSIGHT_API_LLM_PROVIDER",
        "HINDSIGHT_API_LLM_REASONING_EFFORT",
        "HINDSIGHT_API_LLM_STRATEGY",
        "HINDSIGHT_API_LLM_TRACE_ENABLED",
        "HINDSIGHT_API_LLM_TRACE_MAX_CHARS",
        "HINDSIGHT_API_LLM_TRACE_SCOPES",
        "HINDSIGHT_API_RECALL_BUDGET_FUNCTION",
        "HINDSIGHT_API_RERANKER_PROVIDER",
        "HINDSIGHT_API_SKIP_LLM_VERIFICATION",
        *(
            f"HINDSIGHT_API_LLM_{position}_{suffix}"
            for position in range(1, 5)
            for suffix in (
                "API_KEY",
                "BASE_URL",
                "MODEL",
                "PROVIDER",
                "REASONING_EFFORT",
            )
        ),
    }
)
EXACT_DRAIN_PROVIDER_ORDER = (
    "work-codex",
    "personal-codex",
    "alt1-codex",
    "alt2-codex",
    "hatchery",
)
EXACT_DRAIN_OAUTH_LOCATORS = {
    "work-codex": "oauth-home:work",
    "personal-codex": "oauth-home:personal",
    "alt1-codex": "oauth-home:alt1",
    "alt2-codex": "oauth-home:alt2",
}
EXACT_DRAIN_MAX_PACKAGE_ENTRIES = 2048
EXACT_DRAIN_MAX_PACKAGE_FILE_BYTES = 16 * 1024 * 1024
EXACT_DRAIN_MAX_PACKAGE_TOTAL_BYTES = 128 * 1024 * 1024


def exact_drain_platform_environment() -> dict[str, str]:
    """Return OS-owned variables injected across the worker exec boundary."""
    return {
        "__CF_USER_TEXT_ENCODING": f"0x{os.geteuid():X}:0x0:0x0",
    }


def validate_exact_drain_provider_policy(
    policy: ProviderRuntimePolicy,
) -> None:
    """Require the exact four-Codex then Hatchery provider authority."""
    members = {member.id: member for member in policy.members}
    if (
        policy.failover_order != EXACT_DRAIN_PROVIDER_ORDER
        or set(members) != set(EXACT_DRAIN_PROVIDER_ORDER)
        or any(
            members[member_id].identity.provider != "openai-codex"
            or members[member_id].identity.base_url != ""
            or members[member_id].identity.credential_marker
            != f"provider-policy:{member_id}"
            or members[member_id].credential_mode != "oauth-home"
            or members[member_id].credential_locator != locator
            for member_id, locator in EXACT_DRAIN_OAUTH_LOCATORS.items()
        )
        or members["hatchery"].identity.provider != "lmstudio"
        or members["hatchery"].identity.base_url
        != "http://hatchery.komodo-vector.ts.net:13305/v1"
        or members["hatchery"].identity.credential_marker is not None
        or members["hatchery"].credential_mode != "none"
        or members["hatchery"].credential_locator is not None
    ):
        raise OperationRecoveryError(
            "operation-recovery exact drain provider policy differs"
        )


def exact_drain_effective_profile_digest(
    policy: ProviderRuntimePolicy,
    environment: Mapping[str, str],
) -> str:
    """Validate and bind the effective five-member Hindsight LLM profile."""
    validate_exact_drain_provider_policy(policy)
    try:
        strategy = strict_json_loads(
            environment.get("HINDSIGHT_API_LLM_STRATEGY", "").encode(
                "utf-8"
            )
        )
    except (StrictJsonError, UnicodeError, ValueError) as error:
        raise OperationRecoveryError(
            "operation-recovery exact drain LLM profile differs"
        ) from error
    if strategy != {"mode": "round-robin"}:
        raise OperationRecoveryError(
            "operation-recovery exact drain LLM profile differs"
        )
    if (
        environment.get("HINDSIGHT_API_EMBEDDINGS_PROVIDER")
        != "openai-codex"
        or environment.get("HINDSIGHT_API_RERANKER_PROVIDER")
        != "jina-mlx"
        or environment.get("HINDSIGHT_API_FAIL_ON_EXTRACTION_ERRORS")
        not in {None, "false"}
    ):
        raise OperationRecoveryError(
            "operation-recovery exact drain LLM profile differs"
        )
    projection: list[dict[str, Any]] = []
    for position, member_id in enumerate(EXACT_DRAIN_PROVIDER_ORDER):
        member = policy.member(member_id)
        prefix = (
            "HINDSIGHT_API_LLM"
            if position == 0
            else f"HINDSIGHT_API_LLM_{position}"
        )
        provider = environment.get(f"{prefix}_PROVIDER")
        model = environment.get(f"{prefix}_MODEL")
        base_url = environment.get(f"{prefix}_BASE_URL", "")
        api_key = environment.get(f"{prefix}_API_KEY")
        reasoning_effort = environment.get(f"{prefix}_REASONING_EFFORT")
        expected_marker = member.identity.credential_marker
        if (
            provider != member.identity.provider
            or model != member.identity.model
            or base_url.rstrip("/")
            != member.identity.base_url.rstrip("/")
            or (
                expected_marker is not None
                and api_key != expected_marker
            )
            or (
                expected_marker is None
                and api_key not in {None, ""}
            )
            or reasoning_effort
            not in {None, "low", "medium", "high", "xhigh"}
        ):
            raise OperationRecoveryError(
                "operation-recovery exact drain LLM profile differs"
            )
        projection.append(
            {
                "member_id": member_id,
                "provider": provider,
                "model": model,
                "base_url": base_url.rstrip("/"),
                "credential_marker": expected_marker,
                "reasoning_effort": reasoning_effort,
            }
        )
    return digest(
        {
            "strategy": {"mode": "round-robin"},
            "members": projection,
            "embeddings_provider": "openai-codex",
            "reranker_provider": "jina-mlx",
            "profile_environment": {
                key: environment[key]
                for key in sorted(EXACT_DRAIN_PROFILE_ENVIRONMENT_KEYS)
                if key in environment
            },
        }
    )
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
    lock_clause: str = "",
) -> list[dict[str, Any]]:
    """Return safe metadata; payload and error text never leave PostgreSQL."""
    if bank_id != "engineering":
        raise OperationRecoveryError("operation-recovery bank is invalid")
    if lock_clause not in {"", "FOR SHARE", "FOR UPDATE"}:
        raise OperationRecoveryError(
            "operation-recovery row lock clause is invalid"
        )
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
        f"{SAFE_OPERATION_QUERY.format(schema=quoted_schema)} {lock_clause}",
        bank_id,
        list(EXPECTED_OPERATION_COUNTS),
        identifiers,
        None if statuses is None else list(statuses),
    )
    return [_mapping(row) for row in rows]


def exact_drain_worker_id(plan_digest: str) -> str:
    """Derive the private worker identity from an approved plan digest."""
    if not isinstance(plan_digest, str) or not re.fullmatch(
        r"[0-9a-f]{64}", plan_digest
    ):
        raise OperationRecoveryError("exact drain plan digest is invalid")
    return f"operation-recovery-exact-drain-{plan_digest[:12]}"


def install_exact_drain_runtime_guards(
    postgresql_ops_type: type[Any],
    worker_poller_type: type[Any],
    memory_engine_type: type[Any],
    adapter: Any,
) -> None:
    """Restrict upstream worker lifecycle seams to the exact drain cohort."""

    async def claim_tasks(_ops: Any, *args: Any, **kwargs: Any) -> Any:
        return await adapter.claim_tasks(*args, **kwargs)

    async def scan_active_schemas(
        _poller: Any,
        schemas: Sequence[str | None],
    ) -> set[str | None]:
        if None in schemas:
            return {None}
        if "public" in schemas:
            return {"public"}
        raise OperationRecoveryError(
            "operation-recovery exact drain public schema is unavailable"
        )

    async def recover_exact_tasks(poller: Any) -> int:
        return await adapter.recover_own_tasks(poller._backend)

    async def schedule_exact_retry(
        poller: Any,
        operation_id: str,
        retry_at: Any,
        error_message: str,
        schema: str | None,
    ) -> None:
        await adapter.schedule_retry(
            poller._backend,
            operation_id,
            retry_at,
            error_message,
            schema,
        )

    async def defer_exact_operation(
        poller: Any,
        operation_id: str,
        exec_date: Any,
        reason: str,
        schema: str | None,
    ) -> None:
        await adapter.defer_operation(
            poller._backend,
            operation_id,
            exec_date,
            reason,
            schema,
        )

    async def mark_exact_completed(
        poller: Any,
        operation_id: str,
        schema: str | None,
    ) -> None:
        await adapter.mark_completed(
            poller._backend,
            operation_id,
            schema,
        )

    async def mark_exact_failed(
        poller: Any,
        operation_id: str,
        error_message: str,
        schema: str | None,
    ) -> None:
        await adapter.mark_failed(
            poller._backend,
            operation_id,
            error_message,
            schema,
        )

    async def engine_mark_exact_completed(
        engine: Any,
        operation_id: str,
    ) -> None:
        await adapter.mark_completed(
            await engine._get_backend(),
            operation_id,
            None,
        )

    async def engine_mark_exact_failed(
        engine: Any,
        operation_id: str,
        error_message: str,
        error_traceback: str,
    ) -> None:
        await adapter.mark_failed(
            await engine._get_backend(),
            operation_id,
            f"{error_message}\n\nTraceback:\n{error_traceback}",
            None,
        )

    async def engine_mark_exact_consolidation_completed(
        engine: Any,
        operation_id: str,
        bank_id: str,
        status: str,
        result: Mapping[str, Any] | None,
        schema: str | None = None,
        error_message: str | None = None,
    ) -> None:
        del result
        if bank_id != "engineering":
            raise OperationRecoveryError(
                "operation-recovery exact drain consolidation bank differs"
            )
        backend = await engine._get_backend()
        if status == "completed" and error_message in {None, ""}:
            await adapter.mark_completed(backend, operation_id, schema)
            return
        if status == "failed" and isinstance(error_message, str):
            await adapter.mark_failed(
                backend,
                operation_id,
                error_message,
                schema,
            )
            return
        raise OperationRecoveryError(
            "operation-recovery exact drain consolidation status differs"
        )

    async def suppress_parent_propagation(
        _owner: Any,
        *_args: Any,
        **_kwargs: Any,
    ) -> None:
        return None

    postgresql_ops_type.claim_tasks = claim_tasks
    worker_poller_type._scan_active_schemas = scan_active_schemas
    worker_poller_type.recover_own_tasks = recover_exact_tasks
    worker_poller_type._schedule_retry = schedule_exact_retry
    worker_poller_type._defer_operation = defer_exact_operation
    worker_poller_type._mark_completed = mark_exact_completed
    worker_poller_type._mark_failed = mark_exact_failed
    worker_poller_type._maybe_update_parent_operation = (
        suppress_parent_propagation
    )
    memory_engine_type._maybe_update_parent_operation = (
        suppress_parent_propagation
    )
    memory_engine_type._mark_operation_completed = (
        engine_mark_exact_completed
    )
    memory_engine_type._mark_operation_failed = engine_mark_exact_failed
    memory_engine_type._mark_operation_completed_and_fire_webhook = (
        engine_mark_exact_consolidation_completed
    )


def _exact_drain_file_bytes(
    path: Path,
    label: str,
    *,
    max_bytes: int | None = None,
) -> bytes:
    descriptor = -1
    try:
        resolved = path.resolve(strict=True)
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        observed = path.lstat()
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise OperationRecoveryError(f"{label} is unavailable") from error
    if (
        path != resolved
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_mode & 0o022
        or before.st_nlink != 1
        or (before.st_dev, before.st_ino)
        != (observed.st_dev, observed.st_ino)
    ):
        os.close(descriptor)
        raise OperationRecoveryError(f"{label} is untrusted")
    if max_bytes is not None and before.st_size > max_bytes:
        os.close(descriptor)
        descriptor = -1
        raise OperationRecoveryError(f"{label} is too large")
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            read_size = 1024 * 1024
            if max_bytes is not None:
                read_size = min(read_size, max_bytes + 1 - total)
            chunk = os.read(descriptor, read_size)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise OperationRecoveryError(f"{label} is too large")
        after = os.fstat(descriptor)
        current = path.lstat()
    except OSError as error:
        raise OperationRecoveryError(f"{label} is unreadable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise OperationRecoveryError(f"{label} changed while reading")
    return b"".join(chunks)


def _exact_drain_file_digest_evidence(
    path: Path,
    label: str,
    *,
    max_bytes: int | None = None,
) -> tuple[str, int]:
    descriptor = -1
    try:
        resolved = path.resolve(strict=True)
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        observed = path.lstat()
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise OperationRecoveryError(f"{label} is unavailable") from error
    if (
        path != resolved
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_mode & 0o022
        or before.st_nlink != 1
        or (before.st_dev, before.st_ino)
        != (observed.st_dev, observed.st_ino)
    ):
        os.close(descriptor)
        raise OperationRecoveryError(f"{label} is untrusted")
    if max_bytes is not None and before.st_size > max_bytes:
        os.close(descriptor)
        descriptor = -1
        raise OperationRecoveryError(f"{label} is too large")
    hasher = hashlib.sha256()
    total = 0
    try:
        while True:
            read_size = 1024 * 1024
            if max_bytes is not None:
                read_size = min(read_size, max_bytes + 1 - total)
            chunk = os.read(descriptor, read_size)
            if not chunk:
                break
            hasher.update(chunk)
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise OperationRecoveryError(f"{label} is too large")
        after = os.fstat(descriptor)
        current = path.lstat()
    except OSError as error:
        raise OperationRecoveryError(f"{label} is unreadable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise OperationRecoveryError(f"{label} changed while reading")
    return hasher.hexdigest(), after.st_size


def _exact_drain_file_digest(
    path: Path,
    label: str,
    *,
    max_bytes: int | None = None,
) -> str:
    file_digest, _file_size = _exact_drain_file_digest_evidence(
        path,
        label,
        max_bytes=max_bytes,
    )
    return file_digest


def _exact_drain_trusted_directory(path: Path, label: str) -> None:
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except OSError as error:
        raise OperationRecoveryError(f"{label} is unavailable") from error
    if (
        path != resolved
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o022
    ):
        raise OperationRecoveryError(f"{label} is untrusted")


def _exact_drain_package_entries(package_root: Path) -> list[Path]:
    pending = [package_root]
    entries: list[Path] = []
    while pending:
        directory = pending.pop()
        _exact_drain_trusted_directory(
            directory,
            "exact drain upstream worker package directory",
        )
        try:
            with os.scandir(directory) as scanner:
                for entry in scanner:
                    if len(entries) >= EXACT_DRAIN_MAX_PACKAGE_ENTRIES:
                        raise OperationRecoveryError(
                            "exact drain upstream worker runtime has too many entries"
                        )
                    path = Path(entry.path)
                    entries.append(path)
                    if not entry.is_symlink() and entry.is_dir(
                        follow_symlinks=False
                    ):
                        pending.append(path)
        except OSError as error:
            raise OperationRecoveryError(
                "exact drain upstream worker runtime is unavailable"
            ) from error
    return sorted(entries)


def exact_drain_worker_interpreter_path(
    worker_runtime: str | Path,
) -> Path:
    """Return the bound venv launch path without dereferencing it."""
    worker_path = Path(worker_runtime)
    if not worker_path.is_absolute():
        raise OperationRecoveryError(
            "exact drain worker runtime path must be absolute"
        )
    try:
        first_line = (
            _exact_drain_file_bytes(
                worker_path,
                "exact drain worker entrypoint",
                max_bytes=1024 * 1024,
            )
            .splitlines()[0]
            .decode("utf-8")
            .strip()
        )
    except (IndexError, UnicodeDecodeError) as error:
        raise OperationRecoveryError(
            "exact drain worker interpreter is unavailable"
        ) from error
    interpreter = Path(first_line.removeprefix("#!"))
    if (
        not first_line.startswith("#!/")
        or interpreter.parent != worker_path.parent
        or interpreter.name not in {"python", "python3"}
        or not interpreter.is_file()
        or not (worker_path.parent.parent / "pyvenv.cfg").is_file()
    ):
        raise OperationRecoveryError(
            "exact drain worker interpreter is invalid"
        )
    return interpreter


def exact_drain_worker_site_packages_path(
    worker_runtime: str | Path,
) -> Path:
    """Return the trusted dependency directory without processing startup hooks."""
    worker_path = Path(worker_runtime)
    exact_drain_worker_interpreter_path(worker_path)
    library = worker_path.parent.parent / "lib"
    _exact_drain_trusted_directory(
        library,
        "exact drain worker library",
    )
    try:
        with os.scandir(library) as scanner:
            python_libraries = [
                Path(entry.path)
                for entry in scanner
                if (
                    not entry.is_symlink()
                    and entry.is_dir(follow_symlinks=False)
                    and re.fullmatch(r"python[0-9]+\.[0-9]+", entry.name)
                )
            ]
    except OSError as error:
        raise OperationRecoveryError(
            "exact drain worker dependency directory is unavailable"
        ) from error
    if len(python_libraries) != 1:
        raise OperationRecoveryError(
            "exact drain worker dependency directory is unavailable"
        )
    _exact_drain_trusted_directory(
        python_libraries[0],
        "exact drain worker Python library",
    )
    site_packages = python_libraries[0] / "site-packages"
    _exact_drain_trusted_directory(
        site_packages,
        "exact drain worker dependency directory",
    )
    return site_packages


def install_exact_drain_candidate_imports(
    worker_runtime: str | Path,
    candidate_library: str | Path,
) -> Path:
    """Install candidate-first imports without executing external site hooks."""
    if any(
        name == "hindsight_api" or name.startswith("hindsight_api.")
        for name in sys.modules
    ):
        raise OperationRecoveryError(
            "exact drain Hindsight module was preloaded"
        )
    candidate_root = Path(candidate_library)
    if not candidate_root.is_absolute():
        raise OperationRecoveryError(
            "exact drain candidate library is unavailable"
        )
    _exact_drain_trusted_directory(
        candidate_root,
        "exact drain candidate library",
    )
    candidate_package = candidate_root / "hindsight_api"
    _exact_drain_trusted_directory(
        candidate_package,
        "exact drain candidate Hindsight package",
    )
    dependency_root = exact_drain_worker_site_packages_path(worker_runtime)
    if dependency_root == candidate_root:
        raise OperationRecoveryError(
            "exact drain worker dependency directory is invalid"
        )
    candidate_text = str(candidate_root)
    dependency_text = str(dependency_root)
    sys.path[:] = [
        candidate_text,
        *(
            entry
            for entry in sys.path
            if entry not in {candidate_text, dependency_text}
        ),
        dependency_text,
    ]
    spec = importlib.util.find_spec("hindsight_api")
    try:
        origin = Path(spec.origin).resolve(strict=True)
        search_locations = tuple(
            Path(location).resolve(strict=True)
            for location in spec.submodule_search_locations
        )
        origin.relative_to(candidate_package)
    except (AttributeError, OSError, TypeError, ValueError) as error:
        raise OperationRecoveryError(
            "exact drain candidate Hindsight import differs"
        ) from error
    if search_locations != (candidate_package,):
        raise OperationRecoveryError(
            "exact drain candidate Hindsight import differs"
        )
    return dependency_root


def _exact_drain_runtime_distribution_metadata(
    worker_runtime: str | Path,
    runtime_package_root: str | Path,
) -> tuple[str, bytes, Path]:
    """Read immutable candidate runtime metadata without executing Python."""
    worker_path = Path(worker_runtime)
    exact_drain_worker_interpreter_path(worker_path)
    package_root = Path(runtime_package_root)
    if not package_root.is_absolute() or package_root.name != "hindsight_api":
        raise OperationRecoveryError(
            "exact drain worker Hindsight package is unavailable"
        )
    site_packages = package_root.parent
    _exact_drain_trusted_directory(
        site_packages,
        "exact drain candidate library",
    )
    try:
        distributions = tuple(site_packages.glob("hindsight_api-*.dist-info"))
    except OSError as error:
        raise OperationRecoveryError(
            "exact drain worker Hindsight version is unavailable"
        ) from error
    if (
        len(distributions) != 1
        or not distributions[0].is_dir()
        or distributions[0].is_symlink()
    ):
        raise OperationRecoveryError(
            "exact drain worker Hindsight version is unavailable"
        )
    _exact_drain_trusted_directory(
        distributions[0],
        "exact drain worker Hindsight metadata directory",
    )
    metadata_body = _exact_drain_file_bytes(
        distributions[0] / "METADATA",
        "exact drain worker Hindsight metadata",
        max_bytes=1024 * 1024,
    )
    name_values = []
    version_values = []
    for line in metadata_body.splitlines():
        if line.startswith(b"Name: "):
            name_values.append(line.removeprefix(b"Name: "))
        elif line.startswith(b"Version: "):
            version_values.append(line.removeprefix(b"Version: "))
    try:
        version = version_values[0].decode("ascii")
    except (IndexError, UnicodeDecodeError) as error:
        raise OperationRecoveryError(
            "exact drain worker Hindsight version is unavailable"
        ) from error
    if (
        name_values != [b"hindsight-api"]
        or len(version_values) != 1
        or re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+!_-]{0,63}", version)
        is None
        or distributions[0].name != f"hindsight_api-{version}.dist-info"
    ):
        raise OperationRecoveryError(
            "exact drain worker Hindsight version is unavailable"
        )
    _exact_drain_trusted_directory(
        package_root,
        "exact drain candidate Hindsight package",
    )
    return version, metadata_body, package_root


def exact_drain_worker_hindsight_version(
    worker_runtime: str | Path,
    runtime_package_root: str | Path,
) -> str:
    """Read the immutable candidate Hindsight package version."""
    version, _metadata_body, _package_root = (
        _exact_drain_runtime_distribution_metadata(
            worker_runtime,
            runtime_package_root,
        )
    )
    return version


def _exact_drain_interpreter_evidence(interpreter: Path) -> dict[str, str]:
    evidence: dict[str, str] = {"launch_path": str(interpreter)}
    current = interpreter
    seen: set[Path] = set()
    for position in range(8):
        if current in seen:
            raise OperationRecoveryError(
                "exact drain worker interpreter link cycle"
            )
        seen.add(current)
        metadata = current.lstat()
        if not stat.S_ISLNK(metadata.st_mode):
            canonical = current.resolve(strict=True)
            canonical_metadata = canonical.lstat()
            if not stat.S_ISREG(canonical_metadata.st_mode):
                raise OperationRecoveryError(
                    "exact drain worker interpreter is invalid"
                )
            if canonical != current:
                evidence["resolved_parent_alias_path"] = str(current)
            evidence["resolved_path"] = str(canonical)
            evidence["resolved_sha256"] = _exact_drain_file_digest(
                canonical,
                "exact drain worker interpreter",
            )
            return evidence
        target = os.readlink(current)
        evidence[f"link_{position}_path"] = str(current)
        evidence[f"link_{position}_target"] = target
        next_path = Path(target)
        current = (
            next_path
            if next_path.is_absolute()
            else current.parent / next_path
        )
    raise OperationRecoveryError(
        "exact drain worker interpreter link chain is too deep"
    )


def exact_drain_runtime_evidence(
    worker_runtime: str | Path,
    provider_runtime_root: str | Path,
    runtime_package_root: str | Path,
) -> tuple[str, bytes]:
    """Bind runtime sources and retain prevalidated provider bootstrap bytes."""
    worker_path = Path(worker_runtime)
    provider_root = Path(provider_runtime_root)
    if not worker_path.is_absolute() or not provider_root.is_absolute():
        raise OperationRecoveryError("exact drain runtime paths must be absolute")
    sources: dict[str, str] = {
        "worker-entrypoint": _exact_drain_file_digest(
            worker_path, "exact drain worker entrypoint"
        )
    }
    interpreter = exact_drain_worker_interpreter_path(worker_path)
    sources["worker-interpreter"] = digest(
        _exact_drain_interpreter_evidence(interpreter)
    )
    sources["worker-venv-config"] = _exact_drain_file_digest(
        worker_path.parent.parent / "pyvenv.cfg",
        "exact drain worker venv configuration",
    )
    sources["worker-dependency-directory"] = digest(
        {"path": str(exact_drain_worker_site_packages_path(worker_path))}
    )
    _version, distribution_metadata, package_root = (
        _exact_drain_runtime_distribution_metadata(
            worker_path,
            runtime_package_root,
        )
    )
    sources["worker-hindsight-distribution-metadata"] = hashlib.sha256(
        distribution_metadata
    ).hexdigest()
    provider_sources = {
        name: _exact_drain_file_bytes(
            provider_root / name,
            f"exact drain provider runtime {name}",
            max_bytes=1024 * 1024,
        )
        for name in ("sitecustomize.py", "hindsight_llm_failover.py")
    }
    for name, source in provider_sources.items():
        sources[f"provider/{name}"] = hashlib.sha256(source).hexdigest()
    package_entries = _exact_drain_package_entries(package_root)
    package_file_count = 0
    package_total_bytes = 0
    for path in package_entries:
        relative = path.relative_to(package_root).as_posix()
        if path.is_symlink():
            raise OperationRecoveryError(
                "exact drain upstream worker runtime is untrusted"
            )
        if path.is_dir():
            _exact_drain_trusted_directory(
                path,
                f"exact drain upstream directory {relative}",
            )
            sources[f"upstream-directory/hindsight_api/{relative}"] = digest(
                {"kind": "directory"}
            )
            continue
        if not path.is_file():
            raise OperationRecoveryError(
                "exact drain upstream worker runtime is untrusted"
            )
        package_file_count += 1
        remaining_bytes = (
            EXACT_DRAIN_MAX_PACKAGE_TOTAL_BYTES - package_total_bytes
        )
        artifact_digest, artifact_size = _exact_drain_file_digest_evidence(
            path,
            f"exact drain upstream artifact {relative}",
            max_bytes=min(
                EXACT_DRAIN_MAX_PACKAGE_FILE_BYTES,
                remaining_bytes,
            ),
        )
        package_total_bytes += artifact_size
        sources[f"upstream/hindsight_api/{relative}"] = artifact_digest
    if package_file_count == 0:
        raise OperationRecoveryError(
            "exact drain upstream worker runtime is unavailable"
        )
    return digest(sources), provider_sources["sitecustomize.py"]


def exact_drain_runtime_digest(
    worker_runtime: str | Path,
    provider_runtime_root: str | Path,
    runtime_package_root: str | Path,
) -> str:
    """Bind the worker entrypoint, claim seam, and provider patch sources."""
    runtime_digest, _provider_bootstrap = exact_drain_runtime_evidence(
        worker_runtime,
        provider_runtime_root,
        runtime_package_root,
    )
    return runtime_digest


class ExactDrainClaimAdapter:
    """Constrain the upstream worker's claim seam to a digest-bound ID set.

    The first claim verifies every selected row against the planning snapshot
    and generation. Later claims permit retries of those same IDs, while still
    checking the immutable operation type and task-payload digest. Rows outside
    the selected set are never selected or updated.
    """

    _ALLOWED_TABLES = frozenset(
        {
            "async_operations",
            "public.async_operations",
            '"public".async_operations',
        }
    )

    def __init__(
        self,
        plan: Mapping[str, Any],
        *,
        completion_callback: Callable[[], None] | None = None,
        resume: bool = False,
    ):
        verified = verify_exact_drain_plan(plan, allow_expired=resume)
        self._plan = verified
        self._selected = {
            item["operation_id"]: item
            for item in verified["selected_operations"]
        }
        snapshot = {
            item["operation_id"]: item
            for item in verified["live_snapshot"]["operations"]
        }
        self._preserved = {
            operation_id: item
            for operation_id, item in snapshot.items()
            if operation_id not in self._selected
        }
        self._identifiers = [uuid.UUID(value) for value in self._selected]
        self._worker_id = exact_drain_worker_id(verified["plan_digest"])
        self._worker_digest = hashlib.sha256(
            self._worker_id.encode("utf-8")
        ).hexdigest()
        self._max_retries = verified["worker_max_retries"]
        self._initial_guard_complete = False
        self._started_ids: set[str] = set()
        self._resume = resume
        self._completion_callback = completion_callback
        self._completion_signalled = False

    async def _verify_initial_state(self, connection: Any) -> None:
        identity = _mapping(
            await connection.fetchrow(
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
        )
        binding = self._plan["rollback_backup"]["source_authority"][
            "binding"
        ]
        try:
            data_directory = Path(identity["data_directory"]).resolve(
                strict=True
            )
        except (KeyError, OSError, TypeError, ValueError):
            data_directory = None
        if (
            identity.get("database") != binding["database"]
            or identity.get("database_user") != binding["user"]
            or data_directory != Path(binding["data_dir"])
            or identity.get("port") != binding["port"]
            or identity.get("address") is not None
            or identity.get("system_identifier")
            != self._plan["installation_authority"][
                "postgres_system_identifier"
            ]
        ):
            raise OperationRecoveryError(
                "operation-recovery exact drain database identity differs"
            )
        generation = await read_generation(connection, "public", "systalyze")
        if not self._resume and generation != self._plan["pre_generation"]:
            raise OperationRecoveryError(
                "operation-recovery exact drain generation drifted"
            )
        rows = await read_safe_operation_rows(
            connection,
            schema="public",
            bank_id="engineering",
            operation_ids=[*self._selected, *self._preserved],
            lock_clause="FOR SHARE",
        )
        if len(rows) != len(self._selected) + len(self._preserved):
            raise OperationRecoveryError(
                "operation-recovery exact drain cohort row set changed"
            )
        for row in rows:
            preserved = self._preserved.get(row["operation_id"])
            if preserved is not None:
                if live_row_digest(row) != preserved["row_digest"]:
                    raise OperationRecoveryError(
                        "operation-recovery exact drain preserved row drifted"
                    )
                continue
            item = self._selected.get(row["operation_id"])
            exact = item is not None and live_row_digest(row) == item["row_digest"]
            if exact:
                continue
            if (
                item is None
                or row["operation_type"] != item["operation_type"]
                or row["task_payload_digest"] != item["task_payload_digest"]
                or not self._resume
                or row["status"]
                not in {"pending", "processing", "completed", "failed", "cancelled"}
                or row["worker_id_digest"] != self._worker_digest
            ):
                raise OperationRecoveryError(
                    "operation-recovery exact drain selected row drifted"
                )
            self._started_ids.add(row["operation_id"])

    async def recover_own_tasks(self, backend: Any) -> int:
        """Recover only exact-plan rows owned by an interrupted capsule."""
        if not self._resume:
            return 0
        async with backend.acquire() as connection:
            async with connection.transaction(isolation="serializable"):
                await self._verify_initial_state(connection)
                rows = await connection.fetch(
                    """
                    SELECT operation_id,
                           operation_type,
                           retry_count,
                           encode(
                               sha256(convert_to(task_payload::text, 'UTF8')),
                               'hex'
                           ) AS task_payload_digest
                    FROM public.async_operations
                    WHERE operation_id = ANY($1::uuid[])
                      AND bank_id = 'engineering'
                      AND status = 'processing'
                      AND worker_id = $2
                      AND task_payload IS NOT NULL
                    ORDER BY operation_id
                    FOR UPDATE
                    """,
                    self._identifiers,
                    self._worker_id,
                )
                for row_value in rows:
                    row = _mapping(row_value)
                    item = self._selected.get(str(row["operation_id"]))
                    if (
                        item is None
                        or row["operation_type"] != item["operation_type"]
                        or row["task_payload_digest"]
                        != item["task_payload_digest"]
                    ):
                        raise OperationRecoveryError(
                            "operation-recovery exact drain recovery row drifted"
                        )
                identifiers = [row["operation_id"] for row in rows]
                if not identifiers:
                    self._initial_guard_complete = True
                    return 0
                retryable = [
                    row["operation_id"]
                    for row in rows
                    if row["retry_count"] < self._max_retries
                ]
                exhausted = [
                    row["operation_id"]
                    for row in rows
                    if row["retry_count"] >= self._max_retries
                ]
                result = await connection.execute(
                    """
                    UPDATE public.async_operations
                    SET status = 'pending',
                        retry_count = retry_count + 1,
                        updated_at = NOW()
                    WHERE operation_id = ANY($1::uuid[])
                      AND bank_id = 'engineering'
                      AND status = 'processing'
                      AND worker_id = $2
                    """,
                    retryable,
                    self._worker_id,
                )
                if result != f"UPDATE {len(retryable)}":
                    raise OperationRecoveryError(
                        "operation-recovery exact drain recovery count differs"
                    )
                result = await connection.execute(
                    """
                    UPDATE public.async_operations
                    SET status = 'failed',
                        next_retry_at = NULL,
                        error_message = $3,
                        completed_at = NOW(),
                        updated_at = NOW()
                    WHERE operation_id = ANY($1::uuid[])
                      AND bank_id = 'engineering'
                      AND status = 'processing'
                      AND worker_id = $2
                    """,
                    exhausted,
                    self._worker_id,
                    "operation-recovery exact drain retry ceiling reached",
                )
                if result != f"UPDATE {len(exhausted)}":
                    raise OperationRecoveryError(
                        "operation-recovery exact drain recovery count differs"
                    )
                self._started_ids.update(str(value) for value in identifiers)
                self._initial_guard_complete = True
                return len(identifiers)

    async def _verify_unstarted_state(self, connection: Any) -> None:
        preserved_rows = await read_safe_operation_rows(
            connection,
            schema="public",
            bank_id="engineering",
            operation_ids=list(self._preserved),
            lock_clause="FOR SHARE",
        )
        preserved_by_id = {
            row["operation_id"]: row for row in preserved_rows
        }
        if set(preserved_by_id) != set(self._preserved) or any(
            live_row_digest(preserved_by_id[operation_id])
            != self._preserved[operation_id]["row_digest"]
            for operation_id in self._preserved
        ):
            raise OperationRecoveryError(
                "operation-recovery exact drain preserved row drifted"
            )
        unstarted = sorted(set(self._selected) - self._started_ids)
        if not unstarted:
            return
        rows = await read_safe_operation_rows(
            connection,
            schema="public",
            bank_id="engineering",
            operation_ids=unstarted,
        )
        rows_by_id = {row["operation_id"]: row for row in rows}
        if set(rows_by_id) != set(unstarted) or any(
            live_row_digest(rows_by_id[operation_id])
            != self._selected[operation_id]["row_digest"]
            for operation_id in unstarted
        ):
            raise OperationRecoveryError(
                "operation-recovery exact drain unstarted row drifted"
            )

    async def _reschedule_owned_task(
        self,
        backend: Any,
        operation_id: str,
        next_retry_at: Any,
        *,
        error_message: str | None,
        schema: str | None,
    ) -> None:
        if schema not in {None, "public"}:
            raise OperationRecoveryError(
                "operation-recovery exact drain public schema is unavailable"
            )
        try:
            identifier = uuid.UUID(operation_id)
        except (AttributeError, TypeError, ValueError) as error:
            raise OperationRecoveryError(
                "operation-recovery exact drain retry ID is invalid"
            ) from error
        item = self._selected.get(str(identifier))
        if item is None:
            raise OperationRecoveryError(
                "operation-recovery exact drain retry row is outside plan"
            )
        async with backend.acquire() as connection:
            async with connection.transaction(isolation="serializable"):
                await self._verify_unstarted_state(connection)
                row_value = await connection.fetchrow(
                    """
                    SELECT operation_id::text AS operation_id,
                           operation_type,
                           status,
                           worker_id,
                           retry_count,
                           encode(
                               sha256(convert_to(task_payload::text, 'UTF8')),
                               'hex'
                           ) AS task_payload_digest
                    FROM public.async_operations
                    WHERE operation_id = $1
                      AND bank_id = 'engineering'
                      AND task_payload IS NOT NULL
                    FOR UPDATE
                    """,
                    identifier,
                )
                row = None if row_value is None else _mapping(row_value)
                if (
                    row is None
                    or row["operation_type"] != item["operation_type"]
                    or row["status"] != "processing"
                    or row["worker_id"] != self._worker_id
                    or row["task_payload_digest"]
                    != item["task_payload_digest"]
                    or type(row["retry_count"]) is not int
                    or row["retry_count"] < 0
                    or row["retry_count"] > self._max_retries
                ):
                    raise OperationRecoveryError(
                        "operation-recovery exact drain retry row drifted"
                    )
                if row["retry_count"] >= self._max_retries:
                    result = await connection.execute(
                        """
                        UPDATE public.async_operations
                        SET status = 'failed',
                            next_retry_at = NULL,
                            error_message = $2,
                            completed_at = NOW(),
                            updated_at = NOW()
                        WHERE operation_id = $1
                          AND bank_id = 'engineering'
                          AND status = 'processing'
                          AND worker_id = $3
                        """,
                        identifier,
                        "operation-recovery exact drain retry ceiling reached",
                        self._worker_id,
                    )
                elif error_message is None:
                    result = await connection.execute(
                        """
                        UPDATE public.async_operations
                        SET status = 'pending',
                            next_retry_at = $2,
                            retry_count = retry_count + 1,
                            updated_at = NOW()
                        WHERE operation_id = $1
                          AND bank_id = 'engineering'
                          AND status = 'processing'
                          AND worker_id = $3
                        """,
                        identifier,
                        next_retry_at,
                        self._worker_id,
                    )
                else:
                    result = await connection.execute(
                        """
                        UPDATE public.async_operations
                        SET status = 'pending',
                            next_retry_at = $2,
                            retry_count = retry_count + 1,
                            error_message = $3,
                            updated_at = NOW()
                        WHERE operation_id = $1
                          AND bank_id = 'engineering'
                          AND status = 'processing'
                          AND worker_id = $4
                        """,
                        identifier,
                        next_retry_at,
                        error_message[:5000],
                        self._worker_id,
                    )
                if result != "UPDATE 1":
                    raise OperationRecoveryError(
                        "operation-recovery exact drain retry count differs"
                    )
                self._started_ids.add(str(identifier))
                self._initial_guard_complete = True

    async def schedule_retry(
        self,
        backend: Any,
        operation_id: str,
        retry_at: Any,
        error_message: str,
        schema: str | None,
    ) -> None:
        """Schedule one exact owned retry without relinquishing authority."""
        if not isinstance(error_message, str):
            raise OperationRecoveryError(
                "operation-recovery exact drain retry error is invalid"
            )
        await self._reschedule_owned_task(
            backend,
            operation_id,
            retry_at,
            error_message=error_message,
            schema=schema,
        )

    async def defer_operation(
        self,
        backend: Any,
        operation_id: str,
        exec_date: Any,
        reason: str,
        schema: str | None,
    ) -> None:
        """Defer one exact owned row while consuming its retry budget."""
        if not isinstance(reason, str):
            raise OperationRecoveryError(
                "operation-recovery exact drain defer reason is invalid"
            )
        await self._reschedule_owned_task(
            backend,
            operation_id,
            exec_date,
            error_message=None,
            schema=schema,
        )

    async def _terminalize_owned_task(
        self,
        backend: Any,
        operation_id: str,
        schema: str | None,
        *,
        error_message: str | None,
    ) -> None:
        if schema not in {None, "public"}:
            raise OperationRecoveryError(
                "operation-recovery exact drain public schema is unavailable"
            )
        try:
            identifier = uuid.UUID(operation_id)
        except (AttributeError, TypeError, ValueError) as error:
            raise OperationRecoveryError(
                "operation-recovery exact drain terminal ID is invalid"
            ) from error
        item = self._selected.get(str(identifier))
        if item is None:
            raise OperationRecoveryError(
                "operation-recovery exact drain terminal row is outside plan"
            )
        async with backend.acquire() as connection:
            async with connection.transaction(isolation="serializable"):
                await self._verify_unstarted_state(connection)
                row_value = await connection.fetchrow(
                    """
                    SELECT operation_type,
                           status,
                           worker_id,
                           encode(
                               sha256(convert_to(task_payload::text, 'UTF8')),
                               'hex'
                           ) AS task_payload_digest
                    FROM public.async_operations
                    WHERE operation_id = $1
                      AND bank_id = 'engineering'
                      AND task_payload IS NOT NULL
                    FOR UPDATE
                    """,
                    identifier,
                )
                row = None if row_value is None else _mapping(row_value)
                if (
                    row is None
                    or row["operation_type"] != item["operation_type"]
                    or row["worker_id"] != self._worker_id
                    or row["task_payload_digest"]
                    != item["task_payload_digest"]
                ):
                    raise OperationRecoveryError(
                        "operation-recovery exact drain terminal row drifted"
                    )
                if row["status"] in {"completed", "failed", "cancelled"}:
                    self._started_ids.add(str(identifier))
                    self._initial_guard_complete = True
                    return
                if row["status"] != "processing":
                    raise OperationRecoveryError(
                        "operation-recovery exact drain terminal row drifted"
                    )
                if error_message is None:
                    result = await connection.execute(
                        """
                        UPDATE public.async_operations
                        SET status = 'completed',
                            completed_at = NOW(),
                            updated_at = NOW()
                        WHERE operation_id = $1
                          AND bank_id = 'engineering'
                          AND status = 'processing'
                          AND worker_id = $2
                        """,
                        identifier,
                        self._worker_id,
                    )
                else:
                    result = await connection.execute(
                        """
                        UPDATE public.async_operations
                        SET status = 'failed',
                            error_message = $2,
                            completed_at = NOW(),
                            updated_at = NOW()
                        WHERE operation_id = $1
                          AND bank_id = 'engineering'
                          AND status = 'processing'
                          AND worker_id = $3
                        """,
                        identifier,
                        error_message[:5000],
                        self._worker_id,
                    )
                if result != "UPDATE 1":
                    raise OperationRecoveryError(
                        "operation-recovery exact drain terminal count differs"
                    )
                self._started_ids.add(str(identifier))
                self._initial_guard_complete = True

    async def mark_completed(
        self,
        backend: Any,
        operation_id: str,
        schema: str | None,
    ) -> None:
        """Complete one exact owned row under the preserved-row guard."""
        await self._terminalize_owned_task(
            backend,
            operation_id,
            schema,
            error_message=None,
        )

    async def mark_failed(
        self,
        backend: Any,
        operation_id: str,
        error_message: str,
        schema: str | None,
    ) -> None:
        """Fail one exact owned row under the preserved-row guard."""
        if not isinstance(error_message, str):
            raise OperationRecoveryError(
                "operation-recovery exact drain terminal error is invalid"
            )
        await self._terminalize_owned_task(
            backend,
            operation_id,
            schema,
            error_message=error_message,
        )

    @staticmethod
    def _claim_capacity(
        reserved_limits: Mapping[str, int], shared_limit: int
    ) -> int:
        values = [shared_limit, *reserved_limits.values()]
        if any(type(value) is not int or value < 0 for value in values):
            raise OperationRecoveryError(
                "operation-recovery exact drain capacity is invalid"
            )
        return sum(values)

    @staticmethod
    def _choose_rows(
        rows: Sequence[Mapping[str, Any]],
        reserved_limits: Mapping[str, int],
        shared_limit: int,
    ) -> list[Mapping[str, Any]]:
        remaining = list(rows)
        chosen: list[Mapping[str, Any]] = []
        for operation_type, limit in reserved_limits.items():
            matching = [
                row
                for row in remaining
                if row["operation_type"] == operation_type
            ][:limit]
            chosen.extend(matching)
            selected_ids = {row["operation_id"] for row in matching}
            remaining = [
                row for row in remaining if row["operation_id"] not in selected_ids
            ]
        chosen.extend(remaining[:shared_limit])
        return chosen

    @staticmethod
    def _canonical_task_payload(row: Mapping[str, Any]) -> str:
        raw = row.get("task_payload")
        try:
            payload = (
                strict_json_loads(raw.encode("utf-8"))
                if isinstance(raw, str)
                else dict(raw)
                if isinstance(raw, Mapping)
                else None
            )
        except (StrictJsonError, TypeError, UnicodeError, ValueError) as error:
            raise OperationRecoveryError(
                "operation-recovery exact drain task payload is invalid"
            ) from error
        operation_id = str(row["operation_id"])
        expected_type = {
            "retain": "batch_retain",
            "refresh_mental_model": "refresh_mental_model",
            "consolidation": "consolidation",
        }.get(row["operation_type"])
        if (
            not isinstance(payload, dict)
            or payload.get("operation_id") != operation_id
            or payload.get("bank_id") != "engineering"
            or expected_type is None
            or payload.get("type") != expected_type
            or payload.get("_schema") not in {None, "public"}
            or payload.get("_tenant_id") is not None
            or payload.get("_api_key_id") is not None
        ):
            raise OperationRecoveryError(
                "operation-recovery exact drain task payload target differs"
            )
        payload["operation_id"] = operation_id
        payload["bank_id"] = "engineering"
        payload["type"] = expected_type
        payload.pop("_schema", None)
        payload.pop("_tenant_id", None)
        payload.pop("_api_key_id", None)
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    async def claim_tasks(
        self,
        connection: Any,
        table: str,
        worker_id: str,
        reserved_limits: Mapping[str, int],
        shared_limit: int,
        *,
        consolidation_bank_priority: Mapping[str, int] | None = None,
    ) -> list[Mapping[str, Any]]:
        """Claim only plan-selected rows through the upstream public seam."""
        del consolidation_bank_priority
        if table not in self._ALLOWED_TABLES:
            raise OperationRecoveryError(
                "operation-recovery exact drain table is invalid"
            )
        if worker_id != self._worker_id:
            raise OperationRecoveryError(
                "operation-recovery exact drain worker identity differs"
            )
        capacity = self._claim_capacity(reserved_limits, shared_limit)
        if capacity == 0:
            return []
        await connection.execute(
            "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"
        )
        if not self._initial_guard_complete:
            await self._verify_initial_state(connection)
        else:
            await self._verify_unstarted_state(connection)
        rows = await connection.fetch(
            """
            SELECT operation_id,
                   operation_type,
                   task_payload,
                   retry_count,
                   CASE WHEN worker_id IS NULL THEN NULL
                        ELSE encode(
                            sha256(convert_to(worker_id, 'UTF8')),
                            'hex'
                        )
                   END AS worker_id_digest,
                   encode(
                       sha256(convert_to(task_payload::text, 'UTF8')),
                       'hex'
                   ) AS task_payload_digest
            FROM public.async_operations
            WHERE operation_id = ANY($1::uuid[])
              AND bank_id = 'engineering'
              AND status = 'pending'
              AND task_payload IS NOT NULL
              AND (next_retry_at IS NULL OR next_retry_at <= NOW())
            ORDER BY created_at, operation_id
            FOR UPDATE SKIP LOCKED
            """,
            self._identifiers,
        )
        safe_rows = [_mapping(row) for row in rows]
        for row in safe_rows:
            item = self._selected.get(str(row["operation_id"]))
            if (
                item is None
                or row["operation_type"] != item["operation_type"]
                or row["task_payload_digest"] != item["task_payload_digest"]
                or type(row["retry_count"]) is not int
                or row["retry_count"] < 0
                or row["retry_count"] > self._max_retries
                or (
                    str(row["operation_id"]) in self._started_ids
                    and row["worker_id_digest"] != self._worker_digest
                )
            ):
                raise OperationRecoveryError(
                    "operation-recovery exact drain claim row drifted"
                )
            row["task_payload"] = self._canonical_task_payload(row)
        chosen = self._choose_rows(safe_rows, reserved_limits, shared_limit)
        if not chosen:
            self._initial_guard_complete = True
            statuses = await connection.fetch(
                """
                SELECT operation_id::text AS operation_id,
                       status,
                       encode(
                           sha256(convert_to(task_payload::text, 'UTF8')),
                           'hex'
                       ) AS task_payload_digest
                FROM public.async_operations
                WHERE operation_id = ANY($1::uuid[])
                  AND bank_id = 'engineering'
                ORDER BY operation_id
                """,
                self._identifiers,
            )
            terminal = {"completed", "failed", "cancelled"}
            if len(statuses) != len(self._selected):
                raise OperationRecoveryError(
                    "operation-recovery exact drain selected row set changed"
                )
            for status_row in statuses:
                item = self._selected.get(status_row["operation_id"])
                if (
                    item is None
                    or status_row["task_payload_digest"]
                    != item["task_payload_digest"]
                ):
                    raise OperationRecoveryError(
                        "operation-recovery exact drain claim row drifted"
                    )
            if (
                not self._completion_signalled
                and all(row["status"] in terminal for row in statuses)
                and self._completion_callback is not None
            ):
                self._completion_signalled = True
                self._completion_callback()
            return []
        chosen_ids = [row["operation_id"] for row in chosen]
        result = await connection.execute(
            """
            UPDATE public.async_operations
            SET status = 'processing',
                worker_id = $1,
                claimed_at = NOW(),
                updated_at = NOW()
            WHERE operation_id = ANY($2::uuid[])
              AND bank_id = 'engineering'
              AND status = 'pending'
              AND task_payload IS NOT NULL
            """,
            worker_id,
            chosen_ids,
        )
        if result != f"UPDATE {len(chosen)}":
            raise OperationRecoveryError(
                "operation-recovery exact drain claim row count differs"
            )
        self._started_ids.update(str(row["operation_id"]) for row in chosen)
        self._initial_guard_complete = True
        return chosen


async def read_exact_drain_status(
    connection: Any,
    *,
    profile_id: str,
    schema: str,
    plan: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Read stable, payload-free exact-drain and outside-queue evidence."""
    verified = verify_exact_drain_plan(plan, allow_expired=True)
    if profile_id != "systalyze" or schema != "public":
        raise OperationRecoveryError(
            "operation-recovery exact drain profile is invalid"
        )
    cohort_ids = [
        item["operation_id"] for item in verified["cohort"]["operations"]
    ]
    selected = {
        item["operation_id"]: item for item in verified["selected_operations"]
    }
    selected_ids = list(selected)
    worker_digest = hashlib.sha256(
        exact_drain_worker_id(verified["plan_digest"]).encode("utf-8")
    ).hexdigest()
    generation_before = await read_generation(connection, schema, profile_id)
    async with connection.transaction(isolation="repeatable_read", readonly=True):
        rows = await read_safe_operation_rows(
            connection,
            schema=schema,
            bank_id="engineering",
            operation_ids=cohort_ids,
        )
        outside_rows = await connection.fetch(
            """
            SELECT bank_id,
                   operation_type,
                   status,
                   count(*)::bigint AS operation_count
            FROM public.async_operations
            WHERE operation_id != ALL($1::uuid[])
              AND status IN ('pending', 'processing')
            GROUP BY bank_id, operation_type, status
            ORDER BY bank_id, operation_type, status
            """,
            [uuid.UUID(value) for value in selected_ids],
        )
    generation_after = await read_generation(connection, schema, profile_id)
    if generation_before != generation_after:
        raise OperationRecoveryError(
            "migration generation changed during exact drain status"
        )
    rows_by_id = {row["operation_id"]: row for row in rows}
    cohort_by_id = {
        item["operation_id"]: item for item in verified["cohort"]["operations"]
    }
    snapshot_by_id = {
        item["operation_id"]: item
        for item in verified["live_snapshot"]["operations"]
    }
    if set(rows_by_id) != set(cohort_by_id):
        raise OperationRecoveryError(
            "operation-recovery exact drain cohort row set changed"
        )
    for operation_id, row in rows_by_id.items():
        expected = cohort_by_id[operation_id]
        if (
            row["operation_type"] != expected["operation_type"]
            or row["task_payload_digest"] != expected["task_payload_digest"]
        ):
            raise OperationRecoveryError(
                "operation-recovery exact drain cohort row drifted"
            )
        if (
            operation_id in selected
            and live_row_digest(row)
            != snapshot_by_id[operation_id]["row_digest"]
            and row["worker_id_digest"] != worker_digest
        ):
            raise OperationRecoveryError(
                "operation-recovery exact drain selected row ownership drifted"
            )
        if (
            operation_id not in selected
            and live_row_digest(row)
            != snapshot_by_id[operation_id]["row_digest"]
        ):
            raise OperationRecoveryError(
                "operation-recovery exact drain preserved row drifted"
            )
    selected_status_counts = {
        status: sum(
            rows_by_id[operation_id]["status"] == status
            for operation_id in selected
        )
        for status in ("pending", "processing", "completed", "failed", "cancelled")
    }
    selected_status_counts = {
        key: value for key, value in selected_status_counts.items() if value
    }
    preserved_status_counts = {
        status: sum(
            row["status"] == status
            for operation_id, row in rows_by_id.items()
            if operation_id not in selected
        )
        for status in ("pending", "processing", "completed", "failed", "cancelled")
    }
    preserved_status_counts = {
        key: value for key, value in preserved_status_counts.items() if value
    }
    outside = [
        {
            "bank_id": row["bank_id"],
            "operation_type": row["operation_type"],
            "status": row["status"],
            "operation_count": row["operation_count"],
        }
        for row in outside_rows
    ]
    body = {
        "schema_version": 1,
        "kind": "operation-recovery-exact-drain-status",
        "plan_digest": verified["plan_digest"],
        "generation_before": generation_before,
        "generation_after": generation_after,
        "selected_operation_count": len(selected),
        "selected_status_counts": selected_status_counts,
        "preserved_status_counts": preserved_status_counts,
        "outside_nonterminal_counts": outside,
        "observed_at": int(time.time()),
    }
    return verify_exact_drain_status(
        {**body, "status_digest": digest(body)},
        plan=verified,
    )


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
    permitted_count = plan.get("permitted_blocker_count")
    cohort_operation_ids = plan.get("reference_cohort_operation_ids")
    if (
        not isinstance(permitted_rows, list)
        or type(permitted_count) is not int
        or permitted_count != len(permitted_rows)
        or not isinstance(cohort_operation_ids, list)
        or not 1 <= permitted_count <= len(cohort_operation_ids)
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
