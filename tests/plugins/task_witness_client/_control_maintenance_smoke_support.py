from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.plugins.task_witness_deployment._support import (
    ProviderFixture,
    load_deployment_module,
)

from ._support import (
    CLIENT_ENVIRONMENT,
    ValidInvocationFixture,
    activation_lock_identity,
    canonical_document,
    canonical_value,
    document,
    full_filesystem_identity,
    installed_file,
    sha256,
)

ACTIVATION_SMOKE_DRIVER = Path(__file__).with_name("_activation_smoke_driver.py")
CLIENT_GENERATION_ASSIGNMENT = re.compile(
    rb'(?m)^CLIENT_SOURCE_GENERATION_SHA256 = "([0-9a-f]{64})"$',
)

CONTROL_PATHS = {
    "controller": "controller/task_witness_deploy.py",
    "policy": "controller/policy.json",
    "launcher": "launcher/task_witness_launch.py",
    "client": "client/task_witness_client.py",
    "smoke-bundle-manifest": "smoke/bundle/manifest.json",
    "shim": "task-witness",
}
CONTROL_MODES = {
    "controller": 0o500,
    "policy": 0o600,
    "launcher": 0o500,
    "client": 0o500,
    "smoke-bundle-manifest": 0o600,
    "shim": 0o500,
}


def _distinct_client_source(raw: bytes) -> bytes:
    changed = raw + b"\n# fixture complete-control-set candidate B client\n"
    match = CLIENT_GENERATION_ASSIGNMENT.search(changed)
    if (
        match is None
        or CLIENT_GENERATION_ASSIGNMENT.search(changed, match.end()) is not None
    ):
        raise AssertionError("client source generation assignment is not exact")
    normalized = changed[: match.start(1)] + b"0" * 64 + changed[match.end(1) :]
    digest = sha256(normalized).encode("ascii")
    return changed[: match.start(1)] + digest + changed[match.end(1) :]


CONTROL_ORDER = tuple(CONTROL_PATHS)
MUTATION_ORDER = (
    "controller",
    "policy",
    "launcher",
    "client",
    "smoke-bundle-manifest",
    "active-record",
    "deployment-alias",
    "shim",
)


def _binding(path: Path, raw: bytes, mode: int) -> dict[str, object]:
    return {
        "path": str(path),
        "length": len(raw),
        "sha256": sha256(raw),
        "owner": os.geteuid(),
        "mode": mode,
    }


