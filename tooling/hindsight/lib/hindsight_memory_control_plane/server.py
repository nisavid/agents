"""Bounded newline-delimited JSON-RPC over a private Unix socket."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import hmac
import os
from pathlib import Path
import socket
import stat
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
import errno
from typing import Any, Callable, Mapping

from .broker import Broker, BrokerError
from .canonical import StrictJsonError, strict_json_loads
from .file_evidence import open_trusted_parent


MAX_REQUEST_BYTES = 128 * 1024
MAX_RESPONSE_BYTES = 128 * 1024
RPC_METHODS = {
    "session_mint": "session_mint",
    "session_exchange": "session_exchange",
    "session_close": "session_close",
    "recall": "recall",
    "mental_model_fetch": "mental_model_fetch",
    "transcript_checkpoint": "transcript_checkpoint",
    "retain_outcome": "retain_outcome",
    "reflect": "reflect",
    "session_status": "session_status",
}
SOCKET_LIFECYCLE_LOCK = threading.RLock()


@contextmanager
def _socket_advisory_lock(path: Path, parent_descriptor: int):
    """Serialize path lifecycle decisions across competing processes."""

    lock_name = (
        ".hindsight-socket-lifecycle-"
        + hashlib.sha256(path.name.encode("utf-8")).hexdigest()
        + ".lock"
    )
    descriptor = os.open(
        lock_name,
        os.O_RDWR
        | os.O_CREAT
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
        dir_fd=parent_descriptor,
    )
    locked = False
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise OSError("socket lifecycle lock is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        if not _socket_parent_matches(path, parent_descriptor):
            raise OSError("socket parent identity changed")
        yield
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _open_socket_parent(path: Path, *, create_missing: bool) -> int:
    descriptor = open_trusted_parent(
        path.parent,
        unavailable_message="trusted socket parent access is unavailable",
        not_directory_message="socket parent must be a directory",
        owner_message="socket parent ownership is unsafe",
        writable_message="socket parent permissions are unsafe",
        create_missing=create_missing,
    )
    metadata = os.fstat(descriptor)
    if (
        metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        os.close(descriptor)
        raise OSError("socket parent must be private to the current user")
    return descriptor


def _socket_parent_matches(path: Path, descriptor: int) -> bool:
    try:
        pathname = os.stat(path.parent)
        opened = os.fstat(descriptor)
    except OSError:
        return False
    return (pathname.st_dev, pathname.st_ino) == (opened.st_dev, opened.st_ino)


class UnixJsonRpcServer:
    def __init__(
        self, path: str | Path, broker: Broker, *,
        max_request_bytes: int = MAX_REQUEST_BYTES,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        max_connections: int = 16,
        connection_timeout_seconds: float = 2.0,
        close_timeout_seconds: float = 1.0,
        shutdown_callback: Callable[[], None] | None = None,
        shutdown_capability: str | None = None,
    ) -> None:
        if (
            type(max_request_bytes) is not int
            or not 1 <= max_request_bytes <= 1024 * 1024
        ):
            raise ValueError(
                "max_request_bytes must be an integer from 1 to 1048576"
            )
        if type(max_connections) is not int or max_connections < 1 or max_connections > 256:
            raise ValueError("max_connections must be an integer from 1 to 256")
        if (
            type(connection_timeout_seconds) not in (int, float)
            or not 0 < connection_timeout_seconds <= 30
        ):
            raise ValueError("connection_timeout_seconds must be greater than 0 and at most 30")
        if (
            type(close_timeout_seconds) not in (int, float)
            or not 0 <= close_timeout_seconds <= 30
        ):
            raise ValueError("close_timeout_seconds must be at least 0 and at most 30")
        self.path = Path(path)
        self.broker = broker
        self.max_request_bytes = max_request_bytes
        if type(max_response_bytes) is not int or not 1024 <= max_response_bytes <= 1024 * 1024:
            raise ValueError("max_response_bytes must be an integer from 1024 to 1048576")
        self.max_response_bytes = max_response_bytes
        self.max_connections = max_connections
        self.connection_timeout_seconds = float(connection_timeout_seconds)
        self.close_timeout_seconds = float(close_timeout_seconds)
        self.shutdown_callback = shutdown_callback
        if shutdown_callback is not None and (
            not isinstance(shutdown_capability, str)
            or not 32 <= len(shutdown_capability.encode("utf-8")) <= 4096
            or any(character in shutdown_capability for character in "\r\n\0")
        ):
            raise ValueError("shutdown capability is invalid")
        if shutdown_callback is None and shutdown_capability is not None:
            raise ValueError("shutdown capability requires a callback")
        self._shutdown_capability = shutdown_capability
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._closing = threading.Event()
        self._bound_identity: tuple[int, int] | None = None
        self._connection_slots = threading.BoundedSemaphore(max_connections)
        self._executor: ThreadPoolExecutor | None = None
        self._connection_futures: set[Future[Any]] = set()
        self._connection_futures_lock = threading.RLock()

    def start(self) -> None:
        with SOCKET_LIFECYCLE_LOCK:
            with self._connection_futures_lock:
                has_active_connections = bool(self._connection_futures)
            if (
                self._socket is not None
                or self._thread is not None
                or self._executor is not None
                or self._bound_identity is not None
                or has_active_connections
            ):
                raise RuntimeError("Unix JSON-RPC server is already started")
            self._closing.clear()
            connection_slots = threading.BoundedSemaphore(
                self.max_connections
            )
            self._connection_slots = connection_slots
            parent_descriptor = _open_socket_parent(
                self.path, create_missing=True
            )
            listener: socket.socket | None = None
            executor: ThreadPoolExecutor | None = None
            bound_identity: tuple[int, int] | None = None
            try:
                listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                executor = ThreadPoolExecutor(
                    max_workers=self.max_connections,
                    thread_name_prefix="hindsight-json-rpc-connection",
                )
                bound_identity = self._bind_listener(
                    listener, parent_descriptor
                )
                # Closing the descriptor does not reliably unblock a Unix
                # accept in another thread on every supported platform. Keep
                # the poll bounded by the close budget without busy-polling.
                listener.settimeout(
                    min(0.1, max(0.01, self.close_timeout_seconds / 2))
                )
                thread = threading.Thread(
                    target=self._serve,
                    args=(listener, executor, connection_slots),
                    name="hindsight-json-rpc",
                    daemon=True,
                )
                self._executor = executor
                thread.start()
            except Exception:
                if listener is not None:
                    listener.close()
                if executor is not None:
                    executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None
                self._unlink_bound_path_at(
                    parent_descriptor, self.path.name, bound_identity
                )
                self._bound_identity = None
                raise
            finally:
                os.close(parent_descriptor)
            assert listener is not None and executor is not None
            self._socket = listener
            self._thread = thread
            self._bound_identity = bound_identity

    def _bind_listener(
        self, listener: socket.socket, parent_descriptor: int
    ) -> tuple[int, int]:
        bound_identity: tuple[int, int] | None = None
        with _socket_advisory_lock(self.path, parent_descriptor):
            try:
                previous_umask = os.umask(0o177)
                try:
                    try:
                        listener.bind(str(self.path))
                    except OSError as error:
                        if error.errno != errno.EADDRINUSE:
                            raise
                        stale = os.stat(
                            self.path.name,
                            dir_fd=parent_descriptor,
                            follow_symlinks=False,
                        )
                        stale_identity = (stale.st_dev, stale.st_ino)
                        if (
                            not stat.S_ISSOCK(stale.st_mode)
                            or stale.st_uid != os.geteuid()
                            or stale.st_nlink != 1
                            or stat.S_IMODE(stale.st_mode) != 0o600
                        ):
                            raise
                        probe = socket.socket(
                            socket.AF_UNIX, socket.SOCK_STREAM
                        )
                        try:
                            probe.settimeout(0.1)
                            probe.connect(str(self.path))
                        except OSError as probe_error:
                            if probe_error.errno not in {
                                errno.ECONNREFUSED, errno.ENOENT
                            }:
                                raise error
                        else:
                            raise error
                        finally:
                            probe.close()
                        current = os.stat(
                            self.path.name,
                            dir_fd=parent_descriptor,
                            follow_symlinks=False,
                        )
                        if stale_identity != (current.st_dev, current.st_ino):
                            raise OSError("socket path identity changed")
                        os.unlink(
                            self.path.name, dir_fd=parent_descriptor
                        )
                        os.fsync(parent_descriptor)
                        listener.bind(str(self.path))
                finally:
                    os.umask(previous_umask)
                metadata = os.stat(
                    self.path.name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if not stat.S_ISSOCK(metadata.st_mode):
                    raise OSError("socket parent identity changed")
                bound_identity = (metadata.st_dev, metadata.st_ino)
                if not _socket_parent_matches(
                    self.path, parent_descriptor
                ):
                    raise OSError("socket parent identity changed")
                os.chmod(
                    self.path.name, 0o600, dir_fd=parent_descriptor
                )
                self._listen(listener)
                return bound_identity
            except Exception:
                self._unlink_bound_path_at(
                    parent_descriptor, self.path.name, bound_identity
                )
                raise

    def _listen(self, listener: socket.socket) -> None:
        listener.listen(self.max_connections)

    def _serve(
        self,
        listener: socket.socket,
        executor: ThreadPoolExecutor,
        connection_slots: threading.BoundedSemaphore,
    ) -> None:
        while not self._closing.is_set():
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            if not connection_slots.acquire(blocking=False):
                connection.close()
                continue
            try:
                with self._connection_futures_lock:
                    future = executor.submit(
                        self._connection, connection, connection_slots
                    )
                    self._connection_futures.add(future)
                future.add_done_callback(
                    lambda completed, admitted=connection: self._complete_connection(
                        completed, admitted, connection_slots
                    )
                )
            except RuntimeError:
                connection.close()
                connection_slots.release()
                break

    def _cancel_admission(
        self,
        future,
        connection: socket.socket,
        connection_slots: threading.BoundedSemaphore | None = None,
    ) -> None:
        """Release resources for work cancelled before a worker can own it."""
        if not future.cancelled():
            return
        connection.close()
        (connection_slots or self._connection_slots).release()

    def _complete_connection(
        self,
        future: Future[Any],
        connection: socket.socket,
        connection_slots: threading.BoundedSemaphore,
    ) -> None:
        self._cancel_admission(future, connection, connection_slots)
        with self._connection_futures_lock:
            self._connection_futures.discard(future)
        self._release_drained_executor()
        if future.cancelled():
            return
        try:
            callbacks = future.result()
        except BaseException:
            return
        for callback in callbacks or ():
            callback()

    def _release_drained_executor(self) -> None:
        """Release shutdown executor state only after every callback drains."""

        if not self._closing.is_set():
            return
        with SOCKET_LIFECYCLE_LOCK:
            with self._connection_futures_lock:
                drained = not self._connection_futures
            if (
                drained
                and self._socket is None
                and self._thread is None
                and self._bound_identity is None
            ):
                self._executor = None

    def _connection(
        self,
        connection: socket.socket,
        connection_slots: threading.BoundedSemaphore,
    ) -> tuple[Callable[[], None], ...]:
        deferred_callbacks: list[Callable[[], None]] = []
        try:
            connection.settimeout(self.connection_timeout_seconds)
            with connection:
                with connection.makefile("rwb") as stream:
                    try:
                        line = stream.readline(self.max_request_bytes + 1)
                        response = self._error(None, -32600, "REQUEST_INVALID")
                        if len(line) <= self.max_request_bytes and line.endswith(b"\n"):
                            response = self.dispatch(
                                line,
                                deferred_callbacks=deferred_callbacks,
                            )
                        stream.write(self._serialize_response(response))
                        stream.flush()
                    except (TimeoutError, OSError):
                        pass
        finally:
            connection_slots.release()
        return tuple(deferred_callbacks)

    def dispatch(
        self,
        line: bytes,
        *,
        deferred_callbacks: list[Callable[[], None]] | None = None,
    ) -> dict[str, Any]:
        request: Any = None
        try:
            request = strict_json_loads(line)
            if not isinstance(request, dict) or set(request) != {"jsonrpc", "id", "method", "params"}:
                raise BrokerError("SCHEMA_INVALID")
            if request["jsonrpc"] != "2.0" or not isinstance(request["method"], str) or not isinstance(request["params"], dict):
                raise BrokerError("SCHEMA_INVALID")
            if request["id"] is not None and type(request["id"]) not in {str, int}:
                raise BrokerError("SCHEMA_INVALID")
            target = RPC_METHODS.get(request["method"])
            if request["method"] == "broker_shutdown":
                supplied = request["params"].get("shutdown_capability")
                if (
                    set(request["params"]) != {"shutdown_capability"}
                    or not isinstance(supplied, str)
                    or self.shutdown_callback is None
                    or self._shutdown_capability is None
                    or not hmac.compare_digest(
                        supplied.encode("utf-8"),
                        self._shutdown_capability.encode("utf-8"),
                    )
                ):
                    raise BrokerError("METHOD_DENIED")
                if deferred_callbacks is None:
                    self.shutdown_callback()
                else:
                    deferred_callbacks.append(self.shutdown_callback)
                return {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "result": {"stopping": True},
                }
            if target is None:
                raise BrokerError("METHOD_DENIED")
            result = getattr(self.broker, target)(**request["params"])
            return {"jsonrpc": "2.0", "id": request["id"], "result": result}
        except (json.JSONDecodeError, UnicodeDecodeError, StrictJsonError):
            return self._error(None, -32700, "PARSE_ERROR")
        except BrokerError as error:
            return self._error(request.get("id") if isinstance(request, dict) else None, -32000, error.code)
        except (TypeError, ValueError):
            return self._error(request.get("id") if isinstance(request, dict) else None, -32602, "SCHEMA_INVALID")
        except Exception:
            return self._error(request.get("id") if isinstance(request, dict) else None, -32603, "INTERNAL_ERROR")

    @staticmethod
    def _error(identifier: Any, number: int, code: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": identifier, "error": {"code": number, "message": code}}

    def _serialize_response(self, response: Mapping[str, Any]) -> bytes:
        """Serialize incrementally so an adapter cannot force an unbounded copy."""
        encoder = json.JSONEncoder(
            sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        body = bytearray()
        error_code: str | None = None
        try:
            for fragment in encoder.iterencode(response):
                encoded = fragment.encode("utf-8")
                if len(body) + len(encoded) + 1 > self.max_response_bytes:
                    raise OverflowError
                body.extend(encoded)
        except OverflowError:
            error_code = "RESPONSE_TOO_LARGE"
        except (TypeError, ValueError):
            error_code = "RESPONSE_INVALID"
        if error_code is not None:
            identifier = (
                response.get("id")
                if isinstance(response, Mapping)
                and type(response.get("id")) in {str, int, type(None)}
                else None
            )
            fallback = json.dumps(
                self._error(identifier, -32603, error_code),
                sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
            if len(fallback) + 1 > self.max_response_bytes:
                fallback = json.dumps(
                    self._error(None, -32603, error_code),
                    sort_keys=True, separators=(",", ":"), allow_nan=False,
                ).encode("utf-8")
            body = bytearray(fallback)
        body.extend(b"\n")
        return bytes(body)

    def close(self) -> None:
        try:
            self._close()
        except BrokerError:
            raise
        except Exception as error:
            raise BrokerError("SERVER_CLOSE_FAILED") from error

    def _close(self) -> None:
        with SOCKET_LIFECYCLE_LOCK:
            deadline = time.monotonic() + self.close_timeout_seconds
            self._closing.set()
            executor = self._executor
            if self._socket is not None:
                try:
                    self._socket.shutdown(socket.SHUT_RDWR)
                except (AttributeError, OSError):
                    pass
                self._socket.close()
            listener_thread = self._thread
            if listener_thread is not None:
                listener_thread.join(
                    timeout=max(0.0, deadline - time.monotonic())
                )
            listener_active = bool(
                listener_thread is not None and listener_thread.is_alive()
            )
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
            pending: set[Future[Any]] = set()
            while True:
                with self._connection_futures_lock:
                    active = set(self._connection_futures)
                if not active:
                    pending = set()
                    break
                _done, pending = wait(
                    active, timeout=max(0.0, deadline - time.monotonic())
                )
                with self._connection_futures_lock:
                    current = set(self._connection_futures)
                if not current:
                    pending = set()
                    break
                if time.monotonic() >= deadline:
                    pending = current
                    break
            self._unlink_bound_path(self._bound_identity)
            self._socket = None
            self._thread = None
            self._bound_identity = None
            with self._connection_futures_lock:
                callbacks_drained = not self._connection_futures
            if not pending or callbacks_drained:
                self._executor = None
            if pending or listener_active:
                raise BrokerError("SERVER_CLOSE_INCOMPLETE")

    def _unlink_bound_path(self, identity: tuple[int, int] | None) -> None:
        with SOCKET_LIFECYCLE_LOCK:
            if identity is None:
                return
            parent_descriptor: int | None = None
            try:
                parent_descriptor = _open_socket_parent(
                    self.path, create_missing=False
                )
                with _socket_advisory_lock(
                    self.path, parent_descriptor
                ):
                    self._unlink_bound_path_at(
                        parent_descriptor, self.path.name, identity
                    )
            except OSError:
                return
            finally:
                if parent_descriptor is not None:
                    os.close(parent_descriptor)

    @staticmethod
    def _unlink_bound_path_at(
        parent_descriptor: int,
        name: str,
        identity: tuple[int, int] | None,
    ) -> None:
        if identity is None:
            return
        try:
            metadata = os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if (
                stat.S_ISSOCK(metadata.st_mode)
                and identity == (metadata.st_dev, metadata.st_ino)
            ):
                os.unlink(name, dir_fd=parent_descriptor)
        except OSError:
            return


class JsonRpcClient:
    def __init__(
        self,
        path: str | Path,
        *,
        timeout_seconds: float = 2,
        max_request_bytes: int = MAX_REQUEST_BYTES,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        if (
            type(timeout_seconds) not in (int, float)
            or not 0 < timeout_seconds <= 30
        ):
            raise ValueError(
                "timeout_seconds must be greater than 0 and at most 30"
            )
        if (
            type(max_request_bytes) is not int
            or not 1 <= max_request_bytes <= 1024 * 1024
        ):
            raise ValueError(
                "max_request_bytes must be an integer from 1 to 1048576"
            )
        if (
            type(max_response_bytes) is not int
            or not 1024 <= max_response_bytes <= 1024 * 1024
        ):
            raise ValueError(
                "max_response_bytes must be an integer from 1024 to 1048576"
            )
        self.path = Path(path)
        self.timeout_seconds = float(timeout_seconds)
        self.max_request_bytes = max_request_bytes
        self.max_response_bytes = max_response_bytes
        self._next_id = 0
        self._id_lock = threading.Lock()

    def _call(self, method: str, params: Mapping[str, Any]) -> Any:
        with self._id_lock:
            self._next_id += 1
            request_id = self._next_id
        request = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params)}
        body = json.dumps(
            request,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode() + b"\n"
        if len(body) > self.max_request_bytes:
            raise BrokerError("REQUEST_TOO_LARGE")
        parent_descriptor = _open_socket_parent(
            self.path, create_missing=False
        )
        try:
            metadata = os.stat(
                self.path.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            identity = (metadata.st_dev, metadata.st_ino)
            if (
                not stat.S_ISSOCK(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
                or not _socket_parent_matches(self.path, parent_descriptor)
            ):
                raise OSError("socket path is unsafe")
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(self.timeout_seconds)
            with connection:
                connection.connect(str(self.path))
                current = os.stat(
                    self.path.name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if (
                    identity != (current.st_dev, current.st_ino)
                    or not _socket_parent_matches(
                        self.path, parent_descriptor
                    )
                ):
                    raise OSError("socket path identity changed")
                connection.sendall(body)
                with connection.makefile("rb") as stream:
                    response = stream.readline(self.max_response_bytes + 1)
        finally:
            os.close(parent_descriptor)
        if (
            len(response) > self.max_response_bytes
            or not response.endswith(b"\n")
        ):
            raise BrokerError("RESPONSE_INVALID")
        try:
            decoded = strict_json_loads(response)
        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            StrictJsonError,
        ) as error:
            raise BrokerError("RESPONSE_INVALID") from error
        if not isinstance(decoded, dict) or decoded.get("jsonrpc") != "2.0" or decoded.get("id") != request_id:
            raise BrokerError("RESPONSE_INVALID")
        if set(decoded) == {"jsonrpc", "id", "error"}:
            error = decoded["error"]
            if not isinstance(error, dict) or set(error) != {"code", "message"} or type(error["code"]) is not int or not isinstance(error["message"], str):
                raise BrokerError("RESPONSE_INVALID")
            raise BrokerError(error["message"])
        if set(decoded) != {"jsonrpc", "id", "result"} or not isinstance(decoded["result"], dict):
            raise BrokerError("RESPONSE_INVALID")
        return decoded["result"]

    def session_mint(self, control_capability: str, claims: Mapping[str, Any], *, ttl_seconds: float = 60):
        return self._call("session_mint", {"control_capability": control_capability, "claims": dict(claims), "ttl_seconds": ttl_seconds})

    def session_exchange(self, handle: str):
        return self._call("session_exchange", {"handle": handle})

    def session_close(self, capability: str, *, sequence: int, action_id: str, timeout_seconds: float = 2):
        return self._call("session_close", {"capability": capability, "sequence": sequence, "action_id": action_id, "timeout_seconds": timeout_seconds})

    def recall(self, capability: str, *, sequence: int, action_id: str, request: Mapping[str, Any], timeout_seconds: float = 2):
        return self._call("recall", {"capability": capability, "sequence": sequence, "action_id": action_id, "request": dict(request), "timeout_seconds": timeout_seconds})

    def mental_model_fetch(self, capability: str, *, sequence: int, action_id: str, request: Mapping[str, Any], timeout_seconds: float = 2):
        return self._call("mental_model_fetch", {"capability": capability, "sequence": sequence, "action_id": action_id, "request": dict(request), "timeout_seconds": timeout_seconds})

    def transcript_checkpoint(self, capability: str, *, sequence: int, action_id: str, request: Mapping[str, Any]):
        return self._call("transcript_checkpoint", {"capability": capability, "sequence": sequence, "action_id": action_id, "request": dict(request)})

    def retain_outcome(self, capability: str, *, sequence: int, action_id: str, request: Mapping[str, Any]):
        return self._call("retain_outcome", {"capability": capability, "sequence": sequence, "action_id": action_id, "request": dict(request)})

    def reflect(self, capability: str, *, sequence: int, action_id: str, request: Mapping[str, Any], timeout_seconds: float = 2):
        return self._call("reflect", {"capability": capability, "sequence": sequence, "action_id": action_id, "request": dict(request), "timeout_seconds": timeout_seconds})

    def session_status(self, capability: str, *, sequence: int, action_id: str, timeout_seconds: float = 2):
        return self._call("session_status", {"capability": capability, "sequence": sequence, "action_id": action_id, "timeout_seconds": timeout_seconds})

    def broker_shutdown(self, shutdown_capability: str):
        if (
            not isinstance(shutdown_capability, str)
            or not 32 <= len(shutdown_capability.encode("utf-8")) <= 4096
        ):
            raise BrokerError("METHOD_DENIED")
        result = self._call(
            "broker_shutdown",
            {"shutdown_capability": shutdown_capability},
        )
        if result != {"stopping": True}:
            raise BrokerError("RESPONSE_INVALID")
        return result
