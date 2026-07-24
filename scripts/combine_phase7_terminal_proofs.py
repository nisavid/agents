#!/usr/bin/env python3
"""Strictly combine the three independently produced Phase 7 target proofs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from evidence_transport import canonical_bytes, frozen_no_replace_write, json_file_bytes


TARGETS = (
    ("macos-seatbelt", "sandbox-exec"),
    ("linux-bubblewrap", "bwrap"),
    ("wsl2-bubblewrap", "bwrap"),
)
PROOF_FIELDS = {
    "schema_version",
    "contract",
    "target",
    "binary",
    "version",
    "binary_sha256",
    "version_sha256",
    "policy_sha256",
    "capability_manifest_sha256",
    "public_candidate_identity",
    "host_identity_sha256",
    "kernel_identity_sha256",
    "conformance_sha256",
    "terminal_direct_proof_sha256",
}
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
CANDIDATE = re.compile(r"[0-9a-f]{64}")


class CombineError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CombineError(message)


def strict_json(content: bytes, label: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CombineError(f"duplicate JSON key in {label}")
            result[key] = value
        return result

    try:
        document = json.loads(content, object_pairs_hook=unique)
    except (UnicodeDecodeError, ValueError) as error:
        raise CombineError(f"{label} is not strict JSON") from error
    require(
        isinstance(document, dict) and content == json_file_bytes(document),
        f"{label} is not canonical JSON",
    )
    return document


def validate_proof(content: bytes, *, target: str, binary: str) -> dict[str, Any]:
    proof = strict_json(content, f"{target} terminal proof")
    require(
        set(proof) == PROOF_FIELDS
        and proof.get("schema_version") == 2
        and proof.get("contract") == "phase7-public-terminal-direct-proof-v2"
        and proof.get("target") == target
        and proof.get("binary") == binary
        and isinstance(proof.get("version"), str)
        and proof["version"].strip() == proof["version"]
        and proof["version"],
        "terminal proof schema drift",
    )
    require(
        isinstance(proof["public_candidate_identity"], str)
        and CANDIDATE.fullmatch(proof["public_candidate_identity"]) is not None
        and all(
            isinstance(proof[field], str) and DIGEST.fullmatch(proof[field]) is not None
            for field in (
                "binary_sha256",
                "version_sha256",
                "policy_sha256",
                "capability_manifest_sha256",
                "host_identity_sha256",
                "kernel_identity_sha256",
                "conformance_sha256",
                "terminal_direct_proof_sha256",
            )
        ),
        "terminal proof digest drift",
    )
    unsigned = {
        field: value
        for field, value in proof.items()
        if field != "terminal_direct_proof_sha256"
    }
    require(
        proof["terminal_direct_proof_sha256"]
        == "sha256:" + hashlib.sha256(canonical_bytes(unsigned)).hexdigest(),
        "terminal proof digest mismatch",
    )
    return proof


def combine(paths: list[Path], output: Path) -> dict[str, Any]:
    require(len(paths) == len(TARGETS), "exactly three terminal proofs are required")
    proofs = [
        validate_proof(path.read_bytes(), target=target, binary=binary)
        for path, (target, binary) in zip(paths, TARGETS)
    ]
    candidate = proofs[0]["public_candidate_identity"]
    require(
        all(proof["public_candidate_identity"] == candidate for proof in proofs),
        "terminal proofs bind different public candidates",
    )
    for field in (
        "target",
        "host_identity_sha256",
        "kernel_identity_sha256",
        "terminal_direct_proof_sha256",
    ):
        require(
            len({proof[field] for proof in proofs}) == len(proofs),
            f"terminal proof {field} substitution",
        )
    document = {
        "schema_version": 2,
        "contract": "phase7-public-backend-release-evidence-v2",
        "public_candidate_identity": candidate,
        "targets": proofs,
    }
    frozen_no_replace_write(
        output, json_file_bytes(document), error_factory=CombineError
    )
    return document


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--macos-proof", required=True, type=Path)
    parser.add_argument("--linux-proof", required=True, type=Path)
    parser.add_argument("--wsl2-proof", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parsed = parser.parse_args(arguments)
    try:
        paths = [
            parsed.macos_proof.absolute(),
            parsed.linux_proof.absolute(),
            parsed.wsl2_proof.absolute(),
        ]
        require(
            all(path.is_file() and not path.is_symlink() for path in paths),
            "terminal proof is unavailable",
        )
        combine(paths, parsed.output.absolute())
    except (OSError, RuntimeError, ValueError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
