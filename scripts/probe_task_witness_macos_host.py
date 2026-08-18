#!/usr/bin/env python3
# ruff: noqa: SIM905

from __future__ import annotations

import argparse
import ctypes
import errno
import grp
import hashlib
import json
import os
import platform
import plistlib
import pwd
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

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
MAX_HELPER_BYTES = 256 * 1024
MAX_OWNERSHIP_BYTES = 4 * 1024
MAX_ACCOUNT_BINDING_BYTES = 4 * 1024
MAX_USER_DOMAIN_RESET_BYTES = 4 * 1024
MAX_HOME_CLEANUP_BYTES = 256 * 1024
MAX_PROCESS_LIST_BYTES = 64 * 1024
MAX_HOME_ENTRY_OBSERVATION_FILE_BYTES = 4 * 1024
MAX_HOME_LIBRARY_DEPTH = 16
MAX_HOME_LIBRARY_NODES = 512
MAX_HOME_LIBRARY_COMPONENT_BYTES = 255
MAX_HOME_LIBRARY_PATH_BYTES = 2 * 1024
MAX_HOME_LIBRARY_FILE_BYTES = 1024 * 1024
MAX_HOME_LIBRARY_TOTAL_BYTES = 8 * 1024 * 1024
MAX_HOME_LIBRARY_XATTR_LIST_BYTES = 4096
MAX_HOME_LIBRARY_QUARANTINE_VALUE_BYTES = 4096
MAX_DISPOSABLE_HOME_ENTRIES = 3
MAX_LAUNCHD_PROBE_ENTRIES = 4
COMMAND_TIMEOUT_SECONDS = 10
LAUNCHD_POLL_INTERVAL_SECONDS = 0.25
LAUNCHD_POLL_TIMEOUT_SECONDS = 30
PROCESS_EXIT_POLL_INTERVAL_SECONDS = 0.25
PROCESS_EXIT_POLL_TIMEOUT_SECONDS = 30
PROCESS_LIST_MIN_TIMEOUT_SECONDS = 1
LAUNCHD_PRINT_MAX_BYTES = 16 * 1024
LAUNCHCTL_NOT_FOUND_STATUS = 113
RENAME_EXCL = 0x00000004
RENAME_NOFOLLOW_ANY = 0x00000010
ACL_FIRST_ENTRY = 0
ACL_TYPE_EXTENDED = 0x00000100
XATTR_SHOWCOMPRESSION = 0x00000020
DISPOSABLE_UID_MIN = 502
DISPOSABLE_UID_MAX = 599
DSCL_UID_MIN = -(1 << 31)
DSCL_UID_MAX = (1 << 31) - 1
DSCL_UID_RE = re.compile(r"(?:0|[1-9][0-9]*|-[1-9][0-9]*)")
DSCL_ATTRIBUTE_PREFIXES = ("dsAttrTypeNative:", "dsAttrTypeStandard:")
DISABLED_PASSWORD_WRITE_MARKER = "*"
DISABLED_PASSWORD_READBACK_MARKERS = frozenset(
    (DISABLED_PASSWORD_WRITE_MARKER, "********")
)
LAUNCHD_ACCOUNT_RE = re.compile(r"twq-[0-9a-f]{12}")
LAUNCHD_LABEL_RE = re.compile(r"io\.nisavid\.task-witness\.macos-probe\.[0-9a-f]{12}")
LAUNCHD_OWNERSHIP_MARKER_RE = re.compile(r"[0-9a-f]{32}")
LAUNCHD_STAGE_RE = re.compile(
    r"/private/var/tmp/task-witness-macos-launchd-[1-9][0-9]*-[1-9][0-9]*"
)

ARTIFACT_FILES = {
    "probe.json": MAX_PROBE_JSON_BYTES,
    "probe.status": 16,
    "probe.stderr": MAX_STDERR_BYTES,
    "probe.stdout": MAX_STDOUT_BYTES,
}
LAUNCHD_CHILD_FILES = {
    "probe.json": MAX_PROBE_JSON_BYTES,
    "probe.status": 16,
    "probe.stderr": MAX_STDERR_BYTES,
    "probe.stdout": MAX_STDOUT_BYTES,
}
LAUNCHD_ARTIFACT_FILES = {
    "cleanup.json": 16 * 1024,
    "launchd.loaded": LAUNCHD_PRINT_MAX_BYTES,
    "launchd.terminal": LAUNCHD_PRINT_MAX_BYTES,
    "lifecycle.json": 16 * 1024,
    "lifecycle.status": 16,
    **LAUNCHD_CHILD_FILES,
}
PROVISIONING_TOOLS = (
    ("dscl", "/usr/bin/dscl"),
    ("launchctl", "/bin/launchctl"),
    ("sysadminctl", "/usr/sbin/sysadminctl"),
)
LAUNCHD_CONTEXT_NAMES = (
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
)
LIFECYCLE_COMMAND_IDS = frozenset(
    {
        "account-create-record",
        "account-delete",
        "account-generated-uid-read",
        "account-name-list",
        "account-record-read",
        "account-set-authentication-authority",
        "account-set-gid",
        "account-set-hidden",
        "account-set-home",
        "account-set-password",
        "account-set-shell",
        "account-set-uid",
        "account-uid-list",
        "launchd-bootstrap",
        "launchd-bootout",
        "launchd-kickstart",
        "process-list",
    }
)
DISPOSABLE_PROCESS_REMAINS_CODES = frozenset(
    {
        "disposable-user-background-agent-names-remain",
        "disposable-user-background-and-spotlight-names-remain",
        "disposable-user-cfprefsd-name-remains",
        "disposable-user-distnoted-name-remains",
        "disposable-user-launchd-name-remains",
        "disposable-user-mixed-parent-topologies-remain",
        "disposable-user-mixed-processes-remain",
        "disposable-user-other-uid-parented-process-remains",
        "disposable-user-other-uid-parented-processes-remain",
        "disposable-user-parent-unobserved-process-remains",
        "disposable-user-parent-unobserved-processes-remain",
        "disposable-user-pid1-parented-process-remains",
        "disposable-user-pid1-parented-processes-remain",
        "disposable-user-probe-and-other-processes-remain",
        "disposable-user-probe-name-remains",
        "disposable-user-process-observation-unstable",
        "disposable-user-root-parented-process-remains",
        "disposable-user-root-parented-processes-remain",
        "disposable-user-same-uid-process-tree-remains",
        "disposable-user-spotlight-and-other-processes-remain",
        "disposable-user-spotlight-worker-remains",
        "disposable-user-spotlight-workers-remain",
        "disposable-user-zombie-only-remains",
    }
)
DISPOSABLE_UID_ACTIVE_CODES = frozenset(
    {
        "disposable-uid-active-before-bootstrap",
        "disposable-uid-active-before-create",
    }
)
USER_DOMAIN_RESET_CAPABILITY = "github-hosted-ephemeral-user-domain-reset-v1"
USER_DOMAIN_RESET_SURVIVOR_CODE = "disposable-user-pid1-parented-processes-remain"
HOME_LIBRARY_NAME = "Library"
HOME_LIBRARY_QUARANTINE_NAME = ".task-witness-library-cleanup"
_HOME_LIBRARY_SCOPES = ("root", "directory", "file")
_XATTR_PHASES = (
    "journal-inventory source-revalidation quarantine-revalidation "
    "delete-boundary".split()
)
_XATTR_PATH_FAMILIES = (
    "direct preferences caches application-support containers "
    "group-containers metadata nested-other".split()
)
_XATTR_FAMILIES = (
    "apple-provenance apple-quarantine apple-quarantine-stable-bounded "
    "apple-quarantine-unreadable apple-metadata apple-mixed apple-other "
    "compression resource-fork finder-info nonapple-other mixed-other "
    "overflow unstable unclassified".split()
)
_HOME_LIBRARY_QUARANTINE_VALUE_DIGEST_DOMAIN = (
    b"task-witness-macos-home-library-quarantine-value-v1\0"
)
_XATTR_PATH_FAMILY_BY_COMPONENT = {
    family.replace("-", " ").title(): family for family in _XATTR_PATH_FAMILIES[1:-1]
}
HOME_LIBRARY_UNSAFE_CAUSES = frozenset(
    "bound-home-acl duplicate-identity entry-stat-unsupported-kind "
    "file-stat-link-count path-component root-kind".split()
    + [
        f"{scope}-{attribute}"
        for scope in _HOME_LIBRARY_SCOPES
        for attribute in ("acl", "xattr")
    ]
    + [
        f"{scope}-stat-{invariant}"
        for scope in _HOME_LIBRARY_SCOPES
        for invariant in "device-mismatch uid-mismatch gid-mismatch "
        "group-other-write flags-present owner-access".split()
    ]
)
HOME_LIBRARY_ALLOWED_PHASES = frozenset(
    {
        "child-entry",
        "child-read",
        "cleanup-entry",
        "home-removal",
        "marker-replay",
        "post-child-terminal",
        "post-reset",
        "post-system-bootout",
        "pre-account-delete",
        "pre-home-removal",
        "pre-journal",
        "pre-reset",
        "pre-reset-marker",
    }
)
HOME_CLEANUP_DIAGNOSTIC_PHASES = frozenset(
    {
        "child-entry",
        "child-read",
        "cleanup-entry",
        "home-removal",
        "marker-replay",
        "post-child-terminal",
        "post-home-create",
        "post-reset",
        "post-system-bootstrap",
        "post-system-bootout",
        "pre-account-delete",
        "pre-home-removal",
        "pre-journal",
        "pre-reset",
        "pre-reset-marker",
    }
)
HOME_CLEANUP_DIAGNOSTIC_DETAILS = frozenset(
    {
        "home-entry-set-drift",
        "home-entry-known-library-and-text-encoding",
        "home-entry-known-library-owned-directory",
        "home-entry-known-text-encoding-owned-file",
        "home-entry-multiple-or-mixed",
        "home-entry-observation-unreadable",
        "home-entry-observation-unstable",
        "home-entry-single-owned-directory-other",
        "home-entry-single-owned-file-other",
        "home-entry-unsafe-or-foreign",
        "home-identity-drift",
        "home-kind-drift",
        "home-mode-drift",
        "home-owner-drift",
        "home-read-failed",
        "home-state-drift",
        "probe-child-kind-drift",
        "probe-child-link-count-drift",
        "probe-child-owner-drift",
        "probe-child-read-failed",
        "probe-child-size-drift",
        "probe-entry-set-drift",
        "probe-identity-drift",
        "probe-kind-drift",
        "probe-missing",
        "probe-mode-drift",
        "probe-owner-drift",
        "probe-read-failed",
    }
)


