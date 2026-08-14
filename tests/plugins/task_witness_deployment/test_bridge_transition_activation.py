from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest import mock

from ._bridge_transition_activation_support import (
    BRIDGE_JOURNAL_KEYS,
    BridgeTransitionActivationFixture,
    BridgeTransitionSmokeBoundary,
    assert_bridge_journal,
    assert_bridge_migration_receipt,
    deployment_receipt_chain,
    rebind_bridge_activation,
    transaction_result_inventory,
)
from ._control_maintenance_activation_support import (
    CONTROL_MAINTENANCE_REPLACE_PROCESS_LOSS_EXIT,
    PreparedControlMaintenanceActivation,
    assert_candidate_control_set_installed,
    assert_prior_control_set_installed,
    run_control_maintenance_activation_replace_process_loss,
)
from ._control_maintenance_support import (
    MAINTENANCE_REPLACEMENT_ROLES,
    ControlMaintenanceFixture,
    tree_state_with_result,
)
from ._freeze5_upgrade_recovery_support import (
    FREEZE5_CLIENT_SHA256,
    load_controller,
    remove_loaded_controller,
)
from ._routine_activation_support import (
    assert_no_transaction_residue,
    receipt_digest_inventory,
    regular_file_snapshot,
    selector_raws,
)
from ._routine_staged_client_support import installed_client_smoke_process
from ._source_recovery_support import InstalledRecoveryClientProcess
from ._support import set_agent_plugins_candidate_version, sha256
from .test_transaction_result_reconciliation import _run_post_unlink_process_loss


class BridgeTransitionActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.fixture = BridgeTransitionActivationFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_public_bridge_activation_commits_exact_tw4_transition(self) -> None:
        prepared = self.fixture.staged_activation()
        self.addCleanup(self.fixture.close, prepared)
        deployment = prepared.authorized.outbound.deployment
        staged = prepared.staged
        root = prepared.initial.canonical_root
        stage_before = self.fixture.staging.preparation.freeze5.routine.stage_snapshot(
            staged.stage_path
        )
        prior_chain = deployment_receipt_chain(root)
        prior_results = transaction_result_inventory(root)
        smoke = BridgeTransitionSmokeBoundary(
            prepared,
            candidate_accepted=True,
        )
        replacements: list[tuple[str, int, str]] = []
        replace_control = deployment._replace_control_maintenance_artifact

        def observe_replace(*args, **kwargs):
            result = replace_control(*args, **kwargs)
            replacement = kwargs["replacement"]
            replacements.append(
                (kwargs["direction"], kwargs["index"], replacement.role)
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
        ):
            result = deployment.activate_staged(prepared.activation)

        candidate_sha256 = sha256(staged.deployment_raw)
        self.assertEqual(result.outcome, "candidate-active")
        self.assertEqual(result.candidate_receipt_sha256, candidate_sha256)
        self.assertEqual(result.active_receipt_sha256, candidate_sha256)
        self.assertEqual(
            result.accepted_envelope_sha256,
            sha256(smoke.candidate_output),
        )
        self.assertEqual(frozenset(result.journal_value), BRIDGE_JOURNAL_KEYS)
        assert_bridge_journal(dict(result.journal_value), prepared)
        self.assertEqual(result.journal_value["phase"], "terminal")
        self.assertEqual(smoke.targets, ["candidate"])
        self.assertEqual(
            replacements,
            [
                ("candidate", index, role)
                for index, role in enumerate(MAINTENANCE_REPLACEMENT_ROLES)
            ],
        )
        self.assertEqual(
            deployment_receipt_chain(root),
            (*prior_chain, (3, candidate_sha256)),
        )
        self.assertEqual(
            transaction_result_inventory(root),
            tuple(
                sorted(
                    (
                        *prior_results,
                        (
                            (
                                "transaction-results/"
                                f"sha256-{result.transaction_id}.json"
                            ),
                            sha256(result.journal_raw),
                        ),
                    )
                )
            ),
        )
        self.assertEqual((root / "deployment.json").read_bytes(), staged.deployment_raw)
        assert_bridge_migration_receipt(
            (root / "deployment.json").read_bytes(),
            prepared.authorized,
        )
        assert_candidate_control_set_installed(staged)
        assert_no_transaction_residue(root)
        self.assertEqual(
            self.fixture.staging.preparation.freeze5.routine.stage_snapshot(
                staged.stage_path
            ),
            stage_before,
        )
        ControlMaintenanceFixture.assert_private_stage(self, staged.stage_path.parent)
        self.assertFalse(prepared.authorized.outbound.tw4_candidate_root.exists())
        self.assertFalse(prepared.authorized.outbound.release_manifest_path.exists())
        self.assertFalse(prepared.authorized.transition_authorization_path.exists())

    def test_public_bridge_candidate_runs_installed_client_over_mixed_receipts(
        self,
    ) -> None:
        prepared = self.fixture.staged_activation()
        self.addCleanup(self.fixture.close, prepared)
        deployment = prepared.authorized.outbound.deployment
        root = prepared.initial.canonical_root

        with installed_client_smoke_process(
            deployment,
            root,
            self.root / "bridge-installed-client-support",
        ) as smoke:
            result = deployment.activate_staged(prepared.activation)

        self.assertEqual(result.outcome, "candidate-active")
        self.assertEqual(smoke.phases, ["candidate-smoke"])
        self.assertEqual(smoke.calls[0].completed.returncode, 0)
        self.assertEqual(smoke.calls[0].completed.stderr, b"")
        self.assertEqual(smoke.calls[0].filesystem_mutations, ())

    def test_public_bridge_process_loss_recovers_through_exact_staged_b1(
        self,
    ) -> None:
        def replace_controller(candidate: Path) -> None:
            controller = candidate / "controller" / "task_witness_deploy.py"
            controller.write_bytes(
                controller.read_bytes()
                + b"\n# test-only bridge recovery successor controller\n"
            )

        prepared = self.fixture.staged_activation(
            candidate_mutator=replace_controller,
        )
        self.addCleanup(self.fixture.close, prepared)
        deployment = prepared.authorized.outbound.deployment
        root = prepared.initial.canonical_root
        boundary = PreparedControlMaintenanceActivation(
            initial=prepared.initial,
            active=object(),
            candidate=prepared.detached_candidate_root,
            request=prepared.activation.deployment,
            prepared=prepared.authorized.prepared,
            authorization_raw=prepared.activation.authorization_raw,
            staged=prepared.staged,
            activation=prepared.activation,
        )
        stage_before = regular_file_snapshot(prepared.staged.stage_path.parent)

        process_loss = run_control_maintenance_activation_replace_process_loss(
            deployment,
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
        journal_raw = (root / "transaction.json").read_bytes()
        journal = json.loads(journal_raw)
        self.assertEqual(journal["transaction_class"], "control-set-maintenance")
        self.assertEqual(
            journal["pending_step"],
            {
                "operation": "replace-control",
                "index": 0,
                "role": "controller",
            },
        )
        assert_bridge_journal(journal, prepared)

        installed = load_controller(
            root / "controller" / "task_witness_deploy.py",
            "_task_witness_installed_tw4_bridge_recovery",
        )
        rebound_activation = rebind_bridge_activation(installed, prepared)
        client = InstalledRecoveryClientProcess(
            installed,
            root,
            self.root / "bridge-recovery-client-support",
        )
        executor_observations: list[tuple[str, Path, str]] = []

        def observe_executor(*args: object, **kwargs: object) -> object:
            frame = inspect.currentframe()
            try:
                caller = frame.f_back if frame is not None else None
                while caller is not None:
                    if caller.f_code.co_name == "_spawn_activation_smoke_child":
                        module_file = Path(str(caller.f_globals.get("__file__")))
                        executor_observations.append(
                            (
                                str(caller.f_globals.get("__name__")),
                                module_file,
                                sha256(module_file.read_bytes()),
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
                        activation=rebound_activation,
                        expected_journal_raw=journal_raw,
                    )
                )
        finally:
            remove_loaded_controller(installed)

        self.assertEqual(result.outcome, "candidate-active")
        self.assertEqual(
            result.active_receipt_sha256,
            sha256(prepared.staged.deployment_raw),
        )
        self.assertEqual(client.phases, ["candidate-smoke"])
        self.assertEqual(bytes(client.output["stderr"]), b"")
        self.assertEqual(len(executor_observations), 1)
        module_name, module_file, module_sha256 = executor_observations[0]
        self.assertRegex(
            module_name,
            r"\A_task_witness_control_recovery_[0-9a-f]{64}\Z",
        )
        self.assertEqual(
            module_file,
            prepared.staged.stage_path.parent
            / "preimage"
            / "controller"
            / "task_witness_deploy.py",
        )
        self.assertEqual(
            module_sha256,
            prepared.staged.rollback_value["prior_activation_unit"]["control_set"][
                "controller"
            ]["sha256"],
        )
        self.assertFalse(prepared.authorized.outbound.tw4_candidate_root.exists())
        self.assertFalse(prepared.authorized.outbound.release_manifest_path.exists())
        self.assertFalse(prepared.authorized.transition_authorization_path.exists())
        self.assertEqual(
            regular_file_snapshot(prepared.staged.stage_path.parent),
            stage_before,
        )
        assert_no_transaction_residue(root)

    def test_public_bridge_reconciles_retained_result_through_exact_staged_b1(
        self,
    ) -> None:
        def replace_controller(candidate: Path) -> None:
            controller = candidate / "controller" / "task_witness_deploy.py"
            controller.write_bytes(
                controller.read_bytes()
                + b"\n# test-only bridge reconciliation successor controller\n"
            )

        prepared = self.fixture.staged_activation(
            candidate_mutator=replace_controller,
        )
        self.addCleanup(self.fixture.close, prepared)
        deployment = prepared.authorized.outbound.deployment
        root = prepared.initial.canonical_root
        stage_before = regular_file_snapshot(prepared.staged.stage_path.parent)
        smoke = BridgeTransitionSmokeBoundary(prepared, candidate_accepted=True)
        terminal_raw = _run_post_unlink_process_loss(
            deployment,
            prepared.activation,
            smoke,
        )
        terminal = json.loads(terminal_raw)
        self.assertEqual(terminal["terminal_result"]["outcome"], "candidate-active")
        self.assertFalse((root / "transaction.json").exists())

        installed = load_controller(
            root / "controller" / "task_witness_deploy.py",
            "_task_witness_installed_tw4_bridge_reconciliation",
        )
        rebound_activation = rebind_bridge_activation(installed, prepared)
        reconciliation_modules: list[object] = []
        reconciliation_calls: list[tuple[bytes, bytes, str]] = []

        class ObservedReconciliationModule(ModuleType):
            def __getattribute__(self, name: str) -> object:
                value = super().__getattribute__(name)
                if name != "reconcile_transaction_result" or not callable(value):
                    return value

                def observe_public_reconciliation(request: object) -> object:
                    reconciled = value(request)
                    reconciliation_calls.append(
                        (
                            bytes(request.expected_terminal_journal_raw),
                            bytes(reconciled.journal_raw),
                            str(reconciled.active_receipt_sha256),
                        )
                    )
                    return reconciled

                return observe_public_reconciliation

        def observe_module(name: str) -> object:
            module = ObservedReconciliationModule(name)
            reconciliation_modules.append(module)
            return module

        try:
            with mock.patch.object(
                installed,
                "ModuleType",
                side_effect=observe_module,
            ):
                result = installed.reconcile_transaction_result(
                    installed.ResultReconciliationRequest(
                        activation=rebound_activation,
                        expected_terminal_journal_raw=terminal_raw,
                    )
                )
        finally:
            remove_loaded_controller(installed)

        self.assertEqual(result.outcome, "candidate-active")
        self.assertEqual(result.journal_raw, terminal_raw)
        self.assertEqual(
            result.active_receipt_sha256,
            sha256(prepared.staged.deployment_raw),
        )
        self.assertEqual(len(reconciliation_modules), 1)
        self.assertEqual(
            reconciliation_calls,
            [
                (
                    terminal_raw,
                    terminal_raw,
                    sha256(prepared.staged.deployment_raw),
                )
            ],
        )
        reconciliation_module = reconciliation_modules[0]
        self.assertRegex(
            reconciliation_module.__name__,
            r"\A_task_witness_result_reconciliation_[0-9a-f]{64}\Z",
        )
        self.assertEqual(
            Path(reconciliation_module.__file__),
            prepared.staged.stage_path.parent
            / "preimage"
            / "controller"
            / "task_witness_deploy.py",
        )
        self.assertEqual(
            sha256(Path(reconciliation_module.__file__).read_bytes()),
            prepared.staged.rollback_value["prior_activation_unit"]["control_set"][
                "controller"
            ]["sha256"],
        )
        self.assertFalse(prepared.authorized.outbound.tw4_candidate_root.exists())
        self.assertFalse(prepared.authorized.outbound.release_manifest_path.exists())
        self.assertFalse(prepared.authorized.transition_authorization_path.exists())
        self.assertEqual(
            regular_file_snapshot(prepared.staged.stage_path.parent),
            stage_before,
        )
        assert_no_transaction_residue(root)

    def test_public_bridge_rejection_restores_through_installed_clients(self) -> None:
        def reject_candidate(candidate: Path) -> None:
            entrypoint = candidate / "runtime" / "task_witness.py"
            entrypoint.write_bytes(
                entrypoint.read_bytes()
                + b"\n\ndef validate_bundle(*_args, **_kwargs):\n"
                + b'    raise EvidenceError("test-only candidate rejection")\n'
            )

        prepared = self.fixture.staged_activation(
            candidate_mutator=reject_candidate,
        )
        self.addCleanup(self.fixture.close, prepared)
        deployment = prepared.authorized.outbound.deployment
        root = prepared.initial.canonical_root
        bridge_client_raw = prepared.staged.rollback_value["prior_activation_unit"][
            "control_set"
        ]["client"]
        self.assertNotEqual(bridge_client_raw["sha256"], FREEZE5_CLIENT_SHA256)
        self.assertNotEqual(
            bridge_client_raw["sha256"],
            prepared.staged.deployment_value["control_set"]["client"]["sha256"],
        )
        self.assertIn(
            b'CLIENT_RELEASE_PROFILE = "b1-transition"',
            (
                prepared.staged.stage_path.parent
                / "preimage"
                / "client"
                / "task_witness_client.py"
            ).read_bytes(),
        )
        stage_before = self.fixture.staging.preparation.freeze5.routine.stage_snapshot(
            prepared.staged.stage_path
        )

        with installed_client_smoke_process(
            deployment,
            root,
            self.root / "bridge-rejection-installed-client-support",
        ) as smoke:
            result = deployment.activate_staged(prepared.activation)

        self.assertEqual(
            result.outcome,
            "restored-prior",
            [
                (
                    call.observation.phase,
                    call.completed.returncode,
                    call.completed.stderr,
                )
                for call in smoke.calls
            ],
        )
        self.assertEqual(smoke.phases, ["candidate-smoke", "rollback-smoke"])
        self.assertEqual(smoke.calls[0].completed.returncode, 65)
        self.assertEqual(smoke.calls[1].completed.returncode, 0)
        self.assertEqual(
            smoke.calls[0].observation.journal["transaction_id"],
            smoke.calls[1].observation.journal["transaction_id"],
        )
        self.assertEqual(
            smoke.calls[0].observation.journal["bridge_transition"],
            smoke.calls[1].observation.journal["bridge_transition"],
        )
        self.assertEqual(smoke.calls[0].filesystem_mutations, ())
        self.assertEqual(smoke.calls[1].filesystem_mutations, ())
        assert_no_transaction_residue(root)
        self.assertEqual(
            self.fixture.staging.preparation.freeze5.routine.stage_snapshot(
                prepared.staged.stage_path
            ),
            stage_before,
        )

    def test_public_bridge_success_supports_next_ordinary_precondition_capture(
        self,
    ) -> None:
        prepared = self.fixture.staged_activation()
        self.addCleanup(self.fixture.close, prepared)
        deployment = prepared.authorized.outbound.deployment
        root = prepared.initial.canonical_root
        smoke = BridgeTransitionSmokeBoundary(prepared, candidate_accepted=True)
        with mock.patch.object(deployment, "_spawn_activation_smoke_child", smoke):
            result = deployment.activate_staged(prepared.activation)

        candidate = self.fixture.staging.preparation.freeze5.routine.candidate_root()
        set_agent_plugins_candidate_version(candidate, "1.0.1")
        fixture_request = (
            self.fixture.staging.preparation.freeze5.routine.request_for_candidate(
                root,
                result.active_receipt_sha256,
                candidate,
                release_version="1.0.1",
                revision="d" * 40,
                sequence=10,
            )
        )
        request = deployment.DeploymentRequest(
            candidate_root=fixture_request.candidate_root,
            canonical_root=fixture_request.canonical_root,
            source_selection_raw=fixture_request.source_selection_raw,
            source_evidence=deployment.HarnessSnapshotEvidence(
                binding_raw=fixture_request.source_evidence.binding_raw,
                receipt_raw=fixture_request.source_evidence.receipt_raw,
            ),
            runtime_qualification_raw=fixture_request.runtime_qualification_raw,
            maintenance_transaction_sha256=(
                fixture_request.maintenance_transaction_sha256
            ),
            expected_active_receipt_sha256=(
                fixture_request.expected_active_receipt_sha256
            ),
        )
        next_prepared = deployment.prepare_deployment(request)

        captured = next_prepared.plan.precondition
        self.assertEqual(captured.receipt_sha256, result.active_receipt_sha256)
        self.assertEqual(
            (
                captured.receipt_value["schema_version"],
                captured.receipt_value["contract"],
            ),
            (2, "task-witness-deployment-receipt-v2"),
        )
        self.assertIn("migration", captured.receipt_value)
        self.assertEqual(
            tuple(
                (value["sequence"], value["schema_version"], value["contract"])
                for _, value, _ in captured.retained_chain.deployment_receipts
            ),
            (
                (3, 2, "task-witness-deployment-receipt-v2"),
                (2, 1, "task-witness-deployment-receipt-v1"),
                (1, 1, "task-witness-deployment-receipt-v1"),
            ),
        )

    def test_public_bridge_rejects_cross_contract_manual_rollback_before_writes(
        self,
    ) -> None:
        prepared = self.fixture.staged_activation()
        self.addCleanup(self.fixture.close, prepared)
        deployment = prepared.authorized.outbound.deployment
        root = prepared.initial.canonical_root
        smoke = BridgeTransitionSmokeBoundary(prepared, candidate_accepted=True)
        with mock.patch.object(deployment, "_spawn_activation_smoke_child", smoke):
            result = deployment.activate_staged(prepared.activation)
        before = ControlMaintenanceFixture.tree_state(root)

        request = deployment.RollbackToRequest(
            canonical_root=root,
            expected_active_receipt_sha256=result.active_receipt_sha256,
            target_receipt_sha256=(
                prepared.authorized.outbound.starting_active_receipt_sha256
            ),
            maintenance_transaction_sha256="e" * 64,
        )
        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "manual rollback across receipt contracts is unsupported",
        ):
            deployment.prepare_rollback_to(request)

        self.assertEqual(ControlMaintenanceFixture.tree_state(root), before)

    def test_public_bridge_target_rejection_restores_exact_current_b1(self) -> None:
        prepared = self.fixture.staged_activation()
        self.addCleanup(self.fixture.close, prepared)
        deployment = prepared.authorized.outbound.deployment
        staged = prepared.staged
        root = prepared.initial.canonical_root
        root_before = ControlMaintenanceFixture.tree_state(root)
        stage_before = self.fixture.staging.preparation.freeze5.routine.stage_snapshot(
            staged.stage_path
        )
        selectors_before = selector_raws(root)
        receipts_before = receipt_digest_inventory(root)
        chain_before = deployment_receipt_chain(root)
        results_before = transaction_result_inventory(root)
        smoke = BridgeTransitionSmokeBoundary(
            prepared,
            candidate_accepted=False,
        )
        replacements: list[tuple[str, int, str]] = []
        replace_control = deployment._replace_control_maintenance_artifact

        def observe_replace(*args, **kwargs):
            result = replace_control(*args, **kwargs)
            replacement = kwargs["replacement"]
            replacements.append(
                (kwargs["direction"], kwargs["index"], replacement.role)
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
        ):
            result = deployment.activate_staged(prepared.activation)

        candidate_sha256 = sha256(staged.deployment_raw)
        self.assertEqual(result.outcome, "restored-prior")
        self.assertEqual(result.candidate_receipt_sha256, candidate_sha256)
        self.assertEqual(
            result.active_receipt_sha256,
            prepared.authorized.outbound.starting_active_receipt_sha256,
        )
        self.assertEqual(result.accepted_envelope_sha256, sha256(smoke.current_output))
        self.assertEqual(frozenset(result.journal_value), BRIDGE_JOURNAL_KEYS)
        assert_bridge_journal(dict(result.journal_value), prepared)
        self.assertEqual(
            result.journal_value["terminal_result"]["failure_class"],
            "candidate-smoke-rejected",
        )
        self.assertEqual(smoke.targets, ["candidate", "current"])
        self.assertEqual(
            replacements,
            [
                (direction, index, role)
                for direction in ("candidate", "prior")
                for index, role in enumerate(MAINTENANCE_REPLACEMENT_ROLES)
            ],
        )
        self.assertEqual(selector_raws(root), selectors_before)
        self.assertEqual(receipt_digest_inventory(root), receipts_before)
        self.assertEqual(deployment_receipt_chain(root), chain_before)
        self.assertEqual(
            transaction_result_inventory(root),
            tuple(
                sorted(
                    (
                        *results_before,
                        (
                            (
                                "transaction-results/"
                                f"sha256-{result.transaction_id}.json"
                            ),
                            sha256(result.journal_raw),
                        ),
                    )
                )
            ),
        )
        self.assertEqual(
            ControlMaintenanceFixture.tree_state(root),
            tree_state_with_result(root_before, result),
        )
        self.assertNotIn(
            "migration",
            json.loads((root / "deployment.json").read_bytes()),
        )
        assert_prior_control_set_installed(staged)
        assert_no_transaction_residue(root)
        self.assertEqual(
            self.fixture.staging.preparation.freeze5.routine.stage_snapshot(
                staged.stage_path
            ),
            stage_before,
        )
        ControlMaintenanceFixture.assert_private_stage(self, staged.stage_path.parent)
