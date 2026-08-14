#!/usr/bin/env python3
"""Run the complete Phase 7 private-to-production integration gate."""

from __future__ import annotations

import sys

if __name__ == "__main__" and (
    sys.implementation.name != "cpython"
    or sys.version_info < (3, 13)
    or not sys.flags.isolated
    or not sys.flags.dont_write_bytecode
):
    raise SystemExit(
        "run_phase7_production_integration.py must run with CPython 3.13+ and "
        "Python -I -B; the proof boundary begins at isolated interpreter startup"
    )

import argparse
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
_RUNNING_AS_ENTRYPOINT = __name__ == "__main__"
SOURCE_SHA256 = "8ab4fee6e5041c7838fcde794e125b37e3479a77c1536c9d063ae4d0644b599d"
PREPARED_SUPERVISOR_SOURCE_OPTION = "--prepared-supervisor-source-sha256"
MAX_PROOF_SOURCE_BYTES = 2 * 1024 * 1024
CANONICAL_REPOSITORY_URL = "https://github.com/nisavid/agents"
PHASE7_SUPPORT_SOURCES = (
    ("evidence_transport", "scripts/evidence_transport.py"),
    ("phase7_compatibility_projection", "scripts/phase7_compatibility_projection.py"),
    (
        "phase7_private_evidence_isolation",
        "scripts/phase7_private_evidence_isolation.py",
    ),
    ("private_phase7_evidence", "scripts/private_phase7_evidence.py"),
    ("validate_public_release", "scripts/validate_public_release.py"),
    ("run_phase7_composed_matrix", "scripts/run_phase7_composed_matrix.py"),
)


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


@dataclass(frozen=True)
class GitCandidate:
    revision: str
    tree_oid: str
    archive_sha256: str
    repository: str

    def as_dict(self) -> dict[str, str]:
        return {
            "revision": self.revision,
            "tree_oid": self.tree_oid,
            "archive_sha256": self.archive_sha256,
            "repository": self.repository,
        }


@dataclass(frozen=True)
class PreparedPublicCandidate:
    snapshot: Path
    repository: Path
    semantic_sha256: str
    git_candidate: GitCandidate
    supervisor_source_sha256: str

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", self.semantic_sha256) is None:
            raise IntegrationError(
                "prepared public candidate identity must be bare lowercase 64-hex"
            )
        if re.fullmatch(r"sha256:[0-9a-f]{64}", self.supervisor_source_sha256) is None:
            raise IntegrationError("prepared supervisor identity is malformed")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise IntegrationError(message)


