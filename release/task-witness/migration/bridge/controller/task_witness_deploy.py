#!/usr/bin/env python3
"""Deployment-owned provider import and retained trust materialization."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import math
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType, ModuleType
from typing import Any

SCHEMA_VERSION = 1
PROVIDER_DECLARATION_CONTRACT = "task-witness-provider-declaration-v1"
SOURCE_SELECTION_CONTRACT = "task-witness-source-selection-v1"
SOURCE_EVIDENCE_CONTRACT = "task-witness-source-evidence-v1"
MANAGER_BINDING_CONTRACT = "task-witness-manager-binding-v1"
PUBLISHER_CHANNEL_BINDING_CONTRACT = "task-witness-publisher-channel-binding-v1"
COMPATIBILITY_POLICY_CONTRACT = "task-witness-compatibility-policy-v2"
CONTROL_SURFACE_CONTRACT = "task-witness-control-surface-v1"
DEPLOYER_AUTHORIZATION_CONTRACT = "task-witness-deployer-authorization-v1"
BRIDGE_ENDPOINT_PROJECTION_CONTRACT = "task-witness-bridge-endpoint-projection-v1"
BRIDGE_IDENTITY_CONTRACT = "task-witness-tw4-bridge-identity-v1"
BRIDGE_RELEASE_MANIFEST_CONTRACT = "task-witness-tw4-release-manifest-v1"
BRIDGE_TRANSITION_AUTHORIZATION_CONTRACT = (
    "task-witness-bridge-transition-authorization-v1"
)
BRIDGE_MIGRATION_PROJECTION_CONTRACT = "task-witness-bridge-migration-projection-v1"
DEPLOYMENT_RECEIPT_CONTRACT = "task-witness-deployment-receipt-v2"
LEGACY_DEPLOYMENT_RECEIPT_CONTRACT = "task-witness-deployment-receipt-v1"
ROLLBACK_RECEIPT_CONTRACT = "task-witness-rollback-receipt-v1"
STAGED_DEPLOYMENT_CONTRACT = "task-witness-staged-deployment-v1"
MANUAL_ROLLBACK_PLAN_CONTRACT = "task-witness-manual-rollback-plan-v1"
ACTIVATION_TRANSACTION_CONTRACT = "task-witness-activation-transaction-v1"
ACTIVATION_INTENT_CONTRACT = "task-witness-activation-intent-v1"
STAGE_MAPPING_DRIFT_ERROR = (
    "deployment staging root mapping changed; fail-stop with possible residue "
    "from same-EUID namespace mutation"
)
RUNTIME_QUALIFICATION_CONTRACT = "task-witness-runtime-qualification-v1"
ACTIVE_CONTRACT = "task-witness-launch-active-v1"
RUNTIME_CONTRACT = "task-witness-runtime-v1"
RUNTIME_ARTIFACT_MANIFEST_CONTRACT = "task-witness-runtime-artifact-manifest-v2"
PLUGIN_SUBTREE_CONTRACT = "task-witness-plugin-subtree-v1"
TRUST_CONTEXT_CONTRACT = "task-witness-trust-context-v2"
ENVELOPE_CONTRACT = "task-witness-launch-envelope-v1"
COMPLETE_ANCHOR_CONTRACT = "task-witness-complete-anchor-v1"
CANONICAL_PROJECTION_CONTRACT = "task-witness-canonical-projection-v2"
VALIDATOR_ARTIFACT_MANIFEST_CONTRACT = "task-witness-validator-artifact-manifest-v1"
PROVIDER_DECLARATION_NAME = "task-witness-provider.json"
AGENT_PLUGIN_MANIFEST_NAME = "plugin.json"
CLAUDE_MANIFEST_NAME = ".claude-plugin/plugin.json"
CODEX_MANIFEST_NAME = ".codex-plugin/plugin.json"
AGENT_PLUGINS_V1_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
SMOKE_BUNDLE_CONTRACT = "task-witness-smoke-bundle-v1"
SMOKE_PROJECTION_CONTRACT = "task-witness-smoke-projection-v1"
SMOKE_CHALLENGE = "task-witness-activation-smoke-v1"
BUNDLE_INVENTORY_CONTRACT = "task-witness-bundle-inventory-v1"
FIRST_INSTALL_ROLLBACK_CONTRACT = "task-witness-first-install-rollback-v1"
INTRINSIC_SMOKE_PROVIDER_CONTRACT = "task-witness-intrinsic-smoke-provider-v1"
SMOKE_ISSUER_CONTRACT = "task-witness-smoke-issuer-v1"
SMOKE_ISSUER_IMPLEMENTATION_CONTRACT = "task-witness-smoke-issuer-implementation-v1"
SMOKE_PRODUCER_IMPLEMENTATION_CONTRACT = "task-witness-smoke-producer-implementation-v1"
SMOKE_VALIDATOR_NAME = "task-witness-smoke-validator"
SMOKE_PRODUCER_NAME = "task-witness-smoke-producer"
SMOKE_ISSUER_NAME = "task-witness-smoke-issuer"
PINNED_SHIM_TEMPLATE = (
    b"#!/bin/sh\n"
    b"exec /usr/bin/env -i LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC "
    b"@TASK_WITNESS_PYTHON@ -B -I -S -X disable-remote-debug "
    b'@TASK_WITNESS_CLIENT@ "$@"\n'
)
MAX_JSON_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 100
MAX_JSON_NUMBER_CHARACTERS = 128
MAX_VALIDATORS = 64
MAX_VALIDATOR_MODULES = 32
MAX_MODULE_BYTES = 1024 * 1024
MAX_VALIDATOR_ARTIFACT_BYTES = 1024 * 1024
MAX_TRUST_CONTEXT_BYTES = 1024 * 1024
MAX_CANDIDATE_TREE_PATHS = 4096
MAX_CANDIDATE_TREE_FILE_BYTES = 8 * 1024 * 1024
MAX_CANDIDATE_TREE_BYTES = 64 * 1024 * 1024
MAX_INTERPRETER_BYTES = 64 * 1024 * 1024
CONTROL_SET_ROLES = ("controller", "policy", "launcher", "client", "shim")
CONTROL_PREIMAGE_ROLES = (
    "controller",
    "policy",
    "launcher",
    "client",
    "smoke-bundle-manifest",
    "shim",
)
CONTROL_MAINTENANCE_REPLACEMENT_ROLES = (
    "controller",
    "policy",
    "launcher",
    "client",
    "smoke-bundle-manifest",
    "active-record",
    "deployment-alias",
    "shim",
)
CONTROL_MAINTENANCE_ADDITIVE_ROLES = frozenset(
    {
        "runtime-bundle-io",
        "runtime-canonical",
        "runtime-entrypoint",
        "runtime-trust",
        "rollback-receipt",
        "deployment-receipt",
        "trust-context",
        "validator-module",
    }
)
TOKEN = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
AGENT_PLUGIN_NAME = re.compile(r"(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
FREEZE5_COMMIT_SHA1 = "96608a9b91d4dcf3f468a4fab1f0e008c9c32b36"
FREEZE5_CONTROLLER_SHA256 = (
    "8dc51b2a644e30d1f7c4f3b71711698b4130b43f1517e9f5361c6d1a0f7d6cfe"
)
FREEZE5_POLICY_SHA256 = (
    "23e84f210ba69ef79e02bfc3039b2c8be3b91153d7649009b3a22850f5086245"
)
FREEZE5_CLIENT_SHA256 = (
    "778186f6a460655a8b390c831e05c233171236898663ad4155bd45695597c6cf"
)
GIT_REVISION = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY_ID = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
PLATFORM_TOKEN = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}\Z")
RUNTIME_PAYLOAD_SPECS = (
    ("entrypoint", "task_witness.py"),
    ("canonical", "canonical.py"),
    ("bundle-io", "bundle_io.py"),
    ("trust", "trust.py"),
)
PROCESS_PROFILE = {
    "contract": "task-witness-process-profile-v2",
    "interpreter_flags": ["-B", "-I", "-S", "-X", "disable-remote-debug"],
    "environment": {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
    },
    "cwd": "/",
    "stdin": "closed",
    "close_fds": True,
    "new_session": True,
    "restore_signals": True,
    "umask": 0o077,
    "validation_deadline_seconds": 60,
    "accepted_output_deadline_seconds": 60,
    "termination_grace_seconds": 2,
    "kill_reap_seconds": 1,
    "post_leader_pipe_drain_seconds": 1,
    "stdout_max_bytes": 4 * 1024 * 1024,
    "stderr_max_bytes": 256 * 1024,
    "diagnostic_max_bytes": 4 * 1024,
    "diagnostic_write_seconds": 0.05,
    "io_chunk_bytes": 64 * 1024,
    "exclusive_lock_seconds": 65,
    "shared_lock_seconds": 2,
}
RECEIPT_CONTRACTS = {
    "active": ACTIVE_CONTRACT,
    "runtime": RUNTIME_CONTRACT,
    "runtime_artifact_manifest": RUNTIME_ARTIFACT_MANIFEST_CONTRACT,
    "envelope": ENVELOPE_CONTRACT,
    "anchor": COMPLETE_ANCHOR_CONTRACT,
    "canonical_projection": CANONICAL_PROJECTION_CONTRACT,
    "trust_context": TRUST_CONTEXT_CONTRACT,
    "process_profile": PROCESS_PROFILE["contract"],
    "source_selection": SOURCE_SELECTION_CONTRACT,
    "manager_binding": MANAGER_BINDING_CONTRACT,
    "compatibility_policy": COMPATIBILITY_POLICY_CONTRACT,
    "deployment_receipt": DEPLOYMENT_RECEIPT_CONTRACT,
    "rollback_receipt": ROLLBACK_RECEIPT_CONTRACT,
}
LEGACY_RECEIPT_CONTRACTS = {
    **RECEIPT_CONTRACTS,
    "deployment_receipt": LEGACY_DEPLOYMENT_RECEIPT_CONTRACT,
}
CURRENT_RECEIPT_PROFILE = "current-v2"
BRIDGE_LEGACY_RECEIPT_PROFILE = "bridge-legacy-v1"
RECEIPT_CLIENT_CONTRACTS = {
    **RECEIPT_CONTRACTS,
    "activation_intent": ACTIVATION_INTENT_CONTRACT,
    "activation_transaction": ACTIVATION_TRANSACTION_CONTRACT,
    "bundle_inventory": BUNDLE_INVENTORY_CONTRACT,
    "control_surface": CONTROL_SURFACE_CONTRACT,
    "deployer_authorization": DEPLOYER_AUTHORIZATION_CONTRACT,
    "first_install_rollback": FIRST_INSTALL_ROLLBACK_CONTRACT,
    "intrinsic_smoke_provider": INTRINSIC_SMOKE_PROVIDER_CONTRACT,
    "smoke_bundle": SMOKE_BUNDLE_CONTRACT,
    "smoke_issuer": SMOKE_ISSUER_CONTRACT,
    "smoke_issuer_implementation": SMOKE_ISSUER_IMPLEMENTATION_CONTRACT,
    "smoke_producer_implementation": SMOKE_PRODUCER_IMPLEMENTATION_CONTRACT,
    "source_evidence": SOURCE_EVIDENCE_CONTRACT,
    "staged_deployment": STAGED_DEPLOYMENT_CONTRACT,
    "validator_artifact_manifest": VALIDATOR_ARTIFACT_MANIFEST_CONTRACT,
}
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
_ACL_TYPE_EXTENDED = 0x100
_ACL_FIRST_ENTRY = 0
_ACL_NEXT_ENTRY = -1
_ACL_EXTENDED_ALLOW = 1
_ACL_EXTENDED_DENY = 2


class DeploymentError(ValueError):
    """A staged provider or retained deployment artifact is invalid."""


def _macos_descriptor_has_allow_acl(descriptor: int) -> bool:
    if sys.platform != "darwin":
        return False
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    libc.acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
    libc.acl_get_fd_np.restype = ctypes.c_void_p
    libc.acl_get_entry.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    libc.acl_get_entry.restype = ctypes.c_int
    libc.acl_get_tag_type.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
    ]
    libc.acl_get_tag_type.restype = ctypes.c_int
    libc.acl_free.argtypes = [ctypes.c_void_p]
    libc.acl_free.restype = ctypes.c_int
    ctypes.set_errno(0)
    acl = libc.acl_get_fd_np(descriptor, _ACL_TYPE_EXTENDED)
    if not acl:
        error = ctypes.get_errno()
        if error in {errno.ENOENT, getattr(errno, "ENOATTR", 93)}:
            return False
        raise OSError(error, os.strerror(error))
    try:
        selector = _ACL_FIRST_ENTRY
        while True:
            entry = ctypes.c_void_p()
            ctypes.set_errno(0)
            if libc.acl_get_entry(acl, selector, ctypes.byref(entry)) != 0:
                error = ctypes.get_errno()
                if selector == _ACL_NEXT_ENTRY and error == errno.EINVAL:
                    return False
                raise OSError(error, os.strerror(error))
            tag = ctypes.c_int()
            ctypes.set_errno(0)
            if libc.acl_get_tag_type(entry, ctypes.byref(tag)) != 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))
            if tag.value == _ACL_EXTENDED_ALLOW:
                return True
            if tag.value != _ACL_EXTENDED_DENY:
                raise OSError(errno.EINVAL, "unsupported ACL entry type")
            selector = _ACL_NEXT_ENTRY
    finally:
        libc.acl_free(acl)


def _reject_macos_allow_acl(descriptor: int, label: str) -> None:
    try:
        has_allow_acl = _macos_descriptor_has_allow_acl(descriptor)
    except OSError as error:
        raise DeploymentError(f"{label} ACL cannot be verified") from error
    if has_allow_acl:
        raise DeploymentError(f"{label} has a permissive ACL entry")


class _ActivationSmokeOwnershipError(DeploymentError):
    """The exact smoke leader is no longer owned by this controller."""


class _FrozenDict(dict):
    """A JSON-serializable dictionary with no mutation surface."""

    def _immutable(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise TypeError("frozen canonical value cannot be changed")

    __delitem__ = _immutable
    __ior__ = _immutable
    __setitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable


@dataclass(frozen=True)
class RetainedModule:
    """One exact retained validator module."""

    name: str
    storage_path: Path
    path: Path
    raw: bytes
    sha256: str


@dataclass(frozen=True)
class ProviderMaterialization:
    """One normalized provider whose modules no longer depend on source paths."""

    plugin_id: str
    publisher: str
    repository: str
    authority_profile: str
    declaration_sha256: str
    declaration_content_sha256: str
    producers: tuple[Mapping[str, Any], ...]
    issuers: tuple[Mapping[str, Any], ...]
    validators: tuple[Mapping[str, Any], ...]
    modules: tuple[RetainedModule, ...]


@dataclass(frozen=True)
class TrustContextMaterialization:
    """The canonical retained trust-context file and its exact bytes."""

    storage_path: Path
    path: Path
    raw: bytes
    sha256: str
    value: Mapping[str, Any]


@dataclass(frozen=True)
class SourceSelection:
    """One exact deployment-owned source authority projection."""

    mode: str
    publisher_id: str
    manifest_author: Mapping[str, str]
    repository_id: str
    repository_url: str
    release_version: str
    revision: str
    subtree_sha256: str
    source_authority: str
    details: Mapping[str, Any]
    content_sha256: str


@dataclass(frozen=True)
class CandidateTree:
    """One descriptor-captured and rechecked snapshot of candidate plugin bytes."""

    root: Path
    entries: tuple[Mapping[str, Any], ...]
    files: Mapping[str, bytes]
    subtree_sha256: str


@dataclass(frozen=True)
class PluginManifest:
    """One strict harness manifest with its exact byte identity."""

    harness: str
    name: str
    version: str
    author: Mapping[str, str]
    repository: str
    raw_sha256: str


@dataclass(frozen=True)
class ManagerBinding:
    """One adapter projection bound to untouched manager-receipt bytes."""

    harness: str
    manager: str
    adapter_sha256: str
    manager_receipt_sha256: str
    claims: Mapping[str, Any]
    content_sha256: str


@dataclass(frozen=True)
class PublisherChannelBinding:
    """One resolver projection bound to untouched publisher-channel bytes."""

    resolver: str
    adapter_sha256: str
    publisher_record_sha256: str
    claims: Mapping[str, Any]
    content_sha256: str


@dataclass(frozen=True)
class HarnessSnapshotEvidence:
    """Untouched harness adapter binding and manager receipt bytes."""

    binding_raw: bytes
    receipt_raw: bytes


@dataclass(frozen=True)
class PublisherChannelEvidence:
    """Untouched publisher resolver binding and channel record bytes."""

    binding_raw: bytes
    publisher_record_raw: bytes


@dataclass(frozen=True)
class ExactReleaseEvidence:
    """Explicit evidence that selection itself is the complete release pin."""


SourceEvidence = (
    HarnessSnapshotEvidence | PublisherChannelEvidence | ExactReleaseEvidence
)


@dataclass(frozen=True)
class _ValidatedSourceEvidence:
    """One mode-matched evidence variant validated before candidate traversal."""

    mode: str
    source_evidence_sha256: str
    source_trust_class: str
    channel: str | None
    lineage: Mapping[str, Any] | None
    binding_content_sha256: str | None
    binding_sha256: str | None
    record_sha256: str | None
    resolver: str | None
    adapter_sha256: str | None
    harness: ManagerBinding | None
    publisher: PublisherChannelBinding | None


@dataclass(frozen=True)
class CandidateSource:
    """The field-specific composite identity of one candidate provider."""

    plugin_id: str
    source_mode: str
    publisher_id: str
    manifest_author: Mapping[str, str]
    repository_id: str
    repository_url: str
    release_version: str
    revision: str
    subtree_sha256: str
    source_authority: str
    channel: str | None
    source_trust_class: str
    lineage: Mapping[str, Any] | None
    source_evidence_sha256: str
    source_selection_content_sha256: str
    source_selection_sha256: str
    source_binding_content_sha256: str | None
    source_binding_sha256: str | None
    source_record_sha256: str | None
    source_resolver: str | None
    source_adapter_sha256: str | None
    provider_declaration_sha256: str | None
    provider_declaration_content_sha256: str | None
    agent_plugin_manifest_sha256: str
    claude_manifest_sha256: str
    authority_profile: str | None
    provider: _ParsedProvider | None
    tree: CandidateTree


@dataclass(frozen=True)
class _BridgeLegacyCandidateSource:
    """The exact old v1 Claude/Codex source identity admitted only for F5/B1."""

    plugin_id: str
    source_mode: str
    publisher_id: str
    manifest_author: Mapping[str, str]
    repository_id: str
    repository_url: str
    release_version: str
    revision: str
    subtree_sha256: str
    source_authority: str
    channel: str | None
    source_trust_class: str
    lineage: Mapping[str, Any] | None
    source_evidence_sha256: str
    source_selection_content_sha256: str
    source_selection_sha256: str
    source_binding_content_sha256: str | None
    source_binding_sha256: str | None
    source_record_sha256: str | None
    source_resolver: str | None
    source_adapter_sha256: str | None
    provider_declaration_sha256: str | None
    provider_declaration_content_sha256: str | None
    claude_manifest_sha256: str
    codex_manifest_sha256: str
    authority_profile: str | None
    provider: _ParsedProvider | None
    tree: CandidateTree


@dataclass(frozen=True)
class CompatibilityPolicy:
    """One active allowlist of previously approved authority semantics."""

    source: Mapping[str, Any]
    providers: tuple[Mapping[str, Any], ...]
    control_surface: Mapping[str, Any]
    content_sha256: str
    raw_sha256: str


@dataclass(frozen=True)
class Classification:
    """One closed deployment-policy disposition."""

    outcome: str
    reason: str


@dataclass(frozen=True)
class FirstInstallPrecondition:
    """One read-only proof of the stable absent-to-active boundary."""

    canonical_root: Path
    root_identity: tuple[int, ...]
    activation_lock: Mapping[str, Any]
    activation_lock_identity: tuple[int, ...]
    deployment_receipt_absent: bool
    retained_result_sha256s: Mapping[str, str]


@dataclass(frozen=True)
class FirstInstallAuthorization:
    """One exact external approval for an absent-to-active transaction."""

    canonical_root: Path
    effective_uid: int
    plan_sha256: str
    maintenance_transaction_sha256: str
    candidate_controller_sha256: str
    candidate_policy_sha256: str
    source_selection_sha256: str
    source_evidence_sha256: str
    content_sha256: str


@dataclass(frozen=True)
class FirstInstallRequest:
    """Portable raw inputs for one read-only first-install preparation."""

    candidate_root: Path
    canonical_root: Path
    source_selection_raw: bytes
    source_evidence: SourceEvidence
    runtime_qualification_raw: bytes
    maintenance_transaction_sha256: str


@dataclass(frozen=True)
class DeploymentRequest:
    """Portable raw inputs for one active-to-active deployment preparation."""

    candidate_root: Path
    canonical_root: Path
    source_selection_raw: bytes
    source_evidence: SourceEvidence
    runtime_qualification_raw: bytes
    maintenance_transaction_sha256: str
    expected_active_receipt_sha256: str


@dataclass(frozen=True)
class BridgeTransitionRequest:
    """Portable authority inputs for one exact B1-to-TW4 transition."""

    deployment: DeploymentRequest
    release_manifest_path: Path
    endpoint_projection_raw: bytes
    execution_class: str


@dataclass(frozen=True)
class RollbackToRequest:
    """Portable authority for one read-only exact-target rollback plan."""

    canonical_root: Path
    expected_active_receipt_sha256: str
    target_receipt_sha256: str
    maintenance_transaction_sha256: str


@dataclass(frozen=True)
class RollbackEndpointIdentity:
    """Operator-readable identity of one retained deployment endpoint."""

    receipt_sha256: str
    sequence: int
    source: Mapping[str, Any]
    active: Mapping[str, Any]
    control_set: Mapping[str, str]
    compatibility_policy_sha256: str
    trust_context_sha256: str
    content_sha256: str


@dataclass(frozen=True)
class ManualRollbackTargetAuthority:
    """Closed retained authority for restoring one exact historical endpoint."""

    receipt_sha256: str
    receipt_value: Mapping[str, Any]
    receipt_raw: bytes
    active_raw: bytes
    activation_unit: Mapping[str, Any]
    successor_receipt_sha256: str
    successor_rollback_sha256: str
    successor_rollback_raw: bytes
    selector_raws: Mapping[str, bytes]
    control_raws: Mapping[str, bytes]
    control_replacement: bool
    authority_sha256: str


@dataclass(frozen=True)
class ManualRollbackPlan:
    """One read-only exact-target rollback plan bound to current state."""

    precondition: ActiveDeploymentPrecondition
    current: RollbackEndpointIdentity
    target: RollbackEndpointIdentity
    target_authority: ManualRollbackTargetAuthority
    classification: Classification
    maintenance_transaction_sha256: str
    plan_sha256: str
    value: Mapping[str, Any]


@dataclass(frozen=True)
class RollbackToAuthorizationFacts:
    """Exact facts an external deployer may authorize for manual rollback."""

    canonical_root: Path
    effective_uid: int
    plan_sha256: str
    maintenance_transaction_sha256: str
    expected_active_receipt_sha256: str
    target_receipt_sha256: str


@dataclass(frozen=True)
class RollbackToAuthorization:
    """One exact external approval for a manual exact-target rollback."""

    canonical_root: Path
    effective_uid: int
    plan_sha256: str
    maintenance_transaction_sha256: str
    expected_active_receipt_sha256: str
    target_receipt_sha256: str
    content_sha256: str


@dataclass(frozen=True)
class PreparedRollbackTo:
    """One read-only manual rollback plan and authorization projection."""

    plan: ManualRollbackPlan
    current: RollbackEndpointIdentity
    target: RollbackEndpointIdentity
    authorization_facts: RollbackToAuthorizationFacts


@dataclass(frozen=True)
class FirstInstallAuthorizationFacts:
    """Exact controller-derived facts that an external deployer may authorize."""

    canonical_root: Path
    effective_uid: int
    plan_sha256: str
    maintenance_transaction_sha256: str
    candidate_controller_sha256: str
    candidate_policy_sha256: str
    source_selection_sha256: str
    source_evidence_sha256: str


@dataclass(frozen=True)
class RuntimeQualification:
    """External evidence for the complete pinned CPython runtime closure."""

    platform: Mapping[str, str]
    main_executable: Mapping[str, Any]
    runtime_closure: Mapping[str, str]
    dependency_classes: tuple[str, ...]
    content_sha256: str


@dataclass(frozen=True)
class ActiveRuntime:
    """One exact active-record candidate and its immutable runtime payloads."""

    value: Mapping[str, Any]
    raw: bytes
    sha256: str
    generation: str
    runtime_implementation_sha256: str
    payloads: tuple[Mapping[str, Any], ...]
    files: Mapping[str, bytes]


@dataclass(frozen=True)
class PlannedTrust:
    """A read-only provider and context projection using final installed paths."""

    providers: tuple[ProviderMaterialization, ...]
    smoke: ProviderMaterialization
    context: TrustContextMaterialization


@dataclass(frozen=True)
class PlannedArtifact:
    """One exact byte string prescribed for a final canonical path."""

    role: str
    relative_path: str
    installed_path: Path
    raw: bytes
    sha256: str
    owner: int
    mode: int


@dataclass(frozen=True)
class DeploymentPlan:
    """A read-only first-install plan awaiting exact external authorization."""

    source: CandidateSource
    qualification: RuntimeQualification
    precondition: FirstInstallPrecondition
    classification: Classification
    candidate_policy: CompatibilityPolicy
    active: ActiveRuntime
    trust: PlannedTrust
    artifacts: tuple[PlannedArtifact, ...]
    maintenance_transaction_sha256: str
    plan_sha256: str
    value: Mapping[str, Any]


@dataclass(frozen=True)
class PreparedFirstInstall:
    """One read-only plan and its exact external-authorization projection."""

    plan: DeploymentPlan
    authorization_facts: FirstInstallAuthorizationFacts


@dataclass(frozen=True)
class ActiveDeploymentPrecondition:
    """One exact active selector and retained-receipt OCC snapshot."""

    canonical_root: Path
    root_identity: tuple[int, ...]
    activation_lock: Mapping[str, Any]
    activation_lock_identity: tuple[int, ...]
    receipt_value: Mapping[str, Any]
    receipt_raw: bytes
    receipt_sha256: str
    active_raw: bytes
    active_unit: Mapping[str, Any]
    active_policy: CompatibilityPolicy
    active_source: CandidateSource
    retained_chain: RetainedReceiptChain
    control_raws: Mapping[str, bytes]
    retained_result_raws: Mapping[str, bytes]


@dataclass(frozen=True)
class RetainedReceiptAuthorityEdge:
    """One validated successor rollback edge preserving exact prior authority."""

    successor_receipt_sha256: str
    successor_receipt_value: Mapping[str, Any]
    successor_receipt_raw: bytes
    rollback_receipt_sha256: str
    rollback_receipt_value: Mapping[str, Any]
    rollback_receipt_raw: bytes
    prior_receipt_sha256: str
    prior_receipt_value: Mapping[str, Any]
    prior_receipt_raw: bytes
    prior_active_raw: bytes
    prior_activation_unit: Mapping[str, Any]
    selector_raws: Mapping[str, bytes]
    control_raws: Mapping[str, bytes]
    authorization_purpose: str
    authorization_target_receipt_sha256: str | None


@dataclass(frozen=True)
class RetainedReceiptChain:
    """The exact current rollback authority and recursively retained receipts."""

    current_rollback_value: Mapping[str, Any]
    current_rollback_raw: bytes
    receipt_names: frozenset[str]
    deployment_receipts: tuple[tuple[str, Mapping[str, Any], bytes], ...]
    authority_edges: tuple[RetainedReceiptAuthorityEdge, ...]


@dataclass(frozen=True)
class RoutineDeploymentPlan:
    """A read-only compatible-forward plan bound to one exact active receipt."""

    source: CandidateSource
    qualification: RuntimeQualification
    precondition: ActiveDeploymentPrecondition
    classification: Classification
    candidate_policy: CompatibilityPolicy
    active: ActiveRuntime
    trust: PlannedTrust
    artifacts: tuple[PlannedArtifact, ...]
    maintenance_transaction_sha256: str
    plan_sha256: str
    prior_receipt_sha256: str
    value: Mapping[str, Any]


@dataclass(frozen=True)
class ControlSetDeploymentPlan:
    """A derived complete-control maintenance plan bound to exact active A."""

    source: CandidateSource
    qualification: RuntimeQualification
    precondition: ActiveDeploymentPrecondition
    classification: Classification
    candidate_policy: CompatibilityPolicy
    active: ActiveRuntime
    trust: PlannedTrust
    artifacts: tuple[PlannedArtifact, ...]
    maintenance_transaction_sha256: str
    plan_sha256: str
    prior_receipt_sha256: str
    value: Mapping[str, Any]


@dataclass(frozen=True)
class DeploymentAuthorizationFacts:
    """Exact facts for authorizing one derived active-prior deployment."""

    canonical_root: Path
    effective_uid: int
    plan_sha256: str
    maintenance_transaction_sha256: str
    candidate_controller_sha256: str
    candidate_policy_sha256: str
    source_selection_sha256: str
    source_evidence_sha256: str
    expected_active_receipt_sha256: str


@dataclass(frozen=True)
class DeploymentAuthorization:
    """One exact external approval for an active-prior deployment plan."""

    canonical_root: Path
    effective_uid: int
    plan_sha256: str
    maintenance_transaction_sha256: str
    candidate_controller_sha256: str
    candidate_policy_sha256: str
    source_selection_sha256: str
    source_evidence_sha256: str
    expected_active_receipt_sha256: str
    content_sha256: str


@dataclass(frozen=True)
class PreparedDeployment:
    """One derived active-prior plan and its authorization projection."""

    plan: RoutineDeploymentPlan | ControlSetDeploymentPlan
    authorization_facts: DeploymentAuthorizationFacts


@dataclass(frozen=True)
class BridgeTransitionAuthorizationFacts:
    """Exact facts an external deployer may authorize for a B1 transition."""

    canonical_root: Path
    staging_root: Path
    effective_uid: int
    plan_sha256: str
    maintenance_transaction_sha256: str
    expected_deployment_authorization_sha256: str
    expected_active_receipt_core_sha256: str
    bridge_identity_sha256: str
    release_manifest_sha256: str
    endpoint_projection_sha256: str
    execution_class: str


@dataclass(frozen=True)
class PreparedBridgeTransition:
    """One prepared B1 transition and both external-authorization projections."""

    plan: RoutineDeploymentPlan | ControlSetDeploymentPlan
    authorization_facts: DeploymentAuthorizationFacts
    transition_authorization_facts: BridgeTransitionAuthorizationFacts


@dataclass(frozen=True)
class StagedArtifact:
    """One inert staged file paired with its prescribed installed binding."""

    role: str
    relative_path: str
    staged_path: Path
    installed_path: Path
    raw: bytes
    staged: Mapping[str, Any]
    installed: Mapping[str, Any]


@dataclass(frozen=True)
class StagedTransitionEvidence:
    """One stage-only bridge evidence file with no installed binding."""

    role: str
    relative_path: str
    staged_path: Path
    raw: bytes
    staged: Mapping[str, Any]


@dataclass(frozen=True)
class StagedDeployment:
    """A complete inert stage; TW3 alone may promote or roll it back."""

    plan: (
        DeploymentPlan
        | RoutineDeploymentPlan
        | ControlSetDeploymentPlan
        | ManualRollbackPlan
    )
    authorization: (
        FirstInstallAuthorization | DeploymentAuthorization | RollbackToAuthorization
    )
    classification: Classification
    rollback_value: Mapping[str, Any]
    rollback_raw: bytes
    deployment_value: Mapping[str, Any]
    deployment_raw: bytes
    artifacts: tuple[StagedArtifact, ...]
    stage_value: Mapping[str, Any]
    stage_raw: bytes
    stage_path: Path
    transition_evidence: tuple[StagedTransitionEvidence, ...] = ()


@dataclass(frozen=True)
class VerifiedDeploymentStage:
    """A creation-disabled reread of one complete staged receipt and inventory."""

    value: Mapping[str, Any]
    raw: bytes
    path: Path
    artifacts: tuple[StagedArtifact, ...]
    transition_evidence: tuple[StagedTransitionEvidence, ...] = ()


@dataclass(frozen=True)
class _BridgeStageAuthority:
    """Validated external authority copied into one bridge-only stage."""

    facts: BridgeTransitionAuthorizationFacts
    release_manifest_raw: bytes
    transition_authorization: Mapping[str, Any]
    transition_authorization_raw: bytes


@dataclass(frozen=True)
class ActivationRequest:
    """Raw deployment inputs and one inert stage awaiting activation."""

    deployment: (
        FirstInstallRequest
        | DeploymentRequest
        | BridgeTransitionRequest
        | RollbackToRequest
    )
    authorization_raw: bytes
    stage_receipt: Path


@dataclass(frozen=True)
class RecoveryRequest:
    """Original activation authority plus one exact live journal expectation."""

    activation: ActivationRequest
    expected_journal_raw: bytes


@dataclass(frozen=True)
class Freeze5RecoveryRequest:
    """Primitive legacy authority for exact staged-F5 recovery only."""

    source_selection_raw: bytes
    manager_binding_raw: bytes
    manager_receipt_raw: bytes
    runtime_qualification_raw: bytes
    maintenance_transaction_sha256: str
    expected_active_receipt_sha256: str


@dataclass(frozen=True)
class _Freeze5RecoveryDispatch:
    """Closed exact-F5 authority captured before loading legacy code."""

    adapter: Freeze5RecoveryRequest
    stage_path: Path
    stage_raw: bytes
    stage_identity: tuple[int, ...]
    journal_path: Path | None
    journal_raw: bytes | None
    journal_identity: tuple[int, ...] | None
    controller_path: Path
    controller_raw: bytes
    controller_identity: tuple[int, ...]


@dataclass(frozen=True)
class ResultReconciliationRequest:
    """Original activation authority plus exact expected terminal journal bytes."""

    activation: ActivationRequest
    expected_terminal_journal_raw: bytes


@dataclass(frozen=True)
class TransactionResult:
    """One terminal activation result derived from the final journal generation."""

    transaction_id: str
    outcome: str
    candidate_receipt_sha256: str
    active_receipt_sha256: str | None
    accepted_envelope_sha256: str | None
    journal_sha256: str
    journal_value: Mapping[str, Any]
    journal_raw: bytes


@dataclass(frozen=True)
class _DeclaredModule:
    name: str
    relative_path: str
    components: tuple[str, ...]
    length: int
    sha256: str


@dataclass(frozen=True)
class _DeclaredValidator:
    validator_id: str
    contract: str
    implementation_sha256: str
    entrypoint: str
    modules: tuple[_DeclaredModule, ...]
    lifecycle: dict[str, Any]


@dataclass(frozen=True)
class _ParsedProvider:
    plugin_id: str
    publisher: str
    repository: str
    authority_profile: str
    content_sha256: str
    producers: tuple[dict[str, Any], ...]
    issuers: tuple[dict[str, Any], ...]
    validators: tuple[_DeclaredValidator, ...]


@dataclass(frozen=True)
class _DirectoryEdge:
    parent_fd: int
    name: str
    child_fd: int
    identity: tuple[int, ...]
    mapping_only: bool


@dataclass(frozen=True)
class _FileSnapshot:
    label: str
    directory_fds: tuple[int, ...]
    edges: tuple[_DirectoryEdge, ...]
    file_fd: int
    file_parent_fd: int
    file_name: str
    identity: tuple[int, ...]
    raw: bytes


@dataclass(frozen=True)
class _RootSnapshot:
    path: Path
    fd: int
    identity: tuple[int, ...]
    mapping_only: bool


@dataclass(frozen=True)
class _StageRoot:
    fd: int
    path: Path
    name: str
    parent: _RootSnapshot
    lexical_parent: Path
    canonical_parent: Path
    protected: tuple[tuple[_RootSnapshot, str], ...]


@dataclass(frozen=True)
class _CandidateDirectory:
    parent_fd: int
    name: str
    fd: int
    identity: tuple[int, ...]
    names: tuple[str, ...]


@dataclass(frozen=True)
class _CandidateFile:
    parent_fd: int
    name: str
    fd: int
    identity: tuple[int, ...]
    raw: bytes
    relative_path: str


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError) as error:
        raise DeploymentError("document cannot be canonically encoded") from error


def _canonical_document(value: object) -> bytes:
    return _canonical_bytes(value) + b"\n"


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        if key in result:
            raise DeploymentError(f"JSON object contains duplicate key {key!r}")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise DeploymentError(f"JSON contains unsupported number {value}")


def _integer(value: str) -> int:
    if len(value) > MAX_JSON_NUMBER_CHARACTERS:
        raise DeploymentError("JSON integer exceeds the numeric limit")
    return int(value)


def _floating(value: str) -> float:
    if len(value) > MAX_JSON_NUMBER_CHARACTERS:
        raise DeploymentError("JSON float exceeds the numeric limit")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise DeploymentError("JSON contains an unsupported number")
    return parsed


def _depth(value: object, current: int = 0) -> None:
    if current > MAX_JSON_DEPTH:
        raise DeploymentError("JSON exceeds the nesting limit")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise DeploymentError("JSON object key is invalid")
            _depth(item, current + 1)
    elif isinstance(value, list):
        for item in value:
            _depth(item, current + 1)


def _parse_strict_json(raw: bytes, label: str) -> dict[str, Any]:
    if len(raw) > MAX_JSON_BYTES:
        raise DeploymentError(f"{label} exceeds the byte limit")
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_constant=_constant,
            parse_int=_integer,
            parse_float=_floating,
        )
    except DeploymentError:
        raise
    except (RecursionError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DeploymentError(f"{label} is not strict JSON") from error
    _depth(value)
    if not isinstance(value, dict):
        raise DeploymentError(f"{label} must be a JSON object")
    return value


def _parse_canonical_json(raw: bytes, label: str) -> dict[str, Any]:
    value = _parse_strict_json(raw, label)
    if raw != _canonical_document(value):
        raise DeploymentError(f"{label} is not canonical JSON")
    return value


def _parse_bridge_canonical_json(raw: bytes, label: str) -> dict[str, Any]:
    """Parse canonical bridge evidence, whose framing has no trailing newline."""

    value = _parse_strict_json(raw, label)
    if raw != _canonical_bytes(value):
        raise DeploymentError(f"{label} is not canonical JSON")
    return value


def _exact(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise DeploymentError(f"{label} schema drift")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise DeploymentError(f"{label} must be a bounded nonempty string")
    if any(ord(character) < 0x20 for character in value):
        raise DeploymentError(f"{label} contains a control character")
    return value


def _token(value: object, label: str) -> str:
    result = _text(value, label)
    if not TOKEN.fullmatch(result):
        raise DeploymentError(f"{label} must be a closed token")
    return result


def _sha256(value: object, label: str) -> str:
    result = _text(value, label)
    if not SHA256.fullmatch(result):
        raise DeploymentError(f"{label} must be a lowercase SHA-256 digest")
    return result


def _git_revision(value: object, label: str) -> str:
    result = _text(value, label)
    if not GIT_REVISION.fullmatch(result):
        raise DeploymentError(f"{label} must be a lowercase 40-character Git revision")
    return result


def _nonnegative_integer(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise DeploymentError(f"{label} must be a nonnegative integer")
    return value


def _lifecycle(value: object, label: str) -> dict[str, Any]:
    result = _exact(value, {"state", "usable_for_new_publication"}, label)
    if result["state"] != "active" or result["usable_for_new_publication"] is not True:
        raise DeploymentError(f"{label} must declare the active provider lifecycle")
    return {
        "state": "active",
        "usable_for_new_publication": True,
    }


def _content_sha256(value: dict[str, Any], label: str) -> str:
    content_sha256 = _sha256(value["content_sha256"], f"{label}.content_sha256")
    unsigned = {key: item for key, item in value.items() if key != "content_sha256"}
    if content_sha256 != _digest(unsigned):
        raise DeploymentError(f"{label} content digest mismatch")
    return content_sha256


def _lineage(value: object, label: str) -> dict[str, Any]:
    result = _exact(value, {"lineage_id", "sequence"}, label)
    return {
        "lineage_id": _token(result["lineage_id"], f"{label}.lineage_id"),
        "sequence": _nonnegative_integer(result["sequence"], f"{label}.sequence"),
    }


def _manifest_author(value: object, label: str) -> dict[str, str]:
    author = _exact(value, {"name", "url"}, label)
    return {
        "name": _text(author["name"], f"{label}.name"),
        "url": _text(author["url"], f"{label}.url"),
    }


def _repository_id(value: object, label: str) -> str:
    result = _text(value, label)
    if REPOSITORY_ID.fullmatch(result) is None:
        raise DeploymentError(f"{label} must be an owner/repository identity")
    return result


def _platform_token(value: object, label: str) -> str:
    result = _text(value, label)
    if PLATFORM_TOKEN.fullmatch(result) is None:
        raise DeploymentError(f"{label} must be a closed platform token")
    return result


def _string_inventory(
    value: object,
    label: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise DeploymentError(f"{label} must be a bounded string list")
    if len(value) > 64:
        raise DeploymentError(f"{label} exceeds the item limit")
    result = tuple(_text(item, f"{label} item") for item in value)
    if len(result) != len(set(result)):
        raise DeploymentError(f"{label} contains duplicates")
    return result


def _parse_agent_plugin_manifests(
    agent_plugin_raw: bytes,
    claude_raw: bytes,
) -> tuple[PluginManifest, PluginManifest]:
    """Parse one exact Agent Plugins v1 manifest and its Claude projection."""

    label = "Agent Plugins v1 manifest"
    value = _parse_strict_json(agent_plugin_raw, label)
    allowed = {
        "$schema",
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
        "extensions",
    }
    if set(value) - allowed:
        raise DeploymentError(f"{label} contains an unknown field")
    if not {"$schema", "name"}.issubset(value):
        raise DeploymentError(f"{label} is missing a required field")
    if value["$schema"] != AGENT_PLUGINS_V1_SCHEMA:
        raise DeploymentError(f"{label} schema identifier disagrees")
    name = value["name"]
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= 64
        or AGENT_PLUGIN_NAME.fullmatch(name) is None
    ):
        raise DeploymentError(f"{label} name is invalid")
    for field in ("version", "description", "homepage", "repository", "license"):
        if field in value and not isinstance(value[field], str):
            raise DeploymentError(f"{label} {field} must be a string")
    author = value.get("author")
    if author is not None:
        if not isinstance(author, dict) or set(author) - {"name", "email", "url"}:
            raise DeploymentError(f"{label} author is invalid")
        if any(not isinstance(item, str) for item in author.values()):
            raise DeploymentError(f"{label} author values must be strings")
    keywords = value.get("keywords")
    if keywords is not None and (
        not isinstance(keywords, list)
        or any(not isinstance(item, str) for item in keywords)
    ):
        raise DeploymentError(f"{label} keywords are invalid")
    extensions = value.get("extensions")
    if extensions is not None and (
        not isinstance(extensions, dict)
        or any(not isinstance(item, dict) for item in extensions.values())
    ):
        raise DeploymentError(f"{label} extensions are invalid")

    if not {"version", "author", "repository", "extensions"}.issubset(value):
        raise DeploymentError(f"{label} source identity is incomplete")
    if not isinstance(author, dict) or not {"name", "url"}.issubset(author):
        raise DeploymentError(f"{label} source author is incomplete")
    if not isinstance(extensions, dict):
        raise DeploymentError(f"{label} Claude extension is unavailable")
    openai_extension = extensions.get("com.openai")
    interface = (
        openai_extension.get("interface")
        if isinstance(openai_extension, dict)
        else None
    )
    display_name = interface.get("displayName") if isinstance(interface, dict) else None
    if not isinstance(display_name, str) or not display_name:
        raise DeploymentError(f"{label} Claude display name is unavailable")

    expected_claude = {
        "name": name,
        "displayName": display_name,
        **{
            key: item
            for key, item in value.items()
            if key not in {"$schema", "name", "extensions"}
        },
    }
    claude_value = _parse_strict_json(claude_raw, "Claude plugin manifest")
    if claude_value != expected_claude:
        raise DeploymentError("candidate Claude manifest projection disagrees")

    manifest_author = _freeze(
        {
            "name": _text(author["name"], f"{label}.author.name"),
            "url": _text(author["url"], f"{label}.author.url"),
        }
    )
    plugin_id = _token(name, f"{label}.name")
    version = _text(value["version"], f"{label}.version")
    repository = _text(value["repository"], f"{label}.repository")
    return (
        PluginManifest(
            "agent-plugin",
            plugin_id,
            version,
            manifest_author,
            repository,
            hashlib.sha256(agent_plugin_raw).hexdigest(),
        ),
        PluginManifest(
            "claude",
            plugin_id,
            version,
            manifest_author,
            repository,
            hashlib.sha256(claude_raw).hexdigest(),
        ),
    )


def _parse_manager_binding(raw: bytes, manager_receipt_raw: bytes) -> ManagerBinding:
    label = "manager binding"
    if (
        type(manager_receipt_raw) is not bytes
        or not manager_receipt_raw
        or len(manager_receipt_raw) > MAX_JSON_BYTES
    ):
        raise DeploymentError("manager receipt exceeds the byte contract")
    value = _exact(
        _parse_canonical_json(raw, label),
        {
            "schema_version",
            "contract",
            "content_sha256",
            "harness",
            "manager",
            "adapter_sha256",
            "manager_receipt_sha256",
            "claims",
        },
        label,
    )
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise DeploymentError("manager binding schema version mismatch")
    if value["contract"] != MANAGER_BINDING_CONTRACT:
        raise DeploymentError("manager binding contract mismatch")
    manager_receipt_sha256 = _sha256(
        value["manager_receipt_sha256"],
        "manager binding.manager_receipt_sha256",
    )
    if manager_receipt_sha256 != hashlib.sha256(manager_receipt_raw).hexdigest():
        raise DeploymentError("manager receipt digest mismatch")
    claims = _exact(
        value["claims"],
        {
            "plugin_id",
            "release_version",
            "revision",
            "subtree_sha256",
            "channel",
            "manager_trust_class",
            "source_authority",
            "lineage",
        },
        "manager binding claims",
    )
    normalized_claims = {
        "plugin_id": _token(claims["plugin_id"], "manager binding plugin ID"),
        "release_version": _text(
            claims["release_version"],
            "manager binding release version",
        ),
        "revision": _git_revision(claims["revision"], "manager binding revision"),
        "subtree_sha256": _sha256(
            claims["subtree_sha256"],
            "manager binding subtree digest",
        ),
        "channel": _token(claims["channel"], "manager binding channel"),
        "manager_trust_class": _token(
            claims["manager_trust_class"],
            "manager binding trust class",
        ),
        "source_authority": _token(
            claims["source_authority"],
            "manager binding source authority",
        ),
        "lineage": _lineage(claims["lineage"], "manager binding lineage"),
    }
    return ManagerBinding(
        _token(value["harness"], "manager binding.harness"),
        _token(value["manager"], "manager binding.manager"),
        _sha256(value["adapter_sha256"], "manager binding.adapter_sha256"),
        manager_receipt_sha256,
        _freeze(normalized_claims),
        _content_sha256(value, label),
    )


def _parse_publisher_channel_binding(
    raw: bytes,
    publisher_record_raw: bytes,
) -> PublisherChannelBinding:
    label = "publisher channel binding"
    if (
        type(publisher_record_raw) is not bytes
        or not publisher_record_raw
        or len(publisher_record_raw) > MAX_JSON_BYTES
    ):
        raise DeploymentError(
            "publisher channel source evidence record exceeds the byte contract"
        )
    value = _exact(
        _parse_canonical_json(raw, label),
        {
            "schema_version",
            "contract",
            "content_sha256",
            "resolver",
            "adapter_sha256",
            "publisher_record_sha256",
            "claims",
        },
        label,
    )
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise DeploymentError("publisher channel binding schema version mismatch")
    if value["contract"] != PUBLISHER_CHANNEL_BINDING_CONTRACT:
        raise DeploymentError("publisher channel binding contract mismatch")
    publisher_record_sha256 = _sha256(
        value["publisher_record_sha256"],
        "publisher channel binding.publisher_record_sha256",
    )
    if publisher_record_sha256 != hashlib.sha256(publisher_record_raw).hexdigest():
        raise DeploymentError("publisher channel record digest mismatch")
    claims = _exact(
        value["claims"],
        {
            "plugin_id",
            "publisher_id",
            "repository_id",
            "repository_url",
            "release_version",
            "revision",
            "subtree_sha256",
            "channel",
            "source_trust_class",
            "source_authority",
            "lineage",
        },
        "publisher channel binding claims",
    )
    normalized_claims = {
        "plugin_id": _token(claims["plugin_id"], "publisher channel binding plugin ID"),
        "publisher_id": _token(
            claims["publisher_id"], "publisher channel binding publisher ID"
        ),
        "repository_id": _repository_id(
            claims["repository_id"], "publisher channel binding repository ID"
        ),
        "repository_url": _text(
            claims["repository_url"], "publisher channel binding repository URL"
        ),
        "release_version": _text(
            claims["release_version"], "publisher channel binding release version"
        ),
        "revision": _git_revision(
            claims["revision"], "publisher channel binding revision"
        ),
        "subtree_sha256": _sha256(
            claims["subtree_sha256"], "publisher channel binding subtree digest"
        ),
        "channel": _token(claims["channel"], "publisher channel binding channel"),
        "source_trust_class": _token(
            claims["source_trust_class"],
            "publisher channel binding trust class",
        ),
        "source_authority": _token(
            claims["source_authority"],
            "publisher channel binding source authority",
        ),
        "lineage": _lineage(claims["lineage"], "publisher channel binding lineage"),
    }
    return PublisherChannelBinding(
        _token(value["resolver"], "publisher channel binding.resolver"),
        _sha256(value["adapter_sha256"], "publisher channel binding.adapter_sha256"),
        publisher_record_sha256,
        _freeze(normalized_claims),
        _content_sha256(value, label),
    )


def _parse_source_selection(raw: bytes) -> SourceSelection:
    label = "source selection"
    value = _exact(
        _parse_canonical_json(raw, label),
        {
            "schema_version",
            "contract",
            "content_sha256",
            "mode",
            "publisher_id",
            "manifest_author",
            "repository_id",
            "repository_url",
            "release_version",
            "revision",
            "subtree_sha256",
            "source_authority",
            "details",
        },
        label,
    )
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise DeploymentError("source selection schema version mismatch")
    if value["contract"] != SOURCE_SELECTION_CONTRACT:
        raise DeploymentError("source selection contract mismatch")
    mode = _text(value["mode"], "source selection.mode")
    details = value["details"]
    if mode == "harness_snapshot":
        details = _exact(
            details,
            {
                "harness",
                "manager",
                "channel",
                "manager_trust_class",
                "manager_receipt_sha256",
                "lineage",
            },
            "source selection harness_snapshot details",
        )
        normalized_details = {
            "harness": _token(details["harness"], "source selection harness"),
            "manager": _token(details["manager"], "source selection manager"),
            "channel": _token(details["channel"], "source selection channel"),
            "manager_trust_class": _token(
                details["manager_trust_class"],
                "source selection manager trust class",
            ),
            "manager_receipt_sha256": _sha256(
                details["manager_receipt_sha256"],
                "source selection manager receipt",
            ),
            "lineage": _lineage(details["lineage"], "source selection lineage"),
        }
    elif mode == "publisher_channel":
        details = _exact(
            details,
            {"channel", "source_trust_class", "lineage"},
            "source selection publisher_channel details",
        )
        normalized_details = {
            "channel": _token(details["channel"], "source selection channel"),
            "source_trust_class": _token(
                details["source_trust_class"],
                "source selection source trust class",
            ),
            "lineage": _lineage(details["lineage"], "source selection lineage"),
        }
    elif mode == "exact_release":
        details = _exact(
            details,
            {"source_trust_class"},
            "source selection exact_release details",
        )
        normalized_details = {
            "source_trust_class": _token(
                details["source_trust_class"],
                "source selection source trust class",
            )
        }
    else:
        raise DeploymentError("source selection mode is unsupported")
    return SourceSelection(
        mode,
        _token(value["publisher_id"], "source selection.publisher_id"),
        _freeze(
            _manifest_author(
                value["manifest_author"],
                "source selection.manifest_author",
            )
        ),
        _repository_id(
            value["repository_id"],
            "source selection.repository_id",
        ),
        _text(value["repository_url"], "source selection.repository_url"),
        _text(value["release_version"], "source selection.release_version"),
        _git_revision(value["revision"], "source selection.revision"),
        _sha256(value["subtree_sha256"], "source selection.subtree_sha256"),
        _token(value["source_authority"], "source selection.source_authority"),
        _freeze(normalized_details),
        _content_sha256(value, label),
    )


def _validate_source_evidence(
    selection: SourceSelection,
    evidence: object,
) -> _ValidatedSourceEvidence:
    """Validate a closed mode-specific evidence variant before source traversal."""

    expected_type = {
        "harness_snapshot": HarnessSnapshotEvidence,
        "publisher_channel": PublisherChannelEvidence,
        "exact_release": ExactReleaseEvidence,
    }[selection.mode]
    if type(evidence) is not expected_type:
        raise DeploymentError(
            "source evidence variant does not match source selection mode"
        )
    details = selection.details
    if type(evidence) is HarnessSnapshotEvidence:
        if (
            type(evidence.binding_raw) is not bytes
            or type(evidence.receipt_raw) is not bytes
        ):
            raise DeploymentError("source evidence fields must be exact bytes")
        binding = _parse_manager_binding(evidence.binding_raw, evidence.receipt_raw)
        claims = binding.claims
        if (
            binding.harness != details["harness"]
            or binding.manager != details["manager"]
            or binding.manager_receipt_sha256 != details["manager_receipt_sha256"]
            or claims["release_version"] != selection.release_version
            or claims["revision"] != selection.revision
            or claims["subtree_sha256"] != selection.subtree_sha256
            or claims["channel"] != details["channel"]
            or claims["manager_trust_class"] != details["manager_trust_class"]
            or claims["source_authority"] != selection.source_authority
            or claims["lineage"] != details["lineage"]
        ):
            raise DeploymentError("harness source evidence cross-binding disagrees")
        binding_sha256 = hashlib.sha256(evidence.binding_raw).hexdigest()
        record_sha256 = hashlib.sha256(evidence.receipt_raw).hexdigest()
        return _ValidatedSourceEvidence(
            selection.mode,
            _digest(
                {
                    "contract": SOURCE_EVIDENCE_CONTRACT,
                    "mode": selection.mode,
                    "binding_sha256": binding_sha256,
                    "record_sha256": record_sha256,
                }
            ),
            details["manager_trust_class"],
            details["channel"],
            details["lineage"],
            binding.content_sha256,
            binding_sha256,
            record_sha256,
            None,
            binding.adapter_sha256,
            binding,
            None,
        )
    if type(evidence) is PublisherChannelEvidence:
        if (
            type(evidence.binding_raw) is not bytes
            or type(evidence.publisher_record_raw) is not bytes
        ):
            raise DeploymentError("source evidence fields must be exact bytes")
        binding = _parse_publisher_channel_binding(
            evidence.binding_raw,
            evidence.publisher_record_raw,
        )
        claims = binding.claims
        expected_claims = {
            "plugin_id": "task-witness",
            "publisher_id": selection.publisher_id,
            "repository_id": selection.repository_id,
            "repository_url": selection.repository_url,
            "release_version": selection.release_version,
            "revision": selection.revision,
            "subtree_sha256": selection.subtree_sha256,
            "channel": details["channel"],
            "source_trust_class": details["source_trust_class"],
            "source_authority": selection.source_authority,
            "lineage": details["lineage"],
        }
        if _thaw(claims) != expected_claims:
            raise DeploymentError("publisher channel source evidence claims disagree")
        binding_sha256 = hashlib.sha256(evidence.binding_raw).hexdigest()
        record_sha256 = hashlib.sha256(evidence.publisher_record_raw).hexdigest()
        return _ValidatedSourceEvidence(
            selection.mode,
            _digest(
                {
                    "contract": SOURCE_EVIDENCE_CONTRACT,
                    "mode": selection.mode,
                    "binding_sha256": binding_sha256,
                    "record_sha256": record_sha256,
                }
            ),
            details["source_trust_class"],
            details["channel"],
            details["lineage"],
            binding.content_sha256,
            binding_sha256,
            record_sha256,
            binding.resolver,
            binding.adapter_sha256,
            None,
            binding,
        )
    return _ValidatedSourceEvidence(
        selection.mode,
        _digest({"contract": SOURCE_EVIDENCE_CONTRACT, "mode": selection.mode}),
        details["source_trust_class"],
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )


def _policy_lifecycle(value: dict[str, Any], label: str) -> dict[str, Any]:
    if value["state"] != "active" or value["usable_for_new_publication"] is not True:
        raise DeploymentError(f"{label} lifecycle is unsupported")
    return {
        "state": "active",
        "usable_for_new_publication": True,
    }


def _parse_policy_role(
    value: object,
    category: str,
    index: int,
) -> dict[str, Any]:
    label = f"compatibility policy {category}[{index}]"
    lifecycle = {"state", "usable_for_new_publication"}
    if category == "producers":
        role = _exact(
            value,
            {
                "producer_id",
                "contract",
                "validator_id",
                "validator_contract",
            }
            | lifecycle,
            label,
        )
        return {
            "producer_id": _token(role["producer_id"], f"{label}.producer_id"),
            "contract": _text(role["contract"], f"{label}.contract"),
            "validator_id": _token(role["validator_id"], f"{label}.validator_id"),
            "validator_contract": _text(
                role["validator_contract"],
                f"{label}.validator_contract",
            ),
            **_policy_lifecycle(role, label),
        }
    if category == "issuers":
        role = _exact(
            value,
            {"issuer_id", "contract", "capabilities"} | lifecycle,
            label,
        )
        capabilities = _string_inventory(
            role["capabilities"],
            f"{label}.capabilities",
        )
        if capabilities != tuple(sorted(capabilities)):
            raise DeploymentError(f"{label}.capabilities must be sorted")
        return {
            "issuer_id": _token(role["issuer_id"], f"{label}.issuer_id"),
            "contract": _text(role["contract"], f"{label}.contract"),
            "capabilities": list(capabilities),
            **_policy_lifecycle(role, label),
        }
    if category == "validators":
        role = _exact(
            value,
            {"validator_id", "contract"} | lifecycle,
            label,
        )
        return {
            "validator_id": _token(role["validator_id"], f"{label}.validator_id"),
            "contract": _text(role["contract"], f"{label}.contract"),
            **_policy_lifecycle(role, label),
        }
    raise DeploymentError("compatibility policy role category is unsupported")


def _parse_policy_provider(value: object, index: int) -> dict[str, Any]:
    label = f"compatibility policy providers[{index}]"
    provider = _exact(
        value,
        {
            "plugin_id",
            "authority_profile",
            "producers",
            "issuers",
            "validators",
        },
        label,
    )
    result: dict[str, Any] = {
        "plugin_id": _token(provider["plugin_id"], f"{label}.plugin_id"),
        "authority_profile": _token(
            provider["authority_profile"],
            f"{label}.authority_profile",
        ),
    }
    role_keys = {
        "producers": lambda item: (
            item["producer_id"],
            item["contract"],
            item["validator_id"],
            item["validator_contract"],
        ),
        "issuers": lambda item: (item["issuer_id"], item["contract"]),
        "validators": lambda item: (item["validator_id"], item["contract"]),
    }
    for category, key in role_keys.items():
        values = provider[category]
        if not isinstance(values, list) or len(values) > MAX_VALIDATORS:
            raise DeploymentError(f"{label}.{category} inventory is invalid")
        normalized = [
            _parse_policy_role(item, category, role_index)
            for role_index, item in enumerate(values)
        ]
        _require_sorted_unique(normalized, key, f"{label}.{category}")
        result[category] = normalized
    return result


def _parse_control_surface(value: object) -> Mapping[str, Any]:
    label = "compatibility policy control surface"
    surface = _exact(
        value,
        {"schema_version", "contract", "process_profile", "contracts"},
        label,
    )
    if type(surface["schema_version"]) is not int or surface["schema_version"] != 1:
        raise DeploymentError(f"{label} schema version mismatch")
    if surface["contract"] != CONTROL_SURFACE_CONTRACT:
        raise DeploymentError(f"{label} contract mismatch")
    process_profile = _exact(
        surface["process_profile"],
        set(PROCESS_PROFILE),
        f"{label} process profile",
    )
    contracts = _exact(
        surface["contracts"],
        set(RECEIPT_CLIENT_CONTRACTS),
        f"{label} contracts",
    )
    if _canonical_bytes(process_profile) != _canonical_bytes(
        PROCESS_PROFILE
    ) or _canonical_bytes(contracts) != _canonical_bytes(RECEIPT_CLIENT_CONTRACTS):
        raise DeploymentError(f"{label} is unsupported")
    return _freeze(
        {
            "schema_version": 1,
            "contract": CONTROL_SURFACE_CONTRACT,
            "process_profile": _thaw(process_profile),
            "contracts": _thaw(contracts),
        }
    )


def _parse_compatibility_policy(raw: bytes) -> CompatibilityPolicy:
    label = "compatibility policy"
    value = _exact(
        _parse_canonical_json(raw, label),
        {
            "schema_version",
            "contract",
            "source",
            "providers",
            "control_surface",
            "content_sha256",
        },
        label,
    )
    if type(value["schema_version"]) is not int or value["schema_version"] != 2:
        raise DeploymentError("compatibility policy schema version mismatch")
    if value["contract"] != COMPATIBILITY_POLICY_CONTRACT:
        raise DeploymentError("compatibility policy contract mismatch")
    source = _exact(
        value["source"],
        {
            "plugin_id",
            "mode",
            "publisher_id",
            "manifest_author",
            "repository_id",
            "repository_url",
            "source_authority",
            "details",
        },
        "compatibility policy source",
    )
    mode = _text(source["mode"], "compatibility policy source.mode")
    if mode in {"harness_snapshot", "publisher_channel"}:
        details = _exact(
            source["details"],
            {"channel", "trust_class", "lineage_id"},
            "compatibility policy source details",
        )
        normalized_details = {
            "channel": _token(details["channel"], "compatibility policy channel"),
            "trust_class": _token(
                details["trust_class"],
                "compatibility policy trust class",
            ),
            "lineage_id": _token(
                details["lineage_id"],
                "compatibility policy lineage ID",
            ),
        }
    elif mode == "exact_release":
        details = _exact(
            source["details"],
            {"trust_class"},
            "compatibility policy exact-release details",
        )
        normalized_details = {
            "trust_class": _token(
                details["trust_class"],
                "compatibility policy trust class",
            ),
        }
    else:
        raise DeploymentError("compatibility policy source mode is unsupported")
    normalized_source = {
        "plugin_id": _token(source["plugin_id"], "compatibility policy plugin ID"),
        "mode": mode,
        "publisher_id": _token(
            source["publisher_id"],
            "compatibility policy publisher ID",
        ),
        "manifest_author": _manifest_author(
            source["manifest_author"],
            "compatibility policy manifest author",
        ),
        "repository_id": _repository_id(
            source["repository_id"],
            "compatibility policy repository ID",
        ),
        "repository_url": _text(
            source["repository_url"],
            "compatibility policy repository URL",
        ),
        "source_authority": _token(
            source["source_authority"],
            "compatibility policy source authority",
        ),
        "details": normalized_details,
    }
    providers = value["providers"]
    if not isinstance(providers, list) or len(providers) > MAX_VALIDATORS:
        raise DeploymentError("compatibility policy provider inventory is invalid")
    normalized_providers = [
        _parse_policy_provider(item, index) for index, item in enumerate(providers)
    ]
    _require_sorted_unique(
        normalized_providers,
        lambda item: item["plugin_id"],
        "compatibility policy providers",
    )
    return CompatibilityPolicy(
        _freeze(normalized_source),
        tuple(_freeze(item) for item in normalized_providers),
        _parse_control_surface(value["control_surface"]),
        _content_sha256(value, label),
        hashlib.sha256(raw).hexdigest(),
    )


def _parse_bridge_legacy_compatibility_policy(raw: bytes) -> CompatibilityPolicy:
    """Parse only the immutable F5 policy admitted by the B1 bridge."""

    label = "bridge legacy compatibility policy"
    if hashlib.sha256(raw).hexdigest() != FREEZE5_POLICY_SHA256:
        raise DeploymentError(f"{label} identity disagrees")
    value = _exact(
        _parse_canonical_json(raw, label),
        {
            "schema_version",
            "contract",
            "source",
            "providers",
            "control_surface",
            "content_sha256",
        },
        label,
    )
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 2
        or value["contract"] != COMPATIBILITY_POLICY_CONTRACT
        or value["providers"] != []
    ):
        raise DeploymentError(f"{label} contract disagrees")
    source = _exact(
        value["source"],
        {
            "plugin_id",
            "mode",
            "publisher_id",
            "manifest_author",
            "repository_id",
            "repository_url",
            "source_authority",
            "details",
        },
        f"{label} source",
    )
    details = _exact(
        source["details"],
        {"channel", "trust_class", "lineage_id"},
        f"{label} source details",
    )
    normalized_source = {
        "plugin_id": _token(source["plugin_id"], f"{label} plugin ID"),
        "mode": _text(source["mode"], f"{label} mode"),
        "publisher_id": _token(source["publisher_id"], f"{label} publisher ID"),
        "manifest_author": _manifest_author(
            source["manifest_author"],
            f"{label} manifest author",
        ),
        "repository_id": _repository_id(
            source["repository_id"],
            f"{label} repository ID",
        ),
        "repository_url": _text(
            source["repository_url"],
            f"{label} repository URL",
        ),
        "source_authority": _token(
            source["source_authority"],
            f"{label} source authority",
        ),
        "details": {
            "channel": _token(details["channel"], f"{label} channel"),
            "trust_class": _token(
                details["trust_class"],
                f"{label} trust class",
            ),
            "lineage_id": _token(
                details["lineage_id"],
                f"{label} lineage ID",
            ),
        },
    }
    control_surface = _exact(
        value["control_surface"],
        {"schema_version", "contract", "process_profile", "contracts"},
        f"{label} control surface",
    )
    contracts = control_surface["contracts"]
    if (
        normalized_source["mode"] != "harness_snapshot"
        or type(control_surface["schema_version"]) is not int
        or control_surface["schema_version"] != 1
        or control_surface["contract"] != CONTROL_SURFACE_CONTRACT
        or control_surface["process_profile"] != PROCESS_PROFILE
        or not isinstance(contracts, dict)
        or any(key not in contracts for key in RECEIPT_CONTRACTS)
    ):
        raise DeploymentError(f"{label} authority disagrees")
    return CompatibilityPolicy(
        _freeze(normalized_source),
        (),
        _freeze(control_surface),
        _content_sha256(value, label),
        hashlib.sha256(raw).hexdigest(),
    )


def _validate_first_install_authorization(
    raw: bytes,
    *,
    canonical_root: str,
    effective_uid: int,
    plan_sha256: str,
    maintenance_transaction_sha256: str,
    candidate_controller_sha256: str,
    candidate_policy_sha256: str,
    source_selection_sha256: str,
    source_evidence_sha256: str,
) -> FirstInstallAuthorization:
    label = "first-install authorization"
    value = _exact(
        _parse_canonical_json(raw, label),
        {
            "schema_version",
            "contract",
            "content_sha256",
            "purpose",
            "canonical_root",
            "effective_uid",
            "plan_sha256",
            "maintenance_transaction_sha256",
            "candidate_controller_sha256",
            "candidate_policy_sha256",
            "source_selection_sha256",
            "source_evidence_sha256",
        },
        label,
    )
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise DeploymentError("first-install authorization schema version mismatch")
    if value["contract"] != DEPLOYER_AUTHORIZATION_CONTRACT:
        raise DeploymentError("first-install authorization contract mismatch")
    if value["purpose"] != "first-install":
        raise DeploymentError("first-install authorization purpose mismatch")
    authorized_root = _normalized_absolute_path(
        Path(_text(value["canonical_root"], f"{label}.canonical_root")),
        f"{label}.canonical_root",
    )
    authorized_uid = _nonnegative_integer(
        value["effective_uid"],
        f"{label}.effective_uid",
    )
    digests = {
        key: _sha256(value[key], f"{label}.{key}")
        for key in (
            "plan_sha256",
            "maintenance_transaction_sha256",
            "candidate_controller_sha256",
            "candidate_policy_sha256",
            "source_selection_sha256",
            "source_evidence_sha256",
        )
    }
    expected_digests = {
        "plan_sha256": _sha256(plan_sha256, "expected plan digest"),
        "maintenance_transaction_sha256": _sha256(
            maintenance_transaction_sha256,
            "expected maintenance transaction digest",
        ),
        "candidate_controller_sha256": _sha256(
            candidate_controller_sha256,
            "expected candidate controller digest",
        ),
        "candidate_policy_sha256": _sha256(
            candidate_policy_sha256,
            "expected candidate policy digest",
        ),
        "source_selection_sha256": _sha256(
            source_selection_sha256,
            "expected source-selection digest",
        ),
        "source_evidence_sha256": _sha256(
            source_evidence_sha256,
            "expected source-evidence digest",
        ),
    }
    expected_root = _normalized_absolute_path(
        Path(canonical_root),
        "expected canonical root",
    )
    if (
        authorized_root != expected_root
        or authorized_uid != effective_uid
        or digests != expected_digests
    ):
        raise DeploymentError("first-install authorization facts disagree")
    return FirstInstallAuthorization(
        authorized_root,
        authorized_uid,
        digests["plan_sha256"],
        digests["maintenance_transaction_sha256"],
        digests["candidate_controller_sha256"],
        digests["candidate_policy_sha256"],
        digests["source_selection_sha256"],
        digests["source_evidence_sha256"],
        _content_sha256(value, label),
    )


def _validate_deployment_authorization(
    raw: bytes,
    facts: DeploymentAuthorizationFacts,
    *,
    expected_purpose: str = "routine-compatible-forward",
) -> DeploymentAuthorization:
    labels = {
        "routine-compatible-forward": "routine deployment authorization",
        "source-boundary-change": "source-boundary deployment authorization",
        "complete-control-set-maintenance": ("control-set maintenance authorization"),
    }
    try:
        label = labels[expected_purpose]
    except KeyError:
        raise DeploymentError(
            "active-prior authorization purpose is unsupported"
        ) from None
    value = _exact(
        _parse_canonical_json(raw, label),
        {
            "schema_version",
            "contract",
            "purpose",
            "canonical_root",
            "effective_uid",
            "plan_sha256",
            "maintenance_transaction_sha256",
            "candidate_controller_sha256",
            "candidate_policy_sha256",
            "source_selection_sha256",
            "source_evidence_sha256",
            "expected_active_receipt_sha256",
            "content_sha256",
        },
        label,
    )
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["contract"] != DEPLOYER_AUTHORIZATION_CONTRACT
        or value["purpose"] != expected_purpose
    ):
        raise DeploymentError(f"{label} contract mismatch")
    root = _normalized_absolute_path(
        Path(_text(value["canonical_root"], f"{label}.canonical_root")),
        f"{label}.canonical_root",
    )
    effective_uid = _nonnegative_integer(
        value["effective_uid"],
        f"{label}.effective_uid",
    )
    names = (
        "plan_sha256",
        "maintenance_transaction_sha256",
        "candidate_controller_sha256",
        "candidate_policy_sha256",
        "source_selection_sha256",
        "source_evidence_sha256",
        "expected_active_receipt_sha256",
    )
    digests = {name: _sha256(value[name], f"{label}.{name}") for name in names}
    if (
        root != facts.canonical_root
        or effective_uid != facts.effective_uid
        or any(digests[name] != getattr(facts, name) for name in names)
    ):
        raise DeploymentError(f"{label} facts disagree")
    return DeploymentAuthorization(
        root,
        effective_uid,
        *(digests[name] for name in names),
        _content_sha256(value, label),
    )


def _validate_rollback_to_authorization(
    raw: bytes,
    facts: RollbackToAuthorizationFacts,
) -> RollbackToAuthorization:
    label = "manual exact-target rollback authorization"
    value = _exact(
        _parse_canonical_json(raw, label),
        {
            "schema_version",
            "contract",
            "purpose",
            "canonical_root",
            "effective_uid",
            "plan_sha256",
            "maintenance_transaction_sha256",
            "expected_active_receipt_sha256",
            "target_receipt_sha256",
            "content_sha256",
        },
        label,
    )
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["contract"] != DEPLOYER_AUTHORIZATION_CONTRACT
        or value["purpose"] != "manual-exact-target-rollback"
    ):
        raise DeploymentError(f"{label} contract mismatch")
    root = _normalized_absolute_path(
        Path(_text(value["canonical_root"], f"{label}.canonical_root")),
        f"{label}.canonical_root",
    )
    effective_uid = _nonnegative_integer(
        value["effective_uid"],
        f"{label}.effective_uid",
    )
    digests = {
        key: _sha256(value[key], f"{label}.{key}")
        for key in (
            "plan_sha256",
            "maintenance_transaction_sha256",
            "expected_active_receipt_sha256",
            "target_receipt_sha256",
        )
    }
    if (
        root != facts.canonical_root
        or effective_uid != facts.effective_uid
        or digests
        != {
            "plan_sha256": facts.plan_sha256,
            "maintenance_transaction_sha256": (facts.maintenance_transaction_sha256),
            "expected_active_receipt_sha256": (facts.expected_active_receipt_sha256),
            "target_receipt_sha256": facts.target_receipt_sha256,
        }
    ):
        raise DeploymentError(f"{label} facts disagree")
    return RollbackToAuthorization(
        root,
        effective_uid,
        digests["plan_sha256"],
        digests["maintenance_transaction_sha256"],
        digests["expected_active_receipt_sha256"],
        digests["target_receipt_sha256"],
        _content_sha256(value, label),
    )


def _parse_runtime_qualification(raw: bytes) -> RuntimeQualification:
    label = "runtime qualification"
    value = _exact(
        _parse_canonical_json(raw, label),
        {
            "schema_version",
            "contract",
            "content_sha256",
            "platform",
            "main_executable",
            "runtime_closure",
            "dependency_classes",
        },
        label,
    )
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise DeploymentError("runtime qualification schema version mismatch")
    if value["contract"] != RUNTIME_QUALIFICATION_CONTRACT:
        raise DeploymentError("runtime qualification contract mismatch")
    platform_value = _exact(
        value["platform"],
        {"system", "machine", "qualified_filesystem_class"},
        "runtime qualification platform",
    )
    normalized_platform = {
        "system": _platform_token(
            platform_value["system"],
            "runtime qualification platform.system",
        ),
        "machine": _platform_token(
            platform_value["machine"],
            "runtime qualification platform.machine",
        ),
        "qualified_filesystem_class": _token(
            platform_value["qualified_filesystem_class"],
            "runtime qualification filesystem class",
        ),
    }
    executable = _exact(
        value["main_executable"],
        {"path", "length", "sha256", "implementation", "version"},
        "runtime qualification main executable",
    )
    version = _exact(
        executable["version"],
        {"major", "minor", "micro"},
        "runtime qualification version",
    )
    normalized_version = {
        key: _nonnegative_integer(
            version[key],
            f"runtime qualification version.{key}",
        )
        for key in ("major", "minor", "micro")
    }
    implementation = _token(
        executable["implementation"],
        "runtime qualification implementation",
    )
    if implementation != "cpython" or (
        normalized_version["major"],
        normalized_version["minor"],
    ) < (3, 13):
        raise DeploymentError("runtime qualification requires CPython 3.13 or newer")
    executable_path = _normalized_absolute_path(
        Path(_text(executable["path"], "runtime qualification executable path")),
        "runtime qualification executable path",
    )
    executable_raw = _capture_absolute_regular(
        executable_path,
        MAX_INTERPRETER_BYTES,
        "runtime qualification executable",
    )
    executable_length = _nonnegative_integer(
        executable["length"],
        "runtime qualification executable length",
    )
    executable_sha256 = _sha256(
        executable["sha256"],
        "runtime qualification executable digest",
    )
    if (
        executable_length != len(executable_raw)
        or executable_sha256 != hashlib.sha256(executable_raw).hexdigest()
    ):
        raise DeploymentError("runtime qualification executable bytes disagree")
    closure = _exact(
        value["runtime_closure"],
        {"supplier", "provenance", "qualification_class", "evidence_sha256"},
        "runtime qualification closure",
    )
    normalized_closure = {
        "supplier": _token(closure["supplier"], "runtime qualification supplier"),
        "provenance": _token(
            closure["provenance"],
            "runtime qualification provenance",
        ),
        "qualification_class": _token(
            closure["qualification_class"],
            "runtime qualification class",
        ),
        "evidence_sha256": _sha256(
            closure["evidence_sha256"],
            "runtime qualification evidence digest",
        ),
    }
    dependencies = _string_inventory(
        value["dependency_classes"],
        "runtime qualification dependency classes",
    )
    dependencies = tuple(
        _token(item, "runtime qualification dependency class") for item in dependencies
    )
    if dependencies != tuple(sorted(dependencies)):
        raise DeploymentError("runtime qualification dependency classes must be sorted")
    return RuntimeQualification(
        _freeze(normalized_platform),
        _freeze(
            {
                "path": str(executable_path),
                "length": executable_length,
                "sha256": executable_sha256,
                "implementation": implementation,
                "version": normalized_version,
            }
        ),
        _freeze(normalized_closure),
        dependencies,
        _content_sha256(value, label),
    )


def _relative_path(value: object, label: str) -> tuple[str, tuple[str, ...]]:
    if not isinstance(value, str) or not value:
        raise DeploymentError(f"{label} is not a traversal-free relative path")
    raw = _text(value, label)
    if "\\" in raw or raw.startswith("/") or raw.endswith("/"):
        raise DeploymentError(f"{label} is not a traversal-free relative path")
    components = tuple(raw.split("/"))
    if not components or any(item in {"", ".", ".."} for item in components):
        raise DeploymentError(f"{label} contains an unsafe path component")
    parsed = PurePosixPath(raw)
    if parsed.is_absolute() or tuple(parsed.parts) != components:
        raise DeploymentError(f"{label} is not a traversal-free relative path")
    return raw, components


def _require_sorted_unique(
    values: list[Any],
    key,
    label: str,
) -> None:
    keys = [key(value) for value in values]
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise DeploymentError(f"{label} must be sorted and duplicate-free")


def _validator_implementation_identity(
    contract: str,
    entrypoint: str,
    modules: Iterable[tuple[str, str]],
) -> str:
    return _digest(
        {
            "contract": VALIDATOR_ARTIFACT_MANIFEST_CONTRACT,
            "validator_contract": contract,
            "entrypoint_module": entrypoint,
            "modules": [
                {"name": name, "content_sha256": content_sha256}
                for name, content_sha256 in modules
            ],
        }
    )


def _parse_producer(value: object, index: int) -> dict[str, Any]:
    label = f"provider producer {index}"
    result = _exact(
        value,
        {
            "producer_id",
            "contract",
            "implementation_sha256",
            "validator_id",
            "validator_contract",
            "validator_implementation_sha256",
            "lifecycle",
        },
        label,
    )
    lifecycle = _lifecycle(result["lifecycle"], f"{label}.lifecycle")
    return {
        "producer_id": _token(result["producer_id"], f"{label}.producer_id"),
        "contract": _text(result["contract"], f"{label}.contract"),
        "implementation_sha256": _sha256(
            result["implementation_sha256"], f"{label}.implementation_sha256"
        ),
        "validator_id": _token(result["validator_id"], f"{label}.validator_id"),
        "validator_contract": _text(
            result["validator_contract"], f"{label}.validator_contract"
        ),
        "validator_implementation_sha256": _sha256(
            result["validator_implementation_sha256"],
            f"{label}.validator_implementation_sha256",
        ),
        **lifecycle,
    }


def _issuer_capabilities(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise DeploymentError(f"{label} must be a nonempty list")
    if len(value) > MAX_VALIDATORS:
        raise DeploymentError(f"{label} exceeds the item limit")
    normalized = [_token(item, f"{label} item") for item in value]
    if normalized != sorted(normalized) or len(normalized) != len(set(normalized)):
        raise DeploymentError(f"{label} must be sorted and duplicate-free")
    return normalized


def _parse_issuer(value: object, index: int) -> dict[str, Any]:
    label = f"provider issuer {index}"
    result = _exact(
        value,
        {
            "issuer_id",
            "contract",
            "implementation_sha256",
            "capabilities",
            "lifecycle",
        },
        label,
    )
    capabilities = _issuer_capabilities(
        result["capabilities"],
        f"{label}.capabilities",
    )
    lifecycle = _lifecycle(result["lifecycle"], f"{label}.lifecycle")
    return {
        "issuer_id": _token(result["issuer_id"], f"{label}.issuer_id"),
        "contract": _text(result["contract"], f"{label}.contract"),
        "implementation_sha256": _sha256(
            result["implementation_sha256"], f"{label}.implementation_sha256"
        ),
        "capabilities": capabilities,
        **lifecycle,
    }


def _parse_validator(value: object, index: int) -> _DeclaredValidator:
    label = f"provider validator {index}"
    result = _exact(
        value,
        {
            "validator_id",
            "contract",
            "implementation_sha256",
            "entrypoint",
            "modules",
            "lifecycle",
        },
        label,
    )
    validator_id = _token(result["validator_id"], f"{label}.validator_id")
    contract = _text(result["contract"], f"{label}.contract")
    implementation = _sha256(
        result["implementation_sha256"], f"{label}.implementation_sha256"
    )
    entrypoint = _token(result["entrypoint"], f"{label}.entrypoint")
    values = result["modules"]
    if (
        not isinstance(values, list)
        or not values
        or len(values) > MAX_VALIDATOR_MODULES
    ):
        raise DeploymentError(f"{label}.modules is invalid")
    modules: list[_DeclaredModule] = []
    names: set[str] = set()
    paths: set[str] = set()
    for module_index, module_value in enumerate(values):
        module_label = f"{label}.modules[{module_index}]"
        item = _exact(
            module_value,
            {"name", "relative_path", "length", "sha256"},
            module_label,
        )
        name = _token(item["name"], f"{module_label}.name")
        relative_path, components = _relative_path(
            item["relative_path"], f"{module_label}.relative_path"
        )
        length = _nonnegative_integer(item["length"], f"{module_label}.length")
        if length > MAX_MODULE_BYTES:
            raise DeploymentError(f"{module_label}.length exceeds the module limit")
        content_sha256 = _sha256(item["sha256"], f"{module_label}.sha256")
        if name in names or relative_path in paths:
            raise DeploymentError(f"{label} has duplicate module names or paths")
        names.add(name)
        paths.add(relative_path)
        modules.append(
            _DeclaredModule(name, relative_path, components, length, content_sha256)
        )
    if sum(item.length for item in modules) > MAX_VALIDATOR_ARTIFACT_BYTES:
        raise DeploymentError(f"{label} aggregate artifact bytes exceed the limit")
    if modules[0].name != entrypoint:
        raise DeploymentError(f"{label} entrypoint must be the first module")
    expected = _validator_implementation_identity(
        contract,
        entrypoint,
        ((item.name, item.sha256) for item in modules),
    )
    if implementation != expected:
        raise DeploymentError(f"{label} implementation identity mismatch")
    return _DeclaredValidator(
        validator_id,
        contract,
        implementation,
        entrypoint,
        tuple(modules),
        _lifecycle(result["lifecycle"], f"{label}.lifecycle"),
    )


def _parse_provider(raw: bytes) -> _ParsedProvider:
    value = _parse_canonical_json(raw, "provider declaration")
    value = _exact(
        value,
        {
            "schema_version",
            "contract",
            "content_sha256",
            "plugin_id",
            "publisher",
            "repository",
            "authority_profile",
            "producers",
            "issuers",
            "validators",
        },
        "provider declaration",
    )
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise DeploymentError("provider declaration schema version mismatch")
    if value["contract"] != PROVIDER_DECLARATION_CONTRACT:
        raise DeploymentError("provider declaration contract mismatch")
    content_sha256 = _sha256(
        value["content_sha256"], "provider declaration.content_sha256"
    )
    unsigned = {key: item for key, item in value.items() if key != "content_sha256"}
    if content_sha256 != _digest(unsigned):
        raise DeploymentError("provider declaration content digest mismatch")
    for category in ("producers", "issuers", "validators"):
        if not isinstance(value[category], list):
            raise DeploymentError(f"provider declaration.{category} must be a list")
    if len(value["validators"]) > MAX_VALIDATORS:
        raise DeploymentError("provider declaration exceeds the validator limit")
    producers = [
        _parse_producer(item, index) for index, item in enumerate(value["producers"])
    ]
    issuers = [
        _parse_issuer(item, index) for index, item in enumerate(value["issuers"])
    ]
    validators = [
        _parse_validator(item, index) for index, item in enumerate(value["validators"])
    ]
    if not producers and not issuers and not validators:
        raise DeploymentError("provider declaration registers no roles")
    _require_sorted_unique(
        producers,
        lambda item: (
            item["producer_id"],
            item["contract"],
            item["implementation_sha256"],
        ),
        "provider producers",
    )
    _require_sorted_unique(
        issuers,
        lambda item: (
            item["issuer_id"],
            item["contract"],
            item["implementation_sha256"],
        ),
        "provider issuers",
    )
    _require_sorted_unique(
        validators,
        lambda item: (item.validator_id, item.contract, item.implementation_sha256),
        "provider validators",
    )
    return _ParsedProvider(
        _token(value["plugin_id"], "provider declaration.plugin_id"),
        _text(value["publisher"], "provider declaration.publisher"),
        _text(value["repository"], "provider declaration.repository"),
        _token(value["authority_profile"], "provider declaration.authority_profile"),
        content_sha256,
        tuple(producers),
        tuple(issuers),
        tuple(validators),
    )


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _mapping_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
    )


def _open_root(
    path: Path,
    label: str,
    *,
    mapping_only: bool = False,
) -> _RootSnapshot:
    if not path.is_absolute():
        raise DeploymentError(f"{label} must be absolute")
    try:
        before = path.lstat()
    except OSError as error:
        raise DeploymentError(f"{label} is unavailable") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise DeploymentError(f"{label} must be a real directory")
    try:
        descriptor = os.open(path, _DIRECTORY_FLAGS)
    except OSError as error:
        raise DeploymentError(f"{label} cannot be opened") from error
    try:
        after = os.fstat(descriptor)
        identify = _mapping_identity if mapping_only else _identity
        if identify(before) != identify(after):
            raise DeploymentError(f"{label} changed while it was opened")
        return _RootSnapshot(path, descriptor, identify(after), mapping_only)
    except BaseException:
        os.close(descriptor)
        raise


def _recheck_root(snapshot: _RootSnapshot, label: str) -> None:
    identify = _mapping_identity if snapshot.mapping_only else _identity
    try:
        descriptor_identity = identify(os.fstat(snapshot.fd))
        visible_identity = identify(snapshot.path.lstat())
    except OSError as error:
        raise DeploymentError(f"{label} became unavailable") from error
    if (
        descriptor_identity != snapshot.identity
        or visible_identity != snapshot.identity
    ):
        raise DeploymentError(f"{label} changed during provider import")


def _recheck_root_alias(
    snapshot: _RootSnapshot,
    alias_path: Path,
    canonical_path: Path,
    label: str,
) -> None:
    """Require an alias and its physical path to retain one opened identity."""

    _recheck_root(snapshot, label)
    identify = _mapping_identity if snapshot.mapping_only else _identity
    try:
        resolved = alias_path.resolve(strict=True)
        visible_identity = identify(alias_path.stat())
    except (OSError, RuntimeError) as error:
        raise DeploymentError(f"{label} path mapping is unavailable") from error
    if resolved != canonical_path or visible_identity != snapshot.identity:
        raise DeploymentError(f"{label} path mapping changed")


def _read_descriptor(descriptor: int, limit: int, label: str) -> bytes:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise DeploymentError(f"{label} exceeds the byte limit")
        return b"".join(chunks)
    except DeploymentError:
        raise
    except OSError as error:
        raise DeploymentError(f"{label} cannot be read") from error


def _capture_regular(
    root: _RootSnapshot,
    components: tuple[str, ...],
    label: str,
    *,
    limit: int,
    absent: bool = False,
    shared_ancestor_mappings: bool = False,
) -> _FileSnapshot | None:
    if not components:
        raise DeploymentError(f"{label} has no path components")
    directory_fds: list[int] = []
    edges: list[_DirectoryEdge] = []
    current = os.dup(root.fd)
    directory_fds.append(current)
    try:
        directory_components = components[:-1]
        for index, component in enumerate(directory_components):
            try:
                before = os.stat(component, dir_fd=current, follow_symlinks=False)
            except OSError as error:
                raise DeploymentError(f"{label} directory is unavailable") from error
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                raise DeploymentError(
                    f"{label} path component is a symlink or not a directory"
                )
            try:
                child = os.open(component, _DIRECTORY_FLAGS, dir_fd=current)
            except OSError as error:
                raise DeploymentError(f"{label} directory cannot be opened") from error
            after = os.fstat(child)
            mapping_only = (
                shared_ancestor_mappings and index < len(directory_components) - 1
            )
            identify = _mapping_identity if mapping_only else _identity
            if identify(before) != identify(after):
                os.close(child)
                raise DeploymentError(f"{label} directory changed while it was opened")
            directory_fds.append(child)
            edges.append(
                _DirectoryEdge(
                    current,
                    component,
                    child,
                    identify(after),
                    mapping_only,
                )
            )
            current = child
        name = components[-1]
        try:
            before = os.stat(name, dir_fd=current, follow_symlinks=False)
        except FileNotFoundError:
            if absent:
                for descriptor in reversed(directory_fds):
                    os.close(descriptor)
                return None
            raise DeploymentError(f"{label} is missing") from None
        except OSError as error:
            raise DeploymentError(f"{label} is unavailable") from error
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise DeploymentError(
                f"{label} must be a nonsymlink regular file; special files reject"
            )
        try:
            file_descriptor = os.open(name, _FILE_FLAGS, dir_fd=current)
        except OSError as error:
            raise DeploymentError(f"{label} cannot be opened") from error
        try:
            after = os.fstat(file_descriptor)
            if _identity(before) != _identity(after):
                raise DeploymentError(f"{label} changed while it was opened")
            raw = _read_descriptor(file_descriptor, limit, label)
            if len(raw) != after.st_size:
                raise DeploymentError(f"{label} length changed while it was read")
            return _FileSnapshot(
                label,
                tuple(directory_fds),
                tuple(edges),
                file_descriptor,
                current,
                name,
                _identity(after),
                raw,
            )
        except BaseException:
            os.close(file_descriptor)
            raise
    except BaseException:
        for descriptor in reversed(directory_fds):
            os.close(descriptor)
        raise


def _recheck_file(snapshot: _FileSnapshot) -> None:
    try:
        for edge in snapshot.edges:
            identify = _mapping_identity if edge.mapping_only else _identity
            if identify(os.fstat(edge.child_fd)) != edge.identity:
                raise DeploymentError(f"{snapshot.label} directory changed")
            visible = os.stat(edge.name, dir_fd=edge.parent_fd, follow_symlinks=False)
            if identify(visible) != edge.identity:
                raise DeploymentError(f"{snapshot.label} directory mapping changed")
        if _identity(os.fstat(snapshot.file_fd)) != snapshot.identity:
            raise DeploymentError(f"{snapshot.label} changed during provider import")
        visible = os.stat(
            snapshot.file_name,
            dir_fd=snapshot.file_parent_fd,
            follow_symlinks=False,
        )
        if _identity(visible) != snapshot.identity:
            raise DeploymentError(f"{snapshot.label} path mapping changed")
        if (
            _read_descriptor(snapshot.file_fd, len(snapshot.raw), snapshot.label)
            != snapshot.raw
        ):
            raise DeploymentError(
                f"{snapshot.label} bytes changed during provider import"
            )
    except DeploymentError:
        raise
    except OSError as error:
        raise DeploymentError(f"{snapshot.label} became unavailable") from error


def _close_file(snapshot: _FileSnapshot) -> None:
    error: OSError | None = None
    try:
        os.close(snapshot.file_fd)
    except OSError as caught:
        error = caught
    for descriptor in reversed(snapshot.directory_fds):
        try:
            os.close(descriptor)
        except OSError as caught:
            if error is None:
                error = caught
    if error is not None:
        raise error


def _candidate_directory_names(descriptor: int) -> tuple[str, ...]:
    try:
        names = os.listdir(descriptor)
    except OSError as error:
        raise DeploymentError(
            "candidate tree directory cannot be enumerated"
        ) from error
    for name in names:
        _relative_path(name, "candidate tree path component")
    if len(names) != len(set(names)):
        raise DeploymentError("candidate tree directory inventory is ambiguous")
    return tuple(sorted(names))


def _snapshot_candidate_tree(path: Path) -> CandidateTree:
    """Capture one bounded plugin tree without following source aliases."""

    candidate_path = _normalized_absolute_path(path, "candidate tree root")
    try:
        candidate_before = candidate_path.lstat()
        if stat.S_ISLNK(candidate_before.st_mode) or not stat.S_ISDIR(
            candidate_before.st_mode
        ):
            raise DeploymentError("candidate tree root must be a real directory")
        canonical_candidate_path = candidate_path.resolve(strict=True)
    except DeploymentError:
        raise
    except (OSError, RuntimeError) as error:
        raise DeploymentError("candidate tree root cannot be resolved") from error
    root = _open_root(canonical_candidate_path, "candidate tree root")
    directories: list[_CandidateDirectory] = []
    files: list[_CandidateFile] = []
    entries: list[dict[str, Any]] = []
    total_bytes = 0

    def account_path() -> None:
        if len(entries) >= MAX_CANDIDATE_TREE_PATHS:
            raise DeploymentError("candidate tree exceeds the path limit")

    def visit(
        directory_fd: int,
        components: tuple[str, ...],
        names: tuple[str, ...],
    ) -> None:
        nonlocal total_bytes
        for name in names:
            relative_path, relative_components = _relative_path(
                "/".join((*components, name)),
                "candidate tree path",
            )
            try:
                before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as error:
                raise DeploymentError(
                    "candidate tree entry became unavailable"
                ) from error
            if stat.S_ISLNK(before.st_mode):
                raise DeploymentError(
                    "candidate tree entry is a symlink or special file"
                )
            if stat.S_ISDIR(before.st_mode):
                account_path()
                try:
                    child_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=directory_fd)
                except OSError as error:
                    raise DeploymentError(
                        "candidate tree directory cannot be opened"
                    ) from error
                try:
                    after = os.fstat(child_fd)
                    if _identity(before) != _identity(after):
                        raise DeploymentError(
                            "candidate tree directory changed while it was opened"
                        )
                    child_names = _candidate_directory_names(child_fd)
                    directories.append(
                        _CandidateDirectory(
                            directory_fd,
                            name,
                            child_fd,
                            _identity(after),
                            child_names,
                        )
                    )
                    entries.append({"path": relative_path, "kind": "directory"})
                    visit(child_fd, relative_components, child_names)
                except BaseException:
                    if not any(item.fd == child_fd for item in directories):
                        os.close(child_fd)
                    raise
                continue
            if not stat.S_ISREG(before.st_mode):
                raise DeploymentError(
                    "candidate tree entry is a symlink or special file"
                )
            account_path()
            try:
                file_fd = os.open(name, _FILE_FLAGS, dir_fd=directory_fd)
            except OSError as error:
                raise DeploymentError("candidate tree file cannot be opened") from error
            try:
                after = os.fstat(file_fd)
                if _identity(before) != _identity(after):
                    raise DeploymentError(
                        "candidate tree file changed while it was opened"
                    )
                raw = _read_descriptor(
                    file_fd,
                    MAX_CANDIDATE_TREE_FILE_BYTES,
                    f"candidate tree file {relative_path}",
                )
                if len(raw) != after.st_size:
                    raise DeploymentError(
                        "candidate tree file length changed while it was read"
                    )
                total_bytes += len(raw)
                if total_bytes > MAX_CANDIDATE_TREE_BYTES:
                    raise DeploymentError(
                        "candidate tree exceeds the aggregate byte limit"
                    )
                files.append(
                    _CandidateFile(
                        directory_fd,
                        name,
                        file_fd,
                        _identity(after),
                        raw,
                        relative_path,
                    )
                )
                entries.append(
                    {
                        "path": relative_path,
                        "kind": "file",
                        "length": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }
                )
            except BaseException:
                if not any(item.fd == file_fd for item in files):
                    os.close(file_fd)
                raise

    try:
        _recheck_root_alias(
            root,
            candidate_path,
            canonical_candidate_path,
            "candidate tree root",
        )
        root_names = _candidate_directory_names(root.fd)
        visit(root.fd, (), root_names)
        _recheck_root_alias(
            root,
            candidate_path,
            canonical_candidate_path,
            "candidate tree root",
        )
        if _candidate_directory_names(root.fd) != root_names:
            raise DeploymentError("candidate tree root inventory changed")
        for directory in directories:
            try:
                descriptor_identity = _identity(os.fstat(directory.fd))
                visible_identity = _identity(
                    os.stat(
                        directory.name,
                        dir_fd=directory.parent_fd,
                        follow_symlinks=False,
                    )
                )
            except OSError as error:
                raise DeploymentError(
                    "candidate tree directory became unavailable"
                ) from error
            if (
                descriptor_identity != directory.identity
                or visible_identity != directory.identity
            ):
                raise DeploymentError("candidate tree directory mapping changed")
            if _candidate_directory_names(directory.fd) != directory.names:
                raise DeploymentError("candidate tree directory inventory changed")
        for file in files:
            try:
                descriptor_identity = _identity(os.fstat(file.fd))
                visible_identity = _identity(
                    os.stat(
                        file.name,
                        dir_fd=file.parent_fd,
                        follow_symlinks=False,
                    )
                )
            except OSError as error:
                raise DeploymentError(
                    "candidate tree file became unavailable"
                ) from error
            if (
                descriptor_identity != file.identity
                or visible_identity != file.identity
            ):
                raise DeploymentError("candidate tree file mapping changed")
            if (
                _read_descriptor(
                    file.fd,
                    len(file.raw),
                    f"candidate tree file {file.relative_path}",
                )
                != file.raw
            ):
                raise DeploymentError("candidate tree file bytes changed")

        _recheck_root_alias(
            root,
            candidate_path,
            canonical_candidate_path,
            "candidate tree root",
        )

        entries.sort(key=lambda item: item["path"])
        frozen_entries = tuple(MappingProxyType(dict(item)) for item in entries)
        frozen_files = MappingProxyType(
            {
                item.relative_path: item.raw
                for item in sorted(files, key=lambda item: item.relative_path)
            }
        )
        return CandidateTree(
            root.path,
            frozen_entries,
            frozen_files,
            _digest(
                {
                    "contract": PLUGIN_SUBTREE_CONTRACT,
                    "entries": entries,
                }
            ),
        )
    finally:
        for file in reversed(files):
            os.close(file.fd)
        for directory in reversed(directories):
            os.close(directory.fd)
        os.close(root.fd)


def _candidate_file(snapshot: CandidateTree, relative_path: str, label: str) -> bytes:
    try:
        return snapshot.files[relative_path]
    except KeyError:
        raise DeploymentError(f"candidate source is missing {label}") from None


def _bind_candidate_source(
    snapshot: CandidateTree,
    source_selection_raw: bytes,
    manager_binding_raw: bytes,
    manager_receipt_raw: bytes,
) -> CandidateSource:
    """Bind the retained harness-only active-to-active request surface."""

    selection = _parse_source_selection(source_selection_raw)
    evidence = _validate_source_evidence(
        selection,
        HarnessSnapshotEvidence(manager_binding_raw, manager_receipt_raw),
    )
    return _bind_candidate_source_evidence(
        snapshot,
        source_selection_raw,
        selection,
        evidence,
    )


def _bind_candidate_source_evidence(
    snapshot: CandidateTree,
    source_selection_raw: bytes,
    selection: SourceSelection,
    evidence: _ValidatedSourceEvidence,
) -> CandidateSource:
    """Construct one composite identity from prevalidated source evidence."""

    provider_raw = snapshot.files.get(PROVIDER_DECLARATION_NAME)
    agent_plugin_raw = _candidate_file(
        snapshot,
        AGENT_PLUGIN_MANIFEST_NAME,
        "the Agent Plugins manifest",
    )
    claude_raw = _candidate_file(
        snapshot,
        CLAUDE_MANIFEST_NAME,
        "the Claude manifest",
    )
    if CODEX_MANIFEST_NAME in snapshot.files:
        raise DeploymentError("candidate legacy Codex manifest must be absent")
    provider = _parse_provider(provider_raw) if provider_raw is not None else None
    agent_plugin, claude = _parse_agent_plugin_manifests(
        agent_plugin_raw,
        claude_raw,
    )
    if provider is not None and provider.plugin_id != agent_plugin.name:
        raise DeploymentError("candidate source plugin ID cross-binding disagrees")
    evidence_plugin_id = (
        evidence.harness.claims["plugin_id"]
        if evidence.harness is not None
        else (
            evidence.publisher.claims["plugin_id"]
            if evidence.publisher is not None
            else agent_plugin.name
        )
    )
    if agent_plugin.name != evidence_plugin_id:
        raise DeploymentError("candidate source evidence plugin ID disagrees")
    if agent_plugin.author != selection.manifest_author or (
        provider is not None and provider.publisher != selection.publisher_id
    ):
        raise DeploymentError("candidate source publisher cross-binding disagrees")
    if agent_plugin.repository != selection.repository_url or (
        provider is not None and provider.repository != selection.repository_url
    ):
        raise DeploymentError("candidate source repository cross-binding disagrees")
    if agent_plugin.version != selection.release_version:
        raise DeploymentError(
            "candidate source release-version cross-binding disagrees"
        )
    if selection.subtree_sha256 != snapshot.subtree_sha256:
        raise DeploymentError("candidate source subtree cross-binding disagrees")
    return CandidateSource(
        agent_plugin.name,
        selection.mode,
        selection.publisher_id,
        selection.manifest_author,
        selection.repository_id,
        selection.repository_url,
        selection.release_version,
        selection.revision,
        selection.subtree_sha256,
        selection.source_authority,
        evidence.channel,
        evidence.source_trust_class,
        evidence.lineage,
        evidence.source_evidence_sha256,
        selection.content_sha256,
        hashlib.sha256(source_selection_raw).hexdigest(),
        evidence.binding_content_sha256,
        evidence.binding_sha256,
        evidence.record_sha256,
        evidence.resolver,
        evidence.adapter_sha256,
        hashlib.sha256(provider_raw).hexdigest() if provider_raw is not None else None,
        provider.content_sha256 if provider is not None else None,
        agent_plugin.raw_sha256,
        claude.raw_sha256,
        provider.authority_profile if provider is not None else None,
        provider,
        snapshot,
    )


def _source_details_projection(source: CandidateSource) -> dict[str, Any]:
    if source.source_mode == "harness_snapshot":
        binding = source.source_binding_sha256
        record = source.source_record_sha256
        if binding is None or record is None or source.lineage is None:
            raise DeploymentError("harness source evidence projection is incomplete")
        return {
            "channel": source.channel,
            "trust_class": source.source_trust_class,
            "lineage": _thaw(source.lineage),
        }
    if source.source_mode == "publisher_channel":
        if source.channel is None or source.lineage is None:
            raise DeploymentError("publisher source evidence projection is incomplete")
        return {
            "channel": source.channel,
            "trust_class": source.source_trust_class,
            "lineage": _thaw(source.lineage),
        }
    if source.source_mode == "exact_release":
        return {
            "trust_class": source.source_trust_class,
            "revision": source.revision,
            "subtree_sha256": source.subtree_sha256,
        }
    raise DeploymentError("candidate source mode is unsupported")


def _source_evidence_projection(source: CandidateSource) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": source.source_mode,
        "source_evidence_sha256": source.source_evidence_sha256,
    }
    if source.source_mode == "harness_snapshot":
        if (
            source.source_binding_sha256 is None
            or source.source_binding_content_sha256 is None
            or source.source_record_sha256 is None
        ):
            raise DeploymentError("harness source evidence projection is incomplete")
        result.update(
            {
                "manager_binding_sha256": source.source_binding_sha256,
                "manager_binding_content_sha256": (
                    source.source_binding_content_sha256
                ),
                "manager_receipt_sha256": source.source_record_sha256,
                "adapter_sha256": source.source_adapter_sha256,
            }
        )
    elif source.source_mode == "publisher_channel":
        binding = source.source_binding_sha256
        binding_content = source.source_binding_content_sha256
        record = source.source_record_sha256
        if binding is None or binding_content is None or record is None:
            raise DeploymentError("publisher source evidence projection is incomplete")
        if source.source_resolver is None or source.source_adapter_sha256 is None:
            raise DeploymentError("publisher resolver evidence is incomplete")
        result.update(
            {
                "resolver": source.source_resolver,
                "adapter_sha256": source.source_adapter_sha256,
                "publisher_binding_sha256": binding,
                "publisher_binding_content_sha256": binding_content,
                "publisher_record_sha256": record,
            }
        )
    elif source.source_mode != "exact_release":
        raise DeploymentError("candidate source mode is unsupported")
    return result


def _harness_manager_receipt_sha256(source: CandidateSource) -> str:
    if source.source_mode != "harness_snapshot" or source.source_record_sha256 is None:
        raise DeploymentError("harness manager receipt evidence is unavailable")
    return source.source_record_sha256


def _provider_policy_projection(source: CandidateSource) -> dict[str, Any] | None:
    provider = source.provider
    if provider is None:
        return None
    producers = [
        {
            "producer_id": item["producer_id"],
            "contract": item["contract"],
            "validator_id": item["validator_id"],
            "validator_contract": item["validator_contract"],
            "state": item["state"],
            "usable_for_new_publication": item["usable_for_new_publication"],
        }
        for item in provider.producers
    ]
    issuers = [
        {
            "issuer_id": item["issuer_id"],
            "contract": item["contract"],
            "capabilities": list(item["capabilities"]),
            "state": item["state"],
            "usable_for_new_publication": item["usable_for_new_publication"],
        }
        for item in provider.issuers
    ]
    validators = [
        {
            "validator_id": item.validator_id,
            "contract": item.contract,
            "state": item.lifecycle["state"],
            "usable_for_new_publication": item.lifecycle["usable_for_new_publication"],
        }
        for item in provider.validators
    ]
    return {
        "plugin_id": provider.plugin_id,
        "authority_profile": provider.authority_profile,
        "producers": producers,
        "issuers": issuers,
        "validators": validators,
    }


def _source_policy_projection(source: CandidateSource) -> dict[str, Any]:
    if source.source_mode in {"harness_snapshot", "publisher_channel"}:
        if source.channel is None or source.lineage is None:
            raise DeploymentError("channel source authority is incomplete")
        details = {
            "channel": source.channel,
            "trust_class": source.source_trust_class,
            "lineage_id": source.lineage["lineage_id"],
        }
    elif source.source_mode == "exact_release":
        if source.channel is not None or source.lineage is not None:
            raise DeploymentError("exact-release source authority is ambiguous")
        details = {"trust_class": source.source_trust_class}
    else:
        raise DeploymentError("candidate source mode is unsupported")
    return {
        "plugin_id": source.plugin_id,
        "mode": source.source_mode,
        "publisher_id": source.publisher_id,
        "manifest_author": _thaw(source.manifest_author),
        "repository_id": source.repository_id,
        "repository_url": source.repository_url,
        "source_authority": source.source_authority,
        "details": details,
    }


def _policy_covers_source(
    policy: CompatibilityPolicy,
    source: CandidateSource,
) -> bool:
    if _thaw(policy.source) != _source_policy_projection(source):
        return False
    provider = _provider_policy_projection(source)
    if provider is None:
        return True
    return any(_thaw(item) == provider for item in policy.providers)


def _source_authority_tuple(source: CandidateSource) -> tuple[Any, ...]:
    mode_authority: tuple[Any, ...]
    if source.source_mode in {"harness_snapshot", "publisher_channel"}:
        if source.channel is None or source.lineage is None:
            raise DeploymentError("channel source authority is incomplete")
        mode_authority = (source.channel, source.lineage["lineage_id"])
    else:
        mode_authority = ()
    return (
        source.plugin_id,
        source.source_mode,
        source.publisher_id,
        tuple(source.manifest_author.items()),
        source.repository_id,
        source.repository_url,
        source.source_authority,
        source.source_trust_class,
        *mode_authority,
    )


def _same_release_evidence(
    active: CandidateSource | _BridgeLegacyCandidateSource,
    candidate: CandidateSource | _BridgeLegacyCandidateSource,
) -> bool:
    if type(active) is not type(candidate):
        return False
    if type(active) is CandidateSource and type(candidate) is CandidateSource:
        manifests_match = (
            active.agent_plugin_manifest_sha256
            == candidate.agent_plugin_manifest_sha256
            and active.claude_manifest_sha256 == candidate.claude_manifest_sha256
        )
    elif (
        type(active) is _BridgeLegacyCandidateSource
        and type(candidate) is _BridgeLegacyCandidateSource
    ):
        manifests_match = (
            active.claude_manifest_sha256 == candidate.claude_manifest_sha256
            and active.codex_manifest_sha256 == candidate.codex_manifest_sha256
        )
    else:
        return False
    return (
        active.revision == candidate.revision
        and active.subtree_sha256 == candidate.subtree_sha256
        and active.provider_declaration_sha256 == candidate.provider_declaration_sha256
        and manifests_match
        and _provider_policy_projection(active)
        == _provider_policy_projection(candidate)
    )


def _classify_source_transition(
    *,
    active_source: CandidateSource,
    candidate_source: CandidateSource,
    policy_unchanged: bool,
) -> Classification:
    """Classify the source facts shared by planning and inert-stage verification."""

    same_revision = active_source.revision == candidate_source.revision
    same_subtree = active_source.subtree_sha256 == candidate_source.subtree_sha256
    same_release_version = (
        active_source.release_version == candidate_source.release_version
    )
    active_lineage = active_source.lineage
    candidate_lineage = candidate_source.lineage
    same_lineage_position = (
        active_lineage is not None
        and candidate_lineage is not None
        and active_lineage["lineage_id"] == candidate_lineage["lineage_id"]
        and active_lineage["sequence"] == candidate_lineage["sequence"]
    )
    if (same_revision and not same_subtree) or (
        same_release_version and not same_subtree
    ):
        return Classification("integrity-rejected", "immutable-release-reused")
    if same_lineage_position and (not same_revision or not same_subtree):
        return Classification("integrity-rejected", "lineage-position-reused")
    if (
        same_revision
        and same_subtree
        and not _same_release_evidence(
            active_source,
            candidate_source,
        )
    ):
        return Classification("integrity-rejected", "release-evidence-disagrees")
    same_source_authority = _source_authority_tuple(
        active_source
    ) == _source_authority_tuple(candidate_source)
    if (
        _same_release_evidence(active_source, candidate_source)
        and same_source_authority
        and policy_unchanged
    ):
        return Classification("no-op", "exact-release")
    if active_source.source_mode != candidate_source.source_mode:
        return Classification("approval-required", "source-authority")
    if not same_source_authority:
        return Classification("approval-required", "source-authority")
    if not policy_unchanged:
        return Classification("approval-required", "future-update-policy")
    active_provider = _provider_policy_projection(active_source)
    candidate_provider = _provider_policy_projection(candidate_source)
    if (active_provider is None) != (candidate_provider is None):
        return Classification("approval-required", "role-inventory")
    if active_provider != candidate_provider:
        if active_source.authority_profile != candidate_source.authority_profile:
            return Classification("approval-required", "authority-profile")
        return Classification("approval-required", "provider-authority")
    if candidate_source.source_mode == "exact_release":
        return Classification("approval-required", "exact-release-pin")
    if active_lineage is None or candidate_lineage is None:
        return Classification("integrity-rejected", "lineage-evidence-missing")
    if candidate_lineage["sequence"] < active_lineage["sequence"]:
        return Classification("approval-required", "downgrade")
    if candidate_lineage["sequence"] == active_lineage["sequence"]:
        return Classification("integrity-rejected", "lineage-direction-unproven")
    return Classification("compatible-forward", "active-policy")


def _require_deployment_source_outcome(
    classification: Classification,
    *,
    control_maintenance: bool,
    label: str,
) -> None:
    if classification.outcome == "integrity-rejected":
        raise DeploymentError(
            f"{label} source transition failed integrity classification: "
            f"{classification.outcome}/{classification.reason}"
        )
    source_boundary = (
        classification.outcome == "approval-required"
        and classification.reason
        in {"downgrade", "exact-release-pin", "source-authority"}
    )
    if classification.outcome != "compatible-forward" and not (
        classification.outcome == "approval-required"
        and (control_maintenance or source_boundary)
    ):
        raise DeploymentError(
            f"{label} source transition is not authorized: "
            f"{classification.outcome}/{classification.reason}"
        )


def _active_prior_authorization_purpose(
    classification: Classification,
    *,
    control_maintenance: bool,
) -> str:
    """Choose authorization purpose from source policy, then engine shape."""

    source_boundary_reasons = {
        "downgrade",
        "exact-release-pin",
        "source-authority",
    }
    if (
        classification.outcome == "approval-required"
        and classification.reason in source_boundary_reasons
    ):
        return "source-boundary-change"
    if control_maintenance:
        return "complete-control-set-maintenance"
    return "routine-compatible-forward"


def _classify_candidate_source(
    *,
    active_source: CandidateSource | None,
    active_policy: CompatibilityPolicy | None,
    active_policy_sha256: str | None,
    candidate_source: CandidateSource,
    candidate_policy_sha256: str,
) -> Classification:
    """Classify with active authority; candidate policy never authorizes itself."""

    _sha256(candidate_policy_sha256, "candidate policy digest")
    if active_source is None and active_policy is None and active_policy_sha256 is None:
        return Classification("approval-required", "first-install")
    if active_source is None or active_policy is None or active_policy_sha256 is None:
        return Classification("integrity-rejected", "active-authority-incomplete")
    if active_policy.raw_sha256 != active_policy_sha256:
        return Classification("integrity-rejected", "active-policy-disagrees")
    if not _policy_covers_source(active_policy, active_source):
        return Classification("integrity-rejected", "active-policy-coverage-unproven")
    classification = _classify_source_transition(
        active_source=active_source,
        candidate_source=candidate_source,
        policy_unchanged=candidate_policy_sha256 == active_policy_sha256,
    )
    if classification.outcome != "compatible-forward":
        return classification
    if not _policy_covers_source(active_policy, candidate_source):
        return Classification("integrity-rejected", "policy-coverage-unproven")
    return classification


def _build_active_runtime(
    source: CandidateSource,
    qualification: RuntimeQualification,
) -> ActiveRuntime:
    payloads: list[dict[str, Any]] = []
    files: dict[str, bytes] = {}
    for role, relative_path in RUNTIME_PAYLOAD_SPECS:
        source_path = f"runtime/{relative_path}"
        raw = _candidate_file(
            source.tree,
            source_path,
            f"runtime payload {role}",
        )
        if len(raw) > MAX_MODULE_BYTES:
            raise DeploymentError(f"runtime payload {role} exceeds the byte limit")
        payloads.append(
            {
                "role": role,
                "relative_path": relative_path,
                "length": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
        files[relative_path] = raw
    runtime_implementation_sha256 = _digest(
        {
            "contract": RUNTIME_ARTIFACT_MANIFEST_CONTRACT,
            "runtime_contract": RUNTIME_CONTRACT,
            "entrypoint_role": "entrypoint",
            "payloads": payloads,
        }
    )
    generation = f"sha256-{runtime_implementation_sha256}"
    executable = qualification.main_executable
    value = {
        "schema_version": 1,
        "contract": ACTIVE_CONTRACT,
        "generation": generation,
        "runtime_contract": RUNTIME_CONTRACT,
        "interpreter": {
            "executable": executable["path"],
            "implementation": executable["implementation"],
            "version": _thaw(executable["version"]),
        },
        "public_release": {
            "repository": source.repository_id,
            "revision": source.revision,
        },
        "payloads": payloads,
    }
    value = {**value, "content_sha256": _digest(value)}
    raw = _canonical_document(value)
    return ActiveRuntime(
        _freeze(value),
        raw,
        hashlib.sha256(raw).hexdigest(),
        generation,
        runtime_implementation_sha256,
        tuple(_freeze(item) for item in payloads),
        MappingProxyType(dict(files)),
    )


def _planned_artifact(
    role: str,
    canonical_root: Path,
    relative_path: str,
    raw: bytes,
    mode: int,
) -> PlannedArtifact:
    relative_path, components = _relative_path(
        relative_path,
        f"planned {role} path",
    )
    installed_path = canonical_root.joinpath(*components)
    return PlannedArtifact(
        role,
        relative_path,
        installed_path,
        raw,
        hashlib.sha256(raw).hexdigest(),
        os.geteuid(),
        mode,
    )


def _planned_artifact_value(artifact: PlannedArtifact) -> dict[str, Any]:
    return {
        "role": artifact.role,
        "relative_path": artifact.relative_path,
        "installed_path": str(artifact.installed_path),
        "length": len(artifact.raw),
        "sha256": artifact.sha256,
        "owner": artifact.owner,
        "mode": artifact.mode,
    }


def _plan_first_install(
    source: CandidateSource,
    qualification: RuntimeQualification,
    precondition: FirstInstallPrecondition,
    maintenance_transaction_sha256: str,
) -> DeploymentPlan:
    """Build the complete nonreceipt artifact plan without filesystem mutation."""

    transaction_sha256 = _sha256(
        maintenance_transaction_sha256,
        "maintenance transaction digest",
    )
    canonical_root = precondition.canonical_root
    candidate_root = source.tree.root
    if canonical_root.is_relative_to(candidate_root) or candidate_root.is_relative_to(
        canonical_root
    ):
        raise DeploymentError(
            "deployment canonical root must be disjoint from the candidate source"
        )
    policy_raw = _candidate_file(
        source.tree,
        "controller/policy.json",
        "the candidate compatibility policy",
    )
    candidate_policy = _parse_compatibility_policy(policy_raw)
    if not _policy_covers_source(candidate_policy, source):
        raise DeploymentError(
            "candidate compatibility policy does not cover the planned source"
        )
    classification = _classify_candidate_source(
        active_source=None,
        active_policy=None,
        active_policy_sha256=None,
        candidate_source=source,
        candidate_policy_sha256=candidate_policy.raw_sha256,
    )
    active = _build_active_runtime(source, qualification)
    trust = _plan_trust_context(source, canonical_root)
    smoke_manifest_raw = _smoke_bundle_manifest(trust.smoke)
    artifacts = [
        _planned_artifact(
            "client",
            canonical_root,
            "client/task_witness_client.py",
            _candidate_file(
                source.tree,
                "client/task_witness_client.py",
                "the candidate client",
            ),
            0o500,
        ),
        _planned_artifact(
            "controller",
            canonical_root,
            "controller/task_witness_deploy.py",
            _candidate_file(
                source.tree,
                "controller/task_witness_deploy.py",
                "the candidate controller",
            ),
            0o500,
        ),
        _planned_artifact(
            "policy",
            canonical_root,
            "controller/policy.json",
            policy_raw,
            0o600,
        ),
        _planned_artifact(
            "launcher",
            canonical_root,
            "launcher/task_witness_launch.py",
            _candidate_file(
                source.tree,
                "launcher/task_witness_launch.py",
                "the candidate launcher",
            ),
            0o500,
        ),
        _planned_artifact(
            "smoke-bundle-manifest",
            canonical_root,
            "smoke/bundle/manifest.json",
            smoke_manifest_raw,
            0o600,
        ),
    ]
    for payload in active.payloads:
        relative_path = f"generations/{active.generation}/{payload['relative_path']}"
        artifacts.append(
            _planned_artifact(
                f"runtime-{payload['role']}",
                canonical_root,
                relative_path,
                active.files[payload["relative_path"]],
                0o600,
            )
        )
    retained_modules = [
        module
        for provider in (*trust.providers, trust.smoke)
        for module in provider.modules
    ]
    for module in retained_modules:
        try:
            relative_path = module.path.relative_to(canonical_root).as_posix()
        except ValueError as error:
            raise DeploymentError(
                "planned validator module is outside the canonical root"
            ) from error
        artifacts.append(
            _planned_artifact(
                "validator-module",
                canonical_root,
                relative_path,
                module.raw,
                0o600,
            )
        )
    artifacts.extend(
        (
            _planned_artifact(
                "trust-context",
                canonical_root,
                trust.context.path.relative_to(canonical_root).as_posix(),
                trust.context.raw,
                0o600,
            ),
            _planned_artifact(
                "active-record",
                canonical_root,
                "active.json",
                active.raw,
                0o600,
            ),
            _planned_artifact(
                "shim",
                canonical_root,
                "task-witness",
                render_pinned_shim(
                    _candidate_file(
                        source.tree,
                        "client/task_witness_shim.sh.in",
                        "the candidate shim template",
                    ),
                    Path(qualification.main_executable["path"]),
                    canonical_root / "client" / "task_witness_client.py",
                ),
                0o500,
            ),
        )
    )
    artifacts.sort(key=lambda item: item.relative_path)
    paths = [item.relative_path for item in artifacts]
    if len(paths) != len(set(paths)):
        raise DeploymentError("planned artifact paths conflict")
    plan_unsigned = {
        "contract": "task-witness-deployment-plan-v1",
        "operation": "first-install",
        "canonical_root": str(canonical_root),
        "effective_uid": os.geteuid(),
        "root_identity": list(precondition.root_identity),
        "activation_lock": _thaw(precondition.activation_lock),
        "deployment_receipt_absent": precondition.deployment_receipt_absent,
        "retained_result_sha256s": _thaw(precondition.retained_result_sha256s),
        "maintenance_transaction_sha256": transaction_sha256,
        "source": {
            "plugin_id": source.plugin_id,
            "mode": source.source_mode,
            "publisher_id": source.publisher_id,
            "manifest_author": _thaw(source.manifest_author),
            "repository_id": source.repository_id,
            "repository_url": source.repository_url,
            "release_version": source.release_version,
            "revision": source.revision,
            "subtree_sha256": source.subtree_sha256,
            "source_authority": source.source_authority,
            "details": _source_details_projection(source),
            "source_selection_sha256": source.source_selection_sha256,
            "source_evidence_sha256": source.source_evidence_sha256,
        },
        "runtime_qualification_content_sha256": qualification.content_sha256,
        "candidate_policy_sha256": candidate_policy.raw_sha256,
        "classification": {
            "outcome": classification.outcome,
            "reason": classification.reason,
        },
        "artifacts": [_planned_artifact_value(item) for item in artifacts],
    }
    plan_sha256 = _digest(plan_unsigned)
    return DeploymentPlan(
        source,
        qualification,
        precondition,
        classification,
        candidate_policy,
        active,
        trust,
        tuple(artifacts),
        transaction_sha256,
        plan_sha256,
        _freeze({**plan_unsigned, "plan_sha256": plan_sha256}),
    )


def _installed_binding(artifact: PlannedArtifact) -> dict[str, Any]:
    return {
        "path": str(artifact.installed_path),
        "length": len(artifact.raw),
        "sha256": artifact.sha256,
        "owner": artifact.owner,
        "mode": artifact.mode,
    }


def _artifact_for_role(plan: DeploymentPlan, role: str) -> PlannedArtifact:
    matches = [item for item in plan.artifacts if item.role == role]
    if len(matches) != 1:
        raise DeploymentError(f"deployment plan has ambiguous {role} artifact")
    return matches[0]


def _provider_receipt_projection(
    provider: ProviderMaterialization,
    *,
    intrinsic: bool,
) -> dict[str, Any]:
    modules = sorted(provider.modules, key=lambda item: str(item.path))
    return {
        "plugin_id": provider.plugin_id,
        "publisher": provider.publisher,
        "repository": provider.repository,
        "authority_profile": provider.authority_profile,
        "intrinsic": intrinsic,
        "declaration_sha256": provider.declaration_sha256,
        "declaration_content_sha256": provider.declaration_content_sha256,
        "producers": [_thaw(item) for item in provider.producers],
        "issuers": [_thaw(item) for item in provider.issuers],
        "validators": [_thaw(item) for item in provider.validators],
        "retained_modules": [
            {
                "name": item.name,
                "path": str(item.path),
                "length": len(item.raw),
                "sha256": item.sha256,
            }
            for item in modules
        ],
    }


def _smoke_identities(
    provider: ProviderMaterialization,
) -> tuple[dict[str, str], dict[str, str]]:
    if len(provider.producers) != 1 or len(provider.validators) != 1:
        raise DeploymentError("intrinsic smoke provider role inventory mismatch")
    producer = _exact(
        _thaw(provider.producers[0]),
        {
            "producer_id",
            "contract",
            "implementation_sha256",
            "validator_id",
            "validator_contract",
            "validator_implementation_sha256",
            "state",
            "usable_for_new_publication",
        },
        "intrinsic smoke producer",
    )
    validator = _exact(
        _thaw(provider.validators[0]),
        {
            "validator_id",
            "contract",
            "implementation_sha256",
            "entrypoint",
            "modules",
            "state",
            "usable_for_new_publication",
        },
        "intrinsic smoke validator",
    )
    if (
        producer["producer_id"] != SMOKE_PRODUCER_NAME
        or producer["contract"] != SMOKE_BUNDLE_CONTRACT
        or producer["validator_id"] != SMOKE_VALIDATOR_NAME
        or producer["validator_contract"] != SMOKE_BUNDLE_CONTRACT
        or validator["validator_id"] != SMOKE_VALIDATOR_NAME
        or validator["contract"] != SMOKE_BUNDLE_CONTRACT
        or producer["validator_implementation_sha256"]
        != validator["implementation_sha256"]
    ):
        raise DeploymentError("intrinsic smoke provider identity mismatch")
    producer_identity = {
        key: producer[key]
        for key in (
            "producer_id",
            "contract",
            "implementation_sha256",
            "validator_id",
            "validator_contract",
            "validator_implementation_sha256",
        )
    }
    validator_identity = {
        key: validator[key]
        for key in ("validator_id", "contract", "implementation_sha256")
    }
    return producer_identity, validator_identity


def _smoke_bundle_manifest(provider: ProviderMaterialization) -> bytes:
    producer, _ = _smoke_identities(provider)
    return _canonical_document(
        {
            "schema_version": 1,
            "contract": SMOKE_BUNDLE_CONTRACT,
            "producer": {
                key: producer[key]
                for key in ("producer_id", "contract", "implementation_sha256")
            },
            "challenge": SMOKE_CHALLENGE,
        }
    )


def _smoke_bundle_sha256(manifest_raw: bytes) -> str:
    return _digest(
        {
            "contract": BUNDLE_INVENTORY_CONTRACT,
            "files": [
                {
                    "name": "manifest.json",
                    "length": len(manifest_raw),
                    "sha256": hashlib.sha256(manifest_raw).hexdigest(),
                }
            ],
        }
    )


def _smoke_expected_result(
    *,
    bundle_sha256: str,
    trust_context_sha256: str,
    active_value: Mapping[str, Any],
    active_record_sha256: str,
    runtime_implementation_sha256: str,
    producer: Mapping[str, str],
    validator: Mapping[str, str],
) -> dict[str, Any]:
    projection = {
        "schema_version": 1,
        "contract": SMOKE_PROJECTION_CONTRACT,
        "challenge": SMOKE_CHALLENGE,
        "accepted": True,
    }
    anchor = {
        "contract": COMPLETE_ANCHOR_CONTRACT,
        "generation": active_value["generation"],
        "active_record_sha256": active_record_sha256,
        "runtime_contract": active_value["runtime_contract"],
        "interpreter": _thaw(active_value["interpreter"]),
        "public_release": _thaw(active_value["public_release"]),
        "runtime_implementation_sha256": runtime_implementation_sha256,
        "trust_context_sha256": trust_context_sha256,
        "bundle_sha256": bundle_sha256,
        "historical": False,
    }
    envelope = {
        "contract": ENVELOPE_CONTRACT,
        "anchor": anchor,
        "witness": {
            "contract": CANONICAL_PROJECTION_CONTRACT,
            "bundle_sha256": bundle_sha256,
            "producer": dict(producer),
            "validator": dict(validator),
            "projection": projection,
            "trust_context_sha256": trust_context_sha256,
            "historical": False,
        },
    }
    return {
        "expected_projection": projection,
        "expected_anchor": anchor,
        "expected_envelope_sha256": hashlib.sha256(
            _canonical_document(envelope)
        ).hexdigest(),
    }


def _smoke_receipt_projection(plan: DeploymentPlan) -> dict[str, Any]:
    manifest = _artifact_for_role(plan, "smoke-bundle-manifest")
    producer, validator = _smoke_identities(plan.trust.smoke)
    bundle_sha256 = _smoke_bundle_sha256(manifest.raw)
    active = plan.active
    return {
        "bundle": {
            "path": str(plan.precondition.canonical_root / "smoke" / "bundle"),
            "sha256": bundle_sha256,
            "manifest": _installed_binding(manifest),
        },
        "trust_context": {
            "path": str(plan.trust.context.path),
            "sha256": plan.trust.context.sha256,
        },
        "producer": producer,
        "validator": validator,
        **_smoke_expected_result(
            bundle_sha256=bundle_sha256,
            trust_context_sha256=plan.trust.context.sha256,
            active_value=active.value,
            active_record_sha256=active.sha256,
            runtime_implementation_sha256=(active.runtime_implementation_sha256),
            producer=producer,
            validator=validator,
        ),
    }


def _first_install_rollback_receipt(
    plan: DeploymentPlan,
) -> tuple[dict[str, Any], bytes]:
    unsigned = {
        "schema_version": 1,
        "contract": ROLLBACK_RECEIPT_CONTRACT,
        "state": "absent",
        "canonical_root": str(plan.precondition.canonical_root),
        "effective_uid": os.geteuid(),
        "activation_lock": _thaw(plan.precondition.activation_lock),
        "deployment_receipt_absent": True,
        "precondition": {
            "root_identity": list(plan.precondition.root_identity),
            "activation_lock_identity": list(
                plan.precondition.activation_lock_identity
            ),
        },
        "prior_activation_unit": [],
        "external_dependencies": [],
        "smoke": {
            "contract": FIRST_INSTALL_ROLLBACK_CONTRACT,
            "expected_state": "absent",
        },
    }
    value = {**unsigned, "content_sha256": _digest(unsigned)}
    return value, _canonical_document(value)


def _first_install_deployment_receipt(
    plan: DeploymentPlan,
    authorization: FirstInstallAuthorization,
    authorization_raw: bytes,
    rollback_path: Path,
    rollback_raw: bytes,
) -> tuple[dict[str, Any], bytes]:
    controls = {
        role: _installed_binding(_artifact_for_role(plan, role))
        for role in ("shim", "client", "launcher", "controller", "policy")
    }
    qualification = plan.qualification
    executable = qualification.main_executable
    active = plan.active
    trust = plan.trust
    source = plan.source
    policy_artifact = _artifact_for_role(plan, "policy")
    control_surface = plan.candidate_policy.control_surface
    providers = [
        _provider_receipt_projection(item, intrinsic=False) for item in trust.providers
    ]
    providers.append(_provider_receipt_projection(trust.smoke, intrinsic=True))
    providers.sort(key=lambda item: (item["plugin_id"], item["intrinsic"]))
    unsigned = {
        "schema_version": 2,
        "contract": DEPLOYMENT_RECEIPT_CONTRACT,
        "sequence": 1,
        "prior_receipt_sha256": None,
        "canonical_root": str(plan.precondition.canonical_root),
        "effective_uid": os.geteuid(),
        "activation_lock": _thaw(plan.precondition.activation_lock),
        "control_set": controls,
        "interpreter": {
            "executable": executable["path"],
            "implementation": executable["implementation"],
            "version": _thaw(executable["version"]),
            "executable_sha256": executable["sha256"],
        },
        "process_profile": _thaw(control_surface["process_profile"]),
        "active": {
            "record_path": str(plan.precondition.canonical_root / "active.json"),
            "record_sha256": active.sha256,
            "generation": active.generation,
            "runtime_contract": RUNTIME_CONTRACT,
            "runtime_implementation_sha256": active.runtime_implementation_sha256,
            "public_release": {
                "repository": source.repository_id,
                "revision": source.revision,
            },
        },
        "trust_context": {
            "path": str(trust.context.path),
            "sha256": trust.context.sha256,
        },
        "historical_trust_contexts": [],
        "platform": _thaw(qualification.platform),
        "source": {
            "mode": source.source_mode,
            "plugin_id": source.plugin_id,
            "publisher_id": source.publisher_id,
            "manifest_author": _thaw(source.manifest_author),
            "repository_id": source.repository_id,
            "repository_url": source.repository_url,
            "release_version": source.release_version,
            "revision": source.revision,
            "subtree_sha256": source.subtree_sha256,
            "source_authority": source.source_authority,
            "details": _source_details_projection(source),
            "source_selection_sha256": source.source_selection_sha256,
            "source_selection_content_sha256": (source.source_selection_content_sha256),
            "source_evidence": _source_evidence_projection(source),
            "agent_plugin_manifest_sha256": (source.agent_plugin_manifest_sha256),
            "claude_manifest_sha256": source.claude_manifest_sha256,
            "provider_declaration_sha256": source.provider_declaration_sha256,
            "provider_declaration_content_sha256": (
                source.provider_declaration_content_sha256
            ),
        },
        "runtime_closure": {
            **_thaw(qualification.runtime_closure),
            "dependency_classes": list(qualification.dependency_classes),
            "qualification_content_sha256": qualification.content_sha256,
        },
        "contracts": {
            key: control_surface["contracts"][key] for key in RECEIPT_CONTRACTS
        },
        "providers": providers,
        "role_inventory": {
            category: _thaw(trust.context.value[category])
            for category in ("producers", "issuers", "validators")
        },
        "smoke": _smoke_receipt_projection(plan),
        "compatibility_policy": {
            **_installed_binding(policy_artifact),
            "content_sha256": plan.candidate_policy.content_sha256,
        },
        "authorization": {
            "contract": DEPLOYER_AUTHORIZATION_CONTRACT,
            "purpose": "first-install",
            "sha256": hashlib.sha256(authorization_raw).hexdigest(),
            "content_sha256": authorization.content_sha256,
            "plan_sha256": authorization.plan_sha256,
            "maintenance_transaction_sha256": (
                authorization.maintenance_transaction_sha256
            ),
        },
        "rollback": {
            "state": "absent",
            "path": str(rollback_path),
            "sha256": hashlib.sha256(rollback_raw).hexdigest(),
        },
    }
    value = {**unsigned, "content_sha256": _digest(unsigned)}
    return value, _canonical_document(value)


def _write_staged_artifact(
    stage: _StageRoot,
    artifact: PlannedArtifact,
) -> StagedArtifact:
    components = tuple(artifact.relative_path.split("/"))
    current = os.dup(stage.fd)
    directories = [current]
    try:
        for component in components[:-1]:
            _recheck_disjoint_private_stage_root(stage)
            child = _open_private_directory(
                current,
                component,
                f"staged {artifact.role} directory",
            )
            _recheck_disjoint_private_stage_root(stage)
            directories.append(child)
            current = child
        _recheck_disjoint_private_stage_root(stage)
        _create_private_file(
            current,
            components[-1],
            artifact.raw,
            f"staged {artifact.role}",
        )
        _recheck_disjoint_private_stage_root(stage)
        metadata = os.stat(
            components[-1],
            dir_fd=current,
            follow_symlinks=False,
        )
        staged_path = stage.path.joinpath(*components)
        staged = {
            "path": str(staged_path),
            "length": len(artifact.raw),
            "sha256": artifact.sha256,
            "owner": metadata.st_uid,
            "mode": stat.S_IMODE(metadata.st_mode),
        }
        if (
            metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or staged["mode"] != 0o600
        ):
            raise DeploymentError(f"staged {artifact.role} disposition mismatch")
        return StagedArtifact(
            artifact.role,
            artifact.relative_path,
            staged_path,
            artifact.installed_path,
            artifact.raw,
            _freeze(staged),
            _freeze(_installed_binding(artifact)),
        )
    finally:
        for descriptor in reversed(directories):
            os.close(descriptor)


def _write_staged_transition_evidence(
    stage: _StageRoot,
    *,
    role: str,
    relative_path: str,
    raw: bytes,
) -> StagedTransitionEvidence:
    relative, components = _relative_path(
        relative_path,
        f"staged bridge {role} path",
    )
    if len(components) != 1:
        raise DeploymentError(f"staged bridge {role} path disagrees")
    _recheck_disjoint_private_stage_root(stage)
    _create_private_file(
        stage.fd,
        components[0],
        raw,
        f"staged bridge {role}",
    )
    _recheck_disjoint_private_stage_root(stage)
    metadata = os.stat(
        components[0],
        dir_fd=stage.fd,
        follow_symlinks=False,
    )
    staged_path = stage.path / components[0]
    staged = {
        "path": str(staged_path),
        "length": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "owner": metadata.st_uid,
        "mode": stat.S_IMODE(metadata.st_mode),
    }
    if (
        metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or staged["mode"] != 0o600
    ):
        raise DeploymentError(f"staged bridge {role} disposition mismatch")
    return StagedTransitionEvidence(
        role,
        relative,
        staged_path,
        raw,
        _freeze(staged),
    )


def _verify_staged_inventory(
    staging_root: Path,
    expected_files: Mapping[str, bytes],
) -> None:
    snapshot = _snapshot_candidate_tree(staging_root)
    if dict(snapshot.files) != dict(expected_files):
        raise DeploymentError("staged deployment file inventory disagrees")
    expected_directories: set[str] = set()
    for relative_path in expected_files:
        parts = relative_path.split("/")
        for index in range(1, len(parts)):
            expected_directories.add("/".join(parts[:index]))
    expected_paths = expected_directories | set(expected_files)
    observed_paths = {item["path"] for item in snapshot.entries}
    if observed_paths != expected_paths:
        raise DeploymentError("staged deployment directory inventory disagrees")


def _materialize_first_install(
    plan: DeploymentPlan,
    authorization_raw: bytes,
    staging_root: Path,
) -> StagedDeployment:
    """Create and verify only a disjoint inert stage for an authorized plan."""

    canonical_root = plan.precondition.canonical_root
    candidate_root = plan.source.tree.root
    stage_path = _normalized_absolute_path(staging_root, "deployment staging root")
    if (
        stage_path.is_relative_to(canonical_root)
        or canonical_root.is_relative_to(stage_path)
        or stage_path.is_relative_to(candidate_root)
        or candidate_root.is_relative_to(stage_path)
    ):
        raise DeploymentError(
            "deployment staging root must be disjoint from installation and source"
        )
    controller = _artifact_for_role(plan, "controller")
    policy = _artifact_for_role(plan, "policy")
    authorization = _validate_first_install_authorization(
        authorization_raw,
        canonical_root=str(canonical_root),
        effective_uid=os.geteuid(),
        plan_sha256=plan.plan_sha256,
        maintenance_transaction_sha256=plan.maintenance_transaction_sha256,
        candidate_controller_sha256=controller.sha256,
        candidate_policy_sha256=policy.sha256,
        source_selection_sha256=plan.source.source_selection_sha256,
        source_evidence_sha256=plan.source.source_evidence_sha256,
    )
    rollback_value, rollback_raw = _first_install_rollback_receipt(plan)
    rollback_sha256 = hashlib.sha256(rollback_raw).hexdigest()
    rollback_relative = f"receipts/sha256-{rollback_sha256}.json"
    rollback_path = canonical_root / rollback_relative
    rollback_artifact = _planned_artifact(
        "rollback-receipt",
        canonical_root,
        rollback_relative,
        rollback_raw,
        0o600,
    )
    deployment_value, deployment_raw = _first_install_deployment_receipt(
        plan,
        authorization,
        authorization_raw,
        rollback_path,
        rollback_raw,
    )
    deployment_sha256 = hashlib.sha256(deployment_raw).hexdigest()
    deployment_receipt_artifact = _planned_artifact(
        "deployment-receipt",
        canonical_root,
        f"receipts/sha256-{deployment_sha256}.json",
        deployment_raw,
        0o600,
    )
    deployment_alias_artifact = _planned_artifact(
        "deployment-alias",
        canonical_root,
        "deployment.json",
        deployment_raw,
        0o600,
    )
    complete_artifacts = sorted(
        (
            *plan.artifacts,
            rollback_artifact,
            deployment_receipt_artifact,
            deployment_alias_artifact,
        ),
        key=lambda item: item.relative_path,
    )
    stage = _open_disjoint_private_stage_root(
        stage_path,
        canonical_root=canonical_root,
        candidate_root=candidate_root,
    )
    canonical_stage_path = stage.path
    try:
        staged_artifacts = tuple(
            _write_staged_artifact(
                stage,
                artifact,
            )
            for artifact in complete_artifacts
        )
        classification = Classification(
            "authorized-first-install",
            "exact-deployer-authorization",
        )
        stage_unsigned = {
            "schema_version": 1,
            "contract": STAGED_DEPLOYMENT_CONTRACT,
            "staging_root": str(canonical_stage_path),
            "canonical_root": str(canonical_root),
            "plan_sha256": plan.plan_sha256,
            "maintenance_transaction_sha256": (plan.maintenance_transaction_sha256),
            "classification": {
                "outcome": classification.outcome,
                "reason": classification.reason,
            },
            "authorization": {
                "sha256": hashlib.sha256(authorization_raw).hexdigest(),
                "content_sha256": authorization.content_sha256,
            },
            "rollback_receipt": {
                "path": str(rollback_path),
                "sha256": rollback_sha256,
            },
            "deployment_receipt": {
                "path": str(deployment_receipt_artifact.installed_path),
                "sha256": deployment_sha256,
            },
            "artifacts": [
                {
                    "role": item.role,
                    "relative_path": item.relative_path,
                    "staged": _thaw(item.staged),
                    "installed": _thaw(item.installed),
                }
                for item in staged_artifacts
            ],
        }
        stage_value = {
            **stage_unsigned,
            "content_sha256": _digest(stage_unsigned),
        }
        stage_raw = _canonical_document(stage_value)
        _recheck_disjoint_private_stage_root(stage)
        _create_private_file(
            stage.fd,
            "stage.json",
            stage_raw,
            "staged deployment receipt",
        )
        _recheck_disjoint_private_stage_root(stage)
        os.fsync(stage.fd)
        _recheck_disjoint_private_stage_root(stage)
    finally:
        _close_disjoint_private_stage_root(stage)
    expected_files = {item.relative_path: item.raw for item in staged_artifacts}
    expected_files["stage.json"] = stage_raw
    _verify_staged_inventory(canonical_stage_path, expected_files)
    current_source = _snapshot_candidate_tree(plan.source.tree.root)
    if current_source.subtree_sha256 != plan.source.tree.subtree_sha256 or dict(
        current_source.files
    ) != dict(plan.source.tree.files):
        raise DeploymentError("candidate source changed after planning")
    executable_raw = _capture_absolute_regular(
        Path(plan.qualification.main_executable["path"]),
        MAX_INTERPRETER_BYTES,
        "runtime qualification executable",
    )
    if (
        hashlib.sha256(executable_raw).hexdigest()
        != plan.qualification.main_executable["sha256"]
    ):
        raise DeploymentError("runtime qualification changed after planning")
    current_precondition = _capture_first_install_precondition(canonical_root)
    if current_precondition != plan.precondition:
        raise DeploymentError("first-install precondition changed after staging")
    return StagedDeployment(
        plan,
        authorization,
        classification,
        _freeze(rollback_value),
        rollback_raw,
        _freeze(deployment_value),
        deployment_raw,
        staged_artifacts,
        _freeze(stage_value),
        stage_raw,
        canonical_stage_path / "stage.json",
    )


def _routine_raw_binding(path: Path, raw: bytes, mode: int) -> dict[str, Any]:
    return {
        "path": str(path),
        "length": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "owner": os.geteuid(),
        "mode": mode,
    }


def _routine_selector_preimage(
    plan: RoutineDeploymentPlan | ControlSetDeploymentPlan | ManualRollbackPlan,
    staging_root: Path,
) -> list[dict[str, Any]]:
    precondition = plan.precondition
    root = precondition.canonical_root
    return [
        {
            "role": "active-record",
            "staged": _routine_raw_binding(
                staging_root / "preimage" / "active.json",
                precondition.active_raw,
                0o600,
            ),
            "installed": _routine_raw_binding(
                root / "active.json",
                precondition.active_raw,
                0o600,
            ),
        },
        {
            "role": "deployment-alias",
            "staged": _routine_raw_binding(
                staging_root / "preimage" / "deployment.json",
                precondition.receipt_raw,
                0o600,
            ),
            "installed": _routine_raw_binding(
                root / "deployment.json",
                precondition.receipt_raw,
                0o600,
            ),
        },
    ]


def _control_relative_path(role: str) -> str:
    paths = {
        "controller": "controller/task_witness_deploy.py",
        "policy": "controller/policy.json",
        "launcher": "launcher/task_witness_launch.py",
        "client": "client/task_witness_client.py",
        "smoke-bundle-manifest": "smoke/bundle/manifest.json",
        "shim": "task-witness",
    }
    try:
        return paths[role]
    except KeyError as error:
        raise DeploymentError("control preimage role is unsupported") from error


def _control_preimage(
    plan: ControlSetDeploymentPlan | ManualRollbackPlan,
    staging_root: Path,
) -> list[dict[str, Any]]:
    precondition = plan.precondition
    receipt = precondition.receipt_value
    prior_bindings = {
        **_thaw(receipt["control_set"]),
        "smoke-bundle-manifest": _thaw(receipt["smoke"]["bundle"]["manifest"]),
    }
    return [
        {
            "role": role,
            "staged": _routine_raw_binding(
                staging_root / "preimage" / _control_relative_path(role),
                precondition.control_raws[role],
                0o600,
            ),
            "installed": _thaw(prior_bindings[role]),
        }
        for role in CONTROL_PREIMAGE_ROLES
    ]


def _routine_external_dependencies(
    precondition: ActiveDeploymentPrecondition,
) -> dict[str, Any]:
    receipt = precondition.receipt_value
    controls = receipt["control_set"]
    contracts = receipt["contracts"]
    return {
        "interpreter": _thaw(receipt["interpreter"]),
        "runtime_closure": _thaw(receipt["runtime_closure"]),
        "process_profile": _thaw(receipt["process_profile"]),
        "receipt_parser": {
            "deployment_receipt_contract": contracts["deployment_receipt"],
            "rollback_receipt_contract": contracts["rollback_receipt"],
            "controller": _thaw(controls["controller"]),
            "client": _thaw(controls["client"]),
        },
    }


def _routine_rollback_receipt(
    plan: RoutineDeploymentPlan | ControlSetDeploymentPlan,
    staging_root: Path,
) -> tuple[dict[str, Any], bytes]:
    precondition = plan.precondition
    root = precondition.canonical_root
    prior_path = root / "receipts" / (f"sha256-{precondition.receipt_sha256}.json")
    unsigned = {
        "schema_version": 1,
        "contract": ROLLBACK_RECEIPT_CONTRACT,
        "state": "active",
        "canonical_root": str(root),
        "effective_uid": os.geteuid(),
        "activation_lock": _thaw(precondition.activation_lock),
        "deployment_receipt_absent": False,
        "precondition": {
            "root_identity": list(precondition.root_identity),
            "activation_lock_identity": list(precondition.activation_lock_identity),
            "active_receipt_sha256": precondition.receipt_sha256,
        },
        "prior_receipt": _routine_raw_binding(
            prior_path,
            precondition.receipt_raw,
            0o600,
        ),
        "prior_activation_unit": _thaw(precondition.active_unit),
        "selector_preimage": _routine_selector_preimage(plan, staging_root),
        "external_dependencies": _routine_external_dependencies(precondition),
        "smoke": _thaw(precondition.receipt_value["smoke"]),
    }
    if type(plan) is ControlSetDeploymentPlan:
        unsigned["control_preimage"] = _control_preimage(plan, staging_root)
    value = {**unsigned, "content_sha256": _digest(unsigned)}
    return value, _canonical_document(value)


def _routine_deployment_receipt(
    plan: RoutineDeploymentPlan | ControlSetDeploymentPlan,
    authorization: DeploymentAuthorization,
    authorization_raw: bytes,
    rollback_path: Path,
    rollback_raw: bytes,
    *,
    authorization_purpose: str = "routine-compatible-forward",
) -> tuple[dict[str, Any], bytes]:
    value, _ = _first_install_deployment_receipt(
        plan,
        authorization,
        authorization_raw,
        rollback_path,
        rollback_raw,
    )
    prior = plan.precondition.receipt_value
    prior_trust = _exact(
        _thaw(prior["trust_context"]),
        {"path", "sha256"},
        "routine prior trust context",
    )
    candidate_trust = _exact(
        _thaw(value["trust_context"]),
        {"path", "sha256"},
        "routine candidate trust context",
    )
    prior_history = _retained_historical_trust_registry(
        _thaw(prior["historical_trust_contexts"]),
        plan.precondition.canonical_root,
        "routine prior historical trust contexts",
    )
    expected_history = _expected_routine_historical_trust(
        current_trust=candidate_trust,
        prior_trust=prior_trust,
        prior_history=prior_history,
        label="routine deployment history",
    )
    historical = [expected_history[digest] for digest in sorted(expected_history)]
    unsigned = {
        **{
            key: item
            for key, item in value.items()
            if key
            not in {
                "sequence",
                "prior_receipt_sha256",
                "authorization",
                "rollback",
                "historical_trust_contexts",
                "content_sha256",
            }
        },
        "sequence": prior["sequence"] + 1,
        "prior_receipt_sha256": plan.precondition.receipt_sha256,
        "historical_trust_contexts": historical,
        "authorization": {
            "contract": DEPLOYER_AUTHORIZATION_CONTRACT,
            "purpose": authorization_purpose,
            "sha256": hashlib.sha256(authorization_raw).hexdigest(),
            "content_sha256": authorization.content_sha256,
            "plan_sha256": authorization.plan_sha256,
            "maintenance_transaction_sha256": (
                authorization.maintenance_transaction_sha256
            ),
            "expected_active_receipt_sha256": (
                authorization.expected_active_receipt_sha256
            ),
        },
        "rollback": {
            "state": "active",
            "path": str(rollback_path),
            "sha256": hashlib.sha256(rollback_raw).hexdigest(),
        },
    }
    result = {**unsigned, "content_sha256": _digest(unsigned)}
    return result, _canonical_document(result)


def _manual_rollback_receipts(
    plan: ManualRollbackPlan,
    authorization: RollbackToAuthorization,
    authorization_raw: bytes,
    staging_root: Path,
) -> tuple[dict[str, Any], bytes, dict[str, Any], bytes]:
    precondition = plan.precondition
    root = precondition.canonical_root
    rollback_unsigned = {
        "schema_version": 1,
        "contract": ROLLBACK_RECEIPT_CONTRACT,
        "state": "active",
        "canonical_root": str(root),
        "effective_uid": os.geteuid(),
        "activation_lock": _thaw(precondition.activation_lock),
        "deployment_receipt_absent": False,
        "precondition": {
            "root_identity": list(precondition.root_identity),
            "activation_lock_identity": list(precondition.activation_lock_identity),
            "active_receipt_sha256": precondition.receipt_sha256,
        },
        "prior_receipt": _routine_raw_binding(
            root / "receipts" / f"sha256-{precondition.receipt_sha256}.json",
            precondition.receipt_raw,
            0o600,
        ),
        "prior_activation_unit": _thaw(precondition.active_unit),
        "selector_preimage": _routine_selector_preimage(plan, staging_root),
        "control_preimage": _control_preimage(plan, staging_root),
        "external_dependencies": _routine_external_dependencies(precondition),
        "smoke": _thaw(precondition.receipt_value["smoke"]),
    }
    rollback_value = {
        **rollback_unsigned,
        "content_sha256": _digest(rollback_unsigned),
    }
    rollback_raw = _canonical_document(rollback_value)
    rollback_sha256 = hashlib.sha256(rollback_raw).hexdigest()
    rollback_path = root / "receipts" / f"sha256-{rollback_sha256}.json"

    target = _thaw(plan.target_authority.receipt_value)
    current = precondition.receipt_value
    current_trust = _trust_context_binding_shape(
        _thaw(target["trust_context"]),
        root,
        "manual rollback target trust context",
    )
    prior_trust = _trust_context_binding_shape(
        _thaw(current["trust_context"]),
        root,
        "manual rollback prior trust context",
    )
    prior_history = _retained_historical_trust_registry(
        _thaw(current["historical_trust_contexts"]),
        root,
        "manual rollback prior historical trust contexts",
    )
    expected_history = _expected_routine_historical_trust(
        current_trust=current_trust,
        prior_trust=prior_trust,
        prior_history=prior_history,
        label="manual rollback deployment history",
    )
    authorization_value = {
        "contract": DEPLOYER_AUTHORIZATION_CONTRACT,
        "purpose": "manual-exact-target-rollback",
        "sha256": hashlib.sha256(authorization_raw).hexdigest(),
        "content_sha256": authorization.content_sha256,
        "plan_sha256": authorization.plan_sha256,
        "maintenance_transaction_sha256": (
            authorization.maintenance_transaction_sha256
        ),
        "expected_active_receipt_sha256": (
            authorization.expected_active_receipt_sha256
        ),
        "target_receipt_sha256": authorization.target_receipt_sha256,
    }
    deployment_unsigned = {
        **{
            key: value
            for key, value in target.items()
            if key
            not in {
                "sequence",
                "prior_receipt_sha256",
                "historical_trust_contexts",
                "authorization",
                "rollback",
                "content_sha256",
            }
        },
        "sequence": current["sequence"] + 1,
        "prior_receipt_sha256": precondition.receipt_sha256,
        "historical_trust_contexts": [
            expected_history[digest] for digest in sorted(expected_history)
        ],
        "authorization": authorization_value,
        "rollback": {
            "state": "active",
            "path": str(rollback_path),
            "sha256": rollback_sha256,
        },
    }
    deployment_value = {
        **deployment_unsigned,
        "content_sha256": _digest(deployment_unsigned),
    }
    deployment_raw = _canonical_document(deployment_value)
    return rollback_value, rollback_raw, deployment_value, deployment_raw


def _manual_rollback_stage_artifacts(
    plan: ManualRollbackPlan,
    *,
    rollback_raw: bytes,
    deployment_raw: bytes,
) -> list[PlannedArtifact]:
    root = plan.precondition.canonical_root
    target = plan.target_authority
    target_bindings = _manual_rollback_target_control_bindings(target.receipt_value)
    prior_bindings = {
        **_thaw(plan.precondition.receipt_value["control_set"]),
        "smoke-bundle-manifest": _thaw(
            plan.precondition.receipt_value["smoke"]["bundle"]["manifest"]
        ),
    }
    artifacts: list[PlannedArtifact] = []
    for role in CONTROL_PREIMAGE_ROLES:
        artifacts.append(
            _routine_planned_artifact(
                role,
                f"candidate/{_control_relative_path(role)}",
                Path(target_bindings[role]["path"]),
                target.control_raws[role],
                target_bindings[role]["mode"],
            )
        )
        artifacts.append(
            _routine_planned_artifact(
                f"prior-{role}",
                f"preimage/{_control_relative_path(role)}",
                Path(prior_bindings[role]["path"]),
                plan.precondition.control_raws[role],
                prior_bindings[role]["mode"],
            )
        )
    rollback_sha256 = hashlib.sha256(rollback_raw).hexdigest()
    deployment_sha256 = hashlib.sha256(deployment_raw).hexdigest()
    artifacts.extend(
        (
            _routine_planned_artifact(
                "active-record",
                "candidate/active.json",
                root / "active.json",
                target.active_raw,
                0o600,
            ),
            _routine_planned_artifact(
                "deployment-alias",
                "candidate/deployment.json",
                root / "deployment.json",
                deployment_raw,
                0o600,
            ),
            _routine_planned_artifact(
                "prior-active-record",
                "preimage/active.json",
                root / "active.json",
                plan.precondition.active_raw,
                0o600,
            ),
            _routine_planned_artifact(
                "prior-deployment-alias",
                "preimage/deployment.json",
                root / "deployment.json",
                plan.precondition.receipt_raw,
                0o600,
            ),
            _routine_planned_artifact(
                "rollback-receipt",
                f"receipts/sha256-{rollback_sha256}.json",
                root / "receipts" / f"sha256-{rollback_sha256}.json",
                rollback_raw,
                0o600,
            ),
            _routine_planned_artifact(
                "deployment-receipt",
                f"receipts/sha256-{deployment_sha256}.json",
                root / "receipts" / f"sha256-{deployment_sha256}.json",
                deployment_raw,
                0o600,
            ),
        )
    )
    artifacts.sort(key=lambda item: item.relative_path)
    return artifacts


def _routine_planned_artifact(
    role: str,
    staged_relative_path: str,
    installed_path: Path,
    raw: bytes,
    mode: int,
) -> PlannedArtifact:
    relative, _ = _relative_path(staged_relative_path, f"routine staged {role} path")
    return PlannedArtifact(
        role,
        relative,
        installed_path,
        raw,
        hashlib.sha256(raw).hexdigest(),
        os.geteuid(),
        mode,
    )


def _control_maintenance_stage_artifacts(
    plan: ControlSetDeploymentPlan,
    *,
    deployment_raw: bytes,
    deployment_sha256: str,
    rollback_raw: bytes,
    rollback_sha256: str,
) -> list[PlannedArtifact]:
    canonical_root = plan.precondition.canonical_root
    excluded = {*CONTROL_PREIMAGE_ROLES, "active-record"}
    artifacts = [item for item in plan.artifacts if item.role not in excluded]
    for role in CONTROL_PREIMAGE_ROLES:
        candidate = _artifact_for_role(plan, role)
        artifacts.append(
            _routine_planned_artifact(
                role,
                f"candidate/{_control_relative_path(role)}",
                candidate.installed_path,
                candidate.raw,
                candidate.mode,
            )
        )
    prior_bindings = {
        **_thaw(plan.precondition.receipt_value["control_set"]),
        "smoke-bundle-manifest": _thaw(
            plan.precondition.receipt_value["smoke"]["bundle"]["manifest"]
        ),
    }
    for role in CONTROL_PREIMAGE_ROLES:
        binding = prior_bindings[role]
        artifacts.append(
            _routine_planned_artifact(
                f"prior-{role}",
                f"preimage/{_control_relative_path(role)}",
                Path(binding["path"]),
                plan.precondition.control_raws[role],
                binding["mode"],
            )
        )
    rollback_path = canonical_root / "receipts" / (f"sha256-{rollback_sha256}.json")
    artifacts.extend(
        (
            _routine_planned_artifact(
                "active-record",
                "candidate/active.json",
                canonical_root / "active.json",
                plan.active.raw,
                0o600,
            ),
            _routine_planned_artifact(
                "deployment-alias",
                "candidate/deployment.json",
                canonical_root / "deployment.json",
                deployment_raw,
                0o600,
            ),
            _routine_planned_artifact(
                "prior-active-record",
                "preimage/active.json",
                canonical_root / "active.json",
                plan.precondition.active_raw,
                0o600,
            ),
            _routine_planned_artifact(
                "prior-deployment-alias",
                "preimage/deployment.json",
                canonical_root / "deployment.json",
                plan.precondition.receipt_raw,
                0o600,
            ),
            _routine_planned_artifact(
                "rollback-receipt",
                f"receipts/sha256-{rollback_sha256}.json",
                rollback_path,
                rollback_raw,
                0o600,
            ),
            _routine_planned_artifact(
                "deployment-receipt",
                f"receipts/sha256-{deployment_sha256}.json",
                canonical_root / "receipts" / f"sha256-{deployment_sha256}.json",
                deployment_raw,
                0o600,
            ),
        )
    )
    artifacts.sort(key=lambda item: item.relative_path)
    return artifacts


def _bridge_migration_projection(
    authority: _BridgeStageAuthority,
    deployment_authorization_raw: bytes,
) -> dict[str, Any]:
    authorization = authority.transition_authorization
    projection = {
        "schema_version": 1,
        "contract": BRIDGE_MIGRATION_PROJECTION_CONTRACT,
        "edge": {"from": "freeze5", "to": "tw4", "via": "bridge"},
        "purpose": "bridge-transition",
        "execution_class": authorization["execution_class"],
        "maintenance_transaction_sha256": authorization[
            "maintenance_transaction_sha256"
        ],
        "deployment_authorization_sha256": hashlib.sha256(
            deployment_authorization_raw
        ).hexdigest(),
        "transition_authorization_sha256": hashlib.sha256(
            authority.transition_authorization_raw
        ).hexdigest(),
        "expected_active_receipt_core_sha256": authorization[
            "expected_active_receipt_core_sha256"
        ],
        "bridge_identity_sha256": authorization["bridge_identity_sha256"],
        "release_manifest_sha256": authorization["release_manifest_sha256"],
        "endpoint_projection_sha256": authorization["endpoint_projection_sha256"],
    }
    if authorization["execution_class"] == "live-migration":
        projection["prior_rehearsal"] = _thaw(authorization["prior_rehearsal"])
    return projection


def _materialize_routine_deployment(
    prepared: PreparedDeployment,
    authorization_raw: bytes,
    staging_root: Path,
    *,
    bridge_authority: _BridgeStageAuthority | None = None,
) -> StagedDeployment:
    plan = prepared.plan
    control_maintenance = type(plan) is ControlSetDeploymentPlan
    authorization_purpose = _active_prior_authorization_purpose(
        plan.classification,
        control_maintenance=control_maintenance,
    )
    authorization = _validate_deployment_authorization(
        authorization_raw,
        prepared.authorization_facts,
        expected_purpose=authorization_purpose,
    )
    canonical_root = plan.precondition.canonical_root
    candidate_root = plan.source.tree.root
    stage_path = _normalized_absolute_path(staging_root, "routine staging root")
    if any(
        left.is_relative_to(right) or right.is_relative_to(left)
        for left, right in (
            (stage_path, canonical_root),
            (stage_path, candidate_root),
        )
    ):
        raise DeploymentError(
            "routine staging root must be disjoint from installation and source"
        )
    rollback_value, rollback_raw = _routine_rollback_receipt(plan, stage_path)
    rollback_sha256 = hashlib.sha256(rollback_raw).hexdigest()
    rollback_path = canonical_root / "receipts" / f"sha256-{rollback_sha256}.json"
    deployment_value, deployment_raw = _routine_deployment_receipt(
        plan,
        authorization,
        authorization_raw,
        rollback_path,
        rollback_raw,
        authorization_purpose=authorization_purpose,
    )
    if bridge_authority is not None:
        receipt_core = {
            key: value
            for key, value in deployment_value.items()
            if key != "content_sha256"
        }
        if (
            _digest(receipt_core)
            != bridge_authority.facts.expected_active_receipt_core_sha256
        ):
            raise DeploymentError("bridge transition active receipt core disagrees")
        deployment_unsigned = {
            **receipt_core,
            "migration": _bridge_migration_projection(
                bridge_authority,
                authorization_raw,
            ),
        }
        deployment_value = {
            **deployment_unsigned,
            "content_sha256": _digest(deployment_unsigned),
        }
        deployment_raw = _canonical_document(deployment_value)
    deployment_sha256 = hashlib.sha256(deployment_raw).hexdigest()
    if control_maintenance:
        artifacts = _control_maintenance_stage_artifacts(
            plan,
            deployment_raw=deployment_raw,
            deployment_sha256=deployment_sha256,
            rollback_raw=rollback_raw,
            rollback_sha256=rollback_sha256,
        )
    else:
        controls = {
            "shim",
            "client",
            "launcher",
            "controller",
            "policy",
            "active-record",
        }
        artifacts = [item for item in plan.artifacts if item.role not in controls]
        artifacts.extend(
            (
                _routine_planned_artifact(
                    "active-record",
                    "candidate/active.json",
                    canonical_root / "active.json",
                    plan.active.raw,
                    0o600,
                ),
                _routine_planned_artifact(
                    "deployment-alias",
                    "candidate/deployment.json",
                    canonical_root / "deployment.json",
                    deployment_raw,
                    0o600,
                ),
                _routine_planned_artifact(
                    "prior-active-record",
                    "preimage/active.json",
                    canonical_root / "active.json",
                    plan.precondition.active_raw,
                    0o600,
                ),
                _routine_planned_artifact(
                    "prior-deployment-alias",
                    "preimage/deployment.json",
                    canonical_root / "deployment.json",
                    plan.precondition.receipt_raw,
                    0o600,
                ),
                _routine_planned_artifact(
                    "rollback-receipt",
                    f"receipts/sha256-{rollback_sha256}.json",
                    rollback_path,
                    rollback_raw,
                    0o600,
                ),
                _routine_planned_artifact(
                    "deployment-receipt",
                    f"receipts/sha256-{deployment_sha256}.json",
                    canonical_root / "receipts" / f"sha256-{deployment_sha256}.json",
                    deployment_raw,
                    0o600,
                ),
            )
        )
        artifacts.sort(key=lambda item: item.relative_path)
    stage = _open_disjoint_private_stage_root(
        stage_path,
        canonical_root=canonical_root,
        candidate_root=candidate_root,
    )
    try:
        staged_artifacts = tuple(
            _write_staged_artifact(stage, artifact) for artifact in artifacts
        )
        transition_evidence = (
            (
                _write_staged_transition_evidence(
                    stage,
                    role="manifest",
                    relative_path="bridge-transition-manifest.json",
                    raw=bridge_authority.release_manifest_raw,
                ),
                _write_staged_transition_evidence(
                    stage,
                    role="authorization",
                    relative_path="bridge-transition-authorization.json",
                    raw=bridge_authority.transition_authorization_raw,
                ),
            )
            if bridge_authority is not None
            else ()
        )
        classification = (
            Classification(
                "authorized-bridge-transition",
                "exact-bridge-transition-authorization",
            )
            if bridge_authority is not None
            else Classification(
                "authorized-control-set-maintenance",
                "exact-deployer-authorization",
            )
            if control_maintenance
            else Classification(
                "authorized-routine-payload",
                "active-policy-compatible-forward",
            )
        )
        unsigned = {
            "schema_version": 1,
            "contract": STAGED_DEPLOYMENT_CONTRACT,
            "staging_root": str(stage.path),
            "canonical_root": str(canonical_root),
            "plan_sha256": plan.plan_sha256,
            "maintenance_transaction_sha256": plan.maintenance_transaction_sha256,
            "classification": {
                "outcome": classification.outcome,
                "reason": classification.reason,
            },
            "authorization": {
                "sha256": hashlib.sha256(authorization_raw).hexdigest(),
                "content_sha256": authorization.content_sha256,
            },
            "rollback_receipt": {
                "path": str(rollback_path),
                "sha256": rollback_sha256,
            },
            "deployment_receipt": {
                "path": str(
                    canonical_root / "receipts" / f"sha256-{deployment_sha256}.json"
                ),
                "sha256": deployment_sha256,
            },
            "artifacts": [
                {
                    "role": item.role,
                    "relative_path": item.relative_path,
                    "staged": _thaw(item.staged),
                    "installed": _thaw(item.installed),
                }
                for item in staged_artifacts
            ],
        }
        if bridge_authority is not None:
            unsigned["transition_evidence"] = {
                item.role: {
                    "relative_path": item.relative_path,
                    **_thaw(item.staged),
                }
                for item in transition_evidence
            }
        stage_value = {**unsigned, "content_sha256": _digest(unsigned)}
        stage_raw = _canonical_document(stage_value)
        _create_private_file(
            stage.fd,
            "stage.json",
            stage_raw,
            "staged deployment receipt",
        )
        os.fsync(stage.fd)
        _recheck_disjoint_private_stage_root(stage)
    finally:
        _close_disjoint_private_stage_root(stage)
    expected_files = {item.relative_path: item.raw for item in staged_artifacts}
    expected_files.update(
        {item.relative_path: item.raw for item in transition_evidence}
    )
    expected_files["stage.json"] = stage_raw
    _verify_staged_inventory(stage_path, expected_files)
    current = _capture_active_deployment_precondition(
        canonical_root,
        plan.precondition.receipt_sha256,
        **(
            {
                "receipt_profile": BRIDGE_LEGACY_RECEIPT_PROFILE,
            }
            if bridge_authority is not None
            else {}
        ),
    )
    if current != plan.precondition:
        label = "bridge" if bridge_authority is not None else "routine"
        raise DeploymentError(f"{label} active precondition changed after staging")
    return StagedDeployment(
        plan,
        authorization,
        classification,
        _freeze_json(rollback_value),
        rollback_raw,
        _freeze_json(deployment_value),
        deployment_raw,
        staged_artifacts,
        _freeze_json(stage_value),
        stage_raw,
        stage_path / "stage.json",
        transition_evidence,
    )


def _require_independent_stage_verification(
    staged: StagedDeployment,
    verified: VerifiedDeploymentStage,
    diagnostic: str,
) -> None:
    artifact_projection = lambda item: (
        item.role,
        item.relative_path,
        item.raw,
        item.staged,
        item.installed,
    )
    transition_projection = lambda item: (
        item.role,
        item.relative_path,
        item.raw,
        item.staged,
    )
    if (
        verified.raw != staged.stage_raw
        or tuple(map(artifact_projection, verified.artifacts))
        != tuple(map(artifact_projection, staged.artifacts))
        or tuple(map(transition_projection, verified.transition_evidence))
        != tuple(map(transition_projection, staged.transition_evidence))
    ):
        raise DeploymentError(diagnostic)


def _materialize_manual_rollback(
    prepared: PreparedRollbackTo,
    authorization_raw: bytes,
    staging_root: Path,
) -> StagedDeployment:
    plan = prepared.plan
    authorization = _validate_rollback_to_authorization(
        authorization_raw,
        prepared.authorization_facts,
    )
    root = plan.precondition.canonical_root
    stage_path = _normalized_absolute_path(
        staging_root,
        "manual rollback staging root",
    )
    if stage_path.is_relative_to(root) or root.is_relative_to(stage_path):
        raise DeploymentError(
            "manual rollback staging root must be disjoint from installation"
        )
    rollback_value, rollback_raw, deployment_value, deployment_raw = (
        _manual_rollback_receipts(
            plan,
            authorization,
            authorization_raw,
            stage_path,
        )
    )
    artifacts = _manual_rollback_stage_artifacts(
        plan,
        rollback_raw=rollback_raw,
        deployment_raw=deployment_raw,
    )
    stage = _open_disjoint_private_stage_root(
        stage_path,
        canonical_root=root,
        candidate_root=root,
    )
    try:
        staged_artifacts = tuple(
            _write_staged_artifact(stage, artifact) for artifact in artifacts
        )
        classification = Classification(
            "authorized-manual-exact-target-rollback",
            "exact-deployer-authorization",
        )
        rollback_sha256 = hashlib.sha256(rollback_raw).hexdigest()
        deployment_sha256 = hashlib.sha256(deployment_raw).hexdigest()
        unsigned = {
            "schema_version": 1,
            "contract": STAGED_DEPLOYMENT_CONTRACT,
            "staging_root": str(stage.path),
            "canonical_root": str(root),
            "plan_sha256": plan.plan_sha256,
            "maintenance_transaction_sha256": plan.maintenance_transaction_sha256,
            "classification": {
                "outcome": classification.outcome,
                "reason": classification.reason,
            },
            "authorization": {
                "sha256": hashlib.sha256(authorization_raw).hexdigest(),
                "content_sha256": authorization.content_sha256,
            },
            "rollback_receipt": {
                "path": str(root / "receipts" / f"sha256-{rollback_sha256}.json"),
                "sha256": rollback_sha256,
            },
            "deployment_receipt": {
                "path": str(root / "receipts" / f"sha256-{deployment_sha256}.json"),
                "sha256": deployment_sha256,
            },
            "artifacts": [
                {
                    "role": item.role,
                    "relative_path": item.relative_path,
                    "staged": _thaw(item.staged),
                    "installed": _thaw(item.installed),
                }
                for item in staged_artifacts
            ],
        }
        stage_value = {**unsigned, "content_sha256": _digest(unsigned)}
        stage_raw = _canonical_document(stage_value)
        _create_private_file(
            stage.fd,
            "stage.json",
            stage_raw,
            "manual rollback staged receipt",
        )
        os.fsync(stage.fd)
        _recheck_disjoint_private_stage_root(stage)
    finally:
        _close_disjoint_private_stage_root(stage)
    expected_files = {item.relative_path: item.raw for item in staged_artifacts}
    expected_files["stage.json"] = stage_raw
    _verify_private_staged_inventory(stage_path, expected_files)
    current = _capture_active_deployment_precondition(
        root,
        plan.precondition.receipt_sha256,
    )
    if current != plan.precondition:
        raise DeploymentError("manual rollback precondition changed after staging")
    staged = StagedDeployment(
        plan,
        authorization,
        classification,
        _freeze_json(rollback_value),
        rollback_raw,
        _freeze_json(deployment_value),
        deployment_raw,
        staged_artifacts,
        _freeze_json(stage_value),
        stage_raw,
        stage_path / "stage.json",
    )
    verified = verify_deployment_stage(staged.stage_path)
    _require_independent_stage_verification(
        staged,
        verified,
        "manual rollback stage independent verify disagrees",
    )
    return staged


def _receipt_file_binding(value: object, label: str) -> dict[str, Any]:
    binding = _exact(value, {"path", "length", "sha256", "owner", "mode"}, label)
    return {
        "path": str(
            _normalized_absolute_path(
                Path(_text(binding["path"], f"{label}.path")),
                f"{label}.path",
            )
        ),
        "length": _nonnegative_integer(binding["length"], f"{label}.length"),
        "sha256": _sha256(binding["sha256"], f"{label}.sha256"),
        "owner": _nonnegative_integer(binding["owner"], f"{label}.owner"),
        "mode": _nonnegative_integer(binding["mode"], f"{label}.mode"),
    }


def _staged_artifact_for_role(
    artifacts: Iterable[StagedArtifact],
    role: str,
) -> StagedArtifact:
    matches = [item for item in artifacts if item.role == role]
    if len(matches) != 1:
        raise DeploymentError(f"staged deployment has ambiguous {role} artifact")
    return matches[0]


def _verify_smoke_receipt(
    deployment_value: Mapping[str, Any],
    artifacts: tuple[StagedArtifact, ...],
    *,
    manifest_relative_path: str = "smoke/bundle/manifest.json",
) -> None:
    smoke = _exact(
        deployment_value.get("smoke"),
        {
            "bundle",
            "trust_context",
            "producer",
            "validator",
            "expected_projection",
            "expected_anchor",
            "expected_envelope_sha256",
        },
        "staged deployment smoke receipt",
    )
    _exact(
        smoke["bundle"],
        {"path", "sha256", "manifest"},
        "staged deployment smoke bundle",
    )
    _exact(
        smoke["trust_context"],
        {"path", "sha256"},
        "staged deployment smoke trust context",
    )
    _exact(
        smoke["producer"],
        {
            "producer_id",
            "contract",
            "implementation_sha256",
            "validator_id",
            "validator_contract",
            "validator_implementation_sha256",
        },
        "staged deployment smoke producer",
    )
    _exact(
        smoke["validator"],
        {"validator_id", "contract", "implementation_sha256"},
        "staged deployment smoke validator",
    )
    manifest = _staged_artifact_for_role(artifacts, "smoke-bundle-manifest")
    manifest_binding = {
        "path": str(manifest.installed_path),
        "length": len(manifest.raw),
        "sha256": hashlib.sha256(manifest.raw).hexdigest(),
        "owner": manifest.installed["owner"],
        "mode": manifest.installed["mode"],
    }
    if (
        manifest.relative_path != manifest_relative_path
        or smoke["bundle"]["path"] != str(manifest.installed_path.parent)
        or smoke["bundle"]["manifest"] != manifest_binding
        or smoke["bundle"]["sha256"] != _smoke_bundle_sha256(manifest.raw)
    ):
        raise DeploymentError("staged deployment smoke bundle binding disagrees")
    manifest_value = _exact(
        _parse_canonical_json(manifest.raw, "staged deployment smoke manifest"),
        {"schema_version", "contract", "producer", "challenge"},
        "staged deployment smoke manifest",
    )
    manifest_producer = _exact(
        manifest_value["producer"],
        {"producer_id", "contract", "implementation_sha256"},
        "staged deployment smoke manifest producer",
    )
    if (
        type(manifest_value["schema_version"]) is not int
        or manifest_value["schema_version"] != 1
        or manifest_value["contract"] != SMOKE_BUNDLE_CONTRACT
        or manifest_value["challenge"] != SMOKE_CHALLENGE
    ):
        raise DeploymentError("staged deployment smoke manifest challenge mismatch")
    for key in ("producer_id", "contract"):
        _text(manifest_producer[key], f"staged deployment smoke manifest {key}")
    _sha256(
        manifest_producer["implementation_sha256"],
        "staged deployment smoke manifest producer implementation",
    )
    trust_context = _staged_artifact_for_role(artifacts, "trust-context")
    expected = {
        "path": str(trust_context.installed_path),
        "sha256": hashlib.sha256(trust_context.raw).hexdigest(),
    }
    if smoke["trust_context"] != expected:
        raise DeploymentError("staged deployment smoke trust-context binding disagrees")
    trust_value = _exact(
        _parse_canonical_json(
            trust_context.raw,
            "staged deployment smoke trust context",
        ),
        {
            "schema_version",
            "contract",
            "producers",
            "issuers",
            "validators",
            "content_sha256",
        },
        "staged deployment smoke trust context",
    )
    if (
        type(trust_value["schema_version"]) is not int
        or trust_value["schema_version"] != 1
        or trust_value["contract"] != TRUST_CONTEXT_CONTRACT
    ):
        raise DeploymentError("staged deployment smoke trust-context contract mismatch")
    _content_sha256(trust_value, "staged deployment smoke trust context")
    producers = trust_value["producers"]
    validators = trust_value["validators"]
    if not isinstance(producers, list) or not isinstance(validators, list):
        raise DeploymentError("staged deployment smoke trust role inventory mismatch")
    smoke_producers = [
        item
        for item in producers
        if isinstance(item, dict)
        and item.get("producer_id") == SMOKE_PRODUCER_NAME
        and item.get("contract") == SMOKE_BUNDLE_CONTRACT
    ]
    smoke_validators = [
        item
        for item in validators
        if isinstance(item, dict)
        and item.get("validator_id") == SMOKE_VALIDATOR_NAME
        and item.get("contract") == SMOKE_BUNDLE_CONTRACT
    ]
    if len(smoke_producers) != 1 or len(smoke_validators) != 1:
        raise DeploymentError("staged deployment intrinsic smoke identity is ambiguous")
    retained_validator = _exact(
        smoke_validators[0],
        {
            "validator_id",
            "contract",
            "implementation_sha256",
            "entrypoint",
            "modules",
            "state",
            "usable_for_new_publication",
        },
        "staged deployment intrinsic smoke validator",
    )
    retained_modules = retained_validator["modules"]
    if not isinstance(retained_modules, list) or len(retained_modules) != 1:
        raise DeploymentError("staged deployment intrinsic smoke module mismatch")
    retained_module = _exact(
        retained_modules[0],
        {"name", "path", "sha256"},
        "staged deployment intrinsic smoke module",
    )
    retained_module_path = _normalized_absolute_path(
        Path(
            _text(
                retained_module["path"],
                "staged deployment intrinsic smoke module path",
            )
        ),
        "staged deployment intrinsic smoke module path",
    )
    module_matches = [
        item
        for item in artifacts
        if item.role == "validator-module"
        and item.installed_path == retained_module_path
    ]
    if len(module_matches) != 1:
        raise DeploymentError("staged deployment intrinsic smoke module is ambiguous")
    module = module_matches[0]
    parsed_provider, _ = _intrinsic_smoke_definition(module.raw)
    parsed_validator = parsed_provider.validators[0]
    expected_producer = parsed_provider.producers[0]
    expected_validator = {
        "validator_id": parsed_validator.validator_id,
        "contract": parsed_validator.contract,
        "implementation_sha256": parsed_validator.implementation_sha256,
        "entrypoint": parsed_validator.entrypoint,
        "modules": [
            {
                "name": parsed_validator.modules[0].name,
                "path": str(module.installed_path),
                "sha256": hashlib.sha256(module.raw).hexdigest(),
            }
        ],
        **parsed_validator.lifecycle,
    }
    if (
        smoke_producers[0] != expected_producer
        or retained_validator != expected_validator
    ):
        raise DeploymentError("staged deployment intrinsic smoke identity disagrees")
    expected_producer_identity = {
        key: expected_producer[key]
        for key in (
            "producer_id",
            "contract",
            "implementation_sha256",
            "validator_id",
            "validator_contract",
            "validator_implementation_sha256",
        )
    }
    expected_validator_identity = {
        key: expected_validator[key]
        for key in ("validator_id", "contract", "implementation_sha256")
    }
    expected_manifest_producer = {
        key: expected_producer_identity[key]
        for key in ("producer_id", "contract", "implementation_sha256")
    }
    if (
        smoke["producer"] != expected_producer_identity
        or smoke["validator"] != expected_validator_identity
        or manifest_producer != expected_manifest_producer
    ):
        raise DeploymentError("staged deployment smoke producer identity disagrees")
    active_artifact = _staged_artifact_for_role(artifacts, "active-record")
    active_value = _exact(
        _parse_canonical_json(
            active_artifact.raw,
            "staged deployment smoke active record",
        ),
        {
            "schema_version",
            "contract",
            "generation",
            "runtime_contract",
            "interpreter",
            "public_release",
            "payloads",
            "content_sha256",
        },
        "staged deployment smoke active record",
    )
    if (
        type(active_value["schema_version"]) is not int
        or active_value["schema_version"] != 1
        or active_value["contract"] != ACTIVE_CONTRACT
        or active_value["runtime_contract"] != RUNTIME_CONTRACT
        or not isinstance(active_value["payloads"], list)
    ):
        raise DeploymentError("staged deployment smoke active record mismatch")
    _content_sha256(active_value, "staged deployment smoke active record")
    runtime_implementation_sha256 = _digest(
        {
            "contract": RUNTIME_ARTIFACT_MANIFEST_CONTRACT,
            "runtime_contract": RUNTIME_CONTRACT,
            "entrypoint_role": "entrypoint",
            "payloads": active_value["payloads"],
        }
    )
    if active_value["generation"] != f"sha256-{runtime_implementation_sha256}":
        raise DeploymentError("staged deployment smoke active generation mismatch")
    expected_result = _smoke_expected_result(
        bundle_sha256=_smoke_bundle_sha256(manifest.raw),
        trust_context_sha256=expected["sha256"],
        active_value=active_value,
        active_record_sha256=hashlib.sha256(active_artifact.raw).hexdigest(),
        runtime_implementation_sha256=runtime_implementation_sha256,
        producer=expected_producer_identity,
        validator=expected_validator_identity,
    )
    expected_smoke = {
        "bundle": {
            "path": str(manifest.installed_path.parent),
            "sha256": _smoke_bundle_sha256(manifest.raw),
            "manifest": manifest_binding,
        },
        "trust_context": expected,
        "producer": expected_producer_identity,
        "validator": expected_validator_identity,
        **expected_result,
    }
    if smoke != expected_smoke:
        raise DeploymentError("staged deployment smoke receipt disagrees")
    receipt_trust_context = _exact(
        deployment_value.get("trust_context"),
        {"path", "sha256"},
        "staged deployment receipt trust context",
    )
    if receipt_trust_context != expected:
        raise DeploymentError(
            "staged deployment receipt trust-context binding disagrees"
        )
    receipt_active = _exact(
        deployment_value.get("active"),
        {
            "record_path",
            "record_sha256",
            "generation",
            "runtime_contract",
            "runtime_implementation_sha256",
            "public_release",
        },
        "staged deployment receipt active record",
    )
    expected_receipt_active = {
        "record_path": str(active_artifact.installed_path),
        "record_sha256": expected_result["expected_anchor"]["active_record_sha256"],
        "generation": expected_result["expected_anchor"]["generation"],
        "runtime_contract": RUNTIME_CONTRACT,
        "runtime_implementation_sha256": runtime_implementation_sha256,
        "public_release": active_value["public_release"],
    }
    if receipt_active != expected_receipt_active:
        raise DeploymentError("staged deployment receipt active binding disagrees")


_PROVIDER_ROLE_IDENTIFIERS = {
    "producers": "producer_id",
    "issuers": "issuer_id",
    "validators": "validator_id",
}


def _provider_role_authority_key(
    category: str,
    role: Mapping[str, Any],
) -> tuple[str, str]:
    return role[_PROVIDER_ROLE_IDENTIFIERS[category]], role["contract"]


def _provider_role_identity_key(
    category: str,
    role: Mapping[str, Any],
) -> tuple[str, str, bytes]:
    authority = _provider_role_authority_key(category, role)
    return (*authority, _canonical_bytes(role))


def _require_canonical_provider_roles(
    roles: Mapping[str, list[dict[str, Any]]],
    label: str,
) -> None:
    for category in _PROVIDER_ROLE_IDENTIFIERS:
        values = roles[category]
        identities = [_provider_role_identity_key(category, role) for role in values]
        authorities = [_provider_role_authority_key(category, role) for role in values]
        if identities != sorted(identities) or len(authorities) != len(
            set(authorities)
        ):
            raise DeploymentError(
                f"{label} {category} are not canonically sorted and unique"
            )


def _validate_receipt_source_active_binding(
    deployment_value: Mapping[str, Any],
    source: CandidateSource,
    label: str,
) -> None:
    active = _exact(
        _thaw(deployment_value["active"]),
        {
            "record_path",
            "record_sha256",
            "generation",
            "runtime_contract",
            "runtime_implementation_sha256",
            "public_release",
        },
        f"{label} active binding",
    )
    release = _exact(
        active["public_release"],
        {"repository", "revision"},
        f"{label} active public release",
    )
    repository = _repository_id(
        release["repository"],
        f"{label} active public release.repository",
    )
    revision = _git_revision(
        release["revision"],
        f"{label} active public release.revision",
    )
    if source.repository_id != repository or source.revision != revision:
        raise DeploymentError(f"{label} source does not bind its active public release")


def _validate_provider_source_and_modules(
    deployment_value: Mapping[str, Any],
    canonical_root: Path,
    load_module: Callable[[Path, str], bytes] | None,
    label: str,
    *,
    source_parser: Callable[[Mapping[str, Any], Path], CandidateSource] | None = None,
) -> tuple[CandidateSource, tuple[Path, ...]]:
    roles = _exact(
        _thaw(deployment_value["role_inventory"]),
        {"producers", "issuers", "validators"},
        f"{label} role inventory",
    )
    providers = _thaw(deployment_value["providers"])
    if (
        not isinstance(providers, list)
        or not providers
        or len(providers) > MAX_VALIDATORS + 1
    ):
        raise DeploymentError(f"{label} provider inventory is invalid")
    provider_keys: list[tuple[str, bool]] = []
    plugin_ids: list[str] = []
    projected: dict[str, list[dict[str, Any]]] = {
        "producers": [],
        "issuers": [],
        "validators": [],
    }
    module_paths: set[Path] = set()
    external_providers: list[dict[str, Any]] = []
    intrinsic_providers = 0
    role_specs = {
        "producers": (
            {
                "producer_id",
                "contract",
                "implementation_sha256",
                "validator_id",
                "validator_contract",
                "validator_implementation_sha256",
                "state",
                "usable_for_new_publication",
            },
            "producer_id",
        ),
        "issuers": (
            {
                "issuer_id",
                "contract",
                "implementation_sha256",
                "capabilities",
                "state",
                "usable_for_new_publication",
            },
            "issuer_id",
        ),
        "validators": (
            {
                "validator_id",
                "contract",
                "implementation_sha256",
                "entrypoint",
                "modules",
                "state",
                "usable_for_new_publication",
            },
            "validator_id",
        ),
    }
    for provider_index, provider_value in enumerate(providers):
        provider_label = f"{label} providers[{provider_index}]"
        provider = _exact(
            provider_value,
            {
                "plugin_id",
                "publisher",
                "repository",
                "authority_profile",
                "intrinsic",
                "declaration_sha256",
                "declaration_content_sha256",
                "producers",
                "issuers",
                "validators",
                "retained_modules",
            },
            provider_label,
        )
        plugin_id = _token(provider["plugin_id"], f"{provider_label}.plugin_id")
        _text(provider["publisher"], f"{provider_label}.publisher")
        _text(provider["repository"], f"{provider_label}.repository")
        _token(provider["authority_profile"], f"{provider_label}.authority_profile")
        if type(provider["intrinsic"]) is not bool:
            raise DeploymentError(f"{provider_label}.intrinsic is invalid")
        _sha256(
            provider["declaration_sha256"],
            f"{provider_label}.declaration_sha256",
        )
        _sha256(
            provider["declaration_content_sha256"],
            f"{provider_label}.declaration_content_sha256",
        )
        provider_keys.append((plugin_id, provider["intrinsic"]))
        plugin_ids.append(plugin_id)
        provider_roles: dict[str, list[dict[str, Any]]] = {
            "producers": [],
            "issuers": [],
            "validators": [],
        }
        for category, (keys, identifier) in role_specs.items():
            values = provider[category]
            if not isinstance(values, list) or len(values) > MAX_VALIDATORS:
                raise DeploymentError(
                    f"{provider_label} {category} inventory is invalid"
                )
            for role_index, role_value in enumerate(values):
                role_label = f"{provider_label}.{category}[{role_index}]"
                role = _exact(role_value, keys, role_label)
                _token(role[identifier], f"{role_label}.{identifier}")
                _text(role["contract"], f"{role_label}.contract")
                _sha256(
                    role["implementation_sha256"],
                    f"{role_label}.implementation_sha256",
                )
                if (
                    role["state"] != "active"
                    or role["usable_for_new_publication"] is not True
                ):
                    raise DeploymentError(f"{provider_label} role is not active")
                if category == "producers":
                    _token(role["validator_id"], f"{role_label}.validator_id")
                    _text(
                        role["validator_contract"],
                        f"{role_label}.validator_contract",
                    )
                    _sha256(
                        role["validator_implementation_sha256"],
                        f"{role_label}.validator_implementation_sha256",
                    )
                elif category == "issuers":
                    role["capabilities"] = _issuer_capabilities(
                        role["capabilities"],
                        f"{role_label}.capabilities",
                    )
                else:
                    _token(role["entrypoint"], f"{role_label}.entrypoint")
                    if not isinstance(role["modules"], list):
                        raise DeploymentError(f"{role_label}.modules is invalid")
                provider_roles[category].append(role)
                projected[category].append(role)

        _require_canonical_provider_roles(provider_roles, provider_label)
        modules = provider["retained_modules"]
        if (
            not isinstance(modules, list)
            or len(modules) > MAX_VALIDATORS * MAX_VALIDATOR_MODULES
        ):
            raise DeploymentError(f"{provider_label} retained modules are invalid")
        retained: dict[Path, tuple[str, str, int, bytes | None]] = {}
        observed_paths: list[str] = []
        for module_index, module_value in enumerate(modules):
            module_label = f"{provider_label}.retained_modules[{module_index}]"
            module = _exact(
                module_value,
                {"name", "path", "length", "sha256"},
                module_label,
            )
            name = _token(module["name"], f"{module_label}.name")
            path = _normalized_absolute_path(
                Path(_text(module["path"], f"{module_label}.path")),
                f"{module_label}.path",
            )
            length = _nonnegative_integer(module["length"], f"{module_label}.length")
            digest = _sha256(module["sha256"], f"{module_label}.sha256")
            if (
                length > MAX_MODULE_BYTES
                or path.parent.parent != canonical_root / "trust" / "validators"
                or path.name != f"{name}.py"
                or path in module_paths
            ):
                raise DeploymentError(f"{module_label} binding disagrees")
            raw = load_module(path, module_label) if load_module is not None else None
            if raw is not None and (
                len(raw) != length or hashlib.sha256(raw).hexdigest() != digest
            ):
                raise DeploymentError(f"{module_label} bytes disagree")
            module_paths.add(path)
            retained[path] = (name, digest, length, raw)
            observed_paths.append(str(path))
        if observed_paths != sorted(observed_paths):
            raise DeploymentError(f"{provider_label} retained modules are not ordered")

        referenced_paths: set[Path] = set()
        validator_identities: set[tuple[str, str, str]] = set()
        for validator_index, validator in enumerate(provider_roles["validators"]):
            validator_label = f"{provider_label}.validators[{validator_index}]"
            module_values = validator["modules"]
            if not module_values or len(module_values) > MAX_VALIDATOR_MODULES:
                raise DeploymentError(f"{validator_label}.modules is invalid")
            implementation = validator["implementation_sha256"]
            identities: list[tuple[str, str]] = []
            local_paths: set[Path] = set()
            aggregate_length = 0
            for module_index, module_value in enumerate(module_values):
                module_label = f"{validator_label}.modules[{module_index}]"
                module = _exact(
                    module_value,
                    {"name", "path", "sha256"},
                    module_label,
                )
                name = _token(module["name"], f"{module_label}.name")
                path = _normalized_absolute_path(
                    Path(_text(module["path"], f"{module_label}.path")),
                    f"{module_label}.path",
                )
                digest = _sha256(module["sha256"], f"{module_label}.sha256")
                retained_module = retained.get(path)
                if (
                    retained_module is None
                    or retained_module[:2] != (name, digest)
                    or path.parent.name != f"sha256-{implementation}"
                    or path in local_paths
                ):
                    raise DeploymentError(f"{module_label} binding disagrees")
                aggregate_length += retained_module[2]
                if aggregate_length > MAX_VALIDATOR_ARTIFACT_BYTES:
                    raise DeploymentError(
                        f"{validator_label} artifacts exceed the byte limit"
                    )
                local_paths.add(path)
                referenced_paths.add(path)
                identities.append((name, digest))
            entrypoint = validator["entrypoint"]
            if (
                identities[0][0] != entrypoint
                or _validator_implementation_identity(
                    validator["contract"],
                    entrypoint,
                    identities,
                )
                != implementation
            ):
                raise DeploymentError(
                    f"{validator_label} implementation identity disagrees"
                )
            validator_identities.add(
                (validator["validator_id"], validator["contract"], implementation)
            )
        if referenced_paths != set(retained):
            raise DeploymentError(f"{provider_label} retained module closure disagrees")
        for producer in provider_roles["producers"]:
            validator_identity = (
                producer["validator_id"],
                producer["validator_contract"],
                producer["validator_implementation_sha256"],
            )
            if validator_identity not in validator_identities:
                raise DeploymentError(
                    f"{provider_label} producer validator binding disagrees"
                )

        if provider["intrinsic"]:
            intrinsic_providers += 1
            if len(retained) != 1:
                raise DeploymentError(
                    f"{provider_label} intrinsic module inventory disagrees"
                )
            raw = next(iter(retained.values()))[3]
            if raw is None:
                if (
                    provider["plugin_id"] != "task-witness"
                    or provider["publisher"] != "nisavid"
                    or provider["repository"] != "https://github.com/nisavid/agents"
                    or provider["authority_profile"] != "task-witness-smoke"
                ):
                    raise DeploymentError(
                        f"{provider_label} intrinsic identity disagrees"
                    )
            else:
                intrinsic, declaration_raw = _intrinsic_smoke_definition(raw)
                intrinsic_modules = tuple(
                    module
                    for validator in intrinsic.validators
                    for module in validator.modules
                )
                materialized = _project_provider(
                    intrinsic,
                    declaration_raw,
                    {module.relative_path: raw for module in intrinsic_modules},
                    canonical_root / "trust",
                )
                if provider != _provider_receipt_projection(
                    materialized,
                    intrinsic=True,
                ):
                    raise DeploymentError(
                        f"{provider_label} intrinsic declaration disagrees"
                    )
        else:
            if not any(provider_roles.values()):
                raise DeploymentError(f"{provider_label} registers no roles")
            external_providers.append(provider)

    if (
        provider_keys != sorted(provider_keys)
        or len(provider_keys) != len(set(provider_keys))
        or len(plugin_ids) != len(set(plugin_ids))
    ):
        raise DeploymentError(f"{label} providers are not sorted and unique")
    source = (
        _active_receipt_source(deployment_value, canonical_root)
        if source_parser is None
        else source_parser(deployment_value, canonical_root)
    )
    source_declaration = (
        source.provider_declaration_sha256,
        source.provider_declaration_content_sha256,
    )
    _validate_receipt_source_active_binding(deployment_value, source, label)
    if intrinsic_providers != 1:
        raise DeploymentError(f"{label} intrinsic provider is ambiguous")
    if source_declaration == (None, None):
        if external_providers:
            raise DeploymentError(f"{label} provider has no source declaration")
        if source.plugin_id != "task-witness":
            raise DeploymentError(f"{label} intrinsic-only source identity disagrees")
    elif (
        len(external_providers) != 1
        or external_providers[0]["plugin_id"] != source.plugin_id
        or external_providers[0]["publisher"] != source.publisher_id
        or external_providers[0]["repository"] != source.repository_url
        or external_providers[0]["declaration_sha256"] != source_declaration[0]
        or external_providers[0]["declaration_content_sha256"] != source_declaration[1]
    ):
        raise DeploymentError(f"{label} external provider declaration disagrees")
    observed_inventory: dict[str, list[dict[str, Any]]] = {}
    for category, (_, identifier) in role_specs.items():
        observed_values = roles[category]
        if not isinstance(observed_values, list):
            raise DeploymentError(f"{label} role inventory is invalid")
        observed: list[dict[str, Any]] = []
        for role_index, role_value in enumerate(observed_values):
            role = _exact(
                role_value,
                role_specs[category][0],
                f"{label} role inventory.{category}[{role_index}]",
            )
            _token(role[identifier], f"{label} role inventory {category} identity")
            _text(role["contract"], f"{label} role inventory {category} contract")
            observed.append(role)
        observed_inventory[category] = observed
    _require_canonical_provider_roles(
        observed_inventory,
        f"{label} global role inventory",
    )
    for category in _PROVIDER_ROLE_IDENTIFIERS:
        projected_roles = sorted(
            projected[category],
            key=lambda role: _provider_role_identity_key(category, role),
        )
        if projected_roles != observed_inventory[category]:
            raise DeploymentError(f"{label} provider role ownership disagrees")
    return source, tuple(sorted(module_paths))


def _routine_stage_provider_inventory(
    deployment_value: Mapping[str, Any],
    artifacts: tuple[StagedArtifact, ...],
    canonical_root: Path,
) -> tuple[tuple[str, str, Path], ...]:
    trust_artifact = _staged_artifact_for_role(artifacts, "trust-context")
    trust_value = _exact(
        _parse_canonical_json(trust_artifact.raw, "staged routine trust context"),
        {
            "schema_version",
            "contract",
            "producers",
            "issuers",
            "validators",
            "content_sha256",
        },
        "staged routine trust context",
    )
    _content_sha256(trust_value, "staged routine trust context")
    if (
        type(trust_value["schema_version"]) is not int
        or trust_value["schema_version"] != 1
        or trust_value["contract"] != TRUST_CONTEXT_CONTRACT
        or deployment_value["trust_context"]
        != {
            "path": str(trust_artifact.installed_path),
            "sha256": hashlib.sha256(trust_artifact.raw).hexdigest(),
        }
    ):
        raise DeploymentError("staged routine trust context disagrees")
    roles = _exact(
        deployment_value["role_inventory"],
        {"producers", "issuers", "validators"},
        "staged routine role inventory",
    )
    if any(roles[category] != trust_value[category] for category in roles):
        raise DeploymentError("staged routine role inventory disagrees")
    expected: list[tuple[str, str, Path]] = [
        (
            "trust-context",
            trust_artifact.relative_path,
            trust_artifact.installed_path,
        )
    ]
    observed_modules: dict[Path, StagedArtifact] = {}

    def load_module(path: Path, label: str) -> bytes:
        matches = [item for item in artifacts if item.installed_path == path]
        if len(matches) != 1 or matches[0].role != "validator-module":
            raise DeploymentError(f"{label} staged artifact disagrees")
        observed_modules[path] = matches[0]
        return matches[0].raw

    _, module_paths = _validate_provider_source_and_modules(
        deployment_value,
        canonical_root,
        load_module,
        "staged routine",
    )
    for path in module_paths:
        artifact = observed_modules[path]
        expected.append((artifact.role, artifact.relative_path, path))
    return tuple(expected)


def _staged_compatibility_policy(
    receipt: Mapping[str, Any],
    artifact: StagedArtifact,
    label: str,
    *,
    parser: Callable[[bytes], CompatibilityPolicy] = _parse_compatibility_policy,
) -> CompatibilityPolicy:
    parsed = parser(artifact.raw)
    control_binding = _receipt_file_binding(
        receipt["control_set"]["policy"],
        f"{label} control-set policy",
    )
    policy_binding = _exact(
        receipt["compatibility_policy"],
        {"path", "length", "sha256", "owner", "mode", "content_sha256"},
        f"{label} compatibility policy",
    )
    declared_receipt_contracts = {
        key: parsed.control_surface["contracts"][key] for key in RECEIPT_CONTRACTS
    }
    if (
        _thaw(artifact.installed) != control_binding
        or {key: policy_binding[key] for key in control_binding} != control_binding
        or policy_binding["sha256"] != parsed.raw_sha256
        or policy_binding["content_sha256"] != parsed.content_sha256
        or receipt["process_profile"]
        != _thaw(parsed.control_surface["process_profile"])
        or receipt["contracts"] != declared_receipt_contracts
    ):
        raise DeploymentError(f"{label} policy authority disagrees")
    return parsed


def _validate_routine_source_transition(
    prior: Mapping[str, Any],
    candidate: Mapping[str, Any],
    canonical_root: Path,
    *,
    prior_policy: CompatibilityPolicy | None,
    candidate_policy: CompatibilityPolicy | None,
    control_stage: bool,
    manual_stage: bool = False,
    authorization_purpose: str | None = None,
    prior_source_parser: Callable[[Mapping[str, Any], Path], CandidateSource]
    | None = None,
) -> None:
    """Reclassify one staged source edge under independently bound A/B policy."""

    active_source = (
        _active_receipt_source(prior, canonical_root)
        if prior_source_parser is None
        else prior_source_parser(prior, canonical_root)
    )
    candidate_source = _active_receipt_source(candidate, canonical_root)
    if control_stage or manual_stage:
        if prior_policy is None or candidate_policy is None:
            raise DeploymentError("staged control policy authority is incomplete")
        if not _policy_covers_source(prior_policy, active_source):
            raise DeploymentError("staged prior source is outside prior policy")
        if not _policy_covers_source(candidate_policy, candidate_source):
            raise DeploymentError("staged candidate source is outside candidate policy")
    policy_unchanged = (
        candidate["compatibility_policy"] == prior["compatibility_policy"]
    )
    classification = _classify_source_transition(
        active_source=active_source,
        candidate_source=candidate_source,
        policy_unchanged=policy_unchanged,
    )
    if not control_stage and not manual_stage and not policy_unchanged:
        raise DeploymentError("staged routine source policy binding disagrees")
    manual_exact_target = manual_stage and (
        classification.outcome,
        classification.reason,
    ) == ("no-op", "exact-release")
    if not manual_exact_target:
        _require_deployment_source_outcome(
            classification,
            control_maintenance=control_stage or manual_stage,
            label="staged deployment",
        )
    if not manual_stage:
        expected_purpose = _active_prior_authorization_purpose(
            classification,
            control_maintenance=control_stage,
        )
        if authorization_purpose != expected_purpose:
            raise DeploymentError(
                "staged deployment source authorization purpose disagrees: "
                f"{classification.outcome}/{classification.reason}"
            )


def _verify_routine_stage_receipts(
    *,
    stage_value: Mapping[str, Any],
    rollback_value: dict[str, Any],
    deployment_value: dict[str, Any],
    artifacts: tuple[StagedArtifact, ...],
    canonical_root: Path,
    rollback_path: Path,
    rollback_sha256: str,
    control_stage: bool = False,
    manual_stage: bool = False,
    bridge_stage: bool = False,
) -> None:
    rollback_keys = {
        "schema_version",
        "contract",
        "state",
        "canonical_root",
        "effective_uid",
        "activation_lock",
        "deployment_receipt_absent",
        "precondition",
        "prior_receipt",
        "prior_activation_unit",
        "selector_preimage",
        "external_dependencies",
        "smoke",
        "content_sha256",
    }
    if control_stage or manual_stage:
        rollback_keys.add("control_preimage")
    rollback = _exact(
        rollback_value,
        rollback_keys,
        "staged active rollback receipt",
    )
    precondition = _exact(
        rollback["precondition"],
        {"root_identity", "activation_lock_identity", "active_receipt_sha256"},
        "staged active rollback precondition",
    )
    root_identity = _activation_identity_vector(
        precondition["root_identity"],
        "staged active rollback root identity",
    )
    lock_identity = _activation_identity_vector(
        precondition["activation_lock_identity"],
        "staged active rollback activation-lock identity",
    )
    live_root_identity = _identity(canonical_root.lstat())
    live_lock_identity = _identity((canonical_root / "activation.lock").lstat())
    activation_lock = {
        "path": str(canonical_root / "activation.lock"),
        "device": lock_identity[0],
        "inode": lock_identity[1],
        "owner": lock_identity[3],
        "mode": stat.S_IMODE(lock_identity[2]),
    }
    prior_deployment = _staged_artifact_for_role(
        artifacts,
        "prior-deployment-alias",
    )
    prior_active = _staged_artifact_for_role(artifacts, "prior-active-record")
    prior_sha256 = hashlib.sha256(prior_deployment.raw).hexdigest()
    prior = _parse_canonical_json(
        prior_deployment.raw,
        "staged immediate prior deployment receipt",
    )
    _validate_retained_receipt_header(
        prior,
        canonical_root,
        activation_lock,
        "staged immediate prior deployment receipt",
        receipt_profile=(
            BRIDGE_LEGACY_RECEIPT_PROFILE if bridge_stage else CURRENT_RECEIPT_PROFILE
        ),
    )
    if (
        type(rollback["schema_version"]) is not int
        or rollback["schema_version"] != 1
        or rollback["contract"] != ROLLBACK_RECEIPT_CONTRACT
        or rollback["state"] != "active"
        or rollback["canonical_root"] != str(canonical_root)
        or type(rollback["effective_uid"]) is not int
        or rollback["effective_uid"] != os.geteuid()
        or rollback["activation_lock"] != activation_lock
        or rollback["deployment_receipt_absent"] is not False
        or root_identity[:4] != live_root_identity[:4]
        or lock_identity != live_lock_identity
        or _sha256(
            precondition["active_receipt_sha256"],
            "staged active rollback prior digest",
        )
        != prior_sha256
    ):
        raise DeploymentError("staged active rollback receipt disagrees")
    prior_binding = _receipt_file_binding(
        rollback["prior_receipt"],
        "staged active rollback prior receipt",
    )
    if prior_binding != {
        "path": str(canonical_root / "receipts" / f"sha256-{prior_sha256}.json"),
        "length": len(prior_deployment.raw),
        "sha256": prior_sha256,
        "owner": os.geteuid(),
        "mode": 0o600,
    }:
        raise DeploymentError("staged active rollback prior receipt disagrees")
    prior_unit = _activation_journal_unit_shape(
        rollback["prior_activation_unit"],
        canonical_root,
        os.geteuid(),
        "staged active rollback prior activation unit",
    )
    expected_prior_deployment = {
        "path": str(canonical_root / "deployment.json"),
        "length": len(prior_deployment.raw),
        "sha256": prior_sha256,
        "owner": os.geteuid(),
        "mode": 0o600,
    }
    prior_active_binding = prior["active"]
    prior_anchor = prior_unit["smoke"]["expected_anchor"]
    if (
        prior_unit["deployment_receipt"] != expected_prior_deployment
        or prior_unit["active_record"]
        != {
            "path": str(canonical_root / "active.json"),
            "length": len(prior_active.raw),
            "sha256": hashlib.sha256(prior_active.raw).hexdigest(),
            "owner": os.geteuid(),
            "mode": 0o600,
        }
        or prior_unit["control_set"] != prior["control_set"]
        or prior_unit["smoke"] != prior["smoke"]
        or prior_active_binding["record_sha256"]
        != prior_unit["active_record"]["sha256"]
        or prior_active_binding["generation"] != prior_anchor["generation"]
        or prior_active_binding["runtime_contract"] != prior_anchor["runtime_contract"]
        or prior_active_binding["runtime_implementation_sha256"]
        != prior_anchor["runtime_implementation_sha256"]
        or prior_active_binding["public_release"] != prior_anchor["public_release"]
        or prior["trust_context"] != prior_unit["smoke"]["trust_context"]
    ):
        raise DeploymentError("staged active rollback prior unit disagrees")
    _active_runtime_record_shape(
        prior,
        prior_active.raw,
        canonical_root,
        "staged prior active record",
    )
    preimage = rollback["selector_preimage"]
    if not isinstance(preimage, list) or len(preimage) != 2:
        raise DeploymentError("staged active selector preimage is invalid")
    expected_preimage = [
        {
            "role": "active-record",
            "staged": _thaw(prior_active.staged),
            "installed": _thaw(prior_active.installed),
        },
        {
            "role": "deployment-alias",
            "staged": _thaw(prior_deployment.staged),
            "installed": _thaw(prior_deployment.installed),
        },
    ]
    if preimage != expected_preimage:
        raise DeploymentError("staged active selector preimage disagrees")
    prior_control_artifacts: dict[str, StagedArtifact] = {}
    if control_stage or manual_stage:
        control_preimage = rollback["control_preimage"]
        if not isinstance(control_preimage, list) or [
            item.get("role") for item in control_preimage
        ] != list(CONTROL_PREIMAGE_ROLES):
            raise DeploymentError("staged control preimage order disagrees")
        prior_bindings = {
            **_thaw(prior["control_set"]),
            "smoke-bundle-manifest": _thaw(prior["smoke"]["bundle"]["manifest"]),
        }
        for index, role in enumerate(CONTROL_PREIMAGE_ROLES):
            artifact = _staged_artifact_for_role(artifacts, f"prior-{role}")
            prior_control_artifacts[role] = artifact
            expected_control = {
                "role": role,
                "staged": _thaw(artifact.staged),
                "installed": _thaw(artifact.installed),
            }
            if (
                control_preimage[index] != expected_control
                or expected_control["installed"] != prior_bindings[role]
            ):
                raise DeploymentError("staged control preimage binding disagrees")
    external = _exact(
        rollback["external_dependencies"],
        {"interpreter", "runtime_closure", "process_profile", "receipt_parser"},
        "staged active rollback external dependencies",
    )
    parser = _exact(
        external["receipt_parser"],
        {
            "deployment_receipt_contract",
            "rollback_receipt_contract",
            "controller",
            "client",
        },
        "staged active rollback receipt parser",
    )
    if (
        external["interpreter"] != prior["interpreter"]
        or external["runtime_closure"] != prior["runtime_closure"]
        or external["process_profile"] != prior["process_profile"]
        or parser["deployment_receipt_contract"]
        != prior["contracts"]["deployment_receipt"]
        or parser["rollback_receipt_contract"] != prior["contracts"]["rollback_receipt"]
        or parser["controller"] != prior["control_set"]["controller"]
        or parser["client"] != prior["control_set"]["client"]
        or rollback["smoke"] != prior["smoke"]
        or rollback["smoke"] != prior_unit["smoke"]
    ):
        raise DeploymentError("staged active rollback authority disagrees")

    deployment, authorization = _validate_retained_receipt_header(
        deployment_value,
        canonical_root,
        activation_lock,
        "staged routine deployment receipt",
        bridge_migration=bridge_stage,
    )
    if (
        deployment["sequence"] != prior["sequence"] + 1
        or deployment["prior_receipt_sha256"] != prior_sha256
        or authorization["purpose"]
        not in (
            {"manual-exact-target-rollback"}
            if manual_stage
            else {
                "routine-compatible-forward",
                "source-boundary-change",
                "complete-control-set-maintenance",
            }
        )
        or authorization["expected_active_receipt_sha256"] != prior_sha256
        or authorization["plan_sha256"] != stage_value["plan_sha256"]
        or authorization["maintenance_transaction_sha256"]
        != stage_value["maintenance_transaction_sha256"]
        or authorization["sha256"] != stage_value["authorization"]["sha256"]
        or authorization["content_sha256"]
        != stage_value["authorization"]["content_sha256"]
        or deployment["rollback"]
        != {
            "state": "active",
            "path": str(rollback_path),
            "sha256": rollback_sha256,
        }
    ):
        raise DeploymentError("staged routine deployment authority disagrees")
    maintenance_differences = _maintenance_authority_differences(
        _receipt_maintenance_authority_surface(prior),
        _receipt_maintenance_authority_surface(deployment),
    )
    if control_stage and not manual_stage and not maintenance_differences:
        raise DeploymentError(
            "staged control maintenance has no supported authority difference"
        )
    if not control_stage and not manual_stage and maintenance_differences:
        raise DeploymentError("staged routine deployment stable surface disagrees")
    if control_stage or manual_stage:
        candidate_control_artifacts: dict[str, StagedArtifact] = {}
        candidate_bindings = {
            **_thaw(deployment["control_set"]),
            "smoke-bundle-manifest": _thaw(deployment["smoke"]["bundle"]["manifest"]),
        }
        for role in CONTROL_PREIMAGE_ROLES:
            artifact = _staged_artifact_for_role(artifacts, role)
            candidate_control_artifacts[role] = artifact
            if _thaw(artifact.installed) != candidate_bindings[role]:
                raise DeploymentError("staged candidate control-set binding disagrees")
        prior_policy = _staged_compatibility_policy(
            prior,
            prior_control_artifacts["policy"],
            "staged prior",
            parser=(
                _parse_bridge_legacy_compatibility_policy
                if bridge_stage
                else _parse_compatibility_policy
            ),
        )
        candidate_policy = _staged_compatibility_policy(
            deployment,
            candidate_control_artifacts["policy"],
            "staged candidate",
        )
    else:
        prior_policy = None
        candidate_policy = None
    _validate_routine_source_transition(
        prior,
        deployment,
        canonical_root,
        prior_policy=prior_policy,
        candidate_policy=candidate_policy,
        control_stage=control_stage,
        manual_stage=manual_stage,
        authorization_purpose=authorization["purpose"],
        prior_source_parser=(
            _bridge_legacy_active_receipt_source if bridge_stage else None
        ),
    )
    current_history = _historical_trust_registry_shape(
        deployment["historical_trust_contexts"],
        canonical_root,
        "staged routine historical trust contexts",
    )
    prior_history = _historical_trust_registry_shape(
        prior["historical_trust_contexts"],
        canonical_root,
        "staged prior historical trust contexts",
    )
    prior_trust = _trust_context_binding_shape(
        prior["trust_context"],
        canonical_root,
        "staged prior trust context",
    )
    current_trust = _trust_context_binding_shape(
        deployment["trust_context"],
        canonical_root,
        "staged candidate trust context",
    )
    expected_history = _expected_routine_historical_trust(
        current_trust=current_trust,
        prior_trust=prior_trust,
        prior_history=prior_history,
        label="staged routine deployment history",
    )
    if current_history != expected_history:
        raise DeploymentError("staged routine historical trust closure disagrees")

    expected_artifacts = (
        []
        if manual_stage
        else list(
            _routine_stage_provider_inventory(deployment, artifacts, canonical_root)
        )
    )
    candidate_intrinsic = [
        provider
        for provider in deployment["providers"]
        if provider["intrinsic"] is True
    ]
    prior_intrinsic = [
        provider for provider in prior["providers"] if provider["intrinsic"] is True
    ]
    if (
        len(candidate_intrinsic) != 1
        or len(prior_intrinsic) != 1
        or (
            not (control_stage or manual_stage)
            and candidate_intrinsic[0] != prior_intrinsic[0]
        )
    ):
        raise DeploymentError("staged prior intrinsic provider disagrees")
    active_artifact = _staged_artifact_for_role(artifacts, "active-record")
    active_value = _parse_canonical_json(
        active_artifact.raw,
        "staged routine active record",
    )
    for index, payload_value in enumerate(active_value["payloads"]):
        payload = _exact(
            payload_value,
            {"role", "relative_path", "length", "sha256"},
            f"staged routine payloads[{index}]",
        )
        role = _token(payload["role"], f"staged routine payloads[{index}].role")
        _, components = _relative_path(
            payload["relative_path"],
            f"staged routine payloads[{index}].relative_path",
        )
        path = canonical_root / "generations" / active_value["generation"]
        path = path.joinpath(*components)
        if manual_stage:
            raw = _capture_absolute_regular(
                path,
                max(payload["length"], 1),
                f"staged manual rollback runtime payload {role}",
            )
            metadata = path.lstat()
            if (
                len(raw) != payload["length"]
                or hashlib.sha256(raw).hexdigest() != payload["sha256"]
                or metadata.st_uid != os.geteuid()
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise DeploymentError(
                    "staged manual rollback runtime payload disagrees"
                )
            continue
        matches = [item for item in artifacts if item.installed_path == path]
        if (
            len(matches) != 1
            or matches[0].role != f"runtime-{role}"
            or len(matches[0].raw)
            != _nonnegative_integer(payload["length"], "staged routine payload length")
            or hashlib.sha256(matches[0].raw).hexdigest()
            != _sha256(payload["sha256"], "staged routine payload digest")
        ):
            raise DeploymentError("staged routine payload artifact disagrees")
        expected_artifacts.append((matches[0].role, matches[0].relative_path, path))
    fixed_roles = [
        "active-record",
        "deployment-alias",
        "prior-active-record",
        "prior-deployment-alias",
        "rollback-receipt",
        "deployment-receipt",
    ]
    if control_stage or manual_stage:
        fixed_roles.extend(CONTROL_PREIMAGE_ROLES)
        fixed_roles.extend(f"prior-{role}" for role in CONTROL_PREIMAGE_ROLES)
    else:
        fixed_roles.append("smoke-bundle-manifest")
    for role in fixed_roles:
        item = _staged_artifact_for_role(artifacts, role)
        expected_artifacts.append((role, item.relative_path, item.installed_path))
    observed_artifacts = [
        (item.role, item.relative_path, item.installed_path) for item in artifacts
    ]
    if sorted(observed_artifacts) != sorted(expected_artifacts):
        raise DeploymentError("staged routine artifact role inventory disagrees")


def _verify_private_staged_inventory(
    staging_root: Path,
    expected_files: Mapping[str, bytes],
) -> None:
    """Descriptor-pin one exact private, creation-disabled stage tree."""

    root = _open_root(staging_root, "staged deployment private inventory")
    directories: list[tuple[int, str, int, tuple[int, ...], tuple[str, ...], str]] = []
    files: list[tuple[int, str, int, tuple[int, ...], bytes, str]] = []
    observed_files: set[str] = set()
    observed_directories: set[str] = set()
    try:
        root_metadata = os.fstat(root.fd)
        _reject_macos_allow_acl(root.fd, "staged deployment root")
        if (
            root_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(root_metadata.st_mode) != 0o700
        ):
            raise DeploymentError("staged deployment root is not private")

        def visit(descriptor: int, components: tuple[str, ...]) -> None:
            try:
                names = tuple(sorted(os.listdir(descriptor)))
            except OSError as error:
                raise DeploymentError(
                    "staged deployment inventory is unavailable"
                ) from error
            for name in names:
                _relative_path(name, "staged deployment inventory component")
                relative = "/".join((*components, name))
                try:
                    before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                except OSError as error:
                    raise DeploymentError(
                        "staged deployment entry is unavailable"
                    ) from error
                if stat.S_ISLNK(before.st_mode):
                    raise DeploymentError("staged deployment contains a symlink")
                if stat.S_ISDIR(before.st_mode):
                    if (
                        before.st_uid != os.geteuid()
                        or stat.S_IMODE(before.st_mode) != 0o700
                    ):
                        raise DeploymentError(
                            "staged deployment directory is not private"
                        )
                    try:
                        child = os.open(name, _DIRECTORY_FLAGS, dir_fd=descriptor)
                    except OSError as error:
                        raise DeploymentError(
                            "staged deployment directory cannot be opened"
                        ) from error
                    try:
                        after = os.fstat(child)
                        if _identity(before) != _identity(after):
                            raise DeploymentError(
                                "staged deployment directory changed while opening"
                            )
                        _reject_macos_allow_acl(child, "staged deployment directory")
                        child_names = tuple(sorted(os.listdir(child)))
                    except BaseException:
                        os.close(child)
                        raise
                    directories.append(
                        (
                            descriptor,
                            name,
                            child,
                            _identity(after),
                            child_names,
                            relative,
                        )
                    )
                    observed_directories.add(relative)
                    visit(child, (*components, name))
                    continue
                if not stat.S_ISREG(before.st_mode):
                    raise DeploymentError("staged deployment contains a special file")
                if (
                    before.st_uid != os.geteuid()
                    or before.st_nlink != 1
                    or stat.S_IMODE(before.st_mode) != 0o600
                ):
                    raise DeploymentError("staged deployment file is not private")
                try:
                    file_descriptor = os.open(name, _FILE_FLAGS, dir_fd=descriptor)
                except OSError as error:
                    raise DeploymentError(
                        "staged deployment file cannot be opened"
                    ) from error
                try:
                    after = os.fstat(file_descriptor)
                    if _identity(before) != _identity(after):
                        raise DeploymentError(
                            "staged deployment file changed while opening"
                        )
                    _reject_macos_allow_acl(file_descriptor, "staged deployment file")
                    expected = expected_files.get(relative)
                    if expected is None:
                        raise DeploymentError(
                            "staged deployment inventory has an extra file"
                        )
                    raw = _read_descriptor(
                        file_descriptor,
                        len(expected),
                        f"staged deployment inventory {relative}",
                    )
                    if raw != expected:
                        raise DeploymentError(
                            "staged deployment inventory bytes disagree"
                        )
                except BaseException:
                    os.close(file_descriptor)
                    raise
                files.append(
                    (
                        descriptor,
                        name,
                        file_descriptor,
                        _identity(after),
                        raw,
                        relative,
                    )
                )
                observed_files.add(relative)

        visit(root.fd, ())
        expected_directories = {
            "/".join(parts[:index])
            for relative in expected_files
            for parts in (relative.split("/"),)
            for index in range(1, len(parts))
        }
        if (
            observed_files != set(expected_files)
            or observed_directories != expected_directories
        ):
            raise DeploymentError("staged deployment inventory disagrees")
        for parent, name, child, identity, names, _ in directories:
            if (
                _identity(os.fstat(child)) != identity
                or _identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
                != identity
                or tuple(sorted(os.listdir(child))) != names
            ):
                raise DeploymentError(
                    "staged deployment directory changed during verify"
                )
        for parent, name, descriptor, identity, raw, relative in files:
            if (
                _identity(os.fstat(descriptor)) != identity
                or _identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
                != identity
                or _read_descriptor(
                    descriptor,
                    len(raw),
                    f"staged deployment inventory {relative}",
                )
                != raw
            ):
                raise DeploymentError("staged deployment file changed during verify")
        _recheck_root(root, "staged deployment private inventory")
    except OSError as error:
        raise DeploymentError(
            "staged deployment inventory cannot be verified"
        ) from error
    finally:
        for _, _, descriptor, _, _, _ in reversed(files):
            os.close(descriptor)
        for _, _, descriptor, _, _, _ in reversed(directories):
            os.close(descriptor)
        os.close(root.fd)


def _verify_staged_transition_evidence(
    value: object,
    staging_root: Path,
) -> tuple[StagedTransitionEvidence, ...]:
    evidence = _exact(
        value,
        {"manifest", "authorization"},
        "staged bridge transition evidence",
    )
    expected_paths = {
        "manifest": "bridge-transition-manifest.json",
        "authorization": "bridge-transition-authorization.json",
    }
    verified: list[StagedTransitionEvidence] = []
    for role in ("manifest", "authorization"):
        label = f"staged bridge transition {role}"
        item = _exact(
            evidence[role],
            {"relative_path", "path", "length", "sha256", "owner", "mode"},
            label,
        )
        relative, components = _relative_path(
            item["relative_path"],
            f"{label}.relative_path",
        )
        if relative != expected_paths[role] or len(components) != 1:
            raise DeploymentError(f"{label} path disagrees")
        binding = _receipt_file_binding(
            {key: item[key] for key in ("path", "length", "sha256", "owner", "mode")},
            label,
        )
        staged_path = staging_root / relative
        if (
            binding["path"] != str(staged_path)
            or binding["owner"] != os.geteuid()
            or binding["mode"] != 0o600
        ):
            raise DeploymentError(f"{label} binding disagrees")
        raw = _capture_absolute_regular(staged_path, MAX_JSON_BYTES, label)
        metadata = staged_path.lstat()
        if (
            len(raw) != binding["length"]
            or hashlib.sha256(raw).hexdigest() != binding["sha256"]
            or metadata.st_uid != binding["owner"]
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != binding["mode"]
        ):
            raise DeploymentError(f"{label} bytes disagree")
        verified.append(
            StagedTransitionEvidence(
                role,
                relative,
                staged_path,
                raw,
                _freeze(binding),
            )
        )
    return tuple(verified)


def _verify_bridge_stage_authority(
    *,
    stage_value: Mapping[str, Any],
    deployment_value: Mapping[str, Any],
    transition_evidence: tuple[StagedTransitionEvidence, ...],
    canonical_root: Path,
    staging_root: Path,
) -> None:
    if tuple(item.role for item in transition_evidence) != (
        "manifest",
        "authorization",
    ):
        raise DeploymentError("staged bridge transition evidence order disagrees")
    manifest_raw = transition_evidence[0].raw
    authorization_raw = transition_evidence[1].raw
    manifest = _exact(
        _parse_bridge_canonical_json(
            manifest_raw,
            "staged bridge transition manifest",
        ),
        {
            "schema_version",
            "contract",
            "qualification_candidate",
            "targets",
            "bridge_history",
            "canonical_review_evidence_sha256",
            "final_public_release",
            "migration_edge",
            "promotion_delta_sha256",
            "disposition",
            "content_sha256",
        },
        "staged bridge transition manifest",
    )
    _content_sha256(manifest, "staged bridge transition manifest")
    if (
        type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != 1
        or manifest["contract"] != BRIDGE_RELEASE_MANIFEST_CONTRACT
        or manifest["disposition"] != "release-qualified"
    ):
        raise DeploymentError("staged bridge transition manifest contract mismatch")
    history = _exact(
        manifest["bridge_history"],
        {
            "bridge_identity_sha256",
            "bridge_provenance_sha256",
            "freeze5",
            "bridge",
        },
        "staged bridge transition history",
    )
    authorization = _parse_bridge_transition_authorization_document(authorization_raw)
    migration_keys = {
        "schema_version",
        "contract",
        "edge",
        "purpose",
        "execution_class",
        "maintenance_transaction_sha256",
        "deployment_authorization_sha256",
        "transition_authorization_sha256",
        "expected_active_receipt_core_sha256",
        "bridge_identity_sha256",
        "release_manifest_sha256",
        "endpoint_projection_sha256",
    }
    if authorization["execution_class"] == "live-migration":
        migration_keys.add("prior_rehearsal")
    migration = _exact(
        deployment_value.get("migration"),
        migration_keys,
        "staged bridge migration projection",
    )
    expected_migration = {
        "schema_version": 1,
        "contract": BRIDGE_MIGRATION_PROJECTION_CONTRACT,
        "edge": {"from": "freeze5", "to": "tw4", "via": "bridge"},
        "purpose": "bridge-transition",
        "execution_class": authorization["execution_class"],
        "maintenance_transaction_sha256": authorization[
            "maintenance_transaction_sha256"
        ],
        "deployment_authorization_sha256": authorization[
            "deployment_authorization_sha256"
        ],
        "transition_authorization_sha256": hashlib.sha256(
            authorization_raw
        ).hexdigest(),
        "expected_active_receipt_core_sha256": authorization[
            "expected_active_receipt_core_sha256"
        ],
        "bridge_identity_sha256": authorization["bridge_identity_sha256"],
        "release_manifest_sha256": authorization["release_manifest_sha256"],
        "endpoint_projection_sha256": authorization["endpoint_projection_sha256"],
    }
    if authorization["execution_class"] == "live-migration":
        expected_migration["prior_rehearsal"] = authorization["prior_rehearsal"]
    core = {
        key: _thaw(item)
        for key, item in deployment_value.items()
        if key not in {"migration", "content_sha256"}
    }
    if (
        migration != expected_migration
        or authorization["canonical_root"] != str(canonical_root)
        or authorization["staging_root"] != str(staging_root)
        or authorization["effective_uid"] != os.geteuid()
        or authorization["plan_sha256"] != stage_value["plan_sha256"]
        or authorization["maintenance_transaction_sha256"]
        != stage_value["maintenance_transaction_sha256"]
        or authorization["deployment_authorization_sha256"]
        != stage_value["authorization"]["sha256"]
        or authorization["expected_active_receipt_core_sha256"] != _digest(core)
        or authorization["release_manifest_sha256"]
        != hashlib.sha256(manifest_raw).hexdigest()
        or authorization["bridge_identity_sha256"] != history["bridge_identity_sha256"]
    ):
        raise DeploymentError("staged bridge transition authority disagrees")


def _verify_deployment_stage(stage_receipt: Path) -> VerifiedDeploymentStage:
    """Verify a complete stage without creating any missing object."""

    stage_path = _normalized_absolute_path(stage_receipt, "staged deployment receipt")
    if stage_path.name != "stage.json":
        raise DeploymentError("staged deployment receipt path mismatch")
    raw = _capture_absolute_regular(
        stage_path,
        MAX_JSON_BYTES,
        "staged deployment receipt",
    )
    label = "staged deployment receipt"
    parsed = _parse_canonical_json(raw, label)
    stage_keys = {
        "schema_version",
        "contract",
        "staging_root",
        "canonical_root",
        "plan_sha256",
        "maintenance_transaction_sha256",
        "classification",
        "authorization",
        "rollback_receipt",
        "deployment_receipt",
        "artifacts",
        "content_sha256",
    }
    if "transition_evidence" in parsed:
        stage_keys.add("transition_evidence")
    value = _exact(parsed, stage_keys, label)
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise DeploymentError("staged deployment receipt schema version mismatch")
    if value["contract"] != STAGED_DEPLOYMENT_CONTRACT:
        raise DeploymentError("staged deployment receipt contract mismatch")
    _content_sha256(value, label)
    staging_root = _normalized_absolute_path(
        Path(_text(value["staging_root"], "staged deployment root")),
        "staged deployment root",
    )
    canonical_root = _normalized_absolute_path(
        Path(_text(value["canonical_root"], "staged canonical root")),
        "staged canonical root",
    )
    if staging_root != stage_path.parent:
        raise DeploymentError("staged deployment root binding disagrees")
    _sha256(value["plan_sha256"], "staged deployment plan digest")
    _sha256(
        value["maintenance_transaction_sha256"],
        "staged maintenance transaction digest",
    )
    classification = _exact(
        value["classification"],
        {"outcome", "reason"},
        "staged deployment classification",
    )
    first_install_stage = classification == {
        "outcome": "authorized-first-install",
        "reason": "exact-deployer-authorization",
    }
    routine_stage = classification == {
        "outcome": "authorized-routine-payload",
        "reason": "active-policy-compatible-forward",
    }
    control_stage = classification == {
        "outcome": "authorized-control-set-maintenance",
        "reason": "exact-deployer-authorization",
    }
    manual_stage = classification == {
        "outcome": "authorized-manual-exact-target-rollback",
        "reason": "exact-deployer-authorization",
    }
    bridge_stage = classification == {
        "outcome": "authorized-bridge-transition",
        "reason": "exact-bridge-transition-authorization",
    }
    if not any(
        (first_install_stage, routine_stage, control_stage, manual_stage, bridge_stage)
    ):
        raise DeploymentError("staged deployment classification mismatch")
    if bridge_stage != ("transition_evidence" in value):
        raise DeploymentError("staged deployment transition evidence class mismatch")
    authorization = _exact(
        value["authorization"],
        {"sha256", "content_sha256"},
        "staged deployment authorization",
    )
    _sha256(authorization["sha256"], "staged deployment authorization digest")
    _sha256(
        authorization["content_sha256"],
        "staged deployment authorization content digest",
    )
    rollback = _exact(
        value["rollback_receipt"],
        {"path", "sha256"},
        "staged rollback receipt",
    )
    deployment = _exact(
        value["deployment_receipt"],
        {"path", "sha256"},
        "staged canonical deployment receipt",
    )
    rollback_path = _normalized_absolute_path(
        Path(_text(rollback["path"], "staged rollback receipt path")),
        "staged rollback receipt path",
    )
    deployment_path = _normalized_absolute_path(
        Path(_text(deployment["path"], "staged deployment receipt path")),
        "staged deployment receipt path",
    )
    rollback_sha256 = _sha256(
        rollback["sha256"],
        "staged rollback receipt digest",
    )
    deployment_sha256 = _sha256(
        deployment["sha256"],
        "staged deployment receipt digest",
    )
    if (
        not rollback_path.is_relative_to(canonical_root / "receipts")
        or not deployment_path.is_relative_to(canonical_root / "receipts")
        or rollback_path.name != f"sha256-{rollback_sha256}.json"
        or deployment_path.name != f"sha256-{deployment_sha256}.json"
    ):
        raise DeploymentError("staged receipt content-addressed path mismatch")
    transition_evidence = (
        _verify_staged_transition_evidence(
            value["transition_evidence"],
            staging_root,
        )
        if bridge_stage
        else ()
    )
    artifact_values = value["artifacts"]
    if not isinstance(artifact_values, list) or not artifact_values:
        raise DeploymentError("staged deployment artifact inventory is invalid")
    root = _open_root(staging_root, "staged deployment root")
    private_fd = -1
    artifacts: list[StagedArtifact] = []
    snapshots: list[_FileSnapshot] = []
    try:
        private_fd, private_path = _open_private_root(staging_root, create=False)
        if private_path != staging_root:
            raise DeploymentError("staged deployment root is not canonical")
        for index, item in enumerate(artifact_values):
            item = _exact(
                item,
                {"role", "relative_path", "staged", "installed"},
                f"staged deployment artifacts[{index}]",
            )
            role = _text(item["role"], f"staged deployment artifacts[{index}].role")
            relative_path, components = _relative_path(
                item["relative_path"],
                f"staged deployment artifacts[{index}].relative_path",
            )
            staged = _receipt_file_binding(
                item["staged"],
                f"staged deployment artifacts[{index}].staged",
            )
            installed = _receipt_file_binding(
                item["installed"],
                f"staged deployment artifacts[{index}].installed",
            )
            installed_target = canonical_root.joinpath(*components)
            if control_stage or manual_stage or bridge_stage:
                selector_targets = {
                    "active-record": (
                        "candidate/active.json",
                        canonical_root / "active.json",
                    ),
                    "deployment-alias": (
                        "candidate/deployment.json",
                        canonical_root / "deployment.json",
                    ),
                    "prior-active-record": (
                        "preimage/active.json",
                        canonical_root / "active.json",
                    ),
                    "prior-deployment-alias": (
                        "preimage/deployment.json",
                        canonical_root / "deployment.json",
                    ),
                }
                control_targets = {
                    role: (
                        f"candidate/{_control_relative_path(role)}",
                        canonical_root / _control_relative_path(role),
                    )
                    for role in CONTROL_PREIMAGE_ROLES
                }
                control_targets.update(
                    {
                        f"prior-{role}": (
                            f"preimage/{_control_relative_path(role)}",
                            canonical_root / _control_relative_path(role),
                        )
                        for role in CONTROL_PREIMAGE_ROLES
                    }
                )
                control_targets.update(selector_targets)
                if role in control_targets:
                    expected_relative, installed_target = control_targets[role]
                    if relative_path != expected_relative:
                        raise DeploymentError(
                            "control maintenance staged path binding disagrees"
                        )
                elif relative_path.startswith(("candidate/", "preimage/")):
                    raise DeploymentError(
                        "control maintenance staged path divergence is not authorized"
                    )
            elif routine_stage:
                selector_targets = {
                    "active-record": (
                        "candidate/active.json",
                        canonical_root / "active.json",
                    ),
                    "deployment-alias": (
                        "candidate/deployment.json",
                        canonical_root / "deployment.json",
                    ),
                    "prior-active-record": (
                        "preimage/active.json",
                        canonical_root / "active.json",
                    ),
                    "prior-deployment-alias": (
                        "preimage/deployment.json",
                        canonical_root / "deployment.json",
                    ),
                }
                if role in selector_targets:
                    expected_relative, installed_target = selector_targets[role]
                    if relative_path != expected_relative:
                        raise DeploymentError(
                            "routine staged selector path binding disagrees"
                        )
                elif relative_path.startswith(("candidate/", "preimage/")):
                    raise DeploymentError(
                        "routine staged path divergence is not authorized"
                    )
            if (
                staged["path"] != str(staging_root.joinpath(*components))
                or installed["path"] != str(installed_target)
                or staged["length"] != installed["length"]
                or staged["sha256"] != installed["sha256"]
                or staged["owner"] != os.geteuid()
                or installed["owner"] != os.geteuid()
                or staged["mode"] != 0o600
                or installed["mode"] not in {0o500, 0o600}
            ):
                raise DeploymentError("staged deployment artifact binding disagrees")
            snapshot = _capture_regular(
                root,
                components,
                f"staged deployment artifact {role}",
                limit=MAX_INTERPRETER_BYTES,
            )
            if snapshot is None:
                raise DeploymentError("staged deployment artifact is missing")
            snapshots.append(snapshot)
            metadata = os.fstat(snapshot.file_fd)
            if (
                snapshot.raw.__len__() != staged["length"]
                or hashlib.sha256(snapshot.raw).hexdigest() != staged["sha256"]
                or metadata.st_uid != staged["owner"]
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != staged["mode"]
            ):
                raise DeploymentError("staged deployment artifact bytes disagree")
            artifacts.append(
                StagedArtifact(
                    role,
                    relative_path,
                    Path(staged["path"]),
                    Path(installed["path"]),
                    snapshot.raw,
                    _freeze(staged),
                    _freeze(installed),
                )
            )
        relative_paths = [item.relative_path for item in artifacts]
        if relative_paths != sorted(relative_paths) or len(relative_paths) != len(
            set(relative_paths)
        ):
            raise DeploymentError(
                "staged deployment artifacts are not sorted and unique"
            )
        for snapshot in snapshots:
            _recheck_file(snapshot)
        _recheck_root(root, "staged deployment root")
    finally:
        if private_fd >= 0:
            os.close(private_fd)
        for snapshot in reversed(snapshots):
            _close_file(snapshot)
        os.close(root.fd)
    expected_files = {item.relative_path: item.raw for item in artifacts}
    expected_files.update(
        {item.relative_path: item.raw for item in transition_evidence}
    )
    expected_files["stage.json"] = raw
    _verify_staged_inventory(staging_root, expected_files)
    by_role = {item.role: item for item in artifacts}
    if len(by_role) != len(artifacts):
        duplicate_roles = [item.role for item in artifacts]
        allowed_duplicates = duplicate_roles.count("validator-module")
        if len(by_role) + max(allowed_duplicates - 1, 0) != len(artifacts):
            raise DeploymentError("staged deployment artifact roles conflict")
    try:
        rollback_artifact = next(
            item for item in artifacts if item.role == "rollback-receipt"
        )
        deployment_artifact = next(
            item for item in artifacts if item.role == "deployment-receipt"
        )
        deployment_alias = next(
            item for item in artifacts if item.role == "deployment-alias"
        )
    except StopIteration:
        raise DeploymentError(
            "staged deployment receipt artifacts are incomplete"
        ) from None
    if (
        rollback_artifact.installed_path != rollback_path
        or hashlib.sha256(rollback_artifact.raw).hexdigest() != rollback_sha256
        or deployment_artifact.installed_path != deployment_path
        or hashlib.sha256(deployment_artifact.raw).hexdigest() != deployment_sha256
        or deployment_alias.installed_path != canonical_root / "deployment.json"
        or deployment_alias.raw != deployment_artifact.raw
    ):
        raise DeploymentError("staged deployment receipt artifact binding disagrees")
    rollback_value = _parse_canonical_json(
        rollback_artifact.raw,
        "staged rollback receipt",
    )
    deployment_value = _parse_canonical_json(
        deployment_artifact.raw,
        "staged canonical deployment receipt",
    )
    if (
        rollback_value.get("contract") != ROLLBACK_RECEIPT_CONTRACT
        or deployment_value.get("contract") != DEPLOYMENT_RECEIPT_CONTRACT
    ):
        raise DeploymentError("staged canonical receipt contract mismatch")
    _content_sha256(rollback_value, "staged rollback receipt")
    _content_sha256(deployment_value, "staged canonical deployment receipt")
    if first_install_stage:
        first_install_policy = _staged_compatibility_policy(
            deployment_value,
            _staged_artifact_for_role(tuple(artifacts), "policy"),
            "staged first-install candidate",
        )
        first_install_source = _active_receipt_source(
            deployment_value,
            canonical_root,
        )
        if not _policy_covers_source(first_install_policy, first_install_source):
            raise DeploymentError(
                "staged first-install source is outside candidate policy"
            )
    if manual_stage:
        manual_active = _staged_artifact_for_role(
            tuple(artifacts),
            "active-record",
        )
        _activation_journal_smoke_shape(
            deployment_value["smoke"],
            canonical_root,
            os.geteuid(),
            hashlib.sha256(manual_active.raw).hexdigest(),
            "staged manual rollback smoke",
        )
    else:
        _verify_smoke_receipt(
            deployment_value,
            tuple(artifacts),
            manifest_relative_path=(
                "candidate/smoke/bundle/manifest.json"
                if control_stage or bridge_stage
                else "smoke/bundle/manifest.json"
            ),
        )
    if routine_stage or control_stage or manual_stage or bridge_stage:
        _verify_routine_stage_receipts(
            stage_value=value,
            rollback_value=rollback_value,
            deployment_value=deployment_value,
            artifacts=tuple(artifacts),
            canonical_root=canonical_root,
            rollback_path=rollback_path,
            rollback_sha256=rollback_sha256,
            control_stage=control_stage or bridge_stage,
            manual_stage=manual_stage,
            bridge_stage=bridge_stage,
        )
    if bridge_stage:
        _verify_bridge_stage_authority(
            stage_value=value,
            deployment_value=deployment_value,
            transition_evidence=transition_evidence,
            canonical_root=canonical_root,
            staging_root=staging_root,
        )
    _verify_private_staged_inventory(staging_root, expected_files)
    return VerifiedDeploymentStage(
        _freeze(value),
        raw,
        stage_path,
        tuple(artifacts),
        transition_evidence,
    )


def _validate_first_install_request(request: FirstInstallRequest) -> None:
    if type(request) is not FirstInstallRequest:
        raise DeploymentError("first-install request type mismatch")
    if not isinstance(request.candidate_root, Path) or not isinstance(
        request.canonical_root, Path
    ):
        raise DeploymentError("first-install request paths must be Path values")
    for label, raw in (
        ("source selection", request.source_selection_raw),
        ("runtime qualification", request.runtime_qualification_raw),
    ):
        if type(raw) is not bytes:
            raise DeploymentError(f"first-install {label} must be exact bytes")
    selection = _parse_source_selection(request.source_selection_raw)
    _validate_source_evidence(selection, request.source_evidence)
    _sha256(
        request.maintenance_transaction_sha256,
        "first-install maintenance transaction digest",
    )


def _prepare_first_install_against_precondition(
    request: FirstInstallRequest,
    precondition: FirstInstallPrecondition,
) -> PreparedFirstInstall:
    _validate_first_install_request(request)
    if type(precondition) is not FirstInstallPrecondition:
        raise DeploymentError("first-install recorded precondition type mismatch")
    if precondition.canonical_root != request.canonical_root:
        raise DeploymentError("first-install recorded precondition root disagrees")
    selection = _parse_source_selection(request.source_selection_raw)
    evidence = _validate_source_evidence(selection, request.source_evidence)
    snapshot = _snapshot_candidate_tree(request.candidate_root)
    source = _bind_candidate_source_evidence(
        snapshot,
        request.source_selection_raw,
        selection,
        evidence,
    )
    qualification = _parse_runtime_qualification(request.runtime_qualification_raw)
    plan = _plan_first_install(
        source,
        qualification,
        precondition,
        request.maintenance_transaction_sha256,
    )
    controller = _artifact_for_role(plan, "controller")
    policy = _artifact_for_role(plan, "policy")
    facts = FirstInstallAuthorizationFacts(
        canonical_root=precondition.canonical_root,
        effective_uid=os.geteuid(),
        plan_sha256=plan.plan_sha256,
        maintenance_transaction_sha256=plan.maintenance_transaction_sha256,
        candidate_controller_sha256=controller.sha256,
        candidate_policy_sha256=policy.sha256,
        source_selection_sha256=source.source_selection_sha256,
        source_evidence_sha256=source.source_evidence_sha256,
    )
    return PreparedFirstInstall(plan, facts)


_DEPLOYMENT_RECEIPT_KEYS = {
    "schema_version",
    "contract",
    "sequence",
    "prior_receipt_sha256",
    "canonical_root",
    "effective_uid",
    "activation_lock",
    "control_set",
    "interpreter",
    "process_profile",
    "active",
    "trust_context",
    "historical_trust_contexts",
    "platform",
    "source",
    "runtime_closure",
    "contracts",
    "providers",
    "role_inventory",
    "compatibility_policy",
    "authorization",
    "rollback",
    "smoke",
    "content_sha256",
}


def _validate_deployment_request(request: DeploymentRequest) -> None:
    if type(request) is not DeploymentRequest:
        raise DeploymentError("routine deployment request type mismatch")
    if not isinstance(request.candidate_root, Path) or not isinstance(
        request.canonical_root,
        Path,
    ):
        raise DeploymentError("routine deployment request paths must be Path values")
    for label, raw in (
        ("source selection", request.source_selection_raw),
        ("runtime qualification", request.runtime_qualification_raw),
    ):
        if type(raw) is not bytes:
            raise DeploymentError(f"routine deployment {label} must be exact bytes")
    selection = _parse_source_selection(request.source_selection_raw)
    _validate_source_evidence(selection, request.source_evidence)
    _sha256(
        request.maintenance_transaction_sha256,
        "routine deployment maintenance transaction digest",
    )
    _sha256(
        request.expected_active_receipt_sha256,
        "routine deployment expected active receipt digest",
    )


def _validate_bridge_transition_request(
    request: BridgeTransitionRequest,
    staging_root: Path,
) -> None:
    if type(request) is not BridgeTransitionRequest:
        raise DeploymentError("bridge transition request type mismatch")
    _validate_deployment_request(request.deployment)
    if not isinstance(request.release_manifest_path, Path):
        raise DeploymentError("bridge transition manifest path must be a Path value")
    _normalized_absolute_path(
        request.release_manifest_path,
        "bridge transition manifest path",
    )
    if type(request.endpoint_projection_raw) is not bytes:
        raise DeploymentError("bridge transition endpoint must be exact bytes")
    if request.execution_class not in {"isolated-rehearsal", "live-migration"}:
        raise DeploymentError("bridge transition execution class is unsupported")
    if not isinstance(staging_root, Path):
        raise DeploymentError("bridge transition staging root must be a Path value")
    _normalized_absolute_path(staging_root, "bridge transition staging root")


def _receipt_source_evidence(
    source: Mapping[str, Any],
    label: str,
) -> tuple[
    str | None,
    str,
    Mapping[str, Any] | None,
    str,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
]:
    mode = _text(source["mode"], f"{label}.mode")
    details = source["details"]
    if mode in {"harness_snapshot", "publisher_channel"}:
        details = _exact(
            details,
            {"channel", "trust_class", "lineage"},
            f"{label}.details",
        )
        raw_lineage = _exact(
            details["lineage"],
            {"lineage_id", "sequence"},
            f"{label}.details.lineage",
        )
        channel = _token(details["channel"], f"{label}.details.channel")
        trust_class = _token(details["trust_class"], f"{label}.details.trust_class")
        lineage: Mapping[str, Any] | None = _freeze(
            {
                "lineage_id": _token(
                    raw_lineage["lineage_id"], f"{label}.details.lineage_id"
                ),
                "sequence": _nonnegative_integer(
                    raw_lineage["sequence"], f"{label}.details.lineage.sequence"
                ),
            }
        )
    elif mode == "exact_release":
        details = _exact(
            details,
            {"trust_class", "revision", "subtree_sha256"},
            f"{label}.details",
        )
        trust_class = _token(details["trust_class"], f"{label}.details.trust_class")
        if (
            _git_revision(details["revision"], f"{label}.details.revision")
            != source["revision"]
            or _sha256(details["subtree_sha256"], f"{label}.details.subtree_sha256")
            != source["subtree_sha256"]
        ):
            raise DeploymentError(f"{label} exact-release details disagree")
        channel = None
        lineage = None
    else:
        raise DeploymentError(f"{label}.mode is outside the installable receipt domain")
    evidence = source["source_evidence"]
    common = {"kind", "source_evidence_sha256"}
    if mode == "harness_snapshot":
        evidence = _exact(
            evidence,
            common
            | {
                "adapter_sha256",
                "manager_binding_sha256",
                "manager_binding_content_sha256",
                "manager_receipt_sha256",
            },
            f"{label}.source_evidence",
        )
        binding_sha256 = _sha256(
            evidence["manager_binding_sha256"], f"{label}.manager_binding"
        )
        binding_content_sha256 = _sha256(
            evidence["manager_binding_content_sha256"],
            f"{label}.manager_binding_content",
        )
        record_sha256 = _sha256(
            evidence["manager_receipt_sha256"], f"{label}.manager_receipt"
        )
        resolver = None
        adapter_sha256 = _sha256(evidence["adapter_sha256"], f"{label}.adapter")
    elif mode == "publisher_channel":
        evidence = _exact(
            evidence,
            common
            | {
                "resolver",
                "adapter_sha256",
                "publisher_binding_sha256",
                "publisher_binding_content_sha256",
                "publisher_record_sha256",
            },
            f"{label}.source_evidence",
        )
        binding_sha256 = _sha256(
            evidence["publisher_binding_sha256"], f"{label}.publisher_binding"
        )
        binding_content_sha256 = _sha256(
            evidence["publisher_binding_content_sha256"],
            f"{label}.publisher_binding_content",
        )
        record_sha256 = _sha256(
            evidence["publisher_record_sha256"], f"{label}.publisher_record"
        )
        resolver = _token(evidence["resolver"], f"{label}.resolver")
        adapter_sha256 = _sha256(evidence["adapter_sha256"], f"{label}.adapter")
    else:
        evidence = _exact(evidence, common, f"{label}.source_evidence")
        binding_sha256 = None
        binding_content_sha256 = None
        record_sha256 = None
        resolver = None
        adapter_sha256 = None
    if binding_sha256 == EMPTY_SHA256 or record_sha256 == EMPTY_SHA256:
        raise DeploymentError(f"{label} source evidence has an empty-byte identity")
    if evidence["kind"] != mode:
        raise DeploymentError(f"{label} source evidence kind disagrees")
    expected_evidence_sha256 = _digest(
        {
            "contract": SOURCE_EVIDENCE_CONTRACT,
            "mode": mode,
            **(
                {
                    "binding_sha256": binding_sha256,
                    "record_sha256": record_sha256,
                }
                if binding_sha256 is not None and record_sha256 is not None
                else {}
            ),
        }
    )
    evidence_sha256 = _sha256(
        evidence["source_evidence_sha256"], f"{label}.source_evidence"
    )
    if evidence_sha256 != expected_evidence_sha256:
        raise DeploymentError(f"{label} aggregate source evidence disagrees")
    return (
        channel,
        trust_class,
        lineage,
        evidence_sha256,
        binding_content_sha256,
        binding_sha256,
        record_sha256,
        resolver,
        adapter_sha256,
    )


def _active_receipt_source(
    receipt: Mapping[str, Any],
    canonical_root: Path,
) -> CandidateSource:
    label = "active deployment receipt source"
    source = _exact(
        _thaw(receipt["source"]),
        {
            "mode",
            "plugin_id",
            "publisher_id",
            "manifest_author",
            "repository_id",
            "repository_url",
            "release_version",
            "revision",
            "subtree_sha256",
            "source_authority",
            "details",
            "source_selection_sha256",
            "source_selection_content_sha256",
            "source_evidence",
            "agent_plugin_manifest_sha256",
            "claude_manifest_sha256",
            "provider_declaration_sha256",
            "provider_declaration_content_sha256",
        },
        label,
    )
    source_mode = _text(source["mode"], f"{label}.mode")
    (
        channel,
        trust_class,
        lineage,
        source_evidence_sha256,
        binding_content_sha256,
        binding_sha256,
        record_sha256,
        resolver,
        adapter_sha256,
    ) = _receipt_source_evidence(
        source,
        label,
    )
    provider: _ParsedProvider | None = None
    external = [
        _thaw(item)
        for item in receipt["providers"]
        if isinstance(item, Mapping) and item.get("intrinsic") is False
    ]
    declaration_sha256 = source["provider_declaration_sha256"]
    declaration_content_sha256 = source["provider_declaration_content_sha256"]
    authority_profile: str | None = None
    if declaration_sha256 is None or declaration_content_sha256 is None:
        if declaration_sha256 is not None or declaration_content_sha256 is not None:
            raise DeploymentError(f"{label} provider declaration binding disagrees")
        if external:
            raise DeploymentError(f"{label} provider inventory disagrees")
    else:
        _sha256(declaration_sha256, f"{label}.provider_declaration_sha256")
        _sha256(
            declaration_content_sha256,
            f"{label}.provider_declaration_content_sha256",
        )
        matches = [
            item for item in external if item.get("plugin_id") == source["plugin_id"]
        ]
        if len(matches) != 1:
            raise DeploymentError(f"{label} provider inventory is ambiguous")
        projection = matches[0]
        authority_profile = _token(
            projection.get("authority_profile"),
            f"{label}.authority_profile",
        )
        validators: list[_DeclaredValidator] = []
        for index, item in enumerate(projection.get("validators", [])):
            if not isinstance(item, dict):
                raise DeploymentError(f"{label} validator inventory is invalid")
            validators.append(
                _DeclaredValidator(
                    _token(item.get("validator_id"), f"{label}.validators[{index}].id"),
                    _text(
                        item.get("contract"), f"{label}.validators[{index}].contract"
                    ),
                    _sha256(
                        item.get("implementation_sha256"),
                        f"{label}.validators[{index}].implementation",
                    ),
                    _token(
                        item.get("entrypoint"),
                        f"{label}.validators[{index}].entrypoint",
                    ),
                    (),
                    {
                        "state": _token(
                            item.get("state"), f"{label}.validators[{index}].state"
                        ),
                        "usable_for_new_publication": item.get(
                            "usable_for_new_publication"
                        ),
                    },
                )
            )
        provider = _ParsedProvider(
            _token(projection.get("plugin_id"), f"{label}.provider.plugin_id"),
            _text(projection.get("publisher"), f"{label}.provider.publisher"),
            _text(projection.get("repository"), f"{label}.provider.repository"),
            authority_profile,
            declaration_content_sha256,
            tuple(_freeze(item) for item in projection.get("producers", [])),
            tuple(_freeze(item) for item in projection.get("issuers", [])),
            tuple(validators),
        )
    return CandidateSource(
        _token(source["plugin_id"], f"{label}.plugin_id"),
        source_mode,
        _token(source["publisher_id"], f"{label}.publisher_id"),
        _freeze(
            _manifest_author(source["manifest_author"], f"{label}.manifest_author")
        ),
        _repository_id(source["repository_id"], f"{label}.repository_id"),
        _text(source["repository_url"], f"{label}.repository_url"),
        _text(source["release_version"], f"{label}.release_version"),
        _git_revision(source["revision"], f"{label}.revision"),
        _sha256(source["subtree_sha256"], f"{label}.subtree_sha256"),
        _token(source["source_authority"], f"{label}.source_authority"),
        channel,
        trust_class,
        lineage,
        source_evidence_sha256,
        _sha256(
            source["source_selection_content_sha256"],
            f"{label}.source_selection_content",
        ),
        _sha256(source["source_selection_sha256"], f"{label}.source_selection"),
        binding_content_sha256,
        binding_sha256,
        record_sha256,
        resolver,
        adapter_sha256,
        declaration_sha256,
        declaration_content_sha256,
        _sha256(
            source["agent_plugin_manifest_sha256"],
            f"{label}.agent_plugin_manifest",
        ),
        _sha256(source["claude_manifest_sha256"], f"{label}.claude_manifest"),
        authority_profile,
        provider,
        CandidateTree(canonical_root, (), _freeze({}), source["subtree_sha256"]),
    )


def _bridge_legacy_active_receipt_source(
    receipt: Mapping[str, Any],
    canonical_root: Path,
) -> _BridgeLegacyCandidateSource:
    """Adapt only the exact flattened harness source used by F5 and B1."""

    label = "bridge legacy deployment receipt source"
    source = _exact(
        _thaw(receipt["source"]),
        {
            "mode",
            "plugin_id",
            "publisher_id",
            "manifest_author",
            "repository_id",
            "repository_url",
            "release_version",
            "revision",
            "subtree_sha256",
            "source_authority",
            "channel",
            "manager_trust_class",
            "lineage",
            "source_selection_sha256",
            "source_selection_content_sha256",
            "manager_binding_sha256",
            "manager_binding_content_sha256",
            "manager_receipt_sha256",
            "claude_manifest_sha256",
            "codex_manifest_sha256",
            "provider_declaration_sha256",
            "provider_declaration_content_sha256",
        },
        label,
    )
    if source["mode"] != "harness_snapshot":
        raise DeploymentError("bridge legacy source mode is unsupported")
    lineage = _exact(
        source["lineage"],
        {"lineage_id", "sequence"},
        f"{label}.lineage",
    )
    binding_sha256 = _sha256(
        source["manager_binding_sha256"],
        f"{label}.manager_binding",
    )
    record_sha256 = _sha256(
        source["manager_receipt_sha256"],
        f"{label}.manager_receipt",
    )
    declaration_sha256 = source["provider_declaration_sha256"]
    declaration_content_sha256 = source["provider_declaration_content_sha256"]
    external = [
        item
        for item in receipt["providers"]
        if isinstance(item, Mapping) and item.get("intrinsic") is False
    ]
    if (
        declaration_sha256 is not None
        or declaration_content_sha256 is not None
        or external
    ):
        raise DeploymentError("bridge legacy external provider is unsupported")
    evidence_sha256 = _digest(
        {
            "contract": SOURCE_EVIDENCE_CONTRACT,
            "mode": "harness_snapshot",
            "binding_sha256": binding_sha256,
            "record_sha256": record_sha256,
        }
    )
    subtree_sha256 = _sha256(
        source["subtree_sha256"],
        f"{label}.subtree_sha256",
    )
    return _BridgeLegacyCandidateSource(
        _token(source["plugin_id"], f"{label}.plugin_id"),
        "harness_snapshot",
        _token(source["publisher_id"], f"{label}.publisher_id"),
        _freeze(
            _manifest_author(source["manifest_author"], f"{label}.manifest_author")
        ),
        _repository_id(source["repository_id"], f"{label}.repository_id"),
        _text(source["repository_url"], f"{label}.repository_url"),
        _text(source["release_version"], f"{label}.release_version"),
        _git_revision(source["revision"], f"{label}.revision"),
        subtree_sha256,
        _token(source["source_authority"], f"{label}.source_authority"),
        _token(source["channel"], f"{label}.channel"),
        _token(source["manager_trust_class"], f"{label}.manager_trust_class"),
        _freeze(
            {
                "lineage_id": _token(
                    lineage["lineage_id"],
                    f"{label}.lineage_id",
                ),
                "sequence": _nonnegative_integer(
                    lineage["sequence"],
                    f"{label}.lineage.sequence",
                ),
            }
        ),
        evidence_sha256,
        _sha256(
            source["source_selection_content_sha256"],
            f"{label}.source_selection_content",
        ),
        _sha256(
            source["source_selection_sha256"],
            f"{label}.source_selection",
        ),
        _sha256(
            source["manager_binding_content_sha256"],
            f"{label}.manager_binding_content",
        ),
        binding_sha256,
        record_sha256,
        None,
        None,
        None,
        None,
        _sha256(source["claude_manifest_sha256"], f"{label}.claude_manifest"),
        _sha256(source["codex_manifest_sha256"], f"{label}.codex_manifest"),
        None,
        None,
        CandidateTree(canonical_root, (), _freeze({}), subtree_sha256),
    )


def _capture_routine_receipt_file(
    value: object,
    *,
    expected_path: Path,
    byte_limit: int,
    label: str,
) -> tuple[dict[str, Any], bytes]:
    binding = _receipt_file_binding(value, label)
    if (
        binding["path"] != str(expected_path)
        or binding["owner"] != os.geteuid()
        or binding["mode"] not in {0o500, 0o600}
        or binding["length"] > byte_limit
    ):
        raise DeploymentError(f"{label} binding disagrees")
    raw = _capture_absolute_regular(
        expected_path,
        byte_limit,
        label,
    )
    metadata = expected_path.lstat()
    if (
        len(raw) != binding["length"]
        or hashlib.sha256(raw).hexdigest() != binding["sha256"]
        or metadata.st_uid != binding["owner"]
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != binding["mode"]
    ):
        raise DeploymentError(f"{label} bytes disagree")
    return binding, raw


def _routine_receipt_file_binding(
    value: object,
    *,
    expected_path: Path,
    byte_limit: int,
    label: str,
) -> dict[str, Any]:
    binding, _ = _capture_routine_receipt_file(
        value,
        expected_path=expected_path,
        byte_limit=byte_limit,
        label=label,
    )
    return binding


def _active_runtime_record_shape(
    receipt: Mapping[str, Any],
    active_raw: bytes,
    canonical_root: Path,
    label: str,
) -> dict[str, Any]:
    active = _exact(
        _parse_canonical_json(active_raw, label),
        {
            "schema_version",
            "contract",
            "generation",
            "runtime_contract",
            "interpreter",
            "public_release",
            "payloads",
            "content_sha256",
        },
        label,
    )
    if (
        type(active["schema_version"]) is not int
        or active["schema_version"] != 1
        or active["contract"] != ACTIVE_CONTRACT
        or active["runtime_contract"] != RUNTIME_CONTRACT
    ):
        raise DeploymentError(f"{label} contract mismatch")
    _content_sha256(active, label)
    payloads = active["payloads"]
    if not isinstance(payloads, list) or not payloads:
        raise DeploymentError(f"{label} payload inventory is invalid")
    normalized: list[dict[str, Any]] = []
    for index, item_value in enumerate(payloads):
        item = _exact(
            item_value,
            {"role", "relative_path", "length", "sha256"},
            f"{label}.payloads[{index}]",
        )
        role = _token(item["role"], f"{label}.payloads[{index}].role")
        relative, _ = _relative_path(
            item["relative_path"],
            f"{label}.payloads[{index}].relative_path",
        )
        length = _nonnegative_integer(
            item["length"],
            f"{label}.payloads[{index}].length",
        )
        digest = _sha256(item["sha256"], f"{label}.payloads[{index}].sha256")
        normalized.append(
            {
                "role": role,
                "relative_path": relative,
                "length": length,
                "sha256": digest,
            }
        )
    if normalized != payloads:
        raise DeploymentError(f"{label} payload inventory is not canonical")
    runtime_sha256 = _digest(
        {
            "contract": RUNTIME_ARTIFACT_MANIFEST_CONTRACT,
            "runtime_contract": RUNTIME_CONTRACT,
            "entrypoint_role": "entrypoint",
            "payloads": payloads,
        }
    )
    receipt_active = _exact(
        _thaw(receipt["active"]),
        {
            "record_path",
            "record_sha256",
            "generation",
            "runtime_contract",
            "runtime_implementation_sha256",
            "public_release",
        },
        f"{label} receipt binding",
    )
    receipt_interpreter = _thaw(receipt["interpreter"])
    expected_active_interpreter = {
        key: receipt_interpreter[key]
        for key in ("executable", "implementation", "version")
    }
    if (
        active["generation"] != f"sha256-{runtime_sha256}"
        or active["interpreter"] != expected_active_interpreter
        or receipt_active["record_path"] != str(canonical_root / "active.json")
        or receipt_active["generation"] != active["generation"]
        or receipt_active["runtime_implementation_sha256"] != runtime_sha256
        or receipt_active["runtime_contract"] != RUNTIME_CONTRACT
        or receipt_active["public_release"] != active["public_release"]
        or receipt_active["record_sha256"] != hashlib.sha256(active_raw).hexdigest()
    ):
        raise DeploymentError(f"{label} receipt binding disagrees")
    return active


def _validate_active_runtime_and_trust(
    receipt: Mapping[str, Any],
    active_raw: bytes,
    canonical_root: Path,
    *,
    source_parser: Callable[[Mapping[str, Any], Path], CandidateSource] | None = None,
) -> CandidateSource:
    active = _active_runtime_record_shape(
        receipt,
        active_raw,
        canonical_root,
        "active runtime record",
    )
    for index, item in enumerate(active["payloads"]):
        role = item["role"]
        _, components = _relative_path(
            item["relative_path"],
            f"active runtime payloads[{index}].relative_path",
        )
        length = item["length"]
        digest = item["sha256"]
        target = canonical_root / "generations" / active["generation"]
        target = target.joinpath(*components)
        raw = _capture_absolute_regular(
            target,
            max(length, 1),
            f"active runtime payload {role}",
        )
        metadata = target.lstat()
        if (
            len(raw) != length
            or hashlib.sha256(raw).hexdigest() != digest
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise DeploymentError(f"active runtime payload {role} binding disagrees")

    trust = _trust_context_binding_shape(
        receipt["trust_context"],
        canonical_root,
        "active trust-context binding",
    )
    trust_path = Path(trust["path"])
    trust_sha256 = trust["sha256"]
    trust_raw = _capture_absolute_regular(
        trust_path,
        MAX_TRUST_CONTEXT_BYTES,
        "active trust context",
    )
    if hashlib.sha256(trust_raw).hexdigest() != trust_sha256:
        raise DeploymentError("active trust-context digest disagrees")
    trust_value = _exact(
        _parse_canonical_json(trust_raw, "active trust context"),
        {
            "schema_version",
            "contract",
            "producers",
            "issuers",
            "validators",
            "content_sha256",
        },
        "active trust context",
    )
    if (
        trust_value["schema_version"] != 1
        or type(trust_value["schema_version"]) is not int
        or trust_value["contract"] != TRUST_CONTEXT_CONTRACT
    ):
        raise DeploymentError("active trust-context contract mismatch")
    _content_sha256(trust_value, "active trust context")
    role_inventory = _exact(
        _thaw(receipt["role_inventory"]),
        {"producers", "issuers", "validators"},
        "active role inventory",
    )
    if any(
        role_inventory[category] != trust_value[category]
        for category in ("producers", "issuers", "validators")
    ):
        raise DeploymentError("active trust role inventory disagrees")

    def load_module(path: Path, label: str) -> bytes:
        raw = _capture_absolute_regular(path, MAX_MODULE_BYTES, label)
        metadata = path.lstat()
        if (
            metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise DeploymentError(f"{label} disposition disagrees")
        return raw

    source, _ = _validate_provider_source_and_modules(
        receipt,
        canonical_root,
        load_module,
        "active deployment",
        source_parser=source_parser,
    )
    return source


def _retained_receipt_authorization(
    value: object,
    *,
    prior_receipt_sha256: str | None,
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DeploymentError(f"{label} schema drift")
    purpose = value.get("purpose")
    keys = {
        "contract",
        "purpose",
        "sha256",
        "content_sha256",
        "plan_sha256",
        "maintenance_transaction_sha256",
    }
    active_prior_purposes = {
        "routine-compatible-forward",
        "source-boundary-change",
        "complete-control-set-maintenance",
    }
    manual_purpose = "manual-exact-target-rollback"
    if purpose in active_prior_purposes or purpose == manual_purpose:
        keys.add("expected_active_receipt_sha256")
    if purpose == manual_purpose:
        keys.add("target_receipt_sha256")
    authorization = _exact(value, keys, label)
    for key in (
        "sha256",
        "content_sha256",
        "plan_sha256",
        "maintenance_transaction_sha256",
    ):
        _sha256(authorization[key], f"{label}.{key}")
    if (
        authorization["contract"] != DEPLOYER_AUTHORIZATION_CONTRACT
        or purpose not in {"first-install", *active_prior_purposes, manual_purpose}
        or (purpose == "first-install") != (prior_receipt_sha256 is None)
        or (
            purpose in {*active_prior_purposes, manual_purpose}
            and _sha256(
                authorization["expected_active_receipt_sha256"],
                f"{label}.expected_active_receipt_sha256",
            )
            != prior_receipt_sha256
        )
        or (
            purpose == manual_purpose
            and _sha256(
                authorization["target_receipt_sha256"],
                f"{label}.target_receipt_sha256",
            )
            == prior_receipt_sha256
        )
    ):
        raise DeploymentError(f"{label} binding disagrees")
    return authorization


def _trust_context_binding_shape(
    value: object,
    canonical_root: Path,
    label: str,
) -> dict[str, Any]:
    binding = _exact(value, {"path", "sha256"}, label)
    digest = _sha256(binding["sha256"], f"{label}.sha256")
    path = _normalized_absolute_path(
        Path(_text(binding["path"], f"{label}.path")),
        f"{label}.path",
    )
    if (
        path != canonical_root / "trust" / "contexts" / f"sha256-{digest}.json"
        or binding["path"] != str(path)
    ):
        raise DeploymentError(f"{label} binding disagrees")
    return binding


def _historical_trust_registry_shape(
    value: object,
    canonical_root: Path,
    label: str,
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        raise DeploymentError(f"{label} schema drift")
    registry: dict[str, dict[str, Any]] = {}
    ordered: list[str] = []
    for index, item_value in enumerate(value):
        item = _exact(
            item_value,
            {"path", "sha256", "state"},
            f"{label}[{index}]",
        )
        digest = _sha256(item["sha256"], f"{label}[{index}].sha256")
        path = _normalized_absolute_path(
            Path(_text(item["path"], f"{label}[{index}].path")),
            f"{label}[{index}].path",
        )
        if (
            path != canonical_root / "trust" / "contexts" / f"sha256-{digest}.json"
            or item["path"] != str(path)
            or item["state"] not in {"historical-usable", "revoked"}
            or digest in registry
        ):
            raise DeploymentError(f"{label} binding disagrees")
        registry[digest] = item
        ordered.append(digest)
    if ordered != sorted(ordered):
        raise DeploymentError(f"{label} is not ordered")
    return registry


def _retained_historical_trust_registry(
    value: object,
    canonical_root: Path,
    label: str,
) -> dict[str, dict[str, Any]]:
    registry = _historical_trust_registry_shape(value, canonical_root, label)
    for digest, item in registry.items():
        path = Path(item["path"])
        raw = _capture_absolute_regular(path, MAX_TRUST_CONTEXT_BYTES, label)
        metadata = path.lstat()
        if (
            hashlib.sha256(raw).hexdigest() != digest
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise DeploymentError(f"{label} bytes disagree")
    return registry


def _expected_routine_historical_trust(
    *,
    current_trust: Mapping[str, Any],
    prior_trust: Mapping[str, Any],
    prior_history: Mapping[str, dict[str, Any]],
    label: str,
) -> dict[str, dict[str, Any]]:
    expected = dict(prior_history)
    current_digest = current_trust["sha256"]
    reactivated = expected.pop(current_digest, None)
    if reactivated is not None and (
        reactivated["path"] != current_trust["path"]
        or reactivated["sha256"] != current_digest
        or reactivated["state"] != "historical-usable"
    ):
        raise DeploymentError(f"{label} reactivated trust binding disagrees")
    prior_digest = prior_trust["sha256"]
    if prior_digest != current_digest:
        if prior_digest in expected:
            raise DeploymentError(f"{label} prior active trust is already historical")
        expected[prior_digest] = {
            "path": prior_trust["path"],
            "sha256": prior_digest,
            "state": "historical-usable",
        }
    if current_digest in expected:
        raise DeploymentError(f"{label} active trust remains historical")
    return expected


def _retained_bridge_migration_shape(
    value: object,
    receipt: Mapping[str, Any],
    label: str,
) -> Mapping[str, Any]:
    common = {
        "schema_version",
        "contract",
        "edge",
        "purpose",
        "execution_class",
        "maintenance_transaction_sha256",
        "deployment_authorization_sha256",
        "transition_authorization_sha256",
        "expected_active_receipt_core_sha256",
        "bridge_identity_sha256",
        "release_manifest_sha256",
        "endpoint_projection_sha256",
    }
    if not isinstance(value, Mapping):
        raise DeploymentError(f"{label} schema drift")
    execution_class = value.get("execution_class")
    keys = common | (
        {"prior_rehearsal"} if execution_class == "live-migration" else set()
    )
    migration = _exact(value, keys, label)
    edge = _exact(migration["edge"], {"from", "to", "via"}, f"{label}.edge")
    for key in (
        "maintenance_transaction_sha256",
        "deployment_authorization_sha256",
        "transition_authorization_sha256",
        "expected_active_receipt_core_sha256",
        "bridge_identity_sha256",
        "release_manifest_sha256",
        "endpoint_projection_sha256",
    ):
        _sha256(migration[key], f"{label}.{key}")
    if (
        type(migration["schema_version"]) is not int
        or migration["schema_version"] != 1
        or migration["contract"] != BRIDGE_MIGRATION_PROJECTION_CONTRACT
        or edge != {"from": "freeze5", "to": "tw4", "via": "bridge"}
        or migration["purpose"] != "bridge-transition"
        or execution_class not in {"isolated-rehearsal", "live-migration"}
    ):
        raise DeploymentError(f"{label} binding disagrees")
    core = {
        key: _thaw(item)
        for key, item in receipt.items()
        if key not in {"migration", "content_sha256"}
    }
    if migration["expected_active_receipt_core_sha256"] != _digest(core):
        raise DeploymentError(f"{label} active receipt core disagrees")
    if execution_class == "live-migration":
        _bridge_prior_rehearsal(
            migration["prior_rehearsal"],
            f"{label}.prior_rehearsal",
        )
    return _freeze(migration)


def _receipt_profile(
    profile: str,
) -> tuple[int, str, Mapping[str, str], Callable[[Mapping[str, Any], Path], Any]]:
    if profile == CURRENT_RECEIPT_PROFILE:
        return 2, DEPLOYMENT_RECEIPT_CONTRACT, RECEIPT_CONTRACTS, _active_receipt_source
    if profile == BRIDGE_LEGACY_RECEIPT_PROFILE:
        return (
            1,
            LEGACY_DEPLOYMENT_RECEIPT_CONTRACT,
            LEGACY_RECEIPT_CONTRACTS,
            _bridge_legacy_active_receipt_source,
        )
    raise DeploymentError("deployment receipt profile is unsupported")


def _validate_retained_receipt_header(
    value: object,
    canonical_root: Path,
    activation_lock: Mapping[str, Any],
    label: str,
    *,
    receipt_profile: str = CURRENT_RECEIPT_PROFILE,
    bridge_migration: bool | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    schema_version, contract, contracts, source_parser = _receipt_profile(
        receipt_profile
    )
    if bridge_migration is None:
        bridge_migration = (
            receipt_profile == CURRENT_RECEIPT_PROFILE
            and isinstance(value, Mapping)
            and "migration" in value
        )
    if bridge_migration and receipt_profile != CURRENT_RECEIPT_PROFILE:
        raise DeploymentError(f"{label} legacy migration profile is unsupported")
    receipt_keys = _DEPLOYMENT_RECEIPT_KEYS | (
        {"migration"} if bridge_migration else set()
    )
    receipt = _exact(value, receipt_keys, label)
    _content_sha256(receipt, label)
    sequence = _nonnegative_integer(receipt["sequence"], f"{label}.sequence")
    prior = receipt["prior_receipt_sha256"]
    if prior is not None:
        prior = _sha256(prior, f"{label}.prior_receipt_sha256")
    if (
        type(receipt["schema_version"]) is not int
        or receipt["schema_version"] != schema_version
        or receipt["contract"] != contract
        or sequence < 1
        or (sequence == 1) != (prior is None)
        or receipt["canonical_root"] != str(canonical_root)
        or type(receipt["effective_uid"]) is not int
        or receipt["effective_uid"] != os.geteuid()
        or _thaw(receipt["activation_lock"]) != _thaw(activation_lock)
        or _thaw(receipt["process_profile"]) != PROCESS_PROFILE
    ):
        raise DeploymentError(f"{label} binding disagrees")
    active = _exact(
        receipt["active"],
        {
            "record_path",
            "record_sha256",
            "generation",
            "runtime_contract",
            "runtime_implementation_sha256",
            "public_release",
        },
        f"{label}.active",
    )
    runtime_sha256 = _sha256(
        active["runtime_implementation_sha256"],
        f"{label}.active.runtime_implementation_sha256",
    )
    _sha256(active["record_sha256"], f"{label}.active.record_sha256")
    release = _exact(
        active["public_release"],
        {"repository", "revision"},
        f"{label}.active.public_release",
    )
    _text(release["repository"], f"{label}.active.public_release.repository")
    _git_revision(release["revision"], f"{label}.active.public_release.revision")
    if (
        active["record_path"] != str(canonical_root / "active.json")
        or active["generation"] != f"sha256-{runtime_sha256}"
        or active["runtime_contract"] != RUNTIME_CONTRACT
    ):
        raise DeploymentError(f"{label} active binding disagrees")
    interpreter = _exact(
        receipt["interpreter"],
        {"executable", "implementation", "version", "executable_sha256"},
        f"{label}.interpreter",
    )
    _normalized_absolute_path(
        Path(_text(interpreter["executable"], f"{label}.interpreter.executable")),
        f"{label}.interpreter.executable",
    )
    _token(interpreter["implementation"], f"{label}.interpreter.implementation")
    version = _exact(
        interpreter["version"],
        {"major", "minor", "micro"},
        f"{label}.interpreter.version",
    )
    for key in version:
        _nonnegative_integer(version[key], f"{label}.interpreter.version.{key}")
    _sha256(interpreter["executable_sha256"], f"{label}.interpreter.executable_sha256")
    platform = _exact(
        receipt["platform"],
        {"system", "machine", "qualified_filesystem_class"},
        f"{label}.platform",
    )
    for key in platform:
        _platform_token(platform[key], f"{label}.platform.{key}")
    closure = _exact(
        receipt["runtime_closure"],
        {
            "supplier",
            "provenance",
            "qualification_class",
            "evidence_sha256",
            "dependency_classes",
            "qualification_content_sha256",
        },
        f"{label}.runtime_closure",
    )
    for key in ("supplier", "provenance", "qualification_class"):
        _token(closure[key], f"{label}.runtime_closure.{key}")
    for key in ("evidence_sha256", "qualification_content_sha256"):
        _sha256(closure[key], f"{label}.runtime_closure.{key}")
    dependencies = closure["dependency_classes"]
    if (
        not isinstance(dependencies, list)
        or dependencies != sorted(set(dependencies))
        or any(
            _token(item, f"{label}.runtime_closure.dependency_classes") != item
            for item in dependencies
        )
    ):
        raise DeploymentError(f"{label} runtime closure disagrees")
    if _thaw(receipt["contracts"]) != contracts:
        raise DeploymentError(f"{label} contract inventory disagrees")
    if bridge_migration:
        _retained_bridge_migration_shape(
            receipt["migration"],
            receipt,
            f"{label}.migration",
        )
    _validate_provider_source_and_modules(
        receipt,
        canonical_root,
        None,
        f"{label} provider structure",
        source_parser=source_parser,
    )
    authorization = _retained_receipt_authorization(
        receipt["authorization"],
        prior_receipt_sha256=prior,
        label=f"{label}.authorization",
    )
    _trust_context_binding_shape(
        receipt["trust_context"],
        canonical_root,
        f"{label}.trust_context",
    )
    _historical_trust_registry_shape(
        receipt["historical_trust_contexts"],
        canonical_root,
        f"{label}.historical_trust_contexts",
    )
    return receipt, authorization


class _RetainedReceiptDirectory:
    """One held private receipt directory and its exact flat file snapshot."""

    def __init__(self, canonical_root: Path) -> None:
        self.path = canonical_root / "receipts"
        self.root = _open_root(self.path, "retained receipt directory")
        self.snapshots: dict[str, _FileSnapshot] = {}
        try:
            metadata = os.fstat(self.root.fd)
            _reject_macos_allow_acl(self.root.fd, "retained receipt directory")
            if (
                metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise DeploymentError("retained receipt directory is not private")
            self.names = tuple(sorted(os.listdir(self.root.fd)))
        except BaseException:
            os.close(self.root.fd)
            raise

    def capture(
        self,
        digest: str,
        label: str,
    ) -> tuple[bytes, os.stat_result, Path]:
        name = f"sha256-{digest}.json"
        snapshot = self.snapshots.get(digest)
        if snapshot is None:
            snapshot = _capture_regular(
                self.root,
                (name,),
                label,
                limit=MAX_JSON_BYTES,
            )
            if snapshot is None:
                raise DeploymentError(f"{label} is missing")
            try:
                metadata = os.fstat(snapshot.file_fd)
                _reject_macos_allow_acl(snapshot.file_fd, label)
                if (
                    hashlib.sha256(snapshot.raw).hexdigest() != digest
                    or metadata.st_uid != os.geteuid()
                    or metadata.st_nlink != 1
                    or stat.S_IMODE(metadata.st_mode) != 0o600
                ):
                    raise DeploymentError(f"{label} bytes or disposition disagrees")
            except BaseException:
                _close_file(snapshot)
                raise
            self.snapshots[digest] = snapshot
        metadata = os.fstat(snapshot.file_fd)
        return snapshot.raw, metadata, self.path / name

    def verify(self, expected_names: set[str]) -> None:
        if (
            set(self.names) != expected_names
            or tuple(sorted(os.listdir(self.root.fd))) != self.names
        ):
            raise DeploymentError("retained receipt inventory disagrees")
        for snapshot in self.snapshots.values():
            _recheck_file(snapshot)
        _recheck_root(self.root, "retained receipt directory")

    def close(self) -> None:
        error: OSError | None = None
        for snapshot in reversed(tuple(self.snapshots.values())):
            try:
                _close_file(snapshot)
            except OSError as caught:
                if error is None:
                    error = caught
        try:
            os.close(self.root.fd)
        except OSError as caught:
            if error is None:
                error = caught
        if error is not None:
            raise error


def _validate_retained_selector_preimage(
    value: object,
    prior_unit: Mapping[str, Any],
    canonical_root: Path,
    label: str,
) -> dict[str, bytes]:
    if not isinstance(value, list) or len(value) != 2:
        raise DeploymentError(f"{label} schema drift")
    expected = (
        ("active-record", prior_unit["active_record"]),
        ("deployment-alias", prior_unit["deployment_receipt"]),
    )
    staged_paths: set[Path] = set()
    raws: dict[str, bytes] = {}
    for index, ((role, expected_installed), item_value) in enumerate(
        zip(expected, value)
    ):
        item = _exact(
            item_value,
            {"role", "staged", "installed"},
            f"{label}[{index}]",
        )
        installed = _receipt_file_binding(
            item["installed"],
            f"{label}[{index}].installed",
        )
        staged = _receipt_file_binding(
            item["staged"],
            f"{label}[{index}].staged",
        )
        staged_path = Path(staged["path"])
        if (
            item["role"] != role
            or installed != _thaw(expected_installed)
            or installed["owner"] != os.geteuid()
            or installed["mode"] != 0o600
            or installed["length"] > MAX_JSON_BYTES
            or staged["owner"] != os.geteuid()
            or staged["mode"] != 0o600
            or staged["length"] > MAX_JSON_BYTES
            or any(
                staged[key] != installed[key]
                for key in ("length", "sha256", "owner", "mode")
            )
            or staged_path == Path(installed["path"])
            or staged_path.is_relative_to(canonical_root)
            or staged_path in staged_paths
        ):
            raise DeploymentError(f"{label} binding disagrees")
        raw = _capture_absolute_regular(
            staged_path,
            MAX_JSON_BYTES,
            f"{label}[{index}] staged artifact",
        )
        metadata = staged_path.lstat()
        if (
            len(raw) != staged["length"]
            or hashlib.sha256(raw).hexdigest() != staged["sha256"]
            or metadata.st_uid != staged["owner"]
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != staged["mode"]
        ):
            raise DeploymentError(f"{label} bytes disagree")
        staged_paths.add(staged_path)
        raws[role] = raw
    return raws


def _validate_retained_receipt_policy(
    receipt: Mapping[str, Any],
    policy: CompatibilityPolicy,
    canonical_root: Path,
    label: str,
    *,
    source_parser: Callable[[Mapping[str, Any], Path], CandidateSource] | None = None,
) -> None:
    source = (
        _active_receipt_source(receipt, canonical_root)
        if source_parser is None
        else source_parser(receipt, canonical_root)
    )
    controls = _exact(
        _thaw(receipt["control_set"]),
        set(CONTROL_SET_ROLES),
        f"{label} control set",
    )
    control_policy = _receipt_file_binding(
        controls["policy"],
        f"{label} control policy",
    )
    binding = _exact(
        _thaw(receipt["compatibility_policy"]),
        {"path", "length", "sha256", "owner", "mode", "content_sha256"},
        f"{label} compatibility policy",
    )
    declared_contracts = {
        key: policy.control_surface["contracts"][key] for key in RECEIPT_CONTRACTS
    }
    if (
        not _policy_covers_source(policy, source)
        or {key: binding[key] for key in control_policy} != control_policy
        or binding["sha256"] != policy.raw_sha256
        or binding["content_sha256"] != policy.content_sha256
        or _thaw(receipt["process_profile"])
        != _thaw(policy.control_surface["process_profile"])
        or _thaw(receipt["contracts"]) != declared_contracts
    ):
        raise DeploymentError(f"{label} is outside its compatibility policy")


def _validate_retained_control_preimage(
    value: object,
    prior: Mapping[str, Any],
    canonical_root: Path,
    label: str,
    *,
    policy_parser: Callable[[bytes], CompatibilityPolicy] | None = None,
) -> tuple[CompatibilityPolicy, Mapping[str, bytes]]:
    if (
        not isinstance(value, list)
        or len(value) != len(CONTROL_PREIMAGE_ROLES)
        or any(not isinstance(item, Mapping) for item in value)
        or [item.get("role") for item in value] != list(CONTROL_PREIMAGE_ROLES)
    ):
        raise DeploymentError(f"{label} schema drift")
    prior_controls = _exact(
        _thaw(prior["control_set"]),
        set(CONTROL_SET_ROLES),
        f"{label} prior control set",
    )
    prior_bindings = {
        role: _receipt_file_binding(
            prior_controls[role],
            f"{label} prior {role}",
        )
        for role in CONTROL_SET_ROLES
    }
    prior_bindings["smoke-bundle-manifest"] = _receipt_file_binding(
        prior["smoke"]["bundle"]["manifest"],
        f"{label} prior smoke-bundle manifest",
    )
    stage_root: Path | None = None
    staged_paths: set[Path] = set()
    prior_policy: CompatibilityPolicy | None = None
    control_raws: dict[str, bytes] = {}
    for index, role in enumerate(CONTROL_PREIMAGE_ROLES):
        item = _exact(
            value[index],
            {"role", "staged", "installed"},
            f"{label}[{index}]",
        )
        installed = _receipt_file_binding(
            item["installed"],
            f"{label}[{index}].installed",
        )
        staged = _receipt_file_binding(
            item["staged"],
            f"{label}[{index}].staged",
        )
        staged_path = Path(staged["path"])
        suffix = Path("preimage") / _control_relative_path(role)
        if stage_root is None:
            if staged_path.parts[-len(suffix.parts) :] != suffix.parts:
                raise DeploymentError(f"{label} binding disagrees")
            stage_root = staged_path.parents[len(suffix.parts) - 1]
            if stage_root.is_relative_to(
                canonical_root
            ) or canonical_root.is_relative_to(stage_root):
                raise DeploymentError(f"{label} binding disagrees")
        expected_staged_path = stage_root / suffix
        byte_limit = (
            MAX_JSON_BYTES
            if role in {"policy", "smoke-bundle-manifest"}
            else MAX_CANDIDATE_TREE_FILE_BYTES
        )
        expected_installed_path = canonical_root / _control_relative_path(role)
        expected_installed_mode = (
            0o600 if role in {"policy", "smoke-bundle-manifest"} else 0o500
        )
        if (
            item["role"] != role
            or installed != prior_bindings[role]
            or installed["path"] != str(expected_installed_path)
            or installed["owner"] != os.geteuid()
            or installed["mode"] != expected_installed_mode
            or installed["length"] > byte_limit
            or staged_path != expected_staged_path
            or staged_path in staged_paths
            or staged["owner"] != os.geteuid()
            or staged["mode"] != 0o600
            or staged["length"] > byte_limit
            or any(
                staged[key] != installed[key] for key in ("length", "sha256", "owner")
            )
        ):
            raise DeploymentError(f"{label} binding disagrees")
        parent = _open_root(staged_path.parent, f"{label}[{index}] parent")
        try:
            parent_metadata = os.fstat(parent.fd)
            _reject_macos_allow_acl(parent.fd, f"{label}[{index}] parent")
            if (
                parent_metadata.st_uid != os.geteuid()
                or stat.S_IMODE(parent_metadata.st_mode) != 0o700
            ):
                raise DeploymentError(f"{label} is not private")
            _recheck_root(parent, f"{label}[{index}] parent")
        finally:
            os.close(parent.fd)
        raw = _capture_absolute_regular(
            staged_path,
            byte_limit,
            f"{label}[{index}] staged artifact",
        )
        metadata = staged_path.lstat()
        if (
            len(raw) != staged["length"]
            or hashlib.sha256(raw).hexdigest() != staged["sha256"]
            or metadata.st_uid != staged["owner"]
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != staged["mode"]
        ):
            raise DeploymentError(f"{label} bytes disagree")
        if role == "policy":
            prior_policy = (
                _parse_compatibility_policy(raw)
                if policy_parser is None
                else policy_parser(raw)
            )
        control_raws[role] = raw
        staged_paths.add(staged_path)
    if prior_policy is None:
        raise DeploymentError(f"{label} prior policy is unavailable")
    return prior_policy, _freeze(control_raws)


def _validate_retained_receipt_chain_in_directory(
    receipt: Mapping[str, Any],
    receipt_raw: bytes,
    canonical_root: Path,
    retained_directory: _RetainedReceiptDirectory,
    *,
    initial_policy: CompatibilityPolicy,
    allowed_extra_names: frozenset[str] = frozenset(),
    receipt_profile: str = CURRENT_RECEIPT_PROFILE,
) -> RetainedReceiptChain:
    live_root_identity = _identity(canonical_root.lstat())
    live_lock_identity = _identity((canonical_root / "activation.lock").lstat())
    activation_lock = _thaw(receipt["activation_lock"])
    expected_names: set[str] = set()
    seen_receipts: set[str] = set()
    seen_rollbacks: set[str] = set()
    seen_digests: set[str] = set()
    deployment_receipts: list[tuple[str, Mapping[str, Any], bytes]] = []
    authority_edges: list[RetainedReceiptAuthorityEdge] = []
    top_sequence: int | None = None
    current = _thaw(receipt)
    current_raw = receipt_raw
    current_rollback: dict[str, Any] | None = None
    current_rollback_raw: bytes | None = None
    policy = initial_policy
    current_profile = receipt_profile

    while True:
        _, _, _, current_source_parser = _receipt_profile(current_profile)
        current_sha256 = hashlib.sha256(current_raw).hexdigest()
        if current_sha256 in seen_digests:
            raise DeploymentError("retained deployment receipt chain contains a cycle")
        seen_receipts.add(current_sha256)
        seen_digests.add(current_sha256)
        retained_raw, _, _ = retained_directory.capture(
            current_sha256,
            "retained deployment receipt",
        )
        if retained_raw != current_raw:
            raise DeploymentError("retained deployment receipt chain bytes disagree")
        expected_names.add(f"sha256-{current_sha256}.json")
        current, authorization = _validate_retained_receipt_header(
            current,
            canonical_root,
            activation_lock,
            "retained deployment receipt",
            receipt_profile=current_profile,
        )
        _validate_retained_receipt_policy(
            current,
            policy,
            canonical_root,
            "retained deployment receipt",
            source_parser=current_source_parser,
        )
        if top_sequence is None:
            top_sequence = current["sequence"]
            if len(retained_directory.names) != 2 * top_sequence + len(
                allowed_extra_names
            ) or not allowed_extra_names.issubset(retained_directory.names):
                raise DeploymentError("retained receipt inventory depth disagrees")
        if len(deployment_receipts) >= top_sequence:
            raise DeploymentError("retained deployment receipt depth disagrees")
        deployment_receipts.append((current_sha256, _freeze_json(current), current_raw))
        rollback_binding = _exact(
            current["rollback"],
            {"state", "path", "sha256"},
            "retained deployment rollback binding",
        )
        rollback_sha256 = _sha256(
            rollback_binding["sha256"],
            "retained deployment rollback digest",
        )
        if rollback_sha256 in seen_digests:
            raise DeploymentError("retained rollback receipt digest is reused")
        seen_rollbacks.add(rollback_sha256)
        seen_digests.add(rollback_sha256)
        rollback_raw, _, rollback_path = retained_directory.capture(
            rollback_sha256,
            "retained rollback receipt",
        )
        if (
            rollback_binding["state"] not in {"absent", "active"}
            or rollback_binding["path"] != str(rollback_path)
            or rollback_sha256 == current_sha256
        ):
            raise DeploymentError("retained deployment rollback binding disagrees")
        expected_names.add(f"sha256-{rollback_sha256}.json")
        rollback = _parse_canonical_json(rollback_raw, "retained rollback receipt")
        _content_sha256(rollback, "retained rollback receipt")
        if current_rollback is None:
            current_rollback = rollback
            current_rollback_raw = rollback_raw

        if rollback_binding["state"] == "absent":
            recorded = _recorded_first_install_precondition(rollback_raw)
            if (
                current["sequence"] != 1
                or current["prior_receipt_sha256"] is not None
                or authorization["purpose"] != "first-install"
                or recorded.canonical_root != canonical_root
                or _thaw(recorded.activation_lock) != activation_lock
                or recorded.root_identity[:4] != live_root_identity[:4]
                or recorded.activation_lock_identity != live_lock_identity
            ):
                raise DeploymentError("first active rollback receipt chain disagrees")
            break

        next_profile = (
            BRIDGE_LEGACY_RECEIPT_PROFILE
            if current_profile == CURRENT_RECEIPT_PROFILE and "migration" in current
            else current_profile
        )
        _, _, _, next_source_parser = _receipt_profile(next_profile)

        rollback_keys = {
            "schema_version",
            "contract",
            "state",
            "canonical_root",
            "effective_uid",
            "activation_lock",
            "deployment_receipt_absent",
            "precondition",
            "prior_receipt",
            "prior_activation_unit",
            "selector_preimage",
            "external_dependencies",
            "smoke",
            "content_sha256",
        }
        manual_rollback = authorization["purpose"] == "manual-exact-target-rollback"
        control_maintenance = "control_preimage" in rollback or manual_rollback
        if control_maintenance:
            rollback_keys.add("control_preimage")
        rollback = _exact(
            rollback,
            rollback_keys,
            "retained active rollback receipt",
        )
        precondition = _exact(
            rollback["precondition"],
            {
                "root_identity",
                "activation_lock_identity",
                "active_receipt_sha256",
            },
            "retained active rollback precondition",
        )
        root_identity = _activation_identity_vector(
            precondition["root_identity"],
            "retained active rollback root identity",
        )
        lock_identity = _activation_identity_vector(
            precondition["activation_lock_identity"],
            "retained active rollback activation-lock identity",
        )
        prior_sha256 = current["prior_receipt_sha256"]
        if (
            type(rollback["schema_version"]) is not int
            or rollback["schema_version"] != 1
            or rollback["contract"] != ROLLBACK_RECEIPT_CONTRACT
            or rollback["state"] != "active"
            or rollback["canonical_root"] != str(canonical_root)
            or type(rollback["effective_uid"]) is not int
            or rollback["effective_uid"] != os.geteuid()
            or rollback["activation_lock"] != activation_lock
            or rollback["deployment_receipt_absent"] is not False
            or current["sequence"] <= 1
            or prior_sha256 is None
            or authorization["purpose"]
            not in {
                "routine-compatible-forward",
                "source-boundary-change",
                "complete-control-set-maintenance",
                "manual-exact-target-rollback",
            }
            or authorization["expected_active_receipt_sha256"] != prior_sha256
            or precondition["active_receipt_sha256"] != prior_sha256
            or root_identity[:4] != live_root_identity[:4]
            or lock_identity != live_lock_identity
        ):
            raise DeploymentError("retained active rollback receipt disagrees")
        prior_raw, prior_metadata, prior_path = retained_directory.capture(
            prior_sha256,
            "retained prior deployment receipt",
        )
        prior_binding = _receipt_file_binding(
            rollback["prior_receipt"],
            "retained active rollback prior receipt",
        )
        expected_prior_binding = {
            "path": str(prior_path),
            "length": len(prior_raw),
            "sha256": prior_sha256,
            "owner": prior_metadata.st_uid,
            "mode": stat.S_IMODE(prior_metadata.st_mode),
        }
        if prior_binding != expected_prior_binding:
            raise DeploymentError("retained active rollback prior receipt disagrees")
        prior = _parse_canonical_json(prior_raw, "retained prior deployment receipt")
        _validate_retained_receipt_header(
            prior,
            canonical_root,
            activation_lock,
            "retained prior deployment receipt",
            receipt_profile=next_profile,
        )
        if current["sequence"] != prior["sequence"] + 1:
            raise DeploymentError(
                "retained deployment receipt sequence chain disagrees"
            )
        prior_unit = _activation_journal_unit_shape(
            rollback["prior_activation_unit"],
            canonical_root,
            os.geteuid(),
            "retained active rollback prior activation unit",
        )
        expected_prior_deployment = {
            "path": str(canonical_root / "deployment.json"),
            "length": len(prior_raw),
            "sha256": prior_sha256,
            "owner": os.geteuid(),
            "mode": 0o600,
        }
        prior_active = _exact(
            prior["active"],
            {
                "record_path",
                "record_sha256",
                "generation",
                "runtime_contract",
                "runtime_implementation_sha256",
                "public_release",
            },
            "retained prior deployment active binding",
        )
        prior_anchor = prior_unit["smoke"]["expected_anchor"]
        if (
            prior_unit["deployment_receipt"] != expected_prior_deployment
            or prior_unit["control_set"] != prior["control_set"]
            or prior_unit["smoke"] != prior["smoke"]
            or prior_active["record_path"] != str(canonical_root / "active.json")
            or prior_active["record_sha256"] != prior_unit["active_record"]["sha256"]
            or prior_active["generation"] != prior_anchor["generation"]
            or prior_active["runtime_contract"] != prior_anchor["runtime_contract"]
            or prior_active["runtime_implementation_sha256"]
            != prior_anchor["runtime_implementation_sha256"]
            or prior_active["public_release"] != prior_anchor["public_release"]
            or prior["trust_context"] != prior_unit["smoke"]["trust_context"]
        ):
            raise DeploymentError("retained active rollback prior unit disagrees")
        selector_raws = _validate_retained_selector_preimage(
            rollback["selector_preimage"],
            prior_unit,
            canonical_root,
            "retained active rollback selector preimage",
        )
        if selector_raws["deployment-alias"] != prior_raw:
            raise DeploymentError("retained prior deployment selector bytes disagree")
        _validate_active_runtime_and_trust(
            prior,
            selector_raws["active-record"],
            canonical_root,
            source_parser=next_source_parser,
        )
        prior_policy: CompatibilityPolicy | None = None
        control_raws: Mapping[str, bytes] = _freeze({})
        if control_maintenance:
            next_policy_parser = (
                _parse_bridge_legacy_compatibility_policy
                if next_profile == BRIDGE_LEGACY_RECEIPT_PROFILE
                else _parse_compatibility_policy
            )
            prior_policy, control_raws = _validate_retained_control_preimage(
                rollback["control_preimage"],
                prior,
                canonical_root,
                "retained active rollback control preimage",
                policy_parser=next_policy_parser,
            )
        external = _exact(
            rollback["external_dependencies"],
            {"interpreter", "runtime_closure", "process_profile", "receipt_parser"},
            "retained active rollback external dependencies",
        )
        parser = _exact(
            external["receipt_parser"],
            {
                "deployment_receipt_contract",
                "rollback_receipt_contract",
                "controller",
                "client",
            },
            "retained active rollback receipt parser",
        )
        if (
            external["interpreter"] != prior["interpreter"]
            or external["runtime_closure"] != prior["runtime_closure"]
            or external["process_profile"] != prior["process_profile"]
            or parser["deployment_receipt_contract"]
            != prior["contracts"]["deployment_receipt"]
            or parser["rollback_receipt_contract"]
            != prior["contracts"]["rollback_receipt"]
            or parser["controller"] != prior["control_set"]["controller"]
            or parser["client"] != prior["control_set"]["client"]
            or rollback["smoke"] != prior["smoke"]
            or rollback["smoke"] != prior_unit["smoke"]
        ):
            raise DeploymentError("retained active rollback authority disagrees")
        if not control_maintenance:
            stable_fields = (
                "control_set",
                "interpreter",
                "runtime_closure",
                "process_profile",
                "platform",
                "compatibility_policy",
                "contracts",
            )
            stable_smoke_fields = (
                "bundle",
                "producer",
                "validator",
                "expected_projection",
            )
            if any(current[key] != prior[key] for key in stable_fields) or any(
                current["smoke"][key] != prior["smoke"][key]
                for key in stable_smoke_fields
            ):
                raise DeploymentError(
                    "retained routine deployment stable surface disagrees"
                )
        current_history = _retained_historical_trust_registry(
            current["historical_trust_contexts"],
            canonical_root,
            "retained routine historical trust contexts",
        )
        prior_history = _retained_historical_trust_registry(
            prior["historical_trust_contexts"],
            canonical_root,
            "retained prior historical trust contexts",
        )
        prior_trust = _exact(
            prior["trust_context"],
            {"path", "sha256"},
            "retained prior trust context",
        )
        current_trust = _exact(
            current["trust_context"],
            {"path", "sha256"},
            "retained current trust context",
        )
        expected_history = _expected_routine_historical_trust(
            current_trust=current_trust,
            prior_trust=prior_trust,
            prior_history=prior_history,
            label="retained routine deployment history",
        )
        if current_history != expected_history:
            raise DeploymentError("retained routine historical trust closure disagrees")
        authority_edges.append(
            RetainedReceiptAuthorityEdge(
                successor_receipt_sha256=current_sha256,
                successor_receipt_value=_freeze_json(current),
                successor_receipt_raw=current_raw,
                rollback_receipt_sha256=rollback_sha256,
                rollback_receipt_value=_freeze_json(rollback),
                rollback_receipt_raw=rollback_raw,
                prior_receipt_sha256=prior_sha256,
                prior_receipt_value=_freeze_json(prior),
                prior_receipt_raw=prior_raw,
                prior_active_raw=selector_raws["active-record"],
                prior_activation_unit=_freeze_json(prior_unit),
                selector_raws=_freeze(selector_raws),
                control_raws=control_raws,
                authorization_purpose=authorization["purpose"],
                authorization_target_receipt_sha256=(
                    authorization["target_receipt_sha256"] if manual_rollback else None
                ),
            )
        )
        if prior_policy is not None:
            policy = prior_policy
        current = prior
        current_raw = prior_raw
        current_profile = next_profile

    if (
        top_sequence is None
        or len(deployment_receipts) != top_sequence
        or len(seen_receipts) != top_sequence
        or len(seen_rollbacks) != top_sequence
        or len(expected_names) != 2 * top_sequence
    ):
        raise DeploymentError("retained receipt chain depth disagrees")
    receipt_digests = {item[0] for item in deployment_receipts}
    for edge in authority_edges:
        target = edge.authorization_target_receipt_sha256
        if target is not None and (
            target not in receipt_digests or target == edge.successor_receipt_sha256
        ):
            raise DeploymentError("retained manual rollback target ancestry disagrees")
    retained_directory.verify(expected_names | set(allowed_extra_names))
    if current_rollback is None or current_rollback_raw is None:
        raise DeploymentError("retained rollback authority is unavailable")
    return RetainedReceiptChain(
        _freeze_json(current_rollback),
        current_rollback_raw,
        frozenset(expected_names),
        tuple(deployment_receipts),
        tuple(authority_edges),
    )


def _validate_retained_receipt_chain(
    receipt: Mapping[str, Any],
    receipt_raw: bytes,
    canonical_root: Path,
    *,
    active_policy: CompatibilityPolicy,
    allowed_extra_names: frozenset[str] = frozenset(),
    receipt_profile: str = CURRENT_RECEIPT_PROFILE,
) -> RetainedReceiptChain:
    retained_directory = _RetainedReceiptDirectory(canonical_root)
    try:
        return _validate_retained_receipt_chain_in_directory(
            receipt,
            receipt_raw,
            canonical_root,
            retained_directory,
            initial_policy=active_policy,
            allowed_extra_names=allowed_extra_names,
            receipt_profile=receipt_profile,
        )
    finally:
        retained_directory.close()


def _capture_active_deployment_precondition(
    path: Path,
    expected_receipt_sha256: str,
    *,
    receipt_profile: str = CURRENT_RECEIPT_PROFILE,
) -> ActiveDeploymentPrecondition:
    _, _, _, source_parser = _receipt_profile(receipt_profile)
    policy_parser = (
        _parse_bridge_legacy_compatibility_policy
        if receipt_profile == BRIDGE_LEGACY_RECEIPT_PROFILE
        else _parse_compatibility_policy
    )
    canonical_root = _normalized_absolute_path(path, "routine canonical root")
    expected = _sha256(expected_receipt_sha256, "routine expected active receipt")
    try:
        if canonical_root.resolve(strict=True) != canonical_root:
            raise DeploymentError("routine canonical root mapping disagrees")
    except DeploymentError:
        raise
    except (OSError, RuntimeError) as error:
        raise DeploymentError("routine canonical root is unavailable") from error
    root = _open_root(canonical_root, "routine canonical root")
    lock_fd = -1
    lock_acquired = False
    try:
        root_metadata = os.fstat(root.fd)
        if (
            root_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(root_metadata.st_mode) != 0o700
        ):
            raise DeploymentError("routine canonical root is not private")
        _reject_macos_allow_acl(root.fd, "routine canonical root")
        lock_before = os.stat("activation.lock", dir_fd=root.fd, follow_symlinks=False)
        lock_fd = os.open(
            "activation.lock",
            os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=root.fd,
        )
        lock_after = os.fstat(lock_fd)
        _reject_macos_allow_acl(lock_fd, "routine activation lock")
        if (
            _identity(lock_before) != _identity(lock_after)
            or not stat.S_ISREG(lock_after.st_mode)
            or lock_after.st_uid != os.geteuid()
            or lock_after.st_nlink != 1
            or lock_after.st_size != 0
            or stat.S_IMODE(lock_after.st_mode) != 0o600
        ):
            raise DeploymentError("routine activation lock binding disagrees")
        deadline = time.monotonic() + PROCESS_PROFILE["shared_lock_seconds"]
        while True:
            if _identity(os.fstat(root.fd)) != _identity(root_metadata) or _identity(
                os.fstat(lock_fd)
            ) != _identity(lock_after):
                raise DeploymentError("routine state changed before shared capture")
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno == errno.EINTR:
                    continue
                if error.errno not in {
                    errno.EACCES,
                    errno.EAGAIN,
                    errno.EWOULDBLOCK,
                }:
                    raise DeploymentError(
                        "routine shared activation lock cannot be acquired"
                    ) from error
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DeploymentError(
                        "routine shared activation lock acquisition timed out"
                    ) from error
                time.sleep(min(0.01, remaining))
                continue
            lock_acquired = True
            if time.monotonic() >= deadline:
                raise DeploymentError(
                    "routine shared activation lock acquisition timed out"
                )
            break
        if "transaction.json" in os.listdir(root.fd):
            raise DeploymentError("routine deployment has an in-flight transaction")
        retained_result_raws, _, _ = _capture_transaction_result_inventory(
            root.fd,
            canonical_root,
        )
        receipt_raw = _read_activation_file(
            root.fd,
            "deployment.json",
            "active deployment receipt alias",
        )
        receipt_sha256 = hashlib.sha256(receipt_raw).hexdigest()
        if receipt_sha256 != expected:
            raise DeploymentError("routine active receipt OCC disagrees")
        lock_binding = {
            "path": str(canonical_root / "activation.lock"),
            "device": lock_after.st_dev,
            "inode": lock_after.st_ino,
            "owner": lock_after.st_uid,
            "mode": stat.S_IMODE(lock_after.st_mode),
        }
        receipt, _ = _validate_retained_receipt_header(
            _parse_canonical_json(receipt_raw, "active deployment receipt"),
            canonical_root,
            lock_binding,
            "active deployment receipt",
            receipt_profile=receipt_profile,
        )
        retained_path = canonical_root / "receipts" / f"sha256-{receipt_sha256}.json"
        retained_raw = _capture_absolute_regular(
            retained_path,
            MAX_JSON_BYTES,
            "retained active deployment receipt",
        )
        if retained_raw != receipt_raw:
            raise DeploymentError("retained active deployment receipt disagrees")
        active = _exact(
            receipt["active"],
            {
                "record_path",
                "record_sha256",
                "generation",
                "runtime_contract",
                "runtime_implementation_sha256",
                "public_release",
            },
            "active deployment record",
        )
        active_path = canonical_root / "active.json"
        if active["record_path"] != str(active_path):
            raise DeploymentError("active deployment record path disagrees")
        active_raw = _read_activation_file(root.fd, "active.json", "active record")
        if hashlib.sha256(active_raw).hexdigest() != active["record_sha256"]:
            raise DeploymentError("active deployment record digest disagrees")
        active_source = _validate_active_runtime_and_trust(
            receipt,
            active_raw,
            canonical_root,
            source_parser=source_parser,
        )
        controls = _exact(
            receipt["control_set"],
            {"shim", "client", "launcher", "controller", "policy"},
            "active deployment control set",
        )
        control_paths = {
            "shim": canonical_root / "task-witness",
            "client": canonical_root / "client" / "task_witness_client.py",
            "launcher": canonical_root / "launcher" / "task_witness_launch.py",
            "controller": canonical_root / "controller" / "task_witness_deploy.py",
            "policy": canonical_root / "controller" / "policy.json",
        }
        normalized_controls: dict[str, Any] = {}
        control_raws: dict[str, bytes] = {}
        for role, target in control_paths.items():
            binding, control_raw = _capture_routine_receipt_file(
                controls[role],
                expected_path=target,
                byte_limit=(
                    MAX_JSON_BYTES
                    if role == "policy"
                    else MAX_CANDIDATE_TREE_FILE_BYTES
                ),
                label=f"active deployment {role}",
            )
            normalized_controls[role] = binding
            control_raws[role] = control_raw
        smoke_manifest_binding, smoke_manifest_raw = _capture_routine_receipt_file(
            receipt["smoke"]["bundle"]["manifest"],
            expected_path=canonical_root / "smoke" / "bundle" / "manifest.json",
            byte_limit=MAX_JSON_BYTES,
            label="active deployment smoke bundle manifest",
        )
        control_raws["smoke-bundle-manifest"] = smoke_manifest_raw
        policy_raw = control_raws["policy"]
        active_policy = policy_parser(policy_raw)
        policy_binding = _exact(
            receipt["compatibility_policy"],
            {"path", "length", "sha256", "owner", "mode", "content_sha256"},
            "active compatibility policy binding",
        )
        declared_receipt_contracts = {
            key: active_policy.control_surface["contracts"][key]
            for key in RECEIPT_CONTRACTS
        }
        if (
            policy_binding["sha256"] != active_policy.raw_sha256
            or policy_binding["content_sha256"] != active_policy.content_sha256
            or policy_binding["path"] != str(control_paths["policy"])
            or {key: policy_binding[key] for key in normalized_controls["policy"]}
            != normalized_controls["policy"]
            or receipt["process_profile"]
            != _thaw(active_policy.control_surface["process_profile"])
            or receipt["contracts"] != declared_receipt_contracts
        ):
            raise DeploymentError("active compatibility policy receipt disagrees")
        retained_chain = _validate_retained_receipt_chain(
            receipt,
            receipt_raw,
            canonical_root,
            active_policy=active_policy,
            receipt_profile=receipt_profile,
        )
        smoke = _thaw(receipt["smoke"])
        _activation_journal_smoke_shape(
            smoke,
            canonical_root,
            os.geteuid(),
            active["record_sha256"],
            "active deployment smoke",
        )
        active_unit = {
            "state": "active",
            "deployment_receipt": {
                "path": str(canonical_root / "deployment.json"),
                "length": len(receipt_raw),
                "sha256": receipt_sha256,
                "owner": os.geteuid(),
                "mode": 0o600,
            },
            "active_record": {
                "path": str(active_path),
                "length": len(active_raw),
                "sha256": hashlib.sha256(active_raw).hexdigest(),
                "owner": os.geteuid(),
                "mode": 0o600,
            },
            "control_set": normalized_controls,
            "smoke": smoke,
        }
        if (
            _identity(os.fstat(root.fd)) != _identity(root_metadata)
            or _identity(canonical_root.lstat()) != _identity(root_metadata)
            or _identity(os.fstat(lock_fd)) != _identity(lock_after)
            or _identity(
                os.stat(
                    "activation.lock",
                    dir_fd=root.fd,
                    follow_symlinks=False,
                )
            )
            != _identity(lock_after)
            or "transaction.json" in os.listdir(root.fd)
            or _capture_transaction_result_inventory(
                root.fd,
                canonical_root,
            )[0]
            != retained_result_raws
            or _read_activation_file(
                root.fd,
                "deployment.json",
                "active deployment receipt alias",
            )
            != receipt_raw
            or _read_activation_file(root.fd, "active.json", "active record")
            != active_raw
            or _capture_absolute_regular(
                retained_path,
                MAX_JSON_BYTES,
                "retained active deployment receipt",
            )
            != receipt_raw
            or any(
                _capture_absolute_regular(
                    Path(
                        (
                            smoke_manifest_binding
                            if role == "smoke-bundle-manifest"
                            else normalized_controls[role]
                        )["path"]
                    ),
                    (
                        MAX_JSON_BYTES
                        if role in {"policy", "smoke-bundle-manifest"}
                        else MAX_CANDIDATE_TREE_FILE_BYTES
                    ),
                    f"active deployment {role} recheck",
                )
                != control_raw
                for role, control_raw in control_raws.items()
            )
        ):
            raise DeploymentError("routine active state changed during capture")
        return ActiveDeploymentPrecondition(
            canonical_root,
            _identity(root_metadata),
            _freeze(lock_binding),
            _identity(lock_after),
            _freeze_json(receipt),
            receipt_raw,
            receipt_sha256,
            active_raw,
            _freeze_json(active_unit),
            active_policy,
            active_source,
            retained_chain,
            _freeze(control_raws),
            _freeze(retained_result_raws),
        )
    except OSError as error:
        raise DeploymentError("routine active state cannot be captured") from error
    finally:
        if lock_fd >= 0:
            if lock_acquired:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(lock_fd)
        os.close(root.fd)


def _receipt_maintenance_authority_surface(
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    smoke_keys = ("bundle", "producer", "validator", "expected_projection")
    return {
        "control-set": _thaw(receipt["control_set"]),
        "smoke-authority": {key: _thaw(receipt["smoke"][key]) for key in smoke_keys},
        "interpreter": _thaw(receipt["interpreter"]),
        "runtime-qualification": _thaw(receipt["runtime_closure"]),
        "process-profile": _thaw(receipt["process_profile"]),
        "platform": _thaw(receipt["platform"]),
        "compatibility-policy": _thaw(receipt["compatibility_policy"]),
        "receipt-parser": _thaw(receipt["contracts"]),
    }


def _maintenance_authority_differences(
    active: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> tuple[str, ...]:
    supported = (
        "control-set",
        "smoke-authority",
        "interpreter",
        "runtime-qualification",
        "process-profile",
        "platform",
        "compatibility-policy",
        "receipt-parser",
    )
    if set(active) != set(supported) or set(candidate) != set(supported):
        raise DeploymentError("control maintenance authority surface is incomplete")
    return tuple(
        name for name in supported if _thaw(active[name]) != _thaw(candidate[name])
    )


def _control_maintenance_differences(
    precondition: ActiveDeploymentPrecondition,
    candidate: DeploymentPlan,
    qualification: RuntimeQualification,
) -> tuple[str, ...]:
    expected_controls = {
        role: _installed_binding(_artifact_for_role(candidate, role))
        for role in CONTROL_SET_ROLES
    }
    candidate_interpreter = {
        "executable": qualification.main_executable["path"],
        "implementation": qualification.main_executable["implementation"],
        "version": _thaw(qualification.main_executable["version"]),
        "executable_sha256": qualification.main_executable["sha256"],
    }
    candidate_runtime_closure = {
        **_thaw(qualification.runtime_closure),
        "dependency_classes": list(qualification.dependency_classes),
        "qualification_content_sha256": qualification.content_sha256,
    }
    policy_artifact = _artifact_for_role(candidate, "policy")
    candidate_policy = {
        **_installed_binding(policy_artifact),
        "content_sha256": candidate.candidate_policy.content_sha256,
    }
    control_surface = candidate.candidate_policy.control_surface
    candidate_contracts = {
        key: control_surface["contracts"][key] for key in RECEIPT_CONTRACTS
    }
    candidate_smoke = _smoke_receipt_projection(candidate)
    candidate_surface = {
        "control-set": expected_controls,
        "smoke-authority": {
            key: _thaw(candidate_smoke[key])
            for key in ("bundle", "producer", "validator", "expected_projection")
        },
        "interpreter": candidate_interpreter,
        "runtime-qualification": candidate_runtime_closure,
        "process-profile": _thaw(control_surface["process_profile"]),
        "platform": _thaw(qualification.platform),
        "compatibility-policy": candidate_policy,
        "receipt-parser": candidate_contracts,
    }
    return _maintenance_authority_differences(
        _receipt_maintenance_authority_surface(precondition.receipt_value),
        candidate_surface,
    )


def _prepare_routine_against_precondition(
    request: DeploymentRequest,
    precondition: ActiveDeploymentPrecondition,
) -> PreparedDeployment:
    _validate_deployment_request(request)
    if precondition.canonical_root != request.canonical_root:
        raise DeploymentError("routine recorded precondition root disagrees")
    selection = _parse_source_selection(request.source_selection_raw)
    evidence = _validate_source_evidence(selection, request.source_evidence)
    snapshot = _snapshot_candidate_tree(request.candidate_root)
    source = _bind_candidate_source_evidence(
        snapshot,
        request.source_selection_raw,
        selection,
        evidence,
    )
    qualification = _parse_runtime_qualification(request.runtime_qualification_raw)
    synthetic = FirstInstallPrecondition(
        precondition.canonical_root,
        precondition.root_identity,
        precondition.activation_lock,
        precondition.activation_lock_identity,
        False,
        _transaction_result_sha256s(precondition.retained_result_raws),
    )
    base = _plan_first_install(
        source,
        qualification,
        synthetic,
        request.maintenance_transaction_sha256,
    )
    classification = _classify_candidate_source(
        active_source=precondition.active_source,
        active_policy=precondition.active_policy,
        active_policy_sha256=precondition.receipt_value["compatibility_policy"][
            "sha256"
        ],
        candidate_source=source,
        candidate_policy_sha256=base.candidate_policy.raw_sha256,
    )
    if classification.outcome == "integrity-rejected":
        _require_deployment_source_outcome(
            classification,
            control_maintenance=False,
            label="deployment",
        )
    maintenance_differences = _control_maintenance_differences(
        precondition,
        base,
        qualification,
    )
    control_maintenance = bool(maintenance_differences)
    _require_deployment_source_outcome(
        classification,
        control_maintenance=control_maintenance,
        label="deployment",
    )
    if control_maintenance and set(precondition.control_raws) != set(
        CONTROL_PREIMAGE_ROLES
    ):
        raise DeploymentError("control maintenance prior preimage is incomplete")
    operation = (
        "complete-control-set-maintenance" if control_maintenance else "routine-payload"
    )
    unsigned = {
        **{
            key: _thaw(value)
            for key, value in base.value.items()
            if key != "plan_sha256"
        },
        "operation": operation,
        "expected_active_receipt_sha256": precondition.receipt_sha256,
        "classification": {
            "outcome": classification.outcome,
            "reason": classification.reason,
        },
    }
    if control_maintenance:
        unsigned["maintenance_differences"] = list(maintenance_differences)
    plan_sha256 = _digest(unsigned)
    plan_type = (
        ControlSetDeploymentPlan if control_maintenance else RoutineDeploymentPlan
    )
    plan = plan_type(
        source,
        qualification,
        precondition,
        classification,
        base.candidate_policy,
        base.active,
        base.trust,
        base.artifacts,
        base.maintenance_transaction_sha256,
        plan_sha256,
        precondition.receipt_sha256,
        _freeze({**unsigned, "plan_sha256": plan_sha256}),
    )
    controller = _artifact_for_role(plan, "controller")
    policy = _artifact_for_role(plan, "policy")
    return PreparedDeployment(
        plan,
        DeploymentAuthorizationFacts(
            canonical_root=precondition.canonical_root,
            effective_uid=os.geteuid(),
            plan_sha256=plan.plan_sha256,
            maintenance_transaction_sha256=plan.maintenance_transaction_sha256,
            candidate_controller_sha256=controller.sha256,
            candidate_policy_sha256=policy.sha256,
            source_selection_sha256=source.source_selection_sha256,
            source_evidence_sha256=source.source_evidence_sha256,
            expected_active_receipt_sha256=precondition.receipt_sha256,
        ),
    )


def _bridge_identity_object(value: object, label: str) -> dict[str, Any]:
    identity = _exact(
        value,
        {
            "repository_id",
            "commit_sha1",
            "tree_sha1",
            "plugin_subtree_sha256",
            "controller_sha256",
            "policy_sha256",
            "client_sha256",
            "source_mode",
        },
        label,
    )
    if (
        _repository_id(identity["repository_id"], f"{label}.repository_id")
        != "nisavid/agents"
    ):
        raise DeploymentError(f"{label}.repository_id is unsupported")
    _git_revision(identity["commit_sha1"], f"{label}.commit_sha1")
    _git_revision(identity["tree_sha1"], f"{label}.tree_sha1")
    for name in (
        "plugin_subtree_sha256",
        "controller_sha256",
        "policy_sha256",
        "client_sha256",
    ):
        _sha256(identity[name], f"{label}.{name}")
    if identity["source_mode"] != "harness_snapshot":
        raise DeploymentError(f"{label}.source_mode is unsupported")
    return identity


def _capture_bridge_release_manifest(
    request: BridgeTransitionRequest,
    staging_root: Path,
) -> tuple[dict[str, Any], bytes, tuple[int, ...]]:
    deployment = request.deployment
    manifest_path = _normalized_absolute_path(
        request.release_manifest_path,
        "bridge transition release manifest",
    )
    roots = (
        deployment.canonical_root,
        deployment.candidate_root,
        staging_root,
    )
    if any(
        manifest_path.is_relative_to(root) or root.is_relative_to(manifest_path)
        for root in roots
    ):
        raise DeploymentError(
            "bridge transition release manifest must be disjoint from deployment roots"
        )
    try:
        before = manifest_path.lstat()
    except OSError as error:
        raise DeploymentError(
            "bridge transition release manifest is unavailable"
        ) from error
    raw = _capture_absolute_regular(
        manifest_path,
        MAX_JSON_BYTES,
        "bridge transition release manifest",
    )
    try:
        after = manifest_path.lstat()
    except OSError as error:
        raise DeploymentError(
            "bridge transition release manifest became unavailable"
        ) from error
    if (
        _identity(before) != _identity(after)
        or not stat.S_ISREG(after.st_mode)
        or after.st_uid != os.geteuid()
        or after.st_nlink != 1
        or stat.S_IMODE(after.st_mode) != 0o600
        or after.st_size != len(raw)
    ):
        raise DeploymentError("bridge transition release manifest binding disagrees")
    label = "bridge transition release manifest"
    manifest = _exact(
        _parse_bridge_canonical_json(raw, label),
        {
            "schema_version",
            "contract",
            "qualification_candidate",
            "targets",
            "bridge_history",
            "canonical_review_evidence_sha256",
            "final_public_release",
            "migration_edge",
            "promotion_delta_sha256",
            "disposition",
            "content_sha256",
        },
        label,
    )
    _content_sha256(manifest, label)
    if (
        type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != 1
        or manifest["contract"] != BRIDGE_RELEASE_MANIFEST_CONTRACT
        or manifest["disposition"] != "release-qualified"
    ):
        raise DeploymentError("bridge transition release manifest contract mismatch")
    qualification = _exact(
        manifest["qualification_candidate"],
        {
            "repository_id",
            "commit_sha1",
            "tree_sha1",
            "plugin_subtree_sha256",
            "suite_inventory_sha256",
        },
        f"{label}.qualification_candidate",
    )
    _repository_id(
        qualification["repository_id"],
        f"{label}.qualification_candidate.repository_id",
    )
    for name in ("commit_sha1", "tree_sha1"):
        _git_revision(
            qualification[name],
            f"{label}.qualification_candidate.{name}",
        )
    for name in ("plugin_subtree_sha256", "suite_inventory_sha256"):
        _sha256(
            qualification[name],
            f"{label}.qualification_candidate.{name}",
        )
    targets = _exact(
        manifest["targets"],
        {"linux-x86_64", "macos-arm64"},
        f"{label}.targets",
    )
    for target, digest in targets.items():
        _sha256(digest, f"{label}.targets.{target}")
    history = _exact(
        manifest["bridge_history"],
        {
            "bridge_identity_sha256",
            "bridge_provenance_sha256",
            "freeze5",
            "bridge",
        },
        f"{label}.bridge_history",
    )
    _sha256(
        history["bridge_identity_sha256"],
        f"{label}.bridge_history.bridge_identity_sha256",
    )
    _sha256(
        history["bridge_provenance_sha256"],
        f"{label}.bridge_history.bridge_provenance_sha256",
    )
    _bridge_identity_object(history["freeze5"], f"{label}.bridge_history.freeze5")
    _bridge_identity_object(history["bridge"], f"{label}.bridge_history.bridge")
    _sha256(
        manifest["canonical_review_evidence_sha256"],
        f"{label}.canonical_review_evidence_sha256",
    )
    final = _exact(
        manifest["final_public_release"],
        {"commit_sha1", "tree_sha1"},
        f"{label}.final_public_release",
    )
    for name in ("commit_sha1", "tree_sha1"):
        _git_revision(final[name], f"{label}.final_public_release.{name}")
    edge = _exact(
        manifest["migration_edge"],
        {"from", "source_mode", "to", "successor"},
        f"{label}.migration_edge",
    )
    if edge != {
        "from": "freeze5",
        "source_mode": "harness_snapshot",
        "to": "bridge",
        "successor": "tw4",
    }:
        raise DeploymentError("bridge transition release manifest edge disagrees")
    _sha256(
        manifest["promotion_delta_sha256"],
        f"{label}.promotion_delta_sha256",
    )
    return manifest, raw, _identity(after)


def _capture_bridge_transition_authorization(
    path: Path,
    request: BridgeTransitionRequest,
    staging_root: Path,
) -> tuple[bytes, tuple[int, ...]]:
    if not isinstance(path, Path):
        raise DeploymentError(
            "bridge transition authorization path must be a Path value"
        )
    authorization_path = _normalized_absolute_path(
        path,
        "bridge transition authorization",
    )
    roots = (
        request.deployment.canonical_root,
        request.deployment.candidate_root,
        staging_root,
    )
    if authorization_path == request.release_manifest_path or any(
        authorization_path.is_relative_to(root)
        or root.is_relative_to(authorization_path)
        for root in roots
    ):
        raise DeploymentError(
            "bridge transition authorization must be disjoint from deployment roots"
        )
    try:
        before = authorization_path.lstat()
    except OSError as error:
        raise DeploymentError(
            "bridge transition authorization is unavailable"
        ) from error
    raw = _capture_absolute_regular(
        authorization_path,
        MAX_JSON_BYTES,
        "bridge transition authorization",
    )
    try:
        after = authorization_path.lstat()
    except OSError as error:
        raise DeploymentError(
            "bridge transition authorization became unavailable"
        ) from error
    if (
        _identity(before) != _identity(after)
        or not stat.S_ISREG(after.st_mode)
        or after.st_uid != os.geteuid()
        or after.st_nlink != 1
        or stat.S_IMODE(after.st_mode) != 0o600
        or after.st_size != len(raw)
    ):
        raise DeploymentError("bridge transition authorization binding disagrees")
    return raw, _identity(after)


def _bridge_prior_rehearsal(value: object, label: str) -> dict[str, Any]:
    rehearsal = _exact(
        value,
        {
            "endpoint_projection_sha256",
            "transaction_sha256",
            "terminal_result_sha256",
            "active_receipt_sha256",
        },
        label,
    )
    for name in rehearsal:
        _sha256(rehearsal[name], f"{label}.{name}")
    return rehearsal


def _parse_bridge_transition_authorization_document(
    raw: bytes,
) -> dict[str, Any]:
    label = "bridge transition authorization"
    if type(raw) is not bytes:
        raise DeploymentError(f"{label} must be exact bytes")
    parsed = _parse_bridge_canonical_json(raw, label)
    execution_class = parsed.get("execution_class")
    if execution_class not in {"isolated-rehearsal", "live-migration"}:
        raise DeploymentError(f"{label} execution class is unsupported")
    common = {
        "schema_version",
        "contract",
        "purpose",
        "execution_class",
        "canonical_root",
        "staging_root",
        "effective_uid",
        "plan_sha256",
        "maintenance_transaction_sha256",
        "deployment_authorization_sha256",
        "expected_active_receipt_core_sha256",
        "bridge_identity_sha256",
        "release_manifest_sha256",
        "endpoint_projection_sha256",
        "content_sha256",
    }
    keys = common | (
        {"prior_rehearsal"} if execution_class == "live-migration" else set()
    )
    value = _exact(parsed, keys, label)
    _content_sha256(value, label)
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["contract"] != BRIDGE_TRANSITION_AUTHORIZATION_CONTRACT
        or value["purpose"] != "bridge-transition"
    ):
        raise DeploymentError(f"{label} contract mismatch")
    _normalized_absolute_path(
        Path(_text(value["canonical_root"], f"{label}.canonical_root")),
        f"{label}.canonical_root",
    )
    _normalized_absolute_path(
        Path(_text(value["staging_root"], f"{label}.staging_root")),
        f"{label}.staging_root",
    )
    _nonnegative_integer(
        value["effective_uid"],
        f"{label}.effective_uid",
    )
    names = (
        "plan_sha256",
        "maintenance_transaction_sha256",
        "deployment_authorization_sha256",
        "expected_active_receipt_core_sha256",
        "bridge_identity_sha256",
        "release_manifest_sha256",
        "endpoint_projection_sha256",
    )
    for name in names:
        _sha256(value[name], f"{label}.{name}")
    if execution_class == "live-migration":
        _bridge_prior_rehearsal(
            value["prior_rehearsal"],
            f"{label}.prior_rehearsal",
        )
    return value


def _validate_bridge_transition_authorization(
    raw: bytes,
    facts: BridgeTransitionAuthorizationFacts,
    deployment_authorization_raw: bytes,
) -> dict[str, Any]:
    label = "bridge transition authorization"
    value = _parse_bridge_transition_authorization_document(raw)
    names = (
        "plan_sha256",
        "maintenance_transaction_sha256",
        "deployment_authorization_sha256",
        "expected_active_receipt_core_sha256",
        "bridge_identity_sha256",
        "release_manifest_sha256",
        "endpoint_projection_sha256",
    )
    digests = {name: value[name] for name in names}
    expected = {
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
    if (
        value["execution_class"] != facts.execution_class
        or Path(value["canonical_root"]) != facts.canonical_root
        or Path(value["staging_root"]) != facts.staging_root
        or value["effective_uid"] != facts.effective_uid
        or digests != expected
        or digests["deployment_authorization_sha256"]
        != hashlib.sha256(deployment_authorization_raw).hexdigest()
    ):
        raise DeploymentError(f"{label} facts disagree")
    return value


def _bridge_receipt_identity(
    receipt: Mapping[str, Any],
    *,
    commit_sha1: str | None = None,
) -> dict[str, Any]:
    source = receipt["source"]
    controls = receipt["control_set"]
    return {
        "repository_id": source["repository_id"],
        "commit_sha1": source["revision"] if commit_sha1 is None else commit_sha1,
        "plugin_subtree_sha256": source["subtree_sha256"],
        "controller_sha256": controls["controller"]["sha256"],
        "policy_sha256": controls["policy"]["sha256"],
        "client_sha256": controls["client"]["sha256"],
        "source_mode": source["mode"],
    }


def _require_b1_client_source(raw: bytes, label: str) -> None:
    generation_prefix = b"CLIENT_SOURCE_" + b'GENERATION_SHA256 = "'
    generation_start = raw.find(generation_prefix)
    if (
        generation_start < 0
        or (generation_start > 0 and raw[generation_start - 1] != 0x0A)
        or raw.find(generation_prefix, generation_start + 1) >= 0
    ):
        raise DeploymentError(f"{label} profile disagrees")
    digest_start = generation_start + len(generation_prefix)
    digest_end = digest_start + 64
    if (
        raw[digest_end : digest_end + 2] != b'"\n'
        or SHA256.fullmatch(raw[digest_start:digest_end].decode("ascii", "ignore"))
        is None
    ):
        raise DeploymentError(f"{label} profile disagrees")
    normalized = raw[:digest_start] + (b"0" * 64) + raw[digest_end:]
    if (
        raw[digest_start:digest_end].decode("ascii")
        != hashlib.sha256(normalized).hexdigest()
    ):
        raise DeploymentError(f"{label} profile disagrees")

    profile_prefix = b"CLIENT_RELEASE_" + b'PROFILE = "'
    profile_start = raw.find(profile_prefix)
    if (
        profile_start < 0
        or (profile_start > 0 and raw[profile_start - 1] != 0x0A)
        or raw.find(profile_prefix, profile_start + 1) >= 0
    ):
        raise DeploymentError(f"{label} profile disagrees")
    profile_start += len(profile_prefix)
    profile_end = raw.find(b'"\n', profile_start)
    if profile_end < 0 or raw[profile_start:profile_end] != b"b1-transition":
        raise DeploymentError(f"{label} profile disagrees")


def _validate_exact_bridge_predecessor(
    precondition: ActiveDeploymentPrecondition,
    *,
    staged_predecessor: bool = False,
) -> None:
    receipt = precondition.receipt_value
    chain = precondition.retained_chain
    if len(chain.deployment_receipts) != 2 or len(chain.authority_edges) != 1:
        raise DeploymentError("bridge transition retained chain is not exact B1")
    by_sequence = {
        value["sequence"]: value for _, value, _ in chain.deployment_receipts
    }
    if set(by_sequence) != {1, 2} or by_sequence[2] != receipt:
        raise DeploymentError("bridge transition retained sequence is not exact B1")
    freeze5 = by_sequence[1]
    edge = chain.authority_edges[0]
    controls = receipt["control_set"]
    current_controller = (
        precondition.canonical_root / "controller" / "task_witness_deploy.py"
    )
    controller_raw = (
        bytes(precondition.control_raws["controller"])
        if staged_predecessor
        else _capture_absolute_regular(
            _normalized_absolute_path(
                Path(__file__),
                "bridge installed controller",
            ),
            MAX_CANDIDATE_TREE_FILE_BYTES,
            "bridge installed controller",
        )
    )
    controller = _receipt_file_binding(
        controls["controller"],
        "bridge installed controller binding",
    )
    current_client = precondition.canonical_root / "client" / "task_witness_client.py"
    client_raw = (
        bytes(precondition.control_raws["client"])
        if staged_predecessor
        else _capture_absolute_regular(
            _normalized_absolute_path(
                current_client,
                "bridge installed client",
            ),
            MAX_CANDIDATE_TREE_FILE_BYTES,
            "bridge installed client",
        )
    )
    client = _receipt_file_binding(
        controls["client"],
        "bridge installed client binding",
    )
    if (
        controller["path"] != str(current_controller)
        or controller["sha256"] != hashlib.sha256(controller_raw).hexdigest()
        or controller["length"] != len(controller_raw)
        or controller["sha256"] == FREEZE5_CONTROLLER_SHA256
        or controls["policy"]["sha256"] != FREEZE5_POLICY_SHA256
        or client["path"] != str(current_client)
        or client["sha256"] != hashlib.sha256(client_raw).hexdigest()
        or client["length"] != len(client_raw)
        or client["sha256"] == FREEZE5_CLIENT_SHA256
        or precondition.active_policy.raw_sha256 != FREEZE5_POLICY_SHA256
        or edge.successor_receipt_sha256 != precondition.receipt_sha256
        or edge.prior_receipt_sha256
        != hashlib.sha256(chain.deployment_receipts[1][2]).hexdigest()
        or edge.authorization_purpose != "complete-control-set-maintenance"
        or len(precondition.retained_result_raws) != 2
    ):
        raise DeploymentError("bridge transition predecessor is not exact B1")
    _require_b1_client_source(
        client_raw,
        "bridge transition predecessor client",
    )
    freeze5_controls = freeze5["control_set"]
    if (
        freeze5_controls["controller"]["sha256"] != FREEZE5_CONTROLLER_SHA256
        or freeze5_controls["policy"]["sha256"] != FREEZE5_POLICY_SHA256
        or freeze5_controls["client"]["sha256"] != FREEZE5_CLIENT_SHA256
        or hashlib.sha256(edge.control_raws["controller"]).hexdigest()
        != FREEZE5_CONTROLLER_SHA256
        or hashlib.sha256(edge.control_raws["policy"]).hexdigest()
        != FREEZE5_POLICY_SHA256
        or hashlib.sha256(edge.control_raws["client"]).hexdigest()
        != FREEZE5_CLIENT_SHA256
    ):
        raise DeploymentError("bridge transition F5 ancestry disagrees")


def _validate_bridge_manifest_history(
    precondition: ActiveDeploymentPrecondition,
    manifest: Mapping[str, Any],
) -> None:
    receipt = precondition.receipt_value
    by_sequence = {
        value["sequence"]: value
        for _, value, _ in precondition.retained_chain.deployment_receipts
    }
    try:
        freeze5 = by_sequence[1]
    except KeyError:
        raise DeploymentError(
            "bridge transition manifest predecessor history is incomplete"
        ) from None
    history = manifest["bridge_history"]
    identity_unsigned = {
        "schema_version": 1,
        "contract": BRIDGE_IDENTITY_CONTRACT,
        "freeze5": history["freeze5"],
        "bridge": history["bridge"],
        "allowed_edges": [
            {
                "from": "freeze5",
                "source_mode": "harness_snapshot",
                "to": "bridge",
            }
        ],
        "provenance_sha256": history["bridge_provenance_sha256"],
    }
    identity_raw = _canonical_bytes(
        {
            **identity_unsigned,
            "content_sha256": _digest(identity_unsigned),
        }
    )
    if hashlib.sha256(identity_raw).hexdigest() != history["bridge_identity_sha256"]:
        raise DeploymentError("bridge transition manifest identity record disagrees")
    expected_freeze5 = _bridge_receipt_identity(
        freeze5,
        commit_sha1=FREEZE5_COMMIT_SHA1,
    )
    expected_bridge = _bridge_receipt_identity(receipt)
    observed_freeze5 = {
        key: value for key, value in history["freeze5"].items() if key != "tree_sha1"
    }
    observed_bridge = {
        key: value for key, value in history["bridge"].items() if key != "tree_sha1"
    }
    if observed_freeze5 != expected_freeze5 or observed_bridge != expected_bridge:
        raise DeploymentError("bridge transition manifest history disagrees")


def _validate_bridge_endpoint_projection(
    raw: bytes,
    request: BridgeTransitionRequest,
    precondition: ActiveDeploymentPrecondition,
) -> dict[str, Any]:
    label = "bridge transition endpoint projection"
    endpoint = _exact(
        _parse_bridge_canonical_json(raw, label),
        {
            "schema_version",
            "contract",
            "execution_class",
            "target",
            "deployment_root",
            "device",
            "inode",
            "owner",
            "mode",
            "starting_active_receipt_sha256",
            "retained_receipts",
            "retained_results",
            "platform_profile_sha256",
            "runtime_closure_sha256",
            "content_sha256",
        },
        label,
    )
    _content_sha256(endpoint, label)
    receipt = precondition.receipt_value
    platform = receipt["platform"]
    try:
        target = {
            ("darwin", "arm64"): "macos-arm64",
            ("linux", "x86_64"): "linux-x86_64",
        }[(platform["system"], platform["machine"])]
    except KeyError as error:
        raise DeploymentError(
            "bridge transition endpoint target is unsupported"
        ) from error
    root_identity = precondition.root_identity
    if (
        type(endpoint["schema_version"]) is not int
        or endpoint["schema_version"] != 1
        or endpoint["contract"] != BRIDGE_ENDPOINT_PROJECTION_CONTRACT
        or endpoint["execution_class"] != request.execution_class
        or endpoint["target"] != target
        or endpoint["deployment_root"] != str(precondition.canonical_root)
        or endpoint["device"] != root_identity[0]
        or endpoint["inode"] != root_identity[1]
        or endpoint["owner"] != root_identity[3]
        or endpoint["mode"] != stat.S_IMODE(root_identity[2])
        or endpoint["starting_active_receipt_sha256"] != precondition.receipt_sha256
        or endpoint["platform_profile_sha256"] != _digest(platform)
        or endpoint["runtime_closure_sha256"] != _digest(receipt["runtime_closure"])
    ):
        raise DeploymentError("bridge transition endpoint authority disagrees")
    receipts = tuple(
        {
            "sequence": value["sequence"],
            "sha256": digest,
        }
        for digest, value, _ in sorted(
            precondition.retained_chain.deployment_receipts,
            key=lambda item: item[1]["sequence"],
        )
    )
    results = tuple(
        {"path": path, "sha256": hashlib.sha256(result_raw).hexdigest()}
        for path, result_raw in sorted(precondition.retained_result_raws.items())
    )
    if endpoint["retained_receipts"] != list(receipts) or endpoint[
        "retained_results"
    ] != list(results):
        raise DeploymentError("bridge transition endpoint history disagrees")
    return endpoint


def _expected_deployment_authorization_raw(
    prepared: PreparedDeployment,
) -> tuple[bytes, str]:
    plan = prepared.plan
    purpose = _active_prior_authorization_purpose(
        plan.classification,
        control_maintenance=type(plan) is ControlSetDeploymentPlan,
    )
    facts = prepared.authorization_facts
    unsigned = {
        "schema_version": 1,
        "contract": DEPLOYER_AUTHORIZATION_CONTRACT,
        "purpose": purpose,
        "canonical_root": str(facts.canonical_root),
        "effective_uid": facts.effective_uid,
        "plan_sha256": facts.plan_sha256,
        "maintenance_transaction_sha256": facts.maintenance_transaction_sha256,
        "candidate_controller_sha256": facts.candidate_controller_sha256,
        "candidate_policy_sha256": facts.candidate_policy_sha256,
        "source_selection_sha256": facts.source_selection_sha256,
        "source_evidence_sha256": facts.source_evidence_sha256,
        "expected_active_receipt_sha256": facts.expected_active_receipt_sha256,
    }
    value = {**unsigned, "content_sha256": _digest(unsigned)}
    return _canonical_document(value), purpose


def _prepare_bridge_candidate_against_precondition(
    request: DeploymentRequest,
    precondition: ActiveDeploymentPrecondition,
) -> PreparedDeployment:
    """Plan a current candidate after the exact legacy epoch has been captured."""

    _validate_deployment_request(request)
    if precondition.canonical_root != request.canonical_root:
        raise DeploymentError("bridge recorded precondition root disagrees")
    selection = _parse_source_selection(request.source_selection_raw)
    evidence = _validate_source_evidence(selection, request.source_evidence)
    snapshot = _snapshot_candidate_tree(request.candidate_root)
    source = _bind_candidate_source_evidence(
        snapshot,
        request.source_selection_raw,
        selection,
        evidence,
    )
    qualification = _parse_runtime_qualification(request.runtime_qualification_raw)
    synthetic = FirstInstallPrecondition(
        precondition.canonical_root,
        precondition.root_identity,
        precondition.activation_lock,
        precondition.activation_lock_identity,
        False,
        _transaction_result_sha256s(precondition.retained_result_raws),
    )
    base = _plan_first_install(
        source,
        qualification,
        synthetic,
        request.maintenance_transaction_sha256,
    )
    classification = _classify_candidate_source(
        active_source=precondition.active_source,
        active_policy=precondition.active_policy,
        active_policy_sha256=precondition.receipt_value["compatibility_policy"][
            "sha256"
        ],
        candidate_source=source,
        candidate_policy_sha256=base.candidate_policy.raw_sha256,
    )
    maintenance_differences = _control_maintenance_differences(
        precondition,
        base,
        qualification,
    )
    if not maintenance_differences:
        raise DeploymentError("bridge transition requires a complete control change")
    _require_deployment_source_outcome(
        classification,
        control_maintenance=True,
        label="bridge transition",
    )
    if set(precondition.control_raws) != set(CONTROL_PREIMAGE_ROLES):
        raise DeploymentError("bridge transition prior preimage is incomplete")
    unsigned = {
        **{
            key: _thaw(value)
            for key, value in base.value.items()
            if key != "plan_sha256"
        },
        "operation": "complete-control-set-maintenance",
        "expected_active_receipt_sha256": precondition.receipt_sha256,
        "classification": {
            "outcome": classification.outcome,
            "reason": classification.reason,
        },
        "maintenance_differences": list(maintenance_differences),
    }
    plan_sha256 = _digest(unsigned)
    plan = ControlSetDeploymentPlan(
        source,
        qualification,
        precondition,
        classification,
        base.candidate_policy,
        base.active,
        base.trust,
        base.artifacts,
        base.maintenance_transaction_sha256,
        plan_sha256,
        precondition.receipt_sha256,
        _freeze({**unsigned, "plan_sha256": plan_sha256}),
    )
    controller = _artifact_for_role(plan, "controller")
    policy = _artifact_for_role(plan, "policy")
    return PreparedDeployment(
        plan,
        DeploymentAuthorizationFacts(
            canonical_root=precondition.canonical_root,
            effective_uid=os.geteuid(),
            plan_sha256=plan.plan_sha256,
            maintenance_transaction_sha256=plan.maintenance_transaction_sha256,
            candidate_controller_sha256=controller.sha256,
            candidate_policy_sha256=policy.sha256,
            source_selection_sha256=source.source_selection_sha256,
            source_evidence_sha256=source.source_evidence_sha256,
            expected_active_receipt_sha256=precondition.receipt_sha256,
        ),
    )


def _expected_bridge_active_receipt_core_sha256(
    prepared: PreparedDeployment,
    authorization_raw: bytes,
    authorization_purpose: str,
    staging_root: Path,
) -> str:
    authorization = _validate_deployment_authorization(
        authorization_raw,
        prepared.authorization_facts,
        expected_purpose=authorization_purpose,
    )
    rollback_value, rollback_raw = _routine_rollback_receipt(
        prepared.plan,
        staging_root,
    )
    rollback_sha256 = hashlib.sha256(rollback_raw).hexdigest()
    rollback_path = (
        prepared.plan.precondition.canonical_root
        / "receipts"
        / f"sha256-{rollback_sha256}.json"
    )
    deployment_value, _ = _routine_deployment_receipt(
        prepared.plan,
        authorization,
        authorization_raw,
        rollback_path,
        rollback_raw,
        authorization_purpose=authorization_purpose,
    )
    if "migration" in deployment_value or rollback_value["canonical_root"] != str(
        prepared.plan.precondition.canonical_root
    ):
        raise DeploymentError("bridge transition expected receipt core disagrees")
    return _digest(
        {
            key: value
            for key, value in deployment_value.items()
            if key != "content_sha256"
        }
    )


def _prepare_bridge_against_precondition(
    request: BridgeTransitionRequest,
    staging_root: Path,
    precondition: ActiveDeploymentPrecondition,
    manifest: Mapping[str, Any],
    manifest_raw: bytes,
) -> PreparedBridgeTransition:
    _validate_bridge_manifest_history(precondition, manifest)
    _validate_bridge_endpoint_projection(
        request.endpoint_projection_raw,
        request,
        precondition,
    )
    prepared = _prepare_bridge_candidate_against_precondition(
        request.deployment,
        precondition,
    )
    source = prepared.plan.source
    qualification = manifest["qualification_candidate"]
    final = manifest["final_public_release"]
    if (
        qualification["repository_id"] != source.repository_id
        or qualification["plugin_subtree_sha256"] != source.subtree_sha256
        or final["commit_sha1"] != source.revision
    ):
        raise DeploymentError("bridge transition manifest target disagrees")
    authorization_raw, authorization_purpose = _expected_deployment_authorization_raw(
        prepared
    )
    facts = prepared.authorization_facts
    transition = BridgeTransitionAuthorizationFacts(
        canonical_root=precondition.canonical_root,
        staging_root=staging_root,
        effective_uid=os.geteuid(),
        plan_sha256=prepared.plan.plan_sha256,
        maintenance_transaction_sha256=prepared.plan.maintenance_transaction_sha256,
        expected_deployment_authorization_sha256=hashlib.sha256(
            authorization_raw
        ).hexdigest(),
        expected_active_receipt_core_sha256=(
            _expected_bridge_active_receipt_core_sha256(
                prepared,
                authorization_raw,
                authorization_purpose,
                staging_root,
            )
        ),
        bridge_identity_sha256=manifest["bridge_history"]["bridge_identity_sha256"],
        release_manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
        endpoint_projection_sha256=hashlib.sha256(
            request.endpoint_projection_raw
        ).hexdigest(),
        execution_class=request.execution_class,
    )
    if (
        facts.canonical_root != transition.canonical_root
        or facts.effective_uid != transition.effective_uid
        or facts.plan_sha256 != transition.plan_sha256
        or facts.maintenance_transaction_sha256
        != transition.maintenance_transaction_sha256
    ):
        raise DeploymentError("bridge transition authorization facts disagree")
    return PreparedBridgeTransition(
        prepared.plan,
        prepared.authorization_facts,
        transition,
    )


def _validate_rollback_to_request(request: RollbackToRequest) -> None:
    if type(request) is not RollbackToRequest:
        raise DeploymentError("manual rollback request type mismatch")
    if not isinstance(request.canonical_root, Path):
        raise DeploymentError("manual rollback canonical root must be a Path")
    expected = _sha256(
        request.expected_active_receipt_sha256,
        "manual rollback expected active receipt digest",
    )
    target = _sha256(
        request.target_receipt_sha256,
        "manual rollback target receipt digest",
    )
    _sha256(
        request.maintenance_transaction_sha256,
        "manual rollback maintenance transaction digest",
    )
    if target == expected:
        raise DeploymentError("manual rollback target must precede the active receipt")


def _rollback_endpoint_identity(
    receipt_sha256: str,
    receipt: Mapping[str, Any],
) -> RollbackEndpointIdentity:
    source = receipt["source"]
    active = receipt["active"]
    return RollbackEndpointIdentity(
        receipt_sha256=receipt_sha256,
        sequence=receipt["sequence"],
        source=_freeze(
            {
                **{
                    key: _thaw(source[key])
                    for key in (
                        "mode",
                        "plugin_id",
                        "publisher_id",
                        "repository_id",
                        "repository_url",
                        "release_version",
                        "revision",
                        "subtree_sha256",
                        "source_authority",
                        "details",
                    )
                },
                "source_evidence_sha256": source["source_evidence"][
                    "source_evidence_sha256"
                ],
            }
        ),
        active=_freeze(
            {
                key: _thaw(active[key])
                for key in (
                    "generation",
                    "runtime_implementation_sha256",
                    "public_release",
                )
            }
        ),
        control_set=_freeze(
            {
                role: binding["sha256"]
                for role, binding in sorted(receipt["control_set"].items())
            }
        ),
        compatibility_policy_sha256=receipt["compatibility_policy"]["sha256"],
        trust_context_sha256=receipt["trust_context"]["sha256"],
        content_sha256=receipt["content_sha256"],
    )


def _rollback_endpoint_value(endpoint: RollbackEndpointIdentity) -> dict[str, Any]:
    return {
        "receipt_sha256": endpoint.receipt_sha256,
        "sequence": endpoint.sequence,
        "source": _thaw(endpoint.source),
        "active": _thaw(endpoint.active),
        "control_set": _thaw(endpoint.control_set),
        "compatibility_policy_sha256": endpoint.compatibility_policy_sha256,
        "trust_context_sha256": endpoint.trust_context_sha256,
        "content_sha256": endpoint.content_sha256,
    }


def _manual_rollback_target_control_bindings(
    receipt: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    controls = {
        role: _receipt_file_binding(
            receipt["control_set"][role],
            f"manual rollback target {role}",
        )
        for role in CONTROL_SET_ROLES
    }
    controls["smoke-bundle-manifest"] = _receipt_file_binding(
        receipt["smoke"]["bundle"]["manifest"],
        "manual rollback target smoke-bundle manifest",
    )
    return controls


def _resolve_manual_rollback_target_authority(
    precondition: ActiveDeploymentPrecondition,
    target_receipt_sha256: str,
) -> ManualRollbackTargetAuthority:
    matches = tuple(
        edge
        for edge in precondition.retained_chain.authority_edges
        if edge.prior_receipt_sha256 == target_receipt_sha256
    )
    if len(matches) != 1:
        raise DeploymentError(
            "manual rollback target lacks unique retained successor authority"
        )
    selected = matches[0]
    resolved_controls = dict(precondition.control_raws)
    reached = False
    for edge in precondition.retained_chain.authority_edges:
        if edge.control_raws:
            if set(edge.control_raws) != set(CONTROL_PREIMAGE_ROLES):
                raise DeploymentError(
                    "manual rollback historical control authority is incomplete"
                )
            resolved_controls = dict(edge.control_raws)
        if edge.prior_receipt_sha256 == target_receipt_sha256:
            if edge is not selected:
                raise DeploymentError(
                    "manual rollback target successor authority is ambiguous"
                )
            reached = True
            break
    if not reached:
        raise DeploymentError("manual rollback target is outside retained ancestry")
    target_receipt = selected.prior_receipt_value
    if (
        hashlib.sha256(selected.prior_receipt_raw).hexdigest() != target_receipt_sha256
        or selected.selector_raws["deployment-alias"] != selected.prior_receipt_raw
        or hashlib.sha256(selected.prior_active_raw).hexdigest()
        != target_receipt["active"]["record_sha256"]
        or selected.selector_raws["active-record"] != selected.prior_active_raw
    ):
        raise DeploymentError("manual rollback target selector authority disagrees")
    bindings = _manual_rollback_target_control_bindings(target_receipt)
    if set(resolved_controls) != set(CONTROL_PREIMAGE_ROLES):
        raise DeploymentError("manual rollback target control authority is incomplete")
    for role, binding in bindings.items():
        raw = resolved_controls[role]
        if (
            len(raw) != binding["length"]
            or hashlib.sha256(raw).hexdigest() != binding["sha256"]
            or binding["owner"] != os.geteuid()
        ):
            raise DeploymentError(f"manual rollback target {role} authority disagrees")
    control_replacement = any(
        resolved_controls[role] != precondition.control_raws[role]
        for role in CONTROL_PREIMAGE_ROLES
    )
    authority_value = {
        "receipt_sha256": target_receipt_sha256,
        "active_sha256": hashlib.sha256(selected.prior_active_raw).hexdigest(),
        "successor_receipt_sha256": selected.successor_receipt_sha256,
        "successor_rollback_sha256": selected.rollback_receipt_sha256,
        "selector_sha256s": {
            role: hashlib.sha256(raw).hexdigest()
            for role, raw in sorted(selected.selector_raws.items())
        },
        "control_sha256s": {
            role: hashlib.sha256(raw).hexdigest()
            for role, raw in sorted(resolved_controls.items())
        },
        "control_replacement": control_replacement,
    }
    return ManualRollbackTargetAuthority(
        receipt_sha256=target_receipt_sha256,
        receipt_value=target_receipt,
        receipt_raw=selected.prior_receipt_raw,
        active_raw=selected.prior_active_raw,
        activation_unit=selected.prior_activation_unit,
        successor_receipt_sha256=selected.successor_receipt_sha256,
        successor_rollback_sha256=selected.rollback_receipt_sha256,
        successor_rollback_raw=selected.rollback_receipt_raw,
        selector_raws=selected.selector_raws,
        control_raws=_freeze(resolved_controls),
        control_replacement=control_replacement,
        authority_sha256=_digest(authority_value),
    )


def _prepare_rollback_to_against_precondition(
    request: RollbackToRequest,
    precondition: ActiveDeploymentPrecondition,
) -> PreparedRollbackTo:
    _validate_rollback_to_request(request)
    if (
        precondition.canonical_root != request.canonical_root
        or precondition.receipt_sha256 != request.expected_active_receipt_sha256
    ):
        raise DeploymentError("manual rollback recorded precondition disagrees")
    target_authority = _resolve_manual_rollback_target_authority(
        precondition,
        request.target_receipt_sha256,
    )
    target_sha256 = target_authority.receipt_sha256
    target_receipt = target_authority.receipt_value
    if (
        target_receipt["schema_version"],
        target_receipt["contract"],
    ) != (
        precondition.receipt_value["schema_version"],
        precondition.receipt_value["contract"],
    ):
        raise DeploymentError("manual rollback across receipt contracts is unsupported")
    current = _rollback_endpoint_identity(
        precondition.receipt_sha256,
        precondition.receipt_value,
    )
    target = _rollback_endpoint_identity(target_sha256, target_receipt)
    if target.sequence >= current.sequence:
        raise DeploymentError("manual rollback target must precede the active receipt")
    classification = Classification(
        "approval-required",
        "explicit-manual-rollback-target",
    )
    unsigned = {
        "schema_version": 1,
        "contract": MANUAL_ROLLBACK_PLAN_CONTRACT,
        "operation": "manual-exact-target-rollback",
        "canonical_root": str(precondition.canonical_root),
        "effective_uid": os.geteuid(),
        "root_identity": list(precondition.root_identity),
        "activation_lock": _thaw(precondition.activation_lock),
        "activation_lock_identity": list(precondition.activation_lock_identity),
        "maintenance_transaction_sha256": request.maintenance_transaction_sha256,
        "expected_active_receipt_sha256": precondition.receipt_sha256,
        "target_receipt_sha256": target_sha256,
        "target_active_sha256": hashlib.sha256(target_authority.active_raw).hexdigest(),
        "target_authority_sha256": target_authority.authority_sha256,
        "target_successor_receipt_sha256": (target_authority.successor_receipt_sha256),
        "target_successor_rollback_sha256": (
            target_authority.successor_rollback_sha256
        ),
        "target_control_replacement": target_authority.control_replacement,
        "next_sequence": current.sequence + 1,
        "retained_receipt_names": sorted(precondition.retained_chain.receipt_names),
        "retained_result_sha256s": _thaw(
            _transaction_result_sha256s(precondition.retained_result_raws)
        ),
        "current": _rollback_endpoint_value(current),
        "target": _rollback_endpoint_value(target),
        "classification": {
            "outcome": classification.outcome,
            "reason": classification.reason,
        },
    }
    plan_sha256 = _digest(unsigned)
    plan = ManualRollbackPlan(
        precondition=precondition,
        current=current,
        target=target,
        target_authority=target_authority,
        classification=classification,
        maintenance_transaction_sha256=request.maintenance_transaction_sha256,
        plan_sha256=plan_sha256,
        value=_freeze({**unsigned, "plan_sha256": plan_sha256}),
    )
    facts = RollbackToAuthorizationFacts(
        canonical_root=precondition.canonical_root,
        effective_uid=os.geteuid(),
        plan_sha256=plan_sha256,
        maintenance_transaction_sha256=request.maintenance_transaction_sha256,
        expected_active_receipt_sha256=precondition.receipt_sha256,
        target_receipt_sha256=target_sha256,
    )
    return PreparedRollbackTo(plan, current, target, facts)


def prepare_rollback_to(request: RollbackToRequest) -> PreparedRollbackTo:
    """Derive one manual exact-target rollback plan without mutation."""

    _validate_rollback_to_request(request)
    precondition = _capture_active_deployment_precondition(
        request.canonical_root,
        request.expected_active_receipt_sha256,
    )
    return _prepare_rollback_to_against_precondition(request, precondition)


def prepare_deployment(request: DeploymentRequest) -> PreparedDeployment:
    """Derive one deployment plan against an exact current active receipt."""

    _validate_deployment_request(request)
    precondition = _capture_active_deployment_precondition(
        request.canonical_root,
        request.expected_active_receipt_sha256,
    )
    return _prepare_routine_against_precondition(request, precondition)


def prepare_bridge_transition(
    request: BridgeTransitionRequest,
    staging_root: Path,
) -> PreparedBridgeTransition:
    """Prepare one exact B1-to-current transition without creating a stage."""

    _validate_bridge_transition_request(request, staging_root)
    deployment = request.deployment
    stage_path = _normalized_absolute_path(
        staging_root,
        "bridge transition staging root",
    )
    try:
        stage_path.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise DeploymentError(
            "bridge transition staging root state is unavailable"
        ) from error
    else:
        raise DeploymentError("bridge transition staging root must be absent")
    try:
        precondition = _capture_active_deployment_precondition(
            deployment.canonical_root,
            deployment.expected_active_receipt_sha256,
            receipt_profile=BRIDGE_LEGACY_RECEIPT_PROFILE,
        )
    except DeploymentError as legacy_error:
        try:
            _capture_active_deployment_precondition(
                deployment.canonical_root,
                deployment.expected_active_receipt_sha256,
            )
        except DeploymentError:
            raise legacy_error
        raise DeploymentError(
            "bridge transition predecessor is not exact B1"
        ) from legacy_error
    _validate_exact_bridge_predecessor(precondition)
    manifest, manifest_raw, manifest_identity = _capture_bridge_release_manifest(
        request,
        stage_path,
    )
    prepared = _prepare_bridge_against_precondition(
        request,
        stage_path,
        precondition,
        manifest,
        manifest_raw,
    )
    try:
        manifest_after = request.release_manifest_path.lstat()
    except OSError as error:
        raise DeploymentError(
            "bridge transition release manifest became unavailable"
        ) from error
    try:
        stage_path.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise DeploymentError(
            "bridge transition staging root state is unavailable"
        ) from error
    else:
        raise DeploymentError("bridge transition staging root changed during prepare")
    if _identity(manifest_after) != manifest_identity:
        raise DeploymentError(
            "bridge transition release manifest changed during prepare"
        )
    return prepared


def prepare_first_install(request: FirstInstallRequest) -> PreparedFirstInstall:
    """Recompute one complete read-only first-install plan from raw inputs."""

    _validate_first_install_request(request)
    precondition = _capture_first_install_precondition(request.canonical_root)
    return _prepare_first_install_against_precondition(request, precondition)


def verify_deployment_stage(stage_receipt: Path) -> VerifiedDeploymentStage:
    """Creation-disabled public verification of one inert deployment stage."""

    return _verify_deployment_stage(stage_receipt)


def stage_first_install(
    request: FirstInstallRequest,
    authorization_raw: bytes,
    staging_root: Path,
) -> StagedDeployment:
    """Reprepare, authorize, create, and independently reread one inert stage."""

    if type(authorization_raw) is not bytes:
        raise DeploymentError("first-install authorization must be exact bytes")
    if not isinstance(staging_root, Path):
        raise DeploymentError("first-install staging root must be a Path value")
    prepared = prepare_first_install(request)
    staged = _materialize_first_install(
        prepared.plan,
        authorization_raw,
        staging_root,
    )
    verified = verify_deployment_stage(staged.stage_path)
    _require_independent_stage_verification(
        staged,
        verified,
        "staged deployment independent verification disagrees",
    )
    return staged


def stage_deployment(
    request: DeploymentRequest,
    authorization_raw: bytes,
    staging_root: Path,
) -> StagedDeployment:
    """Reprepare, authorize, and independently verify one routine stage."""

    if type(authorization_raw) is not bytes:
        raise DeploymentError("routine deployment authorization must be exact bytes")
    if not isinstance(staging_root, Path):
        raise DeploymentError("routine deployment staging root must be a Path value")
    prepared = prepare_deployment(request)
    staged = _materialize_routine_deployment(
        prepared,
        authorization_raw,
        staging_root,
    )
    verified = verify_deployment_stage(staged.stage_path)
    _require_independent_stage_verification(
        staged,
        verified,
        "routine stage independent verification disagrees",
    )
    return staged


def stage_bridge_transition(
    request: BridgeTransitionRequest,
    deployment_authorization_raw: bytes,
    transition_authorization_path: Path,
    staging_root: Path,
) -> StagedDeployment:
    """Reprepare and create one exact externally authorized bridge stage."""

    if type(deployment_authorization_raw) is not bytes:
        raise DeploymentError(
            "bridge ordinary deployment authorization must be exact bytes"
        )
    if not isinstance(transition_authorization_path, Path):
        raise DeploymentError(
            "bridge transition authorization path must be a Path value"
        )
    prepared = prepare_bridge_transition(request, staging_root)
    ordinary = PreparedDeployment(
        prepared.plan,
        prepared.authorization_facts,
    )
    expected_authorization_raw, expected_purpose = (
        _expected_deployment_authorization_raw(ordinary)
    )
    if (
        deployment_authorization_raw != expected_authorization_raw
        or hashlib.sha256(deployment_authorization_raw).hexdigest()
        != prepared.transition_authorization_facts.expected_deployment_authorization_sha256
    ):
        raise DeploymentError(
            "bridge ordinary deployment authorization prediction disagrees"
        )
    _validate_deployment_authorization(
        deployment_authorization_raw,
        prepared.authorization_facts,
        expected_purpose=expected_purpose,
    )
    _, manifest_raw, manifest_identity = _capture_bridge_release_manifest(
        request,
        prepared.transition_authorization_facts.staging_root,
    )
    if (
        hashlib.sha256(manifest_raw).hexdigest()
        != prepared.transition_authorization_facts.release_manifest_sha256
    ):
        raise DeploymentError("bridge transition release manifest changed before stage")
    transition_raw, transition_identity = _capture_bridge_transition_authorization(
        transition_authorization_path,
        request,
        prepared.transition_authorization_facts.staging_root,
    )
    transition = _validate_bridge_transition_authorization(
        transition_raw,
        prepared.transition_authorization_facts,
        deployment_authorization_raw,
    )
    try:
        manifest_after = request.release_manifest_path.lstat()
        transition_after = transition_authorization_path.lstat()
    except OSError as error:
        raise DeploymentError(
            "bridge transition evidence changed before stage"
        ) from error
    if (
        _identity(manifest_after) != manifest_identity
        or _identity(transition_after) != transition_identity
    ):
        raise DeploymentError("bridge transition evidence changed before stage")
    try:
        prepared.transition_authorization_facts.staging_root.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise DeploymentError(
            "bridge transition staging root state is unavailable"
        ) from error
    else:
        raise DeploymentError("bridge transition staging root changed before stage")
    staged = _materialize_routine_deployment(
        ordinary,
        deployment_authorization_raw,
        prepared.transition_authorization_facts.staging_root,
        bridge_authority=_BridgeStageAuthority(
            prepared.transition_authorization_facts,
            manifest_raw,
            _freeze_json(transition),
            transition_raw,
        ),
    )
    verified = verify_deployment_stage(staged.stage_path)
    _require_independent_stage_verification(
        staged,
        verified,
        "bridge stage independent verification disagrees",
    )
    return staged


def rollback_to(
    request: RollbackToRequest,
    authorization_raw: bytes,
    staging_root: Path,
) -> TransactionResult:
    """Stage and activate one explicitly authorized exact retained target."""

    if type(authorization_raw) is not bytes:
        raise DeploymentError("manual rollback authorization must be exact bytes")
    if not isinstance(staging_root, Path):
        raise DeploymentError("manual rollback staging root must be a Path value")
    prepared = prepare_rollback_to(request)
    staged = _materialize_manual_rollback(
        prepared,
        authorization_raw,
        staging_root,
    )
    activation = ActivationRequest(
        request,
        authorization_raw,
        staged.stage_path,
    )
    initial_precondition = prepared.plan.precondition
    root, lock_fd = _open_locked_activation_root(initial_precondition)
    try:
        _revalidate_locked_active_precondition(
            root,
            lock_fd,
            initial_precondition,
        )
        rebound, _, verified = _rebind_manual_rollback_activation(
            activation,
            initial_precondition,
        )
        if rebound != prepared:
            raise DeploymentError(
                "manual rollback preparation changed before locked activation"
            )
        _revalidate_locked_active_precondition(
            root,
            lock_fd,
            initial_precondition,
        )
        intent, _ = _manual_rollback_activation_intent(
            rebound,
            activation,
            verified,
        )
        return _continue_control_maintenance_transaction(
            root,
            lock_fd,
            initial_precondition,
            verified,
            intent,
            None,
        )
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)
            os.close(root.fd)


@dataclass(frozen=True)
class _ActivationJournal:
    value: Mapping[str, Any]
    raw: bytes


@dataclass(frozen=True)
class _TransactionResultRetentionState:
    directory_present: bool
    final_name: str | None
    temporary_name: str | None


@dataclass(frozen=True)
class _ActivationRemovalStep:
    operation: str
    index: int
    role: str
    artifact: StagedArtifact | None
    relative_path: str | None


@dataclass(frozen=True)
class _ControlMaintenanceReplacement:
    role: str
    current: StagedArtifact
    target: StagedArtifact


def _ordered_activation_artifacts(
    verified: VerifiedDeploymentStage,
) -> tuple[StagedArtifact, ...]:
    return tuple(
        sorted(
            (item for item in verified.artifacts if item.role != "shim"),
            key=lambda item: item.relative_path,
        )
        + [_staged_artifact_for_role(verified.artifacts, "shim")]
    )


def _ordered_routine_activation_artifacts(
    verified: VerifiedDeploymentStage,
) -> tuple[StagedArtifact, ...]:
    selector_roles = {
        "active-record",
        "deployment-alias",
        "prior-active-record",
        "prior-deployment-alias",
    }
    return tuple(
        sorted(
            (item for item in verified.artifacts if item.role not in selector_roles),
            key=lambda item: item.relative_path,
        )
    )


def _ordered_control_maintenance_additive_artifacts(
    verified: VerifiedDeploymentStage,
) -> tuple[StagedArtifact, ...]:
    replacement_roles = set(CONTROL_MAINTENANCE_REPLACEMENT_ROLES)
    prior_roles = {
        "prior-active-record",
        "prior-deployment-alias",
        *(f"prior-{role}" for role in CONTROL_PREIMAGE_ROLES),
    }
    additive = tuple(
        item
        for item in verified.artifacts
        if item.role not in replacement_roles and item.role not in prior_roles
    )
    observed_roles = {item.role for item in additive}
    manual_stage = verified.value["classification"] == {
        "outcome": "authorized-manual-exact-target-rollback",
        "reason": "exact-deployer-authorization",
    }
    expected_roles = (
        {"rollback-receipt", "deployment-receipt"}
        if manual_stage
        else CONTROL_MAINTENANCE_ADDITIVE_ROLES
    )
    if observed_roles != expected_roles:
        raise DeploymentError(
            "control maintenance additive artifact inventory disagrees"
        )
    rollback = _staged_artifact_for_role(additive, "rollback-receipt")
    deployment = _staged_artifact_for_role(additive, "deployment-receipt")
    middle = sorted(
        (item for item in additive if item is not rollback and item is not deployment),
        key=lambda item: item.relative_path,
    )
    return (rollback, *middle, deployment)


def _ordered_control_maintenance_replacements(
    verified: VerifiedDeploymentStage,
) -> tuple[_ControlMaintenanceReplacement, ...]:
    replacements: list[_ControlMaintenanceReplacement] = []
    for role in CONTROL_MAINTENANCE_REPLACEMENT_ROLES:
        target = _staged_artifact_for_role(verified.artifacts, role)
        current_role = {
            "active-record": "prior-active-record",
            "deployment-alias": "prior-deployment-alias",
        }.get(role, f"prior-{role}")
        current = _staged_artifact_for_role(verified.artifacts, current_role)
        if current.installed_path != target.installed_path:
            raise DeploymentError(
                "control maintenance replacement path binding disagrees"
            )
        replacements.append(_ControlMaintenanceReplacement(role, current, target))
    return tuple(replacements)


def _ordered_control_maintenance_cleanup_steps(
    precondition: ActiveDeploymentPrecondition,
    additive: tuple[StagedArtifact, ...],
) -> tuple[_ActivationRemovalStep, ...]:
    rollback = _staged_artifact_for_role(additive, "rollback-receipt")
    deployment = _staged_artifact_for_role(additive, "deployment-receipt")
    baseline_files = set(_routine_baseline_file_inventory(precondition))
    owned = [
        artifact
        for artifact in additive
        if artifact.relative_path not in baseline_files
    ]
    if rollback not in owned or deployment not in owned:
        raise DeploymentError("control maintenance cleanup receipt ownership disagrees")
    middle = sorted(
        (
            artifact
            for artifact in owned
            if artifact is not rollback and artifact is not deployment
        ),
        key=lambda artifact: artifact.relative_path,
    )
    baseline_directories = set(_routine_file_parent_inventory(baseline_files))
    owned_directories = {
        directory
        for artifact in owned
        for directory in _activation_artifact_parent_directories(artifact)
        if directory not in baseline_directories
    }
    steps: list[_ActivationRemovalStep] = []

    def append_artifact(artifact: StagedArtifact) -> None:
        steps.append(
            _ActivationRemovalStep(
                "remove-artifact",
                len(steps),
                artifact.role,
                artifact,
                None,
            )
        )

    append_artifact(rollback)
    for artifact in middle:
        append_artifact(artifact)
    for relative in sorted(
        owned_directories,
        key=lambda item: (item.count("/"), item),
        reverse=True,
    ):
        steps.append(
            _ActivationRemovalStep(
                "remove-directory",
                len(steps),
                relative,
                None,
                relative,
            )
        )
    append_artifact(deployment)
    return tuple(steps)


def _ordered_routine_cleanup_steps(
    precondition: ActiveDeploymentPrecondition,
    verified: VerifiedDeploymentStage,
) -> tuple[_ActivationRemovalStep, ...]:
    artifacts = _ordered_routine_activation_artifacts(verified)
    rollback = _staged_artifact_for_role(
        verified.artifacts,
        "rollback-receipt",
    )
    deployment = _staged_artifact_for_role(
        verified.artifacts,
        "deployment-receipt",
    )
    baseline_files = set(_routine_baseline_file_inventory(precondition))
    owned = [
        artifact
        for artifact in artifacts
        if artifact.relative_path not in baseline_files
    ]
    if rollback not in owned or deployment not in owned:
        raise DeploymentError("routine activation receipt cleanup ownership disagrees")
    middle = sorted(
        (
            artifact
            for artifact in owned
            if artifact is not rollback and artifact is not deployment
        ),
        key=lambda artifact: artifact.relative_path,
    )
    baseline_directories = set(_routine_file_parent_inventory(baseline_files))
    owned_directories = {
        directory
        for artifact in owned
        for directory in _activation_artifact_parent_directories(artifact)
        if directory not in baseline_directories
    }
    steps: list[_ActivationRemovalStep] = []

    def append_artifact(artifact: StagedArtifact) -> None:
        steps.append(
            _ActivationRemovalStep(
                "remove-artifact",
                len(steps),
                artifact.role,
                artifact,
                None,
            )
        )

    append_artifact(rollback)
    for artifact in middle:
        append_artifact(artifact)
    for relative in sorted(
        owned_directories,
        key=lambda item: (item.count("/"), item),
        reverse=True,
    ):
        steps.append(
            _ActivationRemovalStep(
                "remove-directory",
                len(steps),
                relative,
                None,
                relative,
            )
        )
    append_artifact(deployment)
    return tuple(steps)


def _ordered_activation_removal_steps(
    artifacts: tuple[StagedArtifact, ...],
) -> tuple[_ActivationRemovalStep, ...]:
    steps: list[_ActivationRemovalStep] = []
    for artifact in reversed(artifacts):
        steps.append(
            _ActivationRemovalStep(
                "remove-artifact",
                len(steps),
                artifact.role,
                artifact,
                None,
            )
        )
    directories = {
        "/".join(artifact.relative_path.split("/")[:index])
        for artifact in artifacts
        for index in range(1, len(artifact.relative_path.split("/")))
    }
    for relative in sorted(
        directories,
        key=lambda item: (item.count("/"), item),
        reverse=True,
    ):
        steps.append(
            _ActivationRemovalStep(
                "remove-directory",
                len(steps),
                relative,
                None,
                relative,
            )
        )
    return tuple(steps)


def _validate_activation_request(request: ActivationRequest) -> None:
    if type(request) is not ActivationRequest:
        raise DeploymentError("activation request type mismatch")
    if type(request.authorization_raw) is not bytes:
        raise DeploymentError("activation authorization must be exact bytes")
    if not isinstance(request.stage_receipt, Path):
        raise DeploymentError("activation stage receipt must be a Path value")
    if type(request.deployment) is FirstInstallRequest:
        _validate_first_install_request(request.deployment)
    elif type(request.deployment) is DeploymentRequest:
        _validate_deployment_request(request.deployment)
    elif type(request.deployment) is BridgeTransitionRequest:
        _validate_bridge_transition_request(
            request.deployment,
            request.stage_receipt.parent,
        )
    elif type(request.deployment) is RollbackToRequest:
        _validate_rollback_to_request(request.deployment)
    else:
        raise DeploymentError("activation deployment request type mismatch")


def _activation_file_binding(artifact: StagedArtifact) -> dict[str, Any]:
    return {
        "path": str(artifact.installed_path),
        "length": len(artifact.raw),
        "sha256": hashlib.sha256(artifact.raw).hexdigest(),
        "owner": artifact.installed["owner"],
        "mode": artifact.installed["mode"],
    }


def _activation_identity_vector(value: object, label: str) -> tuple[int, ...]:
    if not isinstance(value, list) or len(value) != 8:
        raise DeploymentError(f"{label} schema drift")
    return tuple(
        _nonnegative_integer(item, f"{label}[{index}]")
        for index, item in enumerate(value)
    )


def _recorded_first_install_precondition(
    rollback_raw: bytes,
) -> FirstInstallPrecondition:
    label = "first-install recorded rollback receipt"
    rollback = _exact(
        _parse_canonical_json(rollback_raw, label),
        {
            "schema_version",
            "contract",
            "state",
            "canonical_root",
            "effective_uid",
            "activation_lock",
            "deployment_receipt_absent",
            "precondition",
            "prior_activation_unit",
            "external_dependencies",
            "smoke",
            "content_sha256",
        },
        label,
    )
    _content_sha256(rollback, label)
    root = _normalized_absolute_path(
        Path(_text(rollback["canonical_root"], f"{label}.canonical_root")),
        f"{label}.canonical_root",
    )
    effective_uid = _nonnegative_integer(
        rollback["effective_uid"],
        f"{label}.effective_uid",
    )
    lock = _exact(
        rollback["activation_lock"],
        {"path", "device", "inode", "owner", "mode"},
        f"{label}.activation_lock",
    )
    lock_path = _normalized_absolute_path(
        Path(_text(lock["path"], f"{label}.activation_lock.path")),
        f"{label}.activation_lock.path",
    )
    for key in ("device", "inode", "owner", "mode"):
        _nonnegative_integer(lock[key], f"{label}.activation_lock.{key}")
    recorded = _exact(
        rollback["precondition"],
        {"root_identity", "activation_lock_identity"},
        f"{label}.precondition",
    )
    root_identity = _activation_identity_vector(
        recorded["root_identity"],
        f"{label}.precondition.root_identity",
    )
    lock_identity = _activation_identity_vector(
        recorded["activation_lock_identity"],
        f"{label}.precondition.activation_lock_identity",
    )
    smoke = _exact(
        rollback["smoke"],
        {"contract", "expected_state"},
        f"{label}.smoke",
    )
    if (
        rollback["schema_version"] != 1
        or type(rollback["schema_version"]) is not int
        or rollback["contract"] != ROLLBACK_RECEIPT_CONTRACT
        or rollback["state"] != "absent"
        or effective_uid != os.geteuid()
        or lock_path != root / "activation.lock"
        or lock["path"] != str(lock_path)
        or lock["owner"] != effective_uid
        or lock["mode"] != 0o600
        or rollback["deployment_receipt_absent"] is not True
        or rollback["prior_activation_unit"] != []
        or rollback["external_dependencies"] != []
        or smoke
        != {
            "contract": FIRST_INSTALL_ROLLBACK_CONTRACT,
            "expected_state": "absent",
        }
        or not stat.S_ISDIR(root_identity[2])
        or root_identity[3] != effective_uid
        or stat.S_IMODE(root_identity[2]) != 0o700
        or not stat.S_ISREG(lock_identity[2])
        or lock_identity[0] != lock["device"]
        or lock_identity[1] != lock["inode"]
        or lock_identity[3] != lock["owner"]
        or lock_identity[4] != 1
        or lock_identity[5] != 0
        or stat.S_IMODE(lock_identity[2]) != lock["mode"]
    ):
        raise DeploymentError("first-install recorded precondition disagrees")
    return FirstInstallPrecondition(
        canonical_root=root,
        root_identity=root_identity,
        activation_lock=_freeze(dict(lock)),
        activation_lock_identity=lock_identity,
        deployment_receipt_absent=True,
        retained_result_sha256s=_freeze({}),
    )


def _rebind_first_install_activation(
    request: ActivationRequest,
    *,
    recorded_precondition: FirstInstallPrecondition | None = None,
) -> tuple[PreparedFirstInstall, FirstInstallAuthorization, VerifiedDeploymentStage]:
    prepared = (
        prepare_first_install(request.deployment)
        if recorded_precondition is None
        else _prepare_first_install_against_precondition(
            request.deployment,
            recorded_precondition,
        )
    )
    plan = prepared.plan
    controller = _artifact_for_role(plan, "controller")
    policy = _artifact_for_role(plan, "policy")
    authorization = _validate_first_install_authorization(
        request.authorization_raw,
        canonical_root=str(plan.precondition.canonical_root),
        effective_uid=os.geteuid(),
        plan_sha256=plan.plan_sha256,
        maintenance_transaction_sha256=plan.maintenance_transaction_sha256,
        candidate_controller_sha256=controller.sha256,
        candidate_policy_sha256=policy.sha256,
        source_selection_sha256=plan.source.source_selection_sha256,
        source_evidence_sha256=plan.source.source_evidence_sha256,
    )
    verified = verify_deployment_stage(request.stage_receipt)
    stage = verified.value
    if (
        stage["canonical_root"] != str(plan.precondition.canonical_root)
        or stage["plan_sha256"] != plan.plan_sha256
        or stage["maintenance_transaction_sha256"]
        != plan.maintenance_transaction_sha256
        or stage["authorization"]["sha256"]
        != hashlib.sha256(request.authorization_raw).hexdigest()
        or stage["authorization"]["content_sha256"] != authorization.content_sha256
    ):
        raise DeploymentError("activation request, authorization, and stage disagree")
    rollback_value, rollback_raw = _first_install_rollback_receipt(plan)
    rollback_sha256 = hashlib.sha256(rollback_raw).hexdigest()
    rollback_path = plan.precondition.canonical_root / (
        f"receipts/sha256-{rollback_sha256}.json"
    )
    deployment_value, deployment_raw = _first_install_deployment_receipt(
        plan,
        authorization,
        request.authorization_raw,
        rollback_path,
        rollback_raw,
    )
    deployment_sha256 = hashlib.sha256(deployment_raw).hexdigest()
    expected = {
        item.relative_path: (item.role, item.raw, item.mode) for item in plan.artifacts
    }
    expected.update(
        {
            f"receipts/sha256-{rollback_sha256}.json": (
                "rollback-receipt",
                rollback_raw,
                0o600,
            ),
            f"receipts/sha256-{deployment_sha256}.json": (
                "deployment-receipt",
                deployment_raw,
                0o600,
            ),
            "deployment.json": ("deployment-alias", deployment_raw, 0o600),
        }
    )
    observed = {
        item.relative_path: (
            item.role,
            item.raw,
            item.installed["mode"],
        )
        for item in verified.artifacts
    }
    if observed != expected:
        raise DeploymentError("activation stage no longer matches the prepared plan")
    staged_deployment = _parse_canonical_json(
        _staged_artifact_for_role(
            verified.artifacts,
            "deployment-receipt",
        ).raw,
        "activation candidate deployment receipt",
    )
    if staged_deployment != deployment_value or rollback_value["state"] != "absent":
        raise DeploymentError("activation candidate receipt rebind disagrees")
    return prepared, authorization, verified


def _validate_installed_receipt_binding(
    binding: Mapping[str, Any],
    label: str,
) -> None:
    path = _normalized_absolute_path(
        Path(_text(binding["path"], f"{label}.path")),
        f"{label}.path",
    )
    length = _nonnegative_integer(binding["length"], f"{label}.length")
    raw = _capture_absolute_regular(path, max(length, 1), label)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise DeploymentError(f"{label} disposition is unavailable") from error
    if (
        len(raw) != length
        or hashlib.sha256(raw).hexdigest()
        != _sha256(binding["sha256"], f"{label}.sha256")
        or metadata.st_uid != _nonnegative_integer(binding["owner"], f"{label}.owner")
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode)
        != _nonnegative_integer(binding["mode"], f"{label}.mode")
    ):
        raise DeploymentError(f"{label} binding disagrees")


def _revalidate_locked_active_precondition(
    activation_root: _RootSnapshot,
    activation_lock_fd: int,
    precondition: ActiveDeploymentPrecondition,
    *,
    source_parser: Callable[[Mapping[str, Any], Path], CandidateSource] | None = None,
    policy_parser: Callable[[bytes], CompatibilityPolicy] | None = None,
) -> None:
    _recheck_activation_lock_acquisition(
        activation_root,
        activation_lock_fd,
        precondition,
    )
    if "transaction.json" in os.listdir(activation_root.fd):
        raise DeploymentError("routine activation has an in-flight transaction")
    if (
        _read_activation_file(
            activation_root.fd,
            "deployment.json",
            "routine active deployment receipt alias",
        )
        != precondition.receipt_raw
        or _read_activation_file(
            activation_root.fd,
            "active.json",
            "routine active record",
        )
        != precondition.active_raw
        or _capture_transaction_result_inventory(
            activation_root.fd,
            precondition.canonical_root,
        )[0]
        != precondition.retained_result_raws
    ):
        raise DeploymentError("routine active selectors changed before activation")
    source = _validate_active_runtime_and_trust(
        precondition.receipt_value,
        precondition.active_raw,
        precondition.canonical_root,
        source_parser=source_parser,
    )
    if source != precondition.active_source:
        raise DeploymentError("routine active source changed before activation")
    chain = _validate_retained_receipt_chain(
        precondition.receipt_value,
        precondition.receipt_raw,
        precondition.canonical_root,
        active_policy=precondition.active_policy,
        receipt_profile=(
            BRIDGE_LEGACY_RECEIPT_PROFILE
            if source_parser is _bridge_legacy_active_receipt_source
            else CURRENT_RECEIPT_PROFILE
        ),
    )
    if chain != precondition.retained_chain:
        raise DeploymentError(
            "routine retained receipt chain changed before activation"
        )
    controls = _exact(
        precondition.receipt_value["control_set"],
        {"shim", "client", "launcher", "controller", "policy"},
        "routine active control set",
    )
    for role, binding in controls.items():
        _validate_installed_receipt_binding(
            binding,
            f"routine active {role}",
        )
    policy_binding = precondition.receipt_value["compatibility_policy"]
    policy_raw = _capture_absolute_regular(
        Path(policy_binding["path"]),
        MAX_JSON_BYTES,
        "routine active compatibility policy",
    )
    observed_policy = (
        _parse_compatibility_policy(policy_raw)
        if policy_parser is None
        else policy_parser(policy_raw)
    )
    if observed_policy != precondition.active_policy:
        raise DeploymentError("routine active policy changed before activation")
    _recheck_activation_lock_acquisition(
        activation_root,
        activation_lock_fd,
        precondition,
    )
    if (
        "transaction.json" in os.listdir(activation_root.fd)
        or _capture_transaction_result_inventory(
            activation_root.fd,
            precondition.canonical_root,
        )[0]
        != precondition.retained_result_raws
        or _read_activation_file(
            activation_root.fd,
            "deployment.json",
            "routine active deployment receipt alias",
        )
        != precondition.receipt_raw
        or _read_activation_file(
            activation_root.fd,
            "active.json",
            "routine active record",
        )
        != precondition.active_raw
    ):
        raise DeploymentError("routine active state changed during locked rebind")


def _rebind_routine_activation(
    request: ActivationRequest,
    precondition: ActiveDeploymentPrecondition,
) -> tuple[PreparedDeployment, DeploymentAuthorization, VerifiedDeploymentStage]:
    if type(request.deployment) is not DeploymentRequest:
        raise DeploymentError("routine activation deployment request type mismatch")
    prepared = _prepare_routine_against_precondition(
        request.deployment,
        precondition,
    )
    control_maintenance = type(prepared.plan) is ControlSetDeploymentPlan
    authorization_purpose = _active_prior_authorization_purpose(
        prepared.plan.classification,
        control_maintenance=control_maintenance,
    )
    authorization = _validate_deployment_authorization(
        request.authorization_raw,
        prepared.authorization_facts,
        expected_purpose=authorization_purpose,
    )
    verified = verify_deployment_stage(request.stage_receipt)
    stage = verified.value
    if (
        stage["canonical_root"] != str(precondition.canonical_root)
        or stage["plan_sha256"] != prepared.plan.plan_sha256
        or stage["maintenance_transaction_sha256"]
        != prepared.plan.maintenance_transaction_sha256
        or stage["authorization"]["sha256"]
        != hashlib.sha256(request.authorization_raw).hexdigest()
        or stage["authorization"]["content_sha256"] != authorization.content_sha256
    ):
        raise DeploymentError(
            "routine activation request, authorization, and stage disagree"
        )
    rollback_value, rollback_raw = _routine_rollback_receipt(
        prepared.plan,
        verified.path.parent,
    )
    rollback_sha256 = hashlib.sha256(rollback_raw).hexdigest()
    rollback_path = precondition.canonical_root / (
        f"receipts/sha256-{rollback_sha256}.json"
    )
    deployment_value, deployment_raw = _routine_deployment_receipt(
        prepared.plan,
        authorization,
        request.authorization_raw,
        rollback_path,
        rollback_raw,
        authorization_purpose=authorization_purpose,
    )
    deployment_sha256 = hashlib.sha256(deployment_raw).hexdigest()
    if (
        _staged_artifact_for_role(
            verified.artifacts,
            "rollback-receipt",
        ).raw
        != rollback_raw
        or _staged_artifact_for_role(
            verified.artifacts,
            "deployment-receipt",
        ).raw
        != deployment_raw
        or _staged_artifact_for_role(
            verified.artifacts,
            "deployment-alias",
        ).raw
        != deployment_raw
        or _staged_artifact_for_role(
            verified.artifacts,
            "active-record",
        ).raw
        != prepared.plan.active.raw
        or _staged_artifact_for_role(
            verified.artifacts,
            "prior-deployment-alias",
        ).raw
        != precondition.receipt_raw
        or _staged_artifact_for_role(
            verified.artifacts,
            "prior-active-record",
        ).raw
        != precondition.active_raw
        or rollback_value["prior_activation_unit"] != _thaw(precondition.active_unit)
        or stage["rollback_receipt"]
        != {"path": str(rollback_path), "sha256": rollback_sha256}
        or stage["deployment_receipt"]
        != {
            "path": str(
                precondition.canonical_root
                / "receipts"
                / f"sha256-{deployment_sha256}.json"
            ),
            "sha256": deployment_sha256,
        }
        or _parse_canonical_json(
            deployment_raw,
            "routine activation candidate deployment receipt",
        )
        != deployment_value
    ):
        raise DeploymentError("routine activation stage rebind disagrees")
    if control_maintenance:
        planned_artifacts = _control_maintenance_stage_artifacts(
            prepared.plan,
            deployment_raw=deployment_raw,
            deployment_sha256=deployment_sha256,
            rollback_raw=rollback_raw,
            rollback_sha256=rollback_sha256,
        )
        planned = {
            (
                item.role,
                item.relative_path,
                str(item.installed_path),
                item.sha256,
                item.mode,
            )
            for item in planned_artifacts
        }
        observed = {
            (
                item.role,
                item.relative_path,
                str(item.installed_path),
                hashlib.sha256(item.raw).hexdigest(),
                item.installed["mode"],
            )
            for item in verified.artifacts
        }
    else:
        planned = {
            (
                item.role,
                item.relative_path,
                str(item.installed_path),
                item.sha256,
                item.mode,
            )
            for item in prepared.plan.artifacts
            if item.role
            not in {
                "shim",
                "client",
                "launcher",
                "controller",
                "policy",
                "active-record",
            }
        }
        observed = {
            (
                item.role,
                item.relative_path,
                str(item.installed_path),
                hashlib.sha256(item.raw).hexdigest(),
                item.installed["mode"],
            )
            for item in verified.artifacts
            if item.role
            not in {
                "active-record",
                "deployment-alias",
                "prior-active-record",
                "prior-deployment-alias",
                "rollback-receipt",
                "deployment-receipt",
            }
        }
    if observed != planned:
        raise DeploymentError("routine activation staged payload plan disagrees")
    return prepared, authorization, verified


def _rebind_manual_rollback_activation(
    request: ActivationRequest,
    precondition: ActiveDeploymentPrecondition,
) -> tuple[PreparedRollbackTo, RollbackToAuthorization, VerifiedDeploymentStage]:
    if type(request.deployment) is not RollbackToRequest:
        raise DeploymentError("manual rollback activation request type mismatch")
    prepared = _prepare_rollback_to_against_precondition(
        request.deployment,
        precondition,
    )
    authorization = _validate_rollback_to_authorization(
        request.authorization_raw,
        prepared.authorization_facts,
    )
    verified = verify_deployment_stage(request.stage_receipt)
    stage_root = verified.path.parent
    rollback_value, rollback_raw, deployment_value, deployment_raw = (
        _manual_rollback_receipts(
            prepared.plan,
            authorization,
            request.authorization_raw,
            stage_root,
        )
    )
    expected = _manual_rollback_stage_artifacts(
        prepared.plan,
        rollback_raw=rollback_raw,
        deployment_raw=deployment_raw,
    )
    observed = tuple(
        (item.role, item.relative_path, item.installed_path, item.raw)
        for item in verified.artifacts
    )
    prescribed = tuple(
        (item.role, item.relative_path, item.installed_path, item.raw)
        for item in expected
    )
    rollback_artifact = _staged_artifact_for_role(
        verified.artifacts,
        "rollback-receipt",
    )
    deployment_artifact = _staged_artifact_for_role(
        verified.artifacts,
        "deployment-receipt",
    )
    if (
        verified.value["canonical_root"] != str(precondition.canonical_root)
        or verified.value["plan_sha256"] != prepared.plan.plan_sha256
        or verified.value["maintenance_transaction_sha256"]
        != prepared.plan.maintenance_transaction_sha256
        or verified.value["classification"]
        != {
            "outcome": "authorized-manual-exact-target-rollback",
            "reason": "exact-deployer-authorization",
        }
        or verified.value["authorization"]
        != {
            "sha256": hashlib.sha256(request.authorization_raw).hexdigest(),
            "content_sha256": authorization.content_sha256,
        }
        or rollback_artifact.raw != rollback_raw
        or deployment_artifact.raw != deployment_raw
        or _thaw(rollback_value)
        != _parse_canonical_json(rollback_artifact.raw, "manual rollback stage")
        or _thaw(deployment_value)
        != _parse_canonical_json(deployment_artifact.raw, "manual deployment stage")
        or observed != prescribed
    ):
        raise DeploymentError(
            "manual rollback request, authorization, and stage disagree"
        )
    return prepared, authorization, verified


def _stage_bound_routine_plan_artifacts(
    precondition: ActiveDeploymentPrecondition,
    verified: VerifiedDeploymentStage,
    deployment: Mapping[str, Any],
) -> list[dict[str, Any]]:
    excluded_roles = {
        "deployment-alias",
        "prior-active-record",
        "prior-deployment-alias",
        "rollback-receipt",
        "deployment-receipt",
        *(f"prior-{role}" for role in CONTROL_PREIMAGE_ROLES),
    }
    artifacts: list[dict[str, Any]] = []
    for artifact in verified.artifacts:
        if artifact.role in excluded_roles:
            continue
        try:
            relative_path = artifact.installed_path.relative_to(
                precondition.canonical_root
            ).as_posix()
        except ValueError as error:
            raise DeploymentError(
                "stage-bound routine artifact is outside the canonical root"
            ) from error
        artifacts.append(
            {
                "role": artifact.role,
                "relative_path": relative_path,
                "installed_path": str(artifact.installed_path),
                "length": len(artifact.raw),
                "sha256": hashlib.sha256(artifact.raw).hexdigest(),
                "owner": artifact.installed["owner"],
                "mode": artifact.installed["mode"],
            }
        )
    observed_roles = {item["role"] for item in artifacts}
    controls = _exact(
        deployment["control_set"],
        set(CONTROL_SET_ROLES),
        "stage-bound routine control set",
    )
    for role in ("client", "controller", "policy", "launcher", "shim"):
        if role in observed_roles:
            continue
        binding = _receipt_file_binding(
            controls[role],
            f"stage-bound routine {role}",
        )
        path = Path(binding["path"])
        try:
            relative_path = path.relative_to(precondition.canonical_root).as_posix()
        except ValueError as error:
            raise DeploymentError(
                f"stage-bound routine {role} is outside the canonical root"
            ) from error
        artifacts.append(
            {
                "role": role,
                "relative_path": relative_path,
                "installed_path": str(path),
                "length": binding["length"],
                "sha256": binding["sha256"],
                "owner": binding["owner"],
                "mode": binding["mode"],
            }
        )
    artifacts.sort(key=lambda item: item["relative_path"])
    paths = [item["relative_path"] for item in artifacts]
    roles = [item["role"] for item in artifacts]
    if len(paths) != len(set(paths)) or len(roles) != len(set(roles)) + max(
        roles.count("validator-module") - 1,
        0,
    ):
        raise DeploymentError("stage-bound routine artifact inventory conflicts")
    return artifacts


def _stage_bound_routine_source(
    request: DeploymentRequest,
    deployment: Mapping[str, Any],
    canonical_root: Path,
) -> CandidateSource:
    selection = _parse_source_selection(request.source_selection_raw)
    evidence = _validate_source_evidence(selection, request.source_evidence)
    source = _active_receipt_source(deployment, canonical_root)
    if (
        source.source_mode != selection.mode
        or source.publisher_id != selection.publisher_id
        or source.manifest_author != selection.manifest_author
        or source.repository_id != selection.repository_id
        or source.repository_url != selection.repository_url
        or source.release_version != selection.release_version
        or source.revision != selection.revision
        or source.subtree_sha256 != selection.subtree_sha256
        or source.source_authority != selection.source_authority
        or source.channel != evidence.channel
        or source.source_trust_class != evidence.source_trust_class
        or source.lineage != evidence.lineage
        or source.source_selection_content_sha256 != selection.content_sha256
        or source.source_selection_sha256
        != hashlib.sha256(request.source_selection_raw).hexdigest()
        or source.source_evidence_sha256 != evidence.source_evidence_sha256
        or source.source_binding_content_sha256 != evidence.binding_content_sha256
        or source.source_binding_sha256 != evidence.binding_sha256
        or source.source_record_sha256 != evidence.record_sha256
        or source.source_resolver != evidence.resolver
        or source.source_adapter_sha256 != evidence.adapter_sha256
    ):
        raise DeploymentError("stage-bound routine source authority disagrees")
    return source


def _rebind_routine_activation_from_stage(
    request: ActivationRequest,
    precondition: ActiveDeploymentPrecondition,
    verified: VerifiedDeploymentStage,
    *,
    bridge_stage: bool = False,
) -> bool:
    if type(request.deployment) is not DeploymentRequest:
        raise DeploymentError("stage-bound routine deployment request type mismatch")
    deployment_request = request.deployment
    _validate_deployment_request(deployment_request)
    if (
        precondition.canonical_root != deployment_request.canonical_root
        or precondition.receipt_sha256
        != deployment_request.expected_active_receipt_sha256
    ):
        raise DeploymentError("stage-bound routine recorded precondition disagrees")
    deployment_artifact = _staged_artifact_for_role(
        verified.artifacts,
        "deployment-receipt",
    )
    deployment = _parse_canonical_json(
        deployment_artifact.raw,
        "stage-bound routine deployment receipt",
    )
    _content_sha256(deployment, "stage-bound routine deployment receipt")
    source = _stage_bound_routine_source(
        deployment_request,
        deployment,
        precondition.canonical_root,
    )
    qualification = _parse_runtime_qualification(
        deployment_request.runtime_qualification_raw
    )
    expected_interpreter = {
        "executable": qualification.main_executable["path"],
        "implementation": qualification.main_executable["implementation"],
        "version": _thaw(qualification.main_executable["version"]),
        "executable_sha256": qualification.main_executable["sha256"],
    }
    expected_runtime_closure = {
        **_thaw(qualification.runtime_closure),
        "dependency_classes": list(qualification.dependency_classes),
        "qualification_content_sha256": qualification.content_sha256,
    }
    if (
        deployment["platform"] != _thaw(qualification.platform)
        or deployment["interpreter"] != expected_interpreter
        or deployment["runtime_closure"] != expected_runtime_closure
    ):
        raise DeploymentError("stage-bound routine qualification disagrees")
    candidate_policy_sha256 = _sha256(
        deployment["compatibility_policy"]["sha256"],
        "stage-bound routine candidate policy digest",
    )
    classification = _classify_candidate_source(
        active_source=precondition.active_source,
        active_policy=precondition.active_policy,
        active_policy_sha256=precondition.receipt_value["compatibility_policy"][
            "sha256"
        ],
        candidate_source=source,
        candidate_policy_sha256=candidate_policy_sha256,
    )
    maintenance_differences = _maintenance_authority_differences(
        _receipt_maintenance_authority_surface(precondition.receipt_value),
        _receipt_maintenance_authority_surface(deployment),
    )
    control_maintenance = bool(maintenance_differences)
    _require_deployment_source_outcome(
        classification,
        control_maintenance=control_maintenance,
        label="stage-bound deployment",
    )
    if control_maintenance and set(precondition.control_raws) != set(
        CONTROL_PREIMAGE_ROLES
    ):
        raise DeploymentError("stage-bound control prior preimage is incomplete")
    source_value = {
        "plugin_id": source.plugin_id,
        "mode": source.source_mode,
        "publisher_id": source.publisher_id,
        "manifest_author": _thaw(source.manifest_author),
        "repository_id": source.repository_id,
        "repository_url": source.repository_url,
        "release_version": source.release_version,
        "revision": source.revision,
        "subtree_sha256": source.subtree_sha256,
        "source_authority": source.source_authority,
        "details": _source_details_projection(source),
        "source_selection_sha256": source.source_selection_sha256,
        "source_evidence_sha256": source.source_evidence_sha256,
    }
    operation = (
        "complete-control-set-maintenance" if control_maintenance else "routine-payload"
    )
    unsigned = {
        "contract": "task-witness-deployment-plan-v1",
        "operation": operation,
        "canonical_root": str(precondition.canonical_root),
        "effective_uid": os.geteuid(),
        "root_identity": list(precondition.root_identity),
        "activation_lock": _thaw(precondition.activation_lock),
        "deployment_receipt_absent": False,
        "retained_result_sha256s": _thaw(
            _transaction_result_sha256s(precondition.retained_result_raws)
        ),
        "maintenance_transaction_sha256": _sha256(
            deployment_request.maintenance_transaction_sha256,
            "stage-bound routine maintenance transaction digest",
        ),
        "source": source_value,
        "runtime_qualification_content_sha256": qualification.content_sha256,
        "candidate_policy_sha256": candidate_policy_sha256,
        "classification": {
            "outcome": classification.outcome,
            "reason": classification.reason,
        },
        "artifacts": _stage_bound_routine_plan_artifacts(
            precondition,
            verified,
            deployment,
        ),
        "expected_active_receipt_sha256": precondition.receipt_sha256,
    }
    if control_maintenance:
        unsigned["maintenance_differences"] = list(maintenance_differences)
    plan_sha256 = _digest(unsigned)
    controls = _exact(
        deployment["control_set"],
        set(CONTROL_SET_ROLES),
        "stage-bound routine control set",
    )
    controller = _receipt_file_binding(
        controls["controller"],
        "stage-bound routine controller",
    )
    policy = _receipt_file_binding(
        controls["policy"],
        "stage-bound routine policy",
    )
    facts = DeploymentAuthorizationFacts(
        canonical_root=precondition.canonical_root,
        effective_uid=os.geteuid(),
        plan_sha256=plan_sha256,
        maintenance_transaction_sha256=unsigned["maintenance_transaction_sha256"],
        candidate_controller_sha256=controller["sha256"],
        candidate_policy_sha256=policy["sha256"],
        source_selection_sha256=source.source_selection_sha256,
        source_evidence_sha256=source.source_evidence_sha256,
        expected_active_receipt_sha256=precondition.receipt_sha256,
    )
    authorization_purpose = _active_prior_authorization_purpose(
        classification,
        control_maintenance=control_maintenance,
    )
    authorization = _validate_deployment_authorization(
        request.authorization_raw,
        facts,
        expected_purpose=authorization_purpose,
    )
    expected_stage_classification = (
        {
            "outcome": "authorized-bridge-transition",
            "reason": "exact-bridge-transition-authorization",
        }
        if bridge_stage
        else {
            "outcome": "authorized-control-set-maintenance",
            "reason": "exact-deployer-authorization",
        }
        if control_maintenance
        else {
            "outcome": "authorized-routine-payload",
            "reason": "active-policy-compatible-forward",
        }
    )
    stage = verified.value
    if (
        stage["canonical_root"] != str(precondition.canonical_root)
        or stage["plan_sha256"] != plan_sha256
        or stage["maintenance_transaction_sha256"]
        != unsigned["maintenance_transaction_sha256"]
        or stage["authorization"]["sha256"]
        != hashlib.sha256(request.authorization_raw).hexdigest()
        or stage["authorization"]["content_sha256"] != authorization.content_sha256
        or stage["classification"] != expected_stage_classification
    ):
        raise DeploymentError(
            "stage-bound routine request, authorization, and stage disagree"
        )
    return control_maintenance


def _bridge_transition_evidence_for_role(
    evidence: tuple[StagedTransitionEvidence, ...],
    role: str,
) -> StagedTransitionEvidence:
    matches = tuple(item for item in evidence if item.role == role)
    if len(matches) != 1:
        raise DeploymentError(f"staged bridge {role} evidence is not unique")
    return matches[0]


def _bridge_transition_activation_projection(
    authorization: Mapping[str, Any],
    authorization_raw: bytes,
) -> dict[str, Any]:
    projection = {
        "execution_class": authorization["execution_class"],
        "maintenance_transaction_sha256": authorization[
            "maintenance_transaction_sha256"
        ],
        "deployment_authorization_sha256": authorization[
            "deployment_authorization_sha256"
        ],
        "transition_authorization_sha256": hashlib.sha256(
            authorization_raw
        ).hexdigest(),
        "expected_active_receipt_core_sha256": authorization[
            "expected_active_receipt_core_sha256"
        ],
        "bridge_identity_sha256": authorization["bridge_identity_sha256"],
        "release_manifest_sha256": authorization["release_manifest_sha256"],
        "endpoint_projection_sha256": authorization["endpoint_projection_sha256"],
    }
    if authorization["execution_class"] == "live-migration":
        projection["prior_rehearsal"] = _thaw(authorization["prior_rehearsal"])
    return projection


def _rebind_bridge_activation(
    request: ActivationRequest,
    precondition: ActiveDeploymentPrecondition,
    *,
    staged_predecessor: bool = False,
) -> tuple[VerifiedDeploymentStage, dict[str, Any]]:
    if type(request.deployment) is not BridgeTransitionRequest:
        raise DeploymentError("bridge activation request type mismatch")
    bridge = request.deployment
    verified = verify_deployment_stage(request.stage_receipt)
    if verified.value["classification"] != {
        "outcome": "authorized-bridge-transition",
        "reason": "exact-bridge-transition-authorization",
    }:
        raise DeploymentError("bridge activation stage class disagrees")
    if not _rebind_routine_activation_from_stage(
        ActivationRequest(
            deployment=bridge.deployment,
            authorization_raw=request.authorization_raw,
            stage_receipt=request.stage_receipt,
        ),
        precondition,
        verified,
        bridge_stage=True,
    ):
        raise DeploymentError("bridge activation requires complete-control stage")
    manifest_evidence = _bridge_transition_evidence_for_role(
        verified.transition_evidence,
        "manifest",
    )
    authorization_evidence = _bridge_transition_evidence_for_role(
        verified.transition_evidence,
        "authorization",
    )
    manifest = _parse_bridge_canonical_json(
        manifest_evidence.raw,
        "bridge activation staged manifest",
    )
    transition = _parse_bridge_transition_authorization_document(
        authorization_evidence.raw
    )
    _validate_exact_bridge_predecessor(
        precondition,
        staged_predecessor=staged_predecessor,
    )
    _validate_bridge_manifest_history(precondition, manifest)
    _validate_bridge_endpoint_projection(
        bridge.endpoint_projection_raw,
        bridge,
        precondition,
    )
    deployment_artifact = _staged_artifact_for_role(
        verified.artifacts,
        "deployment-receipt",
    )
    deployment = _parse_canonical_json(
        deployment_artifact.raw,
        "bridge activation candidate deployment receipt",
    )
    migration = deployment["migration"]
    core = {
        key: _thaw(value)
        for key, value in deployment.items()
        if key not in {"migration", "content_sha256"}
    }
    if (
        transition["execution_class"] != bridge.execution_class
        or transition["canonical_root"] != str(precondition.canonical_root)
        or transition["staging_root"] != str(verified.path.parent)
        or transition["effective_uid"] != os.geteuid()
        or transition["plan_sha256"] != verified.value["plan_sha256"]
        or transition["maintenance_transaction_sha256"]
        != verified.value["maintenance_transaction_sha256"]
        or transition["deployment_authorization_sha256"]
        != hashlib.sha256(request.authorization_raw).hexdigest()
        or transition["expected_active_receipt_core_sha256"] != _digest(core)
        or transition["bridge_identity_sha256"]
        != manifest["bridge_history"]["bridge_identity_sha256"]
        or transition["release_manifest_sha256"]
        != hashlib.sha256(manifest_evidence.raw).hexdigest()
        or transition["endpoint_projection_sha256"]
        != hashlib.sha256(bridge.endpoint_projection_raw).hexdigest()
        or migration
        != {
            "schema_version": 1,
            "contract": BRIDGE_MIGRATION_PROJECTION_CONTRACT,
            "edge": {"from": "freeze5", "to": "tw4", "via": "bridge"},
            "purpose": "bridge-transition",
            **_bridge_transition_activation_projection(
                transition,
                authorization_evidence.raw,
            ),
        }
    ):
        raise DeploymentError("bridge activation authority disagrees")
    return verified, _bridge_transition_activation_projection(
        transition,
        authorization_evidence.raw,
    )


def _routine_activation_intent(
    prepared: PreparedDeployment,
    request: ActivationRequest,
    verified: VerifiedDeploymentStage,
) -> tuple[dict[str, Any], str]:
    return _routine_activation_intent_from_stage(
        prepared.plan.precondition,
        request,
        verified,
    )


def _control_maintenance_activation_intent(
    prepared: PreparedDeployment,
    request: ActivationRequest,
    verified: VerifiedDeploymentStage,
) -> tuple[dict[str, Any], str]:
    if type(prepared.plan) is not ControlSetDeploymentPlan:
        raise DeploymentError("control maintenance activation plan type mismatch")
    return _control_maintenance_activation_intent_from_stage(
        prepared.plan.precondition,
        request,
        verified,
    )


def _manual_rollback_activation_intent(
    prepared: PreparedRollbackTo,
    request: ActivationRequest,
    verified: VerifiedDeploymentStage,
) -> tuple[dict[str, Any], str]:
    intent, _ = _control_maintenance_activation_intent_from_stage(
        prepared.plan.precondition,
        request,
        verified,
    )
    target = prepared.plan.target_authority
    preimage = {
        **_thaw(intent["preimage"]),
        "manual_target": {
            "receipt_sha256": target.receipt_sha256,
            "active_sha256": hashlib.sha256(target.active_raw).hexdigest(),
            "successor_receipt_sha256": target.successor_receipt_sha256,
            "successor_rollback_sha256": target.successor_rollback_sha256,
            "authority_sha256": target.authority_sha256,
        },
    }
    identity = {
        "contract": ACTIVATION_INTENT_CONTRACT,
        "transaction_class": "manual-exact-target-rollback",
        "canonical_root": intent["canonical_root"],
        "effective_uid": intent["effective_uid"],
        "activation_lock": _thaw(intent["activation_lock"]),
        "outer_maintenance_transaction_sha256": intent[
            "outer_maintenance_transaction_sha256"
        ],
        "stage": _thaw(intent["stage"]),
        "prior": _thaw(intent["prior"]),
        "candidate": _thaw(intent["candidate"]),
        "rollback_authority": _thaw(intent["rollback_authority"]),
        "preimage": preimage,
    }
    transaction_id = _digest(identity)
    return {
        "transaction_id": transaction_id,
        **{key: value for key, value in identity.items() if key != "contract"},
        "candidate_receipt_sha256": intent["candidate_receipt_sha256"],
    }, transaction_id


def _control_maintenance_activation_intent_from_stage(
    precondition: ActiveDeploymentPrecondition,
    request: ActivationRequest,
    verified: VerifiedDeploymentStage,
    *,
    bridge_transition: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    deployment_alias = _staged_artifact_for_role(
        verified.artifacts,
        "deployment-alias",
    )
    deployment_receipt = _staged_artifact_for_role(
        verified.artifacts,
        "deployment-receipt",
    )
    active_record = _staged_artifact_for_role(
        verified.artifacts,
        "active-record",
    )
    rollback_receipt = _staged_artifact_for_role(
        verified.artifacts,
        "rollback-receipt",
    )
    deployment_value = _parse_canonical_json(
        deployment_receipt.raw,
        "control maintenance candidate deployment receipt",
    )
    rollback_value = _parse_canonical_json(
        rollback_receipt.raw,
        "control maintenance rollback receipt",
    )
    control_preimage = _thaw(rollback_value["control_preimage"])
    selector_preimage = _thaw(rollback_value["selector_preimage"])
    if [item["role"] for item in control_preimage] != list(CONTROL_PREIMAGE_ROLES) or [
        item["role"] for item in selector_preimage
    ] != ["active-record", "deployment-alias"]:
        raise DeploymentError("control maintenance preimage order disagrees")
    ordered_preimage = [
        *control_preimage[:-1],
        *selector_preimage,
        control_preimage[-1],
    ]
    if [item["role"] for item in ordered_preimage] != list(
        CONTROL_MAINTENANCE_REPLACEMENT_ROLES
    ):
        raise DeploymentError("control maintenance replacement order disagrees")
    candidate_receipt_sha256 = hashlib.sha256(deployment_receipt.raw).hexdigest()
    candidate = {
        "state": "active",
        "deployment_receipt": _activation_file_binding(deployment_alias),
        "active_record": _activation_file_binding(active_record),
        "control_set": _thaw(deployment_value["control_set"]),
        "smoke": _thaw(deployment_value["smoke"]),
    }
    stage = {
        "receipt_path": str(verified.path),
        "receipt_sha256": hashlib.sha256(verified.raw).hexdigest(),
        "plan_sha256": verified.value["plan_sha256"],
        "authorization_sha256": hashlib.sha256(request.authorization_raw).hexdigest(),
        "maintenance_transaction_sha256": verified.value[
            "maintenance_transaction_sha256"
        ],
    }
    rollback_sha256 = hashlib.sha256(rollback_receipt.raw).hexdigest()
    rollback_authority = {
        "receipt_path": str(rollback_receipt.installed_path),
        "receipt_sha256": rollback_sha256,
        "target_state": "active",
    }
    preimage = {
        "manifest_path": str(rollback_receipt.installed_path),
        "manifest_sha256": rollback_sha256,
        "artifacts": ordered_preimage,
        "external_dependencies": _thaw(rollback_value["external_dependencies"]),
    }
    identity = {
        "contract": ACTIVATION_INTENT_CONTRACT,
        "transaction_class": "control-set-maintenance",
        "canonical_root": str(precondition.canonical_root),
        "effective_uid": os.geteuid(),
        "activation_lock": _thaw(precondition.activation_lock),
        "outer_maintenance_transaction_sha256": verified.value[
            "maintenance_transaction_sha256"
        ],
        "stage": stage,
        "prior": _thaw(rollback_value["prior_activation_unit"]),
        "candidate": candidate,
        "rollback_authority": rollback_authority,
        "preimage": preimage,
    }
    if bridge_transition is not None:
        identity["bridge_transition"] = _thaw(bridge_transition)
    transaction_id = _digest(identity)
    return {
        "transaction_id": transaction_id,
        **{key: value for key, value in identity.items() if key != "contract"},
        "candidate_receipt_sha256": candidate_receipt_sha256,
    }, transaction_id


def _routine_activation_intent_from_stage(
    precondition: ActiveDeploymentPrecondition,
    request: ActivationRequest,
    verified: VerifiedDeploymentStage,
) -> tuple[dict[str, Any], str]:
    deployment_alias = _staged_artifact_for_role(
        verified.artifacts,
        "deployment-alias",
    )
    deployment_receipt = _staged_artifact_for_role(
        verified.artifacts,
        "deployment-receipt",
    )
    active_record = _staged_artifact_for_role(
        verified.artifacts,
        "active-record",
    )
    rollback_receipt = _staged_artifact_for_role(
        verified.artifacts,
        "rollback-receipt",
    )
    deployment_value = _parse_canonical_json(
        deployment_receipt.raw,
        "routine activation candidate deployment receipt",
    )
    rollback_value = _parse_canonical_json(
        rollback_receipt.raw,
        "routine activation rollback receipt",
    )
    candidate_receipt_sha256 = hashlib.sha256(deployment_receipt.raw).hexdigest()
    candidate = {
        "state": "active",
        "deployment_receipt": _activation_file_binding(deployment_alias),
        "active_record": _activation_file_binding(active_record),
        "control_set": _thaw(deployment_value["control_set"]),
        "smoke": _thaw(deployment_value["smoke"]),
    }
    stage = {
        "receipt_path": str(verified.path),
        "receipt_sha256": hashlib.sha256(verified.raw).hexdigest(),
        "plan_sha256": verified.value["plan_sha256"],
        "authorization_sha256": hashlib.sha256(request.authorization_raw).hexdigest(),
        "maintenance_transaction_sha256": verified.value[
            "maintenance_transaction_sha256"
        ],
    }
    rollback_sha256 = hashlib.sha256(rollback_receipt.raw).hexdigest()
    rollback_authority = {
        "receipt_path": str(rollback_receipt.installed_path),
        "receipt_sha256": rollback_sha256,
        "target_state": "active",
    }
    preimage = {
        "manifest_path": str(rollback_receipt.installed_path),
        "manifest_sha256": rollback_sha256,
        "artifacts": _thaw(rollback_value["selector_preimage"]),
        "external_dependencies": _thaw(rollback_value["external_dependencies"]),
    }
    identity = {
        "contract": ACTIVATION_INTENT_CONTRACT,
        "transaction_class": "routine-payload",
        "canonical_root": str(precondition.canonical_root),
        "effective_uid": os.geteuid(),
        "activation_lock": _thaw(precondition.activation_lock),
        "outer_maintenance_transaction_sha256": (
            verified.value["maintenance_transaction_sha256"]
        ),
        "stage": stage,
        "prior": _thaw(rollback_value["prior_activation_unit"]),
        "candidate": candidate,
        "rollback_authority": rollback_authority,
        "preimage": preimage,
    }
    transaction_id = _digest(identity)
    return {
        "transaction_id": transaction_id,
        **{key: value for key, value in identity.items() if key != "contract"},
        "candidate_receipt_sha256": candidate_receipt_sha256,
    }, transaction_id


def _activation_intent(
    prepared: PreparedFirstInstall,
    request: ActivationRequest,
    verified: VerifiedDeploymentStage,
) -> tuple[dict[str, Any], str]:
    plan = prepared.plan
    canonical_root = plan.precondition.canonical_root
    deployment_alias = _staged_artifact_for_role(
        verified.artifacts,
        "deployment-alias",
    )
    deployment_receipt = _staged_artifact_for_role(
        verified.artifacts,
        "deployment-receipt",
    )
    active_record = _staged_artifact_for_role(
        verified.artifacts,
        "active-record",
    )
    rollback_receipt = _staged_artifact_for_role(
        verified.artifacts,
        "rollback-receipt",
    )
    deployment_value = _parse_canonical_json(
        deployment_receipt.raw,
        "activation candidate deployment receipt",
    )
    candidate_receipt_sha256 = hashlib.sha256(deployment_receipt.raw).hexdigest()
    candidate = {
        "state": "active",
        "deployment_receipt": _activation_file_binding(deployment_alias),
        "active_record": _activation_file_binding(active_record),
        "control_set": {
            role: _activation_file_binding(
                _staged_artifact_for_role(verified.artifacts, role)
            )
            for role in ("shim", "client", "launcher", "controller", "policy")
        },
        "smoke": _thaw(deployment_value["smoke"]),
    }
    prior = {
        "state": "absent",
        "deployment_receipt": None,
        "active_record": None,
        "control_set": None,
        "smoke": None,
    }
    stage = {
        "receipt_path": str(verified.path),
        "receipt_sha256": hashlib.sha256(verified.raw).hexdigest(),
        "plan_sha256": verified.value["plan_sha256"],
        "authorization_sha256": hashlib.sha256(request.authorization_raw).hexdigest(),
        "maintenance_transaction_sha256": verified.value[
            "maintenance_transaction_sha256"
        ],
    }
    rollback_authority = {
        "receipt_path": str(rollback_receipt.installed_path),
        "receipt_sha256": hashlib.sha256(rollback_receipt.raw).hexdigest(),
        "target_state": "absent",
    }
    preimage = {
        "manifest_path": str(rollback_receipt.installed_path),
        "manifest_sha256": hashlib.sha256(rollback_receipt.raw).hexdigest(),
        "artifacts": [],
        "external_dependencies": [],
    }
    identity = {
        "contract": ACTIVATION_INTENT_CONTRACT,
        "transaction_class": "control-set-maintenance",
        "canonical_root": str(canonical_root),
        "effective_uid": os.geteuid(),
        "activation_lock": _thaw(plan.precondition.activation_lock),
        "outer_maintenance_transaction_sha256": (plan.maintenance_transaction_sha256),
        "stage": stage,
        "prior": prior,
        "candidate": candidate,
        "rollback_authority": rollback_authority,
        "preimage": preimage,
    }
    transaction_id = _digest(identity)
    return {
        "transaction_id": transaction_id,
        **{key: value for key, value in identity.items() if key != "contract"},
        "candidate_receipt_sha256": candidate_receipt_sha256,
    }, transaction_id


def _activation_journal_generation(
    intent: Mapping[str, Any],
    previous: _ActivationJournal | None,
    *,
    phase: str,
    pending_step: Mapping[str, Any] | None = None,
    smoke_handoff: Mapping[str, Any] | None = None,
    candidate_smoke_acceptance: Mapping[str, Any] | None = None,
    rollback_smoke_acceptance: Mapping[str, Any] | None = None,
    terminal_result: Mapping[str, Any] | None = None,
) -> _ActivationJournal:
    sequence = 1 if previous is None else previous.value["sequence"] + 1
    previous_sha256 = (
        None if previous is None else hashlib.sha256(previous.raw).hexdigest()
    )
    unsigned = {
        "schema_version": 1,
        "contract": ACTIVATION_TRANSACTION_CONTRACT,
        "transaction_id": intent["transaction_id"],
        "sequence": sequence,
        "previous_journal_sha256": previous_sha256,
        "transaction_class": intent["transaction_class"],
        "phase": phase,
        "canonical_root": intent["canonical_root"],
        "effective_uid": intent["effective_uid"],
        "activation_lock": _thaw(intent["activation_lock"]),
        "outer_maintenance_transaction_sha256": intent[
            "outer_maintenance_transaction_sha256"
        ],
        "stage": _thaw(intent["stage"]),
        "prior": _thaw(intent["prior"]),
        "candidate": _thaw(intent["candidate"]),
        "rollback_authority": _thaw(intent["rollback_authority"]),
        "preimage": _thaw(intent["preimage"]),
        "pending_step": _thaw(pending_step),
        "smoke_handoff": _thaw(smoke_handoff),
        "candidate_smoke_acceptance": _thaw(candidate_smoke_acceptance),
        "rollback_smoke_acceptance": _thaw(rollback_smoke_acceptance),
        "terminal_result": _thaw(terminal_result),
    }
    if "bridge_transition" in intent:
        unsigned["bridge_transition"] = _thaw(intent["bridge_transition"])
    value = {**unsigned, "content_sha256": _digest(unsigned)}
    raw = _canonical_document(value)
    return _ActivationJournal(_freeze_json(value), raw)


def _activation_journal_file_binding(
    value: object,
    *,
    path: Path,
    owner: int,
    mode: int,
    label: str,
) -> dict[str, Any]:
    binding = _exact(
        value,
        {"path", "length", "sha256", "owner", "mode"},
        label,
    )
    bound_path = _normalized_absolute_path(
        Path(_text(binding["path"], f"{label}.path")),
        f"{label}.path",
    )
    if (
        bound_path != path
        or binding["path"] != str(path)
        or _nonnegative_integer(binding["length"], f"{label}.length")
        > MAX_CANDIDATE_TREE_FILE_BYTES
        or _sha256(binding["sha256"], f"{label}.sha256") != binding["sha256"]
        or _nonnegative_integer(binding["owner"], f"{label}.owner") != owner
        or _nonnegative_integer(binding["mode"], f"{label}.mode") != mode
    ):
        raise DeploymentError(f"{label} binding disagrees")
    return binding


def _activation_journal_smoke_shape(
    value: object,
    root: Path,
    owner: int,
    active_record_sha256: str,
    label: str,
) -> dict[str, Any]:
    smoke = _exact(
        value,
        {
            "bundle",
            "trust_context",
            "producer",
            "validator",
            "expected_projection",
            "expected_anchor",
            "expected_envelope_sha256",
        },
        label,
    )
    bundle = _exact(
        smoke["bundle"],
        {"path", "sha256", "manifest"},
        f"{label}.bundle",
    )
    bundle_path = _normalized_absolute_path(
        Path(_text(bundle["path"], f"{label}.bundle.path")),
        f"{label}.bundle.path",
    )
    if bundle_path != root / "smoke" / "bundle" or bundle["path"] != str(bundle_path):
        raise DeploymentError(f"{label} bundle path disagrees")
    bundle_sha256 = _sha256(bundle["sha256"], f"{label}.bundle.sha256")
    _activation_journal_file_binding(
        bundle["manifest"],
        path=bundle_path / "manifest.json",
        owner=owner,
        mode=0o600,
        label=f"{label}.bundle.manifest",
    )
    trust = _exact(
        smoke["trust_context"],
        {"path", "sha256"},
        f"{label}.trust_context",
    )
    trust_path = _normalized_absolute_path(
        Path(_text(trust["path"], f"{label}.trust_context.path")),
        f"{label}.trust_context.path",
    )
    trust_sha256 = _sha256(
        trust["sha256"],
        f"{label}.trust_context.sha256",
    )
    if (
        trust_path.parent != root / "trust" / "contexts"
        or trust_path.name != f"sha256-{trust_sha256}.json"
        or trust["path"] != str(trust_path)
    ):
        raise DeploymentError(f"{label} trust-context path disagrees")
    producer = _exact(
        smoke["producer"],
        {
            "producer_id",
            "contract",
            "implementation_sha256",
            "validator_id",
            "validator_contract",
            "validator_implementation_sha256",
        },
        f"{label}.producer",
    )
    validator = _exact(
        smoke["validator"],
        {"validator_id", "contract", "implementation_sha256"},
        f"{label}.validator",
    )
    for key in ("producer_id", "contract", "validator_id", "validator_contract"):
        _text(producer[key], f"{label}.producer.{key}")
    for key in ("implementation_sha256", "validator_implementation_sha256"):
        _sha256(producer[key], f"{label}.producer.{key}")
    for key in ("validator_id", "contract"):
        _text(validator[key], f"{label}.validator.{key}")
    _sha256(
        validator["implementation_sha256"],
        f"{label}.validator.implementation_sha256",
    )
    if (
        validator["validator_id"] != producer["validator_id"]
        or validator["contract"] != producer["validator_contract"]
        or validator["implementation_sha256"]
        != producer["validator_implementation_sha256"]
    ):
        raise DeploymentError(f"{label} producer and validator disagree")
    projection = _exact(
        smoke["expected_projection"],
        {"schema_version", "contract", "challenge", "accepted"},
        f"{label}.expected_projection",
    )
    if projection != {
        "schema_version": 1,
        "contract": SMOKE_PROJECTION_CONTRACT,
        "challenge": SMOKE_CHALLENGE,
        "accepted": True,
    }:
        raise DeploymentError(f"{label} projection disagrees")
    anchor = _exact(
        smoke["expected_anchor"],
        {
            "contract",
            "generation",
            "active_record_sha256",
            "runtime_contract",
            "interpreter",
            "public_release",
            "runtime_implementation_sha256",
            "trust_context_sha256",
            "bundle_sha256",
            "historical",
        },
        f"{label}.expected_anchor",
    )
    interpreter = _exact(
        anchor["interpreter"],
        {"executable", "implementation", "version"},
        f"{label}.expected_anchor.interpreter",
    )
    _normalized_absolute_path(
        Path(_text(interpreter["executable"], f"{label}.interpreter.executable")),
        f"{label}.interpreter.executable",
    )
    _token(interpreter["implementation"], f"{label}.interpreter.implementation")
    version = _exact(
        interpreter["version"],
        {"major", "minor", "micro"},
        f"{label}.interpreter.version",
    )
    for key in ("major", "minor", "micro"):
        _nonnegative_integer(version[key], f"{label}.interpreter.version.{key}")
    release = _exact(
        anchor["public_release"],
        {"repository", "revision"},
        f"{label}.expected_anchor.public_release",
    )
    _text(release["repository"], f"{label}.public_release.repository")
    _text(release["revision"], f"{label}.public_release.revision")
    runtime_sha256 = _sha256(
        anchor["runtime_implementation_sha256"],
        f"{label}.expected_anchor.runtime_implementation_sha256",
    )
    if (
        anchor["contract"] != COMPLETE_ANCHOR_CONTRACT
        or anchor["generation"] != f"sha256-{runtime_sha256}"
        or anchor["active_record_sha256"] != active_record_sha256
        or anchor["runtime_contract"] != RUNTIME_CONTRACT
        or anchor["trust_context_sha256"] != trust_sha256
        or anchor["bundle_sha256"] != bundle_sha256
        or anchor["historical"] is not False
    ):
        raise DeploymentError(f"{label} anchor disagrees")
    _sha256(
        smoke["expected_envelope_sha256"],
        f"{label}.expected_envelope_sha256",
    )
    return smoke


def _activation_journal_unit_shape(
    value: object,
    root: Path,
    owner: int,
    label: str,
) -> dict[str, Any]:
    unit = _exact(
        value,
        {"state", "deployment_receipt", "active_record", "control_set", "smoke"},
        label,
    )
    if unit["state"] == "absent":
        if any(
            unit[key] is not None
            for key in ("deployment_receipt", "active_record", "control_set", "smoke")
        ):
            raise DeploymentError(f"{label} absent unit is incoherent")
        return unit
    if unit["state"] != "active":
        raise DeploymentError(f"{label}.state is unsupported")
    deployment = _activation_journal_file_binding(
        unit["deployment_receipt"],
        path=root / "deployment.json",
        owner=owner,
        mode=0o600,
        label=f"{label}.deployment_receipt",
    )
    active = _activation_journal_file_binding(
        unit["active_record"],
        path=root / "active.json",
        owner=owner,
        mode=0o600,
        label=f"{label}.active_record",
    )
    controls = _exact(
        unit["control_set"],
        {"shim", "client", "launcher", "controller", "policy"},
        f"{label}.control_set",
    )
    control_paths = {
        "shim": root / "task-witness",
        "client": root / "client" / "task_witness_client.py",
        "launcher": root / "launcher" / "task_witness_launch.py",
        "controller": root / "controller" / "task_witness_deploy.py",
        "policy": root / "controller" / "policy.json",
    }
    for role, path in control_paths.items():
        _activation_journal_file_binding(
            controls[role],
            path=path,
            owner=owner,
            mode=0o600 if role == "policy" else 0o500,
            label=f"{label}.control_set.{role}",
        )
    _activation_journal_smoke_shape(
        unit["smoke"],
        root,
        owner,
        active["sha256"],
        f"{label}.smoke",
    )
    if deployment["sha256"] != unit["deployment_receipt"]["sha256"]:
        raise DeploymentError(f"{label} deployment receipt disagrees")
    return unit


def _activation_journal_acceptance_shape(
    value: object,
    label: str,
    *,
    expected_phase: str = "candidate-smoke",
) -> dict[str, Any]:
    acceptance = _exact(
        value,
        {
            "phase",
            "target_deployment_receipt_sha256",
            "expected_envelope_sha256",
            "accepted_envelope_sha256",
            "exit_status",
            "content_sha256",
        },
        label,
    )
    _content_sha256(acceptance, label)
    if (
        acceptance["phase"] != expected_phase
        or _sha256(
            acceptance["target_deployment_receipt_sha256"],
            f"{label}.target_deployment_receipt_sha256",
        )
        != acceptance["target_deployment_receipt_sha256"]
        or _sha256(
            acceptance["expected_envelope_sha256"],
            f"{label}.expected_envelope_sha256",
        )
        != acceptance["expected_envelope_sha256"]
        or _sha256(
            acceptance["accepted_envelope_sha256"],
            f"{label}.accepted_envelope_sha256",
        )
        != acceptance["accepted_envelope_sha256"]
        or acceptance["exit_status"] != 0
        or type(acceptance["exit_status"]) is not int
    ):
        raise DeploymentError(f"{label} is incoherent")
    return acceptance


def _bridge_transition_activation_projection_shape(
    value: object,
    label: str,
) -> dict[str, Any]:
    execution_class = value.get("execution_class") if isinstance(value, dict) else None
    if execution_class not in {"isolated-rehearsal", "live-migration"}:
        raise DeploymentError(f"{label} execution class is unsupported")
    keys = {
        "execution_class",
        "maintenance_transaction_sha256",
        "deployment_authorization_sha256",
        "transition_authorization_sha256",
        "expected_active_receipt_core_sha256",
        "bridge_identity_sha256",
        "release_manifest_sha256",
        "endpoint_projection_sha256",
    }
    if execution_class == "live-migration":
        keys.add("prior_rehearsal")
    projection = _exact(value, keys, label)
    for name in (
        "maintenance_transaction_sha256",
        "deployment_authorization_sha256",
        "transition_authorization_sha256",
        "expected_active_receipt_core_sha256",
        "bridge_identity_sha256",
        "release_manifest_sha256",
        "endpoint_projection_sha256",
    ):
        _sha256(projection[name], f"{label}.{name}")
    if execution_class == "live-migration":
        _bridge_prior_rehearsal(
            projection["prior_rehearsal"],
            f"{label}.prior_rehearsal",
        )
    return projection


def _parse_routine_activation_journal(raw: bytes) -> _ActivationJournal:
    label = "routine activation transaction journal"
    value = _exact(
        _parse_canonical_json(raw, label),
        {
            "schema_version",
            "contract",
            "transaction_id",
            "sequence",
            "previous_journal_sha256",
            "transaction_class",
            "phase",
            "canonical_root",
            "effective_uid",
            "activation_lock",
            "outer_maintenance_transaction_sha256",
            "stage",
            "prior",
            "candidate",
            "rollback_authority",
            "preimage",
            "pending_step",
            "smoke_handoff",
            "candidate_smoke_acceptance",
            "rollback_smoke_acceptance",
            "terminal_result",
            "content_sha256",
        },
        label,
    )
    _content_sha256(value, label)
    transaction_id = _sha256(value["transaction_id"], f"{label}.transaction_id")
    sequence = _nonnegative_integer(value["sequence"], f"{label}.sequence")
    previous = value["previous_journal_sha256"]
    if previous is not None:
        _sha256(previous, f"{label}.previous_journal_sha256")
    root = _normalized_absolute_path(
        Path(_text(value["canonical_root"], f"{label}.canonical_root")),
        f"{label}.canonical_root",
    )
    owner = _nonnegative_integer(value["effective_uid"], f"{label}.effective_uid")
    phase = _text(value["phase"], f"{label}.phase")
    lock = _exact(
        value["activation_lock"],
        {"path", "device", "inode", "owner", "mode"},
        f"{label}.activation_lock",
    )
    lock_path = _normalized_absolute_path(
        Path(_text(lock["path"], f"{label}.activation_lock.path")),
        f"{label}.activation_lock.path",
    )
    for key in ("device", "inode", "owner", "mode"):
        _nonnegative_integer(lock[key], f"{label}.activation_lock.{key}")
    stage = _exact(
        value["stage"],
        {
            "receipt_path",
            "receipt_sha256",
            "plan_sha256",
            "authorization_sha256",
            "maintenance_transaction_sha256",
        },
        f"{label}.stage",
    )
    stage_path = _normalized_absolute_path(
        Path(_text(stage["receipt_path"], f"{label}.stage.receipt_path")),
        f"{label}.stage.receipt_path",
    )
    for key in (
        "receipt_sha256",
        "plan_sha256",
        "authorization_sha256",
        "maintenance_transaction_sha256",
    ):
        _sha256(stage[key], f"{label}.stage.{key}")
    prior = _activation_journal_unit_shape(
        value["prior"], root, owner, f"{label}.prior"
    )
    candidate = _activation_journal_unit_shape(
        value["candidate"],
        root,
        owner,
        f"{label}.candidate",
    )
    rollback = _exact(
        value["rollback_authority"],
        {"receipt_path", "receipt_sha256", "target_state"},
        f"{label}.rollback_authority",
    )
    rollback_sha256 = _sha256(
        rollback["receipt_sha256"],
        f"{label}.rollback_authority.receipt_sha256",
    )
    rollback_path = _normalized_absolute_path(
        Path(_text(rollback["receipt_path"], f"{label}.rollback_authority.path")),
        f"{label}.rollback_authority.path",
    )
    preimage = _exact(
        value["preimage"],
        {"manifest_path", "manifest_sha256", "artifacts", "external_dependencies"},
        f"{label}.preimage",
    )
    preimage_path = _normalized_absolute_path(
        Path(_text(preimage["manifest_path"], f"{label}.preimage.manifest_path")),
        f"{label}.preimage.manifest_path",
    )
    phases = {
        "prepared",
        "frozen",
        "drained",
        "additive-installing",
        "selector-switching",
        "candidate-smoke",
        "candidate-accepted",
        "prior-restoring",
        "rollback-smoke",
        "prior-accepted",
        "rollback-cleaning",
        "terminal",
    }
    if (
        value["schema_version"] != 1
        or type(value["schema_version"]) is not int
        or value["contract"] != ACTIVATION_TRANSACTION_CONTRACT
        or sequence < 1
        or (sequence == 1) != (previous is None)
        or value["transaction_class"] != "routine-payload"
        or phase not in phases
        or value["canonical_root"] != str(root)
        or owner != os.geteuid()
        or lock_path != root / "activation.lock"
        or lock["path"] != str(lock_path)
        or lock["owner"] != owner
        or lock["mode"] != 0o600
        or stage["receipt_path"] != str(stage_path)
        or stage["maintenance_transaction_sha256"]
        != value["outer_maintenance_transaction_sha256"]
        or _sha256(
            value["outer_maintenance_transaction_sha256"],
            f"{label}.outer_maintenance_transaction_sha256",
        )
        != value["outer_maintenance_transaction_sha256"]
        or prior["state"] != "active"
        or candidate["state"] != "active"
        or rollback_path != root / "receipts" / f"sha256-{rollback_sha256}.json"
        or rollback["receipt_path"] != str(rollback_path)
        or rollback["target_state"] != "active"
        or preimage_path != rollback_path
        or preimage["manifest_path"] != str(preimage_path)
        or preimage["manifest_sha256"] != rollback_sha256
    ):
        raise DeploymentError("routine activation journal contract mismatch")
    artifacts = preimage["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) != 2:
        raise DeploymentError("routine activation preimage artifacts are invalid")
    for index, (role, expected) in enumerate(
        (
            ("active-record", prior["active_record"]),
            ("deployment-alias", prior["deployment_receipt"]),
        )
    ):
        artifact = _exact(
            artifacts[index],
            {"role", "staged", "installed"},
            f"{label}.preimage.artifacts[{index}]",
        )
        staged_binding = _receipt_file_binding(
            artifact["staged"],
            f"{label}.preimage.artifacts[{index}].staged",
        )
        installed_binding = _receipt_file_binding(
            artifact["installed"],
            f"{label}.preimage.artifacts[{index}].installed",
        )
        if (
            artifact["role"] != role
            or installed_binding != expected
            or staged_binding["owner"] != owner
            or staged_binding["mode"] != 0o600
        ):
            raise DeploymentError("routine activation selector preimage disagrees")
    external = _exact(
        preimage["external_dependencies"],
        {"interpreter", "runtime_closure", "process_profile", "receipt_parser"},
        f"{label}.preimage.external_dependencies",
    )
    _exact(
        external["receipt_parser"],
        {
            "deployment_receipt_contract",
            "rollback_receipt_contract",
            "controller",
            "client",
        },
        f"{label}.preimage.external_dependencies.receipt_parser",
    )
    identity = {
        "contract": ACTIVATION_INTENT_CONTRACT,
        "transaction_class": value["transaction_class"],
        "canonical_root": value["canonical_root"],
        "effective_uid": owner,
        "activation_lock": lock,
        "outer_maintenance_transaction_sha256": value[
            "outer_maintenance_transaction_sha256"
        ],
        "stage": stage,
        "prior": prior,
        "candidate": candidate,
        "rollback_authority": rollback,
        "preimage": preimage,
    }
    if transaction_id != _digest(identity):
        raise DeploymentError("routine activation journal intent identity mismatch")
    pending = value["pending_step"]
    if pending is not None:
        pending = _exact(
            pending,
            {"operation", "index", "role"},
            f"{label}.pending_step",
        )
        operation = _text(pending["operation"], f"{label}.pending_step.operation")
        if operation not in {
            "install",
            "replace-selector",
            "remove-artifact",
            "remove-directory",
        }:
            raise DeploymentError("routine activation pending operation is invalid")
        _nonnegative_integer(pending["index"], f"{label}.pending_step.index")
        _text(pending["role"], f"{label}.pending_step.role")
    handoff = value["smoke_handoff"]
    if handoff is not None:
        handoff = _exact(
            handoff,
            {
                "target_deployment_receipt_sha256",
                "smoke_bundle_sha256",
                "smoke_trust_context_sha256",
            },
            f"{label}.smoke_handoff",
        )
        for key in handoff:
            _sha256(handoff[key], f"{label}.smoke_handoff.{key}")
    candidate_acceptance = value["candidate_smoke_acceptance"]
    if candidate_acceptance is not None:
        candidate_acceptance = _activation_journal_acceptance_shape(
            candidate_acceptance,
            f"{label}.candidate_smoke_acceptance",
        )
    rollback_acceptance = value["rollback_smoke_acceptance"]
    if rollback_acceptance is not None:
        rollback_acceptance = _activation_journal_acceptance_shape(
            rollback_acceptance,
            f"{label}.rollback_smoke_acceptance",
            expected_phase="rollback-smoke",
        )
    terminal = value["terminal_result"]
    if terminal is not None:
        terminal = _exact(
            terminal,
            {
                "outcome",
                "candidate_receipt_sha256",
                "active_receipt_sha256",
                "accepted_envelope_sha256",
                "failure_class",
            },
            f"{label}.terminal_result",
        )
        _sha256(
            terminal["candidate_receipt_sha256"],
            f"{label}.terminal_result.candidate_receipt_sha256",
        )
    candidate_sha256 = candidate["deployment_receipt"]["sha256"]
    prior_sha256 = prior["deployment_receipt"]["sha256"]
    candidate_smoke = candidate["smoke"]
    prior_smoke = prior["smoke"]
    candidate_handoff = {
        "target_deployment_receipt_sha256": candidate_sha256,
        "smoke_bundle_sha256": candidate_smoke["bundle"]["sha256"],
        "smoke_trust_context_sha256": candidate_smoke["trust_context"]["sha256"],
    }
    rollback_handoff = {
        "target_deployment_receipt_sha256": prior_sha256,
        "smoke_bundle_sha256": prior_smoke["bundle"]["sha256"],
        "smoke_trust_context_sha256": prior_smoke["trust_context"]["sha256"],
    }
    if candidate_acceptance is not None and (
        candidate_acceptance["target_deployment_receipt_sha256"] != candidate_sha256
        or candidate_acceptance["expected_envelope_sha256"]
        != candidate_smoke["expected_envelope_sha256"]
        or candidate_acceptance["accepted_envelope_sha256"]
        != candidate_smoke["expected_envelope_sha256"]
    ):
        raise DeploymentError("routine candidate smoke acceptance disagrees")
    if rollback_acceptance is not None and (
        rollback_acceptance["target_deployment_receipt_sha256"] != prior_sha256
        or rollback_acceptance["expected_envelope_sha256"]
        != prior_smoke["expected_envelope_sha256"]
        or rollback_acceptance["accepted_envelope_sha256"]
        != prior_smoke["expected_envelope_sha256"]
    ):
        raise DeploymentError("routine rollback smoke acceptance disagrees")
    if phase in {"prepared", "frozen", "drained"}:
        coherent = all(
            item is None
            for item in (
                pending,
                handoff,
                candidate_acceptance,
                rollback_acceptance,
                terminal,
            )
        )
    elif phase == "additive-installing":
        coherent = (
            pending is not None
            and pending["operation"] == "install"
            and handoff is None
            and candidate_acceptance is None
            and rollback_acceptance is None
            and terminal is None
        )
    elif phase in {"selector-switching", "prior-restoring"}:
        coherent = (
            pending is not None
            and pending["operation"] == "replace-selector"
            and handoff is None
            and candidate_acceptance is None
            and rollback_acceptance is None
            and terminal is None
        )
    elif phase == "candidate-smoke":
        coherent = (
            pending is None
            and handoff == candidate_handoff
            and rollback_acceptance is None
            and terminal is None
        )
    elif phase == "candidate-accepted":
        coherent = (
            pending is None
            and handoff is None
            and candidate_acceptance is not None
            and rollback_acceptance is None
            and terminal is None
        )
    elif phase == "rollback-smoke":
        coherent = (
            pending is None
            and handoff == rollback_handoff
            and candidate_acceptance is None
            and terminal is None
        )
    elif phase == "prior-accepted":
        coherent = (
            pending is None
            and handoff is None
            and candidate_acceptance is None
            and rollback_acceptance is not None
            and terminal is None
        )
    elif phase == "rollback-cleaning":
        coherent = (
            pending is not None
            and pending["operation"] in {"remove-artifact", "remove-directory"}
            and handoff is None
            and candidate_acceptance is None
            and rollback_acceptance is not None
            and terminal is None
        )
    else:
        coherent = pending is None and handoff is None and terminal is not None
    if not coherent:
        raise DeploymentError("routine activation journal phase is incoherent")
    if terminal is not None:
        if terminal["candidate_receipt_sha256"] != candidate_sha256:
            raise DeploymentError("routine terminal candidate binding disagrees")
        if terminal["outcome"] == "candidate-active":
            valid = (
                candidate_acceptance is not None
                and rollback_acceptance is None
                and terminal["active_receipt_sha256"] == candidate_sha256
                and terminal["accepted_envelope_sha256"]
                == candidate_smoke["expected_envelope_sha256"]
                and terminal["failure_class"] is None
            )
        elif terminal["outcome"] == "restored-prior":
            valid = (
                candidate_acceptance is None
                and rollback_acceptance is not None
                and terminal["active_receipt_sha256"] == prior_sha256
                and terminal["accepted_envelope_sha256"]
                == prior_smoke["expected_envelope_sha256"]
                and terminal["failure_class"] == "candidate-smoke-rejected"
            )
        elif terminal["outcome"] == "recovery-required":
            valid = (
                candidate_acceptance is None
                and rollback_acceptance is None
                and terminal["active_receipt_sha256"] is None
                and terminal["accepted_envelope_sha256"] is None
                and terminal["failure_class"] == "rollback-smoke-rejected"
            )
        else:
            valid = False
        if not valid:
            raise DeploymentError("routine activation terminal is incoherent")
    return _ActivationJournal(_freeze_json(value), raw)


def _parse_control_maintenance_activation_journal(
    raw: bytes,
    *,
    expected_transaction_class: str = "control-set-maintenance",
) -> _ActivationJournal:
    manual = expected_transaction_class == "manual-exact-target-rollback"
    if expected_transaction_class not in {
        "control-set-maintenance",
        "manual-exact-target-rollback",
    }:
        raise DeploymentError("control transaction class is unsupported")
    label = (
        "manual exact-target rollback journal"
        if manual
        else "control maintenance activation journal"
    )
    parsed_value = _parse_canonical_json(raw, label)
    keys = {
        "schema_version",
        "contract",
        "transaction_id",
        "sequence",
        "previous_journal_sha256",
        "transaction_class",
        "phase",
        "canonical_root",
        "effective_uid",
        "activation_lock",
        "outer_maintenance_transaction_sha256",
        "stage",
        "prior",
        "candidate",
        "rollback_authority",
        "preimage",
        "pending_step",
        "smoke_handoff",
        "candidate_smoke_acceptance",
        "rollback_smoke_acceptance",
        "terminal_result",
        "content_sha256",
    }
    if isinstance(parsed_value, dict) and "bridge_transition" in parsed_value:
        if manual:
            raise DeploymentError("manual rollback bridge transition is forbidden")
        keys.add("bridge_transition")
    value = _exact(
        parsed_value,
        keys,
        label,
    )
    _content_sha256(value, label)
    transaction_id = _sha256(
        value["transaction_id"],
        f"{label}.transaction_id",
    )
    sequence = _nonnegative_integer(value["sequence"], f"{label}.sequence")
    previous = value["previous_journal_sha256"]
    if previous is not None:
        _sha256(previous, f"{label}.previous_journal_sha256")
    root = _normalized_absolute_path(
        Path(_text(value["canonical_root"], f"{label}.canonical_root")),
        f"{label}.canonical_root",
    )
    owner = _nonnegative_integer(value["effective_uid"], f"{label}.effective_uid")
    lock = _exact(
        value["activation_lock"],
        {"path", "device", "inode", "owner", "mode"},
        f"{label}.activation_lock",
    )
    lock_path = _normalized_absolute_path(
        Path(_text(lock["path"], f"{label}.activation_lock.path")),
        f"{label}.activation_lock.path",
    )
    for key in ("device", "inode", "owner", "mode"):
        _nonnegative_integer(lock[key], f"{label}.activation_lock.{key}")
    stage = _exact(
        value["stage"],
        {
            "receipt_path",
            "receipt_sha256",
            "plan_sha256",
            "authorization_sha256",
            "maintenance_transaction_sha256",
        },
        f"{label}.stage",
    )
    stage_path = _normalized_absolute_path(
        Path(_text(stage["receipt_path"], f"{label}.stage.receipt_path")),
        f"{label}.stage.receipt_path",
    )
    for key in (
        "receipt_sha256",
        "plan_sha256",
        "authorization_sha256",
        "maintenance_transaction_sha256",
    ):
        _sha256(stage[key], f"{label}.stage.{key}")
    prior = _activation_journal_unit_shape(
        value["prior"],
        root,
        owner,
        f"{label}.prior",
    )
    candidate = _activation_journal_unit_shape(
        value["candidate"],
        root,
        owner,
        f"{label}.candidate",
    )
    rollback = _exact(
        value["rollback_authority"],
        {"receipt_path", "receipt_sha256", "target_state"},
        f"{label}.rollback_authority",
    )
    rollback_sha256 = _sha256(
        rollback["receipt_sha256"],
        f"{label}.rollback_authority.receipt_sha256",
    )
    rollback_path = _normalized_absolute_path(
        Path(
            _text(
                rollback["receipt_path"],
                f"{label}.rollback_authority.receipt_path",
            )
        ),
        f"{label}.rollback_authority.receipt_path",
    )
    preimage_keys = {
        "manifest_path",
        "manifest_sha256",
        "artifacts",
        "external_dependencies",
    }
    if manual:
        preimage_keys.add("manual_target")
    preimage = _exact(
        value["preimage"],
        preimage_keys,
        f"{label}.preimage",
    )
    preimage_path = _normalized_absolute_path(
        Path(_text(preimage["manifest_path"], f"{label}.preimage.manifest_path")),
        f"{label}.preimage.manifest_path",
    )
    phase = _text(value["phase"], f"{label}.phase")
    bridge_transition = None
    if "bridge_transition" in value:
        bridge_transition = _bridge_transition_activation_projection_shape(
            value["bridge_transition"],
            f"{label}.bridge_transition",
        )
    phases = {
        "prepared",
        "frozen",
        "drained",
        "additive-installing",
        "control-switching",
        "candidate-smoke",
        "candidate-accepted",
        "prior-restoring",
        "rollback-smoke",
        "prior-accepted",
        "rollback-cleaning",
        "terminal",
    }
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["contract"] != ACTIVATION_TRANSACTION_CONTRACT
        or sequence < 1
        or (sequence == 1) != (previous is None)
        or value["transaction_class"] != expected_transaction_class
        or phase not in phases
        or value["canonical_root"] != str(root)
        or owner != os.geteuid()
        or lock_path != root / "activation.lock"
        or lock["path"] != str(lock_path)
        or lock["owner"] != owner
        or lock["mode"] != 0o600
        or stage["receipt_path"] != str(stage_path)
        or stage["maintenance_transaction_sha256"]
        != value["outer_maintenance_transaction_sha256"]
        or _sha256(
            value["outer_maintenance_transaction_sha256"],
            f"{label}.outer_maintenance_transaction_sha256",
        )
        != value["outer_maintenance_transaction_sha256"]
        or prior["state"] != "active"
        or candidate["state"] != "active"
        or rollback_path != root / "receipts" / f"sha256-{rollback_sha256}.json"
        or rollback["receipt_path"] != str(rollback_path)
        or rollback["target_state"] != "active"
        or preimage_path != rollback_path
        or preimage["manifest_path"] != str(preimage_path)
        or preimage["manifest_sha256"] != rollback_sha256
        or (
            bridge_transition is not None
            and (
                bridge_transition["maintenance_transaction_sha256"]
                != value["outer_maintenance_transaction_sha256"]
                or bridge_transition["deployment_authorization_sha256"]
                != stage["authorization_sha256"]
            )
        )
    ):
        raise DeploymentError("control maintenance activation contract mismatch")
    if manual:
        target = _exact(
            preimage["manual_target"],
            {
                "receipt_sha256",
                "active_sha256",
                "successor_receipt_sha256",
                "successor_rollback_sha256",
                "authority_sha256",
            },
            f"{label}.preimage.manual_target",
        )
        for key in target:
            _sha256(target[key], f"{label}.preimage.manual_target.{key}")
    artifacts = preimage["artifacts"]
    prior_controls = {
        **prior["control_set"],
        "smoke-bundle-manifest": prior["smoke"]["bundle"]["manifest"],
        "active-record": prior["active_record"],
        "deployment-alias": prior["deployment_receipt"],
    }
    if not isinstance(artifacts, list) or len(artifacts) != len(
        CONTROL_MAINTENANCE_REPLACEMENT_ROLES
    ):
        raise DeploymentError("control maintenance preimage inventory disagrees")
    for index, role in enumerate(CONTROL_MAINTENANCE_REPLACEMENT_ROLES):
        artifact = _exact(
            artifacts[index],
            {"role", "staged", "installed"},
            f"{label}.preimage.artifacts[{index}]",
        )
        staged_binding = _receipt_file_binding(
            artifact["staged"],
            f"{label}.preimage.artifacts[{index}].staged",
        )
        installed_binding = _receipt_file_binding(
            artifact["installed"],
            f"{label}.preimage.artifacts[{index}].installed",
        )
        if (
            artifact["role"] != role
            or installed_binding != prior_controls[role]
            or staged_binding["owner"] != owner
            or staged_binding["mode"] != 0o600
            or any(
                staged_binding[key] != installed_binding[key]
                for key in ("length", "sha256", "owner")
            )
        ):
            raise DeploymentError("control maintenance preimage binding disagrees")
    external = _exact(
        preimage["external_dependencies"],
        {"interpreter", "runtime_closure", "process_profile", "receipt_parser"},
        f"{label}.preimage.external_dependencies",
    )
    _exact(
        external["receipt_parser"],
        {
            "deployment_receipt_contract",
            "rollback_receipt_contract",
            "controller",
            "client",
        },
        f"{label}.preimage.external_dependencies.receipt_parser",
    )
    identity = {
        "contract": ACTIVATION_INTENT_CONTRACT,
        "transaction_class": value["transaction_class"],
        "canonical_root": value["canonical_root"],
        "effective_uid": owner,
        "activation_lock": lock,
        "outer_maintenance_transaction_sha256": value[
            "outer_maintenance_transaction_sha256"
        ],
        "stage": stage,
        "prior": prior,
        "candidate": candidate,
        "rollback_authority": rollback,
        "preimage": preimage,
    }
    if bridge_transition is not None:
        identity["bridge_transition"] = bridge_transition
    if transaction_id != _digest(identity):
        raise DeploymentError("control maintenance activation intent disagrees")
    pending = value["pending_step"]
    if pending is not None:
        pending = _exact(
            pending,
            {"operation", "index", "role"},
            f"{label}.pending_step",
        )
        operation = _text(
            pending["operation"],
            f"{label}.pending_step.operation",
        )
        if operation not in {
            "install",
            "replace-control",
            "remove-artifact",
            "remove-directory",
        }:
            raise DeploymentError("control maintenance pending operation is invalid")
        _nonnegative_integer(pending["index"], f"{label}.pending_step.index")
        _text(pending["role"], f"{label}.pending_step.role")
    handoff = value["smoke_handoff"]
    if handoff is not None:
        handoff = _exact(
            handoff,
            {
                "target_deployment_receipt_sha256",
                "smoke_bundle_sha256",
                "smoke_trust_context_sha256",
            },
            f"{label}.smoke_handoff",
        )
        for key in handoff:
            _sha256(handoff[key], f"{label}.smoke_handoff.{key}")
    candidate_acceptance = value["candidate_smoke_acceptance"]
    if candidate_acceptance is not None:
        candidate_acceptance = _activation_journal_acceptance_shape(
            candidate_acceptance,
            f"{label}.candidate_smoke_acceptance",
        )
    rollback_acceptance = value["rollback_smoke_acceptance"]
    if rollback_acceptance is not None:
        rollback_acceptance = _activation_journal_acceptance_shape(
            rollback_acceptance,
            f"{label}.rollback_smoke_acceptance",
            expected_phase="rollback-smoke",
        )
    terminal = value["terminal_result"]
    if terminal is not None:
        terminal = _exact(
            terminal,
            {
                "outcome",
                "candidate_receipt_sha256",
                "active_receipt_sha256",
                "accepted_envelope_sha256",
                "failure_class",
            },
            f"{label}.terminal_result",
        )
        _sha256(
            terminal["candidate_receipt_sha256"],
            f"{label}.terminal_result.candidate_receipt_sha256",
        )
    candidate_sha256 = candidate["deployment_receipt"]["sha256"]
    prior_sha256 = prior["deployment_receipt"]["sha256"]
    candidate_smoke = candidate["smoke"]
    prior_smoke = prior["smoke"]
    candidate_handoff = {
        "target_deployment_receipt_sha256": candidate_sha256,
        "smoke_bundle_sha256": candidate_smoke["bundle"]["sha256"],
        "smoke_trust_context_sha256": candidate_smoke["trust_context"]["sha256"],
    }
    rollback_handoff = {
        "target_deployment_receipt_sha256": prior_sha256,
        "smoke_bundle_sha256": prior_smoke["bundle"]["sha256"],
        "smoke_trust_context_sha256": prior_smoke["trust_context"]["sha256"],
    }
    if candidate_acceptance is not None and (
        candidate_acceptance["target_deployment_receipt_sha256"] != candidate_sha256
        or candidate_acceptance["expected_envelope_sha256"]
        != candidate_smoke["expected_envelope_sha256"]
        or candidate_acceptance["accepted_envelope_sha256"]
        != candidate_smoke["expected_envelope_sha256"]
    ):
        raise DeploymentError("control maintenance smoke acceptance disagrees")
    if rollback_acceptance is not None and (
        rollback_acceptance["target_deployment_receipt_sha256"] != prior_sha256
        or rollback_acceptance["expected_envelope_sha256"]
        != prior_smoke["expected_envelope_sha256"]
        or rollback_acceptance["accepted_envelope_sha256"]
        != prior_smoke["expected_envelope_sha256"]
    ):
        raise DeploymentError("control maintenance rollback smoke acceptance disagrees")
    if phase in {"prepared", "frozen", "drained"}:
        coherent = all(
            item is None
            for item in (
                pending,
                handoff,
                candidate_acceptance,
                rollback_acceptance,
                terminal,
            )
        )
    elif phase == "additive-installing":
        coherent = (
            pending is not None
            and pending["operation"] == "install"
            and handoff is None
            and candidate_acceptance is None
            and rollback_acceptance is None
            and terminal is None
        )
    elif phase in {"control-switching", "prior-restoring"}:
        coherent = (
            pending is not None
            and pending["operation"] == "replace-control"
            and pending["index"] < len(CONTROL_MAINTENANCE_REPLACEMENT_ROLES)
            and pending["role"]
            == CONTROL_MAINTENANCE_REPLACEMENT_ROLES[pending["index"]]
            and handoff is None
            and candidate_acceptance is None
            and rollback_acceptance is None
            and terminal is None
        )
    elif phase == "candidate-smoke":
        coherent = (
            pending is None
            and handoff == candidate_handoff
            and rollback_acceptance is None
            and terminal is None
        )
    elif phase == "candidate-accepted":
        coherent = (
            pending is None
            and handoff is None
            and candidate_acceptance is not None
            and rollback_acceptance is None
            and terminal is None
        )
    elif phase == "rollback-smoke":
        coherent = (
            pending is None
            and handoff == rollback_handoff
            and candidate_acceptance is None
            and terminal is None
        )
    elif phase == "prior-accepted":
        coherent = (
            pending is None
            and handoff is None
            and candidate_acceptance is None
            and rollback_acceptance is not None
            and terminal is None
        )
    elif phase == "rollback-cleaning":
        coherent = (
            pending is not None
            and pending["operation"] in {"remove-artifact", "remove-directory"}
            and handoff is None
            and candidate_acceptance is None
            and rollback_acceptance is not None
            and terminal is None
        )
    else:
        coherent = pending is None and handoff is None and terminal is not None
    if not coherent:
        raise DeploymentError("control maintenance activation phase is incoherent")
    if terminal is not None:
        if terminal["candidate_receipt_sha256"] != candidate_sha256:
            raise DeploymentError(
                "control maintenance terminal candidate binding disagrees"
            )
        successful_outcome = "manual-target-active" if manual else "candidate-active"
        restored_outcome = "manual-current-restored" if manual else "restored-prior"
        target_rejection = (
            "target-smoke-rejected" if manual else "candidate-smoke-rejected"
        )
        if terminal["outcome"] == successful_outcome:
            valid = (
                candidate_acceptance is not None
                and rollback_acceptance is None
                and terminal["active_receipt_sha256"] == candidate_sha256
                and terminal["accepted_envelope_sha256"]
                == candidate_smoke["expected_envelope_sha256"]
                and terminal["failure_class"] is None
            )
        elif terminal["outcome"] == restored_outcome:
            valid = (
                candidate_acceptance is None
                and rollback_acceptance is not None
                and terminal["active_receipt_sha256"] == prior_sha256
                and terminal["accepted_envelope_sha256"]
                == prior_smoke["expected_envelope_sha256"]
                and terminal["failure_class"] == target_rejection
            )
        elif terminal["outcome"] == "recovery-required":
            valid = (
                candidate_acceptance is None
                and rollback_acceptance is None
                and terminal["active_receipt_sha256"] is None
                and terminal["accepted_envelope_sha256"] is None
                and terminal["failure_class"] == "rollback-smoke-rejected"
            )
        else:
            valid = False
        if not valid:
            raise DeploymentError("control maintenance terminal is incoherent")
    return _ActivationJournal(_freeze_json(value), raw)


def _parse_activation_journal(raw: bytes) -> _ActivationJournal:
    preliminary = _parse_canonical_json(raw, "activation transaction journal")
    if (
        isinstance(preliminary, dict)
        and preliminary.get("transaction_class") == "routine-payload"
    ):
        return _parse_routine_activation_journal(raw)
    if (
        isinstance(preliminary, dict)
        and preliminary.get("transaction_class") == "control-set-maintenance"
        and isinstance(preliminary.get("prior"), dict)
        and preliminary["prior"].get("state") == "active"
    ):
        return _parse_control_maintenance_activation_journal(raw)
    if (
        isinstance(preliminary, dict)
        and preliminary.get("transaction_class") == "manual-exact-target-rollback"
        and isinstance(preliminary.get("prior"), dict)
        and preliminary["prior"].get("state") == "active"
    ):
        return _parse_control_maintenance_activation_journal(
            raw,
            expected_transaction_class="manual-exact-target-rollback",
        )
    label = "activation transaction journal"
    value = _exact(
        _parse_canonical_json(raw, label),
        {
            "schema_version",
            "contract",
            "transaction_id",
            "sequence",
            "previous_journal_sha256",
            "transaction_class",
            "phase",
            "canonical_root",
            "effective_uid",
            "activation_lock",
            "outer_maintenance_transaction_sha256",
            "stage",
            "prior",
            "candidate",
            "rollback_authority",
            "preimage",
            "pending_step",
            "smoke_handoff",
            "candidate_smoke_acceptance",
            "rollback_smoke_acceptance",
            "terminal_result",
            "content_sha256",
        },
        label,
    )
    _content_sha256(value, label)
    transaction_id = _sha256(value["transaction_id"], f"{label}.transaction_id")
    sequence = _nonnegative_integer(value["sequence"], f"{label}.sequence")
    previous = value["previous_journal_sha256"]
    if previous is not None:
        _sha256(previous, f"{label}.previous_journal_sha256")
    root = _normalized_absolute_path(
        Path(_text(value["canonical_root"], f"{label}.canonical_root")),
        f"{label}.canonical_root",
    )
    effective_uid = _nonnegative_integer(
        value["effective_uid"],
        f"{label}.effective_uid",
    )
    phase = _text(value["phase"], f"{label}.phase")
    lock = _exact(
        value["activation_lock"],
        {"path", "device", "inode", "owner", "mode"},
        f"{label}.activation_lock",
    )
    lock_path = _normalized_absolute_path(
        Path(_text(lock["path"], f"{label}.activation_lock.path")),
        f"{label}.activation_lock.path",
    )
    for key in ("device", "inode", "owner", "mode"):
        _nonnegative_integer(lock[key], f"{label}.activation_lock.{key}")
    stage = _exact(
        value["stage"],
        {
            "receipt_path",
            "receipt_sha256",
            "plan_sha256",
            "authorization_sha256",
            "maintenance_transaction_sha256",
        },
        f"{label}.stage",
    )
    stage_path = _normalized_absolute_path(
        Path(_text(stage["receipt_path"], f"{label}.stage.receipt_path")),
        f"{label}.stage.receipt_path",
    )
    for key in (
        "receipt_sha256",
        "plan_sha256",
        "authorization_sha256",
        "maintenance_transaction_sha256",
    ):
        _sha256(stage[key], f"{label}.stage.{key}")
    prior = _activation_journal_unit_shape(
        value["prior"],
        root,
        effective_uid,
        f"{label}.prior",
    )
    candidate = _activation_journal_unit_shape(
        value["candidate"],
        root,
        effective_uid,
        f"{label}.candidate",
    )
    rollback = _exact(
        value["rollback_authority"],
        {"receipt_path", "receipt_sha256", "target_state"},
        f"{label}.rollback_authority",
    )
    rollback_sha256 = _sha256(
        rollback["receipt_sha256"],
        f"{label}.rollback_authority.receipt_sha256",
    )
    rollback_path = _normalized_absolute_path(
        Path(_text(rollback["receipt_path"], f"{label}.rollback_authority.path")),
        f"{label}.rollback_authority.path",
    )
    preimage = _exact(
        value["preimage"],
        {"manifest_path", "manifest_sha256", "artifacts", "external_dependencies"},
        f"{label}.preimage",
    )
    preimage_path = _normalized_absolute_path(
        Path(_text(preimage["manifest_path"], f"{label}.preimage.manifest_path")),
        f"{label}.preimage.manifest_path",
    )
    if (
        value["schema_version"] != 1
        or type(value["schema_version"]) is not int
        or value["contract"] != ACTIVATION_TRANSACTION_CONTRACT
        or sequence < 1
        or (sequence == 1) != (previous is None)
        or value["transaction_class"] != "control-set-maintenance"
        or phase
        not in {
            "prepared",
            "frozen",
            "drained",
            "control-installing",
            "candidate-smoke",
            "candidate-accepted",
            "absence-restoring",
            "absence-accepted",
            "terminal",
        }
        or value["canonical_root"] != str(root)
        or effective_uid != os.geteuid()
        or lock_path != root / "activation.lock"
        or lock["path"] != str(lock_path)
        or lock["owner"] != effective_uid
        or lock["mode"] != 0o600
        or stage["receipt_path"] != str(stage_path)
        or stage["maintenance_transaction_sha256"]
        != value["outer_maintenance_transaction_sha256"]
        or _sha256(
            value["outer_maintenance_transaction_sha256"],
            f"{label}.outer_maintenance_transaction_sha256",
        )
        != value["outer_maintenance_transaction_sha256"]
        or prior["state"] != "absent"
        or candidate["state"] != "active"
        or rollback_path != root / "receipts" / f"sha256-{rollback_sha256}.json"
        or rollback["receipt_path"] != str(rollback_path)
        or rollback["target_state"] != "absent"
        or preimage_path != rollback_path
        or preimage["manifest_path"] != str(preimage_path)
        or preimage["manifest_sha256"] != rollback_sha256
        or preimage["artifacts"] != []
        or preimage["external_dependencies"] != []
    ):
        raise DeploymentError("activation transaction journal contract mismatch")
    identity = {
        "contract": ACTIVATION_INTENT_CONTRACT,
        "transaction_class": value["transaction_class"],
        "canonical_root": value["canonical_root"],
        "effective_uid": effective_uid,
        "activation_lock": lock,
        "outer_maintenance_transaction_sha256": value[
            "outer_maintenance_transaction_sha256"
        ],
        "stage": stage,
        "prior": prior,
        "candidate": candidate,
        "rollback_authority": rollback,
        "preimage": preimage,
    }
    if transaction_id != _digest(identity):
        raise DeploymentError("activation transaction journal intent identity mismatch")
    pending = value["pending_step"]
    if pending is not None:
        pending = _exact(
            pending,
            {"operation", "index", "role"},
            f"{label}.pending_step",
        )
        operation = _text(
            pending["operation"],
            f"{label}.pending_step.operation",
        )
        if operation not in {
            "install",
            "remove-artifact",
            "remove-directory",
        }:
            raise DeploymentError("activation transaction pending operation is invalid")
        _nonnegative_integer(pending["index"], f"{label}.pending_step.index")
        _text(pending["role"], f"{label}.pending_step.role")
    handoff = value["smoke_handoff"]
    if handoff is not None:
        handoff = _exact(
            handoff,
            {
                "target_deployment_receipt_sha256",
                "smoke_bundle_sha256",
                "smoke_trust_context_sha256",
            },
            f"{label}.smoke_handoff",
        )
        for key in handoff:
            _sha256(handoff[key], f"{label}.smoke_handoff.{key}")
    candidate_acceptance = value["candidate_smoke_acceptance"]
    if candidate_acceptance is not None:
        candidate_acceptance = _activation_journal_acceptance_shape(
            candidate_acceptance,
            f"{label}.candidate_smoke_acceptance",
        )
    if value["rollback_smoke_acceptance"] is not None:
        raise DeploymentError("first-install rollback smoke acceptance is forbidden")
    terminal = value["terminal_result"]
    if terminal is not None:
        terminal = _exact(
            terminal,
            {
                "outcome",
                "candidate_receipt_sha256",
                "active_receipt_sha256",
                "accepted_envelope_sha256",
                "failure_class",
            },
            f"{label}.terminal_result",
        )
        _sha256(
            terminal["candidate_receipt_sha256"],
            f"{label}.terminal_result.candidate_receipt_sha256",
        )
    candidate_receipt_sha256 = candidate["deployment_receipt"]["sha256"]
    smoke = candidate["smoke"]
    expected_handoff = {
        "target_deployment_receipt_sha256": candidate_receipt_sha256,
        "smoke_bundle_sha256": smoke["bundle"]["sha256"],
        "smoke_trust_context_sha256": smoke["trust_context"]["sha256"],
    }
    if candidate_acceptance is not None and (
        candidate_acceptance["target_deployment_receipt_sha256"]
        != candidate_receipt_sha256
        or candidate_acceptance["expected_envelope_sha256"]
        != smoke["expected_envelope_sha256"]
        or candidate_acceptance["accepted_envelope_sha256"]
        != smoke["expected_envelope_sha256"]
    ):
        raise DeploymentError("activation transaction smoke acceptance disagrees")
    plain = (
        pending is None
        and handoff is None
        and candidate_acceptance is None
        and terminal is None
    )
    if phase in {"prepared", "frozen", "drained", "absence-accepted"}:
        coherent = plain
    elif phase == "control-installing":
        coherent = (
            pending is not None
            and pending["operation"] == "install"
            and handoff is None
            and candidate_acceptance is None
            and terminal is None
        )
    elif phase == "candidate-smoke":
        coherent = pending is None and handoff == expected_handoff and terminal is None
    elif phase == "candidate-accepted":
        coherent = (
            pending is None
            and handoff is None
            and candidate_acceptance is not None
            and terminal is None
        )
    elif phase == "absence-restoring":
        coherent = (
            handoff is None
            and candidate_acceptance is None
            and terminal is None
            and (
                pending is None
                or pending["operation"] in {"remove-artifact", "remove-directory"}
            )
        )
    else:
        coherent = pending is None and handoff is None and terminal is not None
    if not coherent:
        raise DeploymentError("activation transaction journal phase is incoherent")
    if terminal is not None:
        if terminal["candidate_receipt_sha256"] != candidate_receipt_sha256:
            raise DeploymentError("activation terminal candidate binding disagrees")
        if terminal["outcome"] == "candidate-active":
            if (
                candidate_acceptance is None
                or terminal["active_receipt_sha256"] != candidate_receipt_sha256
                or terminal["accepted_envelope_sha256"]
                != smoke["expected_envelope_sha256"]
                or terminal["failure_class"] is not None
            ):
                raise DeploymentError("activation candidate terminal is incoherent")
        elif terminal["outcome"] == "restored-absent":
            if (
                candidate_acceptance is not None
                or terminal["active_receipt_sha256"] is not None
                or terminal["accepted_envelope_sha256"] is not None
                or terminal["failure_class"] != "candidate-smoke-rejected"
            ):
                raise DeploymentError("activation absence terminal is incoherent")
        else:
            raise DeploymentError("activation terminal outcome is unsupported")
    return _ActivationJournal(_freeze_json(value), raw)


def _recovery_precondition_from_stage(
    activation: ActivationRequest,
    journal: _ActivationJournal,
) -> tuple[FirstInstallPrecondition, VerifiedDeploymentStage]:
    _validate_activation_request(activation)
    verified = verify_deployment_stage(activation.stage_receipt)
    stage = journal.value["stage"]
    if (
        verified.path != activation.stage_receipt
        or verified.path != Path(stage["receipt_path"])
        or hashlib.sha256(verified.raw).hexdigest() != stage["receipt_sha256"]
        or verified.value["plan_sha256"] != stage["plan_sha256"]
        or verified.value["maintenance_transaction_sha256"]
        != stage["maintenance_transaction_sha256"]
        or verified.value["authorization"]["sha256"] != stage["authorization_sha256"]
        or hashlib.sha256(activation.authorization_raw).hexdigest()
        != stage["authorization_sha256"]
    ):
        raise DeploymentError("activation recovery stage binding disagrees")
    rollback = _staged_artifact_for_role(
        verified.artifacts,
        "rollback-receipt",
    )
    authority = journal.value["rollback_authority"]
    preimage = journal.value["preimage"]
    rollback_sha256 = hashlib.sha256(rollback.raw).hexdigest()
    if (
        rollback.installed_path != Path(authority["receipt_path"])
        or rollback.installed_path != Path(preimage["manifest_path"])
        or rollback_sha256 != authority["receipt_sha256"]
        or rollback_sha256 != preimage["manifest_sha256"]
    ):
        raise DeploymentError("activation recovery rollback authority disagrees")
    if type(activation.deployment) in {
        DeploymentRequest,
        BridgeTransitionRequest,
        RollbackToRequest,
    }:
        active_request = (
            activation.deployment.deployment
            if type(activation.deployment) is BridgeTransitionRequest
            else activation.deployment
        )
        rollback_value = _parse_canonical_json(
            rollback.raw,
            "active-prior activation recovery rollback receipt",
        )
        recorded = _exact(
            rollback_value["precondition"],
            {
                "root_identity",
                "activation_lock_identity",
                "active_receipt_sha256",
            },
            "routine activation recovery precondition",
        )
        root_identity = _activation_identity_vector(
            recorded["root_identity"],
            "routine activation recovery root identity",
        )
        lock_identity = _activation_identity_vector(
            recorded["activation_lock_identity"],
            "routine activation recovery lock identity",
        )
        root = _normalized_absolute_path(
            Path(rollback_value["canonical_root"]),
            "routine activation recovery canonical root",
        )
        manual_rollback = type(activation.deployment) is RollbackToRequest
        control_recovery = (
            journal.value["transaction_class"]
            in {"control-set-maintenance", "manual-exact-target-rollback"}
            and journal.value["prior"]["state"] == "active"
        )
        if control_recovery:
            control_preimage = _thaw(rollback_value["control_preimage"])
            selector_preimage = _thaw(rollback_value["selector_preimage"])
            expected_preimage_artifacts = [
                *control_preimage[:-1],
                *selector_preimage,
                control_preimage[-1],
            ]
        else:
            expected_preimage_artifacts = _thaw(rollback_value["selector_preimage"])
        if (
            manual_rollback
            != (journal.value["transaction_class"] == "manual-exact-target-rollback")
            or root != active_request.canonical_root
            or str(root) != journal.value["canonical_root"]
            or _thaw(rollback_value["activation_lock"])
            != _thaw(journal.value["activation_lock"])
            or _thaw(rollback_value["prior_activation_unit"])
            != _thaw(journal.value["prior"])
            or expected_preimage_artifacts
            != _thaw(journal.value["preimage"]["artifacts"])
            or _thaw(rollback_value["external_dependencies"])
            != _thaw(journal.value["preimage"]["external_dependencies"])
            or recorded["active_receipt_sha256"]
            != active_request.expected_active_receipt_sha256
        ):
            raise DeploymentError("routine activation recovery authority disagrees")
        return (
            FirstInstallPrecondition(
                canonical_root=root,
                root_identity=root_identity,
                activation_lock=_freeze(rollback_value["activation_lock"]),
                activation_lock_identity=lock_identity,
                deployment_receipt_absent=False,
                retained_result_sha256s=_freeze({}),
            ),
            verified,
        )
    precondition = _recorded_first_install_precondition(rollback.raw)
    if (
        precondition.canonical_root != activation.deployment.canonical_root
        or str(precondition.canonical_root) != journal.value["canonical_root"]
        or _thaw(precondition.activation_lock)
        != _thaw(journal.value["activation_lock"])
    ):
        raise DeploymentError("activation recovery recorded precondition disagrees")
    return precondition, verified


def _reconstruct_routine_recovery_precondition(
    activation_root: _RootSnapshot,
    activation_lock_fd: int,
    activation: ActivationRequest,
    verified: VerifiedDeploymentStage,
    journal: _ActivationJournal,
    *,
    opaque_pending_temporary: bool = False,
) -> ActiveDeploymentPrecondition:
    if type(activation.deployment) is not DeploymentRequest:
        raise DeploymentError("routine activation recovery request type mismatch")
    recorded, _ = _recovery_precondition_from_stage(activation, journal)
    _recheck_activation_lock_acquisition(
        activation_root,
        activation_lock_fd,
        recorded,
        root_mapping_only=True,
    )
    rollback = _parse_canonical_json(
        _staged_artifact_for_role(
            verified.artifacts,
            "rollback-receipt",
        ).raw,
        "routine activation recovery rollback receipt",
    )
    prior_deployment = _staged_artifact_for_role(
        verified.artifacts,
        "prior-deployment-alias",
    )
    prior_active = _staged_artifact_for_role(
        verified.artifacts,
        "prior-active-record",
    )
    receipt_raw = prior_deployment.raw
    receipt_sha256 = hashlib.sha256(receipt_raw).hexdigest()
    receipt = _parse_canonical_json(
        receipt_raw,
        "routine activation recovery prior receipt",
    )
    _content_sha256(receipt, "routine activation recovery prior receipt")
    if (
        receipt_sha256 != activation.deployment.expected_active_receipt_sha256
        or receipt_sha256 != rollback["precondition"]["active_receipt_sha256"]
        or _thaw(rollback["prior_activation_unit"]) != _thaw(journal.value["prior"])
    ):
        raise DeploymentError("routine activation recovery prior receipt disagrees")
    active_source = _validate_active_runtime_and_trust(
        receipt,
        prior_active.raw,
        recorded.canonical_root,
    )
    candidate_receipt = _staged_artifact_for_role(
        verified.artifacts,
        "deployment-receipt",
    )
    candidate_rollback = _staged_artifact_for_role(
        verified.artifacts,
        "rollback-receipt",
    )
    candidate_names = {
        candidate_receipt.installed_path.name,
        candidate_rollback.installed_path.name,
    }
    try:
        live_names = set(os.listdir(recorded.canonical_root / "receipts"))
    except OSError as error:
        raise DeploymentError(
            "routine activation recovery receipt inventory is unavailable"
        ) from error
    allowed_extra_names = set(candidate_names & live_names)
    if journal.value["phase"] == "additive-installing":
        artifacts = _ordered_routine_activation_artifacts(verified)
        pending_index = journal.value["pending_step"]["index"]
        pending_artifact = artifacts[pending_index]
        if pending_artifact.role in {"rollback-receipt", "deployment-receipt"}:
            temporary = _activation_artifact_temporary_name(
                journal.value["transaction_id"],
                pending_index,
            )
            if temporary in live_names:
                allowed_extra_names.add(temporary)
    allowed_extras = frozenset(allowed_extra_names)
    controls = _exact(
        receipt["control_set"],
        {"shim", "client", "launcher", "controller", "policy"},
        "routine activation recovery control set",
    )
    normalized_controls: dict[str, Any] = {}
    control_raws: dict[str, bytes] = {}
    for role, binding in controls.items():
        expected_path = {
            "shim": recorded.canonical_root / "task-witness",
            "client": recorded.canonical_root / "client" / "task_witness_client.py",
            "launcher": recorded.canonical_root / "launcher" / "task_witness_launch.py",
            "controller": recorded.canonical_root
            / "controller"
            / "task_witness_deploy.py",
            "policy": recorded.canonical_root / "controller" / "policy.json",
        }[role]
        normalized, control_raw = _capture_routine_receipt_file(
            binding,
            expected_path=expected_path,
            byte_limit=(
                MAX_JSON_BYTES if role == "policy" else MAX_CANDIDATE_TREE_FILE_BYTES
            ),
            label=f"routine activation recovery {role}",
        )
        _validate_installed_receipt_binding(
            normalized,
            f"routine activation recovery {role}",
        )
        normalized_controls[role] = normalized
        control_raws[role] = control_raw
    _, smoke_manifest_raw = _capture_routine_receipt_file(
        receipt["smoke"]["bundle"]["manifest"],
        expected_path=(recorded.canonical_root / "smoke" / "bundle" / "manifest.json"),
        byte_limit=MAX_JSON_BYTES,
        label="routine activation recovery smoke bundle manifest",
    )
    control_raws["smoke-bundle-manifest"] = smoke_manifest_raw
    policy_raw = control_raws["policy"]
    active_policy = _parse_compatibility_policy(policy_raw)
    chain = _validate_retained_receipt_chain(
        receipt,
        receipt_raw,
        recorded.canonical_root,
        active_policy=active_policy,
        allowed_extra_names=allowed_extras,
    )
    active_unit = _activation_journal_unit_shape(
        rollback["prior_activation_unit"],
        recorded.canonical_root,
        os.geteuid(),
        "routine activation recovery prior unit",
    )
    if (
        active_unit["deployment_receipt"]["sha256"] != receipt_sha256
        or active_unit["active_record"]["sha256"]
        != hashlib.sha256(prior_active.raw).hexdigest()
        or active_unit["control_set"] != normalized_controls
        or active_unit["smoke"] != receipt["smoke"]
    ):
        raise DeploymentError("routine activation recovery prior unit disagrees")
    return ActiveDeploymentPrecondition(
        canonical_root=recorded.canonical_root,
        root_identity=recorded.root_identity,
        activation_lock=recorded.activation_lock,
        activation_lock_identity=recorded.activation_lock_identity,
        receipt_value=_freeze_json(receipt),
        receipt_raw=receipt_raw,
        receipt_sha256=receipt_sha256,
        active_raw=prior_active.raw,
        active_unit=_freeze_json(active_unit),
        active_policy=active_policy,
        active_source=active_source,
        retained_chain=chain,
        control_raws=_freeze(control_raws),
        retained_result_raws=_capture_recorded_transaction_result_baseline(
            activation_root.fd,
            recorded.canonical_root,
            journal,
            opaque_pending_temporary=opaque_pending_temporary,
        ),
    )


def _reconstruct_control_maintenance_recovery_precondition(
    activation_root: _RootSnapshot,
    activation_lock_fd: int,
    activation: ActivationRequest,
    verified: VerifiedDeploymentStage,
    journal: _ActivationJournal,
    *,
    opaque_pending_temporary: bool = False,
) -> ActiveDeploymentPrecondition:
    bridge_transition = type(activation.deployment) is BridgeTransitionRequest
    active_request = (
        activation.deployment.deployment if bridge_transition else activation.deployment
    )
    active_prior_class = (
        type(active_request) is DeploymentRequest
        and journal.value["transaction_class"] == "control-set-maintenance"
    ) or (
        type(activation.deployment) is RollbackToRequest
        and journal.value["transaction_class"] == "manual-exact-target-rollback"
    )
    if not active_prior_class or journal.value["prior"]["state"] != "active":
        raise DeploymentError("control maintenance recovery request disagrees")
    recorded, _ = _recovery_precondition_from_stage(activation, journal)
    _recheck_activation_lock_acquisition(
        activation_root,
        activation_lock_fd,
        recorded,
        root_mapping_only=True,
    )
    rollback_artifact = _staged_artifact_for_role(
        verified.artifacts,
        "rollback-receipt",
    )
    rollback = _parse_canonical_json(
        rollback_artifact.raw,
        "control maintenance recovery rollback receipt",
    )
    prior_deployment = _staged_artifact_for_role(
        verified.artifacts,
        "prior-deployment-alias",
    )
    prior_active = _staged_artifact_for_role(
        verified.artifacts,
        "prior-active-record",
    )
    receipt_raw = prior_deployment.raw
    receipt_sha256 = hashlib.sha256(receipt_raw).hexdigest()
    receipt = _parse_canonical_json(
        receipt_raw,
        "control maintenance recovery prior receipt",
    )
    _content_sha256(receipt, "control maintenance recovery prior receipt")
    if (
        receipt_sha256 != active_request.expected_active_receipt_sha256
        or receipt_sha256 != rollback["precondition"]["active_receipt_sha256"]
        or _thaw(rollback["prior_activation_unit"]) != _thaw(journal.value["prior"])
    ):
        raise DeploymentError("control maintenance recovery prior receipt disagrees")
    active_source = _validate_active_runtime_and_trust(
        receipt,
        prior_active.raw,
        recorded.canonical_root,
        source_parser=(
            _bridge_legacy_active_receipt_source if bridge_transition else None
        ),
    )
    candidate_receipt = _staged_artifact_for_role(
        verified.artifacts,
        "deployment-receipt",
    )
    candidate_names = {
        candidate_receipt.installed_path.name,
        rollback_artifact.installed_path.name,
    }
    try:
        live_names = set(os.listdir(recorded.canonical_root / "receipts"))
    except OSError as error:
        raise DeploymentError(
            "control maintenance recovery receipt inventory is unavailable"
        ) from error
    allowed_extra_names = set(candidate_names & live_names)
    if journal.value["phase"] == "additive-installing":
        additive = _ordered_control_maintenance_additive_artifacts(verified)
        pending_index = journal.value["pending_step"]["index"]
        pending_artifact = additive[pending_index]
        if pending_artifact.role in {"rollback-receipt", "deployment-receipt"}:
            temporary = _activation_artifact_temporary_name(
                journal.value["transaction_id"],
                pending_index,
            )
            if temporary in live_names:
                allowed_extra_names.add(temporary)
    controls = _exact(
        receipt["control_set"],
        set(CONTROL_SET_ROLES),
        "control maintenance recovery prior control set",
    )
    normalized_controls: dict[str, Any] = {}
    control_raws: dict[str, bytes] = {}
    for role in CONTROL_SET_ROLES:
        prior = _staged_artifact_for_role(
            verified.artifacts,
            f"prior-{role}",
        )
        binding = _receipt_file_binding(
            controls[role],
            f"control maintenance recovery prior {role}",
        )
        expected = _activation_file_binding(prior)
        if binding != expected:
            raise DeploymentError(
                f"control maintenance recovery prior {role} binding disagrees"
            )
        normalized_controls[role] = binding
        control_raws[role] = prior.raw
    prior_manifest = _staged_artifact_for_role(
        verified.artifacts,
        "prior-smoke-bundle-manifest",
    )
    manifest_binding = _receipt_file_binding(
        receipt["smoke"]["bundle"]["manifest"],
        "control maintenance recovery prior smoke manifest",
    )
    if manifest_binding != _activation_file_binding(prior_manifest):
        raise DeploymentError(
            "control maintenance recovery prior smoke manifest disagrees"
        )
    control_raws["smoke-bundle-manifest"] = prior_manifest.raw
    active_policy = (
        _parse_bridge_legacy_compatibility_policy(control_raws["policy"])
        if bridge_transition
        else _parse_compatibility_policy(control_raws["policy"])
    )
    chain = _validate_retained_receipt_chain(
        receipt,
        receipt_raw,
        recorded.canonical_root,
        active_policy=active_policy,
        allowed_extra_names=frozenset(allowed_extra_names),
        receipt_profile=(
            BRIDGE_LEGACY_RECEIPT_PROFILE
            if bridge_transition
            else CURRENT_RECEIPT_PROFILE
        ),
    )
    active_unit = _activation_journal_unit_shape(
        rollback["prior_activation_unit"],
        recorded.canonical_root,
        os.geteuid(),
        "control maintenance recovery prior unit",
    )
    if (
        active_unit["deployment_receipt"]["sha256"] != receipt_sha256
        or active_unit["active_record"]["sha256"]
        != hashlib.sha256(prior_active.raw).hexdigest()
        or active_unit["control_set"] != normalized_controls
        or active_unit["smoke"] != receipt["smoke"]
    ):
        raise DeploymentError("control maintenance recovery prior unit disagrees")
    return ActiveDeploymentPrecondition(
        canonical_root=recorded.canonical_root,
        root_identity=recorded.root_identity,
        activation_lock=recorded.activation_lock,
        activation_lock_identity=recorded.activation_lock_identity,
        receipt_value=_freeze_json(receipt),
        receipt_raw=receipt_raw,
        receipt_sha256=receipt_sha256,
        active_raw=prior_active.raw,
        active_unit=_freeze_json(active_unit),
        active_policy=active_policy,
        active_source=active_source,
        retained_chain=chain,
        control_raws=_freeze(control_raws),
        retained_result_raws=_capture_recorded_transaction_result_baseline(
            activation_root.fd,
            recorded.canonical_root,
            journal,
            opaque_pending_temporary=opaque_pending_temporary,
        ),
    )


def _validate_activation_journal_program(
    journal: _ActivationJournal,
    artifacts: tuple[StagedArtifact, ...],
    removals: tuple[_ActivationRemovalStep, ...],
) -> None:
    if journal.value["transaction_class"] == "routine-payload":
        _validate_routine_activation_journal_program(
            journal,
            artifacts,
            removals,
        )
        return
    value = journal.value
    phase = value["phase"]
    sequence = value["sequence"]
    pending = value["pending_step"]
    artifact_count = len(artifacts)
    if phase == "prepared":
        expected_sequence = 1
    elif phase == "frozen":
        expected_sequence = 2
    elif phase == "drained":
        expected_sequence = 3
    elif phase == "control-installing":
        index = pending["index"]
        if index >= artifact_count:
            raise DeploymentError("activation install cursor is out of range")
        artifact = artifacts[index]
        if pending != {
            "operation": "install",
            "index": index,
            "role": artifact.role,
        }:
            raise DeploymentError("activation install cursor disagrees")
        expected_sequence = 4 + index
    elif phase == "candidate-smoke":
        expected_sequence = 4 + artifact_count
        if value["candidate_smoke_acceptance"] is not None:
            expected_sequence += 1
    elif phase == "candidate-accepted":
        expected_sequence = 6 + artifact_count
    elif phase == "absence-restoring":
        expected_sequence = 5 + artifact_count
        if pending is not None:
            index = pending["index"]
            if index >= len(removals):
                raise DeploymentError("activation removal cursor is out of range")
            step = removals[index]
            if pending != {
                "operation": step.operation,
                "index": index,
                "role": step.role,
            }:
                raise DeploymentError("activation removal cursor disagrees")
            expected_sequence = 6 + artifact_count + index
    elif phase == "absence-accepted":
        expected_sequence = 6 + artifact_count + len(removals)
    else:
        terminal = value["terminal_result"]
        if terminal["outcome"] == "candidate-active":
            expected_sequence = 7 + artifact_count
        else:
            expected_sequence = 7 + artifact_count + len(removals)
    if sequence != expected_sequence:
        raise DeploymentError(
            "activation journal sequence disagrees with its program state"
        )


def _validate_routine_activation_journal_program(
    journal: _ActivationJournal,
    artifacts: tuple[StagedArtifact, ...],
    cleanups: tuple[_ActivationRemovalStep, ...],
) -> None:
    value = journal.value
    phase = value["phase"]
    sequence = value["sequence"]
    pending = value["pending_step"]
    count = len(artifacts)
    if len(cleanups) < 2:
        raise DeploymentError("routine activation cleanup program disagrees")
    if phase == "prepared":
        expected = 1
    elif phase == "frozen":
        expected = 2
    elif phase == "drained":
        expected = 3
    elif phase == "additive-installing":
        index = pending["index"]
        if index >= count:
            raise DeploymentError("routine activation install cursor is out of range")
        artifact = artifacts[index]
        if pending != {
            "operation": "install",
            "index": index,
            "role": artifact.role,
        }:
            raise DeploymentError("routine activation install cursor disagrees")
        expected = 4 + index
    elif phase == "selector-switching":
        index = pending["index"]
        roles = ("active-record", "deployment-alias")
        if index not in {0, 1} or pending != {
            "operation": "replace-selector",
            "index": index,
            "role": roles[index],
        }:
            raise DeploymentError("routine activation selector cursor disagrees")
        expected = 4 + count + index
    elif phase == "candidate-smoke":
        expected = 6 + count
        if value["candidate_smoke_acceptance"] is not None:
            expected += 1
    elif phase == "candidate-accepted":
        expected = 8 + count
    elif phase == "prior-restoring":
        index = pending["index"]
        roles = ("active-record", "deployment-alias")
        if index not in {0, 1} or pending != {
            "operation": "replace-selector",
            "index": index,
            "role": roles[index],
        }:
            raise DeploymentError("routine activation restore cursor disagrees")
        expected = 7 + count + index
    elif phase == "rollback-smoke":
        expected = 9 + count
        if value["rollback_smoke_acceptance"] is not None:
            expected += 1
    elif phase == "prior-accepted":
        expected = 11 + count
    elif phase == "rollback-cleaning":
        index = pending["index"]
        if index >= len(cleanups):
            raise DeploymentError("routine activation cleanup cursor is out of range")
        step = cleanups[index]
        if pending != {
            "operation": step.operation,
            "index": index,
            "role": step.role,
        }:
            raise DeploymentError("routine activation cleanup cursor disagrees")
        expected = 12 + count + index
    else:
        outcome = value["terminal_result"]["outcome"]
        if outcome == "candidate-active":
            expected = 9 + count
        elif outcome == "recovery-required":
            expected = 10 + count
        else:
            expected = 12 + count + len(cleanups)
    if sequence != expected:
        raise DeploymentError(
            "routine activation journal sequence disagrees with its program state"
        )


def _read_activation_file(
    parent_fd: int,
    name: str,
    label: str,
    *,
    limit: int = MAX_JSON_BYTES,
) -> bytes:
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        raise DeploymentError(f"{label} is unavailable") from error
    try:
        after = os.fstat(descriptor)
        if (
            _identity(before) != _identity(after)
            or not stat.S_ISREG(after.st_mode)
            or after.st_uid != os.geteuid()
            or after.st_nlink != 1
            or stat.S_IMODE(after.st_mode) != 0o600
        ):
            raise DeploymentError(f"{label} disposition disagrees")
        return _read_descriptor(descriptor, limit, label)
    finally:
        os.close(descriptor)


def _write_activation_journal(
    canonical_root_fd: int,
    journal: _ActivationJournal,
) -> None:
    if journal.value["sequence"] == 1:
        try:
            os.stat(
                "transaction.json",
                dir_fd=canonical_root_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise DeploymentError("activation transaction already exists")
    else:
        current = _read_activation_file(
            canonical_root_fd,
            "transaction.json",
            "activation transaction journal",
        )
        if (
            hashlib.sha256(current).hexdigest()
            != journal.value["previous_journal_sha256"]
        ):
            raise DeploymentError("activation transaction journal chain disagrees")
    temporary = (
        f"transaction.{journal.value['transaction_id']}.{journal.value['sequence']}.tmp"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor = -1
    temporary_created = False
    replaced = False
    try:
        descriptor = os.open(temporary, flags, 0o600, dir_fd=canonical_root_fd)
        temporary_created = True
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, journal.raw, "activation transaction journal")
        os.close(descriptor)
        descriptor = -1
        os.replace(
            temporary,
            "transaction.json",
            src_dir_fd=canonical_root_fd,
            dst_dir_fd=canonical_root_fd,
        )
        replaced = True
        os.fsync(canonical_root_fd)
        if (
            _read_activation_file(
                canonical_root_fd,
                "transaction.json",
                "activation transaction journal",
            )
            != journal.raw
        ):
            raise DeploymentError("activation transaction journal reread disagrees")
    except DeploymentError:
        raise
    except OSError as error:
        raise DeploymentError(
            "activation transaction journal cannot be replaced"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_created and not replaced:
            try:
                os.unlink(temporary, dir_fd=canonical_root_fd)
            except OSError:
                pass


def _read_activation_artifact_entry(
    parent_fd: int,
    name: str,
    artifact: StagedArtifact,
    *,
    label: str,
    allowed_nlinks: set[int],
    allowed_modes: set[int] | None = None,
    absent: bool = False,
) -> tuple[os.stat_result, bytes] | None:
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        if absent:
            return None
        raise DeploymentError(f"{label} is unavailable") from None
    except OSError as error:
        raise DeploymentError(f"{label} is unavailable") from error
    try:
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        raise DeploymentError(f"{label} is unavailable") from error
    try:
        after = os.fstat(descriptor)
        modes = {artifact.installed["mode"]} if allowed_modes is None else allowed_modes
        if (
            _identity(before) != _identity(after)
            or not stat.S_ISREG(after.st_mode)
            or after.st_uid != artifact.installed["owner"]
            or after.st_nlink not in allowed_nlinks
            or stat.S_IMODE(after.st_mode) not in modes
        ):
            raise DeploymentError(f"{label} binding disagrees")
        raw = _read_descriptor(
            descriptor,
            max(len(artifact.raw), 1),
            label,
        )
        return after, raw
    finally:
        os.close(descriptor)


def _read_activation_install_temporary(
    parent_fd: int,
    name: str,
    artifact: StagedArtifact,
    *,
    allowed_nlinks: set[int],
    absent: bool = False,
) -> tuple[os.stat_result, bytes] | None:
    label = f"activation {artifact.role} transaction temporary"
    try:
        visible = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        if absent:
            return None
        raise DeploymentError(f"{label} is unavailable") from None
    except OSError as error:
        raise DeploymentError(f"{label} is unavailable") from error
    if (
        stat.S_ISREG(visible.st_mode)
        and visible.st_uid == artifact.installed["owner"]
        and visible.st_nlink == 1
        and visible.st_size == 0
        and stat.S_IMODE(visible.st_mode) == 0
    ):
        return visible, b""
    return _read_activation_artifact_entry(
        parent_fd,
        name,
        artifact,
        label=label,
        allowed_nlinks=allowed_nlinks,
        allowed_modes={0o600, artifact.installed["mode"]},
    )


def _activation_install_temporary_is_owned(
    metadata: os.stat_result,
    raw: bytes,
    artifact: StagedArtifact,
) -> bool:
    mode = stat.S_IMODE(metadata.st_mode)
    if metadata.st_nlink == 2:
        return mode == artifact.installed["mode"] and raw == artifact.raw
    if metadata.st_nlink != 1:
        return False
    if mode == 0:
        return raw == b""
    if mode == 0o600:
        return artifact.raw.startswith(raw)
    return mode == artifact.installed["mode"] and raw == artifact.raw


def _unlink_activation_install_temporary(
    parent_fd: int,
    name: str,
    expected: os.stat_result,
    artifact: StagedArtifact,
) -> None:
    try:
        visible = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if _identity(visible) != _identity(expected):
            raise DeploymentError(
                f"activation {artifact.role} transaction temporary changed"
            )
        os.unlink(name, dir_fd=parent_fd)
    except DeploymentError:
        raise
    except OSError as error:
        raise DeploymentError(
            f"activation {artifact.role} transaction temporary cannot be removed"
        ) from error


def _verify_activation_artifact(
    parent_fd: int,
    name: str,
    artifact: StagedArtifact,
) -> None:
    entry = _read_activation_artifact_entry(
        parent_fd,
        name,
        artifact,
        label=f"installed {artifact.role}",
        allowed_nlinks={1},
    )
    if entry is None or entry[1] != artifact.raw:
        raise DeploymentError(f"installed {artifact.role} binding disagrees")


def _activation_artifact_temporary_name(
    transaction_id: str,
    step_index: int,
) -> str:
    transaction = _sha256(transaction_id, "activation transaction id")
    index = _nonnegative_integer(step_index, "activation install step index")
    return f".task-witness-install-{transaction}-{index}.tmp"


def _open_activation_install_directory(
    parent_fd: int,
    name: str,
    label: str,
    *,
    create: bool,
) -> int | None:
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        except OSError as error:
            raise DeploymentError(f"{label} cannot be created") from error
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        if not create:
            return None
        raise DeploymentError(f"{label} disappeared after creation") from None
    except OSError as error:
        raise DeploymentError(f"{label} cannot be inspected") from error
    before_mode = stat.S_IMODE(before.st_mode)
    if (
        not stat.S_ISDIR(before.st_mode)
        or before.st_uid != os.geteuid()
        or before_mode & ~0o700
    ):
        raise DeploymentError(f"{label} pending directory binding disagrees")
    stable_identity = (before.st_dev, before.st_ino)
    if before_mode == 0o700:
        normalized = before
    else:
        try:
            os.chmod(name, 0o700, dir_fd=parent_fd)
            normalized = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as error:
            raise DeploymentError(f"{label} cannot be normalized") from error
    if (
        (normalized.st_dev, normalized.st_ino) != stable_identity
        or not stat.S_ISDIR(normalized.st_mode)
        or normalized.st_uid != os.geteuid()
        or stat.S_IMODE(normalized.st_mode) != 0o700
    ):
        raise DeploymentError(f"{label} changed during normalization")
    descriptor = -1
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(normalized):
            raise DeploymentError(f"{label} changed while it was opened")
        os.fsync(descriptor)
        os.fsync(parent_fd)
        visible = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if _identity(visible) != _identity(opened):
            raise DeploymentError(f"{label} changed while it was synchronized")
        return descriptor
    except DeploymentError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise DeploymentError(f"{label} cannot be synchronized") from error


def _install_activation_artifact(
    canonical_root_fd: int,
    canonical_root: Path,
    artifact: StagedArtifact,
    *,
    transaction_id: str,
    step_index: int,
    directory_repair_paths: frozenset[str] = frozenset(),
) -> None:
    relative_path, components = _relative_path(
        artifact.relative_path,
        f"activation {artifact.role} path",
    )
    if artifact.installed_path != canonical_root / relative_path:
        raise DeploymentError(f"activation {artifact.role} path binding disagrees")
    current = os.dup(canonical_root_fd)
    directories = [current]
    try:
        parent_paths = _activation_artifact_parent_directories(artifact)
        if not directory_repair_paths.issubset(parent_paths):
            raise DeploymentError(
                f"activation {artifact.role} directory repair program disagrees"
            )
        for component_index, component in enumerate(components[:-1], start=1):
            relative_directory = "/".join(components[:component_index])
            if relative_directory in directory_repair_paths:
                child = _open_activation_install_directory(
                    current,
                    component,
                    f"installed {artifact.role} directory",
                    create=True,
                )
                if child is None:
                    raise DeploymentError(
                        f"installed {artifact.role} directory cannot be created"
                    )
            else:
                child = _open_private_directory(
                    current,
                    component,
                    f"installed {artifact.role} directory",
                    create=False,
                )
                os.fsync(current)
            directories.append(child)
            current = child
        name = components[-1]
        temporary = _activation_artifact_temporary_name(
            transaction_id,
            step_index,
        )
        final_entry = _read_activation_artifact_entry(
            current,
            name,
            artifact,
            label=f"installed {artifact.role}",
            allowed_nlinks={1, 2},
            absent=True,
        )
        temporary_entry = _read_activation_install_temporary(
            current,
            temporary,
            artifact,
            allowed_nlinks={1, 2},
            absent=True,
        )
        if final_entry is not None:
            final_metadata, final_raw = final_entry
            if final_raw != artifact.raw:
                raise DeploymentError(f"installed {artifact.role} binding disagrees")
            if final_metadata.st_nlink == 2:
                if temporary_entry is None:
                    raise DeploymentError(
                        f"installed {artifact.role} link ownership disagrees"
                    )
                temporary_metadata, temporary_raw = temporary_entry
                if (
                    temporary_metadata.st_nlink != 2
                    or (temporary_metadata.st_dev, temporary_metadata.st_ino)
                    != (final_metadata.st_dev, final_metadata.st_ino)
                    or not _activation_install_temporary_is_owned(
                        temporary_metadata,
                        temporary_raw,
                        artifact,
                    )
                ):
                    raise DeploymentError(
                        f"installed {artifact.role} link ownership disagrees"
                    )
                try:
                    _unlink_activation_install_temporary(
                        current,
                        temporary,
                        temporary_metadata,
                        artifact,
                    )
                    os.fsync(current)
                except OSError as error:
                    raise DeploymentError(
                        f"installed {artifact.role} temporary cannot be reconciled"
                    ) from error
                _verify_activation_artifact(current, name, artifact)
                return
            if temporary_entry is not None:
                temporary_metadata, temporary_raw = temporary_entry
                if not _activation_install_temporary_is_owned(
                    temporary_metadata,
                    temporary_raw,
                    artifact,
                ):
                    raise DeploymentError(
                        f"activation {artifact.role} transaction temporary disagrees"
                    )
                _unlink_activation_install_temporary(
                    current,
                    temporary,
                    temporary_metadata,
                    artifact,
                )
            try:
                os.fsync(current)
            except OSError as error:
                raise DeploymentError(
                    f"installed {artifact.role} directory cannot be synchronized"
                ) from error
            _verify_activation_artifact(current, name, artifact)
            return

        if temporary_entry is not None:
            temporary_metadata, temporary_raw = temporary_entry
            if not _activation_install_temporary_is_owned(
                temporary_metadata,
                temporary_raw,
                artifact,
            ):
                raise DeploymentError(
                    f"activation {artifact.role} transaction temporary disagrees"
                )
            _unlink_activation_install_temporary(
                current,
                temporary,
                temporary_metadata,
                artifact,
            )

        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
        descriptor = -1
        try:
            descriptor = os.open(
                temporary,
                flags,
                0,
                dir_fd=current,
            )
        except FileExistsError:
            raise DeploymentError(
                f"activation {artifact.role} transaction temporary already exists"
            ) from None
        except OSError as error:
            raise DeploymentError(
                f"activation {artifact.role} transaction temporary cannot be created"
            ) from error
        try:
            created = os.fstat(descriptor)
            if (
                not stat.S_ISREG(created.st_mode)
                or created.st_uid != artifact.installed["owner"]
                or created.st_nlink != 1
                or stat.S_IMODE(created.st_mode) != 0
            ):
                raise DeploymentError(
                    f"activation {artifact.role} transaction temporary binding disagrees"
                )
            os.fchmod(descriptor, 0o600)
            writable = os.fstat(descriptor)
            if (
                (writable.st_dev, writable.st_ino) != (created.st_dev, created.st_ino)
                or not stat.S_ISREG(writable.st_mode)
                or writable.st_uid != artifact.installed["owner"]
                or writable.st_nlink != 1
                or stat.S_IMODE(writable.st_mode) != 0o600
            ):
                raise DeploymentError(
                    f"activation {artifact.role} transaction temporary binding disagrees"
                )
            _write_all(
                descriptor,
                artifact.raw,
                f"activation {artifact.role} transaction temporary",
            )
            written = os.fstat(descriptor)
            if (
                (written.st_dev, written.st_ino) != (created.st_dev, created.st_ino)
                or not stat.S_ISREG(written.st_mode)
                or written.st_uid != artifact.installed["owner"]
                or written.st_nlink != 1
                or stat.S_IMODE(written.st_mode) != 0o600
                or _read_descriptor(
                    descriptor,
                    max(len(artifact.raw), 1),
                    f"activation {artifact.role} transaction temporary",
                )
                != artifact.raw
            ):
                raise DeploymentError(
                    f"activation {artifact.role} transaction temporary binding disagrees"
                )
            os.fchmod(descriptor, artifact.installed["mode"])
            os.fsync(descriptor)
            ready = os.fstat(descriptor)
            if (
                (ready.st_dev, ready.st_ino) != (created.st_dev, created.st_ino)
                or not stat.S_ISREG(ready.st_mode)
                or ready.st_uid != artifact.installed["owner"]
                or ready.st_nlink != 1
                or stat.S_IMODE(ready.st_mode) != artifact.installed["mode"]
                or _read_descriptor(
                    descriptor,
                    max(len(artifact.raw), 1),
                    f"activation {artifact.role} transaction temporary",
                )
                != artifact.raw
            ):
                raise DeploymentError(
                    f"activation {artifact.role} transaction temporary binding disagrees"
                )
        finally:
            os.close(descriptor)

        ready_entry = _read_activation_artifact_entry(
            current,
            temporary,
            artifact,
            label=f"activation {artifact.role} transaction temporary",
            allowed_nlinks={1},
        )
        if ready_entry is None or ready_entry[1] != artifact.raw:
            raise DeploymentError(
                f"activation {artifact.role} transaction temporary binding disagrees"
            )
        try:
            os.link(
                temporary,
                name,
                src_dir_fd=current,
                dst_dir_fd=current,
                follow_symlinks=False,
            )
        except FileExistsError:
            raise DeploymentError(
                f"installed {artifact.role} cannot be published without overwrite"
            ) from None
        except OSError as error:
            raise DeploymentError(
                f"installed {artifact.role} cannot be published"
            ) from error

        linked_temporary = _read_activation_artifact_entry(
            current,
            temporary,
            artifact,
            label=f"activation {artifact.role} linked transaction temporary",
            allowed_nlinks={2},
        )
        linked_final = _read_activation_artifact_entry(
            current,
            name,
            artifact,
            label=f"installed {artifact.role}",
            allowed_nlinks={2},
        )
        if (
            linked_temporary is None
            or linked_final is None
            or linked_temporary[1] != artifact.raw
            or linked_final[1] != artifact.raw
            or (linked_temporary[0].st_dev, linked_temporary[0].st_ino)
            != (linked_final[0].st_dev, linked_final[0].st_ino)
        ):
            raise DeploymentError(f"installed {artifact.role} link binding disagrees")
        try:
            _unlink_activation_install_temporary(
                current,
                temporary,
                linked_temporary[0],
                artifact,
            )
            os.fsync(current)
        except OSError as error:
            raise DeploymentError(
                f"installed {artifact.role} publication cannot be synchronized"
            ) from error
        _verify_activation_artifact(current, name, artifact)
    finally:
        for descriptor in reversed(directories):
            os.close(descriptor)


def _activation_smoke_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except OSError as error:
        if error.errno == errno.ESRCH:
            return False
        if error.errno == errno.EPERM:
            return True
        raise DeploymentError(
            "activation smoke process group cannot be observed"
        ) from error
    return True


def _signal_activation_smoke_group(process_group: int, number: int) -> bool:
    try:
        os.killpg(process_group, number)
    except ProcessLookupError:
        return False
    except OSError as error:
        if error.errno == errno.ESRCH:
            return False
        if error.errno == errno.EPERM:
            return False
        raise DeploymentError(
            "activation smoke process group cannot be signaled"
        ) from error
    return True


def _activation_smoke_leader_exited(
    process: subprocess.Popen[bytes],
) -> bool:
    try:
        return (
            os.waitid(
                os.P_PID,
                process.pid,
                os.WEXITED | os.WNOHANG | os.WNOWAIT,
            )
            is not None
        )
    except ChildProcessError as error:
        raise _ActivationSmokeOwnershipError(
            "activation smoke child ownership was lost"
        ) from error
    except OSError as error:
        if error.errno == errno.ECHILD:
            raise _ActivationSmokeOwnershipError(
                "activation smoke child ownership was lost"
            ) from error
        raise DeploymentError("activation smoke child cannot be observed") from error


def _wait_activation_smoke_leader(
    process: subprocess.Popen[bytes],
    deadline: float,
) -> bool:
    while not _activation_smoke_leader_exited(process):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(remaining, 0.05))
    return True


def _wait_activation_smoke_group_gone(
    process_group: int,
    deadline: float,
) -> bool:
    while _activation_smoke_group_exists(process_group):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(remaining, 0.05))
    return True


def _reap_activation_smoke_leader(
    process: subprocess.Popen[bytes],
    deadline: float,
) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DeploymentError("activation smoke child cannot be boundedly reaped")
    try:
        process.wait(timeout=remaining)
    except subprocess.TimeoutExpired as error:
        raise DeploymentError(
            "activation smoke child cannot be boundedly reaped"
        ) from error
    except (ChildProcessError, OSError) as error:
        raise _ActivationSmokeOwnershipError(
            "activation smoke child ownership was lost"
        ) from error
    if process.returncode is None:
        raise DeploymentError("activation smoke child cannot be boundedly reaped")


def _close_activation_smoke_pipes(process: subprocess.Popen[bytes]) -> None:
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


def _best_effort_force_activation_smoke_process(
    process: subprocess.Popen[bytes],
) -> None:
    _close_activation_smoke_pipes(process)
    try:
        _activation_smoke_leader_exited(process)
    except _ActivationSmokeOwnershipError:
        return
    except DeploymentError:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=PROCESS_PROFILE["kill_reap_seconds"])
    except (ChildProcessError, OSError, subprocess.TimeoutExpired):
        pass


def _terminate_activation_smoke_process_strict(
    process: subprocess.Popen[bytes],
) -> None:
    _close_activation_smoke_pipes(process)
    term_delivered = _signal_activation_smoke_group(process.pid, signal.SIGTERM)
    if not term_delivered and not _activation_smoke_leader_exited(process):
        raise DeploymentError("activation smoke process group cannot be signaled")
    grace_deadline = time.monotonic() + PROCESS_PROFILE["termination_grace_seconds"]
    _wait_activation_smoke_leader(process, grace_deadline)
    if _activation_smoke_group_exists(process.pid):
        kill_delivered = _signal_activation_smoke_group(process.pid, signal.SIGKILL)
        if not kill_delivered and not _activation_smoke_leader_exited(process):
            raise DeploymentError("activation smoke process group cannot be killed")
    kill_deadline = time.monotonic() + PROCESS_PROFILE["kill_reap_seconds"]
    _reap_activation_smoke_leader(process, kill_deadline)
    if not _wait_activation_smoke_group_gone(process.pid, kill_deadline):
        raise DeploymentError("activation smoke process group did not terminate")


def _terminate_activation_smoke_process(process: subprocess.Popen[bytes]) -> None:
    try:
        _terminate_activation_smoke_process_strict(process)
    except DeploymentError:
        _best_effort_force_activation_smoke_process(process)
        raise


def _spawn_activation_smoke_process(
    argv: tuple[str, ...],
    pass_fds: tuple[int, ...],
) -> subprocess.Popen[bytes]:
    try:
        return subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=PROCESS_PROFILE["cwd"],
            env=dict(PROCESS_PROFILE["environment"]),
            close_fds=PROCESS_PROFILE["close_fds"],
            pass_fds=pass_fds,
            start_new_session=PROCESS_PROFILE["new_session"],
            restore_signals=PROCESS_PROFILE["restore_signals"],
            umask=PROCESS_PROFILE["umask"],
            bufsize=0,
        )
    except OSError as error:
        raise DeploymentError("activation smoke child cannot be started") from error


def _read_activation_smoke_output(
    process: subprocess.Popen[bytes],
    deadline: float,
) -> tuple[bytes, bytes, str | None, bool]:
    if process.stdout is None or process.stderr is None:
        raise DeploymentError("activation smoke output pipes are unavailable")
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {
        "stdout": PROCESS_PROFILE["stdout_max_bytes"],
        "stderr": PROCESS_PROFILE["stderr_max_bytes"],
    }
    overflow: str | None = None
    timed_out = False
    pipe_drain_deadline: float | None = None
    try:
        selector = selectors.DefaultSelector()
        with selector:
            for name, stream in (
                ("stdout", process.stdout),
                ("stderr", process.stderr),
            ):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream.fileno(), selectors.EVENT_READ, name)
            while selector.get_map() and overflow is None:
                now = time.monotonic()
                if (
                    _activation_smoke_leader_exited(process)
                    and pipe_drain_deadline is None
                ):
                    pipe_drain_deadline = (
                        now + PROCESS_PROFILE["post_leader_pipe_drain_seconds"]
                    )
                current_deadline = (
                    min(deadline, pipe_drain_deadline)
                    if pipe_drain_deadline is not None
                    else deadline
                )
                remaining = current_deadline - now
                if remaining <= 0:
                    timed_out = True
                    break
                try:
                    ready = selector.select(min(remaining, 0.05))
                except InterruptedError:
                    continue
                for key, _ in ready:
                    name = key.data
                    retained = buffers[name]
                    available = limits[name] - len(retained)
                    try:
                        chunk = os.read(
                            key.fd,
                            min(
                                PROCESS_PROFILE["io_chunk_bytes"],
                                available + 1,
                            ),
                        )
                    except (BlockingIOError, InterruptedError):
                        continue
                    if not chunk:
                        selector.unregister(key.fd)
                        continue
                    if len(chunk) > available:
                        retained.extend(chunk[:available])
                        overflow = name
                        break
                    retained.extend(chunk)
    except OSError as error:
        raise DeploymentError("activation smoke output cannot be read") from error
    return bytes(buffers["stdout"]), bytes(buffers["stderr"]), overflow, timed_out


def _spawn_activation_smoke_child(
    argv: tuple[str, ...],
    *,
    pass_fds: tuple[int, ...],
) -> subprocess.CompletedProcess[bytes]:
    deadline = time.monotonic() + PROCESS_PROFILE["validation_deadline_seconds"]
    process = _spawn_activation_smoke_process(argv, pass_fds)
    try:
        stdout, stderr, overflow, timed_out = _read_activation_smoke_output(
            process,
            deadline,
        )
    except _ActivationSmokeOwnershipError:
        _close_activation_smoke_pipes(process)
        raise
    except DeploymentError:
        _terminate_activation_smoke_process(process)
        raise
    if overflow is not None:
        _terminate_activation_smoke_process(process)
        return subprocess.CompletedProcess(
            argv,
            124,
            stdout=b"",
            stderr=b"",
        )
    if not timed_out:
        remaining = deadline - time.monotonic()
        if remaining > 0:
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                timed_out = True
            except (ChildProcessError, OSError) as error:
                _close_activation_smoke_pipes(process)
                _best_effort_force_activation_smoke_process(process)
                raise _ActivationSmokeOwnershipError(
                    "activation smoke child ownership was lost"
                ) from error
        elif _activation_smoke_leader_exited(process):
            try:
                _reap_activation_smoke_leader(
                    process,
                    time.monotonic() + PROCESS_PROFILE["kill_reap_seconds"],
                )
            except DeploymentError:
                _best_effort_force_activation_smoke_process(process)
                raise
        else:
            timed_out = True
    if timed_out:
        _terminate_activation_smoke_process(process)
        return subprocess.CompletedProcess(argv, 124, stdout=b"", stderr=b"")
    _close_activation_smoke_pipes(process)
    return subprocess.CompletedProcess(
        argv,
        process.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _run_activation_smoke(
    canonical_root: Path,
    activation_lock_fd: int,
) -> subprocess.CompletedProcess[bytes]:
    original_fd3_present = False
    backup_fd = -1
    original_flags = 0
    fd3_replaced = False
    try:
        try:
            original_flags = fcntl.fcntl(3, fcntl.F_GETFD)
        except OSError as error:
            if error.errno != errno.EBADF:
                raise
        else:
            original_fd3_present = True
            backup_fd = os.dup(3)
        os.dup2(activation_lock_fd, 3, inheritable=True)
        fd3_replaced = True
        return _spawn_activation_smoke_child(
            (str(canonical_root / "task-witness"), "activation-smoke"),
            pass_fds=(3,),
        )
    except OSError as error:
        raise DeploymentError("activation lock cannot be inherited by smoke") from error
    finally:
        cleanup_error: OSError | None = None
        if fd3_replaced:
            try:
                if original_fd3_present:
                    os.dup2(
                        backup_fd,
                        3,
                        inheritable=not bool(original_flags & fcntl.FD_CLOEXEC),
                    )
                    fcntl.fcntl(3, fcntl.F_SETFD, original_flags)
                else:
                    os.close(3)
            except OSError as error:
                if original_fd3_present or error.errno != errno.EBADF:
                    cleanup_error = error
        if backup_fd >= 0:
            try:
                os.close(backup_fd)
            except OSError as error:
                if cleanup_error is None:
                    cleanup_error = error
        if cleanup_error is not None:
            raise DeploymentError(
                "activation smoke caller descriptor cannot be restored"
            ) from cleanup_error


def _transaction_result_names(transaction_id: str) -> tuple[str, str]:
    transaction = _sha256(transaction_id, "activation transaction id")
    return (
        f"sha256-{transaction}.json",
        f".task-witness-result-{transaction}.tmp",
    )


def _transaction_result_directory_temporary_name(transaction_id: str) -> str:
    transaction = _sha256(transaction_id, "activation transaction id")
    return f".task-witness-results-{transaction}.tmp"


def _open_transaction_result_directory(
    canonical_root_fd: int,
    journal: _ActivationJournal,
    *,
    create: bool,
) -> int | None:
    """Open the exact result directory or publish one transaction-owned residue."""

    temporary_name = _transaction_result_directory_temporary_name(
        journal.value["transaction_id"]
    )
    try:
        temporary_names = {
            name
            for name in os.listdir(canonical_root_fd)
            if name.startswith(".task-witness-results-")
        }
    except OSError as error:
        raise DeploymentError(
            "pending transaction result directory inventory is unavailable"
        ) from error
    if not temporary_names.issubset({temporary_name}):
        raise DeploymentError(
            "pending transaction result directory inventory disagrees"
        )
    try:
        final = os.stat(
            "transaction-results",
            dir_fd=canonical_root_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        final = None
    except OSError as error:
        raise DeploymentError(
            "transaction result directory cannot be inspected"
        ) from error
    try:
        temporary = os.stat(
            temporary_name,
            dir_fd=canonical_root_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        temporary = None
    except OSError as error:
        raise DeploymentError(
            "pending transaction result directory cannot be inspected"
        ) from error
    if final is not None:
        if temporary is not None:
            raise DeploymentError(
                "pending transaction result directory inventory disagrees"
            )
        if (
            not stat.S_ISDIR(final.st_mode)
            or final.st_uid != os.geteuid()
            or stat.S_IMODE(final.st_mode) != 0o700
        ):
            raise DeploymentError(
                "pending transaction result directory binding disagrees"
            )
        descriptor = _open_private_directory(
            canonical_root_fd,
            "transaction-results",
            "pending transaction result directory",
            create=False,
        )
        try:
            _reject_macos_allow_acl(descriptor, "pending transaction result directory")
        except DeploymentError:
            os.close(descriptor)
            raise
        return descriptor
    if temporary is None:
        if not create:
            return None
        if (
            _read_activation_file(
                canonical_root_fd,
                "transaction.json",
                "activation transaction journal",
            )
            != journal.raw
        ):
            raise DeploymentError(
                "activation transaction journal changed before result directory creation"
            )
        try:
            os.mkdir(temporary_name, 0o700, dir_fd=canonical_root_fd)
            temporary = os.stat(
                temporary_name,
                dir_fd=canonical_root_fd,
                follow_symlinks=False,
            )
        except OSError as error:
            raise DeploymentError(
                "pending transaction result directory cannot be created"
            ) from error
    mode = stat.S_IMODE(temporary.st_mode)
    if (
        not stat.S_ISDIR(temporary.st_mode)
        or temporary.st_uid != os.geteuid()
        or mode & ~0o700
    ):
        raise DeploymentError("pending transaction result directory binding disagrees")
    if (
        _read_activation_file(
            canonical_root_fd,
            "transaction.json",
            "activation transaction journal",
        )
        != journal.raw
    ):
        raise DeploymentError(
            "activation transaction journal changed before result directory repair"
        )
    stable_identity = (temporary.st_dev, temporary.st_ino)
    if mode != 0o700:
        try:
            os.chmod(temporary_name, 0o700, dir_fd=canonical_root_fd)
            normalized = os.stat(
                temporary_name,
                dir_fd=canonical_root_fd,
                follow_symlinks=False,
            )
        except OSError as error:
            raise DeploymentError(
                "pending transaction result directory cannot be normalized"
            ) from error
    else:
        normalized = temporary
    descriptor = -1
    try:
        descriptor = os.open(
            temporary_name,
            _DIRECTORY_FLAGS,
            dir_fd=canonical_root_fd,
        )
        opened = os.fstat(descriptor)
        _reject_macos_allow_acl(descriptor, "pending transaction result directory")
        if (
            (normalized.st_dev, normalized.st_ino) != stable_identity
            or _identity(normalized) != _identity(opened)
            or not stat.S_ISDIR(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != 0o700
            or os.listdir(descriptor)
        ):
            raise DeploymentError(
                "pending transaction result directory binding disagrees"
            )
        os.fsync(descriptor)
        os.fsync(canonical_root_fd)
        try:
            os.stat(
                "transaction-results",
                dir_fd=canonical_root_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise DeploymentError(
                "transaction result directory appeared before publication"
            )
        os.rename(
            temporary_name,
            "transaction-results",
            src_dir_fd=canonical_root_fd,
            dst_dir_fd=canonical_root_fd,
        )
        os.fsync(canonical_root_fd)
        visible = os.stat(
            "transaction-results",
            dir_fd=canonical_root_fd,
            follow_symlinks=False,
        )
        published = os.fstat(descriptor)
        if (
            _identity(visible) != _identity(published)
            or (published.st_dev, published.st_ino) != stable_identity
            or stat.S_IMODE(published.st_mode) != 0o700
            or published.st_uid != os.geteuid()
            or os.listdir(descriptor)
        ):
            raise DeploymentError(
                "transaction result directory changed during publication"
            )
        try:
            os.stat(
                temporary_name,
                dir_fd=canonical_root_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return descriptor
        raise DeploymentError(
            "pending transaction result directory remains after publication"
        )
    except DeploymentError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise DeploymentError(
            "pending transaction result directory cannot be published"
        ) from error


def _transaction_result_sha256s(
    raws: Mapping[str, bytes],
) -> Mapping[str, str]:
    return _freeze(
        {
            relative: hashlib.sha256(raw).hexdigest()
            for relative, raw in sorted(raws.items())
        }
    )


def _validate_transaction_result_baseline(
    raws: Mapping[str, bytes],
    expected: Mapping[str, str],
    *,
    pending: _ActivationJournal | None = None,
) -> None:
    observed = dict(_transaction_result_sha256s(raws))
    if pending is not None:
        final_name, _ = _transaction_result_names(pending.value["transaction_id"])
        relative = f"transaction-results/{final_name}"
        pending_digest = hashlib.sha256(pending.raw).hexdigest()
        if relative in observed and observed.pop(relative) != pending_digest:
            raise DeploymentError("pending transaction result baseline bytes disagree")
    if observed != dict(expected):
        raise DeploymentError("retained transaction result baseline disagrees")


def _capture_recorded_transaction_result_baseline(
    canonical_root_fd: int,
    canonical_root: Path,
    journal: _ActivationJournal,
    *,
    opaque_pending_temporary: bool = False,
) -> Mapping[str, bytes]:
    pending = (
        journal
        if journal.value["phase"] == "terminal"
        and journal.value["terminal_result"]["outcome"] != "recovery-required"
        else None
    )
    raws, _, _ = _capture_transaction_result_inventory(
        canonical_root_fd,
        canonical_root,
        pending=pending,
        opaque_pending_temporary=opaque_pending_temporary,
    )
    baseline = dict(raws)
    if pending is not None:
        final_name, _ = _transaction_result_names(pending.value["transaction_id"])
        relative = f"transaction-results/{final_name}"
        if relative in baseline and baseline.pop(relative) != pending.raw:
            raise DeploymentError("pending transaction result bytes disagree")
    return _freeze(baseline)


def _rebind_recorded_first_install_result_baseline(
    canonical_root_fd: int,
    recorded: FirstInstallPrecondition,
    journal: _ActivationJournal,
    *,
    opaque_pending_temporary: bool = False,
) -> FirstInstallPrecondition:
    pending = (
        journal
        if journal.value["phase"] == "terminal"
        and journal.value["terminal_result"]["outcome"] != "recovery-required"
        else None
    )
    raws, _, _ = _capture_transaction_result_inventory(
        canonical_root_fd,
        recorded.canonical_root,
        pending=pending,
        opaque_pending_temporary=opaque_pending_temporary,
    )
    baseline = dict(raws)
    if pending is not None:
        final_name, _ = _transaction_result_names(pending.value["transaction_id"])
        relative = f"transaction-results/{final_name}"
        if relative in baseline and baseline.pop(relative) != pending.raw:
            raise DeploymentError(
                "pending first-install transaction result bytes disagree"
            )
    return FirstInstallPrecondition(
        canonical_root=recorded.canonical_root,
        root_identity=recorded.root_identity,
        activation_lock=recorded.activation_lock,
        activation_lock_identity=recorded.activation_lock_identity,
        deployment_receipt_absent=recorded.deployment_receipt_absent,
        retained_result_sha256s=_transaction_result_sha256s(baseline),
    )


def _validated_terminal_result_journal(
    raw: bytes,
    label: str,
) -> _ActivationJournal:
    journal = _parse_activation_journal(raw)
    terminal = journal.value["terminal_result"]
    if (
        journal.value["phase"] != "terminal"
        or terminal is None
        or terminal["outcome"] == "recovery-required"
    ):
        raise DeploymentError(f"{label} is not a retainable terminal result")
    return journal


def _read_transaction_result_entry(
    parent_fd: int,
    name: str,
    label: str,
    *,
    allowed_nlinks: frozenset[int],
    absent: bool = False,
) -> tuple[os.stat_result, bytes] | None:
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        if absent:
            return None
        raise DeploymentError(f"{label} is unavailable") from None
    except OSError as error:
        raise DeploymentError(f"{label} is unavailable") from error
    try:
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        raise DeploymentError(f"{label} is unavailable") from error
    try:
        after = os.fstat(descriptor)
        if (
            _identity(before) != _identity(after)
            or not stat.S_ISREG(after.st_mode)
            or after.st_uid != os.geteuid()
            or after.st_nlink not in allowed_nlinks
            or stat.S_IMODE(after.st_mode) != 0o600
        ):
            raise DeploymentError(f"{label} disposition disagrees")
        return after, _read_descriptor(descriptor, MAX_JSON_BYTES, label)
    finally:
        os.close(descriptor)


def _capture_transaction_result_inventory(
    canonical_root_fd: int,
    canonical_root: Path,
    *,
    pending: _ActivationJournal | None = None,
    opaque_pending_temporary: bool = False,
) -> tuple[
    Mapping[str, bytes],
    _TransactionResultRetentionState,
    tuple[frozenset[str], frozenset[str]],
]:
    """Capture the closed retained-result directory and one optional live suffix."""

    pending_final: str | None = None
    pending_temporary: str | None = None
    if pending is not None:
        pending = _validated_terminal_result_journal(
            pending.raw,
            "pending activation transaction result",
        )
        pending_final, pending_temporary = _transaction_result_names(
            pending.value["transaction_id"]
        )
        if pending.value["canonical_root"] != str(canonical_root):
            raise DeploymentError("pending transaction result root disagrees")
    pending_directory_temporary: str | None = None
    if pending is not None:
        pending_directory_temporary = _transaction_result_directory_temporary_name(
            pending.value["transaction_id"]
        )
    if opaque_pending_temporary:
        try:
            temporary_directories = {
                name
                for name in os.listdir(canonical_root_fd)
                if name.startswith(".task-witness-results-")
            }
        except OSError as error:
            raise DeploymentError(
                "pending transaction result directory inventory is unavailable"
            ) from error
        if not temporary_directories.issubset(
            {pending_directory_temporary}
            if pending_directory_temporary is not None
            else set()
        ):
            raise DeploymentError(
                "pending transaction result directory inventory disagrees"
            )
    else:
        temporary_directories = set()
    try:
        before = os.stat(
            "transaction-results",
            dir_fd=canonical_root_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        if temporary_directories:
            temporary_name = next(iter(temporary_directories))
            try:
                temporary = os.stat(
                    temporary_name,
                    dir_fd=canonical_root_fd,
                    follow_symlinks=False,
                )
            except OSError as error:
                raise DeploymentError(
                    "pending transaction result directory is unavailable"
                ) from error
            mode = stat.S_IMODE(temporary.st_mode)
            if (
                pending is None
                or temporary_name != pending_directory_temporary
                or not stat.S_ISDIR(temporary.st_mode)
                or temporary.st_uid != os.geteuid()
                or temporary.st_nlink != 2
                or mode & ~0o700
            ):
                raise DeploymentError(
                    "pending transaction result directory binding disagrees"
                )
            return (
                MappingProxyType({}),
                _TransactionResultRetentionState(False, None, None),
                (frozenset({temporary_name}), frozenset()),
            )
        return (
            MappingProxyType({}),
            _TransactionResultRetentionState(False, None, None),
            (frozenset(), frozenset()),
        )
    except OSError as error:
        raise DeploymentError("transaction result directory is unavailable") from error
    if temporary_directories:
        raise DeploymentError(
            "pending transaction result directory inventory disagrees"
        )
    if not stat.S_ISDIR(before.st_mode):
        raise DeploymentError("transaction result directory is not a real directory")
    try:
        descriptor = os.open(
            "transaction-results",
            _DIRECTORY_FLAGS,
            dir_fd=canonical_root_fd,
        )
    except OSError as error:
        raise DeploymentError(
            "transaction result directory cannot be opened"
        ) from error
    raws: dict[str, bytes] = {}
    observed_final: str | None = None
    observed_temporary: str | None = None
    try:
        after = os.fstat(descriptor)
        _reject_macos_allow_acl(descriptor, "transaction result directory")
        if (
            _identity(before) != _identity(after)
            or after.st_uid != os.geteuid()
            or stat.S_IMODE(after.st_mode) != 0o700
        ):
            if opaque_pending_temporary:
                raise DeploymentError(
                    "pending transaction result directory binding disagrees"
                )
            raise DeploymentError("transaction result directory is not private")
        try:
            names = tuple(sorted(os.listdir(descriptor)))
        except OSError as error:
            raise DeploymentError(
                "transaction result inventory is unavailable"
            ) from error
        for name in names:
            if name == pending_temporary:
                try:
                    temporary_metadata = os.stat(
                        name,
                        dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                except OSError as error:
                    raise DeploymentError(
                        "pending transaction result temporary is unavailable"
                    ) from error
                if (
                    opaque_pending_temporary
                    and stat.S_IMODE(temporary_metadata.st_mode) != 0o600
                ):
                    mode = stat.S_IMODE(temporary_metadata.st_mode)
                    if (
                        pending is None
                        or not stat.S_ISREG(temporary_metadata.st_mode)
                        or temporary_metadata.st_uid != os.geteuid()
                        or temporary_metadata.st_nlink != 1
                        or temporary_metadata.st_size != 0
                        or mode & ~0o600
                    ):
                        raise DeploymentError(
                            "pending transaction result temporary disposition disagrees"
                        )
                else:
                    temporary_entry = _read_transaction_result_entry(
                        descriptor,
                        name,
                        "pending transaction result temporary",
                        allowed_nlinks=frozenset({1, 2}),
                    )
                    if temporary_entry is None:
                        raise DeploymentError(
                            "pending transaction result temporary is unavailable"
                        )
                    temporary_raw = temporary_entry[1]
                    if pending is None or not pending.raw.startswith(temporary_raw):
                        raise DeploymentError(
                            "pending transaction result temporary bytes disagree"
                        )
                observed_temporary = name
                continue
            if not name.startswith("sha256-") or not name.endswith(".json"):
                raise DeploymentError("transaction result filename disagrees")
            transaction_id = name[len("sha256-") : -len(".json")]
            _sha256(transaction_id, "retained transaction result identity")
            result_entry = _read_transaction_result_entry(
                descriptor,
                name,
                "retained transaction result",
                allowed_nlinks=(
                    frozenset({1, 2}) if name == pending_final else frozenset({1})
                ),
            )
            if result_entry is None:
                raise DeploymentError("retained transaction result is unavailable")
            raw = result_entry[1]
            journal = _validated_terminal_result_journal(
                raw,
                "retained transaction result",
            )
            if (
                journal.value["transaction_id"] != transaction_id
                or journal.value["canonical_root"] != str(canonical_root)
                or journal.value["effective_uid"] != os.geteuid()
            ):
                raise DeploymentError("retained transaction result binding disagrees")
            relative = f"transaction-results/{name}"
            raws[relative] = raw
            if name == pending_final:
                if pending is None or raw != pending.raw:
                    raise DeploymentError(
                        "pending transaction result final bytes disagree"
                    )
                observed_final = name
        if observed_temporary is not None and observed_final is not None:
            temporary = os.stat(
                observed_temporary,
                dir_fd=descriptor,
                follow_symlinks=False,
            )
            final = os.stat(
                observed_final,
                dir_fd=descriptor,
                follow_symlinks=False,
            )
            if (
                (temporary.st_dev, temporary.st_ino) != (final.st_dev, final.st_ino)
                or temporary.st_nlink != 2
                or final.st_nlink != 2
                or pending is None
                or raws[f"transaction-results/{observed_final}"] != pending.raw
            ):
                raise DeploymentError(
                    "pending transaction result publication binding disagrees"
                )
        elif observed_temporary is not None:
            temporary = os.stat(
                observed_temporary,
                dir_fd=descriptor,
                follow_symlinks=False,
            )
            if temporary.st_nlink != 1:
                raise DeploymentError(
                    "pending transaction result temporary binding disagrees"
                )
        elif observed_final is not None:
            final = os.stat(
                observed_final,
                dir_fd=descriptor,
                follow_symlinks=False,
            )
            if final.st_nlink != 1:
                raise DeploymentError(
                    "pending transaction result final binding disagrees"
                )
        if not names and pending is None:
            raise DeploymentError("transaction result directory is unexpectedly empty")
        if tuple(sorted(os.listdir(descriptor))) != names:
            raise DeploymentError("transaction result inventory changed")
        visible = os.stat(
            "transaction-results",
            dir_fd=canonical_root_fd,
            follow_symlinks=False,
        )
        if _identity(visible) != _identity(after):
            raise DeploymentError("transaction result directory changed")
    finally:
        os.close(descriptor)
    files = frozenset(raws) | (
        frozenset({f"transaction-results/{observed_temporary}"})
        if observed_temporary is not None
        else frozenset()
    )
    return (
        MappingProxyType(dict(raws)),
        _TransactionResultRetentionState(
            True,
            observed_final,
            observed_temporary,
        ),
        (frozenset({"transaction-results"}), files),
    )


def _retain_terminal_transaction_result(
    canonical_root_fd: int,
    canonical_root: Path,
    journal: _ActivationJournal,
) -> None:
    """Durably retain exact terminal authority before unlinking the live journal."""

    terminal = _validated_terminal_result_journal(
        journal.raw,
        "activation transaction result",
    )
    if terminal.value != journal.value:
        raise DeploymentError("activation transaction result parse disagrees")
    final_name, temporary_name = _transaction_result_names(
        journal.value["transaction_id"]
    )
    _capture_transaction_result_inventory(
        canonical_root_fd,
        canonical_root,
        pending=journal,
    )
    results_fd = _open_transaction_result_directory(
        canonical_root_fd,
        journal,
        create=True,
    )
    if results_fd is None:
        raise DeploymentError("transaction result directory cannot be created")
    try:
        os.fsync(results_fd)
        os.fsync(canonical_root_fd)
        final_entry = _read_transaction_result_entry(
            results_fd,
            final_name,
            "retained transaction result",
            allowed_nlinks=frozenset({1, 2}),
            absent=True,
        )
        final_raw = final_entry[1] if final_entry is not None else None
        try:
            temporary_before = os.stat(
                temporary_name,
                dir_fd=results_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            temporary_before = None
        except OSError as error:
            raise DeploymentError(
                "transaction result temporary is unavailable"
            ) from error
        if final_raw is not None:
            if final_raw != journal.raw:
                raise DeploymentError("retained transaction result bytes disagree")
            if temporary_before is not None:
                final_metadata = os.stat(
                    final_name,
                    dir_fd=results_fd,
                    follow_symlinks=False,
                )
                if (
                    (temporary_before.st_dev, temporary_before.st_ino)
                    != (final_metadata.st_dev, final_metadata.st_ino)
                    or temporary_before.st_nlink != 2
                    or final_metadata.st_nlink != 2
                ):
                    raise DeploymentError(
                        "retained transaction result publication is contradictory"
                    )
                os.unlink(temporary_name, dir_fd=results_fd)
            os.fsync(results_fd)
            if (
                _read_transaction_result_entry(
                    results_fd,
                    final_name,
                    "retained transaction result",
                    allowed_nlinks=frozenset({1}),
                )[1]
                != journal.raw  # type: ignore[index]
            ):
                raise DeploymentError("retained transaction result reread disagrees")
            return

        descriptor = -1
        try:
            if temporary_before is None:
                descriptor = os.open(
                    temporary_name,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=results_fd,
                )
                os.fchmod(descriptor, 0o600)
                temporary_raw = b""
            else:
                descriptor = os.open(
                    temporary_name,
                    os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=results_fd,
                )
                temporary_after = os.fstat(descriptor)
                if (
                    _identity(temporary_before) != _identity(temporary_after)
                    or not stat.S_ISREG(temporary_after.st_mode)
                    or temporary_after.st_uid != os.geteuid()
                    or temporary_after.st_nlink != 1
                    or stat.S_IMODE(temporary_after.st_mode) != 0o600
                ):
                    raise DeploymentError(
                        "transaction result temporary binding disagrees"
                    )
                temporary_raw = _read_descriptor(
                    descriptor,
                    MAX_JSON_BYTES,
                    "transaction result temporary",
                )
            if not journal.raw.startswith(temporary_raw):
                raise DeploymentError("transaction result temporary bytes disagree")
            os.lseek(descriptor, len(temporary_raw), os.SEEK_SET)
            offset = len(temporary_raw)
            while offset < len(journal.raw):
                written = os.write(descriptor, journal.raw[offset:])
                if written <= 0:
                    raise DeploymentError(
                        "transaction result temporary write made no progress"
                    )
                offset += written
            os.fsync(descriptor)
        except DeploymentError:
            raise
        except OSError as error:
            raise DeploymentError("transaction result cannot be written") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        try:
            os.link(
                temporary_name,
                final_name,
                src_dir_fd=results_fd,
                dst_dir_fd=results_fd,
                follow_symlinks=False,
            )
            linked_temporary = os.stat(
                temporary_name,
                dir_fd=results_fd,
                follow_symlinks=False,
            )
            linked_final = os.stat(
                final_name,
                dir_fd=results_fd,
                follow_symlinks=False,
            )
            if (
                (linked_temporary.st_dev, linked_temporary.st_ino)
                != (linked_final.st_dev, linked_final.st_ino)
                or linked_temporary.st_nlink != 2
                or linked_final.st_nlink != 2
                or _read_transaction_result_entry(
                    results_fd,
                    final_name,
                    "retained transaction result",
                    allowed_nlinks=frozenset({2}),
                )[1]
                != journal.raw  # type: ignore[index]
            ):
                raise DeploymentError(
                    "retained transaction result publication binding disagrees"
                )
            os.unlink(temporary_name, dir_fd=results_fd)
            os.fsync(results_fd)
        except DeploymentError:
            raise
        except OSError as error:
            raise DeploymentError("transaction result cannot be published") from error
        if (
            _read_transaction_result_entry(
                results_fd,
                final_name,
                "retained transaction result",
                allowed_nlinks=frozenset({1}),
            )[1]
            != journal.raw  # type: ignore[index]
        ):
            raise DeploymentError("retained transaction result reread disagrees")
    finally:
        os.close(results_fd)


def _reconcile_pending_transaction_result_state(
    canonical_root_fd: int,
    journal: _ActivationJournal,
) -> None:
    """Normalize only transaction-owned result state after process loss."""

    if (
        journal.value["phase"] != "terminal"
        or journal.value["terminal_result"]["outcome"] == "recovery-required"
    ):
        return
    if (
        _read_activation_file(
            canonical_root_fd,
            "transaction.json",
            "activation transaction journal",
        )
        != journal.raw
    ):
        raise DeploymentError(
            "activation transaction journal changed before result directory repair"
        )
    results_fd = _open_transaction_result_directory(
        canonical_root_fd,
        journal,
        create=False,
    )
    if results_fd is None:
        return
    try:
        _, temporary_name = _transaction_result_names(journal.value["transaction_id"])
        try:
            temporary = os.stat(
                temporary_name,
                dir_fd=results_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            temporary = None
        except OSError as error:
            raise DeploymentError(
                "pending transaction result temporary cannot be inspected"
            ) from error
        if temporary is not None and stat.S_IMODE(temporary.st_mode) != 0o600:
            mode = stat.S_IMODE(temporary.st_mode)
            if (
                not stat.S_ISREG(temporary.st_mode)
                or temporary.st_uid != os.geteuid()
                or temporary.st_nlink != 1
                or temporary.st_size != 0
                or mode & ~0o600
            ):
                raise DeploymentError(
                    "pending transaction result temporary binding disagrees"
                )
            try:
                os.chmod(
                    temporary_name,
                    0o600,
                    dir_fd=results_fd,
                    follow_symlinks=False,
                )
                normalized = os.stat(
                    temporary_name,
                    dir_fd=results_fd,
                    follow_symlinks=False,
                )
                descriptor = os.open(
                    temporary_name,
                    _FILE_FLAGS,
                    dir_fd=results_fd,
                )
            except OSError as error:
                raise DeploymentError(
                    "pending transaction result temporary cannot be normalized"
                ) from error
            try:
                opened = os.fstat(descriptor)
                if (
                    (temporary.st_dev, temporary.st_ino)
                    != (normalized.st_dev, normalized.st_ino)
                    or _identity(normalized) != _identity(opened)
                    or not stat.S_ISREG(opened.st_mode)
                    or opened.st_uid != os.geteuid()
                    or opened.st_nlink != 1
                    or opened.st_size != 0
                    or stat.S_IMODE(opened.st_mode) != 0o600
                ):
                    raise DeploymentError(
                        "pending transaction result temporary changed during "
                        "normalization"
                    )
                os.fsync(descriptor)
                os.fsync(results_fd)
                os.fsync(canonical_root_fd)
            except DeploymentError:
                raise
            except OSError as error:
                raise DeploymentError(
                    "pending transaction result temporary cannot be synchronized"
                ) from error
            finally:
                os.close(descriptor)
    finally:
        os.close(results_fd)
    if (
        _read_activation_file(
            canonical_root_fd,
            "transaction.json",
            "activation transaction journal",
        )
        != journal.raw
    ):
        raise DeploymentError(
            "activation transaction journal changed during result state repair"
        )


def _unlink_activation_journal(
    canonical_root_fd: int,
    expected_raw: bytes,
) -> None:
    current = _read_activation_file(
        canonical_root_fd,
        "transaction.json",
        "activation transaction journal",
    )
    if current != expected_raw:
        raise DeploymentError("activation terminal journal changed before unlink")
    try:
        os.unlink("transaction.json", dir_fd=canonical_root_fd)
        os.fsync(canonical_root_fd)
    except OSError as error:
        raise DeploymentError(
            "activation terminal journal cannot be unlinked"
        ) from error


def _remove_activation_artifact(
    canonical_root_fd: int,
    artifact: StagedArtifact,
) -> None:
    _, components = _relative_path(
        artifact.relative_path,
        f"activation {artifact.role} removal path",
    )
    current = os.dup(canonical_root_fd)
    directories = [current]
    try:
        for component in components[:-1]:
            child = _open_private_directory(
                current,
                component,
                f"installed {artifact.role} directory",
                create=False,
            )
            directories.append(child)
            current = child
        try:
            os.stat(
                components[-1],
                dir_fd=current,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            os.fsync(current)
            return
        _verify_activation_artifact(current, components[-1], artifact)
        os.unlink(components[-1], dir_fd=current)
        os.fsync(current)
    except OSError as error:
        raise DeploymentError(f"installed {artifact.role} cannot be removed") from error
    finally:
        for descriptor in reversed(directories):
            os.close(descriptor)


def _remove_activation_directory(
    canonical_root_fd: int,
    relative_path: str,
) -> None:
    _, components = _relative_path(
        relative_path,
        "activation preimage directory path",
    )
    current = os.dup(canonical_root_fd)
    parents = [current]
    try:
        for component in components[:-1]:
            child = _open_private_directory(
                current,
                component,
                "activation preimage directory parent",
                create=False,
            )
            parents.append(child)
            current = child
        try:
            before = os.stat(
                components[-1],
                dir_fd=current,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            os.fsync(current)
            return
        except OSError as error:
            raise DeploymentError(
                "activation preimage directory is unavailable"
            ) from error
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISDIR(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o700
        ):
            raise DeploymentError("activation preimage directory binding disagrees")
        descriptor = _open_private_directory(
            current,
            components[-1],
            "activation preimage directory",
            create=False,
        )
        try:
            if os.listdir(descriptor):
                raise DeploymentError("activation preimage directory is not empty")
            if _identity(os.fstat(descriptor)) != _identity(before):
                raise DeploymentError("activation preimage directory changed")
        finally:
            os.close(descriptor)
        try:
            os.rmdir(components[-1], dir_fd=current)
            os.fsync(current)
        except OSError as error:
            raise DeploymentError(
                "activation preimage directory cannot be removed"
            ) from error
    finally:
        for descriptor in reversed(parents):
            os.close(descriptor)


def _activation_selector_temporary_name(
    transaction_id: str,
    direction: str,
    index: int,
) -> str:
    transaction = _sha256(transaction_id, "activation transaction id")
    selected_direction = _token(direction, "activation selector direction")
    selected_index = _nonnegative_integer(index, "activation selector index")
    return (
        f".task-witness-selector-{transaction}-{selected_direction}-"
        f"{selected_index}.tmp"
    )


def _control_maintenance_temporary_name(
    transaction_id: str,
    direction: str,
    index: int,
) -> str:
    transaction = _sha256(transaction_id, "control maintenance transaction id")
    selected_direction = _token(direction, "control maintenance direction")
    selected_index = _nonnegative_integer(index, "control maintenance step index")
    return (
        f".task-witness-control-{transaction}-{selected_direction}-{selected_index}.tmp"
    )


def _control_maintenance_live_relative_path(
    canonical_root: Path,
    artifact: StagedArtifact,
) -> tuple[str, tuple[str, ...]]:
    try:
        relative = artifact.installed_path.relative_to(canonical_root).as_posix()
    except ValueError as error:
        raise DeploymentError(
            f"control maintenance {artifact.role} escapes the canonical root"
        ) from error
    return _relative_path(
        relative,
        f"control maintenance {artifact.role} live path",
    )


def _read_control_maintenance_live_artifact(
    parent_fd: int,
    name: str,
    current: StagedArtifact,
    target: StagedArtifact,
) -> bytes:
    label = f"control maintenance {target.role} live artifact"
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        raise DeploymentError(f"{label} is unavailable") from error
    try:
        after = os.fstat(descriptor)
        if (
            _identity(before) != _identity(after)
            or not stat.S_ISREG(after.st_mode)
            or after.st_uid != target.installed["owner"]
            or after.st_nlink != 1
            or stat.S_IMODE(after.st_mode) != target.installed["mode"]
            or current.installed["owner"] != target.installed["owner"]
            or current.installed["mode"] != target.installed["mode"]
        ):
            raise DeploymentError(f"{label} binding disagrees")
        return _read_descriptor(
            descriptor,
            max(len(current.raw), len(target.raw), 1),
            label,
        )
    finally:
        os.close(descriptor)


def _read_control_maintenance_temporary(
    parent_fd: int,
    name: str,
    target: StagedArtifact,
    *,
    absent: bool = False,
) -> tuple[os.stat_result, bytes] | None:
    label = f"control maintenance {target.role} temporary"
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if absent:
            return None
        raise DeploymentError(f"{label} is unavailable") from None
    except OSError as error:
        raise DeploymentError(f"{label} is unavailable") from error
    try:
        after = os.fstat(descriptor)
        if (
            _identity(before) != _identity(after)
            or not stat.S_ISREG(after.st_mode)
            or after.st_uid != target.installed["owner"]
            or after.st_nlink != 1
            or stat.S_IMODE(after.st_mode) not in {0o600, target.installed["mode"]}
            or after.st_size > len(target.raw)
        ):
            raise DeploymentError(f"{label} binding disagrees")
        raw = _read_descriptor(descriptor, max(len(target.raw), 1), label)
        if not target.raw.startswith(raw):
            raise DeploymentError(f"{label} bytes disagree")
        return after, raw
    finally:
        os.close(descriptor)


def _replace_control_maintenance_artifact(
    canonical_root_fd: int,
    canonical_root: Path,
    *,
    transaction_id: str,
    direction: str,
    index: int,
    replacement: _ControlMaintenanceReplacement,
) -> None:
    if direction not in {"candidate", "prior"}:
        raise DeploymentError("control maintenance replacement direction disagrees")
    current, target = (
        (replacement.current, replacement.target)
        if direction == "candidate"
        else (replacement.target, replacement.current)
    )
    if (
        replacement.role != CONTROL_MAINTENANCE_REPLACEMENT_ROLES[index]
        or current.installed_path != target.installed_path
    ):
        raise DeploymentError("control maintenance replacement binding disagrees")
    _, components = _control_maintenance_live_relative_path(
        canonical_root,
        target,
    )
    current_fd = os.dup(canonical_root_fd)
    parents = [current_fd]
    try:
        for component in components[:-1]:
            child = _open_private_directory(
                current_fd,
                component,
                f"control maintenance {replacement.role} parent",
                create=False,
            )
            parents.append(child)
            current_fd = child
        name = components[-1]
        temporary = _control_maintenance_temporary_name(
            transaction_id,
            direction,
            index,
        )
        live = _read_control_maintenance_live_artifact(
            current_fd,
            name,
            current,
            target,
        )
        temporary_entry = _read_control_maintenance_temporary(
            current_fd,
            temporary,
            target,
            absent=True,
        )
        if live == target.raw:
            if temporary_entry is not None:
                raise DeploymentError(
                    "control maintenance temporary remains after replacement"
                )
            try:
                os.fsync(current_fd)
            except OSError as error:
                raise DeploymentError(
                    "control maintenance target parent cannot be synchronized"
                ) from error
            return
        if live != current.raw:
            raise DeploymentError(
                f"control maintenance {replacement.role} current bytes disagree"
            )
        if temporary_entry is not None:
            try:
                visible = os.stat(
                    temporary,
                    dir_fd=current_fd,
                    follow_symlinks=False,
                )
                if _identity(visible) != _identity(temporary_entry[0]):
                    raise DeploymentError("control maintenance temporary changed")
                os.unlink(temporary, dir_fd=current_fd)
                os.fsync(current_fd)
            except DeploymentError:
                raise
            except OSError as error:
                raise DeploymentError(
                    "control maintenance temporary cannot be removed"
                ) from error
        descriptor = -1
        created = False
        replaced = False
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600,
                dir_fd=current_fd,
            )
            created = True
            os.fchmod(descriptor, 0o600)
            _write_all(
                descriptor,
                target.raw,
                f"control maintenance {replacement.role} temporary",
            )
            os.fsync(descriptor)
            os.fchmod(descriptor, target.installed["mode"])
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            if (
                _read_control_maintenance_live_artifact(
                    current_fd,
                    name,
                    current,
                    target,
                )
                != current.raw
            ):
                raise DeploymentError(
                    f"control maintenance {replacement.role} changed before replace"
                )
            os.replace(
                temporary,
                name,
                src_dir_fd=current_fd,
                dst_dir_fd=current_fd,
            )
            replaced = True
            os.fsync(current_fd)
            if (
                _read_control_maintenance_live_artifact(
                    current_fd,
                    name,
                    target,
                    target,
                )
                != target.raw
            ):
                raise DeploymentError(
                    f"control maintenance {replacement.role} reread disagrees"
                )
        except DeploymentError:
            raise
        except OSError as error:
            raise DeploymentError(
                f"control maintenance {replacement.role} cannot be replaced"
            ) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if created and not replaced:
                try:
                    os.unlink(temporary, dir_fd=current_fd)
                except OSError:
                    pass
    finally:
        for descriptor in reversed(parents):
            os.close(descriptor)


def _read_activation_selector_temporary(
    canonical_root_fd: int,
    name: str,
    target: StagedArtifact,
    *,
    absent: bool = False,
) -> tuple[os.stat_result, bytes] | None:
    label = f"activation {target.role} selector temporary"
    try:
        before = os.stat(name, dir_fd=canonical_root_fd, follow_symlinks=False)
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=canonical_root_fd)
    except FileNotFoundError:
        if absent:
            return None
        raise DeploymentError(f"{label} is unavailable") from None
    except OSError as error:
        raise DeploymentError(f"{label} is unavailable") from error
    try:
        after = os.fstat(descriptor)
        if (
            _identity(before) != _identity(after)
            or not stat.S_ISREG(after.st_mode)
            or after.st_uid != os.geteuid()
            or after.st_nlink != 1
            or stat.S_IMODE(after.st_mode) != 0o600
            or after.st_size > len(target.raw)
        ):
            raise DeploymentError(f"{label} binding disagrees")
        raw = _read_descriptor(
            descriptor,
            max(len(target.raw), 1),
            label,
        )
        if not target.raw.startswith(raw):
            raise DeploymentError(f"{label} bytes disagree")
        return after, raw
    finally:
        os.close(descriptor)


def _unlink_activation_selector_temporary(
    canonical_root_fd: int,
    name: str,
    expected: os.stat_result,
) -> None:
    try:
        visible = os.stat(name, dir_fd=canonical_root_fd, follow_symlinks=False)
        if _identity(visible) != _identity(expected):
            raise DeploymentError("activation selector temporary changed")
        os.unlink(name, dir_fd=canonical_root_fd)
        os.fsync(canonical_root_fd)
    except DeploymentError:
        raise
    except OSError as error:
        raise DeploymentError(
            "activation selector temporary cannot be removed"
        ) from error


def _replace_activation_selector(
    canonical_root_fd: int,
    *,
    transaction_id: str,
    direction: str,
    index: int,
    current: StagedArtifact,
    target: StagedArtifact,
) -> None:
    selector_names = {
        "active-record": "active.json",
        "deployment-alias": "deployment.json",
    }
    current_role = current.role.removeprefix("prior-")
    target_role = target.role.removeprefix("prior-")
    if target_role not in selector_names or current_role != target_role:
        raise DeploymentError("activation selector replacement role disagrees")
    name = selector_names[target_role]
    if (
        current.installed_path.name != name
        or target.installed_path.name != name
        or current.installed_path.parent != target.installed_path.parent
    ):
        raise DeploymentError("activation selector replacement path disagrees")
    temporary = _activation_selector_temporary_name(
        transaction_id,
        direction,
        index,
    )
    live = _read_activation_file(
        canonical_root_fd,
        name,
        f"activation {target.role} selector",
        limit=max(len(current.raw), len(target.raw), 1),
    )
    temporary_entry = _read_activation_selector_temporary(
        canonical_root_fd,
        temporary,
        target,
        absent=True,
    )
    if live == target.raw:
        if temporary_entry is None:
            return
        raise DeploymentError("activation selector temporary remains after replace")
    if live != current.raw:
        raise DeploymentError("activation selector current bytes disagree")
    if temporary_entry is not None:
        _unlink_activation_selector_temporary(
            canonical_root_fd,
            temporary,
            temporary_entry[0],
        )
    descriptor = -1
    created = False
    replaced = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=canonical_root_fd,
        )
        created = True
        os.fchmod(descriptor, 0o600)
        _write_all(
            descriptor, target.raw, f"activation {target.role} selector temporary"
        )
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        if (
            _read_activation_file(
                canonical_root_fd,
                name,
                f"activation {target.role} selector",
                limit=max(len(current.raw), len(target.raw), 1),
            )
            != current.raw
        ):
            raise DeploymentError("activation selector changed before replace")
        os.replace(
            temporary,
            name,
            src_dir_fd=canonical_root_fd,
            dst_dir_fd=canonical_root_fd,
        )
        replaced = True
        os.fsync(canonical_root_fd)
        if (
            _read_activation_file(
                canonical_root_fd,
                name,
                f"activation {target.role} selector",
                limit=max(len(target.raw), 1),
            )
            != target.raw
        ):
            raise DeploymentError("activation selector replacement reread disagrees")
    except DeploymentError:
        raise
    except OSError as error:
        raise DeploymentError("activation selector cannot be replaced") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if created and not replaced:
            try:
                os.unlink(temporary, dir_fd=canonical_root_fd)
            except OSError:
                pass


def _verify_absent_activation_preimage(
    canonical_root_fd: int,
    precondition: FirstInstallPrecondition,
) -> None:
    os.fsync(canonical_root_fd)
    try:
        lock = os.stat(
            "activation.lock",
            dir_fd=canonical_root_fd,
            follow_symlinks=False,
        )
        inventory = set(os.listdir(canonical_root_fd))
    except OSError as error:
        raise DeploymentError(
            "activation absent preimage cannot be verified"
        ) from error
    retained_result_raws, result_state, _ = _capture_transaction_result_inventory(
        canonical_root_fd,
        precondition.canonical_root,
    )
    _validate_transaction_result_baseline(
        retained_result_raws,
        precondition.retained_result_sha256s,
    )
    expected_inventory = {"activation.lock", "transaction.json"}
    if result_state.directory_present:
        expected_inventory.add("transaction-results")
    if (
        _identity(lock) != precondition.activation_lock_identity
        or inventory != expected_inventory
    ):
        raise DeploymentError("activation absent preimage restoration disagrees")


def _recheck_activation_lock_acquisition(
    root: _RootSnapshot,
    lock_fd: int,
    precondition: FirstInstallPrecondition,
    *,
    root_mapping_only: bool = False,
) -> None:
    try:
        root_descriptor = os.fstat(root.fd)
        root_visible = root.path.lstat()
    except OSError as error:
        raise DeploymentError(
            "activation canonical root changed while acquiring exclusivity"
        ) from error
    _reject_macos_allow_acl(root.fd, "activation canonical root")
    root_identity = (
        _mapping_identity(root_descriptor)
        if root_mapping_only
        else _identity(root_descriptor)
    )
    visible_identity = (
        _mapping_identity(root_visible)
        if root_mapping_only
        else _identity(root_visible)
    )
    expected_root_identity = (
        precondition.root_identity[:4]
        if root_mapping_only
        else precondition.root_identity
    )
    if (
        root_identity != expected_root_identity
        or visible_identity != expected_root_identity
    ):
        raise DeploymentError(
            "activation canonical root changed while acquiring exclusivity"
        )
    try:
        lock_descriptor = os.fstat(lock_fd)
        lock_visible = os.stat(
            "activation.lock",
            dir_fd=root.fd,
            follow_symlinks=False,
        )
    except OSError as error:
        raise DeploymentError(
            "activation lock changed while acquiring exclusivity"
        ) from error
    _reject_macos_allow_acl(lock_fd, "activation lock")
    if (
        stat.S_IMODE(lock_descriptor.st_mode) != 0o600
        or stat.S_IMODE(lock_visible.st_mode) != 0o600
    ):
        raise DeploymentError("activation lock mode must be 0600")
    if lock_descriptor.st_size != 0 or lock_visible.st_size != 0:
        raise DeploymentError("activation lock must be empty")
    if (
        _identity(lock_descriptor) != precondition.activation_lock_identity
        or _identity(lock_visible) != precondition.activation_lock_identity
    ):
        raise DeploymentError("activation lock changed while acquiring exclusivity")


def _open_locked_activation_root(
    precondition: FirstInstallPrecondition,
    *,
    root_mapping_only: bool = False,
) -> tuple[_RootSnapshot, int]:
    root = _open_root(
        precondition.canonical_root,
        "activation canonical root",
        mapping_only=True,
    )
    lock_fd = -1
    try:
        _reject_macos_allow_acl(root.fd, "activation canonical root")
        before = os.stat(
            "activation.lock",
            dir_fd=root.fd,
            follow_symlinks=False,
        )
        if stat.S_IMODE(before.st_mode) != 0o600:
            raise DeploymentError("activation lock mode must be 0600")
        try:
            lock_fd = os.open(
                "activation.lock",
                os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=root.fd,
            )
        except OSError as error:
            try:
                visible = os.stat(
                    "activation.lock",
                    dir_fd=root.fd,
                    follow_symlinks=False,
                )
            except OSError:
                raise DeploymentError("activation lock cannot be opened") from error
            if stat.S_IMODE(visible.st_mode) != 0o600:
                raise DeploymentError("activation lock mode must be 0600") from error
            raise DeploymentError("activation lock cannot be opened") from error
        after = os.fstat(lock_fd)
        _reject_macos_allow_acl(lock_fd, "activation lock")
        binding = precondition.activation_lock
        if stat.S_IMODE(after.st_mode) != 0o600:
            raise DeploymentError("activation lock mode must be 0600")
        if after.st_size != 0:
            raise DeploymentError("activation lock must be empty")
        if (
            _identity(before) != _identity(after)
            or _identity(after) != precondition.activation_lock_identity
            or binding["device"] != after.st_dev
            or binding["inode"] != after.st_ino
            or binding["owner"] != after.st_uid
            or binding["mode"] != stat.S_IMODE(after.st_mode)
        ):
            raise DeploymentError("activation lock binding disagrees")
        deadline = time.monotonic() + PROCESS_PROFILE["exclusive_lock_seconds"]
        while True:
            _recheck_activation_lock_acquisition(
                root,
                lock_fd,
                precondition,
                root_mapping_only=root_mapping_only,
            )
            if time.monotonic() >= deadline:
                raise DeploymentError("activation lock exclusive acquisition timed out")
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno == errno.EINTR:
                    continue
                if error.errno not in {
                    errno.EACCES,
                    errno.EAGAIN,
                    errno.EWOULDBLOCK,
                }:
                    raise DeploymentError(
                        "activation lock exclusivity cannot be acquired"
                    ) from error
                _recheck_activation_lock_acquisition(
                    root,
                    lock_fd,
                    precondition,
                    root_mapping_only=root_mapping_only,
                )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DeploymentError(
                        "activation lock exclusive acquisition timed out"
                    ) from error
                time.sleep(min(0.01, remaining))
                continue
            _recheck_activation_lock_acquisition(
                root,
                lock_fd,
                precondition,
                root_mapping_only=root_mapping_only,
            )
            if time.monotonic() >= deadline:
                raise DeploymentError("activation lock exclusive acquisition timed out")
            break
        return root, lock_fd
    except BaseException:
        if lock_fd >= 0:
            os.close(lock_fd)
        os.close(root.fd)
        raise


def _smoke_acceptance(
    *,
    phase: str,
    candidate_receipt_sha256: str,
    expected_envelope_sha256: str,
    accepted_envelope_sha256: str,
) -> dict[str, Any]:
    unsigned = {
        "phase": phase,
        "target_deployment_receipt_sha256": candidate_receipt_sha256,
        "expected_envelope_sha256": expected_envelope_sha256,
        "accepted_envelope_sha256": accepted_envelope_sha256,
        "exit_status": 0,
    }
    return {**unsigned, "content_sha256": _digest(unsigned)}


def _transaction_result(
    journal: _ActivationJournal,
) -> TransactionResult:
    terminal = journal.value["terminal_result"]
    outcome = terminal["outcome"]
    if (
        journal.value["transaction_class"] == "manual-exact-target-rollback"
        and outcome == "manual-target-active"
    ):
        outcome = "candidate-active"
    elif (
        journal.value["transaction_class"] == "manual-exact-target-rollback"
        and outcome == "manual-current-restored"
    ):
        outcome = "restored-prior"
    return TransactionResult(
        transaction_id=journal.value["transaction_id"],
        outcome=outcome,
        candidate_receipt_sha256=terminal["candidate_receipt_sha256"],
        active_receipt_sha256=terminal["active_receipt_sha256"],
        accepted_envelope_sha256=terminal["accepted_envelope_sha256"],
        journal_sha256=hashlib.sha256(journal.raw).hexdigest(),
        journal_value=_freeze_json(_thaw(journal.value)),
        journal_raw=journal.raw,
    )


def _advance_activation_journal(
    canonical_root_fd: int,
    intent: Mapping[str, Any],
    previous: _ActivationJournal | None,
    phase: str,
    *,
    pending_step: Mapping[str, Any] | None = None,
    smoke_handoff: Mapping[str, Any] | None = None,
    candidate_smoke_acceptance: Mapping[str, Any] | None = None,
    rollback_smoke_acceptance: Mapping[str, Any] | None = None,
    terminal_result: Mapping[str, Any] | None = None,
) -> _ActivationJournal:
    journal = _activation_journal_generation(
        intent,
        previous,
        phase=phase,
        pending_step=pending_step,
        smoke_handoff=smoke_handoff,
        candidate_smoke_acceptance=candidate_smoke_acceptance,
        rollback_smoke_acceptance=rollback_smoke_acceptance,
        terminal_result=terminal_result,
    )
    _write_activation_journal(canonical_root_fd, journal)
    return journal


def _validate_activation_journal_authority(
    journal: _ActivationJournal,
    intent: Mapping[str, Any],
    artifacts: tuple[StagedArtifact, ...],
    removals: tuple[_ActivationRemovalStep, ...],
) -> None:
    for key in (
        "transaction_id",
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
    ):
        if _thaw(journal.value[key]) != _thaw(intent[key]):
            raise DeploymentError(
                f"activation recovery immutable {key} binding disagrees"
            )
    if (
        journal.value["candidate"]["deployment_receipt"]["sha256"]
        != intent["candidate_receipt_sha256"]
    ):
        raise DeploymentError("activation recovery candidate receipt disagrees")
    _validate_activation_journal_program(journal, artifacts, removals)
    _validate_activation_journal_chain(intent, journal, artifacts, removals)


def _validate_control_maintenance_activation_journal_program(
    journal: _ActivationJournal,
    additive: tuple[StagedArtifact, ...],
    replacements: tuple[_ControlMaintenanceReplacement, ...],
    cleanups: tuple[_ActivationRemovalStep, ...],
) -> None:
    value = journal.value
    phase = value["phase"]
    sequence = value["sequence"]
    pending = value["pending_step"]
    additive_count = len(additive)
    replacement_count = len(replacements)
    cleanup_count = len(cleanups)
    if (
        replacement_count != len(CONTROL_MAINTENANCE_REPLACEMENT_ROLES)
        or cleanup_count < 2
    ):
        raise DeploymentError("control maintenance replacement program disagrees")
    if phase == "prepared":
        expected = 1
    elif phase == "frozen":
        expected = 2
    elif phase == "drained":
        expected = 3
    elif phase == "additive-installing":
        index = pending["index"]
        if index >= additive_count or pending != {
            "operation": "install",
            "index": index,
            "role": additive[index].role,
        }:
            raise DeploymentError("control maintenance additive cursor disagrees")
        expected = 4 + index
    elif phase == "control-switching":
        index = pending["index"]
        if index >= replacement_count or pending != {
            "operation": "replace-control",
            "index": index,
            "role": replacements[index].role,
        }:
            raise DeploymentError("control maintenance replacement cursor disagrees")
        expected = 4 + additive_count + index
    elif phase == "candidate-smoke":
        expected = 4 + additive_count + replacement_count
        if value["candidate_smoke_acceptance"] is not None:
            expected += 1
    elif phase == "candidate-accepted":
        expected = 6 + additive_count + replacement_count
    elif phase == "prior-restoring":
        index = pending["index"]
        if index >= replacement_count or pending != {
            "operation": "replace-control",
            "index": index,
            "role": replacements[index].role,
        }:
            raise DeploymentError("control maintenance restore cursor disagrees")
        expected = 5 + additive_count + replacement_count + index
    elif phase == "rollback-smoke":
        expected = 5 + additive_count + (2 * replacement_count)
        if value["rollback_smoke_acceptance"] is not None:
            expected += 1
    elif phase == "prior-accepted":
        expected = 7 + additive_count + (2 * replacement_count)
    elif phase == "rollback-cleaning":
        index = pending["index"]
        if index >= cleanup_count:
            raise DeploymentError("control maintenance cleanup cursor is out of range")
        step = cleanups[index]
        if pending != {
            "operation": step.operation,
            "index": index,
            "role": step.role,
        }:
            raise DeploymentError("control maintenance cleanup cursor disagrees")
        expected = 8 + additive_count + (2 * replacement_count) + index
    else:
        outcome = value["terminal_result"]["outcome"]
        successful_outcome = (
            "manual-target-active"
            if value["transaction_class"] == "manual-exact-target-rollback"
            else "candidate-active"
        )
        if outcome == successful_outcome:
            expected = 7 + additive_count + replacement_count
        elif outcome == "recovery-required":
            expected = 6 + additive_count + (2 * replacement_count)
        elif outcome == (
            "manual-current-restored"
            if value["transaction_class"] == "manual-exact-target-rollback"
            else "restored-prior"
        ):
            expected = 8 + additive_count + (2 * replacement_count) + cleanup_count
        else:
            raise DeploymentError(
                "control maintenance terminal outcome disagrees with its program"
            )
    if sequence != expected:
        raise DeploymentError(
            "control maintenance journal sequence disagrees with its program state"
        )


def _immediate_control_maintenance_journal_successors(
    intent: Mapping[str, Any],
    journal: _ActivationJournal,
    additive: tuple[StagedArtifact, ...],
    replacements: tuple[_ControlMaintenanceReplacement, ...],
    cleanups: tuple[_ActivationRemovalStep, ...],
) -> tuple[_ActivationJournal, ...]:
    phase = journal.value["phase"]
    options: list[dict[str, Any]] = []
    candidate_smoke = intent["candidate"]["smoke"]
    prior_smoke = intent["prior"]["smoke"]
    candidate_handoff = {
        "target_deployment_receipt_sha256": intent["candidate_receipt_sha256"],
        "smoke_bundle_sha256": candidate_smoke["bundle"]["sha256"],
        "smoke_trust_context_sha256": candidate_smoke["trust_context"]["sha256"],
    }
    prior_sha256 = intent["prior"]["deployment_receipt"]["sha256"]
    rollback_handoff = {
        "target_deployment_receipt_sha256": prior_sha256,
        "smoke_bundle_sha256": prior_smoke["bundle"]["sha256"],
        "smoke_trust_context_sha256": prior_smoke["trust_context"]["sha256"],
    }
    if phase == "prepared":
        options.append({"phase": "frozen"})
    elif phase == "frozen":
        options.append({"phase": "drained"})
    elif phase == "drained":
        if additive:
            options.append(
                {
                    "phase": "additive-installing",
                    "pending_step": {
                        "operation": "install",
                        "index": 0,
                        "role": additive[0].role,
                    },
                }
            )
        else:
            options.append(
                {
                    "phase": "control-switching",
                    "pending_step": {
                        "operation": "replace-control",
                        "index": 0,
                        "role": replacements[0].role,
                    },
                }
            )
    elif phase == "additive-installing":
        index = journal.value["pending_step"]["index"]
        if index + 1 < len(additive):
            following = additive[index + 1]
            options.append(
                {
                    "phase": "additive-installing",
                    "pending_step": {
                        "operation": "install",
                        "index": index + 1,
                        "role": following.role,
                    },
                }
            )
        else:
            options.append(
                {
                    "phase": "control-switching",
                    "pending_step": {
                        "operation": "replace-control",
                        "index": 0,
                        "role": replacements[0].role,
                    },
                }
            )
    elif phase == "control-switching":
        index = journal.value["pending_step"]["index"]
        if index + 1 < len(replacements):
            following = replacements[index + 1]
            options.append(
                {
                    "phase": "control-switching",
                    "pending_step": {
                        "operation": "replace-control",
                        "index": index + 1,
                        "role": following.role,
                    },
                }
            )
        else:
            options.append(
                {
                    "phase": "candidate-smoke",
                    "smoke_handoff": candidate_handoff,
                }
            )
    elif phase == "candidate-smoke":
        acceptance = journal.value["candidate_smoke_acceptance"]
        if acceptance is None:
            acceptance = _smoke_acceptance(
                phase="candidate-smoke",
                candidate_receipt_sha256=intent["candidate_receipt_sha256"],
                expected_envelope_sha256=candidate_smoke["expected_envelope_sha256"],
                accepted_envelope_sha256=candidate_smoke["expected_envelope_sha256"],
            )
            options.extend(
                (
                    {
                        "phase": "candidate-smoke",
                        "smoke_handoff": candidate_handoff,
                        "candidate_smoke_acceptance": acceptance,
                    },
                    {
                        "phase": "prior-restoring",
                        "pending_step": {
                            "operation": "replace-control",
                            "index": 0,
                            "role": replacements[0].role,
                        },
                    },
                )
            )
        else:
            options.append(
                {
                    "phase": "candidate-accepted",
                    "candidate_smoke_acceptance": acceptance,
                }
            )
    elif phase == "candidate-accepted":
        acceptance = journal.value["candidate_smoke_acceptance"]
        options.append(
            {
                "phase": "terminal",
                "candidate_smoke_acceptance": acceptance,
                "terminal_result": {
                    "outcome": (
                        "manual-target-active"
                        if intent["transaction_class"] == "manual-exact-target-rollback"
                        else "candidate-active"
                    ),
                    "candidate_receipt_sha256": intent["candidate_receipt_sha256"],
                    "active_receipt_sha256": intent["candidate_receipt_sha256"],
                    "accepted_envelope_sha256": acceptance["accepted_envelope_sha256"],
                    "failure_class": None,
                },
            }
        )
    elif phase == "prior-restoring":
        index = journal.value["pending_step"]["index"]
        if index + 1 < len(replacements):
            following = replacements[index + 1]
            options.append(
                {
                    "phase": "prior-restoring",
                    "pending_step": {
                        "operation": "replace-control",
                        "index": index + 1,
                        "role": following.role,
                    },
                }
            )
        else:
            options.append(
                {
                    "phase": "rollback-smoke",
                    "smoke_handoff": rollback_handoff,
                }
            )
    elif phase == "rollback-smoke":
        acceptance = journal.value["rollback_smoke_acceptance"]
        if acceptance is None:
            accepted = _smoke_acceptance(
                phase="rollback-smoke",
                candidate_receipt_sha256=prior_sha256,
                expected_envelope_sha256=prior_smoke["expected_envelope_sha256"],
                accepted_envelope_sha256=prior_smoke["expected_envelope_sha256"],
            )
            options.extend(
                (
                    {
                        "phase": "rollback-smoke",
                        "smoke_handoff": rollback_handoff,
                        "rollback_smoke_acceptance": accepted,
                    },
                    {
                        "phase": "terminal",
                        "terminal_result": {
                            "outcome": "recovery-required",
                            "candidate_receipt_sha256": intent[
                                "candidate_receipt_sha256"
                            ],
                            "active_receipt_sha256": None,
                            "accepted_envelope_sha256": None,
                            "failure_class": "rollback-smoke-rejected",
                        },
                    },
                )
            )
        else:
            options.append(
                {
                    "phase": "prior-accepted",
                    "rollback_smoke_acceptance": acceptance,
                }
            )
    elif phase == "prior-accepted":
        step = cleanups[0]
        options.append(
            {
                "phase": "rollback-cleaning",
                "pending_step": {
                    "operation": step.operation,
                    "index": 0,
                    "role": step.role,
                },
                "rollback_smoke_acceptance": journal.value["rollback_smoke_acceptance"],
            }
        )
    elif phase == "rollback-cleaning":
        index = journal.value["pending_step"]["index"]
        acceptance = journal.value["rollback_smoke_acceptance"]
        if index + 1 < len(cleanups):
            step = cleanups[index + 1]
            options.append(
                {
                    "phase": "rollback-cleaning",
                    "pending_step": {
                        "operation": step.operation,
                        "index": index + 1,
                        "role": step.role,
                    },
                    "rollback_smoke_acceptance": acceptance,
                }
            )
        else:
            options.append(
                {
                    "phase": "terminal",
                    "rollback_smoke_acceptance": acceptance,
                    "terminal_result": {
                        "outcome": (
                            "manual-current-restored"
                            if intent["transaction_class"]
                            == "manual-exact-target-rollback"
                            else "restored-prior"
                        ),
                        "candidate_receipt_sha256": intent["candidate_receipt_sha256"],
                        "active_receipt_sha256": prior_sha256,
                        "accepted_envelope_sha256": acceptance[
                            "accepted_envelope_sha256"
                        ],
                        "failure_class": (
                            "target-smoke-rejected"
                            if intent["transaction_class"]
                            == "manual-exact-target-rollback"
                            else "candidate-smoke-rejected"
                        ),
                    },
                }
            )
    return tuple(
        _activation_journal_generation(
            intent,
            journal,
            phase=option["phase"],
            pending_step=option.get("pending_step"),
            smoke_handoff=option.get("smoke_handoff"),
            candidate_smoke_acceptance=option.get("candidate_smoke_acceptance"),
            rollback_smoke_acceptance=option.get("rollback_smoke_acceptance"),
            terminal_result=option.get("terminal_result"),
        )
        for option in options
    )


def _validate_control_maintenance_journal_chain(
    intent: Mapping[str, Any],
    journal: _ActivationJournal,
    additive: tuple[StagedArtifact, ...],
    replacements: tuple[_ControlMaintenanceReplacement, ...],
    cleanups: tuple[_ActivationRemovalStep, ...],
) -> None:
    target_sequence = journal.value["sequence"]
    frontier = [_activation_journal_generation(intent, None, phase="prepared")]
    candidates: list[_ActivationJournal] = []
    while frontier:
        generation = frontier.pop()
        sequence = generation.value["sequence"]
        if sequence == target_sequence:
            if _activation_journal_chain_value(
                generation.value
            ) == _activation_journal_chain_value(journal.value):
                candidates.append(generation)
            continue
        if sequence < target_sequence:
            frontier.extend(
                _immediate_control_maintenance_journal_successors(
                    intent,
                    generation,
                    additive,
                    replacements,
                    cleanups,
                )
            )
    if (
        len(candidates) != 1
        or candidates[0].value["previous_journal_sha256"]
        != journal.value["previous_journal_sha256"]
    ):
        raise DeploymentError("control maintenance journal chain disagrees")


def _validate_control_maintenance_journal_authority(
    journal: _ActivationJournal,
    intent: Mapping[str, Any],
    additive: tuple[StagedArtifact, ...],
    replacements: tuple[_ControlMaintenanceReplacement, ...],
    cleanups: tuple[_ActivationRemovalStep, ...],
) -> None:
    transaction_class = _text(
        intent["transaction_class"],
        "control maintenance transaction class",
    )
    parsed = _parse_control_maintenance_activation_journal(
        journal.raw,
        expected_transaction_class=transaction_class,
    )
    if parsed.value != journal.value:
        raise DeploymentError("control maintenance journal parse disagrees")
    if ("bridge_transition" in journal.value) != ("bridge_transition" in intent):
        raise DeploymentError("control maintenance transition class disagrees")
    immutable_keys = [
        "transaction_id",
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
    if "bridge_transition" in intent:
        immutable_keys.append("bridge_transition")
    for key in immutable_keys:
        if _thaw(journal.value[key]) != _thaw(intent[key]):
            raise DeploymentError(
                f"control maintenance immutable {key} binding disagrees"
            )
    if (
        journal.value["candidate"]["deployment_receipt"]["sha256"]
        != intent["candidate_receipt_sha256"]
    ):
        raise DeploymentError("control maintenance candidate receipt disagrees")
    _validate_control_maintenance_activation_journal_program(
        journal,
        additive,
        replacements,
        cleanups,
    )
    _validate_control_maintenance_journal_chain(
        intent,
        journal,
        additive,
        replacements,
        cleanups,
    )


def _immediate_activation_journal_successors(
    intent: Mapping[str, Any],
    journal: _ActivationJournal,
    artifacts: tuple[StagedArtifact, ...],
    removals: tuple[_ActivationRemovalStep, ...],
) -> tuple[_ActivationJournal, ...]:
    if intent["transaction_class"] == "routine-payload":
        return _immediate_routine_activation_journal_successors(
            intent,
            journal,
            artifacts,
            removals,
        )
    phase = journal.value["phase"]
    options: list[dict[str, Any]] = []
    if phase == "prepared":
        options.append({"phase": "frozen"})
    elif phase == "frozen":
        options.append({"phase": "drained"})
    elif phase == "drained":
        artifact = artifacts[0]
        options.append(
            {
                "phase": "control-installing",
                "pending_step": {
                    "operation": "install",
                    "index": 0,
                    "role": artifact.role,
                },
            }
        )
    elif phase == "control-installing":
        index = journal.value["pending_step"]["index"]
        if index + 1 < len(artifacts):
            artifact = artifacts[index + 1]
            options.append(
                {
                    "phase": "control-installing",
                    "pending_step": {
                        "operation": "install",
                        "index": index + 1,
                        "role": artifact.role,
                    },
                }
            )
        else:
            smoke = intent["candidate"]["smoke"]
            options.append(
                {
                    "phase": "candidate-smoke",
                    "smoke_handoff": {
                        "target_deployment_receipt_sha256": intent[
                            "candidate_receipt_sha256"
                        ],
                        "smoke_bundle_sha256": smoke["bundle"]["sha256"],
                        "smoke_trust_context_sha256": smoke["trust_context"]["sha256"],
                    },
                }
            )
    elif phase == "candidate-smoke":
        acceptance = journal.value["candidate_smoke_acceptance"]
        if acceptance is None:
            smoke = intent["candidate"]["smoke"]
            accepted = _smoke_acceptance(
                phase="candidate-smoke",
                candidate_receipt_sha256=intent["candidate_receipt_sha256"],
                expected_envelope_sha256=smoke["expected_envelope_sha256"],
                accepted_envelope_sha256=smoke["expected_envelope_sha256"],
            )
            handoff = {
                "target_deployment_receipt_sha256": intent["candidate_receipt_sha256"],
                "smoke_bundle_sha256": smoke["bundle"]["sha256"],
                "smoke_trust_context_sha256": smoke["trust_context"]["sha256"],
            }
            options.extend(
                (
                    {
                        "phase": "candidate-smoke",
                        "smoke_handoff": handoff,
                        "candidate_smoke_acceptance": accepted,
                    },
                    {"phase": "absence-restoring"},
                )
            )
        else:
            options.append(
                {
                    "phase": "candidate-accepted",
                    "candidate_smoke_acceptance": acceptance,
                }
            )
    elif phase == "candidate-accepted":
        acceptance = journal.value["candidate_smoke_acceptance"]
        options.append(
            {
                "phase": "terminal",
                "candidate_smoke_acceptance": acceptance,
                "terminal_result": {
                    "outcome": "candidate-active",
                    "candidate_receipt_sha256": intent["candidate_receipt_sha256"],
                    "active_receipt_sha256": intent["candidate_receipt_sha256"],
                    "accepted_envelope_sha256": acceptance["accepted_envelope_sha256"],
                    "failure_class": None,
                },
            }
        )
    elif phase == "absence-restoring":
        pending = journal.value["pending_step"]
        if pending is None:
            following = removals[0]
            options.append(
                {
                    "phase": "absence-restoring",
                    "pending_step": {
                        "operation": following.operation,
                        "index": following.index,
                        "role": following.role,
                    },
                }
            )
        elif pending["index"] + 1 < len(removals):
            following = removals[pending["index"] + 1]
            options.append(
                {
                    "phase": "absence-restoring",
                    "pending_step": {
                        "operation": following.operation,
                        "index": following.index,
                        "role": following.role,
                    },
                }
            )
        else:
            options.append({"phase": "absence-accepted"})
    elif phase == "absence-accepted":
        options.append(
            {
                "phase": "terminal",
                "terminal_result": {
                    "outcome": "restored-absent",
                    "candidate_receipt_sha256": intent["candidate_receipt_sha256"],
                    "active_receipt_sha256": None,
                    "accepted_envelope_sha256": None,
                    "failure_class": "candidate-smoke-rejected",
                },
            }
        )
    return tuple(
        _activation_journal_generation(
            intent,
            journal,
            phase=option["phase"],
            pending_step=option.get("pending_step"),
            smoke_handoff=option.get("smoke_handoff"),
            candidate_smoke_acceptance=option.get("candidate_smoke_acceptance"),
            rollback_smoke_acceptance=None,
            terminal_result=option.get("terminal_result"),
        )
        for option in options
    )


def _immediate_routine_activation_journal_successors(
    intent: Mapping[str, Any],
    journal: _ActivationJournal,
    artifacts: tuple[StagedArtifact, ...],
    cleanups: tuple[_ActivationRemovalStep, ...],
) -> tuple[_ActivationJournal, ...]:
    phase = journal.value["phase"]
    options: list[dict[str, Any]] = []
    candidate_smoke = intent["candidate"]["smoke"]
    prior_smoke = intent["prior"]["smoke"]
    candidate_handoff = {
        "target_deployment_receipt_sha256": intent["candidate_receipt_sha256"],
        "smoke_bundle_sha256": candidate_smoke["bundle"]["sha256"],
        "smoke_trust_context_sha256": candidate_smoke["trust_context"]["sha256"],
    }
    prior_sha256 = intent["prior"]["deployment_receipt"]["sha256"]
    rollback_handoff = {
        "target_deployment_receipt_sha256": prior_sha256,
        "smoke_bundle_sha256": prior_smoke["bundle"]["sha256"],
        "smoke_trust_context_sha256": prior_smoke["trust_context"]["sha256"],
    }
    if phase == "prepared":
        options.append({"phase": "frozen"})
    elif phase == "frozen":
        options.append({"phase": "drained"})
    elif phase == "drained":
        artifact = artifacts[0]
        options.append(
            {
                "phase": "additive-installing",
                "pending_step": {
                    "operation": "install",
                    "index": 0,
                    "role": artifact.role,
                },
            }
        )
    elif phase == "additive-installing":
        index = journal.value["pending_step"]["index"]
        if index + 1 < len(artifacts):
            following = artifacts[index + 1]
            options.append(
                {
                    "phase": "additive-installing",
                    "pending_step": {
                        "operation": "install",
                        "index": index + 1,
                        "role": following.role,
                    },
                }
            )
        else:
            options.append(
                {
                    "phase": "selector-switching",
                    "pending_step": {
                        "operation": "replace-selector",
                        "index": 0,
                        "role": "active-record",
                    },
                }
            )
    elif phase == "selector-switching":
        index = journal.value["pending_step"]["index"]
        if index == 0:
            options.append(
                {
                    "phase": "selector-switching",
                    "pending_step": {
                        "operation": "replace-selector",
                        "index": 1,
                        "role": "deployment-alias",
                    },
                }
            )
        else:
            options.append(
                {
                    "phase": "candidate-smoke",
                    "smoke_handoff": candidate_handoff,
                }
            )
    elif phase == "candidate-smoke":
        acceptance = journal.value["candidate_smoke_acceptance"]
        if acceptance is None:
            accepted = _smoke_acceptance(
                phase="candidate-smoke",
                candidate_receipt_sha256=intent["candidate_receipt_sha256"],
                expected_envelope_sha256=candidate_smoke["expected_envelope_sha256"],
                accepted_envelope_sha256=candidate_smoke["expected_envelope_sha256"],
            )
            options.extend(
                (
                    {
                        "phase": "candidate-smoke",
                        "smoke_handoff": candidate_handoff,
                        "candidate_smoke_acceptance": accepted,
                    },
                    {
                        "phase": "prior-restoring",
                        "pending_step": {
                            "operation": "replace-selector",
                            "index": 0,
                            "role": "active-record",
                        },
                    },
                )
            )
        else:
            options.append(
                {
                    "phase": "candidate-accepted",
                    "candidate_smoke_acceptance": acceptance,
                }
            )
    elif phase == "candidate-accepted":
        acceptance = journal.value["candidate_smoke_acceptance"]
        options.append(
            {
                "phase": "terminal",
                "candidate_smoke_acceptance": acceptance,
                "terminal_result": {
                    "outcome": "candidate-active",
                    "candidate_receipt_sha256": intent["candidate_receipt_sha256"],
                    "active_receipt_sha256": intent["candidate_receipt_sha256"],
                    "accepted_envelope_sha256": acceptance["accepted_envelope_sha256"],
                    "failure_class": None,
                },
            }
        )
    elif phase == "prior-restoring":
        index = journal.value["pending_step"]["index"]
        if index == 0:
            options.append(
                {
                    "phase": "prior-restoring",
                    "pending_step": {
                        "operation": "replace-selector",
                        "index": 1,
                        "role": "deployment-alias",
                    },
                }
            )
        else:
            options.append(
                {
                    "phase": "rollback-smoke",
                    "smoke_handoff": rollback_handoff,
                }
            )
    elif phase == "rollback-smoke":
        acceptance = journal.value["rollback_smoke_acceptance"]
        if acceptance is None:
            accepted = _smoke_acceptance(
                phase="rollback-smoke",
                candidate_receipt_sha256=prior_sha256,
                expected_envelope_sha256=prior_smoke["expected_envelope_sha256"],
                accepted_envelope_sha256=prior_smoke["expected_envelope_sha256"],
            )
            options.extend(
                (
                    {
                        "phase": "rollback-smoke",
                        "smoke_handoff": rollback_handoff,
                        "rollback_smoke_acceptance": accepted,
                    },
                    {
                        "phase": "terminal",
                        "terminal_result": {
                            "outcome": "recovery-required",
                            "candidate_receipt_sha256": intent[
                                "candidate_receipt_sha256"
                            ],
                            "active_receipt_sha256": None,
                            "accepted_envelope_sha256": None,
                            "failure_class": "rollback-smoke-rejected",
                        },
                    },
                )
            )
        else:
            options.append(
                {
                    "phase": "prior-accepted",
                    "rollback_smoke_acceptance": acceptance,
                }
            )
    elif phase == "prior-accepted":
        step = cleanups[0]
        options.append(
            {
                "phase": "rollback-cleaning",
                "pending_step": {
                    "operation": step.operation,
                    "index": 0,
                    "role": step.role,
                },
                "rollback_smoke_acceptance": journal.value["rollback_smoke_acceptance"],
            }
        )
    elif phase == "rollback-cleaning":
        index = journal.value["pending_step"]["index"]
        acceptance = journal.value["rollback_smoke_acceptance"]
        if index + 1 < len(cleanups):
            step = cleanups[index + 1]
            options.append(
                {
                    "phase": "rollback-cleaning",
                    "pending_step": {
                        "operation": step.operation,
                        "index": index + 1,
                        "role": step.role,
                    },
                    "rollback_smoke_acceptance": acceptance,
                }
            )
        else:
            options.append(
                {
                    "phase": "terminal",
                    "rollback_smoke_acceptance": acceptance,
                    "terminal_result": {
                        "outcome": "restored-prior",
                        "candidate_receipt_sha256": intent["candidate_receipt_sha256"],
                        "active_receipt_sha256": prior_sha256,
                        "accepted_envelope_sha256": acceptance[
                            "accepted_envelope_sha256"
                        ],
                        "failure_class": "candidate-smoke-rejected",
                    },
                }
            )
    return tuple(
        _activation_journal_generation(
            intent,
            journal,
            phase=option["phase"],
            pending_step=option.get("pending_step"),
            smoke_handoff=option.get("smoke_handoff"),
            candidate_smoke_acceptance=option.get("candidate_smoke_acceptance"),
            rollback_smoke_acceptance=option.get("rollback_smoke_acceptance"),
            terminal_result=option.get("terminal_result"),
        )
        for option in options
    )


def _activation_journal_chain_value(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _thaw(item)
        for key, item in value.items()
        if key not in {"previous_journal_sha256", "content_sha256"}
    }


def _validate_activation_journal_chain(
    intent: Mapping[str, Any],
    journal: _ActivationJournal,
    artifacts: tuple[StagedArtifact, ...],
    removals: tuple[_ActivationRemovalStep, ...],
) -> None:
    target_sequence = journal.value["sequence"]
    frontier = [
        _activation_journal_generation(
            intent,
            None,
            phase="prepared",
        )
    ]
    candidates: list[_ActivationJournal] = []
    while frontier:
        generation = frontier.pop()
        sequence = generation.value["sequence"]
        if sequence == target_sequence:
            if _activation_journal_chain_value(
                generation.value
            ) == _activation_journal_chain_value(journal.value):
                candidates.append(generation)
            continue
        if sequence < target_sequence:
            frontier.extend(
                _immediate_activation_journal_successors(
                    intent,
                    generation,
                    artifacts,
                    removals,
                )
            )
    if (
        len(candidates) != 1
        or candidates[0].value["previous_journal_sha256"]
        != journal.value["previous_journal_sha256"]
    ):
        raise DeploymentError("activation journal chain binding disagrees")


def _reconcile_activation_journal_temporary(
    canonical_root_fd: int,
    intent: Mapping[str, Any],
    journal: _ActivationJournal,
    artifacts: tuple[StagedArtifact, ...],
    removals: tuple[_ActivationRemovalStep, ...],
    *,
    remove: bool = True,
) -> str | None:
    try:
        names = sorted(
            name
            for name in os.listdir(canonical_root_fd)
            if name.startswith("transaction.") and name.endswith(".tmp")
        )
    except OSError as error:
        raise DeploymentError(
            "activation journal temporary inventory is unavailable"
        ) from error
    if not names:
        return None
    expected_name = (
        f"transaction.{journal.value['transaction_id']}."
        f"{journal.value['sequence'] + 1}.tmp"
    )
    if names != [expected_name]:
        raise DeploymentError("activation journal temporary inventory disagrees")
    try:
        before = os.stat(
            expected_name,
            dir_fd=canonical_root_fd,
            follow_symlinks=False,
        )
        descriptor = os.open(
            expected_name,
            _FILE_FLAGS,
            dir_fd=canonical_root_fd,
        )
    except OSError as error:
        raise DeploymentError("activation journal temporary is unavailable") from error
    try:
        after = os.fstat(descriptor)
        if (
            _identity(before) != _identity(after)
            or not stat.S_ISREG(after.st_mode)
            or after.st_uid != os.geteuid()
            or after.st_nlink != 1
            or stat.S_IMODE(after.st_mode) != 0o600
        ):
            raise DeploymentError("activation journal temporary binding disagrees")
        raw = _read_descriptor(
            descriptor,
            MAX_JSON_BYTES,
            "activation journal temporary",
        )
    finally:
        os.close(descriptor)
    successors = _immediate_activation_journal_successors(
        intent,
        journal,
        artifacts,
        removals,
    )
    if not successors or not any(item.raw.startswith(raw) for item in successors):
        raise DeploymentError("activation journal temporary bytes disagree")
    if not remove:
        return expected_name
    try:
        visible = os.stat(
            expected_name,
            dir_fd=canonical_root_fd,
            follow_symlinks=False,
        )
        if _identity(visible) != _identity(before):
            raise DeploymentError("activation journal temporary changed")
        os.unlink(expected_name, dir_fd=canonical_root_fd)
        os.fsync(canonical_root_fd)
    except DeploymentError:
        raise
    except OSError as error:
        raise DeploymentError(
            "activation journal temporary cannot be reconciled"
        ) from error
    return None


def _reconcile_control_maintenance_journal_temporary(
    canonical_root_fd: int,
    intent: Mapping[str, Any],
    journal: _ActivationJournal,
    additive: tuple[StagedArtifact, ...],
    replacements: tuple[_ControlMaintenanceReplacement, ...],
    cleanups: tuple[_ActivationRemovalStep, ...],
    *,
    remove: bool = True,
) -> str | None:
    try:
        names = sorted(
            name
            for name in os.listdir(canonical_root_fd)
            if name.startswith("transaction.") and name.endswith(".tmp")
        )
    except OSError as error:
        raise DeploymentError(
            "control maintenance journal temporary inventory is unavailable"
        ) from error
    if not names:
        return None
    expected_name = (
        f"transaction.{journal.value['transaction_id']}."
        f"{journal.value['sequence'] + 1}.tmp"
    )
    if names != [expected_name]:
        raise DeploymentError(
            "control maintenance journal temporary inventory disagrees"
        )
    try:
        before = os.stat(
            expected_name,
            dir_fd=canonical_root_fd,
            follow_symlinks=False,
        )
        descriptor = os.open(
            expected_name,
            _FILE_FLAGS,
            dir_fd=canonical_root_fd,
        )
    except OSError as error:
        raise DeploymentError(
            "control maintenance journal temporary is unavailable"
        ) from error
    try:
        after = os.fstat(descriptor)
        if (
            _identity(before) != _identity(after)
            or not stat.S_ISREG(after.st_mode)
            or after.st_uid != os.geteuid()
            or after.st_nlink != 1
            or stat.S_IMODE(after.st_mode) != 0o600
        ):
            raise DeploymentError(
                "control maintenance journal temporary binding disagrees"
            )
        raw = _read_descriptor(
            descriptor,
            MAX_JSON_BYTES,
            "control maintenance journal temporary",
        )
    finally:
        os.close(descriptor)
    successors = _immediate_control_maintenance_journal_successors(
        intent,
        journal,
        additive,
        replacements,
        cleanups,
    )
    if not successors or not any(item.raw.startswith(raw) for item in successors):
        raise DeploymentError("control maintenance journal temporary bytes disagree")
    if not remove:
        return expected_name
    try:
        visible = os.stat(
            expected_name,
            dir_fd=canonical_root_fd,
            follow_symlinks=False,
        )
        if _identity(visible) != _identity(before):
            raise DeploymentError("control maintenance journal temporary changed")
        os.unlink(expected_name, dir_fd=canonical_root_fd)
        os.fsync(canonical_root_fd)
    except DeploymentError:
        raise
    except OSError as error:
        raise DeploymentError(
            "control maintenance journal temporary cannot be reconciled"
        ) from error
    return None


def _activation_artifact_parent_directories(
    artifact: StagedArtifact,
) -> tuple[str, ...]:
    _, components = _relative_path(
        artifact.relative_path,
        f"activation {artifact.role} audit path",
    )
    return tuple("/".join(components[:index]) for index in range(1, len(components)))


def _activation_pending_directory_suffix(
    artifacts: tuple[StagedArtifact, ...],
    step_index: int,
) -> tuple[str, ...]:
    index = _nonnegative_integer(step_index, "activation install step index")
    if index >= len(artifacts):
        raise DeploymentError("activation install step index is out of range")
    completed = {
        directory
        for artifact in artifacts[:index]
        for directory in _activation_artifact_parent_directories(artifact)
    }
    current = _activation_artifact_parent_directories(artifacts[index])
    suffix = tuple(directory for directory in current if directory not in completed)
    if suffix and current[-len(suffix) :] != suffix:
        raise DeploymentError("activation pending directory program is not a suffix")
    return suffix


def _activation_journal_pending_directory_suffix(
    journal: _ActivationJournal,
    artifacts: tuple[StagedArtifact, ...],
) -> tuple[str, ...]:
    if journal.value["phase"] != "control-installing":
        return ()
    pending = journal.value["pending_step"]
    if pending["operation"] != "install":
        raise DeploymentError("activation pending directory operation disagrees")
    index = pending["index"]
    if artifacts[index].role != pending["role"]:
        raise DeploymentError("activation pending directory role disagrees")
    return _activation_pending_directory_suffix(artifacts, index)


def _control_maintenance_journal_pending_directory_suffix(
    journal: _ActivationJournal,
    additive: tuple[StagedArtifact, ...],
) -> tuple[str, ...]:
    if journal.value["phase"] != "additive-installing":
        return ()
    pending = journal.value["pending_step"]
    if pending["operation"] != "install":
        raise DeploymentError(
            "control maintenance pending directory operation disagrees"
        )
    index = pending["index"]
    if additive[index].role != pending["role"]:
        raise DeploymentError("control maintenance pending directory role disagrees")
    return _activation_pending_directory_suffix(additive, index)


def _reconcile_activation_pending_directories(
    canonical_root_fd: int,
    artifact: StagedArtifact,
    repair_paths: tuple[str, ...],
) -> None:
    _, components = _relative_path(
        artifact.relative_path,
        f"activation {artifact.role} pending directory path",
    )
    expected = frozenset(repair_paths)
    if not expected.issubset(_activation_artifact_parent_directories(artifact)):
        raise DeploymentError("activation pending directory repair set disagrees")
    current = os.dup(canonical_root_fd)
    directories = [current]
    try:
        for component_index, component in enumerate(components[:-1], start=1):
            relative_directory = "/".join(components[:component_index])
            if relative_directory in expected:
                child = _open_activation_install_directory(
                    current,
                    component,
                    f"activation {artifact.role} pending directory",
                    create=False,
                )
                if child is None:
                    return
            else:
                child = _open_private_directory(
                    current,
                    component,
                    f"activation {artifact.role} completed directory",
                    create=False,
                )
            directories.append(child)
            current = child
    finally:
        for descriptor in reversed(directories):
            os.close(descriptor)


def _activation_tree_inventory(
    canonical_root_fd: int,
    *,
    pending_directory_residues: frozenset[str] = frozenset(),
) -> tuple[set[str], set[str]]:
    directories: set[str] = set()
    files: set[str] = set()

    def visit(parent_fd: int, prefix: str) -> None:
        try:
            names = sorted(os.listdir(parent_fd))
        except OSError as error:
            raise DeploymentError(
                "activation transaction inventory is unavailable"
            ) from error
        for name in names:
            relative = name if not prefix else f"{prefix}/{name}"
            try:
                before = os.stat(
                    name,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except OSError as error:
                raise DeploymentError(
                    "activation transaction inventory is unavailable"
                ) from error
            if stat.S_ISDIR(before.st_mode):
                mode = stat.S_IMODE(before.st_mode)
                pending_residue = relative in pending_directory_residues
                if before.st_uid != os.geteuid() or (
                    mode & ~0o700 if pending_residue else mode != 0o700
                ):
                    raise DeploymentError(
                        "activation transaction directory binding disagrees"
                    )
                directories.add(relative)
                if pending_residue and mode != 0o700:
                    continue
                try:
                    descriptor = os.open(
                        name,
                        _DIRECTORY_FLAGS,
                        dir_fd=parent_fd,
                    )
                except OSError as error:
                    raise DeploymentError(
                        "activation transaction directory is unavailable"
                    ) from error
                try:
                    if _identity(os.fstat(descriptor)) != _identity(before):
                        raise DeploymentError(
                            "activation transaction directory changed"
                        )
                    visit(descriptor, relative)
                finally:
                    os.close(descriptor)
            elif stat.S_ISREG(before.st_mode):
                files.add(relative)
            else:
                raise DeploymentError(
                    "activation transaction inventory entry is unsupported"
                )

    visit(canonical_root_fd, "")
    return directories, files


def _audit_activation_artifact(
    canonical_root_fd: int,
    artifact: StagedArtifact,
) -> None:
    _, components = _relative_path(
        artifact.relative_path,
        f"activation {artifact.role} audit path",
    )
    current = os.dup(canonical_root_fd)
    parents = [current]
    try:
        for component in components[:-1]:
            child = _open_private_directory(
                current,
                component,
                f"installed {artifact.role} directory",
                create=False,
            )
            parents.append(child)
            current = child
        _verify_activation_artifact(current, components[-1], artifact)
    finally:
        for descriptor in reversed(parents):
            os.close(descriptor)


def _audit_pending_activation_install(
    canonical_root_fd: int,
    artifact: StagedArtifact,
    *,
    transaction_id: str,
    step_index: int,
) -> None:
    _, components = _relative_path(
        artifact.relative_path,
        f"activation {artifact.role} audit path",
    )
    current = os.dup(canonical_root_fd)
    parents = [current]
    try:
        for component in components[:-1]:
            try:
                os.stat(component, dir_fd=current, follow_symlinks=False)
            except FileNotFoundError:
                return
            except OSError as error:
                raise DeploymentError(
                    f"installed {artifact.role} directory cannot be inspected"
                ) from error
            child = _open_private_directory(
                current,
                component,
                f"installed {artifact.role} directory",
                create=False,
            )
            parents.append(child)
            current = child
        name = components[-1]
        temporary = _activation_artifact_temporary_name(
            transaction_id,
            step_index,
        )
        final_entry = _read_activation_artifact_entry(
            current,
            name,
            artifact,
            label=f"installed {artifact.role}",
            allowed_nlinks={1, 2},
            absent=True,
        )
        temporary_entry = _read_activation_install_temporary(
            current,
            temporary,
            artifact,
            allowed_nlinks={1, 2},
            absent=True,
        )
        if final_entry is None and temporary_entry is None:
            return
        if final_entry is None:
            temporary_metadata, temporary_raw = temporary_entry
            if (
                not _activation_install_temporary_is_owned(
                    temporary_metadata,
                    temporary_raw,
                    artifact,
                )
                or temporary_metadata.st_nlink != 1
            ):
                raise DeploymentError(
                    f"activation {artifact.role} pending install disagrees"
                )
            return
        final_metadata, final_raw = final_entry
        if final_raw != artifact.raw:
            raise DeploymentError(
                f"activation {artifact.role} pending install disagrees"
            )
        if temporary_entry is None:
            if final_metadata.st_nlink != 1:
                raise DeploymentError(
                    f"activation {artifact.role} pending install disagrees"
                )
            return
        temporary_metadata, temporary_raw = temporary_entry
        if (
            final_metadata.st_nlink != 2
            or temporary_metadata.st_nlink != 2
            or (final_metadata.st_dev, final_metadata.st_ino)
            != (temporary_metadata.st_dev, temporary_metadata.st_ino)
            or not _activation_install_temporary_is_owned(
                temporary_metadata,
                temporary_raw,
                artifact,
            )
        ):
            raise DeploymentError(
                f"activation {artifact.role} pending install disagrees"
            )
    finally:
        for descriptor in reversed(parents):
            os.close(descriptor)


def _routine_install_temporary_inventory(
    canonical_root_fd: int,
    artifacts: tuple[StagedArtifact, ...],
) -> frozenset[str]:
    directories = {""}
    directories.update(
        directory
        for artifact in artifacts
        for directory in _activation_artifact_parent_directories(artifact)
    )
    temporaries: set[str] = set()
    for relative_directory in sorted(directories):
        current = os.dup(canonical_root_fd)
        try:
            available = True
            if relative_directory:
                _, components = _relative_path(
                    f"{relative_directory}/entry",
                    "routine activation install temporary directory",
                )
                for component in components[:-1]:
                    try:
                        os.stat(
                            component,
                            dir_fd=current,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        available = False
                        break
                    except OSError as error:
                        raise DeploymentError(
                            "routine activation install temporary directory "
                            "cannot be inspected"
                        ) from error
                    child = _open_private_directory(
                        current,
                        component,
                        "routine activation install temporary directory",
                        create=False,
                    )
                    os.close(current)
                    current = child
            if not available:
                continue
            try:
                names = os.listdir(current)
            except OSError as error:
                raise DeploymentError(
                    "routine activation install temporary inventory is unavailable"
                ) from error
            for name in names:
                if name.startswith(".task-witness-install-"):
                    temporaries.add(
                        name
                        if not relative_directory
                        else f"{relative_directory}/{name}"
                    )
        finally:
            os.close(current)
    return frozenset(temporaries)


def _audit_routine_install_temporary_inventory(
    canonical_root_fd: int,
    artifacts: tuple[StagedArtifact, ...],
    journal: _ActivationJournal,
) -> frozenset[str]:
    allowed: set[str] = set()
    if journal.value["phase"] == "additive-installing":
        index = journal.value["pending_step"]["index"]
        artifact = artifacts[index]
        parent = "/".join(artifact.relative_path.split("/")[:-1])
        temporary = _activation_artifact_temporary_name(
            journal.value["transaction_id"],
            index,
        )
        allowed.add(temporary if not parent else f"{parent}/{temporary}")
    observed = _routine_install_temporary_inventory(
        canonical_root_fd,
        artifacts,
    )
    if not observed.issubset(allowed):
        raise DeploymentError(
            "routine activation install temporary inventory disagrees"
        )
    return observed


def _routine_root_relative_file(
    canonical_root: Path,
    value: object,
    label: str,
) -> str:
    path = _normalized_absolute_path(
        Path(_text(value, label)),
        label,
    )
    try:
        relative = path.relative_to(canonical_root).as_posix()
    except ValueError as error:
        raise DeploymentError(f"{label} escapes the canonical root") from error
    normalized, _ = _relative_path(relative, label)
    return normalized


def _routine_baseline_file_inventory(
    precondition: ActiveDeploymentPrecondition,
) -> frozenset[str]:
    canonical_root = precondition.canonical_root
    active = _active_runtime_record_shape(
        precondition.receipt_value,
        precondition.active_raw,
        canonical_root,
        "routine activation baseline active record",
    )
    payload_paths = tuple(item["relative_path"] for item in active["payloads"])
    files = {
        "activation.lock",
        "active.json",
        "deployment.json",
    }
    files.update(precondition.retained_result_raws)
    files.update(
        f"receipts/{name}" for name in precondition.retained_chain.receipt_names
    )
    for _, receipt, _ in precondition.retained_chain.deployment_receipts:
        for role, binding in receipt["control_set"].items():
            files.add(
                _routine_root_relative_file(
                    canonical_root,
                    binding["path"],
                    f"routine activation baseline control {role}",
                )
            )
        generation = _text(
            receipt["active"]["generation"],
            "routine activation baseline generation",
        )
        for relative_path in payload_paths:
            files.add(f"generations/{generation}/{relative_path}")
        files.add(
            _routine_root_relative_file(
                canonical_root,
                receipt["smoke"]["bundle"]["manifest"]["path"],
                "routine activation baseline smoke manifest",
            )
        )
        trust_bindings = [receipt["trust_context"]]
        trust_bindings.extend(receipt["historical_trust_contexts"])
        for index, binding in enumerate(trust_bindings):
            files.add(
                _routine_root_relative_file(
                    canonical_root,
                    binding["path"],
                    f"routine activation baseline trust context {index}",
                )
            )
        for provider_index, provider in enumerate(receipt["providers"]):
            for module_index, module in enumerate(provider["retained_modules"]):
                files.add(
                    _routine_root_relative_file(
                        canonical_root,
                        module["path"],
                        "routine activation baseline provider "
                        f"{provider_index} module {module_index}",
                    )
                )
    return frozenset(files)


def _routine_file_parent_inventory(files: Iterable[str]) -> frozenset[str]:
    directories: set[str] = set()
    for relative_path in files:
        _, components = _relative_path(
            relative_path,
            "routine activation tree file",
        )
        directories.update(
            "/".join(components[:index]) for index in range(1, len(components))
        )
    return frozenset(directories)


def _audit_routine_tree_inventory(
    canonical_root_fd: int,
    precondition: ActiveDeploymentPrecondition,
    *,
    exact_artifacts: Iterable[StagedArtifact],
    optional_artifact: StagedArtifact | None,
    optional_directory: str | None,
    selector_temporaries: Iterable[str],
    install_temporaries: Iterable[str],
    journal_temporary: str | None,
    required_directory_paths: Iterable[str] = (),
    retained_result_directories: Iterable[str] = (),
    retained_result_files: Iterable[str] = (),
    pending_result_directory_residues: frozenset[str] = frozenset(),
    live_journal_required: bool = True,
) -> tuple[frozenset[str], frozenset[str]]:
    baseline_files = set(_routine_baseline_file_inventory(precondition))
    required_files = set(baseline_files)
    if live_journal_required:
        required_files.add("transaction.json")
    required_files.update(retained_result_files)
    exact_paths = {artifact.relative_path for artifact in exact_artifacts}
    required_files.update(exact_paths)
    allowed_files = set(required_files)
    optional_paths: set[str] = set()
    if optional_artifact is not None:
        optional_paths.add(optional_artifact.relative_path)
    allowed_files.update(optional_paths)
    allowed_files.update(selector_temporaries)
    allowed_files.update(install_temporaries)
    if journal_temporary is not None:
        allowed_files.add(journal_temporary)

    required_directories = set(_routine_file_parent_inventory(required_files))
    required_directories.update(retained_result_directories)
    for value in required_directory_paths:
        _, components = _relative_path(
            value,
            "routine activation required directory",
        )
        required_directories.update(
            "/".join(components[:index]) for index in range(1, len(components) + 1)
        )
    allowed_directories = set(required_directories)
    allowed_directories.update(_routine_file_parent_inventory(optional_paths))
    if optional_directory is not None:
        _, components = _relative_path(
            optional_directory,
            "routine activation optional cleanup directory",
        )
        allowed_directories.update(
            "/".join(components[:index]) for index in range(1, len(components) + 1)
        )
    live_directories, live_files = _activation_tree_inventory(
        canonical_root_fd,
        pending_directory_residues=pending_result_directory_residues,
    )
    if (
        not required_directories.issubset(live_directories)
        or not live_directories.issubset(allowed_directories)
        or not required_files.issubset(live_files)
        or not live_files.issubset(allowed_files)
    ):
        raise DeploymentError("routine activation tree inventory disagrees")
    return frozenset(live_directories), frozenset(live_files)


def _audit_first_install_live_state(
    canonical_root_fd: int,
    prepared: PreparedFirstInstall,
    journal: _ActivationJournal,
    artifacts: tuple[StagedArtifact, ...],
    removals: tuple[_ActivationRemovalStep, ...],
    *,
    journal_temporary: str | None = None,
    pending_directory_residues: frozenset[str] = frozenset(),
    opaque_pending_result_temporary: bool = False,
    live_journal_required: bool = True,
) -> None:
    try:
        lock = os.stat(
            "activation.lock",
            dir_fd=canonical_root_fd,
            follow_symlinks=False,
        )
    except OSError as error:
        raise DeploymentError("activation transaction lock is unavailable") from error
    if _identity(lock) != prepared.plan.precondition.activation_lock_identity:
        raise DeploymentError("activation transaction lock binding disagrees")
    if live_journal_required and (
        _read_activation_file(
            canonical_root_fd,
            "transaction.json",
            "activation transaction journal",
        )
        != journal.raw
    ):
        raise DeploymentError("activation transaction journal changed during audit")

    pending_result = (
        journal
        if journal.value["phase"] == "terminal"
        and journal.value["terminal_result"]["outcome"] != "recovery-required"
        else None
    )
    retained_result_raws, _, retained_result_inventory = (
        _capture_transaction_result_inventory(
            canonical_root_fd,
            prepared.plan.precondition.canonical_root,
            pending=pending_result,
            opaque_pending_temporary=opaque_pending_result_temporary,
        )
    )
    _validate_transaction_result_baseline(
        retained_result_raws,
        prepared.plan.precondition.retained_result_sha256s,
        pending=pending_result,
    )
    retained_result_directories, retained_result_files = retained_result_inventory

    result_directory_residues = frozenset(
        item
        for item in retained_result_directories
        if item.startswith(".task-witness-results-")
    )
    live_directories, live_files = _activation_tree_inventory(
        canonical_root_fd,
        pending_directory_residues=(
            pending_directory_residues | result_directory_residues
        ),
    )
    base_files = {"activation.lock"}
    if live_journal_required:
        base_files.add("transaction.json")
    base_files.update(retained_result_files)
    if journal_temporary is not None:
        base_files.add(journal_temporary)
    all_directories = {
        directory
        for artifact in artifacts
        for directory in _activation_artifact_parent_directories(artifact)
    }
    phase = journal.value["phase"]
    pending = journal.value["pending_step"]
    exact_artifacts: list[StagedArtifact] = []
    optional_artifact: StagedArtifact | None = None
    pending_install: tuple[StagedArtifact, int] | None = None
    allowed_directory_sets: tuple[set[str], ...]

    if phase in {"prepared", "frozen", "drained"}:
        allowed_directory_sets = (set(),)
    elif phase == "control-installing":
        index = pending["index"]
        exact_artifacts.extend(artifacts[:index])
        pending_install = (artifacts[index], index)
        completed_directories = {
            directory
            for artifact in artifacts[:index]
            for directory in _activation_artifact_parent_directories(artifact)
        }
        candidates: list[set[str]] = [set(completed_directories)]
        growing = set(completed_directories)
        for directory in _activation_artifact_parent_directories(artifacts[index]):
            growing = {*growing, directory}
            if growing not in candidates:
                candidates.append(set(growing))
        allowed_directory_sets = tuple(candidates)
    elif phase in {"candidate-smoke", "candidate-accepted"} or (
        phase == "absence-restoring" and pending is None
    ):
        exact_artifacts.extend(artifacts)
        allowed_directory_sets = (set(all_directories),)
    elif phase == "absence-restoring":
        removal_index = pending["index"]
        step = removals[removal_index]
        artifact_steps = len(artifacts)
        if removal_index < artifact_steps:
            removed_roles = {
                removal.artifact.role
                for removal in removals[:removal_index]
                if removal.artifact is not None
            }
            optional_artifact = step.artifact
            exact_artifacts.extend(
                artifact
                for artifact in artifacts
                if artifact.role not in removed_roles
                and artifact is not optional_artifact
            )
            allowed_directory_sets = (set(all_directories),)
        else:
            prior_directories = {
                removal.relative_path
                for removal in removals[artifact_steps:removal_index]
                if removal.relative_path is not None
            }
            current_directory = step.relative_path
            present = set(all_directories) - prior_directories
            absent = present - {current_directory}
            allowed_directory_sets = (present, absent)
    elif phase == "absence-accepted":
        allowed_directory_sets = (set(),)
    elif phase == "terminal":
        if journal.value["terminal_result"]["outcome"] == "candidate-active":
            exact_artifacts.extend(artifacts)
            allowed_directory_sets = (set(all_directories),)
        else:
            allowed_directory_sets = (set(),)
    else:
        raise DeploymentError("activation transaction phase is unsupported")

    if live_directories not in allowed_directory_sets:
        with_results = tuple(
            set(item) | set(retained_result_directories)
            for item in allowed_directory_sets
        )
        if live_directories not in with_results:
            raise DeploymentError(
                "activation transaction directory inventory disagrees"
            )
    allowed_files = set(base_files)
    allowed_files.update(artifact.relative_path for artifact in exact_artifacts)
    if optional_artifact is not None:
        allowed_files.add(optional_artifact.relative_path)
    if pending_install is not None:
        artifact, index = pending_install
        allowed_files.add(artifact.relative_path)
        temporary_name = _activation_artifact_temporary_name(
            journal.value["transaction_id"],
            index,
        )
        parent = "/".join(artifact.relative_path.split("/")[:-1])
        allowed_files.add(
            temporary_name if not parent else f"{parent}/{temporary_name}"
        )
    if not base_files.issubset(live_files) or not live_files.issubset(allowed_files):
        raise DeploymentError("activation transaction file inventory disagrees")
    for artifact in exact_artifacts:
        if artifact.relative_path not in live_files:
            raise DeploymentError("activation transaction artifact prefix disagrees")
        _audit_activation_artifact(canonical_root_fd, artifact)
    if optional_artifact is not None and optional_artifact.relative_path in live_files:
        _audit_activation_artifact(canonical_root_fd, optional_artifact)
    if pending_install is not None:
        artifact, index = pending_install
        parent_directories = set(_activation_artifact_parent_directories(artifact))
        if artifact.relative_path in live_files or any(
            path.endswith(
                _activation_artifact_temporary_name(
                    journal.value["transaction_id"],
                    index,
                )
            )
            for path in live_files
        ):
            if not parent_directories.issubset(live_directories):
                raise DeploymentError(
                    "activation transaction install parent inventory disagrees"
                )
            _audit_pending_activation_install(
                canonical_root_fd,
                artifact,
                transaction_id=journal.value["transaction_id"],
                step_index=index,
            )


def _audit_routine_receipt_inventory(
    canonical_root: Path,
    expected_names: set[str],
    *,
    pending_final: str | None = None,
    pending_temporary: str | None = None,
) -> None:
    if pending_final is not None and pending_temporary is None:
        raise DeploymentError("routine activation pending receipt binding disagrees")
    receipts = canonical_root / "receipts"
    try:
        names = set(os.listdir(receipts))
    except OSError as error:
        raise DeploymentError(
            "routine activation receipt inventory is unavailable"
        ) from error
    expected_inventory = set(expected_names)
    if pending_temporary is not None:
        expected_inventory.add(pending_temporary)
    if names != expected_inventory:
        raise DeploymentError("routine activation receipt inventory disagrees")
    for name in names:
        if name in {pending_final, pending_temporary}:
            continue
        if not name.startswith("sha256-") or not name.endswith(".json"):
            raise DeploymentError("routine activation receipt name disagrees")
        digest = name[len("sha256-") : -len(".json")]
        _sha256(digest, "routine activation receipt filename")
        path = receipts / name
        raw = _capture_absolute_regular(
            path, MAX_JSON_BYTES, "routine activation receipt"
        )
        metadata = path.lstat()
        if (
            hashlib.sha256(raw).hexdigest() != digest
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise DeploymentError("routine activation receipt binding disagrees")


def _audit_routine_live_state(
    activation_root: _RootSnapshot,
    activation_lock_fd: int,
    precondition: ActiveDeploymentPrecondition,
    verified: VerifiedDeploymentStage,
    journal: _ActivationJournal,
    artifacts: tuple[StagedArtifact, ...],
    *,
    journal_temporary: str | None = None,
    opaque_pending_result_temporary: bool = False,
    live_journal_required: bool = True,
) -> None:
    root_fd = activation_root.fd
    _recheck_activation_lock_acquisition(
        activation_root,
        activation_lock_fd,
        precondition,
        root_mapping_only=True,
    )
    if live_journal_required and (
        _read_activation_file(
            root_fd,
            "transaction.json",
            "routine activation transaction journal",
        )
        != journal.raw
    ):
        raise DeploymentError("routine activation journal changed during audit")
    pending_result = (
        journal
        if journal.value["phase"] == "terminal"
        and journal.value["terminal_result"]["outcome"] != "recovery-required"
        else None
    )
    _, _, retained_result_inventory = _capture_transaction_result_inventory(
        root_fd,
        precondition.canonical_root,
        pending=pending_result,
        opaque_pending_temporary=opaque_pending_result_temporary,
    )
    retained_result_directories, retained_result_files = retained_result_inventory
    result_directory_residues = frozenset(
        item
        for item in retained_result_directories
        if item.startswith(".task-witness-results-")
    )
    prior_active = _staged_artifact_for_role(
        verified.artifacts,
        "prior-active-record",
    )
    prior_deployment = _staged_artifact_for_role(
        verified.artifacts,
        "prior-deployment-alias",
    )
    candidate_active = _staged_artifact_for_role(
        verified.artifacts,
        "active-record",
    )
    candidate_deployment = _staged_artifact_for_role(
        verified.artifacts,
        "deployment-alias",
    )
    prior_selectors = (prior_active.raw, prior_deployment.raw)
    candidate_selectors = (candidate_active.raw, candidate_deployment.raw)
    live_selectors = (
        _read_activation_file(root_fd, "active.json", "routine active selector"),
        _read_activation_file(
            root_fd,
            "deployment.json",
            "routine deployment selector",
        ),
    )
    phase = journal.value["phase"]
    pending = journal.value["pending_step"]
    if phase == "selector-switching":
        allowed = (
            {prior_selectors, (candidate_selectors[0], prior_selectors[1])}
            if pending["index"] == 0
            else {
                (candidate_selectors[0], prior_selectors[1]),
                candidate_selectors,
            }
        )
    elif phase == "prior-restoring":
        allowed = (
            {candidate_selectors, (prior_selectors[0], candidate_selectors[1])}
            if pending["index"] == 0
            else {
                (prior_selectors[0], candidate_selectors[1]),
                prior_selectors,
            }
        )
    elif phase in {"candidate-smoke", "candidate-accepted"} or (
        phase == "terminal"
        and journal.value["terminal_result"]["outcome"] == "candidate-active"
    ):
        allowed = {candidate_selectors}
    else:
        allowed = {prior_selectors}
    if live_selectors not in allowed:
        raise DeploymentError("routine activation selector state disagrees")

    try:
        selector_temporaries = sorted(
            name
            for name in os.listdir(root_fd)
            if name.startswith(".task-witness-selector-") and name.endswith(".tmp")
        )
    except OSError as error:
        raise DeploymentError(
            "routine activation selector temporary inventory is unavailable"
        ) from error
    if phase in {"selector-switching", "prior-restoring"}:
        index = pending["index"]
        direction = "candidate" if phase == "selector-switching" else "prior"
        target = (
            (candidate_active, candidate_deployment)[index]
            if direction == "candidate"
            else (prior_active, prior_deployment)[index]
        )
        expected_temporary = _activation_selector_temporary_name(
            journal.value["transaction_id"],
            direction,
            index,
        )
        if selector_temporaries not in ([], [expected_temporary]):
            raise DeploymentError(
                "routine activation selector temporary inventory disagrees"
            )
        if selector_temporaries:
            _read_activation_selector_temporary(
                root_fd,
                expected_temporary,
                target,
            )
    elif selector_temporaries:
        raise DeploymentError(
            "routine activation selector temporary inventory disagrees"
        )

    install_temporaries = _audit_routine_install_temporary_inventory(
        root_fd,
        artifacts,
        journal,
    )

    exact: list[StagedArtifact]
    optional: StagedArtifact | None = None
    optional_directory: str | None = None
    if phase in {"prepared", "frozen", "drained"}:
        exact = []
    elif phase == "additive-installing":
        index = pending["index"]
        exact = list(artifacts[:index])
        optional = artifacts[index]
    else:
        exact = list(artifacts)
    if phase == "rollback-cleaning":
        cleanups = _ordered_routine_cleanup_steps(precondition, verified)
        cleanup_index = pending["index"]
        completed_paths = {
            step.artifact.relative_path
            for step in cleanups[:cleanup_index]
            if step.artifact is not None
        }
        current = cleanups[cleanup_index]
        if current.artifact is not None:
            completed_paths.add(current.artifact.relative_path)
            optional = current.artifact
        else:
            optional_directory = current.relative_path
        exact = [item for item in exact if item.relative_path not in completed_paths]
    elif (
        phase == "terminal"
        and journal.value["terminal_result"]["outcome"] == "restored-prior"
    ):
        cleanups = _ordered_routine_cleanup_steps(precondition, verified)
        removed_paths = {
            step.artifact.relative_path
            for step in cleanups
            if step.artifact is not None
        }
        exact = [item for item in exact if item.relative_path not in removed_paths]
    tree_inventory = _audit_routine_tree_inventory(
        root_fd,
        precondition,
        exact_artifacts=exact,
        optional_artifact=optional,
        optional_directory=optional_directory,
        selector_temporaries=selector_temporaries,
        install_temporaries=install_temporaries,
        journal_temporary=journal_temporary,
        retained_result_directories=retained_result_directories,
        retained_result_files=retained_result_files,
        pending_result_directory_residues=result_directory_residues,
        live_journal_required=live_journal_required,
    )
    for artifact in exact:
        _audit_activation_artifact(root_fd, artifact)
    optional_present = False
    pending_receipt_final: str | None = None
    pending_receipt_temporary: str | None = None
    if optional is not None:
        if phase == "additive-installing":
            _audit_pending_activation_install(
                root_fd,
                optional,
                transaction_id=journal.value["transaction_id"],
                step_index=pending["index"],
            )
        try:
            optional.installed_path.lstat()
        except FileNotFoundError:
            pass
        else:
            if phase != "additive-installing":
                _audit_activation_artifact(root_fd, optional)
            optional_present = True
        if phase == "additive-installing" and optional.role in {
            "rollback-receipt",
            "deployment-receipt",
        }:
            temporary = _activation_artifact_temporary_name(
                journal.value["transaction_id"],
                pending["index"],
            )
            if (optional.installed_path.parent / temporary).exists():
                if optional_present:
                    pending_receipt_final = optional.installed_path.name
                pending_receipt_temporary = temporary
    receipt_names = set(precondition.retained_chain.receipt_names)
    for artifact in exact:
        if artifact.role in {"rollback-receipt", "deployment-receipt"}:
            receipt_names.add(artifact.installed_path.name)
    if (
        optional is not None
        and optional_present
        and optional.role in {"rollback-receipt", "deployment-receipt"}
    ):
        receipt_names.add(optional.installed_path.name)
    _audit_routine_receipt_inventory(
        precondition.canonical_root,
        receipt_names,
        pending_final=pending_receipt_final,
        pending_temporary=pending_receipt_temporary,
    )
    if (
        _audit_routine_install_temporary_inventory(
            root_fd,
            artifacts,
            journal,
        )
        != install_temporaries
    ):
        raise DeploymentError("routine activation install temporary inventory changed")
    if (
        _audit_routine_tree_inventory(
            root_fd,
            precondition,
            exact_artifacts=exact,
            optional_artifact=optional,
            optional_directory=optional_directory,
            selector_temporaries=selector_temporaries,
            install_temporaries=install_temporaries,
            journal_temporary=journal_temporary,
            retained_result_directories=retained_result_directories,
            retained_result_files=retained_result_files,
            pending_result_directory_residues=result_directory_residues,
            live_journal_required=live_journal_required,
        )
        != tree_inventory
    ):
        raise DeploymentError("routine activation tree inventory changed")


def _audit_control_maintenance_replacements(
    canonical_root_fd: int,
    canonical_root: Path,
    journal: _ActivationJournal,
    replacements: tuple[_ControlMaintenanceReplacement, ...],
) -> frozenset[str]:
    phase = journal.value["phase"]
    pending = journal.value["pending_step"]
    switching_direction = {
        "control-switching": "candidate",
        "prior-restoring": "prior",
    }.get(phase)
    switching_index = pending["index"] if switching_direction is not None else None
    candidate_live = phase in {"candidate-smoke", "candidate-accepted"} or (
        phase == "terminal"
        and journal.value["terminal_result"]["outcome"]
        in {"candidate-active", "manual-target-active"}
    )
    temporaries: set[str] = set()
    for index, replacement in enumerate(replacements):
        _, components = _control_maintenance_live_relative_path(
            canonical_root,
            replacement.target,
        )
        current_fd = os.dup(canonical_root_fd)
        parents = [current_fd]
        try:
            for component in components[:-1]:
                child = _open_private_directory(
                    current_fd,
                    component,
                    f"control maintenance {replacement.role} audit parent",
                    create=False,
                )
                parents.append(child)
                current_fd = child
            live = _read_control_maintenance_live_artifact(
                current_fd,
                components[-1],
                replacement.current,
                replacement.target,
            )
            if candidate_live or (
                switching_direction == "candidate" and index < switching_index
            ):
                allowed = {replacement.target.raw}
            elif switching_direction == "candidate" and switching_index == index:
                allowed = {replacement.current.raw, replacement.target.raw}
            elif switching_direction == "prior" and index < switching_index:
                allowed = {replacement.current.raw}
            elif switching_direction == "prior" and switching_index == index:
                allowed = {replacement.current.raw, replacement.target.raw}
            elif switching_direction == "prior":
                allowed = {replacement.target.raw}
            else:
                allowed = {replacement.current.raw}
            if live not in allowed:
                raise DeploymentError(
                    f"control maintenance {replacement.role} live prefix disagrees"
                )
            temporary = _control_maintenance_temporary_name(
                journal.value["transaction_id"],
                switching_direction or "candidate",
                index,
            )
            temporary_target = (
                replacement.current
                if switching_direction == "prior"
                else replacement.target
            )
            temporary_entry = _read_control_maintenance_temporary(
                current_fd,
                temporary,
                temporary_target,
                absent=True,
            )
            if temporary_entry is not None:
                expected_live = (
                    replacement.target.raw
                    if switching_direction == "prior"
                    else replacement.current.raw
                )
                if switching_index != index or live != expected_live:
                    raise DeploymentError(
                        "control maintenance temporary cursor disagrees"
                    )
                parent = "/".join(components[:-1])
                temporaries.add(temporary if not parent else f"{parent}/{temporary}")
        finally:
            for descriptor in reversed(parents):
                os.close(descriptor)
    return frozenset(temporaries)


def _audit_control_maintenance_live_state(
    activation_root: _RootSnapshot,
    activation_lock_fd: int,
    precondition: ActiveDeploymentPrecondition,
    journal: _ActivationJournal,
    additive: tuple[StagedArtifact, ...],
    replacements: tuple[_ControlMaintenanceReplacement, ...],
    cleanups: tuple[_ActivationRemovalStep, ...],
    *,
    journal_temporary: str | None = None,
    opaque_pending_result_temporary: bool = False,
    live_journal_required: bool = True,
) -> None:
    root_fd = activation_root.fd
    _recheck_activation_lock_acquisition(
        activation_root,
        activation_lock_fd,
        precondition,
        root_mapping_only=True,
    )
    if live_journal_required and (
        _read_activation_file(
            root_fd,
            "transaction.json",
            "control maintenance activation journal",
        )
        != journal.raw
    ):
        raise DeploymentError("control maintenance journal changed during audit")
    pending_result = (
        journal
        if journal.value["phase"] == "terminal"
        and journal.value["terminal_result"]["outcome"] != "recovery-required"
        else None
    )
    _, _, retained_result_inventory = _capture_transaction_result_inventory(
        root_fd,
        precondition.canonical_root,
        pending=pending_result,
        opaque_pending_temporary=opaque_pending_result_temporary,
    )
    retained_result_directories, retained_result_files = retained_result_inventory
    result_directory_residues = frozenset(
        item
        for item in retained_result_directories
        if item.startswith(".task-witness-results-")
    )
    control_temporaries = _audit_control_maintenance_replacements(
        root_fd,
        precondition.canonical_root,
        journal,
        replacements,
    )
    install_temporaries = _audit_routine_install_temporary_inventory(
        root_fd,
        additive,
        journal,
    )
    phase = journal.value["phase"]
    pending = journal.value["pending_step"]
    exact: list[StagedArtifact]
    optional: StagedArtifact | None = None
    optional_directory: str | None = None
    required_directory_paths: tuple[str, ...] = ()
    if phase in {"prepared", "frozen", "drained"}:
        exact = []
    elif phase == "additive-installing":
        index = pending["index"]
        exact = list(additive[:index])
        optional = additive[index]
    else:
        exact = list(additive)
    if phase == "rollback-cleaning":
        cleanup_index = pending["index"]
        completed_paths = {
            step.artifact.relative_path
            for step in cleanups[:cleanup_index]
            if step.artifact is not None
        }
        current = cleanups[cleanup_index]
        if current.artifact is not None:
            completed_paths.add(current.artifact.relative_path)
            optional = current.artifact
        else:
            optional_directory = current.relative_path
        required_directory_paths = tuple(
            step.relative_path
            for step in cleanups[cleanup_index + 1 :]
            if step.operation == "remove-directory" and step.relative_path is not None
        )
        exact = [item for item in exact if item.relative_path not in completed_paths]
    elif phase == "terminal" and journal.value["terminal_result"]["outcome"] in {
        "restored-prior",
        "manual-current-restored",
    }:
        removed_paths = {
            step.artifact.relative_path
            for step in cleanups
            if step.artifact is not None
        }
        exact = [item for item in exact if item.relative_path not in removed_paths]
    tree_inventory = _audit_routine_tree_inventory(
        root_fd,
        precondition,
        exact_artifacts=exact,
        optional_artifact=optional,
        optional_directory=optional_directory,
        selector_temporaries=(),
        install_temporaries={*install_temporaries, *control_temporaries},
        journal_temporary=journal_temporary,
        required_directory_paths=required_directory_paths,
        retained_result_directories=retained_result_directories,
        retained_result_files=retained_result_files,
        pending_result_directory_residues=result_directory_residues,
        live_journal_required=live_journal_required,
    )
    for artifact in exact:
        _audit_activation_artifact(root_fd, artifact)
    optional_present = False
    pending_receipt_final: str | None = None
    pending_receipt_temporary: str | None = None
    if optional is not None:
        if phase == "additive-installing":
            _audit_pending_activation_install(
                root_fd,
                optional,
                transaction_id=journal.value["transaction_id"],
                step_index=pending["index"],
            )
        try:
            optional.installed_path.lstat()
        except FileNotFoundError:
            pass
        else:
            if phase != "additive-installing":
                _audit_activation_artifact(root_fd, optional)
            optional_present = True
        if phase == "additive-installing" and optional.role in {
            "rollback-receipt",
            "deployment-receipt",
        }:
            temporary = _activation_artifact_temporary_name(
                journal.value["transaction_id"],
                pending["index"],
            )
            if (optional.installed_path.parent / temporary).exists():
                if optional_present:
                    pending_receipt_final = optional.installed_path.name
                pending_receipt_temporary = temporary
    receipt_names = set(precondition.retained_chain.receipt_names)
    for artifact in exact:
        if artifact.role in {"rollback-receipt", "deployment-receipt"}:
            receipt_names.add(artifact.installed_path.name)
    if (
        optional_present
        and optional is not None
        and optional.role
        in {
            "rollback-receipt",
            "deployment-receipt",
        }
    ):
        receipt_names.add(optional.installed_path.name)
    _audit_routine_receipt_inventory(
        precondition.canonical_root,
        receipt_names,
        pending_final=pending_receipt_final,
        pending_temporary=pending_receipt_temporary,
    )
    if (
        _audit_control_maintenance_replacements(
            root_fd,
            precondition.canonical_root,
            journal,
            replacements,
        )
        != control_temporaries
        or _audit_routine_install_temporary_inventory(
            root_fd,
            additive,
            journal,
        )
        != install_temporaries
        or _audit_routine_tree_inventory(
            root_fd,
            precondition,
            exact_artifacts=exact,
            optional_artifact=optional,
            optional_directory=optional_directory,
            selector_temporaries=(),
            install_temporaries={*install_temporaries, *control_temporaries},
            journal_temporary=journal_temporary,
            required_directory_paths=required_directory_paths,
            retained_result_directories=retained_result_directories,
            retained_result_files=retained_result_files,
            pending_result_directory_residues=result_directory_residues,
            live_journal_required=live_journal_required,
        )
        != tree_inventory
    ):
        raise DeploymentError("control maintenance live inventory changed")


def _continue_first_install_transaction(
    activation_root: _RootSnapshot,
    activation_lock_fd: int,
    prepared: PreparedFirstInstall,
    verified: VerifiedDeploymentStage,
    intent: Mapping[str, Any],
    journal: _ActivationJournal | None,
) -> TransactionResult:
    canonical_root_fd = activation_root.fd
    artifacts = _ordered_activation_artifacts(verified)
    removals = _ordered_activation_removal_steps(artifacts)
    transaction_id = intent["transaction_id"]
    candidate_receipt_sha256 = intent["candidate_receipt_sha256"]
    smoke = intent["candidate"]["smoke"]
    smoke_handoff = {
        "target_deployment_receipt_sha256": candidate_receipt_sha256,
        "smoke_bundle_sha256": smoke["bundle"]["sha256"],
        "smoke_trust_context_sha256": smoke["trust_context"]["sha256"],
    }
    if journal is None:
        _recheck_activation_lock_acquisition(
            activation_root,
            activation_lock_fd,
            prepared.plan.precondition,
        )
        journal = _advance_activation_journal(
            canonical_root_fd,
            intent,
            None,
            "prepared",
        )
    while True:
        _validate_activation_journal_authority(
            journal,
            intent,
            artifacts,
            removals,
        )
        _audit_first_install_live_state(
            canonical_root_fd,
            prepared,
            journal,
            artifacts,
            removals,
        )
        _recheck_activation_lock_acquisition(
            activation_root,
            activation_lock_fd,
            prepared.plan.precondition,
            root_mapping_only=True,
        )
        phase = journal.value["phase"]
        if phase == "prepared":
            journal = _advance_activation_journal(
                canonical_root_fd,
                intent,
                journal,
                "frozen",
            )
            continue
        if phase == "frozen":
            journal = _advance_activation_journal(
                canonical_root_fd,
                intent,
                journal,
                "drained",
            )
            continue
        if phase == "drained":
            artifact = artifacts[0]
            journal = _advance_activation_journal(
                canonical_root_fd,
                intent,
                journal,
                "control-installing",
                pending_step={
                    "operation": "install",
                    "index": 0,
                    "role": artifact.role,
                },
            )
            continue
        if phase == "control-installing":
            index = journal.value["pending_step"]["index"]
            artifact = artifacts[index]
            directory_repair_paths = frozenset(
                _activation_pending_directory_suffix(artifacts, index)
            )
            _install_activation_artifact(
                canonical_root_fd,
                prepared.plan.precondition.canonical_root,
                artifact,
                transaction_id=transaction_id,
                step_index=index,
                directory_repair_paths=directory_repair_paths,
            )
            if index + 1 < len(artifacts):
                following = artifacts[index + 1]
                journal = _advance_activation_journal(
                    canonical_root_fd,
                    intent,
                    journal,
                    "control-installing",
                    pending_step={
                        "operation": "install",
                        "index": index + 1,
                        "role": following.role,
                    },
                )
            else:
                journal = _advance_activation_journal(
                    canonical_root_fd,
                    intent,
                    journal,
                    "candidate-smoke",
                    smoke_handoff=smoke_handoff,
                )
            continue
        if phase == "candidate-smoke":
            acceptance = journal.value["candidate_smoke_acceptance"]
            if acceptance is not None:
                journal = _advance_activation_journal(
                    canonical_root_fd,
                    intent,
                    journal,
                    "candidate-accepted",
                    candidate_smoke_acceptance=acceptance,
                )
                continue
            _recheck_activation_lock_acquisition(
                activation_root,
                activation_lock_fd,
                prepared.plan.precondition,
                root_mapping_only=True,
            )
            child = _run_activation_smoke(
                prepared.plan.precondition.canonical_root,
                activation_lock_fd,
            )
            accepted_envelope_sha256 = hashlib.sha256(child.stdout).hexdigest()
            if (
                child.returncode == 0
                and child.stderr == b""
                and accepted_envelope_sha256 == smoke["expected_envelope_sha256"]
            ):
                acceptance = _smoke_acceptance(
                    phase="candidate-smoke",
                    candidate_receipt_sha256=candidate_receipt_sha256,
                    expected_envelope_sha256=smoke["expected_envelope_sha256"],
                    accepted_envelope_sha256=accepted_envelope_sha256,
                )
                journal = _advance_activation_journal(
                    canonical_root_fd,
                    intent,
                    journal,
                    "candidate-smoke",
                    smoke_handoff=smoke_handoff,
                    candidate_smoke_acceptance=acceptance,
                )
            else:
                journal = _advance_activation_journal(
                    canonical_root_fd,
                    intent,
                    journal,
                    "absence-restoring",
                )
            continue
        if phase == "candidate-accepted":
            acceptance = journal.value["candidate_smoke_acceptance"]
            terminal = {
                "outcome": "candidate-active",
                "candidate_receipt_sha256": candidate_receipt_sha256,
                "active_receipt_sha256": candidate_receipt_sha256,
                "accepted_envelope_sha256": acceptance["accepted_envelope_sha256"],
                "failure_class": None,
            }
            journal = _advance_activation_journal(
                canonical_root_fd,
                intent,
                journal,
                "terminal",
                candidate_smoke_acceptance=acceptance,
                terminal_result=terminal,
            )
            continue
        if phase == "absence-restoring":
            pending = journal.value["pending_step"]
            if pending is None:
                step = removals[0]
                journal = _advance_activation_journal(
                    canonical_root_fd,
                    intent,
                    journal,
                    "absence-restoring",
                    pending_step={
                        "operation": step.operation,
                        "index": step.index,
                        "role": step.role,
                    },
                )
                continue
            step = removals[pending["index"]]
            if step.operation == "remove-artifact":
                if step.artifact is None:
                    raise DeploymentError("activation removal artifact is missing")
                _remove_activation_artifact(canonical_root_fd, step.artifact)
            else:
                if step.relative_path is None:
                    raise DeploymentError("activation removal directory is missing")
                _remove_activation_directory(
                    canonical_root_fd,
                    step.relative_path,
                )
            if step.index + 1 < len(removals):
                following = removals[step.index + 1]
                journal = _advance_activation_journal(
                    canonical_root_fd,
                    intent,
                    journal,
                    "absence-restoring",
                    pending_step={
                        "operation": following.operation,
                        "index": following.index,
                        "role": following.role,
                    },
                )
            else:
                journal = _advance_activation_journal(
                    canonical_root_fd,
                    intent,
                    journal,
                    "absence-accepted",
                )
            continue
        if phase == "absence-accepted":
            _verify_absent_activation_preimage(
                canonical_root_fd,
                prepared.plan.precondition,
            )
            terminal = {
                "outcome": "restored-absent",
                "candidate_receipt_sha256": candidate_receipt_sha256,
                "active_receipt_sha256": None,
                "accepted_envelope_sha256": None,
                "failure_class": "candidate-smoke-rejected",
            }
            journal = _advance_activation_journal(
                canonical_root_fd,
                intent,
                journal,
                "terminal",
                terminal_result=terminal,
            )
            continue
        if phase != "terminal":
            raise DeploymentError("activation transaction phase is unsupported")
        result = _transaction_result(journal)
        _retain_terminal_transaction_result(
            canonical_root_fd,
            prepared.plan.precondition.canonical_root,
            journal,
        )
        _unlink_activation_journal(canonical_root_fd, journal.raw)
        return result


def _continue_control_maintenance_transaction(
    activation_root: _RootSnapshot,
    activation_lock_fd: int,
    precondition: ActiveDeploymentPrecondition,
    verified: VerifiedDeploymentStage,
    intent: Mapping[str, Any],
    journal: _ActivationJournal | None,
    *,
    source_parser: Callable[[Mapping[str, Any], Path], CandidateSource] | None = None,
    policy_parser: Callable[[bytes], CompatibilityPolicy] | None = None,
) -> TransactionResult:
    root_fd = activation_root.fd
    additive = _ordered_control_maintenance_additive_artifacts(verified)
    replacements = _ordered_control_maintenance_replacements(verified)
    cleanups = _ordered_control_maintenance_cleanup_steps(
        precondition,
        additive,
    )
    transaction_id = intent["transaction_id"]
    candidate_sha256 = intent["candidate_receipt_sha256"]
    prior_sha256 = intent["prior"]["deployment_receipt"]["sha256"]
    candidate_smoke = intent["candidate"]["smoke"]
    prior_smoke = intent["prior"]["smoke"]
    manual_rollback = intent["transaction_class"] == "manual-exact-target-rollback"
    candidate_handoff = {
        "target_deployment_receipt_sha256": candidate_sha256,
        "smoke_bundle_sha256": candidate_smoke["bundle"]["sha256"],
        "smoke_trust_context_sha256": candidate_smoke["trust_context"]["sha256"],
    }
    rollback_handoff = {
        "target_deployment_receipt_sha256": prior_sha256,
        "smoke_bundle_sha256": prior_smoke["bundle"]["sha256"],
        "smoke_trust_context_sha256": prior_smoke["trust_context"]["sha256"],
    }
    if journal is None:
        _revalidate_locked_active_precondition(
            activation_root,
            activation_lock_fd,
            precondition,
            source_parser=source_parser,
            policy_parser=policy_parser,
        )
        journal = _advance_activation_journal(
            root_fd,
            intent,
            None,
            "prepared",
        )
    while True:
        _validate_control_maintenance_journal_authority(
            journal,
            intent,
            additive,
            replacements,
            cleanups,
        )
        _audit_control_maintenance_live_state(
            activation_root,
            activation_lock_fd,
            precondition,
            journal,
            additive,
            replacements,
            cleanups,
        )
        _recheck_activation_lock_acquisition(
            activation_root,
            activation_lock_fd,
            precondition,
            root_mapping_only=True,
        )
        phase = journal.value["phase"]
        if phase == "prepared":
            journal = _advance_activation_journal(root_fd, intent, journal, "frozen")
            continue
        if phase == "frozen":
            journal = _advance_activation_journal(root_fd, intent, journal, "drained")
            continue
        if phase == "drained":
            if additive:
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "additive-installing",
                    pending_step={
                        "operation": "install",
                        "index": 0,
                        "role": additive[0].role,
                    },
                )
            else:
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "control-switching",
                    pending_step={
                        "operation": "replace-control",
                        "index": 0,
                        "role": replacements[0].role,
                    },
                )
            continue
        if phase == "additive-installing":
            index = journal.value["pending_step"]["index"]
            artifact = additive[index]
            _install_activation_artifact(
                root_fd,
                precondition.canonical_root,
                artifact,
                transaction_id=transaction_id,
                step_index=index,
                directory_repair_paths=frozenset(
                    _activation_pending_directory_suffix(additive, index)
                ),
            )
            if index + 1 < len(additive):
                following = additive[index + 1]
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "additive-installing",
                    pending_step={
                        "operation": "install",
                        "index": index + 1,
                        "role": following.role,
                    },
                )
            else:
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "control-switching",
                    pending_step={
                        "operation": "replace-control",
                        "index": 0,
                        "role": replacements[0].role,
                    },
                )
            continue
        if phase == "control-switching":
            index = journal.value["pending_step"]["index"]
            replacement = replacements[index]
            _replace_control_maintenance_artifact(
                root_fd,
                precondition.canonical_root,
                transaction_id=transaction_id,
                direction="candidate",
                index=index,
                replacement=replacement,
            )
            if index + 1 < len(replacements):
                following = replacements[index + 1]
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "control-switching",
                    pending_step={
                        "operation": "replace-control",
                        "index": index + 1,
                        "role": following.role,
                    },
                )
            else:
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "candidate-smoke",
                    smoke_handoff=candidate_handoff,
                )
            continue
        if phase == "candidate-smoke":
            acceptance = journal.value["candidate_smoke_acceptance"]
            if acceptance is not None:
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "candidate-accepted",
                    candidate_smoke_acceptance=acceptance,
                )
                continue
            child = _run_activation_smoke(
                precondition.canonical_root,
                activation_lock_fd,
            )
            accepted_sha256 = hashlib.sha256(child.stdout).hexdigest()
            if (
                child.returncode != 0
                or child.stderr != b""
                or accepted_sha256 != candidate_smoke["expected_envelope_sha256"]
            ):
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "prior-restoring",
                    pending_step={
                        "operation": "replace-control",
                        "index": 0,
                        "role": replacements[0].role,
                    },
                )
                continue
            acceptance = _smoke_acceptance(
                phase="candidate-smoke",
                candidate_receipt_sha256=candidate_sha256,
                expected_envelope_sha256=candidate_smoke["expected_envelope_sha256"],
                accepted_envelope_sha256=accepted_sha256,
            )
            journal = _advance_activation_journal(
                root_fd,
                intent,
                journal,
                "candidate-smoke",
                smoke_handoff=candidate_handoff,
                candidate_smoke_acceptance=acceptance,
            )
            continue
        if phase == "candidate-accepted":
            acceptance = journal.value["candidate_smoke_acceptance"]
            journal = _advance_activation_journal(
                root_fd,
                intent,
                journal,
                "terminal",
                candidate_smoke_acceptance=acceptance,
                terminal_result={
                    "outcome": (
                        "manual-target-active"
                        if manual_rollback
                        else "candidate-active"
                    ),
                    "candidate_receipt_sha256": candidate_sha256,
                    "active_receipt_sha256": candidate_sha256,
                    "accepted_envelope_sha256": acceptance["accepted_envelope_sha256"],
                    "failure_class": None,
                },
            )
            continue
        if phase == "prior-restoring":
            index = journal.value["pending_step"]["index"]
            replacement = replacements[index]
            _replace_control_maintenance_artifact(
                root_fd,
                precondition.canonical_root,
                transaction_id=transaction_id,
                direction="prior",
                index=index,
                replacement=replacement,
            )
            if index + 1 < len(replacements):
                following = replacements[index + 1]
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "prior-restoring",
                    pending_step={
                        "operation": "replace-control",
                        "index": index + 1,
                        "role": following.role,
                    },
                )
            else:
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "rollback-smoke",
                    smoke_handoff=rollback_handoff,
                )
            continue
        if phase == "rollback-smoke":
            acceptance = journal.value["rollback_smoke_acceptance"]
            if acceptance is not None:
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "prior-accepted",
                    rollback_smoke_acceptance=acceptance,
                )
                continue
            child = _run_activation_smoke(
                precondition.canonical_root,
                activation_lock_fd,
            )
            accepted_sha256 = hashlib.sha256(child.stdout).hexdigest()
            if (
                child.returncode == 0
                and child.stderr == b""
                and accepted_sha256 == prior_smoke["expected_envelope_sha256"]
            ):
                acceptance = _smoke_acceptance(
                    phase="rollback-smoke",
                    candidate_receipt_sha256=prior_sha256,
                    expected_envelope_sha256=prior_smoke["expected_envelope_sha256"],
                    accepted_envelope_sha256=accepted_sha256,
                )
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "rollback-smoke",
                    smoke_handoff=rollback_handoff,
                    rollback_smoke_acceptance=acceptance,
                )
            else:
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "terminal",
                    terminal_result={
                        "outcome": "recovery-required",
                        "candidate_receipt_sha256": candidate_sha256,
                        "active_receipt_sha256": None,
                        "accepted_envelope_sha256": None,
                        "failure_class": "rollback-smoke-rejected",
                    },
                )
            continue
        if phase == "prior-accepted":
            step = cleanups[0]
            journal = _advance_activation_journal(
                root_fd,
                intent,
                journal,
                "rollback-cleaning",
                pending_step={
                    "operation": step.operation,
                    "index": 0,
                    "role": step.role,
                },
                rollback_smoke_acceptance=journal.value["rollback_smoke_acceptance"],
            )
            continue
        if phase == "rollback-cleaning":
            index = journal.value["pending_step"]["index"]
            step = cleanups[index]
            if step.operation == "remove-artifact":
                if step.artifact is None or step.relative_path is not None:
                    raise DeploymentError(
                        "control maintenance cleanup artifact binding disagrees"
                    )
                _remove_activation_artifact(root_fd, step.artifact)
            elif step.operation == "remove-directory":
                if step.artifact is not None or step.relative_path is None:
                    raise DeploymentError(
                        "control maintenance cleanup directory binding disagrees"
                    )
                _remove_activation_directory(root_fd, step.relative_path)
            else:
                raise DeploymentError("control maintenance cleanup operation disagrees")
            acceptance = journal.value["rollback_smoke_acceptance"]
            if index + 1 < len(cleanups):
                following = cleanups[index + 1]
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "rollback-cleaning",
                    pending_step={
                        "operation": following.operation,
                        "index": index + 1,
                        "role": following.role,
                    },
                    rollback_smoke_acceptance=acceptance,
                )
            else:
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "terminal",
                    rollback_smoke_acceptance=acceptance,
                    terminal_result={
                        "outcome": (
                            "manual-current-restored"
                            if manual_rollback
                            else "restored-prior"
                        ),
                        "candidate_receipt_sha256": candidate_sha256,
                        "active_receipt_sha256": prior_sha256,
                        "accepted_envelope_sha256": acceptance[
                            "accepted_envelope_sha256"
                        ],
                        "failure_class": (
                            "target-smoke-rejected"
                            if manual_rollback
                            else "candidate-smoke-rejected"
                        ),
                    },
                )
            continue
        if phase != "terminal":
            raise DeploymentError("control maintenance phase is unsupported")
        result = _transaction_result(journal)
        if result.outcome != "recovery-required":
            _retain_terminal_transaction_result(
                root_fd,
                precondition.canonical_root,
                journal,
            )
            _unlink_activation_journal(root_fd, journal.raw)
        return result


def _continue_routine_transaction(
    activation_root: _RootSnapshot,
    activation_lock_fd: int,
    precondition: ActiveDeploymentPrecondition,
    verified: VerifiedDeploymentStage,
    intent: Mapping[str, Any],
    journal: _ActivationJournal | None,
) -> TransactionResult:
    root_fd = activation_root.fd
    artifacts = _ordered_routine_activation_artifacts(verified)
    cleanups = _ordered_routine_cleanup_steps(precondition, verified)
    transaction_id = intent["transaction_id"]
    candidate_sha256 = intent["candidate_receipt_sha256"]
    prior_sha256 = intent["prior"]["deployment_receipt"]["sha256"]
    candidate_smoke = intent["candidate"]["smoke"]
    prior_smoke = intent["prior"]["smoke"]
    candidate_handoff = {
        "target_deployment_receipt_sha256": candidate_sha256,
        "smoke_bundle_sha256": candidate_smoke["bundle"]["sha256"],
        "smoke_trust_context_sha256": candidate_smoke["trust_context"]["sha256"],
    }
    rollback_handoff = {
        "target_deployment_receipt_sha256": prior_sha256,
        "smoke_bundle_sha256": prior_smoke["bundle"]["sha256"],
        "smoke_trust_context_sha256": prior_smoke["trust_context"]["sha256"],
    }
    candidate_selectors = (
        _staged_artifact_for_role(verified.artifacts, "active-record"),
        _staged_artifact_for_role(verified.artifacts, "deployment-alias"),
    )
    prior_selectors = (
        _staged_artifact_for_role(verified.artifacts, "prior-active-record"),
        _staged_artifact_for_role(verified.artifacts, "prior-deployment-alias"),
    )
    if journal is None:
        _revalidate_locked_active_precondition(
            activation_root,
            activation_lock_fd,
            precondition,
        )
        journal = _advance_activation_journal(
            root_fd,
            intent,
            None,
            "prepared",
        )
    while True:
        _validate_activation_journal_authority(
            journal,
            intent,
            artifacts,
            cleanups,
        )
        _audit_routine_live_state(
            activation_root,
            activation_lock_fd,
            precondition,
            verified,
            journal,
            artifacts,
        )
        phase = journal.value["phase"]
        if phase == "prepared":
            journal = _advance_activation_journal(root_fd, intent, journal, "frozen")
            continue
        if phase == "frozen":
            journal = _advance_activation_journal(root_fd, intent, journal, "drained")
            continue
        if phase == "drained":
            artifact = artifacts[0]
            journal = _advance_activation_journal(
                root_fd,
                intent,
                journal,
                "additive-installing",
                pending_step={
                    "operation": "install",
                    "index": 0,
                    "role": artifact.role,
                },
            )
            continue
        if phase == "additive-installing":
            index = journal.value["pending_step"]["index"]
            artifact = artifacts[index]
            _install_activation_artifact(
                root_fd,
                precondition.canonical_root,
                artifact,
                transaction_id=transaction_id,
                step_index=index,
                directory_repair_paths=frozenset(
                    _activation_pending_directory_suffix(artifacts, index)
                ),
            )
            if index + 1 < len(artifacts):
                following = artifacts[index + 1]
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "additive-installing",
                    pending_step={
                        "operation": "install",
                        "index": index + 1,
                        "role": following.role,
                    },
                )
            else:
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "selector-switching",
                    pending_step={
                        "operation": "replace-selector",
                        "index": 0,
                        "role": "active-record",
                    },
                )
            continue
        if phase == "selector-switching":
            index = journal.value["pending_step"]["index"]
            _replace_activation_selector(
                root_fd,
                transaction_id=transaction_id,
                direction="candidate",
                index=index,
                current=prior_selectors[index],
                target=candidate_selectors[index],
            )
            if index == 0:
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "selector-switching",
                    pending_step={
                        "operation": "replace-selector",
                        "index": 1,
                        "role": "deployment-alias",
                    },
                )
            else:
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "candidate-smoke",
                    smoke_handoff=candidate_handoff,
                )
            continue
        if phase == "candidate-smoke":
            acceptance = journal.value["candidate_smoke_acceptance"]
            if acceptance is not None:
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "candidate-accepted",
                    candidate_smoke_acceptance=acceptance,
                )
                continue
            child = _run_activation_smoke(
                precondition.canonical_root,
                activation_lock_fd,
            )
            accepted_sha256 = hashlib.sha256(child.stdout).hexdigest()
            if (
                child.returncode == 0
                and child.stderr == b""
                and accepted_sha256 == candidate_smoke["expected_envelope_sha256"]
            ):
                acceptance = _smoke_acceptance(
                    phase="candidate-smoke",
                    candidate_receipt_sha256=candidate_sha256,
                    expected_envelope_sha256=candidate_smoke[
                        "expected_envelope_sha256"
                    ],
                    accepted_envelope_sha256=accepted_sha256,
                )
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "candidate-smoke",
                    smoke_handoff=candidate_handoff,
                    candidate_smoke_acceptance=acceptance,
                )
            else:
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "prior-restoring",
                    pending_step={
                        "operation": "replace-selector",
                        "index": 0,
                        "role": "active-record",
                    },
                )
            continue
        if phase == "candidate-accepted":
            acceptance = journal.value["candidate_smoke_acceptance"]
            journal = _advance_activation_journal(
                root_fd,
                intent,
                journal,
                "terminal",
                candidate_smoke_acceptance=acceptance,
                terminal_result={
                    "outcome": "candidate-active",
                    "candidate_receipt_sha256": candidate_sha256,
                    "active_receipt_sha256": candidate_sha256,
                    "accepted_envelope_sha256": acceptance["accepted_envelope_sha256"],
                    "failure_class": None,
                },
            )
            continue
        if phase == "prior-restoring":
            index = journal.value["pending_step"]["index"]
            _replace_activation_selector(
                root_fd,
                transaction_id=transaction_id,
                direction="prior",
                index=index,
                current=candidate_selectors[index],
                target=prior_selectors[index],
            )
            if index == 0:
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "prior-restoring",
                    pending_step={
                        "operation": "replace-selector",
                        "index": 1,
                        "role": "deployment-alias",
                    },
                )
            else:
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "rollback-smoke",
                    smoke_handoff=rollback_handoff,
                )
            continue
        if phase == "rollback-smoke":
            acceptance = journal.value["rollback_smoke_acceptance"]
            if acceptance is not None:
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "prior-accepted",
                    rollback_smoke_acceptance=acceptance,
                )
                continue
            child = _run_activation_smoke(
                precondition.canonical_root,
                activation_lock_fd,
            )
            accepted_sha256 = hashlib.sha256(child.stdout).hexdigest()
            if (
                child.returncode == 0
                and child.stderr == b""
                and accepted_sha256 == prior_smoke["expected_envelope_sha256"]
            ):
                acceptance = _smoke_acceptance(
                    phase="rollback-smoke",
                    candidate_receipt_sha256=prior_sha256,
                    expected_envelope_sha256=prior_smoke["expected_envelope_sha256"],
                    accepted_envelope_sha256=accepted_sha256,
                )
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "rollback-smoke",
                    smoke_handoff=rollback_handoff,
                    rollback_smoke_acceptance=acceptance,
                )
            else:
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "terminal",
                    terminal_result={
                        "outcome": "recovery-required",
                        "candidate_receipt_sha256": candidate_sha256,
                        "active_receipt_sha256": None,
                        "accepted_envelope_sha256": None,
                        "failure_class": "rollback-smoke-rejected",
                    },
                )
            continue
        if phase == "prior-accepted":
            step = cleanups[0]
            journal = _advance_activation_journal(
                root_fd,
                intent,
                journal,
                "rollback-cleaning",
                pending_step={
                    "operation": step.operation,
                    "index": 0,
                    "role": step.role,
                },
                rollback_smoke_acceptance=journal.value["rollback_smoke_acceptance"],
            )
            continue
        if phase == "rollback-cleaning":
            index = journal.value["pending_step"]["index"]
            step = cleanups[index]
            if step.operation == "remove-artifact":
                if step.artifact is None or step.relative_path is not None:
                    raise DeploymentError(
                        "routine activation cleanup artifact binding disagrees"
                    )
                _remove_activation_artifact(root_fd, step.artifact)
            elif step.operation == "remove-directory":
                if step.artifact is not None or step.relative_path is None:
                    raise DeploymentError(
                        "routine activation cleanup directory binding disagrees"
                    )
                _remove_activation_directory(root_fd, step.relative_path)
            else:
                raise DeploymentError("routine activation cleanup operation disagrees")
            acceptance = journal.value["rollback_smoke_acceptance"]
            if index + 1 < len(cleanups):
                following = cleanups[index + 1]
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "rollback-cleaning",
                    pending_step={
                        "operation": following.operation,
                        "index": index + 1,
                        "role": following.role,
                    },
                    rollback_smoke_acceptance=acceptance,
                )
            else:
                journal = _advance_activation_journal(
                    root_fd,
                    intent,
                    journal,
                    "terminal",
                    rollback_smoke_acceptance=acceptance,
                    terminal_result={
                        "outcome": "restored-prior",
                        "candidate_receipt_sha256": candidate_sha256,
                        "active_receipt_sha256": prior_sha256,
                        "accepted_envelope_sha256": acceptance[
                            "accepted_envelope_sha256"
                        ],
                        "failure_class": "candidate-smoke-rejected",
                    },
                )
            continue
        if phase != "terminal":
            raise DeploymentError("routine activation phase is unsupported")
        result = _transaction_result(journal)
        if result.outcome != "recovery-required":
            _retain_terminal_transaction_result(
                root_fd,
                precondition.canonical_root,
                journal,
            )
            _unlink_activation_journal(root_fd, journal.raw)
        return result


def activate_staged(request: ActivationRequest) -> TransactionResult:
    """Activate one exact staged deployment under the exclusive lock."""

    _validate_activation_request(request)
    if type(request.deployment) is BridgeTransitionRequest:
        bridge = request.deployment
        initial_precondition = _capture_active_deployment_precondition(
            bridge.deployment.canonical_root,
            bridge.deployment.expected_active_receipt_sha256,
            receipt_profile=BRIDGE_LEGACY_RECEIPT_PROFILE,
        )
        _validate_exact_bridge_predecessor(initial_precondition)
        root, lock_fd = _open_locked_activation_root(initial_precondition)
        try:
            _revalidate_locked_active_precondition(
                root,
                lock_fd,
                initial_precondition,
                source_parser=_bridge_legacy_active_receipt_source,
                policy_parser=_parse_bridge_legacy_compatibility_policy,
            )
            verified, bridge_transition = _rebind_bridge_activation(
                request,
                initial_precondition,
            )
            _revalidate_locked_active_precondition(
                root,
                lock_fd,
                initial_precondition,
                source_parser=_bridge_legacy_active_receipt_source,
                policy_parser=_parse_bridge_legacy_compatibility_policy,
            )
            intent, _ = _control_maintenance_activation_intent_from_stage(
                initial_precondition,
                request,
                verified,
                bridge_transition=bridge_transition,
            )
            return _continue_control_maintenance_transaction(
                root,
                lock_fd,
                initial_precondition,
                verified,
                intent,
                None,
                source_parser=_bridge_legacy_active_receipt_source,
                policy_parser=_parse_bridge_legacy_compatibility_policy,
            )
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
                os.close(root.fd)
    if type(request.deployment) is DeploymentRequest:
        initial_precondition = _capture_active_deployment_precondition(
            request.deployment.canonical_root,
            request.deployment.expected_active_receipt_sha256,
        )
        root, lock_fd = _open_locked_activation_root(initial_precondition)
        try:
            _revalidate_locked_active_precondition(
                root,
                lock_fd,
                initial_precondition,
            )
            prepared, _, verified = _rebind_routine_activation(
                request,
                initial_precondition,
            )
            _revalidate_locked_active_precondition(
                root,
                lock_fd,
                initial_precondition,
            )
            if type(prepared.plan) is ControlSetDeploymentPlan:
                intent, _ = _control_maintenance_activation_intent(
                    prepared,
                    request,
                    verified,
                )
                return _continue_control_maintenance_transaction(
                    root,
                    lock_fd,
                    prepared.plan.precondition,
                    verified,
                    intent,
                    None,
                )
            intent, _ = _routine_activation_intent(
                prepared,
                request,
                verified,
            )
            return _continue_routine_transaction(
                root,
                lock_fd,
                prepared.plan.precondition,
                verified,
                intent,
                None,
            )
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
                os.close(root.fd)
    initial_precondition = _capture_first_install_precondition(
        request.deployment.canonical_root
    )
    root, lock_fd = _open_locked_activation_root(initial_precondition)
    try:
        prepared, _, verified = _rebind_first_install_activation(request)
        if prepared.plan.precondition != initial_precondition:
            raise DeploymentError(
                "activation precondition changed before locked rebind"
            )
        intent, _ = _activation_intent(prepared, request, verified)
        return _continue_first_install_transaction(
            root,
            lock_fd,
            prepared,
            verified,
            intent,
            None,
        )
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)
            os.close(root.fd)


def _validate_recovery_request(request: RecoveryRequest) -> None:
    if type(request) is not RecoveryRequest:
        raise DeploymentError("activation recovery request type mismatch")
    _validate_activation_request(request.activation)
    if type(request.expected_journal_raw) is not bytes:
        raise DeploymentError("activation recovery journal must be exact bytes")


def _active_prior_control_recovery(
    request: RecoveryRequest,
    journal: _ActivationJournal,
) -> bool:
    return (
        (
            type(request.activation.deployment)
            in {DeploymentRequest, BridgeTransitionRequest}
            and journal.value["transaction_class"] == "control-set-maintenance"
        )
        or (
            type(request.activation.deployment) is RollbackToRequest
            and journal.value["transaction_class"] == "manual-exact-target-rollback"
        )
    ) and journal.value["prior"]["state"] == "active"


def _capture_control_recovery_controller(
    verified: VerifiedDeploymentStage,
) -> tuple[StagedArtifact, bytes]:
    artifact = _staged_artifact_for_role(
        verified.artifacts,
        "prior-controller",
    )
    expected_relative = "preimage/controller/task_witness_deploy.py"
    expected_path = verified.path.parent / expected_relative
    path = _normalized_absolute_path(
        artifact.staged_path,
        "control recovery prior controller path",
    )
    try:
        before = path.lstat()
    except OSError as error:
        raise DeploymentError(
            "control recovery prior controller is unavailable"
        ) from error
    raw = _capture_absolute_regular(
        path,
        MAX_CANDIDATE_TREE_FILE_BYTES,
        "control recovery prior controller",
    )
    try:
        after = path.lstat()
    except OSError as error:
        raise DeploymentError(
            "control recovery prior controller became unavailable"
        ) from error
    if (
        artifact.relative_path != expected_relative
        or path != expected_path
        or artifact.staged["path"] != str(path)
        or _identity(before) != _identity(after)
        or not stat.S_ISREG(after.st_mode)
        or after.st_uid != os.geteuid()
        or after.st_nlink != 1
        or stat.S_IMODE(after.st_mode) != 0o600
        or len(raw) != artifact.staged["length"]
        or hashlib.sha256(raw).hexdigest() != artifact.staged["sha256"]
        or raw != artifact.raw
    ):
        raise DeploymentError("control recovery prior controller binding disagrees")
    return artifact, raw


def _freeze5_recovery_authority(
    request: RecoveryRequest | ResultReconciliationRequest,
    expected: _ActivationJournal,
    *,
    require_live_journal: bool = True,
) -> _Freeze5RecoveryDispatch | None:
    deployment = request.activation.deployment
    if (
        type(deployment) is not DeploymentRequest
        or type(deployment.source_evidence) is not HarnessSnapshotEvidence
        or expected.value["transaction_class"] != "control-set-maintenance"
        or expected.value["prior"]["state"] != "active"
    ):
        return None
    adapter_transaction = deployment.maintenance_transaction_sha256
    stage_receipt = _normalized_absolute_path(
        request.activation.stage_receipt,
        "Freeze 5 recovery stage receipt",
    )
    try:
        stage_before = stage_receipt.lstat()
    except OSError as error:
        raise DeploymentError(
            "Freeze 5 recovery stage receipt is unavailable"
        ) from error
    stage_raw = _capture_absolute_regular(
        stage_receipt,
        MAX_JSON_BYTES,
        "Freeze 5 recovery stage receipt",
    )
    try:
        stage_after = stage_receipt.lstat()
    except OSError as error:
        raise DeploymentError(
            "Freeze 5 recovery stage receipt became unavailable"
        ) from error
    if (
        _identity(stage_before) != _identity(stage_after)
        or not stat.S_ISREG(stage_after.st_mode)
        or stage_after.st_uid != os.geteuid()
        or stage_after.st_nlink != 1
        or stat.S_IMODE(stage_after.st_mode) != 0o600
    ):
        raise DeploymentError("Freeze 5 recovery stage receipt binding disagrees")
    stage = _exact(
        _parse_canonical_json(stage_raw, "Freeze 5 recovery stage receipt"),
        {
            "schema_version",
            "contract",
            "canonical_root",
            "staging_root",
            "plan_sha256",
            "maintenance_transaction_sha256",
            "authorization",
            "classification",
            "rollback_receipt",
            "deployment_receipt",
            "artifacts",
            "content_sha256",
        },
        "Freeze 5 recovery stage receipt",
    )
    _content_sha256(stage, "Freeze 5 recovery stage receipt")
    recorded_stage = expected.value["stage"]
    authorization = _exact(
        stage["authorization"],
        {"sha256", "content_sha256"},
        "Freeze 5 recovery stage authorization",
    )
    authorization_value = _parse_canonical_json(
        request.activation.authorization_raw,
        "Freeze 5 recovery authorization",
    )
    authorization_content_sha256 = _content_sha256(
        authorization_value,
        "Freeze 5 recovery authorization",
    )
    staging_root = _normalized_absolute_path(
        Path(_text(stage["staging_root"], "Freeze 5 recovery staging root")),
        "Freeze 5 recovery staging root",
    )
    if (
        type(stage["schema_version"]) is not int
        or stage["schema_version"] != 1
        or stage["contract"] != STAGED_DEPLOYMENT_CONTRACT
        or stage["classification"]
        != {
            "outcome": "authorized-control-set-maintenance",
            "reason": "exact-deployer-authorization",
        }
        or stage_receipt != staging_root / "stage.json"
        or stage_receipt != Path(recorded_stage["receipt_path"])
        or hashlib.sha256(stage_raw).hexdigest() != recorded_stage["receipt_sha256"]
        or stage["canonical_root"] != str(deployment.canonical_root)
        or stage["canonical_root"] != expected.value["canonical_root"]
        or stage["plan_sha256"] != recorded_stage["plan_sha256"]
        or stage["maintenance_transaction_sha256"] != adapter_transaction
        or stage["maintenance_transaction_sha256"]
        != recorded_stage["maintenance_transaction_sha256"]
        or stage["maintenance_transaction_sha256"]
        != expected.value["outer_maintenance_transaction_sha256"]
        or authorization["sha256"] != recorded_stage["authorization_sha256"]
        or authorization["sha256"]
        != hashlib.sha256(request.activation.authorization_raw).hexdigest()
        or authorization["content_sha256"] != authorization_content_sha256
    ):
        raise DeploymentError("Freeze 5 recovery stage authority disagrees")
    journal_path = _normalized_absolute_path(
        deployment.canonical_root / "transaction.json",
        "Freeze 5 recovery live journal",
    )
    journal_raw: bytes | None = None
    journal_identity: tuple[int, ...] | None = None
    if require_live_journal:
        if type(request) is not RecoveryRequest:
            raise DeploymentError("Freeze 5 recovery request type mismatch")
        try:
            journal_before = journal_path.lstat()
        except OSError as error:
            raise DeploymentError(
                "Freeze 5 recovery live journal is unavailable"
            ) from error
        journal_raw = _capture_absolute_regular(
            journal_path,
            MAX_JSON_BYTES,
            "Freeze 5 recovery live journal",
        )
        try:
            journal_after = journal_path.lstat()
        except OSError as error:
            raise DeploymentError(
                "Freeze 5 recovery live journal became unavailable"
            ) from error
        if (
            _identity(journal_before) != _identity(journal_after)
            or not stat.S_ISREG(journal_after.st_mode)
            or journal_after.st_uid != os.geteuid()
            or journal_after.st_nlink != 1
            or stat.S_IMODE(journal_after.st_mode) != 0o600
            or journal_raw != request.expected_journal_raw
            or journal_raw != expected.raw
        ):
            raise DeploymentError("Freeze 5 recovery live journal freshness disagrees")
        journal_identity = _identity(journal_after)
    else:
        if type(request) is not ResultReconciliationRequest:
            raise DeploymentError(
                "Freeze 5 result reconciliation request type mismatch"
            )
        try:
            journal_path.lstat()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise DeploymentError(
                "Freeze 5 result reconciliation journal state is unavailable"
            ) from error
        else:
            raise DeploymentError(
                "Freeze 5 result reconciliation requires no live journal"
            )
    artifacts = stage["artifacts"]
    if not isinstance(artifacts, list):
        raise DeploymentError("Freeze 5 recovery artifact inventory schema drift")
    by_role: dict[
        str,
        tuple[str, tuple[str, ...], dict[str, Any], dict[str, Any]],
    ] = {}
    relative_paths: list[str] = []
    staged_paths: set[Path] = set()
    for index, item in enumerate(artifacts):
        artifact = _exact(
            item,
            {"role", "relative_path", "staged", "installed"},
            f"Freeze 5 recovery artifact[{index}]",
        )
        role = _token(artifact["role"], f"Freeze 5 recovery artifact[{index}].role")
        if role in by_role:
            raise DeploymentError("Freeze 5 recovery artifact role is duplicated")
        relative, components = _relative_path(
            artifact["relative_path"],
            f"Freeze 5 recovery artifact[{index}].relative_path",
        )
        staged = _receipt_file_binding(
            artifact["staged"],
            f"Freeze 5 recovery artifact[{index}].staged",
        )
        installed = _receipt_file_binding(
            artifact["installed"],
            f"Freeze 5 recovery artifact[{index}].installed",
        )
        staged_path = Path(staged["path"])
        if (
            staged_path != staging_root.joinpath(*components)
            or staged_path in staged_paths
            or staged["owner"] != os.geteuid()
            or staged["mode"] != 0o600
            or staged["length"] != installed["length"]
            or staged["sha256"] != installed["sha256"]
            or staged["owner"] != installed["owner"]
        ):
            raise DeploymentError("Freeze 5 recovery artifact binding disagrees")
        relative_paths.append(relative)
        staged_paths.add(staged_path)
        by_role[role] = (relative, components, staged, installed)
    if relative_paths != sorted(relative_paths) or len(relative_paths) != len(
        set(relative_paths)
    ):
        raise DeploymentError(
            "Freeze 5 recovery artifact paths are not sorted and unique"
        )
    expected_preimage_roles = {
        "prior-active-record",
        "prior-deployment-alias",
        *(f"prior-{role}" for role in CONTROL_PREIMAGE_ROLES),
    }
    if not expected_preimage_roles.issubset(by_role):
        raise DeploymentError("Freeze 5 recovery control preimage is incomplete")
    prior = expected.value["prior"]
    journal_bindings = {
        "prior-active-record": prior["active_record"],
        "prior-deployment-alias": prior["deployment_receipt"],
        "prior-controller": prior["control_set"]["controller"],
        "prior-policy": prior["control_set"]["policy"],
        "prior-launcher": prior["control_set"]["launcher"],
        "prior-client": prior["control_set"]["client"],
        "prior-smoke-bundle-manifest": prior["smoke"]["bundle"]["manifest"],
        "prior-shim": prior["control_set"]["shim"],
    }
    for role, journal_binding in journal_bindings.items():
        _, _, _, installed = by_role[role]
        if installed != journal_binding:
            raise DeploymentError("Freeze 5 recovery control preimage disagrees")
    exact_roles = {
        "prior-controller": FREEZE5_CONTROLLER_SHA256,
        "prior-policy": FREEZE5_POLICY_SHA256,
        "prior-client": FREEZE5_CLIENT_SHA256,
        "policy": FREEZE5_POLICY_SHA256,
    }
    capture_roles = expected_preimage_roles | set(exact_roles) | {"client"}
    captured: dict[str, tuple[Path, bytes, tuple[int, ...]]] = {}
    for role in capture_roles:
        binding = by_role.get(role)
        if binding is None:
            raise DeploymentError("Freeze 5 recovery identity artifact is missing")
        _, _, staged, _ = binding
        path = Path(staged["path"])
        byte_limit = (
            MAX_JSON_BYTES
            if role
            in {
                "prior-active-record",
                "prior-deployment-alias",
                "prior-policy",
                "prior-smoke-bundle-manifest",
                "policy",
            }
            else MAX_CANDIDATE_TREE_FILE_BYTES
        )
        try:
            before = path.lstat()
        except OSError as error:
            raise DeploymentError(
                "Freeze 5 recovery control preimage is unavailable"
            ) from error
        raw = _capture_absolute_regular(
            path,
            byte_limit,
            f"Freeze 5 recovery {role}",
        )
        try:
            after = path.lstat()
        except OSError as error:
            raise DeploymentError(
                "Freeze 5 recovery control preimage became unavailable"
            ) from error
        if (
            _identity(before) != _identity(after)
            or not stat.S_ISREG(after.st_mode)
            or after.st_uid != staged["owner"]
            or after.st_nlink != 1
            or stat.S_IMODE(after.st_mode) != staged["mode"]
            or len(raw) != staged["length"]
            or hashlib.sha256(raw).hexdigest() != staged["sha256"]
        ):
            raise DeploymentError("Freeze 5 recovery control preimage bytes disagree")
        captured[role] = (path, raw, _identity(after))
    prior_receipt_raw = captured["prior-deployment-alias"][1]
    prior_receipt_sha256 = hashlib.sha256(prior_receipt_raw).hexdigest()
    prior_receipt = _parse_canonical_json(
        prior_receipt_raw,
        "Freeze 5 recovery prior deployment receipt",
    )
    _content_sha256(prior_receipt, "Freeze 5 recovery prior deployment receipt")
    prior_source = prior_receipt.get("source")
    if not isinstance(prior_source, Mapping):
        raise DeploymentError("Freeze 5 recovery prior source schema drift")
    if "details" in prior_source or "source_evidence" in prior_source:
        return None
    prior_controls = _exact(
        prior_receipt.get("control_set"),
        set(CONTROL_SET_ROLES),
        "Freeze 5 recovery prior control set",
    )
    prior_control_bindings = {
        role: _receipt_file_binding(
            prior_controls[role],
            f"Freeze 5 recovery prior {role}",
        )
        for role in ("controller", "policy", "client")
    }
    for role, exact_sha256 in exact_roles.items():
        if hashlib.sha256(captured[role][1]).hexdigest() != exact_sha256:
            raise DeploymentError("Freeze 5 recovery identity artifact disagrees")
    candidate_client = by_role.get("client")
    if candidate_client is None:
        raise DeploymentError("Freeze 5 recovery candidate client is missing")
    _, _, candidate_client_staged, candidate_client_installed = candidate_client
    if (
        candidate_client_staged["sha256"] == FREEZE5_CLIENT_SHA256
        or candidate_client_staged["sha256"] != candidate_client_installed["sha256"]
        or candidate_client_staged["length"] != candidate_client_installed["length"]
    ):
        raise DeploymentError("Freeze 5 recovery candidate client identity disagrees")
    _require_b1_client_source(
        captured["client"][1],
        "Freeze 5 recovery candidate client",
    )
    if (
        prior_receipt.get("schema_version") != 1
        or prior_receipt.get("contract") != LEGACY_DEPLOYMENT_RECEIPT_CONTRACT
        or prior_receipt.get("canonical_root") != str(deployment.canonical_root)
        or prior_receipt.get("effective_uid") != os.geteuid()
        or prior_source.get("mode") != "harness_snapshot"
        or prior_receipt_sha256 != deployment.expected_active_receipt_sha256
        or prior_receipt_sha256 != prior["deployment_receipt"]["sha256"]
        or prior_control_bindings["controller"]
        != _thaw(prior["control_set"]["controller"])
        or prior_control_bindings["policy"] != _thaw(prior["control_set"]["policy"])
        or prior_control_bindings["client"] != _thaw(prior["control_set"]["client"])
        or prior_control_bindings["controller"]["sha256"] != FREEZE5_CONTROLLER_SHA256
        or prior_control_bindings["policy"]["sha256"] != FREEZE5_POLICY_SHA256
        or prior_control_bindings["client"]["sha256"] != FREEZE5_CLIENT_SHA256
    ):
        raise DeploymentError("Freeze 5 recovery prior receipt authority disagrees")
    prior_controller, controller_raw, controller_identity = captured["prior-controller"]
    evidence = deployment.source_evidence
    return _Freeze5RecoveryDispatch(
        adapter=Freeze5RecoveryRequest(
            source_selection_raw=bytes(deployment.source_selection_raw),
            manager_binding_raw=bytes(evidence.binding_raw),
            manager_receipt_raw=bytes(evidence.receipt_raw),
            runtime_qualification_raw=bytes(deployment.runtime_qualification_raw),
            maintenance_transaction_sha256=str(
                deployment.maintenance_transaction_sha256
            ),
            expected_active_receipt_sha256=str(
                deployment.expected_active_receipt_sha256
            ),
        ),
        stage_path=stage_receipt,
        stage_raw=stage_raw,
        stage_identity=_identity(stage_after),
        journal_path=journal_path,
        journal_raw=journal_raw,
        journal_identity=journal_identity,
        controller_path=prior_controller,
        controller_raw=controller_raw,
        controller_identity=controller_identity,
    )


def _recover_control_maintenance_through_freeze5(
    request: RecoveryRequest,
    authority: _Freeze5RecoveryDispatch,
) -> TransactionResult:
    adapter = authority.adapter
    controller_path = authority.controller_path
    controller_raw = authority.controller_raw
    deployment = request.activation.deployment
    if type(deployment) is not DeploymentRequest:
        raise DeploymentError("Freeze 5 recovery deployment request type mismatch")

    def recheck_authority(*, include_journal: bool) -> None:
        bindings = [
            (
                authority.stage_path,
                authority.stage_raw,
                authority.stage_identity,
                MAX_JSON_BYTES,
                "Freeze 5 recovery stage receipt",
            ),
            (
                controller_path,
                controller_raw,
                authority.controller_identity,
                MAX_CANDIDATE_TREE_FILE_BYTES,
                "Freeze 5 recovery prior controller",
            ),
        ]
        if include_journal:
            if (
                authority.journal_path is None
                or authority.journal_raw is None
                or authority.journal_identity is None
            ):
                raise DeploymentError("Freeze 5 recovery journal authority is missing")
            bindings.append(
                (
                    authority.journal_path,
                    authority.journal_raw,
                    authority.journal_identity,
                    MAX_JSON_BYTES,
                    "Freeze 5 recovery live journal",
                )
            )
        for path, expected_raw, expected_identity, limit, label in bindings:
            try:
                before = path.lstat()
            except OSError as error:
                raise DeploymentError(f"{label} became unavailable") from error
            raw = _capture_absolute_regular(path, limit, label)
            try:
                after = path.lstat()
            except OSError as error:
                raise DeploymentError(f"{label} became unavailable") from error
            if (
                _identity(before) != expected_identity
                or _identity(after) != expected_identity
                or raw != expected_raw
            ):
                raise DeploymentError(f"{label} changed before legacy dispatch")

    recheck_authority(include_journal=True)
    module_seed = b"\0".join(
        (
            controller_raw,
            request.expected_journal_raw,
            str(request.activation.stage_receipt).encode(),
            os.urandom(32),
        )
    )
    module_name = (
        "_task_witness_control_recovery_" + hashlib.sha256(module_seed).hexdigest()
    )
    if module_name in sys.modules:
        raise DeploymentError("Freeze 5 recovery module name collision")
    module = ModuleType(module_name)
    module.__file__ = str(controller_path)
    module.__package__ = None
    module.__loader__ = None
    module.__spec__ = None
    sys.modules[module_name] = module
    try:
        code = compile(
            controller_raw,
            str(controller_path),
            "exec",
            dont_inherit=True,
            optimize=0,
        )
        exec(code, module.__dict__)  # noqa: S102 - exact immutable F5 source
        if (
            sys.modules.get(module_name) is not module
            or module.__name__ != module_name
            or module.__file__ != str(controller_path)
        ):
            raise DeploymentError("Freeze 5 recovery module identity changed")
        if tuple(module.DeploymentRequest.__dataclass_fields__) != (
            "candidate_root",
            "canonical_root",
            "source_selection_raw",
            "manager_binding_raw",
            "manager_receipt_raw",
            "runtime_qualification_raw",
            "maintenance_transaction_sha256",
            "expected_active_receipt_sha256",
        ):
            raise DeploymentError("Freeze 5 recovery request surface disagrees")
        recheck_authority(include_journal=True)
        rebound_deployment = module.DeploymentRequest(
            candidate_root=module.Path(str(deployment.candidate_root)),
            canonical_root=module.Path(str(deployment.canonical_root)),
            source_selection_raw=adapter.source_selection_raw,
            manager_binding_raw=adapter.manager_binding_raw,
            manager_receipt_raw=adapter.manager_receipt_raw,
            runtime_qualification_raw=adapter.runtime_qualification_raw,
            maintenance_transaction_sha256=(adapter.maintenance_transaction_sha256),
            expected_active_receipt_sha256=(adapter.expected_active_receipt_sha256),
        )
        rebound_activation = module.ActivationRequest(
            deployment=rebound_deployment,
            authorization_raw=bytes(request.activation.authorization_raw),
            stage_receipt=module.Path(str(request.activation.stage_receipt)),
        )
        rebound_request = module.RecoveryRequest(
            activation=rebound_activation,
            expected_journal_raw=bytes(request.expected_journal_raw),
        )
        try:
            rebound_result = module._recover_control_maintenance_transaction(
                rebound_request
            )
        except module.DeploymentError as error:
            raise DeploymentError(str(error)) from error
        recheck_authority(include_journal=False)
        if type(rebound_result) is not module.TransactionResult:
            raise DeploymentError("Freeze 5 recovery result type disagrees")
        return TransactionResult(
            transaction_id=rebound_result.transaction_id,
            outcome=rebound_result.outcome,
            candidate_receipt_sha256=rebound_result.candidate_receipt_sha256,
            active_receipt_sha256=rebound_result.active_receipt_sha256,
            accepted_envelope_sha256=rebound_result.accepted_envelope_sha256,
            journal_sha256=rebound_result.journal_sha256,
            journal_value=_freeze_json(_thaw(rebound_result.journal_value)),
            journal_raw=bytes(rebound_result.journal_raw),
        )
    finally:
        sys.modules.pop(module_name, None)


def _reconcile_transaction_result_through_freeze5(
    request: ResultReconciliationRequest,
    authority: _Freeze5RecoveryDispatch,
) -> TransactionResult:
    adapter = authority.adapter
    controller_path = authority.controller_path
    controller_raw = authority.controller_raw
    deployment = request.activation.deployment
    if type(deployment) is not DeploymentRequest:
        raise DeploymentError(
            "Freeze 5 result reconciliation deployment request type mismatch"
        )

    def recheck_authority() -> None:
        for path, expected_raw, expected_identity, limit, label in (
            (
                authority.stage_path,
                authority.stage_raw,
                authority.stage_identity,
                MAX_JSON_BYTES,
                "Freeze 5 result reconciliation stage receipt",
            ),
            (
                controller_path,
                controller_raw,
                authority.controller_identity,
                MAX_CANDIDATE_TREE_FILE_BYTES,
                "Freeze 5 result reconciliation prior controller",
            ),
        ):
            try:
                before = path.lstat()
            except OSError as error:
                raise DeploymentError(f"{label} became unavailable") from error
            raw = _capture_absolute_regular(path, limit, label)
            try:
                after = path.lstat()
            except OSError as error:
                raise DeploymentError(f"{label} became unavailable") from error
            if (
                _identity(before) != expected_identity
                or _identity(after) != expected_identity
                or raw != expected_raw
            ):
                raise DeploymentError(f"{label} changed before legacy dispatch")
        if authority.journal_path is None:
            raise DeploymentError(
                "Freeze 5 result reconciliation journal authority is missing"
            )
        try:
            authority.journal_path.lstat()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise DeploymentError(
                "Freeze 5 result reconciliation journal state is unavailable"
            ) from error
        else:
            raise DeploymentError(
                "Freeze 5 result reconciliation requires no live journal"
            )

    recheck_authority()
    module_seed = b"\0".join(
        (
            controller_raw,
            request.expected_terminal_journal_raw,
            str(request.activation.stage_receipt).encode(),
            os.urandom(32),
        )
    )
    module_name = (
        "_task_witness_result_reconciliation_" + hashlib.sha256(module_seed).hexdigest()
    )
    if module_name in sys.modules:
        raise DeploymentError("Freeze 5 result reconciliation module name collision")
    module = ModuleType(module_name)
    module.__file__ = str(controller_path)
    module.__package__ = None
    module.__loader__ = None
    module.__spec__ = None
    sys.modules[module_name] = module
    try:
        code = compile(
            controller_raw,
            str(controller_path),
            "exec",
            dont_inherit=True,
            optimize=0,
        )
        exec(code, module.__dict__)  # noqa: S102 - exact immutable F5 source
        if (
            sys.modules.get(module_name) is not module
            or module.__name__ != module_name
            or module.__file__ != str(controller_path)
        ):
            raise DeploymentError(
                "Freeze 5 result reconciliation module identity changed"
            )
        if tuple(module.DeploymentRequest.__dataclass_fields__) != (
            "candidate_root",
            "canonical_root",
            "source_selection_raw",
            "manager_binding_raw",
            "manager_receipt_raw",
            "runtime_qualification_raw",
            "maintenance_transaction_sha256",
            "expected_active_receipt_sha256",
        ):
            raise DeploymentError(
                "Freeze 5 result reconciliation request surface disagrees"
            )
        if tuple(module.ResultReconciliationRequest.__dataclass_fields__) != (
            "activation",
            "expected_terminal_journal_raw",
        ) or not callable(module.reconcile_transaction_result):
            raise DeploymentError(
                "Freeze 5 result reconciliation public surface disagrees"
            )
        recheck_authority()
        rebound_deployment = module.DeploymentRequest(
            candidate_root=module.Path(str(deployment.candidate_root)),
            canonical_root=module.Path(str(deployment.canonical_root)),
            source_selection_raw=adapter.source_selection_raw,
            manager_binding_raw=adapter.manager_binding_raw,
            manager_receipt_raw=adapter.manager_receipt_raw,
            runtime_qualification_raw=adapter.runtime_qualification_raw,
            maintenance_transaction_sha256=adapter.maintenance_transaction_sha256,
            expected_active_receipt_sha256=adapter.expected_active_receipt_sha256,
        )
        rebound_activation = module.ActivationRequest(
            deployment=rebound_deployment,
            authorization_raw=bytes(request.activation.authorization_raw),
            stage_receipt=module.Path(str(request.activation.stage_receipt)),
        )
        rebound_request = module.ResultReconciliationRequest(
            activation=rebound_activation,
            expected_terminal_journal_raw=bytes(request.expected_terminal_journal_raw),
        )
        try:
            rebound_result = module.reconcile_transaction_result(rebound_request)
        except module.DeploymentError as error:
            raise DeploymentError(str(error)) from error
        recheck_authority()
        if type(rebound_result) is not module.TransactionResult:
            raise DeploymentError(
                "Freeze 5 result reconciliation result type disagrees"
            )
        return TransactionResult(
            transaction_id=rebound_result.transaction_id,
            outcome=rebound_result.outcome,
            candidate_receipt_sha256=rebound_result.candidate_receipt_sha256,
            active_receipt_sha256=rebound_result.active_receipt_sha256,
            accepted_envelope_sha256=rebound_result.accepted_envelope_sha256,
            journal_sha256=rebound_result.journal_sha256,
            journal_value=_freeze_json(_thaw(rebound_result.journal_value)),
            journal_raw=bytes(rebound_result.journal_raw),
        )
    finally:
        sys.modules.pop(module_name, None)


def _active_prior_recovery_intent_from_stage(
    request: ActivationRequest,
    precondition: ActiveDeploymentPrecondition,
    verified: VerifiedDeploymentStage,
) -> Mapping[str, Any]:
    if type(request.deployment) is RollbackToRequest:
        prepared, _, locked_verified = _rebind_manual_rollback_activation(
            request,
            precondition,
        )
        if locked_verified.raw != verified.raw:
            raise DeploymentError("manual rollback recovery stage changed")
        intent, _ = _manual_rollback_activation_intent(
            prepared,
            request,
            locked_verified,
        )
        return intent
    if type(request.deployment) is DeploymentRequest:
        if not _rebind_routine_activation_from_stage(
            request,
            precondition,
            verified,
        ):
            raise DeploymentError("control maintenance recovery stage rebind disagrees")
        intent, _ = _control_maintenance_activation_intent_from_stage(
            precondition,
            request,
            verified,
        )
        return intent
    if type(request.deployment) is BridgeTransitionRequest:
        locked_verified, bridge_transition = _rebind_bridge_activation(
            request,
            precondition,
            staged_predecessor=True,
        )
        if locked_verified.raw != verified.raw:
            raise DeploymentError("bridge recovery stage changed")
        intent, _ = _control_maintenance_activation_intent_from_stage(
            precondition,
            request,
            locked_verified,
            bridge_transition=bridge_transition,
        )
        return intent
    raise DeploymentError("active-prior recovery request type disagrees")


def _recover_control_maintenance_transaction(
    request: RecoveryRequest,
) -> TransactionResult:
    _validate_recovery_request(request)
    expected = _parse_activation_journal(request.expected_journal_raw)
    if not _active_prior_control_recovery(request, expected):
        raise DeploymentError("control recovery transaction class disagrees")
    manual_rollback = type(request.activation.deployment) is RollbackToRequest
    bridge_transition = type(request.activation.deployment) is BridgeTransitionRequest
    if manual_rollback and expected.value["phase"] not in {
        "prepared",
        "frozen",
        "drained",
        "additive-installing",
        "control-switching",
        "candidate-smoke",
        "candidate-accepted",
        "prior-restoring",
        "rollback-smoke",
        "prior-accepted",
        "rollback-cleaning",
        "terminal",
    }:
        raise DeploymentError("manual rollback recovery phase is not implemented")
    precondition, verified = _recovery_precondition_from_stage(
        request.activation,
        expected,
    )
    recovery_controller, recovery_raw = _capture_control_recovery_controller(verified)
    module_path = _normalized_absolute_path(
        Path(os.path.abspath(__file__)),
        "control recovery module source",
    )
    if (
        module_path != recovery_controller.staged_path
        or hashlib.sha256(recovery_raw).hexdigest()
        != recovery_controller.staged["sha256"]
    ):
        raise DeploymentError(
            "control recovery module is not the staged prior controller"
        )
    root, lock_fd = _open_locked_activation_root(
        precondition,
        root_mapping_only=True,
    )
    try:
        live_raw = _read_activation_file(
            root.fd,
            "transaction.json",
            "control maintenance recovery journal",
        )
        if live_raw != request.expected_journal_raw:
            raise DeploymentError(
                "control maintenance recovery journal freshness disagrees"
            )
        live = _parse_activation_journal(live_raw)
        if not _active_prior_control_recovery(request, live):
            raise DeploymentError("control maintenance recovery live class disagrees")
        if manual_rollback and live.value["phase"] not in {
            "prepared",
            "frozen",
            "drained",
            "additive-installing",
            "control-switching",
            "candidate-smoke",
            "candidate-accepted",
            "prior-restoring",
            "rollback-smoke",
            "prior-accepted",
            "rollback-cleaning",
            "terminal",
        }:
            raise DeploymentError("manual rollback recovery phase is not implemented")
        locked_precondition, locked_verified = _recovery_precondition_from_stage(
            request.activation,
            live,
        )
        if locked_precondition != precondition or locked_verified.raw != verified.raw:
            raise DeploymentError("control maintenance recovery stage changed")
        recovery_controller, locked_recovery_raw = _capture_control_recovery_controller(
            locked_verified
        )
        if (
            module_path != recovery_controller.staged_path
            or locked_recovery_raw != recovery_raw
        ):
            raise DeploymentError(
                "control recovery module source changed before reconstruction"
            )
        if (
            live.value["phase"] == "terminal"
            and live.value["terminal_result"]["outcome"] != "recovery-required"
        ):
            provisional_precondition = (
                _reconstruct_control_maintenance_recovery_precondition(
                    root,
                    lock_fd,
                    request.activation,
                    locked_verified,
                    live,
                    opaque_pending_temporary=True,
                )
            )
            provisional_intent = _active_prior_recovery_intent_from_stage(
                request.activation,
                provisional_precondition,
                locked_verified,
            )
            provisional_additive = _ordered_control_maintenance_additive_artifacts(
                locked_verified
            )
            provisional_replacements = _ordered_control_maintenance_replacements(
                locked_verified
            )
            provisional_cleanups = _ordered_control_maintenance_cleanup_steps(
                provisional_precondition,
                provisional_additive,
            )
            _validate_control_maintenance_journal_authority(
                live,
                provisional_intent,
                provisional_additive,
                provisional_replacements,
                provisional_cleanups,
            )
            provisional_journal_temporary = (
                _reconcile_control_maintenance_journal_temporary(
                    root.fd,
                    provisional_intent,
                    live,
                    provisional_additive,
                    provisional_replacements,
                    provisional_cleanups,
                    remove=False,
                )
            )
            _audit_control_maintenance_live_state(
                root,
                lock_fd,
                provisional_precondition,
                live,
                provisional_additive,
                provisional_replacements,
                provisional_cleanups,
                journal_temporary=provisional_journal_temporary,
                opaque_pending_result_temporary=True,
            )
        _reconcile_pending_transaction_result_state(root.fd, live)
        active_precondition = _reconstruct_control_maintenance_recovery_precondition(
            root,
            lock_fd,
            request.activation,
            locked_verified,
            live,
        )
        intent = _active_prior_recovery_intent_from_stage(
            request.activation,
            active_precondition,
            locked_verified,
        )
        additive = _ordered_control_maintenance_additive_artifacts(locked_verified)
        replacements = _ordered_control_maintenance_replacements(locked_verified)
        cleanups = _ordered_control_maintenance_cleanup_steps(
            active_precondition,
            additive,
        )
        _validate_control_maintenance_journal_authority(
            live,
            intent,
            additive,
            replacements,
            cleanups,
        )
        journal_temporary = _reconcile_control_maintenance_journal_temporary(
            root.fd,
            intent,
            live,
            additive,
            replacements,
            cleanups,
            remove=False,
        )
        pending_directory_suffix = (
            _control_maintenance_journal_pending_directory_suffix(
                live,
                additive,
            )
        )
        if pending_directory_suffix:
            pending_index = live.value["pending_step"]["index"]
            _reconcile_activation_pending_directories(
                root.fd,
                additive[pending_index],
                pending_directory_suffix,
            )
        _audit_control_maintenance_live_state(
            root,
            lock_fd,
            active_precondition,
            live,
            additive,
            replacements,
            cleanups,
            journal_temporary=journal_temporary,
        )
        if (
            _read_activation_file(
                root.fd,
                "transaction.json",
                "control maintenance recovery journal",
            )
            != live.raw
        ):
            raise DeploymentError(
                "control maintenance recovery journal changed during audit"
            )
        recovery_controller, final_recovery_raw = _capture_control_recovery_controller(
            locked_verified
        )
        if (
            module_path != recovery_controller.staged_path
            or final_recovery_raw != recovery_raw
        ):
            raise DeploymentError(
                "control recovery module source changed before continuation"
            )
        _reconcile_control_maintenance_journal_temporary(
            root.fd,
            intent,
            live,
            additive,
            replacements,
            cleanups,
        )
        return _continue_control_maintenance_transaction(
            root,
            lock_fd,
            active_precondition,
            locked_verified,
            intent,
            live,
            source_parser=(
                _bridge_legacy_active_receipt_source if bridge_transition else None
            ),
            policy_parser=(
                _parse_bridge_legacy_compatibility_policy if bridge_transition else None
            ),
        )
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)
            os.close(root.fd)


def _rebound_control_deployment(module: ModuleType, deployment: object) -> Any:
    def rebound_routine_deployment(routine: DeploymentRequest) -> Any:
        source_evidence = routine.source_evidence
        if type(source_evidence) is HarnessSnapshotEvidence:
            rebound_source_evidence = module.HarnessSnapshotEvidence(
                binding_raw=bytes(source_evidence.binding_raw),
                receipt_raw=bytes(source_evidence.receipt_raw),
            )
        elif type(source_evidence) is PublisherChannelEvidence:
            rebound_source_evidence = module.PublisherChannelEvidence(
                binding_raw=bytes(source_evidence.binding_raw),
                publisher_record_raw=bytes(source_evidence.publisher_record_raw),
            )
        elif type(source_evidence) is ExactReleaseEvidence:
            rebound_source_evidence = module.ExactReleaseEvidence()
        else:
            raise DeploymentError("control recovery source evidence type disagrees")
        return module.DeploymentRequest(
            candidate_root=module.Path(str(routine.candidate_root)),
            canonical_root=module.Path(str(routine.canonical_root)),
            source_selection_raw=bytes(routine.source_selection_raw),
            source_evidence=rebound_source_evidence,
            runtime_qualification_raw=bytes(routine.runtime_qualification_raw),
            maintenance_transaction_sha256=str(routine.maintenance_transaction_sha256),
            expected_active_receipt_sha256=str(routine.expected_active_receipt_sha256),
        )

    if type(deployment) is RollbackToRequest:
        return module.RollbackToRequest(
            canonical_root=module.Path(str(deployment.canonical_root)),
            expected_active_receipt_sha256=str(
                deployment.expected_active_receipt_sha256
            ),
            target_receipt_sha256=str(deployment.target_receipt_sha256),
            maintenance_transaction_sha256=str(
                deployment.maintenance_transaction_sha256
            ),
        )
    if type(deployment) is BridgeTransitionRequest:
        return module.BridgeTransitionRequest(
            deployment=rebound_routine_deployment(deployment.deployment),
            release_manifest_path=module.Path(str(deployment.release_manifest_path)),
            endpoint_projection_raw=bytes(deployment.endpoint_projection_raw),
            execution_class=str(deployment.execution_class),
        )
    if type(deployment) is DeploymentRequest:
        return rebound_routine_deployment(deployment)
    raise DeploymentError("control recovery request type disagrees")


def _recover_control_maintenance_through_prior_controller(
    request: RecoveryRequest,
    expected: _ActivationJournal,
) -> TransactionResult:
    if not _active_prior_control_recovery(request, expected):
        raise DeploymentError("control recovery dispatch disagrees")
    freeze5_authority = _freeze5_recovery_authority(request, expected)
    if freeze5_authority is not None:
        return _recover_control_maintenance_through_freeze5(
            request,
            freeze5_authority,
        )
    _, verified = _recovery_precondition_from_stage(
        request.activation,
        expected,
    )
    controller, raw = _capture_control_recovery_controller(verified)
    module_seed = b"\0".join(
        (
            raw,
            verified.raw,
            request.expected_journal_raw,
            os.urandom(32),
        )
    )
    module_name = (
        "_task_witness_control_recovery_" + hashlib.sha256(module_seed).hexdigest()
    )
    if module_name in sys.modules:
        raise DeploymentError("control recovery module name collision")
    module = ModuleType(module_name)
    module.__file__ = str(controller.staged_path)
    module.__package__ = None
    module.__loader__ = None
    module.__spec__ = None
    sys.modules[module_name] = module
    try:
        code = compile(
            raw,
            str(controller.staged_path),
            "exec",
            dont_inherit=True,
            optimize=0,
        )
        exec(code, module.__dict__)  # noqa: S102 - verified staged recovery source
        if (
            sys.modules.get(module_name) is not module
            or module.__name__ != module_name
            or module.__file__ != str(controller.staged_path)
        ):
            raise DeploymentError("control recovery module identity changed")
        rebound_controller, rebound_raw = _capture_control_recovery_controller(verified)
        if rebound_controller != controller or rebound_raw != raw:
            raise DeploymentError("control recovery module source changed after load")
        rebound_deployment = _rebound_control_deployment(
            module,
            request.activation.deployment,
        )
        rebound_activation = module.ActivationRequest(
            deployment=rebound_deployment,
            authorization_raw=bytes(request.activation.authorization_raw),
            stage_receipt=module.Path(str(request.activation.stage_receipt)),
        )
        rebound_request = module.RecoveryRequest(
            activation=rebound_activation,
            expected_journal_raw=bytes(request.expected_journal_raw),
        )
        try:
            rebound_result = module._recover_control_maintenance_transaction(
                rebound_request
            )
        except module.DeploymentError as error:
            raise DeploymentError(str(error)) from error
        if type(rebound_result) is not module.TransactionResult:
            raise DeploymentError("control recovery result type disagrees")
        return TransactionResult(
            transaction_id=rebound_result.transaction_id,
            outcome=rebound_result.outcome,
            candidate_receipt_sha256=rebound_result.candidate_receipt_sha256,
            active_receipt_sha256=rebound_result.active_receipt_sha256,
            accepted_envelope_sha256=rebound_result.accepted_envelope_sha256,
            journal_sha256=rebound_result.journal_sha256,
            journal_value=_freeze_json(_thaw(rebound_result.journal_value)),
            journal_raw=bytes(rebound_result.journal_raw),
        )
    finally:
        sys.modules.pop(module_name, None)


def recover_transaction(request: RecoveryRequest) -> TransactionResult:
    """Resume one exact activation transaction under its recorded authority."""

    _validate_recovery_request(request)
    expected = _parse_activation_journal(request.expected_journal_raw)
    if _active_prior_control_recovery(request, expected):
        return _recover_control_maintenance_through_prior_controller(
            request,
            expected,
        )
    precondition, _ = _recovery_precondition_from_stage(
        request.activation,
        expected,
    )
    root, lock_fd = _open_locked_activation_root(
        precondition,
        root_mapping_only=True,
    )
    try:
        live_raw = _read_activation_file(
            root.fd,
            "transaction.json",
            "activation transaction journal",
        )
        if live_raw != request.expected_journal_raw:
            raise DeploymentError("activation recovery journal freshness disagrees")
        live = _parse_activation_journal(live_raw)
        locked_precondition, _ = _recovery_precondition_from_stage(
            request.activation,
            live,
        )
        if locked_precondition != precondition:
            raise DeploymentError("activation recovery precondition changed")
        if type(request.activation.deployment) is DeploymentRequest:
            if live.value["transaction_class"] == "control-set-maintenance":
                raise DeploymentError("control maintenance recovery is not implemented")
            verified = verify_deployment_stage(
                request.activation.stage_receipt,
            )
            if (
                live.value["phase"] == "terminal"
                and live.value["terminal_result"]["outcome"] != "recovery-required"
            ):
                provisional_precondition = _reconstruct_routine_recovery_precondition(
                    root,
                    lock_fd,
                    request.activation,
                    verified,
                    live,
                    opaque_pending_temporary=True,
                )
                if _rebind_routine_activation_from_stage(
                    request.activation,
                    provisional_precondition,
                    verified,
                ):
                    raise DeploymentError(
                        "routine activation recovery stage rebind disagrees"
                    )
                provisional_intent, _ = _routine_activation_intent_from_stage(
                    provisional_precondition,
                    request.activation,
                    verified,
                )
                provisional_artifacts = _ordered_routine_activation_artifacts(verified)
                provisional_cleanups = _ordered_routine_cleanup_steps(
                    provisional_precondition,
                    verified,
                )
                _validate_activation_journal_authority(
                    live,
                    provisional_intent,
                    provisional_artifacts,
                    provisional_cleanups,
                )
                provisional_journal_temporary = _reconcile_activation_journal_temporary(
                    root.fd,
                    provisional_intent,
                    live,
                    provisional_artifacts,
                    provisional_cleanups,
                    remove=False,
                )
                _audit_routine_live_state(
                    root,
                    lock_fd,
                    provisional_precondition,
                    verified,
                    live,
                    provisional_artifacts,
                    journal_temporary=provisional_journal_temporary,
                    opaque_pending_result_temporary=True,
                )
            _reconcile_pending_transaction_result_state(root.fd, live)
            routine_precondition = _reconstruct_routine_recovery_precondition(
                root,
                lock_fd,
                request.activation,
                verified,
                live,
            )
            control_maintenance = _rebind_routine_activation_from_stage(
                request.activation,
                routine_precondition,
                verified,
            )
            if control_maintenance:
                raise DeploymentError(
                    "routine activation recovery stage rebind disagrees"
                )
            intent, _ = _routine_activation_intent_from_stage(
                routine_precondition,
                request.activation,
                verified,
            )
            artifacts = _ordered_routine_activation_artifacts(verified)
            cleanups = _ordered_routine_cleanup_steps(
                routine_precondition,
                verified,
            )
            _validate_activation_journal_authority(
                live,
                intent,
                artifacts,
                cleanups,
            )
            journal_temporary = _reconcile_activation_journal_temporary(
                root.fd,
                intent,
                live,
                artifacts,
                cleanups,
                remove=False,
            )
            _audit_routine_live_state(
                root,
                lock_fd,
                routine_precondition,
                verified,
                live,
                artifacts,
                journal_temporary=journal_temporary,
            )
            if (
                _read_activation_file(
                    root.fd,
                    "transaction.json",
                    "routine activation recovery journal",
                )
                != live.raw
            ):
                raise DeploymentError(
                    "routine activation recovery journal changed during audit"
                )
            _reconcile_activation_journal_temporary(
                root.fd,
                intent,
                live,
                artifacts,
                cleanups,
            )
            return _continue_routine_transaction(
                root,
                lock_fd,
                routine_precondition,
                verified,
                intent,
                live,
            )
        if (
            live.value["phase"] == "terminal"
            and live.value["terminal_result"]["outcome"] != "recovery-required"
        ):
            provisional_recorded = _rebind_recorded_first_install_result_baseline(
                root.fd,
                locked_precondition,
                live,
                opaque_pending_temporary=True,
            )
            provisional_prepared, _, provisional_verified = (
                _rebind_first_install_activation(
                    request.activation,
                    recorded_precondition=provisional_recorded,
                )
            )
            provisional_intent, _ = _activation_intent(
                provisional_prepared,
                request.activation,
                provisional_verified,
            )
            provisional_artifacts = _ordered_activation_artifacts(provisional_verified)
            provisional_removals = _ordered_activation_removal_steps(
                provisional_artifacts
            )
            _validate_activation_journal_authority(
                live,
                provisional_intent,
                provisional_artifacts,
                provisional_removals,
            )
            provisional_journal_temporary = _reconcile_activation_journal_temporary(
                root.fd,
                provisional_intent,
                live,
                provisional_artifacts,
                provisional_removals,
                remove=False,
            )
            _audit_first_install_live_state(
                root.fd,
                provisional_prepared,
                live,
                provisional_artifacts,
                provisional_removals,
                journal_temporary=provisional_journal_temporary,
                opaque_pending_result_temporary=True,
            )
        _reconcile_pending_transaction_result_state(root.fd, live)
        precondition = _rebind_recorded_first_install_result_baseline(
            root.fd,
            precondition,
            live,
        )
        prepared, _, verified = _rebind_first_install_activation(
            request.activation,
            recorded_precondition=precondition,
        )
        intent, _ = _activation_intent(
            prepared,
            request.activation,
            verified,
        )
        artifacts = _ordered_activation_artifacts(verified)
        removals = _ordered_activation_removal_steps(artifacts)
        _validate_activation_journal_authority(
            live,
            intent,
            artifacts,
            removals,
        )
        journal_temporary = _reconcile_activation_journal_temporary(
            root.fd,
            intent,
            live,
            artifacts,
            removals,
            remove=False,
        )
        pending_directory_suffix = _activation_journal_pending_directory_suffix(
            live,
            artifacts,
        )
        if pending_directory_suffix:
            pending_directory_residues = frozenset(pending_directory_suffix)
            _audit_first_install_live_state(
                root.fd,
                prepared,
                live,
                artifacts,
                removals,
                journal_temporary=journal_temporary,
                pending_directory_residues=pending_directory_residues,
            )
            _recheck_activation_lock_acquisition(
                root,
                lock_fd,
                precondition,
                root_mapping_only=True,
            )
            if (
                _read_activation_file(
                    root.fd,
                    "transaction.json",
                    "activation transaction journal",
                )
                != live.raw
            ):
                raise DeploymentError(
                    "activation recovery journal changed before directory repair"
                )
            pending = live.value["pending_step"]
            _reconcile_activation_pending_directories(
                root.fd,
                artifacts[pending["index"]],
                pending_directory_suffix,
            )
            _recheck_activation_lock_acquisition(
                root,
                lock_fd,
                precondition,
                root_mapping_only=True,
            )
            if (
                _read_activation_file(
                    root.fd,
                    "transaction.json",
                    "activation transaction journal",
                )
                != live.raw
            ):
                raise DeploymentError(
                    "activation recovery journal changed during directory repair"
                )
        _audit_first_install_live_state(
            root.fd,
            prepared,
            live,
            artifacts,
            removals,
            journal_temporary=journal_temporary,
        )
        _recheck_activation_lock_acquisition(
            root,
            lock_fd,
            precondition,
            root_mapping_only=True,
        )
        if (
            _read_activation_file(
                root.fd,
                "transaction.json",
                "activation transaction journal",
            )
            != live.raw
        ):
            raise DeploymentError("activation recovery journal changed during rebind")
        _reconcile_activation_journal_temporary(
            root.fd,
            intent,
            live,
            artifacts,
            removals,
        )
        _recheck_activation_lock_acquisition(
            root,
            lock_fd,
            precondition,
            root_mapping_only=True,
        )
        if (
            _read_activation_file(
                root.fd,
                "transaction.json",
                "activation transaction journal",
            )
            != live.raw
        ):
            raise DeploymentError(
                "activation recovery journal changed during temporary reconciliation"
            )
        return _continue_first_install_transaction(
            root,
            lock_fd,
            prepared,
            verified,
            intent,
            live,
        )
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)
            os.close(root.fd)


def _validate_result_reconciliation_request(
    request: ResultReconciliationRequest,
) -> None:
    if type(request) is not ResultReconciliationRequest:
        raise DeploymentError("transaction result reconciliation request type mismatch")
    _validate_activation_request(request.activation)
    if type(request.expected_terminal_journal_raw) is not bytes:
        raise DeploymentError(
            "transaction result reconciliation journal must be exact bytes"
        )


def _capture_expected_transaction_result(
    canonical_root_fd: int,
    canonical_root: Path,
    journal: _ActivationJournal,
) -> Mapping[str, bytes]:
    retained, _, _ = _capture_transaction_result_inventory(
        canonical_root_fd,
        canonical_root,
    )
    final_name, _ = _transaction_result_names(journal.value["transaction_id"])
    relative = f"transaction-results/{final_name}"
    if retained.get(relative) != journal.raw:
        raise DeploymentError("retained transaction result authority disagrees")
    return retained


def _result_reconciliation_runs_in_staged_prior_controller(
    request: ResultReconciliationRequest,
    expected: _ActivationJournal,
) -> bool:
    _, verified = _recovery_precondition_from_stage(
        request.activation,
        expected,
    )
    controller, _ = _capture_control_recovery_controller(verified)
    return (
        _normalized_absolute_path(
            Path(os.path.abspath(__file__)),
            "result reconciliation module source",
        )
        == controller.staged_path
    )


def reconcile_transaction_result(
    request: ResultReconciliationRequest,
) -> TransactionResult:
    """Reconcile one exact retained terminal result under original authority."""

    _validate_result_reconciliation_request(request)
    expected = _validated_terminal_result_journal(
        request.expected_terminal_journal_raw,
        "expected transaction result",
    )
    freeze5_authority = _freeze5_recovery_authority(
        request,
        expected,
        require_live_journal=False,
    )
    if freeze5_authority is not None:
        return _reconcile_transaction_result_through_freeze5(
            request,
            freeze5_authority,
        )
    if (
        type(request.activation.deployment) is BridgeTransitionRequest
        and expected.value["transaction_class"] == "control-set-maintenance"
        and expected.value["prior"]["state"] == "active"
        and not _result_reconciliation_runs_in_staged_prior_controller(
            request,
            expected,
        )
    ):
        _, verified = _recovery_precondition_from_stage(
            request.activation,
            expected,
        )
        controller, raw = _capture_control_recovery_controller(verified)
        module_seed = b"\0".join(
            (
                raw,
                verified.raw,
                request.expected_terminal_journal_raw,
                os.urandom(32),
            )
        )
        module_name = (
            "_task_witness_result_reconciliation_"
            + hashlib.sha256(module_seed).hexdigest()
        )
        if module_name in sys.modules:
            raise DeploymentError("result reconciliation module name collision")
        module = ModuleType(module_name)
        module.__file__ = str(controller.staged_path)
        module.__package__ = None
        module.__loader__ = None
        module.__spec__ = None
        sys.modules[module_name] = module
        try:
            code = compile(
                raw,
                str(controller.staged_path),
                "exec",
                dont_inherit=True,
                optimize=0,
            )
            exec(code, module.__dict__)  # noqa: S102 - verified staged authority
            if (
                sys.modules.get(module_name) is not module
                or module.__name__ != module_name
                or module.__file__ != str(controller.staged_path)
            ):
                raise DeploymentError("result reconciliation module identity changed")
            rebound_controller, rebound_raw = _capture_control_recovery_controller(
                verified
            )
            if rebound_controller != controller or rebound_raw != raw:
                raise DeploymentError(
                    "result reconciliation module source changed after load"
                )
            rebound_activation = module.ActivationRequest(
                deployment=_rebound_control_deployment(
                    module,
                    request.activation.deployment,
                ),
                authorization_raw=bytes(request.activation.authorization_raw),
                stage_receipt=module.Path(str(request.activation.stage_receipt)),
            )
            rebound_result = module.reconcile_transaction_result(
                module.ResultReconciliationRequest(
                    activation=rebound_activation,
                    expected_terminal_journal_raw=bytes(
                        request.expected_terminal_journal_raw
                    ),
                )
            )
            if type(rebound_result) is not module.TransactionResult:
                raise DeploymentError("result reconciliation result type disagrees")
            return TransactionResult(
                transaction_id=rebound_result.transaction_id,
                outcome=rebound_result.outcome,
                candidate_receipt_sha256=(rebound_result.candidate_receipt_sha256),
                active_receipt_sha256=rebound_result.active_receipt_sha256,
                accepted_envelope_sha256=(rebound_result.accepted_envelope_sha256),
                journal_sha256=rebound_result.journal_sha256,
                journal_value=_freeze_json(_thaw(rebound_result.journal_value)),
                journal_raw=bytes(rebound_result.journal_raw),
            )
        except module.DeploymentError as error:
            raise DeploymentError(str(error)) from error
        finally:
            sys.modules.pop(module_name, None)
    recorded, verified = _recovery_precondition_from_stage(
        request.activation,
        expected,
    )
    root, lock_fd = _open_locked_activation_root(
        recorded,
        root_mapping_only=True,
    )
    try:
        if "transaction.json" in os.listdir(root.fd):
            raise DeploymentError(
                "transaction result reconciliation requires no live journal"
            )
        retained_before = _capture_expected_transaction_result(
            root.fd,
            recorded.canonical_root,
            expected,
        )
        if type(request.activation.deployment) is RollbackToRequest:
            if expected.value["transaction_class"] != "manual-exact-target-rollback":
                raise DeploymentError(
                    "manual transaction result reconciliation class disagrees"
                )
            precondition = _reconstruct_control_maintenance_recovery_precondition(
                root,
                lock_fd,
                request.activation,
                verified,
                expected,
            )
            prepared, _, locked_verified = _rebind_manual_rollback_activation(
                request.activation,
                precondition,
            )
            if locked_verified.raw != verified.raw:
                raise DeploymentError(
                    "manual transaction result reconciliation stage changed"
                )
            intent, _ = _manual_rollback_activation_intent(
                prepared,
                request.activation,
                locked_verified,
            )
            additive = _ordered_control_maintenance_additive_artifacts(locked_verified)
            replacements = _ordered_control_maintenance_replacements(locked_verified)
            cleanups = _ordered_control_maintenance_cleanup_steps(
                precondition,
                additive,
            )
            _validate_control_maintenance_journal_authority(
                expected,
                intent,
                additive,
                replacements,
                cleanups,
            )
            _audit_control_maintenance_live_state(
                root,
                lock_fd,
                precondition,
                expected,
                additive,
                replacements,
                cleanups,
                live_journal_required=False,
            )
        elif type(request.activation.deployment) is DeploymentRequest:
            if expected.value["transaction_class"] == "control-set-maintenance":
                precondition = _reconstruct_control_maintenance_recovery_precondition(
                    root,
                    lock_fd,
                    request.activation,
                    verified,
                    expected,
                )
                control_maintenance = _rebind_routine_activation_from_stage(
                    request.activation,
                    precondition,
                    verified,
                )
                if not control_maintenance:
                    raise DeploymentError(
                        "control maintenance reconciliation stage rebind disagrees"
                    )
                intent, _ = _control_maintenance_activation_intent_from_stage(
                    precondition,
                    request.activation,
                    verified,
                )
                additive = _ordered_control_maintenance_additive_artifacts(verified)
                replacements = _ordered_control_maintenance_replacements(verified)
                cleanups = _ordered_control_maintenance_cleanup_steps(
                    precondition,
                    additive,
                )
                _validate_control_maintenance_journal_authority(
                    expected,
                    intent,
                    additive,
                    replacements,
                    cleanups,
                )
                _audit_control_maintenance_live_state(
                    root,
                    lock_fd,
                    precondition,
                    expected,
                    additive,
                    replacements,
                    cleanups,
                    live_journal_required=False,
                )
            else:
                if expected.value["transaction_class"] != "routine-payload":
                    raise DeploymentError(
                        "transaction result reconciliation class disagrees"
                    )
                precondition = _reconstruct_routine_recovery_precondition(
                    root,
                    lock_fd,
                    request.activation,
                    verified,
                    expected,
                )
                control_maintenance = _rebind_routine_activation_from_stage(
                    request.activation,
                    precondition,
                    verified,
                )
                if control_maintenance:
                    raise DeploymentError(
                        "routine transaction result reconciliation stage rebind disagrees"
                    )
                intent, _ = _routine_activation_intent_from_stage(
                    precondition,
                    request.activation,
                    verified,
                )
                artifacts = _ordered_routine_activation_artifacts(verified)
                cleanups = _ordered_routine_cleanup_steps(
                    precondition,
                    verified,
                )
                _validate_activation_journal_authority(
                    expected,
                    intent,
                    artifacts,
                    cleanups,
                )
                _audit_routine_live_state(
                    root,
                    lock_fd,
                    precondition,
                    verified,
                    expected,
                    artifacts,
                    live_journal_required=False,
                )
        elif type(request.activation.deployment) is BridgeTransitionRequest:
            if expected.value["transaction_class"] != "control-set-maintenance":
                raise DeploymentError(
                    "bridge transaction result reconciliation class disagrees"
                )
            precondition = _reconstruct_control_maintenance_recovery_precondition(
                root,
                lock_fd,
                request.activation,
                verified,
                expected,
            )
            locked_verified, bridge_transition = _rebind_bridge_activation(
                request.activation,
                precondition,
                staged_predecessor=True,
            )
            if locked_verified.raw != verified.raw:
                raise DeploymentError(
                    "bridge transaction result reconciliation stage changed"
                )
            intent, _ = _control_maintenance_activation_intent_from_stage(
                precondition,
                request.activation,
                locked_verified,
                bridge_transition=bridge_transition,
            )
            additive = _ordered_control_maintenance_additive_artifacts(locked_verified)
            replacements = _ordered_control_maintenance_replacements(locked_verified)
            cleanups = _ordered_control_maintenance_cleanup_steps(
                precondition,
                additive,
            )
            _validate_control_maintenance_journal_authority(
                expected,
                intent,
                additive,
                replacements,
                cleanups,
            )
            _audit_control_maintenance_live_state(
                root,
                lock_fd,
                precondition,
                expected,
                additive,
                replacements,
                cleanups,
                live_journal_required=False,
            )
        else:
            recorded = _rebind_recorded_first_install_result_baseline(
                root.fd,
                recorded,
                expected,
            )
            prepared, _, locked_verified = _rebind_first_install_activation(
                request.activation,
                recorded_precondition=recorded,
            )
            if locked_verified.raw != verified.raw:
                raise DeploymentError("transaction result reconciliation stage changed")
            intent, _ = _activation_intent(
                prepared,
                request.activation,
                locked_verified,
            )
            artifacts = _ordered_activation_artifacts(locked_verified)
            removals = _ordered_activation_removal_steps(artifacts)
            _validate_activation_journal_authority(
                expected,
                intent,
                artifacts,
                removals,
            )
            _audit_first_install_live_state(
                root.fd,
                prepared,
                expected,
                artifacts,
                removals,
                live_journal_required=False,
            )
        retained_after = _capture_expected_transaction_result(
            root.fd,
            recorded.canonical_root,
            expected,
        )
        if retained_after != retained_before:
            raise DeploymentError(
                "retained transaction result inventory changed during reconciliation"
            )
        return _transaction_result(expected)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)
            os.close(root.fd)


def _open_private_root(path: Path, *, create: bool = True) -> tuple[int, Path]:
    if not path.is_absolute():
        raise DeploymentError("retained trust root must be absolute")
    if create:
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        except OSError as error:
            raise DeploymentError("retained trust root cannot be created") from error
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
            raise DeploymentError("retained trust root is not a real directory")
        descriptor = os.open(path, _DIRECTORY_FLAGS)
        after = os.fstat(descriptor)
    except DeploymentError:
        raise
    except OSError as error:
        raise DeploymentError("retained trust root cannot be opened") from error
    try:
        if _identity(before) != _identity(after):
            raise DeploymentError("retained trust root changed while it was opened")
        canonical = path.resolve(strict=True)
        original_visible = path.lstat()
        canonical_visible = canonical.lstat()
        if _identity(original_visible) != _identity(after) or _identity(
            canonical_visible
        ) != _identity(after):
            raise DeploymentError("retained trust root path mapping changed")
        if after.st_uid != os.geteuid() or stat.S_IMODE(after.st_mode) != 0o700:
            raise DeploymentError("retained trust root is not private")
        return descriptor, canonical
    except DeploymentError:
        os.close(descriptor)
        raise
    except (OSError, RuntimeError) as error:
        os.close(descriptor)
        raise DeploymentError(
            "retained trust root canonical path is unavailable"
        ) from error


def _open_disjoint_private_stage_root(
    path: Path,
    *,
    canonical_root: Path,
    candidate_root: Path,
) -> _StageRoot:
    """Create/open a physical stage only after descriptor-bound disjointness."""

    if not path.name:
        raise DeploymentError("deployment staging root has no directory name")
    lexical_parent = path.parent
    try:
        canonical_parent = lexical_parent.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise DeploymentError(
            "deployment staging root parent cannot be resolved"
        ) from error
    parent = _open_root(
        canonical_parent,
        "deployment staging root parent",
        mapping_only=True,
    )
    protected: list[tuple[_RootSnapshot, str]] = []
    stage_fd = -1
    transferred = False
    try:
        for protected_root, label in (
            (canonical_root, "deployment canonical root"),
            (candidate_root, "deployment candidate root"),
        ):
            protected.append(
                (
                    _open_root(protected_root, label, mapping_only=True),
                    label,
                )
            )
        _recheck_root_alias(
            parent,
            lexical_parent,
            canonical_parent,
            "deployment staging root parent",
        )
        canonical_stage_path = canonical_parent / path.name
        if any(
            canonical_stage_path.is_relative_to(protected_root)
            or protected_root.is_relative_to(canonical_stage_path)
            for protected_root in (canonical_root, candidate_root)
        ):
            raise DeploymentError(
                "deployment staging root must be disjoint from installation and source"
            )
        for protected_root, label in protected:
            _recheck_root(protected_root, label)
        _recheck_root_alias(
            parent,
            lexical_parent,
            canonical_parent,
            "deployment staging root parent",
        )
        stage_fd = _open_private_directory(
            parent.fd,
            path.name,
            "deployment staging root",
        )
        for protected_root, label in protected:
            _recheck_root(protected_root, label)
        _recheck_root_alias(
            parent,
            lexical_parent,
            canonical_parent,
            "deployment staging root parent",
        )
        try:
            visible = canonical_stage_path.lstat()
            if _identity(visible) != _identity(os.fstat(stage_fd)):
                raise DeploymentError("deployment staging root mapping changed")
        except DeploymentError:
            raise
        except (OSError, RuntimeError) as error:
            raise DeploymentError(
                "deployment staging root mapping is unavailable"
            ) from error
        result = _StageRoot(
            stage_fd,
            canonical_stage_path,
            path.name,
            parent,
            lexical_parent,
            canonical_parent,
            tuple(protected),
        )
        stage_fd = -1
        transferred = True
        return result
    finally:
        if not transferred:
            if stage_fd >= 0:
                os.close(stage_fd)
            for protected_root, _ in reversed(protected):
                os.close(protected_root.fd)
            os.close(parent.fd)


def _recheck_disjoint_private_stage_root(stage: _StageRoot) -> None:
    for protected_root, label in stage.protected:
        _recheck_root(protected_root, label)
    _recheck_root_alias(
        stage.parent,
        stage.lexical_parent,
        stage.canonical_parent,
        "deployment staging root parent",
    )
    try:
        parent_visible = os.stat(
            stage.name,
            dir_fd=stage.parent.fd,
            follow_symlinks=False,
        )
        canonical_visible = stage.path.lstat()
        opened = os.fstat(stage.fd)
    except OSError as error:
        raise DeploymentError(STAGE_MAPPING_DRIFT_ERROR) from error
    if not (
        _identity(parent_visible) == _identity(canonical_visible) == _identity(opened)
    ):
        raise DeploymentError(STAGE_MAPPING_DRIFT_ERROR)


def _close_disjoint_private_stage_root(stage: _StageRoot) -> None:
    error: OSError | None = None
    for descriptor in (
        stage.fd,
        *(item.fd for item, _ in reversed(stage.protected)),
        stage.parent.fd,
    ):
        try:
            os.close(descriptor)
        except OSError as caught:
            if error is None:
                error = caught
    if error is not None:
        raise error


def _open_private_directory(
    parent_fd: int,
    name: str,
    label: str,
    *,
    create: bool = True,
) -> int:
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        except OSError as error:
            raise DeploymentError(f"{label} cannot be created") from error
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
            raise DeploymentError(f"{label} is not a real directory")
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        after = os.fstat(descriptor)
    except DeploymentError:
        raise
    except OSError as error:
        raise DeploymentError(f"{label} cannot be opened") from error
    if _identity(before) != _identity(after):
        os.close(descriptor)
        raise DeploymentError(f"{label} changed while it was opened")
    if after.st_uid != os.geteuid() or stat.S_IMODE(after.st_mode) != 0o700:
        os.close(descriptor)
        raise DeploymentError(f"{label} is not private")
    return descriptor


def _write_all(descriptor: int, raw: bytes, label: str) -> None:
    offset = 0
    try:
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise DeploymentError(f"{label} write made no progress")
            offset += written
        os.fsync(descriptor)
    except DeploymentError:
        raise
    except OSError as error:
        raise DeploymentError(f"{label} cannot be written") from error


def _read_private_file(parent_fd: int, name: str, raw: bytes, label: str) -> None:
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as error:
        raise DeploymentError(f"{label} is missing") from error
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != 0o600
    ):
        raise DeploymentError(f"{label} has invalid private-file disposition")
    try:
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        raise DeploymentError(f"{label} cannot be opened") from error
    try:
        after = os.fstat(descriptor)
        if _identity(before) != _identity(after):
            raise DeploymentError(f"{label} changed while it was opened")
        if _read_descriptor(descriptor, max(len(raw), 1), label) != raw:
            raise DeploymentError(f"{label} retained bytes disagree")
    finally:
        os.close(descriptor)


def _create_private_file(parent_fd: int, name: str, raw: bytes, label: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, 0o600, dir_fd=parent_fd)
    except FileExistsError:
        _read_private_file(parent_fd, name, raw, label)
        return
    except OSError as error:
        raise DeploymentError(f"{label} cannot be created") from error
    succeeded = False
    try:
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, raw, label)
        succeeded = True
    finally:
        os.close(descriptor)
        if not succeeded:
            try:
                os.unlink(name, dir_fd=parent_fd)
            except OSError:
                pass
    _read_private_file(parent_fd, name, raw, label)


def _generation_specs(
    validator: _DeclaredValidator,
    snapshots: Mapping[str, _FileSnapshot],
) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for module in validator.modules:
        snapshot = snapshots[module.relative_path]
        if len(snapshot.raw) != module.length:
            raise DeploymentError(f"validator module {module.name} length mismatch")
        if hashlib.sha256(snapshot.raw).hexdigest() != module.sha256:
            raise DeploymentError(
                f"validator module {module.name} content digest mismatch"
            )
        result[f"{module.name}.py"] = snapshot.raw
    return result


def _verify_generation(
    validators_fd: int,
    storage_validators_path: Path,
    installed_validators_path: Path,
    implementation: str,
    expected: Mapping[str, bytes],
) -> tuple[RetainedModule, ...]:
    generation_name = f"sha256-{implementation}"
    generation_fd = _open_private_directory(
        validators_fd,
        generation_name,
        "retained validator generation",
        create=False,
    )
    try:
        try:
            inventory = set(os.listdir(generation_fd))
        except OSError as error:
            raise DeploymentError(
                "retained validator generation inventory is unavailable"
            ) from error
        if inventory != set(expected):
            raise DeploymentError("retained validator generation inventory mismatch")
        modules = []
        for name, raw in expected.items():
            _read_private_file(
                generation_fd,
                name,
                raw,
                "retained validator module",
            )
            modules.append(
                RetainedModule(
                    name.removesuffix(".py"),
                    storage_validators_path / generation_name / name,
                    installed_validators_path / generation_name / name,
                    raw,
                    hashlib.sha256(raw).hexdigest(),
                )
            )
        return tuple(modules)
    finally:
        os.close(generation_fd)


def _materialize_generation(
    validators_fd: int,
    storage_validators_path: Path,
    installed_validators_path: Path,
    implementation: str,
    expected: Mapping[str, bytes],
) -> tuple[RetainedModule, ...]:
    generation_name = f"sha256-{implementation}"
    created = False
    try:
        os.mkdir(generation_name, 0o700, dir_fd=validators_fd)
        created = True
    except FileExistsError:
        return _verify_generation(
            validators_fd,
            storage_validators_path,
            installed_validators_path,
            implementation,
            expected,
        )
    except OSError as error:
        raise DeploymentError(
            "retained validator generation cannot be created"
        ) from error
    generation_fd = -1
    try:
        generation_fd = _open_private_directory(
            validators_fd,
            generation_name,
            "retained validator generation",
        )
        for name, raw in expected.items():
            _create_private_file(
                generation_fd,
                name,
                raw,
                "retained validator module",
            )
        os.fsync(generation_fd)
    except BaseException:
        if generation_fd >= 0:
            for name in expected:
                try:
                    os.unlink(name, dir_fd=generation_fd)
                except OSError:
                    pass
        if created:
            try:
                os.rmdir(generation_name, dir_fd=validators_fd)
            except OSError:
                pass
        raise
    finally:
        if generation_fd >= 0:
            os.close(generation_fd)
    return _verify_generation(
        validators_fd,
        storage_validators_path,
        installed_validators_path,
        implementation,
        expected,
    )


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return _FrozenDict({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _projected_trust_root(
    installed_trust_root: Path | None,
    storage_trust_root: Path,
) -> Path:
    if installed_trust_root is None:
        return storage_trust_root
    projected = Path(installed_trust_root)
    normalized = Path(os.path.abspath(projected))
    if not projected.is_absolute() or projected != normalized:
        raise DeploymentError("installed trust root must be a normalized absolute path")
    return projected


def _normalized_absolute_path(value: Path, label: str) -> Path:
    path = Path(value)
    normalized = Path(os.path.abspath(path))
    if not path.is_absolute() or path != normalized:
        raise DeploymentError(f"{label} must be a normalized absolute path")
    if any(ord(character) < 0x20 for character in str(path)):
        raise DeploymentError(f"{label} contains a control character")
    return path


def _capture_absolute_regular(path: Path, limit: int, label: str) -> bytes:
    normalized = _normalized_absolute_path(path, label)
    try:
        if normalized.resolve(strict=True) != normalized:
            raise DeploymentError(f"{label} has a noncanonical path mapping")
    except DeploymentError:
        raise
    except (OSError, RuntimeError) as error:
        raise DeploymentError(f"{label} cannot be resolved") from error
    components = tuple(normalized.parts[1:])
    root = _open_root(
        Path("/"),
        "filesystem root",
        mapping_only=len(components) > 1,
    )
    snapshot: _FileSnapshot | None = None
    try:
        snapshot = _capture_regular(
            root,
            components,
            label,
            limit=limit,
            shared_ancestor_mappings=True,
        )
        if snapshot is None:
            raise DeploymentError(f"{label} is missing")
        _recheck_file(snapshot)
        _recheck_root(root, "filesystem root")
        return snapshot.raw
    finally:
        if snapshot is not None:
            _close_file(snapshot)
        os.close(root.fd)


def _capture_first_install_precondition(path: Path) -> FirstInstallPrecondition:
    """Prove an absent live deployment plus exact retained result history."""

    canonical_root = _normalized_absolute_path(path, "first-install canonical root")
    try:
        if canonical_root.resolve(strict=True) != canonical_root:
            raise DeploymentError(
                "first-install canonical root has a noncanonical path mapping"
            )
        before = canonical_root.lstat()
    except DeploymentError:
        raise
    except (OSError, RuntimeError) as error:
        raise DeploymentError("first-install canonical root is unavailable") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise DeploymentError("first-install canonical root is not a real directory")
    try:
        root_fd = os.open(canonical_root, _DIRECTORY_FLAGS)
    except OSError as error:
        raise DeploymentError(
            "first-install canonical root cannot be opened"
        ) from error
    lock_fd = -1
    try:
        opened_root = os.fstat(root_fd)
        root_identity = _identity(opened_root)
        if _identity(before) != root_identity:
            raise DeploymentError("first-install canonical root changed while opening")
        if (
            opened_root.st_uid != os.geteuid()
            or stat.S_IMODE(opened_root.st_mode) != 0o700
        ):
            raise DeploymentError("first-install canonical root is not private")
        _reject_macos_allow_acl(root_fd, "first-install canonical root")
        try:
            inventory = set(os.listdir(root_fd))
        except OSError as error:
            raise DeploymentError(
                "first-install canonical root cannot be enumerated"
            ) from error
        retained_result_raws, result_state, _ = _capture_transaction_result_inventory(
            root_fd,
            canonical_root,
        )
        expected_inventory = {"activation.lock"}
        if result_state.directory_present:
            expected_inventory.add("transaction-results")
        if inventory != expected_inventory:
            raise DeploymentError(
                "first-install deployment state is not exactly absent"
            )
        try:
            lock_before = os.stat(
                "activation.lock",
                dir_fd=root_fd,
                follow_symlinks=False,
            )
        except OSError as error:
            raise DeploymentError(
                "first-install activation lock is unavailable"
            ) from error
        if (
            stat.S_ISLNK(lock_before.st_mode)
            or not stat.S_ISREG(lock_before.st_mode)
            or lock_before.st_uid != os.geteuid()
            or lock_before.st_nlink != 1
        ):
            raise DeploymentError("first-install activation lock is not private")
        if stat.S_IMODE(lock_before.st_mode) != 0o600:
            raise DeploymentError("first-install activation lock mode must be 0600")
        if lock_before.st_size != 0:
            raise DeploymentError("first-install activation lock must be empty")
        try:
            lock_fd = os.open(
                "activation.lock",
                _FILE_FLAGS,
                dir_fd=root_fd,
            )
        except OSError as error:
            raise DeploymentError(
                "first-install activation lock cannot be opened"
            ) from error
        _reject_macos_allow_acl(lock_fd, "first-install activation lock")
        lock_after = os.fstat(lock_fd)
        lock_identity = _identity(lock_after)
        lock_visible = os.stat(
            "activation.lock",
            dir_fd=root_fd,
            follow_symlinks=False,
        )
        _reject_macos_allow_acl(lock_fd, "first-install activation lock")
        if (
            _identity(lock_before) != lock_identity
            or _identity(lock_visible) != lock_identity
        ):
            raise DeploymentError("first-install activation lock mapping changed")
        root_visible = canonical_root.lstat()
        _reject_macos_allow_acl(root_fd, "first-install canonical root")
        if (
            _identity(os.fstat(root_fd)) != root_identity
            or _identity(root_visible) != root_identity
            or set(os.listdir(root_fd)) != inventory
            or _capture_transaction_result_inventory(
                root_fd,
                canonical_root,
            )[0]
            != retained_result_raws
        ):
            raise DeploymentError("first-install canonical root changed during capture")
        return FirstInstallPrecondition(
            canonical_root,
            root_identity,
            _freeze(
                {
                    "path": str(canonical_root / "activation.lock"),
                    "device": lock_after.st_dev,
                    "inode": lock_after.st_ino,
                    "owner": lock_after.st_uid,
                    "mode": stat.S_IMODE(lock_after.st_mode),
                }
            ),
            lock_identity,
            True,
            _transaction_result_sha256s(retained_result_raws),
        )
    except OSError as error:
        raise DeploymentError(
            "first-install precondition cannot be rechecked"
        ) from error
    finally:
        if lock_fd >= 0:
            os.close(lock_fd)
        os.close(root_fd)


def _shell_quote_path(path: Path) -> bytes:
    raw = str(path).encode("utf-8")
    return b"'" + raw.replace(b"'", b"'\"'\"'") + b"'"


def render_pinned_shim(
    template_raw: bytes,
    interpreter: Path,
    client: Path,
) -> bytes:
    """Render the exact canonical front door from fixed absolute paths."""

    if type(template_raw) is not bytes or template_raw != PINNED_SHIM_TEMPLATE:
        raise DeploymentError("shim template contract mismatch")
    interpreter_path = _normalized_absolute_path(interpreter, "shim interpreter")
    client_path = _normalized_absolute_path(client, "shim client")
    rendered = template_raw.replace(
        b"@TASK_WITNESS_PYTHON@",
        _shell_quote_path(interpreter_path),
    ).replace(
        b"@TASK_WITNESS_CLIENT@",
        _shell_quote_path(client_path),
    )
    if b"@TASK_WITNESS_" in rendered or not rendered.endswith(b"\n"):
        raise DeploymentError("shim placeholder substitution is incomplete")
    return rendered


def _materialized_provider(
    provider: _ParsedProvider,
    declaration_raw: bytes,
    storage_trust_root: Path,
    snapshots: Mapping[str, _FileSnapshot],
    *,
    create: bool = True,
    installed_trust_root: Path | None = None,
) -> ProviderMaterialization:
    trust_fd, storage_trust_path = _open_private_root(
        storage_trust_root,
        create=create,
    )
    installed_trust_path = _projected_trust_root(
        installed_trust_root,
        storage_trust_path,
    )
    validators_fd = -1
    try:
        validators_fd = _open_private_directory(
            trust_fd,
            "validators",
            "retained validators directory",
            create=create,
        )
        storage_validators_path = storage_trust_path / "validators"
        installed_validators_path = installed_trust_path / "validators"
        retained_by_implementation: dict[str, tuple[RetainedModule, ...]] = {}
        validators: list[Mapping[str, Any]] = []
        flattened_modules: list[RetainedModule] = []
        for validator in provider.validators:
            expected = _generation_specs(validator, snapshots)
            retained = retained_by_implementation.get(validator.implementation_sha256)
            if retained is None:
                retain = _materialize_generation if create else _verify_generation
                retained = retain(
                    validators_fd,
                    storage_validators_path,
                    installed_validators_path,
                    validator.implementation_sha256,
                    expected,
                )
                retained_by_implementation[validator.implementation_sha256] = retained
                flattened_modules.extend(retained)
            elif {f"{item.name}.py": item.raw for item in retained} != expected:
                raise DeploymentError(
                    "shared validator implementation has conflicting modules"
                )
            modules = [
                {
                    "name": item.name,
                    "path": str(item.path),
                    "sha256": item.sha256,
                }
                for item in retained
            ]
            validators.append(
                _freeze(
                    {
                        "validator_id": validator.validator_id,
                        "contract": validator.contract,
                        "implementation_sha256": validator.implementation_sha256,
                        "entrypoint": validator.entrypoint,
                        "modules": modules,
                        **validator.lifecycle,
                    }
                )
            )
        return ProviderMaterialization(
            provider.plugin_id,
            provider.publisher,
            provider.repository,
            provider.authority_profile,
            hashlib.sha256(declaration_raw).hexdigest(),
            provider.content_sha256,
            tuple(_freeze(item) for item in provider.producers),
            tuple(_freeze(item) for item in provider.issuers),
            tuple(validators),
            tuple(flattened_modules),
        )
    finally:
        if validators_fd >= 0:
            os.close(validators_fd)
        os.close(trust_fd)


def materialize_provider(
    staged_plugin_root: Path,
    trust_root: Path,
    *,
    installed_trust_root: Path | None = None,
) -> ProviderMaterialization | None:
    """Import one strict plugin declaration into immutable retained storage."""

    plugin_root = _open_root(Path(staged_plugin_root), "staged plugin root")
    declaration: _FileSnapshot | None = None
    module_snapshots: dict[str, _FileSnapshot] = {}
    try:
        declaration = _capture_regular(
            plugin_root,
            (PROVIDER_DECLARATION_NAME,),
            "provider declaration",
            limit=MAX_JSON_BYTES,
            absent=True,
        )
        if declaration is None:
            _recheck_root(plugin_root, "staged plugin root")
            return None
        provider = _parse_provider(declaration.raw)
        for validator in provider.validators:
            for module in validator.modules:
                if module.relative_path in module_snapshots:
                    existing = module_snapshots[module.relative_path]
                    if (
                        len(existing.raw) != module.length
                        or hashlib.sha256(existing.raw).hexdigest() != module.sha256
                    ):
                        raise DeploymentError("provider module path identity conflict")
                    continue
                snapshot = _capture_regular(
                    plugin_root,
                    module.components,
                    f"validator module {module.name}",
                    limit=MAX_MODULE_BYTES,
                )
                if snapshot is None:
                    raise AssertionError("required module capture returned absent")
                module_snapshots[module.relative_path] = snapshot
        result = _materialized_provider(
            provider,
            declaration.raw,
            Path(trust_root),
            module_snapshots,
            installed_trust_root=installed_trust_root,
        )
        for snapshot in module_snapshots.values():
            _recheck_file(snapshot)
        _recheck_file(declaration)
        _recheck_root(plugin_root, "staged plugin root")
        return result
    except DeploymentError:
        raise
    except OSError as error:
        raise DeploymentError("provider materialization failed") from error
    finally:
        for snapshot in module_snapshots.values():
            _close_file(snapshot)
        if declaration is not None:
            _close_file(declaration)
        os.close(plugin_root.fd)


def _intrinsic_smoke_definition(
    raw: bytes,
) -> tuple[_ParsedProvider, bytes]:
    module_sha256 = hashlib.sha256(raw).hexdigest()
    implementation = _validator_implementation_identity(
        SMOKE_BUNDLE_CONTRACT,
        "task-witness-smoke-validator",
        (("task-witness-smoke-validator", module_sha256),),
    )
    lifecycle = {"state": "active", "usable_for_new_publication": True}
    provider = _ParsedProvider(
        "task-witness",
        "nisavid",
        "https://github.com/nisavid/agents",
        "task-witness-smoke",
        _digest(
            {
                "contract": INTRINSIC_SMOKE_PROVIDER_CONTRACT,
                "validator_implementation_sha256": implementation,
            }
        ),
        (
            {
                "producer_id": SMOKE_PRODUCER_NAME,
                "contract": SMOKE_BUNDLE_CONTRACT,
                "implementation_sha256": _digest(
                    {
                        "contract": SMOKE_PRODUCER_IMPLEMENTATION_CONTRACT,
                        "validator_implementation_sha256": implementation,
                    }
                ),
                "validator_id": SMOKE_VALIDATOR_NAME,
                "validator_contract": SMOKE_BUNDLE_CONTRACT,
                "validator_implementation_sha256": implementation,
                **lifecycle,
            },
        ),
        (
            {
                "issuer_id": SMOKE_ISSUER_NAME,
                "contract": SMOKE_ISSUER_CONTRACT,
                "implementation_sha256": _digest(
                    {"contract": SMOKE_ISSUER_IMPLEMENTATION_CONTRACT}
                ),
                "capabilities": ["activation-smoke"],
                **lifecycle,
            },
        ),
        (
            _DeclaredValidator(
                SMOKE_VALIDATOR_NAME,
                SMOKE_BUNDLE_CONTRACT,
                implementation,
                "task-witness-smoke-validator",
                (
                    _DeclaredModule(
                        "task-witness-smoke-validator",
                        "smoke/task_witness_smoke_validator.py",
                        ("smoke", "task_witness_smoke_validator.py"),
                        len(raw),
                        module_sha256,
                    ),
                ),
                lifecycle,
            ),
        ),
    )
    declaration_raw = _canonical_document(
        {
            "contract": INTRINSIC_SMOKE_PROVIDER_CONTRACT,
            "content_sha256": provider.content_sha256,
        }
    )
    return provider, declaration_raw


def _project_provider(
    provider: _ParsedProvider,
    declaration_raw: bytes,
    module_raws: Mapping[str, bytes],
    installed_trust_root: Path,
) -> ProviderMaterialization:
    installed_root = _normalized_absolute_path(
        installed_trust_root,
        "planned installed trust root",
    )
    retained_by_implementation: dict[str, tuple[RetainedModule, ...]] = {}
    validators: list[Mapping[str, Any]] = []
    flattened_modules: list[RetainedModule] = []
    for validator in provider.validators:
        expected: dict[str, bytes] = {}
        for module in validator.modules:
            try:
                raw = module_raws[module.relative_path]
            except KeyError:
                raise DeploymentError(
                    f"planned validator module {module.name} is missing"
                ) from None
            if (
                len(raw) != module.length
                or hashlib.sha256(raw).hexdigest() != module.sha256
            ):
                raise DeploymentError(
                    f"planned validator module {module.name} identity mismatch"
                )
            expected[f"{module.name}.py"] = raw
        retained = retained_by_implementation.get(validator.implementation_sha256)
        if retained is None:
            generation = (
                installed_root
                / "validators"
                / f"sha256-{validator.implementation_sha256}"
            )
            retained = tuple(
                RetainedModule(
                    name.removesuffix(".py"),
                    generation / name,
                    generation / name,
                    raw,
                    hashlib.sha256(raw).hexdigest(),
                )
                for name, raw in expected.items()
            )
            retained_by_implementation[validator.implementation_sha256] = retained
            flattened_modules.extend(retained)
        elif {f"{item.name}.py": item.raw for item in retained} != expected:
            raise DeploymentError(
                "planned shared validator implementation has conflicting modules"
            )
        validators.append(
            _freeze(
                {
                    "validator_id": validator.validator_id,
                    "contract": validator.contract,
                    "implementation_sha256": validator.implementation_sha256,
                    "entrypoint": validator.entrypoint,
                    "modules": [
                        {
                            "name": item.name,
                            "path": str(item.path),
                            "sha256": item.sha256,
                        }
                        for item in retained
                    ],
                    **validator.lifecycle,
                }
            )
        )
    return ProviderMaterialization(
        provider.plugin_id,
        provider.publisher,
        provider.repository,
        provider.authority_profile,
        hashlib.sha256(declaration_raw).hexdigest(),
        provider.content_sha256,
        tuple(_freeze(item) for item in provider.producers),
        tuple(_freeze(item) for item in provider.issuers),
        tuple(validators),
        tuple(flattened_modules),
    )


def _plan_candidate_provider(
    source: CandidateSource,
    installed_trust_root: Path,
) -> ProviderMaterialization | None:
    if source.provider is None:
        return None
    declaration_raw = _candidate_file(
        source.tree,
        PROVIDER_DECLARATION_NAME,
        "the provider declaration",
    )
    module_raws = {
        module.relative_path: _candidate_file(
            source.tree,
            module.relative_path,
            f"validator module {module.name}",
        )
        for validator in source.provider.validators
        for module in validator.modules
    }
    return _project_provider(
        source.provider,
        declaration_raw,
        module_raws,
        installed_trust_root,
    )


def _plan_intrinsic_smoke_provider(
    source: CandidateSource,
    installed_trust_root: Path,
) -> ProviderMaterialization:
    raw = _candidate_file(
        source.tree,
        "smoke/task_witness_smoke_validator.py",
        "the intrinsic smoke validator",
    )
    provider, declaration_raw = _intrinsic_smoke_definition(raw)
    return _project_provider(
        provider,
        declaration_raw,
        {"smoke/task_witness_smoke_validator.py": raw},
        installed_trust_root,
    )


def _smoke_provider(
    trust_root: Path,
    *,
    create: bool,
    installed_trust_root: Path | None = None,
) -> ProviderMaterialization:
    plugin_root_path = Path(os.path.abspath(__file__)).parent.parent
    root = _open_root(plugin_root_path, "Task Witness controller source root")
    snapshot: _FileSnapshot | None = None
    try:
        snapshot = _capture_regular(
            root,
            ("smoke", "task_witness_smoke_validator.py"),
            "Task Witness smoke validator",
            limit=MAX_MODULE_BYTES,
        )
        if snapshot is None:
            raise AssertionError("smoke validator capture returned absent")
        provider, declaration_raw = _intrinsic_smoke_definition(snapshot.raw)
        result = _materialized_provider(
            provider,
            declaration_raw,
            trust_root,
            {"smoke/task_witness_smoke_validator.py": snapshot},
            create=create,
            installed_trust_root=installed_trust_root,
        )
        _recheck_file(snapshot)
        _recheck_root(root, "Task Witness controller source root")
        return result
    finally:
        if snapshot is not None:
            _close_file(snapshot)
        os.close(root.fd)


def materialize_intrinsic_smoke_provider(
    trust_root: Path,
    *,
    installed_trust_root: Path | None = None,
) -> ProviderMaterialization:
    """Retain the controller-owned smoke provider before context composition."""

    return _smoke_provider(
        Path(trust_root),
        create=True,
        installed_trust_root=installed_trust_root,
    )


def _verify_provider_retained(
    provider: ProviderMaterialization,
    trust_root: Path,
    installed_trust_root: Path | None,
) -> None:
    trust_fd, storage_trust_path = _open_private_root(trust_root, create=False)
    installed_trust_path = _projected_trust_root(
        installed_trust_root,
        storage_trust_path,
    )
    validators_fd = -1
    try:
        validators_fd = _open_private_directory(
            trust_fd,
            "validators",
            "retained validators directory",
            create=False,
        )
        modules_by_storage_path = {item.storage_path: item for item in provider.modules}
        if len(modules_by_storage_path) != len(provider.modules):
            raise DeploymentError("provider retained module inventory has duplicates")
        for validator in provider.validators:
            implementation = _sha256(
                validator["implementation_sha256"],
                "materialized validator implementation",
            )
            expected: dict[str, bytes] = {}
            for module in validator["modules"]:
                name = _token(module["name"], "materialized validator module name")
                expected_storage_path = (
                    storage_trust_path
                    / "validators"
                    / f"sha256-{implementation}"
                    / f"{name}.py"
                )
                expected_installed_path = (
                    installed_trust_path
                    / "validators"
                    / f"sha256-{implementation}"
                    / f"{name}.py"
                )
                if module["path"] != str(expected_installed_path):
                    raise DeploymentError("materialized validator path is not retained")
                retained = modules_by_storage_path.get(expected_storage_path)
                if retained is None or retained.sha256 != module["sha256"]:
                    raise DeploymentError(
                        "materialized validator module identity mismatch"
                    )
                if retained.path != expected_installed_path:
                    raise DeploymentError(
                        "materialized validator installed path mismatch"
                    )
                expected[f"{name}.py"] = retained.raw
            _verify_generation(
                validators_fd,
                storage_trust_path / "validators",
                installed_trust_path / "validators",
                implementation,
                expected,
            )
    finally:
        if validators_fd >= 0:
            os.close(validators_fd)
        os.close(trust_fd)


def _identity_key(category: str, item: Mapping[str, Any]) -> tuple[str, str]:
    return (item[f"{category}_id"], item["contract"])


def _compose_trust_context_document(
    providers: Iterable[ProviderMaterialization],
    smoke: ProviderMaterialization,
) -> tuple[dict[str, Any], bytes]:
    ordered = sorted(
        providers,
        key=lambda item: (
            item.plugin_id,
            item.declaration_content_sha256,
            item.declaration_sha256,
        ),
    )
    plugin_ids = [item.plugin_id for item in ordered]
    if len(plugin_ids) != len(set(plugin_ids)):
        raise DeploymentError(
            "trust composition has duplicate or conflicting providers"
        )
    plugin_ids.append(smoke.plugin_id)
    if len(plugin_ids) != len(set(plugin_ids)):
        raise DeploymentError(
            "trust composition has duplicate or conflicting providers"
        )
    categories: dict[str, list[dict[str, Any]]] = {
        "producer": [],
        "issuer": [],
        "validator": [],
    }
    seen: dict[str, dict[tuple[str, str], str]] = {
        "producer": {},
        "issuer": {},
        "validator": {},
    }
    for provider in [*ordered, smoke]:
        for category, category_entries in categories.items():
            values = getattr(provider, f"{category}s")
            for frozen in values:
                item = _thaw(frozen)
                key = _identity_key(category, item)
                implementation = item["implementation_sha256"]
                if key in seen[category]:
                    raise DeploymentError(
                        f"trust composition has a duplicate or conflicting {category} identity"
                    )
                seen[category][key] = implementation
                category_entries.append(item)
    validators = {
        (
            item["validator_id"],
            item["contract"],
            item["implementation_sha256"],
        )
        for item in categories["validator"]
    }
    for producer in categories["producer"]:
        selected = (
            producer["validator_id"],
            producer["validator_contract"],
            producer["validator_implementation_sha256"],
        )
        if selected not in validators:
            raise DeploymentError(
                "provider producer references an unregistered validator"
            )
    for category, values in categories.items():
        values.sort(
            key=lambda item: (
                item[f"{category}_id"],
                item["contract"],
                item["implementation_sha256"],
            )
        )
    return _bounded_trust_context_document(
        {
            "schema_version": SCHEMA_VERSION,
            "contract": TRUST_CONTEXT_CONTRACT,
            "producers": categories["producer"],
            "issuers": categories["issuer"],
            "validators": categories["validator"],
        }
    )


def _bounded_trust_context_document(
    unsigned: Mapping[str, Any],
) -> tuple[dict[str, Any], bytes]:
    value = {**unsigned, "content_sha256": _digest(unsigned)}
    raw = _canonical_document(value)
    if len(raw) > MAX_TRUST_CONTEXT_BYTES:
        raise DeploymentError("trust context exceeds the consumer byte limit")
    return value, raw


def _plan_trust_context(
    source: CandidateSource,
    canonical_root: Path,
) -> PlannedTrust:
    installed_root = (
        _normalized_absolute_path(
            canonical_root,
            "planned canonical root",
        )
        / "trust"
    )
    provider = _plan_candidate_provider(source, installed_root)
    providers = () if provider is None else (provider,)
    smoke = _plan_intrinsic_smoke_provider(source, installed_root)
    value, raw = _compose_trust_context_document(providers, smoke)
    byte_sha256 = hashlib.sha256(raw).hexdigest()
    path = installed_root / "contexts" / f"sha256-{byte_sha256}.json"
    return PlannedTrust(
        providers,
        smoke,
        TrustContextMaterialization(
            path,
            path,
            raw,
            byte_sha256,
            _freeze_json(value),
        ),
    )


def _trust_context(
    providers: Iterable[ProviderMaterialization],
    trust_root: Path,
    *,
    create: bool,
    installed_trust_root: Path | None = None,
) -> TrustContextMaterialization:
    external = list(providers)
    if any(type(item) is not ProviderMaterialization for item in external):
        raise DeploymentError("trust composition received an invalid provider")
    for provider in external:
        _verify_provider_retained(
            provider,
            Path(trust_root),
            installed_trust_root,
        )
    smoke = _smoke_provider(
        Path(trust_root),
        create=False,
        installed_trust_root=installed_trust_root,
    )
    _verify_provider_retained(smoke, Path(trust_root), installed_trust_root)
    value, raw = _compose_trust_context_document(external, smoke)
    byte_sha256 = hashlib.sha256(raw).hexdigest()
    trust_fd, storage_trust_path = _open_private_root(
        Path(trust_root),
        create=create,
    )
    installed_trust_path = _projected_trust_root(
        installed_trust_root,
        storage_trust_path,
    )
    contexts_fd = -1
    try:
        contexts_fd = _open_private_directory(
            trust_fd,
            "contexts",
            "retained contexts directory",
            create=create,
        )
        name = f"sha256-{byte_sha256}.json"
        if create:
            _create_private_file(
                contexts_fd,
                name,
                raw,
                "retained trust context",
            )
            os.fsync(contexts_fd)
        _read_private_file(
            contexts_fd,
            name,
            raw,
            "retained trust context",
        )
        storage_path = storage_trust_path / "contexts" / name
        path = installed_trust_path / "contexts" / name
    finally:
        if contexts_fd >= 0:
            os.close(contexts_fd)
        os.close(trust_fd)
    return TrustContextMaterialization(
        storage_path,
        path,
        raw,
        byte_sha256,
        _freeze_json(value),
    )


def materialize_trust_context(
    providers: Iterable[ProviderMaterialization],
    trust_root: Path,
    *,
    installed_trust_root: Path | None = None,
) -> TrustContextMaterialization:
    """Publish one exact staged context from already-retained providers."""

    return _trust_context(
        providers,
        Path(trust_root),
        create=True,
        installed_trust_root=installed_trust_root,
    )


def compose_trust_context(
    providers: Iterable[ProviderMaterialization],
    trust_root: Path,
    *,
    installed_trust_root: Path | None = None,
) -> TrustContextMaterialization:
    """Verify retained providers and their already-published exact context."""

    return _trust_context(
        providers,
        Path(trust_root),
        create=False,
        installed_trust_root=installed_trust_root,
    )
