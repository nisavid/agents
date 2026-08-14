from __future__ import annotations

import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ._activation_support import (
    PreparedActivation,
    SmokeChildBoundary,
    canonical_value,
    expected_smoke_envelope,
)
from ._control_maintenance_activation_support import (
    AcceptedControlMaintenanceSmoke,
    ControlMaintenanceActivationFixture,
    PreparedControlMaintenanceActivation,
    RejectCandidateAcceptPriorControlMaintenanceSmoke,
)
from ._routine_support import RoutineDeploymentFixture, RoutineSmokeBoundary
from ._support import set_agent_plugins_candidate_version

_POST_UNLINK_PROCESS_LOSS_EXIT = 119
_RESULT_RETENTION_PROCESS_LOSS_EXIT = 118


def _write_all(descriptor: int, raw: bytes) -> None:
    offset = 0
    while offset < len(raw):
        written = os.write(descriptor, raw[offset:])
        if written <= 0:
            raise AssertionError("terminal journal pipe write made no progress")
        offset += written


def _read_all(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 65536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _run_post_unlink_process_loss(
    deployment: object,
    activation: object,
    smoke: object,
    *,
    activation_umask: int | None = None,
) -> bytes:
    """Lose the real activator after journal unlink and its parent fsync."""

    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        if activation_umask is not None:
            os.umask(activation_umask)
        original_unlink = deployment._unlink_activation_journal

        def observed_unlink(canonical_root_fd: int, expected_raw: bytes) -> None:
            _write_all(write_fd, expected_raw)
            original_unlink(canonical_root_fd, expected_raw)
            os.close(write_fd)
            os._exit(_POST_UNLINK_PROCESS_LOSS_EXIT)

        deployment._spawn_activation_smoke_child = smoke
        deployment._unlink_activation_journal = observed_unlink
        try:
            deployment.activate_staged(activation)
        except (AssertionError, OSError, TypeError, deployment.DeploymentError):
            os._exit(120)
        os._exit(121)

    os.close(write_fd)
    terminal_raw = _read_all(read_fd)
    os.close(read_fd)
    waited, status = os.waitpid(child, 0)
    if waited != child or not os.WIFEXITED(status):
        raise AssertionError("post-unlink process-loss child did not exit normally")
    if os.WEXITSTATUS(status) != _POST_UNLINK_PROCESS_LOSS_EXIT:
        raise AssertionError(
            "post-unlink process-loss child exited at an unexpected boundary"
        )
    return terminal_raw


def run_post_unlink_process_loss(
    deployment: object,
    activation: object,
    staged: object,
    *,
    candidate_accepted: bool,
) -> bytes:
    root = Path(activation.deployment.canonical_root)
    smoke = RoutineSmokeBoundary(
        root,
        staged.deployment_value["smoke"],
        staged.rollback_value["prior_activation_unit"]["smoke"],
        candidate_accepted=candidate_accepted,
        rollback_accepted=True,
    )
    return _run_post_unlink_process_loss(deployment, activation, smoke)


def run_result_retention_process_loss(
    deployment: object,
    activation: object,
    smoke: object,
    *,
    cut: str,
    activation_umask: int | None = None,
) -> bytes:
    """Lose the real activator at one real result-retention durability seam."""

    cuts = {
        "directory-created",
        "directory-published",
        "temporary-created",
        "partial-write",
        "file-synced",
        "final-linked",
        "temporary-unlinked",
        "result-retained",
    }
    if cut not in cuts:
        raise AssertionError(f"unknown transaction result retention cut: {cut}")
    child = os.fork()
    if child == 0:
        if activation_umask is not None:
            os.umask(activation_umask)
        inside_retention = False
        result_directory_renamed = False
        original_retain = deployment._retain_terminal_transaction_result
        original_mkdir = deployment.os.mkdir
        original_rename = deployment.os.rename
        original_open = deployment.os.open
        original_write = deployment.os.write
        original_fsync = deployment.os.fsync
        original_link = deployment.os.link
        original_unlink = deployment.os.unlink

        def process_loss() -> None:
            os._exit(_RESULT_RETENTION_PROCESS_LOSS_EXIT)

        def observed_retain(*args: object, **kwargs: object) -> None:
            nonlocal inside_retention
            inside_retention = True
            original_retain(*args, **kwargs)
            inside_retention = False
            if cut == "result-retained":
                process_loss()

        def observed_mkdir(*args: object, **kwargs: object) -> object:
            result = original_mkdir(*args, **kwargs)
            if (
                inside_retention
                and args
                and str(args[0]).startswith(".task-witness-results-")
                and cut == "directory-created"
            ):
                process_loss()
            return result

        def observed_rename(*args: object, **kwargs: object) -> object:
            nonlocal result_directory_renamed
            result = original_rename(*args, **kwargs)
            if (
                inside_retention
                and len(args) >= 2
                and str(args[0]).startswith(".task-witness-results-")
                and args[1] == "transaction-results"
            ):
                result_directory_renamed = True
            return result

        def observed_write(descriptor: int, raw: bytes) -> int:
            if inside_retention and cut == "partial-write":
                original_write(descriptor, raw[: max(1, len(raw) // 2)])
                process_loss()
            return original_write(descriptor, raw)

        def observed_open(*args: object, **kwargs: object) -> int:
            descriptor = original_open(*args, **kwargs)
            if (
                inside_retention
                and args
                and str(args[0]).startswith(".task-witness-result-")
                and cut == "temporary-created"
            ):
                process_loss()
            return descriptor

        def observed_fsync(descriptor: int) -> None:
            original_fsync(descriptor)
            if (
                inside_retention
                and result_directory_renamed
                and cut == "directory-published"
                and stat.S_ISDIR(os.fstat(descriptor).st_mode)
            ):
                process_loss()
            if (
                inside_retention
                and cut == "file-synced"
                and stat.S_ISREG(os.fstat(descriptor).st_mode)
            ):
                process_loss()

        def observed_link(*args: object, **kwargs: object) -> None:
            original_link(*args, **kwargs)
            if inside_retention and cut == "final-linked":
                process_loss()

        def observed_unlink(*args: object, **kwargs: object) -> None:
            original_unlink(*args, **kwargs)
            if (
                inside_retention
                and cut == "temporary-unlinked"
                and args
                and str(args[0]).startswith(".task-witness-result-")
            ):
                process_loss()

        deployment._spawn_activation_smoke_child = smoke
        deployment._retain_terminal_transaction_result = observed_retain
        deployment.os.mkdir = observed_mkdir
        deployment.os.rename = observed_rename
        deployment.os.open = observed_open
        deployment.os.write = observed_write
        deployment.os.fsync = observed_fsync
        deployment.os.link = observed_link
        deployment.os.unlink = observed_unlink
        try:
            deployment.activate_staged(activation)
        except (AssertionError, OSError, TypeError, deployment.DeploymentError):
            os._exit(122)
        os._exit(123)

    waited, status = os.waitpid(child, 0)
    if waited != child or not os.WIFEXITED(status):
        raise AssertionError("transaction result retention cut did not exit normally")
    if os.WEXITSTATUS(status) != _RESULT_RETENTION_PROCESS_LOSS_EXIT:
        raise AssertionError(
            "transaction result retention cut exited at an unexpected boundary"
        )
    root = Path(activation.deployment.canonical_root)
    return (root / "transaction.json").read_bytes()


def tree_state(root: Path) -> tuple[tuple[str, str, int, int, bytes], ...]:
    state: list[tuple[str, str, int, int, bytes]] = []
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        kind = (
            "directory"
            if stat.S_ISDIR(metadata.st_mode)
            else "file"
            if stat.S_ISREG(metadata.st_mode)
            else "other"
        )
        state.append(
            (
                path.relative_to(root).as_posix(),
                kind,
                stat.S_IMODE(metadata.st_mode),
                metadata.st_nlink,
                path.read_bytes() if kind == "file" else b"",
            )
        )
    return tuple(state)


def opaque_tree_state(
    root: Path,
) -> tuple[tuple[str, str, int, int, int, int, int, bytes | None], ...]:
    state: list[tuple[str, str, int, int, int, int, int, bytes | None]] = []
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        kind = (
            "directory"
            if stat.S_ISDIR(metadata.st_mode)
            else "file"
            if stat.S_ISREG(metadata.st_mode)
            else "other"
        )
        mode = stat.S_IMODE(metadata.st_mode)
        state.append(
            (
                path.relative_to(root).as_posix(),
                kind,
                mode,
                metadata.st_nlink,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
                path.read_bytes() if kind == "file" and mode != 0 else None,
            )
        )
    return tuple(state)


def remove_retained_result_baseline(root: Path) -> tuple[str, bytes]:
    result_directory = root / "transaction-results"
    retained = list(result_directory.glob("*.json"))
    if len(retained) != 1:
        raise AssertionError("test setup requires one retained result")
    name = retained[0].name
    raw = retained[0].read_bytes()
    shutil.rmtree(result_directory)
    return name, raw


def restore_retained_result(root: Path, name: str, raw: bytes) -> None:
    retained = root / "transaction-results" / name
    retained.write_bytes(raw)
    retained.chmod(0o600)


def staged_control_without_retained_result(
    fixture: ControlMaintenanceActivationFixture,
) -> tuple[object, PreparedControlMaintenanceActivation, str, bytes]:
    deployment = fixture.deployment()
    initial, active, candidate, request = fixture.control.scenario()
    prior_name, prior_raw = remove_retained_result_baseline(initial.canonical_root)
    prepared = deployment.prepare_deployment(request)
    authorization_raw = fixture.control.authorization_raw(prepared)
    staged = deployment.stage_deployment(
        request,
        authorization_raw,
        fixture.root / "control-empty-result-stage",
    )
    activation = deployment.ActivationRequest(
        deployment=request,
        authorization_raw=authorization_raw,
        stage_receipt=staged.stage_path,
    )
    return (
        deployment,
        PreparedControlMaintenanceActivation(
            initial,
            active,
            candidate,
            request,
            prepared,
            authorization_raw,
            staged,
            activation,
        ),
        prior_name,
        prior_raw,
    )


def stage_next_routine(
    fixture: RoutineDeploymentFixture,
    deployment: object,
    canonical_root: Path,
    active_receipt_sha256: str,
    source: Path,
    *,
    name: str,
    version: str,
    revision: str,
    sequence: int,
) -> tuple[object, object]:
    candidate = fixture.next_candidate(source, name, version)
    request = fixture.request_for_candidate(
        canonical_root,
        active_receipt_sha256,
        candidate,
        release_version=version,
        revision=revision,
        sequence=sequence,
    )
    prepared = deployment.prepare_deployment(request)
    authorization_raw = fixture.authorization_raw(prepared)
    staged = deployment.stage_deployment(
        request,
        authorization_raw,
        fixture.root / f"{name}-stage",
    )
    return staged, deployment.ActivationRequest(
        deployment=request,
        authorization_raw=authorization_raw,
        stage_receipt=staged.stage_path,
    )


class TransactionResultReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.fixture = RoutineDeploymentFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_public_reconciliation_returns_candidate_result_after_post_unlink_loss(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        initial, _, _, _, staged, activation = self.fixture.staged_routine()
        terminal_raw = run_post_unlink_process_loss(
            deployment,
            activation,
            staged,
            candidate_accepted=True,
        )
        terminal = canonical_value(terminal_raw)

        self.assertEqual(terminal["phase"], "terminal")
        self.assertEqual(terminal["terminal_result"]["outcome"], "candidate-active")
        self.assertFalse((initial.canonical_root / "transaction.json").exists())

        result = deployment.reconcile_transaction_result(
            deployment.ResultReconciliationRequest(
                activation=activation,
                expected_terminal_journal_raw=terminal_raw,
            )
        )

        self.assertIsInstance(result, deployment.TransactionResult)
        self.assertEqual(result.outcome, "candidate-active")
        self.assertEqual(result.transaction_id, terminal["transaction_id"])
        self.assertEqual(result.journal_raw, terminal_raw)
        retained = (
            initial.canonical_root
            / "transaction-results"
            / (f"sha256-{terminal['transaction_id']}.json")
        )
        self.assertEqual(retained.read_bytes(), terminal_raw)
        self.assertFalse((initial.canonical_root / "transaction.json").exists())

    def test_public_reconciliation_returns_restored_prior_after_post_unlink_loss(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        initial, active, _, _, staged, activation = self.fixture.staged_routine()
        terminal_raw = run_post_unlink_process_loss(
            deployment,
            activation,
            staged,
            candidate_accepted=False,
        )

        result = deployment.reconcile_transaction_result(
            deployment.ResultReconciliationRequest(
                activation=activation,
                expected_terminal_journal_raw=terminal_raw,
            )
        )

        self.assertEqual(result.outcome, "restored-prior")
        self.assertEqual(result.active_receipt_sha256, active.active_receipt_sha256)
        self.assertEqual(result.journal_raw, terminal_raw)
        self.assertFalse((initial.canonical_root / "transaction.json").exists())

    def test_routine_reconciliation_rejects_post_stage_retained_result_addition(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        initial, active = self.fixture.activate_initial()
        prior_name, prior_raw = remove_retained_result_baseline(initial.canonical_root)
        request = self.fixture.request(
            initial.canonical_root,
            active.active_receipt_sha256,
        )
        prepared = deployment.prepare_deployment(request)
        authorization_raw = self.fixture.authorization_raw(prepared)
        staged = deployment.stage_deployment(
            request,
            authorization_raw,
            self.root / "routine-empty-result-stage",
        )
        activation = deployment.ActivationRequest(
            deployment=request,
            authorization_raw=authorization_raw,
            stage_receipt=staged.stage_path,
        )
        terminal_raw = run_post_unlink_process_loss(
            deployment,
            activation,
            staged,
            candidate_accepted=True,
        )
        restore_retained_result(initial.canonical_root, prior_name, prior_raw)
        before = tree_state(initial.canonical_root)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "routine deployment authorization facts disagree",
        ):
            deployment.reconcile_transaction_result(
                deployment.ResultReconciliationRequest(
                    activation=activation,
                    expected_terminal_journal_raw=terminal_raw,
                )
            )

        self.assertEqual(tree_state(initial.canonical_root), before)
        self.assertFalse((initial.canonical_root / "transaction.json").exists())

    def test_routine_recovery_rejects_post_stage_retained_result_addition(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        initial, active = self.fixture.activate_initial()
        prior_name, prior_raw = remove_retained_result_baseline(initial.canonical_root)
        request = self.fixture.request(
            initial.canonical_root,
            active.active_receipt_sha256,
        )
        prepared = deployment.prepare_deployment(request)
        authorization_raw = self.fixture.authorization_raw(prepared)
        staged = deployment.stage_deployment(
            request,
            authorization_raw,
            self.root / "routine-empty-result-recovery-stage",
        )
        activation = deployment.ActivationRequest(
            deployment=request,
            authorization_raw=authorization_raw,
            stage_receipt=staged.stage_path,
        )
        smoke = RoutineSmokeBoundary(
            initial.canonical_root,
            staged.deployment_value["smoke"],
            staged.rollback_value["prior_activation_unit"]["smoke"],
            candidate_accepted=True,
            rollback_accepted=True,
        )
        terminal_raw = run_result_retention_process_loss(
            deployment,
            activation,
            smoke,
            cut="result-retained",
        )
        restore_retained_result(initial.canonical_root, prior_name, prior_raw)
        before = tree_state(initial.canonical_root)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "routine deployment authorization facts disagree",
        ):
            deployment.recover_transaction(
                deployment.RecoveryRequest(
                    activation=activation,
                    expected_journal_raw=terminal_raw,
                )
            )

        self.assertEqual(tree_state(initial.canonical_root), before)
        self.assertEqual(
            (initial.canonical_root / "transaction.json").read_bytes(),
            terminal_raw,
        )

    def test_recovery_rejects_stable_history_before_result_temp_normalization(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        initial, active = self.fixture.activate_initial()
        prior_name, prior_raw = remove_retained_result_baseline(initial.canonical_root)
        request = self.fixture.request(
            initial.canonical_root,
            active.active_receipt_sha256,
        )
        prepared = deployment.prepare_deployment(request)
        authorization_raw = self.fixture.authorization_raw(prepared)
        staged = deployment.stage_deployment(
            request,
            authorization_raw,
            self.root / "routine-opaque-result-temp-stage",
        )
        activation = deployment.ActivationRequest(
            deployment=request,
            authorization_raw=authorization_raw,
            stage_receipt=staged.stage_path,
        )
        smoke = RoutineSmokeBoundary(
            initial.canonical_root,
            staged.deployment_value["smoke"],
            staged.rollback_value["prior_activation_unit"]["smoke"],
            candidate_accepted=True,
            rollback_accepted=True,
        )
        terminal_raw = run_result_retention_process_loss(
            deployment,
            activation,
            smoke,
            cut="temporary-created",
            activation_umask=0o777,
        )
        pending = next(
            (initial.canonical_root / "transaction-results").glob(
                ".task-witness-result-*.tmp"
            )
        )
        self.assertEqual(stat.S_IMODE(pending.lstat().st_mode), 0)
        restore_retained_result(initial.canonical_root, prior_name, prior_raw)
        before = opaque_tree_state(initial.canonical_root)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "routine deployment authorization facts disagree",
        ):
            deployment.recover_transaction(
                deployment.RecoveryRequest(
                    activation=activation,
                    expected_journal_raw=terminal_raw,
                )
            )

        self.assertEqual(opaque_tree_state(initial.canonical_root), before)
        self.assertEqual(stat.S_IMODE(pending.lstat().st_mode), 0)
        self.assertEqual(
            (initial.canonical_root / "transaction.json").read_bytes(),
            terminal_raw,
        )

    def test_recovery_rejects_live_tree_drift_before_result_temp_normalization(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        initial, _, _, _, staged, activation = self.fixture.staged_routine()
        smoke = RoutineSmokeBoundary(
            initial.canonical_root,
            staged.deployment_value["smoke"],
            staged.rollback_value["prior_activation_unit"]["smoke"],
            candidate_accepted=True,
            rollback_accepted=True,
        )
        terminal_raw = run_result_retention_process_loss(
            deployment,
            activation,
            smoke,
            cut="temporary-created",
            activation_umask=0o777,
        )
        pending = next(
            (initial.canonical_root / "transaction-results").glob(
                ".task-witness-result-*.tmp"
            )
        )
        self.assertEqual(stat.S_IMODE(pending.lstat().st_mode), 0)
        unauthorized = initial.canonical_root / "unauthorized-recovery-residue"
        unauthorized.write_bytes(b"contradiction\n")
        unauthorized.chmod(0o600)
        before = opaque_tree_state(initial.canonical_root)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "routine activation tree inventory disagrees",
        ):
            deployment.recover_transaction(
                deployment.RecoveryRequest(
                    activation=activation,
                    expected_journal_raw=terminal_raw,
                )
            )

        self.assertEqual(opaque_tree_state(initial.canonical_root), before)
        self.assertEqual(stat.S_IMODE(pending.lstat().st_mode), 0)
        self.assertEqual(
            (initial.canonical_root / "transaction.json").read_bytes(),
            terminal_raw,
        )

    def test_control_reconciliation_rejects_post_stage_retained_result_addition(
        self,
    ) -> None:
        fixture = ControlMaintenanceActivationFixture(self.root / "control-reconcile")
        deployment, prepared, prior_name, prior_raw = (
            staged_control_without_retained_result(fixture)
        )
        terminal_raw = _run_post_unlink_process_loss(
            deployment,
            prepared.activation,
            AcceptedControlMaintenanceSmoke(prepared),
        )
        root = prepared.initial.canonical_root
        restore_retained_result(root, prior_name, prior_raw)
        before = tree_state(root)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "control-set maintenance authorization facts disagree",
        ):
            deployment.reconcile_transaction_result(
                deployment.ResultReconciliationRequest(
                    activation=prepared.activation,
                    expected_terminal_journal_raw=terminal_raw,
                )
            )

        self.assertEqual(tree_state(root), before)
        self.assertFalse((root / "transaction.json").exists())

    def test_control_recovery_rejects_post_stage_retained_result_addition(
        self,
    ) -> None:
        fixture = ControlMaintenanceActivationFixture(self.root / "control-recovery")
        deployment, prepared, prior_name, prior_raw = (
            staged_control_without_retained_result(fixture)
        )
        terminal_raw = run_result_retention_process_loss(
            deployment,
            prepared.activation,
            AcceptedControlMaintenanceSmoke(prepared),
            cut="result-retained",
        )
        root = prepared.initial.canonical_root
        restore_retained_result(root, prior_name, prior_raw)
        before = tree_state(root)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "control-set maintenance authorization facts disagree",
        ):
            deployment.recover_transaction(
                deployment.RecoveryRequest(
                    activation=prepared.activation,
                    expected_journal_raw=terminal_raw,
                )
            )

        self.assertEqual(tree_state(root), before)
        self.assertEqual((root / "transaction.json").read_bytes(), terminal_raw)

    def test_routine_reconciliation_rejects_missing_or_substituted_history(
        self,
    ) -> None:
        for case in ("missing", "substituted"):
            with (
                self.subTest(case=case),
                tempfile.TemporaryDirectory() as directory,
            ):
                fixture = RoutineDeploymentFixture(Path(directory).resolve())
                deployment = fixture.deployment()
                initial, _, _, _, staged, activation = fixture.staged_routine()
                result_directory = initial.canonical_root / "transaction-results"
                prior = next(result_directory.glob("*.json"))
                terminal_raw = run_post_unlink_process_loss(
                    deployment,
                    activation,
                    staged,
                    candidate_accepted=True,
                )
                if case == "missing":
                    prior.unlink()
                else:
                    prior.write_bytes(terminal_raw)
                    prior.chmod(0o600)
                before = tree_state(initial.canonical_root)

                with self.assertRaises(deployment.DeploymentError):
                    deployment.reconcile_transaction_result(
                        deployment.ResultReconciliationRequest(
                            activation=activation,
                            expected_terminal_journal_raw=terminal_raw,
                        )
                    )

                self.assertEqual(tree_state(initial.canonical_root), before)
                self.assertFalse((initial.canonical_root / "transaction.json").exists())

    def test_restored_prior_next_transaction_invalidates_prior_reconciliation(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        initial, _, _, _, staged_b, activation_b = self.fixture.staged_routine()
        terminal_b = run_post_unlink_process_loss(
            deployment,
            activation_b,
            staged_b,
            candidate_accepted=True,
        )
        active_b = canonical_value(terminal_b)["terminal_result"][
            "active_receipt_sha256"
        ]
        staged_c, activation_c = stage_next_routine(
            self.fixture,
            deployment,
            initial.canonical_root,
            active_b,
            activation_b.deployment.candidate_root,
            name="candidate-c-restored",
            version="1.0.2",
            revision="c" * 40,
            sequence=9,
        )
        smoke_c = RoutineSmokeBoundary(
            initial.canonical_root,
            staged_c.deployment_value["smoke"],
            staged_c.rollback_value["prior_activation_unit"]["smoke"],
            candidate_accepted=False,
            rollback_accepted=True,
        )
        with mock.patch.object(deployment, "_spawn_activation_smoke_child", smoke_c):
            result_c = deployment.activate_staged(activation_c)
        self.assertEqual(result_c.outcome, "restored-prior")
        result_directory = initial.canonical_root / "transaction-results"
        retained_b = result_directory / (
            f"sha256-{canonical_value(terminal_b)['transaction_id']}.json"
        )
        retained_c = result_directory / f"sha256-{result_c.transaction_id}.json"
        self.assertEqual(retained_b.read_bytes(), terminal_b)
        self.assertEqual(retained_c.read_bytes(), result_c.journal_raw)
        self.assertEqual(len(list(result_directory.glob("*.json"))), 3)
        before = tree_state(initial.canonical_root)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "routine deployment authorization facts disagree",
        ):
            deployment.reconcile_transaction_result(
                deployment.ResultReconciliationRequest(
                    activation=activation_b,
                    expected_terminal_journal_raw=terminal_b,
                )
            )

        self.assertEqual(tree_state(initial.canonical_root), before)
        self.assertFalse((initial.canonical_root / "transaction.json").exists())

    def test_recovery_required_remains_live_journal_retained(self) -> None:
        deployment = self.fixture.deployment()
        initial, _, _, _, staged, activation = self.fixture.staged_routine()
        smoke = RoutineSmokeBoundary(
            initial.canonical_root,
            staged.deployment_value["smoke"],
            staged.rollback_value["prior_activation_unit"]["smoke"],
            candidate_accepted=False,
            rollback_accepted=False,
        )

        with mock.patch.object(deployment, "_spawn_activation_smoke_child", smoke):
            result = deployment.activate_staged(activation)

        live_journal = initial.canonical_root / "transaction.json"
        self.assertEqual(result.outcome, "recovery-required")
        self.assertEqual(live_journal.read_bytes(), result.journal_raw)
        retained = (
            initial.canonical_root
            / "transaction-results"
            / (f"sha256-{result.transaction_id}.json")
        )
        self.assertFalse(retained.exists())
        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "not a retainable terminal result",
        ):
            deployment.reconcile_transaction_result(
                deployment.ResultReconciliationRequest(
                    activation=activation,
                    expected_terminal_journal_raw=result.journal_raw,
                )
            )
        self.assertEqual(live_journal.read_bytes(), result.journal_raw)

    def test_public_reconciliation_returns_restored_absent_after_post_unlink_loss(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        prepared = self.fixture.first_install.prepare()
        activation = deployment.ActivationRequest(
            deployment=prepared.request,
            authorization_raw=prepared.authorization_raw,
            stage_receipt=prepared.staged.stage_path,
        )
        smoke = SmokeChildBoundary(
            prepared,
            expected_smoke_envelope(prepared.staged),
            returncode=70,
        )
        terminal_raw = _run_post_unlink_process_loss(
            deployment,
            activation,
            smoke,
        )

        result = deployment.reconcile_transaction_result(
            deployment.ResultReconciliationRequest(
                activation=activation,
                expected_terminal_journal_raw=terminal_raw,
            )
        )

        self.assertEqual(result.outcome, "restored-absent")
        self.assertIsNone(result.active_receipt_sha256)
        self.assertEqual(result.journal_raw, terminal_raw)
        self.assertEqual(
            set(prepared.canonical_root.iterdir()),
            {prepared.activation_lock, prepared.canonical_root / "transaction-results"},
        )

    def test_result_retention_normalizes_a_new_directory_under_restrictive_umask(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        prepared = self.fixture.first_install.prepare()
        activation = deployment.ActivationRequest(
            deployment=prepared.request,
            authorization_raw=prepared.authorization_raw,
            stage_receipt=prepared.staged.stage_path,
        )
        smoke = SmokeChildBoundary(
            prepared,
            expected_smoke_envelope(prepared.staged),
        )

        terminal_raw = _run_post_unlink_process_loss(
            deployment,
            activation,
            smoke,
            activation_umask=0o777,
        )

        result_directory = prepared.canonical_root / "transaction-results"
        self.assertEqual(stat.S_IMODE(result_directory.stat().st_mode), 0o700)
        result = deployment.reconcile_transaction_result(
            deployment.ResultReconciliationRequest(
                activation=activation,
                expected_terminal_journal_raw=terminal_raw,
            )
        )
        self.assertEqual(result.outcome, "candidate-active")

    def test_next_transaction_preserves_prior_result_and_closes_its_baseline(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        initial, _, _, _, staged_b, activation_b = self.fixture.staged_routine()
        terminal_b = run_post_unlink_process_loss(
            deployment,
            activation_b,
            staged_b,
            candidate_accepted=True,
        )
        result_b = deployment.reconcile_transaction_result(
            deployment.ResultReconciliationRequest(
                activation=activation_b,
                expected_terminal_journal_raw=terminal_b,
            )
        )
        retained_b = (
            initial.canonical_root
            / "transaction-results"
            / (f"sha256-{result_b.transaction_id}.json")
        )

        candidate_c = self.root / "candidate-c"
        shutil.copytree(activation_b.deployment.candidate_root, candidate_c)
        runtime = candidate_c / "runtime" / "task_witness.py"
        runtime.write_bytes(runtime.read_bytes() + b"\n# routine candidate C\n")
        set_agent_plugins_candidate_version(candidate_c, "1.0.2")
        request_c = self.fixture.request_for_candidate(
            initial.canonical_root,
            result_b.active_receipt_sha256,
            candidate_c,
            release_version="1.0.2",
            revision="c" * 40,
            sequence=9,
        )
        prepared_c = deployment.prepare_deployment(request_c)
        authorization_c = self.fixture.authorization_raw(prepared_c)
        staged_c = deployment.stage_deployment(
            request_c,
            authorization_c,
            self.root / "routine-stage-c",
        )
        activation_c = deployment.ActivationRequest(
            deployment=request_c,
            authorization_raw=authorization_c,
            stage_receipt=staged_c.stage_path,
        )

        retained_b.write_bytes(terminal_b + b"\n")
        selectors_before = (
            (initial.canonical_root / "active.json").read_bytes(),
            (initial.canonical_root / "deployment.json").read_bytes(),
        )
        with self.assertRaises(deployment.DeploymentError):
            deployment.activate_staged(activation_c)
        self.assertFalse((initial.canonical_root / "transaction.json").exists())
        self.assertEqual(
            selectors_before,
            (
                (initial.canonical_root / "active.json").read_bytes(),
                (initial.canonical_root / "deployment.json").read_bytes(),
            ),
        )
        retained_b.write_bytes(terminal_b)

        smoke_c = RoutineSmokeBoundary(
            initial.canonical_root,
            staged_c.deployment_value["smoke"],
            staged_c.rollback_value["prior_activation_unit"]["smoke"],
            candidate_accepted=True,
            rollback_accepted=True,
        )
        with mock.patch.object(deployment, "_spawn_activation_smoke_child", smoke_c):
            result_c = deployment.activate_staged(activation_c)

        self.assertEqual(result_c.outcome, "candidate-active")
        self.assertEqual(retained_b.read_bytes(), terminal_b)
        retained = sorted(
            (initial.canonical_root / "transaction-results").glob("*.json")
        )
        self.assertEqual(len(retained), 3)
        self.assertIn(retained_b, retained)

        before = tree_state(initial.canonical_root)
        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "disagrees",
        ):
            deployment.reconcile_transaction_result(
                deployment.ResultReconciliationRequest(
                    activation=activation_b,
                    expected_terminal_journal_raw=terminal_b,
                )
            )
        self.assertEqual(tree_state(initial.canonical_root), before)
        reconciled_c = deployment.reconcile_transaction_result(
            deployment.ResultReconciliationRequest(
                activation=activation_c,
                expected_terminal_journal_raw=result_c.journal_raw,
            )
        )
        self.assertEqual(reconciled_c, result_c)

    def test_first_install_after_restored_absent_preserves_prior_result(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        prepared_a = self.fixture.first_install.prepare()
        activation_a = deployment.ActivationRequest(
            deployment=prepared_a.request,
            authorization_raw=prepared_a.authorization_raw,
            stage_receipt=prepared_a.staged.stage_path,
        )
        smoke_a = SmokeChildBoundary(
            prepared_a,
            expected_smoke_envelope(prepared_a.staged),
            returncode=70,
        )
        terminal_a = _run_post_unlink_process_loss(
            deployment,
            activation_a,
            smoke_a,
        )
        result_a = deployment.reconcile_transaction_result(
            deployment.ResultReconciliationRequest(
                activation=activation_a,
                expected_terminal_journal_raw=terminal_a,
            )
        )
        self.assertEqual(result_a.outcome, "restored-absent")
        retained_a = (
            prepared_a.canonical_root
            / "transaction-results"
            / f"sha256-{result_a.transaction_id}.json"
        )

        request_b = self.fixture.first_install.first_install_request(
            prepared_a.canonical_root
        )
        plan_b = deployment.prepare_first_install(request_b)
        authorization_b = self.fixture.first_install.first_install_authorization_raw(
            plan_b
        )
        staged_b = deployment.stage_first_install(
            request_b,
            authorization_b,
            self.root / "stage-b",
        )
        activation_b = deployment.ActivationRequest(
            deployment=request_b,
            authorization_raw=authorization_b,
            stage_receipt=staged_b.stage_path,
        )
        prepared_b = PreparedActivation(
            request=request_b,
            authorization_raw=authorization_b,
            staged=staged_b,
            verified=deployment.verify_deployment_stage(staged_b.stage_path),
            canonical_root=prepared_a.canonical_root,
            activation_lock=prepared_a.activation_lock,
        )
        smoke_b = SmokeChildBoundary(
            prepared_b,
            expected_smoke_envelope(staged_b),
        )
        with mock.patch.object(deployment, "_spawn_activation_smoke_child", smoke_b):
            result_b = deployment.activate_staged(activation_b)

        self.assertEqual(result_b.outcome, "candidate-active")
        self.assertEqual(retained_a.read_bytes(), terminal_a)
        self.assertEqual(
            len(
                list((prepared_a.canonical_root / "transaction-results").glob("*.json"))
            ),
            2,
        )
        before = tree_state(prepared_a.canonical_root)
        with self.assertRaises(deployment.DeploymentError):
            deployment.reconcile_transaction_result(
                deployment.ResultReconciliationRequest(
                    activation=activation_a,
                    expected_terminal_journal_raw=terminal_a,
                )
            )
        self.assertEqual(tree_state(prepared_a.canonical_root), before)
        self.assertEqual(
            deployment.reconcile_transaction_result(
                deployment.ResultReconciliationRequest(
                    activation=activation_b,
                    expected_terminal_journal_raw=result_b.journal_raw,
                )
            ),
            result_b,
        )

    def test_completed_first_install_transaction_identity_is_single_use(self) -> None:
        deployment = self.fixture.deployment()
        prepared = self.fixture.first_install.prepare()
        activation = deployment.ActivationRequest(
            deployment=prepared.request,
            authorization_raw=prepared.authorization_raw,
            stage_receipt=prepared.staged.stage_path,
        )
        rejecting = SmokeChildBoundary(
            prepared,
            expected_smoke_envelope(prepared.staged),
            returncode=70,
        )
        with mock.patch.object(
            deployment,
            "_spawn_activation_smoke_child",
            rejecting,
        ):
            result = deployment.activate_staged(activation)
        self.assertEqual(result.outcome, "restored-absent")

        accepting = SmokeChildBoundary(
            prepared,
            expected_smoke_envelope(prepared.staged),
        )
        before = tree_state(prepared.canonical_root)
        with (
            mock.patch.object(
                deployment,
                "_spawn_activation_smoke_child",
                accepting,
            ),
            self.assertRaisesRegex(
                deployment.DeploymentError,
                "first-install authorization facts disagree",
            ),
        ):
            deployment.activate_staged(activation)
        self.assertEqual(accepting.calls, 0)
        self.assertEqual(tree_state(prepared.canonical_root), before)
        self.assertFalse((prepared.canonical_root / "transaction.json").exists())

    def test_completed_routine_transaction_identity_is_single_use(self) -> None:
        deployment = self.fixture.deployment()
        initial, _, _, _, staged, activation = self.fixture.staged_routine()
        rejecting = RoutineSmokeBoundary(
            initial.canonical_root,
            staged.deployment_value["smoke"],
            staged.rollback_value["prior_activation_unit"]["smoke"],
            candidate_accepted=False,
            rollback_accepted=True,
        )
        with mock.patch.object(
            deployment,
            "_spawn_activation_smoke_child",
            rejecting,
        ):
            result = deployment.activate_staged(activation)
        self.assertEqual(result.outcome, "restored-prior")

        accepting = RoutineSmokeBoundary(
            initial.canonical_root,
            staged.deployment_value["smoke"],
            staged.rollback_value["prior_activation_unit"]["smoke"],
            candidate_accepted=True,
            rollback_accepted=True,
        )
        before = tree_state(initial.canonical_root)
        with (
            mock.patch.object(
                deployment,
                "_spawn_activation_smoke_child",
                accepting,
            ),
            self.assertRaisesRegex(
                deployment.DeploymentError,
                "routine deployment authorization facts disagree",
            ),
        ):
            deployment.activate_staged(activation)
        self.assertEqual(accepting.calls, [])
        self.assertEqual(tree_state(initial.canonical_root), before)
        self.assertFalse((initial.canonical_root / "transaction.json").exists())

    def test_completed_control_transaction_identity_is_single_use(self) -> None:
        fixture = ControlMaintenanceActivationFixture(self.root / "control-replay")
        deployment = fixture.deployment()
        prepared = fixture.staged_activation()
        rejecting = RejectCandidateAcceptPriorControlMaintenanceSmoke(prepared)
        with mock.patch.object(
            deployment,
            "_spawn_activation_smoke_child",
            rejecting,
        ):
            result = deployment.activate_staged(prepared.activation)
        self.assertEqual(result.outcome, "restored-prior")

        accepting = AcceptedControlMaintenanceSmoke(prepared)
        before = tree_state(prepared.initial.canonical_root)
        with (
            mock.patch.object(
                deployment,
                "_spawn_activation_smoke_child",
                accepting,
            ),
            self.assertRaisesRegex(
                deployment.DeploymentError,
                "control-set maintenance authorization facts disagree",
            ),
        ):
            deployment.activate_staged(prepared.activation)
        self.assertEqual(accepting.observations, [])
        self.assertEqual(tree_state(prepared.initial.canonical_root), before)
        self.assertFalse(
            (prepared.initial.canonical_root / "transaction.json").exists()
        )

    def test_recovery_converges_across_result_retention_process_loss(self) -> None:
        for cut, activation_umask in (
            ("directory-created", None),
            ("directory-created", 0o777),
            ("directory-published", None),
            ("temporary-created", 0o777),
            ("partial-write", None),
            ("file-synced", None),
            ("final-linked", None),
            ("temporary-unlinked", None),
            ("result-retained", None),
        ):
            with (
                self.subTest(
                    cut=cut,
                    activation_umask=activation_umask,
                ),
                tempfile.TemporaryDirectory() as directory,
            ):
                fixture = RoutineDeploymentFixture(Path(directory).resolve())
                deployment = fixture.deployment()
                prepared = fixture.first_install.prepare()
                activation = deployment.ActivationRequest(
                    deployment=prepared.request,
                    authorization_raw=prepared.authorization_raw,
                    stage_receipt=prepared.staged.stage_path,
                )
                smoke = SmokeChildBoundary(
                    prepared,
                    expected_smoke_envelope(prepared.staged),
                )
                terminal_raw = run_result_retention_process_loss(
                    deployment,
                    activation,
                    smoke,
                    cut=cut,
                    activation_umask=activation_umask,
                )
                terminal = canonical_value(terminal_raw)
                self.assertEqual(terminal["phase"], "terminal")
                self.assertEqual(
                    terminal["terminal_result"]["outcome"],
                    "candidate-active",
                )

                result = deployment.recover_transaction(
                    deployment.RecoveryRequest(
                        activation=activation,
                        expected_journal_raw=terminal_raw,
                    )
                )

                self.assertEqual(result.outcome, "candidate-active")
                self.assertFalse(
                    (prepared.canonical_root / "transaction.json").exists()
                )
                retained = (
                    prepared.canonical_root
                    / "transaction-results"
                    / (f"sha256-{result.transaction_id}.json")
                )
                self.assertEqual(retained.read_bytes(), terminal_raw)
                self.assertFalse(
                    any(
                        path.name.startswith(".task-witness-result-")
                        for path in retained.parent.iterdir()
                    )
                )
                reconciled = deployment.reconcile_transaction_result(
                    deployment.ResultReconciliationRequest(
                        activation=activation,
                        expected_terminal_journal_raw=terminal_raw,
                    )
                )
                self.assertEqual(reconciled, result)

    def test_recovery_rejects_mode_drift_on_preexisting_result_directory(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        initial, _, _, _, staged, activation = self.fixture.staged_routine()
        smoke = RoutineSmokeBoundary(
            initial.canonical_root,
            staged.deployment_value["smoke"],
            staged.rollback_value["prior_activation_unit"]["smoke"],
            candidate_accepted=True,
            rollback_accepted=True,
        )
        terminal_raw = run_result_retention_process_loss(
            deployment,
            activation,
            smoke,
            cut="result-retained",
        )
        result_directory = initial.canonical_root / "transaction-results"
        result_directory.chmod(0o500)
        before_identity = (
            result_directory.stat().st_dev,
            result_directory.stat().st_ino,
        )
        before = tree_state(initial.canonical_root)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "pending transaction result directory binding disagrees",
        ):
            deployment.recover_transaction(
                deployment.RecoveryRequest(
                    activation=activation,
                    expected_journal_raw=terminal_raw,
                )
            )

        self.assertEqual(tree_state(initial.canonical_root), before)
        self.assertEqual(stat.S_IMODE(result_directory.stat().st_mode), 0o500)
        self.assertEqual(
            (result_directory.stat().st_dev, result_directory.stat().st_ino),
            before_identity,
        )
        self.assertEqual(
            (initial.canonical_root / "transaction.json").read_bytes(),
            terminal_raw,
        )

    def test_public_reconciliation_rejects_result_inventory_contradictions(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        initial, _, _, _, staged, activation = self.fixture.staged_routine()
        terminal_raw = run_post_unlink_process_loss(
            deployment,
            activation,
            staged,
            candidate_accepted=True,
        )
        terminal = canonical_value(terminal_raw)
        result_directory = initial.canonical_root / "transaction-results"
        retained = result_directory / f"sha256-{terminal['transaction_id']}.json"

        def reconcile() -> object:
            return deployment.reconcile_transaction_result(
                deployment.ResultReconciliationRequest(
                    activation=activation,
                    expected_terminal_journal_raw=terminal_raw,
                )
            )

        def reject_without_mutation(label: str) -> None:
            before = tree_state(initial.canonical_root)
            with (
                self.subTest(case=label),
                self.assertRaises(deployment.DeploymentError),
            ):
                reconcile()
            self.assertEqual(tree_state(initial.canonical_root), before)

        unexpected = result_directory / "unexpected"
        unexpected.write_bytes(b"")
        unexpected.chmod(0o600)
        reject_without_mutation("extra")
        unexpected.unlink()

        misnamed = result_directory / f"sha256-{'0' * 64}.json"
        misnamed.write_bytes(terminal_raw)
        misnamed.chmod(0o600)
        reject_without_mutation("misnamed")
        misnamed.unlink()

        temporary = result_directory / f".task-witness-result-{'0' * 64}.tmp"
        temporary.write_bytes(terminal_raw[:17])
        temporary.chmod(0o600)
        reject_without_mutation("unowned-temporary")
        temporary.unlink()

        linked = result_directory / f"sha256-{'1' * 64}.json"
        os.link(retained, linked)
        reject_without_mutation("hard-link")
        linked.unlink()

        typed = result_directory / f"sha256-{'2' * 64}.json"
        typed.mkdir(mode=0o700)
        reject_without_mutation("non-regular-type")
        typed.rmdir()

        retained.chmod(0o640)
        reject_without_mutation("mode")
        retained.chmod(0o600)

        retained.write_bytes(terminal_raw + b"\n")
        reject_without_mutation("content")
        retained.write_bytes(terminal_raw)

        live_journal = initial.canonical_root / "transaction.json"
        live_journal.write_bytes(terminal_raw)
        live_journal.chmod(0o600)
        reject_without_mutation("contradictory-live-journal")
        live_journal.unlink()

        retained_identity = (retained.stat().st_dev, retained.stat().st_ino)
        real_stat = deployment.os.stat
        real_fstat = deployment.os.fstat

        def with_wrong_owner(metadata: os.stat_result) -> os.stat_result:
            fields = list(metadata)
            fields[4] = os.geteuid() + 1
            return os.stat_result(fields)

        def stat_with_wrong_result_owner(
            *args: object,
            **kwargs: object,
        ) -> os.stat_result:
            metadata = real_stat(*args, **kwargs)
            if (metadata.st_dev, metadata.st_ino) == retained_identity:
                return with_wrong_owner(metadata)
            return metadata

        def fstat_with_wrong_result_owner(descriptor: int) -> os.stat_result:
            metadata = real_fstat(descriptor)
            if (metadata.st_dev, metadata.st_ino) == retained_identity:
                return with_wrong_owner(metadata)
            return metadata

        before = tree_state(initial.canonical_root)
        with (
            mock.patch.object(
                deployment.os,
                "stat",
                side_effect=stat_with_wrong_result_owner,
            ),
            mock.patch.object(
                deployment.os,
                "fstat",
                side_effect=fstat_with_wrong_result_owner,
            ),
            self.assertRaises(deployment.DeploymentError),
        ):
            reconcile()
        self.assertEqual(tree_state(initial.canonical_root), before)

        prior_terminal = next(
            path.read_bytes()
            for path in sorted(result_directory.glob("*.json"))
            if path != retained
        )
        before = tree_state(initial.canonical_root)
        with self.assertRaises(deployment.DeploymentError):
            deployment.reconcile_transaction_result(
                deployment.ResultReconciliationRequest(
                    activation=activation,
                    expected_terminal_journal_raw=prior_terminal,
                )
            )
        self.assertEqual(tree_state(initial.canonical_root), before)

        reconciled = reconcile()
        self.assertEqual(reconciled.journal_raw, terminal_raw)

    def test_public_reconciliation_rejects_contradictory_live_selectors_without_mutation(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        initial, _, _, _, staged, activation = self.fixture.staged_routine()
        terminal_raw = run_post_unlink_process_loss(
            deployment,
            activation,
            staged,
            candidate_accepted=True,
        )
        verified = deployment.verify_deployment_stage(activation.stage_receipt)
        prior_active = next(
            artifact.raw
            for artifact in verified.artifacts
            if artifact.role == "prior-active-record"
        )
        active_path = initial.canonical_root / "active.json"
        candidate_active = active_path.read_bytes()
        active_path.write_bytes(prior_active)
        active_path.chmod(0o600)
        before = tree_state(initial.canonical_root)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "selector state disagrees",
        ):
            deployment.reconcile_transaction_result(
                deployment.ResultReconciliationRequest(
                    activation=activation,
                    expected_terminal_journal_raw=terminal_raw,
                )
            )

        self.assertEqual(tree_state(initial.canonical_root), before)
        active_path.write_bytes(candidate_active)
        active_path.chmod(0o600)
        result = deployment.reconcile_transaction_result(
            deployment.ResultReconciliationRequest(
                activation=activation,
                expected_terminal_journal_raw=terminal_raw,
            )
        )
        self.assertEqual(result.outcome, "candidate-active")

    def test_public_reconciliation_covers_control_maintenance_terminal_outcomes(
        self,
    ) -> None:
        for expected_outcome in ("candidate-active", "restored-prior"):
            with (
                self.subTest(outcome=expected_outcome),
                tempfile.TemporaryDirectory() as directory,
            ):
                fixture = ControlMaintenanceActivationFixture(Path(directory).resolve())
                deployment = fixture.deployment()
                prepared = fixture.staged_activation()
                smoke = (
                    AcceptedControlMaintenanceSmoke(prepared)
                    if expected_outcome == "candidate-active"
                    else RejectCandidateAcceptPriorControlMaintenanceSmoke(prepared)
                )
                terminal_raw = _run_post_unlink_process_loss(
                    deployment,
                    prepared.activation,
                    smoke,
                )

                result = deployment.reconcile_transaction_result(
                    deployment.ResultReconciliationRequest(
                        activation=prepared.activation,
                        expected_terminal_journal_raw=terminal_raw,
                    )
                )

                self.assertEqual(result.outcome, expected_outcome)
                self.assertEqual(result.journal_raw, terminal_raw)


if __name__ == "__main__":
    unittest.main()
