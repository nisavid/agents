from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ._activation_support import (
    canonical_value,
    filesystem_identity,
    process_descriptor_inventory,
)
from ._control_maintenance_activation_support import (
    CONTROL_MAINTENANCE_ADDITIVE_PUBLISH_PROCESS_LOSS_EXIT,
    CONTROL_MAINTENANCE_CLEANUP_PROCESS_LOSS_EXIT,
    CONTROL_MAINTENANCE_RECOVERY_PARENT_FSYNC_EXIT,
    CONTROL_MAINTENANCE_REPLACE_PROCESS_LOSS_EXIT,
    CONTROL_MAINTENANCE_REPLACEMENT_PERSISTENCE_CUTS,
    AcceptedControlMaintenanceSmoke,
    ControlMaintenanceActivationFixture,
    ControlMaintenanceCleanupStep,
    ControlMaintenanceRecoveryPopenAdapter,
    RejectCandidateAcceptPriorControlMaintenanceSmoke,
    RejectCandidateAndPriorControlMaintenanceSmoke,
    assert_candidate_control_set_installed,
    assert_control_maintenance_additive_directory_state,
    assert_control_maintenance_additive_persistence_state,
    assert_control_maintenance_additive_receipt_inventory,
    assert_control_maintenance_additive_set_installed,
    assert_control_maintenance_cleanup_persistence_state,
    assert_control_maintenance_mixed_replacement_state,
    assert_control_maintenance_replacement_persistence_state,
    assert_control_maintenance_replacement_prefix,
    assert_no_control_maintenance_temporaries,
    assert_prior_control_set_installed,
    control_maintenance_cleanup_steps,
    expected_control_maintenance_cleanup,
    ordered_control_maintenance_additive_artifacts,
    run_control_maintenance_activation_replace_process_loss,
    run_control_maintenance_activation_replacement_persistence_cut,
    run_control_maintenance_cleanup_process_loss_cut,
    run_control_maintenance_journal_process_loss_cut,
    run_control_maintenance_recovery_additive_publish_process_loss,
    run_control_maintenance_recovery_already_target_parent_fsync_probe,
    run_control_maintenance_recovery_replace_process_loss,
    run_control_maintenance_restored_prior_terminal_process_loss,
    run_control_maintenance_smoke_process_loss_cut,
)
from ._control_maintenance_support import (
    MAINTENANCE_REPLACEMENT_ROLES,
    tree_state_with_result,
)
from ._routine_activation_support import (
    JOURNAL_KEYS,
    ROUTINE_ADDITIVE_PROCESS_LOSS_CUTS,
    ROUTINE_CLEANUP_PROCESS_LOSS_CUTS,
    ROUTINE_JOURNAL_PROCESS_LOSS_CUTS,
    assert_no_transaction_residue,
    assert_selector_has_distinct_retained_copy,
    directory_snapshot,
    exact_live_journal,
    expected_active_receipt_inventory,
    receipt_digest_inventory,
    regular_file_snapshot,
    run_routine_additive_process_loss_cut,
    selector_raws,
    staged_artifact,
    staged_candidate_selector_raws,
)
from ._support import content_document, sha256


class ControlMaintenanceActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.fixture = ControlMaintenanceActivationFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_public_activation_commits_candidate_complete_control_set(self) -> None:
        deployment = self.fixture.deployment()
        prepared = self.fixture.staged_activation()
        staged = prepared.staged
        root = prepared.initial.canonical_root
        selector_a = selector_raws(root)
        selector_b = staged_candidate_selector_raws(staged)
        expected_receipts = expected_active_receipt_inventory(
            selector_a[1],
            staged,
        )
        root_before = self.fixture.control.tree_state(root)
        stage_before = self.fixture.control.routine.stage_snapshot(staged.stage_path)
        lock_before = filesystem_identity(prepared.initial.activation_lock)
        descriptors_before = process_descriptor_inventory()
        smoke = AcceptedControlMaintenanceSmoke(prepared)

        try:
            with mock.patch.object(
                deployment,
                "_spawn_activation_smoke_child",
                smoke,
            ):
                result = deployment.activate_staged(prepared.activation)
        except deployment.DeploymentError:
            self.assertEqual(self.fixture.control.tree_state(root), root_before)
            self.assertEqual(
                self.fixture.control.routine.stage_snapshot(staged.stage_path),
                stage_before,
            )
            self.assertEqual(
                filesystem_identity(prepared.initial.activation_lock),
                lock_before,
            )
            self.assertEqual(process_descriptor_inventory(), descriptors_before)
            self.assertEqual(smoke.phases, [])
            self.assertFalse((root / "transaction.json").exists())
            raise

        candidate_receipt_sha256 = sha256(staged.deployment_raw)
        self.assertEqual(result.outcome, "candidate-active")
        self.assertEqual(result.candidate_receipt_sha256, candidate_receipt_sha256)
        self.assertEqual(result.active_receipt_sha256, candidate_receipt_sha256)
        self.assertEqual(result.accepted_envelope_sha256, sha256(smoke.output))
        self.assertEqual(frozenset(result.journal_value), JOURNAL_KEYS)
        self.assertEqual(
            result.journal_value["transaction_class"],
            "control-set-maintenance",
        )
        self.assertEqual(result.journal_value["phase"], "terminal")
        self.assertEqual(
            result.journal_value["terminal_result"]["outcome"],
            "candidate-active",
        )
        self.assertEqual(smoke.phases, ["candidate-smoke"])
        self.assertEqual(selector_raws(root), selector_b)
        self.assertNotEqual(selector_a, selector_b)
        self.assertEqual(receipt_digest_inventory(root), expected_receipts)
        assert_selector_has_distinct_retained_copy(root, staged.deployment_raw)
        assert_candidate_control_set_installed(staged)
        assert_no_transaction_residue(root)
        self.assertEqual(
            self.fixture.control.routine.stage_snapshot(staged.stage_path),
            stage_before,
        )
        self.fixture.control.assert_private_stage(self, staged.stage_path.parent)
        self.assertEqual(
            filesystem_identity(prepared.initial.activation_lock),
            lock_before,
        )
        self.assertEqual(process_descriptor_inventory(), descriptors_before)
        self.assertEqual(
            staged_artifact(staged, "deployment-alias").raw,
            staged.deployment_raw,
        )

    def test_public_recovery_replays_each_candidate_control_replacement(
        self,
    ) -> None:
        self._assert_public_recovery_replays_each_control_replacement("candidate")

    def test_public_recovery_replays_each_prior_control_replacement(self) -> None:
        self._assert_public_recovery_replays_each_control_replacement("prior")

    def test_public_recovery_reconciles_candidate_control_replacement_persistence(
        self,
    ) -> None:
        self._assert_public_recovery_reconciles_control_replacement_persistence(
            "candidate"
        )

    def test_public_recovery_reconciles_prior_control_replacement_persistence(
        self,
    ) -> None:
        self._assert_public_recovery_reconciles_control_replacement_persistence(
            "prior"
        )

    def test_public_recovery_reconciles_control_additive_persistence(self) -> None:
        probe = self.fixture.staged_activation(distinct_replacement_bytes=True)
        probe_artifacts = ordered_control_maintenance_additive_artifacts(
            probe.staged
        )
        last_index = len(probe_artifacts) - 1
        self.assertEqual(
            tuple(probe_artifacts[index].role for index in (0, 1, last_index)),
            (
                "rollback-receipt",
                "runtime-bundle-io",
                "deployment-receipt",
            ),
        )
        self.assertFalse(Path(probe_artifacts[1].installed_path).parent.exists())
        cases = tuple(
            (artifact_index, cut)
            for artifact_index in (0, 1, last_index)
            for cut in ROUTINE_ADDITIVE_PROCESS_LOSS_CUTS
        )
        pre_publication_cuts = {"temp-create", "partial-write", "file-fsync"}
        for artifact_index, cut in cases:
            with self.subTest(  # noqa: SIM117
                artifact_index=artifact_index,
                cut=cut,
            ):
                with tempfile.TemporaryDirectory() as directory:
                    fixture = ControlMaintenanceActivationFixture(
                        Path(directory).resolve()
                    )
                    deployment = fixture.deployment()
                    prepared = fixture.staged_activation(
                        distinct_replacement_bytes=True
                    )
                    staged = prepared.staged
                    root = prepared.initial.canonical_root
                    artifacts = ordered_control_maintenance_additive_artifacts(
                        staged
                    )
                    self.assertEqual(len(artifacts), len(probe_artifacts))
                    self.assertEqual(
                        artifacts[artifact_index].role,
                        probe_artifacts[artifact_index].role,
                    )
                    if artifact_index == 1:
                        self.assertFalse(
                            Path(artifacts[artifact_index].installed_path)
                            .parent.exists()
                        )
                    selector_a = selector_raws(root)
                    prior_receipts = receipt_digest_inventory(root)
                    baseline_files = regular_file_snapshot(root)
                    baseline_directories = directory_snapshot(root)
                    stage_before = fixture.control.routine.stage_snapshot(
                        staged.stage_path
                    )
                    root_identity = filesystem_identity(root)[:4]
                    lock_before = filesystem_identity(
                        prepared.initial.activation_lock
                    )
                    descriptors_before = process_descriptor_inventory()

                    run_routine_additive_process_loss_cut(
                        deployment,
                        prepared.activation,
                        artifact_index=artifact_index,
                        cut=cut,
                    )

                    journal_raw = (
                        assert_control_maintenance_additive_persistence_state(
                            prepared,
                            baseline_files,
                            artifact_index=artifact_index,
                            cut=cut,
                        )
                    )
                    self.assertEqual(exact_live_journal(root), journal_raw)
                    self.assertEqual(selector_raws(root), selector_a)
                    assert_prior_control_set_installed(staged)
                    assert_control_maintenance_additive_receipt_inventory(
                        prepared,
                        prior_receipts,
                        artifact_index=artifact_index,
                        cut=cut,
                    )
                    assert_control_maintenance_additive_directory_state(
                        prepared,
                        baseline_directories,
                        through_index=artifact_index,
                    )
                    self.assertEqual(filesystem_identity(root)[:4], root_identity)
                    self.assertEqual(
                        fixture.control.routine.stage_snapshot(staged.stage_path),
                        stage_before,
                    )
                    fixture.control.assert_private_stage(
                        self,
                        staged.stage_path.parent,
                    )
                    self.assertEqual(
                        filesystem_identity(prepared.initial.activation_lock),
                        lock_before,
                    )
                    self.assertEqual(
                        process_descriptor_inventory(),
                        descriptors_before,
                    )
                    recovery_index = (
                        artifact_index
                        if cut in pre_publication_cuts
                        else (
                            artifact_index + 1
                            if artifact_index + 1 < len(artifacts)
                            else None
                        )
                    )

                    recovery_exit = (
                        run_control_maintenance_recovery_additive_publish_process_loss(
                            deployment,
                            prepared,
                            journal_raw,
                            artifact_index=recovery_index,
                        )
                    )

                    expected_exit = (
                        CONTROL_MAINTENANCE_ADDITIVE_PUBLISH_PROCESS_LOSS_EXIT
                        if recovery_index is not None
                        else CONTROL_MAINTENANCE_REPLACE_PROCESS_LOSS_EXIT
                    )
                    self.assertEqual(
                        recovery_exit.exit_status,
                        expected_exit,
                        recovery_exit.diagnostic,
                    )
                    self.assertEqual(recovery_exit.diagnostic, "")
                    if recovery_index is None:
                        advanced_journal_raw = (
                            assert_control_maintenance_mixed_replacement_state(
                                prepared,
                                direction="candidate",
                                replacement_index=0,
                            )
                        )
                        assert_control_maintenance_additive_set_installed(staged)
                        self.assertEqual(
                            receipt_digest_inventory(root),
                            expected_active_receipt_inventory(
                                selector_a[1],
                                staged,
                            ),
                        )
                        directory_index = len(artifacts) - 1
                    else:
                        advanced_journal_raw = (
                            assert_control_maintenance_additive_persistence_state(
                                prepared,
                                baseline_files,
                                artifact_index=recovery_index,
                                cut="publish",
                            )
                        )
                        assert_prior_control_set_installed(staged)
                        assert_control_maintenance_additive_receipt_inventory(
                            prepared,
                            prior_receipts,
                            artifact_index=recovery_index,
                            cut="publish",
                        )
                        directory_index = recovery_index
                    self.assertEqual(
                        exact_live_journal(root),
                        advanced_journal_raw,
                    )
                    self.assertEqual(selector_raws(root), selector_a)
                    assert_control_maintenance_additive_directory_state(
                        prepared,
                        baseline_directories,
                        through_index=directory_index,
                    )
                    self.assertEqual(filesystem_identity(root)[:4], root_identity)
                    self.assertEqual(
                        fixture.control.routine.stage_snapshot(staged.stage_path),
                        stage_before,
                    )
                    fixture.control.assert_private_stage(
                        self,
                        staged.stage_path.parent,
                    )
                    self.assertEqual(
                        filesystem_identity(prepared.initial.activation_lock),
                        lock_before,
                    )
                    self.assertEqual(
                        process_descriptor_inventory(),
                        descriptors_before,
                    )

    def test_public_recovery_finishes_control_rollback_cleanup_persistence(
        self,
    ) -> None:
        probe = self.fixture.staged_activation(distinct_replacement_bytes=True)
        probe_root = probe.initial.canonical_root
        probe_cleanup = control_maintenance_cleanup_steps(
            probe.staged,
            regular_file_snapshot(probe_root),
            directory_snapshot(probe_root),
        )
        directory_depth = max(
            step.relative_path.count("/")
            for step in probe_cleanup
            if step.operation == "remove-directory"
        )
        deepest_directory = next(
            step
            for step in probe_cleanup
            if step.operation == "remove-directory"
            and step.relative_path.count("/") == directory_depth
        )
        selected_indices = (0, deepest_directory.index, len(probe_cleanup) - 1)
        self.assertEqual(probe_cleanup[0].role, "rollback-receipt")
        self.assertEqual(probe_cleanup[0].operation, "remove-artifact")
        self.assertEqual(deepest_directory.operation, "remove-directory")
        self.assertEqual(probe_cleanup[-1].role, "deployment-receipt")
        self.assertEqual(probe_cleanup[-1].operation, "remove-artifact")
        cases = tuple(
            (step_index, cut)
            for step_index in selected_indices
            for cut in ROUTINE_CLEANUP_PROCESS_LOSS_CUTS
        )
        for step_index, cut in cases:
            with self.subTest(step_index=step_index, cut=cut):  # noqa: SIM117
                with tempfile.TemporaryDirectory() as directory:
                    fixture = ControlMaintenanceActivationFixture(
                        Path(directory).resolve()
                    )
                    deployment = fixture.deployment()
                    prepared = fixture.staged_activation(
                        distinct_replacement_bytes=True
                    )
                    staged = prepared.staged
                    root = prepared.initial.canonical_root
                    prior_files = regular_file_snapshot(root)
                    prior_directories = directory_snapshot(root)
                    cleanup = control_maintenance_cleanup_steps(
                        staged,
                        prior_files,
                        prior_directories,
                    )
                    target = cleanup[step_index]
                    expected_target = probe_cleanup[step_index]
                    self.assertEqual(target.operation, expected_target.operation)
                    if target.operation == "remove-artifact":
                        self.assertEqual(target.role, expected_target.role)
                    else:
                        self.assertEqual(
                            target.relative_path.count("/"),
                            directory_depth,
                        )
                    selector_a = selector_raws(root)
                    prior_receipts = receipt_digest_inventory(root)
                    root_before = fixture.control.tree_state(root)
                    stage_before = fixture.control.routine.stage_snapshot(
                        staged.stage_path
                    )
                    root_identity = filesystem_identity(root)[:4]
                    lock_before = filesystem_identity(
                        prepared.initial.activation_lock
                    )
                    descriptors_before = process_descriptor_inventory()

                    activation_exit = run_control_maintenance_cleanup_process_loss_cut(
                        deployment,
                        prepared,
                        cleanup_steps=cleanup,
                        step_index=step_index,
                        cut=cut,
                    )

                    self.assertEqual(
                        activation_exit.exit_status,
                        CONTROL_MAINTENANCE_CLEANUP_PROCESS_LOSS_EXIT,
                        activation_exit.diagnostic,
                    )
                    self.assertEqual(activation_exit.diagnostic, "")
                    journal_raw = (
                        assert_control_maintenance_cleanup_persistence_state(
                            prepared,
                            cleanup,
                            step_index=step_index,
                            cut=cut,
                        )
                    )
                    journal = exact_live_journal(root)
                    self.assertEqual(journal, journal_raw)
                    rollback_smoke = staged.rollback_value[
                        "prior_activation_unit"
                    ]["smoke"]
                    acceptance_unsigned = {
                        "phase": "rollback-smoke",
                        "target_deployment_receipt_sha256": prepared.active.active_receipt_sha256,
                        "expected_envelope_sha256": rollback_smoke[
                            "expected_envelope_sha256"
                        ],
                        "accepted_envelope_sha256": rollback_smoke[
                            "expected_envelope_sha256"
                        ],
                        "exit_status": 0,
                    }
                    live_journal = canonical_value(journal_raw)
                    self.assertEqual(
                        live_journal["rollback_smoke_acceptance"],
                        content_document(acceptance_unsigned),
                    )
                    self.assertEqual(selector_raws(root), selector_a)
                    assert_prior_control_set_installed(staged)
                    crash_receipts = set(prior_receipts)
                    for step in cleanup:
                        if (
                            step.artifact is None
                            or step.role
                            not in {"rollback-receipt", "deployment-receipt"}
                        ):
                            continue
                        present = step.index > step_index or (
                            step.index == step_index and cut == "before-unlink"
                        )
                        if present:
                            crash_receipts.add(sha256(step.artifact.raw))
                    self.assertEqual(
                        receipt_digest_inventory(root),
                        frozenset(crash_receipts),
                    )
                    assert_no_control_maintenance_temporaries(root)
                    self.assertEqual(filesystem_identity(root)[:4], root_identity)
                    self.assertEqual(
                        fixture.control.routine.stage_snapshot(staged.stage_path),
                        stage_before,
                    )
                    fixture.control.assert_private_stage(
                        self,
                        staged.stage_path.parent,
                    )
                    self.assertEqual(
                        filesystem_identity(prepared.initial.activation_lock),
                        lock_before,
                    )
                    self.assertEqual(
                        process_descriptor_inventory(),
                        descriptors_before,
                    )
                    smoke_processes: list[
                        tuple[tuple[object, ...], dict[str, object]]
                    ] = []

                    def unexpected_smoke_process(
                        *args,
                        _processes=smoke_processes,
                        **kwargs,
                    ):
                        _processes.append((args, kwargs))
                        raise AssertionError(
                            "control maintenance recovery reran accepted smoke"
                        )

                    with mock.patch.object(
                        deployment.subprocess,
                        "Popen",
                        side_effect=unexpected_smoke_process,
                    ):
                        result = deployment.recover_transaction(
                            deployment.RecoveryRequest(
                                activation=prepared.activation,
                                expected_journal_raw=journal_raw,
                            )
                        )

                    self.assertEqual(smoke_processes, [])
                    self.assertEqual(result.outcome, "restored-prior")
                    self.assertEqual(
                        result.candidate_receipt_sha256,
                        sha256(staged.deployment_raw),
                    )
                    self.assertEqual(
                        result.active_receipt_sha256,
                        prepared.active.active_receipt_sha256,
                    )
                    self.assertEqual(
                        result.accepted_envelope_sha256,
                        rollback_smoke["expected_envelope_sha256"],
                    )
                    self.assertEqual(selector_raws(root), selector_a)
                    assert_prior_control_set_installed(staged)
                    self.assertEqual(receipt_digest_inventory(root), prior_receipts)
                    assert_selector_has_distinct_retained_copy(root, selector_a[1])
                    for step in cleanup:
                        self.assertFalse((root / step.relative_path).exists())
                    assert_no_transaction_residue(root)
                    self.assertEqual(
                        fixture.control.tree_state(root),
                        tree_state_with_result(root_before, result),
                    )
                    self.assertEqual(filesystem_identity(root)[:4], root_identity)
                    self.assertEqual(
                        fixture.control.routine.stage_snapshot(staged.stage_path),
                        stage_before,
                    )
                    fixture.control.assert_private_stage(
                        self,
                        staged.stage_path.parent,
                    )
                    self.assertEqual(
                        filesystem_identity(prepared.initial.activation_lock),
                        lock_before,
                    )
                    self.assertEqual(
                        process_descriptor_inventory(),
                        descriptors_before,
                    )

    def test_public_recovery_reconciles_control_journal_persistence(self) -> None:
        generations = (
            "mixed-candidate-control",
            "candidate-active-terminal",
        )
        cases = tuple(
            (generation, cut)
            for generation in generations
            for cut in ROUTINE_JOURNAL_PROCESS_LOSS_CUTS
        )
        prepublication_cuts = {"temp-create", "partial-write", "full-write"}
        for generation, cut in cases:
            with self.subTest(generation=generation, cut=cut):  # noqa: SIM117
                with tempfile.TemporaryDirectory() as directory:
                    fixture = ControlMaintenanceActivationFixture(
                        Path(directory).resolve()
                    )
                    deployment = fixture.deployment()
                    prepared = fixture.staged_activation(
                        distinct_replacement_bytes=True
                    )
                    staged = prepared.staged
                    root = prepared.initial.canonical_root
                    selector_a = selector_raws(root)
                    selector_b = staged_candidate_selector_raws(staged)
                    expected_receipts = expected_active_receipt_inventory(
                        selector_a[1],
                        staged,
                    )
                    stage_before = fixture.control.routine.stage_snapshot(
                        staged.stage_path
                    )
                    root_identity = filesystem_identity(root)[:4]
                    lock_before = filesystem_identity(
                        prepared.initial.activation_lock
                    )
                    descriptors_before = process_descriptor_inventory()

                    journal_cut = run_control_maintenance_journal_process_loss_cut(
                        deployment,
                        prepared,
                        generation=generation,
                        cut=cut,
                    )

                    current = canonical_value(journal_cut.current_raw)
                    target = canonical_value(journal_cut.target_raw)
                    self.assertEqual(
                        target["transaction_id"],
                        current["transaction_id"],
                    )
                    self.assertEqual(target["sequence"], current["sequence"] + 1)
                    self.assertEqual(
                        target["previous_journal_sha256"],
                        sha256(journal_cut.current_raw),
                    )
                    temporary = root / (
                        f"transaction.{target['transaction_id']}."
                        f"{target['sequence']}.tmp"
                    )
                    live_journal_raw = exact_live_journal(root)
                    journal_temporaries = tuple(root.glob("transaction.*.tmp"))
                    if cut in prepublication_cuts:
                        self.assertEqual(
                            live_journal_raw,
                            journal_cut.current_raw,
                        )
                        self.assertEqual(journal_temporaries, (temporary,))
                        metadata = temporary.lstat()
                        self.assertTrue(stat.S_ISREG(metadata.st_mode))
                        self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
                        self.assertEqual(metadata.st_uid, os.geteuid())
                        self.assertEqual(metadata.st_nlink, 1)
                        temporary_raw = temporary.read_bytes()
                        if cut == "temp-create":
                            self.assertEqual(temporary_raw, b"")
                        elif cut == "partial-write":
                            self.assertTrue(
                                journal_cut.target_raw.startswith(temporary_raw)
                            )
                            self.assertGreater(len(temporary_raw), 0)
                            self.assertLess(
                                len(temporary_raw),
                                len(journal_cut.target_raw),
                            )
                        else:
                            self.assertEqual(
                                temporary_raw,
                                journal_cut.target_raw,
                            )
                    else:
                        self.assertEqual(journal_temporaries, ())
                        self.assertEqual(
                            live_journal_raw,
                            journal_cut.target_raw,
                        )
                    self.assertEqual(
                        tuple(root.rglob(".task-witness-*.tmp")),
                        (),
                    )
                    if generation == "mixed-candidate-control":
                        self.assertEqual(current["phase"], "control-switching")
                        self.assertEqual(
                            current["pending_step"],
                            {
                                "operation": "replace-control",
                                "index": 0,
                                "role": MAINTENANCE_REPLACEMENT_ROLES[0],
                            },
                        )
                        self.assertEqual(target["phase"], "control-switching")
                        self.assertEqual(
                            target["pending_step"],
                            {
                                "operation": "replace-control",
                                "index": 1,
                                "role": MAINTENANCE_REPLACEMENT_ROLES[1],
                            },
                        )
                        self.assertEqual(selector_raws(root), selector_a)
                        assert_control_maintenance_replacement_prefix(
                            staged,
                            completed_index=0,
                        )
                    else:
                        self.assertEqual(current["phase"], "candidate-accepted")
                        self.assertIsNone(current["pending_step"])
                        acceptance = current["candidate_smoke_acceptance"]
                        self.assertIsNotNone(acceptance)
                        candidate_receipt_sha256 = sha256(staged.deployment_raw)
                        self.assertEqual(target["phase"], "terminal")
                        self.assertEqual(target["pending_step"], None)
                        self.assertEqual(
                            target["terminal_result"],
                            {
                                "outcome": "candidate-active",
                                "candidate_receipt_sha256": (
                                    candidate_receipt_sha256
                                ),
                                "active_receipt_sha256": candidate_receipt_sha256,
                                "accepted_envelope_sha256": acceptance[
                                    "accepted_envelope_sha256"
                                ],
                                "failure_class": None,
                            },
                        )
                        self.assertEqual(selector_raws(root), selector_b)
                        assert_candidate_control_set_installed(staged)
                    assert_control_maintenance_additive_set_installed(staged)
                    self.assertEqual(
                        receipt_digest_inventory(root),
                        expected_receipts,
                    )
                    self.assertEqual(filesystem_identity(root)[:4], root_identity)
                    self.assertEqual(
                        fixture.control.routine.stage_snapshot(staged.stage_path),
                        stage_before,
                    )
                    fixture.control.assert_private_stage(
                        self,
                        staged.stage_path.parent,
                    )
                    self.assertEqual(
                        filesystem_identity(prepared.initial.activation_lock),
                        lock_before,
                    )
                    self.assertEqual(
                        process_descriptor_inventory(),
                        descriptors_before,
                    )

                    if generation == "mixed-candidate-control":
                        recovery_exit = (
                            run_control_maintenance_recovery_replace_process_loss(
                                deployment,
                                prepared,
                                live_journal_raw,
                                direction="candidate",
                                replacement_index=1,
                            )
                        )
                        self.assertEqual(
                            recovery_exit.exit_status,
                            CONTROL_MAINTENANCE_REPLACE_PROCESS_LOSS_EXIT,
                            recovery_exit.diagnostic,
                        )
                        self.assertEqual(recovery_exit.diagnostic, "")
                        recovered_journal_raw = (
                            assert_control_maintenance_mixed_replacement_state(
                                prepared,
                                direction="candidate",
                                replacement_index=1,
                            )
                        )
                        self.assertEqual(
                            recovered_journal_raw,
                            journal_cut.target_raw,
                        )
                        self.assertEqual(selector_raws(root), selector_a)
                    else:
                        smoke_processes: list[
                            tuple[tuple[object, ...], dict[str, object]]
                        ] = []

                        def unexpected_smoke_process(
                            *args,
                            _processes=smoke_processes,
                            **kwargs,
                        ):
                            _processes.append((args, kwargs))
                            raise AssertionError(
                                "control terminal recovery reran accepted smoke"
                            )

                        with mock.patch.object(
                            deployment.subprocess,
                            "Popen",
                            side_effect=unexpected_smoke_process,
                        ):
                            result = deployment.recover_transaction(
                                deployment.RecoveryRequest(
                                    activation=prepared.activation,
                                    expected_journal_raw=live_journal_raw,
                                )
                            )
                        self.assertEqual(smoke_processes, [])
                        self.assertEqual(result.outcome, "candidate-active")
                        self.assertEqual(result.journal_raw, journal_cut.target_raw)
                        self.assertEqual(selector_raws(root), selector_b)
                        assert_candidate_control_set_installed(staged)
                        assert_selector_has_distinct_retained_copy(
                            root,
                            staged.deployment_raw,
                        )
                        assert_no_transaction_residue(root)
                    assert_control_maintenance_additive_set_installed(staged)
                    self.assertEqual(
                        receipt_digest_inventory(root),
                        expected_receipts,
                    )
                    self.assertEqual(filesystem_identity(root)[:4], root_identity)
                    self.assertEqual(
                        fixture.control.routine.stage_snapshot(staged.stage_path),
                        stage_before,
                    )
                    fixture.control.assert_private_stage(
                        self,
                        staged.stage_path.parent,
                    )
                    self.assertEqual(
                        filesystem_identity(prepared.initial.activation_lock),
                        lock_before,
                    )
                    self.assertEqual(
                        process_descriptor_inventory(),
                        descriptors_before,
                    )

    def test_public_recovery_respects_control_smoke_durability(self) -> None:
        cases = tuple(
            (phase, cut)
            for phase in ("candidate-smoke", "rollback-smoke")
            for cut in ("child-return", "accepted-journal")
        )
        for phase, cut in cases:
            with self.subTest(phase=phase, cut=cut):  # noqa: SIM117
                with tempfile.TemporaryDirectory() as directory:
                    fixture = ControlMaintenanceActivationFixture(
                        Path(directory).resolve()
                    )
                    deployment = fixture.deployment()
                    prepared = fixture.staged_activation(
                        distinct_replacement_bytes=True
                    )
                    staged = prepared.staged
                    root = prepared.initial.canonical_root
                    selector_a = selector_raws(root)
                    selector_b = staged_candidate_selector_raws(staged)
                    prior_receipts = receipt_digest_inventory(root)
                    expected_receipts = expected_active_receipt_inventory(
                        selector_a[1],
                        staged,
                    )
                    cleanup = expected_control_maintenance_cleanup(staged, root)
                    root_before = fixture.control.tree_state(root)
                    stage_before = fixture.control.routine.stage_snapshot(
                        staged.stage_path
                    )
                    root_identity = filesystem_identity(root)[:4]
                    lock_before = filesystem_identity(
                        prepared.initial.activation_lock
                    )
                    descriptors_before = process_descriptor_inventory()

                    initial_markers = (
                        run_control_maintenance_smoke_process_loss_cut(
                            deployment,
                            prepared,
                            phase=phase,
                            cut=cut,
                        )
                    )

                    expected_initial_markers = (
                        ("B",)
                        if phase == "candidate-smoke"
                        else ("B", "A")
                    )
                    self.assertEqual(initial_markers, expected_initial_markers)
                    journal_raw = exact_live_journal(root)
                    journal = canonical_value(journal_raw)
                    self.assertEqual(journal["phase"], phase)
                    acceptance = (
                        journal["candidate_smoke_acceptance"]
                        if phase == "candidate-smoke"
                        else journal["rollback_smoke_acceptance"]
                    )
                    if cut == "child-return":
                        self.assertIsNone(acceptance)
                    else:
                        self.assertIsNotNone(acceptance)
                    if phase == "candidate-smoke":
                        self.assertEqual(selector_raws(root), selector_b)
                        assert_candidate_control_set_installed(staged)
                    else:
                        self.assertEqual(selector_raws(root), selector_a)
                        assert_prior_control_set_installed(staged)
                    assert_control_maintenance_additive_set_installed(staged)
                    self.assertEqual(
                        receipt_digest_inventory(root),
                        expected_receipts,
                    )
                    assert_no_control_maintenance_temporaries(root)
                    self.assertEqual(filesystem_identity(root)[:4], root_identity)
                    self.assertEqual(
                        fixture.control.routine.stage_snapshot(staged.stage_path),
                        stage_before,
                    )
                    fixture.control.assert_private_stage(
                        self,
                        staged.stage_path.parent,
                    )
                    self.assertEqual(
                        filesystem_identity(prepared.initial.activation_lock),
                        lock_before,
                    )
                    self.assertEqual(
                        process_descriptor_inventory(),
                        descriptors_before,
                    )
                    adapter = ControlMaintenanceRecoveryPopenAdapter(
                        prepared,
                        deployment.subprocess.Popen,
                        candidate_accepted=phase == "candidate-smoke",
                    )

                    with mock.patch.object(
                        deployment.subprocess,
                        "Popen",
                        side_effect=adapter,
                    ):
                        result = deployment.recover_transaction(
                            deployment.RecoveryRequest(
                                activation=prepared.activation,
                                expected_journal_raw=journal_raw,
                            )
                        )

                    expected_recovery_markers = (
                        (("B",) if phase == "candidate-smoke" else ("A",))
                        if cut == "child-return"
                        else ()
                    )
                    self.assertEqual(
                        tuple(adapter.markers),
                        expected_recovery_markers,
                    )
                    self.assertEqual(
                        (*initial_markers, *adapter.markers),
                        (*expected_initial_markers, *expected_recovery_markers),
                    )
                    candidate_receipt_sha256 = sha256(staged.deployment_raw)
                    self.assertEqual(
                        result.candidate_receipt_sha256,
                        candidate_receipt_sha256,
                    )
                    if phase == "candidate-smoke":
                        self.assertEqual(result.outcome, "candidate-active")
                        self.assertEqual(
                            result.active_receipt_sha256,
                            candidate_receipt_sha256,
                        )
                        self.assertEqual(
                            result.accepted_envelope_sha256,
                            staged.deployment_value["smoke"][
                                "expected_envelope_sha256"
                            ],
                        )
                        self.assertEqual(selector_raws(root), selector_b)
                        assert_candidate_control_set_installed(staged)
                        assert_control_maintenance_additive_set_installed(staged)
                        self.assertEqual(
                            receipt_digest_inventory(root),
                            expected_receipts,
                        )
                        assert_selector_has_distinct_retained_copy(
                            root,
                            staged.deployment_raw,
                        )
                    else:
                        self.assertEqual(result.outcome, "restored-prior")
                        self.assertEqual(
                            result.active_receipt_sha256,
                            prepared.active.active_receipt_sha256,
                        )
                        self.assertEqual(
                            result.accepted_envelope_sha256,
                            staged.rollback_value["prior_activation_unit"][
                                "smoke"
                            ]["expected_envelope_sha256"],
                        )
                        self.assertEqual(selector_raws(root), selector_a)
                        assert_prior_control_set_installed(staged)
                        self.assertEqual(
                            receipt_digest_inventory(root),
                            prior_receipts,
                        )
                        for step in cleanup:
                            self.assertFalse((root / step.relative_path).exists())
                        self.assertEqual(
                            fixture.control.tree_state(root),
                            tree_state_with_result(root_before, result),
                        )
                    assert_no_transaction_residue(root)
                    self.assertEqual(filesystem_identity(root)[:4], root_identity)
                    self.assertEqual(
                        fixture.control.routine.stage_snapshot(staged.stage_path),
                        stage_before,
                    )
                    fixture.control.assert_private_stage(
                        self,
                        staged.stage_path.parent,
                    )
                    self.assertEqual(
                        filesystem_identity(prepared.initial.activation_lock),
                        lock_before,
                    )
                    self.assertEqual(
                        process_descriptor_inventory(),
                        descriptors_before,
                    )

    def test_public_recovery_replays_restored_prior_terminal_before_unlink(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        prepared = self.fixture.staged_activation(
            distinct_replacement_bytes=True
        )
        staged = prepared.staged
        root = prepared.initial.canonical_root
        selector_a = selector_raws(root)
        prior_receipts = receipt_digest_inventory(root)
        cleanup = expected_control_maintenance_cleanup(staged, root)
        root_before = self.fixture.control.tree_state(root)
        stage_before = self.fixture.control.routine.stage_snapshot(
            staged.stage_path
        )
        root_identity = filesystem_identity(root)[:4]
        lock_before = filesystem_identity(prepared.initial.activation_lock)
        descriptors_before = process_descriptor_inventory()

        markers = run_control_maintenance_restored_prior_terminal_process_loss(
            deployment,
            prepared,
        )

        self.assertEqual(markers, ("B", "A"))
        journal_raw = exact_live_journal(root)
        journal = canonical_value(journal_raw)
        candidate_receipt_sha256 = sha256(staged.deployment_raw)
        rollback_smoke = staged.rollback_value["prior_activation_unit"]["smoke"]
        rollback_acceptance = content_document(
            {
                "phase": "rollback-smoke",
                "target_deployment_receipt_sha256": (
                    prepared.active.active_receipt_sha256
                ),
                "expected_envelope_sha256": rollback_smoke[
                    "expected_envelope_sha256"
                ],
                "accepted_envelope_sha256": rollback_smoke[
                    "expected_envelope_sha256"
                ],
                "exit_status": 0,
            }
        )
        self.assertEqual(frozenset(journal), JOURNAL_KEYS)
        self.assertEqual(journal["transaction_class"], "control-set-maintenance")
        self.assertEqual(journal["phase"], "terminal")
        self.assertIsNone(journal["pending_step"])
        self.assertIsNone(journal["candidate_smoke_acceptance"])
        self.assertEqual(journal["rollback_smoke_acceptance"], rollback_acceptance)
        self.assertEqual(
            journal["terminal_result"],
            {
                "outcome": "restored-prior",
                "candidate_receipt_sha256": candidate_receipt_sha256,
                "active_receipt_sha256": prepared.active.active_receipt_sha256,
                "accepted_envelope_sha256": rollback_smoke[
                    "expected_envelope_sha256"
                ],
                "failure_class": "candidate-smoke-rejected",
            },
        )
        self.assertEqual(selector_raws(root), selector_a)
        assert_prior_control_set_installed(staged)
        self.assertEqual(receipt_digest_inventory(root), prior_receipts)
        assert_selector_has_distinct_retained_copy(root, selector_a[1])
        for step in cleanup:
            self.assertFalse((root / step.relative_path).exists())
        assert_no_control_maintenance_temporaries(root)
        self.assertEqual(filesystem_identity(root)[:4], root_identity)
        self.assertEqual(
            self.fixture.control.routine.stage_snapshot(staged.stage_path),
            stage_before,
        )
        self.fixture.control.assert_private_stage(self, staged.stage_path.parent)
        self.assertEqual(
            filesystem_identity(prepared.initial.activation_lock),
            lock_before,
        )
        self.assertEqual(process_descriptor_inventory(), descriptors_before)

        adapter = ControlMaintenanceRecoveryPopenAdapter(
            prepared,
            deployment.subprocess.Popen,
            candidate_accepted=False,
        )
        original_unlink = deployment.os.unlink
        original_fsync = deployment.os.fsync
        original_fstat = deployment.os.fstat
        root_metadata = root.lstat()
        journal_unlinked = False
        root_synced_after_unlink = False

        def observed_unlink(*args, **kwargs):
            nonlocal journal_unlinked
            name = args[0] if args else kwargs.get("path")
            parent_fd = kwargs.get("dir_fd")
            matches = name == "transaction.json" and isinstance(parent_fd, int)
            if matches:
                parent = original_fstat(parent_fd)
                matches = (parent.st_dev, parent.st_ino) == (
                    root_metadata.st_dev,
                    root_metadata.st_ino,
                )
            result = original_unlink(*args, **kwargs)
            if matches:
                journal_unlinked = True
            return result

        def observed_fsync(descriptor: int) -> None:
            nonlocal root_synced_after_unlink
            original_fsync(descriptor)
            if journal_unlinked:
                synchronized = original_fstat(descriptor)
                if (synchronized.st_dev, synchronized.st_ino) == (
                    root_metadata.st_dev,
                    root_metadata.st_ino,
                ):
                    root_synced_after_unlink = True

        with (
            mock.patch.object(
                deployment.subprocess,
                "Popen",
                side_effect=adapter,
            ),
            mock.patch.object(deployment.os, "unlink", side_effect=observed_unlink),
            mock.patch.object(deployment.os, "fsync", side_effect=observed_fsync),
        ):
            result = deployment.recover_transaction(
                deployment.RecoveryRequest(
                    activation=prepared.activation,
                    expected_journal_raw=journal_raw,
                )
            )

        self.assertEqual(adapter.markers, [])
        self.assertTrue(journal_unlinked)
        self.assertTrue(root_synced_after_unlink)
        self.assertEqual(result.transaction_id, journal["transaction_id"])
        self.assertEqual(result.outcome, "restored-prior")
        self.assertEqual(
            result.candidate_receipt_sha256,
            candidate_receipt_sha256,
        )
        self.assertEqual(
            result.active_receipt_sha256,
            prepared.active.active_receipt_sha256,
        )
        self.assertEqual(
            result.accepted_envelope_sha256,
            rollback_smoke["expected_envelope_sha256"],
        )
        self.assertEqual(result.journal_sha256, sha256(journal_raw))
        self.assertEqual(result.journal_raw, journal_raw)
        self.assertEqual(selector_raws(root), selector_a)
        assert_prior_control_set_installed(staged)
        self.assertEqual(receipt_digest_inventory(root), prior_receipts)
        assert_selector_has_distinct_retained_copy(root, selector_a[1])
        for step in cleanup:
            self.assertFalse((root / step.relative_path).exists())
        assert_no_transaction_residue(root)
        self.assertEqual(
            self.fixture.control.tree_state(root),
            tree_state_with_result(root_before, result),
        )
        self.assertEqual(filesystem_identity(root)[:4], root_identity)
        self.assertEqual(
            self.fixture.control.routine.stage_snapshot(staged.stage_path),
            stage_before,
        )
        self.fixture.control.assert_private_stage(self, staged.stage_path.parent)
        self.assertEqual(
            filesystem_identity(prepared.initial.activation_lock),
            lock_before,
        )
        self.assertEqual(process_descriptor_inventory(), descriptors_before)

    def _assert_public_recovery_reconciles_control_replacement_persistence(
        self,
        direction: str,
    ) -> None:
        pre_replace_cuts = {
            "temp-create",
            "partial-write",
            "content-fsync",
            "ready-fsync",
        }
        cases = (
            ((0, "already-target-needs-parent-fsync"),)
            if direction == "candidate"
            else ()
        ) + tuple(
            (replacement_index, cut)
            for replacement_index in (0, 5)
            for cut in CONTROL_MAINTENANCE_REPLACEMENT_PERSISTENCE_CUTS
        )
        for replacement_index, cut in cases:
            with self.subTest(  # noqa: SIM117
                direction=direction,
                replacement_index=replacement_index,
                cut=cut,
            ):
                with tempfile.TemporaryDirectory() as directory:
                    fixture = ControlMaintenanceActivationFixture(
                        Path(directory).resolve()
                    )
                    deployment = fixture.deployment()
                    prepared = fixture.staged_activation(
                        distinct_replacement_bytes=True
                    )
                    staged = prepared.staged
                    root = prepared.initial.canonical_root
                    selector_a = selector_raws(root)
                    expected_receipts = expected_active_receipt_inventory(
                        selector_a[1],
                        staged,
                    )
                    stage_before = fixture.control.routine.stage_snapshot(
                        staged.stage_path
                    )
                    root_identity = filesystem_identity(root)[:4]
                    lock_before = filesystem_identity(
                        prepared.initial.activation_lock
                    )
                    descriptors_before = process_descriptor_inventory()
                    activation_cut = (
                        "replace"
                        if cut == "already-target-needs-parent-fsync"
                        else cut
                    )

                    activation_exit = (
                        run_control_maintenance_activation_replacement_persistence_cut(
                            deployment,
                            prepared,
                            direction=direction,
                            replacement_index=replacement_index,
                            cut=activation_cut,
                        )
                    )

                    self.assertEqual(
                        activation_exit.exit_status,
                        CONTROL_MAINTENANCE_REPLACE_PROCESS_LOSS_EXIT,
                        activation_exit.diagnostic,
                    )
                    self.assertEqual(activation_exit.diagnostic, "")
                    journal_raw = (
                        assert_control_maintenance_replacement_persistence_state(
                            prepared,
                            direction=direction,
                            replacement_index=replacement_index,
                            cut=activation_cut,
                        )
                    )
                    self.assertEqual(exact_live_journal(root), journal_raw)
                    assert_control_maintenance_additive_set_installed(staged)
                    self.assertEqual(
                        receipt_digest_inventory(root),
                        expected_receipts,
                    )
                    self.assertEqual(filesystem_identity(root)[:4], root_identity)
                    self.assertEqual(
                        fixture.control.routine.stage_snapshot(staged.stage_path),
                        stage_before,
                    )
                    fixture.control.assert_private_stage(
                        self,
                        staged.stage_path.parent,
                    )
                    self.assertEqual(
                        filesystem_identity(prepared.initial.activation_lock),
                        lock_before,
                    )
                    self.assertEqual(
                        process_descriptor_inventory(),
                        descriptors_before,
                    )
                    if cut == "already-target-needs-parent-fsync":
                        recovery = (
                            run_control_maintenance_recovery_already_target_parent_fsync_probe(
                                deployment,
                                prepared,
                                journal_raw,
                            )
                        )
                        self.assertEqual(
                            recovery.exit_status,
                            CONTROL_MAINTENANCE_RECOVERY_PARENT_FSYNC_EXIT,
                            recovery.diagnostic,
                        )
                        self.assertEqual(recovery.diagnostic, "")
                        self.assertEqual(recovery.root_parent_fsyncs, 1)
                        advanced_journal_raw = exact_live_journal(root)
                        current = canonical_value(journal_raw)
                        advanced = canonical_value(advanced_journal_raw)
                        self.assertEqual(
                            advanced["transaction_id"],
                            current["transaction_id"],
                        )
                        self.assertEqual(
                            advanced["sequence"],
                            current["sequence"] + 1,
                        )
                        self.assertEqual(
                            advanced["previous_journal_sha256"],
                            sha256(journal_raw),
                        )
                        self.assertEqual(advanced["phase"], "control-switching")
                        self.assertEqual(
                            advanced["pending_step"],
                            {
                                "operation": "replace-control",
                                "index": 1,
                                "role": MAINTENANCE_REPLACEMENT_ROLES[1],
                            },
                        )
                        assert_control_maintenance_replacement_prefix(
                            staged,
                            completed_index=0,
                        )
                        self.assertEqual(
                            exact_live_journal(root),
                            advanced_journal_raw,
                        )
                        assert_control_maintenance_additive_set_installed(staged)
                        self.assertEqual(
                            receipt_digest_inventory(root),
                            expected_receipts,
                        )
                        self.assertEqual(
                            filesystem_identity(root)[:4],
                            root_identity,
                        )
                        self.assertEqual(
                            fixture.control.routine.stage_snapshot(
                                staged.stage_path
                            ),
                            stage_before,
                        )
                        fixture.control.assert_private_stage(
                            self,
                            staged.stage_path.parent,
                        )
                        self.assertEqual(
                            filesystem_identity(prepared.initial.activation_lock),
                            lock_before,
                        )
                        self.assertEqual(
                            process_descriptor_inventory(),
                            descriptors_before,
                        )
                        self.assertGreater(
                            recovery.controller_parent_fsyncs,
                            0,
                            "public recovery must fsync controller parent before journal advance",
                        )
                        continue
                    recovery_index = (
                        replacement_index
                        if activation_cut in pre_replace_cuts
                        else replacement_index + 1
                    )

                    recovery_exit = (
                        run_control_maintenance_recovery_replace_process_loss(
                            deployment,
                            prepared,
                            journal_raw,
                            direction=direction,
                            replacement_index=recovery_index,
                        )
                    )

                    self.assertEqual(
                        recovery_exit.exit_status,
                        CONTROL_MAINTENANCE_REPLACE_PROCESS_LOSS_EXIT,
                        recovery_exit.diagnostic,
                    )
                    self.assertEqual(recovery_exit.diagnostic, "")
                    advanced_journal_raw = (
                        assert_control_maintenance_mixed_replacement_state(
                            prepared,
                            direction=direction,
                            replacement_index=recovery_index,
                        )
                    )
                    if activation_cut in pre_replace_cuts:
                        self.assertEqual(advanced_journal_raw, journal_raw)
                    else:
                        self.assertNotEqual(advanced_journal_raw, journal_raw)
                    self.assertEqual(
                        exact_live_journal(root),
                        advanced_journal_raw,
                    )
                    assert_control_maintenance_additive_set_installed(staged)
                    self.assertEqual(
                        receipt_digest_inventory(root),
                        expected_receipts,
                    )
                    self.assertEqual(filesystem_identity(root)[:4], root_identity)
                    self.assertEqual(
                        fixture.control.routine.stage_snapshot(staged.stage_path),
                        stage_before,
                    )
                    fixture.control.assert_private_stage(
                        self,
                        staged.stage_path.parent,
                    )
                    self.assertEqual(
                        filesystem_identity(prepared.initial.activation_lock),
                        lock_before,
                    )
                    self.assertEqual(
                        process_descriptor_inventory(),
                        descriptors_before,
                    )

    def _assert_public_recovery_replays_each_control_replacement(
        self,
        direction: str,
    ) -> None:
        for replacement_index in range(len(MAINTENANCE_REPLACEMENT_ROLES) - 1):
            with self.subTest(  # noqa: SIM117
                direction=direction,
                replacement_index=replacement_index,
            ):
                with tempfile.TemporaryDirectory() as directory:
                    fixture = ControlMaintenanceActivationFixture(
                        Path(directory).resolve()
                    )
                    deployment = fixture.deployment()
                    prepared = fixture.staged_activation(
                        distinct_replacement_bytes=True
                    )
                    staged = prepared.staged
                    root = prepared.initial.canonical_root
                    selector_a = selector_raws(root)
                    expected_receipts = expected_active_receipt_inventory(
                        selector_a[1],
                        staged,
                    )
                    stage_before = fixture.control.routine.stage_snapshot(
                        staged.stage_path
                    )
                    root_identity = filesystem_identity(root)[:4]
                    lock_before = filesystem_identity(
                        prepared.initial.activation_lock
                    )
                    descriptors_before = process_descriptor_inventory()

                    activation_exit = (
                        run_control_maintenance_activation_replace_process_loss(
                            deployment,
                            prepared,
                            direction=direction,
                            replacement_index=replacement_index,
                        )
                    )

                    self.assertEqual(
                        activation_exit.exit_status,
                        CONTROL_MAINTENANCE_REPLACE_PROCESS_LOSS_EXIT,
                        activation_exit.diagnostic,
                    )
                    self.assertEqual(activation_exit.diagnostic, "")
                    journal_raw = (
                        assert_control_maintenance_mixed_replacement_state(
                            prepared,
                            direction=direction,
                            replacement_index=replacement_index,
                        )
                    )
                    self.assertEqual(exact_live_journal(root), journal_raw)
                    assert_control_maintenance_additive_set_installed(staged)
                    self.assertEqual(
                        receipt_digest_inventory(root),
                        expected_receipts,
                    )
                    self.assertEqual(filesystem_identity(root)[:4], root_identity)
                    self.assertEqual(
                        fixture.control.routine.stage_snapshot(staged.stage_path),
                        stage_before,
                    )
                    fixture.control.assert_private_stage(
                        self,
                        staged.stage_path.parent,
                    )
                    self.assertEqual(
                        filesystem_identity(prepared.initial.activation_lock),
                        lock_before,
                    )
                    self.assertEqual(
                        process_descriptor_inventory(),
                        descriptors_before,
                    )
                    mixed_tree = fixture.control.tree_state(root)

                    recovery_exit = (
                        run_control_maintenance_recovery_replace_process_loss(
                            deployment,
                            prepared,
                            journal_raw,
                            direction=direction,
                            replacement_index=replacement_index + 1,
                        )
                    )

                    if (
                        recovery_exit.exit_status
                        != CONTROL_MAINTENANCE_REPLACE_PROCESS_LOSS_EXIT
                    ):
                        self.assertEqual(fixture.control.tree_state(root), mixed_tree)
                        self.assertEqual(exact_live_journal(root), journal_raw)
                        self.assertEqual(
                            assert_control_maintenance_mixed_replacement_state(
                                prepared,
                                direction=direction,
                                replacement_index=replacement_index,
                            ),
                            journal_raw,
                        )
                        assert_control_maintenance_additive_set_installed(staged)
                        self.assertEqual(
                            receipt_digest_inventory(root),
                            expected_receipts,
                        )
                        self.assertEqual(
                            filesystem_identity(root)[:4],
                            root_identity,
                        )
                        self.assertEqual(
                            fixture.control.routine.stage_snapshot(staged.stage_path),
                            stage_before,
                        )
                        self.assertEqual(
                            filesystem_identity(prepared.initial.activation_lock),
                            lock_before,
                        )
                        self.assertEqual(
                            process_descriptor_inventory(),
                            descriptors_before,
                        )
                    self.assertEqual(
                        recovery_exit.exit_status,
                        CONTROL_MAINTENANCE_REPLACE_PROCESS_LOSS_EXIT,
                        recovery_exit.diagnostic,
                    )
                    self.assertEqual(recovery_exit.diagnostic, "")
                    advanced_journal_raw = (
                        assert_control_maintenance_mixed_replacement_state(
                            prepared,
                            direction=direction,
                            replacement_index=replacement_index + 1,
                        )
                    )
                    self.assertNotEqual(advanced_journal_raw, journal_raw)
                    self.assertEqual(
                        exact_live_journal(root),
                        advanced_journal_raw,
                    )
                    assert_control_maintenance_additive_set_installed(staged)
                    self.assertEqual(
                        receipt_digest_inventory(root),
                        expected_receipts,
                    )
                    self.assertEqual(filesystem_identity(root)[:4], root_identity)
                    self.assertEqual(
                        fixture.control.routine.stage_snapshot(staged.stage_path),
                        stage_before,
                    )
                    fixture.control.assert_private_stage(
                        self,
                        staged.stage_path.parent,
                    )
                    self.assertEqual(
                        filesystem_identity(prepared.initial.activation_lock),
                        lock_before,
                    )
                    self.assertEqual(
                        process_descriptor_inventory(),
                        descriptors_before,
                    )

    def test_public_candidate_rejection_restores_prior_complete_control_set(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        prepared = self.fixture.staged_activation()
        staged = prepared.staged
        root = prepared.initial.canonical_root
        selector_a = selector_raws(root)
        prior_receipts = receipt_digest_inventory(root)
        root_before = self.fixture.control.tree_state(root)
        stage_before = self.fixture.control.routine.stage_snapshot(staged.stage_path)
        lock_before = filesystem_identity(prepared.initial.activation_lock)
        descriptors_before = process_descriptor_inventory()
        cleanup_program = expected_control_maintenance_cleanup(staged, root)
        smoke = RejectCandidateAcceptPriorControlMaintenanceSmoke(prepared)
        replacements: list[tuple[str, int, str]] = []
        cleanups: list[ControlMaintenanceCleanupStep] = []
        replace_control = deployment._replace_control_maintenance_artifact
        remove_artifact = deployment._remove_activation_artifact
        remove_directory = deployment._remove_activation_directory

        def observe_replace(*args, **kwargs):
            result = replace_control(*args, **kwargs)
            replacement = kwargs["replacement"]
            replacements.append(
                (kwargs["direction"], kwargs["index"], replacement.role)
            )
            return result

        def observe_remove_artifact(root_fd, artifact):
            result = remove_artifact(root_fd, artifact)
            relative = Path(artifact.installed["path"]).relative_to(root).as_posix()
            cleanups.append(
                ControlMaintenanceCleanupStep(
                    "remove-artifact",
                    artifact.role,
                    relative,
                )
            )
            return result

        def observe_remove_directory(root_fd, relative_path):
            result = remove_directory(root_fd, relative_path)
            cleanups.append(
                ControlMaintenanceCleanupStep(
                    "remove-directory",
                    relative_path,
                    relative_path,
                )
            )
            return result

        with (
            mock.patch.object(
                deployment,
                "_spawn_activation_smoke_child",
                smoke,
            ),
            mock.patch.object(
                deployment,
                "_replace_control_maintenance_artifact",
                side_effect=observe_replace,
            ),
            mock.patch.object(
                deployment,
                "_remove_activation_artifact",
                side_effect=observe_remove_artifact,
            ),
            mock.patch.object(
                deployment,
                "_remove_activation_directory",
                side_effect=observe_remove_directory,
            ),
        ):
            result = deployment.activate_staged(prepared.activation)

        candidate_receipt_sha256 = sha256(staged.deployment_raw)
        expected_replacements = [
            (direction, index, role)
            for direction in ("candidate", "prior")
            for index, role in enumerate(MAINTENANCE_REPLACEMENT_ROLES)
        ]
        self.assertEqual(result.outcome, "restored-prior")
        self.assertEqual(result.candidate_receipt_sha256, candidate_receipt_sha256)
        self.assertEqual(
            result.active_receipt_sha256,
            prepared.active.active_receipt_sha256,
        )
        self.assertEqual(
            result.accepted_envelope_sha256,
            sha256(smoke.rollback_output),
        )
        self.assertEqual(frozenset(result.journal_value), JOURNAL_KEYS)
        self.assertEqual(
            result.journal_value["transaction_class"],
            "control-set-maintenance",
        )
        self.assertEqual(result.journal_value["phase"], "terminal")
        self.assertEqual(
            result.journal_value["terminal_result"],
            {
                "outcome": "restored-prior",
                "candidate_receipt_sha256": candidate_receipt_sha256,
                "active_receipt_sha256": prepared.active.active_receipt_sha256,
                "accepted_envelope_sha256": sha256(smoke.rollback_output),
                "failure_class": "candidate-smoke-rejected",
            },
        )
        self.assertIsNone(result.journal_value["candidate_smoke_acceptance"])
        self.assertIsNotNone(result.journal_value["rollback_smoke_acceptance"])
        self.assertEqual(smoke.phases, ["candidate-smoke", "rollback-smoke"])
        self.assertEqual(replacements, expected_replacements)
        self.assertEqual(cleanups, list(cleanup_program))
        self.assertEqual(selector_raws(root), selector_a)
        assert_prior_control_set_installed(staged)
        self.assertEqual(receipt_digest_inventory(root), prior_receipts)
        assert_selector_has_distinct_retained_copy(root, selector_a[1])
        assert_no_transaction_residue(root)
        self.assertEqual(
            self.fixture.control.tree_state(root),
            tree_state_with_result(root_before, result),
        )
        self.assertEqual(
            self.fixture.control.routine.stage_snapshot(staged.stage_path),
            stage_before,
        )
        self.fixture.control.assert_private_stage(self, staged.stage_path.parent)
        self.assertEqual(
            filesystem_identity(prepared.initial.activation_lock),
            lock_before,
        )
        self.assertEqual(process_descriptor_inventory(), descriptors_before)

    def test_public_recovery_replays_control_maintenance_fail_stop_terminal(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        prepared = self.fixture.staged_activation()
        staged = prepared.staged
        root = prepared.initial.canonical_root
        selector_a = selector_raws(root)
        expected_receipts = expected_active_receipt_inventory(
            selector_a[1],
            staged,
        )
        stage_before = self.fixture.control.routine.stage_snapshot(staged.stage_path)
        lock_before = filesystem_identity(prepared.initial.activation_lock)
        descriptors_before = process_descriptor_inventory()
        smoke = RejectCandidateAndPriorControlMaintenanceSmoke(prepared)

        with mock.patch.object(
            deployment,
            "_spawn_activation_smoke_child",
            smoke,
        ):
            first = deployment.activate_staged(prepared.activation)

        candidate_receipt_sha256 = sha256(staged.deployment_raw)
        self.assertEqual(first.outcome, "recovery-required")
        self.assertEqual(first.candidate_receipt_sha256, candidate_receipt_sha256)
        self.assertIsNone(first.active_receipt_sha256)
        self.assertIsNone(first.accepted_envelope_sha256)
        self.assertEqual(first.journal_value["phase"], "terminal")
        self.assertEqual(
            first.journal_value["terminal_result"],
            {
                "outcome": "recovery-required",
                "candidate_receipt_sha256": candidate_receipt_sha256,
                "active_receipt_sha256": None,
                "accepted_envelope_sha256": None,
                "failure_class": "rollback-smoke-rejected",
            },
        )
        self.assertEqual(smoke.phases, ["candidate-smoke", "rollback-smoke"])
        self.assertEqual(selector_raws(root), selector_a)
        assert_prior_control_set_installed(staged)
        assert_control_maintenance_additive_set_installed(staged)
        self.assertEqual(receipt_digest_inventory(root), expected_receipts)
        assert_selector_has_distinct_retained_copy(root, selector_a[1])
        assert_no_control_maintenance_temporaries(root)
        journal_raw = exact_live_journal(root)
        self.assertEqual(journal_raw, first.journal_raw)
        recovery_tree = self.fixture.control.tree_state(root)

        try:
            with mock.patch.object(
                deployment,
                "_spawn_activation_smoke_child",
                smoke,
            ):
                recovered = deployment.recover_transaction(
                    deployment.RecoveryRequest(
                        activation=prepared.activation,
                        expected_journal_raw=journal_raw,
                    )
                )
        except deployment.DeploymentError:
            self.assertEqual(self.fixture.control.tree_state(root), recovery_tree)
            self.assertEqual(smoke.phases, ["candidate-smoke", "rollback-smoke"])
            self.assertEqual(exact_live_journal(root), journal_raw)
            self.assertEqual(
                self.fixture.control.routine.stage_snapshot(staged.stage_path),
                stage_before,
            )
            self.assertEqual(
                filesystem_identity(prepared.initial.activation_lock),
                lock_before,
            )
            self.assertEqual(process_descriptor_inventory(), descriptors_before)
            raise

        self.assertEqual(recovered, first)
        self.assertEqual(smoke.phases, ["candidate-smoke", "rollback-smoke"])
        self.assertEqual(self.fixture.control.tree_state(root), recovery_tree)
        self.assertEqual(exact_live_journal(root), journal_raw)
        self.assertEqual(selector_raws(root), selector_a)
        assert_prior_control_set_installed(staged)
        assert_control_maintenance_additive_set_installed(staged)
        self.assertEqual(receipt_digest_inventory(root), expected_receipts)
        assert_no_control_maintenance_temporaries(root)
        self.assertEqual(
            self.fixture.control.routine.stage_snapshot(staged.stage_path),
            stage_before,
        )
        self.fixture.control.assert_private_stage(self, staged.stage_path.parent)
        self.assertEqual(
            filesystem_identity(prepared.initial.activation_lock),
            lock_before,
        )
        self.assertEqual(process_descriptor_inventory(), descriptors_before)
