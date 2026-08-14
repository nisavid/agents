from __future__ import annotations

import fcntl
import hashlib
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ._activation_support import (
    INSTALL_PROCESS_LOSS_CUTS,
    AbsentFd3Binding,
    ActivationCutRecorder,
    CallerFd3Binding,
    DirectSmokeChildBoundary,
    ExclusiveLockBoundary,
    FirstInstallActivationFixture,
    IndependentActivationLockHolder,
    InjectedActivationCrash,
    SmokeChildBoundary,
    activation_install_temporary_path,
    assert_exclusive_lock,
    assert_process_reaped,
    canonical_value,
    close_locked_activation_root,
    exact_restored_absent_inventory,
    expected_smoke_envelope,
    filesystem_identity,
    ordered_activation_artifacts,
    process_descriptor_inventory,
    real_smoke_output_command,
    replay_pending_install,
    run_activation_install_process_loss_cut,
    tree_inventory,
)
from ._support import (
    canonical_bytes,
    canonical_document,
    load_deployment_module,
    sha256,
)


class ActivationTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.fixture = FirstInstallActivationFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def deployment(self):
        return load_deployment_module()

    def test_deployment_receipt_binds_exclusive_lock_deadline(self) -> None:
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        receipt = canonical_value(prepared.staged.deployment_raw)

        self.assertEqual(receipt["process_profile"], deployment.PROCESS_PROFILE)
        self.assertEqual(
            receipt["process_profile"]["exclusive_lock_seconds"],
            65,
        )

    def test_exclusive_lock_deadline_closes_descriptors_under_contention(
        self,
    ) -> None:
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        precondition = deployment.prepare_first_install(
            prepared.request
        ).plan.precondition
        tree_before = tree_inventory(prepared.canonical_root)
        root_before = filesystem_identity(prepared.canonical_root)
        lock_before = filesystem_identity(prepared.activation_lock)

        for name, operation in (
            ("shared", fcntl.LOCK_SH),
            ("exclusive", fcntl.LOCK_EX),
        ):
            with (
                self.subTest(holder=name),
                IndependentActivationLockHolder(
                    prepared.activation_lock,
                    operation,
                    hold_seconds=None,
                ),
            ):
                descriptors_before = process_descriptor_inventory()
                started = time.monotonic()
                with (
                    mock.patch.dict(
                        deployment.PROCESS_PROFILE,
                        {"exclusive_lock_seconds": 0.04},
                    ),
                    self.assertRaisesRegex(
                        deployment.DeploymentError,
                        "^activation lock exclusive acquisition timed out$",
                    ),
                ):
                    opened = deployment._open_locked_activation_root(precondition)
                    close_locked_activation_root(opened)
                elapsed = time.monotonic() - started

                self.assertLess(elapsed, 0.2)
                self.assertEqual(
                    process_descriptor_inventory(),
                    descriptors_before,
                )
                self.assertEqual(
                    tree_inventory(prepared.canonical_root),
                    tree_before,
                )
                self.assertEqual(
                    filesystem_identity(prepared.canonical_root),
                    root_before,
                )
                self.assertEqual(
                    filesystem_identity(prepared.activation_lock),
                    lock_before,
                )

    def test_activation_lock_deadline_prevents_all_locked_activation_work(
        self,
    ) -> None:
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        request = self.fixture.activation_request(prepared)
        tree_before = tree_inventory(prepared.canonical_root)
        root_before = filesystem_identity(prepared.canonical_root)
        lock_before = filesystem_identity(prepared.activation_lock)
        locked_boundaries = {
            name: mock.Mock(
                side_effect=AssertionError(f"{name} must not run before exclusivity")
            )
            for name in (
                "prepare_first_install",
                "stage_first_install",
                "_validate_first_install_authorization",
                "verify_deployment_stage",
                "_write_activation_journal",
                "_install_activation_artifact",
                "_spawn_activation_smoke_child",
            )
        }

        with IndependentActivationLockHolder(
            prepared.activation_lock,
            fcntl.LOCK_EX,
            hold_seconds=None,
        ):
            descriptors_before = process_descriptor_inventory()
            started = time.monotonic()
            with (
                mock.patch.object(
                    deployment,
                    "prepare_first_install",
                    locked_boundaries["prepare_first_install"],
                ),
                mock.patch.object(
                    deployment,
                    "stage_first_install",
                    locked_boundaries["stage_first_install"],
                ),
                mock.patch.object(
                    deployment,
                    "_validate_first_install_authorization",
                    locked_boundaries["_validate_first_install_authorization"],
                ),
                mock.patch.object(
                    deployment,
                    "verify_deployment_stage",
                    locked_boundaries["verify_deployment_stage"],
                ),
                mock.patch.object(
                    deployment,
                    "_write_activation_journal",
                    locked_boundaries["_write_activation_journal"],
                ),
                mock.patch.object(
                    deployment,
                    "_install_activation_artifact",
                    locked_boundaries["_install_activation_artifact"],
                ),
                mock.patch.object(
                    deployment,
                    "_spawn_activation_smoke_child",
                    locked_boundaries["_spawn_activation_smoke_child"],
                ),
                mock.patch.dict(
                    deployment.PROCESS_PROFILE,
                    {"exclusive_lock_seconds": 0.04},
                ),
                self.assertRaisesRegex(
                    deployment.DeploymentError,
                    "^activation lock exclusive acquisition timed out$",
                ),
            ):
                deployment.activate_staged(request)
            elapsed = time.monotonic() - started

            self.assertLess(elapsed, 0.2)
            self.assertEqual(
                process_descriptor_inventory(),
                descriptors_before,
            )
            self.assertEqual(
                tree_inventory(prepared.canonical_root),
                tree_before,
            )
            self.assertEqual(
                filesystem_identity(prepared.canonical_root),
                root_before,
            )
            self.assertEqual(
                filesystem_identity(prepared.activation_lock),
                lock_before,
            )
        for name, boundary in locked_boundaries.items():
            self.assertEqual(boundary.call_count, 0, name)

    def test_exclusive_lock_acquisition_at_expiry_fails_closed(self) -> None:
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        precondition = deployment.prepare_first_install(
            prepared.request
        ).plan.precondition
        descriptors_before = process_descriptor_inventory()
        real_flock = fcntl.flock
        attempts = []

        def acquire_at_deadline(descriptor: int, operation: int) -> None:
            attempts.append(operation)
            real_flock(descriptor, operation)

        with (
            mock.patch.dict(
                deployment.PROCESS_PROFILE,
                {"exclusive_lock_seconds": 0.1},
            ),
            mock.patch.object(
                deployment.time,
                "monotonic",
                side_effect=(100.0, 100.0, 100.1),
            ),
            mock.patch.object(
                deployment.fcntl,
                "flock",
                side_effect=acquire_at_deadline,
            ),
            self.assertRaisesRegex(
                deployment.DeploymentError,
                "^activation lock exclusive acquisition timed out$",
            ),
        ):
            opened = deployment._open_locked_activation_root(precondition)
            close_locked_activation_root(opened)

        self.assertEqual(attempts, [fcntl.LOCK_EX | fcntl.LOCK_NB])
        self.assertEqual(process_descriptor_inventory(), descriptors_before)

    def test_exclusive_lock_contention_succeeds_when_holder_releases(self) -> None:
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        precondition = deployment.prepare_first_install(
            prepared.request
        ).plan.precondition
        tree_before = tree_inventory(prepared.canonical_root)
        root_before = filesystem_identity(prepared.canonical_root)
        lock_before = filesystem_identity(prepared.activation_lock)

        with IndependentActivationLockHolder(
            prepared.activation_lock,
            fcntl.LOCK_SH,
            hold_seconds=0.05,
        ):
            descriptors_before = process_descriptor_inventory()
            started = time.monotonic()
            with mock.patch.dict(
                deployment.PROCESS_PROFILE,
                {"exclusive_lock_seconds": 0.5},
            ):
                opened = deployment._open_locked_activation_root(precondition)
            elapsed = time.monotonic() - started
            try:
                assert_exclusive_lock(prepared.activation_lock)
            finally:
                close_locked_activation_root(opened)

            self.assertLess(elapsed, 0.4)
            self.assertEqual(
                process_descriptor_inventory(),
                descriptors_before,
            )
            self.assertEqual(
                tree_inventory(prepared.canonical_root),
                tree_before,
            )
            self.assertEqual(
                filesystem_identity(prepared.canonical_root),
                root_before,
            )
            self.assertEqual(
                filesystem_identity(prepared.activation_lock),
                lock_before,
            )

    def test_smoke_backup_exhaustion_preserves_benign_caller_fd3(self) -> None:
        deployment = self.deployment()
        caller = self.root / "caller-fd3"
        caller.write_bytes(b"benign caller descriptor\n")
        caller.chmod(0o600)
        activation_lock = self.root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        child = mock.Mock(side_effect=AssertionError("smoke child must not run"))
        with CallerFd3Binding(caller) as binding:
            lock_fd = activation_lock.open("r+b")
            try:
                with (
                    mock.patch.object(
                        deployment.os,
                        "dup",
                        side_effect=binding.fail_backup_dup,
                    ),
                    mock.patch.object(
                        deployment,
                        "_spawn_activation_smoke_child",
                        child,
                    ),
                    self.assertRaises(deployment.DeploymentError),
                ):
                    deployment._run_activation_smoke(self.root, lock_fd.fileno())
                self.assertEqual(binding.dup_attempts, 1)
                binding.assert_unchanged()
            finally:
                lock_fd.close()
        self.assertEqual(child.call_count, 0)

    def test_smoke_success_restores_present_caller_fd3_exactly(self) -> None:
        deployment = self.deployment()
        caller = self.root / "caller-fd3"
        caller.write_bytes(b"benign caller descriptor\n")
        caller.chmod(0o600)
        activation_lock = self.root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        child = DirectSmokeChildBoundary(self.root, activation_lock)
        with CallerFd3Binding(caller) as binding:
            lock_fd = activation_lock.open("r+b")
            try:
                with mock.patch.object(
                    deployment,
                    "_spawn_activation_smoke_child",
                    child,
                ):
                    result = deployment._run_activation_smoke(
                        self.root,
                        lock_fd.fileno(),
                    )
                binding.assert_unchanged()
            finally:
                lock_fd.close()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(child.calls, 1)

    def test_smoke_success_restores_absent_caller_fd3_exactly(self) -> None:
        deployment = self.deployment()
        caller = self.root / "fd3-guard"
        caller.write_bytes(b"guard\n")
        caller.chmod(0o600)
        activation_lock = self.root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        child = DirectSmokeChildBoundary(self.root, activation_lock)
        with CallerFd3Binding(caller):
            lock_fd = activation_lock.open("r+b")
            try:
                with AbsentFd3Binding() as absent:
                    with mock.patch.object(
                        deployment,
                        "_spawn_activation_smoke_child",
                        child,
                    ):
                        result = deployment._run_activation_smoke(
                            self.root,
                            lock_fd.fileno(),
                        )
                    absent.assert_absent()
            finally:
                lock_fd.close()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(child.calls, 1)

    def test_smoke_parent_rejects_stdout_limit_plus_one_and_reaps_child(
        self,
    ) -> None:
        deployment = self.deployment()
        pid_path = self.root / "stdout-overflow.pid"
        descendant_pid_path = self.root / "stdout-overflow-descendant.pid"
        linger_seconds = 2.0
        argv = real_smoke_output_command(
            pid_path,
            stdout_bytes=deployment.PROCESS_PROFILE["stdout_max_bytes"] + 1,
            stderr_bytes=0,
            linger_seconds=linger_seconds,
            descendant_pid_path=descendant_pid_path,
            ignore_sigterm=True,
        )

        started = time.monotonic()
        with mock.patch.dict(
            deployment.PROCESS_PROFILE,
            {"termination_grace_seconds": 0.1, "kill_reap_seconds": 1},
        ):
            result = deployment._spawn_activation_smoke_child(argv, pass_fds=())
        elapsed = time.monotonic() - started

        self.assertEqual(result.returncode, 124)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self.assertLess(elapsed, linger_seconds)
        assert_process_reaped(pid_path)
        assert_process_reaped(descendant_pid_path)

    def test_smoke_parent_rejects_stderr_limit_plus_one_and_reaps_child(
        self,
    ) -> None:
        deployment = self.deployment()
        pid_path = self.root / "stderr-overflow.pid"
        linger_seconds = 2.0
        argv = real_smoke_output_command(
            pid_path,
            stdout_bytes=0,
            stderr_bytes=deployment.PROCESS_PROFILE["stderr_max_bytes"] + 1,
            linger_seconds=linger_seconds,
            ignore_sigterm=True,
        )

        started = time.monotonic()
        with mock.patch.dict(
            deployment.PROCESS_PROFILE,
            {"termination_grace_seconds": 0.1, "kill_reap_seconds": 1},
        ):
            result = deployment._spawn_activation_smoke_child(argv, pass_fds=())
        elapsed = time.monotonic() - started

        self.assertEqual(result.returncode, 124)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self.assertLess(elapsed, linger_seconds)
        assert_process_reaped(pid_path)

    def test_smoke_parent_accepts_each_stream_at_its_exact_limit(self) -> None:
        deployment = self.deployment()
        cases = (
            (
                "stdout",
                deployment.PROCESS_PROFILE["stdout_max_bytes"],
                0,
            ),
            (
                "stderr",
                0,
                deployment.PROCESS_PROFILE["stderr_max_bytes"],
            ),
            (
                "both",
                deployment.PROCESS_PROFILE["stdout_max_bytes"],
                deployment.PROCESS_PROFILE["stderr_max_bytes"],
            ),
        )
        for name, stdout_bytes, stderr_bytes in cases:
            with self.subTest(stream=name):
                pid_path = self.root / f"{name}-at-limit.pid"
                argv = real_smoke_output_command(
                    pid_path,
                    stdout_bytes=stdout_bytes,
                    stderr_bytes=stderr_bytes,
                )

                result = deployment._spawn_activation_smoke_child(
                    argv,
                    pass_fds=(),
                )

                self.assertEqual(result.returncode, 0)
                self.assertEqual(len(result.stdout), stdout_bytes)
                self.assertEqual(len(result.stderr), stderr_bytes)
                assert_process_reaped(pid_path)

    def test_smoke_parent_preserves_ordinary_real_child_success(self) -> None:
        deployment = self.deployment()
        pid_path = self.root / "ordinary-smoke.pid"
        argv = real_smoke_output_command(
            pid_path,
            stdout_bytes=17,
            stderr_bytes=0,
        )

        result = deployment._spawn_activation_smoke_child(argv, pass_fds=())

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"x" * 17)
        self.assertEqual(result.stderr, b"")
        assert_process_reaped(pid_path)

    def test_smoke_parent_timeout_reaps_the_exact_process_group(self) -> None:
        deployment = self.deployment()
        pid_path = self.root / "timeout-smoke.pid"
        descendant_pid_path = self.root / "timeout-smoke-descendant.pid"
        linger_seconds = 2.0
        argv = real_smoke_output_command(
            pid_path,
            stdout_bytes=0,
            stderr_bytes=0,
            linger_seconds=linger_seconds,
            descendant_pid_path=descendant_pid_path,
        )

        started = time.monotonic()
        with mock.patch.dict(
            deployment.PROCESS_PROFILE,
            {
                "validation_deadline_seconds": 0.1,
                "termination_grace_seconds": 0.1,
                "kill_reap_seconds": 1,
            },
        ):
            result = deployment._spawn_activation_smoke_child(argv, pass_fds=())
        elapsed = time.monotonic() - started

        self.assertEqual(result.returncode, 124)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self.assertLess(elapsed, linger_seconds)
        assert_process_reaped(pid_path)
        assert_process_reaped(descendant_pid_path)

    def test_first_install_activation_is_journaled_and_installs_the_shim_last(
        self,
    ) -> None:
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        request = self.fixture.activation_request(prepared)
        envelope_raw = expected_smoke_envelope(prepared.staged)
        child = SmokeChildBoundary(prepared, envelope_raw)
        cuts = ActivationCutRecorder(prepared.canonical_root)
        write_journal = deployment._write_activation_journal
        install_artifact = deployment._install_activation_artifact
        unlink_journal = deployment._unlink_activation_journal
        reprepare = ExclusiveLockBoundary(
            prepared.activation_lock,
            deployment.prepare_first_install,
        )
        reauthorize = ExclusiveLockBoundary(
            prepared.activation_lock,
            deployment._validate_first_install_authorization,
        )
        reverify_stage = ExclusiveLockBoundary(
            prepared.activation_lock,
            deployment.verify_deployment_stage,
        )

        with (
            mock.patch.object(
                deployment,
                "prepare_first_install",
                side_effect=reprepare,
            ),
            mock.patch.object(
                deployment,
                "_validate_first_install_authorization",
                side_effect=reauthorize,
            ),
            mock.patch.object(
                deployment,
                "verify_deployment_stage",
                side_effect=reverify_stage,
            ),
            mock.patch.object(
                deployment,
                "_write_activation_journal",
                side_effect=cuts.wrap_journal_writer(write_journal),
            ),
            mock.patch.object(
                deployment,
                "_install_activation_artifact",
                side_effect=cuts.wrap_artifact_installer(install_artifact),
            ),
            mock.patch.object(
                deployment,
                "_spawn_activation_smoke_child",
                child,
            ),
            mock.patch.object(
                deployment,
                "_unlink_activation_journal",
                side_effect=cuts.wrap_journal_unlinker(unlink_journal),
            ),
        ):
            result = deployment.activate_staged(request)

        candidate_sha256 = sha256(prepared.staged.deployment_raw)
        self.assertIsInstance(result, deployment.TransactionResult)
        self.assertEqual(result.outcome, "candidate-active")
        self.assertEqual(result.candidate_receipt_sha256, candidate_sha256)
        self.assertEqual(result.active_receipt_sha256, candidate_sha256)
        self.assertEqual(result.accepted_envelope_sha256, sha256(envelope_raw))
        self.assertEqual(result.journal_sha256, sha256(result.journal_raw))
        self.assertEqual(canonical_document(result.journal_value), result.journal_raw)
        self.assertEqual(result.journal_value["phase"], "terminal")
        self.assertEqual(
            result.journal_value["terminal_result"]["outcome"],
            "candidate-active",
        )
        self.assertEqual(child.calls, 1)
        self.assertGreaterEqual(reprepare.calls, 1)
        self.assertGreaterEqual(reauthorize.calls, 1)
        self.assertGreaterEqual(reverify_stage.calls, 1)

        installed_by_path = {
            item.relative_path: item for item in prepared.verified.artifacts
        }
        for relative_path, artifact in installed_by_path.items():
            installed = prepared.canonical_root / relative_path
            self.assertEqual(installed.read_bytes(), artifact.raw)
            self.assertEqual(
                hashlib.sha256(installed.read_bytes()).hexdigest(),
                artifact.installed["sha256"],
            )
        artifact_events = [
            event
            for event in cuts.events
            if event.startswith("artifact:") and event.endswith(":after")
        ]
        self.assertTrue(artifact_events)
        self.assertEqual(artifact_events[-1], "artifact:shim:after")
        self.assertTrue(
            all(
                journal["pending_step"] is not None
                for journal in cuts.journals
                if journal["phase"] == "control-installing"
            ),
            "every control-installing generation must retain an exact mutation cursor",
        )
        self.assertLess(
            cuts.events.index("journal:control-installing:durable"),
            cuts.events.index(artifact_events[0]),
        )
        self.assertLess(
            cuts.events.index("journal:terminal:durable"),
            cuts.events.index("journal:before-unlink"),
        )
        self.assertIn(
            "transaction.json",
            {item[0] for item in cuts.snapshots["journal:terminal:durable"]},
        )
        self.assertNotIn(
            "transaction.json",
            {item[0] for item in tree_inventory(prepared.canonical_root)},
        )
        accepted_journal_index, accepted_journal = next(
            (index, journal)
            for index, journal in enumerate(cuts.journals)
            if journal["candidate_smoke_acceptance"] is not None
        )
        acceptance = accepted_journal["candidate_smoke_acceptance"]
        self.assertEqual(
            set(acceptance),
            {
                "phase",
                "target_deployment_receipt_sha256",
                "expected_envelope_sha256",
                "accepted_envelope_sha256",
                "exit_status",
                "content_sha256",
            },
        )
        self.assertEqual(acceptance["phase"], "candidate-smoke")
        self.assertEqual(
            acceptance["target_deployment_receipt_sha256"],
            candidate_sha256,
        )
        self.assertEqual(acceptance["expected_envelope_sha256"], sha256(envelope_raw))
        self.assertEqual(acceptance["accepted_envelope_sha256"], sha256(envelope_raw))
        self.assertEqual(acceptance["exit_status"], 0)
        acceptance_unsigned = {
            key: value for key, value in acceptance.items() if key != "content_sha256"
        }
        self.assertEqual(
            acceptance["content_sha256"],
            sha256(canonical_bytes(acceptance_unsigned)),
        )
        candidate_accepted_index = next(
            index
            for index, journal in enumerate(cuts.journals)
            if journal["phase"] == "candidate-accepted"
        )
        terminal_index = next(
            index
            for index, journal in enumerate(cuts.journals)
            if journal["phase"] == "terminal"
        )
        self.assertLess(accepted_journal_index, candidate_accepted_index)
        self.assertLess(candidate_accepted_index, terminal_index)
        self.assertIsNone(accepted_journal["rollback_smoke_acceptance"])

    def test_crash_before_first_artifact_write_retains_exact_pending_journal(
        self,
    ) -> None:
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        request = self.fixture.activation_request(prepared)
        cuts = ActivationCutRecorder(
            prepared.canonical_root,
            crash_before_first_artifact=True,
        )
        child = mock.Mock(side_effect=AssertionError("smoke must not run"))
        write_journal = deployment._write_activation_journal
        install_artifact = deployment._install_activation_artifact

        with (
            mock.patch.object(
                deployment,
                "_write_activation_journal",
                side_effect=cuts.wrap_journal_writer(write_journal),
            ),
            mock.patch.object(
                deployment,
                "_install_activation_artifact",
                side_effect=cuts.wrap_artifact_installer(install_artifact),
            ),
            mock.patch.object(
                deployment,
                "_spawn_activation_smoke_child",
                child,
            ),
            self.assertRaises(InjectedActivationCrash),
        ):
            deployment.activate_staged(request)

        self.assertEqual(child.call_count, 0)
        first_artifact = next(
            event
            for event in cuts.events
            if event.startswith("artifact:") and event.endswith(":before")
        )
        before = cuts.snapshots[first_artifact]
        self.assertEqual(
            {item[0] for item in before}, {"activation.lock", "transaction.json"}
        )
        journal_raw = (prepared.canonical_root / "transaction.json").read_bytes()
        journal = canonical_value(journal_raw)
        self.assertEqual(journal["phase"], "control-installing")
        self.assertIsNotNone(journal["pending_step"])
        self.assertEqual(journal["smoke_handoff"], None)

    def test_crash_after_artifact_write_retains_an_idempotent_replay_cursor(
        self,
    ) -> None:
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        request = self.fixture.activation_request(prepared)
        cuts = ActivationCutRecorder(
            prepared.canonical_root,
            crash_after_first_artifact=True,
        )
        child = mock.Mock(side_effect=AssertionError("smoke must not run"))
        write_journal = deployment._write_activation_journal
        install_artifact = deployment._install_activation_artifact

        with (
            mock.patch.object(
                deployment,
                "_write_activation_journal",
                side_effect=cuts.wrap_journal_writer(write_journal),
            ),
            mock.patch.object(
                deployment,
                "_install_activation_artifact",
                side_effect=cuts.wrap_artifact_installer(install_artifact),
            ),
            mock.patch.object(
                deployment,
                "_spawn_activation_smoke_child",
                child,
            ),
            self.assertRaises(InjectedActivationCrash),
        ):
            deployment.activate_staged(request)

        self.assertEqual(child.call_count, 0)
        journal = canonical_value(
            (prepared.canonical_root / "transaction.json").read_bytes()
        )
        self.assertEqual(journal["phase"], "control-installing")
        pending = journal["pending_step"]
        self.assertIsNotNone(pending)
        self.assertEqual(pending["operation"], "install")

        ordered = ordered_activation_artifacts(prepared)
        artifact = ordered[pending["index"]]
        self.assertEqual(artifact.role, pending["role"])
        before_replay = tree_inventory(prepared.canonical_root)
        replay_pending_install(deployment, prepared, journal)
        self.assertEqual(tree_inventory(prepared.canonical_root), before_replay)

    def test_pending_install_replays_after_every_publish_process_loss_cut(
        self,
    ) -> None:
        deployment = self.deployment()
        for cut in INSTALL_PROCESS_LOSS_CUTS:
            with self.subTest(cut=cut), tempfile.TemporaryDirectory() as location:
                fixture = FirstInstallActivationFixture(Path(location).resolve())
                prepared = fixture.prepare()
                request = fixture.activation_request(prepared)

                run_activation_install_process_loss_cut(deployment, request, cut)

                journal = canonical_value(
                    (prepared.canonical_root / "transaction.json").read_bytes()
                )
                self.assertEqual(journal["phase"], "control-installing")
                pending = journal["pending_step"]
                self.assertIsNotNone(pending)
                self.assertEqual(pending["operation"], "install")
                artifact = ordered_activation_artifacts(prepared)[pending["index"]]
                self.assertEqual(artifact.role, pending["role"])
                temporary = activation_install_temporary_path(
                    artifact,
                    journal["transaction_id"],
                    pending["index"],
                )
                final = artifact.installed_path

                if cut in {"temp-create", "partial-write", "file-fsync"}:
                    self.assertFalse(final.exists())
                    self.assertTrue(temporary.is_file())
                    if cut == "temp-create":
                        self.assertEqual(temporary.stat().st_size, 0)
                        self.assertEqual(
                            stat.S_IMODE(temporary.stat().st_mode),
                            0,
                        )
                    elif cut == "partial-write":
                        temporary_raw = temporary.read_bytes()
                        self.assertEqual(
                            temporary_raw,
                            artifact.raw[: max(1, len(artifact.raw) // 2)],
                        )
                        self.assertEqual(
                            stat.S_IMODE(temporary.stat().st_mode),
                            0o600,
                        )
                    else:
                        temporary_raw = temporary.read_bytes()
                        self.assertEqual(temporary_raw, artifact.raw)
                        self.assertEqual(
                            stat.S_IMODE(temporary.stat().st_mode),
                            0o600,
                        )
                    self.assertEqual(temporary.stat().st_nlink, 1)
                elif cut == "link-finalization":
                    temporary_metadata = temporary.stat()
                    final_metadata = final.stat()
                    self.assertEqual(
                        (temporary_metadata.st_dev, temporary_metadata.st_ino),
                        (final_metadata.st_dev, final_metadata.st_ino),
                    )
                    self.assertEqual(temporary_metadata.st_nlink, 2)
                    self.assertEqual(final_metadata.st_nlink, 2)
                    self.assertEqual(final.read_bytes(), artifact.raw)
                else:
                    self.assertFalse(temporary.exists())
                    self.assertEqual(final.read_bytes(), artifact.raw)
                    self.assertEqual(final.stat().st_nlink, 1)

                replayed = replay_pending_install(deployment, prepared, journal)
                self.assertEqual(replayed.role, pending["role"])
                self.assertFalse(temporary.exists())
                self.assertEqual(final.read_bytes(), artifact.raw)
                final_metadata = final.stat()
                self.assertTrue(stat.S_ISREG(final_metadata.st_mode))
                self.assertEqual(final_metadata.st_uid, artifact.installed["owner"])
                self.assertEqual(
                    stat.S_IMODE(final_metadata.st_mode),
                    artifact.installed["mode"],
                )
                self.assertEqual(final_metadata.st_nlink, 1)

                before_second_replay = tree_inventory(prepared.canonical_root)
                replay_pending_install(deployment, prepared, journal)
                self.assertEqual(
                    tree_inventory(prepared.canonical_root),
                    before_second_replay,
                )

    def test_pending_install_never_overwrites_an_unrelated_final_entry(
        self,
    ) -> None:
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        request = self.fixture.activation_request(prepared)
        run_activation_install_process_loss_cut(deployment, request, "temp-create")
        journal = canonical_value(
            (prepared.canonical_root / "transaction.json").read_bytes()
        )
        pending = journal["pending_step"]
        artifact = ordered_activation_artifacts(prepared)[pending["index"]]
        temporary = activation_install_temporary_path(
            artifact,
            journal["transaction_id"],
            pending["index"],
        )
        final = artifact.installed_path
        unrelated_raw = b"unrelated activation entry\n"
        final.write_bytes(unrelated_raw)
        final.chmod(artifact.installed["mode"])
        final_before = final.stat()
        temporary_before = temporary.stat()

        with self.assertRaises(deployment.DeploymentError):
            replay_pending_install(deployment, prepared, journal)

        final_after = final.stat()
        temporary_after = temporary.stat()
        self.assertEqual(final.read_bytes(), unrelated_raw)
        self.assertEqual(
            (final_after.st_dev, final_after.st_ino),
            (final_before.st_dev, final_before.st_ino),
        )
        self.assertEqual(
            (
                temporary_after.st_dev,
                temporary_after.st_ino,
                temporary_after.st_mode,
                temporary_after.st_nlink,
                temporary_after.st_size,
            ),
            (
                temporary_before.st_dev,
                temporary_before.st_ino,
                temporary_before.st_mode,
                temporary_before.st_nlink,
                temporary_before.st_size,
            ),
        )

    def test_candidate_failure_restores_exact_absence_without_rollback_smoke(
        self,
    ) -> None:
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        request = self.fixture.activation_request(prepared)
        child = SmokeChildBoundary(prepared, b"", returncode=70)
        cuts = ActivationCutRecorder(prepared.canonical_root)
        write_journal = deployment._write_activation_journal
        install_artifact = deployment._install_activation_artifact
        unlink_journal = deployment._unlink_activation_journal

        with (
            mock.patch.object(
                deployment,
                "_write_activation_journal",
                side_effect=cuts.wrap_journal_writer(write_journal),
            ),
            mock.patch.object(
                deployment,
                "_install_activation_artifact",
                side_effect=cuts.wrap_artifact_installer(install_artifact),
            ),
            mock.patch.object(
                deployment,
                "_spawn_activation_smoke_child",
                child,
            ),
            mock.patch.object(
                deployment,
                "_unlink_activation_journal",
                side_effect=cuts.wrap_journal_unlinker(unlink_journal),
            ),
        ):
            result = deployment.activate_staged(request)

        self.assertEqual(child.calls, 1)
        self.assertEqual(result.outcome, "restored-absent")
        self.assertEqual(
            result.candidate_receipt_sha256,
            sha256(prepared.staged.deployment_raw),
        )
        self.assertIsNone(result.active_receipt_sha256)
        self.assertIsNone(result.accepted_envelope_sha256)
        self.assertEqual(result.journal_sha256, sha256(result.journal_raw))
        self.assertEqual(result.journal_value["phase"], "terminal")
        self.assertEqual(
            result.journal_value["terminal_result"]["outcome"],
            "restored-absent",
        )
        self.assertIsNone(result.journal_value["candidate_smoke_acceptance"])
        self.assertIsNone(result.journal_value["rollback_smoke_acceptance"])
        self.assertIn("journal:absence-restoring:durable", cuts.events)
        self.assertIn("journal:absence-accepted:durable", cuts.events)
        absence_steps = [
            journal["pending_step"]
            for journal in cuts.journals
            if journal["phase"] == "absence-restoring"
            and journal["pending_step"] is not None
        ]
        self.assertGreater(len(absence_steps), len(prepared.verified.artifacts))
        self.assertEqual(
            {step["operation"] for step in absence_steps},
            {"remove-artifact", "remove-directory"},
        )
        self.assertEqual(
            [step["index"] for step in absence_steps],
            list(range(len(absence_steps))),
        )
        self.assertLess(
            cuts.events.index("journal:terminal:durable"),
            cuts.events.index("journal:before-unlink"),
        )
        self.assertEqual(
            tree_inventory(prepared.canonical_root),
            exact_restored_absent_inventory(prepared.activation_lock, result),
        )


if __name__ == "__main__":
    unittest.main()
