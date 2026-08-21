#!/usr/bin/env python3
"""Validate the coordinated-release source-skill lineage evidence."""

from __future__ import annotations

import argparse
import datetime
import fcntl
import hashlib
import json
import math
import os
import posixpath
import re
import stat
import sys
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

LINEAGE_ROOT = Path("release/source-skill-lineage")
SOURCE_MANIFEST = LINEAGE_ROOT / "source-manifest.json"
CONTRIBUTION_LEDGER = LINEAGE_ROOT / "contribution-ledger.json"
HOST_MANIFESTS = {
    "initial-personal-cachyos-v1": LINEAGE_ROOT
    / "installed-hosts/initial-personal-cachyos-v1.json",
    "initial-work-macos-v1": LINEAGE_ROOT
    / "installed-hosts/initial-work-macos-v1.json",
}
HOST_DISCOVERY_PRECEDENCE = {
    "initial-personal-cachyos-v1": {
        "evidence_needed": [
            "restored read-only transport",
            "active discovery precedence receipt",
        ],
        "reason": (
            "The configured read-only route was unavailable, so active "
            "discovery precedence was not observed."
        ),
        "status": "unresolved",
    },
    "initial-work-macos-v1": {
        "evidence_needed": ["active discovery precedence receipt"],
        "reason": (
            "Installed source presence was captured, but active discovery "
            "precedence was not."
        ),
        "status": "unresolved",
    },
}
HOST_DISCOVERY_PRECEDENCE_REASON_CODES = {
    "initial-personal-cachyos-v1": "transport-unavailable",
    "initial-work-macos-v1": "not-observed",
}
HOST_EVIDENCE_INVENTORY_SHA256 = {
    "initial-personal-cachyos-v1": (
        "sha256:e91588a15cb49a6efefa7f5d6d71c1af95b34e1ffb0d5b7aa60915fed5d5445a"
    ),
    "initial-work-macos-v1": (
        "sha256:b15968e4367a44568aa31ec19ae73812376655d68dc4e8407ccb09a151887dac"
    ),
}
LINEAGE_ARTIFACTS = (
    SOURCE_MANIFEST,
    CONTRIBUTION_LEDGER,
    *HOST_MANIFESTS.values(),
)
RESEARCH_REPORT = Path(
    "docs/superpowers/research/2026-08-18-source-skill-lineage-and-drift.md"
)
RESEARCH_REPORT_SHA256 = (
    "sha256:fd27baa90363c27cedd3a1ffeb0734aa01d207535874dd02d2d6ad9f92240dff"
)
LINEAGE_TREE = {
    LINEAGE_ROOT / "installed-hosts",
    *LINEAGE_ARTIFACTS,
}
DISTRIBUTIONS = (
    "artifact-customs",
    "mergecraft",
    "rolecasting",
    "task-witness",
    "tricritical",
    "versionkeeping",
)
CANDIDATE_COMMIT_SHA1 = "8ec465ea915c6759a3693ac8515f0ee3901b8a4f"
CANDIDATE_COMMITTED_AT_UTC = "2026-08-21T10:28:21Z"
CANDIDATE_REFRESHED_AT_UTC = "2026-08-21T13:22:47Z"
CANDIDATE_TREE_SHA1 = "acd75254067861dc33ef4a754734138ae3c37af3"
CANDIDATE_REPORT_NAMES = {
    "artifact-customs": "Artifact Customs",
    "mergecraft": "Mergecraft",
    "rolecasting": "Rolecasting",
    "task-witness": "Task Witness",
    "tricritical": "Tricritical",
    "versionkeeping": "Versionkeeping",
}
RESEARCH_OBSERVED_AT_UTC = "2026-08-18T06:07:48Z"
CANDIDATE_PACKAGE_GIT_TREES = {
    "artifact-customs": "f874b4d7ee36c49ac09098fcacf7fa9c2601d1e4",
    "mergecraft": "82ef6a75c03d2794ea6c792dc348903a9ddfe51c",
    "rolecasting": "d4cdbaee1642cc0c388e49410e5332e3a74be1d1",
    "task-witness": "4ce4650fe524dfa817e715715bd7528639e0de04",
    "tricritical": "db58756a31a98fc8be81a226230a1eb9cef02df6",
    "versionkeeping": "c46a89caa6a6bc842fe69eb2ff0037df1d75832c",
}
CANDIDATE_PACKAGE_RECEIPTS = {
    "artifact-customs": (
        24,
        76474,
        "sha256:29abf2c8904e5b204fd5b2eace59213f5407cd2bfed269ae6e8459d6bfd921e4",
        "sha256:edbc33028f72571b4dd913e5d1eb53388f2071b9216bc69021b1ba4888d5be81",
        "1.0.0",
        (
            (
                "release/plugin-content-locks/artifact-customs.json",
                "sha256:f9ae3a737f9ec7bae29df9926c5076d5a46e32b382a94800e48ae1ff57b532bd",
            ),
        ),
    ),
    "mergecraft": (
        64,
        540847,
        "sha256:b9a7e2114e443c919c4a6a6e965744046375cb96193bef1e20f9417c2ae28251",
        "sha256:799c181398e7eef8e2ec09e9fc158d94a78ca41d5e44ea27e608da4d0147f667",
        "1.0.0",
        (
            (
                "release/plugin-content-locks/mergecraft.json",
                "sha256:cb51585ea1cd1693bd7fb57b164ba414841e87e8ba025b01f6461e9f4fd1253f",
            ),
        ),
    ),
    "rolecasting": (
        43,
        138238,
        "sha256:2638d4f2ba2023f98499510242c9a3c226dd20a694b2cef32c1bc83761d5cb54",
        "sha256:ab2726e06bada2c28d3bc98ae0e885e8a7dfd6fe72df242e351a7ce529b902d0",
        "1.0.0",
        (
            (
                "plugins/rolecasting/content-lock.json",
                "sha256:bb326245f255189c2516696b5b9f17ae0e4dafe296c5d02daaf2e09b9c642ade",
            ),
        ),
    ),
    "task-witness": (
        13,
        1335867,
        "sha256:17b468cc4ca71075f20f40f334d342171ad7564e604df48d48b271ce932d6d85",
        "sha256:5be0a7bd385a4abe6cdfc5d0b3f945bc1a59394637e84c9f5f2e6b310aa95213",
        "1.0.0",
        (
            (
                "release/task-witness/source-shape-review.json",
                "sha256:e9c62bf269301c610ef115c0c09136fc7553be8f663818632e1bae018952cb76",
            ),
            (
                "release/task-witness/tw4-suite-inventory.json",
                "sha256:985e316e7b3d75f07f91a7bd766414f092340ee1006d57081a887026b1da37cd",
            ),
        ),
    ),
    "tricritical": (
        113,
        182135,
        "sha256:f80de19383224291a2596a129ff2e2fc77f74916da0aa1da2a18ba11c067c609",
        "sha256:d4c12cd78b0631e1ea171b5254b5661d9d13f198bca28c4dd2d56d1744b982b7",
        "1.0.0",
        (
            (
                "plugins/tricritical/content-lock.json",
                "sha256:910fea3dba7505b49d640f91a4a7b401af9718eab4924dcf9596e141f8a5715d",
            ),
        ),
    ),
    "versionkeeping": (
        28,
        340115,
        "sha256:0ffc4575a0ffe15373f2eba555a044175ee21e19035bf1fcafbe13c6d47be369",
        "sha256:6fde278f2e58631c3873eb1c71007f71e3bbfa7ea8e3624c8547fde9ee477890",
        "1.0.0",
        (
            (
                "release/plugin-content-locks/versionkeeping.json",
                "sha256:34a4d32b45b103b92e6cef5810d50d8a095841d1c7a12b815aa8659feef78482",
            ),
        ),
    ),
}
SOURCE_FAMILIES = (
    "ivan-authored",
    "matt-pocock",
    "other",
    "retained-specialist",
    "superpowers",
)
SOURCE_EVIDENCE_INVENTORY_SHA256 = (
    "sha256:648c043304b03df238a9c89b15e2eb89197bea37ebceb3e9c9821f123fe1ae93"
)
CONTRIBUTION_EVIDENCE_INVENTORY_SHA256 = (
    "sha256:39f38d726521bb242e7694f18c74432998f2c2670241e7b4e3d1fbb13b2182f1"
)
CONTRIBUTION_EVIDENCE_RECEIPTS = {
    "evals/mergecraft/retirement-fixtures.json": (
        "sha256:c27b4ef2c6772c8e5d0284631f4e32c62974af57eb79c679d990bfb67835578c"
    ),
    "plugins/artifact-customs/plugin.json": (
        "sha256:edbc33028f72571b4dd913e5d1eb53388f2071b9216bc69021b1ba4888d5be81"
    ),
    "plugins/artifact-customs/skills/adopting-third-party-components/SKILL.md": (
        "sha256:1c620f8f5e64dfcc657ab5065d7ec519d82aa554123ce501e06350aa87b8ed2c"
    ),
    "plugins/mergecraft/README.md": (
        "sha256:d675a364f47beff022d931b121a766770de06f629eb4a9033f6885ffdac3e8ef"
    ),
    "plugins/mergecraft/plugin.json": (
        "sha256:799c181398e7eef8e2ec09e9fc158d94a78ca41d5e44ea27e608da4d0147f667"
    ),
    "plugins/mergecraft/skills/addressing-pr-review-feedback/SKILL.md": (
        "sha256:ff329cec02576944f7985b23d8cac15dd342085cc49e3a0fdc3ff8743dcc7d3c"
    ),
    "plugins/mergecraft/skills/getting-prs-merged/references/gh-fix-ci-adapter.md": (
        "sha256:18d47225b110317293710d4b6ef79415f1277568f0abc10a3b53133bb46edb8c"
    ),
    "plugins/mergecraft/skills/getting-prs-ready-for-review/SKILL.md": (
        "sha256:f97e8e1bef1b4a3e3aa4bc667a24711971ce9b8149d816dc10a0289e7afc17a5"
    ),
    "plugins/mergecraft/skills/interacting-with-pr-review-feedback/SKILL.md": (
        "sha256:10a71e605a954040b374f26640c0efafbf195fa6e733ac959ae36943871f05a9"
    ),
    "plugins/mergecraft/skills/publishing-reviewable-prs/SKILL.md": (
        "sha256:786bf63a0fc2cf30dd587a4547908d4c0925e1092263da81fb3a3cd890261b3a"
    ),
    "plugins/mergecraft/skills/writing-reviewable-pr-descriptions/"
    "review-atlas-reference-design.md": (
        "sha256:23b642b37ced3407c84ad2b1ca6da430d95dd68a674f7daceede3a1b297af441"
    ),
    "plugins/mergecraft/topology.json": (
        "sha256:cc58595b94bf483450b6cc473bbce7b6a20500359f5f9ee9f6ec8b16e017e4c7"
    ),
    "plugins/rolecasting/plugin.json": (
        "sha256:ab2726e06bada2c28d3bc98ae0e885e8a7dfd6fe72df242e351a7ce529b902d0"
    ),
    "plugins/rolecasting/skills/delegating-cross-agent-work/SKILL.md": (
        "sha256:87818876b35c2a336bda9d81f374f43f447d0e2b3c5153b17b1e98dd87cb4636"
    ),
    "plugins/task-witness/plugin.json": (
        "sha256:5be0a7bd385a4abe6cdfc5d0b3f945bc1a59394637e84c9f5f2e6b310aa95213"
    ),
    "plugins/task-witness/runtime/task_witness.py": (
        "sha256:9c61d4ad10f54fe14abafeca9016f8d7e472edff7b66b6d871b2f212a0c06afd"
    ),
    "plugins/tricritical/plugin.json": (
        "sha256:d4c12cd78b0631e1ea171b5254b5661d9d13f198bca28c4dd2d56d1744b982b7"
    ),
    "plugins/tricritical/skills/review/SKILL.md": (
        "sha256:ce7c8cc31b823f4f0f6263f3e251505fff2967dcea23322eb9fea3c15d7ea8b6"
    ),
    "plugins/versionkeeping/plugin.json": (
        "sha256:6fde278f2e58631c3873eb1c71007f71e3bbfa7ea8e3624c8547fde9ee477890"
    ),
    "plugins/versionkeeping/skills/checkpointing-and-publishing-git-work/SKILL.md": (
        "sha256:b2b978e21660ed513eb2e15ae7a6be77bfaaf284f51bdaa3acb0fa4288a1ba38"
    ),
    "release/mergecraft-retirement-contribution-ledger.json": (
        "sha256:c2ec01f117ec9ddc5050daabfa4a072ecd56575ad7c639cb6f95b6d112d93d1c"
    ),
    "release/mergecraft/review-atlas-contribution-ledger.json": (
        "sha256:5804803a8abb18e26c2b7700670d036aadf6d44cab2b0457f7b8a69e1a9e0046"
    ),
}
SOURCE_MANIFEST_FIELDS = {
    "candidate",
    "content_sha256",
    "contract",
    "research_observed_at_utc",
    "schema_version",
    "scope",
    "sources",
}
LEDGER_FIELDS = {
    "content_sha256",
    "contract",
    "contributions",
    "limitations",
    "schema_version",
    "source_manifest",
}
HOST_FIELDS = {
    "content_sha256",
    "contract",
    "discovery_precedence",
    "observed_at_utc",
    "profile_id",
    "schema_version",
    "source_manifest",
    "source_observations",
}
HOST_ROUTE_FIELDS = {
    "entry_count",
    "matched_snapshots",
    "skill_ids",
    "skill_tree_sha256",
    "total_bytes",
}
LIMITATIONS = (
    "Evidence only: this ledger records lineage and drift, not a final source "
    "disposition, release-eligibility decision, installation authority, or "
    "retirement authority."
)
SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
SHA1 = re.compile(r"[0-9a-f]{40}\Z")
SAFE_ID = re.compile(r"[a-z0-9][a-z0-9._:-]*\Z")
OPAQUE_HOST_ROUTE_ID = re.compile(r"route-sha256:[0-9a-f]{64}\Z")
UTC = re.compile(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
HOST_ROUTE_ID_DOMAIN = b"coordinated-installed-source-skill-route-v1\0"
HOST_NOT_APPLICABLE_REASONS = {
    "source-not-applicable": "This source is outside the selected public host profile."
}
HOST_UNRESOLVED_REASONS = {
    "transport-unavailable": (
        (
            "The configured read-only route was unavailable, so no remote "
            "installed-source facts were collected."
        ),
        (
            "restored read-only transport",
            "two-pass installed-source observation",
        ),
    )
}
PRIVATE_MARKERS = (
    "/Users/",
    "/home/",
    "~/",
    "\\Users\\",
    "authorization:",
    "bearer ",
    "api_key",
    "api-key",
    "token=",
)
PUBLIC_HTTPS_URL = re.compile(r"https://[^\s`'\"<>()\[\]{},;\\]+", re.IGNORECASE)
PUBLIC_HTTPS_HOSTS = {"github.com"}
FILE_URI_SCHEME = re.compile(
    r"(?:^|[^A-Za-z0-9+.-])file:(?=[\\/%]|[A-Za-z]:[\\/])", re.IGNORECASE
)
POSIX_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_.-])/(?=[^\s`'\"<>])")
POSIX_HOME_PATH = re.compile(r"(?<![A-Za-z0-9_.-])~[A-Za-z0-9._-]*/")
WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]"
    r"|\\\\(?=[^\\\s])"
    r"|(?<![A-Za-z0-9_.\\-])\\(?!\\)"
    r"(?=(?:[^\\\s`'\"<>]+[\\/])|[A-Za-z0-9_.~-]{2,}\b)"
)


class LineageError(ValueError):
    """A stable source-lineage validation failure."""


@dataclass(frozen=True)
class _LineageView:
    __annotations__ = {
        "root": Path,
        "root_descriptor": int,
        "root_identity": tuple[int, int],
        "release_descriptor": int,
        "release_identity": tuple[int, int],
    }


_LINEAGE_DIRECTORY_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
)
_LINEAGE_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)

