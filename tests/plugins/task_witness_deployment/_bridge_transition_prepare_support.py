from __future__ import annotations

import dataclasses
import json
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from ._activation_support import (
    PreparedActivation,
    SmokeChildBoundary,
    expected_smoke_envelope,
)
from ._freeze5_upgrade_recovery_support import (
    FREEZE5_COMMIT,
    Freeze5UpgradeRecoveryFixture,
    detach_candidate,
    load_controller,
    remove_loaded_controller,
)
from ._support import (
    canonical_bytes,
    content_document,
    set_agent_plugins_candidate_version,
    sha256,
)

# These values exercise monotonic source claims only. They are not release policy.
TEST_ONLY_RELEASE_VERSIONS = ("0.1.0", "0.1.1", "1.0.0")


@dataclass(frozen=True)
class PreparedOutboundBridgeRequest:
    deployment: ModuleType
    request: object
    staging_root: Path
    canonical_root: Path
    bridge_candidate_root: Path
    tw4_candidate_root: Path
    bridge_identity_path: Path
    bridge_identity_raw: bytes
    release_manifest_path: Path
    release_manifest_raw: bytes
    endpoint_projection_path: Path
    endpoint_projection_raw: bytes
    retained_receipts: tuple[dict[str, object], ...]
    retained_results: tuple[dict[str, object], ...]
    starting_active_receipt_sha256: str


def _canonical_raw(value: dict[str, Any]) -> bytes:
    return canonical_bytes(content_document(value))


def _write_private(path: Path, raw: bytes) -> None:
    path.write_bytes(raw)
    path.chmod(0o600)


def _receipt_chain(canonical_root: Path) -> tuple[dict[str, object], ...]:
    receipts: list[dict[str, object]] = []
    inventory = sorted((canonical_root / "receipts").iterdir())
    for path in inventory:
        value = json.loads(path.read_bytes())
        if value.get("contract") != "task-witness-deployment-receipt-v1":
            continue
        receipts.append(
            {
                "sequence": value["sequence"],
                "sha256": sha256(path.read_bytes()),
            }
        )
    receipts.sort(key=lambda item: int(item["sequence"]))
    if [item["sequence"] for item in receipts] != [1, 2]:
        raise AssertionError("test bridge receipt chain is incomplete")
    if len(inventory) != 2 * len(receipts):
        raise AssertionError("test bridge rollback inventory is incomplete")
    return tuple(receipts)


def _result_inventory(canonical_root: Path) -> tuple[dict[str, object], ...]:
    directory = canonical_root / "transaction-results"
    results = tuple(
        {
            "path": path.relative_to(canonical_root).as_posix(),
            "sha256": sha256(path.read_bytes()),
        }
        for path in sorted(directory.iterdir())
    )
    if len(results) != 2:
        raise AssertionError("test bridge result inventory is incomplete")
    return results


def _identity_projection(receipt: dict[str, Any], *, tree_sha1: str) -> dict[str, Any]:
    source = receipt["source"]
    controls = receipt["control_set"]
    return {
        "repository_id": source["repository_id"],
        "commit_sha1": source["revision"],
        "tree_sha1": tree_sha1,
        "plugin_subtree_sha256": source["subtree_sha256"],
        "controller_sha256": controls["controller"]["sha256"],
        "policy_sha256": controls["policy"]["sha256"],
        "client_sha256": controls["client"]["sha256"],
        "source_mode": source["mode"],
    }


def _target_for_platform(platform: dict[str, Any]) -> str:
    identity = (platform["system"], platform["machine"])
    try:
        return {
            ("darwin", "arm64"): "macos-arm64",
            ("linux", "x86_64"): "linux-x86_64",
        }[identity]
    except KeyError as error:
        raise AssertionError(
            f"unsupported test endpoint platform: {identity}"
        ) from error


