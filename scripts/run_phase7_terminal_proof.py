#!/usr/bin/env python3
"""Derive one immutable public-safe Phase 7 terminal proof.

This is deliberately a proof-only route.  It neither runs final release
validation nor publishes a private artifact: the only newly emitted datum is a
strict public proof derived from the verified replay summary and composed
receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import run_phase7_composed_matrix
import run_phase7_production_integration as coordinator
from evidence_transport import (
    candidate_content_identity,
    canonical_bytes,
    frozen_no_replace_write,
    json_file_bytes,
)
from private_phase7_evidence import verify_private_evidence


TARGETS = (
    ("macos-seatbelt", "sandbox-exec"),
    ("linux-bubblewrap", "bwrap"),
    ("wsl2-bubblewrap", "bwrap"),
)
TARGET_SET = {target for target, _binary in TARGETS}
HEX = re.compile(r"[0-9a-f]{64}")


class TerminalProofError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise TerminalProofError(message)


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate(value: str) -> bool:
    return HEX.fullmatch(value) is not None


def derive_target_proof(
    receipt: dict[str, Any], *, expected_target: str | None, capability_manifest: Path
) -> dict[str, Any]:
    """Turn one fresh composed receipt into the sole public-safe proof record."""

    require(
        receipt.get("schema_version")
        == run_phase7_composed_matrix.COMPOSED_SCHEMA_VERSION
        and receipt.get("contract") == run_phase7_composed_matrix.COMPOSED_CONTRACT
        and receipt.get("passed") is True,
        "composed receipt did not pass",
    )
    runtime = receipt.get("runtime_isolation")
    require(isinstance(runtime, dict), "composed runtime isolation is malformed")
    backend = runtime.get("backend")
    require(isinstance(backend, dict), "composed backend identity is malformed")
    actual_target = backend.get("target")
    require(actual_target in TARGET_SET, "composed terminal target is unknown")
    if expected_target is None:
        require(
            actual_target == "macos-seatbelt",
            "an omitted proof target is only permitted for macos-seatbelt",
        )
        expected_target = "macos-seatbelt"
    require(expected_target in TARGET_SET, "terminal proof target is unknown")
    expected_binary = dict(TARGETS)[expected_target]
    require(
        actual_target == expected_target
        and backend.get("binary") == expected_binary
        and isinstance(backend.get("version"), str)
        and backend["version"].strip() == backend["version"]
        and backend["version"]
        and isinstance(backend.get("sha256"), str)
        and isinstance(backend.get("version_sha256"), str),
        "composed runtime target does not match the requested terminal target",
    )
    candidate = receipt.get("public_candidate_identity")
    require(
        isinstance(candidate, str) and _candidate(candidate),
        "receipt candidate is malformed",
    )
    manifest_digest = _digest(capability_manifest)
    require(
        runtime.get("capability_manifest_sha256") == manifest_digest,
        "capability manifest does not match the composed runtime binding",
    )
    unsigned = {
        "schema_version": 2,
        "contract": "phase7-public-terminal-direct-proof-v2",
        "target": expected_target,
        "binary": expected_binary,
        "version": backend["version"],
        "binary_sha256": backend["sha256"],
        "version_sha256": backend["version_sha256"],
        "policy_sha256": runtime.get("policy_sha256"),
        "capability_manifest_sha256": manifest_digest,
        "public_candidate_identity": candidate,
        "host_identity_sha256": runtime.get("host_identity_sha256"),
        "kernel_identity_sha256": runtime.get("kernel_identity_sha256"),
        "conformance_sha256": runtime.get("conformance_sha256"),
    }
    require(
        all(
            isinstance(unsigned[field], str)
            and (
                field == "public_candidate_identity"
                and _candidate(unsigned[field])
                or field != "public_candidate_identity"
                and re.fullmatch(r"sha256:[0-9a-f]{64}", unsigned[field]) is not None
            )
            for field in (
                "binary_sha256",
                "version_sha256",
                "policy_sha256",
                "capability_manifest_sha256",
                "public_candidate_identity",
                "host_identity_sha256",
                "kernel_identity_sha256",
                "conformance_sha256",
            )
        ),
        "composed terminal binding is malformed",
    )
    return {
        **unsigned,
        "terminal_direct_proof_sha256": "sha256:"
        + hashlib.sha256(canonical_bytes(unsigned)).hexdigest(),
    }


def run(
    *,
    private_repository: Path,
    private_commit_oid: str,
    expected_private_candidate_identity: str,
    reviewed_producer_sha256: str,
    public_root: Path,
    expected_public_candidate_sha256: str,
    capability_manifest: Path,
    private_output: Path,
    private_summary_output: Path,
    composed_output: Path,
    target: str | None,
    target_proof_output: Path,
) -> dict[str, Any]:
    """Run the immutable private/export/replay path and freeze one proof."""

    require(
        all(
            path.is_absolute()
            for path in (
                private_repository,
                public_root,
                capability_manifest,
                private_output,
                private_summary_output,
                composed_output,
                target_proof_output,
            )
        ),
        "terminal proof paths must be absolute",
    )
    require(
        _candidate(expected_public_candidate_sha256),
        "expected public candidate is malformed",
    )
    require(
        _candidate(expected_private_candidate_identity),
        "expected private candidate is malformed",
    )
    require(
        candidate_content_identity(public_root, error_factory=TerminalProofError)
        == expected_public_candidate_sha256,
        "public candidate changed before terminal proof execution",
    )
    coordinator.require_canonical_capability_manifest(capability_manifest)
    coordinator.launch_private_builder(
        private_repository=private_repository,
        private_commit_oid=private_commit_oid,
        reviewed_producer_sha256=reviewed_producer_sha256,
        public_root=public_root,
        public_candidate_sha256=expected_public_candidate_sha256,
        capability_manifest=capability_manifest,
        private_output=private_output,
        private_summary_output=private_summary_output,
        proof_target=(None if target == "macos-seatbelt" else target),
    )
    registry_path = private_output / "private-producer-registry.json"
    witness_path = private_output / "private-producer-witness.tar"
    frozen_path = private_output / "coordinator-frozen-identity.json"
    require(
        _digest(registry_path) == reviewed_producer_sha256,
        "private builder returned an unreviewed producer registry",
    )
    import json

    registry = json.loads(registry_path.read_bytes())
    package_sha256 = (
        "sha256:"
        + hashlib.sha256(canonical_bytes(registry["package_members"])).hexdigest()
    )
    frozen_identity_sha256 = _digest(frozen_path)
    verify_private_evidence(
        replay_summary_path=private_summary_output,
        producer_witness_path=witness_path,
        producer_registry_path=registry_path,
        expected_frozen_identity_sha256=frozen_identity_sha256,
        expected_commit_oid=private_commit_oid,
        expected_producer_package_sha256=package_sha256,
    )
    receipt = run_phase7_composed_matrix.run(
        replay_summary_path=private_summary_output,
        producer_witness_path=witness_path,
        producer_registry_path=registry_path,
        expected_frozen_identity_sha256=frozen_identity_sha256,
        expected_commit_oid=private_commit_oid,
        expected_producer_package_sha256=package_sha256,
        public_root=public_root,
        public_identity=expected_public_candidate_sha256,
        output=composed_output,
    )
    require(
        candidate_content_identity(public_root, error_factory=TerminalProofError)
        == expected_public_candidate_sha256
        and receipt.get("public_candidate_identity")
        == expected_public_candidate_sha256,
        "public candidate changed while terminal proof was composed",
    )
    require(
        receipt.get("private_candidate_identity")
        == expected_private_candidate_identity,
        "private candidate identity does not match the reviewed selection",
    )
    proof = derive_target_proof(
        receipt, expected_target=target, capability_manifest=capability_manifest
    )
    frozen_no_replace_write(
        target_proof_output,
        json_file_bytes(proof),
        error_factory=TerminalProofError,
    )
    return proof


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-repository", required=True, type=Path)
    parser.add_argument("--private-commit-oid", required=True)
    parser.add_argument("--reviewed-producer-sha256", required=True)
    parser.add_argument("--public-root", required=True, type=Path)
    parser.add_argument("--expected-private-candidate-identity", required=True)
    parser.add_argument("--public-candidate-sha256", required=True)
    parser.add_argument("--capability-manifest", required=True, type=Path)
    parser.add_argument("--private-output", required=True, type=Path)
    parser.add_argument("--private-summary-output", required=True, type=Path)
    parser.add_argument("--composed-output", required=True, type=Path)
    parser.add_argument("--proof-target", choices=sorted(TARGET_SET))
    parser.add_argument("--target-proof-output", required=True, type=Path)
    parsed = parser.parse_args(arguments)
    try:
        run(
            private_repository=parsed.private_repository.absolute(),
            private_commit_oid=parsed.private_commit_oid,
            expected_private_candidate_identity=(
                parsed.expected_private_candidate_identity
            ),
            reviewed_producer_sha256=parsed.reviewed_producer_sha256,
            public_root=parsed.public_root.absolute(),
            expected_public_candidate_sha256=parsed.public_candidate_sha256,
            capability_manifest=parsed.capability_manifest.absolute(),
            private_output=parsed.private_output.absolute(),
            private_summary_output=parsed.private_summary_output.absolute(),
            composed_output=parsed.composed_output.absolute(),
            target=parsed.proof_target,
            target_proof_output=parsed.target_proof_output.absolute(),
        )
    except (OSError, RuntimeError, ValueError):
        return 1
    return 0


def entrypoint_main() -> int:
    if sys.argv[1:] in (["-h"], ["--help"]):
        return main()
    print(
        "ERROR: Phase 7 terminal runtime is unavailable in this source-stage release",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(entrypoint_main())
