from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ._bridge_transition_prepare_support import (
    TEST_ONLY_RELEASE_VERSIONS,
    OutboundBridgePreparationFixture,
    prepared_bridge_field_surface,
    transition_facts_field_surface,
)
from ._freeze5_upgrade_recovery_support import set_resealed_client_profile
from ._source_evidence_support import exact_tree_state
from ._support import canonical_bytes, content_document, sha256

SHA256 = re.compile(r"[0-9a-f]{64}").fullmatch


class BridgeTransitionPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.fixture = OutboundBridgePreparationFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_installed_bridge_prepares_current_candidate_without_writes(self) -> None:
        prepared = self.fixture.prepare()
        deployment = prepared.deployment
        self.addCleanup(self.fixture.close, prepared)

        self.assertEqual(TEST_ONLY_RELEASE_VERSIONS, ("0.1.0", "0.1.1", "1.0.0"))
        self.assertFalse(prepared.staging_root.exists())
        for external in (
            prepared.bridge_identity_path,
            prepared.release_manifest_path,
            prepared.endpoint_projection_path,
        ):
            self.assertFalse(external.is_relative_to(prepared.canonical_root))
            self.assertFalse(external.is_relative_to(prepared.bridge_candidate_root))
            self.assertFalse(external.is_relative_to(prepared.tw4_candidate_root))
            self.assertFalse(external.is_relative_to(prepared.staging_root))

        endpoint = json.loads(prepared.endpoint_projection_raw)
        self.assertEqual(
            endpoint["retained_receipts"],
            list(prepared.retained_receipts),
        )
        self.assertEqual(
            endpoint["retained_results"],
            list(prepared.retained_results),
        )
        self.assertEqual(len(prepared.retained_receipts), 2)
        self.assertEqual(len(prepared.retained_results), 2)
        self.assertEqual(
            prepared.retained_receipts[-1]["sha256"],
            prepared.starting_active_receipt_sha256,
        )

        before = exact_tree_state(self.root)
        original_capture = deployment._capture_active_deployment_precondition
        original_snapshot = deployment._snapshot_candidate_tree
        events: list[str] = []

        def capture_authority(*args: object, **kwargs: object) -> object:
            captured = original_capture(*args, **kwargs)
            events.append("exact-b1-authority")
            return captured

        def inspect_candidate(*args: object, **kwargs: object) -> object:
            if not events or events[0] != "exact-b1-authority":
                raise AssertionError("candidate inspected before exact B1 authority")
            events.append("tw4-candidate")
            return original_snapshot(*args, **kwargs)

        try:
            with (
                mock.patch.object(
                    deployment,
                    "_capture_active_deployment_precondition",
                    side_effect=capture_authority,
                ),
                mock.patch.object(
                    deployment,
                    "_snapshot_candidate_tree",
                    side_effect=inspect_candidate,
                ),
            ):
                first = deployment.prepare_bridge_transition(
                    prepared.request,
                    prepared.staging_root,
                )
                self.assertEqual(events[0], "exact-b1-authority")
                self.assertIn("tw4-candidate", events)
                events.clear()
                second = deployment.prepare_bridge_transition(
                    prepared.request,
                    prepared.staging_root,
                )
        finally:
            self.assertEqual(exact_tree_state(self.root), before)
            self.assertFalse(prepared.staging_root.exists())

        self.assertEqual(first, second)
        self.assertEqual(events[0], "exact-b1-authority")
        self.assertIn("tw4-candidate", events)
        retained_chain = tuple(
            sorted(
                (
                    {
                        "sequence": value["sequence"],
                        "sha256": receipt_sha256,
                    }
                    for receipt_sha256, value, _ in (
                        first.plan.precondition.retained_chain.deployment_receipts
                    )
                ),
                key=lambda item: item["sequence"],
            )
        )
        retained_results = tuple(
            {
                "path": path,
                "sha256": sha256(raw),
            }
            for path, raw in sorted(
                first.plan.precondition.retained_result_raws.items()
            )
        )
        self.assertEqual(retained_chain, prepared.retained_receipts)
        self.assertEqual(retained_results, prepared.retained_results)
        self.assertEqual(
            prepared_bridge_field_surface(deployment),
            ("plan", "authorization_facts", "transition_authorization_facts"),
        )
        self.assertEqual(
            transition_facts_field_surface(deployment),
            (
                "canonical_root",
                "staging_root",
                "effective_uid",
                "plan_sha256",
                "maintenance_transaction_sha256",
                "expected_deployment_authorization_sha256",
                "expected_active_receipt_core_sha256",
                "bridge_identity_sha256",
                "release_manifest_sha256",
                "endpoint_projection_sha256",
                "execution_class",
            ),
        )

        facts = first.transition_authorization_facts
        self.assertEqual(facts.canonical_root, prepared.canonical_root)
        self.assertEqual(facts.staging_root, prepared.staging_root)
        self.assertEqual(facts.plan_sha256, first.plan.plan_sha256)
        self.assertEqual(
            facts.maintenance_transaction_sha256,
            prepared.request.deployment.maintenance_transaction_sha256,
        )
        self.assertEqual(
            facts.bridge_identity_sha256,
            sha256(prepared.bridge_identity_raw),
        )
        self.assertEqual(
            facts.release_manifest_sha256,
            sha256(prepared.release_manifest_raw),
        )
        self.assertEqual(
            facts.endpoint_projection_sha256,
            sha256(prepared.endpoint_projection_raw),
        )
        self.assertEqual(facts.execution_class, "isolated-rehearsal")
        self.assertIsNotNone(SHA256(facts.expected_deployment_authorization_sha256))
        self.assertIsNotNone(SHA256(facts.expected_active_receipt_core_sha256))

    @unittest.skipUnless(sys.platform == "darwin", "macOS ACL semantics required")
    def test_bridge_endpoint_rejects_a_permissive_retained_result_acl(self) -> None:
        prepared = self.fixture.prepare()
        deployment = prepared.deployment
        self.addCleanup(self.fixture.close, prepared)
        retained_results = prepared.canonical_root / "transaction-results"
        subprocess.run(
            ["/bin/chmod", "+a", "everyone allow read", str(retained_results)],
            check=True,
        )
        forbidden_candidate = mock.Mock(
            side_effect=AssertionError(
                "candidate inspected before retained endpoint ACL validation"
            )
        )

        try:
            before = exact_tree_state(self.root)
            acl_evidence = subprocess.run(
                ["/bin/ls", "-led", str(retained_results)],
                check=True,
                capture_output=True,
            ).stdout
            with (
                mock.patch.object(
                    deployment,
                    "_snapshot_candidate_tree",
                    forbidden_candidate,
                ),
                self.assertRaisesRegex(
                    deployment.DeploymentError,
                    "^transaction result directory has a permissive ACL entry$",
                ),
            ):
                deployment.prepare_bridge_transition(
                    prepared.request,
                    prepared.staging_root,
                )

            forbidden_candidate.assert_not_called()
            self.assertEqual(exact_tree_state(self.root), before)
            self.assertEqual(
                subprocess.run(
                    ["/bin/ls", "-led", str(retained_results)],
                    check=True,
                    capture_output=True,
                ).stdout,
                acl_evidence,
            )
            self.assertFalse(prepared.staging_root.exists())
        finally:
            subprocess.run(["/bin/chmod", "-N", str(retained_results)], check=True)

    def test_bridge_preparation_rejects_resealed_non_b1_client_profile_first(
        self,
    ) -> None:
        prepared = self.fixture.prepare(
            bridge_candidate_mutator=lambda candidate: set_resealed_client_profile(
                candidate / "client" / "task_witness_client.py",
                "tw4-current",
            )
        )
        deployment = prepared.deployment
        self.addCleanup(self.fixture.close, prepared)
        before = exact_tree_state(self.root)

        with (
            mock.patch.object(
                deployment,
                "_snapshot_candidate_tree",
                side_effect=AssertionError(
                    "candidate inspected before B1 client profile validation"
                ),
            ),
            self.assertRaisesRegex(
                deployment.DeploymentError,
                "bridge transition predecessor client profile disagrees",
            ),
        ):
            deployment.prepare_bridge_transition(
                prepared.request,
                prepared.staging_root,
            )

        self.assertEqual(exact_tree_state(self.root), before)
        self.assertFalse(prepared.staging_root.exists())

    def test_bridge_history_must_reproduce_exact_identity_record_before_candidate(
        self,
    ) -> None:
        prepared = self.fixture.prepare()
        deployment = prepared.deployment
        self.addCleanup(self.fixture.close, prepared)
        original = json.loads(prepared.release_manifest_raw)

        mutations = (
            ("freeze5-tree", ("freeze5", "tree_sha1"), "f" * 40),
            ("bridge-tree", ("bridge", "tree_sha1"), "f" * 40),
            (
                "identity-digest",
                ("bridge_identity_sha256",),
                "0" * 64,
            ),
            (
                "provenance-digest",
                ("bridge_provenance_sha256",),
                "9" * 64,
            ),
        )
        for label, path, replacement in mutations:
            value = json.loads(prepared.release_manifest_raw)
            selected = value["bridge_history"]
            for component in path[:-1]:
                selected = selected[component]
            selected[path[-1]] = replacement
            value.pop("content_sha256")
            raw = canonical_bytes(content_document(value))
            prepared.release_manifest_path.write_bytes(raw)
            prepared.release_manifest_path.chmod(0o600)
            before = exact_tree_state(self.root)

            with (
                self.subTest(mutation=label),
                mock.patch.object(
                    deployment,
                    "_snapshot_candidate_tree",
                    side_effect=AssertionError(
                        "candidate inspected before bridge identity validation"
                    ),
                ),
                self.assertRaisesRegex(
                    deployment.DeploymentError,
                    "bridge transition manifest identity record disagrees",
                ),
            ):
                deployment.prepare_bridge_transition(
                    prepared.request,
                    prepared.staging_root,
                )

            self.assertEqual(exact_tree_state(self.root), before)
            self.assertFalse(prepared.staging_root.exists())

        prepared.release_manifest_path.write_bytes(
            canonical_bytes(
                content_document(
                    {
                        key: value
                        for key, value in original.items()
                        if key != "content_sha256"
                    }
                )
            )
        )
        prepared.release_manifest_path.chmod(0o600)


if __name__ == "__main__":
    unittest.main()
