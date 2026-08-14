from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import ModuleType
from typing import Any

from . import test_receipt_staging as receipt_staging
from ._support import (
    PLUGIN,
    canonical_document,
    content_document,
    copy_agent_plugins_candidate,
    load_deployment_module,
    sha256,
)


def first_install_authorization_raw(prepared: object) -> bytes:
    facts = prepared.authorization_facts
    return canonical_document(
        content_document(
            {
                "schema_version": 1,
                "contract": "task-witness-deployer-authorization-v1",
                "purpose": "first-install",
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
            }
        )
    )


def exact_tree_state(root: Path) -> tuple[tuple[object, ...], ...]:
    """Capture write-sensitive path metadata and exact regular-file bytes."""

    paths = [root, *sorted(root.rglob("*"))] if root.exists() else []
    state: list[tuple[object, ...]] = []
    for path in paths:
        metadata = path.lstat()
        relative = "." if path == root else path.relative_to(root).as_posix()
        if path.is_symlink():
            kind = "symlink"
            payload: object = path.readlink().as_posix()
        elif path.is_dir():
            kind = "directory"
            payload = None
        else:
            kind = "file"
            payload = (len(path.read_bytes()), sha256(path.read_bytes()))
        state.append(
            (
                relative,
                kind,
                metadata.st_mode,
                metadata.st_uid,
                metadata.st_gid,
                metadata.st_nlink,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
                payload,
            )
        )
    return tuple(state)


class SourceEvidenceFixture:
    """Exact public inputs for first-install source-evidence contract tests."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.module = load_deployment_module()
        self.canonical_root = self._create_absent_canonical_root()
        self.harness_candidate_root = copy_agent_plugins_candidate(
            PLUGIN,
            root / "candidate-harness_snapshot",
        )
        (
            self.harness_selection_raw,
            self.harness_binding_raw,
            self.harness_receipt_raw,
        ) = receipt_staging.ReceiptStagingTests.task_witness_candidate_inputs(
            self,
            self.harness_candidate_root,
        )
        self.publisher_record_raw = b"opaque publisher channel record\n"
        self.publisher_candidate_root = self._candidate_for_mode("publisher_channel")
        self.publisher_selection_raw = self._selection_raw(
            "publisher_channel",
            self.publisher_candidate_root,
        )
        self.publisher_binding_raw = self._publisher_binding_raw()
        self.expected_subtree_sha256 = json.loads(self.publisher_selection_raw)[
            "subtree_sha256"
        ]
        self.exact_release_candidate_root = self._candidate_for_mode("exact_release")
        self.exact_release_selection_raw = self._selection_raw(
            "exact_release",
            self.exact_release_candidate_root,
        )

    def _create_absent_canonical_root(self) -> Path:
        account_home = self.root / "account-home"
        account_home.mkdir(mode=0o700)
        private_parent = account_home / ".local"
        private_parent.mkdir(mode=0o700)
        libexec = private_parent / "libexec"
        libexec.mkdir(mode=0o700)
        canonical_root = libexec / "task-witness"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        return canonical_root

    def _candidate_for_mode(self, mode: str) -> Path:
        candidate = self.root / f"candidate-{mode}"
        copy_agent_plugins_candidate(PLUGIN, candidate)
        policy_path = candidate / "controller" / "policy.json"
        policy = json.loads(policy_path.read_bytes())
        policy["source"]["mode"] = mode
        policy["source"]["details"] = (
            {
                "channel": "stable",
                "trust_class": "publisher-controlled",
                "lineage_id": "agents-stable",
            }
            if mode == "publisher_channel"
            else {"trust_class": "operator-pinned"}
        )
        policy.pop("content_sha256")
        policy_path.write_bytes(canonical_document(content_document(policy)))
        return candidate

    def _selection_raw(self, mode: str, candidate_root: Path) -> bytes:
        selection_raw, _, _ = (
            receipt_staging.ReceiptStagingTests.task_witness_candidate_inputs(
                self,
                candidate_root,
            )
        )
        selection = json.loads(selection_raw)
        selection.pop("content_sha256")
        if mode == "publisher_channel":
            selection["mode"] = mode
            selection["details"] = {
                "channel": "stable",
                "source_trust_class": "publisher-controlled",
                "lineage": {"lineage_id": "agents-stable", "sequence": 7},
            }
        elif mode == "exact_release":
            selection["mode"] = mode
            selection["details"] = {
                "source_trust_class": "operator-pinned",
            }
        else:
            raise AssertionError(f"unsupported fixture source mode: {mode}")
        return canonical_document(content_document(selection))

    def _publisher_binding_raw(self) -> bytes:
        selection = json.loads(self.publisher_selection_raw)
        details = selection["details"]
        return canonical_document(
            content_document(
                {
                    "schema_version": 1,
                    "contract": "task-witness-publisher-channel-binding-v1",
                    "resolver": "github-releases",
                    "adapter_sha256": sha256(b"exact publisher channel adapter"),
                    "publisher_record_sha256": sha256(self.publisher_record_raw),
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

    def runtime_qualification_raw(self) -> bytes:
        return receipt_staging.ReceiptStagingTests.runtime_qualification_raw(self)

    def deployment(self) -> ModuleType:
        return self.module

    def request(
        self,
        source_selection_raw: bytes,
        source_evidence: object,
        *,
        canonical_root: Path | None = None,
    ) -> object:
        mode = json.loads(source_selection_raw)["mode"]
        candidate_root = {
            "harness_snapshot": self.harness_candidate_root,
            "publisher_channel": self.publisher_candidate_root,
            "exact_release": self.exact_release_candidate_root,
        }[mode]
        return self.module.FirstInstallRequest(
            candidate_root=candidate_root,
            canonical_root=(
                self.canonical_root if canonical_root is None else canonical_root
            ),
            source_selection_raw=source_selection_raw,
            source_evidence=source_evidence,
            runtime_qualification_raw=self.runtime_qualification_raw(),
            maintenance_transaction_sha256="9" * 64,
        )

    def evidence_type(self, name: str) -> type[Any]:
        evidence_type = getattr(self.module, name, None)
        if evidence_type is None:
            raise AssertionError(
                f"Task Witness deployment API is missing public {name}"
            )
        return evidence_type

    def publisher_evidence(self, **changes: object) -> object:
        values: dict[str, object] = {
            "binding_raw": self.publisher_binding_raw,
            "publisher_record_raw": self.publisher_record_raw,
        }
        values.update(changes)
        return self.evidence_type("PublisherChannelEvidence")(**values)

    def harness_evidence(self, **changes: object) -> object:
        values: dict[str, object] = {
            "binding_raw": self.harness_binding_raw,
            "receipt_raw": self.harness_receipt_raw,
        }
        values.update(changes)
        return self.evidence_type("HarnessSnapshotEvidence")(**values)

    def exact_release_evidence(self) -> object:
        return self.evidence_type("ExactReleaseEvidence")()

    def trees(self) -> tuple[tuple[tuple[object, ...], ...], ...]:
        return (
            exact_tree_state(PLUGIN),
            exact_tree_state(self.publisher_candidate_root),
            exact_tree_state(self.exact_release_candidate_root),
            exact_tree_state(self.canonical_root),
        )

    def assert_trees_unchanged(
        self,
        test: Any,
        before: tuple[tuple[tuple[object, ...], ...], ...],
    ) -> None:
        test.assertEqual(self.trees(), before)


def public_request_field_names(deployment: ModuleType) -> tuple[str, ...]:
    return tuple(deployment.FirstInstallRequest.__dataclass_fields__)
