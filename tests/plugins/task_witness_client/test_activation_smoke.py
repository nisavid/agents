from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from tests.plugins.task_witness_deployment._support import (
    ProviderFixture,
    load_deployment_module,
)

from ._support import (
    CLIENT_ENVIRONMENT,
    ValidInvocationFixture,
    _TaskWitnessClientTestCase,
    activation_lock_identity,
    canonical_document,
    canonical_value,
    document,
    full_filesystem_identity,
    installed_file,
    sha256,
)

ACTIVATION_SMOKE_DRIVER = Path(__file__).with_name("_activation_smoke_driver.py")


class ActivationSmokeTests(_TaskWitnessClientTestCase):
    def _refresh_fixture_client_generation(
        self,
        fixture: ValidInvocationFixture,
    ) -> None:
        raw = fixture.client.read_bytes()
        prefix = b"CLIENT_SOURCE_" + b'GENERATION_SHA256 = "'
        start = raw.index(prefix) + len(prefix)
        end = start + 64
        normalized = raw[:start] + (b"0" * 64) + raw[end:]
        rendered = raw[:start] + sha256(normalized).encode("ascii") + raw[end:]
        fixture.client.chmod(0o700)
        fixture.client.write_bytes(rendered)
        fixture.client.chmod(0o500)
        fixture._write_deployment_receipt()

    def _routine_trust_variants(
        self,
        fixture: ValidInvocationFixture,
    ) -> dict[str, object]:
        base_context = json.loads(fixture.trust_raw)
        base_context.pop("content_sha256")
        base_intrinsic = json.loads(json.dumps(fixture.receipt["providers"][0]))
        base_history = json.loads(json.dumps(fixture.historical_trust_contexts))

        def thaw(value: object) -> object:
            if isinstance(value, Mapping):
                return {key: thaw(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [thaw(item) for item in value]
            return value

        def materialize(name: str, suffix: bytes) -> tuple[object, dict[str, object]]:
            provider_fixture = ProviderFixture(
                fixture.root / f"routine-provider-{name}",
                prefix="zeta",
            )
            for module, declaration in zip(
                (provider_fixture.entrypoint, provider_fixture.helper),
                provider_fixture.validator_modules,
            ):
                raw = module.read_bytes() + suffix
                module.write_bytes(raw)
                declaration.update(length=len(raw), sha256=sha256(raw))
            provider_fixture.refresh_validator_identity()
            provider_fixture.write()
            provider = load_deployment_module().materialize_provider(
                provider_fixture.root,
                fixture.install / "trust",
            )
            if provider is None:
                self.fail("routine trust provider was not materialized")
            declaration_raw = provider_fixture.provider_path.read_bytes()
            declaration = json.loads(declaration_raw)
            projection = {
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
                    for module in sorted(
                        provider.modules,
                        key=lambda item: str(item.path),
                    )
                ],
            }
            return provider, projection

        provider_a, projection_a = materialize("a", b"# routine trust A\n")
        provider_b, projection_b = materialize("b", b"# routine trust B\n")
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
            "plugin_id": provider_a.plugin_id,
            "authority_profile": provider_a.authority_profile,
            **{
                category: [
                    {key: item[key] for key in fields}
                    for item in getattr(provider_a, category)
                ]
                for category, fields in policy_fields.items()
            },
        }
        policy = json.loads(fixture.policy.read_bytes())
        policy.pop("content_sha256")
        policy["source"].update(
            plugin_id=provider_a.plugin_id,
            publisher_id=provider_a.publisher,
            repository_url=provider_a.repository,
        )
        policy["providers"] = [policy_provider]
        policy = document(policy)
        policy_raw = canonical_document(policy)
        fixture.policy.write_bytes(policy_raw)
        fixture.policy.chmod(0o600)

        variants: dict[str, dict[str, object]] = {}
        for name, provider, projection in (
            ("a", provider_a, projection_a),
            ("b", provider_b, projection_b),
        ):
            context = json.loads(json.dumps(base_context))
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
            raw = canonical_document(document(context))
            digest = sha256(raw)
            path = fixture.context_directory / f"sha256-{digest}.json"
            path.write_bytes(raw)
            path.chmod(0o600)
            variants[name] = {
                "raw": raw,
                "sha256": digest,
                "path": path,
                "context": context,
                "providers": [base_intrinsic, projection],
            }

        def select(name: str) -> None:
            variant = variants["a" if name == "c" else name]
            fixture.trust_raw = variant["raw"]
            fixture.trust_sha256 = variant["sha256"]
            fixture.trust_context = variant["path"]
            fixture.expected_anchor = {
                **fixture.expected_anchor,
                "trust_context_sha256": variant["sha256"],
            }

        def patch_receipt(name: str) -> None:
            variant = variants["a" if name == "c" else name]
            generated_retained = fixture.retained_deployment_receipt
            receipt = json.loads(json.dumps(fixture.receipt))
            receipt.pop("content_sha256")
            receipt["providers"] = variant["providers"]
            external = variant["providers"][1]
            receipt["source"].update(
                plugin_id=external["plugin_id"],
                publisher_id=external["publisher"],
                repository_url=external["repository"],
                provider_declaration_sha256=external["declaration_sha256"],
                provider_declaration_content_sha256=external[
                    "declaration_content_sha256"
                ],
            )
            additions = {
                "a": [],
                "b": [
                    {
                        "path": str(variants["a"]["path"]),
                        "sha256": variants["a"]["sha256"],
                        "state": "historical-usable",
                    }
                ],
                "c": [
                    {
                        "path": str(variants["b"]["path"]),
                        "sha256": variants["b"]["sha256"],
                        "state": "historical-usable",
                    }
                ],
            }
            receipt["historical_trust_contexts"] = sorted(
                json.loads(json.dumps(base_history)) + additions[name],
                key=lambda item: item["sha256"],
            )
            fixture.receipt = document(receipt)
            fixture.deployment_raw = canonical_document(fixture.receipt)
            fixture.deployment.write_bytes(fixture.deployment_raw)
            fixture.deployment.chmod(0o600)
            retained = fixture.receipts_directory / (
                f"sha256-{sha256(fixture.deployment_raw)}.json"
            )
            if generated_retained != retained:
                generated_retained.unlink()
            retained.write_bytes(fixture.deployment_raw)
            retained.chmod(0o600)
            fixture.retained_deployment_receipt = retained

        return {
            "variants": variants,
            "select": select,
            "patch_receipt": patch_receipt,
        }

    def _capture_active_state(
        self,
        fixture: ValidInvocationFixture,
    ) -> dict[str, object]:
        return {
            "active": json.loads(json.dumps(fixture.active)),
            "active_raw": fixture.active_raw,
            "expected_anchor": json.loads(json.dumps(fixture.expected_anchor)),
            "generation": fixture.generation,
            "payloads": json.loads(json.dumps(fixture.payloads)),
            "runtime_sha256": fixture.runtime_sha256,
        }

    def _restore_active_state(
        self,
        fixture: ValidInvocationFixture,
        state: dict[str, object],
    ) -> None:
        for name, value in state.items():
            setattr(fixture, name, value)
        fixture.active_path.write_bytes(state["active_raw"])
        fixture.active_path.chmod(0o600)

    def _binding_for_raw(
        self,
        path: Path,
        raw: bytes,
        mode: int,
    ) -> dict[str, object]:
        return {
            "path": str(path),
            "length": len(raw),
            "sha256": sha256(raw),
            "owner": os.geteuid(),
            "mode": mode,
        }

    def _activation_unit(
        self,
        fixture: ValidInvocationFixture,
        receipt: dict[str, object],
        receipt_raw: bytes,
        active_raw: bytes,
    ) -> dict[str, object]:
        return {
            "state": "active",
            "deployment_receipt": self._binding_for_raw(
                fixture.deployment,
                receipt_raw,
                0o600,
            ),
            "active_record": self._binding_for_raw(
                fixture.active_path,
                active_raw,
                0o600,
            ),
            "control_set": json.loads(json.dumps(receipt["control_set"])),
            "smoke": json.loads(json.dumps(receipt["smoke"])),
        }

    def _prepare_routine_activation_smoke(
        self,
        fixture: ValidInvocationFixture,
        *,
        phase: str,
        trust_change: bool = False,
        successor: bool = False,
    ) -> dict[str, object]:
        self._refresh_fixture_client_generation(fixture)
        trust_variants = self._routine_trust_variants(fixture) if trust_change else None
        if trust_variants is not None:
            trust_variants["select"]("a")
            fixture._write_deployment_receipt()
            trust_variants["patch_receipt"]("a")
        launcher_marker = fixture.root / "routine-activation-launcher-ran"
        a_state = self._capture_active_state(fixture)
        a_envelope = json.loads(json.dumps(fixture.smoke_expected_envelope))

        fixture.runtime_payload_variant = b"routine candidate B\n"
        if trust_variants is not None:
            trust_variants["select"]("b")
        fixture._write_active_record()
        b_state = self._capture_active_state(fixture)
        fixture._write_deployment_receipt()
        if trust_variants is not None:
            trust_variants["patch_receipt"]("b")
        b_envelope = json.loads(json.dumps(fixture.smoke_expected_envelope))

        c_state = None
        c_envelope = None
        if successor:
            if trust_variants is not None:
                trust_variants["select"]("c")
            fixture.runtime_payload_variant = b"routine candidate C\n"
            fixture._write_active_record()
            c_state = self._capture_active_state(fixture)
            fixture._write_deployment_receipt()
            if trust_variants is not None:
                trust_variants["patch_receipt"]("c")
            c_envelope = json.loads(json.dumps(fixture.smoke_expected_envelope))

        launcher_source = (
            "#!/usr/bin/env python3\n"
            "from pathlib import Path\n"
            "import sys\n"
            f"active_raw = Path({str(fixture.active_path)!r}).read_bytes()\n"
            f"if active_raw == {a_state['active_raw']!r}:\n"
            f"    envelope = {canonical_document(a_envelope)!r}\n"
            f"elif active_raw == {b_state['active_raw']!r}:\n"
            f"    envelope = {canonical_document(b_envelope)!r}\n"
        )
        if successor:
            launcher_source += (
                f"elif active_raw == {c_state['active_raw']!r}:\n"
                f"    envelope = {canonical_document(c_envelope)!r}\n"
            )
        launcher_source += (
            "else:\n"
            "    raise SystemExit(91)\n"
            f"Path({str(launcher_marker)!r}).write_text('ran', encoding='utf-8')\n"
            "sys.stdout.buffer.write(envelope)\n"
        )
        fixture.write_launcher_behavior(launcher_source)

        self._restore_active_state(fixture, a_state)
        if trust_variants is not None:
            trust_variants["select"]("a")
        fixture._write_deployment_receipt()
        if trust_variants is not None:
            trust_variants["patch_receipt"]("a")
        self.assertEqual(fixture.smoke_expected_envelope, a_envelope)
        a_receipt = json.loads(json.dumps(fixture.receipt))
        a_raw = fixture.deployment_raw
        a_sha256 = sha256(a_raw)
        a_retained = fixture.retained_deployment_receipt
        a_rollback = fixture.rollback_receipt
        a_rollback_raw = fixture.rollback_receipt_raw
        a_unit = self._activation_unit(
            fixture,
            a_receipt,
            a_raw,
            a_state["active_raw"],
        )

        stage_root = fixture.root / "routine-stage"
        preimage_root = stage_root / "preimage"
        preimage_root.mkdir(parents=True)
        stage_root.chmod(0o700)
        preimage_root.chmod(0o700)
        staged_active = preimage_root / "active.json"
        staged_deployment = preimage_root / "deployment.json"
        staged_active.write_bytes(a_state["active_raw"])
        staged_active.chmod(0o600)
        staged_deployment.write_bytes(a_raw)
        staged_deployment.chmod(0o600)
        selector_preimage = [
            {
                "role": "active-record",
                "staged": installed_file(staged_active),
                "installed": a_unit["active_record"],
            },
            {
                "role": "deployment-alias",
                "staged": installed_file(staged_deployment),
                "installed": a_unit["deployment_receipt"],
            },
        ]
        receipt_parser = {
            "deployment_receipt_contract": a_receipt["contracts"]["deployment_receipt"],
            "rollback_receipt_contract": a_receipt["contracts"]["rollback_receipt"],
            "controller": a_receipt["control_set"]["controller"],
            "client": a_receipt["control_set"]["client"],
        }
        external_dependencies = {
            "interpreter": a_receipt["interpreter"],
            "runtime_closure": a_receipt["runtime_closure"],
            "process_profile": a_receipt["process_profile"],
            "receipt_parser": receipt_parser,
        }

        self._restore_active_state(fixture, b_state)
        if trust_variants is not None:
            trust_variants["select"]("b")
        fixture._write_deployment_receipt()
        if trust_variants is not None:
            trust_variants["patch_receipt"]("b")
        self.assertEqual(fixture.smoke_expected_envelope, b_envelope)
        generated_b_retained = fixture.retained_deployment_receipt
        generated_b_rollback = fixture.rollback_receipt
        b_receipt = json.loads(json.dumps(fixture.receipt))
        b_receipt.pop("content_sha256")

        a_retained.write_bytes(a_raw)
        a_retained.chmod(0o600)
        a_rollback.write_bytes(a_rollback_raw)
        a_rollback.chmod(0o600)
        rollback_value = document(
            {
                "schema_version": 1,
                "contract": "task-witness-rollback-receipt-v1",
                "state": "active",
                "canonical_root": str(fixture.install),
                "effective_uid": os.geteuid(),
                "activation_lock": activation_lock_identity(fixture.lock),
                "deployment_receipt_absent": False,
                "precondition": {
                    "root_identity": full_filesystem_identity(fixture.install),
                    "activation_lock_identity": full_filesystem_identity(fixture.lock),
                    "active_receipt_sha256": a_sha256,
                },
                "prior_receipt": installed_file(a_retained),
                "prior_activation_unit": a_unit,
                "selector_preimage": selector_preimage,
                "external_dependencies": external_dependencies,
                "smoke": a_receipt["smoke"],
            }
        )
        rollback_raw = canonical_document(rollback_value)
        rollback_sha256 = sha256(rollback_raw)
        rollback_path = fixture.receipts_directory / (f"sha256-{rollback_sha256}.json")
        rollback_path.write_bytes(rollback_raw)
        rollback_path.chmod(0o600)

        routine_plan_sha256 = sha256(b"fixture routine A-to-B plan\n")
        maintenance_transaction_sha256 = sha256(
            b"fixture routine A-to-B maintenance transaction\n"
        )
        routine_authorization = document(
            {
                "schema_version": 1,
                "contract": "task-witness-deployer-authorization-v1",
                "purpose": "routine-compatible-forward",
                "canonical_root": str(fixture.install),
                "effective_uid": os.geteuid(),
                "plan_sha256": routine_plan_sha256,
                "maintenance_transaction_sha256": (maintenance_transaction_sha256),
                "candidate_controller_sha256": b_receipt["control_set"]["controller"][
                    "sha256"
                ],
                "candidate_policy_sha256": b_receipt["control_set"]["policy"]["sha256"],
                "source_selection_sha256": b_receipt["source"][
                    "source_selection_sha256"
                ],
                "source_evidence_sha256": b_receipt["source"]["source_evidence"][
                    "source_evidence_sha256"
                ],
                "expected_active_receipt_sha256": a_sha256,
            }
        )
        routine_authorization_raw = canonical_document(routine_authorization)
        b_receipt["sequence"] = a_receipt["sequence"] + 1
        b_receipt["prior_receipt_sha256"] = a_sha256
        b_receipt["authorization"] = {
            "contract": routine_authorization["contract"],
            "purpose": routine_authorization["purpose"],
            "sha256": sha256(routine_authorization_raw),
            "content_sha256": routine_authorization["content_sha256"],
            "plan_sha256": routine_plan_sha256,
            "maintenance_transaction_sha256": (maintenance_transaction_sha256),
            "expected_active_receipt_sha256": a_sha256,
        }
        b_receipt["rollback"] = {
            "state": "active",
            "path": str(rollback_path),
            "sha256": rollback_sha256,
        }
        b_receipt = document(b_receipt)
        b_raw = canonical_document(b_receipt)
        b_sha256 = sha256(b_raw)
        b_retained = fixture.receipts_directory / f"sha256-{b_sha256}.json"
        b_retained.write_bytes(b_raw)
        b_retained.chmod(0o600)
        for provisional in (generated_b_retained, generated_b_rollback):
            if provisional not in {a_retained, a_rollback, rollback_path, b_retained}:
                provisional.unlink(missing_ok=True)

        b_unit = self._activation_unit(
            fixture,
            b_receipt,
            b_raw,
            b_state["active_raw"],
        )
        prior_unit = a_unit
        candidate_unit = b_unit
        candidate_receipt = b_receipt
        candidate_raw = b_raw
        candidate_state = b_state
        candidate_envelope = b_envelope
        transaction_rollback_path = rollback_path
        transaction_rollback_sha256 = rollback_sha256
        transaction_selector_preimage = selector_preimage
        transaction_external_dependencies = external_dependencies
        transaction_stage_root = stage_root
        transaction_plan_sha256 = routine_plan_sha256
        transaction_maintenance_sha256 = maintenance_transaction_sha256
        c_receipt = None
        c_raw = None
        c_retained = None
        c_unit = None
        c_rollback = None
        c_rollback_path = None
        if successor:
            if c_state is None or c_envelope is None:
                self.fail("recursive routine candidate state is missing")
            c_stage_root = fixture.root / "routine-stage-c"
            c_preimage_root = c_stage_root / "preimage"
            c_preimage_root.mkdir(parents=True)
            c_stage_root.chmod(0o700)
            c_preimage_root.chmod(0o700)
            c_staged_active = c_preimage_root / "active.json"
            c_staged_deployment = c_preimage_root / "deployment.json"
            c_staged_active.write_bytes(b_state["active_raw"])
            c_staged_active.chmod(0o600)
            c_staged_deployment.write_bytes(b_raw)
            c_staged_deployment.chmod(0o600)
            c_selector_preimage = [
                {
                    "role": "active-record",
                    "staged": installed_file(c_staged_active),
                    "installed": b_unit["active_record"],
                },
                {
                    "role": "deployment-alias",
                    "staged": installed_file(c_staged_deployment),
                    "installed": b_unit["deployment_receipt"],
                },
            ]
            c_external_dependencies = {
                "interpreter": b_receipt["interpreter"],
                "runtime_closure": b_receipt["runtime_closure"],
                "process_profile": b_receipt["process_profile"],
                "receipt_parser": {
                    "deployment_receipt_contract": b_receipt["contracts"][
                        "deployment_receipt"
                    ],
                    "rollback_receipt_contract": b_receipt["contracts"][
                        "rollback_receipt"
                    ],
                    "controller": b_receipt["control_set"]["controller"],
                    "client": b_receipt["control_set"]["client"],
                },
            }

            self._restore_active_state(fixture, c_state)
            if trust_variants is not None:
                trust_variants["select"]("c")
            fixture._write_deployment_receipt()
            if trust_variants is not None:
                trust_variants["patch_receipt"]("c")
            self.assertEqual(fixture.smoke_expected_envelope, c_envelope)
            generated_c_retained = fixture.retained_deployment_receipt
            generated_c_rollback = fixture.rollback_receipt
            c_receipt = json.loads(json.dumps(fixture.receipt))
            c_receipt.pop("content_sha256")
            c_rollback = document(
                {
                    "schema_version": 1,
                    "contract": "task-witness-rollback-receipt-v1",
                    "state": "active",
                    "canonical_root": str(fixture.install),
                    "effective_uid": os.geteuid(),
                    "activation_lock": activation_lock_identity(fixture.lock),
                    "deployment_receipt_absent": False,
                    "precondition": {
                        "root_identity": full_filesystem_identity(fixture.install),
                        "activation_lock_identity": full_filesystem_identity(
                            fixture.lock
                        ),
                        "active_receipt_sha256": b_sha256,
                    },
                    "prior_receipt": installed_file(b_retained),
                    "prior_activation_unit": b_unit,
                    "selector_preimage": c_selector_preimage,
                    "external_dependencies": c_external_dependencies,
                    "smoke": b_receipt["smoke"],
                }
            )
            c_rollback_raw = canonical_document(c_rollback)
            c_rollback_sha256 = sha256(c_rollback_raw)
            c_rollback_path = fixture.receipts_directory / (
                f"sha256-{c_rollback_sha256}.json"
            )
            c_rollback_path.write_bytes(c_rollback_raw)
            c_rollback_path.chmod(0o600)

            c_plan_sha256 = sha256(b"fixture routine B-to-C plan\n")
            c_maintenance_sha256 = sha256(
                b"fixture routine B-to-C maintenance transaction\n"
            )
            c_authorization = document(
                {
                    "schema_version": 1,
                    "contract": "task-witness-deployer-authorization-v1",
                    "purpose": "routine-compatible-forward",
                    "canonical_root": str(fixture.install),
                    "effective_uid": os.geteuid(),
                    "plan_sha256": c_plan_sha256,
                    "maintenance_transaction_sha256": c_maintenance_sha256,
                    "candidate_controller_sha256": c_receipt["control_set"][
                        "controller"
                    ]["sha256"],
                    "candidate_policy_sha256": c_receipt["control_set"]["policy"][
                        "sha256"
                    ],
                    "source_selection_sha256": c_receipt["source"][
                        "source_selection_sha256"
                    ],
                    "source_evidence_sha256": c_receipt["source"]["source_evidence"][
                        "source_evidence_sha256"
                    ],
                    "expected_active_receipt_sha256": b_sha256,
                }
            )
            c_authorization_raw = canonical_document(c_authorization)
            c_receipt["sequence"] = b_receipt["sequence"] + 1
            c_receipt["prior_receipt_sha256"] = b_sha256
            c_receipt["authorization"] = {
                "contract": c_authorization["contract"],
                "purpose": c_authorization["purpose"],
                "sha256": sha256(c_authorization_raw),
                "content_sha256": c_authorization["content_sha256"],
                "plan_sha256": c_plan_sha256,
                "maintenance_transaction_sha256": c_maintenance_sha256,
                "expected_active_receipt_sha256": b_sha256,
            }
            c_receipt["rollback"] = {
                "state": "active",
                "path": str(c_rollback_path),
                "sha256": c_rollback_sha256,
            }
            c_receipt = document(c_receipt)
            c_raw = canonical_document(c_receipt)
            c_sha256 = sha256(c_raw)
            c_retained = fixture.receipts_directory / f"sha256-{c_sha256}.json"
            c_retained.write_bytes(c_raw)
            c_retained.chmod(0o600)
            for provisional in (generated_c_retained, generated_c_rollback):
                if provisional not in {
                    a_retained,
                    a_rollback,
                    rollback_path,
                    b_retained,
                    c_rollback_path,
                    c_retained,
                }:
                    provisional.unlink(missing_ok=True)

            c_unit = self._activation_unit(
                fixture,
                c_receipt,
                c_raw,
                c_state["active_raw"],
            )
            prior_unit = b_unit
            candidate_unit = c_unit
            candidate_receipt = c_receipt
            candidate_raw = c_raw
            candidate_state = c_state
            candidate_envelope = c_envelope
            transaction_rollback_path = c_rollback_path
            transaction_rollback_sha256 = c_rollback_sha256
            transaction_selector_preimage = c_selector_preimage
            transaction_external_dependencies = c_external_dependencies
            transaction_stage_root = c_stage_root
            transaction_plan_sha256 = c_plan_sha256
            transaction_maintenance_sha256 = c_maintenance_sha256

        rollback_authority = {
            "receipt_path": str(transaction_rollback_path),
            "receipt_sha256": transaction_rollback_sha256,
            "target_state": "active",
        }
        preimage = {
            "manifest_path": str(transaction_rollback_path),
            "manifest_sha256": transaction_rollback_sha256,
            "artifacts": transaction_selector_preimage,
            "external_dependencies": transaction_external_dependencies,
        }
        stage = {
            "receipt_path": str(transaction_stage_root / "stage.json"),
            "receipt_sha256": sha256(b"fixture routine stage receipt\n"),
            "plan_sha256": transaction_plan_sha256,
            "authorization_sha256": candidate_receipt["authorization"]["sha256"],
            "maintenance_transaction_sha256": transaction_maintenance_sha256,
        }
        immutable_intent = {
            "contract": "task-witness-activation-intent-v1",
            "transaction_class": "routine-payload",
            "canonical_root": str(fixture.install),
            "effective_uid": os.geteuid(),
            "activation_lock": activation_lock_identity(fixture.lock),
            "outer_maintenance_transaction_sha256": transaction_maintenance_sha256,
            "stage": stage,
            "prior": prior_unit,
            "candidate": candidate_unit,
            "rollback_authority": rollback_authority,
            "preimage": preimage,
        }
        selected_unit = candidate_unit if phase == "candidate-smoke" else prior_unit
        selected_receipt = (
            candidate_receipt if phase == "candidate-smoke" else b_receipt
        )
        selected_raw = candidate_raw if phase == "candidate-smoke" else b_raw
        selected_state = candidate_state if phase == "candidate-smoke" else b_state
        selected_envelope = (
            candidate_envelope if phase == "candidate-smoke" else b_envelope
        )
        if not successor and phase == "rollback-smoke":
            selected_receipt = a_receipt
            selected_raw = a_raw
            selected_state = a_state
            selected_envelope = a_envelope
        self._restore_active_state(fixture, selected_state)
        fixture.deployment.write_bytes(selected_raw)
        fixture.deployment.chmod(0o600)
        fixture.receipt = selected_receipt
        fixture.deployment_raw = selected_raw

        transaction = document(
            {
                "schema_version": 1,
                "contract": "task-witness-activation-transaction-v1",
                "transaction_id": sha256(canonical_value(immutable_intent)),
                "sequence": 1,
                "previous_journal_sha256": None,
                "transaction_class": "routine-payload",
                "phase": phase,
                "canonical_root": str(fixture.install),
                "effective_uid": os.geteuid(),
                "activation_lock": activation_lock_identity(fixture.lock),
                "outer_maintenance_transaction_sha256": transaction_maintenance_sha256,
                "stage": stage,
                "prior": prior_unit,
                "candidate": candidate_unit,
                "rollback_authority": rollback_authority,
                "preimage": preimage,
                "pending_step": None,
                "smoke_handoff": {
                    "target_deployment_receipt_sha256": selected_unit[
                        "deployment_receipt"
                    ]["sha256"],
                    "smoke_bundle_sha256": selected_unit["smoke"]["bundle"]["sha256"],
                    "smoke_trust_context_sha256": selected_unit["smoke"][
                        "trust_context"
                    ]["sha256"],
                },
                "candidate_smoke_acceptance": None,
                "rollback_smoke_acceptance": None,
                "terminal_result": None,
            }
        )
        fixture.transaction = fixture.install / "transaction.json"
        fixture.transaction.write_bytes(canonical_document(transaction))
        fixture.transaction.chmod(0o600)
        return {
            "a_receipt": a_receipt,
            "a_raw": a_raw,
            "a_retained": a_retained,
            "a_rollback": a_rollback,
            "a_unit": a_unit,
            "b_receipt": b_receipt,
            "b_raw": b_raw,
            "b_retained": b_retained,
            "b_unit": b_unit,
            "b_state": b_state,
            "b_envelope": b_envelope,
            "rollback": rollback_value,
            "rollback_path": rollback_path,
            "c_receipt": c_receipt,
            "c_raw": c_raw,
            "c_retained": c_retained,
            "c_unit": c_unit,
            "c_state": c_state,
            "c_envelope": c_envelope,
            "c_rollback": c_rollback,
            "c_rollback_path": c_rollback_path,
            "transaction": transaction,
            "selected_envelope": selected_envelope,
            "launcher_marker": launcher_marker,
        }

    def _rebind_routine_candidate_after_control_change(
        self,
        fixture: ValidInvocationFixture,
        routine: dict[str, object],
    ) -> None:
        fixture.controller.chmod(0o700)
        fixture.controller.write_bytes(
            fixture.controller.read_bytes() + b"\n# shape-valid routine control drift\n"
        )
        fixture.controller.chmod(0o500)

        old_b_retained = routine["b_retained"]
        b_receipt = json.loads(json.dumps(routine["b_receipt"]))
        b_receipt.pop("content_sha256")
        b_receipt["control_set"]["controller"] = installed_file(fixture.controller)
        b_receipt = document(b_receipt)
        b_raw = canonical_document(b_receipt)
        b_retained = fixture.receipts_directory / (f"sha256-{sha256(b_raw)}.json")
        old_b_retained.unlink()
        b_retained.write_bytes(b_raw)
        b_retained.chmod(0o600)
        fixture.deployment.write_bytes(b_raw)
        fixture.deployment.chmod(0o600)
        fixture.receipt = b_receipt
        fixture.deployment_raw = b_raw

        transaction = json.loads(json.dumps(routine["transaction"]))
        transaction.pop("content_sha256")
        transaction["candidate"]["deployment_receipt"] = self._binding_for_raw(
            fixture.deployment, b_raw, 0o600
        )
        transaction["candidate"]["control_set"] = b_receipt["control_set"]
        transaction["smoke_handoff"]["target_deployment_receipt_sha256"] = sha256(b_raw)
        self._rebind_transaction_id(transaction)
        fixture.transaction.write_bytes(canonical_document(document(transaction)))
        fixture.transaction.chmod(0o600)
        routine["b_receipt"] = b_receipt
        routine["b_raw"] = b_raw
        routine["b_retained"] = b_retained
        routine["transaction"] = transaction

    def _rewrite_routine_b_receipt(
        self,
        fixture: ValidInvocationFixture,
        routine: dict[str, object],
        mutate,
    ) -> None:
        old_b_retained = routine["b_retained"]
        b_receipt = json.loads(json.dumps(routine["b_receipt"]))
        b_receipt.pop("content_sha256")
        mutate(b_receipt)
        b_receipt = document(b_receipt)
        b_raw = canonical_document(b_receipt)
        b_retained = fixture.receipts_directory / (f"sha256-{sha256(b_raw)}.json")
        old_b_retained.unlink()
        b_retained.write_bytes(b_raw)
        b_retained.chmod(0o600)
        fixture.deployment.write_bytes(b_raw)
        fixture.deployment.chmod(0o600)
        fixture.receipt = b_receipt
        fixture.deployment_raw = b_raw

        transaction = json.loads(fixture.transaction.read_bytes())
        transaction.pop("content_sha256")
        transaction["candidate"]["deployment_receipt"] = self._binding_for_raw(
            fixture.deployment, b_raw, 0o600
        )
        transaction["candidate"]["control_set"] = b_receipt["control_set"]
        transaction["candidate"]["smoke"] = b_receipt["smoke"]
        if transaction["phase"] == "candidate-smoke":
            transaction["smoke_handoff"] = {
                "target_deployment_receipt_sha256": sha256(b_raw),
                "smoke_bundle_sha256": b_receipt["smoke"]["bundle"]["sha256"],
                "smoke_trust_context_sha256": b_receipt["smoke"]["trust_context"][
                    "sha256"
                ],
            }
        self._rebind_transaction_id(transaction)
        fixture.transaction.write_bytes(canonical_document(document(transaction)))
        fixture.transaction.chmod(0o600)
        routine["b_receipt"] = b_receipt
        routine["b_raw"] = b_raw
        routine["b_retained"] = b_retained
        routine["transaction"] = transaction

    def _rewrite_recursive_routine_candidate_receipt(
        self,
        fixture: ValidInvocationFixture,
        routine: dict[str, object],
        mutate,
    ) -> None:
        old_c_retained = routine["c_retained"]
        c_receipt = json.loads(json.dumps(routine["c_receipt"]))
        c_receipt.pop("content_sha256")
        mutate(c_receipt)
        c_receipt = document(c_receipt)
        c_raw = canonical_document(c_receipt)
        c_retained = fixture.receipts_directory / (f"sha256-{sha256(c_raw)}.json")
        old_c_retained.unlink()
        c_retained.write_bytes(c_raw)
        c_retained.chmod(0o600)

        transaction = json.loads(fixture.transaction.read_bytes())
        transaction.pop("content_sha256")
        transaction["candidate"]["deployment_receipt"] = self._binding_for_raw(
            fixture.deployment,
            c_raw,
            0o600,
        )
        if transaction["phase"] == "candidate-smoke":
            fixture.deployment.write_bytes(c_raw)
            fixture.deployment.chmod(0o600)
            fixture.receipt = c_receipt
            fixture.deployment_raw = c_raw
            transaction["smoke_handoff"]["target_deployment_receipt_sha256"] = sha256(
                c_raw
            )
        self._rebind_transaction_id(transaction)
        fixture.transaction.write_bytes(canonical_document(document(transaction)))
        fixture.transaction.chmod(0o600)
        routine["c_receipt"] = c_receipt
        routine["c_raw"] = c_raw
        routine["c_retained"] = c_retained
        routine["transaction"] = transaction

    def _rewrite_routine_ancestor_and_rebind_successors(
        self,
        fixture: ValidInvocationFixture,
        routine: dict[str, object],
        mutate,
    ) -> None:
        old_a_retained = routine["a_retained"]
        a_receipt = json.loads(json.dumps(routine["a_receipt"]))
        a_receipt.pop("content_sha256")
        mutate(a_receipt)
        a_receipt = document(a_receipt)
        a_raw = canonical_document(a_receipt)
        a_sha256 = sha256(a_raw)
        a_retained = fixture.receipts_directory / f"sha256-{a_sha256}.json"
        old_a_retained.unlink()
        a_retained.write_bytes(a_raw)
        a_retained.chmod(0o600)
        a_unit = json.loads(json.dumps(routine["a_unit"]))
        a_unit["deployment_receipt"] = self._binding_for_raw(
            fixture.deployment,
            a_raw,
            0o600,
        )

        old_rollback_path = routine["rollback_path"]
        rollback = json.loads(json.dumps(routine["rollback"]))
        rollback.pop("content_sha256")
        rollback["precondition"]["active_receipt_sha256"] = a_sha256
        rollback["prior_receipt"] = installed_file(a_retained)
        rollback["prior_activation_unit"] = a_unit
        staged_deployment = Path(rollback["selector_preimage"][1]["staged"]["path"])
        staged_deployment.write_bytes(a_raw)
        staged_deployment.chmod(0o600)
        rollback["selector_preimage"][1]["staged"] = installed_file(staged_deployment)
        rollback["selector_preimage"][1]["installed"] = a_unit["deployment_receipt"]
        rollback = document(rollback)
        rollback_raw = canonical_document(rollback)
        rollback_sha256 = sha256(rollback_raw)
        rollback_path = fixture.receipts_directory / (f"sha256-{rollback_sha256}.json")
        old_rollback_path.unlink()
        rollback_path.write_bytes(rollback_raw)
        rollback_path.chmod(0o600)

        old_b_retained = routine["b_retained"]
        b_receipt = json.loads(json.dumps(routine["b_receipt"]))
        b_receipt.pop("content_sha256")
        b_authorization = document(
            {
                "schema_version": 1,
                "contract": "task-witness-deployer-authorization-v1",
                "purpose": "routine-compatible-forward",
                "canonical_root": str(fixture.install),
                "effective_uid": os.geteuid(),
                "plan_sha256": b_receipt["authorization"]["plan_sha256"],
                "maintenance_transaction_sha256": b_receipt["authorization"][
                    "maintenance_transaction_sha256"
                ],
                "candidate_controller_sha256": b_receipt["control_set"]["controller"][
                    "sha256"
                ],
                "candidate_policy_sha256": b_receipt["control_set"]["policy"]["sha256"],
                "source_selection_sha256": b_receipt["source"][
                    "source_selection_sha256"
                ],
                "source_evidence_sha256": b_receipt["source"]["source_evidence"][
                    "source_evidence_sha256"
                ],
                "expected_active_receipt_sha256": a_sha256,
            }
        )
        b_authorization_raw = canonical_document(b_authorization)
        b_receipt["prior_receipt_sha256"] = a_sha256
        b_receipt["authorization"] = {
            "contract": b_authorization["contract"],
            "purpose": b_authorization["purpose"],
            "sha256": sha256(b_authorization_raw),
            "content_sha256": b_authorization["content_sha256"],
            "plan_sha256": b_authorization["plan_sha256"],
            "maintenance_transaction_sha256": b_authorization[
                "maintenance_transaction_sha256"
            ],
            "expected_active_receipt_sha256": a_sha256,
        }
        b_receipt["rollback"] = {
            "state": "active",
            "path": str(rollback_path),
            "sha256": rollback_sha256,
        }
        b_receipt = document(b_receipt)
        b_raw = canonical_document(b_receipt)
        b_retained = fixture.receipts_directory / f"sha256-{sha256(b_raw)}.json"
        old_b_retained.unlink()
        b_retained.write_bytes(b_raw)
        b_retained.chmod(0o600)
        b_unit = json.loads(json.dumps(routine["b_unit"]))
        b_unit["deployment_receipt"] = self._binding_for_raw(
            fixture.deployment,
            b_raw,
            0o600,
        )

        routine["a_receipt"] = a_receipt
        routine["a_raw"] = a_raw
        routine["a_retained"] = a_retained
        routine["a_unit"] = a_unit
        routine["rollback"] = rollback
        routine["rollback_path"] = rollback_path
        routine["b_receipt"] = b_receipt
        routine["b_raw"] = b_raw
        routine["b_retained"] = b_retained
        routine["b_unit"] = b_unit
        if routine["c_receipt"] is not None:
            self._rewrite_recursive_routine_prior_and_rebind_candidate(
                fixture,
                routine,
                lambda _receipt: None,
            )
            return

        transaction = json.loads(fixture.transaction.read_bytes())
        transaction.pop("content_sha256")
        transaction["prior"] = a_unit
        transaction["candidate"]["deployment_receipt"] = self._binding_for_raw(
            fixture.deployment,
            b_raw,
            0o600,
        )
        transaction["stage"]["authorization_sha256"] = b_receipt["authorization"][
            "sha256"
        ]
        transaction["rollback_authority"] = {
            "receipt_path": str(rollback_path),
            "receipt_sha256": rollback_sha256,
            "target_state": "active",
        }
        transaction["preimage"] = {
            "manifest_path": str(rollback_path),
            "manifest_sha256": rollback_sha256,
            "artifacts": rollback["selector_preimage"],
            "external_dependencies": rollback["external_dependencies"],
        }
        if transaction["phase"] == "candidate-smoke":
            selected_receipt = b_receipt
            selected_raw = b_raw
        else:
            selected_receipt = a_receipt
            selected_raw = a_raw
        fixture.deployment.write_bytes(selected_raw)
        fixture.deployment.chmod(0o600)
        fixture.receipt = selected_receipt
        fixture.deployment_raw = selected_raw
        transaction["smoke_handoff"]["target_deployment_receipt_sha256"] = sha256(
            selected_raw
        )
        self._rebind_transaction_id(transaction)
        fixture.transaction.write_bytes(canonical_document(document(transaction)))
        fixture.transaction.chmod(0o600)
        routine["transaction"] = transaction

    def _rewrite_recursive_routine_prior_and_rebind_candidate(
        self,
        fixture: ValidInvocationFixture,
        routine: dict[str, object],
        mutate,
    ) -> None:
        old_b_retained = routine["b_retained"]
        b_receipt = json.loads(json.dumps(routine["b_receipt"]))
        b_receipt.pop("content_sha256")
        mutate(b_receipt)
        b_receipt = document(b_receipt)
        b_raw = canonical_document(b_receipt)
        b_sha256 = sha256(b_raw)
        b_retained = fixture.receipts_directory / f"sha256-{b_sha256}.json"
        old_b_retained.unlink()
        b_retained.write_bytes(b_raw)
        b_retained.chmod(0o600)
        b_unit = json.loads(json.dumps(routine["b_unit"]))
        b_unit["deployment_receipt"] = self._binding_for_raw(
            fixture.deployment,
            b_raw,
            0o600,
        )

        old_c_rollback_path = routine["c_rollback_path"]
        c_rollback = json.loads(json.dumps(routine["c_rollback"]))
        c_rollback.pop("content_sha256")
        c_rollback["precondition"]["active_receipt_sha256"] = b_sha256
        c_rollback["prior_receipt"] = installed_file(b_retained)
        c_rollback["prior_activation_unit"] = b_unit
        c_staged_deployment = Path(c_rollback["selector_preimage"][1]["staged"]["path"])
        c_staged_deployment.write_bytes(b_raw)
        c_staged_deployment.chmod(0o600)
        c_rollback["selector_preimage"][1]["staged"] = installed_file(
            c_staged_deployment
        )
        c_rollback["selector_preimage"][1]["installed"] = b_unit["deployment_receipt"]
        c_rollback = document(c_rollback)
        c_rollback_raw = canonical_document(c_rollback)
        c_rollback_sha256 = sha256(c_rollback_raw)
        c_rollback_path = fixture.receipts_directory / (
            f"sha256-{c_rollback_sha256}.json"
        )
        old_c_rollback_path.unlink()
        c_rollback_path.write_bytes(c_rollback_raw)
        c_rollback_path.chmod(0o600)

        old_c_retained = routine["c_retained"]
        c_receipt = json.loads(json.dumps(routine["c_receipt"]))
        c_receipt.pop("content_sha256")
        c_authorization = document(
            {
                "schema_version": 1,
                "contract": "task-witness-deployer-authorization-v1",
                "purpose": "routine-compatible-forward",
                "canonical_root": str(fixture.install),
                "effective_uid": os.geteuid(),
                "plan_sha256": c_receipt["authorization"]["plan_sha256"],
                "maintenance_transaction_sha256": c_receipt["authorization"][
                    "maintenance_transaction_sha256"
                ],
                "candidate_controller_sha256": c_receipt["control_set"]["controller"][
                    "sha256"
                ],
                "candidate_policy_sha256": c_receipt["control_set"]["policy"]["sha256"],
                "source_selection_sha256": c_receipt["source"][
                    "source_selection_sha256"
                ],
                "source_evidence_sha256": c_receipt["source"]["source_evidence"][
                    "source_evidence_sha256"
                ],
                "expected_active_receipt_sha256": b_sha256,
            }
        )
        c_authorization_raw = canonical_document(c_authorization)
        c_receipt["prior_receipt_sha256"] = b_sha256
        c_receipt["authorization"] = {
            "contract": c_authorization["contract"],
            "purpose": c_authorization["purpose"],
            "sha256": sha256(c_authorization_raw),
            "content_sha256": c_authorization["content_sha256"],
            "plan_sha256": c_authorization["plan_sha256"],
            "maintenance_transaction_sha256": c_authorization[
                "maintenance_transaction_sha256"
            ],
            "expected_active_receipt_sha256": b_sha256,
        }
        c_receipt["rollback"] = {
            "state": "active",
            "path": str(c_rollback_path),
            "sha256": c_rollback_sha256,
        }
        c_receipt = document(c_receipt)
        c_raw = canonical_document(c_receipt)
        c_retained = fixture.receipts_directory / f"sha256-{sha256(c_raw)}.json"
        old_c_retained.unlink()
        c_retained.write_bytes(c_raw)
        c_retained.chmod(0o600)

        transaction = json.loads(fixture.transaction.read_bytes())
        transaction.pop("content_sha256")
        transaction["prior"] = b_unit
        transaction["candidate"]["deployment_receipt"] = self._binding_for_raw(
            fixture.deployment,
            c_raw,
            0o600,
        )
        transaction["stage"]["authorization_sha256"] = c_receipt["authorization"][
            "sha256"
        ]
        transaction["rollback_authority"] = {
            "receipt_path": str(c_rollback_path),
            "receipt_sha256": c_rollback_sha256,
            "target_state": "active",
        }
        transaction["preimage"] = {
            "manifest_path": str(c_rollback_path),
            "manifest_sha256": c_rollback_sha256,
            "artifacts": c_rollback["selector_preimage"],
            "external_dependencies": c_rollback["external_dependencies"],
        }
        if transaction["phase"] == "candidate-smoke":
            selected_receipt = c_receipt
            selected_raw = c_raw
        else:
            selected_receipt = b_receipt
            selected_raw = b_raw
        fixture.deployment.write_bytes(selected_raw)
        fixture.deployment.chmod(0o600)
        fixture.receipt = selected_receipt
        fixture.deployment_raw = selected_raw
        transaction["smoke_handoff"]["target_deployment_receipt_sha256"] = sha256(
            selected_raw
        )
        self._rebind_transaction_id(transaction)
        fixture.transaction.write_bytes(canonical_document(document(transaction)))
        fixture.transaction.chmod(0o600)
        routine["b_receipt"] = b_receipt
        routine["b_raw"] = b_raw
        routine["b_retained"] = b_retained
        routine["b_unit"] = b_unit
        routine["c_receipt"] = c_receipt
        routine["c_raw"] = c_raw
        routine["c_retained"] = c_retained
        routine["c_rollback"] = c_rollback
        routine["c_rollback_path"] = c_rollback_path
        routine["transaction"] = transaction

    def _rewrite_routine_rollback_chain(
        self,
        fixture: ValidInvocationFixture,
        routine: dict[str, object],
        mutate,
    ) -> None:
        old_rollback_path = routine["rollback_path"]
        rollback = json.loads(json.dumps(routine["rollback"]))
        rollback.pop("content_sha256")
        mutate(rollback)
        rollback = document(rollback)
        rollback_raw = canonical_document(rollback)
        rollback_sha256 = sha256(rollback_raw)
        rollback_path = fixture.receipts_directory / (f"sha256-{rollback_sha256}.json")
        old_rollback_path.unlink()
        rollback_path.write_bytes(rollback_raw)
        rollback_path.chmod(0o600)

        old_b_retained = routine["b_retained"]
        b_receipt = json.loads(json.dumps(routine["b_receipt"]))
        b_receipt.pop("content_sha256")
        b_receipt["rollback"] = {
            "state": "active",
            "path": str(rollback_path),
            "sha256": rollback_sha256,
        }
        b_receipt = document(b_receipt)
        b_raw = canonical_document(b_receipt)
        b_retained = fixture.receipts_directory / (f"sha256-{sha256(b_raw)}.json")
        old_b_retained.unlink()
        b_retained.write_bytes(b_raw)
        b_retained.chmod(0o600)
        fixture.deployment.write_bytes(b_raw)
        fixture.deployment.chmod(0o600)
        fixture.receipt = b_receipt
        fixture.deployment_raw = b_raw

        transaction = json.loads(fixture.transaction.read_bytes())
        transaction.pop("content_sha256")
        transaction["candidate"]["deployment_receipt"] = self._binding_for_raw(
            fixture.deployment, b_raw, 0o600
        )
        transaction["rollback_authority"] = {
            "receipt_path": str(rollback_path),
            "receipt_sha256": rollback_sha256,
            "target_state": "active",
        }
        transaction["preimage"] = {
            "manifest_path": str(rollback_path),
            "manifest_sha256": rollback_sha256,
            "artifacts": rollback["selector_preimage"],
            "external_dependencies": rollback["external_dependencies"],
        }
        if transaction["phase"] == "candidate-smoke":
            transaction["smoke_handoff"]["target_deployment_receipt_sha256"] = sha256(
                b_raw
            )
        self._rebind_transaction_id(transaction)
        fixture.transaction.write_bytes(canonical_document(document(transaction)))
        fixture.transaction.chmod(0o600)
        routine["rollback"] = rollback
        routine["rollback_path"] = rollback_path
        routine["b_receipt"] = b_receipt
        routine["b_raw"] = b_raw
        routine["b_retained"] = b_retained
        routine["transaction"] = transaction

    def _rebind_transaction_id(self, transaction: dict[str, object]) -> None:
        intent_keys = [
            "transaction_class",
            "canonical_root",
            "effective_uid",
            "activation_lock",
            "outer_maintenance_transaction_sha256",
            "stage",
            "prior",
            "candidate",
            "rollback_authority",
            "preimage",
        ]
        if "bridge_transition" in transaction:
            intent_keys.append("bridge_transition")
        intent = {
            "contract": "task-witness-activation-intent-v1",
            **{key: transaction[key] for key in intent_keys},
        }
        transaction["transaction_id"] = sha256(canonical_value(intent))

    def _add_exact_bridge_transition(self, fixture: ValidInvocationFixture) -> None:
        projection = {
            "execution_class": "isolated-rehearsal",
            "maintenance_transaction_sha256": "1" * 64,
            "deployment_authorization_sha256": "2" * 64,
            "transition_authorization_sha256": "3" * 64,
            "expected_active_receipt_core_sha256": "4" * 64,
            "bridge_identity_sha256": "5" * 64,
            "release_manifest_sha256": "6" * 64,
            "endpoint_projection_sha256": "7" * 64,
        }

        def mutate(transaction: dict[str, object]) -> None:
            transaction["bridge_transition"] = projection

        self._rewrite_transaction(fixture, mutate, rebind_intent=True)

    def _rewrite_transaction(
        self,
        fixture: ValidInvocationFixture,
        mutate,
        *,
        rebind_intent: bool = False,
    ) -> None:
        transaction = json.loads(fixture.transaction.read_bytes())
        transaction.pop("content_sha256")
        mutate(transaction)
        if rebind_intent:
            self._rebind_transaction_id(transaction)
        fixture.transaction.write_bytes(canonical_document(document(transaction)))

    def _rewrite_receipt_and_refresh_transaction(
        self,
        fixture: ValidInvocationFixture,
        mutate,
    ) -> None:
        old_retained = fixture.retained_deployment_receipt
        receipt = json.loads(fixture.deployment.read_bytes())
        receipt.pop("content_sha256")
        mutate(receipt)
        fixture.receipt = document(receipt)
        fixture.deployment_raw = canonical_document(fixture.receipt)
        fixture.deployment.write_bytes(fixture.deployment_raw)
        fixture.deployment.chmod(0o600)
        fixture.retained_deployment_receipt = fixture.receipts_directory / (
            f"sha256-{sha256(fixture.deployment_raw)}.json"
        )
        old_retained.unlink()
        fixture.retained_deployment_receipt.write_bytes(fixture.deployment_raw)
        fixture.retained_deployment_receipt.chmod(0o600)

        transaction = json.loads(fixture.transaction.read_bytes())
        transaction.pop("content_sha256")
        unit_name = (
            "candidate" if transaction["phase"] == "candidate-smoke" else "prior"
        )
        transaction[unit_name] = {
            "state": "active",
            "deployment_receipt": installed_file(fixture.deployment),
            "active_record": installed_file(fixture.active_path),
            "control_set": fixture.receipt["control_set"],
            "smoke": fixture.receipt["smoke"],
        }
        transaction["smoke_handoff"] = {
            "target_deployment_receipt_sha256": sha256(fixture.deployment_raw),
            "smoke_bundle_sha256": fixture.receipt["smoke"]["bundle"]["sha256"],
            "smoke_trust_context_sha256": fixture.receipt["smoke"]["trust_context"][
                "sha256"
            ],
        }
        fixture.transaction.write_bytes(canonical_document(document(transaction)))

    def _prepare_activation_smoke(
        self,
        fixture: ValidInvocationFixture,
        *,
        phase: str = "candidate-smoke",
        launcher_source: str | None = None,
    ) -> None:
        if launcher_source is None:
            launcher_source = (
                "#!/usr/bin/env python3\n"
                "import sys\n"
                f"sys.stdout.buffer.write({canonical_document(fixture.smoke_expected_envelope)!r})\n"
            )
        fixture.replace_launcher_behavior(launcher_source)
        smoke_binding = fixture.receipt["smoke"]

        target_receipt_sha256 = sha256(fixture.deployment_raw)
        active_unit = {
            "state": "active",
            "deployment_receipt": installed_file(fixture.deployment),
            "active_record": installed_file(fixture.active_path),
            "control_set": fixture.receipt["control_set"],
            "smoke": smoke_binding,
        }
        absent_unit = {
            "state": "absent",
            "deployment_receipt": None,
            "active_record": None,
            "control_set": None,
            "smoke": None,
        }
        outer_transaction_sha256 = fixture.receipt["authorization"][
            "maintenance_transaction_sha256"
        ]
        stage = {
            "receipt_path": str(self.root / "stage" / "stage.json"),
            "receipt_sha256": sha256(b"fixture verified stage receipt\n"),
            "plan_sha256": fixture.receipt["authorization"]["plan_sha256"],
            "authorization_sha256": fixture.receipt["authorization"]["sha256"],
            "maintenance_transaction_sha256": outer_transaction_sha256,
        }
        rollback_authority = {
            "receipt_path": str(fixture.rollback_receipt),
            "receipt_sha256": fixture.receipt["rollback"]["sha256"],
            "target_state": ("active" if phase == "rollback-smoke" else "absent"),
        }
        preimage = {
            "manifest_path": str(fixture.rollback_receipt),
            "manifest_sha256": fixture.receipt["rollback"]["sha256"],
            "artifacts": [],
            "external_dependencies": [],
        }
        immutable_intent = {
            "contract": "task-witness-activation-intent-v1",
            "transaction_class": "control-set-maintenance",
            "canonical_root": str(fixture.install),
            "effective_uid": fixture.lock.stat().st_uid,
            "activation_lock": activation_lock_identity(fixture.lock),
            "outer_maintenance_transaction_sha256": outer_transaction_sha256,
            "stage": stage,
            "prior": (active_unit if phase == "rollback-smoke" else absent_unit),
            "candidate": active_unit,
            "rollback_authority": rollback_authority,
            "preimage": preimage,
        }
        transaction = document(
            {
                "schema_version": 1,
                "contract": "task-witness-activation-transaction-v1",
                "transaction_id": sha256(canonical_value(immutable_intent)),
                "sequence": 1,
                "previous_journal_sha256": None,
                "transaction_class": immutable_intent["transaction_class"],
                "phase": phase,
                "canonical_root": str(fixture.install),
                "effective_uid": fixture.lock.stat().st_uid,
                "activation_lock": activation_lock_identity(fixture.lock),
                "outer_maintenance_transaction_sha256": (outer_transaction_sha256),
                "stage": stage,
                "prior": immutable_intent["prior"],
                "candidate": immutable_intent["candidate"],
                "rollback_authority": rollback_authority,
                "preimage": preimage,
                "pending_step": None,
                "smoke_handoff": {
                    "target_deployment_receipt_sha256": target_receipt_sha256,
                    "smoke_bundle_sha256": fixture.smoke_bundle_sha256,
                    "smoke_trust_context_sha256": fixture.trust_sha256,
                },
                "candidate_smoke_acceptance": None,
                "rollback_smoke_acceptance": None,
                "terminal_result": None,
            }
        )
        fixture.transaction = fixture.install / "transaction.json"
        fixture.transaction.write_bytes(canonical_document(transaction))
        fixture.transaction.chmod(0o600)

    def _invoke_activation_smoke(
        self,
        fixture: ValidInvocationFixture,
        *,
        audit_marker: Path | None = None,
        driver_options: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[bytes]:
        driver_arguments = (
            [] if audit_marker is None else ["--audit", str(audit_marker)]
        )
        return subprocess.run(
            [
                sys.executable,
                "-B",
                "-I",
                "-S",
                "-X",
                "disable-remote-debug",
                str(ACTIVATION_SMOKE_DRIVER),
                str(fixture.client),
                str(fixture.install),
                *driver_options,
                *driver_arguments,
                "activation-smoke",
            ],
            text=False,
            capture_output=True,
            check=False,
            env=CLIENT_ENVIRONMENT,
            timeout=10,
        )

    def _invoke_public_client(
        self,
        fixture: ValidInvocationFixture,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [
                sys.executable,
                "-B",
                "-I",
                "-S",
                "-X",
                "disable-remote-debug",
                str(ACTIVATION_SMOKE_DRIVER),
                str(fixture.client),
                str(fixture.install),
                "--no-lock",
                "validate",
                "--bundle",
                str(fixture.bundle),
            ],
            text=False,
            capture_output=True,
            check=False,
            env=CLIENT_ENVIRONMENT,
            timeout=10,
        )

    def test_exact_inherited_lock_and_receipt_owned_smoke_inputs_are_accepted(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._prepare_activation_smoke(fixture)

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            canonical_document(fixture.smoke_expected_envelope),
        )
        self.assertEqual(result.stderr, b"")

    def test_release_profiles_reject_wrong_bridge_smoke_phase(self) -> None:
        cases = (
            ("b1-transition", "candidate-smoke"),
            ("tw4-current", "rollback-smoke"),
        )
        for profile, phase in cases:
            with self.subTest(profile=profile, phase=phase):
                fixture = ValidInvocationFixture(self.root / profile)
                if profile == "b1-transition":
                    raw = fixture.client.read_bytes()
                    current = b'CLIENT_RELEASE_PROFILE = "tw4-current"\n'
                    replacement = b'CLIENT_RELEASE_PROFILE = "b1-transition"\n'
                    self.assertEqual(raw.count(current), 1)
                    fixture.client.chmod(0o700)
                    fixture.client.write_bytes(raw.replace(current, replacement, 1))
                    fixture.client.chmod(0o500)
                    self._refresh_fixture_client_generation(fixture)
                self._prepare_activation_smoke(fixture, phase=phase)
                self._add_exact_bridge_transition(fixture)

                result = self._invoke_activation_smoke(fixture)

                self.assertEqual(result.returncode, 70)
                self.assertIn(b"client installation validation failed", result.stderr)

    def test_routine_candidate_smoke_uses_b_as_authority_and_live_target(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        routine = self._prepare_routine_activation_smoke(
            fixture,
            phase="candidate-smoke",
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            canonical_document(routine["selected_envelope"]),
        )
        self.assertEqual(result.stderr, b"")
        self.assertTrue(routine["launcher_marker"].is_file())

    def test_recursive_routine_smoke_uses_c_authority_for_c_or_b_live_target(
        self,
    ) -> None:
        for phase in ("candidate-smoke", "rollback-smoke"):
            with self.subTest(phase=phase):
                fixture = ValidInvocationFixture(self.root / phase)
                routine = self._prepare_routine_activation_smoke(
                    fixture,
                    phase=phase,
                    successor=True,
                )
                a_sha256 = sha256(routine["a_raw"])
                b_sha256 = sha256(routine["b_raw"])
                c_sha256 = sha256(routine["c_raw"])

                self.assertEqual(
                    {
                        path.name
                        for path in fixture.receipts_directory.iterdir()
                        if path.is_file()
                    },
                    {
                        routine["a_retained"].name,
                        routine["a_rollback"].name,
                        routine["b_retained"].name,
                        routine["rollback_path"].name,
                        routine["c_retained"].name,
                        routine["c_rollback_path"].name,
                    },
                )
                self.assertEqual(routine["a_receipt"]["sequence"], 1)
                self.assertIsNone(routine["a_receipt"]["prior_receipt_sha256"])
                self.assertEqual(routine["b_receipt"]["sequence"], 2)
                self.assertEqual(
                    routine["b_receipt"]["prior_receipt_sha256"],
                    a_sha256,
                )
                self.assertEqual(routine["c_receipt"]["sequence"], 3)
                self.assertEqual(
                    routine["c_receipt"]["prior_receipt_sha256"],
                    b_sha256,
                )
                self.assertEqual(
                    routine["rollback"]["precondition"]["active_receipt_sha256"],
                    a_sha256,
                )
                self.assertEqual(
                    routine["c_rollback"]["precondition"]["active_receipt_sha256"],
                    b_sha256,
                )
                selected_sha256 = c_sha256 if phase == "candidate-smoke" else b_sha256
                self.assertEqual(
                    routine["transaction"]["smoke_handoff"][
                        "target_deployment_receipt_sha256"
                    ],
                    selected_sha256,
                )

                result = self._invoke_activation_smoke(fixture)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    result.stdout,
                    canonical_document(routine["selected_envelope"]),
                )
                self.assertEqual(result.stderr, b"")
                self.assertTrue(routine["launcher_marker"].is_file())

    def test_recursive_routine_reactivates_prior_historical_trust(
        self,
    ) -> None:
        for phase in ("candidate-smoke", "rollback-smoke"):
            with self.subTest(phase=phase):
                fixture = ValidInvocationFixture(self.root / phase)
                routine = self._prepare_routine_activation_smoke(
                    fixture,
                    phase=phase,
                    trust_change=True,
                    successor=True,
                )
                a_trust = routine["a_receipt"]["trust_context"]
                b_trust = routine["b_receipt"]["trust_context"]
                c_trust = routine["c_receipt"]["trust_context"]

                self.assertEqual(c_trust, a_trust)
                self.assertNotEqual(b_trust, a_trust)
                self.assertEqual(routine["a_receipt"]["historical_trust_contexts"], [])
                self.assertEqual(
                    routine["b_receipt"]["historical_trust_contexts"],
                    [
                        {
                            "path": a_trust["path"],
                            "sha256": a_trust["sha256"],
                            "state": "historical-usable",
                        }
                    ],
                )
                self.assertEqual(
                    routine["c_receipt"]["historical_trust_contexts"],
                    [
                        {
                            "path": b_trust["path"],
                            "sha256": b_trust["sha256"],
                            "state": "historical-usable",
                        }
                    ],
                )

                result = self._invoke_activation_smoke(fixture)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    result.stdout,
                    canonical_document(routine["selected_envelope"]),
                )
                self.assertEqual(result.stderr, b"")
                self.assertTrue(routine["launcher_marker"].is_file())

    def test_recursive_routine_rollback_rejects_candidate_provider_substitution(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        routine = self._prepare_routine_activation_smoke(
            fixture,
            phase="rollback-smoke",
            trust_change=True,
            successor=True,
        )
        source_before = json.loads(json.dumps(routine["c_receipt"]["source"]))
        b_external = next(
            provider
            for provider in routine["b_receipt"]["providers"]
            if not provider["intrinsic"]
        )
        b_declaration = (
            b_external["declaration_sha256"],
            b_external["declaration_content_sha256"],
        )

        def substitute_external_declaration(receipt: dict[str, object]) -> None:
            external = next(
                provider
                for provider in receipt["providers"]
                if not provider["intrinsic"]
            )
            external["declaration_sha256"] = b_declaration[0]
            external["declaration_content_sha256"] = b_declaration[1]

        self._rewrite_recursive_routine_candidate_receipt(
            fixture,
            routine,
            substitute_external_declaration,
        )
        c_external = next(
            provider
            for provider in routine["c_receipt"]["providers"]
            if not provider["intrinsic"]
        )
        self.assertEqual(routine["c_receipt"]["source"], source_before)
        self.assertEqual(
            (
                c_external["declaration_sha256"],
                c_external["declaration_content_sha256"],
            ),
            b_declaration,
        )
        self.assertEqual(fixture.deployment.read_bytes(), routine["b_raw"])
        self.assertEqual(
            routine["transaction"]["candidate"]["deployment_receipt"]["sha256"],
            sha256(routine["c_raw"]),
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"validator_code_executed=no", result.stderr)
        self.assertFalse(routine["launcher_marker"].exists())

    def test_recursive_routine_rollback_rejects_candidate_intrinsic_substitution(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        routine = self._prepare_routine_activation_smoke(
            fixture,
            phase="rollback-smoke",
            trust_change=True,
            successor=True,
        )
        c_intrinsic_before = json.loads(
            json.dumps(
                next(
                    provider
                    for provider in routine["c_receipt"]["providers"]
                    if provider["intrinsic"]
                )
            )
        )
        trust_path = Path(routine["c_receipt"]["trust_context"]["path"])
        trust_before = trust_path.read_bytes()
        alternate = document(
            {
                "schema_version": 1,
                "contract": "task-witness-intrinsic-smoke-provider-v1",
                "substitution": "recursive-rollback",
            }
        )

        def substitute_intrinsic_declaration(receipt: dict[str, object]) -> None:
            intrinsic = next(
                provider for provider in receipt["providers"] if provider["intrinsic"]
            )
            intrinsic["declaration_sha256"] = sha256(canonical_document(alternate))
            intrinsic["declaration_content_sha256"] = alternate["content_sha256"]

        self._rewrite_recursive_routine_candidate_receipt(
            fixture,
            routine,
            substitute_intrinsic_declaration,
        )
        c_intrinsic_after = next(
            provider
            for provider in routine["c_receipt"]["providers"]
            if provider["intrinsic"]
        )
        self.assertEqual(
            c_intrinsic_after["retained_modules"],
            c_intrinsic_before["retained_modules"],
        )
        self.assertEqual(trust_path.read_bytes(), trust_before)
        self.assertEqual(fixture.deployment.read_bytes(), routine["b_raw"])

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"validator_code_executed=no", result.stderr)
        self.assertFalse(routine["launcher_marker"].exists())

    def test_recursive_routine_rollback_rejects_candidate_source_revision_drift(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        routine = self._prepare_routine_activation_smoke(
            fixture,
            phase="rollback-smoke",
            successor=True,
        )
        active_revision = routine["c_receipt"]["active"]["public_release"]["revision"]

        def substitute_revision(receipt: dict[str, object]) -> None:
            receipt["source"]["revision"] = "1" * 40

        self._rewrite_recursive_routine_candidate_receipt(
            fixture,
            routine,
            substitute_revision,
        )
        self.assertEqual(fixture.deployment.read_bytes(), routine["b_raw"])
        self.assertEqual(
            routine["c_receipt"]["active"]["public_release"]["revision"],
            active_revision,
        )
        self.assertEqual(routine["c_receipt"]["source"]["revision"], "1" * 40)
        self.assertNotEqual(
            routine["c_receipt"]["source"]["revision"],
            active_revision,
        )
        self.assertEqual(
            routine["transaction"]["candidate"]["deployment_receipt"]["sha256"],
            sha256(routine["c_raw"]),
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"validator_code_executed=no", result.stderr)
        self.assertFalse(routine["launcher_marker"].exists())

    def test_recursive_routine_candidate_source_projection_rejects_one_field_drift(
        self,
    ) -> None:
        replacements = {
            "repository_id": "nisavid/alternate-agents",
            "revision": "1" * 40,
        }

        def source_substitution(field: str, replacement: str):
            def substitute(receipt: dict[str, object]) -> None:
                receipt["source"][field] = replacement

            return substitute

        for phase, field in (
            ("candidate-smoke", "repository_id"),
            ("candidate-smoke", "revision"),
            ("rollback-smoke", "repository_id"),
        ):
            with self.subTest(phase=phase, field=field):
                fixture = ValidInvocationFixture(self.root / f"{phase}-{field}")
                routine = self._prepare_routine_activation_smoke(
                    fixture,
                    phase=phase,
                    successor=True,
                )
                source_before = json.loads(json.dumps(routine["c_receipt"]["source"]))
                active_before = json.loads(json.dumps(routine["c_receipt"]["active"]))

                self._rewrite_recursive_routine_candidate_receipt(
                    fixture,
                    routine,
                    source_substitution(field, replacements[field]),
                )
                expected_source = {**source_before, field: replacements[field]}
                self.assertEqual(routine["c_receipt"]["source"], expected_source)
                self.assertEqual(routine["c_receipt"]["active"], active_before)
                selected_raw = (
                    routine["c_raw"] if phase == "candidate-smoke" else routine["b_raw"]
                )
                self.assertEqual(fixture.deployment.read_bytes(), selected_raw)
                self.assertEqual(
                    routine["transaction"]["candidate"]["deployment_receipt"]["sha256"],
                    sha256(routine["c_raw"]),
                )

                result = self._invoke_activation_smoke(fixture)

                self.assertEqual(result.returncode, 70, result.stderr)
                self.assertEqual(result.stdout, b"")
                self.assertIn(b"validator_code_executed=no", result.stderr)
                self.assertFalse(routine["launcher_marker"].exists())

    def test_recursive_routine_deep_ancestor_source_projection_is_receipt_local(
        self,
    ) -> None:
        replacements = {
            "repository_id": "nisavid/alternate-agents",
            "revision": "1" * 40,
        }

        def source_substitution(field: str, replacement: str):
            def substitute(receipt: dict[str, object]) -> None:
                receipt["source"][field] = replacement

            return substitute

        for phase in ("candidate-smoke", "rollback-smoke"):
            for field, replacement in replacements.items():
                with self.subTest(phase=phase, field=field):
                    fixture = ValidInvocationFixture(self.root / f"{phase}-{field}")
                    routine = self._prepare_routine_activation_smoke(
                        fixture,
                        phase=phase,
                        successor=True,
                    )
                    source_before = json.loads(
                        json.dumps(routine["a_receipt"]["source"])
                    )
                    active_before = json.loads(
                        json.dumps(routine["a_receipt"]["active"])
                    )

                    self._rewrite_routine_ancestor_and_rebind_successors(
                        fixture,
                        routine,
                        source_substitution(field, replacement),
                    )
                    expected_source = {**source_before, field: replacement}
                    self.assertEqual(
                        routine["a_receipt"]["source"],
                        expected_source,
                    )
                    self.assertEqual(routine["a_receipt"]["active"], active_before)
                    self.assertEqual(
                        routine["b_receipt"]["prior_receipt_sha256"],
                        sha256(routine["a_raw"]),
                    )
                    self.assertEqual(
                        routine["c_receipt"]["prior_receipt_sha256"],
                        sha256(routine["b_raw"]),
                    )
                    selected_raw = (
                        routine["c_raw"]
                        if phase == "candidate-smoke"
                        else routine["b_raw"]
                    )
                    self.assertEqual(fixture.deployment.read_bytes(), selected_raw)
                    self.assertEqual(
                        routine["transaction"]["candidate"]["deployment_receipt"][
                            "sha256"
                        ],
                        sha256(routine["c_raw"]),
                    )

                    result = self._invoke_activation_smoke(fixture)

                    self.assertEqual(result.returncode, 70, result.stderr)
                    self.assertEqual(result.stdout, b"")
                    self.assertIn(b"validator_code_executed=no", result.stderr)
                    self.assertFalse(routine["launcher_marker"].exists())

    def test_recursive_routine_external_provider_rollback_uses_c_authority(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        routine = self._prepare_routine_activation_smoke(
            fixture,
            phase="rollback-smoke",
            trust_change=True,
            successor=True,
        )
        self.assertEqual(fixture.deployment.read_bytes(), routine["b_raw"])
        self.assertEqual(
            routine["transaction"]["candidate"]["deployment_receipt"]["sha256"],
            sha256(routine["c_raw"]),
        )
        for receipt in (routine["b_receipt"], routine["c_receipt"]):
            external = [
                provider
                for provider in receipt["providers"]
                if not provider["intrinsic"]
            ]
            self.assertEqual(len(external), 1)
            provider = external[0]
            self.assertEqual(
                (
                    receipt["source"]["plugin_id"],
                    receipt["source"]["publisher_id"],
                    receipt["source"]["repository_url"],
                    receipt["source"]["provider_declaration_sha256"],
                    receipt["source"]["provider_declaration_content_sha256"],
                ),
                (
                    provider["plugin_id"],
                    provider["publisher"],
                    provider["repository"],
                    provider["declaration_sha256"],
                    provider["declaration_content_sha256"],
                ),
            )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            canonical_document(routine["selected_envelope"]),
        )
        self.assertEqual(result.stderr, b"")
        self.assertTrue(routine["launcher_marker"].is_file())

    def test_recursive_routine_reactivation_preserves_other_history(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        preserved_path, preserved_sha256, _ = fixture.add_historical_context(
            state="revoked"
        )
        preserved = {
            "path": str(preserved_path),
            "sha256": preserved_sha256,
            "state": "revoked",
        }
        routine = self._prepare_routine_activation_smoke(
            fixture,
            phase="candidate-smoke",
            trust_change=True,
            successor=True,
        )

        self.assertIn(
            preserved,
            routine["a_receipt"]["historical_trust_contexts"],
        )
        self.assertIn(
            preserved,
            routine["b_receipt"]["historical_trust_contexts"],
        )
        self.assertIn(
            preserved,
            routine["c_receipt"]["historical_trust_contexts"],
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            canonical_document(routine["selected_envelope"]),
        )
        self.assertEqual(result.stderr, b"")
        self.assertTrue(routine["launcher_marker"].is_file())

    def test_routine_retained_receipt_cannot_list_its_active_trust_as_history(
        self,
    ) -> None:
        def retain_active_trust(receipt: dict[str, object]) -> None:
            active_trust = receipt["trust_context"]
            receipt["historical_trust_contexts"].append(
                {
                    "path": active_trust["path"],
                    "sha256": active_trust["sha256"],
                    "state": "historical-usable",
                }
            )
            receipt["historical_trust_contexts"].sort(key=lambda item: item["sha256"])

        for phase, successor in (
            ("candidate-smoke", False),
            ("rollback-smoke", True),
        ):
            with self.subTest(phase=phase, successor=successor):
                fixture = ValidInvocationFixture(self.root / phase)
                routine = self._prepare_routine_activation_smoke(
                    fixture,
                    phase=phase,
                    trust_change=False,
                    successor=successor,
                )
                self._rewrite_routine_ancestor_and_rebind_successors(
                    fixture,
                    routine,
                    retain_active_trust,
                )
                a_sha256 = sha256(routine["a_raw"])
                b_sha256 = sha256(routine["b_raw"])
                active_trust = routine["a_receipt"]["trust_context"]

                self.assertEqual(
                    routine["a_receipt"]["historical_trust_contexts"],
                    [
                        {
                            "path": active_trust["path"],
                            "sha256": active_trust["sha256"],
                            "state": "historical-usable",
                        }
                    ],
                )
                self.assertEqual(
                    routine["b_receipt"]["prior_receipt_sha256"],
                    a_sha256,
                )
                self.assertEqual(
                    routine["rollback"]["precondition"]["active_receipt_sha256"],
                    a_sha256,
                )
                self.assertEqual(fixture.deployment.read_bytes(), routine["b_raw"])
                if successor:
                    self.assertEqual(
                        routine["c_receipt"]["prior_receipt_sha256"],
                        b_sha256,
                    )
                    self.assertEqual(
                        routine["transaction"]["candidate"]["deployment_receipt"][
                            "sha256"
                        ],
                        sha256(routine["c_raw"]),
                    )
                else:
                    self.assertEqual(
                        routine["transaction"]["candidate"]["deployment_receipt"][
                            "sha256"
                        ],
                        b_sha256,
                    )

                result = self._invoke_activation_smoke(fixture)

                self.assertEqual(result.returncode, 70, result.stderr)
                self.assertEqual(result.stdout, b"")
                self.assertIn(b"validator_code_executed=no", result.stderr)
                self.assertFalse(routine["launcher_marker"].exists())

    def test_recursive_routine_reactivation_requires_exact_history_rebound(
        self,
    ) -> None:
        def retain_current(
            fixture: ValidInvocationFixture,
            routine: dict[str, object],
        ) -> None:
            def mutate(receipt: dict[str, object]) -> None:
                current = receipt["trust_context"]
                receipt["historical_trust_contexts"].append(
                    {
                        "path": current["path"],
                        "sha256": current["sha256"],
                        "state": "historical-usable",
                    }
                )
                receipt["historical_trust_contexts"].sort(
                    key=lambda item: item["sha256"]
                )

            self._rewrite_recursive_routine_candidate_receipt(
                fixture,
                routine,
                mutate,
            )

        def omit_immediate_prior(
            fixture: ValidInvocationFixture,
            routine: dict[str, object],
        ) -> None:
            self._rewrite_recursive_routine_candidate_receipt(
                fixture,
                routine,
                lambda receipt: receipt.__setitem__(
                    "historical_trust_contexts",
                    [],
                ),
            )

        def revoke_reactivated_current(
            fixture: ValidInvocationFixture,
            routine: dict[str, object],
        ) -> None:
            self._rewrite_recursive_routine_prior_and_rebind_candidate(
                fixture,
                routine,
                lambda receipt: receipt["historical_trust_contexts"][0].__setitem__(
                    "state",
                    "revoked",
                ),
            )

        def rebind_history_to_wrong_current(
            fixture: ValidInvocationFixture,
            routine: dict[str, object],
        ) -> None:
            def mutate(receipt: dict[str, object]) -> None:
                wrong = receipt["trust_context"]
                receipt["historical_trust_contexts"][0] = {
                    "path": wrong["path"],
                    "sha256": wrong["sha256"],
                    "state": "historical-usable",
                }

            self._rewrite_recursive_routine_prior_and_rebind_candidate(
                fixture,
                routine,
                mutate,
            )

        for name, mutate in {
            "current-retained": retain_current,
            "immediate-prior-missing": omit_immediate_prior,
            "current-rebound-revoked": revoke_reactivated_current,
            "current-rebound-wrong-binding": rebind_history_to_wrong_current,
        }.items():
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                routine = self._prepare_routine_activation_smoke(
                    fixture,
                    phase="candidate-smoke",
                    trust_change=True,
                    successor=True,
                )
                mutate(fixture, routine)
                if name in {
                    "current-rebound-revoked",
                    "current-rebound-wrong-binding",
                }:
                    b_sha256 = sha256(routine["b_raw"])
                    c_sha256 = sha256(routine["c_raw"])
                    c_rollback_sha256 = sha256(
                        canonical_document(routine["c_rollback"])
                    )
                    if name == "current-rebound-revoked":
                        self.assertEqual(
                            routine["b_receipt"]["historical_trust_contexts"][0][
                                "state"
                            ],
                            "revoked",
                        )
                    else:
                        self.assertEqual(
                            routine["b_receipt"]["historical_trust_contexts"][0][
                                "sha256"
                            ],
                            routine["b_receipt"]["trust_context"]["sha256"],
                        )
                        self.assertNotEqual(
                            routine["b_receipt"]["historical_trust_contexts"][0][
                                "sha256"
                            ],
                            routine["c_receipt"]["trust_context"]["sha256"],
                        )
                    self.assertEqual(
                        routine["c_receipt"]["prior_receipt_sha256"],
                        b_sha256,
                    )
                    self.assertEqual(
                        routine["c_receipt"]["authorization"][
                            "expected_active_receipt_sha256"
                        ],
                        b_sha256,
                    )
                    self.assertEqual(
                        routine["c_rollback"]["precondition"]["active_receipt_sha256"],
                        b_sha256,
                    )
                    self.assertEqual(
                        routine["transaction"]["candidate"]["deployment_receipt"][
                            "sha256"
                        ],
                        c_sha256,
                    )
                    self.assertEqual(
                        routine["transaction"]["rollback_authority"]["receipt_sha256"],
                        c_rollback_sha256,
                    )

                result = self._invoke_activation_smoke(fixture)

                self.assertEqual(result.returncode, 70, result.stderr)
                self.assertEqual(result.stdout, b"")
                self.assertFalse(routine["launcher_marker"].exists())

    def test_recursive_routine_rejects_missing_tampered_or_extra_ancestor(
        self,
    ) -> None:
        def missing(routine: dict[str, object]) -> None:
            routine["a_retained"].unlink()

        def tampered(routine: dict[str, object]) -> None:
            routine["rollback_path"].write_bytes(b"{}\n")
            routine["rollback_path"].chmod(0o600)

        def extra(routine: dict[str, object]) -> None:
            receipts_directory = routine["c_retained"].parent
            extra_raw = canonical_document({"inactive": True})
            extra_path = receipts_directory / f"sha256-{sha256(extra_raw)}.json"
            extra_path.write_bytes(extra_raw)
            extra_path.chmod(0o600)

        for name, mutate in {
            "missing": missing,
            "tampered": tampered,
            "extra": extra,
        }.items():
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                routine = self._prepare_routine_activation_smoke(
                    fixture,
                    phase="candidate-smoke",
                    successor=True,
                )
                mutate(routine)

                result = self._invoke_activation_smoke(fixture)

                self.assertEqual(result.returncode, 70, result.stderr)
                self.assertEqual(result.stdout, b"")
                self.assertFalse(routine["launcher_marker"].exists())

    def test_routine_external_provider_declaration_binds_source_in_both_phases(
        self,
    ) -> None:
        for phase in ("candidate-smoke", "rollback-smoke"):
            with self.subTest(phase=phase):
                fixture = ValidInvocationFixture(self.root / phase)
                routine = self._prepare_routine_activation_smoke(
                    fixture,
                    phase=phase,
                    trust_change=True,
                )
                for receipt in (
                    routine["a_receipt"],
                    routine["b_receipt"],
                ):
                    external = [
                        provider
                        for provider in receipt["providers"]
                        if not provider["intrinsic"]
                    ]
                    self.assertEqual(len(external), 1)
                    provider = external[0]
                    self.assertEqual(
                        (
                            receipt["source"]["plugin_id"],
                            receipt["source"]["publisher_id"],
                            receipt["source"]["repository_url"],
                            receipt["source"]["provider_declaration_sha256"],
                            receipt["source"]["provider_declaration_content_sha256"],
                        ),
                        (
                            provider["plugin_id"],
                            provider["publisher"],
                            provider["repository"],
                            provider["declaration_sha256"],
                            provider["declaration_content_sha256"],
                        ),
                    )

                result = self._invoke_activation_smoke(fixture)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    result.stdout,
                    canonical_document(routine["selected_envelope"]),
                )
                self.assertEqual(result.stderr, b"")
                self.assertTrue(routine["launcher_marker"].is_file())

    def test_routine_provider_declaration_crossbind_rejects_substitutions(
        self,
    ) -> None:
        def alternate_declaration(label: str) -> tuple[str, str]:
            declaration = document(
                {
                    "schema_version": 1,
                    "contract": "task-witness-provider-v1",
                    "substitution": label,
                }
            )
            return (
                sha256(canonical_document(declaration)),
                declaration["content_sha256"],
            )

        def external_declaration(receipt: dict[str, object]) -> None:
            external = next(
                provider
                for provider in receipt["providers"]
                if not provider["intrinsic"]
            )
            raw_sha256, content_sha256 = alternate_declaration("external")
            external["declaration_sha256"] = raw_sha256
            external["declaration_content_sha256"] = content_sha256

        def unbound_source(receipt: dict[str, object]) -> None:
            receipt["source"]["provider_declaration_sha256"] = None
            receipt["source"]["provider_declaration_content_sha256"] = None

        def wrong_external_identity(receipt: dict[str, object]) -> None:
            external = next(
                provider
                for provider in receipt["providers"]
                if not provider["intrinsic"]
            )
            external["publisher"] = "substituted-publisher"

        def wrong_source_declaration(receipt: dict[str, object]) -> None:
            raw_sha256, content_sha256 = alternate_declaration("source")
            receipt["source"]["provider_declaration_sha256"] = raw_sha256
            receipt["source"]["provider_declaration_content_sha256"] = content_sha256

        def intrinsic_declaration(receipt: dict[str, object]) -> None:
            intrinsic = next(
                provider for provider in receipt["providers"] if provider["intrinsic"]
            )
            raw_sha256, content_sha256 = alternate_declaration("intrinsic")
            intrinsic["declaration_sha256"] = raw_sha256
            intrinsic["declaration_content_sha256"] = content_sha256

        for name, mutate in {
            "external-declaration": external_declaration,
            "external-identity": wrong_external_identity,
            "source-unbound": unbound_source,
            "source-wrong-declaration": wrong_source_declaration,
            "intrinsic-declaration": intrinsic_declaration,
        }.items():
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                routine = self._prepare_routine_activation_smoke(
                    fixture,
                    phase="candidate-smoke",
                    trust_change=True,
                )
                source_before = json.loads(json.dumps(routine["b_receipt"]["source"]))
                intrinsic_before = json.loads(
                    json.dumps(
                        next(
                            provider
                            for provider in routine["b_receipt"]["providers"]
                            if provider["intrinsic"]
                        )
                    )
                )
                trust_path = Path(routine["b_receipt"]["trust_context"]["path"])
                trust_before = trust_path.read_bytes()
                self._rewrite_routine_b_receipt(fixture, routine, mutate)
                self.assertEqual(
                    routine["transaction"]["candidate"]["deployment_receipt"]["sha256"],
                    sha256(routine["b_raw"]),
                )
                if name == "external-declaration":
                    self.assertEqual(routine["b_receipt"]["source"], source_before)
                if name == "intrinsic-declaration":
                    intrinsic_after = next(
                        provider
                        for provider in routine["b_receipt"]["providers"]
                        if provider["intrinsic"]
                    )
                    self.assertEqual(
                        intrinsic_after["retained_modules"],
                        intrinsic_before["retained_modules"],
                    )
                    self.assertEqual(trust_path.read_bytes(), trust_before)

                result = self._invoke_activation_smoke(fixture)

                self.assertEqual(result.returncode, 70, result.stderr)
                self.assertEqual(result.stdout, b"")
                self.assertIn(b"validator_code_executed=no", result.stderr)
                self.assertFalse(routine["launcher_marker"].exists())

    def test_routine_candidate_allows_new_trust_with_exact_a_history(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        routine = self._prepare_routine_activation_smoke(
            fixture,
            phase="candidate-smoke",
            trust_change=True,
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertNotEqual(
            routine["a_receipt"]["trust_context"]["sha256"],
            routine["b_receipt"]["trust_context"]["sha256"],
        )
        self.assertEqual(
            routine["b_receipt"]["historical_trust_contexts"],
            [
                {
                    "path": routine["a_receipt"]["trust_context"]["path"],
                    "sha256": routine["a_receipt"]["trust_context"]["sha256"],
                    "state": "historical-usable",
                }
            ],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            canonical_document(routine["selected_envelope"]),
        )
        self.assertTrue(routine["launcher_marker"].is_file())

    def test_routine_candidate_requires_exact_prior_trust_history(
        self,
    ) -> None:
        def missing(receipt: dict[str, object]) -> None:
            receipt["historical_trust_contexts"] = []

        def malformed(receipt: dict[str, object]) -> None:
            receipt["historical_trust_contexts"][0]["state"] = "active"

        def duplicate(receipt: dict[str, object]) -> None:
            receipt["historical_trust_contexts"].append(
                json.loads(json.dumps(receipt["historical_trust_contexts"][0]))
            )

        for name, mutate in {
            "missing": missing,
            "malformed": malformed,
            "duplicate": duplicate,
        }.items():
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                routine = self._prepare_routine_activation_smoke(
                    fixture,
                    phase="candidate-smoke",
                    trust_change=True,
                )
                self._rewrite_routine_b_receipt(fixture, routine, mutate)

                result = self._invoke_activation_smoke(fixture)

                self.assertEqual(result.returncode, 70, result.stderr)
                self.assertEqual(result.stdout, b"")
                self.assertFalse(routine["launcher_marker"].exists())

    def test_routine_candidate_rejects_control_drift_from_prior_before_launcher(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        routine = self._prepare_routine_activation_smoke(
            fixture,
            phase="candidate-smoke",
        )
        self._rebind_routine_candidate_after_control_change(fixture, routine)

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertFalse(routine["launcher_marker"].exists())

    def test_routine_candidate_rejects_a_authorization_substituted_for_b(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        routine = self._prepare_routine_activation_smoke(
            fixture,
            phase="candidate-smoke",
        )
        a_authorization = routine["a_receipt"]["authorization"]

        def substitute(transaction: dict[str, object]) -> None:
            transaction["outer_maintenance_transaction_sha256"] = a_authorization[
                "maintenance_transaction_sha256"
            ]
            transaction["stage"].update(
                plan_sha256=a_authorization["plan_sha256"],
                authorization_sha256=a_authorization["sha256"],
                maintenance_transaction_sha256=a_authorization[
                    "maintenance_transaction_sha256"
                ],
            )

        self._rewrite_transaction(
            fixture,
            substitute,
            rebind_intent=True,
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertFalse(routine["launcher_marker"].exists())

    def test_active_prior_rejects_control_maintenance_class_substitution(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        routine = self._prepare_routine_activation_smoke(
            fixture,
            phase="candidate-smoke",
        )
        self._rewrite_transaction(
            fixture,
            lambda transaction: transaction.__setitem__(
                "transaction_class",
                "control-set-maintenance",
            ),
            rebind_intent=True,
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertFalse(routine["launcher_marker"].exists())

    def test_routine_candidate_rejects_b_to_a_chain_mismatch(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        routine = self._prepare_routine_activation_smoke(
            fixture,
            phase="candidate-smoke",
        )
        self._rewrite_routine_b_receipt(
            fixture,
            routine,
            lambda receipt: receipt.__setitem__(
                "prior_receipt_sha256",
                "f" * 64,
            ),
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertFalse(routine["launcher_marker"].exists())

    def test_routine_candidate_rejects_transaction_target_swaps(self) -> None:
        def candidate_prior(
            transaction: dict[str, object],
            routine: dict[str, object],
        ) -> None:
            transaction["candidate"], transaction["prior"] = (
                transaction["prior"],
                transaction["candidate"],
            )
            target = transaction["candidate"]
            transaction["smoke_handoff"] = {
                "target_deployment_receipt_sha256": target["deployment_receipt"][
                    "sha256"
                ],
                "smoke_bundle_sha256": target["smoke"]["bundle"]["sha256"],
                "smoke_trust_context_sha256": target["smoke"]["trust_context"][
                    "sha256"
                ],
            }

        def handoff(
            transaction: dict[str, object],
            routine: dict[str, object],
        ) -> None:
            target = routine["a_unit"]
            transaction["smoke_handoff"] = {
                "target_deployment_receipt_sha256": target["deployment_receipt"][
                    "sha256"
                ],
                "smoke_bundle_sha256": target["smoke"]["bundle"]["sha256"],
                "smoke_trust_context_sha256": target["smoke"]["trust_context"][
                    "sha256"
                ],
            }

        def smoke(
            transaction: dict[str, object],
            routine: dict[str, object],
        ) -> None:
            transaction["candidate"]["smoke"] = routine["a_unit"]["smoke"]

        for name, mutate in {
            "candidate-prior": candidate_prior,
            "handoff": handoff,
            "smoke": smoke,
        }.items():
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                routine = self._prepare_routine_activation_smoke(
                    fixture,
                    phase="candidate-smoke",
                )

                def apply(
                    transaction: dict[str, object],
                    mutate=mutate,
                    routine=routine,
                ) -> None:
                    mutate(transaction, routine)

                self._rewrite_transaction(
                    fixture,
                    apply,
                    rebind_intent=name in {"candidate-prior", "smoke"},
                )

                result = self._invoke_activation_smoke(fixture)

                self.assertEqual(result.returncode, 70, result.stderr)
                self.assertEqual(result.stdout, b"")
                self.assertFalse(routine["launcher_marker"].exists())

    def test_routine_candidate_rejects_mixed_live_receipt_or_active_state(
        self,
    ) -> None:
        for name in ("receipt", "active"):
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                routine = self._prepare_routine_activation_smoke(
                    fixture,
                    phase="candidate-smoke",
                )
                if name == "receipt":
                    fixture.deployment.write_bytes(routine["a_raw"])
                    fixture.deployment.chmod(0o600)
                else:
                    fixture.active_path.write_bytes(
                        (
                            fixture.root / "routine-stage" / "preimage" / "active.json"
                        ).read_bytes()
                    )
                    fixture.active_path.chmod(0o600)

                result = self._invoke_activation_smoke(fixture)

                self.assertEqual(result.returncode, 70, result.stderr)
                self.assertEqual(result.stdout, b"")
                self.assertFalse(routine["launcher_marker"].exists())

    def test_routine_candidate_rejects_inactive_receipt_inventory_extra(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        routine = self._prepare_routine_activation_smoke(
            fixture,
            phase="candidate-smoke",
        )
        extra_raw = canonical_document({"inactive": True})
        extra = fixture.receipts_directory / f"sha256-{sha256(extra_raw)}.json"
        extra.write_bytes(extra_raw)
        extra.chmod(0o600)

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertFalse(routine["launcher_marker"].exists())

    def test_routine_rollback_smoke_uses_b_authority_for_live_a_target(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        routine = self._prepare_routine_activation_smoke(
            fixture,
            phase="rollback-smoke",
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            canonical_document(routine["selected_envelope"]),
        )
        self.assertEqual(result.stderr, b"")
        self.assertTrue(routine["launcher_marker"].is_file())

    def test_routine_rollback_requires_exact_retained_b_authority_digest(
        self,
    ) -> None:
        for name in ("digest-swap", "missing-retained-b"):
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                routine = self._prepare_routine_activation_smoke(
                    fixture,
                    phase="rollback-smoke",
                )
                if name == "digest-swap":
                    self._rewrite_transaction(
                        fixture,
                        lambda transaction: transaction["candidate"][
                            "deployment_receipt"
                        ].__setitem__("sha256", "f" * 64),
                        rebind_intent=True,
                    )
                else:
                    routine["b_retained"].unlink()

                result = self._invoke_activation_smoke(fixture)

                self.assertEqual(result.returncode, 70, result.stderr)
                self.assertEqual(result.stdout, b"")
                self.assertFalse(routine["launcher_marker"].exists())

    def test_routine_smoke_phase_must_match_the_live_target(self) -> None:
        cases = (
            ("rollback-with-live-b", "candidate-smoke", "rollback-smoke", "a_unit"),
            ("candidate-with-live-a", "rollback-smoke", "candidate-smoke", "b_unit"),
        )
        for name, live_phase, claimed_phase, unit_name in cases:
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                routine = self._prepare_routine_activation_smoke(
                    fixture,
                    phase=live_phase,
                )
                target = routine[unit_name]

                def swap_phase(
                    transaction: dict[str, object],
                    claimed_phase=claimed_phase,
                    target=target,
                ) -> None:
                    transaction["phase"] = claimed_phase
                    transaction["smoke_handoff"] = {
                        "target_deployment_receipt_sha256": target[
                            "deployment_receipt"
                        ]["sha256"],
                        "smoke_bundle_sha256": target["smoke"]["bundle"]["sha256"],
                        "smoke_trust_context_sha256": target["smoke"]["trust_context"][
                            "sha256"
                        ],
                    }

                self._rewrite_transaction(fixture, swap_phase)

                result = self._invoke_activation_smoke(fixture)

                self.assertEqual(result.returncode, 70, result.stderr)
                self.assertEqual(result.stdout, b"")
                self.assertFalse(routine["launcher_marker"].exists())

    def test_routine_rejects_rebound_rollback_authority_substitutions(
        self,
    ) -> None:
        def prior_receipt(rollback: dict[str, object]) -> None:
            rollback["prior_receipt"]["length"] += 1

        def selector_preimage(rollback: dict[str, object]) -> None:
            rollback["selector_preimage"].reverse()

        def external_dependency(rollback: dict[str, object]) -> None:
            rollback["external_dependencies"]["receipt_parser"][
                "deployment_receipt_contract"
            ] = "substituted-deployment-receipt-v1"

        for name, mutate in {
            "prior-receipt": prior_receipt,
            "selector-preimage": selector_preimage,
            "external-dependency": external_dependency,
        }.items():
            with self.subTest(name=name):
                fixture = ValidInvocationFixture(self.root / name)
                routine = self._prepare_routine_activation_smoke(
                    fixture,
                    phase="candidate-smoke",
                )
                self._rewrite_routine_rollback_chain(
                    fixture,
                    routine,
                    mutate,
                )

                result = self._invoke_activation_smoke(fixture)

                self.assertEqual(result.returncode, 70, result.stderr)
                self.assertEqual(result.stdout, b"")
                self.assertFalse(routine["launcher_marker"].exists())

    def test_nonempty_inherited_activation_lock_is_rejected_before_launcher(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        launcher_marker = self.root / "activation-smoke-launcher-ran"
        fixture.lock.write_bytes(b"not lock state\n")
        self._prepare_activation_smoke(
            fixture,
            launcher_source=(
                "#!/usr/bin/env python3\n"
                "from pathlib import Path\n"
                "import sys\n"
                f"Path({str(launcher_marker)!r}).write_text('ran', encoding='utf-8')\n"
                f"sys.stdout.buffer.write({canonical_document(fixture.smoke_expected_envelope)!r})\n"
            ),
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

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertFalse(launcher_marker.exists())

    def test_mode_0700_inherited_activation_lock_is_rejected_by_journal(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        launcher_marker = self.root / "activation-smoke-launcher-ran"
        fixture.lock.chmod(0o700)
        self._prepare_activation_smoke(
            fixture,
            launcher_source=(
                "#!/usr/bin/env python3\n"
                "from pathlib import Path\n"
                "import sys\n"
                f"Path({str(launcher_marker)!r}).write_text('ran', encoding='utf-8')\n"
                f"sys.stdout.buffer.write({canonical_document(fixture.smoke_expected_envelope)!r})\n"
            ),
        )
        transaction = json.loads(fixture.transaction.read_bytes())
        rollback = json.loads(fixture.rollback_receipt_raw)
        self.assertEqual(transaction["activation_lock"]["mode"], 0o700)
        self.assertEqual(fixture.receipt["activation_lock"]["mode"], 0o700)
        self.assertEqual(rollback["activation_lock"]["mode"], 0o700)

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertFalse(launcher_marker.exists())

    def test_visible_activation_lock_full_identity_drift_is_rejected_after_smoke(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        injected = self.root / "visible-lock-identity-drift-injected"
        self._prepare_activation_smoke(fixture)

        result = self._invoke_activation_smoke(
            fixture,
            driver_options=(
                "--visible-lock-identity-drift",
                str(injected),
            ),
        )

        self.assertTrue(injected.is_file(), "visible identity hook did not run")
        self.assertEqual(result.returncode, 70, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertEqual(fixture.lock.stat().st_size, 0)

    def test_rollback_smoke_requires_receipt_authority_for_the_active_prior(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        launcher_marker = self.root / "rollback-launcher-ran"
        self._prepare_activation_smoke(
            fixture,
            phase="rollback-smoke",
            launcher_source=(
                "#!/usr/bin/env python3\n"
                "from pathlib import Path\n"
                "import sys\n"
                f"Path({str(launcher_marker)!r}).write_text('ran', encoding='utf-8')\n"
                f"sys.stdout.buffer.write({canonical_document(fixture.smoke_expected_envelope)!r})\n"
            ),
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, b"")
        self.assertFalse(launcher_marker.exists())

    def test_ordinary_caller_without_inherited_fd3_cannot_run_smoke(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        launcher_marker = self.root / "launcher-ran"
        self._prepare_activation_smoke(
            fixture,
            launcher_source=(
                "#!/usr/bin/env python3\n"
                "from pathlib import Path\n"
                f"Path({str(launcher_marker)!r}).write_text('ran', encoding='utf-8')\n"
            ),
        )

        result = fixture.invoke("activation-smoke")

        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, b"")
        self.assertFalse(launcher_marker.exists())

    def test_public_validation_rejects_while_activation_transaction_exists(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        launcher_marker = self.root / "public-launcher-ran"
        self._prepare_activation_smoke(
            fixture,
            launcher_source=(
                "#!/usr/bin/env python3\n"
                "from pathlib import Path\n"
                "import sys\n"
                f"Path({str(launcher_marker)!r}).write_text('ran', encoding='utf-8')\n"
                f"sys.stdout.buffer.write({fixture.envelope_raw!r})\n"
            ),
        )

        result = self._invoke_public_client(fixture)

        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, b"")
        self.assertFalse(launcher_marker.exists())

    def test_public_validation_rechecks_transaction_absence_after_launcher(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        transaction = fixture.install / "transaction.json"
        fixture.replace_launcher_behavior(
            "#!/usr/bin/env python3\n"
            "from pathlib import Path\n"
            "import sys\n"
            f"Path({str(transaction)!r}).write_bytes(b'crash-recovery-state\\n')\n"
            f"sys.stdout.buffer.write({fixture.envelope_raw!r})\n"
        )

        result = self._invoke_public_client(fixture)

        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, b"")
        self.assertTrue(transaction.exists())

    def test_activation_transaction_is_descriptor_pinned_through_post_child_checks(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        transaction = fixture.install / "transaction.json"
        self._prepare_activation_smoke(
            fixture,
            launcher_source=(
                "#!/usr/bin/env python3\n"
                "from pathlib import Path\n"
                "import sys\n"
                f"transaction = Path({str(transaction)!r})\n"
                "transaction.write_bytes(transaction.read_bytes() + b' ')\n"
                f"sys.stdout.buffer.write({canonical_document(fixture.smoke_expected_envelope)!r})\n"
            ),
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, b"")

    def test_inherited_fd3_without_live_transaction_cannot_run_smoke(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        launcher_marker = self.root / "launcher-ran"
        self._prepare_activation_smoke(
            fixture,
            launcher_source=(
                "#!/usr/bin/env python3\n"
                "from pathlib import Path\n"
                f"Path({str(launcher_marker)!r}).write_text('ran', encoding='utf-8')\n"
            ),
        )
        fixture.transaction.unlink()

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, b"")
        self.assertFalse(launcher_marker.exists())

    def test_fd3_from_a_different_open_file_description_is_rejected(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        launcher_marker = self.root / "launcher-ran"
        self._prepare_activation_smoke(
            fixture,
            launcher_source=(
                "#!/usr/bin/env python3\n"
                "from pathlib import Path\n"
                f"Path({str(launcher_marker)!r}).write_text('ran', encoding='utf-8')\n"
            ),
        )

        result = self._invoke_activation_smoke(
            fixture,
            driver_options=("--separate-owner",),
        )

        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, b"")
        self.assertFalse(launcher_marker.exists())

    def test_handoff_target_must_match_the_phase_target_unit(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._prepare_activation_smoke(fixture)
        self._rewrite_transaction(
            fixture,
            lambda transaction: transaction["smoke_handoff"].__setitem__(
                "target_deployment_receipt_sha256",
                "f" * 64,
            ),
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"current_receipt=unknown", result.stderr)

    def test_phase_target_unit_must_match_live_unit_after_parser_acceptance(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        launcher_marker = self.root / "live-unit-launcher-ran"
        self._prepare_activation_smoke(
            fixture,
            launcher_source=(
                "#!/usr/bin/env python3\n"
                "from pathlib import Path\n"
                "import sys\n"
                f"Path({str(launcher_marker)!r}).write_text('ran', encoding='utf-8')\n"
                f"sys.stdout.buffer.write({canonical_document(fixture.smoke_expected_envelope)!r})\n"
            ),
        )
        self._rewrite_transaction(
            fixture,
            lambda transaction: transaction["candidate"]["control_set"][
                "client"
            ].__setitem__("sha256", "f" * 64),
            rebind_intent=True,
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, b"")
        self.assertFalse(launcher_marker.exists())
        self.assertIn(
            (f"current_receipt=sha256:{fixture.receipt['content_sha256'][:12]}").encode(
                "ascii"
            ),
            result.stderr,
        )

    def test_journal_generation_requires_the_previous_digest_after_sequence_one(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._prepare_activation_smoke(fixture)
        self._rewrite_transaction(
            fixture,
            lambda transaction: transaction.__setitem__("sequence", 2),
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, b"")

    def test_transaction_id_must_match_the_full_immutable_intent(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._prepare_activation_smoke(fixture)
        self._rewrite_transaction(
            fixture,
            lambda transaction: transaction.__setitem__(
                "transaction_id",
                "f" * 64,
            ),
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, b"")

    def test_stage_must_be_an_exact_part_of_the_immutable_intent(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._prepare_activation_smoke(fixture)
        self._rewrite_transaction(
            fixture,
            lambda transaction: transaction["stage"].__setitem__(
                "unexpected",
                None,
            ),
            rebind_intent=True,
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, b"")

    def test_stage_plan_must_match_receipt_derived_smoke_authority(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._prepare_activation_smoke(fixture)
        self._rewrite_transaction(
            fixture,
            lambda transaction: transaction["stage"].__setitem__(
                "plan_sha256",
                sha256(b"substituted stage plan\n"),
            ),
            rebind_intent=True,
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, b"")

    def test_stage_receipt_identity_is_recovery_evidence_not_smoke_authority(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._prepare_activation_smoke(fixture)

        def substitute_stage_receipt(transaction: dict[str, object]) -> None:
            transaction["stage"]["receipt_path"] = str(
                self.root / "recovery" / "stage.json"
            )
            transaction["stage"]["receipt_sha256"] = sha256(
                b"substituted recovery receipt\n"
            )

        self._rewrite_transaction(
            fixture,
            substitute_stage_receipt,
            rebind_intent=True,
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            canonical_document(fixture.smoke_expected_envelope),
        )

    def test_maintenance_identity_must_match_receipt_smoke_authority(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._prepare_activation_smoke(fixture)
        substitute = sha256(b"substituted maintenance transaction\n")

        def substitute_maintenance(transaction: dict[str, object]) -> None:
            transaction["outer_maintenance_transaction_sha256"] = substitute
            transaction["stage"]["maintenance_transaction_sha256"] = substitute

        self._rewrite_transaction(
            fixture,
            substitute_maintenance,
            rebind_intent=True,
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, b"")

    def test_rollback_manifest_must_match_receipt_smoke_authority(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._prepare_activation_smoke(fixture)
        substitute = sha256(b"substituted rollback authority\n")
        substitute_path = fixture.receipts_directory / f"sha256-{substitute}.json"

        def substitute_rollback(transaction: dict[str, object]) -> None:
            transaction["rollback_authority"]["receipt_path"] = str(substitute_path)
            transaction["rollback_authority"]["receipt_sha256"] = substitute
            transaction["preimage"]["manifest_path"] = str(substitute_path)
            transaction["preimage"]["manifest_sha256"] = substitute

        self._rewrite_transaction(
            fixture,
            substitute_rollback,
            rebind_intent=True,
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, b"")

    def test_non_target_unit_must_have_the_exact_active_unit_shape(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._prepare_activation_smoke(fixture)
        self._rewrite_transaction(
            fixture,
            lambda transaction: transaction["prior"].__setitem__(
                "unexpected",
                None,
            ),
            rebind_intent=True,
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, b"")

    def test_routine_payload_smoke_requires_an_active_prior_unit(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._prepare_activation_smoke(fixture)
        self._rewrite_transaction(
            fixture,
            lambda transaction: transaction.__setitem__(
                "transaction_class",
                "routine-payload",
            ),
            rebind_intent=True,
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, b"")

    def test_smoke_phase_rejects_a_pending_control_install_step(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._prepare_activation_smoke(fixture)
        self._rewrite_transaction(
            fixture,
            lambda transaction: transaction.__setitem__(
                "pending_step",
                {
                    "operation": "install",
                    "index": 0,
                    "role": "client",
                },
            ),
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, b"")

    def test_smoke_phase_rejects_premature_candidate_acceptance(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._prepare_activation_smoke(fixture)
        acceptance = document(
            {
                "phase": "candidate-smoke",
                "target_deployment_receipt_sha256": sha256(fixture.deployment_raw),
                "expected_envelope_sha256": (fixture.smoke_expected_envelope_sha256),
                "accepted_envelope_sha256": (fixture.smoke_expected_envelope_sha256),
                "exit_status": 0,
            }
        )
        self._rewrite_transaction(
            fixture,
            lambda transaction: transaction.__setitem__(
                "candidate_smoke_acceptance",
                acceptance,
            ),
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, b"")

    def test_smoke_phase_rejects_premature_rollback_acceptance(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._prepare_activation_smoke(fixture)
        acceptance = document(
            {
                "phase": "rollback-smoke",
                "target_deployment_receipt_sha256": sha256(fixture.deployment_raw),
                "expected_envelope_sha256": (fixture.smoke_expected_envelope_sha256),
                "accepted_envelope_sha256": (fixture.smoke_expected_envelope_sha256),
                "exit_status": 0,
            }
        )
        self._rewrite_transaction(
            fixture,
            lambda transaction: transaction.__setitem__(
                "rollback_smoke_acceptance",
                acceptance,
            ),
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, b"")

    def test_smoke_phase_rejects_a_terminal_result(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._prepare_activation_smoke(fixture)
        target_receipt_sha256 = sha256(fixture.deployment_raw)
        self._rewrite_transaction(
            fixture,
            lambda transaction: transaction.__setitem__(
                "terminal_result",
                {
                    "outcome": "candidate-active",
                    "candidate_receipt_sha256": target_receipt_sha256,
                    "active_receipt_sha256": target_receipt_sha256,
                    "accepted_envelope_sha256": (
                        fixture.smoke_expected_envelope_sha256
                    ),
                    "failure_class": None,
                },
            ),
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, b"")

    def test_smoke_producer_must_be_the_receipt_owned_intrinsic_role(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._prepare_activation_smoke(fixture)
        self._rewrite_receipt_and_refresh_transaction(
            fixture,
            lambda receipt: receipt["smoke"]["producer"].__setitem__(
                "producer_id",
                "other-producer",
            ),
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, b"")

    def test_smoke_bundle_path_is_canonical_and_not_journal_selectable(self) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._prepare_activation_smoke(fixture)
        self._rewrite_receipt_and_refresh_transaction(
            fixture,
            lambda receipt: receipt["smoke"]["bundle"].__setitem__(
                "path",
                str(fixture.bundle),
            ),
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, b"")

    def test_lock_proof_uses_two_fresh_probes_and_restores_fd3_cloexec(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        self._prepare_activation_smoke(fixture)
        audit_marker = self.root / "activation-smoke-audit.json"

        result = self._invoke_activation_smoke(
            fixture,
            audit_marker=audit_marker,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(audit_marker.read_text(encoding="utf-8")),
            {
                "probe_generations": [1, 2],
                "fd3_cloexec_restored": True,
            },
        )

    def test_launcher_cannot_inherit_fd3_while_parent_retains_exclusive_lock(
        self,
    ) -> None:
        fixture = ValidInvocationFixture(self.root)
        launcher_source = (
            "#!/usr/bin/env python3\n"
            "import errno\n"
            "import fcntl\n"
            "import os\n"
            "import sys\n"
            "try:\n"
            "    fcntl.fcntl(3, fcntl.F_GETFD)\n"
            "except OSError as error:\n"
            "    if error.errno != errno.EBADF:\n"
            "        raise\n"
            "else:\n"
            "    raise SystemExit(91)\n"
            f"probe = os.open({str(fixture.lock)!r}, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)\n"
            "try:\n"
            "    try:\n"
            "        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "    except OSError as error:\n"
            "        if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:\n"
            "            raise\n"
            "    else:\n"
            "        raise SystemExit(92)\n"
            "finally:\n"
            "    os.close(probe)\n"
            f"sys.stdout.buffer.write({canonical_document(fixture.smoke_expected_envelope)!r})\n"
        )
        self._prepare_activation_smoke(
            fixture,
            launcher_source=launcher_source,
        )

        result = self._invoke_activation_smoke(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            canonical_document(fixture.smoke_expected_envelope),
        )
        self.assertEqual(result.stderr, b"")


if __name__ == "__main__":
    import unittest

    unittest.main()