class ProbeError(Exception):
    """A bounded probe-contract failure."""

    def __init__(self, code: str, *, secondary_code: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.secondary_code = secondary_code


def _home_cleanup_drift_error(phase: str, detail: str) -> ProbeError:
    if (
        phase not in HOME_CLEANUP_DIAGNOSTIC_PHASES
        or detail not in HOME_CLEANUP_DIAGNOSTIC_DETAILS
    ):
        return ProbeError("home-cleanup-diagnostic-invalid")
    return ProbeError(f"home-cleanup-{phase}-{detail}")


def _home_entry_observation_snapshot(
    home: Path,
    *,
    expected_uid: int,
    expected_gid: int,
) -> tuple:
    home_metadata = home.lstat()
    entries = []
    names = _bounded_path_directory_names(
        home,
        home_metadata,
        MAX_DISPOSABLE_HOME_ENTRIES,
        limit_code="unreadable-home-entry-observation",
        failure_code="unreadable-home-entry-observation",
    )
    for name in names:
        if name == "launchd-probe":
            continue
        entry = home / name
        metadata = entry.lstat()
        digest = None
        uid_matches = metadata.st_uid == expected_uid
        gid_matches = metadata.st_gid == expected_gid
        device_matches = metadata.st_dev == home_metadata.st_dev
        if (
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_nlink == 1
            and 0 <= metadata.st_size <= MAX_HOME_ENTRY_OBSERVATION_FILE_BYTES
            and uid_matches
            and gid_matches
            and device_matches
        ):
            digest = hashlib.sha256(
                _read_stable_regular_file(
                    entry,
                    MAX_HOME_ENTRY_OBSERVATION_FILE_BYTES,
                    "home-entry-observation",
                    expected_metadata=_stable_regular_file_metadata(metadata),
                )
            ).hexdigest()
        entries.append(
            (
                name,
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
                metadata.st_nlink,
                metadata.st_size,
                metadata.st_mtime_ns,
                uid_matches,
                gid_matches,
                device_matches,
                digest,
            )
        )
    return (home_metadata.st_dev, home_metadata.st_ino, tuple(entries))


def _classified_unexpected_home_entry_detail(
    home: Path,
    observed_names: set[str],
    *,
    expected_uid: int,
    expected_gid: int,
) -> str:
    try:
        first = _home_entry_observation_snapshot(
            home,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        second = _home_entry_observation_snapshot(
            home,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
    except (OSError, ProbeError):
        return "home-entry-observation-unreadable"
    entries = first[2]
    if first != second or {entry[0] for entry in entries} != observed_names - {
        "launchd-probe"
    }:
        return "home-entry-observation-unstable"
    if not entries:
        return "home-entry-set-drift"

    def owned_directory(entry: tuple) -> bool:
        return stat.S_ISDIR(entry[3]) and entry[7] and entry[8] and entry[9]

    def owned_file(entry: tuple) -> bool:
        return (
            stat.S_ISREG(entry[3])
            and entry[4] == 1
            and 0 <= entry[5] <= MAX_HOME_ENTRY_OBSERVATION_FILE_BYTES
            and entry[7]
            and entry[8]
            and entry[9]
            and isinstance(entry[10], str)
        )

    by_name = {entry[0]: entry for entry in entries}
    if set(by_name) == {"Library"} and owned_directory(by_name["Library"]):
        return "home-entry-known-library-owned-directory"
    if set(by_name) == {".CFUserTextEncoding"} and owned_file(
        by_name[".CFUserTextEncoding"]
    ):
        return "home-entry-known-text-encoding-owned-file"
    if (
        set(by_name) == {".CFUserTextEncoding", "Library"}
        and owned_directory(by_name["Library"])
        and owned_file(by_name[".CFUserTextEncoding"])
    ):
        return "home-entry-known-library-and-text-encoding"
    if any(not owned_directory(entry) and not owned_file(entry) for entry in entries):
        return "home-entry-unsafe-or-foreign"
    if len(entries) == 1:
        return (
            "home-entry-single-owned-directory-other"
            if owned_directory(entries[0])
            else "home-entry-single-owned-file-other"
        )
    return "home-entry-multiple-or-mixed"


def _require_allowed_disposable_home_names(
    home: Path,
    observed_names: set[str],
    *,
    expected_uid: int,
    expected_gid: int,
    diagnostic_phase: str,
) -> None:
    if observed_names == {"launchd-probe"}:
        return
    detail = _classified_unexpected_home_entry_detail(
        home,
        observed_names,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
    )
    if (
        diagnostic_phase in HOME_LIBRARY_ALLOWED_PHASES
        and observed_names == {"launchd-probe", HOME_LIBRARY_NAME}
        and detail == "home-entry-known-library-owned-directory"
    ):
        return
    raise _home_cleanup_drift_error(diagnostic_phase, detail)


def _merge_probe_error(
    primary_code: str | None,
    secondary_code: str | None,
    incoming: ProbeError,
) -> tuple[str, str | None]:
    if primary_code is None:
        return incoming.code, incoming.secondary_code
    if secondary_code is None and incoming.code != primary_code:
        return primary_code, incoming.code
    return primary_code, secondary_code


class DisposableAccount(NamedTuple):
    name: str
    uid: int
    gid: int
    home: Path


class LaunchdPlan(NamedTuple):
    account: DisposableAccount
    label: str
    stage_root: Path
    helper: Path
    plist: Path


class LaunchctlJobStructure(NamedTuple):
    top_values: dict[str, tuple[str, ...]]
    top_blocks: dict[str, tuple[tuple[str, ...], ...]]
    block_values: tuple[tuple[tuple[str, ...], str], ...]


class ValidatedLaunchdJobSnapshot(NamedTuple):
    binding: dict[str, str]
    sanitized: str


class ValidatedStageBindings(NamedTuple):
    account: dict | None
    launchd: dict | None
    domain_reset: dict | None = None
    home_cleanup: dict | None = None


class ProcessRecord(NamedTuple):
    uid: int
    pid: int
    ppid: int
    pgid: int
    state: str
    command: str


class ObservedHomeLibrary(NamedTuple):
    inventory: dict
    stability_sha256: str


class _HomeLibraryQuarantineEvidence(NamedTuple):
    value_length: int
    value_sha256: str


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


def _require_user_domain_reset_capability(
    authorization_sha: object,
    environment: Mapping[str, str],
) -> tuple[dict, dict]:
    try:
        harness, runner = _normalized_context(environment)
    except ProbeError as error:
        raise ProbeError("user-domain-reset-capability-unavailable") from error
    if (
        not isinstance(authorization_sha, str)
        or authorization_sha != harness["commit_sha1"]
        or runner["environment"] != "github-hosted"
        or runner["os"] != "macOS"
        or runner["arch"] != "ARM64"
        or runner["image_os"] != "macos15"
        or not runner["image_version"]
    ):
        raise ProbeError("user-domain-reset-capability-unavailable")
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


def _validated_probe_error(
    error_code: str,
    secondary_error_code: str | None = None,
) -> dict[str, str]:
    code = _require_string(error_code, "probe-error-code", 128)
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", code) is None:
        raise ProbeError("invalid-probe-error-code")
    error = {"code": code}
    if secondary_error_code is not None:
        secondary = _require_string(
            secondary_error_code,
            "probe-secondary-error-code",
            128,
        )
        if (
            re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", secondary) is None
            or secondary == code
        ):
            raise ProbeError("invalid-probe-secondary-error-code")
        error["secondary_code"] = secondary
    return error


def build_probe_error_document(
    candidate_sha: str,
    environment: Mapping[str, str],
    error_code: str,
) -> dict:
    if candidate_sha != EXPECTED_CANDIDATE_SHA:
        raise ProbeError("candidate-sha-disagrees")
    harness, runner = _normalized_context(environment)
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
        "error": _validated_probe_error(error_code),
    }
    document = {
        **unsigned,
        "content_sha256": hashlib.sha256(canonical_bytes(unsigned)).hexdigest(),
    }
    if len(canonical_bytes(document)) > MAX_PROBE_JSON_BYTES:
        raise ProbeError("probe-json-too-large")
    return document


def _validated_launchd_observations(value: object) -> dict:
    observations = _require_exact_keys(
        value,
        {
            "credentials",
            "environment_exact",
            "home",
            "passwordless_sudo",
            "platform",
            "process",
        },
        "launchd-observations",
    )
    base = _validated_observations(
        {
            "credentials": observations["credentials"],
            "home": observations["home"],
            "platform": observations["platform"],
            "provisioning_capability": {
                "passwordless_sudo": False,
                "tools": [
                    {"available": False, "id": tool_id, "path": tool_path}
                    for tool_id, tool_path in PROVISIONING_TOOLS
                ],
            },
        }
    )
    process_value = _require_exact_keys(
        observations["process"],
        {"label", "parent_path", "pid", "ppid"},
        "launchd-process-observation",
    )
    parent_path = _require_string(
        process_value["parent_path"],
        "launchd-parent-path",
        4096,
    )
    if not parent_path.startswith("/"):
        raise ProbeError("invalid-launchd-parent-path")
    process = {
        "pid": _require_nonnegative_integer(process_value["pid"], "launchd-pid"),
        "ppid": _require_nonnegative_integer(process_value["ppid"], "launchd-ppid"),
        "parent_path": parent_path,
        "label": _require_string(process_value["label"], "launchd-label", 256),
    }
    return {
        "platform": base["platform"],
        "credentials": base["credentials"],
        "home": base["home"],
        "environment_exact": _require_boolean(
            observations["environment_exact"],
            "launchd-environment-exact",
        ),
        "passwordless_sudo": _require_boolean(
            observations["passwordless_sudo"],
            "launchd-passwordless-sudo",
        ),
        "process": process,
    }


def build_launchd_user_probe_document(
    candidate_sha: str,
    environment: Mapping[str, str],
    observations: object,
) -> dict:
    if candidate_sha != EXPECTED_CANDIDATE_SHA:
        raise ProbeError("candidate-sha-disagrees")
    harness, runner = _normalized_context(environment)
    expected_label = _require_string(
        environment.get("TASK_WITNESS_LAUNCHD_LABEL"),
        "launchd-label",
        256,
    )
    if LAUNCHD_LABEL_RE.fullmatch(expected_label) is None:
        raise ProbeError("invalid-launchd-label")
    observed = _validated_launchd_observations(observations)
    credentials = observed["credentials"]
    passwd_value = credentials["passwd"]
    home = observed["home"]
    process = observed["process"]
    platform_value = observed["platform"]
    requirements = {
        "admin_absent": credentials["admin_member"] is False,
        "child_environment_exact": observed["environment_exact"] is True,
        "credentials_equal": (
            credentials["real_uid"] == credentials["effective_uid"]
            and credentials["real_gid"] == credentials["effective_gid"]
        ),
        "direct_launchd_parent": (
            process["pid"] > 1
            and process["ppid"] == 1
            and Path(process["parent_path"]).name == "launchd"
            and process["label"] == expected_label
        ),
        "github_hosted": runner["environment"] == "github-hosted",
        "group_views_agree": (
            credentials["supplementary_gids"] == credentials["passwd_group_gids"]
        ),
        "home_apfs": home["filesystem_type"].lower() == "apfs",
        "home_directory": home["kind"] == "directory",
        "home_group_is_primary": home["gid"] == credentials["effective_gid"],
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
        "nonroot_primary_gids": (
            credentials["real_gid"] != 0
            and credentials["effective_gid"] != 0
            and passwd_value["primary_gid"] != 0
            and home["gid"] != 0
        ),
        "nonroot_uid": credentials["effective_uid"] != 0,
        "not_translated": platform_value["translated"] is False,
        "passwd_backed_identity": (
            bool(passwd_value["name"])
            and passwd_value["uid"] == credentials["effective_uid"]
            and passwd_value["primary_gid"] == credentials["effective_gid"]
            and passwd_value["home"] == home["path"]
            and passwd_value["shell"] == "/usr/bin/false"
        ),
        "passwordless_sudo_absent": observed["passwordless_sudo"] is False,
        "root_group_absent": (
            0 not in credentials["supplementary_gids"]
            and 0 not in credentials["passwd_group_gids"]
        ),
        "runner_arch_arm64": runner["arch"] == "ARM64",
        "runner_os_macos": runner["os"] == "macOS",
    }
    disposition = (
        "launchd-user-eligible"
        if all(requirements.values())
        else "launchd-user-ineligible"
    )
    unsigned = {
        "schema_version": 1,
        "contract": "task-witness-macos-github-launchd-user-probe-v1",
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


def build_launchd_user_probe_error_document(
    candidate_sha: str,
    environment: Mapping[str, str],
    error_code: str,
    secondary_error_code: str | None = None,
) -> dict:
    if candidate_sha != EXPECTED_CANDIDATE_SHA:
        raise ProbeError("candidate-sha-disagrees")
    harness, runner = _normalized_context(environment)
    unsigned = {
        "schema_version": 1,
        "contract": "task-witness-macos-github-launchd-user-probe-v1",
        "claim": "host-prerequisite-probe-only",
        "candidate_sha1": candidate_sha,
        "harness": harness,
        "runner": runner,
        "observations": None,
        "requirements": None,
        "disposition": "probe-error",
        "error": _validated_probe_error(error_code, secondary_error_code),
    }
    return {
        **unsigned,
        "content_sha256": hashlib.sha256(canonical_bytes(unsigned)).hexdigest(),
    }


def build_launchd_user_plist(
    *,
    label: str,
    user: str,
    home: Path,
    helper: Path,
    candidate_sha: str,
    environment: Mapping[str, str],
    ownership_marker: str,
) -> dict:
    _normalized_context(environment)
    if (
        LAUNCHD_LABEL_RE.fullmatch(label) is None
        or environment.get("TASK_WITNESS_LAUNCHD_LABEL") != label
    ):
        raise ProbeError("invalid-launchd-label")
    if LAUNCHD_ACCOUNT_RE.fullmatch(user) is None:
        raise ProbeError("invalid-launchd-user")
    if (
        not home.is_absolute()
        or home != Path("/Users") / user
        or not helper.is_absolute()
        or helper.name == ""
    ):
        raise ProbeError("invalid-launchd-path")
    if candidate_sha != EXPECTED_CANDIDATE_SHA:
        raise ProbeError("candidate-sha-disagrees")
    if LAUNCHD_OWNERSHIP_MARKER_RE.fullmatch(ownership_marker) is None:
        raise ProbeError("invalid-launchd-ownership-marker")
    probe_root = home / "launchd-probe"
    child_environment = _expected_launchd_child_environment(
        environment,
        label=label,
        home=home,
        ownership_marker=ownership_marker,
    )
    return {
        "AbandonProcessGroup": False,
        "EnvironmentVariables": child_environment,
        "ExitTimeOut": 5,
        "InitGroups": True,
        "Label": label,
        "Program": "/usr/bin/env",
        "ProgramArguments": [
            "/usr/bin/env",
            "-i",
            "/usr/bin/python3",
            "-I",
            "-B",
            str(helper),
            "probe-launchd-user",
            "--candidate-sha",
            candidate_sha,
            "--output",
            str(probe_root / "probe.json"),
            "--status-output",
            str(probe_root / "probe.status"),
        ],
        "StandardErrorPath": str(probe_root / "probe.stderr"),
        "StandardOutPath": str(probe_root / "probe.stdout"),
        "Umask": 0o077,
        "UserName": user,
        "WorkingDirectory": str(home),
    }


def _expected_launchd_child_environment(
    environment: Mapping[str, str],
    *,
    label: str,
    home: Path,
    ownership_marker: str,
) -> dict[str, str]:
    _normalized_context(environment)
    if (
        LAUNCHD_LABEL_RE.fullmatch(label) is None
        or not home.is_absolute()
        or LAUNCHD_OWNERSHIP_MARKER_RE.fullmatch(ownership_marker) is None
    ):
        raise ProbeError("launchd-child-environment-invalid")
    expected = {name: environment[name] for name in LAUNCHD_CONTEXT_NAMES}
    expected.update(
        {
            "HOME": str(home),
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "TASK_WITNESS_LAUNCHD_LABEL": label,
            "TASK_WITNESS_LAUNCHD_OWNERSHIP_MARKER": ownership_marker,
            "TZ": "UTC",
        }
    )
    if any(
        not isinstance(value, str) or any(character in value for character in "\0\r\n")
        for value in expected.values()
    ):
        raise ProbeError("launchd-child-environment-invalid")
    return expected


def _require_exact_launchd_child_environment(
    environment: Mapping[str, str],
    *,
    label: str,
    home: Path,
    ownership_marker: str,
) -> None:
    expected = _expected_launchd_child_environment(
        environment,
        label=label,
        home=home,
        ownership_marker=ownership_marker,
    )
    if dict(environment) != expected:
        raise ProbeError("launchd-child-environment-invalid")


def _validated_launchd_child_environment_from_plist(
    raw: bytes,
    helper: Path,
) -> dict[str, str]:
    if (
        not isinstance(raw, bytes)
        or len(raw) > 64 * 1024
        or not helper.is_absolute()
        or helper.name != "helper.py"
    ):
        raise ProbeError("launchd-child-environment-invalid")
    try:
        value = plistlib.loads(raw)
    except (plistlib.InvalidFileException, ValueError, TypeError) as error:
        raise ProbeError("launchd-child-environment-invalid") from error
    if not isinstance(value, dict):
        raise ProbeError("launchd-child-environment-invalid")
    environment = value.get("EnvironmentVariables")
    if not isinstance(environment, dict) or any(
        not isinstance(name, str) or not isinstance(item, str)
        for name, item in environment.items()
    ):
        raise ProbeError("launchd-child-environment-invalid")
    try:
        account_name, label = _launchd_identity(environment)
        marker = _require_string(
            environment.get("TASK_WITNESS_LAUNCHD_OWNERSHIP_MARKER"),
            "launchd-ownership-marker",
            64,
        )
        home = Path("/Users") / account_name
        expected = build_launchd_user_plist(
            label=label,
            user=account_name,
            home=home,
            helper=helper,
            candidate_sha=EXPECTED_CANDIDATE_SHA,
            environment=environment,
            ownership_marker=marker,
        )
    except ProbeError as error:
        raise ProbeError("launchd-child-environment-invalid") from error
    if value != expected:
        raise ProbeError("launchd-child-environment-invalid")
    return dict(expected["EnvironmentVariables"])


def _load_staged_launchd_child_environment(helper: Path) -> dict[str, str]:
    stage_root = helper.parent
    plist = stage_root / "job.plist"
    if (
        not _directory_matches(stage_root, 0o755, 0, 0)
        or not _root_file_matches(helper, 0o555)
        or not _root_file_matches(plist, 0o644)
    ):
        raise ProbeError("launchd-child-environment-invalid")
    raw = _read_stable_regular_file(plist, 64 * 1024, "staged-plist")
    return _validated_launchd_child_environment_from_plist(raw, helper)


def _prepare_launchd_child_environment(helper: Path) -> None:
    environment = _load_staged_launchd_child_environment(helper)
    os.environ.clear()
    os.environ.update(environment)


def choose_disposable_uid(occupied: set[int]) -> int:
    if any(
        type(value) is not int or not DSCL_UID_MIN <= value <= DSCL_UID_MAX
        for value in occupied
    ):
        raise ProbeError("invalid-occupied-uids")
    for candidate in range(DISPOSABLE_UID_MIN, DISPOSABLE_UID_MAX + 1):
        if candidate not in occupied:
            return candidate
    raise ProbeError("uid-range-exhausted")


def require_exact_account_record(
    record: Mapping[str, Sequence[str]],
    expected: DisposableAccount,
) -> None:
    field_requirements = (
        (
            "AuthenticationAuthority",
            [";DisabledUser;"],
            "account-record-authentication-authority-missing",
            "account-record-authentication-authority-drift",
        ),
        (
            "GeneratedUID",
            None,
            "account-record-generated-uid-missing",
            "account-record-generated-uid-drift",
        ),
        (
            "IsHidden",
            ["1"],
            "account-record-hidden-missing",
            "account-record-hidden-drift",
        ),
        (
            "NFSHomeDirectory",
            [str(expected.home)],
            "account-record-home-missing",
            "account-record-home-drift",
        ),
        (
            "Password",
            None,
            "account-record-password-missing",
            "account-record-password-drift",
        ),
        (
            "PrimaryGroupID",
            [str(expected.gid)],
            "account-record-gid-missing",
            "account-record-gid-drift",
        ),
        (
            "UniqueID",
            [str(expected.uid)],
            "account-record-uid-missing",
            "account-record-uid-drift",
        ),
        (
            "UserShell",
            ["/usr/bin/false"],
            "account-record-shell-missing",
            "account-record-shell-drift",
        ),
    )
    expected_names = {name for name, *_rest in field_requirements}
    if set(record) - expected_names:
        raise ProbeError("account-record-fields-unexpected")
    for name, _values, missing_code, _drift_code in field_requirements:
        if name not in record:
            raise ProbeError(missing_code)
    try:
        _validated_system_generated_uid(record)
    except ProbeError as error:
        raise ProbeError("account-record-generated-uid-drift") from error
    for name, values, _missing_code, code in field_requirements:
        observed = list(record.get(name, ()))
        if name == "Password":
            if (
                len(observed) != 1
                or observed[0] not in DISABLED_PASSWORD_READBACK_MARKERS
            ):
                raise ProbeError(code)
        elif values is not None and observed != values:
            raise ProbeError(code)


def _validated_system_generated_uid(record: Mapping[str, Sequence[str]]) -> str:
    values = list(record.get("GeneratedUID", ()))
    if len(values) != 1 or not isinstance(values[0], str):
        raise ProbeError("account-record-drift")
    value = values[0]
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError) as error:
        raise ProbeError("account-record-drift") from error
    if parsed.int == 0 or str(parsed).upper() != value:
        raise ProbeError("account-record-drift")
    return value


def parse_dscl_record(raw: str) -> dict[str, list[str]]:
    if len(raw.encode("utf-8")) > MAX_COMMAND_OUTPUT_BYTES:
        raise ProbeError("account-record-too-large")
    record: dict[str, list[str]] = {}
    current: str | None = None
    for line in raw.splitlines():
        if line.startswith(" "):
            if current is None or not line.strip():
                raise ProbeError("account-record-invalid")
            record[current].append(line.strip())
            continue
        for prefix in DSCL_ATTRIBUTE_PREFIXES:
            if line.startswith(prefix):
                name, separator, value = line[len(prefix) :].partition(":")
                break
        else:
            name, separator, value = line.partition(":")
        if not separator or not name or name in record:
            raise ProbeError("account-record-invalid")
        current = name
        record[name] = value.strip().split() if value.strip() else []
    if not record:
        raise ProbeError("account-record-invalid")
    return record


def parse_launchctl_terminal(raw: str, *, expected_status: int) -> dict[str, object]:
    if len(raw.encode("utf-8")) > LAUNCHD_PRINT_MAX_BYTES:
        raise ProbeError("launchctl-print-too-large")
    if re.search(r"(?m)^\s*pid\s*=", raw):
        raise ProbeError("launchd-job-still-running")

    def exact_field(name: str) -> str:
        escaped = re.escape(name)
        top_level = re.findall(rf"(?m)^\t{escaped} = ([^\r\n]*)$", raw)
        if len(top_level) != 1:
            raise ProbeError("launchctl-terminal-invalid")
        return top_level[0]

    active_count = exact_field("active count")
    state = exact_field("state")
    runs_raw = exact_field("runs")
    exit_raw = exact_field("last exit code")
    if (
        active_count != "0"
        or state != "not running"
        or re.fullmatch(r"[0-9]+", runs_raw) is None
        or re.fullmatch(r"-?[0-9]+", exit_raw) is None
    ):
        raise ProbeError("launchctl-terminal-invalid")
    runs = int(runs_raw)
    last_exit_code = int(exit_raw)
    if runs != 1:
        raise ProbeError("launchd-job-respawned")
    if last_exit_code != expected_status:
        raise ProbeError("launchd-exit-status-disagrees")
    return {
        "last_exit_code": last_exit_code,
        "runs": runs,
        "state": state,
    }


def launchd_artifact_payloads(
    artifact_root: Path,
    *,
    include_manifest: bool,
) -> dict[str, bytes]:
    expected_names = set(LAUNCHD_ARTIFACT_FILES)
    if include_manifest:
        expected_names.add("SHA256SUMS")
    try:
        root_metadata = artifact_root.lstat()
        observed_names = {entry.name for entry in artifact_root.iterdir()}
    except OSError as error:
        raise ProbeError("launchd-artifact-root-unreadable") from error
    if not stat.S_ISDIR(root_metadata.st_mode) or artifact_root.is_symlink():
        raise ProbeError("launchd-artifact-root-unsafe")
    if observed_names != expected_names:
        raise ProbeError("launchd-artifact-file-set-disagrees")
    payloads = {
        name: _bounded_regular_file(
            artifact_root / name,
            maximum,
            name.replace(".", "-"),
        )
        for name, maximum in LAUNCHD_ARTIFACT_FILES.items()
    }
    if payloads["probe.status"] not in {b"0\n", b"1\n", b"2\n"}:
        raise ProbeError("probe-status-invalid")
    if payloads["lifecycle.status"] not in {b"0\n", b"1\n", b"2\n"}:
        raise ProbeError("lifecycle-status-invalid")
    if sum(len(raw) for raw in payloads.values()) > MAX_ARTIFACT_BYTES:
        raise ProbeError("launchd-artifact-too-large")
    return payloads


def remove_exact_disposable_home(
    home: Path,
    *,
    expected_uid: int,
    expected_gid: int,
) -> None:
    _validate_exact_disposable_home(
        home,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
        diagnostic_phase="home-removal",
    )
    probe_root = home / "launchd-probe"
    try:
        for name in sorted(LAUNCHD_CHILD_FILES):
            (probe_root / name).unlink()
        probe_root.rmdir()
        home.rmdir()
    except OSError as error:
        raise ProbeError("home-cleanup-failed") from error


def _require_disposable_directory_metadata(
    node: str,
    metadata: object,
    expected_uid: int,
    expected_gid: int,
    diagnostic_phase: str,
) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise _home_cleanup_drift_error(diagnostic_phase, f"{node}-kind-drift")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise _home_cleanup_drift_error(diagnostic_phase, f"{node}-mode-drift")
    if metadata.st_uid != expected_uid or metadata.st_gid != expected_gid:
        raise _home_cleanup_drift_error(diagnostic_phase, f"{node}-owner-drift")


def _validated_disposable_home_names(
    home: Path,
    home_metadata: object,
    expected_uid: int,
    expected_gid: int,
    diagnostic_phase: str,
) -> set[str]:
    names = set(
        _bounded_path_directory_names(
            home,
            home_metadata,
            MAX_DISPOSABLE_HOME_ENTRIES,
            limit_code=_home_cleanup_drift_error(
                diagnostic_phase,
                "home-entry-observation-unreadable",
            ).code,
            failure_code=_home_cleanup_drift_error(
                diagnostic_phase,
                "home-read-failed",
            ).code,
        )
    )
    if "launchd-probe" not in names:
        raise _home_cleanup_drift_error(diagnostic_phase, "probe-missing")
    _require_allowed_disposable_home_names(
        home,
        names,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
        diagnostic_phase=diagnostic_phase,
    )
    return names


def _validated_launchd_probe_children(
    probe_root: Path,
    probe_metadata: object,
    expected_uid: int,
    expected_gid: int,
    diagnostic_phase: str,
    *,
    allow_subset: bool,
) -> tuple[str, ...]:
    names = set(
        _bounded_path_directory_names(
            probe_root,
            probe_metadata,
            MAX_LAUNCHD_PROBE_ENTRIES,
            limit_code=_home_cleanup_drift_error(
                diagnostic_phase,
                "probe-entry-set-drift",
            ).code,
            failure_code=_home_cleanup_drift_error(
                diagnostic_phase,
                "probe-read-failed",
            ).code,
        )
    )
    expected = set(LAUNCHD_CHILD_FILES)
    if not (names <= expected if allow_subset else names == expected):
        raise _home_cleanup_drift_error(
            diagnostic_phase,
            "probe-entry-set-drift",
        )
    for name in sorted(names):
        try:
            metadata = (probe_root / name).lstat()
        except OSError as error:
            raise _home_cleanup_drift_error(
                diagnostic_phase,
                "probe-child-read-failed",
            ) from error
        if not stat.S_ISREG(metadata.st_mode):
            raise _home_cleanup_drift_error(
                diagnostic_phase,
                "probe-child-kind-drift",
            )
        if metadata.st_nlink != 1:
            raise _home_cleanup_drift_error(
                diagnostic_phase,
                "probe-child-link-count-drift",
            )
        if metadata.st_uid != expected_uid or metadata.st_gid != expected_gid:
            raise _home_cleanup_drift_error(
                diagnostic_phase,
                "probe-child-owner-drift",
            )
        if metadata.st_size > LAUNCHD_CHILD_FILES[name]:
            raise _home_cleanup_drift_error(
                diagnostic_phase,
                "probe-child-size-drift",
            )
    return tuple(sorted(names))


def _disposable_home_root(home: Path, uid: int, gid: int, phase: str):
    probe_root = home / "launchd-probe"
    try:
        home_metadata = home.lstat()
    except OSError as error:
        raise _home_cleanup_drift_error(phase, "home-read-failed") from error
    _require_disposable_directory_metadata("home", home_metadata, uid, gid, phase)
    _validated_disposable_home_names(home, home_metadata, uid, gid, phase)
    try:
        probe_metadata = probe_root.lstat()
    except FileNotFoundError as error:
        raise _home_cleanup_drift_error(phase, "probe-missing") from error
    except OSError as error:
        raise _home_cleanup_drift_error(phase, "probe-read-failed") from error
    _require_disposable_directory_metadata("probe", probe_metadata, uid, gid, phase)
    return probe_root, probe_metadata


def _validate_disposable_home_root(
    account: DisposableAccount,
    *,
    diagnostic_phase: str,
) -> None:
    _disposable_home_root(account.home, account.uid, account.gid, diagnostic_phase)


def _validate_exact_disposable_home(
    home: Path,
    *,
    expected_uid: int,
    expected_gid: int,
    diagnostic_phase: str,
) -> None:
    probe_root, probe_metadata = _disposable_home_root(
        home,
        expected_uid,
        expected_gid,
        diagnostic_phase,
    )
    _validated_launchd_probe_children(
        probe_root,
        probe_metadata,
        expected_uid,
        expected_gid,
        diagnostic_phase,
        allow_subset=False,
    )


def _validated_marker_bound_disposable_home(
    account: DisposableAccount,
    expected_identity: object,
    *,
    diagnostic_phase: str,
    library_inventory: object = None,
) -> tuple[bool, bool, tuple[str, ...]]:
    identity = _validated_disposable_home_identity(expected_identity)
    home = account.home
    probe_root = home / "launchd-probe"
    try:
        home_metadata = home.lstat()
    except FileNotFoundError:
        return False, False, ()
    except OSError as error:
        raise _home_cleanup_drift_error(
            diagnostic_phase,
            "home-read-failed",
        ) from error
    _require_disposable_directory_metadata(
        "home", home_metadata, account.uid, account.gid, diagnostic_phase
    )
    if (home_metadata.st_dev, home_metadata.st_ino) != (
        identity["home_device"],
        identity["home_inode"],
    ):
        raise _home_cleanup_drift_error(diagnostic_phase, "home-identity-drift")
    home_names = set(
        _bounded_path_directory_names(
            home,
            home_metadata,
            MAX_DISPOSABLE_HOME_ENTRIES,
            limit_code=(
                "home-library-inventory-drift"
                if library_inventory is not None
                else _home_cleanup_drift_error(
                    diagnostic_phase,
                    "home-entry-observation-unreadable",
                ).code
            ),
            failure_code=_home_cleanup_drift_error(
                diagnostic_phase,
                "home-read-failed",
            ).code,
        )
    )

    def raise_unexpected_entries() -> None:
        raise _home_cleanup_drift_error(
            diagnostic_phase,
            _classified_unexpected_home_entry_detail(
                home,
                home_names,
                expected_uid=account.uid,
                expected_gid=account.gid,
            ),
        )

    library_names = home_names & {
        HOME_LIBRARY_NAME,
        HOME_LIBRARY_QUARANTINE_NAME,
    }
    unexpected_names = home_names - {"launchd-probe"} - library_names
    if unexpected_names or len(library_names) > 1:
        if library_inventory is not None:
            raise ProbeError("home-library-inventory-drift")
        raise_unexpected_entries()
    if library_inventory is None:
        if library_names != ({HOME_LIBRARY_NAME} if library_names else set()):
            raise ProbeError("home-library-inventory-drift")
        if library_names:
            _require_allowed_disposable_home_names(
                home,
                {"launchd-probe", HOME_LIBRARY_NAME},
                expected_uid=account.uid,
                expected_gid=account.gid,
                diagnostic_phase=diagnostic_phase,
            )
    elif library_inventory == "none":
        if library_names:
            raise ProbeError("home-library-inventory-drift")
    else:
        inventory = _validated_library_inventory(
            library_inventory,
            account,
            identity["home_device"],
        )
        if library_names:
            leaf_name = next(iter(library_names))
            try:
                metadata = (home / leaf_name).lstat()
            except OSError as error:
                raise ProbeError("home-library-inventory-drift") from error
            root_sha256 = _home_library_path_sha256((HOME_LIBRARY_NAME,))
            root = next(
                (
                    item
                    for item in inventory["entries"]
                    if item.get("path_sha256") == root_sha256
                ),
                None,
            )
            if not isinstance(root, dict) or any(
                root.get(name) != value
                for name, value in _library_record_metadata(
                    metadata, "directory"
                ).items()
            ):
                raise ProbeError("home-library-inventory-drift")
    try:
        probe_metadata = probe_root.lstat()
    except FileNotFoundError:
        if home_names:
            raise_unexpected_entries()
        return True, False, ()
    except OSError as error:
        raise _home_cleanup_drift_error(
            diagnostic_phase,
            "probe-read-failed",
        ) from error
    if home_names != {"launchd-probe"} | library_names:
        if library_inventory is not None:
            raise ProbeError("home-library-inventory-drift")
        raise_unexpected_entries()
    _require_disposable_directory_metadata(
        "probe", probe_metadata, account.uid, account.gid, diagnostic_phase
    )
    if (probe_metadata.st_dev, probe_metadata.st_ino) != (
        identity["probe_device"],
        identity["probe_inode"],
    ):
        raise _home_cleanup_drift_error(diagnostic_phase, "probe-identity-drift")
    names = _validated_launchd_probe_children(
        probe_root,
        probe_metadata,
        account.uid,
        account.gid,
        diagnostic_phase,
        allow_subset=True,
    )
    return True, True, names


def _remove_marker_bound_disposable_home(
    account: DisposableAccount,
    expected_identity: object,
    authorization: Mapping[str, object] | None,
) -> None:
    identity = _validated_disposable_home_identity(expected_identity)
    if (
        authorization is None
        or authorization.get("disposition") != "armed"
        or authorization.get("account")
        != {
            "name": account.name,
            "uid": account.uid,
            "gid": account.gid,
            "home": str(account.home),
        }
        or authorization.get("home_identity") != identity
    ):
        raise ProbeError("home-cleanup-authorization-drift")
    home_present, probe_present, names = _validated_marker_bound_disposable_home(
        account,
        identity,
        diagnostic_phase="home-removal",
        library_inventory=authorization.get("library_inventory"),
    )
    if not home_present:
        return
    _quarantine_and_remove_bounded_library(account, authorization)
    home_present, probe_present, names = _validated_marker_bound_disposable_home(
        account,
        identity,
        diagnostic_phase="home-removal",
        library_inventory=authorization.get("library_inventory"),
    )
    if not home_present:
        return
    _require_bound_home_path(account, identity)
    probe_root = account.home / "launchd-probe"
    try:
        for name in names:
            (probe_root / name).unlink()
        if probe_present:
            probe_root.rmdir()
        account.home.rmdir()
    except OSError as error:
        raise ProbeError("home-cleanup-failed") from error


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


def _passwordless_sudo_available() -> bool:
    try:
        process = subprocess.run(
            ["/usr/bin/sudo", "-n", "/usr/bin/true"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=COMMAND_TIMEOUT_SECONDS,
            env={
                "HOME": "/var/empty",
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "TZ": "UTC",
            },
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProbeError("passwordless-sudo-probe-failed") from error
    try:
        returncode = process.returncode
    except AttributeError as error:
        raise ProbeError("passwordless-sudo-probe-failed") from error
    if type(returncode) is not int or not 0 <= returncode <= 255:
        raise ProbeError("passwordless-sudo-probe-failed")
    return returncode == 0


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


def collect_observations(*, include_provisioning: bool = True) -> dict:
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
    passwordless_sudo = (
        _passwordless_sudo_available() if include_provisioning else False
    )
    observations = {
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
    }
    if include_provisioning:
        observations["provisioning_capability"] = {
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
        }
    return observations


def _darwin_process_path(pid: int, system: str) -> str:
    if system != "darwin" or pid < 0:
        raise ProbeError("launchd-parent-unavailable")
    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        proc_pidpath = library.proc_pidpath
        proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        proc_pidpath.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(4096)
        length = proc_pidpath(pid, buffer, len(buffer))
        if length <= 0 or length >= len(buffer):
            raise ProbeError("launchd-parent-unavailable")
        raw = buffer.value
        if len(raw) not in {length, length - 1}:
            raise ProbeError("launchd-parent-unavailable")
        return raw.decode("utf-8", "strict")
    except (AttributeError, OSError, UnicodeDecodeError) as error:
        raise ProbeError("launchd-parent-unavailable") from error


def collect_launchd_observations() -> dict:
    observations = collect_observations(include_provisioning=False)
    system = observations["platform"]["system"]
    parent_pid = os.getppid()
    label = _require_string(
        os.environ.get("TASK_WITNESS_LAUNCHD_LABEL"),
        "launchd-label",
        256,
    )
    ownership_marker = _require_string(
        os.environ.get("TASK_WITNESS_LAUNCHD_OWNERSHIP_MARKER"),
        "launchd-ownership-marker",
        64,
    )
    _require_exact_launchd_child_environment(
        os.environ,
        label=label,
        home=Path(observations["home"]["path"]),
        ownership_marker=ownership_marker,
    )
    observations["environment_exact"] = True
    observations["passwordless_sudo"] = _passwordless_sudo_available()
    observations["process"] = {
        "pid": os.getpid(),
        "ppid": parent_pid,
        "parent_path": _darwin_process_path(parent_pid, system),
        "label": label,
    }
    return observations


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


def _rename_exclusive(source: Path, destination: Path) -> None:
    try:
        library = ctypes.CDLL(None, use_errno=True)
        renamex_np = library.renamex_np
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
    except (AttributeError, OSError) as error:
        raise OSError(errno.ENOTSUP, "exclusive rename unavailable") from error
    if renamex_np(os.fsencode(source), os.fsencode(destination), RENAME_EXCL) != 0:
        error_number = ctypes.get_errno()
        raise OSError(
            error_number,
            os.strerror(error_number),
            str(destination),
        )


def _renameat_exclusive(
    source_parent_descriptor: int,
    source_name: str,
    destination_parent_descriptor: int,
    destination_name: str,
) -> None:
    if (
        source_name != HOME_LIBRARY_NAME
        or destination_name != HOME_LIBRARY_QUARANTINE_NAME
    ):
        raise ProbeError("home-library-quarantine-drift")
    try:
        library = ctypes.CDLL(None, use_errno=True)
        renameatx_np = library.renameatx_np
        renameatx_np.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameatx_np.restype = ctypes.c_int
        result = renameatx_np(
            source_parent_descriptor,
            os.fsencode(source_name),
            destination_parent_descriptor,
            os.fsencode(destination_name),
            RENAME_EXCL | RENAME_NOFOLLOW_ANY,
        )
    except (AttributeError, OSError) as error:
        raise ProbeError("home-library-quarantine-failed") from error
    if result != 0:
        error_number = ctypes.get_errno()
        raise ProbeError("home-library-quarantine-failed") from OSError(
            error_number,
            os.strerror(error_number),
        )


def _write_stage_create_new(path: Path, raw: bytes, mode: int) -> None:
    temporary_descriptor = -1
    temporary_path = ""
    try:
        # Keep an interrupted pre-publication file outside the stage's exact-name
        # lattice, then publish one single-link name without replacing a peer.
        temporary_descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{path.parent.name}-{path.name}-",
            dir=path.parent.parent,
        )
        os.fchmod(temporary_descriptor, mode)
        offset = 0
        while offset < len(raw):
            offset += os.write(temporary_descriptor, raw[offset:])
        os.fsync(temporary_descriptor)
        os.close(temporary_descriptor)
        temporary_descriptor = -1
        _rename_exclusive(Path(temporary_path), path)
        temporary_path = ""
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


def write_launchd_artifact_manifest(artifact_root: Path, output: Path) -> None:
    payloads = launchd_artifact_payloads(artifact_root, include_manifest=False)
    write_create_new(output, _manifest_bytes(payloads))


def verify_launchd_artifact_manifest(artifact_root: Path, manifest: Path) -> None:
    payloads = launchd_artifact_payloads(artifact_root, include_manifest=True)
    observed = _bounded_regular_file(manifest, MAX_MANIFEST_BYTES, "manifest")
    if observed != _manifest_bytes(payloads):
        raise ProbeError("launchd-artifact-manifest-disagrees")


def _load_canonical_document(path: Path, maximum: int, label: str) -> dict:
    raw = _bounded_regular_file(path, maximum, label)
    try:
        value = json.loads(raw.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProbeError(f"invalid-{label}") from error
    if not isinstance(value, dict) or canonical_bytes(value) != raw:
        raise ProbeError(f"noncanonical-{label}")
    return value


def _require_content_digest(document: Mapping[str, object], label: str) -> None:
    recorded = document.get("content_sha256")
    if not _is_sha256(recorded):
        raise ProbeError(f"invalid-{label}-digest")
    unsigned = {
        key: value for key, value in document.items() if key != "content_sha256"
    }
    if hashlib.sha256(canonical_bytes(unsigned)).hexdigest() != recorded:
        raise ProbeError(f"{label}-digest-disagrees")


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def verify_provisioning_capability(artifact_root: Path) -> None:
    verify_artifact_manifest(artifact_root, artifact_root / "SHA256SUMS")
    payloads = _artifact_payloads(artifact_root, include_manifest=True)
    if payloads["probe.status"] not in {b"0\n", b"1\n"}:
        raise ProbeError("provisioner-probe-failed")
    document = _load_canonical_document(
        artifact_root / "probe.json",
        MAX_PROBE_JSON_BYTES,
        "provisioner-probe",
    )
    _require_content_digest(document, "provisioner-probe")
    if (
        document.get("contract") != "task-witness-macos-github-host-probe-v1"
        or document.get("candidate_sha1") != EXPECTED_CANDIDATE_SHA
        or document.get("claim") != "host-prerequisite-probe-only"
    ):
        raise ProbeError("provisioner-probe-contract-disagrees")
    requirements = document.get("requirements")
    observations = document.get("observations")
    if not isinstance(requirements, dict) or not isinstance(observations, dict):
        raise ProbeError("provisioner-probe-unavailable")
    expected_document = build_probe_document(
        EXPECTED_CANDIDATE_SHA,
        os.environ,
        observations,
    )
    expected_status = (
        b"0\n"
        if expected_document["disposition"] == "direct-session-eligible"
        else b"1\n"
    )
    if document != expected_document or payloads["probe.status"] != expected_status:
        raise ProbeError("provisioner-probe-contract-disagrees")
    required = {
        "credentials_equal",
        "github_hosted",
        "group_views_agree",
        "home_apfs",
        "home_directory",
        "home_owned_by_effective_uid",
        "home_symlink_free",
        "issetugid_false",
        "native_darwin_arm64",
        "no_container_indicators",
        "nonroot_uid",
        "not_translated",
        "passwd_backed_identity",
        "runner_arch_arm64",
        "runner_os_macos",
    }
    if any(requirements.get(name) is not True for name in required):
        raise ProbeError("provisioner-host-ineligible")
    capability = observations.get("provisioning_capability")
    if (
        not isinstance(capability, dict)
        or capability.get("passwordless_sudo") is not True
    ):
        raise ProbeError("provisioner-sudo-unavailable")
    tools = capability.get("tools")
    if (
        not isinstance(tools, list)
        or len(tools) != len(PROVISIONING_TOOLS)
        or any(
            not isinstance(item, dict)
            or item.get("id") != expected_id
            or item.get("path") != expected_path
            or item.get("available") is not True
            for item, (expected_id, expected_path) in zip(
                tools,
                PROVISIONING_TOOLS,
            )
        )
    ):
        raise ProbeError("provisioner-tools-unavailable")


def verify_launchd_success(artifact_root: Path) -> None:
    verify_launchd_artifact_manifest(artifact_root, artifact_root / "SHA256SUMS")
    payloads = launchd_artifact_payloads(artifact_root, include_manifest=True)
    if payloads["probe.status"] != b"0\n" or payloads["lifecycle.status"] != b"0\n":
        raise ProbeError("launchd-user-probe-failed")
    probe = _load_canonical_document(
        artifact_root / "probe.json",
        MAX_PROBE_JSON_BYTES,
        "launchd-user-probe",
    )
    lifecycle = _load_canonical_document(
        artifact_root / "lifecycle.json",
        LAUNCHD_ARTIFACT_FILES["lifecycle.json"],
        "launchd-lifecycle",
    )
    cleanup = _load_canonical_document(
        artifact_root / "cleanup.json",
        LAUNCHD_ARTIFACT_FILES["cleanup.json"],
        "launchd-cleanup",
    )
    _require_content_digest(probe, "launchd-user-probe")
    _require_content_digest(lifecycle, "launchd-lifecycle")
    _require_content_digest(cleanup, "launchd-cleanup")
    observations = probe.get("observations")
    if not isinstance(observations, dict):
        raise ProbeError("launchd-user-probe-ineligible")
    validated = _validated_launchd_observations(observations)
    harness, _runner = _normalized_context(os.environ)
    account_name, label = _launchd_identity(os.environ)
    expected_home = Path("/Users") / account_name
    credentials = validated["credentials"]
    passwd = credentials["passwd"]
    if (
        validated["process"]["label"] != label
        or passwd["name"] != account_name
        or passwd["home"] != str(expected_home)
        or validated["home"]["path"] != str(expected_home)
        or not DISPOSABLE_UID_MIN <= credentials["effective_uid"] <= DISPOSABLE_UID_MAX
    ):
        raise ProbeError("launchd-user-probe-ineligible")
    expected_probe = build_launchd_user_probe_document(
        EXPECTED_CANDIDATE_SHA,
        {**os.environ, "TASK_WITNESS_LAUNCHD_LABEL": label},
        validated,
    )
    requirements = expected_probe.get("requirements")
    if (
        expected_probe.get("disposition") != "launchd-user-eligible"
        or not isinstance(requirements, dict)
        or any(value is not True for value in requirements.values())
    ):
        raise ProbeError("launchd-user-probe-ineligible")
    binding = _validated_launchd_binding_evidence(lifecycle.get("binding"))
    domain_reset = _validated_domain_reset_evidence(lifecycle.get("domain_reset"))
    home_cleanup = _validated_home_cleanup_evidence(cleanup.get("home_cleanup"))
    if (
        domain_reset["disposition"] == "recovered-to-stable-zero"
        or _validated_domain_reset_evidence(cleanup.get("domain_reset")) != domain_reset
        or home_cleanup["disposition"] != "performed"
    ):
        raise ProbeError("launchd-user-probe-ineligible")
    expected_lifecycle = _document_with_digest(
        {
            "schema_version": 2,
            "contract": "task-witness-macos-launchd-lifecycle-v2",
            "candidate_sha1": EXPECTED_CANDIDATE_SHA,
            "label": label,
            "kickstart_pid": validated["process"]["pid"],
            "probe_disposition": "launchd-user-eligible",
            "disposition": "launchd-user-eligible",
            "binding": binding,
            "domain_reset": domain_reset,
        }
    )
    expected_cleanup = _document_with_digest(
        {
            "schema_version": 3,
            "contract": "task-witness-macos-launchd-cleanup-v3",
            "candidate_sha1": EXPECTED_CANDIDATE_SHA,
            "account": account_name,
            "label": label,
            "disposition": "cleaned",
            "domain_reset": domain_reset,
            "home_cleanup": home_cleanup,
        }
    )
    if (
        probe != expected_probe
        or lifecycle != expected_lifecycle
        or cleanup != expected_cleanup
    ):
        raise ProbeError("launchd-user-probe-ineligible")
    if not payloads["launchd.loaded"]:
        raise ProbeError("launchd-loaded-evidence-missing")
    try:
        loaded = payloads["launchd.loaded"].decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise ProbeError("launchd-job-binding-invalid") from error
    expected_stage_root = Path(
        "/private/var/tmp/"
        f"task-witness-macos-launchd-{harness['run_id']}-{harness['run_attempt']}"
    )
    account = DisposableAccount(
        name=account_name,
        uid=credentials["effective_uid"],
        gid=credentials["effective_gid"],
        home=expected_home,
    )
    expected_plan = LaunchdPlan(
        account=account,
        label=label,
        stage_root=expected_stage_root,
        helper=expected_stage_root / "helper.py",
        plist=expected_stage_root / "job.plist",
    )
    expected_state = {"ownership_marker": binding["ownership_marker"]}
    loaded_snapshot = _validated_launchd_job_snapshot(
        loaded,
        expected_plan,
        expected_state,
    )
    if loaded_snapshot.binding != binding or loaded_snapshot.sanitized != loaded:
        raise ProbeError("launchd-job-binding-invalid")
    try:
        terminal = payloads["launchd.terminal"].decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise ProbeError("launchd-terminal-invalid") from error
    terminal_snapshot = _validated_launchd_job_snapshot(
        terminal,
        expected_plan,
        expected_state,
    )
    if terminal_snapshot.binding != binding or terminal_snapshot.sanitized != terminal:
        raise ProbeError("launchd-job-binding-invalid")
    parse_launchctl_terminal(terminal_snapshot.sanitized, expected_status=0)


def _document_with_digest(unsigned: Mapping[str, object]) -> dict:
    document = dict(unsigned)
    document["content_sha256"] = hashlib.sha256(canonical_bytes(unsigned)).hexdigest()
    return document


def _run_lifecycle_command(
    argv: Sequence[str],
    *,
    maximum: int = MAX_COMMAND_OUTPUT_BYTES,
    timeout: float = COMMAND_TIMEOUT_SECONDS,
) -> tuple[int, str, str]:
    if not argv or any(not isinstance(item, str) or not item for item in argv):
        raise ProbeError("invalid-lifecycle-command")
    try:
        process = subprocess.run(
            list(argv),
            check=False,
            capture_output=True,
            timeout=timeout,
            env={
                "HOME": "/var/empty",
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "TZ": "UTC",
            },
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProbeError("lifecycle-command-failed") from error
    if len(process.stdout) > maximum or len(process.stderr) > maximum:
        raise ProbeError("lifecycle-command-output-too-large")
    try:
        return (
            process.returncode,
            process.stdout.decode("utf-8", "strict").strip(),
            process.stderr.decode("utf-8", "strict").strip(),
        )
    except UnicodeDecodeError as error:
        raise ProbeError("lifecycle-command-output-invalid") from error


def _run_user_domain_reset(uid: int) -> None:
    if type(uid) is not int or not DISPOSABLE_UID_MIN <= uid <= DISPOSABLE_UID_MAX:
        raise ProbeError("invalid-user-domain-reset-uid")
    argv = ["/bin/launchctl", "bootout", f"user/{uid}"]
    try:
        process = subprocess.run(
            argv,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=COMMAND_TIMEOUT_SECONDS,
            env={
                "HOME": "/var/empty",
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "TZ": "UTC",
            },
        )
    except subprocess.TimeoutExpired as error:
        raise ProbeError("launchd-user-domain-bootout-timeout") from error
    except OSError as error:
        raise ProbeError("launchd-user-domain-bootout-failed") from error
    status = process.returncode
    if type(status) is not int or status < 0 or status > 255:
        raise ProbeError("launchd-user-domain-bootout-status-invalid")
    if status != 0:
        raise ProbeError("launchd-user-domain-bootout-nonzero")


def _require_command_success(
    argv: Sequence[str],
    *,
    command_id: str,
    maximum: int = MAX_COMMAND_OUTPUT_BYTES,
    timeout: float = COMMAND_TIMEOUT_SECONDS,
) -> str:
    if command_id not in LIFECYCLE_COMMAND_IDS:
        raise ProbeError("invalid-lifecycle-command-id")
    try:
        status, stdout, _stderr = _run_lifecycle_command(
            argv,
            maximum=maximum,
            timeout=timeout,
        )
    except ProbeError as error:
        if error.code not in {
            "lifecycle-command-failed",
            "lifecycle-command-output-invalid",
            "lifecycle-command-output-too-large",
        }:
            raise
        raise ProbeError(f"{error.code}-{command_id}") from error
    if status != 0:
        raise ProbeError(f"lifecycle-command-nonzero-{command_id}")
    return stdout


def parse_dscl_uid_list(raw: str) -> dict[str, int]:
    if len(raw.encode("utf-8")) > MAX_COMMAND_OUTPUT_BYTES:
        raise ProbeError("account-list-too-large")
    result: dict[str, int] = {}
    occupied: set[int] = set()
    for line in raw.splitlines():
        fields = line.split()
        if (
            len(fields) != 2
            or not fields[0]
            or fields[0] in result
            or DSCL_UID_RE.fullmatch(fields[1]) is None
        ):
            raise ProbeError("account-list-invalid")
        uid = int(fields[1])
        if not DSCL_UID_MIN <= uid <= DSCL_UID_MAX or uid in occupied:
            raise ProbeError("account-list-invalid")
        result[fields[0]] = uid
        occupied.add(uid)
    if not result:
        raise ProbeError("account-list-invalid")
    return result


def _stable_regular_file_metadata(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_mode),
        int(metadata.st_nlink),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(metadata.st_uid),
        int(metadata.st_gid),
    )


def _read_stable_regular_file(
    path: Path,
    maximum: int,
    label: str,
    *,
    expected_metadata: tuple[int, ...] | None = None,
) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size < 0
            or before.st_size > maximum
        ):
            raise ProbeError(f"unsafe-{label}")
        if (
            expected_metadata is not None
            and _stable_regular_file_metadata(before) != expected_metadata
        ):
            raise ProbeError(f"changed-{label}")
        chunks = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise ProbeError(f"changed-{label}")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ProbeError(f"changed-{label}")
        after = os.fstat(descriptor)
        current = path.lstat()
    except OSError as error:
        raise ProbeError(f"unreadable-{label}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)
        or (
            expected_metadata is not None
            and (
                _stable_regular_file_metadata(after) != expected_metadata
                or _stable_regular_file_metadata(current) != expected_metadata
            )
        )
    ):
        raise ProbeError(f"changed-{label}")
    return b"".join(chunks)


def _launchd_identity(environment: Mapping[str, str]) -> tuple[str, str]:
    harness, _runner = _normalized_context(environment)
    seed = (
        f"{harness['commit_sha1']}:{harness['run_id']}:{harness['run_attempt']}"
    ).encode("ascii")
    token = hashlib.sha256(seed).hexdigest()[:12]
    return f"twq-{token}", f"io.nisavid.task-witness.macos-probe.{token}"


def _validate_lifecycle_arguments(
    *,
    source_helper: Path,
    stage_root: Path,
    artifact_root: Path,
    runner_uid: int,
    runner_gid: int,
) -> None:
    if (
        os.geteuid() != 0
        or type(runner_uid) is not int
        or runner_uid <= 0
        or type(runner_gid) is not int
        or runner_gid < 0
        or not source_helper.is_absolute()
        or source_helper.name != Path(__file__).name
        or LAUNCHD_STAGE_RE.fullmatch(str(stage_root)) is None
        or not artifact_root.is_absolute()
        or artifact_root.name != "task-witness-macos-launchd-user-probe"
        or stage_root == artifact_root
    ):
        raise ProbeError("invalid-lifecycle-arguments")


def _require_nonroot_runner_gid(runner_gid: int) -> None:
    if type(runner_gid) is not int or runner_gid <= 0:
        raise ProbeError("invalid-lifecycle-arguments")


def _metadata_matches(
    path: Path,
    *,
    kind: str,
    mode: int,
    uid: int,
    gid: int,
    nlink: int | None = None,
) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    expected_kind = stat.S_ISDIR if kind == "directory" else stat.S_ISREG
    return (
        expected_kind(metadata.st_mode)
        and stat.S_IMODE(metadata.st_mode) == mode
        and metadata.st_uid == uid
        and metadata.st_gid == gid
        and (nlink is None or metadata.st_nlink == nlink)
    )


def _root_file_matches(path: Path, mode: int) -> bool:
    return _metadata_matches(
        path,
        kind="file",
        mode=mode,
        uid=0,
        gid=0,
        nlink=1,
    )


def _directory_matches(path: Path, mode: int, uid: int, gid: int) -> bool:
    return _metadata_matches(
        path,
        kind="directory",
        mode=mode,
        uid=uid,
        gid=gid,
    )


def _load_root_stage_document(
    path: Path,
    maximum: int,
    label: str,
) -> dict | None:
    if not _path_exists_no_follow(path):
        return None
    if not _root_file_matches(path, 0o600):
        raise ProbeError(f"{label}-drift")
    document = _load_canonical_document(path, maximum, label)
    _require_content_digest(document, label)
    return document


def _path_exists_no_follow(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise ProbeError("path-observation-failed") from error
    return True


def _same_directory_identity(
    metadata: os.stat_result,
    identity: tuple[int, int],
) -> bool:
    return (
        stat.S_ISDIR(metadata.st_mode)
        and (
            metadata.st_dev,
            metadata.st_ino,
        )
        == identity
    )


def _rollback_created_root_directory(
    *,
    parent_descriptor: int,
    directory_descriptor: int,
    name: str,
    created_metadata: os.stat_result | None,
    requested_mode: int,
) -> bool:
    if directory_descriptor < 0 or created_metadata is None:
        return False
    identity = (created_metadata.st_dev, created_metadata.st_ino)

    def removable(metadata: os.stat_result) -> bool:
        return (
            _same_directory_identity(metadata, identity)
            and metadata.st_uid == 0
            and metadata.st_gid in {created_metadata.st_gid, 0}
            and stat.S_IMODE(metadata.st_mode)
            in {stat.S_IMODE(created_metadata.st_mode), requested_mode}
        )

    try:
        if not removable(os.fstat(directory_descriptor)) or not removable(
            os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        ):
            return False
        if os.listdir(directory_descriptor):
            return False
        if not removable(os.fstat(directory_descriptor)) or not removable(
            os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        ):
            return False
        os.rmdir(name, dir_fd=parent_descriptor)
    except OSError:
        return False
    return True


def _create_root_directory(path: Path, mode: int) -> None:
    parent_descriptor = -1
    directory_descriptor = -1
    created = False
    created_metadata: os.stat_result | None = None
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        parent_descriptor = os.open(path.parent, flags)
        os.mkdir(path.name, mode, dir_fd=parent_descriptor)
        created = True
        directory_descriptor = os.open(
            path.name,
            flags,
            dir_fd=parent_descriptor,
        )
        created_metadata = os.fstat(directory_descriptor)
        if not stat.S_ISDIR(created_metadata.st_mode) or created_metadata.st_uid != 0:
            raise ProbeError("directory-create-new-disagrees")
        identity = (created_metadata.st_dev, created_metadata.st_ino)
        os.fchown(directory_descriptor, 0, 0)
        os.fchmod(directory_descriptor, mode)
        normalized = os.fstat(directory_descriptor)
        visible = os.stat(
            path.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            not _same_directory_identity(normalized, identity)
            or not _same_directory_identity(visible, identity)
            or stat.S_IMODE(normalized.st_mode) != mode
            or normalized.st_uid != 0
            or normalized.st_gid != 0
            or (
                visible.st_mode,
                visible.st_uid,
                visible.st_gid,
            )
            != (
                normalized.st_mode,
                normalized.st_uid,
                normalized.st_gid,
            )
        ):
            raise ProbeError("directory-create-new-disagrees")
    except (OSError, ProbeError) as error:
        if created and not _rollback_created_root_directory(
            parent_descriptor=parent_descriptor,
            directory_descriptor=directory_descriptor,
            name=path.name,
            created_metadata=created_metadata,
            requested_mode=mode,
        ):
            raise ProbeError("directory-create-new-preserved") from error
        if isinstance(error, ProbeError):
            raise
        raise ProbeError("directory-create-new-failed") from error
    finally:
        if directory_descriptor >= 0:
            os.close(directory_descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _account_record(account: DisposableAccount) -> dict[str, list[str]]:
    attributes = (
        "AuthenticationAuthority",
        "GeneratedUID",
        "IsHidden",
        "NFSHomeDirectory",
        "Password",
        "PrimaryGroupID",
        "UniqueID",
        "UserShell",
    )
    raw = _require_command_success(
        ["/usr/bin/dscl", ".", "-read", f"/Users/{account.name}", *attributes],
        command_id="account-record-read",
    )
    record = parse_dscl_record(raw)
    require_exact_account_record(record, account)
    return record


def _read_system_generated_uid(name: str) -> str:
    raw = _require_command_success(
        ["/usr/bin/dscl", ".", "-read", f"/Users/{name}", "GeneratedUID"],
        command_id="account-generated-uid-read",
    )
    try:
        record = parse_dscl_record(raw)
        if set(record) != {"GeneratedUID"}:
            raise ProbeError("account-record-drift")
        return _validated_system_generated_uid(record)
    except ProbeError as error:
        raise ProbeError("account-record-generated-uid-drift") from error


def _list_accounts() -> dict[str, int]:
    return parse_dscl_uid_list(
        _require_command_success(
            ["/usr/bin/dscl", ".", "-list", "/Users", "UniqueID"],
            command_id="account-uid-list",
        )
    )


def _account_exists(name: str) -> bool:
    if LAUNCHD_ACCOUNT_RE.fullmatch(name) is None:
        raise ProbeError("invalid-launchd-user")
    raw = _require_command_success(
        ["/usr/bin/dscl", ".", "-list", "/Users"],
        command_id="account-name-list",
    )
    names = raw.splitlines()
    if (
        not names
        or names != sorted(set(names))
        or any(
            not item or item.strip() != item or item.split() != [item] for item in names
        )
    ):
        raise ProbeError("account-name-list-invalid")
    return name in names


def _rollback_disposable_account_creation(
    account: DisposableAccount,
    generated_uid: str,
) -> None:
    if not _account_exists(account.name):
        return
    if _read_system_generated_uid(account.name) != generated_uid:
        raise ProbeError("account-record-generated-uid-drift")
    _require_command_success(
        ["/usr/bin/dscl", ".", "-delete", f"/Users/{account.name}"],
        command_id="account-delete",
    )
    if _account_exists(account.name):
        raise ProbeError("account-create-rollback-disagrees")


def _create_disposable_account(
    plan: LaunchdPlan,
    state: Mapping[str, object],
) -> None:
    account = plan.account
    accounts = _list_accounts()
    if (
        _account_exists(account.name)
        or account.name in accounts
        or account.uid in accounts.values()
    ):
        raise ProbeError("account-or-uid-already-exists")
    _require_disposable_uid_available(account.uid)
    record_path = f"/Users/{account.name}"
    property_command_ids = (
        "account-set-shell",
        "account-set-authentication-authority",
        "account-set-password",
        "account-set-hidden",
        "account-set-uid",
        "account-set-gid",
        "account-set-home",
    )
    property_commands = [
        [
            "/usr/bin/dscl",
            ".",
            "-create",
            record_path,
            "UserShell",
            "/usr/bin/false",
        ],
        [
            "/usr/bin/dscl",
            ".",
            "-create",
            record_path,
            "AuthenticationAuthority",
            ";DisabledUser;",
        ],
        [
            "/usr/bin/dscl",
            ".",
            "-create",
            record_path,
            "Password",
            DISABLED_PASSWORD_WRITE_MARKER,
        ],
        ["/usr/bin/dscl", ".", "-create", record_path, "IsHidden", "1"],
        ["/usr/bin/dscl", ".", "-create", record_path, "UniqueID", str(account.uid)],
        [
            "/usr/bin/dscl",
            ".",
            "-create",
            record_path,
            "PrimaryGroupID",
            str(account.gid),
        ],
        [
            "/usr/bin/dscl",
            ".",
            "-create",
            record_path,
            "NFSHomeDirectory",
            str(account.home),
        ],
    ]
    if len(property_command_ids) != len(property_commands):
        raise ProbeError("invalid-lifecycle-command-table")
    record_created = False
    generated_uid: str | None = None
    try:
        _require_command_success(
            ["/usr/bin/dscl", ".", "-create", record_path],
            command_id="account-create-record",
        )
        record_created = True
        generated_uid = _read_system_generated_uid(account.name)
        _write_account_binding(plan, state, generated_uid)
        for command_id, command in zip(property_command_ids, property_commands):
            _require_command_success(command, command_id=command_id)
        if _validated_system_generated_uid(_account_record(account)) != generated_uid:
            raise ProbeError("account-record-generated-uid-drift")
        accounts = _list_accounts()
        if (
            accounts.get(account.name) != account.uid
            or sum(uid == account.uid for uid in accounts.values()) != 1
        ):
            raise ProbeError("account-list-readback-disagrees")
    except ProbeError as primary_error:
        if record_created and generated_uid is not None:
            try:
                _rollback_disposable_account_creation(account, generated_uid)
            except ProbeError as rollback_error:
                primary_code, secondary_code = _merge_probe_error(
                    primary_error.code,
                    primary_error.secondary_code,
                    rollback_error,
                )
                raise ProbeError(
                    primary_code,
                    secondary_code=secondary_code,
                ) from rollback_error
        raise


def _new_directory_identity(path: Path) -> tuple[int, int, int, int]:
    descriptor = -1
    try:
        metadata = path.lstat()
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.geteuid()
            or (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
                metadata.st_uid,
                metadata.st_gid,
            )
            != (
                opened.st_dev,
                opened.st_ino,
                opened.st_mode,
                opened.st_uid,
                opened.st_gid,
            )
        ):
            raise ProbeError("home-create-new-disagrees")
    except (OSError, ProbeError) as error:
        raise ProbeError("home-create-new-disagrees") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return metadata.st_dev, metadata.st_ino, metadata.st_uid, metadata.st_gid


def _require_new_directory_no_acl(
    path: Path,
    identity: tuple[int, int, int, int],
) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        metadata = os.fstat(descriptor)
        if (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_uid,
            metadata.st_gid,
        ) != identity or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise ProbeError("home-create-new-disagrees")
        _require_no_extended_acl(descriptor, "bound-home-acl")
    except (OSError, ProbeError) as error:
        raise ProbeError("home-create-new-disagrees") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _created_directory_is_exact(
    path: Path,
    identity: tuple[int, int, int, int] | None,
    account: DisposableAccount,
) -> bool:
    if identity is None:
        return False
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(metadata.st_mode)
        and stat.S_IMODE(metadata.st_mode) == 0o700
        and (metadata.st_dev, metadata.st_ino) == identity[:2]
        and (metadata.st_uid, metadata.st_gid)
        in {identity[2:], (account.uid, account.gid)}
    )


def _rollback_created_home(
    account: DisposableAccount,
    *,
    home_created: bool,
    home_identity: tuple[int, int, int, int] | None,
    probe_created: bool,
    probe_identity: tuple[int, int, int, int] | None,
) -> None:
    probe_root = account.home / "launchd-probe"
    if account.home.name != account.name or probe_root.parent != account.home:
        raise ProbeError("home-create-new-preserved")
    try:
        probe_metadata = probe_root.lstat() if probe_created else None
        if probe_created and (
            not _created_directory_is_exact(probe_root, probe_identity, account)
            or _bounded_path_directory_names(
                probe_root,
                probe_metadata,
                0,
                limit_code="home-create-new-preserved",
                failure_code="home-create-new-preserved",
            )
        ):
            raise ProbeError("home-create-new-preserved")
        expected_home_names = {"launchd-probe"} if probe_created else set()
        home_metadata = account.home.lstat() if home_created else None
        if home_created and (
            not _created_directory_is_exact(account.home, home_identity, account)
            or set(
                _bounded_path_directory_names(
                    account.home,
                    home_metadata,
                    len(expected_home_names),
                    limit_code="home-create-new-preserved",
                    failure_code="home-create-new-preserved",
                )
            )
            != expected_home_names
        ):
            raise ProbeError("home-create-new-preserved")
        if probe_created:
            probe_root.rmdir()
        if home_created:
            account.home.rmdir()
    except OSError as error:
        raise ProbeError("home-create-new-preserved") from error


def _create_disposable_home(account: DisposableAccount) -> None:
    probe_root = account.home / "launchd-probe"
    home_created = False
    probe_created = False
    home_identity: tuple[int, int, int, int] | None = None
    probe_identity: tuple[int, int, int, int] | None = None
    try:
        account.home.mkdir(mode=0o700)
        home_created = True
        home_identity = _new_directory_identity(account.home)
        _require_new_directory_no_acl(account.home, home_identity)
        os.chown(account.home, account.uid, account.gid, follow_symlinks=False)
        probe_root.mkdir(mode=0o700)
        probe_created = True
        probe_identity = _new_directory_identity(probe_root)
        _require_new_directory_no_acl(probe_root, probe_identity)
        os.chown(probe_root, account.uid, account.gid, follow_symlinks=False)
        for path in (account.home, probe_root):
            if not _directory_matches(path, 0o700, account.uid, account.gid):
                raise ProbeError("home-create-new-disagrees")
    except (OSError, ProbeError) as error:
        _rollback_created_home(
            account,
            home_created=home_created,
            home_identity=home_identity,
            probe_created=probe_created,
            probe_identity=probe_identity,
        )
        if isinstance(error, ProbeError):
            raise
        raise ProbeError("home-create-new-failed") from error


def _write_root_file(path: Path, raw: bytes, mode: int) -> None:
    write_create_new(path, raw, mode)
    if not _root_file_matches(path, mode):
        raise ProbeError("root-file-disagrees")


def _write_account_file(path: Path, raw: bytes, account: DisposableAccount) -> None:
    write_create_new(path, raw)
    try:
        os.chown(path, account.uid, account.gid, follow_symlinks=False)
    except OSError as error:
        raise ProbeError("account-file-chown-failed") from error
    if not _metadata_matches(
        path,
        kind="file",
        mode=0o600,
        uid=account.uid,
        gid=account.gid,
        nlink=1,
    ):
        raise ProbeError("account-file-disagrees")


def _lifecycle_state(
    *,
    plan: LaunchdPlan,
    source_sha256: str,
    plist_sha256: str,
    ownership_marker: str,
    runner_uid: int,
    runner_gid: int,
) -> dict:
    return _document_with_digest(
        {
            "schema_version": 1,
            "contract": "task-witness-macos-launchd-lifecycle-state-v1",
            "candidate_sha1": EXPECTED_CANDIDATE_SHA,
            "account": {
                "name": plan.account.name,
                "uid": plan.account.uid,
                "gid": plan.account.gid,
                "home": str(plan.account.home),
            },
            "label": plan.label,
            "stage_root": str(plan.stage_root),
            "helper_sha256": source_sha256,
            "plist_sha256": plist_sha256,
            "ownership_marker": ownership_marker,
            "runner_uid": runner_uid,
            "runner_gid": runner_gid,
        }
    )


def _account_binding_document(
    plan: LaunchdPlan,
    state: Mapping[str, object],
    generated_uid: str,
) -> dict:
    state_sha256 = state.get("content_sha256")
    try:
        validated_generated_uid = _validated_system_generated_uid(
            {"GeneratedUID": [generated_uid]}
        )
    except ProbeError as error:
        raise ProbeError("account-binding-invalid") from error
    if not _is_sha256(state_sha256):
        raise ProbeError("account-binding-state-invalid")
    return _document_with_digest(
        {
            "schema_version": 1,
            "contract": "task-witness-macos-launchd-account-binding-v1",
            "candidate_sha1": EXPECTED_CANDIDATE_SHA,
            "account": {
                "name": plan.account.name,
                "uid": plan.account.uid,
                "gid": plan.account.gid,
                "home": str(plan.account.home),
                "generated_uid": validated_generated_uid,
            },
            "label": plan.label,
            "stage_root": str(plan.stage_root),
            "state_sha256": state_sha256,
        }
    )


def _load_account_binding(
    plan: LaunchdPlan,
    state: Mapping[str, object],
) -> dict | None:
    document = _load_root_stage_document(
        plan.stage_root / "account.json",
        MAX_ACCOUNT_BINDING_BYTES,
        "account-binding",
    )
    if document is None:
        return None
    account = document.get("account")
    if not isinstance(account, dict):
        raise ProbeError("account-binding-drift")
    generated_uid = account.get("generated_uid")
    if not isinstance(generated_uid, str):
        raise ProbeError("account-binding-drift")
    try:
        expected = _account_binding_document(plan, state, generated_uid)
    except ProbeError as error:
        raise ProbeError("account-binding-drift") from error
    if document != expected:
        raise ProbeError("account-binding-drift")
    return document


def _write_account_binding(
    plan: LaunchdPlan,
    state: Mapping[str, object],
    generated_uid: str,
) -> dict:
    document = _account_binding_document(plan, state, generated_uid)
    raw = canonical_bytes(document)
    if len(raw) > MAX_ACCOUNT_BINDING_BYTES:
        raise ProbeError("account-binding-too-large")
    path = plan.stage_root / "account.json"
    try:
        _write_root_file(path, raw, 0o600)
        observed = _load_account_binding(plan, state)
        if observed != document:
            raise ProbeError("account-binding-disagrees")
    except ProbeError:
        if _path_exists_no_follow(path):
            try:
                removable = (
                    _root_file_matches(path, 0o600)
                    and _read_stable_regular_file(
                        path,
                        len(raw),
                        "partial-account-binding",
                    )
                    == raw
                )
                if not removable:
                    raise ProbeError("account-binding-preserved")
                path.unlink()
            except (OSError, ProbeError) as cleanup_error:
                raise ProbeError("account-binding-preserved") from cleanup_error
        raise
    return document


def _launchd_ownership_document(
    plan: LaunchdPlan,
    state: Mapping[str, object],
) -> dict:
    ownership_marker = state.get("ownership_marker")
    helper_sha256 = state.get("helper_sha256")
    plist_sha256 = state.get("plist_sha256")
    state_sha256 = state.get("content_sha256")
    if (
        not isinstance(ownership_marker, str)
        or LAUNCHD_OWNERSHIP_MARKER_RE.fullmatch(ownership_marker) is None
        or any(
            not _is_sha256(value)
            for value in (
                helper_sha256,
                plist_sha256,
                state_sha256,
            )
        )
    ):
        raise ProbeError("launchd-ownership-state-invalid")
    return _document_with_digest(
        {
            "schema_version": 1,
            "contract": "task-witness-macos-launchd-ownership-v1",
            "candidate_sha1": EXPECTED_CANDIDATE_SHA,
            "account": {
                "name": plan.account.name,
                "uid": plan.account.uid,
            },
            "label": plan.label,
            "stage_root": str(plan.stage_root),
            "ownership_marker": ownership_marker,
            "helper_sha256": helper_sha256,
            "plist_sha256": plist_sha256,
            "state_sha256": state_sha256,
        }
    )


def _write_launchd_ownership_marker(
    plan: LaunchdPlan,
    state: Mapping[str, object],
) -> dict:
    if _load_account_binding(plan, state) is None:
        raise ProbeError("account-binding-missing")
    document = _launchd_ownership_document(plan, state)
    raw = canonical_bytes(document)
    if len(raw) > MAX_OWNERSHIP_BYTES:
        raise ProbeError("launchd-ownership-marker-too-large")
    _write_root_file(plan.stage_root / "ownership.json", raw, 0o600)
    observed = _load_launchd_ownership_marker(plan, state)
    if observed != document:
        raise ProbeError("launchd-ownership-marker-disagrees")
    return document


def _load_launchd_ownership_marker(
    plan: LaunchdPlan,
    state: Mapping[str, object],
) -> dict | None:
    document = _load_root_stage_document(
        plan.stage_root / "ownership.json",
        MAX_OWNERSHIP_BYTES,
        "launchd-ownership-marker",
    )
    if document is None:
        return None
    if document != _launchd_ownership_document(plan, state):
        raise ProbeError("launchd-ownership-marker-drift")
    return document


def _home_library_unsafe_error(cause: object) -> ProbeError:
    secondary = (
        f"home-library-unsafe-entry-{cause}"
        if isinstance(cause, str) and cause in HOME_LIBRARY_UNSAFE_CAUSES
        else "home-library-diagnostic-invalid"
    )
    return ProbeError("home-library-unsafe-entry", secondary_code=secondary)


def _home_library_file_xattr_error(
    phase: object,
    path_family: object,
    family: object,
) -> ProbeError:
    valid = (
        type(phase) is type(path_family) is type(family) is str
        and phase in _XATTR_PHASES
        and path_family in _XATTR_PATH_FAMILIES
        and family in _XATTR_FAMILIES
    )
    secondary = (
        f"home-library-unsafe-entry-file-xattr-{phase}-{path_family}-{family}"
        if valid
        else "home-library-diagnostic-invalid"
    )
    return ProbeError("home-library-unsafe-entry", secondary_code=secondary)


def _home_library_file_xattr_path_family(components: object) -> str:
    if (
        type(components) is not tuple
        or len(components) < 2
        or set(map(type, components)) != {str}
        or components[0] != HOME_LIBRARY_NAME
    ):
        return "nested-other"
    if len(components) == 2:
        return "direct"
    return _XATTR_PATH_FAMILY_BY_COMPONENT.get(components[1], "nested-other")


def _home_library_path_sha256(components: Sequence[str]) -> str:
    digest = hashlib.sha256(b"task-witness-macos-home-library-path-v1\0")
    total = 0
    for component in components:
        raw = os.fsencode(component)
        if (
            not raw
            or raw in {b".", b".."}
            or b"/" in raw
            or b"\0" in raw
            or len(raw) > MAX_HOME_LIBRARY_COMPONENT_BYTES
        ):
            raise _home_library_unsafe_error("path-component")
        total += len(raw)
        if total > MAX_HOME_LIBRARY_PATH_BYTES:
            raise ProbeError("home-library-bounds-exceeded")
        digest.update(len(raw).to_bytes(2, "big"))
        digest.update(raw)
    return digest.hexdigest()


def _darwin_fstatfs_identity(descriptor: int) -> tuple[int, int, str]:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        fstatfs = libc.fstatfs
        fstatfs.argtypes = [ctypes.c_int, ctypes.POINTER(_DarwinStatFs)]
        fstatfs.restype = ctypes.c_int
        observed = _DarwinStatFs()
        if fstatfs(descriptor, ctypes.byref(observed)) != 0:
            raise OSError(ctypes.get_errno(), "fstatfs failed")
        filesystem = bytes(observed.f_fstypename).split(b"\0", 1)[0].decode("ascii")
        return int(observed.f_fsid[0]), int(observed.f_fsid[1]), filesystem.lower()
    except (AttributeError, OSError, UnicodeDecodeError) as error:
        raise ProbeError("home-library-observation-failed") from error


def _require_no_extended_acl(descriptor: int, unsafe_cause: object) -> None:
    acl = None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        acl_get_fd_np = libc.acl_get_fd_np
        acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
        acl_get_fd_np.restype = ctypes.c_void_p
        acl_get_entry = libc.acl_get_entry
        acl_get_entry.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        acl_get_entry.restype = ctypes.c_int
        acl_free = libc.acl_free
        acl_free.argtypes = [ctypes.c_void_p]
        acl_free.restype = ctypes.c_int
        ctypes.set_errno(0)
        acl = acl_get_fd_np(descriptor, ACL_TYPE_EXTENDED)
        if not acl:
            if ctypes.get_errno() == errno.ENOENT:
                return
            raise OSError(ctypes.get_errno(), "acl_get_fd_np failed")
        entry = ctypes.c_void_p()
        result = acl_get_entry(acl, ACL_FIRST_ENTRY, ctypes.byref(entry))
        if result == 0:
            raise _home_library_unsafe_error(unsafe_cause)
        raise OSError(ctypes.get_errno(), "acl_get_entry failed")
    except ProbeError:
        raise
    except (AttributeError, OSError) as error:
        raise ProbeError("home-library-observation-failed") from error
    finally:
        if acl:
            try:
                acl_free(acl)
            except (AttributeError, OSError):
                pass


def _bind_xattr(call, value: bool):
    int_, ptr, size = ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t
    call.argtypes = (
        [int_, ctypes.c_char_p, ptr, size, ctypes.c_uint32, int_]
        if value
        else [int_, ptr, size, int_]
    )
    call.restype = ctypes.c_ssize_t
    return call


def _xattr_names(call, fd: int, options: int) -> object:
    buf = ctypes.create_string_buffer(MAX_HOME_LIBRARY_XATTR_LIST_BYTES)
    ctypes.set_errno(0)
    size = call(fd, buf, len(buf), options)
    if size < 0:
        if ctypes.get_errno() == errno.ERANGE:
            return "overflow"
        raise OSError(ctypes.get_errno(), "flistxattr failed")
    if size == 0:
        return ()
    raw = buf.raw[:size]
    if not raw.endswith(b"\0"):
        return None
    parts = raw[:-1].split(b"\0")
    if not all(parts) or max(map(len, parts)) > 127 or len(set(parts)) != len(parts):
        return None
    try:
        return tuple(sorted(map(bytes.decode, parts)))
    except UnicodeDecodeError:
        return None


def _file_xattr_family(call, fd: int) -> str | None:
    items = [_xattr_names(call, fd, flag) for flag in (0, XATTR_SHOWCOMPRESSION) * 2]
    if "overflow" in items:
        return "overflow"
    if None in items:
        return "unclassified"
    if items[:2] != items[2:]:
        return "unstable"
    normal, shown = map(set, items[:2])
    if not normal <= shown or shown - normal - {"com.apple.decmpfs"}:
        return "unclassified"
    if not shown:
        return None
    known = dict(
        zip(
            "decmpfs ResourceFork FinderInfo provenance quarantine".split(),
            _XATTR_FAMILIES[7:10] + _XATTR_FAMILIES[:2],
        )
    )
    apple = {name for name in shown if name.startswith("com.apple.")}
    if not apple:
        return "nonapple-other"
    if apple != shown:
        return "mixed-other"
    categories = {
        known.get(suffix := name[10:])
        or (
            "apple-metadata"
            if suffix.startswith("metadata:") and suffix != "metadata:"
            else "apple-other"
        )
        for name in shown
    }
    return categories.pop() if len(categories) == 1 else "apple-mixed"


def _home_library_file_xattr_state(
    descriptor: int,
    *,
    diagnostic_phase: object,
    diagnostic_path_family: object,
) -> object:
    failures = (AttributeError, TypeError, ValueError, OSError)
    invalid = _home_library_file_xattr_error(
        diagnostic_phase, diagnostic_path_family, "unclassified"
    )
    if invalid.secondary_code == "home-library-diagnostic-invalid":
        raise invalid
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        flist = _bind_xattr(libc.flistxattr, False)
        family = _file_xattr_family(flist, descriptor)
    except failures as error:
        raise ProbeError("home-library-observation-failed") from error
    if family != "apple-quarantine":
        return family
    try:
        fget = _bind_xattr(libc.fgetxattr, True)
    except failures[:3]:
        return _XATTR_FAMILIES[3]
    name = b"com.apple.quarantine"

    def names(options):
        try:
            return _xattr_names(flist, descriptor, options)
        except failures:
            return None

    def value():
        buf = ctypes.create_string_buffer(MAX_HOME_LIBRARY_QUARANTINE_VALUE_BYTES)
        ctypes.set_errno(0)
        try:
            size = fget(descriptor, name, buf, len(buf), 0, 0)
        except failures:
            return None
        if type(size) is not int:
            return None
        if size < 0:
            return {
                "ERANGE": "overflow",
                "ENOATTR": "unstable",
                "ENODATA": "unstable",
            }.get(errno.errorcode.get(ctypes.get_errno()))
        if size > len(buf):
            return "overflow"
        raw = buf.raw[:size]
        digest = hashlib.sha256(
            _HOME_LIBRARY_QUARANTINE_VALUE_DIGEST_DOMAIN + size.to_bytes(4, "big") + raw
        ).hexdigest()
        return _HomeLibraryQuarantineEvidence(size, digest)

    observed = []
    for _ in range(2):
        observed.append(value())
        observed.extend(map(names, (0, XATTR_SHOWCOMPRESSION)))
    if "overflow" in observed:
        return "overflow"
    if None in observed:
        return _XATTR_FAMILIES[3]
    later = set(observed[1:3] + observed[4:])
    if "unstable" in observed or later != {(name.decode(),)}:
        return "unstable"
    return observed[0] if observed[0] == observed[3] else "unstable"


def _require_no_extended_attributes(
    descriptor: int,
    unsafe_cause: object,
) -> None:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        flist = _bind_xattr(libc.flistxattr, False)
        ctypes.set_errno(0)
        observed = flist(descriptor, None, 0, XATTR_SHOWCOMPRESSION)
        if observed < 0:
            raise OSError(ctypes.get_errno(), "flistxattr failed")
        if observed != 0:
            raise _home_library_unsafe_error(unsafe_cause)
    except ProbeError:
        raise
    except (AttributeError, OSError) as error:
        raise ProbeError("home-library-observation-failed") from error


def _home_library_metadata(metadata: os.stat_result) -> tuple[int, ...]:
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
        int(getattr(metadata, "st_flags", 0)),
    )


def _require_bound_home_descriptor(
    account: DisposableAccount,
    descriptor: int,
    expected_identity: Mapping[str, int] | None = None,
) -> os.stat_result:
    try:
        opened = os.fstat(descriptor)
        visible = account.home.lstat()
    except OSError as error:
        raise ProbeError("home-library-home-drift") from error
    if (
        _home_library_metadata(opened) != _home_library_metadata(visible)
        or not stat.S_ISDIR(opened.st_mode)
        or stat.S_IMODE(opened.st_mode) != 0o700
        or opened.st_uid != account.uid
        or opened.st_gid != account.gid
        or (
            expected_identity is not None
            and (opened.st_dev, opened.st_ino)
            != (
                expected_identity["home_device"],
                expected_identity["home_inode"],
            )
        )
    ):
        raise ProbeError("home-library-home-drift")
    _require_no_extended_acl(descriptor, "bound-home-acl")
    return opened


def _require_bound_home_path(
    account: DisposableAccount,
    expected_identity: Mapping[str, int],
) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            account.home,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        _require_bound_home_descriptor(account, descriptor, expected_identity)
    except ProbeError:
        raise
    except OSError as error:
        raise ProbeError("home-library-home-drift") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _require_home_library_node(
    metadata: os.stat_result,
    *,
    account: DisposableAccount,
    home_device: int,
    diagnostic_scope: str | None = None,
) -> str:
    kind = (
        "directory"
        if stat.S_ISDIR(metadata.st_mode)
        else "file"
        if stat.S_ISREG(metadata.st_mode)
        else ""
    )
    if not kind:
        raise _home_library_unsafe_error(
            "root-kind" if diagnostic_scope == "root" else "entry-stat-unsupported-kind"
        )
    mode = stat.S_IMODE(metadata.st_mode)
    checks = (
        ("device-mismatch", metadata.st_dev != home_device),
        ("uid-mismatch", metadata.st_uid != account.uid),
        ("gid-mismatch", metadata.st_gid != account.gid),
        ("group-other-write", bool(mode & 0o022)),
        ("flags-present", int(getattr(metadata, "st_flags", 0)) != 0),
        (
            "owner-access",
            mode & (0o700 if kind == "directory" else 0o400)
            != (0o700 if kind == "directory" else 0o400),
        ),
        ("link-count", kind == "file" and metadata.st_nlink != 1),
    )
    for invariant, failed in checks:
        if failed:
            raise _home_library_unsafe_error(
                f"{diagnostic_scope or kind}-stat-{invariant}"
            )
    return kind


def _read_home_library_file(
    parent_descriptor: int,
    name: str,
    expected: os.stat_result,
    diagnostic: tuple[object, object],
):
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent_descriptor,
        )
        metadata = _home_library_metadata
        opened = os.fstat(descriptor)
        opened_state = metadata(opened)
        unstable = ProbeError("home-library-observation-unstable")
        if opened_state != metadata(expected):
            raise unstable

        def stable():
            return (
                metadata(os.fstat(descriptor))
                == opened_state
                == metadata(
                    os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
                )
            )

        _require_no_extended_acl(descriptor, "file-acl")
        context = {
            "diagnostic_phase": diagnostic[0],
            "diagnostic_path_family": diagnostic[1],
        }
        before = _home_library_file_xattr_state(descriptor, **context)
        if (
            type(before) is _HomeLibraryQuarantineEvidence
            and diagnostic[1] != "preferences"
        ):
            raise _home_library_file_xattr_error(*diagnostic, _XATTR_FAMILIES[2])
        if before is not None and type(before) is not _HomeLibraryQuarantineEvidence:
            raise _home_library_file_xattr_error(*diagnostic, before)
        if not 0 <= opened.st_size <= MAX_HOME_LIBRARY_FILE_BYTES:
            raise ProbeError("home-library-bounds-exceeded")
        chunks = []
        remaining = opened.st_size + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) != opened.st_size or not stable():
            raise unstable
        after = _home_library_file_xattr_state(descriptor, **context)
        if before is None and after is None:
            receipt = None
        elif (
            type(before) is type(after) is _HomeLibraryQuarantineEvidence
            and before == after
        ):
            receipt = before
        else:
            family = (
                after
                if (
                    after is not None
                    and type(after) not in (str, _HomeLibraryQuarantineEvidence)
                    or type(after) is str
                    and (
                        before is None
                        or after
                        in {"overflow", "apple-quarantine-unreadable", "unclassified"}
                    )
                )
                else _XATTR_FAMILIES[2]
                if before == after
                else "unstable"
            )
            raise _home_library_file_xattr_error(*diagnostic, family)
        if not stable():
            raise unstable
        return raw, opened, receipt
    except ProbeError:
        raise
    except OSError as error:
        raise ProbeError("home-library-observation-failed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _bounded_directory_names(
    descriptor: int,
    maximum: int,
    *,
    limit_code: str,
    failure_code: str,
) -> list[str]:
    if type(maximum) is not int or not 0 <= maximum <= MAX_HOME_LIBRARY_NODES:
        raise ProbeError(limit_code)
    names: list[str] = []
    try:
        with os.scandir(descriptor) as entries:
            for entry in entries:
                names.append(entry.name)
                if len(names) > maximum:
                    raise ProbeError(limit_code)
        return sorted(names, key=os.fsencode)
    except ProbeError:
        raise
    except (OSError, UnicodeError) as error:
        raise ProbeError(failure_code) from error


