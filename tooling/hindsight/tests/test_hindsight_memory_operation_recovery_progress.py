from pathlib import Path
import json
import os
import stat
import tempfile
import unittest
from unittest.mock import patch


from tooling.hindsight.lib.hindsight_memory_control_plane.operation_recovery_progress import (
    ExactDrainProgressRecorder,
    _read_private,
    _write_private,
    create_exact_drain_progress_recorder,
    read_exact_drain_progress,
)
from tooling.hindsight.lib.hindsight_memory_control_plane.canonical import digest
from tooling.hindsight.lib.hindsight_memory_control_plane.operation_recovery import (
    OperationRecoveryError,
)


class ExactDrainProgressTest(unittest.TestCase):
    def test_task_failure_schemas_reject_worker_only_lease_expiry(self) -> None:
        selected = [
            {
                "operation_id": "00000000-0000-4000-8000-000000000001",
                "operation_type": "retain",
                "row_digest": "b" * 64,
            }
        ]
        for schema_version in (2, 3):
            with self.subTest(schema_version=schema_version):
                with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
                    recorder = ExactDrainProgressRecorder(
                        path=Path(directory) / "exact-drain-progress.json",
                        plan_digest="a" * 64,
                        worker_pid=1234,
                        worker_start_time="darwin:1000:1",
                        worker_attempt=1,
                        selected_operations=selected,
                        progress_schema_version=schema_version,
                        clock=lambda: 1000.0,
                    )
                    with self.assertRaisesRegex(
                        OperationRecoveryError,
                        "failure evidence is invalid",
                    ):
                        recorder.task_outcome(
                            selected[0]["operation_id"],
                            status="failed",
                            stage="failed",
                            failure={
                                "category": "execution_lease_expired",
                                "retryable": False,
                                "http_status": None,
                                "error_digest": "c" * 64,
                            },
                            checkpoint=None,
                        )

    def test_v2_reader_rejects_resealed_worker_only_task_failure(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "exact-drain-progress.json"
            operation_id = "00000000-0000-4000-8000-000000000001"
            recorder = ExactDrainProgressRecorder(
                path=path,
                plan_digest="a" * 64,
                worker_pid=1234,
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[
                    {
                        "operation_id": operation_id,
                        "operation_type": "retain",
                        "row_digest": "b" * 64,
                    }
                ],
                progress_schema_version=2,
                clock=lambda: 1000.0,
            )
            recorder.task_outcome(
                operation_id,
                status="failed",
                stage="failed",
                failure={
                    "category": "provider_transport",
                    "retryable": False,
                    "http_status": None,
                    "error_digest": "c" * 64,
                },
                checkpoint=None,
            )
            forged = json.loads(path.read_text(encoding="utf-8"))
            forged["tasks"][0]["failure"]["category"] = (
                "execution_lease_expired"
            )
            forged["progress_digest"] = digest(
                {
                    key: value
                    for key, value in forged.items()
                    if key != "progress_digest"
                }
            )
            path.write_text(
                json.dumps(forged, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                OperationRecoveryError,
                "failure evidence is invalid",
            ):
                read_exact_drain_progress(
                    path,
                    plan_digest="a" * 64,
                    progress_schema_version=2,
                )

    def test_task_runtime_failure_rejects_worker_only_category_atomically(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "exact-drain-progress.json"
            operation_id = "00000000-0000-4000-8000-000000000001"
            recorder = ExactDrainProgressRecorder(
                path=path,
                plan_digest="a" * 64,
                worker_pid=1234,
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[
                    {
                        "operation_id": operation_id,
                        "operation_type": "retain",
                        "row_digest": "b" * 64,
                    }
                ],
                progress_schema_version=3,
                clock=lambda: 1000.0,
            )
            recorder.task_stage(
                operation_id,
                status="processing",
                stage="retain.phase1.request",
            )
            before = path.read_bytes()
            before_progress = read_exact_drain_progress(
                path,
                plan_digest="a" * 64,
                progress_schema_version=3,
            )

            with self.assertRaisesRegex(
                OperationRecoveryError,
                "failure evidence is invalid",
            ):
                recorder.task_runtime_failure(
                    operation_id,
                    stage="failure.runtime",
                    failure={
                        "category": "execution_lease_expired",
                        "retryable": False,
                        "http_status": None,
                        "error_digest": "c" * 64,
                    },
                )

            after_progress = read_exact_drain_progress(
                path,
                plan_digest="a" * 64,
                progress_schema_version=3,
            )
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(
                after_progress["progress_digest"],
                before_progress["progress_digest"],
            )

    def test_v3_progress_accepts_closed_execution_lease_expiry(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "exact-drain-progress.json"
            recorder = ExactDrainProgressRecorder(
                path=path,
                plan_digest="a" * 64,
                worker_pid=1234,
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[],
                progress_schema_version=3,
                clock=lambda: 1000.0,
            )
            recorder.worker_stage(
                status="running",
                stage="worker.poller.running",
            )
            recorder.worker_failure(
                exit_code=2,
                failure={
                    "category": "execution_lease_expired",
                    "retryable": False,
                    "http_status": None,
                    "error_digest": "c" * 64,
                },
            )
            progress = read_exact_drain_progress(
                path,
                plan_digest="a" * 64,
                progress_schema_version=3,
                now=1001.0,
            )

        self.assertEqual(
            progress["worker_failure_stage"],
            "worker.poller.running",
        )
        self.assertEqual(
            progress["worker_failure"]["category"],
            "execution_lease_expired",
        )
        self.assertFalse(progress["worker_failure"]["retryable"])

    def test_v3_progress_records_preclaim_worker_failure_without_raw_error(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "exact-drain-progress.json"
            recorder = ExactDrainProgressRecorder(
                path=path,
                plan_digest="a" * 64,
                worker_pid=1234,
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[
                    {
                        "operation_id": (
                            "00000000-0000-4000-8000-000000000001"
                        ),
                        "operation_type": "retain",
                        "row_digest": "b" * 64,
                    }
                ],
                progress_schema_version=3,
                clock=lambda: 1000.0,
            )
            recorder.worker_stage(
                status="starting",
                stage="worker.memory.initialize",
            )
            recorder.worker_failure(
                exit_code=2,
                failure={
                    "category": "provider_transport",
                    "retryable": True,
                    "http_status": None,
                    "error_digest": "c" * 64,
                }
            )
            progress = read_exact_drain_progress(
                path,
                plan_digest="a" * 64,
                progress_schema_version=3,
                now=1001.0,
            )
            raw = path.read_text(encoding="utf-8")

        self.assertEqual(progress["worker_status"], "failed")
        self.assertEqual(progress["worker_stage"], "failed")
        self.assertEqual(progress["worker_exit_code"], 2)
        self.assertEqual(
            progress["worker_failure_stage"],
            "worker.memory.initialize",
        )
        self.assertEqual(
            progress["worker_failure"],
            {
                "category": "provider_transport",
                "retryable": True,
                "http_status": None,
                "error_digest": "c" * 64,
            },
        )
        self.assertEqual(progress["selected_status_counts"], {"pending": 1})
        self.assertEqual(progress["tasks"][0]["stage"], "queued")
        self.assertNotIn("provider socket closed", raw)

    def test_v3_progress_rejects_resealed_failed_stage_without_failure(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "exact-drain-progress.json"
            ExactDrainProgressRecorder(
                path=path,
                plan_digest="a" * 64,
                worker_pid=1234,
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[],
                progress_schema_version=3,
                clock=lambda: 1000.0,
            )
            forged = json.loads(path.read_text(encoding="utf-8"))
            forged["worker_stage"] = "failed"
            body = {
                key: value
                for key, value in forged.items()
                if key != "progress_digest"
            }
            forged["progress_digest"] = digest(body)
            path.write_text(
                json.dumps(forged, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                OperationRecoveryError,
                "worker evidence is invalid",
            ):
                read_exact_drain_progress(
                    path,
                    plan_digest="a" * 64,
                    progress_schema_version=3,
                )

    def test_v3_resume_exposes_closed_prior_worker_failure(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "exact-drain-progress.json"
            plan = {
                "plan_digest": "a" * 64,
                "progress_artifact_path": str(path),
                "progress_schema_version": 3,
                "worker_max_attempts": 3,
                "selected_operations": [],
            }
            authorization = {
                "receipt_digest": "c" * 64,
                "authorized_at": 1000,
            }

            def journal(attempt: int) -> dict:
                body = {
                    "schema_version": 1,
                    "kind": "operation-recovery-exact-drain-application-journal",
                    "plan_digest": plan["plan_digest"],
                    "authorization_receipt_digest": authorization[
                        "receipt_digest"
                    ],
                    "started_at": authorization["authorized_at"],
                    "worker_pid": os.getpid(),
                    "worker_start_time": f"darwin:1000:{attempt}",
                    "worker_attempt": attempt,
                }
                return {**body, "receipt_digest": digest(body)}

            first = create_exact_drain_progress_recorder(
                plan=plan,
                authorization=authorization,
                journal=journal(1),
                clock=lambda: 1001.0,
            )
            first.worker_stage(
                status="starting",
                stage="worker.memory.initialize",
            )
            first.worker_failure(
                exit_code=2,
                failure={
                    "category": "provider_transport",
                    "retryable": True,
                    "http_status": None,
                    "error_digest": "d" * 64,
                },
            )
            create_exact_drain_progress_recorder(
                plan=plan,
                authorization=authorization,
                journal=journal(2),
                clock=lambda: 1002.0,
            )
            resumed = read_exact_drain_progress(
                path,
                plan_digest=plan["plan_digest"],
                progress_schema_version=3,
                now=1003.0,
            )

        prior = resumed["prior_attempts"][0]
        self.assertEqual(prior["worker_status"], "failed")
        self.assertEqual(
            prior["worker_failure_stage"],
            "worker.memory.initialize",
        )
        self.assertEqual(prior["worker_failure"]["category"], "provider_transport")
        self.assertEqual(prior["worker_exit_code"], 2)

    def test_v2_progress_exposes_only_closed_failure_and_checkpoint_evidence(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "exact-drain-progress.json"
            operation_id = "00000000-0000-4000-8000-000000000001"
            recorder = ExactDrainProgressRecorder(
                path=path,
                plan_digest="a" * 64,
                worker_pid=1234,
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[
                    {
                        "operation_id": operation_id,
                        "operation_type": "retain",
                        "row_digest": "b" * 64,
                    }
                ],
                progress_schema_version=2,
                clock=lambda: 1000.0,
            )
            recorder.task_stage(
                operation_id,
                status="processing",
                stage="retain.phase1.candidates.fuzzy.2/3",
            )
            recorder.task_outcome(
                operation_id,
                status="failed",
                stage="failed",
                failure={
                    "category": "phase_one_timeout",
                    "retryable": False,
                    "http_status": None,
                    "error_digest": "c" * 64,
                },
                checkpoint={
                    "facts_committed": True,
                    "committed_document_count": 1,
                    "unit_ids_count": 29,
                    "stage": "storing",
                    "processed": 14,
                    "total": 14,
                },
            )
            progress = read_exact_drain_progress(
                path,
                plan_digest="a" * 64,
                progress_schema_version=2,
                now=1001.0,
            )
            raw = path.read_text(encoding="utf-8")

        task = progress["tasks"][0]
        self.assertEqual(task["failure_stage"], "retain.phase1.candidates.fuzzy.2/3")
        self.assertEqual(
            task["failure"],
            {
                "category": "phase_one_timeout",
                "retryable": False,
                "http_status": None,
                "error_digest": "c" * 64,
            },
        )
        self.assertTrue(task["checkpoint"]["facts_committed"])
        self.assertEqual(task["checkpoint"]["committed_document_count"], 1)
        self.assertEqual(task["checkpoint"]["unit_ids_count"], 29)
        self.assertEqual(task["checkpoint"]["stage"], "storing")
        self.assertEqual(task["checkpoint"]["processed"], 14)
        self.assertEqual(task["checkpoint"]["total"], 14)
        self.assertRegex(task["checkpoint"]["checkpoint_digest"], r"^[0-9a-f]{64}$")
        for forbidden in ("raw timeout text", "prompt", "response_body", "credential"):
            self.assertNotIn(forbidden, raw.lower())

    def test_v2_runtime_failure_preserves_the_last_committed_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "exact-drain-progress.json"
            operation_id = "00000000-0000-4000-8000-000000000001"
            recorder = ExactDrainProgressRecorder(
                path=path,
                plan_digest="a" * 64,
                worker_pid=1234,
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[
                    {
                        "operation_id": operation_id,
                        "operation_type": "retain",
                        "row_digest": "b" * 64,
                    }
                ],
                progress_schema_version=2,
                clock=lambda: 1000.0,
            )
            recorder.task_stage(
                operation_id,
                status="processing",
                stage="retain.phase2.storing",
            )
            recorder.task_outcome(
                operation_id,
                status="processing",
                stage="retain.phase2.committed",
                failure=None,
                checkpoint={
                    "facts_committed": True,
                    "committed_document_count": 1,
                    "unit_ids_count": 29,
                    "stage": "storing",
                    "processed": 14,
                    "total": 14,
                },
            )
            recorder.task_runtime_failure(
                operation_id,
                stage="failure.terminal-state",
                failure={
                    "category": "terminal_state_persistence",
                    "retryable": False,
                    "http_status": None,
                    "error_digest": "c" * 64,
                },
            )
            progress = read_exact_drain_progress(
                path,
                plan_digest="a" * 64,
                progress_schema_version=2,
                now=1001.0,
            )

        task = progress["tasks"][0]
        self.assertEqual(task["status"], "processing")
        self.assertEqual(task["stage"], "failure.terminal-state")
        self.assertEqual(task["failure_stage"], "retain.phase2.committed")
        self.assertEqual(
            task["failure"]["category"],
            "terminal_state_persistence",
        )
        self.assertTrue(task["checkpoint"]["facts_committed"])
        self.assertEqual(task["checkpoint"]["processed"], 14)

    def test_legacy_progress_schemas_reject_schema_five_failure_categories(
        self,
    ) -> None:
        for progress_schema_version in (2, 3, 4):
            with self.subTest(progress_schema_version=progress_schema_version):
                with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
                    operation_id = "00000000-0000-4000-8000-000000000001"
                    recorder = ExactDrainProgressRecorder(
                        path=Path(directory) / "exact-drain-progress.json",
                        plan_digest="a" * 64,
                        worker_pid=1234,
                        worker_start_time="darwin:1000:1",
                        worker_attempt=1,
                        selected_operations=[
                            {
                                "operation_id": operation_id,
                                "operation_type": "retain",
                                "row_digest": "b" * 64,
                            }
                        ],
                        progress_schema_version=progress_schema_version,
                        clock=lambda: 1000.0,
                    )
                    with self.assertRaisesRegex(
                        OperationRecoveryError,
                        "failure evidence is invalid",
                    ):
                        recorder.task_outcome(
                            operation_id,
                            status="failed",
                            stage="failed",
                            failure={
                                "category": "upstream_timeout",
                                "retryable": False,
                                "http_status": None,
                                "error_digest": "c" * 64,
                            },
                            checkpoint=None,
                        )

    def test_schema_five_accepts_richer_timeout_failure_categories(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            operation_id = "00000000-0000-4000-8000-000000000001"
            recorder = ExactDrainProgressRecorder(
                path=Path(directory) / "exact-drain-progress.json",
                plan_digest="a" * 64,
                worker_pid=1234,
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[
                    {
                        "operation_id": operation_id,
                        "operation_type": "retain",
                        "row_digest": "b" * 64,
                    }
                ],
                progress_schema_version=5,
                clock=lambda: 1000.0,
            )
            recorder.task_outcome(
                operation_id,
                status="failed",
                stage="failed",
                failure={
                    "category": "upstream_timeout",
                    "retryable": False,
                    "http_status": None,
                    "error_digest": "c" * 64,
                },
                checkpoint=None,
            )

            progress = read_exact_drain_progress(
                recorder.path,
                plan_digest="a" * 64,
                progress_schema_version=5,
                now=1001.0,
            )

        self.assertEqual(
            progress["tasks"][0]["failure"]["category"],
            "upstream_timeout",
        )

    def test_create_only_commit_survives_post_rename_interruption(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "archive.json"
            calls = 0
            original_fsync = os.fsync

            def interrupted_fsync(descriptor: int) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("interrupted after rename")
                original_fsync(descriptor)

            with (
                patch(
                    "tooling.hindsight.lib.hindsight_memory_control_plane."
                    "operation_recovery_progress.os.fsync",
                    side_effect=interrupted_fsync,
                ),
                self.assertRaisesRegex(
                    OperationRecoveryError,
                    "progress is unavailable",
                ),
            ):
                _write_private(path, {"evidence": "committed"}, create_only=True)

            self.assertEqual(path.stat().st_nlink, 1)
            self.assertEqual(_read_private(path), {"evidence": "committed"})

    def test_progress_clock_never_moves_backward(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "exact-drain-progress.json"
            readings = iter((1000.0, 900.0, 800.0))
            recorder = ExactDrainProgressRecorder(
                path=path,
                plan_digest="a" * 64,
                worker_pid=1234,
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[
                    {
                        "operation_id": (
                            "00000000-0000-4000-8000-000000000001"
                        ),
                        "operation_type": "retain",
                        "row_digest": "b" * 64,
                    }
                ],
                clock=lambda: next(readings),
            )
            recorder.task_stage(
                "00000000-0000-4000-8000-000000000001",
                status="processing",
                stage="claimed",
            )
            recorder.cooldown(
                "work-codex",
                until=1300.0,
                reason="usage_limit",
            )
            progress = read_exact_drain_progress(
                path,
                plan_digest="a" * 64,
                now=1001.0,
            )

        self.assertEqual(progress["observed_at"], 1000.0)
        self.assertEqual(progress["last_progress_at"], 1000.0)
        self.assertEqual(progress["tasks"][0]["stage_started_at"], 1000.0)

    def test_invalid_cooldown_does_not_mutate_progress(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "exact-drain-progress.json"
            recorder = ExactDrainProgressRecorder(
                path=path,
                plan_digest="a" * 64,
                worker_pid=1234,
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[],
                clock=lambda: 1000.0,
            )
            before = path.read_bytes()
            with self.assertRaises(OperationRecoveryError):
                recorder.provider_failed_over("work-codex")
            self.assertEqual(path.read_bytes(), before)
            for provider_id, until in (
                ("not valid", 1300.0),
                ("work-codex", float("nan")),
            ):
                with self.subTest(provider_id=provider_id, until=until):
                    with self.assertRaises(OperationRecoveryError):
                        recorder.cooldown(
                            provider_id,
                            until=until,
                            reason="usage_limit",
                        )
                    self.assertEqual(path.read_bytes(), before)

    def test_late_upstream_stage_does_not_revive_a_terminal_task(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "exact-drain-progress.json"
            recorder = ExactDrainProgressRecorder(
                path=path,
                plan_digest="a" * 64,
                worker_pid=1234,
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[
                    {
                        "operation_id": "00000000-0000-4000-8000-000000000001",
                        "operation_type": "retain",
                        "row_digest": "b" * 64,
                    }
                ],
                clock=lambda: 1000.0,
            )
            operation_id = "00000000-0000-4000-8000-000000000001"
            recorder.task_stage(
                operation_id,
                status="processing",
                stage="claimed",
            )
            recorder.task_stage(
                operation_id,
                status="completed",
                stage="completed",
            )
            recorder.task_processing_stage(
                operation_id,
                stage="llm.codex.retain.attempt=1/1",
            )
            progress = read_exact_drain_progress(
                path,
                plan_digest="a" * 64,
                now=1001.0,
            )

        self.assertEqual(progress["selected_status_counts"], {"completed": 1})
        self.assertEqual(progress["tasks"][0]["stage"], "completed")

    def test_terminal_persist_failure_restores_processing_breadcrumb(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "exact-drain-progress.json"
            recorder = ExactDrainProgressRecorder(
                path=path,
                plan_digest="a" * 64,
                worker_pid=1234,
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[
                    {
                        "operation_id": "00000000-0000-4000-8000-000000000001",
                        "operation_type": "retain",
                        "row_digest": "b" * 64,
                    }
                ],
                clock=lambda: 1000.0,
            )
            operation_id = "00000000-0000-4000-8000-000000000001"
            recorder.task_stage(
                operation_id,
                status="processing",
                stage="claimed",
            )
            original_persist = recorder._persist
            failures_remaining = 1

            def fail_terminal_persist_once(observed_at: float) -> None:
                nonlocal failures_remaining
                if failures_remaining:
                    failures_remaining -= 1
                    raise OSError("terminal progress persist failed")
                original_persist(observed_at)

            with patch.object(recorder, "_persist", fail_terminal_persist_once):
                with self.assertRaisesRegex(
                    OSError,
                    "terminal progress persist failed",
                ):
                    recorder.task_stage(
                        operation_id,
                        status="failed",
                        stage="failed",
                    )
                recorder.task_processing_stage(
                    operation_id,
                    stage="failure.terminal-state",
                )
            progress = read_exact_drain_progress(
                path,
                plan_digest="a" * 64,
                now=1001.0,
            )

        self.assertEqual(progress["selected_status_counts"], {"processing": 1})
        self.assertEqual(progress["tasks"][0]["stage"], "failure.terminal-state")

    def test_resume_archives_prior_attempt_and_carries_task_state(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "exact-drain-progress.json"
            plan = {
                "plan_digest": "a" * 64,
                "progress_artifact_path": str(path),
                "worker_max_attempts": 3,
                "selected_operations": [
                    {
                        "operation_id": "00000000-0000-4000-8000-000000000001",
                        "operation_type": "retain",
                        "row_digest": "b" * 64,
                    }
                ],
            }
            authorization = {
                "receipt_digest": "c" * 64,
                "authorized_at": 1000,
            }

            def journal(attempt: int) -> dict:
                body = {
                    "schema_version": 1,
                    "kind": "operation-recovery-exact-drain-application-journal",
                    "plan_digest": plan["plan_digest"],
                    "authorization_receipt_digest": authorization["receipt_digest"],
                    "started_at": authorization["authorized_at"],
                    "worker_pid": os.getpid(),
                    "worker_start_time": f"darwin:1000:{attempt}",
                    "worker_attempt": attempt,
                }
                return {**body, "receipt_digest": digest(body)}

            first = create_exact_drain_progress_recorder(
                plan=plan,
                authorization=authorization,
                journal=journal(1),
                clock=lambda: 1001.0,
            )
            first.task_stage(
                "00000000-0000-4000-8000-000000000001",
                status="completed",
                stage="completed",
            )
            first_digest = read_exact_drain_progress(
                path,
                plan_digest=plan["plan_digest"],
                now=1002.0,
            )["progress_digest"]

            create_exact_drain_progress_recorder(
                plan=plan,
                authorization=authorization,
                journal=journal(2),
                clock=lambda: 900.0,
            )
            resumed = read_exact_drain_progress(
                path,
                plan_digest=plan["plan_digest"],
                now=1004.0,
            )

        self.assertEqual(resumed["selected_status_counts"], {"completed": 1})
        self.assertEqual(resumed["tasks"][0]["stage"], "completed")
        self.assertEqual(resumed["prior_attempts"][0]["progress_digest"], first_digest)
        self.assertEqual(resumed["prior_attempts"][0]["selected_status_counts"], {"completed": 1})
        self.assertIn(".attempt-1.json", resumed["prior_attempts"][0]["artifact_path"])

    def test_failover_counter_requires_a_subsequent_provider_attempt(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "exact-drain-progress.json"
            recorder = ExactDrainProgressRecorder(
                path=path,
                plan_digest="a" * 64,
                worker_pid=1234,
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[],
                clock=lambda: 1000.0,
            )
            work = recorder.provider_started(
                "work-codex", retry_attempt=1, scope="retain"
            )
            recorder.provider_finished(work, outcome="failed")
            recorder.provider_failed_over("work-codex")
            hatchery = recorder.provider_started(
                "hatchery", retry_attempt=1, scope="retain"
            )
            recorder.provider_finished(hatchery, outcome="failed")
            progress = read_exact_drain_progress(
                path,
                plan_digest="a" * 64,
                now=1001.0,
            )

        counters = {
            item["provider_id"]: item for item in progress["provider_counters"]
        }
        self.assertEqual(counters["work-codex"]["failed_over"], 1)
        self.assertEqual(counters["hatchery"]["failed_over"], 0)

    def test_provider_cooldown_reason_is_visible_and_clearable(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "exact-drain-progress.json"
            recorder = ExactDrainProgressRecorder(
                path=path,
                plan_digest="a" * 64,
                worker_pid=1234,
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[],
                clock=lambda: 1000.0,
            )
            recorder.cooldown(
                "work-codex",
                until=1300.0,
                reason="usage_limit",
            )
            cooling = read_exact_drain_progress(
                path,
                plan_digest="a" * 64,
                now=1001.0,
            )
            recorder.clear_cooldown("work-codex")
            cleared = read_exact_drain_progress(
                path,
                plan_digest="a" * 64,
                now=1002.0,
            )

        self.assertEqual(
            cooling["cooldowns"],
            [
                {
                    "provider_id": "work-codex",
                    "until": 1300.0,
                    "reason": "usage_limit",
                }
            ],
        )
        self.assertEqual(cleared["cooldowns"], [])

    def test_progress_recorder_is_bound_to_the_application_journal(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "exact-drain-progress.json"
            plan = {
                "plan_digest": "a" * 64,
                "progress_artifact_path": str(path),
                "worker_max_attempts": 3,
                "selected_operations": [
                    {
                        "operation_id": "00000000-0000-4000-8000-000000000001",
                        "operation_type": "retain",
                        "row_digest": "b" * 64,
                    }
                ],
            }
            authorization = {
                "receipt_digest": "c" * 64,
                "authorized_at": 1000,
            }
            body = {
                "schema_version": 1,
                "kind": "operation-recovery-exact-drain-application-journal",
                "plan_digest": plan["plan_digest"],
                "authorization_receipt_digest": authorization[
                    "receipt_digest"
                ],
                "started_at": authorization["authorized_at"],
                "worker_pid": os.getpid(),
                "worker_start_time": "darwin:1000:1",
                "worker_attempt": 1,
            }
            journal = {**body, "receipt_digest": digest(body)}

            recorder = create_exact_drain_progress_recorder(
                plan=plan,
                authorization=authorization,
                journal=journal,
                clock=lambda: 1001.0,
            )
            progress = read_exact_drain_progress(
                path,
                plan_digest=plan["plan_digest"],
                now=1002.0,
            )

            self.assertEqual(recorder.worker_pid, os.getpid())
            self.assertEqual(progress["worker_attempt"], 1)
            changed = dict(journal, worker_pid=os.getpid() + 1)
            with self.assertRaisesRegex(Exception, "journal is invalid"):
                create_exact_drain_progress_recorder(
                    plan=plan,
                    authorization=authorization,
                    journal=changed,
                    clock=lambda: 1002.0,
                )

    def test_progress_is_private_digest_sealed_and_payload_free(self) -> None:
        ticks = iter((1000.0, 1002.0, 1002.0, 1006.0))
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "exact-drain-progress.json"
            recorder = ExactDrainProgressRecorder(
                path=path,
                plan_digest="a" * 64,
                worker_pid=1234,
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[
                    {
                        "operation_id": "00000000-0000-4000-8000-000000000001",
                        "operation_type": "retain",
                        "row_digest": "b" * 64,
                    }
                ],
                clock=lambda: next(ticks),
            )

            recorder.task_stage(
                "00000000-0000-4000-8000-000000000001",
                status="processing",
                stage="claimed",
            )
            request = recorder.provider_started(
                "work-codex",
                retry_attempt=1,
                scope="retain_extract_facts",
            )
            active = read_exact_drain_progress(
                path,
                plan_digest="a" * 64,
                now=1005.0,
            )
            recorder.provider_finished(request, outcome="succeeded")
            finished = read_exact_drain_progress(
                path,
                plan_digest="a" * 64,
                now=1006.0,
            )

            raw = json.loads(path.read_text(encoding="utf-8"))
            mode = stat.S_IMODE(path.stat().st_mode)

        self.assertEqual(mode, 0o600)
        self.assertEqual(active["selected_status_counts"], {"processing": 1})
        self.assertEqual(active["tasks"][0]["stage"], "claimed")
        self.assertEqual(active["tasks"][0]["stage_age_seconds"], 3.0)
        self.assertEqual(active["active_provider_requests"][0]["provider_id"], "work-codex")
        self.assertEqual(finished["active_provider_requests"], [])
        self.assertEqual(
            finished["provider_counters"],
            [
                {
                    "provider_id": "work-codex",
                    "started": 1,
                    "succeeded": 1,
                    "failed": 0,
                    "timed_out": 0,
                    "failed_over": 0,
                    "duration_count": 1,
                    "duration_total_seconds": 4.0,
                    "duration_max_seconds": 4.0,
                    "last_provider_response_at": 1006.0,
                }
            ],
        )
        rendered = json.dumps(raw, sort_keys=True).lower()
        for forbidden in (
            "prompt",
            "response_body",
            "error_message",
            "authorization",
            "credential",
            "api_key",
            "worker_id",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_progress_schema_four_separates_queue_and_execution_age(self) -> None:
        ticks = iter((1000.0, 1001.0, 1003.0, 1006.0))
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "exact-drain-progress.json"
            recorder = ExactDrainProgressRecorder(
                path=path,
                plan_digest="a" * 64,
                worker_pid=1234,
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[],
                progress_schema_version=4,
                clock=lambda: next(ticks),
            )
            request = recorder.provider_started(
                "hatchery",
                retry_attempt=1,
                scope="retain_extract_facts",
            )
            queued = read_exact_drain_progress(
                path,
                plan_digest="a" * 64,
                progress_schema_version=4,
                now=1002.0,
            )
            recorder.provider_executing(request)
            executing = read_exact_drain_progress(
                path,
                plan_digest="a" * 64,
                progress_schema_version=4,
                now=1004.0,
            )
            recorder.provider_finished(request, outcome="succeeded")
            finished = read_exact_drain_progress(
                path,
                plan_digest="a" * 64,
                progress_schema_version=4,
                now=1006.0,
            )

        self.assertEqual(queued["active_provider_requests"][0]["state"], "queued")
        self.assertEqual(queued["active_provider_requests"][0]["queue_age_seconds"], 1.0)
        self.assertIsNone(queued["active_provider_requests"][0]["execution_age_seconds"])
        self.assertEqual(executing["active_provider_requests"][0]["state"], "executing")
        self.assertEqual(executing["active_provider_requests"][0]["queue_age_seconds"], 2.0)
        self.assertEqual(executing["active_provider_requests"][0]["execution_age_seconds"], 1.0)
        self.assertEqual(finished["active_provider_requests"], [])
        self.assertEqual(
            finished["provider_counters"],
            [
                {
                    "provider_id": "hatchery",
                    "started": 1,
                    "succeeded": 1,
                    "failed": 0,
                    "queue_timed_out": 0,
                    "execution_timed_out": 0,
                    "failed_over": 0,
                    "queue_duration_count": 1,
                    "queue_duration_total_seconds": 2.0,
                    "queue_duration_max_seconds": 2.0,
                    "execution_duration_count": 1,
                    "execution_duration_total_seconds": 3.0,
                    "execution_duration_max_seconds": 3.0,
                    "last_provider_response_at": 1006.0,
                }
            ],
        )

    def test_progress_schema_four_preserves_legacy_queued_cancellation_rejection(
        self,
    ) -> None:
        ticks = iter((1000.0, 1001.0))
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "exact-drain-progress.json"
            recorder = ExactDrainProgressRecorder(
                path=path,
                plan_digest="a" * 64,
                worker_pid=1234,
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[],
                progress_schema_version=4,
                clock=lambda: next(ticks),
            )
            request = recorder.provider_started(
                "hatchery",
                retry_attempt=1,
                scope="retain_extract_facts",
            )
            with self.assertRaisesRegex(
                OperationRecoveryError,
                "provider outcome is invalid",
            ):
                recorder.provider_cancelled(request)
            progress = read_exact_drain_progress(
                path,
                plan_digest="a" * 64,
                progress_schema_version=4,
                now=1002.0,
            )

        counter = progress["provider_counters"][0]
        self.assertEqual(
            progress["active_provider_requests"][0]["state"],
            "queued",
        )
        self.assertEqual(counter["failed"], 0)
        self.assertEqual(counter["queue_timed_out"], 0)
        self.assertEqual(counter["execution_timed_out"], 0)

    def test_progress_schema_four_preserves_legacy_executing_cancellation_failure(
        self,
    ) -> None:
        ticks = iter((1000.0, 1001.0, 1002.0, 1003.0))
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "exact-drain-progress.json"
            recorder = ExactDrainProgressRecorder(
                path=path,
                plan_digest="a" * 64,
                worker_pid=1234,
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[],
                progress_schema_version=4,
                clock=lambda: next(ticks),
            )
            request = recorder.provider_started(
                "hatchery",
                retry_attempt=1,
                scope="retain_extract_facts",
            )
            recorder.provider_executing(request)
            recorder.provider_cancelled(request)
            progress = read_exact_drain_progress(
                path,
                plan_digest="a" * 64,
                progress_schema_version=4,
                now=1003.0,
            )

        counter = progress["provider_counters"][0]
        self.assertEqual(progress["active_provider_requests"], [])
        self.assertEqual(counter["failed"], 1)
        self.assertEqual(counter["queue_timed_out"], 0)
        self.assertEqual(counter["execution_timed_out"], 0)

    def test_progress_schema_four_rejects_success_before_gate_admission(self) -> None:
        ticks = iter((1000.0, 1001.0))
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "exact-drain-progress.json"
            recorder = ExactDrainProgressRecorder(
                path=path,
                plan_digest="a" * 64,
                worker_pid=1234,
                worker_start_time="darwin:1000:1",
                worker_attempt=1,
                selected_operations=[],
                progress_schema_version=4,
                clock=lambda: next(ticks),
            )
            request = recorder.provider_started(
                "hatchery",
                retry_attempt=1,
                scope="retain_extract_facts",
            )
            with self.assertRaisesRegex(
                OperationRecoveryError,
                "provider outcome is invalid",
            ):
                recorder.provider_finished(request, outcome="succeeded")
            queued = read_exact_drain_progress(
                path,
                plan_digest="a" * 64,
                progress_schema_version=4,
                now=1002.0,
            )

        self.assertEqual(
            queued["active_provider_requests"][0]["state"],
            "queued",
        )


if __name__ == "__main__":
    unittest.main()
