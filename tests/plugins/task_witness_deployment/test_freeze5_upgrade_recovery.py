from __future__ import annotations

import dataclasses
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ._activation_support import (
    expected_smoke_envelope,
)
from ._control_maintenance_activation_support import (
    CONTROL_MAINTENANCE_REPLACE_PROCESS_LOSS_EXIT,
    AcceptedControlMaintenanceSmoke,
    PreparedControlMaintenanceActivation,
    RejectCandidateAcceptPriorControlMaintenanceSmoke,
    assert_candidate_control_set_installed,
    assert_prior_control_set_installed,
    run_control_maintenance_activation_replace_process_loss,
)
from ._freeze5_upgrade_recovery_support import (
    FREEZE5_CLIENT_SHA256,
    FREEZE5_CONTROLLER_SHA256,
    FREEZE5_POLICY_SHA256,
    Freeze5UpgradeRecoveryFixture,
    detach_candidate,
    load_controller,
    remove_loaded_controller,
    set_resealed_client_profile,
)
from ._routine_activation_support import (
    assert_no_transaction_residue,
    exact_live_journal,
    regular_file_snapshot,
    staged_artifact,
)
from ._routine_staged_client_support import installed_client_smoke_process
from ._source_recovery_support import InstalledRecoveryClientProcess
from ._support import canonical_document, content_document, sha256
from .test_transaction_result_reconciliation import (
    _run_post_unlink_process_loss,
)


