from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ._control_maintenance_followup_support import (
    activate_control_maintenance_b,
)


class ControlMaintenanceFollowupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.active_b = activate_control_maintenance_b(self.root)

    def tearDown(self) -> None:
        self.active_b.close()
        self.temporary.cleanup()

    def test_installed_b_prepares_and_stages_followup_routine(self) -> None:
        active_b = self.active_b
        active_b.assert_clean_active_b(self)
        candidate, request = active_b.routine_candidate_and_request()
        self.assertIs(type(request), active_b.deployment.DeploymentRequest)
        stage_root = self.root / "candidate-c-routine-stage"
        canonical_before = active_b.fixture.control.tree_state(active_b.canonical_root)
        candidate_before = active_b.fixture.control.tree_state(candidate)

        try:
            prepared = active_b.deployment.prepare_deployment(request)
        finally:
            self.assertEqual(
                active_b.fixture.control.tree_state(active_b.canonical_root),
                canonical_before,
            )
            self.assertEqual(
                active_b.fixture.control.tree_state(candidate),
                candidate_before,
            )
            self.assertFalse(stage_root.exists())
            active_b.assert_clean_active_b(self)

        self.assertEqual(
            (
                prepared.plan.value["operation"],
                prepared.plan.classification.outcome,
                prepared.plan.classification.reason,
            ),
            ("routine-payload", "compatible-forward", "active-policy"),
        )
        staged = active_b.deployment.stage_deployment(
            request,
            active_b.routine_authorization_raw(prepared),
            stage_root,
        )
        verified = active_b.deployment.verify_deployment_stage(staged.stage_path)
        self.assertEqual(
            (staged.classification.outcome, staged.classification.reason),
            (
                "authorized-routine-payload",
                "active-policy-compatible-forward",
            ),
        )
        self.assertEqual(verified.raw, staged.stage_raw)
        active_b.fixture.control.assert_private_stage(self, stage_root)
        active_b.assert_clean_active_b(self)

    def test_installed_b_prepares_and_stages_followup_control_maintenance(
        self,
    ) -> None:
        active_b = self.active_b
        active_b.assert_clean_active_b(self)
        candidate, request = active_b.control_candidate_and_request()
        self.assertIs(type(request), active_b.deployment.DeploymentRequest)
        stage_root = self.root / "candidate-c-control-stage"
        canonical_before = active_b.fixture.control.tree_state(active_b.canonical_root)
        candidate_before = active_b.fixture.control.tree_state(candidate)

        try:
            prepared = active_b.deployment.prepare_deployment(request)
        finally:
            self.assertEqual(
                active_b.fixture.control.tree_state(active_b.canonical_root),
                canonical_before,
            )
            self.assertEqual(
                active_b.fixture.control.tree_state(candidate),
                candidate_before,
            )
            self.assertFalse(stage_root.exists())
            active_b.assert_clean_active_b(self)

        self.assertEqual(
            (
                prepared.plan.value["operation"],
                prepared.plan.classification.outcome,
                prepared.plan.classification.reason,
            ),
            (
                "complete-control-set-maintenance",
                "approval-required",
                "future-update-policy",
            ),
        )
        staged = active_b.deployment.stage_deployment(
            request,
            active_b.control_authorization_raw(prepared),
            stage_root,
        )
        verified = active_b.deployment.verify_deployment_stage(staged.stage_path)
        self.assertEqual(
            (staged.classification.outcome, staged.classification.reason),
            (
                "authorized-control-set-maintenance",
                "exact-deployer-authorization",
            ),
        )
        self.assertEqual(verified.raw, staged.stage_raw)
        active_b.fixture.control.assert_private_stage(self, stage_root)
        active_b.assert_clean_active_b(self)


if __name__ == "__main__":
    unittest.main()