def _bounded_path_directory_names(
    path: Path,
    expected: os.stat_result,
    maximum: int,
    *,
    limit_code: str,
    failure_code: str,
) -> list[str]:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        opened = os.fstat(descriptor)
        if _home_library_metadata(opened) != _home_library_metadata(expected):
            raise ProbeError(failure_code)
        names = _bounded_directory_names(
            descriptor,
            maximum,
            limit_code=limit_code,
            failure_code=failure_code,
        )
        if _home_library_metadata(os.fstat(descriptor)) != _home_library_metadata(
            opened
        ) or _home_library_metadata(path.lstat()) != _home_library_metadata(opened):
            raise ProbeError(failure_code)
        return names
    except ProbeError:
        raise
    except (OSError, UnicodeError) as error:
        raise ProbeError(failure_code) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _library_record_metadata(metadata: os.stat_result, kind: str) -> dict:
    return {
        "device": metadata.st_dev,
        "flags": int(getattr(metadata, "st_flags", 0)),
        "gid": metadata.st_gid,
        "inode": metadata.st_ino,
        "kind": kind,
        "mode": metadata.st_mode,
        "uid": metadata.st_uid,
    }


def _library_node_state(metadata, raw=None, receipt=None):
    state = {
        "ctime_ns": metadata.st_ctime_ns,
        "link_count": metadata.st_nlink,
        "mtime_ns": metadata.st_mtime_ns,
        "size": metadata.st_size,
    }
    if raw is not None:
        state["content_sha256"] = hashlib.sha256(raw).hexdigest()
        state["xattr_receipt"] = (
            None
            if receipt is None
            else {"family": _XATTR_FAMILIES[2], **receipt._asdict()}
        )
    return state


