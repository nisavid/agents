"""Private runtime memory broker with scoped capabilities and durable writes."""

from __future__ import annotations

import base64
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import heapq
import hmac
import fcntl
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import threading
import time
from typing import Any, Callable, Mapping

from .adapters import Adapter, AdapterError
from .canonical import canonical_bytes
from .ledger import LedgerError, append_record_once, validate_record


LOGGER = logging.getLogger(__name__)


CAPABILITY_METHODS = frozenset({
    "recall", "mental_model_fetch", "transcript_checkpoint", "retain_outcome",
    "reflect", "session_status", "session_close",
})
CLAIM_KEYS = frozenset({
    "session_id", "harness_id", "home_bank", "trust_class", "companion_id",
    "policy_digest", "artifact_digest", "methods", "route",
})
MINT_REQUEST_KEYS = frozenset({"session_id", "companion_id", "route"})
ENVELOPE_KEYS = CLAIM_KEYS | {"kind", "issued_at", "expires_at", "nonce", "revocation_id", "broker_generation"}
CAPABILITY_KEYS = CLAIM_KEYS | {"kind", "issued_at", "expires_at", "nonce", "revocation_id"}
RECOVERY_RECEIPT_KEYS = frozenset({
    "kind", "handle", "capability_digest", "nonce_digest", "session_id", "expires_at",
})
FORBIDDEN_KEYS = frozenset({
    "destination", "destination_bank", "target_bank", "home_bank", "bank", "bank_id",
    "endpoint", "url", "authorization", "bearer", "credential", "credentials", "token",
    "api_key", "control_key", "signing_key", "secret", "route",
})
FORBIDDEN_KEY_TOKENS = frozenset(
    re.sub(r"[^a-z0-9]", "", key.lower()) for key in FORBIDDEN_KEYS
)
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
REQUEST_SCHEMAS = {
    "recall": ({"query"}, {"limit", "depth"}),
    "mental_model_fetch": ({"model_id"}, set()),
    "transcript_checkpoint": ({"document_id", "epoch", "checkpoint", "content"}, set()),
    "retain_outcome": ({"document_id", "epoch", "checkpoint", "outcome"}, set()),
    "reflect": ({"reflection"}, set()),
}
COMPLETED_RETENTION_SECONDS = 30 * 24 * 60 * 60
MAX_HANDLE_GC_FILES = 256
LOCK_STRIPES = 64
MAX_IN_FLIGHT_ADAPTER_CALLS = 4
MAX_IN_FLIGHT_READ_ADAPTER_CALLS = 16
MAX_ADAPTER_GENERATION_LEASE_SECONDS = 30.0
PENDING_OPERATION_WARNING_POLL = 32
SESSION_STATUS_WRITE_LIMIT = 8
MAX_DURABLE_QUEUE_ENTRIES = 1024
MAX_DURABLE_QUEUE_ENTRY_BYTES = 128 * 1024
MAX_DURABLE_QUEUE_BYTES = 8 * 1024 * 1024
MAX_COMPLETED_ENTRIES = 4096
MAX_COMPLETED_BYTES = 16 * 1024 * 1024
MAX_LEDGER_OUTBOX_ENTRIES = 1024
MAX_LEDGER_OUTBOX_BYTES = 8 * 1024 * 1024
MAX_SESSION_ACTION_IDS = 1024
MAX_REQUEST_SEQUENCE = 1_000_000
MAX_DURABLE_SESSIONS = 4096
MAX_DURABLE_EXCHANGES = 4096
MAX_DURABLE_NONCES = 8192
MAX_DURABLE_HANDLES = 4096
MAX_DURABLE_SESSION_BYTES = 8 * 1024 * 1024
MAX_DURABLE_EXCHANGE_BYTES = 8 * 1024 * 1024
MAX_DURABLE_NONCE_BYTES = 2 * 1024 * 1024
MAX_DURABLE_HANDLE_BYTES = 8 * 1024 * 1024
MIN_PAYLOAD_BYTES = 4 * 1024
MAX_PAYLOAD_BYTES = MAX_DURABLE_QUEUE_ENTRY_BYTES


class BrokerError(ValueError):
    """Content-free broker rejection suitable for an operator diagnostic."""

    def __init__(self, code: str, operator_diagnostic: str | None = None):
        self.code = code
        self.operator_diagnostic = operator_diagnostic
        message = (
            f"{code}: {operator_diagnostic}"
            if operator_diagnostic is not None else code
        )
        super().__init__(message)


class AdapterContractError(AdapterError):
    """The adapter returned a response outside the closed broker contract."""


class _AtomicJsonError(OSError):
    """A state publication failure classified around the replace boundary."""

    def __init__(self, *, replaced: bool) -> None:
        self.replaced = replaced
        super().__init__(
            "state publication failed after replace"
            if replaced
            else "state publication failed before replace"
        )


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not isinstance(value, str) or len(value) > 32768:
        raise BrokerError("CAPABILITY_INVALID")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as error:
        raise BrokerError("CAPABILITY_INVALID") from error


def _atomic_json(
    path: Path, value: Any, *, directory_fd: int | None = None
) -> None:
    if directory_fd is None:
        path.parent.mkdir(parents=True, exist_ok=True)
    temporary = f".{path.name}.{secrets.token_hex(8)}.tmp"
    replaced = False
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary if directory_fd is not None else path.with_name(temporary),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            **({"dir_fd": directory_fd} if directory_fd is not None else {}),
        )
        os.fchmod(descriptor, 0o600)
        body = canonical_bytes(value)
        written = 0
        while written < len(body):
            count = os.write(descriptor, body[written:])
            if count <= 0:
                raise OSError("state write failed")
            written += count
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        if directory_fd is None:
            os.replace(path.with_name(temporary), path)
        else:
            os.replace(
                temporary,
                path.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
        replaced = True
        if directory_fd is None:
            os.chmod(path, 0o600)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        else:
            os.chmod(path.name, 0o600, dir_fd=directory_fd)
            os.fsync(directory_fd)
    except Exception as error:
        if descriptor is not None:
            os.close(descriptor)
        if not replaced:
            try:
                if directory_fd is None:
                    path.with_name(temporary).unlink()
                else:
                    os.unlink(temporary, dir_fd=directory_fd)
            except OSError:
                pass
        raise _AtomicJsonError(replaced=replaced) from error


def _read_json(
    path: Path, default: Any, *, directory_fd: int | None = None
) -> Any:
    try:
        if directory_fd is None:
            return json.loads(path.read_text(encoding="utf-8"))
        descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise OSError("state file is unsafe")
            with os.fdopen(descriptor, encoding="utf-8", closefd=False) as stream:
                return json.load(stream)
        finally:
            os.close(descriptor)
    except FileNotFoundError:
        return deepcopy(default)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BrokerError("STATE_INVALID") from error


def _absolute_components(path: str | Path) -> tuple[str, ...]:
    absolute = Path(os.path.abspath(path))
    if sys.platform == "darwin" and len(absolute.parts) > 1:
        aliases = {
            "var": ("private", "var"),
            "tmp": ("private", "tmp"),
            "etc": ("private", "etc"),
        }
        replacement = aliases.get(absolute.parts[1])
        if replacement is not None:
            absolute = Path("/").joinpath(
                *replacement, *absolute.parts[2:]
            )
    return absolute.parts[1:]


def _validate_state_directory(descriptor: int, *, final: bool) -> None:
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        raise OSError("broker state path component must be a directory")
    if metadata.st_uid not in {0, os.geteuid()}:
        raise OSError("broker state path component owner is unsafe")
    mode = stat.S_IMODE(metadata.st_mode)
    writable = mode & 0o022
    if writable and not (
        metadata.st_uid == 0 and metadata.st_mode & stat.S_ISVTX
    ):
        raise OSError("broker state path component mode is unsafe")
    if final and (
        metadata.st_uid != os.geteuid() or mode != 0o700
    ):
        raise OSError("broker state directory must be private")


def _open_state_directory(path: str | Path) -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise OSError("symlink-safe state directory access is unavailable")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    components = _absolute_components(path)
    descriptor = os.open("/", flags)
    try:
        _validate_state_directory(descriptor, final=not components)
        for index, component in enumerate(components):
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                os.mkdir(component, 0o700, dir_fd=descriptor)
                os.fsync(descriptor)
                child = os.open(component, flags, dir_fd=descriptor)
            try:
                _validate_state_directory(
                    child, final=index == len(components) - 1
                )
            except Exception:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_private_child_directory(parent: int, name: str) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
        os.fsync(parent)
    except FileExistsError:
        pass
    descriptor = os.open(name, flags, dir_fd=parent)
    try:
        _validate_state_directory(descriptor, final=True)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _has_forbidden_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            not isinstance(key, str)
            or re.sub(r"[^a-z0-9]", "", key.lower()) in FORBIDDEN_KEY_TOKENS
            or _has_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_has_forbidden_key(child) for child in value)
    return False


