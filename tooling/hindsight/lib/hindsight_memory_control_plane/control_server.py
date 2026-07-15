"""Authenticated, redacted loopback HTTP control surface."""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future, TimeoutError as FutureTimeout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import ipaddress
import json
import re
import socket
import threading
import time
from typing import Any, Callable, Mapping

from .canonical import StrictJsonError, strict_json_loads


DEFAULT_MAX_REQUEST_BYTES = 16 * 1024
DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024
DEFAULT_MUTATION_TIMEOUT_SECONDS = 30.0
MAX_RESPONSE_DEPTH = 32
MAX_RESPONSE_NODES = 16_384
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
SESSION_OPERATIONS = frozenset({"mint", "status", "close"})
STATUS_KEYS = frozenset(
    {"schema_version", "state", "policy_digest", "active_sessions"}
)
PLAN_SUMMARY_KEYS = frozenset(
    {"schema_version", "plan_digest", "destructive", "actions"}
)
PLAN_ACTION_KEYS = frozenset({"id", "kind"})
FORBIDDEN_RESPONSE_KEYS = frozenset(
    {
        "access_key",
        "access_token",
        "api_key",
        "authorization",
        "bearer",
        "capability",
        "control_key",
        "client_secret",
        "credential",
        "credentials",
        "data_plane_key",
        "data_plane_token",
        "envelope_handle",
        "handle",
        "password",
        "private_key",
        "profile_bearer",
        "proxy_authorization",
        "refresh_token",
        "secret",
        "signing_key",
        "signing_material",
        "session_capability",
        "token",
    }
)
FORBIDDEN_RESPONSE_KEY_TOKENS = frozenset(
    key.replace("_", "") for key in FORBIDDEN_RESPONSE_KEYS
)
ERROR_STATUSES = {
    "NOT_FOUND": 404,
    "METHOD_DENIED": 405,
    "REQUEST_TOO_LARGE": 413,
    "SCHEMA_INVALID": 400,
    "RESPONSE_INVALID": 500,
    "PROVIDER_UNAVAILABLE": 503,
}


