#!/usr/bin/env python3
"""Run the complete Phase 7 private-to-production integration gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import run_phase7_composed_matrix  # noqa: E402
import validate_public_release  # noqa: E402
from evidence_transport import canonical_bytes  # noqa: E402
from evidence_transport import candidate_content_identity  # noqa: E402
from evidence_transport import strict_json_bytes  # noqa: E402
from private_phase7_evidence import verify_private_evidence  # noqa: E402


PIPELINE_STAGES = (
    "immutable-private-builder",
    "private-summary-store",
    "public-private-verification",
    "public-composition",
    "production-release-validation",
)
PRIVATE_ARTIFACTS = {
    "private-evidence-bundle.tar",
    "private-receipt.json",
    "private-producer-registry.json",
    "private-producer-witness.tar",
    "private-trust-anchor.json",
    "coordinator-frozen-identity.json",
}


class IntegrationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise IntegrationError(message)


def git(repository: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(repository), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            "PATH": os.environ.get("PATH", ""),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        },
        check=False,
        timeout=30,
    )
    require(
        completed.returncode == 0 and not completed.stderr,
        "private commit export failed",
    )
    return completed.stdout


def export_commit(repository: Path, commit: str, destination: Path) -> None:
    require(not destination.exists(), "private commit export destination exists")
    destination.mkdir(mode=0o700)
    for row in git(repository, "ls-tree", "-r", "-z", commit).split(b"\0"):
        if not row:
            continue
        left, raw_path = row.split(b"\t", 1)
        mode, kind, oid = left.decode("ascii").split(" ")
        path = PurePosixPath(raw_path.decode("utf-8"))
        require(
            kind == "blob"
            and mode in {"100644", "100755"}
            and not path.is_absolute()
            and ".." not in path.parts,
            "private commit export contains an unsafe entry",
        )
        target = destination.joinpath(*path.parts)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        content = git(repository, "cat-file", "blob", oid)
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o700 if mode == "100755" else 0o600,
        )
        try:
            offset = 0
            while offset < len(content):
                written = os.write(descriptor, content[offset:])
                require(written > 0, "private commit export write failed")
                offset += written
        finally:
            os.close(descriptor)


def launch_private_builder(
    *,
    private_repository: Path,
    private_commit_oid: str,
    reviewed_producer_sha256: str,
    public_root: Path,
    public_candidate_sha256: str,
    capability_manifest: Path,
    private_output: Path,
    private_summary_output: Path,
    proof_target: str | None = None,
) -> None:
    require(
        private_repository.is_absolute()
        and public_root.is_absolute()
        and capability_manifest.is_absolute()
        and private_output.is_absolute()
        and private_summary_output.is_absolute()
        and not private_output.exists()
        and not private_summary_output.exists(),
        "production integration paths are unsafe",
    )
    with tempfile.TemporaryDirectory(
        prefix="phase7-private-commit-",
        dir=private_output.parent,
    ) as temporary:
        export = Path(temporary) / "export"
        export_commit(private_repository, private_commit_oid, export)
        builder = export / "scripts/build_phase7_private_evidence.py"
        require(
            builder.is_file() and not builder.is_symlink(),
            "committed private builder is unavailable",
        )
        descriptor = os.open(
            private_summary_output,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            command = [
                sys.executable,
                "-I",
                "-S",
                "-B",
                str(builder),
                "--repo",
                str(private_repository),
                "--commit",
                private_commit_oid,
                "--reviewed-producer-sha256",
                reviewed_producer_sha256,
                "--public-root",
                str(public_root),
                "--public-candidate-sha256",
                public_candidate_sha256,
                "--capability-manifest",
                str(capability_manifest),
                "--output-directory",
                str(private_output),
                "--replay-summary-fd",
                str(descriptor),
            ]
            if proof_target is not None:
                require(
                    proof_target in {
                        "linux-bubblewrap",
                        "wsl2-bubblewrap",
                    },
                    "proof target is unsafe",
                )
                command.extend(["--proof-target", proof_target])
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONNOUSERSITE": "1",
                },
                pass_fds=(descriptor,),
                check=False,
                timeout=1800,
            )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if (
            completed.returncode != 0
            or completed.stdout
            or completed.stderr
            or not private_output.is_dir()
            or {path.name for path in private_output.iterdir()} != PRIVATE_ARTIFACTS
            or private_summary_output.stat().st_size == 0
            or private_summary_output.stat().st_mode & 0o077
        ):
            if private_summary_output.exists() and not private_output.exists():
                private_summary_output.unlink()
            raise IntegrationError("immutable private builder failed")


def _digest_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def require_canonical_capability_manifest(path: Path) -> None:
    """Reject retired input shapes without duplicating private sealing rules."""

    content = path.read_bytes()
    document = strict_json_bytes(
        content,
        label="canonical Phase 7 capability manifest",
        error_factory=IntegrationError,
    )
    require(
        content == canonical_bytes(document) + b"\n",
        "capability manifest is not canonical JSON",
    )
    require(
        isinstance(document, dict)
        and set(document)
        == {"schema_version", "contract", "roots", "expected_backend"}
        and document["schema_version"] == 2
        and document["contract"] == "phase7-readonly-capabilities-v2",
        "capability manifest is not the canonical v2 transport schema",
    )


def coordinate(
    *,
    private_repository: Path,
    private_commit_oid: str,
    reviewed_producer_sha256: str,
    public_root: Path,
    public_candidate_sha256: str,
    capability_manifest: Path,
    private_output: Path,
    private_summary_output: Path,
    composed_output: Path,
    routing_evidence: Path,
    plugin_eval_executable: Path,
    release_receipt_output: Path,
    backend_release_evidence: Path | None = None,
    expected_backend_release_evidence_sha256: str | None = None,
) -> dict[str, Any]:
    require_canonical_capability_manifest(capability_manifest)
    launch_private_builder(
        private_repository=private_repository,
        private_commit_oid=private_commit_oid,
        reviewed_producer_sha256=reviewed_producer_sha256,
        public_root=public_root,
        public_candidate_sha256=public_candidate_sha256,
        capability_manifest=capability_manifest,
        private_output=private_output,
        private_summary_output=private_summary_output,
    )
    registry_path = private_output / "private-producer-registry.json"
    witness_path = private_output / "private-producer-witness.tar"
    frozen_path = private_output / "coordinator-frozen-identity.json"
    require(
        _digest_file(registry_path) == reviewed_producer_sha256,
        "private builder returned an unreviewed producer registry",
    )
    registry = json.loads(registry_path.read_bytes())
    producer_package_sha256 = (
        "sha256:"
        + hashlib.sha256(canonical_bytes(registry["package_members"])).hexdigest()
    )
    frozen_identity_sha256 = _digest_file(frozen_path)
    verify_private_evidence(
        replay_summary_path=private_summary_output,
        producer_witness_path=witness_path,
        producer_registry_path=registry_path,
        expected_frozen_identity_sha256=frozen_identity_sha256,
        expected_commit_oid=private_commit_oid,
        expected_producer_package_sha256=producer_package_sha256,
    )
    run_phase7_composed_matrix.run(
        replay_summary_path=private_summary_output,
        producer_witness_path=witness_path,
        producer_registry_path=registry_path,
        expected_frozen_identity_sha256=frozen_identity_sha256,
        expected_commit_oid=private_commit_oid,
        expected_producer_package_sha256=producer_package_sha256,
        public_root=public_root,
        public_identity=public_candidate_sha256,
        output=composed_output,
    )
    return validate_public_release.validate_release(
        public_root,
        routing_evidence=routing_evidence,
        plugin_eval_executable=plugin_eval_executable,
        receipt_output=release_receipt_output,
        composed_receipt=composed_output / "phase7-composed-matrix.json",
        private_producer_witness=witness_path,
        private_producer_registry=registry_path,
        expected_frozen_private_identity_sha256=frozen_identity_sha256,
        expected_private_commit_oid=private_commit_oid,
        expected_private_producer_package_sha256=producer_package_sha256,
        expected_public_candidate_sha256=public_candidate_sha256,
        backend_release_evidence=backend_release_evidence,
        expected_backend_release_evidence_sha256=expected_backend_release_evidence_sha256,
    )


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-repository", required=True, type=Path)
    parser.add_argument("--private-commit-oid", required=True)
    parser.add_argument("--reviewed-producer-sha256", required=True)
    parser.add_argument("--public-root", required=True, type=Path)
    parser.add_argument("--public-candidate-sha256")
    parser.add_argument("--capability-manifest", required=True, type=Path)
    parser.add_argument("--private-output", required=True, type=Path)
    parser.add_argument("--private-summary-output", required=True, type=Path)
    parser.add_argument("--composed-output", required=True, type=Path)
    parser.add_argument("--routing-evidence", required=True, type=Path)
    parser.add_argument("--plugin-eval-executable", required=True, type=Path)
    parser.add_argument("--release-receipt-output", required=True, type=Path)
    parser.add_argument("--backend-release-evidence", type=Path)
    parser.add_argument("--expected-backend-release-evidence-sha256")
    parsed = parser.parse_args(arguments)
    try:
        public_root = parsed.public_root.resolve(strict=True)
        public_identity = parsed.public_candidate_sha256 or candidate_content_identity(
            public_root,
            error_factory=IntegrationError,
        )
        coordinate(
            private_repository=parsed.private_repository.resolve(strict=True),
            private_commit_oid=parsed.private_commit_oid,
            reviewed_producer_sha256=parsed.reviewed_producer_sha256,
            public_root=public_root,
            public_candidate_sha256=public_identity,
            capability_manifest=parsed.capability_manifest.resolve(strict=True),
            private_output=parsed.private_output.absolute(),
            private_summary_output=parsed.private_summary_output.absolute(),
            composed_output=parsed.composed_output.absolute(),
            routing_evidence=parsed.routing_evidence.resolve(strict=True),
            plugin_eval_executable=parsed.plugin_eval_executable.resolve(strict=True),
            release_receipt_output=parsed.release_receipt_output.absolute(),
            backend_release_evidence=(
                parsed.backend_release_evidence.resolve(strict=True)
                if parsed.backend_release_evidence is not None
                else None
            ),
            expected_backend_release_evidence_sha256=(
                parsed.expected_backend_release_evidence_sha256
            ),
        )
    except (
        IntegrationError,
        OSError,
        ValueError,
        subprocess.SubprocessError,
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
