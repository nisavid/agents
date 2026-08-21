#!/usr/bin/env python3
"""Compose public Phase 7 evidence with a public-safe private replay summary."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from evidence_transport import (
    candidate_content_identity,
    canonical_bytes,
    digest_bytes,
    json_file_bytes,
    lexical_absolute_path,
    prepare_private_directory,
    private_atomic_write,
)
from phase7_compatibility_projection import compatibility_bytes
from private_phase7_evidence import (
    PrivateEvidenceError,
    replay_payload_sha256,
    verify_private_evidence,
)

COMPOSED_SCHEMA_VERSION = 7
COMPOSED_CONTRACT = "phase7-composed-evidence-v7"
COMPOSED_CLAIM = (
    "provider-free public composition with replay-payload-bound frozen private evidence"
)
FAMILY_IDS = ("lifecycle-dispatch", "tricritical-contract")
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


class ComposedEvidenceError(RuntimeError):
    pass


def candidate_identity(root: Path) -> str:
    return candidate_content_identity(root, error_factory=ComposedEvidenceError)


@contextmanager
def _immutable_candidate_snapshot(
    public_root: Path,
) -> Iterator[tuple[Path, str]]:
    """Capture the exact candidate bytes once and execute only that snapshot."""

    temporary = tempfile.TemporaryDirectory(prefix="phase7-public-snapshot-")
    snapshot = Path(temporary.name).resolve() / "candidate"
    snapshot.mkdir(mode=0o700)
    captured_files: list[Path] = []

    def capture_file(relative: Path, content: bytes | None, executable: bool) -> None:
        if content is None:
            return
        target = snapshot.joinpath(*relative.parts)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        private_atomic_write(
            target,
            content,
            error_factory=ComposedEvidenceError,
        )
        os.chmod(target, 0o500 if executable else 0o400)
        captured_files.append(target)

    try:
        captured_identity = candidate_content_identity(
            public_root,
            error_factory=ComposedEvidenceError,
            capture_file=capture_file,
        )
        for directory in sorted(
            (path for path in snapshot.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            os.chmod(directory, 0o500)
        os.chmod(snapshot, 0o500)
        yield snapshot, captured_identity
    finally:
        try:
            os.chmod(snapshot, 0o700)
            for path in snapshot.rglob("*"):
                if path.is_dir():
                    os.chmod(path, 0o700)
                elif path in captured_files:
                    os.chmod(path, 0o600)
        except OSError:
            pass
        temporary.cleanup()


def _isolated_environment(output: Path) -> dict[str, str]:
    return {
        "HOME": str(output / "home"),
        "CODEX_HOME": str(output / "codex"),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "GH_CONFIG_DIR": str(output / "gh"),
        "GIT_TERMINAL_PROMPT": "0",
    }


def _family_command(identifier: str, public_root: Path) -> tuple[str, ...]:
    if identifier == "lifecycle-dispatch":
        return (
            sys.executable,
            str(public_root / "scripts/run_control_plane_eval.py"),
            "validate-definition",
        )
    if identifier == "tricritical-contract":
        return (
            sys.executable,
            "-m",
            "unittest",
            (
                "tests.test_tricritical_eval_corpus."
                "TricriticalEvalCorpusTests."
                "test_scenarios_cover_terminal_reverse_edge_and_provenance_gates"
            ),
        )
    raise ComposedEvidenceError("unknown public evidence family")


def _run_public_family(
    identifier: str,
    public_root: Path,
    environment: dict[str, str],
) -> tuple[int, bytes, bytes]:
    completed = subprocess.run(
        _family_command(identifier, public_root),
        cwd=public_root,
        capture_output=True,
        check=False,
        env=environment,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _validate_public_root(root: Path) -> Path:
    root = lexical_absolute_path(root)
    if not root.is_dir() or root.is_symlink():
        raise ComposedEvidenceError(
            "public candidate root must be an absolute non-symlink directory"
        )
    return root


def _verified_private(
    *,
    replay_summary_path: Path,
    producer_witness_path: Path,
    producer_registry_path: Path,
    expected_frozen_identity_sha256: str,
    expected_commit_oid: str,
    expected_producer_package_sha256: str,
) -> dict[str, Any]:
    try:
        return verify_private_evidence(
            replay_summary_path=lexical_absolute_path(replay_summary_path),
            producer_witness_path=lexical_absolute_path(producer_witness_path),
            producer_registry_path=lexical_absolute_path(producer_registry_path),
            expected_frozen_identity_sha256=expected_frozen_identity_sha256,
            expected_commit_oid=expected_commit_oid,
            expected_producer_package_sha256=expected_producer_package_sha256,
        )
    except PrivateEvidenceError as error:
        raise ComposedEvidenceError(str(error)) from error


def _require_compatibility(
    private: dict[str, Any],
    *,
    public_root: Path,
) -> str:
    public_bytes = compatibility_bytes(public_root)
    try:
        private_bytes = base64.b64decode(
            private["compatibility_bytes_base64"],
            validate=True,
        )
    except (KeyError, ValueError, base64.binascii.Error) as error:
        raise ComposedEvidenceError(
            "private compatibility bytes are malformed"
        ) from error
    public_digest = digest_bytes(public_bytes)
    if (
        private_bytes != public_bytes
        or private["compatibility_sha256"] != public_digest
        or digest_bytes(private_bytes) != public_digest
    ):
        raise ComposedEvidenceError(
            "private and public compatibility bytes do not match exactly"
        )
    return public_digest


def _public_conformance(private: dict[str, Any]) -> dict[str, Any]:
    """Retain only the already verified opaque conformance projection."""

    conformance = private["conformance"]
    runtime = private["runtime_isolation"]
    if (
        not isinstance(conformance, dict)
        or set(conformance) != PUBLIC_CONFORMANCE_FIELDS
        or not isinstance(conformance["sha256"], str)
        or digest_bytes(canonical_bytes(conformance))
        != runtime.get("conformance_sha256")
        or any(
            type(conformance[field]) is not int or conformance[field] <= 0
            for field in ("sealed_root_count", "read_count", "probe_count")
        )
        or conformance["read_count"] != conformance["probe_count"]
    ):
        raise ComposedEvidenceError("private conformance digest mismatch")
    return copy.deepcopy(conformance)


def run(
    *,
    replay_summary_path: Path,
    producer_witness_path: Path,
    producer_registry_path: Path,
    expected_frozen_identity_sha256: str,
    expected_commit_oid: str,
    expected_producer_package_sha256: str,
    public_root: Path,
    public_identity: str,
    output: Path,
) -> dict[str, Any]:
    public_root = _validate_public_root(public_root)
    if (
        not isinstance(public_identity, str)
        or len(public_identity) != 64
        or any(character not in "0123456789abcdef" for character in public_identity)
    ):
        raise ComposedEvidenceError("public candidate identity is not a digest")
    if candidate_identity(public_root) != public_identity:
        raise ComposedEvidenceError("public candidate identity mismatch")
    with _immutable_candidate_snapshot(public_root) as (
        execution_root,
        captured_identity,
    ):
        if captured_identity != public_identity:
            raise ComposedEvidenceError("public candidate changed while captured")
        private = _verified_private(
            replay_summary_path=replay_summary_path,
            producer_witness_path=producer_witness_path,
            producer_registry_path=producer_registry_path,
            expected_frozen_identity_sha256=expected_frozen_identity_sha256,
            expected_commit_oid=expected_commit_oid,
            expected_producer_package_sha256=expected_producer_package_sha256,
        )
        compatibility_sha256 = _require_compatibility(
            private,
            public_root=execution_root,
        )
        conformance = _public_conformance(private)
        output = lexical_absolute_path(output)
        prepare_private_directory(output, error_factory=ComposedEvidenceError)
        environment = _isolated_environment(output)
        records: list[dict[str, Any]] = []
        for identifier in FAMILY_IDS:
            try:
                returncode, stdout, stderr = _run_public_family(
                    identifier,
                    execution_root,
                    environment,
                )
            except OSError as error:
                raise ComposedEvidenceError(
                    "public execution snapshot mutation was rejected"
                ) from error
            artifact = output / f"{identifier}.log"
            private_atomic_write(
                artifact,
                b"stdout:\n" + stdout + b"\nstderr:\n" + stderr,
                error_factory=ComposedEvidenceError,
            )
            records.append(
                {
                    "family": identifier,
                    "returncode": returncode,
                    "artifact": artifact.name,
                    "artifact_sha256": digest_bytes(artifact.read_bytes()),
                }
            )
        unsigned = {
            "schema_version": COMPOSED_SCHEMA_VERSION,
            "contract": COMPOSED_CONTRACT,
            "claim": COMPOSED_CLAIM,
            "public_candidate_identity": public_identity,
            "private_commit_oid": private["private_commit_oid"],
            "private_candidate_identity": private["private_candidate_identity"],
            "producer_package_sha256": private["producer_package_sha256"],
            "producer_registry_sha256": private["producer_registry_sha256"],
            "producer_witness_sha256": private["producer_witness_sha256"],
            "private_receipt_sha256": private["private_receipt_sha256"],
            "private_trust_anchor_sha256": private["private_trust_anchor_sha256"],
            "frozen_identity_sha256": private["frozen_identity_sha256"],
            "private_evidence_bundle_sha256": private["private_evidence_bundle_sha256"],
            "private_replay_payload_sha256": replay_payload_sha256(private),
            "private_replay_summary_sha256": private["summary_sha256"],
            "compatibility_sha256": compatibility_sha256,
            "checks": copy.deepcopy(private["checks"]),
            "role_payloads": copy.deepcopy(private["role_payloads"]),
            "runtime_isolation": copy.deepcopy(private["runtime_isolation"]),
            "conformance": conformance,
            "records": records,
            "passed": all(record["returncode"] == 0 for record in records),
        }
        receipt = {
            **unsigned,
            "receipt_sha256": digest_bytes(canonical_bytes(unsigned)),
        }
        private_atomic_write(
            output / "phase7-composed-matrix.json",
            json_file_bytes(receipt),
            error_factory=ComposedEvidenceError,
        )
    return receipt


def receipt_is_current(
    receipt: dict[str, Any],
    *,
    replay_summary_path: Path,
    producer_witness_path: Path,
    producer_registry_path: Path,
    expected_frozen_identity_sha256: str,
    expected_commit_oid: str,
    expected_producer_package_sha256: str,
    public_root: Path,
    public_identity: str,
    output: Path,
) -> bool:
    try:
        if (
            not isinstance(receipt, dict)
            or set(receipt) != COMPOSED_RECEIPT_FIELDS
            or type(receipt["schema_version"]) is not int
            or receipt["schema_version"] != COMPOSED_SCHEMA_VERSION
            or receipt["contract"] != COMPOSED_CONTRACT
            or receipt["claim"] != COMPOSED_CLAIM
            or receipt["passed"] is not True
        ):
            return False
        public_root = _validate_public_root(public_root)
        private = _verified_private(
            replay_summary_path=replay_summary_path,
            producer_witness_path=producer_witness_path,
            producer_registry_path=producer_registry_path,
            expected_frozen_identity_sha256=expected_frozen_identity_sha256,
            expected_commit_oid=expected_commit_oid,
            expected_producer_package_sha256=expected_producer_package_sha256,
        )
        compatibility_sha256 = _require_compatibility(
            private,
            public_root=public_root,
        )
        conformance = _public_conformance(private)
        expected_private_fields = {
            "private_commit_oid": private["private_commit_oid"],
            "private_candidate_identity": private["private_candidate_identity"],
            "producer_package_sha256": private["producer_package_sha256"],
            "producer_registry_sha256": private["producer_registry_sha256"],
            "producer_witness_sha256": private["producer_witness_sha256"],
            "private_receipt_sha256": private["private_receipt_sha256"],
            "private_trust_anchor_sha256": private["private_trust_anchor_sha256"],
            "frozen_identity_sha256": private["frozen_identity_sha256"],
            "private_evidence_bundle_sha256": private["private_evidence_bundle_sha256"],
            "private_replay_payload_sha256": replay_payload_sha256(private),
            "private_replay_summary_sha256": private["summary_sha256"],
            "compatibility_sha256": compatibility_sha256,
            "checks": private["checks"],
            "role_payloads": private["role_payloads"],
            "runtime_isolation": private["runtime_isolation"],
            "conformance": conformance,
        }
        if (
            candidate_identity(public_root) != public_identity
            or receipt["public_candidate_identity"] != public_identity
            or any(
                receipt[field] != value
                for field, value in expected_private_fields.items()
            )
        ):
            return False
        records = receipt["records"]
        if not isinstance(records, list) or len(records) != len(FAMILY_IDS):
            return False
        output = lexical_absolute_path(output)
        for index, identifier in enumerate(FAMILY_IDS):
            record = records[index]
            artifact = output / f"{identifier}.log"
            if (
                not isinstance(record, dict)
                or set(record)
                != {"family", "returncode", "artifact", "artifact_sha256"}
                or record["family"] != identifier
                or type(record["returncode"]) is not int
                or record["returncode"] != 0
                or record["artifact"] != artifact.name
                or not artifact.is_file()
                or artifact.is_symlink()
                or record["artifact_sha256"] != digest_bytes(artifact.read_bytes())
            ):
                return False
        unsigned = {
            key: value for key, value in receipt.items() if key != "receipt_sha256"
        }
        return receipt["receipt_sha256"] == digest_bytes(canonical_bytes(unsigned))
    except (
        ComposedEvidenceError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-replay-summary", required=True, type=Path)
    parser.add_argument("--private-producer-witness", required=True, type=Path)
    parser.add_argument("--private-producer-registry", required=True, type=Path)
    parser.add_argument("--expected-frozen-private-identity-sha256", required=True)
    parser.add_argument("--expected-private-commit-oid", required=True)
    parser.add_argument(
        "--expected-private-producer-package-sha256",
        required=True,
    )
    parser.add_argument("--public-root", required=True, type=Path)
    parser.add_argument("--public-candidate-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        receipt = run(
            replay_summary_path=lexical_absolute_path(arguments.private_replay_summary),
            producer_witness_path=lexical_absolute_path(
                arguments.private_producer_witness
            ),
            producer_registry_path=lexical_absolute_path(
                arguments.private_producer_registry
            ),
            expected_frozen_identity_sha256=(
                arguments.expected_frozen_private_identity_sha256
            ),
            expected_commit_oid=arguments.expected_private_commit_oid,
            expected_producer_package_sha256=(
                arguments.expected_private_producer_package_sha256
            ),
            public_root=lexical_absolute_path(arguments.public_root),
            public_identity=arguments.public_candidate_sha256,
            output=lexical_absolute_path(arguments.output),
        )
    except (
        ComposedEvidenceError,
        OSError,
        subprocess.SubprocessError,
    ) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["passed"] else 1


def entrypoint_main() -> int:
    if sys.argv[1:] in (["-h"], ["--help"]):
        return main()
    print(
        "ERROR: Phase 7 composed runtime is unavailable in this source-stage release",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(entrypoint_main())
