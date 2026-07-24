from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Any

SUMMARY_CONTRACT = "phase7-private-replay-public-summary-v2"
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
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
COMMIT_OID = "1" * 40
FIXTURE_ROOT = Path(__file__).parent / "fixtures"
FROZEN_REGISTRY_PATH = FIXTURE_ROOT / "phase7-v4-private-registry.json"
FROZEN_WITNESS_PATH = FIXTURE_ROOT / "phase7-v4-private-witness.tar"
FROZEN_BINDING_PATH = FIXTURE_ROOT / "phase7-v4-private-conformance-binding.json"
BACKEND_CONTRACTS_PATH = (
    Path(__file__).parents[1]
    / "scripts"
    / "phase7_private_evidence_backend_contracts.json"
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def json_file_bytes(value: Any) -> bytes:
    return canonical_bytes(value) + b"\n"


def digest_bytes(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def frozen_identity_sha256(summary: dict[str, Any]) -> str:
    frozen = {
        "schema_version": 4,
        "contract": "phase7-coordinator-frozen-identity-v4",
        "commit_oid": summary["private_commit_oid"],
        "producer_package_sha256": summary["producer_package_sha256"],
        "private_candidate_identity": summary["private_candidate_identity"],
        "private_receipt_sha256": summary["private_receipt_sha256"],
        "producer_registry_sha256": summary["producer_registry_sha256"],
        "producer_witness_sha256": summary["producer_witness_sha256"],
        "trust_anchor_sha256": summary["private_trust_anchor_sha256"],
        "evidence_bundle_sha256": summary["private_evidence_bundle_sha256"],
        "replay_payload_sha256": replay_payload_sha256(summary),
    }
    frozen["content_sha256"] = digest_bytes(canonical_bytes(frozen))
    return digest_bytes(json_file_bytes(frozen))


def replay_payload_sha256(summary: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in summary.items()
        if key not in {"frozen_identity_sha256", "summary_sha256"}
    }
    return digest_bytes(canonical_bytes(payload))


def replay_summary(
    compatibility_bytes: bytes,
    *,
    producer_registry_sha256: str = DIGEST_A,
    producer_witness_sha256: str = DIGEST_B,
    producer_package_sha256: str = DIGEST_C,
) -> dict[str, Any]:
    unsigned = {
        "schema_version": 2,
        "contract": SUMMARY_CONTRACT,
        "private_commit_oid": COMMIT_OID,
        "private_candidate_identity": "2" * 64,
        "producer_package_sha256": producer_package_sha256,
        "producer_registry_sha256": producer_registry_sha256,
        "producer_witness_sha256": producer_witness_sha256,
        "private_receipt_sha256": "sha256:" + "3" * 64,
        "private_trust_anchor_sha256": "sha256:" + "4" * 64,
        "frozen_identity_sha256": "sha256:" + "5" * 64,
        "private_evidence_bundle_sha256": "sha256:" + "6" * 64,
        "compatibility_bytes_base64": base64.b64encode(compatibility_bytes).decode(
            "ascii"
        ),
        "compatibility_sha256": digest_bytes(compatibility_bytes),
        "checks": [
            {
                "id": identifier,
                "registry_entry_sha256": "sha256:" + f"{index:x}" * 64,
                "test_inventory_sha256": "sha256:" + f"{index + 7:x}" * 64,
                "exit_code": 0,
                "terminal": "passed",
            }
            for index, identifier in enumerate(CHECK_ORDER)
        ],
        "role_payloads": [
            {
                "role": role,
                "sha256": "sha256:" + f"{index + 1:x}" * 64,
            }
            for index, role in enumerate(ROLE_ORDER)
        ],
        "runtime_isolation": {
            "backend": {
                "target": "macos-seatbelt",
                "binary": "sandbox-exec",
                "version": "1",
                "sha256": "sha256:" + "7" * 64,
                "version_sha256": "sha256:" + "e" * 64,
            },
            "policy_sha256": "sha256:" + "8" * 64,
            "capability_manifest_sha256": "sha256:" + "9" * 64,
            "host_identity_sha256": "sha256:" + "a" * 64,
            "kernel_identity_sha256": "sha256:" + "b" * 64,
            "conformance_sha256": "sha256:" + "c" * 64,
        },
        "conformance": {
            "sealed_request_roots": [
                {
                    "root": "request-1",
                    "tree_sha256": "sha256:" + "d" * 64,
                    "entry_count": 2,
                }
            ],
            "read_inventory": [{"root": "request-1", "path": "payload.txt"}],
            "probe_results": [
                {"root": "request-1", "path": "payload.txt", "result": "passed"}
            ],
        },
    }
    return {
        **unsigned,
        "summary_sha256": digest_bytes(canonical_bytes(unsigned)),
    }


def write_private(path: Path, content: bytes) -> None:
    path.write_bytes(content)
    path.chmod(0o600)


def deterministic_tar(members: list[tuple[str, bytes]]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
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
    return output.getvalue()


def _strict_fixture_json(content: bytes, label: str) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise AssertionError(f"duplicate key in {label}")
            value[key] = item
        return value

    value = json.loads(content, object_pairs_hook=unique_object)
    if not isinstance(value, dict) or content != json_file_bytes(value):
        raise AssertionError(f"{label} is not canonical")
    return value


def frozen_private_sample() -> dict[str, Any]:
    registry_bytes = FROZEN_REGISTRY_PATH.read_bytes()
    witness_bytes = FROZEN_WITNESS_PATH.read_bytes()
    binding_bytes = FROZEN_BINDING_PATH.read_bytes()
    registry = _strict_fixture_json(registry_bytes, "frozen private registry")
    binding = _strict_fixture_json(binding_bytes, "frozen private binding")
    if (
        binding.get("schema_version") != 1
        or binding.get("contract") != "phase7-private-public-conformance-sample-v1"
        or binding.get("claim")
        != (
            "frozen builder-produced conformance sample; "
            "not final private-candidate evidence"
        )
        or binding.get("producer_registry_sha256") != digest_bytes(registry_bytes)
        or binding.get("producer_witness_sha256") != digest_bytes(witness_bytes)
    ):
        raise AssertionError("frozen private conformance binding drift")
    unsigned_binding = {
        key: value for key, value in binding.items() if key != "content_sha256"
    }
    if binding.get("content_sha256") != digest_bytes(canonical_bytes(unsigned_binding)):
        raise AssertionError("frozen private conformance identity drift")
    with tarfile.open(fileobj=io.BytesIO(witness_bytes), mode="r:") as archive:
        members = archive.getmembers()
        manifest_member = next(
            (member for member in members if member.name == "manifest.json"),
            None,
        )
        if manifest_member is None:
            raise AssertionError("frozen private witness manifest is absent")
        extracted = archive.extractfile(manifest_member)
        if extracted is None:
            raise AssertionError("frozen private witness manifest is unreadable")
        manifest_bytes = extracted.read()
        witness_members = [
            (member.name, archive.extractfile(member).read())
            for member in members
            if member.name != "manifest.json"
        ]
    manifest = _strict_fixture_json(
        manifest_bytes,
        "frozen private witness manifest",
    )
    if (
        binding.get("witness_manifest_sha256") != digest_bytes(manifest_bytes)
        or binding.get("private_commit_oid") != manifest.get("commit_oid")
        or binding.get("producer_package_sha256")
        != manifest.get("producer_package_sha256")
        or manifest.get("registry_sha256") != digest_bytes(registry_bytes)
        or witness_bytes
        != deterministic_tar([("manifest.json", manifest_bytes), *witness_members])
    ):
        raise AssertionError("frozen private witness binding drift")
    package_members = registry.get("package_members")
    checks = registry.get("checks")
    if (
        registry.get("schema_version") != 1
        or registry.get("contract") != "phase7-private-producer-registry-v1"
        or registry.get("roles") != list(ROLE_ORDER)
        or not isinstance(package_members, list)
        or not isinstance(checks, list)
        or binding.get("producer_package_sha256")
        != digest_bytes(canonical_bytes(package_members))
    ):
        raise AssertionError("frozen private registry drift")
    return {
        "binding": binding,
        "manifest": manifest,
        "registry": registry,
        "registry_bytes": registry_bytes,
        "witness_bytes": witness_bytes,
        "witness_members": witness_members,
    }


FROZEN_PRIVATE_SAMPLE = frozen_private_sample()
PRIVATE_SOURCE_PATHS = tuple(
    member["path"] for member in FROZEN_PRIVATE_SAMPLE["registry"]["package_members"]
)
PRIVATE_CHECKS = tuple(
    (check["id"], tuple(check["tests"]))
    for check in FROZEN_PRIVATE_SAMPLE["registry"]["checks"]
)


def build_public_verifier_fixture(
    root: Path, compatibility_bytes: bytes
) -> dict[str, Any]:
    root.mkdir(mode=0o700)
    sample = frozen_private_sample()
    registry = copy.deepcopy(sample["registry"])
    backend_contracts = json.loads(BACKEND_CONTRACTS_PATH.read_bytes())
    if (
        not isinstance(backend_contracts, dict)
        or set(backend_contracts) != {"schema_version", "contract", "targets"}
        or backend_contracts["schema_version"] != 2
        or backend_contracts["contract"] != "phase7-private-isolation-backends-v2"
        or not isinstance(backend_contracts["targets"], list)
    ):
        raise AssertionError("current private backend contract drift")
    registry["schema_version"] = 2
    registry["contract"] = "phase7-private-producer-registry-v2"
    registry["backend_contracts"] = backend_contracts["targets"]
    registry_path = root / "private-producer-registry.json"
    registry_content = json_file_bytes(registry)
    write_private(registry_path, registry_content)

    witness_manifest = copy.deepcopy(sample["manifest"])
    witness_manifest["registry_sha256"] = digest_bytes(registry_content)
    commit_oid = witness_manifest["commit_oid"]
    package_sha256 = sample["binding"]["producer_package_sha256"]
    witness_path = root / "private-producer-witness.tar"
    witness_content = deterministic_tar(
        [
            ("manifest.json", json_file_bytes(witness_manifest)),
            *sample["witness_members"],
        ]
    )
    write_private(witness_path, witness_content)
    summary = replay_summary(
        compatibility_bytes,
        producer_registry_sha256=digest_bytes(registry_content),
        producer_witness_sha256=digest_bytes(witness_content),
        producer_package_sha256=package_sha256,
    )
    summary["private_commit_oid"] = commit_oid
    unsigned_summary = {
        key: value for key, value in summary.items() if key != "summary_sha256"
    }
    summary["summary_sha256"] = digest_bytes(canonical_bytes(unsigned_summary))
    for index, check in enumerate(summary["checks"]):
        registry_check = registry["checks"][index]
        check["registry_entry_sha256"] = registry_check["registry_entry_sha256"]
        check["test_inventory_sha256"] = digest_bytes(
            canonical_bytes(registry_check["tests"])
        )
    summary["frozen_identity_sha256"] = frozen_identity_sha256(summary)
    summary["summary_sha256"] = digest_bytes(
        canonical_bytes(
            {key: value for key, value in summary.items() if key != "summary_sha256"}
        )
    )
    summary_path = root / "private-replay-public-summary.json"
    write_private(summary_path, json_file_bytes(summary))
    return {
        "registry": registry,
        "registry_path": registry_path,
        "witness_manifest": witness_manifest,
        "witness_package_paths": PRIVATE_SOURCE_PATHS,
        "witness_path": witness_path,
        "summary": summary,
        "summary_path": summary_path,
        "commit_oid": commit_oid,
        "producer_package_sha256": package_sha256,
    }
