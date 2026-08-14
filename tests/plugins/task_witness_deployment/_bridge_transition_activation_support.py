from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._bridge_transition_stage_support import (
    AuthorizedOutboundBridgeRequest,
    OutboundBridgeStagingFixture,
)
from ._control_maintenance_activation_support import (
    assert_candidate_control_set_installed,
    assert_prior_control_set_installed,
    control_maintenance_unit_is_installed,
)
from ._routine_activation_support import (
    JOURNAL_KEYS,
    receipt_digest_inventory,
    staged_artifact,
    staged_candidate_selector_raws,
    staged_prior_selector_raws,
)
from ._routine_support import smoke_envelope
from ._support import canonical_bytes, sha256

BRIDGE_JOURNAL_KEYS = JOURNAL_KEYS | {"bridge_transition"}


@dataclass(frozen=True)
class PreparedBridgeTransitionActivation:
    authorized: AuthorizedOutboundBridgeRequest
    staged: object
    activation: object
    detached_candidate_root: Path
    detached_authority_root: Path
    starting_receipt_digests: frozenset[str]

    @property
    def initial(self) -> object:
        return self.authorized.prepared.plan.precondition


def bridge_transition_projection(authorized: AuthorizedOutboundBridgeRequest):
    authorization = json.loads(authorized.transition_authorization_raw)
    projection = {
        "execution_class": authorization["execution_class"],
        "maintenance_transaction_sha256": authorization[
            "maintenance_transaction_sha256"
        ],
        "deployment_authorization_sha256": authorization[
            "deployment_authorization_sha256"
        ],
        "transition_authorization_sha256": sha256(
            authorized.transition_authorization_raw
        ),
        "expected_active_receipt_core_sha256": authorization[
            "expected_active_receipt_core_sha256"
        ],
        "bridge_identity_sha256": authorization["bridge_identity_sha256"],
        "release_manifest_sha256": authorization["release_manifest_sha256"],
        "endpoint_projection_sha256": authorization["endpoint_projection_sha256"],
    }
    if authorization["execution_class"] == "live-migration":
        projection["prior_rehearsal"] = authorization["prior_rehearsal"]
    return projection


def assert_bridge_journal(
    journal: dict[str, Any],
    prepared: PreparedBridgeTransitionActivation,
) -> None:
    if frozenset(journal) != BRIDGE_JOURNAL_KEYS:
        raise AssertionError("bridge activation journal key set disagrees")
    if journal["transaction_class"] != "control-set-maintenance":
        raise AssertionError("bridge activation transaction class disagrees")
    projection = bridge_transition_projection(prepared.authorized)
    if journal["bridge_transition"] != projection:
        raise AssertionError("bridge activation transition projection disagrees")
    identity = {
        "contract": "task-witness-activation-intent-v1",
        "transaction_class": journal["transaction_class"],
        "canonical_root": journal["canonical_root"],
        "effective_uid": journal["effective_uid"],
        "activation_lock": journal["activation_lock"],
        "outer_maintenance_transaction_sha256": journal[
            "outer_maintenance_transaction_sha256"
        ],
        "stage": journal["stage"],
        "prior": journal["prior"],
        "candidate": journal["candidate"],
        "rollback_authority": journal["rollback_authority"],
        "preimage": journal["preimage"],
        "bridge_transition": projection,
    }
    if journal["transaction_id"] != sha256(canonical_bytes(identity)):
        raise AssertionError("bridge activation transaction identity disagrees")


def assert_bridge_migration_receipt(
    receipt_raw: bytes,
    authorized: AuthorizedOutboundBridgeRequest,
) -> None:
    receipt = json.loads(receipt_raw)
    transition = json.loads(authorized.transition_authorization_raw)
    expected = {
        "schema_version": 1,
        "contract": "task-witness-bridge-migration-projection-v1",
        "edge": {"from": "freeze5", "to": "tw4", "via": "bridge"},
        "purpose": "bridge-transition",
        **bridge_transition_projection(authorized),
    }
    if transition["execution_class"] == "live-migration":
        expected["prior_rehearsal"] = transition["prior_rehearsal"]
    if receipt.get("migration") != expected:
        raise AssertionError("bridge migration receipt projection disagrees")
    if (
        receipt["authorization"]["sha256"]
        != expected["deployment_authorization_sha256"]
    ):
        raise AssertionError("bridge ordinary authorization receipt binding disagrees")
    core = {
        key: value
        for key, value in receipt.items()
        if key not in {"migration", "content_sha256"}
    }
    if sha256(canonical_bytes(core)) != expected["expected_active_receipt_core_sha256"]:
        raise AssertionError("bridge active receipt core disagrees")


