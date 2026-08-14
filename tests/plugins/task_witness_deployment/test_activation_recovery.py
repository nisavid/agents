from __future__ import annotations

import copy
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ._activation_recovery_support import run_activation_smoke_process_loss_cut
from ._activation_support import (
    DIRECTORY_PROCESS_LOSS_CUTS,
    INSTALL_PROCESS_LOSS_CUTS,
    FirstInstallActivationFixture,
    SmokeChildBoundary,
    activation_install_temporary_path,
    canonical_value,
    exact_retained_result_inventory,
    expected_smoke_envelope,
    filesystem_identity,
    ordered_activation_artifacts,
    process_descriptor_inventory,
    run_activation_directory_process_loss_cut,
    run_activation_install_process_loss_cut,
    thaw_json,
    tree_inventory,
)
from ._support import (
    canonical_bytes,
    canonical_document,
    load_deployment_module,
    sha256,
)


def reseal_journal(value: dict[str, object]) -> bytes:
    unsigned = {key: item for key, item in value.items() if key != "content_sha256"}
    value["content_sha256"] = sha256(canonical_bytes(unsigned))
    return canonical_document(value)


def rebind_journal_transaction_id(value: dict[str, object]) -> None:
    identity = {
        "contract": "task-witness-activation-intent-v1",
        **{
            key: value[key]
            for key in (
                "transaction_class",
                "canonical_root",
                "effective_uid",
                "activation_lock",
                "outer_maintenance_transaction_sha256",
                "stage",
                "prior",
                "candidate",
                "rollback_authority",
                "preimage",
            )
        },
    }
    value["transaction_id"] = sha256(canonical_bytes(identity))


class ActivationRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.fixture = FirstInstallActivationFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def deployment(self):
        return load_deployment_module()

    def assert_exact_active_install(self, prepared: object, result: object) -> None:
        expected_inventory: list[tuple[str, str, int, int, str]] = [
            ("activation.lock", "file", 0o600, 0, sha256(b"")),
        ]
        expected_directories: set[str] = set()
        for artifact in prepared.verified.artifacts:
            relative = Path(artifact.relative_path)
            for parent in relative.parents:
                if parent == Path("."):
                    continue
                expected_directories.add(parent.as_posix())
            expected_inventory.append(
                (
                    relative.as_posix(),
                    "file",
                    artifact.installed["mode"],
                    len(artifact.raw),
                    sha256(artifact.raw),
                )
            )
            metadata = artifact.installed_path.lstat()
            self.assertTrue(stat.S_ISREG(metadata.st_mode), artifact.role)
            self.assertEqual(metadata.st_uid, artifact.installed["owner"])
            self.assertEqual(metadata.st_nlink, 1)
            self.assertEqual(
                stat.S_IMODE(metadata.st_mode),
                artifact.installed["mode"],
            )
            self.assertEqual(artifact.installed_path.read_bytes(), artifact.raw)
        expected_inventory.extend(
            (relative, "directory", 0o700, 0, "") for relative in expected_directories
        )
        expected_inventory.extend(exact_retained_result_inventory(result))
        self.assertEqual(
            tree_inventory(prepared.canonical_root),
            tuple(sorted(expected_inventory)),
        )

    def assert_install_process_loss_state(
        self,
        prepared: object,
        journal: dict[str, object],
        artifact_index: int,
        cut: str,
    ) -> None:
        artifact = ordered_activation_artifacts(prepared)[artifact_index]
        temporary = activation_install_temporary_path(
            artifact,
            journal["transaction_id"],
            artifact_index,
        )
        final = artifact.installed_path
        if cut in {"temp-create", "partial-write", "file-fsync"}:
            self.assertFalse(final.exists())
            metadata = temporary.lstat()
            self.assertTrue(stat.S_ISREG(metadata.st_mode))
            self.assertEqual(metadata.st_uid, artifact.installed["owner"])
            self.assertEqual(metadata.st_nlink, 1)
            if cut == "temp-create":
                self.assertEqual(metadata.st_size, 0)
                self.assertEqual(stat.S_IMODE(metadata.st_mode), 0)
            elif cut == "partial-write":
                expected_length = max(1, len(artifact.raw) // 2)
                self.assertEqual(
                    temporary.read_bytes(),
                    artifact.raw[:expected_length],
                )
                self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
            else:
                self.assertEqual(temporary.read_bytes(), artifact.raw)
                self.assertEqual(
                    stat.S_IMODE(metadata.st_mode),
                    artifact.installed["mode"],
                )
            return
        self.assertEqual(final.read_bytes(), artifact.raw)
        final_metadata = final.lstat()
        self.assertEqual(
            stat.S_IMODE(final_metadata.st_mode),
            artifact.installed["mode"],
        )
        if cut == "link-finalization":
            temporary_metadata = temporary.lstat()
            self.assertEqual(
                (temporary_metadata.st_dev, temporary_metadata.st_ino),
                (final_metadata.st_dev, final_metadata.st_ino),
            )
            self.assertEqual(temporary_metadata.st_nlink, 2)
            self.assertEqual(final_metadata.st_nlink, 2)
        else:
            self.assertFalse(temporary.exists())
            self.assertEqual(final_metadata.st_nlink, 1)

    def test_public_activation_normalizes_new_directories_under_restrictive_umask(
        self,
    ) -> None:
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        activation = self.fixture.activation_request(prepared)
        artifacts = ordered_activation_artifacts(prepared)
        self.assertEqual(artifacts[1].role, "client")
        lock_identity = filesystem_identity(prepared.activation_lock)
        descriptors_before = process_descriptor_inventory()
        envelope_raw = expected_smoke_envelope(prepared.staged)
        smoke = SmokeChildBoundary(prepared, envelope_raw)

        original_umask = os.umask(0o777)
        try:
            with mock.patch.object(
                deployment,
                "_spawn_activation_smoke_child",
                smoke,
            ):
                result = deployment.activate_staged(activation)
        finally:
            os.umask(original_umask)

        transaction = prepared.canonical_root / "transaction.json"
        self.assertEqual(result.outcome, "candidate-active")
        self.assertEqual(smoke.calls, 1)
        self.assertFalse(transaction.exists())
        self.assertFalse(
            tuple(prepared.canonical_root.rglob(".task-witness-install-*.tmp"))
        )
        self.assert_exact_active_install(prepared, result)
        self.assertEqual(
            filesystem_identity(prepared.activation_lock),
            lock_identity,
        )
        self.assertEqual(process_descriptor_inventory(), descriptors_before)

    def test_public_recovery_converges_after_directory_process_loss(self) -> None:
        deployment = self.deployment()
        for cut in DIRECTORY_PROCESS_LOSS_CUTS:
            with (
                self.subTest(cut=cut),
                tempfile.TemporaryDirectory() as location,
            ):
                fixture = FirstInstallActivationFixture(Path(location).resolve())
                prepared = fixture.prepare()
                activation = fixture.activation_request(prepared)
                artifacts = ordered_activation_artifacts(prepared)
                lock_identity = filesystem_identity(prepared.activation_lock)
                descriptors_before = process_descriptor_inventory()

                run_activation_directory_process_loss_cut(
                    deployment,
                    activation,
                    prepared,
                    cut,
                )

                transaction = prepared.canonical_root / "transaction.json"
                journal_raw = transaction.read_bytes()
                journal = canonical_value(journal_raw)
                self.assertEqual(journal["phase"], "control-installing")
                self.assertEqual(
                    journal["pending_step"],
                    {"operation": "install", "index": 1, "role": "client"},
                )
                client_directory = artifacts[1].installed_path.parent
                self.assertEqual(
                    stat.S_IMODE(client_directory.lstat().st_mode),
                    0 if cut == "mkdir-return" else 0o700,
                )
                envelope_raw = expected_smoke_envelope(prepared.staged)
                smoke = SmokeChildBoundary(prepared, envelope_raw)

                with mock.patch.object(
                    deployment,
                    "_spawn_activation_smoke_child",
                    smoke,
                ):
                    result = deployment.recover_transaction(
                        deployment.RecoveryRequest(
                            activation=activation,
                            expected_journal_raw=journal_raw,
                        )
                    )

                self.assertEqual(result.outcome, "candidate-active")
                self.assertEqual(smoke.calls, 1)
                self.assertFalse(transaction.exists())
                self.assertFalse(
                    tuple(prepared.canonical_root.rglob(".task-witness-install-*.tmp"))
                )
                self.assert_exact_active_install(prepared, result)
                self.assertEqual(
                    filesystem_identity(prepared.activation_lock),
                    lock_identity,
                )
                self.assertEqual(
                    process_descriptor_inventory(),
                    descriptors_before,
                )

    def test_recovery_converges_after_install_process_loss(self) -> None:
        deployment = self.deployment()
        for artifact_index in range(16):
            for cut in INSTALL_PROCESS_LOSS_CUTS:
                with (
                    self.subTest(artifact_index=artifact_index, cut=cut),
                    tempfile.TemporaryDirectory() as location,
                ):
                    fixture = FirstInstallActivationFixture(Path(location).resolve())
                    prepared = fixture.prepare()
                    activation = fixture.activation_request(prepared)
                    artifacts = ordered_activation_artifacts(prepared)
                    self.assertEqual(len(artifacts), 16)
                    lock_identity = filesystem_identity(prepared.activation_lock)
                    descriptors_before = process_descriptor_inventory()

                    run_activation_install_process_loss_cut(
                        deployment,
                        activation,
                        cut,
                        artifact_index=artifact_index,
                    )

                    transaction_path = prepared.canonical_root / "transaction.json"
                    expected_journal_raw = transaction_path.read_bytes()
                    journal = canonical_value(expected_journal_raw)
                    self.assertEqual(journal["phase"], "control-installing")
                    self.assertEqual(
                        journal["pending_step"],
                        {
                            "operation": "install",
                            "index": artifact_index,
                            "role": artifacts[artifact_index].role,
                        },
                    )
                    self.assert_install_process_loss_state(
                        prepared,
                        journal,
                        artifact_index,
                        cut,
                    )
                    envelope_raw = expected_smoke_envelope(prepared.staged)
                    smoke = SmokeChildBoundary(prepared, envelope_raw)

                    with mock.patch.object(
                        deployment,
                        "_spawn_activation_smoke_child",
                        smoke,
                    ):
                        result = deployment.recover_transaction(
                            deployment.RecoveryRequest(
                                activation=activation,
                                expected_journal_raw=expected_journal_raw,
                            )
                        )

                    candidate_sha256 = sha256(prepared.staged.deployment_raw)
                    self.assertEqual(
                        result.transaction_id,
                        journal["transaction_id"],
                    )
                    self.assertEqual(result.outcome, "candidate-active")
                    self.assertEqual(
                        result.candidate_receipt_sha256,
                        candidate_sha256,
                    )
                    self.assertEqual(
                        result.active_receipt_sha256,
                        candidate_sha256,
                    )
                    self.assertEqual(
                        result.accepted_envelope_sha256,
                        sha256(envelope_raw),
                    )
                    self.assertEqual(
                        result.journal_sha256,
                        sha256(result.journal_raw),
                    )
                    terminal = canonical_value(result.journal_raw)
                    self.assertEqual(terminal, thaw_json(result.journal_value))
                    self.assertEqual(terminal["phase"], "terminal")
                    self.assertEqual(terminal["sequence"], 23)
                    self.assertIsNone(terminal["pending_step"])
                    self.assertEqual(
                        terminal["terminal_result"],
                        {
                            "accepted_envelope_sha256": sha256(envelope_raw),
                            "active_receipt_sha256": candidate_sha256,
                            "candidate_receipt_sha256": candidate_sha256,
                            "failure_class": None,
                            "outcome": "candidate-active",
                        },
                    )
                    self.assertEqual(smoke.calls, 1)
                    self.assertFalse(transaction_path.exists())
                    self.assert_exact_active_install(prepared, result)
                    self.assertEqual(
                        filesystem_identity(prepared.activation_lock),
                        lock_identity,
                    )
                    self.assertEqual(
                        process_descriptor_inventory(),
                        descriptors_before,
                    )

    def test_rollback_receipt_records_the_original_precondition(self) -> None:
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        plan = deployment.prepare_first_install(prepared.request).plan
        rollback = next(
            artifact
            for artifact in prepared.verified.artifacts
            if artifact.role == "rollback-receipt"
        )
        receipt = canonical_value(rollback.raw)

        self.assertEqual(
            receipt["precondition"],
            {
                "root_identity": list(plan.precondition.root_identity),
                "activation_lock_identity": list(
                    plan.precondition.activation_lock_identity
                ),
            },
        )

    def test_recovery_continues_after_first_installed_artifact(self) -> None:
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        activation = self.fixture.activation_request(prepared)
        run_activation_install_process_loss_cut(
            deployment,
            activation,
            "directory-fsync",
            artifact_index=0,
        )
        expected_journal_raw = (
            prepared.canonical_root / "transaction.json"
        ).read_bytes()
        parsed_journal = deployment._parse_activation_journal(expected_journal_raw)
        self.assertEqual(parsed_journal.raw, expected_journal_raw)
        request = deployment.RecoveryRequest(
            activation=activation,
            expected_journal_raw=expected_journal_raw,
        )
        envelope_raw = expected_smoke_envelope(prepared.staged)
        smoke = SmokeChildBoundary(prepared, envelope_raw)

        with mock.patch.object(
            deployment,
            "_spawn_activation_smoke_child",
            smoke,
        ):
            result = deployment.recover_transaction(request)

        candidate_sha256 = sha256(prepared.staged.deployment_raw)
        self.assertEqual(result.outcome, "candidate-active")
        self.assertEqual(result.candidate_receipt_sha256, candidate_sha256)
        self.assertEqual(result.active_receipt_sha256, candidate_sha256)
        self.assertEqual(result.accepted_envelope_sha256, sha256(envelope_raw))
        self.assertEqual(smoke.calls, 1)
        self.assertFalse((prepared.canonical_root / "transaction.json").exists())
        for artifact in prepared.verified.artifacts:
            self.assertEqual(artifact.installed_path.read_bytes(), artifact.raw)

    def test_recovery_rejects_stale_expected_journal_before_mutation(self) -> None:
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        activation = self.fixture.activation_request(prepared)
        run_activation_install_process_loss_cut(
            deployment,
            activation,
            "directory-fsync",
            artifact_index=0,
        )
        transaction_path = prepared.canonical_root / "transaction.json"
        stale_raw = transaction_path.read_bytes()
        stale = deployment._parse_activation_journal(stale_raw)
        following = ordered_activation_artifacts(prepared)[1]
        successor = deployment._activation_journal_generation(
            stale.value,
            stale,
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
        state_before = tree_inventory(prepared.canonical_root)
        descriptors_before = process_descriptor_inventory()
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
        request = deployment.RecoveryRequest(activation, stale_raw)

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
                "^activation recovery journal freshness disagrees$",
            ),
        ):
            deployment.recover_transaction(request)

        self.assertEqual(transaction_path.read_bytes(), successor.raw)
        self.assertEqual(tree_inventory(prepared.canonical_root), state_before)
        self.assertEqual(process_descriptor_inventory(), descriptors_before)
        for name, boundary in forbidden.items():
            self.assertEqual(boundary.call_count, 0, name)

    def test_recovery_rejects_self_rebound_journal_authority(self) -> None:
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        activation = self.fixture.activation_request(prepared)
        run_activation_install_process_loss_cut(
            deployment,
            activation,
            "directory-fsync",
            artifact_index=0,
        )
        transaction_path = prepared.canonical_root / "transaction.json"
        rebound = copy.deepcopy(canonical_value(transaction_path.read_bytes()))
        rebound_digest = "f" * 64
        rebound["outer_maintenance_transaction_sha256"] = rebound_digest
        rebound["stage"]["maintenance_transaction_sha256"] = rebound_digest
        rebind_journal_transaction_id(rebound)
        rebound_raw = reseal_journal(rebound)
        transaction_path.write_bytes(rebound_raw)
        state_before = tree_inventory(prepared.canonical_root)
        smoke = mock.Mock(side_effect=AssertionError("smoke must not run"))

        with (
            mock.patch.object(
                deployment,
                "_spawn_activation_smoke_child",
                smoke,
            ),
            self.assertRaisesRegex(
                deployment.DeploymentError,
                "^activation recovery stage binding disagrees$",
            ),
        ):
            deployment.recover_transaction(
                deployment.RecoveryRequest(activation, rebound_raw)
            )

        self.assertEqual(smoke.call_count, 0)
        self.assertEqual(transaction_path.read_bytes(), rebound_raw)
        self.assertEqual(tree_inventory(prepared.canonical_root), state_before)

    def test_recovery_returns_terminal_result_without_rerunning_smoke(self) -> None:
        deployment = self.deployment()
        prepared = self.fixture.prepare()
        activation = self.fixture.activation_request(prepared)
        envelope_raw = expected_smoke_envelope(prepared.staged)
        run_activation_smoke_process_loss_cut(
            deployment,
            activation,
            cut="terminal-journal",
            envelope_raw=envelope_raw,
        )
        expected_raw = (prepared.canonical_root / "transaction.json").read_bytes()
        terminal = canonical_value(expected_raw)
        self.assertEqual(terminal["phase"], "terminal")
        forbidden_smoke = mock.Mock(
            side_effect=AssertionError("terminal recovery must not smoke")
        )

        with mock.patch.object(
            deployment,
            "_spawn_activation_smoke_child",
            forbidden_smoke,
        ):
            result = deployment.recover_transaction(
                deployment.RecoveryRequest(activation, expected_raw)
            )

        self.assertEqual(forbidden_smoke.call_count, 0)
        self.assertEqual(result.journal_raw, expected_raw)
        self.assertEqual(result.outcome, "candidate-active")
        self.assertFalse((prepared.canonical_root / "transaction.json").exists())


if __name__ == "__main__":
    unittest.main()