MATERIALIZE_TIMEOUT_SECONDS = 30.0
MAX_TREE_ENTRIES = 10_000
MAX_TREE_LISTING_BYTES = 8 * 1024 * 1024
MAX_TREE_PATH_BYTES = 4 * 1024
MAX_TREE_COMPONENT_BYTES = 255
MAX_TREE_COMPONENTS = 256
MAX_TREE_TOTAL_COMPONENTS = 100_000
MAX_BLOB_BYTES = 32 * 1024 * 1024
MAX_MATERIALIZED_BYTES = 256 * 1024 * 1024
MAX_SYMLINK_BYTES = 4 * 1024
MAX_VALIDATION_FILE_BYTES = 2 * 1024 * 1024
MAX_VALIDATION_TREE_ENTRIES = 10_000
MAX_PROCESS_READ_BYTES = 64 * 1024
MAX_JSON_NUMBER_TOKEN_BYTES = 128

TREE_LIMIT_DIAGNOSTIC = "tree exceeds capture limits"
VALIDATION_LIMIT_DIAGNOSTIC = "source-lineage validation exceeds limits"


class _CaptureBudget:
    def __init__(self, deadline: float, diagnostic: str):
        self.deadline = deadline
        self.diagnostic = diagnostic
        self.entries = 0
        self.validation_entries = 0
        self.listing_bytes = 0
        self.materialized_bytes = 0
        self.total_components = 0

    def fail(self) -> None:
        raise LineageError(self.diagnostic) from None

    def require_time(self) -> None:
        if time.monotonic() >= self.deadline:
            self.fail()

    def reserve_tree_entry(self) -> None:
        self.require_time()
        self.entries += 1
        if self.entries > MAX_TREE_ENTRIES:
            self.fail()

    def reserve_validation_entry(self) -> None:
        self.require_time()
        self.validation_entries += 1
        if self.validation_entries > MAX_VALIDATION_TREE_ENTRIES:
            self.fail()

    def reserve_path(self, relative: str) -> None:
        self.require_time()
        try:
            encoded = relative.encode("utf-8")
            components = relative.split("/")
            component_sizes = [
                len(component.encode("utf-8")) for component in components
            ]
        except UnicodeEncodeError:
            self.fail()
        if (
            not relative
            or len(encoded) > MAX_TREE_PATH_BYTES
            or len(components) > MAX_TREE_COMPONENTS
            or self.listing_bytes + len(encoded) > MAX_TREE_LISTING_BYTES
            or self.total_components + len(components) > MAX_TREE_TOTAL_COMPONENTS
            or any(
                not component or size > MAX_TREE_COMPONENT_BYTES
                for component, size in zip(components, component_sizes)
            )
        ):
            self.fail()
        self.listing_bytes += len(encoded)
        self.total_components += len(components)

    def reserve_bytes(self, size: int) -> None:
        self.require_time()
        if size < 0 or self.materialized_bytes + size > MAX_MATERIALIZED_BYTES:
            self.fail()
        self.materialized_bytes += size


def _new_tree_budget(
    diagnostic: str = TREE_LIMIT_DIAGNOSTIC,
) -> _CaptureBudget:
    return _CaptureBudget(
        time.monotonic() + MATERIALIZE_TIMEOUT_SECONDS,
        diagnostic,
    )


def _new_validation_budget() -> _CaptureBudget:
    return _new_tree_budget(VALIDATION_LIMIT_DIAGNOSTIC)


def _directory_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _require_directory_binding(
    expected: tuple[int, int], *observations: os.stat_result
) -> None:
    require(
        all(
            stat.S_ISDIR(metadata.st_mode) and _directory_identity(metadata) == expected
            for metadata in observations
        ),
        "source-lineage artifact tree drift",
    )


def _require_single_component(name: object) -> str:
    require(
        type(name) is str
        and bool(name)
        and name not in {".", ".."}
        and "/" not in name
        and "\0" not in name,
        "source-lineage artifact tree drift",
    )
    return name


def _close_lineage_descriptors(*descriptors: int | None) -> None:
    active_failure = sys.exc_info()[0] is not None
    failed = False
    for descriptor in descriptors:
        if descriptor is None:
            continue
        try:
            os.close(descriptor)
        except OSError:
            failed = True
    if failed and not active_failure:
        raise LineageError("source-lineage artifact tree drift") from None


def _require_lineage_view_binding(view: _LineageView) -> None:
    try:
        visible_root = view.root.lstat()
        opened_root = os.fstat(view.root_descriptor)
        visible = os.stat(
            LINEAGE_ROOT.parent.name,
            dir_fd=view.root_descriptor,
            follow_symlinks=False,
        )
        opened = os.fstat(view.release_descriptor)
    except OSError:
        raise LineageError("source-lineage artifact tree drift") from None
    _require_directory_binding(view.root_identity, visible_root, opened_root)
    _require_directory_binding(view.release_identity, visible, opened)