class Broker:
    """Authorize memory calls and persist writes before adapter dispatch."""

    def __init__(
        self, *, state_dir: str | Path, signing_key: bytes | Callable[[], bytes],
        routes: Mapping[str, Mapping[str, Any]], policy_digest: str,
        artifact_digest: str, ledger_path: str | Path | None = None,
        mint_authorizer: Callable[[str, Mapping[str, Any], float], Mapping[str, Any]] | None = None,
        clock: Callable[[], float] = time.time, max_payload_bytes: int = 64 * 1024,
        adapter_call_timeout_seconds: float = 2.0,
    ) -> None:
        self.state_dir = Path(state_dir)
        key = signing_key() if callable(signing_key) else signing_key
        if not isinstance(key, bytes) or len(key) < 32:
            raise BrokerError("SIGNING_KEY_INVALID")
        self.__signing_key = bytes(key)
        self.routes = {str(name): dict(route) for name, route in routes.items()}
        for route in self.routes.values():
            if not isinstance(route.get("adapter"), Adapter):
                raise BrokerError("ADAPTER_INVALID")
        self.adapter_call_timeout_seconds = self._timeout(
            adapter_call_timeout_seconds
        )
        self._adapter_generation_lease_timeout_seconds = (
            MAX_ADAPTER_GENERATION_LEASE_SECONDS
        )
        if (
            type(max_payload_bytes) is not int
            or not MIN_PAYLOAD_BYTES
            <= max_payload_bytes
            <= MAX_PAYLOAD_BYTES
        ):
            raise BrokerError("MAX_PAYLOAD_BYTES_INVALID")
        self._state_dir_fd = _open_state_directory(self.state_dir)
        try:
            self._handles_dir_fd = _open_private_child_directory(
                self._state_dir_fd, "handles"
            )
        except Exception:
            os.close(self._state_dir_fd)
            self._state_dir_fd = -1
            raise
        self.policy_digest = policy_digest
        self.artifact_digest = artifact_digest
        self.ledger_path = Path(ledger_path) if ledger_path else None
        self._mint_authorizer = mint_authorizer
        self.clock = clock
        self.max_payload_bytes = max_payload_bytes
        self._lock = threading.RLock()
        self._document_locks = tuple(
            threading.Lock() for _ in range(LOCK_STRIPES)
        )
        self._work_locks = tuple(
            threading.Lock() for _ in range(LOCK_STRIPES)
        )
        self._read_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hindsight-read")
        self._write_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hindsight-write")
        self._read_futures: set[Future[Any]] = set()
        self._write_futures: set[Future[Any]] = set()
        self._write_futures_by_queue_id: dict[str, Future[Any]] = {}
        self._write_retry_condition = threading.Condition(self._lock)
        self._write_retry_heap: list[tuple[float, int, str]] = []
        self._write_retry_deadlines: dict[str, float] = {}
        self._write_retry_sequence = 0
        self._write_predecessors: dict[str, str] = {}
        self._write_dependents: dict[str, set[str]] = {}
        self._write_retry_thread = threading.Thread(
            target=self._write_retry_loop,
            name="hindsight-write-retry",
            daemon=True,
        )
        self._shutdown_callers = 0
        self._read_adapter_calls = 0
        self._read_adapter_calls_lock = threading.Lock()
        self._adapter_calls: dict[
            str, tuple[str, dict[str, Any], Future[Any]]
        ] = {}
        self._adapter_calls_lock = threading.Lock()
        self._shutdown_event = threading.Event()
        self._shutdown_started = False
        self._retirement_pending = False
        self._retirement_finalizer: threading.Thread | None = None
        self._used_path = self.state_dir / "used_nonces.json"
        self._revoked_path = self.state_dir / "revoked_nonces.json"
        self._work_path = self.state_dir / "durable_work.json"
        self._lease_path = self.state_dir / "broker.lease"
        self._generation_lease_path = self.state_dir / "generation.lease"
        self._generation = secrets.token_hex(32)
        self._handle_gc_cursor = ""
        self._closed = False
        try:
            self._work = self._install_generation()
        except Exception:
            self._read_executor.shutdown(wait=False, cancel_futures=True)
            self._write_executor.shutdown(wait=False, cancel_futures=True)
            os.close(self._handles_dir_fd)
            os.close(self._state_dir_fd)
            self._handles_dir_fd = -1
            self._state_dir_fd = -1
            raise
        try:
            self._used = set(self._work["used_nonces"])
            self._revoked = set(self._work["revoked_nonces"])
            self._write_retry_thread.start()
            for item in tuple(self._work["queue"]):
                self._submit_write(item["queue_id"])
            self._flush_ledger_outbox()
        except Exception:
            self._closed = True
            self._shutdown_started = True
            self._shutdown_event.set()
            with self._lock:
                self._stop_write_retry_scheduler_locked()
            if self._write_retry_thread.ident is not None:
                self._write_retry_thread.join(timeout=1)
            self._read_executor.shutdown(wait=True, cancel_futures=True)
            self._write_executor.shutdown(wait=True, cancel_futures=True)
            if self._handles_dir_fd >= 0:
                os.close(self._handles_dir_fd)
                self._handles_dir_fd = -1
            if self._state_dir_fd >= 0:
                os.close(self._state_dir_fd)
                self._state_dir_fd = -1
            raise

    def shutdown(self, *, timeout_seconds: float = 2) -> dict[str, int | bool]:
        with self._lock:
            self._shutdown_callers += 1
        try:
            return self._shutdown(timeout_seconds=timeout_seconds)
        finally:
            with self._lock:
                self._shutdown_callers -= 1
                self._maybe_close_state_directories_locked()

    def _shutdown(self, *, timeout_seconds: float) -> dict[str, int | bool]:
        timeout = self._timeout(timeout_seconds)
        with self._lock:
            self._shutdown_started = True
            if self._closed and not self._retirement_pending:
                return {
                    "undrained": len(self._work["queue"]),
                    "active_reads": sum(
                        not future.done() for future in self._read_futures
                    ),
                    "active_writes": sum(
                        not future.done() for future in self._write_futures
                    ),
                    "retired": False,
                    "retirement_pending": False,
                }
        try:
            ledger_flushed = self._flush_ledger_outbox()
        except BrokerError as error:
            if error.code != "BROKER_RETIRED":
                raise
            ledger_flushed = True
        deadline = time.monotonic() + timeout
        retired = False
        with self._lock:
            already_closed = self._closed
            if ledger_flushed:
                try:
                    if not self._retire_generation(deadline=deadline):
                        self._retirement_pending = True
                        self._schedule_deferred_retirement_locked()
                    else:
                        self._retirement_pending = False
                        retired = True
                except BrokerError as error:
                    if error.code != "BROKER_RETIRED":
                        raise
                    self._retirement_pending = False
                    retired = True
            else:
                self._retirement_pending = True
                self._schedule_deferred_retirement_locked()
            if already_closed:
                return {
                    "undrained": len(self._work["queue"]),
                    "active_reads": sum(
                        not future.done() for future in self._read_futures
                    ),
                    "active_writes": sum(
                        not future.done() for future in self._write_futures
                    ),
                    "retired": retired,
                    "retirement_pending": self._retirement_pending,
                }
            self._closed = True
            self._shutdown_event.set()
            self._stop_write_retry_scheduler_locked()
            for future in self._read_futures:
                future.cancel()
            for future in self._write_futures:
                future.cancel()
        while time.monotonic() < deadline:
            with self._lock:
                if not self._read_futures and not self._write_futures:
                    break
            time.sleep(min(0.005, max(0, deadline - time.monotonic())))
        self._read_executor.shutdown(wait=False, cancel_futures=True)
        self._write_executor.shutdown(wait=False, cancel_futures=True)
        if (
            self._write_retry_thread.ident is not None
            and self._write_retry_thread is not threading.current_thread()
        ):
            self._write_retry_thread.join(
                timeout=max(0.0, deadline - time.monotonic())
            )
        with self._lock:
            result = {
                "undrained": len(self._work["queue"]),
                "active_reads": sum(not future.done() for future in self._read_futures),
                "active_writes": sum(not future.done() for future in self._write_futures),
                "retired": retired,
                "retirement_pending": self._retirement_pending,
            }
            self._maybe_close_state_directories_locked()
            return result

    def _maybe_close_state_directories_locked(self) -> None:
        if (
            self._state_dir_fd < 0
            or not self._closed
            or self._retirement_pending
            or self._retirement_finalizer is not None
            or self._shutdown_callers
            or self._read_futures
            or self._write_futures
            or self._write_futures_by_queue_id
            or self._write_retry_deadlines
            or self._write_predecessors
            or self._write_dependents
        ):
            return
        os.close(self._handles_dir_fd)
        os.close(self._state_dir_fd)
        self._handles_dir_fd = -1
        self._state_dir_fd = -1

    def _ensure_runtime_open(self) -> None:
        if self._shutdown_started or self._closed:
            raise BrokerError("BROKER_CLOSED")

    def _owns_generation(self) -> bool:
        try:
            return _read_json(
                self._work_path, {}, directory_fd=self._state_dir_fd
            ).get("generation") == self._generation
        except BrokerError:
            return False

    def _lease_descriptor(self) -> int:
        descriptor = os.open(
            self._lease_path.name,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
            dir_fd=self._state_dir_fd,
        )
        try:
            os.fchmod(descriptor, 0o600)
            self._validate_state_file(descriptor, "broker lease")
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    def _generation_lease_descriptor(self) -> int:
        descriptor = os.open(
            self._generation_lease_path.name,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
            dir_fd=self._state_dir_fd,
        )
        try:
            os.fchmod(descriptor, 0o600)
            self._validate_state_file(descriptor, "generation lease")
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    @staticmethod
    def _validate_state_file(descriptor: int, label: str) -> None:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise OSError(f"{label} is unsafe")

    @staticmethod
    def _empty_work() -> dict[str, Any]:
        return {
            "schema_version": 9,
            "queue": [], "completed": {}, "sessions": {}, "used_nonces": [],
            "revoked_nonces": [], "exchanges": {}, "ledger_outbox": {},
            "expirations": {
                "sessions": {}, "used_nonces": {}, "revoked_nonces": {},
                "completed": {},
            },
            "generation": "initial",
        }

    def _migrate_work(self, value: Any) -> tuple[Any, bool]:
        if not isinstance(value, dict):
            return value, False
        migrated = deepcopy(value)
        changed = False
        current_keys = set(self._empty_work())
        version_three_keys = current_keys - {"expirations"}
        version_two_keys = version_three_keys - {"ledger_outbox"}
        legacy_two_keys = version_two_keys - {"schema_version"}
        legacy_three_keys = version_three_keys - {"schema_version"}
        legacy_four_keys = current_keys - {"schema_version"}
        if "schema_version" not in migrated:
            if frozenset(migrated) not in {
                frozenset(legacy_two_keys), frozenset(legacy_three_keys),
                frozenset(legacy_four_keys),
            }:
                return value, False
            migrated["schema_version"] = (
                4 if "expirations" in migrated
                else 3 if "ledger_outbox" in migrated
                else 2
            )
            exchanges = migrated.get("exchanges")
            if isinstance(exchanges, dict):
                migrated_exchanges = {}
                for handle, result in exchanges.items():
                    receipt = self._legacy_exchange_receipt(
                        handle, result, migrated
                    )
                    if receipt is not None:
                        migrated_exchanges[handle] = {**result, "receipt": receipt}
                migrated["exchanges"] = migrated_exchanges
            changed = True
        if (
            type(migrated.get("schema_version")) is int
            and migrated["schema_version"] < 9
            and isinstance(migrated.get("queue"), list)
            and any(
                not isinstance(item, Mapping)
                or item.get("method") != "reflect"
                for item in migrated["queue"]
            )
        ):
            # Pre-v9 durable writes were admitted under a different adapter
            # contract. Transcript entries lack the cleaned content required
            # now, while even complete-looking outcomes may already have been
            # accepted under the former document identity without a durable
            # operation ID. Replaying either form could lose or duplicate a
            # write, so only the prior broker may drain them. Legacy reflect
            # entries are safe to discard below because reflect is non-durable.
            raise BrokerError(
                "LEGACY_QUEUE_NOT_DRAINED",
                "drain legacy transcript and outcome writes with the prior "
                "broker version before upgrading",
            )
        if migrated.get("schema_version") == 2 and set(migrated) == version_two_keys:
            sessions = migrated.get("sessions")
            if (
                self.ledger_path is not None
                and isinstance(sessions, Mapping)
                and any(
                    isinstance(session, Mapping)
                    and session.get("closed") is True
                    for session in sessions.values()
                )
            ):
                raise BrokerError("STATE_INVALID")
            migrated["schema_version"] = 3
            migrated["ledger_outbox"] = {}
            changed = True
        if migrated.get("schema_version") == 3 and set(migrated) == version_three_keys:
            if (
                not isinstance(migrated.get("exchanges"), dict)
                or not isinstance(migrated.get("sessions"), dict)
                or not isinstance(migrated.get("completed"), dict)
                or not isinstance(migrated.get("ledger_outbox"), dict)
                or not isinstance(migrated.get("queue"), list)
                or not isinstance(migrated.get("used_nonces"), list)
                or not isinstance(migrated.get("revoked_nonces"), list)
            ):
                return value, False
            now = self.clock()
            exchange_expirations = {
                result.get("session_id"): result.get("expires_at")
                for result in migrated.get("exchanges", {}).values()
                if isinstance(result, Mapping)
                and isinstance(result.get("session_id"), str)
                and type(result.get("expires_at")) in (int, float)
                and math.isfinite(result["expires_at"])
            }
            migrated["expirations"] = {
                "sessions": {
                    session_id: exchange_expirations.get(session_id, now + 300)
                    for session_id in migrated.get("sessions", {})
                },
                "used_nonces": {
                    nonce: next(
                        (
                            result["expires_at"]
                            for result in migrated.get("exchanges", {}).values()
                            if isinstance(result, Mapping)
                            and result.get("nonce_digest") == nonce
                            and type(result.get("expires_at")) in (int, float)
                            and math.isfinite(result["expires_at"])
                        ),
                        now + 300,
                    )
                    for nonce in migrated.get("used_nonces", [])
                },
                "revoked_nonces": {
                    nonce: next(
                        (
                            exchange_expirations.get(session_id, now + 300)
                            for session_id, session in migrated.get("sessions", {}).items()
                            if isinstance(session, Mapping)
                            and session.get("revocation_digest") == nonce
                        ),
                        now + 300,
                    )
                    for nonce in migrated.get("revoked_nonces", [])
                },
                "completed": {
                    state_key: now + COMPLETED_RETENTION_SECONDS
                    for state_key in migrated.get("completed", {})
                },
            }
            for completed in migrated.get("completed", {}).values():
                if isinstance(completed, dict):
                    completed["adapter_result"] = None
            migrated["schema_version"] = 4
            changed = True
        if migrated.get("schema_version") == 4 and isinstance(
            migrated.get("queue"), list
        ):
            for item in migrated["queue"]:
                if isinstance(item, dict):
                    if "in_flight" not in item:
                        item["in_flight"] = False
                        changed = True
                    if "operation_id" not in item:
                        item["operation_id"] = None
                        changed = True
            completed = migrated.get("completed")
            if not isinstance(completed, dict):
                return value, False
            for record in completed.values():
                if (
                    isinstance(record, dict)
                    and record.get("adapter_result") == {"accepted": True}
                ):
                    # The former boolean acknowledgement did not persist the
                    # synthesis. Preserve replay safety and surface a bounded
                    # unavailable result instead of inventing reflection text.
                    record["adapter_result"] = None
                    changed = True
            migrated["schema_version"] = 5
            changed = True
        if migrated.get("schema_version") == 5 and isinstance(
            migrated.get("queue"), list
        ):
            for item in migrated["queue"]:
                if not isinstance(item, dict):
                    return value, False
                item["poll_attempts"] = 0
            migrated["schema_version"] = 6
            changed = True
        if migrated.get("schema_version") == 6 and isinstance(
            migrated.get("sessions"), dict
        ):
            for session in migrated["sessions"].values():
                if not isinstance(session, dict):
                    return value, False
                session["watermark_receipts"] = {}
            migrated["schema_version"] = 7
            changed = True
        if migrated.get("schema_version") == 7 and isinstance(
            migrated.get("completed"), dict
        ):
            for record in migrated["completed"].values():
                if not isinstance(record, dict):
                    return value, False
                record["session_id"] = None
                record["method"] = None
                record["operation_id"] = None
            migrated["schema_version"] = 8
            changed = True
        if migrated.get("schema_version") == 8 and isinstance(
            migrated.get("completed"), dict
        ) and isinstance(migrated.get("queue"), list):
            retained_queue = []
            for item in migrated["queue"]:
                if (
                    isinstance(item, Mapping)
                    and item.get("method") == "reflect"
                ):
                    continue
                retained_queue.append(item)
            migrated["queue"] = retained_queue
            removed_completed = {
                state_key for state_key, record in migrated["completed"].items()
                if state_key.startswith("reflect:")
                or (
                    isinstance(record, Mapping)
                    and record.get("method") == "reflect"
                )
            }
            migrated["completed"] = {
                state_key: record
                for state_key, record in migrated["completed"].items()
                if state_key not in removed_completed
            }
            expirations = migrated.get("expirations")
            if isinstance(expirations, dict) and isinstance(
                expirations.get("completed"), dict
            ):
                for state_key in removed_completed:
                    expirations["completed"].pop(state_key, None)
            for completion_order, record in enumerate(
                migrated["completed"].values(), start=1
            ):
                if not isinstance(record, dict):
                    return value, False
                record["completion_order"] = completion_order
            migrated["schema_version"] = 9
            changed = True
        return migrated, changed

    def _legacy_exchange_receipt(
        self, handle: Any, result: Any, work: Mapping[str, Any]
    ) -> str | None:
        if (
            not isinstance(handle, str)
            or re.fullmatch(r"[0-9a-f]{64}", handle) is None
            or not isinstance(result, dict)
            or set(result)
            != {"session_id", "capability", "expires_at", "nonce_digest"}
            or not isinstance(result["session_id"], str)
            or IDENTIFIER.fullmatch(result["session_id"]) is None
            or not isinstance(result["capability"], str)
            or type(result["expires_at"]) not in (int, float)
            or not math.isfinite(result["expires_at"])
            or not isinstance(result["nonce_digest"], str)
            or DIGEST.fullmatch(result["nonce_digest"]) is None
            or not isinstance(work.get("used_nonces"), list)
            or result["nonce_digest"] not in work["used_nonces"]
            or not isinstance(work.get("sessions"), dict)
        ):
            return None
        try:
            claims = self._verify(
                result["capability"], "capability", allow_expired=True
            )
        except BrokerError:
            return None
        session = work["sessions"].get(result["session_id"])
        if (
            claims["session_id"] != result["session_id"]
            or claims["expires_at"] != result["expires_at"]
            or not isinstance(session, dict)
            or session.get("nonce_digest") != _sha256_text(claims["nonce"])
            or session.get("revocation_digest")
            != _sha256_text(claims["revocation_id"])
        ):
            return None
        return self._sign({
            "kind": "exchange-recovery",
            "handle": handle,
            "capability_digest": _sha256_text(result["capability"]),
            "nonce_digest": result["nonce_digest"],
            "session_id": result["session_id"],
            "expires_at": result["expires_at"],
        })

    def _read_work(
        self, *, allow_ledger_unavailable: bool = False
    ) -> dict[str, Any]:
        value, migrated = self._migrate_work(
            _read_json(
                self._work_path,
                self._empty_work(),
                directory_fd=self._state_dir_fd,
            )
        )
        validated = self._validate_work(
            value, allow_ledger_unavailable=allow_ledger_unavailable
        )
        if migrated:
            _atomic_json(
                self._work_path,
                validated,
                directory_fd=self._state_dir_fd,
            )
        return validated

    def _validate_work(
        self, value: Any, *, allow_ledger_unavailable: bool = False
    ) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != set(self._empty_work()):
            raise BrokerError("STATE_INVALID")
        if (
                value["schema_version"] != 9
            or not isinstance(value["exchanges"], dict)
            or not isinstance(value["sessions"], dict)
            or not isinstance(value["used_nonces"], list)
            or not isinstance(value["revoked_nonces"], list)
            or not isinstance(value["queue"], list)
            or len(value["queue"]) > MAX_DURABLE_QUEUE_ENTRIES
            or not isinstance(value["completed"], dict)
            or not isinstance(value["ledger_outbox"], dict)
            or not isinstance(value["expirations"], dict)
            or set(value["expirations"])
            != {"sessions", "used_nonces", "revoked_nonces", "completed"}
            or not isinstance(value["generation"], str)
            or IDENTIFIER.fullmatch(value["generation"]) is None
            or not all(
                isinstance(item, str) and DIGEST.fullmatch(item)
                for item in value["used_nonces"]
            )
            or not all(
                isinstance(item, str) and DIGEST.fullmatch(item)
                for item in value["revoked_nonces"]
            )
            or len(value["used_nonces"]) != len(set(value["used_nonces"]))
            or len(value["revoked_nonces"])
            != len(set(value["revoked_nonces"]))
        ):
            raise BrokerError("STATE_INVALID")
        expiration_collections = {
            "sessions": value["sessions"],
            "used_nonces": value["used_nonces"],
            "revoked_nonces": value["revoked_nonces"],
            "completed": value["completed"],
        }
        for label, collection in expiration_collections.items():
            expirations = value["expirations"].get(label)
            expected = set(collection)
            if (
                not isinstance(expirations, dict)
                or set(expirations) != expected
                or any(
                    type(expires_at) not in (int, float)
                    or not math.isfinite(expires_at)
                    for expires_at in expirations.values()
                )
            ):
                raise BrokerError("STATE_INVALID")
        for outbox_id, entry in value["ledger_outbox"].items():
            if (
                not isinstance(outbox_id, str)
                or DIGEST.fullmatch(outbox_id) is None
                or not isinstance(entry, dict)
                or set(entry) != {"action_digest", "record"}
                or entry["action_digest"] != outbox_id
                or not isinstance(entry["record"], dict)
            ):
                raise BrokerError("STATE_INVALID")
            try:
                validate_record(entry["record"])
            except LedgerError:
                raise BrokerError("STATE_INVALID") from None
        if (
            value["ledger_outbox"]
            and self.ledger_path is None
            and not allow_ledger_unavailable
        ):
            raise BrokerError("LEDGER_UNAVAILABLE")
        for session_id, session in value["sessions"].items():
            if (
                not isinstance(session_id, str)
                or IDENTIFIER.fullmatch(session_id) is None
                or not isinstance(session, dict)
                or set(session)
                != {
                    "nonce_digest", "revocation_digest", "sequence",
                    "action_ids", "closed", "watermark_receipts",
                }
                or not isinstance(session["nonce_digest"], str)
                or DIGEST.fullmatch(session["nonce_digest"]) is None
                or not isinstance(session["revocation_digest"], str)
                or DIGEST.fullmatch(session["revocation_digest"]) is None
                or type(session["sequence"]) is not int
                or session["sequence"] < 0
                or session["sequence"] > MAX_REQUEST_SEQUENCE
                or not isinstance(session["action_ids"], list)
                or not all(
                    isinstance(action_id, str)
                    and IDENTIFIER.fullmatch(action_id)
                    for action_id in session["action_ids"]
                )
                or len(session["action_ids"])
                != len(set(session["action_ids"]))
                or len(session["action_ids"]) > MAX_SESSION_ACTION_IDS
                or not isinstance(session["watermark_receipts"], dict)
                or any(
                    not isinstance(receipt_action_id, str)
                    or receipt_action_id not in session["action_ids"]
                    or not isinstance(receipt, dict)
                    or set(receipt) != {
                        "method", "state_key", "watermark",
                        "reported_watermark", "request_digest",
                        "idempotency_key",
                    }
                    or receipt["method"] not in {
                        "transcript_checkpoint", "retain_outcome",
                    }
                    or not isinstance(receipt["state_key"], str)
                    or not receipt["state_key"]
                    or len(receipt["state_key"]) > 1024
                    or not isinstance(receipt["watermark"], list)
                    or len(receipt["watermark"]) != 2
                    or not all(
                        type(part) is int and part >= 0
                        for part in receipt["watermark"]
                    )
                    or not isinstance(receipt["reported_watermark"], list)
                    or len(receipt["reported_watermark"]) != 2
                    or not all(
                        type(part) is int and part >= 0
                        for part in receipt["reported_watermark"]
                    )
                    or not isinstance(receipt["request_digest"], str)
                    or DIGEST.fullmatch(receipt["request_digest"]) is None
                    or not isinstance(receipt["idempotency_key"], str)
                    or DIGEST.fullmatch(receipt["idempotency_key"]) is None
                    for receipt_action_id, receipt
                    in session["watermark_receipts"].items()
                )
                or type(session["closed"]) is not bool
            ):
                raise BrokerError("STATE_INVALID")
        for item in value["queue"]:
            if (
                not isinstance(item, dict)
                or set(item)
                != {
                    "queue_id", "session_id", "route", "method", "state_key",
                    "watermark", "request_digest", "idempotency_key",
                    "adapter_request", "attempts", "last_error", "next_retry",
                    "in_flight", "operation_id", "poll_attempts",
                    "authorized_bank", "policy_digest",
                    "artifact_digest",
                }
                or not isinstance(item["queue_id"], str)
                or re.fullmatch(r"[0-9a-f]{32}", item["queue_id"]) is None
                or not isinstance(item["session_id"], str)
                or IDENTIFIER.fullmatch(item["session_id"]) is None
                or item["session_id"] not in value["sessions"]
                or not isinstance(item["route"], str)
                or IDENTIFIER.fullmatch(item["route"]) is None
                or not self._valid_bank_reference(item["authorized_bank"])
                or not isinstance(item["policy_digest"], str)
                or DIGEST.fullmatch(item["policy_digest"]) is None
                or not isinstance(item["artifact_digest"], str)
                or DIGEST.fullmatch(item["artifact_digest"]) is None
                or not isinstance(item["method"], str)
                or item["method"]
                not in ("transcript_checkpoint", "retain_outcome")
                or not isinstance(item["state_key"], str)
                or not item["state_key"]
                or len(item["state_key"]) > 1024
                or not isinstance(item["watermark"], list)
                or len(item["watermark"]) != 2
                or not all(type(part) is int and part >= 0 for part in item["watermark"])
                or not isinstance(item["request_digest"], str)
                or DIGEST.fullmatch(item["request_digest"]) is None
                or not isinstance(item["idempotency_key"], str)
                or DIGEST.fullmatch(item["idempotency_key"]) is None
                or not isinstance(item["adapter_request"], dict)
                or item["adapter_request"].get("idempotency_key")
                != item["idempotency_key"]
                or item["adapter_request"].get("session_id")
                != item["session_id"]
                or type(item["attempts"]) is not int
                or item["attempts"] < 0
                or type(item["poll_attempts"]) is not int
                or item["poll_attempts"] < 0
                or item["poll_attempts"] > 1_000_000
                or type(item["in_flight"]) is not bool
                or (
                    item["operation_id"] is not None
                    and (
                        not isinstance(item["operation_id"], str)
                        or IDENTIFIER.fullmatch(item["operation_id"]) is None
                    )
                )
                or item["last_error"] not in (None, "ADAPTER_UNAVAILABLE")
                or (
                    item["next_retry"] is not None
                    and (
                        type(item["next_retry"]) not in (int, float)
                        or not math.isfinite(item["next_retry"])
                    )
                )
            ):
                raise BrokerError("STATE_INVALID")
        try:
            if (
                any(
                    len(canonical_bytes(item))
                    > MAX_DURABLE_QUEUE_ENTRY_BYTES
                    for item in value["queue"]
                )
                or len(canonical_bytes(value["queue"]))
                > MAX_DURABLE_QUEUE_BYTES
            ):
                raise BrokerError("STATE_INVALID")
        except BrokerError:
            raise
        except (TypeError, ValueError):
            raise BrokerError("STATE_INVALID") from None
        for state_key, record in value["completed"].items():
            if (
                not isinstance(state_key, str)
                or not state_key
                or len(state_key) > 1024
                or not isinstance(record, dict)
                or set(record)
                != {
                    "watermark", "request_digest", "idempotency_key",
                    "adapter_result", "session_id", "method",
                    "operation_id", "completion_order",
                }
                or not isinstance(record["watermark"], list)
                or len(record["watermark"]) != 2
                or not all(
                    type(part) is int and part >= 0
                    for part in record["watermark"]
                )
                or not isinstance(record["request_digest"], str)
                or DIGEST.fullmatch(record["request_digest"]) is None
                or not isinstance(record["idempotency_key"], str)
                or DIGEST.fullmatch(record["idempotency_key"]) is None
                or (
                    record["session_id"] is not None
                    and (
                        not isinstance(record["session_id"], str)
                        or IDENTIFIER.fullmatch(record["session_id"]) is None
                    )
                )
                or record["method"] not in {
                    None, "transcript_checkpoint", "retain_outcome",
                }
                or (
                    record["operation_id"] is not None
                    and (
                        not isinstance(record["operation_id"], str)
                        or IDENTIFIER.fullmatch(record["operation_id"]) is None
                    )
                )
                or record["adapter_result"] is not None
                or type(record["completion_order"]) is not int
                or record["completion_order"] < 0
            ):
                raise BrokerError("STATE_INVALID")
        if not self._durable_aggregates_within_limits(value):
            raise BrokerError("STATE_INVALID")
        for handle, result in value["exchanges"].items():
            if (
                not isinstance(handle, str)
                or re.fullmatch(r"[0-9a-f]{64}", handle) is None
                or not isinstance(result, dict)
                or set(result)
                != {"session_id", "capability", "expires_at", "nonce_digest", "receipt"}
                or not isinstance(result["session_id"], str)
                or IDENTIFIER.fullmatch(result["session_id"]) is None
                or not isinstance(result["capability"], str)
                or result["capability"].count(".") != 1
                or type(result["expires_at"]) not in (int, float)
                or not math.isfinite(result["expires_at"])
                or not isinstance(result["nonce_digest"], str)
                or DIGEST.fullmatch(result["nonce_digest"]) is None
                or not isinstance(result["receipt"], str)
                or result["receipt"].count(".") != 1
            ):
                raise BrokerError("STATE_INVALID")
            try:
                claims = self._verify(
                    result["capability"], "capability", allow_expired=True
                )
                receipt = self._verify(
                    result["receipt"], "exchange-recovery", allow_expired=True
                )
            except BrokerError:
                raise BrokerError("STATE_INVALID") from None
            if (
                claims["session_id"] != result["session_id"]
                or claims["expires_at"] != result["expires_at"]
                or receipt["handle"] != handle
                or receipt["capability_digest"] != _sha256_text(result["capability"])
                or receipt["nonce_digest"] != result["nonce_digest"]
                or receipt["session_id"] != result["session_id"]
                or receipt["expires_at"] != result["expires_at"]
                or result["nonce_digest"] not in value["used_nonces"]
            ):
                raise BrokerError("STATE_INVALID")
            # Recovery receipts expire independently; a fully validated durable
            # session may therefore remain after its recovery entry is pruned.
            session = value["sessions"].get(result["session_id"])
            if (
                not isinstance(session, dict)
                or set(session)
                != {
                    "nonce_digest", "revocation_digest", "sequence",
                    "action_ids", "closed", "watermark_receipts",
                }
                or session.get("nonce_digest") != _sha256_text(claims["nonce"])
                or session.get("revocation_digest")
                != _sha256_text(claims["revocation_id"])
            ):
                raise BrokerError("STATE_INVALID")
        return value

    def _sync_digest_mirrors(self, value: Mapping[str, Any]) -> None:
        _atomic_json(
            self._used_path,
            value["used_nonces"],
            directory_fd=self._state_dir_fd,
        )
        _atomic_json(
            self._revoked_path,
            value["revoked_nonces"],
            directory_fd=self._state_dir_fd,
        )

    def _install_generation(self) -> dict[str, Any]:
        generation_descriptor = self._generation_lease_descriptor()
        descriptor: int | None = None
        generation_locked = False
        state_locked = False
        try:
            descriptor = self._lease_descriptor()
            fcntl.flock(generation_descriptor, fcntl.LOCK_EX)
            generation_locked = True
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            state_locked = True
            current = self._read_work()
            value = deepcopy(current)
            self._prune_expired_state(value)
            for item in value["queue"]:
                item["in_flight"] = False
            value["generation"] = self._generation
            _atomic_json(
                self._work_path, value, directory_fd=self._state_dir_fd
            )
            try:
                self._sync_digest_mirrors(value)
            except OSError:
                pass
            self._prune_handle_files()
            return value
        finally:
            if descriptor is not None:
                if state_locked:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            if generation_locked:
                fcntl.flock(generation_descriptor, fcntl.LOCK_UN)
            os.close(generation_descriptor)

    def _retire_generation(self, *, deadline: float | None = None) -> bool:
        descriptor = self._generation_lease_descriptor()
        locked = False
        try:
            if deadline is None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                locked = True
            else:
                while True:
                    try:
                        fcntl.flock(
                            descriptor,
                            fcntl.LOCK_EX | fcntl.LOCK_NB,
                        )
                        locked = True
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            return False
                        time.sleep(
                            min(
                                0.005,
                                max(0.0, deadline - time.monotonic()),
                            )
                        )
            with self._lock:
                self._transaction(
                    lambda work: work.__setitem__(
                        "generation",
                        f"stopped-{secrets.token_hex(24)}",
                    )
                )
            return True
        finally:
            if locked:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _finish_deferred_retirement(self) -> None:
        completed = False
        try:
            while True:
                with self._lock:
                    if not self._retirement_pending:
                        completed = True
                        return
                if self._flush_ledger_outbox():
                    break
                time.sleep(0.05)
            with self._lock:
                still_pending = self._retirement_pending
            if still_pending:
                self._retire_generation()
            completed = True
        except BrokerError as error:
            if error.code == "BROKER_RETIRED":
                completed = True
            else:
                LOGGER.error(
                    "deferred broker retirement failed: %s",
                    error.code,
                )
        except OSError:
            LOGGER.error("deferred broker retirement failed: OS_ERROR")
        finally:
            with self._lock:
                self._retirement_finalizer = None
                if completed:
                    self._retirement_pending = False
                    self._maybe_close_state_directories_locked()

    def _schedule_deferred_retirement_locked(self) -> None:
        if (
            self._retirement_finalizer is not None
            and self._retirement_finalizer.is_alive()
        ):
            return
        finalizer = threading.Thread(
            target=self._finish_deferred_retirement,
            name="hindsight-generation-retire",
            daemon=True,
        )
        self._retirement_finalizer = finalizer
        try:
            finalizer.start()
        except Exception:
            self._retirement_finalizer = None
            raise

    def _transaction(
        self,
        mutation: Callable[[dict[str, Any]], Any],
        *,
        runtime: bool = False,
        allow_ledger_unavailable: bool = False,
    ) -> Any:
        descriptor = self._lease_descriptor()
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            if runtime:
                self._ensure_runtime_open()
            current = self._read_work(
                allow_ledger_unavailable=allow_ledger_unavailable
            )
            if current.get("generation") != self._generation:
                raise BrokerError("BROKER_RETIRED")
            value = deepcopy(current)
            self._prune_expired_state(value)
            result = mutation(value)
            self._persist_locked_work(value)
            return result
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _persist_locked_work(self, value: dict[str, Any]) -> None:
        """Publish canonical work while the caller owns the generation lease."""
        if not self._durable_aggregates_within_limits(value):
            raise BrokerError("STATE_INVALID")
        try:
            _atomic_json(
                self._work_path, value, directory_fd=self._state_dir_fd
            )
        except _AtomicJsonError as error:
            if not error.replaced:
                raise
            # The new state is already visible. Adopt the exact durable value
            # so callers cannot replay a transition after a post-replace fsync
            # or permission failure.
            value = self._read_work()
            self._adopt_work(value)
            raise
        self._adopt_work(value)
        try:
            self._sync_digest_mirrors(value)
        except OSError:
            pass

    def _adopt_work(self, value: dict[str, Any]) -> None:
        self._work = value
        self._used = set(value["used_nonces"])
        self._revoked = set(value["revoked_nonces"])

    def _completed_reservation_bytes(self, item: Mapping[str, Any]) -> int:
        record = {
            "watermark": item["watermark"],
            "request_digest": item["request_digest"],
            "idempotency_key": item["idempotency_key"],
            "adapter_result": None,
            "session_id": item["session_id"],
            "method": item["method"],
            "operation_id": item["operation_id"] or ("x" * 128),
            "completion_order": (
                MAX_COMPLETED_ENTRIES + MAX_DURABLE_QUEUE_ENTRIES
            ),
        }
        return len(canonical_bytes({item["state_key"]: record}))

    def _normalize_completion_orders(self, work: dict[str, Any]) -> None:
        """Keep completion ranks bounded while preserving their total order."""

        ordered = sorted(
            work["completed"].items(),
            key=lambda item: (item[1]["completion_order"], item[0]),
        )
        for completion_order, (_state_key, record) in enumerate(
            ordered, start=1
        ):
            record["completion_order"] = completion_order

    def _durable_aggregates_within_limits(
        self, work: Mapping[str, Any]
    ) -> bool:
        try:
            completed = work["completed"]
            ledger_outbox = work["ledger_outbox"]
            reservations: dict[str, int] = {}
            for item in work["queue"]:
                state_key = item["state_key"]
                reservations[state_key] = max(
                    reservations.get(state_key, 0),
                    self._completed_reservation_bytes(item),
                )
            return (
                isinstance(completed, Mapping)
                and len(completed) <= MAX_COMPLETED_ENTRIES
                and len(canonical_bytes(completed))
                + sum(reservations.values()) <= MAX_COMPLETED_BYTES
                and isinstance(ledger_outbox, Mapping)
                and len(ledger_outbox) <= MAX_LEDGER_OUTBOX_ENTRIES
                and len(canonical_bytes(ledger_outbox))
                <= MAX_LEDGER_OUTBOX_BYTES
                and isinstance(work["sessions"], Mapping)
                and len(work["sessions"]) <= MAX_DURABLE_SESSIONS
                and len(canonical_bytes(work["sessions"]))
                <= MAX_DURABLE_SESSION_BYTES
                and isinstance(work["exchanges"], Mapping)
                and len(work["exchanges"]) <= MAX_DURABLE_EXCHANGES
                and len(canonical_bytes(work["exchanges"]))
                <= MAX_DURABLE_EXCHANGE_BYTES
                and isinstance(work["used_nonces"], list)
                and isinstance(work["revoked_nonces"], list)
                and len(work["used_nonces"]) <= MAX_DURABLE_NONCES
                and len(work["revoked_nonces"]) <= MAX_DURABLE_NONCES
                and len(canonical_bytes(work["used_nonces"]))
                <= MAX_DURABLE_NONCE_BYTES
                and len(canonical_bytes(work["revoked_nonces"]))
                <= MAX_DURABLE_NONCE_BYTES
            )
        except (KeyError, TypeError, ValueError):
            return False

    def _prune_expired_state(self, work: dict[str, Any]) -> None:
        now = self.clock()
        work["exchanges"] = {
            handle: result
            for handle, result in work["exchanges"].items()
            if isinstance(result, Mapping)
            and type(result.get("expires_at")) in (int, float)
            and result["expires_at"] > now
        }
        protected_sessions = {
            item["session_id"] for item in work["queue"]
        }
        protected_sessions.update(
            entry["record"]["correlation_id"]
            for entry in work["ledger_outbox"].values()
        )
        protected_sessions.update(
            result["session_id"] for result in work["exchanges"].values()
        )
        for session_id, expires_at in tuple(
            work["expirations"]["sessions"].items()
        ):
            if expires_at <= now and session_id not in protected_sessions:
                work["sessions"].pop(session_id, None)
                del work["expirations"]["sessions"][session_id]
        for label in ("used_nonces", "revoked_nonces"):
            retained = []
            for nonce in work[label]:
                if work["expirations"][label][nonce] > now:
                    retained.append(nonce)
                else:
                    del work["expirations"][label][nonce]
            work[label] = retained
        for state_key, expires_at in tuple(
            work["expirations"]["completed"].items()
        ):
            if expires_at <= now:
                work["completed"].pop(state_key, None)
                del work["expirations"]["completed"][state_key]
        self._normalize_completion_orders(work)

    def _prune_handle_files(self) -> None:
        with os.scandir(self._handles_dir_fd) as entries:
            after = heapq.nsmallest(
                MAX_HANDLE_GC_FILES,
                (
                    entry.name for entry in entries
                    if entry.name > self._handle_gc_cursor
                ),
            )
        selected_names = after
        if len(selected_names) < MAX_HANDLE_GC_FILES:
            with os.scandir(self._handles_dir_fd) as entries:
                selected_names.extend(
                    heapq.nsmallest(
                        MAX_HANDLE_GC_FILES - len(selected_names),
                        (
                            entry.name for entry in entries
                            if entry.name <= self._handle_gc_cursor
                        ),
                    )
                )
        now = self.clock()
        if selected_names:
            self._handle_gc_cursor = selected_names[-1]
        for name in selected_names:
            if re.fullmatch(r"[0-9a-f]{64}\.json", name) is None:
                continue
            try:
                metadata = os.stat(
                    name, dir_fd=self._handles_dir_fd, follow_symlinks=False
                )
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink != 1
                    or metadata.st_uid != os.geteuid()
                ):
                    continue
                record = _read_json(
                    Path(name), None, directory_fd=self._handles_dir_fd
                )
                if not isinstance(record, dict) or set(record) != {"envelope"}:
                    continue
                claims = self._verify(
                    record["envelope"], "exchange", allow_expired=True
                )
                if claims["expires_at"] <= now:
                    os.unlink(name, dir_fd=self._handles_dir_fd)
            except (BrokerError, OSError):
                continue

    def _require_handle_admission(self, record: Mapping[str, Any]) -> None:
        """Bound durable handle count and bytes before creating a new file."""

        try:
            total = 0
            count = 0
            with os.scandir(self._handles_dir_fd) as entries:
                for entry in entries:
                    if re.fullmatch(
                        r"[0-9a-f]{64}\.json", entry.name
                    ) is None:
                        continue
                    count += 1
                    if count >= MAX_DURABLE_HANDLES:
                        raise BrokerError("STATE_FULL")
                    metadata = entry.stat(follow_symlinks=False)
                    if (
                        not stat.S_ISREG(metadata.st_mode)
                        or metadata.st_uid != os.geteuid()
                        or metadata.st_nlink != 1
                        or stat.S_IMODE(metadata.st_mode) & 0o022
                    ):
                        raise BrokerError("STATE_INVALID")
                    total += metadata.st_size
                    if total > MAX_DURABLE_HANDLE_BYTES:
                        raise BrokerError("STATE_FULL")
            if count >= MAX_DURABLE_HANDLES:
                raise BrokerError("STATE_FULL")
            if total + len(canonical_bytes(record)) > MAX_DURABLE_HANDLE_BYTES:
                raise BrokerError("STATE_FULL")
        except BrokerError:
            raise
        except (OSError, TypeError, ValueError):
            raise BrokerError("STATE_INVALID") from None

    def _sign(self, claims: Mapping[str, Any]) -> str:
        body = canonical_bytes(claims)
        signature = hmac.new(self.__signing_key, body, hashlib.sha256).digest()
        return f"{_b64encode(body)}.{_b64encode(signature)}"

    def _verify(
        self, token: str, kind: str, *, allow_expired: bool = False
    ) -> dict[str, Any]:
        if not isinstance(token, str) or token.count(".") != 1:
            raise BrokerError("CAPABILITY_INVALID")
        encoded, encoded_signature = token.split(".")
        body, supplied = _b64decode(encoded), _b64decode(encoded_signature)
        expected = hmac.new(self.__signing_key, body, hashlib.sha256).digest()
        if not hmac.compare_digest(supplied, expected):
            raise BrokerError("CAPABILITY_INVALID")
        try:
            claims = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BrokerError("CAPABILITY_INVALID") from error
        if not isinstance(claims, dict) or claims.get("kind") != kind:
            raise BrokerError("CAPABILITY_INVALID")
        expected = {
            "exchange": ENVELOPE_KEYS,
            "capability": CAPABILITY_KEYS,
            "exchange-recovery": RECOVERY_RECEIPT_KEYS,
        }.get(kind)
        if expected is None:
            raise BrokerError("CAPABILITY_INVALID")
        if set(claims) != expected:
            raise BrokerError("CAPABILITY_INVALID")
        if type(claims.get("expires_at")) not in (int, float) or (
            not allow_expired and self.clock() >= claims["expires_at"]
        ):
            raise BrokerError("EXPIRED")
        return claims

    def _validate_claims(self, claims: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(claims, Mapping) or set(claims) != CLAIM_KEYS:
            raise BrokerError("SCHEMA_INVALID")
        value = deepcopy(dict(claims))
        for key in ("session_id", "harness_id", "trust_class", "companion_id", "route"):
            if not isinstance(value[key], str) or not IDENTIFIER.fullmatch(value[key]):
                raise BrokerError("SCHEMA_INVALID")
        if not self._valid_bank_reference(value["home_bank"]):
            raise BrokerError("SCHEMA_INVALID")
        if not all(isinstance(value[key], str) and DIGEST.fullmatch(value[key]) for key in ("policy_digest", "artifact_digest")):
            raise BrokerError("SCHEMA_INVALID")
        methods = value["methods"]
        if not isinstance(methods, list) or len(methods) != len(set(methods)) or not set(methods) <= CAPABILITY_METHODS:
            raise BrokerError("SCHEMA_INVALID")
        value["methods"] = sorted(methods)
        return value

    def _validate_mint_request(
        self, request: Mapping[str, Any]
    ) -> dict[str, str]:
        if not isinstance(request, Mapping) or set(request) != MINT_REQUEST_KEYS:
            raise BrokerError("SCHEMA_INVALID")
        value = deepcopy(dict(request))
        if any(
            not isinstance(value[key], str)
            or IDENTIFIER.fullmatch(value[key]) is None
            for key in MINT_REQUEST_KEYS
        ):
            raise BrokerError("SCHEMA_INVALID")
        return value

    def _bootstrap_response(self, action_id: str, method: str, session_id: str, payload: Any) -> dict[str, Any]:
        action_digest = hashlib.sha256(canonical_bytes({
            "action_id": action_id, "method": method, "sequence": 0,
            "session_id": session_id, "capability_nonce_digest": None,
        })).hexdigest()
        return self._response(action_id, action_digest, "ok", payload)

    def session_mint(self, control_capability: str, request: Mapping[str, Any], *, ttl_seconds: float = 60) -> dict[str, Any]:
        with self._lock:
            self._ensure_runtime_open()
        self._prune_handle_files()
        requested = self._validate_mint_request(request)
        if type(ttl_seconds) not in (int, float) or not math.isfinite(ttl_seconds) or ttl_seconds <= 0 or ttl_seconds > 300:
            raise BrokerError("SCHEMA_INVALID")
        if self._mint_authorizer is None or not isinstance(control_capability, str) or not control_capability:
            raise BrokerError("MINT_DENIED")
        try:
            def authorize(request: Mapping[str, Any]) -> Any:
                return self._mint_authorizer(
                    request["control_capability"],
                    request["request"],
                    request["ttl_seconds"],
                )

            authorized = self._validate_claims(
                self._invoke_read_adapter_bounded(
                    authorize,
                    {
                        "control_capability": control_capability,
                        "request": requested,
                        "ttl_seconds": ttl_seconds,
                    },
                    self.adapter_call_timeout_seconds,
                )
            )
        except Exception:
            raise BrokerError("MINT_DENIED") from None
        value = authorized
        route = self.routes.get(value["route"])
        bank = self._route_bank(route, "MINT_DENIED")
        if (
            any(value[key] != requested[key] for key in MINT_REQUEST_KEYS)
            or value["policy_digest"] != self.policy_digest
            or value["artifact_digest"] != self.artifact_digest or route is None
            or dict(bank) != value["home_bank"]
        ):
            raise BrokerError("MINT_DENIED")
        with self._lock:
            self._ensure_runtime_open()
            descriptor = self._lease_descriptor()
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                current = self._read_work()
                if current["generation"] != self._generation:
                    raise BrokerError("BROKER_RETIRED")
                now = self.clock()
                envelope = {
                    **value, "kind": "exchange", "issued_at": now, "expires_at": now + ttl_seconds,
                    "nonce": secrets.token_hex(32), "revocation_id": secrets.token_hex(32),
                    "broker_generation": current["generation"],
                }
                handle = secrets.token_hex(32)
                handle_path = self.state_dir / "handles" / f"{handle}.json"
                handle_record = {"envelope": self._sign(envelope)}
                self._require_handle_admission(handle_record)
                _atomic_json(
                    handle_path,
                    handle_record,
                    directory_fd=self._handles_dir_fd,
                )
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
        return self._bootstrap_response("session-mint", "session_mint", value["session_id"], {"handle": handle})

    def session_exchange(self, handle: str) -> dict[str, Any]:
        if not isinstance(handle, str) or not re.fullmatch(r"[0-9a-f]{64}", handle):
            raise BrokerError("HANDLE_INVALID")
        path = self.state_dir / "handles" / f"{handle}.json"
        with self._lock:
            self._ensure_runtime_open()
            record = _read_json(
                path, None, directory_fd=self._handles_dir_fd
            )
            if not isinstance(record, dict) or set(record) != {"envelope"}:
                def recover(work):
                    recovered = work["exchanges"].get(handle)
                    if not recovered:
                        raise BrokerError("HANDLE_USED")
                    return deepcopy(recovered)
                recovered = self._transaction(recover, runtime=True)
                return self._exchange_response(recovered)
            claims = self._verify(record["envelope"], "exchange")
            nonce_digest = _sha256_text(claims["nonce"])
            now = self.clock()
            capability_claims = {
                **{key: claims[key] for key in CLAIM_KEYS}, "kind": "capability",
                "issued_at": now, "expires_at": claims["expires_at"],
                "nonce": secrets.token_hex(32), "revocation_id": claims["revocation_id"],
            }
            capability = self._sign(capability_claims)
            def exchange(work):
                if claims["broker_generation"] != work["generation"]:
                    raise BrokerError("BROKER_RETIRED")
                recovered = work["exchanges"].get(handle)
                if nonce_digest in work["used_nonces"]:
                    if recovered:
                        return deepcopy(recovered)
                    raise BrokerError("HANDLE_USED")
                if claims["session_id"] in work["sessions"]:
                    raise BrokerError("SESSION_ACTIVE")
                work["used_nonces"].append(nonce_digest)
                work["sessions"][claims["session_id"]] = {
                    "nonce_digest": _sha256_text(capability_claims["nonce"]),
                    "revocation_digest": _sha256_text(claims["revocation_id"]),
                    "sequence": 0, "action_ids": [], "closed": False,
                    "watermark_receipts": {},
                }
                work["expirations"]["used_nonces"][nonce_digest] = claims["expires_at"]
                work["expirations"]["sessions"][claims["session_id"]] = claims["expires_at"]
                recovered = {
                    "session_id": claims["session_id"], "capability": capability,
                    "expires_at": claims["expires_at"], "nonce_digest": nonce_digest,
                }
                recovered["receipt"] = self._sign({
                    "kind": "exchange-recovery",
                    "handle": handle,
                    "capability_digest": _sha256_text(capability),
                    "nonce_digest": nonce_digest,
                    "session_id": claims["session_id"],
                    "expires_at": claims["expires_at"],
                })
                work["exchanges"][handle] = recovered
                if not self._durable_aggregates_within_limits(work):
                    raise BrokerError("STATE_FULL")
                return deepcopy(recovered)
            recovered = self._transaction(exchange, runtime=True)
            try:
                os.unlink(path.name, dir_fd=self._handles_dir_fd)
            except FileNotFoundError:
                pass
        return self._exchange_response(recovered)

    def _exchange_response(self, recovered: Mapping[str, Any]) -> dict[str, Any]:
        return self._bootstrap_response("session-exchange", "session_exchange", recovered["session_id"], {
            "capability": recovered["capability"], "expires_at": recovered["expires_at"],
        })

    def _authorize(self, capability: str, method: str, sequence: Any, action_id: Any,
                   *, commit: bool = True) -> tuple[dict[str, Any], dict[str, Any], str]:
        with self._lock:
            self._ensure_runtime_open()
        claims = self._verify(capability, "capability")
        if method not in claims.get("methods", []):
            raise BrokerError("METHOD_DENIED")
        if claims.get("policy_digest") != self.policy_digest or claims.get("artifact_digest") != self.artifact_digest:
            raise BrokerError("DIGEST_DRIFT")
        route = self.routes.get(claims.get("route"))
        if not route:
            raise BrokerError("ROUTE_DENIED")
        bank = self._route_bank(route, "ROUTE_DENIED")
        if dict(bank) != claims["home_bank"]:
            raise BrokerError("ROUTE_DENIED")
        if not isinstance(route.get("adapter"), Adapter):
            raise BrokerError("ADAPTER_INVALID")
        if (
            type(sequence) is not int
            or not 1 <= sequence <= MAX_REQUEST_SEQUENCE
            or not isinstance(action_id, str)
            or not IDENTIFIER.fullmatch(action_id)
        ):
            raise BrokerError("SCHEMA_INVALID")
        nonce_digest = _sha256_text(claims["nonce"])
        action_digest = hashlib.sha256(canonical_bytes({
            "action_id": action_id, "method": method, "sequence": sequence,
            "session_id": claims["session_id"], "harness_id": claims["harness_id"],
            "capability_nonce_digest": nonce_digest,
        })).hexdigest()
        if commit:
            with self._lock:
                def authorize(work):
                    self._commit_action(
                        work, claims, method, sequence, action_id
                    )
                self._transaction(authorize, runtime=True)
        return claims, route, action_digest

    def _commit_action(
        self,
        work: dict[str, Any],
        claims: Mapping[str, Any],
        method: str,
        sequence: int,
        action_id: str,
    ) -> None:
        state = work["sessions"].get(claims["session_id"])
        if not state or state.get("nonce_digest") != _sha256_text(claims["nonce"]):
            raise BrokerError("CAPABILITY_INVALID")
        if state.get("revocation_digest") in work["revoked_nonces"] or state.get("closed"):
            raise BrokerError("REVOKED")
        if action_id in state["action_ids"]:
            raise BrokerError("ACTION_REPLAY")
        if sequence <= state["sequence"]:
            raise BrokerError("SEQUENCE_ROLLBACK")
        action_limit = (
            MAX_SESSION_ACTION_IDS
            if method == "session_close"
            else MAX_SESSION_ACTION_IDS - 1
        )
        if len(state["action_ids"]) >= action_limit:
            raise BrokerError("SESSION_ACTION_LIMIT")
        state["sequence"] = sequence
        state["action_ids"].append(action_id)
        if (
            len(work["sessions"]) > MAX_DURABLE_SESSIONS
            or len(canonical_bytes(work["sessions"]))
            > MAX_DURABLE_SESSION_BYTES
        ):
            raise BrokerError("STATE_FULL")

    @staticmethod
    def _validate_committed_replay(
        work: Mapping[str, Any], claims: Mapping[str, Any], action_id: str
    ) -> None:
        state = work["sessions"].get(claims["session_id"])
        if (
            not state
            or state.get("nonce_digest") != _sha256_text(claims["nonce"])
        ):
            raise BrokerError("CAPABILITY_INVALID")
        if (
            state.get("revocation_digest") in work["revoked_nonces"]
            or state.get("closed")
        ):
            raise BrokerError("REVOKED")
        if action_id not in state["action_ids"]:
            raise BrokerError("STATE_INVALID")

    def _validate_request(self, method: str, request: Any) -> dict[str, Any]:
        if not isinstance(request, dict) or _has_forbidden_key(request):
            raise BrokerError("SCHEMA_INVALID")
        required, optional = REQUEST_SCHEMAS[method]
        if not required <= set(request) or set(request) - required - optional:
            raise BrokerError("SCHEMA_INVALID")
        value = deepcopy(request)
        for key in required | optional:
            if key not in value:
                continue
            item = value[key]
            if key in {"epoch", "checkpoint", "limit"}:
                if type(item) is not int or item < 0 or item > 1_000_000:
                    raise BrokerError("SCHEMA_INVALID")
            elif key == "document_id":
                if not isinstance(item, str) or not IDENTIFIER.fullmatch(item):
                    raise BrokerError("SCHEMA_INVALID")
            elif key == "depth":
                if item not in {"routine", "deep"}:
                    raise BrokerError("SCHEMA_INVALID")
            elif not isinstance(item, str) or not item or len(item.encode("utf-8")) > self.max_payload_bytes:
                raise BrokerError("SCHEMA_INVALID")
        if len(canonical_bytes(value)) > self.max_payload_bytes:
            raise BrokerError("REQUEST_TOO_LARGE")
        return value

    def _response(self, action_id: str, action_digest: str, disposition: str, payload: Any,
                  diagnostic: Mapping[str, Any] | None = None) -> dict[str, Any]:
        try:
            if payload is not None and len(canonical_bytes(payload)) > self.max_payload_bytes:
                payload = None
                disposition = "unavailable"
                diagnostic = {"code": "RESPONSE_TOO_LARGE", "visible": True}
        except (TypeError, ValueError):
            payload = None
            disposition = "unavailable"
            diagnostic = {"code": "RESPONSE_INVALID", "visible": True}
        return {
            "schema_version": 1, "action_id": action_id, "action_digest": action_digest,
            "policy_digest": self.policy_digest, "artifact_digest": self.artifact_digest,
            "disposition": disposition, "payload": deepcopy(payload),
            "diagnostic": deepcopy(diagnostic),
        }

    def _adapter_payload(self, method: str, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or _has_forbidden_key(payload):
            raise BrokerError("RESPONSE_INVALID")
        if method == "recall":
            allowed = {"memories", "entities", "chunks", "source_facts"}
            if (
                "memories" not in payload
                or set(payload) - allowed
                or any(not isinstance(value, list) for value in payload.values())
            ):
                raise BrokerError("RESPONSE_INVALID")
        elif method == "mental_model_fetch" and (
            set(payload) != {"models"}
            or not isinstance(payload["models"], list)
        ):
            raise BrokerError("RESPONSE_INVALID")
        elif method == "session_status" and (
            set(payload) != {"status"}
            or not isinstance(payload["status"], str)
        ):
            raise BrokerError("RESPONSE_INVALID")
        elif method == "reflect" and not self._valid_reflect_adapter_result(
            payload
        ):
            raise BrokerError("RESPONSE_INVALID")
        try:
            if len(canonical_bytes(payload)) > self.max_payload_bytes:
                raise BrokerError("RESPONSE_TOO_LARGE")
        except BrokerError:
            raise
        except (TypeError, ValueError):
            raise BrokerError("RESPONSE_INVALID") from None
        return deepcopy(payload)

    @staticmethod
    def _valid_reflect_adapter_result(payload: Any) -> bool:
        if (
            not isinstance(payload, dict)
            or set(payload) not in (
                {"reflection"}, {"reflection", "based_on"}
            )
            or not isinstance(payload.get("reflection"), str)
            or not payload["reflection"]
        ):
            return False
        if "based_on" not in payload:
            return True
        based_on = payload["based_on"]
        if (
            not isinstance(based_on, dict)
            or set(based_on) != {
                "memory_ids", "mental_model_ids", "directive_ids",
                "source_resolution_required", "unresolved_memory_items",
            }
            or type(based_on["source_resolution_required"]) is not bool
            or type(based_on["unresolved_memory_items"]) is not int
            or not 0 <= based_on["unresolved_memory_items"] <= 128
        ):
            return False
        for key, limit in (
            ("memory_ids", 128), ("mental_model_ids", 16),
            ("directive_ids", 32),
        ):
            identifiers = based_on[key]
            if (
                not isinstance(identifiers, list)
                or len(identifiers) > limit
                or any(
                    not isinstance(identifier, str) or not identifier
                    or len(identifier.encode("utf-8")) > 256
                    or any(
                        not character.isprintable()
                        for character in identifier
                    )
                    for identifier in identifiers
                )
            ):
                return False
            if len(identifiers) != len(set(identifiers)):
                return False
        return based_on["source_resolution_required"] is bool(
            based_on["memory_ids"]
            or based_on["unresolved_memory_items"]
        )

    def _route_bank(
        self, route: Mapping[str, Any] | None, error_code: str
    ) -> Mapping[str, Any]:
        bank = route.get("bank") if isinstance(route, Mapping) else None
        if not self._valid_bank_reference(bank):
            raise BrokerError(error_code)
        if self.ledger_path is not None and "endpoint" not in bank:
            raise BrokerError(error_code)
        return bank

    @staticmethod
    def _valid_bank_reference(value: Any) -> bool:
        if not isinstance(value, Mapping) or set(value) not in (
            {"profile_id", "bank_id"},
            {"profile_id", "bank_id", "endpoint"},
        ):
            return False
        if any(
            not isinstance(value[key], str)
            or IDENTIFIER.fullmatch(value[key]) is None
            for key in ("profile_id", "bank_id")
        ):
            return False
        if "endpoint" not in value:
            return True
        endpoint = value["endpoint"]
        if not isinstance(endpoint, Mapping) or set(endpoint) != {
            "profile_id", "scheme", "host", "port", "tenant",
        }:
            return False
        if endpoint["profile_id"] != value["profile_id"]:
            return False
        if (
            type(endpoint["port"]) is not int
            or not 1 <= endpoint["port"] <= 65_535
        ):
            return False
        return all(
            isinstance(endpoint[key], str)
            and bool(endpoint[key])
            and len(endpoint[key].encode("utf-8")) <= 256
            and all(character.isprintable() for character in endpoint[key])
            for key in ("profile_id", "scheme", "host", "tenant")
        )

    def _reserve_adapter_call(
        self,
        queue_id: str,
        method: str,
        operation: Callable[[Mapping[str, Any]], Any],
        request: Mapping[str, Any],
    ) -> Future[Any]:
        request_value = deepcopy(dict(request))
        with self._adapter_calls_lock:
            existing = self._adapter_calls.get(queue_id)
            if existing is not None:
                existing_method, existing_request, future = existing
                if (
                    existing_method != method
                    or existing_request != request_value
                ):
                    raise BrokerError("STATE_INVALID")
            else:
                if sum(
                    not entry[2].done()
                    for entry in self._adapter_calls.values()
                ) >= MAX_IN_FLIGHT_ADAPTER_CALLS:
                    raise FutureTimeout()
                future = Future()
                self._adapter_calls[queue_id] = (
                    method, request_value, future
                )
                def invoke() -> None:
                    try:
                        future.set_result(operation(request_value))
                    except BaseException as error:
                        future.set_exception(error)

                thread = threading.Thread(
                    target=invoke,
                    name="hindsight-bounded-adapter-call",
                    daemon=True,
                )
                try:
                    thread.start()
                except Exception:
                    del self._adapter_calls[queue_id]
                    raise
        return future

    def _invoke_adapter_bounded(
        self,
        queue_id: str,
        method: str,
        operation: Callable[[Mapping[str, Any]], Any],
        request: Mapping[str, Any],
    ) -> Any:
        """Bound a reserved potentially stuck call without retaining state."""

        future = self._reserve_adapter_call(
            queue_id, method, operation, request
        )
        try:
            result = future.result(timeout=self.adapter_call_timeout_seconds)
        except FutureTimeout:
            raise
        except BaseException:
            with self._adapter_calls_lock:
                if self._adapter_calls.get(queue_id, (None, None, None))[2] is future:
                    del self._adapter_calls[queue_id]
            raise
        if getattr(future, "_hindsight_generation_lease_expired", False):
            with self._adapter_calls_lock:
                if self._adapter_calls.get(
                    queue_id, (None, None, None)
                )[2] is future:
                    del self._adapter_calls[queue_id]
            raise FutureTimeout()
        with self._adapter_calls_lock:
            if self._adapter_calls.get(queue_id, (None, None, None))[2] is future:
                del self._adapter_calls[queue_id]
        return result

    def _read_call(self, method: str, empty: Any, capability: str, sequence: int,
                   action_id: str, request: Mapping[str, Any], timeout_seconds: float,
                   unavailable_code: str = "MEMORY_UNAVAILABLE") -> dict[str, Any]:
        timeout = self._timeout(timeout_seconds)
        value = self._validate_request(method, request)
        with self._lock:
            _, route, action_digest = self._authorize(
                capability, method, sequence, action_id
            )
            operation = getattr(route["adapter"], method)
            self._ensure_runtime_open()
        try:
            payload = self._invoke_read_adapter_bounded(
                operation, value, timeout
            )
        except FutureTimeout:
            return self._response(action_id, action_digest, "unavailable", empty, {"code": unavailable_code, "visible": True})
        except Exception:
            return self._response(action_id, action_digest, "unavailable", empty, {"code": unavailable_code, "visible": True})
        try:
            payload = self._adapter_payload(method, payload)
        except BrokerError as error:
            return self._response(action_id, action_digest, "unavailable", empty, {"code": error.code, "visible": True})
        return self._response(action_id, action_digest, "ok", payload)

    def _invoke_read_adapter_bounded(
        self,
        operation: Callable[[Mapping[str, Any]], Any],
        request: Mapping[str, Any],
        timeout: float,
    ) -> Any:
        request_value = deepcopy(dict(request))
        with self._read_adapter_calls_lock:
            if self._read_adapter_calls >= MAX_IN_FLIGHT_READ_ADAPTER_CALLS:
                raise FutureTimeout()
            self._read_adapter_calls += 1
        future: Future[Any] = Future()
        with self._lock:
            try:
                self._ensure_runtime_open()
            except Exception:
                with self._read_adapter_calls_lock:
                    self._read_adapter_calls -= 1
                raise
            self._read_futures.add(future)
        future.add_done_callback(self._discard_read_future)

        def invoke() -> None:
            result: Any = None
            failure: BaseException | None = None
            try:
                if not future.set_running_or_notify_cancel():
                    return
                try:
                    result = operation(request_value)
                except BaseException as error:
                    failure = error
            finally:
                with self._read_adapter_calls_lock:
                    self._read_adapter_calls -= 1
            if failure is None:
                future.set_result(result)
            else:
                future.set_exception(failure)

        thread = threading.Thread(
            target=invoke,
            name="hindsight-bounded-read-adapter-call",
            daemon=True,
        )
        try:
            thread.start()
        except Exception:
            with self._read_adapter_calls_lock:
                self._read_adapter_calls -= 1
            future.cancel()
            raise
        return future.result(timeout=timeout)

    def _discard_read_future(self, future: Future[Any]) -> None:
        with self._lock:
            self._read_futures.discard(future)
            self._maybe_close_state_directories_locked()

    def _submit_write(self, queue_id: str, *, runtime: bool = False) -> None:
        with self._lock:
            if runtime:
                self._ensure_runtime_open()
            active = self._write_futures_by_queue_id.get(queue_id)
            if active is not None and not active.done():
                active.add_done_callback(
                    lambda _completed, selected=queue_id: (
                        self._schedule_write_retry(selected, 0.0)
                    )
                )
                return
            future = self._write_executor.submit(self._drain_item, queue_id)
            self._write_futures.add(future)
            self._write_futures_by_queue_id[queue_id] = future
        future.add_done_callback(
            lambda completed, selected=queue_id: self._discard_write_future(
                selected, completed
            )
        )

    def _discard_write_future(
        self, queue_id: str, future: Future[Any]
    ) -> None:
        with self._lock:
            self._write_futures.discard(future)
            if self._write_futures_by_queue_id.get(queue_id) is future:
                del self._write_futures_by_queue_id[queue_id]
            self._maybe_close_state_directories_locked()

    @staticmethod
    def _timeout(value: Any) -> float:
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or value > 30:
            raise BrokerError("SCHEMA_INVALID")
        return float(value)

    def recall(self, capability: str, *, sequence: int, action_id: str, request: Mapping[str, Any], timeout_seconds: float = 2) -> dict[str, Any]:
        return self._read_call("recall", {"memories": []}, capability, sequence, action_id, request, timeout_seconds)

    def mental_model_fetch(self, capability: str, *, sequence: int, action_id: str, request: Mapping[str, Any], timeout_seconds: float = 2) -> dict[str, Any]:
        return self._read_call("mental_model_fetch", {"models": []}, capability, sequence, action_id, request, timeout_seconds)

    def _document_lock(self, claims: Mapping[str, Any], document_id: str) -> threading.Lock:
        key = canonical_bytes({
            "bank": claims["home_bank"],
            "document_id": document_id,
            "session_id": claims["session_id"],
        })
        return self._document_locks[int.from_bytes(
            hashlib.sha256(key).digest()[:8], "big"
        ) % LOCK_STRIPES]

    @staticmethod
    def _checkpoint_series_key(
        method: str, claims: Mapping[str, Any], document_id: str,
    ) -> str:
        state_identity = {
            "bank": claims["home_bank"],
            "document_id": document_id,
            "method": method,
            "session_id": claims["session_id"],
        }
        body = canonical_bytes(state_identity)
        return f"checkpoint:{hashlib.sha256(body).hexdigest()}"

    @classmethod
    def _checkpoint_state_key(
        cls, method: str, claims: Mapping[str, Any], document_id: str,
        *, epoch: int | None = None, checkpoint: int | None = None,
    ) -> str:
        series_key = cls._checkpoint_series_key(
            method, claims, document_id
        )
        if method == "retain_outcome":
            return f"{series_key}:{epoch}:{checkpoint}"
        return f"{series_key}:{epoch}"

    def _work_lock(self, state_key: str) -> threading.Lock:
        index = int.from_bytes(
            hashlib.sha256(state_key.encode("utf-8")).digest()[:8], "big"
        ) % LOCK_STRIPES
        return self._work_locks[index]

    @staticmethod
    def _store_watermark_receipt(
        work: dict[str, Any], claims: Mapping[str, Any], action_id: str,
        *, method: str, state_key: str, watermark: list[int],
        reported_watermark: list[int], request_digest: str,
        action_digest: str,
    ) -> None:
        work["sessions"][claims["session_id"]]["watermark_receipts"][
            action_id
        ] = {
            "method": method,
            "state_key": state_key,
            "watermark": list(watermark),
            "reported_watermark": list(reported_watermark),
            "request_digest": request_digest,
            "idempotency_key": action_digest,
        }

    def _append_queue_item(
        self,
        work: dict[str, Any],
        item: dict[str, Any],
        *,
        supersede_state_key: str | None = None,
    ) -> None:
        try:
            if len(canonical_bytes(item)) > MAX_DURABLE_QUEUE_ENTRY_BYTES:
                raise BrokerError("QUEUE_FULL")
            retained = [
                entry
                for entry in work["queue"]
                if (
                    entry["state_key"] != supersede_state_key
                    or entry["in_flight"]
                    or entry["operation_id"] is not None
                )
            ]
            candidate = [*retained, item]
            reserved_completed = set(work["completed"])
            reserved_completed.update(
                entry["state_key"] for entry in candidate
            )
            reservation_bytes: dict[str, int] = {}
            for entry in candidate:
                reservation_bytes[entry["state_key"]] = max(
                    reservation_bytes.get(entry["state_key"], 0),
                    self._completed_reservation_bytes(entry),
                )
            if (
                len(candidate) > MAX_DURABLE_QUEUE_ENTRIES
                or len(canonical_bytes(candidate))
                > MAX_DURABLE_QUEUE_BYTES
                or len(reserved_completed) > MAX_COMPLETED_ENTRIES
                or len(canonical_bytes(work["completed"]))
                + sum(reservation_bytes.values()) > MAX_COMPLETED_BYTES
            ):
                raise BrokerError("QUEUE_FULL")
        except BrokerError:
            raise
        except (TypeError, ValueError):
            raise BrokerError("STATE_INVALID") from None
        work["queue"] = candidate

    def _enqueue_watermarked(self, method: str, claims: Mapping[str, Any], route: Mapping[str, Any],
                             sequence: int, action_id: str, action_digest: str,
        request: Mapping[str, Any]) -> dict[str, Any]:
        document_id = request["document_id"]
        state_key = self._checkpoint_state_key(
            method, claims, document_id,
            epoch=request["epoch"], checkpoint=request["checkpoint"],
        )
        series_key = self._checkpoint_series_key(
            method, claims, document_id
        )
        watermark = [request["epoch"], request["checkpoint"]]
        request_digest = hashlib.sha256(canonical_bytes(request)).hexdigest()
        with self._document_lock(claims, document_id):
            with self._lock:
                def enqueue(work):
                    session = work["sessions"].get(claims["session_id"])
                    receipt = (
                        session.get("watermark_receipts", {}).get(action_id)
                        if isinstance(session, Mapping)
                        else None
                    )
                    if receipt is not None:
                        if (
                            receipt["method"] != method
                            or receipt["state_key"] != state_key
                            or receipt["watermark"] != watermark
                            or receipt["request_digest"] != request_digest
                            or receipt["idempotency_key"] != action_digest
                        ):
                            raise BrokerError("DIGEST_DRIFT")
                        self._validate_committed_replay(
                            work, claims, action_id
                        )
                        existing_queue_id = next(
                            (
                                entry["queue_id"]
                                for entry in work["queue"]
                                if entry["idempotency_key"] == action_digest
                            ),
                            None,
                        )
                        return {
                            "disposition": "idempotent",
                            "payload": {
                                "watermark": receipt["reported_watermark"],
                                **(
                                    {"queue_id": existing_queue_id}
                                    if existing_queue_id is not None
                                    else {}
                                ),
                            },
                            "queue_id": existing_queue_id,
                        }
                    if method == "transcript_checkpoint":
                        series_prefix = f"{series_key}:"
                        records = [
                            entry for entry in work["queue"]
                            if entry.get("state_key", "").startswith(
                                series_prefix
                            )
                        ]
                        records.extend(
                            record
                            for completed_key, record
                            in work["completed"].items()
                            if completed_key.startswith(series_prefix)
                        )
                    else:
                        records = [
                            entry for entry in work["queue"]
                            if entry.get("state_key") == state_key
                        ]
                        completed = work["completed"].get(state_key)
                        if completed:
                            records.append(completed)
                    if records:
                        latest = max(records, key=lambda entry: tuple(entry["watermark"]))
                        if latest["watermark"] == watermark:
                            if (
                                latest["request_digest"] != request_digest
                                or latest["idempotency_key"] != action_digest
                            ):
                                raise BrokerError("DIGEST_DRIFT")
                            self._validate_committed_replay(
                                work, claims, action_id
                            )
                            existing_queue_id = latest.get("queue_id")
                            return {
                                "disposition": "idempotent",
                                "payload": {
                                    "watermark": watermark,
                                    **(
                                        {"queue_id": existing_queue_id}
                                        if existing_queue_id is not None
                                        else {}
                                    ),
                                },
                                "queue_id": existing_queue_id,
                            }
                        if tuple(watermark) < tuple(latest["watermark"]):
                            self._commit_action(
                                work, claims, method, sequence, action_id
                            )
                            self._store_watermark_receipt(
                                work, claims, action_id,
                                method=method, state_key=state_key,
                                watermark=watermark,
                                reported_watermark=latest["watermark"],
                                request_digest=request_digest,
                                action_digest=action_digest,
                            )
                            return {"disposition": "stale", "payload": {"watermark": latest["watermark"]}, "queue_id": None}
                    item = {
                        "queue_id": secrets.token_hex(16), "session_id": claims["session_id"],
                        "route": claims["route"], "method": method, "state_key": state_key,
                        "authorized_bank": deepcopy(claims["home_bank"]),
                        "policy_digest": claims["policy_digest"],
                        "artifact_digest": claims["artifact_digest"],
                        "watermark": watermark, "request_digest": request_digest,
                        "idempotency_key": action_digest,
                        "adapter_request": {
                            **request,
                            "session_id": claims["session_id"],
                            "idempotency_key": action_digest,
                        },
                        "attempts": 0, "last_error": None, "next_retry": None,
                        "in_flight": False, "operation_id": None,
                        "poll_attempts": 0,
                    }
                    self._append_queue_item(
                        work, item, supersede_state_key=state_key
                    )
                    self._commit_action(
                        work, claims, method, sequence, action_id
                    )
                    self._store_watermark_receipt(
                        work, claims, action_id,
                        method=method, state_key=state_key,
                        watermark=watermark,
                        reported_watermark=watermark,
                        request_digest=request_digest,
                        action_digest=action_digest,
                    )
                    return {"disposition": "queued", "payload": {"watermark": watermark, "queue_id": item["queue_id"]}, "queue_id": item["queue_id"]}
                result = self._transaction(enqueue, runtime=True)
                if result["queue_id"]:
                    self._submit_write(result["queue_id"], runtime=True)
        return self._response(action_id, action_digest, result["disposition"], result["payload"])

    def transcript_checkpoint(self, capability: str, *, sequence: int, action_id: str, request: Mapping[str, Any]) -> dict[str, Any]:
        value = self._validate_request("transcript_checkpoint", request)
        claims, route, action_digest = self._authorize(capability, "transcript_checkpoint", sequence, action_id, commit=False)
        return self._enqueue_watermarked("transcript_checkpoint", claims, route, sequence, action_id, action_digest, value)

    def retain_outcome(self, capability: str, *, sequence: int, action_id: str, request: Mapping[str, Any]) -> dict[str, Any]:
        value = self._validate_request("retain_outcome", request)
        claims, route, action_digest = self._authorize(capability, "retain_outcome", sequence, action_id, commit=False)
        return self._enqueue_watermarked("retain_outcome", claims, route, sequence, action_id, action_digest, value)

    def reflect(self, capability: str, *, sequence: int, action_id: str, request: Mapping[str, Any], timeout_seconds: float = 2) -> dict[str, Any]:
        return self._read_call(
            "reflect", None, capability, sequence, action_id, request,
            timeout_seconds, unavailable_code="REFLECT_UNAVAILABLE",
        )

    def _drain_item(self, queue_id: str) -> None:
        with self._lock:
            item = next((entry for entry in self._work["queue"] if entry["queue_id"] == queue_id), None)
            work_lock = self._work_lock(
                item["state_key"] if item else queue_id
            )
        if item is None:
            return
        if self._closed:
            return
        with work_lock:
            try:
                disposition, delay = self._dispatch_queued_item(queue_id)
            except BrokerError as error:
                if error.code == "BROKER_RETIRED":
                    return
                raise
            except OSError:
                # Publication failures leave either the durable queue item or
                # its completed record authoritative. Re-read the adopted
                # state before deciding whether this is a retry or success.
                with self._lock:
                    disposition, delay = (
                        self._recover_write_publication_error(queue_id)
                    )
                if disposition == "retired":
                    return
                if disposition == "complete":
                    self._release_write_dependents(queue_id)
                    return
                self._schedule_write_retry(queue_id, delay)
                return
        if disposition in {"missing", "complete"}:
            self._release_write_dependents(queue_id)
        elif disposition == "predecessor":
            if not isinstance(delay, str):
                raise BrokerError("STATE_INVALID")
            self._remember_write_predecessor(queue_id, delay)
            self._schedule_write_after_predecessor(queue_id)
        else:
            self._schedule_write_retry(queue_id, delay)

    def _recover_write_publication_error(
        self, queue_id: str
    ) -> tuple[str, float]:
        """Adopt durable state and persist bounded retry state after I/O failure."""
        generation_descriptor = self._generation_lease_descriptor()
        descriptor: int | None = None
        generation_locked = False
        state_locked = False
        try:
            fcntl.flock(generation_descriptor, fcntl.LOCK_SH)
            generation_locked = True
            descriptor = self._lease_descriptor()
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            state_locked = True
            current = self._read_work()
            self._adopt_work(current)
            if current.get("generation") != self._generation:
                return "retired", 0.0
            if not any(
                item["queue_id"] == queue_id for item in current["queue"]
            ):
                return "complete", 0.0
            value = deepcopy(current)
            delay = self._persist_dispatch_retry(value, queue_id)
            return "retry", delay
        finally:
            if descriptor is not None:
                if state_locked:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            if generation_locked:
                fcntl.flock(generation_descriptor, fcntl.LOCK_UN)
            os.close(generation_descriptor)

    def _schedule_write_retry(self, queue_id: str, delay: float) -> None:
        deadline = time.monotonic() + max(0.0, delay)
        with self._write_retry_condition:
            if self._shutdown_started or self._closed:
                return
            if queue_id in self._write_retry_deadlines:
                return
            if len(self._write_retry_deadlines) >= MAX_DURABLE_QUEUE_ENTRIES:
                raise BrokerError("STATE_INVALID")
            self._write_retry_sequence += 1
            self._write_retry_deadlines[queue_id] = deadline
            heapq.heappush(
                self._write_retry_heap,
                (deadline, self._write_retry_sequence, queue_id),
            )
            self._write_retry_condition.notify()

    def _write_retry_loop(self) -> None:
        while True:
            with self._write_retry_condition:
                while True:
                    if self._shutdown_started or self._closed:
                        return
                    while self._write_retry_heap:
                        deadline, _sequence, queue_id = self._write_retry_heap[0]
                        if self._write_retry_deadlines.get(queue_id) == deadline:
                            break
                        heapq.heappop(self._write_retry_heap)
                    if not self._write_retry_heap:
                        self._write_retry_condition.wait()
                        continue
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        self._write_retry_condition.wait(remaining)
                        continue
                    heapq.heappop(self._write_retry_heap)
                    del self._write_retry_deadlines[queue_id]
                    break
            try:
                self._submit_write(queue_id, runtime=True)
            except BrokerError as error:
                if error.code != "BROKER_CLOSED":
                    LOGGER.exception("write retry submission failed")

    def _remember_write_predecessor(
        self, queue_id: str, predecessor_id: str
    ) -> None:
        with self._write_retry_condition:
            previous = self._write_predecessors.get(queue_id)
            if previous == predecessor_id:
                return
            if previous is not None:
                dependents = self._write_dependents.get(previous)
                if dependents is not None:
                    dependents.discard(queue_id)
                    if not dependents:
                        del self._write_dependents[previous]
            if len(self._write_predecessors) >= MAX_DURABLE_QUEUE_ENTRIES:
                raise BrokerError("STATE_INVALID")
            self._write_predecessors[queue_id] = predecessor_id

    def _schedule_write_after_predecessor(self, queue_id: str) -> None:
        with self._write_retry_condition:
            predecessor_id = self._write_predecessors.get(queue_id)
            if predecessor_id is None:
                return
            predecessor_queued = any(
                item["queue_id"] == predecessor_id
                for item in self._work["queue"]
            )
            if predecessor_queued:
                dependents = self._write_dependents.setdefault(
                    predecessor_id, set()
                )
                if len(dependents) >= MAX_DURABLE_QUEUE_ENTRIES:
                    raise BrokerError("STATE_INVALID")
                dependents.add(queue_id)
                return
            del self._write_predecessors[queue_id]
        self._schedule_write_retry(queue_id, 0.0)

    def _release_write_dependents(self, predecessor_id: str) -> None:
        with self._write_retry_condition:
            dependents = tuple(
                self._write_dependents.pop(predecessor_id, ())
            )
            for queue_id in dependents:
                if self._write_predecessors.get(queue_id) == predecessor_id:
                    del self._write_predecessors[queue_id]
        for queue_id in dependents:
            self._schedule_write_retry(queue_id, 0.0)

    def _stop_write_retry_scheduler_locked(self) -> None:
        self._write_retry_heap.clear()
        self._write_retry_deadlines.clear()
        self._write_predecessors.clear()
        self._write_dependents.clear()
        self._write_retry_condition.notify_all()

    def _dispatch_queued_item(
        self, queue_id: str
    ) -> tuple[str, float | str]:
        """Fence generation ownership across one external adapter invocation."""
        generation_descriptor: int | None = (
            self._generation_lease_descriptor()
        )
        descriptor: int | None = None
        generation_locked = False
        state_locked = False
        try:
            fcntl.flock(generation_descriptor, fcntl.LOCK_SH)
            generation_locked = True
            descriptor = self._lease_descriptor()
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            state_locked = True
            current = self._read_work()
            if current.get("generation") != self._generation:
                raise BrokerError("BROKER_RETIRED")
            # Completion order is a rank, not a durable global sequence. Rebase
            # it before any external write so the reserved completion record is
            # always large enough for the final durable transition.
            self._normalize_completion_orders(current)
            item = next(
                (
                    entry for entry in current["queue"]
                    if entry["queue_id"] == queue_id
                ),
                None,
            )
            if item is None:
                return "missing", 0.0
            item_index = next(
                index for index, entry in enumerate(current["queue"])
                if entry["queue_id"] == queue_id
            )
            if any(
                entry["session_id"] == item["session_id"]
                for entry in current["queue"][:item_index]
            ):
                # Queue order is the durable dependency relation for ordinary
                # session work. Close drains this prior work and therefore
                # needs no synthesized queue item of its own.
                predecessor = next(
                    entry for entry in reversed(current["queue"][:item_index])
                    if entry["session_id"] == item["session_id"]
                )
                # Return the dependency while the durable lease is held, but
                # update the in-process scheduler only after releasing it.
                # Broker transactions acquire the scheduler lock before this
                # lease, so acquiring that lock here would invert the order.
                return "predecessor", predecessor["queue_id"]
            same_key = [
                entry for entry in current["queue"]
                if entry["state_key"] == item["state_key"]
            ]
            oldest = min(
                same_key,
                key=lambda entry: tuple(entry["watermark"]),
            )
            if oldest["queue_id"] != queue_id:
                return "predecessor", oldest["queue_id"]
            retry_at = item["next_retry"]
            now = self.clock()
            if retry_at is not None and retry_at > now:
                return "retry", min(30.0, retry_at - now)
            route = self.routes.get(item["route"])
            if route is None or not isinstance(route.get("adapter"), Adapter):
                value = deepcopy(current)
                delay = self._persist_dispatch_retry(value, queue_id)
                return "retry", delay
            route_bank = self._route_bank(route, "STATE_INVALID")
            if (
                item["policy_digest"] != self.policy_digest
                or item["artifact_digest"] != self.artifact_digest
                or dict(route_bank) != item["authorized_bank"]
            ):
                raise BrokerError("STATE_INVALID")
            adapter = route["adapter"]
            method = item["method"]
            if item["operation_id"] is None:
                dispatch_method = method
                adapter_request = deepcopy(item["adapter_request"])
            else:
                dispatch_method = "operation_status"
                adapter_request = {"operation_id": item["operation_id"]}
            value = deepcopy(current)
            reserved_item = next(
                entry for entry in value["queue"]
                if entry["queue_id"] == queue_id
            )
            reserved_item["in_flight"] = True
            self._persist_locked_work(value)
            item = reserved_item
            try:
                self._reserve_adapter_call(
                    queue_id,
                    dispatch_method,
                    getattr(adapter, dispatch_method),
                    adapter_request,
                )
            except Exception:
                delay = self._persist_dispatch_retry(
                    value, queue_id, in_flight=False
                )
                return "retry", delay
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            state_locked = False
            os.close(descriptor)
            descriptor = None
            operation_pending = False
            submitted_operation_id = None
            failed_dispatch = False
            operation_poll_failed = False
            try:
                adapter_result = self._invoke_adapter_bounded(
                    queue_id,
                    dispatch_method,
                    getattr(adapter, dispatch_method),
                    adapter_request,
                )
                if dispatch_method == "operation_status":
                    if (
                        not isinstance(adapter_result, Mapping)
                        or set(adapter_result) != {"status"}
                        or adapter_result["status"] not in {
                            "pending", "processing", "completed", "failed",
                            "cancelled", "not_found",
                        }
                    ):
                        raise AdapterContractError(
                            "runtime operation status response is invalid"
                        )
                    operation_pending = adapter_result["status"] in {
                        "pending", "processing",
                    }
                    failed_dispatch = adapter_result["status"] in {
                        "failed", "cancelled", "not_found",
                    }
                    adapter_result = None
                else:
                    synchronous_result = (
                        {"applied": True}
                        if method == "transcript_checkpoint"
                        else {"retained": True}
                    )
                    if (
                        isinstance(adapter_result, Mapping)
                        and set(adapter_result) == {"operation_id"}
                    ):
                        submitted_operation_id = adapter_result["operation_id"]
                    elif adapter_result != synchronous_result:
                        raise AdapterContractError(
                            "runtime retain response is invalid"
                        )
                    if (
                        submitted_operation_id is not None
                        and (
                            not isinstance(submitted_operation_id, str)
                            or IDENTIFIER.fullmatch(submitted_operation_id)
                            is None
                        )
                    ):
                        raise AdapterContractError(
                            "runtime retain response is invalid"
                        )
                    adapter_result = None
                    failed_dispatch = False
            except AdapterContractError as error:
                LOGGER.warning(
                    "runtime adapter contract violation during %s: %s",
                    dispatch_method, error,
                )
                adapter_result = None
                submitted_operation_id = None
                if dispatch_method == "operation_status":
                    operation_poll_failed = True
                else:
                    failed_dispatch = True
            except Exception:
                adapter_result = None
                submitted_operation_id = None
                if dispatch_method == "operation_status":
                    operation_poll_failed = True
                else:
                    failed_dispatch = True
            descriptor = self._lease_descriptor()
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            state_locked = True
            latest = self._read_work()
            if latest.get("generation") != self._generation:
                raise BrokerError("BROKER_RETIRED")
            current_item = next(
                (
                    entry for entry in latest["queue"]
                    if entry["queue_id"] == queue_id
                ),
                None,
            )
            if current_item is None:
                return "missing", 0.0
            if (
                current_item["method"] != method
                or current_item["adapter_request"] != item["adapter_request"]
                or current_item["idempotency_key"] != item["idempotency_key"]
                or current_item["operation_id"] != item["operation_id"]
            ):
                raise BrokerError("STATE_INVALID")
            value = deepcopy(latest)
            if operation_poll_failed:
                with self._adapter_calls_lock:
                    active = self._adapter_calls.get(queue_id)
                    active_future = active[2] if active is not None else None
                    call_in_flight = bool(
                        active_future is not None
                        and not active_future.done()
                    )
                delay = self._persist_dispatch_retry(
                    value, queue_id, in_flight=call_in_flight
                )
                if (
                    call_in_flight
                    and active_future is not None
                    and self._defer_generation_lease_release(
                        active_future, generation_descriptor
                    )
                ):
                    generation_locked = False
                    generation_descriptor = None
                return "retry", delay
            if submitted_operation_id is not None or operation_pending:
                pending_item = next(
                    entry for entry in value["queue"]
                    if entry["queue_id"] == queue_id
                )
                pending_item["operation_id"] = (
                    submitted_operation_id or item["operation_id"]
                )
                pending_item["in_flight"] = False
                pending_item["last_error"] = None
                poll_delay = min(
                    5.0,
                    0.25 * (
                        2 ** min(pending_item["poll_attempts"], 5)
                    ),
                )
                # This is a saturated backoff exponent, not a retry ceiling.
                # Only the server can declare an accepted durable operation
                # terminal; abandoning a pending ID could duplicate or lose a
                # write that completes after a broker restart.
                next_poll_attempt = min(
                    1_000_000, pending_item["poll_attempts"] + 1
                )
                if (
                    next_poll_attempt != pending_item["poll_attempts"]
                    and next_poll_attempt >= PENDING_OPERATION_WARNING_POLL
                    and next_poll_attempt % PENDING_OPERATION_WARNING_POLL == 0
                ):
                    LOGGER.warning(
                        "runtime operation remains pending after %d polls "
                        "(operation_id=%s queue_id=%s)",
                        next_poll_attempt, pending_item["operation_id"],
                        queue_id,
                    )
                pending_item["poll_attempts"] = next_poll_attempt
                pending_item["next_retry"] = self.clock() + poll_delay
                self._persist_locked_work(value)
                return "retry", poll_delay
            if failed_dispatch:
                failed_item = next(
                    entry for entry in value["queue"]
                    if entry["queue_id"] == queue_id
                )
                failed_item["operation_id"] = None
                failed_item["poll_attempts"] = 0
                with self._adapter_calls_lock:
                    active = self._adapter_calls.get(queue_id)
                    active_future = active[2] if active is not None else None
                    call_in_flight = bool(
                        active_future is not None
                        and not active_future.done()
                    )
                delay = self._persist_dispatch_retry(
                    value, queue_id, in_flight=call_in_flight
                )
                if (
                    call_in_flight
                    and active_future is not None
                    and self._defer_generation_lease_release(
                        active_future, generation_descriptor
                    )
                ):
                    generation_locked = False
                    generation_descriptor = None
                return "retry", delay
            completed = next(
                entry for entry in value["queue"]
                if entry["queue_id"] == queue_id
            )
            value["queue"] = [
                entry for entry in value["queue"]
                if entry["queue_id"] != queue_id
            ]
            value["completed"][completed["state_key"]] = {
                "watermark": completed["watermark"],
                "request_digest": completed["request_digest"],
                "idempotency_key": completed["idempotency_key"],
                "adapter_result": adapter_result,
                "session_id": completed["session_id"],
                "method": completed["method"],
                "operation_id": completed["operation_id"],
                "completion_order": 1 + max(
                    (
                        record["completion_order"]
                        for record in value["completed"].values()
                    ),
                    default=0,
                ),
            }
            value["expirations"]["completed"][completed["state_key"]] = (
                self.clock() + COMPLETED_RETENTION_SECONDS
            )
            self._persist_locked_work(value)
            return "complete", 0.0
        finally:
            if descriptor is not None:
                if state_locked:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            if generation_locked and generation_descriptor is not None:
                with self._adapter_calls_lock:
                    active = self._adapter_calls.get(queue_id)
                    active_future = active[2] if active is not None else None
                if (
                    active_future is not None
                    and not active_future.done()
                    and self._defer_generation_lease_release(
                        active_future, generation_descriptor
                    )
                ):
                    generation_locked = False
                    generation_descriptor = None
            if generation_locked and generation_descriptor is not None:
                fcntl.flock(generation_descriptor, fcntl.LOCK_UN)
            if generation_descriptor is not None:
                os.close(generation_descriptor)

    def _defer_generation_lease_release(
        self, future: Future[Any], descriptor: int
    ) -> bool:
        """Transfer one shared generation lease to an active adapter call."""
        with self._adapter_calls_lock:
            if getattr(future, "_hindsight_generation_lease_held", False):
                return False
            future._hindsight_generation_lease_held = True

        release_lock = threading.Lock()
        released = False

        def release(*, expired: bool) -> None:
            nonlocal released
            with release_lock:
                if released:
                    return
                released = True
                if expired:
                    future._hindsight_generation_lease_expired = True
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

        timer = threading.Timer(
            self._adapter_generation_lease_timeout_seconds,
            lambda: release(expired=True),
        )
        timer.daemon = True
        timer.start()

        def completed(_future: Future[Any]) -> None:
            timer.cancel()
            release(expired=False)

        future.add_done_callback(completed)
        return True

    def _persist_dispatch_retry(
        self,
        work: dict[str, Any],
        queue_id: str,
        *,
        in_flight: bool = False,
    ) -> float:
        failed = next(
            entry for entry in work["queue"]
            if entry["queue_id"] == queue_id
        )
        failed["attempts"] += 1
        failed["last_error"] = "ADAPTER_UNAVAILABLE"
        failed["in_flight"] = in_flight
        base_delay = min(
            30.0,
            0.25 * (2 ** min(failed["attempts"] - 1, 7)),
        )
        jitter = 0.75 + (secrets.randbelow(501) / 1000)
        delay = min(30.0, base_delay * jitter)
        failed["next_retry"] = self.clock() + delay
        self._persist_locked_work(work)
        return delay

    def session_status(self, capability: str, *, sequence: int, action_id: str, timeout_seconds: float = 2) -> dict[str, Any]:
        timeout = self._timeout(timeout_seconds)
        with self._lock:
            claims, route, action_digest = self._authorize(
                capability, "session_status", sequence, action_id
            )
            self._ensure_runtime_open()
            session_queue = [
                entry for entry in self._work["queue"]
                if entry["session_id"] == claims["session_id"]
            ]
            queued = len(session_queue)
            failures = [
                {
                    "attempts": entry["attempts"],
                    "last_error": entry["last_error"],
                    "next_retry": entry["next_retry"],
                }
                for entry in session_queue if entry["last_error"]
            ]
            pending_writes = [{
                "queue_id": entry["queue_id"],
                "method": entry["method"],
                "watermark": deepcopy(entry["watermark"]),
                "operation_id": entry["operation_id"],
                "status": (
                    "pending" if entry["operation_id"] is not None
                    else "retrying" if entry["last_error"] is not None
                    else "queued"
                ),
                "poll_attempts": entry["poll_attempts"],
            } for entry in session_queue]
            completed_writes = [
                {
                    "method": record["method"],
                    "watermark": deepcopy(record["watermark"]),
                    "operation_id": record["operation_id"],
                    "status": "completed",
                }
                for _state_key, record in sorted(
                    self._work["completed"].items(),
                    key=lambda item: (
                        item[1]["completion_order"], item[0]
                    ),
                )
                if record["session_id"] == claims["session_id"]
                and record["method"] in {
                    "transcript_checkpoint", "retain_outcome",
                }
            ]
            writes = {
                "pending": pending_writes[-SESSION_STATUS_WRITE_LIMIT:],
                "completed": completed_writes[-SESSION_STATUS_WRITE_LIMIT:],
                "omitted": {
                    "pending": max(
                        0, len(pending_writes) - SESSION_STATUS_WRITE_LIMIT
                    ),
                    "completed": max(
                        0, len(completed_writes) - SESSION_STATUS_WRITE_LIMIT
                    ),
                },
            }
        try:
            adapter_status = self._invoke_read_adapter_bounded(
                route["adapter"].session_status,
                {"session_id": claims["session_id"]},
                timeout,
            )
            adapter_status = self._adapter_payload("session_status", adapter_status)
            return self._response(action_id, action_digest, "active", {
                "queued": queued, "failures": failures,
                "writes": writes, "adapter": adapter_status,
            })
        except Exception:
            return self._response(
                action_id, action_digest, "unavailable",
                {"queued": queued, "failures": failures, "writes": writes},
                {"code": "MEMORY_UNAVAILABLE", "visible": True},
            )

    def session_close(self, capability: str, *, sequence: int, action_id: str, timeout_seconds: float = 2) -> dict[str, Any]:
        timeout = self._timeout(timeout_seconds)
        claims, _, action_digest = self._authorize(capability, "session_close", sequence, action_id, commit=False)
        ledger_record = self._session_close_ledger_record(claims, action_id)
        with self._lock:
            def close(work):
                existing_state = work["sessions"].get(
                    claims["session_id"]
                )
                if (
                    isinstance(existing_state, dict)
                    and existing_state.get("closed") is True
                    and action_id
                    in existing_state.get("action_ids", [])
                    and existing_state.get("sequence") == sequence
                ):
                    return [
                        queued["queue_id"]
                        for queued in work["queue"]
                        if queued["session_id"] == claims["session_id"]
                    ]
                if work["ledger_outbox"] and self.ledger_path is None:
                    raise BrokerError("LEDGER_UNAVAILABLE")
                self._commit_action(
                    work, claims, "session_close", sequence, action_id
                )
                state = work["sessions"][claims["session_id"]]
                state["closed"] = True
                if state["revocation_digest"] not in work["revoked_nonces"]:
                    work["revoked_nonces"].append(state["revocation_digest"])
                    work["expirations"]["revoked_nonces"][
                        state["revocation_digest"]
                    ] = work["expirations"]["sessions"][claims["session_id"]]
                if ledger_record is not None:
                    work["ledger_outbox"][action_digest] = {
                        "action_digest": action_digest,
                        "record": ledger_record,
                    }
                    if not self._durable_aggregates_within_limits(work):
                        raise BrokerError("QUEUE_FULL")
                if not self._durable_aggregates_within_limits(work):
                    raise BrokerError("STATE_FULL")
                return [
                    queued["queue_id"]
                    for queued in work["queue"]
                    if queued["session_id"] == claims["session_id"]
                ]
            close_queue_ids = self._transaction(
                close,
                runtime=True,
                allow_ledger_unavailable=True,
            )
            for close_queue_id in close_queue_ids:
                self._submit_write(close_queue_id, runtime=True)
        pending = self._wait_for_session_barrier(
            claims["session_id"], timeout
        )
        ledger_written = self._flush_ledger_outbox(action_digest)
        return self._close_response(
            action_id, action_digest, claims["session_id"], ledger_written,
            pending=pending,
        )

    def _wait_for_session_barrier(
        self, session_id: str, timeout: float
    ) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                pending = sum(
                    entry["session_id"] == session_id
                    for entry in self._work["queue"]
                )
            if not pending:
                break
            time.sleep(min(0.005, max(0, deadline - time.monotonic())))
        with self._lock:
            return sum(
                entry["session_id"] == session_id
                for entry in self._work["queue"]
            )

    def _session_close_ledger_record(
        self, claims: Mapping[str, Any], action_id: str
    ) -> dict[str, Any] | None:
        if self.ledger_path is None:
            return None
        bank = self._route_bank(
            self.routes.get(claims["route"]), "STATE_INVALID"
        )
        record = {
            "schema_version": 1, "action_id": action_id, "correlation_id": claims["session_id"],
            "source_bank": bank, "target_bank": bank, "policy_digest": self.policy_digest,
            "artifact_digest": self.artifact_digest, "decision": "apply", "reason_code": "SESSION_CLOSED",
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "reversible_record_id": None,
        }
        try:
            validate_record(record)
        except LedgerError:
            raise BrokerError("STATE_INVALID") from None
        return record

    def _flush_ledger_outbox(self, outbox_id: str | None = None) -> bool:
        with self._lock:
            identifiers = (
                [outbox_id]
                if outbox_id is not None
                else list(self._work["ledger_outbox"])
            )
            pending = any(
                identifier in self._work["ledger_outbox"]
                for identifier in identifiers
            )
        if self.ledger_path is None:
            return not pending
        complete = True
        for identifier in identifiers:
            with self._lock:
                entry = deepcopy(self._work["ledger_outbox"].get(identifier))
            if entry is None:
                continue
            try:
                append_record_once(self.ledger_path, entry["record"])
            except Exception:
                complete = False
                continue
            with self._lock:
                def acknowledge(work):
                    if work["ledger_outbox"].get(identifier) == entry:
                        del work["ledger_outbox"][identifier]
                self._transaction(acknowledge)
        return complete

    def _close_response(
        self, action_id: str, action_digest: str, session_id: str,
        ledger_written: bool, *, pending: int | None = None,
    ) -> dict[str, Any]:
        if pending is None:
            with self._lock:
                pending = sum(
                    entry["session_id"] == session_id
                    for entry in self._work["queue"]
                )
        payload = {
            "undrained": pending,
            "write_drain": "drained" if pending == 0 else "queued",
        }
        if not ledger_written:
            payload["write_drain"] = "ledger-pending"
            return self._response(
                action_id, action_digest, "unavailable", payload,
                {"code": "LEDGER_UNAVAILABLE", "visible": True},
            )
        return self._response(action_id, action_digest, "closed", payload)
