from __future__ import annotations

import errno
import fcntl
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from unittest import mock

from ._support import (
    CLIENT_ENVIRONMENT,
    CLIENT_SOURCE,
    TERMINAL_OUTPUT_DRIVER_SOURCE,
    ValidInvocationFixture,
    _TaskWitnessClientTestCase,
    load_client_module,
    parse_diagnostic,
    write_configured_driver,
)


class TerminalOutputTests(_TaskWitnessClientTestCase):
    def cleanup_context(self, module: object, pid: int) -> object:
        context = module._prepare_cleanup_context()
        context.publish_pid(pid)
        return context

    def test_closed_stdout_returns_a_structured_resource_error(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        read_descriptor, write_descriptor = os.pipe()
        os.close(read_descriptor)
        try:
            process = subprocess.Popen(
                fixture.command(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                ),
                stdout=write_descriptor,
                stderr=subprocess.PIPE,
                env=CLIENT_ENVIRONMENT,
            )
        finally:
            os.close(write_descriptor)
        _, stderr = process.communicate(timeout=5)

        self.assertEqual(process.returncode, 124, stderr.decode())
        self.assert_diagnostic(
            stderr,
            message="accepted output transport failed",
            validator_code_executed="yes",
            active_state_changed="unknown",
            current_receipt="sha256:" + fixture.receipt["content_sha256"][:12],
            next_action=(
                "discard any visible output; do not retry; repair the caller transport"
            ),
        )

    def test_file_size_limit_returns_a_structured_resource_error(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        write_configured_driver(
            fixture.driver,
            TERMINAL_OUTPUT_DRIVER_SOURCE,
            {"scenario": "file-size-limit"},
        )
        stdout_path = self.root / "accepted-output"
        with stdout_path.open("wb") as stdout:
            process = subprocess.run(
                fixture.command("validate", "--bundle", str(fixture.bundle)),
                stdout=stdout,
                stderr=subprocess.PIPE,
                check=False,
                env=CLIENT_ENVIRONMENT,
                timeout=5,
            )

        self.assertEqual(process.returncode, 124, process.stderr.decode())
        self.assertEqual(stdout_path.read_bytes(), b"")
        self.assert_diagnostic(
            process.stderr,
            message="accepted output transport failed",
            validator_code_executed="yes",
            active_state_changed="unknown",
            current_receipt="sha256:" + fixture.receipt["content_sha256"][:12],
            next_action=(
                "discard any visible output; do not retry; repair the caller transport"
            ),
        )

    def test_output_writer_fork_failure_is_a_resource_failure(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        driver, writer_pid, writer_reaped = self.configured_observed_writer_driver(
            fixture,
            "fork_failure",
            TERMINAL_OUTPUT_DRIVER_SOURCE,
            {"scenario": "writer-fork-failure"},
        )

        result = fixture.invoke(
            str(writer_pid),
            str(writer_reaped),
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
        )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

    def test_post_writer_result_fault_preserves_accepted_bytes_as_resource_failure(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        faulted = self.root / "post-writer-result-faulted"
        write_configured_driver(
            fixture.driver,
            TERMINAL_OUTPUT_DRIVER_SOURCE,
            {
                "scenario": "post-writer-result-fault",
                "faulted": str(faulted),
            },
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            timeout=5,
        )

        self.assertTrue(faulted.is_file())
        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, fixture.envelope_raw)
        self.assert_diagnostic(
            result.stderr,
            message="accepted output transport failed",
            validator_code_executed="yes",
            active_state_changed="unknown",
            current_receipt="sha256:" + fixture.receipt["content_sha256"][:12],
            next_action=(
                "discard any visible output; do not retry; repair the caller transport"
            ),
        )

    def test_unknown_writer_pid_after_real_fork_is_wildcard_reaped(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        forgot_pid = self.root / "forgot-writer-pid"
        wildcard_reaped = self.root / "wildcard-reaped"
        unknown_signaled = self.root / "unknown-writer-signaled"
        driver, writer_pid, writer_reaped = self.configured_observed_writer_driver(
            fixture,
            "forgotten_pid",
            TERMINAL_OUTPUT_DRIVER_SOURCE,
            {
                "scenario": "unknown-writer-pid",
                "forgot_pid": str(forgot_pid),
                "wildcard_reaped": str(wildcard_reaped),
                "unknown_signaled": str(unknown_signaled),
            },
        )

        result = fixture.invoke(
            str(writer_pid),
            str(writer_reaped),
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
            timeout=5,
        )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertTrue(forgot_pid.is_file())
        self.assertTrue(wildcard_reaped.is_file())
        self.assertTrue(writer_reaped.is_file())
        self.assertFalse(unknown_signaled.exists())
        with self.assertRaises(ProcessLookupError):
            os.kill(int(forgot_pid.read_text(encoding="utf-8")), 0)

    def test_persistent_writer_recovery_deadline_failure_prevents_fork(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        forked = self.root / "unexpected-writer-fork"
        driver, writer_pid, writer_reaped = self.configured_observed_writer_driver(
            fixture,
            "persistent_deadline",
            TERMINAL_OUTPUT_DRIVER_SOURCE,
            {"scenario": "persistent-recovery-deadline", "forked": str(forked)},
        )

        result = fixture.invoke(
            str(writer_pid),
            str(writer_reaped),
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
        )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertFalse(forked.exists())
        self.assertFalse(writer_pid.exists())
        self.assertFalse(writer_reaped.exists())

    def test_writer_context_preparation_failure_prevents_fork(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        forked = self.root / "unexpected-writer-fork"
        driver, writer_pid, writer_reaped = self.configured_observed_writer_driver(
            fixture,
            "context_preparation",
            TERMINAL_OUTPUT_DRIVER_SOURCE,
            {"scenario": "context-preparation-failure", "forked": str(forked)},
        )

        result = fixture.invoke(
            str(writer_pid),
            str(writer_reaped),
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
        )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertFalse(forked.exists())
        self.assertFalse(writer_pid.exists())
        self.assertFalse(writer_reaped.exists())

    def test_writer_publishes_pid_before_the_first_parent_fallible_step(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        published = self.root / "writer-pid-published"
        driver, writer_pid, writer_reaped = self.configured_observed_writer_driver(
            fixture,
            "pid_publication",
            TERMINAL_OUTPUT_DRIVER_SOURCE,
            {"scenario": "pid-publication", "published": str(published)},
        )

        result = fixture.invoke(
            str(writer_pid),
            str(writer_reaped),
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertTrue(published.is_file())

    def test_writer_pid_publication_failure_wildcard_reaps_without_output(
        self,
    ) -> None:
        for phase in ("before-mutation", "after-pid-store"):
            with self.subTest(phase=phase):
                fixture = ValidInvocationFixture(self.root / phase)
                cleanup_elapsed_marker = fixture.root / "cleanup-elapsed"
                no_waitable_marker = fixture.root / "no-waitable-child"
                numeric_signal_marker = fixture.root / "numeric-signal"
                driver, writer_pid, writer_reaped = (
                    self.configured_observed_writer_driver(
                        fixture,
                        f"pid_publication_failure_{phase}",
                        TERMINAL_OUTPUT_DRIVER_SOURCE,
                        {
                            "scenario": "pid-publication-failure",
                            "phase": phase,
                            "cleanup_elapsed_marker": str(cleanup_elapsed_marker),
                            "no_waitable_marker": str(no_waitable_marker),
                            "numeric_signal_marker": str(numeric_signal_marker),
                        },
                    )
                )

                result = fixture.invoke(
                    str(writer_pid),
                    str(writer_reaped),
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                    driver=driver,
                    timeout=5,
                )

                self.assertEqual(result.returncode, 124, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, b"")
                self.assertTrue(writer_pid.is_file())
                self.assertTrue(writer_reaped.is_file())
                self.assertTrue(no_waitable_marker.is_file())
                self.assertFalse(numeric_signal_marker.exists())
                self.assertLess(
                    float(cleanup_elapsed_marker.read_text(encoding="utf-8")),
                    1.25,
                )

    def test_nonzero_writer_after_complete_write_never_proves_success(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        write_configured_driver(
            fixture.driver,
            TERMINAL_OUTPUT_DRIVER_SOURCE,
            {"scenario": "nonzero-writer"},
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
        )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, fixture.envelope_raw)
        self.assert_diagnostic(
            result.stderr,
            message="accepted output transport failed",
            validator_code_executed="yes",
            active_state_changed="unknown",
            current_receipt="sha256:" + fixture.receipt["content_sha256"][:12],
            next_action=(
                "discard any visible output; do not retry; repair the caller transport"
            ),
        )

    def test_reaped_writer_retains_the_first_context_fault_as_transport_cause(
        self,
    ) -> None:
        module = load_client_module(
            "task_witness_client_reaped_writer_context_fault_fixture"
        )
        first_fault = MemoryError("fixture first writer-context clock failure")

        def started(
            _descriptor: int,
            _raw: bytes,
            _invocation: object,
            context: object,
        ) -> bool:
            context.record(first_fault)
            context.publish_pid(42)
            return True

        def reaped(
            _pid: int,
            _deadline: float,
            _invocation: object,
            context: object,
        ) -> None:
            context.wait_status = 0
            context.wait_owned = True
            context.lifecycle.state = "reaped"

        with (
            mock.patch.object(module, "_canonical_client_process", return_value=True),
            mock.patch.object(
                module, "_terminal_writer_reap_is_safe", return_value=True
            ),
            mock.patch.object(module, "_start_terminal_writer", side_effect=started),
            mock.patch.object(module, "_wait_terminal_writer", side_effect=reaped),
            self.assertRaises(module.ClientError) as raised,
        ):
            module._write_terminal(1, b"accepted", module.InvocationState())

        self.assertEqual(raised.exception.exit_code, 124)
        self.assertEqual(str(raised.exception), "accepted output transport failed")
        self.assertIs(raised.exception.__cause__, first_fault)

    def test_full_open_stderr_cannot_delay_rejection_exit(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        budget_marker = self.root / "diagnostic-budget"
        write_configured_driver(
            fixture.driver,
            TERMINAL_OUTPUT_DRIVER_SOURCE,
            {"scenario": "full-stderr-budget", "budget_marker": str(budget_marker)},
        )
        read_descriptor, write_descriptor = os.pipe()
        original_flags = fcntl.fcntl(write_descriptor, fcntl.F_GETFL)
        fcntl.fcntl(
            write_descriptor,
            fcntl.F_SETFL,
            original_flags | os.O_NONBLOCK,
        )
        filled_bytes = 0
        try:
            while True:
                try:
                    filled_bytes += os.write(write_descriptor, b"x" * 65536)
                except BlockingIOError:
                    break
        finally:
            fcntl.fcntl(write_descriptor, fcntl.F_SETFL, original_flags)

        process = subprocess.Popen(
            fixture.command("invalid"),
            stdout=subprocess.PIPE,
            stderr=write_descriptor,
            env=CLIENT_ENVIRONMENT,
        )
        os.close(write_descriptor)
        forced_kill = False
        stdout = b""
        started = time.monotonic()
        try:
            try:
                stdout, _ = process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                forced_kill = True
                process.kill()
                stdout, _ = process.communicate(timeout=3)
        finally:
            if process.poll() is None:
                forced_kill = True
                process.kill()
                process.communicate(timeout=3)
            os.close(read_descriptor)

        elapsed = time.monotonic() - started
        self.assertGreater(filled_bytes, 0)
        self.assertFalse(forced_kill, "blocked diagnostic required SIGKILL cleanup")
        self.assertEqual(process.returncode, 64)
        self.assertEqual(stdout, b"")
        self.assertLess(elapsed, 0.8)
        self.assertEqual(float(budget_marker.read_text(encoding="utf-8")), 0.05)

    def test_incomplete_large_envelope_write_exposes_no_accepted_proof(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        fixture.replace_launcher_envelope(
            {
                **fixture.envelope,
                "witness": {
                    **fixture.witness,
                    "projection": {
                        "contract": "fixture-projection-v1",
                        "payload": "x" * (1024 * 1024),
                    },
                },
            }
        )
        read_descriptor, write_descriptor = os.pipe()
        try:
            process = subprocess.Popen(
                fixture.command(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                ),
                stdout=write_descriptor,
                stderr=subprocess.PIPE,
                env=CLIENT_ENVIRONMENT,
            )
        finally:
            os.close(write_descriptor)
        try:
            fragment = os.read(read_descriptor, 64 * 1024)
        finally:
            os.close(read_descriptor)
        _, stderr = process.communicate(timeout=5)

        self.assertTrue(fragment)
        self.assertTrue(fixture.envelope_raw.startswith(fragment))
        self.assertNotEqual(fragment, fixture.envelope_raw)
        self.assertEqual(process.returncode, 124, stderr.decode())
        self.assert_diagnostic(
            stderr,
            message="accepted output transport failed",
            validator_code_executed="yes",
            active_state_changed="unknown",
            current_receipt="sha256:" + fixture.receipt["content_sha256"][:12],
            next_action=(
                "discard any visible output; do not retry; repair the caller transport"
            ),
        )

    def test_sigterm_interrupts_accepted_output_to_a_full_open_pipe(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        accepted_gate_armed = self.root / "cancelled-output-gate-armed"
        driver, writer_pid, writer_reaped = self.configured_observed_writer_driver(
            fixture,
            "cancelled_output",
            TERMINAL_OUTPUT_DRIVER_SOURCE,
            {
                "scenario": "plain",
                "accepted_gate_armed_marker": str(accepted_gate_armed),
            },
        )
        read_descriptor, write_descriptor, filled_bytes = self.full_pipe()
        original_flags = fcntl.fcntl(write_descriptor, fcntl.F_GETFL)
        process = subprocess.Popen(
            fixture.command(
                str(writer_pid),
                str(writer_reaped),
                "validate",
                "--bundle",
                str(fixture.bundle),
                driver=driver,
            ),
            stdout=write_descriptor,
            stderr=subprocess.PIPE,
            env=CLIENT_ENVIRONMENT,
        )
        forced_kill = False
        stderr = b""
        final_flags = original_flags
        started = time.monotonic()
        try:
            deadline = time.monotonic() + 3
            while not accepted_gate_armed.exists() and process.poll() is None:
                if time.monotonic() >= deadline:
                    self.fail("client did not arm the accepted-output writer gate")
                time.sleep(0.01)
            self.assertIsNone(process.poll())
            self.assertTrue(
                writer_pid.is_file(), "client did not publish the writer PID"
            )
            fcntl.fcntl(
                write_descriptor,
                fcntl.F_SETFL,
                original_flags | os.O_APPEND,
            )
            started = time.monotonic()
            os.kill(process.pid, signal.SIGTERM)
            try:
                _, stderr = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                forced_kill = True
                process.kill()
                _, stderr = process.communicate(timeout=3)
        finally:
            if process.poll() is None:
                forced_kill = True
                process.kill()
                process.communicate(timeout=3)
            final_flags = fcntl.fcntl(write_descriptor, fcntl.F_GETFL)
            os.close(write_descriptor)
            os.close(read_descriptor)

        elapsed = time.monotonic() - started
        self.assertGreater(filled_bytes, 0)
        self.assertFalse(forced_kill, "blocked output required SIGKILL cleanup")
        self.assertEqual(process.returncode, 124, stderr.decode())
        self.assertLess(elapsed, 1.8)
        self.assert_diagnostic(
            stderr,
            message="validation interrupted",
            validator_code_executed="yes",
            active_state_changed="unknown",
            current_receipt="sha256:" + fixture.receipt["content_sha256"][:12],
            next_action=(
                "discard any visible output; do not retry; "
                "verify validator termination and active state"
            ),
        )
        self.assertTrue(writer_reaped.is_file(), "blocked writer was not reaped")
        self.assertFalse(final_flags & os.O_NONBLOCK)
        self.assertTrue(final_flags & os.O_APPEND)

    def test_pre_gate_failure_does_not_claim_output_may_be_visible(self) -> None:
        module = load_client_module("task_witness_client_pre_gate_guidance_fixture")
        invocation = module.InvocationState()
        error = module.ClientError(
            "accepted output transport failed",
            module.EXIT_RESOURCE,
        )

        diagnostic = module._diagnostic(error, invocation)

        self.assertIn(
            b"next_action=do not retry; repair the caller transport",
            diagnostic,
        )
        self.assertNotIn(b"discard any visible output", diagnostic)

    def test_output_deadline_kills_and_reaps_a_blocked_writer(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        fixture.accepted_output_deadline_seconds = 0.25
        fixture._write_deployment_receipt()
        driver, writer_pid, writer_reaped = self.configured_observed_writer_driver(
            fixture,
            "timed_out_output",
            TERMINAL_OUTPUT_DRIVER_SOURCE,
            {"scenario": "output-deadline"},
        )
        read_descriptor, write_descriptor, filled_bytes = self.full_pipe()
        started = time.monotonic()
        try:
            process = subprocess.run(
                fixture.command(
                    str(writer_pid),
                    str(writer_reaped),
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                    driver=driver,
                ),
                stdout=write_descriptor,
                stderr=subprocess.PIPE,
                check=False,
                env=CLIENT_ENVIRONMENT,
                timeout=3,
            )
        finally:
            os.close(write_descriptor)
            os.close(read_descriptor)

        elapsed = time.monotonic() - started
        self.assertGreater(filled_bytes, 0)
        self.assertEqual(process.returncode, 124, process.stderr.decode())
        self.assertLess(elapsed, 1.5)
        self.assertTrue(writer_pid.is_file(), "client did not fork output writer")
        self.assertTrue(writer_reaped.is_file(), "timed-out writer was not reaped")
        self.assert_diagnostic(
            process.stderr,
            message="accepted output transport failed",
            validator_code_executed="yes",
            active_state_changed="unknown",
            current_receipt="sha256:" + fixture.receipt["content_sha256"][:12],
            next_action=(
                "discard any visible output; do not retry; repair the caller transport"
            ),
        )

    def test_consume_retries_one_deadline_fault_but_bounds_persistent_faults(
        self,
    ) -> None:
        for persistent in (False, True):
            with self.subTest(persistent=persistent):
                module = load_client_module(f"task_witness_client_consume_{persistent}")
                child = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                )
                context = self.cleanup_context(module, child.pid)
                original_monotonic = time.monotonic
                calls = 0

                def fail_deadline(
                    *,
                    persistent: bool = persistent,
                    original_monotonic: Callable[[], float] = original_monotonic,
                ) -> float:
                    nonlocal calls
                    calls += 1
                    if persistent or calls == 1:
                        raise MemoryError("fixture cleanup deadline allocation")
                    return original_monotonic()

                started = original_monotonic()
                try:
                    with mock.patch.object(
                        module.time,
                        "monotonic",
                        side_effect=fail_deadline,
                    ):
                        cleanup = module._consume(child.pid, context)
                    self.assertEqual(cleanup.completed, not persistent)
                    self.assertIsInstance(cleanup.error, MemoryError)
                    self.assertLess(original_monotonic() - started, 1)
                    self.assertGreaterEqual(calls, 2)
                    if persistent:
                        self.assertEqual(calls, 2)
                    self.assertEqual(context.lifecycle.responsible, persistent)
                    if persistent:
                        os.kill(child.pid, 0)
                    else:
                        with self.assertRaises(ChildProcessError):
                            os.waitpid(child.pid, os.WNOHANG)
                        child.returncode = -signal.SIGKILL
                finally:
                    if context.lifecycle.responsible:
                        os.kill(child.pid, signal.SIGKILL)
                        child.wait(timeout=3)

    def test_writer_wait_memory_error_requires_exact_reobservation_before_signal(
        self,
    ) -> None:
        module = load_client_module("task_witness_client_writer_wait_ambiguity_fixture")
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
        )
        context = self.cleanup_context(module, child.pid)
        original_waitpid = os.waitpid
        original_kill = os.kill
        actions: list[str] = []
        signal_calls: list[tuple[int, signal.Signals]] = []

        def ambiguous_wait(pid: int, options: int) -> tuple[int, int]:
            self.assertEqual(pid, child.pid)
            actions.append("wait" if len(actions) < 2 else "exact-wait")
            if len(actions) == 1:
                return 0, 0
            if len(actions) == 2:
                raise MemoryError("fixture WNOHANG allocation failure")
            waited, status = original_waitpid(pid, options)
            if waited == pid:
                child._handle_exitstatus(status)
            return waited, status

        def observed_kill(pid: int, number: signal.Signals) -> None:
            actions.append("signal")
            signal_calls.append((pid, number))
            original_kill(pid, number)

        try:
            with (
                mock.patch.object(module.os, "waitpid", side_effect=ambiguous_wait),
                mock.patch.object(module.os, "kill", side_effect=observed_kill),
            ):
                module._wait_terminal_writer(
                    child.pid,
                    time.monotonic() + 0.5,
                    None,
                    context,
                )
                status = context.wait_status
                writer_owned = context.wait_owned
                wait_error = context.wait_error

                self.assertIsNone(status)
                self.assertTrue(writer_owned)
                self.assertIsInstance(wait_error, MemoryError)
                self.assertEqual(context.lifecycle.state, "ambiguous")
                self.assertEqual(signal_calls, [])

                cleanup = module._consume(child.pid, context)
                self.assertTrue(cleanup.completed)
                self.assertIsNone(cleanup.error)

            self.assertEqual(actions[:3], ["wait", "wait", "exact-wait"])
            self.assertGreater(actions.index("signal"), actions.index("exact-wait"))
            self.assertEqual(signal_calls, [(child.pid, signal.SIGKILL)])
            self.assertEqual(context.lifecycle.state, "reaped")
            with self.assertRaises(ChildProcessError):
                original_waitpid(child.pid, os.WNOHANG)
        finally:
            if context.lifecycle.responsible:
                try:
                    original_kill(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    original_waitpid(child.pid, 0)
                except ChildProcessError:
                    pass

    def test_writer_wait_post_sleep_profile_fault_is_consumed_and_reaped(
        self,
    ) -> None:
        module = load_client_module("task_witness_client_writer_sleep_fixture")
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
        )
        context = self.cleanup_context(module, child.pid)
        original_waitpid = os.waitpid
        callback_error = MemoryError("fixture writer post-sleep profile failure")
        profile_faulted = False

        def fail_after_sleep(_frame, event, argument):
            nonlocal profile_faulted
            if not profile_faulted and event == "c_return" and argument is time.sleep:
                profile_faulted = True
                sys.setprofile(None)
                raise callback_error
            return fail_after_sleep

        try:
            sys.setprofile(fail_after_sleep)
            module._wait_terminal_writer(
                child.pid,
                time.monotonic() + 0.5,
                None,
                context,
            )
            self.assertTrue(profile_faulted)
            self.assertIsNone(context.wait_status)
            self.assertTrue(context.wait_owned)
            self.assertIs(context.wait_error, callback_error)

            cleanup = module._consume(child.pid, context)
            self.assertTrue(cleanup.completed)
            self.assertIsNone(cleanup.error)
            self.assertEqual(context.lifecycle.state, "reaped")
            with self.assertRaises(ChildProcessError):
                original_waitpid(child.pid, os.WNOHANG)
            child.returncode = -signal.SIGKILL
            self.assertEqual(child.returncode, -signal.SIGKILL)
        finally:
            sys.setprofile(None)
            if context.lifecycle.responsible:
                try:
                    os.kill(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    original_waitpid(child.pid, 0)
                except ChildProcessError:
                    pass

    def test_writer_exact_wait_failure_is_retained_after_retry_reaps(
        self,
    ) -> None:
        module = load_client_module(
            "task_witness_client_writer_exact_wait_retry_fixture"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
        )
        context = self.cleanup_context(module, child.pid)
        original_waitpid = os.waitpid
        original_kill = os.kill
        actions: list[str] = []
        signal_calls: list[tuple[int, signal.Signals]] = []
        injected = False

        def failing_first_wait(pid: int, options: int) -> tuple[int, int]:
            nonlocal injected
            self.assertEqual(pid, child.pid)
            actions.append("wait")
            if not injected:
                injected = True
                raise OSError(errno.EIO, "fixture exact wait refused")
            waited, status = original_waitpid(pid, options)
            if waited == pid:
                child._handle_exitstatus(status)
            return waited, status

        def observed_kill(pid: int, number: signal.Signals) -> None:
            actions.append("signal")
            signal_calls.append((pid, number))
            original_kill(pid, number)

        try:
            with (
                mock.patch.object(module.os, "waitpid", side_effect=failing_first_wait),
                mock.patch.object(module.os, "kill", side_effect=observed_kill),
            ):
                cleanup = module._consume(child.pid, context)
                self.assertTrue(cleanup.completed)
                self.assertIsInstance(cleanup.error, OSError)

            self.assertEqual(actions[:3], ["signal", "wait", "wait"])
            self.assertEqual(signal_calls, [(child.pid, signal.SIGKILL)])
            self.assertEqual(context.lifecycle.state, "reaped")
            with self.assertRaises(ChildProcessError):
                original_waitpid(child.pid, os.WNOHANG)
            self.assertEqual(child.returncode, -signal.SIGKILL)
        finally:
            if context.lifecycle.responsible:
                try:
                    original_kill(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    original_waitpid(child.pid, 0)
                except ChildProcessError:
                    pass

    def test_writer_persistent_signal_failure_is_retried_once_and_bounded(
        self,
    ) -> None:
        module = load_client_module("task_witness_client_writer_signal_retry_fixture")
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
        )
        original_kill = os.kill
        signal_calls: list[tuple[int, signal.Signals]] = []
        original_kill_reap_seconds = module.PROCESS_PROFILE["kill_reap_seconds"]
        module.PROCESS_PROFILE["kill_reap_seconds"] = 0.05
        context = self.cleanup_context(module, child.pid)

        def failed_kill(pid: int, number: signal.Signals) -> None:
            self.assertEqual(pid, child.pid)
            signal_calls.append((pid, number))
            raise OSError(errno.EIO, "fixture persistent kill failure")

        started = time.monotonic()
        try:
            with mock.patch.object(module.os, "kill", side_effect=failed_kill):
                cleanup = module._consume(child.pid, context)
                self.assertFalse(cleanup.completed)
                self.assertIsInstance(cleanup.error, OSError)

            self.assertLess(time.monotonic() - started, 1)
            self.assertEqual(
                signal_calls,
                [(child.pid, signal.SIGKILL), (child.pid, signal.SIGKILL)],
            )
            self.assertTrue(context.lifecycle.responsible)
        finally:
            module.PROCESS_PROFILE["kill_reap_seconds"] = original_kill_reap_seconds
            if context.lifecycle.responsible:
                try:
                    original_kill(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    waited, status = os.waitpid(child.pid, 0)
                except ChildProcessError:
                    pass
                else:
                    self.assertEqual(waited, child.pid)
                    child._handle_exitstatus(status)
                    self.assertEqual(child.returncode, -signal.SIGKILL)

    def test_accepted_output_budget_excludes_preflight_and_includes_handoff(
        self,
    ) -> None:
        for phase in ("preflight", "handoff"):
            with self.subTest(phase=phase):
                fixture = ValidInvocationFixture(self.root / phase)
                fixture.accepted_output_deadline_seconds = 0.05
                fixture._write_deployment_receipt()
                driver, writer_pid, writer_reaped = (
                    self.configured_observed_writer_driver(
                        fixture,
                        f"accepted_budget_{phase}",
                        TERMINAL_OUTPUT_DRIVER_SOURCE,
                        {"scenario": "accepted-output-budget", "phase": phase},
                    )
                )

                result = fixture.invoke(
                    str(writer_pid),
                    str(writer_reaped),
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                    driver=driver,
                    timeout=5,
                )

                self.assertEqual(
                    result.returncode,
                    0 if phase == "preflight" else 124,
                    result.stderr.decode(),
                )
                self.assertEqual(
                    result.stdout,
                    fixture.envelope_raw if phase == "preflight" else b"",
                )
                self.assertTrue(writer_reaped.is_file())

    def test_cancellation_after_accepted_output_prevents_exit_zero(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        driver = self.root / "post_output_cancellation_driver.py"
        write_configured_driver(
            driver,
            TERMINAL_OUTPUT_DRIVER_SOURCE,
            {"scenario": "cancellation-after-output"},
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
            timeout=5,
        )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, fixture.envelope_raw)
        self.assert_diagnostic(
            result.stderr,
            message="validation interrupted",
            validator_code_executed="yes",
            active_state_changed="unknown",
            current_receipt="sha256:" + fixture.receipt["content_sha256"][:12],
            next_action=(
                "discard any visible output; do not retry; "
                "verify validator termination and active state"
            ),
        )

    def test_post_fork_success_handoff_failures_reap_the_writer(
        self,
    ) -> None:
        for phase in ("before-fork", "cancellation", "memory"):
            with self.subTest(phase=phase):
                fixture = ValidInvocationFixture(self.root / phase)
                driver, writer_pid, writer_reaped = (
                    self.configured_observed_writer_driver(
                        fixture,
                        f"post_fork_{phase}",
                        TERMINAL_OUTPUT_DRIVER_SOURCE,
                        {"scenario": "post-fork-handoff", "phase": phase},
                    )
                )
                read_descriptor, write_descriptor, filled_bytes = self.full_pipe()
                try:
                    result = subprocess.run(
                        fixture.command(
                            str(writer_pid),
                            str(writer_reaped),
                            "validate",
                            "--bundle",
                            str(fixture.bundle),
                            driver=driver,
                        ),
                        stdout=write_descriptor,
                        stderr=subprocess.PIPE,
                        check=False,
                        env=CLIENT_ENVIRONMENT,
                        timeout=5,
                    )
                finally:
                    os.close(write_descriptor)
                    os.close(read_descriptor)

                self.assertGreater(filled_bytes, 0)
                self.assertEqual(result.returncode, 124, result.stderr.decode())
                self.assertNotIn(b"sensitive", result.stderr)
                if phase != "before-fork":
                    self.assertTrue(writer_reaped.is_file(), "writer was not reaped")
                message = (
                    "client resources are unavailable"
                    if phase == "memory"
                    else "validation interrupted"
                )
                observed_message, _ = parse_diagnostic(result.stderr)
                self.assertEqual(
                    observed_message,
                    f"task witness client rejected: {message}",
                )
                if phase == "cancellation":
                    self.assertNotIn(b"discard any visible output", result.stderr)

    def test_known_writer_is_cleaned_across_post_fork_allocation_gaps(self) -> None:
        for phase in ("deadline", "gate", "return", "before-wait"):
            with self.subTest(phase=phase):
                root = self.root / phase
                root.mkdir()
                writer_pid = root / "writer-pid"
                writer_reaped = root / "writer-reaped"
                driver = Path(__file__).with_name("_writer_guard_driver.py")
                read_descriptor, write_descriptor, filled_bytes = self.full_pipe()
                try:
                    result = subprocess.run(
                        [
                            sys.executable,
                            "-B",
                            "-I",
                            "-S",
                            str(driver),
                            str(CLIENT_SOURCE),
                            str(writer_pid),
                            str(writer_reaped),
                            phase,
                        ],
                        stdout=write_descriptor,
                        stderr=subprocess.PIPE,
                        check=False,
                        env=CLIENT_ENVIRONMENT,
                        timeout=5,
                    )
                finally:
                    os.close(write_descriptor)
                    os.close(read_descriptor)

                self.assertGreater(filled_bytes, 0)
                self.assertEqual(result.returncode, 124, result.stderr.decode())
                self.assertEqual(result.stderr, b"")
                self.assertTrue(writer_reaped.is_file(), "writer was not reaped")
                pid = int(writer_pid.read_text(encoding="utf-8"))
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)

    def test_writer_gate_false_releases_no_accepted_output_or_visibility_claim(
        self,
    ) -> None:
        writer_pid = self.root / "writer-gate-pid"
        writer_reaped = self.root / "writer-gate-reaped"
        driver = Path(__file__).with_name("_writer_guard_driver.py")
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-I",
                "-S",
                str(driver),
                str(CLIENT_SOURCE),
                str(writer_pid),
                str(writer_reaped),
                "gate-false",
            ],
            capture_output=True,
            check=False,
            env=CLIENT_ENVIRONMENT,
            timeout=5,
        )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assert_diagnostic(
            result.stderr,
            message="terminal writer gate failed",
            validator_code_executed="no",
            active_state_changed="no",
            current_receipt="unknown",
            next_action="do not retry; verify validator termination and active state",
        )
        self.assertTrue(writer_reaped.is_file(), "writer was not reaped")
        with self.assertRaises(ProcessLookupError):
            os.kill(int(writer_pid.read_text(encoding="utf-8")), 0)

    def test_wait_failure_after_writer_reap_does_not_kill_its_pid(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        killed_after_reap = self.root / "killed-after-reap"
        failure_injected = self.root / "failure-injected"
        driver, writer_pid, writer_reaped = self.configured_observed_writer_driver(
            fixture,
            "post_reap_memory",
            TERMINAL_OUTPUT_DRIVER_SOURCE,
            {
                "scenario": "post-reap-memory",
                "killed_after_reap": str(killed_after_reap),
                "failure_injected": str(failure_injected),
            },
        )

        result = fixture.invoke(
            str(writer_pid),
            str(writer_reaped),
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
            timeout=5,
        )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertTrue(writer_reaped.is_file(), "writer was not reaped")
        self.assertTrue(failure_injected.is_file())
        self.assertFalse(killed_after_reap.exists())
        with self.assertRaises(ProcessLookupError):
            os.kill(int(writer_pid.read_text(encoding="utf-8")), 0)

    def test_exact_wait_echild_prevents_later_signal_to_writer_pid(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        echild_injected = self.root / "echild-injected"
        killed_after_echild = self.root / "killed-after-echild"
        driver, writer_pid, writer_reaped = self.configured_observed_writer_driver(
            fixture,
            "post_reap_echild",
            TERMINAL_OUTPUT_DRIVER_SOURCE,
            {
                "scenario": "post-reap-echild",
                "echild_injected": str(echild_injected),
                "killed_after_echild": str(killed_after_echild),
            },
        )

        result = fixture.invoke(
            str(writer_pid),
            str(writer_reaped),
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
            timeout=5,
        )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertTrue(echild_injected.is_file())
        self.assertTrue(writer_reaped.is_file())
        self.assertFalse(killed_after_echild.exists())
        with self.assertRaises(ProcessLookupError):
            os.kill(int(writer_pid.read_text(encoding="utf-8")), 0)

    def test_late_profile_drift_and_allocation_failures_are_classified(self) -> None:
        phases = (
            "SIGTERM",
            "SIGCHLD",
            "selector-memory",
            "bootstrap-memory",
            "diagnostic-memory",
            "finish-memory",
            "finish-runtime",
            "exception-text-memory",
        )
        for phase in phases:
            with self.subTest(phase=phase):
                fixture = ValidInvocationFixture(self.root / phase)
                accepted_writer_attempted = fixture.root / "accepted-writer-attempted"
                write_configured_driver(
                    fixture.driver,
                    TERMINAL_OUTPUT_DRIVER_SOURCE,
                    {
                        "scenario": "late-failure",
                        "phase": phase,
                        "accepted_writer_attempted": str(accepted_writer_attempted),
                    },
                )

                result = fixture.invoke(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                    timeout=5,
                )

                expected_returncode = (
                    70
                    if phase
                    in {"SIGTERM", "SIGCHLD", "finish-runtime", "exception-text-memory"}
                    else 124
                )
                self.assertEqual(
                    result.returncode,
                    expected_returncode,
                    result.stderr.decode(),
                )
                expected_stdout = (
                    fixture.envelope_raw
                    if phase in {"finish-memory", "finish-runtime"}
                    else b""
                )
                self.assertEqual(result.stdout, expected_stdout)
                if phase in {"SIGTERM", "SIGCHLD"}:
                    self.assertFalse(accepted_writer_attempted.exists())
                self.assertNotIn(b"sensitive", result.stderr)
                self.assertNotIn(b"Traceback", result.stderr)
                if phase in {
                    "bootstrap-memory",
                    "SIGCHLD",
                    "diagnostic-memory",
                }:
                    self.assertEqual(result.stderr, b"")
                elif result.stderr:
                    self.assertIn(b"task witness client rejected:", result.stderr)
                if phase == "finish-memory" and result.stderr:
                    self.assert_diagnostic(
                        result.stderr,
                        message="client resources are unavailable",
                        validator_code_executed="yes",
                        active_state_changed="unknown",
                        current_receipt=(
                            "sha256:" + fixture.receipt["content_sha256"][:12]
                        ),
                        next_action=(
                            "discard any visible output; do not retry; "
                            "verify validator termination and active state"
                        ),
                    )
                if phase == "finish-runtime" and result.stderr:
                    self.assert_diagnostic(
                        result.stderr,
                        message="client installation validation failed",
                        validator_code_executed="yes",
                        active_state_changed="unknown",
                        current_receipt=(
                            "sha256:" + fixture.receipt["content_sha256"][:12]
                        ),
                        next_action=(
                            "discard any visible output; do not retry; "
                            "verify validator termination and active state"
                        ),
                    )

    def test_success_restores_the_original_cancellation_disposition(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        restored_marker = self.root / "restored-signal-handler"
        driver = self.root / "restored_signal_handler_driver.py"
        write_configured_driver(
            driver,
            TERMINAL_OUTPUT_DRIVER_SOURCE,
            {
                "scenario": "restore-signal-handler",
                "marker": str(restored_marker),
            },
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
            timeout=5,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, fixture.envelope_raw)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(restored_marker.read_text(encoding="utf-8"), "restored")