def _write_private(path: Path, raw: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.chmod(0o700)
    path.write_bytes(raw)
    path.chmod(mode)


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class _CandidateAuthority:
    policy_raw: bytes
    trust_raw: bytes
    trust_path: Path
    trust_sha256: str
    source: dict[str, Any]
    providers: list[dict[str, Any]]
    role_inventory: dict[str, Any]
    historical_trust_contexts: list[dict[str, Any]]
    smoke: dict[str, Any]
    envelope_raw: bytes
    additive_artifacts: tuple[tuple[str, Path, bytes, int], ...]


def _candidate_authority(
    fixture: ValidInvocationFixture,
    prior_receipt: dict[str, Any],
    prior_policy_raw: bytes,
) -> _CandidateAuthority:
    provider_fixture = ProviderFixture(
        fixture.root / "candidate-control-provider",
        prefix="controlb",
    )
    provider = load_deployment_module().materialize_provider(
        provider_fixture.root,
        fixture.install / "trust",
    )
    if provider is None:
        raise AssertionError("candidate control provider was not materialized")
    declaration_raw = provider_fixture.provider_path.read_bytes()
    declaration = json.loads(declaration_raw)
    provider_projection = {
        "plugin_id": provider.plugin_id,
        "publisher": provider.publisher,
        "repository": provider.repository,
        "authority_profile": provider.authority_profile,
        "intrinsic": False,
        "declaration_sha256": sha256(declaration_raw),
        "declaration_content_sha256": declaration["content_sha256"],
        "producers": _thaw(provider.producers),
        "issuers": _thaw(provider.issuers),
        "validators": _thaw(provider.validators),
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

    trust = json.loads(fixture.trust_raw)
    trust.pop("content_sha256")
    for category in ("producers", "issuers", "validators"):
        trust[category].extend(_thaw(getattr(provider, category)))
        identifier = f"{category[:-1]}_id"
        trust[category].sort(
            key=lambda item: (
                item[identifier],
                item["contract"],
                item["implementation_sha256"],
            )
        )
    trust_raw = canonical_document(document(trust))
    trust_sha256 = sha256(trust_raw)
    trust_path = fixture.context_directory / f"sha256-{trust_sha256}.json"
    _write_private(trust_path, trust_raw, 0o600)

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
                {key: _thaw(item[key]) for key in fields}
                for item in getattr(provider, category)
            ]
            for category, fields in policy_fields.items()
        },
    }
    policy = json.loads(prior_policy_raw)
    policy.pop("content_sha256")
    policy["source"].update(
        plugin_id=provider.plugin_id,
        publisher_id=provider.publisher,
        repository_url=provider.repository,
    )
    policy["providers"] = [policy_provider]
    policy_raw = canonical_document(document(policy))

    source = json.loads(json.dumps(prior_receipt["source"]))
    source.update(
        plugin_id=provider.plugin_id,
        publisher_id=provider.publisher,
        repository_url=provider.repository,
        provider_declaration_sha256=sha256(declaration_raw),
        provider_declaration_content_sha256=declaration["content_sha256"],
    )
    providers = sorted(
        [
            json.loads(json.dumps(prior_receipt["providers"][0])),
            provider_projection,
        ],
        key=lambda item: item["plugin_id"],
    )
    historical = sorted(
        [
            *json.loads(json.dumps(prior_receipt["historical_trust_contexts"])),
            {
                "path": str(fixture.trust_context),
                "sha256": fixture.trust_sha256,
                "state": "historical-usable",
            },
        ],
        key=lambda item: item["sha256"],
    )
    envelope = json.loads(json.dumps(fixture.smoke_expected_envelope))
    envelope["anchor"]["trust_context_sha256"] = trust_sha256
    envelope["witness"]["trust_context_sha256"] = trust_sha256
    envelope_raw = canonical_document(envelope)
    smoke = json.loads(json.dumps(prior_receipt["smoke"]))
    smoke["trust_context"] = {
        "path": str(trust_path),
        "sha256": trust_sha256,
    }
    smoke["expected_anchor"]["trust_context_sha256"] = trust_sha256
    smoke["expected_envelope_sha256"] = sha256(envelope_raw)

    expected_policy_source = {
        "plugin_id": source["plugin_id"],
        "mode": source["mode"],
        "publisher_id": source["publisher_id"],
        "manifest_author": source["manifest_author"],
        "repository_id": source["repository_id"],
        "repository_url": source["repository_url"],
        "source_authority": source["source_authority"],
        "details": {
            "channel": source["details"]["channel"],
            "trust_class": source["details"]["trust_class"],
            "lineage_id": source["details"]["lineage"]["lineage_id"],
        },
    }
    if policy["source"] != expected_policy_source:
        raise AssertionError("candidate policy source projection is not exact")
    if policy_provider["plugin_id"] != provider_projection["plugin_id"]:
        raise AssertionError("candidate policy provider projection is not exact")
    for category, fields in policy_fields.items():
        expected = [
            {key: item[key] for key in fields} for item in provider_projection[category]
        ]
        if policy_provider[category] != expected:
            raise AssertionError("candidate policy role projection is not exact")

    return _CandidateAuthority(
        policy_raw=policy_raw,
        trust_raw=trust_raw,
        trust_path=trust_path,
        trust_sha256=trust_sha256,
        source=source,
        providers=providers,
        role_inventory={
            category: json.loads(json.dumps(trust[category]))
            for category in ("producers", "issuers", "validators")
        },
        historical_trust_contexts=historical,
        smoke=smoke,
        envelope_raw=envelope_raw,
        additive_artifacts=(
            ("trust-context", trust_path, trust_raw, 0o600),
            *tuple(
                ("validator-module", module.path, module.raw, 0o600)
                for module in provider.modules
            ),
        ),
    )


def _activation_unit(
    fixture: ValidInvocationFixture,
    receipt: dict[str, Any],
    receipt_raw: bytes,
) -> dict[str, object]:
    return {
        "state": "active",
        "deployment_receipt": _binding(
            fixture.deployment,
            receipt_raw,
            0o600,
        ),
        "active_record": _binding(
            fixture.active_path,
            fixture.active_raw,
            0o600,
        ),
        "control_set": receipt["control_set"],
        "smoke": receipt["smoke"],
    }