@contextmanager
def _open_directory_at(
    parent_descriptor: int,
    name: str,
    *,
    budget: _CaptureBudget | None = None,
):
    name = _require_single_component(name)
    descriptor = None
    identity = None
    try:
        if budget is not None:
            budget.require_time()
        before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        require(
            stat.S_ISDIR(before.st_mode) and not stat.S_ISLNK(before.st_mode),
            "source-lineage artifact tree drift",
        )
        descriptor = os.open(
            name,
            _LINEAGE_DIRECTORY_FLAGS,
            dir_fd=parent_descriptor,
        )
        if budget is not None:
            budget.require_time()
        opened = os.fstat(descriptor)
        after = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        identity = _directory_identity(opened)
        _require_directory_binding(identity, before, opened, after)
    except LineageError:
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
        raise LineageError("source-lineage artifact tree drift") from None

    try:
        yield descriptor
    finally:
        active_failure = sys.exc_info()[0] is not None
        binding_error = False
        if not active_failure:
            try:
                if budget is not None:
                    budget.require_time()
                visible = os.stat(
                    name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                opened = os.fstat(descriptor)
                _require_directory_binding(identity, visible, opened)
            except (LineageError, OSError):
                binding_error = True
        try:
            os.close(descriptor)
        except OSError:
            binding_error = True
        if binding_error and not active_failure:
            raise LineageError("source-lineage artifact tree drift") from None


def _regular_file_identity(
    metadata: os.stat_result,
) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_regular_file_at(
    parent_descriptor: int,
    name: str,
    *,
    budget: _CaptureBudget | None = None,
    max_bytes: int | None = None,
) -> tuple[bytes, str]:
    name = _require_single_component(name)
    if budget is None:
        budget = _new_validation_budget()
    if max_bytes is None:
        max_bytes = MAX_VALIDATION_FILE_BYTES
    descriptor = None
    result = None
    failure = None
    try:
        budget.require_time()
        before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        require(
            stat.S_ISREG(before.st_mode) and not stat.S_ISLNK(before.st_mode),
            "source-lineage artifact tree drift",
        )
        if before.st_size > max_bytes:
            budget.fail()
        budget.reserve_bytes(before.st_size)
        descriptor = os.open(name, _LINEAGE_FILE_FLAGS, dir_fd=parent_descriptor)
        budget.require_time()
        opened = os.fstat(descriptor)
        visible = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        require(
            stat.S_ISREG(opened.st_mode)
            and _regular_file_identity(opened)
            == _regular_file_identity(before)
            == _regular_file_identity(visible),
            "source-lineage artifact tree drift",
        )
        captured = bytearray()
        while True:
            budget.require_time()
            chunk = os.read(
                descriptor,
                min(MAX_PROCESS_READ_BYTES, before.st_size - len(captured) + 1),
            )
            if not chunk:
                break
            captured.extend(chunk)
            if len(captured) > before.st_size or len(captured) > max_bytes:
                raise LineageError("source-lineage artifact tree drift")
        raw = bytes(captured)
        opened_after = os.fstat(descriptor)
        visible_after = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        require(
            _regular_file_identity(before)
            == _regular_file_identity(opened)
            == _regular_file_identity(opened_after)
            == _regular_file_identity(visible_after)
            and len(raw) == opened_after.st_size,
            "source-lineage artifact tree drift",
        )
        mode = "100755" if opened.st_mode & 0o111 else "100644"
        result = raw, mode
    except LineageError as error:
        failure = error
    except OSError:
        failure = LineageError("source-lineage artifact tree drift")
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                if failure is None:
                    failure = LineageError("source-lineage artifact tree drift")
    if failure is not None:
        raise failure from None
    return result


def _read_relative_file_at(
    root_descriptor: int,
    relative: Path,
    *,
    budget: _CaptureBudget | None = None,
    max_bytes: int | None = None,
) -> bytes:
    require(
        isinstance(relative, Path)
        and not relative.is_absolute()
        and bool(relative.parts),
        "source-lineage artifact tree drift",
    )
    if budget is None:
        budget = _new_validation_budget()
    budget.reserve_path(relative.as_posix())
    with ExitStack() as stack:
        parent_descriptor = root_descriptor
        for component in relative.parts[:-1]:
            parent_descriptor = stack.enter_context(
                _open_directory_at(parent_descriptor, component, budget=budget)
            )
        raw, _ = _read_regular_file_at(
            parent_descriptor,
            relative.parts[-1],
            budget=budget,
            max_bytes=max_bytes,
        )
        return raw


@contextmanager
def _verified_lineage_parent(artifacts_root: Path):
    root_descriptor = None
    parent_descriptor = None
    try:
        root = Path(os.path.abspath(os.fspath(artifacts_root)))
        canonical_root = root.resolve(strict=True)
        root_before = canonical_root.lstat()
        require(
            stat.S_ISDIR(root_before.st_mode) and not stat.S_ISLNK(root_before.st_mode),
            "source-lineage artifact tree drift",
        )
        root_descriptor = os.open(canonical_root, _LINEAGE_DIRECTORY_FLAGS)
        root_opened = os.fstat(root_descriptor)
        root_after = root.lstat()
        root_identity = _directory_identity(root_opened)
        _require_directory_binding(root_identity, root_before, root_opened, root_after)

        parent_name = LINEAGE_ROOT.parent.name
        parent_before = os.stat(
            parent_name, dir_fd=root_descriptor, follow_symlinks=False
        )
        require(
            stat.S_ISDIR(parent_before.st_mode)
            and not stat.S_ISLNK(parent_before.st_mode),
            "source-lineage artifact tree drift",
        )
        parent_descriptor = os.open(
            parent_name, _LINEAGE_DIRECTORY_FLAGS, dir_fd=root_descriptor
        )
        parent_opened = os.fstat(parent_descriptor)
        parent_after = os.stat(
            parent_name, dir_fd=root_descriptor, follow_symlinks=False
        )
        parent_identity = _directory_identity(parent_opened)
        _require_directory_binding(
            parent_identity, parent_before, parent_opened, parent_after
        )
        require(
            (root / parent_name).resolve(strict=True) == canonical_root / parent_name,
            "source-lineage artifact tree drift",
        )
    except LineageError:
        _close_lineage_descriptors(parent_descriptor, root_descriptor)
        raise
    except (OSError, RuntimeError):
        _close_lineage_descriptors(parent_descriptor, root_descriptor)
        raise LineageError("source-lineage artifact tree drift") from None
    except BaseException:
        _close_lineage_descriptors(parent_descriptor, root_descriptor)
        raise

    view = _LineageView(
        root=root,
        root_descriptor=root_descriptor,
        root_identity=root_identity,
        release_descriptor=parent_descriptor,
        release_identity=parent_identity,
    )
    try:
        yield view
    finally:
        _close_lineage_descriptors(parent_descriptor, root_descriptor)


@contextmanager
def _lineage_lock(repository: Path, *, exclusive: bool, nonblocking: bool = False):
    with _verified_lineage_parent(repository) as view:
        descriptor = view.release_descriptor
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        if nonblocking:
            operation |= fcntl.LOCK_NB
        locked = False
        try:
            try:
                fcntl.flock(descriptor, operation)
            except BlockingIOError as error:
                raise LineageError("source-lineage writer is already active") from error
            except OSError:
                raise LineageError("source-lineage artifact tree drift") from None
            locked = True
            _require_lineage_view_binding(view)
            yield view
        finally:
            if locked:
                active_failure = sys.exc_info()[0] is not None
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    if not active_failure:
                        raise LineageError(
                            "source-lineage artifact tree drift"
                        ) from None


def require(condition: bool, diagnostic: str) -> None:
    if not condition:
        raise LineageError(diagnostic)


def _strict_json(raw: bytes, label: str, *, reject_numbers: bool = False) -> object:
    def object_pairs(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, f"duplicate JSON key in {label}")
            value[key] = item
        return value

    def constant(value: str):
        if reject_numbers:
            raise LineageError(f"numeric JSON value is not allowed in {label}")
        raise LineageError(f"non-finite JSON value in {label}: {value}")

    def floating(value: str) -> float:
        if reject_numbers:
            raise LineageError(f"numeric JSON value is not allowed in {label}")
        if len(value) > MAX_JSON_NUMBER_TOKEN_BYTES:
            raise LineageError(f"numeric JSON value exceeds limits in {label}")
        try:
            parsed = float(value)
        except (ValueError, OverflowError):
            raise LineageError(f"numeric JSON value is invalid in {label}") from None
        require(math.isfinite(parsed), f"non-finite JSON value in {label}")
        return parsed

    def integer(value: str) -> int:
        if reject_numbers:
            raise LineageError(f"numeric JSON value is not allowed in {label}")
        if len(value) > MAX_JSON_NUMBER_TOKEN_BYTES:
            raise LineageError(f"numeric JSON value exceeds limits in {label}")
        try:
            return int(value)
        except (ValueError, OverflowError):
            raise LineageError(f"numeric JSON value is invalid in {label}") from None

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=constant,
            parse_float=floating,
            parse_int=integer,
        )
        pending = [value]
        while pending:
            item = pending.pop()
            if type(item) is str:
                item.encode("utf-8")
            elif type(item) is list:
                pending.extend(item)
            elif type(item) is dict:
                pending.extend(item)
                pending.extend(item.values())
        return value
    except LineageError:
        raise
    except UnicodeDecodeError as error:
        raise LineageError(f"{label} is not UTF-8") from error
    except UnicodeEncodeError:
        raise LineageError(f"{label} is not valid JSON") from None
    except (json.JSONDecodeError, ValueError, OverflowError, RecursionError):
        raise LineageError(f"{label} is not valid JSON") from None


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def content_sha256(value: dict) -> str:
    unsigned = {key: item for key, item in value.items() if key != "content_sha256"}
    return canonical_sha256(unsigned)


def content_document(value: object) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _read_document_bytes(raw: bytes, label: str) -> tuple[dict, bytes]:
    value = _strict_json(raw, label)
    require(type(value) is dict, f"{label} must be an object")
    require(raw == content_document(value), f"{label} is not canonical JSON")
    return value, raw


def _validate_lineage_tree_view(view: _LineageView) -> None:
    _require_lineage_view_binding(view)
    _lineage_snapshot_at(view.release_descriptor, LINEAGE_ROOT.name)
    _require_lineage_view_binding(view)


def _validate_lineage_tree(artifacts_root: Path) -> None:
    with _verified_lineage_parent(artifacts_root) as view:
        _validate_lineage_tree_view(view)


def _validate_content_document(value: dict, fields: set[str], label: str) -> None:
    require(set(value) == fields, f"{label} schema drift")
    require(
        type(value["content_sha256"]) is str
        and SHA256.fullmatch(value["content_sha256"]) is not None
        and value["content_sha256"] == content_sha256(value),
        f"{label} content digest mismatch",
    )


def _safe_symlink_target(relative: str, target: object) -> bytes:
    require(
        type(target) is str and bool(target) and "\0" not in target,
        "tree symlink target is invalid",
    )
    require(
        not target.startswith("/")
        and posixpath.normpath(posixpath.join(posixpath.dirname(relative), target))
        not in {".."}
        and not posixpath.normpath(
            posixpath.join(posixpath.dirname(relative), target)
        ).startswith("../"),
        "tree symlink escapes its root",
    )
    try:
        return target.encode("utf-8")
    except UnicodeEncodeError as error:
        raise LineageError("tree symlink target is not UTF-8") from error


def _tree_entry(relative: str, mode: str, content: bytes) -> dict:
    require(
        type(relative) is str
        and bool(relative)
        and not Path(relative).is_absolute()
        and ".." not in Path(relative).parts
        and Path(relative).as_posix() == relative,
        "tree entry path is invalid",
    )
    require(mode in {"100644", "100755", "120000"}, "tree entry mode is unsupported")
    return {
        "length": len(content),
        "mode": mode,
        "path": relative,
        "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
    }


def _tree_entries_identity(entries: list[dict]) -> dict:
    ordered = sorted(entries, key=lambda item: item["path"])
    require(
        len(ordered) == len({item["path"] for item in ordered}),
        "tree entry paths are ambiguous",
    )
    return {
        "entry_count": len(ordered),
        "total_bytes": sum(item["length"] for item in ordered),
        "tree_sha256": canonical_sha256(ordered),
    }


def _tree_directory_state(
    metadata: os.stat_result,
) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


