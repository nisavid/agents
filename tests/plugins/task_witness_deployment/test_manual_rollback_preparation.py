from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ._manual_rollback_support import (
    activate_routine_a_b_c,
    boundary_state,
    endpoint_identity_from_receipt,
    endpoint_identity_observation,
)
from ._support import sha256


class ManualRollbackPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_prepare_c_to_a_displays_exact_target_and_writes_nothing(self) -> None:
        chain = activate_routine_a_b_c(self.root)
        deployment = chain.deployment
        before = boundary_state(chain)

        try:
            request = deployment.RollbackToRequest(
                canonical_root=chain.canonical_root,
                expected_active_receipt_sha256=chain.receipt_c_sha256,
                target_receipt_sha256=chain.receipt_a_sha256,
                maintenance_transaction_sha256="d" * 64,
            )
            prepared = deployment.prepare_rollback_to(request)
        finally:
            self.assertEqual(boundary_state(chain), before)

        self.assertEqual(
            endpoint_identity_observation(prepared.current),
            endpoint_identity_from_receipt(chain.receipt_c_raw),
        )
        self.assertEqual(
            endpoint_identity_observation(prepared.target),
            endpoint_identity_from_receipt(chain.receipt_a_raw),
        )
        self.assertEqual(
            (
                prepared.authorization_facts.canonical_root,
                prepared.authorization_facts.effective_uid,
                prepared.authorization_facts.plan_sha256,
                prepared.authorization_facts.maintenance_transaction_sha256,
                prepared.authorization_facts.expected_active_receipt_sha256,
                prepared.authorization_facts.target_receipt_sha256,
            ),
            (
                chain.canonical_root,
                before["effective_uid"],
                prepared.plan.plan_sha256,
                "d" * 64,
                chain.receipt_c_sha256,
                chain.receipt_a_sha256,
            ),
        )
        self.assertEqual(
            (
                prepared.plan.value["operation"],
                prepared.plan.classification.outcome,
                prepared.plan.classification.reason,
                prepared.plan.value["target_active_sha256"],
                prepared.plan.value["target_successor_receipt_sha256"],
                prepared.plan.value["target_successor_rollback_sha256"],
                prepared.plan.value["target_control_replacement"],
            ),
            (
                "manual-exact-target-rollback",
                "approval-required",
                "explicit-manual-rollback-target",
                sha256(chain.active_a_raw),
                chain.receipt_b_sha256,
                sha256(chain.staged_b.rollback_raw),
                False,
            ),
        )

    def test_prepare_rejects_missing_target_selector_authority_without_writes(
        self,
    ) -> None:
        chain = activate_routine_a_b_c(self.root)
        deployment = chain.deployment
        target_active = next(
            item
            for item in chain.staged_b.artifacts
            if item.role == "prior-active-record"
        ).staged_path
        target_active.unlink()
        before = boundary_state(chain)
        request = deployment.RollbackToRequest(
            canonical_root=chain.canonical_root,
            expected_active_receipt_sha256=chain.receipt_c_sha256,
            target_receipt_sha256=chain.receipt_a_sha256,
            maintenance_transaction_sha256="d" * 64,
        )

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "retained active rollback selector preimage.*cannot be resolved",
        ):
            deployment.prepare_rollback_to(request)

        self.assertEqual(boundary_state(chain), before)


if __name__ == "__main__":
    unittest.main()
