from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest import mock

from ._activation_support import (
    PreparedActivation,
    SmokeChildBoundary,
    expected_smoke_envelope,
)
from ._routine_support import RoutineSmokeBoundary
from ._source_evidence_support import (
    SourceEvidenceFixture,
    first_install_authorization_raw,
)
from ._support import (
    canonical_document,
    content_document,
    set_agent_plugins_candidate_version,
    sha256,
)


class SourceTransitionFixture:
    """Build public active-to-active inputs for every source-evidence variant."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.chmod(0o700)
        first_root = root / "first"
        first_root.mkdir(mode=0o700)
        self.first = SourceEvidenceFixture(first_root)
        self.deployment = self.first.module

    def candidate(self, mode: str, name: str, version: str) -> Path:
        source = {
            "publisher_channel": self.first.publisher_candidate_root,
            "exact_release": self.first.exact_release_candidate_root,
        }[mode]
        candidate = self.root / name
        shutil.copytree(source, candidate)
        runtime = candidate / "runtime" / "task_witness.py"
        runtime.write_bytes(runtime.read_bytes() + f"\n# {name}\n".encode())
        set_agent_plugins_candidate_version(candidate, version)
        return candidate

    def selection_raw(
        self,
        mode: str,
        candidate_root: Path,
        *,
        revision: str,
        sequence: int | None = None,
        source_authority: str = "github-nisavid-agents",
    ) -> bytes:
        subtree_sha256 = self.deployment._snapshot_candidate_tree(
            candidate_root
        ).subtree_sha256
        details: dict[str, object]
        if mode == "publisher_channel":
            if sequence is None:
                raise AssertionError("publisher source requires a lineage sequence")
            details = {
                "channel": "stable",
                "source_trust_class": "publisher-controlled",
                "lineage": {
                    "lineage_id": "agents-stable",
                    "sequence": sequence,
                },
            }
        elif mode == "exact_release":
            details = {"source_trust_class": "operator-pinned"}
        else:
            raise AssertionError(f"unsupported source mode: {mode}")
        return canonical_document(
            content_document(
                {
                    "schema_version": 1,
                    "contract": "task-witness-source-selection-v1",
                    "mode": mode,
                    "publisher_id": "nisavid",
                    "manifest_author": {
                        "name": "Ivan D Vasin",
                        "url": "https://github.com/nisavid",
                    },
                    "repository_id": "nisavid/agents",
                    "repository_url": "https://github.com/nisavid/agents",
                    "release_version": json.loads(
                        (candidate_root / ".claude-plugin" / "plugin.json").read_bytes()
                    )["version"],
                    "revision": revision,
                    "subtree_sha256": subtree_sha256,
                    "source_authority": source_authority,
                    "details": details,
                }
            )
        )

    def evidence(self, selection_raw: bytes) -> object:
        selection = json.loads(selection_raw)
        mode = selection["mode"]
        if mode == "exact_release":
            return self.deployment.ExactReleaseEvidence()
        details = selection["details"]
        record_raw = (
            f"publisher record {selection['revision']} "
            f"{details['lineage']['sequence']}\n"
        ).encode()
        binding_raw = canonical_document(
            content_document(
                {
                    "schema_version": 1,
                    "contract": "task-witness-publisher-channel-binding-v1",
                    "resolver": "github-releases",
                    "adapter_sha256": sha256(b"exact publisher channel adapter"),
                    "publisher_record_sha256": sha256(record_raw),
                    "claims": {
                        "plugin_id": "task-witness",
                        "publisher_id": selection["publisher_id"],
                        "repository_id": selection["repository_id"],
                        "repository_url": selection["repository_url"],
                        "release_version": selection["release_version"],
                        "revision": selection["revision"],
                        "subtree_sha256": selection["subtree_sha256"],
                        "channel": details["channel"],
                        "source_trust_class": details["source_trust_class"],
                        "source_authority": selection["source_authority"],
                        "lineage": details["lineage"],
                    },
                }
            )
        )
        return self.deployment.PublisherChannelEvidence(
            binding_raw=binding_raw,
            publisher_record_raw=record_raw,
        )

    def activate_initial(
        self,
        mode: str,
        *,
        sequence: int | None = None,
    ) -> tuple[object, object, bytes, object]:
        candidate = {
            "publisher_channel": self.first.publisher_candidate_root,
            "exact_release": self.first.exact_release_candidate_root,
        }[mode]
        selection_raw = self.selection_raw(
            mode,
            candidate,
            revision="a" * 40,
            sequence=sequence,
        )
        request = self.deployment.FirstInstallRequest(
            candidate_root=candidate,
            canonical_root=self.first.canonical_root,
            source_selection_raw=selection_raw,
            source_evidence=self.evidence(selection_raw),
            runtime_qualification_raw=self.first.runtime_qualification_raw(),
            maintenance_transaction_sha256="9" * 64,
        )
        prepared = self.deployment.prepare_first_install(request)
        authorization_raw = first_install_authorization_raw(prepared)
        staged = self.deployment.stage_first_install(
            request,
            authorization_raw,
            self.root / f"first-stage-{mode}",
        )
        activation = PreparedActivation(
            request,
            authorization_raw,
            staged,
            self.deployment.verify_deployment_stage(staged.stage_path),
            self.first.canonical_root,
            self.first.canonical_root / "activation.lock",
        )
        smoke = SmokeChildBoundary(activation, expected_smoke_envelope(staged))
        with mock.patch.object(
            self.deployment,
            "_spawn_activation_smoke_child",
            smoke,
        ):
            result = self.deployment.activate_staged(
                self.deployment.ActivationRequest(
                    request,
                    authorization_raw,
                    staged.stage_path,
                )
            )
        return request, result, selection_raw, candidate

    def request(
        self,
        *,
        candidate_root: Path,
        canonical_root: Path,
        active_receipt_sha256: str,
        mode: str,
        revision: str,
        sequence: int | None = None,
        source_authority: str = "github-nisavid-agents",
    ) -> object:
        selection_raw = self.selection_raw(
            mode,
            candidate_root,
            revision=revision,
            sequence=sequence,
            source_authority=source_authority,
        )
        return self.deployment.DeploymentRequest(
            candidate_root=candidate_root,
            canonical_root=canonical_root,
            source_selection_raw=selection_raw,
            source_evidence=self.evidence(selection_raw),
            runtime_qualification_raw=self.first.runtime_qualification_raw(),
            maintenance_transaction_sha256="a" * 64,
            expected_active_receipt_sha256=active_receipt_sha256,
        )

    def authorization_raw(self, prepared: object, purpose: str) -> bytes:
        facts = prepared.authorization_facts
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
                    "candidate_controller_sha256": (facts.candidate_controller_sha256),
                    "candidate_policy_sha256": facts.candidate_policy_sha256,
                    "source_selection_sha256": facts.source_selection_sha256,
                    "source_evidence_sha256": facts.source_evidence_sha256,
                    "expected_active_receipt_sha256": (
                        facts.expected_active_receipt_sha256
                    ),
                }
            )
        )

    def activate(self, request: object, purpose: str) -> tuple[object, object]:
        prepared = self.deployment.prepare_deployment(request)
        authorization_raw = self.authorization_raw(prepared, purpose)
        staged = self.deployment.stage_deployment(
            request,
            authorization_raw,
            self.root / f"stage-{purpose}",
        )
        smoke = RoutineSmokeBoundary(
            prepared.plan.precondition.canonical_root,
            staged.deployment_value["smoke"],
            prepared.plan.precondition.receipt_value["smoke"],
            candidate_accepted=True,
            rollback_accepted=True,
        )
        with mock.patch.object(
            self.deployment,
            "_spawn_activation_smoke_child",
            smoke,
        ):
            result = self.deployment.activate_staged(
                self.deployment.ActivationRequest(
                    request,
                    authorization_raw,
                    staged.stage_path,
                )
            )
        return staged, result
