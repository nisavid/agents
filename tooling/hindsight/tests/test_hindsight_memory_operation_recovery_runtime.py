from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch
import asyncio
import hashlib
import os
import socket
import sys
import tempfile
import threading
import time
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
    SAFE_OPERATION_QUERY,
    apply_requeue_transaction,
    assert_connected_live_database,
    connect_verified_local_postgres,
    live_row_digest,
    read_global_queue_blockers,
    read_claim_release_evidence,
    read_snapshot,
    rollback_requeue_transaction,
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
