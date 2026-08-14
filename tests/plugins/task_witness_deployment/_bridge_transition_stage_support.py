from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from ._bridge_transition_prepare_support import (
    OutboundBridgePreparationFixture,
    PreparedOutboundBridgeRequest,
)
from ._support import canonical_bytes, canonical_document, content_document, sha256


@dataclass(frozen=True)
class AuthorizedOutboundBridgeRequest:
    outbound: PreparedOutboundBridgeRequest
    prepared: object
    deployment_authorization_raw: bytes
    transition_authorization_path: Path
    transition_authorization_raw: bytes


def deployment_authorization_raw(prepared: object) -> bytes:
    facts = prepared.authorization_facts
    classification = prepared.plan.classification
    purpose = (
        "source-boundary-change"
        if (
            classification.outcome == "approval-required"
            and classification.reason
            in {"downgrade", "exact-release-pin", "source-authority"}
        )
        else "complete-control-set-maintenance"
    )
    return canonical_document(
        content_document(
            {
                "schema_version": 1,
                "contract": "task-witness-deployer-authorization-v1",
                "purpose": purpose,
                "canonical_root": str(facts.canonical_root),
                "effective_uid": facts.effective_uid,
                "plan_sha256": facts.plan_sha256,
                "maintenance_transaction_sha256": (
                    facts.maintenance_transaction_sha256
                ),
                "candidate_controller_sha256": facts.candidate_controller_sha256,
                "candidate_policy_sha256": facts.candidate_policy_sha256,
                "source_selection_sha256": facts.source_selection_sha256,
                "source_evidence_sha256": facts.source_evidence_sha256,
                "expected_active_receipt_sha256": (
                    facts.expected_active_receipt_sha256
                ),
            }
        )
    )


def transition_authorization_raw(
    prepared: object,
    *,
    prior_rehearsal: dict[str, str] | None = None,
) -> bytes:
    facts = prepared.transition_authorization_facts
    unsigned: dict[str, object] = {
        "schema_version": 1,
        "contract": "task-witness-bridge-transition-authorization-v1",
        "purpose": "bridge-transition",
        "execution_class": facts.execution_class,
        "canonical_root": str(facts.canonical_root),
        "staging_root": str(facts.staging_root),
        "effective_uid": facts.effective_uid,
        "plan_sha256": facts.plan_sha256,
        "maintenance_transaction_sha256": facts.maintenance_transaction_sha256,
        "deployment_authorization_sha256": (
            facts.expected_deployment_authorization_sha256
        ),
        "expected_active_receipt_core_sha256": (
            facts.expected_active_receipt_core_sha256
        ),
        "bridge_identity_sha256": facts.bridge_identity_sha256,
        "release_manifest_sha256": facts.release_manifest_sha256,
        "endpoint_projection_sha256": facts.endpoint_projection_sha256,
    }
    if prior_rehearsal is not None:
        unsigned["prior_rehearsal"] = prior_rehearsal
    return canonical_bytes(content_document(unsigned))


class OutboundBridgeStagingFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.preparation = OutboundBridgePreparationFixture(root)

    def prepare_authorized(
        self,
        *,
        execution_class: str = "isolated-rehearsal",
        prior_rehearsal: dict[str, str] | None = None,
        candidate_mutator: Callable[[Path], None] | None = None,
    ) -> AuthorizedOutboundBridgeRequest:
        outbound = self.preparation.prepare(candidate_mutator=candidate_mutator)
        deployment = outbound.deployment
        try:
            if execution_class != "isolated-rehearsal":
                endpoint = json.loads(outbound.endpoint_projection_raw)
                endpoint.pop("content_sha256")
                endpoint["execution_class"] = execution_class
                endpoint_raw = canonical_bytes(content_document(endpoint))
                outbound.endpoint_projection_path.write_bytes(endpoint_raw)
                outbound.endpoint_projection_path.chmod(0o600)
                request = deployment.BridgeTransitionRequest(
                    deployment=outbound.request.deployment,
                    release_manifest_path=outbound.request.release_manifest_path,
                    endpoint_projection_raw=endpoint_raw,
                    execution_class=execution_class,
                )
                outbound = replace(
                    outbound,
                    request=request,
                    endpoint_projection_raw=endpoint_raw,
                )
            prepared = deployment.prepare_bridge_transition(
                outbound.request,
                outbound.staging_root,
            )
            ordinary_raw = deployment_authorization_raw(prepared)
            transition_raw = transition_authorization_raw(
                prepared,
                prior_rehearsal=prior_rehearsal,
            )
            transition_path = (
                outbound.release_manifest_path.parent
                / "bridge-transition-authorization.json"
            )
            transition_path.write_bytes(transition_raw)
            transition_path.chmod(0o600)
            return AuthorizedOutboundBridgeRequest(
                outbound=outbound,
                prepared=prepared,
                deployment_authorization_raw=ordinary_raw,
                transition_authorization_path=transition_path,
                transition_authorization_raw=transition_raw,
            )
        except BaseException:
            self.preparation.close(outbound)
            raise

    def close(self, authorized: AuthorizedOutboundBridgeRequest) -> None:
        self.preparation.close(authorized.outbound)


def transition_evidence_projection(item: object) -> tuple[object, ...]:
    return (
        item.role,
        item.relative_path,
        item.staged_path,
        item.raw,
        item.staged,
    )


def expected_transition_binding(
    *,
    staging_root: Path,
    relative_path: str,
    raw: bytes,
) -> dict[str, object]:
    return {
        "relative_path": relative_path,
        "path": str(staging_root / relative_path),
        "length": len(raw),
        "sha256": sha256(raw),
        "owner": staging_root.stat().st_uid,
        "mode": 0o600,
    }