def parse_public_candidate_sha256(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise argparse.ArgumentTypeError(
            "public candidate identity must be bare lowercase 64-hex"
        )
    return value


def _normalized_source_generation_sha256(source: bytes) -> str:
    pattern = re.compile(rb'^SOURCE_SHA256 = "[0-9a-f]{64}"$', re.MULTILINE)
    if len(pattern.findall(source)) != 1:
        raise SystemExit("Phase 7 coordinator source generation is malformed")
    normalized = pattern.sub(b'SOURCE_SHA256 = "' + (b"0" * 64) + b'"', source)
    return hashlib.sha256(normalized).hexdigest()


def _read_pinned_source(path: Path, label: str) -> bytes:
    path = Path(os.path.abspath(os.fspath(path)))
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        visible = os.lstat(path)
        require(
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_nlink == 1
            and (visible.st_dev, visible.st_ino) == (metadata.st_dev, metadata.st_ino)
            and metadata.st_size <= MAX_PROOF_SOURCE_BYTES,
            f"{label} is not a pinned single-link regular file",
        )
        source = os.pread(descriptor, metadata.st_size + 1, 0)
        after = os.fstat(descriptor)
        require(
            len(source) == metadata.st_size
            and (after.st_dev, after.st_ino, after.st_size)
            == (metadata.st_dev, metadata.st_ino, metadata.st_size),
            f"{label} changed while it was read",
        )
        return source
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _bind_loaded_coordinator_source() -> dict[str, object]:
    path = Path(os.path.abspath(__file__))
    try:
        source = _read_pinned_source(path, "Phase 7 coordinator")
        require(
            _normalized_source_generation_sha256(source) == SOURCE_SHA256,
            "Phase 7 coordinator source generation mismatch",
        )
    except (IntegrationError, OSError) as error:
        raise SystemExit("Phase 7 coordinator source generation mismatch") from error
    return {
        "path": path,
        "source": source,
        "source_sha256": "sha256:" + hashlib.sha256(source).hexdigest(),
    }


_LOADED_COORDINATOR_SOURCE = _bind_loaded_coordinator_source()


def require_loaded_coordinator_generation(snapshot: Path) -> None:
    source = _read_pinned_source(
        snapshot / "scripts/run_phase7_production_integration.py",
        "frozen Phase 7 coordinator",
    )
    require(
        source == _LOADED_COORDINATOR_SOURCE["source"],
        "loaded Phase 7 coordinator differs from the frozen candidate",
    )


def require_prepared_supervisor_generation(
    snapshot: Path, expected_source_sha256: str | None
) -> None:
    require(
        isinstance(expected_source_sha256, str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", expected_source_sha256) is not None,
        "Phase 7 production requires its prepared supervisor source identity",
    )
    source = _read_pinned_source(
        snapshot / "scripts/supervise_prepared_release_validation.py",
        "frozen prepared-release supervisor",
    )
    require(
        "sha256:" + hashlib.sha256(source).hexdigest() == expected_source_sha256,
        "loaded prepared-release supervisor differs from the frozen candidate",
    )


def _compile_frozen_module(
    snapshot: Path, module_name: str, relative_path: str
) -> ModuleType:
    require(
        module_name not in sys.modules,
        f"Phase 7 support loaded before candidate freeze: {module_name}",
    )
    path = snapshot / relative_path
    source = _read_pinned_source(path, f"frozen Phase 7 support {module_name}")
    module = ModuleType(module_name)
    module.__file__ = str(path)
    module.__package__ = ""
    sys.modules[module_name] = module
    try:
        exec(compile(source, str(path), "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def _install_frozen_phase7_support(snapshot: Path) -> None:
    loaded: dict[str, ModuleType] = {}
    try:
        for module_name, relative_path in PHASE7_SUPPORT_SOURCES:
            loaded[module_name] = _compile_frozen_module(
                snapshot, module_name, relative_path
            )
    except BaseException as error:
        for module_name in reversed(loaded):
            sys.modules.pop(module_name, None)
        if isinstance(error, IntegrationError):
            raise
        raise IntegrationError(
            f"frozen Phase 7 support cannot be loaded: {error}"
        ) from error

    transport = loaded["evidence_transport"]
    private = loaded["private_phase7_evidence"]
    globals().update(
        {
            "canonical_bytes": transport.canonical_bytes,
            "candidate_content_identity": transport.candidate_content_identity,
            "strict_json_bytes": transport.strict_json_bytes,
            "verify_private_evidence": private.verify_private_evidence,
            "validate_public_release": loaded["validate_public_release"],
            "run_phase7_composed_matrix": loaded["run_phase7_composed_matrix"],
        }
    )


if _RUNNING_AS_ENTRYPOINT:
    _PHASE7_SUPPORT_BOUND = False
else:
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))
    import run_phase7_composed_matrix
    import validate_public_release
    from evidence_transport import (
        candidate_content_identity,
        canonical_bytes,
        strict_json_bytes,
    )
    from private_phase7_evidence import verify_private_evidence

    _PHASE7_SUPPORT_BOUND = True


def ensure_frozen_phase7_support(snapshot: Path) -> None:
    global _PHASE7_SUPPORT_BOUND
    if not _PHASE7_SUPPORT_BOUND:
        _install_frozen_phase7_support(snapshot)
        _PHASE7_SUPPORT_BOUND = True


def lexical_public_repository(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        require(
            not current.is_symlink(),
            f"public repository path contains a symlink: {current}",
        )
    require(absolute.is_dir(), "public repository root must be a directory")
    return absolute


def _run_public_git(repository: Path, *arguments: str) -> bytes:
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
        completed.returncode == 0,
        completed.stderr.decode(errors="replace").strip()
        or "public candidate Git inspection failed",
    )
    return completed.stdout


def _canonical_repository_origin(origin: str) -> str:
    aliases = {
        CANONICAL_REPOSITORY_URL,
        f"{CANONICAL_REPOSITORY_URL}.git",
        "git@github.com:nisavid/agents.git",
        "ssh://git@github.com/nisavid/agents.git",
    }
    require(
        origin.strip() in aliases,
        "production release candidate origin is not nisavid/agents",
    )
    return CANONICAL_REPOSITORY_URL


def prepare_frozen_candidate_inventory(snapshot: Path) -> None:
    for arguments in (("init", "--quiet"), ("add", "--force", "--all")):
        _run_public_git(snapshot, *arguments)


def materialize_frozen_public_candidate(
    repository: Path, destination: Path
) -> GitCandidate:
    status = _run_public_git(
        repository, "status", "--porcelain=v1", "--untracked-files=all"
    )
    require(not status, "production release candidate must be a clean Git checkout")
    revision = (
        _run_public_git(repository, "rev-parse", "--verify", "HEAD")
        .decode("ascii")
        .strip()
    )
    tree_oid = (
        _run_public_git(repository, "rev-parse", "--verify", f"{revision}^{{tree}}")
        .decode("ascii")
        .strip()
    )
    archive = _run_public_git(repository, "archive", "--format=tar", revision)
    origin = _canonical_repository_origin(
        _run_public_git(repository, "remote", "get-url", "origin")
        .decode("utf-8")
        .strip()
    )
    require(
        re.fullmatch(r"[0-9a-f]{40,64}", revision) is not None
        and re.fullmatch(r"[0-9a-f]{40,64}", tree_oid) is not None,
        "Git candidate identity is malformed",
    )
    candidate = GitCandidate(
        revision=revision,
        tree_oid=tree_oid,
        archive_sha256="sha256:" + hashlib.sha256(archive).hexdigest(),
        repository=origin,
    )

    require(not destination.exists(), "frozen public candidate destination exists")
    destination.mkdir(mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        for member in tar:
            relative = Path(member.name)
            require(
                not relative.is_absolute() and ".." not in relative.parts,
                "Git candidate archive contains an unsafe path",
            )
            target = destination / relative
            require(
                target.is_relative_to(destination),
                "Git candidate archive escapes its snapshot",
            )
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(member.mode & 0o777)
                continue
            require(
                member.isfile() and not target.exists(),
                "Git candidate archive contains an unsafe entry",
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            require(source is not None, "Git candidate archive member is unreadable")
            target.write_bytes(source.read())
            target.chmod(member.mode & 0o777)
    prepare_frozen_candidate_inventory(destination)
    return candidate


@contextmanager
def frozen_public_execution(
    repository: Path, prepared_supervisor_source_sha256: str | None
) -> Iterator[PreparedPublicCandidate]:
    require(
        not _PHASE7_SUPPORT_BOUND,
        "frozen Phase 7 execution must begin before candidate support is loaded",
    )
    temporary_parent = Path(tempfile.gettempdir()).resolve()
    with tempfile.TemporaryDirectory(
        prefix="phase7-public-candidate-", dir=temporary_parent
    ) as temporary:
        snapshot = Path(temporary) / "repository"
        expected_candidate = materialize_frozen_public_candidate(repository, snapshot)
        require_loaded_coordinator_generation(snapshot)
        require_prepared_supervisor_generation(
            snapshot, prepared_supervisor_source_sha256
        )
        ensure_frozen_phase7_support(snapshot)
        validate_public_release.require_loaded_validator_generation(snapshot)
        validate_public_release.require_prepared_supervisor_generation(
            snapshot, prepared_supervisor_source_sha256
        )
        validate_public_release.validate_public_release_registration_inventory(snapshot)
        require(
            validate_public_release.git_candidate_identity(repository)
            == expected_candidate.as_dict(),
            "public candidate changed before frozen Phase 7 execution",
        )
        assert isinstance(prepared_supervisor_source_sha256, str)
        yield PreparedPublicCandidate(
            snapshot=snapshot,
            repository=repository,
            semantic_sha256=candidate_content_identity(
                snapshot,
                error_factory=IntegrationError,
            ),
            git_candidate=expected_candidate,
            supervisor_source_sha256=prepared_supervisor_source_sha256,
        )


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
                    proof_target
                    in {
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
        and set(document) == {"schema_version", "contract", "roots", "expected_backend"}
        and document["schema_version"] == 2
        and document["contract"] == "phase7-readonly-capabilities-v2",
        "capability manifest is not the canonical v2 transport schema",
    )


def coordinate(
    *,
    private_repository: Path,
    private_commit_oid: str,
    reviewed_producer_sha256: str,
    public_candidate: PreparedPublicCandidate,
    capability_manifest: Path,
    private_output: Path,
    private_summary_output: Path,
    composed_output: Path,
    routing_evidence: Path,
    plugin_eval_executable: Path,
    node_executable: Path,
    release_receipt_output: Path,
    backend_release_evidence: Path | None = None,
    expected_backend_release_evidence_sha256: str | None = None,
) -> dict[str, Any]:
    public_root = public_candidate.snapshot
    public_candidate_sha256 = public_candidate.semantic_sha256
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
        public_candidate.repository,
        routing_evidence=routing_evidence,
        plugin_eval_executable=plugin_eval_executable,
        node_executable=node_executable,
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
        expected_git_candidate=public_candidate.git_candidate.as_dict(),
        prepared_supervisor_source_sha256=(public_candidate.supervisor_source_sha256),
    )


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--private-repository", required=True, type=Path)
    parser.add_argument("--private-commit-oid", required=True)
    parser.add_argument("--reviewed-producer-sha256", required=True)
    parser.add_argument("--public-root", required=True, type=Path)
    parser.add_argument(
        "--public-candidate-sha256",
        type=parse_public_candidate_sha256,
    )
    parser.add_argument("--capability-manifest", required=True, type=Path)
    parser.add_argument("--private-output", required=True, type=Path)
    parser.add_argument("--private-summary-output", required=True, type=Path)
    parser.add_argument("--composed-output", required=True, type=Path)
    parser.add_argument("--routing-evidence", required=True, type=Path)
    parser.add_argument("--plugin-eval-executable", required=True, type=Path)
    parser.add_argument("--node-executable", required=True, type=Path)
    parser.add_argument("--release-receipt-output", required=True, type=Path)
    parser.add_argument("--backend-release-evidence", type=Path)
    parser.add_argument("--expected-backend-release-evidence-sha256")
    parser.add_argument(
        PREPARED_SUPERVISOR_SOURCE_OPTION,
        dest="prepared_supervisor_source_sha256",
        help=argparse.SUPPRESS,
    )
    parsed = parser.parse_args(arguments)
    try:
        common_arguments = {
            "private_repository": parsed.private_repository.resolve(strict=True),
            "private_commit_oid": parsed.private_commit_oid,
            "reviewed_producer_sha256": parsed.reviewed_producer_sha256,
            "capability_manifest": parsed.capability_manifest.resolve(strict=True),
            "private_output": parsed.private_output.absolute(),
            "private_summary_output": parsed.private_summary_output.absolute(),
            "composed_output": parsed.composed_output.absolute(),
            "routing_evidence": parsed.routing_evidence.resolve(strict=True),
            "plugin_eval_executable": parsed.plugin_eval_executable.resolve(
                strict=True
            ),
            "node_executable": parsed.node_executable.absolute(),
            "release_receipt_output": parsed.release_receipt_output.absolute(),
            "backend_release_evidence": (
                parsed.backend_release_evidence.resolve(strict=True)
                if parsed.backend_release_evidence is not None
                else None
            ),
            "expected_backend_release_evidence_sha256": (
                parsed.expected_backend_release_evidence_sha256
            ),
        }
        public_repository = lexical_public_repository(parsed.public_root)
        with frozen_public_execution(
            public_repository, parsed.prepared_supervisor_source_sha256
        ) as public_candidate:
            require(
                parsed.public_candidate_sha256
                in {None, public_candidate.semantic_sha256},
                "supplied public candidate identity differs from the frozen candidate",
            )
            coordinate(
                public_candidate=public_candidate,
                **common_arguments,
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