@dataclass(frozen=True)
class ControlMaintenanceSmokeScenario:
    """Handcrafted client-parser contract fixture, not controller-stage evidence."""

    fixture: ValidInvocationFixture
    phase: str
    launcher_marker: Path
    expected_envelope_raw: bytes
    stage_path: Path
    stage_raw: bytes
    stage: dict[str, Any]
    transaction: dict[str, Any]
    prior_receipt: dict[str, Any]
    candidate_receipt: dict[str, Any]
    rollback_receipt: dict[str, Any]

    def invoke(self) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [
                sys.executable,
                "-B",
                "-I",
                "-S",
                "-X",
                "disable-remote-debug",
                str(ACTIVATION_SMOKE_DRIVER),
                str(self.fixture.client),
                str(self.fixture.install),
                "activation-smoke",
            ],
            text=False,
            capture_output=True,
            check=False,
            env=CLIENT_ENVIRONMENT,
            timeout=10,
        )


def rewrite_control_maintenance_transaction(
    scenario: ControlMaintenanceSmokeScenario,
    mutate,
) -> None:
    transaction = json.loads(json.dumps(scenario.transaction))
    transaction.pop("content_sha256")
    mutate(transaction)
    identity = {
        "contract": "task-witness-activation-intent-v1",
        "transaction_class": transaction["transaction_class"],
        "canonical_root": transaction["canonical_root"],
        "effective_uid": transaction["effective_uid"],
        "activation_lock": transaction["activation_lock"],
        "outer_maintenance_transaction_sha256": transaction[
            "outer_maintenance_transaction_sha256"
        ],
        "stage": transaction["stage"],
        "prior": transaction["prior"],
        "candidate": transaction["candidate"],
        "rollback_authority": transaction["rollback_authority"],
        "preimage": transaction["preimage"],
    }
    transaction["transaction_id"] = sha256(canonical_value(identity))
    transaction = document(transaction)
    scenario.transaction.clear()
    scenario.transaction.update(transaction)
    _write_private(
        scenario.fixture.install / "transaction.json",
        canonical_document(transaction),
        0o600,
    )


def rewrite_control_maintenance_stage(
    scenario: ControlMaintenanceSmokeScenario,
    mutate,
) -> None:
    stage = json.loads(json.dumps(scenario.stage))
    stage.pop("content_sha256")
    mutate(stage)
    stage = document(stage)
    raw = canonical_document(stage)
    scenario.stage.clear()
    scenario.stage.update(stage)
    _write_private(scenario.stage_path, raw, 0o600)
    rewrite_control_maintenance_transaction(
        scenario,
        lambda transaction: transaction["stage"].__setitem__(
            "receipt_sha256",
            sha256(raw),
        ),
    )


def swap_staged_control_maintenance_receipts(
    scenario: ControlMaintenanceSmokeScenario,
) -> None:
    def mutate(stage: dict[str, Any]) -> None:
        artifacts = {item["role"]: item for item in stage["artifacts"]}
        candidate = artifacts["deployment-alias"]
        prior = artifacts["prior-deployment-alias"]
        candidate_path = Path(candidate["staged"]["path"])
        prior_path = Path(prior["staged"]["path"])
        candidate_raw = candidate_path.read_bytes()
        prior_raw = prior_path.read_bytes()
        _write_private(candidate_path, prior_raw, 0o600)
        _write_private(prior_path, candidate_raw, 0o600)
        candidate["staged"] = installed_file(candidate_path)
        prior["staged"] = installed_file(prior_path)

    rewrite_control_maintenance_stage(scenario, mutate)