@contextmanager
def _tree_route_descriptor(
    parent_descriptor: int,
    name: str,
    before: os.stat_result,
    budget: _CaptureBudget,
):
    descriptor = None
    identity = None
    failure = None
    try:
        budget.require_time()
        descriptor = os.open(
            _require_single_component(name),
            _LINEAGE_DIRECTORY_FLAGS,
            dir_fd=parent_descriptor,
        )
        opened = os.fstat(descriptor)
        visible = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        identity = _directory_identity(opened)
        require(
            all(
                stat.S_ISDIR(item.st_mode)
                and not stat.S_ISLNK(item.st_mode)
                and _directory_identity(item) == identity
                for item in (before, opened, visible)
            ),
            "tree entry cannot be observed",
        )
    except LineageError:
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
        raise LineageError("tree entry cannot be observed") from None
    try:
        yield descriptor
    except BaseException as error:
        failure = error
        raise
    finally:
        final_failure = None
        if failure is None:
            try:
                budget.require_time()
                opened_after = os.fstat(descriptor)
                visible_after = os.stat(
                    name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if (
                    _directory_identity(opened_after) != identity
                    or _directory_identity(visible_after) != identity
                    or not stat.S_ISDIR(visible_after.st_mode)
                    or stat.S_ISLNK(visible_after.st_mode)
                ):
                    final_failure = LineageError("tree entry cannot be observed")
            except LineageError as error:
                final_failure = error
            except OSError:
                final_failure = LineageError("tree entry cannot be observed")
        try:
            os.close(descriptor)
        except OSError:
            if failure is None and final_failure is None:
                final_failure = LineageError("tree entry cannot be observed")
        if failure is None and final_failure is not None:
            raise final_failure from None


@contextmanager
def _tree_root_descriptor(root: Path, budget: _CaptureBudget):
    visible_root = Path(root)
    absolute = None
    root_identity = None
    anchor_descriptor = None
    anchor_identity = None
    failure = None
    try:
        budget.require_time()
        root_before = visible_root.lstat()
        require(
            stat.S_ISDIR(root_before.st_mode) and not stat.S_ISLNK(root_before.st_mode),
            "tree root must be a directory",
        )
        absolute = visible_root.resolve(strict=True)
        root_identity = _tree_directory_state(root_before)
        anchor_descriptor = os.open(absolute.anchor, _LINEAGE_DIRECTORY_FLAGS)
        anchor_identity = _directory_identity(os.fstat(anchor_descriptor))
    except LineageError:
        if anchor_descriptor is not None:
            try:
                os.close(anchor_descriptor)
            except OSError:
                pass
        raise
    except OSError:
        if anchor_descriptor is not None:
            try:
                os.close(anchor_descriptor)
            except OSError:
                pass
        raise LineageError("tree entry cannot be observed") from None
    try:
        with ExitStack() as stack:
            directory_descriptor = anchor_descriptor
            components = absolute.parts[1:]
            for index, component in enumerate(components):
                budget.require_time()
                try:
                    before = os.stat(
                        component,
                        dir_fd=directory_descriptor,
                        follow_symlinks=False,
                    )
                except OSError:
                    raise LineageError("tree entry cannot be observed") from None
                require(
                    stat.S_ISDIR(before.st_mode) and not stat.S_ISLNK(before.st_mode),
                    "tree root must be a directory",
                )
                context = (
                    _tree_child_descriptor
                    if index == len(components) - 1
                    else _tree_route_descriptor
                )
                directory_descriptor = stack.enter_context(
                    context(
                        directory_descriptor,
                        component,
                        before,
                        budget,
                    )
                )
            require(
                _tree_directory_state(os.fstat(directory_descriptor)) == root_identity,
                "tree entry cannot be observed",
            )
            yield directory_descriptor
            budget.require_time()
            require(
                _tree_directory_state(os.fstat(directory_descriptor))
                == root_identity
                == _tree_directory_state(visible_root.lstat()),
                "tree entry cannot be observed",
            )
    except BaseException as error:
        failure = error
        raise
    finally:
        final_failure = None
        if failure is None:
            try:
                budget.require_time()
                if _directory_identity(os.fstat(anchor_descriptor)) != anchor_identity:
                    final_failure = LineageError("tree entry cannot be observed")
            except LineageError as error:
                final_failure = error
            except OSError:
                final_failure = LineageError("tree entry cannot be observed")
        try:
            os.close(anchor_descriptor)
        except OSError:
            if failure is None and final_failure is None:
                final_failure = LineageError("tree entry cannot be observed")
        if failure is None and final_failure is not None:
            raise final_failure from None


@contextmanager
def _tree_child_descriptor(
    parent_descriptor: int,
    name: str,
    before: os.stat_result,
    budget: _CaptureBudget,
):
    descriptor = None
    identity = None
    failure = None
    try:
        budget.require_time()
        descriptor = os.open(
            _require_single_component(name),
            _LINEAGE_DIRECTORY_FLAGS,
            dir_fd=parent_descriptor,
        )
        budget.require_time()
        opened = os.fstat(descriptor)
        visible = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        identity = _tree_directory_state(opened)
        require(
            stat.S_ISDIR(opened.st_mode)
            and not stat.S_ISLNK(opened.st_mode)
            and _tree_directory_state(before)
            == identity
            == _tree_directory_state(visible),
            "tree entry cannot be observed",
        )
    except LineageError:
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
        raise LineageError("tree entry cannot be observed") from None
    try:
        yield descriptor
    except BaseException as error:
        failure = error
        raise
    finally:
        final_failure = None
        if failure is None:
            try:
                budget.require_time()
                opened_after = os.fstat(descriptor)
                visible_after = os.stat(
                    name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if (
                    _tree_directory_state(opened_after) != identity
                    or _tree_directory_state(visible_after) != identity
                ):
                    final_failure = LineageError("tree entry cannot be observed")
            except LineageError as error:
                final_failure = error
            except OSError:
                final_failure = LineageError("tree entry cannot be observed")
        try:
            os.close(descriptor)
        except OSError:
            if failure is None and final_failure is None:
                final_failure = LineageError("tree entry cannot be observed")
        if failure is None and final_failure is not None:
            raise final_failure from None


def _tree_regular_entry(
    parent_descriptor: int,
    name: str,
    relative: str,
    before: os.stat_result,
    budget: _CaptureBudget,
) -> dict:
    descriptor = None
    result = None
    failure = None
    try:
        if before.st_size > MAX_BLOB_BYTES:
            budget.fail()
        budget.reserve_bytes(before.st_size)
        visible_before = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        require(
            _regular_file_identity(visible_before) == _regular_file_identity(before),
            "tree entry cannot be observed",
        )
        descriptor = os.open(name, _LINEAGE_FILE_FLAGS, dir_fd=parent_descriptor)
        budget.require_time()
        opened = os.fstat(descriptor)
        require(
            stat.S_ISREG(opened.st_mode)
            and _regular_file_identity(opened) == _regular_file_identity(before),
            "tree entry cannot be observed",
        )
        digest = hashlib.sha256()
        length = 0
        while True:
            budget.require_time()
            chunk = os.read(
                descriptor,
                min(MAX_PROCESS_READ_BYTES, before.st_size - length + 1),
            )
            if not chunk:
                break
            length += len(chunk)
            if length > before.st_size or length > MAX_BLOB_BYTES:
                raise LineageError("tree entry changed while read")
            digest.update(chunk)
        opened_after = os.fstat(descriptor)
        visible_after = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        require(
            length == before.st_size
            and _regular_file_identity(opened_after)
            == _regular_file_identity(before)
            == _regular_file_identity(visible_after),
            "tree entry changed while read",
        )
        result = {
            "length": length,
            "mode": "100755" if before.st_mode & 0o111 else "100644",
            "path": relative,
            "sha256": "sha256:" + digest.hexdigest(),
        }
    except LineageError as error:
        failure = error
    except OSError:
        failure = LineageError("tree entry cannot be observed")
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                if failure is None:
                    failure = LineageError("tree entry cannot be observed")
    if failure is not None:
        raise failure from None
    return result


def _tree_symlink_entry(
    parent_descriptor: int,
    name: str,
    relative: str,
    before: os.stat_result,
    budget: _CaptureBudget,
) -> dict:
    if before.st_size > MAX_SYMLINK_BYTES:
        budget.fail()
    try:
        budget.require_time()
        visible_before = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        target = os.readlink(name, dir_fd=parent_descriptor)
        budget.require_time()
        target_after = os.readlink(name, dir_fd=parent_descriptor)
        visible_after = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except LineageError:
        raise
    except OSError:
        raise LineageError("tree entry cannot be observed") from None
    require(
        target == target_after
        and _regular_file_identity(visible_before)
        == _regular_file_identity(before)
        == _regular_file_identity(visible_after),
        "tree symlink changed while read",
    )
    content = _safe_symlink_target(relative, target)
    if len(content) > MAX_SYMLINK_BYTES:
        budget.fail()
    budget.reserve_bytes(len(content))
    return _tree_entry(relative, "120000", content)


def _capture_tree_entries(
    directory_descriptor: int,
    budget: _CaptureBudget,
    entries: list[dict],
    relative_parent: str = "",
) -> None:
    try:
        budget.require_time()
        before_directory = os.fstat(directory_descriptor)
        with os.scandir(directory_descriptor) as scanner:
            for observed in scanner:
                budget.reserve_tree_entry()
                name = _require_single_component(observed.name)
                relative = f"{relative_parent}/{name}" if relative_parent else name
                budget.reserve_path(relative)
                metadata = os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
                if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(
                    metadata.st_mode
                ):
                    with _tree_child_descriptor(
                        directory_descriptor,
                        name,
                        metadata,
                        budget,
                    ) as child_descriptor:
                        _capture_tree_entries(
                            child_descriptor,
                            budget,
                            entries,
                            relative,
                        )
                elif stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(
                    metadata.st_mode
                ):
                    entries.append(
                        _tree_regular_entry(
                            directory_descriptor,
                            name,
                            relative,
                            metadata,
                            budget,
                        )
                    )
                elif stat.S_ISLNK(metadata.st_mode):
                    entries.append(
                        _tree_symlink_entry(
                            directory_descriptor,
                            name,
                            relative,
                            metadata,
                            budget,
                        )
                    )
                else:
                    raise LineageError("tree contains a special entry")
        budget.require_time()
        after_directory = os.fstat(directory_descriptor)
        require(
            _tree_directory_state(before_directory)
            == _tree_directory_state(after_directory),
            "tree entry cannot be observed",
        )
    except LineageError:
        raise
    except OSError:
        raise LineageError("tree entry cannot be observed") from None


def _directory_snapshot_identity(
    metadata: os.stat_result,
) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _snapshot_directory_descriptor(
    directory_descriptor: int,
    prefix: str,
    directories: set[str],
    files: dict[str, bytes],
    entries: list[dict],
    budget: _CaptureBudget,
) -> None:
    try:
        budget.require_time()
        before = os.fstat(directory_descriptor)
        require(
            stat.S_ISDIR(before.st_mode),
            "source-lineage artifact tree drift",
        )
    except LineageError:
        raise
    except OSError:
        raise LineageError("source-lineage artifact tree drift") from None

    try:
        with os.scandir(directory_descriptor) as scanner:
            for observed in scanner:
                budget.reserve_validation_entry()
                name = _require_single_component(observed.name)
                relative = name if not prefix else f"{prefix}/{name}"
                budget.reserve_path(relative)
                metadata = os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
                if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(
                    metadata.st_mode
                ):
                    directories.add(relative)
                    with _open_directory_at(
                        directory_descriptor,
                        name,
                        budget=budget,
                    ) as child_descriptor:
                        _snapshot_directory_descriptor(
                            child_descriptor,
                            relative,
                            directories,
                            files,
                            entries,
                            budget,
                        )
                elif stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(
                    metadata.st_mode
                ):
                    raw, mode = _read_regular_file_at(
                        directory_descriptor,
                        name,
                        budget=budget,
                    )
                    files[relative] = raw
                    entries.append(_tree_entry(relative, mode, raw))
                else:
                    raise LineageError("source-lineage artifact tree drift")
    except LineageError:
        raise
    except OSError:
        raise LineageError("source-lineage artifact tree drift") from None

    try:
        budget.require_time()
        after = os.fstat(directory_descriptor)
    except LineageError:
        raise
    except OSError:
        raise LineageError("source-lineage artifact tree drift") from None
    require(
        _directory_snapshot_identity(before) == _directory_snapshot_identity(after),
        "source-lineage artifact tree drift",
    )


def _lineage_snapshot_descriptor(
    directory_descriptor: int,
    *,
    budget: _CaptureBudget | None = None,
) -> dict[str, object]:
    if budget is None:
        budget = _new_validation_budget()
    directories: set[str] = set()
    files: dict[str, bytes] = {}
    entries: list[dict] = []
    _snapshot_directory_descriptor(
        directory_descriptor,
        "",
        directories,
        files,
        entries,
        budget,
    )
    observed = {
        (LINEAGE_ROOT / relative).as_posix() for relative in directories | set(files)
    }
    require(
        observed == {path.as_posix() for path in LINEAGE_TREE},
        "source-lineage artifact tree drift",
    )
    return {
        "identity": _tree_entries_identity(entries),
        "files": {relative: files[relative] for relative in sorted(files)},
    }


def _lineage_snapshot_at(
    parent_descriptor: int,
    name: str,
    *,
    budget: _CaptureBudget | None = None,
) -> dict[str, object]:
    if budget is None:
        budget = _new_validation_budget()
    with _open_directory_at(
        parent_descriptor,
        name,
        budget=budget,
    ) as directory_descriptor:
        return _lineage_snapshot_descriptor(directory_descriptor, budget=budget)


def _tree_identity_at(parent_descriptor: int, name: str) -> dict:
    snapshot = _lineage_snapshot_at(parent_descriptor, name)
    identity = snapshot["identity"]
    require(type(identity) is dict, "source-lineage artifact tree drift")
    return identity


def tree_identity(
    root: Path,
    *,
    capture_budget: _CaptureBudget | None = None,
) -> dict:
    root = Path(root)
    budget = capture_budget if capture_budget is not None else _new_tree_budget()
    entries: list[dict] = []
    with _tree_root_descriptor(root, budget) as descriptor:
        _capture_tree_entries(descriptor, budget, entries)
    return _tree_entries_identity(entries)


def _tree_identity_relative_at(
    root_descriptor: int,
    relative: Path,
    budget: _CaptureBudget,
) -> dict:
    require(
        isinstance(relative, Path)
        and not relative.is_absolute()
        and bool(relative.parts),
        "candidate plugin root drift",
    )
    entries: list[dict] = []
    with ExitStack() as stack:
        directory_descriptor = root_descriptor
        for component in relative.parts:
            budget.require_time()
            try:
                before = os.stat(
                    component,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except OSError:
                raise LineageError("tree entry cannot be observed") from None
            require(
                stat.S_ISDIR(before.st_mode) and not stat.S_ISLNK(before.st_mode),
                "tree root must be a directory",
            )
            directory_descriptor = stack.enter_context(
                _tree_child_descriptor(
                    directory_descriptor,
                    component,
                    before,
                    budget,
                )
            )
        _capture_tree_entries(directory_descriptor, budget, entries)
    return _tree_entries_identity(entries)


def _safe_id(value: object, label: str) -> str:
    require(
        type(value) is str and SAFE_ID.fullmatch(value) is not None,
        f"{label} must be a safe identifier",
    )
    return value


def _public_host_route_id(value: object) -> str:
    require(
        type(value) is str and OPAQUE_HOST_ROUTE_ID.fullmatch(value) is not None,
        "installed source route identifier must be opaque",
    )
    return value


def _assigned_host_routes(source_id: str, installations: object) -> list[dict]:
    require(type(installations) is list, "installed source routes must be an array")
    routes = []
    for installation in installations:
        require(
            type(installation) is dict
            and set(installation)
            in (HOST_ROUTE_FIELDS, HOST_ROUTE_FIELDS | {"installation_id"}),
            "installed source route schema drift",
        )
        routes.append({key: installation[key] for key in HOST_ROUTE_FIELDS})
    routes.sort(key=canonical_bytes)
    occurrences: dict[bytes, int] = {}
    assigned = []
    for route in routes:
        identity = canonical_bytes(route)
        occurrence = occurrences.get(identity, 0) + 1
        occurrences[identity] = occurrence
        frame = canonical_bytes(
            {"occurrence": occurrence, "route": route, "source_id": source_id}
        )
        assigned.append(
            {
                **route,
                "installation_id": "route-sha256:"
                + hashlib.sha256(HOST_ROUTE_ID_DOMAIN + frame).hexdigest(),
            }
        )
    assigned.sort(key=lambda item: item["installation_id"])
    return assigned


def _sha256(value: object, label: str) -> str:
    require(
        type(value) is str and SHA256.fullmatch(value) is not None,
        f"{label} must be a SHA-256 identity",
    )
    return value


def _sha1(value: object, label: str) -> str:
    require(
        type(value) is str and SHA1.fullmatch(value) is not None,
        f"{label} must be a Git SHA-1 identity",
    )
    return value


def _utc(value: object, label: str) -> str:
    valid = type(value) is str and UTC.fullmatch(value) is not None
    if valid:
        try:
            datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            valid = False
    require(valid, f"{label} must be a UTC second timestamp")
    return value


def _nonempty(value: object, label: str) -> str:
    require(
        type(value) is str and value.strip() == value and bool(value),
        f"{label} must be a non-empty string",
    )
    return value


def _string_list(
    value: object,
    label: str,
    *,
    safe_ids: bool = False,
    allow_empty: bool = False,
) -> list[str]:
    require(
        type(value) is list
        and (allow_empty or bool(value))
        and all(type(item) is str and bool(item) for item in value)
        and value == sorted(set(value)),
        f"{label} must be a sorted unique string array"
        + ("" if allow_empty else " with at least one item"),
    )
    if safe_ids:
        for item in value:
            _safe_id(item, label)
    return value


def _relative_path(value: object, label: str, *, allow_dot: bool = False) -> str:
    require(type(value) is str and bool(value), f"{label} must be a relative path")
    candidate = Path(value)
    normalized = candidate.as_posix()
    require(
        not candidate.is_absolute()
        and ".." not in candidate.parts
        and normalized == value
        and (allow_dot or value != "."),
        f"{label} must be a canonical repository-relative path",
    )
    return value


def _https_url(value: object, label: str) -> str:
    require(type(value) is str and bool(value), f"{label} must be an HTTPS URL")
    parsed = urlsplit(value)
    require(
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and parsed.query == ""
        and parsed.fragment == ""
        and not value.endswith(".git")
        and not value.endswith("/"),
        f"{label} is an unsafe or noncanonical source repository URL",
    )
    return value


def _privacy_scan(value: object, label: str) -> None:
    if type(value) is dict:
        for key, item in value.items():
            _privacy_scan(key, label)
            _privacy_scan(item, label)
    elif type(value) is list:
        for item in value:
            _privacy_scan(item, label)
    elif type(value) is str:
        variants = [value]
        for _ in range(3):
            decoded = unquote(variants[-1])
            if decoded == variants[-1]:
                break
            variants.append(decoded)
        else:
            require(
                unquote(variants[-1]) == variants[-1],
                f"{label} contains private material",
            )
        for variant in variants:
            lowered = variant.lower()
            require(
                not any(marker.lower() in lowered for marker in PRIVATE_MARKERS)
                and FILE_URI_SCHEME.search(variant) is None
                and POSIX_HOME_PATH.search(variant) is None
                and WINDOWS_ABSOLUTE_PATH.search(variant) is None,
                f"{label} contains private material",
            )
            cursor = 0
            prose = []
            for match in PUBLIC_HTTPS_URL.finditer(variant):
                token = match.group(0)
                try:
                    parsed = urlsplit(token)
                    hostname = parsed.hostname
                    port = parsed.port
                except ValueError:
                    raise LineageError(f"{label} contains private material") from None
                require(
                    parsed.scheme.lower() == "https"
                    and hostname in PUBLIC_HTTPS_HOSTS
                    and parsed.username is None
                    and parsed.password is None
                    and parsed.query == ""
                    and parsed.fragment == ""
                    and (port is None or type(port) is int),
                    f"{label} contains private material",
                )
                url_path = parsed.path.removeprefix("/")
                require(
                    POSIX_ABSOLUTE_PATH.search(url_path) is None,
                    f"{label} contains private material",
                )
                prose.append(variant[cursor : match.start()])
                cursor = match.end()
            prose.append(variant[cursor:])
            require(
                POSIX_ABSOLUTE_PATH.search("".join(prose)) is None,
                f"{label} contains private material",
            )


def _validate_research_report_bytes(raw: bytes) -> bytes:
    try:
        report = raw.decode("utf-8")
    except UnicodeError as error:
        raise LineageError("source-lineage research report is unavailable") from error
    _privacy_scan(report, "source-lineage research report")
    require(
        "sha256:" + hashlib.sha256(raw).hexdigest() == RESEARCH_REPORT_SHA256,
        "source-lineage research report evidence drift",
    )
    candidate_marker = (
        "The refreshed release-candidate boundary is commit\n"
        f"[`{CANDIDATE_COMMIT_SHA1}`]"
        f"(https://github.com/nisavid/agents/commit/{CANDIDATE_COMMIT_SHA1}),\n"
        f"tree `{CANDIDATE_TREE_SHA1}`."
    )
    candidate_rows = {}
    for line in report.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 6 and cells[0] in CANDIDATE_REPORT_NAMES.values():
            candidate_rows[cells[0]] = cells
    require(
        candidate_marker in report
        and f"Research inventory observed at: {RESEARCH_OBSERVED_AT_UTC}" in report
        and (
            f"Candidate projection refreshed at: {CANDIDATE_REFRESHED_AT_UTC}" in report
        )
        and f"| Refreshed candidate at `{CANDIDATE_COMMIT_SHA1[:8]}` |" in report
        and all(
            candidate_rows.get(CANDIDATE_REPORT_NAMES[identifier], [None] * 6)[4]
            == f"`{tree_sha1}`"
            for identifier, tree_sha1 in CANDIDATE_PACKAGE_GIT_TREES.items()
        ),
        "source-lineage research report candidate boundary drift",
    )
    return raw


def _validate_research_report(artifacts_root: Path) -> bytes:
    budget = _new_validation_budget()
    path = Path(artifacts_root) / RESEARCH_REPORT
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        raise LineageError("source-lineage research report evidence drift") from None
    except OSError:
        raise LineageError("source-lineage research report is unavailable") from None
    require(
        stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode),
        "source-lineage research report must be a regular file",
    )
    try:
        with _tree_root_descriptor(Path(artifacts_root), budget) as root_descriptor:
            raw = _read_relative_file_at(
                root_descriptor,
                RESEARCH_REPORT,
                budget=budget,
                max_bytes=MAX_VALIDATION_FILE_BYTES,
            )
    except LineageError as error:
        if str(error) == VALIDATION_LIMIT_DIAGNOSTIC:
            raise
        raise LineageError("source-lineage research report evidence drift") from None
    return _validate_research_report_bytes(raw)


def _validate_license(value: object, label: str) -> None:
    require(type(value) is dict, f"{label} must be an object")
    status = value.get("status")
    if status == "resolved":
        require(
            set(value)
            == {
                "attribution_requirement",
                "evidence_ref",
                "evidence_sha256",
                "spdx_expression",
                "status",
            }
            and type(value["spdx_expression"]) is str
            and bool(value["spdx_expression"])
            and value["attribution_requirement"] in {"none", "notice-required"},
            f"{label} resolved license schema drift",
        )
        evidence = value["evidence_ref"]
        if type(evidence) is str and evidence.startswith("https://"):
            _https_url(evidence, f"{label} evidence")
        else:
            _relative_path(evidence, f"{label} evidence")
        _sha256(value["evidence_sha256"], f"{label} evidence")
    elif status == "unresolved":
        require(
            set(value) == {"evidence_needed", "reason", "status"},
            f"{label} unresolved license schema drift",
        )
        _nonempty(value["reason"], f"{label} unresolved license reason")
        _string_list(value["evidence_needed"], f"{label} unresolved license evidence")
    else:
        raise LineageError(f"{label} license status is invalid")


def _validate_snapshot(value: object, authority_kind: str, label: str) -> None:
    require(type(value) is dict, f"{label} must be an object")
    status = value.get("status")
    if status == "unresolved":
        require(
            set(value) == {"evidence_needed", "reason", "status"},
            f"{label} unresolved snapshot schema drift",
        )
        _nonempty(value["reason"], f"{label} unresolved snapshot reason")
        _string_list(value["evidence_needed"], f"{label} unresolved snapshot evidence")
        return
    require(status == "resolved", f"{label} snapshot status is invalid")
    common = {"entry_count", "license", "skill_tree_sha256", "status", "total_bytes"}
    if authority_kind == "git":
        require(
            set(value) == common | {"commit_sha1", "tree_sha1"},
            f"{label} resolved git snapshot schema drift",
        )
        _sha1(value["commit_sha1"], f"{label} commit")
        _sha1(value["tree_sha1"], f"{label} tree")
    else:
        require(
            set(value) == common | {"evidence_sha256", "revision_id"},
            f"{label} resolved opaque snapshot schema drift",
        )
        _nonempty(value["revision_id"], f"{label} revision")
        _sha256(value["evidence_sha256"], f"{label} evidence")
    _sha256(value["skill_tree_sha256"], f"{label} skill tree")
    require(
        type(value["entry_count"]) is int
        and value["entry_count"] >= 1
        and type(value["total_bytes"]) is int
        and value["total_bytes"] >= 1,
        f"{label} snapshot bounds are invalid",
    )
    _validate_license(value["license"], label)


def _snapshot_tree(value: dict) -> str | None:
    return value.get("skill_tree_sha256") if value.get("status") == "resolved" else None


def _validate_source(value: object, families: tuple[str, ...]) -> dict:
    require(
        type(value) is dict
        and set(value)
        == {
            "authority",
            "baseline",
            "byte_drift",
            "family",
            "id",
            "skill_ids",
            "current",
        },
        "source schema drift",
    )
    source_id = _safe_id(value["id"], "source id")
    require(value["family"] in families, "source family is invalid")
    if source_id == "ivan-task-witness":
        require(value["skill_ids"] == [], "Task Witness must remain code-only")
    else:
        _string_list(value["skill_ids"], "source skill ids", safe_ids=True)
    authority = value["authority"]
    require(type(authority) is dict, "source authority must be an object")
    kind = authority.get("kind")
    if kind == "git":
        require(
            set(authority) == {"kind", "refresh_ref", "repository_url", "skill_root"},
            "git source authority schema drift",
        )
        _https_url(authority["repository_url"], "source repository URL")
        _nonempty(authority["refresh_ref"], "source refresh ref")
        _relative_path(authority["skill_root"], "source skill root", allow_dot=True)
    elif kind == "opaque":
        require(
            set(authority)
            == {"authority_id", "kind", "refresh_adapter_id", "skill_root_id"},
            "opaque source authority schema drift",
        )
        _safe_id(authority["authority_id"], "opaque authority id")
        _safe_id(authority["refresh_adapter_id"], "opaque refresh adapter id")
        _safe_id(authority["skill_root_id"], "opaque skill root id")
    else:
        raise LineageError("source authority kind is invalid")
    _validate_snapshot(value["baseline"], kind, "source baseline")
    _validate_snapshot(value["current"], kind, "source current")
    drift = value["byte_drift"]
    require(
        type(drift) is dict
        and set(drift) == {"status", "summary"}
        and drift["status"] in {"changed", "unchanged", "unresolved"},
        "source byte drift schema drift",
    )
    _nonempty(drift["summary"], "source byte drift summary")
    baseline_tree = _snapshot_tree(value["baseline"])
    current_tree = _snapshot_tree(value["current"])
    expected = (
        "unresolved"
        if baseline_tree is None or current_tree is None
        else "unchanged"
        if baseline_tree == current_tree
        else "changed"
    )
    require(drift["status"] == expected, "source byte drift claim is inconsistent")
    return value


def _validate_candidate(
    repository: Path,
    value: object,
    *,
    budget: _CaptureBudget | None = None,
) -> None:
    if budget is None:
        budget = _new_validation_budget()
    budget.require_time()
    require(
        type(value) is dict
        and set(value)
        == {
            "basis",
            "package_projection_contract",
            "packages",
            "packages_sha256",
            "refreshed_at_utc",
            "repository_id",
        },
        "candidate schema drift",
    )
    require(value["repository_id"] == "nisavid/agents", "candidate repository drift")
    basis = value["basis"]
    require(
        type(basis) is dict
        and set(basis) == {"commit_sha1", "committed_at_utc", "tree_sha1"},
        "candidate basis schema drift",
    )
    commit = _sha1(basis["commit_sha1"], "candidate commit")
    tree = _sha1(basis["tree_sha1"], "candidate tree")
    require(
        commit == CANDIDATE_COMMIT_SHA1 and tree == CANDIDATE_TREE_SHA1,
        "candidate basis identity drift",
    )
    committed_at = _utc(basis["committed_at_utc"], "candidate commit")
    refreshed_at = _utc(value["refreshed_at_utc"], "candidate refresh")
    require(
        committed_at == CANDIDATE_COMMITTED_AT_UTC
        and refreshed_at == CANDIDATE_REFRESHED_AT_UTC
        and committed_at <= refreshed_at,
        "candidate refresh timestamp drift",
    )
    require(
        value["package_projection_contract"] == "agent-plugin-tree-v1",
        "candidate package projection contract drift",
    )
    packages = value["packages"]
    require(
        type(packages) is list
        and [item.get("id") for item in packages] == list(DISTRIBUTIONS),
        "candidate package inventory drift",
    )
    require(
        value["packages_sha256"] == canonical_sha256(packages),
        "candidate package aggregate drift",
    )
    with _tree_root_descriptor(repository, budget) as repository_descriptor:
        for package in packages:
            budget.require_time()
            require(
                type(package) is dict
                and set(package)
                == {
                    "entry_count",
                    "git_tree_sha1",
                    "id",
                    "identity_artifacts",
                    "package_tree_sha256",
                    "plugin_manifest_sha256",
                    "plugin_root",
                    "total_bytes",
                    "version",
                },
                "candidate package schema drift",
            )
            identifier = package["id"]
            root_relative = f"plugins/{identifier}"
            require(
                package["plugin_root"] == root_relative,
                "candidate plugin root drift",
            )
            require(
                package["git_tree_sha1"] == CANDIDATE_PACKAGE_GIT_TREES[identifier],
                "candidate package Git tree identity drift",
            )
            plugin_raw = _read_relative_file_at(
                repository_descriptor,
                Path(root_relative) / "plugin.json",
                budget=budget,
                max_bytes=MAX_VALIDATION_FILE_BYTES,
            )
            require(
                package["plugin_manifest_sha256"]
                == "sha256:" + hashlib.sha256(plugin_raw).hexdigest(),
                "candidate plugin manifest identity drift",
            )
            budget.require_time()
            plugin = _strict_json(plugin_raw, "candidate plugin manifest")
            require(
                type(plugin) is dict
                and plugin.get("name") == identifier
                and plugin.get("version") == package["version"],
                "candidate plugin identity drift",
            )
            artifacts = package["identity_artifacts"]
            require(
                type(artifacts) is list
                and bool(artifacts)
                and artifacts
                == sorted(artifacts, key=lambda item: item.get("path", "")),
                "candidate identity artifact inventory drift",
            )
            for artifact in artifacts:
                require(
                    type(artifact) is dict and set(artifact) == {"path", "sha256"},
                    "candidate identity artifact schema drift",
                )
                relative = _relative_path(
                    artifact["path"],
                    "candidate identity artifact",
                )
                try:
                    artifact_raw = _read_relative_file_at(
                        repository_descriptor,
                        Path(relative),
                        budget=budget,
                        max_bytes=MAX_VALIDATION_FILE_BYTES,
                    )
                except LineageError as error:
                    if str(error) == VALIDATION_LIMIT_DIAGNOSTIC:
                        raise
                    raise LineageError(
                        "candidate identity artifact is missing"
                    ) from None
                require(
                    artifact["sha256"]
                    == "sha256:" + hashlib.sha256(artifact_raw).hexdigest(),
                    "candidate identity artifact drift",
                )
            observed = _tree_identity_relative_at(
                repository_descriptor,
                Path(root_relative),
                budget,
            )
            require(
                package["package_tree_sha256"] == observed["tree_sha256"]
                and package["entry_count"] == observed["entry_count"]
                and package["total_bytes"] == observed["total_bytes"],
                "candidate package tree identity drift",
            )
            receipt = (
                package["entry_count"],
                package["total_bytes"],
                package["package_tree_sha256"],
                package["plugin_manifest_sha256"],
                package["version"],
                tuple((item["path"], item["sha256"]) for item in artifacts),
            )
            require(
                receipt == CANDIDATE_PACKAGE_RECEIPTS[identifier],
                "candidate package pinned projection drift",
            )


def _local_license_evidence_receipts(sources: list[dict]) -> dict[str, str]:
    evidence: dict[str, str] = {}
    for source in sources:
        for snapshot_name in ("baseline", "current"):
            snapshot = source[snapshot_name]
            if snapshot.get("status") != "resolved":
                continue
            license_value = snapshot["license"]
            if license_value.get("status") != "resolved":
                continue
            evidence_ref = license_value["evidence_ref"]
            if evidence_ref.startswith("https://"):
                continue
            evidence_sha256 = license_value["evidence_sha256"]
            if evidence_ref in evidence:
                require(
                    evidence[evidence_ref] == evidence_sha256,
                    "source license evidence binding drift",
                )
            else:
                evidence[evidence_ref] = evidence_sha256
    return {relative: evidence[relative] for relative in sorted(evidence)}


def _validate_local_license_evidence(
    repository: Path,
    sources: list[dict],
    budget: _CaptureBudget,
) -> None:
    evidence = _local_license_evidence_receipts(sources)
    if not evidence:
        return
    with _tree_root_descriptor(repository, budget) as repository_descriptor:
        for evidence_ref, expected_sha256 in evidence.items():
            budget.require_time()
            try:
                raw = _read_relative_file_at(
                    repository_descriptor,
                    Path(evidence_ref),
                    budget=budget,
                    max_bytes=MAX_VALIDATION_FILE_BYTES,
                )
            except LineageError as error:
                if str(error) == VALIDATION_LIMIT_DIAGNOSTIC:
                    raise
                raise LineageError("source license evidence is missing") from None
            require(
                "sha256:" + hashlib.sha256(raw).hexdigest() == expected_sha256,
                "source license evidence drift",
            )


def validate_source_manifest(
    repository: Path,
    value: dict,
    *,
    budget: _CaptureBudget | None = None,
) -> dict[str, dict]:
    if budget is None:
        budget = _new_validation_budget()
    _validate_content_document(value, SOURCE_MANIFEST_FIELDS, "source manifest")
    require(
        type(value["schema_version"]) is int and value["schema_version"] == 1,
        "source manifest schema version drift",
    )
    require(
        value["contract"] == "coordinated-source-skill-manifest-v1",
        "source manifest contract drift",
    )
    research_observed_at = _utc(
        value["research_observed_at_utc"], "source manifest research observation"
    )
    scope = value["scope"]
    require(
        type(scope) is dict
        and set(scope) == {"distribution_ids", "host_profile_ids", "source_families"}
        and tuple(scope["distribution_ids"]) == DISTRIBUTIONS
        and tuple(scope["host_profile_ids"]) == tuple(sorted(HOST_MANIFESTS))
        and tuple(scope["source_families"]) == SOURCE_FAMILIES,
        "source manifest scope drift",
    )
    _validate_candidate(repository, value["candidate"], budget=budget)
    require(
        research_observed_at == RESEARCH_OBSERVED_AT_UTC
        and research_observed_at <= value["candidate"]["refreshed_at_utc"],
        "source manifest research observation drift",
    )
    sources = value["sources"]
    require(
        type(sources) is list and bool(sources), "source inventory must be non-empty"
    )
    require(
        [item.get("id") for item in sources]
        == sorted({item.get("id") for item in sources}),
        "source inventory must be sorted and unique",
    )
    validated = {
        item["id"]: _validate_source(item, SOURCE_FAMILIES) for item in sources
    }
    candidate_commit = value["candidate"]["basis"]["commit_sha1"]
    candidate_packages = {
        package["id"]: package for package in value["candidate"]["packages"]
    }
    for distribution_id in DISTRIBUTIONS:
        source = validated[f"ivan-{distribution_id}"]
        package = candidate_packages[distribution_id]
        current = source["current"]
        require(
            source["authority"]
            == {
                "kind": "git",
                "refresh_ref": candidate_commit,
                "repository_url": "https://github.com/nisavid/agents",
                "skill_root": package["plugin_root"],
            }
            and current["status"] == "resolved"
            and current["commit_sha1"] == candidate_commit
            and current["tree_sha1"] == package["git_tree_sha1"]
            and current["skill_tree_sha256"] == package["package_tree_sha256"]
            and current["entry_count"] == package["entry_count"]
            and current["total_bytes"] == package["total_bytes"],
            "candidate source snapshot drift",
        )
    require(
        {item["family"] for item in sources} == set(SOURCE_FAMILIES),
        "source family coverage drift",
    )
    _privacy_scan(value, "source manifest")
    require(
        canonical_sha256(sources) == SOURCE_EVIDENCE_INVENTORY_SHA256,
        "source evidence inventory drift",
    )
    _validate_local_license_evidence(repository, sources, budget)
    return validated


def _validate_semantic_drift(value: object, source: dict, mapping: str) -> None:
    require(
        type(value) is dict
        and set(value) == {"status", "summary"}
        and value["status"] in {"changed", "unchanged", "unresolved"},
        "semantic drift schema drift",
    )
    _nonempty(value["summary"], "semantic drift summary")
    byte_status = source["byte_drift"]["status"]
    require(
        not (
            (mapping == "unresolved" or byte_status == "unresolved")
            and value["status"] != "unresolved"
        ),
        "semantic drift is inconsistent with available evidence",
    )


def validate_contribution_ledger(
    repository: Path,
    value: dict,
    source_raw: bytes,
    sources: dict[str, dict],
    *,
    budget: _CaptureBudget | None = None,
) -> set[str]:
    if budget is None:
        budget = _new_validation_budget()
    _validate_content_document(value, LEDGER_FIELDS, "contribution ledger")
    require(
        type(value["schema_version"]) is int and value["schema_version"] == 1,
        "contribution ledger schema version drift",
    )
    require(
        value["contract"] == "coordinated-source-skill-contribution-ledger-v1",
        "contribution ledger contract drift",
    )
    require(
        value["limitations"] == LIMITATIONS, "contribution ledger limitations drift"
    )
    source_binding = value["source_manifest"]
    require(
        type(source_binding) is dict
        and set(source_binding) == {"path", "sha256"}
        and source_binding["path"] == SOURCE_MANIFEST.as_posix()
        and source_binding["sha256"]
        == "sha256:" + hashlib.sha256(source_raw).hexdigest(),
        "contribution ledger source-manifest binding drift",
    )
    contributions = value["contributions"]
    require(
        type(contributions) is list
        and bool(contributions)
        and [item.get("id") for item in contributions]
        == sorted({item.get("id") for item in contributions}),
        "contribution inventory must be sorted and unique",
    )
    source_coverage = set()
    distribution_coverage = set()
    unresolved_ids = set()
    mapped_evidence_paths = []
    for contribution in contributions:
        require(type(contribution) is dict, "contribution must be an object")
        require(
            "disposition" not in contribution, "contribution disposition is forbidden"
        )
        _safe_id(contribution.get("id"), "contribution id")
        source_id = contribution.get("source_id")
        require(source_id in sources, "contribution source is unresolved")
        source_coverage.add(source_id)
        _nonempty(contribution.get("behavior"), "contribution behavior")
        mapping = contribution.get("mapping_status")
        if mapping == "mapped":
            require(
                set(contribution)
                == {
                    "behavior",
                    "destination",
                    "historical_relationship",
                    "id",
                    "mapping_status",
                    "semantic_drift",
                    "source_id",
                },
                "mapped contribution schema drift",
            )
            require(
                contribution["historical_relationship"]
                in {
                    "candidate-authored",
                    "comparison-only",
                    "input",
                    "retained-specialist",
                },
                "contribution historical relationship drift",
            )
            destination = contribution["destination"]
            require(
                type(destination) is dict
                and set(destination)
                == {"distribution_id", "evidence_paths", "owner_id"},
                "contribution destination schema drift",
            )
            distribution = destination["distribution_id"]
            require(
                distribution in DISTRIBUTIONS, "contribution distribution is invalid"
            )
            distribution_coverage.add(distribution)
            _safe_id(destination["owner_id"], "contribution owner")
            evidence_paths = _string_list(
                destination["evidence_paths"], "contribution evidence paths"
            )
            owns_path = False
            for raw_path in evidence_paths:
                relative = _relative_path(raw_path, "contribution evidence path")
                mapped_evidence_paths.append(Path(relative))
                owns_path |= relative.startswith(f"plugins/{distribution}/")
            require(owns_path, "contribution evidence is not owned by its distribution")
        elif mapping == "unresolved":
            require(
                set(contribution)
                == {
                    "behavior",
                    "candidate_distribution_ids",
                    "evidence_needed",
                    "id",
                    "mapping_status",
                    "reason",
                    "semantic_drift",
                    "source_id",
                },
                "unresolved contribution schema drift",
            )
            candidate_ids = _string_list(
                contribution["candidate_distribution_ids"],
                "unresolved candidate distributions",
                safe_ids=True,
            )
            require(
                set(candidate_ids) <= set(DISTRIBUTIONS),
                "unresolved distribution is invalid",
            )
            distribution_coverage.update(candidate_ids)
            _nonempty(contribution["reason"], "unresolved contribution reason")
            _string_list(
                contribution["evidence_needed"], "unresolved contribution evidence"
            )
        else:
            raise LineageError("contribution mapping status is invalid")
        _validate_semantic_drift(
            contribution["semantic_drift"], sources[source_id], mapping
        )
        if (
            mapping == "unresolved"
            or contribution["semantic_drift"]["status"] == "unresolved"
        ):
            unresolved_ids.add(contribution["id"])
    require(
        source_coverage == set(sources), "contribution coverage does not match sources"
    )
    require(
        distribution_coverage == set(DISTRIBUTIONS),
        "distribution contribution coverage drift",
    )
    _privacy_scan(value, "contribution ledger")
    require(
        canonical_sha256(contributions) == CONTRIBUTION_EVIDENCE_INVENTORY_SHA256,
        "contribution evidence inventory drift",
    )
    mapped_evidence_inventory = {
        relative.as_posix() for relative in mapped_evidence_paths
    }
    require(
        tuple(CONTRIBUTION_EVIDENCE_RECEIPTS)
        == tuple(sorted(mapped_evidence_inventory))
        and all(
            type(digest) is str and SHA256.fullmatch(digest) is not None
            for digest in CONTRIBUTION_EVIDENCE_RECEIPTS.values()
        ),
        "contribution evidence inventory drift",
    )
    try:
        with _tree_root_descriptor(repository, budget) as repository_descriptor:
            for raw_relative, expected_sha256 in CONTRIBUTION_EVIDENCE_RECEIPTS.items():
                evidence_raw = _read_relative_file_at(
                    repository_descriptor,
                    Path(raw_relative),
                    budget=budget,
                    max_bytes=MAX_VALIDATION_FILE_BYTES,
                )
                require(
                    "sha256:" + hashlib.sha256(evidence_raw).hexdigest()
                    == expected_sha256,
                    "contribution evidence content drift",
                )
    except LineageError as error:
        if str(error) in {
            VALIDATION_LIMIT_DIAGNOSTIC,
            "contribution evidence content drift",
        }:
            raise
        raise LineageError("contribution evidence path is missing") from None
    return unresolved_ids


def _matched_snapshots(installation: dict, source: dict) -> list[str]:
    matches = []
    for label in ("baseline", "current"):
        snapshot = source[label]
        if (
            snapshot.get("status") == "resolved"
            and installation["skill_ids"] == source["skill_ids"]
            and snapshot["skill_tree_sha256"] == installation["skill_tree_sha256"]
        ):
            matches.append(label)
    return matches


def validate_host_manifest(
    value: dict,
    profile_id: str,
    source_raw: bytes,
    sources: dict[str, dict],
    *,
    budget: _CaptureBudget | None = None,
) -> set[str]:
    if budget is None:
        budget = _new_validation_budget()
    budget.require_time()
    _privacy_scan(value, "installed-host manifest")
    _validate_content_document(value, HOST_FIELDS, "installed-host manifest")
    require(
        type(value["schema_version"]) is int and value["schema_version"] == 1,
        "installed-host schema version drift",
    )
    require(
        value["contract"] == "coordinated-installed-source-skill-manifest-v1",
        "installed-host contract drift",
    )
    require(value["profile_id"] == profile_id, "installed-host profile drift")
    _utc(value["observed_at_utc"], "installed-host observation")
    require(
        value["discovery_precedence"] == HOST_DISCOVERY_PRECEDENCE[profile_id],
        "installed-host discovery precedence drift",
    )
    source_binding = value["source_manifest"]
    require(
        type(source_binding) is dict
        and set(source_binding) == {"path", "sha256"}
        and source_binding["path"] == SOURCE_MANIFEST.as_posix()
        and source_binding["sha256"]
        == "sha256:" + hashlib.sha256(source_raw).hexdigest(),
        "installed-host source-manifest binding drift",
    )
    observations = value["source_observations"]
    require(
        type(observations) is list
        and [item.get("source_id") for item in observations] == sorted(sources),
        "host source coverage drift",
    )
    unresolved = {"discovery-precedence"}
    for observation in observations:
        budget.require_time()
        require(type(observation) is dict, "host source observation must be an object")
        source_id = observation["source_id"]
        status_value = observation.get("status")
        if status_value == "installed":
            require(
                set(observation)
                == {
                    "installations",
                    "source_id",
                    "status",
                    "unobserved_skill_ids",
                },
                "installed source observation schema drift",
            )
            source_skill_ids = set(sources[source_id]["skill_ids"])
            unobserved_skill_ids = set(
                _string_list(
                    observation["unobserved_skill_ids"],
                    "unobserved source skill ids",
                    safe_ids=True,
                    allow_empty=True,
                )
            )
            installations = observation["installations"]
            require(
                type(installations) is list
                and bool(installations)
                and [item.get("installation_id") for item in installations]
                == sorted({item.get("installation_id") for item in installations}),
                "installed source routes must be sorted and unique",
            )
            observed_skill_ids = set()
            for installation in installations:
                budget.require_time()
                require(
                    type(installation) is dict
                    and set(installation)
                    == {
                        "entry_count",
                        "installation_id",
                        "matched_snapshots",
                        "skill_ids",
                        "skill_tree_sha256",
                        "total_bytes",
                    },
                    "installed source route schema drift",
                )
                _public_host_route_id(installation["installation_id"])
                installation_skill_ids = set(
                    _string_list(
                        installation["skill_ids"],
                        "installed source skill ids",
                        safe_ids=True,
                    )
                )
                require(
                    bool(installation_skill_ids)
                    and installation_skill_ids <= source_skill_ids,
                    "installed source skill coverage drift",
                )
                observed_skill_ids.update(installation_skill_ids)
                _sha256(installation["skill_tree_sha256"], "installed skill tree")
                require(
                    type(installation["entry_count"]) is int
                    and installation["entry_count"] >= 1
                    and type(installation["total_bytes"]) is int
                    and installation["total_bytes"] >= 1,
                    "installed source route bounds are invalid",
                )
                require(
                    installation["matched_snapshots"]
                    == _matched_snapshots(installation, sources[source_id]),
                    "installed source matched snapshots drift",
                )
                budget.require_time()
            require(
                not (observed_skill_ids & unobserved_skill_ids)
                and observed_skill_ids | unobserved_skill_ids == source_skill_ids,
                "installed source skill coverage drift",
            )
            require(
                installations == _assigned_host_routes(source_id, installations),
                "installed source route identity drift",
            )
            if unobserved_skill_ids:
                unresolved.add(source_id)
        elif status_value == "absent":
            require(
                set(observation) == {"source_id", "status"},
                "absent source observation schema drift",
            )
        elif status_value == "not-applicable":
            require(
                set(observation) == {"reason", "source_id", "status"},
                "not-applicable source observation schema drift",
            )
            require(
                observation["reason"] in HOST_NOT_APPLICABLE_REASONS.values(),
                "not-applicable source reason is not a public template",
            )
        elif status_value == "unresolved":
            require(
                set(observation)
                == {"evidence_needed", "reason", "source_id", "status"},
                "unresolved source observation schema drift",
            )
            evidence_needed = _string_list(
                observation["evidence_needed"], "unresolved host source evidence"
            )
            require(
                any(
                    observation["reason"] == reason
                    and evidence_needed == list(expected_evidence)
                    for reason, expected_evidence in HOST_UNRESOLVED_REASONS.values()
                ),
                "unresolved host source evidence is not a public template",
            )
            unresolved.add(source_id)
        else:
            raise LineageError("host source observation status is invalid")
        budget.require_time()
    budget.require_time()
    return unresolved


def _lineage_snapshot_files(snapshot: dict[str, object]) -> dict[str, bytes]:
    files = snapshot.get("files")
    require(
        type(files) is dict
        and all(
            type(relative) is str and type(raw) is bytes
            for relative, raw in files.items()
        ),
        "source-lineage artifact tree drift",
    )
    return files


def _lineage_file(snapshot_files: dict[str, bytes], relative: Path) -> bytes:
    try:
        key = relative.relative_to(LINEAGE_ROOT).as_posix()
        raw = snapshot_files[key]
    except (KeyError, ValueError):
        raise LineageError("source-lineage artifact tree drift") from None
    return raw


def _validate_lineage_view(repository: Path, view: _LineageView) -> dict:
    budget = _new_validation_budget()
    _require_lineage_view_binding(view)
    try:
        report_raw = _read_relative_file_at(
            view.root_descriptor,
            RESEARCH_REPORT,
            budget=budget,
            max_bytes=MAX_VALIDATION_FILE_BYTES,
        )
    except LineageError as error:
        if str(error) == VALIDATION_LIMIT_DIAGNOSTIC:
            raise
        raise LineageError("source-lineage research report evidence drift") from None
    _validate_research_report_bytes(report_raw)
    snapshot = _lineage_snapshot_at(
        view.release_descriptor,
        LINEAGE_ROOT.name,
        budget=budget,
    )
    snapshot_files = _lineage_snapshot_files(snapshot)
    budget.require_time()
    source, source_raw = _read_document_bytes(
        _lineage_file(snapshot_files, SOURCE_MANIFEST), "source manifest"
    )
    sources = validate_source_manifest(repository, source, budget=budget)
    budget.require_time()
    ledger, _ = _read_document_bytes(
        _lineage_file(snapshot_files, CONTRIBUTION_LEDGER), "contribution ledger"
    )
    unresolved_contributions = validate_contribution_ledger(
        repository,
        ledger,
        source_raw,
        sources,
        budget=budget,
    )
    unresolved_hosts = set()
    for profile_id, relative in HOST_MANIFESTS.items():
        budget.require_time()
        host, _ = _read_document_bytes(
            _lineage_file(snapshot_files, relative),
            f"installed-host manifest: {profile_id}",
        )
        host_unresolved = validate_host_manifest(
            host,
            profile_id,
            source_raw,
            sources,
            budget=budget,
        )
        budget.require_time()
        unresolved_hosts.update(
            f"{profile_id}:{source_id}" for source_id in host_unresolved
        )
        require(
            canonical_sha256(
                {
                    "discovery_precedence": host["discovery_precedence"],
                    "source_observations": host["source_observations"],
                }
            )
            == HOST_EVIDENCE_INVENTORY_SHA256[profile_id],
            "installed-host evidence inventory drift",
        )
        budget.require_time()
    budget.require_time()
    summary = {
        "candidate": source["candidate"],
        "source_ids": sorted(sources),
        "unresolved_contribution_ids": sorted(unresolved_contributions),
        "unresolved_host_observation_ids": sorted(unresolved_hosts),
        "unresolved_source_ids": sorted(
            source_id
            for source_id, item in sources.items()
            if item["baseline"]["status"] == "unresolved"
            or item["current"]["status"] == "unresolved"
            or item["baseline"].get("license", {}).get("status") == "unresolved"
            or item["current"].get("license", {}).get("status") == "unresolved"
        ),
    }
    _require_lineage_view_binding(view)
    budget.require_time()
    return summary


def _validate_lineage_schema(repository: Path, view: _LineageView) -> dict:
    try:
        return _validate_lineage_view(repository, view)
    except LineageError:
        raise
    except (
        AttributeError,
        IndexError,
        KeyError,
        RecursionError,
        TypeError,
        ValueError,
    ):
        raise LineageError("source-lineage schema drift") from None


def validate_lineage(
    repository: Path,
    artifacts_root: Path | None = None,
    *,
    acquire_lock: bool = True,
) -> dict:
    repository = Path(os.path.abspath(os.fspath(repository)))
    if acquire_lock:
        with _lineage_lock(repository, exclusive=False) as locked_view:
            if artifacts_root is None:
                return _validate_lineage_schema(repository, locked_view)
            return validate_lineage(repository, artifacts_root, acquire_lock=False)
    artifacts_root = (
        repository
        if artifacts_root is None
        else Path(os.path.abspath(os.fspath(artifacts_root)))
    )
    with _verified_lineage_parent(artifacts_root) as view:
        return _validate_lineage_schema(repository, view)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("repository", nargs="?", type=Path, default=Path.cwd())
    parser.add_argument("--artifacts-root", type=Path)
    parser.add_argument("--receipt-summary", action="store_true")
    arguments = parser.parse_args()
    try:
        summary = validate_lineage(
            arguments.repository,
            arguments.artifacts_root,
            acquire_lock=not arguments.receipt_summary,
        )
    except (LineageError, OSError) as error:
        print(f"source-skill-lineage: {error}", file=sys.stderr)
        return 1
    if arguments.receipt_summary:
        receipt_summary = {
            "candidate_packages_sha256": summary["candidate"]["packages_sha256"],
            "source_ids": summary["source_ids"],
            "unresolved_contribution_ids": summary["unresolved_contribution_ids"],
            "unresolved_host_observation_ids": summary[
                "unresolved_host_observation_ids"
            ],
            "unresolved_source_ids": summary["unresolved_source_ids"],
        }
        sys.stdout.buffer.write(canonical_bytes(receipt_summary) + b"\n")
        return 0
    print("source-skill-lineage-valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
