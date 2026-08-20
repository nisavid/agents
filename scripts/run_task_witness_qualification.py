#!/usr/bin/env python3
"""Run the canonical Task Witness host qualification."""

from __future__ import annotations

import base64
import binascii
import ctypes
import hashlib
import json
import os
import platform as host_platform
import pwd
import re
import secrets
import selectors
import signal
import stat
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any


class QualificationError(ValueError):
    """A qualification precondition was not satisfied."""


MAX_JSON_BYTES = 1024 * 1024
MAX_PATH_BYTES = 4096
UINT64_MAX = (1 << 64) - 1
MAX_OBSERVED_FILE_BYTES = 1024 * 1024 * 1024
MAX_RUNTIME_CLOSURE_BYTES = 4 * MAX_OBSERVED_FILE_BYTES
MAX_RUNTIME_CLOSURE_ENTRIES = 100_000
MAX_RUNTIME_CLOSURE_ROOTS = 16
MAX_RUNTIME_DIRECTORY_DEPTH = 32
MAX_LINUX_STATUS_BYTES = 64 * 1024
MAX_SYMLINK_TARGET_BYTES = 1023
MAX_SYMLINK_TARGET_COMPONENTS = 256
NON_NATIVE_ENVIRONMENTS = {"container", "emulated"}
SHA256_RE = re.compile(r"[0-9a-f]{64}")
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")
PLATFORM_TARGETS = {
    "macos-arm64": ("darwin", "arm64"),
    "linux-x86_64": ("linux", "x86_64"),
}
SUITE_INVENTORY_CONTRACT = "task-witness-tw4-suite-inventory-v1"
SUITE_RESULT_CONTRACT = "task-witness-tw4-suite-result-v1"
SUITE_PROJECTIONS = (
    ("client-common", "common", ("macos-arm64", "linux-x86_64")),
    ("deployment-common", "common", ("macos-arm64", "linux-x86_64")),
    ("package-contract", "common", ("macos-arm64", "linux-x86_64")),
    (
        "qualification-runner-contract",
        "common",
        ("macos-arm64", "linux-x86_64"),
    ),
    ("task-witness-source-stage", "common", ("macos-arm64", "linux-x86_64")),
    ("public-release-source-stage", "common", ("macos-arm64", "linux-x86_64")),
    ("forward-update", "portable-vertical", ("macos-arm64", "linux-x86_64")),
    (
        "authorized-downgrade-and-manual-rollback",
        "portable-vertical",
        ("macos-arm64", "linux-x86_64"),
    ),
    (
        "candidate-rejection-rollback",
        "portable-vertical",
        ("macos-arm64", "linux-x86_64"),
    ),
    (
        "candidate-source-disappearance",
        "portable-vertical",
        ("macos-arm64", "linux-x86_64"),
    ),
    (
        "provider-cache-deletion-and-movement",
        "portable-vertical",
        ("macos-arm64", "linux-x86_64"),
    ),
    (
        "literal-rendered-shim",
        "portable-vertical",
        ("macos-arm64", "linux-x86_64"),
    ),
    (
        "migration-freeze5-to-bridge",
        "portable-vertical",
        ("macos-arm64", "linux-x86_64"),
    ),
    (
        "migration-bridge-to-tw4",
        "portable-vertical",
        ("macos-arm64", "linux-x86_64"),
    ),
    ("macos-acl", "platform-vertical", ("macos-arm64",)),
    ("linux-process-supervision", "platform-vertical", ("linux-x86_64",)),
)
SUITE_EXPECTED_COUNTS = {
    "client-common": 321,
    "deployment-common": 203,
    "package-contract": 71,
    "qualification-runner-contract": 7,
    "task-witness-source-stage": 1,
    "public-release-source-stage": 1,
    "forward-update": 53,
    "authorized-downgrade-and-manual-rollback": 18,
    "candidate-rejection-rollback": 11,
    "candidate-source-disappearance": 1,
    "provider-cache-deletion-and-movement": 1,
    "literal-rendered-shim": 1,
    "migration-freeze5-to-bridge": 11,
    "migration-bridge-to-tw4": 15,
    "macos-acl": 12,
    "linux-process-supervision": 3,
}
SUITE_DRIVER_ARGV_PREFIX = (
    "-I",
    "-B",
    "scripts/run_task_witness_qualification_suite.py",
    "--suite",
)
REQUIRED_FILESYSTEM_SEMANTICS = [
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
SYSTEM_TOOL_IDS = ["environment-clearer", "git", "posix-shell"]
REQUIRED_RUNTIME_DEPENDENCY_CLASSES = {
    "cpython-extension-modules",
    "cpython-stdlib",
    "loader-shared-libraries",
}
PORTABLE_SYMLINK_HOP_LIMIT = 32
MAX_GIT_STDOUT_BYTES = 64 * 1024 * 1024
MAX_PROCESS_STDERR_BYTES = 1024 * 1024
GIT_TIMEOUT_SECONDS = 30
MAX_CANDIDATE_MEMBER_BYTES = 4096
MAX_BRIDGE_HISTORY_BYTES = 16 * 1024
MAX_CANDIDATE_OBSERVATION_BYTES = 64 * 1024
MAX_CANDIDATE_RETAINED_BYTES = 64 * 1024 * 1024
MAX_VALIDATOR_SOURCE_BYTES = 1024 * 1024
CANDIDATE_VALIDATOR_TIMEOUT_SECONDS = 30
SYSTEM_TOOL_OBSERVATION_MAX_BYTES = 8_388_608
RUNTIME_CLOSURE_OBSERVATION_MAX_BYTES = 41_200_000
DETAIL_STREAM_MAX_BYTES = 1024 * 1024
SUITE_TIMEOUT_SECONDS = 900
RENDERED_SHIM_MAX_BYTES = 65_536
HOST_RECEIPT_MAX_BYTES = 64 * 1024 * 1024
HOST_RECEIPT_MEMBER_CAPS = {
    "qualification_candidate": 4096,
    "candidate_closure": 4096,
    "bridge_history": 16_384,
    "suite_inventory": 4096,
    "platform": 2_113_536,
    "runtime": 1_114_112,
    "rendered_shim": RENDERED_SHIM_MAX_BYTES,
    "observations": 51_500_000,
    "suite_results": 65_536,
}
HOST_OBSERVATION_INPUT_CAPS = {
    "candidate": 65_536,
    "bridge_history": 65_536,
    "suite_inventory": 65_536,
    "platform": 9_500_000,
    "runtime": 41_300_000,
}
CANDIDATE_REPOSITORY_ID = "nisavid/agents"
CANDIDATE_PLUGIN_ROOT = "plugins/task-witness"
CANDIDATE_CATALOG = "release/public-release-runtime-packages.json"
CANDIDATE_REGISTRATION = "release/task-witness/public-release-registration.json"
CANDIDATE_VALIDATOR = "scripts/validate_task_witness.py"
CANDIDATE_AGENT_STANDARD = "scripts/agent_plugins_standard.py"
CANDIDATE_SUPPORT_PATHS = (
    "docs/superpowers/specs/2026-07-27-task-witness-canonical-client-design.md",
    "docs/superpowers/specs/2026-08-12-task-witness-tw4-migration-and-qualification-design.md",
    "release/task-witness/migration",
    "release/task-witness/source-shape-review.json",
    "release/task-witness/tw4-bridge-identity.json",
    "release/task-witness/tw4-bridge-provenance.json",
    "release/task-witness/tw4-suite-inventory.json",
    "scripts/run_task_witness_qualification.py",
    "scripts/run_task_witness_qualification_suite.py",
    "tests/plugins/task_witness_client",
    "tests/plugins/task_witness_deployment",
    "tests/test_task_witness_package.py",
    "tests/test_task_witness_qualification.py",
)
CANDIDATE_CLOSURE_ROOTS = (
    CANDIDATE_PLUGIN_ROOT,
    CANDIDATE_CATALOG,
    CANDIDATE_REGISTRATION,
    CANDIDATE_VALIDATOR,
    *CANDIDATE_SUPPORT_PATHS,
    CANDIDATE_AGENT_STANDARD,
)
CANDIDATE_DIRECTORY_ROOTS = {
    CANDIDATE_PLUGIN_ROOT,
    "release/task-witness/migration",
    "tests/plugins/task_witness_client",
    "tests/plugins/task_witness_deployment",
}
CANDIDATE_FREEZE5_COMMIT_SHA1 = "96608a9b91d4dcf3f468a4fab1f0e008c9c32b36"
CANDIDATE_BRIDGE_COMMIT_TIMESTAMP = "1786517677 -0400"
CANDIDATE_EXPECTED_BRIDGE_IDENTITIES = {
    "freeze5": {
        "commit_sha1": CANDIDATE_FREEZE5_COMMIT_SHA1,
        "tree_sha1": "4d7133e64e1322743781b24e295ae4626835eed5",
        "plugin_subtree_sha256": "1fa2e1ea237bc4be175ff42478fd209ff6f57c59d8cbdc0cef592492c7eea749",
        "controller_sha256": "8dc51b2a644e30d1f7c4f3b71711698b4130b43f1517e9f5361c6d1a0f7d6cfe",
        "policy_sha256": "23e84f210ba69ef79e02bfc3039b2c8be3b91153d7649009b3a22850f5086245",
        "client_sha256": "778186f6a460655a8b390c831e05c233171236898663ad4155bd45695597c6cf",
    },
    "bridge": {
        "commit_sha1": "391112a2f222d966a3dc54da953594667227d6d3",
        "tree_sha1": "8027107354602a5d16a53183e3002c2bed892ef6",
        "plugin_subtree_sha256": "1056ff94dc73575932cc37f94f96ccb54324cd60dc83af4cce5951a17fd959f4",
        "controller_sha256": "671693603673e8e895301620817c7fa15a96a37365cff59298d8261ee923a6b3",
        "policy_sha256": "23e84f210ba69ef79e02bfc3039b2c8be3b91153d7649009b3a22850f5086245",
        "client_sha256": "912cba0f5b93900d4caaf651c81a3ef3b10f65b837f2c038db5c232d8b71d875",
    },
}
CANDIDATE_BRIDGE_SNAPSHOT_PATHS = {
    "freeze5": {
        ".claude-plugin/plugin.json": ".claude-plugin/plugin.json",
        ".codex-plugin/plugin.json": ".codex-plugin/plugin.json",
        "client/task_witness_client.py": "client/task_witness_client.py",
        "controller/policy.json": "controller/policy.json",
        "controller/task_witness_deploy.py": "controller/task_witness_deploy.py",
    },
    "bridge": {
        ".claude-plugin/plugin.json": ".claude-plugin/plugin.json",
        ".codex-plugin/plugin.json": ".codex-plugin/plugin.json",
        "client/task_witness_client.py": "client/task_witness_client.py",
        "controller/task_witness_deploy.py": "controller/task_witness_deploy.py",
    },
}
CANDIDATE_BRIDGE_STABLE_PATHS = {
    "client/task_witness_shim.sh.in",
    "launcher/task_witness_launch.py",
    "runtime/bundle_io.py",
    "runtime/canonical.py",
    "runtime/task_witness.py",
    "runtime/trust.py",
    "smoke/task_witness_smoke_validator.py",
}
CANDIDATE_VALIDATOR_BOOTSTRAP = r"""import base64, hashlib, json, resource, sys, types
from pathlib import Path
def fail():
    raise SystemExit(70)
try:
    if not hasattr(resource, "RLIMIT_NPROC"):
        fail()
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    if resource.getrlimit(resource.RLIMIT_NPROC) != (0, 0):
        fail()
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    if set(payload) != {"agent_standard", "candidate_root", "validator"}:
        fail()
    root = payload["candidate_root"]
    if not isinstance(root, str) or not root.startswith("/"):
        fail()
    agent_raw = base64.b64decode(payload["agent_standard"], validate=True)
    validator_raw = base64.b64decode(payload["validator"], validate=True)
    identity = hashlib.sha256(agent_raw + b"\0" + validator_raw).hexdigest()
    agent_name = "_task_witness_candidate_agent_standard_" + identity
    validator_name = "_task_witness_candidate_validator_" + identity
    agent = types.ModuleType(agent_name)
    agent.__file__ = root + "/scripts/agent_plugins_standard.py"
    agent.__package__ = ""
    validator = types.ModuleType(validator_name)
    validator.__file__ = root + "/scripts/validate_task_witness.py"
    validator.__package__ = ""
    sys.modules["agent_plugins_standard"] = agent
    sys.modules[agent_name] = agent
    exec(compile(agent_raw, agent.__file__, "exec"), agent.__dict__)
    sys.modules[validator_name] = validator
    exec(compile(validator_raw, validator.__file__, "exec"), validator.__dict__)
    root_path = Path(root)
    function = validator.__dict__.get("validate_candidate_source")
    if not callable(function):
        fail()
    result = function(root_path, True)
    raw = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    sys.stdout.buffer.write(raw)
except BaseException:
    fail()
"""
INVOCATION_FIELDS = (
    ("--candidate-root", "candidate_root", "candidate root"),
    ("--runtime-executable", "runtime_executable", "runtime executable"),
    (
        "--runtime-closure-evidence",
        "runtime_closure_evidence",
        "runtime closure evidence",
    ),
    ("--platform-profile", "platform_profile", "platform profile"),
    ("--receipt-output", "receipt_output", "receipt output"),
)

StatBinding = tuple[int, int, int, int, int, int, int, int, int]


def stable_stat_binding(metadata: os.stat_result) -> StatBinding:
    """Project metadata that must remain stable across qualification observations."""

    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _stat_projection_from_binding(binding: StatBinding) -> dict[str, int]:
    return dict(
        zip(
            (
                "device",
                "inode",
                "mode",
                "uid",
                "gid",
                "nlink",
                "size",
                "mtime_ns",
                "ctime_ns",
            ),
            binding,
            strict=True,
        )
    )


def effective_access(path: Path, mode: int) -> bool:
    """Evaluate access using the effective identity bound by qualification."""

    try:
        return os.access(path, mode, effective_ids=True)
    except (NotImplementedError, TypeError) as error:
        raise QualificationError(
            "effective-identity access checks are unavailable"
        ) from error


def darwin_process_credentials_are_tainted() -> bool:
    """Return Darwin's persistent set-ID credential-history signal."""

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        issetugid = libc.issetugid
        issetugid.argtypes = []
        issetugid.restype = ctypes.c_int
        return bool(issetugid())
    except (AttributeError, OSError) as error:
        raise QualificationError(
            "platform saved credential identity is unavailable"
        ) from error


def linux_process_capabilities_are_empty() -> bool:
    """Read the kernel's complete current-process capability projection."""

    path = Path("/proc/self/status")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise QualificationError("Linux capability identity is unavailable") from error
    try:
        chunks: list[bytes] = []
        remaining = MAX_LINUX_STATUS_BYTES + 1
        while remaining:
            try:
                chunk = os.read(descriptor, min(remaining, 4096))
            except OSError as error:
                raise QualificationError(
                    "Linux capability identity is unavailable"
                ) from error
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > MAX_LINUX_STATUS_BYTES:
            raise QualificationError("Linux capability identity is invalid")
    finally:
        close_descriptor(descriptor, "Linux capability identity")
    try:
        status = raw.decode("ascii")
    except UnicodeDecodeError as error:
        raise QualificationError("Linux capability identity is invalid") from error
    names = ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
    observed: dict[str, int] = {}
    for line in status.splitlines():
        name, separator, value = line.partition(":")
        if name not in names:
            continue
        if separator != ":" or name in observed:
            raise QualificationError("Linux capability identity is invalid")
        token_value = value.strip()
        if not token_value or any(
            character not in "0123456789abcdefABCDEF" for character in token_value
        ):
            raise QualificationError("Linux capability identity is invalid")
        observed[name] = int(token_value, 16)
    if set(observed) != set(names):
        raise QualificationError("Linux capability identity is incomplete")
    return all(value == 0 for value in observed.values())


def close_descriptor(descriptor: int, label: str) -> None:
    """Close a descriptor without masking an already-active failure."""

    active_error = sys.exception()
    try:
        os.close(descriptor)
    except OSError as error:
        if active_error is None:
            raise QualificationError(f"{label} cannot be closed") from error


def open_absolute_path_without_symlinks(
    path: Path,
    label: str,
    *,
    expected_kind: str,
) -> tuple[int, os.stat_result]:
    """Open an absolute leaf through pinned real-directory descriptors."""

    if str(path).startswith("//"):
        raise QualificationError(f"{label} must use one leading slash")
    if not path.is_absolute() or ".." in path.parts:
        raise QualificationError(f"{label} must be an absolute path")
    components = list(path.parts[1:])
    directory_flags = (
        os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        current_descriptor = os.open(path.anchor, directory_flags)
    except OSError as error:
        raise QualificationError(
            f"{label} cannot be opened without symlinks"
        ) from error
    try:
        for component in components[:-1]:
            try:
                next_descriptor = os.open(
                    component,
                    directory_flags,
                    dir_fd=current_descriptor,
                )
            except OSError as error:
                raise QualificationError(
                    f"{label} cannot be opened without symlinks"
                ) from error
            close_descriptor(current_descriptor, label)
            current_descriptor = next_descriptor
        if not components:
            leaf_descriptor = current_descriptor
            current_descriptor = -1
        else:
            leaf_flags = (
                os.O_RDONLY
                | os.O_CLOEXEC
                | os.O_NONBLOCK
                | getattr(os, "O_NOFOLLOW", 0)
            )
            if expected_kind == "directory":
                leaf_flags |= os.O_DIRECTORY
            try:
                leaf_descriptor = os.open(
                    components[-1],
                    leaf_flags,
                    dir_fd=current_descriptor,
                )
            except OSError as error:
                raise QualificationError(
                    f"{label} cannot be opened without symlinks"
                ) from error
        try:
            metadata = os.fstat(leaf_descriptor)
        except OSError as error:
            close_descriptor(leaf_descriptor, label)
            raise QualificationError(f"{label} cannot be read") from error
        if expected_kind == "regular" and not stat.S_ISREG(metadata.st_mode):
            close_descriptor(leaf_descriptor, label)
            raise QualificationError(f"{label} must be a regular file")
        if expected_kind == "directory" and not stat.S_ISDIR(metadata.st_mode):
            close_descriptor(leaf_descriptor, label)
            raise QualificationError(f"{label} must be a regular directory")
        return leaf_descriptor, metadata
    finally:
        if current_descriptor >= 0:
            close_descriptor(current_descriptor, label)


def require_no_symlink_components(path: Path, label: str) -> os.stat_result:
    descriptor, metadata = open_absolute_path_without_symlinks(
        path,
        label,
        expected_kind="any",
    )
    close_descriptor(descriptor, label)
    return metadata


def _invocation_path(raw: object, label: str) -> Path:
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise QualificationError(f"{label} is invalid")
    try:
        encoded = raw.encode("utf-8")
    except UnicodeEncodeError as error:
        raise QualificationError(f"{label} is invalid") from error
    if raw.startswith("//"):
        raise QualificationError(f"{label} must use one leading slash")
    if raw.endswith(("/", "/.")):
        raise QualificationError(f"{label} has terminal directory syntax")
    components = raw.split("/")
    if (
        len(encoded) > MAX_PATH_BYTES
        or not raw.startswith("/")
        or components[0] != ""
        or any(component in {"", ".", ".."} for component in components[1:])
        or str(Path(raw)) != raw
    ):
        raise QualificationError(f"{label} is invalid")
    return Path(raw)


def parse_invocation(
    argv: list[str] | tuple[str, ...] | None = None,
) -> SimpleNamespace:
    """Parse the one exact five-input qualification invocation."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 2 * len(INVOCATION_FIELDS) or tuple(arguments[::2]) != tuple(
        flag for flag, _field, _label in INVOCATION_FIELDS
    ):
        raise QualificationError("qualification arguments are invalid")
    values: dict[str, str] = {}
    for index, (_flag, field, label) in enumerate(INVOCATION_FIELDS):
        raw = arguments[(2 * index) + 1]
        _invocation_path(raw, label)
        values[field] = raw
    return SimpleNamespace(**values)


def parse_args() -> SimpleNamespace:
    """Compatibility seam for tests that replace the process invocation."""

    return parse_invocation()


def require_directory_observation(
    path: Path,
    label: str,
) -> tuple[Path, os.stat_result]:
    descriptor, metadata = open_absolute_path_without_symlinks(
        path,
        label,
        expected_kind="directory",
    )
    close_descriptor(descriptor, label)
    return path, metadata


def require_directory(path: Path, label: str) -> Path:
    return require_directory_observation(path, label)[0]


def require_file(path: Path, label: str) -> Path:
    descriptor, _metadata = open_absolute_path_without_symlinks(
        path,
        label,
        expected_kind="regular",
    )
    close_descriptor(descriptor, label)
    return path


def _validate_canonical_value(value: object, label: str) -> None:
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if not 0 <= value <= UINT64_MAX:
            raise QualificationError(f"{label} integer is outside uint64")
        return
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise QualificationError(f"{label} contains a surrogate") from error
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_canonical_value(item, f"{label}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise QualificationError(f"{label} contains a non-string key")
            _validate_canonical_value(key, f"{label} key")
            _validate_canonical_value(item, f"{label}.{key}")
        return
    raise QualificationError(f"{label} contains an unsupported value")


def canonical_json_bytes(
    value: object,
    *,
    maximum: int | None = None,
    label: str = "canonical JSON",
) -> bytes:
    try:
        _validate_canonical_value(value, label)
        raw = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except QualificationError:
        raise
    except (RecursionError, TypeError, UnicodeEncodeError, ValueError) as error:
        raise QualificationError(f"{label} is not valid JSON") from error
    if maximum is not None and len(raw) > maximum:
        raise QualificationError(f"{label} exceeds the byte limit")
    return raw


def decode_canonical_json(
    raw: bytes,
    *,
    maximum: int,
    expected_root: type,
    label: str,
) -> Any:
    if len(raw) > maximum:
        raise QualificationError(f"{label} exceeds the byte limit")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise QualificationError(f"{label} contains a duplicate key")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise QualificationError(f"{label} contains a non-finite number: {value}")

    def reject_float(value: str) -> None:
        raise QualificationError(f"{label} contains a floating-point value: {value}")

    def parse_integer(value: str) -> int:
        if (
            not value
            or value.startswith("-")
            or len(value) > 20
            or not value.isascii()
            or not value.isdecimal()
        ):
            raise QualificationError(f"{label} is not valid JSON")
        parsed = int(value)
        if not 0 <= parsed <= UINT64_MAX:
            raise QualificationError(f"{label} integer is outside uint64")
        return parsed

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=reject_constant,
            parse_float=reject_float,
            parse_int=parse_integer,
        )
    except QualificationError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ) as error:
        raise QualificationError(f"{label} is not valid JSON") from error
    if not isinstance(value, expected_root):
        raise QualificationError(f"{label} has the wrong root type")
    try:
        canonical_raw = canonical_json_bytes(value, maximum=maximum, label=label)
    except QualificationError as error:
        raise QualificationError(f"{label} is not valid JSON") from error
    if canonical_raw != raw:
        raise QualificationError(f"{label} is not canonical JSON")
    return value


def exact_object(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise QualificationError(f"{label} schema drift")
    return value


def text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise QualificationError(f"{label} is invalid")
    return value


def token(value: object, label: str) -> str:
    result = text(value, label)
    if TOKEN_RE.fullmatch(result) is None:
        raise QualificationError(f"{label} is invalid")
    return result


def sha256_text(value: object, label: str) -> str:
    result = text(value, label)
    if SHA256_RE.fullmatch(result) is None:
        raise QualificationError(f"{label} is invalid")
    return result


def nonnegative_integer(value: object, label: str) -> int:
    if type(value) is not int or not 0 <= value <= UINT64_MAX:
        raise QualificationError(f"{label} is invalid")
    return value


def positive_integer(value: object, label: str) -> int:
    result = nonnegative_integer(value, label)
    if result == 0:
        raise QualificationError(f"{label} is invalid")
    return result


def bounded_file_length(value: object, label: str, *, positive: bool) -> int:
    result = (
        positive_integer(value, label)
        if positive
        else nonnegative_integer(
            value,
            label,
        )
    )
    if result > MAX_OBSERVED_FILE_BYTES:
        raise QualificationError(f"{label} exceeds the supported limit")
    return result


def require_safe_executable_mode(value: object, label: str) -> int:
    mode = nonnegative_integer(value, label)
    if mode & 0o111 == 0 or mode & (stat.S_ISUID | stat.S_ISGID):
        raise QualificationError(f"{label} is unsafe")
    return mode


def absolute_path_text(value: object, label: str) -> str:
    result = text(value, label)
    path = Path(result)
    if (
        len(result.encode("utf-8")) > MAX_PATH_BYTES
        or result.startswith("//")
        or not path.is_absolute()
        or ".." in path.parts
        or str(path) != result
    ):
        raise QualificationError(f"{label} is invalid")
    return result


def cli_path_operand(value: object, label: str, *, file_operand: bool) -> Path:
    """Preserve raw CLI pathname semantics until terminal syntax is validated."""

    raw = text(value, label)
    if file_operand and raw.endswith(("/", "/.")):
        raise QualificationError(f"{label} has terminal directory syntax")
    return Path(raw)


def symlink_target_components(target: str, label: str) -> tuple[bool, tuple[str, ...]]:
    """Split a symlink target without erasing terminal directory requirements."""

    try:
        encoded_length = len(target.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise QualificationError(f"{label} is invalid") from error
    components = tuple(
        target[1:].split("/") if target.startswith("/") else target.split("/")
    )
    if (
        encoded_length > MAX_SYMLINK_TARGET_BYTES
        or len(components) > MAX_SYMLINK_TARGET_COMPONENTS
    ):
        raise QualificationError(f"{label} is invalid")
    if target.startswith("/"):
        return True, components
    return False, components


def require_content_digest(value: dict[str, Any], label: str) -> None:
    recorded = sha256_text(value.get("content_sha256"), f"{label}.content_sha256")
    unsigned = {key: item for key, item in value.items() if key != "content_sha256"}
    observed = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    if recorded != observed:
        raise QualificationError(f"{label} content digest disagrees")


def sorted_unique_tokens(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise QualificationError(f"{label} is invalid")
    result = [token(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if result != sorted(set(result)):
        raise QualificationError(f"{label} must be sorted and unique")
    return result


def parse_platform_profile(value: object) -> dict[str, Any]:
    """Validate the closed externally supplied native-host profile."""

    profile = exact_object(
        value,
        {
            "schema_version",
            "contract",
            "content_sha256",
            "target",
            "execution_environment",
            "platform",
            "passwd_user",
            "native_evidence",
            "filesystem",
            "system_tools",
        },
        "platform profile",
    )
    if (
        type(profile["schema_version"]) is not int
        or profile["schema_version"] != 1
        or profile["contract"] != "task-witness-platform-profile-v1"
    ):
        raise QualificationError("platform profile contract mismatch")
    target = text(profile["target"], "platform profile.target")
    if target not in PLATFORM_TARGETS:
        raise QualificationError("platform profile target is unsupported")
    if profile["execution_environment"] != "native":
        raise QualificationError("platform profile does not describe a native host")
    platform = exact_object(
        profile["platform"],
        {"system", "machine", "qualified_filesystem_class"},
        "platform profile.platform",
    )
    expected_system, expected_machine = PLATFORM_TARGETS[target]
    if (
        platform["system"] != expected_system
        or platform["machine"] != expected_machine
        or platform["qualified_filesystem_class"] != "local-private-filesystem"
    ):
        raise QualificationError("platform profile target binding disagrees")
    passwd_user = exact_object(
        profile["passwd_user"],
        {
            "purpose",
            "name",
            "uid",
            "primary_gid",
            "supplementary_gids",
            "home",
            "provisioning_evidence_sha256",
        },
        "platform profile.passwd_user",
    )
    if passwd_user["purpose"] != "task-witness-disposable-qualification-v1":
        raise QualificationError("platform profile passwd purpose disagrees")
    token(passwd_user["name"], "platform profile.passwd_user.name")
    positive_integer(passwd_user["uid"], "platform profile.passwd_user.uid")
    nonnegative_integer(
        passwd_user["primary_gid"], "platform profile.passwd_user.primary_gid"
    )
    supplementary_gids = passwd_user["supplementary_gids"]
    if (
        not isinstance(supplementary_gids, list)
        or any(type(item) is not int or item < 0 for item in supplementary_gids)
        or supplementary_gids != sorted(set(supplementary_gids))
    ):
        raise QualificationError(
            "platform profile.passwd_user.supplementary_gids is invalid"
        )
    absolute_path_text(passwd_user["home"], "platform profile.passwd_user.home")
    sha256_text(
        passwd_user["provisioning_evidence_sha256"],
        "platform profile.passwd_user.provisioning_evidence_sha256",
    )
    native = exact_object(
        profile["native_evidence"],
        {
            "issuer",
            "provenance",
            "qualification_class",
            "evidence_sha256",
            "container",
            "emulation",
        },
        "platform profile.native_evidence",
    )
    for name in ("issuer", "provenance"):
        token(native[name], f"platform profile.native_evidence.{name}")
    if native["qualification_class"] != "task-witness-native-host-v1":
        raise QualificationError("platform profile native class disagrees")
    sha256_text(
        native["evidence_sha256"],
        "platform profile.native_evidence.evidence_sha256",
    )
    if native["container"] is not False or native["emulation"] is not False:
        raise QualificationError("platform profile does not describe a native host")
    filesystem = exact_object(
        profile["filesystem"],
        {"type", "evidence_sha256", "required_semantics"},
        "platform profile.filesystem",
    )
    token(filesystem["type"], "platform profile.filesystem.type")
    sha256_text(
        filesystem["evidence_sha256"],
        "platform profile.filesystem.evidence_sha256",
    )
    semantics = sorted_unique_tokens(
        filesystem["required_semantics"],
        "platform profile.filesystem.required_semantics",
    )
    if semantics != REQUIRED_FILESYSTEM_SEMANTICS:
        raise QualificationError("platform profile filesystem semantics disagree")
    tools = profile["system_tools"]
    if not isinstance(tools, list):
        raise QualificationError("platform profile system tools are invalid")
    normalized_ids: list[str] = []
    for index, item in enumerate(tools):
        tool = exact_object(
            item,
            {
                "id",
                "invoked_path",
                "resolved_path",
                "length",
                "sha256",
                "uid",
                "gid",
                "mode",
            },
            f"platform profile.system_tools[{index}]",
        )
        normalized_ids.append(token(tool["id"], f"system tool {index}.id"))
        absolute_path_text(tool["invoked_path"], f"system tool {index}.invoked_path")
        absolute_path_text(tool["resolved_path"], f"system tool {index}.resolved_path")
        bounded_file_length(
            tool["length"],
            f"system tool {index}.length",
            positive=True,
        )
        sha256_text(tool["sha256"], f"system tool {index}.sha256")
        nonnegative_integer(tool["uid"], f"system tool {index}.uid")
        nonnegative_integer(tool["gid"], f"system tool {index}.gid")
        require_safe_executable_mode(tool["mode"], f"system tool {index}.mode")
    if normalized_ids != SYSTEM_TOOL_IDS:
        raise QualificationError("platform profile system tool inventory disagrees")
    require_content_digest(profile, "platform profile")
    return profile


def parse_suite_inventory(value: object) -> dict[str, Any]:
    """Validate the closed candidate-owned TW4 suite inventory."""

    inventory = exact_object(
        value,
        {
            "schema_version",
            "contract",
            "runtime_status",
            "entries",
            "aggregates",
        },
        "suite inventory",
    )
    if (
        type(inventory["schema_version"]) is not int
        or inventory["schema_version"] != 1
        or inventory["contract"] != SUITE_INVENTORY_CONTRACT
        or inventory["runtime_status"] != "retired-source-stage"
    ):
        raise QualificationError("suite inventory contract mismatch")
    entries = inventory["entries"]
    if not isinstance(entries, list) or len(entries) != len(SUITE_PROJECTIONS):
        raise QualificationError("suite inventory entries are invalid")
    count_projection: list[dict[str, Any]] = []
    expected_count_total = 0
    for index, (item, projection) in enumerate(
        zip(entries, SUITE_PROJECTIONS, strict=True)
    ):
        suite_id, phase, targets = projection
        entry = exact_object(
            item,
            {
                "argv",
                "executor",
                "expected_count",
                "expected_terminal",
                "id",
                "phase",
                "targets",
            },
            f"suite inventory entry {index}",
        )
        executor = exact_object(
            entry["executor"],
            {"kind"},
            f"suite inventory entry {index}.executor",
        )
        if executor["kind"] != "qualified-cpython":
            raise QualificationError(
                f"suite inventory entry {index} executor disagrees"
            )
        expected_argv = [*SUITE_DRIVER_ARGV_PREFIX, suite_id]
        if (
            entry["id"] != suite_id
            or entry["argv"] != expected_argv
            or entry["phase"] != phase
            or entry["targets"] != list(targets)
            or entry["expected_terminal"] != "passed"
        ):
            raise QualificationError(
                f"suite inventory entry {index} projection disagrees"
            )
        expected_count = positive_integer(
            entry["expected_count"],
            f"suite inventory entry {index}.expected_count",
        )
        if expected_count != SUITE_EXPECTED_COUNTS[suite_id]:
            raise QualificationError(
                f"suite inventory entry {index} expected count disagrees"
            )
        count_projection.append({"expected_count": expected_count, "id": suite_id})
        expected_count_total += expected_count
    aggregates = exact_object(
        inventory["aggregates"],
        {
            "counts_sha256",
            "entries_sha256",
            "entry_count",
            "expected_count_total",
        },
        "suite inventory.aggregates",
    )
    counts_sha256 = sha256_text(
        aggregates["counts_sha256"],
        "suite inventory.aggregates.counts_sha256",
    )
    entries_sha256 = sha256_text(
        aggregates["entries_sha256"],
        "suite inventory.aggregates.entries_sha256",
    )
    if (
        counts_sha256
        != hashlib.sha256(canonical_json_bytes(count_projection)).hexdigest()
    ):
        raise QualificationError("suite inventory count aggregate disagrees")
    if entries_sha256 != hashlib.sha256(canonical_json_bytes(entries)).hexdigest():
        raise QualificationError("suite inventory entry aggregate disagrees")
    if (
        type(aggregates["entry_count"]) is not int
        or aggregates["entry_count"] != len(entries)
        or type(aggregates["expected_count_total"]) is not int
        or aggregates["expected_count_total"] != expected_count_total
    ):
        raise QualificationError("suite inventory aggregate counts disagree")
    return inventory


def parse_suite_result(value: object) -> dict[str, Any]:
    """Validate one closed successful result from the reviewed suite driver."""

    result = exact_object(
        value,
        {
            "schema_version",
            "contract",
            "id",
            "observed_count",
            "terminal",
            "detail_stdout_length",
            "detail_stdout_sha256",
            "detail_stderr_length",
            "detail_stderr_sha256",
        },
        "suite result",
    )
    if (
        type(result["schema_version"]) is not int
        or result["schema_version"] != 1
        or result["contract"] != SUITE_RESULT_CONTRACT
    ):
        raise QualificationError("suite result contract mismatch")
    if not any(
        result["id"] == suite_id for suite_id, _phase, _targets in SUITE_PROJECTIONS
    ):
        raise QualificationError("suite result ID is not registered")
    positive_integer(result["observed_count"], "suite result observed_count")
    if result["terminal"] != "passed":
        raise QualificationError("suite result terminal disagrees")
    for stream in ("stdout", "stderr"):
        length = nonnegative_integer(
            result[f"detail_{stream}_length"],
            f"suite result detail_{stream}_length",
        )
        if length > DETAIL_STREAM_MAX_BYTES:
            raise QualificationError(f"suite result detail_{stream} exceeds the byte limit")
        sha256_text(
            result[f"detail_{stream}_sha256"],
            f"suite result detail_{stream}_sha256",
        )
    return result


def parse_runtime_closure_evidence(value: object) -> dict[str, Any]:
    """Validate one closed externally qualified CPython runtime closure."""

    document = exact_object(
        value,
        {
            "schema_version",
            "contract",
            "content_sha256",
            "authority",
            "main_executable",
            "closure",
        },
        "runtime closure evidence",
    )
    if (
        type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or document["contract"] != "task-witness-runtime-closure-evidence-v1"
    ):
        raise QualificationError("runtime closure evidence contract mismatch")
    authority = exact_object(
        document["authority"],
        {
            "supplier",
            "provenance",
            "qualification_class",
            "issuer",
            "disposition",
            "evidence_sha256",
        },
        "runtime closure evidence.authority",
    )
    for name in ("supplier", "provenance", "qualification_class", "issuer"):
        token(authority[name], f"runtime closure evidence.authority.{name}")
    if authority["disposition"] != "qualified":
        raise QualificationError("runtime closure evidence is not qualified")
    sha256_text(
        authority["evidence_sha256"],
        "runtime closure evidence.authority.evidence_sha256",
    )
    executable = exact_object(
        document["main_executable"],
        {
            "path",
            "length",
            "sha256",
            "uid",
            "gid",
            "mode",
            "implementation",
            "version",
        },
        "runtime closure evidence.main_executable",
    )
    absolute_path_text(executable["path"], "runtime executable path")
    bounded_file_length(
        executable["length"],
        "runtime executable length",
        positive=True,
    )
    sha256_text(executable["sha256"], "runtime executable sha256")
    for name in ("uid", "gid"):
        nonnegative_integer(executable[name], f"runtime executable {name}")
    require_safe_executable_mode(executable["mode"], "runtime executable mode")
    if executable["implementation"] != "cpython":
        raise QualificationError("runtime implementation is unsupported")
    version = exact_object(
        executable["version"],
        {"major", "minor", "micro"},
        "runtime executable version",
    )
    for name in ("major", "minor", "micro"):
        nonnegative_integer(version[name], f"runtime executable version.{name}")
    if version["major"] != 3 or version["minor"] < 13:
        raise QualificationError("runtime version is unsupported")
    closure = exact_object(
        document["closure"],
        {
            "inventory_contract",
            "roots",
            "dependency_classes",
            "entries",
            "entries_sha256",
            "entry_count",
            "total_regular_file_bytes",
        },
        "runtime closure evidence.closure",
    )
    if closure["inventory_contract"] != "task-witness-runtime-closure-inventory-v1":
        raise QualificationError("runtime closure inventory contract mismatch")
    roots = closure["roots"]
    if (
        not isinstance(roots, list)
        or not roots
        or len(roots) > MAX_RUNTIME_CLOSURE_ROOTS
    ):
        raise QualificationError("runtime closure roots are invalid")
    root_paths: list[str] = []
    for index, item in enumerate(roots):
        root = exact_object(
            item,
            {"path", "role", "complete_inventory"},
            f"runtime closure root {index}",
        )
        root_path = absolute_path_text(root["path"], f"runtime root {index}.path")
        if Path(root_path) == Path(root_path).parent:
            raise QualificationError(
                f"runtime root {index}.path cannot be the filesystem root"
            )
        root_paths.append(root_path)
        token(root["role"], f"runtime root {index}.role")
        if root["complete_inventory"] is not True:
            raise QualificationError("runtime closure root inventory is incomplete")
    if root_paths != sorted(set(root_paths)):
        raise QualificationError("runtime closure roots must be sorted and unique")
    root_path_values = tuple(Path(path) for path in root_paths)
    dependencies = sorted_unique_tokens(
        closure["dependency_classes"], "runtime closure dependency classes"
    )
    if not REQUIRED_RUNTIME_DEPENDENCY_CLASSES.issubset(dependencies):
        raise QualificationError("runtime closure dependency classes are incomplete")
    entries = closure["entries"]
    if (
        not isinstance(entries, list)
        or not entries
        or len(entries) > MAX_RUNTIME_CLOSURE_ENTRIES
    ):
        raise QualificationError("runtime closure entries are invalid")
    entry_paths: list[str] = []
    regular_total = 0
    executable_entry: dict[str, Any] | None = None
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise QualificationError(f"runtime closure entry {index} is invalid")
        kind = item.get("kind")
        common = {"path", "kind", "role", "uid", "gid"}
        if kind == "regular-file":
            entry = exact_object(
                item,
                common | {"length", "sha256", "mode"},
                f"runtime closure entry {index}",
            )
            regular_total += bounded_file_length(
                entry["length"],
                f"runtime closure entry {index}.length",
                positive=False,
            )
            if regular_total > MAX_RUNTIME_CLOSURE_BYTES:
                raise QualificationError(
                    "runtime closure regular byte count exceeds the supported limit"
                )
            sha256_text(entry["sha256"], f"runtime closure entry {index}.sha256")
            nonnegative_integer(entry["mode"], f"runtime closure entry {index}.mode")
        elif kind == "directory":
            entry = exact_object(
                item,
                common | {"mode"},
                f"runtime closure entry {index}",
            )
            nonnegative_integer(entry["mode"], f"runtime closure entry {index}.mode")
        elif kind == "symlink":
            entry = exact_object(
                item,
                common | {"target"},
                f"runtime closure entry {index}",
            )
            target_label = f"runtime closure entry {index}.target"
            target_text = text(entry["target"], target_label)
            symlink_target_components(target_text, target_label)
        else:
            raise QualificationError(f"runtime closure entry {index} kind is invalid")
        path = absolute_path_text(entry["path"], f"runtime closure entry {index}.path")
        path_value = Path(path)
        containing_roots = [
            root
            for root in root_path_values
            if path_value == root or path_value.is_relative_to(root)
        ]
        if not containing_roots:
            raise QualificationError(
                f"runtime closure entry {index} is outside the complete roots"
            )
        containing_entry_root = max(containing_roots, key=lambda item: len(item.parts))
        relative_depth = len(path_value.relative_to(containing_entry_root).parts)
        directory_depth = relative_depth if kind == "directory" else relative_depth - 1
        if directory_depth > MAX_RUNTIME_DIRECTORY_DEPTH:
            raise QualificationError(
                f"runtime closure entry {index} exceeds the directory depth limit"
            )
        entry_paths.append(path)
        token(entry["role"], f"runtime closure entry {index}.role")
        nonnegative_integer(entry["uid"], f"runtime closure entry {index}.uid")
        nonnegative_integer(entry["gid"], f"runtime closure entry {index}.gid")
        if path == executable["path"]:
            executable_entry = entry
    if entry_paths != sorted(set(entry_paths)):
        raise QualificationError("runtime closure entries must be sorted and unique")
    entry_by_path = {Path(item["path"]): item for item in entries}

    def containing_root(path: Path) -> Path:
        matches = [
            root
            for root in root_path_values
            if path == root or path.is_relative_to(root)
        ]
        if not matches:
            raise QualificationError(
                "runtime closure symlink target is outside the complete inventory"
            )
        return max(matches, key=lambda item: len(item.parts))

    def containing_absolute_target_root(target_text: str) -> Path:
        matches = [
            root
            for root in root_path_values
            if target_text == str(root) or target_text.startswith(f"{root}/")
        ]
        if not matches:
            raise QualificationError(
                "runtime closure symlink target is outside the complete inventory"
            )
        return max(matches, key=lambda item: len(item.parts))

    def target_state(
        link_path: Path,
        target_text: str,
        suffix: list[str],
    ) -> tuple[Path, Path, deque[str]]:
        absolute, components = symlink_target_components(
            target_text,
            "runtime closure symlink target",
        )
        if absolute:
            root = containing_absolute_target_root(target_text)
            raw_suffix = target_text[len(str(root)) :]
            if not raw_suffix:
                components = ()
            else:
                components = tuple(raw_suffix[1:].split("/"))
            return root, root, deque((*components, *suffix))
        root = containing_root(link_path)
        return link_path.parent, root, deque((*components, *suffix))

    def validate_symlink_target(link_path: Path, target_text: str) -> None:
        current, root, pending = target_state(link_path, target_text, [])
        visited_states: set[tuple[Path, tuple[str, ...], Path]] = set()
        symlink_hops = 1
        while pending:
            component = pending.popleft()
            if component in {"", "."}:
                continue
            if component == "..":
                if current == root:
                    raise QualificationError(
                        "runtime closure symlink target is outside the complete inventory"
                    )
                current = current.parent
                continue
            candidate = current / component
            target_entry = entry_by_path.get(candidate)
            if target_entry is None:
                raise QualificationError(
                    "runtime closure symlink target is outside the complete inventory"
                )
            if target_entry["kind"] == "symlink":
                state = (candidate, tuple(pending), root)
                if (
                    state in visited_states
                    or symlink_hops >= PORTABLE_SYMLINK_HOP_LIMIT
                ):
                    raise QualificationError("runtime closure symlink target cycle")
                visited_states.add(state)
                symlink_hops += 1
                current, root, pending = target_state(
                    candidate,
                    target_entry["target"],
                    pending,
                )
                continue
            if pending and target_entry["kind"] != "directory":
                raise QualificationError(
                    "runtime closure symlink target cannot be traversed"
                )
            current = candidate

    for item in entries:
        if item["kind"] != "symlink":
            continue
        validate_symlink_target(Path(item["path"]), item["target"])
    if (
        type(executable_entry) is not dict
        or executable_entry.get("kind") != "regular-file"
        or executable_entry.get("role") != "main-executable"
        or any(
            executable_entry[name] != executable[name]
            for name in ("length", "sha256", "uid", "gid", "mode")
        )
        or executable["mode"] & 0o111 == 0
    ):
        raise QualificationError("runtime executable closure binding disagrees")
    if nonnegative_integer(
        closure["entry_count"], "runtime closure entry count"
    ) != len(entries):
        raise QualificationError("runtime closure entry count disagrees")
    if (
        nonnegative_integer(
            closure["total_regular_file_bytes"], "runtime closure regular byte count"
        )
        != regular_total
    ):
        raise QualificationError("runtime closure regular byte count disagrees")
    entries_sha256 = sha256_text(
        closure["entries_sha256"], "runtime closure entries sha256"
    )
    observed_entries_sha256 = hashlib.sha256(canonical_json_bytes(entries)).hexdigest()
    if entries_sha256 != observed_entries_sha256:
        raise QualificationError("runtime closure entry digest disagrees")
    require_content_digest(document, "runtime closure evidence")
    return document


def normalized_host_platform() -> tuple[str, str]:
    system = host_platform.system().lower()
    machine = host_platform.machine().lower()
    machine = {"aarch64": "arm64", "amd64": "x86_64"}.get(machine, machine)
    return system, machine


def detect_non_native_environment() -> bool:
    indicators = (
        Path("/.dockerenv"),
        Path("/run/.containerenv"),
    )
    return any(path.exists() for path in indicators) or bool(
        os.environ.get("container")
    )


def regular_file_observation(
    path: Path,
    label: str,
    *,
    expected_length: int,
) -> tuple[os.stat_result, int, str]:
    if expected_length < 0 or expected_length > MAX_OBSERVED_FILE_BYTES:
        raise QualificationError(f"{label} length is outside the supported limit")
    descriptor, opened = open_absolute_path_without_symlinks(
        path,
        label,
        expected_kind="regular",
    )
    try:
        if opened.st_size != expected_length:
            raise QualificationError(f"{label} length disagrees")
        observed_length = 0
        digest = hashlib.sha256()
        remaining = expected_length + 1
        while remaining:
            try:
                chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            except OSError as error:
                raise QualificationError(f"{label} cannot be read") from error
            if not chunk:
                break
            observed_length += len(chunk)
            digest.update(chunk)
            remaining -= len(chunk)
        if observed_length != expected_length:
            raise QualificationError(f"{label} length disagrees")
        try:
            after = os.fstat(descriptor)
        except OSError as error:
            raise QualificationError(f"{label} cannot be read") from error
        if stable_stat_binding(after) != stable_stat_binding(opened):
            raise QualificationError(f"{label} changed while it was read")
        current_descriptor, current = open_absolute_path_without_symlinks(
            path,
            label,
            expected_kind="regular",
        )
        close_descriptor(current_descriptor, label)
        if stable_stat_binding(current) != stable_stat_binding(after):
            raise QualificationError(f"{label} path identity changed")
        return after, observed_length, digest.hexdigest()
    finally:
        close_descriptor(descriptor, label)


def symlink_observation(path: Path, label: str) -> tuple[os.stat_result, str]:
    """Read one symlink while proving its pathname identity stayed stable."""

    try:
        before = path.lstat()
        target = os.readlink(path)
        after = path.lstat()
    except OSError as error:
        raise QualificationError(f"{label} cannot be read") from error
    if not stat.S_ISLNK(before.st_mode) or not stat.S_ISLNK(after.st_mode):
        raise QualificationError(f"{label} kind disagrees")
    if stable_stat_binding(before) != stable_stat_binding(after):
        raise QualificationError(f"{label} identity changed")
    return after, target


def observe_path_resolution(
    path: Path,
    label: str,
) -> tuple[Path, dict[Path, StatBinding]]:
    """Resolve one absolute path while binding every component and symlink hop."""

    if not path.is_absolute():
        raise QualificationError(f"{label} must be an absolute path")
    current = Path(path.anchor)
    pending = deque(path.parts[1:])
    bindings: dict[Path, StatBinding] = {}
    followed_symlink_states: set[tuple[Path, tuple[str, ...]]] = set()
    symlink_hops = 0
    current_is_directory = True
    while pending:
        component = pending.popleft()
        if component in {"", "."}:
            continue
        if component == "..":
            if not current_is_directory:
                raise QualificationError(f"{label} cannot be resolved")
            current = current.parent
            current_is_directory = True
            continue
        if not current_is_directory:
            raise QualificationError(f"{label} cannot be resolved")
        candidate = current / component
        try:
            metadata = candidate.lstat()
        except OSError as error:
            raise QualificationError(f"{label} cannot be resolved") from error
        if not stat.S_ISLNK(metadata.st_mode):
            binding = stable_stat_binding(metadata)
            prior = bindings.get(candidate)
            if prior is not None and prior != binding:
                raise QualificationError(f"{label} resolution changed")
            bindings[candidate] = binding
            current = candidate
            current_is_directory = stat.S_ISDIR(metadata.st_mode)
            if pending and not current_is_directory:
                raise QualificationError(f"{label} cannot be resolved")
            continue
        link_metadata, target_text = symlink_observation(candidate, label)
        binding = stable_stat_binding(link_metadata)
        prior = bindings.get(candidate)
        if prior is not None and prior != binding:
            raise QualificationError(f"{label} resolution changed")
        bindings[candidate] = binding
        state = (candidate, tuple(pending))
        if (
            state in followed_symlink_states
            or symlink_hops >= PORTABLE_SYMLINK_HOP_LIMIT
        ):
            raise QualificationError(f"{label} resolution contains a symlink cycle")
        followed_symlink_states.add(state)
        symlink_hops += 1
        suffix = pending
        absolute, target_components = symlink_target_components(target_text, label)
        if absolute:
            current = Path(path.anchor)
            pending = deque((*target_components, *suffix))
        else:
            current = candidate.parent
            pending = deque((*target_components, *suffix))
        current_is_directory = True
    return current, bindings


def validate_platform_observations(profile: dict[str, Any]) -> dict[str, Any]:
    system, machine = normalized_host_platform()
    expected_system, expected_machine = PLATFORM_TARGETS[profile["target"]]
    if (
        system != expected_system
        or machine != expected_machine
        or profile["platform"]["system"] != system
        or profile["platform"]["machine"] != machine
    ):
        raise QualificationError("observed platform target disagrees")
    if detect_non_native_environment():
        raise QualificationError("qualification host is containerized or emulated")
    euid = os.geteuid()
    if euid == 0:
        raise QualificationError("qualification must run as an unprivileged user")
    try:
        entry = pwd.getpwuid(euid)
    except KeyError as error:
        raise QualificationError("effective user is not passwd-backed") from error
    recorded_user = profile["passwd_user"]
    egid = os.getegid()
    if egid != entry.pw_gid:
        raise QualificationError("platform effective group identity disagrees")
    try:
        uid_credentials = [os.getuid(), euid]
        gid_credentials = [os.getgid(), egid]
    except OSError as error:
        raise QualificationError(
            "platform retained credential identity is unavailable"
        ) from error
    getresuid = getattr(os, "getresuid", None)
    getresgid = getattr(os, "getresgid", None)
    if callable(getresuid) and callable(getresgid):
        try:
            uid_credentials.extend(getresuid())
            gid_credentials.extend(getresgid())
        except OSError as error:
            raise QualificationError(
                "platform saved credential identity is unavailable"
            ) from error
    elif callable(getresuid) or callable(getresgid):
        raise QualificationError("platform saved credential identity is unavailable")
    elif system == "darwin":
        if darwin_process_credentials_are_tainted():
            raise QualificationError("platform retained credential identity disagrees")
    else:
        raise QualificationError("platform saved credential identity is unavailable")
    if any(item != euid for item in uid_credentials) or any(
        item != egid for item in gid_credentials
    ):
        raise QualificationError("platform retained credential identity disagrees")
    if system == "linux" and not linux_process_capabilities_are_empty():
        raise QualificationError("platform Linux capability identity disagrees")
    try:
        observed_supplementary_gids = sorted(set(os.getgroups()))
    except OSError as error:
        raise QualificationError(
            "platform supplementary group identity is unavailable"
        ) from error
    if observed_supplementary_gids != recorded_user["supplementary_gids"]:
        raise QualificationError("platform supplementary group identity disagrees")
    if (
        recorded_user["name"] != entry.pw_name
        or recorded_user["uid"] != entry.pw_uid
        or recorded_user["primary_gid"] != entry.pw_gid
        or recorded_user["home"] != entry.pw_dir
    ):
        raise QualificationError("platform passwd identity disagrees")
    home, home_metadata = require_directory_observation(
        Path(entry.pw_dir), "passwd-backed home"
    )
    if home_metadata.st_uid != euid:
        raise QualificationError("passwd-backed home is invalid")
    tool_observations: list[dict[str, Any]] = []
    for tool in profile["system_tools"]:
        invoked = Path(tool["invoked_path"])
        resolved = Path(tool["resolved_path"])
        label = f"platform system tool {tool['id']}"
        observed_resolved, resolution_bindings = observe_path_resolution(
            invoked,
            label,
        )
        if observed_resolved != resolved:
            raise QualificationError(f"{label} resolution disagrees")
        metadata, observed_length, observed_sha256 = regular_file_observation(
            resolved,
            label,
            expected_length=tool["length"],
        )
        require_safe_executable_mode(
            stat.S_IMODE(metadata.st_mode),
            f"{label} mode",
        )
        observed = {
            "length": observed_length,
            "sha256": observed_sha256,
            "uid": metadata.st_uid,
            "gid": metadata.st_gid,
            "mode": stat.S_IMODE(metadata.st_mode),
        }
        if any(tool[key] != observed[key] for key in observed):
            raise QualificationError(f"{label} binding disagrees")
        tool_binding = stable_stat_binding(metadata)
        if resolution_bindings.get(resolved) != tool_binding:
            raise QualificationError(f"{label} resolution binding changed")
        if not effective_access(resolved, os.X_OK):
            raise QualificationError(f"{label} is not executable")
        require_paths_immutable(
            set(resolution_bindings),
            label,
            expected_bindings=resolution_bindings,
        )
        final_resolved, final_resolution_bindings = observe_path_resolution(
            invoked,
            label,
        )
        if (
            final_resolved != resolved
            or final_resolution_bindings != resolution_bindings
        ):
            raise QualificationError(f"{label} resolution changed")
        tool_observations.append(
            {
                "id": tool["id"],
                "invoked_path": tool["invoked_path"],
                "resolved_path": tool["resolved_path"],
                "resolution": [
                    {
                        "path": str(path),
                        "stat": _stat_projection_from_binding(binding),
                    }
                    for path, binding in sorted(
                        resolution_bindings.items(), key=lambda item: str(item[0])
                    )
                ],
                "file": {
                    "path": str(resolved),
                    "stat": _live_stat(metadata),
                    "length": observed_length,
                    "sha256": observed_sha256,
                },
            }
        )
    canonical_json_bytes(
        tool_observations,
        maximum=SYSTEM_TOOL_OBSERVATION_MAX_BYTES,
        label="platform system-tool observations",
    )
    if system == "darwin":
        credential_state: dict[str, Any] = {
            "kind": "darwin-issetugid-v1",
            "real_uid": os.getuid(),
            "effective_uid": euid,
            "real_gid": os.getgid(),
            "effective_gid": egid,
            "supplementary_gids": observed_supplementary_gids,
            "issetugid": False,
        }
    else:
        credential_state = {
            "kind": "linux-res-id-capabilities-v1",
            "real_uid": uid_credentials[0],
            "effective_uid": euid,
            "saved_uid": uid_credentials[-1],
            "real_gid": gid_credentials[0],
            "effective_gid": egid,
            "saved_gid": gid_credentials[-1],
            "supplementary_gids": observed_supplementary_gids,
            "capabilities": {
                "ambient": 0,
                "bounding": 0,
                "effective": 0,
                "inheritable": 0,
                "permitted": 0,
            },
        }
    return {
        "credential_state": credential_state,
        "home": {"path": str(home), "stat": _live_stat(home_metadata)},
        "system_tools": tool_observations,
    }


def scan_runtime_root(
    root: Path,
    expected_paths: set[Path],
    *,
    expected_root_binding: StatBinding | None = None,
) -> tuple[set[Path], StatBinding]:
    """Scan one complete root through pinned, identity-bound directory handles."""

    root_descriptor, root_metadata = open_absolute_path_without_symlinks(
        root,
        "runtime closure root",
        expected_kind="directory",
    )
    root_binding = stable_stat_binding(root_metadata)
    observed: set[Path] = set()
    directory_flags = (
        os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    )

    def scan_directory(
        directory: Path,
        descriptor: int,
        opened_binding: StatBinding,
        depth: int,
    ) -> None:
        try:
            before = os.fstat(descriptor)
        except OSError as error:
            raise QualificationError(
                "runtime closure root cannot be scanned"
            ) from error
        if stable_stat_binding(before) != opened_binding:
            raise QualificationError("runtime closure directory binding changed")
        try:
            entries = os.scandir(descriptor)
        except OSError as error:
            raise QualificationError(
                "runtime closure root cannot be scanned"
            ) from error
        try:
            with entries:
                for item in entries:
                    path = directory / item.name
                    if path not in expected_paths or path in observed:
                        raise QualificationError(
                            "runtime closure complete inventory disagrees"
                        )
                    observed.add(path)
                    if len(observed) > len(expected_paths):
                        raise QualificationError(
                            "runtime closure complete inventory disagrees"
                        )
                    try:
                        item_metadata = item.stat(follow_symlinks=False)
                    except OSError as error:
                        raise QualificationError(
                            "runtime closure entry cannot be observed"
                        ) from error
                    if not stat.S_ISDIR(item_metadata.st_mode):
                        continue
                    if depth >= MAX_RUNTIME_DIRECTORY_DEPTH:
                        raise QualificationError(
                            "runtime closure directory depth exceeds the supported limit"
                        )
                    try:
                        child_descriptor = os.open(
                            item.name,
                            directory_flags,
                            dir_fd=descriptor,
                        )
                    except OSError as error:
                        raise QualificationError(
                            "runtime closure directory cannot be opened"
                        ) from error
                    try:
                        try:
                            child_metadata = os.fstat(child_descriptor)
                        except OSError as error:
                            raise QualificationError(
                                "runtime closure directory cannot be observed"
                            ) from error
                        item_binding = stable_stat_binding(item_metadata)
                        child_binding = stable_stat_binding(child_metadata)
                        if child_binding[:2] != item_binding[:2]:
                            raise QualificationError(
                                "runtime closure directory identity changed"
                            )
                        if child_binding != item_binding:
                            raise QualificationError(
                                "runtime closure directory binding changed"
                            )
                        scan_directory(
                            path,
                            child_descriptor,
                            child_binding,
                            depth + 1,
                        )
                    finally:
                        close_descriptor(
                            child_descriptor,
                            "runtime closure directory",
                        )
        except QualificationError:
            raise
        except OSError as error:
            raise QualificationError(
                "runtime closure root cannot be scanned"
            ) from error
        try:
            after = os.fstat(descriptor)
        except OSError as error:
            raise QualificationError(
                "runtime closure root cannot be scanned"
            ) from error
        if stable_stat_binding(after) != opened_binding:
            raise QualificationError("runtime closure directory binding changed")

    try:
        if expected_root_binding is not None:
            if root_binding[:2] != expected_root_binding[:2]:
                raise QualificationError("runtime closure root identity changed")
            if root_binding != expected_root_binding:
                raise QualificationError("runtime closure root binding changed")
        scan_directory(root, root_descriptor, root_binding, 0)
    finally:
        close_descriptor(root_descriptor, "runtime closure directory")
    return observed, root_binding


def require_paths_immutable(
    paths: set[Path],
    label: str,
    *,
    expected_bindings: dict[Path, StatBinding] | None = None,
) -> None:
    """Require paths and their ancestors to be outside the current user's authority."""

    euid = os.geteuid()
    observed_paths: set[Path] = set()
    for path in paths:
        current = path
        while True:
            observed_paths.add(current)
            if current == current.parent:
                break
            current = current.parent
    for path in sorted(observed_paths):
        try:
            metadata = path.lstat()
        except OSError as error:
            raise QualificationError(
                f"{label} immutable path is unavailable"
            ) from error
        if expected_bindings is not None and path in expected_bindings:
            observed_binding = stable_stat_binding(metadata)
            expected_binding = expected_bindings[path]
            if observed_binding[:2] != expected_binding[:2]:
                raise QualificationError(f"{label} immutable entry identity changed")
            if observed_binding != expected_binding:
                raise QualificationError(f"{label} immutable entry binding changed")
        if metadata.st_uid == euid:
            raise QualificationError(f"{label} is mutable by the qualification user")
        if not stat.S_ISLNK(metadata.st_mode) and effective_access(path, os.W_OK):
            raise QualificationError(f"{label} is mutable by the qualification user")


def require_runtime_closure_immutable(
    evidence: dict[str, Any],
    path_bindings: dict[Path, StatBinding],
) -> None:
    """Require runtime paths to be outside the qualification user's authority."""

    paths = {Path(item["path"]) for item in evidence["closure"]["entries"]}
    paths.update(Path(item["path"]) for item in evidence["closure"]["roots"])
    require_paths_immutable(
        paths,
        "runtime closure",
        expected_bindings=path_bindings,
    )


def runtime_entry_observation(
    path: Path,
    recorded: dict[str, Any],
) -> tuple[StatBinding, dict[str, Any]]:
    if recorded["kind"] == "regular-file":
        opened, observed_length, observed_sha256 = regular_file_observation(
            path,
            "runtime closure entry",
            expected_length=recorded["length"],
        )
        binding = stable_stat_binding(opened)
        observed = {
            "uid": opened.st_uid,
            "gid": opened.st_gid,
            "length": observed_length,
            "sha256": observed_sha256,
            "mode": stat.S_IMODE(opened.st_mode),
        }
        if recorded["role"] == "main-executable":
            require_safe_executable_mode(observed["mode"], "runtime executable mode")
    elif recorded["kind"] == "directory":
        try:
            metadata = path.lstat()
        except OSError as error:
            raise QualificationError("runtime closure entry is unavailable") from error
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise QualificationError("runtime closure directory kind disagrees")
        binding = stable_stat_binding(metadata)
        observed = {
            "uid": metadata.st_uid,
            "gid": metadata.st_gid,
            "mode": stat.S_IMODE(metadata.st_mode),
        }
    else:
        link_metadata, target = symlink_observation(path, "runtime closure symlink")
        binding = stable_stat_binding(link_metadata)
        observed = {
            "uid": link_metadata.st_uid,
            "gid": link_metadata.st_gid,
            "target": target,
        }
    if any(recorded[key] != observed[key] for key in ("uid", "gid")):
        raise QualificationError("runtime closure entry ownership disagrees")
    if any(recorded[key] != value for key, value in observed.items()):
        raise QualificationError("runtime closure entry binding disagrees")
    return binding, observed


def validate_runtime_observations(
    runtime_executable: Path,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    expected_executable = Path(evidence["main_executable"]["path"])
    if runtime_executable != expected_executable:
        raise QualificationError("runtime executable path disagrees with evidence")
    recorded_entries = {
        Path(item["path"]): item for item in evidence["closure"]["entries"]
    }
    roots = [Path(item["path"]) for item in evidence["closure"]["roots"]]
    expected_paths = set(recorded_entries)
    observed_paths: set[Path] = set()
    initial_root_bindings: dict[Path, StatBinding] = {}
    for root in roots:
        if any(
            root == prior or root.is_relative_to(prior) or prior.is_relative_to(root)
            for prior in roots
            if prior != root
        ):
            raise QualificationError("runtime closure roots overlap")
        root_paths, root_binding = scan_runtime_root(root, expected_paths)
        initial_root_bindings[root] = root_binding
        observed_paths.update(root_paths)
    if observed_paths != expected_paths:
        raise QualificationError("runtime closure complete inventory disagrees")
    initial_entry_bindings = {
        path: runtime_entry_observation(path, recorded)[0]
        for path, recorded in recorded_entries.items()
    }
    if not effective_access(runtime_executable, os.X_OK):
        raise QualificationError("runtime executable is not executable")
    final_observed_paths: set[Path] = set()
    final_root_bindings: dict[Path, StatBinding] = {}
    for root in roots:
        initial_binding = initial_root_bindings[root]
        root_paths, final_binding = scan_runtime_root(
            root,
            expected_paths,
            expected_root_binding=initial_binding,
        )
        if final_binding[:2] != initial_binding[:2]:
            raise QualificationError("runtime closure root identity changed")
        if final_binding != initial_binding:
            raise QualificationError("runtime closure root binding changed")
        final_root_bindings[root] = final_binding
        final_observed_paths.update(root_paths)
    if final_observed_paths != expected_paths:
        raise QualificationError("runtime closure complete inventory changed")
    final_entry_bindings: dict[Path, StatBinding] = {}
    for path, recorded in recorded_entries.items():
        final_binding, _observed = runtime_entry_observation(path, recorded)
        initial_binding = initial_entry_bindings[path]
        if final_binding[:2] != initial_binding[:2]:
            raise QualificationError("runtime closure entry identity changed")
        if final_binding != initial_binding:
            raise QualificationError("runtime closure entry binding changed")
        final_entry_bindings[path] = final_binding
    require_runtime_closure_immutable(
        evidence,
        {**final_entry_bindings, **final_root_bindings},
    )
    roots_observation = [
        {
            "path": str(root),
            "stat": _stat_projection_from_binding(final_root_bindings[root]),
        }
        for root in roots
    ]
    entries_observation: list[dict[str, Any]] = []
    main_executable_observation: dict[str, Any] | None = None
    for recorded in evidence["closure"]["entries"]:
        path = Path(recorded["path"])
        binding = final_entry_bindings[path]
        item: dict[str, Any] = {
            "kind": recorded["kind"],
            "path": str(path),
            "stat": _stat_projection_from_binding(binding),
        }
        if recorded["kind"] == "regular-file":
            item.update(
                {
                    "length": recorded["length"],
                    "sha256": recorded["sha256"],
                }
            )
        elif recorded["kind"] == "symlink":
            item["target"] = recorded["target"]
        entries_observation.append(item)
        if recorded["role"] == "main-executable":
            main_executable_observation = {
                "path": str(path),
                "length": recorded["length"],
                "sha256": recorded["sha256"],
                "uid": recorded["uid"],
                "gid": recorded["gid"],
                "mode": recorded["mode"],
                "nlink": binding[5],
            }
    if main_executable_observation is None:
        raise QualificationError("runtime main executable observation is unavailable")
    closure_observation = {
        "contract": "task-witness-runtime-closure-observation-v1",
        "roots": roots_observation,
        "entries": entries_observation,
    }
    canonical_json_bytes(
        closure_observation,
        maximum=RUNTIME_CLOSURE_OBSERVATION_MAX_BYTES,
        label="runtime closure observation",
    )
    return {
        "main_executable_observation": main_executable_observation,
        "closure_observation": closure_observation,
    }


def load_canonical_json_document(
    path: Path,
    label: str,
) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    descriptor, opened = open_absolute_path_without_symlinks(
        path,
        label,
        expected_kind="regular",
    )
    try:
        if opened.st_size > MAX_JSON_BYTES:
            raise QualificationError(f"{label} exceeds the byte limit")
        chunks: list[bytes] = []
        remaining = MAX_JSON_BYTES + 1
        while remaining:
            try:
                chunk = os.read(descriptor, min(remaining, 64 * 1024))
            except OSError as error:
                raise QualificationError(f"{label} cannot be read") from error
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > MAX_JSON_BYTES:
            raise QualificationError(f"{label} exceeds the byte limit")
        try:
            after = os.fstat(descriptor)
        except OSError as error:
            raise QualificationError(f"{label} cannot be read") from error
        if (
            stable_stat_binding(opened) != stable_stat_binding(after)
            or len(raw) != after.st_size
        ):
            raise QualificationError(f"{label} changed while it was read")
    finally:
        close_descriptor(descriptor, label)

    current_descriptor = -1
    try:
        current_descriptor, current = open_absolute_path_without_symlinks(
            path,
            label,
            expected_kind="regular",
        )
    except QualificationError as error:
        raise QualificationError(f"{label} changed while it was read") from error
    finally:
        if current_descriptor >= 0:
            close_descriptor(current_descriptor, label)
    if stable_stat_binding(after) != stable_stat_binding(current):
        raise QualificationError(f"{label} changed while it was read")

    value = decode_canonical_json(
        raw,
        maximum=MAX_JSON_BYTES,
        expected_root=dict,
        label=label,
    )
    binding = {
        "path": str(path),
        "stat": _live_stat(after),
        "length": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    if after.st_nlink != 1:
        raise QualificationError(f"{label} has a hard-link alias")
    return value, raw, binding


def load_canonical_json_object(path: Path, label: str) -> dict[str, Any]:
    return load_canonical_json_document(path, label)[0]


def _owned_leader_terminal(process: subprocess.Popen[bytes], label: str) -> bool:
    """Observe a session leader without reaping it or releasing its PGID."""

    try:
        result = os.waitid(
            os.P_PID,
            process.pid,
            os.WEXITED | os.WNOHANG | os.WNOWAIT,
        )
    except ChildProcessError as error:
        raise QualificationError(f"{label} child ownership changed") from error
    return result is not None


def _darwin_sysctl_process_bytes(selector: int, value: int, label: str) -> int:
    """Return the exact byte size of a Darwin process-selection result."""

    libc = ctypes.CDLL(None, use_errno=True)
    sysctl = libc.sysctl
    sysctl.argtypes = (
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p,
        ctypes.c_size_t,
    )
    sysctl.restype = ctypes.c_int
    mib = (ctypes.c_int * 4)(1, 14, selector, value)
    required = ctypes.c_size_t()
    if sysctl(mib, 4, None, ctypes.byref(required), None, 0) != 0:
        error_number = ctypes.get_errno()
        raise QualificationError(
            f"{label} process group cannot be observed"
        ) from OSError(
            error_number,
            os.strerror(error_number),
        )
    if required.value == 0 or required.value > MAX_GIT_STDOUT_BYTES:
        raise QualificationError(f"{label} process group observation is invalid")
    storage = ctypes.create_string_buffer(required.value)
    actual = ctypes.c_size_t(required.value)
    if sysctl(mib, 4, storage, ctypes.byref(actual), None, 0) != 0:
        error_number = ctypes.get_errno()
        raise QualificationError(
            f"{label} process group cannot be observed"
        ) from OSError(
            error_number,
            os.strerror(error_number),
        )
    return actual.value


def _owned_group_has_other_members(
    process: subprocess.Popen[bytes],
    label: str,
) -> bool:
    """Check the owned PGID while its direct leader is still waitable."""

    if sys.platform == "darwin":
        leader_size = _darwin_sysctl_process_bytes(1, process.pid, label)
        group_size = _darwin_sysctl_process_bytes(2, process.pid, label)
        if group_size < leader_size or group_size % leader_size != 0:
            raise QualificationError(f"{label} process group observation is invalid")
        return group_size > leader_size
    if sys.platform.startswith("linux"):
        try:
            entries = Path("/proc").iterdir()
        except OSError as error:
            raise QualificationError(
                f"{label} process group cannot be observed"
            ) from error
        members = 0
        observed = 0
        try:
            for entry in entries:
                if not entry.name.isdecimal():
                    continue
                observed += 1
                if observed > MAX_RUNTIME_CLOSURE_ENTRIES:
                    raise QualificationError(
                        f"{label} process group observation is too large"
                    )
                try:
                    stat_raw = (entry / "stat").read_bytes()
                except FileNotFoundError:
                    continue
                except OSError as error:
                    raise QualificationError(
                        f"{label} process group cannot be observed"
                    ) from error
                closing = stat_raw.rfind(b") ")
                fields = stat_raw[closing + 2 :].split() if closing >= 0 else []
                if len(fields) < 3:
                    raise QualificationError(
                        f"{label} process group observation is invalid"
                    )
                try:
                    process_group = int(fields[2])
                except ValueError as error:
                    raise QualificationError(
                        f"{label} process group observation is invalid"
                    ) from error
                if process_group == process.pid:
                    members += 1
                    if members > 1:
                        return True
        except OSError as error:
            raise QualificationError(
                f"{label} process group cannot be observed"
            ) from error
        if members != 1:
            raise QualificationError(f"{label} process group observation is invalid")
        return False
    raise QualificationError(f"{label} process groups are unsupported on this platform")


def _signal_owned_group(
    process: subprocess.Popen[bytes],
    signal_number: int,
    label: str,
) -> None:
    """Signal an owned PGID only while its direct leader remains unreaped."""

    try:
        os.killpg(process.pid, signal_number)
    except (PermissionError, ProcessLookupError) as error:
        raise QualificationError(
            f"{label} process group cannot be signalled"
        ) from error


def _quiesce_owned_descendants(
    process: subprocess.Popen[bytes],
    *,
    deadline: float,
    label: str,
) -> None:
    """Kill and observe terminal-leader descendants before releasing the PGID."""

    if not _owned_leader_terminal(process, label):
        raise QualificationError(f"{label} leader is not terminal")
    if not _owned_group_has_other_members(process, label):
        return
    _signal_owned_group(process, signal.SIGKILL, label)
    while _owned_group_has_other_members(process, label):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise QualificationError(f"{label} process group remained present")
        time.sleep(min(remaining, 0.01))


def _settle_owned_process(
    process: subprocess.Popen[bytes],
    *,
    deadline: float,
    label: str,
    terminate: bool,
) -> int:
    """Quiesce an owned session, then and only then reap its direct leader."""

    cleanup_deadline = max(deadline, time.monotonic() + 1.0)
    terminal = _owned_leader_terminal(process, label)
    if terminate and (not terminal or _owned_group_has_other_members(process, label)):
        _signal_owned_group(process, signal.SIGTERM, label)
        term_deadline = min(cleanup_deadline, time.monotonic() + 0.1)
        while time.monotonic() < term_deadline:
            terminal = _owned_leader_terminal(process, label)
            if terminal and not _owned_group_has_other_members(process, label):
                break
            time.sleep(min(term_deadline - time.monotonic(), 0.01))
    terminal = _owned_leader_terminal(process, label)
    if not terminal:
        _signal_owned_group(process, signal.SIGKILL, label)
        while not _owned_leader_terminal(process, label):
            remaining = cleanup_deadline - time.monotonic()
            if remaining <= 0:
                raise QualificationError(f"{label} leader did not terminate")
            time.sleep(min(remaining, 0.01))
    _quiesce_owned_descendants(
        process,
        deadline=cleanup_deadline,
        label=label,
    )
    try:
        return process.wait(timeout=0)
    except subprocess.TimeoutExpired as error:
        raise QualificationError(f"{label} leader cannot be reaped") from error


def _bounded_process(
    argv: list[str],
    *,
    env: dict[str, str],
    stdout_maximum: int,
    stderr_maximum: int,
    label: str,
    stdin: bytes | None = None,
    timeout_seconds: float = GIT_TIMEOUT_SECONDS,
    own_process_group: bool = False,
    cwd: Path | None = None,
) -> tuple[int, bytes, bytes]:
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            close_fds=True,
            start_new_session=own_process_group,
            cwd=cwd,
        )
    except OSError as error:
        raise QualificationError(f"{label} cannot start") from error
    deadline = time.monotonic() + timeout_seconds
    chunks: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
    lengths = {"stdout": 0, "stderr": 0}
    selector: selectors.BaseSelector | None = None
    leader_reaped = False
    input_offset = 0
    try:
        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        for stream, name, maximum in (
            (process.stdout, "stdout", stdout_maximum),
            (process.stderr, "stderr", stderr_maximum),
        ):
            descriptor = stream.fileno()
            os.set_blocking(descriptor, False)
            selector.register(
                descriptor,
                selectors.EVENT_READ,
                (name, maximum),
            )
        if stdin is not None:
            assert process.stdin is not None
            if stdin:
                descriptor = process.stdin.fileno()
                os.set_blocking(descriptor, False)
                selector.register(
                    descriptor,
                    selectors.EVENT_WRITE,
                    ("stdin", len(stdin)),
                )
            else:
                process.stdin.close()
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise QualificationError(f"{label} timed out")
            events = selector.select(
                min(remaining, 0.05) if own_process_group else remaining
            )
            if not events:
                if own_process_group and _owned_leader_terminal(process, label):
                    _quiesce_owned_descendants(
                        process,
                        deadline=deadline,
                        label=label,
                    )
                    continue
                if time.monotonic() >= deadline:
                    raise QualificationError(f"{label} timed out")
                continue
            for key, mask in events:
                descriptor = int(key.fd)
                name, maximum = key.data
                if name == "stdin" and mask & selectors.EVENT_WRITE:
                    try:
                        written = os.write(
                            descriptor,
                            stdin[input_offset : input_offset + 64 * 1024],
                        )
                    except BlockingIOError:
                        continue
                    except (BrokenPipeError, OSError) as error:
                        raise QualificationError(
                            f"{label} input cannot be written"
                        ) from error
                    if written <= 0:
                        raise QualificationError(f"{label} input cannot be written")
                    input_offset += written
                    if input_offset == maximum:
                        selector.unregister(descriptor)
                        assert process.stdin is not None
                        process.stdin.close()
                    continue
                if not mask & selectors.EVENT_READ:
                    continue
                try:
                    chunk = os.read(descriptor, min(64 * 1024, maximum + 1))
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(descriptor)
                    continue
                lengths[name] += len(chunk)
                if lengths[name] > maximum:
                    raise QualificationError(f"{label} {name} exceeds the byte limit")
                chunks[name].append(chunk)
        if own_process_group:
            while not _owned_leader_terminal(process, label):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise QualificationError(f"{label} timed out")
                time.sleep(min(remaining, 0.01))
            status = _settle_owned_process(
                process,
                deadline=deadline,
                label=label,
                terminate=False,
            )
            leader_reaped = True
        else:
            try:
                status = process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as error:
                raise QualificationError(f"{label} timed out") from error
    except BaseException:
        if own_process_group and not leader_reaped:
            _settle_owned_process(
                process,
                deadline=time.monotonic() + 1.0,
                label=label,
                terminate=True,
            )
        else:
            process.kill()
            process.wait()
        raise
    finally:
        if selector is not None:
            selector.close()
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
    return status, b"".join(chunks["stdout"]), b"".join(chunks["stderr"])


def run_recorded_git(
    git_executable: Path,
    candidate_root: Path,
    *arguments: str,
    allowed_statuses: tuple[int, ...] = (0,),
) -> bytes:
    """Run one bounded, noninteractive, no-replacement Git observation."""

    if not git_executable.is_absolute():
        raise QualificationError("recorded Git executable is invalid")
    argv = [
        str(git_executable),
        "--no-replace-objects",
        "-c",
        f"safe.directory={candidate_root}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.attributesFile=/dev/null",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "protocol.allow=never",
        "-c",
        "protocol.file.allow=never",
        "-c",
        "fetch.fsckObjects=true",
        "-c",
        "transfer.fsckObjects=true",
        "-C",
        str(candidate_root),
        *arguments,
    ]
    env = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_EDITOR": "/usr/bin/false",
        "GIT_PAGER": "/usr/bin/false",
        "GIT_SEQUENCE_EDITOR": "/usr/bin/false",
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",
        "GIT_ASKPASS": "/usr/bin/false",
        "GIT_SSH_COMMAND": "/usr/bin/false",
        "SSH_ASKPASS": "/usr/bin/false",
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "HOME": "/nonexistent-task-witness-home",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "PAGER": "/usr/bin/false",
    }
    status, stdout, stderr = _bounded_process(
        argv,
        env=env,
        stdout_maximum=MAX_GIT_STDOUT_BYTES,
        stderr_maximum=MAX_PROCESS_STDERR_BYTES,
        label="candidate Git observation",
    )
    if status not in allowed_statuses:
        raise QualificationError("candidate Git observation failed")
    if status == 0 and stderr:
        raise QualificationError("candidate Git observation produced diagnostics")
    return stdout


def _git_text(
    git_executable: Path,
    candidate_root: Path,
    *arguments: str,
) -> str:
    raw = run_recorded_git(git_executable, candidate_root, *arguments)
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise QualificationError("candidate Git text is invalid") from error
    if not value.endswith("\n") or "\x00" in value:
        raise QualificationError("candidate Git text is invalid")
    return value[:-1]


def _live_stat(metadata: os.stat_result) -> dict[str, int]:
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mode": metadata.st_mode,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "nlink": metadata.st_nlink,
        "size": metadata.st_size,
        "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
    }


def _projected_stat_binding(value: dict[str, int]) -> StatBinding:
    return (
        value["device"],
        value["inode"],
        value["mode"],
        value["uid"],
        value["gid"],
        value["nlink"],
        value["size"],
        value["mtime_ns"],
        value["ctime_ns"],
    )


def _directory_binding(path: Path, label: str) -> dict[str, Any]:
    _path, metadata = require_directory_observation(path, label)
    return {"path": str(path), "stat": _live_stat(metadata)}


def _regular_binding(
    path: Path,
    label: str,
    *,
    expected_length: int,
) -> dict[str, Any]:
    metadata, length, digest = regular_file_observation(
        path,
        label,
        expected_length=expected_length,
    )
    if metadata.st_nlink != 1:
        raise QualificationError(f"{label} has a hard-link alias")
    return {
        "path": str(path),
        "stat": _live_stat(metadata),
        "length": length,
        "sha256": digest,
    }


def capture_platform_state(
    profile_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    profile, raw, profile_binding = load_canonical_json_document(
        profile_path,
        "platform profile",
    )
    parse_platform_profile(profile)
    retained = validate_platform_observations(profile)
    tool_observations = retained["system_tools"]
    receipt = {
        "profile_sha256": hashlib.sha256(raw).hexdigest(),
        "profile": profile,
        "credential_state": retained["credential_state"],
        "system_tool_observation_sha256": hashlib.sha256(
            canonical_json_bytes(tool_observations)
        ).hexdigest(),
    }
    observation = {
        "contract": "task-witness-tw4-platform-observation-v1",
        "profile_file": profile_binding,
        "home": retained["home"],
        "credential_state": retained["credential_state"],
        "system_tools": tool_observations,
    }
    return receipt, observation


def capture_runtime_state(
    runtime_executable: Path,
    evidence_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence, raw, evidence_binding = load_canonical_json_document(
        evidence_path,
        "runtime closure evidence",
    )
    parse_runtime_closure_evidence(evidence)
    retained = validate_runtime_observations(runtime_executable, evidence)
    closure_observation = retained["closure_observation"]
    receipt = {
        "evidence_sha256": hashlib.sha256(raw).hexdigest(),
        "evidence": evidence,
        "main_executable_observation": retained["main_executable_observation"],
        "closure_observation_sha256": hashlib.sha256(
            canonical_json_bytes(closure_observation)
        ).hexdigest(),
    }
    observation = {
        "contract": "task-witness-tw4-runtime-observation-v1",
        "evidence_file": evidence_binding,
        "main_executable_observation": retained["main_executable_observation"],
        "closure_observation": closure_observation,
    }
    return receipt, observation


def run_applicable_suites(
    candidate_root: Path,
    runtime_executable: Path,
    inventory: dict[str, Any],
    target: str,
    workspace: Path,
    home: Path,
) -> list[dict[str, Any]]:
    if target not in PLATFORM_TARGETS:
        raise QualificationError("qualification suite target is invalid")
    environment = {
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "TASK_WITNESS_QUALIFICATION_WORKSPACE": str(workspace),
        "TZ": "UTC",
    }
    records: list[dict[str, Any]] = []
    for entry in inventory["entries"]:
        if target not in entry["targets"]:
            continue
        argv = [str(runtime_executable), *entry["argv"]]
        status, stdout, stderr = _bounded_process(
            argv,
            env=environment,
            stdout_maximum=MAX_JSON_BYTES,
            stderr_maximum=MAX_PROCESS_STDERR_BYTES,
            label=f"qualification suite {entry['id']}",
            stdin=None,
            timeout_seconds=SUITE_TIMEOUT_SECONDS,
            own_process_group=True,
            cwd=candidate_root,
        )
        if status != 0 or stderr:
            raise QualificationError(f"qualification suite {entry['id']} failed")
        result = parse_suite_result(
            decode_canonical_json(
                stdout,
                maximum=MAX_JSON_BYTES,
                expected_root=dict,
                label=f"qualification suite {entry['id']} result",
            )
        )
        if (
            result["id"] != entry["id"]
            or result["observed_count"] != entry["expected_count"]
            or result["terminal"] != entry["expected_terminal"]
        ):
            raise QualificationError(
                f"qualification suite {entry['id']} result binding disagrees"
            )
        records.append(
            {
                "id": entry["id"],
                "expected_count": entry["expected_count"],
                "expected_terminal": entry["expected_terminal"],
                "process": {
                    "exit_status": status,
                    "stdout_length": len(stdout),
                    "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
                    "stderr_length": len(stderr),
                    "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
                },
                "result": result,
            }
        )
    if len(records) != 15:
        raise QualificationError("qualification suite applicable inventory disagrees")
    canonical_json_bytes(
        records,
        maximum=HOST_RECEIPT_MEMBER_CAPS["suite_results"],
        label="qualification suite results",
    )
    return records


def capture_host_state(
    candidate_root: Path,
    runtime_executable: Path,
    runtime_evidence_path: Path,
    platform_profile_path: Path,
) -> dict[str, Any]:
    platform_receipt, platform_observation = capture_platform_state(
        platform_profile_path
    )
    runtime_receipt, runtime_observation = capture_runtime_state(
        runtime_executable,
        runtime_evidence_path,
    )
    profile = platform_receipt["profile"]
    git_paths = [
        tool["resolved_path"]
        for tool in profile["system_tools"]
        if tool["id"] == "git"
    ]
    if len(git_paths) != 1:
        raise QualificationError("platform Git tool binding disagrees")
    candidate_observations = observe_candidate(
        candidate_root,
        Path(git_paths[0]),
        runtime_executable=runtime_executable,
    )
    inventory_path = candidate_root / "release/task-witness/tw4-suite-inventory.json"
    inventory_value, inventory_raw, inventory_binding = load_canonical_json_document(
        inventory_path,
        "candidate suite inventory",
    )
    inventory = parse_suite_inventory(inventory_value)
    expected_binding = candidate_observations["suite_inventory"]["file"]
    expected_summary = candidate_observations["suite_inventory"]["suite_inventory"]
    if (
        inventory_binding != expected_binding
        or len(inventory_raw) != expected_summary["length"]
        or hashlib.sha256(inventory_raw).hexdigest() != expected_summary["sha256"]
    ):
        raise QualificationError("candidate suite inventory binding changed")
    inputs = {
        "candidate": candidate_observations["candidate"],
        "bridge_history": candidate_observations["bridge_history"],
        "suite_inventory": candidate_observations["suite_inventory"],
        "platform": platform_observation,
        "runtime": runtime_observation,
    }
    for name, maximum in HOST_OBSERVATION_INPUT_CAPS.items():
        canonical_json_bytes(
            inputs[name],
            maximum=maximum,
            label=f"host observation input {name}",
        )
    candidate = inputs["candidate"]
    known_members = {
        "qualification_candidate": candidate["qualification_candidate"],
        "candidate_closure": candidate["candidate_closure"],
        "bridge_history": inputs["bridge_history"]["bridge_history"],
        "suite_inventory": inputs["suite_inventory"]["suite_inventory"],
        "platform": platform_receipt,
        "runtime": runtime_receipt,
    }
    for name, value in known_members.items():
        canonical_json_bytes(
            value,
            maximum=HOST_RECEIPT_MEMBER_CAPS[name],
            label=f"host receipt {name}",
        )
    stability = _host_input_digests(inputs)
    canonical_json_bytes(
        {
            "contract": "task-witness-tw4-host-input-stability-v1",
            "inputs": inputs,
            "before": stability,
            "after": stability,
        },
        maximum=HOST_RECEIPT_MEMBER_CAPS["observations"],
        label="host receipt observations",
    )
    return {
        "target": profile["target"],
        "platform": platform_receipt,
        "runtime": runtime_receipt,
        "inputs": inputs,
        "inventory": inventory,
    }


def _host_input_digests(inputs: dict[str, Any]) -> dict[str, str]:
    return {
        f"{name}_sha256": hashlib.sha256(
            canonical_json_bytes(
                inputs[name],
                maximum=HOST_OBSERVATION_INPUT_CAPS[name],
                label=f"host observation input {name}",
            )
        ).hexdigest()
        for name in (
            "candidate",
            "bridge_history",
            "suite_inventory",
            "platform",
            "runtime",
        )
    }


def construct_host_qualification_receipt(
    before: dict[str, Any],
    after: dict[str, Any],
    rendered_shim: dict[str, Any],
    suite_results: list[dict[str, Any]],
) -> tuple[dict[str, Any], bytes]:
    required_state = {"target", "platform", "runtime", "inputs", "inventory"}
    if set(before) != required_state or set(after) != required_state:
        raise QualificationError("host state capture shape disagrees")
    before_inputs = before["inputs"]
    after_inputs = after["inputs"]
    before_raw = canonical_json_bytes(before_inputs, label="host inputs before")
    after_raw = canonical_json_bytes(after_inputs, label="host inputs after")
    if (
        before_raw != after_raw
        or before["target"] != after["target"]
        or canonical_json_bytes(before["platform"])
        != canonical_json_bytes(after["platform"])
        or canonical_json_bytes(before["runtime"])
        != canonical_json_bytes(after["runtime"])
        or canonical_json_bytes(before["inventory"])
        != canonical_json_bytes(after["inventory"])
    ):
        raise QualificationError("host input changed during qualification")
    before_digests = _host_input_digests(before_inputs)
    after_digests = _host_input_digests(after_inputs)
    if before_digests != after_digests:
        raise QualificationError("host input changed during qualification")
    if len(suite_results) != 15:
        raise QualificationError("qualification suite result inventory disagrees")
    candidate = before_inputs["candidate"]
    bridge_observation = before_inputs["bridge_history"]
    inventory_observation = before_inputs["suite_inventory"]
    unsigned = {
        "schema_version": 1,
        "contract": "task-witness-tw4-host-qualification-receipt-v1",
        "qualification_candidate": candidate["qualification_candidate"],
        "candidate_closure": candidate["candidate_closure"],
        "bridge_history": bridge_observation["bridge_history"],
        "suite_inventory": inventory_observation["suite_inventory"],
        "target": before["target"],
        "platform": before["platform"],
        "runtime": before["runtime"],
        "rendered_shim": rendered_shim,
        "observations": {
            "contract": "task-witness-tw4-host-input-stability-v1",
            "inputs": before_inputs,
            "before": before_digests,
            "after": after_digests,
        },
        "suite_results": suite_results,
        "disposition": "qualified",
    }
    for name, maximum in HOST_RECEIPT_MEMBER_CAPS.items():
        canonical_json_bytes(
            unsigned[name],
            maximum=maximum,
            label=f"host receipt {name}",
        )
    content_sha256 = hashlib.sha256(
        canonical_json_bytes(
            unsigned,
            maximum=HOST_RECEIPT_MAX_BYTES,
            label="host receipt content",
        )
    ).hexdigest()
    receipt = {**unsigned, "content_sha256": content_sha256}
    raw = canonical_json_bytes(
        receipt,
        maximum=HOST_RECEIPT_MAX_BYTES,
        label="host qualification receipt",
    )
    return receipt, raw


def _git_tree_entries(
    git_executable: Path,
    candidate_root: Path,
    commit: str,
) -> dict[str, tuple[str, str, str]]:
    raw = run_recorded_git(
        git_executable,
        candidate_root,
        "ls-tree",
        "-r",
        "-t",
        "-z",
        "--full-tree",
        commit,
    )
    entries: dict[str, tuple[str, str, str]] = {}
    try:
        rows = raw.split(b"\0")
        if rows[-1] != b"":
            raise ValueError
        for row in rows[:-1]:
            if len(entries) >= MAX_RUNTIME_CLOSURE_ENTRIES:
                raise QualificationError("candidate Git tree exceeds the entry limit")
            left, raw_path = row.split(b"\t", 1)
            mode, kind, oid = left.decode("ascii").split(" ")
            path = raw_path.decode("utf-8")
            if (
                path in entries
                or not path
                or path.startswith("/")
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or (kind, mode)
                not in {
                    ("blob", "100644"),
                    ("blob", "100755"),
                    ("blob", "120000"),
                    ("commit", "160000"),
                    ("tree", "040000"),
                }
                or re.fullmatch(r"[0-9a-f]{40}", oid) is None
            ):
                raise ValueError
            entries[path] = (mode, kind, oid)
    except (UnicodeDecodeError, ValueError) as error:
        raise QualificationError("candidate Git tree is invalid") from error
    return entries


def _strict_json_object(raw: bytes, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise QualificationError(f"{label} contains a duplicate key")
            result[key] = value
        return result

    def reject_number(value: str) -> None:
        raise QualificationError(f"{label} contains an unsupported number: {value}")

    def integer(value: str) -> int:
        if not value.isdecimal() or len(value) > 20:
            raise QualificationError(f"{label} contains an invalid integer")
        parsed = int(value)
        if parsed > UINT64_MAX:
            raise QualificationError(f"{label} integer is outside uint64")
        return parsed

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=reject_number,
            parse_float=reject_number,
            parse_int=integer,
        )
    except QualificationError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ) as error:
        raise QualificationError(f"{label} is not valid JSON") from error
    if not isinstance(value, dict):
        raise QualificationError(f"{label} must contain an object")
    _validate_canonical_value(value, label)
    return value


def _bridge_generation_projection(value: object, label: str) -> dict[str, Any]:
    generation = exact_object(
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
        generation["repository_id"] != CANDIDATE_REPOSITORY_ID
        or generation["source_mode"] != "harness_snapshot"
    ):
        raise QualificationError(f"{label} identity disagrees")
    for field in ("commit_sha1", "tree_sha1"):
        value = generation[field]
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
            raise QualificationError(f"{label} {field} is invalid")
    for field in (
        "plugin_subtree_sha256",
        "controller_sha256",
        "policy_sha256",
        "client_sha256",
    ):
        sha256_text(generation[field], f"{label} {field}")
    return generation


def parse_bridge_history_projection(value: object) -> dict[str, Any]:
    history = exact_object(
        value,
        {
            "bridge_identity_sha256",
            "bridge_provenance_sha256",
            "freeze5",
            "bridge",
        },
        "candidate bridge history",
    )
    sha256_text(
        history["bridge_identity_sha256"],
        "candidate bridge identity digest",
    )
    sha256_text(
        history["bridge_provenance_sha256"],
        "candidate bridge provenance digest",
    )
    _bridge_generation_projection(history["freeze5"], "candidate Freeze 5")
    _bridge_generation_projection(history["bridge"], "candidate bridge")
    canonical_json_bytes(
        history,
        maximum=MAX_BRIDGE_HISTORY_BYTES,
        label="candidate bridge history",
    )
    return history


def _bridge_object_sha1(kind: str, raw: bytes) -> str:
    framed = kind.encode("ascii") + b" " + str(len(raw)).encode("ascii") + b"\0" + raw
    return hashlib.sha1(framed).hexdigest()


def _bridge_tree_entries(raw: bytes, label: str) -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []
    offset = 0
    try:
        while offset < len(raw):
            separator = raw.index(b" ", offset)
            terminator = raw.index(b"\0", separator + 1)
            mode = raw[offset:separator].decode("ascii")
            name_raw = raw[separator + 1 : terminator]
            oid_raw = raw[terminator + 1 : terminator + 21]
            if len(oid_raw) != 20:
                raise ValueError
            name = name_raw.decode("utf-8")
            if (
                mode not in {"40000", "100644", "100755"}
                or not name
                or "/" in name
                or name in {".", ".."}
            ):
                raise ValueError
            entries.append((mode, name, oid_raw.hex()))
            offset = terminator + 21
    except (UnicodeDecodeError, ValueError) as error:
        raise QualificationError(f"{label} tree payload is invalid") from error
    if len({name for _mode, name, _oid in entries}) != len(entries):
        raise QualificationError(f"{label} tree contains duplicate names")
    ordered = sorted(
        entries,
        key=lambda entry: (
            entry[1].encode("utf-8") + (b"/" if entry[0] == "40000" else b"")
        ),
    )
    if entries != ordered:
        raise QualificationError(f"{label} tree entry order disagrees")
    return entries


def _bridge_commit_tree(raw: bytes, label: str) -> str:
    header, separator, message = raw.partition(b"\n\n")
    if separator != b"\n\n" or not message.endswith(b"\n"):
        raise QualificationError(f"{label} commit payload is invalid")
    lines = header.splitlines()
    tree_lines = [line for line in lines if line.startswith(b"tree ")]
    if len(tree_lines) != 1 or lines[0] != tree_lines[0]:
        raise QualificationError(f"{label} commit tree header is invalid")
    try:
        tree_oid = tree_lines[0][5:].decode("ascii")
    except UnicodeDecodeError as error:
        raise QualificationError(f"{label} commit tree header is invalid") from error
    if re.fullmatch(r"[0-9a-f]{40}", tree_oid) is None:
        raise QualificationError(f"{label} commit tree header is invalid")
    if any(line.startswith(b"tree ") for line in lines[1:]):
        raise QualificationError(f"{label} commit tree header is invalid")
    return tree_oid


def _decode_bridge_provenance_objects(
    provenance: dict[str, Any],
) -> dict[str, tuple[str, bytes]]:
    encoded_objects = provenance["objects"]
    if not isinstance(encoded_objects, list):
        raise QualificationError(
            "candidate bridge provenance object inventory is invalid"
        )
    objects: dict[str, tuple[str, bytes]] = {}
    observed: list[str] = []
    for index, value in enumerate(encoded_objects):
        item = exact_object(
            value,
            {"type", "sha1", "raw_base64"},
            f"candidate bridge provenance object {index}",
        )
        kind = item["type"]
        oid = item["sha1"]
        encoded = item["raw_base64"]
        if (
            kind not in {"commit", "tree"}
            or not isinstance(oid, str)
            or re.fullmatch(r"[0-9a-f]{40}", oid) is None
            or not isinstance(encoded, str)
        ):
            raise QualificationError("candidate bridge provenance object is invalid")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise QualificationError(
                "candidate bridge provenance object encoding is invalid"
            ) from error
        if (
            base64.b64encode(raw).decode("ascii") != encoded
            or _bridge_object_sha1(kind, raw) != oid
            or oid in objects
        ):
            raise QualificationError(
                "candidate bridge provenance object identity disagrees"
            )
        objects[oid] = (kind, raw)
        observed.append(oid)
    if observed != sorted(observed):
        raise QualificationError("candidate bridge provenance object order disagrees")
    return objects


def _candidate_bridge_source_bytes(
    raw_files: dict[str, bytes],
    generation: str,
    relative: str,
) -> bytes:
    snapshots = CANDIDATE_BRIDGE_SNAPSHOT_PATHS[generation]
    if relative in snapshots:
        key = f"release/task-witness/migration/{generation}/{snapshots[relative]}"
    elif generation == "bridge" and relative == "controller/policy.json":
        key = "release/task-witness/migration/freeze5/controller/policy.json"
    elif relative in CANDIDATE_BRIDGE_STABLE_PATHS:
        key = f"{CANDIDATE_PLUGIN_ROOT}/{relative}"
    else:
        raise QualificationError(f"candidate {generation} plugin inventory disagrees")
    try:
        return raw_files[key]
    except KeyError as error:
        raise QualificationError(
            f"candidate {generation} {relative} source is missing"
        ) from error


def _candidate_required_raw(
    raw_files: dict[str, bytes],
    relative: str,
    label: str,
) -> bytes:
    try:
        return raw_files[relative]
    except KeyError as error:
        raise QualificationError(f"{label} is missing") from error


def _validate_candidate_current_client_boundary(current: bytes) -> None:
    profile = re.compile(rb'(?m)^CLIENT_RELEASE_PROFILE = "([a-z0-9-]+)"$')
    generation = re.compile(
        rb'(?m)^CLIENT_SOURCE_GENERATION_SHA256 = "([0-9a-f]{64})"$'
    )
    profiles = list(profile.finditer(current))
    if len(profiles) != 1 or profiles[0].group(1) != b"tw4-current":
        raise QualificationError("candidate current client release profile disagrees")
    generations = list(generation.finditer(current))
    if len(generations) != 1:
        raise QualificationError("candidate current client identity disagrees")
    start, end = generations[0].span(1)
    normalized = current[:start] + (b"0" * 64) + current[end:]
    digest = hashlib.sha256(normalized).hexdigest().encode("ascii")
    if digest != generations[0].group(1):
        raise QualificationError("candidate current client identity disagrees")


def _walk_candidate_bridge_plugin(
    root_oid: str,
    objects: dict[str, tuple[str, bytes]],
    label: str,
) -> tuple[set[str], set[str], dict[str, tuple[str, str]]]:
    required: set[str] = set()
    directories: set[str] = set()
    files: dict[str, tuple[str, str]] = {}

    def entries(oid: str) -> list[tuple[str, str, str]]:
        value = objects.get(oid)
        if value is None or value[0] != "tree":
            raise QualificationError(f"candidate {label} provenance tree is incomplete")
        required.add(oid)
        return _bridge_tree_entries(value[1], f"candidate {label}")

    roots = entries(root_oid)
    plugins = [entry for entry in roots if entry[:2] == ("40000", "plugins")]
    if len(plugins) != 1:
        raise QualificationError(f"candidate {label} plugins tree disagrees")
    plugin_entries = entries(plugins[0][2])
    task_witness = [
        entry for entry in plugin_entries if entry[:2] == ("40000", "task-witness")
    ]
    if len(task_witness) != 1:
        raise QualificationError(f"candidate {label} Task Witness tree disagrees")

    def descend(oid: str, prefix: str) -> None:
        for mode, name, child in entries(oid):
            relative = f"{prefix}/{name}" if prefix else name
            if mode == "40000":
                directories.add(relative)
                descend(child, relative)
            else:
                if relative in files:
                    raise QualificationError(
                        f"candidate {label} plugin inventory disagrees"
                    )
                files[relative] = (mode, child)

    descend(task_witness[0][2], "")
    return required, directories, files


def _candidate_plugin_subtree_sha256(
    raw_files: dict[str, bytes],
    generation: str,
    directories: set[str],
    files: dict[str, tuple[str, str]],
) -> str:
    entries: list[dict[str, Any]] = [
        {"kind": "directory", "path": relative} for relative in directories
    ]
    for relative in files:
        raw = _candidate_bridge_source_bytes(raw_files, generation, relative)
        entries.append(
            {
                "kind": "file",
                "length": len(raw),
                "path": relative,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    entries.sort(key=lambda entry: str(entry["path"]))
    projection = {"contract": "task-witness-plugin-subtree-v1", "entries": entries}
    return hashlib.sha256(canonical_json_bytes(projection)).hexdigest()


def validate_bridge_history_evidence(
    raw_files: dict[str, bytes],
    child_projection: dict[str, Any],
) -> dict[str, Any]:
    identity_raw = _candidate_required_raw(
        raw_files,
        "release/task-witness/tw4-bridge-identity.json",
        "candidate bridge identity",
    )
    provenance_raw = _candidate_required_raw(
        raw_files,
        "release/task-witness/tw4-bridge-provenance.json",
        "candidate bridge provenance",
    )
    identity = _strict_json_object(identity_raw, "candidate bridge identity")
    provenance = _strict_json_object(provenance_raw, "candidate bridge provenance")
    exact_object(
        identity,
        {
            "schema_version",
            "contract",
            "freeze5",
            "bridge",
            "allowed_edges",
            "provenance_sha256",
            "content_sha256",
        },
        "candidate bridge identity",
    )
    exact_object(
        provenance,
        {
            "schema_version",
            "contract",
            "repository_id",
            "freeze5",
            "bridge",
            "objects",
            "content_sha256",
        },
        "candidate bridge provenance",
    )
    if (
        type(identity["schema_version"]) is not int
        or identity["schema_version"] != 1
        or identity["contract"] != "task-witness-tw4-bridge-identity-v1"
        or type(provenance["schema_version"]) is not int
        or provenance["schema_version"] != 1
        or provenance["contract"] != "task-witness-tw4-bridge-provenance-v1"
        or provenance["repository_id"] != CANDIDATE_REPOSITORY_ID
        or identity["allowed_edges"]
        != [{"from": "freeze5", "source_mode": "harness_snapshot", "to": "bridge"}]
    ):
        raise QualificationError("candidate bridge history contract disagrees")
    require_content_digest(identity, "candidate bridge identity")
    require_content_digest(provenance, "candidate bridge provenance")
    provenance_digest = hashlib.sha256(provenance_raw).hexdigest()
    if identity["provenance_sha256"] != provenance_digest:
        raise QualificationError("candidate bridge provenance digest disagrees")
    projection = parse_bridge_history_projection(
        {
            "bridge_identity_sha256": hashlib.sha256(identity_raw).hexdigest(),
            "bridge_provenance_sha256": provenance_digest,
            "freeze5": identity["freeze5"],
            "bridge": identity["bridge"],
        }
    )
    for generation in ("freeze5", "bridge"):
        expected_identity = CANDIDATE_EXPECTED_BRIDGE_IDENTITIES[generation]
        if any(
            projection[generation][field] != expected
            for field, expected in expected_identity.items()
        ):
            raise QualificationError(
                f"candidate {generation} frozen identity disagrees"
            )
    for generation in ("freeze5", "bridge"):
        proof = exact_object(
            provenance[generation],
            {"commit_sha1", "tree_sha1"},
            f"candidate {generation} provenance",
        )
        if any(
            projection[generation][field] != proof[field]
            for field in ("commit_sha1", "tree_sha1")
        ):
            raise QualificationError(f"candidate {generation} provenance disagrees")
    objects = _decode_bridge_provenance_objects(provenance)
    commit_values: dict[str, bytes] = {}
    for generation in ("freeze5", "bridge"):
        generation_value = projection[generation]
        commit_oid = generation_value["commit_sha1"]
        tree_oid = generation_value["tree_sha1"]
        commit = objects.get(commit_oid)
        root_tree = objects.get(tree_oid)
        if (
            commit is None
            or commit[0] != "commit"
            or root_tree is None
            or root_tree[0] != "tree"
        ):
            raise QualificationError(f"candidate {generation} provenance is incomplete")
        if _bridge_commit_tree(commit[1], f"candidate {generation}") != tree_oid:
            raise QualificationError(f"candidate {generation} commit tree disagrees")
        commit_values[generation] = commit[1]
    freeze5_lines = commit_values["freeze5"].splitlines()
    if (
        not freeze5_lines
        or freeze5_lines[0]
        != b"tree " + projection["freeze5"]["tree_sha1"].encode("ascii")
        or sum(line.startswith(b"tree ") for line in freeze5_lines) != 1
    ):
        raise QualificationError("candidate Freeze 5 commit tree disagrees")
    expected_bridge_commit = (
        f"tree {projection['bridge']['tree_sha1']}\n"
        f"parent {CANDIDATE_FREEZE5_COMMIT_SHA1}\n"
        f"author Ivan D Vasin <ivan@nisavid.io> {CANDIDATE_BRIDGE_COMMIT_TIMESTAMP}\n"
        f"committer Ivan D Vasin <ivan@nisavid.io> {CANDIDATE_BRIDGE_COMMIT_TIMESTAMP}\n"
        "\nfeat(task-witness/b1): freeze transition bridge\n"
    ).encode()
    if commit_values["bridge"] != expected_bridge_commit:
        raise QualificationError("candidate bridge commit payload disagrees")

    root_trees: dict[str, dict[str, tuple[str, str]]] = {}
    plugin_trees: dict[str, dict[str, tuple[str, str]]] = {}
    for generation in ("freeze5", "bridge"):
        root_object = objects[projection[generation]["tree_sha1"]]
        root_trees[generation] = {
            name: (mode, oid)
            for mode, name, oid in _bridge_tree_entries(
                root_object[1],
                f"candidate {generation} root",
            )
        }
        plugins_oid = root_trees[generation].get("plugins", (None, None))[1]
        plugins_object = (
            objects.get(plugins_oid) if isinstance(plugins_oid, str) else None
        )
        if plugins_object is None or plugins_object[0] != "tree":
            raise QualificationError(
                f"candidate {generation} plugins tree is incomplete"
            )
        plugin_trees[generation] = {
            name: (mode, oid)
            for mode, name, oid in _bridge_tree_entries(
                plugins_object[1],
                f"candidate {generation} plugins",
            )
        }
    if set(root_trees["freeze5"]) != set(root_trees["bridge"]) or any(
        name != "plugins" and root_trees["freeze5"][name] != root_trees["bridge"][name]
        for name in root_trees["freeze5"]
    ):
        raise QualificationError("candidate bridge root transition disagrees")
    if set(plugin_trees["freeze5"]) != set(plugin_trees["bridge"]) or any(
        name != "task-witness"
        and plugin_trees["freeze5"][name] != plugin_trees["bridge"][name]
        for name in plugin_trees["freeze5"]
    ):
        raise QualificationError("candidate bridge plugins transition disagrees")

    required_objects = {
        projection["freeze5"]["commit_sha1"],
        projection["bridge"]["commit_sha1"],
    }
    walked: dict[str, tuple[set[str], dict[str, tuple[str, str]]]] = {}
    for generation in ("freeze5", "bridge"):
        trees, directories, files = _walk_candidate_bridge_plugin(
            projection[generation]["tree_sha1"],
            objects,
            generation,
        )
        required_objects.update(trees)
        walked[generation] = (directories, files)
    if set(objects) != required_objects:
        raise QualificationError(
            "candidate bridge provenance object inventory disagrees"
        )

    expected_files = CANDIDATE_BRIDGE_STABLE_PATHS | {
        ".claude-plugin/plugin.json",
        ".codex-plugin/plugin.json",
        "client/task_witness_client.py",
        "controller/policy.json",
        "controller/task_witness_deploy.py",
    }
    current_controller = _candidate_required_raw(
        raw_files,
        f"{CANDIDATE_PLUGIN_ROOT}/controller/task_witness_deploy.py",
        "candidate current controller",
    )
    if current_controller != _candidate_bridge_source_bytes(
        raw_files,
        "bridge",
        "controller/task_witness_deploy.py",
    ):
        raise QualificationError("candidate bridge controller snapshot disagrees")
    current_client = _candidate_required_raw(
        raw_files,
        f"{CANDIDATE_PLUGIN_ROOT}/client/task_witness_client.py",
        "candidate current client",
    )
    _validate_candidate_current_client_boundary(current_client)
    for generation in ("freeze5", "bridge"):
        directories, files = walked[generation]
        if set(files) != expected_files:
            raise QualificationError(
                f"candidate {generation} plugin inventory disagrees"
            )
        for relative, (mode, blob_oid) in files.items():
            raw = _candidate_bridge_source_bytes(raw_files, generation, relative)
            expected_mode = (
                "100755"
                if relative == "controller/task_witness_deploy.py"
                else "100644"
            )
            if generation == "bridge" and relative == "client/task_witness_client.py":
                expected_mode = "100755"
            if mode != expected_mode or _bridge_object_sha1("blob", raw) != blob_oid:
                raise QualificationError(
                    f"candidate {generation} {relative} snapshot disagrees"
                )
        if (
            _candidate_plugin_subtree_sha256(
                raw_files,
                generation,
                directories,
                files,
            )
            != projection[generation]["plugin_subtree_sha256"]
        ):
            raise QualificationError(f"candidate {generation} plugin subtree disagrees")
        for component, relative in (
            ("controller", "controller/task_witness_deploy.py"),
            ("policy", "controller/policy.json"),
            ("client", "client/task_witness_client.py"),
        ):
            if (
                hashlib.sha256(
                    _candidate_bridge_source_bytes(raw_files, generation, relative)
                ).hexdigest()
                != projection[generation][f"{component}_sha256"]
            ):
                raise QualificationError(
                    f"candidate {generation} {component} identity disagrees"
                )
    changed = {
        relative
        for relative in expected_files
        if walked["freeze5"][1][relative] != walked["bridge"][1][relative]
    }
    if changed != {
        ".claude-plugin/plugin.json",
        ".codex-plugin/plugin.json",
        "client/task_witness_client.py",
        "controller/task_witness_deploy.py",
    }:
        raise QualificationError("candidate bridge four-path transition disagrees")
    if canonical_json_bytes(projection) != canonical_json_bytes(child_projection):
        raise QualificationError("candidate validator bridge history disagrees")
    return projection


def _git_blob(
    git_executable: Path,
    candidate_root: Path,
    oid: str,
) -> bytes:
    return run_recorded_git(git_executable, candidate_root, "cat-file", "blob", oid)


def _git_blob_length(
    git_executable: Path,
    candidate_root: Path,
    oid: str,
) -> int:
    raw = run_recorded_git(git_executable, candidate_root, "cat-file", "-s", oid)
    if (
        not raw.endswith(b"\n")
        or raw.count(b"\n") != 1
        or re.fullmatch(rb"[0-9]+", raw[:-1]) is None
        or len(raw) > 22
    ):
        raise QualificationError("candidate Git blob length is invalid")
    length = int(raw[:-1])
    if length > MAX_CANDIDATE_RETAINED_BYTES:
        raise QualificationError("candidate closure exceeds the retained byte limit")
    return length


def _candidate_administration(
    candidate_root: Path,
    git_executable: Path,
) -> dict[Path, StatBinding]:
    top_level = _git_text(
        git_executable, candidate_root, "rev-parse", "--show-toplevel"
    )
    if top_level != str(candidate_root):
        raise QualificationError("candidate Git top-level disagrees")
    object_format = _git_text(
        git_executable,
        candidate_root,
        "rev-parse",
        "--show-object-format",
    )
    if object_format != "sha1":
        raise QualificationError("candidate Git object format is unsupported")
    git_directory = Path(
        _git_text(
            git_executable,
            candidate_root,
            "rev-parse",
            "--absolute-git-dir",
        )
    )
    common_directory = Path(
        _git_text(
            git_executable,
            candidate_root,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        )
    )
    paths = {
        candidate_root,
        candidate_root / ".git",
        git_directory,
        common_directory,
        Path(
            _git_text(
                git_executable,
                candidate_root,
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                "HEAD",
            )
        ),
        Path(
            _git_text(
                git_executable,
                candidate_root,
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                "index",
            )
        ),
        Path(
            _git_text(
                git_executable,
                candidate_root,
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                "objects",
            )
        ),
    }
    recursive_roots = {git_directory}
    for relative in (
        "config",
        "config.worktree",
        "commondir",
        "info",
        "packed-refs",
        "refs",
        "objects/info",
        "objects/pack",
        "info/attributes",
    ):
        path = Path(
            _git_text(
                git_executable,
                candidate_root,
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                relative,
            )
        )
        if path.exists() or path.is_symlink():
            paths.add(path)
            if relative in {"info", "objects/info", "objects/pack", "refs"}:
                recursive_roots.add(path)
    symbolic_head = run_recorded_git(
        git_executable,
        candidate_root,
        "symbolic-ref",
        "-q",
        "HEAD",
        allowed_statuses=(0, 1),
    )
    if symbolic_head:
        try:
            head_ref = symbolic_head.decode("ascii")
        except UnicodeDecodeError as error:
            raise QualificationError(
                "candidate Git HEAD reference is invalid"
            ) from error
        if (
            not head_ref.endswith("\n")
            or head_ref.count("\n") != 1
            or not head_ref.startswith("refs/")
            or any(part in {"", ".", ".."} for part in head_ref[:-1].split("/"))
        ):
            raise QualificationError("candidate Git HEAD reference is invalid")
        resolved_ref = Path(
            _git_text(
                git_executable,
                candidate_root,
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                head_ref[:-1],
            )
        )
        if resolved_ref.exists() or resolved_ref.is_symlink():
            paths.add(resolved_ref)
    alternates = Path(
        _git_text(
            git_executable,
            candidate_root,
            "rev-parse",
            "--path-format=absolute",
            "--git-path",
            "objects/info/alternates",
        )
    )
    if alternates.exists() or alternates.is_symlink():
        raise QualificationError(
            "candidate Git alternate object storage is unsupported"
        )
    for root in recursive_roots:
        if not root.is_dir():
            continue
        pending = [root]
        observed = 0
        while pending:
            directory = pending.pop()
            try:
                children = list(directory.iterdir())
            except OSError as error:
                raise QualificationError(
                    "candidate Git administration is unavailable"
                ) from error
            for child in children:
                observed += 1
                if observed > MAX_RUNTIME_CLOSURE_ENTRIES:
                    raise QualificationError(
                        "candidate Git administration is too large"
                    )
                try:
                    metadata = child.lstat()
                except OSError as error:
                    raise QualificationError(
                        "candidate Git administration is unavailable"
                    ) from error
                if stat.S_ISLNK(metadata.st_mode) or not (
                    stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
                ):
                    raise QualificationError(
                        "candidate Git administration entry is unsupported"
                    )
                paths.add(child)
                if stat.S_ISDIR(metadata.st_mode):
                    pending.append(child)
    bindings: dict[Path, StatBinding] = {}
    for path in paths:
        try:
            bindings[path] = stable_stat_binding(path.lstat())
        except OSError as error:
            raise QualificationError(
                "candidate Git administration is unavailable"
            ) from error
    require_paths_immutable(paths, "candidate Git administration")
    return bindings


def _candidate_object_bindings(
    candidate_root: Path,
    git_executable: Path,
    object_ids: set[str],
) -> dict[Path, StatBinding]:
    paths: set[Path] = set()
    for oid in sorted(object_ids):
        raw = run_recorded_git(
            git_executable,
            candidate_root,
            "rev-parse",
            "--path-format=absolute",
            "--git-path",
            f"objects/{oid[:2]}/{oid[2:]}",
        )
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise QualificationError("candidate Git object path is invalid") from error
        if not value.endswith("\n") or value.count("\n") != 1:
            raise QualificationError("candidate Git object path is invalid")
        loose = Path(value[:-1])
        if loose.exists() or loose.is_symlink():
            paths.add(loose)
    objects = Path(
        _git_text(
            git_executable,
            candidate_root,
            "rev-parse",
            "--path-format=absolute",
            "--git-path",
            "objects",
        )
    )
    pack_directory = objects / "pack"
    if pack_directory.exists():
        try:
            paths.update(
                path
                for path in pack_directory.iterdir()
                if path.suffix in {".pack", ".idx", ".rev"}
            )
        except OSError as error:
            raise QualificationError(
                "candidate Git object storage is unavailable"
            ) from error
    bindings: dict[Path, StatBinding] = {}
    for path in paths:
        try:
            bindings[path] = stable_stat_binding(path.lstat())
        except OSError as error:
            raise QualificationError(
                "candidate Git object storage is unavailable"
            ) from error
    require_paths_immutable(paths, "candidate Git object storage")
    return bindings


def _require_candidate_administration_stable(
    bindings: dict[Path, StatBinding],
) -> None:
    require_paths_immutable(
        set(bindings),
        "candidate Git administration",
        expected_bindings=bindings,
    )


def _require_safe_candidate_config(
    candidate_root: Path,
    git_executable: Path,
) -> None:
    execution_config = run_recorded_git(
        git_executable,
        candidate_root,
        "config",
        "--includes",
        "--show-scope",
        "--show-origin",
        "--null",
        "--name-only",
        "--get-regexp",
        (
            r"^(alias|credential|diff|filter|gpg|http|include|interactive|merge|"
            r"pager|protocol|remote|ssh|submodule|url)\."
            r"|^core\.(attributesfile|editor|fsmonitor|gitproxy|hookspath|pager|"
            r"sshcommand)$|^extensions\.partialclone$"
        ),
        allowed_statuses=(0, 1),
    )
    tokens = execution_config.split(b"\0")
    values = tokens[:-1]
    if tokens[-1] != b"" or len(values) % 3 != 0:
        raise QualificationError("candidate Git configuration projection is invalid")
    allowed_command_keys = {
        b"core.attributesfile",
        b"core.fsmonitor",
        b"core.hookspath",
        b"protocol.allow",
        b"protocol.file.allow",
    }
    if any(
        scope != b"command" or key.lower() not in allowed_command_keys
        for scope, _origin, key in zip(
            values[::3],
            values[1::3],
            values[2::3],
            strict=True,
        )
    ):
        raise QualificationError(
            "candidate Git execution-driving configuration is unsupported"
        )
    flags = run_recorded_git(
        git_executable,
        candidate_root,
        "ls-files",
        "-v",
        "-z",
    )
    rows = flags.split(b"\0")
    if rows[-1] != b"" or any(not row.startswith(b"H ") for row in rows[:-1]):
        raise QualificationError("candidate Git index contains hidden state")
    try:
        tracked_paths = [row[2:].decode("utf-8") for row in rows[:-1]]
    except UnicodeDecodeError as error:
        raise QualificationError("candidate Git index path is invalid") from error
    if any(
        path == ".gitattributes" or path.endswith("/.gitattributes")
        for path in tracked_paths
    ):
        raise QualificationError("candidate Git attributes are unsupported")
    attribute_source = Path(
        _git_text(
            git_executable,
            candidate_root,
            "rev-parse",
            "--path-format=absolute",
            "--git-path",
            "info/attributes",
        )
    )
    if attribute_source.exists() or attribute_source.is_symlink():
        raise QualificationError("candidate Git attributes are unsupported")
    for key in ("core.sparseCheckout", "core.sparseCheckoutCone"):
        raw = run_recorded_git(
            git_executable,
            candidate_root,
            "config",
            "--bool",
            "--get",
            key,
            allowed_statuses=(0, 1),
        )
        if raw not in {b"", b"false\n"}:
            raise QualificationError("candidate Git sparse checkout is unsupported")


def _candidate_index_entries(
    candidate_root: Path,
    git_executable: Path,
) -> dict[str, tuple[str, str]]:
    raw = run_recorded_git(
        git_executable,
        candidate_root,
        "ls-files",
        "--stage",
        "-z",
    )
    rows = raw.split(b"\0")
    if rows[-1] != b"":
        raise QualificationError("candidate Git index projection is invalid")
    entries: dict[str, tuple[str, str]] = {}
    try:
        for row in rows[:-1]:
            left, raw_path = row.split(b"\t", 1)
            mode, oid, stage = left.decode("ascii").split(" ")
            path = raw_path.decode("utf-8")
            if (
                path in entries
                or stage != "0"
                or mode not in {"100644", "100755", "120000"}
                or re.fullmatch(r"[0-9a-f]{40}", oid) is None
                or not path
                or path.startswith("/")
                or any(part in {"", ".", ".."} for part in path.split("/"))
            ):
                raise ValueError
            entries[path] = (mode, oid)
    except (UnicodeDecodeError, ValueError) as error:
        raise QualificationError("candidate Git index projection is invalid") from error
    return entries


def _candidate_visible_blob_oid(
    path: Path,
    relative: str,
    metadata: os.stat_result,
    *,
    remaining_bytes: int,
) -> tuple[str, int]:
    if stat.S_ISLNK(metadata.st_mode):
        try:
            target = os.readlink(path).encode("utf-8")
            after = path.lstat()
        except (OSError, UnicodeEncodeError) as error:
            raise QualificationError(
                f"candidate visible symlink is unavailable: {relative}"
            ) from error
        if stable_stat_binding(after) != stable_stat_binding(metadata):
            raise QualificationError(f"candidate visible symlink changed: {relative}")
        raw = target
    else:
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
                raise QualificationError(
                    f"candidate visible file has a hard-link alias: {relative}"
                )
            raise QualificationError(
                f"candidate visible entry is unsupported: {relative}"
            )
        if metadata.st_size > remaining_bytes:
            raise QualificationError("candidate visible tree exceeds the byte limit")
        descriptor, opened = open_absolute_path_without_symlinks(
            path,
            f"candidate visible file {relative}",
            expected_kind="regular",
        )
        chunks: list[bytes] = []
        remaining = metadata.st_size
        try:
            if stable_stat_binding(opened) != stable_stat_binding(metadata):
                raise QualificationError(f"candidate visible file changed: {relative}")
            while remaining:
                chunk = os.read(descriptor, min(remaining, 1024 * 1024))
                if not chunk:
                    raise QualificationError(
                        f"candidate visible file is incomplete: {relative}"
                    )
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
            if stable_stat_binding(after) != stable_stat_binding(opened):
                raise QualificationError(f"candidate visible file changed: {relative}")
        except OSError as error:
            raise QualificationError(
                f"candidate visible file is unavailable: {relative}"
            ) from error
        finally:
            close_descriptor(descriptor, f"candidate visible file {relative}")
        raw = b"".join(chunks)
    if len(raw) > remaining_bytes:
        raise QualificationError("candidate visible tree exceeds the byte limit")
    return _bridge_object_sha1("blob", raw), len(raw)


def _require_clean_candidate(
    candidate_root: Path,
    git_executable: Path,
    tree: dict[str, tuple[str, str, str]],
) -> None:
    index = _candidate_index_entries(candidate_root, git_executable)
    expected_index = {
        path: (mode, oid) for path, (mode, kind, oid) in tree.items() if kind != "tree"
    }
    if index != expected_index:
        raise QualificationError("candidate Git index disagrees with HEAD")
    expected_directories = {
        path for path, (_mode, kind, _oid) in tree.items() if kind == "tree"
    }
    observed_directories: set[str] = set()
    observed_files: dict[str, tuple[str, str]] = {}
    visible_bindings: dict[Path, StatBinding] = {}
    pending = [(candidate_root, "", 0)]
    observed_count = 0
    retained_bytes = 0
    while pending:
        directory, prefix, depth = pending.pop()
        if depth > MAX_RUNTIME_DIRECTORY_DEPTH:
            raise QualificationError("candidate visible tree exceeds the depth limit")
        try:
            children = sorted(directory.iterdir(), key=lambda path: path.name)
        except OSError as error:
            raise QualificationError("candidate visible tree is unavailable") from error
        for child in children:
            relative = f"{prefix}/{child.name}" if prefix else child.name
            if relative == ".git":
                continue
            observed_count += 1
            if observed_count > MAX_RUNTIME_CLOSURE_ENTRIES:
                raise QualificationError(
                    "candidate visible tree exceeds the entry limit"
                )
            try:
                metadata = child.lstat()
            except OSError as error:
                raise QualificationError(
                    "candidate visible tree is unavailable"
                ) from error
            visible_bindings[child] = stable_stat_binding(metadata)
            if stat.S_ISDIR(metadata.st_mode):
                observed_directories.add(relative)
                pending.append((child, relative, depth + 1))
                continue
            oid, length = _candidate_visible_blob_oid(
                child,
                relative,
                metadata,
                remaining_bytes=MAX_RUNTIME_CLOSURE_BYTES - retained_bytes,
            )
            retained_bytes += length
            mode = (
                "120000"
                if stat.S_ISLNK(metadata.st_mode)
                else ("100755" if stat.S_IMODE(metadata.st_mode) == 0o755 else "100644")
            )
            if not stat.S_ISLNK(metadata.st_mode) and stat.S_IMODE(
                metadata.st_mode
            ) not in {0o644, 0o755}:
                raise QualificationError(
                    f"candidate visible file mode is unsupported: {relative}"
                )
            observed_files[relative] = (mode, oid)
    if observed_directories != expected_directories or observed_files != expected_index:
        raise QualificationError("candidate Git checkout is not clean")
    require_paths_immutable(
        set(visible_bindings),
        "candidate visible tree",
        expected_bindings=visible_bindings,
    )


def _candidate_file(
    candidate_root: Path,
    git_executable: Path,
    relative: str,
    tree_entry: tuple[str, str, str],
    *,
    expected_length: int,
) -> tuple[dict[str, Any], bytes]:
    mode, kind, oid = tree_entry
    if kind != "blob" or mode not in {"100644", "100755"}:
        raise QualificationError("candidate closure file mode is unsupported")
    raw = _git_blob(git_executable, candidate_root, oid)
    if len(raw) != expected_length:
        raise QualificationError("candidate Git blob length disagrees")
    framed = b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw
    if hashlib.sha1(framed).hexdigest() != oid:
        raise QualificationError("candidate Git blob identity disagrees")
    path = candidate_root / relative
    binding = _regular_binding(
        path,
        f"candidate closure file {relative}",
        expected_length=len(raw),
    )
    if binding["sha256"] != hashlib.sha256(raw).hexdigest() or stat.S_IMODE(
        binding["stat"]["mode"]
    ) != int(mode[-3:], 8):
        raise QualificationError("candidate closure visible bytes disagree")
    return binding, raw


def _candidate_core(
    candidate_root: Path,
    git_executable: Path,
) -> tuple[dict[str, Any], dict[str, bytes], dict[str, dict[str, Any]]]:
    _require_safe_candidate_config(candidate_root, git_executable)
    commit = _git_text(
        git_executable,
        candidate_root,
        "rev-parse",
        "--verify",
        "HEAD^{commit}",
    )
    tree_oid = _git_text(
        git_executable,
        candidate_root,
        "rev-parse",
        "--verify",
        "HEAD^{tree}",
    )
    if (
        re.fullmatch(r"[0-9a-f]{40}", commit) is None
        or re.fullmatch(r"[0-9a-f]{40}", tree_oid) is None
    ):
        raise QualificationError("candidate Git identity is invalid")
    tree = _git_tree_entries(git_executable, candidate_root, commit)
    _require_clean_candidate(candidate_root, git_executable, tree)
    roots = tuple(CANDIDATE_CLOSURE_ROOTS)
    if len(set(roots)) != 18 or any(
        left != right and (left.startswith(f"{right}/") or right.startswith(f"{left}/"))
        for left in roots
        for right in roots
    ):
        raise QualificationError("candidate closure root inventory is invalid")
    selected: set[str] = set()
    for root in roots:
        entry = tree.get(root)
        if entry is None:
            raise QualificationError(f"candidate closure root is missing: {root}")
        expected_kind = "tree" if root in CANDIDATE_DIRECTORY_ROOTS else "blob"
        if entry[1] != expected_kind:
            raise QualificationError(f"candidate closure root kind disagrees: {root}")
        selected.add(root)
        if entry[1] == "tree":
            selected.update(path for path in tree if path.startswith(f"{root}/"))
    closure_entries: list[dict[str, Any]] = []
    raw_files: dict[str, bytes] = {}
    file_bindings: dict[str, dict[str, Any]] = {}
    retained_total = 0
    visible_bindings: dict[Path, StatBinding] = {}
    required_raw = {
        CANDIDATE_REGISTRATION,
        CANDIDATE_VALIDATOR,
        CANDIDATE_AGENT_STANDARD,
        "release/task-witness/source-shape-review.json",
        "release/task-witness/tw4-bridge-identity.json",
        "release/task-witness/tw4-bridge-provenance.json",
        "release/task-witness/tw4-suite-inventory.json",
    }
    for relative in sorted(selected):
        mode, kind, _oid = tree[relative]
        path = candidate_root / relative
        if kind == "tree":
            binding = _directory_binding(
                path,
                f"candidate closure directory {relative}",
            )
            visible_bindings[path] = _projected_stat_binding(binding["stat"])
            closure_entries.append({"kind": "directory", "path": relative})
            continue
        blob_length = _git_blob_length(git_executable, candidate_root, _oid)
        if blob_length > MAX_CANDIDATE_RETAINED_BYTES - retained_total:
            raise QualificationError(
                "candidate closure exceeds the retained byte limit"
            )
        retained_total += blob_length
        binding, raw = _candidate_file(
            candidate_root,
            git_executable,
            relative,
            tree[relative],
            expected_length=blob_length,
        )
        if relative in required_raw or relative.startswith(
            ("release/task-witness/migration/", f"{CANDIDATE_PLUGIN_ROOT}/")
        ):
            raw_files[relative] = raw
        file_bindings[relative] = binding
        visible_bindings[path] = _projected_stat_binding(binding["stat"])
        closure_entries.append(
            {
                "kind": "file",
                "length": len(raw),
                "mode": mode,
                "path": relative,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    require_paths_immutable(
        set(visible_bindings),
        "candidate closure visible tree",
        expected_bindings=visible_bindings,
    )
    registration = _strict_json_object(
        _candidate_required_raw(
            raw_files,
            CANDIDATE_REGISTRATION,
            "candidate public-release registration",
        ),
        "candidate public-release registration",
    )
    if registration != {
        "production_eligible": False,
        "schema_version": 1,
        "source_stage_validator_flags": ["--source-stage"],
        "support_paths": list(CANDIDATE_SUPPORT_PATHS),
    }:
        raise QualificationError("candidate public-release registration disagrees")
    inventory_raw = _candidate_required_raw(
        raw_files,
        "release/task-witness/tw4-suite-inventory.json",
        "candidate suite inventory",
    )
    inventory = parse_suite_inventory(
        decode_canonical_json(
            inventory_raw,
            maximum=MAX_JSON_BYTES,
            expected_root=dict,
            label="candidate suite inventory",
        )
    )
    plugin_entries: list[dict[str, Any]] = []
    plugin_prefix = f"{CANDIDATE_PLUGIN_ROOT}/"
    for entry in closure_entries:
        relative = str(entry["path"])
        if not relative.startswith(plugin_prefix):
            continue
        projected = {key: value for key, value in entry.items() if key != "mode"}
        projected["path"] = relative.removeprefix(plugin_prefix)
        plugin_entries.append(projected)
    plugin_projection = {
        "contract": "task-witness-plugin-subtree-v1",
        "entries": plugin_entries,
    }
    closure_projection = {
        "contract": "task-witness-qualification-candidate-closure-v1",
        "entries": closure_entries,
    }
    qualification_candidate = {
        "repository_id": CANDIDATE_REPOSITORY_ID,
        "commit_sha1": commit,
        "tree_sha1": tree_oid,
        "plugin_subtree_sha256": hashlib.sha256(
            canonical_json_bytes(plugin_projection)
        ).hexdigest(),
        "suite_inventory_sha256": hashlib.sha256(inventory_raw).hexdigest(),
    }
    candidate_closure = {
        "contract": "task-witness-qualification-candidate-closure-v1",
        "entry_count": len(closure_entries),
        "projection_sha256": hashlib.sha256(
            canonical_json_bytes(closure_projection)
        ).hexdigest(),
        "source_shape_sha256": hashlib.sha256(
            _candidate_required_raw(
                raw_files,
                "release/task-witness/source-shape-review.json",
                "candidate source-shape review",
            )
        ).hexdigest(),
    }
    candidate = {
        "contract": "task-witness-tw4-candidate-observation-v1",
        "root_path": str(candidate_root),
        "root": _directory_binding(candidate_root, "candidate root"),
        "qualification_candidate": qualification_candidate,
        "candidate_closure": candidate_closure,
        "worktree": {"tracked": "clean", "untracked": "none"},
    }
    aggregates = inventory["aggregates"]
    suite_summary = {
        "path": "release/task-witness/tw4-suite-inventory.json",
        "length": len(inventory_raw),
        "sha256": hashlib.sha256(inventory_raw).hexdigest(),
        "counts_sha256": aggregates["counts_sha256"],
        "entries_sha256": aggregates["entries_sha256"],
        "entry_count": aggregates["entry_count"],
        "expected_count_total": aggregates["expected_count_total"],
    }
    object_ids = {commit, tree_oid}
    object_ids.update(oid for _mode, _kind, oid in tree.values())
    object_bindings = _candidate_object_bindings(
        candidate_root,
        git_executable,
        object_ids,
    )
    core = {"candidate": candidate, "suite_inventory": suite_summary}
    core["_object_bindings"] = {
        str(path): list(binding)
        for path, binding in sorted(
            object_bindings.items(),
            key=lambda item: str(item[0]),
        )
    }
    core["_visible_bindings"] = {
        str(path): list(binding)
        for path, binding in sorted(
            visible_bindings.items(),
            key=lambda item: str(item[0]),
        )
    }
    return core, raw_files, file_bindings


def _require_candidate_object_bindings_stable(core: dict[str, Any]) -> None:
    value = core.get("_object_bindings")
    if not isinstance(value, dict):
        raise QualificationError("candidate Git object binding inventory is invalid")
    expected: dict[Path, StatBinding] = {}
    for raw_path, raw_binding in value.items():
        if (
            not isinstance(raw_path, str)
            or not isinstance(raw_binding, list)
            or len(raw_binding) != 9
            or any(type(item) is not int for item in raw_binding)
        ):
            raise QualificationError(
                "candidate Git object binding inventory is invalid"
            )
        expected[Path(raw_path)] = tuple(raw_binding)  # type: ignore[assignment]
    require_paths_immutable(
        set(expected),
        "candidate Git object storage",
        expected_bindings=expected,
    )


def _require_candidate_visible_bindings_stable(core: dict[str, Any]) -> None:
    value = core.get("_visible_bindings")
    if not isinstance(value, dict):
        raise QualificationError("candidate visible binding inventory is invalid")
    expected: dict[Path, StatBinding] = {}
    for raw_path, raw_binding in value.items():
        if (
            not isinstance(raw_path, str)
            or not isinstance(raw_binding, list)
            or len(raw_binding) != 9
            or any(type(item) is not int for item in raw_binding)
        ):
            raise QualificationError("candidate visible binding inventory is invalid")
        expected[Path(raw_path)] = tuple(raw_binding)  # type: ignore[assignment]
    require_paths_immutable(
        set(expected),
        "candidate closure visible tree",
        expected_bindings=expected,
    )


def _capture_candidate_source(
    path: Path,
    label: str,
    tree_bytes: bytes,
    expected_binding: dict[str, Any],
) -> tuple[int, StatBinding]:
    descriptor, opened = open_absolute_path_without_symlinks(
        path,
        label,
        expected_kind="regular",
    )
    try:
        if (
            opened.st_nlink != 1
            or _live_stat(opened) != expected_binding["stat"]
            or opened.st_size != len(tree_bytes)
        ):
            raise QualificationError(f"{label} binding changed")
        chunks: list[bytes] = []
        remaining = len(tree_bytes) + 1
        while remaining:
            try:
                chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            except OSError as error:
                raise QualificationError(f"{label} cannot be captured") from error
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        captured = b"".join(chunks)
        try:
            after = os.fstat(descriptor)
        except OSError as error:
            raise QualificationError(f"{label} cannot be captured") from error
        if captured != tree_bytes or stable_stat_binding(after) != stable_stat_binding(
            opened
        ):
            raise QualificationError(f"{label} binding changed")
        return descriptor, stable_stat_binding(after)
    except BaseException:
        close_descriptor(descriptor, label)
        raise


def _recheck_captured_candidate_source(
    descriptor: int,
    captured_binding: StatBinding,
    path: Path,
    label: str,
    expected_binding: dict[str, Any],
) -> None:
    try:
        current = os.fstat(descriptor)
        visible = _regular_binding(
            path,
            label,
            expected_length=expected_binding["length"],
        )
    except (OSError, QualificationError) as error:
        raise QualificationError(f"{label} binding changed") from error
    if stable_stat_binding(current) != captured_binding or visible != expected_binding:
        raise QualificationError(f"{label} binding changed")


def run_captured_candidate_validator(
    candidate_root: Path,
    runtime_executable: Path,
    raw_files: dict[str, bytes],
    file_bindings: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Execute descriptor-captured candidate code in a bounded isolated child."""

    sources = (
        (CANDIDATE_AGENT_STANDARD, "candidate Agent Plugins standard"),
        (CANDIDATE_VALIDATOR, "candidate validator"),
    )
    captured: dict[str, tuple[int, StatBinding]] = {}
    try:
        for relative, label in sources:
            raw = raw_files[relative]
            if len(raw) > MAX_VALIDATOR_SOURCE_BYTES:
                raise QualificationError(f"{label} exceeds the byte limit")
            captured[relative] = _capture_candidate_source(
                candidate_root / relative,
                label,
                raw,
                file_bindings[relative],
            )
    except BaseException:
        for relative, (descriptor, _binding) in captured.items():
            close_descriptor(descriptor, dict(sources)[relative])
        raise

    payload = canonical_json_bytes(
        {
            "agent_standard": base64.b64encode(
                raw_files[CANDIDATE_AGENT_STANDARD]
            ).decode("ascii"),
            "candidate_root": str(candidate_root),
            "validator": base64.b64encode(raw_files[CANDIDATE_VALIDATOR]).decode(
                "ascii"
            ),
        },
        maximum=(3 * MAX_VALIDATOR_SOURCE_BYTES),
        label="candidate validator payload",
    )
    execution_error: QualificationError | None = None
    status = -1
    stdout = b""
    stderr = b""
    try:
        status, stdout, stderr = _bounded_process(
            [
                str(runtime_executable),
                "-I",
                "-B",
                "-c",
                CANDIDATE_VALIDATOR_BOOTSTRAP,
            ],
            env={
                "HOME": "/nonexistent-task-witness-validator-home",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/bin:/bin",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "0",
                "PYTHONNOUSERSITE": "1",
            },
            stdout_maximum=MAX_BRIDGE_HISTORY_BYTES,
            stderr_maximum=MAX_PROCESS_STDERR_BYTES,
            label="candidate validator child",
            stdin=payload,
            timeout_seconds=CANDIDATE_VALIDATOR_TIMEOUT_SECONDS,
            own_process_group=True,
        )
    except QualificationError as error:
        execution_error = error
    binding_error: QualificationError | None = None
    for relative, label in sources:
        descriptor, captured_binding = captured[relative]
        try:
            _recheck_captured_candidate_source(
                descriptor,
                captured_binding,
                candidate_root / relative,
                label,
                file_bindings[relative],
            )
        except QualificationError as error:
            if binding_error is None:
                binding_error = error
        finally:
            close_descriptor(descriptor, label)
    if binding_error is not None:
        if execution_error is not None:
            raise binding_error from execution_error
        raise binding_error
    if execution_error is not None:
        raise execution_error
    if status != 0 or stderr:
        raise QualificationError("candidate validator rejected the checkout")
    return parse_bridge_history_projection(
        decode_canonical_json(
            stdout,
            maximum=MAX_BRIDGE_HISTORY_BYTES,
            expected_root=dict,
            label="candidate validator bridge history",
        )
    )


def observe_candidate(
    candidate_root: Path,
    git_executable: Path,
    *,
    runtime_executable: Path,
) -> dict[str, dict[str, Any]]:
    """Derive one receipt-shaped observation from an immutable Git candidate."""

    require_directory(candidate_root, "candidate root")
    require_file(git_executable, "recorded Git executable")
    if not effective_access(git_executable, os.X_OK):
        raise QualificationError("recorded Git executable is not executable")
    administration = _candidate_administration(candidate_root, git_executable)
    first, first_raw, first_bindings = _candidate_core(
        candidate_root,
        git_executable,
    )
    child_bridge_history = run_captured_candidate_validator(
        candidate_root,
        runtime_executable,
        first_raw,
        first_bindings,
    )
    bridge_history = validate_bridge_history_evidence(
        first_raw,
        child_bridge_history,
    )
    _require_candidate_administration_stable(administration)
    _require_candidate_object_bindings_stable(first)
    _require_candidate_visible_bindings_stable(first)
    second, second_raw, second_bindings = _candidate_core(
        candidate_root,
        git_executable,
    )
    _require_candidate_administration_stable(administration)
    _require_candidate_object_bindings_stable(second)
    _require_candidate_visible_bindings_stable(second)
    if (
        canonical_json_bytes(first) != canonical_json_bytes(second)
        or first_raw != second_raw
        or canonical_json_bytes(first_bindings) != canonical_json_bytes(second_bindings)
    ):
        raise QualificationError("candidate observation changed")

    candidate = first["candidate"]
    suite_summary = first["suite_inventory"]
    canonical_json_bytes(
        candidate["qualification_candidate"],
        maximum=MAX_CANDIDATE_MEMBER_BYTES,
        label="qualification candidate",
    )
    canonical_json_bytes(
        candidate["candidate_closure"],
        maximum=MAX_CANDIDATE_MEMBER_BYTES,
        label="candidate closure",
    )
    canonical_json_bytes(
        suite_summary,
        maximum=MAX_CANDIDATE_MEMBER_BYTES,
        label="candidate suite inventory summary",
    )
    bridge_observation = {
        "contract": "task-witness-tw4-bridge-history-observation-v1",
        "bridge_history": bridge_history,
        "identity_file": first_bindings[
            "release/task-witness/tw4-bridge-identity.json"
        ],
        "provenance_file": first_bindings[
            "release/task-witness/tw4-bridge-provenance.json"
        ],
    }
    suite_observation = {
        "contract": "task-witness-tw4-suite-inventory-observation-v1",
        "file": first_bindings["release/task-witness/tw4-suite-inventory.json"],
        "suite_inventory": suite_summary,
    }
    observations = {
        "candidate": candidate,
        "bridge_history": bridge_observation,
        "suite_inventory": suite_observation,
    }
    for label, value in observations.items():
        canonical_json_bytes(
            value,
            maximum=MAX_CANDIDATE_OBSERVATION_BYTES,
            label=f"candidate {label} observation",
        )
    return observations


def validate_passwd_user() -> None:
    euid = os.geteuid()
    if euid == 0:
        raise QualificationError("qualification must run as an unprivileged user")
    try:
        entry = pwd.getpwuid(euid)
    except KeyError as error:
        raise QualificationError("effective user is not passwd-backed") from error
    home = Path(entry.pw_dir)
    if not entry.pw_name or entry.pw_uid != euid or not home.is_absolute():
        raise QualificationError("passwd-backed user identity is invalid")
    _home, home_metadata = require_directory_observation(home, "passwd-backed home")
    if home_metadata.st_uid != euid:
        raise QualificationError("passwd-backed home is invalid")


def qualification_profile_home(profile: dict[str, Any]) -> Path:
    validate_passwd_user()
    try:
        entry = pwd.getpwuid(os.geteuid())
    except KeyError as error:
        raise QualificationError("effective user is not passwd-backed") from error
    recorded = profile["passwd_user"]
    if (
        recorded["name"] != entry.pw_name
        or recorded["uid"] != entry.pw_uid
        or recorded["primary_gid"] != entry.pw_gid
        or recorded["home"] != entry.pw_dir
    ):
        raise QualificationError("platform passwd identity disagrees")
    return Path(entry.pw_dir)


def reject_receipt_output_overlap(
    receipt_output: Path,
    candidate_root: Path,
    runtime_evidence: dict[str, Any],
) -> None:
    protected_roots = [candidate_root]
    protected_roots.extend(
        Path(root["path"]) for root in runtime_evidence["closure"]["roots"]
    )
    if any(receipt_output.is_relative_to(root) for root in protected_roots):
        raise QualificationError(
            "receipt output overlaps the candidate or runtime closure"
        )


def reject_non_native_profile(profile: dict[str, Any]) -> None:
    environment = profile.get("execution_environment")
    if environment in NON_NATIVE_ENVIRONMENTS:
        raise QualificationError("platform profile does not describe a native host")


def validate_receipt_output(path: Path) -> Path:
    _safe_child_name(path.name, "receipt output")
    parent_descriptor, _metadata = open_absolute_path_without_symlinks(
        path.parent,
        "receipt output parent",
        expected_kind="directory",
    )
    try:
        if _entry_exists_at(parent_descriptor, path.name, "receipt output"):
            raise QualificationError("receipt output already exists")
    finally:
        close_descriptor(parent_descriptor, "receipt output parent")
    return path


def _safe_child_name(name: str, label: str) -> None:
    if not name or name in {".", ".."} or "/" in name or "\0" in name:
        raise QualificationError(f"{label} name is invalid")


def _entry_exists_at(directory_descriptor: int, name: str, label: str) -> bool:
    _safe_child_name(name, label)
    try:
        os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as error:
        raise QualificationError(f"{label} cannot be inspected") from error
    return True


def publish_receipt_create_new(path: Path, raw: bytes) -> None:
    if not isinstance(raw, bytes) or not raw or len(raw) > HOST_RECEIPT_MAX_BYTES:
        raise QualificationError("host qualification receipt bytes are invalid")
    _safe_child_name(path.name, "receipt output")
    parent_descriptor, _parent_metadata = open_absolute_path_without_symlinks(
        path.parent,
        "receipt output parent",
        expected_kind="directory",
    )
    receipt_descriptor = -1
    verification_descriptor = -1
    temporary_name = ""
    temporary_exists = False
    primary_error: QualificationError | None = None
    try:
        if _entry_exists_at(parent_descriptor, path.name, "receipt output"):
            raise QualificationError("receipt output already exists")
        flags = os.O_RDWR | os.O_CLOEXEC | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        for _attempt in range(128):
            temporary_name = f".task-witness-receipt-{secrets.token_hex(16)}"
            try:
                receipt_descriptor = os.open(
                    temporary_name,
                    flags,
                    0o600,
                    dir_fd=parent_descriptor,
                )
            except FileExistsError:
                continue
            except OSError as error:
                raise QualificationError("receipt output cannot be created") from error
            temporary_exists = True
            break
        if receipt_descriptor < 0:
            raise QualificationError("receipt output cannot be created")
        try:
            os.fchmod(receipt_descriptor, 0o600)
            offset = 0
            while offset < len(raw):
                written = os.write(receipt_descriptor, raw[offset:])
                if written <= 0:
                    raise OSError("short receipt write")
                offset += written
            os.fsync(receipt_descriptor)
            os.lseek(receipt_descriptor, 0, os.SEEK_SET)
            chunks: list[bytes] = []
            remaining = len(raw) + 1
            while remaining:
                chunk = os.read(receipt_descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            written_raw = b"".join(chunks)
            metadata = os.fstat(receipt_descriptor)
            visible_temporary = os.stat(
                temporary_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except OSError as error:
            raise QualificationError("receipt output cannot be written") from error
        if (
            written_raw != raw
            or stable_stat_binding(metadata)
            != stable_stat_binding(visible_temporary)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or metadata.st_size != len(raw)
        ):
            raise QualificationError("receipt output publication binding disagrees")
        try:
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError as error:
            raise QualificationError("receipt output already exists") from error
        except OSError as error:
            raise QualificationError("receipt output cannot be published") from error
        try:
            os.unlink(temporary_name, dir_fd=parent_descriptor)
            temporary_exists = False
            os.fsync(parent_descriptor)
        except OSError as error:
            raise QualificationError("receipt output cannot be published") from error
        verification_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
        verification_flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            verification_descriptor = os.open(
                path.name,
                verification_flags,
                dir_fd=parent_descriptor,
            )
            opened = os.fstat(verification_descriptor)
            chunks = []
            remaining = len(raw) + 1
            while remaining:
                chunk = os.read(verification_descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            verified_raw = b"".join(chunks)
            after = os.fstat(verification_descriptor)
            visible = os.stat(
                path.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except OSError as error:
            raise QualificationError("receipt output cannot be verified") from error
        if (
            verified_raw != raw
            or stable_stat_binding(opened) != stable_stat_binding(visible)
            or stable_stat_binding(opened) != stable_stat_binding(after)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
        ):
            raise QualificationError("receipt output publication binding disagrees")
    except QualificationError as error:
        primary_error = error
    finally:
        if temporary_exists and temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except OSError as cleanup_error:
                if primary_error is None:
                    primary_error = QualificationError(
                        "receipt output temporary cleanup failed"
                    )
                    primary_error.__cause__ = cleanup_error
        for descriptor, label in (
            (verification_descriptor, "receipt output verification"),
            (receipt_descriptor, "receipt output temporary file"),
            (parent_descriptor, "receipt output parent"),
        ):
            if descriptor >= 0:
                try:
                    close_descriptor(descriptor, label)
                except QualificationError as cleanup_error:
                    if primary_error is None:
                        primary_error = cleanup_error
    if primary_error is not None:
        raise primary_error


def _open_private_child_directory_at(
    parent_descriptor: int,
    name: str,
    label: str,
    *,
    create: bool,
) -> tuple[int, StatBinding]:
    _safe_child_name(name, label)
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_descriptor)
        except OSError as error:
            raise QualificationError(f"{label} cannot be created") from error
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
        opened = os.fstat(descriptor)
        visible = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError as error:
        if descriptor >= 0:
            close_descriptor(descriptor, label)
        raise QualificationError(f"{label} cannot be opened") from error
    if (
        not stat.S_ISDIR(opened.st_mode)
        or stat.S_ISLNK(visible.st_mode)
        or stable_stat_binding(opened) != stable_stat_binding(visible)
        or opened.st_uid != os.geteuid()
        or stat.S_IMODE(opened.st_mode) != 0o700
    ):
        close_descriptor(descriptor, label)
        raise QualificationError(f"{label} is unsafe")
    return descriptor, stable_stat_binding(opened)


def _remove_pinned_directory_at(
    parent_descriptor: int,
    name: str,
    descriptor: int,
    binding: StatBinding,
    label: str,
) -> None:
    try:
        opened = os.fstat(descriptor)
        visible = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError as error:
        raise QualificationError(f"{label} identity changed") from error
    if (
        stable_stat_binding(opened)[:5] != binding[:5]
        or stable_stat_binding(visible)[:5] != binding[:5]
    ):
        raise QualificationError(f"{label} identity changed")
    try:
        os.rmdir(name, dir_fd=parent_descriptor)
    except OSError as error:
        raise QualificationError(f"{label} cannot be removed") from error


def _unlink_optional_owned_regular_at(
    directory_descriptor: int,
    name: str,
    label: str,
    *,
    expected_mode: int | None = None,
) -> None:
    if not _entry_exists_at(directory_descriptor, name, label):
        return
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
        opened = os.fstat(descriptor)
        visible = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or stable_stat_binding(opened) != stable_stat_binding(visible)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or (
                expected_mode is not None
                and stat.S_IMODE(opened.st_mode) != expected_mode
            )
        ):
            raise QualificationError(f"{label} is unsafe")
        os.unlink(name, dir_fd=directory_descriptor)
        if os.fstat(descriptor).st_nlink != 0:
            raise QualificationError(f"{label} identity changed")
    except OSError as error:
        raise QualificationError(f"{label} cannot be removed") from error
    finally:
        if descriptor >= 0:
            close_descriptor(descriptor, label)


def _capture_regular_at(
    directory_descriptor: int,
    name: str,
    path: Path,
    label: str,
    *,
    maximum: int,
    expected_mode: int,
) -> tuple[bytes, dict[str, Any]]:
    _safe_child_name(name, label)
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != expected_mode
            or opened.st_size > maximum
        ):
            raise QualificationError(f"{label} is unsafe")
        chunks: list[bytes] = []
        remaining = opened.st_size + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        visible = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except QualificationError:
        raise
    except OSError as error:
        raise QualificationError(f"{label} cannot be captured") from error
    finally:
        if descriptor >= 0:
            close_descriptor(descriptor, label)
    if (
        stable_stat_binding(opened) != stable_stat_binding(after)
        or stable_stat_binding(opened) != stable_stat_binding(visible)
        or len(raw) != opened.st_size
    ):
        raise QualificationError(f"{label} identity changed")
    return raw, {
        "path": str(path),
        "length": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "uid": opened.st_uid,
        "gid": opened.st_gid,
        "mode": stat.S_IMODE(opened.st_mode),
        "nlink": opened.st_nlink,
    }


def _capture_absolute_regular_bytes(
    path: Path,
    label: str,
    *,
    maximum: int,
) -> tuple[bytes, os.stat_result]:
    descriptor, opened = open_absolute_path_without_symlinks(
        path,
        label,
        expected_kind="regular",
    )
    try:
        if opened.st_nlink != 1 or opened.st_size > maximum:
            raise QualificationError(f"{label} is unsafe")
        chunks: list[bytes] = []
        remaining = opened.st_size + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
    except QualificationError:
        raise
    except OSError as error:
        raise QualificationError(f"{label} cannot be captured") from error
    finally:
        close_descriptor(descriptor, label)
    current_descriptor, current = open_absolute_path_without_symlinks(
        path,
        label,
        expected_kind="regular",
    )
    close_descriptor(current_descriptor, label)
    if (
        len(raw) != opened.st_size
        or stable_stat_binding(opened) != stable_stat_binding(after)
        or stable_stat_binding(opened) != stable_stat_binding(current)
    ):
        raise QualificationError(f"{label} identity changed")
    return raw, opened


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _render_shim(template: bytes, runtime: Path, client: Path) -> bytes:
    try:
        text_value = template.decode("utf-8")
    except UnicodeDecodeError as error:
        raise QualificationError("literal-rendered-shim template is invalid") from error
    if (
        text_value.count("@TASK_WITNESS_PYTHON@") != 1
        or text_value.count("@TASK_WITNESS_CLIENT@") != 1
        or not text_value.endswith("\n")
    ):
        raise QualificationError("literal-rendered-shim template is invalid")
    return (
        text_value.replace("@TASK_WITNESS_PYTHON@", _shell_quote(str(runtime)))
        .replace("@TASK_WITNESS_CLIENT@", _shell_quote(str(client)))
        .encode("utf-8")
    )


def bind_literal_rendered_shim(
    candidate_root: Path,
    runtime_executable: Path,
    profile: dict[str, Any],
    resources: dict[str, Any],
    suite_record: dict[str, Any],
) -> dict[str, Any]:
    template_relative = Path("plugins/task-witness/client/task_witness_shim.sh.in")
    client_relative = Path("plugins/task-witness/client/task_witness_client.py")
    template, _template_metadata = _capture_absolute_regular_bytes(
        candidate_root / template_relative,
        "literal-rendered-shim template",
        maximum=DETAIL_STREAM_MAX_BYTES,
    )
    candidate_client, _client_metadata = _capture_absolute_regular_bytes(
        candidate_root / client_relative,
        "literal-rendered-shim candidate client",
        maximum=DETAIL_STREAM_MAX_BYTES,
    )
    if not candidate_client:
        raise QualificationError("literal-rendered-shim candidate client is empty")
    root_descriptor, _root_binding = _open_private_child_directory_at(
        resources["libexec_descriptor"],
        "task-witness",
        "literal-rendered-shim install root",
        create=False,
    )
    client_descriptor = -1
    try:
        client_descriptor, _client_binding = _open_private_child_directory_at(
            root_descriptor,
            "client",
            "literal-rendered-shim client directory",
            create=False,
        )
        home = Path(profile["passwd_user"]["home"])
        installed_client = (
            home / ".local/libexec/task-witness/client/task_witness_client.py"
        )
        installed_shim = home / ".local/libexec/task-witness/task-witness"
        client_raw, client_observation = _capture_regular_at(
            client_descriptor,
            installed_client.name,
            installed_client,
            "literal-rendered-shim installed client",
            maximum=DETAIL_STREAM_MAX_BYTES,
            expected_mode=0o500,
        )
        shim_raw, shim_observation = _capture_regular_at(
            root_descriptor,
            installed_shim.name,
            installed_shim,
            "literal-rendered-shim installed shim",
            maximum=DETAIL_STREAM_MAX_BYTES,
            expected_mode=0o500,
        )
    finally:
        if client_descriptor >= 0:
            close_descriptor(client_descriptor, "literal-rendered-shim client directory")
        close_descriptor(root_descriptor, "literal-rendered-shim install root")
    expected_uid = profile["passwd_user"]["uid"]
    expected_gid = profile["passwd_user"]["primary_gid"]
    if (
        client_raw != candidate_client
        or client_observation["uid"] != expected_uid
        or client_observation["gid"] != expected_gid
        or shim_observation["uid"] != expected_uid
        or shim_observation["gid"] != expected_gid
        or shim_raw != _render_shim(template, runtime_executable, installed_client)
    ):
        raise QualificationError("literal-rendered-shim live binding disagrees")
    rendered = {
        "contract": "task-witness-rendered-shim-observation-v1",
        "template": {
            "path": template_relative.as_posix(),
            "length": len(template),
            "sha256": hashlib.sha256(template).hexdigest(),
        },
        "runtime_executable_path": str(runtime_executable),
        "client": client_observation,
        "shim": shim_observation,
    }
    rendered_raw = canonical_json_bytes(
        rendered,
        maximum=RENDERED_SHIM_MAX_BYTES,
        label="literal-rendered-shim observation",
    )
    sidecar_raw, _sidecar_observation = _capture_regular_at(
        resources["workspace_descriptor"],
        "literal-rendered-shim-observation.json",
        resources["workspace_path"] / "literal-rendered-shim-observation.json",
        "literal-rendered-shim sidecar",
        maximum=RENDERED_SHIM_MAX_BYTES,
        expected_mode=0o600,
    )
    sidecar = decode_canonical_json(
        sidecar_raw,
        maximum=RENDERED_SHIM_MAX_BYTES,
        expected_root=dict,
        label="literal-rendered-shim sidecar",
    )
    result = suite_record.get("result")
    if (
        sidecar != rendered
        or sidecar_raw != rendered_raw
        or not isinstance(result, dict)
        or result.get("id") != "literal-rendered-shim"
        or result.get("detail_stdout_length") != len(sidecar_raw)
        or result.get("detail_stdout_sha256")
        != hashlib.sha256(sidecar_raw).hexdigest()
    ):
        raise QualificationError("literal-rendered-shim observation binding disagrees")
    return rendered


def prepare_qualification_workspace(home: Path) -> dict[str, Any]:
    home_descriptor, home_metadata = open_absolute_path_without_symlinks(
        home,
        "qualification home",
        expected_kind="directory",
    )
    if (
        home_metadata.st_uid != os.geteuid()
        or stat.S_IMODE(home_metadata.st_mode) != 0o700
    ):
        close_descriptor(home_descriptor, "qualification home")
        raise QualificationError("qualification home is unsafe")
    local_descriptor = -1
    libexec_descriptor = -1
    workspace_descriptor = -1
    created_local = False
    created_libexec = False
    workspace_name = ""
    try:
        created_local = not _entry_exists_at(
            home_descriptor, ".local", "qualification local directory"
        )
        local_descriptor, local_binding = _open_private_child_directory_at(
            home_descriptor,
            ".local",
            "qualification local directory",
            create=created_local,
        )
        created_libexec = not _entry_exists_at(
            local_descriptor, "libexec", "qualification libexec directory"
        )
        libexec_descriptor, libexec_binding = _open_private_child_directory_at(
            local_descriptor,
            "libexec",
            "qualification libexec directory",
            create=created_libexec,
        )
        if _entry_exists_at(
            libexec_descriptor,
            "task-witness",
            "literal-rendered-shim install root",
        ):
            raise QualificationError("literal-rendered-shim install root already exists")
        for _attempt in range(128):
            candidate = f".task-witness-qualification-{secrets.token_hex(16)}"
            try:
                os.mkdir(candidate, 0o700, dir_fd=home_descriptor)
            except FileExistsError:
                continue
            except OSError as error:
                raise QualificationError(
                    "qualification workspace cannot be created"
                ) from error
            workspace_name = candidate
            break
        if not workspace_name:
            raise QualificationError("qualification workspace cannot be created")
        workspace_descriptor, workspace_binding = _open_private_child_directory_at(
            home_descriptor,
            workspace_name,
            "qualification workspace",
            create=False,
        )
        return {
            "home_path": home,
            "home_descriptor": home_descriptor,
            "home_binding": stable_stat_binding(home_metadata),
            "local_descriptor": local_descriptor,
            "local_binding": local_binding,
            "libexec_descriptor": libexec_descriptor,
            "libexec_binding": libexec_binding,
            "workspace_descriptor": workspace_descriptor,
            "workspace_binding": workspace_binding,
            "workspace_name": workspace_name,
            "workspace_path": home / workspace_name,
            "created_local": created_local,
            "created_libexec": created_libexec,
            "closed": False,
        }
    except BaseException:
        if workspace_descriptor >= 0:
            close_descriptor(workspace_descriptor, "qualification workspace")
        if workspace_name:
            try:
                os.rmdir(workspace_name, dir_fd=home_descriptor)
            except OSError:
                pass
        if libexec_descriptor >= 0:
            close_descriptor(libexec_descriptor, "qualification libexec directory")
        if created_libexec and local_descriptor >= 0:
            try:
                os.rmdir("libexec", dir_fd=local_descriptor)
            except OSError:
                pass
        if local_descriptor >= 0:
            close_descriptor(local_descriptor, "qualification local directory")
        if created_local:
            try:
                os.rmdir(".local", dir_fd=home_descriptor)
            except OSError:
                pass
        close_descriptor(home_descriptor, "qualification home")
        raise


def _cleanup_literal_rendered_shim_install(resources: dict[str, Any]) -> None:
    libexec_descriptor = resources["libexec_descriptor"]
    if not _entry_exists_at(
        libexec_descriptor,
        "task-witness",
        "literal-rendered-shim install root",
    ):
        return
    root_descriptor, root_binding = _open_private_child_directory_at(
        libexec_descriptor,
        "task-witness",
        "literal-rendered-shim install root",
        create=False,
    )
    client_descriptor = -1
    try:
        if _entry_exists_at(
            root_descriptor,
            "client",
            "literal-rendered-shim client directory",
        ):
            client_descriptor, client_binding = _open_private_child_directory_at(
                root_descriptor,
                "client",
                "literal-rendered-shim client directory",
                create=False,
            )
            _unlink_optional_owned_regular_at(
                client_descriptor,
                "task_witness_client.py",
                "literal-rendered-shim client",
                expected_mode=0o500,
            )
            _remove_pinned_directory_at(
                root_descriptor,
                "client",
                client_descriptor,
                client_binding,
                "literal-rendered-shim client directory",
            )
            close_descriptor(client_descriptor, "literal-rendered-shim client directory")
            client_descriptor = -1
        _unlink_optional_owned_regular_at(
            root_descriptor,
            "task-witness",
            "literal-rendered-shim shim",
            expected_mode=0o500,
        )
        _remove_pinned_directory_at(
            libexec_descriptor,
            "task-witness",
            root_descriptor,
            root_binding,
            "literal-rendered-shim install root",
        )
    finally:
        if client_descriptor >= 0:
            close_descriptor(client_descriptor, "literal-rendered-shim client directory")
        close_descriptor(root_descriptor, "literal-rendered-shim install root")


def cleanup_qualification_workspace(resources: dict[str, Any]) -> None:
    if resources.get("closed"):
        raise QualificationError("qualification workspace is already closed")
    error: QualificationError | None = None
    try:
        _cleanup_literal_rendered_shim_install(resources)
        workspace_descriptor = resources["workspace_descriptor"]
        for name in (
            "literal-rendered-shim-process-observation.json",
            "literal-rendered-shim-observation.json",
        ):
            _unlink_optional_owned_regular_at(
                workspace_descriptor,
                name,
                "qualification workspace observation",
            )
        _remove_pinned_directory_at(
            resources["home_descriptor"],
            resources["workspace_name"],
            workspace_descriptor,
            resources["workspace_binding"],
            "qualification workspace",
        )
        if resources["created_libexec"]:
            _remove_pinned_directory_at(
                resources["local_descriptor"],
                "libexec",
                resources["libexec_descriptor"],
                resources["libexec_binding"],
                "qualification libexec directory",
            )
        if resources["created_local"]:
            _remove_pinned_directory_at(
                resources["home_descriptor"],
                ".local",
                resources["local_descriptor"],
                resources["local_binding"],
                "qualification local directory",
            )
    except QualificationError as caught:
        error = caught
    finally:
        for key, label in (
            ("workspace_descriptor", "qualification workspace"),
            ("libexec_descriptor", "qualification libexec directory"),
            ("local_descriptor", "qualification local directory"),
            ("home_descriptor", "qualification home"),
        ):
            descriptor = resources.get(key, -1)
            if isinstance(descriptor, int) and descriptor >= 0:
                try:
                    close_descriptor(descriptor, label)
                except QualificationError as caught:
                    if error is None:
                        error = caught
                resources[key] = -1
        resources["closed"] = True
    if error is not None:
        raise error


def execute_host_qualification(
    *,
    receipt_output: Path,
    candidate_root: Path,
    runtime_executable: Path,
    runtime_evidence_path: Path,
    runtime_evidence: dict[str, Any],
    platform_profile_path: Path,
    platform_profile: dict[str, Any],
    home: Path,
) -> None:
    resources: dict[str, Any] | None = None
    primary_error: QualificationError | None = None
    try:
        resources = prepare_qualification_workspace(home)
        before = capture_host_state(
            candidate_root,
            runtime_executable,
            runtime_evidence_path,
            platform_profile_path,
        )
        if (
            canonical_json_bytes(before["platform"]["profile"])
            != canonical_json_bytes(platform_profile)
            or canonical_json_bytes(before["runtime"]["evidence"])
            != canonical_json_bytes(runtime_evidence)
        ):
            raise QualificationError("qualification evidence changed before capture")
        suite_results = run_applicable_suites(
            candidate_root,
            runtime_executable,
            before["inventory"],
            before["target"],
            resources["workspace_path"],
            home,
        )
        literal_records = [
            record
            for record in suite_results
            if record.get("id") == "literal-rendered-shim"
        ]
        if len(literal_records) != 1:
            raise QualificationError(
                "literal-rendered-shim suite result inventory disagrees"
            )
        rendered_shim = bind_literal_rendered_shim(
            candidate_root,
            runtime_executable,
            before["platform"]["profile"],
            resources,
            literal_records[0],
        )
        _cleanup_literal_rendered_shim_install(resources)
        after = capture_host_state(
            candidate_root,
            runtime_executable,
            runtime_evidence_path,
            platform_profile_path,
        )
        _receipt, raw = construct_host_qualification_receipt(
            before,
            after,
            rendered_shim,
            suite_results,
        )
        publish_receipt_create_new(receipt_output, raw)
    except QualificationError as error:
        primary_error = error
    finally:
        active_error = sys.exception()
        if resources is not None and not resources.get("closed", False):
            try:
                cleanup_qualification_workspace(resources)
            except QualificationError as cleanup_error:
                if primary_error is None and active_error is None:
                    primary_error = cleanup_error
    if primary_error is not None:
        raise primary_error


def _retired_native_main() -> int:
    """Retain the reviewed design for source-level validation only."""

    try:
        args = parse_args()
        receipt_output = cli_path_operand(
            args.receipt_output,
            "receipt output",
            file_operand=True,
        )
        candidate_root = cli_path_operand(
            args.candidate_root,
            "candidate root",
            file_operand=False,
        )
        runtime_executable_path = cli_path_operand(
            args.runtime_executable,
            "runtime executable",
            file_operand=True,
        )
        runtime_evidence_path = cli_path_operand(
            args.runtime_closure_evidence,
            "runtime closure evidence",
            file_operand=True,
        )
        platform_profile_path = cli_path_operand(
            args.platform_profile,
            "platform profile",
            file_operand=True,
        )
        validate_receipt_output(receipt_output)
        require_directory(candidate_root, "candidate root")
        runtime_executable = require_file(
            runtime_executable_path,
            "runtime executable",
        )
        if not effective_access(runtime_executable, os.X_OK):
            raise QualificationError("runtime executable is not executable")
        runtime_evidence = require_file(
            runtime_evidence_path,
            "runtime closure evidence",
        )
        platform_profile = require_file(platform_profile_path, "platform profile")
        runtime_value = load_canonical_json_object(
            runtime_evidence, "runtime closure evidence"
        )
        profile = load_canonical_json_object(platform_profile, "platform profile")
        parse_runtime_closure_evidence(runtime_value)
        parse_platform_profile(profile)
        reject_non_native_profile(profile)
        home = qualification_profile_home(profile)
        reject_receipt_output_overlap(
            receipt_output,
            candidate_root,
            runtime_value,
        )
        execute_host_qualification(
            receipt_output=receipt_output,
            candidate_root=candidate_root,
            runtime_executable=runtime_executable,
            runtime_evidence_path=runtime_evidence,
            runtime_evidence=runtime_value,
            platform_profile_path=platform_profile,
            platform_profile=profile,
            home=home,
        )
        return 0
    except QualificationError as error:
        print(f"task-witness qualification: {error}", file=sys.stderr)
        return 1


def main() -> int:
    print(
        "task-witness qualification: native qualification is unavailable in this "
        "source-stage release; a host-owned content-pinned network-denied sandbox "
        "and prior review authorization are required",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
