from __future__ import annotations

import fcntl
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tests.plugins.task_witness_deployment._support import (
    ProviderFixture,
    load_deployment_module,
)

from ._support import (
    CLIENT_ENVIRONMENT,
    RETAINED_STATE_DRIVER_SOURCE,
    ValidInvocationFixture,
    _TaskWitnessClientTestCase,
    bundle_identity,
    canonical_document,
    canonical_value,
    document,
    install_launcher_behavior,
    sha256,
    validator_identity,
    write_configured_driver,
    write_configured_launcher,
)


class RetainedStateTests(_TaskWitnessClientTestCase):
    def test_first_install_rollback_precondition_is_accepted(self) -> None:
        fixture = ValidInvocationFixture(self.root / "rollback-precondition")

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, fixture.envelope_raw)

    @staticmethod
    def _rewrite_rollback_receipt(
        fixture: ValidInvocationFixture,
        mutate,
    ) -> None:
        old_rollback = fixture.rollback_receipt
        rollback = json.loads(fixture.rollback_receipt_raw)
        rollback.pop("content_sha256")
        mutate(rollback)
        fixture.rollback_receipt_value = document(rollback)
        fixture.rollback_receipt_raw = canonical_document(
            fixture.rollback_receipt_value
        )
        rollback_sha256 = sha256(fixture.rollback_receipt_raw)
        fixture.rollback_receipt = fixture.receipts_directory / (
            f"sha256-{rollback_sha256}.json"
        )
        if old_rollback != fixture.rollback_receipt:
            old_rollback.unlink()
        fixture.rollback_receipt.write_bytes(fixture.rollback_receipt_raw)
        fixture.rollback_receipt.chmod(0o600)

        old_retained = fixture.retained_deployment_receipt
        receipt = json.loads(fixture.deployment_raw)
        receipt.pop("content_sha256")
        receipt["rollback"] = {
            "state": "absent",
            "path": str(fixture.rollback_receipt),
            "sha256": rollback_sha256,
        }
        fixture.receipt = document(receipt)
        fixture.deployment_raw = canonical_document(fixture.receipt)
        fixture.deployment.write_bytes(fixture.deployment_raw)
        fixture.deployment.chmod(0o600)
        fixture.retained_deployment_receipt = fixture.receipts_directory / (
            f"sha256-{sha256(fixture.deployment_raw)}.json"
        )
        if old_retained != fixture.retained_deployment_receipt:
            old_retained.unlink()
        fixture.retained_deployment_receipt.write_bytes(fixture.deployment_raw)
        fixture.retained_deployment_receipt.chmod(0o600)

    def test_first_install_rollback_precondition_schema_is_strict(self) -> None:
        def mutate_vector(
            receipt: dict[str, Any],
            name: str,
            index: int,
            value: object,
        ) -> None:
            receipt["precondition"][name][index] = value

        cases = {
            "missing-precondition": lambda receipt: receipt.pop("precondition"),
            "extra-precondition-field": lambda receipt: receipt[
                "precondition"
            ].__setitem__("unexpected", []),
            "root-identity-not-array": lambda receipt: receipt[
                "precondition"
            ].__setitem__("root_identity", {}),
            "root-identity-short": lambda receipt: receipt["precondition"].__setitem__(
                "root_identity",
                receipt["precondition"]["root_identity"][:-1],
            ),
            "root-identity-float": lambda receipt: mutate_vector(
                receipt,
                "root_identity",
                4,
                float(receipt["precondition"]["root_identity"][4]),
            ),
            "root-identity-negative": lambda receipt: mutate_vector(
                receipt,
                "root_identity",
                5,
                -1,
            ),
            "lock-identity-short": lambda receipt: receipt["precondition"].__setitem__(
                "activation_lock_identity",
                receipt["precondition"]["activation_lock_identity"][:-1],
            ),
            "lock-identity-boolean": lambda receipt: mutate_vector(
                receipt,
                "activation_lock_identity",
                4,
                True,
            ),
            "lock-identity-negative": lambda receipt: mutate_vector(
                receipt,
                "activation_lock_identity",
                5,
                -1,
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                self._rewrite_rollback_receipt(fixture, mutate)

                result = fixture.invoke(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                )

                self.assertEqual(result.returncode, 70, result.stderr.decode())
                self.assertEqual(result.stdout, b"")

    def test_first_install_rollback_precondition_binds_pinned_identities(
        self,
    ) -> None:
        cases = {
            "root-stable-mapping": ("root_identity", 0),
            "lock-full-identity": ("activation_lock_identity", 6),
        }
        for name, (vector_name, index) in cases.items():
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)

                def mutate(
                    receipt: dict[str, Any],
                    vector_name: str = vector_name,
                    index: int = index,
                ) -> None:
                    vector = receipt["precondition"][vector_name]
                    vector[index] += 1

                self._rewrite_rollback_receipt(fixture, mutate)

                result = fixture.invoke(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                )

                self.assertEqual(result.returncode, 70, result.stderr.decode())
                self.assertEqual(result.stdout, b"")

    def test_first_install_rollback_root_history_is_not_live_state(self) -> None:
        fixture = ValidInvocationFixture(self.root / "root-history")

        def mutate(receipt: dict[str, Any]) -> None:
            identity = receipt["precondition"]["root_identity"]
            for index in range(4, 8):
                identity[index] += index + 1

        self._rewrite_rollback_receipt(fixture, mutate)

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, fixture.envelope_raw)

    @staticmethod
    def _replace_active_trust_context(
        fixture: ValidInvocationFixture,
        context: dict[str, Any],
    ) -> None:
        raw = canonical_document(document(context))
        digest = sha256(raw)
        path = fixture.context_directory / f"sha256-{digest}.json"
        path.write_bytes(raw)
        path.chmod(0o600)
        if path != fixture.trust_context:
            fixture.trust_context.unlink()
        fixture.trust_raw = raw
        fixture.trust_sha256 = digest
        fixture.trust_context = path
        fixture.expected_anchor = {
            **fixture.expected_anchor,
            "trust_context_sha256": digest,
        }
        producer = context["producers"][0]
        validator = context["validators"][0]
        fixture.witness = {
            **fixture.witness,
            "producer": {
                key: producer[key]
                for key in (
                    "producer_id",
                    "contract",
                    "implementation_sha256",
                    "validator_id",
                    "validator_contract",
                    "validator_implementation_sha256",
                )
            },
            "validator": {
                key: validator[key]
                for key in (
                    "validator_id",
                    "contract",
                    "implementation_sha256",
                )
            },
            "trust_context_sha256": digest,
        }

    @staticmethod
    def _rewrite_deployment_receipt(
        fixture: ValidInvocationFixture,
        mutate,
    ) -> None:
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

    def _install_external_provider_context(
        self,
        fixture: ValidInvocationFixture,
        *,
        prefix: str = "zeta",
    ) -> None:
        previous_receipt = json.loads(fixture.deployment_raw)
        previous_external = [
            provider
            for provider in previous_receipt["providers"]
            if not provider["intrinsic"]
        ]
        previous_intrinsic = next(
            provider
            for provider in previous_receipt["providers"]
            if provider["intrinsic"]
        )
        provider_fixture = ProviderFixture(
            fixture.root / f"{prefix}-provider",
            prefix=prefix,
        )
        for module, declaration in zip(
            (provider_fixture.entrypoint, provider_fixture.helper),
            provider_fixture.validator_modules,
        ):
            raw = module.read_bytes() + f"# {prefix}\n".encode()
            module.write_bytes(raw)
            declaration.update(length=len(raw), sha256=sha256(raw))
        provider_fixture.refresh_validator_identity()
        provider_fixture.write()
        provider = load_deployment_module().materialize_provider(
            provider_fixture.root,
            fixture.install / "trust",
        )
        if provider is None:
            self.fail("external provider fixture was not materialized")

        def thaw(value):
            if isinstance(value, Mapping):
                return {key: thaw(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [thaw(item) for item in value]
            return value

        context = json.loads(fixture.trust_raw)
        context.pop("content_sha256")
        for category in ("producers", "issuers", "validators"):
            context[category].extend(thaw(getattr(provider, category)))
            identifier = f"{category[:-1]}_id"
            context[category].sort(
                key=lambda item: (
                    item[identifier],
                    item["contract"],
                    item["implementation_sha256"],
                )
            )
        self._replace_active_trust_context(fixture, context)

        marker = fixture.root / "launcher-ran"
        install_launcher_behavior(
            fixture,
            "marker_output",
            marker=marker,
            output=canonical_document(
                {
                    "contract": "task-witness-launch-envelope-v1",
                    "anchor": fixture.expected_anchor,
                    "witness": fixture.witness,
                }
            ),
        )

        policy_fields = {
            "producers": (
                "producer_id",
                "contract",
                "validator_id",
                "validator_contract",
                "state",
                "usable_for_new_publication",
            ),
            "issuers": (
                "issuer_id",
                "contract",
                "capabilities",
                "state",
                "usable_for_new_publication",
            ),
            "validators": (
                "validator_id",
                "contract",
                "state",
                "usable_for_new_publication",
            ),
        }
        policy_provider = {
            "plugin_id": provider.plugin_id,
            "authority_profile": provider.authority_profile,
            **{
                category: [
                    {key: item[key] for key in fields}
                    for item in getattr(provider, category)
                ]
                for category, fields in policy_fields.items()
            },
        }
        policy = json.loads(fixture.policy.read_bytes())
        policy.pop("content_sha256")
        if not previous_external:
            policy["source"].update(
                plugin_id=provider.plugin_id,
                publisher_id=provider.publisher,
                repository_url=provider.repository,
            )
        policy["providers"].append(policy_provider)
        policy["providers"].sort(key=lambda item: item["plugin_id"])
        policy = document(policy)
        policy_raw = canonical_document(policy)
        fixture.policy.write_bytes(policy_raw)
        fixture.policy.chmod(0o600)

        declaration_raw = provider_fixture.provider_path.read_bytes()
        declaration = json.loads(declaration_raw)
        external_provider = {
            "plugin_id": provider.plugin_id,
            "publisher": provider.publisher,
            "repository": provider.repository,
            "authority_profile": provider.authority_profile,
            "intrinsic": False,
            "declaration_sha256": sha256(declaration_raw),
            "declaration_content_sha256": declaration["content_sha256"],
            "producers": thaw(provider.producers),
            "issuers": thaw(provider.issuers),
            "validators": thaw(provider.validators),
            "retained_modules": [
                {
                    "name": module.name,
                    "path": str(module.path),
                    "length": len(module.raw),
                    "sha256": module.sha256,
                }
                for module in sorted(provider.modules, key=lambda item: str(item.path))
            ],
        }

        def mutate(receipt: dict[str, Any]) -> None:
            receipt["providers"] = [
                previous_intrinsic,
                *previous_external,
                external_provider,
            ]
            receipt["providers"].sort(
                key=lambda item: (item["plugin_id"], item["intrinsic"])
            )
            if not previous_external:
                receipt["source"].update(
                    plugin_id=external_provider["plugin_id"],
                    publisher_id=external_provider["publisher"],
                    repository_url=external_provider["repository"],
                    provider_declaration_sha256=external_provider["declaration_sha256"],
                    provider_declaration_content_sha256=external_provider[
                        "declaration_content_sha256"
                    ],
                )
            receipt["control_set"]["policy"].update(
                length=len(policy_raw),
                sha256=sha256(policy_raw),
            )
            receipt["compatibility_policy"].update(
                length=len(policy_raw),
                sha256=sha256(policy_raw),
                content_sha256=policy["content_sha256"],
            )

        self._rewrite_deployment_receipt(fixture, mutate)

    def test_external_provider_and_intrinsic_smoke_reach_the_launcher(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._install_external_provider_context(fixture)
        receipt = json.loads(fixture.deployment_raw)
        external = next(
            provider for provider in receipt["providers"] if not provider["intrinsic"]
        )

        self.assertEqual(
            (
                receipt["source"]["plugin_id"],
                receipt["source"]["publisher_id"],
                receipt["source"]["repository_url"],
                receipt["source"]["provider_declaration_sha256"],
                receipt["source"]["provider_declaration_content_sha256"],
            ),
            (
                external["plugin_id"],
                external["publisher"],
                external["repository"],
                external["declaration_sha256"],
                external["declaration_content_sha256"],
            ),
        )

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertTrue((fixture.root / "launcher-ran").exists())

    def test_two_external_providers_never_reach_the_launcher(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._install_external_provider_context(fixture)
        self._install_external_provider_context(fixture, prefix="yankee")

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertFalse((fixture.root / "launcher-ran").exists())

    def test_malformed_external_provider_identity_never_reaches_the_launcher(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._install_external_provider_context(fixture)

        def mutate(receipt: dict[str, Any]) -> None:
            external = next(
                provider
                for provider in receipt["providers"]
                if not provider["intrinsic"]
            )
            external["publisher"] = ""

        self._rewrite_deployment_receipt(fixture, mutate)

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertFalse((fixture.root / "launcher-ran").exists())

    def test_external_provider_policy_identity_mismatch_never_reaches_the_launcher(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._install_external_provider_context(fixture)

        def mutate(receipt: dict[str, Any]) -> None:
            external = next(
                provider
                for provider in receipt["providers"]
                if not provider["intrinsic"]
            )
            external["plugin_id"] = "zulu-plugin"

        self._rewrite_deployment_receipt(fixture, mutate)

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertFalse((fixture.root / "launcher-ran").exists())

    def test_external_provider_policy_authority_mismatch_never_reaches_the_launcher(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._install_external_provider_context(fixture)

        def mutate(receipt: dict[str, Any]) -> None:
            external = next(
                provider
                for provider in receipt["providers"]
                if not provider["intrinsic"]
            )
            external["authority_profile"] = "unapproved-authority"

        self._rewrite_deployment_receipt(fixture, mutate)

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertFalse((fixture.root / "launcher-ran").exists())

    def test_unbound_source_with_external_provider_never_reaches_the_launcher(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._install_external_provider_context(fixture)

        def mutate(receipt: dict[str, Any]) -> None:
            receipt["source"]["provider_declaration_sha256"] = None
            receipt["source"]["provider_declaration_content_sha256"] = None

        self._rewrite_deployment_receipt(fixture, mutate)

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertFalse((fixture.root / "launcher-ran").exists())

    def test_bound_source_without_external_provider_never_reaches_the_launcher(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        marker = fixture.root / "launcher-ran"
        install_launcher_behavior(
            fixture,
            "marker_output",
            marker=marker,
            output=fixture.envelope_raw,
        )

        def mutate(receipt: dict[str, Any]) -> None:
            receipt["source"]["provider_declaration_sha256"] = sha256(
                b"unbound provider declaration\n"
            )
            receipt["source"]["provider_declaration_content_sha256"] = sha256(
                b"unbound provider declaration content\n"
            )

        self._rewrite_deployment_receipt(fixture, mutate)

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertIn(b"validator_code_executed=no", result.stderr)
        self.assertFalse(marker.exists())

    def test_external_provider_module_cross_binding_never_reaches_the_launcher(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._install_external_provider_context(fixture)

        def mutate(receipt: dict[str, Any]) -> None:
            intrinsic = next(
                provider for provider in receipt["providers"] if provider["intrinsic"]
            )
            external = next(
                provider
                for provider in receipt["providers"]
                if not provider["intrinsic"]
            )
            external["retained_modules"] = intrinsic["retained_modules"]

        self._rewrite_deployment_receipt(fixture, mutate)

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertFalse((fixture.root / "launcher-ran").exists())

    def test_external_provider_module_ownership_swap_never_reaches_the_launcher(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._install_external_provider_context(fixture)
        self._install_external_provider_context(fixture, prefix="yankee")

        def mutate(receipt: dict[str, Any]) -> None:
            external = [
                provider
                for provider in receipt["providers"]
                if not provider["intrinsic"]
            ]
            external[0]["retained_modules"], external[1]["retained_modules"] = (
                external[1]["retained_modules"],
                external[0]["retained_modules"],
            )

        self._rewrite_deployment_receipt(fixture, mutate)

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertFalse((fixture.root / "launcher-ran").exists())

    def test_external_provider_policy_cross_binding_never_reaches_the_launcher(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._install_external_provider_context(fixture)
        policy = json.loads(fixture.policy.read_bytes())
        policy.pop("content_sha256")
        policy["providers"][0]["validators"][0]["validator_id"] = (
            "cross-bound-validator"
        )
        policy = document(policy)
        policy_raw = canonical_document(policy)
        fixture.policy.write_bytes(policy_raw)
        fixture.policy.chmod(0o600)

        def mutate(receipt: dict[str, Any]) -> None:
            receipt["control_set"]["policy"].update(
                length=len(policy_raw),
                sha256=sha256(policy_raw),
            )
            receipt["compatibility_policy"].update(
                length=len(policy_raw),
                sha256=sha256(policy_raw),
                content_sha256=policy["content_sha256"],
            )

        self._rewrite_deployment_receipt(fixture, mutate)

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertFalse((fixture.root / "launcher-ran").exists())

    def test_external_provider_policy_role_swap_never_reaches_the_launcher(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._install_external_provider_context(fixture)
        self._install_external_provider_context(fixture, prefix="yankee")
        policy = json.loads(fixture.policy.read_bytes())
        policy.pop("content_sha256")
        for category in ("producers", "issuers", "validators"):
            policy["providers"][0][category], policy["providers"][1][category] = (
                policy["providers"][1][category],
                policy["providers"][0][category],
            )
        policy = document(policy)
        policy_raw = canonical_document(policy)
        fixture.policy.write_bytes(policy_raw)
        fixture.policy.chmod(0o600)

        def mutate(receipt: dict[str, Any]) -> None:
            receipt["control_set"]["policy"].update(
                length=len(policy_raw),
                sha256=sha256(policy_raw),
            )
            receipt["compatibility_policy"].update(
                length=len(policy_raw),
                sha256=sha256(policy_raw),
                content_sha256=policy["content_sha256"],
            )

        self._rewrite_deployment_receipt(fixture, mutate)

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertFalse((fixture.root / "launcher-ran").exists())

    def test_external_provider_issuer_ownership_swap_never_reaches_the_launcher(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._install_external_provider_context(fixture)
        self._install_external_provider_context(fixture, prefix="yankee")
        policy = json.loads(fixture.policy.read_bytes())
        policy.pop("content_sha256")
        external = [
            provider
            for provider in policy["providers"]
            if provider["plugin_id"] != "task-witness"
        ]
        self.assertEqual(len(external), 2)
        external[0]["issuers"], external[1]["issuers"] = (
            external[1]["issuers"],
            external[0]["issuers"],
        )
        policy = document(policy)
        policy_raw = canonical_document(policy)
        fixture.policy.write_bytes(policy_raw)
        fixture.policy.chmod(0o600)

        def mutate(receipt: dict[str, Any]) -> None:
            receipt["control_set"]["policy"].update(
                length=len(policy_raw),
                sha256=sha256(policy_raw),
            )
            receipt["compatibility_policy"].update(
                length=len(policy_raw),
                sha256=sha256(policy_raw),
                content_sha256=policy["content_sha256"],
            )

        self._rewrite_deployment_receipt(fixture, mutate)

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(
            (result.returncode, (fixture.root / "launcher-ran").exists()),
            (70, False),
            result.stderr.decode(),
        )

    def test_unowned_external_trust_role_never_reaches_the_launcher(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._install_external_provider_context(fixture)
        context = json.loads(fixture.trust_raw)
        context.pop("content_sha256")
        context["issuers"].append(
            {
                "issuer_id": "orphan-issuer",
                "contract": "orphan-issuer-v1",
                "implementation_sha256": sha256(b"orphan issuer implementation\n"),
                "capabilities": ["operator-choice"],
                "state": "active",
                "usable_for_new_publication": True,
            }
        )
        context["issuers"].sort(
            key=lambda item: (
                item["issuer_id"],
                item["contract"],
                item["implementation_sha256"],
            )
        )
        self._replace_active_trust_context(fixture, context)

        def mutate(receipt: dict[str, Any]) -> None:
            receipt["trust_context"] = {
                "path": str(fixture.trust_context),
                "sha256": fixture.trust_sha256,
            }
            receipt["role_inventory"] = {
                category: context[category]
                for category in ("producers", "issuers", "validators")
            }

        self._rewrite_deployment_receipt(fixture, mutate)

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertFalse((fixture.root / "launcher-ran").exists())

    def test_external_producer_cannot_cross_provider_authority(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._install_external_provider_context(fixture)
        self._install_external_provider_context(fixture, prefix="yankee")
        context = json.loads(fixture.trust_raw)
        context.pop("content_sha256")
        producer = next(
            item
            for item in context["producers"]
            if item["producer_id"] == "yankee-producer"
        )
        validator = next(
            item
            for item in context["validators"]
            if item["validator_id"] == "zeta-validator"
        )
        producer.update(
            validator_id=validator["validator_id"],
            validator_contract=validator["contract"],
            validator_implementation_sha256=validator["implementation_sha256"],
        )
        self._replace_active_trust_context(fixture, context)

        policy = json.loads(fixture.policy.read_bytes())
        policy.pop("content_sha256")
        policy_producer = next(
            item for item in policy["providers"] if item["plugin_id"] == "yankee-plugin"
        )["producers"][0]
        policy_producer.update(
            validator_id=validator["validator_id"],
            validator_contract=validator["contract"],
        )
        policy = document(policy)
        policy_raw = canonical_document(policy)
        fixture.policy.write_bytes(policy_raw)
        fixture.policy.chmod(0o600)

        def mutate(receipt: dict[str, Any]) -> None:
            receipt["trust_context"] = {
                "path": str(fixture.trust_context),
                "sha256": fixture.trust_sha256,
            }
            receipt["role_inventory"] = {
                category: context[category]
                for category in ("producers", "issuers", "validators")
            }
            receipt["control_set"]["policy"].update(
                length=len(policy_raw),
                sha256=sha256(policy_raw),
            )
            receipt["compatibility_policy"].update(
                length=len(policy_raw),
                sha256=sha256(policy_raw),
                content_sha256=policy["content_sha256"],
            )

        self._rewrite_deployment_receipt(fixture, mutate)

        result = fixture.invoke("validate", "--bundle", str(fixture.bundle))

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertFalse((fixture.root / "launcher-ran").exists())

    def test_nonempty_activation_lock_is_rejected_before_launcher(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        launcher_marker = self.root / "launcher-executed"
        fixture.lock.write_bytes(b"not lock state\n")
        install_launcher_behavior(
            fixture,
            "marker_output",
            marker=launcher_marker,
            marker_content="executed",
            output=fixture.envelope_raw,
        )
        lock_metadata = fixture.lock.stat()
        rollback = json.loads(fixture.rollback_receipt_raw)
        self.assertEqual(
            rollback["precondition"]["activation_lock_identity"],
            [
                lock_metadata.st_dev,
                lock_metadata.st_ino,
                lock_metadata.st_mode,
                lock_metadata.st_uid,
                lock_metadata.st_nlink,
                lock_metadata.st_size,
                lock_metadata.st_mtime_ns,
                lock_metadata.st_ctime_ns,
            ],
        )
        self.assertEqual(
            fixture.receipt["rollback"]["sha256"],
            sha256(fixture.rollback_receipt_raw),
        )
        self.assertEqual(
            fixture.retained_deployment_receipt.read_bytes(),
            fixture.deployment_raw,
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
        )

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertFalse(launcher_marker.exists())

    def test_mode_0700_activation_lock_is_rejected_before_launcher(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        launcher_marker = self.root / "launcher-executed"
        fixture.lock.chmod(0o700)
        install_launcher_behavior(
            fixture,
            "marker_output",
            marker=launcher_marker,
            marker_content="executed",
            output=fixture.envelope_raw,
        )
        rollback = json.loads(fixture.rollback_receipt_raw)
        self.assertEqual(fixture.receipt["activation_lock"]["mode"], 0o700)
        self.assertEqual(rollback["activation_lock"]["mode"], 0o700)
        self.assertEqual(
            stat.S_IMODE(rollback["precondition"]["activation_lock_identity"][2]),
            0o700,
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
        )

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertFalse(launcher_marker.exists())

    def test_mode_0400_activation_lock_is_rejected_before_open(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        launcher_marker = self.root / "launcher-executed"
        open_marker = self.root / "activation-lock-opened"
        fixture.lock.chmod(0o400)
        install_launcher_behavior(
            fixture,
            "marker_output",
            marker=launcher_marker,
            marker_content="executed",
            output=fixture.envelope_raw,
        )

        result = fixture.invoke_with_activation_lock_open_audit(
            open_marker,
            "validate",
            "--bundle",
            str(fixture.bundle),
        )

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertFalse(open_marker.exists())
        self.assertFalse(launcher_marker.exists())

    def test_activation_lock_swap_between_preflight_and_open_is_rejected(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        launcher_marker = self.root / "launcher-executed"
        audit_marker = self.root / "lock-swapped"
        replacement = self.root / "replacement.lock"
        replacement.touch(mode=0o600)
        install_launcher_behavior(
            fixture,
            "marker_output",
            marker=launcher_marker,
            marker_content="executed",
            output=fixture.envelope_raw,
        )
        held_lock = os.open(fixture.lock, os.O_RDWR)
        try:
            fcntl.flock(held_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

            result = fixture.invoke_with_open_swap(
                fixture.lock,
                replacement,
                audit_marker,
                "validate",
                "--bundle",
                str(fixture.bundle),
            )
        finally:
            fcntl.flock(held_lock, fcntl.LOCK_UN)
            os.close(held_lock)

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertTrue(audit_marker.is_file(), "audit hook did not swap the lock")
        self.assertFalse(launcher_marker.exists())

    def test_preexisting_activation_lock_replacement_is_rejected(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        launcher_marker = self.root / "launcher-executed"
        replacement = self.root / "replacement.lock"
        replacement.touch(mode=0o600)
        install_launcher_behavior(
            fixture,
            "marker_output",
            marker=launcher_marker,
            marker_content="executed",
            output=fixture.envelope_raw,
        )
        held_lock = os.open(fixture.lock, os.O_RDWR)
        try:
            fcntl.flock(held_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.replace(replacement, fixture.lock)

            result = fixture.invoke(
                "validate",
                "--bundle",
                str(fixture.bundle),
            )
        finally:
            fcntl.flock(held_lock, fcntl.LOCK_UN)
            os.close(held_lock)

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertFalse(launcher_marker.exists())

    def test_activation_lock_replacement_while_launcher_runs_is_rejected(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        launcher_started = self.root / "launcher-started"
        release_launcher = self.root / "release-launcher"
        replacement = self.root / "replacement.lock"
        replacement.touch(mode=0o600)
        install_launcher_behavior(
            fixture,
            "marker_gate",
            started=launcher_started,
            continuation=release_launcher,
            output=fixture.envelope_raw,
        )
        process = subprocess.Popen(
            fixture.command(
                "validate",
                "--bundle",
                str(fixture.bundle),
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=CLIENT_ENVIRONMENT,
        )
        try:
            deadline = time.monotonic() + 3
            while not launcher_started.exists() and process.poll() is None:
                if time.monotonic() >= deadline:
                    self.fail("launcher did not reach the synchronization point")
                time.sleep(0.01)
            self.assertIsNone(process.poll())
            os.replace(replacement, fixture.lock)
            release_launcher.touch()
            stdout, stderr = process.communicate(timeout=5)
        finally:
            release_launcher.touch(exist_ok=True)
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

        self.assertEqual(process.returncode, 70, stderr.decode())
        self.assertEqual(stdout, b"")

    def test_activation_lock_content_change_while_launcher_runs_is_rejected(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        unexpected_content = b"not lock state\n"
        fixture.replace_launcher_behavior(
            "#!/usr/bin/env python3\n"
            "from pathlib import Path\n"
            "import sys\n"
            f"Path({str(fixture.lock)!r}).write_bytes({unexpected_content!r})\n"
            f"sys.stdout.buffer.write({fixture.envelope_raw!r})\n"
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
        )

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertEqual(fixture.lock.read_bytes(), unexpected_content)

    def test_activation_lock_empty_aba_while_launcher_runs_is_rejected(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        fixture.replace_launcher_behavior(
            "#!/usr/bin/env python3\n"
            "import os\n"
            "from pathlib import Path\n"
            "import sys\n"
            f"lock = Path({str(fixture.lock)!r})\n"
            "before = lock.stat()\n"
            "lock.write_bytes(b'temporary lock state\\n')\n"
            "lock.write_bytes(b'')\n"
            "os.utime(\n"
            "    lock,\n"
            "    ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),\n"
            "    follow_symlinks=False,\n"
            ")\n"
            f"sys.stdout.buffer.write({fixture.envelope_raw!r})\n"
        )
        before = fixture.lock.stat()

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
        )

        after = fixture.lock.stat()
        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertEqual(after.st_size, 0)
        self.assertNotEqual(
            (after.st_mtime_ns, after.st_ctime_ns),
            (before.st_mtime_ns, before.st_ctime_ns),
        )

    def test_visible_activation_lock_full_identity_drift_is_rejected_after_launcher(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        injected = self.root / "visible-lock-identity-drift-injected"

        result = fixture.invoke_with_visible_lock_identity_drift(
            injected,
            "validate",
            "--bundle",
            str(fixture.bundle),
        )

        self.assertTrue(injected.is_file(), "visible identity hook did not run")
        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertEqual(fixture.lock.stat().st_size, 0)

    def test_retained_input_replacement_while_launcher_runs_is_rejected(
        self,
    ) -> None:
        for name, target_attribute, mode in (
            ("receipt", "deployment", 0o600),
            ("active", "active_path", 0o600),
            ("context", "trust_context", 0o600),
            ("validator", "validator_module", 0o600),
            ("runtime", "runtime_trust_payload", 0o600),
            ("client", "client", 0o500),
            ("launcher", "launcher", 0o500),
            ("shim", "shim", 0o500),
            ("bundle", "bundle_manifest", 0o600),
        ):
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                launcher_started = fixture.root / "launcher-started"
                launcher_continue = fixture.root / "launcher-continue"
                install_launcher_behavior(
                    fixture,
                    "marker_gate",
                    started=launcher_started,
                    continuation=launcher_continue,
                    deadline_seconds=5,
                    output=fixture.envelope_raw,
                )
                target = getattr(fixture, target_attribute)
                replacement = fixture.root / f"{name}-replacement"
                replacement.write_bytes(target.read_bytes())
                replacement.chmod(mode)
                process = subprocess.Popen(
                    fixture.command(
                        "validate",
                        "--bundle",
                        str(fixture.bundle),
                    ),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=CLIENT_ENVIRONMENT,
                )
                try:
                    deadline = time.monotonic() + 3
                    while not launcher_started.exists() and process.poll() is None:
                        if time.monotonic() >= deadline:
                            self.fail("launcher did not reach the mutation barrier")
                        time.sleep(0.01)
                    self.assertIsNone(process.poll())
                    os.replace(replacement, target)
                    launcher_continue.write_text("continue", encoding="utf-8")
                    stdout, stderr = process.communicate(timeout=5)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate(timeout=5)

                self.assertEqual(process.returncode, 70, stderr.decode())
                self.assertEqual(stdout, b"")

    def test_transient_launcher_aba_is_rejected(self) -> None:
        for mode in ("rename-restore", "in-place-restore"):
            with self.subTest(mode=mode):
                fixture = ValidInvocationFixture(self.root / mode)
                launcher_started = fixture.root / "launcher-started"
                replacement = fixture.root / "replacement-launcher.py"
                original_raw = fixture.launcher.read_bytes()
                original_metadata = fixture.launcher.stat()
                backup = fixture.root / "original-launcher.py"
                write_configured_launcher(
                    replacement,
                    "aba_restore",
                    launcher=fixture.launcher,
                    backup=backup,
                    started=launcher_started,
                    restore_mode=("rename" if mode == "rename-restore" else "in_place"),
                    original=original_raw,
                    times=[
                        original_metadata.st_atime_ns,
                        original_metadata.st_mtime_ns,
                    ],
                    output=fixture.envelope_raw,
                )
                driver = fixture.root / "launcher_aba_driver.py"
                write_configured_driver(
                    driver,
                    RETAINED_STATE_DRIVER_SOURCE,
                    {
                        "scenario": "launcher-aba",
                        "mode": mode,
                        "launcher": str(fixture.launcher),
                        "replacement": str(replacement),
                        "backup": str(backup),
                    },
                )

                result = fixture.invoke(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                    driver=driver,
                    timeout=10,
                )

                self.assertEqual(result.returncode, 70, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                self.assertTrue(launcher_started.exists())

    def test_unrelated_ancestor_churn_does_not_invalidate_validation(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        launcher_started = fixture.root / "launcher-started"
        launcher_continue = fixture.root / "launcher-continue"
        install_launcher_behavior(
            fixture,
            "marker_gate",
            started=launcher_started,
            continuation=launcher_continue,
            deadline_seconds=5,
            output=fixture.envelope_raw,
        )
        process = subprocess.Popen(
            fixture.command(
                "validate",
                "--bundle",
                str(fixture.bundle),
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=CLIENT_ENVIRONMENT,
        )
        try:
            deadline = time.monotonic() + 3
            while not launcher_started.exists() and process.poll() is None:
                if time.monotonic() >= deadline:
                    self.fail("launcher did not reach the ancestor-churn barrier")
                time.sleep(0.01)
            self.assertIsNone(process.poll())
            unrelated = fixture.account_home / "unrelated"
            unrelated.write_text("temporary", encoding="utf-8")
            unrelated.unlink()
            launcher_continue.touch()
            stdout, stderr = process.communicate(timeout=5)
        finally:
            launcher_continue.touch(exist_ok=True)
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

        self.assertEqual(process.returncode, 0, stderr.decode())
        self.assertEqual(stdout, fixture.envelope_raw)

    def test_unrelated_interpreter_sibling_churn_does_not_invalidate_validation(
        self,
    ) -> None:
        interpreter_directory = self.root / "shared-bin"
        interpreter_directory.mkdir()
        pinned_interpreter = interpreter_directory / "python"
        shutil.copy2(Path(sys.executable).resolve(), pinned_interpreter)
        fixture = ValidInvocationFixture(
            self.root / "fixture",
            interpreter_executable=pinned_interpreter,
        )
        launcher_started = fixture.root / "launcher-started"
        launcher_continue = fixture.root / "launcher-continue"
        install_launcher_behavior(
            fixture,
            "marker_gate",
            started=launcher_started,
            continuation=launcher_continue,
            deadline_seconds=5,
            output=fixture.envelope_raw,
        )
        process = subprocess.Popen(
            fixture.command(
                "validate",
                "--bundle",
                str(fixture.bundle),
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=CLIENT_ENVIRONMENT,
        )
        try:
            deadline = time.monotonic() + 3
            while not launcher_started.exists() and process.poll() is None:
                if time.monotonic() >= deadline:
                    self.fail("launcher did not reach the interpreter-churn barrier")
                time.sleep(0.01)
            self.assertIsNone(process.poll())
            unrelated = interpreter_directory / "unrelated"
            unrelated.write_text("temporary", encoding="utf-8")
            unrelated.unlink()
            launcher_continue.touch()
            stdout, stderr = process.communicate(timeout=5)
        finally:
            launcher_continue.touch(exist_ok=True)
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

        self.assertEqual(process.returncode, 0, stderr.decode())
        self.assertEqual(stdout, fixture.envelope_raw)

    def test_interpreter_sibling_churn_during_descriptor_open_is_accepted(
        self,
    ) -> None:
        interpreter_directory = self.root / "shared-bin"
        interpreter_directory.mkdir()
        pinned_interpreter = interpreter_directory / "python"
        shutil.copy2(Path(sys.executable).resolve(), pinned_interpreter)
        fixture = ValidInvocationFixture(
            self.root / "fixture",
            interpreter_executable=pinned_interpreter,
        )
        churned = fixture.root / "interpreter-directory-churned"
        driver = fixture.root / "interpreter_preflight_churn_driver.py"
        write_configured_driver(
            driver,
            RETAINED_STATE_DRIVER_SOURCE,
            {
                "scenario": "interpreter-sibling-churn",
                "main_argv_start": 6,
                "pinned_executable": str(pinned_interpreter),
                "interpreter_directory": str(interpreter_directory),
                "churned": str(churned),
            },
        )

        result = fixture.invoke(
            str(interpreter_directory),
            str(churned),
            "validate",
            "--bundle",
            str(fixture.bundle),
            driver=driver,
            timeout=5,
        )

        self.assertTrue(churned.is_file(), "interpreter directory did not churn")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, fixture.envelope_raw)

    def test_selected_interpreter_changes_during_validation_are_rejected(
        self,
    ) -> None:
        for mode in (
            "rename-replacement",
            "in-place-mutation",
            "rename-restore",
            "in-place-restore",
        ):
            with self.subTest(mode=mode):
                root = self.root / mode
                interpreter_directory = root / "shared-bin"
                interpreter_directory.mkdir(parents=True)
                pinned_interpreter = interpreter_directory / "python"
                shutil.copy2(Path(sys.executable).resolve(), pinned_interpreter)
                fixture = ValidInvocationFixture(
                    root / "fixture",
                    interpreter_executable=pinned_interpreter,
                )
                launcher_started = fixture.root / "launcher-started"
                launcher_continue = fixture.root / "launcher-continue"
                install_launcher_behavior(
                    fixture,
                    "marker_gate",
                    started=launcher_started,
                    continuation=launcher_continue,
                    deadline_seconds=5,
                    output=fixture.envelope_raw,
                )
                original_raw = pinned_interpreter.read_bytes()
                original_metadata = pinned_interpreter.stat()
                mutated_raw = bytes([original_raw[0] ^ 1]) + original_raw[1:]
                process = subprocess.Popen(
                    fixture.command(
                        "validate",
                        "--bundle",
                        str(fixture.bundle),
                    ),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=CLIENT_ENVIRONMENT,
                )
                try:
                    deadline = time.monotonic() + 3
                    while not launcher_started.exists() and process.poll() is None:
                        if time.monotonic() >= deadline:
                            self.fail(
                                "launcher did not reach the interpreter-change barrier"
                            )
                        time.sleep(0.01)
                    self.assertIsNone(process.poll())
                    if mode.startswith("rename"):
                        replacement = interpreter_directory / "replacement"
                        replacement.write_bytes(original_raw)
                        replacement.chmod(stat.S_IMODE(original_metadata.st_mode))
                        if mode == "rename-replacement":
                            os.replace(replacement, pinned_interpreter)
                        else:
                            backup = interpreter_directory / "original"
                            while True:
                                os.replace(pinned_interpreter, backup)
                                os.replace(replacement, pinned_interpreter)
                                os.replace(backup, pinned_interpreter)
                                if (
                                    pinned_interpreter.stat().st_ctime_ns
                                    != original_metadata.st_ctime_ns
                                ):
                                    break
                                time.sleep(0.01)
                                replacement.write_bytes(original_raw)
                                replacement.chmod(
                                    stat.S_IMODE(original_metadata.st_mode)
                                )
                    elif mode == "in-place-mutation":
                        pinned_interpreter.write_bytes(mutated_raw)
                    else:
                        while True:
                            pinned_interpreter.write_bytes(mutated_raw)
                            pinned_interpreter.write_bytes(original_raw)
                            pinned_interpreter.chmod(
                                stat.S_IMODE(original_metadata.st_mode)
                            )
                            os.utime(
                                pinned_interpreter,
                                ns=(
                                    original_metadata.st_atime_ns,
                                    original_metadata.st_mtime_ns,
                                ),
                            )
                            if (
                                pinned_interpreter.stat().st_ctime_ns
                                != original_metadata.st_ctime_ns
                            ):
                                break
                            time.sleep(0.01)
                    launcher_continue.touch()
                    stdout, stderr = process.communicate(timeout=5)
                finally:
                    launcher_continue.touch(exist_ok=True)
                    if process.poll() is None:
                        process.kill()
                        process.communicate(timeout=5)

                self.assertEqual(process.returncode, 70, stderr.decode())
                self.assertEqual(stdout, b"")

    def test_selected_interpreter_parent_mapping_change_is_rejected(self) -> None:
        interpreter_directory = self.root / "shared-bin"
        interpreter_directory.mkdir()
        pinned_interpreter = interpreter_directory / "python"
        shutil.copy2(Path(sys.executable).resolve(), pinned_interpreter)
        fixture = ValidInvocationFixture(
            self.root / "fixture",
            interpreter_executable=pinned_interpreter,
        )
        launcher_started = fixture.root / "launcher-started"
        launcher_continue = fixture.root / "launcher-continue"
        install_launcher_behavior(
            fixture,
            "marker_gate",
            started=launcher_started,
            continuation=launcher_continue,
            deadline_seconds=5,
            output=fixture.envelope_raw,
        )
        process = subprocess.Popen(
            fixture.command(
                "validate",
                "--bundle",
                str(fixture.bundle),
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=CLIENT_ENVIRONMENT,
        )
        try:
            deadline = time.monotonic() + 3
            while not launcher_started.exists() and process.poll() is None:
                if time.monotonic() >= deadline:
                    self.fail("launcher did not reach the parent-change barrier")
                time.sleep(0.01)
            self.assertIsNone(process.poll())
            original_directory = self.root / "original-shared-bin"
            os.replace(interpreter_directory, original_directory)
            interpreter_directory.mkdir()
            shutil.copy2(Path(sys.executable).resolve(), pinned_interpreter)
            launcher_continue.touch()
            stdout, stderr = process.communicate(timeout=5)
        finally:
            launcher_continue.touch(exist_ok=True)
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

        self.assertEqual(process.returncode, 70, stderr.decode())
        self.assertEqual(stdout, b"")

    def test_bundle_child_swap_between_preflight_and_open_is_rejected(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        target = fixture.bundle / "manifest.json"
        replacement = self.root / "replacement-manifest.json"
        replacement_raw = canonical_document(
            {
                "producer": {
                    "producer_id": "task-witness-smoke-producer",
                    "contract": "task-witness-smoke-bundle-v1",
                    "implementation_sha256": "3" * 64,
                }
            }
        )
        replacement.write_bytes(replacement_raw)
        replacement.chmod(0o600)
        replacement_bundle_sha256 = bundle_identity({"manifest.json": replacement_raw})
        replacement_anchor = {
            **fixture.expected_anchor,
            "bundle_sha256": replacement_bundle_sha256,
        }
        replacement_witness = {
            **fixture.witness,
            "bundle_sha256": replacement_bundle_sha256,
            "producer": {
                **fixture.witness["producer"],
                "implementation_sha256": "3" * 64,
            },
        }
        fixture.replace_launcher_envelope(
            {
                "contract": "task-witness-launch-envelope-v1",
                "anchor": replacement_anchor,
                "witness": replacement_witness,
            }
        )
        audit_marker = self.root / "bundle-swapped"

        result = fixture.invoke_with_open_swap(
            target,
            replacement,
            audit_marker,
            "validate",
            "--bundle",
            str(fixture.bundle),
        )

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertTrue(audit_marker.is_file(), "audit hook did not swap the bundle")

    def test_retained_symlink_swap_before_open_is_rejected(self) -> None:
        for name, target_attribute in (
            ("active", "active_path"),
            ("context", "trust_context"),
            ("validator", "validator_module"),
            ("runtime", "runtime_trust_payload"),
            ("client", "client"),
            ("launcher", "launcher"),
            ("shim", "shim"),
        ):
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                launcher_marker = fixture.root / "launcher-executed"
                audit_marker = fixture.root / "target-swapped"
                install_launcher_behavior(
                    fixture,
                    "marker_output",
                    marker=launcher_marker,
                    output=fixture.envelope_raw,
                )
                target = getattr(fixture, target_attribute)
                symlink_target = fixture.root / f"{name}-symlink-target"
                symlink_target.write_bytes(target.read_bytes())
                symlink_target.chmod(stat.S_IMODE(target.stat().st_mode))
                replacement = fixture.root / f"{name}-replacement"
                replacement.symlink_to(symlink_target)

                result = fixture.invoke_with_open_swap(
                    target,
                    replacement,
                    audit_marker,
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                )

                self.assertEqual(result.returncode, 70, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                self.assertTrue(audit_marker.is_file())
                self.assertFalse(launcher_marker.exists())

    def test_deployment_receipt_fifo_swap_between_preflight_and_open_is_rejected(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        replacement = self.root / "replacement-deployment.fifo"
        os.mkfifo(replacement, mode=0o600)
        audit_marker = self.root / "deployment-swapped"

        result = fixture.invoke_with_open_swap(
            fixture.deployment,
            replacement,
            audit_marker,
            "validate",
            "--bundle",
            str(fixture.bundle),
            timeout=3,
        )

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertTrue(
            audit_marker.is_file(),
            "audit hook did not swap the deployment receipt",
        )

    def test_duplicate_bundle_option_rejects_before_installation_access(self) -> None:
        result, installation_access = self.invoke_with_installation_sentinel(
            "validate",
            "--bundle",
            "/tmp/first-bundle",
            "--bundle",
            "/tmp/second-bundle",
        )

        self.assertEqual(result.returncode, 64, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertFalse(installation_access.exists())
        self.assertLessEqual(len(result.stderr), 4 * 1024)
        self.assert_diagnostic(
            result.stderr,
            message="invalid public arguments",
            validator_code_executed="no",
            active_state_changed="no",
            current_receipt="unknown",
            next_action="invoke the canonical shim with documented arguments",
        )

    def test_valid_historical_form_reaches_installation(self) -> None:
        result, installation_access = self.invoke_with_installation_sentinel(
            "validate",
            "--bundle",
            "/tmp/bundle",
            "--historical",
            "--trust-context-sha256",
            "a" * 64,
        )

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertTrue(installation_access.is_file())

    def test_unapproved_historical_context_never_reaches_the_launcher(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        marker = self.root / "launcher-ran"
        install_launcher_behavior(
            fixture,
            "marker_output",
            marker=marker,
            output=fixture.envelope_raw,
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            "--historical",
            "--trust-context-sha256",
            "a" * 64,
        )

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertFalse(marker.exists())

    def test_revoked_historical_context_never_reaches_the_launcher(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        _, revoked_digest, _ = fixture.add_historical_context(state="revoked")
        marker = self.root / "launcher-ran"
        install_launcher_behavior(
            fixture,
            "marker_output",
            marker=marker,
            output=fixture.envelope_raw,
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            "--historical",
            "--trust-context-sha256",
            revoked_digest,
        )

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertFalse(marker.exists())

    def test_dormant_historical_registry_paths_require_canonical_text(self) -> None:
        for state in ("historical-usable", "revoked"):
            with self.subTest(state=state):
                fixture = ValidInvocationFixture(self.root / state)
                _, digest, _ = fixture.add_historical_context(state=state)
                fixture.historical_trust_contexts[0]["path"] = (
                    f"{fixture.context_directory}/./sha256-{digest}.json"
                )
                fixture._write_deployment_receipt()
                marker = fixture.root / "launcher-ran"
                install_launcher_behavior(
                    fixture,
                    "marker_output",
                    marker=marker,
                    output=fixture.envelope_raw,
                )

                result = fixture.invoke(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                )

                self.assertEqual(result.returncode, 70, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                self.assertFalse(marker.exists())

    def test_empty_historical_trust_context_never_reaches_the_launcher(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        malformed_raw = canonical_document(
            document(
                {
                    "schema_version": 1,
                    "contract": "task-witness-trust-context-v2",
                    "producers": [],
                    "issuers": [],
                    "validators": [],
                }
            )
        )
        _, malformed_digest, _ = fixture.add_historical_context_raw(malformed_raw)
        marker = self.root / "launcher-ran"
        install_launcher_behavior(
            fixture,
            "marker_output",
            marker=marker,
            output=fixture.envelope_raw,
        )

        result = fixture.invoke(
            "validate",
            "--bundle",
            str(fixture.bundle),
            "--historical",
            "--trust-context-sha256",
            malformed_digest,
        )

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertFalse(marker.exists())

    def test_retained_trust_context_closure_is_strict(self) -> None:
        def extra_context_field(value: dict[str, Any]) -> None:
            value["unexpected"] = True

        def arbitrary_validator_path(value: dict[str, Any]) -> None:
            value["validators"][0]["modules"][0]["path"] = "/tmp/validator.py"

        def validator_content_disagreement(value: dict[str, Any]) -> None:
            value["validators"][0]["modules"][0]["sha256"] = "4" * 64

        def validator_implementation_disagreement(value: dict[str, Any]) -> None:
            value["validators"][0]["implementation_sha256"] = "5" * 64
            value["producers"][0]["validator_implementation_sha256"] = "5" * 64

        def unregistered_producer_validator(value: dict[str, Any]) -> None:
            value["producers"][0]["validator_id"] = "missing-validator"

        cases = {
            "extra-context-field": extra_context_field,
            "arbitrary-validator-path": arbitrary_validator_path,
            "validator-content-disagreement": validator_content_disagreement,
            "validator-implementation-disagreement": (
                validator_implementation_disagreement
            ),
            "unregistered-producer-validator": unregistered_producer_validator,
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                context = json.loads(fixture.trust_raw)
                context.pop("content_sha256")
                mutate(context)
                raw = canonical_document(document(context))
                _, digest, _ = fixture.add_historical_context_raw(raw)
                marker = fixture.root / "launcher-ran"
                install_launcher_behavior(
                    fixture,
                    "marker_output",
                    marker=marker,
                    output=fixture.envelope_raw,
                )

                result = fixture.invoke(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                    "--historical",
                    "--trust-context-sha256",
                    digest,
                )

                self.assertEqual(result.returncode, 70, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                self.assertFalse(marker.exists())

    def test_intrinsic_smoke_identity_is_controller_derived(self) -> None:
        def alternate_digest(current: str) -> str:
            return "0" * 64 if current != "0" * 64 else "1" * 64

        def arbitrary_producer_implementation(
            fixture: ValidInvocationFixture,
            context: dict[str, Any],
        ) -> None:
            implementation = alternate_digest(
                context["producers"][0]["implementation_sha256"]
            )
            context["producers"][0]["implementation_sha256"] = implementation
            fixture.producer_implementation_sha256 = implementation

        def arbitrary_issuer_implementation(
            fixture: ValidInvocationFixture,
            context: dict[str, Any],
        ) -> None:
            implementation = alternate_digest(
                context["issuers"][0]["implementation_sha256"]
            )
            context["issuers"][0]["implementation_sha256"] = implementation
            fixture.issuer_implementation_sha256 = implementation

        def alternate_validator_module(
            fixture: ValidInvocationFixture,
            context: dict[str, Any],
        ) -> None:
            name = "alternate-smoke-validator"
            module_sha256 = sha256(fixture.validator_module_raw)
            implementation = validator_identity(
                fixture.validator_contract,
                name,
                [(name, module_sha256)],
            )
            generation = fixture.validator_directory / f"sha256-{implementation}"
            generation.mkdir(mode=0o700)
            module = generation / f"{name}.py"
            module.write_bytes(fixture.validator_module_raw)
            module.chmod(0o600)
            producer_implementation = sha256(
                canonical_value(
                    {
                        "contract": "task-witness-smoke-producer-implementation-v1",
                        "validator_implementation_sha256": implementation,
                    }
                )
            )
            context["validators"][0].update(
                implementation_sha256=implementation,
                entrypoint=name,
                modules=[
                    {
                        "name": name,
                        "path": str(module),
                        "sha256": module_sha256,
                    }
                ],
            )
            context["producers"][0].update(
                implementation_sha256=producer_implementation,
                validator_implementation_sha256=implementation,
            )
            fixture.validator_module_name = name
            fixture.validator_module_sha256 = module_sha256
            fixture.validator_implementation_sha256 = implementation
            fixture.validator_module = module
            fixture.producer_implementation_sha256 = producer_implementation

        def unchanged_context(
            fixture: ValidInvocationFixture,
            context: dict[str, Any],
        ) -> None:
            del fixture, context

        def unchanged_receipt(receipt: dict[str, Any]) -> None:
            del receipt

        def alternate_declaration_content(receipt: dict[str, Any]) -> None:
            provider = receipt["providers"][0]
            content_sha256 = alternate_digest(provider["declaration_content_sha256"])
            provider["declaration_content_sha256"] = content_sha256
            provider["declaration_sha256"] = sha256(
                canonical_document(
                    {
                        "contract": "task-witness-intrinsic-smoke-provider-v1",
                        "content_sha256": content_sha256,
                    }
                )
            )

        def alternate_declaration_raw(receipt: dict[str, Any]) -> None:
            provider = receipt["providers"][0]
            provider["declaration_sha256"] = alternate_digest(
                provider["declaration_sha256"]
            )

        cases = {
            "producer-implementation": (
                arbitrary_producer_implementation,
                unchanged_receipt,
            ),
            "issuer-implementation": (
                arbitrary_issuer_implementation,
                unchanged_receipt,
            ),
            "declaration-content": (
                unchanged_context,
                alternate_declaration_content,
            ),
            "declaration-raw": (unchanged_context, alternate_declaration_raw),
            "validator-entrypoint-module": (
                alternate_validator_module,
                unchanged_receipt,
            ),
        }
        for name, (mutate_context, mutate_receipt) in cases.items():
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                context = json.loads(fixture.trust_raw)
                context.pop("content_sha256")
                mutate_context(fixture, context)
                self._replace_active_trust_context(fixture, context)
                marker = fixture.root / "launcher-ran"
                envelope_raw = canonical_document(
                    {
                        "contract": "task-witness-launch-envelope-v1",
                        "anchor": fixture.expected_anchor,
                        "witness": fixture.witness,
                    }
                )
                install_launcher_behavior(
                    fixture,
                    "marker_output",
                    marker=marker,
                    output=envelope_raw,
                )
                self._rewrite_deployment_receipt(fixture, mutate_receipt)

                result = fixture.invoke(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                )

                self.assertEqual(result.returncode, 70, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                self.assertFalse(marker.exists())

    def test_retained_trust_role_authority_and_order_are_strict(self) -> None:
        def alternate_implementation(current: str) -> str:
            return "0" * 64 if current != "0" * 64 else "1" * 64

        def duplicate_authority(
            category: str,
            fixture: ValidInvocationFixture,
            context: dict[str, Any],
        ) -> None:
            del fixture
            original = context[category][0]
            duplicate = {
                **original,
                "implementation_sha256": alternate_implementation(
                    original["implementation_sha256"]
                ),
            }
            context[category].append(duplicate)
            identifier = f"{category[:-1]}_id"
            context[category].sort(
                key=lambda item: (
                    item[identifier],
                    item["contract"],
                    item["implementation_sha256"],
                )
            )

        def duplicate_validator_authority(
            fixture: ValidInvocationFixture,
            context: dict[str, Any],
        ) -> None:
            original = context["validators"][0]
            module = original["modules"][0]
            alternate_raw = fixture.validator_module_raw + b"# alternate authority\n"
            alternate_module_sha256 = sha256(alternate_raw)
            alternate_implementation_sha256 = validator_identity(
                original["contract"],
                original["entrypoint"],
                [(module["name"], alternate_module_sha256)],
            )
            generation = (
                fixture.validator_directory
                / f"sha256-{alternate_implementation_sha256}"
            )
            generation.mkdir(mode=0o700)
            alternate_module = generation / f"{module['name']}.py"
            alternate_module.write_bytes(alternate_raw)
            alternate_module.chmod(0o600)
            context["validators"].append(
                {
                    **original,
                    "implementation_sha256": alternate_implementation_sha256,
                    "modules": [
                        {
                            **module,
                            "path": str(alternate_module),
                            "sha256": alternate_module_sha256,
                        }
                    ],
                }
            )
            context["validators"].sort(
                key=lambda item: (
                    item["validator_id"],
                    item["contract"],
                    item["implementation_sha256"],
                )
            )

        def unsorted_inventory(
            category: str,
            fixture: ValidInvocationFixture,
            context: dict[str, Any],
        ) -> None:
            del fixture
            identifier = f"{category[:-1]}_id"
            original = context[category][0]
            context[category].append(
                {
                    **original,
                    identifier: f"alternate-{category[:-1]}",
                    "implementation_sha256": alternate_implementation(
                        original["implementation_sha256"]
                    ),
                }
            )

        def unsorted_validator_inventory(
            fixture: ValidInvocationFixture,
            context: dict[str, Any],
        ) -> None:
            del fixture
            original = context["validators"][0]
            context["validators"].append(
                {**original, "validator_id": "alternate-validator"}
            )

        def unsorted_issuer_capabilities(
            fixture: ValidInvocationFixture,
            context: dict[str, Any],
        ) -> None:
            del fixture
            context["issuers"][0]["capabilities"] = [
                "audit-smoke",
                "activation-smoke",
            ]

        cases = {
            "duplicate-validator-authority": duplicate_validator_authority,
            "duplicate-producer-authority": lambda fixture, context: (
                duplicate_authority("producers", fixture, context)
            ),
            "duplicate-issuer-authority": lambda fixture, context: duplicate_authority(
                "issuers", fixture, context
            ),
            "unsorted-validator-inventory": unsorted_validator_inventory,
            "unsorted-producer-inventory": lambda fixture, context: unsorted_inventory(
                "producers", fixture, context
            ),
            "unsorted-issuer-inventory": lambda fixture, context: unsorted_inventory(
                "issuers", fixture, context
            ),
            "unsorted-issuer-capabilities": unsorted_issuer_capabilities,
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                context = json.loads(fixture.trust_raw)
                context.pop("content_sha256")
                mutate(fixture, context)
                raw = canonical_document(document(context))
                _, digest, _ = fixture.add_historical_context_raw(raw)
                marker = fixture.root / "launcher-ran"
                install_launcher_behavior(
                    fixture,
                    "marker_output",
                    marker=marker,
                    output=fixture.envelope_raw,
                )

                result = fixture.invoke(
                    "validate",
                    "--bundle",
                    str(fixture.bundle),
                    "--historical",
                    "--trust-context-sha256",
                    digest,
                )

                self.assertEqual(result.returncode, 70, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                self.assertFalse(marker.exists())

    def test_invalid_historical_forms_reject_before_installation_access(self) -> None:
        digest = "a" * 64
        cases = {
            "historical_without_digest": [
                "validate",
                "--bundle",
                "/tmp/bundle",
                "--historical",
            ],
            "digest_without_historical": [
                "validate",
                "--bundle",
                "/tmp/bundle",
                "--trust-context-sha256",
                digest,
            ],
            "public_trust_context_path": [
                "validate",
                "--bundle",
                "/tmp/bundle",
                "--trust-context",
                "/tmp/context.json",
            ],
            "reordered_pair": [
                "validate",
                "--bundle",
                "/tmp/bundle",
                "--trust-context-sha256",
                digest,
                "--historical",
            ],
            "uppercase_digest": [
                "validate",
                "--bundle",
                "/tmp/bundle",
                "--historical",
                "--trust-context-sha256",
                digest.upper(),
            ],
            "short_digest": [
                "validate",
                "--bundle",
                "/tmp/bundle",
                "--historical",
                "--trust-context-sha256",
                "a" * 63,
            ],
            "duplicate_historical": [
                "validate",
                "--bundle",
                "/tmp/bundle",
                "--historical",
                "--historical",
                "--trust-context-sha256",
                digest,
            ],
        }
        for name, arguments in cases.items():
            with self.subTest(name=name):
                result, installation_access = self.invoke_with_installation_sentinel(
                    *arguments
                )

                self.assertEqual(result.returncode, 64, result.stderr.decode())
                self.assertEqual(result.stdout, b"")
                self.assertFalse(installation_access.exists())
