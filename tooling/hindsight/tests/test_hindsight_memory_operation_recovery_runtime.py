from contextlib import asynccontextmanager, contextmanager
from builtins import BaseExceptionGroup
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import tracemalloc
from types import SimpleNamespace
import unittest

from tooling.hindsight.tests import (
    test_hindsight_memory_operation_recovery as recovery_fixtures,
)

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from hindsight_memory_control_plane import operation_recovery_runtime  # noqa: E402
from hindsight_memory_control_plane.operation_recovery_runtime import (  # noqa: E402
    GLOBAL_QUEUE_BLOCKER_QUERY,
    QUEUE_BLOCKER_GUARD_CONTRACT_DIGEST,
    QUEUE_BLOCKER_GUARD_CONTRACT_VERSION,
    QUEUE_BLOCKER_PREDICATE,
    CLAIM_RELEASE_EVIDENCE_QUERY,
    ExactDrainClaimAdapter,
    ExactDrainWorkerMainShutdownBridge,
    SAFE_OPERATION_QUERY,
    apply_requeue_transaction,
    apply_post_abort_recovery_transaction,
    assert_connected_live_database,
    connect_verified_local_postgres,
    exact_drain_worker_interpreter_path,
    exact_drain_worker_failure_evidence,
    install_exact_drain_runtime_guards,
    live_row_digest,
    read_global_queue_blockers,
    read_claim_release_evidence,
    read_snapshot,
    rollback_requeue_transaction,
    _exact_drain_interpreter_evidence,
    _postgres_safe_error_text,
)
from hindsight_memory_control_plane.operation_recovery import (  # noqa: E402
    OperationRecoveryError,
)


class _RunCapableWorkerPoller:
    async def run(self):
        raise AssertionError("test worker poller run seam was not configured")


class _ControlConnectionAdapterMixin:
    async def reserve_control_connection(self, _backend):
        return None

    async def close_control_connection(self):
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.transaction_arguments = None
        self.fetch_calls = []
        self.generation_reads = 0

    @asynccontextmanager
    async def transaction(self, **arguments):
        self.transaction_arguments = arguments
        yield

    async def fetchrow(self, query, *arguments):
        self.generation_reads += 1
        return {
            "generation": 123,
            "missing_trigger_count": 0,
            "reserved_guard_count": 0,
        }

    async def fetch(self, query, *arguments):
        self.fetch_calls.append((query, arguments))
        return [
            {
                "operation_id": "00000000-0000-4000-8000-000000000001",
                "bank_id": "engineering",
                "operation_type": "retain",
                "status": "failed",
                "created_at": "2026-07-29T12:00:00.000000Z",
                "updated_at": "2026-07-29T13:00:00.000000Z",
                "completed_at": "2026-07-29T13:00:00.000000Z",
                "retry_count": 1,
                "next_retry_at": None,
                "worker_id_present": False,
                "worker_id_digest": None,
                "claimed_at": None,
                "task_payload_present": True,
                "task_payload_digest": "a" * 64,
                "result_metadata_digest": "b" * 64,
                "error_category": "provider_capacity",
                "error_digest": "c" * 64,
            }
        ]


