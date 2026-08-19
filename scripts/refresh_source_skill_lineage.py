#!/usr/bin/env python3
"""Refresh or receipt source-skill lineage evidence from explicit inputs."""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import errno
import hashlib
import json
import os
import platform
import secrets
import select
import signal
import stat
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

SCRIPT = Path(__file__).resolve()
VALIDATOR = SCRIPT.with_name("validate_source_skill_lineage.py")
PREPARATION_PREFIX = ".source-lineage-preparation-"
TRANSACTION_PREFIX = ".source-lineage-transaction-"
RECOVERY_QUARANTINE_PREFIX = ".source-lineage-quarantine-"
MAX_RECOVERY_QUARANTINE_ATTEMPTS = 128
TRANSACTION_MARKER = "owner"
TRANSACTION_CONTRACT = "coordinated-source-skill-lineage-transaction-v1"
TRUSTED_VALIDATOR_SIZE = 100_258
TRUSTED_VALIDATOR_SHA256 = (
    "8d2258e80b492995b3f002d9ce17cdcebee441186dc0f902cc0e3744625d0dba"
)
MATERIALIZE_TIMEOUT_SECONDS = 30.0
PROCESS_REAP_TIMEOUT_SECONDS = 1.0
MAX_TREE_ENTRIES = 10_000
MAX_TREE_LISTING_BYTES = 8 * 1024 * 1024
MAX_TREE_PATH_BYTES = 4 * 1024
MAX_TREE_COMPONENT_BYTES = 255
MAX_TREE_COMPONENTS = 256
MAX_TREE_TOTAL_COMPONENTS = 100_000
MAX_BLOB_BYTES = 32 * 1024 * 1024
MAX_UNIQUE_BLOB_BYTES = 128 * 1024 * 1024
MAX_MATERIALIZED_BYTES = 256 * 1024 * 1024
MAX_SYMLINK_BYTES = 4 * 1024
MAX_BATCH_RECORD_BYTES = 96
MAX_PROCESS_READ_BYTES = 64 * 1024
MAX_GIT_STDOUT_BYTES = 1024 * 1024
MAX_EXPLICIT_GIT_BLOB_BYTES = 4 * 1024 * 1024
MAX_PRIVATE_HOST_INPUT_BYTES = 1024 * 1024
TRUSTED_RECEIPT_PARENT = Path("/private/tmp" if sys.platform == "darwin" else "/tmp")
_HOST_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_HOST_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
_DARWIN_EVENT_ONLY_FLAG = 0x00008000
_NON_DIRECTORY_HANDLE_FLAG = 0x00200000  # Darwin O_SYMLINK / Linux O_PATH


class _MaterializationLimit(Exception):
    pass


class _FilesystemAlias(Exception):
    pass


class _UnsupportedGitPath(Exception):
    pass


class _NoReplaceCollision(Exception):
    pass


class _NoReplaceSourceDrift(Exception):
    pass


