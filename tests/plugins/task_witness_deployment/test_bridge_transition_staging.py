from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ._bridge_transition_stage_support import (
    OutboundBridgeStagingFixture,
    expected_transition_binding,
    transition_evidence_projection,
)
from ._control_maintenance_support import ControlMaintenanceFixture
from ._source_evidence_support import exact_tree_state
from ._support import canonical_bytes, content_document, sha256


class BridgeTransitionStagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.fixture = OutboundBridgeStagingFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_public_bridge_stage_binds_external_evidence_without_installing_it(
        self,
    ) -> None:
        authorized = self.fixture.prepare_authorized()
        outbound = authorized.outbound
        deployment = outbound.deployment
        self.addCleanup(self.fixture.close, authorized)

        staged = deployment.stage_bridge_transition(
            outbound.request,
            authorized.deployment_authorization_raw,
            authorized.transition_authorization_path,
            outbound.staging_root,
        )
        verified = deployment.verify_deployment_stage(staged.stage_path)

        self.assertEqual(
            (staged.classification.outcome, staged.classification.reason),
            (
                "authorized-bridge-transition",
                "exact-bridge-transition-authorization",
            ),
        )
        self.assertEqual(staged.plan, authorized.prepared.plan)
        self.assertEqual(
            tuple(item.role for item in staged.transition_evidence),
            ("manifest", "authorization"),
        )
        self.assertEqual(
            tuple(
                transition_evidence_projection(item)
                for item in verified.transition_evidence
            ),
            tuple(
                transition_evidence_projection(item)
                for item in staged.transition_evidence
            ),
        )
        expected_manifest = expected_transition_binding(
            staging_root=outbound.staging_root,
            relative_path="bridge-transition-manifest.json",
            raw=outbound.release_manifest_raw,
        )
        expected_authorization = expected_transition_binding(
            staging_root=outbound.staging_root,
            relative_path="bridge-transition-authorization.json",
            raw=authorized.transition_authorization_raw,
        )
        self.assertEqual(
            json.loads(staged.stage_raw)["transition_evidence"],
            {
                "manifest": expected_manifest,
                "authorization": expected_authorization,
            },
        )
        self.assertEqual(
            [
                {
                    **dict(item.staged),
                    "relative_path": item.relative_path,
                }
                for item in staged.transition_evidence
            ],
            [expected_manifest, expected_authorization],
        )
        artifact_paths = {item.staged_path for item in staged.artifacts}
        self.assertTrue(
            artifact_paths.isdisjoint(
                item.staged_path for item in staged.transition_evidence
            )
        )
        self.assertFalse(
            {"manifest", "authorization"} & {item.role for item in staged.artifacts}
        )

        receipt = json.loads(staged.deployment_raw)
        migration = receipt["migration"]
        transition = json.loads(authorized.transition_authorization_raw)
        self.assertEqual(
            migration,
            {
                "schema_version": 1,
                "contract": "task-witness-bridge-migration-projection-v1",
                "edge": {"from": "freeze5", "to": "tw4", "via": "bridge"},
                "purpose": "bridge-transition",
                "execution_class": "isolated-rehearsal",
                "maintenance_transaction_sha256": transition[
                    "maintenance_transaction_sha256"
                ],
                "deployment_authorization_sha256": sha256(
                    authorized.deployment_authorization_raw
                ),
                "transition_authorization_sha256": sha256(
                    authorized.transition_authorization_raw
                ),
                "expected_active_receipt_core_sha256": transition[
                    "expected_active_receipt_core_sha256"
                ],
                "bridge_identity_sha256": transition["bridge_identity_sha256"],
                "release_manifest_sha256": transition["release_manifest_sha256"],
                "endpoint_projection_sha256": transition["endpoint_projection_sha256"],
            },
        )
        core = {
            key: value
            for key, value in receipt.items()
            if key not in {"migration", "content_sha256"}
        }
        self.assertEqual(
            sha256(canonical_bytes(core)),
            transition["expected_active_receipt_core_sha256"],
        )

    def test_invalid_external_authority_rejects_before_stage_creation(self) -> None:
        cases = (
            "missing-transition-authorization",
            "wrong-ordinary-authorization",
            "wrong-transition-purpose",
            "wrong-execution-class",
            "isolated-extra-prior-rehearsal",
            "live-missing-prior-rehearsal",
            "reused-for-another-stage",
            "changed-release-manifest",
            "changed-endpoint-projection",
        )
        for case in cases:
            with self.subTest(case=case):
                case_root = self.root / case
                case_root.mkdir(mode=0o700)
                fixture = OutboundBridgeStagingFixture(case_root)
                authorized = fixture.prepare_authorized(
                    execution_class=(
                        "live-migration"
                        if case == "live-missing-prior-rehearsal"
                        else "isolated-rehearsal"
                    )
                )
                outbound = authorized.outbound
                deployment = outbound.deployment
                deployment_raw = authorized.deployment_authorization_raw
                transition_path = authorized.transition_authorization_path
                staging_root = outbound.staging_root
                request = outbound.request
                try:
                    if case == "missing-transition-authorization":
                        transition_path.unlink()
                    elif case == "wrong-ordinary-authorization":
                        deployment_raw = b""
                    elif case in {
                        "wrong-transition-purpose",
                        "wrong-execution-class",
                        "isolated-extra-prior-rehearsal",
                    }:
                        value = json.loads(transition_path.read_bytes())
                        value.pop("content_sha256")
                        if case == "wrong-transition-purpose":
                            value["purpose"] = "ordinary-deployment"
                        elif case == "wrong-execution-class":
                            value["execution_class"] = "live-migration"
                            value["prior_rehearsal"] = {
                                "endpoint_projection_sha256": "1" * 64,
                                "transaction_sha256": "2" * 64,
                                "terminal_result_sha256": "3" * 64,
                                "active_receipt_sha256": "4" * 64,
                            }
                        else:
                            value["prior_rehearsal"] = {
                                "endpoint_projection_sha256": "1" * 64,
                                "transaction_sha256": "2" * 64,
                                "terminal_result_sha256": "3" * 64,
                                "active_receipt_sha256": "4" * 64,
                            }
                        transition_path.write_bytes(
                            canonical_bytes(content_document(value))
                        )
                        transition_path.chmod(0o600)
                    elif case == "reused-for-another-stage":
                        staging_root = case_root / "another-bridge-stage"
                    elif case == "changed-release-manifest":
                        value = json.loads(outbound.release_manifest_path.read_bytes())
                        value.pop("content_sha256")
                        value["promotion_delta_sha256"] = "f" * 64
                        outbound.release_manifest_path.write_bytes(
                            canonical_bytes(content_document(value))
                        )
                        outbound.release_manifest_path.chmod(0o600)
                    elif case == "changed-endpoint-projection":
                        value = json.loads(outbound.endpoint_projection_raw)
                        value.pop("content_sha256")
                        value["runtime_closure_sha256"] = "f" * 64
                        request = deployment.BridgeTransitionRequest(
                            deployment=outbound.request.deployment,
                            release_manifest_path=(
                                outbound.request.release_manifest_path
                            ),
                            endpoint_projection_raw=canonical_bytes(
                                content_document(value)
                            ),
                            execution_class=outbound.request.execution_class,
                        )

                    before = exact_tree_state(case_root)
                    with self.assertRaises(deployment.DeploymentError):
                        deployment.stage_bridge_transition(
                            request,
                            deployment_raw,
                            transition_path,
                            staging_root,
                        )
                    self.assertEqual(exact_tree_state(case_root), before)
                    self.assertFalse(staging_root.exists())
                    self.assertFalse(outbound.staging_root.exists())
                finally:
                    fixture.close(authorized)

    def test_live_bridge_stage_retains_exact_completed_rehearsal(self) -> None:
        prior_rehearsal = {
            "endpoint_projection_sha256": "1" * 64,
            "transaction_sha256": "2" * 64,
            "terminal_result_sha256": "3" * 64,
            "active_receipt_sha256": "4" * 64,
        }
        authorized = self.fixture.prepare_authorized(
            execution_class="live-migration",
            prior_rehearsal=prior_rehearsal,
        )
        outbound = authorized.outbound
        deployment = outbound.deployment
        self.addCleanup(self.fixture.close, authorized)

        staged = deployment.stage_bridge_transition(
            outbound.request,
            authorized.deployment_authorization_raw,
            authorized.transition_authorization_path,
            outbound.staging_root,
        )
        verified = deployment.verify_deployment_stage(staged.stage_path)

        self.assertEqual(
            json.loads(authorized.transition_authorization_raw)["prior_rehearsal"],
            prior_rehearsal,
        )
        self.assertEqual(
            json.loads(staged.deployment_raw)["migration"]["prior_rehearsal"],
            prior_rehearsal,
        )
        self.assertEqual(
            json.loads(verified.raw)["transition_evidence"],
            json.loads(staged.stage_raw)["transition_evidence"],
        )

    def test_ordinary_stage_values_expose_empty_transition_evidence(self) -> None:
        fixture = ControlMaintenanceFixture(self.root / "ordinary")
        deployment = fixture.deployment()
        _, _, _, request = fixture.policy_scenario()
        prepared = deployment.prepare_deployment(request)
        staged = deployment.stage_deployment(
            request,
            fixture.authorization_raw(prepared),
            fixture.root / "ordinary-stage",
        )
        verified = deployment.verify_deployment_stage(staged.stage_path)

        self.assertEqual(staged.transition_evidence, ())
        self.assertEqual(verified.transition_evidence, ())
        self.assertNotIn("transition_evidence", json.loads(staged.stage_raw))


if __name__ == "__main__":
    unittest.main()