class OutboundBridgePreparationFixture:
    """Build exact F5 -> provisional B1, then one public B1 -> TW4 request."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.freeze5 = Freeze5UpgradeRecoveryFixture(root)

    def prepare(
        self,
        *,
        candidate_mutator: Callable[[Path], None] | None = None,
        bridge_candidate_mutator: Callable[[Path], None] | None = None,
    ) -> PreparedOutboundBridgeRequest:
        first_hop = self.freeze5.prepared_first_hop(
            candidate_mutator=bridge_candidate_mutator,
        )
        bridge_activation = PreparedActivation(
            request=first_hop.request,
            authorization_raw=first_hop.authorization_raw,
            staged=first_hop.staged,
            verified=first_hop.deployment.verify_deployment_stage(
                first_hop.staged.stage_path
            ),
            canonical_root=first_hop.initial.canonical_root,
            activation_lock=first_hop.initial.activation_lock,
        )
        smoke = SmokeChildBoundary(
            bridge_activation,
            expected_smoke_envelope(first_hop.staged),
        )
        original_smoke = first_hop.deployment._spawn_activation_smoke_child
        first_hop.deployment._spawn_activation_smoke_child = smoke
        try:
            active_bridge = first_hop.deployment.activate_staged(first_hop.activation)
        finally:
            first_hop.deployment._spawn_activation_smoke_child = original_smoke
            remove_loaded_controller(first_hop.deployment)

        bridge_candidate_root = detach_candidate(first_hop.candidate)
        canonical_root = first_hop.initial.canonical_root
        deployment = load_controller(
            canonical_root / "controller" / "task_witness_deploy.py",
            "_task_witness_installed_bridge_prepare",
        )
        try:
            tw4_candidate_root = self.freeze5.routine.candidate_root()
            self._set_test_manifest_version(
                tw4_candidate_root,
                TEST_ONLY_RELEASE_VERSIONS[2],
            )
            if candidate_mutator is not None:
                candidate_mutator(tw4_candidate_root)
            portable_request = self.freeze5.routine.request_for_candidate(
                canonical_root,
                active_bridge.active_receipt_sha256,
                tw4_candidate_root,
                release_version=TEST_ONLY_RELEASE_VERSIONS[2],
                revision="c" * 40,
                sequence=9,
            )
            evidence = portable_request.source_evidence
            deployment_request = deployment.DeploymentRequest(
                candidate_root=deployment.Path(str(tw4_candidate_root)),
                canonical_root=deployment.Path(str(canonical_root)),
                source_selection_raw=bytes(portable_request.source_selection_raw),
                source_evidence=deployment.HarnessSnapshotEvidence(
                    binding_raw=bytes(evidence.binding_raw),
                    receipt_raw=bytes(evidence.receipt_raw),
                ),
                runtime_qualification_raw=bytes(
                    portable_request.runtime_qualification_raw
                ),
                maintenance_transaction_sha256="d" * 64,
                expected_active_receipt_sha256=(active_bridge.active_receipt_sha256),
            )

            retained_receipts = _receipt_chain(canonical_root)
            retained_results = _result_inventory(canonical_root)
            receipt_values = [
                json.loads(
                    (
                        canonical_root / "receipts" / f"sha256-{item['sha256']}.json"
                    ).read_bytes()
                )
                for item in retained_receipts
            ]
            observed_versions = tuple(
                value["source"]["release_version"] for value in receipt_values
            ) + (
                json.loads(deployment_request.source_selection_raw)["release_version"],
            )
            if observed_versions != TEST_ONLY_RELEASE_VERSIONS:
                raise AssertionError("test-only release version sequence disagrees")

            authority_root = self.root / "test-owned-bridge-authority"
            authority_root.mkdir(mode=0o700)
            bridge_history, identity_raw = self._bridge_identity(receipt_values)
            identity_path = authority_root / "bridge-identity.json"
            _write_private(identity_path, identity_raw)

            manifest_raw = self._release_manifest(
                deployment_request,
                bridge_history,
            )
            manifest_path = authority_root / "tw4-release-manifest.json"
            _write_private(manifest_path, manifest_raw)

            endpoint_raw = self._endpoint_projection(
                canonical_root,
                active_bridge.active_receipt_sha256,
                receipt_values[-1],
                retained_receipts,
                retained_results,
            )
            endpoint_path = authority_root / "isolated-endpoint-projection.json"
            _write_private(endpoint_path, endpoint_raw)

            staging_root = self.root / "tw4-bridge-stage"
            if staging_root.exists():
                raise AssertionError("prospective bridge staging root already exists")
            request = deployment.BridgeTransitionRequest(
                deployment=deployment_request,
                release_manifest_path=deployment.Path(str(manifest_path)),
                endpoint_projection_raw=endpoint_raw,
                execution_class="isolated-rehearsal",
            )
            return PreparedOutboundBridgeRequest(
                deployment=deployment,
                request=request,
                staging_root=staging_root,
                canonical_root=canonical_root,
                bridge_candidate_root=bridge_candidate_root,
                tw4_candidate_root=tw4_candidate_root,
                bridge_identity_path=identity_path,
                bridge_identity_raw=identity_raw,
                release_manifest_path=manifest_path,
                release_manifest_raw=manifest_raw,
                endpoint_projection_path=endpoint_path,
                endpoint_projection_raw=endpoint_raw,
                retained_receipts=retained_receipts,
                retained_results=retained_results,
                starting_active_receipt_sha256=(active_bridge.active_receipt_sha256),
            )
        except BaseException:
            remove_loaded_controller(deployment)
            raise

    @staticmethod
    def close(prepared: PreparedOutboundBridgeRequest) -> None:
        remove_loaded_controller(prepared.deployment)

    @staticmethod
    def _set_test_manifest_version(candidate_root: Path, version: str) -> None:
        set_agent_plugins_candidate_version(candidate_root, version)

    @staticmethod
    def _bridge_identity(
        receipts: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], bytes]:
        freeze5 = _identity_projection(receipts[0], tree_sha1="1" * 40)
        freeze5["commit_sha1"] = FREEZE5_COMMIT
        bridge = _identity_projection(receipts[1], tree_sha1="2" * 40)
        unsigned = {
            "schema_version": 1,
            "contract": "task-witness-tw4-bridge-identity-v1",
            "freeze5": freeze5,
            "bridge": bridge,
            "allowed_edges": [
                {
                    "from": "freeze5",
                    "source_mode": "harness_snapshot",
                    "to": "bridge",
                }
            ],
            "provenance_sha256": "3" * 64,
        }
        raw = _canonical_raw(unsigned)
        value = json.loads(raw)
        history = {
            "bridge_identity_sha256": sha256(raw),
            "bridge_provenance_sha256": value["provenance_sha256"],
            "freeze5": value["freeze5"],
            "bridge": value["bridge"],
        }
        return history, raw

    @staticmethod
    def _release_manifest(
        deployment_request: object,
        bridge_history: dict[str, Any],
    ) -> bytes:
        selection = json.loads(deployment_request.source_selection_raw)
        return _canonical_raw(
            {
                "schema_version": 1,
                "contract": "task-witness-tw4-release-manifest-v1",
                "qualification_candidate": {
                    "repository_id": selection["repository_id"],
                    "commit_sha1": "e" * 40,
                    "tree_sha1": "4" * 40,
                    "plugin_subtree_sha256": selection["subtree_sha256"],
                    "suite_inventory_sha256": "5" * 64,
                },
                "targets": {
                    "linux-x86_64": "6" * 64,
                    "macos-arm64": "7" * 64,
                },
                "bridge_history": bridge_history,
                "canonical_review_evidence_sha256": "8" * 64,
                "final_public_release": {
                    "commit_sha1": selection["revision"],
                    "tree_sha1": "a" * 40,
                },
                "migration_edge": {
                    "from": "freeze5",
                    "source_mode": "harness_snapshot",
                    "to": "bridge",
                    "successor": "tw4",
                },
                "promotion_delta_sha256": "b" * 64,
                "disposition": "release-qualified",
            }
        )

    @staticmethod
    def _endpoint_projection(
        canonical_root: Path,
        active_receipt_sha256: str,
        active_receipt: dict[str, Any],
        retained_receipts: tuple[dict[str, object], ...],
        retained_results: tuple[dict[str, object], ...],
    ) -> bytes:
        metadata = canonical_root.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise AssertionError("test deployment root is not a directory")
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            raise AssertionError("test deployment root is not private")
        return _canonical_raw(
            {
                "schema_version": 1,
                "contract": "task-witness-bridge-endpoint-projection-v1",
                "execution_class": "isolated-rehearsal",
                "target": _target_for_platform(active_receipt["platform"]),
                "deployment_root": str(canonical_root),
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
                "owner": os.geteuid(),
                "mode": stat.S_IMODE(metadata.st_mode),
                "starting_active_receipt_sha256": active_receipt_sha256,
                "retained_receipts": list(retained_receipts),
                "retained_results": list(retained_results),
                "platform_profile_sha256": sha256(
                    canonical_bytes(active_receipt["platform"])
                ),
                "runtime_closure_sha256": sha256(
                    canonical_bytes(active_receipt["runtime_closure"])
                ),
            }
        )


def prepared_bridge_field_surface(module: ModuleType) -> tuple[str, ...]:
    return tuple(
        field.name for field in dataclasses.fields(module.PreparedBridgeTransition)
    )


def transition_facts_field_surface(module: ModuleType) -> tuple[str, ...]:
    return tuple(
        field.name
        for field in dataclasses.fields(module.BridgeTransitionAuthorizationFacts)
    )
