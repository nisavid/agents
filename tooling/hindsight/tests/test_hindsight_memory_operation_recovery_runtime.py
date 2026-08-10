from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
import asyncio
import hashlib
import os
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from hindsight_memory_control_plane.operation_recovery_runtime import (  # noqa: E402
    GLOBAL_QUEUE_BLOCKER_QUERY,
    QUEUE_BLOCKER_GUARD_CONTRACT_DIGEST,
    QUEUE_BLOCKER_GUARD_CONTRACT_VERSION,
    QUEUE_BLOCKER_PREDICATE,
    CLAIM_RELEASE_EVIDENCE_QUERY,
    ExactDrainClaimAdapter,
    SAFE_OPERATION_QUERY,
    apply_requeue_transaction,
    assert_connected_live_database,
    connect_verified_local_postgres,
    exact_drain_worker_interpreter_path,
    install_exact_drain_runtime_guards,
    live_row_digest,
    read_global_queue_blockers,
    read_claim_release_evidence,
    read_snapshot,
    rollback_requeue_transaction,
    _exact_drain_interpreter_evidence,
)
from hindsight_memory_control_plane.operation_recovery import (  # noqa: E402
    OperationRecoveryError,
)


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
    def test_runtime_guard_rejects_missing_progress_seams(self):
        class MissingWorkerPoller:
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

    def test_runtime_guard_records_claim_only_after_upstream_commit_seam(self):
        committed = []
        task = type("Task", (), {"operation_id": "operation-1"})()

        class Adapter:
            def claim_committed(self, tasks):
                committed.extend(tasks)

        class PostgreSQLOps:
            pass

        class WorkerPoller:
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

        class WorkerPoller:
            def __init__(self):
                self._shutdown = asyncio.Event()

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
        )

        poller = WorkerPoller()
        asyncio.run(asyncio.wait_for(poller.run(), timeout=1))
        self.assertEqual(adapter._started_ids, {operation_id})
        self.assertTrue(poller._shutdown.is_set())
        self.assertEqual(aborts, [True])

    def test_runtime_guard_projects_upstream_stage_holder_changes(self):
        stages = []

        class Adapter:
            def record_upstream_stage(self, operation_id, stage):
                stages.append((operation_id, stage))

        class PostgreSQLOps:
            pass

        class WorkerPoller:
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

        class Adapter:
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

        class WorkerPoller:
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
