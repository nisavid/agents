from __future__ import annotations

import fcntl
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

from ._support import (
    CLIENT_DRIVER_SOURCE,
    CLIENT_ENVIRONMENT,
    CLIENT_SOURCE,
    INVOCATION_PROFILE_DRIVER_SOURCE,
    LAUNCHER_SOURCE,
    PLUGIN,
    RUNTIME_PAYLOAD_SPECS,
    ComposedInvocationFixture,
    ValidInvocationFixture,
    _TaskWitnessClientTestCase,
    canonical_document,
    document,
    install_launcher_behavior,
    load_client_module,
    sha256,
    write_configured_driver,
)


class InvocationProfileTests(_TaskWitnessClientTestCase):
    def test_receipt_requires_the_exact_exclusive_lock_budget(self) -> None:
        control = ValidInvocationFixture(self.root / "exclusive-lock-control")
        self.assertEqual(
            control.process_profile.get("exclusive_lock_seconds"),
            65,
        )

        control_result = control.invoke(
            "validate",
            "--bundle",
            str(control.bundle),
        )

        self.assertEqual(
            control_result.returncode,
            0,
            control_result.stderr.decode(),
        )
        self.assertEqual(control_result.stdout, control.envelope_raw)

        cases = {
            "missing": lambda profile: profile.pop("exclusive_lock_seconds"),
            "changed": lambda profile: profile.__setitem__(
                "exclusive_lock_seconds",
                64,
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                unsigned = json.loads(fixture.deployment.read_bytes())
                unsigned.pop("content_sha256")
                mutate(unsigned["process_profile"])
                fixture.receipt = document(unsigned)
                fixture.deployment_raw = canonical_document(fixture.receipt)
                fixture.deployment.write_bytes(fixture.deployment_raw)
                fixture.deployment.chmod(0o600)
                fixture.retained_deployment_receipt.unlink()
                fixture.retained_deployment_receipt = (
                    fixture.receipts_directory
                    / f"sha256-{sha256(fixture.deployment_raw)}.json"
                )
                fixture.retained_deployment_receipt.write_bytes(fixture.deployment_raw)
                fixture.retained_deployment_receipt.chmod(0o600)

                result = fixture.invoke(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                )

                self.assertEqual(result.returncode, 70, result.stderr.decode())
                self.assertEqual(result.stdout, b"")

    def test_valid_canonical_invocation_emits_the_exact_launcher_envelope(self) -> None:
        self.assertTrue(CLIENT_SOURCE.is_file(), "canonical client source is missing")
        fixture = ValidInvocationFixture(self.root)
        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, fixture.envelope_raw)

    def test_loaded_client_rejects_a_newer_receipt_bound_source_generation(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        driver = self.root / "source_generation_overlap_driver.py"
        write_configured_driver(
            driver,
            CLIENT_DRIVER_SOURCE,
            {"scenario": "replace-loaded-client-generation"},
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
        )

        current_receipt = json.loads(fixture.deployment.read_bytes())["content_sha256"]
        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assert_diagnostic(
            result.stderr,
            message="client installation validation failed",
            validator_code_executed="no",
            active_state_changed="no",
            current_receipt="sha256:" + current_receipt[:12],
            next_action=(
                "do not retry; ask the deployment operator to inspect the installation"
            ),
        )
        self.assertNotIn(str(fixture.client).encode(), result.stderr)
        self.assertNotIn(b"generation", result.stderr)

    def test_client_accepts_the_real_launcher_runtime_and_validator(self) -> None:
        fixture = ComposedInvocationFixture(self.root)

        self.assertEqual(fixture.client.read_bytes(), CLIENT_SOURCE.read_bytes())
        self.assertEqual(fixture.launcher.read_bytes(), LAUNCHER_SOURCE.read_bytes())
        for role, name in RUNTIME_PAYLOAD_SPECS:
            self.assertEqual(
                (fixture.runtime_generation / name).read_bytes(),
                (PLUGIN / "runtime" / name).read_bytes(),
                role,
            )

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, fixture.envelope_raw)
        self.assertEqual(result.stdout.count(b"\n"), 1)

    def test_shared_lock_contention_uses_the_resource_exit_class(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        launcher_marker = self.root / "launcher-executed"
        install_launcher_behavior(
            fixture,
            "marker_output",
            marker=launcher_marker,
        )
        with fixture.lock.open("rb+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = fixture.invoke(
                "validate",
                "--bundle",
                str(fixture.bundle),
                timeout=5,
            )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assert_diagnostic(
            result.stderr,
            message="activation lock is unavailable",
            validator_code_executed="no",
            active_state_changed="no",
            current_receipt="unknown",
            next_action="do not retry; wait for the deployment operator",
        )
        self.assertFalse(launcher_marker.exists())

    def test_shared_lock_drift_during_contention_is_an_installation_error(
        self,
    ) -> None:
        for mode in ("mapping-replacement", "permission-drift"):
            with self.subTest(mode=mode):
                fixture = ValidInvocationFixture(self.root / mode)
                lock_attempted = fixture.root / "lock-attempted"
                driver = fixture.root / "contended_lock_driver.py"
                write_configured_driver(
                    driver,
                    INVOCATION_PROFILE_DRIVER_SOURCE,
                    {
                        "scenario": "shared-lock-observation",
                        "main_argv_start": 4,
                        "attempted": str(lock_attempted),
                    },
                )
                held_lock = os.open(fixture.lock, os.O_RDWR)
                process = None
                try:
                    fcntl.flock(held_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    process = subprocess.Popen(
                        fixture.command(
                            str(lock_attempted),
                            "validate",
                            "--bundle",
                            str(fixture.bundle),
                            driver=driver,
                        ),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=CLIENT_ENVIRONMENT,
                    )
                    deadline = time.monotonic() + 3
                    while not lock_attempted.exists() and process.poll() is None:
                        if time.monotonic() >= deadline:
                            self.fail("client did not attempt the shared lock")
                        time.sleep(0.01)
                    self.assertIsNone(process.poll())
                    if mode == "mapping-replacement":
                        replacement = fixture.root / "replacement.lock"
                        replacement.touch(mode=0o600)
                        os.replace(replacement, fixture.lock)
                    else:
                        fixture.lock.chmod(0o666)
                    stdout, stderr = process.communicate(timeout=5)
                finally:
                    if process is not None and process.poll() is None:
                        process.kill()
                        process.communicate(timeout=5)
                    fcntl.flock(held_lock, fcntl.LOCK_UN)
                    os.close(held_lock)

                self.assertEqual(process.returncode, 70, stderr.decode())
                self.assertEqual(stdout, b"")
                self.assertEqual(stderr.count(b"\n"), 1)

    def test_terminal_lock_drift_precedes_timeout_classification(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        replacement = self.root / "replacement.lock"
        replacement.touch(mode=0o600)
        driver = self.root / "terminal_lock_drift_driver.py"
        write_configured_driver(
            driver,
            INVOCATION_PROFILE_DRIVER_SOURCE,
            {
                "scenario": "terminal-lock-drift",
                "main_argv_start": 4,
                "replacement": str(replacement),
            },
        )
        held_lock = os.open(fixture.lock, os.O_RDWR)
        try:
            fcntl.flock(held_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = fixture.invoke(
                str(replacement),
                "validate",
                "--bundle",
                str(fixture.bundle),
                driver=driver,
                timeout=5,
            )
        finally:
            fcntl.flock(held_lock, fcntl.LOCK_UN)
            os.close(held_lock)

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")

    def test_directory_open_failures_close_every_acquired_descriptor(self) -> None:
        module = load_client_module("task_witness_client_descriptor_cleanup_fixture")
        child = self.root / "pending-descriptor"
        child.mkdir()
        original_open = module.os.open
        original_fstat = module.os.fstat
        original_close = module.os.close

        for phase, target in (("root", Path("/")), ("child", child)):
            with self.subTest(phase=phase):
                opened: list[int] = []
                state: dict[str, int | None] = {"failing_descriptor": None}

                def recording_open(
                    path,
                    *arguments,
                    opened=opened,
                    phase=phase,
                    child_name=child.name,
                    state=state,
                    **keywords,
                ):
                    descriptor = original_open(path, *arguments, **keywords)
                    opened.append(descriptor)
                    if (phase == "root" and path == "/") or (
                        phase == "child" and path == child_name
                    ):
                        state["failing_descriptor"] = descriptor
                    return descriptor

                def failing_fstat(descriptor, state=state):
                    if descriptor == state["failing_descriptor"]:
                        raise OSError("forced descriptor verification failure")
                    return original_fstat(descriptor)

                with (
                    mock.patch.object(module.os, "open", side_effect=recording_open),
                    mock.patch.object(module.os, "fstat", side_effect=failing_fstat),
                    self.assertRaisesRegex(
                        OSError,
                        "forced descriptor verification failure",
                    ),
                ):
                    module._open_directory_chain(
                        target,
                        "descriptor cleanup fixture",
                        private_final=False,
                    )

                leaked = []
                for descriptor in opened:
                    try:
                        original_fstat(descriptor)
                    except OSError:
                        continue
                    leaked.append(descriptor)
                    original_close(descriptor)
                self.assertEqual(leaked, [])

    def test_launcher_child_receives_fixed_profile_without_high_fd_leak(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        sentinel_read = os.open(os.devnull, os.O_RDONLY)
        sentinel_descriptor = 300
        descriptor_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
        if descriptor_limit[1] != resource.RLIM_INFINITY and descriptor_limit[1] < 301:
            os.close(sentinel_read)
            self.fail("qualified host RLIMIT_NOFILE cannot accommodate descriptor 300")
        if descriptor_limit[0] != resource.RLIM_INFINITY:
            resource.setrlimit(resource.RLIMIT_NOFILE, (301, descriptor_limit[1]))
            self.addCleanup(
                resource.setrlimit, resource.RLIMIT_NOFILE, descriptor_limit
            )
        os.dup2(sentinel_read, sentinel_descriptor, inheritable=True)
        os.set_inheritable(sentinel_descriptor, True)
        if sentinel_read != sentinel_descriptor:
            os.close(sentinel_read)
        write_configured_driver(
            fixture.driver,
            INVOCATION_PROFILE_DRIVER_SOURCE,
            {"scenario": "resource-limit"},
        )
        expected_arguments = [
            "validate",
            "--bundle",
            str(fixture.bundle),
            "--trust-context",
            str(fixture.trust_context),
        ]
        install_launcher_behavior(
            fixture,
            "fixed_profile",
            expected=expected_arguments,
            sentinel_descriptor=sentinel_descriptor,
            output=fixture.envelope_raw,
        )
        try:
            result = subprocess.run(
                fixture.command(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                ),
                input=b"hostile stdin",
                text=False,
                capture_output=True,
                check=False,
                env=CLIENT_ENVIRONMENT,
                pass_fds=(sentinel_descriptor,),
                timeout=10,
            )
        finally:
            os.close(sentinel_descriptor)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, fixture.envelope_raw)

    def test_changing_descriptor_inventory_fails_before_target_fork(self) -> None:
        for target in ("launcher", "writer"):
            with self.subTest(target=target):
                fixture = ValidInvocationFixture(self.root / target)
                target_forked = fixture.root / "target-forked"
                write_configured_driver(
                    fixture.driver,
                    INVOCATION_PROFILE_DRIVER_SOURCE,
                    {
                        "scenario": "changing-descriptor-inventory",
                        "target": target,
                        "target_forked": str(target_forked),
                    },
                )

                result = fixture.invoke(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                )

                self.assertEqual(
                    result.returncode,
                    70 if target == "launcher" else 124,
                    result.stderr.decode(),
                )
                self.assertEqual(result.stdout, b"")
                self.assertFalse(target_forked.exists())

    def test_noncanonical_client_executor_rejects_before_installation_access(
        self,
    ) -> None:
        cases = (
            (
                "missing-isolated-flag",
                ["-B", "-S", "-X", "disable-remote-debug"],
                CLIENT_ENVIRONMENT,
            ),
            (
                "missing-no-site-flag",
                ["-B", "-I", "-X", "disable-remote-debug"],
                CLIENT_ENVIRONMENT,
            ),
            (
                "missing-remote-debug-shutdown",
                ["-B", "-I", "-S"],
                CLIENT_ENVIRONMENT,
            ),
            (
                "optimization-flag",
                ["-B", "-I", "-S", "-X", "disable-remote-debug", "-O"],
                CLIENT_ENVIRONMENT,
            ),
            (
                "warning-option",
                [
                    "-B",
                    "-I",
                    "-S",
                    "-X",
                    "disable-remote-debug",
                    "-W",
                    "ignore",
                ],
                CLIENT_ENVIRONMENT,
            ),
            (
                "implementation-option",
                [
                    "-B",
                    "-I",
                    "-S",
                    "-X",
                    "disable-remote-debug",
                    "-X",
                    "dev",
                ],
                CLIENT_ENVIRONMENT,
            ),
            (
                "ambient-environment",
                ["-B", "-I", "-S", "-X", "disable-remote-debug"],
                {**CLIENT_ENVIRONMENT, "PATH": "/attacker/bin"},
            ),
        )
        for name, interpreter_arguments, environment in cases:
            with self.subTest(name=name):
                result, installation_access = self.invoke_with_installation_sentinel(
                    "validate",
                    "--bundle",
                    "/tmp/bundle",
                    interpreter_arguments=tuple(interpreter_arguments),
                    environment=environment,
                )

                self.assertEqual(result.returncode, 70, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                self.assertFalse(installation_access.exists())
                self.assert_diagnostic(
                    result.stderr,
                    message="client requires the canonical process profile",
                    validator_code_executed="no",
                    active_state_changed="no",
                    current_receipt="unknown",
                    next_action=(
                        "do not retry; ask the deployment operator to inspect "
                        "the installation"
                    ),
                )

    def test_cpython_before_3_13_rejects_before_installation_access(self) -> None:
        result, installation_access = self.invoke_with_installation_sentinel(
            "validate",
            "--bundle",
            "/tmp/bundle",
            sentinel_mode="version",
        )

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertFalse(installation_access.exists())
        self.assert_diagnostic(
            result.stderr,
            message="client requires the canonical process profile",
            validator_code_executed="no",
            active_state_changed="no",
            current_receipt="unknown",
            next_action=(
                "do not retry; ask the deployment operator to inspect the installation"
            ),
        )

    def test_noncanonical_signal_state_rejects_before_installation_access(
        self,
    ) -> None:
        cases = {
            "ignored-sigterm": ("SIGTERM", "ignored"),
            "default-sigpipe": ("SIGPIPE", "default"),
            "blocked-sigterm": ("SIGTERM", "blocked"),
            "custom-sigchld": ("SIGCHLD", "custom"),
            "custom-sigusr1": ("SIGUSR1", "custom"),
        }
        for signal_name in ("SIGXFZ", "SIGXFSZ"):
            if hasattr(signal, signal_name):
                cases[f"default-{signal_name.lower()}"] = (signal_name, "default")
        for name, (signal_name, signal_action) in cases.items():
            with self.subTest(name=name):
                result, installation_access = self.invoke_with_installation_sentinel(
                    "validate",
                    "--bundle",
                    "/tmp/bundle",
                    sentinel_mode="signal-state",
                    sentinel_configuration={
                        "signal_name": signal_name,
                        "signal_action": signal_action,
                    },
                )

                self.assertEqual(result.returncode, 70, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                self.assertFalse(installation_access.exists())
                if name == "custom-sigchld":
                    self.assertEqual(result.stderr, b"")
                    continue
                self.assert_diagnostic(
                    result.stderr,
                    message="client requires the canonical process profile",
                    validator_code_executed="no",
                    active_state_changed="no",
                    current_receipt="unknown",
                    next_action=(
                        "do not retry; ask the deployment operator to inspect "
                        "the installation"
                    ),
                )

    def test_unobservable_signal_disposition_rejects_before_installation_access(
        self,
    ) -> None:
        for error_name in ("OSError", "ValueError"):
            with self.subTest(error=error_name):
                result, installation_access = self.invoke_with_installation_sentinel(
                    "validate",
                    "--bundle",
                    "/tmp/bundle",
                    sentinel_mode="unobservable-signal",
                    sentinel_configuration={"error_name": error_name},
                )

                self.assertEqual(result.returncode, 124, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, b"")
                self.assertFalse(installation_access.exists())

    def test_retained_instrumentation_rejects_before_installation_access(self) -> None:
        for kind in (
            "trace",
            "profile",
            "thread-trace",
            "thread-profile",
            "monitoring-local-freed",
            "monitoring-property-freed",
            *(("monitoring-annotation-freed",) if sys.version_info >= (3, 14) else ()),
            "monitoring-unavailable",
        ):
            with self.subTest(kind=kind):
                result, installation_access = self.invoke_with_installation_sentinel(
                    "validate",
                    "--bundle",
                    "/tmp/bundle",
                    sentinel_mode="instrumentation",
                    sentinel_configuration={"instrumentation_kind": kind},
                )

                self.assertEqual(result.returncode, 70, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, b"")
                self.assertFalse(installation_access.exists())

        for tool_id in range(6):
            with self.subTest(kind="monitoring-occupied", tool_id=tool_id):
                result, installation_access = self.invoke_with_installation_sentinel(
                    "validate",
                    "--bundle",
                    "/tmp/bundle",
                    sentinel_mode="instrumentation",
                    sentinel_configuration={
                        "instrumentation_kind": "monitoring-occupied",
                        "tool_id": tool_id,
                    },
                )

                self.assertEqual(result.returncode, 70, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, b"")
                self.assertFalse(installation_access.exists())

    def test_self_erasing_pre_import_hook_is_outside_the_client_detector(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        driver = self.root / "self_erasing_pre_import_driver.py"
        trace_marker = self.root / "pre-import-trace-callback"
        write_configured_driver(
            driver,
            CLIENT_DRIVER_SOURCE,
            {
                "scenario": "self-erasing-pre-import-instrumentation",
                "main_argv_start": 3,
                "pre_import_trace_marker": str(trace_marker),
            },
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, fixture.envelope_raw)
        self.assertEqual(trace_marker.read_text(encoding="utf-8"), "callback-fired")

    def test_freed_global_monitoring_mask_is_version_sensitive(self) -> None:
        marker = self.root / "freed-global-events"
        result, installation_access = self.invoke_with_installation_sentinel(
            "validate",
            "--bundle",
            "/tmp/bundle",
            sentinel_mode="instrumentation",
            sentinel_configuration={
                "instrumentation_kind": "monitoring-global-freed",
                "global_events_marker": str(marker),
            },
        )

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        global_events = int(marker.read_text(encoding="utf-8"))
        if global_events:
            self.assertEqual(result.stderr, b"")
            self.assertFalse(installation_access.exists())
        else:
            self.assertTrue(installation_access.exists())

    def test_freed_module_monitoring_mask_exits_without_returning_to_the_module(
        self,
    ) -> None:
        driver = self.root / "module_frame_monitoring_driver.py"
        installation_access = self.root / "installation-accessed"
        configured_local_events_marker = self.root / "configured-module-local-events"
        post_free_local_events_marker = self.root / "post-free-module-local-events"
        callback_armed_marker = self.root / "module-callback-armed"
        main_returned_marker = self.root / "module-main-returned"
        post_main_callback_marker = self.root / "post-main-monitoring-callback"
        final_local_events_marker = self.root / "final-module-local-events"
        write_configured_driver(
            driver,
            CLIENT_DRIVER_SOURCE,
            {"scenario": "module-frame-monitoring"},
        )

        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-I",
                "-S",
                "-X",
                "disable-remote-debug",
                str(driver),
                str(CLIENT_SOURCE),
                str(installation_access),
                str(configured_local_events_marker),
                str(post_free_local_events_marker),
                str(callback_armed_marker),
                str(main_returned_marker),
                str(post_main_callback_marker),
                str(final_local_events_marker),
            ],
            text=False,
            capture_output=True,
            check=False,
            env=CLIENT_ENVIRONMENT,
        )

        configured_local_events = int(
            configured_local_events_marker.read_text(encoding="utf-8")
        )
        post_free_local_events = int(
            post_free_local_events_marker.read_text(encoding="utf-8")
        )
        self.assertNotEqual(configured_local_events, 0)
        self.assertNotEqual(post_free_local_events, 0)
        self.assertFalse(post_main_callback_marker.exists())
        if sys.version_info[:2] == (3, 13):
            self.assertTrue(callback_armed_marker.exists())
            self.assertFalse(main_returned_marker.exists())
            self.assertEqual(result.returncode, 70, result.stderr.decode())
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"")
            self.assertFalse(installation_access.exists())
            final_local_events = int(
                final_local_events_marker.read_text(encoding="utf-8")
            )
            self.assertNotEqual(final_local_events, 0)
        elif sys.version_info[:2] >= (3, 14):
            self.assertFalse(callback_armed_marker.exists())
            self.assertFalse(main_returned_marker.exists())
            final_local_events = int(
                final_local_events_marker.read_text(encoding="utf-8")
            )
            self.assertEqual(final_local_events, 0)
            self.assertEqual(result.returncode, 70, result.stderr.decode())
            self.assertEqual(result.stdout, b"")
            self.assertTrue(installation_access.exists())
            self.assert_diagnostic(
                result.stderr,
                message="client installation validation failed",
                validator_code_executed="no",
                active_state_changed="no",
                current_receipt="unknown",
                next_action=(
                    "do not retry; ask the deployment operator to inspect "
                    "the installation"
                ),
            )

    def test_handler_installation_failure_is_silent_and_restores_every_capture(
        self,
    ) -> None:
        module = load_client_module("task_witness_client_handler_install_fixture")

        cases = (
            (0, False, False),
            (1, False, False),
            (2, False, False),
            (len(module.CANCELLATION_SIGNALS) - 1, True, True),
        )
        for failure_index, mutation_before_failure, restoration_fails in cases:
            with self.subTest(
                failure_index=failure_index,
                mutation_before_failure=mutation_before_failure,
                restoration_fails=restoration_fails,
            ):
                original_dispositions = {
                    number: f"original-{number}"
                    for number in module.CANCELLATION_SIGNALS
                }
                current_dispositions = dict(original_dispositions)
                install_count = 0
                mutated: list[int] = []
                restored: list[int] = []

                def getsignal(number: int) -> object:
                    return original_dispositions[number]

                def install_or_restore(number: int, disposition: object) -> None:
                    nonlocal install_count
                    if disposition == original_dispositions[number]:
                        restored.append(number)
                        current_dispositions[number] = disposition
                        if restoration_fails and len(restored) == 1:
                            raise OSError("fixture restoration failure")
                        return
                    current_dispositions[number] = disposition
                    if install_count == failure_index:
                        install_count += 1
                        if mutation_before_failure:
                            mutated.append(number)
                            raise OSError("fixture mutation failure")
                        raise OSError("fixture installation failure")
                    install_count += 1

                with (
                    mock.patch.object(module.signal, "getsignal", getsignal),
                    mock.patch.object(module.signal, "signal", install_or_restore),
                    mock.patch.object(module, "_emit_diagnostic") as diagnostic,
                    mock.patch.object(module, "_write_terminal") as writer,
                ):
                    status = module.main(["validate", "--bundle", "/tmp/bundle"])

                self.assertEqual(status, 124)
                diagnostic.assert_not_called()
                writer.assert_not_called()
                self.assertEqual(
                    restored,
                    list(module.CANCELLATION_SIGNALS[: failure_index + 1]),
                )
                self.assertEqual(
                    mutated,
                    (
                        [module.CANCELLATION_SIGNALS[failure_index]]
                        if mutation_before_failure
                        else []
                    ),
                )
                self.assertEqual(
                    current_dispositions,
                    original_dispositions,
                )

    def test_first_post_installation_memory_error_is_diagnostic_eligible(self) -> None:
        module = load_client_module("task_witness_client_post_install_memory_fixture")

        with (
            mock.patch.object(module.signal, "getsignal", return_value=signal.SIG_DFL),
            mock.patch.object(module.signal, "signal"),
            mock.patch.object(module, "_canonical_client_process", return_value=True),
            mock.patch.object(
                module, "_parse_public_arguments", side_effect=MemoryError
            ),
            mock.patch.object(module, "_emit_diagnostic") as diagnostic,
            mock.patch.object(module, "_write_terminal") as writer,
        ):
            status = module.main(["validate", "--bundle", "/tmp/bundle"])

        self.assertEqual(status, 124)
        writer.assert_not_called()
        diagnostic.assert_called_once()
        error, invocation = diagnostic.call_args.args
        self.assertIs(error, module.CLIENT_RESOURCE_ERROR)
        self.assertEqual(invocation.diagnostic_state, "installed")

    def test_long_ambient_process_timers_remain_observable_before_rejection(
        self,
    ) -> None:
        for timer_name in ("ITIMER_REAL", "ITIMER_VIRTUAL", "ITIMER_PROF"):
            if not hasattr(signal, timer_name):
                continue
            with self.subTest(timer=timer_name):
                result, installation_access = self.invoke_with_installation_sentinel(
                    "validate",
                    "--bundle",
                    "/tmp/bundle",
                    sentinel_mode="timer",
                    sentinel_configuration={"timer_name": timer_name},
                )

                self.assertEqual(result.returncode, 70, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                self.assertFalse(installation_access.exists())

    def test_short_inherited_real_timer_ends_before_profile_or_deployment_access(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        launcher_executed = self.root / "launcher-executed"
        deployment_opened = self.root / "deployment-opened"
        watcher_ready = self.root / "deployment-watcher-ready"
        install_launcher_behavior(
            fixture,
            "marker_output",
            marker=launcher_executed,
        )
        fixture.deployment.unlink()
        os.mkfifo(fixture.deployment, 0o600)
        watcher_driver = self.root / "fifo_watcher_driver.py"
        write_configured_driver(
            watcher_driver,
            INVOCATION_PROFILE_DRIVER_SOURCE,
            {
                "scenario": "fifo-watcher",
                "fifo": str(fixture.deployment),
                "ready": str(watcher_ready),
                "opened": str(deployment_opened),
            },
        )
        watcher = subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-I",
                "-S",
                str(watcher_driver),
            ]
        )
        try:
            deadline = time.monotonic() + 1
            while not watcher_ready.exists():
                if time.monotonic() >= deadline:
                    self.fail("deployment watcher did not become ready")
                time.sleep(0.001)
            result = subprocess.run(
                fixture.command("validate", "--bundle", str(fixture.bundle)),
                capture_output=True,
                check=False,
                env=CLIENT_ENVIRONMENT,
                preexec_fn=lambda: signal.setitimer(signal.ITIMER_REAL, 0.001),
                timeout=5,
            )
        finally:
            if watcher.poll() is None:
                watcher.terminate()
            watcher.communicate(timeout=5)

        self.assertEqual(result.returncode, -signal.SIGALRM, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self.assertFalse(launcher_executed.exists())
        self.assertFalse(deployment_opened.exists())

    def test_unobservable_process_timers_reject_before_installation_access(
        self,
    ) -> None:
        for name in ("missing", "oserror", "valueerror"):
            with self.subTest(name=name):
                result, installation_access = self.invoke_with_installation_sentinel(
                    "validate",
                    "--bundle",
                    "/tmp/bundle",
                    sentinel_mode="timer-unobservable",
                    sentinel_configuration={"timer_failure": name},
                )

                self.assertEqual(result.returncode, 70, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                self.assertFalse(installation_access.exists())

    def test_preexisting_direct_child_rejects_before_installation_access(self) -> None:
        result, installation_access = self.invoke_with_installation_sentinel(
            "validate",
            "--bundle",
            "/tmp/bundle",
            sentinel_mode="preexisting-child",
        )

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self.assertFalse(installation_access.exists())

    def test_unknown_launcher_pid_after_fork_memory_error_is_reaped(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        launcher_executed = self.root / "launcher-executed"
        child_pid_marker = self.root / "unknown-child-pid"
        install_launcher_behavior(
            fixture,
            "marker_output",
            marker=launcher_executed,
        )
        driver = self.root / "unknown_launcher_pid_driver.py"
        write_configured_driver(
            driver,
            INVOCATION_PROFILE_DRIVER_SOURCE,
            {
                "scenario": "unknown-launcher-pid",
                "main_argv_start": 4,
                "child_pid_marker": str(child_pid_marker),
            },
        )

        result = fixture.invoke(
            str(child_pid_marker),
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
            timeout=5,
        )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertFalse(launcher_executed.exists())
        pid = int(child_pid_marker.read_text(encoding="utf-8"))
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_persistent_launcher_recovery_deadline_failure_prevents_fork(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        forked = self.root / "unexpected-launcher-fork"
        driver = self.root / "launcher_deadline_failure_driver.py"
        write_configured_driver(
            driver,
            INVOCATION_PROFILE_DRIVER_SOURCE,
            {
                "scenario": "persistent-recovery-deadline",
                "forked": str(forked),
            },
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
        )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertFalse(forked.exists())

    def test_launcher_context_preparation_failure_prevents_fork(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        forked = self.root / "unexpected-launcher-fork"
        driver = self.root / "launcher_context_failure_driver.py"
        write_configured_driver(
            driver,
            INVOCATION_PROFILE_DRIVER_SOURCE,
            {
                "scenario": "context-preparation-failure",
                "forked": str(forked),
            },
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
        )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertFalse(forked.exists())

    def test_launcher_publishes_pid_before_the_first_parent_fallible_step(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        published = self.root / "launcher-pid-published"
        driver = self.root / "launcher_pid_publication_driver.py"
        write_configured_driver(
            driver,
            INVOCATION_PROFILE_DRIVER_SOURCE,
            {
                "scenario": "pid-publication",
                "published": str(published),
            },
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
        )

        self.assertEqual(result.returncode, 124, result.stderr.decode())
        self.assertTrue(published.is_file())

    def test_launcher_pid_publication_failure_wildcard_reaps_without_release(
        self,
    ) -> None:
        for phase in ("before-mutation", "after-pid-store"):
            with self.subTest(phase=phase):
                fixture = ValidInvocationFixture(self.root / phase)
                launcher_executed = fixture.root / "launcher-executed"
                child_pid_marker = fixture.root / "launcher-child-pid"
                cleanup_elapsed_marker = fixture.root / "cleanup-elapsed"
                no_waitable_marker = fixture.root / "no-waitable-child"
                numeric_signal_marker = fixture.root / "numeric-signal"
                install_launcher_behavior(
                    fixture,
                    "marker_output",
                    marker=launcher_executed,
                    output=fixture.envelope_raw,
                )
                driver = fixture.root / "pid_publication_failure_driver.py"
                write_configured_driver(
                    driver,
                    INVOCATION_PROFILE_DRIVER_SOURCE,
                    {
                        "scenario": "pid-publication-failure",
                        "phase": phase,
                        "child_pid_marker": str(child_pid_marker),
                        "cleanup_elapsed_marker": str(cleanup_elapsed_marker),
                        "no_waitable_marker": str(no_waitable_marker),
                        "numeric_signal_marker": str(numeric_signal_marker),
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
                self.assertEqual(result.stderr, b"")
                self.assertTrue(child_pid_marker.is_file())
                self.assertTrue(no_waitable_marker.is_file())
                self.assertFalse(launcher_executed.exists())
                self.assertFalse(numeric_signal_marker.exists())
                self.assertLess(
                    float(cleanup_elapsed_marker.read_text(encoding="utf-8")),
                    1.25,
                )

    def test_launcher_and_writer_wait_for_parent_gate_sequence(self) -> None:
        for target in ("launcher", "writer"):
            with self.subTest(target=target):
                fixture = ValidInvocationFixture(self.root / target)
                child_action = fixture.root / "child-action"
                observed_order = fixture.root / "observed-order"
                if target == "launcher":
                    install_launcher_behavior(
                        fixture,
                        "marker_output",
                        marker=child_action,
                        output=fixture.envelope_raw,
                    )
                driver = fixture.root / "gate_order_driver.py"
                write_configured_driver(
                    driver,
                    INVOCATION_PROFILE_DRIVER_SOURCE,
                    {
                        "scenario": "gate-order",
                        "target": target,
                        "child_action": str(child_action),
                        "observed_order": str(observed_order),
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
                self.assertTrue(child_action.is_file())
                self.assertEqual(
                    observed_order.read_text(encoding="utf-8"),
                    "pid,mask,cancellation,gate",
                )

    def test_unregistered_cpython_thread_rejects_before_installation_or_fork(
        self,
    ) -> None:
        forked = self.root / "unexpected-fork"
        installation_access = self.root / "installation-accessed"
        driver = self.root / "unregistered_thread_driver.py"
        write_configured_driver(
            driver,
            INVOCATION_PROFILE_DRIVER_SOURCE,
            {
                "scenario": "unregistered-thread",
                "forked": str(forked),
                "installation_access": str(installation_access),
            },
        )
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-I",
                "-S",
                "-X",
                "disable-remote-debug",
                str(driver),
                str(CLIENT_SOURCE),
                str(installation_access),
                "validate",
                "--bundle",
                "/tmp/bundle",
            ],
            text=False,
            capture_output=True,
            check=False,
            env=CLIENT_ENVIRONMENT,
        )

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self.assertFalse(installation_access.exists())
        self.assertFalse(forked.exists())

    def test_json_boundaries_reject_numeric_type_aliases(self) -> None:
        receipt_cases = {
            "process-boolean-as-integer": lambda receipt: receipt[
                "process_profile"
            ].__setitem__("close_fds", 1),
            "process-integer-as-float": lambda receipt: receipt[
                "process_profile"
            ].__setitem__("validation_deadline_seconds", 60.0),
            "file-length-as-float": lambda receipt: receipt["control_set"][
                "client"
            ].__setitem__(
                "length",
                float(receipt["control_set"]["client"]["length"]),
            ),
            "interpreter-version-as-float": lambda receipt: receipt["interpreter"][
                "version"
            ].__setitem__(
                "major",
                float(receipt["interpreter"]["version"]["major"]),
            ),
            "activation-lock-mode-as-float": lambda receipt: receipt[
                "activation_lock"
            ].__setitem__(
                "mode",
                float(receipt["activation_lock"]["mode"]),
            ),
        }
        for name, mutate in receipt_cases.items():
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                receipt = json.loads(fixture.deployment.read_bytes())
                receipt.pop("content_sha256")
                mutate(receipt)
                fixture.deployment.write_bytes(
                    canonical_document(document(receipt)),
                )

                result = fixture.invoke(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                )

                self.assertEqual(result.returncode, 70, result.stderr.decode())
                self.assertEqual(result.stdout, b"")

    def test_canonical_receipt_accepts_exact_contract_and_rejects_stage_or_drift(
        self,
    ) -> None:
        control = ValidInvocationFixture(self.root / "canonical-control")
        control_result = control.invoke(
            "validate",
            "--bundle",
            str(control.bundle),
        )

        self.assertEqual(control_result.returncode, 0, control_result.stderr.decode())
        self.assertEqual(control_result.stderr, b"")
        self.assertEqual(control_result.stdout, control.envelope_raw)

        cases = {
            "legacy-stage-contract": lambda receipt: receipt.__setitem__(
                "contract", "task-witness-client-stage-receipt-v1"
            ),
            "legacy-deployment-contract": lambda receipt: receipt.update(
                schema_version=1,
                contract="task-witness-deployment-receipt-v1",
            ),
            "missing-canonical-binding": lambda receipt: receipt.pop("trust_context"),
            "unknown-future-binding": lambda receipt: receipt.__setitem__(
                "source_selection", {"mode": "exact_release"}
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                receipt = json.loads(fixture.deployment.read_bytes())
                receipt.pop("content_sha256")
                mutate(receipt)
                fixture.deployment.write_bytes(canonical_document(document(receipt)))

                result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

                self.assertEqual(result.returncode, 70, result.stderr.decode())
                self.assertEqual(result.stdout, b"")

        anchor_cases = {
            "anchor-boolean-as-integer": lambda anchor: anchor.__setitem__(
                "historical",
                0,
            ),
            "anchor-version-as-float": lambda anchor: anchor["interpreter"][
                "version"
            ].__setitem__(
                "major",
                float(anchor["interpreter"]["version"]["major"]),
            ),
        }
        for name, mutate in anchor_cases.items():
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                envelope = json.loads(fixture.envelope_raw)
                mutate(envelope["anchor"])
                fixture.replace_launcher_envelope(envelope)

                result = fixture.invoke(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                )

                self.assertEqual(result.returncode, 65, result.stderr.decode())
                self.assertEqual(result.stdout, b"")

    def test_valid_large_envelope_within_stdout_limit_is_accepted(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        envelope = {
            **fixture.envelope,
            "witness": {
                **fixture.witness,
                "projection": {
                    "contract": "fixture-projection-v1",
                    "padding": "x" * (1024 * 1024),
                },
            },
        }
        raw = canonical_document(envelope)
        self.assertGreater(len(raw), 1024 * 1024)
        self.assertLess(len(raw), 4 * 1024 * 1024)
        fixture.replace_launcher_envelope(envelope)

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, raw)

    def test_stdout_limit_is_inclusive_for_one_valid_canonical_envelope(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        envelope = {
            **fixture.envelope,
            "witness": {
                **fixture.witness,
                "projection": {
                    "contract": "fixture-projection-v1",
                    "padding": "",
                },
            },
        }
        limit = 4 * 1024 * 1024
        padding_length = limit - len(canonical_document(envelope))
        envelope["witness"]["projection"]["padding"] = "x" * padding_length
        expected = canonical_document(envelope)
        self.assertEqual(len(expected), limit)
        envelope["witness"]["projection"]["padding"] = ""
        install_launcher_behavior(
            fixture,
            "canonical_padding",
            envelope=envelope,
            padding_length=padding_length,
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            timeout=10,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, expected)

    def test_keyboard_interrupt_uses_the_resource_exit_class(self) -> None:
        for phase, target in (
            ("process-preflight", "_canonical_client_process"),
            ("validation", "_validate"),
        ):
            with self.subTest(phase=phase):
                driver = self.root / f"keyboard_interrupt_{phase}_driver.py"
                write_configured_driver(
                    driver,
                    INVOCATION_PROFILE_DRIVER_SOURCE,
                    {
                        "scenario": "keyboard-interrupt",
                        "target": target,
                    },
                )

                result = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        "-I",
                        "-S",
                        "-X",
                        "disable-remote-debug",
                        str(driver),
                        str(CLIENT_SOURCE),
                        str(self.root),
                        "validate",
                        "--bundle",
                        "/tmp/bundle",
                    ],
                    text=False,
                    capture_output=True,
                    check=False,
                    env=CLIENT_ENVIRONMENT,
                    timeout=5,
                )

                self.assertEqual(result.returncode, 124, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                if result.stderr:
                    self.assert_diagnostic(
                        result.stderr,
                        message="validation interrupted",
                        validator_code_executed="no",
                        active_state_changed="no",
                        current_receipt="unknown",
                        next_action=(
                            "do not retry; verify validator termination and active state"
                        ),
                    )

    def test_cancellation_handlers_precede_process_preflight(self) -> None:
        for signal_name in ("SIGTERM", "SIGHUP"):
            with self.subTest(signal=signal_name):
                driver = self.root / f"preflight_{signal_name.lower()}_driver.py"
                write_configured_driver(
                    driver,
                    INVOCATION_PROFILE_DRIVER_SOURCE,
                    {
                        "scenario": "preflight-cancellation",
                        "main_argv_start": 4,
                        "signal_name": signal_name,
                    },
                )

                result = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        "-I",
                        "-S",
                        "-X",
                        "disable-remote-debug",
                        str(driver),
                        str(CLIENT_SOURCE),
                        str(self.root),
                        signal_name,
                        "validate",
                        "--bundle",
                        "/tmp/bundle",
                    ],
                    text=False,
                    capture_output=True,
                    check=False,
                    env=CLIENT_ENVIRONMENT,
                    timeout=5,
                )

                self.assertEqual(result.returncode, 124, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                self.assert_diagnostic(
                    result.stderr,
                    message="validation interrupted",
                    validator_code_executed="no",
                    active_state_changed="no",
                    current_receipt="unknown",
                    next_action="do not retry; verify validator termination and active state",
                )

    def test_canonical_root_comes_from_the_effective_users_passwd_entry(self) -> None:
        driver = self.root / "passwd_root_driver.py"
        write_configured_driver(
            driver,
            INVOCATION_PROFILE_DRIVER_SOURCE,
            {"scenario": "passwd-root"},
        )

        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-I",
                "-S",
                "-X",
                "disable-remote-debug",
                str(driver),
                str(CLIENT_SOURCE),
            ],
            text=True,
            capture_output=True,
            check=False,
            env={**CLIENT_ENVIRONMENT, "HOME": "/attacker/home"},
            timeout=5,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "/passwd/home/.local/libexec/task-witness\n",
        )

    def test_missing_runtime_payload_never_reaches_the_launcher(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        marker = self.root / "launcher-ran"
        install_launcher_behavior(
            fixture,
            "marker_output",
            marker=marker,
            output=fixture.envelope_raw,
        )
        (fixture.runtime_generation / "trust.py").unlink()

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
        )

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertFalse(marker.exists())

    def test_receipt_bound_shim_with_wrong_mode_never_reaches_the_launcher(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        fixture.shim.chmod(0o700)
        marker = self.root / "launcher-ran"
        install_launcher_behavior(
            fixture,
            "marker_output",
            marker=marker,
            output=fixture.envelope_raw,
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
        )

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertFalse(marker.exists())

    def test_installation_directories_require_exact_mode_0700(self) -> None:
        for name, select_target in (
            ("canonical-root", lambda fixture: fixture.install),
            ("nested-client-directory", lambda fixture: fixture.client.parent),
            ("nested-launcher-directory", lambda fixture: fixture.launcher.parent),
            (
                "runtime-generations-directory",
                lambda fixture: fixture.generation_directory,
            ),
            ("active-runtime-generation", lambda fixture: fixture.runtime_generation),
            ("trust-directory", lambda fixture: fixture.context_directory.parent),
            ("trust-context-directory", lambda fixture: fixture.context_directory),
            ("validator-directory", lambda fixture: fixture.validator_directory),
            (
                "retained-validator-generation",
                lambda fixture: fixture.validator_module.parent,
            ),
        ):
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                select_target(fixture).chmod(0o500)

                result = fixture.invoke(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                )

                self.assertEqual(result.returncode, 70, result.stderr.decode())
                self.assertEqual(result.stdout, b"")

    def test_owner_private_bundle_does_not_require_installation_mode(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        fixture.bundle.chmod(0o500)

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, fixture.envelope_raw)

    def test_bundle_rejects_group_or_other_permissions(self) -> None:
        for name, select_target in (
            ("directory", lambda fixture: fixture.bundle),
            ("child", lambda fixture: fixture.bundle_manifest),
        ):
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                target = select_target(fixture)
                target.chmod((target.stat().st_mode & 0o777) | 0o040)

                result = fixture.invoke(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                )

                self.assertEqual(result.returncode, 70, result.stderr.decode())
                self.assertEqual(result.stdout, b"")

    def test_bundle_rejects_macos_allow_acl_before_launcher(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("macOS ACL semantics required")
        fixture = ValidInvocationFixture(self.root)
        marker = self.root / "launcher-ran"
        install_launcher_behavior(
            fixture,
            "marker_output",
            marker=marker,
            output=fixture.envelope_raw,
        )
        subprocess.run(
            ["/bin/chmod", "+a", "everyone allow read", str(fixture.bundle_manifest)],
            check=True,
        )
        try:
            result = fixture.invoke(
                "validate",
                "--bundle",
                str(fixture.bundle),
            )
        finally:
            subprocess.run(
                ["/bin/chmod", "-N", str(fixture.bundle_manifest)], check=True
            )

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertFalse(marker.exists())

    def test_bundle_accepts_macos_deny_only_acl(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("macOS ACL semantics required")
        fixture = ValidInvocationFixture(self.root)
        subprocess.run(
            ["/bin/chmod", "+a", "everyone deny write", str(fixture.bundle_manifest)],
            check=True,
        )
        try:
            result = fixture.invoke(
                "validate",
                "--bundle",
                str(fixture.bundle),
            )
        finally:
            subprocess.run(
                ["/bin/chmod", "-N", str(fixture.bundle_manifest)], check=True
            )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, fixture.envelope_raw)

    def test_bundle_rejects_inherited_macos_allow_acl_before_launcher(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("macOS ACL semantics required")
        fixture = ValidInvocationFixture(self.root / "fixture")
        acl_parent = self.root / "acl-parent"
        acl_parent.mkdir()
        acl_parent.chmod(0o700)
        subprocess.run(
            [
                "/bin/chmod",
                "+a",
                "everyone allow read,file_inherit,directory_inherit",
                str(acl_parent),
            ],
            check=True,
        )
        inherited_bundle = acl_parent / "bundle"
        try:
            inherited_bundle.mkdir()
            inherited_bundle.chmod(0o700)
            for source in fixture.bundle.iterdir():
                target = inherited_bundle / source.name
                shutil.copyfile(source, target)
                target.chmod(0o600)
            module = load_client_module("task_witness_client_inherited_acl_fixture")
            descriptor = os.open(inherited_bundle / "manifest.json", os.O_RDONLY)
            try:
                self.assertTrue(module._macos_descriptor_has_allow_acl(descriptor))
            finally:
                os.close(descriptor)
            marker = self.root / "launcher-ran"
            install_launcher_behavior(
                fixture,
                "marker_output",
                marker=marker,
                output=fixture.envelope_raw,
            )

            result = fixture.invoke(
                "validate",
                "--bundle",
                str(inherited_bundle),
            )
        finally:
            subprocess.run(["/bin/chmod", "-RN", str(acl_parent)], check=True)

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertFalse(marker.exists())

    def test_acl_lookup_failure_rejects_private_node(self) -> None:
        module = load_client_module("task_witness_client_acl_failure_fixture")
        with (
            mock.patch.object(
                module,
                "_macos_descriptor_has_allow_acl",
                side_effect=OSError("unavailable"),
            ),
            self.assertRaisesRegex(ValueError, "ACL cannot be verified"),
        ):
            module._reject_macos_allow_acl(17, "private node")

    def test_bundle_inside_canonical_installation_never_reaches_launcher(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        nested_bundle = fixture.install / "caller-bundle"
        fixture.bundle.rename(nested_bundle)
        marker = self.root / "launcher-ran"
        install_launcher_behavior(
            fixture,
            "marker_output",
            marker=marker,
            output=fixture.envelope_raw,
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(nested_bundle),
        )

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertFalse(marker.exists())

    def test_bundle_hard_link_to_installation_never_reaches_launcher(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        os.link(fixture.active_path, fixture.bundle / "active-copy.json")
        marker = self.root / "launcher-ran"
        install_launcher_behavior(
            fixture,
            "marker_output",
            marker=marker,
            output=fixture.envelope_raw,
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
        )

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertFalse(marker.exists())

    def test_shared_interpreter_parent_does_not_require_installation_mode(
        self,
    ) -> None:
        interpreter_directory = self.root / "shared-bin"
        interpreter_directory.mkdir()
        pinned_interpreter = interpreter_directory / "python"
        shutil.copy2(Path(sys.executable).resolve(), pinned_interpreter)
        interpreter_directory.chmod(0o755)
        fixture = ValidInvocationFixture(
            self.root / "fixture",
            interpreter_executable=pinned_interpreter,
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, fixture.envelope_raw)