def _require_host_capture_time(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise lineage.LineageError("private host capture exceeds limits")


def _require_materialization_time(deadline: float, diagnostic: str) -> None:
    if time.monotonic() >= deadline:
        raise lineage.LineageError(diagnostic)


class _HostCaptureBudget:
    def __init__(self, deadline: float):
        self.deadline = deadline
        self.entries = 0
        self.listing_bytes = 0
        self.materialized_bytes = 0
        self.total_components = 0

    def reserve_entry(self) -> None:
        self.require_time()
        self.entries += 1
        if self.entries > MAX_TREE_ENTRIES:
            raise lineage.LineageError("private host capture exceeds limits")

    def reserve_path(self, relative: str) -> None:
        try:
            encoded = relative.encode("utf-8")
            components = relative.split("/")
            component_sizes = [
                len(component.encode("utf-8")) for component in components
            ]
        except UnicodeEncodeError:
            raise lineage.LineageError("private host capture exceeds limits") from None
        if (
            len(encoded) > MAX_TREE_PATH_BYTES
            or len(components) > MAX_TREE_COMPONENTS
            or self.listing_bytes + len(encoded) > MAX_TREE_LISTING_BYTES
            or self.total_components + len(components) > MAX_TREE_TOTAL_COMPONENTS
            or any(
                not component or size > MAX_TREE_COMPONENT_BYTES
                for component, size in zip(components, component_sizes)
            )
        ):
            raise lineage.LineageError("private host capture exceeds limits")
        self.listing_bytes += len(encoded)
        self.total_components += len(components)

    def reserve_bytes(self, size: int) -> None:
        if size < 0 or self.materialized_bytes + size > MAX_MATERIALIZED_BYTES:
            raise lineage.LineageError("private host capture exceeds limits")
        self.materialized_bytes += size

    def require_time(self) -> None:
        _require_host_capture_time(self.deadline)


class _GitPathIndex:
    def __init__(self, deadline: float):
        self.deadline = deadline
        self.root = {}
        self.total_components = 0

    def add(self, relative: str) -> None:
        encoded = relative.encode("utf-8")
        if len(encoded) > MAX_TREE_PATH_BYTES:
            raise _MaterializationLimit
        if any(character < " " or character > "~" for character in relative):
            raise _UnsupportedGitPath
        components = relative.split("/")
        self.total_components += len(components)
        if (
            len(components) > MAX_TREE_COMPONENTS
            or self.total_components > MAX_TREE_TOTAL_COMPONENTS
            or any(
                not component
                or len(component.encode("utf-8")) > MAX_TREE_COMPONENT_BYTES
                for component in components
            )
        ):
            raise _MaterializationLimit
        node = self.root
        for index, component in enumerate(components):
            if time.monotonic() >= self.deadline:
                raise _MaterializationLimit
            alias = component.lower()
            entry = node.get(alias)
            if entry is None:
                entry = [component, False, {}]
                node[alias] = entry
            elif entry[0] != component:
                raise _FilesystemAlias
            last = index == len(components) - 1
            if last:
                if entry[1] or entry[2]:
                    raise _FilesystemAlias
                entry[1] = True
            else:
                if entry[1]:
                    raise _FilesystemAlias
                node = entry[2]


def _git_blob_sha1(raw) -> str:
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(f"blob {len(raw)}\0".encode("ascii"))
    digest.update(raw)
    return digest.hexdigest()


def _validator_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_mode,
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _load_validator():
    descriptor = -1
    failed = False
    raw = b""
    try:
        descriptor = os.open(VALIDATOR, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size != TRUSTED_VALIDATOR_SIZE:
            raise ValueError
        captured = bytearray()
        while chunk := os.read(descriptor, 64 * 1024):
            captured.extend(chunk)
            if len(captured) > TRUSTED_VALIDATOR_SIZE:
                raise ValueError
        raw = bytes(captured)
        after = os.fstat(descriptor)
        visible = os.stat(VALIDATOR, follow_symlinks=False)
        if (
            _validator_identity(before) != _validator_identity(after)
            or _validator_identity(before) != _validator_identity(visible)
            or len(raw) != TRUSTED_VALIDATOR_SIZE
            or hashlib.sha256(raw).hexdigest() != TRUSTED_VALIDATOR_SHA256
        ):
            raise ValueError
    except BaseException:  # noqa: BLE001 - pathless trusted-loader boundary
        failed = True
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except BaseException:  # noqa: BLE001 - failed close cannot be retried
                failed = True
    if failed:
        raise SystemExit("source-skill-lineage validator cannot be loaded") from None
    execution_failed = False
    try:
        module = types.ModuleType("validate_source_skill_lineage")
        module.__file__ = str(VALIDATOR)
        exec(compile(raw, str(VALIDATOR), "exec"), module.__dict__)  # noqa: S102
    except BaseException:  # noqa: BLE001 - pathless trusted-loader boundary
        execution_failed = True
    if execution_failed:
        raise SystemExit("source-skill-lineage validator cannot be loaded") from None
    return module, raw


lineage, TRUSTED_VALIDATOR_BYTES = _load_validator()


def _wait_bounded_process(process: subprocess.Popen[bytes], deadline: float) -> int:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        try:
            return process.wait(timeout=remaining)
        except InterruptedError:
            continue
        except subprocess.TimeoutExpired:
            raise TimeoutError from None


def _kill_and_reap_bounded_process(process: subprocess.Popen[bytes]) -> None:
    signalling_failed = False
    group_signal_failed = False
    while True:
        try:
            os.killpg(process.pid, signal.SIGKILL)
            break
        except InterruptedError:
            continue
        except ProcessLookupError:
            group_signal_failed = True
            break
        except BaseException:  # noqa: BLE001 - cleanup must still reap the leader
            group_signal_failed = True
            break
    if group_signal_failed and process.poll() is None:
        signalling_failed = True
        try:
            process.kill()
        except BaseException:  # noqa: BLE001, S110 - fixed failure boundary
            pass
    try:
        _wait_bounded_process(
            process,
            time.monotonic() + PROCESS_REAP_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        raise RuntimeError from None
    if signalling_failed:
        raise RuntimeError


def _bounded_git_output(
    repository: Path,
    arguments: tuple[str, ...],
    *,
    stdin,
    output_limit: int,
    deadline: float,
) -> bytearray:
    process = None
    descriptor = None
    output = bytearray()
    failure = None
    reaped = False
    environment_directory = None
    try:
        if deadline <= time.monotonic():
            raise TimeoutError
        environment_directory = tempfile.TemporaryDirectory(
            prefix="source-lineage-git-", dir=TRUSTED_RECEIPT_PARENT
        )
        private_directory = environment_directory.name
        Path(private_directory).chmod(0o700)
        process = subprocess.Popen(
            [
                "/usr/bin/git",
                "--no-replace-objects",
                "-C",
                str(repository),
                *arguments,
            ],
            stdin=stdin if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
            umask=0o077,
            env={
                "GIT_ASKPASS": "/usr/bin/false",
                "GIT_CONFIG_COUNT": "3",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_KEY_0": "core.fsmonitor",
                "GIT_CONFIG_KEY_1": "core.hooksPath",
                "GIT_CONFIG_KEY_2": "core.pager",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_VALUE_0": "false",
                "GIT_CONFIG_VALUE_1": os.devnull,
                "GIT_CONFIG_VALUE_2": "cat",
                "GIT_NO_LAZY_FETCH": "1",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_PAGER": "cat",
                "GIT_TERMINAL_PROMPT": "0",
                "HOME": private_directory,
                "LANG": "C",
                "LC_ALL": "C",
                "NO_COLOR": "1",
                "PAGER": "cat",
                "PATH": "/usr/bin:/bin",
                "SSH_ASKPASS": "/usr/bin/false",
                "TMPDIR": private_directory,
            },
        )
        assert process.stdout is not None
        descriptor = process.stdout.fileno()
        os.set_blocking(descriptor, False)
        poller = select.poll()
        poller.register(descriptor, select.POLLIN | select.POLLHUP | select.POLLERR)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            try:
                events = poller.poll(max(1, int(remaining * 1000 + 0.999)))
            except InterruptedError:
                continue
            if not events:
                raise TimeoutError
            allowance = output_limit + 1 - len(output)
            if allowance <= 0:
                raise _MaterializationLimit
            try:
                chunk = os.read(descriptor, min(MAX_PROCESS_READ_BYTES, allowance))
            except BlockingIOError:
                continue
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > output_limit:
                raise _MaterializationLimit
        status = _wait_bounded_process(process, deadline)
        reaped = True
        if status != 0:
            failure = "unavailable"
    except _MaterializationLimit:
        failure = "limit"
    except BaseException:  # noqa: BLE001 - pathless subprocess boundary
        failure = "unavailable"
    if process is not None and failure == "limit" and not reaped:
        try:
            _wait_bounded_process(process, time.monotonic() + 0.1)
            reaped = True
        except TimeoutError:
            pass
    if process is not None and (failure is not None or not reaped):
        try:
            _kill_and_reap_bounded_process(process)
        except BaseException:  # noqa: BLE001 - pathless subprocess boundary
            failure = "unavailable"
    stdout = process.stdout if process is not None else None
    if stdout is not None:
        try:
            stdout.close()
        except BaseException:  # noqa: BLE001 - pathless subprocess boundary
            failure = "unavailable"
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
    if environment_directory is not None:
        try:
            environment_directory.cleanup()
        except BaseException:  # noqa: BLE001 - pathless subprocess boundary
            failure = "unavailable"
    if failure == "limit":
        raise lineage.LineageError(
            "committed repository snapshot exceeds materialization limits"
        ) from None
    if failure is not None:
        raise lineage.LineageError(
            "committed repository snapshot is unavailable"
        ) from None
    return output


def _git(repository: Path, *arguments: str, deadline: float | None = None) -> str:
    if deadline is None:
        deadline = time.monotonic() + MATERIALIZE_TIMEOUT_SECONDS
    try:
        raw = _bounded_git_output(
            repository,
            tuple(arguments),
            stdin=None,
            output_limit=MAX_GIT_STDOUT_BYTES,
            deadline=deadline,
        )
        return raw.decode("utf-8").strip()
    except (UnicodeDecodeError, lineage.LineageError):
        raise lineage.LineageError("refresh Git identity is unavailable") from None


def _load(raw: bytes, label: str, *, redact_parse_errors: bool = False) -> dict:
    try:
        value = lineage._strict_json(raw, label)
    except lineage.LineageError:
        if redact_parse_errors:
            raise lineage.LineageError(f"{label} is invalid") from None
        raise
    if type(value) is not dict:
        raise lineage.LineageError(f"{label} must be an object")
    return value


def _read_research_report_at(view, budget) -> bytes:
    with contextlib.ExitStack() as stack:
        parent_descriptor = view.root_descriptor
        try:
            for component in lineage.RESEARCH_REPORT.parts[:-1]:
                parent_descriptor = stack.enter_context(
                    lineage._open_directory_at(
                        parent_descriptor,
                        component,
                        budget=budget,
                    )
                )
            budget.require_time()
            metadata = os.stat(
                lineage.RESEARCH_REPORT.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except lineage.LineageError as error:
            if str(error) == lineage.VALIDATION_LIMIT_DIAGNOSTIC:
                raise
            raise lineage.LineageError(
                "source-lineage research report evidence drift"
            ) from None
        except FileNotFoundError:
            raise lineage.LineageError(
                "source-lineage research report evidence drift"
            ) from None
        except OSError:
            raise lineage.LineageError(
                "source-lineage research report is unavailable"
            ) from None
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise lineage.LineageError(
                "source-lineage research report must be a regular file"
            )
        try:
            report_raw, _ = lineage._read_regular_file_at(
                parent_descriptor,
                lineage.RESEARCH_REPORT.name,
                budget=budget,
                max_bytes=lineage.MAX_VALIDATION_FILE_BYTES,
            )
        except lineage.LineageError as error:
            if str(error) == lineage.VALIDATION_LIMIT_DIAGNOSTIC:
                raise
            raise lineage.LineageError(
                "source-lineage research report evidence drift"
            ) from None
    return lineage._validate_research_report_bytes(report_raw)


def _capture_checked_in_inputs(
    view, *, budget, lineage_descriptor: int | None = None
) -> tuple[dict[Path, bytes], dict]:
    lineage._require_lineage_view_binding(view)
    report_raw = _read_research_report_at(view, budget)
    if lineage_descriptor is None:
        snapshot = lineage._lineage_snapshot_at(
            view.release_descriptor,
            lineage.LINEAGE_ROOT.name,
            budget=budget,
        )
    else:
        snapshot = lineage._lineage_snapshot_descriptor(
            lineage_descriptor,
            budget=budget,
        )
    files = lineage._lineage_snapshot_files(snapshot)
    captured = {lineage.RESEARCH_REPORT: report_raw}
    for relative in lineage.LINEAGE_ARTIFACTS:
        captured[relative] = lineage._lineage_file(files, relative)
    lineage._require_lineage_view_binding(view)
    return captured, snapshot["identity"]


def _repository_evidence_receipts(
    captured: dict[Path, bytes],
) -> tuple[tuple[Path, str], ...]:
    source = _load(captured[lineage.SOURCE_MANIFEST], "source manifest")
    receipts = dict(lineage.CONTRIBUTION_EVIDENCE_RECEIPTS)
    additional = list(
        lineage._local_license_evidence_receipts(source["sources"]).items()
    )
    additional.extend(
        (artifact["path"], artifact["sha256"])
        for package in source["candidate"]["packages"]
        for artifact in package["identity_artifacts"]
    )
    for relative, digest in additional:
        previous = receipts.get(relative)
        if previous is not None and previous != digest:
            raise lineage.LineageError("source-lineage inputs changed while rendered")
        receipts[relative] = digest
    return tuple((Path(relative), receipts[relative]) for relative in sorted(receipts))


def _candidate_package_tree_receipts(
    captured: dict[Path, bytes],
) -> tuple[tuple[Path, dict[str, object]], ...]:
    source = _load(captured[lineage.SOURCE_MANIFEST], "source manifest")
    return tuple(
        (
            Path("plugins") / identifier,
            {
                "entry_count": package["entry_count"],
                "total_bytes": package["total_bytes"],
                "tree_sha256": package["package_tree_sha256"],
            },
        )
        for identifier, package in zip(
            lineage.DISTRIBUTIONS,
            source["candidate"]["packages"],
        )
    )


def _require_repository_evidence_receipts(view, receipts, budget) -> None:
    lineage._require_lineage_view_binding(view)
    for relative, expected_sha256 in receipts:
        raw = lineage._read_relative_file_at(
            view.root_descriptor,
            relative,
            budget=budget,
            max_bytes=lineage.MAX_VALIDATION_FILE_BYTES,
        )
        if "sha256:" + hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise lineage.LineageError("source-lineage inputs changed while rendered")
    lineage._require_lineage_view_binding(view)


def _require_candidate_package_tree_receipts(view, receipts, budget) -> None:
    lineage._require_lineage_view_binding(view)
    for relative, expected_identity in receipts:
        observed = lineage._tree_identity_relative_at(
            view.root_descriptor,
            relative,
            budget,
        )
        if observed != expected_identity:
            raise lineage.LineageError("source-lineage inputs changed while rendered")
    lineage._require_lineage_view_binding(view)


def _load_private_host_input(path: Path, deadline: float) -> dict:
    label = "private host capture input"
    if time.monotonic() >= deadline:
        raise lineage.LineageError("private host capture exceeds limits")
    try:
        before = path.lstat()
    except OSError:
        raise lineage.LineageError(f"{label} is unavailable") from None
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise lineage.LineageError(f"{label} must be a regular file")
    if before.st_size < 0 or before.st_size > MAX_PRIVATE_HOST_INPUT_BYTES:
        raise lineage.LineageError("private host capture exceeds limits")

    descriptor = None
    raw = bytearray()
    failure = None
    try:
        if time.monotonic() >= deadline:
            raise lineage.LineageError("private host capture exceeds limits")
        descriptor = os.open(path, _HOST_FILE_FLAGS)
        opened = os.fstat(descriptor)
        visible = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _validator_identity(opened) != _validator_identity(before)
            or _validator_identity(visible) != _validator_identity(before)
        ):
            raise lineage.LineageError(f"{label} cannot be read")
        while True:
            if time.monotonic() >= deadline:
                raise lineage.LineageError("private host capture exceeds limits")
            remaining = before.st_size - len(raw)
            if remaining < 0:
                raise lineage.LineageError(f"{label} cannot be read")
            chunk = os.read(
                descriptor,
                min(MAX_PROCESS_READ_BYTES, remaining + 1),
            )
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > before.st_size:
                raise lineage.LineageError(f"{label} cannot be read")
        after_opened = os.fstat(descriptor)
        after_visible = os.stat(path, follow_symlinks=False)
        if (
            len(raw) != before.st_size
            or _validator_identity(after_opened) != _validator_identity(before)
            or _validator_identity(after_visible) != _validator_identity(before)
        ):
            raise lineage.LineageError(f"{label} cannot be read")
    except lineage.LineageError as error:
        failure = error
    except OSError:
        failure = lineage.LineageError(f"{label} cannot be read")
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                if failure is None:
                    failure = lineage.LineageError(f"{label} cannot be read")
    if failure is not None:
        raise failure from None

    if time.monotonic() >= deadline:
        raise lineage.LineageError("private host capture exceeds limits")
    try:
        value = lineage._strict_json(bytes(raw), label, reject_numbers=True)
    except lineage.LineageError:
        raise lineage.LineageError(f"{label} is invalid") from None
    if time.monotonic() >= deadline:
        raise lineage.LineageError("private host capture exceeds limits")
    if type(value) is not dict:
        raise lineage.LineageError(f"{label} must be an object")
    return value


def _require_render_time(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise lineage.LineageError("source-lineage refresh exceeds limits")


def _candidate_projection_seed(source: dict) -> tuple[str, list[dict]]:
    diagnostic = "source-lineage refresh input schema drift"
    candidate = source.get("candidate")
    candidate_fields = {
        "basis",
        "package_projection_contract",
        "packages",
        "packages_sha256",
        "repository_id",
    }
    lineage.require(
        type(candidate) is dict
        and {"basis", "package_projection_contract", "packages", "repository_id"}
        <= set(candidate)
        <= candidate_fields
        and candidate["repository_id"] == "nisavid/agents"
        and candidate["package_projection_contract"] == "agent-plugin-tree-v1",
        diagnostic,
    )
    basis = candidate["basis"]
    lineage.require(
        type(basis) is dict
        and set(basis) == {"commit_sha1", "tree_sha1"}
        and basis["commit_sha1"] == lineage.CANDIDATE_COMMIT_SHA1
        and basis["tree_sha1"] == lineage.CANDIDATE_TREE_SHA1,
        diagnostic,
    )
    packages = candidate["packages"]
    lineage.require(
        type(packages) is list and len(packages) == len(lineage.DISTRIBUTIONS),
        diagnostic,
    )
    package_fields = {
        "entry_count",
        "git_tree_sha1",
        "id",
        "identity_artifacts",
        "package_tree_sha256",
        "plugin_manifest_sha256",
        "plugin_root",
        "total_bytes",
        "version",
    }
    for identifier, package in zip(lineage.DISTRIBUTIONS, packages):
        lineage.require(
            type(package) is dict
            and {"id", "identity_artifacts"} <= set(package) <= package_fields
            and package["id"] == identifier,
            diagnostic,
        )
        artifacts = package["identity_artifacts"]
        expected_paths = tuple(
            path for path, _ in lineage.CANDIDATE_PACKAGE_RECEIPTS[identifier][5]
        )
        lineage.require(
            type(artifacts) is list and len(artifacts) == len(expected_paths),
            diagnostic,
        )
        observed_paths = []
        for artifact in artifacts:
            lineage.require(
                type(artifact) is dict
                and "path" in artifact
                and set(artifact) <= {"path", "sha256"}
                and type(artifact["path"]) is str,
                diagnostic,
            )
            observed_paths.append(artifact["path"])
        lineage.require(tuple(observed_paths) == expected_paths, diagnostic)
    return basis["commit_sha1"], packages


def _package_projection(
    repository: Path,
    package: dict,
    commit: str,
    *,
    deadline: float,
) -> dict:
    identifier = package["id"]
    root_relative = f"plugins/{identifier}"
    identity = _git_tree_identity(repository, commit, root_relative, deadline=deadline)
    manifest_raw = _git_blob(
        repository,
        commit,
        f"{root_relative}/plugin.json",
        deadline=deadline,
    )
    manifest = lineage._strict_json(manifest_raw, "candidate plugin manifest")
    artifacts = []
    for artifact in package["identity_artifacts"]:
        relative = artifact["path"]
        artifacts.append(
            {
                "path": relative,
                "sha256": "sha256:"
                + hashlib.sha256(
                    _git_blob(repository, commit, relative, deadline=deadline)
                ).hexdigest(),
            }
        )
    return {
        "entry_count": identity["entry_count"],
        "git_tree_sha1": identity["tree_sha1"],
        "id": identifier,
        "identity_artifacts": artifacts,
        "package_tree_sha256": identity["skill_tree_sha256"],
        "plugin_manifest_sha256": "sha256:" + hashlib.sha256(manifest_raw).hexdigest(),
        "plugin_root": root_relative,
        "total_bytes": identity["total_bytes"],
        "version": manifest["version"],
    }


def render_checked_in_documents(
    repository: Path,
    captured: dict[Path, bytes],
    *,
    deadline: float | None = None,
) -> dict[Path, bytes]:
    if deadline is None:
        deadline = time.monotonic() + MATERIALIZE_TIMEOUT_SECONDS
    _require_render_time(deadline)
    report_raw = captured[lineage.RESEARCH_REPORT]
    source = _load(captured[lineage.SOURCE_MANIFEST], "source manifest")
    lineage._privacy_scan(source, "source manifest")
    commit, packages = _candidate_projection_seed(source)
    source["candidate"]["packages"] = [
        _package_projection(repository, package, commit, deadline=deadline)
        for package in packages
    ]
    _require_render_time(deadline)
    source["candidate"]["packages_sha256"] = lineage.canonical_sha256(
        source["candidate"]["packages"]
    )
    source["content_sha256"] = lineage.content_sha256(source)
    source_raw = lineage.content_document(source)
    source_digest = "sha256:" + hashlib.sha256(source_raw).hexdigest()
    sources = {item["id"]: item for item in source["sources"]}

    ledger = _load(captured[lineage.CONTRIBUTION_LEDGER], "contribution ledger")
    lineage._privacy_scan(ledger, "contribution ledger")
    ledger["contributions"].sort(key=lambda item: item["id"])
    ledger["source_manifest"]["sha256"] = source_digest
    ledger["content_sha256"] = lineage.content_sha256(ledger)
    rendered = {
        lineage.SOURCE_MANIFEST: source_raw,
        lineage.CONTRIBUTION_LEDGER: lineage.content_document(ledger),
        lineage.RESEARCH_REPORT: report_raw,
    }
    for profile_id, relative in lineage.HOST_MANIFESTS.items():
        _require_render_time(deadline)
        host = _load(captured[relative], f"installed-host manifest: {profile_id}")
        lineage._privacy_scan(host, "installed-host manifest")
        host["source_manifest"]["sha256"] = source_digest
        for observation in host["source_observations"]:
            if observation["status"] == "installed":
                for installation in observation["installations"]:
                    installation["matched_snapshots"] = lineage._matched_snapshots(
                        installation, sources[observation["source_id"]]
                    )
                observation["installations"] = lineage._assigned_host_routes(
                    observation["source_id"], observation["installations"]
                )
        host["content_sha256"] = lineage.content_sha256(host)
        rendered[relative] = lineage.content_document(host)
    _require_render_time(deadline)
    return rendered


def _stable_render(repository: Path, captured: dict[Path, bytes]) -> dict[Path, bytes]:
    deadline = time.monotonic() + MATERIALIZE_TIMEOUT_SECONDS
    try:
        _require_render_time(deadline)
        first = render_checked_in_documents(repository, captured, deadline=deadline)
        _require_render_time(deadline)
        second = render_checked_in_documents(repository, captured, deadline=deadline)
        _require_render_time(deadline)
    except lineage.LineageError:
        raise
    except (
        AttributeError,
        IndexError,
        KeyError,
        RecursionError,
        TypeError,
        ValueError,
    ):
        raise lineage.LineageError(
            "source-lineage refresh input schema drift"
        ) from None
    if first != second:
        raise lineage.LineageError("source-lineage inputs changed while rendered")
    return first


def _validate_rendered(repository: Path, rendered: dict[Path, bytes]) -> None:
    try:
        with tempfile.TemporaryDirectory(
            prefix="source-lineage-render-", dir=TRUSTED_RECEIPT_PARENT
        ) as directory:
            artifacts_root = Path(directory)
            for relative, content in rendered.items():
                _atomic_write(artifacts_root / relative, content)
            lineage.validate_lineage(repository, artifacts_root, acquire_lock=False)
    except lineage.LineageError:
        raise
    except (OSError, RuntimeError):
        raise lineage.LineageError(
            "source-lineage rendered validation failed"
        ) from None


def _check_locked(repository: Path, view) -> None:
    lineage._require_lineage_view_binding(view)
    budget = lineage._new_validation_budget()
    captured, captured_identity = _capture_checked_in_inputs(view, budget=budget)
    lineage._require_lineage_view_binding(view)
    _validate_rendered(repository, captured)
    repository_evidence_receipts = _repository_evidence_receipts(captured)
    candidate_package_tree_receipts = _candidate_package_tree_receipts(captured)
    lineage._require_lineage_view_binding(view)
    rendered = _stable_render(repository, captured)
    lineage._require_lineage_view_binding(view)
    for relative, expected in rendered.items():
        if captured[relative] != expected:
            raise lineage.LineageError(
                f"checked-in source-lineage artifact is stale: {relative.as_posix()}"
            )
    lineage._require_lineage_view_binding(view)
    try:
        observed, observed_identity = _capture_checked_in_inputs(view, budget=budget)
        _require_repository_evidence_receipts(
            view,
            repository_evidence_receipts,
            budget,
        )
        _require_candidate_package_tree_receipts(
            view,
            candidate_package_tree_receipts,
            budget,
        )
    except lineage.LineageError as error:
        if str(error) == lineage.VALIDATION_LIMIT_DIAGNOSTIC:
            raise
        raise lineage.LineageError(
            "source-lineage inputs changed while rendered"
        ) from None
    if observed != captured or observed_identity != captured_identity:
        raise lineage.LineageError("source-lineage inputs changed while rendered")
    lineage._require_lineage_view_binding(view)


def check(repository: Path) -> None:
    with lineage._lineage_lock(repository, exclusive=False) as view:
        _check_locked(view.root, view)


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | os.O_DIRECTORY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)


class _DirectoryHandle:
    __slots__ = ("descriptor", "identity")

    def __init__(self, descriptor: int, identity: tuple[int, int]):
        self.descriptor = descriptor
        self.identity = identity


class _NonDirectoryHandle:
    __slots__ = ("descriptor", "identity")

    def __init__(self, descriptor: int | None, identity: tuple[int, ...]):
        self.descriptor = descriptor
        self.identity = identity


class _GenerationBinding:
    __slots__ = (
        "diagnostic",
        "directory",
        "expected_identity",
        "parent_descriptor",
        "report_raw",
    )

    def __init__(
        self,
        parent_descriptor: int,
        directory: _DirectoryHandle,
        expected_identity: dict | None,
        report_raw: bytes,
        diagnostic: str,
    ):
        self.parent_descriptor = parent_descriptor
        self.directory = directory
        self.expected_identity = expected_identity
        self.report_raw = report_raw
        self.diagnostic = diagnostic


class _TransactionHandle:
    __slots__ = ("directory", "marker_identity", "name")

    def __init__(self, name: str, directory: _DirectoryHandle):
        self.name = name
        self.directory = directory
        self.marker_identity = None


def _directory_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _single_component(name: str, diagnostic: str) -> str:
    if (
        type(name) is not str
        or not name
        or name in {".", ".."}
        or "/" in name
        or "\0" in name
    ):
        raise lineage.LineageError(diagnostic)
    return name


def _open_directory_at(
    parent_descriptor: int, name: str, diagnostic: str
) -> _DirectoryHandle:
    name = _single_component(name, diagnostic)
    descriptor = None
    try:
        before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
            raise lineage.LineageError(diagnostic)
        descriptor = os.open(
            name,
            _DIRECTORY_FLAGS,
            dir_fd=parent_descriptor,
        )
        opened = os.fstat(descriptor)
        after = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        identity = _directory_identity(opened)
        if any(
            _directory_identity(item) != identity for item in (before, opened, after)
        ):
            raise lineage.LineageError(diagnostic)
        return _DirectoryHandle(descriptor, identity)
    except lineage.LineageError:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise
    except OSError:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise lineage.LineageError(diagnostic) from None


def _close_directory(handle: _DirectoryHandle | None, diagnostic: str) -> None:
    if handle is None:
        return
    try:
        os.close(handle.descriptor)
    except OSError:
        raise lineage.LineageError(diagnostic) from None


def _close_directories(
    *handles: _DirectoryHandle | None,
    diagnostic: str,
) -> None:
    failed = False
    for handle in handles:
        if handle is None:
            continue
        try:
            os.close(handle.descriptor)
        except OSError:
            failed = True
    if failed:
        raise lineage.LineageError(diagnostic) from None


def _require_directory_name(
    parent_descriptor: int,
    name: str,
    handle: _DirectoryHandle,
    diagnostic: str,
) -> None:
    try:
        visible = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        opened = os.fstat(handle.descriptor)
    except OSError:
        raise lineage.LineageError(diagnostic) from None
    if (
        not stat.S_ISDIR(visible.st_mode)
        or stat.S_ISLNK(visible.st_mode)
        or _directory_identity(visible) != handle.identity
        or _directory_identity(opened) != handle.identity
    ):
        raise lineage.LineageError(diagnostic)


def _open_non_directory_at(
    parent_descriptor: int,
    name: str,
    diagnostic: str,
) -> _NonDirectoryHandle:
    name = _single_component(name, diagnostic)
    descriptor = None
    try:
        before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if stat.S_ISDIR(before.st_mode):
            raise lineage.LineageError(diagnostic)
        if sys.platform == "darwin" and (
            stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode)
        ):
            flags = (
                getattr(os, "O_EVTONLY", _DARWIN_EVENT_ONLY_FLAG)
                | _NON_DIRECTORY_HANDLE_FLAG
                | os.O_NONBLOCK
                | os.O_CLOEXEC
            )
        elif sys.platform.startswith("linux"):
            flags = (
                getattr(os, "O_PATH", _NON_DIRECTORY_HANDLE_FLAG)
                | os.O_NOFOLLOW
                | os.O_CLOEXEC
            )
        elif sys.platform != "darwin":
            raise lineage.LineageError(diagnostic)
        else:
            flags = None
        if flags is not None:
            try:
                descriptor = os.open(name, flags, dir_fd=parent_descriptor)
            except OSError as error:
                if sys.platform != "darwin" or error.errno is None:
                    raise
                fallback = os.stat(
                    name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if (
                    stat.S_ISDIR(fallback.st_mode)
                    or stat.S_ISREG(fallback.st_mode)
                    or stat.S_ISLNK(fallback.st_mode)
                ):
                    raise
                identity = _validator_identity(fallback)
            else:
                opened = os.fstat(descriptor)
                identity = _validator_identity(opened)
                if stat.S_ISDIR(opened.st_mode):
                    raise lineage.LineageError(diagnostic)
        else:
            identity = _validator_identity(before)
        after = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if stat.S_ISDIR(after.st_mode) or _validator_identity(after) != identity:
            raise lineage.LineageError(diagnostic)
        return _NonDirectoryHandle(descriptor, identity)
    except lineage.LineageError:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise
    except OSError:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise lineage.LineageError(diagnostic) from None


def _require_non_directory_name(
    parent_descriptor: int,
    name: str,
    handle: _NonDirectoryHandle,
    diagnostic: str,
) -> None:
    try:
        visible = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        opened_identity = (
            handle.identity
            if handle.descriptor is None
            else _validator_identity(os.fstat(handle.descriptor))
        )
    except OSError:
        raise lineage.LineageError(diagnostic) from None
    if (
        stat.S_ISDIR(visible.st_mode)
        or _validator_identity(visible) != handle.identity
        or opened_identity != handle.identity
    ):
        raise lineage.LineageError(diagnostic)


def _rebind_non_directory_name_after_move(
    parent_descriptor: int,
    name: str,
    handle: _NonDirectoryHandle,
    diagnostic: str,
) -> None:
    try:
        visible_before = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        opened_identity = (
            _validator_identity(visible_before)
            if handle.descriptor is None
            else _validator_identity(os.fstat(handle.descriptor))
        )
        visible_after = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except OSError:
        raise lineage.LineageError(diagnostic) from None
    visible_identity = _validator_identity(visible_before)
    if (
        stat.S_ISDIR(visible_before.st_mode)
        or stat.S_ISDIR(visible_after.st_mode)
        or visible_identity != _validator_identity(visible_after)
        or opened_identity != visible_identity
        or visible_identity[:-1] != handle.identity[:-1]
    ):
        raise lineage.LineageError(diagnostic)
    handle.identity = visible_identity


def _non_directory_name_matches(
    parent_descriptor: int,
    name: str,
    handle: _NonDirectoryHandle,
) -> bool:
    try:
        _require_non_directory_name(
            parent_descriptor,
            name,
            handle,
            "source-lineage recovery state is ambiguous",
        )
    except lineage.LineageError:
        return False
    return True


def _close_non_directory(
    handle: _NonDirectoryHandle | None,
    diagnostic: str,
) -> None:
    if handle is None or handle.descriptor is None:
        return
    try:
        os.close(handle.descriptor)
    except OSError:
        raise lineage.LineageError(diagnostic) from None


def _require_generation_name(
    parent_descriptor: int,
    name: str,
    handle: _DirectoryHandle,
    expected_identity: dict,
    diagnostic: str,
) -> None:
    _require_directory_name(parent_descriptor, name, handle, diagnostic)
    observed_identity = lineage._lineage_snapshot_descriptor(handle.descriptor)[
        "identity"
    ]
    _require_directory_name(parent_descriptor, name, handle, diagnostic)
    if observed_identity != expected_identity:
        raise lineage.LineageError(diagnostic)


def _generation_name_matches(
    parent_descriptor: int,
    name: str,
    handle: _DirectoryHandle,
    expected_identity: dict,
) -> bool:
    try:
        _require_generation_name(
            parent_descriptor,
            name,
            handle,
            expected_identity,
            "source-lineage artifact rollback failed",
        )
    except lineage.LineageError:
        return False
    return True


def _directory_name_matches(
    parent_descriptor: int,
    name: str,
    handle: _DirectoryHandle,
) -> bool:
    try:
        _require_directory_name(
            parent_descriptor,
            name,
            handle,
            "source-lineage artifact rollback failed",
        )
    except lineage.LineageError:
        return False
    return True


def _directory_exists_at(parent_descriptor: int, name: str) -> bool:
    name = _single_component(name, "source-lineage recovery state is ambiguous")
    try:
        metadata = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError:
        raise lineage.LineageError(
            "source-lineage recovery state is ambiguous"
        ) from None
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise lineage.LineageError("source-lineage recovery state is ambiguous")
    return True


def _name_exists_at(parent_descriptor: int, name: str) -> bool:
    name = _single_component(name, "source-lineage recovery state is ambiguous")
    try:
        os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError:
        raise lineage.LineageError(
            "source-lineage recovery state is ambiguous"
        ) from None
    return True


def _read_regular_at(
    parent_descriptor: int,
    name: str,
    expected_size: int,
    diagnostic: str,
) -> tuple[bytes, tuple[int, ...]]:
    name = _single_component(name, diagnostic)
    descriptor = None
    failed = False
    raw = b""
    try:
        visible_before = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(visible_before.st_mode)
            or stat.S_ISLNK(visible_before.st_mode)
            or visible_before.st_size != expected_size
        ):
            raise OSError
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        before = os.fstat(descriptor)
        visible_after_open = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size != expected_size
            or _validator_identity(before) != _validator_identity(visible_before)
            or _validator_identity(visible_after_open)
            != _validator_identity(visible_before)
        ):
            raise OSError
        captured = bytearray()
        while chunk := os.read(descriptor, 64 * 1024):
            captured.extend(chunk)
            if len(captured) > expected_size:
                raise OSError
        after = os.fstat(descriptor)
        visible_after_read = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        raw = bytes(captured)
        if (
            _validator_identity(after) != _validator_identity(visible_before)
            or _validator_identity(visible_after_read)
            != _validator_identity(visible_before)
            or len(raw) != expected_size
        ):
            raise OSError
    except OSError:
        failed = True
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                failed = True
    if failed:
        raise lineage.LineageError(diagnostic) from None
    return raw, _validator_identity(visible_before)


def _require_transaction_marker(
    transaction: _TransactionHandle,
    diagnostic: str = "source-lineage recovery state is unowned",
) -> None:
    if transaction.marker_identity is None:
        raise lineage.LineageError(diagnostic)
    try:
        visible = os.stat(
            TRANSACTION_MARKER,
            dir_fd=transaction.directory.descriptor,
            follow_symlinks=False,
        )
    except OSError:
        raise lineage.LineageError(diagnostic) from None
    if (
        not stat.S_ISREG(visible.st_mode)
        or stat.S_ISLNK(visible.st_mode)
        or _validator_identity(visible) != transaction.marker_identity
    ):
        raise lineage.LineageError(diagnostic)


def _fsync_descriptor(descriptor: int, diagnostic: str) -> None:
    try:
        os.fsync(descriptor)
    except OSError:
        raise lineage.LineageError(diagnostic) from None


def _rename_at(
    source_parent: int,
    source_name: str,
    destination_parent: int,
    destination_name: str,
    *,
    applied=None,
    diagnostic: str = "source-lineage artifact publication failed",
) -> None:
    source_name = _single_component(source_name, diagnostic)
    destination_name = _single_component(destination_name, diagnostic)
    try:
        os.rename(
            source_name,
            destination_name,
            src_dir_fd=source_parent,
            dst_dir_fd=destination_parent,
        )
        if applied is not None:
            applied()
        os.fsync(destination_parent)
        if _directory_identity(os.fstat(source_parent)) != _directory_identity(
            os.fstat(destination_parent)
        ):
            os.fsync(source_parent)
    except OSError:
        raise lineage.LineageError(diagnostic) from None


def _rename_noreplace_at(
    source_parent: int,
    source_name: str,
    destination_parent: int,
    destination_name: str,
    *,
    applied=None,
    diagnostic: str = "source-lineage recovery state is ambiguous",
) -> None:
    source_name = _single_component(source_name, diagnostic)
    destination_name = _single_component(destination_name, diagnostic)
    source_raw = os.fsencode(source_name)
    destination_raw = os.fsencode(destination_name)
    try:
        library = ctypes.CDLL(None, use_errno=True)
        ctypes.set_errno(0)
        if sys.platform == "darwin":
            native_rename = library.renameatx_np
            native_rename.argtypes = (
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            native_rename.restype = ctypes.c_int
            result = native_rename(
                source_parent,
                source_raw,
                destination_parent,
                destination_raw,
                0x04 | 0x10,  # RENAME_EXCL | RENAME_NOFOLLOW_ANY
            )
        elif sys.platform.startswith("linux"):
            try:
                native_rename = library.renameat2
            except AttributeError:
                machine = platform.machine().lower()
                syscall_numbers = {
                    "x86_64": 316,
                    "amd64": 316,
                    "aarch64": 276,
                    "arm64": 276,
                    "riscv64": 276,
                }
                if machine not in syscall_numbers:
                    raise OSError(errno.ENOSYS, os.strerror(errno.ENOSYS))
                library.syscall.restype = ctypes.c_long
                result = library.syscall(
                    syscall_numbers[machine],
                    source_parent,
                    source_raw,
                    destination_parent,
                    destination_raw,
                    1,  # RENAME_NOREPLACE
                )
            else:
                native_rename.argtypes = (
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_uint,
                )
                native_rename.restype = ctypes.c_int
                result = native_rename(
                    source_parent,
                    source_raw,
                    destination_parent,
                    destination_raw,
                    1,  # RENAME_NOREPLACE
                )
        else:
            raise OSError(errno.ENOSYS, os.strerror(errno.ENOSYS))
        if result != 0:
            error_number = ctypes.get_errno() or errno.EIO
            if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
                raise _NoReplaceCollision
            raise OSError(error_number, os.strerror(error_number))
        if applied is not None:
            applied()
        os.fsync(destination_parent)
        if _directory_identity(os.fstat(source_parent)) != _directory_identity(
            os.fstat(destination_parent)
        ):
            os.fsync(source_parent)
    except _NoReplaceCollision:
        raise
    except (AttributeError, OSError):
        raise lineage.LineageError(diagnostic) from None


def _new_recovery_quarantine_at(parent: _DirectoryHandle, budget):
    diagnostic = "source-lineage recovery state is ambiguous"
    budget.require_time()
    name = f"{RECOVERY_QUARANTINE_PREFIX}{secrets.token_hex(8)}"
    budget.reserve_tree_entry()
    budget.reserve_path(name)
    try:
        os.mkdir(name, 0o700, dir_fd=parent.descriptor)
    except FileExistsError:
        raise _NoReplaceCollision from None
    except OSError:
        raise lineage.LineageError(diagnostic) from None
    _fsync_descriptor(parent.descriptor, diagnostic)
    directory = _open_directory_at(parent.descriptor, name, diagnostic)
    try:
        metadata = os.fstat(directory.descriptor)
        if stat.S_IMODE(metadata.st_mode) != 0o700 or metadata.st_uid != os.geteuid():
            raise lineage.LineageError(diagnostic)
        _require_directory_name(
            parent.descriptor,
            name,
            directory,
            diagnostic,
        )
    except BaseException:
        _close_directory(directory, diagnostic)
        raise
    return name, directory


def _durable_replace(source: Path, destination: Path, applied=None) -> None:
    source_parent = source.parent
    destination_parent = destination.parent
    os.replace(source, destination)
    if applied is not None:
        applied()
    _fsync_directory(destination_parent)
    if source_parent != destination_parent:
        _fsync_directory(source_parent)


def _fsync_directory_tree(root: Path) -> None:
    directories = [root]
    for path in root.rglob("*"):
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            directories.append(path)
    directories.sort(key=lambda path: len(path.parts), reverse=True)
    for directory in directories:
        _fsync_directory(directory)


def _transaction_marker(transaction: Path | str) -> bytes:
    name = Path(os.fspath(transaction)).name
    return f"{TRANSACTION_CONTRACT} {name}\n".encode("ascii")


@contextlib.contextmanager
def _bounded_directory_scan(
    descriptor: int,
    budget,
    diagnostic: str,
):
    scanner = None
    try:
        budget.require_time()
        scanner = os.scandir(descriptor)
        yield scanner
        budget.require_time()
    except lineage.LineageError:
        raise
    except OSError:
        raise lineage.LineageError(diagnostic) from None
    finally:
        active_failure = sys.exc_info()[0] is not None
        if scanner is not None:
            try:
                scanner.close()
            except OSError:
                if not active_failure:
                    raise lineage.LineageError(diagnostic) from None


def _bounded_directory_entry_name(entry, budget, diagnostic: str) -> str:
    try:
        name = entry.name
    except (AttributeError, OSError):
        raise lineage.LineageError(diagnostic) from None
    if type(name) is not str:
        raise lineage.LineageError(diagnostic)
    budget.reserve_tree_entry()
    budget.reserve_path(name)
    return name


def _require_owned_transaction_descriptor(
    transaction: _TransactionHandle,
    budget=None,
) -> None:
    if budget is None:
        budget = lineage._new_tree_budget("source-lineage recovery state is unowned")
    descriptor = transaction.directory.descriptor
    expected_marker = _transaction_marker(transaction.name)
    try:
        budget.require_time()
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.geteuid()
        ):
            raise lineage.LineageError("source-lineage recovery state is unowned")
        marker_raw, marker_identity = _read_regular_at(
            descriptor,
            TRANSACTION_MARKER,
            len(expected_marker),
            "source-lineage recovery state is unowned",
        )
        budget.require_time()
        if marker_raw != expected_marker:
            raise lineage.LineageError("source-lineage recovery state is unowned")
        if (
            transaction.marker_identity is not None
            and marker_identity != transaction.marker_identity
        ):
            raise lineage.LineageError("source-lineage recovery state is unowned")
        marker_seen = False
        with _bounded_directory_scan(
            descriptor,
            budget,
            budget.diagnostic,
        ) as children:
            for entry in children:
                child = _bounded_directory_entry_name(
                    entry,
                    budget,
                    budget.diagnostic,
                )
                if child not in {"failed", TRANSACTION_MARKER, "previous", "staged"}:
                    raise lineage.LineageError(
                        "source-lineage recovery state is invalid"
                    )
                metadata = os.stat(child, dir_fd=descriptor, follow_symlinks=False)
                if child == TRANSACTION_MARKER:
                    marker_seen = True
                    if (
                        not stat.S_ISREG(metadata.st_mode)
                        or stat.S_ISLNK(metadata.st_mode)
                        or _validator_identity(metadata) != marker_identity
                    ):
                        raise lineage.LineageError(
                            "source-lineage recovery state is unowned"
                        )
                elif not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(
                    metadata.st_mode
                ):
                    raise lineage.LineageError(
                        "source-lineage recovery state is invalid"
                    )
        if not marker_seen:
            raise lineage.LineageError("source-lineage recovery state is unowned")
        transaction.marker_identity = marker_identity
        _require_transaction_marker(transaction)
    except lineage.LineageError:
        raise
    except OSError:
        raise lineage.LineageError("source-lineage recovery state is unowned") from None


def _require_opaque_transaction_owner(
    transaction: _TransactionHandle,
    budget,
    diagnostic: str,
) -> None:
    try:
        budget.require_time()
        metadata = os.fstat(transaction.directory.descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.geteuid()
        ):
            raise lineage.LineageError(diagnostic)
        _require_transaction_marker(transaction, diagnostic)
        budget.require_time()
    except lineage.LineageError:
        raise
    except OSError:
        raise lineage.LineageError(diagnostic) from None


def _open_owned_transaction_at(
    release_descriptor: int,
    name: str,
    budget=None,
) -> _TransactionHandle:
    directory = _open_directory_at(
        release_descriptor,
        name,
        "source-lineage recovery state is unowned",
    )
    transaction = _TransactionHandle(name, directory)
    try:
        _require_owned_transaction_descriptor(transaction, budget)
    except lineage.LineageError:
        _close_directory(directory, "source-lineage recovery state is unowned")
        raise
    return transaction


def _transaction_directories(release_descriptor: int, budget=None) -> list[str]:
    if budget is None:
        budget = lineage._new_tree_budget("source-lineage recovery state is ambiguous")
    names = []
    try:
        with _bounded_directory_scan(
            release_descriptor,
            budget,
            "source-lineage recovery state is ambiguous",
        ) as entries:
            for entry in entries:
                name = _bounded_directory_entry_name(
                    entry,
                    budget,
                    "source-lineage recovery state is ambiguous",
                )
                if not name.startswith(TRANSACTION_PREFIX):
                    continue
                names.append(name)
                if len(names) > 1:
                    raise lineage.LineageError(
                        "source-lineage recovery state is ambiguous"
                    )
        names.sort()
    except lineage.LineageError:
        raise
    except OSError:
        raise lineage.LineageError(
            "source-lineage recovery state is ambiguous"
        ) from None
    return names


def _open_git_directory(repository: Path, view=None) -> tuple[Path, _DirectoryHandle]:
    if view is not None:
        lineage._require_lineage_view_binding(view)
    git_directory = Path(_git(repository, "rev-parse", "--absolute-git-dir"))
    if not git_directory.is_absolute():
        raise lineage.LineageError("source-lineage recovery store is invalid")
    parent_descriptor = None
    try:
        parent_descriptor = os.open(git_directory.parent, _DIRECTORY_FLAGS)
        handle = _open_directory_at(
            parent_descriptor,
            git_directory.name,
            "source-lineage recovery store is unavailable",
        )
        if view is not None:
            lineage._require_lineage_view_binding(view)
        return git_directory, handle
    except lineage.LineageError:
        raise
    except OSError:
        raise lineage.LineageError(
            "source-lineage recovery store is unavailable"
        ) from None
    finally:
        if parent_descriptor is not None:
            try:
                os.close(parent_descriptor)
            except OSError:
                raise lineage.LineageError(
                    "source-lineage recovery store is unavailable"
                ) from None


def _open_retention_root(repository: Path, view=None) -> tuple[Path, _DirectoryHandle]:
    git_directory, git_handle = _open_git_directory(repository, view)
    root = git_directory / "source-lineage-recovery"
    root_handle = None
    try:
        try:
            os.mkdir(root.name, 0o700, dir_fd=git_handle.descriptor)
        except FileExistsError:
            pass
        _fsync_descriptor(
            git_handle.descriptor, "source-lineage recovery store is unavailable"
        )
        root_handle = _open_directory_at(
            git_handle.descriptor,
            root.name,
            "source-lineage recovery store is unavailable",
        )
        metadata = os.fstat(root_handle.descriptor)
        if stat.S_IMODE(metadata.st_mode) != 0o700 or metadata.st_uid != os.geteuid():
            raise lineage.LineageError("source-lineage recovery store is invalid")
        if view is not None:
            lineage._require_lineage_view_binding(view)
        handle = root_handle
        root_handle = None
        return root, handle
    except lineage.LineageError:
        raise
    except OSError:
        raise lineage.LineageError(
            "source-lineage recovery store is unavailable"
        ) from None
    finally:
        _close_directories(
            root_handle,
            git_handle,
            diagnostic="source-lineage recovery store is unavailable",
        )


def _retention_root(repository: Path) -> Path:
    root, handle = _open_retention_root(repository)
    _close_directory(handle, "source-lineage recovery store is unavailable")
    return root


def _new_transaction(
    repository: Path,
    view,
    rendered: dict[Path, bytes],
) -> tuple[_TransactionHandle, dict]:
    lineage._require_lineage_view_binding(view)
    preparation_root, retention_root = _open_retention_root(repository, view)
    preparation_handle = None
    try:
        preparation = Path(
            tempfile.mkdtemp(prefix=PREPARATION_PREFIX, dir=preparation_root)
        )
        suffix = preparation.name.removeprefix(PREPARATION_PREFIX)
        name = f"{TRANSACTION_PREFIX}{suffix}"
        _atomic_write(preparation / TRANSACTION_MARKER, _transaction_marker(name))
        staged_root = preparation / "staged"
        for relative, content in rendered.items():
            staged = staged_root / relative
            staged.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(staged, content)
        lineage._require_lineage_view_binding(view)
        lineage.validate_lineage(repository, staged_root, acquire_lock=False)
        lineage._require_lineage_view_binding(view)
        staged_identity = lineage.tree_identity(staged_root / lineage.LINEAGE_ROOT)
        _fsync_directory_tree(staged_root)
        _fsync_directory(preparation)

        preparation_handle = _open_directory_at(
            retention_root.descriptor,
            preparation.name,
            "source-lineage transaction publication failed",
        )
        if _directory_exists_at(view.release_descriptor, name):
            raise lineage.LineageError("source-lineage transaction namespace collision")
        lineage._require_lineage_view_binding(view)
        _rename_at(
            retention_root.descriptor,
            preparation.name,
            view.release_descriptor,
            name,
            diagnostic="source-lineage transaction publication failed",
        )
        _require_directory_name(
            view.release_descriptor,
            name,
            preparation_handle,
            "source-lineage transaction publication failed",
        )
        transaction = _TransactionHandle(name, preparation_handle)
        preparation_handle = None
        _require_owned_transaction_descriptor(transaction)
        return transaction, staged_identity
    except lineage.LineageError:
        raise
    except OSError:
        raise lineage.LineageError(
            "source-lineage transaction publication failed"
        ) from None
    finally:
        _close_directories(
            preparation_handle,
            retention_root,
            diagnostic="source-lineage transaction publication failed",
        )


def _new_replay_directory(root: _DirectoryHandle, view) -> _DirectoryHandle:
    for _ in range(128):
        name = f".source-lineage-replay-{secrets.token_hex(8)}"
        lineage._require_lineage_view_binding(view)
        try:
            os.mkdir(name, 0o700, dir_fd=root.descriptor)
        except FileExistsError:
            continue
        except OSError:
            raise lineage.LineageError(
                "source-lineage recovery store is unavailable"
            ) from None
        _fsync_descriptor(
            root.descriptor, "source-lineage recovery store is unavailable"
        )
        return _open_directory_at(
            root.descriptor,
            name,
            "source-lineage recovery store is unavailable",
        )
    raise lineage.LineageError("source-lineage recovery store is unavailable")


def _retain_transaction(
    repository: Path, view, transaction: _TransactionHandle
) -> None:
    ownership_budget = lineage._new_tree_budget(
        "source-lineage recovery state is unowned"
    )
    _require_owned_transaction_descriptor(transaction, ownership_budget)
    lineage._require_lineage_view_binding(view)
    _, root = _open_retention_root(repository, view)
    lineage._require_lineage_view_binding(view)
    destination = root
    replay = None
    try:
        if _directory_exists_at(root.descriptor, transaction.name):
            _require_transaction_marker(transaction)
            replay = _new_replay_directory(root, view)
            destination = replay
        lineage._require_lineage_view_binding(view)
        _require_directory_name(
            view.release_descriptor,
            transaction.name,
            transaction.directory,
            "source-lineage transaction retention failed",
        )
        lineage._require_lineage_view_binding(view)
        _require_transaction_marker(transaction)
        _rename_at(
            view.release_descriptor,
            transaction.name,
            destination.descriptor,
            transaction.name,
            diagnostic="source-lineage transaction retention failed",
        )
        _require_directory_name(
            destination.descriptor,
            transaction.name,
            transaction.directory,
            "source-lineage transaction retention failed",
        )
        _require_owned_transaction_descriptor(transaction, ownership_budget)
        lineage._require_lineage_view_binding(view)
    except lineage.LineageError:
        raise
    except OSError:
        raise lineage.LineageError(
            "source-lineage transaction retention failed"
        ) from None
    finally:
        _close_directories(
            replay,
            root,
            diagnostic="source-lineage transaction retention failed",
        )


def _require_recovery_authority(view, transaction, bindings=()) -> None:
    diagnostic = "source-lineage recovery state is ambiguous"
    lineage._require_lineage_view_binding(view)
    _require_directory_name(
        view.release_descriptor,
        transaction.name,
        transaction.directory,
        diagnostic,
    )
    _require_transaction_marker(transaction)
    for parent_descriptor, name, handle in bindings:
        _require_directory_name(parent_descriptor, name, handle, diagnostic)
    lineage._require_lineage_view_binding(view)
    _require_directory_name(
        view.release_descriptor,
        transaction.name,
        transaction.directory,
        diagnostic,
    )
    _require_transaction_marker(transaction)
    for parent_descriptor, name, handle in bindings:
        _require_directory_name(parent_descriptor, name, handle, diagnostic)


def _require_recovery_compensation_scope(view, transaction, bindings=()) -> None:
    diagnostic = "source-lineage recovery state is ambiguous"
    _require_directory_name(
        view.release_descriptor,
        transaction.name,
        transaction.directory,
        diagnostic,
    )
    for parent_descriptor, name, handle in bindings:
        _require_directory_name(parent_descriptor, name, handle, diagnostic)
    _require_directory_name(
        view.release_descriptor,
        transaction.name,
        transaction.directory,
        diagnostic,
    )


def _move_bound_directory_noreplace(
    source_parent: int,
    source_name: str,
    source: _DirectoryHandle,
    destination_parent: int,
    destination_name: str,
    *,
    authority,
    applied=None,
) -> None:
    diagnostic = "source-lineage recovery state is ambiguous"
    authority()
    try:
        _require_directory_name(
            source_parent,
            source_name,
            source,
            diagnostic,
        )
    except lineage.LineageError:
        raise _NoReplaceSourceDrift from None

    move_applied = False
    first_failure = None

    def mark_applied():
        nonlocal move_applied
        move_applied = True
        if applied is not None:
            applied()

    try:
        _rename_noreplace_at(
            source_parent,
            source_name,
            destination_parent,
            destination_name,
            applied=mark_applied,
            diagnostic=diagnostic,
        )
    except _NoReplaceCollision:
        try:
            _require_directory_name(
                source_parent,
                source_name,
                source,
                diagnostic,
            )
        except lineage.LineageError:
            raise _NoReplaceSourceDrift from None
        authority()
        try:
            _require_directory_name(
                source_parent,
                source_name,
                source,
                diagnostic,
            )
        except lineage.LineageError:
            raise _NoReplaceSourceDrift from None
        raise
    except BaseException as error:
        if not move_applied:
            try:
                _require_directory_name(
                    source_parent,
                    source_name,
                    source,
                    diagnostic,
                )
            except lineage.LineageError:
                raise _NoReplaceSourceDrift from None
            authority()
            try:
                _require_directory_name(
                    source_parent,
                    source_name,
                    source,
                    diagnostic,
                )
            except lineage.LineageError:
                raise _NoReplaceSourceDrift from None
            raise
        first_failure = error

    try:
        _require_directory_name(
            destination_parent,
            destination_name,
            source,
            diagnostic,
        )
        if _directory_exists_at(source_parent, source_name):
            raise lineage.LineageError(diagnostic)
    except BaseException as error:  # noqa: BLE001 - retain first applied failure
        if first_failure is None:
            first_failure = error
    try:
        authority()
    except BaseException as error:  # noqa: BLE001 - retain first applied failure
        if first_failure is None:
            first_failure = error
    try:
        _require_directory_name(
            destination_parent,
            destination_name,
            source,
            diagnostic,
        )
        if _directory_exists_at(source_parent, source_name):
            raise lineage.LineageError(diagnostic)
    except BaseException as error:  # noqa: BLE001 - retain first applied failure
        if first_failure is None:
            first_failure = error
    if first_failure is not None:
        raise first_failure


def _move_bound_non_directory_noreplace(
    source_parent: int,
    source_name: str,
    source: _NonDirectoryHandle,
    destination_parent: int,
    destination_name: str,
    *,
    authority,
    applied=None,
) -> None:
    diagnostic = "source-lineage recovery state is ambiguous"
    authority()
    try:
        _require_non_directory_name(
            source_parent,
            source_name,
            source,
            diagnostic,
        )
    except lineage.LineageError:
        raise _NoReplaceSourceDrift from None

    move_applied = False
    first_failure = None

    def mark_applied():
        nonlocal move_applied
        move_applied = True
        if applied is not None:
            applied()

    try:
        _rename_noreplace_at(
            source_parent,
            source_name,
            destination_parent,
            destination_name,
            applied=mark_applied,
            diagnostic=diagnostic,
        )
    except _NoReplaceCollision:
        try:
            _require_non_directory_name(
                source_parent,
                source_name,
                source,
                diagnostic,
            )
        except lineage.LineageError:
            raise _NoReplaceSourceDrift from None
        authority()
        try:
            _require_non_directory_name(
                source_parent,
                source_name,
                source,
                diagnostic,
            )
        except lineage.LineageError:
            raise _NoReplaceSourceDrift from None
        raise
    except BaseException as error:
        if not move_applied:
            try:
                _require_non_directory_name(
                    source_parent,
                    source_name,
                    source,
                    diagnostic,
                )
            except lineage.LineageError:
                raise _NoReplaceSourceDrift from None
            authority()
            try:
                _require_non_directory_name(
                    source_parent,
                    source_name,
                    source,
                    diagnostic,
                )
            except lineage.LineageError:
                raise _NoReplaceSourceDrift from None
            raise
        first_failure = error

    try:
        _rebind_non_directory_name_after_move(
            destination_parent,
            destination_name,
            source,
            diagnostic,
        )
        if _name_exists_at(source_parent, source_name):
            raise lineage.LineageError(diagnostic)
    except BaseException as error:  # noqa: BLE001 - retain first applied failure
        if first_failure is None:
            first_failure = error
    try:
        authority()
    except BaseException as error:  # noqa: BLE001 - retain first applied failure
        if first_failure is None:
            first_failure = error
    try:
        _require_non_directory_name(
            destination_parent,
            destination_name,
            source,
            diagnostic,
        )
        if _name_exists_at(source_parent, source_name):
            raise lineage.LineageError(diagnostic)
    except BaseException as error:  # noqa: BLE001 - retain first applied failure
        if first_failure is None:
            first_failure = error
    if first_failure is not None:
        raise first_failure


def _retain_ambiguous_transaction(
    repository: Path,
    view,
    transaction: _TransactionHandle,
    bindings=(),
    non_directory_bindings=(),
) -> None:
    try:
        _require_recovery_compensation_scope(view, transaction, bindings)
        for parent_descriptor, name, handle in non_directory_bindings:
            _require_non_directory_name(
                parent_descriptor,
                name,
                handle,
                "source-lineage recovery retention failed",
            )
        _retain_transaction(repository, view, transaction)
        for parent_descriptor, name, handle in bindings:
            _require_directory_name(
                parent_descriptor,
                name,
                handle,
                "source-lineage recovery retention failed",
            )
        for parent_descriptor, name, handle in non_directory_bindings:
            _require_non_directory_name(
                parent_descriptor,
                name,
                handle,
                "source-lineage recovery retention failed",
            )
    except lineage.LineageError:
        raise lineage.LineageError("source-lineage recovery retention failed") from None
    raise lineage.LineageError("source-lineage recovery state is ambiguous") from None


def _close_recovery_directories(*handles: _DirectoryHandle | None) -> None:
    active_failure = sys.exc_info()[0] is not None
    try:
        _close_directories(
            *handles,
            diagnostic="source-lineage recovery state is ambiguous",
        )
    except lineage.LineageError:
        if not active_failure:
            raise


def _close_recovery_non_directories(
    *handles: _NonDirectoryHandle | None,
) -> None:
    active_failure = sys.exc_info()[0] is not None
    for handle in handles:
        try:
            _close_non_directory(
                handle,
                "source-lineage recovery state is ambiguous",
            )
        except lineage.LineageError:
            if not active_failure:
                raise


def _retain_opaque_transaction_at_recovery_root(
    view,
    transaction: _TransactionHandle,
    recovery_root: _DirectoryHandle,
    recovery_budget,
    *,
    protected_bindings=(),
    non_directory_bindings=(),
) -> None:
    diagnostic = "source-lineage recovery retention failed"
    try:
        recovery_budget.require_time()
        recovery_budget.reserve_tree_entry()
        recovery_budget.reserve_path(transaction.name)
    except lineage.LineageError:
        raise lineage.LineageError(diagnostic) from None

    for attempt in range(MAX_RECOVERY_QUARANTINE_ATTEMPTS + 1):
        replay = None
        replay_binding = None
        try:
            if attempt == 0:
                destination = recovery_root
            else:
                try:
                    replay_name, replay = _new_recovery_quarantine_at(
                        recovery_root,
                        recovery_budget,
                    )
                except _NoReplaceCollision:
                    continue
                except lineage.LineageError:
                    raise lineage.LineageError(diagnostic) from None
                replay_binding = (
                    recovery_root.descriptor,
                    replay_name,
                    replay,
                )
                destination = replay

            move_state = {"applied": False}

            def mark_applied(move_state=move_state):
                move_state["applied"] = True

            def authority(
                replay_binding=replay_binding,
                destination=destination,
                move_state=move_state,
            ):
                lineage._require_lineage_view_binding(view)
                _require_opaque_transaction_owner(
                    transaction,
                    recovery_budget,
                    diagnostic,
                )
                for parent_descriptor, name, handle in (
                    *protected_bindings,
                    *((replay_binding,) if replay_binding is not None else ()),
                ):
                    _require_directory_name(
                        parent_descriptor,
                        name,
                        handle,
                        diagnostic,
                    )
                for parent_descriptor, name, handle in non_directory_bindings:
                    _require_non_directory_name(
                        parent_descriptor,
                        name,
                        handle,
                        diagnostic,
                    )
                _require_directory_name(
                    (
                        destination.descriptor
                        if move_state["applied"]
                        else view.release_descriptor
                    ),
                    transaction.name,
                    transaction.directory,
                    diagnostic,
                )
                lineage._require_lineage_view_binding(view)

            try:
                _move_bound_directory_noreplace(
                    view.release_descriptor,
                    transaction.name,
                    transaction.directory,
                    destination.descriptor,
                    transaction.name,
                    authority=authority,
                    applied=mark_applied,
                )
            except _NoReplaceCollision:
                continue
            except BaseException:  # noqa: BLE001 - normalize retention failure
                raise lineage.LineageError(diagnostic) from None
            return
        finally:
            active_failure = sys.exc_info()[0] is not None
            try:
                _close_directory(replay, diagnostic)
            except lineage.LineageError:
                if not active_failure:
                    raise
    raise lineage.LineageError(diagnostic)


def _quarantine_public_non_directory_in_recovery_root(
    repository: Path,
    view,
    transaction: _TransactionHandle,
    target_name: str,
    generation: _NonDirectoryHandle,
    recovery_budget,
    *,
    protected_bindings=(),
) -> None:
    ambiguous = "source-lineage recovery state is ambiguous"
    retention_failed = "source-lineage recovery retention failed"
    recovery_root = None
    quarantine = None
    try:
        try:
            _, recovery_root = _open_retention_root(repository, view)
        except lineage.LineageError:
            raise lineage.LineageError(retention_failed) from None

        for _ in range(MAX_RECOVERY_QUARANTINE_ATTEMPTS):
            quarantine = None
            try:
                try:
                    quarantine_name, quarantine = _new_recovery_quarantine_at(
                        recovery_root,
                        recovery_budget,
                    )
                except _NoReplaceCollision:
                    continue
                except lineage.LineageError:
                    raise lineage.LineageError(retention_failed) from None
                quarantine_binding = (
                    recovery_root.descriptor,
                    quarantine_name,
                    quarantine,
                )

                def authority(quarantine_binding=quarantine_binding):
                    _require_recovery_compensation_scope(
                        view,
                        transaction,
                        (*protected_bindings, quarantine_binding),
                    )

                quarantine_failure = None
                try:
                    _move_bound_non_directory_noreplace(
                        view.release_descriptor,
                        target_name,
                        generation,
                        quarantine.descriptor,
                        target_name,
                        authority=authority,
                    )
                except _NoReplaceCollision:
                    continue
                except _NoReplaceSourceDrift:
                    raise lineage.LineageError(retention_failed) from None
                except BaseException as error:  # noqa: BLE001 - preserve applied leaf
                    if not _non_directory_name_matches(
                        quarantine.descriptor,
                        target_name,
                        generation,
                    ) or _name_exists_at(view.release_descriptor, target_name):
                        raise lineage.LineageError(retention_failed) from None
                    quarantine_failure = error

                leaf_binding = (
                    quarantine.descriptor,
                    target_name,
                    generation,
                )
                _retain_opaque_transaction_at_recovery_root(
                    view,
                    transaction,
                    recovery_root,
                    recovery_budget,
                    protected_bindings=(
                        *protected_bindings,
                        quarantine_binding,
                    ),
                    non_directory_bindings=(leaf_binding,),
                )
                _require_directory_name(
                    recovery_root.descriptor,
                    quarantine_name,
                    quarantine,
                    retention_failed,
                )
                _require_non_directory_name(
                    quarantine.descriptor,
                    target_name,
                    generation,
                    retention_failed,
                )
                lineage._require_lineage_view_binding(view)
                if quarantine_failure is not None:
                    raise lineage.LineageError(retention_failed) from None
                raise lineage.LineageError(ambiguous) from None
            finally:
                _close_recovery_directories(quarantine)
        raise lineage.LineageError(retention_failed)
    finally:
        _close_recovery_directories(recovery_root)


def _quarantine_public_non_directory(
    repository: Path,
    view,
    transaction: _TransactionHandle,
    target_name: str,
    recovery_budget,
    *,
    protected_bindings=(),
) -> None:
    diagnostic = "source-lineage recovery state is ambiguous"
    generation = None
    staged = None

    def authority(extra_bindings=()):
        _require_recovery_compensation_scope(
            view,
            transaction,
            (*protected_bindings, *extra_bindings),
        )

    def retain_ambiguous(extra_bindings=(), non_directory_bindings=()):
        _retain_ambiguous_transaction(
            repository,
            view,
            transaction,
            (*protected_bindings, *extra_bindings),
            non_directory_bindings,
        )

    try:
        try:
            generation = _open_non_directory_at(
                view.release_descriptor,
                target_name,
                diagnostic,
            )
        except lineage.LineageError:
            retain_ambiguous()

        try:
            authority()
            recovery_budget.require_time()
            recovery_budget.reserve_tree_entry()
            recovery_budget.reserve_path("staged")
        except lineage.LineageError:
            retain_ambiguous()
        staged_created = False
        try:
            os.mkdir("staged", 0o700, dir_fd=transaction.directory.descriptor)
            staged_created = True
        except FileExistsError:
            pass
        except OSError:
            retain_ambiguous()
        if staged_created:
            try:
                _fsync_descriptor(transaction.directory.descriptor, diagnostic)
            except lineage.LineageError:
                retain_ambiguous()
        try:
            staged = _open_directory_at(
                transaction.directory.descriptor,
                "staged",
                diagnostic,
            )
        except lineage.LineageError:
            _quarantine_public_non_directory_in_recovery_root(
                repository,
                view,
                transaction,
                target_name,
                generation,
                recovery_budget,
                protected_bindings=protected_bindings,
            )
        staged_binding = (
            transaction.directory.descriptor,
            "staged",
            staged,
        )
        try:
            authority((staged_binding,))
        except lineage.LineageError:
            retain_ambiguous((staged_binding,))

        for _ in range(MAX_RECOVERY_QUARANTINE_ATTEMPTS):
            quarantine = None
            try:
                try:
                    authority((staged_binding,))
                except lineage.LineageError:
                    retain_ambiguous((staged_binding,))
                try:
                    quarantine_name, quarantine = _new_recovery_quarantine_at(
                        staged,
                        recovery_budget,
                    )
                except _NoReplaceCollision:
                    continue
                except lineage.LineageError:
                    retain_ambiguous((staged_binding,))
                quarantine_binding = (
                    staged.descriptor,
                    quarantine_name,
                    quarantine,
                )
                try:
                    _move_bound_non_directory_noreplace(
                        view.release_descriptor,
                        target_name,
                        generation,
                        quarantine.descriptor,
                        target_name,
                        authority=lambda quarantine_binding=quarantine_binding: (
                            authority((staged_binding, quarantine_binding))
                        ),
                    )
                except _NoReplaceCollision:
                    continue
                except _NoReplaceSourceDrift:
                    retain_ambiguous((staged_binding, quarantine_binding))
                except BaseException:  # noqa: BLE001 - retain any applied leaf move
                    if _non_directory_name_matches(
                        quarantine.descriptor,
                        target_name,
                        generation,
                    ):
                        retain_ambiguous(
                            (staged_binding, quarantine_binding),
                            (
                                (
                                    quarantine.descriptor,
                                    target_name,
                                    generation,
                                ),
                            ),
                        )
                    retain_ambiguous((staged_binding, quarantine_binding))
                retain_ambiguous(
                    (staged_binding, quarantine_binding),
                    (
                        (
                            quarantine.descriptor,
                            target_name,
                            generation,
                        ),
                    ),
                )
            finally:
                try:
                    _close_recovery_directories(quarantine)
                except lineage.LineageError:
                    retain_ambiguous((staged_binding,))
        retain_ambiguous((staged_binding,))
    finally:
        _close_recovery_directories(staged)
        _close_recovery_non_directories(generation)


def _retract_public_generation(
    repository: Path,
    view,
    transaction: _TransactionHandle,
    target_name: str,
    generation: _DirectoryHandle,
    recovery_budget,
    *,
    protected_bindings=(),
    restore_primary_failure=None,
) -> None:
    diagnostic = "source-lineage recovery state is ambiguous"
    collision_handles = []
    collision_bindings = []
    staged_collision = None

    def authority(extra_bindings=()):
        _require_recovery_compensation_scope(
            view,
            transaction,
            (*protected_bindings, *collision_bindings, *extra_bindings),
        )

    def retain_ambiguous(extra_bindings=()):
        _retain_ambiguous_transaction(
            repository,
            view,
            transaction,
            (*protected_bindings, *collision_bindings, *extra_bindings),
        )

    try:
        for destination_name in ("previous", "staged"):
            move_applied = False

            def mark_applied():
                nonlocal move_applied
                move_applied = True

            try:
                _move_bound_directory_noreplace(
                    view.release_descriptor,
                    target_name,
                    generation,
                    transaction.directory.descriptor,
                    destination_name,
                    authority=authority,
                    applied=mark_applied,
                )
            except _NoReplaceCollision:
                try:
                    occupant = _open_directory_at(
                        transaction.directory.descriptor,
                        destination_name,
                        diagnostic,
                    )
                except lineage.LineageError:
                    retain_ambiguous()
                collision_handles.append(occupant)
                collision_bindings.append(
                    (
                        transaction.directory.descriptor,
                        destination_name,
                        occupant,
                    )
                )
                if destination_name == "staged":
                    staged_collision = occupant
                continue
            except _NoReplaceSourceDrift:
                retain_ambiguous()
            except BaseException:  # noqa: BLE001 - quarantine any applied result
                if (
                    restore_primary_failure is not None
                    and destination_name == "previous"
                    and move_applied
                    and _directory_name_matches(
                        transaction.directory.descriptor,
                        destination_name,
                        generation,
                    )
                    and not _directory_exists_at(
                        view.release_descriptor,
                        target_name,
                    )
                ):
                    raise lineage.LineageError(diagnostic) from None
                retain_ambiguous()
            if restore_primary_failure is not None and destination_name == "previous":
                raise restore_primary_failure
            retain_ambiguous()

        if staged_collision is None:
            retain_ambiguous()

        for _ in range(MAX_RECOVERY_QUARANTINE_ATTEMPTS):
            quarantine = None
            try:
                authority()
                try:
                    quarantine_name, quarantine = _new_recovery_quarantine_at(
                        staged_collision,
                        recovery_budget,
                    )
                except _NoReplaceCollision:
                    continue
                except lineage.LineageError:
                    retain_ambiguous()
                quarantine_binding = (
                    staged_collision.descriptor,
                    quarantine_name,
                    quarantine,
                )
                move_applied = False

                def mark_quarantine_applied():
                    nonlocal move_applied
                    move_applied = True

                try:
                    _move_bound_directory_noreplace(
                        view.release_descriptor,
                        target_name,
                        generation,
                        quarantine.descriptor,
                        target_name,
                        authority=lambda quarantine_binding=quarantine_binding: (
                            authority((quarantine_binding,))
                        ),
                        applied=mark_quarantine_applied,
                    )
                except _NoReplaceCollision:
                    continue
                except _NoReplaceSourceDrift:
                    retain_ambiguous()
                except BaseException:  # noqa: BLE001 - quarantine applied state
                    retain_ambiguous((quarantine_binding,))
                retain_ambiguous((quarantine_binding,))
            finally:
                _close_recovery_directories(quarantine)
        retain_ambiguous()
    finally:
        _close_recovery_directories(
            *reversed(collision_handles),
        )


def _restore_previous_noreplace_or_quarantine(
    repository: Path,
    view,
    transaction: _TransactionHandle,
    previous: _DirectoryHandle,
    recovery_budget,
    *,
    protected_bindings=(),
    original_failure=None,
    keep_bound_destination_on_failure: bool = False,
) -> None:
    target_name = lineage.LINEAGE_ROOT.name
    move_applied = False
    replacement_target = None

    def mark_applied():
        nonlocal move_applied
        move_applied = True

    def forward_authority():
        if original_failure is None:
            _require_recovery_authority(view, transaction, protected_bindings)
        else:
            _require_recovery_compensation_scope(
                view,
                transaction,
                protected_bindings,
            )

    try:
        try:
            _move_bound_directory_noreplace(
                transaction.directory.descriptor,
                "previous",
                previous,
                view.release_descriptor,
                target_name,
                authority=forward_authority,
                applied=mark_applied,
            )
        except _NoReplaceCollision:
            _retain_ambiguous_transaction(
                repository,
                view,
                transaction,
                (
                    *protected_bindings,
                    (
                        transaction.directory.descriptor,
                        "previous",
                        previous,
                    ),
                ),
            )
        except _NoReplaceSourceDrift:
            _retain_ambiguous_transaction(
                repository,
                view,
                transaction,
                protected_bindings,
            )
        except BaseException as failure:
            if not move_applied:
                raise
            if _directory_name_matches(
                view.release_descriptor,
                target_name,
                previous,
            ):
                if keep_bound_destination_on_failure:
                    if original_failure is not None:
                        raise original_failure
                    raise
                _retract_public_generation(
                    repository,
                    view,
                    transaction,
                    target_name,
                    previous,
                    recovery_budget,
                    protected_bindings=protected_bindings,
                    restore_primary_failure=failure,
                )
            try:
                replacement_target = _open_directory_at(
                    view.release_descriptor,
                    target_name,
                    "source-lineage recovery state is ambiguous",
                )
            except lineage.LineageError:
                _quarantine_public_non_directory(
                    repository,
                    view,
                    transaction,
                    target_name,
                    recovery_budget,
                    protected_bindings=protected_bindings,
                )
            _retract_public_generation(
                repository,
                view,
                transaction,
                target_name,
                replacement_target,
                recovery_budget,
                protected_bindings=protected_bindings,
            )
        if original_failure is not None:
            raise original_failure
    finally:
        _close_recovery_directories(replacement_target)


def _bound_generation_snapshot(view, binding: _GenerationBinding, name: str) -> dict:
    lineage._require_lineage_view_binding(view)
    _require_directory_name(
        binding.parent_descriptor,
        name,
        binding.directory,
        binding.diagnostic,
    )
    snapshot = lineage._lineage_snapshot_descriptor(binding.directory.descriptor)
    if (
        binding.expected_identity is not None
        and snapshot["identity"] != binding.expected_identity
    ):
        raise lineage.LineageError(binding.diagnostic)
    _require_directory_name(
        binding.parent_descriptor,
        name,
        binding.directory,
        binding.diagnostic,
    )
    lineage._require_lineage_view_binding(view)
    return snapshot


def _validate_generation_at(
    repository: Path, view, binding: _GenerationBinding, name: str
) -> dict:
    snapshot = _bound_generation_snapshot(view, binding, name)
    expected_identity = (
        snapshot["identity"]
        if binding.expected_identity is None
        else binding.expected_identity
    )
    try:
        with tempfile.TemporaryDirectory(
            prefix="source-lineage-recovery-", dir=TRUSTED_RECEIPT_PARENT
        ) as directory:
            artifacts_root = Path(directory)
            _atomic_write(
                artifacts_root / lineage.RESEARCH_REPORT,
                binding.report_raw,
            )
            for relative, content in snapshot["files"].items():
                _atomic_write(
                    artifacts_root / lineage.LINEAGE_ROOT / relative,
                    content,
                )
            lineage.validate_lineage(repository, artifacts_root, acquire_lock=False)
            lineage._require_lineage_view_binding(view)
            observed = _bound_generation_snapshot(view, binding, name)
            if observed["identity"] != expected_identity:
                raise lineage.LineageError(binding.diagnostic)
    except lineage.LineageError:
        raise
    except (OSError, RuntimeError):
        raise lineage.LineageError(
            "source-lineage recovery state is ambiguous"
        ) from None
    return snapshot


def _recover_interrupted_write(repository: Path, view=None) -> None:
    if view is None:
        with lineage._lineage_lock(
            repository, exclusive=True, nonblocking=True
        ) as locked:
            _recover_interrupted_write(locked.root, locked)
        return
    lineage._require_lineage_view_binding(view)
    recovery_budget = lineage._new_tree_budget(
        "source-lineage recovery state is ambiguous"
    )
    names = _transaction_directories(view.release_descriptor, recovery_budget)
    if not names:
        return
    transaction = _open_owned_transaction_at(
        view.release_descriptor,
        names[0],
        recovery_budget,
    )
    target = None
    previous = None
    staged_root = None
    staged_release = None
    staged = None
    target_name = lineage.LINEAGE_ROOT.name
    rejected_generation = False
    restored_previous = False
    try:
        target_exists = _directory_exists_at(view.release_descriptor, target_name)
        previous_exists = _directory_exists_at(
            transaction.directory.descriptor, "previous"
        )
        staged_root_exists = _directory_exists_at(
            transaction.directory.descriptor, "staged"
        )
        if target_exists:
            target = _open_directory_at(
                view.release_descriptor,
                target_name,
                "source-lineage recovery state is ambiguous",
            )
        if previous_exists:
            previous = _open_directory_at(
                transaction.directory.descriptor,
                "previous",
                "source-lineage recovery state is ambiguous",
            )
        if staged_root_exists:
            staged_root = _open_directory_at(
                transaction.directory.descriptor,
                "staged",
                "source-lineage recovery state is ambiguous",
            )
            if _directory_exists_at(
                staged_root.descriptor, lineage.LINEAGE_ROOT.parent.name
            ):
                staged_release = _open_directory_at(
                    staged_root.descriptor,
                    lineage.LINEAGE_ROOT.parent.name,
                    "source-lineage recovery state is ambiguous",
                )
                if _directory_exists_at(staged_release.descriptor, target_name):
                    staged = _open_directory_at(
                        staged_release.descriptor,
                        target_name,
                        "source-lineage recovery state is ambiguous",
                    )
        staged_exists = staged is not None
        report_raw = None
        if target_exists and previous_exists:
            report_raw = _read_research_report_at(
                view,
                lineage._new_validation_budget(),
            )
        if target_exists and previous_exists and staged_exists:
            try:
                target_identity = lineage._lineage_snapshot_descriptor(
                    target.descriptor
                )["identity"]
                duplicate_identity = target_identity in (
                    lineage._lineage_snapshot_descriptor(previous.descriptor)[
                        "identity"
                    ],
                    lineage._lineage_snapshot_descriptor(staged.descriptor)["identity"],
                )
                _validate_generation_at(
                    repository,
                    view,
                    _GenerationBinding(
                        view.release_descriptor,
                        target,
                        target_identity,
                        report_raw,
                        "source-lineage recovery state is ambiguous",
                    ),
                    target_name,
                )
            except lineage.LineageError:
                raise lineage.LineageError(
                    "source-lineage recovery state is ambiguous"
                ) from None
            if not duplicate_identity:
                raise lineage.LineageError("source-lineage recovery state is ambiguous")
        elif not target_exists:
            if not previous_exists:
                raise lineage.LineageError(
                    "source-lineage recovery state is incomplete"
                )
            _restore_previous_noreplace_or_quarantine(
                repository,
                view,
                transaction,
                previous,
                recovery_budget,
            )
            restored_previous = True
        if target_exists and previous_exists and not staged_exists:
            try:
                target_identity = lineage._lineage_snapshot_descriptor(
                    target.descriptor
                )["identity"]
            except lineage.LineageError:
                target_identity = None
            try:
                _validate_generation_at(
                    repository,
                    view,
                    _GenerationBinding(
                        view.release_descriptor,
                        target,
                        target_identity,
                        report_raw,
                        "source-lineage recovery state is ambiguous",
                    ),
                    target_name,
                )
            except lineage.LineageError:
                lineage._require_lineage_view_binding(view)
                try:
                    _require_directory_name(
                        view.release_descriptor,
                        target_name,
                        target,
                        "source-lineage recovery state is ambiguous",
                    )
                    if target_identity is not None and (
                        lineage._lineage_snapshot_descriptor(target.descriptor)[
                            "identity"
                        ]
                        != target_identity
                    ):
                        raise lineage.LineageError(
                            "source-lineage recovery state is ambiguous"
                        )
                    lineage._require_lineage_view_binding(view)
                except lineage.LineageError:
                    raise lineage.LineageError(
                        "source-lineage recovery state is ambiguous"
                    ) from None
                if _directory_exists_at(transaction.directory.descriptor, "failed"):
                    raise lineage.LineageError(
                        "source-lineage recovery state is ambiguous"
                    )
                failed_moved = False

                def mark_failed_moved():
                    nonlocal failed_moved
                    failed_moved = True

                rejection_failure = None
                try:
                    _require_recovery_authority(view, transaction)
                    _require_directory_name(
                        view.release_descriptor,
                        target_name,
                        target,
                        "source-lineage recovery state is ambiguous",
                    )
                    _rename_at(
                        view.release_descriptor,
                        target_name,
                        transaction.directory.descriptor,
                        "failed",
                        applied=mark_failed_moved,
                        diagnostic="source-lineage recovery state is ambiguous",
                    )
                    _require_directory_name(
                        transaction.directory.descriptor,
                        "failed",
                        target,
                        "source-lineage recovery state is ambiguous",
                    )
                except BaseException as error:  # noqa: BLE001 - compensate after apply
                    rejection_failure = error
                if not failed_moved:
                    if rejection_failure is not None:
                        raise rejection_failure
                    raise lineage.LineageError(
                        "source-lineage recovery state is ambiguous"
                    )
                try:
                    _require_recovery_authority(
                        view,
                        transaction,
                        (
                            (
                                transaction.directory.descriptor,
                                "failed",
                                target,
                            ),
                        ),
                    )
                except BaseException as error:  # noqa: BLE001 - compensate safely
                    if rejection_failure is None:
                        rejection_failure = error
                _restore_previous_noreplace_or_quarantine(
                    repository,
                    view,
                    transaction,
                    previous,
                    recovery_budget,
                    protected_bindings=(
                        (
                            transaction.directory.descriptor,
                            "failed",
                            target,
                        ),
                    ),
                    original_failure=rejection_failure,
                    keep_bound_destination_on_failure=True,
                )
                restored_previous = True
                rejected_generation = True
        lineage._require_lineage_view_binding(view)
        try:
            _retain_transaction(repository, view, transaction)
        except lineage.LineageError:
            raise lineage.LineageError(
                "source-lineage recovery retention failed"
            ) from None
        if restored_previous:
            _require_directory_name(
                view.release_descriptor,
                target_name,
                previous,
                "source-lineage recovery state is ambiguous",
            )
            lineage._require_lineage_view_binding(view)
        if rejected_generation:
            raise lineage.LineageError(
                "source-lineage rejected generation was retained"
            )
    finally:
        _close_directories(
            staged,
            staged_release,
            staged_root,
            previous,
            target,
            transaction.directory,
            diagnostic="source-lineage recovery state is ambiguous",
        )


def _write_locked(repository: Path, view) -> None:
    lineage._require_lineage_view_binding(view)
    current = _open_directory_at(
        view.release_descriptor,
        lineage.LINEAGE_ROOT.name,
        "source-lineage artifact tree drift",
    )
    transaction = None
    staged_root = None
    staged_release = None
    staged = None
    try:
        budget = lineage._new_validation_budget()
        captured, previous_identity = _capture_checked_in_inputs(
            view,
            budget=budget,
            lineage_descriptor=current.descriptor,
        )
        lineage._require_lineage_view_binding(view)
        rendered = _stable_render(repository, captured)
        lineage._require_lineage_view_binding(view)
        _validate_rendered(repository, rendered)
        lineage._require_lineage_view_binding(view)
        transaction, staged_identity = _new_transaction(repository, view, rendered)
        staged_root = _open_directory_at(
            transaction.directory.descriptor,
            "staged",
            "source-lineage artifact publication failed",
        )
        staged_release = _open_directory_at(
            staged_root.descriptor,
            lineage.LINEAGE_ROOT.parent.name,
            "source-lineage artifact publication failed",
        )
        staged = _open_directory_at(
            staged_release.descriptor,
            lineage.LINEAGE_ROOT.name,
            "source-lineage artifact publication failed",
        )
        if (
            lineage._lineage_snapshot_descriptor(staged.descriptor)["identity"]
            != staged_identity
        ):
            raise lineage.LineageError("source-lineage artifact tree drift")
        previous_moved = False
        staged_installed = False
        retention_attempted = False

        def mark_previous_moved():
            nonlocal previous_moved
            previous_moved = True

        def mark_staged_installed():
            nonlocal staged_installed
            staged_installed = True

        try:
            lineage._require_lineage_view_binding(view)
            _require_directory_name(
                view.release_descriptor,
                lineage.LINEAGE_ROOT.name,
                current,
                "source-lineage artifact tree drift",
            )
            if (
                lineage._lineage_snapshot_descriptor(current.descriptor)["identity"]
                != previous_identity
            ):
                raise lineage.LineageError("source-lineage artifact tree drift")
            lineage._require_lineage_view_binding(view)
            _require_transaction_marker(transaction)
            _rename_at(
                view.release_descriptor,
                lineage.LINEAGE_ROOT.name,
                transaction.directory.descriptor,
                "previous",
                applied=mark_previous_moved,
            )
            _require_directory_name(
                transaction.directory.descriptor,
                "previous",
                current,
                "source-lineage artifact tree drift",
            )
            _require_transaction_marker(transaction)
            lineage._require_lineage_view_binding(view)
            _rename_at(
                staged_release.descriptor,
                lineage.LINEAGE_ROOT.name,
                view.release_descriptor,
                lineage.LINEAGE_ROOT.name,
                applied=mark_staged_installed,
            )
            _require_directory_name(
                view.release_descriptor,
                lineage.LINEAGE_ROOT.name,
                staged,
                "source-lineage artifact tree drift",
            )
            _require_transaction_marker(transaction)
            if (
                lineage._lineage_snapshot_descriptor(staged.descriptor)["identity"]
                != staged_identity
            ):
                raise lineage.LineageError("source-lineage artifact tree drift")
            _validate_generation_at(
                repository,
                view,
                _GenerationBinding(
                    view.release_descriptor,
                    staged,
                    staged_identity,
                    rendered[lineage.RESEARCH_REPORT],
                    "source-lineage artifact tree drift",
                ),
                lineage.LINEAGE_ROOT.name,
            )
            if (
                lineage._lineage_snapshot_descriptor(current.descriptor)["identity"]
                != previous_identity
            ):
                raise lineage.LineageError("source-lineage artifact tree drift")
            lineage._require_lineage_view_binding(view)
            _require_generation_name(
                view.release_descriptor,
                lineage.LINEAGE_ROOT.name,
                staged,
                staged_identity,
                "source-lineage artifact tree drift",
            )
            _require_transaction_marker(transaction)
            retention_attempted = True
            _retain_transaction(repository, view, transaction)
            lineage._require_lineage_view_binding(view)
            _require_generation_name(
                view.release_descriptor,
                lineage.LINEAGE_ROOT.name,
                staged,
                staged_identity,
                "source-lineage artifact tree drift",
            )
            lineage._require_lineage_view_binding(view)
        except Exception:  # noqa: BLE001 - rollback before normalization
            try:
                if staged_installed:
                    staged_is_visible = _directory_name_matches(
                        view.release_descriptor,
                        lineage.LINEAGE_ROOT.name,
                        staged,
                    )
                    staged_is_failed = _directory_exists_at(
                        transaction.directory.descriptor,
                        "failed",
                    ) and _directory_name_matches(
                        transaction.directory.descriptor,
                        "failed",
                        staged,
                    )
                    if staged_is_visible and not staged_is_failed:
                        _rename_at(
                            view.release_descriptor,
                            lineage.LINEAGE_ROOT.name,
                            transaction.directory.descriptor,
                            "failed",
                        )
                        _require_directory_name(
                            transaction.directory.descriptor,
                            "failed",
                            staged,
                            "source-lineage artifact rollback failed",
                        )
                    elif not staged_is_failed:
                        raise lineage.LineageError(
                            "source-lineage artifact rollback failed"
                        )
                if previous_moved:
                    previous_is_retained = _directory_exists_at(
                        transaction.directory.descriptor,
                        "previous",
                    ) and _directory_name_matches(
                        transaction.directory.descriptor,
                        "previous",
                        current,
                    )
                    previous_is_visible = _directory_name_matches(
                        view.release_descriptor,
                        lineage.LINEAGE_ROOT.name,
                        current,
                    )
                    if previous_is_retained and not previous_is_visible:
                        _rename_at(
                            transaction.directory.descriptor,
                            "previous",
                            view.release_descriptor,
                            lineage.LINEAGE_ROOT.name,
                        )
                        _require_directory_name(
                            view.release_descriptor,
                            lineage.LINEAGE_ROOT.name,
                            current,
                            "source-lineage artifact rollback failed",
                        )
                    elif not previous_is_visible:
                        raise lineage.LineageError(
                            "source-lineage artifact rollback failed"
                        )
            except Exception:  # noqa: BLE001 - rollback must normalize every failure
                raise lineage.LineageError(
                    "source-lineage artifact rollback failed"
                ) from None
            if not retention_attempted:
                try:
                    _retain_transaction(repository, view, transaction)
                except lineage.LineageError as error:
                    if str(error) == "source-lineage artifact tree drift":
                        raise error from None
            raise lineage.LineageError(
                "source-lineage artifact publication failed"
            ) from None
    finally:
        _close_directories(
            staged,
            staged_release,
            staged_root,
            current,
            None if transaction is None else transaction.directory,
            diagnostic="source-lineage artifact publication failed",
        )


def write(repository: Path) -> None:
    with lineage._lineage_lock(repository, exclusive=True, nonblocking=True) as view:
        lineage._require_lineage_view_binding(view)
        _recover_interrupted_write(view.root, view)
        _write_locked(view.root, view)
        lineage._require_lineage_view_binding(view)


def _git_blob(
    repository: Path,
    commit: str,
    relative: str,
    *,
    deadline: float | None = None,
) -> bytes:
    if deadline is None:
        deadline = time.monotonic() + MATERIALIZE_TIMEOUT_SECONDS
    commit = lineage._sha1(commit, "committed lineage commit")
    relative = lineage._relative_path(relative, "committed lineage path")
    object_id = lineage._sha1(
        _git(
            repository,
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{commit}:{relative}",
            deadline=deadline,
        ),
        "committed lineage blob",
    )
    try:
        raw = bytes(
            _bounded_git_output(
                repository,
                ("cat-file", "blob", object_id),
                stdin=None,
                output_limit=MAX_EXPLICIT_GIT_BLOB_BYTES,
                deadline=deadline,
            )
        )
    except lineage.LineageError:
        raise lineage.LineageError(
            f"committed lineage artifact is missing: {relative}"
        ) from None
    if _git_blob_sha1(raw) != object_id:
        raise lineage.LineageError("committed lineage artifact is invalid")
    return raw


def _receipt_artifact(relative: Path, raw: bytes) -> dict:
    return {
        "length": len(raw),
        "path": relative.as_posix(),
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
    }


def _materialize_git_commit(repository: Path, commit: str, destination: Path) -> None:
    deadline = time.monotonic() + MATERIALIZE_TIMEOUT_SECONDS
    limit_diagnostic = "committed repository snapshot exceeds materialization limits"
    listing = _bounded_git_output(
        repository,
        ("ls-tree", "-r", "-z", commit),
        stdin=None,
        output_limit=MAX_TREE_LISTING_BYTES,
        deadline=deadline,
    )
    _require_materialization_time(deadline, limit_diagnostic)
    entries = []
    path_index = _GitPathIndex(deadline)
    try:
        if not listing or listing[-1:] != b"\0":
            raise ValueError
        records = listing[:-1].split(b"\0")
        if any(not record for record in records):
            raise ValueError
        for record in records:
            _require_materialization_time(deadline, limit_diagnostic)
            if len(entries) == MAX_TREE_ENTRIES:
                raise _MaterializationLimit
            metadata, raw_path = record.split(b"\t", 1)
            if len(raw_path) > MAX_TREE_PATH_BYTES:
                raise _MaterializationLimit
            mode, kind, object_id = metadata.decode("ascii").split()
            relative = raw_path.decode("utf-8")
            if (
                kind != "blob"
                or mode not in {"100644", "100755", "120000"}
                or lineage._sha1(object_id, "committed repository object") != object_id
                or lineage._relative_path(relative, "committed repository path")
                != relative
            ):
                raise ValueError
            entries.append((relative, mode, object_id))
            path_index.add(relative)
    except _MaterializationLimit:
        raise lineage.LineageError(
            "committed repository snapshot exceeds materialization limits"
        ) from None
    except _FilesystemAlias:
        raise lineage.LineageError(
            "committed repository snapshot contains a filesystem alias"
        ) from None
    except (_UnsupportedGitPath, UnicodeDecodeError, ValueError):
        raise lineage.LineageError(
            "committed repository snapshot contains an unsafe entry"
        ) from None
    if not entries:
        raise lineage.LineageError("committed repository snapshot is invalid")
    object_ids = list(dict.fromkeys(object_id for _, _, object_id in entries))
    with tempfile.TemporaryFile(mode="w+b", dir=TRUSTED_RECEIPT_PARENT) as requests:
        for object_id in object_ids:
            _require_materialization_time(deadline, limit_diagnostic)
            requests.write(object_id.encode("ascii") + b"\n")
        requests.flush()
        requests.seek(0)
        checked = _bounded_git_output(
            repository,
            (
                "cat-file",
                "--batch-check=%(objectname) %(objecttype) %(objectsize)",
            ),
            stdin=requests,
            output_limit=len(object_ids) * MAX_BATCH_RECORD_BYTES,
            deadline=deadline,
        )
        size_by_object = {}
        unique_total = 0
        try:
            checked_records = checked.split(b"\n")
            if not checked_records or checked_records[-1] != b"":
                raise ValueError
            checked_records.pop()
            if len(checked_records) != len(object_ids):
                raise ValueError
            for expected, record in zip(object_ids, checked_records):
                _require_materialization_time(deadline, limit_diagnostic)
                object_id, kind, raw_size = record.decode("ascii").split()
                size = int(raw_size)
                if (
                    object_id != expected
                    or kind != "blob"
                    or raw_size != str(size)
                    or size < 0
                ):
                    raise ValueError
                if size > MAX_BLOB_BYTES:
                    raise _MaterializationLimit
                unique_total += size
                if unique_total > MAX_UNIQUE_BLOB_BYTES:
                    raise _MaterializationLimit
                size_by_object[object_id] = size
            materialized_total = 0
            for _, mode, object_id in entries:
                _require_materialization_time(deadline, limit_diagnostic)
                size = size_by_object[object_id]
                if mode == "120000" and size > MAX_SYMLINK_BYTES:
                    raise _MaterializationLimit
                materialized_total += size
                if materialized_total > MAX_MATERIALIZED_BYTES:
                    raise _MaterializationLimit
        except _MaterializationLimit:
            raise lineage.LineageError(
                "committed repository snapshot exceeds materialization limits"
            ) from None
        except (UnicodeDecodeError, ValueError):
            raise lineage.LineageError(
                "committed repository snapshot is invalid"
            ) from None
        requests.seek(0)
        batch = _bounded_git_output(
            repository,
            ("cat-file", "--batch"),
            stdin=requests,
            output_limit=unique_total + len(object_ids) * MAX_BATCH_RECORD_BYTES,
            deadline=deadline,
        )
    _require_materialization_time(deadline, limit_diagnostic)
    blobs = {}
    cursor = 0
    view = memoryview(batch)
    try:
        for expected in object_ids:
            _require_materialization_time(deadline, limit_diagnostic)
            header_end = batch.index(b"\n", cursor)
            object_id, kind, raw_size = (
                bytes(view[cursor:header_end]).decode("ascii").split()
            )
            size = int(raw_size)
            start = header_end + 1
            end = start + size
            if (
                object_id != expected
                or kind != "blob"
                or raw_size != str(size)
                or size != size_by_object[expected]
                or bytes(view[end : end + 1]) != b"\n"
                or _git_blob_sha1(view[start:end]) != expected
            ):
                raise ValueError
            blobs[object_id] = view[start:end]
            cursor = end + 1
        if cursor != len(batch):
            raise ValueError
    except (UnicodeDecodeError, ValueError):
        raise lineage.LineageError("committed repository snapshot is invalid") from None
    symlink_targets = {}
    for relative, mode, object_id in entries:
        _require_materialization_time(deadline, limit_diagnostic)
        if mode != "120000":
            continue
        try:
            target = bytes(blobs[object_id]).decode("utf-8")
        except UnicodeDecodeError:
            raise lineage.LineageError(
                "committed repository snapshot contains an unsafe entry"
            ) from None
        lineage._safe_symlink_target(relative, target)
        symlink_targets[relative] = target
    _require_materialization_time(deadline, limit_diagnostic)
    try:
        destination.mkdir(mode=0o700)
        destination.chmod(0o700)
        for relative, mode, object_id in entries:
            _require_materialization_time(deadline, limit_diagnostic)
            path = destination / relative
            parent = destination
            for component in Path(relative).parts[:-1]:
                _require_materialization_time(deadline, limit_diagnostic)
                parent /= component
                if not parent.exists():
                    parent.mkdir(mode=0o700)
                    parent.chmod(0o700)
            raw = blobs[object_id]
            if mode == "120000":
                path.symlink_to(symlink_targets[relative])
            else:
                _require_materialization_time(deadline, limit_diagnostic)
                with path.open("xb") as stream:
                    if stream.write(raw) != len(raw):
                        raise OSError
                path.chmod(0o755 if mode == "100755" else 0o644)
        _require_materialization_time(deadline, limit_diagnostic)
    except OSError:
        raise lineage.LineageError(
            "committed repository snapshot contains a filesystem alias"
        ) from None


def _committed_lineage_snapshot(
    repository: Path, commit: str
) -> tuple[dict, dict[Path, bytes], bytes]:
    relatives = (
        lineage.SOURCE_MANIFEST,
        lineage.CONTRIBUTION_LEDGER,
        *lineage.HOST_MANIFESTS.values(),
    )
    with tempfile.TemporaryDirectory(
        prefix="source-lineage-receipt-", dir=TRUSTED_RECEIPT_PARENT
    ) as directory:
        Path(directory).chmod(0o700)
        artifacts_root = Path(directory) / "repository"
        _materialize_git_commit(repository, commit, artifacts_root)
        before_identity = lineage.tree_identity(artifacts_root)
        captured = {
            relative: (artifacts_root / relative).read_bytes() for relative in relatives
        }
        validator_path = artifacts_root / "scripts/validate_source_skill_lineage.py"
        validator = validator_path.read_bytes()
        try:
            if validator != TRUSTED_VALIDATOR_BYTES:
                raise lineage.LineageError("committed validator identity drift")
            validated = lineage.validate_lineage(
                artifacts_root, artifacts_root, acquire_lock=False
            )
            summary = {
                "candidate_packages_sha256": validated["candidate"]["packages_sha256"],
                "source_ids": validated["source_ids"],
                "unresolved_contribution_ids": validated["unresolved_contribution_ids"],
                "unresolved_host_observation_ids": validated[
                    "unresolved_host_observation_ids"
                ],
                "unresolved_source_ids": validated["unresolved_source_ids"],
            }
            snapshot_changed = lineage.tree_identity(artifacts_root) != before_identity
        except (lineage.LineageError, OSError):
            raise lineage.LineageError("committed lineage validation failed") from None
        if snapshot_changed:
            raise lineage.LineageError("committed lineage validation failed")
    return summary, captured, validator


def _clean_head(repository: Path) -> str:
    status = _git(
        repository,
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "--work-tree",
        str(repository),
        "status",
        "--porcelain=v2",
        "--branch",
        "--untracked-files=all",
    ).splitlines()
    oid_lines = [line for line in status if line.startswith("# branch.oid ")]
    if len(oid_lines) != 1 or any(not line.startswith("# ") for line in status):
        raise lineage.LineageError("reconciliation receipt requires a clean checkout")
    return lineage._sha1(oid_lines[0].removeprefix("# branch.oid "), "receipt head")


def _receipt_locked(repository: Path, output: Path, captured_at_utc: str, view) -> None:
    lineage._require_lineage_view_binding(view)
    parent_descriptor, output_name = _external_receipt_parent(repository, output)
    try:
        lineage._require_lineage_view_binding(view)
        _publish_receipt_locked(
            repository, parent_descriptor, output_name, captured_at_utc, view
        )
        lineage._require_lineage_view_binding(view)
    finally:
        active_failure = sys.exc_info()[0] is not None
        try:
            os.close(parent_descriptor)
        except OSError:
            if not active_failure:
                raise lineage.LineageError(
                    "reconciliation receipt publication failed"
                ) from None


def _publish_receipt_locked(
    repository: Path,
    parent_descriptor: int,
    output_name: str,
    captured_at_utc: str,
    view,
) -> None:
    lineage._require_lineage_view_binding(view)
    commit = _clean_head(repository)
    summary, captured, validator_raw = _committed_lineage_snapshot(repository, commit)
    lineage._require_lineage_view_binding(view)
    tree = _git(repository, "rev-parse", f"{commit}^{{tree}}")
    artifacts = [_receipt_artifact(relative, raw) for relative, raw in captured.items()]
    artifacts.sort(key=lambda item: item["path"])
    validator_path = Path("scripts/validate_source_skill_lineage.py")
    validator = _receipt_artifact(validator_path, validator_raw)
    value = {
        "artifacts": artifacts,
        "candidate": {
            "commit_sha1": commit,
            "packages_sha256": summary["candidate_packages_sha256"],
            "repository_id": "nisavid/agents",
            "tree_sha1": tree,
        },
        "captured_at_utc": captured_at_utc,
        "content_sha256": "",
        "contract": "coordinated-source-skill-reconciliation-receipt-v1",
        "release_eligibility": "not-asserted",
        "schema_version": 1,
        "unresolved": {
            "contribution_ids": summary["unresolved_contribution_ids"],
            "host_observation_ids": summary["unresolved_host_observation_ids"],
            "source_ids": summary["unresolved_source_ids"],
        },
        "validator": validator,
    }
    value["content_sha256"] = lineage.content_sha256(value)
    content = lineage.content_document(value)
    if _clean_head(repository) != commit:
        raise lineage.LineageError("candidate changed while receipt was captured")
    lineage._require_lineage_view_binding(view)
    descriptor = None
    try:
        try:
            descriptor = os.open(
                output_name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_descriptor,
            )
            os.fchmod(descriptor, 0o600)
            opened_metadata = os.fstat(descriptor)
            if stat.S_IMODE(opened_metadata.st_mode) != 0o600:
                raise OSError
        except FileExistsError as error:
            raise lineage.LineageError(
                "reconciliation receipt output already exists"
            ) from error
        except OSError as error:
            raise lineage.LineageError(
                "reconciliation receipt publication failed"
            ) from error
        published = os.fdopen(descriptor, "wb", closefd=False)
        with published:
            published.write(content)
            published.flush()
            os.fsync(published.fileno())
            _require_receipt_content(
                parent_descriptor,
                output_name,
                descriptor,
                opened_metadata,
                content,
            )
            os.fsync(parent_descriptor)
            _require_receipt_content(
                parent_descriptor,
                output_name,
                descriptor,
                opened_metadata,
                content,
            )
            lineage._require_lineage_view_binding(view)
            _require_receipt_content(
                parent_descriptor,
                output_name,
                descriptor,
                opened_metadata,
                content,
            )
    except BaseException as error:
        if isinstance(error, OSError):
            raise lineage.LineageError(
                "reconciliation receipt publication failed"
            ) from error
        raise
    finally:
        if descriptor is not None:
            active_failure = sys.exc_info()[0] is not None
            try:
                os.close(descriptor)
            except OSError as error:
                if not active_failure:
                    raise lineage.LineageError(
                        "reconciliation receipt publication failed"
                    ) from error


def _receipt_descriptor_state(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _require_receipt_content(
    parent_descriptor: int,
    output_name: str,
    descriptor: int,
    expected_metadata: os.stat_result,
    expected_content: bytes,
) -> None:
    _require_receipt_name(parent_descriptor, output_name, expected_metadata)
    try:
        before = os.fstat(descriptor)
        observed = bytearray()
        limit = len(expected_content) + 1
        while len(observed) < limit:
            chunk = os.pread(
                descriptor,
                min(MAX_PROCESS_READ_BYTES, limit - len(observed)),
                len(observed),
            )
            if not chunk:
                break
            observed.extend(chunk)
        after = os.fstat(descriptor)
    except OSError as error:
        raise lineage.LineageError(
            "reconciliation receipt publication failed"
        ) from error
    _require_receipt_name(parent_descriptor, output_name, expected_metadata)
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_IMODE(before.st_mode) != 0o600
        or (before.st_dev, before.st_ino)
        != (expected_metadata.st_dev, expected_metadata.st_ino)
        or _receipt_descriptor_state(before) != _receipt_descriptor_state(after)
        or bytes(observed) != expected_content
    ):
        raise lineage.LineageError("reconciliation receipt publication failed")


def _require_receipt_name(
    parent_descriptor: int, output_name: str, expected: os.stat_result
) -> None:
    try:
        observed = os.stat(output_name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError as error:
        raise lineage.LineageError(
            "reconciliation receipt publication failed"
        ) from error
    if (
        not stat.S_ISREG(observed.st_mode)
        or stat.S_IMODE(observed.st_mode) != 0o600
        or (observed.st_dev, observed.st_ino) != (expected.st_dev, expected.st_ino)
    ):
        raise lineage.LineageError("reconciliation receipt publication failed")


def _require_immovable_receipt_parent(parent: Path) -> None:
    if os.getuid() != os.geteuid() or os.geteuid() == 0:
        raise lineage.LineageError("reconciliation receipt publication failed")
    try:
        trusted = TRUSTED_RECEIPT_PARENT.resolve(strict=True)
    except (OSError, RuntimeError):
        raise lineage.LineageError(
            "reconciliation receipt publication failed"
        ) from None
    if parent != trusted:
        raise lineage.LineageError("reconciliation receipt publication failed")
    current = parent
    while True:
        descriptor = None
        try:
            before = current.lstat()
            resolved = current.resolve(strict=True)
            descriptor = os.open(resolved, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            opened = os.fstat(descriptor)
            writable = os.access(resolved, os.W_OK, effective_ids=True)
            after = current.stat()
        except (OSError, RuntimeError, TypeError, ValueError):
            raise lineage.LineageError(
                "reconciliation receipt publication failed"
            ) from None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    raise lineage.LineageError(
                        "reconciliation receipt publication failed"
                    ) from None
        identities = {
            (before.st_dev, before.st_ino),
            (opened.st_dev, opened.st_ino),
            (after.st_dev, after.st_ino),
        }
        if (
            resolved != current
            or len(identities) != 1
            or not stat.S_ISDIR(opened.st_mode)
            or opened.st_uid != 0
            or (current == parent and not opened.st_mode & stat.S_ISVTX)
            or (current != parent and writable)
        ):
            raise lineage.LineageError("reconciliation receipt publication failed")
        if current == current.parent:
            return
        current = current.parent


def _external_receipt_parent(repository: Path, output: Path) -> tuple[int, str]:
    output = Path(os.path.abspath(os.fspath(output)))
    if not output.name:
        raise lineage.LineageError(
            "reconciliation receipt output parent must be an existing directory"
        )
    try:
        parent_metadata = output.parent.lstat()
        parent = output.parent.resolve(strict=True)
        resolved_metadata = parent.stat()
        repository_root = repository.resolve(strict=True)
        common_git_directory = Path(
            _git(
                repository,
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            )
        ).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise lineage.LineageError(
            "reconciliation receipt output parent must be an existing directory"
        ) from None
    if not stat.S_ISDIR(parent_metadata.st_mode) or stat.S_ISLNK(
        parent_metadata.st_mode
    ):
        raise lineage.LineageError(
            "reconciliation receipt output parent must be an existing directory"
        )
    candidate = parent / output.name
    if any(
        candidate == boundary or boundary in candidate.parents
        for boundary in (repository_root, common_git_directory)
    ):
        raise lineage.LineageError(
            "reconciliation receipt output must be external to the candidate"
        )
    _require_immovable_receipt_parent(parent)
    descriptor = None
    try:
        descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened_metadata = os.fstat(descriptor)
    except (OSError, RuntimeError, ValueError):
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise lineage.LineageError(
            "reconciliation receipt output parent must be an existing directory"
        ) from None
    if not stat.S_ISDIR(opened_metadata.st_mode) or (
        opened_metadata.st_dev,
        opened_metadata.st_ino,
    ) != (resolved_metadata.st_dev, resolved_metadata.st_ino):
        os.close(descriptor)
        raise lineage.LineageError(
            "reconciliation receipt output parent must be an existing directory"
        )
    return descriptor, output.name


def receipt(repository: Path, output: Path, captured_at_utc: str) -> None:
    try:
        repository = repository.resolve(strict=True)
        lineage._utc(captured_at_utc, "receipt capture")
        with lineage._lineage_lock(repository, exclusive=False) as view:
            _receipt_locked(view.root, output, captured_at_utc, view)
    except lineage.LineageError as error:
        raise lineage.LineageError(str(error)) from None
    except (OSError, RuntimeError, subprocess.SubprocessError):
        raise lineage.LineageError("reconciliation receipt failed") from None


def _git_tree_identity(
    repository: Path,
    revision: str,
    root: str,
    *,
    deadline: float | None = None,
) -> dict:
    limit_diagnostic = "source Git tree exceeds capture limits"
    lineage._relative_path(root, "source Git root", allow_dot=True)
    if deadline is None:
        deadline = time.monotonic() + MATERIALIZE_TIMEOUT_SECONDS
    if root != ".":
        try:
            _GitPathIndex(deadline).add(root)
        except _MaterializationLimit:
            raise lineage.LineageError(
                "source Git tree exceeds capture limits"
            ) from None
        except (_FilesystemAlias, _UnsupportedGitPath):
            raise lineage.LineageError(
                "source Git tree contains an unsafe entry"
            ) from None
    commit = lineage._sha1(
        _git(
            repository,
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{revision}^{{commit}}",
            deadline=deadline,
        ),
        "source Git commit",
    )
    whole_tree = root == "."
    tree = lineage._sha1(
        _git(
            repository,
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{commit}^{{tree}}" if whole_tree else f"{commit}:{root}",
            deadline=deadline,
        ),
        "source Git tree",
    )
    arguments = ["ls-tree", "-r", "-l", "-z", commit]
    if not whole_tree:
        arguments.extend(("--", root))
    try:
        listing = _bounded_git_output(
            repository,
            tuple(arguments),
            stdin=None,
            output_limit=MAX_TREE_LISTING_BYTES,
            deadline=deadline,
        )
    except lineage.LineageError:
        raise lineage.LineageError("source Git tree cannot be enumerated") from None
    _require_materialization_time(deadline, limit_diagnostic)
    prefix = "" if whole_tree else root.rstrip("/") + "/"
    raw_prefix = prefix.encode("utf-8")
    observed = []
    path_index = _GitPathIndex(deadline)
    size_by_object = {}
    unique_total = 0
    materialized_total = 0
    try:
        if not listing or listing[-1:] != b"\0":
            raise ValueError
        records = listing[:-1].split(b"\0")
        if any(not record for record in records):
            raise ValueError
        for record in records:
            _require_materialization_time(deadline, limit_diagnostic)
            if len(observed) == MAX_TREE_ENTRIES:
                raise _MaterializationLimit
            metadata, raw_path = record.split(b"\t", 1)
            if len(raw_path) > len(raw_prefix) + MAX_TREE_PATH_BYTES:
                raise _MaterializationLimit
            mode, kind, object_id, raw_size = metadata.decode("ascii").split()
            size = int(raw_size)
            relative = raw_path.decode("utf-8")
            local_relative = relative[len(prefix) :]
            if (
                kind != "blob"
                or mode not in {"100644", "100755", "120000"}
                or lineage._sha1(object_id, "source Git object") != object_id
                or raw_size != str(size)
                or size < 0
                or size > MAX_BLOB_BYTES
                or not relative.startswith(prefix)
                or lineage._relative_path(local_relative, "source Git path")
                != local_relative
            ):
                raise ValueError
            path_index.add(local_relative)
            if object_id in size_by_object:
                if size_by_object[object_id] != size:
                    raise ValueError
            else:
                size_by_object[object_id] = size
                unique_total += size
                if unique_total > MAX_UNIQUE_BLOB_BYTES:
                    raise _MaterializationLimit
            materialized_total += size
            if materialized_total > MAX_MATERIALIZED_BYTES:
                raise _MaterializationLimit
            if mode == "120000" and size > MAX_SYMLINK_BYTES:
                raise _MaterializationLimit
            observed.append((local_relative, mode, object_id))
    except _MaterializationLimit:
        raise lineage.LineageError("source Git tree exceeds capture limits") from None
    except _FilesystemAlias:
        raise lineage.LineageError(
            "source Git tree contains a filesystem alias"
        ) from None
    except (_UnsupportedGitPath, UnicodeDecodeError, ValueError):
        raise lineage.LineageError("source Git tree contains an unsafe entry") from None
    if not observed:
        raise lineage.LineageError("source Git tree is empty")
    object_ids = list(dict.fromkeys(object_id for _, _, object_id in observed))
    with tempfile.TemporaryFile(mode="w+b", dir=TRUSTED_RECEIPT_PARENT) as requests:
        for object_id in object_ids:
            _require_materialization_time(deadline, limit_diagnostic)
            requests.write(object_id.encode("ascii") + b"\n")
        requests.flush()
        requests.seek(0)
        try:
            batch = _bounded_git_output(
                repository,
                ("cat-file", "--batch"),
                stdin=requests,
                output_limit=unique_total + len(object_ids) * MAX_BATCH_RECORD_BYTES,
                deadline=deadline,
            )
        except lineage.LineageError:
            raise lineage.LineageError("source Git blob is unavailable") from None
    _require_materialization_time(deadline, limit_diagnostic)
    blobs = {}
    cursor = 0
    view = memoryview(batch)
    try:
        for expected in object_ids:
            _require_materialization_time(deadline, limit_diagnostic)
            header_end = batch.index(b"\n", cursor)
            object_id, kind, raw_size = (
                bytes(view[cursor:header_end]).decode("ascii").split()
            )
            size = int(raw_size)
            start = header_end + 1
            end = start + size
            if (
                object_id != expected
                or kind != "blob"
                or raw_size != str(size)
                or size != size_by_object[expected]
                or bytes(view[end : end + 1]) != b"\n"
                or _git_blob_sha1(view[start:end]) != expected
            ):
                raise ValueError
            blobs[object_id] = view[start:end]
            cursor = end + 1
        if cursor != len(batch):
            raise ValueError
    except (UnicodeDecodeError, ValueError):
        raise lineage.LineageError("source Git blob response is invalid") from None
    entries = []
    for local_relative, mode, object_id in observed:
        _require_materialization_time(deadline, limit_diagnostic)
        raw = blobs[object_id]
        if mode == "120000":
            try:
                target = bytes(raw).decode("utf-8")
            except UnicodeDecodeError as error:
                raise lineage.LineageError(
                    "source Git symlink target is not UTF-8"
                ) from error
            raw = lineage._safe_symlink_target(local_relative, target)
        entries.append(lineage._tree_entry(local_relative, mode, raw))
    _require_materialization_time(deadline, limit_diagnostic)
    identity = lineage._tree_entries_identity(entries)
    _require_materialization_time(deadline, limit_diagnostic)
    return {
        "commit_sha1": commit,
        "entry_count": identity["entry_count"],
        "skill_tree_sha256": identity["tree_sha256"],
        "total_bytes": identity["total_bytes"],
        "tree_sha1": tree,
    }


def capture_git(repository: Path, revision: str, root: str) -> None:
    deadline = time.monotonic() + MATERIALIZE_TIMEOUT_SECONDS
    lineage._relative_path(root, "source Git root", allow_dot=True)
    output = json.dumps(
        _git_tree_identity(repository, revision, root, deadline=deadline),
        indent=2,
        sort_keys=True,
    )
    _require_materialization_time(deadline, "source Git tree exceeds capture limits")
    print(output)


def capture_tree(root: Path) -> None:
    print(json.dumps(lineage.tree_identity(root), indent=2, sort_keys=True))


def _host_file_state(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _host_visible_state(
    path: str | Path, parent_descriptor: int | None
) -> os.stat_result:
    if parent_descriptor is None:
        return Path(path).lstat()
    return os.stat(path, dir_fd=parent_descriptor, follow_symlinks=False)


@contextlib.contextmanager
def _host_directory_descriptor(
    path: str | Path,
    before: os.stat_result,
    parent_descriptor: int | None = None,
):
    descriptor = None
    try:
        descriptor = os.open(path, _HOST_DIRECTORY_FLAGS, dir_fd=parent_descriptor)
        opened = os.fstat(descriptor)
        visible = _host_visible_state(path, parent_descriptor)
    except OSError:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise lineage.LineageError("private host route cannot be enumerated") from None
    if (
        not stat.S_ISDIR(opened.st_mode)
        or _host_file_state(opened) != _host_file_state(before)
        or _host_file_state(visible) != _host_file_state(before)
    ):
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise lineage.LineageError("private host route cannot be enumerated")
    try:
        yield descriptor
    finally:
        active_failure = sys.exc_info()[0] is not None
        final_failure = None
        if not active_failure:
            try:
                after_opened = os.fstat(descriptor)
                after_visible = _host_visible_state(path, parent_descriptor)
                if _host_file_state(after_opened) != _host_file_state(
                    before
                ) or _host_file_state(after_visible) != _host_file_state(before):
                    final_failure = lineage.LineageError(
                        "private host route cannot be enumerated"
                    )
            except OSError:
                final_failure = lineage.LineageError(
                    "private host route cannot be enumerated"
                )
        try:
            os.close(descriptor)
        except OSError:
            if not active_failure and final_failure is None:
                final_failure = lineage.LineageError(
                    "private host route cannot be enumerated"
                )
        if final_failure is not None:
            raise final_failure from None


def _host_scandir(directory_descriptor: int):
    return os.scandir(directory_descriptor)


def _host_route_entries(
    directory_descriptor: int,
    capture_budget: _HostCaptureBudget,
    relative_parent: str = "",
):
    try:
        with _host_scandir(directory_descriptor) as scanner:
            for observed in scanner:
                name = observed.name
                relative = f"{relative_parent}/{name}" if relative_parent else name
                capture_budget.reserve_entry()
                capture_budget.reserve_path(relative)
                before = os.stat(
                    name, dir_fd=directory_descriptor, follow_symlinks=False
                )
                if stat.S_ISDIR(before.st_mode):
                    with _host_directory_descriptor(
                        name, before, directory_descriptor
                    ) as child_descriptor:
                        yield from _host_route_entries(
                            child_descriptor, capture_budget, relative
                        )
                else:
                    yield directory_descriptor, name, relative, before
    except lineage.LineageError:
        raise
    except OSError:
        raise lineage.LineageError("private host route cannot be enumerated") from None


def _host_regular_tree_entry(
    parent_descriptor: int,
    name: str,
    output_relative: str,
    before: os.stat_result,
    capture_budget: _HostCaptureBudget,
) -> dict:
    descriptor = None
    result = None
    failure = None
    try:
        if before.st_size > MAX_BLOB_BYTES:
            raise lineage.LineageError("private host capture exceeds limits")
        capture_budget.require_time()
        visible_before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if _host_file_state(visible_before) != _host_file_state(before):
            raise lineage.LineageError("private host route cannot be read")
        descriptor = os.open(name, _HOST_FILE_FLAGS, dir_fd=parent_descriptor)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _host_file_state(
            opened
        ) != _host_file_state(before):
            raise lineage.LineageError("private host route cannot be read")
        digest = hashlib.sha256()
        length = 0
        while True:
            capture_budget.require_time()
            chunk = os.read(
                descriptor,
                min(MAX_PROCESS_READ_BYTES, before.st_size - length + 1),
            )
            if not chunk:
                break
            length += len(chunk)
            if length > before.st_size:
                raise lineage.LineageError("private host route cannot be read")
            if length > MAX_BLOB_BYTES:
                raise lineage.LineageError("private host capture exceeds limits")
            digest.update(chunk)
        after_opened = os.fstat(descriptor)
        after_visible = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            length != before.st_size
            or _host_file_state(after_opened) != _host_file_state(before)
            or _host_file_state(after_visible) != _host_file_state(before)
        ):
            raise lineage.LineageError("private host route cannot be read")
        lineage._relative_path(output_relative, "private host tree entry")
        result = {
            "length": length,
            "mode": "100755" if before.st_mode & 0o111 else "100644",
            "path": output_relative,
            "sha256": "sha256:" + digest.hexdigest(),
        }
    except lineage.LineageError as error:
        failure = error
    except OSError:
        failure = lineage.LineageError("private host route cannot be read")
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                if failure is None:
                    failure = lineage.LineageError("private host route cannot be read")
    if failure is not None:
        raise failure from None
    return result


def _host_symlink_tree_entry(
    parent_descriptor: int,
    name: str,
    relative: str,
    output_relative: str,
    before: os.stat_result,
    capture_budget: _HostCaptureBudget,
) -> dict:
    try:
        capture_budget.require_time()
        visible_before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        capture_budget.require_time()
        target = os.readlink(name, dir_fd=parent_descriptor)
        capture_budget.require_time()
        after_target = os.readlink(name, dir_fd=parent_descriptor)
        capture_budget.require_time()
        after_visible = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        capture_budget.require_time()
    except OSError as error:
        raise lineage.LineageError("tree entry cannot be observed") from error
    if (
        target != after_target
        or _host_file_state(visible_before) != _host_file_state(before)
        or _host_file_state(after_visible) != _host_file_state(before)
    ):
        raise lineage.LineageError("tree symlink changed while read")
    content = lineage._safe_symlink_target(relative, target)
    if len(content) > MAX_SYMLINK_BYTES:
        raise lineage.LineageError("private host capture exceeds limits")
    return lineage._tree_entry(output_relative, "120000", content)


def _host_installation_identity(
    value: object, source: dict, claimed_roots=None, capture_budget=None
) -> dict:
    if type(value) is not dict or set(value) != {"skill_roots"}:
        raise lineage.LineageError("private host installation schema drift")
    roots = value["skill_roots"]
    if type(roots) is not list or not roots:
        raise lineage.LineageError("private host skill roots must be non-empty")
    for item in roots:
        if type(item) is not dict or set(item) != {"path", "skill_id"}:
            raise lineage.LineageError("private host skill root schema drift")
        lineage._safe_id(item["skill_id"], "private host skill id")
    skill_ids = [item["skill_id"] for item in roots]
    if skill_ids != sorted(set(skill_ids)):
        raise lineage.LineageError("private host skill roots must be sorted and unique")
    entries = []
    allowed = set(source["skill_ids"])
    if claimed_roots is None:
        claimed_roots = []
    if capture_budget is None:
        capture_budget = _HostCaptureBudget(
            time.monotonic() + MATERIALIZE_TIMEOUT_SECONDS
        )
    for item in roots:
        skill_id = item["skill_id"]
        if skill_id not in allowed:
            raise lineage.LineageError("private host skill id is outside its source")
        raw_path = item["path"]
        if type(raw_path) is not str or not raw_path:
            raise lineage.LineageError("private host skill root is invalid")
        root = Path(raw_path)
        if not root.is_absolute():
            raise lineage.LineageError("private host skill root must be absolute")
        capture_budget.require_time()
        try:
            root_metadata = root.lstat()
        except OSError:
            raise lineage.LineageError(
                "private host skill root is unavailable"
            ) from None
        if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(
            root_metadata.st_mode
        ):
            raise lineage.LineageError("private host skill root must be a directory")
        try:
            resolved_root = root.resolve(strict=True)
        except (OSError, RuntimeError):
            raise lineage.LineageError(
                "private host skill root is unavailable"
            ) from None
        capture_budget.require_time()
        with _host_directory_descriptor(root, root_metadata) as root_descriptor:
            opened_root = os.fstat(root_descriptor)
            identity = (opened_root.st_dev, opened_root.st_ino)
            if any(
                identity == known_identity
                or resolved_root == known_root
                or resolved_root in known_root.parents
                or known_root in resolved_root.parents
                for known_identity, known_root in claimed_roots
            ):
                raise lineage.LineageError(
                    "private host skill roots must be physically disjoint"
                )
            try:
                entrypoint_metadata = os.stat(
                    "SKILL.md",
                    dir_fd=root_descriptor,
                    follow_symlinks=False,
                )
            except OSError:
                raise lineage.LineageError(
                    "private host skill root lacks a regular SKILL.md"
                ) from None
            if not stat.S_ISREG(entrypoint_metadata.st_mode):
                raise lineage.LineageError(
                    "private host skill root lacks a regular SKILL.md"
                )
            claimed_roots.append((identity, resolved_root))
            route_entries = _host_route_entries(root_descriptor, capture_budget)
            try:
                for parent_descriptor, name, relative, before in route_entries:
                    if (
                        stat.S_ISLNK(before.st_mode)
                        and before.st_size > MAX_SYMLINK_BYTES
                    ):
                        raise lineage.LineageError(
                            "private host capture exceeds limits"
                        ) from None
                    capture_budget.reserve_bytes(before.st_size)
                    try:
                        output_relative = f"{skill_id}/{relative}"
                        if stat.S_ISREG(before.st_mode):
                            entry = _host_regular_tree_entry(
                                parent_descriptor,
                                name,
                                output_relative,
                                before,
                                capture_budget,
                            )
                        elif stat.S_ISLNK(before.st_mode):
                            entry = _host_symlink_tree_entry(
                                parent_descriptor,
                                name,
                                relative,
                                output_relative,
                                before,
                                capture_budget,
                            )
                        else:
                            raise lineage.LineageError(
                                "private host route contains a special entry"
                            )
                        entries.append(entry)
                    except lineage.LineageError as error:
                        if str(error) == "private host capture exceeds limits":
                            raise
                        if "special entry" in str(error):
                            raise lineage.LineageError(
                                "private host route contains a special entry"
                            ) from None
                        if "symlink" in str(error):
                            raise lineage.LineageError(
                                "private host route contains an unsafe symlink"
                            ) from None
                        raise lineage.LineageError(
                            "private host route cannot be read"
                        ) from None
            finally:
                route_entries.close()
            try:
                if root.resolve(strict=True) != resolved_root:
                    raise lineage.LineageError(
                        "private host route cannot be enumerated"
                    )
            except (OSError, RuntimeError):
                raise lineage.LineageError(
                    "private host route cannot be enumerated"
                ) from None
    if not entries:
        raise lineage.LineageError("private host route inventory is empty")
    identity = lineage._tree_entries_identity(entries)
    installation = {
        "entry_count": identity["entry_count"],
        "matched_snapshots": [],
        "skill_ids": skill_ids,
        "skill_tree_sha256": identity["tree_sha256"],
        "total_bytes": identity["total_bytes"],
    }
    installation["matched_snapshots"] = lineage._matched_snapshots(installation, source)
    return installation


def _capture_host_observations(
    value: object, sources: dict[str, dict], deadline: float | None = None
) -> list[dict]:
    if deadline is None:
        deadline = time.monotonic() + MATERIALIZE_TIMEOUT_SECONDS
    capture_budget = _HostCaptureBudget(deadline)
    capture_budget.require_time()
    if type(value) is not list or [
        item.get("source_id") for item in value if type(item) is dict
    ] != sorted(sources):
        raise lineage.LineageError("private host source coverage drift")
    observations = []
    claimed_roots = []
    for item in value:
        if type(item) is not dict:
            raise lineage.LineageError("private host observation must be an object")
        source_id = item.get("source_id")
        status_value = item.get("status")
        if status_value == "installed":
            if set(item) != {"installations", "source_id", "status"}:
                raise lineage.LineageError("private installed-host schema drift")
            installations = item["installations"]
            if type(installations) is not list or not installations:
                raise lineage.LineageError(
                    "private host installations must be non-empty"
                )
            captured = [
                _host_installation_identity(
                    installation,
                    sources[source_id],
                    claimed_roots,
                    capture_budget,
                )
                for installation in installations
            ]
            captured = lineage._assigned_host_routes(source_id, captured)
            observed_skill_ids = {
                skill_id
                for installation in captured
                for skill_id in installation["skill_ids"]
            }
            observations.append(
                {
                    "installations": captured,
                    "source_id": source_id,
                    "status": "installed",
                    "unobserved_skill_ids": sorted(
                        set(sources[source_id]["skill_ids"]) - observed_skill_ids
                    ),
                }
            )
        elif status_value == "absent":
            if set(item) != {"source_id", "status"}:
                raise lineage.LineageError("private absent-host schema drift")
            observations.append(dict(item))
        elif status_value == "not-applicable":
            if set(item) != {"reason_code", "source_id", "status"}:
                raise lineage.LineageError("private inapplicable-host schema drift")
            reason_code = item["reason_code"]
            if reason_code not in lineage.HOST_NOT_APPLICABLE_REASONS:
                raise lineage.LineageError(
                    "private inapplicable-host reason is invalid"
                )
            observations.append(
                {
                    "reason": lineage.HOST_NOT_APPLICABLE_REASONS[reason_code],
                    "source_id": source_id,
                    "status": status_value,
                }
            )
        elif status_value == "unresolved":
            if set(item) != {"reason_code", "source_id", "status"}:
                raise lineage.LineageError("private unresolved-host schema drift")
            reason_code = item["reason_code"]
            if reason_code not in lineage.HOST_UNRESOLVED_REASONS:
                raise lineage.LineageError("private unresolved-host reason is invalid")
            reason, evidence_needed = lineage.HOST_UNRESOLVED_REASONS[reason_code]
            observations.append(
                {
                    "evidence_needed": list(evidence_needed),
                    "reason": reason,
                    "source_id": source_id,
                    "status": status_value,
                }
            )
        else:
            raise lineage.LineageError("private host observation status is invalid")
    capture_budget.require_time()
    return observations


def _capture_discovery_precedence(value: object, profile_id: str) -> dict:
    if (
        type(value) is not dict
        or set(value) != {"reason_code", "status"}
        or value.get("status") != "unresolved"
    ):
        raise lineage.LineageError("private discovery-precedence schema drift")
    if (
        value["reason_code"]
        != lineage.HOST_DISCOVERY_PRECEDENCE_REASON_CODES[profile_id]
    ):
        raise lineage.LineageError("private discovery-precedence reason is invalid")
    template = lineage.HOST_DISCOVERY_PRECEDENCE[profile_id]
    return {
        "evidence_needed": list(template["evidence_needed"]),
        "reason": template["reason"],
        "status": template["status"],
    }


def _capture_host_locked(repository: Path, private_input: Path, view) -> None:
    lineage._require_lineage_view_binding(view)
    snapshot = lineage._lineage_snapshot_at(
        view.release_descriptor, lineage.LINEAGE_ROOT.name
    )
    source_raw = lineage._lineage_file(
        lineage._lineage_snapshot_files(snapshot), lineage.SOURCE_MANIFEST
    )
    source, source_raw = lineage._read_document_bytes(source_raw, "source manifest")
    sources = lineage.validate_source_manifest(repository, source)
    lineage._require_lineage_view_binding(view)
    deadline = time.monotonic() + MATERIALIZE_TIMEOUT_SECONDS
    private = _load_private_host_input(private_input, deadline)
    if set(private) != {
        "discovery_precedence",
        "observed_at_utc",
        "profile_id",
        "source_observations",
    }:
        raise lineage.LineageError("private host capture schema drift")
    profile_id = private["profile_id"]
    if profile_id not in lineage.HOST_MANIFESTS:
        raise lineage.LineageError("private host profile is invalid")
    lineage._utc(private["observed_at_utc"], "private host observation")
    discovery_precedence = _capture_discovery_precedence(
        private["discovery_precedence"], profile_id
    )
    first = _capture_host_observations(
        private["source_observations"], sources, deadline
    )
    second = _capture_host_observations(
        private["source_observations"], sources, deadline
    )
    if first != second:
        raise lineage.LineageError("private host routes changed between capture passes")
    captured = {
        "content_sha256": "",
        "contract": "coordinated-installed-source-skill-manifest-v1",
        "discovery_precedence": discovery_precedence,
        "observed_at_utc": private["observed_at_utc"],
        "profile_id": profile_id,
        "schema_version": 1,
        "source_manifest": {
            "path": lineage.SOURCE_MANIFEST.as_posix(),
            "sha256": "sha256:" + hashlib.sha256(source_raw).hexdigest(),
        },
        "source_observations": first,
    }
    captured["content_sha256"] = lineage.content_sha256(captured)
    _require_host_capture_time(deadline)
    lineage.validate_host_manifest(
        captured,
        profile_id,
        source_raw,
        sources,
        budget=lineage._CaptureBudget(
            deadline,
            "private host capture exceeds limits",
        ),
    )
    output = lineage.content_document(captured)
    lineage._require_lineage_view_binding(view)
    _require_host_capture_time(deadline)
    sys.stdout.buffer.write(output)


def capture_host(repository: Path, private_input: Path) -> None:
    try:
        repository = repository.resolve()
        if not private_input.is_absolute():
            private_input = Path.cwd() / private_input
        with lineage._lineage_lock(repository, exclusive=False) as view:
            _capture_host_locked(view.root, private_input, view)
    except lineage.LineageError:
        raise
    except (
        AttributeError,
        IndexError,
        KeyError,
        OSError,
        RecursionError,
        RuntimeError,
        subprocess.SubprocessError,
        TypeError,
        ValueError,
    ):
        raise lineage.LineageError("private host capture failed") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "write"):
        command = commands.add_parser(name)
        command.add_argument("repository", nargs="?", type=Path, default=Path.cwd())
    receipt_command = commands.add_parser("receipt")
    receipt_command.add_argument("repository", type=Path)
    receipt_command.add_argument("--output", type=Path, required=True)
    receipt_command.add_argument("--captured-at-utc", required=True)
    git_command = commands.add_parser("capture-git")
    git_command.add_argument("repository", type=Path)
    git_command.add_argument("--revision", required=True)
    git_command.add_argument("--root", required=True)
    tree_command = commands.add_parser("capture-tree")
    tree_command.add_argument("root", type=Path)
    host_command = commands.add_parser("capture-host")
    host_command.add_argument("repository", type=Path)
    host_command.add_argument("--private-input", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.command == "check":
            check(arguments.repository.resolve())
            print("source-skill-lineage-byte-clean")
        elif arguments.command == "write":
            write(arguments.repository.resolve())
            print("source-skill-lineage-refreshed")
        elif arguments.command == "receipt":
            receipt(
                arguments.repository,
                arguments.output,
                arguments.captured_at_utc,
            )
            print("source-skill-lineage-receipt-created")
        elif arguments.command == "capture-git":
            capture_git(
                arguments.repository.resolve(), arguments.revision, arguments.root
            )
        elif arguments.command == "capture-host":
            capture_host(arguments.repository, arguments.private_input)
        else:
            capture_tree(arguments.root.resolve())
    except (lineage.LineageError, OSError, subprocess.SubprocessError) as error:
        print(f"source-skill-lineage-refresh: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