def build_control_maintenance_smoke(
    root: Path,
    *,
    phase: str,
    swap_stage_role: str | None = None,
    mutate_prior_receipt: Callable[[dict[str, Any]], None] | None = None,
    mutate_candidate_receipt: Callable[[dict[str, Any]], None] | None = None,
) -> ControlMaintenanceSmokeScenario:
    """Build exact FD3 smoke inputs for the client contract in isolation."""

    if phase not in {"candidate-smoke", "rollback-smoke"}:
        raise ValueError("unsupported control-maintenance smoke phase")
    if swap_stage_role not in {None, "controller", "policy"}:
        raise ValueError("unsupported staged authority swap")

    fixture = ValidInvocationFixture(root)
    initial_receipt = json.loads(json.dumps(fixture.receipt))
    prior_policy_raw = fixture.policy.read_bytes()
    candidate_authority = _candidate_authority(
        fixture,
        initial_receipt,
        prior_policy_raw,
    )
    launcher_marker = root / "control-maintenance-launcher-ran"
    prior_envelope_raw = canonical_document(fixture.smoke_expected_envelope)
    fixture.replace_launcher_behavior(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "import sys\n"
        f"policy_raw = Path({str(fixture.policy)!r}).read_bytes()\n"
        f"if policy_raw == {prior_policy_raw!r}:\n"
        f"    envelope = {prior_envelope_raw!r}\n"
        f"elif policy_raw == {candidate_authority.policy_raw!r}:\n"
        f"    envelope = {candidate_authority.envelope_raw!r}\n"
        "else:\n"
        "    raise SystemExit(91)\n"
        f"Path({str(launcher_marker)!r}).write_text('ran', encoding='utf-8')\n"
        "sys.stdout.buffer.write(envelope)\n"
    )

    prior_receipt = json.loads(json.dumps(fixture.receipt))
    if mutate_prior_receipt is not None:
        prior_receipt.pop("content_sha256")
        mutate_prior_receipt(prior_receipt)
        prior_receipt = document(prior_receipt)
    prior_raw = canonical_document(prior_receipt)
    prior_sha256 = sha256(prior_raw)
    prior_retained = fixture.receipts_directory / f"sha256-{prior_sha256}.json"
    if prior_retained != fixture.retained_deployment_receipt:
        fixture.retained_deployment_receipt.unlink()
    _write_private(prior_retained, prior_raw, 0o600)
    prior_unit = _activation_unit(fixture, prior_receipt, prior_raw)

    installed_paths = {
        role: fixture.install / relative for role, relative in CONTROL_PATHS.items()
    }
    prior_control_raw = {
        role: path.read_bytes() for role, path in installed_paths.items()
    }
    candidate_control_raw = dict(prior_control_raw)
    candidate_control_raw["controller"] = (
        prior_control_raw["controller"]
        + b"\n# fixture complete-control-set candidate B\n"
    )
    candidate_control_raw["policy"] = candidate_authority.policy_raw
    candidate_control_raw["client"] = _distinct_client_source(
        prior_control_raw["client"]
    )
    prior_controls = {
        **prior_receipt["control_set"],
        "smoke-bundle-manifest": prior_receipt["smoke"]["bundle"]["manifest"],
    }
    candidate_controls = {
        role: _binding(
            installed_paths[role],
            candidate_control_raw[role],
            CONTROL_MODES[role],
        )
        for role in CONTROL_ORDER
    }

    stage_root = root / "control-maintenance-stage"
    stage_root.mkdir(mode=0o700)
    candidate_stage_paths = {
        role: stage_root / "candidate" / relative
        for role, relative in CONTROL_PATHS.items()
    }
    preimage_stage_paths = {
        role: stage_root / "preimage" / relative
        for role, relative in CONTROL_PATHS.items()
    }
    for role in CONTROL_ORDER:
        candidate_raw = candidate_control_raw[role]
        preimage_raw = prior_control_raw[role]
        if role == swap_stage_role:
            candidate_raw, preimage_raw = preimage_raw, candidate_raw
        _write_private(
            candidate_stage_paths[role],
            candidate_raw,
            0o600,
        )
        _write_private(
            preimage_stage_paths[role],
            preimage_raw,
            0o600,
        )

    candidate_active_path = stage_root / "candidate" / "active.json"
    prior_active_path = stage_root / "preimage" / "active.json"
    prior_deployment_path = stage_root / "preimage" / "deployment.json"
    for path, raw in (
        (candidate_active_path, fixture.active_raw),
        (prior_active_path, fixture.active_raw),
        (prior_deployment_path, prior_raw),
    ):
        _write_private(path, raw, 0o600)

    selector_preimage = [
        {
            "role": "active-record",
            "staged": installed_file(prior_active_path),
            "installed": prior_unit["active_record"],
        },
        {
            "role": "deployment-alias",
            "staged": installed_file(prior_deployment_path),
            "installed": prior_unit["deployment_receipt"],
        },
    ]
    control_preimage = [
        {
            "role": role,
            "staged": installed_file(preimage_stage_paths[role]),
            "installed": prior_controls[role],
        }
        for role in CONTROL_ORDER
    ]
    external_dependencies = {
        "interpreter": prior_receipt["interpreter"],
        "runtime_closure": prior_receipt["runtime_closure"],
        "process_profile": prior_receipt["process_profile"],
        "receipt_parser": {
            "deployment_receipt_contract": prior_receipt["contracts"][
                "deployment_receipt"
            ],
            "rollback_receipt_contract": prior_receipt["contracts"]["rollback_receipt"],
            "controller": prior_receipt["control_set"]["controller"],
            "client": prior_receipt["control_set"]["client"],
        },
    }
    rollback = document(
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
                "active_receipt_sha256": prior_sha256,
            },
            "prior_receipt": installed_file(prior_retained),
            "prior_activation_unit": prior_unit,
            "selector_preimage": selector_preimage,
            "control_preimage": control_preimage,
            "external_dependencies": external_dependencies,
            "smoke": prior_receipt["smoke"],
        }
    )
    rollback_raw = canonical_document(rollback)
    rollback_sha256 = sha256(rollback_raw)
    rollback_path = fixture.receipts_directory / f"sha256-{rollback_sha256}.json"
    _write_private(rollback_path, rollback_raw, 0o600)

    plan_sha256 = sha256(
        canonical_value(
            {
                "contract": "fixture-complete-control-set-plan-v1",
                "prior_receipt_sha256": prior_sha256,
                "candidate_controls": candidate_controls,
            }
        )
    )
    maintenance_sha256 = sha256(
        canonical_value(
            {
                "contract": "fixture-complete-control-set-maintenance-v1",
                "plan_sha256": plan_sha256,
            }
        )
    )
    authorization = document(
        {
            "schema_version": 1,
            "contract": "task-witness-deployer-authorization-v1",
            "purpose": "complete-control-set-maintenance",
            "canonical_root": str(fixture.install),
            "effective_uid": os.geteuid(),
            "plan_sha256": plan_sha256,
            "maintenance_transaction_sha256": maintenance_sha256,
            "candidate_controller_sha256": candidate_controls["controller"]["sha256"],
            "candidate_policy_sha256": candidate_controls["policy"]["sha256"],
            "source_selection_sha256": prior_receipt["source"][
                "source_selection_sha256"
            ],
            "source_evidence_sha256": prior_receipt["source"]["source_evidence"][
                "source_evidence_sha256"
            ],
            "expected_active_receipt_sha256": prior_sha256,
        }
    )
    authorization_raw = canonical_document(authorization)

    candidate_receipt = json.loads(json.dumps(prior_receipt))
    candidate_receipt.pop("content_sha256")
    candidate_receipt["sequence"] = prior_receipt["sequence"] + 1
    candidate_receipt["prior_receipt_sha256"] = prior_sha256
    candidate_receipt["control_set"] = {
        role: candidate_controls[role]
        for role in ("shim", "client", "launcher", "controller", "policy")
    }
    candidate_policy = json.loads(candidate_control_raw["policy"])
    candidate_receipt["compatibility_policy"] = {
        **candidate_controls["policy"],
        "content_sha256": candidate_policy["content_sha256"],
    }
    candidate_receipt["source"] = candidate_authority.source
    candidate_receipt["providers"] = candidate_authority.providers
    candidate_receipt["role_inventory"] = candidate_authority.role_inventory
    candidate_receipt["trust_context"] = {
        "path": str(candidate_authority.trust_path),
        "sha256": candidate_authority.trust_sha256,
    }
    candidate_receipt["historical_trust_contexts"] = (
        candidate_authority.historical_trust_contexts
    )
    candidate_receipt["smoke"] = candidate_authority.smoke
    candidate_receipt["authorization"] = {
        "contract": authorization["contract"],
        "purpose": authorization["purpose"],
        "sha256": sha256(authorization_raw),
        "content_sha256": authorization["content_sha256"],
        "plan_sha256": plan_sha256,
        "maintenance_transaction_sha256": maintenance_sha256,
        "expected_active_receipt_sha256": prior_sha256,
    }
    candidate_receipt["rollback"] = {
        "state": "active",
        "path": str(rollback_path),
        "sha256": rollback_sha256,
    }
    if mutate_candidate_receipt is not None:
        mutate_candidate_receipt(candidate_receipt)
    candidate_receipt = document(candidate_receipt)
    candidate_raw = canonical_document(candidate_receipt)
    candidate_sha256 = sha256(candidate_raw)
    candidate_retained = fixture.receipts_directory / f"sha256-{candidate_sha256}.json"
    _write_private(candidate_retained, candidate_raw, 0o600)
    candidate_unit = _activation_unit(fixture, candidate_receipt, candidate_raw)

    candidate_deployment_path = stage_root / "candidate" / "deployment.json"
    staged_rollback_path = stage_root / "receipts" / f"sha256-{rollback_sha256}.json"
    staged_candidate_receipt = (
        stage_root / "receipts" / f"sha256-{candidate_sha256}.json"
    )
    for path, raw in (
        (candidate_deployment_path, candidate_raw),
        (staged_rollback_path, rollback_raw),
        (staged_candidate_receipt, candidate_raw),
    ):
        _write_private(path, raw, 0o600)

    staged_additive: list[tuple[str, str, Path, Path]] = []
    for role, installed_path, raw, mode in candidate_authority.additive_artifacts:
        relative_path = installed_path.relative_to(fixture.install).as_posix()
        staged_path = stage_root / relative_path
        _write_private(staged_path, raw, mode)
        staged_additive.append((role, relative_path, staged_path, installed_path))

    artifacts: list[dict[str, object]] = []
    for role in CONTROL_ORDER:
        artifacts.append(
            {
                "role": role,
                "relative_path": f"candidate/{CONTROL_PATHS[role]}",
                "staged": installed_file(candidate_stage_paths[role]),
                "installed": candidate_controls[role],
            }
        )
        artifacts.append(
            {
                "role": f"prior-{role}",
                "relative_path": f"preimage/{CONTROL_PATHS[role]}",
                "staged": installed_file(preimage_stage_paths[role]),
                "installed": prior_controls[role],
            }
        )
    artifacts.extend(
        (
            {
                "role": "active-record",
                "relative_path": "candidate/active.json",
                "staged": installed_file(candidate_active_path),
                "installed": candidate_unit["active_record"],
            },
            {
                "role": "deployment-alias",
                "relative_path": "candidate/deployment.json",
                "staged": installed_file(candidate_deployment_path),
                "installed": candidate_unit["deployment_receipt"],
            },
            {
                "role": "prior-active-record",
                "relative_path": "preimage/active.json",
                "staged": installed_file(prior_active_path),
                "installed": prior_unit["active_record"],
            },
            {
                "role": "prior-deployment-alias",
                "relative_path": "preimage/deployment.json",
                "staged": installed_file(prior_deployment_path),
                "installed": prior_unit["deployment_receipt"],
            },
            {
                "role": "rollback-receipt",
                "relative_path": f"receipts/sha256-{rollback_sha256}.json",
                "staged": installed_file(staged_rollback_path),
                "installed": installed_file(rollback_path),
            },
            {
                "role": "deployment-receipt",
                "relative_path": f"receipts/sha256-{candidate_sha256}.json",
                "staged": installed_file(staged_candidate_receipt),
                "installed": installed_file(candidate_retained),
            },
        )
    )
    artifacts.extend(
        {
            "role": role,
            "relative_path": relative_path,
            "staged": installed_file(staged_path),
            "installed": installed_file(installed_path),
        }
        for role, relative_path, staged_path, installed_path in staged_additive
    )
    artifacts.sort(key=lambda item: str(item["relative_path"]))
    for directory in (stage_root, *stage_root.rglob("*")):
        if directory.is_dir():
            directory.chmod(0o700)
    stage = document(
        {
            "schema_version": 1,
            "contract": "task-witness-staged-deployment-v1",
            "staging_root": str(stage_root),
            "canonical_root": str(fixture.install),
            "plan_sha256": plan_sha256,
            "maintenance_transaction_sha256": maintenance_sha256,
            "classification": {
                "outcome": "authorized-control-set-maintenance",
                "reason": "exact-deployer-authorization",
            },
            "authorization": {
                "sha256": sha256(authorization_raw),
                "content_sha256": authorization["content_sha256"],
            },
            "rollback_receipt": {
                "path": str(rollback_path),
                "sha256": rollback_sha256,
            },
            "deployment_receipt": {
                "path": str(candidate_retained),
                "sha256": candidate_sha256,
            },
            "artifacts": artifacts,
        }
    )
    stage_raw = canonical_document(stage)
    stage_path = stage_root / "stage.json"
    _write_private(stage_path, stage_raw, 0o600)

    live_control_raw = (
        candidate_control_raw if phase == "candidate-smoke" else prior_control_raw
    )
    for role, path in installed_paths.items():
        _write_private(path, live_control_raw[role], CONTROL_MODES[role])
    selected_receipt = (
        candidate_receipt if phase == "candidate-smoke" else prior_receipt
    )
    selected_raw = candidate_raw if phase == "candidate-smoke" else prior_raw
    expected_envelope_raw = (
        candidate_authority.envelope_raw
        if phase == "candidate-smoke"
        else prior_envelope_raw
    )
    _write_private(fixture.deployment, selected_raw, 0o600)
    selected_unit = candidate_unit if phase == "candidate-smoke" else prior_unit

    rollback_authority = {
        "receipt_path": str(rollback_path),
        "receipt_sha256": rollback_sha256,
        "target_state": "active",
    }
    preimage_artifacts = [
        *control_preimage[:-1],
        *selector_preimage,
        control_preimage[-1],
    ]
    if tuple(item["role"] for item in preimage_artifacts) != MUTATION_ORDER:
        raise AssertionError("control-maintenance mutation order disagrees")
    preimage = {
        "manifest_path": str(rollback_path),
        "manifest_sha256": rollback_sha256,
        "artifacts": preimage_artifacts,
        "external_dependencies": external_dependencies,
    }
    stage_binding = {
        "receipt_path": str(stage_path),
        "receipt_sha256": sha256(stage_raw),
        "plan_sha256": plan_sha256,
        "authorization_sha256": sha256(authorization_raw),
        "maintenance_transaction_sha256": maintenance_sha256,
    }
    immutable_intent = {
        "contract": "task-witness-activation-intent-v1",
        "transaction_class": "control-set-maintenance",
        "canonical_root": str(fixture.install),
        "effective_uid": os.geteuid(),
        "activation_lock": activation_lock_identity(fixture.lock),
        "outer_maintenance_transaction_sha256": maintenance_sha256,
        "stage": stage_binding,
        "prior": prior_unit,
        "candidate": candidate_unit,
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
            "transaction_class": "control-set-maintenance",
            "phase": phase,
            "canonical_root": str(fixture.install),
            "effective_uid": os.geteuid(),
            "activation_lock": activation_lock_identity(fixture.lock),
            "outer_maintenance_transaction_sha256": maintenance_sha256,
            "stage": stage_binding,
            "prior": prior_unit,
            "candidate": candidate_unit,
            "rollback_authority": rollback_authority,
            "preimage": preimage,
            "pending_step": None,
            "smoke_handoff": {
                "target_deployment_receipt_sha256": selected_unit["deployment_receipt"][
                    "sha256"
                ],
                "smoke_bundle_sha256": selected_unit["smoke"]["bundle"]["sha256"],
                "smoke_trust_context_sha256": selected_unit["smoke"]["trust_context"][
                    "sha256"
                ],
            },
            "candidate_smoke_acceptance": None,
            "rollback_smoke_acceptance": None,
            "terminal_result": None,
        }
    )
    transaction_path = fixture.install / "transaction.json"
    _write_private(transaction_path, canonical_document(transaction), 0o600)
    fixture.receipt = selected_receipt
    fixture.deployment_raw = selected_raw

    return ControlMaintenanceSmokeScenario(
        fixture=fixture,
        phase=phase,
        launcher_marker=launcher_marker,
        expected_envelope_raw=expected_envelope_raw,
        stage_path=stage_path,
        stage_raw=stage_raw,
        stage=stage,
        transaction=transaction,
        prior_receipt=prior_receipt,
        candidate_receipt=candidate_receipt,
        rollback_receipt=rollback,
    )
