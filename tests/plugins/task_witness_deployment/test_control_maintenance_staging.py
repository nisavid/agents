from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from ._control_maintenance_support import (
    CONTROL_PREIMAGE_ROLES,
    CONTROL_ROLES,
    EXACT_PROCESS_PROFILE,
    EXACT_RECEIPT_CLIENT_CONTRACTS,
    EXACT_RECEIPT_CONTRACTS,
    MAINTENANCE_REPLACEMENT_ROLES,
    ControlMaintenanceFixture,
)
from ._support import sha256


class ControlMaintenanceStagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.fixture = ControlMaintenanceFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def assert_prepare_rejected_without_writes(
        self,
        fixture: ControlMaintenanceFixture,
        scenario: tuple[object, object, Path, object],
        message: str,
    ) -> None:
        initial, _, candidate_root, request = scenario
        canonical_root = initial.canonical_root
        canonical_before = fixture.tree_state(canonical_root)
        candidate_before = fixture.tree_state(candidate_root)
        with self.assertRaisesRegex(
            fixture.deployment().DeploymentError,
            message,
        ):
            fixture.deployment().prepare_deployment(request)
        self.assertEqual(fixture.tree_state(canonical_root), canonical_before)
        self.assertEqual(fixture.tree_state(candidate_root), candidate_before)

    def test_public_prepare_and_stage_derive_complete_control_maintenance(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        initial, active, candidate_root, request = self.fixture.scenario()
        stage_root = self.root / "control-maintenance-stage"
        request_fields = {item.name for item in fields(request)}
        self.assertNotIn("operation", request_fields)
        self.assertNotIn("transaction_class", request_fields)

        canonical_before = self.fixture.tree_state(initial.canonical_root)
        candidate_before = self.fixture.tree_state(candidate_root)
        try:
            prepared = deployment.prepare_deployment(request)
        finally:
            self.assertEqual(
                self.fixture.tree_state(initial.canonical_root),
                canonical_before,
            )
            self.assertEqual(self.fixture.tree_state(candidate_root), candidate_before)
            self.assertFalse(stage_root.exists())

        self.assertEqual(
            prepared.plan.value["operation"],
            "complete-control-set-maintenance",
        )
        self.assertEqual(
            prepared.plan.value["maintenance_differences"],
            ("control-set",),
        )
        self.assertEqual(
            (
                prepared.plan.classification.outcome,
                prepared.plan.classification.reason,
            ),
            ("compatible-forward", "active-policy"),
        )
        self.assertEqual(
            prepared.plan.prior_receipt_sha256,
            active.active_receipt_sha256,
        )
        prior_controls = prepared.plan.precondition.receipt_value["control_set"]
        planned_controls = {
            item.role: item
            for item in prepared.plan.artifacts
            if item.role in CONTROL_ROLES
        }
        self.assertEqual(set(planned_controls), set(CONTROL_ROLES))
        self.assertNotEqual(
            planned_controls["controller"].sha256,
            prior_controls["controller"]["sha256"],
        )

        authorization_raw = self.fixture.authorization_raw(prepared)
        authorization = json.loads(authorization_raw)
        self.assertEqual(
            authorization["purpose"],
            "complete-control-set-maintenance",
        )

        staged = deployment.stage_deployment(
            request,
            authorization_raw,
            stage_root,
        )
        self.assertEqual(
            self.fixture.tree_state(initial.canonical_root),
            canonical_before,
        )
        self.assertEqual(self.fixture.tree_state(candidate_root), candidate_before)
        self.assertFalse(stage_root.is_relative_to(initial.canonical_root))
        self.assertFalse(stage_root.is_relative_to(candidate_root))
        self.fixture.assert_private_stage(self, stage_root)

        before_verify = self.fixture.tree_state(stage_root)
        verified = deployment.verify_deployment_stage(staged.stage_path)
        self.assertEqual(self.fixture.tree_state(stage_root), before_verify)
        self.assertEqual(verified.raw, staged.stage_raw)
        self.assertEqual(staged.stage_path.read_bytes(), staged.stage_raw)
        stage = self.fixture.canonical_stage_value(staged)
        self.assertEqual(
            stage["classification"],
            {
                "outcome": "authorized-control-set-maintenance",
                "reason": "exact-deployer-authorization",
            },
        )
        self.assertEqual(
            staged.classification.outcome,
            "authorized-control-set-maintenance",
        )
        self.assertEqual(
            staged.classification.reason,
            "exact-deployer-authorization",
        )
        self.assertEqual(
            staged.deployment_value["authorization"]["purpose"],
            "complete-control-set-maintenance",
        )
        candidate_receipt = json.loads(staged.deployment_raw)
        self.assertEqual(
            candidate_receipt["process_profile"],
            EXACT_PROCESS_PROFILE,
        )
        self.assertEqual(
            candidate_receipt["contracts"],
            EXACT_RECEIPT_CONTRACTS,
        )
        candidate_policy = json.loads(
            next(
                item.raw
                for item in verified.artifacts
                if item.role == "policy"
            )
        )
        self.assertEqual(
            candidate_policy["control_surface"]["contracts"],
            EXACT_RECEIPT_CLIENT_CONTRACTS,
        )

        candidate_controls = {
            item.role: item
            for item in verified.artifacts
            if item.role in CONTROL_ROLES
        }
        self.assertEqual(set(candidate_controls), set(CONTROL_ROLES))
        for role in CONTROL_ROLES:
            artifact = candidate_controls[role]
            self.assertEqual(artifact.raw, planned_controls[role].raw)
            self.assertEqual(
                dict(artifact.installed),
                staged.deployment_value["control_set"][role],
            )

        rollback = staged.rollback_value
        self.assertEqual(
            [item["role"] for item in rollback["selector_preimage"]],
            ["active-record", "deployment-alias"],
        )
        control_preimage = rollback["control_preimage"]
        self.assertEqual(
            [item["role"] for item in control_preimage],
            list(CONTROL_PREIMAGE_ROLES),
        )
        prior_bindings = {
            **prior_controls,
            "smoke-bundle-manifest": prepared.plan.precondition.receipt_value[
                "smoke"
            ]["bundle"]["manifest"],
        }
        for item in control_preimage:
            role = item["role"]
            self.assertEqual(item["installed"], prior_bindings[role])
            staged_path = Path(item["staged"]["path"])
            installed_path = Path(item["installed"]["path"])
            self.assertTrue(staged_path.is_relative_to(stage_root))
            self.assertTrue(installed_path.is_relative_to(initial.canonical_root))
            self.assertNotEqual(staged_path, installed_path)
            raw = staged_path.read_bytes()
            self.assertEqual(len(raw), item["staged"]["length"])
            self.assertEqual(sha256(raw), item["staged"]["sha256"])
            matches = [
                artifact
                for artifact in verified.artifacts
                if dict(artifact.staged) == item["staged"]
                and dict(artifact.installed) == item["installed"]
            ]
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].raw, raw)

        recovery = control_preimage[0]
        self.assertEqual(recovery["role"], "controller")
        self.assertEqual(
            recovery["staged"]["sha256"],
            prior_controls["controller"]["sha256"],
        )
        self.assertNotEqual(
            recovery["staged"]["sha256"],
            staged.deployment_value["control_set"]["controller"]["sha256"],
        )
        candidate_manifest = next(
            item
            for item in verified.artifacts
            if item.role == "smoke-bundle-manifest"
            and dict(item.installed)
            == staged.deployment_value["smoke"]["bundle"]["manifest"]
        )
        self.assertEqual(
            candidate_manifest.raw,
            next(
                item.raw
                for item in prepared.plan.artifacts
                if item.role == "smoke-bundle-manifest"
            ),
        )
        self.assertEqual(
            tuple(item["role"] for item in control_preimage[:-1])
            + tuple(item["role"] for item in rollback["selector_preimage"])
            + (control_preimage[-1]["role"],),
            MAINTENANCE_REPLACEMENT_ROLES,
        )

    def test_public_prepare_rejects_legacy_v1_policy_without_writes(self) -> None:
        self.assert_prepare_rejected_without_writes(
            self.fixture,
            self.fixture.v1_scenario(),
            "compatibility policy schema drift",
        )

    def test_public_prepare_rejects_missing_extra_or_malformed_control_surface(
        self,
    ) -> None:
        cases = (
            (
                "missing",
                lambda policy: policy.pop("control_surface"),
                "compatibility policy schema drift",
            ),
            (
                "extra",
                lambda policy: policy["control_surface"].update(
                    {"unexpected": True}
                ),
                "compatibility policy control surface schema drift",
            ),
            (
                "malformed-outer",
                lambda policy: policy.update({"schema_version": 1}),
                "compatibility policy schema version mismatch",
            ),
            (
                "malformed-surface",
                lambda policy: policy["control_surface"].update(
                    {"schema_version": "1"}
                ),
                "compatibility policy control surface schema version mismatch",
            ),
        )
        for name, mutate, message in cases:
            with self.subTest(name=name):
                fixture = ControlMaintenanceFixture(self.root / name)
                self.assert_prepare_rejected_without_writes(
                    fixture,
                    fixture.invalid_v2_scenario(mutate),
                    message,
                )

    def test_public_prepare_rejects_unsupported_declared_control_surface(
        self,
    ) -> None:
        def unsupported_profile(policy: dict[str, object]) -> None:
            policy["control_surface"]["process_profile"][
                "shared_lock_seconds"
            ] = 3

        def unsupported_contracts(policy: dict[str, object]) -> None:
            policy["control_surface"]["contracts"][
                "deployment_receipt"
            ] = "task-witness-deployment-receipt-v999"

        def unsupported_client_contract(policy: dict[str, object]) -> None:
            policy["control_surface"]["contracts"][
                "activation_transaction"
            ] = "task-witness-activation-transaction-v999"

        for name, mutate in (
            ("process-profile", unsupported_profile),
            ("receipt-contract", unsupported_contracts),
            ("client-contract", unsupported_client_contract),
        ):
            with self.subTest(name=name):
                fixture = ControlMaintenanceFixture(self.root / name)
                self.assert_prepare_rejected_without_writes(
                    fixture,
                    fixture.invalid_v2_scenario(mutate),
                    "compatibility policy control surface is unsupported",
                )

    def test_public_prepare_rejects_no_op_source_with_maintenance_difference(
        self,
    ) -> None:
        self.assert_prepare_rejected_without_writes(
            self.fixture,
            self.fixture.no_op_runtime_qualification_scenario(),
            (
                "deployment source transition is not authorized: "
                "no-op/exact-release"
            ),
        )

    def test_policy_change_is_authorized_maintenance_and_policy_is_rebound(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        _, _, _, request = self.fixture.policy_scenario()
        prepared = deployment.prepare_deployment(request)

        self.assertEqual(
            prepared.plan.value["maintenance_differences"],
            ("control-set", "compatibility-policy"),
        )
        self.assertEqual(
            (
                prepared.plan.classification.outcome,
                prepared.plan.classification.reason,
            ),
            ("approval-required", "future-update-policy"),
        )
        staged = deployment.stage_deployment(
            request,
            self.fixture.authorization_raw(prepared),
            self.root / "policy-maintenance-stage",
        )
        verified = deployment.verify_deployment_stage(staged.stage_path)
        self.assertEqual(verified.raw, staged.stage_raw)

        def break_candidate_policy_binding(
            rollback: dict[str, object],
            candidate: dict[str, object],
            stage: dict[str, object],
        ) -> None:
            del rollback, stage
            candidate["compatibility_policy"]["content_sha256"] = "0" * 64

        broken = self.fixture.rewrite_stage(
            staged,
            break_candidate_policy_binding,
        )
        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "staged candidate policy authority disagrees",
        ):
            deployment.verify_deployment_stage(broken)

    def test_runtime_qualification_change_requires_real_maintenance_difference(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        _, _, _, request = self.fixture.runtime_qualification_scenario()
        prepared = deployment.prepare_deployment(request)

        self.assertEqual(
            prepared.plan.value["maintenance_differences"],
            ("runtime-qualification",),
        )
        staged = deployment.stage_deployment(
            request,
            self.fixture.authorization_raw(prepared),
            self.root / "runtime-maintenance-stage",
        )
        verified = deployment.verify_deployment_stage(staged.stage_path)
        self.assertEqual(verified.raw, staged.stage_raw)
        prior_runtime_closure = json.loads(staged.rollback_raw)[
            "external_dependencies"
        ]["runtime_closure"]

        def erase_runtime_maintenance_difference(
            rollback: dict[str, object],
            candidate: dict[str, object],
            stage: dict[str, object],
        ) -> None:
            del rollback, stage
            candidate["runtime_closure"] = prior_runtime_closure

        broken = self.fixture.rewrite_stage(
            staged,
            erase_runtime_maintenance_difference,
        )
        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "staged control maintenance has no supported authority difference",
        ):
            deployment.verify_deployment_stage(broken)


if __name__ == "__main__":
    unittest.main()
