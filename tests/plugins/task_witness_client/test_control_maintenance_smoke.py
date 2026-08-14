from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ._control_maintenance_smoke_support import (
    CONTROL_PATHS,
    MUTATION_ORDER,
    build_control_maintenance_smoke,
    rewrite_control_maintenance_stage,
    rewrite_control_maintenance_transaction,
    swap_staged_control_maintenance_receipts,
)
from ._support import sha256


class ControlMaintenanceSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def assert_rejected_before_launch(self, scenario) -> None:
        result = scenario.invoke()

        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertFalse(scenario.launcher_marker.exists())

    def test_candidate_smoke_uses_stage_bound_candidate_controls(self) -> None:
        scenario = build_control_maintenance_smoke(
            self.root,
            phase="candidate-smoke",
        )
        self.assertNotEqual(
            scenario.prior_receipt["control_set"]["controller"]["sha256"],
            scenario.candidate_receipt["control_set"]["controller"]["sha256"],
        )
        self.assertNotEqual(
            scenario.prior_receipt["control_set"]["policy"]["sha256"],
            scenario.candidate_receipt["control_set"]["policy"]["sha256"],
        )
        self.assertNotEqual(
            scenario.prior_receipt["control_set"]["client"]["sha256"],
            scenario.candidate_receipt["control_set"]["client"]["sha256"],
        )
        self.assertEqual(
            sha256(scenario.stage_path.read_bytes()), sha256(scenario.stage_raw)
        )
        self.assertEqual(
            scenario.transaction["stage"],
            {
                **scenario.transaction["stage"],
                "receipt_path": str(scenario.stage_path),
                "receipt_sha256": sha256(scenario.stage_raw),
            },
        )
        self.assertEqual(
            scenario.candidate_receipt["authorization"]["purpose"],
            "complete-control-set-maintenance",
        )
        self.assertEqual(
            tuple(
                item["role"] for item in scenario.transaction["preimage"]["artifacts"]
            ),
            MUTATION_ORDER,
        )
        self.assertEqual(
            tuple(
                item["role"] for item in scenario.rollback_receipt["control_preimage"]
            ),
            (
                "controller",
                "policy",
                "launcher",
                "client",
                "smoke-bundle-manifest",
                "shim",
            ),
        )
        stage_artifacts = {item["role"]: item for item in scenario.stage["artifacts"]}
        for role, relative_path in CONTROL_PATHS.items():
            self.assertEqual(
                stage_artifacts[role]["relative_path"],
                f"candidate/{relative_path}",
            )
            self.assertEqual(
                stage_artifacts[f"prior-{role}"]["relative_path"],
                f"preimage/{relative_path}",
            )

        result = scenario.invoke()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, scenario.expected_envelope_raw)
        self.assertEqual(result.stderr, b"")
        self.assertTrue(scenario.launcher_marker.is_file())

    def test_rollback_smoke_uses_stage_bound_prior_controls(self) -> None:
        scenario = build_control_maintenance_smoke(
            self.root,
            phase="rollback-smoke",
        )

        result = scenario.invoke()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, scenario.expected_envelope_raw)
        self.assertEqual(result.stderr, b"")
        self.assertTrue(scenario.launcher_marker.is_file())

    def test_swapped_candidate_and_prior_stage_authority_rejects_before_launch(
        self,
    ) -> None:
        for role in ("controller", "policy"):
            with self.subTest(role=role):
                scenario = build_control_maintenance_smoke(
                    self.root / role,
                    phase="candidate-smoke",
                    swap_stage_role=role,
                )
                stage_artifacts = {
                    item["role"]: item for item in scenario.stage["artifacts"]
                }
                self.assertEqual(
                    stage_artifacts[role]["staged"]["sha256"],
                    scenario.prior_receipt["control_set"][role]["sha256"],
                )
                self.assertEqual(
                    stage_artifacts[f"prior-{role}"]["staged"]["sha256"],
                    scenario.candidate_receipt["control_set"][role]["sha256"],
                )

                result = scenario.invoke()

                self.assertEqual(result.returncode, 70, result.stderr)
                self.assertEqual(result.stdout, b"")
                self.assertFalse(scenario.launcher_marker.exists())

    def test_stage_receipt_identity_classification_and_order_are_exact(self) -> None:
        def wrong_path(scenario) -> None:
            rewrite_control_maintenance_transaction(
                scenario,
                lambda transaction: transaction["stage"].__setitem__(
                    "receipt_path",
                    str(scenario.stage_path.with_name("other-stage.json")),
                ),
            )

        def wrong_digest(scenario) -> None:
            rewrite_control_maintenance_transaction(
                scenario,
                lambda transaction: transaction["stage"].__setitem__(
                    "receipt_sha256",
                    "0" * 64,
                ),
            )

        def wrong_classification(scenario) -> None:
            rewrite_control_maintenance_stage(
                scenario,
                lambda stage: stage.__setitem__(
                    "classification",
                    {
                        "outcome": "authorized-routine-payload",
                        "reason": "active-policy-compatible-forward",
                    },
                ),
            )

        def reordered_artifacts(scenario) -> None:
            rewrite_control_maintenance_stage(
                scenario,
                lambda stage: stage["artifacts"].__setitem__(
                    slice(0, 2),
                    list(reversed(stage["artifacts"][:2])),
                ),
            )

        for name, mutate in {
            "path": wrong_path,
            "digest": wrong_digest,
            "classification": wrong_classification,
            "artifact-order": reordered_artifacts,
        }.items():
            with self.subTest(name=name):
                scenario = build_control_maintenance_smoke(
                    self.root / name,
                    phase="candidate-smoke",
                )
                mutate(scenario)
                self.assert_rejected_before_launch(scenario)

    def test_stage_inventory_and_candidate_prior_receipts_are_exact(self) -> None:
        for name in ("extra-file", "receipt-swap"):
            with self.subTest(name=name):
                scenario = build_control_maintenance_smoke(
                    self.root / name,
                    phase="candidate-smoke",
                )
                if name == "extra-file":
                    extra = scenario.stage_path.parent / "unexpected"
                    extra.write_bytes(b"unexpected\n")
                    extra.chmod(0o600)
                else:
                    swap_staged_control_maintenance_receipts(scenario)
                self.assert_rejected_before_launch(scenario)

    def test_control_preimage_order_and_content_are_exact(self) -> None:
        def reorder(transaction) -> None:
            transaction["preimage"]["artifacts"][:2] = reversed(
                transaction["preimage"]["artifacts"][:2]
            )

        def replace_content(transaction) -> None:
            transaction["preimage"]["artifacts"][0]["staged"]["sha256"] = transaction[
                "candidate"
            ]["control_set"]["controller"]["sha256"]

        for name, mutate in {"order": reorder, "content": replace_content}.items():
            with self.subTest(name=name):
                scenario = build_control_maintenance_smoke(
                    self.root / name,
                    phase="candidate-smoke",
                )
                rewrite_control_maintenance_transaction(scenario, mutate)
                self.assert_rejected_before_launch(scenario)

    def test_phase_and_target_selection_are_exact(self) -> None:
        def phase_swap(transaction) -> None:
            transaction["phase"] = "rollback-smoke"

        def target_swap(transaction) -> None:
            transaction["smoke_handoff"]["target_deployment_receipt_sha256"] = (
                transaction["prior"]["deployment_receipt"]["sha256"]
            )

        for name, mutate in {"phase": phase_swap, "target": target_swap}.items():
            with self.subTest(name=name):
                scenario = build_control_maintenance_smoke(
                    self.root / name,
                    phase="candidate-smoke",
                )
                rewrite_control_maintenance_transaction(scenario, mutate)
                self.assert_rejected_before_launch(scenario)

    def test_current_and_terminal_receipt_contracts_are_validated(self) -> None:
        def drift(receipt) -> None:
            receipt["contracts"]["compatibility_policy"] = (
                "task-witness-compatibility-policy-v1"
            )

        def restore(receipt) -> None:
            receipt["contracts"]["compatibility_policy"] = (
                "task-witness-compatibility-policy-v2"
            )

        for name, options in {
            "current": {"mutate_candidate_receipt": drift},
            "terminal": {
                "mutate_prior_receipt": drift,
                "mutate_candidate_receipt": restore,
            },
        }.items():
            with self.subTest(name=name):
                scenario = build_control_maintenance_smoke(
                    self.root / name,
                    phase="candidate-smoke",
                    **options,
                )
                self.assert_rejected_before_launch(scenario)


if __name__ == "__main__":
    unittest.main()