class Freeze5UpgradeRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.fixture = Freeze5UpgradeRecoveryFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_direct_freeze5_to_tw4_rejects_before_stage_or_live_mutation(
        self,
    ) -> None:
        attempt = self.fixture.direct_current_upgrade_request()
        before = regular_file_snapshot(self.root)
        controller = attempt.prior_candidate / "controller" / "task_witness_deploy.py"
        freeze5 = load_controller(controller, "_task_witness_freeze5_direct_reject")
        try:
            evidence = attempt.request.source_evidence
            request = freeze5.DeploymentRequest(
                candidate_root=freeze5.Path(str(attempt.candidate)),
                canonical_root=freeze5.Path(str(attempt.initial.canonical_root)),
                source_selection_raw=bytes(attempt.request.source_selection_raw),
                manager_binding_raw=bytes(evidence.binding_raw),
                manager_receipt_raw=bytes(evidence.receipt_raw),
                runtime_qualification_raw=bytes(
                    attempt.request.runtime_qualification_raw
                ),
                maintenance_transaction_sha256=(
                    attempt.request.maintenance_transaction_sha256
                ),
                expected_active_receipt_sha256=(
                    attempt.request.expected_active_receipt_sha256
                ),
            )

            with self.assertRaisesRegex(
                freeze5.DeploymentError,
                "candidate source is missing the Codex manifest",
            ):
                freeze5.prepare_deployment(request)
        finally:
            remove_loaded_controller(freeze5)

        self.assertEqual(regular_file_snapshot(self.root), before)
        self.assertFalse((self.root / "freeze5-upgrade-stage").exists())

    def test_public_bridge_transition_request_has_exact_closed_surface(self) -> None:
        deployment = self.fixture.deployment

        self.assertEqual(
            tuple(
                field.name
                for field in dataclasses.fields(deployment.BridgeTransitionRequest)
            ),
            (
                "deployment",
                "release_manifest_path",
                "endpoint_projection_raw",
                "execution_class",
            ),
        )

    def test_bridge_preparation_rejects_non_bridge_predecessor_without_writes(
        self,
    ) -> None:
        deployment = self.fixture.deployment
        initial, active = self.fixture.routine.activate_initial()
        candidate = self.fixture.routine.candidate_root()
        ordinary = self.fixture.routine.request_for_candidate(
            initial.canonical_root,
            active.active_receipt_sha256,
            candidate,
            release_version="1.0.1",
            revision="b" * 40,
            sequence=8,
        )
        manifest = self.root / "detached-release-manifest.json"
        manifest.write_bytes(canonical_document(content_document({"fixture": True})))
        manifest.chmod(0o600)
        bridge = deployment.BridgeTransitionRequest(
            deployment=ordinary,
            release_manifest_path=manifest,
            endpoint_projection_raw=canonical_document(
                content_document({"fixture": "endpoint"})
            ),
            execution_class="isolated-rehearsal",
        )
        staging_root = self.root / "bridge-stage"
        before = regular_file_snapshot(self.root)

        with (
            mock.patch.object(
                deployment,
                "_snapshot_candidate_tree",
                side_effect=AssertionError("candidate must remain uninspected"),
            ),
            self.assertRaisesRegex(
                deployment.DeploymentError,
                "bridge transition predecessor is not exact B1",
            ),
        ):
            deployment.prepare_bridge_transition(bridge, staging_root)

        self.assertEqual(regular_file_snapshot(self.root), before)
        self.assertFalse(staging_root.exists())

    def test_exact_freeze5_installs_bridge_shape_through_existing_program(
        self,
    ) -> None:
        prepared = self.fixture.prepared_first_hop()
        deployment = prepared.deployment
        staged = prepared.staged
        try:
            with installed_client_smoke_process(
                deployment,
                prepared.initial.canonical_root,
                self.root / "freeze5-bridge-installed-client-support",
            ) as smoke:
                result = deployment.activate_staged(prepared.activation)
        finally:
            remove_loaded_controller(deployment)

        self.assertEqual(
            prepared.prepared.plan.value["operation"],
            "complete-control-set-maintenance",
        )
        self.assertEqual(
            result.outcome,
            "candidate-active",
            [
                (
                    call.observation.phase,
                    call.completed.returncode,
                    call.completed.stderr,
                )
                for call in smoke.calls
            ],
        )
        self.assertEqual(smoke.phases, ["candidate-smoke"])
        self.assertEqual(smoke.calls[0].completed.returncode, 0)
        self.assertEqual(smoke.calls[0].completed.stderr, b"")
        self.assertEqual(smoke.calls[0].filesystem_mutations, ())
        self.assertEqual(
            sha256(staged_artifact(staged, "policy").raw),
            FREEZE5_POLICY_SHA256,
        )
        bridge_client_raw = staged_artifact(staged, "client").raw
        self.assertNotEqual(sha256(bridge_client_raw), FREEZE5_CLIENT_SHA256)
        self.assertIn(
            b'CLIENT_RELEASE_PROFILE = "b1-transition"',
            bridge_client_raw,
        )
        self.assertEqual(
            staged_artifact(staged, "controller").raw,
            (prepared.candidate / "controller" / "task_witness_deploy.py").read_bytes(),
        )
        assert_candidate_control_set_installed(staged)
        assert_no_transaction_residue(prepared.initial.canonical_root)

    def test_exact_freeze5_rejects_modified_bridge_authorization_before_stage(
        self,
    ) -> None:
        prepared = self.fixture.prepared_first_hop()
        deployment = prepared.deployment
        wrong = json.loads(prepared.authorization_raw)
        wrong["candidate_controller_sha256"] = "0" * 64
        wrong.pop("content_sha256")
        wrong_raw = canonical_document(content_document(wrong))
        staging_root = self.root / "modified-bridge-stage"
        before = regular_file_snapshot(self.root)
        try:
            with self.assertRaisesRegex(
                deployment.DeploymentError,
                "control-set maintenance authorization facts disagree",
            ):
                deployment.stage_deployment(
                    prepared.request,
                    wrong_raw,
                    deployment.Path(str(staging_root)),
                )
        finally:
            remove_loaded_controller(deployment)

        self.assertEqual(regular_file_snapshot(self.root), before)
        self.assertFalse(staging_root.exists())

    def test_first_hop_recovery_executes_exact_staged_freeze5_without_candidate(
        self,
    ) -> None:
        prepared = self.fixture.prepared_first_hop()
        freeze5 = prepared.deployment
        boundary = PreparedControlMaintenanceActivation(
            prepared.initial,
            prepared.active,
            prepared.candidate,
            prepared.request,
            prepared.prepared,
            prepared.authorization_raw,
            prepared.staged,
            prepared.activation,
        )
        process_loss = run_control_maintenance_activation_replace_process_loss(
            freeze5,
            boundary,
            direction="candidate",
            replacement_index=0,
        )
        self.assertEqual(
            process_loss.exit_status,
            CONTROL_MAINTENANCE_REPLACE_PROCESS_LOSS_EXIT,
            process_loss.diagnostic,
        )
        self.assertEqual(process_loss.diagnostic, "")
        journal_raw = exact_live_journal(prepared.initial.canonical_root)
        journal = json.loads(journal_raw)
        self.assertEqual(journal["phase"], "control-switching")
        self.assertEqual(
            journal["pending_step"],
            {
                "operation": "replace-control",
                "index": 0,
                "role": "controller",
            },
        )
        stage_snapshot = regular_file_snapshot(prepared.staged.stage_path.parent)
        candidate_snapshot = regular_file_snapshot(prepared.candidate)
        detached = detach_candidate(prepared.candidate)
        installed = load_controller(
            prepared.initial.canonical_root / "controller" / "task_witness_deploy.py",
            "_task_witness_installed_bridge_recovery",
        )
        evidence = prepared.current_request.source_evidence
        installed_request = installed.DeploymentRequest(
            candidate_root=installed.Path(str(prepared.candidate)),
            canonical_root=installed.Path(str(prepared.initial.canonical_root)),
            source_selection_raw=bytes(prepared.current_request.source_selection_raw),
            source_evidence=installed.HarnessSnapshotEvidence(
                binding_raw=bytes(evidence.binding_raw),
                receipt_raw=bytes(evidence.receipt_raw),
            ),
            runtime_qualification_raw=bytes(
                prepared.current_request.runtime_qualification_raw
            ),
            maintenance_transaction_sha256=str(
                prepared.current_request.maintenance_transaction_sha256
            ),
            expected_active_receipt_sha256=str(
                prepared.current_request.expected_active_receipt_sha256
            ),
        )
        installed_activation = installed.ActivationRequest(
            deployment=installed_request,
            authorization_raw=prepared.authorization_raw,
            stage_receipt=installed.Path(str(prepared.staged.stage_path)),
        )
        client = InstalledRecoveryClientProcess(
            installed,
            prepared.initial.canonical_root,
            self.root / "freeze5-recovery-client-support",
        )
        real_snapshot = installed._snapshot_candidate_tree
        executor_observations: list[tuple[str, str, str]] = []

        def forbid_candidate_snapshot(path: Path) -> object:
            if path == prepared.candidate:
                raise AssertionError("first-hop recovery reread the candidate")
            return real_snapshot(path)

        def observe_executor(*args: object, **kwargs: object) -> object:
            frame = inspect.currentframe()
            try:
                caller = frame.f_back if frame is not None else None
                while caller is not None:
                    if caller.f_code.co_name == "_spawn_activation_smoke_child":
                        module_name = str(caller.f_globals.get("__name__"))
                        module_file = str(caller.f_globals.get("__file__"))
                        executor_observations.append(
                            (
                                module_name,
                                module_file,
                                sha256(Path(module_file).read_bytes()),
                            )
                        )
                        break
                    caller = caller.f_back
            finally:
                del frame
            return client(*args, **kwargs)

        try:
            with (
                mock.patch.object(
                    installed,
                    "_snapshot_candidate_tree",
                    side_effect=forbid_candidate_snapshot,
                ),
                mock.patch.object(
                    installed,
                    "verify_deployment_stage",
                    side_effect=AssertionError(
                        "current stage verifier ran for exact Freeze 5 recovery"
                    ),
                ),
                mock.patch.object(
                    installed.subprocess,
                    "Popen",
                    side_effect=observe_executor,
                ),
                mock.patch.object(
                    installed.os,
                    "read",
                    side_effect=client.observe_read,
                ),
            ):
                result = installed.recover_transaction(
                    installed.RecoveryRequest(
                        activation=installed_activation,
                        expected_journal_raw=journal_raw,
                    )
                )
        finally:
            remove_loaded_controller(installed)
            remove_loaded_controller(freeze5)

        self.assertEqual(result.outcome, "candidate-active")
        self.assertEqual(
            result.active_receipt_sha256,
            sha256(prepared.staged.deployment_raw),
        )
        self.assertEqual(client.phases, ["candidate-smoke"])
        self.assertEqual(
            bytes(client.output["stdout"]),
            expected_smoke_envelope(prepared.staged),
        )
        self.assertEqual(bytes(client.output["stderr"]), b"")
        self.assertEqual(len(executor_observations), 1)
        module_name, module_file, module_sha256 = executor_observations[0]
        self.assertRegex(
            module_name, r"\A_task_witness_control_recovery_[0-9a-f]{64}\Z"
        )
        self.assertEqual(
            Path(module_file),
            prepared.staged.stage_path.parent
            / "preimage"
            / "controller"
            / "task_witness_deploy.py",
        )
        self.assertEqual(module_sha256, FREEZE5_CONTROLLER_SHA256)
        self.assertFalse(prepared.candidate.exists())
        self.assertEqual(regular_file_snapshot(detached), candidate_snapshot)
        self.assertEqual(
            regular_file_snapshot(prepared.staged.stage_path.parent),
            stage_snapshot,
        )
        assert_candidate_control_set_installed(prepared.staged)
        assert_no_transaction_residue(prepared.initial.canonical_root)

    def test_installed_bridge_reconciles_exact_freeze5_candidate_result_after_post_unlink_loss(
        self,
    ) -> None:
        prepared = self.fixture.prepared_first_hop()
        freeze5 = prepared.deployment
        boundary = PreparedControlMaintenanceActivation(
            prepared.initial,
            prepared.active,
            prepared.candidate,
            prepared.request,
            prepared.prepared,
            prepared.authorization_raw,
            prepared.staged,
            prepared.activation,
        )
        terminal_raw = _run_post_unlink_process_loss(
            freeze5,
            prepared.activation,
            AcceptedControlMaintenanceSmoke(boundary),
        )
        terminal = json.loads(terminal_raw)
        retained = (
            prepared.initial.canonical_root
            / "transaction-results"
            / f"sha256-{terminal['transaction_id']}.json"
        )
        self.assertFalse(
            (prepared.initial.canonical_root / "transaction.json").exists()
        )
        self.assertEqual(retained.read_bytes(), terminal_raw)
        stage_snapshot = regular_file_snapshot(prepared.staged.stage_path.parent)
        candidate_snapshot = regular_file_snapshot(prepared.candidate)
        detached = detach_candidate(prepared.candidate)
        installed = load_controller(
            prepared.initial.canonical_root / "controller" / "task_witness_deploy.py",
            "_task_witness_installed_bridge_result_reconciliation",
        )
        evidence = prepared.current_request.source_evidence
        installed_request = installed.DeploymentRequest(
            candidate_root=installed.Path(str(prepared.candidate)),
            canonical_root=installed.Path(str(prepared.initial.canonical_root)),
            source_selection_raw=bytes(prepared.current_request.source_selection_raw),
            source_evidence=installed.HarnessSnapshotEvidence(
                binding_raw=bytes(evidence.binding_raw),
                receipt_raw=bytes(evidence.receipt_raw),
            ),
            runtime_qualification_raw=bytes(
                prepared.current_request.runtime_qualification_raw
            ),
            maintenance_transaction_sha256=str(
                prepared.current_request.maintenance_transaction_sha256
            ),
            expected_active_receipt_sha256=str(
                prepared.current_request.expected_active_receipt_sha256
            ),
        )
        installed_activation = installed.ActivationRequest(
            deployment=installed_request,
            authorization_raw=prepared.authorization_raw,
            stage_receipt=installed.Path(str(prepared.staged.stage_path)),
        )
        real_module_type = installed.ModuleType
        reconciliation_modules: list[object] = []

        def observe_reconciliation_module(name: str) -> object:
            module = real_module_type(name)
            reconciliation_modules.append(module)
            return module

        try:
            with (
                mock.patch.object(
                    installed,
                    "_snapshot_candidate_tree",
                    side_effect=AssertionError(
                        "result reconciliation reread the detached candidate"
                    ),
                ),
                mock.patch.object(
                    installed,
                    "verify_deployment_stage",
                    side_effect=AssertionError(
                        "current stage verifier ran for exact Freeze 5 result reconciliation"
                    ),
                ),
                mock.patch.object(
                    installed,
                    "_recovery_precondition_from_stage",
                    side_effect=AssertionError(
                        "current stage parser ran for exact Freeze 5 result reconciliation"
                    ),
                ),
                mock.patch.object(
                    installed,
                    "ModuleType",
                    side_effect=observe_reconciliation_module,
                ),
            ):
                reconciliation_request = installed.ResultReconciliationRequest(
                    activation=installed_activation,
                    expected_terminal_journal_raw=terminal_raw,
                )
                result = installed.reconcile_transaction_result(reconciliation_request)
                repeated = installed.reconcile_transaction_result(
                    reconciliation_request
                )
        finally:
            remove_loaded_controller(installed)
            remove_loaded_controller(freeze5)

        self.assertEqual(repeated, result)
        self.assertEqual(result.outcome, "candidate-active")
        self.assertEqual(result.journal_raw, terminal_raw)
        self.assertEqual(
            result.active_receipt_sha256,
            sha256(prepared.staged.deployment_raw),
        )
        self.assertFalse(prepared.candidate.exists())
        self.assertEqual(regular_file_snapshot(detached), candidate_snapshot)
        self.assertEqual(
            regular_file_snapshot(prepared.staged.stage_path.parent),
            stage_snapshot,
        )
        self.assertEqual(retained.read_bytes(), terminal_raw)
        self.assertEqual(len(reconciliation_modules), 2)
        for module in reconciliation_modules:
            self.assertRegex(
                module.__name__,
                r"\A_task_witness_result_reconciliation_[0-9a-f]{64}\Z",
            )
            self.assertEqual(
                Path(module.__file__),
                prepared.staged.stage_path.parent
                / "preimage"
                / "controller"
                / "task_witness_deploy.py",
            )
            self.assertEqual(
                sha256(Path(module.__file__).read_bytes()), FREEZE5_CONTROLLER_SHA256
            )
        assert_candidate_control_set_installed(prepared.staged)
        assert_no_transaction_residue(prepared.initial.canonical_root)

    def test_first_hop_recovery_rejects_changed_prior_receipt_before_module_load(
        self,
    ) -> None:
        prepared = self.fixture.prepared_first_hop()
        freeze5 = prepared.deployment
        boundary = PreparedControlMaintenanceActivation(
            prepared.initial,
            prepared.active,
            prepared.candidate,
            prepared.request,
            prepared.prepared,
            prepared.authorization_raw,
            prepared.staged,
            prepared.activation,
        )
        process_loss = run_control_maintenance_activation_replace_process_loss(
            freeze5,
            boundary,
            direction="candidate",
            replacement_index=0,
        )
        self.assertEqual(
            process_loss.exit_status,
            CONTROL_MAINTENANCE_REPLACE_PROCESS_LOSS_EXIT,
            process_loss.diagnostic,
        )
        journal_raw = exact_live_journal(prepared.initial.canonical_root)
        prior_receipt = (
            prepared.staged.stage_path.parent / "preimage" / "deployment.json"
        )
        prior_receipt.write_bytes(b"{}")
        prior_receipt.chmod(0o600)
        before = regular_file_snapshot(self.root)
        current = self.fixture.deployment
        current_activation = current.ActivationRequest(
            deployment=prepared.current_request,
            authorization_raw=prepared.authorization_raw,
            stage_receipt=prepared.staged.stage_path,
        )
        try:
            with (
                mock.patch.object(
                    current,
                    "ModuleType",
                    side_effect=AssertionError(
                        "Freeze 5 module loaded before authority closed"
                    ),
                ),
                self.assertRaisesRegex(
                    current.DeploymentError,
                    "Freeze 5 recovery control preimage bytes disagree",
                ),
            ):
                current.recover_transaction(
                    current.RecoveryRequest(
                        activation=current_activation,
                        expected_journal_raw=journal_raw,
                    )
                )
        finally:
            remove_loaded_controller(freeze5)

        self.assertEqual(regular_file_snapshot(self.root), before)
        self.assertEqual(
            exact_live_journal(prepared.initial.canonical_root),
            journal_raw,
        )

    def test_first_hop_recovery_rejects_resealed_non_b1_client_before_module_load(
        self,
    ) -> None:
        def replace_profile(candidate: Path) -> None:
            set_resealed_client_profile(
                candidate / "client" / "task_witness_client.py",
                "tw4-current",
            )

        prepared = self.fixture.prepared_first_hop(
            candidate_mutator=replace_profile,
        )
        freeze5 = prepared.deployment
        boundary = PreparedControlMaintenanceActivation(
            prepared.initial,
            prepared.active,
            prepared.candidate,
            prepared.request,
            prepared.prepared,
            prepared.authorization_raw,
            prepared.staged,
            prepared.activation,
        )
        process_loss = run_control_maintenance_activation_replace_process_loss(
            freeze5,
            boundary,
            direction="candidate",
            replacement_index=0,
        )
        self.assertEqual(
            process_loss.exit_status,
            CONTROL_MAINTENANCE_REPLACE_PROCESS_LOSS_EXIT,
            process_loss.diagnostic,
        )
        journal_raw = exact_live_journal(prepared.initial.canonical_root)
        before = regular_file_snapshot(self.root)
        current = self.fixture.deployment
        current_activation = current.ActivationRequest(
            deployment=prepared.current_request,
            authorization_raw=prepared.authorization_raw,
            stage_receipt=prepared.staged.stage_path,
        )
        try:
            with (
                mock.patch.object(
                    current,
                    "ModuleType",
                    side_effect=AssertionError(
                        "Freeze 5 module loaded before B1 client profile closed"
                    ),
                ),
                self.assertRaisesRegex(
                    current.DeploymentError,
                    "Freeze 5 recovery candidate client profile disagrees",
                ),
            ):
                current.recover_transaction(
                    current.RecoveryRequest(
                        activation=current_activation,
                        expected_journal_raw=journal_raw,
                    )
                )
        finally:
            remove_loaded_controller(freeze5)

        self.assertEqual(regular_file_snapshot(self.root), before)
        self.assertEqual(
            exact_live_journal(prepared.initial.canonical_root),
            journal_raw,
        )

    def test_first_hop_recovery_rejects_stale_journal_before_module_load(
        self,
    ) -> None:
        prepared = self.fixture.prepared_first_hop()
        freeze5 = prepared.deployment
        boundary = PreparedControlMaintenanceActivation(
            prepared.initial,
            prepared.active,
            prepared.candidate,
            prepared.request,
            prepared.prepared,
            prepared.authorization_raw,
            prepared.staged,
            prepared.activation,
        )
        process_loss = run_control_maintenance_activation_replace_process_loss(
            freeze5,
            boundary,
            direction="candidate",
            replacement_index=0,
        )
        self.assertEqual(
            process_loss.exit_status,
            CONTROL_MAINTENANCE_REPLACE_PROCESS_LOSS_EXIT,
            process_loss.diagnostic,
        )
        journal_raw = exact_live_journal(prepared.initial.canonical_root)
        journal_path = prepared.initial.canonical_root / "transaction.json"
        journal_path.write_bytes(b"{}")
        journal_path.chmod(0o600)
        before = regular_file_snapshot(self.root)
        current = self.fixture.deployment
        current_activation = current.ActivationRequest(
            deployment=prepared.current_request,
            authorization_raw=prepared.authorization_raw,
            stage_receipt=prepared.staged.stage_path,
        )
        try:
            with (
                mock.patch.object(
                    current,
                    "ModuleType",
                    side_effect=AssertionError(
                        "Freeze 5 module loaded for a stale journal"
                    ),
                ),
                self.assertRaisesRegex(
                    current.DeploymentError,
                    "Freeze 5 recovery live journal freshness disagrees",
                ),
            ):
                current.recover_transaction(
                    current.RecoveryRequest(
                        activation=current_activation,
                        expected_journal_raw=journal_raw,
                    )
                )
        finally:
            remove_loaded_controller(freeze5)

        self.assertEqual(regular_file_snapshot(self.root), before)

    def test_exact_freeze5_rejection_restores_only_freeze5(self) -> None:
        prepared = self.fixture.prepared_first_hop()
        freeze5 = prepared.deployment
        boundary = PreparedControlMaintenanceActivation(
            prepared.initial,
            object(),
            prepared.candidate,
            prepared.request,
            prepared.prepared,
            prepared.authorization_raw,
            prepared.staged,
            prepared.activation,
        )
        smoke = RejectCandidateAcceptPriorControlMaintenanceSmoke(boundary)
        original_smoke = freeze5._spawn_activation_smoke_child
        freeze5._spawn_activation_smoke_child = smoke
        try:
            result = freeze5.activate_staged(prepared.activation)
        finally:
            freeze5._spawn_activation_smoke_child = original_smoke
            remove_loaded_controller(freeze5)

        self.assertEqual(result.outcome, "restored-prior")
        self.assertEqual(smoke.phases, ["candidate-smoke", "rollback-smoke"])
        assert_prior_control_set_installed(prepared.staged)
        assert_no_transaction_residue(prepared.initial.canonical_root)


if __name__ == "__main__":
    unittest.main()
