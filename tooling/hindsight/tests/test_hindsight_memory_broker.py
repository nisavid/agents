import base64
from concurrent.futures import Future
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import runpy
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import call, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
sys.path.insert(0, str(LIB))

from hindsight_memory_control_plane.adapters import AdapterError, FakeAdapter
from hindsight_memory_control_plane.broker import (
    append_record_once,
    Broker,
    BrokerError,
    MIN_PAYLOAD_BYTES,
    MAX_REQUEST_SEQUENCE,
    MAX_SESSION_ACTION_IDS,
    MAX_PAYLOAD_BYTES,
)
from hindsight_memory_control_plane.canonical import canonical_bytes
from hindsight_memory_control_plane.ledger import LedgerError
from hindsight_memory_control_plane.server import JsonRpcClient, UnixJsonRpcServer


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
BANK = {"profile_id": "core", "bank_id": "engineering"}
ENDPOINT = {"profile_id": "core", "scheme": "http", "host": "127.0.0.1", "port": 7979, "tenant": "default"}
METHODS = ["recall", "mental_model_fetch", "transcript_checkpoint", "retain_outcome", "reflect", "session_status", "session_close"]
RESPONSE_KEYS = {"schema_version", "action_id", "action_digest", "policy_digest", "artifact_digest", "disposition", "payload", "diagnostic"}


def authority_claims(**changes):
    value = {
        "session_id": "session-1", "harness_id": "codex", "home_bank": BANK,
        "trust_class": "local", "companion_id": "gui-1",
        "policy_digest": DIGEST_A, "artifact_digest": DIGEST_B,
        "methods": METHODS, "route": "local-core",
    }
    value.update(changes)
    return value


def claims(**changes):
    value = {
        "session_id": "session-1",
        "companion_id": "gui-1",
        "route": "local-core",
    }
    value.update(changes)
    return value


def authorize_mint(control, requested, ttl):
    if (
        control != "control"
        or requested.get("route") != "local-core"
        or ttl > 60
    ):
        return {}
    methods = ["recall"] if requested["session_id"] == "limited" else METHODS
    return authority_claims(**requested, methods=methods)


class BrokerSocketTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        self.socket_path = self.state / "broker.sock"
        self.adapter = FakeAdapter(endpoint=ENDPOINT)
        self.start(self.adapter)
        controller = runpy.run_path(str(ROOT / "bin/hindsight-memory"))
        start_time = controller["_process_start_time"](os.getpid())
        self.assertIsNotNone(start_time)
        (self.state / "broker.pid").write_text(
            json.dumps({"pid": os.getpid(), "start_time": start_time}),
            encoding="ascii",
        )
        os.chmod(self.state / "broker.pid", 0o600)

    def start(self, adapter, *, clock=time.time):
        self.broker = Broker(
            state_dir=self.state, signing_key=b"k" * 32,
            routes={"local-core": {"bank": BANK, "adapter": adapter}},
            policy_digest=DIGEST_A, artifact_digest=DIGEST_B,
            mint_authorizer=authorize_mint,
            clock=clock,
            max_payload_bytes=4096,
        )
        self.server = UnixJsonRpcServer(self.socket_path, self.broker)
        self.server.start()
        self.client = JsonRpcClient(self.socket_path)

    def stop(self):
        self.server.close()
        self.broker.shutdown()

    def tearDown(self):
        self.stop()
        self.temporary.cleanup()

    def exchange(self, **changes):
        minted = self.client.session_mint("control", claims(**changes), ttl_seconds=30)
        self.assert_response(minted, "session-mint")
        exchanged = self.client.session_exchange(minted["payload"]["handle"])
        self.assert_response(exchanged, "session-exchange")
        return exchanged["payload"]["capability"]

    def assert_response(self, response, action_id):
        self.assertEqual(set(response), RESPONSE_KEYS)
        self.assertEqual(response["schema_version"], 1)
        self.assertEqual(response["action_id"], action_id)
        self.assertRegex(response["action_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(response["policy_digest"], DIGEST_A)
        self.assertEqual(response["artifact_digest"], DIGEST_B)

    def run_cli(self, *arguments, env=None):
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "bin/hindsight-memory"),
                "--state-dir",
                str(self.state),
                *map(str, arguments),
            ],
            cwd=ROOT,
            env={**os.environ, **(env or {})},
            text=True,
            capture_output=True,
            timeout=30,
        )

    def test_stable_cli_clients_use_files_and_environment_for_private_inputs(self):
        claims_path = self.root / "mint-request.json"
        request_path = self.root / "request.json"
        claims_path.write_text(json.dumps(claims()), encoding="utf-8")
        request_path.write_text(json.dumps({"query": "deployment", "limit": 3}), encoding="utf-8")
        os.chmod(claims_path, 0o600)
        os.chmod(request_path, 0o600)

        minted = self.run_cli(
            "session", "mint", "--socket", self.socket_path,
            "--request", claims_path, "--ttl-seconds", "30",
            env={"HINDSIGHT_MEMORY_CONTROL_CAPABILITY": "control"},
        )
        self.assertEqual(minted.returncode, 0, minted.stderr)
        handle = json.loads(minted.stdout)["payload"]["handle"]
        exchanged = self.run_cli(
            "session", "exchange", "--socket", self.socket_path,
            env={"HINDSIGHT_MEMORY_SESSION_HANDLE": handle},
        )
        self.assertEqual(exchanged.returncode, 0, exchanged.stderr)
        capability = json.loads(exchanged.stdout)["payload"]["capability"]

        recalled = self.run_cli(
            "recall", "--socket", self.socket_path, "--sequence", "1",
            "--action-id", "cli-recall", "--request", request_path,
            env={"HINDSIGHT_MEMORY_SESSION_CAPABILITY": capability},
        )
        self.assertEqual(recalled.returncode, 0, recalled.stderr)
        self.assertEqual(json.loads(recalled.stdout)["action_id"], "cli-recall")
        status = self.run_cli(
            "session_status", "--socket", self.socket_path, "--sequence", "2",
            "--action-id", "cli-status",
            env={"HINDSIGHT_MEMORY_SESSION_CAPABILITY": capability},
        )
        self.assertEqual(status.returncode, 0, status.stderr)
        closed = self.run_cli(
            "session", "close", "--socket", self.socket_path, "--sequence", "3",
            "--action-id", "cli-close",
            env={"HINDSIGHT_MEMORY_SESSION_CAPABILITY": capability},
        )
        self.assertEqual(closed.returncode, 0, closed.stderr)

        for result in (minted, exchanged, recalled, status, closed):
            self.assertFalse(any("control" in str(arg) for arg in result.args))
            self.assertFalse(any(handle in str(arg) for arg in result.args))
            self.assertFalse(any(capability in str(arg) for arg in result.args))

    def test_cli_clients_fail_closed_when_private_environment_is_missing(self):
        claims_path = self.root / "mint-request.json"
        claims_path.write_text(json.dumps(claims()), encoding="utf-8")
        result = self.run_cli(
            "session", "mint", "--socket", self.socket_path,
            "--request", claims_path,
            env={"HINDSIGHT_MEMORY_CONTROL_CAPABILITY": ""},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONTROL_CAPABILITY_UNAVAILABLE", result.stderr)

    def test_server_refuses_to_replace_an_existing_socket_path(self):
        self.stop()
        self.socket_path.write_text("preserve", encoding="utf-8")
        replacement = UnixJsonRpcServer(self.socket_path, self.broker)
        with self.assertRaises(OSError):
            replacement.start()
        self.assertEqual(self.socket_path.read_text(encoding="utf-8"), "preserve")
        self.server = replacement

    def test_server_identity_verifies_and_rebinds_owned_stale_socket(self):
        self.stop()
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(self.socket_path))
        self.socket_path.chmod(0o600)
        stale.close()

        replacement = UnixJsonRpcServer(self.socket_path, self.broker)
        replacement.start()
        self.assertTrue(self.socket_path.is_socket())
        self.assertEqual(stat.S_IMODE(self.socket_path.stat().st_mode), 0o600)
        self.server = replacement

    def test_server_socket_lifecycle_uses_cross_process_advisory_lock(self):
        self.stop()
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(self.socket_path))
        self.socket_path.chmod(0o600)
        stale.close()

        original_flock = fcntl.flock
        with patch(
            "hindsight_memory_control_plane.server.fcntl.flock",
            wraps=original_flock,
        ) as flock:
            replacement = UnixJsonRpcServer(self.socket_path, self.broker)
            replacement.start()
        operations = [item.args[1] for item in flock.call_args_list]
        self.assertIn(fcntl.LOCK_EX, operations)
        self.assertIn(fcntl.LOCK_UN, operations)
        lock_files = list(
            self.socket_path.parent.glob(".hindsight-socket-lifecycle-*.lock")
        )
        self.assertEqual(len(lock_files), 1)
        self.assertEqual(stat.S_IMODE(lock_files[0].stat().st_mode), 0o600)
        self.server = replacement

    def test_socket_bind_and_client_reject_untrusted_parent_paths(self):
        linked_parent = self.root / "linked-socket-parent"
        linked_parent.symlink_to(self.root, target_is_directory=True)
        linked_client = JsonRpcClient(linked_parent / self.socket_path.name)
        with self.assertRaises(OSError):
            linked_client.session_mint("control", claims())

        self.stop()
        unsafe_parent = self.root / "unsafe-socket-parent"
        unsafe_parent.mkdir(mode=0o700)
        unsafe_parent.chmod(0o777)
        replacement = UnixJsonRpcServer(
            unsafe_parent / "broker.sock", self.broker
        )
        with self.assertRaises(OSError):
            replacement.start()
        self.server = replacement

    def test_server_close_preserves_a_replacement_path(self):
        self.socket_path.unlink()
        self.socket_path.write_text("replacement", encoding="utf-8")
        self.server.close()
        self.assertEqual(self.socket_path.read_text(encoding="utf-8"), "replacement")

    def test_server_start_failure_removes_its_bound_socket_path(self):
        self.stop()
        replacement = UnixJsonRpcServer(self.socket_path, self.broker)
        with patch(
            "hindsight_memory_control_plane.server.os.chmod",
            side_effect=OSError("chmod failed"),
        ):
            with self.assertRaisesRegex(OSError, "chmod failed"):
                replacement.start()
        self.assertFalse(self.socket_path.exists())
        self.server = replacement

    def test_server_parent_identity_failure_removes_bound_socket_and_restarts(self):
        self.stop()
        replacement = UnixJsonRpcServer(self.socket_path, self.broker)
        with patch(
            "hindsight_memory_control_plane.server._socket_parent_matches",
            side_effect=(True, False),
        ):
            with self.assertRaisesRegex(OSError, "parent identity changed"):
                replacement.start()
        self.assertFalse(self.socket_path.exists())

        replacement.start()
        self.assertTrue(self.socket_path.is_socket())
        self.server = replacement

    def test_server_restores_restrictive_bind_umask_after_bind_failure(self):
        self.stop()
        self.socket_path.write_text("preserve", encoding="utf-8")
        replacement = UnixJsonRpcServer(self.socket_path, self.broker)
        with patch(
            "hindsight_memory_control_plane.server.os.umask",
            side_effect=(0o022, 0o177),
        ) as umask:
            with self.assertRaises(OSError):
                replacement.start()
        self.assertEqual(
            umask.call_args_list,
            [call(0o177), call(0o022)],
        )
        self.server = replacement

    def test_server_start_is_rollback_safe_and_restartable(self):
        self.stop()
        replacement = UnixJsonRpcServer(self.socket_path, self.broker)
        with patch(
            "hindsight_memory_control_plane.server.threading.Thread.start",
            side_effect=RuntimeError("thread start failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "thread start failed"):
                replacement.start()
        self.assertFalse(self.socket_path.exists())
        self.assertIsNone(replacement._socket)
        self.assertIsNone(replacement._thread)
        self.assertIsNone(replacement._bound_identity)

        replacement.start()
        replacement.close()
        self.assertFalse(self.socket_path.exists())
        replacement.start()
        self.assertTrue(self.socket_path.is_socket())
        self.server = replacement

    def test_server_double_start_preserves_the_live_listener(self):
        identity = self.server._bound_identity
        with self.assertRaisesRegex(RuntimeError, "already started"):
            self.server.start()
        self.assertEqual(self.server._bound_identity, identity)
        self.assertTrue(self.socket_path.is_socket())
        self.assertIsNotNone(self.server._thread)
        self.assertTrue(self.server._thread.is_alive())

    def test_server_rejects_connections_immediately_at_max_connections(self):
        self.stop()
        self.broker = Broker(
            state_dir=self.state, signing_key=b"k" * 32,
            routes={"local-core": {"bank": BANK, "adapter": self.adapter}},
            policy_digest=DIGEST_A, artifact_digest=DIGEST_B,
            mint_authorizer=authorize_mint,
        )
        self.server = UnixJsonRpcServer(
            self.socket_path, self.broker, max_connections=1,
            connection_timeout_seconds=0.2,
        )
        self.server.start()
        admitted = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        admissions = []
        original_connection = self.server._connection

        def observed_connection(*args, **kwargs):
            admissions.append(args)
            admitted.set()
            release.wait(1)
            return original_connection(*args, **kwargs)

        self.server._connection = observed_connection
        first = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        second = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        first.connect(str(self.socket_path))
        self.assertTrue(admitted.wait(0.2))
        second.settimeout(0.3)
        second.connect(str(self.socket_path))
        self.assertEqual(second.recv(1), b"")
        self.assertEqual(len(admissions), 1)
        release.set()

    def test_server_registers_submitted_connection_before_lock_observers_run(self):
        admitted, peer = socket.socketpair()
        self.addCleanup(peer.close)
        observed = threading.Event()
        attempted = threading.Event()
        observer_threads = []

        class Listener:
            def accept(_self):
                return admitted, None

        class Executor:
            def submit(_self, operation, connection, slots):
                del operation

                def observe():
                    attempted.set()
                    with self.server._connection_futures_lock:
                        observed.set()

                observer = threading.Thread(target=observe)
                observer.start()
                observer_threads.append(observer)
                self.assertTrue(attempted.wait(1))
                self.assertFalse(observed.is_set())
                connection.close()
                slots.release()
                future = Future()
                future.set_result(())
                self.server._closing.set()
                return future

        slots = threading.BoundedSemaphore(1)
        self.server._closing.clear()
        self.server._serve(Listener(), Executor(), slots)
        for observer in observer_threads:
            observer.join(1)
            self.assertFalse(observer.is_alive())
        self.assertTrue(observed.is_set())
        with self.server._connection_futures_lock:
            self.assertEqual(self.server._connection_futures, set())
        self.server._closing.clear()

    def test_completed_connection_callback_runs_outside_registration_lock(self):
        admitted, peer = socket.socketpair()
        self.addCleanup(peer.close)
        callback_observed_unlocked = []
        observer_threads = []

        class Listener:
            def accept(_self):
                return admitted, None

        class Executor:
            def submit(_self, operation, connection, slots):
                del operation
                connection.close()
                slots.release()
                future = Future()
                future.set_result(())
                self.server._closing.set()
                return future

        def observe_registration_lock():
            acquired = threading.Event()

            def observe():
                with self.server._connection_futures_lock:
                    acquired.set()

            observer = threading.Thread(target=observe)
            observer.start()
            observer_threads.append(observer)
            callback_observed_unlocked.append(acquired.wait(0.2))

        slots = threading.BoundedSemaphore(1)
        self.server._closing.clear()
        with patch.object(
            self.server,
            "_release_drained_executor",
            side_effect=observe_registration_lock,
        ):
            self.server._serve(Listener(), Executor(), slots)
        for observer in observer_threads:
            observer.join(1)
            self.assertFalse(observer.is_alive())
        self.assertEqual(callback_observed_unlocked, [True])
        with self.server._connection_futures_lock:
            self.assertEqual(self.server._connection_futures, set())
        self.server._closing.clear()

    def test_server_times_out_an_admitted_idle_connection(self):
        self.stop()
        self.broker = Broker(
            state_dir=self.state, signing_key=b"k" * 32,
            routes={"local-core": {"bank": BANK, "adapter": self.adapter}},
            policy_digest=DIGEST_A, artifact_digest=DIGEST_B,
            mint_authorizer=authorize_mint,
        )
        self.server = UnixJsonRpcServer(
            self.socket_path, self.broker, max_connections=1,
            connection_timeout_seconds=0.05,
        )
        self.server.start()
        first = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(first.close)
        first.settimeout(0.3)
        first.connect(str(self.socket_path))
        self.assertEqual(first.recv(1), b"")
        response = self.raw_rpc("unknown-method", {})
        self.assertIn("error", response)

    def test_server_close_fails_bounded_while_operation_is_active_before_restart(self):
        self.server.close()
        self.server = UnixJsonRpcServer(
            self.socket_path,
            self.broker,
            close_timeout_seconds=0.02,
        )
        self.server.start()
        self.client = JsonRpcClient(self.socket_path)
        entered = threading.Event()
        release = threading.Event()
        results = []

        def blocking_mint(**_params):
            entered.set()
            release.wait(1)
            return {"finished": True}

        with patch.object(self.broker, "session_mint", blocking_mint):
            request = threading.Thread(
                target=lambda: results.append(
                    self.client.session_mint("control", claims(), ttl_seconds=30)
                )
            )
            request.start()
            self.assertTrue(entered.wait(0.2))
            with self.assertRaisesRegex(BrokerError, "SERVER_CLOSE_INCOMPLETE"):
                self.server.close()
            self.assertFalse(self.socket_path.exists())
            self.assertIsNone(self.server._socket)
            self.assertIsNone(self.server._thread)
            self.assertIsNotNone(self.server._executor)
            self.assertIsNone(self.server._bound_identity)
            with self.assertRaisesRegex(RuntimeError, "already started"):
                self.server.start()
            release.set()
            request.join(1)
            self.assertFalse(request.is_alive())
        self.assertEqual(results, [{"finished": True}])
        deadline = time.monotonic() + 1
        while self.server._executor is not None and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertIsNone(self.server._executor)
        self.server.start()
        self.assertTrue(self.socket_path.is_socket())

    def test_server_close_bounds_listener_join_and_reports_live_listener(self):
        self.server.close()

        class Listener:
            def close(_self):
                return None

        class Thread:
            def __init__(_self):
                _self.join_timeout = None

            def join(_self, timeout=None):
                _self.join_timeout = timeout

            def is_alive(_self):
                return True

        listener = Listener()
        listener_thread = Thread()
        self.server.close_timeout_seconds = 0.02
        self.server._socket = listener
        self.server._thread = listener_thread
        with self.assertRaisesRegex(BrokerError, "SERVER_CLOSE_INCOMPLETE"):
            self.server.close()
        self.assertIsNotNone(listener_thread.join_timeout)
        self.assertLessEqual(listener_thread.join_timeout, 0.02)

    def test_private_shutdown_rpc_invokes_only_the_configured_callback(self):
        self.server.close()
        requested = threading.Event()
        callback_saw_drained_future = []

        def request_shutdown():
            with self.server._connection_futures_lock:
                callback_saw_drained_future.append(
                    not self.server._connection_futures
                )
            requested.set()

        self.server = UnixJsonRpcServer(
            self.socket_path,
            self.broker,
            shutdown_callback=request_shutdown,
            shutdown_capability="s" * 32,
        )
        self.server.start()
        self.client = JsonRpcClient(self.socket_path)
        with self.assertRaisesRegex(BrokerError, "METHOD_DENIED"):
            self.client.broker_shutdown("x" * 32)
        self.assertFalse(requested.is_set())
        self.assertEqual(
            self.client.broker_shutdown("s" * 32), {"stopping": True}
        )
        self.assertTrue(requested.wait(0.2))
        self.assertEqual(callback_saw_drained_future, [True])

    def test_server_bounds_serialized_response_before_writing(self):
        self.server.close()
        self.server = UnixJsonRpcServer(
            self.socket_path, self.broker, max_response_bytes=1024
        )
        self.server.start()
        self.client = JsonRpcClient(self.socket_path)
        with patch.object(
            self.broker,
            "session_mint",
            return_value={"oversized": "x" * 4096},
        ):
            with self.assertRaisesRegex(BrokerError, "RESPONSE_TOO_LARGE"):
                self.client.session_mint("control", claims(), ttl_seconds=30)

    def test_rpc_server_and_client_reject_ambiguous_json(self):
        duplicate_request = (
            b'{"jsonrpc":"2.0","id":1,"id":2,'
            b'"method":"session_exchange","params":{"handle":"x"}}'
        )
        self.assertEqual(
            self.server.dispatch(duplicate_request)["error"]["message"],
            "PARSE_ERROR",
        )

        response_id = self.client._next_id + 1
        duplicate_response = (
            f'{{"jsonrpc":"2.0","id":{response_id},'
            '"result":{},"result":{}}\n'
        ).encode("utf-8")
        with patch.object(
            self.server,
            "_serialize_response",
            return_value=duplicate_response,
        ), self.assertRaisesRegex(BrokerError, "RESPONSE_INVALID"):
            self.client.session_exchange("opaque-handle")

    def test_rpc_serialization_never_emits_non_finite_json_numbers(self):
        encoded = self.server._serialize_response(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"score": float("nan")},
            }
        )

        self.assertNotIn(b"NaN", encoded)
        self.assertEqual(
            json.loads(encoded)["error"]["message"], "RESPONSE_INVALID"
        )

    def test_server_and_client_validate_transport_bounds(self):
        for invalid in (0, True, 1024 * 1024 + 1):
            with self.subTest(max_request_bytes=invalid), self.assertRaises(
                ValueError
            ):
                UnixJsonRpcServer(
                    self.root / "invalid.sock",
                    self.broker,
                    max_request_bytes=invalid,
                )
            with self.subTest(client_max_request_bytes=invalid), self.assertRaises(
                ValueError
            ):
                JsonRpcClient(self.socket_path, max_request_bytes=invalid)
        for invalid in (0, float("nan"), float("inf"), 31):
            with self.subTest(timeout=invalid), self.assertRaises(ValueError):
                JsonRpcClient(self.socket_path, timeout_seconds=invalid)

        bounded = JsonRpcClient(self.socket_path, max_response_bytes=1024)
        with patch.object(
            self.broker,
            "session_mint",
            return_value={"oversized": "x" * 2048},
        ):
            with self.assertRaisesRegex(BrokerError, "RESPONSE_INVALID"):
                bounded.session_mint("control", claims(), ttl_seconds=30)

        request_bounded = JsonRpcClient(self.socket_path, max_request_bytes=32)
        with self.assertRaisesRegex(BrokerError, "REQUEST_TOO_LARGE"):
            request_bounded.session_mint("control", claims(), ttl_seconds=30)

    def test_server_listener_backlog_matches_max_connections(self):
        listener = Mock()
        server = UnixJsonRpcServer(
            self.root / "backlog.sock", self.broker, max_connections=7
        )
        server._listen(listener)
        listener.listen.assert_called_once_with(7)

    def test_cancelled_connection_work_releases_admission_and_socket(self):
        admitted, peer = socket.socketpair()
        self.addCleanup(peer.close)
        slots = threading.BoundedSemaphore(1)
        self.assertTrue(slots.acquire(blocking=False))
        future = Future()
        self.assertTrue(future.cancel())
        self.server._cancel_admission(future, admitted, slots)
        self.assertTrue(slots.acquire(blocking=False))
        self.assertFalse(slots.acquire(blocking=False))
        slots.release()
        peer.settimeout(0.2)
        self.assertEqual(peer.recv(1), b"")

    def test_checkpoint_keys_are_unambiguous_and_locks_are_bounded(self):
        first = {
            "session_id": "session-1",
            "home_bank": {"profile_id": "a/b", "bank_id": "c"},
        }
        second = {
            "session_id": "session-1",
            "home_bank": {"profile_id": "a", "bank_id": "b/c"},
        }
        first_key = self.broker._checkpoint_state_key(
            "transcript_checkpoint", first, "d"
        )
        second_key = self.broker._checkpoint_state_key(
            "transcript_checkpoint", second, "d"
        )
        self.assertNotEqual(first_key, second_key)
        self.assertNotEqual(
            self.broker._checkpoint_state_key(
                "transcript_checkpoint", first, "d", epoch=1
            ),
            self.broker._checkpoint_state_key(
                "transcript_checkpoint", first, "d", epoch=2
            ),
        )
        for index in range(10_000):
            self.broker._document_lock(
                authority_claims(), f"document/{index}"
            )
            self.broker._work_lock(f"state/{index}")
        self.assertEqual(len(self.broker._document_locks), 64)
        self.assertEqual(len(self.broker._work_locks), 64)

    def test_shared_client_assigns_and_checks_request_ids_atomically(self):
        barrier = threading.Barrier(2)
        results = []
        failures = []

        def delayed_mint(*, control_capability, request, ttl_seconds):
            self.assertEqual(control_capability, "control")
            self.assertEqual(ttl_seconds, 30)
            barrier.wait(timeout=1)
            return {"session_id": request["session_id"]}

        def invoke(session_id):
            try:
                results.append(
                    self.client.session_mint(
                        "control", claims(session_id=session_id), ttl_seconds=30
                    )
                )
            except Exception as error:
                failures.append(error)

        with patch.object(self.broker, "session_mint", side_effect=delayed_mint):
            threads = [
                threading.Thread(target=invoke, args=(f"session-{index}",))
                for index in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(
            {result["session_id"] for result in results},
            {"session-0", "session-1"},
        )
        self.assertEqual(self.client._next_id, 2)

    def raw_rpc(self, method, params):
        request = {"jsonrpc": "2.0", "id": 91, "method": method, "params": params}
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        with connection:
            connection.settimeout(1)
            connection.connect(str(self.socket_path))
            connection.sendall(json.dumps(request).encode() + b"\n")
            return json.loads(connection.makefile("rb").readline())

    def test_all_typed_clients_use_private_socket_and_real_adapter(self):
        capability = self.exchange()
        recall = self.client.recall(capability, sequence=1, action_id="recall-1", request={"query": "q", "limit": 2})
        model = self.client.mental_model_fetch(capability, sequence=2, action_id="model-1", request={"model_id": "model1"})
        checkpoint = self.client.transcript_checkpoint(
            capability, sequence=3, action_id="checkpoint-1",
            request={
                "document_id": "doc", "epoch": 1, "checkpoint": 1,
                "content": "complete cleaned transcript",
            },
        )
        retain = self.client.retain_outcome(capability, sequence=4, action_id="retain-1", request={"document_id": "doc", "epoch": 1, "checkpoint": 1, "outcome": "done"})
        reflect = self.client.reflect(capability, sequence=5, action_id="reflect-1", request={"reflection": "note"})
        status = self.client.session_status(capability, sequence=6, action_id="status-1")
        closed = self.client.session_close(capability, sequence=7, action_id="close-1", timeout_seconds=1)
        for response, action in ((recall, "recall-1"), (model, "model-1"), (checkpoint, "checkpoint-1"),
                                 (retain, "retain-1"), (reflect, "reflect-1"), (status, "status-1"), (closed, "close-1")):
            self.assert_response(response, action)
        self.assertEqual(os.stat(self.socket_path).st_mode & 0o777, 0o600)
        called = {entry["method"] for entry in self.adapter.calls}
        self.assertTrue({"recall", "mental_model_fetch", "transcript_checkpoint", "retain_outcome", "reflect", "session_status"} <= called)

    def test_action_digest_is_canonical_and_capability_bound(self):
        capability = self.exchange()
        response = self.client.recall(capability, sequence=1, action_id="recall-digest", request={"query": "q"})
        body = json.loads(base64.urlsafe_b64decode(capability.split(".")[0] + "=="))
        expected = hashlib.sha256(canonical_bytes({
            "action_id": "recall-digest", "method": "recall", "sequence": 1,
            "session_id": "session-1", "harness_id": "codex",
            "capability_nonce_digest": hashlib.sha256(body["nonce"].encode()).hexdigest(),
        })).hexdigest()
        self.assertEqual(response["action_digest"], expected)

    def test_socket_rejects_unknown_nested_routing_and_auth_before_adapter(self):
        capability = self.exchange()
        forbidden = ("destination", "bank", "bank_id", "endpoint", "url", "authorization", "bearer", "credential", "token")
        for sequence, key in enumerate(forbidden, 1):
            before = len(self.adapter.calls)
            response = self.raw_rpc("recall", {
                "capability": capability, "sequence": sequence, "action_id": f"bad-{sequence}",
                "request": {"query": {"text": "q", key: "private"}},
            })
            self.assertEqual(response["error"]["message"], "SCHEMA_INVALID")
            self.assertNotIn("private", json.dumps(response))
            self.assertEqual(len(self.adapter.calls), before)
        alias = self.raw_rpc("checkpoint", {})
        self.assertEqual(alias["error"]["message"], "METHOD_DENIED")

    def test_invalid_ttl_and_timeout_are_rejected_before_session_or_adapter_state(self):
        for ttl_seconds in (float("nan"), 0, -1):
            invalid_mint = self.raw_rpc("session_mint", {
                "control_capability": "control",
                "request": claims(),
                "ttl_seconds": ttl_seconds,
            })
            expected = (
                "PARSE_ERROR"
                if isinstance(ttl_seconds, float)
                and ttl_seconds != ttl_seconds
                else "SCHEMA_INVALID"
            )
            self.assertEqual(invalid_mint["error"]["message"], expected)
        capability = self.exchange()
        invalid_read = self.raw_rpc("recall", {
            "capability": capability, "sequence": 1, "action_id": "timeout-action",
            "request": {"query": "q"}, "timeout_seconds": "invalid",
        })
        self.assertEqual(invalid_read["error"]["message"], "SCHEMA_INVALID")
        valid = self.client.recall(capability, sequence=1, action_id="timeout-action", request={"query": "q"})
        self.assertEqual(valid["disposition"], "ok")

    def test_mint_requires_control_capability_and_internal_authority(self):
        denied = [("wrong", claims(), "MINT_DENIED")]
        for key, value in (
            ("home_bank", BANK),
            ("methods", ["recall"]),
            ("policy_digest", DIGEST_A),
            ("artifact_digest", DIGEST_B),
            ("harness_id", "codex"),
            ("trust_class", "local"),
        ):
            denied.append(
                ("control", {**claims(), key: value}, "SCHEMA_INVALID")
            )
        for control, requested, expected in denied:
            response = self.raw_rpc("session_mint", {
                "control_capability": control,
                "request": requested,
                "ttl_seconds": 30,
            })
            self.assertEqual(response["error"]["message"], expected)

    def test_json_rpc_ids_are_scalar_and_non_boolean(self):
        for identifier in (True, 1.5, [], {}):
            request = {"jsonrpc": "2.0", "id": identifier, "method": "session_mint", "params": {}}
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            with connection:
                connection.settimeout(1)
                connection.connect(str(self.socket_path))
                connection.sendall(json.dumps(request).encode() + b"\n")
                response = json.loads(connection.makefile("rb").readline())
            self.assertEqual(response["error"]["message"], "SCHEMA_INVALID")

    def test_replay_expiry_revocation_sequence_digest_method_and_route_survive_restart(self):
        capability = self.exchange()
        self.client.recall(capability, sequence=1, action_id="once", request={"query": "q"})
        with self.assertRaisesRegex(BrokerError, "ACTION_REPLAY"):
            self.client.recall(capability, sequence=2, action_id="once", request={"query": "q"})
        with self.assertRaisesRegex(BrokerError, "SEQUENCE_ROLLBACK"):
            self.client.recall(capability, sequence=1, action_id="older", request={"query": "q"})
        limited = self.exchange(session_id="limited")
        with self.assertRaisesRegex(BrokerError, "METHOD_DENIED"):
            self.client.reflect(limited, sequence=1, action_id="denied", request={"reflection": "x"})
        wrong = self.exchange(session_id="wrong")
        saved_route = self.broker.routes.pop("local-core")
        with self.assertRaisesRegex(BrokerError, "ROUTE_DENIED"):
            self.client.recall(wrong, sequence=1, action_id="route", request={"query": "q"})
        self.broker.routes["local-core"] = saved_route
        now = [time.time()]
        self.broker.clock = lambda: now[0]
        expired = self.client.session_mint("control", claims(session_id="expired"), ttl_seconds=1)
        now[0] += 2
        with self.assertRaisesRegex(BrokerError, "EXPIRED"):
            self.client.session_exchange(expired["payload"]["handle"])
        self.broker.clock = time.time
        closed = self.exchange(session_id="closed")
        self.client.session_close(closed, sequence=1, action_id="close", timeout_seconds=1)
        self.stop()
        self.start(self.adapter)
        with self.assertRaisesRegex(BrokerError, "REVOKED"):
            self.client.recall(closed, sequence=2, action_id="after-close", request={"query": "q"})
        self.broker.policy_digest = "c" * 64
        with self.assertRaisesRegex(BrokerError, "DIGEST_DRIFT"):
            self.client.recall(capability, sequence=3, action_id="drift", request={"query": "q"})
        for name in ("used_nonces.json", "revoked_nonces.json"):
            path = self.state / name
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertTrue(all(len(value) == 64 for value in json.loads(path.read_text())))
            self.assertNotIn("k" * 32, path.read_text())

    def test_concurrent_handle_exchange_returns_one_capability_and_marks_one_nonce(self):
        minted = self.client.session_mint("control", claims(), ttl_seconds=30)
        handle = minted["payload"]["handle"]
        ready = threading.Barrier(3)
        capabilities = []
        failures = []

        def exchange():
            ready.wait()
            try:
                response = self.broker.session_exchange(handle)
                capabilities.append(response["payload"]["capability"])
            except Exception as error:
                failures.append(error)

        threads = [threading.Thread(target=exchange) for _ in range(2)]
        for thread in threads:
            thread.start()
        ready.wait()
        for thread in threads:
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive())

        self.assertEqual(failures, [])
        self.assertEqual(len(capabilities), 2)
        self.assertEqual(len(set(capabilities)), 1)
        work = json.loads((self.state / "durable_work.json").read_text())
        self.assertEqual(len(work["used_nonces"]), 1)
        self.assertEqual(len(work["exchanges"]), 1)

    def test_exchange_result_survives_restart_and_is_removed_after_expiry(self):
        self.stop()
        now = [1000.0]
        def clock():
            return now[0]

        self.start(self.adapter, clock=clock)
        minted = self.broker.session_mint(
            "control", claims(), ttl_seconds=30
        )
        handle = minted["payload"]["handle"]
        first = self.broker.session_exchange(handle)

        self.stop()
        self.start(self.adapter, clock=clock)
        recovered = self.broker.session_exchange(handle)
        self.assertEqual(
            recovered["payload"]["capability"],
            first["payload"]["capability"],
        )

        long_lived = self.broker.session_mint(
            "control", claims(session_id="long-lived"), ttl_seconds=60
        )
        long_lived_capability = self.broker.session_exchange(
            long_lived["payload"]["handle"]
        )["payload"]["capability"]
        now[0] = 1031.0
        self.broker.recall(
            long_lived_capability,
            sequence=1,
            action_id="prune-expired-exchange",
            request={"query": "q"},
        )
        work = json.loads((self.state / "durable_work.json").read_text())
        self.assertNotIn(handle, work["exchanges"])

        expiring = self.broker.session_mint(
            "control", claims(session_id="restart-expiry"), ttl_seconds=30
        )
        expiring_handle = expiring["payload"]["handle"]
        self.broker.session_exchange(expiring_handle)
        self.stop()
        now[0] = 1062.0
        self.start(self.adapter, clock=clock)
        work = json.loads((self.state / "durable_work.json").read_text())
        self.assertNotIn(expiring_handle, work["exchanges"])

    def test_expired_runtime_state_and_handle_files_are_garbage_collected(self):
        self.stop()
        now = [1000.0]
        self.start(self.adapter, clock=lambda: now[0])
        minted = self.broker.session_mint(
            "control", claims(session_id="expired-session"), ttl_seconds=30
        )
        capability = self.broker.session_exchange(
            minted["payload"]["handle"]
        )["payload"]["capability"]
        retained = self.broker.retain_outcome(
            capability,
            sequence=1,
            action_id="expired-retain",
            request={
                "document_id": "expired", "epoch": 1,
                "checkpoint": 1, "outcome": "done",
            },
        )
        self.assertEqual(retained["disposition"], "queued")
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and not self.broker._work["completed"]:
            time.sleep(0.005)
        self.assertTrue(self.broker._work["completed"])
        closed = self.broker.session_close(
            capability,
            sequence=2,
            action_id="expired-close",
            timeout_seconds=1,
        )
        self.assertEqual(closed["disposition"], "closed")
        unused = self.broker.session_mint(
            "control", claims(session_id="unused-handle"), ttl_seconds=30
        )["payload"]["handle"]
        unused_path = self.state / "handles" / f"{unused}.json"
        self.assertTrue(unused_path.exists())
        before = json.loads((self.state / "durable_work.json").read_text())
        self.assertIn("expired-session", before["sessions"])
        self.assertTrue(before["used_nonces"])
        self.assertTrue(before["revoked_nonces"])
        self.assertTrue(before["completed"])

        self.stop()
        now[0] += 30 * 24 * 60 * 60 + 1
        self.start(self.adapter, clock=lambda: now[0])
        after = json.loads((self.state / "durable_work.json").read_text())
        self.assertNotIn("expired-session", after["sessions"])
        self.assertEqual(after["used_nonces"], [])
        self.assertEqual(after["revoked_nonces"], [])
        self.assertEqual(after["completed"], {})
        self.assertFalse(unused_path.exists())

    def test_zero_ttl_is_rejected_without_creating_a_handle(self):
        handles = self.state / "handles"
        before = tuple(handles.iterdir())
        with self.assertRaisesRegex(BrokerError, "SCHEMA_INVALID"):
            self.broker.session_mint(
                "control", claims(session_id="zero-ttl"), ttl_seconds=0
            )
        self.assertEqual(tuple(handles.iterdir()), before)

    def test_legacy_exchange_result_is_migrated_to_a_signed_receipt(self):
        minted = self.broker.session_mint("control", claims())
        handle = minted["payload"]["handle"]
        first = self.broker.session_exchange(handle)
        self.stop()
        work_path = self.state / "durable_work.json"
        work = json.loads(work_path.read_text())
        work.pop("schema_version")
        work["exchanges"][handle].pop("receipt")
        work_path.write_text(json.dumps(work), encoding="utf-8")

        self.start(self.adapter)
        recovered = self.broker.session_exchange(handle)
        migrated = json.loads(work_path.read_text())
        self.assertEqual(
            recovered["payload"]["capability"],
            first["payload"]["capability"],
        )
        self.assertEqual(migrated["schema_version"], 10)
        self.assertIn("receipt", migrated["exchanges"][handle])

    def test_version_nine_queue_adds_missing_operation_counter(self):
        capability = self.exchange(session_id="version-nine")
        with patch.object(self.broker, "_submit_write"):
            self.broker.transcript_checkpoint(
                capability,
                sequence=1,
                action_id="version-nine-checkpoint",
                request={
                    "document_id": "version-nine", "epoch": 1,
                    "checkpoint": 1, "content": "clean transcript",
                },
            )
        self.stop()
        work_path = self.state / "durable_work.json"
        work = json.loads(work_path.read_text())
        work["schema_version"] = 9
        work["queue"][0].pop("missing_operation_polls")
        work_path.write_text(json.dumps(work), encoding="utf-8")

        self.start(self.adapter)

        migrated = json.loads(work_path.read_text())
        self.assertEqual(migrated["schema_version"], 10)
        self.assertEqual(migrated["queue"][0]["missing_operation_polls"], 0)

    def test_legacy_reflect_content_is_scrubbed_during_migration(self):
        self.exchange(session_id="legacy-reflect")
        self.stop()
        work_path = self.state / "durable_work.json"
        work = json.loads(work_path.read_text())
        work["schema_version"] = 4
        state_key = "reflect:" + ("a" * 64)
        work["completed"][state_key] = {
            "watermark": [0, 1], "request_digest": "b" * 64,
            "idempotency_key": "c" * 64,
            "adapter_result": {"accepted": True},
            "session_id": "legacy-reflect", "method": "reflect",
            "operation_id": None,
        }
        work["expirations"]["completed"][state_key] = time.time() + 300
        work_path.write_text(json.dumps(work), encoding="utf-8")

        self.start(self.adapter)
        migrated = json.loads(work_path.read_text())
        self.assertEqual(migrated["schema_version"], 10)
        self.assertNotIn(state_key, migrated["completed"])
        self.assertNotIn(state_key, migrated["expirations"]["completed"])

    def test_legacy_queued_write_requires_drain_before_upgrade(self):
        capability = self.exchange(session_id="legacy-queued-write")
        with patch.object(self.broker, "_submit_write"):
            self.broker.transcript_checkpoint(
                capability, sequence=1, action_id="legacy-checkpoint",
                request={
                    "document_id": "legacy", "epoch": 1,
                    "checkpoint": 1, "content": "not legacy compatible",
                },
            )
        legacy = json.loads(
            (self.state / "durable_work.json").read_text()
        )
        legacy["schema_version"] = 4
        legacy["queue"][0]["adapter_request"].pop("content")
        legacy["queue"][0]["adapter_request"].pop("session_id")
        legacy["queue"][0]["authorized_bank"] = dict(BANK)

        with self.assertRaisesRegex(
            BrokerError, "LEGACY_QUEUE_NOT_DRAINED"
        ) as raised:
            self.broker._migrate_work(legacy)
        self.assertEqual(raised.exception.code, "LEGACY_QUEUE_NOT_DRAINED")
        self.assertIn("prior broker version", str(raised.exception))

    def test_complete_looking_legacy_outcome_still_requires_prior_drain(self):
        capability = self.exchange(session_id="legacy-queued-outcome")
        with patch.object(self.broker, "_submit_write"):
            self.broker.retain_outcome(
                capability,
                sequence=1,
                action_id="legacy-outcome",
                request={
                    "document_id": "legacy",
                    "epoch": 1,
                    "checkpoint": 1,
                    "outcome": "possibly already accepted",
                },
            )
        legacy = json.loads(
            (self.state / "durable_work.json").read_text()
        )
        legacy["schema_version"] = 8

        with self.assertRaisesRegex(
            BrokerError, "LEGACY_QUEUE_NOT_DRAINED"
        ):
            self.broker._migrate_work(legacy)

    def test_legacy_queued_reflect_is_scrubbed_during_migration(self):
        capability = self.exchange(session_id="legacy-queued-reflect")
        with patch.object(self.broker, "_submit_write"):
            self.broker.transcript_checkpoint(
                capability, sequence=1, action_id="legacy-reflect",
                request={
                    "document_id": "legacy", "epoch": 1,
                    "checkpoint": 1, "content": "temporary",
                },
            )
        legacy = json.loads(
            (self.state / "durable_work.json").read_text()
        )
        legacy["schema_version"] = 4
        item = legacy["queue"][0]
        item["method"] = "reflect"
        item["state_key"] = "reflect:" + ("a" * 64)
        item["adapter_request"] = {
            "reflection": "private legacy prompt",
            "idempotency_key": item["idempotency_key"],
        }

        migrated, changed = self.broker._migrate_work(legacy)

        self.assertTrue(changed)
        self.assertEqual(migrated["schema_version"], 10)
        self.assertEqual(migrated["queue"], [])
        self.assertNotIn("private legacy prompt", json.dumps(migrated))

    def test_malformed_exchange_ledger_is_rejected(self):
        self.stop()
        work_path = self.state / "durable_work.json"
        work = json.loads(work_path.read_text())
        work["exchanges"] = []
        work_path.write_text(json.dumps(work), encoding="utf-8")
        with self.assertRaisesRegex(BrokerError, "STATE_INVALID"):
            self.start(self.adapter)

    def test_nested_malformed_exchange_record_is_rejected(self):
        self.stop()
        work_path = self.state / "durable_work.json"
        work = json.loads(work_path.read_text())
        work["exchanges"] = {
            "a" * 64: {"expires_at": 2000.0},
        }
        work_path.write_text(json.dumps(work), encoding="utf-8")
        with self.assertRaisesRegex(BrokerError, "STATE_INVALID"):
            self.start(self.adapter)

    def test_malformed_exchange_state_containers_are_rejected_cleanly(self):
        for key, malformed in (
            ("sessions", []),
            ("used_nonces", {}),
            ("revoked_nonces", {}),
            ("queue", {}),
            ("completed", []),
            ("ledger_outbox", []),
            ("generation", []),
        ):
            with self.subTest(key=key):
                self.stop()
                work_path = self.state / "durable_work.json"
                work = json.loads(work_path.read_text())
                original = work[key]
                work[key] = malformed
                work_path.write_text(json.dumps(work), encoding="utf-8")
                with self.assertRaisesRegex(BrokerError, "STATE_INVALID"):
                    self.start(self.adapter)
                work[key] = original
                work_path.write_text(json.dumps(work), encoding="utf-8")
                self.start(self.adapter)

    def test_malformed_schema_three_containers_fail_as_invalid_state(self):
        self.stop()
        work_path = self.state / "durable_work.json"
        work = json.loads(work_path.read_text())
        work["schema_version"] = 3
        work.pop("expirations")
        work["exchanges"] = []
        work_path.write_text(json.dumps(work), encoding="utf-8")
        with self.assertRaisesRegex(BrokerError, "STATE_INVALID"):
            self.start(self.adapter)

    def test_malformed_queue_completed_and_session_records_are_rejected(self):
        minted = self.broker.session_mint("control", claims())
        self.broker.session_exchange(minted["payload"]["handle"])
        self.stop()
        work_path = self.state / "durable_work.json"
        original = json.loads(work_path.read_text())
        mutations = (
            lambda work: work["queue"].append({}),
            lambda work: work["completed"].update({"state": {}}),
            lambda work: work["sessions"]["session-1"].update(
                {"action_ids": ["duplicate", "duplicate"]}
            ),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                work = deepcopy(original)
                mutate(work)
                work_path.write_text(json.dumps(work), encoding="utf-8")
                with self.assertRaisesRegex(BrokerError, "STATE_INVALID"):
                    self.start(self.adapter)
        work_path.write_text(json.dumps(original), encoding="utf-8")
        self.start(self.adapter)

    def test_invalidly_signed_persisted_exchange_is_rejected(self):
        minted = self.broker.session_mint("control", claims())
        handle = minted["payload"]["handle"]
        self.broker.session_exchange(handle)
        self.stop()
        work_path = self.state / "durable_work.json"
        work = json.loads(work_path.read_text())
        work["exchanges"][handle]["capability"] = "invalid.signature"
        work_path.write_text(json.dumps(work), encoding="utf-8")
        with self.assertRaisesRegex(BrokerError, "STATE_INVALID"):
            self.start(self.adapter)

    def test_tampered_persisted_exchange_receipt_is_rejected(self):
        minted = self.broker.session_mint("control", claims())
        handle = minted["payload"]["handle"]
        self.broker.session_exchange(handle)
        self.stop()
        work_path = self.state / "durable_work.json"
        work = json.loads(work_path.read_text())
        work["exchanges"][handle]["nonce_digest"] = "f" * 64
        work_path.write_text(json.dumps(work), encoding="utf-8")
        with self.assertRaisesRegex(BrokerError, "STATE_INVALID"):
            self.start(self.adapter)

    def test_invalidly_signed_persisted_exchange_receipt_is_rejected(self):
        minted = self.broker.session_mint("control", claims())
        handle = minted["payload"]["handle"]
        self.broker.session_exchange(handle)
        self.stop()
        work_path = self.state / "durable_work.json"
        work = json.loads(work_path.read_text())
        work["exchanges"][handle]["receipt"] = "invalid.signature"
        work_path.write_text(json.dumps(work), encoding="utf-8")
        with self.assertRaisesRegex(BrokerError, "STATE_INVALID"):
            self.start(self.adapter)

    def test_persisted_exchange_receipt_cannot_be_transplanted_to_another_handle(self):
        minted = self.broker.session_mint("control", claims())
        handle = minted["payload"]["handle"]
        self.broker.session_exchange(handle)
        self.stop()
        work_path = self.state / "durable_work.json"
        work = json.loads(work_path.read_text())
        work["exchanges"]["e" * 64] = work["exchanges"].pop(handle)
        work_path.write_text(json.dumps(work), encoding="utf-8")
        with self.assertRaisesRegex(BrokerError, "STATE_INVALID"):
            self.start(self.adapter)

    def test_max_payload_bytes_is_a_bounded_integer(self):
        self.stop()
        identifier = "a" * 128
        maximal_claims = authority_claims(
            session_id=identifier,
            harness_id=identifier,
            home_bank={"profile_id": identifier, "bank_id": identifier},
            trust_class=identifier,
            companion_id=identifier,
            route=identifier,
        )
        minimum_bound = Broker(
            state_dir=self.root / "minimum-payload",
            signing_key=b"k" * 32,
            routes={
                identifier: {
                    "bank": maximal_claims["home_bank"],
                    "adapter": FakeAdapter(
                        endpoint={
                            **ENDPOINT,
                            "profile_id": identifier,
                        }
                    ),
                }
            },
            policy_digest=DIGEST_A,
            artifact_digest=DIGEST_B,
            mint_authorizer=lambda control, requested, _ttl: (
                maximal_claims if control == "control" else {}
            ),
            max_payload_bytes=MIN_PAYLOAD_BYTES,
        )
        try:
            minted = minimum_bound.session_mint(
                "control",
                {
                    "session_id": identifier,
                    "companion_id": identifier,
                    "route": identifier,
                },
            )
            exchanged = minimum_bound.session_exchange(
                minted["payload"]["handle"]
            )
        finally:
            minimum_bound.shutdown()
        self.assertEqual(minted["disposition"], "ok")
        self.assertIsNotNone(minted["payload"].get("handle"))
        self.assertEqual(exchanged["disposition"], "ok")
        self.assertIsNotNone(exchanged["payload"].get("capability"))
        for value in (
            False,
            MIN_PAYLOAD_BYTES - 1,
            MAX_PAYLOAD_BYTES + 1,
        ):
            with self.subTest(value=value), self.assertRaisesRegex(
                BrokerError, "MAX_PAYLOAD_BYTES_INVALID"
            ):
                Broker(
                    state_dir=self.state,
                    signing_key=b"k" * 32,
                    routes={
                        "local-core": {"bank": BANK, "adapter": self.adapter}
                    },
                    policy_digest=DIGEST_A,
                    artifact_digest=DIGEST_B,
                    max_payload_bytes=value,
                )

    def test_persisted_exchange_requires_its_consumed_nonce_marker(self):
        minted = self.broker.session_mint("control", claims())
        handle = minted["payload"]["handle"]
        self.broker.session_exchange(handle)
        self.stop()
        work_path = self.state / "durable_work.json"
        work = json.loads(work_path.read_text())
        work["used_nonces"].remove(work["exchanges"][handle]["nonce_digest"])
        work_path.write_text(json.dumps(work), encoding="utf-8")
        with self.assertRaisesRegex(BrokerError, "STATE_INVALID"):
            self.start(self.adapter)

    def test_read_and_model_timeouts_discard_late_payload_and_shutdown_drains(self):
        class SlowFake(FakeAdapter):
            def recall(self, request):
                time.sleep(0.05)
                return {"memories": [{"payload": "private"}]}
            def mental_model_fetch(self, request):
                time.sleep(0.05)
                return {"models": [{"payload": "private"}]}
        self.stop()
        self.adapter = SlowFake(endpoint=ENDPOINT)
        self.start(self.adapter)
        capability = self.exchange()
        recalled = self.client.recall(capability, sequence=1, action_id="slow-recall", request={"query": "q"}, timeout_seconds=0)
        modeled = self.client.mental_model_fetch(capability, sequence=2, action_id="slow-model", request={"model_id": "m"}, timeout_seconds=0)
        self.assertEqual(recalled["payload"], {"memories": []})
        self.assertEqual(modeled["payload"], {"models": []})
        for response in (recalled, modeled):
            self.assertEqual(response["diagnostic"], {"code": "MEMORY_UNAVAILABLE", "visible": True})
            self.assertNotIn("private", json.dumps(response))

    def test_timed_out_reads_do_not_starve_later_read_operations(self):
        release = threading.Event()

        class SelectivelyHungFake(FakeAdapter):
            def recall(self, request):
                if request["query"].startswith("hang-"):
                    release.wait()
                return super().recall(request)

        self.stop()
        self.adapter = SelectivelyHungFake(endpoint=ENDPOINT)
        self.start(self.adapter)
        capability = self.exchange()
        try:
            for sequence in range(1, 5):
                response = self.client.recall(
                    capability,
                    sequence=sequence,
                    action_id=f"hung-read-{sequence}",
                    request={"query": f"hang-{sequence}"},
                    timeout_seconds=0,
                )
                self.assertEqual(response["disposition"], "unavailable")

            recalled = self.client.recall(
                capability,
                sequence=5,
                action_id="read-after-timeouts",
                request={"query": "ready"},
                timeout_seconds=1,
            )
            modeled = self.client.mental_model_fetch(
                capability,
                sequence=6,
                action_id="model-after-timeouts",
                request={"model_id": "ready"},
                timeout_seconds=1,
            )
            status = self.client.session_status(
                capability,
                sequence=7,
                action_id="status-after-timeouts",
                timeout_seconds=1,
            )

            self.assertEqual(recalled["disposition"], "ok")
            self.assertEqual(modeled["disposition"], "ok")
            self.assertEqual(status["disposition"], "active")
        finally:
            release.set()

    def test_isolated_read_adapter_calls_have_a_hard_global_bound(self):
        release = threading.Event()
        entered = []
        all_entered = threading.Event()

        def hang(request):
            entered.append(deepcopy(request))
            if len(entered) == 2:
                all_entered.set()
            release.wait()
            return {"memories": []}

        try:
            with patch(
                "hindsight_memory_control_plane.broker.MAX_IN_FLIGHT_READ_ADAPTER_CALLS",
                2,
            ):
                for index in range(2):
                    with self.assertRaises(TimeoutError):
                        self.broker._invoke_read_adapter_bounded(
                            hang, {"query": f"hang-{index}"}, 0
                        )
                with self.assertRaises(TimeoutError):
                    self.broker._invoke_read_adapter_bounded(
                        hang, {"query": "overflow"}, 0
                    )

            self.assertTrue(all_entered.wait(0.2))
            self.assertEqual(len(entered), 2)
            self.assertEqual(self.broker._read_adapter_calls, 2)
        finally:
            release.set()

    def test_mint_authorizer_timeout_and_capacity_fail_closed(self):
        release = threading.Event()
        entered = threading.Event()
        calls = []

        def hang(control, requested, ttl):
            calls.append((control, deepcopy(requested), ttl))
            entered.set()
            release.wait()
            return requested

        self.broker._mint_authorizer = hang
        self.broker.adapter_call_timeout_seconds = 0.01
        try:
            with patch(
                "hindsight_memory_control_plane.broker.MAX_IN_FLIGHT_READ_ADAPTER_CALLS",
                1,
            ):
                with self.assertRaisesRegex(BrokerError, "MINT_DENIED"):
                    self.broker.session_mint(
                        "control", claims(session_id="mint-timeout")
                    )
                self.assertTrue(entered.wait(0.2))
                with self.assertRaisesRegex(BrokerError, "MINT_DENIED"):
                    self.broker.session_mint(
                        "control", claims(session_id="mint-capacity")
                    )
            self.assertEqual(len(calls), 1)
        finally:
            release.set()

    def test_shutdown_has_explicit_bound_and_reports_active_read(self):
        release = threading.Event()
        class HungFake(FakeAdapter):
            def recall(self, request):
                release.wait(1)
                return {"memories": []}
        self.stop()
        self.adapter = HungFake(endpoint=ENDPOINT)
        self.start(self.adapter)
        capability = self.exchange()
        self.client.recall(capability, sequence=1, action_id="hung", request={"query": "q"}, timeout_seconds=0)
        completed = threading.Event()
        statuses = []

        def shutdown():
            statuses.append(self.broker.shutdown(timeout_seconds=0))
            completed.set()

        shutdown_thread = threading.Thread(target=shutdown)
        shutdown_thread.start()
        try:
            self.assertTrue(completed.wait(0.2))
            shutdown_thread.join(timeout=1)
            self.assertFalse(shutdown_thread.is_alive())
            status = statuses[0]
            self.assertGreaterEqual(status["active_reads"], 1)
        finally:
            release.set()

    def test_response_payload_is_bounded(self):
        self.adapter.state["recall"] = {"memories": [{"value": "x" * 8192}]}
        capability = self.exchange()
        response = self.client.recall(capability, sequence=1, action_id="large", request={"query": "q"})
        self.assertEqual(response["disposition"], "unavailable")
        self.assertEqual(response["payload"], {"memories": []})
        self.assertEqual(response["diagnostic"]["code"], "RESPONSE_TOO_LARGE")

    def test_reflect_timeout_is_synchronous_and_visible(self):
        class SlowReflect(FakeAdapter):
            def reflect(self, request):
                time.sleep(0.05)
                return super().reflect(request)
        self.stop()
        self.adapter = SlowReflect(endpoint=ENDPOINT)
        self.start(self.adapter)
        capability = self.exchange()
        response = self.client.reflect(capability, sequence=1, action_id="reflect-timeout", request={"reflection": "note"}, timeout_seconds=0)
        self.assertEqual(response["disposition"], "unavailable")
        self.assertEqual(response["diagnostic"]["code"], "REFLECT_UNAVAILABLE")
        durable = (self.state / "durable_work.json").read_text()
        self.assertNotIn("note", durable)
        self.assertFalse(any(
            item["method"] == "reflect"
            for item in json.loads(durable)["queue"]
        ))
        reflect_calls = [
            call for call in self.adapter.calls if call["method"] == "reflect"
        ]
        deadline = time.monotonic() + 1
        while not reflect_calls and time.monotonic() < deadline:
            time.sleep(0.005)
            reflect_calls = [
                call for call in self.adapter.calls
                if call["method"] == "reflect"
            ]
        self.assertEqual(len(reflect_calls), 1)

    def test_reflect_preserves_bounded_provenance_for_source_resolution(self):
        provenance = {
            "memory_ids": ["memory-1"],
            "mental_model_ids": ["model-1"],
            "directive_ids": ["directive-1"],
            "source_resolution_required": True,
            "unresolved_memory_items": 0,
        }
        capability = self.exchange()
        with patch.object(
            self.adapter, "reflect",
            return_value={"reflection": "answer", "based_on": provenance},
        ):
            response = self.client.reflect(
                capability, sequence=1, action_id="reflect-provenance",
                request={"reflection": "note"}, timeout_seconds=1,
            )

        self.assertEqual(response["disposition"], "ok")
        self.assertEqual(response["payload"], {
            "reflection": "answer", "based_on": provenance,
        })
        durable = (self.state / "durable_work.json").read_text()
        self.assertNotIn("answer", durable)
        self.assertNotIn("note", durable)

    def test_adapter_response_cannot_expose_routing_or_credentials(self):
        self.adapter.state["recall"] = {"memories": [{"token": "private"}]}
        capability = self.exchange()
        response = self.client.recall(capability, sequence=1, action_id="redacted", request={"query": "q"})
        self.assertEqual(response["payload"], {"memories": []})
        self.assertEqual(response["diagnostic"]["code"], "RESPONSE_INVALID")
        self.assertNotIn("private", json.dumps(response))

        for sequence, key in enumerate(
            ("api-key", "Api.Key", "CONTROL key", "signing/key"),
            start=2,
        ):
            with self.subTest(key=key):
                self.adapter.state["recall"] = {
                    "memories": [{key: "private"}]
                }
                response = self.client.recall(
                    capability,
                    sequence=sequence,
                    action_id=f"redacted-{sequence}",
                    request={"query": "q"},
                )
                self.assertEqual(response["diagnostic"]["code"], "RESPONSE_INVALID")
                self.assertNotIn("private", json.dumps(response))

    def test_adapter_canonical_serialization_failure_is_response_invalid(self):
        self.adapter.state["recall"] = {"memories": [object()]}
        capability = self.exchange()

        response = self.client.recall(
            capability,
            sequence=1,
            action_id="noncanonical",
            request={"query": "q"},
        )

        self.assertEqual(response["disposition"], "unavailable")
        self.assertEqual(response["payload"], {"memories": []})
        self.assertEqual(response["diagnostic"]["code"], "RESPONSE_INVALID")


class StateDirectorySafetyTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.adapter = FakeAdapter(endpoint=ENDPOINT)

    def broker(self, state_dir):
        return Broker(
            state_dir=state_dir,
            signing_key=b"s" * 32,
            routes={
                "local-core": {"bank": BANK, "adapter": self.adapter}
            },
            policy_digest=DIGEST_A,
            artifact_digest=DIGEST_B,
            mint_authorizer=authorize_mint,
        )

    def test_state_directory_rejects_symlinked_path_component(self):
        target = self.root / "target"
        target.mkdir(mode=0o700)
        linked = self.root / "linked"
        linked.symlink_to(target, target_is_directory=True)

        with self.assertRaises(OSError):
            self.broker(linked / "state")

        self.assertFalse((target / "state").exists())

    def test_state_directory_rejects_unsafe_ancestor_mode(self):
        unsafe = self.root / "unsafe"
        unsafe.mkdir(mode=0o700)
        unsafe.chmod(0o733)

        with self.assertRaisesRegex(OSError, "component mode is unsafe"):
            self.broker(unsafe / "state")

        self.assertFalse((unsafe / "state").exists())

    def test_state_directory_rejects_nonprivate_final_directory(self):
        state = self.root / "state"
        state.mkdir(mode=0o755)

        with self.assertRaisesRegex(OSError, "must be private"):
            self.broker(state)

    def test_handles_directory_rejects_symlink(self):
        state = self.root / "state"
        state.mkdir(mode=0o700)
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir(mode=0o700)
        (state / "handles").symlink_to(elsewhere, target_is_directory=True)

        with self.assertRaises(OSError):
            self.broker(state)

        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_state_operations_remain_bound_to_opened_directory(self):
        state = self.root / "state"
        broker = self.broker(state)
        moved = self.root / "moved-state"
        try:
            state.rename(moved)
            state.mkdir(mode=0o700)

            minted = broker.session_mint(
                "control", claims(session_id="descriptor-bound"), ttl_seconds=30
            )
            self.assertTrue(
                (moved / "handles" / f'{minted["payload"]["handle"]}.json').exists()
            )
            self.assertEqual(list(state.iterdir()), [])
            broker.session_exchange(minted["payload"]["handle"])
            self.assertTrue((moved / "durable_work.json").exists())
            self.assertEqual(list(state.iterdir()), [])
        finally:
            broker.shutdown(timeout_seconds=1)

    def test_shutdown_closes_retained_state_directory_descriptors(self):
        broker = self.broker(self.root / "state")
        state_descriptor = broker._state_dir_fd
        handles_descriptor = broker._handles_dir_fd

        broker.shutdown(timeout_seconds=1)

        with self.assertRaises(OSError):
            os.fstat(state_descriptor)
        with self.assertRaises(OSError):
            os.fstat(handles_descriptor)

    def test_initialization_failure_closes_state_directory_descriptors(self):
        module = __import__(
            "hindsight_memory_control_plane.broker", fromlist=["Broker"]
        )
        opened = []
        original_state = module._open_state_directory
        original_child = module._open_private_child_directory

        def tracked_state(*args, **kwargs):
            descriptor = original_state(*args, **kwargs)
            opened.append(descriptor)
            return descriptor

        def tracked_child(*args, **kwargs):
            descriptor = original_child(*args, **kwargs)
            opened.append(descriptor)
            return descriptor

        with (
            patch(
                "hindsight_memory_control_plane.broker._open_state_directory",
                side_effect=tracked_state,
            ),
            patch(
                "hindsight_memory_control_plane.broker._open_private_child_directory",
                side_effect=tracked_child,
            ),
            patch.object(
                Broker,
                "_flush_ledger_outbox",
                side_effect=OSError("initialization failed"),
            ),
        ):
            with self.assertRaisesRegex(OSError, "initialization failed"):
                self.broker(self.root / "state")

        self.assertEqual(len(opened), 2)
        for descriptor in opened:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_invalid_timeout_is_rejected_before_state_descriptors_open(self):
        with (
            patch(
                "hindsight_memory_control_plane.broker._open_state_directory"
            ) as open_state,
            patch(
                "hindsight_memory_control_plane.broker._open_private_child_directory"
            ) as open_handles,
            self.assertRaisesRegex(BrokerError, "SCHEMA_INVALID"),
        ):
            Broker(
                state_dir=self.root / "state",
                signing_key=b"k" * 32,
                routes={
                    "local-core": {"bank": BANK, "adapter": self.adapter}
                },
                policy_digest=DIGEST_A,
                artifact_digest=DIGEST_B,
                adapter_call_timeout_seconds=31,
            )
        open_state.assert_not_called()
        open_handles.assert_not_called()


class DurableWorkTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.socket_path = self.root / "broker.sock"
        self.adapter = FakeAdapter(endpoint=ENDPOINT)
        self._start()

    def _start(self, *, clock=time.time):
        self.broker = Broker(state_dir=self.state, signing_key=b"z" * 32,
            routes={"local-core": {"bank": BANK, "adapter": self.adapter}},
            policy_digest=DIGEST_A, artifact_digest=DIGEST_B,
            mint_authorizer=self.authorize_mint, clock=clock)
        self.server = UnixJsonRpcServer(self.socket_path, self.broker)
        self.server.start()
        self.client = JsonRpcClient(self.socket_path)

    def authorize_mint(self, control, requested, ttl):
        route = self.broker.routes.get(requested.get("route"))
        if control != "control" or route is None or ttl > 60:
            return {}
        methods = ["recall"] if requested["session_id"] == "limited" else METHODS
        return authority_claims(
            **requested,
            methods=methods,
            home_bank=deepcopy(route["bank"]),
        )

    def _stop(self):
        self.server.close()
        self.broker.shutdown()

    def tearDown(self):
        self._stop()
        self.temporary.cleanup()

    def exchange(self, **changes):
        mint = self.client.session_mint(
            "control", claims(**changes), ttl_seconds=30
        )
        return self.client.session_exchange(mint["payload"]["handle"])["payload"]["capability"]

    def work(self):
        return json.loads((self.state / "durable_work.json").read_text())

    def wait_until(self, predicate, *, timeout=1):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            threading.Event().wait(0.005)
        self.fail("timed out waiting for broker state transition")

    def generation_lease_is_released(self):
        descriptor = os.open(self.broker._generation_lease_path, os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            return True
        except BlockingIOError:
            return False
        finally:
            os.close(descriptor)

    def test_async_retain_operation_survives_restart_until_terminal_success(self):
        self._stop()

        class AsyncRetainFake(FakeAdapter):
            complete = False

            def transcript_checkpoint(self, request):
                super().transcript_checkpoint(request)
                return {"operation_id": "operation-1"}

            def operation_status(self, request):
                super().operation_status(request)
                return {"status": "completed" if self.complete else "pending"}

        self.adapter = AsyncRetainFake(endpoint=ENDPOINT)
        self._start()
        capability = self.exchange(session_id="async-retain")
        queued = self.client.transcript_checkpoint(
            capability,
            sequence=1,
            action_id="checkpoint",
            request={
                "document_id": "session",
                "epoch": 1,
                "checkpoint": 1,
                "content": "clean transcript",
            },
        )
        self.assertEqual(queued["disposition"], "queued")
        self.wait_until(
            lambda: self.work()["queue"][0].get("operation_id")
            == "operation-1"
        )
        self.assertEqual(self.work()["completed"], {})

        self._stop()
        self.adapter.complete = True
        self._start()
        self.wait_until(lambda: not self.work()["queue"])
        self.assertTrue(self.work()["completed"])

    def test_failed_async_retain_is_resubmitted_before_watermark_completion(self):
        self._stop()

        class RetryRetainFake(FakeAdapter):
            submissions = 0

            def transcript_checkpoint(self, request):
                super().transcript_checkpoint(request)
                self.submissions += 1
                return {"operation_id": f"operation-{self.submissions}"}

            def operation_status(self, request):
                super().operation_status(request)
                return {
                    "status": (
                        "failed"
                        if request["operation_id"] == "operation-1"
                        else "completed"
                    )
                }

        self.adapter = RetryRetainFake(endpoint=ENDPOINT)
        self._start()
        capability = self.exchange(session_id="retry-retain")
        self.client.transcript_checkpoint(
            capability,
            sequence=1,
            action_id="checkpoint",
            request={
                "document_id": "session",
                "epoch": 1,
                "checkpoint": 1,
                "content": "clean transcript",
            },
        )
        self.wait_until(lambda: not self.work()["queue"], timeout=2)
        self.assertEqual(self.adapter.submissions, 2)
        self.assertTrue(self.work()["completed"])

    def test_transient_operation_status_failure_never_resubmits_retain(self):
        self._stop()

        class TransientStatusFake(FakeAdapter):
            submissions = 0
            polls = 0

            def transcript_checkpoint(self, request):
                super().transcript_checkpoint(request)
                self.submissions += 1
                return {"operation_id": "operation-1"}

            def operation_status(self, request):
                super().operation_status(request)
                self.polls += 1
                if self.polls == 1:
                    raise RuntimeError("transient status transport failure")
                return {"status": "completed"}

        self.adapter = TransientStatusFake(endpoint=ENDPOINT)
        self._start()
        capability = self.exchange(session_id="transient-status")
        self.client.transcript_checkpoint(
            capability, sequence=1, action_id="checkpoint",
            request={
                "document_id": "session", "epoch": 1, "checkpoint": 1,
                "content": "clean transcript",
            },
        )
        self.wait_until(lambda: not self.work()["queue"], timeout=2)
        self.assertEqual(self.adapter.submissions, 1)
        self.assertGreaterEqual(self.adapter.polls, 2)
        self.assertTrue(self.work()["completed"])

    def test_missing_operation_status_terminalizes_without_resubmission(self):
        self._stop()

        class MissingStatusFake(FakeAdapter):
            submissions = 0
            polls = 0

            def transcript_checkpoint(self, request):
                super().transcript_checkpoint(request)
                self.submissions += 1
                return {"operation_id": "operation-1"}

            def operation_status(self, request):
                super().operation_status(request)
                self.polls += 1
                return {"status": "not_found"}

        self.adapter = MissingStatusFake(endpoint=ENDPOINT)
        with patch(
            "hindsight_memory_control_plane.broker."
            "MAX_MISSING_OPERATION_POLLS",
            3,
        ):
            self._start()
            capability = self.exchange(session_id="missing-status")
            self.client.transcript_checkpoint(
                capability, sequence=1, action_id="checkpoint",
                request={
                    "document_id": "session", "epoch": 1,
                    "checkpoint": 1, "content": "clean transcript",
                },
            )
            self.wait_until(lambda: self.adapter.polls >= 2, timeout=2)
            self.assertEqual(self.adapter.submissions, 1)

            self._stop()
            self._start()
            self.wait_until(lambda: not self.work()["queue"], timeout=3)
            self.assertEqual(self.adapter.submissions, 1)
            status = self.client.session_status(
                capability, sequence=2, action_id="status"
            )
            completed = next(iter(self.work()["completed"].values()))
            self.assertEqual(
                completed["adapter_result"], {"status": "indeterminate"}
            )
            self.assertEqual(status["payload"]["writes"]["pending"], [])
            self.assertEqual(
                status["payload"]["writes"]["completed"],
                [{
                    "method": "transcript_checkpoint",
                    "watermark": [1, 1],
                    "operation_id": "operation-1",
                    "status": "indeterminate",
                }],
            )
            with patch(
                "hindsight_memory_control_plane.broker."
                "MAX_DURABLE_QUEUE_ENTRIES",
                1,
            ):
                accepted = self.client.transcript_checkpoint(
                    capability, sequence=3, action_id="next-checkpoint",
                    request={
                        "document_id": "next", "epoch": 1,
                        "checkpoint": 1, "content": "next transcript",
                    },
                )
            self.assertEqual(accepted["disposition"], "queued")

    def test_malformed_runtime_write_responses_retry_without_corrupting_state(self):
        capability = self.exchange()
        with patch.object(self.broker, "_submit_write"):
            submitted = self.broker.transcript_checkpoint(
                capability, sequence=1, action_id="malformed-submit",
                request={
                    "document_id": "session", "epoch": 1,
                    "checkpoint": 1, "content": "clean transcript",
                },
            )
        queue_id = submitted["payload"]["queue_id"]
        with (
            patch.object(
                self.adapter, "transcript_checkpoint",
                return_value={"operation_id": "invalid operation id"},
            ),
            self.assertLogs(
                "hindsight_memory_control_plane.broker", level="WARNING"
            ),
        ):
            disposition, _ = self.broker._dispatch_queued_item(queue_id)
        item = self.work()["queue"][0]
        self.assertEqual(disposition, "retry")
        self.assertIsNone(item["operation_id"])
        self.assertEqual(item["attempts"], 1)

        self.broker._transaction(
            lambda work: (
                work["queue"][0].__setitem__("operation_id", "operation-1"),
                work["queue"][0].__setitem__("next_retry", None),
            ),
            runtime=True,
        )
        with (
            patch.object(
                self.adapter, "operation_status",
                return_value={"status": "unknown"},
            ),
            self.assertLogs(
                "hindsight_memory_control_plane.broker", level="WARNING"
            ),
        ):
            disposition, _ = self.broker._dispatch_queued_item(queue_id)
        item = self.work()["queue"][0]
        self.assertEqual(disposition, "retry")
        self.assertEqual(item["operation_id"], "operation-1")
        self.assertEqual(item["attempts"], 2)

    def test_pending_operation_polling_uses_capped_exponential_backoff(self):
        capability = self.exchange()
        now = [100.0]
        self.broker.clock = lambda: now[0]
        with patch.object(self.broker, "_submit_write"):
            submitted = self.broker.transcript_checkpoint(
                capability, sequence=1, action_id="pending-backoff",
                request={
                    "document_id": "session", "epoch": 1,
                    "checkpoint": 1, "content": "clean transcript",
                },
            )
        queue_id = submitted["payload"]["queue_id"]
        self.broker._transaction(
            lambda work: work["queue"][0].__setitem__(
                "operation_id", "operation-1"
            ),
            runtime=True,
        )
        with patch.object(
            self.adapter, "operation_status",
            return_value={"status": "pending"},
        ), self.assertLogs(
            "hindsight_memory_control_plane.broker", level="WARNING"
        ) as logs:
            delays = []
            for _ in range(70):
                disposition, delay = self.broker._dispatch_queued_item(queue_id)
                self.assertEqual(disposition, "retry")
                delays.append(delay)
                now[0] += delay

        self.assertEqual(delays[:4], [0.25, 0.5, 1.0, 2.0])
        self.assertEqual(delays[-2:], [5.0, 5.0])
        item = self.work()["queue"][0]
        self.assertEqual(item["poll_attempts"], 70)
        self.assertEqual(item["attempts"], 0)
        self.assertIsNone(item["last_error"])
        pending_warnings = [
            message for message in logs.output
            if "runtime operation remains pending" in message
        ]
        self.assertEqual(len(pending_warnings), 2)
        self.assertIn("operation_id=operation-1", pending_warnings[0])
        self.assertIn(f"queue_id={queue_id}", pending_warnings[0])

    def test_session_status_persists_pending_and_completed_write_watermarks(self):
        capability = self.exchange(session_id="status-watermarks")
        with patch.object(self.broker, "_submit_write"):
            submitted = self.client.transcript_checkpoint(
                capability, sequence=1, action_id="status-checkpoint",
                request={
                    "document_id": "session", "epoch": 2,
                    "checkpoint": 3, "content": "clean transcript",
                },
            )
        queue_id = submitted["payload"]["queue_id"]
        self.broker._transaction(
            lambda work: (
                work["queue"][0].__setitem__(
                    "operation_id", "operation-1"
                ),
                work["queue"][0].__setitem__("poll_attempts", 7),
            ),
            runtime=True,
        )

        pending = self.client.session_status(
            capability, sequence=2, action_id="pending-status"
        )
        self.assertEqual(pending["payload"]["writes"], {
            "pending": [{
                "queue_id": queue_id,
                "method": "transcript_checkpoint",
                "watermark": [2, 3],
                "operation_id": "operation-1",
                "status": "pending",
                "poll_attempts": 7,
            }],
            "completed": [],
            "omitted": {"pending": 0, "completed": 0},
        })

        with patch.object(
            self.adapter, "operation_status",
            return_value={"status": "completed"},
        ):
            disposition, _ = self.broker._dispatch_queued_item(queue_id)
        self.assertEqual(disposition, "complete")
        self._stop()
        self._start()

        completed = self.client.session_status(
            capability, sequence=3, action_id="completed-status"
        )
        self.assertEqual(completed["payload"]["writes"], {
            "pending": [],
            "completed": [{
                "method": "transcript_checkpoint",
                "watermark": [2, 3],
                "operation_id": "operation-1",
                "status": "completed",
            }],
            "omitted": {"pending": 0, "completed": 0},
        })

    def test_session_status_reports_most_recently_completed_replacement(self):
        capability = self.exchange(session_id="completion-order")

        def complete(response):
            disposition, _ = self.broker._dispatch_queued_item(
                response["payload"]["queue_id"]
            )
            self.assertEqual(disposition, "complete")

        with patch.object(self.broker, "_submit_write"):
            complete(self.client.transcript_checkpoint(
                capability, sequence=1, action_id="completion-first",
                request={
                    "document_id": "session", "epoch": 1,
                    "checkpoint": 1, "content": "first",
                },
            ))
            for sequence in range(2, 11):
                complete(self.client.retain_outcome(
                    capability, sequence=sequence,
                    action_id=f"completion-outcome-{sequence}",
                    request={
                        "document_id": f"outcome-{sequence}", "epoch": 1,
                        "checkpoint": 1, "outcome": "done",
                    },
                ))
            complete(self.client.transcript_checkpoint(
                capability, sequence=11, action_id="completion-latest",
                request={
                    "document_id": "session", "epoch": 1,
                    "checkpoint": 2, "content": "latest",
                },
            ))

        response = self.client.session_status(
            capability, sequence=12, action_id="completion-status"
        )
        writes = response["payload"]["writes"]
        self.assertIn({
            "method": "transcript_checkpoint",
            "watermark": [1, 2],
            "operation_id": None,
            "status": "completed",
        }, writes["completed"])
        self.assertEqual(writes["omitted"]["completed"], 2)

    @staticmethod
    def writes_drained(broker):
        with broker._lock:
            return not broker._write_futures

    def test_queue_and_watermark_are_one_atomic_private_state_before_ack(self):
        capability = self.exchange()
        response = self.client.retain_outcome(capability, sequence=1, action_id="retain", request={"document_id": "doc", "epoch": 1, "checkpoint": 1, "outcome": "done"})
        self.assertEqual(response["disposition"], "queued")
        self.assertEqual(os.stat(self.state / "durable_work.json").st_mode & 0o777, 0o600)
        state = self.work()
        records = state["queue"] + list(state["completed"].values())
        self.assertTrue(any(record["watermark"] == [1, 1] for record in records))
        self.assertTrue(all("idempotency_key" in record for record in records))

    def test_queued_writes_persist_their_authorized_route_and_digests(self):
        capability = self.exchange()
        with patch.object(self.broker, "_submit_write"):
            self.broker.retain_outcome(
                capability,
                sequence=1,
                action_id="authorized-queue",
                request={
                    "document_id": "doc",
                    "epoch": 1,
                    "checkpoint": 1,
                    "outcome": "done",
                },
            )

        queued = self.work()["queue"][0]
        self.assertTrue(
            {"authorized_bank", "policy_digest", "artifact_digest"}
            <= set(queued)
        )
        self.assertEqual(queued["authorized_bank"], BANK)
        self.assertEqual(queued["policy_digest"], DIGEST_A)
        self.assertEqual(queued["artifact_digest"], DIGEST_B)

    def test_dispatch_rejects_authorization_drift_before_adapter_write(self):
        capability = self.exchange()
        with patch.object(self.broker, "_submit_write"):
            queued = self.broker.retain_outcome(
                capability,
                sequence=1,
                action_id="drifted-queue",
                request={
                    "document_id": "doc",
                    "epoch": 1,
                    "checkpoint": 1,
                    "outcome": "done",
                },
            )
        queue_id = queued["payload"]["queue_id"]
        before = len(self.adapter.calls)
        self.broker.policy_digest = "c" * 64

        with self.assertRaisesRegex(BrokerError, "STATE_INVALID"):
            self.broker._dispatch_queued_item(queue_id)

        self.assertEqual(len(self.adapter.calls), before)

    def test_missing_legacy_queue_authorization_metadata_is_rejected(self):
        capability = self.exchange()
        with patch.object(self.broker, "_submit_write"):
            self.broker.retain_outcome(
                capability,
                sequence=1,
                action_id="legacy-queue",
                request={
                    "document_id": "doc",
                    "epoch": 1,
                    "checkpoint": 1,
                    "outcome": "done",
                },
            )
        legacy = self.work()
        for key in ("authorized_bank", "policy_digest", "artifact_digest"):
            legacy["queue"][0].pop(key, None)

        with self.assertRaisesRegex(BrokerError, "STATE_INVALID"):
            self.broker._validate_work(legacy)

    def test_session_action_history_is_bounded_before_sequence_commit(self):
        capability = self.exchange()

        def fill_history(work):
            state = work["sessions"]["session-1"]
            state["sequence"] = MAX_SESSION_ACTION_IDS
            state["action_ids"] = [
                f"prior-{index}" for index in range(MAX_SESSION_ACTION_IDS)
            ]

        self.broker._transaction(fill_history, runtime=True)
        with self.assertRaisesRegex(BrokerError, "SESSION_ACTION_LIMIT"):
            self.client.recall(
                capability,
                sequence=MAX_SESSION_ACTION_IDS + 1,
                action_id="over-limit",
                request={"query": "q"},
            )
        state = self.work()["sessions"]["session-1"]
        self.assertEqual(state["sequence"], MAX_SESSION_ACTION_IDS)
        self.assertNotIn("over-limit", state["action_ids"])

    def test_request_and_persisted_sequences_have_the_runtime_numeric_bound(self):
        capability = self.exchange()

        with self.assertRaisesRegex(BrokerError, "SCHEMA_INVALID"):
            self.client.recall(
                capability,
                sequence=MAX_REQUEST_SEQUENCE + 1,
                action_id="oversized-sequence",
                request={"query": "q"},
            )

        state = self.work()["sessions"]["session-1"]
        self.assertEqual(state["sequence"], 0)
        self.assertEqual(state["action_ids"], [])
        invalid = self.work()
        invalid["sessions"]["session-1"]["sequence"] = (
            MAX_REQUEST_SEQUENCE + 1
        )
        with self.assertRaisesRegex(BrokerError, "STATE_INVALID"):
            self.broker._validate_work(invalid)

    def test_session_action_history_reserves_its_final_slot_for_close(self):
        capability = self.exchange()

        def fill_history(work):
            state = work["sessions"]["session-1"]
            state["sequence"] = MAX_SESSION_ACTION_IDS - 1
            state["action_ids"] = [
                f"prior-{index}"
                for index in range(MAX_SESSION_ACTION_IDS - 1)
            ]

        self.broker._transaction(fill_history, runtime=True)
        with self.assertRaisesRegex(BrokerError, "SESSION_ACTION_LIMIT"):
            self.client.recall(
                capability,
                sequence=MAX_SESSION_ACTION_IDS,
                action_id="consume-close-slot",
                request={"query": "q"},
            )

        with patch.object(self.broker, "_submit_write"):
            response = self.broker.session_close(
                capability,
                sequence=MAX_SESSION_ACTION_IDS,
                action_id="reserved-close",
                timeout_seconds=0,
            )

        self.assertEqual(response["disposition"], "closed")
        state = self.work()["sessions"]["session-1"]
        self.assertTrue(state["closed"])
        self.assertEqual(state["sequence"], MAX_SESSION_ACTION_IDS)
        self.assertEqual(len(state["action_ids"]), MAX_SESSION_ACTION_IDS)
        self.assertEqual(state["action_ids"][-1], "reserved-close")

    def test_newer_checkpoint_coalesces_queued_state_before_entry_limit(self):
        capability = self.exchange()
        with (
            patch.object(self.broker, "_submit_write"),
            patch(
                "hindsight_memory_control_plane.broker.MAX_DURABLE_QUEUE_ENTRIES",
                1,
            ),
        ):
            first = self.broker.transcript_checkpoint(
                capability,
                sequence=1,
                action_id="bounded-first",
                request={
                    "document_id": "doc",
                    "epoch": 1,
                    "checkpoint": 1,
                    "content": "first",
                },
            )
            newer = self.broker.transcript_checkpoint(
                capability,
                sequence=2,
                action_id="bounded-newer",
                request={
                    "document_id": "doc",
                    "epoch": 1,
                    "checkpoint": 2,
                    "content": "newer",
                },
            )
            with self.assertRaisesRegex(BrokerError, "QUEUE_FULL"):
                self.broker.transcript_checkpoint(
                    capability,
                    sequence=3,
                    action_id="bounded-overflow",
                    request={
                        "document_id": "other-doc",
                        "epoch": 1,
                        "checkpoint": 1,
                        "content": "overflow",
                    },
                )

        self.assertEqual(first["disposition"], "queued")
        self.assertEqual(newer["disposition"], "queued")
        queue = self.work()["queue"]
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["watermark"], [1, 2])
        self.assertEqual(queue[0]["adapter_request"]["content"], "newer")
        self.assertEqual(self.work()["sessions"]["session-1"]["sequence"], 2)

    def test_new_epoch_preserves_queued_sealed_epoch_document(self):
        capability = self.exchange()
        with patch.object(self.broker, "_submit_write"):
            first = self.broker.transcript_checkpoint(
                capability, sequence=1, action_id="sealed-epoch",
                request={
                    "document_id": "doc", "epoch": 1, "checkpoint": 3,
                    "content": "sealed epoch",
                },
            )
            second = self.broker.transcript_checkpoint(
                capability, sequence=2, action_id="new-epoch",
                request={
                    "document_id": "doc", "epoch": 2, "checkpoint": 1,
                    "content": "new epoch",
                },
            )

        self.assertEqual(first["disposition"], "queued")
        self.assertEqual(second["disposition"], "queued")
        queue = self.work()["queue"]
        self.assertEqual(len(queue), 2)
        self.assertEqual(
            [entry["watermark"] for entry in queue], [[1, 3], [2, 1]]
        )
        self.assertNotEqual(queue[0]["state_key"], queue[1]["state_key"])

    def test_same_logical_document_is_isolated_between_sessions(self):
        first_capability = self.exchange(session_id="first-session")
        second_capability = self.exchange(session_id="second-session")
        with patch.object(self.broker, "_submit_write"):
            self.broker.transcript_checkpoint(
                first_capability, sequence=1, action_id="first-session-write",
                request={
                    "document_id": "shared", "epoch": 1,
                    "checkpoint": 1, "content": "first",
                },
            )
            self.broker.transcript_checkpoint(
                second_capability, sequence=1,
                action_id="second-session-write",
                request={
                    "document_id": "shared", "epoch": 1,
                    "checkpoint": 1, "content": "second",
                },
            )

        queue = self.work()["queue"]
        self.assertEqual(len(queue), 2)
        self.assertNotEqual(queue[0]["state_key"], queue[1]["state_key"])
        self.assertEqual(
            [entry["adapter_request"]["session_id"] for entry in queue],
            ["first-session", "second-session"],
        )

    def test_newest_checkpoint_coalesces_obsolete_waiters_behind_in_flight(self):
        capability = self.exchange()
        with patch.object(self.broker, "_submit_write"):
            first = self.broker.transcript_checkpoint(
                capability, sequence=1, action_id="in-flight-first",
                request={
                    "document_id": "doc", "epoch": 1, "checkpoint": 1,
                    "content": "first",
                },
            )
            self.broker._transaction(
                lambda work: work["queue"][0].__setitem__("in_flight", True),
                runtime=True,
            )
            self.broker.transcript_checkpoint(
                capability, sequence=2, action_id="waiting-obsolete",
                request={
                    "document_id": "doc", "epoch": 2, "checkpoint": 1,
                    "content": "obsolete",
                },
            )
            newest = self.broker.transcript_checkpoint(
                capability, sequence=3, action_id="waiting-newest",
                request={
                    "document_id": "doc", "epoch": 3, "checkpoint": 1,
                    "content": "newest",
                },
            )

        self.assertEqual(first["disposition"], "queued")
        self.assertEqual(newest["disposition"], "queued")
        queue = self.work()["queue"]
        self.assertEqual(len(queue), 3)
        self.assertTrue(queue[0]["in_flight"])
        self.assertEqual(queue[0]["watermark"], [1, 1])
        self.assertEqual(queue[1]["watermark"], [2, 1])
        self.assertEqual(queue[2]["watermark"], [3, 1])
        self.assertEqual(queue[2]["adapter_request"]["content"], "newest")

    def test_distinct_outcome_checkpoints_remain_separate_queued_documents(self):
        capability = self.exchange()
        with patch.object(self.broker, "_submit_write"):
            first = self.broker.retain_outcome(
                capability, sequence=1, action_id="outcome-first",
                request={
                    "document_id": "doc", "epoch": 1, "checkpoint": 1,
                    "outcome": "first",
                },
            )
            second = self.broker.retain_outcome(
                capability, sequence=2, action_id="outcome-second",
                request={
                    "document_id": "doc", "epoch": 1, "checkpoint": 2,
                    "outcome": "second",
                },
            )

        self.assertEqual(first["disposition"], "queued")
        self.assertEqual(second["disposition"], "queued")
        queue = self.work()["queue"]
        self.assertEqual(len(queue), 2)
        self.assertNotEqual(queue[0]["state_key"], queue[1]["state_key"])
        self.assertEqual(
            [entry["adapter_request"]["outcome"] for entry in queue],
            ["first", "second"],
        )

    def test_newer_checkpoint_preserves_submitted_operation_until_terminal(self):
        capability = self.exchange()
        with patch.object(self.broker, "_submit_write"):
            first = self.broker.transcript_checkpoint(
                capability, sequence=1, action_id="submitted-first",
                request={
                    "document_id": "session", "epoch": 1,
                    "checkpoint": 1, "content": "first transcript",
                },
            )
            self.broker._transaction(
                lambda work: work["queue"][0].__setitem__(
                    "operation_id", "operation-1"
                ),
                runtime=True,
            )
            newer = self.broker.transcript_checkpoint(
                capability, sequence=2, action_id="submitted-newer",
                request={
                    "document_id": "session", "epoch": 1,
                    "checkpoint": 2, "content": "newer transcript",
                },
            )

        queue = self.work()["queue"]
        self.assertEqual(
            [item["queue_id"] for item in queue],
            [first["payload"]["queue_id"], newer["payload"]["queue_id"]],
        )
        self.assertEqual(queue[0]["operation_id"], "operation-1")

    def test_older_checkpoint_replay_survives_newer_watermark_completion(self):
        capability = self.exchange()
        first_request = {
            "document_id": "session", "epoch": 1, "checkpoint": 1,
            "content": "first transcript",
        }
        with patch.object(self.broker, "_submit_write"):
            first = self.broker.transcript_checkpoint(
                capability, sequence=1, action_id="checkpoint-first",
                request=first_request,
            )
            newer = self.broker.transcript_checkpoint(
                capability, sequence=2, action_id="checkpoint-newer",
                request={
                    "document_id": "session", "epoch": 1,
                    "checkpoint": 2, "content": "newer transcript",
                },
            )
        self.assertNotEqual(
            first["payload"]["queue_id"], newer["payload"]["queue_id"]
        )
        self.assertEqual(
            self.broker._dispatch_queued_item(
                newer["payload"]["queue_id"]
            )[0],
            "complete",
        )

        replay = self.broker.transcript_checkpoint(
            capability, sequence=1, action_id="checkpoint-first",
            request=first_request,
        )
        self.assertEqual(replay["disposition"], "idempotent")
        self.assertEqual(replay["payload"], {"watermark": [1, 1]})

        with self.assertRaisesRegex(BrokerError, "DIGEST_DRIFT"):
            self.broker.transcript_checkpoint(
                capability, sequence=1, action_id="checkpoint-first",
                request={**first_request, "content": "changed transcript"},
            )

    def test_completed_capacity_is_reserved_before_a_new_write(self):
        capability = self.exchange()
        with (
            patch.object(self.broker, "_submit_write"),
            patch(
                "hindsight_memory_control_plane.broker.MAX_COMPLETED_ENTRIES",
                1,
            ),
        ):
            first = self.broker.retain_outcome(
                capability, sequence=1, action_id="completed-first",
                request={
                    "document_id": "doc", "epoch": 1, "checkpoint": 1,
                    "outcome": "first",
                },
            )
            disposition, _delay = self.broker._dispatch_queued_item(
                first["payload"]["queue_id"]
            )
            with self.assertRaisesRegex(BrokerError, "QUEUE_FULL"):
                self.broker.retain_outcome(
                    capability, sequence=2, action_id="completed-overflow",
                    request={
                        "document_id": "other", "epoch": 1,
                        "checkpoint": 1, "outcome": "overflow",
                    },
                )

        self.assertEqual(disposition, "complete")
        self.assertEqual(len(self.work()["completed"]), 1)
        self.assertEqual(self.work()["sessions"]["session-1"]["sequence"], 1)

    def test_admission_reserves_full_async_completion_after_rank_rebase(self):
        capability = self.exchange()
        with patch.object(self.broker, "_submit_write"):
            first = self.broker.retain_outcome(
                capability, sequence=1, action_id="completed-old-order",
                request={
                    "document_id": "old", "epoch": 1, "checkpoint": 1,
                    "outcome": "first",
                },
            )
            self.assertEqual(
                self.broker._dispatch_queued_item(
                    first["payload"]["queue_id"]
                )[0],
                "complete",
            )

            def inflate_order(work):
                only_record = next(iter(work["completed"].values()))
                only_record["completion_order"] = 10**11

            self.broker._transaction(inflate_order)
            normalized = self.work()
            self.broker._normalize_completion_orders(normalized)
            state_key = self.broker._checkpoint_state_key(
                "retain_outcome",
                authority_claims(),
                "new",
                epoch=1,
                checkpoint=1,
            )
            reservation = self.broker._completed_reservation_bytes({
                "state_key": state_key,
                "watermark": [1, 1],
                "request_digest": "d" * 64,
                "idempotency_key": "e" * 64,
                "session_id": "session-1",
                "method": "retain_outcome",
                "operation_id": None,
            })
            exact_limit = (
                len(canonical_bytes(normalized["completed"])) + reservation
            )
            with (
                patch(
                    "hindsight_memory_control_plane.broker."
                    "MAX_COMPLETED_BYTES",
                    exact_limit,
                ),
                patch.object(
                    self.adapter,
                    "retain_outcome",
                    return_value={"operation_id": "o" * 128},
                ),
            ):
                second = self.broker.retain_outcome(
                    capability,
                    sequence=2,
                    action_id="completed-new-order",
                    request={
                        "document_id": "new",
                        "epoch": 1,
                        "checkpoint": 1,
                        "outcome": "second",
                    },
                )
                self.assertEqual(
                    self.broker._dispatch_queued_item(
                        second["payload"]["queue_id"]
                    )[0],
                    "retry",
                )

                def make_poll_ready(work):
                    queued = next(
                        item for item in work["queue"]
                        if item["queue_id"] == second["payload"]["queue_id"]
                    )
                    queued["next_retry"] = 0

                self.broker._transaction(make_poll_ready)
                self.assertEqual(
                    self.broker._dispatch_queued_item(
                        second["payload"]["queue_id"]
                    )[0],
                    "complete",
                )

        self.assertEqual(self.work()["queue"], [])
        self.assertEqual(
            sorted(
                record["completion_order"]
                for record in self.work()["completed"].values()
            ),
            [1, 2],
        )

    def test_reflect_does_not_reserve_or_consume_completed_storage(self):
        capability = self.exchange()
        self.broker.max_payload_bytes = 128
        with (
            patch(
                "hindsight_memory_control_plane.broker.MAX_COMPLETED_BYTES",
                64,
            ),
        ):
            response = self.broker.reflect(
                capability,
                sequence=1,
                action_id="completed-byte-reservation",
                request={"reflection": "bounded"},
                timeout_seconds=1,
            )
        self.assertEqual(response["disposition"], "ok")
        self.assertEqual(self.work()["queue"], [])
        self.assertEqual(self.work()["completed"], {})
        self.assertEqual(self.work()["sessions"]["session-1"]["sequence"], 1)

    def test_close_drains_prior_session_work_without_synthesizing_checkpoint(self):
        capability = self.exchange()
        with patch.object(self.broker, "_submit_write"):
            prior = self.broker.retain_outcome(
                capability,
                sequence=1,
                action_id="prior-session-work",
                request={
                    "document_id": "doc", "epoch": 1,
                    "checkpoint": 1, "outcome": "done",
                },
            )
            closed = self.broker.session_close(
                capability,
                sequence=2,
                action_id="ordered-close",
                timeout_seconds=0,
            )
        queue = self.work()["queue"]
        self.assertEqual(len(queue), 1)
        self.assertEqual(closed["payload"]["undrained"], 1)
        self.assertFalse(any(
            item["idempotency_key"] == closed["action_digest"]
            for item in queue
        ))
        self.assertEqual(
            self.broker._dispatch_queued_item(
                prior["payload"]["queue_id"]
            )[0],
            "complete",
        )

    def test_predecessor_scheduler_state_is_updated_after_durable_lease_release(self):
        capability = self.exchange()
        with patch.object(self.broker, "_submit_write"):
            prior = self.broker.retain_outcome(
                capability,
                sequence=1,
                action_id="prior-session-work",
                request={
                    "document_id": "doc", "epoch": 1,
                    "checkpoint": 1, "outcome": "done",
                },
            )
            following = self.broker.transcript_checkpoint(
                capability,
                sequence=2,
                action_id="following-checkpoint",
                request={
                    "document_id": "transcript", "epoch": 1,
                    "checkpoint": 1,
                    "content": "complete cleaned transcript",
                },
            )
        following_id = following["payload"]["queue_id"]
        original = self.broker._remember_write_predecessor

        def remember_after_lease(queue_id, predecessor_id):
            descriptor = self.broker._lease_descriptor()
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
            original(queue_id, predecessor_id)

        with patch.object(
            self.broker,
            "_remember_write_predecessor",
            side_effect=remember_after_lease,
        ) as remember:
            self.broker._drain_item(following_id)

        remember.assert_called_once()
        self.assertEqual(remember.call_args.args[1], prior["payload"]["queue_id"])

    def test_ledger_outbox_capacity_is_checked_before_close_commit(self):
        self.broker.ledger_path = self.root / "bounded-ledger.jsonl"
        self.broker.routes["local-core"]["bank"] = {
            **BANK,
            "endpoint": ENDPOINT,
        }
        capability = self.exchange()
        for constant, limit in (
            ("MAX_LEDGER_OUTBOX_ENTRIES", 0),
            ("MAX_LEDGER_OUTBOX_BYTES", 2),
        ):
            with self.subTest(constant=constant), patch(
                f"hindsight_memory_control_plane.broker.{constant}", limit,
            ), self.assertRaisesRegex(BrokerError, "QUEUE_FULL"):
                self.broker.session_close(
                    capability,
                    sequence=1,
                    action_id=f"bounded-{constant.lower()}",
                    timeout_seconds=0,
                )
            state = self.work()
            self.assertEqual(state["ledger_outbox"], {})
            self.assertEqual(state["queue"], [])
            self.assertFalse(state["sessions"]["session-1"]["closed"])

    def test_every_queueing_path_rejects_oversized_durable_entry(self):
        capability = self.exchange()

        def operation():
            return self.broker.retain_outcome(
                capability,
                sequence=1,
                action_id="oversized-retain",
                request={
                    "document_id": "doc",
                    "epoch": 1,
                    "checkpoint": 1,
                    "outcome": "done",
                },
            )

        with patch(
            "hindsight_memory_control_plane.broker.MAX_DURABLE_QUEUE_ENTRY_BYTES",
            1,
        ):
            with self.assertRaisesRegex(BrokerError, "QUEUE_FULL"):
                operation()

        state = self.work()
        self.assertEqual(state["queue"], [])
        self.assertEqual(state["sessions"]["session-1"]["sequence"], 0)
        self.assertFalse(state["sessions"]["session-1"]["closed"])

    def test_queueing_rejects_total_serialized_byte_overflow(self):
        capability = self.exchange()
        with patch(
            "hindsight_memory_control_plane.broker.MAX_DURABLE_QUEUE_BYTES",
            128,
        ):
            with self.assertRaisesRegex(BrokerError, "QUEUE_FULL"):
                self.broker.transcript_checkpoint(
                    capability,
                    sequence=1,
                    action_id="serialized-overflow",
                    request={
                        "document_id": "doc",
                        "epoch": 1,
                        "checkpoint": 1,
                        "content": "x" * 256,
                    },
                )

        self.assertEqual(self.work()["queue"], [])

    def test_persisted_queue_is_rejected_when_entry_bound_is_exceeded(self):
        capability = self.exchange()
        with patch.object(self.broker, "_submit_write"):
            self.broker.retain_outcome(
                capability,
                sequence=1,
                action_id="persisted-entry",
                request={
                    "document_id": "doc",
                    "epoch": 1,
                    "checkpoint": 1,
                    "outcome": "done",
                },
            )
        self._stop()

        with (
            patch(
                "hindsight_memory_control_plane.broker.MAX_DURABLE_QUEUE_ENTRY_BYTES",
                1,
            ),
            self.assertRaisesRegex(BrokerError, "STATE_INVALID"),
        ):
            self._start()

    def test_ledger_routes_are_closed_before_minting(self):
        for bank in (
            BANK,
            {**BANK, "endpoint": ENDPOINT, "unexpected": True},
        ):
            with self.subTest(bank=bank):
                state = self.root / f"ledger-route-{len(bank)}"
                broker = Broker(
                    state_dir=state,
                    signing_key=b"r" * 32,
                    routes={
                        "local-core": {"bank": bank, "adapter": self.adapter}
                    },
                    policy_digest=DIGEST_A,
                    artifact_digest=DIGEST_B,
                    mint_authorizer=authorize_mint,
                    ledger_path=self.root / "controller.jsonl",
                )
                try:
                    with self.assertRaisesRegex(BrokerError, "MINT_DENIED"):
                        broker.session_mint("control", claims())
                finally:
                    broker.shutdown()

    def test_hung_adapter_call_is_bounded_and_releases_lease_after_worker_exit(self):
        release = threading.Event()
        self.addCleanup(release.set)
        calls = []

        class HungWriteAdapter(FakeAdapter):
            def retain_outcome(self, request):
                calls.append(deepcopy(request))
                release.wait()
                return super().retain_outcome(request)

        self._stop()
        self.adapter = HungWriteAdapter(endpoint=ENDPOINT)
        self._start()
        self.broker.adapter_call_timeout_seconds = 0.02
        capability = self.exchange()
        with patch.object(self.broker, "_submit_write"):
            response = self.broker.retain_outcome(
                capability,
                sequence=1,
                action_id="bounded-adapter",
                request={
                    "document_id": "doc",
                    "epoch": 1,
                    "checkpoint": 1,
                    "outcome": "done",
                },
            )
        queue_id = response["payload"]["queue_id"]

        disposition, delay = self.broker._dispatch_queued_item(queue_id)

        self.assertEqual(disposition, "retry")
        self.assertGreater(delay, 0)
        queued = next(
            item for item in self.work()["queue"] if item["queue_id"] == queue_id
        )
        self.assertEqual(queued["attempts"], 1)
        self.assertEqual(queued["last_error"], "ADAPTER_UNAVAILABLE")
        self.assertTrue(queued["in_flight"])
        with self.assertRaises(TimeoutError):
            self.broker._invoke_adapter_bounded(
                queue_id,
                "retain_outcome",
                self.adapter.retain_outcome,
                queued["adapter_request"],
            )
        self.assertEqual(len(calls), 1)

        with patch.object(self.broker, "_submit_write"):
            newer = self.broker.retain_outcome(
                capability,
                sequence=2,
                action_id="bounded-adapter-newer",
                request={
                    "document_id": "doc",
                    "epoch": 2,
                    "checkpoint": 1,
                    "outcome": "newer",
                },
            )
        queued_ids = {item["queue_id"] for item in self.work()["queue"]}
        self.assertEqual(
            queued_ids, {queue_id, newer["payload"]["queue_id"]}
        )

        # The external write may still take effect after the local timeout, so
        # it retains the generation fence until the worker actually exits.
        self.assertFalse(self.generation_lease_is_released())
        release.set()
        self.wait_until(self.generation_lease_is_released)

    def test_hung_adapter_generation_lease_expires_and_fences_late_result(self):
        release = threading.Event()
        self.addCleanup(release.set)

        class HungWriteAdapter(FakeAdapter):
            def retain_outcome(self, request):
                release.wait()
                return super().retain_outcome(request)

        self._stop()
        self.adapter = HungWriteAdapter(endpoint=ENDPOINT)
        self._start()
        self.broker.adapter_call_timeout_seconds = 0.01
        self.broker._adapter_generation_lease_timeout_seconds = 0.05
        capability = self.exchange()
        with patch.object(self.broker, "_submit_write"):
            response = self.broker.retain_outcome(
                capability,
                sequence=1,
                action_id="expiring-generation-lease",
                request={
                    "document_id": "doc",
                    "epoch": 1,
                    "checkpoint": 1,
                    "outcome": "done",
                },
            )
        queue_id = response["payload"]["queue_id"]

        disposition, _delay = self.broker._dispatch_queued_item(queue_id)

        self.assertEqual(disposition, "retry")
        self.assertFalse(self.generation_lease_is_released())
        self.wait_until(self.generation_lease_is_released)
        with self.broker._adapter_calls_lock:
            future = self.broker._adapter_calls[queue_id][2]
        self.assertTrue(future._hindsight_generation_lease_expired)

        release.set()
        self.wait_until(future.done)
        queued = next(
            item for item in self.work()["queue"]
            if item["queue_id"] == queue_id
        )
        with self.assertRaises(TimeoutError):
            self.broker._invoke_adapter_bounded(
                queue_id,
                "retain_outcome",
                self.adapter.retain_outcome,
                queued["adapter_request"],
            )
        with self.broker._adapter_calls_lock:
            self.assertNotIn(queue_id, self.broker._adapter_calls)

    def test_dispatch_interrupt_transfers_generation_lease_to_reserved_call(self):
        release = threading.Event()
        self.addCleanup(release.set)

        class HungWriteAdapter(FakeAdapter):
            def retain_outcome(self, request):
                release.wait()
                return super().retain_outcome(request)

        self._stop()
        self.adapter = HungWriteAdapter(endpoint=ENDPOINT)
        self._start()
        capability = self.exchange()
        with patch.object(self.broker, "_submit_write"):
            response = self.broker.retain_outcome(
                capability,
                sequence=1,
                action_id="interrupted-generation-lease",
                request={
                    "document_id": "doc",
                    "epoch": 1,
                    "checkpoint": 1,
                    "outcome": "done",
                },
            )
        queue_id = response["payload"]["queue_id"]

        with (
            patch.object(
                self.broker,
                "_invoke_adapter_bounded",
                side_effect=KeyboardInterrupt("dispatch interrupted"),
            ),
            self.assertRaisesRegex(KeyboardInterrupt, "dispatch interrupted"),
        ):
            self.broker._dispatch_queued_item(queue_id)

        self.assertFalse(self.generation_lease_is_released())
        release.set()
        self.wait_until(self.generation_lease_is_released)

    def test_distinct_hung_adapter_calls_have_a_global_bound(self):
        release = threading.Event()
        calls = []

        def hang(request):
            calls.append(deepcopy(request))
            release.wait()

        try:
            self.broker.adapter_call_timeout_seconds = 0.01
            for index in range(4):
                with self.assertRaises(TimeoutError):
                    self.broker._invoke_adapter_bounded(
                        f"hung-{index}", "retain_outcome", hang,
                        {"index": index},
                    )
            with self.assertRaises(TimeoutError):
                self.broker._invoke_adapter_bounded(
                    "hung-overflow", "retain_outcome", hang, {"index": 4}
                )
            self.assertEqual(len(calls), 4)
            with self.broker._adapter_calls_lock:
                active = sum(
                    not entry[2].done()
                    for entry in self.broker._adapter_calls.values()
                )
            self.assertEqual(active, 4)
        finally:
            release.set()

    def test_expired_session_is_retained_while_durable_work_is_recoverable(self):
        class UnavailableAdapter(FakeAdapter):
            def retain_outcome(self, request):
                raise AdapterError("temporarily unavailable")

        self._stop()
        now = [1000.0]
        self.adapter = UnavailableAdapter(endpoint=ENDPOINT)
        self._start(clock=lambda: now[0])
        capability = self.exchange()
        self.client.retain_outcome(
            capability,
            sequence=1,
            action_id="recoverable-retain",
            request={
                "document_id": "doc",
                "epoch": 1,
                "checkpoint": 1,
                "outcome": "done",
            },
        )
        self.wait_until(lambda: bool(self.work()["queue"]))
        self._stop()
        now[0] += 31
        self._start(clock=lambda: now[0])
        state = self.work()
        self.assertIn("session-1", state["sessions"])
        self.assertTrue(
            any(item["session_id"] == "session-1" for item in state["queue"])
        )

    def test_enqueue_failure_leaves_no_orphan_watermark(self):
        capability = self.exchange()
        original = __import__("hindsight_memory_control_plane.broker", fromlist=["_atomic_json"])._atomic_json
        def fail_work(path, value, **kwargs):
            if Path(path).name == "durable_work.json":
                raise OSError("simulated crash")
            return original(path, value, **kwargs)
        with patch("hindsight_memory_control_plane.broker._atomic_json", side_effect=fail_work):
            with self.assertRaisesRegex(BrokerError, "INTERNAL_ERROR"):
                self.client.retain_outcome(capability, sequence=1, action_id="before", request={"document_id": "doc", "epoch": 1, "checkpoint": 1, "outcome": "done"})
        state = self.work()
        self.assertEqual(state["queue"], [])
        self.assertEqual(state["completed"], {})
        retried = self.client.retain_outcome(capability, sequence=1, action_id="before", request={"document_id": "doc", "epoch": 1, "checkpoint": 1, "outcome": "done"})
        self.assertEqual(retried["disposition"], "queued")

    def test_post_replace_failure_adopts_committed_state_and_blocks_replay(self):
        capability = self.exchange()
        original_chmod = os.chmod
        failed = {"once": False}

        def fail_after_replace(path, mode, **kwargs):
            if Path(path).name == "durable_work.json" and not failed["once"]:
                failed["once"] = True
                raise OSError("post-replace permission failure")
            return original_chmod(path, mode, **kwargs)

        with patch(
            "hindsight_memory_control_plane.broker.os.chmod",
            side_effect=fail_after_replace,
        ):
            with self.assertRaises(OSError):
                self.broker.retain_outcome(
                    capability,
                    sequence=1,
                    action_id="post-replace",
                    request={
                        "document_id": "doc",
                        "epoch": 1,
                        "checkpoint": 1,
                        "outcome": "done",
                    },
                )
        self.assertEqual(self.work()["sessions"]["session-1"]["sequence"], 1)
        with self.assertRaisesRegex(BrokerError, "DIGEST_DRIFT"):
            self.broker.retain_outcome(
                capability,
                sequence=2,
                action_id="post-replace",
                request={
                    "document_id": "doc",
                    "epoch": 1,
                    "checkpoint": 1,
                    "outcome": "done",
                },
            )

    def test_digest_mirror_failure_cannot_undo_canonical_enqueue_ack(self):
        capability = self.exchange()
        original = __import__("hindsight_memory_control_plane.broker", fromlist=["_atomic_json"])._atomic_json
        def fail_mirror(path, value, **kwargs):
            if Path(path).name in {"used_nonces.json", "revoked_nonces.json"}:
                raise OSError("derived mirror unavailable")
            return original(path, value, **kwargs)
        with patch("hindsight_memory_control_plane.broker._atomic_json", side_effect=fail_mirror):
            response = self.client.retain_outcome(capability, sequence=1, action_id="mirror", request={"document_id": "doc", "epoch": 1, "checkpoint": 1, "outcome": "done"})
        self.assertEqual(response["disposition"], "queued")
        state = self.work()
        self.assertEqual(state["sessions"]["session-1"]["sequence"], 1)
        with self.assertRaisesRegex(BrokerError, "DIGEST_DRIFT"):
            self.client.retain_outcome(capability, sequence=2, action_id="mirror", request={"document_id": "doc", "epoch": 1, "checkpoint": 1, "outcome": "done"})
        self._stop()
        (self.state / "used_nonces.json").unlink(missing_ok=True)
        (self.state / "revoked_nonces.json").unlink(missing_ok=True)
        self._start()
        self.assertTrue((self.state / "used_nonces.json").exists())
        self.assertEqual(json.loads((self.state / "used_nonces.json").read_text()), self.work()["used_nonces"])

    def test_enqueue_revalidates_when_close_wins_after_preflight(self):
        capability = self.exchange()
        entered = threading.Event()
        release = threading.Event()
        original = self.broker._enqueue_watermarked
        def paused(*args, **kwargs):
            entered.set()
            release.wait(1)
            return original(*args, **kwargs)
        self.broker._enqueue_watermarked = paused
        failure = []
        thread = threading.Thread(target=lambda: self._capture_error(
            failure,
            lambda: self.broker.retain_outcome(capability, sequence=1, action_id="racing-retain", request={"document_id": "doc", "epoch": 1, "checkpoint": 1, "outcome": "done"}),
        ))
        thread.start()
        self.assertTrue(entered.wait(0.2))
        closed = self.broker.session_close(capability, sequence=2, action_id="racing-close", timeout_seconds=0)
        release.set()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(closed["disposition"], "closed")
        self.assertEqual(failure, ["REVOKED"])
        state = self.work()
        self.assertTrue(state["sessions"]["session-1"]["closed"])
        self.assertIn(state["sessions"]["session-1"]["revocation_digest"], state["revoked_nonces"])

    def test_concurrent_identical_close_replays_inside_atomic_transaction(self):
        capability = self.exchange()
        barrier = threading.Barrier(2)
        original_record = self.broker._session_close_ledger_record
        responses = []
        failures = []

        def synchronize_close(claims_value, action_id):
            barrier.wait(1)
            return original_record(claims_value, action_id)

        def close():
            try:
                responses.append(
                    self.broker.session_close(
                        capability,
                        sequence=1,
                        action_id="concurrent-close",
                        timeout_seconds=0,
                    )
                )
            except BrokerError as error:
                failures.append(error.code)

        threads = [threading.Thread(target=close) for _ in range(2)]
        with (
            patch.object(
                self.broker,
                "_session_close_ledger_record",
                side_effect=synchronize_close,
            ),
            patch.object(self.broker, "_submit_write") as submit,
        ):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(2)
                self.assertFalse(thread.is_alive())

        self.assertEqual(failures, [])
        self.assertEqual(len(responses), 2)
        self.assertTrue(all(
            response["disposition"] == "closed"
            for response in responses
        ))
        state = self.work()
        session = state["sessions"]["session-1"]
        self.assertTrue(session["closed"])
        self.assertEqual(session["action_ids"], ["concurrent-close"])
        queued = [
            item for item in state["queue"]
            if item["session_id"] == "session-1"
        ]
        self.assertEqual(queued, [])
        submit.assert_not_called()

    def test_close_is_retryable_before_atomic_commit_and_drainable_after_commit(self):
        capability = self.exchange()
        original_atomic = __import__("hindsight_memory_control_plane.broker", fromlist=["_atomic_json"])._atomic_json
        failed = {"once": False}
        def fail_commit(path, value, **kwargs):
            if Path(path).name == "durable_work.json" and not failed["once"]:
                failed["once"] = True
                raise OSError("before close commit")
            return original_atomic(path, value, **kwargs)
        with patch("hindsight_memory_control_plane.broker._atomic_json", side_effect=fail_commit):
            with self.assertRaises(OSError):
                self.broker.session_close(capability, sequence=1, action_id="close-retry", timeout_seconds=0)
        retried = self.broker.session_close(capability, sequence=1, action_id="close-retry", timeout_seconds=0)
        self.assertEqual(retried["disposition"], "closed")

        mint = self.client.session_mint("control", claims(session_id="close-after"), ttl_seconds=30)
        other = self.client.session_exchange(mint["payload"]["handle"])["payload"]["capability"]
        with patch.object(self.broker, "_submit_write"):
            queued_response = self.broker.transcript_checkpoint(
                other, sequence=1, action_id="close-after-checkpoint",
                request={
                    "document_id": "transcript", "epoch": 1,
                    "checkpoint": 1,
                    "content": "complete cleaned transcript",
                },
            )
        with patch.object(self.broker, "_submit_write", side_effect=OSError("after close commit")):
            with self.assertRaises(OSError):
                self.broker.session_close(other, sequence=2, action_id="close-after", timeout_seconds=0)
        state = self.work()
        self.assertTrue(state["sessions"]["close-after"]["closed"])
        queued = next(item for item in state["queue"] if item["queue_id"] == queued_response["payload"]["queue_id"])
        with patch.object(self.broker, "_submit_write") as submit:
            retry = self.broker.session_close(
                other, sequence=2, action_id="close-after",
                timeout_seconds=0,
            )
        self.assertEqual(retry["disposition"], "closed")
        submit.assert_called_once_with(queued["queue_id"], runtime=True)
        self._stop()
        self._start()
        self.wait_until(lambda: not any(
            item["session_id"] == "close-after" for item in self.work()["queue"]
        ))
        self.assertFalse(any(item["session_id"] == "close-after" for item in self.work()["queue"]))

    def test_close_retry_restores_its_durable_session_barrier_after_restart(self):
        capability = self.exchange()
        with patch.object(self.broker, "_submit_write"):
            self.broker.transcript_checkpoint(
                capability, sequence=1, action_id="restart-checkpoint",
                request={
                    "document_id": "transcript", "epoch": 1,
                    "checkpoint": 1,
                    "content": "complete cleaned transcript",
                },
            )
            first = self.broker.session_close(
                capability,
                sequence=2,
                action_id="restart-close",
                timeout_seconds=0,
            )
        self.assertEqual(first["payload"]["undrained"], 1)
        self._stop()

        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        class BlockingCheckpoint(FakeAdapter):
            def transcript_checkpoint(self, request):
                entered.set()
                release.wait(1)
                return super().transcript_checkpoint(request)

        self.adapter = BlockingCheckpoint(endpoint=ENDPOINT)
        self._start()
        self.assertTrue(entered.wait(0.5))
        pending = self.broker.session_close(
            capability,
            sequence=2,
            action_id="restart-close",
            timeout_seconds=0,
        )
        self.assertEqual(pending["payload"]["undrained"], 1)
        release.set()
        self.wait_until(lambda: not self.work()["queue"])
        drained = self.broker.session_close(
            capability,
            sequence=2,
            action_id="restart-close",
            timeout_seconds=1,
        )
        self.assertEqual(drained["payload"]["undrained"], 0)
        self.assertEqual(drained["payload"]["write_drain"], "drained")
        self.assertNotIn("final_checkpoint", drained["payload"])

    def test_close_persists_and_retries_ledger_outbox_before_success(self):
        self._stop()
        ledger = self.root / "controller.jsonl"
        bank = {**BANK, "endpoint": ENDPOINT}
        self.broker = Broker(
            state_dir=self.state, signing_key=b"z" * 32,
            routes={"local-core": {"bank": bank, "adapter": self.adapter}},
            policy_digest=DIGEST_A, artifact_digest=DIGEST_B,
            mint_authorizer=self.authorize_mint, ledger_path=ledger,
        )
        self.server = UnixJsonRpcServer(self.socket_path, self.broker)
        self.server.start()
        self.client = JsonRpcClient(self.socket_path)
        capability = self.exchange()
        real_append = __import__(
            "hindsight_memory_control_plane.broker", fromlist=["append_record_once"]
        ).append_record_once
        failures = {"remaining": 1}

        def flaky_append(path, record):
            if failures["remaining"]:
                failures["remaining"] -= 1
                raise OSError("ledger unavailable")
            return real_append(path, record)

        with patch(
            "hindsight_memory_control_plane.broker.append_record_once",
            side_effect=flaky_append,
        ):
            first = self.broker.session_close(
                capability, sequence=1, action_id="ledger-close",
                timeout_seconds=0,
            )
            self.assertEqual(first["disposition"], "unavailable")
            self.assertEqual(first["diagnostic"]["code"], "LEDGER_UNAVAILABLE")
            self.assertEqual(len(self.work()["ledger_outbox"]), 1)
            self.broker.ledger_path = None
            missing_destination = self.broker.session_close(
                capability, sequence=1, action_id="ledger-close",
                timeout_seconds=0,
            )
            self.assertEqual(missing_destination["disposition"], "unavailable")
            self.assertEqual(len(self.work()["ledger_outbox"]), 1)
            self.broker.ledger_path = ledger
            record = next(iter(self.work()["ledger_outbox"].values()))["record"]
            barrier = threading.Barrier(4)
            append_results = []
            append_errors = []

            def append_concurrently():
                try:
                    barrier.wait()
                    append_results.append(real_append(ledger, record))
                except Exception as error:
                    append_errors.append(error)

            appenders = [
                threading.Thread(target=append_concurrently) for _ in range(4)
            ]
            for thread in appenders:
                thread.start()
            for thread in appenders:
                thread.join(timeout=1)
                self.assertFalse(thread.is_alive())
            self.assertEqual(append_errors, [])
            self.assertEqual(append_results.count(True), 1)
            self.assertEqual(append_results.count(False), 3)
            with patch.object(
                self.broker, "_transaction",
                side_effect=OSError("crash after ledger append"),
            ):
                with self.assertRaisesRegex(OSError, "crash after ledger append"):
                    self.broker.session_close(
                        capability, sequence=1, action_id="ledger-close",
                        timeout_seconds=0,
                    )
            self.assertEqual(len(self.work()["ledger_outbox"]), 1)
            with self.assertRaisesRegex(BrokerError, "REVOKED"):
                self.broker.session_close(
                    capability, sequence=2, action_id="ledger-close",
                    timeout_seconds=0,
                )
            second = self.broker.session_close(
                capability, sequence=1, action_id="ledger-close",
                timeout_seconds=0,
            )
        self.assertEqual(second["disposition"], "closed")
        self.assertEqual(self.work()["ledger_outbox"], {})
        records = [json.loads(line) for line in ledger.read_text().splitlines()]
        self.assertEqual([record["action_id"] for record in records], ["ledger-close"])
        with self.assertRaisesRegex(LedgerError, "idempotency identity conflicts"):
            real_append(ledger, {**records[0], "reason_code": "CONFLICT"})

    def test_v2_closed_session_with_configured_ledger_fails_closed(self):
        capability = self.exchange()
        self.broker.session_close(
            capability, sequence=1, action_id="legacy-close",
            timeout_seconds=0,
        )
        self._stop()
        work_path = self.state / "durable_work.json"
        legacy_v2 = json.loads(work_path.read_text())
        legacy_v2["schema_version"] = 2
        legacy_v2.pop("ledger_outbox")
        legacy_v2.pop("expirations")
        for completed in legacy_v2["completed"].values():
            completed.pop("adapter_result")
        work_path.write_text(json.dumps(legacy_v2), encoding="utf-8")
        with self.assertRaisesRegex(BrokerError, "STATE_INVALID"):
            Broker(
                state_dir=self.state, signing_key=b"z" * 32,
                routes={"local-core": {
                    "bank": {**BANK, "endpoint": ENDPOINT},
                    "adapter": self.adapter,
                }},
                policy_digest=DIGEST_A, artifact_digest=DIGEST_B,
                mint_authorizer=authorize_mint,
                ledger_path=self.root / "controller.jsonl",
            )
        legacy_v2["schema_version"] = 3
        legacy_v2["ledger_outbox"] = {}
        work_path.write_text(json.dumps(legacy_v2), encoding="utf-8")
        self._start()

    def test_shutdown_retries_outbox_without_live_capability(self):
        self._stop()
        ledger = self.root / "shutdown-controller.jsonl"
        self.broker = Broker(
            state_dir=self.state, signing_key=b"z" * 32,
            routes={"local-core": {
                "bank": {**BANK, "endpoint": ENDPOINT},
                "adapter": self.adapter,
            }},
            policy_digest=DIGEST_A, artifact_digest=DIGEST_B,
            mint_authorizer=self.authorize_mint, ledger_path=ledger,
        )
        self.server = UnixJsonRpcServer(self.socket_path, self.broker)
        self.server.start()
        self.client = JsonRpcClient(self.socket_path)
        capability = self.exchange()
        with patch(
            "hindsight_memory_control_plane.broker.append_record_once",
            side_effect=OSError("temporarily unavailable"),
        ):
            response = self.broker.session_close(
                capability, sequence=1, action_id="shutdown-close",
                timeout_seconds=0,
            )
        self.assertEqual(response["disposition"], "unavailable")
        self.assertEqual(len(self.work()["ledger_outbox"]), 1)
        with patch(
            "hindsight_memory_control_plane.broker.append_record_once",
            side_effect=OSError("shutdown retry unavailable"),
        ):
            first_shutdown = self.broker.shutdown(timeout_seconds=0)
        self.assertTrue(first_shutdown["retirement_pending"])
        self.assertEqual(len(self.work()["ledger_outbox"]), 1)
        second_shutdown = self.broker.shutdown(timeout_seconds=0)
        self.assertFalse(second_shutdown["retirement_pending"])
        self.assertEqual(self.work()["ledger_outbox"], {})
        self.assertEqual(
            [json.loads(line)["action_id"] for line in ledger.read_text().splitlines()],
            ["shutdown-close"],
        )

    def test_shutdown_uses_one_finalizer_until_ledger_flush_and_retirement(self):
        self._stop()
        ledger = self.root / "deferred-shutdown-controller.jsonl"
        self.broker = Broker(
            state_dir=self.state, signing_key=b"z" * 32,
            routes={"local-core": {
                "bank": {**BANK, "endpoint": ENDPOINT},
                "adapter": self.adapter,
            }},
            policy_digest=DIGEST_A, artifact_digest=DIGEST_B,
            mint_authorizer=self.authorize_mint, ledger_path=ledger,
        )
        self.server = UnixJsonRpcServer(self.socket_path, self.broker)
        self.server.start()
        self.client = JsonRpcClient(self.socket_path)
        capability = self.exchange()
        with patch(
            "hindsight_memory_control_plane.broker.append_record_once",
            side_effect=OSError("close ledger unavailable"),
        ):
            self.broker.session_close(
                capability, sequence=1, action_id="deferred-close",
                timeout_seconds=0,
            )

        release = threading.Event()
        attempts = 0
        def delayed_append(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if not release.is_set():
                raise OSError("ledger remains unavailable")
            return append_record_once(*args, **kwargs)

        with patch(
            "hindsight_memory_control_plane.broker.append_record_once",
            side_effect=delayed_append,
        ):
            first = self.broker.shutdown(timeout_seconds=0)
            finalizer = self.broker._retirement_finalizer
            self.assertIsNotNone(finalizer)
            second = self.broker.shutdown(timeout_seconds=0)
            self.assertIs(self.broker._retirement_finalizer, finalizer)
            self.assertTrue(first["retirement_pending"])
            self.assertTrue(second["retirement_pending"])
            release.set()
            self.wait_until(lambda: not self.broker._retirement_pending)

        self.assertGreaterEqual(attempts, 2)
        self.assertIsNone(self.broker._retirement_finalizer)
        self.assertEqual(
            [json.loads(line)["action_id"] for line in ledger.read_text().splitlines()],
            ["deferred-close"],
        )

    def test_successful_shutdown_reports_generation_retired(self):
        shutdown = self.broker.shutdown(timeout_seconds=1)
        self.assertTrue(shutdown["retired"])
        self.assertFalse(shutdown["retirement_pending"])

    def test_submit_write_deduplicates_active_queue_future(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def drain(queue_id):
            calls.append(queue_id)
            entered.set()
            release.wait(1)

        with patch.object(self.broker, "_drain_item", side_effect=drain):
            self.broker._submit_write("f" * 32, runtime=True)
            self.assertTrue(entered.wait(1))
            self.broker._submit_write("f" * 32, runtime=True)
            self.assertEqual(calls, ["f" * 32])
            self.assertEqual(
                set(self.broker._write_futures_by_queue_id), {"f" * 32}
            )
            release.set()
            self.wait_until(
                lambda: "f" * 32
                not in self.broker._write_futures_by_queue_id
            )

    def test_submit_write_requeues_retry_that_arrives_before_active_drain_finishes(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def drain(queue_id):
            calls.append(queue_id)
            if len(calls) == 1:
                entered.set()
                release.wait(1)

        with patch.object(self.broker, "_drain_item", side_effect=drain):
            self.broker._submit_write("e" * 32, runtime=True)
            self.assertTrue(entered.wait(1))
            self.broker._submit_write("e" * 32, runtime=True)
            release.set()
            self.wait_until(lambda: len(calls) == 2)

        self.assertEqual(calls, ["e" * 32, "e" * 32])

    def test_mint_and_exchange_pre_admit_durable_state_limits(self):
        for limit in ("MAX_DURABLE_HANDLES", "MAX_DURABLE_HANDLE_BYTES"):
            with self.subTest(limit=limit), patch(
                f"hindsight_memory_control_plane.broker.{limit}", 0
            ):
                with self.assertRaisesRegex(BrokerError, "STATE_FULL"):
                    self.client.session_mint(
                        "control",
                        claims(session_id=f"{limit.lower()}-full"),
                        ttl_seconds=30,
                    )
        for limit, capacity in (
            ("MAX_DURABLE_SESSIONS", 0),
            ("MAX_DURABLE_SESSION_BYTES", len(canonical_bytes({}))),
            ("MAX_DURABLE_EXCHANGES", 0),
            ("MAX_DURABLE_EXCHANGE_BYTES", len(canonical_bytes({}))),
            ("MAX_DURABLE_NONCES", 0),
            ("MAX_DURABLE_NONCE_BYTES", len(canonical_bytes([]))),
        ):
            with self.subTest(limit=limit):
                minted = self.client.session_mint(
                    "control",
                    claims(session_id=f"{limit.lower()}-full"),
                    ttl_seconds=30,
                )
                with patch(
                    f"hindsight_memory_control_plane.broker.{limit}",
                    capacity,
                ):
                    with self.assertRaisesRegex(BrokerError, "STATE_FULL"):
                        self.client.session_exchange(
                            minted["payload"]["handle"]
                        )

    @staticmethod
    def _capture_error(target, operation):
        try:
            operation()
        except BrokerError as error:
            target.append(error.code)

    def test_restart_replays_after_enqueue_with_same_idempotency_key(self):
        class FailOnceFake(FakeAdapter):
            def __init__(self):
                super().__init__(endpoint=ENDPOINT)
                self.fail = True
            def retain_outcome(self, request):
                if self.fail:
                    self.fail = False
                    raise SystemExit("crash after enqueue")
                return super().retain_outcome(request)
        self._stop()
        self.adapter = FailOnceFake()
        self._start()
        capability = self.exchange()
        response = self.client.retain_outcome(capability, sequence=1, action_id="after-enqueue", request={"document_id": "doc", "epoch": 1, "checkpoint": 1, "outcome": "done"})
        key = response["action_digest"]
        self.wait_until(lambda: self.writes_drained(self.broker))
        self.assertEqual(self.work()["queue"][0]["idempotency_key"], key)
        self._stop()
        self._start()
        self.wait_until(lambda: self.work()["queue"] == [])
        self.assertEqual(self.work()["queue"], [])
        retain_calls = [entry for entry in self.adapter.calls if entry["method"] == "retain_outcome"]
        self.assertEqual(len(retain_calls), 1)

    def test_restart_after_adapter_success_before_dequeue_reuses_key(self):
        class CrashAfterSuccess(FakeAdapter):
            def __init__(self):
                super().__init__(endpoint=ENDPOINT)
                self.crash = True
            def retain_outcome(self, request):
                result = super().retain_outcome(request)
                if self.crash:
                    self.crash = False
                    raise SystemExit("crash window")
                return result
        self._stop()
        self.adapter = CrashAfterSuccess()
        self._start()
        capability = self.exchange()
        response = self.client.retain_outcome(capability, sequence=1, action_id="after-success", request={"document_id": "doc", "epoch": 1, "checkpoint": 1, "outcome": "done"})
        self.wait_until(lambda: self.writes_drained(self.broker))
        self.assertEqual(self.work()["queue"][0]["idempotency_key"], response["action_digest"])
        self._stop()
        self._start()
        self.wait_until(lambda: self.work()["queue"] == [])
        self.assertEqual(self.work()["queue"], [])
        self.assertEqual(len([entry for entry in self.adapter.calls if entry["method"] == "retain_outcome"]), 1)

    def test_concurrent_older_watermark_cannot_replace_newer(self):
        capability = self.exchange()
        barrier = threading.Barrier(3)
        outcomes = []
        def send(sequence, action, epoch):
            client = JsonRpcClient(self.socket_path)
            barrier.wait()
            try:
                outcomes.append(client.transcript_checkpoint(
                    capability, sequence=sequence, action_id=action,
                    request={
                        "document_id": "doc", "epoch": epoch,
                        "checkpoint": 1, "content": action,
                    },
                ))
            except BrokerError as error:
                outcomes.append(error.code)
        threads = [threading.Thread(target=send, args=(1, "older", 1)), threading.Thread(target=send, args=(2, "newer", 2))]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive())
        self.wait_until(lambda: self.writes_drained(self.broker))
        state = self.work()
        records = list(state["completed"].values()) + state["queue"]
        self.assertEqual(max(tuple(record["watermark"]) for record in records), (2, 1))
        self.assertFalse(any(tuple(record["watermark"]) > (2, 1) for record in records))
        newest = next(
            record for record in records if record["watermark"] == [2, 1]
        )
        newer_request = {
            "document_id": "doc",
            "epoch": 2,
            "checkpoint": 1,
            "content": "newer",
        }
        newer_digest = hashlib.sha256(canonical_bytes(newer_request)).hexdigest()
        self.assertEqual(newest["request_digest"], newer_digest)
        self.assertFalse(
            any(
                record["watermark"] == [2, 1]
                and record["request_digest"] != newer_digest
                for record in records
            )
        )

    def test_same_watermark_retry_requires_matching_idempotency_identity(self):
        capability = self.exchange()
        with patch.object(self.broker, "_submit_write") as submit:
            first = self.client.transcript_checkpoint(capability, sequence=1, action_id="first", request={"document_id": "doc", "epoch": 2, "checkpoint": 1, "content": "new"})
            retry = self.client.transcript_checkpoint(capability, sequence=1, action_id="first", request={"document_id": "doc", "epoch": 2, "checkpoint": 1, "content": "new"})
        self.assertEqual(retry["disposition"], "idempotent")
        self.assertEqual(retry["action_digest"], first["action_digest"])
        self.assertEqual(retry["payload"]["queue_id"], first["payload"]["queue_id"])
        self.assertEqual(
            [item.args[0] for item in submit.call_args_list],
            [first["payload"]["queue_id"], first["payload"]["queue_id"]],
        )
        stale = self.client.transcript_checkpoint(capability, sequence=2, action_id="stale", request={"document_id": "doc", "epoch": 1, "checkpoint": 1, "content": "old"})
        self.assertEqual(stale["disposition"], "stale")
        with self.assertRaisesRegex(BrokerError, "DIGEST_DRIFT"):
            self.client.transcript_checkpoint(capability, sequence=3, action_id="same", request={"document_id": "doc", "epoch": 2, "checkpoint": 1, "content": "new"})

    def test_reflect_timeout_is_not_replayed_or_persisted(self):
        capability = self.exchange()
        with patch.object(
            self.adapter, "reflect",
            side_effect=lambda _request: (
                time.sleep(0.05) or {"reflection": "summary"}
            ),
        ):
            first = self.client.reflect(
                capability, sequence=1, action_id="reflect-retry",
                request={"reflection": "summary"}, timeout_seconds=0,
            )
            with self.assertRaisesRegex(BrokerError, "ACTION_REPLAY"):
                self.client.reflect(
                    capability, sequence=1, action_id="reflect-retry",
                    request={"reflection": "summary"}, timeout_seconds=0,
                )

        self.assertEqual(first["disposition"], "unavailable")
        durable = (self.state / "durable_work.json").read_text()
        self.assertNotIn("summary", durable)
        self.assertEqual(self.work()["queue"], [])

    def test_reflect_replay_revalidates_live_session_authorization(self):
        def complete_reflect(session_id, action_id):
            capability = self.exchange(session_id=session_id)
            response = self.client.reflect(
                capability, sequence=1, action_id=action_id,
                request={"reflection": "summary"}, timeout_seconds=1,
            )
            self.assertEqual(response["disposition"], "ok")
            return capability

        revoked = complete_reflect("reflect-revoked", "reflect-revoked")
        self.client.session_close(
            revoked, sequence=2, action_id="close-reflect-revoked"
        )
        with self.assertRaisesRegex(BrokerError, "REVOKED"):
            self.client.reflect(
                revoked, sequence=1, action_id="reflect-revoked",
                request={"reflection": "summary"}, timeout_seconds=0,
            )

        wrong_nonce = complete_reflect("reflect-nonce", "reflect-nonce")
        with self.broker._lock:
            work_wrong_nonce = deepcopy(self.broker._work)
        work_without_action = deepcopy(work_wrong_nonce)
        work_wrong_nonce["sessions"]["reflect-nonce"]["nonce_digest"] = "f" * 64
        claims_value = self.broker._verify(wrong_nonce, "capability")
        with self.assertRaisesRegex(BrokerError, "CAPABILITY_INVALID"):
            self.broker._validate_committed_replay(
                work_wrong_nonce, claims_value, "reflect-nonce"
            )

        work_without_action["sessions"]["reflect-nonce"]["action_ids"] = []
        with self.assertRaisesRegex(BrokerError, "STATE_INVALID"):
            self.broker._validate_committed_replay(
                work_without_action, claims_value, "reflect-nonce"
            )

    def test_retry_delay_uses_bounded_scheduler_and_shutdown_cancels_it(self):
        capability = self.exchange()
        with patch.object(self.broker, "_submit_write"):
            queued = self.client.retain_outcome(
                capability, sequence=1, action_id="delayed-retry",
                request={
                    "document_id": "retry-doc", "epoch": 1,
                    "checkpoint": 1, "outcome": "queued",
                },
            )
        queue_id = queued["payload"]["queue_id"]
        with patch.object(
            self.broker,
            "_dispatch_queued_item",
            return_value=("retry", 10.0),
        ):
            self.broker._submit_write(queue_id, runtime=True)
            self.wait_until(
                lambda: queue_id in self.broker._write_retry_deadlines
                and not self.broker._write_futures
            )
            self.assertTrue(self.broker._write_retry_thread.is_alive())
            self.assertLessEqual(
                len(self.broker._write_retry_heap),
                len(self.broker._write_retry_deadlines),
            )
            shutdown = self.broker.shutdown(timeout_seconds=0)

        self.assertEqual(self.broker._write_retry_deadlines, {})
        self.assertEqual(self.broker._write_retry_heap, [])
        self.assertEqual(shutdown["active_writes"], 0)

    def test_drain_item_recovers_publication_errors_as_retry_success_or_retired(self):
        capability = self.exchange()
        with patch.object(self.broker, "_submit_write"):
            queued = self.client.retain_outcome(
                capability,
                sequence=1,
                action_id="publication-error",
                request={
                    "document_id": "publication-error",
                    "epoch": 1,
                    "checkpoint": 1,
                    "outcome": "queued",
                },
            )
        queue_id = queued["payload"]["queue_id"]

        with (
            patch.object(
                self.broker,
                "_dispatch_queued_item",
                side_effect=OSError("before replace"),
            ),
            patch(
                "hindsight_memory_control_plane.broker.secrets.randbelow",
                side_effect=(0, 500),
            ),
            patch.object(self.broker, "_schedule_write_retry") as retry,
        ):
            self.broker._drain_item(queue_id)
            self.broker._drain_item(queue_id)
        self.assertEqual(
            retry.call_args_list,
            [call(queue_id, 0.25 * 0.75), call(queue_id, 0.5 * 1.25)],
        )
        retried = next(
            item for item in self.work()["queue"]
            if item["queue_id"] == queue_id
        )
        self.assertEqual(retried["attempts"], 2)
        self.assertEqual(retried["last_error"], "ADAPTER_UNAVAILABLE")

        def completed_then_failed(_queue_id):
            self.broker._transaction(
                lambda work: work.update(queue=[]), runtime=True
            )
            raise OSError("after replace")

        with (
            patch.object(
                self.broker,
                "_dispatch_queued_item",
                side_effect=completed_then_failed,
            ),
            patch.object(self.broker, "_release_write_dependents") as release,
        ):
            self.broker._drain_item(queue_id)
        release.assert_called_once_with(queue_id)

        with (
            patch.object(
                self.broker,
                "_dispatch_queued_item",
                side_effect=OSError("retired"),
            ),
            patch.object(
                self.broker,
                "_recover_write_publication_error",
                return_value=("retired", 0.0),
            ),
            patch.object(self.broker, "_schedule_write_retry") as retry,
            patch.object(self.broker, "_release_write_dependents") as release,
        ):
            self.broker._drain_item(queue_id)
        retry.assert_not_called()
        release.assert_not_called()

    def test_exchange_recovers_same_capability_after_unlink_crash(self):
        mint = self.client.session_mint("control", claims(), ttl_seconds=30)
        handle = mint["payload"]["handle"]
        original_unlink = os.unlink
        def fail_handle(path, *args, **kwargs):
            if Path(path).name == f"{handle}.json":
                raise OSError("crash after state commit")
            return original_unlink(path, *args, **kwargs)
        with patch(
            "hindsight_memory_control_plane.broker.os.unlink",
            side_effect=fail_handle,
        ):
            with self.assertRaisesRegex(BrokerError, "INTERNAL_ERROR"):
                self.client.session_exchange(handle)
        recovered = self.client.session_exchange(handle)
        again = self.client.session_exchange(handle)
        self.assertEqual(recovered["payload"]["capability"], again["payload"]["capability"])

    def test_exchange_recovery_allows_same_handle_but_rejects_second_session_capability(self):
        first = self.client.session_mint("control", claims(), ttl_seconds=30)
        second = self.client.session_mint("control", claims(), ttl_seconds=30)
        exchanged = self.client.session_exchange(first["payload"]["handle"])
        recovered = self.client.session_exchange(first["payload"]["handle"])
        self.assertEqual(
            exchanged["payload"]["capability"],
            recovered["payload"]["capability"],
        )
        with self.assertRaisesRegex(BrokerError, "SESSION_ACTIVE"):
            self.client.session_exchange(second["payload"]["handle"])

    def test_shutdown_fences_every_runtime_transition(self):
        capability = self.exchange()
        staged = self.client.session_mint(
            "control", claims(session_id="staged"), ttl_seconds=30
        )
        self.broker.shutdown(timeout_seconds=0)

        operations = (
            lambda: self.broker.session_exchange(staged["payload"]["handle"]),
            lambda: self.broker.recall(
                capability, sequence=1, action_id="closed-recall",
                request={"query": "q"},
            ),
            lambda: self.broker.transcript_checkpoint(
                capability, sequence=1, action_id="closed-write",
                request={
                    "document_id": "doc", "epoch": 1, "checkpoint": 1,
                    "content": "complete cleaned transcript",
                },
            ),
            lambda: self.broker.reflect(
                capability, sequence=1, action_id="closed-reflect",
                request={"reflection": "note"},
            ),
            lambda: self.broker.session_status(
                capability, sequence=1, action_id="closed-status"
            ),
            lambda: self.broker.session_close(
                capability, sequence=1, action_id="closed-close"
            ),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(BrokerError, "BROKER_CLOSED"):
                    operation()

    def test_retry_backoff_is_exponential_jittered_and_persisted(self):
        capability = self.exchange()
        now = [100.0]
        self.broker.clock = lambda: now[0]
        with patch.object(self.broker, "_submit_write"):
            response = self.client.retain_outcome(
                capability,
                sequence=1,
                action_id="retry-backoff",
                request={
                    "document_id": "doc",
                    "epoch": 1,
                    "checkpoint": 1,
                    "outcome": "done",
                },
            )
        queue_id = response["payload"]["queue_id"]
        with (
            patch.object(
                self.adapter,
                "retain_outcome",
                side_effect=RuntimeError("adapter unavailable"),
            ),
            patch(
                "hindsight_memory_control_plane.broker.secrets.randbelow",
                side_effect=(0, 500),
            ),
        ):
            disposition, first_delay = self.broker._dispatch_queued_item(
                queue_id
            )
            first = self.work()["queue"][0]
            now[0] = first["next_retry"]
            disposition_again, second_delay = (
                self.broker._dispatch_queued_item(queue_id)
            )

        second = self.work()["queue"][0]
        self.assertEqual((disposition, disposition_again), ("retry", "retry"))
        self.assertEqual(first["attempts"], 1)
        self.assertEqual(second["attempts"], 2)
        self.assertAlmostEqual(first_delay, 0.25 * 0.75)
        self.assertAlmostEqual(second_delay, 0.5 * 1.25)
        self.assertAlmostEqual(first["next_retry"], 100.0 + first_delay)
        self.assertAlmostEqual(
            second["next_retry"], now[0] + second_delay
        )
        self.assertLessEqual(second_delay, 30.0)

    def test_missing_route_is_persisted_for_retry(self):
        capability = self.exchange()
        now = [100.0]
        self.broker.clock = lambda: now[0]
        with patch.object(self.broker, "_submit_write"):
            response = self.client.retain_outcome(
                capability,
                sequence=1,
                action_id="missing-route",
                request={
                    "document_id": "doc",
                    "epoch": 1,
                    "checkpoint": 1,
                    "outcome": "done",
                },
            )
        queue_id = response["payload"]["queue_id"]
        self.broker.routes.clear()
        with patch(
            "hindsight_memory_control_plane.broker.secrets.randbelow",
            return_value=0,
        ):
            disposition, delay = self.broker._dispatch_queued_item(queue_id)

        queued = self.work()["queue"]
        self.assertEqual(disposition, "retry")
        self.assertAlmostEqual(delay, 0.25 * 0.75)
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["attempts"], 1)
        self.assertEqual(queued[0]["last_error"], "ADAPTER_UNAVAILABLE")
        self.assertAlmostEqual(queued[0]["next_retry"], now[0] + delay)

    def test_checkpoint_state_key_stays_in_private_broker_state(self):
        capability = self.exchange()
        checkpoint = self.client.transcript_checkpoint(
            capability, sequence=1, action_id="private-final-checkpoint",
            request={
                "document_id": "transcript", "epoch": 1,
                "checkpoint": 1,
                "content": "complete cleaned transcript",
            },
        )
        closed = self.client.session_close(
            capability,
            sequence=2,
            action_id="private-close",
            timeout_seconds=1,
        )
        self.assertNotIn("state_key", json.dumps(closed))
        self.assertNotIn("state_key", json.dumps(checkpoint))
        final_calls = [
            entry for entry in self.adapter.calls
            if entry["method"] == "transcript_checkpoint"
        ]
        self.assertEqual(len(final_calls), 1)
        self.assertNotIn("state_key", final_calls[0]["metadata"]["keys"])
        completed_keys = tuple(self.work()["completed"])
        self.assertEqual(len(completed_keys), 1)
        self.assertTrue(completed_keys[0].startswith("checkpoint:"))

    def test_close_reports_slow_undrained_durable_work(self):
        class SlowWriteFake(FakeAdapter):
            def retain_outcome(self, request):
                time.sleep(0.1)
                return super().retain_outcome(request)
        self._stop()
        self.adapter = SlowWriteFake(endpoint=ENDPOINT)
        self._start()
        capability = self.exchange()
        self.client.retain_outcome(capability, sequence=1, action_id="retain-close", request={"document_id": "doc", "epoch": 1, "checkpoint": 1, "outcome": "done"})
        closed = self.client.session_close(capability, sequence=2, action_id="close", timeout_seconds=0)
        self.assertEqual(closed["disposition"], "closed")
        self.assertGreaterEqual(closed["payload"]["undrained"], 1)

    def test_inflight_dispatch_does_not_block_unrelated_transactions(self):
        release = threading.Event()
        self.addCleanup(release.set)
        started = threading.Event()

        class FencedFake(FakeAdapter):
            def retain_outcome(self, request):
                started.set()
                release.wait()
                return super().retain_outcome(request)

        completed = threading.Event()
        results = []
        thread = None

        def mint_unrelated_session():
            results.append(
                self.broker.session_mint(
                    "control",
                    claims(session_id="parallel-session"),
                    ttl_seconds=30,
                )
            )
            completed.set()

        try:
            self._stop()
            self.adapter = FencedFake(endpoint=ENDPOINT)
            self._start()
            capability = self.exchange()
            self.client.retain_outcome(
                capability,
                sequence=1,
                action_id="responsive-fence",
                request={
                    "document_id": "doc",
                    "epoch": 1,
                    "checkpoint": 1,
                    "outcome": "done",
                },
            )
            self.assertTrue(started.wait(0.2))
            thread = threading.Thread(target=mint_unrelated_session)
            thread.start()
            self.assertTrue(completed.wait(0.2))
            self.assertEqual(results[0]["disposition"], "ok")
        finally:
            release.set()
            if thread is not None:
                thread.join(1)
        self.assertIsNotNone(thread)
        self.assertFalse(thread.is_alive())

    def test_shutdown_defers_retirement_while_replacement_waits_for_dispatch(self):
        release = threading.Event()
        self.addCleanup(release.set)
        started = threading.Event()
        class FencedFake(FakeAdapter):
            def retain_outcome(self, request):
                if not started.is_set():
                    started.set()
                    release.wait()
                return super().retain_outcome(request)
        self._stop()
        self.adapter = FencedFake(endpoint=ENDPOINT)
        self._start()
        capability = self.exchange()
        self.client.retain_outcome(capability, sequence=1, action_id="fenced", request={"document_id": "doc", "epoch": 1, "checkpoint": 1, "outcome": "done"})
        self.assertTrue(started.wait(0.2))
        self.server.close()
        retired_broker = self.broker
        shutdown_entered = threading.Event()
        replacement_entered = threading.Event()
        shutdown_results = []
        replacements = []

        def shutdown():
            shutdown_entered.set()
            shutdown_results.append(
                retired_broker.shutdown(timeout_seconds=0)
            )

        def replace():
            replacement_entered.set()
            replacements.append(Broker(
                state_dir=self.state,
                signing_key=b"z" * 32,
                routes={
                    "local-core": {"bank": BANK, "adapter": self.adapter}
                },
                policy_digest=DIGEST_A,
                artifact_digest=DIGEST_B,
                mint_authorizer=authorize_mint,
            ))

        shutdown_thread = threading.Thread(target=shutdown)
        replacement_thread = threading.Thread(target=replace)
        shutdown_thread.start()
        replacement_thread.start()
        try:
            self.assertTrue(shutdown_entered.wait(0.2))
            self.assertTrue(replacement_entered.wait(0.2))
            shutdown_thread.join(0.2)
            self.assertFalse(shutdown_thread.is_alive())
            self.assertEqual(len(shutdown_results), 1)
            self.assertTrue(
                shutdown_results[0]["retirement_pending"]
            )
            repeated = retired_broker.shutdown(timeout_seconds=0)
            self.assertGreaterEqual(repeated["active_writes"], 1)
            self.assertTrue(repeated["retirement_pending"])
            self.assertTrue(replacement_thread.is_alive())
        finally:
            release.set()
            shutdown_thread.join(1)
            replacement_thread.join(1)
        self.assertFalse(shutdown_thread.is_alive())
        self.assertFalse(replacement_thread.is_alive())
        self.assertEqual(len(shutdown_results), 1)
        self.assertEqual(len(replacements), 1)
        self.wait_until(lambda: not retired_broker._retirement_pending)

        self.broker = replacements[0]
        self.server = UnixJsonRpcServer(self.socket_path, self.broker)
        self.server.start()
        self.client = JsonRpcClient(self.socket_path)
        self.assertEqual(self.work()["generation"], self.broker._generation)
        self.assertEqual(self.work()["queue"], [])

    def test_deferred_retirement_stays_pending_after_unexpected_failure(self):
        self.broker._retirement_pending = True
        with (
            patch.object(
                self.broker,
                "_retire_generation",
                side_effect=BrokerError("STATE_INVALID"),
            ),
            self.assertLogs(
                "hindsight_memory_control_plane.broker",
                level="ERROR",
            ) as broker_logs,
        ):
            self.broker._finish_deferred_retirement()
        self.assertTrue(self.broker._retirement_pending)
        self.assertIn("STATE_INVALID", broker_logs.output[0])

        with (
            patch.object(
                self.broker,
                "_retire_generation",
                side_effect=OSError("private detail"),
            ),
            self.assertLogs(
                "hindsight_memory_control_plane.broker",
                level="ERROR",
            ) as os_logs,
        ):
            self.broker._finish_deferred_retirement()
        self.assertTrue(self.broker._retirement_pending)
        self.assertIn("OS_ERROR", os_logs.output[0])
        self.assertNotIn("private detail", os_logs.output[0])

        with patch.object(
            self.broker,
            "_retire_generation",
            side_effect=BrokerError("BROKER_RETIRED"),
        ):
            self.broker._finish_deferred_retirement()
        self.assertFalse(self.broker._retirement_pending)

    def test_replacement_generation_fences_all_old_broker_state_transitions(self):
        capability = self.exchange()
        self.client.recall(capability, sequence=1, action_id="preserved-read", request={"query": "q"})
        mint = self.client.session_mint("control", claims(session_id="pending-exchange"), ttl_seconds=30)
        handle = mint["payload"]["handle"]
        before = self.work()
        replacement = Broker(
            state_dir=self.state, signing_key=b"z" * 32,
            routes={"local-core": {"bank": BANK, "adapter": self.adapter}},
            policy_digest=DIGEST_A, artifact_digest=DIGEST_B, mint_authorizer=authorize_mint,
        )
        try:
            after_start = self.work()
            self.assertEqual(after_start["sessions"], before["sessions"])
            self.assertEqual(after_start["queue"], before["queue"])
            self.assertEqual(after_start["completed"], before["completed"])
            with self.assertRaisesRegex(BrokerError, "BROKER_RETIRED"):
                self.broker.session_exchange(handle)
            for operation in (
                lambda: self.broker.retain_outcome(capability, sequence=2, action_id="old-enqueue", request={"document_id": "doc", "epoch": 2, "checkpoint": 1, "outcome": "old"}),
                lambda: self.broker.retain_outcome(capability, sequence=2, action_id="old-stale", request={"document_id": "doc", "epoch": 0, "checkpoint": 1, "outcome": "old"}),
                lambda: self.broker.session_close(capability, sequence=2, action_id="old-close", timeout_seconds=0),
            ):
                with self.assertRaisesRegex(BrokerError, "BROKER_RETIRED"):
                    operation()
            shutdown = self.broker.shutdown(timeout_seconds=0)
            self.assertTrue(shutdown["retired"])
            self.assertEqual(self.work()["generation"], after_start["generation"])
            self.assertEqual(self.work()["sessions"], after_start["sessions"])
        finally:
            replacement.shutdown(timeout_seconds=1)

    def test_staged_mint_is_generation_bound_and_closed_brokers_cannot_mint(self):
        staged = self.client.session_mint("control", claims(session_id="staged-old"), ttl_seconds=30)
        handle = staged["payload"]["handle"]
        replacement = Broker(
            state_dir=self.state, signing_key=b"z" * 32,
            routes={"local-core": {"bank": BANK, "adapter": self.adapter}},
            policy_digest=DIGEST_A, artifact_digest=DIGEST_B, mint_authorizer=authorize_mint,
        )
        try:
            with self.assertRaisesRegex(BrokerError, "BROKER_RETIRED"):
                replacement.session_exchange(handle)
            handles = set((self.state / "handles").glob("*.json"))
            with self.assertRaisesRegex(BrokerError, "BROKER_RETIRED"):
                self.broker.session_mint("control", claims(session_id="retired-mint"), ttl_seconds=30)
            self.assertEqual(set((self.state / "handles").glob("*.json")), handles)
            current = replacement.session_mint("control", claims(session_id="current-mint"), ttl_seconds=30)
            exchanged = replacement.session_exchange(current["payload"]["handle"])
            self.assertIn("capability", exchanged["payload"])
        finally:
            replacement.shutdown(timeout_seconds=1)
        shutdown = self.broker.shutdown(timeout_seconds=0)
        self.assertTrue(shutdown["retired"])
        with self.assertRaisesRegex(BrokerError, "BROKER_CLOSED"):
            self.broker.session_mint("control", claims(session_id="closed-mint"), ttl_seconds=30)

    def test_staged_mint_holds_generation_lease_against_replacement_race(self):
        entered = threading.Event()
        release = threading.Event()
        original = __import__("hindsight_memory_control_plane.broker", fromlist=["_atomic_json"])._atomic_json
        def pause_handle(path, value, **kwargs):
            if Path(path).parent.name == "handles" and not entered.is_set():
                entered.set()
                release.wait(1)
            return original(path, value, **kwargs)
        minted = []
        replacements = []
        replacement_entered = threading.Event()

        def replace():
            replacement_entered.set()
            replacements.append(Broker(
                state_dir=self.state,
                signing_key=b"z" * 32,
                routes={
                    "local-core": {"bank": BANK, "adapter": self.adapter}
                },
                policy_digest=DIGEST_A,
                artifact_digest=DIGEST_B,
                mint_authorizer=authorize_mint,
            ))

        with patch("hindsight_memory_control_plane.broker._atomic_json", side_effect=pause_handle):
            mint_thread = threading.Thread(target=lambda: minted.append(
                self.broker.session_mint("control", claims(session_id="racing-mint"), ttl_seconds=30)
            ))
            mint_thread.start()
            self.assertTrue(entered.wait(0.2))
            replacement_thread = threading.Thread(target=replace)
            replacement_thread.start()
            self.assertTrue(replacement_entered.wait(0.2))
            self.assertTrue(replacement_thread.is_alive())
            release.set()
            mint_thread.join(timeout=1)
            replacement_thread.join(timeout=1)
            self.assertFalse(mint_thread.is_alive())
            self.assertFalse(replacement_thread.is_alive())
        replacement = replacements[0]
        try:
            handle = minted[0]["payload"]["handle"]
            with self.assertRaisesRegex(BrokerError, "BROKER_RETIRED"):
                replacement.session_exchange(handle)
        finally:
            replacement.shutdown(timeout_seconds=1)


if __name__ == "__main__":
    unittest.main()
