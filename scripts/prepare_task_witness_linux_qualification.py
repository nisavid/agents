#!/usr/bin/env python3
"""Prepare cooperative evidence for the Task Witness Linux qualification run.

This operator-owned helper is intentionally external to the frozen candidate. It
does not issue product or cryptographic attestations. It records live facts,
seals a CPython closure, and emits the canonical inputs consumed by the
candidate-owned qualification runner.
"""

from __future__ import annotations

import argparse
import base64
import csv
import errno
import fcntl
import hashlib
import io
import json
import locale
import os
import platform
import pwd
import shutil
import signal
import stat
import subprocess
import sys
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any

CAPABILITY_FIELDS = ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
CONTENT_DIGEST_FIELD = "content_sha256"
FILESYSTEM_SEMANTICS = [
    "advisory-flock-open-file-description",
    "atomic-same-directory-replace",
    "c-utf8-locale",
    "directory-fsync",
    "o-cloexec",
    "o-nofollow",
    "owner-mode",
    "passwd-database",
    "process-session",
    "signal-mask-pending",
    "waitid-wnowait",
]
EXPECTED_HARNESS_REF = "refs/heads/ivan/task-witness-linux-qualification-harness"
EXPECTED_REPOSITORY = "nisavid/agents"
GITHUB_CONTEXT_FIELDS = (
    "GITHUB_ACTION",
    "GITHUB_ACTOR",
    "GITHUB_EVENT_NAME",
    "GITHUB_JOB",
    "GITHUB_REF",
    "GITHUB_REPOSITORY",
    "GITHUB_RUN_ATTEMPT",
    "GITHUB_RUN_ID",
    "GITHUB_RUN_NUMBER",
    "GITHUB_SERVER_URL",
    "GITHUB_SHA",
    "GITHUB_WORKFLOW",
    "GITHUB_WORKFLOW_REF",
    "GITHUB_WORKFLOW_SHA",
    "ImageOS",
    "ImageVersion",
    "RUNNER_ARCH",
    "RUNNER_ENVIRONMENT",
    "RUNNER_NAME",
    "RUNNER_OS",
)
QUALIFICATION_CLASS = "task-witness-cpython-closure-v1"
RUNTIME_DEPENDENCY_CLASSES = [
    "cpython-extension-modules",
    "cpython-stdlib",
    "loader-shared-libraries",
    "qualification-python-packages",
]
PYYAML_DISTRIBUTION = "PyYAML"
PYYAML_VERSION = "6.0.3"
PYYAML_WHEEL_FILENAME = (
    "pyyaml-6.0.3-cp313-cp313-manylinux2014_x86_64."
    "manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl"
)
PYYAML_WHEEL_SHA256 = "0f29edc409a6392443abf94b9cf89ce99889a1dd5376d94316ae5145dfedd5d6"
PYYAML_WHEEL_URL = (
    "https://files.pythonhosted.org/packages/74/27/"
    "e5b8f34d02d9995b80abcef563ea1f8b56d20134d8f4e5e81733b1feceb2/"
    f"{PYYAML_WHEEL_FILENAME}"
)
SYSTEM_TOOLS = (
    ("environment-clearer", Path("/usr/bin/env")),
    ("git", Path("/usr/bin/git")),
    ("posix-shell", Path("/usr/bin/sh")),
)


class PreparationError(ValueError):
    """A qualification preparation precondition was not satisfied."""


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def content_document(value: dict[str, Any]) -> dict[str, Any]:
    if CONTENT_DIGEST_FIELD in value:
        raise PreparationError("content digest field already exists")
    result = dict(value)
    result[CONTENT_DIGEST_FIELD] = hashlib.sha256(canonical_bytes(value)).hexdigest()
    return result


