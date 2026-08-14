from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from ._activation_support import IndependentActivationLockHolder, canonical_value
from ._routine_support import RoutineDeploymentFixture
from ._support import canonical_bytes, canonical_document, sha256, validator_identity


class RoutineTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.fixture = RoutineDeploymentFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _provider_role_identity(category, role):
        identifiers = {
            "producers": "producer_id",
            "issuers": "issuer_id",
            "validators": "validator_id",
        }
        return (
            role[identifiers[category]],
            role["contract"],
            canonical_bytes(role),
        )

    def _reproject_provider_roles(self, receipt) -> None:
        receipt["role_inventory"] = {}
        for category in ("producers", "issuers", "validators"):
            for provider in receipt["providers"]:
                provider[category].sort(
                    key=lambda role: self._provider_role_identity(category, role)
                )
            roles = [
                json.loads(json.dumps(role))
                for provider in receipt["providers"]
                for role in provider[category]
            ]
            roles.sort(key=lambda role: self._provider_role_identity(category, role))
            receipt["role_inventory"][category] = roles

    @staticmethod
    def _provider_policy_projection(provider):
        return {
            "plugin_id": provider["plugin_id"],
            "authority_profile": provider["authority_profile"],
            "producers": [
                {
                    key: item[key]
                    for key in (
                        "producer_id",
                        "contract",
                        "validator_id",
                        "validator_contract",
                        "state",
                        "usable_for_new_publication",
                    )
                }
                for item in provider["producers"]
            ],
            "issuers": [
                {
                    key: item[key]
                    for key in (
                        "issuer_id",
                        "contract",
                        "capabilities",
                        "state",
                        "usable_for_new_publication",
                    )
                }
                for item in provider["issuers"]
            ],
            "validators": [
                {
                    key: item[key]
                    for key in (
                        "validator_id",
                        "contract",
                        "state",
                        "usable_for_new_publication",
                    )
                }
                for item in provider["validators"]
            ],
        }

    def _stage_external_update(self, name: str):
        fixture = RoutineDeploymentFixture(self.root / name)
        deployment = fixture.deployment()
        candidate_a, candidate_b, source_identity = fixture.provider_candidate_pair()
        initial, active_a = fixture.activate_initial(
            candidate_a,
            source_identity=source_identity,
        )
        request_b = fixture.request_for_candidate(
            initial.canonical_root,
            active_a.active_receipt_sha256,
            candidate_b,
            release_version="1.0.1",
            revision="b" * 40,
            sequence=8,
            **source_identity,
        )
        prepared_b = deployment.prepare_deployment(request_b)
        staged_b = deployment.stage_deployment(
            request_b,
            fixture.authorization_raw(prepared_b),
            fixture.root / "routine-stage-b",
        )
        return (
            deployment,
            fixture,
            initial,
            active_a,
            candidate_a,
            candidate_b,
            source_identity,
            staged_b,
        )

    def _stage_external_prior(self, name: str):
        state = self._stage_external_update(name)
        (
            deployment,
            fixture,
            initial,
            _,
            candidate_a,
            _,
            source_identity,
            staged_b,
        ) = state
        fixture.materialize_staged_candidate_as_live(staged_b)
        candidate_c = fixture.next_candidate(
            candidate_a,
            "provider-candidate-c",
            "1.0.2",
        )
        request_c = fixture.request_for_candidate(
            initial.canonical_root,
            sha256(staged_b.deployment_raw),
            candidate_c,
            release_version="1.0.2",
            revision="c" * 40,
            sequence=9,
            **source_identity,
        )
        prepared_c = deployment.prepare_deployment(request_c)
        staged_c = deployment.stage_deployment(
            request_c,
            fixture.authorization_raw(prepared_c),
            fixture.root / "routine-stage-c",
        )
        return (*state, candidate_c, request_c, staged_c)

    def _stage_intrinsic_prior(self, name: str):
        fixture = RoutineDeploymentFixture(self.root / name)
        deployment = fixture.deployment()
        initial, active_a = fixture.activate_initial()
        candidate_b = fixture.candidate_root()
        request_b = fixture.request_for_candidate(
            initial.canonical_root,
            active_a.active_receipt_sha256,
            candidate_b,
            release_version="1.0.1",
            revision="b" * 40,
            sequence=8,
        )
        prepared_b = deployment.prepare_deployment(request_b)
        staged_b = deployment.stage_deployment(
            request_b,
            fixture.authorization_raw(prepared_b),
            fixture.root / "routine-stage-b",
        )
        fixture.materialize_staged_candidate_as_live(staged_b)
        request_c = fixture.request_for_candidate(
            initial.canonical_root,
            sha256(staged_b.deployment_raw),
            fixture.next_candidate(candidate_b, "candidate-c", "1.0.2"),
            release_version="1.0.2",
            revision="c" * 40,
            sequence=9,
        )
        prepared_c = deployment.prepare_deployment(request_c)
        staged_c = deployment.stage_deployment(
            request_c,
            fixture.authorization_raw(prepared_c),
            fixture.root / "routine-stage-c",
        )
        return deployment, fixture, staged_c

    def _materialize_rewritten_prior_chain(
        self,
        fixture: RoutineDeploymentFixture,
        staged,
        replaced_prior_sha256: str,
    ) -> None:
        stage = json.loads(Path(staged.stage_path).read_bytes())
        prior_artifact = next(
            item
            for item in stage["artifacts"]
            if item["role"] == "prior-deployment-alias"
        )
        prior_raw = Path(prior_artifact["staged"]["path"]).read_bytes()
        prior_sha256 = sha256(prior_raw)
        receipts = staged.plan.precondition.canonical_root / "receipts"
        retained = receipts / f"sha256-{prior_sha256}.json"
        retained.write_bytes(prior_raw)
        retained.chmod(0o600)
        replaced = receipts / f"sha256-{replaced_prior_sha256}.json"
        if replaced != retained:
            replaced.unlink()
        fixture.materialize_staged_candidate_as_live(staged)

    def test_prepare_captures_the_exact_active_receipt_and_classifies_forward(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        initial, active = self.fixture.activate_initial()
        request = self.fixture.request(
            initial.canonical_root,
            active.active_receipt_sha256,
        )

        prepared = deployment.prepare_deployment(request)

        self.assertEqual(
            prepared.authorization_facts.expected_active_receipt_sha256,
            active.active_receipt_sha256,
        )
        self.assertEqual(prepared.plan.classification.outcome, "compatible-forward")
        self.assertEqual(prepared.plan.classification.reason, "active-policy")
        self.assertEqual(
            prepared.plan.prior_receipt_sha256,
            active.active_receipt_sha256,
        )
        self.assertEqual(
            prepared.plan.value["operation"],
            "routine-payload",
        )

    def test_stage_binds_b_r_and_disjoint_prior_selector_preimages(self) -> None:
        deployment = self.fixture.deployment()
        initial, active = self.fixture.activate_initial()
        request = self.fixture.request(
            initial.canonical_root,
            active.active_receipt_sha256,
        )
        prepared = deployment.prepare_deployment(request)
        authorization_raw = self.fixture.authorization_raw(prepared)

        staged = deployment.stage_deployment(
            request,
            authorization_raw,
            self.root / "routine-stage",
        )
        verified = deployment.verify_deployment_stage(staged.stage_path)

        receipt_a = prepared.plan.precondition.receipt_value
        receipt_b = staged.deployment_value
        rollback = staged.rollback_value
        self.assertEqual(receipt_b["sequence"], receipt_a["sequence"] + 1)
        self.assertEqual(
            receipt_b["prior_receipt_sha256"],
            prepared.plan.prior_receipt_sha256,
        )
        self.assertEqual(
            receipt_b["authorization"]["purpose"], "routine-compatible-forward"
        )
        self.assertEqual(receipt_b["rollback"]["state"], "active")
        self.assertEqual(receipt_b["rollback"]["sha256"], sha256(staged.rollback_raw))
        self.assertEqual(rollback["state"], "active")
        self.assertEqual(
            rollback["precondition"]["active_receipt_sha256"],
            prepared.plan.prior_receipt_sha256,
        )
        self.assertEqual(
            rollback["prior_activation_unit"],
            prepared.plan.precondition.active_unit,
        )
        self.assertEqual(
            [item["role"] for item in rollback["selector_preimage"]],
            ["active-record", "deployment-alias"],
        )
        for item in rollback["selector_preimage"]:
            self.assertNotEqual(item["staged"]["path"], item["installed"]["path"])
            self.assertTrue(
                Path(item["staged"]["path"]).is_relative_to(staged.stage_path.parent)
            )
            self.assertTrue(
                Path(item["installed"]["path"]).is_relative_to(initial.canonical_root)
            )
        roles = [item.role for item in verified.artifacts]
        self.assertNotIn("controller", roles)
        self.assertNotIn("client", roles)
        self.assertNotIn("launcher", roles)
        self.assertNotIn("policy", roles)
        self.assertNotIn("shim", roles)
        self.assertEqual(roles.count("active-record"), 1)
        self.assertEqual(roles.count("deployment-alias"), 1)
        self.assertEqual(roles.count("prior-active-record"), 1)
        self.assertEqual(roles.count("prior-deployment-alias"), 1)

    def test_prepare_requires_a_bounded_shared_lock_before_capture(self) -> None:
        deployment = self.fixture.deployment()
        initial, active = self.fixture.activate_initial()
        request = self.fixture.request(
            initial.canonical_root,
            active.active_receipt_sha256,
        )

        with (
            IndependentActivationLockHolder(
                initial.activation_lock,
                __import__("fcntl").LOCK_EX,
                hold_seconds=None,
            ),
            mock.patch.dict(deployment.PROCESS_PROFILE, {"shared_lock_seconds": 0.04}),
            self.assertRaisesRegex(deployment.DeploymentError, "shared|timed out"),
        ):
            deployment.prepare_deployment(request)

    def test_prepare_rejects_transaction_or_selector_drift_during_capture(self) -> None:
        deployment = self.fixture.deployment()
        for drift in ("transaction", "selector"):
            with self.subTest(drift=drift):
                case = RoutineDeploymentFixture(self.root / drift)
                initial, active = case.activate_initial()
                request = case.request(
                    initial.canonical_root,
                    active.active_receipt_sha256,
                )
                original = deployment._read_activation_file
                injected = False

                def read_with_drift(*args, **kwargs):
                    nonlocal injected
                    raw = original(*args, **kwargs)
                    name = args[1]
                    if not injected and name == "deployment.json":
                        injected = True
                        if drift == "transaction":
                            transaction = initial.canonical_root / "transaction.json"
                            transaction.write_bytes(b"untrusted in-flight bytes\n")
                            transaction.chmod(0o600)
                        else:
                            selector = initial.canonical_root / "active.json"
                            selector.write_bytes(selector.read_bytes() + b" ")
                    return raw

                with (
                    mock.patch.object(
                        deployment,
                        "_read_activation_file",
                        side_effect=read_with_drift,
                    ),
                    self.assertRaises(deployment.DeploymentError),
                ):
                    deployment.prepare_deployment(request)

    def test_prepare_rejects_a_damaged_active_runtime_payload(self) -> None:
        deployment = self.fixture.deployment()
        initial, active = self.fixture.activate_initial()
        request = self.fixture.request(
            initial.canonical_root,
            active.active_receipt_sha256,
        )
        active_value = canonical_value(
            (initial.canonical_root / "active.json").read_bytes()
        )
        payload = active_value["payloads"][0]
        target = (
            initial.canonical_root
            / "generations"
            / active_value["generation"]
            / payload["relative_path"]
        )
        target.write_bytes(target.read_bytes() + b"damage")

        with self.assertRaisesRegex(deployment.DeploymentError, "runtime|payload"):
            deployment.prepare_deployment(request)

    def test_prepare_rejects_a_missing_retained_rollback_receipt(self) -> None:
        deployment = self.fixture.deployment()
        initial, active = self.fixture.activate_initial()
        request = self.fixture.request(
            initial.canonical_root,
            active.active_receipt_sha256,
        )
        receipt = canonical_value(
            (initial.canonical_root / "deployment.json").read_bytes()
        )
        Path(receipt["rollback"]["path"]).unlink()

        with self.assertRaisesRegex(deployment.DeploymentError, "rollback|receipt"):
            deployment.prepare_deployment(request)

    def test_prepare_allows_compatible_forward_trust_bytes_to_change(self) -> None:
        deployment = self.fixture.deployment()
        candidate_a, candidate_b, identity = self.fixture.provider_candidate_pair()
        initial, active = self.fixture.activate_initial(
            candidate_a,
            source_identity=identity,
        )
        request = self.fixture.request_for_candidate(
            initial.canonical_root,
            active.active_receipt_sha256,
            candidate_b,
            release_version="1.0.1",
            revision="b" * 40,
            sequence=8,
            **identity,
        )

        prepared = deployment.prepare_deployment(request)

        self.assertEqual(prepared.plan.classification.outcome, "compatible-forward")
        self.assertNotEqual(
            prepared.plan.precondition.receipt_value["trust_context"]["sha256"],
            prepared.plan.trust.context.sha256,
        )

    def test_prepare_validates_the_exact_recursive_active_receipt_chain(self) -> None:
        deployment = self.fixture.deployment()
        cases = ("valid", "missing-ancestor", "extra-ancestor", "rebound-prior-unit")
        for name in cases:
            with self.subTest(case=name):
                fixture = RoutineDeploymentFixture(self.root / name)
                initial, _, request_b, _, staged_b, _ = fixture.staged_routine()
                if name == "rebound-prior-unit":

                    def rebind_prior_unit(rollback, deployment_value, stage):
                        rollback["prior_activation_unit"]["control_set"]["client"][
                            "sha256"
                        ] = "1" * 64

                    fixture.rewrite_routine_stage(staged_b, rebind_prior_unit)
                fixture.materialize_staged_candidate_as_live(staged_b)
                deployment_raw = (
                    initial.canonical_root / "deployment.json"
                ).read_bytes()
                candidate_c = fixture.next_candidate(
                    request_b.candidate_root,
                    "candidate-c",
                    "1.0.2",
                )
                request_c = fixture.request_for_candidate(
                    initial.canonical_root,
                    sha256(deployment_raw),
                    candidate_c,
                    release_version="1.0.2",
                    revision="c" * 40,
                    sequence=9,
                )
                if name == "missing-ancestor":
                    prior = json.loads(deployment_raw)["prior_receipt_sha256"]
                    (
                        initial.canonical_root / "receipts" / f"sha256-{prior}.json"
                    ).unlink()
                elif name == "extra-ancestor":
                    extra = (
                        initial.canonical_root / "receipts" / f"sha256-{'f' * 64}.json"
                    )
                    extra.write_bytes(b"{}\n")
                    extra.chmod(0o600)

                if name == "valid":
                    prepared_c = deployment.prepare_deployment(request_c)
                    self.assertEqual(
                        prepared_c.plan.prior_receipt_sha256,
                        sha256(deployment_raw),
                    )
                else:
                    with self.assertRaisesRegex(
                        deployment.DeploymentError,
                        "ancestor|chain|receipt|inventory|unit|control",
                    ):
                        deployment.prepare_deployment(request_c)

    def test_verify_routine_stage_rejects_rehashed_semantic_tampering_without_writes(
        self,
    ) -> None:
        deployment = self.fixture.deployment()

        def wrong_root(rollback, deployment_value, stage):
            rollback["precondition"]["root_identity"][0] += 1

        def wrong_lock(rollback, deployment_value, stage):
            rollback["precondition"]["activation_lock_identity"][1] += 1

        def wrong_active_digest(rollback, deployment_value, stage):
            digest = "1" * 64
            rollback["precondition"]["active_receipt_sha256"] = digest
            deployment_value["prior_receipt_sha256"] = digest
            deployment_value["authorization"]["expected_active_receipt_sha256"] = digest

        def wrong_prior_binding(rollback, deployment_value, stage):
            rollback["prior_receipt"]["length"] += 1

        def wrong_prior_control(rollback, deployment_value, stage):
            rollback["prior_activation_unit"]["control_set"]["client"]["sha256"] = (
                "2" * 64
            )

        def wrong_prior_smoke(rollback, deployment_value, stage):
            rollback["prior_activation_unit"]["smoke"]["expected_envelope_sha256"] = (
                "3" * 64
            )
            rollback["smoke"] = json.loads(
                json.dumps(rollback["prior_activation_unit"]["smoke"])
            )

        def wrong_prior_selector(rollback, deployment_value, stage):
            artifact = next(
                item
                for item in stage["artifacts"]
                if item["role"] == "prior-active-record"
            )
            path = Path(artifact["staged"]["path"])
            raw = path.read_bytes() + b" "
            path.write_bytes(raw)
            path.chmod(0o600)
            staged_binding = RoutineDeploymentFixture._binding(path, raw, 0o600)
            installed_binding = RoutineDeploymentFixture._binding(
                Path(artifact["installed"]["path"]),
                raw,
                0o600,
            )
            artifact["staged"] = staged_binding
            artifact["installed"] = installed_binding
            unit = rollback["prior_activation_unit"]
            unit["active_record"] = installed_binding
            unit["smoke"]["expected_anchor"]["active_record_sha256"] = sha256(raw)
            unit["smoke"]["expected_envelope_sha256"] = "4" * 64
            rollback["smoke"] = json.loads(json.dumps(unit["smoke"]))
            rollback["selector_preimage"][0] = {
                "role": "active-record",
                "staged": staged_binding,
                "installed": installed_binding,
            }

        def wrong_prior_anchor_link(rollback, deployment_value, stage):
            active_artifact = next(
                item
                for item in stage["artifacts"]
                if item["role"] == "prior-active-record"
            )
            active_path = Path(active_artifact["staged"]["path"])
            active = json.loads(active_path.read_bytes())
            active["public_release"] = {
                "repository": "rewritten/prior",
                "revision": "8" * 40,
            }
            active = RoutineDeploymentFixture._recontent(active)
            active_raw = canonical_document(active)
            active_path.write_bytes(active_raw)
            active_path.chmod(0o600)
            active_staged = RoutineDeploymentFixture._binding(
                active_path,
                active_raw,
                0o600,
            )
            active_installed = RoutineDeploymentFixture._binding(
                Path(active_artifact["installed"]["path"]),
                active_raw,
                0o600,
            )
            active_artifact["staged"] = active_staged
            active_artifact["installed"] = active_installed

            prior_artifact = next(
                item
                for item in stage["artifacts"]
                if item["role"] == "prior-deployment-alias"
            )
            prior_path = Path(prior_artifact["staged"]["path"])
            prior = json.loads(prior_path.read_bytes())
            prior["active"]["record_sha256"] = sha256(active_raw)
            prior["active"]["public_release"] = active["public_release"]
            prior["smoke"]["expected_anchor"]["active_record_sha256"] = sha256(
                active_raw
            )
            prior["smoke"]["expected_envelope_sha256"] = "8" * 64
            prior = RoutineDeploymentFixture._recontent(prior)
            prior_raw = canonical_document(prior)
            prior_path.write_bytes(prior_raw)
            prior_path.chmod(0o600)
            prior_staged = RoutineDeploymentFixture._binding(
                prior_path,
                prior_raw,
                0o600,
            )
            prior_installed = RoutineDeploymentFixture._binding(
                Path(prior_artifact["installed"]["path"]),
                prior_raw,
                0o600,
            )
            prior_artifact["staged"] = prior_staged
            prior_artifact["installed"] = prior_installed
            prior_sha256 = sha256(prior_raw)
            rollback["precondition"]["active_receipt_sha256"] = prior_sha256
            rollback["prior_receipt"] = RoutineDeploymentFixture._binding(
                Path(stage["canonical_root"])
                / "receipts"
                / f"sha256-{prior_sha256}.json",
                prior_raw,
                0o600,
            )
            unit = rollback["prior_activation_unit"]
            unit["active_record"] = active_installed
            unit["deployment_receipt"] = prior_installed
            unit["smoke"] = json.loads(json.dumps(prior["smoke"]))
            rollback["smoke"] = json.loads(json.dumps(prior["smoke"]))
            rollback["selector_preimage"] = [
                {
                    "role": "active-record",
                    "staged": active_staged,
                    "installed": active_installed,
                },
                {
                    "role": "deployment-alias",
                    "staged": prior_staged,
                    "installed": prior_installed,
                },
            ]
            deployment_value["prior_receipt_sha256"] = prior_sha256
            deployment_value["authorization"]["expected_active_receipt_sha256"] = (
                prior_sha256
            )

        def wrong_process(rollback, deployment_value, stage):
            rollback["external_dependencies"]["process_profile"]["cwd"] = "/var"

        def wrong_parser(rollback, deployment_value, stage):
            rollback["external_dependencies"]["receipt_parser"][
                "deployment_receipt_contract"
            ] = "task-witness-deployment-receipt-v999"

        def wrong_authorized_active(rollback, deployment_value, stage):
            deployment_value["authorization"]["expected_active_receipt_sha256"] = (
                "5" * 64
            )

        def wrong_authorization_projection(rollback, deployment_value, stage):
            deployment_value["authorization"]["sha256"] = "6" * 64

        def wrong_history(rollback, deployment_value, stage):
            digest = "7" * 64
            deployment_value["historical_trust_contexts"].append(
                {
                    "path": str(
                        Path(stage["canonical_root"])
                        / "trust"
                        / "contexts"
                        / f"sha256-{digest}.json"
                    ),
                    "sha256": digest,
                    "state": "historical-usable",
                }
            )

        def extra_control(rollback, deployment_value, stage):
            raw = b"creation-disabled verifier must reject this role\n"
            staged_path = Path(stage["staging_root"]) / "extra" / "controller.py"
            staged_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            staged_path.parent.chmod(0o700)
            staged_path.write_bytes(raw)
            staged_path.chmod(0o600)
            installed_path = Path(stage["canonical_root"]) / "extra" / "controller.py"
            stage["artifacts"].append(
                {
                    "role": "controller",
                    "relative_path": "extra/controller.py",
                    "staged": RoutineDeploymentFixture._binding(
                        staged_path,
                        raw,
                        0o600,
                    ),
                    "installed": RoutineDeploymentFixture._binding(
                        installed_path,
                        raw,
                        0o500,
                    ),
                }
            )

        def missing_selector(rollback, deployment_value, stage):
            artifact = next(
                item
                for item in stage["artifacts"]
                if item["role"] == "prior-active-record"
            )
            Path(artifact["staged"]["path"]).unlink()
            stage["artifacts"].remove(artifact)

        cases = {
            "rollback-root": wrong_root,
            "rollback-lock": wrong_lock,
            "rollback-active-digest": wrong_active_digest,
            "rollback-prior-binding": wrong_prior_binding,
            "rollback-prior-control": wrong_prior_control,
            "rollback-prior-smoke": wrong_prior_smoke,
            "rollback-prior-selector": wrong_prior_selector,
            "rollback-prior-anchor": wrong_prior_anchor_link,
            "rollback-process": wrong_process,
            "rollback-parser": wrong_parser,
            "authorization-active": wrong_authorized_active,
            "authorization-projection": wrong_authorization_projection,
            "historical-trust": wrong_history,
            "extra-control": extra_control,
            "missing-selector": missing_selector,
        }
        for name, mutator in cases.items():
            with self.subTest(case=name):
                fixture = RoutineDeploymentFixture(self.root / f"verify-{name}")
                *_, staged, _ = fixture.staged_routine()
                stage_path = fixture.rewrite_routine_stage(staged, mutator)
                before = fixture.stage_snapshot(stage_path)

                with self.assertRaises(deployment.DeploymentError):
                    deployment.verify_deployment_stage(stage_path)

                self.assertEqual(fixture.stage_snapshot(stage_path), before)

    def test_verify_stage_rejects_nonprivate_dispositions_without_writes(self) -> None:
        deployment = self.fixture.deployment()
        for name in (
            "stage-mode",
            "stage-hardlink",
            "directory-mode",
            "directory-symlink",
        ):
            with self.subTest(case=name):
                fixture = RoutineDeploymentFixture(self.root / f"disposition-{name}")
                *_, staged, _ = fixture.staged_routine()
                stage_path = Path(staged.stage_path)
                if name == "stage-mode":
                    stage_path.chmod(0o644)
                elif name == "stage-hardlink":
                    os.link(stage_path, fixture.root / "stage-hardlink.json")
                elif name == "directory-mode":
                    (stage_path.parent / "candidate").chmod(0o755)
                else:
                    candidate = stage_path.parent / "candidate"
                    outside = fixture.root / "candidate-outside"
                    candidate.rename(outside)
                    candidate.symlink_to(outside, target_is_directory=True)
                before = fixture.stage_snapshot(stage_path)

                with self.assertRaises(deployment.DeploymentError):
                    deployment.verify_deployment_stage(stage_path)

                self.assertEqual(fixture.stage_snapshot(stage_path), before)

    def test_prepare_pins_the_private_recursive_receipt_inventory(self) -> None:
        deployment = self.fixture.deployment()
        for name in (
            "directory-mode",
            "hardlink",
            "symlink",
            "special",
            "inventory-aba",
        ):
            with self.subTest(case=name):
                fixture = RoutineDeploymentFixture(self.root / f"receipts-{name}")
                initial, active = fixture.activate_initial()
                request = fixture.request(
                    initial.canonical_root,
                    active.active_receipt_sha256,
                )
                receipts = initial.canonical_root / "receipts"
                receipt = initial.canonical_root / "deployment.json"
                receipt_value = json.loads(receipt.read_bytes())
                retained = Path(receipt_value["rollback"]["path"])
                if name == "directory-mode":
                    receipts.chmod(0o755)
                elif name == "hardlink":
                    os.link(retained, fixture.root / "retained-hardlink.json")
                elif name == "symlink":
                    outside = fixture.root / "retained-outside.json"
                    retained.rename(outside)
                    retained.symlink_to(outside)
                elif name == "special":
                    fifo = receipts / f"sha256-{'e' * 64}.json"
                    os.mkfifo(fifo, 0o600)

                if name != "inventory-aba":
                    with self.assertRaises(deployment.DeploymentError):
                        deployment.prepare_deployment(request)
                    continue

                original_listdir = deployment.os.listdir
                injected = False

                def listdir_with_aba(path):
                    nonlocal injected
                    names = original_listdir(path)
                    receipt_identity = receipts.lstat()
                    descriptor_matches = (
                        isinstance(path, int)
                        and os.fstat(path).st_dev == receipt_identity.st_dev
                        and os.fstat(path).st_ino == receipt_identity.st_ino
                    )
                    if not injected and (
                        descriptor_matches
                        or (
                            isinstance(path, (str, bytes, os.PathLike))
                            and Path(path) == receipts
                        )
                    ):
                        injected = True
                        extra = receipts / f"sha256-{'d' * 64}.json"
                        extra.write_bytes(b"{}\n")
                        extra.chmod(0o600)
                    return names

                with (
                    mock.patch.object(
                        deployment.os,
                        "listdir",
                        side_effect=listdir_with_aba,
                    ),
                    self.assertRaises(deployment.DeploymentError),
                ):
                    deployment.prepare_deployment(request)

    def test_creation_disabled_capture_closes_descriptors_on_inspection_failure(
        self,
    ) -> None:
        deployment = self.fixture.deployment()

        def descriptor_inventory() -> frozenset[int]:
            descriptors: set[int] = set()
            for descriptor in range(512):
                try:
                    os.fstat(descriptor)
                except OSError:
                    continue
                descriptors.add(descriptor)
            return frozenset(descriptors)

        for name in ("directory-acl", "file-acl", "file-read", "directory-list"):
            with self.subTest(case=name):
                fixture = RoutineDeploymentFixture(self.root / f"fd-stage-{name}")
                *_, staged, _ = fixture.staged_routine()
                stage_path = Path(staged.stage_path)
                before = descriptor_inventory()
                if name in {"directory-acl", "file-acl"}:
                    original = deployment._reject_macos_allow_acl
                    target = (
                        "staged deployment directory"
                        if name == "directory-acl"
                        else "staged deployment file"
                    )

                    def fail_acl(descriptor, label):
                        result = original(descriptor, label)
                        if label == target:
                            raise OSError(5, "injected ACL inspection failure")
                        return result

                    patcher = mock.patch.object(
                        deployment,
                        "_reject_macos_allow_acl",
                        side_effect=fail_acl,
                    )
                elif name == "file-read":
                    original = deployment._read_descriptor

                    def fail_read(descriptor, limit, label):
                        raw = original(descriptor, limit, label)
                        if label.startswith("staged deployment inventory "):
                            raise OSError(5, "injected stage read failure")
                        return raw

                    patcher = mock.patch.object(
                        deployment,
                        "_read_descriptor",
                        side_effect=fail_read,
                    )
                else:
                    original = deployment.os.listdir
                    target = (stage_path.parent / "candidate").lstat()
                    matching_calls = 0

                    def fail_listdir(path):
                        nonlocal matching_calls
                        names = original(path)
                        if isinstance(path, int):
                            metadata = os.fstat(path)
                            if (
                                metadata.st_dev == target.st_dev
                                and metadata.st_ino == target.st_ino
                            ):
                                matching_calls += 1
                                if matching_calls >= 2:
                                    raise OSError(
                                        5, "injected stage directory list failure"
                                    )
                        return names

                    patcher = mock.patch.object(
                        deployment.os,
                        "listdir",
                        side_effect=fail_listdir,
                    )
                with patcher, self.assertRaises(deployment.DeploymentError):
                    deployment.verify_deployment_stage(stage_path)
                self.assertEqual(descriptor_inventory(), before)

        fixture = RoutineDeploymentFixture(self.root / "fd-receipts-acl")
        initial, active = fixture.activate_initial()
        request = fixture.request(initial.canonical_root, active.active_receipt_sha256)
        original_acl = deployment._reject_macos_allow_acl

        def fail_receipt_acl(descriptor, label):
            result = original_acl(descriptor, label)
            if label == "retained deployment receipt":
                raise OSError(5, "injected retained receipt ACL failure")
            return result

        before = descriptor_inventory()
        with (
            mock.patch.object(
                deployment,
                "_reject_macos_allow_acl",
                side_effect=fail_receipt_acl,
            ),
            self.assertRaises(deployment.DeploymentError),
        ):
            deployment.prepare_deployment(request)
        self.assertEqual(descriptor_inventory(), before)

    def test_verify_stage_cross_binds_external_and_intrinsic_provider_declarations(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        for intrinsic in (False, True):
            name = "intrinsic" if intrinsic else "external"
            with self.subTest(provider=name):
                fixture = RoutineDeploymentFixture(self.root / f"provider-{name}")
                candidate_a, candidate_b, identity = fixture.provider_candidate_pair()
                initial, active = fixture.activate_initial(
                    candidate_a,
                    source_identity=identity,
                )
                request = fixture.request_for_candidate(
                    initial.canonical_root,
                    active.active_receipt_sha256,
                    candidate_b,
                    release_version="1.0.1",
                    revision="b" * 40,
                    sequence=8,
                    **identity,
                )
                prepared = deployment.prepare_deployment(request)
                authorization_raw = fixture.authorization_raw(prepared)
                staged = deployment.stage_deployment(
                    request,
                    authorization_raw,
                    fixture.root / "routine-stage",
                )

                def mutate_provider(rollback, deployment_value, stage):
                    provider = next(
                        item
                        for item in deployment_value["providers"]
                        if item["intrinsic"] is intrinsic
                    )
                    provider["declaration_sha256"] = "e" * 64 if intrinsic else "f" * 64

                stage_path = fixture.rewrite_routine_stage(staged, mutate_provider)
                before = fixture.stage_snapshot(stage_path)

                with self.assertRaises(deployment.DeploymentError):
                    deployment.verify_deployment_stage(stage_path)

                self.assertEqual(fixture.stage_snapshot(stage_path), before)

    def test_prepare_cross_binds_retained_provider_declarations(self) -> None:
        deployment = self.fixture.deployment()
        for name in (
            "valid",
            "external",
            "intrinsic",
            "duplicate-plugin-id",
            "provider-order",
            "module-order",
        ):
            with self.subTest(provider=name):
                fixture = RoutineDeploymentFixture(
                    self.root / f"retained-provider-{name}"
                )
                candidate_a, candidate_b, identity = fixture.provider_candidate_pair()
                initial, active = fixture.activate_initial(
                    candidate_a,
                    source_identity=identity,
                )
                request_b = fixture.request_for_candidate(
                    initial.canonical_root,
                    active.active_receipt_sha256,
                    candidate_b,
                    release_version="1.0.1",
                    revision="b" * 40,
                    sequence=8,
                    **identity,
                )
                prepared_b = deployment.prepare_deployment(request_b)
                staged_b = deployment.stage_deployment(
                    request_b,
                    fixture.authorization_raw(prepared_b),
                    fixture.root / "routine-stage-b",
                )
                if name != "valid":

                    def mutate_provider(rollback, deployment_value, stage):
                        if name in {"external", "intrinsic"}:
                            intrinsic = name == "intrinsic"
                            provider = next(
                                item
                                for item in deployment_value["providers"]
                                if item["intrinsic"] is intrinsic
                            )
                            provider["declaration_sha256"] = (
                                "e" * 64 if intrinsic else "f" * 64
                            )
                        elif name == "duplicate-plugin-id":
                            provider = next(
                                item
                                for item in deployment_value["providers"]
                                if item["intrinsic"] is False
                            )
                            provider["plugin_id"] = "task-witness"
                            deployment_value["source"]["plugin_id"] = "task-witness"
                        elif name == "provider-order":
                            deployment_value["providers"].reverse()
                        else:
                            provider = next(
                                item
                                for item in deployment_value["providers"]
                                if item["intrinsic"] is False
                            )
                            self.assertGreater(len(provider["retained_modules"]), 1)
                            provider["retained_modules"].reverse()

                    fixture.rewrite_routine_stage(staged_b, mutate_provider)
                fixture.materialize_staged_candidate_as_live(staged_b)
                live_b_raw = (initial.canonical_root / "deployment.json").read_bytes()
                candidate_c = fixture.next_candidate(
                    candidate_b,
                    "provider-candidate-c",
                    "1.0.2",
                )
                request_c = fixture.request_for_candidate(
                    initial.canonical_root,
                    sha256(live_b_raw),
                    candidate_c,
                    release_version="1.0.2",
                    revision="c" * 40,
                    sequence=9,
                    **identity,
                )
                if name == "valid":
                    prepared_c = deployment.prepare_deployment(request_c)
                    self.assertEqual(
                        prepared_c.plan.prior_receipt_sha256,
                        sha256(live_b_raw),
                    )
                else:
                    with self.assertRaisesRegex(
                        deployment.DeploymentError,
                        "provider|declaration|source|module",
                    ):
                        deployment.prepare_deployment(request_c)

    def test_verify_routine_stage_does_not_resolve_installed_prior_authority(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        for name in (
            "candidate-current-trust",
            "prior-current-trust",
            "prior-generation-payload",
            "prior-validator-module",
            "live-active-selector",
            "live-deployment-selector",
            "deep-ancestor-receipt",
        ):
            with self.subTest(case=name):
                fixture = RoutineDeploymentFixture(self.root / f"stage-local-{name}")
                candidate_a, candidate_b, identity = fixture.provider_candidate_pair()
                initial, active_a = fixture.activate_initial(
                    candidate_a,
                    source_identity=identity,
                )
                request_b = fixture.request_for_candidate(
                    initial.canonical_root,
                    active_a.active_receipt_sha256,
                    candidate_b,
                    release_version="1.0.1",
                    revision="b" * 40,
                    sequence=8,
                    **identity,
                )
                prepared_b = deployment.prepare_deployment(request_b)
                staged_b = deployment.stage_deployment(
                    request_b,
                    fixture.authorization_raw(prepared_b),
                    fixture.root / "routine-stage-b",
                )
                fixture.materialize_staged_candidate_as_live(staged_b)
                candidate_c = fixture.next_candidate(
                    candidate_a,
                    "provider-candidate-c",
                    "1.0.2",
                )
                request_c = fixture.request_for_candidate(
                    initial.canonical_root,
                    sha256(staged_b.deployment_raw),
                    candidate_c,
                    release_version="1.0.2",
                    revision="c" * 40,
                    sequence=9,
                    **identity,
                )
                prepared_c = deployment.prepare_deployment(request_c)
                staged_c = deployment.stage_deployment(
                    request_c,
                    fixture.authorization_raw(prepared_c),
                    fixture.root / "routine-stage-c",
                )
                stage_path = Path(staged_c.stage_path)
                before = fixture.stage_snapshot(stage_path)
                live_b = json.loads(
                    (initial.canonical_root / "deployment.json").read_bytes()
                )
                live_active = json.loads(
                    (initial.canonical_root / "active.json").read_bytes()
                )
                if name == "candidate-current-trust":
                    target = Path(staged_c.deployment_value["trust_context"]["path"])
                    target.unlink()
                elif name == "prior-current-trust":
                    target = Path(live_b["trust_context"]["path"])
                    target.write_bytes(b"mutated prior trust bytes\n")
                elif name == "prior-generation-payload":
                    payload = live_active["payloads"][0]
                    target = (
                        initial.canonical_root
                        / "generations"
                        / live_active["generation"]
                        / payload["relative_path"]
                    )
                    target.unlink()
                elif name == "prior-validator-module":
                    target = Path(
                        next(
                            module["path"]
                            for provider in live_b["providers"]
                            if provider["intrinsic"] is False
                            for module in provider["retained_modules"]
                        )
                    )
                    target.write_bytes(b"mutated prior validator bytes\n")
                elif name == "live-active-selector":
                    target = initial.canonical_root / "active.json"
                    target.write_bytes(b"mutated live active selector\n")
                elif name == "live-deployment-selector":
                    target = initial.canonical_root / "deployment.json"
                    target.write_bytes(b"mutated live deployment selector\n")
                else:
                    target = (
                        initial.canonical_root
                        / "receipts"
                        / f"sha256-{live_b['prior_receipt_sha256']}.json"
                    )
                    target.unlink()

                deployment.verify_deployment_stage(stage_path)

                self.assertEqual(fixture.stage_snapshot(stage_path), before)
                with self.assertRaisesRegex(
                    deployment.DeploymentError,
                    "active|trust|payload|module|receipt|ancestor|chain|missing|resolve",
                ):
                    deployment.prepare_deployment(request_c)

    def test_verify_routine_stage_rejects_staged_prior_semantic_drift(self) -> None:
        deployment = self.fixture.deployment()
        for name in (
            "history-duplicate",
            "active-manifest",
            "provider-module-order",
            "provider-source-declaration",
            "intrinsic-declaration",
            "intrinsic-module-semantic",
        ):
            with self.subTest(case=name):
                fixture = RoutineDeploymentFixture(self.root / f"staged-prior-{name}")
                candidate_a, candidate_b, identity = fixture.provider_candidate_pair()
                initial, active_a = fixture.activate_initial(
                    candidate_a,
                    source_identity=identity,
                )
                request_b = fixture.request_for_candidate(
                    initial.canonical_root,
                    active_a.active_receipt_sha256,
                    candidate_b,
                    release_version="1.0.1",
                    revision="b" * 40,
                    sequence=8,
                    **identity,
                )
                prepared_b = deployment.prepare_deployment(request_b)
                staged_b = deployment.stage_deployment(
                    request_b,
                    fixture.authorization_raw(prepared_b),
                    fixture.root / "routine-stage-b",
                )
                fixture.materialize_staged_candidate_as_live(staged_b)
                candidate_c = fixture.next_candidate(
                    candidate_a,
                    "provider-candidate-c",
                    "1.0.2",
                )
                request_c = fixture.request_for_candidate(
                    initial.canonical_root,
                    sha256(staged_b.deployment_raw),
                    candidate_c,
                    release_version="1.0.2",
                    revision="c" * 40,
                    sequence=9,
                    **identity,
                )
                prepared_c = deployment.prepare_deployment(request_c)
                staged_c = deployment.stage_deployment(
                    request_c,
                    fixture.authorization_raw(prepared_c),
                    fixture.root / "routine-stage-c",
                )

                def mutate_prior(prior, active):
                    if name == "history-duplicate":
                        history = prior["historical_trust_contexts"]
                        self.assertEqual(len(history), 1)
                        history.append(dict(history[0]))
                    elif name == "active-manifest":
                        active["payloads"][0]["length"] += 1
                    elif name == "intrinsic-module-semantic":
                        provider = next(
                            item
                            for item in prior["providers"]
                            if item["intrinsic"] is True
                        )
                        module_digest = "d" * 64
                        provider["retained_modules"][0]["sha256"] = module_digest
                        validator = provider["validators"][0]
                        validator["modules"][0]["sha256"] = module_digest
                        implementation = validator_identity(
                            validator["contract"],
                            validator["entrypoint"],
                            [
                                (item["name"], item["sha256"])
                                for item in validator["modules"]
                            ],
                        )
                        validator["implementation_sha256"] = implementation
                        module_path = Path(provider["retained_modules"][0]["path"])
                        rebound_path = (
                            module_path.parent.parent
                            / f"sha256-{implementation}"
                            / module_path.name
                        )
                        provider["retained_modules"][0]["path"] = str(rebound_path)
                        validator["modules"][0]["path"] = str(rebound_path)
                        producer = provider["producers"][0]
                        producer["validator_implementation_sha256"] = implementation
                        producer["implementation_sha256"] = sha256(
                            canonical_bytes(
                                {
                                    "contract": (
                                        "task-witness-smoke-producer-implementation-v1"
                                    ),
                                    "validator_implementation_sha256": implementation,
                                }
                            )
                        )
                        declaration_content = sha256(
                            canonical_bytes(
                                {
                                    "contract": (
                                        "task-witness-intrinsic-smoke-provider-v1"
                                    ),
                                    "validator_implementation_sha256": implementation,
                                }
                            )
                        )
                        provider["declaration_content_sha256"] = declaration_content
                        provider["declaration_sha256"] = sha256(
                            canonical_document(
                                {
                                    "contract": (
                                        "task-witness-intrinsic-smoke-provider-v1"
                                    ),
                                    "content_sha256": declaration_content,
                                }
                            )
                        )
                        identities = {
                            "producers": lambda item: (
                                item["producer_id"],
                                item["contract"],
                            ),
                            "issuers": lambda item: (
                                item["issuer_id"],
                                item["contract"],
                            ),
                            "validators": lambda item: (
                                item["validator_id"],
                                item["contract"],
                            ),
                        }
                        prior["role_inventory"] = {
                            category: sorted(
                                [
                                    dict(role)
                                    for item in prior["providers"]
                                    for role in item[category]
                                ],
                                key=identities[category],
                            )
                            for category in identities
                        }
                    else:
                        intrinsic = name == "intrinsic-declaration"
                        provider = next(
                            item
                            for item in prior["providers"]
                            if item["intrinsic"] is intrinsic
                        )
                        if name == "provider-module-order":
                            self.assertGreater(len(provider["retained_modules"]), 1)
                            provider["retained_modules"].reverse()
                        else:
                            provider["declaration_sha256"] = "f" * 64

                stage_path = fixture.rewrite_routine_stage_prior(
                    staged_c,
                    mutate_prior,
                )
                before = fixture.stage_snapshot(stage_path)
                expected_error = (
                    "staged prior intrinsic provider disagrees"
                    if name in {"intrinsic-declaration", "intrinsic-module-semantic"}
                    else "history|historical|active|runtime|provider|module|declaration"
                )
                with self.assertRaisesRegex(
                    deployment.DeploymentError,
                    expected_error,
                ):
                    deployment.verify_deployment_stage(stage_path)
                self.assertEqual(fixture.stage_snapshot(stage_path), before)

    def test_verify_routine_stage_requires_canonical_prior_role_inventories(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        identifiers = {
            "producers": "producer_id",
            "issuers": "issuer_id",
            "validators": "validator_id",
        }

        def identity(category, role):
            return (
                role[identifiers[category]],
                role["contract"],
                role["implementation_sha256"],
                canonical_bytes(role),
            )

        for category in ("producers", "issuers", "validators"):
            for mutation in (
                "duplicate",
                "provider-order",
                "global-order",
                "conflicting-authority",
            ):
                with self.subTest(category=category, mutation=mutation):
                    fixture = RoutineDeploymentFixture(
                        self.root / f"prior-{category}-{mutation}"
                    )
                    candidate_a, candidate_b, source_identity = (
                        fixture.provider_candidate_pair()
                    )
                    initial, active_a = fixture.activate_initial(
                        candidate_a,
                        source_identity=source_identity,
                    )
                    request_b = fixture.request_for_candidate(
                        initial.canonical_root,
                        active_a.active_receipt_sha256,
                        candidate_b,
                        release_version="1.0.1",
                        revision="b" * 40,
                        sequence=8,
                        **source_identity,
                    )
                    prepared_b = deployment.prepare_deployment(request_b)
                    staged_b = deployment.stage_deployment(
                        request_b,
                        fixture.authorization_raw(prepared_b),
                        fixture.root / "routine-stage-b",
                    )
                    fixture.materialize_staged_candidate_as_live(staged_b)
                    candidate_c = fixture.next_candidate(
                        candidate_a,
                        "provider-candidate-c",
                        "1.0.2",
                    )
                    request_c = fixture.request_for_candidate(
                        initial.canonical_root,
                        sha256(staged_b.deployment_raw),
                        candidate_c,
                        release_version="1.0.2",
                        revision="c" * 40,
                        sequence=9,
                        **source_identity,
                    )
                    prepared_c = deployment.prepare_deployment(request_c)
                    staged_c = deployment.stage_deployment(
                        request_c,
                        fixture.authorization_raw(prepared_c),
                        fixture.root / "routine-stage-c",
                    )

                    def mutate_prior(prior, active):
                        del active
                        provider = next(
                            item
                            for item in prior["providers"]
                            if item["intrinsic"] is False
                        )
                        roles = provider[category]
                        self.assertTrue(roles)
                        clone = json.loads(json.dumps(roles[0]))
                        if mutation == "duplicate":
                            roles.append(clone)
                        elif mutation in {"provider-order", "global-order"}:
                            clone[identifiers[category]] = f"zzzz-{category[:-1]}"
                            roles.append(clone)
                            roles.sort(key=lambda role: identity(category, role))
                            if mutation == "provider-order":
                                roles.reverse()
                        else:
                            if category == "validators":
                                clone["modules"][0]["sha256"] = "d" * 64
                                implementation = validator_identity(
                                    clone["contract"],
                                    clone["entrypoint"],
                                    [
                                        (item["name"], item["sha256"])
                                        for item in clone["modules"]
                                    ],
                                )
                                clone["implementation_sha256"] = implementation
                                for clone_module in clone["modules"]:
                                    module_path = Path(clone_module["path"])
                                    retained = json.loads(
                                        json.dumps(
                                            next(
                                                item
                                                for item in provider["retained_modules"]
                                                if item["path"] == str(module_path)
                                            )
                                        )
                                    )
                                    rebound_path = (
                                        module_path.parent.parent
                                        / f"sha256-{implementation}"
                                        / module_path.name
                                    )
                                    clone_module["path"] = str(rebound_path)
                                    retained["path"] = str(rebound_path)
                                    retained["sha256"] = clone_module["sha256"]
                                    provider["retained_modules"].append(retained)
                                provider["retained_modules"].sort(
                                    key=lambda item: item["path"]
                                )
                            else:
                                clone["implementation_sha256"] = "c" * 64
                            roles.append(clone)
                            roles.sort(key=lambda role: identity(category, role))

                        global_roles = [
                            json.loads(json.dumps(role))
                            for item in prior["providers"]
                            for role in item[category]
                        ]
                        global_roles.sort(key=lambda role: identity(category, role))
                        if mutation == "global-order":
                            global_roles.reverse()
                        prior["role_inventory"][category] = global_roles

                    stage_path = fixture.rewrite_routine_stage_prior(
                        staged_c,
                        mutate_prior,
                    )
                    before = fixture.stage_snapshot(stage_path)
                    with self.assertRaisesRegex(
                        deployment.DeploymentError,
                        "role|provider|sorted|unique|authority|inventory",
                    ):
                        deployment.verify_deployment_stage(stage_path)
                    self.assertEqual(fixture.stage_snapshot(stage_path), before)

    def test_verify_routine_stage_requires_canonical_prior_issuer_capabilities(
        self,
    ) -> None:
        deployment = self.fixture.deployment()

        for mutation in (
            "valid-multiple",
            "valid-max",
            "too-many",
            "empty",
            "duplicate",
            "unsorted",
            "invalid-token",
        ):
            with self.subTest(mutation=mutation):
                fixture = RoutineDeploymentFixture(
                    self.root / f"prior-issuer-capabilities-{mutation}"
                )
                candidate_a, candidate_b, source_identity = (
                    fixture.provider_candidate_pair()
                )
                if mutation in {"valid-multiple", "valid-max"}:
                    if mutation == "valid-multiple":
                        capabilities = [
                            "additional-capability",
                            "task-witness-attestation",
                        ]
                    else:
                        capabilities = [
                            f"capability-{index:02d}"
                            for index in range(deployment.MAX_VALIDATORS)
                        ]
                    for candidate in (candidate_a, candidate_b):
                        declaration_path = candidate / "task-witness-provider.json"
                        declaration = json.loads(declaration_path.read_bytes())
                        declaration["issuers"][0]["capabilities"] = capabilities
                        declaration_path.write_bytes(
                            canonical_document(fixture._recontent(declaration))
                        )
                        policy_path = candidate / "controller" / "policy.json"
                        policy = json.loads(policy_path.read_bytes())
                        policy["providers"][0]["issuers"][0]["capabilities"] = (
                            capabilities
                        )
                        policy_path.write_bytes(
                            canonical_document(fixture._recontent(policy))
                        )
                initial, active_a = fixture.activate_initial(
                    candidate_a,
                    source_identity=source_identity,
                )
                request_b = fixture.request_for_candidate(
                    initial.canonical_root,
                    active_a.active_receipt_sha256,
                    candidate_b,
                    release_version="1.0.1",
                    revision="b" * 40,
                    sequence=8,
                    **source_identity,
                )
                prepared_b = deployment.prepare_deployment(request_b)
                staged_b = deployment.stage_deployment(
                    request_b,
                    fixture.authorization_raw(prepared_b),
                    fixture.root / "routine-stage-b",
                )
                if mutation in {"valid-multiple", "valid-max"}:
                    before = fixture.stage_snapshot(staged_b.stage_path)
                    deployment.verify_deployment_stage(staged_b.stage_path)
                    self.assertEqual(
                        fixture.stage_snapshot(staged_b.stage_path),
                        before,
                    )
                    continue
                fixture.materialize_staged_candidate_as_live(staged_b)
                candidate_c = fixture.next_candidate(
                    candidate_a,
                    "provider-candidate-c",
                    "1.0.2",
                )
                request_c = fixture.request_for_candidate(
                    initial.canonical_root,
                    sha256(staged_b.deployment_raw),
                    candidate_c,
                    release_version="1.0.2",
                    revision="c" * 40,
                    sequence=9,
                    **source_identity,
                )
                prepared_c = deployment.prepare_deployment(request_c)
                staged_c = deployment.stage_deployment(
                    request_c,
                    fixture.authorization_raw(prepared_c),
                    fixture.root / "routine-stage-c",
                )

                def mutate_prior(prior, active):
                    del active
                    provider = next(
                        item
                        for item in prior["providers"]
                        if item["intrinsic"] is False
                    )
                    issuer = provider["issuers"][0]
                    capability = issuer["capabilities"][0]
                    if mutation == "too-many":
                        count = deployment.MAX_VALIDATORS + 1
                        issuer["capabilities"] = [
                            f"capability-{index:02d}" for index in range(count)
                        ]
                    elif mutation == "empty":
                        issuer["capabilities"] = []
                    elif mutation == "duplicate":
                        issuer["capabilities"] = [capability, capability]
                    elif mutation == "unsorted":
                        issuer["capabilities"] = ["zeta-capability", capability]
                        self.assertNotEqual(
                            issuer["capabilities"],
                            sorted(issuer["capabilities"]),
                        )
                    else:
                        issuer["capabilities"] = ["not a closed token"]
                    global_issuers = [
                        json.loads(json.dumps(role))
                        for item in prior["providers"]
                        for role in item["issuers"]
                    ]
                    global_issuers.sort(
                        key=lambda role: (
                            role["issuer_id"],
                            role["contract"],
                            canonical_bytes(role),
                        )
                    )
                    prior["role_inventory"]["issuers"] = global_issuers

                stage_path = fixture.rewrite_routine_stage_prior(
                    staged_c,
                    mutate_prior,
                )
                before = fixture.stage_snapshot(stage_path)
                with self.assertRaisesRegex(
                    deployment.DeploymentError,
                    "capabilit|issuer|provider|role|token",
                ):
                    deployment.verify_deployment_stage(stage_path)
                self.assertEqual(fixture.stage_snapshot(stage_path), before)

    def test_verify_routine_stage_requires_closed_prior_provider_tokens_and_source(
        self,
    ) -> None:
        for mutation in (
            "producer-id",
            "issuer-id",
            "validator-id",
            "validator-entrypoint",
            "source-mode",
            "source-plugin-token",
            "source-publisher-token",
            "source-authority-token",
            "source-channel-token",
            "source-manager-trust-token",
            "source-lineage-token",
            "source-repository-domain",
            "source-repository",
            "source-url-empty",
            "source-release-empty",
            "source-revision",
            "source-nonhex-revision",
            "source-subtree-digest",
            "source-selection-digest",
            "source-selection-content-digest",
            "source-manager-binding-digest",
            "source-manager-binding-content-digest",
            "source-manager-receipt-digest",
            "source-claude-manifest-digest",
            "source-codex-manifest-digest",
            "source-provider-sha-only",
            "source-provider-content-only",
            "source-lineage-negative",
        ):
            with self.subTest(mutation=mutation):
                state = self._stage_external_prior(f"prior-parity-{mutation}")
                deployment, fixture, *_, staged_c = state

                def mutate_prior(prior, active):
                    provider = next(
                        item
                        for item in prior["providers"]
                        if item["intrinsic"] is False
                    )
                    if mutation == "producer-id":
                        provider["producers"][0]["producer_id"] = "not a token"
                    elif mutation == "issuer-id":
                        provider["issuers"][0]["issuer_id"] = "not a token"
                    elif mutation == "validator-id":
                        validator = provider["validators"][0]
                        old_id = validator["validator_id"]
                        validator["validator_id"] = "not a token"
                        for producer in provider["producers"]:
                            if producer["validator_id"] == old_id:
                                producer["validator_id"] = "not a token"
                    elif mutation == "validator-entrypoint":
                        provider["validators"][0]["entrypoint"] = "not a token"
                    elif mutation == "source-mode":
                        prior["source"]["mode"] = "unsupported-mode"
                    elif mutation == "source-plugin-token":
                        prior["source"]["plugin_id"] = "not a token"
                    elif mutation == "source-publisher-token":
                        prior["source"]["publisher_id"] = "not a token"
                    elif mutation == "source-authority-token":
                        prior["source"]["source_authority"] = "not a token"
                    elif mutation == "source-channel-token":
                        prior["source"]["details"]["channel"] = "not a token"
                    elif mutation == "source-manager-trust-token":
                        prior["source"]["details"]["trust_class"] = "not a token"
                    elif mutation == "source-lineage-token":
                        prior["source"]["details"]["lineage"]["lineage_id"] = (
                            "not a token"
                        )
                    elif mutation == "source-repository-domain":
                        prior["source"]["repository_id"] = "not-a-repository"
                    elif mutation == "source-repository":
                        prior["source"]["repository_id"] = "other/repository"
                    elif mutation == "source-url-empty":
                        prior["source"]["repository_url"] = ""
                    elif mutation == "source-release-empty":
                        prior["source"]["release_version"] = ""
                    elif mutation == "source-nonhex-revision":
                        prior["source"]["revision"] = "g" * 40
                        active["public_release"]["revision"] = "g" * 40
                    elif mutation == "source-lineage-negative":
                        prior["source"]["details"]["lineage"]["sequence"] = -1
                    elif mutation in {
                        "source-provider-sha-only",
                        "source-provider-content-only",
                    }:
                        field = (
                            "provider_declaration_content_sha256"
                            if mutation == "source-provider-sha-only"
                            else "provider_declaration_sha256"
                        )
                        prior["source"][field] = None
                    elif mutation.startswith("source-") and mutation.endswith(
                        "-digest"
                    ):
                        if mutation.startswith("source-manager-"):
                            field = {
                                "source-manager-binding-digest": (
                                    "manager_binding_sha256"
                                ),
                                "source-manager-binding-content-digest": (
                                    "manager_binding_content_sha256"
                                ),
                                "source-manager-receipt-digest": (
                                    "manager_receipt_sha256"
                                ),
                            }[mutation]
                            prior["source"]["source_evidence"][field] = "not-a-digest"
                        else:
                            field = {
                                "source-subtree-digest": "subtree_sha256",
                                "source-selection-digest": "source_selection_sha256",
                                "source-selection-content-digest": (
                                    "source_selection_content_sha256"
                                ),
                                "source-claude-manifest-digest": (
                                    "claude_manifest_sha256"
                                ),
                                "source-codex-manifest-digest": (
                                    "codex_manifest_sha256"
                                ),
                            }[mutation]
                            prior["source"][field] = "not-a-digest"
                    else:
                        prior["source"]["revision"] = "d" * 40
                    self._reproject_provider_roles(prior)

                stage_path = fixture.rewrite_routine_stage_prior(
                    staged_c,
                    mutate_prior,
                )
                before = fixture.stage_snapshot(stage_path)
                expected = (
                    "closed token"
                    if mutation
                    in {
                        "producer-id",
                        "issuer-id",
                        "validator-id",
                        "validator-entrypoint",
                    }
                    else (
                        "source|mode|token|repository|bounded|digest|lineage|"
                        "provider|active|public release|revision"
                    )
                )
                with self.assertRaisesRegex(deployment.DeploymentError, expected):
                    deployment.verify_deployment_stage(stage_path)
                self.assertEqual(fixture.stage_snapshot(stage_path), before)

    def test_verify_routine_stage_requires_candidate_receipt_source_mode(self) -> None:
        state = self._stage_external_update("candidate-source-mode")
        deployment, fixture, *_, staged_b = state

        def mutate_candidate(rollback, deployment_value, stage):
            del rollback, stage
            deployment_value["source"]["mode"] = "unsupported-mode"

        stage_path = fixture.rewrite_routine_stage(staged_b, mutate_candidate)
        before = fixture.stage_snapshot(stage_path)
        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "source|mode|harness",
        ):
            deployment.verify_deployment_stage(stage_path)
        self.assertEqual(fixture.stage_snapshot(stage_path), before)

    def test_verify_routine_stage_requires_candidate_compatible_forward_source_transition(
        self,
    ) -> None:
        mutations = (
            "plugin-id",
            "publisher-id",
            "manifest-author",
            "repository-id",
            "repository-url",
            "source-authority",
            "channel",
            "manager-trust-class",
            "lineage-id",
            "provider-policy",
            "lineage-equal",
            "lineage-lower",
            "revision-reused",
            "release-version-reused",
            "release-evidence-drift",
            "exact-no-op",
            "exact-evidence-channel",
            "exact-evidence-source-authority",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                state = self._stage_external_update(
                    f"candidate-source-transition-{mutation}"
                )
                deployment, fixture, *_, staged_b = state

                def mutate_candidate(candidate, active, prior):
                    source = candidate["source"]
                    prior_source = prior["source"]
                    external = next(
                        item
                        for item in candidate["providers"]
                        if item["intrinsic"] is False
                    )
                    if mutation == "plugin-id":
                        source["plugin_id"] = "alternate-provider"
                        external["plugin_id"] = "alternate-provider"
                        candidate["providers"].sort(
                            key=lambda item: (item["plugin_id"], item["intrinsic"])
                        )
                    elif mutation == "publisher-id":
                        source["publisher_id"] = "alternate-publisher"
                        external["publisher"] = "alternate-publisher"
                    elif mutation == "manifest-author":
                        source["manifest_author"] = {
                            "name": "Alternate Publisher",
                            "url": "https://example.com/alternate-publisher",
                        }
                    elif mutation == "repository-id":
                        source["repository_id"] = "alternate/repository"
                        active["public_release"]["repository"] = "alternate/repository"
                    elif mutation == "repository-url":
                        source["repository_url"] = (
                            "https://example.com/alternate/repository"
                        )
                        external["repository"] = source["repository_url"]
                    elif mutation == "source-authority":
                        source["source_authority"] = "alternate-authority"
                    elif mutation == "channel":
                        source["details"]["channel"] = "alternate-channel"
                    elif mutation == "manager-trust-class":
                        source["details"]["trust_class"] = "alternate-trust"
                    elif mutation == "lineage-id":
                        source["details"]["lineage"]["lineage_id"] = "alternate-lineage"
                    elif mutation == "provider-policy":
                        external["authority_profile"] = "alternate-profile"
                    elif mutation == "lineage-equal":
                        source["details"]["lineage"]["sequence"] = prior_source[
                            "details"
                        ]["lineage"]["sequence"]
                    elif mutation == "lineage-lower":
                        source["details"]["lineage"]["sequence"] = (
                            prior_source["details"]["lineage"]["sequence"] - 1
                        )
                    elif mutation == "revision-reused":
                        source["revision"] = prior_source["revision"]
                        active["public_release"]["revision"] = source["revision"]
                    elif mutation == "release-version-reused":
                        source["release_version"] = prior_source["release_version"]
                    elif mutation == "release-evidence-drift":
                        source["revision"] = prior_source["revision"]
                        source["subtree_sha256"] = prior_source["subtree_sha256"]
                        active["public_release"]["revision"] = source["revision"]
                    else:
                        for field in (
                            "release_version",
                            "revision",
                            "subtree_sha256",
                            "provider_declaration_sha256",
                            "provider_declaration_content_sha256",
                            "agent_plugin_manifest_sha256",
                            "claude_manifest_sha256",
                        ):
                            source[field] = prior_source[field]
                        active["public_release"] = json.loads(
                            json.dumps(prior["active"]["public_release"])
                        )
                        external["declaration_sha256"] = source[
                            "provider_declaration_sha256"
                        ]
                        external["declaration_content_sha256"] = source[
                            "provider_declaration_content_sha256"
                        ]
                        if mutation == "exact-evidence-channel":
                            source["details"]["channel"] = "alternate-channel"
                        elif mutation == "exact-evidence-source-authority":
                            source["source_authority"] = "alternate-authority"

                stage_path = fixture.rewrite_routine_stage_candidate(
                    staged_b,
                    mutate_candidate,
                )
                before = fixture.stage_snapshot(stage_path)
                with self.assertRaises(deployment.DeploymentError) as raised:
                    deployment.verify_deployment_stage(stage_path)
                self.assertEqual(fixture.stage_snapshot(stage_path), before)
                expected = (
                    "approval-required/source-authority"
                    if mutation
                    in {
                        "exact-evidence-channel",
                        "exact-evidence-source-authority",
                    }
                    else (
                        "source|authority|provider|lineage|release|compatible|"
                        "forward|no-op"
                    )
                )
                self.assertRegex(str(raised.exception), expected)

    def test_verify_routine_stage_accepts_candidate_compatible_forward_variants(
        self,
    ) -> None:
        for mutation in (
            "provider-and-trust-implementation",
            "release-version",
            "revision",
            "subtree",
        ):
            with self.subTest(mutation=mutation):
                state = self._stage_external_update(
                    f"candidate-source-forward-{mutation}"
                )
                deployment, fixture, *_, staged_b = state

                if mutation == "provider-and-trust-implementation":
                    stage_path = staged_b.stage_path
                else:

                    def mutate_candidate(candidate, active, prior):
                        del prior
                        if mutation == "release-version":
                            candidate["source"]["release_version"] = "1.0.2"
                        elif mutation == "revision":
                            candidate["source"]["revision"] = "d" * 40
                            active["public_release"]["revision"] = "d" * 40
                        else:
                            candidate["source"]["subtree_sha256"] = "d" * 64

                    stage_path = fixture.rewrite_routine_stage_candidate(
                        staged_b,
                        mutate_candidate,
                    )

                stage = json.loads(Path(stage_path).read_bytes())

                def receipt(role):
                    artifact = next(
                        item for item in stage["artifacts"] if item["role"] == role
                    )
                    return json.loads(Path(artifact["staged"]["path"]).read_bytes())

                candidate = receipt("deployment-receipt")
                prior = receipt("prior-deployment-alias")
                if mutation == "provider-and-trust-implementation":
                    candidate_provider = next(
                        item
                        for item in candidate["providers"]
                        if item["intrinsic"] is False
                    )
                    prior_provider = next(
                        item
                        for item in prior["providers"]
                        if item["intrinsic"] is False
                    )
                    self.assertEqual(
                        self._provider_policy_projection(candidate_provider),
                        self._provider_policy_projection(prior_provider),
                    )
                    self.assertNotEqual(candidate_provider, prior_provider)
                    self.assertNotEqual(
                        candidate["trust_context"],
                        prior["trust_context"],
                    )
                before = fixture.stage_snapshot(Path(stage_path))
                deployment.verify_deployment_stage(Path(stage_path))
                self.assertEqual(fixture.stage_snapshot(Path(stage_path)), before)

    def test_planner_classifies_source_authority_before_policy_change(
        self,
    ) -> None:
        fixture = RoutineDeploymentFixture(self.root / "planner-authority-precedence")
        deployment = fixture.deployment()
        candidate_a, _, source_identity = fixture.provider_candidate_pair()
        initial, active_a = fixture.activate_initial(
            candidate_a,
            source_identity=source_identity,
        )
        precondition = deployment._capture_active_deployment_precondition(
            initial.canonical_root,
            active_a.active_receipt_sha256,
        )
        active_source = precondition.active_source
        active_policy = precondition.active_policy
        active_policy_sha256 = precondition.receipt_value["compatibility_policy"][
            "sha256"
        ]
        candidate_source = replace(active_source, channel="alternate-channel")
        stable_policy = deployment._classify_candidate_source(
            active_source=active_source,
            active_policy=active_policy,
            active_policy_sha256=active_policy_sha256,
            candidate_source=candidate_source,
            candidate_policy_sha256=active_policy_sha256,
        )
        future_policy = deployment._classify_candidate_source(
            active_source=active_source,
            active_policy=active_policy,
            active_policy_sha256=active_policy_sha256,
            candidate_source=candidate_source,
            candidate_policy_sha256="f" * 64,
        )
        self.assertEqual(
            (future_policy.outcome, future_policy.reason),
            ("approval-required", "source-authority"),
        )
        self.assertEqual(
            (stable_policy.outcome, stable_policy.reason),
            ("approval-required", "source-authority"),
        )

    def test_verify_routine_stage_requires_intrinsic_only_source_identity(self) -> None:
        deployment, fixture, staged_c = self._stage_intrinsic_prior(
            "prior-intrinsic-source"
        )

        def mutate_prior(prior, active):
            del active
            self.assertIsNone(prior["source"]["provider_declaration_sha256"])
            self.assertFalse(
                any(item["intrinsic"] is False for item in prior["providers"])
            )
            prior["source"]["plugin_id"] = "other-plugin"

        stage_path = fixture.rewrite_routine_stage_prior(staged_c, mutate_prior)
        before = fixture.stage_snapshot(stage_path)
        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "source|provider|task-witness|intrinsic",
        ):
            deployment.verify_deployment_stage(stage_path)
        self.assertEqual(fixture.stage_snapshot(stage_path), before)

    def test_verify_routine_stage_enforces_provider_and_module_bounds(self) -> None:
        cases = {
            "providers": "provider inventory is invalid",
            "producers": "producers inventory is invalid",
            "issuers": "issuers inventory is invalid",
            "validators": "validators inventory is invalid",
            "retained-modules": "retained modules are invalid",
            "validator-modules": "modules is invalid",
            "external-empty": "registers no roles",
        }
        for mutation, expected in cases.items():
            with self.subTest(mutation=mutation):
                state = self._stage_external_prior(f"prior-bounds-{mutation}")
                deployment, fixture, *_, staged_c = state

                def mutate_prior(prior, active):
                    del active
                    provider = next(
                        item
                        for item in prior["providers"]
                        if item["intrinsic"] is False
                    )
                    if mutation == "providers":
                        for index in range(deployment.MAX_VALIDATORS):
                            clone = json.loads(json.dumps(provider))
                            clone["plugin_id"] = f"extra-provider-{index:02d}"
                            prior["providers"].append(clone)
                        prior["providers"].sort(
                            key=lambda item: (item["plugin_id"], item["intrinsic"])
                        )
                    elif mutation in {"producers", "issuers", "validators"}:
                        identifier = {
                            "producers": "producer_id",
                            "issuers": "issuer_id",
                            "validators": "validator_id",
                        }[mutation]
                        for index in range(deployment.MAX_VALIDATORS):
                            clone = json.loads(json.dumps(provider[mutation][0]))
                            clone[identifier] = f"extra-{mutation[:-1]}-{index:02d}"
                            provider[mutation].append(clone)
                    elif mutation == "retained-modules":
                        template = json.loads(
                            json.dumps(provider["retained_modules"][0])
                        )
                        parent = Path(template["path"]).parent
                        provider["retained_modules"] = []
                        for index in range(
                            deployment.MAX_VALIDATORS * deployment.MAX_VALIDATOR_MODULES
                            + 1
                        ):
                            name = f"extra-module-{index:04d}"
                            module = json.loads(json.dumps(template))
                            module.update(
                                {
                                    "name": name,
                                    "path": str(parent / f"{name}.py"),
                                    "length": 0,
                                }
                            )
                            provider["retained_modules"].append(module)
                    elif mutation == "validator-modules":
                        validator = provider["validators"][0]
                        names_and_digests = [
                            (
                                f"bounded-module-{index:02d}",
                                sha256(f"m{index}".encode()),
                            )
                            for index in range(deployment.MAX_VALIDATOR_MODULES + 1)
                        ]
                        implementation = validator_identity(
                            validator["contract"],
                            names_and_digests[0][0],
                            names_and_digests,
                        )
                        generation = (
                            Path(provider["retained_modules"][0]["path"]).parent.parent
                            / f"sha256-{implementation}"
                        )
                        validator["entrypoint"] = names_and_digests[0][0]
                        validator["implementation_sha256"] = implementation
                        validator["modules"] = [
                            {
                                "name": name,
                                "path": str(generation / f"{name}.py"),
                                "sha256": digest,
                            }
                            for name, digest in names_and_digests
                        ]
                        provider["retained_modules"] = [
                            {
                                "name": name,
                                "path": str(generation / f"{name}.py"),
                                "length": 1,
                                "sha256": digest,
                            }
                            for name, digest in names_and_digests
                        ]
                        for producer in provider["producers"]:
                            if producer["validator_id"] == validator["validator_id"]:
                                producer["validator_implementation_sha256"] = (
                                    implementation
                                )
                    else:
                        provider["producers"] = []
                        provider["issuers"] = []
                        provider["validators"] = []
                        provider["retained_modules"] = []
                    self._reproject_provider_roles(prior)

                stage_path = fixture.rewrite_routine_stage_prior(
                    staged_c,
                    mutate_prior,
                )
                before = fixture.stage_snapshot(stage_path)
                with self.assertRaisesRegex(deployment.DeploymentError, expected):
                    deployment.verify_deployment_stage(stage_path)
                self.assertEqual(fixture.stage_snapshot(stage_path), before)

    def test_verify_routine_stage_budgets_modules_per_validator(self) -> None:
        fixture = RoutineDeploymentFixture(self.root / "validator-budget")
        deployment = fixture.deployment()
        candidate_a, candidate_b, source_identity = fixture.provider_candidate_pair()
        per_validator_length = deployment.MAX_VALIDATOR_ARTIFACT_BYTES // 2 + 1
        self.assertLessEqual(
            per_validator_length,
            deployment.MAX_VALIDATOR_ARTIFACT_BYTES,
        )
        self.assertGreater(
            2 * per_validator_length,
            deployment.MAX_VALIDATOR_ARTIFACT_BYTES,
        )
        for candidate in (candidate_a, candidate_b):
            declaration_path = candidate / "task-witness-provider.json"
            declaration = json.loads(declaration_path.read_bytes())
            template = declaration["validators"][0]
            validators = []
            for index in range(2):
                name = f"budget-module-{index}"
                raw = b"#" + bytes([97 + index]) * (per_validator_length - 2) + b"\n"
                module_path = candidate / "validators" / f"{name}.py"
                module_path.write_bytes(raw)
                digest = sha256(raw)
                implementation = validator_identity(
                    template["contract"],
                    name,
                    [(name, digest)],
                )
                validators.append(
                    {
                        "validator_id": f"budget-validator-{index}",
                        "contract": template["contract"],
                        "implementation_sha256": implementation,
                        "entrypoint": name,
                        "modules": [
                            {
                                "name": name,
                                "relative_path": f"validators/{name}.py",
                                "length": len(raw),
                                "sha256": digest,
                            }
                        ],
                        "lifecycle": json.loads(json.dumps(template["lifecycle"])),
                    }
                )
            declaration["validators"] = validators
            declaration["producers"][0].update(
                {
                    "validator_id": validators[0]["validator_id"],
                    "validator_contract": validators[0]["contract"],
                    "validator_implementation_sha256": validators[0][
                        "implementation_sha256"
                    ],
                }
            )
            declaration_path.write_bytes(
                canonical_document(fixture._recontent(declaration))
            )

            policy_path = candidate / "controller" / "policy.json"
            policy = json.loads(policy_path.read_bytes())
            provider = policy["providers"][0]
            lifecycle = {
                "state": "active",
                "usable_for_new_publication": True,
            }
            provider["validators"] = [
                {
                    "validator_id": item["validator_id"],
                    "contract": item["contract"],
                    **lifecycle,
                }
                for item in validators
            ]
            provider["producers"][0].update(
                {
                    "validator_id": validators[0]["validator_id"],
                    "validator_contract": validators[0]["contract"],
                }
            )
            policy_path.write_bytes(canonical_document(fixture._recontent(policy)))

        initial, active_a = fixture.activate_initial(
            candidate_a,
            source_identity=source_identity,
        )
        request_b = fixture.request_for_candidate(
            initial.canonical_root,
            active_a.active_receipt_sha256,
            candidate_b,
            release_version="1.0.1",
            revision="b" * 40,
            sequence=8,
            **source_identity,
        )
        prepared_b = deployment.prepare_deployment(request_b)
        staged_b = deployment.stage_deployment(
            request_b,
            fixture.authorization_raw(prepared_b),
            fixture.root / "routine-stage-b",
        )
        before = fixture.stage_snapshot(staged_b.stage_path)
        deployment.verify_deployment_stage(staged_b.stage_path)
        self.assertEqual(fixture.stage_snapshot(staged_b.stage_path), before)

    def test_prepare_validates_prior_source_projection_and_policy_coverage(
        self,
    ) -> None:
        for mutation in ("source-revision", "policy-source"):
            with self.subTest(mutation=mutation):
                state = self._stage_external_update(f"retained-source-{mutation}")
                (
                    deployment,
                    fixture,
                    initial,
                    active_a,
                    _,
                    candidate_b,
                    source_identity,
                    staged_b,
                ) = state

                def mutate_prior(prior, active):
                    del active
                    if mutation == "source-revision":
                        prior["source"]["revision"] = "d" * 40
                    else:
                        prior["source"]["plugin_id"] = "outside-policy-provider"
                        provider = next(
                            item
                            for item in prior["providers"]
                            if item["intrinsic"] is False
                        )
                        provider["plugin_id"] = "outside-policy-provider"

                fixture.rewrite_routine_stage_prior(staged_b, mutate_prior)
                self._materialize_rewritten_prior_chain(
                    fixture,
                    staged_b,
                    active_a.active_receipt_sha256,
                )
                live_b_raw = (initial.canonical_root / "deployment.json").read_bytes()
                live_b = json.loads(live_b_raw)
                self.assertEqual(
                    live_b["source"]["plugin_id"],
                    source_identity["plugin_id"],
                )
                retained_a = json.loads(
                    (
                        initial.canonical_root
                        / "receipts"
                        / f"sha256-{live_b['prior_receipt_sha256']}.json"
                    ).read_bytes()
                )
                if mutation == "source-revision":
                    self.assertNotEqual(
                        retained_a["source"]["revision"],
                        retained_a["active"]["public_release"]["revision"],
                    )
                else:
                    self.assertEqual(
                        retained_a["source"]["plugin_id"],
                        "outside-policy-provider",
                    )
                    self.assertEqual(
                        next(
                            item
                            for item in retained_a["providers"]
                            if item["intrinsic"] is False
                        )["plugin_id"],
                        "outside-policy-provider",
                    )
                candidate_c = fixture.next_candidate(
                    candidate_b,
                    "provider-candidate-c",
                    "1.0.2",
                )
                request_c = fixture.request_for_candidate(
                    initial.canonical_root,
                    sha256(live_b_raw),
                    candidate_c,
                    release_version="1.0.2",
                    revision="c" * 40,
                    sequence=9,
                    **source_identity,
                )
                expected = (
                    "source|active|public release"
                    if mutation == "source-revision"
                    else "policy|coverage|source"
                )
                with self.assertRaisesRegex(deployment.DeploymentError, expected):
                    deployment.prepare_deployment(request_c)

    def test_stage_reactivates_exact_historical_trust_without_duplication(self) -> None:
        deployment = self.fixture.deployment()
        for name in (
            "valid",
            "wrong-binding",
            "duplicate",
            "omission",
            "not-popped",
            "wrong-prior",
            "revoked-current",
        ):
            with self.subTest(case=name):
                fixture = RoutineDeploymentFixture(self.root / f"reactivation-{name}")
                candidate_a, candidate_b, identity = fixture.provider_candidate_pair()
                initial, active_a = fixture.activate_initial(
                    candidate_a,
                    source_identity=identity,
                )
                request_b = fixture.request_for_candidate(
                    initial.canonical_root,
                    active_a.active_receipt_sha256,
                    candidate_b,
                    release_version="1.0.1",
                    revision="b" * 40,
                    sequence=8,
                    **identity,
                )
                prepared_b = deployment.prepare_deployment(request_b)
                authorization_b = fixture.authorization_raw(prepared_b)
                staged_b = deployment.stage_deployment(
                    request_b,
                    authorization_b,
                    fixture.root / "routine-stage-b",
                )
                fixture.materialize_staged_candidate_as_live(staged_b)
                candidate_c = fixture.next_candidate(
                    candidate_a,
                    "provider-candidate-c",
                    "1.0.2",
                )
                request_c = fixture.request_for_candidate(
                    initial.canonical_root,
                    sha256(staged_b.deployment_raw),
                    candidate_c,
                    release_version="1.0.2",
                    revision="c" * 40,
                    sequence=9,
                    **identity,
                )
                prepared_c = deployment.prepare_deployment(request_c)
                self.assertEqual(
                    prepared_c.plan.classification.outcome,
                    "compatible-forward",
                )
                authorization_c = fixture.authorization_raw(prepared_c)
                staged_c = deployment.stage_deployment(
                    request_c,
                    authorization_c,
                    fixture.root / "routine-stage-c",
                )
                trust_x = staged_c.deployment_value["trust_context"]
                trust_y = staged_b.deployment_value["trust_context"]
                self.assertNotEqual(trust_x["sha256"], trust_y["sha256"])

                if name == "valid":
                    deployment.verify_deployment_stage(staged_c.stage_path)
                    self.assertEqual(
                        list(staged_c.deployment_value["historical_trust_contexts"]),
                        [{**trust_y, "state": "historical-usable"}],
                    )
                    fixture.materialize_staged_candidate_as_live(staged_c)
                    candidate_d = fixture.next_candidate(
                        candidate_c,
                        "provider-candidate-d",
                        "1.0.3",
                    )
                    request_d = fixture.request_for_candidate(
                        initial.canonical_root,
                        sha256(staged_c.deployment_raw),
                        candidate_d,
                        release_version="1.0.3",
                        revision="d" * 40,
                        sequence=10,
                        **identity,
                    )
                    prepared_d = deployment.prepare_deployment(request_d)
                    self.assertEqual(
                        prepared_d.plan.prior_receipt_sha256,
                        sha256(staged_c.deployment_raw),
                    )
                    continue

                def mutate_history(rollback, deployment_value, stage):
                    expected = {**trust_y, "state": "historical-usable"}
                    if name == "wrong-binding":
                        expected["path"] = str(
                            Path(expected["path"]).with_name(f"sha256-{'9' * 64}.json")
                        )
                        deployment_value["historical_trust_contexts"] = [expected]
                    elif name == "duplicate":
                        deployment_value["historical_trust_contexts"] = [
                            expected,
                            dict(expected),
                        ]
                    elif name == "omission":
                        deployment_value["historical_trust_contexts"] = []
                    elif name == "not-popped":
                        deployment_value["historical_trust_contexts"] = sorted(
                            [
                                {**trust_x, "state": "historical-usable"},
                                expected,
                            ],
                            key=lambda item: item["sha256"],
                        )
                    else:
                        if name == "wrong-prior":
                            deployment_value["historical_trust_contexts"] = [
                                {**trust_x, "state": "historical-usable"}
                            ]
                            return
                        prior_artifact = next(
                            item
                            for item in stage["artifacts"]
                            if item["role"] == "prior-deployment-alias"
                        )
                        prior_path = Path(prior_artifact["staged"]["path"])
                        prior = json.loads(prior_path.read_bytes())
                        reactivated = next(
                            item
                            for item in prior["historical_trust_contexts"]
                            if item["sha256"] == trust_x["sha256"]
                        )
                        reactivated["state"] = "revoked"
                        prior = RoutineDeploymentFixture._recontent(prior)
                        prior_raw = canonical_document(prior)
                        prior_path.write_bytes(prior_raw)
                        prior_path.chmod(0o600)
                        prior_staged = RoutineDeploymentFixture._binding(
                            prior_path,
                            prior_raw,
                            0o600,
                        )
                        prior_installed = RoutineDeploymentFixture._binding(
                            Path(prior_artifact["installed"]["path"]),
                            prior_raw,
                            0o600,
                        )
                        prior_artifact["staged"] = prior_staged
                        prior_artifact["installed"] = prior_installed
                        prior_sha256 = sha256(prior_raw)
                        rollback["precondition"]["active_receipt_sha256"] = prior_sha256
                        rollback["prior_receipt"] = RoutineDeploymentFixture._binding(
                            Path(stage["canonical_root"])
                            / "receipts"
                            / f"sha256-{prior_sha256}.json",
                            prior_raw,
                            0o600,
                        )
                        rollback["prior_activation_unit"]["deployment_receipt"] = (
                            prior_installed
                        )
                        rollback["selector_preimage"][1] = {
                            "role": "deployment-alias",
                            "staged": prior_staged,
                            "installed": prior_installed,
                        }
                        deployment_value["prior_receipt_sha256"] = prior_sha256
                        deployment_value["authorization"][
                            "expected_active_receipt_sha256"
                        ] = prior_sha256

                stage_path = fixture.rewrite_routine_stage(staged_c, mutate_history)
                before = fixture.stage_snapshot(stage_path)
                with self.assertRaises(deployment.DeploymentError):
                    deployment.verify_deployment_stage(stage_path)
                self.assertEqual(fixture.stage_snapshot(stage_path), before)
                if name in {"not-popped", "wrong-prior"}:
                    fixture.materialize_staged_candidate_as_live(staged_c)
                    candidate_d = fixture.next_candidate(
                        candidate_c,
                        "provider-candidate-d",
                        "1.0.3",
                    )
                    live_c_raw = (
                        initial.canonical_root / "deployment.json"
                    ).read_bytes()
                    request_d = fixture.request_for_candidate(
                        initial.canonical_root,
                        sha256(live_c_raw),
                        candidate_d,
                        release_version="1.0.3",
                        revision="d" * 40,
                        sequence=10,
                        **identity,
                    )
                    with self.assertRaisesRegex(
                        deployment.DeploymentError,
                        "historical|history|trust",
                    ):
                        deployment.prepare_deployment(request_d)


if __name__ == "__main__":
    unittest.main()
