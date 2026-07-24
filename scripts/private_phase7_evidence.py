#!/usr/bin/env python3
"""Verify public-safe Phase 7 v2 replay evidence."""

from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import stat
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

from evidence_transport import (
    candidate_content_identity,
    canonical_bytes,
    digest_bytes,
    json_file_bytes,
    strict_json_bytes,
)
from phase7_compatibility_projection import compatibility_bytes

PUBLIC_REPLAY_SUMMARY_CONTRACT = "phase7-private-replay-public-summary-v2"
PUBLIC_REPLAY_SUMMARY_SCHEMA_VERSION = 2
COMPLETE_TREE_WITNESS_CONTRACT = "phase7-private-complete-tree-witness-v1"
PRODUCER_REGISTRY_CONTRACT = "phase7-private-producer-registry-v2"
MAX_PUBLIC_SUMMARY_BYTES = 1024 * 1024
MAX_COMPATIBILITY_BYTES = 512 * 1024
MAX_REGISTRY_BYTES = 2 * 1024 * 1024
MAX_WITNESS_BYTES = 32 * 1024 * 1024
MAX_WITNESS_MEMBERS = 4096
MAX_WITNESS_MEMBER_BYTES = 32 * 1024 * 1024
SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
OID_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
PRIVATE_CANDIDATE_PATTERN = re.compile(r"[0-9a-f]{64}")
CHECK_ORDER = (
    "agent-topology",
    "global-agents-policy",
    "codex-private-config",
    "claude-settings",
    "hook-retirement",
    "plugin-deployment",
    "review-atlas-overlay",
)
ROLE_ORDER = (
    "candidate-snapshot",
    "semantic-inputs",
    "runtime-inputs",
    "test-inputs",
    "exported-schema",
    "legacy-retirement",
    "verification-results",
)
SUMMARY_FIELDS = {
    "schema_version",
    "contract",
    "private_commit_oid",
    "private_candidate_identity",
    "producer_package_sha256",
    "producer_registry_sha256",
    "producer_witness_sha256",
    "private_receipt_sha256",
    "private_trust_anchor_sha256",
    "frozen_identity_sha256",
    "private_evidence_bundle_sha256",
    "compatibility_bytes_base64",
    "compatibility_sha256",
    "checks",
    "role_payloads",
    "runtime_isolation",
    "conformance",
    "summary_sha256",
}
SUMMARY_DIGEST_FIELDS = (
    "producer_package_sha256",
    "producer_registry_sha256",
    "producer_witness_sha256",
    "private_receipt_sha256",
    "private_trust_anchor_sha256",
    "frozen_identity_sha256",
    "private_evidence_bundle_sha256",
    "compatibility_sha256",
    "summary_sha256",
)
RUNTIME_ISOLATION_FIELDS = {
    "backend",
    "policy_sha256",
    "capability_manifest_sha256",
    "host_identity_sha256",
    "kernel_identity_sha256",
    "conformance_sha256",
}
RUNTIME_DIGEST_FIELDS = (
    "policy_sha256",
    "capability_manifest_sha256",
    "host_identity_sha256",
    "kernel_identity_sha256",
    "conformance_sha256",
)
COMPOSED_RECEIPT_FIELDS = {
    "schema_version",
    "contract",
    "claim",
    "public_candidate_identity",
    "private_commit_oid",
    "private_candidate_identity",
    "producer_package_sha256",
    "producer_registry_sha256",
    "producer_witness_sha256",
    "private_receipt_sha256",
    "private_trust_anchor_sha256",
    "frozen_identity_sha256",
    "private_evidence_bundle_sha256",
    "private_replay_payload_sha256",
    "private_replay_summary_sha256",
    "compatibility_sha256",
    "checks",
    "role_payloads",
    "runtime_isolation",
    "conformance",
    "records",
    "passed",
    "receipt_sha256",
}
PUBLIC_CONFORMANCE_FIELDS = {
    "sha256",
    "sealed_root_count",
    "read_count",
    "probe_count",
}