def write_create_new(path: Path, value: object, *, mode: int = 0o444) -> bytes:
    raw = canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        offset = 0
        while offset < len(raw):
            offset += os.write(descriptor, raw[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return raw


def load_canonical(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PreparationError(f"{path} is not valid JSON") from error
    if not isinstance(value, dict) or canonical_bytes(value) != raw:
        raise PreparationError(f"{path} is not canonical JSON")
    return value, raw


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def stable_file_binding(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def stage_bootstrap_helper(source: Path, output: Path) -> str:
    source = require_absolute(source, "bootstrap helper source")
    output = require_absolute(output, "bootstrap helper output")
    if source == output or output.name == "":
        raise PreparationError("bootstrap helper paths are invalid")

    source_descriptor = os.open(
        source,
        os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        source_metadata = os.fstat(source_descriptor)
        if (
            not stat.S_ISREG(source_metadata.st_mode)
            or source_metadata.st_nlink != 1
            or source_metadata.st_mode & 0o022
        ):
            raise PreparationError("bootstrap helper source disposition is unsafe")
        chunks: list[bytes] = []
        while chunk := os.read(source_descriptor, 1024 * 1024):
            chunks.append(chunk)
        raw = b"".join(chunks)
        if not raw or stable_file_binding(os.fstat(source_descriptor)) != (
            stable_file_binding(source_metadata)
        ):
            raise PreparationError("bootstrap helper source changed while reading")
    finally:
        os.close(source_descriptor)

    parent_descriptor = os.open(
        output.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        output_descriptor = os.open(
            output.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o555,
            dir_fd=parent_descriptor,
        )
        try:
            os.fchmod(output_descriptor, 0o555)
            offset = 0
            while offset < len(raw):
                offset += os.write(output_descriptor, raw[offset:])
            os.fsync(output_descriptor)
            output_metadata = os.fstat(output_descriptor)
            if (
                not stat.S_ISREG(output_metadata.st_mode)
                or output_metadata.st_nlink != 1
                or output_metadata.st_uid != os.geteuid()
                or output_metadata.st_gid != os.getegid()
                or stat.S_IMODE(output_metadata.st_mode) != 0o555
            ):
                raise PreparationError("bootstrap helper output disposition is unsafe")
        finally:
            os.close(output_descriptor)
        os.fsync(parent_descriptor)
    finally:
        os.close(parent_descriptor)

    expected = hashlib.sha256(raw).hexdigest()
    if sha256_file(output) != expected:
        raise PreparationError("bootstrap helper copy digest disagrees")
    return expected


def artifact_files(root: Path) -> list[tuple[str, Path]]:
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise PreparationError("artifact root is not an absolute regular directory")
    normalized_root = root.resolve(strict=True)
    result: list[tuple[str, Path]] = []
    for path in [normalized_root, *sorted(normalized_root.rglob("*"))]:
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise PreparationError("artifact tree contains a non-regular entry")
        if path == normalized_root / "SHA256SUMS":
            continue
        relative = path.relative_to(normalized_root).as_posix()
        if (
            not relative
            or "\\" in relative
            or any(
                ord(character) < 32 or ord(character) == 127 for character in relative
            )
        ):
            raise PreparationError("artifact path is not manifest-safe")
        result.append((relative, path))
    if not result:
        raise PreparationError("artifact tree contains no retained files")
    return result


def write_artifact_manifest(root: Path, output: Path) -> None:
    files = artifact_files(root)
    if not output.is_absolute() or output.name == "":
        raise PreparationError("artifact manifest output is not absolute")
    normalized_root = root.resolve(strict=True)
    normalized_output = output.parent.resolve(strict=True) / output.name
    if normalized_output.is_relative_to(normalized_root):
        raise PreparationError("artifact manifest must be created outside the tree")
    raw = "".join(
        f"{sha256_file(path)}  ./{relative}\n" for relative, path in files
    ).encode("ascii")
    descriptor = os.open(
        normalized_output,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o444,
    )
    try:
        offset = 0
        while offset < len(raw):
            offset += os.write(descriptor, raw[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    verify_artifact_manifest(normalized_root, normalized_output)


def verify_artifact_manifest(root: Path, manifest: Path) -> None:
    files = artifact_files(root)
    normalized_root = root.resolve(strict=True)
    if not manifest.is_absolute():
        raise PreparationError("artifact manifest path is not absolute")
    normalized_manifest = manifest.parent.resolve(strict=True) / manifest.name
    if normalized_manifest.is_relative_to(normalized_root) and normalized_manifest != (
        normalized_root / "SHA256SUMS"
    ):
        raise PreparationError("artifact manifest has an invalid in-tree path")
    descriptor = os.open(normalized_manifest, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise PreparationError("artifact manifest disposition is unsafe")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        raw = b"".join(chunks)
        if stable_file_binding(os.fstat(descriptor)) != stable_file_binding(metadata):
            raise PreparationError("artifact manifest changed while reading")
    finally:
        os.close(descriptor)
    try:
        lines = raw.decode("ascii").splitlines(keepends=True)
    except UnicodeDecodeError as error:
        raise PreparationError("artifact manifest is not ASCII") from error
    expected_paths = [relative for relative, _path in files]
    observed_paths: list[str] = []
    observed_digests: dict[str, str] = {}
    for line in lines:
        if not line.endswith("\n") or len(line) < 69 or line[64:68] != "  ./":
            raise PreparationError("artifact manifest line is malformed")
        digest = line[:64]
        relative = line[68:-1]
        if (
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not relative
            or "\\" in relative
            or any(
                ord(character) < 32 or ord(character) == 127 for character in relative
            )
            or PurePosixPath(relative).is_absolute()
            or any(part in {"", ".", ".."} for part in PurePosixPath(relative).parts)
        ):
            raise PreparationError("artifact manifest line is malformed")
        observed_paths.append(relative)
        observed_digests[relative] = digest
    if observed_paths != expected_paths or len(observed_digests) != len(observed_paths):
        raise PreparationError("artifact manifest inventory disagrees")
    for relative, path in files:
        if sha256_file(path) != observed_digests[relative]:
            raise PreparationError("artifact manifest digest disagrees")


def run(
    argv: list[str],
    *,
    check: bool = True,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if check and process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip() or "no diagnostics"
        raise PreparationError(f"{argv[0]} failed: {detail}")
    return process


def require_root() -> None:
    if os.geteuid() != 0:
        raise PreparationError("this operation must run as root in the ephemeral VM")


def require_unprivileged_boundary() -> None:
    if os.getuid() == 0 or os.geteuid() != os.getuid() or os.getgid() != os.getegid():
        raise PreparationError("this operation requires a stable unprivileged identity")
    if os.getgroups():
        raise PreparationError("the unprivileged identity retains supplementary groups")
    if any(linux_capabilities().values()):
        raise PreparationError("the unprivileged identity retains Linux capabilities")
    no_new_privs: list[int] = []
    for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
        name, separator, raw = line.partition(":")
        if separator and name == "NoNewPrivs":
            no_new_privs.append(int(raw.strip()))
    if no_new_privs != [1]:
        raise PreparationError("the unprivileged identity lacks no-new-privileges")


def require_absolute(path: Path, label: str) -> Path:
    if not path.is_absolute() or ".." in path.parts or str(path).startswith("//"):
        raise PreparationError(f"{label} must be a normalized absolute path")
    return path


def capture_context(output: Path) -> None:
    require_root()
    if output.exists():
        raise PreparationError("context output already exists")
    context = {
        key: os.environ[key] for key in GITHUB_CONTEXT_FIELDS if os.environ.get(key)
    }
    required = {
        "GITHUB_EVENT_NAME",
        "GITHUB_REF",
        "GITHUB_REPOSITORY",
        "GITHUB_RUN_ATTEMPT",
        "GITHUB_RUN_ID",
        "GITHUB_SERVER_URL",
        "GITHUB_SHA",
        "GITHUB_WORKFLOW_REF",
        "RUNNER_ARCH",
        "RUNNER_ENVIRONMENT",
        "RUNNER_OS",
    }
    if not required.issubset(context):
        raise PreparationError("GitHub Actions context is incomplete")
    expected = {
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": EXPECTED_HARNESS_REF,
        "GITHUB_REPOSITORY": EXPECTED_REPOSITORY,
        "RUNNER_ARCH": "X64",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Linux",
    }
    if any(context.get(key) != value for key, value in expected.items()):
        raise PreparationError("GitHub Actions context is outside the approved harness")
    write_create_new(
        output,
        content_document(
            {
                "schema_version": 1,
                "contract": "task-witness-github-actions-context-v1",
                "context": context,
            }
        ),
    )


def verify_runtime_source(args: argparse.Namespace) -> None:
    root = require_absolute(args.root, "runtime source root")
    executable = require_absolute(args.executable, "runtime source executable")
    if (
        not root.is_dir()
        or not executable.exists()
        or not executable.is_relative_to(root)
    ):
        raise PreparationError("runtime source is incomplete")
    for path in [root, *root.rglob("*")]:
        try:
            metadata = path.lstat()
        except OSError as error:
            raise PreparationError("runtime source cannot be inspected") from error
        if stat.S_ISLNK(metadata.st_mode):
            try:
                resolved = path.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise PreparationError(
                    "runtime source symlink cannot be resolved"
                ) from error
            if not resolved.is_relative_to(root):
                raise PreparationError("runtime source symlink escapes the source root")
        elif not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
            raise PreparationError("runtime source contains a special file")


def canonical_distribution_name(value: str) -> str:
    return "-".join(filter(None, value.lower().replace("_", "-").split("-")))


def normalized_wheel_path(value: str, label: str) -> PurePosixPath:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or value.startswith("/")
        or "\x00" in value
    ):
        raise PreparationError(f"{label} path is unsafe")
    path = PurePosixPath(value)
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise PreparationError(f"{label} path is unsafe")
    return path


def install_wheel(args: argparse.Namespace) -> None:
    require_unprivileged_boundary()
    runtime_root = require_absolute(args.runtime_root, "runtime root")
    site_packages = require_absolute(args.site_packages, "site-packages root")
    wheel = require_absolute(args.wheel, "wheel")
    if not site_packages.is_relative_to(runtime_root) or not site_packages.is_dir():
        raise PreparationError("site-packages is outside the runtime root")
    if runtime_root.stat().st_uid != os.geteuid():
        raise PreparationError(
            "the runtime root is not owned by the installer identity"
        )
    wheel_metadata = wheel.lstat()
    if (
        not stat.S_ISREG(wheel_metadata.st_mode)
        or wheel_metadata.st_uid != 0
        or wheel_metadata.st_mode & 0o222
        or wheel.name != PYYAML_WHEEL_FILENAME
        or sha256_file(wheel) != PYYAML_WHEEL_SHA256
    ):
        raise PreparationError("the pinned PyYAML wheel identity disagrees")

    with zipfile.ZipFile(wheel) as archive:
        members = archive.infolist()
        if not members or len(members) > 1000:
            raise PreparationError("the pinned wheel inventory is invalid")
        names: set[str] = set()
        total_bytes = 0
        for member in members:
            if member.flag_bits & 0x1:
                raise PreparationError("the pinned wheel contains encrypted content")
            relative = normalized_wheel_path(member.filename, "wheel member")
            normalized = relative.as_posix()
            if normalized in names:
                raise PreparationError("the pinned wheel contains duplicate paths")
            names.add(normalized)
            mode = member.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise PreparationError("the pinned wheel contains a special file")
            if ".data" in relative.parts:
                raise PreparationError(
                    "the pinned wheel requires unsupported relocation"
                )
            total_bytes += member.file_size
            if member.file_size > 16 * 1024 * 1024 or total_bytes > 32 * 1024 * 1024:
                raise PreparationError("the pinned wheel exceeds the extraction bound")
            destination = site_packages.joinpath(*relative.parts)
            if member.is_dir():
                destination.mkdir(mode=0o755, parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o644,
            )
            try:
                with archive.open(member) as source:
                    while chunk := source.read(1024 * 1024):
                        offset = 0
                        while offset < len(chunk):
                            offset += os.write(descriptor, chunk[offset:])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    validate_installed_wheel(site_packages)


def record_digest(raw: str) -> str:
    algorithm, separator, value = raw.partition("=")
    if separator != "=" or algorithm != "sha256" or not value:
        raise PreparationError("PyYAML RECORD uses an unsupported digest")
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (TypeError, ValueError) as error:
        raise PreparationError("PyYAML RECORD digest is malformed") from error
    if len(decoded) != hashlib.sha256().digest_size:
        raise PreparationError("PyYAML RECORD digest length disagrees")
    return decoded.hex()


def validate_installed_wheel(site_packages: Path) -> dict[str, Any]:
    expected_dist_info = f"pyyaml-{PYYAML_VERSION}.dist-info"
    candidates = [
        path
        for path in site_packages.glob("*.dist-info")
        if path.name.lower() == expected_dist_info
    ]
    if len(candidates) != 1 or not candidates[0].is_dir():
        raise PreparationError("the installed PyYAML distribution metadata disagrees")
    dist_info = candidates[0]
    metadata_path = dist_info / "METADATA"
    wheel_path = dist_info / "WHEEL"
    record_path = dist_info / "RECORD"
    metadata = BytesParser().parsebytes(metadata_path.read_bytes())
    if (
        canonical_distribution_name(metadata.get("Name", "")) != "pyyaml"
        or metadata.get("Version") != PYYAML_VERSION
    ):
        raise PreparationError("the installed PyYAML name or version disagrees")
    wheel_metadata = BytesParser().parsebytes(wheel_path.read_bytes())
    if (
        wheel_metadata.get("Wheel-Version") != "1.0"
        or wheel_metadata.get("Root-Is-Purelib") != "false"
        or sorted(wheel_metadata.get_all("Tag", []))
        != [
            "cp313-cp313-manylinux2014_x86_64",
            "cp313-cp313-manylinux_2_17_x86_64",
            "cp313-cp313-manylinux_2_28_x86_64",
        ]
    ):
        raise PreparationError("the installed PyYAML wheel tags disagree")

    try:
        rows = list(csv.reader(io.StringIO(record_path.read_text(encoding="utf-8"))))
    except (OSError, UnicodeError, csv.Error) as error:
        raise PreparationError("the installed PyYAML RECORD is unreadable") from error
    if not rows or any(len(row) != 3 for row in rows):
        raise PreparationError("the installed PyYAML RECORD shape disagrees")
    seen: set[str] = set()
    entries: list[dict[str, Any]] = []
    record_relative = record_path.relative_to(site_packages).as_posix()
    for raw_path, raw_digest, raw_length in rows:
        relative = normalized_wheel_path(raw_path, "PyYAML RECORD")
        value = relative.as_posix()
        if relative.suffix in {".pth", ".pyc"}:
            raise PreparationError(
                "the installed PyYAML RECORD names executable residue"
            )
        if value in seen:
            raise PreparationError("the installed PyYAML RECORD repeats a path")
        seen.add(value)
        path = site_packages.joinpath(*relative.parts)
        metadata_value = path.lstat()
        if not stat.S_ISREG(metadata_value.st_mode):
            raise PreparationError("the installed PyYAML RECORD names a non-file")
        digest = sha256_file(path)
        length = metadata_value.st_size
        if value == record_relative:
            if raw_digest or raw_length:
                raise PreparationError("the installed PyYAML RECORD self-row disagrees")
            expected_digest = None
            expected_length = None
        else:
            expected_digest = record_digest(raw_digest)
            if not raw_length.isascii() or not raw_length.isdecimal():
                raise PreparationError("the installed PyYAML RECORD length is invalid")
            expected_length = int(raw_length)
            if digest != expected_digest or length != expected_length:
                raise PreparationError(
                    "the installed PyYAML content disagrees with RECORD"
                )
        entries.append(
            {
                "path": value,
                "record_sha256": expected_digest,
                "record_length": expected_length,
                "installed_sha256": digest,
                "installed_length": length,
            }
        )
    required = {"yaml/__init__.py", "_yaml/__init__.py", record_relative}
    native_extensions = sorted(
        value
        for value in seen
        if value.startswith("yaml/_yaml") and value.endswith(".so")
    )
    if not required.issubset(seen) or native_extensions != [
        "yaml/_yaml.cpython-313-x86_64-linux-gnu.so"
    ]:
        raise PreparationError("the installed PyYAML package surface is incomplete")
    actual: set[str] = set()
    for root in (site_packages / "yaml", site_packages / "_yaml", dist_info):
        for path in [root, *root.rglob("*")]:
            path_metadata = path.lstat()
            if stat.S_ISREG(path_metadata.st_mode):
                actual.add(path.relative_to(site_packages).as_posix())
            elif not stat.S_ISDIR(path_metadata.st_mode):
                raise PreparationError(
                    "the installed PyYAML package contains a special file"
                )
    if actual != seen:
        raise PreparationError("the installed PyYAML RECORD inventory is incomplete")
    return {
        "distribution": PYYAML_DISTRIBUTION,
        "version": PYYAML_VERSION,
        "site_packages": str(site_packages),
        "dist_info": dist_info.name,
        "record_path": record_relative,
        "record_sha256": sha256_file(record_path),
        "native_extensions": native_extensions,
        "entries": sorted(entries, key=lambda item: item["path"]),
    }


def rebound_record_digest(digest: str) -> str:
    raw = bytes.fromhex(digest)
    return "sha256=" + base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def finalize_installed_wheel(
    site_packages: Path,
    before: dict[str, Any],
) -> dict[str, Any]:
    native_extensions = set(before["native_extensions"])
    transformed: list[str] = []
    before_by_path = {entry["path"]: entry for entry in before["entries"]}
    for entry in before["entries"]:
        path = site_packages.joinpath(*PurePosixPath(entry["path"]).parts)
        final_digest = sha256_file(path)
        final_length = path.stat().st_size
        changed = (
            entry["installed_sha256"] != final_digest
            or entry["installed_length"] != final_length
        )
        if changed:
            if entry["path"] not in native_extensions or not is_elf(path):
                raise PreparationError("non-ELF PyYAML content changed during sealing")
            transformed.append(entry["path"])
    if set(transformed) != native_extensions:
        raise PreparationError("the PyYAML native extension was not closure-rewritten")

    record_path = site_packages / before["record_path"]
    rows = list(csv.reader(io.StringIO(record_path.read_text(encoding="utf-8"))))
    rewritten = 0
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for row in rows:
        if row[0] in native_extensions:
            path = site_packages.joinpath(*PurePosixPath(row[0]).parts)
            row = [
                row[0],
                rebound_record_digest(sha256_file(path)),
                str(path.stat().st_size),
            ]
            rewritten += 1
        writer.writerow(row)
    if rewritten != len(native_extensions):
        raise PreparationError("the PyYAML RECORD rewrite set disagrees")
    temporary = record_path.with_name(".RECORD.task-witness-new")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o644,
    )
    try:
        raw = output.getvalue().encode("utf-8")
        offset = 0
        while offset < len(raw):
            offset += os.write(descriptor, raw[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, record_path)
    directory = os.open(record_path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)

    after = validate_installed_wheel(site_packages)
    after_by_path = {entry["path"]: entry for entry in after["entries"]}
    if set(after_by_path) != set(before_by_path):
        raise PreparationError("the final PyYAML RECORD path set disagrees")
    entries: list[dict[str, Any]] = []
    for value in sorted(before_by_path):
        upstream = before_by_path[value]
        final = after_by_path[value]
        if value in native_extensions:
            disposition = "operator-rewritten-elf-and-record-rebound"
        elif value == before["record_path"]:
            disposition = "operator-rebound-record"
        else:
            disposition = "unchanged"
            if (
                upstream["installed_sha256"] != final["installed_sha256"]
                or upstream["installed_length"] != final["installed_length"]
            ):
                raise PreparationError(
                    "unexpected PyYAML content changed during sealing"
                )
        entries.append(
            {
                "path": value,
                "upstream_record_sha256": upstream["record_sha256"],
                "upstream_record_length": upstream["record_length"],
                "upstream_installed_sha256": upstream["installed_sha256"],
                "upstream_installed_length": upstream["installed_length"],
                "final_record_sha256": final["record_sha256"],
                "final_record_length": final["record_length"],
                "final_sha256": final["installed_sha256"],
                "final_length": final["installed_length"],
                "disposition": disposition,
            }
        )
    return {
        **{
            key: value
            for key, value in before.items()
            if key not in {"entries", "record_sha256"}
        },
        "upstream_record_sha256": before["record_sha256"],
        "final_record_sha256": after["record_sha256"],
        "record_verified_before_runtime_rewrite": True,
        "record_verified_after_runtime_rewrite": True,
        "transformed_paths": transformed,
        "entries": entries,
    }


def is_elf(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with path.open("rb") as stream:
            return stream.read(4) == b"\x7fELF"
    except OSError:
        return False


def elf_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if is_elf(path))


def dynamic_elf_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for path in elf_files(root):
        rpath = run(["/usr/bin/patchelf", "--print-rpath", str(path)], check=False)
        interpreter = run(
            ["/usr/bin/patchelf", "--print-interpreter", str(path)],
            check=False,
        )
        if rpath.returncode == 0 or interpreter.returncode == 0:
            result.append(path)
    return result


def unprivileged_argv(
    uid: int,
    gid: int,
    argv: list[str],
    *,
    home: str,
) -> list[str]:
    return [
        "/usr/bin/setpriv",
        f"--reuid={uid}",
        f"--regid={gid}",
        "--clear-groups",
        "--inh-caps=-all",
        "--ambient-caps=-all",
        "--bounding-set=-all",
        "--no-new-privs",
        "--reset-env",
        "/usr/bin/env",
        "-i",
        f"HOME={home}",
        "LANG=C.UTF-8",
        "LC_ALL=C.UTF-8",
        "PATH=/usr/bin:/bin",
        "TZ=UTC",
        *argv,
    ]


def ldd_paths(path: Path, uid: int, gid: int) -> tuple[list[Path], str]:
    process = run(
        unprivileged_argv(
            uid,
            gid,
            ["/usr/bin/ldd", str(path)],
            home="/nonexistent-task-witness-ldd-home",
        ),
        check=False,
    )
    output = process.stdout + process.stderr
    if "not a dynamic executable" in output or "statically linked" in output:
        return [], output
    if process.returncode != 0 or "not found" in output:
        raise PreparationError(
            f"dynamic dependency inspection failed for {path}: {output}"
        )
    paths: set[Path] = set()
    for line in output.splitlines():
        line = line.strip()
        if "=>" in line:
            _name, resolved = line.split("=>", 1)
            candidate = resolved.strip().split(" ", 1)[0]
        else:
            candidate = line.split(" ", 1)[0]
        if candidate.startswith("/"):
            paths.add(Path(candidate).resolve())
    return sorted(paths), output


def patchelf_interpreter(path: Path) -> str | None:
    process = run(["/usr/bin/patchelf", "--print-interpreter", str(path)], check=False)
    if process.returncode != 0:
        return None
    value = process.stdout.strip()
    return value or None


def seal_runtime(args: argparse.Namespace) -> None:
    require_root()
    runtime_root = require_absolute(args.runtime_root, "runtime root")
    executable = require_absolute(args.runtime_executable, "runtime executable")
    site_packages = require_absolute(args.site_packages, "site-packages root")
    dependency_wheel = require_absolute(args.dependency_wheel, "dependency wheel")
    audit_output = require_absolute(args.audit_output, "runtime audit output")
    if not runtime_root.is_dir() or not executable.is_file():
        raise PreparationError("runtime copy is incomplete")
    if not executable.is_relative_to(runtime_root):
        raise PreparationError("runtime executable is outside the runtime root")
    if not site_packages.is_relative_to(runtime_root):
        raise PreparationError("site-packages is outside the runtime root")
    if audit_output.exists():
        raise PreparationError("runtime audit output already exists")
    if args.inspection_uid <= 0 or args.inspection_gid < 0:
        raise PreparationError("runtime inspection identity is invalid")
    wheel_metadata = dependency_wheel.lstat()
    if (
        args.runtime_version != "3.13.7"
        or args.pyyaml_version != PYYAML_VERSION
        or args.pyyaml_wheel_url != PYYAML_WHEEL_URL
        or dependency_wheel.name != PYYAML_WHEEL_FILENAME
        or not stat.S_ISREG(wheel_metadata.st_mode)
        or wheel_metadata.st_uid != 0
        or wheel_metadata.st_mode & 0o222
        or sha256_file(dependency_wheel) != PYYAML_WHEEL_SHA256
    ):
        raise PreparationError("the pinned PyYAML wheel source disagrees")
    for path in [runtime_root, *runtime_root.rglob("*")]:
        metadata = path.lstat()
        if (
            metadata.st_uid != 0
            or metadata.st_mode & 0o022
            or not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode))
        ):
            raise PreparationError(
                "runtime sealing input is not root-owned immutable data"
            )
    pyyaml_before = validate_installed_wheel(site_packages)
    loader_root = runtime_root / "lib" / "task-witness-loader"
    loader_root.mkdir(parents=True, exist_ok=False)

    copied: dict[str, dict[str, Any]] = {}
    pending = dynamic_elf_files(runtime_root)
    inspected: set[Path] = set()
    while pending:
        item = pending.pop(0)
        if item in inspected:
            continue
        inspected.add(item)
        dependencies, _output = ldd_paths(
            item,
            args.inspection_uid,
            args.inspection_gid,
        )
        for source in dependencies:
            if source.is_relative_to(runtime_root):
                continue
            destination = loader_root / source.name
            source_digest = sha256_file(source)
            if destination.exists():
                if sha256_file(destination) != source_digest:
                    raise PreparationError(
                        f"loader dependency basename collision for {source.name}"
                    )
            else:
                shutil.copy2(source, destination, follow_symlinks=True)
                pending.append(destination)
            copied[str(source)] = {
                "retained_path": str(destination),
                "sha256": source_digest,
            }

    loader_interpreters = {
        source.name: loader_root / source.name
        for source in (Path(path) for path in copied)
        if source.name.startswith("ld-linux")
    }
    if not loader_interpreters:
        raise PreparationError("the Linux dynamic loader was not retained")
    for item in dynamic_elf_files(runtime_root):
        if item.parent == loader_root and item.name.startswith("ld-linux"):
            continue
        interpreter = patchelf_interpreter(item)
        run(
            [
                "/usr/bin/patchelf",
                "--set-rpath",
                str(loader_root),
                str(item),
            ]
        )
        if interpreter is not None:
            replacement = loader_interpreters.get(Path(interpreter).name)
            if replacement is None:
                raise PreparationError(f"no retained interpreter for {item}")
            run(
                [
                    "/usr/bin/patchelf",
                    "--set-interpreter",
                    str(replacement),
                    str(item),
                ]
            )

    dependency_audit: list[dict[str, Any]] = []
    for item in dynamic_elf_files(runtime_root):
        has_interpreter = patchelf_interpreter(item) is not None
        dependencies, output = ldd_paths(
            item,
            args.inspection_uid,
            args.inspection_gid,
        )
        external = [
            str(path)
            for path in dependencies
            if not path.is_relative_to(runtime_root)
            and (has_interpreter or not path.name.startswith("ld-linux"))
        ]
        if external:
            raise PreparationError(
                f"runtime ELF still resolves outside the closure: {item}: {external}"
            )
        dependency_audit.append(
            {
                "path": str(item),
                "ldd_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
                "resolved_paths": [str(path) for path in dependencies],
            }
        )

    pyyaml_after = finalize_installed_wheel(site_packages, pyyaml_before)
    native_paths = {
        str(site_packages.joinpath(*PurePosixPath(path).parts))
        for path in pyyaml_after["native_extensions"]
    }
    native_dependency_rows = [
        row for row in dependency_audit if row["path"] in native_paths
    ]
    if len(native_dependency_rows) != len(native_paths):
        raise PreparationError("PyYAML native dependency audit is incomplete")
    dynamic_libyaml = sorted(
        path
        for row in native_dependency_rows
        for path in row["resolved_paths"]
        if "libyaml" in Path(path).name.lower()
    )
    if dynamic_libyaml:
        raise PreparationError(
            "the pinned PyYAML wheel unexpectedly uses shared libyaml"
        )

    smoke = run(
        unprivileged_argv(
            args.inspection_uid,
            args.inspection_gid,
            [
                str(executable),
                "-I",
                "-B",
                "-c",
                (
                    "import hashlib,json,os,pwd,sqlite3,ssl,subprocess,sys;"
                    "import yaml,yaml._yaml as libyaml;"
                    "print(json.dumps({'implementation':sys.implementation.name,"
                    "'version':list(sys.version_info[:3]),"
                    "'executable':sys.executable,"
                    "'pyyaml':{'version':yaml.__version__,"
                    "'with_libyaml':yaml.__with_libyaml__,"
                    "'libyaml_version':list(libyaml.get_version()),"
                    "'yaml_path':yaml.__file__,"
                    "'native_path':libyaml.__file__}},"
                    "sort_keys=True,separators=(',',':')))"
                ),
            ],
            home="/nonexistent-task-witness-runtime-smoke-home",
        )
    )
    try:
        smoke_value = json.loads(smoke.stdout)
    except json.JSONDecodeError as error:
        raise PreparationError("runtime smoke output is invalid") from error
    if smoke_value.get("implementation") != "cpython":
        raise PreparationError("sealed runtime is not CPython")
    version = smoke_value.get("version")
    if version != [3, 13, 7]:
        raise PreparationError("sealed runtime is not exact CPython 3.13.7")
    pyyaml_smoke = smoke_value.get("pyyaml")
    expected_pyyaml_paths = {
        "yaml_path": str(site_packages / "yaml" / "__init__.py"),
        "native_path": str(
            site_packages.joinpath(
                *PurePosixPath(pyyaml_after["native_extensions"][0]).parts
            )
        ),
    }
    if (
        not isinstance(pyyaml_smoke, dict)
        or pyyaml_smoke.get("version") != PYYAML_VERSION
        or pyyaml_smoke.get("with_libyaml") is not True
        or not isinstance(pyyaml_smoke.get("libyaml_version"), list)
        or not pyyaml_smoke["libyaml_version"]
        or any(
            type(item) is not int or item < 0
            for item in pyyaml_smoke["libyaml_version"]
        )
        or any(
            pyyaml_smoke.get(field) != expected
            for field, expected in expected_pyyaml_paths.items()
        )
    ):
        raise PreparationError("sealed runtime PyYAML/libyaml smoke disagrees")

    patchelf_version = run(["/usr/bin/patchelf", "--version"]).stdout.strip()
    audit = content_document(
        {
            "schema_version": 1,
            "contract": "task-witness-linux-runtime-transformation-audit-v1",
            "authority": {
                "kind": "cooperative-operator-owned-workflow",
                "cryptographic_attestation": False,
                "product_attestation": False,
            },
            "source": {
                "action_sha": args.setup_python_action_sha,
                "cpython_version_request": args.runtime_version,
                "root": args.source_root,
                "qualification_dependencies": [
                    {
                        "distribution": PYYAML_DISTRIBUTION,
                        "version": PYYAML_VERSION,
                        "wheel_url": args.pyyaml_wheel_url,
                        "wheel_filename": PYYAML_WHEEL_FILENAME,
                        "wheel_length": wheel_metadata.st_size,
                        "wheel_sha256": PYYAML_WHEEL_SHA256,
                    }
                ],
            },
            "transformation": {
                "copy_semantics": "archive-dereference-symlinks",
                "patchelf_version": patchelf_version,
                "retained_loader_dependencies": copied,
                "runtime_root": str(runtime_root),
            },
            "inspection_identity": {
                "uid": args.inspection_uid,
                "gid": args.inspection_gid,
                "supplementary_gids": [],
                "capabilities": {
                    "ambient": 0,
                    "bounding": 0,
                    "effective": 0,
                    "inheritable": 0,
                    "permitted": 0,
                },
                "no_new_privs": True,
            },
            "dynamic_dependency_audit": dependency_audit,
            "qualification_dependencies": [
                {
                    **pyyaml_after,
                    "wheel_url": args.pyyaml_wheel_url,
                    "wheel_filename": PYYAML_WHEEL_FILENAME,
                    "wheel_length": wheel_metadata.st_size,
                    "wheel_sha256": PYYAML_WHEEL_SHA256,
                    "native_backend": {
                        "kind": "embedded-libyaml-in-wheel-extension",
                        "dynamic_libyaml_dependencies": dynamic_libyaml,
                        "libyaml_version": pyyaml_smoke["libyaml_version"],
                        "with_libyaml": True,
                    },
                }
            ],
            "smoke": smoke_value,
            "disposition": "qualified",
        }
    )
    write_create_new(audit_output, audit)


def linux_capabilities() -> dict[str, int]:
    observed: dict[str, int] = {}
    for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
        name, separator, raw = line.partition(":")
        if separator and name in CAPABILITY_FIELDS:
            observed[name] = int(raw.strip(), 16)
    if set(observed) != set(CAPABILITY_FIELDS):
        raise PreparationError("Linux capability projection is incomplete")
    return observed


def filesystem_probes(root: Path) -> dict[str, Any]:
    results: dict[str, Any] = {}
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    try:
        lock_path = root / "lock"
        lock_path.write_bytes(b"")
        held = os.open(lock_path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
        duplicate = os.dup(held)
        contender = os.open(lock_path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.close(held)
            held = -1
            blocked = False
            try:
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                blocked = True
            if not blocked:
                raise PreparationError("flock is not open-file-description scoped")
            os.close(duplicate)
            duplicate = -1
            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(contender, fcntl.LOCK_UN)
            results["advisory-flock-open-file-description"] = True
        finally:
            for descriptor in (held, duplicate, contender):
                if descriptor >= 0:
                    os.close(descriptor)

        source = root / "replace-source"
        target = root / "replace-target"
        source.write_bytes(b"new")
        target.write_bytes(b"old")
        old_inode = target.stat().st_ino
        os.replace(source, target)
        if target.read_bytes() != b"new" or target.stat().st_ino == old_inode:
            raise PreparationError("same-directory replace probe failed")
        results["atomic-same-directory-replace"] = True

        active_locale = locale.setlocale(locale.LC_ALL, "C.UTF-8")
        results["c-utf8-locale"] = active_locale

        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        results["directory-fsync"] = True

        descriptor = os.open(target, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            if not fcntl.fcntl(descriptor, fcntl.F_GETFD) & fcntl.FD_CLOEXEC:
                raise PreparationError("O_CLOEXEC probe failed")
        finally:
            os.close(descriptor)
        results["o-cloexec"] = True

        link = root / "replace-link"
        link.symlink_to(target.name)
        try:
            os.open(link, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as error:
            if error.errno != errno.ELOOP:
                raise
        else:
            raise PreparationError("O_NOFOLLOW followed a symlink")
        results["o-nofollow"] = True

        owned = root / "owner-mode"
        descriptor = os.open(owned, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        metadata = owned.stat()
        if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise PreparationError("owner/mode probe failed")
        results["owner-mode"] = True

        entry = pwd.getpwuid(os.geteuid())
        if entry.pw_uid != os.geteuid() or entry.pw_gid != os.getegid():
            raise PreparationError("passwd database probe failed")
        results["passwd-database"] = entry.pw_name

        read_descriptor, write_descriptor = os.pipe2(os.O_CLOEXEC)
        child = os.fork()
        if child == 0:
            try:
                os.close(read_descriptor)
                session = os.setsid()
                os.write(write_descriptor, str(session).encode("ascii"))
                os._exit(0)
            except (OSError, ValueError):
                os._exit(127)
        os.close(write_descriptor)
        session_raw = os.read(read_descriptor, 64)
        os.close(read_descriptor)
        _pid, status_value = os.waitpid(child, 0)
        if status_value != 0 or int(session_raw) != child:
            raise PreparationError("process session probe failed")
        results["process-session"] = True

        original_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})
        try:
            os.kill(os.getpid(), signal.SIGUSR1)
            if signal.SIGUSR1 not in signal.sigpending():
                raise PreparationError("pending signal probe failed")
            if signal.sigwait({signal.SIGUSR1}) != signal.SIGUSR1:
                raise PreparationError("signal wait probe failed")
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)
        results["signal-mask-pending"] = True

        child = os.fork()
        if child == 0:
            os._exit(0)
        observation = os.waitid(os.P_PID, child, os.WEXITED | os.WNOWAIT)
        if observation.si_pid != child:
            raise PreparationError("waitid WNOWAIT observation failed")
        reaped, _status = os.waitpid(child, 0)
        if reaped != child:
            raise PreparationError("waitid WNOWAIT reaping failed")
        results["waitid-wnowait"] = True
    finally:
        shutil.rmtree(root)
    if sorted(results) != FILESYSTEM_SEMANTICS:
        raise PreparationError("filesystem semantic probe inventory disagrees")
    return results


def probe_host(args: argparse.Namespace) -> None:
    if os.geteuid() == 0 or platform.system().lower() != "linux":
        raise PreparationError("host probe must run as an unprivileged Linux user")
    if platform.machine().lower() not in {"x86_64", "amd64"}:
        raise PreparationError("host probe requires Linux x86_64")
    capabilities = linux_capabilities()
    if any(capabilities.values()):
        raise PreparationError("host probe retained a Linux capability")
    if os.getresuid() != (os.geteuid(),) * 3 or os.getresgid() != (os.getegid(),) * 3:
        raise PreparationError("host probe retained a different saved identity")
    if os.getgroups():
        raise PreparationError("host probe retained supplementary groups")
    context, _context_raw = load_canonical(args.context)
    output = require_absolute(args.output_dir, "host audit output")
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    entry = pwd.getpwuid(os.geteuid())
    home = Path(entry.pw_dir)
    if home != output.parent or stat.S_IMODE(home.stat().st_mode) != 0o700:
        raise PreparationError("qualification home is not the private passwd home")

    no_new_privs = next(
        int(line.split(":", 1)[1].strip())
        for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines()
        if line.startswith("NoNewPrivs:")
    )
    if no_new_privs != 1:
        raise PreparationError("host probe did not retain no_new_privs")
    provisioning = content_document(
        {
            "schema_version": 1,
            "contract": "task-witness-linux-passwd-provisioning-audit-v1",
            "authority": {
                "kind": "cooperative-operator-owned-workflow",
                "cryptographic_attestation": False,
                "product_attestation": False,
            },
            "passwd_user": {
                "name": entry.pw_name,
                "uid": entry.pw_uid,
                "primary_gid": entry.pw_gid,
                "supplementary_gids": os.getgroups(),
                "home": entry.pw_dir,
                "shell": entry.pw_shell,
            },
            "credential_state": {
                "resuid": list(os.getresuid()),
                "resgid": list(os.getresgid()),
                "capabilities": capabilities,
                "no_new_privs": no_new_privs,
            },
            "disposition": "qualified",
        }
    )
    write_create_new(output / "provisioning-audit.json", provisioning, mode=0o600)

    container_markers = {
        "/.dockerenv": Path("/.dockerenv").exists(),
        "/run/.containerenv": Path("/run/.containerenv").exists(),
        "container_environment": bool(os.environ.get("container")),
    }
    if any(container_markers.values()):
        raise PreparationError("host probe detected a container")
    virtualization = run(
        ["/usr/bin/systemd-detect-virt", "--vm"],
        check=False,
    )
    if virtualization.returncode != 0 or virtualization.stdout.strip() != "microsoft":
        raise PreparationError("host probe did not observe the expected Azure VM")
    native = content_document(
        {
            "schema_version": 1,
            "contract": "task-witness-linux-native-host-audit-v1",
            "authority": {
                "kind": "cooperative-operator-owned-workflow",
                "cryptographic_attestation": False,
                "product_attestation": False,
            },
            "github_actions_context_sha256": hashlib.sha256(
                canonical_bytes(context)
            ).hexdigest(),
            "host": {
                "system": platform.system().lower(),
                "machine": platform.machine().lower(),
                "release": platform.release(),
                "version": platform.version(),
                "virtualization": {
                    "returncode": virtualization.returncode,
                    "stdout": virtualization.stdout.strip(),
                    "stderr": virtualization.stderr.strip(),
                },
                "container_markers": container_markers,
            },
            "conclusion": {"container": False, "emulation": False},
            "disposition": "qualified",
        }
    )
    write_create_new(output / "native-host-audit.json", native, mode=0o600)

    filesystem_root = home / ".task-witness-filesystem-probe"
    probe_results = filesystem_probes(filesystem_root)
    filesystem = content_document(
        {
            "schema_version": 1,
            "contract": "task-witness-linux-filesystem-audit-v1",
            "authority": {
                "kind": "cooperative-operator-owned-workflow",
                "cryptographic_attestation": False,
                "product_attestation": False,
            },
            "filesystem": {
                "type": run(
                    ["/usr/bin/stat", "--file-system", "--format=%T", str(home)]
                ).stdout.strip(),
                "probe_root": str(filesystem_root),
                "semantics": probe_results,
            },
            "disposition": "qualified",
        }
    )
    write_create_new(output / "filesystem-audit.json", filesystem, mode=0o600)


def live_entry(path: Path, root: Path, executable: Path) -> dict[str, Any]:
    metadata = path.lstat()
    common = {
        "path": str(path),
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
    }
    if stat.S_ISDIR(metadata.st_mode):
        return {
            **common,
            "kind": "directory",
            "role": "runtime-directory",
            "mode": stat.S_IMODE(metadata.st_mode),
        }
    if stat.S_ISLNK(metadata.st_mode):
        return {
            **common,
            "kind": "symlink",
            "role": "runtime-symlink",
            "target": os.readlink(path),
        }
    if not stat.S_ISREG(metadata.st_mode):
        raise PreparationError(f"runtime contains a special file: {path}")
    if path == executable:
        role = "main-executable"
    elif "site-packages" in path.parts:
        role = "qualification-python-package"
    elif is_elf(path):
        role = (
            "loader-shared-library"
            if "task-witness-loader" in path.parts
            else "cpython-extension-module"
        )
    elif path.suffix in {".py", ".pyi", ".pyc"}:
        role = "cpython-stdlib"
    else:
        role = "runtime-resource"
    return {
        **common,
        "kind": "regular-file",
        "role": role,
        "length": metadata.st_size,
        "sha256": sha256_file(path),
        "mode": stat.S_IMODE(metadata.st_mode),
    }


def validate_runtime_pyyaml_audit(
    runtime_audit: dict[str, Any],
    runtime_root: Path,
) -> None:
    dependencies = runtime_audit.get("qualification_dependencies")
    if not isinstance(dependencies, list) or len(dependencies) != 1:
        raise PreparationError("runtime PyYAML qualification audit is missing")
    dependency = dependencies[0]
    if not isinstance(dependency, dict):
        raise PreparationError("runtime PyYAML qualification audit is invalid")
    if (
        dependency.get("distribution") != PYYAML_DISTRIBUTION
        or dependency.get("version") != PYYAML_VERSION
        or dependency.get("wheel_filename") != PYYAML_WHEEL_FILENAME
        or dependency.get("wheel_sha256") != PYYAML_WHEEL_SHA256
        or dependency.get("wheel_url") != PYYAML_WHEEL_URL
        or dependency.get("record_verified_before_runtime_rewrite") is not True
        or dependency.get("record_verified_after_runtime_rewrite") is not True
    ):
        raise PreparationError("runtime PyYAML qualification identity disagrees")
    raw_site_packages = dependency.get("site_packages")
    if not isinstance(raw_site_packages, str):
        raise PreparationError("runtime PyYAML site-packages root disagrees")
    site_packages = Path(raw_site_packages)
    if not site_packages.is_absolute() or not site_packages.is_relative_to(
        runtime_root
    ):
        raise PreparationError("runtime PyYAML site-packages root disagrees")
    native_extensions = dependency.get("native_extensions")
    transformed = dependency.get("transformed_paths")
    if (
        not isinstance(native_extensions, list)
        or not native_extensions
        or transformed != native_extensions
    ):
        raise PreparationError("runtime PyYAML native extension audit disagrees")
    native_backend = dependency.get("native_backend")
    if (
        not isinstance(native_backend, dict)
        or native_backend.get("kind") != "embedded-libyaml-in-wheel-extension"
        or native_backend.get("dynamic_libyaml_dependencies") != []
        or native_backend.get("with_libyaml") is not True
    ):
        raise PreparationError("runtime PyYAML embedded libyaml audit disagrees")
    entries = dependency.get("entries")
    if not isinstance(entries, list) or not entries:
        raise PreparationError("runtime PyYAML file audit is incomplete")
    paths: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise PreparationError("runtime PyYAML file audit entry is invalid")
        relative = normalized_wheel_path(entry.get("path", ""), "PyYAML audit")
        value = relative.as_posix()
        path = site_packages.joinpath(*relative.parts)
        if (
            entry.get("final_sha256") != sha256_file(path)
            or entry.get("final_length") != path.stat().st_size
        ):
            raise PreparationError("runtime PyYAML final content audit disagrees")
        paths.append(value)
    if paths != sorted(set(paths)):
        raise PreparationError("runtime PyYAML file audit paths disagree")
    smoke = runtime_audit.get("smoke")
    pyyaml_smoke = smoke.get("pyyaml") if isinstance(smoke, dict) else None
    if (
        not isinstance(pyyaml_smoke, dict)
        or pyyaml_smoke.get("version") != PYYAML_VERSION
        or pyyaml_smoke.get("with_libyaml") is not True
        or pyyaml_smoke.get("libyaml_version") != native_backend.get("libyaml_version")
    ):
        raise PreparationError("runtime PyYAML smoke audit disagrees")


def tool_record(identifier: str, invoked: Path) -> dict[str, Any]:
    resolved = Path(os.path.realpath(invoked))
    metadata = resolved.stat()
    if not stat.S_ISREG(metadata.st_mode) or not os.access(resolved, os.X_OK):
        raise PreparationError(f"system tool is unavailable: {invoked}")
    return {
        "id": identifier,
        "invoked_path": str(invoked),
        "resolved_path": str(resolved),
        "length": metadata.st_size,
        "sha256": sha256_file(resolved),
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": stat.S_IMODE(metadata.st_mode),
    }


def build_evidence(args: argparse.Namespace) -> None:
    require_root()
    runtime_root = require_absolute(args.runtime_root, "runtime root")
    executable = require_absolute(args.runtime_executable, "runtime executable")
    candidate_root = require_absolute(args.candidate_root, "candidate root")
    host_audit_root = require_absolute(args.host_audit_root, "host audit root")
    output = require_absolute(args.output_dir, "evidence output")
    if output.exists():
        raise PreparationError("evidence output already exists")
    output.mkdir(mode=0o755, parents=False)
    context, context_raw = load_canonical(args.context)
    runtime_audit, runtime_audit_raw = load_canonical(args.runtime_audit)
    if runtime_audit.get("disposition") != "qualified":
        raise PreparationError("runtime transformation audit is not qualified")
    validate_runtime_pyyaml_audit(runtime_audit, runtime_root)

    audit_raw: dict[str, bytes] = {}
    audit_value: dict[str, dict[str, Any]] = {}
    for name in ("provisioning", "native-host", "filesystem"):
        source = host_audit_root / f"{name}-audit.json"
        value, raw = load_canonical(source)
        if value.get("disposition") != "qualified":
            raise PreparationError(f"{name} audit is not qualified")
        destination = output / source.name
        write_create_new(destination, value)
        audit_raw[name] = raw
        audit_value[name] = value
    write_create_new(output / "github-actions-context.json", context)
    write_create_new(output / "runtime-transformation-audit.json", runtime_audit)

    passwd_record = audit_value["provisioning"]["passwd_user"]
    system_tools = [tool_record(identifier, path) for identifier, path in SYSTEM_TOOLS]
    profile = content_document(
        {
            "schema_version": 1,
            "contract": "task-witness-platform-profile-v1",
            "target": "linux-x86_64",
            "execution_environment": "native",
            "platform": {
                "system": "linux",
                "machine": "x86_64",
                "qualified_filesystem_class": "local-private-filesystem",
            },
            "passwd_user": {
                "purpose": "task-witness-disposable-qualification-v1",
                "name": passwd_record["name"],
                "uid": passwd_record["uid"],
                "primary_gid": passwd_record["primary_gid"],
                "supplementary_gids": passwd_record["supplementary_gids"],
                "home": passwd_record["home"],
                "provisioning_evidence_sha256": hashlib.sha256(
                    audit_raw["provisioning"]
                ).hexdigest(),
            },
            "native_evidence": {
                "issuer": "nisavid-agents-qualification-harness",
                "provenance": "github-actions-hosted-vm-observation",
                "qualification_class": "task-witness-native-host-v1",
                "evidence_sha256": hashlib.sha256(audit_raw["native-host"]).hexdigest(),
                "container": False,
                "emulation": False,
            },
            "filesystem": {
                "type": audit_value["filesystem"]["filesystem"]["type"],
                "evidence_sha256": hashlib.sha256(audit_raw["filesystem"]).hexdigest(),
                "required_semantics": FILESYSTEM_SEMANTICS,
            },
            "system_tools": system_tools,
        }
    )
    write_create_new(output / "platform-profile.json", profile)

    if not runtime_root.is_dir() or not executable.is_file():
        raise PreparationError("sealed runtime is unavailable")
    if any(path.is_symlink() for path in runtime_root.rglob("*")):
        raise PreparationError("sealed runtime unexpectedly contains a symlink")
    paths = [runtime_root, *sorted(runtime_root.rglob("*"))]
    entries = [live_entry(path, runtime_root, executable) for path in paths]
    if [entry["path"] for entry in entries] != sorted(
        {entry["path"] for entry in entries}
    ):
        raise PreparationError("runtime inventory paths are not sorted and unique")
    regular_total = sum(
        entry["length"] for entry in entries if entry["kind"] == "regular-file"
    )
    smoke = runtime_audit.get("smoke")
    if not isinstance(smoke, dict) or smoke.get("executable") != str(executable):
        raise PreparationError("runtime transformation audit executable disagrees")
    version = smoke.get("version")
    if (
        smoke.get("implementation") != "cpython"
        or not isinstance(version, list)
        or len(version) != 3
        or any(type(item) is not int or item < 0 for item in version)
        or version != [3, 13, 7]
    ):
        raise PreparationError("runtime transformation audit version disagrees")
    executable_metadata = executable.stat()
    evidence = content_document(
        {
            "schema_version": 1,
            "contract": "task-witness-runtime-closure-evidence-v1",
            "authority": {
                "supplier": "actions-setup-python-and-pypi-wheel",
                "provenance": "github-actions-toolcache-plus-hashed-wheel-sealed-copy",
                "qualification_class": QUALIFICATION_CLASS,
                "issuer": "nisavid-agents-qualification-harness",
                "disposition": "qualified",
                "evidence_sha256": hashlib.sha256(runtime_audit_raw).hexdigest(),
            },
            "main_executable": {
                "path": str(executable),
                "length": executable_metadata.st_size,
                "sha256": sha256_file(executable),
                "uid": executable_metadata.st_uid,
                "gid": executable_metadata.st_gid,
                "mode": stat.S_IMODE(executable_metadata.st_mode),
                "implementation": "cpython",
                "version": {
                    "major": version[0],
                    "minor": version[1],
                    "micro": version[2],
                },
            },
            "closure": {
                "inventory_contract": "task-witness-runtime-closure-inventory-v1",
                "roots": [
                    {
                        "path": str(runtime_root),
                        "role": "cpython-runtime",
                        "complete_inventory": True,
                    }
                ],
                "dependency_classes": RUNTIME_DEPENDENCY_CLASSES,
                "entries": entries,
                "entries_sha256": hashlib.sha256(canonical_bytes(entries)).hexdigest(),
                "entry_count": len(entries),
                "total_regular_file_bytes": regular_total,
            },
        }
    )
    raw = canonical_bytes(evidence)
    if len(raw) > 1024 * 1024:
        raise PreparationError("runtime closure evidence exceeds the runner input cap")
    write_create_new(output / "runtime-closure-evidence.json", evidence)

    candidate = run(
        [
            "/usr/bin/git",
            "--no-replace-objects",
            "-c",
            f"safe.directory={candidate_root}",
            "-C",
            str(candidate_root),
            "rev-parse",
            "HEAD^{commit}",
        ]
    ).stdout.strip()
    if candidate != args.candidate_sha:
        raise PreparationError("candidate checkout SHA disagrees")
    summary = content_document(
        {
            "schema_version": 1,
            "contract": "task-witness-linux-qualification-input-summary-v1",
            "authority": {
                "kind": "cooperative-operator-owned-workflow",
                "cryptographic_attestation": False,
                "product_attestation": False,
            },
            "candidate": {"root": str(candidate_root), "commit_sha1": candidate},
            "inputs": {
                "platform_profile_sha256": sha256_file(
                    output / "platform-profile.json"
                ),
                "runtime_closure_evidence_sha256": sha256_file(
                    output / "runtime-closure-evidence.json"
                ),
                "github_actions_context_sha256": hashlib.sha256(
                    context_raw
                ).hexdigest(),
                "runtime_transformation_audit_sha256": hashlib.sha256(
                    runtime_audit_raw
                ).hexdigest(),
            },
            "disposition": "prepared",
        }
    )
    write_create_new(output / "input-summary.json", summary)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    commands = result.add_subparsers(dest="command", required=True)

    capture = commands.add_parser("capture-context")
    capture.add_argument("--output", type=Path, required=True)
    capture.set_defaults(function=lambda args: capture_context(args.output))

    verify = commands.add_parser("verify-runtime-source")
    verify.add_argument("--root", type=Path, required=True)
    verify.add_argument("--executable", type=Path, required=True)
    verify.set_defaults(function=verify_runtime_source)

    stage_helper = commands.add_parser("stage-bootstrap-helper")
    stage_helper.add_argument("--source", type=Path, required=True)
    stage_helper.add_argument("--output", type=Path, required=True)
    stage_helper.set_defaults(
        function=lambda args: print(stage_bootstrap_helper(args.source, args.output))
    )

    install = commands.add_parser("install-wheel")
    install.add_argument("--runtime-root", type=Path, required=True)
    install.add_argument("--site-packages", type=Path, required=True)
    install.add_argument("--wheel", type=Path, required=True)
    install.set_defaults(function=install_wheel)

    seal = commands.add_parser("seal-runtime")
    seal.add_argument("--runtime-root", type=Path, required=True)
    seal.add_argument("--runtime-executable", type=Path, required=True)
    seal.add_argument("--site-packages", type=Path, required=True)
    seal.add_argument("--dependency-wheel", type=Path, required=True)
    seal.add_argument("--audit-output", type=Path, required=True)
    seal.add_argument("--source-root", required=True)
    seal.add_argument("--runtime-version", required=True)
    seal.add_argument("--setup-python-action-sha", required=True)
    seal.add_argument("--pyyaml-version", required=True)
    seal.add_argument("--pyyaml-wheel-url", required=True)
    seal.add_argument("--inspection-uid", type=int, required=True)
    seal.add_argument("--inspection-gid", type=int, required=True)
    seal.set_defaults(function=seal_runtime)

    probe = commands.add_parser("probe-host")
    probe.add_argument("--context", type=Path, required=True)
    probe.add_argument("--output-dir", type=Path, required=True)
    probe.set_defaults(function=probe_host)

    build = commands.add_parser("build-evidence")
    build.add_argument("--candidate-root", type=Path, required=True)
    build.add_argument("--candidate-sha", required=True)
    build.add_argument("--runtime-root", type=Path, required=True)
    build.add_argument("--runtime-executable", type=Path, required=True)
    build.add_argument("--runtime-audit", type=Path, required=True)
    build.add_argument("--context", type=Path, required=True)
    build.add_argument("--host-audit-root", type=Path, required=True)
    build.add_argument("--output-dir", type=Path, required=True)
    build.set_defaults(function=build_evidence)

    write_manifest = commands.add_parser("write-artifact-manifest")
    write_manifest.add_argument("--artifact-root", type=Path, required=True)
    write_manifest.add_argument("--output", type=Path, required=True)
    write_manifest.set_defaults(
        function=lambda args: write_artifact_manifest(
            args.artifact_root,
            args.output,
        )
    )

    verify_manifest = commands.add_parser("verify-artifact-manifest")
    verify_manifest.add_argument("--artifact-root", type=Path, required=True)
    verify_manifest.add_argument("--manifest", type=Path, required=True)
    verify_manifest.set_defaults(
        function=lambda args: verify_artifact_manifest(
            args.artifact_root,
            args.manifest,
        )
    )
    return result


def main() -> int:
    try:
        args = parser().parse_args()
        args.function(args)
        return 0
    except PreparationError as error:
        print(f"task-witness Linux qualification preparation: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
