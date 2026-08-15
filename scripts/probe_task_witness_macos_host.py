#!/usr/bin/env python3
"""Capture a bounded, non-qualifying GitHub-hosted macOS session probe."""

from __future__ import annotations

import argparse
import ctypes
import grp
import hashlib
import json
import os
import platform
import pwd
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

EXPECTED_CANDIDATE_SHA = "b47f03519068b858cf0c070b5d331ee053ef6b7b"
EXPECTED_REPOSITORY = "nisavid/agents"
EXPECTED_EVENT = "push"
EXPECTED_REF = "refs/heads/ivan/task-witness-macos-qualification-harness"
EXPECTED_WORKFLOW_REF = (
    "nisavid/agents/.github/workflows/task-witness-macos-host-probe.yml@" + EXPECTED_REF
)
SHA1_RE = re.compile(r"[0-9a-f]{40}")
POSITIVE_DECIMAL_RE = re.compile(r"[1-9][0-9]*")

MAX_PROBE_JSON_BYTES = 32 * 1024
MAX_STDOUT_BYTES = 64 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 4 * 1024
MAX_ARTIFACT_BYTES = 256 * 1024
MAX_COMMAND_OUTPUT_BYTES = 4 * 1024
COMMAND_TIMEOUT_SECONDS = 10

ARTIFACT_FILES = {
    "probe.json": MAX_PROBE_JSON_BYTES,
    "probe.status": 16,
    "probe.stderr": MAX_STDERR_BYTES,
    "probe.stdout": MAX_STDOUT_BYTES,
}
PROVISIONING_TOOLS = (
    ("dscl", "/usr/bin/dscl"),
    ("launchctl", "/bin/launchctl"),
    ("sysadminctl", "/usr/sbin/sysadminctl"),
)


