from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ._control_maintenance_activation_support import (
    assert_prior_control_set_installed,
)
from ._manual_rollback_support import (
    ManualRecoveryPopenAdapter,
    PublicManualRollbackSmokeBoundary,
    activate_a_b_control_c,
    activate_routine_a_b_c,
    rollback_authorization_raw,
    run_manual_candidate_controller_replacement_process_loss,
    run_manual_candidate_smoke_process_loss,
    run_manual_early_process_loss,
    run_manual_journal_process_loss_cut,
    run_manual_prior_controller_replacement_process_loss,
    run_manual_rollback_cleanup_process_loss,
    run_manual_rollback_smoke_process_loss,
    run_manual_terminal_process_loss,
)
from ._routine_activation_support import (
    assert_no_transaction_residue,
    receipt_digest_inventory,
    selector_raws,
)
from ._support import sha256


class ManualRollbackRecoveryTests(unittest.TestCase):
    def test_early_phase_recovery_runs_through_staged_c(self) -> None:
        cases = (
            ("prepared", None),
            ("frozen", None),
            ("drained", None),
            ("rollback-receipt", 0),
            ("deployment-receipt", 1),
        )
        for case_index, (boundary, additive_index) in enumerate(cases):
            with (
                self.subTest(boundary=boundary),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory).resolve()
                chain = activate_a_b_control_c(root)
                deployment = chain.deployment
                request = deployment.RollbackToRequest(
                    canonical_root=chain.canonical_root,
                    expected_active_receipt_sha256=chain.receipt_c_sha256,
                    target_receipt_sha256=chain.receipt_a_sha256,
                    maintenance_transaction_sha256=f"{case_index + 11:x}" * 64,
                )
                prepared = deployment.prepare_rollback_to(request)
                authorization_raw = rollback_authorization_raw(prepared)
                before_receipts = receipt_digest_inventory(chain.canonical_root)
                result_directory = chain.canonical_root / "transaction-results"
                before_results = {
                    path.name: path.read_bytes()
                    for path in result_directory.glob("*.json")
                }
                stage_root = root / f"manual-early-{boundary}-stage"
                journal_raw = run_manual_early_process_loss(
                    chain,
                    request,
                    authorization_raw,
                    stage_root,
                    boundary=boundary,
                )
                journal = json.loads(journal_raw)
                self.assertEqual(
                    journal["transaction_class"],
                    "manual-exact-target-rollback",
                )
                if additive_index is None:
                    self.assertEqual(journal["phase"], boundary)
                    self.assertIsNone(journal["pending_step"])
                else:
                    self.assertEqual(journal["phase"], "additive-installing")
                    self.assertEqual(
                        journal["pending_step"],
                        {
                            "operation": "install",
                            "index": additive_index,
                            "role": boundary,
                        },
                    )
                verified = deployment.verify_deployment_stage(
                    stage_root / "stage.json"
                )
                d = next(
                    item
                    for item in verified.artifacts
                    if item.role == "deployment-receipt"
                )
                r_d = next(
                    item
                    for item in verified.artifacts
                    if item.role == "rollback-receipt"
                )
                additive = (r_d, d)
                if additive_index is None:
                    self.assertFalse(r_d.installed_path.exists())
                    self.assertFalse(d.installed_path.exists())
                else:
                    installed = additive[additive_index]
                    temporary = installed.installed_path.parent / (
                        f".task-witness-install-{journal['transaction_id']}-"
                        f"{additive_index}.tmp"
                    )
                    self.assertTrue(installed.installed_path.exists())
                    self.assertTrue(temporary.exists())
                    installed_stat = installed.installed_path.lstat()
                    temporary_stat = temporary.lstat()
                    self.assertEqual(installed_stat.st_nlink, 2)
                    self.assertEqual(temporary_stat.st_nlink, 2)
                    self.assertEqual(
                        (installed_stat.st_dev, installed_stat.st_ino),
                        (temporary_stat.st_dev, temporary_stat.st_ino),
                    )
                activation = deployment.ActivationRequest(
                    deployment=request,
                    authorization_raw=authorization_raw,
                    stage_receipt=stage_root / "stage.json",
                )
                adapter = ManualRecoveryPopenAdapter(
                    chain,
                    prepared,
                    deployment.subprocess.Popen,
                    target_accepted=True,
                )
                with (
                    mock.patch.object(
                        deployment,
                        "_spawn_activation_smoke_child",
                        side_effect=AssertionError(
                            "outer/live controller executed recovery"
                        ),
                    ),
                    mock.patch.object(
                        deployment.subprocess,
                        "Popen",
                        side_effect=adapter,
                    ),
                ):
                    result = deployment.recover_transaction(
                        deployment.RecoveryRequest(
                            activation=activation,
                            expected_journal_raw=journal_raw,
                        )
                    )
                self.assertEqual(result.outcome, "candidate-active")
                self.assertEqual(adapter.markers, ["A"])
                self.assertEqual(
                    receipt_digest_inventory(chain.canonical_root),
                    before_receipts | {sha256(r_d.raw), sha256(d.raw)},
                )
                for artifact in additive:
                    metadata = artifact.installed_path.lstat()
                    self.assertEqual(metadata.st_nlink, 1)
                    self.assertEqual(artifact.installed_path.read_bytes(), artifact.raw)
                self.assertEqual(
                    selector_raws(chain.canonical_root)[0],
                    chain.active_a_raw,
                )
                retained = (
                    result_directory / f"sha256-{result.transaction_id}.json"
                )
                self.assertEqual(
                    {
                        path.name: path.read_bytes()
                        for path in result_directory.glob("*.json")
                    },
                    {**before_results, retained.name: result.journal_raw},
                )
                assert_no_transaction_residue(chain.canonical_root)

    def test_terminal_recovery_is_smoke_free_and_k1_idempotent(self) -> None:
        cases = (
            (True, "manual-target-active", "candidate-active"),
            (False, "manual-current-restored", "restored-prior"),
        )
        for target_accepted, durable_outcome, public_outcome in cases:
            with (
                self.subTest(outcome=durable_outcome),
                tempfile.TemporaryDirectory() as directory,
            ):
                    root = Path(directory).resolve()
                    chain = activate_routine_a_b_c(root)
                    deployment = chain.deployment
                    request = deployment.RollbackToRequest(
                        canonical_root=chain.canonical_root,
                        expected_active_receipt_sha256=chain.receipt_c_sha256,
                        target_receipt_sha256=chain.receipt_a_sha256,
                        maintenance_transaction_sha256=(
                            "1" * 64 if target_accepted else "2" * 64
                        ),
                    )
                    prepared = deployment.prepare_rollback_to(request)
                    authorization_raw = rollback_authorization_raw(prepared)
                    activation = deployment.ActivationRequest(
                        deployment=request,
                        authorization_raw=authorization_raw,
                        stage_receipt=root / "manual-terminal-stage" / "stage.json",
                    )
                    before_receipts = receipt_digest_inventory(
                        chain.canonical_root
                    )
                    before_results = {
                        path.name: path.read_bytes()
                        for path in (
                            chain.canonical_root / "transaction-results"
                        ).glob("*.json")
                    }

                    journal_raw = run_manual_terminal_process_loss(
                        chain,
                        request,
                        authorization_raw,
                        activation.stage_receipt.parent,
                        target_accepted=target_accepted,
                    )
                    journal = json.loads(journal_raw)
                    self.assertEqual(journal["phase"], "terminal")
                    self.assertEqual(
                        journal["transaction_class"],
                        "manual-exact-target-rollback",
                    )
                    self.assertEqual(
                        journal["terminal_result"]["outcome"],
                        durable_outcome,
                    )
                    retained = (
                        chain.canonical_root
                        / "transaction-results"
                        / f"sha256-{journal['transaction_id']}.json"
                    )
                    self.assertFalse(retained.exists())
                    forbidden_smoke = mock.Mock(
                        side_effect=AssertionError("terminal recovery reran smoke")
                    )
                    with mock.patch.object(
                        deployment,
                        "_spawn_activation_smoke_child",
                        forbidden_smoke,
                    ):
                        recovered = deployment.recover_transaction(
                            deployment.RecoveryRequest(
                                activation=activation,
                                expected_journal_raw=journal_raw,
                            )
                        )
                    forbidden_smoke.assert_not_called()
                    self.assertEqual(recovered.outcome, public_outcome)
                    self.assertEqual(recovered.journal_raw, journal_raw)
                    self.assertEqual(retained.read_bytes(), journal_raw)
                    self.assertFalse(
                        (chain.canonical_root / "transaction.json").exists()
                    )

                    reconciled = []
                    for _ in range(2):
                        reconciled.append(
                            deployment.reconcile_transaction_result(
                                deployment.ResultReconciliationRequest(
                                    activation=activation,
                                    expected_terminal_journal_raw=journal_raw,
                                )
                            )
                        )
                    self.assertEqual(reconciled, [recovered, recovered])
                    self.assertEqual(
                        {
                            path.name: path.read_bytes()
                            for path in (
                                chain.canonical_root / "transaction-results"
                            ).glob("*.json")
                        },
                        {
                            **before_results,
                            retained.name: journal_raw,
                        },
                    )
                    if target_accepted:
                        deployment_raw = (
                            chain.canonical_root / "deployment.json"
                        ).read_bytes()
                        self.assertEqual(
                            recovered.active_receipt_sha256,
                            sha256(deployment_raw),
                        )
                        self.assertEqual(
                            selector_raws(chain.canonical_root)[0],
                            chain.active_a_raw,
                        )
                        self.assertEqual(
                            len(receipt_digest_inventory(chain.canonical_root)),
                            len(before_receipts) + 2,
                        )
                    else:
                        self.assertEqual(
                            recovered.active_receipt_sha256,
                            chain.receipt_c_sha256,
                        )
                        self.assertEqual(
                            selector_raws(chain.canonical_root),
                            (
                                prepared.plan.precondition.active_raw,
                                chain.receipt_c_raw,
                            ),
                        )
                        self.assertEqual(
                            receipt_digest_inventory(chain.canonical_root),
                            before_receipts,
                        )
                    assert_no_transaction_residue(chain.canonical_root)

    def test_recovery_replays_candidate_controller_replacement_through_c(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            chain = activate_a_b_control_c(root)
            deployment = chain.deployment
            request = deployment.RollbackToRequest(
                canonical_root=chain.canonical_root,
                expected_active_receipt_sha256=chain.receipt_c_sha256,
                target_receipt_sha256=chain.receipt_a_sha256,
                maintenance_transaction_sha256="3" * 64,
            )
            prepared = deployment.prepare_rollback_to(request)
            authorization_raw = rollback_authorization_raw(prepared)
            stage_root = root / "manual-replacement-stage"
            journal_raw = run_manual_candidate_controller_replacement_process_loss(
                chain,
                request,
                authorization_raw,
                stage_root,
            )
            journal = json.loads(journal_raw)
            self.assertEqual(journal["phase"], "control-switching")
            self.assertEqual(
                journal["pending_step"],
                {"operation": "replace-control", "index": 0, "role": "controller"},
            )
            target_controller = prepared.plan.target_authority.control_raws[
                "controller"
            ]
            current_controller = prepared.plan.precondition.control_raws[
                "controller"
            ]
            controller_path = (
                chain.canonical_root / "controller" / "task_witness_deploy.py"
            )
            self.assertNotEqual(target_controller, current_controller)
            self.assertEqual(controller_path.read_bytes(), target_controller)
            activation = deployment.ActivationRequest(
                deployment=request,
                authorization_raw=authorization_raw,
                stage_receipt=stage_root / "stage.json",
            )
            adapter = ManualRecoveryPopenAdapter(
                chain,
                prepared,
                deployment.subprocess.Popen,
                target_accepted=True,
            )
            with (
                mock.patch.object(
                    deployment,
                    "_spawn_activation_smoke_child",
                    side_effect=AssertionError(
                        "outer/live controller executed recovery"
                    ),
                ),
                mock.patch.object(
                    deployment.subprocess,
                    "Popen",
                    side_effect=adapter,
                ),
            ):
                result = deployment.recover_transaction(
                    deployment.RecoveryRequest(
                        activation=activation,
                        expected_journal_raw=journal_raw,
                    )
                )
            self.assertEqual(result.outcome, "candidate-active")
            self.assertEqual(adapter.markers, ["A"])
            self.assertEqual(selector_raws(chain.canonical_root)[0], chain.active_a_raw)
            self.assertNotEqual(controller_path.read_bytes(), current_controller)
            self.assertEqual(controller_path.read_bytes(), target_controller)
            assert_no_transaction_residue(chain.canonical_root)

    def test_recovery_replays_prior_controller_replacement_through_c(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            chain = activate_a_b_control_c(root)
            deployment = chain.deployment
            request = deployment.RollbackToRequest(
                canonical_root=chain.canonical_root,
                expected_active_receipt_sha256=chain.receipt_c_sha256,
                target_receipt_sha256=chain.receipt_a_sha256,
                maintenance_transaction_sha256="4" * 64,
            )
            prepared = deployment.prepare_rollback_to(request)
            authorization_raw = rollback_authorization_raw(prepared)
            before_receipts = receipt_digest_inventory(chain.canonical_root)
            stage_root = root / "manual-prior-replacement-stage"
            journal_raw = run_manual_prior_controller_replacement_process_loss(
                chain,
                request,
                authorization_raw,
                stage_root,
            )
            journal = json.loads(journal_raw)
            self.assertEqual(journal["phase"], "prior-restoring")
            self.assertEqual(
                journal["pending_step"],
                {"operation": "replace-control", "index": 0, "role": "controller"},
            )
            target_controller = prepared.plan.target_authority.control_raws[
                "controller"
            ]
            current_controller = prepared.plan.precondition.control_raws[
                "controller"
            ]
            controller_path = (
                chain.canonical_root / "controller" / "task_witness_deploy.py"
            )
            self.assertNotEqual(target_controller, current_controller)
            self.assertEqual(controller_path.read_bytes(), current_controller)
            activation = deployment.ActivationRequest(
                deployment=request,
                authorization_raw=authorization_raw,
                stage_receipt=stage_root / "stage.json",
            )
            adapter = ManualRecoveryPopenAdapter(
                chain,
                prepared,
                deployment.subprocess.Popen,
                target_accepted=False,
                current_accepted=True,
            )
            with (
                mock.patch.object(
                    deployment,
                    "_spawn_activation_smoke_child",
                    side_effect=AssertionError(
                        "outer/live controller executed recovery"
                    ),
                ),
                mock.patch.object(
                    deployment.subprocess,
                    "Popen",
                    side_effect=adapter,
                ),
            ):
                result = deployment.recover_transaction(
                    deployment.RecoveryRequest(
                        activation=activation,
                        expected_journal_raw=journal_raw,
                    )
                )
            self.assertEqual(result.outcome, "restored-prior")
            self.assertEqual(adapter.markers, ["C"])
            self.assertEqual(controller_path.read_bytes(), current_controller)
            self.assertEqual(
                selector_raws(chain.canonical_root),
                (prepared.plan.precondition.active_raw, chain.receipt_c_raw),
            )
            self.assertEqual(
                receipt_digest_inventory(chain.canonical_root),
                before_receipts,
            )
            assert_no_transaction_residue(chain.canonical_root)

    def test_candidate_smoke_recovery_respects_durable_acceptance(self) -> None:
        for cut, expected_markers in (
            ("child-return", ["A"]),
            ("accepted-journal", []),
        ):
            with (
                self.subTest(cut=cut),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory).resolve()
                chain = activate_a_b_control_c(root)
                deployment = chain.deployment
                request = deployment.RollbackToRequest(
                    canonical_root=chain.canonical_root,
                    expected_active_receipt_sha256=chain.receipt_c_sha256,
                    target_receipt_sha256=chain.receipt_a_sha256,
                    maintenance_transaction_sha256=(
                        "5" * 64 if cut == "child-return" else "6" * 64
                    ),
                )
                prepared = deployment.prepare_rollback_to(request)
                authorization_raw = rollback_authorization_raw(prepared)
                stage_root = root / f"manual-smoke-{cut}-stage"
                journal_raw = run_manual_candidate_smoke_process_loss(
                    chain,
                    request,
                    authorization_raw,
                    stage_root,
                    cut=cut,
                )
                journal = json.loads(journal_raw)
                self.assertEqual(journal["phase"], "candidate-smoke")
                self.assertEqual(
                    journal["candidate_smoke_acceptance"] is not None,
                    cut == "accepted-journal",
                )
                activation = deployment.ActivationRequest(
                    deployment=request,
                    authorization_raw=authorization_raw,
                    stage_receipt=stage_root / "stage.json",
                )
                adapter = ManualRecoveryPopenAdapter(
                    chain,
                    prepared,
                    deployment.subprocess.Popen,
                    target_accepted=True,
                )
                with (
                    mock.patch.object(
                        deployment,
                        "_spawn_activation_smoke_child",
                        side_effect=AssertionError(
                            "outer/live controller executed recovery"
                        ),
                    ),
                    mock.patch.object(
                        deployment.subprocess,
                        "Popen",
                        side_effect=adapter,
                    ),
                ):
                    result = deployment.recover_transaction(
                        deployment.RecoveryRequest(
                            activation=activation,
                            expected_journal_raw=journal_raw,
                        )
                    )
                self.assertEqual(result.outcome, "candidate-active")
                self.assertEqual(
                    result.journal_value["terminal_result"]["outcome"],
                    "manual-target-active",
                )
                self.assertEqual(adapter.markers, expected_markers)
                self.assertEqual(
                    selector_raws(chain.canonical_root)[0],
                    chain.active_a_raw,
                )
                assert_no_transaction_residue(chain.canonical_root)

    def test_rollback_smoke_recovery_respects_durable_acceptance(self) -> None:
        for cut, expected_markers in (
            ("child-return", ["C"]),
            ("accepted-journal", []),
        ):
            with (
                self.subTest(cut=cut),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory).resolve()
                chain = activate_a_b_control_c(root)
                deployment = chain.deployment
                request = deployment.RollbackToRequest(
                    canonical_root=chain.canonical_root,
                    expected_active_receipt_sha256=chain.receipt_c_sha256,
                    target_receipt_sha256=chain.receipt_a_sha256,
                    maintenance_transaction_sha256=(
                        "7" * 64 if cut == "child-return" else "8" * 64
                    ),
                )
                prepared = deployment.prepare_rollback_to(request)
                authorization_raw = rollback_authorization_raw(prepared)
                before_receipts = receipt_digest_inventory(chain.canonical_root)
                stage_root = root / f"manual-rollback-smoke-{cut}-stage"
                journal_raw = run_manual_rollback_smoke_process_loss(
                    chain,
                    request,
                    authorization_raw,
                    stage_root,
                    cut=cut,
                )
                journal = json.loads(journal_raw)
                self.assertEqual(journal["phase"], "rollback-smoke")
                self.assertEqual(
                    journal["rollback_smoke_acceptance"] is not None,
                    cut == "accepted-journal",
                )
                activation = deployment.ActivationRequest(
                    deployment=request,
                    authorization_raw=authorization_raw,
                    stage_receipt=stage_root / "stage.json",
                )
                adapter = ManualRecoveryPopenAdapter(
                    chain,
                    prepared,
                    deployment.subprocess.Popen,
                    target_accepted=False,
                    current_accepted=True,
                )
                with (
                    mock.patch.object(
                        deployment,
                        "_spawn_activation_smoke_child",
                        side_effect=AssertionError(
                            "outer/live controller executed recovery"
                        ),
                    ),
                    mock.patch.object(
                        deployment.subprocess,
                        "Popen",
                        side_effect=adapter,
                    ),
                ):
                    result = deployment.recover_transaction(
                        deployment.RecoveryRequest(
                            activation=activation,
                            expected_journal_raw=journal_raw,
                        )
                    )
                self.assertEqual(result.outcome, "restored-prior")
                self.assertEqual(
                    result.journal_value["terminal_result"]["outcome"],
                    "manual-current-restored",
                )
                self.assertEqual(adapter.markers, expected_markers)
                self.assertEqual(
                    selector_raws(chain.canonical_root),
                    (prepared.plan.precondition.active_raw, chain.receipt_c_raw),
                )
                self.assertEqual(
                    receipt_digest_inventory(chain.canonical_root),
                    before_receipts,
                )
                assert_no_transaction_residue(chain.canonical_root)

    def test_recovery_resumes_after_rollback_receipt_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            chain = activate_a_b_control_c(root)
            deployment = chain.deployment
            request = deployment.RollbackToRequest(
                canonical_root=chain.canonical_root,
                expected_active_receipt_sha256=chain.receipt_c_sha256,
                target_receipt_sha256=chain.receipt_a_sha256,
                maintenance_transaction_sha256="9" * 64,
            )
            prepared = deployment.prepare_rollback_to(request)
            authorization_raw = rollback_authorization_raw(prepared)
            before_receipts = receipt_digest_inventory(chain.canonical_root)
            result_directory = chain.canonical_root / "transaction-results"
            before_results = {
                path.name: path.read_bytes()
                for path in result_directory.glob("*.json")
            }
            before_nontransaction_files = {
                path.relative_to(chain.canonical_root): path.read_bytes()
                for path in chain.canonical_root.rglob("*")
                if path.is_file()
                and path.name != "transaction.json"
                and "transaction-results" not in path.parts
            }
            stage_root = root / "manual-rollback-cleanup-stage"
            journal_raw = run_manual_rollback_cleanup_process_loss(
                chain,
                request,
                authorization_raw,
                stage_root,
            )
            journal = json.loads(journal_raw)
            self.assertEqual(journal["phase"], "rollback-cleaning")
            self.assertEqual(
                journal["pending_step"],
                {
                    "operation": "remove-artifact",
                    "index": 0,
                    "role": "rollback-receipt",
                },
            )
            verified = deployment.verify_deployment_stage(stage_root / "stage.json")
            d = next(
                item
                for item in verified.artifacts
                if item.role == "deployment-receipt"
            )
            r_d = next(
                item
                for item in verified.artifacts
                if item.role == "rollback-receipt"
            )
            self.assertFalse(r_d.installed_path.exists())
            self.assertTrue(d.installed_path.exists())
            activation = deployment.ActivationRequest(
                deployment=request,
                authorization_raw=authorization_raw,
                stage_receipt=stage_root / "stage.json",
            )
            adapter = ManualRecoveryPopenAdapter(
                chain,
                prepared,
                deployment.subprocess.Popen,
                target_accepted=False,
                current_accepted=True,
            )
            with (
                mock.patch.object(
                    deployment,
                    "_spawn_activation_smoke_child",
                    side_effect=AssertionError(
                        "outer/live controller executed recovery"
                    ),
                ),
                mock.patch.object(
                    deployment.subprocess,
                    "Popen",
                    side_effect=adapter,
                ),
            ):
                result = deployment.recover_transaction(
                    deployment.RecoveryRequest(
                        activation=activation,
                        expected_journal_raw=journal_raw,
                    )
                )
            self.assertEqual(result.outcome, "restored-prior")
            self.assertEqual(
                result.journal_value["terminal_result"]["outcome"],
                "manual-current-restored",
            )
            self.assertEqual(adapter.markers, [])
            self.assertFalse(r_d.installed_path.exists())
            self.assertFalse(d.installed_path.exists())
            assert_prior_control_set_installed(verified)
            self.assertEqual(
                selector_raws(chain.canonical_root),
                (prepared.plan.precondition.active_raw, chain.receipt_c_raw),
            )
            self.assertEqual(
                receipt_digest_inventory(chain.canonical_root),
                before_receipts,
            )
            self.assertEqual(
                {
                    path.relative_to(chain.canonical_root): path.read_bytes()
                    for path in chain.canonical_root.rglob("*")
                    if path.is_file()
                    and path.name != "transaction.json"
                    and "transaction-results" not in path.parts
                },
                before_nontransaction_files,
            )
            retained = result_directory / f"sha256-{journal['transaction_id']}.json"
            self.assertEqual(
                {
                    path.name: path.read_bytes()
                    for path in result_directory.glob("*.json")
                },
                {**before_results, retained.name: result.journal_raw},
            )
            assert_no_transaction_residue(chain.canonical_root)

    def test_rollback_smoke_rejection_remains_recovery_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            chain = activate_a_b_control_c(root)
            deployment = chain.deployment
            request = deployment.RollbackToRequest(
                canonical_root=chain.canonical_root,
                expected_active_receipt_sha256=chain.receipt_c_sha256,
                target_receipt_sha256=chain.receipt_a_sha256,
                maintenance_transaction_sha256="a" * 64,
            )
            prepared = deployment.prepare_rollback_to(request)
            authorization_raw = rollback_authorization_raw(prepared)
            before_receipts = receipt_digest_inventory(chain.canonical_root)
            result_directory = chain.canonical_root / "transaction-results"
            before_results = {
                path.name: path.read_bytes()
                for path in result_directory.glob("*.json")
            }
            stage_root = root / "manual-rollback-recovery-required-stage"
            smoke = PublicManualRollbackSmokeBoundary(
                chain.canonical_root,
                chain.receipt_c_raw,
                chain.receipt_a_raw,
                candidate_accepted=False,
                rollback_accepted=False,
            )
            with mock.patch.object(
                deployment,
                "_spawn_activation_smoke_child",
                smoke,
            ):
                initial = deployment.rollback_to(
                    request,
                    authorization_raw,
                    stage_root,
                )
            self.assertEqual(initial.outcome, "recovery-required")
            self.assertEqual(smoke.phases, ["candidate-smoke", "rollback-smoke"])
            journal_raw = (chain.canonical_root / "transaction.json").read_bytes()
            journal = json.loads(journal_raw)
            self.assertEqual(initial.journal_raw, journal_raw)
            self.assertEqual(journal["phase"], "terminal")
            self.assertEqual(
                journal["terminal_result"],
                {
                    "outcome": "recovery-required",
                    "candidate_receipt_sha256": journal[
                        "candidate"
                    ]["deployment_receipt"]["sha256"],
                    "active_receipt_sha256": None,
                    "accepted_envelope_sha256": None,
                    "failure_class": "rollback-smoke-rejected",
                },
            )
            retained = result_directory / f"sha256-{journal['transaction_id']}.json"
            self.assertFalse(retained.exists())
            verified = deployment.verify_deployment_stage(stage_root / "stage.json")
            d = next(
                item
                for item in verified.artifacts
                if item.role == "deployment-receipt"
            )
            r_d = next(
                item
                for item in verified.artifacts
                if item.role == "rollback-receipt"
            )
            self.assertTrue(d.installed_path.exists())
            self.assertTrue(r_d.installed_path.exists())
            assert_prior_control_set_installed(verified)
            self.assertEqual(
                selector_raws(chain.canonical_root),
                (prepared.plan.precondition.active_raw, chain.receipt_c_raw),
            )
            self.assertEqual(
                receipt_digest_inventory(chain.canonical_root),
                before_receipts | {sha256(d.raw), sha256(r_d.raw)},
            )
            activation = deployment.ActivationRequest(
                deployment=request,
                authorization_raw=authorization_raw,
                stage_receipt=stage_root / "stage.json",
            )
            forbidden_smoke = mock.Mock(
                side_effect=AssertionError("recovery-required replayed smoke")
            )
            recovered = []
            for _ in range(2):
                with (
                    mock.patch.object(
                        deployment,
                        "_spawn_activation_smoke_child",
                        forbidden_smoke,
                    ),
                    mock.patch.object(
                        deployment.subprocess,
                        "Popen",
                        side_effect=AssertionError(
                            "recovery-required spawned a child"
                        ),
                    ),
                ):
                    recovered.append(
                        deployment.recover_transaction(
                            deployment.RecoveryRequest(
                                activation=activation,
                                expected_journal_raw=journal_raw,
                            )
                        )
                    )
            forbidden_smoke.assert_not_called()
            self.assertEqual(recovered, [initial, initial])
            self.assertEqual(
                (chain.canonical_root / "transaction.json").read_bytes(),
                journal_raw,
            )
            self.assertFalse(retained.exists())
            self.assertEqual(
                {
                    path.name: path.read_bytes()
                    for path in result_directory.glob("*.json")
                },
                before_results,
            )
            self.assertTrue(d.installed_path.exists())
            self.assertTrue(r_d.installed_path.exists())

    def test_manual_journal_persistence_reconciles_through_staged_c(self) -> None:
        cases = [
            ("mixed-candidate-control", cut)
            for cut in (
                "temp-create",
                "partial-write",
                "full-write",
                "replace",
                "parent-fsync",
            )
        ]
        cases.extend(
            (generation, cut)
            for generation in (
                "target-active-terminal",
                "current-restored-terminal",
            )
            for cut in (
                "temp-create",
                "partial-write",
                "full-write",
                "replace",
                "parent-fsync",
            )
        )
        prepublication = {"temp-create", "partial-write", "full-write"}
        for case_index, (generation, cut) in enumerate(cases):
            with (
                self.subTest(generation=generation, cut=cut),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory).resolve()
                chain = activate_a_b_control_c(root)
                deployment = chain.deployment
                request = deployment.RollbackToRequest(
                    canonical_root=chain.canonical_root,
                    expected_active_receipt_sha256=chain.receipt_c_sha256,
                    target_receipt_sha256=chain.receipt_a_sha256,
                    maintenance_transaction_sha256=f"{case_index + 1:x}" * 64,
                )
                prepared = deployment.prepare_rollback_to(request)
                authorization_raw = rollback_authorization_raw(prepared)
                stage_root = root / f"manual-journal-{generation}-{cut}-stage"
                journal_cut = run_manual_journal_process_loss_cut(
                    chain,
                    request,
                    authorization_raw,
                    stage_root,
                    generation=generation,
                    cut=cut,
                )
                current = json.loads(journal_cut.current_raw)
                target = json.loads(journal_cut.target_raw)
                self.assertEqual(
                    current["transaction_class"],
                    "manual-exact-target-rollback",
                )
                self.assertEqual(
                    target["transaction_class"],
                    "manual-exact-target-rollback",
                )
                self.assertEqual(target["sequence"], current["sequence"] + 1)
                self.assertEqual(
                    target["previous_journal_sha256"],
                    sha256(journal_cut.current_raw),
                )
                temporary = chain.canonical_root / (
                    f"transaction.{target['transaction_id']}."
                    f"{target['sequence']}.tmp"
                )
                live_raw = (
                    chain.canonical_root / "transaction.json"
                ).read_bytes()
                if cut in prepublication:
                    self.assertEqual(live_raw, journal_cut.current_raw)
                    self.assertTrue(temporary.exists())
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
                            len(temporary_raw), len(journal_cut.target_raw)
                        )
                    else:
                        self.assertEqual(temporary_raw, journal_cut.target_raw)
                else:
                    self.assertEqual(live_raw, journal_cut.target_raw)
                    self.assertFalse(temporary.exists())
                if generation == "mixed-candidate-control":
                    self.assertEqual(current["phase"], "control-switching")
                    self.assertEqual(target["phase"], "control-switching")
                    self.assertEqual(current["pending_step"]["index"], 0)
                    self.assertEqual(target["pending_step"]["index"], 1)
                else:
                    self.assertEqual(target["phase"], "terminal")
                    expected_durable = (
                        "manual-target-active"
                        if generation == "target-active-terminal"
                        else "manual-current-restored"
                    )
                    self.assertEqual(
                        target["terminal_result"]["outcome"],
                        expected_durable,
                    )
                activation = deployment.ActivationRequest(
                    deployment=request,
                    authorization_raw=authorization_raw,
                    stage_receipt=stage_root / "stage.json",
                )
                target_accepted = generation != "current-restored-terminal"
                adapter = ManualRecoveryPopenAdapter(
                    chain,
                    prepared,
                    deployment.subprocess.Popen,
                    target_accepted=target_accepted,
                    current_accepted=True,
                )
                with (
                    mock.patch.object(
                        deployment,
                        "_spawn_activation_smoke_child",
                        side_effect=AssertionError(
                            "outer/live controller executed recovery"
                        ),
                    ),
                    mock.patch.object(
                        deployment.subprocess,
                        "Popen",
                        side_effect=adapter,
                    ),
                ):
                    result = deployment.recover_transaction(
                        deployment.RecoveryRequest(
                            activation=activation,
                            expected_journal_raw=live_raw,
                        )
                    )
                expected_public = (
                    "restored-prior"
                    if generation == "current-restored-terminal"
                    else "candidate-active"
                )
                self.assertEqual(result.outcome, expected_public)
                self.assertEqual(
                    adapter.markers,
                    ["A"] if generation == "mixed-candidate-control" else [],
                )
                if generation != "mixed-candidate-control":
                    self.assertEqual(result.journal_raw, journal_cut.target_raw)
                assert_no_transaction_residue(chain.canonical_root)


if __name__ == "__main__":
    unittest.main()
