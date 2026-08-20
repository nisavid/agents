from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.plugins.task_witness_client._support import parse_diagnostic

from ._activation_support import filesystem_identity, process_descriptor_inventory
from ._control_maintenance_activation_support import (
    ControlMaintenanceActivationFixture,
    PreparedControlMaintenanceActivation,
    assert_candidate_control_set_installed,
    assert_control_maintenance_additive_set_installed,
    assert_prior_control_set_installed,
    expected_control_maintenance_cleanup,
)
from ._control_maintenance_staged_client_support import (
    installed_control_maintenance_smoke_process,
)
from ._control_maintenance_support import tree_state_with_result
from ._routine_activation_support import (
    assert_no_transaction_residue,
    assert_selector_has_distinct_retained_copy,
    expected_active_receipt_inventory,
    receipt_digest_inventory,
    selector_raws,
    staged_artifact,
    staged_candidate_selector_raws,
    staged_prior_selector_raws,
)
from ._routine_support import smoke_envelope
from ._support import sha256

SEALED_CLIENT_SHA256 = (
    "2fabfdba708ea84c366b480c8a5d7aacf61706ba8e62e9cd97f2afe8dda87e9c"
)
SEALED_LAUNCHER_SHA256 = (
    "4d97e8df695276f8da9cb87ef1c27fa5979c5edf9f338127a0a19ae077d3172b"
)


class ControlMaintenanceStagedClientIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.fixture = ControlMaintenanceActivationFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _assert_installed_client_and_launcher(
        self,
        staged: object,
        *,
        prior: bool,
    ) -> None:
        prefix = "prior-" if prior else ""
        client = staged_artifact(staged, f"{prefix}client")
        launcher = staged_artifact(staged, f"{prefix}launcher")
        self.assertEqual(Path(client.installed["path"]).read_bytes(), client.raw)
        self.assertEqual(sha256(client.raw), SEALED_CLIENT_SHA256)
        self.assertEqual(Path(launcher.installed["path"]).read_bytes(), launcher.raw)
        self.assertEqual(sha256(launcher.raw), SEALED_LAUNCHER_SHA256)

    def test_public_control_maintenance_activation_accepts_b_through_installed_client_and_launcher(
        self,
    ) -> None:
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
        stage_before = self.fixture.control.routine.stage_snapshot(staged.stage_path)
        root_identity = filesystem_identity(root)[:4]
        lock_before = filesystem_identity(prepared.initial.activation_lock)
        descriptors_before = process_descriptor_inventory()
        self._assert_installed_client_and_launcher(staged, prior=True)

        with installed_control_maintenance_smoke_process(
            deployment,
            prepared,
            self.root / "smoke-process-support",
        ) as smoke:
            result = deployment.activate_staged(prepared.activation)

        receipt_b = sha256(staged.deployment_raw)
        candidate_envelope = smoke_envelope(staged.deployment_value["smoke"])
        self.assertEqual(result.outcome, "candidate-active")
        self.assertEqual(result.candidate_receipt_sha256, receipt_b)
        self.assertEqual(result.active_receipt_sha256, receipt_b)
        self.assertEqual(
            result.accepted_envelope_sha256,
            staged.deployment_value["smoke"]["expected_envelope_sha256"],
        )
        self.assertEqual(result.journal_value["phase"], "terminal")
        self.assertEqual(
            result.journal_value["transaction_class"],
            "control-set-maintenance",
        )
        self.assertEqual(smoke.phases, ["candidate-smoke"])
        candidate_call = smoke.calls[0]
        self.assertEqual(candidate_call.completed.returncode, 0)
        self.assertEqual(candidate_call.completed.stdout, candidate_envelope)
        self.assertEqual(candidate_call.completed.stderr, b"")
        self.assertEqual(
            (
                candidate_call.observation.active_raw,
                candidate_call.observation.deployment_raw,
            ),
            selector_b,
        )
        self.assertEqual(
            candidate_call.observation.receipt_digests,
            expected_receipts,
        )
        self.assertEqual(selector_raws(root), selector_b)
        self.assertNotEqual(selector_a, selector_b)
        assert_candidate_control_set_installed(staged)
        self._assert_installed_client_and_launcher(staged, prior=False)
        assert_control_maintenance_additive_set_installed(staged)
        self.assertEqual(receipt_digest_inventory(root), expected_receipts)
        assert_selector_has_distinct_retained_copy(root, staged.deployment_raw)
        assert_no_transaction_residue(root)
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

    def test_public_control_maintenance_activation_rejects_b_and_accepts_restored_a_through_installed_client_and_launcher(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        initial, active, candidate, _ = self.fixture.control.scenario()
        runtime = candidate / "runtime" / "task_witness.py"
        runtime.write_bytes(
            runtime.read_bytes()
            + b"\n\ndef validate_bundle(*_args, **_kwargs):\n"
            + b'    raise EvidenceError("fixture candidate rejects smoke")\n'
        )
        request = self.fixture.control.routine.request_for_candidate(
            initial.canonical_root,
            active.active_receipt_sha256,
            candidate,
            release_version="1.0.1",
            revision="b" * 40,
            sequence=8,
        )
        plan = deployment.prepare_deployment(request)
        authorization_raw = self.fixture.control.authorization_raw(plan)
        staged = deployment.stage_deployment(
            request,
            authorization_raw,
            self.root / "control-maintenance-stage",
        )
        activation = deployment.ActivationRequest(
            deployment=request,
            authorization_raw=authorization_raw,
            stage_receipt=staged.stage_path,
        )
        prepared = PreparedControlMaintenanceActivation(
            initial,
            active,
            candidate,
            request,
            plan,
            authorization_raw,
            staged,
            activation,
        )
        root = initial.canonical_root
        selector_a = selector_raws(root)
        selector_b = staged_candidate_selector_raws(staged)
        prior_receipts = receipt_digest_inventory(root)
        expected_receipts = expected_active_receipt_inventory(
            selector_a[1],
            staged,
        )
        cleanup = expected_control_maintenance_cleanup(staged, root)
        root_before = self.fixture.control.tree_state(root)
        stage_before = self.fixture.control.routine.stage_snapshot(staged.stage_path)
        root_identity = filesystem_identity(root)[:4]
        lock_before = filesystem_identity(initial.activation_lock)
        descriptors_before = process_descriptor_inventory()
        self._assert_installed_client_and_launcher(staged, prior=True)

        with installed_control_maintenance_smoke_process(
            deployment,
            prepared,
            self.root / "smoke-process-support",
        ) as smoke:
            result = deployment.activate_staged(activation)

        receipt_b = sha256(staged.deployment_raw)
        prior_smoke = staged.rollback_value["prior_activation_unit"]["smoke"]
        prior_envelope = smoke_envelope(prior_smoke)
        self.assertEqual(result.outcome, "restored-prior")
        self.assertEqual(result.candidate_receipt_sha256, receipt_b)
        self.assertEqual(result.active_receipt_sha256, active.active_receipt_sha256)
        self.assertEqual(
            result.accepted_envelope_sha256,
            prior_smoke["expected_envelope_sha256"],
        )
        self.assertEqual(result.journal_value["phase"], "terminal")
        self.assertEqual(
            result.journal_value["terminal_result"]["failure_class"],
            "candidate-smoke-rejected",
        )
        self.assertEqual(smoke.phases, ["candidate-smoke", "rollback-smoke"])
        candidate_call, rollback_call = smoke.calls
        self.assertEqual(
            (
                candidate_call.observation.active_raw,
                candidate_call.observation.deployment_raw,
            ),
            selector_b,
        )
        self.assertEqual(
            candidate_call.observation.receipt_digests,
            expected_receipts,
        )
        self.assertEqual(candidate_call.completed.returncode, 65)
        self.assertEqual(candidate_call.completed.stdout, b"")
        diagnostic, fields = parse_diagnostic(candidate_call.completed.stderr)
        self.assertEqual(
            diagnostic,
            "task witness client rejected: launcher rejected validation",
        )
        self.assertEqual(
            fields,
            {
                "validator_code_executed": "unknown",
                "active_state_changed": "unknown",
                "current_receipt": (
                    "sha256:" + staged.deployment_value["content_sha256"][:12]
                ),
                "candidate_receipt": "none",
                "rollback": "not-run",
                "next_action": (
                    "do not retry; inspect the bundle and validator evidence"
                ),
            },
        )
        self.assertEqual(
            (
                rollback_call.observation.active_raw,
                rollback_call.observation.deployment_raw,
            ),
            staged_prior_selector_raws(staged),
        )
        self.assertEqual(
            rollback_call.observation.receipt_digests,
            expected_receipts,
        )
        self.assertEqual(rollback_call.completed.returncode, 0)
        self.assertEqual(rollback_call.completed.stdout, prior_envelope)
        self.assertEqual(rollback_call.completed.stderr, b"")
        self.assertEqual(selector_raws(root), selector_a)
        assert_prior_control_set_installed(staged)
        self._assert_installed_client_and_launcher(staged, prior=True)
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
        self.assertEqual(filesystem_identity(initial.activation_lock), lock_before)
        self.assertEqual(process_descriptor_inventory(), descriptors_before)


if __name__ == "__main__":
    unittest.main()
