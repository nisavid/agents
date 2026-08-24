"""Private payload-free progress evidence for an exact operation drain."""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time
from typing import Any, Callable, Mapping, Sequence
import unicodedata

from .canonical import digest, strict_json_loads
from .operation_recovery import (
    OperationRecoveryError,
    exact_drain_progress_archive_path,
)


SHA256 = re.compile(r"[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
STAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/=-]{0,127}\Z")
OPERATION_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
MAX_PROGRESS_BYTES = 1024 * 1024
TASK_STATUSES = frozenset(
    {"pending", "processing", "completed", "failed", "cancelled"}
)
WORKER_STATUSES = frozenset({"starting", "running", "failed"})
SUMMARY_STATUSES = TASK_STATUSES | {"retrying"}
OPERATION_TYPES = frozenset(
    {"retain", "refresh_mental_model", "consolidation"}
)
SCOPE_CATEGORIES = frozenset(
    {"retain", "consolidation", "reflect", "default"}
)
PROVIDER_OUTCOMES = frozenset({"succeeded", "failed", "timed_out"})
PROVIDER_OUTCOMES_V4 = frozenset(
    {
        "succeeded",
        "failed",
        "queue_timed_out",
        "execution_timed_out",
    }
)
PROVIDER_OUTCOMES_V5 = PROVIDER_OUTCOMES_V4 | {
    "queue_cancelled",
    "execution_cancelled",
}
TASK_FAILURE_CATEGORIES_V4 = frozenset(
    {
        "phase_one_timeout",
        "provider_queue_timeout",
        "provider_execution_timeout",
        "operation_attempt_timeout",
        "provider_bad_request",
        "provider_authentication",
        "provider_capacity",
        "provider_transport",
        "retry_ceiling",
        "terminal_state_persistence",
        "nonquiescent_shutdown",
        "operation_error",
        "unclassified_empty",
        "worker_initialization",
        "worker_initialization_timeout",
        "worker_runtime_failure",
    }
)
TASK_FAILURE_CATEGORIES_V5 = TASK_FAILURE_CATEGORIES_V4 | {
    "upstream_timeout",
    "database_statement_timeout",
}
WORKER_FAILURE_CATEGORIES_V4 = TASK_FAILURE_CATEGORIES_V4 | {
    "execution_lease_expired"
}
WORKER_FAILURE_CATEGORIES_V5 = TASK_FAILURE_CATEGORIES_V5 | {
    "execution_lease_expired"
}


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise OperationRecoveryError(f"{label} is invalid")
    return value


def _scope_category(scope: str) -> str:
    if scope.startswith("retain"):
        return "retain"
    if scope.startswith("consolidation"):
        return "consolidation"
    if scope.startswith("reflect"):
        return "reflect"
    return "default"


def _timestamp(value: Any, label: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise OperationRecoveryError(f"{label} is invalid")
    return float(value)


def _count(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise OperationRecoveryError(f"{label} is invalid")
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        raise OperationRecoveryError(f"{label} is invalid")
    return value


def _validated_failure(
    value: Any,
    *,
    categories: frozenset[str],
) -> dict[str, Any] | None:
    if value is None:
        return None
    keys = {"category", "retryable", "http_status", "error_digest"}
    if not isinstance(value, Mapping) or set(value) != keys:
        raise OperationRecoveryError("exact drain failure evidence is invalid")
    category = value.get("category")
    retryable = value.get("retryable")
    http_status = value.get("http_status")
    if (
        category not in categories
        or type(retryable) is not bool
        or (
            http_status is not None
            and (type(http_status) is not int or not 400 <= http_status <= 599)
        )
    ):
        raise OperationRecoveryError("exact drain failure evidence is invalid")
    return {
        "category": category,
        "retryable": retryable,
        "http_status": http_status,
        "error_digest": _sha(
            value.get("error_digest"),
            "exact drain failure error digest",
        ),
    }


def _failure_categories(
    progress_schema_version: int,
    *,
    worker: bool = False,
) -> frozenset[str]:
    if progress_schema_version == 5:
        return (
            WORKER_FAILURE_CATEGORIES_V5
            if worker
            else TASK_FAILURE_CATEGORIES_V5
        )
    return (
        WORKER_FAILURE_CATEGORIES_V4
        if worker
        else TASK_FAILURE_CATEGORIES_V4
    )


def _validated_checkpoint(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    body_keys = {
        "facts_committed",
        "committed_document_count",
        "unit_ids_count",
        "stage",
        "processed",
        "total",
    }
    if not isinstance(value, Mapping) or frozenset(value) not in {
        frozenset(body_keys),
        frozenset(body_keys | {"checkpoint_digest"}),
    }:
        raise OperationRecoveryError("exact drain checkpoint evidence is invalid")
    stage = value.get("stage")
    if (
        type(value.get("facts_committed")) is not bool
        or not isinstance(stage, str)
        or STAGE.fullmatch(stage) is None
    ):
        raise OperationRecoveryError("exact drain checkpoint evidence is invalid")
    body = {
        "facts_committed": value["facts_committed"],
        "committed_document_count": _count(
            value.get("committed_document_count"),
            "exact drain committed document count",
        ),
        "unit_ids_count": _count(
            value.get("unit_ids_count"),
            "exact drain committed unit count",
        ),
        "stage": stage,
        "processed": _count(
            value.get("processed"),
            "exact drain checkpoint processed count",
        ),
        "total": _count(
            value.get("total"),
            "exact drain checkpoint total count",
        ),
    }
    if body["processed"] > body["total"]:
        raise OperationRecoveryError("exact drain checkpoint evidence is invalid")
    checkpoint_digest = digest(body)
    if (
        "checkpoint_digest" in value
        and _sha(
            value.get("checkpoint_digest"),
            "exact drain checkpoint digest",
        )
        != checkpoint_digest
    ):
        raise OperationRecoveryError("exact drain checkpoint digest differs")
    return {**body, "checkpoint_digest": checkpoint_digest}


def _trusted_parent(path: Path) -> None:
    try:
        resolved = path.parent.resolve(strict=True)
        metadata = path.parent.lstat()
    except OSError as error:
        raise OperationRecoveryError("exact drain progress directory is unavailable") from error
    if (
        resolved != path.parent
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o022
    ):
        raise OperationRecoveryError("exact drain progress directory is untrusted")


def _commit_create_only(temporary: Path, target: Path) -> None:
    """Atomically install one immutable artifact without a hard-link window."""
    if temporary.parent != target.parent:
        raise OSError("progress artifact temporary directory differs")
    library = ctypes.CDLL(None, use_errno=True)
    directory = os.open(
        target.parent,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0),
    )
    try:
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
                directory,
                os.fsencode(temporary.name),
                directory,
                os.fsencode(target.name),
                exclusive,
            )
            != 0
        ):
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number), target)
    except AttributeError as error:
        raise OSError("atomic conditional rename is unavailable") from error
    finally:
        os.close(directory)


