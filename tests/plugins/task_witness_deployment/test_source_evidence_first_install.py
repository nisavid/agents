from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

from ._activation_support import (
    PreparedActivation,
    SmokeChildBoundary,
    expected_smoke_envelope,
)
from ._source_evidence_support import (
    SourceEvidenceFixture,
    first_install_authorization_raw,
    public_request_field_names,
)
from ._support import canonical_document, content_document, sha256


class FirstInstallSourceEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = SourceEvidenceFixture(Path(self.temporary.name).resolve())

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_publisher_channel_prepare_accepts_exact_evidence_without_writes(
        self,
    ) -> None:
        before = self.fixture.trees()
        request = self.fixture.request(
            self.fixture.publisher_selection_raw,
            self.fixture.publisher_evidence(),
        )

        prepared = self.fixture.module.prepare_first_install(request)

        self.assertEqual(prepared.plan.source.source_mode, "publisher_channel")
        self.assertEqual(prepared.plan.source.revision, "a" * 40)
        self.assertEqual(
            prepared.plan.source.subtree_sha256,
            self.fixture.expected_subtree_sha256,
        )
        self.fixture.assert_trees_unchanged(self, before)

    def test_exact_release_prepare_accepts_explicit_empty_evidence_without_writes(
        self,
    ) -> None:
        before = self.fixture.trees()
        request = self.fixture.request(
            self.fixture.exact_release_selection_raw,
            self.fixture.exact_release_evidence(),
        )

        prepared = self.fixture.module.prepare_first_install(request)

        self.assertEqual(prepared.plan.source.source_mode, "exact_release")
        self.assertEqual(prepared.plan.source.revision, "a" * 40)
        self.fixture.assert_trees_unchanged(self, before)

    def test_publisher_and_exact_stages_are_parseable_by_the_staged_client(
        self,
    ) -> None:
        cases = (
            (
                "publisher",
                self.fixture.publisher_selection_raw,
                self.fixture.publisher_evidence(),
            ),
            (
                "exact",
                self.fixture.exact_release_selection_raw,
                self.fixture.exact_release_evidence(),
            ),
        )
        for name, selection_raw, evidence in cases:
            with self.subTest(name=name):
                canonical_root = self.fixture.root / f"canonical-{name}"
                canonical_root.mkdir(mode=0o700)
                activation_lock = canonical_root / "activation.lock"
                activation_lock.write_bytes(b"")
                activation_lock.chmod(0o600)
                request = self.fixture.request(
                    selection_raw,
                    evidence,
                    canonical_root=canonical_root,
                )
                prepared = self.fixture.module.prepare_first_install(request)
                staged = self.fixture.module.stage_first_install(
                    request,
                    first_install_authorization_raw(prepared),
                    self.fixture.root / f"stage-{name}",
                )
                receipt = next(
                    item
                    for item in staged.artifacts
                    if item.role == "deployment-receipt"
                )
                client = next(
                    item for item in staged.artifacts if item.role == "client"
                )
                specification = importlib.util.spec_from_file_location(
                    f"task_witness_client_{name}",
                    client.staged_path,
                )
                self.assertIsNotNone(specification)
                self.assertIsNotNone(specification.loader)
                module = importlib.util.module_from_spec(specification)
                specification.loader.exec_module(module)

                source = module._validate_receipt_source(
                    json.loads(receipt.raw)["source"]
                )

                self.assertEqual(source["mode"], json.loads(selection_raw)["mode"])

    def test_new_mode_receipt_source_rejects_cross_mode_missing_or_extra_fields(
        self,
    ) -> None:
        request = self.fixture.request(
            self.fixture.publisher_selection_raw,
            self.fixture.publisher_evidence(),
        )
        prepared = self.fixture.module.prepare_first_install(request)
        staged = self.fixture.module.stage_first_install(
            request,
            first_install_authorization_raw(prepared),
            self.fixture.root / "stage-receipt-negatives",
        )
        receipt = json.loads(staged.deployment_raw)
        client = next(item for item in staged.artifacts if item.role == "client")
        specification = importlib.util.spec_from_file_location(
            "task_witness_client_receipt_negatives",
            client.staged_path,
        )
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        cases = {}
        wrong_kind = deepcopy(receipt["source"])
        wrong_kind["source_evidence"]["kind"] = "exact_release"
        cases["wrong-kind"] = wrong_kind
        missing_record = deepcopy(receipt["source"])
        missing_record["source_evidence"].pop("publisher_record_sha256")
        cases["missing-record"] = missing_record
        exact_extra = deepcopy(receipt["source"])
        exact_extra["mode"] = "exact_release"
        exact_extra["details"] = {
            "trust_class": "operator-pinned",
            "revision": exact_extra["revision"],
            "subtree_sha256": exact_extra["subtree_sha256"],
        }
        exact_extra["source_evidence"] = {
            "kind": "exact_release",
            "source_evidence_sha256": self.fixture.module._digest(
                {
                    "contract": "task-witness-source-evidence-v1",
                    "mode": "exact_release",
                }
            ),
            "publisher_record_sha256": "0" * 64,
        }
        cases["exact-extra"] = exact_extra
        for name, source in cases.items():
            with self.subTest(name=name), self.assertRaises((TypeError, ValueError)):
                module._validate_receipt_source(source)

    def test_retained_source_parsers_reject_empty_byte_identities_for_all_modes(
        self,
    ) -> None:
        modes = (
            (
                "harness",
                self.fixture.harness_selection_raw,
                self.fixture.harness_evidence(),
                "manager_binding_sha256",
                "manager_receipt_sha256",
            ),
            (
                "publisher",
                self.fixture.publisher_selection_raw,
                self.fixture.publisher_evidence(),
                "publisher_binding_sha256",
                "publisher_record_sha256",
            ),
        )
        empty_sha256 = sha256(b"")
        for name, selection_raw, evidence, binding_field, record_field in modes:
            canonical_root = self.fixture.root / f"canonical-empty-{name}"
            canonical_root.mkdir(mode=0o700)
            activation_lock = canonical_root / "activation.lock"
            activation_lock.write_bytes(b"")
            activation_lock.chmod(0o600)
            request = self.fixture.request(
                selection_raw,
                evidence,
                canonical_root=canonical_root,
            )
            prepared = self.fixture.module.prepare_first_install(request)
            staged = self.fixture.module.stage_first_install(
                request,
                first_install_authorization_raw(prepared),
                self.fixture.root / f"stage-empty-{name}",
            )
            receipt = json.loads(staged.deployment_raw)
            client = next(item for item in staged.artifacts if item.role == "client")
            specification = importlib.util.spec_from_file_location(
                f"task_witness_client_empty_{name}",
                client.staged_path,
            )
            self.assertIsNotNone(specification)
            self.assertIsNotNone(specification.loader)
            module = importlib.util.module_from_spec(specification)
            specification.loader.exec_module(module)

            for field in (binding_field, record_field):
                with self.subTest(mode=name, field=field):
                    source = deepcopy(receipt["source"])
                    source["source_evidence"][field] = empty_sha256
                    source["source_evidence"]["source_evidence_sha256"] = (
                        self.fixture.module._digest(
                            {
                                "contract": "task-witness-source-evidence-v1",
                                "mode": source["mode"],
                                "binding_sha256": source["source_evidence"][
                                    binding_field
                                ],
                                "record_sha256": source["source_evidence"][
                                    record_field
                                ],
                            }
                        )
                    )
                    with self.assertRaisesRegex(
                        self.fixture.module.DeploymentError,
                        "empty-byte identity",
                    ):
                        self.fixture.module._receipt_source_evidence(
                            source,
                            "test retained source",
                        )
                    with self.assertRaisesRegex(ValueError, "empty-byte identity"):
                        module._validate_receipt_source(source)

    def test_publisher_claims_cross_bind_before_candidate_inspection(self) -> None:
        base = json.loads(self.fixture.publisher_binding_raw)
        changes = {
            "publisher": ("publisher_id", "other"),
            "repository": ("repository_id", "other/agents"),
            "revision": ("revision", "b" * 40),
            "channel": ("channel", "edge"),
            "trust": ("source_trust_class", "other-trust"),
            "lineage": (
                "lineage",
                {"lineage_id": "other-stable", "sequence": 7},
            ),
        }
        for name, (field, value) in changes.items():
            with self.subTest(name=name):
                changed = deepcopy(base)
                changed["claims"][field] = value
                changed.pop("content_sha256")
                binding_raw = self.fixture.module._canonical_document(
                    {
                        **changed,
                        "content_sha256": self.fixture.module._digest(changed),
                    }
                )
                request = self.fixture.request(
                    self.fixture.publisher_selection_raw,
                    self.fixture.publisher_evidence(binding_raw=binding_raw),
                )
                with (
                    mock.patch.object(
                        self.fixture.module,
                        "_snapshot_candidate_tree",
                        side_effect=AssertionError("candidate inspected"),
                    ) as snapshot,
                    self.assertRaises(self.fixture.module.DeploymentError),
                ):
                    self.fixture.module.prepare_first_install(request)
                snapshot.assert_not_called()

    def test_new_mode_authorization_binds_the_aggregate_source_evidence(self) -> None:
        request = self.fixture.request(
            self.fixture.publisher_selection_raw,
            self.fixture.publisher_evidence(),
        )
        prepared = self.fixture.module.prepare_first_install(request)
        raw = first_install_authorization_raw(prepared)
        value = json.loads(raw)
        self.assertEqual(
            value["source_evidence_sha256"],
            prepared.plan.source.source_evidence_sha256,
        )
        self.assertNotEqual(
            value["source_evidence_sha256"],
            self.fixture.module.hashlib.sha256(
                self.fixture.publisher_record_raw
            ).hexdigest(),
        )
        value["source_evidence_sha256"] = "0" * 64
        value.pop("content_sha256")
        changed = self.fixture.module._canonical_document(
            {**value, "content_sha256": self.fixture.module._digest(value)}
        )
        stage = self.fixture.root / "stage-bad-evidence-auth"
        with self.assertRaises(self.fixture.module.DeploymentError):
            self.fixture.module.stage_first_install(request, changed, stage)
        self.assertFalse(stage.exists())

    def test_new_modes_activate_and_capture_their_retained_source(self) -> None:
        cases = (
            (
                "publisher",
                self.fixture.publisher_selection_raw,
                self.fixture.publisher_evidence(),
            ),
            (
                "exact",
                self.fixture.exact_release_selection_raw,
                self.fixture.exact_release_evidence(),
            ),
        )
        for name, selection_raw, evidence in cases:
            with self.subTest(name=name):
                canonical_root = self.fixture.root / f"active-{name}"
                canonical_root.mkdir(mode=0o700)
                activation_lock = canonical_root / "activation.lock"
                activation_lock.write_bytes(b"")
                activation_lock.chmod(0o600)
                request = self.fixture.request(
                    selection_raw,
                    evidence,
                    canonical_root=canonical_root,
                )
                prepared = self.fixture.module.prepare_first_install(request)
                authorization_raw = first_install_authorization_raw(prepared)
                staged = self.fixture.module.stage_first_install(
                    request,
                    authorization_raw,
                    self.fixture.root / f"stage-activate-{name}",
                )
                activation = PreparedActivation(
                    request,
                    authorization_raw,
                    staged,
                    self.fixture.module.verify_deployment_stage(staged.stage_path),
                    canonical_root,
                    activation_lock,
                )
                child = SmokeChildBoundary(
                    activation,
                    expected_smoke_envelope(staged),
                )
                with mock.patch.object(
                    self.fixture.module,
                    "_spawn_activation_smoke_child",
                    child,
                ):
                    result = self.fixture.module.activate_staged(
                        self.fixture.module.ActivationRequest(
                            request,
                            authorization_raw,
                            staged.stage_path,
                        )
                    )
                captured = self.fixture.module._capture_active_deployment_precondition(
                    canonical_root,
                    result.active_receipt_sha256,
                )
                self.assertEqual(
                    captured.active_source.source_mode,
                    json.loads(selection_raw)["mode"],
                )
                endpoint = self.fixture.module._rollback_endpoint_identity(
                    captured.receipt_sha256,
                    captured.receipt_value,
                )
                self.assertEqual(
                    endpoint.source["mode"],
                    json.loads(selection_raw)["mode"],
                )
                self.assertEqual(
                    endpoint.source["source_evidence_sha256"],
                    prepared.plan.source.source_evidence_sha256,
                )

    def test_request_surface_has_one_discriminated_source_evidence_slot(self) -> None:
        fields = public_request_field_names(self.fixture.module)

        self.assertIn("source_evidence", fields)
        self.assertNotIn("manager_binding_raw", fields)
        self.assertNotIn("manager_receipt_raw", fields)

    def test_source_evidence_variant_must_match_mode_before_candidate_inspection(
        self,
    ) -> None:
        cases = (
            (
                "publisher-with-harness",
                self.fixture.publisher_selection_raw,
                self.fixture.harness_evidence(),
            ),
            (
                "publisher-with-exact",
                self.fixture.publisher_selection_raw,
                self.fixture.exact_release_evidence(),
            ),
            (
                "exact-with-publisher",
                self.fixture.exact_release_selection_raw,
                self.fixture.publisher_evidence(),
            ),
            (
                "harness-with-exact",
                self.fixture.harness_selection_raw,
                self.fixture.exact_release_evidence(),
            ),
        )
        for name, source_selection_raw, source_evidence in cases:
            with self.subTest(name=name):
                before = self.fixture.trees()
                request = self.fixture.request(
                    source_selection_raw,
                    source_evidence,
                )
                with (
                    mock.patch.object(
                        self.fixture.module,
                        "_snapshot_candidate_tree",
                        side_effect=AssertionError(
                            "candidate inspection preceded source-evidence validation"
                        ),
                    ) as snapshot,
                    self.assertRaisesRegex(
                        self.fixture.module.DeploymentError,
                        "source evidence.*mode|mode.*evidence",
                    ),
                ):
                    self.fixture.module.prepare_first_install(request)
                snapshot.assert_not_called()
                self.fixture.assert_trees_unchanged(self, before)

    def test_bound_source_evidence_requires_exact_bytes_before_candidate_inspection(
        self,
    ) -> None:
        cases = (
            (
                "publisher-binding-bytearray",
                self.fixture.publisher_selection_raw,
                self.fixture.publisher_evidence(
                    binding_raw=bytearray(self.fixture.publisher_binding_raw)
                ),
            ),
            (
                "publisher-record-memoryview",
                self.fixture.publisher_selection_raw,
                self.fixture.publisher_evidence(
                    publisher_record_raw=memoryview(self.fixture.publisher_record_raw)
                ),
            ),
            (
                "harness-binding-text",
                self.fixture.harness_selection_raw,
                self.fixture.harness_evidence(binding_raw="not exact bytes"),
            ),
            (
                "harness-receipt-bytearray",
                self.fixture.harness_selection_raw,
                self.fixture.harness_evidence(
                    receipt_raw=bytearray(self.fixture.harness_receipt_raw)
                ),
            ),
        )
        for name, source_selection_raw, source_evidence in cases:
            with self.subTest(name=name):
                before = self.fixture.trees()
                request = self.fixture.request(
                    source_selection_raw,
                    source_evidence,
                )
                with (
                    mock.patch.object(
                        self.fixture.module,
                        "_snapshot_candidate_tree",
                        side_effect=AssertionError(
                            "candidate inspection preceded source-evidence validation"
                        ),
                    ) as snapshot,
                    self.assertRaisesRegex(
                        self.fixture.module.DeploymentError,
                        "source evidence.*exact bytes|exact bytes.*source evidence",
                    ),
                ):
                    self.fixture.module.prepare_first_install(request)
                snapshot.assert_not_called()
                self.fixture.assert_trees_unchanged(self, before)

    def test_harness_evidence_rejects_empty_receipt_before_candidate_inspection(
        self,
    ) -> None:
        empty_receipt_sha256 = sha256(b"")
        binding = json.loads(self.fixture.harness_binding_raw)
        binding["manager_receipt_sha256"] = empty_receipt_sha256
        binding_raw = canonical_document(
            content_document(
                {
                    key: value
                    for key, value in binding.items()
                    if key != "content_sha256"
                }
            )
        )
        selection = json.loads(self.fixture.harness_selection_raw)
        selection["details"]["manager_receipt_sha256"] = empty_receipt_sha256
        selection_raw = canonical_document(
            content_document(
                {
                    key: value
                    for key, value in selection.items()
                    if key != "content_sha256"
                }
            )
        )
        request = self.fixture.request(
            selection_raw,
            self.fixture.harness_evidence(
                binding_raw=binding_raw,
                receipt_raw=b"",
            ),
        )
        before = self.fixture.trees()

        with (
            mock.patch.object(
                self.fixture.module,
                "_snapshot_candidate_tree",
                side_effect=AssertionError(
                    "candidate inspection preceded source-evidence validation"
                ),
            ) as snapshot,
            self.assertRaisesRegex(
                self.fixture.module.DeploymentError,
                "manager receipt.*byte contract",
            ),
        ):
            self.fixture.module.prepare_first_install(request)

        snapshot.assert_not_called()
        self.fixture.assert_trees_unchanged(self, before)

    def test_source_evidence_constructors_reject_missing_extra_or_mixed_fields(
        self,
    ) -> None:
        before = self.fixture.trees()
        publisher = self.fixture.evidence_type("PublisherChannelEvidence")
        harness = self.fixture.evidence_type("HarnessSnapshotEvidence")

        with self.assertRaises(TypeError):
            publisher(binding_raw=self.fixture.publisher_binding_raw)
        with self.assertRaises(TypeError):
            publisher(
                binding_raw=self.fixture.publisher_binding_raw,
                publisher_record_raw=self.fixture.publisher_record_raw,
                receipt_raw=b"manager sentinel forbidden",
            )
        with self.assertRaises(TypeError):
            harness(binding_raw=self.fixture.harness_binding_raw)
        with self.assertRaises(TypeError):
            harness(
                binding_raw=self.fixture.harness_binding_raw,
                receipt_raw=self.fixture.harness_receipt_raw,
                publisher_record_raw=b"publisher sentinel forbidden",
            )

        self.fixture.assert_trees_unchanged(self, before)


if __name__ == "__main__":
    unittest.main()