def _observe_bounded_library(
    account: DisposableAccount,
    leaf_name: str,
    expected_home_identity: Mapping[str, int] | None = None,
    diagnostic_phase: object = None,
) -> ObservedHomeLibrary:
    home_descriptor = -1
    library_descriptor = -1
    records: list[dict] = []
    stability: list[dict] = []
    node_count = 0
    total_bytes = 0
    seen_identities: set[tuple[int, int]] = set()
    try:
        home_descriptor = os.open(
            account.home,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        home_opened = _require_bound_home_descriptor(
            account,
            home_descriptor,
            expected_home_identity,
        )
        home_filesystem = _darwin_fstatfs_identity(home_descriptor)
        library_metadata = os.stat(
            leaf_name,
            dir_fd=home_descriptor,
            follow_symlinks=False,
        )
        if not stat.S_ISDIR(library_metadata.st_mode):
            raise _home_library_unsafe_error("root-kind")
        _require_home_library_node(
            library_metadata,
            account=account,
            home_device=home_opened.st_dev,
            diagnostic_scope="root",
        )
        library_descriptor = os.open(
            leaf_name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=home_descriptor,
        )
        if (
            _home_library_metadata(os.fstat(library_descriptor))
            != _home_library_metadata(library_metadata)
            or _darwin_fstatfs_identity(library_descriptor) != home_filesystem
        ):
            raise ProbeError("home-library-observation-unstable")

        def walk(
            descriptor: int,
            metadata: os.stat_result,
            components: tuple[str, ...],
            parent_sha256: str,
        ) -> None:
            nonlocal node_count, total_bytes
            depth = len(components)
            node_count += 1
            if depth > MAX_HOME_LIBRARY_DEPTH or node_count > MAX_HOME_LIBRARY_NODES:
                raise ProbeError("home-library-bounds-exceeded")
            path_sha256 = _home_library_path_sha256(components)
            identity = (metadata.st_dev, metadata.st_ino)
            if identity in seen_identities:
                raise _home_library_unsafe_error("duplicate-identity")
            seen_identities.add(identity)
            kind = _require_home_library_node(
                metadata,
                account=account,
                home_device=home_opened.st_dev,
                diagnostic_scope="root" if depth == 1 else None,
            )
            scope = "root" if depth == 1 else kind
            record = {
                "depth": depth,
                **_library_record_metadata(metadata, kind),
                "parent_sha256": parent_sha256,
                "path_sha256": path_sha256,
            }
            observed = {**record, **_library_node_state(metadata)}
            if kind == "file":
                raw, opened, receipt = _read_home_library_file(
                    descriptor,
                    components[-1],
                    metadata,
                    (
                        diagnostic_phase,
                        _home_library_file_xattr_path_family(components),
                    ),
                )
                total_bytes += len(raw)
                if total_bytes > MAX_HOME_LIBRARY_TOTAL_BYTES:
                    raise ProbeError("home-library-bounds-exceeded")
                record.update(_library_node_state(opened, raw, receipt))
                records.append(record)
                stability.append(dict(record))
                return
            _require_no_extended_acl(descriptor, f"{scope}-acl")
            _require_no_extended_attributes(descriptor, f"{scope}-xattr")
            records.append(record)
            stability.append(observed)
            names = _bounded_directory_names(
                descriptor,
                MAX_HOME_LIBRARY_NODES - node_count,
                limit_code="home-library-bounds-exceeded",
                failure_code="home-library-observation-failed",
            )
            for name in names:
                child_components = (*components, name)
                child_metadata = os.stat(
                    name,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
                child_kind = _require_home_library_node(
                    child_metadata,
                    account=account,
                    home_device=home_opened.st_dev,
                )
                if child_kind == "file":
                    walk(descriptor, child_metadata, child_components, path_sha256)
                    continue
                child_descriptor = -1
                try:
                    child_descriptor = os.open(
                        name,
                        os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=descriptor,
                    )
                    child_opened = os.fstat(child_descriptor)
                    if (
                        _home_library_metadata(child_opened)
                        != _home_library_metadata(child_metadata)
                        or _darwin_fstatfs_identity(child_descriptor) != home_filesystem
                    ):
                        raise ProbeError("home-library-observation-unstable")
                    walk(
                        child_descriptor,
                        child_opened,
                        child_components,
                        path_sha256,
                    )
                finally:
                    if child_descriptor >= 0:
                        os.close(child_descriptor)

        walk(
            library_descriptor,
            os.fstat(library_descriptor),
            (HOME_LIBRARY_NAME,),
            _home_library_path_sha256(()),
        )
        records.sort(key=lambda item: item["path_sha256"])
        inventory = _document_with_digest(
            {
                "schema_version": 2,
                "contract": "task-witness-macos-bounded-library-inventory-v2",
                "entry_count": node_count,
                "regular_file_bytes": total_bytes,
                "entries": records,
            }
        )
        stability.sort(key=lambda item: item["path_sha256"])
        return ObservedHomeLibrary(
            inventory=inventory,
            stability_sha256=hashlib.sha256(canonical_bytes(stability)).hexdigest(),
        )
    except ProbeError:
        raise
    except (OSError, UnicodeError) as error:
        raise ProbeError("home-library-observation-failed") from error
    finally:
        if library_descriptor >= 0:
            os.close(library_descriptor)
        if home_descriptor >= 0:
            os.close(home_descriptor)


def _valid_library_xattr_receipt(record, by_hash):
    receipt = record.get("xattr_receipt")
    if receipt is None:
        return True
    if type(receipt) is not dict:
        return False
    length = receipt.get("value_length")
    if (
        set(receipt) != {"family", "value_length", "value_sha256"}
        or receipt.get("family") != _XATTR_FAMILIES[2]
        or type(length) is not int
        or not 0 <= length <= MAX_HOME_LIBRARY_QUARANTINE_VALUE_BYTES
        or not _is_sha256(receipt.get("value_sha256"))
    ):
        return False
    while record["depth"] > 2:
        record = by_hash[record["parent_sha256"]]
    return (record["kind"], record["path_sha256"]) == (
        "directory",
        _home_library_path_sha256((HOME_LIBRARY_NAME, "Preferences")),
    )


def _validated_library_inventory(value, account, home_device):
    inventory = _require_exact_keys(
        value,
        set(
            "content_sha256 contract entries entry_count regular_file_bytes "
            "schema_version".split()
        ),
        "home-library-inventory",
    )
    _require_content_digest(inventory, "home-library-inventory")
    invalid = ProbeError("home-library-inventory-invalid")
    records = inventory.get("entries")
    root_path = _home_library_path_sha256((HOME_LIBRARY_NAME,))
    if (
        type(inventory.get("schema_version")) is not int
        or inventory.get("schema_version") != 2
        or inventory.get("contract")
        != "task-witness-macos-bounded-library-inventory-v2"
        or not isinstance(records, list)
        or not 1 <= len(records) <= MAX_HOME_LIBRARY_NODES
        or type(inventory.get("entry_count")) is not int
        or inventory.get("entry_count") != len(records)
        or type(inventory.get("regular_file_bytes")) is not int
        or any(
            not isinstance(item, dict) or not _is_sha256(item.get("path_sha256"))
            for item in records
        )
        or canonical_bytes(records)
        != canonical_bytes(sorted(records, key=lambda item: item["path_sha256"]))
    ):
        raise invalid
    by_hash = {}
    identities = set()
    total_bytes = 0
    common_keys = set(
        "depth device flags gid inode kind mode parent_sha256 path_sha256 uid".split()
    )
    file_keys = common_keys | set(
        "content_sha256 ctime_ns link_count mtime_ns size xattr_receipt".split()
    )
    for record_value in records:
        if not isinstance(record_value, dict):
            raise invalid
        kind = record_value.get("kind")
        if set(record_value) != (file_keys if kind == "file" else common_keys):
            raise invalid
        path_sha256 = record_value.get("path_sha256")
        parent_sha256 = record_value.get("parent_sha256")
        integer_names = common_keys - set("kind parent_sha256 path_sha256".split())
        if kind == "file":
            integer_names |= set("ctime_ns link_count mtime_ns size".split())
        if (
            not isinstance(kind, str)
            or kind not in {"directory", "file"}
            or not _is_sha256(parent_sha256)
            or path_sha256 in by_hash
            or any(type(record_value.get(name)) is not int for name in integer_names)
            or record_value.get("depth", 0) not in range(1, MAX_HOME_LIBRARY_DEPTH + 1)
            or record_value.get("device") != home_device
            or record_value.get("uid") != account.uid
            or record_value.get("gid") != account.gid
            or record_value.get("flags") != 0
        ):
            raise invalid
        identity = (record_value["device"], record_value["inode"])
        if record_value["inode"] <= 0 or identity in identities:
            raise invalid
        identities.add(identity)
        if kind == "directory":
            if (
                not stat.S_ISDIR(record_value["mode"])
                or stat.S_IMODE(record_value["mode"]) & 0o022
                or stat.S_IMODE(record_value["mode"]) & 0o700 != 0o700
            ):
                raise invalid
        else:
            digest = record_value.get("content_sha256")
            size = record_value.get("size")
            if (
                not stat.S_ISREG(record_value["mode"])
                or stat.S_IMODE(record_value["mode"]) & 0o022
                or stat.S_IMODE(record_value["mode"]) & 0o400 != 0o400
                or record_value.get("link_count") != 1
                or not _is_sha256(digest)
                or not isinstance(size, int)
                or not 0 <= size <= MAX_HOME_LIBRARY_FILE_BYTES
            ):
                raise invalid
            total_bytes += size
        by_hash[path_sha256] = record_value
    empty_path = _home_library_path_sha256(())
    for path_sha256, record in by_hash.items():
        parent = record["parent_sha256"]
        if record["depth"] == 1:
            if (
                path_sha256 != root_path
                or parent != empty_path
                or record["kind"] != "directory"
            ):
                raise invalid
        elif (
            parent not in by_hash
            or by_hash[parent]["kind"] != "directory"
            or by_hash[parent]["depth"] != record["depth"] - 1
        ):
            raise invalid
    if any(not _valid_library_xattr_receipt(record, by_hash) for record in records):
        raise invalid
    if (
        total_bytes != inventory.get("regular_file_bytes")
        or total_bytes > MAX_HOME_LIBRARY_TOTAL_BYTES
    ):
        raise invalid
    return dict(inventory)


def _bounded_library_inventory(account: DisposableAccount) -> dict | None:
    source = account.home / HOME_LIBRARY_NAME
    quarantine = account.home / HOME_LIBRARY_QUARANTINE_NAME
    if _path_exists_no_follow(quarantine):
        raise ProbeError("home-library-quarantine-drift")
    if not _path_exists_no_follow(source):
        return None
    first = _stable_home_library_observation(
        account, HOME_LIBRARY_NAME, None, "journal-inventory"
    )
    home_device = account.home.lstat().st_dev
    return _validated_library_inventory(first.inventory, account, home_device)


def _require_library_inventory_relation(
    observed: Mapping[str, object],
    authorized: Mapping[str, object],
    *,
    exact: bool,
) -> None:
    observed_entries = observed.get("entries")
    authorized_entries = authorized.get("entries")
    if not isinstance(observed_entries, list) or not isinstance(
        authorized_entries, list
    ):
        raise ProbeError("home-library-inventory-drift")
    authorized_by_path = {
        item.get("path_sha256"): item
        for item in authorized_entries
        if isinstance(item, dict)
    }
    if len(authorized_by_path) != len(authorized_entries) or any(
        not isinstance(item, dict)
        or authorized_by_path.get(item.get("path_sha256")) != item
        for item in observed_entries
    ):
        raise ProbeError("home-library-inventory-drift")
    if exact and len(observed_entries) != len(authorized_entries):
        raise ProbeError("home-library-inventory-drift")


def _stable_home_library_observation(
    account: DisposableAccount,
    leaf_name: str,
    home_identity: Mapping[str, int] | None,
    diagnostic_phase: str,
) -> ObservedHomeLibrary:
    first, second = (
        _observe_bounded_library(account, leaf_name, home_identity, diagnostic_phase)
        for _ in range(2)
    )
    if first != second:
        raise ProbeError("home-library-observation-unstable")
    return first


def _stable_observed_library(
    account: DisposableAccount,
    leaf_name: str,
    authorized: Mapping[str, object],
    home_identity: Mapping[str, int],
    *,
    exact: bool,
    diagnostic_phase: str,
) -> dict:
    first = _stable_home_library_observation(
        account, leaf_name, home_identity, diagnostic_phase
    )
    current = _validated_library_inventory(
        first.inventory,
        account,
        home_identity["home_device"],
    )
    _require_library_inventory_relation(current, authorized, exact=exact)
    return current


def _authorized_library_record(
    authorized_by_path: Mapping[str, Mapping[str, object]],
    components: Sequence[str],
) -> Mapping[str, object]:
    record = authorized_by_path.get(_home_library_path_sha256(components))
    if record is None:
        raise ProbeError("home-library-inventory-drift")
    return record


def _require_library_record_metadata(
    record: Mapping[str, object],
    metadata: os.stat_result,
    kind: str,
) -> None:
    if any(
        record.get(name) != value
        for name, value in _library_record_metadata(metadata, kind).items()
    ):
        raise ProbeError("home-library-inventory-drift")


def _require_library_directory(
    record: Mapping[str, object], descriptor: int, scope: str
) -> None:
    _require_library_record_metadata(record, os.fstat(descriptor), "directory")
    _require_no_extended_acl(descriptor, f"{scope}-acl")
    _require_no_extended_attributes(descriptor, f"{scope}-xattr")


def _walk(
    descriptor: int,
    components: tuple[str, ...],
    authorized_by_path: Mapping[str, Mapping[str, object]],
    remove: bool,
) -> None:
    names = _bounded_directory_names(
        descriptor,
        len(authorized_by_path),
        limit_code="home-library-inventory-drift",
        failure_code="home-library-cleanup-failed",
    )
    for name in names:
        child_components = (*components, name)
        record = _authorized_library_record(authorized_by_path, child_components)
        try:
            metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except OSError as error:
            raise ProbeError("home-library-inventory-drift") from error
        kind = record.get("kind")
        if kind == "file":
            _require_library_record_metadata(record, metadata, "file")
            raw, opened, receipt = _read_home_library_file(
                descriptor,
                name,
                metadata,
                (
                    "delete-boundary",
                    _home_library_file_xattr_path_family(child_components),
                ),
            )
            if any(
                record.get(field) != value
                for field, value in _library_node_state(opened, raw, receipt).items()
            ):
                raise ProbeError("home-library-inventory-drift")
            if not remove:
                continue
            try:
                os.unlink(name, dir_fd=descriptor)
                os.fsync(descriptor)
            except OSError as error:
                raise ProbeError("home-library-cleanup-failed") from error
            continue
        if kind != "directory":
            raise ProbeError("home-library-inventory-drift")
        _require_library_record_metadata(record, metadata, "directory")
        child_descriptor = -1
        try:
            child_descriptor = os.open(
                name,
                os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            _require_library_directory(record, child_descriptor, "directory")
            _walk(
                child_descriptor,
                child_components,
                authorized_by_path,
                remove,
            )
            _require_library_directory(record, child_descriptor, "directory")
        except ProbeError:
            raise
        except OSError as error:
            raise ProbeError("home-library-cleanup-failed") from error
        finally:
            if child_descriptor >= 0:
                os.close(child_descriptor)
        try:
            current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            _require_library_record_metadata(record, current, "directory")
            if remove:
                os.rmdir(name, dir_fd=descriptor)
                os.fsync(descriptor)
        except ProbeError:
            raise
        except OSError as error:
            raise ProbeError("home-library-cleanup-failed") from error


def _quarantine_and_remove_bounded_library(
    account: DisposableAccount,
    authorization: Mapping[str, object],
) -> None:
    identity = _validated_disposable_home_identity(authorization.get("home_identity"))
    inventory_value = authorization.get("library_inventory", "none")
    if inventory_value is None:
        inventory_value = "none"
    source = account.home / HOME_LIBRARY_NAME
    quarantine = account.home / HOME_LIBRARY_QUARANTINE_NAME
    source_present = _path_exists_no_follow(source)
    quarantine_present = _path_exists_no_follow(quarantine)
    if inventory_value == "none":
        if source_present or quarantine_present:
            raise ProbeError("home-library-inventory-drift")
        return
    inventory = _validated_library_inventory(
        inventory_value,
        account,
        identity["home_device"],
    )
    if source_present and quarantine_present:
        raise ProbeError("home-library-quarantine-drift")
    if not source_present and not quarantine_present:
        return
    home_descriptor = -1
    try:
        home_descriptor = os.open(
            account.home,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        _require_bound_home_descriptor(account, home_descriptor, identity)
        if source_present:
            _stable_observed_library(
                account,
                HOME_LIBRARY_NAME,
                inventory,
                identity,
                exact=True,
                diagnostic_phase="source-revalidation",
            )
            _require_bound_home_descriptor(account, home_descriptor, identity)
            _renameat_exclusive(
                home_descriptor,
                HOME_LIBRARY_NAME,
                home_descriptor,
                HOME_LIBRARY_QUARANTINE_NAME,
            )
        try:
            os.fsync(home_descriptor)
        except OSError as error:
            raise ProbeError("home-library-quarantine-failed") from error
        current = _stable_observed_library(
            account,
            HOME_LIBRARY_QUARANTINE_NAME,
            inventory,
            identity,
            exact=False,
            diagnostic_phase="quarantine-revalidation",
        )
        _require_bound_home_descriptor(account, home_descriptor, identity)
        authorized_entries = inventory.get("entries")
        if not isinstance(authorized_entries, list):
            raise ProbeError("home-library-inventory-drift")
        authorized_by_path = {
            item["path_sha256"]: item
            for item in authorized_entries
            if isinstance(item, dict) and isinstance(item.get("path_sha256"), str)
        }
        current_entries = current.get("entries")
        if not isinstance(current_entries, list) or not current_entries:
            raise ProbeError("home-library-inventory-drift")
        root = _authorized_library_record(
            authorized_by_path,
            (HOME_LIBRARY_NAME,),
        )
        root_metadata = os.stat(
            HOME_LIBRARY_QUARANTINE_NAME,
            dir_fd=home_descriptor,
            follow_symlinks=False,
        )
        _require_library_record_metadata(root, root_metadata, "directory")
        library_descriptor = -1
        try:
            library_descriptor = os.open(
                HOME_LIBRARY_QUARANTINE_NAME,
                os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=home_descriptor,
            )
            for remove in (False, True):
                _require_library_directory(root, library_descriptor, "root")
                _walk(
                    library_descriptor,
                    (HOME_LIBRARY_NAME,),
                    authorized_by_path,
                    remove,
                )
            _require_library_directory(root, library_descriptor, "root")
        finally:
            if library_descriptor >= 0:
                os.close(library_descriptor)
        try:
            final_root = os.stat(
                HOME_LIBRARY_QUARANTINE_NAME,
                dir_fd=home_descriptor,
                follow_symlinks=False,
            )
            _require_library_record_metadata(root, final_root, "directory")
            os.rmdir(HOME_LIBRARY_QUARANTINE_NAME, dir_fd=home_descriptor)
            os.fsync(home_descriptor)
        except ProbeError:
            raise
        except OSError as error:
            raise ProbeError("home-library-cleanup-failed") from error
    finally:
        if home_descriptor >= 0:
            os.close(home_descriptor)


def _exact_disposable_home_identity(
    account: DisposableAccount,
    *,
    diagnostic_phase: str,
) -> dict[str, int]:
    _validate_exact_disposable_home(
        account.home,
        expected_uid=account.uid,
        expected_gid=account.gid,
        diagnostic_phase=diagnostic_phase,
    )
    try:
        home = account.home.lstat()
    except OSError as error:
        raise _home_cleanup_drift_error(
            diagnostic_phase,
            "home-read-failed",
        ) from error
    try:
        probe = (account.home / "launchd-probe").lstat()
    except OSError as error:
        raise _home_cleanup_drift_error(
            diagnostic_phase,
            "probe-read-failed",
        ) from error
    return {
        "home_device": home.st_dev,
        "home_inode": home.st_ino,
        "probe_device": probe.st_dev,
        "probe_inode": probe.st_ino,
    }


def _validated_disposable_home_identity(value: object) -> dict[str, int]:
    identity = _require_exact_keys(
        value,
        {"home_device", "home_inode", "probe_device", "probe_inode"},
        "user-domain-reset-home-identity",
    )
    if any(
        type(identity[name]) is not int
        or identity[name] < 0
        or identity[name] > (1 << 64) - 1
        for name in identity
    ) or any(identity[name] == 0 for name in ("home_inode", "probe_inode")):
        raise ProbeError("user-domain-reset-home-identity-invalid")
    return {name: int(identity[name]) for name in sorted(identity)}


def _user_domain_reset_document(
    plan: LaunchdPlan,
    state: Mapping[str, object],
    account_binding: Mapping[str, object],
    ownership: Mapping[str, object],
    authorization_sha: object,
    environment: Mapping[str, str],
    *,
    home_identity: object = None,
) -> dict:
    harness, runner = _require_user_domain_reset_capability(
        authorization_sha,
        environment,
    )
    if account_binding != _load_account_binding(
        plan, state
    ) or ownership != _launchd_ownership_document(plan, state):
        raise ProbeError("user-domain-reset-binding-drift")
    account_value = account_binding.get("account")
    state_sha256 = state.get("content_sha256")
    account_sha256 = account_binding.get("content_sha256")
    ownership_sha256 = ownership.get("content_sha256")
    if not isinstance(account_value, dict) or any(
        not _is_sha256(value)
        for value in (state_sha256, account_sha256, ownership_sha256)
    ):
        raise ProbeError("user-domain-reset-binding-drift")
    return _document_with_digest(
        {
            "schema_version": 1,
            "contract": (
                "task-witness-macos-launchd-user-domain-reset-authorization-v1"
            ),
            "candidate_sha1": EXPECTED_CANDIDATE_SHA,
            "capability": USER_DOMAIN_RESET_CAPABILITY,
            "disposition": "armed",
            "authorized_survivor_code": USER_DOMAIN_RESET_SURVIVOR_CODE,
            "authorization_sha1": authorization_sha,
            "target": f"user/{plan.account.uid}",
            "harness": harness,
            "runner": runner,
            "account": account_value,
            "home_identity": (
                _exact_disposable_home_identity(
                    plan.account,
                    diagnostic_phase="pre-reset-marker",
                )
                if home_identity is None
                else _validated_disposable_home_identity(home_identity)
            ),
            "label": plan.label,
            "stage_root": str(plan.stage_root),
            "state_sha256": state_sha256,
            "account_binding_sha256": account_sha256,
            "ownership_sha256": ownership_sha256,
        }
    )


def _load_user_domain_reset_authorization(
    plan: LaunchdPlan,
    state: Mapping[str, object],
    account_binding: Mapping[str, object],
    ownership: Mapping[str, object],
    authorization_sha: object,
    environment: Mapping[str, str],
) -> dict | None:
    document = _load_root_stage_document(
        plan.stage_root / "domain-reset.json",
        MAX_USER_DOMAIN_RESET_BYTES,
        "user-domain-reset-authorization",
    )
    if document is None:
        return None
    try:
        expected = _user_domain_reset_document(
            plan,
            state,
            account_binding,
            ownership,
            authorization_sha,
            environment,
            home_identity=document.get("home_identity"),
        )
    except ProbeError as error:
        raise ProbeError("user-domain-reset-authorization-drift") from error
    if document != expected:
        raise ProbeError("user-domain-reset-authorization-drift")
    return document


def _fsync_stage_directory(stage_root: Path) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            stage_root,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        opened = os.fstat(descriptor)
        visible = stage_root.lstat()
        if (
            not stat.S_ISDIR(opened.st_mode)
            or stat.S_IMODE(opened.st_mode) != 0o755
            or opened.st_uid != 0
            or opened.st_gid != 0
            or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
        ):
            raise ProbeError("user-domain-reset-stage-drift")
        os.fsync(descriptor)
    except OSError as error:
        raise ProbeError("user-domain-reset-stage-fsync-failed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _require_durable_user_domain_reset_authorization(
    plan: LaunchdPlan,
    state: Mapping[str, object],
    bindings: ValidatedStageBindings,
    authorization_sha: object,
    environment: Mapping[str, str],
    expected: Mapping[str, object],
) -> dict:
    if bindings.account is None or bindings.launchd is None:
        raise ProbeError("user-domain-reset-binding-missing")
    _fsync_stage_directory(plan.stage_root)
    observed = _load_user_domain_reset_authorization(
        plan,
        state,
        bindings.account,
        bindings.launchd,
        authorization_sha,
        environment,
    )
    if observed != expected:
        raise ProbeError("user-domain-reset-authorization-disagrees")
    return observed


def _write_user_domain_reset_authorization(
    plan: LaunchdPlan,
    state: Mapping[str, object],
    bindings: ValidatedStageBindings,
    authorization_sha: object,
    environment: Mapping[str, str],
) -> dict:
    if (
        bindings.account is None
        or bindings.launchd is None
        or bindings.domain_reset is not None
    ):
        raise ProbeError("user-domain-reset-binding-missing")
    account_value = bindings.account.get("account")
    if not isinstance(account_value, dict) or account_value.get(
        "generated_uid"
    ) != _read_system_generated_uid(plan.account.name):
        raise ProbeError("account-record-generated-uid-drift")
    _require_launchd_absent(plan.label)
    document = _user_domain_reset_document(
        plan,
        state,
        bindings.account,
        bindings.launchd,
        authorization_sha,
        environment,
    )
    raw = canonical_bytes(document)
    if len(raw) > MAX_USER_DOMAIN_RESET_BYTES:
        raise ProbeError("user-domain-reset-authorization-too-large")
    _write_stage_create_new(plan.stage_root / "domain-reset.json", raw, 0o600)
    return _require_durable_user_domain_reset_authorization(
        plan,
        state,
        bindings,
        authorization_sha,
        environment,
        document,
    )


def _home_cleanup_document(
    plan: LaunchdPlan,
    state: Mapping[str, object],
    bindings: ValidatedStageBindings,
    home_identity: object,
    library_inventory: object = None,
) -> dict:
    if bindings.account is None:
        raise ProbeError("home-cleanup-authorization-binding-missing")
    state_sha256 = state.get("content_sha256")
    account_sha256 = bindings.account.get("content_sha256")
    ownership_sha256 = (
        "none" if bindings.launchd is None else bindings.launchd.get("content_sha256")
    )
    reset_sha256 = (
        "none"
        if bindings.domain_reset is None
        else bindings.domain_reset.get("content_sha256")
    )
    account_value = bindings.account.get("account")
    identity = _validated_disposable_home_identity(home_identity)
    validated_library = (
        "none"
        if library_inventory is None or library_inventory == "none"
        else _validated_library_inventory(
            library_inventory,
            plan.account,
            identity["home_device"],
        )
    )
    if (
        not isinstance(account_value, dict)
        or account_value
        != {
            "name": plan.account.name,
            "uid": plan.account.uid,
            "gid": plan.account.gid,
            "home": str(plan.account.home),
            "generated_uid": account_value.get("generated_uid"),
        }
        or not isinstance(account_value.get("generated_uid"), str)
        or any(not _is_sha256(value) for value in (state_sha256, account_sha256))
        or any(
            value != "none" and not _is_sha256(value)
            for value in (ownership_sha256, reset_sha256)
        )
        or (
            bindings.domain_reset is not None
            and bindings.domain_reset.get("home_identity") != identity
        )
    ):
        raise ProbeError("home-cleanup-authorization-binding-drift")
    return _document_with_digest(
        {
            "schema_version": 3,
            "contract": "task-witness-macos-home-cleanup-authorization-v3",
            "candidate_sha1": EXPECTED_CANDIDATE_SHA,
            "disposition": "armed",
            "account": {
                "name": plan.account.name,
                "uid": plan.account.uid,
                "gid": plan.account.gid,
                "home": str(plan.account.home),
            },
            "home_identity": identity,
            "library_inventory": validated_library,
            "label": plan.label,
            "stage_root": str(plan.stage_root),
            "state_sha256": state_sha256,
            "account_binding_sha256": account_sha256,
            "ownership_sha256": ownership_sha256,
            "domain_reset_sha256": reset_sha256,
        }
    )


def _load_home_cleanup_authorization(
    plan: LaunchdPlan,
    state: Mapping[str, object],
    bindings: ValidatedStageBindings,
) -> dict | None:
    document = _load_root_stage_document(
        plan.stage_root / "home-cleanup.json",
        MAX_HOME_CLEANUP_BYTES,
        "home-cleanup-authorization",
    )
    if document is None:
        return None
    try:
        expected = _home_cleanup_document(
            plan,
            state,
            bindings,
            document.get("home_identity"),
            document.get("library_inventory"),
        )
    except ProbeError as error:
        raise ProbeError("home-cleanup-authorization-drift") from error
    if canonical_bytes(document) != canonical_bytes(expected):
        raise ProbeError("home-cleanup-authorization-drift")
    return document


def _require_durable_home_cleanup_authorization(
    plan: LaunchdPlan,
    state: Mapping[str, object],
    bindings: ValidatedStageBindings,
    expected: Mapping[str, object],
) -> dict:
    _fsync_stage_directory(plan.stage_root)
    observed = _load_home_cleanup_authorization(plan, state, bindings)
    if observed is None or canonical_bytes(observed) != canonical_bytes(expected):
        raise ProbeError("home-cleanup-authorization-disagrees")
    return observed


def _write_home_cleanup_authorization(
    plan: LaunchdPlan,
    state: Mapping[str, object],
    bindings: ValidatedStageBindings,
    home_identity: object,
) -> dict:
    if bindings.home_cleanup is not None:
        raise ProbeError("home-cleanup-authorization-already-exists")
    identity = _validated_disposable_home_identity(home_identity)
    _require_bound_home_path(plan.account, identity)
    library_inventory = _bounded_library_inventory(plan.account)
    _require_bound_home_path(plan.account, identity)
    document = _home_cleanup_document(
        plan,
        state,
        bindings,
        identity,
        library_inventory,
    )
    raw = canonical_bytes(document)
    if len(raw) > MAX_HOME_CLEANUP_BYTES:
        raise ProbeError("home-cleanup-authorization-too-large")
    _write_stage_create_new(plan.stage_root / "home-cleanup.json", raw, 0o600)
    return _require_durable_home_cleanup_authorization(
        plan,
        state,
        bindings,
        document,
    )


def validate_prestaged_helper(stage_root: Path, expected_sha256: str) -> bytes:
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise ProbeError("invalid-staged-helper-digest")
    helper = stage_root / "helper.py"
    try:
        names = {entry.name for entry in stage_root.iterdir()}
    except OSError as error:
        raise ProbeError("staged-helper-metadata-drift") from error
    if (
        names != {"helper.py"}
        or not _directory_matches(stage_root, 0o755, 0, 0)
        or not _root_file_matches(helper, 0o555)
    ):
        raise ProbeError("staged-helper-metadata-drift")
    raw = _read_stable_regular_file(helper, MAX_HELPER_BYTES, "staged-helper")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ProbeError("staged-helper-digest-disagrees")
    return raw


def _cleanup_helper_only_stage_before_state(
    *,
    stage_root: Path,
    artifact_root: Path,
    expected_helper_sha256: str,
    environment: Mapping[str, str],
) -> bool:
    try:
        names = {entry.name for entry in stage_root.iterdir()}
    except OSError as error:
        raise ProbeError("helper-only-stage-observation-failed") from error
    if names != {"helper.py"}:
        return False
    helper = stage_root / "helper.py"
    if Path(__file__) != helper:
        raise ProbeError("helper-only-executable-path-disagrees")
    validate_prestaged_helper(stage_root, expected_helper_sha256)
    account_name, label = _launchd_identity(environment)
    if _launchd_job_snapshot(label) is not None:
        raise ProbeError("helper-only-launchd-label-present")
    accounts = _list_accounts()
    if account_name in accounts or _account_exists(account_name):
        raise ProbeError("helper-only-account-present")
    if _path_exists_no_follow(Path("/Users") / account_name):
        raise ProbeError("helper-only-home-present")
    if _path_exists_no_follow(artifact_root):
        raise ProbeError("helper-only-artifact-present")
    validate_prestaged_helper(stage_root, expected_helper_sha256)
    try:
        helper.unlink()
        stage_root.rmdir()
    except OSError as error:
        raise ProbeError("helper-only-stage-cleanup-failed") from error
    if _path_exists_no_follow(stage_root):
        raise ProbeError("helper-only-stage-cleanup-disagrees")
    return True


def _initialize_lifecycle(
    *,
    stage_root: Path,
    expected_helper_sha256: str,
    runner_uid: int,
    runner_gid: int,
    environment: Mapping[str, str],
) -> LaunchdPlan:
    _require_nonroot_runner_gid(runner_gid)
    helper_raw = validate_prestaged_helper(
        stage_root,
        expected_helper_sha256,
    )
    accounts = _list_accounts()
    process_uids = {
        uid
        for uid in process_occupied_uids(_process_records())
        if DISPOSABLE_UID_MIN <= uid <= DISPOSABLE_UID_MAX
    }
    uid = choose_disposable_uid(set(accounts.values()) | process_uids)
    account_name, label = _launchd_identity(environment)
    if account_name in accounts:
        raise ProbeError("account-name-already-exists")
    account = DisposableAccount(
        name=account_name,
        uid=uid,
        gid=runner_gid,
        home=Path("/Users") / account_name,
    )
    plan = LaunchdPlan(
        account=account,
        label=label,
        stage_root=stage_root,
        helper=stage_root / "helper.py",
        plist=stage_root / "job.plist",
    )
    ownership_marker = uuid.uuid4().hex
    plist_raw = plistlib.dumps(
        build_launchd_user_plist(
            label=label,
            user=account.name,
            home=account.home,
            helper=plan.helper,
            candidate_sha=EXPECTED_CANDIDATE_SHA,
            environment={**environment, "TASK_WITNESS_LAUNCHD_LABEL": label},
            ownership_marker=ownership_marker,
        ),
        fmt=plistlib.FMT_XML,
        sort_keys=True,
    )
    state = _lifecycle_state(
        plan=plan,
        source_sha256=hashlib.sha256(helper_raw).hexdigest(),
        plist_sha256=hashlib.sha256(plist_raw).hexdigest(),
        ownership_marker=ownership_marker,
        runner_uid=runner_uid,
        runner_gid=runner_gid,
    )
    state_raw = canonical_bytes(state)
    expected_files = {
        "job.plist": (plist_raw, 0o644),
        "state.json": (state_raw, 0o600),
    }
    attempted: list[str] = []
    completed: list[str] = []
    try:
        for name, (raw, mode) in expected_files.items():
            attempted.append(name)
            _write_root_file(stage_root / name, raw, mode)
            completed.append(name)
    except ProbeError:
        try:
            removable: list[Path] = []
            for name in reversed(attempted):
                raw, mode = expected_files[name]
                path = stage_root / name
                if not _path_exists_no_follow(path):
                    if name in completed:
                        raise ProbeError("stage-initialization-preserved")
                    continue
                if (
                    not _root_file_matches(path, mode)
                    or _read_stable_regular_file(
                        path,
                        len(raw),
                        "partial-stage-file",
                    )
                    != raw
                ):
                    raise ProbeError("stage-initialization-preserved")
                removable.append(path)
            for path in removable:
                path.unlink()
        except (OSError, ProbeError) as cleanup_error:
            raise ProbeError("stage-initialization-preserved") from cleanup_error
        raise
    return plan


def initialize_launchd_user_lifecycle(
    *,
    stage_root: Path,
    artifact_root: Path,
    candidate_sha: str,
    expected_helper_sha256: str,
    runner_uid: int,
    runner_gid: int,
) -> int:
    if candidate_sha != EXPECTED_CANDIDATE_SHA:
        raise ProbeError("candidate-sha-disagrees")
    _normalized_context(os.environ)
    _validate_lifecycle_arguments(
        source_helper=Path(__file__),
        stage_root=stage_root,
        artifact_root=artifact_root,
        runner_uid=runner_uid,
        runner_gid=runner_gid,
    )
    if Path(__file__) != stage_root / "helper.py" or _path_exists_no_follow(
        artifact_root
    ):
        raise ProbeError("invalid-prestaged-helper-path")
    _initialize_lifecycle(
        stage_root=stage_root,
        expected_helper_sha256=expected_helper_sha256,
        runner_uid=runner_uid,
        runner_gid=runner_gid,
        environment=os.environ,
    )
    return 0


def _child_status(home: Path) -> int:
    raw = _read_stable_regular_file(
        home / "launchd-probe/probe.status",
        16,
        "probe-status",
    )
    if raw not in {b"0\n", b"1\n", b"2\n"}:
        raise ProbeError("probe-status-invalid")
    return int(raw.strip())


def _poll_launchd_terminal(
    plan: LaunchdPlan,
    state: Mapping[str, object],
) -> tuple[str, int]:
    deadline = time.monotonic() + LAUNCHD_POLL_TIMEOUT_SECONDS
    target = f"system/{plan.label}"
    last = ""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        status, stdout, _stderr = _run_lifecycle_command(
            ["/bin/launchctl", "print", target],
            maximum=LAUNCHD_PRINT_MAX_BYTES,
            timeout=min(COMMAND_TIMEOUT_SECONDS, remaining),
        )
        if status != 0:
            raise ProbeError("launchd-job-disappeared")
        last = stdout
        if not re.search(r"(?m)^\s*pid\s*=", stdout) and re.search(
            r"(?m)^\s*state\s*=\s*not running\s*$",
            stdout,
        ):
            validated = _validated_launchd_job_snapshot(stdout, plan, state)
            child_status = _child_status(plan.account.home)
            parse_launchctl_terminal(
                validated.sanitized,
                expected_status=child_status,
            )
            return validated.sanitized, child_status
        time.sleep(
            min(
                LAUNCHD_POLL_INTERVAL_SECONDS,
                max(0.0, deadline - time.monotonic()),
            )
        )
    if len(last.encode("utf-8")) > LAUNCHD_PRINT_MAX_BYTES:
        raise ProbeError("launchctl-print-too-large")
    raise ProbeError("launchd-job-timeout")


def _launchd_job_snapshot(label: str) -> str | None:
    if LAUNCHD_LABEL_RE.fullmatch(label) is None:
        raise ProbeError("invalid-launchd-label")
    target = f"system/{label}"
    status, stdout, _stderr = _run_lifecycle_command(
        ["/bin/launchctl", "print", target],
        maximum=LAUNCHD_PRINT_MAX_BYTES,
    )
    if status == 0:
        if not stdout:
            raise ProbeError("launchd-job-binding-invalid")
        return stdout
    if status == LAUNCHCTL_NOT_FOUND_STATUS:
        return None
    raise ProbeError("launchd-presence-unavailable")


def _require_launchd_absent(label: str) -> None:
    if _launchd_job_snapshot(label) is not None:
        raise ProbeError("launchd-label-already-loaded")


def _parse_launchctl_job_structure(raw: str, label: str) -> LaunchctlJobStructure:
    if (
        not raw
        or len(raw.encode("utf-8")) > LAUNCHD_PRINT_MAX_BYTES
        or "\x00" in raw
        or "\r" in raw
    ):
        raise ProbeError("launchd-job-binding-invalid")
    lines = raw.splitlines()
    if not lines or lines[0] != f"system/{label} = {{" or lines[-1] != "}":
        raise ProbeError("launchd-job-binding-invalid")

    top_values: dict[str, list[str]] = {}
    top_blocks: dict[str, list[tuple[str, ...]]] = {}
    block_values: list[tuple[tuple[str, ...], str]] = []
    stack: list[tuple[str, int, list[str], tuple[str, ...]]] = []
    for line in lines[1:-1]:
        if not line:
            continue
        if not line.startswith("\t"):
            raise ProbeError("launchd-job-binding-invalid")
        indent = len(line) - len(line.lstrip("\t"))
        content = line[indent:]
        if not content:
            raise ProbeError("launchd-job-binding-invalid")
        if content == "}":
            if not stack or indent != stack[-1][1]:
                raise ProbeError("launchd-job-binding-invalid")
            name, block_indent, values, _path = stack.pop()
            if block_indent == 1:
                top_blocks.setdefault(name, []).append(tuple(values))
            continue
        expected_indent = 1 if not stack else stack[-1][1] + 1
        if indent != expected_indent:
            raise ProbeError("launchd-job-binding-invalid")
        if content.endswith(" = {"):
            name = content.removesuffix(" = {")
            if not name or name.strip() != name:
                raise ProbeError("launchd-job-binding-invalid")
            if stack:
                stack[-1][2].append(content)
                path = (*stack[-1][3], name)
            else:
                path = (name,)
            stack.append((name, indent, [], path))
            continue
        if stack:
            stack[-1][2].append(content)
            block_values.append((stack[-1][3], content))
            continue
        name, separator, value = content.partition(" = ")
        if not separator or not name or not value or name.strip() != name:
            raise ProbeError("launchd-job-binding-invalid")
        top_values.setdefault(name, []).append(value)
    if stack:
        raise ProbeError("launchd-job-binding-invalid")
    return LaunchctlJobStructure(
        top_values={name: tuple(values) for name, values in top_values.items()},
        top_blocks={name: tuple(values) for name, values in top_blocks.items()},
        block_values=tuple(block_values),
    )


def _launchctl_top_value(structure: LaunchctlJobStructure, name: str) -> str:
    values = structure.top_values.get(name, ())
    if len(values) != 1 or not values[0]:
        raise ProbeError("launchd-job-binding-invalid")
    return values[0]


def _launchctl_optional_top_value(
    structure: LaunchctlJobStructure,
    name: str,
) -> str | None:
    values = structure.top_values.get(name, ())
    if len(values) > 1 or (values and not values[0]):
        raise ProbeError("launchd-job-binding-invalid")
    return values[0] if values else None


def _launchctl_block_values(
    structure: LaunchctlJobStructure,
    name: str,
) -> tuple[str, ...]:
    blocks = structure.top_blocks.get(name, ())
    if len(blocks) != 1:
        raise ProbeError("launchd-job-binding-invalid")
    return blocks[0]


def _expected_launchd_program_arguments(plan: LaunchdPlan) -> list[str]:
    probe_root = plan.account.home / "launchd-probe"
    return [
        "/usr/bin/env",
        "-i",
        "/usr/bin/python3",
        "-I",
        "-B",
        str(plan.helper),
        "probe-launchd-user",
        "--candidate-sha",
        EXPECTED_CANDIDATE_SHA,
        "--output",
        str(probe_root / "probe.json"),
        "--status-output",
        str(probe_root / "probe.status"),
    ]


def _validated_launchd_binding_evidence(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"ownership_marker"}:
        raise ProbeError("launchd-job-binding-invalid")
    ownership_marker = value.get("ownership_marker")
    if (
        not isinstance(ownership_marker, str)
        or LAUNCHD_OWNERSHIP_MARKER_RE.fullmatch(ownership_marker) is None
    ):
        raise ProbeError("launchd-job-binding-invalid")
    return {"ownership_marker": ownership_marker}


def _validated_launchd_job_snapshot(
    raw: str,
    plan: LaunchdPlan,
    state: Mapping[str, object],
    *,
    environment: Mapping[str, str] | None = None,
) -> ValidatedLaunchdJobSnapshot:
    structure = _parse_launchctl_job_structure(raw, plan.label)
    if (
        _launchctl_top_value(structure, "path") != str(plan.plist)
        or _launchctl_top_value(structure, "program") != "/usr/bin/env"
        or _launchctl_top_value(structure, "username") != plan.account.name
        or _launchctl_top_value(structure, "domain") != "system"
        or list(_launchctl_block_values(structure, "arguments"))
        != _expected_launchd_program_arguments(plan)
    ):
        raise ProbeError("launchd-job-binding-invalid")
    marker = state.get("ownership_marker")
    if (
        not isinstance(marker, str)
        or LAUNCHD_OWNERSHIP_MARKER_RE.fullmatch(marker) is None
    ):
        raise ProbeError("launchd-job-binding-invalid")
    job_environment = _launchctl_block_values(structure, "environment")
    expected_environment = _expected_launchd_child_environment(
        os.environ if environment is None else environment,
        label=plan.label,
        home=plan.account.home,
        ownership_marker=marker,
    )
    required = {f"{name} => {value}" for name, value in expected_environment.items()}
    xpc = f"XPC_SERVICE_NAME => {plan.label}"
    if not (
        (len(job_environment) == len(required) and set(job_environment) == required)
        or (
            len(job_environment) == len(required) + 1
            and set(job_environment) == required | {xpc}
        )
    ):
        raise ProbeError("launchd-job-binding-invalid")
    marker_prefix = "TASK_WITNESS_LAUNCHD_OWNERSHIP_MARKER => "
    marker_occurrences = [
        (path, value)
        for path, value in structure.block_values
        if value.startswith(marker_prefix)
    ]
    if marker_occurrences != [(("environment",), f"{marker_prefix}{marker}")]:
        raise ProbeError("launchd-job-binding-invalid")

    active_count = _launchctl_top_value(structure, "active count")
    state_value = _launchctl_top_value(structure, "state")
    runs = _launchctl_optional_top_value(structure, "runs")
    last_exit_code = _launchctl_optional_top_value(structure, "last exit code")
    if (
        re.fullmatch(r"[0-9]+", active_count) is None
        or re.fullmatch(r"[a-z][a-z -]*", state_value) is None
        or (runs is not None and re.fullmatch(r"[0-9]+", runs) is None)
        or (
            last_exit_code is not None
            and last_exit_code != "(never exited)"
            and re.fullmatch(r"-?[0-9]+", last_exit_code) is None
        )
    ):
        raise ProbeError("launchd-job-binding-invalid")

    argument_lines = "\n".join(
        f"\t\t{argument}" for argument in _expected_launchd_program_arguments(plan)
    )
    environment_lines = "\n".join(
        f"\t\t{name} => {value}" for name, value in sorted(expected_environment.items())
    )
    if xpc in job_environment:
        environment_lines += f"\n\t\t{xpc}"
    terminal_lines = ""
    if runs is not None:
        terminal_lines += f"\truns = {runs}\n"
    if last_exit_code is not None:
        terminal_lines += f"\tlast exit code = {last_exit_code}\n"
    sanitized = (
        f"system/{plan.label} = {{\n"
        f"\tactive count = {active_count}\n"
        f"\tpath = {plan.plist}\n"
        f"\tstate = {state_value}\n\n"
        "\tprogram = /usr/bin/env\n"
        "\targuments = {\n"
        f"{argument_lines}\n"
        "\t}\n\n"
        "\tenvironment = {\n"
        f"{environment_lines}\n"
        "\t}\n\n"
        "\tdomain = system\n"
        f"\tusername = {plan.account.name}\n"
        f"{terminal_lines}"
        "}\n"
    )
    return ValidatedLaunchdJobSnapshot(
        binding={"ownership_marker": marker},
        sanitized=sanitized,
    )


def _validate_launchd_job_binding(
    raw: str,
    plan: LaunchdPlan,
    state: Mapping[str, object],
) -> dict[str, str]:
    return _validated_launchd_job_snapshot(raw, plan, state).binding


def _reconcile_owned_launchd_job(
    plan: LaunchdPlan,
    state: Mapping[str, object],
    ownership: Mapping[str, object] | None,
) -> None:
    snapshot = _launchd_job_snapshot(plan.label)
    if snapshot is None:
        return
    if ownership != _launchd_ownership_document(plan, state):
        raise ProbeError("launchd-job-ownership-unproven")
    _bootout_validated_launchd_job(plan, state, snapshot)


def _reconcile_in_process_bootstrap(
    plan: LaunchdPlan,
    state: Mapping[str, object],
) -> None:
    snapshot = _launchd_job_snapshot(plan.label)
    if snapshot is None:
        return
    _bootout_validated_launchd_job(plan, state, snapshot)


def _bootout_validated_launchd_job(
    plan: LaunchdPlan,
    state: Mapping[str, object],
    snapshot: str,
) -> None:
    _validate_launchd_job_binding(snapshot, plan, state)
    target = f"system/{plan.label}"
    _require_command_success(
        ["/bin/launchctl", "bootout", target],
        command_id="launchd-bootout",
    )
    if _launchd_job_snapshot(plan.label) is not None:
        raise ProbeError("launchd-bootout-disagrees")


def _read_launchd_child_payloads(account: DisposableAccount) -> dict[str, bytes]:
    _validate_exact_disposable_home(
        account.home,
        expected_uid=account.uid,
        expected_gid=account.gid,
        diagnostic_phase="child-read",
    )
    payloads = {
        name: _read_stable_regular_file(
            account.home / "launchd-probe" / name,
            maximum,
            name.replace(".", "-"),
        )
        for name, maximum in LAUNCHD_CHILD_FILES.items()
    }
    if payloads["probe.status"] not in {b"0\n", b"1\n", b"2\n"}:
        raise ProbeError("probe-status-invalid")
    return payloads


def _ensure_failed_child_files(
    account: DisposableAccount,
    environment: Mapping[str, str],
    code: str,
    secondary_code: str | None = None,
) -> None:
    probe_root = account.home / "launchd-probe"
    if not _directory_matches(
        account.home, 0o700, account.uid, account.gid
    ) or not _directory_matches(probe_root, 0o700, account.uid, account.gid):
        raise ProbeError("home-cleanup-drift")
    try:
        probe_metadata = probe_root.lstat()
        names = set(
            _bounded_path_directory_names(
                probe_root,
                probe_metadata,
                MAX_LAUNCHD_PROBE_ENTRIES,
                limit_code="home-cleanup-drift",
                failure_code="home-cleanup-drift",
            )
        )
    except ProbeError:
        raise
    except OSError as error:
        raise ProbeError("home-cleanup-drift") from error
    if not names <= set(LAUNCHD_CHILD_FILES):
        raise ProbeError("home-cleanup-drift")
    payloads = {
        "probe.json": canonical_bytes(
            build_launchd_user_probe_error_document(
                EXPECTED_CANDIDATE_SHA,
                {
                    **environment,
                    "TASK_WITNESS_LAUNCHD_LABEL": _launchd_identity(environment)[1],
                },
                code,
                secondary_code,
            )
        ),
        "probe.status": b"2\n",
        "probe.stderr": f"task-witness macOS launchd-user probe: {code}\n".encode(),
        "probe.stdout": b"probe-error\n",
    }
    for name, raw in payloads.items():
        path = probe_root / name
        if not _path_exists_no_follow(path):
            _write_account_file(path, raw, account)


def _write_lifecycle_artifact(
    *,
    artifact_root: Path,
    plan: LaunchdPlan | None,
    binding: Mapping[str, object] | None,
    environment: Mapping[str, str],
    loaded: str,
    terminal: str,
    kickstart_pid: int | None,
    status: int,
    error_code: str | None,
    secondary_error_code: str | None = None,
    domain_reset: Mapping[str, object] | None = None,
) -> None:
    label = plan.label if plan is not None else _launchd_identity(environment)[1]
    child: dict[str, bytes]
    probe_disposition = "probe-error"
    if plan is not None:
        try:
            child = _read_launchd_child_payloads(plan.account)
            probe_document = json.loads(child["probe.json"].decode("utf-8", "strict"))
            if (
                not isinstance(probe_document, dict)
                or canonical_bytes(probe_document) != child["probe.json"]
            ):
                raise ProbeError("noncanonical-launchd-user-probe")
            _require_content_digest(probe_document, "launchd-user-probe")
            probe_disposition = str(probe_document.get("disposition", "probe-error"))
        except (ProbeError, UnicodeDecodeError, json.JSONDecodeError):
            child = {}
    else:
        child = {}
    if status == 2 and error_code is not None:
        child = {}
        probe_disposition = "probe-error"
    if not child:
        code = error_code or "lifecycle-incomplete"
        child = {
            "probe.json": canonical_bytes(
                build_launchd_user_probe_error_document(
                    EXPECTED_CANDIDATE_SHA,
                    {**environment, "TASK_WITNESS_LAUNCHD_LABEL": label},
                    code,
                    secondary_error_code,
                )
            ),
            "probe.status": b"2\n",
            "probe.stderr": f"task-witness macOS launchd-user probe: {code}\n".encode(),
            "probe.stdout": b"probe-error\n",
        }
    disposition = (
        "launchd-user-eligible"
        if status == 0 and probe_disposition == "launchd-user-eligible"
        else "launchd-user-ineligible"
        if status == 1
        else "probe-error"
    )
    validated_binding = (
        _validated_launchd_binding_evidence(binding) if binding is not None else None
    )
    artifact_loaded = ""
    artifact_terminal = ""
    if loaded or terminal:
        if plan is None or validated_binding is None:
            raise ProbeError("launchd-job-binding-invalid")
        snapshot_state = {"ownership_marker": validated_binding["ownership_marker"]}
        if loaded:
            artifact_loaded = _validated_launchd_job_snapshot(
                loaded,
                plan,
                snapshot_state,
                environment=environment,
            ).sanitized
        if terminal:
            artifact_terminal = _validated_launchd_job_snapshot(
                terminal,
                plan,
                snapshot_state,
                environment=environment,
            ).sanitized
    unsigned: dict[str, object] = {
        "schema_version": 2,
        "contract": "task-witness-macos-launchd-lifecycle-v2",
        "candidate_sha1": EXPECTED_CANDIDATE_SHA,
        "label": label,
        "kickstart_pid": kickstart_pid,
        "probe_disposition": probe_disposition,
        "disposition": disposition,
    }
    if error_code is not None:
        unsigned["error"] = _validated_probe_error(
            error_code,
            secondary_error_code,
        )
    if disposition == "launchd-user-eligible":
        if plan is None or validated_binding is None:
            raise ProbeError("launchd-job-binding-invalid")
        unsigned["binding"] = validated_binding
        unsigned["domain_reset"] = _validated_domain_reset_evidence(
            _domain_reset_evidence(None) if domain_reset is None else domain_reset
        )
    payloads = {
        "launchd.loaded": artifact_loaded.encode("utf-8"),
        "launchd.terminal": artifact_terminal.encode("utf-8"),
        "lifecycle.json": canonical_bytes(_document_with_digest(unsigned)),
        "lifecycle.status": f"{status}\n".encode("ascii"),
        **child,
    }
    if set(payloads) != set(LAUNCHD_ARTIFACT_FILES) - {"cleanup.json"}:
        raise ProbeError("launchd-artifact-build-disagrees")
    for name, raw in payloads.items():
        if len(raw) > LAUNCHD_ARTIFACT_FILES[name]:
            raise ProbeError("launchd-artifact-payload-too-large")
        _write_root_file(artifact_root / name, raw, 0o600)


def run_launchd_user_lifecycle(
    *,
    stage_root: Path,
    artifact_root: Path,
    candidate_sha: str,
    runner_uid: int,
    runner_gid: int,
    user_domain_reset_authorization: str | None = None,
) -> int:
    if candidate_sha != EXPECTED_CANDIDATE_SHA:
        raise ProbeError("candidate-sha-disagrees")
    _normalized_context(os.environ)
    _validate_lifecycle_arguments(
        source_helper=Path(__file__),
        stage_root=stage_root,
        artifact_root=artifact_root,
        runner_uid=runner_uid,
        runner_gid=runner_gid,
    )
    _require_nonroot_runner_gid(runner_gid)
    plan: LaunchdPlan | None = None
    binding: dict[str, str] | None = None
    loaded = ""
    terminal = ""
    kickstart_pid: int | None = None
    status = 2
    error_code: str | None = None
    secondary_error_code: str | None = None
    reset_evidence = _domain_reset_evidence(None)
    bootstrap_attempted = False
    bootout_confirmed = False
    _create_root_directory(artifact_root, 0o700)
    try:
        plan, state = _load_lifecycle_state(
            stage_root=stage_root,
            runner_uid=runner_uid,
            runner_gid=runner_gid,
            environment=os.environ,
        )
        stage_bindings = _validate_exact_stage(
            plan,
            state,
            user_domain_reset_authorization=user_domain_reset_authorization,
            environment=os.environ,
        )
        if stage_bindings.account is not None or stage_bindings.launchd is not None:
            raise ProbeError("launchd-job-ownership-already-recorded")
        _require_launchd_absent(plan.label)
        _create_disposable_account(plan, state)
        _create_disposable_home(plan.account)
        _validate_disposable_home_root(
            plan.account,
            diagnostic_phase="post-home-create",
        )
        _require_disposable_uid_available(
            plan.account.uid,
            active_code="disposable-uid-active-before-bootstrap",
        )
        _require_launchd_absent(plan.label)
        bootstrap_attempted = True
        _require_command_success(
            ["/bin/launchctl", "bootstrap", "system", str(plan.plist)],
            command_id="launchd-bootstrap",
        )
        _validate_disposable_home_root(
            plan.account,
            diagnostic_phase="post-system-bootstrap",
        )
        _write_launchd_ownership_marker(plan, state)
        loaded_snapshot = _launchd_job_snapshot(plan.label)
        if loaded_snapshot is None:
            raise ProbeError("launchd-job-disappeared")
        validated_loaded = _validated_launchd_job_snapshot(
            loaded_snapshot,
            plan,
            state,
        )
        binding = validated_loaded.binding
        loaded = validated_loaded.sanitized
        pid_raw = _require_command_success(
            ["/bin/launchctl", "kickstart", "-p", f"system/{plan.label}"],
            command_id="launchd-kickstart",
        )
        if re.fullmatch(r"[1-9][0-9]*", pid_raw) is None:
            raise ProbeError("launchd-kickstart-pid-invalid")
        kickstart_pid = int(pid_raw)
        terminal, status = _poll_launchd_terminal(plan, state)
        probe = _load_canonical_document(
            plan.account.home / "launchd-probe/probe.json",
            MAX_PROBE_JSON_BYTES,
            "launchd-user-probe",
        )
        if status == 2:
            raw_error = probe.get("error")
            if not isinstance(raw_error, dict):
                raise ProbeError("launchd-user-probe-error-disagrees")
            try:
                child_error = _validated_probe_error(
                    raw_error.get("code"),
                    raw_error.get("secondary_code"),
                )
                expected_probe = build_launchd_user_probe_error_document(
                    EXPECTED_CANDIDATE_SHA,
                    {**os.environ, "TASK_WITNESS_LAUNCHD_LABEL": plan.label},
                    child_error["code"],
                    child_error.get("secondary_code"),
                )
            except ProbeError as error:
                raise ProbeError("launchd-user-probe-error-disagrees") from error
            if probe != expected_probe:
                raise ProbeError("launchd-user-probe-error-disagrees")
            error_code = child_error["code"]
            secondary_error_code = child_error.get("secondary_code")
        else:
            observed_pid = (
                probe.get("observations", {}).get("process", {}).get("pid")
                if isinstance(probe.get("observations"), dict)
                else None
            )
            if observed_pid != kickstart_pid:
                raise ProbeError("launchd-child-pid-disagrees")
        if status == 0 and error_code is None and secondary_error_code is None:
            _validate_disposable_home_root(
                plan.account,
                diagnostic_phase="post-child-terminal",
            )
    except ProbeError as error:
        error_code = error.code
        secondary_error_code = error.secondary_code
        status = 2
    finally:
        if plan is not None and bootstrap_attempted:
            try:
                _reconcile_in_process_bootstrap(plan, state)
                bootout_confirmed = True
            except ProbeError as error:
                error_code, secondary_error_code = _merge_probe_error(
                    error_code,
                    secondary_error_code,
                    error,
                )
                status = 2
    processes_absent = False
    if plan is not None and bootout_confirmed:
        try:
            _validate_disposable_home_root(
                plan.account,
                diagnostic_phase="post-system-bootout",
            )
        except ProbeError as error:
            error_code, secondary_error_code = _merge_probe_error(
                error_code,
                secondary_error_code,
                error,
            )
            status = 2
        try:
            current_bindings = _validate_exact_stage(
                plan,
                state,
                user_domain_reset_authorization=user_domain_reset_authorization,
                environment=os.environ,
            )
            reset_evidence = _quiesce_disposable_user(
                plan,
                state,
                current_bindings,
                user_domain_reset_authorization,
                os.environ,
                allow_create=(
                    status == 0 and error_code is None and secondary_error_code is None
                ),
            )
            processes_absent = True
        except ProbeError as error:
            error_code, secondary_error_code = _merge_probe_error(
                error_code,
                secondary_error_code,
                error,
            )
            status = 2
    if status == 2 and plan is not None and processes_absent:
        try:
            _ensure_failed_child_files(
                plan.account,
                os.environ,
                error_code or "lifecycle-incomplete",
                secondary_error_code,
            )
        except ProbeError:
            pass
    _write_lifecycle_artifact(
        artifact_root=artifact_root,
        plan=plan if processes_absent else None,
        binding=binding if processes_absent else None,
        environment=os.environ,
        loaded=loaded if processes_absent else "",
        terminal=terminal if processes_absent else "",
        kickstart_pid=kickstart_pid,
        status=status,
        error_code=error_code,
        secondary_error_code=secondary_error_code,
        domain_reset=reset_evidence,
    )
    if status == 2:
        print(
            "task-witness macOS launchd-user lifecycle: "
            f"{error_code or 'lifecycle-incomplete'}",
            file=sys.stderr,
        )
        if secondary_error_code is not None:
            print(
                "task-witness macOS launchd-user lifecycle secondary: "
                f"{secondary_error_code}",
                file=sys.stderr,
            )
    return status


def _load_lifecycle_state(
    stage_root: Path,
    *,
    runner_uid: int,
    runner_gid: int,
    environment: Mapping[str, str],
) -> tuple[LaunchdPlan, dict]:
    state_path = stage_root / "state.json"
    if not _root_file_matches(state_path, 0o600):
        raise ProbeError("lifecycle-state-drift")
    state = _load_canonical_document(state_path, 16 * 1024, "lifecycle-state")
    _require_content_digest(state, "lifecycle-state")
    expected_name, expected_label = _launchd_identity(environment)
    account_value = state.get("account")
    if not isinstance(account_value, dict):
        raise ProbeError("lifecycle-state-drift")
    try:
        account = DisposableAccount(
            name=str(account_value["name"]),
            uid=int(account_value["uid"]),
            gid=int(account_value["gid"]),
            home=Path(str(account_value["home"])),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProbeError("lifecycle-state-drift") from error
    plan = LaunchdPlan(
        account=account,
        label=expected_label,
        stage_root=stage_root,
        helper=stage_root / "helper.py",
        plist=stage_root / "job.plist",
    )
    if (
        set(state)
        != {
            "schema_version",
            "contract",
            "candidate_sha1",
            "account",
            "label",
            "stage_root",
            "helper_sha256",
            "plist_sha256",
            "ownership_marker",
            "runner_uid",
            "runner_gid",
            "content_sha256",
        }
        or set(account_value) != {"name", "uid", "gid", "home"}
        or state.get("schema_version") != 1
        or state.get("contract") != "task-witness-macos-launchd-lifecycle-state-v1"
        or state.get("candidate_sha1") != EXPECTED_CANDIDATE_SHA
        or state.get("label") != expected_label
        or state.get("stage_root") != str(stage_root)
        or state.get("runner_uid") != runner_uid
        or state.get("runner_gid") != runner_gid
        or not isinstance(state.get("ownership_marker"), str)
        or LAUNCHD_OWNERSHIP_MARKER_RE.fullmatch(state["ownership_marker"]) is None
        or account.name != expected_name
        or account.uid not in range(DISPOSABLE_UID_MIN, DISPOSABLE_UID_MAX + 1)
        or account.gid != runner_gid
        or account.home != Path("/Users") / expected_name
    ):
        raise ProbeError("lifecycle-state-drift")
    return plan, state


def _validate_exact_stage(
    plan: LaunchdPlan,
    state: Mapping[str, object],
    *,
    user_domain_reset_authorization: object = None,
    environment: Mapping[str, str] | None = None,
) -> ValidatedStageBindings:
    try:
        names = {entry.name for entry in plan.stage_root.iterdir()}
    except OSError as error:
        raise ProbeError("stage-cleanup-drift") from error
    base_names = {"helper.py", "job.plist", "state.json"}
    extra_names = names - base_names
    if (
        not base_names <= names
        or extra_names
        - {
            "account.json",
            "domain-reset.json",
            "home-cleanup.json",
            "ownership.json",
        }
        or (extra_names and "account.json" not in extra_names)
        or ("domain-reset.json" in extra_names and "ownership.json" not in extra_names)
        or not _directory_matches(plan.stage_root, 0o755, 0, 0)
        or not _root_file_matches(plan.helper, 0o555)
        or not _root_file_matches(plan.plist, 0o644)
    ):
        raise ProbeError("stage-cleanup-drift")
    helper = _read_stable_regular_file(plan.helper, MAX_HELPER_BYTES, "staged-helper")
    plist = _read_stable_regular_file(plan.plist, 64 * 1024, "staged-plist")
    if hashlib.sha256(helper).hexdigest() != state.get(
        "helper_sha256"
    ) or hashlib.sha256(plist).hexdigest() != state.get("plist_sha256"):
        raise ProbeError("stage-cleanup-drift")
    account = _load_account_binding(plan, state)
    ownership = _load_launchd_ownership_marker(plan, state)
    domain_reset: dict | None = None
    home_cleanup: dict | None = None
    if "domain-reset.json" in names:
        if account is None or ownership is None or environment is None:
            raise ProbeError("stage-cleanup-drift")
        domain_reset = _load_user_domain_reset_authorization(
            plan,
            state,
            account,
            ownership,
            user_domain_reset_authorization,
            environment,
        )
    if "home-cleanup.json" in names:
        if account is None:
            raise ProbeError("stage-cleanup-drift")
        home_cleanup = _load_home_cleanup_authorization(
            plan,
            state,
            ValidatedStageBindings(account, ownership, domain_reset),
        )
    if (
        ("account.json" in names) != (account is not None)
        or ("ownership.json" in names) != (ownership is not None)
        or ("domain-reset.json" in names) != (domain_reset is not None)
        or ("home-cleanup.json" in names) != (home_cleanup is not None)
        or (ownership is not None and account is None)
        or (domain_reset is not None and ownership is None)
        or (home_cleanup is not None and account is None)
    ):
        raise ProbeError("stage-cleanup-drift")
    return ValidatedStageBindings(
        account=account,
        launchd=ownership,
        domain_reset=domain_reset,
        home_cleanup=home_cleanup,
    )


def parse_process_list(raw: str) -> tuple[ProcessRecord, ...]:
    if len(raw.encode("utf-8")) > MAX_PROCESS_LIST_BYTES:
        raise ProbeError("process-list-too-large")
    result: list[ProcessRecord] = []
    observed_pids: set[int] = set()
    observed_pid_one = False
    for line in raw.splitlines():
        fields = line.split(maxsplit=5)
        if (
            len(fields) != 6
            or DSCL_UID_RE.fullmatch(fields[0]) is None
            or any(
                re.fullmatch(r"(?:0|[1-9][0-9]*)", item) is None for item in fields[1:4]
            )
            or not fields[4]
            or len(fields[4]) > 32
            or any(
                character.isspace() or not character.isascii()
                for character in fields[4]
            )
            or not fields[5]
            or len(fields[5].encode("utf-8")) > 128
            or any(
                ord(character) < 0x20 or ord(character) == 0x7F
                for character in fields[5]
            )
        ):
            raise ProbeError("process-list-invalid")
        uid, pid, ppid, pgid = (int(item) for item in fields[:4])
        if (
            not DSCL_UID_MIN <= uid <= (1 << 32) - 1
            or pid > DSCL_UID_MAX
            or ppid > DSCL_UID_MAX
            or pgid > DSCL_UID_MAX
            or (pid != 0 and pid == ppid)
            or pid in observed_pids
        ):
            raise ProbeError("process-list-invalid")
        observed_pids.add(pid)
        if uid == 0 and pid == 1:
            observed_pid_one = True
        result.append(
            ProcessRecord(
                uid=uid,
                pid=pid,
                ppid=ppid,
                pgid=pgid,
                state=fields[4],
                command=fields[5],
            )
        )
    if not result or not observed_pid_one:
        raise ProbeError("process-list-invalid")
    return tuple(result)


def process_occupied_uids(records: Sequence[ProcessRecord]) -> set[int]:
    if any(not isinstance(record, ProcessRecord) for record in records):
        raise ProbeError("process-list-invalid")
    return {record.uid for record in records}


def _process_records(
    *,
    timeout: float = COMMAND_TIMEOUT_SECONDS,
) -> tuple[ProcessRecord, ...]:
    return parse_process_list(
        _require_command_success(
            ["/bin/ps", "-axo", "uid=,pid=,ppid=,pgid=,state=,ucomm="],
            command_id="process-list",
            maximum=MAX_PROCESS_LIST_BYTES,
            timeout=timeout,
        )
    )


def _process_survivor_code(
    records: Sequence[ProcessRecord],
    uid: int,
) -> str | None:
    if (
        type(uid) is not int
        or not DISPOSABLE_UID_MIN <= uid <= DISPOSABLE_UID_MAX
        or any(not isinstance(record, ProcessRecord) for record in records)
    ):
        raise ProbeError("process-list-invalid")
    observed = [record for record in records if record.uid == uid]
    if not observed:
        return None
    if any(record.pid <= 0 for record in observed):
        raise ProbeError("process-list-invalid")
    by_pid = {item.pid: item for item in records}

    def family(record: ProcessRecord) -> str:
        command = Path(record.command).name.lower()
        parent = by_pid.get(record.ppid)
        parent_command = "" if parent is None else Path(parent.command).name.lower()
        if record.state.upper().startswith("Z"):
            return "zombie"
        if command in {"launchd", "cfprefsd", "distnoted"}:
            return "background"
        if command in {"python", "python3"} or command.startswith("python3."):
            return "probe"
        if command in {"mdworker", "mdworker_shared", "mdworker_sizing"} or (
            parent is not None
            and parent.uid == 0
            and parent_command in {"mds", "mds_stores"}
        ):
            return "spotlight"
        if record.ppid == 1:
            return "pid1"
        if parent is None:
            return "parent-unobserved"
        if parent.uid == 0:
            return "root-parent"
        if parent.uid != uid:
            return "other-uid-parent"
        return "same-uid"

    if len(observed) > 1:
        families = {family(record) for record in observed}
        if families == {"zombie"}:
            return "disposable-user-zombie-only-remains"
        if families == {"background"}:
            return "disposable-user-background-agent-names-remain"
        if families == {"spotlight"}:
            return "disposable-user-spotlight-workers-remain"
        if families == {"background", "spotlight"}:
            return "disposable-user-background-and-spotlight-names-remain"
        if "probe" in families:
            return "disposable-user-probe-and-other-processes-remain"
        if "same-uid" in families:
            return "disposable-user-same-uid-process-tree-remains"
        external_relations = families & {
            "other-uid-parent",
            "parent-unobserved",
            "pid1",
            "root-parent",
        }
        if len(external_relations) > 1:
            return "disposable-user-mixed-parent-topologies-remain"
        if external_relations:
            relation = next(iter(external_relations))
            return {
                "other-uid-parent": (
                    "disposable-user-other-uid-parented-processes-remain"
                ),
                "parent-unobserved": (
                    "disposable-user-parent-unobserved-processes-remain"
                ),
                "pid1": "disposable-user-pid1-parented-processes-remain",
                "root-parent": "disposable-user-root-parented-processes-remain",
            }[relation]
        if "spotlight" in families:
            return "disposable-user-spotlight-and-other-processes-remain"
        return "disposable-user-mixed-processes-remain"

    record = observed[0]
    command = Path(record.command).name.lower()
    if record.state.upper().startswith("Z"):
        return "disposable-user-zombie-only-remains"
    if command == "launchd":
        return "disposable-user-launchd-name-remains"
    if command == "cfprefsd":
        return "disposable-user-cfprefsd-name-remains"
    if command == "distnoted":
        return "disposable-user-distnoted-name-remains"
    if command in {"python", "python3"} or command.startswith("python3."):
        return "disposable-user-probe-name-remains"
    parent = by_pid.get(record.ppid)
    parent_command = "" if parent is None else Path(parent.command).name.lower()
    if command in {"mdworker", "mdworker_shared", "mdworker_sizing"} or (
        parent is not None
        and parent.uid == 0
        and parent_command in {"mds", "mds_stores"}
    ):
        return "disposable-user-spotlight-worker-remains"
    if record.ppid == 1:
        return "disposable-user-pid1-parented-process-remains"
    if parent is None:
        return "disposable-user-parent-unobserved-process-remains"
    if parent.uid == 0:
        return "disposable-user-root-parented-process-remains"
    if parent.uid != uid:
        return "disposable-user-other-uid-parented-process-remains"
    raise ProbeError("process-list-invalid")


def _require_disposable_uid_available(
    uid: int,
    *,
    active_code: str = "disposable-uid-active-before-create",
) -> None:
    if active_code not in DISPOSABLE_UID_ACTIVE_CODES:
        raise ProbeError("invalid-disposable-uid-active-code")
    if _process_survivor_code(_process_records(), uid) is not None:
        raise ProbeError(active_code)


def _require_no_uid_processes(
    uid: int,
    *,
    timeout: float = COMMAND_TIMEOUT_SECONDS,
) -> None:
    code = _process_survivor_code(_process_records(timeout=timeout), uid)
    if code is not None:
        raise ProbeError(code)


def _process_wait_timeout_code(observed_codes: set[str]) -> str:
    if len(observed_codes) == 1:
        return next(iter(observed_codes))
    if observed_codes:
        return "disposable-user-process-observation-unstable"
    return "disposable-user-process-observation-unavailable"


def _wait_for_no_uid_processes(
    uid: int,
) -> None:
    deadline = time.monotonic() + PROCESS_EXIT_POLL_TIMEOUT_SECONDS
    observed_codes: set[str] = set()
    while True:
        remaining = deadline - time.monotonic()
        if remaining < PROCESS_LIST_MIN_TIMEOUT_SECONDS:
            raise ProbeError(_process_wait_timeout_code(observed_codes))
        try:
            _require_no_uid_processes(
                uid,
                timeout=min(COMMAND_TIMEOUT_SECONDS, remaining),
            )
            return
        except ProbeError as error:
            if error.code not in DISPOSABLE_PROCESS_REMAINS_CODES:
                raise
            observed_codes.add(error.code)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeError(_process_wait_timeout_code(observed_codes))
        time.sleep(min(PROCESS_EXIT_POLL_INTERVAL_SECONDS, remaining))


def _require_stable_no_uid_processes(uid: int) -> None:
    _require_no_uid_processes(uid)
    time.sleep(PROCESS_EXIT_POLL_INTERVAL_SECONDS)
    _require_no_uid_processes(uid)


def _domain_reset_evidence(marker: Mapping[str, object] | None) -> dict[str, str]:
    if marker is None:
        return {
            "authorization_sha256": "none",
            "capability": USER_DOMAIN_RESET_CAPABILITY,
            "disposition": "not-needed",
            "precondition": "none",
        }
    digest = marker.get("content_sha256")
    if not _is_sha256(digest):
        raise ProbeError("user-domain-reset-authorization-drift")
    return {
        "authorization_sha256": digest,
        "capability": USER_DOMAIN_RESET_CAPABILITY,
        "disposition": "performed",
        "precondition": USER_DOMAIN_RESET_SURVIVOR_CODE,
    }


def _recovered_domain_reset_evidence(
    marker: Mapping[str, object],
) -> dict[str, str]:
    evidence = _domain_reset_evidence(marker)
    evidence["disposition"] = "recovered-to-stable-zero"
    return evidence


def _validated_domain_reset_evidence(value: object) -> dict[str, str]:
    evidence = _require_exact_keys(
        value,
        {
            "authorization_sha256",
            "capability",
            "disposition",
            "precondition",
        },
        "user-domain-reset-evidence",
    )
    disposition = evidence.get("disposition")
    authorization_sha256 = evidence.get("authorization_sha256")
    precondition = evidence.get("precondition")
    if (
        evidence.get("capability") != USER_DOMAIN_RESET_CAPABILITY
        or not isinstance(disposition, str)
        or disposition not in {"not-needed", "performed", "recovered-to-stable-zero"}
        or (
            disposition == "not-needed"
            and (authorization_sha256 != "none" or precondition != "none")
        )
        or (
            disposition in {"performed", "recovered-to-stable-zero"}
            and (
                not _is_sha256(authorization_sha256)
                or precondition != USER_DOMAIN_RESET_SURVIVOR_CODE
            )
        )
    ):
        raise ProbeError("invalid-user-domain-reset-evidence")
    return {
        "authorization_sha256": str(authorization_sha256),
        "capability": USER_DOMAIN_RESET_CAPABILITY,
        "disposition": str(disposition),
        "precondition": str(precondition),
    }


def _home_cleanup_evidence(
    authorization: Mapping[str, object] | None,
    *,
    recovered: bool,
) -> dict[str, str]:
    if authorization is None:
        return {
            "authorization_sha256": "none",
            "disposition": "not-needed",
        }
    digest = authorization.get("content_sha256")
    if not _is_sha256(digest):
        raise ProbeError("home-cleanup-authorization-drift")
    return {
        "authorization_sha256": digest,
        "disposition": "recovered" if recovered else "performed",
    }


def _validated_home_cleanup_evidence(value: object) -> dict[str, str]:
    evidence = _require_exact_keys(
        value,
        {"authorization_sha256", "disposition"},
        "home-cleanup-evidence",
    )
    authorization_sha256 = evidence.get("authorization_sha256")
    disposition = evidence.get("disposition")
    if (
        not isinstance(disposition, str)
        or disposition not in {"not-needed", "performed", "recovered"}
        or (disposition == "not-needed" and authorization_sha256 != "none")
        or (
            disposition in {"performed", "recovered"}
            and (not _is_sha256(authorization_sha256))
        )
    ):
        raise ProbeError("invalid-home-cleanup-evidence")
    return {
        "authorization_sha256": str(authorization_sha256),
        "disposition": str(disposition),
    }


def _cleanup_domain_reset_evidence(
    artifact_root: Path,
    plan: LaunchdPlan,
    state: Mapping[str, object],
    marker: Mapping[str, object] | None,
    *,
    force_recovered: bool = False,
) -> dict[str, str]:
    if marker is None:
        return _domain_reset_evidence(None)
    performed = _domain_reset_evidence(marker)
    recovered = _recovered_domain_reset_evidence(marker)
    if force_recovered:
        return recovered
    try:
        lifecycle = _load_canonical_document(
            artifact_root / "lifecycle.json",
            LAUNCHD_ARTIFACT_FILES["lifecycle.json"],
            "launchd-lifecycle",
        )
        _require_content_digest(lifecycle, "launchd-lifecycle")
        _require_exact_keys(
            lifecycle,
            {
                "binding",
                "candidate_sha1",
                "content_sha256",
                "contract",
                "disposition",
                "domain_reset",
                "kickstart_pid",
                "label",
                "probe_disposition",
                "schema_version",
            },
            "launchd-lifecycle",
        )
        binding = _validated_launchd_binding_evidence(lifecycle.get("binding"))
        if (
            lifecycle.get("schema_version") != 2
            or lifecycle.get("contract") != "task-witness-macos-launchd-lifecycle-v2"
            or lifecycle.get("candidate_sha1") != EXPECTED_CANDIDATE_SHA
            or lifecycle.get("label") != plan.label
            or type(lifecycle.get("kickstart_pid")) is not int
            or lifecycle["kickstart_pid"] <= 0
            or lifecycle.get("probe_disposition") != "launchd-user-eligible"
            or lifecycle.get("disposition") != "launchd-user-eligible"
            or binding.get("ownership_marker") != state.get("ownership_marker")
            or _validated_domain_reset_evidence(lifecycle.get("domain_reset"))
            != performed
        ):
            return recovered
    except ProbeError:
        return recovered
    return performed


def _validate_reset_bindings_and_resources(
    plan: LaunchdPlan,
    state: Mapping[str, object],
    marker: Mapping[str, object],
    authorization_sha: object,
    environment: Mapping[str, str],
    home_cleanup: Mapping[str, object] | None = None,
    *,
    home_drift_phase: str,
) -> tuple[bool, bool]:
    _require_launchd_absent(plan.label)
    bindings = _validate_exact_stage(
        plan,
        state,
        user_domain_reset_authorization=authorization_sha,
        environment=environment,
    )
    if (
        bindings.domain_reset != marker
        or bindings.home_cleanup != home_cleanup
        or bindings.account is None
    ):
        raise ProbeError("user-domain-reset-authorization-drift")
    account_value = bindings.account.get("account")
    if not isinstance(account_value, dict):
        raise ProbeError("account-binding-drift")
    account_present = _account_exists(plan.account.name)
    accounts = _list_accounts()
    if account_present:
        if (
            accounts.get(plan.account.name) != plan.account.uid
            or sum(
                observed_uid == plan.account.uid for observed_uid in accounts.values()
            )
            != 1
        ):
            raise ProbeError("account-record-drift")
        if account_value.get("generated_uid") != _validated_system_generated_uid(
            _account_record(plan.account)
        ):
            raise ProbeError("account-record-generated-uid-drift")
    elif plan.account.name in accounts or plan.account.uid in accounts.values():
        raise ProbeError("account-record-drift")
    marker_home_arguments = {
        "diagnostic_phase": home_drift_phase,
    }
    if home_cleanup is not None and "library_inventory" in home_cleanup:
        marker_home_arguments["library_inventory"] = home_cleanup.get(
            "library_inventory"
        )
    home_present, probe_present, remaining_home_files = (
        _validated_marker_bound_disposable_home(
            plan.account,
            marker.get("home_identity"),
            **marker_home_arguments,
        )
    )
    if (
        account_present
        and home_present
        and (not probe_present or set(remaining_home_files) != set(LAUNCHD_CHILD_FILES))
    ):
        raise _home_cleanup_drift_error(
            home_drift_phase,
            "probe-entry-set-drift",
        )
    home_complete = (
        home_present
        and probe_present
        and set(remaining_home_files) == set(LAUNCHD_CHILD_FILES)
    )
    if not home_complete and home_cleanup is None:
        raise _home_cleanup_drift_error(
            home_drift_phase,
            "home-state-drift",
        )
    if home_cleanup is not None and account_present:
        raise ProbeError("account-record-drift")
    return account_present, home_present


def _quiesce_disposable_user(
    plan: LaunchdPlan,
    state: Mapping[str, object],
    bindings: ValidatedStageBindings,
    authorization_sha: object,
    environment: Mapping[str, str],
    *,
    allow_create: bool,
) -> dict[str, str]:
    if bindings.home_cleanup is not None:
        bindings = bindings._replace(
            home_cleanup=_require_durable_home_cleanup_authorization(
                plan,
                state,
                bindings,
                bindings.home_cleanup,
            )
        )
    marker = bindings.domain_reset
    if marker is not None:
        marker = _require_durable_user_domain_reset_authorization(
            plan,
            state,
            bindings,
            authorization_sha,
            environment,
            marker,
        )
        _validate_reset_bindings_and_resources(
            plan,
            state,
            marker,
            authorization_sha,
            environment,
            bindings.home_cleanup,
            home_drift_phase="marker-replay",
        )
    try:
        _wait_for_no_uid_processes(plan.account.uid)
    except ProbeError as error:
        if error.code != USER_DOMAIN_RESET_SURVIVOR_CODE:
            raise
        if marker is None:
            if not allow_create:
                raise
            marker = _write_user_domain_reset_authorization(
                plan,
                state,
                bindings,
                authorization_sha,
                environment,
            )
        _validate_reset_bindings_and_resources(
            plan,
            state,
            marker,
            authorization_sha,
            environment,
            bindings.home_cleanup,
            home_drift_phase="pre-reset",
        )
        _run_user_domain_reset(plan.account.uid)
        _wait_for_no_uid_processes(plan.account.uid)
        _require_stable_no_uid_processes(plan.account.uid)
        _validate_reset_bindings_and_resources(
            plan,
            state,
            marker,
            authorization_sha,
            environment,
            bindings.home_cleanup,
            home_drift_phase="post-reset",
        )
        return _domain_reset_evidence(marker)
    if marker is not None:
        _require_stable_no_uid_processes(plan.account.uid)
        _validate_reset_bindings_and_resources(
            plan,
            state,
            marker,
            authorization_sha,
            environment,
            bindings.home_cleanup,
            home_drift_phase="marker-replay",
        )
    return _domain_reset_evidence(marker)


def _validate_precleanup_artifact(artifact_root: Path) -> None:
    try:
        names = {entry.name for entry in artifact_root.iterdir()}
    except OSError as error:
        raise ProbeError("launchd-artifact-root-unreadable") from error
    expected = set(LAUNCHD_ARTIFACT_FILES) - {"cleanup.json"}
    if names != expected or not _directory_matches(artifact_root, 0o700, 0, 0):
        raise ProbeError("launchd-artifact-precleanup-drift")
    for name in expected:
        if not _root_file_matches(artifact_root / name, 0o600):
            raise ProbeError("launchd-artifact-precleanup-drift")


def cleanup_launchd_user_lifecycle(
    *,
    stage_root: Path,
    artifact_root: Path,
    expected_helper_sha256: str,
    runner_uid: int,
    runner_gid: int,
    user_domain_reset_authorization: str | None = None,
) -> int:
    _normalized_context(os.environ)
    _validate_lifecycle_arguments(
        source_helper=Path(__file__),
        stage_root=stage_root,
        artifact_root=artifact_root,
        runner_uid=runner_uid,
        runner_gid=runner_gid,
    )
    try:
        if _cleanup_helper_only_stage_before_state(
            stage_root=stage_root,
            artifact_root=artifact_root,
            expected_helper_sha256=expected_helper_sha256,
            environment=os.environ,
        ):
            return 2
    except ProbeError as error:
        print(
            f"task-witness macOS launchd-user cleanup: {error.code}",
            file=sys.stderr,
        )
        return 2
    error_code: str | None = None
    secondary_error_code: str | None = None
    try:
        plan, state = _load_lifecycle_state(
            stage_root,
            runner_uid=runner_uid,
            runner_gid=runner_gid,
            environment=os.environ,
        )
        stage_bindings = _validate_exact_stage(
            plan,
            state,
            user_domain_reset_authorization=user_domain_reset_authorization,
            environment=os.environ,
        )
        _reconcile_owned_launchd_job(plan, state, stage_bindings.launchd)
        account_present = _account_exists(plan.account.name)
        home_present = _path_exists_no_follow(plan.account.home)
        expected_generated_uid: str | None = None
        if account_present or home_present:
            if stage_bindings.account is None:
                raise ProbeError("account-binding-missing")
            account_value = stage_bindings.account.get("account")
            if not isinstance(account_value, dict) or not isinstance(
                account_value.get("generated_uid"), str
            ):
                raise ProbeError("account-binding-drift")
            expected_generated_uid = account_value["generated_uid"]
        if account_present and (
            _read_system_generated_uid(plan.account.name) != expected_generated_uid
        ):
            raise ProbeError("account-record-generated-uid-drift")
        if stage_bindings.home_cleanup is not None and account_present:
            raise ProbeError("account-record-drift")
        if home_present:
            if stage_bindings.home_cleanup is not None:
                _validated_marker_bound_disposable_home(
                    plan.account,
                    stage_bindings.home_cleanup.get("home_identity"),
                    diagnostic_phase="cleanup-entry",
                    library_inventory=stage_bindings.home_cleanup.get(
                        "library_inventory"
                    ),
                )
            elif stage_bindings.domain_reset is None:
                _validate_exact_disposable_home(
                    plan.account.home,
                    expected_uid=plan.account.uid,
                    expected_gid=plan.account.gid,
                    diagnostic_phase="cleanup-entry",
                )
        home_cleanup_recovered = stage_bindings.home_cleanup is not None or (
            stage_bindings.account is not None and not account_present
        )
        reset_evidence = _domain_reset_evidence(stage_bindings.domain_reset)
        owned_user_state = (
            stage_bindings.account is not None or account_present or home_present
        )
        if owned_user_state:
            reset_evidence = _quiesce_disposable_user(
                plan,
                state,
                stage_bindings,
                user_domain_reset_authorization,
                os.environ,
                allow_create=False,
            )
        if stage_bindings.domain_reset is not None:
            account_present, home_present = _validate_reset_bindings_and_resources(
                plan,
                state,
                stage_bindings.domain_reset,
                user_domain_reset_authorization,
                os.environ,
                stage_bindings.home_cleanup,
                home_drift_phase="pre-account-delete",
            )
        if owned_user_state:
            _require_no_uid_processes(plan.account.uid)
        if account_present:
            if stage_bindings.domain_reset is not None:
                current_account_present, home_present = (
                    _validate_reset_bindings_and_resources(
                        plan,
                        state,
                        stage_bindings.domain_reset,
                        user_domain_reset_authorization,
                        os.environ,
                        stage_bindings.home_cleanup,
                        home_drift_phase="pre-account-delete",
                    )
                )
                if not current_account_present:
                    raise ProbeError("account-record-drift")
            else:
                current_accounts = _list_accounts()
                current_uid = current_accounts.get(plan.account.name)
                if (
                    (
                        stage_bindings.launchd is not None
                        and current_uid != plan.account.uid
                    )
                    or (current_uid is not None and current_uid != plan.account.uid)
                    or any(
                        name != plan.account.name and uid == plan.account.uid
                        for name, uid in current_accounts.items()
                    )
                ):
                    raise ProbeError("account-record-drift")
                if (
                    _read_system_generated_uid(plan.account.name)
                    != expected_generated_uid
                ):
                    raise ProbeError("account-record-generated-uid-drift")
            _require_command_success(
                ["/usr/bin/dscl", ".", "-delete", f"/Users/{plan.account.name}"],
                command_id="account-delete",
            )
            if _account_exists(plan.account.name):
                raise ProbeError("account-delete-disagrees")
            _require_no_uid_processes(plan.account.uid)
        remaining_accounts = _list_accounts()
        if (
            plan.account.name in remaining_accounts
            or plan.account.uid in remaining_accounts.values()
        ):
            raise ProbeError(
                "account-delete-disagrees"
                if account_present
                else "account-record-drift"
            )
        if owned_user_state:
            _require_stable_no_uid_processes(plan.account.uid)
            current_accounts = _list_accounts()
            if (
                plan.account.name in current_accounts
                or plan.account.uid in current_accounts.values()
            ):
                raise ProbeError("account-record-drift")
        if home_present and stage_bindings.home_cleanup is None:
            home_identity = _exact_disposable_home_identity(
                plan.account,
                diagnostic_phase="pre-journal",
            )
            if (
                stage_bindings.domain_reset is not None
                and home_identity
                != _validated_disposable_home_identity(
                    stage_bindings.domain_reset.get("home_identity")
                )
            ):
                raise _home_cleanup_drift_error(
                    "pre-journal",
                    "home-identity-drift",
                )
            stage_bindings = stage_bindings._replace(
                home_cleanup=_write_home_cleanup_authorization(
                    plan,
                    state,
                    stage_bindings,
                    home_identity,
                )
            )
        elif stage_bindings.home_cleanup is not None:
            stage_bindings = stage_bindings._replace(
                home_cleanup=_require_durable_home_cleanup_authorization(
                    plan,
                    state,
                    stage_bindings,
                    stage_bindings.home_cleanup,
                )
            )
        if stage_bindings.home_cleanup is not None:
            _require_stable_no_uid_processes(plan.account.uid)
            current_accounts = _list_accounts()
            if (
                plan.account.name in current_accounts
                or plan.account.uid in current_accounts.values()
            ):
                raise ProbeError("account-record-drift")
            marker_home_present, probe_present, remaining_home_files = (
                _validated_marker_bound_disposable_home(
                    plan.account,
                    stage_bindings.home_cleanup.get("home_identity"),
                    diagnostic_phase="pre-home-removal",
                    library_inventory=stage_bindings.home_cleanup.get(
                        "library_inventory"
                    ),
                )
            )
            home_cleanup_recovered = home_cleanup_recovered or (
                marker_home_present
                and (
                    not probe_present
                    or set(remaining_home_files) != set(LAUNCHD_CHILD_FILES)
                )
            )
            if marker_home_present:
                _remove_marker_bound_disposable_home(
                    plan.account,
                    stage_bindings.home_cleanup.get("home_identity"),
                    stage_bindings.home_cleanup,
                )
        reset_evidence = _cleanup_domain_reset_evidence(
            artifact_root,
            plan,
            state,
            stage_bindings.domain_reset,
        )
        home_cleanup_evidence = _home_cleanup_evidence(
            stage_bindings.home_cleanup,
            recovered=home_cleanup_recovered,
        )
        try:
            stage_names = ["helper.py", "job.plist", "state.json"]
            if stage_bindings.account is not None:
                stage_names.append("account.json")
            if stage_bindings.launchd is not None:
                stage_names.append("ownership.json")
            if stage_bindings.domain_reset is not None:
                stage_names.append("domain-reset.json")
            if stage_bindings.home_cleanup is not None:
                stage_names.append("home-cleanup.json")
            for name in stage_names:
                (stage_root / name).unlink()
            stage_root.rmdir()
        except OSError as error:
            raise ProbeError("stage-cleanup-failed") from error
        _validate_precleanup_artifact(artifact_root)
        cleanup = _document_with_digest(
            {
                "schema_version": 3,
                "contract": "task-witness-macos-launchd-cleanup-v3",
                "candidate_sha1": EXPECTED_CANDIDATE_SHA,
                "account": plan.account.name,
                "label": plan.label,
                "disposition": "cleaned",
                "domain_reset": _validated_domain_reset_evidence(reset_evidence),
                "home_cleanup": _validated_home_cleanup_evidence(home_cleanup_evidence),
            }
        )
        _write_root_file(
            artifact_root / "cleanup.json", canonical_bytes(cleanup), 0o600
        )
        launchd_artifact_payloads(artifact_root, include_manifest=False)
        try:
            for name in LAUNCHD_ARTIFACT_FILES:
                os.chown(
                    artifact_root / name,
                    runner_uid,
                    runner_gid,
                    follow_symlinks=False,
                )
            os.chown(artifact_root, runner_uid, runner_gid, follow_symlinks=False)
        except OSError as error:
            raise ProbeError("artifact-transfer-failed") from error
        return 0
    except ProbeError as error:
        error_code = error.code
        secondary_error_code = error.secondary_code
    try:
        if _directory_matches(
            artifact_root, 0o700, 0, 0
        ) and not _path_exists_no_follow(artifact_root / "cleanup.json"):
            cleanup = _document_with_digest(
                {
                    "schema_version": 2,
                    "contract": "task-witness-macos-launchd-cleanup-v2",
                    "candidate_sha1": EXPECTED_CANDIDATE_SHA,
                    "disposition": "preserved-on-drift",
                    "error": _validated_probe_error(
                        error_code,
                        secondary_error_code,
                    ),
                }
            )
            _write_root_file(
                artifact_root / "cleanup.json",
                canonical_bytes(cleanup),
                0o600,
            )
    except ProbeError:
        pass
    print(
        f"task-witness macOS launchd-user cleanup: {error_code}",
        file=sys.stderr,
    )
    if secondary_error_code is not None:
        print(
            "task-witness macOS launchd-user cleanup secondary: "
            f"{secondary_error_code}",
            file=sys.stderr,
        )
    return 2


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


def _validate_launchd_child_entry_home(
    output: Path,
    status_output: Path,
) -> None:
    account_name, label = _launchd_identity(os.environ)
    home = Path("/Users") / account_name
    ownership_marker = _require_string(
        os.environ.get("TASK_WITNESS_LAUNCHD_OWNERSHIP_MARKER"),
        "launchd-ownership-marker",
        64,
    )
    _require_exact_launchd_child_environment(
        os.environ,
        label=label,
        home=home,
        ownership_marker=ownership_marker,
    )
    probe_root = home / "launchd-probe"
    if (
        output != probe_root / "probe.json"
        or status_output != probe_root / "probe.status"
    ):
        raise ProbeError("invalid-launchd-output-paths")
    _validate_disposable_home_root(
        DisposableAccount(
            name=account_name,
            uid=os.geteuid(),
            gid=os.getegid(),
            home=home,
        ),
        diagnostic_phase="child-entry",
    )


def run_launchd_user_probe(
    output: Path,
    status_output: Path,
    candidate_sha: str,
) -> int:
    if (
        output.parent != status_output.parent
        or output.name != "probe.json"
        or status_output.name != "probe.status"
    ):
        raise ProbeError("invalid-launchd-output-paths")
    _normalized_context(os.environ)
    status = 0
    try:
        _validate_launchd_child_entry_home(output, status_output)
        observations = collect_launchd_observations()
        document = build_launchd_user_probe_document(
            candidate_sha,
            os.environ,
            observations,
        )
        if document["disposition"] != "launchd-user-eligible":
            status = 1
    except ProbeError as error:
        document = build_launchd_user_probe_error_document(
            candidate_sha,
            os.environ,
            error.code,
        )
        status = 2
    write_create_new(output, canonical_bytes(document))
    write_create_new(status_output, f"{status}\n".encode("ascii"))
    print(document["disposition"])
    if status == 2:
        print(
            f"task-witness macOS launchd-user probe: {document['error']['code']}",
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

    launchd_probe = subparsers.add_parser("probe-launchd-user")
    launchd_probe.add_argument("--candidate-sha", required=True)
    launchd_probe.add_argument("--output", required=True, type=Path)
    launchd_probe.add_argument("--status-output", required=True, type=Path)

    provisioner = subparsers.add_parser("verify-provisioner")
    provisioner.add_argument("--candidate-sha", required=True)
    provisioner.add_argument("--artifact-root", required=True, type=Path)

    initialize = subparsers.add_parser("initialize-launchd-user-lifecycle")
    initialize.add_argument("--candidate-sha", required=True)
    initialize.add_argument("--expected-helper-sha256", required=True)
    initialize.add_argument("--stage-root", required=True, type=Path)
    initialize.add_argument("--artifact-root", required=True, type=Path)
    initialize.add_argument("--runner-uid", required=True, type=int)
    initialize.add_argument("--runner-gid", required=True, type=int)

    lifecycle = subparsers.add_parser("run-launchd-user-lifecycle")
    lifecycle.add_argument("--candidate-sha", required=True)
    lifecycle.add_argument("--stage-root", required=True, type=Path)
    lifecycle.add_argument("--artifact-root", required=True, type=Path)
    lifecycle.add_argument("--runner-uid", required=True, type=int)
    lifecycle.add_argument("--runner-gid", required=True, type=int)
    lifecycle.add_argument("--user-domain-reset-authorization", required=True)

    cleanup = subparsers.add_parser("cleanup-launchd-user-lifecycle")
    cleanup.add_argument("--expected-helper-sha256", required=True)
    cleanup.add_argument("--stage-root", required=True, type=Path)
    cleanup.add_argument("--artifact-root", required=True, type=Path)
    cleanup.add_argument("--runner-uid", required=True, type=int)
    cleanup.add_argument("--runner-gid", required=True, type=int)
    cleanup.add_argument("--user-domain-reset-authorization", required=True)

    launchd_manifest = subparsers.add_parser("write-launchd-artifact-manifest")
    launchd_manifest.add_argument("--artifact-root", required=True, type=Path)
    launchd_manifest.add_argument("--output", required=True, type=Path)

    verify_launchd_manifest = subparsers.add_parser("verify-launchd-artifact-manifest")
    verify_launchd_manifest.add_argument(
        "--artifact-root",
        required=True,
        type=Path,
    )
    verify_launchd_manifest.add_argument("--manifest", required=True, type=Path)

    launchd_success = subparsers.add_parser("verify-launchd-success")
    launchd_success.add_argument("--artifact-root", required=True, type=Path)
    launchd_success.add_argument("--manifest", required=True, type=Path)
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
        if args.command == "probe-launchd-user":
            _prepare_launchd_child_environment(Path(__file__))
            return run_launchd_user_probe(
                args.output,
                args.status_output,
                args.candidate_sha,
            )
        if args.command == "verify-provisioner":
            if args.candidate_sha != EXPECTED_CANDIDATE_SHA:
                raise ProbeError("candidate-sha-disagrees")
            verify_provisioning_capability(args.artifact_root)
            return 0
        if args.command == "initialize-launchd-user-lifecycle":
            return initialize_launchd_user_lifecycle(
                stage_root=args.stage_root,
                artifact_root=args.artifact_root,
                candidate_sha=args.candidate_sha,
                expected_helper_sha256=args.expected_helper_sha256,
                runner_uid=args.runner_uid,
                runner_gid=args.runner_gid,
            )
        if args.command == "run-launchd-user-lifecycle":
            return run_launchd_user_lifecycle(
                stage_root=args.stage_root,
                artifact_root=args.artifact_root,
                candidate_sha=args.candidate_sha,
                runner_uid=args.runner_uid,
                runner_gid=args.runner_gid,
                user_domain_reset_authorization=(args.user_domain_reset_authorization),
            )
        if args.command == "cleanup-launchd-user-lifecycle":
            return cleanup_launchd_user_lifecycle(
                stage_root=args.stage_root,
                artifact_root=args.artifact_root,
                expected_helper_sha256=args.expected_helper_sha256,
                runner_uid=args.runner_uid,
                runner_gid=args.runner_gid,
                user_domain_reset_authorization=(args.user_domain_reset_authorization),
            )
        if args.command == "write-launchd-artifact-manifest":
            write_launchd_artifact_manifest(args.artifact_root, args.output)
            return 0
        if args.command == "verify-launchd-artifact-manifest":
            verify_launchd_artifact_manifest(args.artifact_root, args.manifest)
            return 0
        if args.command == "verify-launchd-success":
            if args.manifest != args.artifact_root / "SHA256SUMS":
                raise ProbeError("launchd-manifest-path-disagrees")
            verify_launchd_success(args.artifact_root)
            return 0
        raise ProbeError("unsupported-command")
    except ProbeError as error:
        print(f"task-witness macOS host probe: {error.code}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