class ControlServerError(ValueError):
    """Content-free control service rejection."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _HeaderBudgetExceeded(ValueError):
    """Signal that request headers exceeded the cumulative byte budget."""


class ProviderCallCancelled(RuntimeError):
    """Signal that a provider observed the control-plane deadline."""


class _CumulativeHeaderReader:
    """Bound allocation while the standard parser consumes header lines."""

    def __init__(self, stream: Any, remaining: int) -> None:
        self._stream = stream
        self._remaining = max(0, remaining)

    def readline(self, size: int = -1) -> bytes:
        if self._remaining <= 0:
            raise _HeaderBudgetExceeded("request headers")
        bounded = self._remaining + 1
        if size >= 0:
            bounded = min(bounded, size)
        line = self._stream.readline(bounded)
        if len(line) > self._remaining:
            raise _HeaderBudgetExceeded("request headers")
        self._remaining -= len(line)
        return line

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


class ProviderCallContext:
    """Cooperative, acknowledged cancellation contract for provider work."""

    def __init__(self, deadline: float | None) -> None:
        self._deadline = deadline
        self._cancelled = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def remaining_seconds(self) -> float | None:
        if self._deadline is None:
            return None
        return max(0.0, self._deadline - time.monotonic())

    def checkpoint(self) -> None:
        if self.cancelled or (
            self._deadline is not None
            and time.monotonic() >= self._deadline
        ):
            raise ProviderCallCancelled("provider call cancelled")

    def wait(
        self, event: threading.Event, timeout: float | None = None
    ) -> bool:
        local_deadline = (
            None if timeout is None else time.monotonic() + max(0.0, timeout)
        )
        while True:
            self.checkpoint()
            deadlines = tuple(
                value
                for value in (self._deadline, local_deadline)
                if value is not None
            )
            wait_seconds = 0.01
            if deadlines:
                wait_seconds = min(
                    wait_seconds, max(0.0, min(deadlines) - time.monotonic())
                )
            if event.wait(wait_seconds):
                self.checkpoint()
                return True
            if local_deadline is not None and time.monotonic() >= local_deadline:
                self.checkpoint()
                return False

    def cancel(self) -> None:
        self._cancelled.set()


class _BoundedThreadingHTTPServer(ThreadingHTTPServer):
    def __init__(self, *args: Any, max_connections: int, **kwargs: Any) -> None:
        self._connection_slots = threading.BoundedSemaphore(max_connections)
        super().__init__(*args, **kwargs)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._connection_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._connection_slots.release()
            self.shutdown_request(request)
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connection_slots.release()


class _IPv6ThreadingHTTPServer(_BoundedThreadingHTTPServer):
    address_family = socket.AF_INET6


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                return True
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if normalized in FORBIDDEN_RESPONSE_KEY_TOKENS:
                return True
            if _contains_forbidden_key(child):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_key(child) for child in value)
    return False


def _contains_forbidden_material(
    value: Any, materials: tuple[bytes, ...]
) -> bool:
    def contains(text: str) -> bool:
        encoded = text.encode("utf-8")
        return any(material in encoded for material in materials)

    if isinstance(value, Mapping):
        return any(
            not isinstance(key, str)
            or contains(key)
            or _contains_forbidden_material(child, materials)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(
            _contains_forbidden_material(child, materials) for child in value
        )
    return isinstance(value, str) and contains(value)


def _json_compatible(
    value: Any,
    *,
    max_depth: int = MAX_RESPONSE_DEPTH,
    max_nodes: int = MAX_RESPONSE_NODES,
    max_string_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> Any:
    """Materialize accepted abstract containers before JSON encoding."""
    remaining = max_nodes

    def materialize(candidate: Any, depth: int) -> Any:
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > max_depth:
            raise ValueError("response structure exceeds configured limits")
        if isinstance(candidate, str):
            if len(candidate.encode("utf-8")) > max_string_bytes:
                raise ValueError("response string exceeds configured limits")
            return candidate
        if isinstance(candidate, Mapping):
            result: dict[str, Any] = {}
            for key, child in candidate.items():
                if (
                    not isinstance(key, str)
                    or len(key.encode("utf-8")) > max_string_bytes
                ):
                    raise ValueError("response key exceeds configured limits")
                result[key] = materialize(child, depth + 1)
            return result
        if isinstance(candidate, (list, tuple)):
            return [materialize(child, depth + 1) for child in candidate]
        return candidate

    return materialize(value, 0)


def _closed_mapping(
    value: Any, keys: frozenset[str], label: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ControlServerError(f"{label}_INVALID")
    return value


def _status_response(value: Any) -> Mapping[str, Any]:
    result = _closed_mapping(value, STATUS_KEYS, "RESPONSE")
    if (
        type(result["schema_version"]) is not int
        or result["schema_version"] != 1
        or not isinstance(result["state"], str)
        or not IDENTIFIER.fullmatch(result["state"])
        or not isinstance(result["policy_digest"], str)
        or not DIGEST.fullmatch(result["policy_digest"])
        or type(result["active_sessions"]) is not int
        or result["active_sessions"] < 0
    ):
        raise ControlServerError("RESPONSE_INVALID")
    return result


def _plan_response(value: Any, plan_digest: str) -> Mapping[str, Any]:
    result = _closed_mapping(value, PLAN_SUMMARY_KEYS, "RESPONSE")
    if (
        type(result["schema_version"]) is not int
        or result["schema_version"] != 1
        or result["plan_digest"] != plan_digest
        or result["destructive"] is not False
        or not isinstance(result["actions"], (list, tuple))
    ):
        raise ControlServerError("RESPONSE_INVALID")
    for action in result["actions"]:
        record = _closed_mapping(action, PLAN_ACTION_KEYS, "RESPONSE")
        if any(
            not isinstance(record[key], str)
            or not IDENTIFIER.fullmatch(record[key])
            for key in PLAN_ACTION_KEYS
        ):
            raise ControlServerError("RESPONSE_INVALID")
    return result


class ControlServer:
    """Serve a closed, secret-free control API on a literal loopback address."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        access_key_resolver: Callable[[ProviderCallContext], str | bytes],
        forbidden_material_resolver: Callable[
            [ProviderCallContext], tuple[str | bytes, ...]
        ],
        status_provider: Callable[[ProviderCallContext], Mapping[str, Any]],
        plan_provider: Callable[
            [str, ProviderCallContext], Mapping[str, Any] | None
        ],
        session_operator: Callable[
            [str, Mapping[str, Any], ProviderCallContext], Mapping[str, Any]
        ],
        max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        request_timeout_seconds: float = 2.0,
        max_connections: int = 16,
    ) -> None:
        try:
            address = ipaddress.ip_address(host)
        except ValueError as error:
            raise ControlServerError("BIND_DENIED") from error
        if not address.is_loopback or host not in {"127.0.0.1", "::1"}:
            raise ControlServerError("BIND_DENIED")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ControlServerError("PORT_INVALID")
        if not callable(access_key_resolver) or not callable(
            forbidden_material_resolver
        ):
            raise ControlServerError("ACCESS_KEY_RESOLVER_INVALID")
        if not all(
            callable(value)
            for value in (status_provider, plan_provider, session_operator)
        ):
            raise ControlServerError("PROVIDER_INVALID")
        if type(max_request_bytes) is not int or max_request_bytes < 256:
            raise ControlServerError("REQUEST_LIMIT_INVALID")
        if type(max_response_bytes) is not int or max_response_bytes < 128:
            raise ControlServerError("RESPONSE_LIMIT_INVALID")
        if (
            type(request_timeout_seconds) not in (int, float)
            or not 0 < request_timeout_seconds <= 30
        ):
            raise ControlServerError("REQUEST_TIMEOUT_INVALID")
        if type(max_connections) is not int or not 1 <= max_connections <= 256:
            raise ControlServerError("CONNECTION_LIMIT_INVALID")

        self.host = host
        self.port = port
        self.access_key_resolver = access_key_resolver
        self.forbidden_material_resolver = forbidden_material_resolver
        self.status_provider = status_provider
        self.plan_provider = plan_provider
        self.session_operator = session_operator
        self.max_request_bytes = max_request_bytes
        self.max_response_bytes = max_response_bytes
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.max_connections = max_connections
        self._callback_slots = threading.BoundedSemaphore(max_connections)
        self._mutation_slots = threading.BoundedSemaphore(max_connections)
        self._session_operation_timeout_seconds = (
            DEFAULT_MUTATION_TIMEOUT_SECONDS
        )
        self._session_operations_lock = threading.Lock()
        self._session_operations: OrderedDict[
            tuple[str, str], tuple[Future[Any], float, ProviderCallContext]
        ] = OrderedDict()
        self._callback_operations_lock = threading.Lock()
        self._callback_operations: dict[
            Future[Any], ProviderCallContext
        ] = {}
        self._accepting_callbacks = False
        self._max_session_operations = max(64, max_connections * 4)
        self._session_operation_ttl_seconds = 300.0
        self._shutdown_timeout_seconds = 1.0
        self._accepting_mutations = False
        self._lifecycle_lock = threading.Lock()
        self._lifecycle_token: object | None = None
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._lifecycle_lock:
            self._start_locked()

    def _start_locked(self) -> None:
        if self._server is not None:
            raise ControlServerError("ALREADY_STARTED")
        with self._session_operations_lock:
            if any(
                not future.done()
                for future, _started_at, _context
                in self._session_operations.values()
            ):
                raise ControlServerError("SHUTDOWN_INCOMPLETE")
            self._accepting_mutations = True
        with self._callback_operations_lock:
            if any(not future.done() for future in self._callback_operations):
                raise ControlServerError("SHUTDOWN_INCOMPLETE")
            self._accepting_callbacks = True
        owner = self
        lifecycle_token = object()

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self) -> None:
                super().setup()
                self._hindsight_lifecycle_token = lifecycle_token
                self.connection.settimeout(owner.request_timeout_seconds)
                self._hindsight_deadline_lock = threading.Lock()
                self._hindsight_deadline_token = None

            def handle_one_request(self) -> None:
                token = object()
                deadline = time.monotonic() + owner.request_timeout_seconds
                with self._hindsight_deadline_lock:
                    self._hindsight_deadline_token = token
                    self._hindsight_request_deadline = deadline
                    self._hindsight_reading_request = True
                    self._hindsight_request_expired = False

                def expire() -> None:
                    with self._hindsight_deadline_lock:
                        if (
                            self._hindsight_deadline_token is not token
                            or not self._hindsight_reading_request
                        ):
                            return
                        self._hindsight_deadline_token = None
                        self._hindsight_request_expired = True
                        self.close_connection = True
                        try:
                            self.connection.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass

                timer = threading.Timer(
                    owner.request_timeout_seconds, expire
                )
                timer.daemon = True
                try:
                    timer.start()
                    super().handle_one_request()
                finally:
                    with self._hindsight_deadline_lock:
                        if self._hindsight_deadline_token is token:
                            self._hindsight_deadline_token = None
                    timer.cancel()
                    self.connection.settimeout(owner.request_timeout_seconds)

            def parse_request(self) -> bool:
                original = self.rfile
                self.rfile = _CumulativeHeaderReader(
                    original,
                    owner.max_request_bytes - len(self.raw_requestline),
                )
                try:
                    return super().parse_request()
                except _HeaderBudgetExceeded:
                    self.close_connection = True
                    owner._send(
                        self,
                        413,
                        {"error": "REQUEST_TOO_LARGE"},
                        validate=False,
                    )
                    return False
                finally:
                    self.rfile = original

            def _hindsight_set_reading_request(self, reading: bool) -> None:
                with self._hindsight_deadline_lock:
                    self._hindsight_reading_request = reading
                    expired = (
                        self._hindsight_request_expired
                        or (
                            reading
                            and time.monotonic()
                            >= self._hindsight_request_deadline
                        )
                    )
                if expired:
                    self.close_connection = True
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    raise TimeoutError

            def handle(self) -> None:
                try:
                    super().handle()
                except (TimeoutError, OSError):
                    self.close_connection = True

            def do_GET(self) -> None:
                owner._handle(self, "GET")

            def do_POST(self) -> None:
                owner._handle(self, "POST")

            def do_PUT(self) -> None:
                owner._handle(self, "PUT")

            def do_PATCH(self) -> None:
                owner._handle(self, "PATCH")

            def do_DELETE(self) -> None:
                owner._handle(self, "DELETE")

            def do_OPTIONS(self) -> None:
                owner._handle(self, "OPTIONS")

            def do_HEAD(self) -> None:
                owner._handle(self, "HEAD")

            def do_TRACE(self) -> None:
                owner._handle(self, "TRACE")

            def do_CONNECT(self) -> None:
                owner._handle(self, "CONNECT")

            def __getattr__(self, name: str) -> Any:
                if name.startswith("do_") and len(name) > 3:
                    method = name[3:]
                    return lambda: owner._handle(self, method)
                raise AttributeError(name)

            def log_message(
                self, format_string: str, *args: Any
            ) -> None:
                return

        server_type = (
            _IPv6ThreadingHTTPServer
            if self.host == "::1"
            else _BoundedThreadingHTTPServer
        )
        try:
            server = server_type(
                (self.host, self.port), Handler,
                max_connections=self.max_connections,
            )
        except OSError as error:
            with self._session_operations_lock:
                self._accepting_mutations = False
            with self._callback_operations_lock:
                self._accepting_callbacks = False
            raise ControlServerError("BIND_FAILED") from error
        server.daemon_threads = True
        thread = threading.Thread(
            target=server.serve_forever,
            name="hindsight-control-http",
            daemon=True,
        )
        self._lifecycle_token = lifecycle_token
        try:
            thread.start()
        except Exception as error:
            with self._session_operations_lock:
                self._accepting_mutations = False
            with self._callback_operations_lock:
                self._accepting_callbacks = False
            self._lifecycle_token = None
            server.server_close()
            raise ControlServerError("START_FAILED") from error
        self._server = server
        self.port = int(server.server_address[1])
        self._thread = thread

    def close(self) -> None:
        with self._lifecycle_lock:
            self._close_locked()

    def _close_locked(self) -> None:
        self._lifecycle_token = None
        with self._session_operations_lock:
            self._accepting_mutations = False
            pending = [
                (future, context)
                for future, _started_at, context
                in self._session_operations.values()
                if not future.done()
            ]
            for _future, context in pending:
                context.cancel()
        with self._callback_operations_lock:
            self._accepting_callbacks = False
            callback_pending = [
                (future, context)
                for future, context in self._callback_operations.items()
                if not future.done()
            ]
            for _future, context in callback_pending:
                context.cancel()
        server = self._server
        thread = self._thread
        if server is not None:
            server.shutdown()
            server.server_close()
        deadline = time.monotonic() + self._shutdown_timeout_seconds
        incomplete = False
        for future, _context in pending + callback_pending:
            try:
                future.result(timeout=max(0.0, deadline - time.monotonic()))
            except FutureTimeout:
                incomplete = True
            except BaseException:
                pass
        if thread is not None:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
            incomplete = incomplete or thread.is_alive()
        self._thread = None
        self._server = None
        if incomplete:
            raise ControlServerError("SHUTDOWN_INCOMPLETE")

    def _handle(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        if not self._handler_lifecycle_is_current(handler):
            handler.close_connection = True
            return
        handler._hindsight_set_reading_request(False)
        if self._header_bytes(handler) > self.max_request_bytes:
            handler.close_connection = True
            self._send(
                handler,
                413,
                {"error": "REQUEST_TOO_LARGE"},
                validate=False,
            )
            return
        if method == "GET":
            handler.close_connection = True
        try:
            authenticated = self._authenticated(handler)
        except ControlServerError as error:
            handler.close_connection = True
            self._send(
                handler,
                ERROR_STATUSES.get(error.code, 500),
                {"error": error.code if error.code in ERROR_STATUSES else "INTERNAL_ERROR"},
                validate=False,
            )
            return
        if not authenticated:
            handler.close_connection = True
            self._send(
                handler, 401, {"error": "AUTH_REQUIRED"}, validate=False
            )
            return
        try:
            handler._hindsight_forbidden_material = self._forbidden_material(
                handler
            )
            status, result = self._dispatch(handler, method)
            if not self._handler_lifecycle_is_current(handler):
                handler.close_connection = True
                return
            self._send(handler, status, result)
        except TimeoutError:
            handler.close_connection = True
        except ControlServerError as error:
            handler.close_connection = True
            if error.code in ERROR_STATUSES:
                self._send(
                    handler,
                    ERROR_STATUSES[error.code],
                    {"error": error.code},
                    validate=False,
                )
            else:
                self._send(
                    handler, 500, {"error": "INTERNAL_ERROR"}, validate=False
                )
        except Exception:
            handler.close_connection = True
            self._send(
                handler, 500, {"error": "INTERNAL_ERROR"}, validate=False
            )

    def _authenticated(self, handler: BaseHTTPRequestHandler) -> bool:
        names = {name.lower() for name in handler.headers.keys()}
        if (
            "proxy-authorization" in names
            or "forwarded" in names
            or any(name.startswith("x-forwarded-") for name in names)
        ):
            return False
        values = handler.headers.get_all("Authorization", [])
        if len(values) != 1:
            return False
        authorization = values[0]
        if not isinstance(authorization, str) or re.fullmatch(
            r"Bearer [A-Za-z0-9._~+/=-]+", authorization
        ) is None:
            return False
        supplied = authorization[7:].encode("utf-8")
        if len(supplied) < 32 or len(supplied) > 4096:
            return False
        try:
            resolved = self._invoke_callback(
                handler, self.access_key_resolver
            )
        except ControlServerError:
            raise
        except Exception:
            return False
        if isinstance(resolved, str):
            expected = resolved.encode("utf-8")
        elif isinstance(resolved, bytes):
            expected = resolved
        else:
            return False
        if len(expected) < 32 or len(expected) > 4096:
            return False
        handler._hindsight_control_secret = expected
        return hmac.compare_digest(supplied, expected)

    def _dispatch(
        self, handler: BaseHTTPRequestHandler, method: str
    ) -> tuple[int, Mapping[str, Any]]:
        path = handler.path.split("?", 1)[0]
        if method == "GET":
            if path == "/health":
                return 200, {"schema_version": 1, "status": "ok"}
            if path == "/v1/status":
                return 200, _status_response(
                    self._invoke_callback(handler, self.status_provider)
                )
            prefix = "/v1/plans/"
            if path.startswith(prefix):
                plan_digest = path[len(prefix) :]
                if not DIGEST.fullmatch(plan_digest):
                    raise ControlServerError("NOT_FOUND")
                result = self._invoke_callback(
                    handler, self.plan_provider, plan_digest
                )
                if result is None:
                    raise ControlServerError("NOT_FOUND")
                return 200, _plan_response(result, plan_digest)
            raise ControlServerError("NOT_FOUND")

        if method != "POST":
            raise ControlServerError("METHOD_DENIED")
        prefix = "/v1/sessions/"
        if not path.startswith(prefix):
            raise ControlServerError(
                "METHOD_DENIED"
                if path in {"/health", "/v1/status"}
                else "NOT_FOUND"
            )
        operation = path[len(prefix) :]
        if operation not in SESSION_OPERATIONS:
            raise ControlServerError("NOT_FOUND")
        request = self._read_request(handler)
        if (
            set(request) != {"session_id"}
            or not isinstance(request["session_id"], str)
            or not IDENTIFIER.fullmatch(request["session_id"])
        ):
            raise ControlServerError("SCHEMA_INVALID")
        if operation in {"mint", "close"}:
            result = self._invoke_session_operation(
                handler, operation, request
            )
            if result is None:
                return 202, {
                    "session_id": request["session_id"],
                    "state": "pending",
                }
        else:
            result = self._invoke_callback(
                handler, self.session_operator, operation, request
            )
        if (
            not isinstance(result, Mapping)
            or set(result) != {"session_id", "state"}
            or result["session_id"] != request["session_id"]
            or result["state"] not in {"staged", "active", "closed"}
        ):
            raise ControlServerError("RESPONSE_INVALID")
        return 200, result

    def _invoke_session_operation(
        self,
        handler: BaseHTTPRequestHandler,
        operation: str,
        request: Mapping[str, Any],
    ) -> Any | None:
        """Run each mutating session operation once and acknowledge timeouts."""
        session_id = request["session_id"]
        key = (session_id, operation)
        now = time.monotonic()
        with self._session_operations_lock:
            if (
                not self._handler_lifecycle_is_current(handler)
                or not self._accepting_mutations
            ):
                raise ControlServerError("PROVIDER_UNAVAILABLE")
            for stored_key, (
                stored_future, started_at, _stored_context
            ) in tuple(
                self._session_operations.items()
            ):
                if (
                    stored_future.done()
                    and now - started_at >= self._session_operation_ttl_seconds
                ):
                    del self._session_operations[stored_key]
            entry = self._session_operations.pop(key, None)
            if entry is not None:
                future, started_at, context = entry
                # Successes and failures are both idempotency outcomes. Keep
                # the exact future until its TTL expires so a retry cannot
                # repeat a mutation whose caller only observed an error.
                self._session_operations[key] = entry
                leader = False
            if entry is None:
                if any(
                    stored_key[0] == session_id and not stored_future.done()
                    for stored_key, (stored_future, _started, _context)
                    in self._session_operations.items()
                ):
                    raise ControlServerError("PROVIDER_UNAVAILABLE")
                # Every completed future is an idempotency outcome, including
                # failures.  Retain it for the full TTL; admitting new work by
                # evicting an outcome could repeat a mutation whose response
                # was lost to the caller.
                if len(self._session_operations) >= self._max_session_operations:
                    raise ControlServerError("PROVIDER_UNAVAILABLE")
                if not self._mutation_slots.acquire(blocking=False):
                    raise ControlServerError("PROVIDER_UNAVAILABLE")
                future = Future()
                context = ProviderCallContext(
                    time.monotonic()
                    + self._session_operation_timeout_seconds
                )
                started_at = now
                self._session_operations[key] = (
                    future, started_at, context
                )
                leader = True

        if leader:
            def invoke() -> None:
                try:
                    future.set_result(
                        self.session_operator(operation, request, context)
                    )
                except BaseException as error:
                    future.set_exception(error)
                finally:
                    self._mutation_slots.release()

            thread = threading.Thread(
                target=invoke,
                name="hindsight-control-session-operation",
                daemon=True,
            )
            try:
                thread.start()
            except Exception as error:
                # Publish the terminal outcome before making the cache entry
                # unreachable so any waiter holding this future is released.
                future.set_exception(error)
                with self._session_operations_lock:
                    current = self._session_operations.get(key)
                    if current is not None and current[0] is future:
                        del self._session_operations[key]
                self._mutation_slots.release()
                raise ControlServerError("PROVIDER_UNAVAILABLE") from None

        deadline = getattr(
            handler,
            "_hindsight_request_deadline",
            time.monotonic() + self.request_timeout_seconds,
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            return future.result(timeout=remaining)
        except ProviderCallCancelled:
            raise ControlServerError("PROVIDER_UNAVAILABLE") from None
        except FutureTimeout:
            return None

    def _read_request(self, handler: BaseHTTPRequestHandler) -> dict[str, Any]:
        if handler.headers.get("Transfer-Encoding") is not None:
            raise ControlServerError("SCHEMA_INVALID")
        lengths = handler.headers.get_all("Content-Length", [])
        if len(lengths) != 1:
            raise ControlServerError("SCHEMA_INVALID")
        try:
            length = int(lengths[0])
        except ValueError as error:
            raise ControlServerError("SCHEMA_INVALID") from error
        if length < 0:
            raise ControlServerError("SCHEMA_INVALID")
        header_bytes = self._header_bytes(handler)
        if length > self.max_request_bytes - header_bytes:
            raise ControlServerError("REQUEST_TOO_LARGE")
        if handler.headers.get_content_type() != "application/json":
            raise ControlServerError("SCHEMA_INVALID")
        handler._hindsight_set_reading_request(True)
        try:
            body = handler.rfile.read(length)
        finally:
            handler._hindsight_set_reading_request(False)
        if len(body) != length:
            raise ControlServerError("SCHEMA_INVALID")
        try:
            value = strict_json_loads(body)
        except (StrictJsonError, ValueError, UnicodeDecodeError) as error:
            raise ControlServerError("SCHEMA_INVALID") from error
        if not isinstance(value, dict):
            raise ControlServerError("SCHEMA_INVALID")
        return value

    @staticmethod
    def _header_bytes(handler: BaseHTTPRequestHandler) -> int:
        return (
            len(handler.raw_requestline)
            + sum(
                len(name.encode("utf-8")) + len(value.encode("utf-8")) + 4
                for name, value in handler.headers.items()
            )
            + 2
        )

    def _invoke_callback(
        self,
        handler: BaseHTTPRequestHandler,
        callback: Callable[..., Any],
        *args: Any,
    ) -> Any:
        deadline = getattr(
            handler,
            "_hindsight_request_deadline",
            time.monotonic() + self.request_timeout_seconds,
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ControlServerError("PROVIDER_UNAVAILABLE")
        future: Future[Any] = Future()
        context = ProviderCallContext(deadline)
        with self._callback_operations_lock:
            if (
                not self._handler_lifecycle_is_current(handler)
                or not self._accepting_callbacks
                or not self._callback_slots.acquire(blocking=False)
            ):
                raise ControlServerError("PROVIDER_UNAVAILABLE")
            self._callback_operations[future] = context

        def complete(_future: Future[Any]) -> None:
            with self._callback_operations_lock:
                self._callback_operations.pop(_future, None)

        future.add_done_callback(complete)

        def invoke() -> None:
            try:
                result = callback(*args, context)
            except BaseException as error:
                self._callback_slots.release()
                future.set_exception(error)
            else:
                self._callback_slots.release()
                future.set_result(result)

        thread = threading.Thread(
            target=invoke,
            name="hindsight-control-callback",
            daemon=True,
        )
        try:
            thread.start()
        except Exception as error:
            self._callback_slots.release()
            # The done callback removes the tracked future only after its
            # terminal state is visible to concurrent shutdown/wait paths.
            future.set_exception(error)
            raise ControlServerError("PROVIDER_UNAVAILABLE") from None
        try:
            return future.result(timeout=remaining)
        except ProviderCallCancelled:
            raise ControlServerError("PROVIDER_UNAVAILABLE") from None
        except FutureTimeout:
            context.cancel()
            try:
                future.result(timeout=min(0.05, self.request_timeout_seconds))
            except ProviderCallCancelled:
                raise ControlServerError("PROVIDER_UNAVAILABLE") from None
            except FutureTimeout:
                raise ControlServerError(
                    "PROVIDER_CANCELLATION_FAILED"
                ) from None
            except BaseException:
                raise ControlServerError(
                    "PROVIDER_CANCELLATION_FAILED"
                ) from None
            raise ControlServerError("PROVIDER_CANCELLATION_FAILED")

    def _handler_lifecycle_is_current(
        self, handler: BaseHTTPRequestHandler
    ) -> bool:
        token = getattr(handler, "_hindsight_lifecycle_token", None)
        return token is None or (
            self._lifecycle_token is not None
            and token is self._lifecycle_token
        )

    def _forbidden_material(
        self, handler: BaseHTTPRequestHandler
    ) -> tuple[bytes, ...] | None:
        try:
            resolved = self._invoke_callback(
                handler, self.forbidden_material_resolver
            )
        except ControlServerError:
            raise
        except Exception:
            return None
        if not isinstance(resolved, (list, tuple)):
            return None
        materials: list[bytes] = []
        for value in resolved:
            if isinstance(value, str):
                material = value.encode("utf-8")
            elif isinstance(value, bytes):
                material = value
            else:
                return None
            if not material:
                return None
            materials.append(material)
        return tuple(materials)

    def _send(
        self,
        handler: BaseHTTPRequestHandler,
        status: int,
        value: Mapping[str, Any],
        *,
        validate: bool = True,
    ) -> None:
        if not self._handler_lifecycle_is_current(handler):
            handler.close_connection = True
            return
        if validate and not isinstance(value, Mapping):
            status, value, validate = 500, {"error": "RESPONSE_INVALID"}, False
        try:
            normalized = _json_compatible(
                value,
                max_string_bytes=self.max_response_bytes,
            )
            if validate and _contains_forbidden_key(normalized):
                raise ValueError("response contains forbidden key")
            encoder = json.JSONEncoder(
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            body_buffer = bytearray()
            for chunk in encoder.iterencode(normalized):
                encoded = chunk.encode("utf-8")
                if len(body_buffer) + len(encoded) > self.max_response_bytes:
                    raise ValueError("response exceeds configured size limit")
                body_buffer.extend(encoded)
            body = bytes(body_buffer)
        except Exception:
            status, body, validate = 500, b'{"error":"RESPONSE_INVALID"}', False
        if validate:
            secret = getattr(handler, "_hindsight_control_secret", None)
            forbidden_material = getattr(
                handler,
                "_hindsight_forbidden_material",
                None,
            )
            response_material = (
                ()
                if forbidden_material is None
                else forbidden_material
            )
            if isinstance(secret, bytes) and secret:
                response_material = (secret, *response_material)
            if (
                _contains_forbidden_key(normalized)
                or forbidden_material is None
                or any(material in body for material in response_material)
                or _contains_forbidden_material(normalized, response_material)
            ):
                status, body = 500, b'{"error":"RESPONSE_INVALID"}'
        if len(body) > self.max_response_bytes:
            status, body = 500, b'{"error":"RESPONSE_INVALID"}'
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Pragma", "no-cache")
        handler.send_header("X-Content-Type-Options", "nosniff")
        if handler.close_connection:
            handler.send_header("Connection", "close")
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(body)
