from __future__ import annotations

import json

from ._support import (
    ValidInvocationFixture,
    _TaskWitnessClientTestCase,
    canonical_document,
    document,
    sha256,
)


class CompatibilityPolicyV2Tests(_TaskWitnessClientTestCase):
    @staticmethod
    def _rewrite_receipt(fixture: ValidInvocationFixture, mutate) -> None:
        old_retained = fixture.retained_deployment_receipt
        receipt = json.loads(fixture.deployment_raw)
        receipt.pop("content_sha256")
        mutate(receipt)
        fixture.receipt = document(receipt)
        fixture.deployment_raw = canonical_document(fixture.receipt)
        fixture.deployment.write_bytes(fixture.deployment_raw)
        fixture.deployment.chmod(0o600)
        retained = fixture.receipts_directory / (
            f"sha256-{sha256(fixture.deployment_raw)}.json"
        )
        if retained != old_retained:
            old_retained.unlink()
        retained.write_bytes(fixture.deployment_raw)
        retained.chmod(0o600)
        fixture.retained_deployment_receipt = retained

    @classmethod
    def _rewrite_policy(cls, fixture: ValidInvocationFixture, mutate) -> None:
        policy = json.loads(fixture.policy.read_bytes())
        policy.pop("content_sha256")
        mutate(policy)
        policy = document(policy)
        policy_raw = canonical_document(policy)
        fixture.policy.write_bytes(policy_raw)
        fixture.policy.chmod(0o600)

        def bind_policy(receipt) -> None:
            receipt["control_set"]["policy"].update(
                length=len(policy_raw),
                sha256=sha256(policy_raw),
            )
            receipt["compatibility_policy"].update(
                length=len(policy_raw),
                sha256=sha256(policy_raw),
                content_sha256=policy["content_sha256"],
            )

        cls._rewrite_receipt(fixture, bind_policy)

    def assert_public_rejection(self, fixture: ValidInvocationFixture) -> None:
        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertEqual(result.stdout, b"")

    def test_current_exact_surface_and_receipt_subset_are_accepted(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        policy = json.loads(fixture.policy.read_bytes())
        surface = policy["control_surface"]

        self.assertEqual(
            (policy["schema_version"], policy["contract"]),
            (2, "task-witness-compatibility-policy-v2"),
        )
        self.assertEqual(
            (surface["schema_version"], surface["contract"]),
            (1, "task-witness-control-surface-v1"),
        )
        self.assertEqual(len(surface["contracts"]), 27)
        self.assertEqual(
            surface["contracts"]["source_evidence"],
            "task-witness-source-evidence-v1",
        )
        self.assertEqual(len(fixture.receipt["contracts"]), 13)
        self.assertEqual(
            fixture.receipt["contracts"],
            {key: surface["contracts"][key] for key in fixture.receipt["contracts"]},
        )
        self.assertEqual(fixture.receipt["process_profile"], surface["process_profile"])

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_outer_and_nested_policy_shapes_are_strict(self) -> None:
        cases = {
            "outer-version": lambda policy: policy.__setitem__("schema_version", 1),
            "outer-contract": lambda policy: policy.__setitem__(
                "contract", "task-witness-compatibility-policy-v1"
            ),
            "outer-extra": lambda policy: policy.__setitem__("unexpected", None),
            "nested-version": lambda policy: policy["control_surface"].__setitem__(
                "schema_version", 2
            ),
            "nested-contract": lambda policy: policy["control_surface"].__setitem__(
                "contract", "task-witness-control-surface-v2"
            ),
            "nested-extra": lambda policy: policy["control_surface"].__setitem__(
                "unexpected", None
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                self._rewrite_policy(fixture, mutate)
                self.assert_public_rejection(fixture)

    def test_complete_contract_catalog_is_exact(self) -> None:
        def missing(policy) -> None:
            policy["control_surface"]["contracts"].pop("source_evidence")

        def extra(policy) -> None:
            policy["control_surface"]["contracts"]["unexpected"] = (
                "task-witness-unexpected-v1"
            )

        def changed(policy) -> None:
            policy["control_surface"]["contracts"]["activation_transaction"] = (
                "task-witness-activation-transaction-v2"
            )

        for name, mutate in {
            "missing": missing,
            "extra": extra,
            "changed": changed,
        }.items():
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                self._rewrite_policy(fixture, mutate)
                self.assert_public_rejection(fixture)

    def test_process_profile_is_current_exact(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._rewrite_policy(
            fixture,
            lambda policy: policy["control_surface"]["process_profile"].__setitem__(
                "umask", 0o022
            ),
        )

        self.assert_public_rejection(fixture)

    def test_receipt_contract_subset_is_exact(self) -> None:
        def missing(receipt) -> None:
            receipt["contracts"].pop("rollback_receipt")

        def extra(receipt) -> None:
            receipt["contracts"]["activation_transaction"] = (
                "task-witness-activation-transaction-v1"
            )

        def changed(receipt) -> None:
            receipt["contracts"]["compatibility_policy"] = (
                "task-witness-compatibility-policy-v1"
            )

        for name, mutate in {
            "missing": missing,
            "extra": extra,
            "changed": changed,
        }.items():
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                self._rewrite_receipt(fixture, mutate)
                self.assert_public_rejection(fixture)