def deployment_receipt_chain(canonical_root: Path) -> tuple[tuple[int, str], ...]:
    receipts: list[tuple[int, str]] = []
    for path in (canonical_root / "receipts").iterdir():
        value = json.loads(path.read_bytes())
        if value.get("contract") in {
            "task-witness-deployment-receipt-v1",
            "task-witness-deployment-receipt-v2",
        }:
            receipts.append((value["sequence"], sha256(path.read_bytes())))
    return tuple(sorted(receipts))


def transaction_result_inventory(
    canonical_root: Path,
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (path.relative_to(canonical_root).as_posix(), sha256(path.read_bytes()))
        for path in sorted((canonical_root / "transaction-results").iterdir())
    )


class BridgeTransitionActivationFixture:
    def __init__(self, root: Path) -> None:
        self.staging = OutboundBridgeStagingFixture(root)

    def staged_activation(
        self,
        *,
        candidate_mutator: Callable[[Path], None] | None = None,
    ) -> PreparedBridgeTransitionActivation:
        authorized = self.staging.prepare_authorized(
            candidate_mutator=candidate_mutator,
        )
        outbound = authorized.outbound
        deployment = outbound.deployment
        try:
            staged = deployment.stage_bridge_transition(
                outbound.request,
                authorized.deployment_authorization_raw,
                authorized.transition_authorization_path,
                outbound.staging_root,
            )
            activation = deployment.ActivationRequest(
                deployment=outbound.request,
                authorization_raw=authorized.deployment_authorization_raw,
                stage_receipt=staged.stage_path,
            )
            detached_candidate = outbound.tw4_candidate_root.with_name(
                f"{outbound.tw4_candidate_root.name}-detached-before-activation"
            )
            outbound.tw4_candidate_root.rename(detached_candidate)
            authority_root = outbound.release_manifest_path.parent
            detached_authority = authority_root.with_name(
                f"{authority_root.name}-detached-before-activation"
            )
            authority_root.rename(detached_authority)
            return PreparedBridgeTransitionActivation(
                authorized,
                staged,
                activation,
                detached_candidate,
                detached_authority,
                receipt_digest_inventory(outbound.canonical_root),
            )
        except BaseException:
            self.staging.close(authorized)
            raise

    def close(self, prepared: PreparedBridgeTransitionActivation) -> None:
        self.staging.close(prepared.authorized)


def rebind_bridge_activation(
    module: object,
    prepared: PreparedBridgeTransitionActivation,
) -> object:
    """Rebuild one bridge activation entirely in a freshly loaded module."""

    bridge = prepared.activation.deployment
    deployment = bridge.deployment
    evidence = deployment.source_evidence
    rebound_deployment = module.DeploymentRequest(
        candidate_root=module.Path(str(deployment.candidate_root)),
        canonical_root=module.Path(str(deployment.canonical_root)),
        source_selection_raw=bytes(deployment.source_selection_raw),
        source_evidence=module.HarnessSnapshotEvidence(
            binding_raw=bytes(evidence.binding_raw),
            receipt_raw=bytes(evidence.receipt_raw),
        ),
        runtime_qualification_raw=bytes(deployment.runtime_qualification_raw),
        maintenance_transaction_sha256=str(deployment.maintenance_transaction_sha256),
        expected_active_receipt_sha256=str(deployment.expected_active_receipt_sha256),
    )
    rebound_bridge = module.BridgeTransitionRequest(
        deployment=rebound_deployment,
        release_manifest_path=module.Path(str(bridge.release_manifest_path)),
        endpoint_projection_raw=bytes(bridge.endpoint_projection_raw),
        execution_class=str(bridge.execution_class),
    )
    return module.ActivationRequest(
        deployment=rebound_bridge,
        authorization_raw=bytes(prepared.activation.authorization_raw),
        stage_receipt=module.Path(str(prepared.staged.stage_path)),
    )


