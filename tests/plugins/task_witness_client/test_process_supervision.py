from __future__ import annotations

import contextlib
import errno
import io
import os
import signal
import subprocess
import sys
import time
import types
from unittest import mock

from ._support import (
    CLIENT_ENVIRONMENT,
    PROCESS_SUPERVISION_DRIVER_SOURCE,
    ValidInvocationFixture,
    _TaskWitnessClientTestCase,
    install_launcher_behavior,
    load_client_module,
    write_configured_driver,
)


class ProcessSupervisionTests(_TaskWitnessClientTestCase):
    def cleanup_context(self, module: object, pid: int | None = None) -> object:
        context = module._prepare_cleanup_context()
        if pid is not None:
            context.publish_pid(pid)
        return context

    def test_missing_or_invalid_lifecycle_never_authorizes_signals(self) -> None:
        module = load_client_module(
            "task_witness_client_invalid_lifecycle_authority_fixture"
        )

        class MissingLifecycle:
            pass

        class InvalidLifecycle:
            lifecycle = object()
            owned = True

        for target in (MissingLifecycle(), InvalidLifecycle()):
            with self.subTest(target=type(target).__name__, state="initial"):
                self.assertFalse(module._responsible_for_child(target))
                self.assertFalse(module._may_signal_child(target))

            module._mark_child_owned(target)

            with self.subTest(target=type(target).__name__, state="marked-owned"):
                self.assertFalse(module._responsible_for_child(target))
                self.assertFalse(module._may_signal_child(target))

    def test_cleanup_deadline_arming_is_once_only_for_graceful_and_forced_work(
        self,
    ) -> None:
        module = load_client_module("task_witness_client_deadline_arming_fixture")

        for graceful, expected_deadline, expected_grace_cutoff in (
            (False, 11.0, 10.0),
            (True, 13.0, 12.0),
        ):
            with self.subTest(graceful=graceful, state="armed"):
                context = module._prepare_cleanup_context()
                context.termination_grace_seconds = 2.0
                context.kill_reap_seconds = 1.0
                monotonic = mock.Mock(return_value=10.0)
                with mock.patch.object(module.time, "monotonic", monotonic):
                    self.assertTrue(context.arm_cleanup(graceful=graceful))
                    self.assertTrue(context.arm_cleanup(graceful=not graceful))
                self.assertEqual(context.cleanup_deadline, expected_deadline)
                self.assertEqual(context.grace_cutoff, expected_grace_cutoff)
                self.assertEqual(monotonic.call_count, 1)

            with self.subTest(graceful=graceful, state="unavailable"):
                context = module._prepare_cleanup_context()
                monotonic = mock.Mock(
                    side_effect=(MemoryError("first"), MemoryError("second"))
                )
                with mock.patch.object(module.time, "monotonic", monotonic):
                    self.assertFalse(context.arm_cleanup(graceful=graceful))
                    self.assertFalse(context.arm_cleanup(graceful=not graceful))
                self.assertEqual(monotonic.call_count, 2)

    def test_lost_pid_deadline_arming_remains_independent_from_cleanup(self) -> None:
        module = load_client_module("task_witness_client_fork_deadline_arming_fixture")
        context = module._prepare_cleanup_context()
        monotonic = mock.Mock(return_value=10.0)

        with mock.patch.object(module.time, "monotonic", monotonic):
            self.assertTrue(context.arm_fork_deadlines())
            self.assertTrue(context.arm_fork_deadlines())

        self.assertEqual(
            context.lost_pid_deadline,
            10.0 + module.PROCESS_PROFILE["kill_reap_seconds"],
        )
        self.assertFalse(context.cleanup_deadline_armed)
        self.assertEqual(monotonic.call_count, 1)

    def test_group_cleanup_shares_one_deadline_across_grace_force_probe_and_reap(
        self,
    ) -> None:
        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform):
                module = load_client_module(
                    f"task_witness_client_shared_cleanup_deadline_{platform}_fixture"
                )
                context = self.cleanup_context(module, 42)
                context.termination_grace_seconds = 2.0
                context.kill_reap_seconds = 1.0
                observed: list[tuple[str, float]] = []

                class Process:
                    pid = 42
                    returncode = None
                    lifecycle = context.lifecycle

                process = Process()

                def remaining(
                    deadline: float,
                    _context: object,
                    _retry_slot: int,
                ) -> float:
                    observed.append(("remaining", deadline))
                    return 0.0 if deadline == 12.0 else 1.0

                def signal_process(
                    _operation: object,
                    _pid: int,
                    number: int,
                    *,
                    deadline: float,
                    **_keywords: object,
                ) -> None:
                    observed.append((f"signal-{number}", deadline))

                def reap(
                    target: object,
                    deadline: float,
                    *,
                    context: object,
                ) -> None:
                    del context
                    observed.append(("reap", deadline))
                    module._mark_child_reaped(target)
                    target.returncode = -signal.SIGKILL

                patches = [
                    mock.patch.object(module.sys, "platform", platform),
                    mock.patch.object(module.time, "monotonic", return_value=10.0),
                    mock.patch.object(
                        module,
                        "_remaining_with_retry",
                        side_effect=remaining,
                    ),
                    mock.patch.object(module, "_sleep_with_retry", return_value=True),
                    mock.patch.object(
                        module,
                        "_signal_with_retry",
                        side_effect=signal_process,
                    ),
                    mock.patch.object(
                        module,
                        "_reap_owned_process",
                        side_effect=reap,
                    ),
                ]
                if platform == "linux":

                    def kill_group(
                        _process: object,
                        *,
                        deadline: float,
                        context: object,
                    ) -> tuple[None, bool]:
                        del context
                        observed.append(("group-kill", deadline))
                        return None, True

                    def linux_group_state(
                        _process: object,
                        deadline: float,
                        _context: object,
                        **_keywords: object,
                    ) -> str:
                        observed.append(("linux-probe", deadline))
                        return "quiescent"

                    patches.extend(
                        (
                            mock.patch.object(
                                module,
                                "_signal_process_group_kill_with_retry",
                                side_effect=kill_group,
                            ),
                            mock.patch.object(
                                module,
                                "_non_darwin_group_state_with_retry",
                                side_effect=linux_group_state,
                            ),
                        )
                    )
                else:
                    darwin_states = iter(("live", "live", "quiescent"))

                    def darwin_group_state(
                        _process: object,
                        deadline: float,
                        _context: object,
                        **_keywords: object,
                    ) -> str:
                        observed.append(("darwin-probe", deadline))
                        return next(darwin_states)

                    patches.append(
                        mock.patch.object(
                            module,
                            "_darwin_group_state_with_retry",
                            side_effect=darwin_group_state,
                        )
                    )

                with contextlib.ExitStack() as stack:
                    for patch in patches:
                        stack.enter_context(patch)
                    module._finalize_process_group(
                        process,
                        graceful=True,
                        context=context,
                    )

                self.assertEqual(context.grace_cutoff, 12.0)
                self.assertEqual(context.cleanup_deadline, 13.0)
                self.assertIn((f"signal-{signal.SIGTERM}", 12.0), observed)
                self.assertIn(("remaining", 12.0), observed)
                if platform == "linux":
                    self.assertIn(("group-kill", 13.0), observed)
                    self.assertIn(("linux-probe", 13.0), observed)
                else:
                    self.assertIn((f"signal-{signal.SIGKILL}", 13.0), observed)
                    self.assertEqual(
                        [
                            deadline
                            for operation, deadline in observed
                            if operation == "darwin-probe"
                        ],
                        [12.0, 13.0, 13.0],
                    )
                self.assertIn(("remaining", 13.0), observed)
                self.assertIn(("reap", 13.0), observed)

    def test_cleanup_deadline_failure_blocks_platform_cleanup_work(self) -> None:
        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform):
                module = load_client_module(
                    f"task_witness_client_cleanup_deadline_fault_{platform}_fixture"
                )
                context = self.cleanup_context(module, 42)

                class Process:
                    pid = 42
                    returncode = None
                    lifecycle = context.lifecycle

                first_error = MemoryError("first cleanup clock fault")
                with (
                    mock.patch.object(module.sys, "platform", platform),
                    mock.patch.object(
                        module.time,
                        "monotonic",
                        side_effect=(
                            first_error,
                            MemoryError("second cleanup clock fault"),
                        ),
                    ) as monotonic,
                    mock.patch.object(module.os, "killpg") as group_signal,
                    mock.patch.object(module.os, "kill") as direct_signal,
                    mock.patch.object(
                        module,
                        "_non_darwin_group_state_with_retry",
                    ) as linux_probe,
                    mock.patch.object(
                        module,
                        "_darwin_group_state_with_retry",
                    ) as darwin_probe,
                    mock.patch.object(module, "_reap_owned_process") as reap,
                    self.assertRaisesRegex(
                        module.ClientError,
                        "validation process group cleanup failed",
                    ) as raised,
                ):
                    module._finalize_process_group(
                        Process(),
                        graceful=True,
                        context=context,
                    )

                self.assertIs(raised.exception.__cause__, first_error)
                self.assertEqual(monotonic.call_count, 2)
                group_signal.assert_not_called()
                direct_signal.assert_not_called()
                linux_probe.assert_not_called()
                darwin_probe.assert_not_called()
                reap.assert_not_called()

    def test_direct_child_consume_gets_a_distinct_forced_cleanup_event(self) -> None:
        module = load_client_module(
            "task_witness_client_direct_consume_deadline_fixture"
        )
        context = module._prepare_cleanup_context(writer_seconds=60.0)
        with mock.patch.object(module.time, "monotonic", return_value=10.0):
            self.assertTrue(context.arm_fork_deadlines())
        context.publish_pid(42)
        signaled_deadlines: list[float] = []

        def signal_child(
            _operation: object,
            _pid: int,
            _number: int,
            *,
            deadline: float,
            **_keywords: object,
        ) -> None:
            signaled_deadlines.append(deadline)

        def reap_writer(
            _pid: int,
            _deadline: float,
            _invocation: object,
            cleanup_context: object,
        ) -> None:
            cleanup_context.wait_status = 0
            cleanup_context.wait_owned = False
            module._mark_child_reaped(cleanup_context)

        with (
            mock.patch.object(module.time, "monotonic", return_value=20.0),
            mock.patch.object(
                module,
                "_remaining_with_retry",
                return_value=1.0,
            ),
            mock.patch.object(
                module,
                "_signal_with_retry",
                side_effect=signal_child,
            ),
            mock.patch.object(
                module,
                "_wait_terminal_writer",
                side_effect=reap_writer,
            ),
        ):
            cleanup = module._consume(42, context)

        self.assertTrue(cleanup.completed)
        self.assertEqual(context.writer_deadline, 70.0)
        self.assertEqual(context.lost_pid_deadline, 11.0)
        self.assertEqual(context.grace_cutoff, 20.0)
        self.assertEqual(context.cleanup_deadline, 21.0)
        self.assertEqual(signaled_deadlines, [21.0])

    def test_group_signal_observation_fault_recovers_before_exact_reap(self) -> None:
        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform):
                module = load_client_module(
                    f"task_witness_client_group_observation_recovery_{platform}"
                )

                class Process:
                    pid = 42
                    returncode = None

                    def __init__(self) -> None:
                        self.lifecycle = module._ChildLifecycle()
                        self.wait_count = 0

                    def wait(self, *, deadline: float) -> int:
                        del deadline
                        self.wait_count += 1
                        module._mark_child_reaped(self)
                        self.returncode = -signal.SIGKILL
                        return self.returncode

                process = Process()
                context = self.cleanup_context(module)
                refusal = PermissionError(errno.EPERM, "fixture group refusal")
                group_kill = mock.Mock(side_effect=refusal)
                waitid = mock.Mock(
                    side_effect=(
                        MemoryError("fixture observation allocation"),
                        None,
                        None,
                    )
                )
                group_state = mock.patch.object(
                    module,
                    (
                        "_darwin_wait_for_process_group_state"
                        if platform == "darwin"
                        else "_non_darwin_group_state_with_retry"
                    ),
                    side_effect=("live", "quiescent")
                    if platform == "darwin"
                    else ("quiescent",),
                )
                with (
                    mock.patch.object(module.sys, "platform", platform),
                    mock.patch.object(module.os, "killpg", group_kill),
                    mock.patch.object(module.os, "waitid", waitid),
                    mock.patch.object(module.os, "kill") as direct_kill,
                    group_state,
                    self.assertRaises(module.ClientError) as raised,
                ):
                    module._finalize_process_group(
                        process,
                        graceful=False,
                        context=context,
                    )

                self.assertIs(raised.exception.__cause__, refusal)
                self.assertEqual(group_kill.call_count, 2)
                self.assertEqual(waitid.call_count, 3)
                direct_kill.assert_called_once_with(42, signal.SIGKILL)
                self.assertEqual(process.wait_count, 0 if platform == "linux" else 1)
                self.assertEqual(
                    process.lifecycle.state,
                    "owned" if platform == "linux" else "reaped",
                )

    def test_persistent_group_observation_failure_is_bounded_and_ambiguous(
        self,
    ) -> None:
        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform):
                module = load_client_module(
                    f"task_witness_client_group_observation_persistent_{platform}"
                )

                class Process:
                    pid = 42
                    returncode = None

                    def __init__(self) -> None:
                        self.lifecycle = module._ChildLifecycle()
                        self.wait_count = 0

                    def wait(self, *, deadline: float) -> int:
                        del deadline
                        self.wait_count += 1
                        module._mark_child_ambiguous(self)
                        raise MemoryError("fixture exact observation allocation")

                process = Process()
                context = self.cleanup_context(module)
                refusal = PermissionError(errno.EPERM, "fixture group refusal")
                group_kill = mock.Mock(side_effect=refusal)
                waitid = mock.Mock(
                    side_effect=(
                        MemoryError("fixture first observation allocation"),
                        MemoryError("fixture second observation allocation"),
                    )
                )
                group_state = mock.patch.object(
                    module,
                    (
                        "_darwin_wait_for_process_group_state"
                        if platform == "darwin"
                        else "_non_darwin_group_state_with_retry"
                    ),
                    side_effect=(
                        "live",
                        MemoryError("fixture first state allocation"),
                        MemoryError("fixture second state allocation"),
                    )
                    if platform == "darwin"
                    else (None,),
                )
                with (
                    mock.patch.object(module.sys, "platform", platform),
                    mock.patch.object(module.os, "killpg", group_kill),
                    mock.patch.object(module.os, "waitid", waitid),
                    mock.patch.object(module.os, "kill") as direct_kill,
                    group_state,
                    self.assertRaises(module.ClientError) as raised,
                ):
                    module._finalize_process_group(
                        process,
                        graceful=False,
                        context=context,
                    )

                self.assertIs(raised.exception.__cause__, refusal)
                self.assertEqual(group_kill.call_count, 1)
                self.assertEqual(waitid.call_count, 2)
                direct_kill.assert_not_called()
                self.assertEqual(process.lifecycle.state, "ambiguous")
                self.assertLessEqual(process.wait_count, 2)

    def test_group_observation_echild_forbids_all_later_numeric_signals(
        self,
    ) -> None:
        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform):
                module = load_client_module(
                    f"task_witness_client_group_observation_echild_{platform}"
                )

                class Process:
                    pid = 42
                    returncode = None

                    def __init__(self) -> None:
                        self.lifecycle = module._ChildLifecycle()

                    def wait(self, *, deadline: float) -> int:
                        raise AssertionError(f"lost child reaped through {deadline}")

                process = Process()
                context = self.cleanup_context(module)
                refusal = PermissionError(errno.EPERM, "fixture group refusal")
                group_state = (
                    mock.patch.object(
                        module,
                        "_darwin_wait_for_process_group_state",
                        return_value="live",
                    )
                    if platform == "darwin"
                    else contextlib.nullcontext()
                )
                with (
                    mock.patch.object(module.sys, "platform", platform),
                    mock.patch.object(
                        module.os,
                        "killpg",
                        side_effect=refusal,
                    ) as group_kill,
                    mock.patch.object(
                        module.os,
                        "waitid",
                        side_effect=ChildProcessError(42),
                    ),
                    mock.patch.object(module.os, "kill") as direct_kill,
                    group_state,
                    self.assertRaises(module.ClientError) as raised,
                ):
                    module._finalize_process_group(
                        process,
                        graceful=False,
                        context=context,
                    )

                self.assertIs(raised.exception.__cause__, refusal)
                self.assertEqual(group_kill.call_count, 1)
                direct_kill.assert_not_called()
                self.assertEqual(process.lifecycle.state, "lost")

    def test_group_cleanup_recovers_term_and_kill_observation_faults_then_reaps(
        self,
    ) -> None:
        module = load_client_module("task_witness_client_group_observation_faults")

        class Process:
            pid = 42
            returncode = None

            def __init__(self) -> None:
                self.lifecycle = module._ChildLifecycle()

        process = Process()
        context = self.cleanup_context(module)
        signals: list[int] = []
        term_attempts = 0
        kill_attempts = 0

        def signal_group(_pid: int, number: int) -> None:
            nonlocal term_attempts, kill_attempts
            signals.append(number)
            if number == signal.SIGTERM:
                term_attempts += 1
                if term_attempts == 1:
                    raise PermissionError(errno.EPERM, "fixture TERM refusal")
                return
            self.assertEqual(number, signal.SIGKILL)
            kill_attempts += 1
            if kill_attempts == 1:
                raise PermissionError(errno.EPERM, "fixture KILL refusal")

        def reap(target: object, deadline: float, *, context: object) -> None:
            del context
            self.assertGreater(deadline, 0)
            target.returncode = -signal.SIGKILL
            module._mark_child_reaped(target)

        with (
            mock.patch.object(module.sys, "platform", "linux"),
            mock.patch.object(module.time, "monotonic", return_value=10.0),
            mock.patch.object(module, "_remaining_with_retry", return_value=1.0),
            mock.patch.object(module, "_sleep_with_retry", side_effect=(False, True)),
            mock.patch.object(module.os, "killpg", side_effect=signal_group),
            mock.patch.object(
                module.os,
                "waitid",
                side_effect=(
                    MemoryError("fixture TERM observation"),
                    None,
                    MemoryError("fixture KILL observation"),
                    None,
                ),
            ) as waitid,
            mock.patch.object(
                module,
                "_non_darwin_group_state_with_retry",
                return_value="quiescent",
            ),
            mock.patch.object(
                module,
                "_reap_owned_process",
                side_effect=reap,
            ) as reap_call,
            self.assertRaisesRegex(
                module.ClientError,
                "validation process group cleanup failed",
            ) as raised,
        ):
            module._finalize_process_group(
                process,
                graceful=True,
                context=context,
            )

        self.assertIsInstance(raised.exception.__cause__, PermissionError)
        self.assertIn("fixture TERM refusal", str(raised.exception.__cause__))
        self.assertEqual(
            signals,
            [signal.SIGTERM, signal.SIGTERM, signal.SIGKILL, signal.SIGKILL],
        )
        self.assertEqual(waitid.call_count, 4)
        reap_call.assert_called_once_with(
            process,
            context.cleanup_deadline,
            context=context,
        )
        self.assertFalse(process.lifecycle.responsible)

    def test_exact_reap_does_not_rebase_or_wait_after_the_original_deadline(
        self,
    ) -> None:
        module = load_client_module("task_witness_client_exact_reap_deadline_fixture")

        class Process:
            pid = 42
            returncode = None

            def __init__(self) -> None:
                self.lifecycle = module._ChildLifecycle()

            def wait(self, *, deadline: float) -> int:
                raise AssertionError("expired exact deadline must not enter wait")

        process = Process()
        with (
            mock.patch.object(module.time, "monotonic", return_value=2.0),
            mock.patch.object(module.time, "sleep") as sleep,
        ):
            error = module._reap_owned_process(
                process,
                1.0,
                context=module._prepare_cleanup_context(),
            )

        self.assertIsInstance(error, subprocess.TimeoutExpired)
        sleep.assert_not_called()

    def test_darwin_leaf_probe_requires_time_and_exact_child_responsibility(
        self,
    ) -> None:
        module = load_client_module("task_witness_client_darwin_leaf_guard_fixture")

        for name, observe, error_type in (
            (
                "expired",
                lambda process: False,
                subprocess.TimeoutExpired,
            ),
            (
                "lost",
                lambda process: (module._mark_child_lost(process), False)[1],
                module.ClientError,
            ),
        ):
            with self.subTest(name=name):
                context = module._prepare_cleanup_context()
                context.publish_pid(42)
                process = module._OwnedProcess(42, None, None, context)
                clock = 2.0 if name == "expired" else 0.0
                with (
                    mock.patch.object(
                        module,
                        "_leader_exited_without_reaping",
                        side_effect=lambda _process: observe(process),
                    ),
                    mock.patch.object(module.time, "monotonic", return_value=clock),
                    mock.patch.object(module.os, "killpg") as probe,
                    self.assertRaises(error_type),
                ):
                    module._darwin_process_group_state(
                        process,
                        1.0,
                        context,
                        probe_clock_slot=module._RETRY_DARWIN_PROBE_CLOCK,
                    )
                probe.assert_not_called()

    def test_darwin_preterm_and_forced_group_state_retries_are_independent(
        self,
    ) -> None:
        module = load_client_module("task_witness_client_darwin_phase_retry_fixture")
        original_grace = module.PROCESS_PROFILE["termination_grace_seconds"]
        module.PROCESS_PROFILE["termination_grace_seconds"] = 0.0
        context = self.cleanup_context(module, 42)
        preterm_fault = MemoryError("fixture preterm probe fault")
        forced_fault = MemoryError("fixture forced probe fault")
        outcomes: list[object] = [
            preterm_fault,
            "live",
            forced_fault,
            "quiescent",
        ]

        class Process:
            pid = 42
            returncode: int | None = None
            lifecycle = context.lifecycle

            def __init__(self) -> None:
                self.wait_calls = 0

            def wait(self, *, deadline: float) -> int:
                self.wait_calls += 1
                self.returncode = 0
                self.lifecycle.state = "reaped"
                return 0

        def classify(
            _process: object,
            _deadline: float,
            _context: object,
            *,
            wait_clock_slot: int,
            probe_clock_slot: int,
        ) -> str:
            del wait_clock_slot, probe_clock_slot
            outcome = outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        process = Process()
        try:
            with (
                mock.patch.object(module.time, "monotonic", return_value=1.0),
                mock.patch.object(
                    module,
                    "_darwin_wait_for_process_group_state",
                    side_effect=classify,
                ) as classify_group_state,
                mock.patch.object(
                    module, "_signal_with_retry", return_value=None
                ) as signal_group,
                self.assertRaisesRegex(
                    module.ClientError,
                    "validation process group cleanup failed",
                ) as raised,
            ):
                with mock.patch.object(module.sys, "platform", "darwin"):
                    module._finalize_process_group(
                        process,
                        graceful=True,
                        context=context,
                    )
        finally:
            module.PROCESS_PROFILE["termination_grace_seconds"] = original_grace

        self.assertEqual(classify_group_state.call_count, 4)
        self.assertEqual(outcomes, [])
        signal_group.assert_called_once()
        self.assertEqual(signal_group.call_args.args[2], signal.SIGTERM)
        self.assertIs(raised.exception.__cause__, preterm_fault)
        self.assertEqual(process.wait_calls, 1)
        self.assertEqual(process.lifecycle.state, "reaped")

    def test_cleanup_signal_deadlines_block_retries_and_fallbacks(self) -> None:
        module = load_client_module("task_witness_client_signal_deadline_fixture")

        class Process:
            pid = 42
            returncode = None

            def __init__(self) -> None:
                self.lifecycle = module._ChildLifecycle()

            def wait(self, *, deadline: float) -> int:
                self.fail("expired cleanup must not attempt an exact reap")

        original_budget = module.PROCESS_PROFILE["kill_reap_seconds"]
        module.PROCESS_PROFILE["kill_reap_seconds"] = 0.01
        try:
            for platform in ("linux", "darwin"):
                with self.subTest(platform=platform):
                    process = Process()
                    refusal = OSError(errno.EIO, "fixture signal refusal")
                    group_signals: list[int] = []

                    def reject_group(
                        _pid: int,
                        number: int,
                        group_signals: list[int] = group_signals,
                        refusal: OSError = refusal,
                    ) -> None:
                        group_signals.append(number)
                        time.sleep(0.02)
                        raise refusal

                    with (
                        mock.patch.object(module.sys, "platform", platform),
                        mock.patch.object(
                            module.os, "killpg", side_effect=reject_group
                        ),
                        mock.patch.object(module.os, "kill") as direct_kill,
                        mock.patch.object(
                            module,
                            "_darwin_wait_for_process_group_state",
                            return_value="live",
                        ),
                        self.assertRaises(module.ClientError) as raised,
                    ):
                        module._finalize_process_group(
                            process,
                            graceful=False,
                            context=module._prepare_cleanup_context(),
                        )

                    self.assertEqual(raised.exception.exit_code, 124)
                    self.assertIs(raised.exception.__cause__, refusal)
                    self.assertEqual(group_signals, [signal.SIGKILL])
                    direct_kill.assert_not_called()

            context = module._prepare_cleanup_context()
            context.publish_pid(42)
            refusal = OSError(errno.EIO, "fixture direct signal refusal")
            direct_signals: list[int] = []

            def reject_direct(_pid: int, number: int) -> None:
                direct_signals.append(number)
                time.sleep(0.02)
                raise refusal

            with mock.patch.object(module.os, "kill", side_effect=reject_direct):
                cleanup = module._consume(42, context)

            self.assertFalse(cleanup.completed)
            self.assertIs(cleanup.error, refusal)
            self.assertEqual(direct_signals, [signal.SIGKILL])
        finally:
            module.PROCESS_PROFILE["kill_reap_seconds"] = original_budget

    def test_darwin_probe_fault_survives_ownership_loss(self) -> None:
        module = load_client_module("task_witness_client_darwin_probe_loss_fixture")

        class Process:
            pid = 42
            returncode = None

            def __init__(self) -> None:
                self.lifecycle = module._ChildLifecycle()

        process = Process()
        probe_error = module.ClientError(
            "validation process group state is unavailable",
            module.EXIT_RESOURCE,
        )
        attempts = 0

        def fail_then_lose(*_arguments: object, **_keywords: object) -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise probe_error
            module._mark_child_lost(process)
            raise module.ClientError(
                "validation child state became unavailable",
                module.EXIT_RESOURCE,
            )

        with (
            mock.patch.object(module.sys, "platform", "darwin"),
            mock.patch.object(
                module,
                "_darwin_wait_for_process_group_state",
                side_effect=fail_then_lose,
            ),
            mock.patch.object(module.os, "killpg") as group_kill,
            mock.patch.object(module.os, "kill") as direct_kill,
            self.assertRaises(module.ClientError) as raised,
        ):
            module._finalize_process_group(
                process,
                graceful=True,
                context=self.cleanup_context(module),
            )

        self.assertEqual(raised.exception.exit_code, 124)
        self.assertIs(raised.exception.__cause__, probe_error)
        self.assertEqual(process.lifecycle.state, "lost")
        group_kill.assert_not_called()
        direct_kill.assert_not_called()

    def test_unbounded_launcher_stderr_is_terminated_at_the_byte_limit(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        install_launcher_behavior(
            fixture,
            "unbounded_stream",
            descriptor=2,
            prefix=b"sensitive-child-detail\n",
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            timeout=5,
        )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertLessEqual(len(result.stderr), 4 * 1024)
        self.assertNotIn(b"sensitive-child-detail", result.stderr)
        self.assertEqual(result.stderr.count(b"\n"), 1)
        self.assertTrue(all(byte < 128 for byte in result.stderr))

    def test_unbounded_launcher_stdout_is_terminated_at_the_byte_limit(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        install_launcher_behavior(
            fixture,
            "unbounded_stream",
            descriptor=1,
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            timeout=5,
        )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertLessEqual(len(result.stderr), 4 * 1024)

    def test_simultaneous_bounded_streams_do_not_deadlock(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        install_launcher_behavior(fixture, "simultaneous_streams")

        started = time.monotonic()
        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            timeout=5,
        )
        elapsed = time.monotonic() - started

        self.assertEqual(result.returncode, 65, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertLess(elapsed, 5)

    def test_exited_launcher_with_inherited_open_pipes_is_bounded(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        install_launcher_behavior(
            fixture,
            "inherited_open_pipes",
            output=fixture.envelope_raw,
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            timeout=5,
        )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertLessEqual(len(result.stderr), 4 * 1024)

    def test_terminal_launcher_outcomes_kill_ordinary_descendants(self) -> None:
        for launcher_exit in (0, 1):
            with self.subTest(launcher_exit=launcher_exit):
                fixture = ValidInvocationFixture(self.root / str(launcher_exit))
                child_pid = fixture.root / "ordinary-child-pid"
                output = fixture.envelope_raw if launcher_exit == 0 else b""
                install_launcher_behavior(
                    fixture,
                    "terminal_descendant",
                    pid_path=child_pid,
                    output=output,
                    exit_code=launcher_exit,
                )
                child_survived = False
                pid = None
                try:
                    result = fixture.invoke(
                        "validate",
                        "--bundle",
                        str(fixture.bundle),
                        timeout=5,
                    )
                    pid = int(child_pid.read_text(encoding="utf-8"))
                    deadline = time.monotonic() + 1
                    while True:
                        try:
                            os.kill(pid, 0)
                        except ProcessLookupError:
                            break
                        if time.monotonic() >= deadline:
                            child_survived = True
                            break
                        time.sleep(0.01)
                finally:
                    if child_survived and pid is not None:
                        os.kill(pid, signal.SIGKILL)

                self.assertEqual(
                    result.returncode,
                    0 if launcher_exit == 0 else 65,
                    result.stderr.decode(),
                )
                self.assertFalse(
                    child_survived,
                    "terminal launcher outcome orphaned an ordinary descendant",
                )

    def test_ordinary_rejected_launcher_preserves_its_semantic_exit(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        install_launcher_behavior(
            fixture,
            "emit_output",
            output=b"",
            exit_code=1,
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            timeout=5,
        )

        self.assertEqual(result.returncode, 65, result.stderr.decode())
        self.assertEqual(result.stdout, b"")

    def test_process_spawn_time_counts_against_the_validation_deadline(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        fixture.validation_deadline_seconds = 0.05
        fixture._write_deployment_receipt()
        driver = self.root / "delayed_fork_driver.py"
        write_configured_driver(
            driver,
            PROCESS_SUPERVISION_DRIVER_SOURCE,
            {"scenario": "spawn-time", "delay_seconds": 0.1},
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
            timeout=5,
        )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, b"")

    def test_spawn_waits_for_post_setup_session_acknowledgement(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        fixture.kill_reap_seconds = 1
        fixture._write_deployment_receipt()
        setup_stage = fixture.root / "child-setup-stage"
        launcher_pid = fixture.root / "launcher-pid"
        install_launcher_behavior(fixture, "blocking")
        driver = self.root / "session_acknowledgement_driver.py"
        write_configured_driver(
            driver,
            PROCESS_SUPERVISION_DRIVER_SOURCE,
            {
                "scenario": "session-acknowledgement",
                "setup_stage": str(setup_stage),
                "launcher_pid": str(launcher_pid),
            },
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
            timeout=5,
        )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertEqual(setup_stage.read_text(encoding="utf-8"), "after")
        pid = int(launcher_pid.read_text(encoding="utf-8"))
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_pre_setsid_readiness_failure_uses_exact_pid_cleanup(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        fixture.kill_reap_seconds = 0.5
        fixture._write_deployment_receipt()
        setup_stage = fixture.root / "child-setup-stage"
        child_pid = fixture.root / "child-pid"
        direct_kill = fixture.root / "direct-kill"
        exact_wait = fixture.root / "exact-wait"
        group_kill = fixture.root / "group-kill"
        launcher_executed = fixture.root / "launcher-executed"
        install_launcher_behavior(
            fixture,
            "marker_output",
            marker=launcher_executed,
            marker_content="executed",
        )
        driver = self.root / "unacknowledged_setup_driver.py"
        write_configured_driver(
            driver,
            PROCESS_SUPERVISION_DRIVER_SOURCE,
            {
                "scenario": "pre-setsid-readiness",
                "setup_stage": str(setup_stage),
                "child_pid": str(child_pid),
                "direct_kill": str(direct_kill),
                "exact_wait": str(exact_wait),
                "group_kill": str(group_kill),
            },
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
            timeout=5,
        )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertEqual(setup_stage.read_text(encoding="utf-8"), "before")
        self.assertFalse(launcher_executed.exists())
        pid = int(child_pid.read_text(encoding="utf-8"))
        self.assertEqual(int(direct_kill.read_text(encoding="utf-8")), signal.SIGKILL)
        self.assertEqual(int(exact_wait.read_text(encoding="utf-8")), pid)
        self.assertFalse(group_kill.exists())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_terminal_state_is_observed_before_the_group_leader_is_reaped(
        self,
    ) -> None:
        module = load_client_module("task_witness_client_unreaped_leader_fixture")
        child = subprocess.Popen(
            [sys.executable, "-c", "raise SystemExit(2)"],
            start_new_session=True,
        )
        process = module._OwnedProcess(
            child.pid, None, None, self.cleanup_context(module, child.pid)
        )
        try:
            deadline = time.monotonic() + 3
            while not module._leader_exited_without_reaping(process):
                if time.monotonic() >= deadline:
                    self.fail("child did not reach its terminal state")
                time.sleep(0.01)

            self.assertIsNone(process.returncode)
            module._finalize_process_group(
                process,
                graceful=False,
                context=self.cleanup_context(module),
            )
            self.assertEqual(process.returncode, 2)
            child.returncode = process.returncode
        finally:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(deadline=time.monotonic() + 3)

    def test_real_darwin_echild_forbids_later_numeric_signals(self) -> None:
        module = load_client_module("task_witness_client_launcher_echild_fixture")
        child = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            start_new_session=True,
        )
        child.wait(timeout=3)
        process = module._OwnedProcess(
            child.pid, None, None, self.cleanup_context(module, child.pid)
        )

        with (
            mock.patch.object(module.sys, "platform", "darwin"),
            mock.patch.object(module.os, "killpg") as group_kill,
            mock.patch.object(module.os, "kill") as direct_kill,
            self.assertRaisesRegex(
                module.ClientError,
                "validation process group cleanup failed",
            ),
        ):
            module._finalize_process_group(
                process,
                graceful=True,
                context=self.cleanup_context(module),
            )

        self.assertFalse(process.lifecycle.responsible)
        group_kill.assert_not_called()
        direct_kill.assert_not_called()

    def test_darwin_live_probe_then_kill_failure_rejects(self) -> None:
        module = load_client_module("task_witness_client_darwin_kill_failure_fixture")

        class Process:
            pid = 42
            returncode = None

            def __init__(self) -> None:
                self.lifecycle = module._ChildLifecycle()

            def wait(self, *, deadline: float) -> int:
                module._mark_child_reaped(self)
                self.returncode = 0
                return 0

        process = Process()
        live_probe_count = 0

        def killpg(pid: int, number: int) -> None:
            nonlocal live_probe_count
            self.assertEqual(pid, process.pid)
            if number == 0:
                live_probe_count += 1
                if live_probe_count == 1:
                    return
                raise PermissionError(errno.EPERM, "zombie-only group")
            self.assertEqual(number, signal.SIGKILL)
            raise OSError(errno.EIO, "SIGKILL failed")

        with (
            mock.patch.object(module.sys, "platform", "darwin"),
            mock.patch.object(module.os, "killpg", side_effect=killpg) as group_kill,
            mock.patch.object(
                module,
                "_leader_exited_without_reaping",
                return_value=True,
            ),
            self.assertRaisesRegex(
                module.ClientError,
                "validation process group cleanup failed",
            ),
        ):
            module._finalize_process_group(
                process,
                graceful=False,
                context=self.cleanup_context(module),
            )

        self.assertEqual(
            group_kill.call_args_list,
            [
                mock.call(process.pid, 0),
                mock.call(process.pid, signal.SIGKILL),
                mock.call(process.pid, signal.SIGKILL),
                mock.call(process.pid, 0),
            ],
        )
        self.assertFalse(process.lifecycle.responsible)

    def test_darwin_probe_checks_deadline_before_each_numeric_probe(self) -> None:
        module = load_client_module("task_witness_client_darwin_probe_deadline_fixture")

        class Process:
            pid = 42

            def __init__(self) -> None:
                self.lifecycle = module._ChildLifecycle("ambiguous")

        process = Process()
        for name, moments, expected_probes in (
            ("already-expired", (10.0,), 0),
            ("interrupted-then-expired", (9.0, 10.0), 1),
        ):
            with self.subTest(name=name):
                probe = mock.Mock(return_value="interrupted")
                context = self.cleanup_context(module)
                monotonic = mock.Mock(side_effect=moments)
                with (
                    mock.patch.object(
                        module,
                        "_darwin_process_group_state",
                        probe,
                    ),
                    mock.patch.object(module.time, "monotonic", monotonic),
                    self.assertRaises(subprocess.TimeoutExpired),
                ):
                    module._darwin_wait_for_process_group_state(
                        process,
                        10.0,
                        context,
                        wait_clock_slot=module._RETRY_DARWIN_WAIT_CLOCK,
                        probe_clock_slot=module._RETRY_DARWIN_PROBE_CLOCK,
                    )

                self.assertEqual(probe.call_count, expected_probes)
                self.assertEqual(monotonic.call_count, len(moments))

    def test_darwin_one_shot_cleanup_allocation_failure_reaps_the_leader(
        self,
    ) -> None:
        module = load_client_module("task_witness_client_darwin_wait_retry_fixture")
        child = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            start_new_session=True,
        )
        process = module._OwnedProcess(
            child.pid, None, None, self.cleanup_context(module, child.pid)
        )
        deadline = time.monotonic() + 3
        while not module._leader_exited_without_reaping(process):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        original_wait = process.wait
        original_monotonic = time.monotonic
        calls = 0
        wait_calls = 0

        def fail_once() -> float:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise MemoryError("fixture cleanup deadline allocation")
            return original_monotonic()

        def fail_wait_once(*, deadline: float) -> int:
            nonlocal wait_calls
            wait_calls += 1
            if wait_calls == 1:
                raise MemoryError("fixture exact-wait allocation")
            return original_wait(deadline=deadline)

        process.wait = fail_wait_once
        try:
            with (
                mock.patch.object(module.sys, "platform", "darwin"),
                mock.patch.object(module.time, "monotonic", side_effect=fail_once),
                self.assertRaisesRegex(
                    module.ClientError,
                    "validation process group cleanup failed",
                ),
            ):
                module._finalize_process_group(
                    process,
                    graceful=False,
                    context=self.cleanup_context(module),
                )
        finally:
            process.wait = original_wait
            if process.lifecycle.responsible:
                process.wait(deadline=time.monotonic() + 3)
            child.returncode = process.returncode

        self.assertGreaterEqual(calls, 2)
        self.assertEqual(wait_calls, 2)
        self.assertFalse(process.lifecycle.responsible)
        with self.assertRaises(ChildProcessError):
            os.waitpid(process.pid, os.WNOHANG)

    def test_non_darwin_deadline_allocation_retry_forces_and_reaps_real_child(
        self,
    ) -> None:
        if sys.platform != "linux":
            self.skipTest("requires Linux /proc process-group evidence")
        module = load_client_module("task_witness_client_non_darwin_deadline_fixture")
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import signal, time; "
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"
                ),
            ],
            start_new_session=True,
        )
        process = module._OwnedProcess(
            child.pid, None, None, self.cleanup_context(module, child.pid)
        )
        original_monotonic = time.monotonic
        monotonic_calls = 0
        group_signals: list[int] = []
        original_killpg = os.killpg

        def one_shot_allocation_failure() -> float:
            nonlocal monotonic_calls
            monotonic_calls += 1
            if monotonic_calls == 1:
                raise MemoryError("fixture cleanup deadline allocation")
            return original_monotonic()

        def observed_killpg(pid: int, number: int) -> None:
            if pid == process.pid:
                group_signals.append(number)
            original_killpg(pid, number)

        try:
            with (
                mock.patch.object(module.sys, "platform", "linux"),
                mock.patch.object(
                    module.time,
                    "monotonic",
                    side_effect=one_shot_allocation_failure,
                ),
                mock.patch.object(module.os, "killpg", side_effect=observed_killpg),
                mock.patch.object(
                    module,
                    "_non_darwin_group_state_with_retry",
                    return_value="quiescent",
                ),
                self.assertRaisesRegex(
                    module.ClientError,
                    "validation process group cleanup failed",
                ),
            ):
                module._finalize_process_group(
                    process,
                    graceful=True,
                    context=self.cleanup_context(module),
                )
        finally:
            if process.lifecycle.responsible:
                try:
                    original_killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=3)
            child.returncode = process.returncode

        self.assertGreaterEqual(monotonic_calls, 2)
        self.assertEqual(group_signals[0], signal.SIGTERM)
        self.assertIn(signal.SIGKILL, group_signals)
        self.assertFalse(process.lifecycle.responsible)
        with self.assertRaises(ChildProcessError):
            os.waitpid(process.pid, os.WNOHANG)

    def test_post_sleep_profile_faults_force_and_reap_term_ignoring_children(
        self,
    ) -> None:
        cases = (
            ("linux", True),
            ("darwin", True),
            ("darwin", False),
        )
        for platform, graceful in cases:
            with self.subTest(platform=platform, graceful=graceful):
                module = load_client_module(
                    f"task_witness_client_post_sleep_{platform}_{graceful}"
                )
                ready = self.root / f"post-sleep-{platform}-{graceful}-ready"
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import signal, sys, time; "
                            "from pathlib import Path; "
                            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                            "Path(sys.argv[1]).touch(); "
                            "time.sleep(30)"
                        ),
                        str(ready),
                    ],
                    start_new_session=True,
                )
                original_killpg = os.killpg
                signals: list[int] = []
                callback_error = MemoryError("fixture post-sleep profile failure")
                profile_faulted = False
                context = self.cleanup_context(module, child.pid)
                context.termination_grace_seconds = 0.05
                context.kill_reap_seconds = 0.5
                process = module._OwnedProcess(child.pid, None, None, context)

                def observed_killpg(pid: int, number: int) -> None:
                    if pid == child.pid:
                        signals.append(number)
                    original_killpg(pid, number)

                def fail_after_sleep(_frame, event, argument):
                    nonlocal profile_faulted
                    if (
                        not profile_faulted
                        and event == "c_return"
                        and argument is time.sleep
                    ):
                        profile_faulted = True
                        sys.setprofile(None)
                        raise callback_error
                    return fail_after_sleep

                try:
                    deadline = time.monotonic() + 3
                    while not ready.exists():
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(0.01)
                    group_state = (
                        mock.patch.object(
                            module,
                            "_non_darwin_group_state_with_retry",
                            return_value="quiescent",
                        )
                        if platform == "linux"
                        else contextlib.nullcontext()
                    )
                    with (
                        mock.patch.object(module.sys, "platform", platform),
                        mock.patch.object(
                            module.os,
                            "killpg",
                            side_effect=observed_killpg,
                        ),
                        group_state,
                        self.assertRaisesRegex(
                            module.ClientError,
                            "validation process group cleanup failed",
                        ) as raised,
                    ):
                        sys.setprofile(fail_after_sleep)
                        module._finalize_process_group(
                            process,
                            graceful=graceful,
                            context=context,
                        )
                finally:
                    sys.setprofile(None)
                    if process.lifecycle.responsible:
                        try:
                            original_killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait(deadline=time.monotonic() + 3)
                    child.returncode = process.returncode

                self.assertTrue(profile_faulted)
                self.assertIs(raised.exception.__cause__, callback_error)
                self.assertTrue(context.cleanup_deadline_armed)
                self.assertIn(signal.SIGKILL, signals)
                if graceful:
                    self.assertLess(
                        context.grace_cutoff,
                        context.cleanup_deadline,
                    )
                    self.assertIn(signal.SIGTERM, signals)
                    self.assertLess(
                        signals.index(signal.SIGTERM), signals.index(signal.SIGKILL)
                    )
                self.assertFalse(process.lifecycle.responsible)
                with self.assertRaises(ChildProcessError):
                    os.waitpid(process.pid, os.WNOHANG)

    def test_darwin_lost_ownership_never_signals_after_deadline_unavailable(
        self,
    ) -> None:
        module = load_client_module("task_witness_client_darwin_lost_ownership_fixture")
        child = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            start_new_session=True,
        )
        child.wait(timeout=3)
        process = module._OwnedProcess(
            child.pid, None, None, self.cleanup_context(module, child.pid)
        )
        unavailable = MemoryError("fixture cleanup deadline allocation")
        context = self.cleanup_context(module)
        module._mark_child_lost(process)

        def arm_cleanup(*, graceful: bool) -> bool:
            self.assertTrue(graceful)
            context.record(unavailable)
            return False

        with (
            mock.patch.object(module.sys, "platform", "darwin"),
            mock.patch.object(
                module._CleanupContext,
                "arm_cleanup",
                side_effect=arm_cleanup,
            ),
            mock.patch.object(module.os, "killpg") as group_kill,
            mock.patch.object(module.os, "kill") as direct_kill,
            self.assertRaisesRegex(
                module.ClientError,
                "validation process group cleanup failed",
            ),
        ):
            module._finalize_process_group(
                process,
                graceful=True,
                context=context,
            )

        self.assertFalse(process.lifecycle.responsible)
        group_kill.assert_not_called()
        direct_kill.assert_not_called()

    def test_group_kill_retry_removes_real_same_session_descendants(self) -> None:
        if sys.platform != "linux":
            self.skipTest("requires Linux /proc process-group evidence")
        module = load_client_module("task_witness_client_group_kill_retry_fixture")

        for platform in ("linux",):
            with self.subTest(platform=platform):
                descendant_marker = self.root / f"{platform}-descendant-pid"
                descendant_driver = self.root / f"{platform}-descendant-driver.py"
                write_configured_driver(
                    descendant_driver,
                    PROCESS_SUPERVISION_DRIVER_SOURCE,
                    {
                        "scenario": "same-session-descendant",
                        "marker": str(descendant_marker),
                    },
                )
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-B",
                        "-I",
                        "-S",
                        str(descendant_driver),
                    ],
                    start_new_session=True,
                )
                process = module._OwnedProcess(
                    child.pid, None, None, self.cleanup_context(module, child.pid)
                )
                original_killpg = os.killpg
                original_wait = process.wait
                kill_attempts = 0
                descendant_pid = None
                reap_boundary_states: list[tuple[str, ...]] = []

                def fail_first_group_kill(
                    pid: int,
                    number: int,
                    *,
                    expected_pid: int = process.pid,
                    killpg: object = original_killpg,
                ) -> None:
                    nonlocal kill_attempts
                    if pid == expected_pid and number == signal.SIGKILL:
                        kill_attempts += 1
                        if kill_attempts == 1:
                            raise OSError(errno.EIO, "fixture group kill failure")
                    killpg(pid, number)

                try:
                    deadline = time.monotonic() + 3
                    descendant_text = ""
                    while not descendant_text:
                        self.assertLess(time.monotonic(), deadline)
                        if descendant_marker.exists():
                            descendant_text = descendant_marker.read_text(
                                encoding="utf-8"
                            ).strip()
                        time.sleep(0.01)
                    descendant_pid = int(descendant_text)

                    def wait_after_group_quiescence(*, deadline: float) -> int:
                        states = module._linux_process_group_member_states(
                            process.pid,
                            deadline=deadline,
                            context=process.cleanup,
                        )
                        self.assertTrue(
                            all(state in {"X", "Z"} for state in states),
                            states,
                        )
                        reap_boundary_states.append(states)
                        return original_wait(deadline=deadline)

                    process.wait = wait_after_group_quiescence
                    with (
                        mock.patch.object(module.sys, "platform", platform),
                        mock.patch.object(
                            module.os,
                            "killpg",
                            side_effect=fail_first_group_kill,
                        ),
                    ):
                        try:
                            module._finalize_process_group(
                                process,
                                graceful=False,
                                context=self.cleanup_context(module),
                            )
                        except module.ClientError:
                            pass
                finally:
                    process.wait = original_wait
                    if process.lifecycle.responsible:
                        try:
                            original_killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait(timeout=3)
                    child.returncode = process.returncode

                self.assertEqual(kill_attempts, 2)
                self.assertFalse(process.lifecycle.responsible)
                self.assertIsNotNone(descendant_pid)
                self.assertEqual(len(reap_boundary_states), 1)

    def test_linux_threaded_descendant_quiesces_before_leader_reap(self) -> None:
        if sys.platform != "linux":
            self.skipTest("requires Linux /proc process-group evidence")
        module = load_client_module("task_witness_client_threaded_descendant_fixture")
        descendant_marker = self.root / "threaded-descendant-pids"
        descendant_driver = self.root / "threaded-descendant-driver.py"
        write_configured_driver(
            descendant_driver,
            PROCESS_SUPERVISION_DRIVER_SOURCE,
            {
                "scenario": "threaded-same-session-descendant",
                "marker": str(descendant_marker),
            },
        )
        child = subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-I",
                "-S",
                str(descendant_driver),
            ],
            start_new_session=True,
        )
        context = self.cleanup_context(module, child.pid)
        process = module._OwnedProcess(child.pid, None, None, context)
        original_wait = process.wait
        reap_boundary_states: list[tuple[str, ...]] = []

        try:
            deadline = time.monotonic() + 3
            descendant_pid = worker_tid = None
            while descendant_pid is None or worker_tid is None:
                self.assertLess(time.monotonic(), deadline)
                if descendant_marker.exists():
                    fields = descendant_marker.read_text(encoding="ascii").split()
                    if len(fields) == 2:
                        descendant_pid, worker_tid = (int(field) for field in fields)
                time.sleep(0.01)

            while True:
                self.assertLess(time.monotonic(), deadline)
                try:
                    main_state, _, _ = module._linux_proc_stat(
                        f"/proc/{descendant_pid}/stat",
                        deadline,
                        context,
                        child.pid,
                    )
                    worker_state, _, _ = module._linux_proc_stat(
                        f"/proc/{descendant_pid}/task/{worker_tid}/stat",
                        deadline,
                        context,
                        child.pid,
                    )
                except FileNotFoundError:
                    continue
                if main_state == "Z" and worker_state not in {"X", "Z"}:
                    break
                time.sleep(0.01)

            def wait_after_group_quiescence(*, deadline: float) -> int:
                states = module._linux_process_group_member_states(
                    process.pid,
                    deadline=deadline,
                    context=context,
                )
                self.assertTrue(
                    all(state in {"X", "Z"} for state in states),
                    states,
                )
                reap_boundary_states.append(states)
                return original_wait(deadline=deadline)

            process.wait = wait_after_group_quiescence
            module._finalize_process_group(
                process,
                graceful=False,
                context=context,
            )
        finally:
            process.wait = original_wait
            if process.lifecycle.responsible:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(deadline=time.monotonic() + 3)
            child.returncode = process.returncode

        self.assertFalse(process.lifecycle.responsible)
        self.assertEqual(len(reap_boundary_states), 1)

    def test_non_darwin_reaps_only_after_process_group_quiesces(self) -> None:
        module = load_client_module("task_witness_client_non_darwin_quiescence_fixture")
        test_case = self

        class Process:
            pid = 42
            returncode = None

            def __init__(self) -> None:
                self.lifecycle = module._ChildLifecycle()
                self.wait_count = 0

            def wait(self, *, deadline: float) -> int:
                del deadline
                self.wait_count += 1
                test_case.assertEqual(
                    group_calls,
                    [signal.SIGKILL, signal.SIGKILL, signal.SIGKILL],
                )
                module._mark_child_reaped(self)
                self.returncode = -signal.SIGKILL
                return self.returncode

        process = Process()
        context = self.cleanup_context(module)
        group_calls: list[int] = []

        def killpg(pid: int, number: int) -> None:
            test_case.assertEqual(pid, process.pid)
            group_calls.append(number)

        with (
            mock.patch.object(module.sys, "platform", "linux"),
            mock.patch.object(
                module, "_leader_exited_without_reaping", return_value=True
            ),
            mock.patch.object(module.os, "killpg", side_effect=killpg),
            mock.patch.object(
                module,
                "_linux_process_group_member_states",
                side_effect=(("R", "R"), ("Z", "R"), ("Z",), ("Z",)),
            ),
            mock.patch.object(module.time, "sleep"),
        ):
            module._finalize_process_group(
                process,
                graceful=False,
                context=context,
            )

        self.assertEqual(process.wait_count, 1)
        self.assertEqual(process.lifecycle.state, "reaped")

    def test_non_darwin_requires_a_successful_group_kill_before_reap(self) -> None:
        module = load_client_module("task_witness_client_group_barrier_fixture")

        class Process:
            pid = 42
            returncode = None

            def __init__(self) -> None:
                self.lifecycle = module._ChildLifecycle()
                self.wait_count = 0

            def wait(self, *, deadline: float) -> int:
                del deadline
                self.wait_count += 1
                raise AssertionError("leader reaped without a group-kill barrier")

        process = Process()
        with (
            mock.patch.object(module.sys, "platform", "linux"),
            mock.patch.object(
                module,
                "_non_darwin_group_state_with_retry",
                return_value="quiescent",
            ),
            mock.patch.object(
                module.os,
                "killpg",
                side_effect=ProcessLookupError(process.pid),
            ),
            self.assertRaises(module.ClientError),
        ):
            module._finalize_process_group(
                process,
                graceful=False,
                context=self.cleanup_context(module),
            )

        self.assertEqual(process.wait_count, 0)
        self.assertEqual(process.lifecycle.state, "owned")

    def test_linux_task_snapshot_rejects_a_missing_leader_task(self) -> None:
        module = load_client_module("task_witness_client_missing_leader_task_fixture")

        class Entry:
            def __init__(self, name: str) -> None:
                self.name = name

            def is_dir(self, *, follow_symlinks: bool) -> bool:
                if follow_symlinks:
                    raise AssertionError("procfs entry follow_symlinks must be false")
                return True

        entry = Entry
        context = self.cleanup_context(module)
        with (
            mock.patch.object(
                module.os,
                "scandir",
                side_effect=(
                    contextlib.nullcontext([entry("42")]),
                    contextlib.nullcontext([entry("43")]),
                ),
            ),
            mock.patch.object(
                module,
                "_linux_proc_stat",
                side_effect=(("Z", 42, 42), ("Z", 42, 42)),
            ),
            self.assertRaises(module.ClientError),
        ):
            module._linux_process_group_member_states(
                42,
                deadline=time.monotonic() + 1,
                context=context,
            )

    def test_linux_task_snapshot_observes_deadline_during_directory_walk(self) -> None:
        module = load_client_module("task_witness_client_proc_deadline_fixture")

        class Entry:
            name = "42"

            def is_dir(self, *, follow_symlinks: bool) -> bool:
                if follow_symlinks:
                    raise AssertionError("procfs entry follow_symlinks must be false")
                return True

        context = self.cleanup_context(module)
        timeout = subprocess.TimeoutExpired(42, 0)
        with (
            mock.patch.object(
                module.os,
                "scandir",
                return_value=contextlib.nullcontext([Entry()]),
            ),
            mock.patch.object(
                module,
                "_linux_proc_remaining",
                side_effect=(None, timeout),
            ),
            self.assertRaises(subprocess.TimeoutExpired) as raised,
        ):
            module._linux_process_group_member_states(
                42,
                deadline=time.monotonic() + 1,
                context=context,
            )

        self.assertIs(raised.exception, timeout)

    def test_linux_proc_stat_parses_final_delimiter_and_rejects_bad_input(self) -> None:
        module = load_client_module("task_witness_client_proc_stat_fixture")
        context = self.cleanup_context(module)
        deadline = time.monotonic() + 1
        valid = b"42 (caf\xc3\xa9) worker) Z 1 42 42 0\n"

        with mock.patch("builtins.open", return_value=io.BytesIO(valid)):
            self.assertEqual(
                module._linux_proc_stat("/proc/42/stat", deadline, context, 42),
                ("Z", 42, 42),
            )

        for raw in (b"42 malformed\n", b"x" * (module.MAX_PROC_STAT_BYTES + 1)):
            with (
                self.subTest(raw=raw[:8]),
                mock.patch("builtins.open", return_value=io.BytesIO(raw)),
                self.assertRaises(module.ClientError),
            ):
                module._linux_proc_stat("/proc/42/stat", deadline, context, 42)

    def test_unsupported_platform_rejects_before_installation_access(self) -> None:
        module = load_client_module("task_witness_client_platform_preflight_fixture")

        with (
            mock.patch.object(module.sys, "platform", "freebsd14"),
            mock.patch.object(module, "_installed_root") as installed_root,
        ):
            self.assertFalse(module._canonical_client_process())

        installed_root.assert_not_called()

    def test_persistent_darwin_wait_failure_is_bounded_and_state_unknown(
        self,
    ) -> None:
        module = load_client_module(
            "task_witness_client_darwin_persistent_wait_fixture"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
        )
        process = module._OwnedProcess(
            child.pid, None, None, self.cleanup_context(module, child.pid)
        )
        original_wait = process.wait
        wait_calls = 0

        def fail_wait(*, deadline: float) -> int:
            nonlocal wait_calls
            wait_calls += 1
            raise MemoryError("fixture persistent exact-wait failure")

        process.wait = fail_wait
        started = time.monotonic()
        try:
            with (
                mock.patch.object(module.sys, "platform", "darwin"),
                mock.patch.object(
                    module,
                    "_darwin_wait_for_process_group_state",
                    return_value="quiescent",
                ),
                self.assertRaisesRegex(
                    module.ClientError,
                    "validation process group cleanup failed",
                ),
            ):
                module._finalize_process_group(
                    process,
                    graceful=False,
                    context=self.cleanup_context(module),
                )
            self.assertLess(time.monotonic() - started, 1)
            self.assertTrue(process.lifecycle.responsible)
            self.assertEqual(wait_calls, 2)
        finally:
            process.wait = original_wait
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(deadline=time.monotonic() + 3)
            child.returncode = process.returncode

    def test_pregate_cleanup_failure_supersedes_stream_materialization_error(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        driver = self.root / "pregate_cleanup_failure_driver.py"
        write_configured_driver(
            driver,
            PROCESS_SUPERVISION_DRIVER_SOURCE,
            {"scenario": "pregate-cleanup-failure"},
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
        )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"launcher child cleanup failed", result.stderr)

    def test_pregate_exact_cleanup_preserves_stream_materialization_class(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        driver = self.root / "pregate_cleanup_success_driver.py"
        write_configured_driver(
            driver,
            PROCESS_SUPERVISION_DRIVER_SOURCE,
            {"scenario": "pregate-cleanup-success"},
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
        )

        self.assertEqual(result.returncode, 65, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"launcher could not be started", result.stderr)

    def test_launcher_gate_failure_keeps_diagnostics_conservative(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        fixture.kill_reap_seconds = 0.5
        fixture._write_deployment_receipt()
        launcher_pid = self.root / "launcher-pid"
        driver = self.root / "launcher_gate_failure_driver.py"
        write_configured_driver(
            driver,
            PROCESS_SUPERVISION_DRIVER_SOURCE,
            {"scenario": "launcher-gate-failure", "launcher_pid": str(launcher_pid)},
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
            timeout=5,
        )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assert_diagnostic(
            result.stderr,
            message="launcher child gate failed",
            validator_code_executed="unknown",
            active_state_changed="unknown",
            current_receipt="sha256:" + fixture.receipt["content_sha256"][:12],
            next_action="do not retry; verify validator termination and active state",
        )
        pid = int(launcher_pid.read_text(encoding="utf-8"))
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_repeated_eintr_preserves_exact_and_wildcard_wait_deadlines(
        self,
    ) -> None:
        module = load_client_module("task_witness_client_eintr_deadline_fixture")

        for wait_kind in ("exact", "wildcard"):
            with self.subTest(wait=wait_kind):
                waitpid = mock.Mock(
                    side_effect=(
                        InterruptedError(),
                        InterruptedError(),
                        InterruptedError(),
                    )
                )
                monotonic = mock.Mock(side_effect=(9.0, 9.5, 10.0))
                context = self.cleanup_context(module, 42)
                if wait_kind == "wildcard":
                    context.lost_pid_deadline = 10.0
                with (
                    mock.patch.object(module.os, "waitpid", waitpid),
                    mock.patch.object(module.time, "monotonic", monotonic),
                ):
                    if wait_kind == "exact":
                        module._wait_terminal_writer(42, 10.0, None, context)
                        self.assertIsNone(context.wait_status)
                        self.assertTrue(context.wait_owned)
                        self.assertIsNone(context.wait_error)
                    else:
                        with self.assertRaisesRegex(
                            module.ClientError,
                            "sole-child cleanup timed out",
                        ):
                            module._wildcard_reap_sole_child(context)

                waited_pid = 42 if wait_kind == "exact" else -1
                self.assertEqual(
                    waitpid.call_args_list,
                    [mock.call(waited_pid, os.WNOHANG)] * 3,
                )
                self.assertEqual(monotonic.call_count, 3)

    def test_wildcard_reap_preserves_the_first_wait_fault(self) -> None:
        module = load_client_module("task_witness_client_wildcard_wait_memory_fixture")

        cases = (
            (
                "memory-then-echild",
                MemoryError("fixture wildcard wait allocation"),
                OSError(errno.ECHILD, "fixture child absent"),
            ),
            (
                "eio-then-reap",
                OSError(errno.EIO, "fixture wildcard wait failure"),
                (42, 0),
            ),
            (
                "persistent-memory",
                MemoryError("fixture first wildcard wait allocation"),
                MemoryError("fixture second wildcard wait allocation"),
            ),
        )
        for name, first_error, terminal in cases:
            with self.subTest(name=name):
                waitpid = mock.Mock(side_effect=(first_error, terminal))
                with (
                    mock.patch.object(module.os, "waitpid", waitpid),
                    self.assertRaises(module.ClientError) as raised,
                ):
                    context = self.cleanup_context(module)
                    context.lost_pid_deadline = time.monotonic() + 1
                    module._wildcard_reap_sole_child(context)

                self.assertEqual(raised.exception.exit_code, 124)
                self.assertIs(raised.exception.__cause__, first_error)
                self.assertEqual(
                    waitpid.call_args_list,
                    [mock.call(-1, os.WNOHANG)] * 2,
                )

    def test_wildcard_reap_continues_after_a_post_zero_callback(self) -> None:
        module = load_client_module("task_witness_client_wildcard_post_zero_fixture")
        context = self.cleanup_context(module)
        context.lost_pid_deadline = time.monotonic() + 1
        callback_error = RuntimeError("fixture post-zero callback")
        original_publish = module._publish_wildcard_wait_result
        waitpid = mock.Mock(side_effect=((0, 0), (42, 0)))

        def callback_after_zero(cleanup_context, result):
            state = original_publish(cleanup_context, result)
            if result[0] == 0:
                raise callback_error
            return state

        with (
            mock.patch.object(module.os, "waitpid", waitpid),
            mock.patch.object(
                module,
                "_publish_wildcard_wait_result",
                side_effect=callback_after_zero,
            ),
            mock.patch.object(module.time, "sleep"),
            self.assertRaises(module.ClientError) as raised,
        ):
            module._wildcard_reap_sole_child(context)

        self.assertIs(raised.exception.__cause__, callback_error)
        self.assertEqual(
            waitpid.call_args_list,
            [mock.call(-1, os.WNOHANG), mock.call(-1, os.WNOHANG)],
        )

    def test_post_reap_callback_never_restores_numeric_signal_authority(self) -> None:
        module = load_client_module("task_witness_client_post_reap_callback_fixture")
        callback_error = RuntimeError("fixture post-reap callback")
        original_publish = module._publish_wait_result

        for site in ("owned-process", "terminal-writer"):
            with self.subTest(site=site):
                context = self.cleanup_context(module, 42)

                def callback_after_reap(target, pid, result):
                    original_publish(target, pid, result)
                    raise callback_error

                with (
                    mock.patch.object(module.os, "waitpid", return_value=(42, 0)),
                    mock.patch.object(
                        module,
                        "_publish_wait_result",
                        side_effect=callback_after_reap,
                    ),
                ):
                    if site == "owned-process":
                        process = module._OwnedProcess(42, None, None, context)
                        with self.assertRaisesRegex(RuntimeError, "post-reap"):
                            process.wait(deadline=time.monotonic() + 1)
                    else:
                        module._wait_terminal_writer(
                            42,
                            time.monotonic() + 1,
                            None,
                            context,
                        )
                        self.assertIs(context.wait_error, callback_error)

                self.assertEqual(context.lifecycle.state, "reaped")
                with mock.patch.object(module.os, "kill") as kill:
                    cleanup = module._consume(42, context)
                self.assertFalse(cleanup.completed)
                self.assertEqual(kill.call_args_list, [])

    def test_fork_callback_after_positive_pid_loses_publication_authority(self) -> None:
        module = load_client_module("task_witness_client_fork_callback_fixture")

        for site in ("launcher", "writer"):
            with self.subTest(site=site):
                context = self.cleanup_context(module)
                callback_error = RuntimeError(f"fixture {site} fork callback")

                def callback(frame, event, _argument):
                    if (
                        event == "call"
                        and frame.f_code is module._publish_fork_result.__code__
                    ):
                        sys.settrace(None)
                        raise callback_error
                    return callback

                def fork_then_enable_callback():
                    sys.settrace(callback)
                    return 42

                try:
                    with mock.patch.object(
                        module.os,
                        "fork",
                        side_effect=fork_then_enable_callback,
                    ):
                        with self.assertRaisesRegex(RuntimeError, "fork callback"):
                            module._fork_and_publish(context)
                finally:
                    sys.settrace(None)

                self.assertIs(context.error, callback_error)
                self.assertIsNone(context.pid)
                self.assertEqual(context.lifecycle.state, "lost")

    def test_nonzero_or_malformed_monitoring_observation_rejects(self) -> None:
        module = load_client_module("task_witness_client_monitoring_fixture")

        class Monitoring:
            def __init__(self, global_events):
                self.global_events = global_events

            def get_tool(self, _tool_id):
                return None

            def get_events(self, _tool_id):
                return self.global_events

            def get_local_events(self, _tool_id, _code):
                return 0

        for global_events in (1, "malformed"):
            with self.subTest(global_events=global_events):
                with mock.patch.object(
                    module.sys, "monitoring", Monitoring(global_events)
                ):
                    self.assertFalse(module._instrumentation_is_clear())

    def test_module_local_monitoring_mask_is_a_pure_rejection(self) -> None:
        module = load_client_module("task_witness_client_module_monitoring_fixture")

        class Monitoring:
            def get_tool(self, _tool_id):
                return None

            def get_events(self, _tool_id):
                return 0

            def get_local_events(self, _tool_id, code):
                return int(code is module._MODULE_CODE)

        with (
            mock.patch.object(module.sys, "monitoring", Monitoring()),
            mock.patch.object(module.os, "_exit") as exit_process,
        ):
            self.assertFalse(module._instrumentation_is_clear())

        exit_process.assert_not_called()

    def test_module_code_closure_includes_property_accessors(self) -> None:
        module = load_client_module("task_witness_client_property_closure_fixture")

        codes = module._module_code_objects()

        self.assertIsNotNone(codes)
        self.assertIn(module._MODULE_CODE, codes)
        self.assertIn(module._ChildLifecycle.responsible.fget.__code__, codes)
        self.assertIn(module._ChildLifecycle.may_signal.fget.__code__, codes)
        module_constants = tuple(
            constant
            for constant in module._MODULE_CODE.co_consts
            if isinstance(constant, types.CodeType)
        )
        child_lifecycle_body = next(
            constant
            for constant in module_constants
            if constant.co_name == "_ChildLifecycle"
        )
        module_genexpr = next(
            constant for constant in module_constants if constant.co_name == "<genexpr>"
        )
        self.assertNotIn(child_lifecycle_body, codes)
        self.assertNotIn(module_genexpr, codes)
        with mock.patch.object(
            module,
            "_MODULE_CODE",
            compile("", "not-the-client-module", "exec"),
        ):
            self.assertIsNone(module._module_code_objects())
        annotation = getattr(module._absolute, "__annotate__", None)
        if sys.version_info >= (3, 14):
            self.assertIsNotNone(annotation)
            self.assertIn(annotation.__code__, codes)
        else:
            self.assertIsNone(annotation)

    def test_lost_pid_cleanup_preserves_the_initiating_fork_error(self) -> None:
        for site in ("launcher", "writer"):
            with self.subTest(site=site):
                module = load_client_module(
                    f"task_witness_client_{site}_lost_pid_first_error_fixture"
                )
                initiating = MemoryError(f"{site} fork PID-return loss")
                wildcard = MemoryError(f"{site} wildcard wait allocation")

                with (
                    mock.patch.object(module.os, "fork", side_effect=initiating),
                    mock.patch.object(
                        module.os,
                        "waitpid",
                        side_effect=(wildcard, ChildProcessError()),
                    ),
                    self.assertRaises(module.ClientError) as raised,
                ):
                    if site == "launcher":
                        module._spawn_launcher(["/unused"], module.InvocationState())
                    else:
                        module._start_terminal_writer(
                            1,
                            b"unused",
                            module.InvocationState(),
                            module._prepare_cleanup_context(writer_seconds=0.25),
                        )

                self.assertEqual(raised.exception.exit_code, 124)
                self.assertIs(raised.exception.__cause__, initiating)

    def test_writer_kill_error_does_not_prevent_exact_pid_reap(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        fixture.accepted_output_deadline_seconds = 0.25
        fixture.kill_reap_seconds = 0.5
        fixture._write_deployment_receipt()
        driver, writer_pid, writer_reaped = self.configured_observed_writer_driver(
            fixture,
            "failed_first_kill",
            PROCESS_SUPERVISION_DRIVER_SOURCE,
            {"scenario": "writer-kill-failure"},
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
                timeout=3,
            )
        finally:
            os.close(write_descriptor)
            os.close(read_descriptor)

        self.assertGreater(filled_bytes, 0)
        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertTrue(writer_reaped.is_file(), "writer was not exactly reaped")
        pid = int(writer_pid.read_text(encoding="utf-8"))
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_group_signal_error_does_not_prevent_owned_child_reap(self) -> None:
        for failed_signal_name in ("SIGTERM", "SIGKILL"):
            with self.subTest(failed_signal=failed_signal_name):
                fixture = ValidInvocationFixture(self.root / failed_signal_name.lower())
                fixture.validation_deadline_seconds = 0.5
                fixture.termination_grace_seconds = 0.05
                fixture.kill_reap_seconds = 0.5
                child_pid = fixture.root / "child-pid"
                signal_failure = fixture.root / "signal-failure"
                install_launcher_behavior(fixture, "blocking")
                driver = fixture.root / "failed_group_signal_driver.py"
                write_configured_driver(
                    driver,
                    PROCESS_SUPERVISION_DRIVER_SOURCE,
                    {
                        "scenario": "group-signal-failure",
                        "failure_marker": str(signal_failure),
                        "failed_signal": failed_signal_name,
                        "child_pid": str(child_pid),
                    },
                )

                result = fixture.invoke(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                    driver=driver,
                    timeout=3,
                )

                self.assertEqual(result.returncode, 124, result.stderr.decode())
                self.assertTrue(signal_failure.is_file())
                pid = int(child_pid.read_text(encoding="utf-8"))
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)

    def test_launcher_spawn_failure_is_not_retried(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        launch_count = self.root / "launch-count"
        driver = self.root / "failed_fork_driver.py"
        write_configured_driver(
            driver,
            PROCESS_SUPERVISION_DRIVER_SOURCE,
            {"scenario": "spawn-failure", "count": str(launch_count)},
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
            timeout=5,
        )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertEqual(launch_count.read_text(encoding="utf-8"), "1")
        self.assertNotIn(b"fixture spawn failure", result.stderr)

    def test_post_fork_profile_errors_reap_unpublished_children(self) -> None:
        for target in ("launcher", "writer"):
            with self.subTest(target=target):
                fixture = ValidInvocationFixture(self.root / target)
                driver = fixture.root / "post_fork_profile_error_driver.py"
                write_configured_driver(
                    driver,
                    PROCESS_SUPERVISION_DRIVER_SOURCE,
                    {"scenario": "post-fork-profile-error", "target": target},
                )

                result = fixture.invoke(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                    driver=driver,
                    timeout=5,
                )

                self.assertEqual(result.returncode, 124, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                self.assertNotIn(b"fixture post-fork profile failure", result.stderr)

    def test_launcher_resource_errnos_use_the_resource_exit_class(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        driver = self.root / "resource_fork_driver.py"
        write_configured_driver(
            driver,
            PROCESS_SUPERVISION_DRIVER_SOURCE,
            {"scenario": "resource-fork", "main_argv_start": 4},
        )

        for number in sorted({errno.EAGAIN, errno.EMFILE, errno.ENFILE, errno.ENOMEM}):
            with self.subTest(number=number):
                result = fixture.invoke(
                    str(number),
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                    driver=driver,
                    timeout=5,
                )

                self.assertEqual(result.returncode, 124, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                self.assertNotIn(b"sensitive resource failure", result.stderr)

    def test_descriptor_exhaustion_uses_the_resource_exit_class(self) -> None:
        targets = {
            "preflight": (
                "client resources are unavailable",
                "module._open_directory_chain",
            ),
            "launcher": ("launcher resources are unavailable", "module._cloexec_pipe"),
            "selector": (
                "validation output resources are unavailable",
                "module.selectors.DefaultSelector",
            ),
        }
        for phase, (expected_message, target_expression) in targets.items():
            with self.subTest(phase=phase):
                fixture = ValidInvocationFixture(self.root / phase)
                driver = fixture.root / f"exhausted_{phase}_driver.py"
                write_configured_driver(
                    driver,
                    PROCESS_SUPERVISION_DRIVER_SOURCE,
                    {"scenario": "descriptor-exhaustion", "phase": phase},
                )

                result = fixture.invoke(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                    driver=driver,
                    timeout=5,
                )

                self.assertEqual(result.returncode, 124, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                self.assertNotIn(b"Too many open files", result.stderr)
                self.assert_diagnostic(
                    result.stderr,
                    message=expected_message,
                    validator_code_executed=(
                        "unknown" if phase == "selector" else "no"
                    ),
                    active_state_changed=("unknown" if phase == "selector" else "no"),
                    current_receipt=(
                        "unknown"
                        if phase == "preflight"
                        else "sha256:" + fixture.receipt["content_sha256"][:12]
                    ),
                    next_action=(
                        "do not retry; restore process and descriptor capacity"
                    ),
                )

    def test_launcher_output_oserrors_use_the_resource_exit_class(self) -> None:
        for phase in (
            "set_blocking",
            "register",
            "select",
            "read",
            "unregister",
            "close",
        ):
            with self.subTest(phase=phase):
                fixture = ValidInvocationFixture(self.root / phase)
                driver = fixture.root / f"failed_{phase}_driver.py"
                write_configured_driver(
                    driver,
                    PROCESS_SUPERVISION_DRIVER_SOURCE,
                    {
                        "scenario": "output-oserror",
                        "phase": phase,
                        "main_argv_start": 4,
                    },
                )

                result = fixture.invoke(
                    phase,
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                    driver=driver,
                    timeout=5,
                )

                self.assertEqual(result.returncode, 124, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                self.assertNotIn(b"sensitive output failure", result.stderr)
                cleanup_superseded_output_error = result.stderr.startswith(
                    b"task witness client rejected: "
                    b"validation process group cleanup failed"
                )
                expected_message = (
                    "validation process group cleanup failed"
                    if cleanup_superseded_output_error
                    else "validation output resources are unavailable"
                )
                self.assert_diagnostic(
                    result.stderr,
                    message=expected_message,
                    validator_code_executed="unknown",
                    active_state_changed="unknown",
                    current_receipt=(
                        "sha256:" + fixture.receipt["content_sha256"][:12]
                    ),
                    next_action=(
                        "do not retry; verify validator termination and active state"
                        if cleanup_superseded_output_error
                        else "do not retry; restore process and descriptor capacity"
                    ),
                )

    def test_timeout_kills_and_reaps_once_without_retry(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        fixture.validation_deadline_seconds = 1
        fixture.termination_grace_seconds = 0.1
        fixture.kill_reap_seconds = 0.5
        launch_count = self.root / "launch-count"
        child_pid = self.root / "child-pid"
        install_launcher_behavior(
            fixture,
            "counted_blocking",
            count=launch_count,
            pid=child_pid,
        )
        driver = self.root / "bounded_process_profile_driver.py"
        write_configured_driver(
            driver,
            PROCESS_SUPERVISION_DRIVER_SOURCE,
            {"scenario": "bounded-process-profile"},
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
            timeout=5,
        )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertEqual(launch_count.read_text(encoding="utf-8"), "1")
        self.assert_diagnostic(
            result.stderr,
            message="validation timed out",
            validator_code_executed="unknown",
            active_state_changed="unknown",
            current_receipt="sha256:" + fixture.receipt["content_sha256"][:12],
            next_action="do not retry; verify validator termination and active state",
        )
        pid = int(child_pid.read_text(encoding="utf-8"))
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_cancellation_signals_kill_and_reap_once_without_retry(self) -> None:
        for signal_name in ("SIGTERM", "SIGHUP"):
            with self.subTest(signal=signal_name):
                fixture = ValidInvocationFixture(self.root / signal_name.lower())
                fixture.validation_deadline_seconds = 10
                fixture.termination_grace_seconds = 0.1
                fixture.kill_reap_seconds = 0.5
                launch_count = fixture.root / "launch-count"
                child_pid = fixture.root / "child-pid"
                install_launcher_behavior(
                    fixture,
                    "counted_blocking",
                    count=launch_count,
                    pid=child_pid,
                )
                driver = fixture.root / "cancellable_process_profile_driver.py"
                write_configured_driver(
                    driver,
                    PROCESS_SUPERVISION_DRIVER_SOURCE,
                    {
                        "scenario": "cancellation-after-spawn",
                        "main_argv_start": 5,
                        "child_pid": str(child_pid),
                        "signal_name": signal_name,
                    },
                )
                pid = None
                child_survived = False
                try:
                    result = fixture.invoke(
                        str(child_pid),
                        signal_name,
                        "validate",
                        "--bundle",
                        str(fixture.bundle),
                        driver=driver,
                        timeout=5,
                    )
                    if child_pid.exists():
                        pid = int(child_pid.read_text(encoding="utf-8"))
                        try:
                            os.killpg(pid, 0)
                        except ProcessLookupError:
                            pass
                        else:
                            child_survived = True
                finally:
                    if child_survived and pid is not None:
                        os.killpg(pid, signal.SIGKILL)

                self.assertEqual(result.returncode, 124, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                self.assertEqual(launch_count.read_text(encoding="utf-8"), "1")
                self.assert_diagnostic(
                    result.stderr,
                    message="validation interrupted",
                    validator_code_executed="unknown",
                    active_state_changed="unknown",
                    current_receipt="sha256:" + fixture.receipt["content_sha256"][:12],
                    next_action="do not retry; verify validator termination and active state",
                )
                self.assertFalse(child_survived, "cancellation orphaned the launcher")

    def test_cancellation_after_pipe_eof_kills_and_reaps_promptly(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        fixture.validation_deadline_seconds = 1
        fixture.termination_grace_seconds = 0.1
        fixture.kill_reap_seconds = 0.5
        child_pid = self.root / "child-pid"
        install_launcher_behavior(
            fixture,
            "counted_blocking",
            pid=child_pid,
            close_streams=True,
        )
        driver = self.root / "post_eof_cancellation_driver.py"
        write_configured_driver(
            driver,
            PROCESS_SUPERVISION_DRIVER_SOURCE,
            {"scenario": "bounded-process-profile"},
        )
        process = subprocess.Popen(
            fixture.command(
                "validate",
                "--bundle",
                str(fixture.bundle),
                driver=driver,
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=CLIENT_ENVIRONMENT,
        )
        child_survived = False
        pid = None
        try:
            deadline = time.monotonic() + 3
            while not child_pid.exists() and process.poll() is None:
                if time.monotonic() >= deadline:
                    self.fail("launcher did not close its output pipes")
                time.sleep(0.01)
            self.assertIsNone(process.poll())
            time.sleep(0.1)
            started = time.monotonic()
            os.kill(process.pid, signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=3)
            elapsed = time.monotonic() - started
            if child_pid.exists():
                pid = int(child_pid.read_text(encoding="utf-8"))
                try:
                    os.killpg(pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    child_survived = True
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=3)
            if child_survived and pid is not None:
                os.killpg(pid, signal.SIGKILL)

        self.assertEqual(process.returncode, 124, stderr.decode())
        self.assertEqual(stdout, b"")
        self.assertLess(elapsed, 0.8)
        if stderr:
            self.assert_diagnostic(
                stderr,
                message="validation interrupted",
                validator_code_executed="unknown",
                active_state_changed="unknown",
                current_receipt="sha256:" + fixture.receipt["content_sha256"][:12],
                next_action=(
                    "do not retry; verify validator termination and active state"
                ),
            )
        self.assertFalse(child_survived, "post-EOF cancellation orphaned the launcher")
