from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ._source_transition_support import SourceTransitionFixture
from ._support import canonical_document, content_document


class SourceEvidenceTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.fixture = SourceTransitionFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_deployment_request_has_one_discriminated_source_evidence_slot(
        self,
    ) -> None:
        fields = tuple(self.fixture.deployment.DeploymentRequest.__dataclass_fields__)

        self.assertIn("source_evidence", fields)
        self.assertNotIn("manager_binding_raw", fields)
        self.assertNotIn("manager_receipt_raw", fields)

    def test_publisher_forward_is_routine_compatible(self) -> None:
        _, active, _, _ = self.fixture.activate_initial(
            "publisher_channel",
            sequence=7,
        )
        candidate = self.fixture.candidate(
            "publisher_channel",
            "publisher-forward",
            "1.0.1",
        )
        request = self.fixture.request(
            candidate_root=candidate,
            canonical_root=self.fixture.first.canonical_root,
            active_receipt_sha256=active.active_receipt_sha256,
            mode="publisher_channel",
            revision="b" * 40,
            sequence=8,
        )

        prepared = self.fixture.deployment.prepare_deployment(request)

        self.assertEqual(prepared.plan.classification.outcome, "compatible-forward")
        self.assertEqual(prepared.plan.classification.reason, "active-policy")
        self.assertEqual(prepared.plan.value["operation"], "routine-payload")

    def test_publisher_downgrade_requires_exact_source_boundary_approval(self) -> None:
        _, active, _, _ = self.fixture.activate_initial(
            "publisher_channel",
            sequence=7,
        )
        candidate = self.fixture.candidate(
            "publisher_channel",
            "publisher-downgrade",
            "1.0.1",
        )
        request = self.fixture.request(
            candidate_root=candidate,
            canonical_root=self.fixture.first.canonical_root,
            active_receipt_sha256=active.active_receipt_sha256,
            mode="publisher_channel",
            revision="b" * 40,
            sequence=6,
        )

        prepared = self.fixture.deployment.prepare_deployment(request)

        self.assertEqual(prepared.plan.classification.outcome, "approval-required")
        self.assertEqual(prepared.plan.classification.reason, "downgrade")
        self.assertEqual(prepared.plan.value["operation"], "routine-payload")

    def test_exact_release_changed_pin_requires_source_boundary_approval(self) -> None:
        _, active, _, _ = self.fixture.activate_initial("exact_release")
        candidate = self.fixture.candidate(
            "exact_release",
            "exact-changed-pin",
            "1.0.1",
        )
        request = self.fixture.request(
            candidate_root=candidate,
            canonical_root=self.fixture.first.canonical_root,
            active_receipt_sha256=active.active_receipt_sha256,
            mode="exact_release",
            revision="b" * 40,
        )

        prepared = self.fixture.deployment.prepare_deployment(request)

        self.assertEqual(prepared.plan.classification.outcome, "approval-required")
        self.assertEqual(prepared.plan.classification.reason, "exact-release-pin")
        self.assertEqual(prepared.plan.value["operation"], "routine-payload")

    def test_exact_release_same_pin_is_rejected_as_a_no_op(self) -> None:
        _, active, _, candidate = self.fixture.activate_initial("exact_release")
        request = self.fixture.request(
            candidate_root=candidate,
            canonical_root=self.fixture.first.canonical_root,
            active_receipt_sha256=active.active_receipt_sha256,
            mode="exact_release",
            revision="a" * 40,
        )

        with self.assertRaisesRegex(
            self.fixture.deployment.DeploymentError,
            "no-op/exact-release",
        ):
            self.fixture.deployment.prepare_deployment(request)

    def test_cross_mode_change_requires_source_authority_approval(self) -> None:
        _, active, _, _ = self.fixture.activate_initial(
            "publisher_channel",
            sequence=7,
        )
        candidate = self.fixture.candidate(
            "exact_release",
            "cross-mode-exact",
            "1.0.1",
        )
        request = self.fixture.request(
            candidate_root=candidate,
            canonical_root=self.fixture.first.canonical_root,
            active_receipt_sha256=active.active_receipt_sha256,
            mode="exact_release",
            revision="b" * 40,
        )

        prepared = self.fixture.deployment.prepare_deployment(request)

        self.assertEqual(prepared.plan.classification.outcome, "approval-required")
        self.assertEqual(prepared.plan.classification.reason, "source-authority")
        self.assertEqual(
            prepared.plan.value["operation"],
            "complete-control-set-maintenance",
        )

    def test_same_mode_source_authority_precedes_candidate_policy_change(self) -> None:
        _, active, _, _ = self.fixture.activate_initial(
            "publisher_channel",
            sequence=7,
        )
        candidate = self.fixture.candidate(
            "publisher_channel",
            "publisher-authority-and-policy",
            "1.0.1",
        )
        policy_path = candidate / "controller" / "policy.json"
        policy = json.loads(policy_path.read_bytes())
        policy["source"]["source_authority"] = "alternate-source-authority"
        policy.pop("content_sha256")
        policy_path.write_bytes(canonical_document(content_document(policy)))
        request = self.fixture.request(
            candidate_root=candidate,
            canonical_root=self.fixture.first.canonical_root,
            active_receipt_sha256=active.active_receipt_sha256,
            mode="publisher_channel",
            revision="b" * 40,
            sequence=8,
            source_authority="alternate-source-authority",
        )

        prepared = self.fixture.deployment.prepare_deployment(request)
        authorization_raw = self.fixture.authorization_raw(
            prepared,
            "source-boundary-change",
        )
        staged = self.fixture.deployment.stage_deployment(
            request,
            authorization_raw,
            self.root / "publisher-authority-and-policy-stage",
        )

        self.assertEqual(
            (
                prepared.plan.classification.outcome,
                prepared.plan.classification.reason,
            ),
            ("approval-required", "source-authority"),
        )
        self.assertEqual(
            prepared.plan.value["operation"],
            "complete-control-set-maintenance",
        )
        self.assertEqual(
            staged.deployment_value["authorization"]["purpose"],
            "source-boundary-change",
        )
        self.assertEqual(
            self.fixture.deployment.verify_deployment_stage(staged.stage_path).raw,
            staged.stage_raw,
        )

    def test_integrity_rejection_precedes_cross_mode_source_approval(self) -> None:
        _, active, _, _ = self.fixture.activate_initial(
            "publisher_channel",
            sequence=7,
        )
        candidate = self.fixture.candidate(
            "exact_release",
            "cross-mode-integrity",
            "1.0.1",
        )
        request = self.fixture.request(
            candidate_root=candidate,
            canonical_root=self.fixture.first.canonical_root,
            active_receipt_sha256=active.active_receipt_sha256,
            mode="exact_release",
            revision="a" * 40,
        )

        with self.assertRaisesRegex(
            self.fixture.deployment.DeploymentError,
            "integrity-rejected/immutable-release-reused",
        ):
            self.fixture.deployment.prepare_deployment(request)

    def test_publisher_forward_activates_and_captures_retained_evidence(self) -> None:
        _, active, _, _ = self.fixture.activate_initial(
            "publisher_channel",
            sequence=7,
        )
        candidate = self.fixture.candidate(
            "publisher_channel",
            "publisher-activation",
            "1.0.1",
        )
        request = self.fixture.request(
            candidate_root=candidate,
            canonical_root=self.fixture.first.canonical_root,
            active_receipt_sha256=active.active_receipt_sha256,
            mode="publisher_channel",
            revision="b" * 40,
            sequence=8,
        )

        staged, result = self.fixture.activate(
            request,
            "routine-compatible-forward",
        )
        captured = self.fixture.deployment._capture_active_deployment_precondition(
            self.fixture.first.canonical_root,
            result.active_receipt_sha256,
        )

        self.assertEqual(result.outcome, "candidate-active")
        self.assertEqual(staged.deployment_value["source"]["mode"], "publisher_channel")
        self.assertEqual(captured.active_source.source_mode, "publisher_channel")
        self.assertEqual(captured.active_source.lineage["sequence"], 8)

    def test_routine_source_boundary_activates_with_its_exact_purpose(self) -> None:
        _, active, _, _ = self.fixture.activate_initial(
            "publisher_channel",
            sequence=7,
        )
        candidate = self.fixture.candidate(
            "publisher_channel",
            "publisher-boundary",
            "1.0.1",
        )
        request = self.fixture.request(
            candidate_root=candidate,
            canonical_root=self.fixture.first.canonical_root,
            active_receipt_sha256=active.active_receipt_sha256,
            mode="publisher_channel",
            revision="b" * 40,
            sequence=6,
        )

        staged, result = self.fixture.activate(request, "source-boundary-change")
        captured = self.fixture.deployment._capture_active_deployment_precondition(
            self.fixture.first.canonical_root,
            result.active_receipt_sha256,
        )

        self.assertEqual(result.outcome, "candidate-active")
        self.assertEqual(
            staged.deployment_value["authorization"]["purpose"],
            "source-boundary-change",
        )
        self.assertEqual(captured.active_source.lineage["sequence"], 6)


if __name__ == "__main__":
    unittest.main()
