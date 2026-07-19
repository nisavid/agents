from collections.abc import Mapping
import http.client
import json
import socket
import threading
import time
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
sys.path.insert(0, str(LIB))

from hindsight_memory_control_plane.control_server import (
    ControlServer,
    ControlServerError,
)
import hindsight_memory_control_plane.control_server as control_server_module


KEY = "control-" + "k" * 40
DIGEST = "a" * 64
DATA_PLANE_TOKEN = "data-plane-token-value"
SIGNING_MATERIAL = "private-signing-material-value"


class ControlServerTest(unittest.TestCase):
    def setUp(self):
        self.resolutions = 0
        self.material_resolutions = 0
        self.session_calls = []

        def resolve_key(_context):
            self.resolutions += 1
            return KEY

        def session_operation(operation, request, _context):
            self.session_calls.append((operation, request))
            return {
                "session_id": request["session_id"],
                "state": {
                    "mint": "staged",
                    "status": "active",
                    "close": "closed",
                }[operation],
            }

        def resolve_forbidden_material(_context):
            self.material_resolutions += 1
            return (DATA_PLANE_TOKEN, SIGNING_MATERIAL)

        self.server = ControlServer(
            host="127.0.0.1",
            port=0,
            access_key_resolver=resolve_key,
            forbidden_material_resolver=resolve_forbidden_material,
            status_provider=lambda _context: {
                "schema_version": 1,
                "state": "inactive",
                "policy_digest": DIGEST,
                "active_sessions": 0,
            },
            plan_provider=lambda plan_digest, _context: {
                "schema_version": 1,
                "plan_digest": plan_digest,
                "destructive": False,
                "actions": [],
            },
            session_operator=session_operation,
            max_request_bytes=512,
            max_response_bytes=1024,
        )
        self.server.start()

    def tearDown(self):
        self.server.close()

    def request(self, method, path, *, body=None, headers=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.port, timeout=2
        )
        encoded = None if body is None else json.dumps(body).encode()
        supplied = dict(headers or {})
        if encoded is not None:
            supplied.setdefault("Content-Type", "application/json")
        connection.request(method, path, body=encoded, headers=supplied)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response.status, dict(response.getheaders()), json.loads(payload)

    @staticmethod
    def auth():
        return {"Authorization": f"Bearer {KEY}"}

    def test_bind_is_literal_loopback_only(self):
        for host in ("0.0.0.0", "localhost", "192.0.2.1", "::"):
            with (
                self.subTest(host=host),
                self.assertRaisesRegex(ControlServerError, "BIND_DENIED"),
            ):
                ControlServer(
                    host=host,
                    port=0,
                    access_key_resolver=lambda _context: KEY,
                    forbidden_material_resolver=lambda _context: (),
                    status_provider=lambda _context: {},
                    plan_provider=lambda digest, _context: {},
                    session_operator=lambda operation, request, _context: {},
                )
        ipv6 = ControlServer(
            host="::1",
            port=0,
            access_key_resolver=lambda _context: KEY,
            forbidden_material_resolver=lambda _context: (),
            status_provider=lambda _context: {},
            plan_provider=lambda digest, _context: {},
            session_operator=lambda operation, request, _context: {},
        )
        ipv6.close()

    def test_every_request_requires_fresh_direct_bearer_authentication(self):
        cases = [
            ({}, 401),
            ({"Authorization": "Bearer " + "w" * 40}, 401),
            ({"X-Forwarded-Authorization": f"Bearer {KEY}"}, 401),
            ({"Proxy-Authorization": f"Bearer {KEY}"}, 401),
            (self.auth(), 200),
            (self.auth(), 200),
        ]
        before = self.resolutions
        for headers, expected in cases:
            status, response_headers, body = self.request(
                "GET", "/health", headers=headers
            )
            self.assertEqual(status, expected)
            self.assertEqual(response_headers["Cache-Control"], "no-store")
            self.assertEqual(response_headers["Connection"], "close")
            self.assertNotIn(KEY, json.dumps(body))
        self.assertEqual(self.resolutions - before, 3)

    def test_implausible_or_proxied_auth_is_rejected_before_key_resolution(self):
        before = self.resolutions
        cases = (
            {},
            {"Authorization": "Basic opaque"},
            {"Authorization": "Bearer short"},
            {
                **self.auth(),
                "Forwarded": "for=127.0.0.1",
            },
            {
                **self.auth(),
                "X-Forwarded-For": "127.0.0.1",
            },
            {
                **self.auth(),
                "Proxy-Authorization": "Bearer " + "x" * 32,
            },
        )
        for headers in cases:
            with self.subTest(headers=headers):
                self.assertEqual(
                    self.request("GET", "/health", headers=headers)[0], 401
                )
        self.assertEqual(self.resolutions, before)

        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.port, timeout=2
        )
        self.addCleanup(connection.close)
        connection.putrequest("GET", "/health")
        connection.putheader("Authorization", f"Bearer {KEY}")
        connection.putheader("Authorization", f"Bearer {KEY}")
        connection.endheaders()
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 401)
        self.assertEqual(self.resolutions, before)

    def test_unsupported_methods_are_authenticated_before_method_denial(self):
        for method in ("TRACE", "CONNECT", "PROPFIND"):
            with self.subTest(method=method):
                status, headers, body = self.request(method, "/health")
                self.assertEqual((status, body), (401, {"error": "AUTH_REQUIRED"}))
                self.assertEqual(headers["Connection"], "close")
                status, headers, body = self.request(
                    method, "/health", headers=self.auth()
                )
                self.assertEqual((status, body), (405, {"error": "METHOD_DENIED"}))
                self.assertEqual(headers["Connection"], "close")

    def test_health_status_and_plan_inspection_are_closed_and_redacted(self):
        status, _, health = self.request("GET", "/health", headers=self.auth())
        self.assertEqual(status, 200)
        self.assertEqual(health, {"schema_version": 1, "status": "ok"})

        status, _, report = self.request(
            "GET", "/v1/status", headers=self.auth()
        )
        self.assertEqual(status, 200)
        self.assertEqual(report["policy_digest"], DIGEST)
        self.assertEqual(report["state"], "inactive")

        status, _, plan = self.request(
            "GET", f"/v1/plans/{DIGEST}", headers=self.auth()
        )
        self.assertEqual(status, 200)
        self.assertEqual(plan["plan_digest"], DIGEST)

        self.server.plan_provider = lambda plan_digest, _context: {
            "schema_version": 1,
            "plan_digest": "b" * 64,
            "destructive": False,
            "actions": [],
        }
        status, _, body = self.request(
            "GET", f"/v1/plans/{DIGEST}", headers=self.auth()
        )
        self.assertEqual(status, 500)
        self.assertEqual(body, {"error": "RESPONSE_INVALID"})

        for method, path in (
            ("GET", "/v1/plans/not-a-digest"),
            ("GET", "/v1/unknown"),
            ("POST", "/v1/status"),
        ):
            with self.subTest(method=method, path=path):
                status, _, body = self.request(
                    method,
                    path,
                    headers=self.auth(),
                    body={} if method == "POST" else None,
                )
                self.assertIn(status, {404, 405})
                self.assertEqual(set(body), {"error"})

    def test_only_redacted_broker_session_operations_are_exposed(self):
        for operation in ("mint", "status", "close"):
            status, _, result = self.request(
                "POST",
                f"/v1/sessions/{operation}",
                headers=self.auth(),
                body={"session_id": "session-1"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(result["session_id"], "session-1")
            self.assertNotIn("capability", json.dumps(result).lower())
        self.assertEqual(
            [item[0] for item in self.session_calls],
            ["mint", "status", "close"],
        )

        for operation in ("exchange", "recall", "retain"):
            status, _, result = self.request(
                "POST",
                f"/v1/sessions/{operation}",
                headers=self.auth(),
                body={"session_id": "session-1"},
            )
            self.assertEqual(status, 404)
            self.assertEqual(result, {"error": "NOT_FOUND"})

        self.server.session_operator = lambda operation, request, _context: {
            "session_id": request["session_id"],
            "state": "active",
            "payload": "private-session-data",
        }
        status, _, result = self.request(
            "POST",
            "/v1/sessions/status",
            headers=self.auth(),
            body={"session_id": "session-1"},
        )
        self.assertEqual(status, 500)
        self.assertEqual(result, {"error": "RESPONSE_INVALID"})
        self.assertNotIn("private-session-data", json.dumps(result))

        def fail_with_private_error(operation, request, _context):
            raise ControlServerError("private-provider-diagnostic")

        self.server.session_operator = fail_with_private_error
        status, _, result = self.request(
            "POST",
            "/v1/sessions/status",
            headers=self.auth(),
            body={"session_id": "session-1"},
        )
        self.assertEqual(status, 500)
        self.assertEqual(result, {"error": "INTERNAL_ERROR"})
        self.assertNotIn("private-provider-diagnostic", json.dumps(result))

    def test_request_and_response_bodies_are_bounded(self):
        before = self.resolutions
        status, headers, body = self.request(
            "GET",
            "/health",
            headers={**self.auth(), "X-Padding": "x" * 600},
        )
        self.assertEqual(status, 413)
        self.assertEqual(body, {"error": "REQUEST_TOO_LARGE"})
        self.assertEqual(headers["Connection"], "close")
        self.assertEqual(self.resolutions, before)

        status, _, body = self.request(
            "POST",
            "/v1/sessions/mint",
            headers=self.auth(),
            body={"session_id": "x" * 600},
        )
        self.assertEqual(status, 413)
        self.assertEqual(body, {"error": "REQUEST_TOO_LARGE"})
        self.assertEqual(self.session_calls, [])

    def test_cumulative_headers_are_bounded_during_standard_parser_reads(self):
        before = self.resolutions
        connection = socket.create_connection(
            ("127.0.0.1", self.server.port), timeout=1
        )
        self.addCleanup(connection.close)
        request = ["GET /health HTTP/1.1", "Host: 127.0.0.1"]
        request.extend(f"X-Pad-{index}: {'x' * 40}" for index in range(32))
        connection.sendall(("\r\n".join(request) + "\r\n\r\n").encode())
        response = bytearray()
        while chunk := connection.recv(4096):
            response.extend(chunk)
        self.assertIn(b"413", response)
        self.assertIn(b'"error":"REQUEST_TOO_LARGE"', response)
        self.assertEqual(self.resolutions, before)

    def test_raw_request_line_and_parser_errors_use_bounded_json(self):
        requests = (
            (
                b"GET /" + b"x" * 2048 + b" HTTP/1.1\r\n\r\n",
                b'"error":"REQUEST_TOO_LARGE"',
            ),
            (
                b"GET /health HTTP/9.9\r\nHost: 127.0.0.1\r\n\r\n",
                b'"error":"SCHEMA_INVALID"',
            ),
        )
        for request, expected in requests:
            with self.subTest(expected=expected):
                connection = socket.create_connection(
                    ("127.0.0.1", self.server.port), timeout=1
                )
                try:
                    connection.sendall(request)
                    response = bytearray()
                    while chunk := connection.recv(4096):
                        response.extend(chunk)
                finally:
                    connection.close()
                self.assertIn(b"Content-Type: application/json", response)
                self.assertIn(expected, response)
                self.assertNotIn(b"<!DOCTYPE HTML", response)
                self.assertLessEqual(len(response), 2048)

    def test_authentication_capacity_failures_are_reported_as_unavailable(self):
        self.server.close()
        self.server._callback_slots = threading.BoundedSemaphore(1)
        release = threading.Event()
        self.addCleanup(release.set)
        entered = threading.Event()
        calls = []

        def resolve_key(context):
            calls.append(context)
            entered.set()
            context.wait(release, 1)
            return KEY

        self.server.access_key_resolver = resolve_key
        self.server.start()
        first_result = []
        first = threading.Thread(
            target=lambda: first_result.append(
                self.request("GET", "/health", headers=self.auth())
            )
        )
        first.start()
        self.addCleanup(first.join, 1)
        self.assertTrue(entered.wait(0.5))

        status, headers, body = self.request(
            "GET", "/health", headers=self.auth()
        )

        self.assertEqual(status, 503)
        self.assertEqual(headers["Connection"], "close")
        self.assertEqual(body, {"error": "PROVIDER_UNAVAILABLE"})
        self.assertEqual(len(calls), 1)
        release.set()
        first.join(timeout=1)
        self.assertFalse(first.is_alive())
        self.assertEqual(first_result[0][0], 200)

    def test_early_request_rejections_explicitly_close_the_connection(self):
        cases = (
            ("POST", "/v1/sessions/mint", {}, {"session_id": "session-1"}, 401),
            ("POST", "/v1/unknown", self.auth(), {"ignored": "body"}, 404),
            ("PUT", "/health", self.auth(), {"ignored": "body"}, 405),
        )
        for method, path, headers, body, expected in cases:
            with self.subTest(method=method, path=path):
                status, response_headers, _ = self.request(
                    method, path, headers=headers, body=body
                )
                self.assertEqual(status, expected)
                self.assertEqual(response_headers["Connection"], "close")

    def test_successful_post_consumes_body_and_remains_reusable(self):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.port, timeout=2
        )
        self.addCleanup(connection.close)
        for operation in ("mint", "status"):
            body = json.dumps({"session_id": "session-reused"}).encode()
            connection.request(
                "POST",
                f"/v1/sessions/{operation}",
                body=body,
                headers={
                    **self.auth(),
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertNotEqual(response.getheader("Connection"), "close")
            self.assertEqual(payload["session_id"], "session-reused")

    def test_partial_request_body_is_cut_off_by_connection_timeout(self):
        self.server.close()
        self.server.request_timeout_seconds = 0.05
        self.server.start()
        connection = socket.create_connection(
            ("127.0.0.1", self.server.port), timeout=1
        )
        self.addCleanup(connection.close)
        connection.settimeout(0.3)
        request = (
            "POST /v1/sessions/mint HTTP/1.1\r\n"
            f"Host: 127.0.0.1\r\nAuthorization: Bearer {KEY}\r\n"
            "Content-Type: application/json\r\nContent-Length: 64\r\n\r\n{}"
        )
        connection.sendall(request.encode())
        try:
            response = connection.recv(4096)
        except (ConnectionResetError, BrokenPipeError):
            response = b""
        self.assertEqual(response, b"")

        self.server.status_provider = lambda _context: {
            "schema_version": 1,
            "state": "x" * 128,
            "policy_digest": DIGEST,
            "active_sessions": int("9" * 900),
        }
        status, _, body = self.request("GET", "/v1/status", headers=self.auth())
        self.assertEqual(status, 500)
        self.assertEqual(body, {"error": "RESPONSE_INVALID"})

    def test_request_timeout_is_absolute_across_slow_body_reads(self):
        self.server.close()
        self.server.request_timeout_seconds = 0.1
        self.server.start()
        connection = socket.create_connection(
            ("127.0.0.1", self.server.port), timeout=1
        )
        self.addCleanup(connection.close)
        connection.settimeout(0.4)
        request = (
            "POST /v1/sessions/mint HTTP/1.1\r\n"
            f"Host: 127.0.0.1\r\nAuthorization: Bearer {KEY}\r\n"
            "Content-Type: application/json\r\nContent-Length: 64\r\n\r\n"
        )
        started = time.monotonic()
        connection.sendall(request.encode())
        closed_at = None
        for _ in range(8):
            time.sleep(0.03)
            try:
                connection.sendall(b"x")
            except OSError:
                closed_at = time.monotonic()
                break
        try:
            response = connection.recv(4096)
        except (ConnectionResetError, BrokenPipeError):
            response = b""
        closed_at = closed_at or time.monotonic()
        self.assertLess(closed_at - started, 0.3)
        self.assertEqual(response, b"")

    def test_request_timeout_is_absolute_across_slow_header_reads(self):
        self.server.close()
        self.server.request_timeout_seconds = 0.1
        self.server.start()
        connection = socket.create_connection(
            ("127.0.0.1", self.server.port), timeout=1
        )
        self.addCleanup(connection.close)
        connection.settimeout(0.4)
        started = time.monotonic()
        connection.sendall(b"GET /health HTTP/1.1\r\nX-Slow: ")
        closed_at = None
        for _ in range(8):
            time.sleep(0.03)
            try:
                connection.sendall(b"x")
            except OSError:
                closed_at = time.monotonic()
                break
        try:
            response = connection.recv(4096)
        except (ConnectionResetError, BrokenPipeError):
            response = b""
        closed_at = closed_at or time.monotonic()
        self.assertLess(closed_at - started, 0.3)
        self.assertEqual(response, b"")

    def test_admission_is_bounded_to_configured_connection_limit(self):
        self.server.close()
        self.server.max_connections = 1
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def blocking_session(operation, request, context):
            entered.set()
            context.wait(release, 1)
            return {"session_id": request["session_id"], "state": "staged"}

        self.server.session_operator = blocking_session
        self.server.start()
        first_result = []

        def first_request():
            first_result.append(
                self.request(
                    "POST",
                    "/v1/sessions/mint",
                    headers=self.auth(),
                    body={"session_id": "session-1"},
                )
            )

        first = threading.Thread(target=first_request)
        first.start()
        self.addCleanup(first.join, 1)
        self.assertTrue(entered.wait(0.5))
        second = socket.create_connection(
            ("127.0.0.1", self.server.port), timeout=0.3
        )
        self.addCleanup(second.close)
        second.settimeout(0.3)
        second.sendall(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        try:
            rejected = second.recv(1)
        except ConnectionResetError:
            rejected = b""
        self.assertEqual(rejected, b"")
        release.set()
        first.join(1)
        self.assertFalse(first.is_alive())
        self.assertEqual(first_result[0][0], 200)

    def test_external_callbacks_are_timeout_and_capacity_bounded(self):
        self.server.request_timeout_seconds = 0.05
        self.server._callback_slots = threading.BoundedSemaphore(1)
        entered = threading.Event()
        release = threading.Event()
        completed = threading.Event()
        late_side_effect = threading.Event()
        self.addCleanup(release.set)

        def blocked_status(context):
            entered.set()
            try:
                context.wait(release, 1)
                late_side_effect.set()
                return {
                    "schema_version": 1,
                    "state": "inactive",
                    "policy_digest": DIGEST,
                    "active_sessions": 0,
                }
            finally:
                completed.set()

        self.server.status_provider = blocked_status
        started = time.monotonic()
        status, headers, body = self.request(
            "GET", "/v1/status", headers=self.auth()
        )
        self.assertTrue(entered.is_set())
        self.assertLess(time.monotonic() - started, 0.3)
        self.assertEqual(
            (status, body), (503, {"error": "PROVIDER_UNAVAILABLE"})
        )
        self.assertEqual(headers["Connection"], "close")

        self.assertTrue(completed.wait(0.5))
        release.set()
        time.sleep(0.02)
        self.assertFalse(late_side_effect.is_set())
        status, _, body = self.request("GET", "/health", headers=self.auth())
        self.assertEqual((status, body), (200, {"schema_version": 1, "status": "ok"}))

    def test_mutating_session_timeout_is_single_flight_and_acknowledged(self):
        self.server.request_timeout_seconds = 0.05
        entered = threading.Event()
        release = threading.Event()
        calls = []
        self.addCleanup(release.set)

        def blocked_mint(operation, request, context):
            calls.append((operation, dict(request)))
            entered.set()
            context.wait(release, 1)
            return {"session_id": request["session_id"], "state": "staged"}

        self.server.session_operator = blocked_mint
        first = self.request(
            "POST", "/v1/sessions/mint", headers=self.auth(),
            body={"session_id": "single-flight"},
        )
        self.assertTrue(entered.is_set())
        self.assertEqual(
            (first[0], first[2]),
            (202, {"session_id": "single-flight", "state": "pending"}),
        )
        second = self.request(
            "POST", "/v1/sessions/mint", headers=self.auth(),
            body={"session_id": "single-flight"},
        )
        self.assertEqual(second[0], 202)
        self.assertEqual(len(calls), 1)
        release.set()
        for _ in range(50):
            if self.server._session_operations[
                ("single-flight", "mint")
            ][0].done():
                break
            time.sleep(0.01)
        completed = self.request(
            "POST", "/v1/sessions/mint", headers=self.auth(),
            body={"session_id": "single-flight"},
        )
        self.assertEqual(
            (completed[0], completed[2]),
            (200, {"session_id": "single-flight", "state": "staged"}),
        )
        self.assertEqual(len(calls), 1)

    def test_mutations_are_serialized_across_operations_for_one_session(self):
        self.server.request_timeout_seconds = 0.05
        entered = threading.Event()
        release = threading.Event()
        calls = []
        self.addCleanup(release.set)

        def operation(name, request, context):
            calls.append(f"{name}-start")
            if name == "mint":
                entered.set()
                context.wait(release, 1)
            calls.append(f"{name}-end")
            return {
                "session_id": request["session_id"],
                "state": "staged" if name == "mint" else "closed",
            }

        self.server.session_operator = operation
        mint = self.request(
            "POST", "/v1/sessions/mint", headers=self.auth(),
            body={"session_id": "serialized"},
        )
        self.assertTrue(entered.is_set())
        self.assertEqual(mint[0], 202)
        close = self.request(
            "POST", "/v1/sessions/close", headers=self.auth(),
            body={"session_id": "serialized"},
        )
        self.assertEqual(
            (close[0], close[2]),
            (503, {"error": "PROVIDER_UNAVAILABLE"}),
        )
        self.assertEqual(calls, ["mint-start"])

        release.set()
        for _ in range(50):
            if self.server._session_operations[
                ("serialized", "mint")
            ][0].done():
                break
            time.sleep(0.01)
        completed_close = self.request(
            "POST", "/v1/sessions/close", headers=self.auth(),
            body={"session_id": "serialized"},
        )
        self.assertEqual(
            (completed_close[0], completed_close[2]["state"]),
            (200, "closed"),
        )
        self.assertEqual(
            calls,
            ["mint-start", "mint-end", "close-start", "close-end"],
        )

    def test_mutation_timeout_releases_separate_capacity_without_late_effect(self):
        self.server.request_timeout_seconds = 0.02
        self.server._session_operation_timeout_seconds = 0.06
        self.server._mutation_slots = threading.BoundedSemaphore(1)
        completed = threading.Event()
        late_effect = threading.Event()

        def blocked(_operation, request, context):
            try:
                context.wait(threading.Event(), 1)
                late_effect.set()
                return {"session_id": request["session_id"], "state": "staged"}
            finally:
                completed.set()

        self.server.session_operator = blocked
        first = self.request(
            "POST", "/v1/sessions/mint", headers=self.auth(),
            body={"session_id": "bounded-1"},
        )
        self.assertEqual(first[0], 202)
        health = self.request("GET", "/health", headers=self.auth())
        self.assertEqual(health[0], 200)
        saturated = self.request(
            "POST", "/v1/sessions/mint", headers=self.auth(),
            body={"session_id": "bounded-2"},
        )
        self.assertEqual(saturated[0], 503)
        self.assertTrue(completed.wait(0.5))
        self.assertFalse(late_effect.is_set())

        self.server.session_operator = lambda name, request, context: {
            "session_id": request["session_id"], "state": "staged",
        }
        recovered = self.request(
            "POST", "/v1/sessions/mint", headers=self.auth(),
            body={"session_id": "bounded-2"},
        )
        self.assertEqual(recovered[0], 200)

    def test_close_cancels_and_waits_for_cooperative_mutations(self):
        self.server.request_timeout_seconds = 0.02
        entered = threading.Event()
        completed = threading.Event()

        def operation(_name, request, context):
            entered.set()
            try:
                context.wait(threading.Event(), 10)
            finally:
                completed.set()
            return {"session_id": request["session_id"], "state": "staged"}

        self.server.session_operator = operation
        response = self.request(
            "POST", "/v1/sessions/mint", headers=self.auth(),
            body={"session_id": "shutdown-cooperative"},
        )
        self.assertEqual(response[0], 202)
        self.assertTrue(entered.is_set())

        self.server.close()

        self.assertTrue(completed.is_set())
        self.assertFalse(self.server._accepting_mutations)
        self.assertTrue(
            self.server._session_operations[
                ("shutdown-cooperative", "mint")
            ][0].done()
        )

    def test_close_reports_noncooperative_mutation_shutdown(self):
        self.server.request_timeout_seconds = 0.02
        self.server._shutdown_timeout_seconds = 0.05
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def operation(_name, request, _context):
            entered.set()
            release.wait(1)
            return {"session_id": request["session_id"], "state": "staged"}

        self.server.session_operator = operation
        response = self.request(
            "POST", "/v1/sessions/mint", headers=self.auth(),
            body={"session_id": "shutdown-incomplete"},
        )
        self.assertEqual(response[0], 202)
        self.assertTrue(entered.is_set())

        with self.assertRaisesRegex(
            ControlServerError, "SHUTDOWN_INCOMPLETE"
        ):
            self.server.close()
        self.assertFalse(self.server._accepting_mutations)
        with self.assertRaisesRegex(
            ControlServerError, "SHUTDOWN_INCOMPLETE"
        ):
            self.server.start()

        release.set()
        future = self.server._session_operations[
            ("shutdown-incomplete", "mint")
        ][0]
        future.result(timeout=1)
        self.server.close()

    def test_completed_outcomes_do_not_consume_active_mutation_capacity(self):
        self.server.request_timeout_seconds = 0.02
        self.server._max_session_operations = 1
        release = threading.Event()
        self.addCleanup(release.set)

        def operation(_name, request, context):
            if request["session_id"] == "pending":
                context.wait(release, 1)
            return {"session_id": request["session_id"], "state": "staged"}

        self.server.session_operator = operation
        self.assertEqual(self.request(
            "POST", "/v1/sessions/mint", headers=self.auth(),
            body={"session_id": "completed"},
        )[0], 200)
        self.assertEqual(self.request(
            "POST", "/v1/sessions/mint", headers=self.auth(),
            body={"session_id": "pending"},
        )[0], 202)
        rejected = self.request(
            "POST", "/v1/sessions/mint", headers=self.auth(),
            body={"session_id": "new"},
        )
        self.assertEqual(
            (rejected[0], rejected[2]),
            (503, {"error": "PROVIDER_UNAVAILABLE"}),
        )
        self.assertIn(("pending", "mint"), self.server._session_operations)
        self.assertIn(("completed", "mint"), self.server._session_operations)
        self.assertNotIn(("new", "mint"), self.server._session_operations)

    def test_completed_outcomes_are_bounded_without_evicting_idempotency_results(self):
        self.server._max_retained_session_outcomes = 64

        for index in range(64):
            status, _, body = self.request(
                "POST",
                "/v1/sessions/mint",
                headers=self.auth(),
                body={"session_id": f"completed-{index}"},
            )
            self.assertEqual(status, 200, body)
            self.assertEqual((status, body["state"]), (200, "staged"))

        overflow = self.request(
            "POST", "/v1/sessions/mint", headers=self.auth(),
            body={"session_id": "completed-overflow"},
        )
        self.assertEqual(
            (overflow[0], overflow[2]),
            (503, {"error": "PROVIDER_UNAVAILABLE"}),
        )
        self.assertEqual(len(self.server._session_operations), 64)
        self.assertTrue(
            all(
                future.done()
                for future, _started, _context
                in self.server._session_operations.values()
            )
        )

    def test_worker_start_failures_resolve_tracked_futures_before_removal(self):
        futures = []
        real_future = control_server_module.Future

        def make_future():
            future = real_future()
            futures.append(future)
            return future

        class FailingThread:
            def start(self):
                raise RuntimeError("thread start failed")

        handler = type(
            "Handler", (),
            {"_hindsight_request_deadline": time.monotonic() + 1},
        )()
        with (
            patch.object(
                control_server_module, "Future", side_effect=make_future
            ),
            patch.object(
                control_server_module.threading, "Thread",
                return_value=FailingThread(),
            ),
        ):
            with self.assertRaisesRegex(
                ControlServerError, "PROVIDER_UNAVAILABLE"
            ):
                self.server._invoke_session_operation(
                    handler, "mint", {"session_id": "start-failure"}
                )
            with self.assertRaisesRegex(
                ControlServerError, "PROVIDER_UNAVAILABLE"
            ):
                self.server._invoke_callback(
                    handler, lambda _context: None
                )
        self.assertEqual(len(futures), 2)
        self.assertTrue(all(future.done() for future in futures))
        self.assertTrue(
            all(isinstance(future.exception(), RuntimeError) for future in futures)
        )
        self.assertNotIn(
            ("start-failure", "mint"), self.server._session_operations
        )
        self.assertEqual(self.server._callback_operations, {})

    def test_failed_mutation_is_deduplicated_and_repropagated_until_ttl(self):
        calls = []

        def fail(operation, request, _context):
            calls.append((operation, request["session_id"]))
            raise RuntimeError("provider failed after mutation boundary")

        self.server.session_operator = fail
        for _ in range(2):
            status, _, body = self.request(
                "POST", "/v1/sessions/mint", headers=self.auth(),
                body={"session_id": "failed-dedupe"},
            )
            self.assertEqual((status, body), (500, {"error": "INTERNAL_ERROR"}))
        self.assertEqual(calls, [("mint", "failed-dedupe")])

        with self.server._session_operations_lock:
            future, _started, context = self.server._session_operations[
                ("failed-dedupe", "mint")
            ]
            self.server._session_operations[("failed-dedupe", "mint")] = (
                future,
                time.monotonic() - self.server._session_operation_ttl_seconds,
                context,
            )
        status, _, body = self.request(
            "POST", "/v1/sessions/mint", headers=self.auth(),
            body={"session_id": "failed-dedupe"},
        )
        self.assertEqual((status, body), (500, {"error": "INTERNAL_ERROR"}))
        self.assertEqual(
            calls,
            [("mint", "failed-dedupe"), ("mint", "failed-dedupe")],
        )

    def test_forbidden_material_provider_errors_use_control_error_response(self):
        def unavailable(_context):
            raise ControlServerError("PROVIDER_UNAVAILABLE")

        self.server.forbidden_material_resolver = unavailable
        status, _, body = self.request("GET", "/health", headers=self.auth())
        self.assertEqual(
            (status, body), (503, {"error": "PROVIDER_UNAVAILABLE"})
        )

    def test_start_failure_closes_listener_and_leaves_server_restartable(self):
        self.server.close()
        with patch.object(
            threading.Thread, "start", side_effect=RuntimeError("no thread")
        ):
            with self.assertRaisesRegex(ControlServerError, "START_FAILED"):
                self.server.start()
        self.assertIsNone(self.server._server)
        self.assertIsNone(self.server._thread)
        self.server.start()
        status, _, body = self.request("GET", "/health", headers=self.auth())
        self.assertEqual((status, body["status"]), (200, "ok"))

    def test_start_waits_for_concurrent_close_lifecycle_transition(self):
        current = self.server._server
        self.assertIsNotNone(current)
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        failures = []
        original_shutdown = current.shutdown

        def slow_shutdown():
            entered.set()
            if not release.wait(1):
                raise TimeoutError("close was not released")
            original_shutdown()

        def invoke(operation):
            try:
                operation()
            except Exception as error:
                failures.append(error)

        with patch.object(current, "shutdown", side_effect=slow_shutdown):
            closing = threading.Thread(target=invoke, args=(self.server.close,))
            starting = threading.Thread(target=invoke, args=(self.server.start,))
            closing.start()
            self.assertTrue(entered.wait(1))
            starting.start()
            time.sleep(0.02)
            self.assertTrue(starting.is_alive())
            release.set()
            closing.join(1)
            starting.join(1)

        self.assertFalse(closing.is_alive())
        self.assertFalse(starting.is_alive())
        self.assertEqual(failures, [])
        status, _, body = self.request("GET", "/health", headers=self.auth())
        self.assertEqual((status, body["status"]), (200, "ok"))

    def test_stale_handler_token_cannot_dispatch_after_restart(self):
        stale_token = self.server._lifecycle_token
        self.server.close()
        self.server.start()
        calls = []
        handler = type(
            "StaleHandler",
            (),
            {
                "_hindsight_lifecycle_token": stale_token,
                "_hindsight_request_deadline": time.monotonic() + 1,
            },
        )()

        with self.assertRaisesRegex(ControlServerError, "PROVIDER_UNAVAILABLE"):
            self.server._invoke_callback(
                handler, lambda _context: calls.append("called")
            )

        self.assertEqual(calls, [])

    def test_successful_response_cannot_reveal_control_or_data_plane_material(
        self,
    ):
        forbidden = [
            {"access_key": KEY},
            {"value": KEY},
            {"token": "data-plane-token"},
            {"signing_material": "private-signing-material"},
            {"signingMaterial": "private-signing-material"},
            {"nested": {"capability": "private-capability"}},
            {"nested": [{"authorization": "Bearer private"}]},
            {"accessToken": "private-access-token"},
            {"nested": {"refresh-token": "private-refresh-token"}},
            {"nested": [{"client_secret": "private-client-secret"}]},
            {"value": DATA_PLANE_TOKEN},
            {"nested": [{"value": SIGNING_MATERIAL}]},
        ]
        for value in forbidden:
            with self.subTest(value=value):
                self.server.status_provider = (
                    lambda _context, value=value: value
                )
                status, _, body = self.request(
                    "GET", "/v1/status", headers=self.auth()
                )
                self.assertEqual(status, 500)
                rendered = json.dumps(body)
                self.assertEqual(body, {"error": "RESPONSE_INVALID"})
                for secret in (
                    KEY,
                    "data-plane-token",
                    "private-signing-material",
                    "private-capability",
                    "Bearer private",
                    DATA_PLANE_TOKEN,
                    SIGNING_MATERIAL,
                ):
                    self.assertNotIn(secret, rendered)

        self.server.status_provider = lambda _context: {
            "schema_version": 1,
            "state": DATA_PLANE_TOKEN,
            "policy_digest": DIGEST,
            "active_sessions": 0,
        }
        before = self.material_resolutions
        status, _, body = self.request("GET", "/v1/status", headers=self.auth())
        self.assertEqual(status, 500)
        self.assertEqual(body, {"error": "RESPONSE_INVALID"})
        self.assertEqual(self.material_resolutions, before + 1)

    def test_response_materializes_nested_abstract_mappings(self):
        class AbstractMapping(Mapping):
            def __init__(self, value):
                self.value = value

            def __getitem__(self, key):
                return self.value[key]

            def __iter__(self):
                return iter(self.value)

            def __len__(self):
                return len(self.value)

        self.server._dispatch = lambda _handler, _method: (
            200,
            AbstractMapping({
                "schema_version": 1,
                "nested": [AbstractMapping({"value": "safe"})],
            }),
        )
        status, _, body = self.request(
            "GET", "/health", headers=self.auth()
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            body,
            {"schema_version": 1, "nested": [{"value": "safe"}]},
        )

    def test_response_materialization_rejects_excessive_depth_nodes_and_strings(self):
        deeply_nested = "safe"
        for _ in range(40):
            deeply_nested = [deeply_nested]
        responses = (
            {"nested": deeply_nested},
            {"nodes": [None] * 20_000},
            {"value": "x" * (self.server.max_response_bytes + 1)},
        )
        original_dispatch = self.server._dispatch
        try:
            for response in responses:
                with self.subTest(kind=next(iter(response))):
                    self.server._dispatch = (
                        lambda _handler, _method, response=response: (200, response)
                    )
                    status, _, body = self.request(
                        "GET", "/health", headers=self.auth()
                    )
                    self.assertEqual((status, body), (500, {"error": "RESPONSE_INVALID"}))
        finally:
            self.server._dispatch = original_dispatch

    def test_response_scans_decoded_string_keys_and_values_for_escaped_material(self):
        escaped_material = 'private"material'
        self.server.forbidden_material_resolver = (
            lambda _context: (escaped_material,)
        )
        for response in (
            {"safe": escaped_material},
            {escaped_material: "safe"},
            {"nested": [{"safe": escaped_material}]},
        ):
            with self.subTest(response=response):
                with patch.object(
                    self.server,
                    "_dispatch",
                    side_effect=lambda _handler, _method, response=response: (
                        200, response
                    ),
                ):
                    status, _, body = self.request(
                        "GET", "/health", headers=self.auth()
                    )
                self.assertEqual(status, 500)
                self.assertEqual(body, {"error": "RESPONSE_INVALID"})
                self.assertNotIn(escaped_material, json.dumps(body))

    def test_successful_overridden_dispatch_still_runs_secret_scanner(self):
        secret = "private-overridden-dispatch-material"
        self.server.forbidden_material_resolver = lambda _context: (secret,)
        with patch.object(
            self.server,
            "_dispatch",
            return_value=(200, {"schema_version": 1, "value": "safe"}),
        ) as dispatch:
            status, _, body = self.request(
                "GET", "/health", headers=self.auth()
            )
        self.assertEqual(status, 200)
        self.assertEqual(body, {"schema_version": 1, "value": "safe"})
        dispatch.assert_called_once()

        with patch.object(
            self.server,
            "_dispatch",
            return_value=(200, {"schema_version": 1, "value": secret}),
        ):
            status, _, body = self.request(
                "GET", "/health", headers=self.auth()
            )
        self.assertEqual((status, body), (500, {"error": "RESPONSE_INVALID"}))

    def test_session_input_schema_is_closed_before_operator_dispatch(self):
        invalid = [
            {},
            {"session_id": "session-1", "token": "private"},
            {"session_id": "bad space"},
            {"session_id": 7},
        ]
        for body in invalid:
            status, _, result = self.request(
                "POST", "/v1/sessions/mint", headers=self.auth(), body=body
            )
            self.assertEqual(status, 400)
            self.assertEqual(result, {"error": "SCHEMA_INVALID"})
        self.assertEqual(self.session_calls, [])

    def test_session_input_rejects_duplicate_json_keys(self):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.port, timeout=2
        )
        self.addCleanup(connection.close)
        body = b'{"session_id":"first","session_id":"second"}'
        connection.request(
            "POST",
            "/v1/sessions/mint",
            body=body,
            headers={
                **self.auth(),
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )
        response = connection.getresponse()
        result = json.loads(response.read())
        self.assertEqual(response.status, 400)
        self.assertEqual(result, {"error": "SCHEMA_INVALID"})
        self.assertEqual(self.session_calls, [])


if __name__ == "__main__":
    unittest.main()