class OperationRecoveryRuntimeTest(unittest.TestCase):
    def test_worker_failure_classifies_exact_drain_execution_lease_expiry(self):
        evidence = exact_drain_worker_failure_evidence(
            OperationRecoveryError(
                "operation-recovery exact drain execution lease expired"
            )
        )

        self.assertEqual(evidence["category"], "execution_lease_expired")
        self.assertFalse(evidence["retryable"])
        self.assertIsNone(evidence["http_status"])
        self.assertEqual(
            evidence["error_digest"],
            hashlib.sha256(
                (
                    "OperationRecoveryError: operation-recovery exact drain "
                    "execution lease expired"
                ).encode("utf-8")
            ).hexdigest(),
        )
        self.assertEqual(
            evidence["error_digest"],
            "2814599b21d040f684426dfa56cf6036e3f9bd90eb629aa355d27a33ed41221f",
        )
        self.assertEqual(
            set(evidence),
            {"category", "retryable", "http_status", "error_digest"},
        )

    def test_worker_failure_classifies_operation_attempt_timeout(self):
        evidence = exact_drain_worker_failure_evidence(
            OperationRecoveryError(
                operation_recovery_runtime.EXACT_DRAIN_OPERATION_ATTEMPT_TIMEOUT_MESSAGE
            )
        )

        self.assertEqual(evidence["category"], "operation_attempt_timeout")
        self.assertFalse(evidence["retryable"])
        self.assertIsNone(evidence["http_status"])
        self.assertEqual(
            evidence["error_digest"],
            hashlib.sha256(
                operation_recovery_runtime.EXACT_DRAIN_OPERATION_ATTEMPT_TIMEOUT_MESSAGE.encode(
                    "utf-8"
                )
            ).hexdigest(),
        )

    def test_postgres_safe_error_text_bounds_work_before_encoding(self):
        prefix = "x" * 100 + "\x00\ud800" + "y" * 4898
        value = prefix + "z" * 5_000_000

        tracemalloc.start()
        try:
            result = _postgres_safe_error_text(value)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        self.assertEqual(len(result), 5000)
        self.assertNotIn("\x00", result)
        self.assertEqual(result[100:102], "\ufffd?")
        self.assertLess(peak, 1_000_000)

    @staticmethod
    def _initialize_unreserved_control_lifecycle(adapter):
        adapter._control_backend = None
        adapter._control_connection_context = None
        adapter._control_connection = None
        adapter._control_connection_state = "never-reserved"
        adapter._control_connection_state_lock = asyncio.Lock()
        adapter._control_connection_use_lock = asyncio.Lock()
        return adapter

    @staticmethod
    def _current_exact_drain_adapter():
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        planned_at = int(time.time())
        plan = fixtures.drain_plan(
            snapshot=fixtures.drain_snapshot(observed_at=planned_at),
            created_at=planned_at,
        )
        return ExactDrainClaimAdapter(
            plan,
            authorization=recovery_fixtures.exact_drain_authorization(
                plan,
                authorized_at=planned_at,
            ),
        )

    def test_consumed_v2_authorization_starts_adapter_after_approval_expiry(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        now = int(time.time())
        planned_at = now - 86_401
        plan = fixtures.drain_plan(
            snapshot=fixtures.drain_snapshot(observed_at=planned_at),
            created_at=planned_at,
        )
        authorization = recovery_fixtures.exact_drain_authorization(
            plan,
            authorized_at=plan["expires_at"] - 1,
        )
        authorization_bytes = json.dumps(
            authorization,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

        adapter = ExactDrainClaimAdapter(
            plan,
            authorization=authorization,
            clock=lambda: now,
        )

        self.assertEqual(
            adapter._execution_deadline,
            authorization["authorized_at"]
            + plan["execution_window"]["calculated_seconds"],
        )
        self.assertEqual(
            json.dumps(
                authorization,
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
            authorization_bytes,
        )

    def test_legacy_v1_unresumed_adapter_still_rejects_expired_approval(self):
        fixture = json.loads(
            (
                ROOT
                / "tests"
                / "fixtures"
                / "legacy_exact_drain_plans.json"
            ).read_text(encoding="utf-8")
        )["exact"]

        with self.assertRaisesRegex(OperationRecoveryError, "plan expired"):
            ExactDrainClaimAdapter(fixture)

    def test_legacy_v1_terminal_reconciliation_bounds_public_claim_waits(self):
        fixture = json.loads(
            (
                ROOT
                / "tests"
                / "fixtures"
                / "legacy_exact_drain_plans.json"
            ).read_text(encoding="utf-8")
        )["exact"]
        adapter = ExactDrainClaimAdapter(
            fixture,
            resume=True,
            terminal_reconciliation=True,
        )
        adapter._verify_initial_state = AsyncMock()

        class Connection:
            def __init__(self):
                self.timeouts = []

            async def execute(self, *_arguments):
                return "SET"

            async def fetchval(self, query, value):
                self.timeouts.append((query, value))

        connection = Connection()
        tasks = asyncio.run(
            adapter.claim_tasks(
                connection,
                "public.async_operations",
                adapter._worker_id,
                {},
                1,
            )
        )

        self.assertEqual(tasks, [])
        self.assertEqual(
            [value for _query, value in connection.timeouts],
            ["120000ms", "120000ms", "120000ms"],
        )

    def test_terminal_reconciliation_constructs_at_execution_lease_boundary(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        planned_at = int(time.time())
        plan = fixtures.drain_plan(
            snapshot=fixtures.drain_snapshot(observed_at=planned_at),
            created_at=planned_at,
        )
        authorization = recovery_fixtures.exact_drain_authorization(
            plan,
            authorized_at=planned_at,
        )
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "terminal status evidence is required",
        ):
            ExactDrainClaimAdapter(
                plan,
                authorization=authorization,
                resume=True,
                terminal_reconciliation=True,
                clock=lambda: planned_at
                + plan["execution_window"]["calculated_seconds"],
            )

        adapter = ExactDrainClaimAdapter(
            plan,
            authorization=authorization,
            resume=True,
            terminal_reconciliation=True,
            terminal_status_evidence={
                "generation": plan["pre_generation"],
                "observed_at": planned_at,
                "status_digest": "6" * 64,
            },
            clock=lambda: planned_at
            + plan["execution_window"]["calculated_seconds"],
        )

        self.assertTrue(adapter._terminal_reconciliation)
        self.assertEqual(
            adapter._execution_deadline,
            planned_at + plan["execution_window"]["calculated_seconds"],
        )

    def test_exact_drain_public_claim_rejects_the_execution_lease_boundary(self):
        adapter = object.__new__(ExactDrainClaimAdapter)
        adapter._worker_id = "exact-worker"
        adapter._execution_deadline = 86_500
        adapter._clock = lambda: 86_500

        class Connection:
            async def execute(self, *_args, **_kwargs):
                raise AssertionError("expired exact drain touched PostgreSQL")

        with self.assertRaisesRegex(
            OperationRecoveryError,
            "execution lease expired",
        ):
            asyncio.run(
                adapter.claim_tasks(
                    Connection(),
                    '"public".async_operations',
                    "exact-worker",
                    {},
                    1,
                )
            )

    def test_exact_drain_public_claim_bounds_database_waits_to_120_seconds(self):
        adapter = object.__new__(ExactDrainClaimAdapter)
        adapter._worker_id = "exact-worker"
        adapter._execution_deadline = 86_500
        adapter._transaction_timeout_seconds = 120
        adapter._clock = lambda: 100
        adapter._terminal_reconciliation = False
        adapter._initial_guard_complete = True
        adapter._verify_unstarted_state = AsyncMock()
        adapter._selected = {}
        adapter._started_ids = set()
        adapter._max_retries = 3
        adapter._identifiers = []
        adapter._completion_signalled = False
        adapter._completion_callback = None

        class Connection:
            def __init__(self):
                self.timeouts = []

            async def execute(self, _query, *_arguments):
                return "SET"

            async def fetchval(self, query, value):
                self.timeouts.append((query, value))

            async def fetch(self, *_args, **_kwargs):
                return []

        connection = Connection()
        asyncio.run(
            adapter.claim_tasks(
                connection,
                '"public".async_operations',
                "exact-worker",
                {},
                1,
            )
        )

        self.assertEqual(
            connection.timeouts,
            [
                (
                    "SELECT pg_catalog.set_config('transaction_timeout', $1, true)",
                    "120000ms",
                ),
                (
                    "SELECT pg_catalog.set_config('lock_timeout', $1, true)",
                    "120000ms",
                ),
                (
                    "SELECT pg_catalog.set_config('statement_timeout', $1, true)",
                    "120000ms",
                ),
            ],
        )

    def test_exact_drain_public_claim_parses_only_capacity_bound_rows(self):
        first_id = "00000000-0000-4000-8000-000000000001"
        later_id = "00000000-0000-4000-8000-000000000002"

        class UnchosenPayload(dict):
            def __iter__(self):
                raise AssertionError("unchosen task payload was parsed")

            def keys(self):
                raise AssertionError("unchosen task payload was parsed")

        adapter = object.__new__(ExactDrainClaimAdapter)
        adapter._worker_id = "exact-worker"
        adapter._worker_digest = hashlib.sha256(b"exact-worker").hexdigest()
        adapter._terminal_reconciliation = False
        adapter._initial_guard_complete = True
        adapter._verify_unstarted_state = AsyncMock()
        adapter._selected = {
            first_id: {
                "operation_type": "retain",
                "task_payload_digest": "a" * 64,
            },
            later_id: {
                "operation_type": "retain",
                "task_payload_digest": "b" * 64,
            },
        }
        adapter._started_ids = set()
        adapter._max_retries = 3
        adapter._identifiers = [first_id, later_id]

        class Connection:
            async def execute(self, query, *_arguments):
                return "UPDATE 1" if query.lstrip().startswith("UPDATE") else "SET"

            async def fetch(self, _query, *_arguments):
                return [
                    {
                        "operation_id": first_id,
                        "operation_type": "retain",
                        "task_payload": {
                            "operation_id": first_id,
                            "bank_id": "engineering",
                            "type": "batch_retain",
                        },
                        "retry_count": 0,
                        "worker_id_digest": None,
                        "task_payload_digest": "a" * 64,
                    },
                    {
                        "operation_id": later_id,
                        "operation_type": "retain",
                        "task_payload": UnchosenPayload(),
                        "retry_count": 0,
                        "worker_id_digest": None,
                        "task_payload_digest": "b" * 64,
                    },
                ]

        claimed = asyncio.run(
            adapter.claim_tasks(
                Connection(),
                '"public".async_operations',
                "exact-worker",
                {},
                1,
            )
        )

        self.assertEqual(
            [str(row["operation_id"]) for row in claimed],
            [first_id],
        )

    def test_runtime_guard_rejects_missing_progress_seams(self):
        class MissingWorkerPoller(_RunCapableWorkerPoller):
            pass

        with self.assertRaisesRegex(
            Exception,
            "required worker progress seam is unavailable",
        ):
            install_exact_drain_runtime_guards(
                type("PostgreSQLOps", (), {}),
                MissingWorkerPoller,
                type("MemoryEngine", (), {}),
                object(),
            )

    def test_runtime_guard_rejects_missing_poller_lifecycle_seam(self):
        class MissingRunWorkerPoller:
            async def _execute_task_inner(self, _task, _holder):
                return None

            async def _claim_batch_for_schema_inner(self, *_arguments):
                return []

            async def _claim_batch_for_schema(self, *_arguments):
                return []

        with self.assertRaisesRegex(
            OperationRecoveryError,
            "required worker lifecycle seam is unavailable",
        ):
            install_exact_drain_runtime_guards(
                type("PostgreSQLOps", (), {}),
                MissingRunWorkerPoller,
                type("MemoryEngine", (), {}),
                object(),
            )

    def test_runtime_guard_disables_upstream_retain_folding(self):
        fold_calls = []

        class WorkerPoller(_RunCapableWorkerPoller):
            async def _execute_task_inner(self, _task, _holder):
                return None

            async def _claim_batch_for_schema_inner(self, *_arguments):
                return []

            async def _claim_batch_for_schema(self, *_arguments):
                return []

            async def _fold_retain_peers(self, *_arguments):
                fold_calls.append(True)
                return ["outside-plan"]

        class Adapter:
            _plan = {"progress_schema_version": 1}

        install_exact_drain_runtime_guards(
            type("PostgreSQLOps", (), {}),
            WorkerPoller,
            type("MemoryEngine", (), {}),
            Adapter(),
        )

        self.assertEqual(
            asyncio.run(
                WorkerPoller()._fold_retain_peers(
                    object(),
                    "public.async_operations",
                    object(),
                    {},
                )
            ),
            [],
        )
        self.assertEqual(fold_calls, [])

    def test_claim_commit_rejects_folded_operations(self):
        adapter = object.__new__(ExactDrainClaimAdapter)
        operation_id = "00000000-0000-4000-8000-000000000001"
        adapter._selected = {operation_id: {}}
        task = SimpleNamespace(
            operation_id=operation_id,
            folded_operation_ids=[
                "00000000-0000-4000-8000-000000000002"
            ],
        )

        with self.assertRaisesRegex(
            OperationRecoveryError,
            "committed claim folded outside plan",
        ):
            adapter.claim_committed([task])

    def test_v8_runtime_guard_records_memory_initialization_failure(self):
        events = []

        class WorkerPoller(_RunCapableWorkerPoller):
            async def _execute_task_inner(self, _task, _holder):
                return None

            async def _claim_batch_for_schema_inner(self, *_arguments):
                return []

            async def _claim_batch_for_schema(self, *_arguments):
                return []

        class MemoryEngine:
            async def initialize(self):
                raise RuntimeError("raw startup failure must stay private")

        class Adapter:
            def __init__(self):
                self._plan = {"progress_schema_version": 3}

            def record_worker_stage(self, *, status, stage):
                events.append(("stage", status, stage))

            def record_worker_failure(self, error, *, exit_code):
                events.append(("failure", type(error).__name__, exit_code))

        install_exact_drain_runtime_guards(
            type("PostgreSQLOps", (), {}),
            WorkerPoller,
            MemoryEngine,
            Adapter(),
        )

        with self.assertRaisesRegex(RuntimeError, "raw startup failure"):
            asyncio.run(MemoryEngine().initialize())

        self.assertEqual(
            events,
            [
                ("stage", "starting", "worker.memory.initialize"),
                ("failure", "RuntimeError", 2),
            ],
        )

    def test_runtime_guard_records_operational_stage_before_poller_run(self):
        events = []

        class WorkerPoller:
            def __init__(self):
                self._shutdown = asyncio.Event()

            async def _execute_task_inner(self, _task, _holder):
                return None

            async def _claim_batch_for_schema_inner(self, *_arguments):
                return []

            async def _claim_batch_for_schema(self, *_arguments):
                return []

            async def run(self):
                events.append("upstream-run")
                raise OperationRecoveryError(
                    "operation-recovery exact drain execution lease expired"
                )

        class MemoryEngine:
            async def initialize(self):
                return None

        class Adapter(_ControlConnectionAdapterMixin):
            def __init__(self):
                self._plan = {"progress_schema_version": 3}

            async def reserve_control_connection(self, _backend):
                events.append("control-reserved")

            def record_worker_stage(self, *, status, stage):
                events.append(("stage", status, stage))

        install_exact_drain_runtime_guards(
            type("PostgreSQLOps", (), {}),
            WorkerPoller,
            MemoryEngine,
            Adapter(),
            request_worker_shutdown=lambda: events.append("shutdown"),
        )

        self.assertIsNone(asyncio.run(WorkerPoller().run()))
        self.assertEqual(
            events,
            [
                "control-reserved",
                ("stage", "running", "worker.poller.running"),
                "upstream-run",
                "shutdown",
            ],
        )

    def test_shutdown_bridge_deduplicates_internal_request_after_signal(self):
        external_handlers = []
        worker_callbacks = []

        def install_signal_handlers(_loop, handler):
            external_handlers.append(handler)
            return True

        worker_main = SimpleNamespace(
            _install_shutdown_signal_handlers=install_signal_handlers,
        )
        bridge = ExactDrainWorkerMainShutdownBridge(worker_main)
        with bridge:
            self.assertTrue(
                worker_main._install_shutdown_signal_handlers(
                    object(),
                    lambda: worker_callbacks.append("shutdown"),
                )
            )
            external_handlers[0]()
            bridge.request()
            self.assertEqual(worker_callbacks, ["shutdown"])
            external_handlers[0]()
            self.assertEqual(
                worker_callbacks,
                ["shutdown", "shutdown"],
            )
            bridge.request()
            self.assertEqual(
                worker_callbacks,
                ["shutdown", "shutdown"],
            )

    def test_uvicorn_signal_guard_preserves_worker_main_shutdown_handler(self):
        signal_bus = {"handler": None}
        worker_callbacks = []
        server_callbacks = []

        def install_signal_handlers(_loop, handler):
            signal_bus["handler"] = handler
            return True

        class Server:
            @contextmanager
            def capture_signals(self):
                previous_handler = signal_bus["handler"]
                signal_bus["handler"] = self.handle_exit
                try:
                    yield
                finally:
                    signal_bus["handler"] = previous_handler

            def handle_exit(self):
                server_callbacks.append("shutdown")

        worker_main = SimpleNamespace(
            _install_shutdown_signal_handlers=install_signal_handlers,
        )
        bridge = ExactDrainWorkerMainShutdownBridge(worker_main)
        guard_type = (
            operation_recovery_runtime.ExactDrainUvicornSignalGuard
        )

        with bridge, guard_type(Server):
            self.assertTrue(
                worker_main._install_shutdown_signal_handlers(
                    object(),
                    lambda: worker_callbacks.append("shutdown"),
                )
            )
            with Server().capture_signals():
                signal_bus["handler"]()

        self.assertEqual(worker_callbacks, ["shutdown"])
        self.assertEqual(server_callbacks, [])

    def test_startup_recovery_failure_requests_worker_main_shutdown(self):
        try:
            from hindsight_api.worker.poller import (
                WorkerPoller as UpstreamWorkerPoller,
            )
        except ImportError as error:
            raise unittest.SkipTest(
                "hindsight_api worker runtime is unavailable"
            ) from error

        worker_main_shutdown = asyncio.Event()

        class Adapter(_ControlConnectionAdapterMixin):
            phase_one_timeout_seconds = 3600

            async def recover_own_tasks(self, _backend):
                raise OperationRecoveryError("startup recovery failed")

        class PostgreSQLOps:
            pass

        class WorkerPoller(UpstreamWorkerPoller):
            pass

        class MemoryEngine:
            pass

        install_exact_drain_runtime_guards(
            PostgreSQLOps,
            WorkerPoller,
            MemoryEngine,
            Adapter(),
            request_worker_shutdown=worker_main_shutdown.set,
        )

        async def exercise():
            poller = object.__new__(WorkerPoller)
            poller._backend = "exact-backend"
            poller._shutdown = asyncio.Event()
            poller_task = asyncio.create_task(poller.run())
            try:
                try:
                    await asyncio.wait_for(
                        worker_main_shutdown.wait(),
                        timeout=0.25,
                    )
                    requested = True
                except asyncio.TimeoutError:
                    requested = False
                result = (await asyncio.gather(
                    poller_task,
                    return_exceptions=True,
                ))[0]
                return requested, result
            finally:
                if not poller_task.done():
                    poller_task.cancel()
                    await asyncio.gather(
                        poller_task,
                        return_exceptions=True,
                    )

        requested, result = asyncio.run(exercise())
        self.assertTrue(requested)
        self.assertIsNone(result)

    def test_runtime_reserves_control_connection_before_polling_and_closes_last(self):
        events = []

        class Adapter(_ControlConnectionAdapterMixin):
            phase_one_timeout_seconds = 3600

            async def reserve_control_connection(self, backend):
                events.append(("reserve", backend))

            async def close_control_connection(self):
                events.append(("close", None))

        class WorkerPoller:
            def __init__(self):
                self._backend = "exact-backend"
                self._shutdown = asyncio.Event()

            async def run(self):
                events.append(("poll", self._backend))
                self._shutdown.set()

            async def shutdown_graceful(self, timeout=30.0):
                events.append(("shutdown", timeout))

            async def _execute_task_inner(self, _task, _holder):
                return None

            async def _claim_batch_for_schema_inner(self, *_arguments):
                return []

            async def _claim_batch_for_schema(self, *_arguments):
                return []

        install_exact_drain_runtime_guards(
            type("PostgreSQLOps", (), {}),
            WorkerPoller,
            type("MemoryEngine", (), {}),
            Adapter(),
            request_worker_shutdown=lambda: None,
        )

        async def exercise():
            poller = WorkerPoller()
            await poller.run()
            await poller.shutdown_graceful(timeout=0.25)

        asyncio.run(exercise())
        self.assertEqual(
            events,
            [
                ("reserve", "exact-backend"),
                ("poll", "exact-backend"),
                ("shutdown", 0.25),
                ("close", None),
            ],
        )

    def test_reserved_hindsight_connection_terminalizes_exact_row(self):
        try:
            from hindsight_api.engine.db.postgresql import PostgresConnection
        except ImportError as error:
            raise unittest.SkipTest(
                "hindsight_api PostgreSQL runtime is unavailable"
            ) from error

        adapter = self._current_exact_drain_adapter()
        operation_id, selected = next(iter(adapter._selected.items()))
        adapter._started_ids.add(operation_id)
        adapter._verify_unstarted_state = AsyncMock()
        adapter._configure_mutation_transaction = AsyncMock()
        row = {
            "operation_type": selected["operation_type"],
            "status": "processing",
            "worker_id": adapter._worker_id,
            "task_payload_digest": selected["task_payload_digest"],
            "error_message": None,
        }
        statements = []

        class Transaction:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *_arguments):
                return None

        class RawConnection:
            def transaction(self):
                return Transaction()

            async def fetchrow(self, *_arguments, **_keywords):
                return dict(row)

            async def execute(self, *_arguments, **_keywords):
                statement = _arguments[0]
                statements.append(statement)
                if "UPDATE public.async_operations" in statement:
                    error_message = _arguments[2]
                    if "\x00" in error_message:
                        raise ValueError("PostgreSQL text cannot contain NUL")
                    row["status"] = "failed"
                    row["error_message"] = error_message
                    return "UPDATE 1"
                return "SET"

        connection = PostgresConnection(RawConnection())

        class Context:
            async def __aenter__(self):
                return connection

            async def __aexit__(self, *_arguments):
                return None

        class Backend:
            def acquire(self):
                return Context()

        async def exercise():
            backend = Backend()
            await adapter.reserve_control_connection(backend)
            try:
                await adapter.mark_failed(
                    backend,
                    operation_id,
                    "provider\x00request failed",
                    "public",
                )
            finally:
                await adapter.close_control_connection()

        asyncio.run(exercise())
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["error_message"], "provider�request failed")
        self.assertEqual(
            statements[0],
            "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE",
        )

    def test_terminal_failure_records_closed_cause_and_committed_checkpoint(self):
        adapter = self._current_exact_drain_adapter()
        adapter._plan = {**adapter._plan, "progress_schema_version": 2}
        operation_id, selected = next(iter(adapter._selected.items()))
        adapter._started_ids.add(operation_id)
        adapter._verify_unstarted_state = AsyncMock()
        adapter._configure_mutation_transaction = AsyncMock()
        outcomes = []

        class Recorder:
            def task_stage(self, *arguments, **keywords):
                outcomes.append(("legacy", arguments, keywords))

            def task_outcome(self, *arguments, **keywords):
                outcomes.append(("outcome", arguments, keywords))

        adapter._progress_recorder = Recorder()
        row = {
            "operation_type": selected["operation_type"],
            "status": "processing",
            "worker_id": adapter._worker_id,
            "task_payload_digest": selected["task_payload_digest"],
            "checkpoint_facts_committed": True,
            "checkpoint_committed_document_count": 1,
            "checkpoint_unit_ids_count": 29,
            "checkpoint_stage": "storing",
            "checkpoint_processed": 14,
            "checkpoint_total": 14,
        }

        class Transaction:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *_arguments):
                return None

        class Connection:
            def transaction(self):
                return Transaction()

            async def fetchrow(self, *_arguments):
                return dict(row)

            async def execute(self, statement, *_arguments):
                if "UPDATE public.async_operations" in statement:
                    row["status"] = "failed"
                    return "UPDATE 1"
                return "SET"

        class Context:
            async def __aenter__(self):
                return Connection()

            async def __aexit__(self, *_arguments):
                return None

        class Backend:
            def acquire(self):
                return Context()

        asyncio.run(
            adapter.mark_failed(
                Backend(),
                operation_id,
                "TimeoutError",
                "public",
            )
        )

        self.assertEqual(len(outcomes), 1)
        kind, arguments, evidence = outcomes[0]
        self.assertEqual(kind, "outcome")
        self.assertEqual(arguments, (operation_id,))
        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["stage"], "failed")
        self.assertEqual(
            evidence["failure"],
            {
                "category": "phase_one_timeout",
                "retryable": False,
                "http_status": None,
                "error_digest": hashlib.sha256(b"TimeoutError").hexdigest(),
            },
        )
        self.assertEqual(
            evidence["checkpoint"],
            {
                "facts_committed": True,
                "committed_document_count": 1,
                "unit_ids_count": 29,
                "stage": "storing",
                "processed": 14,
                "total": 14,
            },
        )

    def test_failure_evidence_classifies_provider_bad_request_without_url_port(self):
        classify = operation_recovery_runtime._exact_drain_failure_evidence
        bad_request = classify(
            "Client error '400 Bad Request' for url 'https://api.example/v1'",
            retryable=False,
        )
        transport = classify(
            "ConnectError for url 'https://api.example:443/v1'",
            retryable=True,
        )
        openai_bad_request = classify(
            "BadRequestError: Error code: 400 - invalid request",
            retryable=False,
        )

        self.assertEqual(bad_request["category"], "provider_bad_request")
        self.assertEqual(bad_request["http_status"], 400)
        self.assertIs(bad_request["retryable"], False)
        self.assertEqual(transport["category"], "provider_transport")
        self.assertIsNone(transport["http_status"])
        self.assertIs(transport["retryable"], True)
        self.assertEqual(
            openai_bad_request["category"],
            "provider_bad_request",
        )
        self.assertEqual(openai_bad_request["http_status"], 400)

    def test_schema_eleven_timeout_failures_have_distinct_closed_categories(self):
        classify = operation_recovery_runtime._exact_drain_failure_evidence

        queue = classify("provider_queue_timeout", retryable=True)
        execution = classify("provider_execution_timeout", retryable=True)
        attempt = classify(
            operation_recovery_runtime.EXACT_DRAIN_OPERATION_ATTEMPT_TIMEOUT_MESSAGE,
            retryable=False,
        )

        self.assertEqual(queue["category"], "provider_queue_timeout")
        self.assertEqual(execution["category"], "provider_execution_timeout")
        self.assertEqual(attempt["category"], "operation_attempt_timeout")
        self.assertTrue(queue["retryable"])
        self.assertTrue(execution["retryable"])
        self.assertFalse(attempt["retryable"])
        for evidence in (queue, execution, attempt):
            self.assertIsNone(evidence["http_status"])
            self.assertEqual(
                set(evidence),
                {"category", "retryable", "http_status", "error_digest"},
            )

    def test_retry_ceiling_records_the_terminal_disposition(self):
        adapter = self._current_exact_drain_adapter()
        adapter._plan = {**adapter._plan, "progress_schema_version": 2}
        operation_id, selected = next(iter(adapter._selected.items()))
        adapter._started_ids.add(operation_id)
        adapter._verify_unstarted_state = AsyncMock()
        adapter._configure_mutation_transaction = AsyncMock()
        outcomes = []

        class Recorder:
            def task_outcome(self, *arguments, **keywords):
                outcomes.append((arguments, keywords))

        adapter._progress_recorder = Recorder()
        row = {
            "operation_type": selected["operation_type"],
            "status": "processing",
            "worker_id": adapter._worker_id,
            "retry_count": adapter._max_retries,
            "task_payload_digest": selected["task_payload_digest"],
            "checkpoint_facts_committed": False,
            "checkpoint_committed_document_count": 0,
            "checkpoint_unit_ids_count": 0,
            "checkpoint_stage": "unavailable",
            "checkpoint_processed": 0,
            "checkpoint_total": 0,
        }

        class Transaction:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *_arguments):
                return None

        class Connection:
            def transaction(self):
                return Transaction()

            async def fetchrow(self, *_arguments):
                return dict(row)

            async def execute(self, statement, *_arguments):
                if "UPDATE public.async_operations" in statement:
                    row["status"] = "failed"
                    return "UPDATE 1"
                return "SET"

        class Context:
            async def __aenter__(self):
                return Connection()

            async def __aexit__(self, *_arguments):
                return None

        class Backend:
            def acquire(self):
                return Context()

        asyncio.run(
            adapter.schedule_retry(
                Backend(),
                operation_id,
                None,
                "ConnectError: provider unavailable",
                "public",
            )
        )

        self.assertEqual(len(outcomes), 1)
        arguments, evidence = outcomes[0]
        self.assertEqual(arguments, (operation_id,))
        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["stage"], "retry-ceiling")
        self.assertEqual(evidence["failure"]["category"], "retry_ceiling")
        self.assertIs(evidence["failure"]["retryable"], False)

    def test_schema10_retry_and_defer_enforce_the_plan_bound_delay(self):
        observed_at = int(time.time())
        maximum_delay = 3_600

        class Transaction:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *_arguments):
                return None

        class Connection:
            def __init__(self, row):
                self.row = row
                self.update_count = 0
                self.update_arguments = []

            def transaction(self):
                return Transaction()

            async def fetchrow(self, *_arguments):
                return dict(self.row)

            async def execute(self, statement, *arguments):
                if "UPDATE public.async_operations" in statement:
                    self.update_count += 1
                    self.update_arguments.append(arguments)
                    return "UPDATE 1"
                return "SET"

        class Context:
            def __init__(self, connection):
                self.connection = connection

            async def __aenter__(self):
                return self.connection

            async def __aexit__(self, *_arguments):
                return None

        class Backend:
            def __init__(self, connection):
                self.connection = connection

            def acquire(self):
                return Context(self.connection)

        def case(*, retry_count=0, clock=lambda: observed_at):
            adapter = self._current_exact_drain_adapter()
            adapter._clock = clock
            adapter._verify_unstarted_state = AsyncMock()
            adapter._configure_mutation_transaction = AsyncMock()
            operation_id, selected = next(iter(adapter._selected.items()))
            connection = Connection(
                {
                    "operation_type": selected["operation_type"],
                    "status": "processing",
                    "worker_id": adapter._worker_id,
                    "retry_count": retry_count,
                    "task_payload_digest": selected["task_payload_digest"],
                    "checkpoint_facts_committed": False,
                    "checkpoint_committed_document_count": 0,
                    "checkpoint_unit_ids_count": 0,
                    "checkpoint_stage": "unavailable",
                    "checkpoint_processed": 0,
                    "checkpoint_total": 0,
                }
            )
            return adapter, operation_id, connection, Backend(connection)

        def invoke(name, adapter, operation_id, backend, timestamp):
            if name == "retry":
                return adapter.schedule_retry(
                    backend,
                    operation_id,
                    timestamp,
                    "provider unavailable",
                    "public",
                )
            return adapter.defer_operation(
                backend,
                operation_id,
                timestamp,
                "capacity",
                "public",
            )

        for name in ("retry", "defer"):
            with self.subTest(name=name, timestamp="non-UTC-boundary"):
                adapter, operation_id, connection, backend = case()
                self.assertEqual(
                    adapter._maximum_retry_delay_seconds,
                    maximum_delay,
                )
                boundary = datetime.fromtimestamp(
                    observed_at + maximum_delay,
                    timezone(timedelta(hours=5, minutes=45)),
                )

                asyncio.run(
                    invoke(name, adapter, operation_id, backend, boundary)
                )

                self.assertEqual(connection.update_count, 1)
                self.assertEqual(
                    connection.update_arguments[0][1],
                    boundary.astimezone(timezone.utc),
                )
                self.assertIs(
                    connection.update_arguments[0][1].tzinfo,
                    timezone.utc,
                )

            for label, timestamp in {
                "past": datetime.fromtimestamp(
                    observed_at - 1,
                    timezone.utc,
                ),
                "immediate": datetime.fromtimestamp(
                    observed_at,
                    timezone.utc,
                ),
            }.items():
                with self.subTest(name=name, timestamp=label):
                    adapter, operation_id, connection, backend = case()

                    asyncio.run(
                        invoke(name, adapter, operation_id, backend, timestamp)
                    )

                    self.assertEqual(connection.update_count, 1)

            invalid = {
                "over-limit": datetime.fromtimestamp(
                    observed_at + maximum_delay,
                    timezone.utc,
                )
                + timedelta(microseconds=1),
                "naive": datetime.fromtimestamp(observed_at),
                "non-datetime": object(),
            }
            for label, timestamp in invalid.items():
                with self.subTest(name=name, timestamp=label):
                    adapter, operation_id, connection, backend = case()
                    with self.assertRaisesRegex(
                        OperationRecoveryError,
                        "reschedule time is invalid",
                    ):
                        asyncio.run(
                            invoke(
                                name,
                                adapter,
                                operation_id,
                                backend,
                                timestamp,
                            )
                        )

                    self.assertEqual(connection.update_count, 0)

            with self.subTest(name=name, timestamp="deadline"):
                adapter, operation_id, connection, backend = case()
                adapter._execution_deadline = observed_at + maximum_delay // 2
                deadline = datetime.fromtimestamp(
                    adapter._execution_deadline,
                    timezone.utc,
                )
                with self.assertRaisesRegex(
                    OperationRecoveryError,
                    "reschedule time is invalid",
                ):
                    asyncio.run(
                        invoke(name, adapter, operation_id, backend, deadline)
                    )
                self.assertEqual(connection.update_count, 0)

            with self.subTest(name=name, timestamp="before-deadline"):
                adapter, operation_id, connection, backend = case()
                adapter._execution_deadline = observed_at + maximum_delay // 2
                before_deadline = datetime.fromtimestamp(
                    adapter._execution_deadline,
                    timezone.utc,
                ) - timedelta(microseconds=1)

                asyncio.run(
                    invoke(
                        name,
                        adapter,
                        operation_id,
                        backend,
                        before_deadline,
                    )
                )

                self.assertEqual(connection.update_count, 1)

            for label, clock_value in {
                "boolean": True,
                "not-numeric": "now",
                "nan": float("nan"),
                "infinity": float("inf"),
                "overflowing-integer": 10**1_000,
            }.items():
                with self.subTest(name=name, clock=label):
                    adapter, operation_id, connection, backend = case(
                        clock=lambda value=clock_value: value
                    )
                    timestamp = datetime.fromtimestamp(
                        observed_at,
                        timezone.utc,
                    )
                    with self.assertRaisesRegex(
                        OperationRecoveryError,
                        "clock is invalid",
                    ):
                        asyncio.run(
                            invoke(
                                name,
                                adapter,
                                operation_id,
                                backend,
                                timestamp,
                            )
                        )
                    self.assertEqual(connection.update_count, 0)

            with self.subTest(name=name, timestamp="retry-ceiling-none"):
                adapter, operation_id, connection, backend = case(retry_count=3)

                asyncio.run(invoke(name, adapter, operation_id, backend, None))

                self.assertEqual(connection.update_count, 1)

        class DatetimeSubclass(datetime):
            def timestamp(self):
                raise AssertionError("datetime subclass reached timestamp")

        adapter, operation_id, connection, backend = case()
        hostile = DatetimeSubclass.fromtimestamp(observed_at, timezone.utc)
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "reschedule time is invalid",
        ):
            asyncio.run(
                invoke("retry", adapter, operation_id, backend, hostile)
            )
        self.assertEqual(connection.update_count, 0)

        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        schema10 = fixtures.drain_plan(
            snapshot=fixtures.drain_snapshot(observed_at=observed_at),
            created_at=observed_at,
        )
        body = {
            key: value
            for key, value in schema10.items()
            if key
            not in {
                "plan_digest",
                "execution_window",
                "recovery_context",
                "recovery_context_digest",
            }
        }
        body["schema_version"] = 9
        body["execution_lease_seconds"] = 86_400
        schema9 = {
            **body,
            "plan_digest": recovery_fixtures.digest(body),
        }
        legacy = ExactDrainClaimAdapter(
            schema9,
            authorization=recovery_fixtures.exact_drain_authorization(
                schema9,
                authorized_at=observed_at,
            ),
            clock=lambda: observed_at,
        )
        legacy._verify_unstarted_state = AsyncMock()
        legacy._configure_mutation_transaction = AsyncMock()
        operation_id, selected = next(iter(legacy._selected.items()))
        connection = Connection(
            {
                "operation_type": selected["operation_type"],
                "status": "processing",
                "worker_id": legacy._worker_id,
                "retry_count": 0,
                "task_payload_digest": selected["task_payload_digest"],
                "checkpoint_facts_committed": False,
                "checkpoint_committed_document_count": 0,
                "checkpoint_unit_ids_count": 0,
                "checkpoint_stage": "unavailable",
                "checkpoint_processed": 0,
                "checkpoint_total": 0,
            }
        )
        far_future = datetime.fromtimestamp(
            observed_at + 10 * 86_400,
            timezone(timedelta(hours=-4)),
        )

        asyncio.run(
            legacy.schedule_retry(
                Backend(connection),
                operation_id,
                far_future,
                "provider unavailable",
                "public",
            )
        )

        self.assertIsNone(legacy._maximum_retry_delay_seconds)
        self.assertEqual(connection.update_count, 1)
        self.assertIs(connection.update_arguments[0][1], far_future)

    def test_closed_control_connection_never_falls_back_to_worker_pool(self):
        adapter = self._current_exact_drain_adapter()

        class Context:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, *_arguments):
                return None

        class Backend:
            def __init__(self):
                self.acquisitions = 0

            def acquire(self):
                self.acquisitions += 1
                return Context()

        async def exercise():
            backend = Backend()
            await adapter.reserve_control_connection(backend)
            await adapter.close_control_connection()
            with self.assertRaisesRegex(
                OperationRecoveryError,
                "control connection",
            ):
                async with adapter._mutation_connection(backend):
                    pass
            return backend.acquisitions

        self.assertEqual(asyncio.run(exercise()), 1)

    def test_malformed_closed_control_lifecycle_never_falls_back(self):
        adapter = object.__new__(ExactDrainClaimAdapter)
        adapter._control_connection_state = "closed"

        class Backend:
            def __init__(self):
                self.acquisitions = 0

            @asynccontextmanager
            async def acquire(self):
                self.acquisitions += 1
                yield object()

        async def exercise():
            backend = Backend()
            with self.assertRaisesRegex(
                OperationRecoveryError,
                "control connection",
            ):
                async with adapter._mutation_connection(backend):
                    pass
            return backend.acquisitions

        self.assertEqual(asyncio.run(exercise()), 0)

    def test_missing_control_lifecycle_state_never_falls_back(self):
        adapter = object.__new__(ExactDrainClaimAdapter)
        adapter._control_backend = None
        adapter._control_connection_context = None
        adapter._control_connection = None
        adapter._control_connection_state_lock = asyncio.Lock()
        adapter._control_connection_use_lock = asyncio.Lock()

        class Backend:
            def __init__(self):
                self.acquisitions = 0

            @asynccontextmanager
            async def acquire(self):
                self.acquisitions += 1
                yield object()

        async def exercise():
            backend = Backend()
            with self.assertRaisesRegex(
                OperationRecoveryError,
                "control connection",
            ):
                async with adapter._mutation_connection(backend):
                    pass
            return backend.acquisitions

        self.assertEqual(asyncio.run(exercise()), 0)

    def test_inconsistent_control_lifecycle_never_falls_back(self):
        malformed = {
            "unknown-state": ("unknown", None, None, None),
            "unreserved-with-backend": (
                "never-reserved",
                object(),
                None,
                None,
            ),
            "reserved-without-context": (
                "reserved",
                object(),
                None,
                object(),
            ),
            "closed-with-connection": (
                "closed",
                None,
                None,
                object(),
            ),
            "poisoned-without-context": (
                "poisoned",
                object(),
                None,
                None,
            ),
        }

        class Backend:
            def __init__(self):
                self.acquisitions = 0

            @asynccontextmanager
            async def acquire(self):
                self.acquisitions += 1
                yield object()

        async def exercise(values):
            adapter = object.__new__(ExactDrainClaimAdapter)
            (
                adapter._control_connection_state,
                adapter._control_backend,
                adapter._control_connection_context,
                adapter._control_connection,
            ) = values
            adapter._control_connection_state_lock = asyncio.Lock()
            adapter._control_connection_use_lock = asyncio.Lock()
            backend = Backend()
            with self.assertRaisesRegex(
                OperationRecoveryError,
                "control connection",
            ):
                async with adapter._mutation_connection(backend):
                    pass
            return backend.acquisitions

        for name, values in malformed.items():
            with self.subTest(name=name):
                self.assertEqual(asyncio.run(exercise(values)), 0)

    def test_failed_control_connection_release_stays_poisoned_until_retried(self):
        adapter = self._current_exact_drain_adapter()

        class Context:
            def __init__(self):
                self.exits = 0

            async def __aenter__(self):
                return object()

            async def __aexit__(self, *_arguments):
                self.exits += 1
                if self.exits == 1:
                    raise RuntimeError("release failed")
                return None

        context = Context()

        class Backend:
            def __init__(self):
                self.acquisitions = 0

            def acquire(self):
                self.acquisitions += 1
                return context

        async def exercise():
            backend = Backend()
            await adapter.reserve_control_connection(backend)
            with self.assertRaisesRegex(RuntimeError, "release failed"):
                await adapter.close_control_connection()
            with self.assertRaisesRegex(
                OperationRecoveryError,
                "control connection",
            ):
                async with adapter._mutation_connection(backend):
                    pass
            await adapter.close_control_connection()
            return backend.acquisitions, context.exits

        self.assertEqual(asyncio.run(exercise()), (1, 2))

    def test_cancelled_control_connection_close_remains_retryable(self):
        adapter = self._current_exact_drain_adapter()
        mutation_entered = asyncio.Event()
        release_mutation = asyncio.Event()

        class Context:
            def __init__(self):
                self.exits = 0

            async def __aenter__(self):
                return object()

            async def __aexit__(self, *_arguments):
                self.exits += 1
                return None

        context = Context()

        class Backend:
            def acquire(self):
                return context

        async def exercise():
            backend = Backend()
            await adapter.reserve_control_connection(backend)

            async def hold_mutation():
                async with adapter._mutation_connection(backend):
                    mutation_entered.set()
                    await release_mutation.wait()

            mutation = asyncio.create_task(hold_mutation())
            await mutation_entered.wait()
            close = asyncio.create_task(adapter.close_control_connection())
            while adapter._control_connection_state != "closing":
                await asyncio.sleep(0)
            close.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await close
            release_mutation.set()
            await mutation
            await adapter.close_control_connection()
            return adapter._control_connection_state, context.exits

        self.assertEqual(asyncio.run(exercise()), ("closed", 1))

    def test_poller_failure_is_consumed_and_surfaces_after_release_failure(self):
        shutdown_requested = asyncio.Event()

        class Adapter(_ControlConnectionAdapterMixin):
            phase_one_timeout_seconds = 3600

        class WorkerPoller:
            def __init__(self):
                self._shutdown = asyncio.Event()

            async def run(self):
                raise OperationRecoveryError("startup recovery failed")

            async def shutdown_graceful(self, timeout=30.0):
                del timeout
                raise OperationRecoveryError("release failed")

            async def _execute_task_inner(self, _task, _holder):
                return None

            async def _claim_batch_for_schema_inner(self, *_arguments):
                return []

            async def _claim_batch_for_schema(self, *_arguments):
                return []

        install_exact_drain_runtime_guards(
            type("PostgreSQLOps", (), {}),
            WorkerPoller,
            type("MemoryEngine", (), {}),
            Adapter(),
            request_worker_shutdown=shutdown_requested.set,
        )

        async def exercise():
            poller = WorkerPoller()
            poller_task = asyncio.create_task(poller.run())
            await asyncio.wait_for(shutdown_requested.wait(), timeout=0.25)
            try:
                await poller.shutdown_graceful(timeout=0.25)
            except OperationRecoveryError as error:
                shutdown_error = error
            else:
                shutdown_error = None
            poller_result = (await asyncio.gather(
                poller_task,
                return_exceptions=True,
            ))[0]
            return shutdown_error, poller_result

        shutdown_error, poller_result = asyncio.run(exercise())
        self.assertIsInstance(shutdown_error, OperationRecoveryError)
        self.assertEqual(str(shutdown_error), "startup recovery failed")
        self.assertIsInstance(
            shutdown_error.__cause__,
            OperationRecoveryError,
        )
        self.assertEqual(str(shutdown_error.__cause__), "release failed")

    def test_shutdown_reports_primary_and_control_connection_failures(self):
        shutdown_requested = asyncio.Event()

        class Adapter(_ControlConnectionAdapterMixin):
            phase_one_timeout_seconds = 3600

            async def close_control_connection(self):
                raise OperationRecoveryError("control release failed")

        class WorkerPoller:
            def __init__(self):
                self._shutdown = asyncio.Event()

            async def run(self):
                raise OperationRecoveryError("startup recovery failed")

            async def shutdown_graceful(self, timeout=30.0):
                del timeout
                raise OperationRecoveryError("row release failed")

            async def _execute_task_inner(self, _task, _holder):
                return None

            async def _claim_batch_for_schema_inner(self, *_arguments):
                return []

            async def _claim_batch_for_schema(self, *_arguments):
                return []

        install_exact_drain_runtime_guards(
            type("PostgreSQLOps", (), {}),
            WorkerPoller,
            type("MemoryEngine", (), {}),
            Adapter(),
            request_worker_shutdown=shutdown_requested.set,
        )

        async def exercise():
            poller = WorkerPoller()
            poller_task = asyncio.create_task(poller.run())
            await asyncio.wait_for(shutdown_requested.wait(), timeout=0.25)
            try:
                await poller.shutdown_graceful(timeout=0.25)
            except BaseException as error:
                shutdown_error = error
            else:
                shutdown_error = None
            poller_result = (await asyncio.gather(
                poller_task,
                return_exceptions=True,
            ))[0]
            return shutdown_error, poller_result

        shutdown_error, poller_result = asyncio.run(exercise())
        self.assertIsInstance(shutdown_error, BaseExceptionGroup)
        self.assertEqual(
            [str(error) for error in shutdown_error.exceptions],
            ["startup recovery failed", "control release failed"],
        )
        self.assertIsInstance(
            shutdown_error.exceptions[0].__cause__,
            OperationRecoveryError,
        )
        self.assertEqual(
            str(shutdown_error.exceptions[0].__cause__),
            "row release failed",
        )
        self.assertIsNone(poller_result)
        self.assertIsNone(poller_result)

    def test_premature_poller_return_requests_worker_main_shutdown(self):
        shutdown_requests = []

        class Adapter(_ControlConnectionAdapterMixin):
            phase_one_timeout_seconds = 3600

        class WorkerPoller:
            def __init__(self):
                self._shutdown = asyncio.Event()

            async def run(self):
                return "stopped"

            async def shutdown_graceful(self, timeout=30.0):
                del timeout

            async def _execute_task_inner(self, _task, _holder):
                return None

            async def _claim_batch_for_schema_inner(self, *_arguments):
                return []

            async def _claim_batch_for_schema(self, *_arguments):
                return []

        install_exact_drain_runtime_guards(
            type("PostgreSQLOps", (), {}),
            WorkerPoller,
            type("MemoryEngine", (), {}),
            Adapter(),
            request_worker_shutdown=lambda: shutdown_requests.append(True),
        )

        poller = WorkerPoller()
        self.assertIsNone(asyncio.run(poller.run()))
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "poller stopped unexpectedly",
        ):
            asyncio.run(poller.shutdown_graceful())
        self.assertTrue(poller._shutdown.is_set())
        self.assertEqual(shutdown_requests, [True])

    def test_poller_return_after_shutdown_does_not_request_again(self):
        shutdown_requests = []

        class Adapter(_ControlConnectionAdapterMixin):
            phase_one_timeout_seconds = 3600

        class WorkerPoller:
            def __init__(self):
                self._shutdown = asyncio.Event()
                self._shutdown.set()

            async def run(self):
                return "stopped"

            async def _execute_task_inner(self, _task, _holder):
                return None

            async def _claim_batch_for_schema_inner(self, *_arguments):
                return []

            async def _claim_batch_for_schema(self, *_arguments):
                return []

        install_exact_drain_runtime_guards(
            type("PostgreSQLOps", (), {}),
            WorkerPoller,
            type("MemoryEngine", (), {}),
            Adapter(),
            request_worker_shutdown=lambda: shutdown_requests.append(True),
        )

        poller = WorkerPoller()
        self.assertEqual(asyncio.run(poller.run()), "stopped")
        self.assertEqual(shutdown_requests, [])

    def test_poller_cancellation_does_not_request_worker_main_shutdown(self):
        try:
            from hindsight_api.worker.poller import (
                WorkerPoller as UpstreamWorkerPoller,
            )
        except ImportError as error:
            raise unittest.SkipTest(
                "hindsight_api worker runtime is unavailable"
            ) from error

        shutdown_requests = []
        started = asyncio.Event()

        class Adapter(_ControlConnectionAdapterMixin):
            phase_one_timeout_seconds = 3600

            async def recover_own_tasks(self, _backend):
                return 0

        class WorkerPoller(UpstreamWorkerPoller):
            async def claim_batch(self):
                started.set()
                await asyncio.Event().wait()
                return []

        install_exact_drain_runtime_guards(
            type("PostgreSQLOps", (), {}),
            WorkerPoller,
            type("MemoryEngine", (), {}),
            Adapter(),
            request_worker_shutdown=lambda: shutdown_requests.append(True),
        )

        async def exercise():
            poller = object.__new__(WorkerPoller)
            poller._backend = "exact-backend"
            poller._shutdown = asyncio.Event()
            poller._slot_reservations = {}
            poller._max_slots = 1
            poller._worker_id = "exact-worker"
            poller._poll_interval_ms = 250
            task = asyncio.create_task(poller.run())
            await asyncio.wait_for(started.wait(), timeout=0.25)
            task.cancel()
            result = (await asyncio.gather(task, return_exceptions=True))[0]
            return poller, result

        poller, result = asyncio.run(exercise())
        self.assertIsNone(result)
        self.assertFalse(poller._shutdown.is_set())
        self.assertEqual(shutdown_requests, [])
        self.assertFalse(hasattr(poller, "_exact_drain_task_errors"))

    def test_runtime_guard_records_claim_only_after_upstream_commit_seam(self):
        committed = []
        task = type("Task", (), {"operation_id": "operation-1"})()

        class Adapter(_ControlConnectionAdapterMixin):
            def claim_committed(self, tasks):
                committed.extend(tasks)

        class PostgreSQLOps:
            pass

        class WorkerPoller(_RunCapableWorkerPoller):
            async def _execute_task_inner(self, _task, _holder):
                return None

            async def _claim_batch_for_schema_inner(
                self,
                _schema,
                _reserved_limits,
                _shared_limit,
            ):
                return [task]

            async def _claim_batch_for_schema(self, schema, reserved, shared):
                return await self._claim_batch_for_schema_inner(
                    schema, reserved, shared
                )

        class MemoryEngine:
            pass

        install_exact_drain_runtime_guards(
            PostgreSQLOps,
            WorkerPoller,
            MemoryEngine,
            Adapter(),
        )
        result = asyncio.run(
            WorkerPoller()._claim_batch_for_schema_inner(None, {}, 1)
        )

        self.assertEqual(result, [task])
        self.assertEqual(committed, [task])

    def test_exact_terminal_failure_never_uses_upstream_sql_reclaim(self):
        try:
            from hindsight_api.worker.poller import ClaimedTask
            from hindsight_api.worker.poller import (
                WorkerPoller as UpstreamWorkerPoller,
            )
        except ImportError as error:
            raise unittest.SkipTest(
                "hindsight_api worker runtime is unavailable"
            ) from error

        row = {"status": "processing"}
        sql_calls = []
        recovery_calls = []
        shutdown_requests = []

        class Connection:
            async def execute(self, query, *_arguments):
                sql_calls.append(query)
                row["status"] = "pending"
                return "UPDATE 1"

            async def fetch(self, query, *_arguments):
                sql_calls.append(query)
                return []

        class Backend:
            @asynccontextmanager
            async def acquire(self):
                yield Connection()

        class Adapter(_ControlConnectionAdapterMixin):
            def record_upstream_stage(self, _operation_id, _stage):
                return None

            async def mark_failed(self, *_arguments):
                raise RuntimeError("exact terminal write failed")

            async def recover_own_tasks(self, _backend):
                recovery_calls.append("recover")
                return 0

            async def release_own_tasks(self, _backend):
                recovery_calls.append("release")
                return 0

        class PostgreSQLOps:
            pass

        class WorkerPoller(UpstreamWorkerPoller):
            async def _run_executor(self, _task, _task_type):
                raise RuntimeError("executor failed")

        class MemoryEngine:
            pass

        install_exact_drain_runtime_guards(
            PostgreSQLOps,
            WorkerPoller,
            MemoryEngine,
            Adapter(),
            request_worker_shutdown=lambda: shutdown_requests.append(True),
        )

        async def exercise():
            poller = object.__new__(WorkerPoller)
            poller._backend = Backend()
            poller._worker_id = "exact-worker"
            poller._max_retries = 3
            poller._shutdown = asyncio.Event()
            poller._active_tasks = {}
            task = ClaimedTask(
                operation_id="operation-1",
                task_dict={
                    "type": "retain",
                    "operation_type": "retain",
                    "bank_id": "engineering",
                },
                schema=None,
            )
            holder = SimpleNamespace(stage="queued.retain")
            await poller._execute_task_inner(task, holder)
            await poller.recover_own_tasks()
            await poller.release_own_tasks()

        asyncio.run(exercise())
        self.assertEqual(sql_calls, [])
        self.assertEqual(row, {"status": "processing"})
        self.assertEqual(recovery_calls, ["recover", "release"])
        self.assertEqual(shutdown_requests, [True])

    def test_transient_terminal_failure_consumes_exact_retry_budget(self):
        events = []

        class Adapter(_ControlConnectionAdapterMixin):
            async def schedule_retry(
                self,
                backend,
                operation_id,
                retry_at,
                error_message,
                schema,
            ):
                events.append(
                    (
                        "retry",
                        backend,
                        operation_id,
                        retry_at,
                        error_message,
                        schema,
                    )
                )

            async def mark_failed(self, *arguments):
                events.append(("failed", *arguments))

        class WorkerPoller(_RunCapableWorkerPoller):
            _backend = "exact-backend"

            async def _execute_task_inner(self, _task, _holder):
                return None

            async def _claim_batch_for_schema_inner(
                self,
                _schema,
                _reserved_limits,
                _shared_limit,
            ):
                return []

            async def _claim_batch_for_schema(self, schema, reserved, shared):
                return await self._claim_batch_for_schema_inner(
                    schema, reserved, shared
                )

        install_exact_drain_runtime_guards(
            type("PostgreSQLOps", (), {}),
            WorkerPoller,
            type("MemoryEngine", (), {}),
            Adapter(),
        )

        asyncio.run(
            WorkerPoller()._mark_failed(
                "operation-1",
                "canceling statement due to statement timeout",
                "public",
            )
        )
        asyncio.run(
            WorkerPoller()._mark_failed(
                "operation-2",
                "entity payload is invalid",
                "public",
            )
        )
        asyncio.run(
            WorkerPoller()._mark_failed(
                "operation-3",
                "401 unauthorized while opening provider connection",
                "public",
            )
        )

        self.assertEqual(len(events), 3)
        self.assertEqual(events[0][:3], ("retry", "exact-backend", "operation-1"))
        self.assertEqual(
            events[0][4:],
            ("canceling statement due to statement timeout", "public"),
        )
        self.assertEqual(
            events[1],
            (
                "failed",
                "exact-backend",
                "operation-2",
                "entity payload is invalid",
                "public",
            ),
        )
        self.assertEqual(
            events[2],
            (
                "failed",
                "exact-backend",
                "operation-3",
                "401 unauthorized while opening provider connection",
                "public",
            ),
        )

    def test_swallowed_exact_terminal_failures_surface_after_public_shutdown(self):
        try:
            from hindsight_api.worker.poller import ClaimedTask
            from hindsight_api.worker.poller import (
                WorkerPoller as UpstreamWorkerPoller,
            )
        except ImportError as error:
            raise unittest.SkipTest(
                "hindsight_api worker runtime is unavailable"
            ) from error

        for failure_mode in ("terminal-write", "post-commit-progress"):
            with self.subTest(failure_mode=failure_mode):
                row = {"status": "processing"}
                events = []
                sql_calls = []
                shutdown_requested = asyncio.Event()
                sibling_quiesced = asyncio.Event()
                expected_error = (
                    "exact terminal write failed"
                    if failure_mode == "terminal-write"
                    else "terminal progress recorder failed"
                )

                class Backend:
                    @asynccontextmanager
                    async def acquire(self, _sql_calls=sql_calls):
                        _sql_calls.append("acquire")
                        raise AssertionError("upstream SQL reclaim executed")
                        yield

                class Adapter(_ControlConnectionAdapterMixin):
                    def record_upstream_stage(
                        self,
                        operation_id,
                        stage,
                        _events=events,
                    ):
                        _events.append(("stage", operation_id, stage))

                    def record_upstream_failure(
                        self,
                        operation_id,
                        *,
                        stage,
                        category,
                        retryable,
                        error_message,
                        _events=events,
                    ):
                        _events.append(
                            (
                                "failure",
                                operation_id,
                                stage,
                                category,
                                retryable,
                                type(error_message).__name__,
                            )
                        )

                    async def mark_completed(
                        self,
                        _backend,
                        operation_id,
                        _schema,
                        _events=events,
                    ):
                        _events.append(("completed", operation_id))

                    async def mark_failed(
                        self,
                        *_arguments,
                        _failure_mode=failure_mode,
                        _row=row,
                        _events=events,
                        _expected_error=expected_error,
                    ):
                        if _failure_mode == "post-commit-progress":
                            _row["status"] = "failed"
                            _events.append(("terminal", "committed"))
                        raise RuntimeError(_expected_error)

                    async def release_own_tasks(
                        self,
                        _backend,
                        _sibling_quiesced=sibling_quiesced,
                        _events=events,
                        _row=row,
                    ):
                        if not _sibling_quiesced.is_set():
                            raise AssertionError(
                                "exact release preceded sibling quiescence"
                            )
                        _events.append(("release", _row["status"]))
                        if _row["status"] == "processing":
                            _row["status"] = "pending"
                            return 1
                        return 0

                class PostgreSQLOps:
                    pass

                class WorkerPoller(UpstreamWorkerPoller):
                    async def _run_executor(
                        self,
                        task,
                        _task_type,
                        _sibling_quiesced=sibling_quiesced,
                        _events=events,
                    ):
                        if task.operation_id == "operation-a":
                            raise RuntimeError("executor failed")
                        try:
                            await self._shutdown.wait()
                        finally:
                            _sibling_quiesced.set()
                            _events.append(("sibling", "quiesced"))

                    async def _cleanup_task(
                        self,
                        operation_id,
                        operation_type,
                        _events=events,
                    ):
                        _events.append(("cleanup", operation_id))
                        await super()._cleanup_task(
                            operation_id,
                            operation_type,
                        )

                class MemoryEngine:
                    pass

                def request_shutdown(
                    _events=events,
                    _shutdown_requested=shutdown_requested,
                ):
                    _events.append(("shutdown", "requested"))
                    _shutdown_requested.set()

                install_exact_drain_runtime_guards(
                    PostgreSQLOps,
                    WorkerPoller,
                    MemoryEngine,
                    Adapter(),
                    request_worker_shutdown=request_shutdown,
                )

                async def exercise(
                    _shutdown_requested=shutdown_requested,
                    _expected_error=expected_error,
                ):
                    poller = object.__new__(WorkerPoller)
                    poller._backend = Backend()
                    poller._worker_id = "exact-worker"
                    poller._shutdown = asyncio.Event()
                    poller._in_flight_lock = asyncio.Lock()
                    poller._in_flight_count = 0
                    poller._in_flight_by_type = {}
                    poller._active_tasks = {}
                    sibling = ClaimedTask(
                        operation_id="operation-b",
                        task_dict={
                            "type": "retain",
                            "operation_type": "retain",
                            "bank_id": "engineering",
                        },
                        schema=None,
                    )
                    failing = ClaimedTask(
                        operation_id="operation-a",
                        task_dict={
                            "type": "retain",
                            "operation_type": "retain",
                            "bank_id": "engineering",
                        },
                        schema=None,
                    )
                    await poller.execute_task(sibling)
                    await asyncio.sleep(0)
                    await poller.execute_task(failing)
                    try:
                        await asyncio.wait_for(
                            _shutdown_requested.wait(),
                            timeout=0.5,
                        )
                        self.assertTrue(poller._shutdown.is_set())
                        with self.assertRaisesRegex(
                            RuntimeError,
                            _expected_error,
                        ):
                            await poller.shutdown_graceful(timeout=0.25)
                        self.assertEqual(
                            await poller._claim_batch_for_schema_inner(
                                None,
                                {},
                                1,
                            ),
                            [],
                        )
                    finally:
                        for info in list(poller._active_tasks.values()):
                            if not info.bg_task.done():
                                info.bg_task.cancel()
                        await asyncio.gather(
                            *[
                                info.bg_task
                                for info in poller._active_tasks.values()
                            ],
                            return_exceptions=True,
                        )

                asyncio.run(exercise())
                self.assertEqual(sql_calls, [])
                self.assertEqual(
                    [event for event in events if event[0] == "shutdown"],
                    [("shutdown", "requested")],
                )
                self.assertLess(
                    events.index(("shutdown", "requested")),
                    events.index(("cleanup", "operation-a")),
                )
                self.assertLess(
                    events.index(("sibling", "quiesced")),
                    next(
                        index
                        for index, event in enumerate(events)
                        if event[0] == "release"
                    ),
                )
                self.assertEqual(
                    row["status"],
                    (
                        "pending"
                        if failure_mode == "terminal-write"
                        else "failed"
                    ),
                )
                self.assertIn(
                    (
                        "failure",
                        "operation-a",
                        "failure.terminal-state",
                        "terminal_state_persistence",
                        False,
                        "RuntimeError",
                    ),
                    events,
                )

    def test_phase_one_shutdown_waits_for_plan_bound_statement_cancellation(self):
        shutdown_requests = []

        class Adapter(_ControlConnectionAdapterMixin):
            phase_one_timeout_seconds = 0.001
            phase_one_statement_timeout_seconds = 0.05

            def record_upstream_stage(self, _operation_id, _stage):
                return None

        class WorkerPoller(_RunCapableWorkerPoller):
            def __init__(self):
                self._shutdown = asyncio.Event()

            async def _claim_batch_for_schema_inner(self, *_arguments):
                return []

            async def _claim_batch_for_schema(self, schema, reserved, shared):
                return await self._claim_batch_for_schema_inner(
                    schema, reserved, shared
                )

            async def _execute_task_inner(self, _task, holder):
                holder.stage = "retain.phase1.candidates.fuzzy.1/2"
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    await asyncio.sleep(0.02)

            async def _claim_batch_for_schema_inner(self, *_arguments):
                return []

            async def _claim_batch_for_schema(self, schema, reserved, shared):
                return await self._claim_batch_for_schema_inner(
                    schema,
                    reserved,
                    shared,
                )

        guard_globals = install_exact_drain_runtime_guards.__globals__
        original_cancellation_timeout = guard_globals[
            "EXACT_DRAIN_TASK_CANCELLATION_TIMEOUT_SECONDS"
        ]
        guard_globals["EXACT_DRAIN_TASK_CANCELLATION_TIMEOUT_SECONDS"] = 0.01
        try:
            install_exact_drain_runtime_guards(
                type("PostgreSQLOps", (), {}),
                WorkerPoller,
                type("MemoryEngine", (), {}),
                Adapter(),
                request_worker_shutdown=lambda: shutdown_requests.append(True),
            )

            async def exercise():
                poller = WorkerPoller()
                with self.assertRaisesRegex(
                    OperationRecoveryError,
                    "retain phase one exceeded its deadline",
                ):
                    await poller._execute_task_inner(
                        SimpleNamespace(operation_id="operation-1"),
                        SimpleNamespace(stage="queued.retain"),
                    )

            asyncio.run(exercise())
        finally:
            guard_globals["EXACT_DRAIN_TASK_CANCELLATION_TIMEOUT_SECONDS"] = (
                original_cancellation_timeout
            )
        self.assertEqual(shutdown_requests, [True])

    def test_public_shutdown_waits_for_plan_bound_phase_one_quiescence(self):
        try:
            from hindsight_api.worker.poller import ClaimedTask
            from hindsight_api.worker.poller import (
                WorkerPoller as UpstreamWorkerPoller,
            )
        except ImportError as error:
            raise unittest.SkipTest(
                "hindsight_api worker runtime is unavailable"
            ) from error

        row = {"status": "processing"}
        task_quiesced = asyncio.Event()
        shutdown_requested = asyncio.Event()

        class Adapter(_ControlConnectionAdapterMixin):
            phase_one_timeout_seconds = 0.001
            phase_one_statement_timeout_seconds = 0.6

            def record_upstream_stage(self, _operation_id, _stage):
                return None

            async def recover_own_tasks(self, _backend):
                return 0

            async def release_own_tasks(self, _backend):
                if not task_quiesced.is_set():
                    raise AssertionError("claim released before task quiescence")
                row["status"] = "pending"
                return 1

        class WorkerPoller(UpstreamWorkerPoller):
            async def claim_batch(self):
                if getattr(self, "_test_delivered", False):
                    return []
                self._test_delivered = True
                return [
                    ClaimedTask(
                        operation_id="operation-1",
                        task_dict={
                            "type": "retain",
                            "operation_type": "retain",
                            "bank_id": "engineering",
                        },
                        schema=None,
                    )
                ]

            async def _log_progress_if_due(self):
                return None

            async def _execute_task_inner(self, _task, holder):
                holder.stage = "retain.phase1.candidates.fuzzy.1/2"
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    await asyncio.sleep(0.55)
                    task_quiesced.set()

        guard_globals = install_exact_drain_runtime_guards.__globals__
        original_cancellation_timeout = guard_globals[
            "EXACT_DRAIN_TASK_CANCELLATION_TIMEOUT_SECONDS"
        ]
        guard_globals["EXACT_DRAIN_TASK_CANCELLATION_TIMEOUT_SECONDS"] = 0.01
        try:
            install_exact_drain_runtime_guards(
                type("PostgreSQLOps", (), {}),
                WorkerPoller,
                type("MemoryEngine", (), {}),
                Adapter(),
                request_worker_shutdown=shutdown_requested.set,
            )

            async def exercise():
                poller = object.__new__(WorkerPoller)
                poller._backend = "exact-backend"
                poller._worker_id = "exact-worker"
                poller._shutdown = asyncio.Event()
                poller._poll_interval_ms = 1
                poller._slot_reservations = {}
                poller._max_slots = 1
                poller._in_flight_lock = asyncio.Lock()
                poller._in_flight_count = 0
                poller._in_flight_by_type = {}
                poller._active_tasks = {}
                poller_task = asyncio.create_task(poller.run())
                await asyncio.wait_for(shutdown_requested.wait(), timeout=1.0)
                with self.assertRaisesRegex(
                    OperationRecoveryError,
                    "retain phase one exceeded its deadline",
                ):
                    await poller.shutdown_graceful(timeout=0.0)
                await asyncio.wait_for(poller_task, timeout=1.0)

            with patch(
                "hindsight_api.worker.poller._CANCEL_DRAIN_TIMEOUT",
                0.005,
            ):
                asyncio.run(exercise())
        finally:
            guard_globals["EXACT_DRAIN_TASK_CANCELLATION_TIMEOUT_SECONDS"] = (
                original_cancellation_timeout
            )
        self.assertTrue(task_quiesced.is_set())
        self.assertEqual(row, {"status": "pending"})

    def test_external_shutdown_waits_for_phase_one_quiescence(self):
        nested_quiesced = asyncio.Event()
        release_observations = []

        class Adapter(_ControlConnectionAdapterMixin):
            phase_one_timeout_seconds = 60.0
            phase_one_statement_timeout_seconds = 0.05

            def record_upstream_stage(self, _operation_id, _stage):
                return None

            async def release_own_tasks(self, _backend):
                release_observations.append(nested_quiesced.is_set())
                return 1

        class WorkerPoller(_RunCapableWorkerPoller):
            def __init__(self):
                self._backend = "exact-backend"
                self._shutdown = asyncio.Event()
                self._active_tasks = {}

            async def _execute_task_inner(self, _task, holder):
                holder.stage = "retain.phase1.blocked"
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    await asyncio.sleep(0.02)
                    nested_quiesced.set()

            async def _claim_batch_for_schema_inner(self, *_arguments):
                return []

            async def _claim_batch_for_schema(self, schema, reserved, shared):
                return await self._claim_batch_for_schema_inner(
                    schema,
                    reserved,
                    shared,
                )

            async def shutdown_graceful(self, timeout=30.0):
                self._shutdown.set()
                tasks = [
                    info.bg_task
                    for info in self._active_tasks.values()
                ]
                _done, pending = await asyncio.wait(tasks, timeout=timeout)
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.wait(pending, timeout=0.005)
                await self.release_own_tasks()

        guard_globals = install_exact_drain_runtime_guards.__globals__
        original_cancellation_timeout = guard_globals[
            "EXACT_DRAIN_TASK_CANCELLATION_TIMEOUT_SECONDS"
        ]
        guard_globals["EXACT_DRAIN_TASK_CANCELLATION_TIMEOUT_SECONDS"] = 0.01
        try:
            install_exact_drain_runtime_guards(
                type("PostgreSQLOps", (), {}),
                WorkerPoller,
                type("MemoryEngine", (), {}),
                Adapter(),
                request_worker_shutdown=lambda: None,
            )

            async def exercise():
                poller = WorkerPoller()
                operation = SimpleNamespace(operation_id="operation-1")
                holder = SimpleNamespace(stage="queued.retain")
                outer = asyncio.create_task(
                    poller._execute_task_inner(operation, holder)
                )
                poller._active_tasks = {
                    operation.operation_id: SimpleNamespace(bg_task=outer)
                }
                await asyncio.sleep(0)
                await poller.shutdown_graceful(timeout=0.0)
                await asyncio.gather(outer, return_exceptions=True)

            asyncio.run(exercise())
        finally:
            guard_globals["EXACT_DRAIN_TASK_CANCELLATION_TIMEOUT_SECONDS"] = (
                original_cancellation_timeout
            )
        self.assertEqual(release_observations, [True])

    def test_public_shutdown_never_releases_a_nonquiescent_phase_one_task(self):
        shutdown_requests = []
        nested_tasks = []
        release_observations = []

        class Adapter(_ControlConnectionAdapterMixin):
            phase_one_timeout_seconds = 60.0
            phase_one_statement_timeout_seconds = 0.05

            def record_upstream_stage(self, _operation_id, _stage):
                return None

            async def release_own_tasks(self, _backend):
                release_observations.append(
                    [task.done() for task in nested_tasks]
                )
                return 1

        class WorkerPoller(_RunCapableWorkerPoller):
            def __init__(self):
                self._backend = "exact-backend"
                self._shutdown = asyncio.Event()
                self._active_tasks = {}

            async def _execute_task_inner(self, _task, holder):
                holder.stage = "retain.phase1.blocked"
                nested_tasks.append(asyncio.current_task())
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    await asyncio.Event().wait()

            async def _claim_batch_for_schema_inner(self, *_arguments):
                return []

            async def _claim_batch_for_schema(self, schema, reserved, shared):
                return await self._claim_batch_for_schema_inner(
                    schema,
                    reserved,
                    shared,
                )

            async def shutdown_graceful(self, timeout=30.0):
                self._shutdown.set()
                tasks = [
                    info.bg_task
                    for info in self._active_tasks.values()
                ]
                _done, pending = await asyncio.wait(tasks, timeout=timeout)
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.wait(pending, timeout=0.01)
                await self.release_own_tasks()

        guard_globals = install_exact_drain_runtime_guards.__globals__
        original_cancellation_timeout = guard_globals[
            "EXACT_DRAIN_TASK_CANCELLATION_TIMEOUT_SECONDS"
        ]
        guard_globals["EXACT_DRAIN_TASK_CANCELLATION_TIMEOUT_SECONDS"] = 0.01
        try:
            install_exact_drain_runtime_guards(
                type("PostgreSQLOps", (), {}),
                WorkerPoller,
                type("MemoryEngine", (), {}),
                Adapter(),
                request_worker_shutdown=lambda: shutdown_requests.append(True),
            )

            async def exercise():
                poller = WorkerPoller()
                operation = SimpleNamespace(operation_id="operation-1")
                holder = SimpleNamespace(stage="queued.retain")
                outer = asyncio.create_task(
                    poller._execute_task_inner(operation, holder)
                )
                poller._active_tasks = {
                    operation.operation_id: SimpleNamespace(bg_task=outer)
                }
                try:
                    await asyncio.sleep(0)
                    with self.assertRaisesRegex(
                        OperationRecoveryError,
                        "claim release is disabled after failed quiescence",
                    ):
                        await poller.shutdown_graceful(timeout=0.0)
                finally:
                    for task in nested_tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(
                        *nested_tasks,
                        return_exceptions=True,
                    )
                    if not outer.done():
                        outer.cancel()
                    await asyncio.gather(outer, return_exceptions=True)

            asyncio.run(exercise())
        finally:
            guard_globals["EXACT_DRAIN_TASK_CANCELLATION_TIMEOUT_SECONDS"] = (
                original_cancellation_timeout
            )
        self.assertEqual(release_observations, [])

    def test_cancelled_exact_terminal_mutation_does_not_request_shutdown(self):
        shutdown_requests = []

        class Adapter(_ControlConnectionAdapterMixin):
            def record_upstream_stage(self, _operation_id, _stage):
                return None

            async def mark_failed(self, *_arguments):
                raise asyncio.CancelledError

        class WorkerPoller(_RunCapableWorkerPoller):
            def __init__(self):
                self._backend = "exact-backend"
                self._shutdown = asyncio.Event()

            async def _execute_task_inner(self, task, _holder):
                await self._mark_failed(
                    task.operation_id,
                    "cancelled",
                    None,
                )

            async def _claim_batch_for_schema_inner(self, *_arguments):
                return []

            async def _claim_batch_for_schema(self, schema, reserved, shared):
                return await self._claim_batch_for_schema_inner(
                    schema,
                    reserved,
                    shared,
                )

        install_exact_drain_runtime_guards(
            type("PostgreSQLOps", (), {}),
            WorkerPoller,
            type("MemoryEngine", (), {}),
            Adapter(),
            request_worker_shutdown=lambda: shutdown_requests.append(True),
        )

        async def exercise():
            poller = WorkerPoller()
            with self.assertRaises(asyncio.CancelledError):
                await poller._execute_task_inner(
                    SimpleNamespace(operation_id="operation-1"),
                    SimpleNamespace(stage="queued.retain"),
                )
            self.assertFalse(poller._shutdown.is_set())
            self.assertFalse(
                hasattr(poller, "_exact_drain_task_errors")
            )

        asyncio.run(exercise())
        self.assertEqual(shutdown_requests, [])

    def test_shutdown_serializes_with_a_committing_claim(self):
        events = []
        claim_entered = asyncio.Event()
        allow_claim_commit = asyncio.Event()
        task = type("Task", (), {"operation_id": "operation-1"})()

        class Adapter(_ControlConnectionAdapterMixin):
            def claim_committed(self, tasks):
                events.append(("committed", list(tasks)))

            async def release_own_tasks(self, _backend):
                events.append(("released", [task]))
                return 1

        class PostgreSQLOps:
            pass

        class WorkerPoller(_RunCapableWorkerPoller):
            def __init__(self):
                self._backend = "exact-backend"
                self._shutdown = asyncio.Event()
                self._active_tasks = {}

            async def _execute_task_inner(self, _task, _holder):
                return None

            async def _claim_batch_for_schema_inner(
                self,
                _schema,
                _reserved_limits,
                _shared_limit,
            ):
                claim_entered.set()
                await allow_claim_commit.wait()
                return [task]

            async def _claim_batch_for_schema(self, schema, reserved, shared):
                return await self._claim_batch_for_schema_inner(
                    schema,
                    reserved,
                    shared,
                )

            async def shutdown_graceful(self, timeout=30.0):
                del timeout
                self._shutdown.set()
                await self.release_own_tasks()

        class MemoryEngine:
            pass

        install_exact_drain_runtime_guards(
            PostgreSQLOps,
            WorkerPoller,
            MemoryEngine,
            Adapter(),
        )

        async def exercise():
            poller = WorkerPoller()
            claim = asyncio.create_task(
                poller._claim_batch_for_schema_inner(None, {}, 1)
            )
            await asyncio.wait_for(claim_entered.wait(), timeout=1.0)
            shutdown = asyncio.create_task(
                poller.shutdown_graceful(timeout=0.25)
            )
            await asyncio.wait_for(poller._shutdown.wait(), timeout=1.0)
            await asyncio.sleep(0)
            self.assertFalse(shutdown.done())
            allow_claim_commit.set()
            self.assertEqual(await claim, [])
            await shutdown

        asyncio.run(exercise())
        self.assertEqual(
            events,
            [
                ("committed", [task]),
                ("released", [task]),
            ],
        )

    def test_public_claim_does_not_swallow_post_commit_progress_failure(self):
        operation_id = "00000000-0000-4000-8000-000000000001"
        task = type("Task", (), {"operation_id": operation_id})()
        recorder = Mock()
        recorder.task_stage.side_effect = RuntimeError("progress failed")
        aborts = []
        adapter = object.__new__(ExactDrainClaimAdapter)
        adapter._selected = {operation_id: {}}
        adapter._started_ids = set()
        adapter._pending_progress_stages = {}
        adapter._progress_recorder = recorder
        adapter._completion_callback = lambda: aborts.append(True)
        adapter.reserve_control_connection = AsyncMock()
        adapter.close_control_connection = AsyncMock()

        class WorkerPoller(_RunCapableWorkerPoller):
            def __init__(self):
                self._shutdown = asyncio.Event()

            async def _claim_batch_for_schema_inner(self, *_arguments):
                return []

            async def _claim_batch_for_schema(self, schema, reserved, shared):
                return await self._claim_batch_for_schema_inner(
                    schema, reserved, shared
                )

            async def _execute_task_inner(self, _task, _holder):
                return None

            async def _claim_batch_for_schema_inner(
                self,
                _schema,
                _reserved_limits,
                _shared_limit,
            ):
                return [task]

            async def _claim_batch_for_schema(self, schema, reserved, shared):
                try:
                    return await self._claim_batch_for_schema_inner(
                        schema, reserved, shared
                    )
                except Exception:
                    return []

            async def claim_batch(self):
                return await self._claim_batch_for_schema(None, {}, 1)

            async def run(self):
                while not self._shutdown.is_set():
                    try:
                        await self.claim_batch()
                    except Exception:
                        await asyncio.sleep(0)

        install_exact_drain_runtime_guards(
            type("PostgreSQLOps", (), {}),
            WorkerPoller,
            type("MemoryEngine", (), {}),
            adapter,
            request_worker_shutdown=lambda: None,
        )

        poller = WorkerPoller()
        asyncio.run(asyncio.wait_for(poller.run(), timeout=1))
        self.assertEqual(adapter._started_ids, {operation_id})
        self.assertTrue(poller._shutdown.is_set())
        self.assertEqual(aborts, [True])

    def test_runtime_guard_projects_upstream_stage_holder_changes(self):
        stages = []

        class Adapter(_ControlConnectionAdapterMixin):
            def record_upstream_stage(self, operation_id, stage):
                stages.append((operation_id, stage))

        class PostgreSQLOps:
            pass

        class WorkerPoller(_RunCapableWorkerPoller):
            async def _claim_batch_for_schema_inner(
                self,
                _schema,
                _reserved_limits,
                _shared_limit,
            ):
                return []

            async def _claim_batch_for_schema(self, schema, reserved, shared):
                return await self._claim_batch_for_schema_inner(
                    schema, reserved, shared
                )

            async def _execute_task_inner(self, task, holder):
                holder.stage = "retain.phase1.resolve"
                await asyncio.sleep(0.6)
                holder.stage = "llm.codex.retain.attempt=1/1"
                return "done"

        class MemoryEngine:
            pass

        install_exact_drain_runtime_guards(
            PostgreSQLOps,
            WorkerPoller,
            MemoryEngine,
            Adapter(),
        )

        async def exercise():
            task = type("Task", (), {"operation_id": "operation-1"})()
            holder = type("Holder", (), {"stage": "queued.retain"})()
            return await WorkerPoller()._execute_task_inner(task, holder)

        self.assertEqual(asyncio.run(exercise()), "done")
        self.assertEqual(
            stages,
            [
                ("operation-1", "queued.retain"),
                ("operation-1", "retain.phase1.resolve"),
                ("operation-1", "llm.codex.retain.attempt=1/1"),
            ],
        )

    def test_runtime_guard_bounds_retain_phase_one_after_breadcrumb(self):
        events = []

        class Adapter(_ControlConnectionAdapterMixin):
            phase_one_timeout_seconds = 0.001

            def record_upstream_stage(self, _operation_id, _stage):
                return None

        class PostgreSQLOps:
            pass

        class WorkerPoller(_RunCapableWorkerPoller):
            _backend = "exact-backend"

            def __init__(self):
                self._shutdown = asyncio.Event()

            async def _claim_batch_for_schema_inner(
                self,
                _schema,
                _reserved_limits,
                _shared_limit,
            ):
                return []

            async def _claim_batch_for_schema(self, schema, reserved, shared):
                return await self._claim_batch_for_schema_inner(
                    schema, reserved, shared
                )

            async def _execute_task_inner(self, _task, holder):
                holder.stage = "retain.phase1.candidates"
                try:
                    await asyncio.Event().wait()
                finally:
                    events.append("cancelled")

        class MemoryEngine:
            pass

        install_exact_drain_runtime_guards(
            PostgreSQLOps,
            WorkerPoller,
            MemoryEngine,
            Adapter(),
            request_worker_shutdown=lambda: events.append("shutdown"),
        )

        async def exercise():
            task = type("Task", (), {"operation_id": "operation-1"})()
            holder = type("Holder", (), {"stage": "queued.retain"})()
            return await WorkerPoller()._execute_task_inner(task, holder)

        with self.assertRaisesRegex(
            OperationRecoveryError,
            "retain phase one exceeded its deadline",
        ):
            asyncio.run(exercise())
        self.assertEqual(events, ["shutdown", "cancelled"])

    def test_schema_eleven_phase_one_deadline_survives_llm_breadcrumb(self):
        events = []

        class Adapter(_ControlConnectionAdapterMixin):
            phase_one_timeout_seconds = 0.3
            phase_one_nested_stage_prefixes = ("llm.",)

            def record_upstream_stage(self, _operation_id, _stage):
                return None

        class WorkerPoller(_RunCapableWorkerPoller):
            def __init__(self):
                self._shutdown = asyncio.Event()

            async def _claim_batch_for_schema_inner(self, *_arguments):
                return []

            async def _claim_batch_for_schema(self, schema, reserved, shared):
                return await self._claim_batch_for_schema_inner(
                    schema, reserved, shared
                )

            async def _execute_task_inner(self, _task, holder):
                holder.stage = "retain.phase1.resolve"
                await asyncio.sleep(0.26)
                holder.stage = "llm.codex.retain.attempt=1/1"
                try:
                    await asyncio.Event().wait()
                finally:
                    events.append("cancelled")

        install_exact_drain_runtime_guards(
            type("PostgreSQLOps", (), {}),
            WorkerPoller,
            type("MemoryEngine", (), {}),
            Adapter(),
            request_worker_shutdown=lambda: events.append("shutdown"),
        )

        async def exercise():
            return await WorkerPoller()._execute_task_inner(
                SimpleNamespace(operation_id="operation-1"),
                SimpleNamespace(stage="queued.retain"),
            )

        with self.assertRaisesRegex(
            OperationRecoveryError,
            "retain phase one exceeded its deadline",
        ):
            asyncio.run(exercise())
        self.assertEqual(events, ["shutdown", "cancelled"])

    def test_schema_eleven_operation_attempt_deadline_bounds_all_stages(self):
        events = []

        class Adapter(_ControlConnectionAdapterMixin):
            operation_attempt_timeout_seconds = 0.01
            phase_one_nested_stage_prefixes = ("llm.",)

            def record_upstream_stage(self, _operation_id, _stage):
                return None

        class WorkerPoller(_RunCapableWorkerPoller):
            def __init__(self):
                self._shutdown = asyncio.Event()

            async def _claim_batch_for_schema_inner(self, *_arguments):
                return []

            async def _claim_batch_for_schema(self, schema, reserved, shared):
                return await self._claim_batch_for_schema_inner(
                    schema, reserved, shared
                )

            async def _execute_task_inner(self, _task, holder):
                holder.stage = "retain.phase2.insert_facts"
                try:
                    await asyncio.Event().wait()
                finally:
                    events.append("cancelled")

        install_exact_drain_runtime_guards(
            type("PostgreSQLOps", (), {}),
            WorkerPoller,
            type("MemoryEngine", (), {}),
            Adapter(),
            request_worker_shutdown=lambda: events.append("shutdown"),
        )

        async def exercise():
            return await WorkerPoller()._execute_task_inner(
                SimpleNamespace(operation_id="operation-1"),
                SimpleNamespace(stage="queued.retain"),
            )

        with self.assertRaisesRegex(
            OperationRecoveryError,
            "operation attempt exceeded its deadline",
        ):
            asyncio.run(exercise())
        self.assertEqual(events, ["shutdown", "cancelled"])

    def test_phase_one_timeout_never_releases_before_task_quiescence(self):
        release_calls = []
        stages = []
        failures = []

        class Adapter(_ControlConnectionAdapterMixin):
            phase_one_timeout_seconds = 0.001

            def record_upstream_stage(self, operation_id, stage):
                stages.append((operation_id, stage))

            def record_upstream_failure(
                self,
                operation_id,
                *,
                stage,
                category,
                retryable,
                error_message,
            ):
                failures.append(
                    (
                        operation_id,
                        stage,
                        category,
                        retryable,
                        type(error_message).__name__,
                    )
                )

            async def release_own_tasks(self, _backend):
                release_calls.append(True)
                return 1

        class PostgreSQLOps:
            pass

        class WorkerPoller(_RunCapableWorkerPoller):
            _backend = "exact-backend"

            def __init__(self):
                self._shutdown = asyncio.Event()

            async def _claim_batch_for_schema_inner(
                self,
                _schema,
                _reserved_limits,
                _shared_limit,
            ):
                return []

            async def _claim_batch_for_schema(self, schema, reserved, shared):
                return await self._claim_batch_for_schema_inner(
                    schema, reserved, shared
                )

            async def _execute_task_inner(self, _task, holder):
                holder.stage = "retain.phase1.cooccurrence"
                ignored = False
                while True:
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        if ignored:
                            raise
                        ignored = True

        class MemoryEngine:
            pass

        guard_globals = install_exact_drain_runtime_guards.__globals__
        original_cancellation_timeout = guard_globals[
            "EXACT_DRAIN_TASK_CANCELLATION_TIMEOUT_SECONDS"
        ]
        guard_globals["EXACT_DRAIN_TASK_CANCELLATION_TIMEOUT_SECONDS"] = 0.0
        try:
            install_exact_drain_runtime_guards(
                PostgreSQLOps,
                WorkerPoller,
                MemoryEngine,
                Adapter(),
                request_worker_shutdown=lambda: release_calls.append(
                    "shutdown"
                ),
            )

            async def exercise():
                poller = WorkerPoller()
                task = type("Task", (), {"operation_id": "operation-1"})()
                holder = type("Holder", (), {"stage": "queued.retain"})()
                with self.assertRaisesRegex(
                    OperationRecoveryError,
                    "did not quiesce",
                ):
                    await poller._execute_task_inner(task, holder)
                with self.assertRaisesRegex(
                    OperationRecoveryError,
                    "claim release is disabled",
                ):
                    await poller.release_own_tasks()

            asyncio.run(exercise())
            self.assertEqual(release_calls, ["shutdown"])
            self.assertEqual(
                failures,
                [
                    (
                        "operation-1",
                        "failure.nonquiescent",
                        "nonquiescent_shutdown",
                        False,
                        "OperationRecoveryError",
                    )
                ],
            )
        finally:
            guard_globals["EXACT_DRAIN_TASK_CANCELLATION_TIMEOUT_SECONDS"] = (
                original_cancellation_timeout
            )

    def test_public_shutdown_never_releases_a_child_ignoring_cancellation(self):
        release_calls = []
        shutdown_requested = asyncio.Event()
        child_tasks = []

        class Adapter(_ControlConnectionAdapterMixin):
            phase_one_timeout_seconds = 0.001

            def record_upstream_stage(self, _operation_id, _stage):
                return None

            async def release_own_tasks(self, _backend):
                release_calls.append(True)
                return 1

        class PostgreSQLOps:
            pass

        class WorkerPoller(_RunCapableWorkerPoller):
            def __init__(self):
                self._backend = "exact-backend"
                self._shutdown = asyncio.Event()
                self._active_tasks = {}

            async def _claim_batch_for_schema_inner(
                self,
                _schema,
                _reserved_limits,
                _shared_limit,
            ):
                return []

            async def _claim_batch_for_schema(self, schema, reserved, shared):
                return await self._claim_batch_for_schema_inner(
                    schema,
                    reserved,
                    shared,
                )

            async def _execute_task_inner(self, _task, holder):
                holder.stage = "retain.phase1.cooccurrence"
                child_tasks.append(asyncio.current_task())
                ignored = False
                while True:
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        if ignored:
                            raise
                        ignored = True

            async def shutdown_graceful(self, timeout=30.0):
                self._shutdown.set()
                tasks = [info.bg_task for info in self._active_tasks.values()]
                _done, pending = await asyncio.wait(tasks, timeout=timeout)
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.wait(pending, timeout=0.1)
                await self.release_own_tasks()

        class MemoryEngine:
            pass

        guard_globals = install_exact_drain_runtime_guards.__globals__
        original_cancellation_timeout = guard_globals[
            "EXACT_DRAIN_TASK_CANCELLATION_TIMEOUT_SECONDS"
        ]
        guard_globals["EXACT_DRAIN_TASK_CANCELLATION_TIMEOUT_SECONDS"] = 0.01
        try:
            install_exact_drain_runtime_guards(
                PostgreSQLOps,
                WorkerPoller,
                MemoryEngine,
                Adapter(),
                request_worker_shutdown=shutdown_requested.set,
            )

            async def exercise():
                poller = WorkerPoller()
                task = type("Task", (), {"operation_id": "operation-1"})()
                holder = type("Holder", (), {"stage": "queued.retain"})()
                execution = asyncio.create_task(
                    poller._execute_task_inner(task, holder)
                )
                poller._active_tasks = {
                    task.operation_id: SimpleNamespace(bg_task=execution),
                }
                await asyncio.wait_for(shutdown_requested.wait(), timeout=1.0)
                with self.assertRaisesRegex(
                    OperationRecoveryError,
                    "claim release is disabled",
                ):
                    await poller.shutdown_graceful(timeout=0.0)
                for child in child_tasks:
                    if child is not None and not child.done():
                        child.cancel()
                if child_tasks:
                    await asyncio.gather(*child_tasks, return_exceptions=True)

            asyncio.run(exercise())
            self.assertEqual(release_calls, [])
        finally:
            guard_globals["EXACT_DRAIN_TASK_CANCELLATION_TIMEOUT_SECONDS"] = (
                original_cancellation_timeout
            )

    def test_phase_one_deadline_quiesces_shuts_down_and_releases_owned_row(self):
        try:
            from hindsight_api.worker import main as worker_main_module
            from hindsight_api.worker.poller import ClaimedTask
            from hindsight_api.worker.poller import (
                WorkerPoller as UpstreamWorkerPoller,
            )
        except ImportError as error:
            raise unittest.SkipTest(
                "hindsight_api worker runtime is unavailable"
            ) from error

        events = []
        row = {"status": "processing"}
        task_quiesced = asyncio.Event()
        worker_main_shutdown = asyncio.Event()

        class Adapter(_ControlConnectionAdapterMixin):
            phase_one_timeout_seconds = 0.001

            def record_upstream_stage(self, _operation_id, stage):
                events.append(("stage", stage))

            async def recover_own_tasks(self, _backend):
                events.append(("recover", row["status"]))
                return 0

            async def release_own_tasks(self, _backend):
                if not task_quiesced.is_set():
                    raise AssertionError("owned row released before task quiescence")
                if row["status"] != "processing":
                    return 0
                row["status"] = "pending"
                events.append(("release", "pending"))
                return 1

        class PostgreSQLOps:
            pass

        class WorkerPoller(UpstreamWorkerPoller):
            async def claim_batch(self):
                if any(event[0] == "release" for event in events):
                    raise AssertionError("claim attempted after exact release")
                if not getattr(self, "_test_delivered", False):
                    self._test_delivered = True
                    events.append(("claim", row["status"]))
                    return [
                        ClaimedTask(
                            operation_id="operation-1",
                            task_dict={
                                "type": "retain",
                                "operation_type": "retain",
                                "bank_id": "engineering",
                            },
                            schema=None,
                        )
                    ]
                return []

            async def _log_progress_if_due(self):
                return None

            async def _execute_task_inner(self, _task, holder):
                holder.stage = "retain.phase1.cooccurrence"
                try:
                    await asyncio.Event().wait()
                finally:
                    task_quiesced.set()
                events.append(("provider", "after-quiescence"))

        class MemoryEngine:
            pass

        shutdown_bridge = ExactDrainWorkerMainShutdownBridge(
            worker_main_module
        )

        async def exercise():
            loop = asyncio.get_running_loop()
            installed = worker_main_module._install_shutdown_signal_handlers(
                loop,
                worker_main_shutdown.set,
            )
            poller = object.__new__(WorkerPoller)
            poller._backend = "exact-backend"
            poller._worker_id = "exact-worker"
            poller._shutdown = asyncio.Event()
            poller._poll_interval_ms = 1
            poller._slot_reservations = {}
            poller._max_slots = 1
            poller._in_flight_lock = asyncio.Lock()
            poller._in_flight_count = 0
            poller._in_flight_by_type = {}
            poller._active_tasks = {}
            try:
                poller_task = asyncio.create_task(poller.run())
                await asyncio.wait_for(
                    worker_main_shutdown.wait(),
                    timeout=1.0,
                )
                with self.assertRaisesRegex(
                    OperationRecoveryError,
                    "retain phase one exceeded its deadline",
                ):
                    await poller.shutdown_graceful(timeout=0.25)
                await asyncio.wait_for(poller_task, timeout=1.0)
                await asyncio.sleep(0)
                return poller
            finally:
                if installed:
                    loop.remove_signal_handler(signal.SIGINT)
                    loop.remove_signal_handler(signal.SIGTERM)

        with shutdown_bridge:
            install_exact_drain_runtime_guards(
                PostgreSQLOps,
                WorkerPoller,
                MemoryEngine,
                Adapter(),
                request_worker_shutdown=shutdown_bridge.request,
            )
            poller = asyncio.run(exercise())
        self.assertTrue(poller._shutdown.is_set())
        self.assertEqual(row["status"], "pending")
        self.assertIn(("release", "pending"), events)
        self.assertNotIn(("provider", "after-quiescence"), events)

    def test_phase_one_deadline_quiesces_sibling_before_owned_row_release(self):
        try:
            from hindsight_api.worker.poller import ClaimedTask
            from hindsight_api.worker.poller import (
                WorkerPoller as UpstreamWorkerPoller,
            )
        except ImportError as error:
            raise unittest.SkipTest(
                "hindsight_api worker runtime is unavailable"
            ) from error

        rows = {
            "operation-a": "processing",
            "operation-b": "processing",
        }
        quiesced = {operation_id: asyncio.Event() for operation_id in rows}
        events = []
        worker_main_shutdown = asyncio.Event()

        class Adapter(_ControlConnectionAdapterMixin):
            phase_one_timeout_seconds = 0.001

            def record_upstream_stage(self, _operation_id, _stage):
                return None

            async def recover_own_tasks(self, _backend):
                return 0

            async def release_own_tasks(self, _backend):
                if not all(event.is_set() for event in quiesced.values()):
                    raise AssertionError(
                        "owned rows released while a sibling task could write"
                    )
                for operation_id in rows:
                    rows[operation_id] = "pending"
                events.append("release")
                return 2

        class PostgreSQLOps:
            pass

        class WorkerPoller(UpstreamWorkerPoller):
            async def claim_batch(self):
                if self._shutdown.is_set():
                    raise AssertionError("claim attempted after shutdown")
                if getattr(self, "_test_delivered", False):
                    return []
                self._test_delivered = True
                return [
                    ClaimedTask(
                        operation_id=operation_id,
                        task_dict={
                            "type": "retain",
                            "operation_type": "retain",
                            "bank_id": "engineering",
                        },
                        schema=None,
                    )
                    for operation_id in rows
                ]

            async def _log_progress_if_due(self):
                return None

            async def _execute_task_inner(self, task, holder):
                if task.operation_id == "operation-a":
                    holder.stage = "retain.phase1.cooccurrence"
                try:
                    await asyncio.Event().wait()
                finally:
                    quiesced[task.operation_id].set()
                events.append(("provider", task.operation_id))

        class MemoryEngine:
            pass

        install_exact_drain_runtime_guards(
            PostgreSQLOps,
            WorkerPoller,
            MemoryEngine,
            Adapter(),
            request_worker_shutdown=worker_main_shutdown.set,
        )

        async def exercise():
            poller = object.__new__(WorkerPoller)
            poller._backend = "exact-backend"
            poller._worker_id = "exact-worker"
            poller._shutdown = asyncio.Event()
            poller._poll_interval_ms = 1
            poller._slot_reservations = {}
            poller._max_slots = 2
            poller._in_flight_lock = asyncio.Lock()
            poller._in_flight_count = 0
            poller._in_flight_by_type = {}
            poller._active_tasks = {}
            poller_task = asyncio.create_task(poller.run())
            await asyncio.wait_for(
                worker_main_shutdown.wait(),
                timeout=1.0,
            )
            self.assertNotIn("release", events)
            with self.assertRaisesRegex(
                OperationRecoveryError,
                "retain phase one exceeded its deadline",
            ):
                await poller.shutdown_graceful(timeout=30.0)
            await asyncio.wait_for(poller_task, timeout=1.0)

        asyncio.run(exercise())
        self.assertEqual(rows, {
            "operation-a": "pending",
            "operation-b": "pending",
        })
        self.assertEqual(events, ["release"])

    def test_phase_one_recorder_failure_shuts_down_releases_then_surfaces(self):
        events = []
        shutdown_requested = asyncio.Event()

        class Adapter(_ControlConnectionAdapterMixin):
            phase_one_timeout_seconds = 30.0

            def record_upstream_stage(self, _operation_id, stage):
                events.append(("stage", stage))
                if stage == "retain.phase1.candidates":
                    raise RuntimeError("progress recorder failed")

            async def release_own_tasks(self, _backend):
                events.append(("release", "owned"))
                return 1

        class PostgreSQLOps:
            pass

        class WorkerPoller(_RunCapableWorkerPoller):
            def __init__(self):
                self._backend = "exact-backend"
                self._shutdown = asyncio.Event()
                self._active_tasks = {}

            async def _claim_batch_for_schema_inner(
                self,
                _schema,
                _reserved_limits,
                _shared_limit,
            ):
                events.append(("claim", "upstream"))
                return []

            async def _claim_batch_for_schema(self, schema, reserved, shared):
                return await self._claim_batch_for_schema_inner(
                    schema,
                    reserved,
                    shared,
                )

            async def _execute_task_inner(self, _task, holder):
                holder.stage = "retain.phase1.candidates"
                try:
                    await asyncio.Event().wait()
                finally:
                    events.append(("child", "quiesced"))

            async def shutdown_graceful(self, timeout=30.0):
                del timeout
                self._shutdown.set()
                tasks = [info.bg_task for info in self._active_tasks.values()]
                await asyncio.gather(*tasks, return_exceptions=True)
                await self.release_own_tasks()

        class MemoryEngine:
            pass

        install_exact_drain_runtime_guards(
            PostgreSQLOps,
            WorkerPoller,
            MemoryEngine,
            Adapter(),
            request_worker_shutdown=lambda: (
                events.append(("shutdown", "requested")),
                shutdown_requested.set(),
            ),
        )

        async def exercise():
            poller = WorkerPoller()
            task = type("Task", (), {"operation_id": "operation-1"})()
            holder = type("Holder", (), {"stage": "queued.retain"})()
            execution = asyncio.create_task(
                poller._execute_task_inner(task, holder)
            )
            poller._active_tasks = {
                task.operation_id: SimpleNamespace(bg_task=execution),
            }
            await asyncio.wait_for(shutdown_requested.wait(), timeout=1.0)
            with self.assertRaisesRegex(
                RuntimeError,
                "progress recorder failed",
            ):
                await poller.shutdown_graceful(timeout=0.25)
            self.assertEqual(
                await poller._claim_batch_for_schema_inner(None, {}, 1),
                [],
            )

        asyncio.run(exercise())
        self.assertEqual(
            [event for event in events if event[0] == "shutdown"],
            [("shutdown", "requested")],
        )
        self.assertLess(
            events.index(("shutdown", "requested")),
            events.index(("child", "quiesced")),
        )
        self.assertLess(
            events.index(("child", "quiesced")),
            events.index(("release", "owned")),
        )
        self.assertNotIn(("claim", "upstream"), events)

    def test_escaped_upstream_failure_quiesces_sibling_before_release(self):
        events = []
        shutdown_requested = asyncio.Event()

        class Adapter(_ControlConnectionAdapterMixin):
            def record_upstream_stage(self, _operation_id, stage):
                events.append(("stage", stage))

            async def release_own_tasks(self, _backend):
                events.append(("release", "owned"))
                return 2

        class PostgreSQLOps:
            pass

        class WorkerPoller(_RunCapableWorkerPoller):
            def __init__(self):
                self._backend = "exact-backend"
                self._shutdown = asyncio.Event()
                self._active_tasks = {}

            async def _claim_batch_for_schema_inner(
                self,
                _schema,
                _reserved_limits,
                _shared_limit,
            ):
                return []

            async def _claim_batch_for_schema(self, schema, reserved, shared):
                return await self._claim_batch_for_schema_inner(
                    schema,
                    reserved,
                    shared,
                )

            async def _execute_task_inner(self, task, holder):
                holder.stage = "executor.retain"
                if task.operation_id == "operation-a":
                    raise RuntimeError("exact retry mutation failed")
                try:
                    await asyncio.Event().wait()
                finally:
                    events.append(("child", task.operation_id))

            async def shutdown_graceful(self, timeout=30.0):
                del timeout
                self._shutdown.set()
                await asyncio.sleep(0)
                tasks = [info.bg_task for info in self._active_tasks.values()]
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await self.release_own_tasks()

        class MemoryEngine:
            pass

        install_exact_drain_runtime_guards(
            PostgreSQLOps,
            WorkerPoller,
            MemoryEngine,
            Adapter(),
            request_worker_shutdown=lambda: (
                events.append(("shutdown", "requested")),
                shutdown_requested.set(),
            ),
        )

        async def exercise():
            poller = WorkerPoller()
            holder_a = SimpleNamespace(stage="queued.retain")
            holder_b = SimpleNamespace(stage="queued.retain")
            task_a = SimpleNamespace(operation_id="operation-a")
            task_b = SimpleNamespace(operation_id="operation-b")
            execution_a = asyncio.create_task(
                poller._execute_task_inner(task_a, holder_a)
            )
            execution_b = asyncio.create_task(
                poller._execute_task_inner(task_b, holder_b)
            )
            poller._active_tasks = {
                task_a.operation_id: SimpleNamespace(bg_task=execution_a),
                task_b.operation_id: SimpleNamespace(bg_task=execution_b),
            }
            try:
                await asyncio.wait_for(shutdown_requested.wait(), timeout=0.5)
                with self.assertRaisesRegex(
                    RuntimeError,
                    "exact retry mutation failed",
                ):
                    await poller.shutdown_graceful(timeout=0.25)
            finally:
                for execution in (execution_a, execution_b):
                    if not execution.done():
                        execution.cancel()
                await asyncio.gather(
                    execution_a,
                    execution_b,
                    return_exceptions=True,
                )

        asyncio.run(exercise())
        self.assertEqual(
            [event for event in events if event[0] == "shutdown"],
            [("shutdown", "requested")],
        )
        self.assertLess(
            events.index(("child", "operation-b")),
            events.index(("release", "owned")),
        )

    def test_normal_task_cancellation_does_not_request_worker_shutdown(self):
        shutdown_requests = []

        class Adapter(_ControlConnectionAdapterMixin):
            def record_upstream_stage(self, _operation_id, _stage):
                return None

        class WorkerPoller(_RunCapableWorkerPoller):
            def __init__(self):
                self._shutdown = asyncio.Event()

            async def _claim_batch_for_schema_inner(self, *_arguments):
                return []

            async def _claim_batch_for_schema(self, schema, reserved, shared):
                return await self._claim_batch_for_schema_inner(
                    schema,
                    reserved,
                    shared,
                )

            async def _execute_task_inner(self, _task, _holder):
                await asyncio.Event().wait()

        install_exact_drain_runtime_guards(
            type("PostgreSQLOps", (), {}),
            WorkerPoller,
            type("MemoryEngine", (), {}),
            Adapter(),
            request_worker_shutdown=lambda: shutdown_requests.append(True),
        )

        async def exercise():
            poller = WorkerPoller()
            task = asyncio.create_task(
                poller._execute_task_inner(
                    SimpleNamespace(operation_id="operation-1"),
                    SimpleNamespace(stage="queued.retain"),
                )
            )
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(exercise())
        self.assertEqual(shutdown_requests, [])

    def test_sigterm_runs_the_exact_release_seam(self):
        try:
            import hindsight_api.worker.main  # noqa: F401
            import hindsight_api.worker.poller  # noqa: F401
        except ImportError as error:
            raise unittest.SkipTest(
                "hindsight_api worker runtime is unavailable"
            ) from error
        script = textwrap.dedent(
            f"""
            import asyncio
            import sys

            sys.path.insert(0, {str(LIB)!r})
            from hindsight_memory_control_plane.operation_recovery_runtime import install_exact_drain_runtime_guards
            from hindsight_api.worker.main import _install_shutdown_signal_handlers
            from hindsight_api.worker.poller import WorkerPoller as UpstreamWorkerPoller

            released = []

            class Adapter:
                async def reserve_control_connection(self, _backend):
                    return None

                async def close_control_connection(self):
                    return None

                async def release_own_tasks(self, backend):
                    released.append(backend)
                    return 2

            class PostgreSQLOps:
                pass

            class WorkerPoller(UpstreamWorkerPoller):
                pass

            class MemoryEngine:
                pass

            install_exact_drain_runtime_guards(
                PostgreSQLOps,
                WorkerPoller,
                MemoryEngine,
                Adapter(),
            )

            async def exercise():
                poller = object.__new__(WorkerPoller)
                poller._backend = "exact-backend"
                poller._worker_id = "exact-worker"
                poller._shutdown = asyncio.Event()
                poller._in_flight_lock = asyncio.Lock()
                poller._in_flight_count = 0
                poller._active_tasks = {{}}
                shutdown = asyncio.Event()
                loop = asyncio.get_running_loop()
                if not _install_shutdown_signal_handlers(loop, shutdown.set):
                    raise RuntimeError("signal handlers unavailable")
                print("READY", flush=True)
                await shutdown.wait()
                await poller.shutdown_graceful(timeout=0.01)
                print(f"RELEASED={{released!r}}", flush=True)

            asyncio.run(exercise())
            """
        )
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            ready = []
            reader = threading.Thread(
                target=lambda: ready.append(process.stdout.readline())
            )
            reader.daemon = True
            reader.start()
            reader.join(timeout=10)
            self.assertFalse(reader.is_alive(), "worker never reported READY")
            self.assertEqual(ready[0].strip(), "READY")
            os.kill(process.pid, signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=10)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("RELEASED=['exact-backend']", stdout)

    def test_exact_drain_resume_reconciles_terminal_progress(self):
        adapter = object.__new__(ExactDrainClaimAdapter)
        recorder = Mock()
        operation_id = "00000000-0000-4000-8000-000000000001"
        adapter._plan = {
            "pre_generation": "systalyze:public:123",
            "installation_authority": {
                "postgres_system_identifier": "7659746962107358086",
            },
            "rollback_backup": {
                "source_authority": {
                    "binding": {
                        "database": "hindsight",
                        "user": "hindsight",
                        "data_dir": "/private/tmp",
                        "port": 54329,
                    }
                }
            },
        }
        adapter._selected = {
            operation_id: {
                "operation_type": "retain",
                "task_payload_digest": "a" * 64,
                "row_digest": "b" * 64,
            }
        }
        adapter._preserved = {}
        adapter._resume = True
        adapter._terminal_reconciliation = True
        adapter._worker_digest = "c" * 64
        adapter._started_ids = set()
        adapter._progress_recorder = recorder

        class Connection:
            async def fetchrow(self, _query):
                return {
                    "database": "hindsight",
                    "database_user": "hindsight",
                    "data_directory": "/private/tmp",
                    "port": 54329,
                    "address": None,
                    "system_identifier": "7659746962107358086",
                }

        row = {
            "operation_id": operation_id,
            "operation_type": "retain",
            "task_payload_digest": "a" * 64,
            "worker_id_digest": "c" * 64,
            "status": "completed",
        }
        with (
            patch(
                "hindsight_memory_control_plane.operation_recovery_runtime."
                "read_generation",
                new=AsyncMock(return_value="systalyze:public:124"),
            ),
            patch(
                "hindsight_memory_control_plane.operation_recovery_runtime."
                "read_safe_operation_rows",
                new=AsyncMock(return_value=[row]),
            ),
            patch(
                "hindsight_memory_control_plane.operation_recovery_runtime."
                "live_row_digest",
                return_value="d" * 64,
            ),
        ):
            asyncio.run(adapter._verify_initial_state(Connection()))

        recorder.task_stage.assert_not_called()
        adapter._flush_pending_progress_stages()
        recorder.task_stage.assert_called_once_with(
            operation_id,
            status="completed",
            stage="resume-completed",
        )
        row["status"] = "pending"
        with (
            patch(
                "hindsight_memory_control_plane.operation_recovery_runtime."
                "read_generation",
                new=AsyncMock(return_value="systalyze:public:124"),
            ),
            patch(
                "hindsight_memory_control_plane.operation_recovery_runtime."
                "read_safe_operation_rows",
                new=AsyncMock(return_value=[row]),
            ),
            patch(
                "hindsight_memory_control_plane.operation_recovery_runtime."
                "live_row_digest",
                return_value="d" * 64,
            ),
            self.assertRaisesRegex(
                OperationRecoveryError,
                "terminal reconciliation state differs",
            ),
        ):
            asyncio.run(adapter._verify_initial_state(Connection()))

    def test_terminal_public_claim_binds_prelaunch_generation_and_status(self):
        adapter = object.__new__(ExactDrainClaimAdapter)
        operation_id = "00000000-0000-4000-8000-000000000001"
        adapter._plan = {
            "plan_digest": "1" * 64,
            "pre_generation": "systalyze:public:123",
            "installation_authority": {
                "postgres_system_identifier": "7659746962107358086",
            },
            "rollback_backup": {
                "source_authority": {
                    "binding": {
                        "database": "hindsight",
                        "user": "hindsight",
                        "data_dir": "/private/tmp",
                        "port": 54329,
                    }
                }
            },
        }
        adapter._selected = {
            operation_id: {
                "operation_type": "retain",
                "task_payload_digest": "a" * 64,
                "row_digest": "b" * 64,
            }
        }
        adapter._preserved = {}
        adapter._identifiers = []
        adapter._resume = True
        adapter._terminal_reconciliation = True
        adapter._worker_id = "exact-worker"
        adapter._worker_digest = "c" * 64
        adapter._started_ids = set()
        adapter._progress_recorder = None
        adapter._pending_progress_stages = {}
        adapter._transaction_timeout_seconds = None
        adapter._execution_deadline = None
        adapter._initial_guard_complete = False
        adapter._terminal_reconciliation_ready = False
        adapter._completion_signalled = False
        adapter._completion_callback = None
        status_body = {
            "schema_version": 1,
            "kind": "operation-recovery-exact-drain-status",
            "plan_digest": "1" * 64,
            "generation_before": "systalyze:public:123",
            "generation_after": "systalyze:public:123",
            "selected_operation_count": 1,
            "selected_status_counts": {"completed": 1},
            "preserved_status_counts": {},
            "outside_nonterminal_counts": [],
            "observed_at": 1_000,
        }
        adapter._terminal_status_evidence = {
            "generation": "systalyze:public:123",
            "observed_at": 1_000,
            "status_digest": recovery_fixtures.digest(status_body),
        }

        class Connection:
            async def execute(self, *_arguments):
                return "SET"

            async def fetchrow(self, _query):
                return {
                    "database": "hindsight",
                    "database_user": "hindsight",
                    "data_directory": "/private/tmp",
                    "port": 54329,
                    "address": None,
                    "system_identifier": "7659746962107358086",
                }

            async def fetch(self, *_arguments):
                return []

        row = {
            "operation_id": operation_id,
            "operation_type": "retain",
            "task_payload_digest": "a" * 64,
            "worker_id_digest": "c" * 64,
            "status": "completed",
        }

        async def claim(generation):
            with (
                patch(
                    "hindsight_memory_control_plane.operation_recovery_runtime."
                    "read_generation",
                    new=AsyncMock(return_value=generation),
                ),
                patch(
                    "hindsight_memory_control_plane.operation_recovery_runtime."
                    "read_safe_operation_rows",
                    new=AsyncMock(return_value=[row]),
                ),
                patch(
                    "hindsight_memory_control_plane.operation_recovery_runtime."
                    "live_row_digest",
                    return_value="d" * 64,
                ),
            ):
                return await adapter.claim_tasks(
                    Connection(),
                    "public.async_operations",
                    "exact-worker",
                    {},
                    1,
                )

        with self.assertRaisesRegex(
            OperationRecoveryError,
            "terminal generation evidence differs",
        ):
            asyncio.run(claim("systalyze:public:124"))

        row["status"] = "failed"
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "terminal status evidence differs",
        ):
            asyncio.run(claim("systalyze:public:123"))

    def test_terminal_reconciliation_rechecks_before_every_no_work_claim(self):
        adapter = object.__new__(ExactDrainClaimAdapter)
        adapter._worker_id = "exact-worker"
        adapter._terminal_reconciliation = True
        adapter._terminal_reconciliation_ready = False
        adapter._initial_guard_complete = True
        adapter._verify_initial_state = AsyncMock(
            side_effect=OperationRecoveryError(
                "operation-recovery terminal reconciliation state differs"
            )
        )
        connection = SimpleNamespace(
            execute=AsyncMock(return_value="SET"),
            fetch=AsyncMock(),
        )

        with self.assertRaisesRegex(
            OperationRecoveryError,
            "terminal reconciliation state differs",
        ):
            asyncio.run(
                adapter.claim_tasks(
                    connection,
                    '"public".async_operations',
                    "exact-worker",
                    {},
                    1,
                )
            )

        adapter._verify_initial_state.assert_awaited_once_with(connection)
        connection.fetch.assert_not_awaited()
        self.assertFalse(adapter._terminal_reconciliation_ready)

    def test_expired_terminal_reconciliation_claims_no_tasks_or_provider_work(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        planned_at = int(time.time())
        plan = fixtures.drain_plan(
            snapshot=fixtures.drain_snapshot(observed_at=planned_at),
            created_at=planned_at,
        )
        authorization = recovery_fixtures.exact_drain_authorization(
            plan,
            authorized_at=planned_at,
        )
        completed = []
        adapter = ExactDrainClaimAdapter(
            plan,
            authorization=authorization,
            resume=True,
            terminal_reconciliation=True,
            terminal_status_evidence={
                "generation": plan["pre_generation"],
                "observed_at": planned_at,
                "status_digest": "6" * 64,
            },
            completion_callback=lambda: completed.append(True),
            clock=lambda: planned_at
            + plan["execution_window"]["calculated_seconds"]
            + 1,
        )
        adapter._verify_initial_state = AsyncMock()

        class Connection:
            def __init__(self):
                self.statements = []
                self.timeouts = []

            async def execute(self, statement, *_arguments):
                self.statements.append(statement)
                return "SET"

            async def fetchval(self, statement, value):
                self.timeouts.append((statement, value))

            async def fetch(self, *_arguments, **_keywords):
                raise AssertionError("terminal reconciliation selected claim rows")

        connection = Connection()
        tasks = asyncio.run(
            adapter.claim_tasks(
                connection,
                '"public".async_operations',
                adapter._worker_id,
                {},
                1,
            )
        )
        adapter.claim_committed(tasks)

        self.assertEqual(tasks, [])
        self.assertEqual(
            connection.statements,
            ["SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"],
        )
        self.assertEqual(len(connection.timeouts), 3)
        adapter._verify_initial_state.assert_awaited_once_with(connection)
        self.assertEqual(completed, [True])

    def test_expired_terminal_reconciliation_recovery_is_read_only(self):
        fixtures = recovery_fixtures.OperationRecoveryContractTest()
        planned_at = int(time.time())
        plan = fixtures.drain_plan(
            snapshot=fixtures.drain_snapshot(observed_at=planned_at),
            created_at=planned_at,
        )
        authorization = recovery_fixtures.exact_drain_authorization(
            plan,
            authorized_at=planned_at,
        )
        adapter = ExactDrainClaimAdapter(
            plan,
            authorization=authorization,
            resume=True,
            terminal_reconciliation=True,
            terminal_status_evidence={
                "generation": plan["pre_generation"],
                "observed_at": planned_at,
                "status_digest": "6" * 64,
            },
            clock=lambda: planned_at
            + plan["execution_window"]["calculated_seconds"]
            + 1,
        )
        adapter._verify_initial_state = AsyncMock()

        statements = []

        class Connection:
            @asynccontextmanager
            async def transaction(self):
                yield

            async def fetchval(self, *_arguments):
                return None

            async def fetch(self, *_arguments, **_keywords):
                raise AssertionError("terminal reconciliation recovered rows")

            async def execute(self, statement, *_arguments, **_keywords):
                statements.append(statement)
                if statement != "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE":
                    raise AssertionError("terminal reconciliation mutated rows")
                return "SET"

        class Backend:
            @asynccontextmanager
            async def acquire(self):
                yield Connection()

        recovered = asyncio.run(adapter.recover_own_tasks(Backend()))

        self.assertEqual(recovered, 0)
        adapter._verify_initial_state.assert_awaited_once()
        self.assertEqual(
            statements,
            ["SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"],
        )

    def test_terminal_reconciliation_rejects_public_task_mutations(self):
        adapter = object.__new__(ExactDrainClaimAdapter)
        adapter._terminal_reconciliation = True
        adapter._execution_deadline = 86_500
        adapter._clock = lambda: 100

        class Backend:
            def acquire(self):
                raise AssertionError("terminal reconciliation acquired PostgreSQL")

        operation_id = "00000000-0000-4000-8000-000000000001"
        invocations = {
            "retry": lambda: adapter.schedule_retry(
                Backend(), operation_id, object(), "provider failure", "public"
            ),
            "defer": lambda: adapter.defer_operation(
                Backend(), operation_id, object(), "capacity", "public"
            ),
            "complete": lambda: adapter.mark_completed(
                Backend(), operation_id, "public"
            ),
            "fail": lambda: adapter.mark_failed(
                Backend(), operation_id, "provider failure", "public"
            ),
        }
        for name, invoke in invocations.items():
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(
                    OperationRecoveryError,
                    "terminal reconciliation cannot mutate",
                ),
            ):
                asyncio.run(invoke())

    def test_exact_drain_public_mutation_seams_configure_timeouts_before_reads(self):
        operation_id = "00000000-0000-4000-8000-000000000001"

        class Configured(RuntimeError):
            pass

        statements = []

        class Connection:
            @asynccontextmanager
            async def transaction(self):
                yield

            async def execute(self, statement, *_arguments, **_keywords):
                statements.append(statement)
                return "SET"

        class Backend:
            @asynccontextmanager
            async def acquire(self):
                yield Connection()

        invocations = {
            "recover": lambda adapter: adapter.recover_own_tasks(Backend()),
            "release": lambda adapter: adapter.release_own_tasks(Backend()),
            "retry": lambda adapter: adapter.schedule_retry(
                Backend(), operation_id, object(), "failure", "public"
            ),
            "defer": lambda adapter: adapter.defer_operation(
                Backend(), operation_id, object(), "capacity", "public"
            ),
            "complete": lambda adapter: adapter.mark_completed(
                Backend(), operation_id, "public"
            ),
            "fail": lambda adapter: adapter.mark_failed(
                Backend(), operation_id, "failure", "public"
            ),
        }
        for name, invoke in invocations.items():
            with self.subTest(name=name):
                adapter = object.__new__(ExactDrainClaimAdapter)
                adapter._resume = True
                adapter._terminal_reconciliation = False
                adapter._execution_deadline = None
                adapter._selected = {
                    operation_id: {
                        "operation_type": "retain",
                        "task_payload_digest": "a" * 64,
                    }
                }
                adapter._configure_mutation_transaction = AsyncMock(
                    side_effect=Configured
                )
                self._initialize_unreserved_control_lifecycle(adapter)

                with self.assertRaises(Configured):
                    asyncio.run(invoke(adapter))

                adapter._configure_mutation_transaction.assert_awaited_once()

        self.assertEqual(
            statements,
            [
                "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"
                for _name in invocations
            ],
        )

    def test_exact_drain_release_uses_one_post_lease_cleanup_deadline(self):
        now = [100]
        configured = []
        adapter = object.__new__(ExactDrainClaimAdapter)
        adapter._terminal_reconciliation = False
        adapter._execution_deadline = 100
        adapter._transaction_timeout_seconds = 120
        adapter._cleanup_deadline = None
        adapter._clock = lambda: now[0]
        adapter._worker_id = "exact-worker"
        adapter._selected = {}
        adapter._started_ids = set()
        adapter._initial_guard_complete = True
        adapter._verify_unstarted_state = AsyncMock()
        self._initialize_unreserved_control_lifecycle(adapter)

        class Connection:
            @asynccontextmanager
            async def transaction(self, **_arguments):
                yield

            async def fetchval(self, query, value):
                configured.append((query, value))

            async def fetch(self, *_args, **_kwargs):
                return []

            async def execute(self, *_args, **_kwargs):
                return "UPDATE 0"

        class Backend:
            @asynccontextmanager
            async def acquire(self):
                yield Connection()

        self.assertEqual(asyncio.run(adapter.release_own_tasks(Backend())), 0)
        now[0] = 150
        self.assertEqual(asyncio.run(adapter.release_own_tasks(Backend())), 0)

        self.assertEqual(configured[0][1], "120000ms")
        self.assertEqual(configured[3][1], "70000ms")
        self.assertEqual(adapter._cleanup_deadline, 220)

    def test_exact_drain_interpreter_evidence_resolves_version_alias_parent(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory).resolve(strict=True)
            canonical = root / "cpython-3.13.14" / "bin"
            canonical.mkdir(parents=True)
            interpreter = canonical / "python3.13"
            interpreter.write_bytes(b"trusted-interpreter")
            interpreter.chmod(0o500)
            alias = root / "cpython-3.13"
            alias.symlink_to(canonical.parent, target_is_directory=True)

            venv = root / "venv"
            (venv / "bin").mkdir(parents=True)
            (venv / "pyvenv.cfg").write_text("home = test\n", encoding="utf-8")
            (venv / "bin" / "python").symlink_to(
                alias / "bin" / "python3.13"
            )
            (venv / "bin" / "python3").symlink_to("python")
            worker = venv / "bin" / "hindsight-worker"
            worker.write_text(
                f"#!{venv / 'bin' / 'python3'}\n",
                encoding="utf-8",
            )
            worker.chmod(0o500)

            evidence = _exact_drain_interpreter_evidence(
                exact_drain_worker_interpreter_path(worker)
            )

        self.assertEqual(evidence["resolved_path"], str(interpreter))
        self.assertEqual(
            evidence["resolved_parent_alias_path"],
            str(alias / "bin" / "python3.13"),
        )
        self.assertEqual(
            evidence["resolved_sha256"],
            hashlib.sha256(b"trusted-interpreter").hexdigest(),
        )

    def test_exact_drain_resume_allows_only_a_bound_expired_plan(self):
        verified = {
            "plan_digest": "a" * 64,
            "selected_operations": [],
            "live_snapshot": {"operations": []},
            "worker_max_retries": 3,
        }
        with patch(
            "hindsight_memory_control_plane.operation_recovery_runtime."
            "verify_exact_drain_plan",
            return_value=verified,
        ) as verify:
            ExactDrainClaimAdapter({}, resume=True)
        verify.assert_called_once_with({}, allow_expired=True)

    def test_exact_drain_resume_keeps_the_original_authorization_deadline(self):
        verified = {
            "schema_version": 2,
            "plan_digest": "a" * 64,
            "selected_operations": [],
            "live_snapshot": {"operations": []},
            "worker_max_retries": 3,
            "execution_lease_seconds": 86_400,
            "transaction_timeout_seconds": 120,
        }
        authorization = {"authorized_at": 100}
        with (
            patch(
                "hindsight_memory_control_plane.operation_recovery_runtime."
                "verify_exact_drain_plan",
                return_value=verified,
            ),
            patch(
                "hindsight_memory_control_plane.operation_recovery_runtime."
                "verify_exact_drain_authorization_receipt",
                return_value=authorization,
            ),
        ):
            started = ExactDrainClaimAdapter(
                {},
                authorization=authorization,
                clock=lambda: 200,
            )
            resumed = ExactDrainClaimAdapter(
                {},
                authorization=authorization,
                clock=lambda: 300,
                resume=True,
            )

        self.assertEqual(started._execution_deadline, 86_500)
        self.assertEqual(resumed._execution_deadline, 86_500)

    def test_exact_drain_initial_guard_rejects_the_wrong_database_identity(self):
        adapter = object.__new__(ExactDrainClaimAdapter)
        adapter._plan = {
            "pre_generation": "systalyze:public:123",
            "installation_authority": {
                "postgres_system_identifier": "7659746962107358086",
            },
            "rollback_backup": {
                "source_authority": {
                    "binding": {
                        "database": "hindsight",
                        "user": "hindsight",
                        "data_dir": "/private/tmp/expected-pg-data",
                        "port": 54329,
                    }
                }
            },
        }
        adapter._selected = {}

        class WrongDatabase:
            async def fetchrow(self, _query):
                return {
                    "database": "hindsight",
                    "database_user": "hindsight",
                    "data_directory": "/private/tmp/other-pg-data",
                    "port": 54329,
                    "address": None,
                    "system_identifier": "7659746962107358086",
                }

        with (
            patch(
                "hindsight_memory_control_plane.operation_recovery_runtime.read_generation",
                new=AsyncMock(return_value="systalyze:public:123"),
            ),
            patch(
                "hindsight_memory_control_plane.operation_recovery_runtime.read_safe_operation_rows",
                new=AsyncMock(return_value=[]),
            ),
            self.assertRaisesRegex(
                OperationRecoveryError,
                "database identity differs",
            ),
        ):
            asyncio.run(adapter._verify_initial_state(WrongDatabase()))

    def test_exact_drain_guards_disable_global_startup_and_parent_mutations(self):
        events = []

        class Adapter(_ControlConnectionAdapterMixin):
            async def claim_tasks(self, *arguments, **keywords):
                events.append(("claim", arguments, keywords))
                return ["selected"]

            async def recover_own_tasks(self, backend):
                events.append(("exact-recovery", backend))
                return 0

            async def release_own_tasks(self, backend):
                events.append(("exact-release", backend))
                return 2

            async def schedule_retry(
                self, backend, operation_id, retry_at, error_message, schema
            ):
                events.append(
                    (
                        "exact-retry",
                        backend,
                        operation_id,
                        retry_at,
                        error_message,
                        schema,
                    )
                )

            async def defer_operation(
                self, backend, operation_id, exec_date, reason, schema
            ):
                events.append(
                    (
                        "exact-defer",
                        backend,
                        operation_id,
                        exec_date,
                        reason,
                        schema,
                    )
                )

            async def mark_completed(self, backend, operation_id, schema):
                events.append(
                    ("exact-complete", backend, operation_id, schema)
                )

            async def mark_failed(
                self, backend, operation_id, error_message, schema
            ):
                events.append(
                    (
                        "exact-fail",
                        backend,
                        operation_id,
                        error_message,
                        schema,
                    )
                )

        class PostgreSQLOps:
            async def claim_tasks(self, *arguments, **keywords):
                raise AssertionError("upstream claim seam remained active")

        class WorkerPoller(_RunCapableWorkerPoller):
            _backend = "exact-backend"

            async def _execute_task_inner(self, _task, _holder):
                return None

            async def _claim_batch_for_schema_inner(
                self,
                _schema,
                _reserved_limits,
                _shared_limit,
            ):
                return []

            async def _claim_batch_for_schema(self, schema, reserved, shared):
                return await self._claim_batch_for_schema_inner(
                    schema, reserved, shared
                )

            async def _scan_active_schemas(self, schemas):
                events.append(("upstream-scan", schemas))
                return set(schemas)

            async def recover_own_tasks(self):
                events.append(("global-recovery",))
                return 99

            async def release_own_tasks(self):
                events.append(("global-release",))
                return 99

            async def _maybe_update_parent_operation(self, *arguments):
                events.append(("poller-parent", arguments))

        class MemoryEngine:
            async def _get_backend(self):
                return "exact-backend"

            async def _maybe_update_parent_operation(self, *arguments):
                events.append(("engine-parent", arguments))

        install_exact_drain_runtime_guards(
            PostgreSQLOps,
            WorkerPoller,
            MemoryEngine,
            Adapter(),
        )

        async def exercise():
            claimed = await PostgreSQLOps().claim_tasks(
                "connection",
                "public.async_operations",
                "worker",
                {},
                1,
            )
            active = await WorkerPoller()._scan_active_schemas(
                ["tenant-a", None, "tenant-b"]
            )
            recovered = await WorkerPoller().recover_own_tasks()
            released = await WorkerPoller().release_own_tasks()
            retried = await WorkerPoller()._schedule_retry(
                "operation", "retry-at", "safe-error", None
            )
            deferred = await WorkerPoller()._defer_operation(
                "operation", "exec-date", "safe-reason", "public"
            )
            completed = await WorkerPoller()._mark_completed(
                "operation", None
            )
            failed = await WorkerPoller()._mark_failed(
                "operation", "safe-error", "public"
            )
            engine_completed = await MemoryEngine()._mark_operation_completed(
                "operation"
            )
            engine_failed = await MemoryEngine()._mark_operation_failed(
                "operation", "safe-error", "safe-traceback"
            )
            consolidation_completed = await MemoryEngine()._mark_operation_completed_and_fire_webhook(
                "operation",
                "engineering",
                "completed",
                {"observations_created": 1},
                "public",
            )
            consolidation_failed = await MemoryEngine()._mark_operation_completed_and_fire_webhook(
                "operation",
                "engineering",
                "failed",
                None,
                "public",
                "consolidation failure",
            )
            poller_parent = await WorkerPoller()._maybe_update_parent_operation(
                "child",
                None,
                "connection",
            )
            engine_parent = await MemoryEngine()._maybe_update_parent_operation(
                "child",
                "connection",
            )
            return (
                claimed,
                active,
                recovered,
                released,
                retried,
                deferred,
                completed,
                failed,
                engine_completed,
                engine_failed,
                consolidation_completed,
                consolidation_failed,
                poller_parent,
                engine_parent,
            )

        (
            claimed,
            active,
            recovered,
            released,
            retried,
            deferred,
            completed,
            failed,
            engine_completed,
            engine_failed,
            consolidation_completed,
            consolidation_failed,
            poller_parent,
            engine_parent,
        ) = asyncio.run(exercise())
        self.assertEqual(claimed, ["selected"])
        self.assertEqual(active, {None})
        self.assertEqual(recovered, 0)
        self.assertEqual(released, 2)
        self.assertIn(("exact-release", "exact-backend"), events)
        self.assertNotIn(("global-release",), events)
        self.assertIsNone(retried)
        self.assertIsNone(deferred)
        self.assertIsNone(completed)
        self.assertIsNone(failed)
        self.assertIsNone(engine_completed)
        self.assertIsNone(engine_failed)
        self.assertIsNone(consolidation_completed)
        self.assertIsNone(consolidation_failed)
        self.assertIn(
            (
                "exact-retry",
                "exact-backend",
                "operation",
                "retry-at",
                "safe-error",
                None,
            ),
            events,
        )
        self.assertIn(
            ("exact-complete", "exact-backend", "operation", None),
            events,
        )
        self.assertIn(
            (
                "exact-fail",
                "exact-backend",
                "operation",
                "safe-error",
                "public",
            ),
            events,
        )
        self.assertIn(
            ("exact-complete", "exact-backend", "operation", None),
            events,
        )
        self.assertIn(
            (
                "exact-fail",
                "exact-backend",
                "operation",
                "safe-error\n\nTraceback:\nsafe-traceback",
                None,
            ),
            events,
        )
        self.assertIn(
            ("exact-complete", "exact-backend", "operation", "public"),
            events,
        )
        self.assertIn(
            (
                "exact-fail",
                "exact-backend",
                "operation",
                "consolidation failure",
                "public",
            ),
            events,
        )
        self.assertIn(
            (
                "exact-defer",
                "exact-backend",
                "operation",
                "exec-date",
                "safe-reason",
                "public",
            ),
            events,
        )
        self.assertIsNone(poller_parent)
        self.assertIsNone(engine_parent)
        self.assertEqual(
            [event[0] for event in events],
            [
                "claim",
                "exact-recovery",
                "exact-release",
                "exact-retry",
                "exact-defer",
                "exact-complete",
                "exact-fail",
                "exact-complete",
                "exact-fail",
                "exact-complete",
                "exact-fail",
            ],
        )

    def test_live_identity_accepts_verified_unix_socket_connection(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary).resolve()

            class UnixConnection:
                async def fetchrow(self, query):
                    if (
                        "current_setting('port')::integer AS port"
                        not in query
                        or "inet_server_port()" in query
                    ):
                        raise AssertionError(
                            "live identity must read the configured server port"
                        )
                    return {
                        "database": "hindsight",
                        "database_user": "hindsight",
                        "data_directory": str(data_dir),
                        "port": 5432,
                        "address": None,
                        "system_identifier": "7659746962107358086",
                    }

            asyncio.run(
                assert_connected_live_database(
                    UnixConnection(),
                    {
                        "database": "hindsight",
                        "user": "hindsight",
                        "data_dir": str(data_dir),
                        "port": 5432,
                    },
                    expected_system_identifier="7659746962107358086",
                )
            )

    def test_live_identity_rejects_tcp_connection(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary).resolve()

            class TcpConnection:
                async def fetchrow(self, query):
                    return {
                        "database": "hindsight",
                        "database_user": "hindsight",
                        "data_directory": str(data_dir),
                        "port": 5432,
                        "address": "127.0.0.1",
                        "system_identifier": "7659746962107358086",
                    }

            with self.assertRaisesRegex(
                OperationRecoveryError,
                "does not match the pinned live pg0 identity",
            ):
                asyncio.run(
                    assert_connected_live_database(
                        TcpConnection(),
                        {
                            "database": "hindsight",
                            "user": "hindsight",
                            "data_dir": str(data_dir),
                            "port": 5432,
                        },
                        expected_system_identifier="7659746962107358086",
                    )
                )

    def test_snapshot_is_repeatable_read_readonly_and_payload_free(self):
        connection = FakeConnection()
        before, after, rows = asyncio.run(
            read_snapshot(
                connection,
                profile_id="systalyze",
                schema="public",
                bank_id="engineering",
                operation_ids=[
                    "00000000-0000-4000-8000-000000000001"
                ],
            )
        )

        self.assertEqual(before, "systalyze:public:123")
        self.assertEqual(after, before)
        self.assertEqual(
            connection.transaction_arguments,
            {"isolation": "repeatable_read", "readonly": True},
        )
        self.assertEqual(connection.generation_reads, 2)
        self.assertEqual(len(rows), 1)
        self.assertNotIn("task_payload", rows[0])
        self.assertNotIn("error_message", rows[0])
        query, arguments = connection.fetch_calls[0]
        self.assertEqual(arguments[0], "engineering")
        self.assertIn("sha256(convert_to(task_payload::text", query)
        self.assertIn("sha256(convert_to(error_message", query)

    def test_safe_query_never_projects_raw_payload_or_error(self):
        select_list = SAFE_OPERATION_QUERY.split("FROM {schema}.async_operations")[0]
        self.assertNotIn("task_payload AS", select_list)
        self.assertNotIn("error_message AS", select_list)
        self.assertNotIn("worker_id AS", select_list)
        self.assertIn("task_payload_digest", select_list)
        self.assertIn("worker_id_digest", select_list)
        self.assertIn("error_digest", select_list)

    def test_global_queue_blockers_share_the_apply_guard_and_are_payload_free(self):
        class BlockerConnection(FakeConnection):
            async def fetch(self, query, *arguments):
                self.fetch_calls.append((query, arguments))
                return [
                    {
                        "operation_id": (
                            "00000000-0000-4000-8000-000000000099"
                        ),
                        "bank_id": "another-bank",
                        "operation_type": "another-operation",
                        "status": "pending",
                        "created_at": "2026-07-29T12:00:00.000000Z",
                        "updated_at": "2026-07-29T13:00:00.000000Z",
                        "completed_at": None,
                        "retry_count": 1,
                        "next_retry_at": None,
                        "worker_id_present": True,
                        "worker_id_digest": "6" * 64,
                        "claimed_at": "2026-07-29T12:30:00.000000Z",
                        "task_payload_present": True,
                        "task_payload_digest": "7" * 64,
                        "in_reference_cohort": False,
                        "in_reference_selected_set": False,
                        "blocker_reason": "claimed_pending",
                    }
                ]

        connection = BlockerConnection()
        before, after, rows = asyncio.run(
            read_global_queue_blockers(
                connection,
                profile_id="systalyze",
                schema="public",
                reference_cohort_operation_ids=[
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000002",
                ],
                reference_selected_operation_ids=[
                    "00000000-0000-4000-8000-000000000002"
                ],
            )
        )

        self.assertEqual((before, after), (
            "systalyze:public:123",
            "systalyze:public:123",
        ))
        self.assertEqual(
            connection.transaction_arguments,
            {"isolation": "repeatable_read", "readonly": True},
        )
        self.assertEqual(rows[0]["bank_id"], "another-bank")
        self.assertEqual(rows[0]["operation_type"], "another-operation")
        query, arguments = connection.fetch_calls[0]
        self.assertEqual(
            [[str(value) for value in group] for group in arguments],
            [
                ["00000000-0000-4000-8000-000000000002"],
                [
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000002",
                ],
            ],
        )
        self.assertEqual(connection.generation_reads, 2)
        self.assertIn(QUEUE_BLOCKER_PREDICATE, query)
        self.assertIn(QUEUE_BLOCKER_PREDICATE, GLOBAL_QUEUE_BLOCKER_QUERY)
        self.assertEqual(QUEUE_BLOCKER_GUARD_CONTRACT_VERSION, 1)
        self.assertEqual(len(QUEUE_BLOCKER_GUARD_CONTRACT_DIGEST), 64)
        select_list = GLOBAL_QUEUE_BLOCKER_QUERY.split(
            "FROM {schema}.async_operations"
        )[0]
        self.assertNotIn("task_payload AS", select_list)
        self.assertNotIn("error_message", select_list)
        self.assertNotIn("worker_id AS", select_list)
        self.assertIn("task_payload_digest", select_list)
        self.assertIn("worker_id_digest", select_list)

    def test_global_queue_blocker_generation_reads_bracket_the_snapshot(self):
        class BracketConnection(FakeConnection):
            def __init__(self):
                super().__init__()
                self.in_transaction = False
                self.events = []

            @asynccontextmanager
            async def transaction(self, **arguments):
                self.transaction_arguments = arguments
                self.in_transaction = True
                self.events.append("transaction_enter")
                try:
                    yield
                finally:
                    self.events.append("transaction_exit")
                    self.in_transaction = False

            async def fetchrow(self, query, *arguments):
                self.assert_generation_outside_transaction()
                self.events.append("generation")
                return await super().fetchrow(query, *arguments)

            async def fetch(self, query, *arguments):
                if not self.in_transaction:
                    raise AssertionError("blocker query must use a snapshot")
                self.events.append("blockers")
                return await super().fetch(query, *arguments)

            def assert_generation_outside_transaction(self):
                if self.in_transaction:
                    raise AssertionError(
                        "generation reads must bracket the snapshot"
                    )

        connection = BracketConnection()
        asyncio.run(
            read_global_queue_blockers(
                connection,
                profile_id="systalyze",
                schema="public",
                reference_cohort_operation_ids=[
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000002",
                ],
                reference_selected_operation_ids=[
                    "00000000-0000-4000-8000-000000000002"
                ],
            )
        )

        self.assertEqual(
            connection.events,
            [
                "generation",
                "transaction_enter",
                "blockers",
                "transaction_exit",
                "generation",
            ],
        )

    def test_global_queue_blocker_rejects_invalid_reference_sets(self):
        invalid_sets = (
            (
                [
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000001",
                ],
                ["00000000-0000-4000-8000-000000000001"],
                "contains duplicates",
            ),
            (
                ["00000000-0000-4000-8000-000000000001"],
                [
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000001",
                ],
                "contains duplicates",
            ),
            (
                ["00000000-0000-4000-8000-000000000001"],
                [],
                "is empty",
            ),
            (
                ["00000000-0000-4000-8000-000000000001"],
                ["00000000-0000-4000-8000-000000000002"],
                "not a cohort subset",
            ),
        )
        for cohort_ids, selected_ids, message in invalid_sets:
            with self.subTest(message=message), self.assertRaisesRegex(
                OperationRecoveryError,
                message,
            ):
                asyncio.run(
                    read_global_queue_blockers(
                        FakeConnection(),
                        profile_id="systalyze",
                        schema="public",
                        reference_cohort_operation_ids=cohort_ids,
                        reference_selected_operation_ids=selected_ids,
                    )
                )

    def test_claim_release_evidence_is_generation_bound_and_payload_free(self):
        operation_id = "00000000-0000-4000-8000-000000000099"

        class ClaimEvidenceConnection(FakeConnection):
            async def fetch(self, query, *arguments):
                self.fetch_calls.append((query, arguments))
                return [
                    {
                        "operation_id": operation_id,
                        "bank_id": "codex",
                        "operation_type": "retain",
                        "status": "failed",
                        "created_at": "2026-07-29T12:00:00.000000Z",
                        "updated_at": "2026-07-29T13:00:00.000000Z",
                        "completed_at": "2026-07-29T13:00:00.000000Z",
                        "retry_count": 2,
                        "next_retry_at": None,
                        "worker_id_present": True,
                        "worker_id_digest": "6" * 64,
                        "claimed_at": "2026-07-29T12:30:00.000000Z",
                        "task_payload_present": True,
                        "task_payload_digest": "7" * 64,
                        "in_reference_cohort": False,
                        "in_reference_selected_set": False,
                        "blocker_reason": "claimed_failed",
                        "nonclaim_state_digest": "8" * 64,
                    }
                ]

        connection = ClaimEvidenceConnection()
        cohort_only_id = "00000000-0000-4000-8000-000000000002"
        before, after, rows = asyncio.run(
            read_claim_release_evidence(
                connection,
                profile_id="systalyze",
                schema="public",
                operation_ids=[operation_id],
                reference_cohort_operation_ids=[operation_id, cohort_only_id],
                reference_selected_operation_ids=[operation_id],
                expected_generation="systalyze:public:123",
            )
        )

        self.assertEqual((before, after), (
            "systalyze:public:123",
            "systalyze:public:123",
        ))
        self.assertEqual(
            connection.transaction_arguments,
            {"isolation": "repeatable_read", "readonly": True},
        )
        self.assertEqual(rows[0]["nonclaim_state_digest"], "8" * 64)
        self.assertEqual(connection.generation_reads, 2)
        _query, arguments = connection.fetch_calls[0]
        self.assertEqual([str(value) for value in arguments[0]], [operation_id])
        self.assertEqual(
            [str(value) for value in arguments[1]],
            [operation_id, cohort_only_id],
        )
        self.assertEqual([str(value) for value in arguments[2]], [operation_id])
        select_list = CLAIM_RELEASE_EVIDENCE_QUERY.split(
            "FROM {schema}.async_operations"
        )[0]
        self.assertNotIn("task_payload AS", select_list)
        self.assertNotIn("error_message AS", select_list)
        self.assertNotIn("worker_id AS", select_list)
        self.assertIn("nonclaim_state_digest", select_list)

    def test_claim_release_evidence_rejects_generation_or_row_set_drift(self):
        operation_id = "00000000-0000-4000-8000-000000000001"

        class GenerationDriftConnection(FakeConnection):
            async def fetchrow(self, query, *arguments):
                self.generation_reads += 1
                return {
                    "generation": 122 + self.generation_reads,
                    "missing_trigger_count": 0,
                    "reserved_guard_count": 0,
                }

        with self.assertRaisesRegex(
            OperationRecoveryError,
            "generation changed during claim-release planning",
        ):
            asyncio.run(
                read_claim_release_evidence(
                    GenerationDriftConnection(),
                    profile_id="systalyze",
                    schema="public",
                    operation_ids=[operation_id],
                    reference_cohort_operation_ids=[],
                    reference_selected_operation_ids=[],
                    expected_generation="systalyze:public:123",
                )
            )

        with self.assertRaisesRegex(
            OperationRecoveryError,
            "claim-release row set changed",
        ):
            asyncio.run(
                read_claim_release_evidence(
                    FakeConnection(),
                    profile_id="systalyze",
                    schema="public",
                    operation_ids=[
                        "00000000-0000-4000-8000-000000000002"
                    ],
                    reference_cohort_operation_ids=[],
                    reference_selected_operation_ids=[],
                    expected_generation="systalyze:public:123",
                )
            )

        invalid_selected_sets = (
            ([operation_id], [operation_id, operation_id]),
            ([], [operation_id]),
            (
                [operation_id, "00000000-0000-4000-8000-000000000098"],
                ["00000000-0000-4000-8000-000000000098"],
            ),
        )
        for cohort_ids, selected_ids in invalid_selected_sets:
            with self.subTest(
                cohort_ids=cohort_ids,
                selected_ids=selected_ids,
            ), self.assertRaisesRegex(
                OperationRecoveryError,
                "operation ID set is invalid",
            ):
                asyncio.run(
                    read_claim_release_evidence(
                        FakeConnection(),
                        profile_id="systalyze",
                        schema="public",
                        operation_ids=[operation_id],
                        reference_cohort_operation_ids=cohort_ids,
                        reference_selected_operation_ids=selected_ids,
                        expected_generation="systalyze:public:123",
                    )
                )

    def test_apply_allows_bound_claim_on_selected_terminal_row(self):
        before = {
            "operation_id": "00000000-0000-4000-8000-000000000001",
            "bank_id": "engineering",
            "operation_type": "retain",
            "status": "failed",
            "created_at": "2026-07-29T12:00:00.000000Z",
            "updated_at": "2026-07-29T13:00:00.000000Z",
            "completed_at": "2026-07-29T13:00:00.000000Z",
            "retry_count": 2,
            "next_retry_at": None,
            "worker_id_present": True,
            "worker_id_digest": hashlib.sha256(
                b"orphaned-worker"
            ).hexdigest(),
            "claimed_at": "2026-07-29T12:30:00.000000Z",
            "task_payload_present": True,
            "task_payload_digest": "a" * 64,
            "result_metadata_digest": "b" * 64,
            "error_category": "provider_capacity",
            "error_digest": "c" * 64,
        }
        after = {
            **before,
            "status": "pending",
            "updated_at": "2026-07-30T16:00:00.000000Z",
            "completed_at": None,
            "retry_count": 0,
            "worker_id_present": False,
            "worker_id_digest": None,
            "claimed_at": None,
            "error_category": "none",
            "error_digest": None,
        }

        class ApplyConnection(FakeConnection):
            def __init__(self):
                super().__init__()
                self.fetchval_results = [123, 0, 124]
                self.fetch_results = [[before], [after]]
                self.execute_call = None
                self.deadline_settings = []

            async def fetchval(self, query, *arguments):
                if "set_config" in query:
                    self.deadline_settings.append((query, arguments))
                    return arguments[-1]
                if "SELECT EXISTS" in query:
                    self.assert_selected_ids = arguments
                    return False
                return self.fetchval_results.pop(0)

            async def fetch(self, query, *arguments):
                self.fetch_calls.append((query, arguments))
                return self.fetch_results.pop(0)

            async def execute(self, query, *arguments):
                self.execute_call = (query, arguments)
                return "UPDATE 1"

        selected = {
            "operation_id": before["operation_id"],
            "operation_type": "retain",
            "expected_status": "failed",
            "row_digest": live_row_digest(before),
            "task_payload_digest": before["task_payload_digest"],
        }
        connection = ApplyConnection()
        generation_before, generation_after = asyncio.run(
            apply_requeue_transaction(
                connection,
                profile_id="systalyze",
                schema="public",
                bank_id="engineering",
                plan={
                    "pre_generation": "systalyze:public:123",
                    "selected_operations": [selected],
                    "expires_at": int(time.time()) + 60,
                },
            )
        )

        self.assertEqual(generation_before, "systalyze:public:123")
        self.assertEqual(generation_after, "systalyze:public:124")
        self.assertEqual(
            [str(value) for value in connection.assert_selected_ids[0]],
            [before["operation_id"]],
        )
        update, arguments = connection.execute_call
        self.assertIn("SET status = 'pending'", update)
        self.assertIn("retry_count = 0", update)
        self.assertNotIn("task_payload =", update)
        self.assertNotIn("result_metadata =", update)
        self.assertEqual(arguments[1], "engineering")
        self.assertEqual(
            sum(
                "transaction_timeout" in query
                for query, _arguments in connection.deadline_settings
            ),
            1,
        )

    def test_apply_rejects_expired_plan_before_database_reads(self):
        connection = FakeConnection()
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "requeue plan expired",
        ):
            asyncio.run(
                apply_requeue_transaction(
                    connection,
                    profile_id="systalyze",
                    schema="public",
                    bank_id="engineering",
                    plan={
                        "pre_generation": "systalyze:public:123",
                        "selected_operations": [],
                        "expires_at": int(time.time()) - 1,
                    },
                )
            )
        self.assertEqual(connection.generation_reads, 0)

    def test_apply_rechecks_expiry_after_selected_rows_are_locked(self):
        before = {
            "operation_id": "00000000-0000-4000-8000-000000000001",
            "bank_id": "engineering",
            "operation_type": "retain",
            "status": "failed",
            "created_at": "2026-07-29T12:00:00.000000Z",
            "updated_at": "2026-07-29T13:00:00.000000Z",
            "completed_at": "2026-07-29T13:00:00.000000Z",
            "retry_count": 2,
            "next_retry_at": None,
            "worker_id_present": False,
            "worker_id_digest": None,
            "claimed_at": None,
            "task_payload_present": True,
            "task_payload_digest": "a" * 64,
            "result_metadata_digest": "b" * 64,
            "error_category": "provider_capacity",
            "error_digest": "c" * 64,
        }

        class DelayedConnection(FakeConnection):
            def __init__(self):
                super().__init__()
                self.fetchval_results = [123, 0, False]
                self.execute_calls = []

            async def fetchval(self, query, *arguments):
                if "set_config" in query:
                    return arguments[-1]
                return self.fetchval_results.pop(0)

            async def fetch(self, query, *arguments):
                return [before]

            async def execute(self, query, *arguments):
                self.execute_calls.append((query, arguments))
                return "UPDATE 1"

        selected = {
            "operation_id": before["operation_id"],
            "operation_type": "retain",
            "expected_status": "failed",
            "row_digest": live_row_digest(before),
            "task_payload_digest": before["task_payload_digest"],
        }
        connection = DelayedConnection()
        with (
            patch(
                "hindsight_memory_control_plane.operation_recovery_runtime.time.time",
                side_effect=[100.0, 100.0, 101.0],
            ),
            self.assertRaisesRegex(OperationRecoveryError, "expired"),
        ):
            asyncio.run(
                apply_requeue_transaction(
                    connection,
                    profile_id="systalyze",
                    schema="public",
                    bank_id="engineering",
                    plan={
                        "pre_generation": "systalyze:public:123",
                        "selected_operations": [selected],
                        "expires_at": 101,
                    },
                )
            )
        self.assertEqual(connection.execute_calls, [])

    def test_apply_rolls_back_when_plan_expires_before_commit(self):
        before = {
            "operation_id": "00000000-0000-4000-8000-000000000001",
            "bank_id": "engineering",
            "operation_type": "retain",
            "status": "failed",
            "created_at": "2026-07-29T12:00:00.000000Z",
            "updated_at": "2026-07-29T13:00:00.000000Z",
            "completed_at": "2026-07-29T13:00:00.000000Z",
            "retry_count": 2,
            "next_retry_at": "2026-07-29T13:05:00.000000Z",
            "worker_id_present": False,
            "worker_id_digest": None,
            "claimed_at": None,
            "task_payload_present": True,
            "task_payload_digest": "a" * 64,
            "result_metadata_digest": "b" * 64,
            "error_category": "provider_capacity",
            "error_digest": "c" * 64,
        }
        after = {
            **before,
            "status": "pending",
            "updated_at": "2026-07-30T16:00:00.000000Z",
            "completed_at": None,
            "retry_count": 0,
            "next_retry_at": None,
            "error_category": "none",
            "error_digest": None,
        }

        class ExpiringConnection(FakeConnection):
            def __init__(self):
                super().__init__()
                self.fetchval_results = [123, 0, False, 124]
                self.fetch_results = [[before], [after]]
                self.committed = False
                self.rolled_back = False
                self.deadline_settings = []

            @asynccontextmanager
            async def transaction(self, **arguments):
                self.transaction_arguments = arguments
                try:
                    yield
                except Exception:
                    self.rolled_back = True
                    raise
                else:
                    self.committed = True

            async def fetchval(self, query, *arguments):
                if "set_config" in query:
                    self.deadline_settings.append((query, arguments))
                    return arguments[-1]
                return self.fetchval_results.pop(0)

            async def fetch(self, query, *arguments):
                self.fetch_calls.append((query, arguments))
                return self.fetch_results.pop(0)

            async def execute(self, query, *arguments):
                return "UPDATE 1"

        selected = {
            "operation_id": before["operation_id"],
            "operation_type": "retain",
            "expected_status": "failed",
            "row_digest": live_row_digest(before),
            "task_payload_digest": before["task_payload_digest"],
        }
        connection = ExpiringConnection()
        with (
            patch(
                "hindsight_memory_control_plane.operation_recovery_runtime.time.time",
                side_effect=[100.0, 100.0, 100.0, 100.0, 101.0],
            ),
            self.assertRaisesRegex(OperationRecoveryError, "expired"),
        ):
            asyncio.run(
                apply_requeue_transaction(
                    connection,
                    profile_id="systalyze",
                    schema="public",
                    bank_id="engineering",
                    plan={
                        "pre_generation": "systalyze:public:123",
                        "selected_operations": [selected],
                        "expires_at": 101,
                    },
                )
            )
        self.assertFalse(connection.committed)
        self.assertTrue(connection.rolled_back)

    def test_apply_rejects_every_incomplete_retry_postcondition(self):
        before = {
            "operation_id": "00000000-0000-4000-8000-000000000001",
            "bank_id": "engineering",
            "operation_type": "retain",
            "status": "failed",
            "created_at": "2026-07-29T12:00:00.000000Z",
            "updated_at": "2026-07-29T13:00:00.000000Z",
            "completed_at": "2026-07-29T13:00:00.000000Z",
            "retry_count": 2,
            "next_retry_at": "2026-07-29T13:05:00.000000Z",
            "worker_id_present": False,
            "worker_id_digest": None,
            "claimed_at": None,
            "task_payload_present": True,
            "task_payload_digest": "a" * 64,
            "result_metadata_digest": "b" * 64,
            "error_category": "provider_capacity",
            "error_digest": "c" * 64,
        }
        valid_after = {
            **before,
            "status": "pending",
            "updated_at": "2026-07-30T16:00:00.000000Z",
            "completed_at": None,
            "retry_count": 0,
            "next_retry_at": None,
            "error_category": "none",
            "error_digest": None,
        }

        class PostconditionConnection(FakeConnection):
            def __init__(self, after):
                super().__init__()
                self.fetchval_results = [123, 0, False, 124]
                self.fetch_results = [[before], [after]]

            async def fetchval(self, query, *arguments):
                if "set_config" in query:
                    return arguments[-1]
                return self.fetchval_results.pop(0)

            async def fetch(self, query, *arguments):
                self.fetch_calls.append((query, arguments))
                return self.fetch_results.pop(0)

            async def execute(self, query, *arguments):
                return "UPDATE 1"

        selected = {
            "operation_id": before["operation_id"],
            "operation_type": "retain",
            "expected_status": "failed",
            "row_digest": live_row_digest(before),
            "task_payload_digest": before["task_payload_digest"],
        }
        violations = {
            "completed_at": {"completed_at": "2026-07-29T13:00:00.000000Z"},
            "next_retry_at": {"next_retry_at": "2026-07-29T13:05:00.000000Z"},
            "error_message": {
                "error_category": "provider_capacity",
                "error_digest": "c" * 64,
            },
            "updated_at": {
                "updated_at": before["updated_at"],
            },
        }
        for label, changed_fields in violations.items():
            with self.subTest(postcondition=label):
                connection = PostconditionConnection({**valid_after, **changed_fields})
                with self.assertRaisesRegex(
                    OperationRecoveryError,
                    "post-state differs",
                ):
                    asyncio.run(
                        apply_requeue_transaction(
                            connection,
                            profile_id="systalyze",
                            schema="public",
                            bank_id="engineering",
                            plan={
                                "pre_generation": "systalyze:public:123",
                                "selected_operations": [selected],
                                "expires_at": int(time.time()) + 60,
                            },
                        )
                    )

    def test_post_abort_v10_retry_postcondition_reads_the_selected_row(self):
        before = {
            "operation_id": "00000000-0000-4000-8000-000000000001",
            "bank_id": "engineering",
            "operation_type": "retain",
            "status": "failed",
            "created_at": "2026-07-29T12:00:00.000000Z",
            "updated_at": "2026-07-29T13:00:00.000000Z",
            "completed_at": "2026-07-29T13:00:00.000000Z",
            "retry_count": 3,
            "next_retry_at": None,
            "worker_id_present": True,
            "worker_id_digest": "d" * 64,
            "claimed_at": "2026-07-29T12:30:00.000000Z",
            "task_payload_present": True,
            "task_payload_digest": "a" * 64,
            "result_metadata_digest": "b" * 64,
            "error_category": "provider_capacity",
            "error_digest": "c" * 64,
        }
        after = {
            **before,
            "status": "pending",
            "updated_at": "2026-07-30T16:00:00.000000Z",
            "completed_at": None,
            "retry_count": 0,
            "next_retry_at": None,
            "worker_id_present": False,
            "worker_id_digest": None,
            "claimed_at": None,
            "error_category": "none",
            "error_digest": None,
        }

        class PostAbortConnection(FakeConnection):
            def __init__(self):
                super().__init__()
                self.fetchval_results = [123, 0, False, 124]
                self.fetch_results = [[before], [after]]

            async def fetchval(self, query, *arguments):
                if "set_config" in query:
                    return arguments[-1]
                return self.fetchval_results.pop(0)

            async def fetch(self, query, *arguments):
                self.fetch_calls.append((query, arguments))
                return self.fetch_results.pop(0)

            async def execute(self, query, *arguments):
                return "UPDATE 1"

        selected = {
            "operation_id": before["operation_id"],
            "operation_type": "retain",
            "expected_status": "failed",
            "row_digest": live_row_digest(before),
            "task_payload_digest": before["task_payload_digest"],
        }
        plan = {
            "schema_version": 10,
            "pre_generation": "systalyze:public:123",
            "selected_operations": [selected],
            "live_snapshot": {
                "operations": [
                    {
                        "operation_id": before["operation_id"],
                        "row_digest": live_row_digest(before),
                    }
                ]
            },
            "retry_recovery": {
                "operations": [
                    {
                        "operation_id": before["operation_id"],
                        "retry_count_before": 3,
                        "retry_count_after": 0,
                        "reset_applied": True,
                    }
                ]
            },
            "reference_worker_id_digest": before["worker_id_digest"],
            "transaction_timeout_seconds": 120,
            "expires_at": int(time.time()) + 60,
        }
        with patch.object(
            operation_recovery_runtime,
            "verify_post_abort_recovery_plan",
            side_effect=lambda value: value,
        ):
            generations = asyncio.run(
                apply_post_abort_recovery_transaction(
                    PostAbortConnection(),
                    profile_id="systalyze",
                    schema="public",
                    bank_id="engineering",
                    plan=plan,
                )
            )

        self.assertEqual(
            generations,
            ("systalyze:public:123", "systalyze:public:124"),
        )

    def test_rollback_reconciles_claimed_selected_preimage(self):
        restored = {
            "operation_id": "00000000-0000-4000-8000-000000000001",
            "bank_id": "engineering",
            "operation_type": "retain",
            "status": "failed",
            "created_at": "2026-07-29T12:00:00.000000Z",
            "updated_at": "2026-07-29T13:00:00.000000Z",
            "completed_at": "2026-07-29T13:00:00.000000Z",
            "retry_count": 2,
            "next_retry_at": None,
            "worker_id_present": True,
            "worker_id_digest": hashlib.sha256(
                b"orphaned-worker"
            ).hexdigest(),
            "claimed_at": "2026-07-29T12:30:00.000000Z",
            "task_payload_present": True,
            "task_payload_digest": "a" * 64,
            "result_metadata_digest": "b" * 64,
            "error_category": "provider_capacity",
            "error_digest": hashlib.sha256(
                b"provider capacity exhausted"
            ).hexdigest(),
        }
        selected = {
            "operation_id": restored["operation_id"],
            "operation_type": "retain",
            "expected_status": "failed",
            "row_digest": "c" * 64,
            "task_payload_digest": restored["task_payload_digest"],
        }
        preimage = {
            "operation_id": restored["operation_id"],
            "status": "failed",
            "error_message": "provider capacity exhausted",
            "completed_at": restored["completed_at"],
            "next_retry_at": None,
            "worker_id": "orphaned-worker",
            "claimed_at": restored["claimed_at"],
            "retry_count": 2,
            "updated_at": restored["updated_at"],
            "task_payload_digest": restored["task_payload_digest"],
        }

        class ReconcileConnection(FakeConnection):
            def __init__(self):
                super().__init__()
                self.execute_calls = []

            async def fetchval(self, query, *arguments):
                return 125

            async def fetch(self, query, *arguments):
                return [restored]

            async def execute(self, query, *arguments):
                self.execute_calls.append((query, arguments))
                return "UPDATE 1"

        connection = ReconcileConnection()
        before, after = asyncio.run(
            rollback_requeue_transaction(
                connection,
                profile_id="systalyze",
                schema="public",
                bank_id="engineering",
                plan={"selected_operations": [selected]},
                application={
                    "post_generation": "systalyze:public:124"
                },
                rollback_record={
                    "pre_generation": "systalyze:public:124",
                    "post_generation": "systalyze:public:125",
                },
                preimage=[preimage],
            )
        )

        self.assertEqual(before, "systalyze:public:124")
        self.assertEqual(after, "systalyze:public:125")
        self.assertEqual(connection.execute_calls, [])

        restored["worker_id_digest"] = hashlib.sha256(
            b"different-worker"
        ).hexdigest()
        drifted = ReconcileConnection()
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "rollback post-state differs",
        ):
            asyncio.run(
                rollback_requeue_transaction(
                    drifted,
                    profile_id="systalyze",
                    schema="public",
                    bank_id="engineering",
                    plan={"selected_operations": [selected]},
                    application={
                        "post_generation": "systalyze:public:124"
                    },
                    rollback_record={
                        "pre_generation": "systalyze:public:124",
                        "post_generation": "systalyze:public:125",
                    },
                    preimage=[preimage],
                )
            )
        self.assertEqual(drifted.execute_calls, [])

    def test_connect_authenticates_only_over_exact_unix_peer_pid(self):
        class Connected:
            async def close(self):
                return None

        class FakeAsyncpg:
            async def connect(self, **arguments):
                loop = asyncio.get_running_loop()
                transport, _protocol = await loop.create_unix_connection(
                    asyncio.Protocol,
                    str(
                        Path(arguments["host"])
                        / f".s.PGSQL.{arguments['port']}"
                    ),
                )
                transport.close()
                await asyncio.sleep(0)
                return Connected()

        async def exercise(expected_pid):
            with tempfile.TemporaryDirectory(
                dir="/private/tmp",
                prefix="hindsight-peer-test-",
            ) as directory:
                socket_path = str(Path(directory) / ".s.PGSQL.55439")
                listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                listener.bind(socket_path)
                listener.listen(1)

                def close_client():
                    client, _address = listener.accept()
                    try:
                        while client.recv(4096):
                            pass
                    finally:
                        client.close()

                thread = threading.Thread(target=close_client)
                thread.start()
                try:
                    return await connect_verified_local_postgres(
                        FakeAsyncpg(),
                        {
                            "pid": expected_pid,
                            "socket_dir": directory,
                            "socket_path": socket_path,
                            "port": 55439,
                            "user": "hindsight",
                            "database": "hindsight",
                        },
                        password="test-only",
                        readonly=True,
                    )
                finally:
                    await asyncio.sleep(0)
                    listener.close()
                    thread.join(timeout=5)
                    self.assertFalse(thread.is_alive())

        connection = asyncio.run(exercise(os.getpid()))
        asyncio.run(connection.close())
        with self.assertRaisesRegex(
            OperationRecoveryError,
            "peer PID differs",
        ):
            asyncio.run(exercise(os.getpid() + 1))


if __name__ == "__main__":
    unittest.main()