class ProbeError(Exception):
    """A bounded probe-contract failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ProbeError("noncanonical-value") from error


def _require_exact_keys(value: object, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise ProbeError(f"invalid-{label}-shape")
    return value


def _require_string(value: object, label: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise ProbeError(f"invalid-{label}")
    return value


def _require_optional_string(
    value: object,
    label: str,
    maximum: int = 4096,
) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > maximum:
        raise ProbeError(f"invalid-{label}")
    return value


def _require_nonnegative_integer(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ProbeError(f"invalid-{label}")
    return value


def _require_boolean(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ProbeError(f"invalid-{label}")
    return value


def _normalize_machine(machine: str) -> str:
    normalized = machine.lower()
    return {"aarch64": "arm64", "amd64": "x86_64"}.get(
        normalized,
        normalized,
    )


def _normalized_context(environment: Mapping[str, str]) -> tuple[dict, dict]:
    required = {
        "GITHUB_EVENT_NAME",
        "GITHUB_REF",
        "GITHUB_REPOSITORY",
        "GITHUB_RUN_ATTEMPT",
        "GITHUB_RUN_ID",
        "GITHUB_SHA",
        "GITHUB_WORKFLOW_REF",
        "GITHUB_WORKFLOW_SHA",
        "ImageOS",
        "ImageVersion",
        "RUNNER_ARCH",
        "RUNNER_ENVIRONMENT",
        "RUNNER_OS",
    }
    if any(name not in environment for name in required):
        raise ProbeError("missing-github-context")
    repository = _require_string(environment["GITHUB_REPOSITORY"], "repository")
    event_name = _require_string(environment["GITHUB_EVENT_NAME"], "event-name")
    ref = _require_string(environment["GITHUB_REF"], "ref")
    commit_sha = _require_string(environment["GITHUB_SHA"], "harness-sha")
    workflow_ref = _require_string(
        environment["GITHUB_WORKFLOW_REF"],
        "workflow-ref",
    )
    workflow_sha = _require_string(
        environment["GITHUB_WORKFLOW_SHA"],
        "workflow-sha",
    )
    run_id = _require_string(environment["GITHUB_RUN_ID"], "run-id", 32)
    run_attempt_raw = _require_string(
        environment["GITHUB_RUN_ATTEMPT"],
        "run-attempt",
        16,
    )
    if (
        repository != EXPECTED_REPOSITORY
        or event_name != EXPECTED_EVENT
        or ref != EXPECTED_REF
        or workflow_ref != EXPECTED_WORKFLOW_REF
        or SHA1_RE.fullmatch(commit_sha) is None
        or SHA1_RE.fullmatch(workflow_sha) is None
        or workflow_sha != commit_sha
        or POSITIVE_DECIMAL_RE.fullmatch(run_id) is None
        or POSITIVE_DECIMAL_RE.fullmatch(run_attempt_raw) is None
    ):
        raise ProbeError("github-context-disagrees")
    harness = {
        "repository": repository,
        "ref": ref,
        "commit_sha1": commit_sha,
        "workflow_ref": workflow_ref,
        "workflow_sha1": workflow_sha,
        "run_id": run_id,
        "run_attempt": int(run_attempt_raw),
    }
    runner = {
        "environment": _require_string(
            environment["RUNNER_ENVIRONMENT"],
            "runner-environment",
            64,
        ),
        "os": _require_string(environment["RUNNER_OS"], "runner-os", 64),
        "arch": _require_string(environment["RUNNER_ARCH"], "runner-arch", 64),
        "image_os": _require_optional_string(environment["ImageOS"], "image-os", 128),
        "image_version": _require_optional_string(
            environment["ImageVersion"],
            "image-version",
            128,
        ),
    }
    return harness, runner


def _validated_observations(value: object) -> dict:
    observations = _require_exact_keys(
        value,
        {"credentials", "home", "platform", "provisioning_capability"},
        "observations",
    )
    platform_value = _require_exact_keys(
        observations["platform"],
        {
            "container_indicators",
            "kernel_release",
            "machine",
            "macos_build_version",
            "macos_product_version",
            "system",
            "translated",
        },
        "platform-observation",
    )
    platform_observation = {
        "system": _require_string(platform_value["system"], "platform-system", 64),
        "machine": _require_string(
            platform_value["machine"],
            "platform-machine",
            64,
        ),
        "kernel_release": _require_string(
            platform_value["kernel_release"],
            "kernel-release",
            1024,
        ),
        "macos_product_version": _require_optional_string(
            platform_value["macos_product_version"],
            "macos-product-version",
            256,
        ),
        "macos_build_version": _require_optional_string(
            platform_value["macos_build_version"],
            "macos-build-version",
            256,
        ),
        "translated": (
            None
            if platform_value["translated"] is None
            else _require_boolean(platform_value["translated"], "translated")
        ),
        "container_indicators": {
            name: _require_boolean(present, f"container-indicator-{name}")
            for name, present in _require_exact_keys(
                platform_value["container_indicators"],
                {"container_environment", "dockerenv", "run_containerenv"},
                "container-indicators",
            ).items()
        },
    }

    credentials_value = _require_exact_keys(
        observations["credentials"],
        {
            "admin_gid",
            "admin_member",
            "effective_gid",
            "effective_uid",
            "issetugid",
            "passwd",
            "passwd_group_gids",
            "real_gid",
            "real_uid",
            "supplementary_gids",
        },
        "credential-observation",
    )
    passwd_value = _require_exact_keys(
        credentials_value["passwd"],
        {"home", "name", "primary_gid", "shell", "uid"},
        "passwd-observation",
    )
    supplementary_gids = credentials_value["supplementary_gids"]
    if (
        not isinstance(supplementary_gids, list)
        or any(type(item) is not int or item < 0 for item in supplementary_gids)
        or supplementary_gids != sorted(set(supplementary_gids))
        or len(supplementary_gids) > 256
    ):
        raise ProbeError("invalid-supplementary-gids")
    passwd_group_gids = credentials_value["passwd_group_gids"]
    if (
        not isinstance(passwd_group_gids, list)
        or any(type(item) is not int or item < 0 for item in passwd_group_gids)
        or passwd_group_gids != sorted(set(passwd_group_gids))
        or len(passwd_group_gids) > 256
    ):
        raise ProbeError("invalid-passwd-group-gids")
    credentials = {
        "real_uid": _require_nonnegative_integer(
            credentials_value["real_uid"],
            "real-uid",
        ),
        "effective_uid": _require_nonnegative_integer(
            credentials_value["effective_uid"],
            "effective-uid",
        ),
        "real_gid": _require_nonnegative_integer(
            credentials_value["real_gid"],
            "real-gid",
        ),
        "effective_gid": _require_nonnegative_integer(
            credentials_value["effective_gid"],
            "effective-gid",
        ),
        "supplementary_gids": supplementary_gids,
        "passwd_group_gids": passwd_group_gids,
        "passwd": {
            "name": _require_string(passwd_value["name"], "passwd-name", 256),
            "uid": _require_nonnegative_integer(passwd_value["uid"], "passwd-uid"),
            "primary_gid": _require_nonnegative_integer(
                passwd_value["primary_gid"],
                "passwd-primary-gid",
            ),
            "home": _require_string(passwd_value["home"], "passwd-home", 4096),
            "shell": _require_string(passwd_value["shell"], "passwd-shell", 4096),
        },
        "issetugid": (
            None
            if credentials_value["issetugid"] is None
            else _require_boolean(credentials_value["issetugid"], "issetugid")
        ),
        "admin_gid": _require_nonnegative_integer(
            credentials_value["admin_gid"],
            "admin-gid",
        ),
        "admin_member": _require_boolean(
            credentials_value["admin_member"],
            "admin-member",
        ),
    }

    home_value = _require_exact_keys(
        observations["home"],
        {
            "filesystem_type",
            "gid",
            "kind",
            "mode",
            "path",
            "symlink_components",
            "uid",
        },
        "home-observation",
    )
    symlink_components = home_value["symlink_components"]
    if (
        not isinstance(symlink_components, list)
        or any(
            not isinstance(item, str)
            or not item.startswith("/")
            or len(item.encode("utf-8")) > 4096
            for item in symlink_components
        )
        or len(symlink_components) > 256
        or len(symlink_components) != len(set(symlink_components))
    ):
        raise ProbeError("invalid-home-symlink-components")
    home = {
        "path": _require_string(home_value["path"], "home-path", 4096),
        "kind": _require_string(home_value["kind"], "home-kind", 64),
        "uid": _require_nonnegative_integer(home_value["uid"], "home-uid"),
        "gid": _require_nonnegative_integer(home_value["gid"], "home-gid"),
        "mode": _require_nonnegative_integer(home_value["mode"], "home-mode"),
        "filesystem_type": _require_optional_string(
            home_value["filesystem_type"],
            "home-filesystem-type",
            128,
        ),
        "symlink_components": symlink_components,
    }

    provisioning_value = _require_exact_keys(
        observations["provisioning_capability"],
        {"passwordless_sudo", "tools"},
        "provisioning-capability",
    )
    tools_value = provisioning_value["tools"]
    if not isinstance(tools_value, list) or len(tools_value) != len(PROVISIONING_TOOLS):
        raise ProbeError("invalid-provisioning-tools")
    tools = []
    for index, (tool_id, tool_path) in enumerate(PROVISIONING_TOOLS):
        tool = _require_exact_keys(
            tools_value[index],
            {"available", "id", "path"},
            "provisioning-tool",
        )
        if tool["id"] != tool_id or tool["path"] != tool_path:
            raise ProbeError("provisioning-tool-disagrees")
        tools.append(
            {
                "id": tool_id,
                "path": tool_path,
                "available": _require_boolean(
                    tool["available"],
                    "provisioning-tool-availability",
                ),
            }
        )
    provisioning = {
        "passwordless_sudo": _require_boolean(
            provisioning_value["passwordless_sudo"],
            "passwordless-sudo",
        ),
        "tools": tools,
    }
    return {
        "platform": platform_observation,
        "credentials": credentials,
        "home": home,
        "provisioning_capability": provisioning,
    }


def build_probe_document(
    candidate_sha: str,
    environment: Mapping[str, str],
    observations: object,
) -> dict:
    if candidate_sha != EXPECTED_CANDIDATE_SHA:
        raise ProbeError("candidate-sha-disagrees")
    harness, runner = _normalized_context(environment)
    observed = _validated_observations(observations)
    credentials = observed["credentials"]
    passwd_value = credentials["passwd"]
    home = observed["home"]
    platform_value = observed["platform"]
    requirements = {
        "admin_absent": credentials["admin_member"] is False,
        "credentials_equal": (
            credentials["real_uid"] == credentials["effective_uid"]
            and credentials["real_gid"] == credentials["effective_gid"]
        ),
        "github_hosted": runner["environment"] == "github-hosted",
        "group_views_agree": (
            credentials["supplementary_gids"] == credentials["passwd_group_gids"]
        ),
        "home_apfs": home["filesystem_type"].lower() == "apfs",
        "home_directory": home["kind"] == "directory",
        "home_mode_0700": home["mode"] == 0o700,
        "home_owned_by_effective_uid": home["uid"] == credentials["effective_uid"],
        "home_symlink_free": home["symlink_components"] == [],
        "issetugid_false": credentials["issetugid"] is False,
        "native_darwin_arm64": (
            platform_value["system"] == "darwin"
            and platform_value["machine"] == "arm64"
        ),
        "no_container_indicators": not any(
            platform_value["container_indicators"].values()
        ),
        "nonroot_uid": credentials["effective_uid"] != 0,
        "not_translated": platform_value["translated"] is False,
        "passwd_backed_identity": (
            bool(passwd_value["name"])
            and passwd_value["uid"] == credentials["effective_uid"]
            and passwd_value["primary_gid"] == credentials["effective_gid"]
            and passwd_value["home"] == home["path"]
        ),
        "runner_arch_arm64": runner["arch"] == "ARM64",
        "runner_os_macos": runner["os"] == "macOS",
    }
    disposition = (
        "direct-session-eligible"
        if all(requirements.values())
        else "direct-session-ineligible"
    )
    unsigned = {
        "schema_version": 1,
        "contract": "task-witness-macos-github-host-probe-v1",
        "claim": "host-prerequisite-probe-only",
        "candidate_sha1": candidate_sha,
        "harness": harness,
        "runner": runner,
        "observations": observed,
        "requirements": requirements,
        "disposition": disposition,
    }
    document = {
        **unsigned,
        "content_sha256": hashlib.sha256(canonical_bytes(unsigned)).hexdigest(),
    }
    if len(canonical_bytes(document)) > MAX_PROBE_JSON_BYTES:
        raise ProbeError("probe-json-too-large")
    return document


def build_probe_error_document(
    candidate_sha: str,
    environment: Mapping[str, str],
    error_code: str,
) -> dict:
    if candidate_sha != EXPECTED_CANDIDATE_SHA:
        raise ProbeError("candidate-sha-disagrees")
    harness, runner = _normalized_context(environment)
    code = _require_string(error_code, "probe-error-code", 128)
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", code) is None:
        raise ProbeError("invalid-probe-error-code")
    unsigned = {
        "schema_version": 1,
        "contract": "task-witness-macos-github-host-probe-v1",
        "claim": "host-prerequisite-probe-only",
        "candidate_sha1": candidate_sha,
        "harness": harness,
        "runner": runner,
        "observations": None,
        "requirements": None,
        "disposition": "probe-error",
        "error": {"code": code},
    }
    document = {
        **unsigned,
        "content_sha256": hashlib.sha256(canonical_bytes(unsigned)).hexdigest(),
    }
    if len(canonical_bytes(document)) > MAX_PROBE_JSON_BYTES:
        raise ProbeError("probe-json-too-large")
    return document


def _run_bounded_command(argv: Sequence[str]) -> tuple[int, str, str]:
    try:
        process = subprocess.run(
            list(argv),
            check=False,
            capture_output=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            env={
                "HOME": "/var/empty",
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            },
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProbeError("host-command-failed") from error
    if (
        len(process.stdout) > MAX_COMMAND_OUTPUT_BYTES
        or len(process.stderr) > MAX_COMMAND_OUTPUT_BYTES
    ):
        raise ProbeError("host-command-output-too-large")
    try:
        stdout = process.stdout.decode("utf-8", "strict").strip()
        stderr = process.stderr.decode("utf-8", "strict").strip()
    except UnicodeDecodeError as error:
        raise ProbeError("host-command-output-invalid") from error
    return process.returncode, stdout, stderr


def _command_value(argv: Sequence[str]) -> str:
    status, stdout, _stderr = _run_bounded_command(argv)
    return stdout if status == 0 else ""


def _darwin_issetugid(system: str) -> bool | None:
    if system != "darwin":
        return None
    try:
        libc = ctypes.CDLL(None)
        issetugid = libc.issetugid
        issetugid.argtypes = []
        issetugid.restype = ctypes.c_int
        return bool(issetugid())
    except (AttributeError, OSError) as error:
        raise ProbeError("issetugid-unavailable") from error


class _DarwinStatFs(ctypes.Structure):
    _fields_ = [
        ("f_bsize", ctypes.c_uint32),
        ("f_iosize", ctypes.c_int32),
        ("f_blocks", ctypes.c_uint64),
        ("f_bfree", ctypes.c_uint64),
        ("f_bavail", ctypes.c_uint64),
        ("f_files", ctypes.c_uint64),
        ("f_ffree", ctypes.c_uint64),
        ("f_fsid", ctypes.c_int32 * 2),
        ("f_owner", ctypes.c_uint32),
        ("f_type", ctypes.c_uint32),
        ("f_flags", ctypes.c_uint32),
        ("f_fssubtype", ctypes.c_uint32),
        ("f_fstypename", ctypes.c_char * 16),
        ("f_mntonname", ctypes.c_char * 1024),
        ("f_mntfromname", ctypes.c_char * 1024),
        ("f_flags_ext", ctypes.c_uint32),
        ("f_reserved", ctypes.c_uint32 * 7),
    ]


def _darwin_filesystem_type(path: Path, system: str) -> str:
    if system != "darwin":
        return ""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        statfs = libc.statfs
        statfs.argtypes = [ctypes.c_char_p, ctypes.POINTER(_DarwinStatFs)]
        statfs.restype = ctypes.c_int
        observed = _DarwinStatFs()
        if statfs(os.fsencode(path), ctypes.byref(observed)) != 0:
            raise ProbeError("filesystem-type-unavailable")
        return bytes(observed.f_fstypename).split(b"\0", 1)[0].decode("ascii").lower()
    except (AttributeError, OSError, UnicodeDecodeError) as error:
        raise ProbeError("filesystem-type-unavailable") from error


def _darwin_translation_state(system: str, machine: str) -> bool | None:
    if system != "darwin":
        return None
    if machine == "arm64":
        return False
    status, stdout, _stderr = _run_bounded_command(
        ["/usr/sbin/sysctl", "-in", "sysctl.proc_translated"]
    )
    if status != 0:
        return None
    return {"0": False, "1": True}.get(stdout)


def _path_symlink_components(path: Path) -> list[str]:
    if not path.is_absolute():
        raise ProbeError("home-path-not-absolute")
    current = Path(path.anchor)
    symlink_components = []
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as error:
            raise ProbeError("home-component-observation-failed") from error
        if stat.S_ISLNK(metadata.st_mode):
            symlink_components.append(str(current))
    return symlink_components


def collect_observations() -> dict:
    system = platform.system().lower()
    machine = _normalize_machine(platform.machine())
    translated = _darwin_translation_state(system, machine)

    real_uid = os.getuid()
    effective_uid = os.geteuid()
    real_gid = os.getgid()
    effective_gid = os.getegid()
    supplementary_gids = sorted(set(os.getgroups()))
    try:
        account = pwd.getpwuid(effective_uid)
        admin = grp.getgrnam("admin")
    except KeyError as error:
        raise ProbeError("passwd-or-admin-group-unavailable") from error
    try:
        passwd_group_gids = sorted(
            set(os.getgrouplist(account.pw_name, account.pw_gid))
        )
    except OSError as error:
        raise ProbeError("passwd-group-list-unavailable") from error
    admin_member = (
        account.pw_name in admin.gr_mem
        or account.pw_gid == admin.gr_gid
        or admin.gr_gid in supplementary_gids
        or admin.gr_gid in passwd_group_gids
    )
    home = Path(account.pw_dir)
    home_symlink_components = _path_symlink_components(home)
    try:
        home_metadata = home.lstat()
    except OSError as error:
        raise ProbeError("home-observation-failed") from error
    if stat.S_ISDIR(home_metadata.st_mode):
        home_kind = "directory"
    elif stat.S_ISLNK(home_metadata.st_mode):
        home_kind = "symlink"
    else:
        home_kind = "other"
    filesystem_type = ""
    if home_kind == "directory":
        filesystem_type = _darwin_filesystem_type(home, system)
    sudo_available = Path("/usr/bin/sudo").is_file()
    passwordless_sudo = False
    if sudo_available:
        try:
            sudo_status, _sudo_stdout, _sudo_stderr = _run_bounded_command(
                ["/usr/bin/sudo", "-n", "/usr/bin/true"]
            )
        except ProbeError:
            sudo_status = 1
        passwordless_sudo = sudo_status == 0
    return {
        "platform": {
            "system": system,
            "machine": machine,
            "kernel_release": platform.release(),
            "macos_product_version": _command_value(
                ["/usr/bin/sw_vers", "-productVersion"]
            ),
            "macos_build_version": _command_value(
                ["/usr/bin/sw_vers", "-buildVersion"]
            ),
            "translated": translated,
            "container_indicators": {
                "dockerenv": Path("/.dockerenv").exists(),
                "run_containerenv": Path("/run/.containerenv").exists(),
                "container_environment": bool(os.environ.get("container")),
            },
        },
        "credentials": {
            "real_uid": real_uid,
            "effective_uid": effective_uid,
            "real_gid": real_gid,
            "effective_gid": effective_gid,
            "supplementary_gids": supplementary_gids,
            "passwd_group_gids": passwd_group_gids,
            "passwd": {
                "name": account.pw_name,
                "uid": account.pw_uid,
                "primary_gid": account.pw_gid,
                "home": account.pw_dir,
                "shell": account.pw_shell,
            },
            "issetugid": _darwin_issetugid(system),
            "admin_gid": admin.gr_gid,
            "admin_member": admin_member,
        },
        "home": {
            "path": account.pw_dir,
            "kind": home_kind,
            "uid": home_metadata.st_uid,
            "gid": home_metadata.st_gid,
            "mode": stat.S_IMODE(home_metadata.st_mode),
            "filesystem_type": filesystem_type,
            "symlink_components": home_symlink_components,
        },
        "provisioning_capability": {
            "passwordless_sudo": passwordless_sudo,
            "tools": [
                {
                    "id": tool_id,
                    "path": tool_path,
                    "available": (
                        Path(tool_path).is_file()
                        and os.access(tool_path, os.X_OK, follow_symlinks=False)
                    ),
                }
                for tool_id, tool_path in PROVISIONING_TOOLS
            ],
        },
    }


def write_create_new(path: Path, raw: bytes, mode: int = 0o600) -> None:
    temporary_descriptor = -1
    temporary_path = ""
    try:
        temporary_descriptor, temporary_path = tempfile.mkstemp(
            prefix=".task-witness-macos-host-probe-",
            dir=path.parent,
        )
        os.fchmod(temporary_descriptor, mode)
        offset = 0
        while offset < len(raw):
            offset += os.write(temporary_descriptor, raw[offset:])
        os.fsync(temporary_descriptor)
        os.close(temporary_descriptor)
        temporary_descriptor = -1
        os.link(temporary_path, path, follow_symlinks=False)
    except OSError as error:
        raise ProbeError("output-create-new-failed") from error
    finally:
        if temporary_descriptor >= 0:
            os.close(temporary_descriptor)
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            except OSError as error:
                raise ProbeError("temporary-output-cleanup-failed") from error


def _bounded_regular_file(path: Path, maximum: int, label: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ProbeError(f"missing-{label}") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size < 0
        or metadata.st_size > maximum
    ):
        raise ProbeError(f"unsafe-{label}")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ProbeError(f"unreadable-{label}") from error
    if len(raw) != metadata.st_size or len(raw) > maximum:
        raise ProbeError(f"changed-{label}")
    return raw


def _artifact_payloads(artifact_root: Path, include_manifest: bool) -> dict[str, bytes]:
    expected_names = set(ARTIFACT_FILES)
    if include_manifest:
        expected_names.add("SHA256SUMS")
    try:
        observed_names = {entry.name for entry in artifact_root.iterdir()}
    except OSError as error:
        raise ProbeError("artifact-root-unreadable") from error
    if observed_names != expected_names:
        raise ProbeError("artifact-file-set-disagrees")
    payloads = {
        name: _bounded_regular_file(
            artifact_root / name,
            maximum,
            name.replace(".", "-"),
        )
        for name, maximum in ARTIFACT_FILES.items()
    }
    if payloads["probe.status"] not in {b"0\n", b"1\n", b"2\n"}:
        raise ProbeError("probe-status-invalid")
    if sum(len(raw) for raw in payloads.values()) > MAX_ARTIFACT_BYTES:
        raise ProbeError("artifact-too-large")
    return payloads


def _manifest_bytes(payloads: Mapping[str, bytes]) -> bytes:
    raw = b"".join(
        hashlib.sha256(payloads[name]).hexdigest().encode("ascii")
        + b"  ./"
        + name.encode("ascii")
        + b"\n"
        for name in sorted(payloads)
    )
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ProbeError("manifest-too-large")
    return raw


def write_artifact_manifest(artifact_root: Path, output: Path) -> None:
    payloads = _artifact_payloads(artifact_root, include_manifest=False)
    write_create_new(output, _manifest_bytes(payloads))


def verify_artifact_manifest(artifact_root: Path, manifest: Path) -> None:
    payloads = _artifact_payloads(artifact_root, include_manifest=True)
    observed = _bounded_regular_file(manifest, MAX_MANIFEST_BYTES, "manifest")
    if observed != _manifest_bytes(payloads):
        raise ProbeError("artifact-manifest-disagrees")


def run_probe(output: Path, candidate_sha: str) -> int:
    if candidate_sha != EXPECTED_CANDIDATE_SHA:
        raise ProbeError("candidate-sha-disagrees")
    _normalized_context(os.environ)
    status = 0
    try:
        observations = collect_observations()
        document = build_probe_document(candidate_sha, os.environ, observations)
        if document["disposition"] != "direct-session-eligible":
            status = 1
    except ProbeError as error:
        document = build_probe_error_document(candidate_sha, os.environ, error.code)
        status = 2
    raw = canonical_bytes(document)
    write_create_new(output, raw)
    print(document["disposition"])
    if status == 2:
        print(
            f"task-witness macOS host probe: {document['error']['code']}",
            file=sys.stderr,
        )
    return status


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    subparsers = value.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe")
    probe.add_argument("--candidate-sha", required=True)
    probe.add_argument("--output", required=True, type=Path)

    manifest = subparsers.add_parser("write-artifact-manifest")
    manifest.add_argument("--artifact-root", required=True, type=Path)
    manifest.add_argument("--output", required=True, type=Path)

    verify = subparsers.add_parser("verify-artifact-manifest")
    verify.add_argument("--artifact-root", required=True, type=Path)
    verify.add_argument("--manifest", required=True, type=Path)
    return value


def main() -> int:
    try:
        args = parser().parse_args()
        if args.command == "probe":
            return run_probe(args.output, args.candidate_sha)
        if args.command == "write-artifact-manifest":
            write_artifact_manifest(args.artifact_root, args.output)
            return 0
        if args.command == "verify-artifact-manifest":
            verify_artifact_manifest(args.artifact_root, args.manifest)
            return 0
        raise ProbeError("unsupported-command")
    except ProbeError as error:
        print(f"task-witness macOS host probe: {error.code}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
