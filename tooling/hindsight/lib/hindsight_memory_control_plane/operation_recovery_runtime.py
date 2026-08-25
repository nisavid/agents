"""Privileged PostgreSQL seam for detached async-operation recovery.

This module deliberately has no Hindsight API, provider, or HTTP dependency.
It accepts an already authenticated local PostgreSQL connection and emits only
the closed, payload-free evidence consumed by :mod:`operation_recovery`.
"""

from __future__ import annotations

import asyncio
from builtins import BaseExceptionGroup
from collections.abc import Callable, Mapping, Sequence
from contextlib import asynccontextmanager, nullcontext
import ctypes
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import struct
import subprocess
import sys
import time
import uuid
from typing import TYPE_CHECKING, Any, Self

from .operation_recovery import (
    EXACT_DRAIN_HATCHERY_MAX_CONCURRENT,
    EXACT_DRAIN_LEGACY_HATCHERY_MAX_CONCURRENT,
    EXACT_DRAIN_PHASE_ONE_CLIENT_TIMEOUT_SECONDS,
    EXACT_DRAIN_PHASE_ONE_STATEMENT_TIMEOUT_SECONDS,
    EXACT_DRAIN_PHASE_REPAIR_CONTRACT_DIGEST,
    EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V2_DIGEST,
    EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V3_DIGEST,
    EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V4_DIGEST,
    EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V5_DIGEST,
    EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V6_DIGEST,
    EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V7_DIGEST,
    EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V8_DIGEST,
    EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V9_DIGEST,
    EXACT_DRAIN_TRANSACTION_TIMEOUT_SECONDS,
    EXPECTED_CLAIM_RELEASE_ROW_COUNT,
    EXPECTED_OPERATION_COUNTS,
    FAILURE_CAUSE_FAMILIES,
    OperationRecoveryError,
    exact_drain_execution_deadline,
    verify_checkpoint_continuation_handoff,
    verify_exact_drain_authorization_receipt,
    verify_exact_drain_plan,
    verify_exact_drain_status,
    verify_post_abort_recovery_plan,
)
from .canonical import StrictJsonError, digest, strict_json_loads

