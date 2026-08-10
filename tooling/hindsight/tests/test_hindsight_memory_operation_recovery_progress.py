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


if __name__ == "__main__":
    unittest.main()