def _write_private(
    path: Path,
    value: Mapping[str, Any],
    *,
    create_only: bool = False,
) -> None:
    if not path.is_absolute():
        raise OperationRecoveryError("exact drain progress path must be absolute")
    payload = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(payload) > MAX_PROGRESS_BYTES:
        raise OperationRecoveryError("exact drain progress is too large")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _trusted_parent(path)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("progress write made no progress")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        if create_only:
            _commit_create_only(temporary, path)
        else:
            os.replace(temporary, path)
        directory_descriptor = os.open(
            path.parent,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as error:
        raise OperationRecoveryError("exact drain progress is unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_private(path: Path) -> Mapping[str, Any]:
    descriptor = -1
    try:
        _trusted_parent(path)
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
        observed = path.lstat()
        if (
            path.resolve(strict=True) != path
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o077
            or metadata.st_nlink != 1
            or metadata.st_size > MAX_PROGRESS_BYTES
            or (metadata.st_dev, metadata.st_ino)
            != (observed.st_dev, observed.st_ino)
        ):
            raise OperationRecoveryError("exact drain progress is untrusted")
        payload = os.read(descriptor, metadata.st_size + 1)
        after = os.fstat(descriptor)
        current = path.lstat()
        if (
            len(payload) != metadata.st_size
            or (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or (after.st_dev, after.st_ino)
            != (current.st_dev, current.st_ino)
        ):
            raise OperationRecoveryError("exact drain progress changed while reading")
    except OSError as error:
        raise OperationRecoveryError("exact drain progress is unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        value = strict_json_loads(payload)
    except (UnicodeError, ValueError) as error:
        raise OperationRecoveryError("exact drain progress is invalid") from error
    if not isinstance(value, Mapping):
        raise OperationRecoveryError("exact drain progress is invalid")
    return value


class ExactDrainProgressRecorder:
    """Persist bounded task and provider progress without content or error text."""

    def __init__(
        self,
        *,
        path: Path,
        plan_digest: str,
        worker_pid: int,
        worker_start_time: str,
        worker_attempt: int,
        selected_operations: Sequence[Mapping[str, Any]],
        prior_attempts: Sequence[Mapping[str, Any]] = (),
        initial_tasks: Sequence[Mapping[str, Any]] | None = None,
        progress_schema_version: int = 1,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = path
        self.plan_digest = _sha(plan_digest, "exact drain plan digest")
        if type(worker_pid) is not int or worker_pid <= 1:
            raise OperationRecoveryError("exact drain progress worker PID is invalid")
        if not isinstance(worker_start_time, str) or not worker_start_time:
            raise OperationRecoveryError("exact drain progress worker start is invalid")
        if type(worker_attempt) is not int or worker_attempt < 1:
            raise OperationRecoveryError("exact drain progress worker attempt is invalid")
        self.worker_pid = worker_pid
        self.worker_start_time = worker_start_time
        self.worker_attempt = worker_attempt
        if progress_schema_version not in {1, 2, 3, 4, 5}:
            raise OperationRecoveryError(
                "exact drain progress schema version is invalid"
            )
        self.progress_schema_version = progress_schema_version
        self._clock = clock
        self._lock = threading.RLock()
        self._sequence = 0
        started_at = _timestamp(clock(), "exact drain progress clock")
        self._started_at = started_at
        self._observed_at = started_at
        self._last_progress_at = started_at
        self._worker_status = "starting"
        self._worker_stage = "progress.created"
        self._worker_stage_started_at = started_at
        self._worker_failure_stage: str | None = None
        self._worker_failure: dict[str, Any] | None = None
        self._worker_exit_code: int | None = None
        self._prior_attempts = [dict(item) for item in prior_attempts]
        if len(self._prior_attempts) != worker_attempt - 1:
            raise OperationRecoveryError("exact drain prior attempts differ")
        for expected_attempt, item in enumerate(self._prior_attempts, start=1):
            if (
                set(item) != {"worker_attempt", "progress_digest"}
                or item.get("worker_attempt") != expected_attempt
            ):
                raise OperationRecoveryError("exact drain prior attempts are invalid")
            _sha(item.get("progress_digest"), "exact drain prior progress digest")
        self._tasks: dict[str, dict[str, Any]] = {}
        initial_by_id = {
            str(item.get("operation_id", "")): dict(item)
            for item in (initial_tasks or ())
        }
        for item in selected_operations:
            operation_id = str(item.get("operation_id", ""))
            operation_type = str(item.get("operation_type", ""))
            row_digest = _sha(item.get("row_digest"), "exact drain row digest")
            if (
                OPERATION_ID.fullmatch(operation_id) is None
                or operation_type not in OPERATION_TYPES
                or operation_id in self._tasks
            ):
                raise OperationRecoveryError("exact drain progress task is invalid")
            task = {
                "operation_id": operation_id,
                "operation_digest": hashlib.sha256(operation_id.encode("utf-8")).hexdigest(),
                "row_digest": row_digest,
                "operation_type": operation_type,
                "status": "pending",
                "stage": "queued",
                "total_started_at": started_at,
                "stage_started_at": started_at,
                "last_progress_at": started_at,
                **(
                    {}
                    if progress_schema_version == 1
                    else {
                        "failure_stage": None,
                        "failure": None,
                        "checkpoint": None,
                    }
                ),
            }
            previous = initial_by_id.get(operation_id)
            if previous is not None:
                if (
                    previous.get("row_digest") != row_digest
                    or previous.get("operation_type") != operation_type
                ):
                    raise OperationRecoveryError(
                        "exact drain prior task evidence differs"
                    )
                task.update(
                    status=previous["status"],
                    stage=previous["stage"],
                    total_started_at=previous["total_started_at"],
                    stage_started_at=previous["stage_started_at"],
                    last_progress_at=previous["last_progress_at"],
                )
                if progress_schema_version in {2, 3, 4, 5}:
                    task.update(
                        failure_stage=previous["failure_stage"],
                        failure=previous["failure"],
                        checkpoint=previous["checkpoint"],
                    )
            self._tasks[operation_id] = task
        if set(initial_by_id) - set(self._tasks):
            raise OperationRecoveryError("exact drain prior task set differs")
        carried_high_water = max(
            (
                _timestamp(task[key], "exact drain carried task timestamp")
                for task in self._tasks.values()
                for key in (
                    "total_started_at",
                    "stage_started_at",
                    "last_progress_at",
                )
            ),
            default=started_at,
        )
        self._observed_at = max(self._observed_at, carried_high_water)
        self._last_progress_at = max(
            self._last_progress_at,
            carried_high_water,
        )
        self._provider_counters: dict[str, dict[str, Any]] = {}
        self._active: dict[str, dict[str, Any]] = {}
        self._cooldowns: dict[str, dict[str, Any]] = {}
        self._persist(started_at)

    def _counter(self, provider_id: str) -> dict[str, Any]:
        _identifier(provider_id, "exact drain progress provider")
        if self.progress_schema_version in {4, 5}:
            cancellation_counter = (
                {"queue_cancelled": 0, "execution_cancelled": 0}
                if self.progress_schema_version == 5
                else {}
            )
            return self._provider_counters.setdefault(
                provider_id,
                {
                    "provider_id": provider_id,
                    "started": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "queue_timed_out": 0,
                    "execution_timed_out": 0,
                    **cancellation_counter,
                    "failed_over": 0,
                    "queue_duration_count": 0,
                    "queue_duration_total_seconds": 0.0,
                    "queue_duration_max_seconds": 0.0,
                    "execution_duration_count": 0,
                    "execution_duration_total_seconds": 0.0,
                    "execution_duration_max_seconds": 0.0,
                    "last_provider_response_at": None,
                },
            )
        return self._provider_counters.setdefault(
            provider_id,
            {
                "provider_id": provider_id,
                "started": 0,
                "succeeded": 0,
                "failed": 0,
                "timed_out": 0,
                "failed_over": 0,
                "duration_count": 0,
                "duration_total_seconds": 0.0,
                "duration_max_seconds": 0.0,
                "last_provider_response_at": None,
            },
        )

    def _body(self, observed_at: float) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for task in self._tasks.values():
            summary_status = (
                "retrying"
                if task["status"] == "pending" and task["stage"] == "retrying"
                else task["status"]
            )
            counts[summary_status] = counts.get(summary_status, 0) + 1
        return {
            "schema_version": self.progress_schema_version,
            "kind": "operation-recovery-exact-drain-progress",
            "plan_digest": self.plan_digest,
            "worker_pid": self.worker_pid,
            "worker_start_time": self.worker_start_time,
            "worker_attempt": self.worker_attempt,
            "prior_attempts": self._prior_attempts,
            "started_at": self._started_at,
            "observed_at": observed_at,
            "last_progress_at": self._last_progress_at,
            "selected_status_counts": counts,
            "tasks": [self._tasks[key] for key in sorted(self._tasks)],
            "provider_counters": [
                self._provider_counters[key]
                for key in sorted(self._provider_counters)
            ],
            "active_provider_requests": [
                self._active[key] for key in sorted(self._active)
            ],
            "cooldowns": [self._cooldowns[key] for key in sorted(self._cooldowns)],
            **(
                {}
                if self.progress_schema_version not in {3, 4, 5}
                else {
                    "worker_status": self._worker_status,
                    "worker_stage": self._worker_stage,
                    "worker_stage_started_at": self._worker_stage_started_at,
                    "worker_failure_stage": self._worker_failure_stage,
                    "worker_failure": self._worker_failure,
                    "worker_exit_code": self._worker_exit_code,
                }
            ),
        }

    def _now(self) -> float:
        observed = _timestamp(self._clock(), "exact drain progress clock")
        return max(self._observed_at, observed)

    def _persist(self, observed_at: float) -> None:
        observed_at = max(
            self._observed_at,
            _timestamp(observed_at, "exact drain progress observed time"),
        )
        self._observed_at = observed_at
        body = self._body(observed_at)
        _write_private(self.path, {**body, "progress_digest": digest(body)})

    def task_stage(self, operation_id: str, *, status: str, stage: str) -> None:
        if status not in TASK_STATUSES:
            raise OperationRecoveryError("exact drain progress task stage is invalid")
        if not isinstance(stage, str) or STAGE.fullmatch(stage) is None:
            raise OperationRecoveryError("exact drain progress task stage is invalid")
        with self._lock:
            task = self._tasks.get(operation_id)
            if task is None:
                raise OperationRecoveryError("exact drain progress task is outside plan")
            prior_task = dict(task)
            prior_observed_at = self._observed_at
            prior_last_progress_at = self._last_progress_at
            now = self._now()
            task.update(
                status=status,
                stage=stage,
                stage_started_at=now,
                last_progress_at=now,
            )
            self._last_progress_at = now
            try:
                self._persist(now)
            except BaseException:  # noqa: BLE001
                task.clear()
                task.update(prior_task)
                self._observed_at = prior_observed_at
                self._last_progress_at = prior_last_progress_at
                raise

    def worker_stage(self, *, status: str, stage: str) -> None:
        """Persist a payload-free worker lifecycle breadcrumb before claims."""
        if (
            self.progress_schema_version not in {3, 4, 5}
            or status not in {"starting", "running"}
            or not isinstance(stage, str)
            or STAGE.fullmatch(stage) is None
        ):
            raise OperationRecoveryError(
                "exact drain progress worker stage is invalid"
            )
        with self._lock:
            prior = (
                self._worker_status,
                self._worker_stage,
                self._worker_stage_started_at,
                self._observed_at,
                self._last_progress_at,
            )
            now = self._now()
            self._worker_status = status
            self._worker_stage = stage
            self._worker_stage_started_at = now
            self._last_progress_at = now
            try:
                self._persist(now)
            except BaseException:  # noqa: BLE001
                (
                    self._worker_status,
                    self._worker_stage,
                    self._worker_stage_started_at,
                    self._observed_at,
                    self._last_progress_at,
                ) = prior
                raise

    def worker_failure(
        self,
        *,
        exit_code: int,
        failure: Mapping[str, Any],
    ) -> None:
        """Persist one closed worker failure without raw error content."""
        if self.progress_schema_version not in {3, 4, 5}:
            raise OperationRecoveryError(
                "exact drain progress worker failure schema is unavailable"
            )
        checked_failure = _validated_failure(
            failure,
            categories=_failure_categories(
                self.progress_schema_version,
                worker=True,
            ),
        )
        if (
            checked_failure is None
            or type(exit_code) is not int
            or not 1 <= exit_code <= 255
        ):
            raise OperationRecoveryError(
                "exact drain progress worker failure is invalid"
            )
        with self._lock:
            if self._worker_status == "failed":
                raise OperationRecoveryError(
                    "exact drain progress worker failure is invalid"
                )
            prior = (
                self._worker_status,
                self._worker_stage,
                self._worker_stage_started_at,
                self._worker_failure_stage,
                self._worker_failure,
                self._worker_exit_code,
                self._observed_at,
                self._last_progress_at,
            )
            now = self._now()
            self._worker_failure_stage = self._worker_stage
            self._worker_failure = checked_failure
            self._worker_exit_code = exit_code
            self._worker_status = "failed"
            self._worker_stage = "failed"
            self._worker_stage_started_at = now
            self._last_progress_at = now
            try:
                self._persist(now)
            except BaseException:
                (
                    self._worker_status,
                    self._worker_stage,
                    self._worker_stage_started_at,
                    self._worker_failure_stage,
                    self._worker_failure,
                    self._worker_exit_code,
                    self._observed_at,
                    self._last_progress_at,
                ) = prior
                raise

    def task_processing_stage(self, operation_id: str, *, stage: str) -> None:
        """Update a live breadcrumb without reviving a terminal or released task."""
        if not isinstance(stage, str) or STAGE.fullmatch(stage) is None:
            raise OperationRecoveryError("exact drain progress task stage is invalid")
        with self._lock:
            task = self._tasks.get(operation_id)
            if task is None:
                raise OperationRecoveryError("exact drain progress task is outside plan")
            if task["status"] != "processing":
                return
            now = self._now()
            task.update(
                stage=stage,
                stage_started_at=now,
                last_progress_at=now,
            )
            self._last_progress_at = now
            self._persist(now)

    def task_outcome(
        self,
        operation_id: str,
        *,
        status: str,
        stage: str,
        failure: Mapping[str, Any] | None,
        checkpoint: Mapping[str, Any] | None,
    ) -> None:
        """Persist one closed failure/checkpoint projection without raw content."""
        if self.progress_schema_version not in {2, 3, 4, 5}:
            raise OperationRecoveryError(
                "exact drain failure evidence schema is unavailable"
            )
        if (
            status not in TASK_STATUSES
            or not isinstance(stage, str)
            or STAGE.fullmatch(stage) is None
        ):
            raise OperationRecoveryError(
                "exact drain progress task outcome is invalid"
            )
        checked_failure = _validated_failure(
            failure,
            categories=_failure_categories(self.progress_schema_version),
        )
        checked_checkpoint = _validated_checkpoint(checkpoint)
        with self._lock:
            task = self._tasks.get(operation_id)
            if task is None:
                raise OperationRecoveryError(
                    "exact drain progress task is outside plan"
                )
            failure_stage = task["stage"] if checked_failure is not None else None
            prior_task = dict(task)
            prior_observed_at = self._observed_at
            prior_last_progress_at = self._last_progress_at
            now = self._now()
            task.update(
                status=status,
                stage=stage,
                stage_started_at=now,
                last_progress_at=now,
                failure_stage=failure_stage,
                failure=checked_failure,
                checkpoint=checked_checkpoint,
            )
            self._last_progress_at = now
            try:
                self._persist(now)
            except BaseException:
                task.clear()
                task.update(prior_task)
                self._observed_at = prior_observed_at
                self._last_progress_at = prior_last_progress_at
                raise

    def task_runtime_failure(
        self,
        operation_id: str,
        *,
        stage: str,
        failure: Mapping[str, Any],
    ) -> None:
        """Record a worker/runtime failure while preserving DB checkpoint evidence."""
        if self.progress_schema_version not in {2, 3, 4, 5}:
            raise OperationRecoveryError(
                "exact drain failure evidence schema is unavailable"
            )
        if not isinstance(stage, str) or STAGE.fullmatch(stage) is None:
            raise OperationRecoveryError(
                "exact drain progress runtime failure is invalid"
            )
        checked_failure = _validated_failure(
            failure,
            categories=_failure_categories(self.progress_schema_version),
        )
        if checked_failure is None:
            raise OperationRecoveryError(
                "exact drain progress runtime failure is invalid"
            )
        with self._lock:
            task = self._tasks.get(operation_id)
            if task is None or task["status"] != "processing":
                raise OperationRecoveryError(
                    "exact drain progress runtime failure is invalid"
                )
            prior_task = dict(task)
            prior_observed_at = self._observed_at
            prior_last_progress_at = self._last_progress_at
            now = self._now()
            task.update(
                stage=stage,
                stage_started_at=now,
                last_progress_at=now,
                failure_stage=task["stage"],
                failure=checked_failure,
            )
            self._last_progress_at = now
            try:
                self._persist(now)
            except BaseException:
                task.clear()
                task.update(prior_task)
                self._observed_at = prior_observed_at
                self._last_progress_at = prior_last_progress_at
                raise

    def provider_started(
        self,
        provider_id: str,
        *,
        retry_attempt: int,
        scope: str,
    ) -> str:
        if type(retry_attempt) is not int or retry_attempt < 1:
            raise OperationRecoveryError("exact drain progress retry attempt is invalid")
        with self._lock:
            now = self._now()
            self._sequence += 1
            token = hashlib.sha256(
                f"{self.plan_digest}:{self._sequence}:{provider_id}".encode("utf-8")
            ).hexdigest()
            counter = self._counter(provider_id)
            counter["started"] += 1
            self._active[token] = (
                {
                    "request_digest": token,
                    "provider_id": provider_id,
                    "state": "queued",
                    "queued_at": now,
                    "executing_at": None,
                    "retry_attempt": retry_attempt,
                    "scope_category": _scope_category(scope),
                }
                if self.progress_schema_version in {4, 5}
                else {
                    "request_digest": token,
                    "provider_id": provider_id,
                    "started_at": now,
                    "retry_attempt": retry_attempt,
                    "scope_category": _scope_category(scope),
                }
            )
            self._last_progress_at = now
            self._persist(now)
            return token

    def provider_executing(self, request_digest: str) -> None:
        if self.progress_schema_version not in {4, 5}:
            return
        with self._lock:
            active = self._active.get(request_digest)
            if active is None or active.get("state") != "queued":
                raise OperationRecoveryError(
                    "exact drain progress request is not queued"
                )
            now = self._now()
            active["state"] = "executing"
            active["executing_at"] = now
            self._last_progress_at = now
            self._persist(now)

    def provider_finished(
        self,
        request_digest: str,
        *,
        outcome: str,
        failed_over: bool = False,
    ) -> None:
        allowed_outcomes = (
            PROVIDER_OUTCOMES_V5
            if self.progress_schema_version == 5
            else (
                PROVIDER_OUTCOMES_V4
                if self.progress_schema_version == 4
                else PROVIDER_OUTCOMES
            )
        )
        if outcome not in allowed_outcomes or type(failed_over) is not bool:
            raise OperationRecoveryError("exact drain progress provider outcome is invalid")
        with self._lock:
            active = self._active.get(request_digest)
            if active is None:
                raise OperationRecoveryError("exact drain progress request is unknown")
            if self.progress_schema_version in {4, 5} and (
                (
                    outcome in {"queue_timed_out", "queue_cancelled"}
                    and active["state"] != "queued"
                )
                or (
                    outcome not in {"queue_timed_out", "queue_cancelled"}
                    and active["state"] != "executing"
                )
            ):
                raise OperationRecoveryError(
                    "exact drain progress provider outcome is invalid"
                )
            del self._active[request_digest]
            now = self._now()
            counter = self._counter(active["provider_id"])
            counter[outcome] += 1
            if failed_over:
                counter["failed_over"] += 1
            if self.progress_schema_version in {4, 5}:
                queued_at = float(active["queued_at"])
                executing_at = active["executing_at"]
                queue_duration = max(
                    0.0,
                    float(executing_at if executing_at is not None else now)
                    - queued_at,
                )
                counter["queue_duration_count"] += 1
                counter["queue_duration_total_seconds"] += queue_duration
                counter["queue_duration_max_seconds"] = max(
                    counter["queue_duration_max_seconds"],
                    queue_duration,
                )
                if executing_at is not None:
                    execution_duration = max(0.0, now - float(executing_at))
                    counter["execution_duration_count"] += 1
                    counter["execution_duration_total_seconds"] += (
                        execution_duration
                    )
                    counter["execution_duration_max_seconds"] = max(
                        counter["execution_duration_max_seconds"],
                        execution_duration,
                    )
            else:
                duration = max(0.0, now - float(active["started_at"]))
                counter["duration_count"] += 1
                counter["duration_total_seconds"] += duration
                counter["duration_max_seconds"] = max(
                    counter["duration_max_seconds"], duration
                )
            counter["last_provider_response_at"] = now
            self._last_progress_at = now
            self._persist(now)

    def provider_failed_over(self, provider_id: str) -> None:
        with self._lock:
            _identifier(provider_id, "exact drain progress provider")
            counter = self._provider_counters.get(provider_id)
            if counter is None:
                raise OperationRecoveryError(
                    "exact drain progress failover count differs"
                )
            timeout_count = (
                counter["queue_timed_out"]
                + counter["execution_timed_out"]
                if self.progress_schema_version in {4, 5}
                else counter["timed_out"]
            )
            if counter["failed_over"] >= counter["failed"] + timeout_count:
                raise OperationRecoveryError(
                    "exact drain progress failover count differs"
                )
            now = self._now()
            counter["failed_over"] += 1
            self._last_progress_at = now
            self._persist(now)

    def provider_cancelled(self, request_digest: str) -> None:
        """Close one cancelled request without reporting a provider fault."""
        with self._lock:
            active = self._active.get(request_digest)
            if active is None:
                raise OperationRecoveryError(
                    "exact drain progress request is unknown"
                )
            if self.progress_schema_version == 5:
                outcome = (
                    "queue_cancelled"
                    if active.get("state") == "queued"
                    else "execution_cancelled"
                )
            else:
                outcome = "failed"
            self.provider_finished(request_digest, outcome=outcome)

    def cooldown(self, provider_id: str, *, until: float, reason: str) -> None:
        if reason not in {"usage_limit", "terminal_auth", "transport"}:
            raise OperationRecoveryError("exact drain progress cooldown is invalid")
        _identifier(provider_id, "exact drain progress provider")
        deadline = _timestamp(until, "exact drain progress cooldown")
        with self._lock:
            now = self._now()
            self._cooldowns[provider_id] = {
                "provider_id": provider_id,
                "until": deadline,
                "reason": reason,
            }
            self._last_progress_at = now
            self._persist(now)

    def clear_cooldown(self, provider_id: str) -> None:
        with self._lock:
            if provider_id not in self._cooldowns:
                return
            now = self._now()
            del self._cooldowns[provider_id]
            self._last_progress_at = now
            self._persist(now)


def verify_exact_drain_application_journal(
    *,
    plan: Mapping[str, Any],
    authorization: Mapping[str, Any],
    journal: Mapping[str, Any],
    terminal_reconciliation: bool = False,
    expected_receipt_digest: str | None = None,
    expected_worker_start_time: str | None = None,
) -> dict[str, Any]:
    """Verify one exact worker attempt before activating progress or runtime."""
    journal_keys = {
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
    if not isinstance(journal, Mapping) or set(journal) != journal_keys:
        raise OperationRecoveryError("exact drain application journal is invalid")
    body = {
        key: journal[key]
        for key in journal_keys
        if key != "receipt_digest"
    }
    worker_attempt = journal.get("worker_attempt")
    maximum_attempt = plan.get("worker_max_attempts", 0)
    if (
        type(terminal_reconciliation) is not bool
        or journal.get("schema_version") != 1
        or journal.get("kind")
        != "operation-recovery-exact-drain-application-journal"
        or journal.get("plan_digest") != plan.get("plan_digest")
        or journal.get("authorization_receipt_digest")
        != authorization.get("receipt_digest")
        or journal.get("started_at") != authorization.get("authorized_at")
        or journal.get("worker_pid") != os.getpid()
        or not isinstance(journal.get("worker_start_time"), str)
        or not journal.get("worker_start_time")
        or len(journal["worker_start_time"]) > 128
        or journal["worker_start_time"]
        != " ".join(journal["worker_start_time"].split())
        or type(worker_attempt) is not int
        or (
            terminal_reconciliation
            and not 2 <= worker_attempt <= maximum_attempt + 1
        )
        or (
            not terminal_reconciliation
            and not 1 <= worker_attempt <= maximum_attempt
        )
        or journal.get("receipt_digest") != digest(body)
        or (
            expected_receipt_digest is not None
            and journal.get("receipt_digest") != expected_receipt_digest
        )
        or (
            expected_worker_start_time is not None
            and not hmac.compare_digest(
                journal["worker_start_time"],
                expected_worker_start_time,
            )
        )
    ):
        raise OperationRecoveryError("exact drain application journal is invalid")
    return dict(journal)


def create_exact_drain_progress_recorder(
    *,
    plan: Mapping[str, Any],
    authorization: Mapping[str, Any],
    journal: Mapping[str, Any],
    terminal_reconciliation: bool = False,
    clock: Callable[[], float] = time.time,
) -> ExactDrainProgressRecorder:
    """Create a progress recorder only for this authenticated worker attempt."""
    journal = verify_exact_drain_application_journal(
        plan=plan,
        authorization=authorization,
        journal=journal,
        terminal_reconciliation=terminal_reconciliation,
    )
    worker_attempt = journal["worker_attempt"]
    progress_path = Path(plan["progress_artifact_path"])
    prior_attempts: list[dict[str, Any]] = []
    initial_tasks: Sequence[Mapping[str, Any]] | None = None
    if worker_attempt == 1:
        if progress_path.exists():
            raise OperationRecoveryError(
                "unexpected exact drain progress artifact exists"
            )
    else:
        if not progress_path.exists():
            raise OperationRecoveryError(
                "prior exact drain progress artifact is unavailable"
            )
        previous = _validated_progress(
            _read_private(progress_path),
            plan["plan_digest"],
            progress_schema_version=(
                plan.get("progress_schema_version", 1)
            ),
        )
        if previous["worker_attempt"] != worker_attempt - 1:
            raise OperationRecoveryError(
                "prior exact drain progress attempt differs"
            )
        prior_attempts = [dict(item) for item in previous["prior_attempts"]]
        prior_attempts.append(
            {
                "worker_attempt": previous["worker_attempt"],
                "progress_digest": previous["progress_digest"],
            }
        )
        archive_path = exact_drain_progress_archive_path(
            progress_path,
            previous["worker_attempt"],
        )
        def normalized_path(value: str | Path) -> str:
            return unicodedata.normalize(
                "NFD",
                str(Path(value).resolve(strict=False)).casefold(),
            )

        artifact_paths = {
            normalized_path(value)
            for key, value in plan.items()
            if key.endswith("_path") and isinstance(value, str)
        }
        if normalized_path(archive_path) in artifact_paths:
            raise OperationRecoveryError(
                "exact drain prior attempt path aliases another artifact"
            )
        if archive_path.exists():
            archived = _validated_progress(
                _read_private(archive_path),
                plan["plan_digest"],
                progress_schema_version=(
                    plan.get("progress_schema_version", 1)
                ),
            )
            if archived["progress_digest"] != previous["progress_digest"]:
                raise OperationRecoveryError(
                    "exact drain prior attempt artifact differs"
                )
        else:
            _write_private(archive_path, previous, create_only=True)
        initial_tasks = previous["tasks"]
    return ExactDrainProgressRecorder(
        path=progress_path,
        plan_digest=plan["plan_digest"],
        worker_pid=journal["worker_pid"],
        worker_start_time=journal["worker_start_time"],
        worker_attempt=worker_attempt,
        selected_operations=plan["selected_operations"],
        prior_attempts=prior_attempts,
        initial_tasks=initial_tasks,
        progress_schema_version=plan.get("progress_schema_version", 1),
        clock=clock,
    )


def _validated_progress(
    value: Mapping[str, Any],
    plan_digest: str,
    *,
    progress_schema_version: int = 1,
) -> dict[str, Any]:
    expected_keys = {
        "schema_version",
        "kind",
        "plan_digest",
        "worker_pid",
        "worker_start_time",
        "worker_attempt",
        "prior_attempts",
        "started_at",
        "observed_at",
        "last_progress_at",
        "selected_status_counts",
        "tasks",
        "provider_counters",
        "active_provider_requests",
        "cooldowns",
        "progress_digest",
    }
    if progress_schema_version in {3, 4, 5}:
        expected_keys |= {
            "worker_status",
            "worker_stage",
            "worker_stage_started_at",
            "worker_failure_stage",
            "worker_failure",
            "worker_exit_code",
        }
    if (
        set(value) != expected_keys
        or value.get("schema_version") != progress_schema_version
        or progress_schema_version not in {1, 2, 3, 4, 5}
        or value.get("kind") != "operation-recovery-exact-drain-progress"
        or value.get("plan_digest") != _sha(plan_digest, "exact drain plan digest")
    ):
        raise OperationRecoveryError("exact drain progress is invalid")
    result = dict(value)
    progress_digest = result.pop("progress_digest")
    if _sha(progress_digest, "exact drain progress digest") != digest(result):
        raise OperationRecoveryError("exact drain progress digest differs")
    if (
        type(result.get("worker_pid")) is not int
        or result["worker_pid"] <= 1
        or not isinstance(result.get("worker_start_time"), str)
        or not result["worker_start_time"]
        or len(result["worker_start_time"]) > 128
        or result["worker_start_time"]
        != " ".join(result["worker_start_time"].split())
        or type(result.get("worker_attempt")) is not int
        or result["worker_attempt"] < 1
    ):
        raise OperationRecoveryError("exact drain progress identity is invalid")
    prior_attempts = result.get("prior_attempts")
    if not isinstance(prior_attempts, list):
        raise OperationRecoveryError("exact drain prior attempts are invalid")
    for expected_attempt, item in enumerate(prior_attempts, start=1):
        if (
            not isinstance(item, Mapping)
            or set(item) != {"worker_attempt", "progress_digest"}
            or item.get("worker_attempt") != expected_attempt
        ):
            raise OperationRecoveryError("exact drain prior attempts are invalid")
        _sha(item.get("progress_digest"), "exact drain prior progress digest")
    if len(prior_attempts) != result["worker_attempt"] - 1:
        raise OperationRecoveryError("exact drain prior attempts differ")
    started_at = _timestamp(result.get("started_at"), "exact drain progress start")
    observed_at = _timestamp(
        result.get("observed_at"), "exact drain progress observation"
    )
    last_progress_at = _timestamp(
        result.get("last_progress_at"), "exact drain last progress"
    )
    if not started_at <= last_progress_at <= observed_at:
        raise OperationRecoveryError("exact drain progress timestamps differ")
    if progress_schema_version in {3, 4, 5}:
        worker_status = result.get("worker_status")
        worker_stage = result.get("worker_stage")
        worker_failure_stage = result.get("worker_failure_stage")
        worker_failure = _validated_failure(
            result.get("worker_failure"),
            categories=_failure_categories(
                progress_schema_version,
                worker=True,
            ),
        )
        worker_exit_code = result.get("worker_exit_code")
        worker_stage_started_at = _timestamp(
            result.get("worker_stage_started_at"),
            "exact drain worker stage start",
        )
        if (
            worker_status not in WORKER_STATUSES
            or not isinstance(worker_stage, str)
            or STAGE.fullmatch(worker_stage) is None
            or not started_at
            <= worker_stage_started_at
            <= last_progress_at
            or (worker_failure is None) != (worker_failure_stage is None)
            or (worker_failure is None) != (worker_exit_code is None)
            or (worker_status == "failed") != (worker_failure is not None)
            or (worker_failure is None and worker_stage == "failed")
            or (
                worker_exit_code is not None
                and (
                    type(worker_exit_code) is not int
                    or not 1 <= worker_exit_code <= 255
                )
            )
            or (
                worker_failure_stage is not None
                and (
                    not isinstance(worker_failure_stage, str)
                    or STAGE.fullmatch(worker_failure_stage) is None
                    or worker_stage != "failed"
                )
            )
        ):
            raise OperationRecoveryError(
                "exact drain progress worker evidence is invalid"
            )

    task_keys = {
        "operation_id",
        "operation_digest",
        "row_digest",
        "operation_type",
        "status",
        "stage",
        "total_started_at",
        "stage_started_at",
        "last_progress_at",
    }
    if progress_schema_version in {2, 3, 4, 5}:
        task_keys |= {"failure_stage", "failure", "checkpoint"}
    tasks = result.get("tasks")
    if not isinstance(tasks, list):
        raise OperationRecoveryError("exact drain progress tasks are invalid")
    task_ids: set[str] = set()
    derived_counts: dict[str, int] = {}
    for item in tasks:
        if not isinstance(item, Mapping) or set(item) != task_keys:
            raise OperationRecoveryError("exact drain progress task is invalid")
        operation_id = item.get("operation_id")
        status = item.get("status")
        stage = item.get("stage")
        if (
            not isinstance(operation_id, str)
            or OPERATION_ID.fullmatch(operation_id) is None
            or operation_id in task_ids
            or item.get("operation_digest")
            != hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
            or SHA256.fullmatch(str(item.get("row_digest", ""))) is None
            or item.get("operation_type") not in OPERATION_TYPES
            or status not in TASK_STATUSES
        ):
            raise OperationRecoveryError("exact drain progress task is invalid")
        if not isinstance(stage, str) or STAGE.fullmatch(stage) is None:
            raise OperationRecoveryError("exact drain progress task stage is invalid")
        if progress_schema_version in {2, 3, 4, 5}:
            failure_stage = item.get("failure_stage")
            failure = _validated_failure(
                item.get("failure"),
                categories=_failure_categories(progress_schema_version),
            )
            _validated_checkpoint(item.get("checkpoint"))
            if (
                (failure is None) != (failure_stage is None)
                or (
                    failure_stage is not None
                    and (
                        not isinstance(failure_stage, str)
                        or STAGE.fullmatch(failure_stage) is None
                    )
                )
            ):
                raise OperationRecoveryError(
                    "exact drain progress failure evidence is invalid"
                )
        total_started_at = _timestamp(
            item.get("total_started_at"), "exact drain task start"
        )
        stage_started_at = _timestamp(
            item.get("stage_started_at"), "exact drain task stage start"
        )
        task_progress_at = _timestamp(
            item.get("last_progress_at"), "exact drain task progress"
        )
        if not total_started_at <= stage_started_at <= task_progress_at <= observed_at:
            raise OperationRecoveryError("exact drain task timestamps differ")
        task_ids.add(operation_id)
        summary_status = (
            "retrying" if status == "pending" and stage == "retrying" else status
        )
        derived_counts[summary_status] = derived_counts.get(summary_status, 0) + 1

    selected_counts = result.get("selected_status_counts")
    if (
        not isinstance(selected_counts, Mapping)
        or set(selected_counts) - SUMMARY_STATUSES
        or any(type(count) is not int or count <= 0 for count in selected_counts.values())
        or dict(selected_counts) != derived_counts
    ):
        raise OperationRecoveryError("exact drain progress counts are invalid")

    counter_keys = {
        "provider_id",
        "started",
        "succeeded",
        "failed",
        "timed_out",
        "failed_over",
        "duration_count",
        "duration_total_seconds",
        "duration_max_seconds",
        "last_provider_response_at",
    }
    counter_keys_v4 = {
        "provider_id",
        "started",
        "succeeded",
        "failed",
        "queue_timed_out",
        "execution_timed_out",
        "failed_over",
        "queue_duration_count",
        "queue_duration_total_seconds",
        "queue_duration_max_seconds",
        "execution_duration_count",
        "execution_duration_total_seconds",
        "execution_duration_max_seconds",
        "last_provider_response_at",
    }
    counter_keys_v5 = counter_keys_v4 | {
        "queue_cancelled",
        "execution_cancelled",
    }
    counters = result.get("provider_counters")
    active = result.get("active_provider_requests")
    cooldowns = result.get("cooldowns")
    if not isinstance(counters, list) or not isinstance(active, list) or not isinstance(cooldowns, list):
        raise OperationRecoveryError("exact drain provider progress is invalid")
    counters_by_id: dict[str, Mapping[str, Any]] = {}
    for item in counters:
        expected_counter_keys = (
            counter_keys_v5
            if progress_schema_version == 5
            else (
                counter_keys_v4
                if progress_schema_version == 4
                else counter_keys
            )
        )
        if not isinstance(item, Mapping) or set(item) != expected_counter_keys:
            raise OperationRecoveryError("exact drain provider counter is invalid")
        provider_id = _identifier(item.get("provider_id"), "exact drain provider")
        if provider_id in counters_by_id:
            raise OperationRecoveryError("exact drain provider counter is invalid")
        if progress_schema_version in {4, 5}:
            terminal_count_keys = [
                "succeeded",
                "failed",
                "queue_timed_out",
                "execution_timed_out",
            ]
            if progress_schema_version == 5:
                terminal_count_keys.extend(
                    ("queue_cancelled", "execution_cancelled")
                )
            counts = {
                key: _count(item.get(key), f"exact drain provider {key}")
                for key in (
                    "started",
                    "succeeded",
                    "failed",
                    "queue_timed_out",
                    "execution_timed_out",
                    *(
                        ("queue_cancelled", "execution_cancelled")
                        if progress_schema_version == 5
                        else ()
                    ),
                    "failed_over",
                    "queue_duration_count",
                    "execution_duration_count",
                )
            }
            terminal_count = sum(counts[key] for key in terminal_count_keys)
            queue_total = _timestamp(
                item.get("queue_duration_total_seconds"),
                "exact drain provider queue duration",
            )
            queue_max = _timestamp(
                item.get("queue_duration_max_seconds"),
                "exact drain provider queue duration",
            )
            execution_total = _timestamp(
                item.get("execution_duration_total_seconds"),
                "exact drain provider execution duration",
            )
            execution_max = _timestamp(
                item.get("execution_duration_max_seconds"),
                "exact drain provider execution duration",
            )
            if (
                counts["failed_over"]
                > counts["failed"]
                + counts["queue_timed_out"]
                + counts["execution_timed_out"]
                or counts["queue_duration_count"] != terminal_count
                or counts["execution_duration_count"]
                != terminal_count
                - counts["queue_timed_out"]
                - (
                    counts["queue_cancelled"]
                    if progress_schema_version == 5
                    else 0
                )
                or queue_max > queue_total
                or execution_max > execution_total
                or (
                    counts["queue_duration_count"] == 0
                    and (queue_total or queue_max)
                )
                or (
                    counts["execution_duration_count"] == 0
                    and (execution_total or execution_max)
                )
            ):
                raise OperationRecoveryError(
                    "exact drain provider counter differs"
                )
        else:
            counts = [
                _count(item.get(key), f"exact drain provider {key}")
                for key in (
                    "started",
                    "succeeded",
                    "failed",
                    "timed_out",
                    "failed_over",
                    "duration_count",
                )
            ]
            duration_total = _timestamp(
                item.get("duration_total_seconds"),
                "exact drain provider duration",
            )
            duration_max = _timestamp(
                item.get("duration_max_seconds"),
                "exact drain provider duration",
            )
            terminal_count = counts[1] + counts[2] + counts[3]
            if (
                counts[4] > counts[2] + counts[3]
                or counts[5] != terminal_count
                or duration_max > duration_total
                or (terminal_count == 0 and (duration_total or duration_max))
            ):
                raise OperationRecoveryError(
                    "exact drain provider counter differs"
                )
        last_response = item.get("last_provider_response_at")
        if last_response is not None:
            _timestamp(last_response, "exact drain provider response")
        counters_by_id[provider_id] = item

    active_keys = {
        "request_digest",
        "provider_id",
        "started_at",
        "retry_attempt",
        "scope_category",
    }
    active_keys_v4 = {
        "request_digest",
        "provider_id",
        "state",
        "queued_at",
        "executing_at",
        "retry_attempt",
        "scope_category",
    }
    active_by_provider: dict[str, int] = {}
    request_ids: set[str] = set()
    for item in active:
        expected_active_keys = (
            active_keys_v4
            if progress_schema_version in {4, 5}
            else active_keys
        )
        if not isinstance(item, Mapping) or set(item) != expected_active_keys:
            raise OperationRecoveryError("exact drain active request is invalid")
        request_digest = _sha(
            item.get("request_digest"), "exact drain request digest"
        )
        provider_id = _identifier(item.get("provider_id"), "exact drain provider")
        if (
            request_digest in request_ids
            or provider_id not in counters_by_id
            or type(item.get("retry_attempt")) is not int
            or item["retry_attempt"] < 1
            or item.get("scope_category") not in SCOPE_CATEGORIES
        ):
            raise OperationRecoveryError("exact drain active request is invalid")
        if progress_schema_version in {4, 5}:
            queued_at = _timestamp(
                item.get("queued_at"), "exact drain request queue start"
            )
            executing_at = item.get("executing_at")
            if (
                item.get("state") not in {"queued", "executing"}
                or not started_at <= queued_at <= observed_at
                or (
                    item.get("state") == "queued"
                    and executing_at is not None
                )
                or (
                    item.get("state") == "executing"
                    and (
                        executing_at is None
                        or not queued_at
                        <= _timestamp(
                            executing_at,
                            "exact drain request execution start",
                        )
                        <= observed_at
                    )
                )
            ):
                raise OperationRecoveryError(
                    "exact drain active request is invalid"
                )
        elif not started_at <= _timestamp(
            item.get("started_at"), "exact drain request start"
        ) <= observed_at:
            raise OperationRecoveryError("exact drain active request is invalid")
        request_ids.add(request_digest)
        active_by_provider[provider_id] = active_by_provider.get(provider_id, 0) + 1
    for provider_id, counter in counters_by_id.items():
        terminal_count = (
            counter["succeeded"]
            + counter["failed"]
            + (
                counter["queue_timed_out"]
                + counter["execution_timed_out"]
                + (
                    counter["queue_cancelled"]
                    + counter["execution_cancelled"]
                    if progress_schema_version == 5
                    else 0
                )
                if progress_schema_version in {4, 5}
                else counter["timed_out"]
            )
        )
        if counter["started"] != terminal_count + active_by_provider.get(provider_id, 0):
            raise OperationRecoveryError("exact drain provider counter differs")

    cooldown_keys = {"provider_id", "until", "reason"}
    cooling: set[str] = set()
    for item in cooldowns:
        if not isinstance(item, Mapping) or set(item) != cooldown_keys:
            raise OperationRecoveryError("exact drain cooldown is invalid")
        provider_id = _identifier(item.get("provider_id"), "exact drain provider")
        if (
            provider_id in cooling
            or item.get("reason") not in {"usage_limit", "terminal_auth", "transport"}
        ):
            raise OperationRecoveryError("exact drain cooldown is invalid")
        _timestamp(item.get("until"), "exact drain cooldown")
        cooling.add(provider_id)
    return {**result, "progress_digest": progress_digest}


def read_exact_drain_progress(
    path: Path,
    *,
    plan_digest: str,
    progress_schema_version: int = 1,
    now: float | None = None,
    freeze_ages_at_observed_at: bool = False,
) -> Mapping[str, Any]:
    value = _validated_progress(
        _read_private(path),
        plan_digest,
        progress_schema_version=progress_schema_version,
    )
    progress_digest = value["progress_digest"]
    if type(freeze_ages_at_observed_at) is not bool:
        raise OperationRecoveryError(
            "exact drain progress age projection is invalid"
        )
    observed_now = (
        float(value["observed_at"])
        if freeze_ages_at_observed_at
        else float(time.time() if now is None else now)
    )
    prior_attempts = []
    for reference in value["prior_attempts"]:
        archive_path = exact_drain_progress_archive_path(
            path,
            reference["worker_attempt"],
        )
        archived = _validated_progress(
            _read_private(archive_path),
            plan_digest,
            progress_schema_version=progress_schema_version,
        )
        if (
            archived["worker_attempt"] != reference["worker_attempt"]
            or archived["progress_digest"] != reference["progress_digest"]
            or archived["prior_attempts"]
            != value["prior_attempts"][: reference["worker_attempt"] - 1]
        ):
            raise OperationRecoveryError(
                "exact drain prior attempt artifact differs"
            )
        prior_attempts.append(
            {
                "worker_attempt": archived["worker_attempt"],
                "worker_pid": archived["worker_pid"],
                "worker_start_time": archived["worker_start_time"],
                "started_at": archived["started_at"],
                "observed_at": archived["observed_at"],
                "last_progress_at": archived["last_progress_at"],
                "selected_status_counts": archived["selected_status_counts"],
                "provider_counters": archived["provider_counters"],
                "active_provider_request_count": len(
                    archived["active_provider_requests"]
                ),
                "cooldowns": archived["cooldowns"],
                **(
                    {}
                    if progress_schema_version not in {3, 4, 5}
                    else {
                        "worker_status": archived["worker_status"],
                        "worker_stage": archived["worker_stage"],
                        "worker_failure_stage": archived[
                            "worker_failure_stage"
                        ],
                        "worker_failure": archived["worker_failure"],
                        "worker_exit_code": archived["worker_exit_code"],
                    }
                ),
                "progress_digest": archived["progress_digest"],
                "artifact_path": str(archive_path),
            }
        )
    tasks = []
    for item in value["tasks"]:
        task = dict(item)
        task["total_age_seconds"] = max(
            0.0, observed_now - float(task["total_started_at"])
        )
        task["stage_age_seconds"] = max(
            0.0, observed_now - float(task["stage_started_at"])
        )
        tasks.append(task)
    active = []
    for item in value["active_provider_requests"]:
        request = dict(item)
        if progress_schema_version in {4, 5}:
            queued_at = float(request["queued_at"])
            executing_at = request["executing_at"]
            request["queue_age_seconds"] = max(
                0.0,
                float(
                    observed_now if executing_at is None else executing_at
                )
                - queued_at,
            )
            request["execution_age_seconds"] = (
                None
                if executing_at is None
                else max(0.0, observed_now - float(executing_at))
            )
        else:
            request["age_seconds"] = max(
                0.0, observed_now - float(request["started_at"])
            )
        active.append(request)
    return {
        **value,
        "prior_attempts": prior_attempts,
        "tasks": tasks,
        "active_provider_requests": active,
        "progress_digest": progress_digest,
    }