class PrivateEvidenceError(RuntimeError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PrivateEvidenceError(message)


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None


def _exact_object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    _require(
        isinstance(value, dict) and set(value) == fields,
        f"{label} schema drift",
    )
    return value


def _validate_checks(value: Any) -> None:
    _require(
        isinstance(value, list) and len(value) == len(CHECK_ORDER),
        "private replay check inventory drift",
    )
    for index, expected_identifier in enumerate(CHECK_ORDER):
        check = _exact_object(
            value[index],
            {
                "id",
                "registry_entry_sha256",
                "test_inventory_sha256",
                "exit_code",
                "terminal",
            },
            "private replay check",
        )
        _require(
            check["id"] == expected_identifier
            and _is_digest(check["registry_entry_sha256"])
            and _is_digest(check["test_inventory_sha256"])
            and type(check["exit_code"]) is int
            and check["exit_code"] == 0
            and check["terminal"] == "passed",
            "private replay check is not terminally passed",
        )


def _validate_roles(value: Any) -> None:
    _require(
        isinstance(value, list) and len(value) == len(ROLE_ORDER),
        "private replay role inventory drift",
    )
    for index, expected_role in enumerate(ROLE_ORDER):
        role = _exact_object(
            value[index],
            {"role", "sha256"},
            "private replay role",
        )
        _require(
            role["role"] == expected_role and _is_digest(role["sha256"]),
            "private replay role digest drift",
        )


def _validate_runtime_isolation(value: Any) -> None:
    runtime = _exact_object(
        value,
        RUNTIME_ISOLATION_FIELDS,
        "private replay runtime/isolation identity",
    )
    backend = _exact_object(
        runtime["backend"],
        {"target", "binary", "version", "sha256", "version_sha256"},
        "private replay isolation backend",
    )
    version = backend["version"]
    _require(
        (backend["target"], backend["binary"])
        in {
            ("macos-seatbelt", "sandbox-exec"),
            ("linux-bubblewrap", "bwrap"),
            ("wsl2-bubblewrap", "bwrap"),
        }
        and isinstance(version, str)
        and 0 < len(version) <= 256
        and version.strip() == version
        and "/" not in version
        and "\\" not in version
        and all(32 <= ord(character) < 127 for character in version)
        and _is_digest(backend["sha256"])
        and _is_digest(backend["version_sha256"]),
        "private replay isolation backend identity drift",
    )
    for field in RUNTIME_DIGEST_FIELDS:
        _require(
            _is_digest(runtime[field]),
            f"private replay runtime/isolation {field} is malformed",
        )


def _validate_public_conformance(value: Any, runtime: dict[str, Any]) -> None:
    conformance = _exact_object(
        value, PUBLIC_CONFORMANCE_FIELDS, "composed public conformance"
    )
    _require(
        _is_digest(conformance["sha256"])
        and digest_bytes(canonical_bytes(conformance)) == runtime["conformance_sha256"]
        and all(
            type(conformance[field]) is int and conformance[field] > 0
            for field in ("sealed_root_count", "read_count", "probe_count")
        )
        and conformance["read_count"] == conformance["probe_count"],
        "composed public conformance drift",
    )


def _validate_backend_contracts(value: Any) -> list[dict[str, Any]]:
    _require(
        isinstance(value, list) and len(value) == 3,
        "private producer backend contract inventory drift",
    )
    expected = (
        ("macos-seatbelt", "sandbox-exec", "direct-required", False),
        ("linux-bubblewrap", "bwrap", "external-release-required", True),
        ("wsl2-bubblewrap", "bwrap", "external-release-required", True),
    )
    contracts: list[dict[str, Any]] = []
    for (target, binary, execution, evidence_required), contract in zip(
        expected, value
    ):
        fields = {
            "target",
            "binary",
            "version",
            "capabilities",
            "execution",
            "release_evidence_required",
        }
        if not evidence_required:
            fields.add("sha256")
        contract = _exact_object(
            contract,
            fields,
            "private producer backend contract",
        )
        _require(
            contract["target"] == target
            and contract["binary"] == binary
            and isinstance(contract["version"], str)
            and contract["version"].strip() == contract["version"]
            and contract["version"]
            and isinstance(contract["capabilities"], list)
            and contract["capabilities"]
            and all(
                isinstance(capability, str) and capability
                for capability in contract["capabilities"]
            )
            and len(set(contract["capabilities"])) == len(contract["capabilities"])
            and contract["execution"] == execution
            and contract["release_evidence_required"] is evidence_required
            and (
                evidence_required
                or contract["sha256"]
                == "sha256:8290e4be7387a0df83cd1559e86afd880464f269450573d012795761fe298f16"
            ),
            "private producer backend contract drift",
        )
        contracts.append(contract)
    return contracts


def _git_oid(object_format: str, object_type: str, content: bytes) -> str:
    _require(object_format in {"sha1", "sha256"}, "Git object format drift")
    digest = hashlib.new(object_format)
    digest.update(f"{object_type} {len(content)}\0".encode("ascii"))
    digest.update(content)
    return digest.hexdigest()


def _tree_entries(
    content: bytes,
    *,
    object_format: str,
) -> list[tuple[str, bytes, str]]:
    oid_size = 20 if object_format == "sha1" else 32
    entries: list[tuple[str, bytes, str]] = []
    names: set[bytes] = set()
    offset = 0
    while offset < len(content):
        mode_end = content.find(b" ", offset)
        name_end = content.find(b"\0", mode_end + 1)
        _require(
            mode_end > offset and name_end > mode_end + 1,
            "complete-tree witness contains a malformed tree",
        )
        mode_bytes = content[offset:mode_end]
        name = content[mode_end + 1 : name_end]
        oid_end = name_end + 1 + oid_size
        _require(
            oid_end <= len(content),
            "complete-tree witness contains a malformed tree",
        )
        try:
            mode = mode_bytes.decode("ascii")
        except UnicodeDecodeError as error:
            raise PrivateEvidenceError(
                "complete-tree witness contains a malformed mode"
            ) from error
        _require(
            mode in {"40000", "100644", "100755", "120000", "160000"}
            and name not in {b"", b".", b".."}
            and b"/" not in name
            and name not in names,
            "complete-tree witness contains an unsafe tree entry",
        )
        names.add(name)
        entries.append(
            (
                mode,
                name,
                content[name_end + 1 : oid_end].hex(),
            )
        )
        offset = oid_end
    _require(
        offset == len(content),
        "complete-tree witness contains a malformed tree",
    )
    ordering_keys = [
        name + (b"/" if mode == "40000" else b"") for mode, name, _oid in entries
    ]
    _require(
        ordering_keys == sorted(ordering_keys),
        "complete-tree witness tree entries are not canonically ordered",
    )
    return entries


def _commit_root_tree(content: bytes, *, object_format: str) -> str:
    headers, separator, _message = content.partition(b"\n\n")
    lines = headers.splitlines()
    tree_lines = [line for line in lines if line.startswith(b"tree ")]
    _require(
        separator == b"\n\n"
        and lines
        and len(tree_lines) == 1
        and tree_lines[0] == lines[0],
        "complete-tree witness commit is malformed",
    )
    try:
        oid = tree_lines[0][5:].decode("ascii")
    except UnicodeDecodeError as error:
        raise PrivateEvidenceError(
            "complete-tree witness commit root is malformed"
        ) from error
    expected_length = 40 if object_format == "sha1" else 64
    _require(
        len(oid) == expected_length
        and all(character in "0123456789abcdef" for character in oid),
        "complete-tree witness commit root is malformed",
    )
    return oid


def _deterministic_tar_bytes(members: list[tuple[str, bytes]]) -> bytes:
    output = io.BytesIO()
    try:
        with tarfile.open(
            fileobj=output,
            mode="w",
            format=tarfile.USTAR_FORMAT,
        ) as archive:
            for name, content in members:
                member = tarfile.TarInfo(name)
                member.size = len(content)
                member.mode = 0o600
                member.mtime = 0
                member.uid = 0
                member.gid = 0
                member.uname = ""
                member.gname = ""
                archive.addfile(member, io.BytesIO(content))
    except (OSError, tarfile.TarError, ValueError) as error:
        raise PrivateEvidenceError(
            "complete-tree witness cannot be reconstructed"
        ) from error
    return output.getvalue()


def _tar_members(content: bytes) -> list[tuple[str, bytes]]:
    payloads: list[tuple[str, bytes]] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:") as archive:
            members = archive.getmembers()
            _require(
                0 < len(members) <= MAX_WITNESS_MEMBERS,
                "complete-tree witness member inventory drift",
            )
            seen: set[str] = set()
            total_size = 0
            for member in members:
                relative = PurePosixPath(member.name)
                _require(
                    member.isfile()
                    and member.type == tarfile.REGTYPE
                    and not relative.is_absolute()
                    and relative.as_posix() == member.name
                    and all(part not in {"", ".", ".."} for part in relative.parts)
                    and member.name not in seen
                    and member.mode == 0o600
                    and member.mtime == 0
                    and member.uid == 0
                    and member.gid == 0
                    and member.uname == ""
                    and member.gname == ""
                    and 0 <= member.size <= MAX_WITNESS_MEMBER_BYTES,
                    "complete-tree witness member metadata drift",
                )
                seen.add(member.name)
                total_size += member.size
                _require(
                    total_size <= MAX_WITNESS_BYTES,
                    "complete-tree witness members exceed the size limit",
                )
                stream = archive.extractfile(member)
                _require(
                    stream is not None,
                    "complete-tree witness member is unreadable",
                )
                payload = stream.read()
                _require(
                    len(payload) == member.size,
                    "complete-tree witness member size drift",
                )
                payloads.append((member.name, payload))
    except (OSError, tarfile.TarError) as error:
        raise PrivateEvidenceError(
            "complete-tree witness is not a valid USTAR archive"
        ) from error
    _require(
        content == _deterministic_tar_bytes(payloads),
        "complete-tree witness is not canonical deterministic USTAR",
    )
    return payloads


def _validate_registry(
    registry: Any,
    *,
    expected_producer_package_sha256: str,
) -> dict[str, Any]:
    registry = _exact_object(
        registry,
        {
            "schema_version",
            "contract",
            "roles",
            "checks",
            "package_members",
            "role_selectors",
            "compatibility_module",
            "backend_contracts",
        },
        "private producer registry",
    )
    _require(
        type(registry["schema_version"]) is int
        and registry["schema_version"] == 2
        and registry["contract"] == PRODUCER_REGISTRY_CONTRACT
        and registry["roles"] == list(ROLE_ORDER),
        "private producer registry contract drift",
    )
    checks = registry["checks"]
    _require(
        isinstance(checks, list) and len(checks) == len(CHECK_ORDER),
        "private producer registry check inventory drift",
    )
    for index, identifier in enumerate(CHECK_ORDER):
        check = _exact_object(
            checks[index],
            {"id", "tests", "registry_entry_sha256"},
            "private producer registry check",
        )
        tests = check["tests"]
        unsigned = {"id": check["id"], "tests": tests}
        _require(
            check["id"] == identifier
            and isinstance(tests, list)
            and 0 < len(tests) <= 256
            and len(set(tests)) == len(tests)
            and all(isinstance(test, str) and 0 < len(test) <= 512 for test in tests)
            and check["registry_entry_sha256"]
            == digest_bytes(canonical_bytes(unsigned)),
            "private producer registry check drift",
        )
    members = registry["package_members"]
    _require(
        isinstance(members, list) and 0 < len(members) <= 64,
        "private producer package inventory drift",
    )
    observed_paths: set[str] = set()
    for member in members:
        member = _exact_object(
            member,
            {"path", "mode", "blob_oid", "size", "sha256"},
            "private producer package member",
        )
        path = member["path"]
        relative = PurePosixPath(path) if isinstance(path, str) else None
        _require(
            relative is not None
            and not relative.is_absolute()
            and relative.as_posix() == path
            and all(part not in {"", ".", ".."} for part in relative.parts)
            and path not in observed_paths
            and member["mode"] in {"100644", "100755"}
            and isinstance(member["blob_oid"], str)
            and OID_PATTERN.fullmatch(member["blob_oid"]) is not None
            and type(member["size"]) is int
            and 0 <= member["size"] <= MAX_WITNESS_MEMBER_BYTES
            and _is_digest(member["sha256"]),
            "private producer package member drift",
        )
        observed_paths.add(path)
    _require(
        expected_producer_package_sha256 == digest_bytes(canonical_bytes(members)),
        "reviewed producer-package digest mismatch",
    )
    selectors = _exact_object(
        registry["role_selectors"],
        set(ROLE_ORDER),
        "private producer role selectors",
    )
    for role in ROLE_ORDER:
        selector = _exact_object(
            selectors[role],
            {"role", "selector"},
            "private producer role selector",
        )
        _require(
            selector == {"role": role, "selector": "phase7-private-evidence-v4"},
            "private producer role selector drift",
        )
    _require(
        _exact_object(
            registry["compatibility_module"],
            {"path", "expected_projection_sha256"},
            "private producer compatibility module",
        )
        == {
            "path": "scripts/phase7_compatibility_projection.py",
            "expected_projection_sha256": "sha256:a62f152451781b7018180cb4e5ae0bb13071f3dd1364d4a93dbadbe2bb985f58",
        },
        "private producer compatibility module drift",
    )
    registry["backend_contracts"] = _validate_backend_contracts(
        registry["backend_contracts"]
    )
    return registry


def _validate_witness(
    content: bytes,
    *,
    expected_commit_oid: str,
    expected_registry_sha256: str,
    expected_producer_package_sha256: str,
    package_members: list[dict[str, Any]],
) -> None:
    members = _tar_members(content)
    _require(
        members[0][0] == "manifest.json",
        "complete-tree witness manifest is absent",
    )
    manifest = strict_json_bytes(
        members[0][1],
        label="complete-tree witness manifest",
        error_factory=PrivateEvidenceError,
    )
    manifest = _exact_object(
        manifest,
        {
            "schema_version",
            "contract",
            "commit_oid",
            "root_tree_oid",
            "registry_sha256",
            "producer_package_sha256",
            "trees",
        },
        "complete-tree witness manifest",
    )
    _require(
        type(manifest["schema_version"]) is int
        and manifest["schema_version"] == 1
        and manifest["contract"] == COMPLETE_TREE_WITNESS_CONTRACT
        and manifest["commit_oid"] == expected_commit_oid
        and manifest["registry_sha256"] == expected_registry_sha256
        and manifest["producer_package_sha256"] == expected_producer_package_sha256
        and members[0][1] == json_file_bytes(manifest),
        "complete-tree witness manifest drift",
    )
    object_format = "sha1" if len(expected_commit_oid) == 40 else "sha256"
    trees = manifest["trees"]
    _require(
        isinstance(trees, list) and trees,
        "complete-tree witness tree inventory drift",
    )
    tree_oids: list[str] = []
    for tree in trees:
        tree = _exact_object(
            tree,
            {"oid", "sha256"},
            "complete-tree witness tree record",
        )
        _require(
            isinstance(tree["oid"], str)
            and len(tree["oid"]) == len(expected_commit_oid)
            and all(character in "0123456789abcdef" for character in tree["oid"])
            and tree["oid"] not in tree_oids
            and _is_digest(tree["sha256"]),
            "complete-tree witness tree record drift",
        )
        tree_oids.append(tree["oid"])
    expected_names = [
        "manifest.json",
        f"objects/{expected_commit_oid}",
        *[f"objects/{oid}" for oid in tree_oids],
    ]
    _require(
        [name for name, _payload in members] == expected_names,
        "complete-tree witness object inventory drift",
    )
    commit_content = members[1][1]
    _require(
        _git_oid(object_format, "commit", commit_content) == expected_commit_oid,
        "complete-tree witness commit identity drift",
    )
    root_tree_oid = _commit_root_tree(
        commit_content,
        object_format=object_format,
    )
    _require(
        root_tree_oid == manifest["root_tree_oid"] and tree_oids[0] == root_tree_oid,
        "complete-tree witness root tree drift",
    )
    tree_contents = {oid: members[index + 2][1] for index, oid in enumerate(tree_oids)}
    for index, oid in enumerate(tree_oids):
        content_record = tree_contents[oid]
        _require(
            _git_oid(object_format, "tree", content_record) == oid
            and digest_bytes(content_record) == trees[index]["sha256"],
            "complete-tree witness tree identity drift",
        )
    reachable_order: list[str] = []
    path_entries: dict[str, tuple[str, str]] = {}

    def visit(tree_oid: str, prefix: str) -> None:
        _require(
            tree_oid in tree_contents,
            "complete-tree witness omits a reachable tree",
        )
        if tree_oid not in reachable_order:
            reachable_order.append(tree_oid)
        for mode, raw_name, oid in _tree_entries(
            tree_contents[tree_oid],
            object_format=object_format,
        ):
            try:
                name = raw_name.decode("utf-8", errors="strict")
            except UnicodeDecodeError as error:
                raise PrivateEvidenceError(
                    "complete-tree witness path is not UTF-8"
                ) from error
            _require(
                name
                and "\\" not in name
                and all(ord(character) >= 32 for character in name),
                "complete-tree witness path is unsafe",
            )
            path = f"{prefix}/{name}" if prefix else name
            path_entries[path] = (mode, oid)
            if mode == "40000":
                visit(oid, path)

    visit(root_tree_oid, "")
    _require(
        reachable_order == tree_oids,
        "complete-tree witness has omitted, extraneous, or unordered trees",
    )
    for package_member in package_members:
        _require(
            path_entries.get(package_member["path"])
            == (package_member["mode"], package_member["blob_oid"]),
            "complete-tree witness producer-package path binding drift",
        )


def _validate_summary_registry_binding(
    summary: dict[str, Any],
    registry: dict[str, Any],
) -> None:
    for index, identifier in enumerate(CHECK_ORDER):
        summary_check = summary["checks"][index]
        registry_check = registry["checks"][index]
        _require(
            summary_check["id"] == identifier
            and summary_check["registry_entry_sha256"]
            == registry_check["registry_entry_sha256"]
            and summary_check["test_inventory_sha256"]
            == digest_bytes(canonical_bytes(registry_check["tests"])),
            "private replay check does not match the producer registry",
        )
    backend = summary["runtime_isolation"]["backend"]
    matching = [
        contract
        for contract in registry["backend_contracts"]
        if contract["target"] == backend["target"]
    ]
    _require(
        len(matching) == 1
        and backend["binary"] == matching[0]["binary"]
        and backend["version"] == matching[0]["version"]
        and (
            matching[0]["execution"] == "external-release-required"
            or backend["sha256"] == matching[0]["sha256"]
        ),
        "private replay backend is not independently pinned",
    )


def _reconstructed_frozen_identity_sha256(summary: dict[str, Any]) -> str:
    return _frozen_identity_sha256(
        commit_oid=summary["private_commit_oid"],
        producer_package_sha256=summary["producer_package_sha256"],
        private_candidate_identity=summary["private_candidate_identity"],
        private_receipt_sha256=summary["private_receipt_sha256"],
        producer_registry_sha256=summary["producer_registry_sha256"],
        producer_witness_sha256=summary["producer_witness_sha256"],
        trust_anchor_sha256=summary["private_trust_anchor_sha256"],
        evidence_bundle_sha256=summary["private_evidence_bundle_sha256"],
        replay_payload_sha256=replay_payload_sha256(summary),
    )


def replay_payload_sha256(summary: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in summary.items()
        if key not in {"frozen_identity_sha256", "summary_sha256"}
    }
    return digest_bytes(canonical_bytes(payload))


def _composed_replay_summary(
    composed_receipt: dict[str, Any],
    *,
    public_compatibility: bytes,
) -> dict[str, Any]:
    return {
        "schema_version": PUBLIC_REPLAY_SUMMARY_SCHEMA_VERSION,
        "contract": PUBLIC_REPLAY_SUMMARY_CONTRACT,
        "private_commit_oid": composed_receipt["private_commit_oid"],
        "private_candidate_identity": composed_receipt["private_candidate_identity"],
        "producer_package_sha256": composed_receipt["producer_package_sha256"],
        "producer_registry_sha256": composed_receipt["producer_registry_sha256"],
        "producer_witness_sha256": composed_receipt["producer_witness_sha256"],
        "private_receipt_sha256": composed_receipt["private_receipt_sha256"],
        "private_trust_anchor_sha256": composed_receipt["private_trust_anchor_sha256"],
        "frozen_identity_sha256": composed_receipt["frozen_identity_sha256"],
        "private_evidence_bundle_sha256": composed_receipt[
            "private_evidence_bundle_sha256"
        ],
        "compatibility_bytes_base64": base64.b64encode(public_compatibility).decode(
            "ascii"
        ),
        "compatibility_sha256": composed_receipt["compatibility_sha256"],
        "checks": composed_receipt["checks"],
        "role_payloads": composed_receipt["role_payloads"],
        "runtime_isolation": composed_receipt["runtime_isolation"],
        "conformance": composed_receipt["conformance"],
    }


def validate_composed_replay_binding(
    composed_receipt: dict[str, Any],
    *,
    public_compatibility: bytes,
) -> None:
    summary = _composed_replay_summary(
        composed_receipt,
        public_compatibility=public_compatibility,
    )
    reconstructed_payload_sha256 = replay_payload_sha256(summary)
    _require(
        composed_receipt["private_replay_payload_sha256"]
        == reconstructed_payload_sha256,
        "private replay payload binding reconstruction mismatch",
    )
    _require(
        composed_receipt["private_replay_summary_sha256"]
        == digest_bytes(canonical_bytes(summary)),
        "private replay summary binding reconstruction mismatch",
    )
    _require(
        composed_receipt["frozen_identity_sha256"]
        == _frozen_identity_sha256(
            commit_oid=composed_receipt["private_commit_oid"],
            producer_package_sha256=composed_receipt["producer_package_sha256"],
            private_candidate_identity=composed_receipt["private_candidate_identity"],
            private_receipt_sha256=composed_receipt["private_receipt_sha256"],
            producer_registry_sha256=composed_receipt["producer_registry_sha256"],
            producer_witness_sha256=composed_receipt["producer_witness_sha256"],
            trust_anchor_sha256=composed_receipt["private_trust_anchor_sha256"],
            evidence_bundle_sha256=composed_receipt["private_evidence_bundle_sha256"],
            replay_payload_sha256=reconstructed_payload_sha256,
        ),
        "coordinator-frozen identity reconstruction mismatch",
    )


def _frozen_identity_sha256(
    *,
    commit_oid: str,
    producer_package_sha256: str,
    private_candidate_identity: str,
    private_receipt_sha256: str,
    producer_registry_sha256: str,
    producer_witness_sha256: str,
    trust_anchor_sha256: str,
    evidence_bundle_sha256: str,
    replay_payload_sha256: str,
) -> str:
    frozen = {
        "schema_version": 4,
        "contract": "phase7-coordinator-frozen-identity-v4",
        "commit_oid": commit_oid,
        "producer_package_sha256": producer_package_sha256,
        "private_candidate_identity": private_candidate_identity,
        "private_receipt_sha256": private_receipt_sha256,
        "producer_registry_sha256": producer_registry_sha256,
        "producer_witness_sha256": producer_witness_sha256,
        "trust_anchor_sha256": trust_anchor_sha256,
        "evidence_bundle_sha256": evidence_bundle_sha256,
        "replay_payload_sha256": replay_payload_sha256,
    }
    frozen["content_sha256"] = digest_bytes(canonical_bytes(frozen))
    return digest_bytes(json_file_bytes(frozen))


def validate_public_replay_summary(content: bytes) -> dict[str, Any]:
    """Validate one canonical, bounded, public-safe replay summary."""

    _require(
        isinstance(content, bytes) and 0 < len(content) <= MAX_PUBLIC_SUMMARY_BYTES,
        "private replay public summary has an invalid size",
    )
    summary = strict_json_bytes(
        content,
        label="private replay public summary",
        error_factory=PrivateEvidenceError,
    )
    summary = _exact_object(
        summary,
        SUMMARY_FIELDS,
        "private replay public summary",
    )
    _require(
        type(summary["schema_version"]) is int
        and summary["schema_version"] == PUBLIC_REPLAY_SUMMARY_SCHEMA_VERSION
        and summary["contract"] == PUBLIC_REPLAY_SUMMARY_CONTRACT,
        "private replay public summary contract drift",
    )
    _require(
        isinstance(summary["private_commit_oid"], str)
        and OID_PATTERN.fullmatch(summary["private_commit_oid"]) is not None,
        "private replay commit identity is malformed",
    )
    _require(
        isinstance(summary["private_candidate_identity"], str)
        and PRIVATE_CANDIDATE_PATTERN.fullmatch(summary["private_candidate_identity"])
        is not None,
        "private replay candidate identity is malformed",
    )
    for field in SUMMARY_DIGEST_FIELDS:
        _require(
            _is_digest(summary[field]),
            f"private replay public summary {field} is malformed",
        )
    encoded = summary["compatibility_bytes_base64"]
    _require(
        isinstance(encoded, str)
        and 0 < len(encoded) <= 4 * MAX_COMPATIBILITY_BYTES // 3 + 4
        and encoded.isascii(),
        "private replay compatibility bytes are malformed",
    )
    try:
        compatibility = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise PrivateEvidenceError(
            "private replay compatibility bytes are malformed"
        ) from error
    _require(
        0 < len(compatibility) <= MAX_COMPATIBILITY_BYTES
        and base64.b64encode(compatibility).decode("ascii") == encoded
        and digest_bytes(compatibility) == summary["compatibility_sha256"],
        "private replay compatibility byte binding mismatch",
    )
    _validate_checks(summary["checks"])
    _validate_roles(summary["role_payloads"])
    _validate_runtime_isolation(summary["runtime_isolation"])
    _validate_public_conformance(summary["conformance"], summary["runtime_isolation"])
    unsigned = {key: value for key, value in summary.items() if key != "summary_sha256"}
    _require(
        summary["summary_sha256"] == digest_bytes(canonical_bytes(unsigned)),
        "private replay public summary self-digest mismatch",
    )
    _require(
        content == json_file_bytes(summary),
        "private replay public summary is not canonical",
    )
    return summary


def _read_external_regular(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
) -> bytes:
    _require(path.is_absolute(), f"{label} path must be absolute")
    try:
        metadata = path.lstat()
    except OSError as error:
        raise PrivateEvidenceError(f"{label} is unavailable") from error
    _require(
        stat.S_ISREG(metadata.st_mode)
        and not path.is_symlink()
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and metadata.st_nlink == 1
        and 0 < metadata.st_size <= maximum_bytes,
        f"{label} must be a bounded mode-0600 regular file",
    )
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise PrivateEvidenceError(f"{label} is unavailable") from error
    try:
        chunks: list[bytes] = []
        observed_bytes = 0
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            chunks.append(chunk)
            observed_bytes += len(chunk)
            _require(
                observed_bytes <= maximum_bytes,
                f"{label} exceeds its size limit",
            )
        content = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    _require(
        len(content) == metadata.st_size
        and after.st_dev == metadata.st_dev
        and after.st_ino == metadata.st_ino
        and after.st_size == metadata.st_size,
        f"{label} changed while it was read",
    )
    return content


def verify_private_evidence(
    *,
    replay_summary_path: Path,
    producer_witness_path: Path,
    producer_registry_path: Path,
    expected_frozen_identity_sha256: str,
    expected_commit_oid: str,
    expected_producer_package_sha256: str,
) -> dict[str, Any]:
    """Verify the public-safe v4 replay boundary and independent roots."""

    summary = validate_public_replay_summary(
        _read_external_regular(
            replay_summary_path,
            label="private replay public summary",
            maximum_bytes=MAX_PUBLIC_SUMMARY_BYTES,
        )
    )
    registry_content = _read_external_regular(
        producer_registry_path,
        label="private producer registry",
        maximum_bytes=MAX_REGISTRY_BYTES,
    )
    registry = strict_json_bytes(
        registry_content,
        label="private producer registry",
        error_factory=PrivateEvidenceError,
    )
    _require(
        isinstance(registry, dict) and registry_content == json_file_bytes(registry),
        "private producer registry is not canonical",
    )
    registry = _validate_registry(
        registry,
        expected_producer_package_sha256=expected_producer_package_sha256,
    )
    witness_content = _read_external_regular(
        producer_witness_path,
        label="private complete-tree witness",
        maximum_bytes=MAX_WITNESS_BYTES,
    )
    _require(
        summary["producer_registry_sha256"] == digest_bytes(registry_content)
        and summary["producer_witness_sha256"] == digest_bytes(witness_content),
        "private producer registry/witness digest mismatch",
    )
    _validate_summary_registry_binding(summary, registry)
    _require(
        summary["compatibility_sha256"]
        == registry["compatibility_module"]["expected_projection_sha256"],
        "private compatibility projection digest mismatch",
    )
    _validate_witness(
        witness_content,
        expected_commit_oid=expected_commit_oid,
        expected_registry_sha256=summary["producer_registry_sha256"],
        expected_producer_package_sha256=expected_producer_package_sha256,
        package_members=registry["package_members"],
    )
    _require(
        _is_digest(expected_frozen_identity_sha256)
        and summary["frozen_identity_sha256"] == expected_frozen_identity_sha256,
        "coordinator-frozen identity digest mismatch",
    )
    _require(
        _reconstructed_frozen_identity_sha256(summary)
        == expected_frozen_identity_sha256,
        "coordinator-frozen identity reconstruction mismatch",
    )
    _require(
        isinstance(expected_commit_oid, str)
        and OID_PATTERN.fullmatch(expected_commit_oid) is not None
        and summary["private_commit_oid"] == expected_commit_oid,
        "private commit identity mismatch",
    )
    _require(
        _is_digest(expected_producer_package_sha256)
        and summary["producer_package_sha256"] == expected_producer_package_sha256,
        "reviewed producer-package digest mismatch",
    )
    return summary


def verify_public_release_evidence(
    *,
    composed_receipt: dict[str, Any],
    producer_witness_path: Path,
    producer_registry_path: Path,
    expected_frozen_identity_sha256: str,
    expected_commit_oid: str,
    expected_producer_package_sha256: str,
    public_root: Path,
    expected_public_candidate_sha256: str,
) -> dict[str, Any]:
    """Verify composed v7 evidence without private replay artifacts or host equality."""

    composed_receipt = _exact_object(
        composed_receipt,
        COMPOSED_RECEIPT_FIELDS,
        "composed receipt",
    )
    _require(
        type(composed_receipt["schema_version"]) is int
        and composed_receipt["schema_version"] == 7
        and composed_receipt["contract"] == "phase7-composed-evidence-v7"
        and composed_receipt["claim"]
        == (
            "provider-free public composition with replay-payload-bound "
            "frozen private evidence"
        )
        and composed_receipt["passed"] is True,
        "composed receipt contract drift",
    )
    _validate_checks(composed_receipt["checks"])
    _validate_roles(composed_receipt["role_payloads"])
    _validate_runtime_isolation(composed_receipt["runtime_isolation"])
    _validate_public_conformance(
        composed_receipt["conformance"], composed_receipt["runtime_isolation"]
    )
    unsigned_receipt = {
        key: value for key, value in composed_receipt.items() if key != "receipt_sha256"
    }
    _require(
        _is_digest(composed_receipt["receipt_sha256"])
        and composed_receipt["receipt_sha256"]
        == digest_bytes(canonical_bytes(unsigned_receipt)),
        "composed receipt self-digest mismatch",
    )
    public_compatibility = compatibility_bytes(public_root)
    validate_composed_replay_binding(
        composed_receipt,
        public_compatibility=public_compatibility,
    )
    registry_content = _read_external_regular(
        producer_registry_path,
        label="private producer registry",
        maximum_bytes=MAX_REGISTRY_BYTES,
    )
    registry = strict_json_bytes(
        registry_content,
        label="private producer registry",
        error_factory=PrivateEvidenceError,
    )
    _require(
        isinstance(registry, dict) and registry_content == json_file_bytes(registry),
        "private producer registry is not canonical",
    )
    registry = _validate_registry(
        registry,
        expected_producer_package_sha256=expected_producer_package_sha256,
    )
    witness_content = _read_external_regular(
        producer_witness_path,
        label="private complete-tree witness",
        maximum_bytes=MAX_WITNESS_BYTES,
    )
    _require(
        _is_digest(expected_frozen_identity_sha256)
        and composed_receipt.get("frozen_identity_sha256")
        == expected_frozen_identity_sha256,
        "coordinator-frozen identity digest mismatch",
    )
    _require(
        isinstance(expected_commit_oid, str)
        and OID_PATTERN.fullmatch(expected_commit_oid) is not None
        and composed_receipt.get("private_commit_oid") == expected_commit_oid,
        "private commit identity mismatch",
    )
    _require(
        _is_digest(expected_producer_package_sha256)
        and composed_receipt.get("producer_package_sha256")
        == expected_producer_package_sha256,
        "reviewed producer-package digest mismatch",
    )
    _require(
        composed_receipt.get("producer_registry_sha256")
        == digest_bytes(registry_content)
        and composed_receipt.get("producer_witness_sha256")
        == digest_bytes(witness_content),
        "private producer registry/witness digest mismatch",
    )
    _validate_summary_registry_binding(composed_receipt, registry)
    _require(
        composed_receipt["compatibility_sha256"]
        == registry["compatibility_module"]["expected_projection_sha256"]
        == digest_bytes(public_compatibility),
        "private compatibility projection digest mismatch",
    )
    _validate_witness(
        witness_content,
        expected_commit_oid=expected_commit_oid,
        expected_registry_sha256=composed_receipt["producer_registry_sha256"],
        expected_producer_package_sha256=expected_producer_package_sha256,
        package_members=registry["package_members"],
    )
    _require(
        isinstance(expected_public_candidate_sha256, str)
        and PRIVATE_CANDIDATE_PATTERN.fullmatch(expected_public_candidate_sha256)
        is not None
        and composed_receipt.get("public_candidate_identity")
        == expected_public_candidate_sha256
        and candidate_content_identity(
            public_root,
            error_factory=PrivateEvidenceError,
        )
        == expected_public_candidate_sha256,
        "public candidate identity mismatch",
    )
    return composed_receipt