class BridgeTransitionSmokeBoundary:
    """Observe candidate TW4, then optionally restored current B1 smoke."""

    def __init__(
        self,
        prepared: PreparedBridgeTransitionActivation,
        *,
        candidate_accepted: bool,
    ) -> None:
        self.prepared = prepared
        self.candidate_accepted = candidate_accepted
        self.candidate_output = smoke_envelope(
            prepared.staged.deployment_value["smoke"]
        )
        self.current_output = smoke_envelope(
            prepared.staged.rollback_value["prior_activation_unit"]["smoke"]
        )
        self.targets: list[str] = []
        self.journals: list[dict[str, Any]] = []

    def _assert_smoke_state(
        self,
        journal: dict[str, Any],
        *,
        candidate_live: bool,
    ) -> None:
        staged = self.prepared.staged
        root = self.prepared.initial.canonical_root
        deployment = json.loads(staged.deployment_raw)
        rollback = json.loads(staged.rollback_raw)
        stage = json.loads(staged.stage_raw)
        candidate_sha256 = sha256(staged.deployment_raw)
        candidate = {
            "state": "active",
            "deployment_receipt": dict(
                staged_artifact(staged, "deployment-alias").installed
            ),
            "active_record": dict(staged_artifact(staged, "active-record").installed),
            "control_set": deployment["control_set"],
            "smoke": deployment["smoke"],
        }
        control_preimage = rollback["control_preimage"]
        selector_preimage = rollback["selector_preimage"]
        expected_preimage = {
            "manifest_path": stage["rollback_receipt"]["path"],
            "manifest_sha256": sha256(staged.rollback_raw),
            "artifacts": [
                *control_preimage[:-1],
                *selector_preimage,
                control_preimage[-1],
            ],
            "external_dependencies": rollback["external_dependencies"],
        }
        if (
            journal["candidate"] != candidate
            or journal["prior"] != rollback["prior_activation_unit"]
            or journal["rollback_authority"]
            != {
                "receipt_path": stage["rollback_receipt"]["path"],
                "receipt_sha256": sha256(staged.rollback_raw),
                "target_state": "active",
            }
            or journal["preimage"] != expected_preimage
            or journal["stage"]
            != {
                "receipt_path": str(staged.stage_path),
                "receipt_sha256": sha256(staged.stage_raw),
                "plan_sha256": stage["plan_sha256"],
                "authorization_sha256": stage["authorization"]["sha256"],
                "maintenance_transaction_sha256": stage[
                    "maintenance_transaction_sha256"
                ],
            }
        ):
            raise AssertionError("bridge complete-control journal authority disagrees")
        smoke = (
            deployment["smoke"]
            if candidate_live
            else rollback["prior_activation_unit"]["smoke"]
        )
        receipt_sha256 = (
            candidate_sha256
            if candidate_live
            else rollback["prior_activation_unit"]["deployment_receipt"]["sha256"]
        )
        if journal["smoke_handoff"] != {
            "target_deployment_receipt_sha256": receipt_sha256,
            "smoke_bundle_sha256": smoke["bundle"]["sha256"],
            "smoke_trust_context_sha256": smoke["trust_context"]["sha256"],
        }:
            raise AssertionError("bridge smoke handoff disagrees")
        expected_selectors = (
            staged_candidate_selector_raws(staged)
            if candidate_live
            else staged_prior_selector_raws(staged)
        )
        if (
            (root / "active.json").read_bytes(),
            (root / "deployment.json").read_bytes(),
        ) != expected_selectors:
            raise AssertionError("bridge smoke selectors disagree")
        expected_receipts = self.prepared.starting_receipt_digests | {
            sha256(staged.rollback_raw),
            candidate_sha256,
        }
        if receipt_digest_inventory(root) != expected_receipts:
            raise AssertionError("bridge smoke receipt closure disagrees")

    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        pass_fds: tuple[int, ...],
    ) -> subprocess.CompletedProcess[bytes]:
        root = self.prepared.initial.canonical_root
        if argv != (str(root / "task-witness"), "activation-smoke"):
            raise AssertionError("bridge activation smoke argv disagrees")
        if pass_fds != (3,):
            raise AssertionError("bridge activation smoke descriptor set disagrees")
        outbound = self.prepared.authorized.outbound
        if (
            outbound.tw4_candidate_root.exists()
            or outbound.release_manifest_path.exists()
            or self.prepared.authorized.transition_authorization_path.exists()
        ):
            raise AssertionError("bridge activation reopened external source authority")

        journal = json.loads((root / "transaction.json").read_bytes())
        assert_bridge_journal(journal, self.prepared)
        candidate_live = control_maintenance_unit_is_installed(
            self.prepared.staged,
            prior=False,
        )
        current_live = control_maintenance_unit_is_installed(
            self.prepared.staged,
            prior=True,
        )
        if candidate_live == current_live:
            raise AssertionError("bridge smoke did not observe one exact control unit")
        target = "candidate" if candidate_live else "current"
        expected_phase = "candidate-smoke" if candidate_live else "rollback-smoke"
        if journal["phase"] != expected_phase:
            raise AssertionError("bridge smoke phase disagrees with installed unit")

        if candidate_live:
            assert_candidate_control_set_installed(self.prepared.staged)
        else:
            assert_prior_control_set_installed(self.prepared.staged)
        self._assert_smoke_state(journal, candidate_live=candidate_live)
        self.targets.append(target)
        self.journals.append(journal)
        accepted = self.candidate_accepted if candidate_live else True
        return subprocess.CompletedProcess(
            argv,
            0 if accepted else 70,
            self.candidate_output
            if candidate_live and accepted
            else (self.current_output if current_live else b""),
            b"" if accepted else b"fixture TW4 target rejects smoke\n",
        )
