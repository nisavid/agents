"""Bounded, resumable execution for already-validated import projections."""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import time
from typing import Any, Callable, Mapping

from .canonical import canonical_bytes, digest, strict_json_loads
from .file_evidence import open_trusted_parent
from .importing import import_item_digest
from .model import deep_freeze


MAX_INSPECTION_CHECKPOINT_BYTES = 256 * 1024


def _read_bounded(descriptor: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    remaining = limit + 1
    while remaining:
        chunk = os.read(descriptor, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


@dataclass(frozen=True)
class ImportRunResult:
    completed_item_ids: tuple[str, ...]
    deferred_item_ids: tuple[str, ...]
    resume_state: Mapping[str, str]
    events: tuple[Mapping[str, str], ...]
    run_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "resume_state", deep_freeze(self.resume_state))
        object.__setattr__(self, "events", tuple(deep_freeze(event) for event in self.events))


def _inspector_failure(error: Exception) -> dict[str, str]:
    error_type = type(error).__name__
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", error_type):
        error_type = "Exception"
    try:
        message = str(error)
    except Exception:
        message = "diagnostic unavailable"
    bounded_message = message.encode("utf-8", errors="replace")[:1024]
    diagnostic = digest({
        "type": error_type,
        "message": bounded_message.decode("utf-8", errors="ignore"),
    })[:16]
    return {
        "error_type": error_type,
        "error_message": f"inspector callback failed ({diagnostic})",
    }


def run_import_inspection(
    projection: Any,
    *,
    inspector: Callable[[Any], Any],
    resume_state: Mapping[str, str] | None = None,
    max_items: int = 100,
    requests_per_window: int = 10,
    window_seconds: float = 60.0,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    rate_limit_state_path: str | Path,
) -> ImportRunResult:
    """Run a bounded proposal-inspection pass without controller apply."""

    from .importing import ImportValidationError as DomainImportError
    from .importing import _sha, validate_projection

    validate_projection(projection)
    if not callable(inspector) or not callable(clock) or not callable(sleep):
        raise DomainImportError("inspection run callbacks must be callable")
    if type(max_items) is not int or not 1 <= max_items <= 1000:
        raise DomainImportError("max_items must be an integer from 1 to 1000")
    if type(requests_per_window) is not int or not 1 <= requests_per_window <= 1000:
        raise DomainImportError("requests_per_window must be an integer from 1 to 1000")
    if (
        not isinstance(window_seconds, (int, float))
        or isinstance(window_seconds, bool)
        or not math.isfinite(window_seconds)
        or not 0 < window_seconds <= 3600
    ):
        raise DomainImportError("window_seconds must be greater than zero and at most 3600")
    rate_limit_path = Path(rate_limit_state_path)
    if (
        not rate_limit_path.is_absolute()
        or rate_limit_path.name in {"", ".", ".."}
    ):
        raise DomainImportError("rate-limit state path is invalid")

    item_by_id = {item.item_id: item for item in projection.items}
    checkpoint_path = rate_limit_path.with_name(
        f"{rate_limit_path.name}.inspection-checkpoint."
        f"{projection.projection_digest}"
    )
    incoming_state = dict(resume_state or {})
    for item_id, item_digest in incoming_state.items():
        _sha(item_id, "resume item identity")
        _sha(item_digest, "resume item digest")
        if item_id not in item_by_id:
            raise DomainImportError("resume state references an unknown projection item")
        if item_digest != import_item_digest(item_by_id[item_id]):
            raise DomainImportError(
                "resume state item digest does not match the projection"
            )
    validated_incoming_state = {
        item.item_id: import_item_digest(item)
        for item in projection.items
        if incoming_state.get(item.item_id) == import_item_digest(item)
    }
    supplied_state = _inspection_checkpoint(
        checkpoint_path,
        projection.projection_digest,
        completed=(
            validated_incoming_state if resume_state is not None else None
        ),
        compatible=incoming_state,
    )
    for item_id, item_digest in supplied_state.items():
        _sha(item_id, "resume item identity")
        _sha(item_digest, "resume item digest")
        if item_id not in item_by_id:
            raise DomainImportError("resume state references an unknown projection item")
        if item_digest != import_item_digest(item_by_id[item_id]):
            raise DomainImportError(
                "inspection checkpoint item digest does not match the projection"
            )
    completed_state = {
        item.item_id: import_item_digest(item)
        for item in projection.items
        if item.item_id in projection.skipped_item_ids
        or supplied_state.get(item.item_id) == import_item_digest(item)
    }
    pending = [item for item in projection.pending_items if item.item_id not in completed_state]
    events: list[dict[str, str]] = []
    for item in pending[:max_items]:
        while True:
            reservation = _reserve_rate_limit_slot(
                rate_limit_path,
                clock=clock,
                requests_per_window=requests_per_window,
                window_seconds=float(window_seconds),
            )
            if reservation is None:
                break
            delay, observed_time = reservation
            sleep(delay)
            if clock() <= observed_time:
                raise DomainImportError("rate-limit clock did not advance through the window")
        try:
            inspector(item)
        except Exception as error:
            events.append({
                "item_id": item.item_id,
                "status": "failed",
                **_inspector_failure(error),
            })
            break
        item_digest = import_item_digest(item)
        next_state = {**completed_state, item.item_id: item_digest}
        completed_state = _inspection_checkpoint(
            checkpoint_path,
            projection.projection_digest,
            completed=next_state,
        )
        events.append({"item_id": item.item_id, "status": "inspected"})

    completed_ids = tuple(item.item_id for item in projection.items if item.item_id in completed_state)
    deferred_ids = tuple(item.item_id for item in projection.pending_items if item.item_id not in completed_state)
    body = {
        "projection_digest": projection.projection_digest,
        "completed": [{"item_id": item_id, "item_digest": completed_state[item_id]} for item_id in completed_ids],
        "deferred_item_ids": list(deferred_ids),
        "events": events,
    }
    return ImportRunResult(
        completed_ids, deferred_ids,
        {item_id: completed_state[item_id] for item_id in completed_ids},
        tuple(events), digest(body),
    )


def _inspection_checkpoint(
    path: Path,
    projection_digest: str,
    *,
    completed: Mapping[str, str] | None = None,
    compatible: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Read or atomically replace one durable per-item inspection checkpoint."""

    from .importing import ImportValidationError as DomainImportError

    parent = lock = source = temporary = None
    temporary_name: str | None = None
    try:
        parent = _open_rate_limit_parent(path.parent)
        flags = os.O_RDWR | os.O_CREAT | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        for attempt in range(3):
            try:
                lock = os.open(
                    f"{path.name}.lock", flags, 0o600, dir_fd=parent
                )
                break
            except FileNotFoundError:
                if attempt == 2:
                    raise
                time.sleep(0.001)
        metadata = os.fstat(lock)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise DomainImportError("inspection checkpoint is unsafe")
        for attempt in range(50):
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if attempt == 49:
                    raise DomainImportError(
                        "inspection checkpoint lock is busy"
                    ) from None
                time.sleep(0.001)
        read_flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        try:
            source = os.open(path.name, read_flags, dir_fd=parent)
        except FileNotFoundError:
            existing: dict[str, str] = {}
        else:
            metadata = os.fstat(source)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_size > MAX_INSPECTION_CHECKPOINT_BYTES
            ):
                raise DomainImportError("inspection checkpoint is unsafe")
            raw = _read_bounded(source, MAX_INSPECTION_CHECKPOINT_BYTES)
            if len(raw) > MAX_INSPECTION_CHECKPOINT_BYTES:
                raise DomainImportError("inspection checkpoint is unsafe")
            try:
                value = strict_json_loads(raw)
            except (UnicodeDecodeError, ValueError):
                raise DomainImportError("inspection checkpoint is invalid") from None
            if (
                not isinstance(value, dict)
                or set(value) != {"schema_version", "projection_digest", "completed"}
                or value["schema_version"] != 1
                or value["projection_digest"] != projection_digest
                or not isinstance(value["completed"], dict)
                or any(
                    not isinstance(key, str) or not isinstance(item, str)
                    for key, item in value["completed"].items()
                )
            ):
                raise DomainImportError("inspection checkpoint is invalid")
            existing = dict(value["completed"])
        for item_id, item_digest in (compatible or {}).items():
            if item_id in existing and existing[item_id] != item_digest:
                raise DomainImportError(
                    "incoming resume state conflicts with inspection checkpoint"
                )
        if completed is None:
            return existing
        merged = dict(existing)
        for item_id, item_digest in completed.items():
            if item_id in merged and merged[item_id] != item_digest:
                raise DomainImportError(
                    "incoming resume state conflicts with inspection checkpoint"
                )
            merged[item_id] = item_digest
        body = canonical_bytes(
            {
                "schema_version": 1,
                "projection_digest": projection_digest,
                "completed": merged,
            }
        )
        if len(body) > MAX_INSPECTION_CHECKPOINT_BYTES:
            raise DomainImportError("inspection checkpoint is unsafe")
        temporary_name = f".{path.name}.tmp-{secrets.token_hex(8)}"
        temporary = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent,
        )
        offset = 0
        while offset < len(body):
            written = os.write(temporary, body[offset:])
            if written <= 0:
                raise OSError("short inspection checkpoint write")
            offset += written
        os.fsync(temporary)
        os.close(temporary)
        temporary = None
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=parent,
            dst_dir_fd=parent,
        )
        temporary_name = None
        os.fsync(parent)
        return merged
    except DomainImportError:
        raise
    except (OSError, ValueError) as error:
        raise DomainImportError("inspection checkpoint is unavailable") from error
    finally:
        if temporary is not None:
            os.close(temporary)
        if temporary_name is not None and parent is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent)
            except FileNotFoundError:
                pass
        if source is not None:
            os.close(source)
        if lock is not None:
            os.close(lock)
        if parent is not None:
            os.close(parent)


def _reserve_rate_limit_slot(
    path: Path, *, clock: Callable[[], float], requests_per_window: int,
    window_seconds: float,
) -> tuple[float, float] | None:
    """Atomically reserve one persisted sliding-window request slot."""
    from .importing import ImportValidationError as DomainImportError

    parent_descriptor: int | None = None
    lock_descriptor: int | None = None
    state_descriptor: int | None = None
    temporary_descriptor: int | None = None
    temporary_name: str | None = None
    try:
        parent_descriptor = _open_rate_limit_parent(path.parent)
        flags = os.O_RDWR | os.O_CREAT | os.O_NONBLOCK
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        for attempt in range(3):
            try:
                lock_descriptor = os.open(
                    f"{path.name}.lock", flags, 0o600,
                    dir_fd=parent_descriptor,
                )
                break
            except FileNotFoundError:
                if attempt == 2:
                    raise
                time.sleep(0.001)
        metadata = os.fstat(lock_descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise DomainImportError("rate-limit state is unsafe")
        lock_deadline = time.monotonic() + 2.0
        while True:
            try:
                fcntl.flock(
                    lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB
                )
                break
            except BlockingIOError:
                if time.monotonic() >= lock_deadline:
                    raise DomainImportError("rate-limit state lock timed out")
                time.sleep(0.01)

        state_flags = os.O_RDONLY | os.O_NONBLOCK
        if hasattr(os, "O_NOFOLLOW"):
            state_flags |= os.O_NOFOLLOW
        try:
            state_descriptor = os.open(
                path.name, state_flags, dir_fd=parent_descriptor
            )
        except FileNotFoundError:
            raw = b""
        else:
            metadata = os.fstat(state_descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise DomainImportError("rate-limit state is unsafe")
            raw = os.read(state_descriptor, 64 * 1024 + 1)
            if len(raw) > 64 * 1024:
                raise DomainImportError("rate-limit state is invalid")

        observed = clock()
        if not isinstance(observed, (int, float)) or isinstance(observed, bool):
            raise DomainImportError("rate-limit clock must return a finite number")
        current_time = float(observed)
        if not math.isfinite(current_time):
            raise DomainImportError("rate-limit clock must return a finite number")
        if raw:
            try:
                state = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise DomainImportError("rate-limit state is invalid") from None
        else:
            state = {
                "schema_version": 1,
                "requests_per_window": requests_per_window,
                "window_seconds": window_seconds,
                "request_times": [],
            }
        if (
            not isinstance(state, dict)
            or set(state)
            != {
                "schema_version", "requests_per_window", "window_seconds",
                "request_times",
            }
            or state["schema_version"] != 1
            or state["requests_per_window"] != requests_per_window
            or state["window_seconds"] != window_seconds
            or not isinstance(state["request_times"], list)
            or any(
                type(started) not in (int, float)
                or not math.isfinite(started)
                for started in state["request_times"]
            )
        ):
            raise DomainImportError("rate-limit state is invalid")
        request_times = [
            min(float(started), current_time)
            for started in state["request_times"]
            if current_time - min(float(started), current_time) < window_seconds
        ]
        delay: float | None = None
        if len(request_times) >= requests_per_window:
            delay = max(0.0, window_seconds - (current_time - request_times[0]))
        else:
            request_times.append(current_time)
        state["request_times"] = request_times
        body = json.dumps(
            state, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")

        temporary_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            temporary_flags |= os.O_NOFOLLOW
        for attempt in range(10):
            temporary_name = f".{path.name}.tmp-{secrets.token_hex(8)}"
            try:
                temporary_descriptor = os.open(
                    temporary_name,
                    temporary_flags,
                    0o600,
                    dir_fd=parent_descriptor,
                )
                break
            except FileExistsError:
                if attempt == 9:
                    raise
        os.fchmod(temporary_descriptor, 0o600)
        offset = 0
        while offset < len(body):
            written = os.write(temporary_descriptor, body[offset:])
            if written <= 0:
                raise OSError("short rate-limit state write")
            offset += written
        os.fsync(temporary_descriptor)
        os.close(temporary_descriptor)
        temporary_descriptor = None
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        temporary_name = None
        os.fsync(parent_descriptor)
        return None if delay is None else (delay, current_time)
    except DomainImportError:
        raise
    except (OSError, ValueError) as error:
        raise DomainImportError("rate-limit state is unavailable") from error
    finally:
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
        if temporary_name is not None and parent_descriptor is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
        if state_descriptor is not None:
            os.close(state_descriptor)
        if lock_descriptor is not None:
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def _open_rate_limit_parent(path: Path) -> int:
    """Open/create an absolute parent without following mutable path names."""
    return open_trusted_parent(
        path,
        unavailable_message=(
            "symlink-safe rate-limit state access is unavailable"
        ),
        not_directory_message="rate-limit state parent is unsafe",
        owner_message="rate-limit state parent is unsafe",
        writable_message="rate-limit state parent is unsafe",
    )
