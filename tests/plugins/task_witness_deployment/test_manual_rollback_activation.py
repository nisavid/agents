from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ._control_maintenance_activation_support import (
    assert_prior_control_set_installed,
)
from ._control_maintenance_support import MAINTENANCE_REPLACEMENT_ROLES
from ._manual_rollback_support import (
    PublicManualRollbackSmokeBoundary,
    activate_routine_a_b_c,
    exact_tree_state,
    rollback_authorization_raw,
)
from ._routine_activation_support import (
    assert_no_transaction_residue,
    receipt_digest_inventory,
    selector_raws,
)
from ._support import sha256


class ManualRollbackActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_public_d_to_same_a_endpoint_mints_a_new_receipt(self) -> None:
        chain = activate_routine_a_b_c(self.root)
        deployment = chain.deployment
        first_request = deployment.RollbackToRequest(
            canonical_root=chain.canonical_root,
            expected_active_receipt_sha256=chain.receipt_c_sha256,
            target_receipt_sha256=chain.receipt_a_sha256,
            maintenance_transaction_sha256="1" * 64,
        )
        first_prepared = deployment.prepare_rollback_to(first_request)
        first_stage = self.root / "manual-rollback-stage-d"
        first_smoke = PublicManualRollbackSmokeBoundary(
            chain.canonical_root,
            chain.receipt_c_raw,
            chain.receipt_a_raw,
            candidate_accepted=True,
            rollback_accepted=True,
        )
        with mock.patch.object(
            deployment,
            "_spawn_activation_smoke_child",
            first_smoke,
        ):
            first = deployment.rollback_to(
                first_request,
                rollback_authorization_raw(first_prepared),
                first_stage,
            )

        receipt_d_raw = (chain.canonical_root / "deployment.json").read_bytes()
        receipt_d = json.loads(receipt_d_raw)
        receipt_d_sha256 = sha256(receipt_d_raw)
        self.assertEqual(first.active_receipt_sha256, receipt_d_sha256)
        before_receipts = receipt_digest_inventory(chain.canonical_root)
        second_request = deployment.RollbackToRequest(
            canonical_root=chain.canonical_root,
            expected_active_receipt_sha256=receipt_d_sha256,
            target_receipt_sha256=chain.receipt_a_sha256,
            maintenance_transaction_sha256="2" * 64,
        )
        second_prepared = deployment.prepare_rollback_to(second_request)
        second_stage = self.root / "manual-rollback-stage-e"
        second_smoke = PublicManualRollbackSmokeBoundary(
            chain.canonical_root,
            receipt_d_raw,
            chain.receipt_a_raw,
            candidate_accepted=True,
            rollback_accepted=True,
        )

        with mock.patch.object(
            deployment,
            "_spawn_activation_smoke_child",
            second_smoke,
        ):
            second = deployment.rollback_to(
                second_request,
                rollback_authorization_raw(second_prepared),
                second_stage,
            )

        receipt_e_raw = (chain.canonical_root / "deployment.json").read_bytes()
        receipt_e = json.loads(receipt_e_raw)
        target = json.loads(chain.receipt_a_raw)
        self.assertEqual(second.outcome, "candidate-active")
        self.assertEqual(
            second.journal_value["terminal_result"]["outcome"],
            "manual-target-active",
        )
        self.assertEqual(second_smoke.phases, ["candidate-smoke"])
        self.assertEqual(selector_raws(chain.canonical_root)[0], chain.active_a_raw)
        self.assertEqual(
            (receipt_e["sequence"], receipt_e["prior_receipt_sha256"]),
            (receipt_d["sequence"] + 1, receipt_d_sha256),
        )
        for key in (
            "source",
            "active",
            "control_set",
            "interpreter",
            "runtime_closure",
            "process_profile",
            "platform",
            "compatibility_policy",
            "contracts",
            "providers",
            "role_inventory",
            "smoke",
            "trust_context",
        ):
            self.assertEqual(receipt_e[key], target[key], key)
        self.assertEqual(
            len(receipt_digest_inventory(chain.canonical_root)),
            len(before_receipts) + 2,
        )
        self.assertTrue(first_stage.is_dir())
        self.assertTrue(second_stage.is_dir())
        assert_no_transaction_residue(chain.canonical_root)

    def test_manual_stage_rejects_reused_immutable_release_identity(self) -> None:
        chain = activate_routine_a_b_c(self.root, c_revision="a" * 40)
        deployment = chain.deployment
        request = deployment.RollbackToRequest(
            canonical_root=chain.canonical_root,
            expected_active_receipt_sha256=chain.receipt_c_sha256,
            target_receipt_sha256=chain.receipt_a_sha256,
            maintenance_transaction_sha256="3" * 64,
        )
        prepared = deployment.prepare_rollback_to(request)
        authorization_raw = rollback_authorization_raw(prepared)
        before = exact_tree_state(chain.canonical_root)
        stage_root = self.root / "manual-integrity-rejection-stage"
        smoke = PublicManualRollbackSmokeBoundary(
            chain.canonical_root,
            chain.receipt_c_raw,
            chain.receipt_a_raw,
            candidate_accepted=True,
            rollback_accepted=True,
        )

        with (
            mock.patch.object(
                deployment,
                "_spawn_activation_smoke_child",
                smoke,
            ),
            self.assertRaisesRegex(
                deployment.DeploymentError,
                "source transition failed integrity classification: "
                "integrity-rejected/immutable-release-reused",
            ),
        ):
            deployment.rollback_to(request, authorization_raw, stage_root)

        self.assertEqual(smoke.phases, [])
        self.assertEqual(exact_tree_state(chain.canonical_root), before)
        self.assertTrue(stage_root.is_dir())
        assert_no_transaction_residue(chain.canonical_root)

    def test_public_c_to_a_rollback_mints_d_and_preserves_full_lineage(self) -> None:
        chain = activate_routine_a_b_c(self.root)
        deployment = chain.deployment
        request = deployment.RollbackToRequest(
            canonical_root=chain.canonical_root,
            expected_active_receipt_sha256=chain.receipt_c_sha256,
            target_receipt_sha256=chain.receipt_a_sha256,
            maintenance_transaction_sha256="d" * 64,
        )
        prepared = deployment.prepare_rollback_to(request)
        authorization_raw = rollback_authorization_raw(prepared)
        before_receipts = receipt_digest_inventory(chain.canonical_root)
        result_directory = chain.canonical_root / "transaction-results"
        before_results = {
            path.name: path.read_bytes()
            for path in result_directory.glob("*.json")
        }
        stage_root = self.root / "manual-rollback-stage"
        smoke = PublicManualRollbackSmokeBoundary(
            chain.canonical_root,
            chain.receipt_c_raw,
            chain.receipt_a_raw,
            candidate_accepted=True,
            rollback_accepted=True,
        )

        with mock.patch.object(
            deployment,
            "_spawn_activation_smoke_child",
            smoke,
        ):
            result = deployment.rollback_to(
                request,
                authorization_raw,
                stage_root,
            )

        deployment_raw = (chain.canonical_root / "deployment.json").read_bytes()
        deployment_value = json.loads(deployment_raw)
        target = json.loads(chain.receipt_a_raw)
        current = json.loads(chain.receipt_c_raw)
        self.assertEqual(result.outcome, "candidate-active")
        self.assertEqual(
            result.journal_value["terminal_result"]["outcome"],
            "manual-target-active",
        )
        self.assertEqual(result.active_receipt_sha256, sha256(deployment_raw))
        self.assertEqual(smoke.phases, ["candidate-smoke"])
        self.assertEqual(selector_raws(chain.canonical_root)[0], chain.active_a_raw)
        self.assertEqual(
            (
                deployment_value["sequence"],
                deployment_value["prior_receipt_sha256"],
                deployment_value["authorization"]["purpose"],
            ),
            (
                current["sequence"] + 1,
                chain.receipt_c_sha256,
                "manual-exact-target-rollback",
            ),
        )
        for key in (
            "source",
            "active",
            "control_set",
            "interpreter",
            "runtime_closure",
            "process_profile",
            "platform",
            "compatibility_policy",
            "contracts",
            "providers",
            "role_inventory",
            "smoke",
            "trust_context",
        ):
            self.assertEqual(deployment_value[key], target[key], key)
        after_receipts = receipt_digest_inventory(chain.canonical_root)
        self.assertEqual(len(after_receipts), len(before_receipts) + 2)
        self.assertTrue(before_receipts < after_receipts)
        after_results = {
            path.name: path.read_bytes()
            for path in result_directory.glob("*.json")
        }
        self.assertEqual(
            after_results,
            {
                **before_results,
                f"sha256-{result.transaction_id}.json": result.journal_raw,
            },
        )
        fresh = deployment._capture_active_deployment_precondition(
            chain.canonical_root,
            sha256(deployment_raw),
        )
        self.assertEqual(
            [
                edge.successor_receipt_sha256
                for edge in fresh.retained_chain.authority_edges
            ],
            [
                sha256(deployment_raw),
                chain.receipt_c_sha256,
                chain.receipt_b_sha256,
            ],
        )
        self.assertEqual(
            fresh.retained_chain.authority_edges[0].authorization_purpose,
            "manual-exact-target-rollback",
        )
        self.assertEqual(
            fresh.retained_chain.authority_edges[0].control_raws,
            prepared.plan.precondition.control_raws,
        )
        for target_sha256 in (
            chain.receipt_a_sha256,
            chain.receipt_b_sha256,
            chain.receipt_c_sha256,
        ):
            followup = deployment.prepare_rollback_to(
                deployment.RollbackToRequest(
                    canonical_root=chain.canonical_root,
                    expected_active_receipt_sha256=sha256(deployment_raw),
                    target_receipt_sha256=target_sha256,
                    maintenance_transaction_sha256="e" * 64,
                )
            )
            self.assertEqual(
                followup.plan.target.receipt_sha256,
                target_sha256,
            )
        reconciled = deployment.reconcile_transaction_result(
            deployment.ResultReconciliationRequest(
                activation=deployment.ActivationRequest(
                    deployment=request,
                    authorization_raw=authorization_raw,
                    stage_receipt=stage_root / "stage.json",
                ),
                expected_terminal_journal_raw=result.journal_raw,
            )
        )
        self.assertEqual(reconciled, result)
        self.assertEqual(
            receipt_digest_inventory(chain.canonical_root),
            after_receipts,
        )
        self.assertEqual(
            {
                path.name: path.read_bytes()
                for path in result_directory.glob("*.json")
            },
            after_results,
        )
        self.assertEqual(selector_raws(chain.canonical_root)[0], chain.active_a_raw)
        self.assertEqual(
            (chain.canonical_root / "deployment.json").read_bytes(),
            deployment_raw,
        )
        self.assertTrue(stage_root.is_dir())
        assert_no_transaction_residue(chain.canonical_root)

    def test_candidate_rejection_restores_only_c_and_removes_d_authority(self) -> None:
        chain = activate_routine_a_b_c(self.root)
        deployment = chain.deployment
        request = deployment.RollbackToRequest(
            canonical_root=chain.canonical_root,
            expected_active_receipt_sha256=chain.receipt_c_sha256,
            target_receipt_sha256=chain.receipt_a_sha256,
            maintenance_transaction_sha256="f" * 64,
        )
        prepared = deployment.prepare_rollback_to(request)
        authorization_raw = rollback_authorization_raw(prepared)
        before_receipts = receipt_digest_inventory(chain.canonical_root)
        result_directory = chain.canonical_root / "transaction-results"
        before_results = {
            path.name: path.read_bytes()
            for path in result_directory.glob("*.json")
        }
        stage_root = self.root / "manual-rollback-rejection-stage"
        smoke = PublicManualRollbackSmokeBoundary(
            chain.canonical_root,
            chain.receipt_c_raw,
            chain.receipt_a_raw,
            candidate_accepted=False,
            rollback_accepted=True,
        )
        replacements: list[tuple[str, int, str]] = []
        cleanups: list[tuple[str, str]] = []
        replace = deployment._replace_control_maintenance_artifact
        remove_artifact = deployment._remove_activation_artifact
        remove_directory = deployment._remove_activation_directory

        def observe_replace(*args, **kwargs):
            result = replace(*args, **kwargs)
            replacements.append(
                (
                    kwargs["direction"],
                    kwargs["index"],
                    kwargs["replacement"].role,
                )
            )
            return result

        def observe_remove_artifact(root_fd, artifact):
            result = remove_artifact(root_fd, artifact)
            cleanups.append(("remove-artifact", artifact.role))
            return result

        def observe_remove_directory(root_fd, relative_path):
            result = remove_directory(root_fd, relative_path)
            cleanups.append(("remove-directory", relative_path))
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
            result = deployment.rollback_to(
                request,
                authorization_raw,
                stage_root,
            )

        verified = deployment.verify_deployment_stage(stage_root / "stage.json")
        d = next(item for item in verified.artifacts if item.role == "deployment-receipt")
        r_d = next(item for item in verified.artifacts if item.role == "rollback-receipt")
        self.assertEqual(result.outcome, "restored-prior")
        self.assertEqual(
            result.journal_value["terminal_result"]["outcome"],
            "manual-current-restored",
        )
        self.assertEqual(
            result.journal_value["terminal_result"]["failure_class"],
            "target-smoke-rejected",
        )
        self.assertEqual(result.active_receipt_sha256, chain.receipt_c_sha256)
        self.assertEqual(smoke.phases, ["candidate-smoke", "rollback-smoke"])
        self.assertEqual(
            smoke.observations[0]["active_raw"],
            chain.active_a_raw,
        )
        self.assertEqual(
            smoke.observations[1]["deployment_raw"],
            chain.receipt_c_raw,
        )
        self.assertEqual(
            replacements,
            [
                (direction, index, role)
                for direction in ("candidate", "prior")
                for index, role in enumerate(MAINTENANCE_REPLACEMENT_ROLES)
            ],
        )
        self.assertEqual(
            cleanups,
            [
                ("remove-artifact", "rollback-receipt"),
                ("remove-artifact", "deployment-receipt"),
            ],
        )
        assert_prior_control_set_installed(verified)
        self.assertEqual(
            selector_raws(chain.canonical_root),
            (prepared.plan.precondition.active_raw, chain.receipt_c_raw),
        )
        self.assertEqual(
            receipt_digest_inventory(chain.canonical_root),
            before_receipts,
        )
        self.assertNotIn(sha256(r_d.raw), before_receipts)
        self.assertNotIn(sha256(d.raw), before_receipts)
        after_results = {
            path.name: path.read_bytes()
            for path in result_directory.glob("*.json")
        }
        self.assertEqual(
            after_results,
            {
                **before_results,
                f"sha256-{result.transaction_id}.json": result.journal_raw,
            },
        )
        fresh = deployment._capture_active_deployment_precondition(
            chain.canonical_root,
            chain.receipt_c_sha256,
        )
        self.assertEqual(
            [
                edge.successor_receipt_sha256
                for edge in fresh.retained_chain.authority_edges
            ],
            [chain.receipt_c_sha256, chain.receipt_b_sha256],
        )
        assert_no_transaction_residue(chain.canonical_root)


if __name__ == "__main__":
    unittest.main()
