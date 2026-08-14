from __future__ import annotations

import copy
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ._activation_recovery_support import (
    install_temporary_paths,
    run_activation_absence_process_loss_cut,
    run_activation_journal_process_loss_cut,
    run_activation_journal_temporary_process_loss_cut,
    run_activation_smoke_process_loss_cut,
)
from ._activation_support import (
    FirstInstallActivationFixture,
    SmokeChildBoundary,
    activation_install_temporary_path,
    assert_exclusive_lock,
    canonical_value,
    exact_restored_absent_inventory,
    expected_smoke_envelope,
    filesystem_identity,
    ordered_activation_artifacts,
    process_descriptor_inventory,
    run_activation_directory_process_loss_cut,
    run_activation_install_process_loss_cut,
    tree_inventory,
)
from ._support import (
    canonical_bytes,
    canonical_document,
    load_deployment_module,
    sha256,
)


def _reseal(value: dict[str, object]) -> bytes:
    unsigned = {key: item for key, item in value.items() if key != "content_sha256"}
    value["content_sha256"] = sha256(canonical_bytes(unsigned))
    return canonical_document(value)


class ActivationRecoveryValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.fixture = FirstInstallActivationFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def deployment(self):
        return load_deployment_module()

    def interrupted_first_install(self):
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        activation = self.fixture.activation_request(prepared)
        run_activation_install_process_loss_cut(
            deployment,
            activation,
            "directory-fsync",
            artifact_index=0,
        )
        raw = (prepared.canonical_root / "transaction.json").read_bytes()
        return deployment, prepared, activation, raw

    def test_recovery_reconciles_only_a_legal_next_journal_temporary(self) -> None:
        deployment, prepared, activation, raw = self.interrupted_first_install()
        journal = deployment._parse_activation_journal(raw)
        artifacts = deployment._ordered_activation_artifacts(prepared.verified)
        removals = deployment._ordered_activation_removal_steps(artifacts)
        precondition = deployment._recorded_first_install_precondition(
            next(
                artifact.raw
                for artifact in prepared.verified.artifacts
                if artifact.role == "rollback-receipt"
            )
        )
        rebound, _, verified = deployment._rebind_first_install_activation(
            activation,
            recorded_precondition=precondition,
        )
        intent, _ = deployment._activation_intent(rebound, activation, verified)
        successor = deployment._immediate_activation_journal_successors(
            intent,
            journal,
            artifacts,
            removals,
        )[0]
        temporary = prepared.canonical_root / (
            f"transaction.{journal.value['transaction_id']}."
            f"{journal.value['sequence'] + 1}.tmp"
        )
        prefix = successor.raw[: max(1, len(successor.raw) // 2)]
        temporary.write_bytes(prefix)
        temporary.chmod(0o600)
        envelope = expected_smoke_envelope(prepared.staged)

        with mock.patch.object(
            deployment,
            "_spawn_activation_smoke_child",
            SmokeChildBoundary(prepared, envelope),
        ):
            result = deployment.recover_transaction(
                deployment.RecoveryRequest(activation, raw)
            )

        self.assertEqual(result.outcome, "candidate-active")
        self.assertFalse(temporary.exists())
        self.assertFalse((prepared.canonical_root / "transaction.json").exists())

    def test_recovery_reconciles_a_real_partial_journal_write(self) -> None:
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        activation = self.fixture.activation_request(prepared)
        run_activation_journal_temporary_process_loss_cut(
            deployment,
            activation,
        )
        transaction = prepared.canonical_root / "transaction.json"
        raw = transaction.read_bytes()
        journal = canonical_value(raw)
        self.assertEqual(journal["phase"], "prepared")
        temporary = prepared.canonical_root / (
            f"transaction.{journal['transaction_id']}.2.tmp"
        )
        self.assertTrue(temporary.exists())
        envelope = expected_smoke_envelope(prepared.staged)

        with mock.patch.object(
            deployment,
            "_spawn_activation_smoke_child",
            SmokeChildBoundary(prepared, envelope),
        ):
            result = deployment.recover_transaction(
                deployment.RecoveryRequest(activation, raw)
            )

        self.assertEqual(result.outcome, "candidate-active")
        self.assertFalse(temporary.exists())
        self.assertFalse(transaction.exists())

    def test_recovery_preserves_an_invalid_journal_temporary(self) -> None:
        deployment, prepared, activation, raw = self.interrupted_first_install()
        journal = deployment._parse_activation_journal(raw)
        temporary = prepared.canonical_root / (
            f"transaction.{journal.value['transaction_id']}."
            f"{journal.value['sequence'] + 1}.tmp"
        )
        temporary.write_bytes(b"not-a-successor-prefix")
        temporary.chmod(0o600)
        before = tree_inventory(prepared.canonical_root)
        smoke = mock.Mock(side_effect=AssertionError("invalid residue must not smoke"))

        with (
            mock.patch.object(deployment, "_spawn_activation_smoke_child", smoke),
            self.assertRaisesRegex(
                deployment.DeploymentError,
                "^activation journal temporary bytes disagree$",
            ),
        ):
            deployment.recover_transaction(deployment.RecoveryRequest(activation, raw))

        self.assertEqual(smoke.call_count, 0)
        self.assertEqual(tree_inventory(prepared.canonical_root), before)
        self.assertEqual(temporary.read_bytes(), b"not-a-successor-prefix")

    def test_recovery_preserves_misnamed_or_multiple_journal_temporaries(self) -> None:
        for residue in ("misnamed", "multiple"):
            with (
                self.subTest(residue=residue),
                tempfile.TemporaryDirectory() as raw_root,
            ):
                fixture = FirstInstallActivationFixture(Path(raw_root).resolve())
                deployment = load_deployment_module()
                prepared = fixture.prepare()
                activation = fixture.activation_request(prepared)
                run_activation_install_process_loss_cut(
                    deployment,
                    activation,
                    "directory-fsync",
                    artifact_index=0,
                )
                transaction = prepared.canonical_root / "transaction.json"
                raw = transaction.read_bytes()
                journal = canonical_value(raw)
                expected_name = (
                    f"transaction.{journal['transaction_id']}."
                    f"{journal['sequence'] + 1}.tmp"
                )
                first_name = (
                    "transaction."
                    + ("0" * 64 if residue == "misnamed" else journal["transaction_id"])
                    + f".{journal['sequence'] + 1}.tmp"
                )
                first = prepared.canonical_root / first_name
                first.write_bytes(b"")
                first.chmod(0o600)
                if residue == "multiple":
                    second = prepared.canonical_root / (
                        f"transaction.{journal['transaction_id']}."
                        f"{journal['sequence'] + 2}.tmp"
                    )
                    second.write_bytes(b"")
                    second.chmod(0o600)
                    self.assertEqual(first.name, expected_name)
                before = tree_inventory(prepared.canonical_root)

                with self.assertRaisesRegex(
                    deployment.DeploymentError,
                    "^activation journal temporary inventory disagrees$",
                ):
                    deployment.recover_transaction(
                        deployment.RecoveryRequest(activation, raw)
                    )

                self.assertEqual(tree_inventory(prepared.canonical_root), before)

    def test_recovery_preserves_a_legal_temp_when_live_state_contradicts(self) -> None:
        deployment, prepared, activation, raw = self.interrupted_first_install()
        journal = deployment._parse_activation_journal(raw)
        artifacts = deployment._ordered_activation_artifacts(prepared.verified)
        removals = deployment._ordered_activation_removal_steps(artifacts)
        precondition = deployment._recorded_first_install_precondition(
            next(
                artifact.raw
                for artifact in prepared.verified.artifacts
                if artifact.role == "rollback-receipt"
            )
        )
        rebound, _, verified = deployment._rebind_first_install_activation(
            activation,
            recorded_precondition=precondition,
        )
        intent, _ = deployment._activation_intent(rebound, activation, verified)
        successor = deployment._immediate_activation_journal_successors(
            intent,
            journal,
            artifacts,
            removals,
        )[0]
        temporary = prepared.canonical_root / (
            f"transaction.{journal.value['transaction_id']}."
            f"{journal.value['sequence'] + 1}.tmp"
        )
        temporary.write_bytes(successor.raw[: len(successor.raw) // 2])
        temporary.chmod(0o600)
        unexpected = prepared.canonical_root / "unowned-residue"
        unexpected.write_bytes(b"preserve me")
        unexpected.chmod(0o600)
        before = tree_inventory(prepared.canonical_root)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "^activation transaction file inventory disagrees$",
        ):
            deployment.recover_transaction(deployment.RecoveryRequest(activation, raw))

        self.assertEqual(tree_inventory(prepared.canonical_root), before)
        self.assertTrue(temporary.exists())

    def test_recovery_rejects_a_sequence_not_derived_from_the_program(self) -> None:
        deployment, prepared, activation, raw = self.interrupted_first_install()
        rebound = copy.deepcopy(canonical_value(raw))
        rebound["sequence"] = 999
        rebound_raw = _reseal(rebound)
        transaction = prepared.canonical_root / "transaction.json"
        transaction.write_bytes(rebound_raw)
        transaction.chmod(0o600)
        before = tree_inventory(prepared.canonical_root)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "^activation journal sequence disagrees with its program state$",
        ):
            deployment.recover_transaction(
                deployment.RecoveryRequest(activation, rebound_raw)
            )

        self.assertEqual(tree_inventory(prepared.canonical_root), before)

    def test_recovery_rejects_a_rebound_previous_journal_digest(self) -> None:
        deployment, prepared, activation, raw = self.interrupted_first_install()
        rebound = copy.deepcopy(canonical_value(raw))
        rebound["previous_journal_sha256"] = "f" * 64
        rebound_raw = _reseal(rebound)
        transaction = prepared.canonical_root / "transaction.json"
        transaction.write_bytes(rebound_raw)
        transaction.chmod(0o600)
        before = tree_inventory(prepared.canonical_root)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "^activation journal chain binding disagrees$",
        ):
            deployment.recover_transaction(
                deployment.RecoveryRequest(activation, rebound_raw)
            )

        self.assertEqual(tree_inventory(prepared.canonical_root), before)

    def test_recovery_rejects_a_cursor_not_derived_from_the_program(self) -> None:
        deployment, prepared, activation, raw = self.interrupted_first_install()
        rebound = copy.deepcopy(canonical_value(raw))
        rebound["pending_step"]["role"] = "wrong-role"
        rebound_raw = _reseal(rebound)
        transaction = prepared.canonical_root / "transaction.json"
        transaction.write_bytes(rebound_raw)
        transaction.chmod(0o600)
        before = tree_inventory(prepared.canonical_root)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "^activation install cursor disagrees$",
        ):
            deployment.recover_transaction(
                deployment.RecoveryRequest(activation, rebound_raw)
            )

        self.assertEqual(tree_inventory(prepared.canonical_root), before)

    def test_public_recovery_requires_every_exact_journal_field(self) -> None:
        deployment, prepared, activation, raw = self.interrupted_first_install()
        original = canonical_value(raw)
        transaction = prepared.canonical_root / "transaction.json"
        self.assertEqual(len(original), 22)
        for missing in tuple(original):
            with self.subTest(missing=missing):
                value = copy.deepcopy(original)
                del value[missing]
                rebound = (
                    canonical_document(value)
                    if missing == "content_sha256"
                    else _reseal(value)
                )
                transaction.write_bytes(rebound)
                transaction.chmod(0o600)
                before = tree_inventory(prepared.canonical_root)

                with self.assertRaises(deployment.DeploymentError):
                    deployment.recover_transaction(
                        deployment.RecoveryRequest(activation, rebound)
                    )

                self.assertEqual(tree_inventory(prepared.canonical_root), before)
        transaction.write_bytes(raw)
        transaction.chmod(0o600)

    def test_public_recovery_normalizes_malformed_journal_types(self) -> None:
        deployment, prepared, activation, raw = self.interrupted_first_install()
        transaction = prepared.canonical_root / "transaction.json"
        for field in ("phase", "pending-operation"):
            with self.subTest(field=field):
                value = copy.deepcopy(canonical_value(raw))
                if field == "phase":
                    value["phase"] = []
                else:
                    value["pending_step"]["operation"] = []
                rebound = _reseal(value)
                transaction.write_bytes(rebound)
                transaction.chmod(0o600)
                before = tree_inventory(prepared.canonical_root)

                with self.assertRaises(deployment.DeploymentError):
                    deployment.recover_transaction(
                        deployment.RecoveryRequest(activation, rebound)
                    )

                self.assertEqual(tree_inventory(prepared.canonical_root), before)
        transaction.write_bytes(raw)
        transaction.chmod(0o600)

    def test_recovery_rejects_unexpected_live_inventory_before_replay(self) -> None:
        deployment, prepared, activation, raw = self.interrupted_first_install()
        unexpected = prepared.canonical_root / "unowned-residue"
        unexpected.write_bytes(b"preserve me")
        unexpected.chmod(0o600)
        before = tree_inventory(prepared.canonical_root)
        install = mock.Mock(side_effect=AssertionError("audit must precede replay"))
        smoke = mock.Mock(side_effect=AssertionError("audit must precede smoke"))

        with (
            mock.patch.object(deployment, "_install_activation_artifact", install),
            mock.patch.object(deployment, "_spawn_activation_smoke_child", smoke),
            self.assertRaisesRegex(
                deployment.DeploymentError,
                "^activation transaction file inventory disagrees$",
            ),
        ):
            deployment.recover_transaction(deployment.RecoveryRequest(activation, raw))

        self.assertEqual(install.call_count, 0)
        self.assertEqual(smoke.call_count, 0)
        self.assertEqual(tree_inventory(prepared.canonical_root), before)

    def test_recovery_rejects_a_missing_completed_install_prefix(self) -> None:
        deployment, prepared, activation, _ = self.interrupted_first_install()
        transaction = prepared.canonical_root / "transaction.json"
        current = deployment._parse_activation_journal(transaction.read_bytes())
        artifacts = ordered_activation_artifacts(prepared)
        following = artifacts[1]
        successor = deployment._activation_journal_generation(
            current.value,
            current,
            phase="control-installing",
            pending_step={
                "operation": "install",
                "index": 1,
                "role": following.role,
            },
        )
        root_fd = os.open(
            prepared.canonical_root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
        )
        try:
            deployment._write_activation_journal(root_fd, successor)
        finally:
            os.close(root_fd)
        completed = artifacts[0]
        completed.installed_path.unlink()
        before = tree_inventory(prepared.canonical_root)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "^activation transaction artifact prefix disagrees$",
        ):
            deployment.recover_transaction(
                deployment.RecoveryRequest(activation, successor.raw)
            )

        self.assertEqual(tree_inventory(prepared.canonical_root), before)

    def test_recovery_continues_from_each_preinstallation_phase(self) -> None:
        for phase in ("prepared", "frozen", "drained"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as raw_root:
                root = Path(raw_root).resolve()
                fixture = FirstInstallActivationFixture(root)
                deployment = load_deployment_module()
                prepared = fixture.prepare()
                activation = fixture.activation_request(prepared)
                run_activation_journal_process_loss_cut(
                    deployment,
                    activation,
                    phase,
                )
                journal_raw = (
                    prepared.canonical_root / "transaction.json"
                ).read_bytes()
                envelope = expected_smoke_envelope(prepared.staged)
                smoke = SmokeChildBoundary(prepared, envelope)

                with mock.patch.object(
                    deployment,
                    "_spawn_activation_smoke_child",
                    smoke,
                ):
                    result = deployment.recover_transaction(
                        deployment.RecoveryRequest(activation, journal_raw)
                    )

                self.assertEqual(result.outcome, "candidate-active")
                self.assertEqual(smoke.calls, 1)
                self.assertFalse(
                    (prepared.canonical_root / "transaction.json").exists()
                )

    def test_recovery_obeys_durable_smoke_acceptance_and_direction(self) -> None:
        cuts = (
            ("accepted-child-return", "candidate-active", 1),
            ("accepted-journal", "candidate-active", 0),
            ("candidate-accepted-journal", "candidate-active", 0),
            ("terminal-journal", "candidate-active", 0),
            ("rejected-child-return", "restored-absent", 1),
            ("absence-journal", "restored-absent", 0),
        )
        for cut, outcome, expected_calls in cuts:
            with self.subTest(cut=cut), tempfile.TemporaryDirectory() as raw_root:
                root = Path(raw_root).resolve()
                fixture = FirstInstallActivationFixture(root)
                deployment = load_deployment_module()
                prepared = fixture.prepare()
                activation = fixture.activation_request(prepared)
                envelope = expected_smoke_envelope(prepared.staged)
                run_activation_smoke_process_loss_cut(
                    deployment,
                    activation,
                    cut=cut,
                    envelope_raw=envelope,
                )
                journal_raw = (
                    prepared.canonical_root / "transaction.json"
                ).read_bytes()
                calls = 0
                bound_lock = prepared.activation_lock
                bound_outcome = outcome
                bound_envelope = envelope

                def recovery_child(
                    *args: object,
                    _lock: Path = bound_lock,
                    _outcome: str = bound_outcome,
                    _envelope: bytes = bound_envelope,
                    **kwargs: object,
                ) -> subprocess.CompletedProcess[bytes]:
                    nonlocal calls
                    calls += 1
                    assert_exclusive_lock(_lock)
                    if _outcome == "candidate-active":
                        return subprocess.CompletedProcess(
                            ("activation-smoke",),
                            0,
                            _envelope,
                            b"",
                        )
                    return subprocess.CompletedProcess(
                        ("activation-smoke",),
                        1,
                        b"",
                        b"candidate rejected",
                    )

                with mock.patch.object(
                    deployment,
                    "_spawn_activation_smoke_child",
                    recovery_child,
                ):
                    result = deployment.recover_transaction(
                        deployment.RecoveryRequest(activation, journal_raw)
                    )

                self.assertEqual(result.outcome, outcome)
                self.assertEqual(calls, expected_calls)
                self.assertFalse(
                    (prepared.canonical_root / "transaction.json").exists()
                )
                if outcome == "restored-absent":
                    self.assertEqual(
                        tree_inventory(prepared.canonical_root),
                        exact_restored_absent_inventory(
                            prepared.activation_lock,
                            result,
                        ),
                    )

    def test_public_recovery_is_crash_total_for_every_absence_step(self) -> None:
        cuts = ("before-mutation", "after-mutation", "parent-fsync")
        for step_index in range(28):
            for cut in cuts:
                with (
                    self.subTest(step_index=step_index, cut=cut),
                    tempfile.TemporaryDirectory() as raw_root,
                ):
                    root = Path(raw_root).resolve()
                    fixture = FirstInstallActivationFixture(root)
                    deployment = load_deployment_module()
                    prepared = fixture.prepare()
                    activation = fixture.activation_request(prepared)
                    lock_identity = os.lstat(prepared.activation_lock)
                    descriptors_before = process_descriptor_inventory()
                    run_activation_absence_process_loss_cut(
                        deployment,
                        prepared,
                        activation,
                        step_index=step_index,
                        cut=cut,
                    )
                    journal_raw = (
                        prepared.canonical_root / "transaction.json"
                    ).read_bytes()
                    forbidden_smoke = mock.Mock(
                        side_effect=AssertionError(
                            "absence recovery must never return to smoke"
                        )
                    )

                    with mock.patch.object(
                        deployment,
                        "_spawn_activation_smoke_child",
                        forbidden_smoke,
                    ):
                        result = deployment.recover_transaction(
                            deployment.RecoveryRequest(activation, journal_raw)
                        )

                    self.assertEqual(result.outcome, "restored-absent")
                    self.assertEqual(forbidden_smoke.call_count, 0)
                    self.assertEqual(
                        tree_inventory(prepared.canonical_root),
                        exact_restored_absent_inventory(
                            prepared.activation_lock,
                            result,
                        ),
                    )
                    self.assertEqual(
                        (
                            prepared.activation_lock.lstat().st_dev,
                            prepared.activation_lock.lstat().st_ino,
                        ),
                        (lock_identity.st_dev, lock_identity.st_ino),
                    )
                    self.assertEqual(
                        install_temporary_paths(prepared.canonical_root), ()
                    )
                    self.assertEqual(
                        process_descriptor_inventory(),
                        descriptors_before,
                    )

    def test_recovery_preserves_an_invalid_pending_install_temporary(self) -> None:
        deployment, prepared, activation, raw = self.interrupted_first_install()
        journal = canonical_value(raw)
        artifact = ordered_activation_artifacts(prepared)[0]
        temporary = activation_install_temporary_path(
            artifact,
            journal["transaction_id"],
            0,
        )
        temporary.write_bytes(b"not-an-artifact-prefix")
        temporary.chmod(0o600)
        before = tree_inventory(prepared.canonical_root)
        install = mock.Mock(side_effect=AssertionError("audit must precede replay"))

        with (
            mock.patch.object(deployment, "_install_activation_artifact", install),
            self.assertRaises(deployment.DeploymentError),
        ):
            deployment.recover_transaction(deployment.RecoveryRequest(activation, raw))

        self.assertEqual(install.call_count, 0)
        self.assertEqual(tree_inventory(prepared.canonical_root), before)

    def test_absence_audit_rejects_each_prefix_contradiction(self) -> None:
        cases = ("resurrected-prefix", "missing-suffix", "mutated-current")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as raw_root:
                fixture = FirstInstallActivationFixture(Path(raw_root).resolve())
                deployment = load_deployment_module()
                prepared = fixture.prepare()
                activation = fixture.activation_request(prepared)
                step_index = 1 if case == "resurrected-prefix" else 0
                run_activation_absence_process_loss_cut(
                    deployment,
                    prepared,
                    activation,
                    step_index=step_index,
                    cut="before-mutation",
                )
                artifacts = deployment._ordered_activation_artifacts(prepared.verified)
                removals = deployment._ordered_activation_removal_steps(artifacts)
                if case == "resurrected-prefix":
                    artifact = removals[0].artifact
                    artifact.installed_path.write_bytes(artifact.raw)
                    artifact.installed_path.chmod(artifact.installed["mode"])
                elif case == "missing-suffix":
                    removals[1].artifact.installed_path.unlink()
                else:
                    artifact = removals[0].artifact
                    artifact.installed_path.chmod(0o600)
                    artifact.installed_path.write_bytes(b"mutated")
                    artifact.installed_path.chmod(artifact.installed["mode"])
                journal_raw = (
                    prepared.canonical_root / "transaction.json"
                ).read_bytes()
                before = tree_inventory(prepared.canonical_root)
                smoke = mock.Mock(
                    side_effect=AssertionError("absence contradiction must not smoke")
                )

                with (
                    mock.patch.object(
                        deployment,
                        "_spawn_activation_smoke_child",
                        smoke,
                    ),
                    self.assertRaises(deployment.DeploymentError),
                ):
                    deployment.recover_transaction(
                        deployment.RecoveryRequest(activation, journal_raw)
                    )

                self.assertEqual(smoke.call_count, 0)
                self.assertEqual(tree_inventory(prepared.canonical_root), before)

    def test_terminal_recovery_rejects_live_outcome_contradiction(self) -> None:
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        activation = self.fixture.activation_request(prepared)
        envelope = expected_smoke_envelope(prepared.staged)
        run_activation_smoke_process_loss_cut(
            deployment,
            activation,
            cut="terminal-journal",
            envelope_raw=envelope,
        )
        ordered_activation_artifacts(prepared)[0].installed_path.unlink()
        journal_raw = (prepared.canonical_root / "transaction.json").read_bytes()
        before = tree_inventory(prepared.canonical_root)
        unlink = mock.Mock(side_effect=AssertionError("audit must precede unlink"))

        with (
            mock.patch.object(deployment, "_unlink_activation_journal", unlink),
            self.assertRaises(deployment.DeploymentError),
        ):
            deployment.recover_transaction(
                deployment.RecoveryRequest(activation, journal_raw)
            )

        self.assertEqual(unlink.call_count, 0)
        self.assertEqual(tree_inventory(prepared.canonical_root), before)

    def test_recovery_rejects_activation_lock_identity_drift(self) -> None:
        deployment, prepared, activation, raw = self.interrupted_first_install()
        replacement = self.root / "replacement-lock"
        replacement.write_bytes(b"")
        replacement.chmod(0o600)
        os.replace(replacement, prepared.activation_lock)
        before = tree_inventory(prepared.canonical_root)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "^activation lock binding disagrees$",
        ):
            deployment.recover_transaction(deployment.RecoveryRequest(activation, raw))

        self.assertEqual(tree_inventory(prepared.canonical_root), before)

    def test_recovery_rejects_a_nonempty_activation_lock_before_replay(self) -> None:
        deployment, prepared, activation, raw = self.interrupted_first_install()
        prepared.activation_lock.write_bytes(b"not lock state")
        prepared.activation_lock.chmod(0o600)
        before = tree_inventory(prepared.canonical_root)
        forbidden = {
            name: mock.Mock(side_effect=AssertionError(f"{name} must not run"))
            for name in (
                "_rebind_first_install_activation",
                "_install_activation_artifact",
                "_spawn_activation_smoke_child",
                "_write_activation_journal",
                "_unlink_activation_journal",
            )
        }

        with (
            mock.patch.object(
                deployment,
                "_rebind_first_install_activation",
                forbidden["_rebind_first_install_activation"],
            ),
            mock.patch.object(
                deployment,
                "_install_activation_artifact",
                forbidden["_install_activation_artifact"],
            ),
            mock.patch.object(
                deployment,
                "_spawn_activation_smoke_child",
                forbidden["_spawn_activation_smoke_child"],
            ),
            mock.patch.object(
                deployment,
                "_write_activation_journal",
                forbidden["_write_activation_journal"],
            ),
            mock.patch.object(
                deployment,
                "_unlink_activation_journal",
                forbidden["_unlink_activation_journal"],
            ),
            self.assertRaisesRegex(
                deployment.DeploymentError,
                "^activation lock must be empty$",
            ),
        ):
            deployment.recover_transaction(deployment.RecoveryRequest(activation, raw))

        self.assertEqual(tree_inventory(prepared.canonical_root), before)
        self.assertEqual(prepared.activation_lock.read_bytes(), b"not lock state")
        self.assertEqual(
            (prepared.canonical_root / "transaction.json").read_bytes(),
            raw,
        )
        for name, boundary in forbidden.items():
            self.assertEqual(boundary.call_count, 0, name)

    def test_recovery_rejects_read_only_activation_lock_drift_before_replay(
        self,
    ) -> None:
        deployment, prepared, activation, raw = self.interrupted_first_install()
        prepared.activation_lock.chmod(0o400)
        before = tree_inventory(prepared.canonical_root)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "^activation lock mode must be 0600$",
        ):
            deployment.recover_transaction(deployment.RecoveryRequest(activation, raw))

        self.assertEqual(tree_inventory(prepared.canonical_root), before)
        self.assertEqual(prepared.activation_lock.stat().st_mode & 0o777, 0o400)
        self.assertEqual(
            (prepared.canonical_root / "transaction.json").read_bytes(),
            raw,
        )

    @unittest.skipUnless(sys.platform == "darwin", "macOS ACL semantics required")
    def test_recovery_rejects_a_permissive_root_acl_before_replay(self) -> None:
        deployment, prepared, activation, raw = self.interrupted_first_install()
        subprocess.run(
            [
                "/bin/chmod",
                "+a",
                "everyone allow read",
                str(prepared.canonical_root),
            ],
            check=True,
        )
        forbidden = {
            name: mock.Mock(side_effect=AssertionError(f"{name} must not run"))
            for name in (
                "_rebind_first_install_activation",
                "_install_activation_artifact",
                "_spawn_activation_smoke_child",
                "_write_activation_journal",
                "_unlink_activation_journal",
            )
        }

        try:
            before = tree_inventory(prepared.canonical_root)
            acl_evidence = subprocess.run(
                ["/bin/ls", "-led", str(prepared.canonical_root)],
                check=True,
                capture_output=True,
            ).stdout
            with (
                mock.patch.object(
                    deployment,
                    "_rebind_first_install_activation",
                    forbidden["_rebind_first_install_activation"],
                ),
                mock.patch.object(
                    deployment,
                    "_install_activation_artifact",
                    forbidden["_install_activation_artifact"],
                ),
                mock.patch.object(
                    deployment,
                    "_spawn_activation_smoke_child",
                    forbidden["_spawn_activation_smoke_child"],
                ),
                mock.patch.object(
                    deployment,
                    "_write_activation_journal",
                    forbidden["_write_activation_journal"],
                ),
                mock.patch.object(
                    deployment,
                    "_unlink_activation_journal",
                    forbidden["_unlink_activation_journal"],
                ),
                self.assertRaisesRegex(
                    deployment.DeploymentError,
                    "^activation canonical root has a permissive ACL entry$",
                ),
            ):
                deployment.recover_transaction(
                    deployment.RecoveryRequest(activation, raw)
                )

            self.assertEqual(tree_inventory(prepared.canonical_root), before)
            self.assertEqual(
                subprocess.run(
                    ["/bin/ls", "-led", str(prepared.canonical_root)],
                    check=True,
                    capture_output=True,
                ).stdout,
                acl_evidence,
            )
            self.assertEqual(
                (prepared.canonical_root / "transaction.json").read_bytes(),
                raw,
            )
            for name, boundary in forbidden.items():
                self.assertEqual(boundary.call_count, 0, name)
        finally:
            subprocess.run(
                ["/bin/chmod", "-N", str(prepared.canonical_root)],
                check=True,
            )

    @unittest.skipUnless(sys.platform == "darwin", "macOS ACL semantics required")
    def test_recovery_rejects_a_permissive_lock_acl_before_replay(self) -> None:
        deployment, prepared, activation, raw = self.interrupted_first_install()
        subprocess.run(
            [
                "/bin/chmod",
                "+a",
                "everyone allow read",
                str(prepared.activation_lock),
            ],
            check=True,
        )
        forbidden = {
            name: mock.Mock(side_effect=AssertionError(f"{name} must not run"))
            for name in (
                "_rebind_first_install_activation",
                "_install_activation_artifact",
                "_spawn_activation_smoke_child",
                "_write_activation_journal",
                "_unlink_activation_journal",
            )
        }

        try:
            before = tree_inventory(prepared.canonical_root)
            acl_evidence = subprocess.run(
                ["/bin/ls", "-led", str(prepared.activation_lock)],
                check=True,
                capture_output=True,
            ).stdout
            with (
                mock.patch.object(
                    deployment,
                    "_rebind_first_install_activation",
                    forbidden["_rebind_first_install_activation"],
                ),
                mock.patch.object(
                    deployment,
                    "_install_activation_artifact",
                    forbidden["_install_activation_artifact"],
                ),
                mock.patch.object(
                    deployment,
                    "_spawn_activation_smoke_child",
                    forbidden["_spawn_activation_smoke_child"],
                ),
                mock.patch.object(
                    deployment,
                    "_write_activation_journal",
                    forbidden["_write_activation_journal"],
                ),
                mock.patch.object(
                    deployment,
                    "_unlink_activation_journal",
                    forbidden["_unlink_activation_journal"],
                ),
                self.assertRaisesRegex(
                    deployment.DeploymentError,
                    "^activation lock has a permissive ACL entry$",
                ),
            ):
                deployment.recover_transaction(
                    deployment.RecoveryRequest(activation, raw)
                )

            self.assertEqual(tree_inventory(prepared.canonical_root), before)
            self.assertEqual(
                subprocess.run(
                    ["/bin/ls", "-led", str(prepared.activation_lock)],
                    check=True,
                    capture_output=True,
                ).stdout,
                acl_evidence,
            )
            self.assertEqual(
                (prepared.canonical_root / "transaction.json").read_bytes(),
                raw,
            )
            for name, boundary in forbidden.items():
                self.assertEqual(boundary.call_count, 0, name)
        finally:
            subprocess.run(
                ["/bin/chmod", "-N", str(prepared.activation_lock)],
                check=True,
            )

    def test_activation_rejects_a_nonempty_lock_before_its_first_journal(self) -> None:
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        activation = self.fixture.activation_request(prepared)
        prepared.activation_lock.write_bytes(b"not lock state")
        prepared.activation_lock.chmod(0o600)
        before = tree_inventory(prepared.canonical_root)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "^first-install activation lock must be empty$",
        ):
            deployment.activate_staged(activation)

        self.assertEqual(tree_inventory(prepared.canonical_root), before)
        self.assertEqual(prepared.activation_lock.read_bytes(), b"not lock state")
        self.assertFalse((prepared.canonical_root / "transaction.json").exists())
        for artifact in prepared.verified.artifacts:
            self.assertFalse(artifact.installed_path.exists(), artifact.role)

    def test_recovery_preserves_noncurrent_directory_mode_contradictions(
        self,
    ) -> None:
        for case in ("completed-prefix", "current-extra-bits", "unexpected"):
            with (
                self.subTest(case=case),
                tempfile.TemporaryDirectory() as location,
            ):
                fixture = FirstInstallActivationFixture(Path(location).resolve())
                deployment = load_deployment_module()
                prepared = fixture.prepare()
                activation = fixture.activation_request(prepared)
                cleanup_paths: list[Path] = []
                if case == "completed-prefix":
                    run_activation_install_process_loss_cut(
                        deployment,
                        activation,
                        "temp-create",
                        artifact_index=3,
                    )
                    target = prepared.canonical_root / "controller"
                    target.chmod(0)
                    cleanup_paths.append(target)
                else:
                    run_activation_directory_process_loss_cut(
                        deployment,
                        activation,
                        prepared,
                        "mkdir-return",
                    )
                    client = prepared.canonical_root / "client"
                    cleanup_paths.append(client)
                    if case == "current-extra-bits":
                        target = client
                        target.chmod(0o750)
                    else:
                        target = prepared.canonical_root / "unexpected"
                        target.mkdir(mode=0o700)
                        target.chmod(0)
                        cleanup_paths.append(target)
                transaction = prepared.canonical_root / "transaction.json"
                journal_raw = transaction.read_bytes()
                before = tree_inventory(prepared.canonical_root)
                target_identity = filesystem_identity(target)
                lock_identity = filesystem_identity(prepared.activation_lock)
                forbidden = {
                    name: mock.Mock(side_effect=AssertionError(f"{name} must not run"))
                    for name in (
                        "chmod",
                        "mkdir",
                        "_install_activation_artifact",
                        "_spawn_activation_smoke_child",
                        "_write_activation_journal",
                        "_unlink_activation_journal",
                    )
                }

                try:
                    with (
                        mock.patch.object(
                            deployment.os,
                            "chmod",
                            forbidden["chmod"],
                        ),
                        mock.patch.object(
                            deployment.os,
                            "mkdir",
                            forbidden["mkdir"],
                        ),
                        mock.patch.object(
                            deployment,
                            "_install_activation_artifact",
                            forbidden["_install_activation_artifact"],
                        ),
                        mock.patch.object(
                            deployment,
                            "_spawn_activation_smoke_child",
                            forbidden["_spawn_activation_smoke_child"],
                        ),
                        mock.patch.object(
                            deployment,
                            "_write_activation_journal",
                            forbidden["_write_activation_journal"],
                        ),
                        mock.patch.object(
                            deployment,
                            "_unlink_activation_journal",
                            forbidden["_unlink_activation_journal"],
                        ),
                        self.assertRaises(deployment.DeploymentError),
                    ):
                        deployment.recover_transaction(
                            deployment.RecoveryRequest(activation, journal_raw)
                        )

                    self.assertEqual(tree_inventory(prepared.canonical_root), before)
                    self.assertEqual(filesystem_identity(target), target_identity)
                    self.assertEqual(transaction.read_bytes(), journal_raw)
                    self.assertEqual(
                        filesystem_identity(prepared.activation_lock),
                        lock_identity,
                    )
                    for name, boundary in forbidden.items():
                        self.assertEqual(boundary.call_count, 0, name)
                finally:
                    for path in reversed(cleanup_paths):
                        if path.exists():
                            path.chmod(0o700)

    def test_recovery_normalizes_only_the_opaque_current_directory_before_audit(
        self,
    ) -> None:
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        activation = self.fixture.activation_request(prepared)
        run_activation_directory_process_loss_cut(
            deployment,
            activation,
            prepared,
            "mkdir-return",
        )
        client = prepared.canonical_root / "client"
        client.chmod(0o700)
        hidden = client / "unexpected"
        hidden.write_bytes(b"retained contradiction\n")
        hidden.chmod(0o600)
        client.chmod(0)
        before_identity = filesystem_identity(client)
        transaction = prepared.canonical_root / "transaction.json"
        journal_raw = transaction.read_bytes()
        lock_identity = filesystem_identity(prepared.activation_lock)
        forbidden = {
            name: mock.Mock(side_effect=AssertionError(f"{name} must not run"))
            for name in (
                "mkdir",
                "_install_activation_artifact",
                "_spawn_activation_smoke_child",
                "_write_activation_journal",
                "_unlink_activation_journal",
            )
        }

        with (
            mock.patch.object(deployment.os, "mkdir", forbidden["mkdir"]),
            mock.patch.object(
                deployment,
                "_install_activation_artifact",
                forbidden["_install_activation_artifact"],
            ),
            mock.patch.object(
                deployment,
                "_spawn_activation_smoke_child",
                forbidden["_spawn_activation_smoke_child"],
            ),
            mock.patch.object(
                deployment,
                "_write_activation_journal",
                forbidden["_write_activation_journal"],
            ),
            mock.patch.object(
                deployment,
                "_unlink_activation_journal",
                forbidden["_unlink_activation_journal"],
            ),
            self.assertRaises(deployment.DeploymentError),
        ):
            deployment.recover_transaction(
                deployment.RecoveryRequest(activation, journal_raw)
            )

        after_identity = filesystem_identity(client)
        self.assertEqual(
            (after_identity[0], after_identity[1]),
            (before_identity[0], before_identity[1]),
        )
        self.assertEqual(stat.S_IMODE(client.lstat().st_mode), 0o700)
        self.assertEqual(hidden.read_bytes(), b"retained contradiction\n")
        self.assertEqual(transaction.read_bytes(), journal_raw)
        self.assertEqual(
            filesystem_identity(prepared.activation_lock),
            lock_identity,
        )
        for name, boundary in forbidden.items():
            self.assertEqual(boundary.call_count, 0, name)

    def test_recovery_rechecks_the_visible_root_immediately_before_smoke(self) -> None:
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        activation = self.fixture.activation_request(prepared)
        envelope = expected_smoke_envelope(prepared.staged)
        run_activation_smoke_process_loss_cut(
            deployment,
            activation,
            cut="accepted-child-return",
            envelope_raw=envelope,
        )
        journal_raw = (prepared.canonical_root / "transaction.json").read_bytes()
        original_audit = deployment._audit_first_install_live_state
        displaced = prepared.canonical_root.with_name("task-witness-displaced")
        moved = False

        def audit_then_replace_root(*args: object, **kwargs: object) -> None:
            nonlocal moved
            original_audit(*args, **kwargs)
            journal = args[2]
            if not moved and journal.value["phase"] == "candidate-smoke":
                prepared.canonical_root.rename(displaced)
                prepared.canonical_root.mkdir(mode=0o700)
                moved = True

        smoke = mock.Mock(side_effect=AssertionError("root drift must precede smoke"))
        with (
            mock.patch.object(
                deployment,
                "_audit_first_install_live_state",
                audit_then_replace_root,
            ),
            mock.patch.object(deployment, "_spawn_activation_smoke_child", smoke),
            self.assertRaisesRegex(
                deployment.DeploymentError,
                "^activation canonical root changed while acquiring exclusivity$",
            ),
        ):
            deployment.recover_transaction(
                deployment.RecoveryRequest(activation, journal_raw)
            )

        self.assertTrue(moved)
        self.assertEqual(smoke.call_count, 0)
        self.assertEqual((displaced / "transaction.json").read_bytes(), journal_raw)

    def test_activation_rechecks_the_visible_root_before_its_first_journal(
        self,
    ) -> None:
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        activation = self.fixture.activation_request(prepared)
        original_intent = deployment._activation_intent
        displaced = prepared.canonical_root.with_name("task-witness-displaced")

        def intent_then_replace_root(*args: object, **kwargs: object):
            result = original_intent(*args, **kwargs)
            prepared.canonical_root.rename(displaced)
            prepared.canonical_root.mkdir(mode=0o700)
            return result

        writer = mock.Mock(side_effect=AssertionError("root drift must precede write"))
        with (
            mock.patch.object(
                deployment,
                "_activation_intent",
                intent_then_replace_root,
            ),
            mock.patch.object(deployment, "_write_activation_journal", writer),
            self.assertRaisesRegex(
                deployment.DeploymentError,
                "^activation canonical root changed while acquiring exclusivity$",
            ),
        ):
            deployment.activate_staged(activation)

        self.assertEqual(writer.call_count, 0)
        self.assertFalse((displaced / "transaction.json").exists())
        self.assertFalse((prepared.canonical_root / "transaction.json").exists())

    def test_recorded_recovery_precondition_schema_is_closed(self) -> None:
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        rollback_raw = next(
            artifact.raw
            for artifact in prepared.verified.artifacts
            if artifact.role == "rollback-receipt"
        )
        for mutation in ("missing", "malformed"):
            with self.subTest(mutation=mutation):
                value = copy.deepcopy(canonical_value(rollback_raw))
                if mutation == "missing":
                    del value["precondition"]
                else:
                    value["precondition"]["root_identity"] = [0] * 7
                rebound = _reseal(value)
                with self.assertRaises(deployment.DeploymentError):
                    deployment._recorded_first_install_precondition(rebound)


if __name__ == "__main__":
    unittest.main()