if TYPE_CHECKING:
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
        "HINDSIGHT_API_RETAIN_LLM_TIMEOUT",
        "HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS",
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
EXACT_DRAIN_RETAIN_LLM_TIMEOUT_SECONDS = "3600"
EXACT_DRAIN_RETAIN_MAX_COMPLETION_TOKENS = "8192"
EXACT_DRAIN_PROVIDER_ORDER = (
    "work-codex",
    "personal-codex",
    "alt1-codex",
    "alt2-codex",
    "hatchery",
)
EXACT_DRAIN_EXECUTION_LEASE_EXPIRED_MESSAGE = (
    "operation-recovery exact drain execution lease expired"
)
EXACT_DRAIN_EXECUTION_LEASE_ERROR_DIGEST = hashlib.sha256(
    (
        f"{OperationRecoveryError.__name__}: "
        f"{EXACT_DRAIN_EXECUTION_LEASE_EXPIRED_MESSAGE}"
    ).encode("utf-8")
).hexdigest()
EXACT_DRAIN_OPERATION_ATTEMPT_TIMEOUT_MESSAGE = (
    "operation-recovery exact drain operation attempt exceeded its deadline"
)
EXACT_DRAIN_RETRY_CEILING_MESSAGE = (
    "operation-recovery exact drain retry ceiling reached"
)
EXACT_DRAIN_PHASE_ONE_DEADLINE_TIMEOUT_MESSAGE = (
    "exact drain retain phase one exceeded its deadline"
)
EXACT_DRAIN_PHASE_ONE_DEADLINE_TIMEOUT_ERROR = (
    f"{OperationRecoveryError.__name__}: "
    f"{EXACT_DRAIN_PHASE_ONE_DEADLINE_TIMEOUT_MESSAGE}"
)
EXACT_DRAIN_PHASE_ONE_QUERY_TIMEOUT_PREFIX = (
    "operation-recovery exact drain phase-one query timed out"
)
EXACT_DRAIN_PHASE_ONE_QUERY_TIMEOUT = re.compile(
    re.escape(EXACT_DRAIN_PHASE_ONE_QUERY_TIMEOUT_PREFIX)
    + r" at (retain\.phase1\.[A-Za-z0-9._:/=-]{1,112})(?:\s|$)"
)
EXACT_DRAIN_SCHEMA_FIVE_SENTINELS = (
    (
        "operation_attempt_deadline",
        "operation_attempt_timeout",
        (EXACT_DRAIN_OPERATION_ATTEMPT_TIMEOUT_MESSAGE.casefold(),),
    ),
    (
        "phase_one_deadline",
        "phase_one_timeout",
        (
            EXACT_DRAIN_PHASE_ONE_DEADLINE_TIMEOUT_MESSAGE.casefold(),
            EXACT_DRAIN_PHASE_ONE_QUERY_TIMEOUT_PREFIX.casefold(),
        ),
    ),
    (
        "database_statement_timeout",
        "database_statement_timeout",
        ("statement timeout", "sqlstate 57014"),
    ),
    (
        "provider_queue_timeout",
        "provider_queue_timeout",
        ("provider_queue_timeout",),
    ),
    (
        "provider_execution_timeout",
        "provider_execution_timeout",
        ("provider_execution_timeout",),
    ),
)
EXACT_DRAIN_BAD_REQUEST_SHORTHAND_PATTERN = (
    r"bad.?request|client.?error|"
    r"status.?400([^0-9]|$)|error.?400([^0-9]|$)"
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
EXACT_DRAIN_MAX_DEPENDENCY_ENTRIES = 100_000
EXACT_DRAIN_MAX_DEPENDENCY_FILE_BYTES = 512 * 1024 * 1024
EXACT_DRAIN_MAX_DEPENDENCY_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
EXACT_DRAIN_CANDIDATE_RUNTIME_DIRECTORY = "exact_drain_runtime"
EXACT_DRAIN_CANDIDATE_RESOLVER_PATH = Path(
    "hindsight_api/engine/entity_resolver.py"
)
EXACT_DRAIN_CANDIDATE_POSTGRESQL_OPS_PATH = Path(
    "hindsight_api/engine/db/ops_postgresql.py"
)
EXACT_DRAIN_CANDIDATE_MEMORY_ENGINE_PATH = Path(
    "hindsight_api/engine/memory_engine.py"
)
EXACT_DRAIN_CANDIDATE_POLLER_PATH = Path(
    "hindsight_api/worker/poller.py"
)
EXACT_DRAIN_PROVIDER_SOURCE_NAMES = (
    "sitecustomize.py",
    "hindsight_llm_failover.py",
)
EXACT_DRAIN_TASK_CANCELLATION_TIMEOUT_SECONDS = 5.0
EXACT_DRAIN_START_MESSAGE_PREFIX = b"exact-drain-start-v1 "
EXACT_DRAIN_START_MESSAGE_BYTES = (
    len(EXACT_DRAIN_START_MESSAGE_PREFIX) + (64 * 3) + 3
)
EXACT_DRAIN_AUTHENTICATION_ERROR = re.compile(
    r"auth|credential|token|unauthori[sz]ed|forbidden|401|403",
    re.IGNORECASE,
)
EXACT_DRAIN_CAPACITY_ERROR = re.compile(
    r"capacity|quota|rate.?limit|usage.?limit|429|exhaust",
    re.IGNORECASE,
)
EXACT_DRAIN_TRANSPORT_ERROR = re.compile(
    r"connect|network|timeout|transport|unavailable|hatchery|502|503|504",
    re.IGNORECASE,
)
EXACT_DRAIN_FUZZY_QUERY_BATCH_SIZE = 10
EXACT_DRAIN_COOCCURRENCE_QUERY_BATCH_SIZE = 128
EXACT_DRAIN_CHECKPOINT_PROJECTION = """
    CASE WHEN result_metadata->>'facts_committed' = 'true'
         THEN TRUE ELSE FALSE END AS checkpoint_facts_committed,
    CASE WHEN jsonb_typeof(result_metadata->'facts_committed_document_ids') = 'array'
         THEN jsonb_array_length(result_metadata->'facts_committed_document_ids')
         ELSE 0 END AS checkpoint_committed_document_count,
    CASE WHEN COALESCE(result_metadata->>'unit_ids_count', '') ~ '^[0-9]{1,9}$'
         THEN (result_metadata->>'unit_ids_count')::integer
         ELSE 0 END AS checkpoint_unit_ids_count,
    left(COALESCE(result_metadata->'progress'->>'stage', 'unavailable'), 128)
         AS checkpoint_stage,
    CASE WHEN COALESCE(result_metadata->'progress'->>'processed', '')
                   ~ '^[0-9]{1,9}$'
         THEN (result_metadata->'progress'->>'processed')::integer
         ELSE 0 END AS checkpoint_processed,
    CASE WHEN COALESCE(result_metadata->'progress'->>'total', '')
                   ~ '^[0-9]{1,9}$'
         THEN (result_metadata->'progress'->>'total')::integer
         ELSE 0 END AS checkpoint_total
""".strip()
LOGGER = logging.getLogger(__name__)


def _exact_drain_error_is_transient(error_message: Any) -> bool:
    return (
        isinstance(error_message, str)
        and EXACT_DRAIN_AUTHENTICATION_ERROR.search(error_message) is None
        and (
            EXACT_DRAIN_CAPACITY_ERROR.search(error_message) is not None
            or EXACT_DRAIN_TRANSPORT_ERROR.search(error_message) is not None
        )
    )


def _exact_drain_schema_five_sentinel(
    error_message: str,
) -> tuple[str, str] | None:
    lowered = error_message.casefold()
    for cause_family, category, markers in EXACT_DRAIN_SCHEMA_FIVE_SENTINELS:
        if any(marker in lowered for marker in markers):
            return cause_family, category
    return None


def _exact_drain_schema_five_sentinel_sql() -> str:
    cases = []
    for cause_family, _category, markers in EXACT_DRAIN_SCHEMA_FIVE_SENTINELS:
        predicates = []
        for marker in markers:
            escaped_marker = marker.replace("'", "''")
            predicates.append(
                "strpos(lower(error_message), "
                f"'{escaped_marker}') > 0"
            )
        cases.append(
            "    WHEN "
            + "\n        OR ".join(predicates)
            + f"\n        THEN '{cause_family}'"
        )
    return "\n".join(cases)


def exact_drain_platform_environment() -> dict[str, str]:
    """Return OS-owned variables injected across the worker exec boundary."""
    return {
        "__CF_USER_TEXT_ENCODING": f"0x{os.geteuid():X}:0x0:0x0",
    }


def process_start_time(pid: int) -> str | None:
    """Return the cross-platform process start token used by exact journals."""
    if type(pid) is not int or pid <= 1:
        return None
    if sys.platform == "darwin":

        class ProcBsdInfo(ctypes.Structure):
            _fields_ = [
                ("pbi_flags", ctypes.c_uint32),
                ("pbi_status", ctypes.c_uint32),
                ("pbi_xstatus", ctypes.c_uint32),
                ("pbi_pid", ctypes.c_uint32),
                ("pbi_ppid", ctypes.c_uint32),
                ("pbi_uid", ctypes.c_uint32),
                ("pbi_gid", ctypes.c_uint32),
                ("pbi_ruid", ctypes.c_uint32),
                ("pbi_rgid", ctypes.c_uint32),
                ("pbi_svuid", ctypes.c_uint32),
                ("pbi_svgid", ctypes.c_uint32),
                ("pbi_rfu_1", ctypes.c_uint32),
                ("pbi_comm", ctypes.c_char * 16),
                ("pbi_name", ctypes.c_char * 32),
                ("pbi_nfiles", ctypes.c_uint32),
                ("pbi_pgid", ctypes.c_uint32),
                ("pbi_pjobc", ctypes.c_uint32),
                ("e_tdev", ctypes.c_uint32),
                ("e_tpgid", ctypes.c_uint32),
                ("pbi_nice", ctypes.c_int32),
                ("pbi_start_tvsec", ctypes.c_uint64),
                ("pbi_start_tvusec", ctypes.c_uint64),
            ]

        try:
            libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
            proc_pidinfo = libproc.proc_pidinfo
            proc_pidinfo.argtypes = [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint64,
                ctypes.c_void_p,
                ctypes.c_int,
            ]
            proc_pidinfo.restype = ctypes.c_int
            info = ProcBsdInfo()
            result = proc_pidinfo(
                pid,
                3,
                0,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
        except (AttributeError, OSError):
            pass
        else:
            if result == ctypes.sizeof(info):
                return (
                    f"darwin:{info.pbi_start_tvsec}:"
                    f"{info.pbi_start_tvusec}"
                )

    try:
        raw_stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        start_ticks = raw_stat.rsplit(")", 1)[1].split()[19]
    except (FileNotFoundError, IndexError, OSError, UnicodeDecodeError):
        pass
    else:
        return f"linux:{start_ticks}"

    try:
        result = subprocess.run(
            ["/bin/ps", "-o", "lstart=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = " ".join(result.stdout.split())
    if not value or len(value) > 128:
        return None
    return value


def exact_drain_start_message(
    plan_digest: str,
    authorization_receipt_digest: str,
    application_journal_digest: str,
) -> bytes:
    """Return the one canonical parent-to-child start authorization."""
    values = (
        plan_digest,
        authorization_receipt_digest,
        application_journal_digest,
    )
    if any(
        not isinstance(value, str)
        or re.fullmatch(r"[0-9a-f]{64}", value) is None
        for value in values
    ):
        raise OperationRecoveryError("exact drain start authority is invalid")
    return (
        EXACT_DRAIN_START_MESSAGE_PREFIX
        + b" ".join(value.encode("ascii") for value in values)
        + b"\n"
    )


def verify_exact_drain_start_message(
    value: bytes,
    *,
    plan_digest: str,
    authorization_receipt_digest: str,
) -> str:
    """Verify a bounded canonical gate message and return its journal digest."""
    if not isinstance(value, bytes) or len(value) != EXACT_DRAIN_START_MESSAGE_BYTES:
        raise OperationRecoveryError("exact drain start was not authorized")
    prefix = EXACT_DRAIN_START_MESSAGE_PREFIX
    if not value.startswith(prefix) or not value.endswith(b"\n"):
        raise OperationRecoveryError("exact drain start was not authorized")
    parts = value[len(prefix) : -1].split(b" ")
    try:
        decoded = tuple(part.decode("ascii") for part in parts)
    except UnicodeDecodeError as error:
        raise OperationRecoveryError(
            "exact drain start was not authorized"
        ) from error
    if (
        len(decoded) != 3
        or exact_drain_start_message(*decoded) != value
        or decoded[0] != plan_digest
        or decoded[1] != authorization_receipt_digest
    ):
        raise OperationRecoveryError("exact drain start was not authorized")
    return decoded[2]


def validate_exact_drain_provider_policy(
    policy: ProviderRuntimePolicy,
    *,
    plan_schema_version: int | None = None,
) -> None:
    """Require the exact four-Codex then Hatchery provider authority."""
    members = {member.id: member for member in policy.members}
    hatchery_max_concurrent = (
        EXACT_DRAIN_HATCHERY_MAX_CONCURRENT
        if policy.schema_version == 2
        else EXACT_DRAIN_LEGACY_HATCHERY_MAX_CONCURRENT
    )
    legacy_timeout_invalid = (
        policy.schema_version == 1
        and members.get("hatchery") is not None
        and members["hatchery"].timeout_seconds != 1200
    )
    split_timeout_invalid = (
        policy.schema_version == 2
        and any(
            member.queue_timeout_seconds != 3600
            or member.execution_timeout_seconds
            != (
                (
                    3600
                    if plan_schema_version in {11, 12, 13, 14, 15}
                    else 1200
                )
                if member.id == "hatchery"
                else 3600
            )
            for member in policy.members
        )
    )
    if (
        policy.schema_version not in {1, 2}
        or (
            plan_schema_version in {11, 12, 13, 14, 15}
            and policy.schema_version != 2
        )
        or (
            plan_schema_version in {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}
            and policy.schema_version != 1
        )
        or policy.default_usage_limit_cooldown_seconds != 300
        or policy.failover_order != EXACT_DRAIN_PROVIDER_ORDER
        or set(members) != set(EXACT_DRAIN_PROVIDER_ORDER)
        or any(
            members[member_id].identity.provider != "openai-codex"
            or members[member_id].identity.base_url != ""
            or members[member_id].identity.credential_marker
            != f"provider-policy:{member_id}"
            or members[member_id].credential_mode != "oauth-home"
            or members[member_id].credential_locator != locator
            or members[member_id].quota_cooldown is not True
            or members[member_id].max_retries != 0
            or members[member_id].max_concurrent is not None
            for member_id, locator in EXACT_DRAIN_OAUTH_LOCATORS.items()
        )
        or members["hatchery"].identity.provider != "lmstudio"
        or members["hatchery"].identity.base_url
        != "http://hatchery.komodo-vector.ts.net:13305/v1"
        or members["hatchery"].identity.credential_marker is not None
        or members["hatchery"].credential_mode != "none"
        or members["hatchery"].credential_locator is not None
        or legacy_timeout_invalid
        or split_timeout_invalid
        or members["hatchery"].max_retries != 0
        or members["hatchery"].max_concurrent != hatchery_max_concurrent
    ):
        raise OperationRecoveryError(
            "operation-recovery exact drain provider policy differs"
        )


def exact_drain_effective_profile_digest(
    policy: ProviderRuntimePolicy,
    environment: Mapping[str, str],
    *,
    plan_schema_version: int | None = None,
) -> str:
    """Validate and bind the effective five-member Hindsight LLM profile."""
    if plan_schema_version is None:
        plan_schema_version = 12 if policy.schema_version == 2 else 10
    validate_exact_drain_provider_policy(
        policy,
        plan_schema_version=plan_schema_version,
    )
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
FAILURE_CAUSE_FAMILY_SQL = f"""
CASE
{_exact_drain_schema_five_sentinel_sql()}
    WHEN lower(error_message) ~
         '(auth|credential|token|unauthori[sz]ed|forbidden|401|403)'
        THEN 'provider_authentication'
    WHEN lower(error_message) ~
         '(capacity|quota|rate.?limit|usage.?limit|429|exhaust)'
        THEN 'provider_capacity'
    WHEN lower(error_message) ~ (
         '({EXACT_DRAIN_BAD_REQUEST_SHORTHAND_PATTERN}|'
         || '(client|server)[[:space:]]+error[[:space:]]+["'']?'
         || '400([^0-9]|$)|'
         || '(status|error)(_code|[[:space:]]+code)?'
         || '[ =:''"]+400([^0-9]|$))'
    )
        THEN 'provider_bad_request'
    WHEN lower(error_message) LIKE '%timeouterror%'
        OR lower(error_message) ~
           '(^|[^a-z])timeout([^a-z]|$)|timed.?out'
        THEN 'upstream_timeout'
    WHEN lower(error_message) ~
         '(connect|network|transport|unavailable|hatchery|502|503|504)'
        THEN 'provider_transport'
    WHEN lower(error_message) ~
         '(validation|invalid.?json|json.?decode|structured.?output|schema)'
        THEN 'structured_output_validation'
    WHEN lower(error_message) ~
         '(integrity|constraint|duplicate.?key|foreign.?key)'
        THEN 'database_integrity'
    WHEN lower(error_message) ~ '(cancelled|canceled)'
        THEN 'cancellation'
    ELSE 'unknown'
END
""".strip()
FAILURE_CLASSIFICATION_QUERY = """
SELECT
    cause_family,
    error_digest,
    count(*)::bigint AS occurrence_count
FROM (
    SELECT
        {failure_cause_family} AS cause_family,
        encode(
            sha256(convert_to(error_message, 'UTF8')),
            'hex'
        ) AS error_digest
    FROM {schema}.async_operations
    WHERE bank_id = $1
      AND operation_id = ANY($2::uuid[])
      AND error_message IS NOT NULL
      AND error_message <> ''
) AS classified
GROUP BY cause_family, error_digest
ORDER BY cause_family, error_digest
""".replace("{failure_cause_family}", FAILURE_CAUSE_FAMILY_SQL)
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


def _postgres_safe_error_text(value: str) -> str:
    """Keep exact-drain diagnostics writable as PostgreSQL UTF-8 text."""
    return (
        value[:5000]
        .encode("utf-8", errors="replace")
        .decode("utf-8")
        .replace("\x00", "\ufffd")
    )


def _exact_drain_phase_one_query_timeout_stage(
    error_message: str,
) -> str | None:
    if not isinstance(error_message, str):
        return None
    matched = EXACT_DRAIN_PHASE_ONE_QUERY_TIMEOUT.search(error_message)
    return None if matched is None else matched.group(1)


def _exact_drain_worker_initialization_timed_out(
    error: BaseException,
    message: str,
) -> bool:
    return (
        isinstance(error, TimeoutError)
        or getattr(error, "sqlstate", None) == "57014"
        or "statement timeout" in message.casefold()
    )


def _exact_drain_failure_evidence(
    error_message: str,
    *,
    retryable: bool,
    category_override: str | None = None,
    progress_schema_version: int = 5,
) -> dict[str, Any]:
    if progress_schema_version not in {1, 2, 3, 4, 5, 6}:
        raise OperationRecoveryError(
            "exact drain progress schema version is invalid"
        )
    if category_override not in {
        None,
        "retry_ceiling",
        "terminal_state_persistence",
        "nonquiescent_shutdown",
        "operation_attempt_timeout",
    }:
        raise OperationRecoveryError(
            "exact drain failure category is invalid"
        )
    safe_error = _postgres_safe_error_text(error_message)
    status_match = re.search(
        r"(?:client|server)\s+error\s+['\"]?([45][0-9]{2})\b|"
        r"(?:status|error)(?:_code|\s+code)?[ =:'\"]+([45][0-9]{2})\b",
        safe_error,
        re.IGNORECASE,
    )
    http_status = (
        None
        if status_match is None
        else int(status_match.group(1) or status_match.group(2))
    )
    lowered = safe_error.casefold()
    if progress_schema_version != 5:
        if category_override is not None:
            category = category_override
        elif safe_error == "provider_queue_timeout":
            category = "provider_queue_timeout"
        elif safe_error == "provider_execution_timeout":
            category = "provider_execution_timeout"
        elif safe_error == EXACT_DRAIN_OPERATION_ATTEMPT_TIMEOUT_MESSAGE:
            category = "operation_attempt_timeout"
        elif safe_error in {
            EXACT_DRAIN_PHASE_ONE_DEADLINE_TIMEOUT_MESSAGE,
            EXACT_DRAIN_PHASE_ONE_DEADLINE_TIMEOUT_ERROR,
        }:
            category = "phase_one_timeout"
        elif http_status == 400:
            category = "provider_bad_request"
        elif EXACT_DRAIN_AUTHENTICATION_ERROR.search(safe_error) is not None:
            category = "provider_authentication"
        elif EXACT_DRAIN_CAPACITY_ERROR.search(safe_error) is not None:
            category = "provider_capacity"
        elif _exact_drain_phase_one_query_timeout_stage(safe_error) is not None:
            category = "phase_one_timeout"
        elif "timeouterror" in lowered or "statement timeout" in lowered:
            category = "operation_error"
        elif EXACT_DRAIN_TRANSPORT_ERROR.search(safe_error) is not None:
            category = "provider_transport"
        elif not safe_error:
            category = "unclassified_empty"
        else:
            category = "operation_error"
        return {
            "category": category,
            "retryable": retryable,
            "http_status": http_status,
            "error_digest": hashlib.sha256(
                safe_error.encode("utf-8")
            ).hexdigest(),
        }
    sentinel = _exact_drain_schema_five_sentinel(safe_error)
    if category_override is not None:
        category = category_override
    elif sentinel is not None:
        _cause_family, category = sentinel
    elif EXACT_DRAIN_AUTHENTICATION_ERROR.search(safe_error) is not None:
        category = "provider_authentication"
    elif EXACT_DRAIN_CAPACITY_ERROR.search(safe_error) is not None:
        category = "provider_capacity"
    elif http_status == 400 or re.search(
        EXACT_DRAIN_BAD_REQUEST_SHORTHAND_PATTERN,
        safe_error,
        re.IGNORECASE,
    ) is not None:
        category = "provider_bad_request"
    elif (
        "timeouterror" in lowered
        or re.search(
            r"(^|[^a-z])timeout([^a-z]|$)|timed.?out",
            lowered,
        )
        is not None
    ):
        category = "upstream_timeout"
    elif EXACT_DRAIN_TRANSPORT_ERROR.search(safe_error) is not None:
        category = "provider_transport"
    elif not safe_error:
        category = "unclassified_empty"
    else:
        category = "operation_error"
    return {
        "category": category,
        "retryable": retryable,
        "http_status": http_status,
        "error_digest": hashlib.sha256(safe_error.encode("utf-8")).hexdigest(),
    }


def _exact_drain_closed_cause_evidence(
    cause_family: Any,
    error_digest: Any,
    *,
    retryable: bool,
) -> dict[str, Any]:
    category = {
        "operation_attempt_deadline": "operation_attempt_timeout",
        "phase_one_deadline": "phase_one_timeout",
        "database_statement_timeout": "database_statement_timeout",
        "provider_queue_timeout": "provider_queue_timeout",
        "provider_execution_timeout": "provider_execution_timeout",
        "provider_authentication": "provider_authentication",
        "provider_capacity": "provider_capacity",
        "provider_bad_request": "provider_bad_request",
        "provider_transport": "provider_transport",
        "upstream_timeout": "upstream_timeout",
        "structured_output_validation": "operation_error",
        "database_integrity": "operation_error",
        "cancellation": "operation_error",
        "unknown": "operation_error",
        "unclassified_empty": "unclassified_empty",
    }.get(cause_family)
    if category is None or not isinstance(error_digest, str) or re.fullmatch(
        r"[0-9a-f]{64}", error_digest
    ) is None:
        raise OperationRecoveryError(
            "operation-recovery exact drain closed cause is invalid"
        )
    return {
        "category": category,
        "retryable": retryable,
        "http_status": None,
        "error_digest": error_digest,
    }


def exact_drain_worker_failure_evidence(
    error: BaseException,
    *,
    worker_stage: str | None = None,
    progress_schema_version: int = 5,
) -> dict[str, Any]:
    """Return a closed retry projection for a worker-level failure."""
    if not isinstance(error, BaseException):
        raise OperationRecoveryError(
            "exact drain worker failure is invalid"
        )
    message = str(error)
    typed_message = (
        f"{type(error).__name__}: {message}"
        if message
        else type(error).__name__
    )
    phase_one_deadline = (
        isinstance(error, OperationRecoveryError)
        and message == EXACT_DRAIN_PHASE_ONE_DEADLINE_TIMEOUT_MESSAGE
    )
    if (
        isinstance(error, OperationRecoveryError)
        and message == EXACT_DRAIN_EXECUTION_LEASE_EXPIRED_MESSAGE
    ):
        evidence = {
            "category": "execution_lease_expired",
            "retryable": False,
            "http_status": None,
            "error_digest": EXACT_DRAIN_EXECUTION_LEASE_ERROR_DIGEST,
        }
    elif (
        isinstance(error, OperationRecoveryError)
        and message == EXACT_DRAIN_OPERATION_ATTEMPT_TIMEOUT_MESSAGE
    ) or phase_one_deadline:
        evidence = _exact_drain_failure_evidence(
            message,
            retryable=False,
            progress_schema_version=progress_schema_version,
        )
    elif _exact_drain_worker_initialization_timed_out(error, message):
        evidence = _exact_drain_failure_evidence(
            typed_message,
            retryable=False,
            progress_schema_version=progress_schema_version,
        )
        evidence["category"] = "worker_initialization_timeout"
    else:
        evidence = _exact_drain_failure_evidence(
            typed_message,
            retryable=False,
            progress_schema_version=progress_schema_version,
        )
    if evidence["category"] == "phase_one_timeout" and not phase_one_deadline:
        evidence["category"] = "worker_initialization_timeout"
    elif evidence["category"] == "operation_error":
        runtime_stage = (
            isinstance(worker_stage, str)
            and (
                worker_stage.startswith("worker.poller.")
                or worker_stage.startswith("worker.shutdown.")
                or worker_stage == "worker.main"
            )
        )
        evidence["category"] = (
            "worker_runtime_failure" if runtime_stage else "worker_initialization"
        )
    evidence["retryable"] = evidence["category"] in {
        "provider_capacity",
        "provider_transport",
        "worker_initialization_timeout",
    }
    return evidence


def _exact_drain_checkpoint_evidence(
    row: Mapping[str, Any],
) -> dict[str, Any] | None:
    names = {
        "facts_committed": "checkpoint_facts_committed",
        "committed_document_count": "checkpoint_committed_document_count",
        "unit_ids_count": "checkpoint_unit_ids_count",
        "stage": "checkpoint_stage",
        "processed": "checkpoint_processed",
        "total": "checkpoint_total",
    }
    if not any(name in row for name in names.values()):
        return None
    values = {key: row.get(name) for key, name in names.items()}
    stage = values["stage"]
    if not isinstance(stage, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:/=-]{0,127}", stage
    ):
        stage = "unavailable"
    for key in (
        "committed_document_count",
        "unit_ids_count",
        "processed",
        "total",
    ):
        value = values[key]
        if type(value) is not int or value < 0:
            values[key] = 0
    values["processed"] = min(values["processed"], values["total"])
    return {
        "facts_committed": values["facts_committed"] is True,
        "committed_document_count": values["committed_document_count"],
        "unit_ids_count": values["unit_ids_count"],
        "stage": stage,
        "processed": values["processed"],
        "total": values["total"],
    }


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


async def read_failure_classifications(
    connection: Any,
    *,
    schema: str,
    operation_ids: Sequence[str],
) -> list[dict[str, Any]]:
    """Return closed failure causes and digests without returning error text."""
    quoted_schema = _quoted_identifier(schema, "database schema")
    try:
        identifiers = [uuid.UUID(value) for value in operation_ids]
    except (AttributeError, TypeError, ValueError) as error:
        raise OperationRecoveryError("operation ID set is invalid") from error
    if not identifiers or len(identifiers) != len(set(identifiers)):
        raise OperationRecoveryError("operation ID set is invalid")
    rows = await connection.fetch(
        FAILURE_CLASSIFICATION_QUERY.format(schema=quoted_schema),
        "engineering",
        identifiers,
    )
    classifications = []
    for row_value in rows:
        row = _mapping(row_value)
        cause_family = row.get("cause_family")
        error_digest = row.get("error_digest")
        occurrence_count = row.get("occurrence_count")
        if (
            cause_family not in FAILURE_CAUSE_FAMILIES
            or not isinstance(error_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", error_digest) is None
            or type(occurrence_count) is not int
            or occurrence_count < 1
        ):
            raise OperationRecoveryError(
                "operation-recovery failure classification is invalid"
            )
        classifications.append(
            {
                "cause_family": cause_family,
                "error_digest": error_digest,
                "occurrence_count": occurrence_count,
            }
        )
    if classifications != sorted(
        classifications,
        key=lambda item: (item["cause_family"], item["error_digest"]),
    ) or len(
        {
            (item["cause_family"], item["error_digest"])
            for item in classifications
        }
    ) != len(classifications):
        raise OperationRecoveryError(
            "operation-recovery failure classification is invalid"
        )
    return classifications


def _exact_drain_status_body(
    *,
    plan: Mapping[str, Any],
    generation_before: str,
    generation_after: str,
    selected_operation_count: int,
    selected_status_counts: Mapping[str, int],
    preserved_status_counts: Mapping[str, int],
    outside_nonterminal_counts: Sequence[Mapping[str, Any]],
    failure_classifications: Sequence[Mapping[str, Any]],
    observed_at: int,
) -> dict[str, Any]:
    """Build the one canonical payload-free exact-drain status body."""
    schema_with_failure_classifications = plan.get("schema_version") in {
        12,
        13,
        14,
    }
    return {
        "schema_version": 2 if schema_with_failure_classifications else 1,
        "kind": "operation-recovery-exact-drain-status",
        "plan_digest": plan["plan_digest"],
        "generation_before": generation_before,
        "generation_after": generation_after,
        "selected_operation_count": selected_operation_count,
        "selected_status_counts": dict(selected_status_counts),
        "preserved_status_counts": dict(preserved_status_counts),
        "outside_nonterminal_counts": [
            dict(item) for item in outside_nonterminal_counts
        ],
        **(
            {
                "failure_classifications": [
                    dict(item) for item in failure_classifications
                ]
            }
            if schema_with_failure_classifications
            else {}
        ),
        "observed_at": observed_at,
    }


def exact_drain_worker_id(plan_digest: str) -> str:
    """Derive the private worker identity from an approved plan digest."""
    if not isinstance(plan_digest, str) or not re.fullmatch(
        r"[0-9a-f]{64}", plan_digest
    ):
        raise OperationRecoveryError("exact drain plan digest is invalid")
    return f"operation-recovery-exact-drain-{plan_digest[:12]}"


class ExactDrainWorkerMainShutdownBridge:
    """Bind an internal exact-drain stop to worker-main's graceful event."""

    def __init__(self, worker_main_module: Any) -> None:
        installer = getattr(
            worker_main_module,
            "_install_shutdown_signal_handlers",
            None,
        )
        if not callable(installer):
            raise OperationRecoveryError(
                "exact drain worker shutdown handler seam is unavailable"
            )
        self._module = worker_main_module
        self._upstream_installer = installer
        self._handler: Callable[[], None] | None = None
        self._requested = False

    def __enter__(self) -> Self:
        self._module._install_shutdown_signal_handlers = self.install
        return self

    def __exit__(self, *_arguments: object) -> None:
        self._module._install_shutdown_signal_handlers = (
            self._upstream_installer
        )

    def install(self, loop: Any, handler: Callable[[], None]) -> bool:
        if self._handler is not None or not callable(handler):
            raise OperationRecoveryError(
                "exact drain worker shutdown handler was rebound"
            )
        self._handler = handler

        def external_request() -> None:
            self._requested = True
            handler()

        return bool(self._upstream_installer(loop, external_request))

    def request(self) -> None:
        if self._handler is None:
            raise OperationRecoveryError(
                "exact drain worker shutdown handler is unavailable"
            )
        if not self._requested:
            self._requested = True
            self._handler()


class ExactDrainUvicornSignalGuard:
    """Keep exact-worker SIGTERM ownership in worker-main's shutdown seam."""

    def __init__(self, server_type: type[Any]) -> None:
        capture_signals = getattr(server_type, "capture_signals", None)
        if not callable(capture_signals):
            raise OperationRecoveryError(
                "exact drain Uvicorn signal seam is unavailable"
            )
        self._server_type = server_type
        self._upstream_capture_signals = capture_signals
        self._replacement: Callable[[Any], Any] | None = None

    def __enter__(self) -> Self:
        if (
            getattr(self._server_type, "capture_signals", None)
            is not self._upstream_capture_signals
        ):
            raise OperationRecoveryError(
                "exact drain Uvicorn signal seam drifted"
            )

        def preserve_worker_main_handler(_server: Any) -> Any:
            return nullcontext()

        self._replacement = preserve_worker_main_handler
        self._server_type.capture_signals = preserve_worker_main_handler
        return self

    def __exit__(self, *_arguments: object) -> None:
        replacement = self._replacement
        observed = getattr(self._server_type, "capture_signals", None)
        self._server_type.capture_signals = self._upstream_capture_signals
        if replacement is None or observed is not replacement:
            raise OperationRecoveryError(
                "exact drain Uvicorn signal seam was rebound"
            )


def install_exact_drain_runtime_guards(
    postgresql_ops_type: type[Any],
    worker_poller_type: type[Any],
    memory_engine_type: type[Any],
    adapter: Any,
    *,
    request_worker_shutdown: Callable[[], None] | None = None,
) -> None:
    """Restrict upstream worker lifecycle seams to the exact drain cohort."""

    upstream_execute_task_inner = getattr(
        worker_poller_type,
        "_execute_task_inner",
        None,
    )
    upstream_claim_batch_inner = getattr(
        worker_poller_type,
        "_claim_batch_for_schema_inner",
        None,
    )
    upstream_claim_batch = getattr(
        worker_poller_type,
        "_claim_batch_for_schema",
        None,
    )
    upstream_fold_retain_peers = getattr(
        worker_poller_type,
        "_fold_retain_peers",
        None,
    )
    upstream_cleanup_task = getattr(
        worker_poller_type,
        "_cleanup_task",
        None,
    )
    upstream_shutdown_graceful = getattr(
        worker_poller_type,
        "shutdown_graceful",
        None,
    )
    upstream_run = getattr(worker_poller_type, "run", None)
    plan = getattr(adapter, "_plan", {})
    records_worker_lifecycle = (
        isinstance(plan, Mapping)
        and plan.get("progress_schema_version") in {3, 4, 5, 6}
    )
    upstream_memory_initialize = getattr(memory_engine_type, "initialize", None)
    if not callable(upstream_run) or (
        records_worker_lifecycle and not callable(upstream_memory_initialize)
    ):
        raise OperationRecoveryError(
            "operation-recovery required worker lifecycle seam is unavailable"
        )
    if (
        not callable(upstream_execute_task_inner)
        or not callable(upstream_claim_batch_inner)
        or not callable(upstream_claim_batch)
    ):
        raise OperationRecoveryError(
            "operation-recovery required worker progress seam is unavailable"
        )
    if (
        upstream_fold_retain_peers is not None
        and not callable(upstream_fold_retain_peers)
    ):
        raise OperationRecoveryError(
            "operation-recovery retain-folding seam is invalid"
        )

    claim_release_disabled = False
    worker_shutdown_requested = False

    async def initialize_exact_memory(
        engine: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        adapter.record_worker_stage(
            status="starting",
            stage="worker.memory.initialize",
        )
        try:
            result = await upstream_memory_initialize(engine, *args, **kwargs)
        except BaseException as error:  # noqa: BLE001
            adapter.record_worker_failure(error, exit_code=2)
            raise
        adapter.record_worker_stage(
            status="running",
            stage="worker.memory.ready",
        )
        return result
    phase_one_timeout_seconds = getattr(
        adapter,
        "phase_one_timeout_seconds",
        None,
    )
    operation_attempt_timeout_seconds = getattr(
        adapter,
        "operation_attempt_timeout_seconds",
        None,
    )
    operation_attempt_timeout_disposition = getattr(
        adapter,
        "operation_attempt_timeout_disposition",
        "worker-fail-stop",
    )
    if operation_attempt_timeout_seconds is not None and (
        type(operation_attempt_timeout_seconds) not in {int, float}
        or operation_attempt_timeout_seconds <= 0
    ):
        raise OperationRecoveryError(
            "exact drain operation-attempt timeout authority is invalid"
        )
    if operation_attempt_timeout_disposition not in {
        "worker-fail-stop",
        "task-retry-after-quiescence",
    }:
        raise OperationRecoveryError(
            "exact drain operation-attempt timeout disposition is invalid"
        )
    retry_deadlines_after_quiescence = (
        operation_attempt_timeout_disposition
        == "task-retry-after-quiescence"
    )
    if retry_deadlines_after_quiescence and not callable(
        getattr(adapter, "schedule_retry", None)
    ):
        raise OperationRecoveryError(
            "exact drain operation-attempt retry seam is unavailable"
        )
    phase_one_nested_stage_prefixes = getattr(
        adapter,
        "phase_one_nested_stage_prefixes",
        (),
    )
    if (
        not isinstance(phase_one_nested_stage_prefixes, tuple)
        or any(
            not isinstance(prefix, str) or not prefix
            for prefix in phase_one_nested_stage_prefixes
        )
    ):
        raise OperationRecoveryError(
            "exact drain phase-one nested-stage authority is invalid"
        )
    if phase_one_timeout_seconds is not None and (
        type(phase_one_timeout_seconds) not in {int, float}
        or phase_one_timeout_seconds <= 0
    ):
        raise OperationRecoveryError(
            "exact drain phase-one timeout authority is invalid"
        )
    phase_one_statement_timeout_seconds = getattr(
        adapter,
        "phase_one_statement_timeout_seconds",
        None,
    )
    if phase_one_statement_timeout_seconds is not None and (
        type(phase_one_statement_timeout_seconds) not in {int, float}
        or phase_one_statement_timeout_seconds <= 0
    ):
        raise OperationRecoveryError(
            "exact drain phase-one statement-timeout authority is invalid"
        )
    task_cancellation_timeout_seconds = max(
        EXACT_DRAIN_TASK_CANCELLATION_TIMEOUT_SECONDS,
        phase_one_statement_timeout_seconds or 0,
    )

    async def claim_tasks(_ops: Any, *args: Any, **kwargs: Any) -> Any:
        return await adapter.claim_tasks(*args, **kwargs)

    async def suppress_retain_folding(
        _poller: Any,
        *_args: Any,
        **_kwargs: Any,
    ) -> list[str]:
        """Keep every exact-drain execution bound to one approved row."""
        return []

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

    async def suppress_upstream_processing_reclaim(
        _poller: Any,
        _schema: str | None,
        *,
        operation_id: str | None = None,
    ) -> int:
        del operation_id
        return 0

    def claim_lifecycle_lock(poller: Any) -> asyncio.Lock:
        lock = getattr(poller, "_exact_drain_claim_lifecycle_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            poller._exact_drain_claim_lifecycle_lock = lock
        if not isinstance(lock, asyncio.Lock):
            raise OperationRecoveryError(
                "exact drain claim lifecycle seam is unavailable"
            )
        return lock

    async def release_exact_tasks(poller: Any) -> int:
        async with claim_lifecycle_lock(poller):
            if claim_release_disabled:
                raise OperationRecoveryError(
                    "exact drain claim release is disabled after failed quiescence"
                )
            active_tasks = getattr(poller, "_active_tasks", {})
            if not isinstance(active_tasks, Mapping) or any(
                not getattr(item, "bg_task", None).done()
                for item in active_tasks.values()
            ):
                raise OperationRecoveryError(
                    "exact drain claim release requires worker quiescence"
                )
            return await adapter.release_own_tasks(poller._backend)

    def request_exact_worker_shutdown(poller: Any) -> None:
        nonlocal claim_release_disabled, worker_shutdown_requested
        shutdown = getattr(poller, "_shutdown", None)
        shutdown_set = getattr(shutdown, "set", None)
        if not callable(shutdown_set) or not callable(
            request_worker_shutdown
        ):
            claim_release_disabled = True
            raise OperationRecoveryError(
                "exact drain worker shutdown seam is unavailable"
            )
        shutdown_set()
        if not worker_shutdown_requested:
            worker_shutdown_requested = True
            request_worker_shutdown()

    def record_runtime_failure(
        operation_id: str,
        *,
        stage: str,
        category: str,
        error: BaseException,
    ) -> None:
        recorder = getattr(adapter, "record_upstream_failure", None)
        if callable(recorder):
            recorder(
                operation_id,
                stage=stage,
                category=category,
                retryable=False,
                error_message=error,
            )
            return
        adapter.record_upstream_stage(operation_id, stage)

    async def execute_exact_task_inner(
        poller: Any,
        task: Any,
        holder: Any | None = None,
    ) -> Any:
        nonlocal claim_release_disabled
        execution = asyncio.create_task(
            upstream_execute_task_inner(poller, task, holder)
        )
        last_stage: str | None = None
        phase_one_deadline: float | None = None
        retry_deadline_message: str | None = None
        operation_attempt_deadline = (
            None
            if operation_attempt_timeout_seconds is None
            else time.monotonic() + operation_attempt_timeout_seconds
        )
        try:
            while True:
                stage = getattr(holder, "stage", None)
                now = time.monotonic()
                if (
                    operation_attempt_deadline is not None
                    and now >= operation_attempt_deadline
                ):
                    if retry_deadlines_after_quiescence:
                        retry_deadline_message = (
                            EXACT_DRAIN_OPERATION_ATTEMPT_TIMEOUT_MESSAGE
                        )
                        break
                    request_exact_worker_shutdown(poller)
                    raise OperationRecoveryError(
                        EXACT_DRAIN_OPERATION_ATTEMPT_TIMEOUT_MESSAGE
                    )
                if (
                    phase_one_timeout_seconds is not None
                    and isinstance(stage, str)
                    and stage.startswith("retain.phase1.")
                ):
                    if phase_one_deadline is None:
                        phase_one_deadline = (
                            now + phase_one_timeout_seconds
                        )
                elif not (
                    phase_one_deadline is not None
                    and isinstance(stage, str)
                    and stage.startswith(phase_one_nested_stage_prefixes)
                ):
                    phase_one_deadline = None
                if (
                    phase_one_deadline is not None
                    and now >= phase_one_deadline
                ):
                    if retry_deadlines_after_quiescence:
                        retry_deadline_message = (
                            EXACT_DRAIN_PHASE_ONE_DEADLINE_TIMEOUT_MESSAGE
                        )
                        break
                    request_exact_worker_shutdown(poller)
                    raise OperationRecoveryError(
                        EXACT_DRAIN_PHASE_ONE_DEADLINE_TIMEOUT_MESSAGE
                    )
                if execution.done():
                    if isinstance(stage, str) and stage != last_stage:
                        try:
                            adapter.record_upstream_stage(
                                task.operation_id,
                                stage,
                            )
                        except BaseException:
                            request_exact_worker_shutdown(poller)
                            raise
                    return await execution
                if isinstance(stage, str) and stage != last_stage:
                    try:
                        adapter.record_upstream_stage(
                            task.operation_id,
                            stage,
                        )
                    except BaseException:
                        request_exact_worker_shutdown(poller)
                        raise
                    last_stage = stage
                active_deadlines = tuple(
                    deadline
                    for deadline in (
                        operation_attempt_deadline,
                        phase_one_deadline,
                    )
                    if deadline is not None
                )
                wait_timeout = 0.25
                if active_deadlines:
                    wait_timeout = min(
                        wait_timeout,
                        max(0.0, min(active_deadlines) - now),
                    )
                done, _pending = await asyncio.wait(
                    {execution},
                    timeout=wait_timeout,
                )
                if done:
                    stage = getattr(holder, "stage", None)
                    if isinstance(stage, str) and stage != last_stage:
                        try:
                            adapter.record_upstream_stage(
                                task.operation_id,
                                stage,
                            )
                        except BaseException:
                            request_exact_worker_shutdown(poller)
                            raise
                    return await execution
        except asyncio.CancelledError:
            raise
        except BaseException:
            request_exact_worker_shutdown(poller)
            raise
        finally:
            if not execution.done():
                execution.cancel()
                nonquiescence_error: OperationRecoveryError | None = None
                try:
                    cancellation_timeout_seconds = (
                        EXACT_DRAIN_TASK_CANCELLATION_TIMEOUT_SECONDS
                    )
                    stage = getattr(holder, "stage", None)
                    if (
                        phase_one_statement_timeout_seconds is not None
                        and isinstance(stage, str)
                        and stage.startswith("retain.phase1.")
                    ):
                        cancellation_timeout_seconds = (
                            task_cancellation_timeout_seconds
                        )
                    done, _pending = await asyncio.wait(
                        {execution},
                        timeout=cancellation_timeout_seconds,
                    )
                    if not done:
                        claim_release_disabled = True
                        nonquiescence_error = OperationRecoveryError(
                            "exact drain task did not quiesce after cancellation"
                        )
                        try:
                            record_runtime_failure(
                                task.operation_id,
                                stage="failure.nonquiescent",
                                category="nonquiescent_shutdown",
                                error=nonquiescence_error,
                            )
                        except BaseException as evidence_error:
                            LOGGER.warning(
                                "exact drain nonquiescent evidence failed with %s",
                                type(evidence_error).__name__,
                            )
                        try:
                            request_exact_worker_shutdown(poller)
                        except BaseException as shutdown_error:
                            raise nonquiescence_error from shutdown_error
                        raise nonquiescence_error
                    await execution
                except asyncio.CancelledError:
                    current = asyncio.current_task()
                    if current is not None and current.cancelling():
                        raise
                    LOGGER.warning(
                        "exact drain child task cancelled during cleanup"
                    )
                except BaseException as error:
                    if error is nonquiescence_error or (
                        retry_deadline_message is None
                        and isinstance(error, OperationRecoveryError)
                    ):
                        raise
                    LOGGER.warning(
                        "exact drain task cleanup ended with %s",
                        type(error).__name__,
                    )
        if retry_deadline_message is not None:
            try:
                await adapter.schedule_retry(
                    poller._backend,
                    task.operation_id,
                    datetime.now(timezone.utc),
                    retry_deadline_message,
                    getattr(task, "schema", None),
                )
            except BaseException as error:
                try:
                    record_runtime_failure(
                        task.operation_id,
                        stage="failure.retry-persistence",
                        category="terminal_state_persistence",
                        error=error,
                    )
                finally:
                    request_exact_worker_shutdown(poller)
                raise
            return None

    async def claim_exact_batch(
        poller: Any,
        schema: str | None,
        reserved_limits: Mapping[str, int],
        shared_limit: int,
    ) -> Any:
        async with claim_lifecycle_lock(poller):
            shutdown = getattr(poller, "_shutdown", None)
            shutdown_is_set = getattr(shutdown, "is_set", None)
            if callable(shutdown_is_set) and shutdown_is_set():
                return []
            tasks = await upstream_claim_batch_inner(
                poller,
                schema,
                reserved_limits,
                shared_limit,
            )
            try:
                adapter.claim_committed(tasks)
            except Exception:
                shutdown_set = getattr(shutdown, "set", None)
                if callable(shutdown_set):
                    shutdown_set()
                adapter.abort_after_committed_claim_failure()
                raise
            if callable(shutdown_is_set) and shutdown_is_set():
                return []
            return tasks

    async def claim_exact_batch_public(
        poller: Any,
        schema: str | None,
        reserved_limits: Mapping[str, int],
        shared_limit: int,
    ) -> Any:
        """Do not convert a committed-claim evidence failure into an empty poll."""
        return await poller._claim_batch_for_schema_inner(
            schema,
            reserved_limits,
            shared_limit,
        )

    def record_exact_task_error(
        poller: Any,
        error: BaseException,
    ) -> None:
        errors = getattr(poller, "_exact_drain_task_errors", None)
        if errors is None:
            errors = []
            poller._exact_drain_task_errors = errors
        errors.append(error)

    async def run_exact_worker(poller: Any) -> Any:
        try:
            reserve_control_connection = getattr(
                adapter,
                "reserve_control_connection",
                None,
            )
            if not callable(reserve_control_connection):
                raise OperationRecoveryError(
                    "exact drain control connection seam is unavailable"
                )
            await reserve_control_connection(
                getattr(poller, "_backend", None)
            )
            if records_worker_lifecycle:
                adapter.record_worker_stage(
                    status="running",
                    stage="worker.poller.running",
                )
            result = await upstream_run(poller)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - exact worker boundary
            poller._exact_drain_poller_error = error
            request_exact_worker_shutdown(poller)
            return None
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            return result
        shutdown = getattr(poller, "_shutdown", None)
        shutdown_is_set = getattr(shutdown, "is_set", None)
        if not callable(shutdown_is_set) or not shutdown_is_set():
            poller._exact_drain_poller_error = OperationRecoveryError(
                "exact drain worker poller stopped unexpectedly"
            )
            request_exact_worker_shutdown(poller)
            return None
        return result

    def consume_exact_task_result(poller: Any, task: asyncio.Task[Any]) -> None:
        consumed = getattr(poller, "_exact_drain_consumed_tasks", None)
        if consumed is None:
            consumed = set()
            poller._exact_drain_consumed_tasks = consumed
        if task in consumed or not task.done():
            return
        consumed.add(task)
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is None:
            return
        record_exact_task_error(poller, error)

    async def cleanup_exact_task(
        poller: Any,
        operation_id: str,
        operation_type: str,
    ) -> None:
        active_tasks = getattr(poller, "_active_tasks", {})
        info = active_tasks.get(operation_id)
        task = getattr(info, "bg_task", None)
        if isinstance(task, asyncio.Task):
            consume_exact_task_result(poller, task)
        await upstream_cleanup_task(poller, operation_id, operation_type)

    async def shutdown_exact_worker(
        poller: Any,
        timeout: float = 30.0,
    ) -> None:
        nonlocal claim_release_disabled
        if not callable(upstream_shutdown_graceful):
            raise OperationRecoveryError(
                "exact drain worker graceful shutdown seam is unavailable"
            )
        active_tasks = getattr(poller, "_active_tasks", {})
        observed_tasks = [
            info.bg_task
            for info in active_tasks.values()
            if isinstance(getattr(info, "bg_task", None), asyncio.Task)
        ]
        if not worker_shutdown_requested:
            shutdown = getattr(poller, "_shutdown", None)
            shutdown_set = getattr(shutdown, "set", None)
            if not callable(shutdown_set):
                claim_release_disabled = True
                raise OperationRecoveryError(
                    "exact drain worker shutdown seam is unavailable"
                )
            shutdown_set()
            for task in observed_tasks:
                if not task.done():
                    task.cancel()
        upstream_error: BaseException | None = None
        try:
            graceful_timeout = timeout
            if observed_tasks:
                graceful_timeout = max(
                    timeout,
                    task_cancellation_timeout_seconds + 0.5,
                )
            await upstream_shutdown_graceful(
                poller,
                timeout=graceful_timeout,
            )
        except BaseException as error:
            upstream_error = error
        control_error: BaseException | None = None
        close_control_connection = getattr(
            adapter,
            "close_control_connection",
            None,
        )
        if not callable(close_control_connection):
            control_error = OperationRecoveryError(
                "exact drain control connection seam is unavailable"
            )
        else:
            try:
                await close_control_connection()
            except BaseException as error:
                control_error = error
        for task in observed_tasks:
            consume_exact_task_result(poller, task)
        await asyncio.sleep(0)
        poller_error = getattr(poller, "_exact_drain_poller_error", None)
        primary_error: BaseException | None = None
        if isinstance(poller_error, BaseException):
            if upstream_error is not None:
                poller_error.__cause__ = upstream_error
                poller_error.__suppress_context__ = True
            primary_error = poller_error
        elif upstream_error is not None:
            primary_error = upstream_error
        else:
            task_errors = getattr(poller, "_exact_drain_task_errors", ())
            if task_errors:
                primary_error = task_errors[0]
        if primary_error is not None and control_error is not None:
            raise BaseExceptionGroup(
                "exact drain worker shutdown failed",
                [primary_error, control_error],
            )
        if primary_error is not None:
            raise primary_error
        if control_error is not None:
            raise control_error

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
        try:
            if _exact_drain_error_is_transient(error_message):
                await adapter.schedule_retry(
                    poller._backend,
                    operation_id,
                    datetime.now(timezone.utc),
                    error_message,
                    schema,
                )
            else:
                await adapter.mark_failed(
                    poller._backend,
                    operation_id,
                    error_message,
                    schema,
                )
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            try:
                record_runtime_failure(
                    operation_id,
                    stage="failure.terminal-state",
                    category="terminal_state_persistence",
                    error=error,
                )
            except BaseException as evidence_error:
                LOGGER.warning(
                    "exact drain terminal failure evidence failed with %s",
                    type(evidence_error).__name__,
                )
            record_exact_task_error(poller, error)
            request_exact_worker_shutdown(poller)
            raise

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
    worker_poller_type.run = run_exact_worker
    worker_poller_type._scan_active_schemas = scan_active_schemas
    worker_poller_type.recover_own_tasks = recover_exact_tasks
    worker_poller_type.release_own_tasks = release_exact_tasks
    worker_poller_type._reclaim_own_processing_tasks = (
        suppress_upstream_processing_reclaim
    )
    worker_poller_type._execute_task_inner = execute_exact_task_inner
    worker_poller_type._claim_batch_for_schema_inner = claim_exact_batch
    worker_poller_type._claim_batch_for_schema = claim_exact_batch_public
    if callable(upstream_fold_retain_peers):
        worker_poller_type._fold_retain_peers = suppress_retain_folding
    if callable(upstream_cleanup_task):
        worker_poller_type._cleanup_task = cleanup_exact_task
    worker_poller_type.shutdown_graceful = shutdown_exact_worker
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
    if records_worker_lifecycle:
        memory_engine_type.initialize = initialize_exact_memory


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


def _exact_drain_dependency_entries(root: Path) -> list[Path]:
    pending = [root]
    entries: list[Path] = []
    while pending:
        directory = pending.pop()
        _exact_drain_trusted_directory(
            directory,
            "exact drain worker dependency directory",
        )
        try:
            children = sorted(
                (Path(entry.path) for entry in os.scandir(directory)),
                key=lambda path: path.name,
            )
        except OSError as error:
            raise OperationRecoveryError(
                "exact drain worker dependency closure is unavailable"
            ) from error
        for path in children:
            if len(entries) >= EXACT_DRAIN_MAX_DEPENDENCY_ENTRIES:
                raise OperationRecoveryError(
                    "exact drain worker dependency closure has too many entries"
                )
            try:
                metadata = path.lstat()
            except OSError as error:
                raise OperationRecoveryError(
                    "exact drain worker dependency closure is unavailable"
                ) from error
            if stat.S_ISLNK(metadata.st_mode):
                raise OperationRecoveryError(
                    "exact drain worker dependency closure contains a symlink"
                )
            if stat.S_ISDIR(metadata.st_mode):
                pending.append(path)
            elif not stat.S_ISREG(metadata.st_mode):
                raise OperationRecoveryError(
                    "exact drain worker dependency closure contains an "
                    "unsupported entry"
                )
            entries.append(path)
    return sorted(entries, key=lambda path: path.relative_to(root).as_posix())


def exact_drain_dependency_manifest(
    worker_runtime: str | Path,
) -> dict[str, Any]:
    """Stream-hash the complete external worker dependency authority."""
    root = exact_drain_worker_site_packages_path(worker_runtime)
    entries = _exact_drain_dependency_entries(root)
    hasher = hashlib.sha256()
    total_bytes = 0
    file_count = 0
    directory_count = 0
    for path in entries:
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISDIR(metadata.st_mode):
            _exact_drain_trusted_directory(
                path,
                f"exact drain worker dependency directory {relative}",
            )
            entry = {
                "kind": "directory",
                "mode": mode,
                "path": relative,
            }
            directory_count += 1
        elif stat.S_ISREG(metadata.st_mode):
            remaining = EXACT_DRAIN_MAX_DEPENDENCY_TOTAL_BYTES - total_bytes
            artifact_digest, artifact_size = (
                _exact_drain_file_digest_evidence(
                    path,
                    f"exact drain worker dependency artifact {relative}",
                    max_bytes=min(
                        EXACT_DRAIN_MAX_DEPENDENCY_FILE_BYTES,
                        remaining,
                    ),
                )
            )
            total_bytes += artifact_size
            entry = {
                "kind": "file",
                "mode": mode,
                "path": relative,
                "sha256": artifact_digest,
                "size": artifact_size,
            }
            file_count += 1
        else:
            raise OperationRecoveryError(
                "exact drain worker dependency closure contains an "
                "unsupported entry"
            )
        hasher.update(
            json.dumps(
                entry,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        hasher.update(b"\n")
    if file_count == 0:
        raise OperationRecoveryError(
            "exact drain worker dependency closure is unavailable"
        )
    if _exact_drain_dependency_entries(root) != entries:
        raise OperationRecoveryError(
            "exact drain worker dependency closure changed while reading"
        )
    return {
        "schema_version": 1,
        "root": str(root),
        "entry_count": len(entries),
        "file_count": file_count,
        "directory_count": directory_count,
        "total_bytes": total_bytes,
        "entries_digest": hasher.hexdigest(),
    }


def exact_drain_candidate_provider_root(
    candidate_library: str | Path,
) -> Path:
    root = (
        Path(candidate_library)
        / EXACT_DRAIN_CANDIDATE_RUNTIME_DIRECTORY
        / "provider"
    )
    if not root.is_absolute():
        raise OperationRecoveryError(
            "exact drain candidate provider runtime is unavailable"
        )
    return root


def _write_exact_drain_snapshot_file(path: Path, body: bytes) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        position = 0
        while position < len(body):
            position += os.write(descriptor, body[position:])
        os.fsync(descriptor)
    except OSError as error:
        raise OperationRecoveryError(
            "exact drain candidate runtime snapshot is unavailable"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _replace_exact_drain_source_fragment(
    source: str,
    old: str,
    new: str,
    label: str,
) -> str:
    if source.count(old) != 1:
        raise OperationRecoveryError(
            f"exact drain candidate {label} source differs"
        )
    return source.replace(old, new)


def _patch_exact_drain_entity_resolver(
    source: bytes,
    *,
    legacy_entity_schema: bool = False,
    candidate_query_batch_size: int | None = None,
    cooccurrence_query_batch_size: int | None = None,
    full_strategy_repair: bool = False,
) -> bytes:
    """Return the exact bounded Phase-1 resolver overlay for Hindsight 0.9."""
    if (
        EXACT_DRAIN_PHASE_ONE_STATEMENT_TIMEOUT_SECONDS != 120
        or EXACT_DRAIN_PHASE_ONE_CLIENT_TIMEOUT_SECONDS != 125
    ):
        raise OperationRecoveryError(
            "exact drain phase-one statement timeout authority differs"
        )
    if candidate_query_batch_size not in {
        None,
        EXACT_DRAIN_FUZZY_QUERY_BATCH_SIZE,
    }:
        raise OperationRecoveryError(
            "exact drain candidate query batch authority differs"
        )
    if cooccurrence_query_batch_size not in {
        None,
        EXACT_DRAIN_COOCCURRENCE_QUERY_BATCH_SIZE,
    }:
        raise OperationRecoveryError(
            "exact drain cooccurrence query batch authority differs"
        )
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as error:
        raise OperationRecoveryError(
            "exact drain candidate entity resolver source differs"
        ) from error
    start = "    async def _resolve_entities_batch_trigram("
    end = "    async def _resolve_entities_batch_oracle_fuzzy("
    if text.count(start) != 1 or text.count(end) != 1:
        raise OperationRecoveryError(
            "exact drain candidate entity resolver source differs"
        )
    prefix, remainder = text.split(start, 1)
    if full_strategy_repair:
        full_start = "    async def _resolve_entities_batch_full("
        if prefix.count(full_start) != 1:
            raise OperationRecoveryError(
                "exact drain candidate full entity resolver source differs"
            )
        before_full, full = prefix.split(full_start, 1)
        full = full_start + full
        full = _replace_exact_drain_source_fragment(
            full,
            '''        """Original strategy: load all bank entities then match in Python."""
        # Query ALL candidates for this bank
''',
            '''        """Original strategy: load all bank entities then match in Python."""

        async def _bounded_phase1_fetch(stage: str, *arguments):
            set_stage(stage)
            async with conn.transaction():
                await conn.execute("SET TRANSACTION READ ONLY")
                await conn.execute(
                    "SET LOCAL statement_timeout = '120s'"
                )
                try:
                    return await conn.fetch(*arguments, timeout=125.0)
                except Exception as error:
                    if (
                        isinstance(error, TimeoutError)
                        or getattr(error, "sqlstate", None) == "57014"
                        or "statement timeout" in str(error).casefold()
                    ):
                        raise TimeoutError(
                            "operation-recovery exact drain phase-one query "
                            f"timed out at {stage}"
                        ) from error
                    raise

        # Query ALL candidates for this bank
''',
            "full entity resolver deadline",
        )
        full = _replace_exact_drain_source_fragment(
            full,
            "        all_entities = await conn.fetch(\n",
            "        all_entities = await _bounded_phase1_fetch(\n"
            '            "retain.phase1.candidates.full",\n',
            "full candidate query",
        )
        full = _replace_exact_drain_source_fragment(
            full,
            "SELECT canonical_name, id, metadata, last_seen, mention_count",
            "SELECT id, canonical_name, last_seen, mention_count",
            "full candidate projection",
        )
        full = _replace_exact_drain_source_fragment(
            full,
            "        all_cooccurrences = await conn.fetch(\n",
            "        all_cooccurrences = await _bounded_phase1_fetch(\n"
            '            "retain.phase1.cooccurrence.full",\n',
            "full cooccurrence query",
        )
        full = _replace_exact_drain_source_fragment(
            full,
            "WHERE ec.entity_id_1 IN (SELECT id FROM {fq_table(\"entities\")} WHERE bank_id = $1)\n"
            "               OR ec.entity_id_2 IN (SELECT id FROM {fq_table(\"entities\")} WHERE bank_id = $1)",
            "WHERE ec.entity_id_1 IN (SELECT id FROM {fq_table(\"entities\")} WHERE bank_id = $1)\n"
            "              AND ec.entity_id_2 IN (SELECT id FROM {fq_table(\"entities\")} WHERE bank_id = $1)",
            "full cooccurrence predicate",
        )
        full = _replace_exact_drain_source_fragment(
            full,
            '''                metadata = row["metadata"]
                last_seen = row["last_seen"]
                mention_count = row["mention_count"]
''',
            '''                last_seen = row["last_seen"]
                mention_count = row["mention_count"]
''',
            "full candidate fields",
        )
        full = _replace_exact_drain_source_fragment(
            full,
            "matching.append((ent_id, canonical_name, metadata, last_seen, mention_count))",
            "matching.append((ent_id, canonical_name, None, last_seen, mention_count))",
            "full candidate tuple",
        )
        full = _replace_exact_drain_source_fragment(
            full,
            "        return await self._resolve_from_candidates(\n",
            '        set_stage("retain.phase1.scoring")\n'
            "        return await self._resolve_from_candidates(\n",
            "full candidate scoring breadcrumb",
        )
        prefix = before_full + full
    trigram, suffix = remainder.split(end, 1)
    trigram = start + trigram
    trigram = _replace_exact_drain_source_fragment(
        trigram,
        """        Reduces DB data transfer from 165K rows to ~5-20 rows per entity.
        \"\"\"
        entity_texts = list(set(e[\"text\"] for e in entities_data))
""",
        """        Reduces DB data transfer from 165K rows to ~5-20 rows per entity.
        \"\"\"

        async def _bounded_phase1_fetch(stage: str, *arguments):
            set_stage(stage)
            async with conn.transaction():
                await conn.execute("SET TRANSACTION READ ONLY")
                await conn.execute(
                    \"SET LOCAL statement_timeout = '120s'\"
                )
                try:
                    return await conn.fetch(*arguments, timeout=125.0)
                except Exception as error:
                    if (
                        isinstance(error, TimeoutError)
                        or getattr(error, \"sqlstate\", None) == \"57014\"
                        or \"statement timeout\" in str(error).casefold()
                    ):
                        raise TimeoutError(
                            \"operation-recovery exact drain phase-one query \"
                            f\"timed out at {stage}\"
                        ) from error
                    raise

        entity_texts = list(set(e[\"text\"] for e in entities_data))
""",
        "entity resolver deadline",
    )
    trigram = _replace_exact_drain_source_fragment(
        trigram,
        "SELECT e.id, e.canonical_name, e.metadata, e.last_seen, e.mention_count,\n"
        "                           q.query_text",
        "SELECT e.id, e.canonical_name, e.last_seen, q.query_text",
        "exact candidate projection",
    )
    trigram = _replace_exact_drain_source_fragment(
        trigram,
        "SELECT c.id, c.canonical_name, c.metadata, c.last_seen, c.mention_count,\n"
        "                           q.query_text",
        "SELECT c.id, c.canonical_name, c.last_seen, q.query_text",
        "fuzzy candidate projection",
    )
    trigram = _replace_exact_drain_source_fragment(
        trigram,
        "SELECT e.id, e.canonical_name, e.metadata, e.last_seen, e.mention_count\n"
        "                        FROM",
        "SELECT e.id, e.canonical_name, e.last_seen\n"
        "                        FROM",
        "fuzzy candidate lateral projection",
    )
    if legacy_entity_schema:
        trigram = _replace_exact_drain_source_fragment(
            trigram,
            "                          AND e.entity_kind != 'label'\n",
            "",
            "legacy entity schema fuzzy predicate",
        )
    trigram = _replace_exact_drain_source_fragment(
        trigram,
        """        for entity_text_batch in self._chunked(label_texts, self.entity_resolution_batch_size):
""",
        """        label_batch_count = (
            len(label_texts) + self.entity_resolution_batch_size - 1
        ) // self.entity_resolution_batch_size
        for label_batch_index, entity_text_batch in enumerate(
            self._chunked(label_texts, self.entity_resolution_batch_size),
            start=1,
        ):
""",
        "exact candidate batch",
    )
    fuzzy_batch_replacement = (
        """        fuzzy_batch_count = (
            len(fuzzy_texts) + self.entity_resolution_batch_size - 1
        ) // self.entity_resolution_batch_size
        for fuzzy_batch_index, entity_text_batch in enumerate(
            self._chunked(fuzzy_texts, self.entity_resolution_batch_size),
            start=1,
        ):
"""
        if candidate_query_batch_size is None
        else f"""        fuzzy_query_batch_size = {candidate_query_batch_size}
        fuzzy_batch_count = (
            len(fuzzy_texts) + fuzzy_query_batch_size - 1
        ) // fuzzy_query_batch_size
        for fuzzy_batch_index, entity_text_batch in enumerate(
            self._chunked(fuzzy_texts, fuzzy_query_batch_size),
            start=1,
        ):
"""
    )
    trigram = _replace_exact_drain_source_fragment(
        trigram,
        """        for entity_text_batch in self._chunked(fuzzy_texts, self.entity_resolution_batch_size):
""",
        fuzzy_batch_replacement,
        "fuzzy candidate batch",
    )
    if trigram.count("await conn.fetch(\n") != 3:
        raise OperationRecoveryError(
            "exact drain candidate entity resolver query source differs"
        )
    trigram = trigram.replace(
        "await conn.fetch(\n",
        "await _bounded_phase1_fetch(\n"
        "                    f\"retain.phase1.candidates.exact.\"\n"
        "                    f\"{label_batch_index}/{label_batch_count}\",\n",
        1,
    )
    trigram = trigram.replace(
        "await conn.fetch(\n",
        "await _bounded_phase1_fetch(\n"
        "                    f\"retain.phase1.candidates.fuzzy.\"\n"
        "                    f\"{fuzzy_batch_index}/{fuzzy_batch_count}\",\n",
        1,
    )
    trigram = trigram.replace(
        "await conn.fetch(\n",
        "await _bounded_phase1_fetch(\n"
        "                \"retain.phase1.cooccurrence\",\n",
        1,
    )
    trigram = _replace_exact_drain_source_fragment(
        trigram,
        "(row[\"id\"], row[\"canonical_name\"], row[\"metadata\"], "
        "row[\"last_seen\"], row[\"mention_count\"])",
        "(row[\"id\"], row[\"canonical_name\"], row[\"last_seen\"])",
        "candidate tuple",
    )
    trigram = _replace_exact_drain_source_fragment(
        trigram,
        "WHERE ec.entity_id_1 = ANY($1::uuid[])\n"
        "                   OR ec.entity_id_2 = ANY($1::uuid[])",
        "WHERE ec.entity_id_1 = ANY($1::uuid[])\n"
        "                   AND ec.entity_id_2 = ANY($1::uuid[])",
        "cooccurrence predicate",
    )
    if cooccurrence_query_batch_size is not None:
        trigram = _replace_exact_drain_source_fragment(
            trigram,
            '''            candidate_id_list = list(candidate_ids)
            cooc_rows = await _bounded_phase1_fetch(
                "retain.phase1.cooccurrence",
                f"""
                SELECT ec.entity_id_1, ec.entity_id_2
                FROM {fq_table("entity_cooccurrences")} ec
                WHERE ec.entity_id_1 = ANY($1::uuid[])
                   AND ec.entity_id_2 = ANY($1::uuid[])
                """,
                candidate_id_list,
            )
''',
            f'''            candidate_id_list = sorted(candidate_ids, key=str)
            cooccurrence_batch_size = {cooccurrence_query_batch_size}
            cooccurrence_batch_count = (
                len(candidate_id_list) + cooccurrence_batch_size - 1
            ) // cooccurrence_batch_size
            cooc_rows = []
            for cooccurrence_batch_index, candidate_id_batch in enumerate(
                self._chunked(candidate_id_list, cooccurrence_batch_size),
                start=1,
            ):
                cooc_rows.extend(
                    await _bounded_phase1_fetch(
                        f"retain.phase1.cooccurrence."
                        f"{{cooccurrence_batch_index}}/{{cooccurrence_batch_count}}",
                        f"""
                        SELECT ec.entity_id_1, ec.entity_id_2
                        FROM {{fq_table("entity_cooccurrences")}} ec
                        WHERE ec.entity_id_1 = ANY($1::uuid[])
                           AND ec.entity_id_2 = ANY($2::uuid[])
                        """,
                        candidate_id_batch,
                        candidate_id_list,
                    )
                )
''',
            "cooccurrence query batch",
        )
    trigram = _replace_exact_drain_source_fragment(
        trigram,
        "        return await self._resolve_from_candidates(\n",
        "        set_stage(\"retain.phase1.scoring\")\n"
        "        return await self._resolve_from_candidates(\n",
        "candidate scoring breadcrumb",
    )
    text = prefix + trigram + end + suffix
    text = _replace_exact_drain_source_fragment(
        text,
        "from .retain.types import ResolvedEntity\n",
        "from ..worker.stage import set_stage\n"
        "from .retain.types import ResolvedEntity\n",
        "stage import",
    )
    if full_strategy_repair:
        text = _replace_exact_drain_source_fragment(
            text,
            "_INTRABATCH_MAX_NAMES = 250\n",
            "_INTRABATCH_MAX_NAMES = 250\n"
            "_INTRABATCH_MAX_NAME_CODEPOINTS = 4096\n"
            "_INTRABATCH_MAX_TOTAL_CODEPOINTS = 65536\n",
            "intrabatch input bounds",
        )
        text = _replace_exact_drain_source_fragment(
            text,
            "        pairs = _find_intrabatch_similar_pairs(list(rep_by_lower.values()), self._intrabatch_merge_similarity)\n"
            "        if not pairs:\n",
            "        eligible_names = [\n"
            "            name\n"
            "            for name in rep_by_lower.values()\n"
            "            if len(name) <= _INTRABATCH_MAX_NAME_CODEPOINTS\n"
            "        ]\n"
            "        skipped_name_count = len(rep_by_lower) - len(eligible_names)\n"
            "        if skipped_name_count:\n"
            "            logger.warning(\n"
            "                \"Skipping fuzzy in-batch entity dedup for %d names \"\n"
            "                \"that exceed the %d-code-point bound.\",\n"
            "                skipped_name_count,\n"
            "                _INTRABATCH_MAX_NAME_CODEPOINTS,\n"
            "            )\n"
            "        if len(eligible_names) < 2:\n"
            "            return {}\n"
            "        total_codepoints = sum(map(len, eligible_names))\n"
            "        if total_codepoints > _INTRABATCH_MAX_TOTAL_CODEPOINTS:\n"
            "            logger.warning(\n"
            "                \"Skipping fuzzy in-batch entity dedup: %d eligible \"\n"
            "                \"names exceed the %d-code-point batch bound.\",\n"
            "                len(eligible_names),\n"
            "                _INTRABATCH_MAX_TOTAL_CODEPOINTS,\n"
            "            )\n"
            "            return {}\n"
            "        pairs = _find_intrabatch_similar_pairs(\n"
            "            eligible_names, self._intrabatch_merge_similarity\n"
            "        )\n"
            "        if not pairs:\n",
            "intrabatch trigram bounds",
        )
    text = _replace_exact_drain_source_fragment(
        text,
        """        candidate[1],
    )


class EntityResolver:
""",
        """        candidate[1],
    )


def _resolution_candidate_fields(candidate: tuple) -> tuple[Any, str, datetime | None]:
    if len(candidate) == 3:
        return candidate
    if len(candidate) == 5:
        return candidate[0], candidate[1], candidate[3]
    raise RuntimeError(\"Entity resolution candidate shape is invalid\")


class EntityResolver:
""",
        "candidate normalization",
    )
    text = _replace_exact_drain_source_fragment(
        text,
        """                for candidate_id, canonical_name, metadata, last_seen, mention_count in candidates:
                    if canonical_name.lower() == entity_text_lower:
""",
        """                for candidate in candidates:
                    candidate_id, canonical_name, last_seen = _resolution_candidate_fields(candidate)
                    if canonical_name.lower() == entity_text_lower:
""",
        "label candidate iteration",
    )
    text = _replace_exact_drain_source_fragment(
        text,
        """            for candidate_id, canonical_name, metadata, last_seen, mention_count in candidates:
                # Hand the loop back periodically so /health (and every other task
""",
        """            for candidate in candidates:
                candidate_id, canonical_name, last_seen = _resolution_candidate_fields(candidate)
                # Hand the loop back periodically so /health (and every other task
""",
        "scoring candidate iteration",
    )
    try:
        compile(text, "hindsight_api/engine/entity_resolver.py", "exec")
    except SyntaxError as error:
        raise OperationRecoveryError(
            "exact drain patched entity resolver source is invalid"
        ) from error
    return text.encode("utf-8")


def _patch_exact_drain_postgresql_ops(source: bytes) -> bytes:
    """Keep candidate entity writes compatible with the bound legacy schema."""
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as error:
        raise OperationRecoveryError(
            "exact drain candidate PostgreSQL ops source differs"
        ) from error
    text = _replace_exact_drain_source_fragment(
        text,
        "INSERT INTO {table} (bank_id, canonical_name, first_seen, "
        "last_seen, mention_count, entity_kind)\n"
        "            SELECT $1, name, COALESCE(event_date, now()), "
        "COALESCE(event_date, now()), 0, kind",
        "INSERT INTO {table} (bank_id, canonical_name, first_seen, "
        "last_seen, mention_count)\n"
        "            SELECT $1, name, COALESCE(event_date, now()), "
        "COALESCE(event_date, now()), 0",
        "legacy entity insert",
    )
    text = _replace_exact_drain_source_fragment(
        text,
        "INSERT INTO {table} (id, bank_id, canonical_name, entity_kind)\n"
        "            SELECT t.entity_id, $1, t.canonical_name, "
        "t.entity_kind",
        "INSERT INTO {table} (id, bank_id, canonical_name)\n"
        "            SELECT t.entity_id, $1, t.canonical_name",
        "legacy entity reassertion",
    )
    try:
        compile(text, "hindsight_api/engine/db/ops_postgresql.py", "exec")
    except SyntaxError as error:
        raise OperationRecoveryError(
            "exact drain patched PostgreSQL ops source is invalid"
        ) from error
    return text.encode("utf-8")


def _patch_exact_drain_memory_engine(
    source: bytes,
    *,
    idempotent_missing_mental_model: bool = False,
) -> bytes:
    """Apply exact-drain task failure and idempotency repairs."""
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as error:
        raise OperationRecoveryError(
            "exact drain candidate memory engine source differs"
        ) from error
    native_typed_errors = (
        text.count(
            "from ..worker.exceptions import DeferOperation, RetryTaskAt, "
            "format_task_error"
        )
        == 1
        and text.count("error_message = format_task_error(e)") == 1
        and text.count(
            "await self._mark_operation_failed(operation_id, "
            "error_message, error_traceback)"
        )
        == 2
        and text.count("message=error_message,") >= 2
    )
    typed_error_helper = (
        ""
        if native_typed_errors
        else (
            "def _operation_recovery_task_error_message(e: Exception) -> str:\n"
            "    message = str(e)\n"
            "    name = type(e).__name__\n"
            "    return f\"{name}: {message}\" if message else name\n\n\n"
        )
    )
    text = _replace_exact_drain_source_fragment(
        text,
        "def _is_non_retryable_task_error(e: Exception) -> bool:\n"
        "    \"\"\"Classify deterministic task failures that should skip worker retry.\"\"\"\n"
        "    return (\n"
        "        isinstance(e, asyncpg.exceptions.IntegrityConstraintViolationError)\n",
        typed_error_helper
        + "def _operation_recovery_http_status(e: Exception) -> int | None:\n"
        "    seen = set()\n"
        "    candidate = e\n"
        "    while candidate is not None and id(candidate) not in seen:\n"
        "        seen.add(id(candidate))\n"
        "        response = getattr(candidate, 'response', None)\n"
        "        status = getattr(response, 'status_code', None)\n"
        "        if not isinstance(status, int):\n"
        "            status = getattr(candidate, 'status_code', None)\n"
        "        if isinstance(status, int):\n"
        "            return status\n"
        "        candidate = candidate.__cause__ or candidate.__context__\n"
        "    return None\n\n\n"
        "def _is_non_retryable_task_error(e: Exception) -> bool:\n"
        "    \"\"\"Classify deterministic task failures that should skip worker retry.\"\"\"\n"
        "    return (\n"
        "        _operation_recovery_http_status(e) == 400\n"
        "        or isinstance(e, asyncpg.exceptions.IntegrityConstraintViolationError)\n",
        "typed deterministic task error",
    )
    if not native_typed_errors:
        for old, new, label in (
            (
                "await self._mark_operation_failed(operation_id, str(e), error_traceback)",
                (
                    "await self._mark_operation_failed(\n"
                    "                            operation_id,\n"
                    "                            _operation_recovery_task_error_message(e),\n"
                    "                            error_traceback,\n"
                    "                        )"
                ),
                "typed task terminal error",
            ),
            (
                "                            message=str(e),",
                "                            message=_operation_recovery_task_error_message(e),",
                "typed task retry error",
            ),
        ):
            expected_count = 2
            if text.count(old) != expected_count:
                raise OperationRecoveryError(
                    f"exact drain candidate {label} source differs"
                )
            text = text.replace(old, new)
    if idempotent_missing_mental_model:
        text = _replace_exact_drain_source_fragment(
            text,
            "        if refreshed is None:\n"
            "            raise ValueError(f\"Mental model {mental_model_id} not found in bank {bank_id}\")\n",
            "        if refreshed is None:\n"
            "            logger.info(\n"
            "                f\"[REFRESH_MENTAL_MODEL_TASK] Mental model {mental_model_id} \"\n"
            "                f\"in bank {bank_id} target is already absent; completing idempotently\"\n"
            "            )\n"
            "            return\n",
            "idempotent missing mental model refresh",
        )
    try:
        compile(text, "hindsight_api/engine/memory_engine.py", "exec")
    except SyntaxError as error:
        raise OperationRecoveryError(
            "exact drain patched memory engine source is invalid"
        ) from error
    return text.encode("utf-8")


def _patch_exact_drain_poller(source: bytes) -> bytes:
    """Keep exception type when the upstream poller records a final failure."""
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as error:
        raise OperationRecoveryError(
            "exact drain candidate poller source differs"
        ) from error
    native_typed_errors = (
        text.count(
            "from .exceptions import DeferOperation, RetryTaskAt, "
            "format_task_error"
        )
        == 1
        and text.count("error_message = format_task_error(e)") == 1
        and text.count("await self._mark_all_failed(task, error_message)") == 1
    )
    if native_typed_errors:
        text = _replace_exact_drain_source_fragment(
            text,
            "await self._schedule_retry_all(task, e.retry_at, str(e))",
            "await self._schedule_retry_all(\n"
            "                task, e.retry_at, format_task_error(e)\n"
            "            )",
            "poller typed retry error",
        )
    else:
        text = _replace_exact_drain_source_fragment(
            text,
            "def _wall_timeout_for(task_type: str) -> float | None:\n",
            "def _operation_recovery_task_error_message(e: Exception) -> str:\n"
            "    message = str(e)\n"
            "    name = type(e).__name__\n"
            "    return f\"{name}: {message}\" if message else name\n\n\n"
            "def _wall_timeout_for(task_type: str) -> float | None:\n",
            "poller typed error helper",
        )
        text = _replace_exact_drain_source_fragment(
            text,
            "await self._schedule_retry(task.operation_id, e.retry_at, str(e), task.schema)",
            "await self._schedule_retry(\n"
            "                task.operation_id,\n"
            "                e.retry_at,\n"
            "                _operation_recovery_task_error_message(e),\n"
            "                task.schema,\n"
            "            )",
            "poller typed retry error",
        )
        text = _replace_exact_drain_source_fragment(
            text,
            "await self._mark_failed(task.operation_id, str(e), task.schema)",
            "await self._mark_failed(\n"
            "                    task.operation_id,\n"
            "                    _operation_recovery_task_error_message(e),\n"
            "                    task.schema,\n"
            "                )",
            "poller typed terminal error",
        )
    try:
        compile(text, "hindsight_api/worker/poller.py", "exec")
    except SyntaxError as error:
        raise OperationRecoveryError(
            "exact drain patched poller source is invalid"
        ) from error
    return text.encode("utf-8")


def _atomic_replace_exact_drain_candidate_source(
    path: Path,
    *,
    expected: bytes,
    replacement: bytes,
) -> None:
    temporary = path.with_name(f".{path.name}.exact-drain.tmp")
    try:
        if temporary.exists():
            metadata = temporary.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or metadata.st_mode & 0o022
                or metadata.st_nlink != 1
            ):
                raise OperationRecoveryError(
                    "exact drain candidate source staging is untrusted"
                )
            temporary.unlink()
        _write_exact_drain_snapshot_file(temporary, replacement)
        if _exact_drain_file_bytes(
            path,
            "exact drain candidate entity resolver",
            max_bytes=1024 * 1024,
        ) != expected:
            raise OperationRecoveryError(
                "exact drain candidate entity resolver changed"
            )
        os.replace(temporary, path)
        descriptor = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise OperationRecoveryError(
            "exact drain candidate runtime snapshot is unavailable"
        ) from error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _fsync_exact_drain_directory(path: Path) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0),
        )
        os.fsync(descriptor)
    except OSError as error:
        raise OperationRecoveryError(
            "exact drain candidate runtime snapshot is unavailable"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _publish_exact_drain_snapshot_directory(
    staging: Path,
    target: Path,
) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            target.parent,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0),
        )
        library = ctypes.CDLL(None, use_errno=True)
        if sys.platform == "darwin":
            rename = library.renameatx_np
            exclusive = 0x00000004
        else:
            rename = library.renameat2
            exclusive = 0x00000001
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        if (
            rename(
                descriptor,
                os.fsencode(staging.name),
                descriptor,
                os.fsencode(target.name),
                exclusive,
            )
            != 0
        ):
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number), target)
    except (AttributeError, OSError) as error:
        raise OperationRecoveryError(
            "exact drain candidate runtime snapshot is unavailable"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _fsync_exact_drain_directory(target.parent)


def _remove_exact_drain_snapshot_staging(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise OperationRecoveryError(
            "exact drain candidate runtime snapshot is unavailable"
        ) from error
    if (
        path.resolve(strict=True) != path
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o077
    ):
        raise OperationRecoveryError(
            "exact drain candidate runtime snapshot staging is untrusted"
        )
    try:
        shutil.rmtree(path)
    except OSError as error:
        raise OperationRecoveryError(
            "exact drain candidate runtime snapshot is unavailable"
        ) from error


def _exact_drain_snapshot_recovery_body(
    snapshot: Mapping[str, Any],
    patched_sources: Mapping[str, bytes],
) -> bytes:
    if snapshot["schema_version"] == 2:
        value = {
            "schema_version": 1,
            "kind": "exact-drain-candidate-runtime-snapshot-recovery",
            "snapshot_digest": snapshot["snapshot_digest"],
            "patched_resolver_sha256": hashlib.sha256(
                patched_sources[
                    EXACT_DRAIN_CANDIDATE_RESOLVER_PATH.as_posix()
                ]
            ).hexdigest(),
        }
    else:
        value = {
            "schema_version": 2,
            "kind": "exact-drain-candidate-runtime-snapshot-recovery",
            "snapshot_digest": snapshot["snapshot_digest"],
            "patched_source_digests": {
                path: hashlib.sha256(body).hexdigest()
                for path, body in sorted(patched_sources.items())
            },
        }
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _remove_exact_drain_snapshot_recovery_staging(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise OperationRecoveryError(
            "exact drain candidate runtime snapshot is unavailable"
        ) from error
    if (
        path.resolve(strict=True) != path
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o077
        or metadata.st_nlink != 1
    ):
        raise OperationRecoveryError(
            "exact drain candidate snapshot recovery staging is untrusted"
        )
    try:
        path.unlink()
    except OSError as error:
        raise OperationRecoveryError(
            "exact drain candidate runtime snapshot is unavailable"
        ) from error


def _write_exact_drain_snapshot_recovery_file(
    path: Path,
    body: bytes,
) -> None:
    staging = path.with_name(f".{path.name}.staging")
    _remove_exact_drain_snapshot_recovery_staging(staging)
    try:
        _write_exact_drain_snapshot_file(staging, body)
        _publish_exact_drain_snapshot_directory(staging, path)
    finally:
        _remove_exact_drain_snapshot_recovery_staging(staging)


def _finalize_exact_drain_snapshot_recovery(
    recovery_path: Path,
    library: Path,
    recovery_body: bytes,
) -> None:
    try:
        recovery_path.unlink()
        _fsync_exact_drain_directory(library)
    except (OSError, OperationRecoveryError) as error:
        try:
            if not recovery_path.exists():
                _write_exact_drain_snapshot_recovery_file(
                    recovery_path,
                    recovery_body,
                )
            elif _exact_drain_file_bytes(
                recovery_path,
                "exact drain candidate snapshot recovery",
                max_bytes=1024,
            ) != recovery_body:
                raise OperationRecoveryError(
                    "exact drain candidate runtime snapshot differs"
                )
            _fsync_exact_drain_directory(library)
        except (OSError, OperationRecoveryError) as recovery_error:
            if not recovery_path.exists():
                try:
                    _write_exact_drain_snapshot_recovery_file(
                        recovery_path,
                        recovery_body,
                    )
                    _fsync_exact_drain_directory(library)
                except (OSError, OperationRecoveryError) as retry_error:
                    raise OperationRecoveryError(
                        "exact drain candidate runtime snapshot is unavailable"
                    ) from retry_error
            raise OperationRecoveryError(
                "exact drain candidate runtime snapshot is unavailable"
            ) from recovery_error
        if isinstance(error, OperationRecoveryError):
            raise
        raise OperationRecoveryError(
            "exact drain candidate runtime snapshot is unavailable"
        ) from error


def assemble_exact_drain_candidate_runtime_snapshot(
    provider_runtime_root: str | Path,
    candidate_library: str | Path,
) -> dict[str, Any]:
    """Seal provider sources and the bounded Phase-1 patch into a candidate."""
    source_root = Path(provider_runtime_root)
    library = Path(candidate_library)
    if not source_root.is_absolute() or not library.is_absolute():
        raise OperationRecoveryError(
            "exact drain candidate runtime snapshot paths must be absolute"
        )
    _exact_drain_trusted_directory(
        library,
        "exact drain candidate library",
    )
    source_bodies = {
        name: _exact_drain_file_bytes(
            source_root / name,
            f"exact drain provider runtime {name}",
            max_bytes=1024 * 1024,
        )
        for name in EXACT_DRAIN_PROVIDER_SOURCE_NAMES
    }
    resolver_path = library / EXACT_DRAIN_CANDIDATE_RESOLVER_PATH
    resolver_source = _exact_drain_file_bytes(
        resolver_path,
        "exact drain candidate entity resolver",
        max_bytes=1024 * 1024,
    )
    postgresql_ops_path = library / EXACT_DRAIN_CANDIDATE_POSTGRESQL_OPS_PATH
    postgresql_ops_source = _exact_drain_file_bytes(
        postgresql_ops_path,
        "exact drain candidate PostgreSQL ops",
        max_bytes=1024 * 1024,
    )
    memory_engine_path = library / EXACT_DRAIN_CANDIDATE_MEMORY_ENGINE_PATH
    memory_engine_source = _exact_drain_file_bytes(
        memory_engine_path,
        "exact drain candidate memory engine",
        max_bytes=1024 * 1024,
    )
    poller_path = library / EXACT_DRAIN_CANDIDATE_POLLER_PATH
    poller_source = _exact_drain_file_bytes(
        poller_path,
        "exact drain candidate poller",
        max_bytes=1024 * 1024,
    )
    candidate_sources = {
        EXACT_DRAIN_CANDIDATE_RESOLVER_PATH.as_posix(): resolver_source,
        EXACT_DRAIN_CANDIDATE_POSTGRESQL_OPS_PATH.as_posix(): (
            postgresql_ops_source
        ),
        EXACT_DRAIN_CANDIDATE_MEMORY_ENGINE_PATH.as_posix(): (
            memory_engine_source
        ),
        EXACT_DRAIN_CANDIDATE_POLLER_PATH.as_posix(): poller_source,
    }
    runtime_root = library / EXACT_DRAIN_CANDIDATE_RUNTIME_DIRECTORY
    staging_root = library / f".{EXACT_DRAIN_CANDIDATE_RUNTIME_DIRECTORY}.staging"
    recovery_path = library / f".{EXACT_DRAIN_CANDIDATE_RUNTIME_DIRECTORY}.recovery"
    if runtime_root.exists():
        snapshot, sealed_sources, original_sources, patched_sources = (
            _verify_exact_drain_candidate_runtime_snapshot(
                library,
                require_candidate_patch=False,
            )
        )
        if snapshot["schema_version"] not in {2, 3, 4, 5, 6, 7, 8}:
            raise OperationRecoveryError(
                "exact drain candidate runtime snapshot already exists"
            )
        if sealed_sources != source_bodies:
            raise OperationRecoveryError(
                "exact drain candidate runtime snapshot differs"
            )
        current_sources = {
            EXACT_DRAIN_CANDIDATE_RESOLVER_PATH.as_posix(): resolver_source,
            EXACT_DRAIN_CANDIDATE_POSTGRESQL_OPS_PATH.as_posix(): (
                postgresql_ops_source
            ),
            EXACT_DRAIN_CANDIDATE_MEMORY_ENGINE_PATH.as_posix(): (
                memory_engine_source
            ),
            EXACT_DRAIN_CANDIDATE_POLLER_PATH.as_posix(): poller_source,
        }
        if all(
            current_sources[path] == patched
            for path, patched in patched_sources.items()
        ):
            if not recovery_path.exists():
                raise OperationRecoveryError(
                    "exact drain candidate runtime snapshot already exists"
                )
            if _exact_drain_file_bytes(
                recovery_path,
                "exact drain candidate snapshot recovery",
                max_bytes=1024,
            ) != _exact_drain_snapshot_recovery_body(
                snapshot,
                patched_sources,
            ):
                raise OperationRecoveryError(
                    "exact drain candidate runtime snapshot differs"
                )
            _finalize_exact_drain_snapshot_recovery(
                recovery_path,
                library,
                _exact_drain_snapshot_recovery_body(
                    snapshot,
                    patched_sources,
                ),
            )
            verified, _sources = verify_exact_drain_candidate_runtime_snapshot(
                library
            )
            return verified
        if any(
            current_sources[path] not in {original_sources[path], patched}
            for path, patched in patched_sources.items()
        ):
            raise OperationRecoveryError(
                "exact drain candidate runtime snapshot differs"
            )
        recovery_body = _exact_drain_snapshot_recovery_body(
            snapshot,
            patched_sources,
        )
        if recovery_path.exists():
            if _exact_drain_file_bytes(
                recovery_path,
                "exact drain candidate snapshot recovery",
                max_bytes=1024,
            ) != recovery_body:
                raise OperationRecoveryError(
                    "exact drain candidate runtime snapshot differs"
                )
        else:
            _write_exact_drain_snapshot_recovery_file(
                recovery_path,
                recovery_body,
            )
            _fsync_exact_drain_directory(library)
        for relative, replacement in patched_sources.items():
            if current_sources[relative] == replacement:
                continue
            _atomic_replace_exact_drain_candidate_source(
                library / relative,
                expected=original_sources[relative],
                replacement=replacement,
            )
        _finalize_exact_drain_snapshot_recovery(
            recovery_path,
            library,
            recovery_body,
        )
        verified, _sources = verify_exact_drain_candidate_runtime_snapshot(
            library
        )
        return verified
    patched_sources = {
        EXACT_DRAIN_CANDIDATE_RESOLVER_PATH.as_posix(): (
            _patch_exact_drain_entity_resolver(
                resolver_source,
                legacy_entity_schema=True,
                candidate_query_batch_size=(
                    EXACT_DRAIN_FUZZY_QUERY_BATCH_SIZE
                ),
                cooccurrence_query_batch_size=(
                    EXACT_DRAIN_COOCCURRENCE_QUERY_BATCH_SIZE
                ),
                full_strategy_repair=True,
            )
        ),
        EXACT_DRAIN_CANDIDATE_POSTGRESQL_OPS_PATH.as_posix(): (
            _patch_exact_drain_postgresql_ops(postgresql_ops_source)
        ),
        EXACT_DRAIN_CANDIDATE_MEMORY_ENGINE_PATH.as_posix(): (
            _patch_exact_drain_memory_engine(
                memory_engine_source,
                idempotent_missing_mental_model=True,
            )
        ),
        EXACT_DRAIN_CANDIDATE_POLLER_PATH.as_posix(): (
            _patch_exact_drain_poller(poller_source)
        ),
    }
    _remove_exact_drain_snapshot_staging(staging_root)
    staged_provider_root = staging_root / "provider"
    staged_hindsight_root = staging_root / "hindsight"
    try:
        staging_root.mkdir(mode=0o700)
        staged_provider_root.mkdir(mode=0o700)
        staged_hindsight_root.mkdir(mode=0o700)
    except OSError as error:
        _remove_exact_drain_snapshot_staging(staging_root)
        raise OperationRecoveryError(
            "exact drain candidate runtime snapshot is unavailable"
        ) from error
    provider_evidence = [
        {
            "path": f"provider/{name}",
            "sha256": hashlib.sha256(source_bodies[name]).hexdigest(),
            "size": len(source_bodies[name]),
        }
        for name in EXACT_DRAIN_PROVIDER_SOURCE_NAMES
    ]
    candidate_patch_evidence = []
    for relative, original in candidate_sources.items():
        snapshot_name = {
            EXACT_DRAIN_CANDIDATE_RESOLVER_PATH.as_posix(): "entity_resolver",
            EXACT_DRAIN_CANDIDATE_POSTGRESQL_OPS_PATH.as_posix(): "ops_postgresql",
            EXACT_DRAIN_CANDIDATE_MEMORY_ENGINE_PATH.as_posix(): "memory_engine",
            EXACT_DRAIN_CANDIDATE_POLLER_PATH.as_posix(): "poller",
        }[relative]
        patched = patched_sources[relative]
        candidate_patch_evidence.append(
            {
                "path": relative,
                "original": {
                    "path": f"hindsight/{snapshot_name}.original.py",
                    "sha256": hashlib.sha256(original).hexdigest(),
                    "size": len(original),
                },
                "patched": {
                    "path": f"hindsight/{snapshot_name}.py",
                    "sha256": hashlib.sha256(patched).hexdigest(),
                    "size": len(patched),
                },
            }
        )
    manifest = {
        "schema_version": 8,
        "kind": "exact-drain-candidate-runtime-snapshot",
        "sources": provider_evidence,
        "candidate_patches": candidate_patch_evidence,
    }
    try:
        for name, body in source_bodies.items():
            _write_exact_drain_snapshot_file(staged_provider_root / name, body)
        for evidence in candidate_patch_evidence:
            relative = evidence["path"]
            _write_exact_drain_snapshot_file(
                staging_root / evidence["original"]["path"],
                candidate_sources[relative],
            )
            _write_exact_drain_snapshot_file(
                staging_root / evidence["patched"]["path"],
                patched_sources[relative],
            )
        manifest_body = (
            json.dumps(manifest, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        _write_exact_drain_snapshot_file(
            staging_root / "manifest.json",
            manifest_body,
        )
        staged_snapshot, staged_sources, _original, _patched = (
            _verify_exact_drain_candidate_runtime_snapshot(
                library,
                require_candidate_patch=False,
                runtime_root_override=staging_root,
            )
        )
        if staged_sources != source_bodies:
            raise OperationRecoveryError(
                "exact drain candidate runtime snapshot differs"
            )
        _publish_exact_drain_snapshot_directory(staging_root, runtime_root)
        recovery_body = _exact_drain_snapshot_recovery_body(
            staged_snapshot,
            patched_sources,
        )
        _write_exact_drain_snapshot_recovery_file(
            recovery_path,
            recovery_body,
        )
        _fsync_exact_drain_directory(library)
        for relative, replacement in patched_sources.items():
            _atomic_replace_exact_drain_candidate_source(
                library / relative,
                expected=candidate_sources[relative],
                replacement=replacement,
            )
        _finalize_exact_drain_snapshot_recovery(
            recovery_path,
            library,
            recovery_body,
        )
        verified, _sources = verify_exact_drain_candidate_runtime_snapshot(
            library
        )
        return verified
    finally:
        _remove_exact_drain_snapshot_staging(staging_root)


def _verify_exact_drain_candidate_runtime_snapshot(
    candidate_library: str | Path,
    *,
    require_candidate_patch: bool,
    runtime_root_override: Path | None = None,
) -> tuple[
    dict[str, Any],
    dict[str, bytes],
    dict[str, bytes],
    dict[str, bytes],
]:
    library = Path(candidate_library)
    runtime_root = (
        library / EXACT_DRAIN_CANDIDATE_RUNTIME_DIRECTORY
        if runtime_root_override is None
        else runtime_root_override
    )
    provider_root = runtime_root / "provider"
    _exact_drain_trusted_directory(
        runtime_root,
        "exact drain candidate runtime snapshot",
    )
    _exact_drain_trusted_directory(
        provider_root,
        "exact drain candidate provider runtime",
    )
    manifest_body = _exact_drain_file_bytes(
        runtime_root / "manifest.json",
        "exact drain candidate runtime snapshot manifest",
        max_bytes=64 * 1024,
    )
    try:
        manifest = strict_json_loads(manifest_body)
    except (StrictJsonError, UnicodeDecodeError) as error:
        raise OperationRecoveryError(
            "exact drain candidate runtime snapshot differs"
        ) from error
    if not isinstance(manifest, Mapping) or type(
        manifest.get("schema_version")
    ) is not int:
        raise OperationRecoveryError(
            "exact drain candidate runtime snapshot differs"
        )
    schema_version = manifest["schema_version"]
    try:
        runtime_names = {path.name for path in runtime_root.iterdir()}
        provider_names = {path.name for path in provider_root.iterdir()}
    except OSError as error:
        raise OperationRecoveryError(
            "exact drain candidate runtime snapshot is unavailable"
        ) from error
    sources = {
        name: _exact_drain_file_bytes(
            provider_root / name,
            f"exact drain provider runtime {name}",
            max_bytes=1024 * 1024,
        )
        for name in EXACT_DRAIN_PROVIDER_SOURCE_NAMES
    }
    provider_evidence = [
        {
            "path": f"provider/{name}",
            "sha256": hashlib.sha256(sources[name]).hexdigest(),
            "size": len(sources[name]),
        }
        for name in EXACT_DRAIN_PROVIDER_SOURCE_NAMES
    ]
    original_sources: dict[str, bytes] = {}
    patched_sources: dict[str, bytes] = {}
    if schema_version == 1:
        expected = {
            "schema_version": 1,
            "kind": "exact-drain-candidate-runtime-snapshot",
            "sources": provider_evidence,
        }
        if runtime_names != {"manifest.json", "provider"}:
            raise OperationRecoveryError(
                "exact drain candidate runtime snapshot differs"
            )
    elif schema_version == 2:
        hindsight_root = runtime_root / "hindsight"
        _exact_drain_trusted_directory(
            hindsight_root,
            "exact drain candidate Hindsight source snapshot",
        )
        try:
            hindsight_names = {path.name for path in hindsight_root.iterdir()}
        except OSError as error:
            raise OperationRecoveryError(
                "exact drain candidate runtime snapshot is unavailable"
            ) from error
        if runtime_names != {"manifest.json", "provider", "hindsight"} or (
            hindsight_names
            != {"entity_resolver.original.py", "entity_resolver.py"}
        ):
            raise OperationRecoveryError(
                "exact drain candidate runtime snapshot differs"
            )
        original_resolver = _exact_drain_file_bytes(
            hindsight_root / "entity_resolver.original.py",
            "exact drain original entity resolver snapshot",
            max_bytes=1024 * 1024,
        )
        patched_resolver = _exact_drain_file_bytes(
            hindsight_root / "entity_resolver.py",
            "exact drain patched entity resolver snapshot",
            max_bytes=1024 * 1024,
        )
        if _patch_exact_drain_entity_resolver(original_resolver) != patched_resolver:
            raise OperationRecoveryError(
                "exact drain candidate runtime snapshot differs"
            )
        original_sources[EXACT_DRAIN_CANDIDATE_RESOLVER_PATH.as_posix()] = (
            original_resolver
        )
        patched_sources[EXACT_DRAIN_CANDIDATE_RESOLVER_PATH.as_posix()] = (
            patched_resolver
        )
        expected = {
            "schema_version": 2,
            "kind": "exact-drain-candidate-runtime-snapshot",
            "sources": provider_evidence,
            "candidate_patch": {
                "path": EXACT_DRAIN_CANDIDATE_RESOLVER_PATH.as_posix(),
                "original": {
                    "path": "hindsight/entity_resolver.original.py",
                    "sha256": hashlib.sha256(original_resolver).hexdigest(),
                    "size": len(original_resolver),
                },
                "patched": {
                    "path": "hindsight/entity_resolver.py",
                    "sha256": hashlib.sha256(patched_resolver).hexdigest(),
                    "size": len(patched_resolver),
                },
            },
        }
        if require_candidate_patch and _exact_drain_file_bytes(
            library / EXACT_DRAIN_CANDIDATE_RESOLVER_PATH,
            "exact drain candidate entity resolver",
            max_bytes=1024 * 1024,
        ) != patched_resolver:
            raise OperationRecoveryError(
                "exact drain candidate runtime snapshot differs"
            )
    elif schema_version in {3, 4, 5, 6, 7, 8}:
        hindsight_root = runtime_root / "hindsight"
        _exact_drain_trusted_directory(
            hindsight_root,
            "exact drain candidate Hindsight source snapshot",
        )
        try:
            hindsight_names = {path.name for path in hindsight_root.iterdir()}
        except OSError as error:
            raise OperationRecoveryError(
                "exact drain candidate runtime snapshot is unavailable"
            ) from error
        expected_names = {
            "entity_resolver.original.py",
            "entity_resolver.py",
            "ops_postgresql.original.py",
            "ops_postgresql.py",
        }
        if schema_version in {5, 6, 7, 8}:
            expected_names |= {
                "memory_engine.original.py",
                "memory_engine.py",
                "poller.original.py",
                "poller.py",
            }
        if runtime_names != {"manifest.json", "provider", "hindsight"} or (
            hindsight_names != expected_names
        ):
            raise OperationRecoveryError(
                "exact drain candidate runtime snapshot differs"
            )
        patch_specs = [
            (
                EXACT_DRAIN_CANDIDATE_RESOLVER_PATH,
                "entity_resolver",
                lambda source: _patch_exact_drain_entity_resolver(
                    source,
                    legacy_entity_schema=True,
                    candidate_query_batch_size=(
                        None
                        if schema_version == 3
                        else EXACT_DRAIN_FUZZY_QUERY_BATCH_SIZE
                    ),
                    cooccurrence_query_batch_size=(
                        EXACT_DRAIN_COOCCURRENCE_QUERY_BATCH_SIZE
                        if schema_version in {6, 7, 8}
                        else None
                    ),
                    full_strategy_repair=(schema_version in {7, 8}),
                ),
                "entity resolver",
            ),
            (
                EXACT_DRAIN_CANDIDATE_POSTGRESQL_OPS_PATH,
                "ops_postgresql",
                _patch_exact_drain_postgresql_ops,
                "PostgreSQL ops",
            ),
        ]
        if schema_version in {5, 6, 7, 8}:
            patch_specs.extend(
                [
                    (
                        EXACT_DRAIN_CANDIDATE_MEMORY_ENGINE_PATH,
                        "memory_engine",
                        lambda source: _patch_exact_drain_memory_engine(
                            source,
                            idempotent_missing_mental_model=(
                                schema_version == 8
                            ),
                        ),
                        "memory engine",
                    ),
                    (
                        EXACT_DRAIN_CANDIDATE_POLLER_PATH,
                        "poller",
                        _patch_exact_drain_poller,
                        "poller",
                    ),
                ]
            )
        candidate_patch_evidence = []
        for relative_path, snapshot_name, patcher, label in patch_specs:
            relative = relative_path.as_posix()
            original = _exact_drain_file_bytes(
                hindsight_root / f"{snapshot_name}.original.py",
                f"exact drain original {label} snapshot",
                max_bytes=1024 * 1024,
            )
            patched = _exact_drain_file_bytes(
                hindsight_root / f"{snapshot_name}.py",
                f"exact drain patched {label} snapshot",
                max_bytes=1024 * 1024,
            )
            if patcher(original) != patched:
                raise OperationRecoveryError(
                    "exact drain candidate runtime snapshot differs"
                )
            original_sources[relative] = original
            patched_sources[relative] = patched
            candidate_patch_evidence.append(
                {
                    "path": relative,
                    "original": {
                        "path": f"hindsight/{snapshot_name}.original.py",
                        "sha256": hashlib.sha256(original).hexdigest(),
                        "size": len(original),
                    },
                    "patched": {
                        "path": f"hindsight/{snapshot_name}.py",
                        "sha256": hashlib.sha256(patched).hexdigest(),
                        "size": len(patched),
                    },
                }
            )
            if require_candidate_patch and _exact_drain_file_bytes(
                library / relative_path,
                f"exact drain candidate {label}",
                max_bytes=1024 * 1024,
            ) != patched:
                raise OperationRecoveryError(
                    "exact drain candidate runtime snapshot differs"
                )
        expected = {
            "schema_version": schema_version,
            "kind": "exact-drain-candidate-runtime-snapshot",
            "sources": provider_evidence,
            "candidate_patches": candidate_patch_evidence,
        }
    else:
        raise OperationRecoveryError(
            "exact drain candidate runtime snapshot differs"
        )
    if provider_names != set(EXACT_DRAIN_PROVIDER_SOURCE_NAMES):
        raise OperationRecoveryError(
            "exact drain candidate runtime snapshot differs"
        )
    if manifest != expected or manifest_body != (
        json.dumps(expected, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8"):
        raise OperationRecoveryError(
            "exact drain candidate runtime snapshot differs"
        )
    return (
        {**expected, "snapshot_digest": digest(expected)},
        sources,
        original_sources,
        patched_sources,
    )


def verify_exact_drain_candidate_runtime_snapshot(
    candidate_library: str | Path,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Verify the closed provider/code snapshot sealed into a candidate."""
    snapshot, sources, _original, _patched = (
        _verify_exact_drain_candidate_runtime_snapshot(
            candidate_library,
            require_candidate_patch=True,
        )
    )
    return snapshot, sources


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
    try:
        standard_library_root = Path(sys.base_prefix).resolve(strict=True)
    except OSError as error:
        raise OperationRecoveryError(
            "exact drain trusted Python runtime is unavailable"
        ) from error
    trusted_runtime_entries: list[str] = []
    for entry in sys.path:
        if not entry or entry in {candidate_text, dependency_text}:
            continue
        try:
            resolved = Path(entry).resolve(strict=True)
            resolved.relative_to(standard_library_root)
        except (OSError, ValueError):
            continue
        if "site-packages" in resolved.parts or "dist-packages" in resolved.parts:
            continue
        text = str(resolved)
        if text not in trusted_runtime_entries:
            trusted_runtime_entries.append(text)
    sys.path[:] = [
        candidate_text,
        *trusted_runtime_entries,
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


def validate_exact_drain_import_origins(
    worker_runtime: str | Path,
    candidate_library: str | Path,
) -> None:
    """Reject loaded code outside candidate, dependency, or Python roots."""
    dependency_root = exact_drain_worker_site_packages_path(worker_runtime)
    candidate_root = Path(candidate_library).resolve(strict=True)
    release_root = candidate_root.parent.resolve(strict=True)
    python_root = Path(sys.base_prefix).resolve(strict=True)
    try:
        worker_entrypoint = (
            release_root / "bin" / "hindsight-exact-drain-worker"
        ).resolve(strict=True)
    except OSError as error:
        raise OperationRecoveryError(
            "exact drain worker entrypoint is unavailable"
        ) from error
    allowed_candidate_names = {
        "hindsight_api",
        "hindsight_memory_control_plane",
        "hindsight_llm_failover",
        "sitecustomize",
    }
    for name, module in tuple(sys.modules.items()):
        raw_path = getattr(module, "__file__", None)
        if not isinstance(raw_path, str):
            continue
        try:
            path = Path(raw_path).resolve(strict=True)
        except OSError as error:
            raise OperationRecoveryError(
                "exact drain loaded module origin is unavailable"
            ) from error
        try:
            path.relative_to(dependency_root)
            continue
        except ValueError:
            pass
        try:
            path.relative_to(python_root)
            continue
        except ValueError:
            pass
        try:
            path.relative_to(candidate_root)
        except ValueError:
            pass
        else:
            if name.split(".", 1)[0] in allowed_candidate_names:
                continue
            raise OperationRecoveryError(
                "exact drain candidate dependency shadow differs"
            )
        try:
            path.relative_to(release_root)
        except ValueError as error:
            raise OperationRecoveryError(
                "exact drain loaded module origin differs"
            ) from error
        # multiprocessing publishes the running main module under this alias.
        if path != worker_entrypoint or (
            name != "__main__"
            and not (
                name == "__mp_main__"
                and module is sys.modules.get("__main__")
            )
        ):
            raise OperationRecoveryError(
                "exact drain loaded module origin differs"
            )


def validate_exact_drain_dependency_spec(
    name: str,
    worker_runtime: str | Path,
) -> None:
    """Prove one required dependency resolves only from the closed root."""
    dependency_root = exact_drain_worker_site_packages_path(worker_runtime)
    spec = importlib.util.find_spec(name)
    try:
        origin = Path(spec.origin).resolve(strict=True)
        origin.relative_to(dependency_root)
        locations = tuple(
            Path(value).resolve(strict=True)
            for value in (spec.submodule_search_locations or ())
        )
    except (AttributeError, OSError, TypeError, ValueError) as error:
        raise OperationRecoveryError(
            f"exact drain dependency {name} origin differs"
        ) from error
    if locations and any(
        location != dependency_root / name for location in locations
    ):
        raise OperationRecoveryError(
            f"exact drain dependency {name} origin differs"
        )


def validate_exact_drain_candidate_release_import(
    candidate_library: str | Path,
) -> Path:
    """Return the detached candidate only when Hindsight resolves inside it."""
    library = Path(candidate_library)
    if not library.is_absolute():
        raise OperationRecoveryError(
            "exact drain candidate release import differs"
        )
    try:
        library = library.resolve(strict=True)
        release = library.parent.resolve(strict=True)
        first_import = Path(sys.path[0]).resolve(strict=True)
    except (IndexError, OSError) as error:
        raise OperationRecoveryError(
            "exact drain candidate release import differs"
        ) from error
    _exact_drain_trusted_directory(
        release,
        "exact drain detached candidate release",
    )
    _exact_drain_trusted_directory(
        library,
        "exact drain detached candidate library",
    )
    package = library / "hindsight_api"
    _exact_drain_trusted_directory(
        package,
        "exact drain candidate Hindsight package",
    )
    spec = importlib.util.find_spec("hindsight_api")
    try:
        origin = Path(spec.origin).resolve(strict=True)
        locations = tuple(
            Path(value).resolve(strict=True)
            for value in spec.submodule_search_locations
        )
        origin.relative_to(package)
    except (AttributeError, OSError, TypeError, ValueError) as error:
        raise OperationRecoveryError(
            "exact drain candidate release import differs"
        ) from error
    if first_import != library or locations != (package,):
        raise OperationRecoveryError(
            "exact drain candidate release import differs"
        )
    return release


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


def _exact_drain_asyncpg_runtime_evidence(
    worker_runtime: str | Path,
) -> dict[str, str]:
    """Bind the exact asyncpg distribution and code imported by the worker."""
    site_packages = exact_drain_worker_site_packages_path(worker_runtime)
    try:
        distributions = tuple(site_packages.glob("asyncpg-*.dist-info"))
    except OSError as error:
        raise OperationRecoveryError(
            "exact drain worker asyncpg dependency is unavailable"
        ) from error
    if (
        len(distributions) != 1
        or not distributions[0].is_dir()
        or distributions[0].is_symlink()
    ):
        raise OperationRecoveryError(
            "exact drain worker asyncpg dependency is unavailable"
        )
    _exact_drain_trusted_directory(
        distributions[0],
        "exact drain worker asyncpg metadata directory",
    )
    metadata = _exact_drain_file_bytes(
        distributions[0] / "METADATA",
        "exact drain worker asyncpg metadata",
        max_bytes=1024 * 1024,
    )
    names = [
        line.removeprefix(b"Name: ")
        for line in metadata.splitlines()
        if line.startswith(b"Name: ")
    ]
    versions = [
        line.removeprefix(b"Version: ")
        for line in metadata.splitlines()
        if line.startswith(b"Version: ")
    ]
    try:
        version = versions[0].decode("ascii")
    except (IndexError, UnicodeDecodeError) as error:
        raise OperationRecoveryError(
            "exact drain worker asyncpg dependency is unavailable"
        ) from error
    if (
        names != [b"asyncpg"]
        or len(versions) != 1
        or re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+!_-]{0,63}", version)
        is None
        or distributions[0].name != f"asyncpg-{version}.dist-info"
    ):
        raise OperationRecoveryError(
            "exact drain worker asyncpg dependency is unavailable"
        )
    package_root = site_packages / "asyncpg"
    _exact_drain_trusted_directory(
        package_root,
        "exact drain worker asyncpg package",
    )
    evidence = {
        "dependency/asyncpg/distribution-metadata": hashlib.sha256(
            metadata
        ).hexdigest()
    }
    file_count = 0
    total_bytes = 0
    for path in _exact_drain_package_entries(package_root):
        relative = path.relative_to(package_root).as_posix()
        if path.is_symlink():
            raise OperationRecoveryError(
                "exact drain worker asyncpg dependency is untrusted"
            )
        if path.is_dir():
            _exact_drain_trusted_directory(
                path,
                f"exact drain worker asyncpg directory {relative}",
            )
            evidence[f"dependency-directory/asyncpg/{relative}"] = digest(
                {"kind": "directory"}
            )
            continue
        if not path.is_file():
            raise OperationRecoveryError(
                "exact drain worker asyncpg dependency is untrusted"
            )
        file_count += 1
        artifact_digest, artifact_size = _exact_drain_file_digest_evidence(
            path,
            f"exact drain worker asyncpg artifact {relative}",
            max_bytes=min(
                EXACT_DRAIN_MAX_PACKAGE_FILE_BYTES,
                EXACT_DRAIN_MAX_PACKAGE_TOTAL_BYTES - total_bytes,
            ),
        )
        total_bytes += artifact_size
        evidence[f"dependency/asyncpg/{relative}"] = artifact_digest
    if file_count == 0:
        raise OperationRecoveryError(
            "exact drain worker asyncpg dependency is unavailable"
        )
    return evidence


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
    *,
    schema_version: int = 13,
) -> tuple[str, bytes]:
    """Bind runtime sources and retain prevalidated provider bootstrap bytes."""
    if type(schema_version) is not int or schema_version not in {
        1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15
    }:
        raise OperationRecoveryError(
            "exact drain runtime evidence schema version is invalid"
        )
    worker_path = Path(worker_runtime)
    provider_root = Path(provider_runtime_root)
    if not worker_path.is_absolute() or (
        schema_version == 1 and not provider_root.is_absolute()
    ):
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
    if schema_version == 1:
        sources["worker-dependency-directory"] = digest(
            {"path": str(exact_drain_worker_site_packages_path(worker_path))}
        )
    else:
        sources["worker-dependency-manifest"] = digest(
            exact_drain_dependency_manifest(worker_path)
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
    if schema_version == 1:
        provider_sources = {
            name: _exact_drain_file_bytes(
                provider_root / name,
                f"exact drain provider runtime {name}",
                max_bytes=1024 * 1024,
            )
            for name in EXACT_DRAIN_PROVIDER_SOURCE_NAMES
        }
    else:
        snapshot, provider_sources = (
            verify_exact_drain_candidate_runtime_snapshot(
                package_root.parent
            )
        )
        sources["candidate-runtime-snapshot"] = snapshot[
            "snapshot_digest"
        ]
        if schema_version == 3:
            if snapshot["schema_version"] != 2:
                raise OperationRecoveryError(
                    "exact drain phase repair candidate snapshot is required"
                )
            sources["phase-repair-contract"] = (
                EXACT_DRAIN_PHASE_REPAIR_CONTRACT_DIGEST
            )
        elif schema_version == 4:
            if snapshot["schema_version"] != 3:
                raise OperationRecoveryError(
                    "exact drain legacy-schema repair snapshot is required"
                )
            sources["phase-repair-contract"] = (
                EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V2_DIGEST
            )
        elif schema_version == 5:
            if snapshot["schema_version"] != 4:
                raise OperationRecoveryError(
                    "exact drain query-batch repair snapshot is required"
                )
            sources["phase-repair-contract"] = (
                EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V3_DIGEST
            )
        elif schema_version == 6:
            if snapshot["schema_version"] != 5:
                raise OperationRecoveryError(
                    "exact drain failure-evidence repair snapshot is required"
                )
            sources["phase-repair-contract"] = (
                EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V4_DIGEST
            )
        elif schema_version in {7, 8}:
            if snapshot["schema_version"] != 6:
                raise OperationRecoveryError(
                    "exact drain cooccurrence-batch repair snapshot is required"
                )
            sources["phase-repair-contract"] = (
                EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V5_DIGEST
            )
        elif schema_version in {9, 10}:
            if snapshot["schema_version"] != 7:
                raise OperationRecoveryError(
                    "exact drain full-query repair snapshot is required"
                )
            sources["phase-repair-contract"] = (
                EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V6_DIGEST
            )
        elif schema_version == 11:
            if snapshot["schema_version"] != 7:
                raise OperationRecoveryError(
                    "exact drain full-query repair snapshot is required"
                )
            sources["phase-repair-contract"] = (
                EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V7_DIGEST
            )
        elif schema_version == 12:
            if snapshot["schema_version"] not in {7, 8}:
                raise OperationRecoveryError(
                    "exact drain full-query repair snapshot is required"
                )
            sources["phase-repair-contract"] = (
                EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V9_DIGEST
                if snapshot["schema_version"] == 8
                else EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V8_DIGEST
            )
        elif schema_version in {13, 14, 15}:
            if snapshot["schema_version"] != 8:
                raise OperationRecoveryError(
                    "exact drain idempotent-refresh repair snapshot is required"
                )
            sources["phase-repair-contract"] = (
                EXACT_DRAIN_PHASE_REPAIR_CONTRACT_V9_DIGEST
            )
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
    *,
    schema_version: int = 13,
) -> str:
    """Bind the worker entrypoint, claim seam, and provider patch sources."""
    runtime_digest, _provider_bootstrap = exact_drain_runtime_evidence(
        worker_runtime,
        provider_runtime_root,
        runtime_package_root,
        schema_version=schema_version,
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
        progress_recorder: Any | None = None,
        resume: bool = False,
        terminal_reconciliation: bool = False,
        terminal_status_evidence: Mapping[str, Any] | None = None,
        authorization: Mapping[str, Any] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        if type(terminal_reconciliation) is not bool or (
            terminal_reconciliation and not resume
        ):
            raise OperationRecoveryError(
                "operation-recovery terminal reconciliation is invalid"
            )
        if terminal_status_evidence is not None:
            terminal_status_evidence = _mapping(terminal_status_evidence)
            if (
                not terminal_reconciliation
                or set(terminal_status_evidence)
                != {"generation", "observed_at", "status_digest"}
                or not isinstance(
                    terminal_status_evidence.get("generation"), str
                )
                or not re.fullmatch(
                    r"systalyze:public:[1-9][0-9]*",
                    terminal_status_evidence["generation"],
                )
                or type(terminal_status_evidence.get("observed_at")) is not int
                or terminal_status_evidence["observed_at"] < 0
                or not isinstance(
                    terminal_status_evidence.get("status_digest"), str
                )
                or not re.fullmatch(
                    r"[0-9a-f]{64}",
                    terminal_status_evidence["status_digest"],
                )
            ):
                raise OperationRecoveryError(
                    "operation-recovery terminal status evidence is invalid"
                )
        verified = verify_exact_drain_plan(plan, allow_expired=True)
        if (
            terminal_reconciliation
            and verified.get("schema_version")
            in {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15}
            and terminal_status_evidence is None
        ):
            raise OperationRecoveryError(
                "operation-recovery terminal status evidence is required"
            )
        self._clock = clock
        self._maximum_retry_delay_seconds = (
            verified["execution_window"]["maximum_retry_delay_seconds"]
            if verified.get("schema_version") in {10, 11, 12, 13, 14, 15}
            else None
        )
        if verified.get("schema_version") in {
            2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15
        }:
            if authorization is None:
                raise OperationRecoveryError(
                    "operation-recovery exact drain authorization is required"
                )
            checked_authorization = verify_exact_drain_authorization_receipt(
                authorization,
                plan=verified,
            )
            self._execution_deadline = exact_drain_execution_deadline(
                verified,
                checked_authorization,
            )
            self._transaction_timeout_seconds = verified[
                "transaction_timeout_seconds"
            ]
        else:
            if not resume:
                verified = verify_exact_drain_plan(plan)
            self._execution_deadline = None
            self._transaction_timeout_seconds = (
                EXACT_DRAIN_TRANSACTION_TIMEOUT_SECONDS
                if terminal_reconciliation
                else None
            )
        self.phase_one_timeout_seconds = (
            verified["phase_one_timeout_seconds"]
            if verified.get("schema_version")
            in {3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15}
            else None
        )
        self.phase_one_statement_timeout_seconds = (
            verified["phase_one_statement_timeout_seconds"]
            if verified.get("schema_version")
            in {3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15}
            else None
        )
        self.operation_attempt_timeout_seconds = (
            verified["operation_attempt_timeout_seconds"]
            if verified.get("schema_version") in {11, 12, 13, 14, 15}
            else None
        )
        self.phase_one_nested_stage_prefixes = (
            tuple(verified["phase_one_nested_stage_prefixes"])
            if verified.get("schema_version") in {11, 12, 13, 14, 15}
            else ()
        )
        self.operation_attempt_timeout_disposition = (
            verified["operation_attempt_timeout_disposition"]
            if verified.get("schema_version") in {12, 13, 14, 15}
            else "worker-fail-stop"
        )
        self._cleanup_deadline: float | None = None
        self._terminal_reconciliation_deadline: float | None = None
        self._plan = verified
        if verified.get("checkpoint_continuation_handoff") is not None:
            verify_checkpoint_continuation_handoff(
                verified["checkpoint_continuation_handoff"],
                now=int(self._clock()),
            )
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
        self._terminal_reconciliation = terminal_reconciliation
        self._terminal_status_evidence = terminal_status_evidence
        self._terminal_reconciliation_ready = False
        self._completion_callback = completion_callback
        self._completion_signalled = False
        self._progress_recorder = progress_recorder
        self._pending_progress_stages: dict[str, tuple[str, str]] = {}
        self._control_backend: Any | None = None
        self._control_connection_context: Any | None = None
        self._control_connection: Any | None = None
        self._control_connection_state = "never-reserved"
        self._control_connection_state_lock = asyncio.Lock()
        self._control_connection_use_lock = asyncio.Lock()
        if not terminal_reconciliation:
            self._assert_execution_lease()

    def _validated_control_connection_lifecycle(
        self,
    ) -> tuple[str, asyncio.Lock, asyncio.Lock]:
        unavailable = object()
        state = getattr(self, "_control_connection_state", unavailable)
        state_lock = getattr(
            self,
            "_control_connection_state_lock",
            unavailable,
        )
        use_lock = getattr(
            self,
            "_control_connection_use_lock",
            unavailable,
        )
        backend = getattr(self, "_control_backend", unavailable)
        context = getattr(
            self,
            "_control_connection_context",
            unavailable,
        )
        connection = getattr(self, "_control_connection", unavailable)
        if not isinstance(state_lock, asyncio.Lock) or not isinstance(
            use_lock,
            asyncio.Lock,
        ):
            raise OperationRecoveryError(
                "exact drain control connection lifecycle seam is unavailable"
            )
        if any(
            value is unavailable
            for value in (backend, context, connection)
        ):
            raise OperationRecoveryError(
                "exact drain control connection lifecycle seam is unavailable"
            )
        if state in {"never-reserved", "closed"}:
            valid = backend is None and context is None and connection is None
        elif state in {"reserved", "closing"}:
            valid = (
                backend is not None
                and context is not None
                and connection is not None
            )
        elif state == "poisoned":
            valid = backend is not None and context is not None
        else:
            valid = False
        if not valid:
            raise OperationRecoveryError(
                "exact drain control connection lifecycle state is invalid"
            )
        return state, state_lock, use_lock

    async def reserve_control_connection(self, backend: Any) -> None:
        """Hold one worker-pool connection outside task query fan-out."""
        _, state_lock, _ = self._validated_control_connection_lifecycle()
        async with state_lock:
            state, observed_state_lock, _ = (
                self._validated_control_connection_lifecycle()
            )
            if observed_state_lock is not state_lock:
                raise OperationRecoveryError(
                    "exact drain control connection lifecycle seam changed"
                )
            if state == "reserved":
                if self._control_backend is backend:
                    return
                raise OperationRecoveryError(
                    "exact drain control connection backend differs"
                )
            if state != "never-reserved":
                raise OperationRecoveryError(
                    "exact drain control connection state differs"
                )
            try:
                context = backend.acquire()
                enter = context.__aenter__
                exit_context = context.__aexit__
            except (AttributeError, TypeError) as error:
                raise OperationRecoveryError(
                    "exact drain control connection seam is unavailable"
                ) from error
            try:
                connection = await enter()
            except BaseException:
                raise
            if connection is None or not callable(exit_context):
                try:
                    await exit_context(None, None, None)
                except BaseException:
                    self._control_backend = backend
                    self._control_connection_context = context
                    self._control_connection = connection
                    self._control_connection_state = "poisoned"
                    raise
                self._control_connection_state = "closed"
                raise OperationRecoveryError(
                    "exact drain control connection seam is unavailable"
                )
            self._control_backend = backend
            self._control_connection_context = context
            self._control_connection = connection
            self._control_connection_state = "reserved"

    async def close_control_connection(self) -> None:
        """Release the reserved connection only after graceful row release."""
        _, state_lock, use_lock = self._validated_control_connection_lifecycle()
        async with state_lock:
            state, observed_state_lock, observed_use_lock = (
                self._validated_control_connection_lifecycle()
            )
            if (
                observed_state_lock is not state_lock
                or observed_use_lock is not use_lock
            ):
                raise OperationRecoveryError(
                    "exact drain control connection lifecycle seam changed"
                )
            if state == "closed":
                return
            if state == "never-reserved":
                self._control_connection_state = "closed"
                return
            if state not in {"reserved", "poisoned"}:
                raise OperationRecoveryError(
                    "exact drain control connection state differs"
                )
            self._control_connection_state = "closing"
            acquired = False
            try:
                await use_lock.acquire()
                acquired = True
                context = self._control_connection_context
                if context is None:
                    raise OperationRecoveryError(
                        "exact drain control connection seam is unavailable"
                    )
                try:
                    await context.__aexit__(None, None, None)
                except BaseException:
                    self._control_connection_state = "poisoned"
                    raise
                self._control_backend = None
                self._control_connection_context = None
                self._control_connection = None
                self._control_connection_state = "closed"
            except BaseException:
                if not acquired and self._control_connection_state == "closing":
                    self._control_connection_state = state
                raise
            finally:
                if acquired:
                    use_lock.release()

    @asynccontextmanager
    async def _mutation_connection(self, backend: Any):
        _, state_lock, use_lock = self._validated_control_connection_lifecycle()
        async with state_lock:
            state, observed_state_lock, observed_use_lock = (
                self._validated_control_connection_lifecycle()
            )
            if (
                observed_state_lock is not state_lock
                or observed_use_lock is not use_lock
            ):
                raise OperationRecoveryError(
                    "exact drain control connection lifecycle seam changed"
                )
            if state not in {"never-reserved", "reserved"}:
                raise OperationRecoveryError(
                    "exact drain control connection is unavailable"
                )
            connection = self._control_connection
            if state == "reserved":
                if connection is None:
                    raise OperationRecoveryError(
                        "exact drain control connection is unavailable"
                    )
                if self._control_backend is not backend:
                    raise OperationRecoveryError(
                        "exact drain control connection backend differs"
                    )
                await use_lock.acquire()
        if state == "never-reserved":
            async with backend.acquire() as fallback:
                yield fallback
            return
        try:
            yield connection
        finally:
            use_lock.release()

    def _record_task_stage(
        self,
        operation_id: str,
        *,
        status: str,
        stage: str,
    ) -> None:
        if self._progress_recorder is not None:
            self._progress_recorder.task_stage(
                operation_id,
                status=status,
                stage=stage,
            )

    def _record_task_outcome(
        self,
        operation_id: str,
        *,
        status: str,
        stage: str,
        failure: Mapping[str, Any] | None,
        checkpoint: Mapping[str, Any] | None,
    ) -> None:
        if self._progress_recorder is None:
            return
        if self._plan.get("progress_schema_version") in {2, 3, 4, 5, 6}:
            self._progress_recorder.task_outcome(
                operation_id,
                status=status,
                stage=stage,
                failure=failure,
                checkpoint=checkpoint,
            )
            return
        self._progress_recorder.task_stage(
            operation_id,
            status=status,
            stage=stage,
        )

    def record_worker_stage(self, *, status: str, stage: str) -> None:
        """Persist a worker lifecycle stage when the plan binds it."""
        if (
            self._progress_recorder is not None
            and self._plan.get("progress_schema_version") in {3, 4, 5, 6}
        ):
            self._progress_recorder.worker_stage(
                status=status,
                stage=stage,
            )

    def record_worker_failure(
        self,
        error: BaseException,
        *,
        exit_code: int,
    ) -> None:
        """Persist a closed worker-level failure without changing DB state."""
        if (
            self._progress_recorder is not None
            and self._plan.get("progress_schema_version") in {3, 4, 5, 6}
            and getattr(self._progress_recorder, "_worker_status", None)
            != "failed"
        ):
            self._progress_recorder.worker_failure(
                exit_code=exit_code,
                failure=exact_drain_worker_failure_evidence(
                    error,
                    worker_stage=getattr(
                        self._progress_recorder,
                        "_worker_stage",
                        None,
                    ),
                    progress_schema_version=self._plan[
                        "progress_schema_version"
                    ],
                ),
            )

    def _assert_execution_lease(self) -> float | None:
        deadline = getattr(self, "_execution_deadline", None)
        if deadline is None:
            return None
        observed_at = getattr(self, "_clock", time.time)()
        try:
            normalized_observed_at = (
                float(observed_at)
                if type(observed_at) in {int, float}
                else float("nan")
            )
        except (OverflowError, TypeError, ValueError):
            normalized_observed_at = float("nan")
        if not math.isfinite(normalized_observed_at):
            raise OperationRecoveryError(
                "operation-recovery exact drain clock is invalid"
            )
        if normalized_observed_at >= deadline:
            raise OperationRecoveryError(
                "operation-recovery exact drain execution lease expired"
            )
        return normalized_observed_at

    def _assert_claim_capable_mutation(self) -> float | None:
        if getattr(self, "_terminal_reconciliation", False):
            raise OperationRecoveryError(
                "operation-recovery terminal reconciliation cannot mutate"
            )
        return self._assert_execution_lease()

    def _validated_reschedule_time(
        self,
        value: Any,
        *,
        observed_at: float | None,
    ) -> Any:
        maximum_delay = getattr(
            self,
            "_maximum_retry_delay_seconds",
            None,
        )
        if maximum_delay is None:
            return value
        try:
            aware = (
                type(value) is datetime
                and value.tzinfo is not None
                and value.utcoffset() is not None
            )
            normalized = value.astimezone(timezone.utc) if aware else None
            delayed_until = normalized.timestamp() if normalized else None
        except (OverflowError, OSError, TypeError, ValueError):
            normalized = None
            delayed_until = None
        deadline = getattr(self, "_execution_deadline", None)
        if (
            observed_at is None
            or delayed_until is None
            or not math.isfinite(delayed_until)
            or delayed_until - observed_at > maximum_delay
            or (deadline is not None and delayed_until >= deadline)
        ):
            raise OperationRecoveryError(
                "operation-recovery exact drain reschedule time is invalid"
            )
        return normalized

    async def _configure_mutation_transaction(
        self,
        connection: Any,
        *,
        allow_expired_cleanup: bool = False,
    ) -> None:
        timeout_seconds = getattr(self, "_transaction_timeout_seconds", None)
        if timeout_seconds is None:
            return
        observed_at = getattr(self, "_clock", time.time)()
        execution_deadline = getattr(self, "_execution_deadline", None)
        if (
            getattr(self, "_terminal_reconciliation", False)
            and execution_deadline is not None
            and observed_at >= execution_deadline
        ):
            reconciliation_deadline = getattr(
                self,
                "_terminal_reconciliation_deadline",
                None,
            )
            if reconciliation_deadline is None:
                reconciliation_deadline = observed_at + timeout_seconds
                self._terminal_reconciliation_deadline = reconciliation_deadline
            deadline = reconciliation_deadline
        elif (
            allow_expired_cleanup
            and execution_deadline is not None
            and observed_at >= execution_deadline
        ):
            cleanup_deadline = getattr(self, "_cleanup_deadline", None)
            if cleanup_deadline is None:
                cleanup_deadline = observed_at + timeout_seconds
                self._cleanup_deadline = cleanup_deadline
            deadline = cleanup_deadline
        else:
            self._assert_execution_lease()
            deadline = observed_at + timeout_seconds
            if execution_deadline is not None:
                deadline = min(deadline, execution_deadline)
        remaining_ms = int((deadline - observed_at) * 1000)
        if remaining_ms <= 0:
            raise OperationRecoveryError(
                "operation-recovery exact drain transaction timeout expired"
            )
        timeout = f"{remaining_ms}ms"
        for name in (
            "transaction_timeout",
            "lock_timeout",
            "statement_timeout",
        ):
            await connection.fetchval(
                f"SELECT pg_catalog.set_config('{name}', $1, true)",
                timeout,
            )

    @asynccontextmanager
    async def _serializable_mutation_transaction(
        self,
        connection: Any,
        *,
        allow_expired_cleanup: bool = False,
    ):
        """Open a bounded SERIALIZABLE transaction through the public DB seam."""
        async with connection.transaction():
            await connection.execute(
                "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"
            )
            await self._configure_mutation_transaction(
                connection,
                allow_expired_cleanup=allow_expired_cleanup,
            )
            if not getattr(self, "_terminal_reconciliation", False):
                await connection.execute(
                    "SELECT generation FROM "
                    "public.hindsight_migration_generation "
                    "WHERE singleton FOR UPDATE"
                )
            yield

    def record_upstream_stage(self, operation_id: str, stage: str) -> None:
        """Project the upstream payload-free StageHolder breadcrumb."""
        if not isinstance(stage, str):
            raise OperationRecoveryError(
                "operation-recovery exact drain task stage is invalid"
            )
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/=-]{0,127}", stage):
            safe_stage = stage
        else:
            safe_stage = f"upstream:{hashlib.sha256(stage.encode('utf-8')).hexdigest()[:16]}"
        if self._progress_recorder is not None:
            self._progress_recorder.task_processing_stage(
                operation_id,
                stage=safe_stage,
            )

    def record_upstream_failure(
        self,
        operation_id: str,
        *,
        stage: str,
        category: str,
        retryable: bool,
        error_message: BaseException,
    ) -> None:
        """Persist one payload-free runtime cause without changing DB state."""
        if not isinstance(error_message, BaseException):
            raise OperationRecoveryError(
                "operation-recovery exact drain runtime failure is invalid"
            )
        if self._progress_recorder is None:
            return
        if self._plan.get("progress_schema_version") not in {2, 3, 4, 5, 6}:
            self.record_upstream_stage(operation_id, stage)
            return
        message = str(error_message)
        typed_message = (
            f"{type(error_message).__name__}: {message}"
            if message
            else type(error_message).__name__
        )
        self._progress_recorder.task_runtime_failure(
            operation_id,
            stage=stage,
            failure=_exact_drain_failure_evidence(
                typed_message,
                retryable=retryable,
                category_override=category,
                progress_schema_version=self._plan[
                    "progress_schema_version"
                ],
            ),
        )

    def claim_committed(self, tasks: Sequence[Any]) -> None:
        """Record task ownership only after the upstream claim transaction commits."""
        for task in tasks:
            operation_id = str(getattr(task, "operation_id", ""))
            folded_operation_ids = getattr(
                task,
                "folded_operation_ids",
                None,
            )
            if folded_operation_ids is not None and (
                type(folded_operation_ids) not in {list, tuple}
                or len(folded_operation_ids) != 0
            ):
                raise OperationRecoveryError(
                    "operation-recovery committed claim folded outside plan"
                )
            if operation_id not in self._selected:
                raise OperationRecoveryError(
                    "operation-recovery committed claim is outside plan"
                )
        self._started_ids.update(str(task.operation_id) for task in tasks)
        self._flush_pending_progress_stages()
        for task in tasks:
            operation_id = str(task.operation_id)
            self._record_task_stage(
                operation_id,
                status="processing",
                stage="claimed",
            )
        if (
            self._terminal_reconciliation_ready
            and not self._completion_signalled
            and self._completion_callback is not None
        ):
            self._completion_signalled = True
            self._completion_callback()

    def bind_terminal_progress_recorder(self, progress_recorder: Any) -> None:
        """Publish terminal progress only after its read-only guard commits."""
        if (
            not self._terminal_reconciliation
            or not self._terminal_reconciliation_ready
            or self._progress_recorder is not None
            or progress_recorder is None
        ):
            raise OperationRecoveryError(
                "operation-recovery terminal progress binding is invalid"
            )
        self._progress_recorder = progress_recorder

    def abort_after_committed_claim_failure(self) -> None:
        """Stop the capsule so graceful shutdown releases committed claims."""
        if self._completion_callback is not None:
            self._completion_callback()

    def _stage_after_commit(
        self,
        operation_id: str,
        *,
        status: str,
        stage: str,
    ) -> None:
        self._pending_progress_stages[operation_id] = (status, stage)

    def _flush_pending_progress_stages(self) -> None:
        pending = self._pending_progress_stages
        self._pending_progress_stages = {}
        for operation_id, (status, stage) in pending.items():
            self._record_task_stage(
                operation_id,
                status=status,
                stage=stage,
            )

    async def _verify_checkpoint_continuation_state(
        self,
        connection: Any,
    ) -> None:
        """Re-verify committed retain side effects before skipping extraction.

        The handoff is an authenticated, payload-free claim, but its JSON file
        is not itself a database proof.  Before the worker can let the upstream
        retain orchestrator take its idempotent recovery branch, bind each
        handoff row to the live queue row and check the checkpoint counts
        against the durable documents and memory_units tables.  No protected
        text or identifiers leave PostgreSQL.
        """
        if self._plan.get("checkpoint_continuation_handoff") is None:
            return
        handoff = self._plan.get("checkpoint_continuation_handoff")
        if not isinstance(handoff, Mapping):
            raise OperationRecoveryError(
                "operation-recovery checkpoint continuation handoff is unavailable"
            )
        handoff_operations = handoff.get("operations")
        audits = handoff.get("side_effect_audit")
        if (
            not isinstance(handoff_operations, list)
            or not handoff_operations
            or not isinstance(audits, list)
        ):
            raise OperationRecoveryError(
                "operation-recovery checkpoint continuation evidence is invalid"
            )
        expected = {
            item["operation_id"]: item for item in handoff_operations
        }
        expected_audits = {item["operation_id"]: item for item in audits}
        if set(expected) != set(expected_audits):
            raise OperationRecoveryError(
                "operation-recovery checkpoint continuation audit set differs"
            )
        identifiers = []
        try:
            identifiers = [uuid.UUID(value) for value in expected]
        except (AttributeError, TypeError, ValueError) as error:
            raise OperationRecoveryError(
                "operation-recovery checkpoint continuation IDs are invalid"
            ) from error
        rows = [
            _mapping(row)
            for row in await connection.fetch(
                f"""
                SELECT operation_id::text AS operation_id,
                       operation_type,
                       status,
                       retry_count,
                       encode(
                           sha256(convert_to(task_payload::text, 'UTF8')),
                           'hex'
                       ) AS task_payload_digest,
                       encode(
                           sha256(
                               convert_to(
                                   COALESCE(result_metadata, '{{}}'::jsonb)::text,
                                   'UTF8'
                               )
                           ),
                           'hex'
                       ) AS result_metadata_digest,
                       {EXACT_DRAIN_CHECKPOINT_PROJECTION}
                FROM public.async_operations
                WHERE operation_id = ANY($1::uuid[])
                  AND bank_id = 'engineering'
                  AND status = 'pending'
                  AND task_payload IS NOT NULL
                ORDER BY operation_id
                FOR SHARE
                """,
                self._identifiers,
            )
        ]
        if len(rows) != len(self._selected) or {
            row["operation_id"] for row in rows
        } != set(self._selected):
            raise OperationRecoveryError(
                "operation-recovery checkpoint continuation queue set differs"
            )
        # Resolve the document IDs inside PostgreSQL.  Only counts cross the
        # privileged seam; the actual IDs and content remain server-side.
        side_effect_rows = [
            _mapping(row)
            for row in await connection.fetch(
                """
                WITH selected AS (
                    SELECT operation_id,
                           CASE
                               WHEN jsonb_typeof(
                                   result_metadata->'facts_committed_document_ids'
                               ) = 'array'
                               AND jsonb_array_length(
                                   result_metadata->'facts_committed_document_ids'
                               ) > 0
                               THEN result_metadata->'facts_committed_document_ids'
                               ELSE COALESCE(
                                   CASE
                                       WHEN jsonb_typeof(
                                           result_metadata->'document_ids'
                                       ) = 'array'
                                       THEN result_metadata->'document_ids'
                                       ELSE '[]'::jsonb
                                   END,
                                   '[]'::jsonb
                               )
                           END AS document_ids
                    FROM public.async_operations
                    WHERE operation_id = ANY($1::uuid[])
                      AND bank_id = 'engineering'
                )
                SELECT operation_id::text AS operation_id,
                       jsonb_array_length(document_ids)::integer
                           AS metadata_document_count,
                       (
                           SELECT count(*)::integer
                           FROM public.documents AS d
                           WHERE d.bank_id = 'engineering'
                             AND d.id = ANY(
                                 ARRAY(
                                     SELECT jsonb_array_elements_text(
                                         selected.document_ids
                                     )
                                 )
                             )
                       ) AS document_count,
                       (
                           SELECT count(*)::integer
                           FROM public.memory_units AS u
                           WHERE u.bank_id = 'engineering'
                             AND u.document_id = ANY(
                                 ARRAY(
                                     SELECT jsonb_array_elements_text(
                                         selected.document_ids
                                     )
                                 )
                             )
                       ) AS unit_count
                FROM selected
                ORDER BY operation_id
                """,
                identifiers,
            )
        ]
        if len(side_effect_rows) != len(expected):
            raise OperationRecoveryError(
                "operation-recovery checkpoint continuation side-effect set differs"
            )
        side_effect_by_id = {
            row["operation_id"]: row for row in side_effect_rows
        }
        for row in rows:
            operation_id = row["operation_id"]
            if operation_id not in expected:
                checkpoint = _exact_drain_checkpoint_evidence(row)
                if checkpoint is not None and (
                    checkpoint["facts_committed"]
                    or checkpoint["committed_document_count"]
                    or checkpoint["unit_ids_count"]
                ):
                    raise OperationRecoveryError(
                        "operation-recovery checkpoint continuation handoff omits committed row"
                    )
                continue
            operation = expected[operation_id]
            checkpoint = operation["checkpoint"]
            audit = expected_audits[operation_id]
            if (
                row["operation_type"] != "retain"
                or row["status"] != "pending"
                or row["retry_count"] != operation["retry_count"]
                or row["task_payload_digest"]
                != operation["task_payload_digest"]
                or row["result_metadata_digest"]
                != operation["result_metadata_digest"]
                or _exact_drain_checkpoint_evidence(row)
                != checkpoint
            ):
                raise OperationRecoveryError(
                    "operation-recovery checkpoint continuation row differs"
                )
            observed = side_effect_by_id.get(operation_id)
            if (
                observed is None
                or observed["metadata_document_count"]
                != checkpoint["committed_document_count"]
                or observed["document_count"]
                != checkpoint["committed_document_count"]
                # ``unit_ids_count`` is recorded for this attempt.  Retries
                # may reuse a document id whose durable row contains facts
                # from an earlier successful attempt, so require at least the
                # checkpointed count while retaining the exact document
                # cardinality check above.
                or observed["unit_count"] < checkpoint["unit_ids_count"]
                or audit["document_count"] != observed["document_count"]
                or audit["unit_count"] != observed["unit_count"]
            ):
                raise OperationRecoveryError(
                    "operation-recovery checkpoint continuation side effects differ"
                )

    async def _verify_initial_state(self, connection: Any) -> None:
        self._pending_progress_stages = {}
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
        terminal_status_evidence = getattr(
            self,
            "_terminal_status_evidence",
            None,
        )
        if (
            terminal_status_evidence is not None
            and generation != terminal_status_evidence["generation"]
        ):
            raise OperationRecoveryError(
                "operation-recovery terminal generation evidence differs"
            )
        if not self._resume and generation != self._plan["pre_generation"]:
            raise OperationRecoveryError(
                "operation-recovery exact drain generation drifted"
            )
        rows = await read_safe_operation_rows(
            connection,
            schema="public",
            bank_id="engineering",
            operation_ids=[*self._selected, *self._preserved],
            lock_clause=(
                "" if self._terminal_reconciliation else "FOR SHARE"
            ),
        )
        if len(rows) != len(self._selected) + len(self._preserved):
            raise OperationRecoveryError(
                "operation-recovery exact drain cohort row set changed"
            )
        await self._verify_checkpoint_continuation_state(connection)
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
                if self._resume:
                    self._stage_after_commit(
                        row["operation_id"],
                        status="pending",
                        stage="resume-pending",
                    )
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
            self._stage_after_commit(
                row["operation_id"],
                status=row["status"],
                stage=(
                    "retrying"
                    if row["status"] == "pending"
                    else f"resume-{row['status']}"
                ),
            )
        if getattr(self, "_terminal_reconciliation", False) and any(
            row["operation_id"] in self._selected
            and row["status"] not in {"completed", "failed", "cancelled"}
            for row in rows
        ):
            raise OperationRecoveryError(
                "operation-recovery terminal reconciliation state differs"
            )
        if terminal_status_evidence is not None:
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
                self._identifiers,
            )
            failure_classifications = (
                await read_failure_classifications(
                    connection,
                    schema="public",
                    operation_ids=list(self._selected),
                )
                if self._plan.get("schema_version") in {12, 13, 14, 15}
                else []
            )
            selected_status_counts = {
                status: sum(
                    row["operation_id"] in self._selected
                    and row["status"] == status
                    for row in rows
                )
                for status in (
                    "pending",
                    "processing",
                    "completed",
                    "failed",
                    "cancelled",
                )
            }
            selected_status_counts = {
                key: value
                for key, value in selected_status_counts.items()
                if value
            }
            preserved_status_counts = {
                status: sum(
                    row["operation_id"] in self._preserved
                    and row["status"] == status
                    for row in rows
                )
                for status in (
                    "pending",
                    "processing",
                    "completed",
                    "failed",
                    "cancelled",
                )
            }
            preserved_status_counts = {
                key: value
                for key, value in preserved_status_counts.items()
                if value
            }
            body = _exact_drain_status_body(
                plan=self._plan,
                generation_before=generation,
                generation_after=generation,
                selected_operation_count=len(self._selected),
                selected_status_counts=selected_status_counts,
                preserved_status_counts=preserved_status_counts,
                outside_nonterminal_counts=[
                    {
                        "bank_id": row["bank_id"],
                        "operation_type": row["operation_type"],
                        "status": row["status"],
                        "operation_count": row["operation_count"],
                    }
                    for row in outside_rows
                ],
                failure_classifications=failure_classifications,
                observed_at=terminal_status_evidence["observed_at"],
            )
            if digest(body) != terminal_status_evidence["status_digest"]:
                raise OperationRecoveryError(
                    "operation-recovery terminal status evidence differs"
                )

    async def recover_own_tasks(self, backend: Any) -> int:
        """Recover only exact-plan rows owned by an interrupted capsule."""
        if not self._resume:
            return 0
        if not self._terminal_reconciliation:
            self._assert_execution_lease()
        async with self._mutation_connection(backend) as connection:
            async with self._serializable_mutation_transaction(connection):
                await self._verify_initial_state(connection)
                if self._terminal_reconciliation:
                    self._initial_guard_complete = True
                    return 0
                rows = [
                    _mapping(row)
                    for row in await connection.fetch(
                        f"""
                    SELECT operation_id,
                           operation_type,
                           retry_count,
                           {EXACT_DRAIN_CHECKPOINT_PROJECTION},
                           CASE
                               WHEN error_message IS NULL
                                    OR error_message = ''
                                   THEN 'unclassified_empty'
                               ELSE {FAILURE_CAUSE_FAMILY_SQL}
                           END AS error_cause_family,
                           encode(
                               sha256(
                                   convert_to(
                                       COALESCE(error_message, ''),
                                       'UTF8'
                                   )
                               ),
                               'hex'
                           ) AS error_digest,
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
                ]
                for row in rows:
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
                if self._plan.get("progress_schema_version") == 5:
                    result = await connection.execute(
                        """
                        UPDATE public.async_operations
                        SET status = 'failed',
                            next_retry_at = NULL,
                            error_message = CASE
                                WHEN error_message IS NULL
                                     OR error_message = ''
                                    THEN $3
                                ELSE error_message
                            END,
                            completed_at = NOW(),
                            updated_at = NOW()
                        WHERE operation_id = ANY($1::uuid[])
                          AND bank_id = 'engineering'
                          AND status = 'processing'
                          AND worker_id = $2
                        """,
                        exhausted,
                        self._worker_id,
                        EXACT_DRAIN_RETRY_CEILING_MESSAGE,
                    )
                else:
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
                        EXACT_DRAIN_RETRY_CEILING_MESSAGE,
                    )
                if result != f"UPDATE {len(exhausted)}":
                    raise OperationRecoveryError(
                        "operation-recovery exact drain recovery count differs"
                    )
                self._started_ids.update(str(value) for value in identifiers)
                self._initial_guard_complete = True
        self._flush_pending_progress_stages()
        for identifier in retryable:
            self._record_task_stage(
                str(identifier),
                status="pending",
                stage="recovered",
            )
        for identifier in exhausted:
            row = next(
                item
                for item in rows
                if item["operation_id"] == identifier
            )
            if (
                self._plan.get("progress_schema_version") == 5
                and row["error_cause_family"] == "unclassified_empty"
            ):
                failure = _exact_drain_failure_evidence(
                    EXACT_DRAIN_RETRY_CEILING_MESSAGE,
                    retryable=False,
                    category_override="retry_ceiling",
                    progress_schema_version=5,
                )
            elif self._plan.get("progress_schema_version") == 5:
                failure = _exact_drain_closed_cause_evidence(
                    row["error_cause_family"],
                    row["error_digest"],
                    retryable=False,
                )
            else:
                failure = _exact_drain_failure_evidence(
                    EXACT_DRAIN_RETRY_CEILING_MESSAGE,
                    retryable=False,
                    category_override="retry_ceiling",
                    progress_schema_version=self._plan[
                        "progress_schema_version"
                    ],
                )
            self._record_task_outcome(
                str(identifier),
                status="failed",
                stage="retry-ceiling",
                failure=failure,
                checkpoint=_exact_drain_checkpoint_evidence(row),
            )
        return len(identifiers)

    async def release_own_tasks(self, backend: Any) -> int:
        """Release only exact-plan rows owned by this worker on shutdown."""
        async with self._mutation_connection(backend) as connection:
            async with self._serializable_mutation_transaction(
                connection,
                allow_expired_cleanup=True,
            ):
                if self._terminal_reconciliation:
                    await self._verify_initial_state(connection)
                    return 0
                await self._verify_unstarted_state(connection)
                rows = [
                    _mapping(row)
                    for row in await connection.fetch(
                        """
                        SELECT operation_id::text AS operation_id,
                               bank_id,
                               operation_type,
                               retry_count,
                               encode(
                                   sha256(convert_to(task_payload::text, 'UTF8')),
                                   'hex'
                               ) AS task_payload_digest
                        FROM public.async_operations
                        WHERE status = 'processing'
                          AND worker_id = $1
                        ORDER BY operation_id
                        FOR UPDATE
                        """,
                        self._worker_id,
                    )
                ]
                for row in rows:
                    item = self._selected.get(row["operation_id"])
                    if (
                        item is None
                        or row["bank_id"] != "engineering"
                        or row["operation_type"] != item["operation_type"]
                        or row["task_payload_digest"]
                        != item["task_payload_digest"]
                        or type(row["retry_count"]) is not int
                        or row["retry_count"] < 0
                        or row["retry_count"] > self._max_retries
                    ):
                        raise OperationRecoveryError(
                            "operation-recovery exact drain shutdown row drifted"
                        )
                identifiers = [uuid.UUID(row["operation_id"]) for row in rows]
                result = await connection.execute(
                    """
                    UPDATE public.async_operations
                    SET status = 'pending',
                        worker_id = NULL,
                        claimed_at = NULL,
                        updated_at = NOW()
                    WHERE operation_id = ANY($1::uuid[])
                      AND bank_id = 'engineering'
                      AND status = 'processing'
                      AND worker_id = $2
                    """,
                    identifiers,
                    self._worker_id,
                )
                if result != f"UPDATE {len(identifiers)}":
                    raise OperationRecoveryError(
                        "operation-recovery exact drain shutdown count differs"
                    )
                self._started_ids.update(row["operation_id"] for row in rows)
                self._initial_guard_complete = True
        for row in rows:
            self._record_task_stage(
                row["operation_id"],
                status="pending",
                stage="released",
            )
        return len(rows)

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
        observed_at = self._assert_claim_capable_mutation()
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
        async with self._mutation_connection(backend) as connection:
            async with self._serializable_mutation_transaction(connection):
                await self._verify_unstarted_state(connection)
                row_value = await connection.fetchrow(
                    f"""
                    SELECT operation_id::text AS operation_id,
                           operation_type,
                           status,
                           worker_id,
                           retry_count,
                           {EXACT_DRAIN_CHECKPOINT_PROJECTION},
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
                retry_ceiling = row["retry_count"] >= self._max_retries
                if not retry_ceiling:
                    next_retry_at = self._validated_reschedule_time(
                        next_retry_at,
                        observed_at=observed_at,
                    )
                if retry_ceiling:
                    terminal_error = (
                        EXACT_DRAIN_RETRY_CEILING_MESSAGE
                        if error_message is None
                        else _postgres_safe_error_text(error_message)
                    )
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
                        terminal_error,
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
                        _postgres_safe_error_text(error_message),
                        self._worker_id,
                    )
                if result != "UPDATE 1":
                    raise OperationRecoveryError(
                        "operation-recovery exact drain retry count differs"
                    )
                self._started_ids.add(str(identifier))
                self._initial_guard_complete = True
        failure_message = (
            EXACT_DRAIN_RETRY_CEILING_MESSAGE
            if retry_ceiling and error_message is None
            else error_message
        )
        self._record_task_outcome(
            str(identifier),
            status="failed" if retry_ceiling else "pending",
            stage="retry-ceiling" if retry_ceiling else "retrying",
            failure=(
                None
                if failure_message is None
                else _exact_drain_failure_evidence(
                    failure_message,
                    retryable=not retry_ceiling,
                    category_override=(
                        "retry_ceiling"
                        if retry_ceiling
                        and self._plan.get("progress_schema_version") != 5
                        else None
                    ),
                    progress_schema_version=self._plan[
                        "progress_schema_version"
                    ],
                )
            ),
            checkpoint=_exact_drain_checkpoint_evidence(row),
        )

    async def schedule_retry(
        self,
        backend: Any,
        operation_id: str,
        retry_at: Any,
        error_message: str,
        schema: str | None,
    ) -> None:
        """Schedule one exact owned retry without relinquishing authority."""
        if not isinstance(error_message, str) or (
            getattr(self, "_plan", {}).get("progress_schema_version") == 5
            and not error_message.strip()
        ):
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
        if not isinstance(reason, str) or (
            getattr(self, "_plan", {}).get("progress_schema_version") == 5
            and not reason.strip()
        ):
            raise OperationRecoveryError(
                "operation-recovery exact drain defer reason is invalid"
            )
        await self._reschedule_owned_task(
            backend,
            operation_id,
            exec_date,
            error_message=(
                reason
                if getattr(self, "_plan", {}).get("progress_schema_version")
                == 5
                else None
            ),
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
        self._assert_claim_capable_mutation()
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
        observed_status: str | None = None
        completion_ready = False
        async with self._mutation_connection(backend) as connection:
            async with self._serializable_mutation_transaction(connection):
                await self._verify_unstarted_state(connection)
                row_value = await connection.fetchrow(
                    f"""
                    SELECT operation_type,
                           status,
                           worker_id,
                           {EXACT_DRAIN_CHECKPOINT_PROJECTION},
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
                    observed_status = row["status"]
                elif row["status"] != "processing":
                    raise OperationRecoveryError(
                        "operation-recovery exact drain terminal row drifted"
                    )
                elif error_message is None:
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
                    observed_status = "completed"
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
                        _postgres_safe_error_text(error_message),
                        self._worker_id,
                    )
                    observed_status = "failed"
                if observed_status not in {"completed", "failed", "cancelled"}:
                    raise OperationRecoveryError(
                        "operation-recovery exact drain terminal status differs"
                    )
                if row["status"] == "processing" and result != "UPDATE 1":
                    raise OperationRecoveryError(
                        "operation-recovery exact drain terminal count differs"
                    )
                self._started_ids.add(str(identifier))
                self._initial_guard_complete = True
                completion_ready = await self._selected_rows_are_terminal(
                    connection
                )
        self._record_task_outcome(
            str(identifier),
            status=observed_status,
            stage=observed_status,
            failure=(
                None
                if error_message is None
                else _exact_drain_failure_evidence(
                    error_message,
                    retryable=False,
                    progress_schema_version=self._plan[
                        "progress_schema_version"
                    ],
                )
            ),
            checkpoint=_exact_drain_checkpoint_evidence(row),
        )
        if completion_ready:
            self._signal_completion()

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
        if not isinstance(error_message, str) or (
            getattr(self, "_plan", {}).get("progress_schema_version") == 5
            and not error_message.strip()
        ):
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

    async def _selected_rows_are_terminal(self, connection: Any) -> bool:
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
        terminal = {"completed", "failed", "cancelled"}
        return all(row["status"] in terminal for row in statuses)

    def _signal_completion(self) -> None:
        if self._completion_signalled or self._completion_callback is None:
            return
        self._completion_signalled = True
        self.record_worker_stage(
            status="running",
            stage="worker.shutdown.requested",
        )
        self._completion_callback()

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
        if not getattr(self, "_terminal_reconciliation", False):
            self._assert_execution_lease()
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
        await connection.execute(
            "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"
        )
        await self._configure_mutation_transaction(connection)
        if self._terminal_reconciliation:
            await self._verify_initial_state(connection)
            self._initial_guard_complete = True
            self._terminal_reconciliation_ready = True
            return []
        if capacity == 0:
            return []
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
        chosen = self._choose_rows(safe_rows, reserved_limits, shared_limit)
        for row in chosen:
            row["task_payload"] = self._canonical_task_payload(row)
        if not chosen:
            self._initial_guard_complete = True
            if await self._selected_rows_are_terminal(connection):
                self._signal_completion()
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
        self._initial_guard_complete = True
        return chosen


def _interrupted_progress_rows(
    plan: Mapping[str, Any],
    progress: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Bind an inactive worker's frozen progress to every selected row."""
    selected_by_id = {
        item["operation_id"]: item
        for item in plan["selected_operations"]
    }
    selected_ids = set(selected_by_id)
    tasks = progress.get("tasks")
    observed_counts: dict[str, int] = {}
    evidence: dict[str, dict[str, Any]] = {}
    if (
        plan.get("schema_version") not in {12, 13, 14, 15}
        or progress.get("plan_digest") != plan.get("plan_digest")
        or (
            plan.get("schema_version") == 15
            and (
                progress.get("grant_id") != plan.get("grant_id")
                or progress.get("grant_digest") != plan.get("grant_digest")
            )
        )
        or not isinstance(tasks, list)
        or not isinstance(progress.get("selected_status_counts"), Mapping)
    ):
        raise OperationRecoveryError(
            "operation-recovery interrupted progress evidence differs"
        )
    for task in tasks:
        if not isinstance(task, Mapping):
            raise OperationRecoveryError(
                "operation-recovery interrupted progress evidence differs"
            )
        operation_id = task.get("operation_id")
        status = task.get("status")
        row_digest = task.get("row_digest")
        selected = selected_by_id.get(operation_id)
        if (
            operation_id not in selected_ids
            or operation_id in evidence
            or status
            not in {"pending", "processing", "completed", "failed", "cancelled"}
            or not isinstance(row_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", row_digest) is None
            or selected is None
            or row_digest != selected["row_digest"]
        ):
            raise OperationRecoveryError(
                "operation-recovery interrupted progress evidence differs"
            )
        evidence[operation_id] = {
            "status": status,
            "row_digest": row_digest,
            "stage": task.get("stage"),
        }
        summary_status = (
            "retrying"
            if status == "pending" and task.get("stage") == "retrying"
            else status
        )
        observed_counts[summary_status] = (
            observed_counts.get(summary_status, 0) + 1
        )
    if (
        set(evidence) != selected_ids
        or observed_counts != dict(progress["selected_status_counts"])
    ):
        raise OperationRecoveryError(
            "operation-recovery interrupted progress evidence differs"
        )
    return evidence


async def read_exact_drain_status(
    connection: Any,
    *,
    profile_id: str,
    schema: str,
    plan: Mapping[str, Any],
    interrupted_progress: Mapping[str, Any] | None = None,
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
    interrupted_rows = (
        None
        if interrupted_progress is None
        else _interrupted_progress_rows(verified, interrupted_progress)
    )
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
        failure_classifications = (
            await read_failure_classifications(
                connection,
                schema=schema,
                operation_ids=selected_ids,
            )
            if verified["schema_version"] in {12, 13, 14, 15}
            else []
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
        if operation_id in selected:
            row_digest = live_row_digest(row)
            if interrupted_rows is not None:
                interrupted = interrupted_rows[operation_id]
                if (
                    row["status"] != interrupted["status"]
                    or (
                        row_digest
                        != snapshot_by_id[operation_id]["row_digest"]
                        and row["worker_id_digest"] != worker_digest
                        and not (
                            row["status"] == "pending"
                            and row["worker_id_digest"] is None
                            and interrupted["stage"] == "released"
                        )
                    )
                ):
                    raise OperationRecoveryError(
                        "operation-recovery exact drain selected row ownership drifted"
                    )
            elif (
                row_digest != snapshot_by_id[operation_id]["row_digest"]
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
    body = _exact_drain_status_body(
        plan=verified,
        generation_before=generation_before,
        generation_after=generation_after,
        selected_operation_count=len(selected),
        selected_status_counts=selected_status_counts,
        preserved_status_counts=preserved_status_counts,
        outside_nonterminal_counts=outside,
        failure_classifications=failure_classifications,
        observed_at=int(time.time()),
    )
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


async def apply_post_abort_recovery_transaction(
    connection: Any,
    *,
    profile_id: str,
    schema: str,
    bank_id: str,
    plan: Mapping[str, Any],
    on_mutation_attempt: Callable[[], None] | None = None,
) -> tuple[str, str]:
    """Reset only the exact stopped-drain rows in one serializable CAS."""
    verified = verify_post_abort_recovery_plan(plan)
    if profile_id != "systalyze" or schema != "public" or bank_id != "engineering":
        raise OperationRecoveryError(
            "operation-recovery post-abort target is invalid"
        )
    quoted_schema = _quoted_identifier(schema, "database schema")
    quoted_generation = _quoted_identifier(
        GENERATION_TABLE,
        "migration generation table",
    )
    selected = {
        item["operation_id"]: item for item in verified["selected_operations"]
    }
    retry_by_id = (
        {
            item["operation_id"]: item
            for item in verified["retry_recovery"]["operations"]
        }
        if verified["schema_version"] in {10, 11, 12, 13}
        else {}
    )
    if retry_by_id and set(retry_by_id) != set(selected):
        raise OperationRecoveryError(
            "operation-recovery post-abort retry row set differs"
        )
    snapshot = {
        item["operation_id"]: item
        for item in verified["live_snapshot"]["operations"]
    }
    selected_identifiers = [uuid.UUID(value) for value in selected]
    expires_at = verified["expires_at"]
    transaction_expires_at = min(
        expires_at,
        int(time.time()) + verified["transaction_timeout_seconds"],
    )
    _assert_transaction_deadline(expires_at)
    async with connection.transaction(isolation="serializable"):
        await _configure_transaction_deadline(
            connection,
            transaction_expires_at,
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
        if generation_before != verified["pre_generation"]:
            raise OperationRecoveryError(
                "operation-recovery post-abort generation drifted"
            )
        if await read_generation(connection, schema, profile_id) != generation_before:
            raise OperationRecoveryError(
                "operation-recovery post-abort generation authority differs"
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
                "operation-recovery post-abort requires exclusive database access"
            )
        unexpected_blocker = await connection.fetchval(
            f"""
            SELECT EXISTS (
                SELECT 1
                FROM {quoted_schema}.async_operations
                WHERE (
                    status = 'processing'
                    AND NOT (operation_id = ANY($1::uuid[]))
                ) OR (
                    status IN ('pending', 'failed', 'cancelled')
                    AND (worker_id IS NOT NULL OR claimed_at IS NOT NULL)
                    AND NOT (operation_id = ANY($1::uuid[]))
                )
            )
            """,
            selected_identifiers,
        )
        if unexpected_blocker is not False:
            raise OperationRecoveryError(
                "operation-recovery post-abort queue guard differs"
            )
        rows = await read_safe_operation_rows(
            connection,
            schema=schema,
            bank_id=bank_id,
            operation_ids=list(snapshot),
            lock_clause="FOR UPDATE",
        )
        rows_by_id = {row["operation_id"]: row for row in rows}
        if set(rows_by_id) != set(snapshot) or any(
            live_row_digest(rows_by_id[operation_id])
            != snapshot[operation_id]["row_digest"]
            for operation_id in snapshot
        ):
            raise OperationRecoveryError(
                "operation-recovery post-abort cohort drifted"
            )
        before = {key: dict(value) for key, value in rows_by_id.items()}
        await _configure_transaction_deadline(
            connection,
            transaction_expires_at,
        )
        if on_mutation_attempt is not None:
            on_mutation_attempt()
        failed_owner_predicate = (
            "worker_id IS NOT NULL "
            "AND encode(sha256(convert_to(worker_id, 'UTF8')), 'hex') = $3 "
            "AND claimed_at IS NOT NULL"
            if verified["schema_version"] in {5, 6, 7, 8, 9, 10, 11, 12, 13}
            else "worker_id IS NULL AND claimed_at IS NULL"
        )
        result = await connection.execute(
            f"""
            UPDATE {quoted_schema}.async_operations
            SET status = 'pending',
                error_message = CASE
                    WHEN status = 'failed' THEN NULL ELSE error_message END,
                completed_at = CASE
                    WHEN status = 'failed' THEN NULL ELSE completed_at END,
                next_retry_at = CASE
                    WHEN status = 'failed' THEN NULL ELSE next_retry_at END,
                retry_count = CASE
                    WHEN status = 'failed' THEN 0 ELSE retry_count END,
                worker_id = NULL,
                claimed_at = NULL,
                updated_at = NOW()
            WHERE operation_id = ANY($1::uuid[])
              AND bank_id = $2
              AND (
                    (status = 'processing'
                     AND encode(
                         sha256(convert_to(worker_id, 'UTF8')),
                         'hex'
                     ) = $3)
                    OR (status = 'failed'
                        AND {failed_owner_predicate})
                    OR (status = 'pending'
                        AND $4
                        AND encode(
                            sha256(convert_to(worker_id, 'UTF8')),
                            'hex'
                        ) = $3
                        AND claimed_at IS NOT NULL)
              )
            """,
            selected_identifiers,
            bank_id,
            verified["reference_worker_id_digest"],
            verified["schema_version"] in {7, 9, 10, 11, 12, 13},
        )
        if result != f"UPDATE {len(selected)}":
            raise OperationRecoveryError(
                "operation-recovery post-abort row count differs"
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
                "operation-recovery post-abort generation did not advance once"
            )
        post_rows = await read_safe_operation_rows(
            connection,
            schema=schema,
            bank_id=bank_id,
            operation_ids=list(snapshot),
        )
        post = {row["operation_id"]: row for row in post_rows}
        if set(post) != set(snapshot):
            raise OperationRecoveryError(
                "operation-recovery post-abort post-state is incomplete"
            )
        for operation_id, prior in before.items():
            after = post[operation_id]
            item = selected.get(operation_id)
            if item is None:
                if live_row_digest(after) != live_row_digest(prior):
                    raise OperationRecoveryError(
                        "operation-recovery post-abort preserved row changed"
                    )
                continue
            if (
                after["status"] != "pending"
                or after["worker_id_present"]
                or after["worker_id_digest"] is not None
                or after["claimed_at"] is not None
                or after["task_payload_digest"] != item["task_payload_digest"]
                or after["result_metadata_digest"]
                != prior["result_metadata_digest"]
                or after["updated_at"] == prior["updated_at"]
            ):
                raise OperationRecoveryError(
                    "operation-recovery post-abort post-state differs"
                )
            retry_recovery = retry_by_id.get(operation_id)
            if retry_recovery is not None and (
                prior["retry_count"]
                != retry_recovery["retry_count_before"]
                or after["retry_count"]
                != retry_recovery["retry_count_after"]
                or retry_recovery["reset_applied"]
                != (item["expected_status"] == "failed")
            ):
                raise OperationRecoveryError(
                    "operation-recovery post-abort retry state differs"
                )
            if item["expected_status"] == "processing" and any(
                after[key] != prior[key]
                for key in (
                    "completed_at",
                    "retry_count",
                    "next_retry_at",
                    "error_category",
                    "error_digest",
                )
            ):
                raise OperationRecoveryError(
                    "operation-recovery post-abort processing row differs"
                )
            if item["expected_status"] == "pending" and any(
                after[key] != prior[key]
                for key in (
                    "completed_at",
                    "retry_count",
                    "next_retry_at",
                    "error_category",
                    "error_digest",
                )
            ):
                raise OperationRecoveryError(
                    "operation-recovery post-abort pending row differs"
                )
            if item["expected_status"] == "failed" and (
                after["completed_at"] is not None
                or after["retry_count"] != 0
                or after["next_retry_at"] is not None
                or after["error_category"] != "none"
                or after["error_digest"] is not None
            ):
                raise OperationRecoveryError(
                    "operation-recovery post-abort failed row differs"
                )
        await _configure_transaction_deadline(
            connection,
            transaction_expires_at,
        )
        _assert_transaction_deadline(transaction_expires_at)
    return (
        generation_before,
        f"{profile_id}:{schema}:{generation_after_value}",
    )


async def rollback_post_abort_recovery_transaction(
    connection: Any,
    *,
    profile_id: str,
    schema: str,
    bank_id: str,
    plan: Mapping[str, Any],
    application: Mapping[str, Any],
    rollback_record: Mapping[str, Any],
    preimage: Sequence[Mapping[str, Any]] | None,
) -> tuple[str, str]:
    """Restore the exact post-abort preimage before any row is reclaimed."""
    verified = verify_post_abort_recovery_plan(plan, allow_expired=True)
    if profile_id != "systalyze" or schema != "public" or bank_id != "engineering":
        raise OperationRecoveryError(
            "operation-recovery post-abort rollback target is invalid"
        )
    quoted_schema = _quoted_identifier(schema, "database schema")
    quoted_generation = _quoted_identifier(
        GENERATION_TABLE,
        "migration generation table",
    )
    selected = {
        item["operation_id"]: item for item in verified["selected_operations"]
    }
    snapshot = {
        item["operation_id"]: item
        for item in verified["live_snapshot"]["operations"]
    }
    preimage_by_id = (
        {item["operation_id"]: dict(item) for item in preimage}
        if preimage is not None
        else None
    )
    if preimage_by_id is not None and set(preimage_by_id) != set(selected):
        raise OperationRecoveryError(
            "operation-recovery post-abort rollback preimage row set differs"
        )
    rollback_rows = []
    if preimage_by_id is not None:
        for operation_id in sorted(selected):
            item = selected[operation_id]
            row = preimage_by_id[operation_id]
            allowed_preimage_statuses = {"processing", "failed"}
            if verified["schema_version"] in {7, 9, 10, 11, 12, 13}:
                allowed_preimage_statuses.add("pending")
            if (
                row.get("status") != item["expected_status"]
                or row.get("status") not in allowed_preimage_statuses
                or row.get("task_payload_digest") != item["task_payload_digest"]
            ):
                raise OperationRecoveryError(
                    "operation-recovery post-abort rollback preimage differs"
                )
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
    transaction_expires_at = (
        int(time.time()) + verified["transaction_timeout_seconds"]
    )
    async with connection.transaction(isolation="serializable"):
        await _configure_transaction_deadline(
            connection,
            transaction_expires_at,
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
                "operation-recovery post-abort rollback generation drifted"
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
                "operation-recovery post-abort rollback requires exclusive "
                "database access"
            )
        await _configure_transaction_deadline(
            connection,
            transaction_expires_at,
        )
        rows = await read_safe_operation_rows(
            connection,
            schema=schema,
            bank_id=bank_id,
            operation_ids=list(snapshot),
            lock_clause="FOR UPDATE",
        )
        rows_by_id = {row["operation_id"]: row for row in rows}
        if set(rows_by_id) != set(snapshot):
            raise OperationRecoveryError(
                "operation-recovery post-abort rollback cohort differs"
            )
        if generation_before == rollback_record.get("post_generation"):
            if any(
                live_row_digest(rows_by_id[operation_id])
                != snapshot[operation_id]["row_digest"]
                for operation_id in snapshot
            ):
                raise OperationRecoveryError(
                    "operation-recovery post-abort rollback state differs"
                )
            return (
                rollback_record["pre_generation"],
                rollback_record["post_generation"],
            )
        if preimage_by_id is None:
            raise OperationRecoveryError(
                "operation-recovery post-abort rollback preimage is required"
            )
        for operation_id, row in rows_by_id.items():
            item = selected.get(operation_id)
            if item is None:
                if live_row_digest(row) != snapshot[operation_id]["row_digest"]:
                    raise OperationRecoveryError(
                        "operation-recovery post-abort rollback preserved row "
                        "changed"
                    )
                continue
            before = snapshot[operation_id]
            if (
                row["status"] != "pending"
                or row["worker_id_present"]
                or row["worker_id_digest"] is not None
                or row["claimed_at"] is not None
                or row["task_payload_digest"] != item["task_payload_digest"]
                or row["result_metadata_digest"]
                != before["result_metadata_digest"]
            ):
                raise OperationRecoveryError(
                    "operation-recovery post-abort rollback is no longer safe"
                )
            if item["expected_status"] == "processing" and (
                row["retry_count"] != before["retry_count"]
                or row["completed_at"] != before["completed_at"]
                or row["next_retry_at"] != before["next_retry_at"]
                or row["error_digest"] != before["error_digest"]
            ):
                raise OperationRecoveryError(
                    "operation-recovery post-abort rollback is no longer safe"
                )
            if item["expected_status"] == "pending" and (
                row["retry_count"] != before["retry_count"]
                or row["completed_at"] != before["completed_at"]
                or row["next_retry_at"] != before["next_retry_at"]
                or row["error_category"] != before["error_category"]
                or row["error_digest"] != before["error_digest"]
            ):
                raise OperationRecoveryError(
                    "operation-recovery post-abort rollback is no longer safe"
                )
            if item["expected_status"] == "failed" and (
                row["retry_count"] != 0
                or row["completed_at"] is not None
                or row["next_retry_at"] is not None
                or row["error_category"] != "none"
                or row["error_digest"] is not None
            ):
                raise OperationRecoveryError(
                    "operation-recovery post-abort rollback is no longer safe"
                )
        await _configure_transaction_deadline(
            connection,
            transaction_expires_at,
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
                "operation-recovery post-abort rollback row count differs"
            )
        generation_after_value = await connection.fetchval(
            f"""
            SELECT generation
            FROM {quoted_schema}.{quoted_generation}
            WHERE singleton
            """
        )
        generation_after = f"{profile_id}:{schema}:{generation_after_value}"
        if (
            generation_after_value != generation_value + 1
            or generation_after != rollback_record.get("post_generation")
        ):
            raise OperationRecoveryError(
                "operation-recovery post-abort rollback generation differs"
            )
        post_rows = await read_safe_operation_rows(
            connection,
            schema=schema,
            bank_id=bank_id,
            operation_ids=list(snapshot),
        )
        post = {row["operation_id"]: row for row in post_rows}
        if set(post) != set(snapshot) or any(
            live_row_digest(post[operation_id])
            != snapshot[operation_id]["row_digest"]
            for operation_id in snapshot
        ):
            raise OperationRecoveryError(
                "operation-recovery post-abort rollback verification differs"
            )
        await _configure_transaction_deadline(
            connection,
            transaction_expires_at,
        )
        _assert_transaction_deadline(transaction_expires_at)
    return generation_before, generation_after


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
