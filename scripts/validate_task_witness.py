#!/usr/bin/env python3
"""Validate the code-only Task Witness Agent Plugin package boundary."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import pwd
import re
import runpy
import stat
import sys
from collections import deque
from pathlib import Path

SCRIPT_DIRECTORY = Path(__file__).absolute().parent
sys.path.insert(0, str(SCRIPT_DIRECTORY))

from agent_plugins_standard import (
    discover_direct_skills,
    load_agent_plugin_manifest,
)

PLUGIN_RELATIVE = Path("plugins/task-witness")
MAX_JSON_BYTES = 1024 * 1024
TASK_WITNESS_REVIEW_STDERR_MAX_BYTES = 4096
TASK_WITNESS_REVIEW_TIMEOUT_SECONDS = 65.0
SOURCE_SHAPE_RECORD_RELATIVE = Path("release/task-witness/source-shape-review.json")
PUBLIC_RELEASE_REGISTRATION_RELATIVE = Path(
    "release/task-witness/public-release-registration.json"
)
MARKETPLACE_RELATIVE = Path(".claude-plugin/marketplace.json")
PROMOTION_PATHS = (
    MARKETPLACE_RELATIVE.as_posix(),
    PUBLIC_RELEASE_REGISTRATION_RELATIVE.as_posix(),
)
PROMOTION_ANCESTOR_PATHS = (
    ".claude-plugin",
    "release",
    "release/task-witness",
)
TASK_WITNESS_MARKETPLACE_ROUTE = {
    "name": "task-witness",
    "source": "./plugins/task-witness",
    "category": "developer-tools",
}
BRIDGE_IDENTITY_RELATIVE = Path("release/task-witness/tw4-bridge-identity.json")
BRIDGE_PROVENANCE_RELATIVE = Path("release/task-witness/tw4-bridge-provenance.json")
SUITE_INVENTORY_RELATIVE = Path("release/task-witness/tw4-suite-inventory.json")
MIGRATION_RELATIVE = Path("release/task-witness/migration")
SUITE_INVENTORY_CONTRACT = "task-witness-tw4-suite-inventory-v1"
SUITE_DRIVER_ARGV_PREFIX = (
    "-I",
    "-B",
    "scripts/run_task_witness_qualification_suite.py",
    "--suite",
)
SUITE_PROJECTIONS = (
    ("client-common", "common", ("macos-arm64", "linux-x86_64")),
    ("deployment-common", "common", ("macos-arm64", "linux-x86_64")),
    ("package-contract", "common", ("macos-arm64", "linux-x86_64")),
    ("qualification-runner-contract", "common", ("macos-arm64", "linux-x86_64")),
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
FREEZE5_COMMIT_SHA1 = "96608a9b91d4dcf3f468a4fab1f0e008c9c32b36"
BRIDGE_COMMIT_TIMESTAMP = "1786517677 -0400"
EXPECTED_BRIDGE_IDENTITIES = {
    "freeze5": {
        "commit_sha1": FREEZE5_COMMIT_SHA1,
        "tree_sha1": "4d7133e64e1322743781b24e295ae4626835eed5",
        "plugin_subtree_sha256": (
            "1fa2e1ea237bc4be175ff42478fd209ff6f57c59d8cbdc0cef592492c7eea749"
        ),
        "controller_sha256": (
            "8dc51b2a644e30d1f7c4f3b71711698b4130b43f1517e9f5361c6d1a0f7d6cfe"
        ),
        "policy_sha256": (
            "23e84f210ba69ef79e02bfc3039b2c8be3b91153d7649009b3a22850f5086245"
        ),
        "client_sha256": (
            "778186f6a460655a8b390c831e05c233171236898663ad4155bd45695597c6cf"
        ),
    },
    "bridge": {
        "commit_sha1": "391112a2f222d966a3dc54da953594667227d6d3",
        "tree_sha1": "8027107354602a5d16a53183e3002c2bed892ef6",
        "plugin_subtree_sha256": (
            "1056ff94dc73575932cc37f94f96ccb54324cd60dc83af4cce5951a17fd959f4"
        ),
        "controller_sha256": (
            "671693603673e8e895301620817c7fa15a96a37365cff59298d8261ee923a6b3"
        ),
        "policy_sha256": (
            "23e84f210ba69ef79e02bfc3039b2c8be3b91153d7649009b3a22850f5086245"
        ),
        "client_sha256": (
            "912cba0f5b93900d4caaf651c81a3ef3b10f65b837f2c038db5c232d8b71d875"
        ),
    },
}
BRIDGE_SNAPSHOT_PATHS = {
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
EXPECTED_MIGRATION_DIRECTORIES = {
    "bridge",
    "bridge/.claude-plugin",
    "bridge/.codex-plugin",
    "bridge/client",
    "bridge/controller",
    "freeze5",
    "freeze5/.claude-plugin",
    "freeze5/.codex-plugin",
    "freeze5/client",
    "freeze5/controller",
}
EXPECTED_MIGRATION_FILES = {
    f"{generation}/{relative}"
    for generation, paths in BRIDGE_SNAPSHOT_PATHS.items()
    for relative in paths.values()
}
BRIDGE_STABLE_PATHS = {
    "client/task_witness_shim.sh.in",
    "launcher/task_witness_launch.py",
    "runtime/bundle_io.py",
    "runtime/canonical.py",
    "runtime/task_witness.py",
    "runtime/trust.py",
    "smoke/task_witness_smoke_validator.py",
}
EXPECTED_DIRECT_TEST_PACKAGES = {
    Path("tests/plugins/task_witness_client"): {
        "__init__.py",
        "_activation_smoke_driver.py",
        "_client_driver.py",
        "_control_maintenance_smoke_support.py",
        "_invocation_profile_driver.py",
        "_launcher_behavior_driver.py",
        "_launcher_module_driver.py",
        "_process_supervision_driver.py",
        "_retained_state_driver.py",
        "_shim_observer_driver.py",
        "_support.py",
        "_terminal_output_driver.py",
        "_writer_guard_driver.py",
        "test_activation_smoke.py",
        "test_compatibility_policy_v2.py",
        "test_control_maintenance_smoke.py",
        "test_invocation_profile.py",
        "test_launcher.py",
        "test_process_supervision.py",
        "test_retained_state.py",
        "test_runtime.py",
        "test_runtime_acceptance.py",
        "test_shim.py",
        "test_terminal_output.py",
    },
    Path("tests/plugins/task_witness_deployment"): {
        "__init__.py",
        "_activation_recovery_support.py",
        "_activation_support.py",
        "_bridge_transition_activation_support.py",
        "_bridge_transition_prepare_support.py",
        "_bridge_transition_stage_support.py",
        "_control_maintenance_activation_support.py",
        "_control_maintenance_followup_support.py",
        "_control_maintenance_staged_client_support.py",
        "_control_maintenance_support.py",
        "_freeze5_upgrade_recovery_support.py",
        "_manual_rollback_support.py",
        "_provider_cache_deletion_and_movement_support.py",
        "_routine_activation_support.py",
        "_routine_staged_client_support.py",
        "_routine_support.py",
        "_source_evidence_support.py",
        "_source_recovery_support.py",
        "_source_transition_support.py",
        "_support.py",
        "test_activation_recovery.py",
        "test_activation_recovery_validation.py",
        "test_activation_transactions.py",
        "test_agent_plugins_source_receipts.py",
        "test_bridge_transition_activation.py",
        "test_bridge_transition_preparation.py",
        "test_bridge_transition_staging.py",
        "test_control_maintenance_activation.py",
        "test_control_maintenance_followup.py",
        "test_control_maintenance_staged_client_integration.py",
        "test_control_maintenance_staging.py",
        "test_freeze5_upgrade_recovery.py",
        "test_manual_rollback_activation.py",
        "test_manual_rollback_preparation.py",
        "test_manual_rollback_recovery.py",
        "test_provider_cache_deletion_and_movement.py",
        "test_provider_import.py",
        "test_receipt_staging.py",
        "test_routine_activation_recovery.py",
        "test_routine_staged_client_integration.py",
        "test_routine_transactions.py",
        "test_source_evidence_first_install.py",
        "test_source_evidence_recovery.py",
        "test_source_evidence_transitions.py",
        "test_staged_client_integration.py",
        "test_transaction_result_reconciliation.py",
    },
}
EXPECTED_DIRECTORIES = {
    ".claude-plugin",
    "client",
    "controller",
    "launcher",
    "runtime",
    "smoke",
}
CLIENT_FILES = {
    "client/task_witness_client.py",
    "client/task_witness_shim.sh.in",
}
LAUNCHER_FILES = {"launcher/task_witness_launch.py"}
RUNTIME_FILES = {
    "runtime/task_witness.py",
    "runtime/canonical.py",
    "runtime/bundle_io.py",
    "runtime/trust.py",
}
TW2_CONTROL_PLANE_FILES = {
    "controller/policy.json",
    "controller/task_witness_deploy.py",
    "smoke/task_witness_smoke_validator.py",
}
REVIEWED_SOURCE_FILES = (
    CLIENT_FILES | RUNTIME_FILES | LAUNCHER_FILES | TW2_CONTROL_PLANE_FILES
)
TW0_SOURCE_FILES = RUNTIME_FILES | LAUNCHER_FILES
REVIEWED_TEST_FILES = {
    (package / name).as_posix()
    for package, names in EXPECTED_DIRECT_TEST_PACKAGES.items()
    for name in names
} | {
    "tests/test_task_witness_package.py",
    "tests/test_task_witness_qualification.py",
}
RELEASE_VALIDATOR_PATHS = ("scripts/validate_task_witness.py",)
PUBLIC_RELEASE_REGISTRATION_PATHS = (PUBLIC_RELEASE_REGISTRATION_RELATIVE.as_posix(),)
RELEASE_DOCUMENTATION_PATHS = (
    "docs/superpowers/specs/2026-07-27-task-witness-canonical-client-design.md",
    "docs/superpowers/specs/2026-08-12-task-witness-tw4-migration-and-qualification-design.md",
    "plugins/task-witness/README.md",
)
TW4_MIGRATION_EVIDENCE_PATHS = tuple(
    sorted(
        (
            BRIDGE_IDENTITY_RELATIVE.as_posix(),
            BRIDGE_PROVENANCE_RELATIVE.as_posix(),
            *(
                (MIGRATION_RELATIVE / generation / relative).as_posix()
                for generation, paths in BRIDGE_SNAPSHOT_PATHS.items()
                for relative in paths.values()
            ),
        )
    )
)
TW4_QUALIFICATION_CONTRACT_PATHS = (
    SUITE_INVENTORY_RELATIVE.as_posix(),
    "scripts/run_task_witness_qualification.py",
    "scripts/run_task_witness_qualification_suite.py",
    "tests/test_task_witness_qualification.py",
)
RELEASE_INTEGRATION_TEST_PATHS = (
    "tests/test_task_witness_package.py",
    "tests/test_task_witness_qualification.py",
)
SOURCE_SHAPE_PATHS = tuple(
    sorted(
        (PLUGIN_RELATIVE / relative).as_posix() for relative in REVIEWED_SOURCE_FILES
    )
)
SOURCE_SHAPE_SETS = {
    "tw0": tuple(
        sorted((PLUGIN_RELATIVE / relative).as_posix() for relative in TW0_SOURCE_FILES)
    ),
    "tw1_client": tuple(
        sorted((PLUGIN_RELATIVE / relative).as_posix() for relative in CLIENT_FILES)
    ),
    "tw2_control_plane": tuple(
        sorted(
            (PLUGIN_RELATIVE / relative).as_posix()
            for relative in TW2_CONTROL_PLANE_FILES
        )
    ),
    "current_control_set": SOURCE_SHAPE_PATHS,
    "direct_release_owned_tests": tuple(sorted(REVIEWED_TEST_FILES)),
    "public_release_registration": PUBLIC_RELEASE_REGISTRATION_PATHS,
    "release_documentation": RELEASE_DOCUMENTATION_PATHS,
    "release_integration": tuple(
        sorted(PUBLIC_RELEASE_REGISTRATION_PATHS + RELEASE_INTEGRATION_TEST_PATHS)
    ),
    "release_integration_tests": RELEASE_INTEGRATION_TEST_PATHS,
    "release_validator": RELEASE_VALIDATOR_PATHS,
    "tw4_migration_evidence": TW4_MIGRATION_EVIDENCE_PATHS,
    "tw4_qualification_contract": TW4_QUALIFICATION_CONTRACT_PATHS,
}
REVIEWED_SHAPE_PATHS = tuple(
    sorted({path for paths in SOURCE_SHAPE_SETS.values() for path in paths})
)
SOURCE_SHAPE_MEASUREMENT = (
    "nonblank-noncomment-lines-v2+ordered-source-byte-identity-v1"
)
SOURCE_SHAPE_REBASELINE_REQUIREMENT = "independent-source-shape-review"
SOURCE_BYTE_IDENTITY_FRAMING = "path-utf8-nul-sha256-hex-nul-v1"
SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")
SHA1_HEX = re.compile(r"[0-9a-f]{40}\Z")
TOKEN = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")
UINT64_MAX = (1 << 64) - 1
HOST_RECEIPT_MAX_BYTES = 64 * 1024 * 1024
HOST_RECEIPT_MEMBER_CAPS = {
    "qualification_candidate": 4096,
    "candidate_closure": 4096,
    "bridge_history": 16_384,
    "suite_inventory": 4096,
    "platform": 2_113_536,
    "runtime": 1_114_112,
    "rendered_shim": 65_536,
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
SYSTEM_TOOL_OBSERVATION_MAX_BYTES = 8_388_608
RUNTIME_CLOSURE_OBSERVATION_MAX_BYTES = 41_200_000
PROFILE_AND_EVIDENCE_MAX_BYTES = 1024 * 1024
DETAIL_STREAM_MAX_BYTES = 1024 * 1024
MAX_PATH_BYTES = 4096
MAX_OBSERVED_TOOL_BYTES = 64 * 1024 * 1024
MAX_RUNTIME_CLOSURE_ENTRIES = 100_000
MAX_RUNTIME_CLOSURE_ROOTS = 16
MAX_RUNTIME_REGULAR_FILE_BYTES = 1024 * 1024 * 1024
MAX_RUNTIME_TOTAL_REGULAR_BYTES = 4 * MAX_RUNTIME_REGULAR_FILE_BYTES
SYSTEM_TOOL_IDS = ("environment-clearer", "git", "posix-shell")
REQUIRED_FILESYSTEM_SEMANTICS = (
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
)
REQUIRED_RUNTIME_DEPENDENCY_CLASSES = {
    "cpython-extension-modules",
    "cpython-stdlib",
    "loader-shared-libraries",
}
EXPECTED_FILES = {
    ".claude-plugin/plugin.json",
    "README.md",
    "plugin.json",
} | REVIEWED_SOURCE_FILES
CANONICAL_IDENTITY_FIELDS = {
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
}
EXPECTED_AGENT_PLUGIN_MANIFEST = {
    "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
    "name": "task-witness",
    "version": "1.0.0",
    "description": (
        "Task Witness launches exact-byte-pinned registered validators against "
        "retained operator trust."
    ),
    "author": {"name": "Ivan D Vasin", "url": "https://github.com/nisavid"},
    "homepage": "https://github.com/nisavid/agents/tree/main/plugins/task-witness",
    "repository": "https://github.com/nisavid/agents",
    "license": "MIT",
    "keywords": ["evidence", "provenance", "validation", "trust"],
    "extensions": {
        "com.openai": {
            "interface": {
                "displayName": "Task Witness",
                "shortDescription": "Validate registered task-evidence bundles.",
                "longDescription": (
                    "Task Witness runs exact-byte-pinned, operator-approved "
                    "validators. Validators are trusted full-process Python code "
                    "with ambient user authority; Task Witness grants no workflow "
                    "authority and is not a sandbox."
                ),
                "developerName": "Ivan D Vasin",
                "category": "Developer Tools",
                "capabilities": ["Validation"],
                "websiteURL": "https://github.com/nisavid/agents/tree/main/plugins/task-witness",
            }
        }
    },
}
EXPECTED_CLAUDE_MANIFEST = {
    field: EXPECTED_AGENT_PLUGIN_MANIFEST[field] for field in CANONICAL_IDENTITY_FIELDS
} | {"displayName": "Task Witness"}
EXPECTED_PUBLIC_RELEASE_REGISTRATION = {
    "production_eligible": False,
    "schema_version": 1,
    "source_stage_validator_flags": ["--source-stage"],
    "support_paths": [
        "docs/superpowers/specs/2026-07-27-task-witness-canonical-client-design.md",
        "docs/superpowers/specs/2026-08-12-task-witness-tw4-migration-and-qualification-design.md",
        "plugins/task-witness/README.md",
        "release/task-witness/migration",
        SOURCE_SHAPE_RECORD_RELATIVE.as_posix(),
        BRIDGE_IDENTITY_RELATIVE.as_posix(),
        BRIDGE_PROVENANCE_RELATIVE.as_posix(),
        SUITE_INVENTORY_RELATIVE.as_posix(),
        "scripts/run_task_witness_qualification.py",
        "scripts/run_task_witness_qualification_suite.py",
        "tests/plugins/task_witness_client",
        "tests/plugins/task_witness_deployment",
        "tests/test_task_witness_package.py",
        "tests/test_task_witness_qualification.py",
    ],
}


def load_json(path: Path, label: str) -> dict:
    def unique_object(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} contains a duplicate key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"{label} contains a non-finite number: {value}")

    def reject_nonfinite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"{label} contains a non-finite number: {value}")
        return parsed

    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
        parse_float=reject_nonfinite_float,
    )
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain an object")
    return value


def validate_inventory(plugin: Path) -> None:
    metadata = plugin.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ValueError("Task Witness package root is invalid")
    legacy_codex_manifest = plugin / ".codex-plugin" / "plugin.json"
    if legacy_codex_manifest.exists() or legacy_codex_manifest.is_symlink():
        raise ValueError("Task Witness current legacy Codex manifest is unsupported")
    directories: set[str] = set()
    files: set[str] = set()
    for path in plugin.rglob("*"):
        relative = path.relative_to(plugin).as_posix()
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not (
            stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
        ):
            raise ValueError("Task Witness code-only inventory drift")
        if stat.S_ISDIR(metadata.st_mode):
            directories.add(relative)
        else:
            files.add(relative)
    if directories != EXPECTED_DIRECTORIES or files != EXPECTED_FILES:
        raise ValueError("Task Witness code-only inventory drift")


def validate_manifests(plugin: Path) -> None:
    canonical = load_agent_plugin_manifest(plugin)
    claude = load_json(plugin / ".claude-plugin/plugin.json", "Claude manifest")
    if canonical != EXPECTED_AGENT_PLUGIN_MANIFEST:
        raise ValueError("Task Witness canonical Agent Plugin manifest drift")
    if set(claude) != CANONICAL_IDENTITY_FIELDS | {"displayName"}:
        raise ValueError("Task Witness Claude manifest keys drift")
    for field in sorted(CANONICAL_IDENTITY_FIELDS):
        if claude[field] != canonical[field]:
            raise ValueError(f"Task Witness Claude manifest projection drift: {field}")
    if claude["displayName"] != "Task Witness":
        raise ValueError("Task Witness Claude manifest displayName drift")
    if claude != EXPECTED_CLAUDE_MANIFEST:
        raise ValueError("Task Witness Claude manifest contract drift")
    if discover_direct_skills(plugin) != ():
        raise ValueError("Task Witness Agent Plugins skill inventory drift")


def validate_public_release_registration(root: Path) -> None:
    path = root / PUBLIC_RELEASE_REGISTRATION_RELATIVE
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise ValueError(
            "Task Witness public-release registration is missing"
        ) from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(
            "Task Witness public-release registration must be a regular file"
        )
    verified_reviewed_path(root, PUBLIC_RELEASE_REGISTRATION_RELATIVE.as_posix())
    registration = load_json(path, "Task Witness public-release registration")
    if registration != EXPECTED_PUBLIC_RELEASE_REGISTRATION:
        raise ValueError("Task Witness public-release registration drift")


def require_exact_fields(value: dict, expected: set[str], label: str) -> None:
    observed = set(value)
    missing = sorted(expected - observed)
    unknown = sorted(observed - expected)
    if missing or unknown:
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise ValueError(f"{label} schema drift: {'; '.join(details)}")


def require_nonnegative_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def require_sha256_hex(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA256_HEX.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")
    return value


def source_byte_identity(entries: list[tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    for path, content_sha256 in entries:
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content_sha256.encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def validate_review_context(value: object) -> None:
    if value != {
        "protocol": {
            "launch_envelope_contract": "task-witness-launch-envelope-v1",
            "complete_anchor_contract": "task-witness-complete-anchor-v1",
            "canonical_projection_contract": "task-witness-canonical-projection-v2",
        },
        "external_review": {
            "required_evidence": "frozen-source-shape-review",
            "record_role": "source-shape-measurement-not-review-authentication",
        },
    }:
        raise ValueError("Task Witness source-shape review context drift")


def load_source_shape_record(root: Path) -> dict[str, object]:
    path = root / SOURCE_SHAPE_RECORD_RELATIVE
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise ValueError(
            "Task Witness source-shape review record is missing"
        ) from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(
            "Task Witness source-shape review record must be a regular file"
        )

    record = load_json(path, "Task Witness source-shape review record")
    require_exact_fields(
        record,
        {
            "schema_version",
            "measurement",
            "reviewed_shape",
            "source_byte_identity",
            "tripwires",
            "rebaseline_requirement",
            "review_context",
        },
        "Task Witness source-shape review record",
    )
    if (
        require_nonnegative_integer(
            record["schema_version"],
            "Task Witness source-shape review record schema version",
        )
        != 4
    ):
        raise ValueError(
            "Task Witness source-shape review record schema version is invalid"
        )
    if record["measurement"] != SOURCE_SHAPE_MEASUREMENT:
        raise ValueError(
            "Task Witness source-shape review record measurement is invalid"
        )
    if record["rebaseline_requirement"] != SOURCE_SHAPE_REBASELINE_REQUIREMENT:
        raise ValueError(
            "Task Witness source-shape review record rebaseline requirement is invalid"
        )
    validate_review_context(record["review_context"])

    reviewed_shape = record["reviewed_shape"]
    if not isinstance(reviewed_shape, dict):
        raise ValueError("Task Witness reviewed source shape must be an object")
    require_exact_fields(
        reviewed_shape,
        {
            "file_nonblank_noncomment_lines",
            "sets",
        },
        "Task Witness reviewed source shape",
    )
    file_nonblank_noncomment_lines = reviewed_shape["file_nonblank_noncomment_lines"]
    if not isinstance(file_nonblank_noncomment_lines, dict):
        raise ValueError(
            "Task Witness reviewed file nonblank/noncomment lines must be an object"
        )
    if set(file_nonblank_noncomment_lines) != set(REVIEWED_SHAPE_PATHS):
        raise ValueError("Task Witness reviewed source file path inventory drift")
    reviewed_file_lines = {
        path: require_nonnegative_integer(
            file_nonblank_noncomment_lines[path],
            f"Task Witness reviewed nonblank/noncomment line count for {path}",
        )
        for path in REVIEWED_SHAPE_PATHS
    }
    reviewed_sets = reviewed_shape["sets"]
    if not isinstance(reviewed_sets, dict):
        raise ValueError("Task Witness reviewed source sets must be an object")
    require_exact_fields(
        reviewed_sets,
        set(SOURCE_SHAPE_SETS),
        "Task Witness reviewed source sets",
    )
    reviewed_aggregates: dict[str, int] = {}
    for name, expected_paths in SOURCE_SHAPE_SETS.items():
        reviewed_set = reviewed_sets[name]
        if not isinstance(reviewed_set, dict):
            raise ValueError(f"Task Witness reviewed source set {name} is invalid")
        require_exact_fields(
            reviewed_set,
            {"paths", "aggregate_nonblank_noncomment_lines"},
            f"Task Witness reviewed source set {name}",
        )
        if reviewed_set["paths"] != list(expected_paths):
            raise ValueError(f"Task Witness reviewed source set {name} path drift")
        aggregate = require_nonnegative_integer(
            reviewed_set["aggregate_nonblank_noncomment_lines"],
            f"Task Witness reviewed source set {name} aggregate",
        )
        if aggregate != sum(reviewed_file_lines[path] for path in expected_paths):
            raise ValueError(
                f"Task Witness reviewed source set {name} aggregate is inconsistent"
            )
        reviewed_aggregates[name] = aggregate

    recorded_identity = record["source_byte_identity"]
    if not isinstance(recorded_identity, dict):
        raise ValueError("Task Witness source byte identity must be an object")
    require_exact_fields(
        recorded_identity,
        {"algorithm", "framing", "entries", "aggregate_sha256_by_set"},
        "Task Witness source byte identity",
    )
    if recorded_identity["algorithm"] != "sha256":
        raise ValueError("Task Witness source byte identity algorithm is invalid")
    if recorded_identity["framing"] != SOURCE_BYTE_IDENTITY_FRAMING:
        raise ValueError("Task Witness source byte identity framing is invalid")
    entries = recorded_identity["entries"]
    if not isinstance(entries, list):
        raise ValueError("Task Witness source byte identity entries must be a list")
    parsed_entries: list[tuple[str, str]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(
                f"Task Witness source byte identity entry {index} is invalid"
            )
        require_exact_fields(
            entry,
            {"path", "sha256"},
            f"Task Witness source byte identity entry {index}",
        )
        path_value = entry["path"]
        if not isinstance(path_value, str):
            raise ValueError(
                f"Task Witness source byte identity entry {index} path is invalid"
            )
        parsed_entries.append(
            (
                path_value,
                require_sha256_hex(
                    entry["sha256"],
                    f"Task Witness source byte identity entry {index} digest",
                ),
            )
        )
    if [path for path, _ in parsed_entries] != list(REVIEWED_SHAPE_PATHS):
        raise ValueError("Task Witness ordered source-byte identity drift")
    entries_by_path = dict(parsed_entries)
    aggregate_sha256_by_set = recorded_identity["aggregate_sha256_by_set"]
    if not isinstance(aggregate_sha256_by_set, dict):
        raise ValueError(
            "Task Witness source byte identity set aggregates must be an object"
        )
    require_exact_fields(
        aggregate_sha256_by_set,
        set(SOURCE_SHAPE_SETS),
        "Task Witness source byte identity set aggregates",
    )
    for name, paths in SOURCE_SHAPE_SETS.items():
        aggregate_sha256 = require_sha256_hex(
            aggregate_sha256_by_set[name],
            f"Task Witness source byte identity {name} aggregate digest",
        )
        scoped_entries = [(path, entries_by_path[path]) for path in paths]
        if source_byte_identity(scoped_entries) != aggregate_sha256:
            raise ValueError(
                f"Task Witness source byte identity {name} aggregate drift"
            )

    tripwires = record["tripwires"]
    if not isinstance(tripwires, dict):
        raise ValueError("Task Witness source-shape tripwires must be an object")
    require_exact_fields(
        tripwires,
        {
            "file_nonblank_noncomment_lines",
            "aggregate_nonblank_noncomment_lines",
        },
        "Task Witness source-shape tripwires",
    )
    file_tripwires = tripwires["file_nonblank_noncomment_lines"]
    if not isinstance(file_tripwires, dict):
        raise ValueError("Task Witness file source-line tripwires must be an object")
    require_exact_fields(
        file_tripwires,
        set(SOURCE_SHAPE_PATHS),
        "Task Witness file source-line tripwires",
    )
    parsed_file_tripwires = {
        path: require_nonnegative_integer(
            file_tripwires[path],
            f"Task Witness file source-line tripwire for {path}",
        )
        for path in SOURCE_SHAPE_PATHS
    }
    aggregate_tripwires = tripwires["aggregate_nonblank_noncomment_lines"]
    if not isinstance(aggregate_tripwires, dict):
        raise ValueError(
            "Task Witness aggregate source-line tripwires must be an object"
        )
    require_exact_fields(
        aggregate_tripwires,
        set(SOURCE_SHAPE_SETS),
        "Task Witness aggregate source-line tripwires",
    )
    parsed_aggregate_tripwires = {
        name: require_nonnegative_integer(
            aggregate_tripwires[name],
            f"Task Witness aggregate source-line tripwire for {name}",
        )
        for name in SOURCE_SHAPE_SETS
    }
    if any(
        reviewed_file_lines[path] > parsed_file_tripwires[path]
        for path in SOURCE_SHAPE_PATHS
    ) or any(
        reviewed_aggregates[name] > parsed_aggregate_tripwires[name]
        for name in SOURCE_SHAPE_SETS
    ):
        raise ValueError(
            "Task Witness reviewed source shape exceeds its declared tripwires"
        )
    return {
        "file_nonblank_noncomment_lines": parsed_file_tripwires,
        "aggregate_nonblank_noncomment_lines": parsed_aggregate_tripwires,
        "reviewed_file_nonblank_noncomment_lines": reviewed_file_lines,
        "reviewed_aggregate_nonblank_noncomment_lines": reviewed_aggregates,
        "source_byte_entries": parsed_entries,
    }


def nonblank_noncomment_line_count(source: str) -> int:
    return sum(
        bool(line.strip()) and not line.lstrip().startswith("#")
        for line in source.splitlines()
    )


def verified_reviewed_path(root: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if (
        not relative.parts
        or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("Task Witness reviewed source path is not repository-relative")
    current = root
    for part in relative.parts[:-1]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError as error:
            raise ValueError(
                "Task Witness reviewed source path is unavailable"
            ) from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("Task Witness reviewed source path has an unsafe ancestor")
    path = current / relative.name
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise ValueError("Task Witness reviewed source path is unavailable") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("Task Witness reviewed source path must be a regular file")
    if metadata.st_nlink != 1:
        raise ValueError("Task Witness reviewed source path must have link count one")
    return path


def verified_reviewed_paths(root: Path) -> dict[str, Path]:
    return {
        relative_path: verified_reviewed_path(root, relative_path)
        for relative_path in REVIEWED_SHAPE_PATHS
    }


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _bounded_json_object_capture(
    root: Path,
    relative: Path,
    label: str,
    *,
    canonical: bool,
    maximum: int | None = None,
    private: bool = False,
) -> tuple[dict, bytes]:
    maximum = MAX_JSON_BYTES if maximum is None else maximum
    if type(maximum) is not int or maximum <= 0:
        raise ValueError(f"{label} byte bound is invalid")
    if (
        not relative.parts
        or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"{label} path is invalid")
    directory_descriptor: int | None = None
    leaf_descriptor: int | None = None
    owned_descriptors: set[int] = set()
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    leaf_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )

    def stable_binding(metadata: os.stat_result) -> tuple[int, ...]:
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

    try:
        directory_descriptor = os.open(root, directory_flags)
        owned_descriptors.add(directory_descriptor)
        for component in relative.parts[:-1]:
            next_descriptor = os.open(
                component,
                directory_flags,
                dir_fd=directory_descriptor,
            )
            owned_descriptors.add(next_descriptor)
            directory_descriptor = next_descriptor
        leaf_descriptor = os.open(
            relative.name,
            leaf_flags,
            dir_fd=directory_descriptor,
        )
        owned_descriptors.add(leaf_descriptor)
        before = os.fstat(leaf_descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or (
                private
                and (
                    before.st_uid != os.geteuid()
                    or stat.S_IMODE(before.st_mode) != 0o600
                )
            )
        ):
            raise ValueError(f"{label} is not a private regular file")
        if before.st_size > maximum:
            raise ValueError(f"{label} is too large")
        raw = bytearray()
        while chunk := os.read(leaf_descriptor, min(64 * 1024, maximum + 1)):
            raw.extend(chunk)
            if len(raw) > maximum:
                raise ValueError(f"{label} is too large")
        after = os.fstat(leaf_descriptor)
        visible = os.stat(
            relative.name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if (
            stable_binding(before) != stable_binding(after)
            or stable_binding(after) != stable_binding(visible)
            or before.st_size != len(raw)
        ):
            raise ValueError(f"{label} identity drift")
    except OSError as error:
        raise ValueError(f"{label} is unavailable") from error
    finally:
        primary_error = sys.exception()
        close_error: OSError | None = None
        for descriptor in sorted(owned_descriptors, reverse=True):
            owned_descriptors.remove(descriptor)
            try:
                os.close(descriptor)
            except OSError as error:
                if close_error is None:
                    close_error = error
        if close_error is not None and primary_error is None:
            raise ValueError(f"{label} cannot be closed") from close_error

    def unique_object(pairs: list[tuple[str, object]]) -> dict:
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"{label} contains a duplicate key: {key}")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise ValueError(f"{label} contains a non-finite number: {value}")

    def reject_nonfinite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"{label} contains a non-finite number: {value}")
        return parsed

    try:
        parsed = json.loads(
            bytes(raw).decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
            parse_float=reject_nonfinite_float,
        )
    except (RecursionError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is invalid JSON") from error
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must contain an object")
    captured = bytes(raw)
    if canonical and captured != _canonical_json(parsed):
        raise ValueError(f"{label} is not canonical JSON")
    return parsed, captured


def _bounded_canonical_json_object(root: Path, relative: Path, label: str) -> dict:
    return _bounded_json_object_capture(
        root,
        relative,
        label,
        canonical=True,
    )[0]


def _bounded_external_canonical_json_object(
    path: Path,
    label: str,
    *,
    maximum: int | None = None,
    private: bool = False,
) -> tuple[dict, bytes]:
    """Capture one absolute external canonical document without symlink ancestors."""

    if not path.is_absolute() or path == Path(path.anchor):
        raise ValueError(f"{label} path is invalid")
    return _bounded_json_object_capture(
        Path(path.anchor),
        Path(*path.parts[1:]),
        label,
        canonical=True,
        maximum=maximum,
        private=private,
    )


def _validate_content_document(value: dict, label: str) -> None:
    supplied = require_sha256_hex(value.get("content_sha256"), f"{label} content")
    content = dict(value)
    del content["content_sha256"]
    if hashlib.sha256(_canonical_json(content)).hexdigest() != supplied:
        raise ValueError(f"{label} content digest drift")


def _exact_object(value: object, fields: set[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    require_exact_fields(value, fields, label)
    return value


def _parse_qualification_candidate(value: object, label: str) -> dict:
    candidate = _exact_object(
        value,
        {
            "repository_id",
            "commit_sha1",
            "tree_sha1",
            "plugin_subtree_sha256",
            "suite_inventory_sha256",
        },
        label,
    )
    if candidate["repository_id"] != "nisavid/agents":
        raise ValueError(f"{label} repository identity drift")
    for field in ("commit_sha1", "tree_sha1"):
        _require_sha1(candidate[field], f"{label} {field}")
    for field in ("plugin_subtree_sha256", "suite_inventory_sha256"):
        require_sha256_hex(candidate[field], f"{label} {field}")
    return candidate


def _parse_bridge_identity(value: object, label: str) -> dict:
    identity = _exact_object(
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
        identity["repository_id"] != "nisavid/agents"
        or identity["source_mode"] != "harness_snapshot"
    ):
        raise ValueError(f"{label} source identity drift")
    for field in ("commit_sha1", "tree_sha1"):
        _require_sha1(identity[field], f"{label} {field}")
    for field in (
        "plugin_subtree_sha256",
        "controller_sha256",
        "policy_sha256",
        "client_sha256",
    ):
        require_sha256_hex(identity[field], f"{label} {field}")
    return identity


def _parse_bridge_history_projection(value: object, label: str) -> dict:
    history = _exact_object(
        value,
        {
            "bridge_identity_sha256",
            "bridge_provenance_sha256",
            "freeze5",
            "bridge",
        },
        label,
    )
    for field in ("bridge_identity_sha256", "bridge_provenance_sha256"):
        require_sha256_hex(history[field], f"{label} {field}")
    for generation in ("freeze5", "bridge"):
        _parse_bridge_identity(history[generation], f"{label} {generation}")
    return history


def parse_tw4_release_manifest(value: object) -> dict:
    """Validate the closed detached TW4 release-manifest document."""

    manifest = _exact_object(
        value,
        {
            "schema_version",
            "contract",
            "qualification_candidate",
            "targets",
            "bridge_history",
            "canonical_review_evidence_sha256",
            "final_public_release",
            "migration_edge",
            "promotion_delta_sha256",
            "disposition",
            "content_sha256",
        },
        "Task Witness TW4 release manifest",
    )
    _bounded_projection(
        manifest,
        MAX_JSON_BYTES,
        "Task Witness TW4 release manifest",
    )
    if (
        type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != 1
        or manifest["contract"] != "task-witness-tw4-release-manifest-v1"
        or manifest["disposition"] != "release-qualified"
    ):
        raise ValueError("Task Witness TW4 release manifest contract drift")
    _parse_qualification_candidate(
        manifest["qualification_candidate"],
        "Task Witness TW4 release manifest qualification candidate",
    )
    targets = _exact_object(
        manifest["targets"],
        {"linux-x86_64", "macos-arm64"},
        "Task Witness TW4 release manifest targets",
    )
    for target in ("linux-x86_64", "macos-arm64"):
        require_sha256_hex(
            targets[target],
            f"Task Witness TW4 release manifest target {target}",
        )
    if targets["linux-x86_64"] == targets["macos-arm64"]:
        raise ValueError("Task Witness TW4 release manifest target identity drift")
    _parse_bridge_history_projection(
        manifest["bridge_history"],
        "Task Witness TW4 release manifest bridge history",
    )
    require_sha256_hex(
        manifest["canonical_review_evidence_sha256"],
        "Task Witness TW4 release manifest review evidence",
    )
    final = _exact_object(
        manifest["final_public_release"],
        {"commit_sha1", "tree_sha1"},
        "Task Witness TW4 release manifest final public release",
    )
    for field in ("commit_sha1", "tree_sha1"):
        _require_sha1(
            final[field],
            f"Task Witness TW4 release manifest final public release {field}",
        )
    if manifest["migration_edge"] != {
        "from": "freeze5",
        "source_mode": "harness_snapshot",
        "to": "bridge",
        "successor": "tw4",
    }:
        raise ValueError("Task Witness TW4 release manifest migration edge drift")
    require_sha256_hex(
        manifest["promotion_delta_sha256"],
        "Task Witness TW4 release manifest promotion delta",
    )
    _validate_content_document(manifest, "Task Witness TW4 release manifest")
    return manifest


def _canonical_bytes(value: object, label: str) -> bytes:
    try:
        return _canonical_json(value)
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ValueError(f"{label} is not canonical JSON data") from error


def _bounded_projection(value: object, maximum: int, label: str) -> bytes:
    raw = _canonical_bytes(value, label)
    if len(raw) > maximum:
        raise ValueError(f"{label} is too large")
    return raw


def _uint64(value: object, label: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0) or value > UINT64_MAX:
        raise ValueError(f"{label} must be an unsigned 64-bit integer")
    return value


def _bounded_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"{label} must be a nonempty string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} is not valid UTF-8 text") from error
    return value


def _token(value: object, label: str) -> str:
    result = _bounded_text(value, label)
    if TOKEN.fullmatch(result) is None:
        raise ValueError(f"{label} must be a closed token")
    return result


def _absolute_path(value: object, label: str) -> str:
    result = _bounded_text(value, label)
    path = Path(result)
    if (
        len(result.encode("utf-8")) > MAX_PATH_BYTES
        or result.startswith("//")
        or not path.is_absolute()
        or ".." in path.parts
        or str(path) != result
    ):
        raise ValueError(f"{label} must be a normalized absolute path")
    return result


def _symlink_target_components(
    target: str,
    label: str,
) -> tuple[bool, tuple[str, ...]]:
    components = tuple(
        target[1:].split("/") if target.startswith("/") else target.split("/")
    )
    if len(target.encode("utf-8")) > 1023 or len(components) > 256:
        raise ValueError(f"{label} is too large")
    return target.startswith("/"), components


def _sorted_unique_uint64s(value: object, label: str) -> list[int]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    result = [_uint64(item, f"{label} item") for item in value]
    if result != sorted(set(result)):
        raise ValueError(f"{label} must be sorted and unique")
    return result


def _parse_platform_profile(value: object) -> dict:
    label = "Task Witness host receipt platform profile"
    profile = _exact_object(
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
        label,
    )
    if (
        type(profile["schema_version"]) is not int
        or profile["schema_version"] != 1
        or profile["contract"] != "task-witness-platform-profile-v1"
        or profile["execution_environment"] != "native"
        or profile["target"] not in {"macos-arm64", "linux-x86_64"}
    ):
        raise ValueError(f"{label} contract drift")
    expected_platform = {
        "macos-arm64": ("darwin", "arm64"),
        "linux-x86_64": ("linux", "x86_64"),
    }[profile["target"]]
    platform = _exact_object(
        profile["platform"],
        {"system", "machine", "qualified_filesystem_class"},
        f"{label} platform",
    )
    if (platform["system"], platform["machine"]) != expected_platform or platform[
        "qualified_filesystem_class"
    ] != "local-private-filesystem":
        raise ValueError(f"{label} target binding drift")
    passwd = _exact_object(
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
        f"{label} passwd user",
    )
    if passwd["purpose"] != "task-witness-disposable-qualification-v1":
        raise ValueError(f"{label} passwd purpose drift")
    _token(passwd["name"], f"{label} passwd name")
    _uint64(passwd["uid"], f"{label} passwd UID", positive=True)
    _uint64(passwd["primary_gid"], f"{label} passwd primary GID")
    _sorted_unique_uint64s(passwd["supplementary_gids"], f"{label} supplementary GIDs")
    _absolute_path(passwd["home"], f"{label} passwd home")
    require_sha256_hex(
        passwd["provisioning_evidence_sha256"],
        f"{label} provisioning evidence",
    )
    native = _exact_object(
        profile["native_evidence"],
        {
            "issuer",
            "provenance",
            "qualification_class",
            "evidence_sha256",
            "container",
            "emulation",
        },
        f"{label} native evidence",
    )
    for field in ("issuer", "provenance"):
        _token(native[field], f"{label} native {field}")
    if (
        native["qualification_class"] != "task-witness-native-host-v1"
        or native["container"] is not False
        or native["emulation"] is not False
    ):
        raise ValueError(f"{label} native evidence drift")
    require_sha256_hex(native["evidence_sha256"], f"{label} native evidence")
    filesystem = _exact_object(
        profile["filesystem"],
        {"type", "evidence_sha256", "required_semantics"},
        f"{label} filesystem",
    )
    _token(filesystem["type"], f"{label} filesystem type")
    require_sha256_hex(filesystem["evidence_sha256"], f"{label} filesystem evidence")
    semantics = filesystem["required_semantics"]
    if semantics != list(REQUIRED_FILESYSTEM_SEMANTICS):
        raise ValueError(f"{label} filesystem semantics drift")
    tools = profile["system_tools"]
    if not isinstance(tools, list) or len(tools) != len(SYSTEM_TOOL_IDS):
        raise ValueError(f"{label} system tool inventory drift")
    for index, (tool, expected_id) in enumerate(
        zip(tools, SYSTEM_TOOL_IDS, strict=True)
    ):
        parsed = _exact_object(
            tool,
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
            f"{label} system tool {index}",
        )
        if parsed["id"] != expected_id:
            raise ValueError(f"{label} system tool order drift")
        for field in ("invoked_path", "resolved_path"):
            _absolute_path(parsed[field], f"{label} system tool {index} {field}")
        _uint64(parsed["length"], f"{label} system tool {index} length", positive=True)
        require_sha256_hex(parsed["sha256"], f"{label} system tool {index}")
        for field in ("uid", "gid", "mode"):
            _uint64(parsed[field], f"{label} system tool {index} {field}")
        if parsed["mode"] & 0o111 == 0 or parsed["mode"] & (
            stat.S_ISUID | stat.S_ISGID
        ):
            raise ValueError(f"{label} system tool mode drift")
    _validate_content_document(profile, label)
    _bounded_projection(profile, PROFILE_AND_EVIDENCE_MAX_BYTES, label)
    return profile


def _parse_runtime_evidence(value: object) -> dict:
    label = "Task Witness host receipt runtime evidence"
    evidence = _exact_object(
        value,
        {
            "schema_version",
            "contract",
            "content_sha256",
            "authority",
            "main_executable",
            "closure",
        },
        label,
    )
    if (
        type(evidence["schema_version"]) is not int
        or evidence["schema_version"] != 1
        or evidence["contract"] != "task-witness-runtime-closure-evidence-v1"
    ):
        raise ValueError(f"{label} contract drift")
    authority = _exact_object(
        evidence["authority"],
        {
            "supplier",
            "provenance",
            "qualification_class",
            "issuer",
            "disposition",
            "evidence_sha256",
        },
        f"{label} authority",
    )
    for field in ("supplier", "provenance", "qualification_class", "issuer"):
        _token(authority[field], f"{label} authority {field}")
    if authority["disposition"] != "qualified":
        raise ValueError(f"{label} disposition drift")
    require_sha256_hex(authority["evidence_sha256"], f"{label} authority evidence")
    executable = _exact_object(
        evidence["main_executable"],
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
        f"{label} main executable",
    )
    _absolute_path(executable["path"], f"{label} executable path")
    _uint64(executable["length"], f"{label} executable length", positive=True)
    if executable["length"] > MAX_RUNTIME_REGULAR_FILE_BYTES:
        raise ValueError(f"{label} executable length is too large")
    require_sha256_hex(executable["sha256"], f"{label} executable")
    for field in ("uid", "gid", "mode"):
        _uint64(executable[field], f"{label} executable {field}")
    if (
        executable["implementation"] != "cpython"
        or executable["mode"] & 0o111 == 0
        or executable["mode"] & (stat.S_ISUID | stat.S_ISGID)
    ):
        raise ValueError(f"{label} executable drift")
    version = _exact_object(
        executable["version"],
        {"major", "minor", "micro"},
        f"{label} version",
    )
    for field in ("major", "minor", "micro"):
        _uint64(version[field], f"{label} version {field}")
    if version["major"] != 3 or version["minor"] < 13:
        raise ValueError(f"{label} version drift")
    closure = _exact_object(
        evidence["closure"],
        {
            "inventory_contract",
            "roots",
            "dependency_classes",
            "entries",
            "entries_sha256",
            "entry_count",
            "total_regular_file_bytes",
        },
        f"{label} closure",
    )
    if closure["inventory_contract"] != "task-witness-runtime-closure-inventory-v1":
        raise ValueError(f"{label} closure contract drift")
    roots = closure["roots"]
    if (
        not isinstance(roots, list)
        or not roots
        or len(roots) > MAX_RUNTIME_CLOSURE_ROOTS
    ):
        raise ValueError(f"{label} roots drift")
    root_paths: list[str] = []
    for index, root in enumerate(roots):
        parsed_root = _exact_object(
            root,
            {"path", "role", "complete_inventory"},
            f"{label} root {index}",
        )
        path = _absolute_path(parsed_root["path"], f"{label} root {index} path")
        if Path(path) == Path(path).parent:
            raise ValueError(f"{label} root cannot be the filesystem root")
        root_paths.append(path)
        _token(parsed_root["role"], f"{label} root {index} role")
        if parsed_root["complete_inventory"] is not True:
            raise ValueError(f"{label} root inventory is incomplete")
    if root_paths != sorted(set(root_paths)):
        raise ValueError(f"{label} root order drift")
    dependencies = closure["dependency_classes"]
    if (
        not isinstance(dependencies, list)
        or any(not isinstance(item, str) for item in dependencies)
        or dependencies != sorted(set(dependencies))
        or not REQUIRED_RUNTIME_DEPENDENCY_CLASSES.issubset(dependencies)
    ):
        raise ValueError(f"{label} dependency classes drift")
    for dependency in dependencies:
        _token(dependency, f"{label} dependency class")
    entries = closure["entries"]
    if (
        not isinstance(entries, list)
        or not entries
        or len(entries) > MAX_RUNTIME_CLOSURE_ENTRIES
    ):
        raise ValueError(f"{label} entries drift")
    entry_paths: list[str] = []
    regular_total = 0
    executable_entry: dict | None = None
    for index, entry_value in enumerate(entries):
        if not isinstance(entry_value, dict):
            raise ValueError(f"{label} entry {index} must be an object")
        kind = entry_value.get("kind")
        common = {"path", "kind", "role", "uid", "gid"}
        if kind == "regular-file":
            entry = _exact_object(
                entry_value,
                common | {"length", "sha256", "mode"},
                f"{label} entry {index}",
            )
            length = _uint64(entry["length"], f"{label} entry {index} length")
            if length > MAX_RUNTIME_REGULAR_FILE_BYTES:
                raise ValueError(f"{label} entry length is too large")
            regular_total += length
            if regular_total > MAX_RUNTIME_TOTAL_REGULAR_BYTES:
                raise ValueError(f"{label} regular byte total is too large")
            require_sha256_hex(entry["sha256"], f"{label} entry {index}")
            _uint64(entry["mode"], f"{label} entry {index} mode")
        elif kind == "directory":
            entry = _exact_object(
                entry_value,
                common | {"mode"},
                f"{label} entry {index}",
            )
            _uint64(entry["mode"], f"{label} entry {index} mode")
        elif kind == "symlink":
            entry = _exact_object(
                entry_value,
                common | {"target"},
                f"{label} entry {index}",
            )
            target = _bounded_text(entry["target"], f"{label} entry {index} target")
            _symlink_target_components(target, f"{label} entry {index} target")
        else:
            raise ValueError(f"{label} entry {index} kind drift")
        path = _absolute_path(entry["path"], f"{label} entry {index} path")
        path_value = Path(path)
        containing_roots = [
            Path(root)
            for root in root_paths
            if path == root or path.startswith(f"{root}/")
        ]
        if not containing_roots:
            raise ValueError(f"{label} entry is outside the roots")
        containing_root = max(containing_roots, key=lambda item: len(item.parts))
        relative_depth = len(path_value.relative_to(containing_root).parts)
        directory_depth = relative_depth if kind == "directory" else relative_depth - 1
        if directory_depth > 32:
            raise ValueError(f"{label} entry exceeds the directory depth limit")
        entry_paths.append(path)
        _token(entry["role"], f"{label} entry {index} role")
        _uint64(entry["uid"], f"{label} entry {index} UID")
        _uint64(entry["gid"], f"{label} entry {index} GID")
        if path == executable["path"]:
            executable_entry = entry
    if entry_paths != sorted(set(entry_paths)):
        raise ValueError(f"{label} entry order drift")
    entry_by_path = {Path(item["path"]): item for item in entries}
    root_path_values = tuple(Path(path) for path in root_paths)

    def containing_root(path: Path) -> Path:
        matches = [
            root
            for root in root_path_values
            if path == root or path.is_relative_to(root)
        ]
        if not matches:
            raise ValueError(f"{label} symlink target is outside the roots")
        return max(matches, key=lambda item: len(item.parts))

    def absolute_target_root(target: str) -> Path:
        matches = [
            root
            for root in root_path_values
            if target == str(root) or target.startswith(f"{root}/")
        ]
        if not matches:
            raise ValueError(f"{label} symlink target is outside the roots")
        return max(matches, key=lambda item: len(item.parts))

    def target_state(
        link_path: Path,
        target: str,
        suffix: deque[str],
    ) -> tuple[Path, Path, deque[str]]:
        absolute, components = _symlink_target_components(
            target, f"{label} symlink target"
        )
        if absolute:
            root = absolute_target_root(target)
            raw_suffix = target[len(str(root)) :]
            components = () if not raw_suffix else tuple(raw_suffix[1:].split("/"))
            return root, root, deque((*components, *suffix))
        root = containing_root(link_path)
        return link_path.parent, root, deque((*components, *suffix))

    for item in entries:
        if item["kind"] != "symlink":
            continue
        link = Path(item["path"])
        current, root, pending = target_state(link, item["target"], deque())
        visited: set[tuple[Path, tuple[str, ...], Path]] = set()
        hops = 1
        while pending:
            component = pending.popleft()
            if component in {"", "."}:
                continue
            if component == "..":
                if current == root:
                    raise ValueError(f"{label} symlink target escapes its root")
                current = current.parent
                continue
            candidate_path = current / component
            candidate_entry = entry_by_path.get(candidate_path)
            if candidate_entry is None:
                raise ValueError(f"{label} symlink target is not inventoried")
            if candidate_entry["kind"] == "symlink":
                state = (candidate_path, tuple(pending), root)
                if state in visited or hops >= 32:
                    raise ValueError(f"{label} symlink target cycle")
                visited.add(state)
                hops += 1
                current, root, pending = target_state(
                    candidate_path,
                    candidate_entry["target"],
                    pending,
                )
                continue
            if pending and candidate_entry["kind"] != "directory":
                raise ValueError(f"{label} symlink target cannot be traversed")
            current = candidate_path
    if (
        executable_entry is None
        or executable_entry.get("kind") != "regular-file"
        or executable_entry.get("role") != "main-executable"
        or any(
            executable_entry.get(field) != executable[field]
            for field in ("length", "sha256", "uid", "gid", "mode")
        )
    ):
        raise ValueError(f"{label} executable closure binding drift")
    if _uint64(closure["entry_count"], f"{label} entry count") != len(entries):
        raise ValueError(f"{label} entry count drift")
    if (
        _uint64(closure["total_regular_file_bytes"], f"{label} regular byte count")
        != regular_total
    ):
        raise ValueError(f"{label} regular byte count drift")
    if (
        require_sha256_hex(closure["entries_sha256"], f"{label} entry digest")
        != hashlib.sha256(_canonical_bytes(entries, f"{label} entries")).hexdigest()
    ):
        raise ValueError(f"{label} entry digest drift")
    _validate_content_document(evidence, label)
    _bounded_projection(evidence, PROFILE_AND_EVIDENCE_MAX_BYTES, label)
    return evidence


def _parse_live_stat(value: object, label: str) -> dict:
    metadata = _exact_object(
        value,
        {
            "device",
            "inode",
            "mode",
            "uid",
            "gid",
            "nlink",
            "size",
            "mtime_ns",
            "ctime_ns",
        },
        label,
    )
    for field in metadata:
        _uint64(metadata[field], f"{label} {field}")
    return metadata


def _parse_regular_binding(value: object, label: str) -> dict:
    binding = _exact_object(
        value,
        {"path", "stat", "length", "sha256"},
        label,
    )
    _absolute_path(binding["path"], f"{label} path")
    metadata = _parse_live_stat(binding["stat"], f"{label} stat")
    length = _uint64(binding["length"], f"{label} length")
    require_sha256_hex(binding["sha256"], f"{label} digest")
    if not stat.S_ISREG(metadata["mode"]) or metadata["size"] != length:
        raise ValueError(f"{label} regular-file binding drift")
    return binding


def _parse_directory_binding(value: object, label: str) -> dict:
    binding = _exact_object(value, {"path", "stat"}, label)
    _absolute_path(binding["path"], f"{label} path")
    metadata = _parse_live_stat(binding["stat"], f"{label} stat")
    if not stat.S_ISDIR(metadata["mode"]):
        raise ValueError(f"{label} directory binding drift")
    return binding


def _parse_credential_state(
    value: object,
    target: str,
    profile: dict,
    label: str,
) -> dict:
    passwd = profile["passwd_user"]
    if target == "macos-arm64":
        credential = _exact_object(
            value,
            {
                "kind",
                "real_uid",
                "effective_uid",
                "real_gid",
                "effective_gid",
                "supplementary_gids",
                "issetugid",
            },
            label,
        )
        if (
            credential["kind"] != "darwin-issetugid-v1"
            or credential["issetugid"] is not False
        ):
            raise ValueError(f"{label} Darwin credential drift")
        uid_fields = ("real_uid", "effective_uid")
        gid_fields = ("real_gid", "effective_gid")
    else:
        credential = _exact_object(
            value,
            {
                "kind",
                "real_uid",
                "effective_uid",
                "saved_uid",
                "real_gid",
                "effective_gid",
                "saved_gid",
                "supplementary_gids",
                "capabilities",
            },
            label,
        )
        if credential["kind"] != "linux-res-id-capabilities-v1":
            raise ValueError(f"{label} Linux credential drift")
        capabilities = _exact_object(
            credential["capabilities"],
            {"ambient", "bounding", "effective", "inheritable", "permitted"},
            f"{label} capabilities",
        )
        if any(
            _uint64(item, f"{label} capability") != 0 for item in capabilities.values()
        ):
            raise ValueError(f"{label} capabilities are not empty")
        uid_fields = ("real_uid", "effective_uid", "saved_uid")
        gid_fields = ("real_gid", "effective_gid", "saved_gid")
    if any(
        _uint64(credential[field], f"{label} {field}", positive=True) != passwd["uid"]
        for field in uid_fields
    ):
        raise ValueError(f"{label} UID binding drift")
    if any(
        _uint64(credential[field], f"{label} {field}") != passwd["primary_gid"]
        for field in gid_fields
    ):
        raise ValueError(f"{label} GID binding drift")
    if (
        _sorted_unique_uint64s(
            credential["supplementary_gids"], f"{label} supplementary GIDs"
        )
        != passwd["supplementary_gids"]
    ):
        raise ValueError(f"{label} supplementary GID binding drift")
    return credential


def _parse_runtime_closure_observation(
    value: object,
    evidence: dict,
    label: str,
) -> dict:
    observation = _exact_object(value, {"contract", "roots", "entries"}, label)
    if observation["contract"] != "task-witness-runtime-closure-observation-v1":
        raise ValueError(f"{label} contract drift")
    observed_roots = observation["roots"]
    expected_roots = evidence["closure"]["roots"]
    if not isinstance(observed_roots, list) or len(observed_roots) != len(
        expected_roots
    ):
        raise ValueError(f"{label} root inventory drift")
    for index, (observed, expected) in enumerate(
        zip(observed_roots, expected_roots, strict=True)
    ):
        binding = _parse_directory_binding(observed, f"{label} root {index}")
        if binding["path"] != expected["path"]:
            raise ValueError(f"{label} root binding drift")
    observed_entries = observation["entries"]
    expected_entries = evidence["closure"]["entries"]
    if not isinstance(observed_entries, list) or len(observed_entries) != len(
        expected_entries
    ):
        raise ValueError(f"{label} entry inventory drift")
    for index, (observed, expected) in enumerate(
        zip(observed_entries, expected_entries, strict=True)
    ):
        if not isinstance(observed, dict) or observed.get("kind") != expected["kind"]:
            raise ValueError(f"{label} entry {index} kind drift")
        kind = expected["kind"]
        if kind == "regular-file":
            item = _exact_object(
                observed,
                {"kind", "path", "stat", "length", "sha256"},
                f"{label} entry {index}",
            )
            binding = _parse_regular_binding(
                {key: item[key] for key in ("path", "stat", "length", "sha256")},
                f"{label} entry {index}",
            )
            if (
                binding["path"] != expected["path"]
                or binding["length"] != expected["length"]
                or binding["sha256"] != expected["sha256"]
            ):
                raise ValueError(f"{label} entry {index} identity drift")
        elif kind == "directory":
            item = _exact_object(
                observed,
                {"kind", "path", "stat"},
                f"{label} entry {index}",
            )
            binding = _parse_directory_binding(
                {"path": item["path"], "stat": item["stat"]},
                f"{label} entry {index}",
            )
            if binding["path"] != expected["path"]:
                raise ValueError(f"{label} entry {index} identity drift")
        else:
            item = _exact_object(
                observed,
                {"kind", "path", "stat", "target"},
                f"{label} entry {index}",
            )
            _absolute_path(item["path"], f"{label} entry {index} path")
            metadata = _parse_live_stat(item["stat"], f"{label} entry {index} stat")
            if (
                not stat.S_ISLNK(metadata["mode"])
                or item["path"] != expected["path"]
                or item["target"] != expected["target"]
            ):
                raise ValueError(f"{label} entry {index} identity drift")
        metadata = item["stat"]
        if (
            metadata["uid"] != expected["uid"]
            or metadata["gid"] != expected["gid"]
            or stat.S_IMODE(metadata["mode"])
            != expected.get("mode", stat.S_IMODE(metadata["mode"]))
        ):
            raise ValueError(f"{label} entry {index} metadata drift")
    _bounded_projection(observation, RUNTIME_CLOSURE_OBSERVATION_MAX_BYTES, label)
    return observation


def _parse_tool_observations(value: object, profile: dict, label: str) -> list[dict]:
    if not isinstance(value, list) or len(value) != len(SYSTEM_TOOL_IDS):
        raise ValueError(f"{label} inventory drift")
    for index, (item, expected) in enumerate(
        zip(value, profile["system_tools"], strict=True)
    ):
        tool = _exact_object(
            item,
            {"id", "invoked_path", "resolved_path", "resolution", "file"},
            f"{label} item {index}",
        )
        if any(
            tool[field] != expected[field]
            for field in ("id", "invoked_path", "resolved_path")
        ):
            raise ValueError(f"{label} item {index} profile binding drift")
        resolution = tool["resolution"]
        if not isinstance(resolution, list) or not resolution:
            raise ValueError(f"{label} item {index} resolution drift")
        paths: list[str] = []
        for hop_index, hop_value in enumerate(resolution):
            hop = _exact_object(
                hop_value,
                {"path", "stat"},
                f"{label} item {index} resolution {hop_index}",
            )
            paths.append(_absolute_path(hop["path"], f"{label} resolution path"))
            _parse_live_stat(hop["stat"], f"{label} resolution stat")
        if paths != sorted(set(paths)):
            raise ValueError(f"{label} item {index} resolution order drift")
        binding = _parse_regular_binding(tool["file"], f"{label} item {index} file")
        if (
            binding["path"] != expected["resolved_path"]
            or binding["length"] != expected["length"]
            or binding["sha256"] != expected["sha256"]
            or binding["stat"]["uid"] != expected["uid"]
            or binding["stat"]["gid"] != expected["gid"]
            or stat.S_IMODE(binding["stat"]["mode"]) != expected["mode"]
        ):
            raise ValueError(f"{label} item {index} file binding drift")
    _bounded_projection(value, SYSTEM_TOOL_OBSERVATION_MAX_BYTES, label)
    return value


def _parse_candidate_closure(value: object, label: str) -> dict:
    closure = _exact_object(
        value,
        {"contract", "entry_count", "projection_sha256", "source_shape_sha256"},
        label,
    )
    if closure["contract"] != "task-witness-qualification-candidate-closure-v1":
        raise ValueError(f"{label} contract drift")
    _uint64(closure["entry_count"], f"{label} entry count", positive=True)
    for field in ("projection_sha256", "source_shape_sha256"):
        require_sha256_hex(closure[field], f"{label} {field}")
    return closure


def _parse_suite_inventory_summary(value: object, label: str) -> dict:
    summary = _exact_object(
        value,
        {
            "path",
            "length",
            "sha256",
            "counts_sha256",
            "entries_sha256",
            "entry_count",
            "expected_count_total",
        },
        label,
    )
    if summary["path"] != SUITE_INVENTORY_RELATIVE.as_posix():
        raise ValueError(f"{label} path drift")
    length = _uint64(summary["length"], f"{label} length", positive=True)
    if length > MAX_JSON_BYTES:
        raise ValueError(f"{label} length is too large")
    for field in ("sha256", "counts_sha256", "entries_sha256"):
        require_sha256_hex(summary[field], f"{label} {field}")
    if _uint64(summary["entry_count"], f"{label} entry count") != len(
        SUITE_PROJECTIONS
    ):
        raise ValueError(f"{label} entry count drift")
    count_projection = [
        {"expected_count": SUITE_EXPECTED_COUNTS[suite_id], "id": suite_id}
        for suite_id, _phase, _targets in SUITE_PROJECTIONS
    ]
    expected_count_total = sum(SUITE_EXPECTED_COUNTS.values())
    if (
        summary["counts_sha256"]
        != hashlib.sha256(
            _canonical_bytes(count_projection, f"{label} count projection")
        ).hexdigest()
        or _uint64(
            summary["expected_count_total"],
            f"{label} expected count total",
            positive=True,
        )
        != expected_count_total
    ):
        raise ValueError(f"{label} count projection drift")
    return summary


def _parse_main_executable_observation(
    value: object,
    evidence: dict,
    label: str,
) -> dict:
    observation = _exact_object(
        value,
        {"path", "length", "sha256", "uid", "gid", "mode", "nlink"},
        label,
    )
    executable = evidence["main_executable"]
    if any(
        observation[field] != executable[field]
        for field in ("path", "length", "sha256", "uid", "gid", "mode")
    ):
        raise ValueError(f"{label} runtime binding drift")
    _uint64(observation["nlink"], f"{label} link count", positive=True)
    return observation


def _parse_rendered_shim(
    value: object,
    profile: dict,
    evidence: dict,
    label: str,
) -> dict:
    rendered = _exact_object(
        value,
        {"contract", "template", "runtime_executable_path", "client", "shim"},
        label,
    )
    if (
        rendered["contract"] != "task-witness-rendered-shim-observation-v1"
        or rendered["runtime_executable_path"] != evidence["main_executable"]["path"]
    ):
        raise ValueError(f"{label} contract drift")
    template = _exact_object(
        rendered["template"],
        {"path", "length", "sha256"},
        f"{label} template",
    )
    if template["path"] != "plugins/task-witness/client/task_witness_shim.sh.in":
        raise ValueError(f"{label} template path drift")
    _uint64(template["length"], f"{label} template length", positive=True)
    require_sha256_hex(template["sha256"], f"{label} template")
    home = profile["passwd_user"]["home"]
    expected_paths = {
        "client": f"{home}/.local/libexec/task-witness/client/task_witness_client.py",
        "shim": f"{home}/.local/libexec/task-witness/task-witness",
    }
    for name in ("client", "shim"):
        item = _exact_object(
            rendered[name],
            {"path", "length", "sha256", "uid", "gid", "mode", "nlink"},
            f"{label} {name}",
        )
        _absolute_path(item["path"], f"{label} {name} path")
        _uint64(item["length"], f"{label} {name} length", positive=True)
        require_sha256_hex(item["sha256"], f"{label} {name}")
        for field in ("uid", "gid", "mode", "nlink"):
            _uint64(item[field], f"{label} {name} {field}")
        if (
            item["path"] != expected_paths[name]
            or item["uid"] != profile["passwd_user"]["uid"]
            or item["gid"] != profile["passwd_user"]["primary_gid"]
            or item["mode"] != 0o500
            or item["nlink"] != 1
        ):
            raise ValueError(f"{label} {name} binding drift")
    _bounded_projection(rendered, HOST_RECEIPT_MEMBER_CAPS["rendered_shim"], label)
    return rendered


def _parse_suite_result_document(value: object, label: str) -> dict:
    result = _exact_object(
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
        label,
    )
    if (
        type(result["schema_version"]) is not int
        or result["schema_version"] != 1
        or result["contract"] != "task-witness-tw4-suite-result-v1"
        or result["terminal"] != "passed"
        or result["id"] not in {item[0] for item in SUITE_PROJECTIONS}
    ):
        raise ValueError(f"{label} contract drift")
    _uint64(result["observed_count"], f"{label} observed count", positive=True)
    for stream in ("stdout", "stderr"):
        length = _uint64(
            result[f"detail_{stream}_length"], f"{label} detail {stream} length"
        )
        if length > DETAIL_STREAM_MAX_BYTES:
            raise ValueError(f"{label} detail stream is too large")
        require_sha256_hex(
            result[f"detail_{stream}_sha256"], f"{label} detail {stream}"
        )
    return result


def _parse_suite_results(
    value: object,
    target: str,
    rendered_shim: dict,
    suite_inventory: dict,
    label: str,
) -> list[dict]:
    expected_ids = [
        suite_id for suite_id, _phase, targets in SUITE_PROJECTIONS if target in targets
    ]
    target_expected_total = sum(
        SUITE_EXPECTED_COUNTS[suite_id] for suite_id in expected_ids
    )
    if not isinstance(value, list) or len(value) != len(expected_ids):
        raise ValueError(f"{label} inventory drift")
    expected_total = 0
    empty_digest = hashlib.sha256(b"").hexdigest()
    rendered_raw = _canonical_bytes(rendered_shim, "Task Witness rendered shim")
    for index, (item_value, expected_id) in enumerate(
        zip(value, expected_ids, strict=True)
    ):
        item = _exact_object(
            item_value,
            {"id", "expected_count", "expected_terminal", "process", "result"},
            f"{label} item {index}",
        )
        expected_count = _uint64(
            item["expected_count"],
            f"{label} item {index} expected count",
            positive=True,
        )
        expected_total += expected_count
        if (
            item["id"] != expected_id
            or expected_count != SUITE_EXPECTED_COUNTS[expected_id]
            or item["expected_terminal"] != "passed"
        ):
            raise ValueError(f"{label} item {index} order drift")
        result = _parse_suite_result_document(
            item["result"], f"{label} item {index} result"
        )
        if (
            result["id"] != expected_id
            or result["observed_count"] != expected_count
            or result["terminal"] != item["expected_terminal"]
        ):
            raise ValueError(f"{label} item {index} result binding drift")
        process = _exact_object(
            item["process"],
            {
                "exit_status",
                "stdout_length",
                "stdout_sha256",
                "stderr_length",
                "stderr_sha256",
            },
            f"{label} item {index} process",
        )
        for field in ("exit_status", "stdout_length", "stderr_length"):
            _uint64(process[field], f"{label} item {index} process {field}")
        for field in ("stdout_sha256", "stderr_sha256"):
            require_sha256_hex(process[field], f"{label} item {index} process {field}")
        result_raw = _canonical_bytes(result, f"{label} item {index} result")
        if (
            process["exit_status"] != 0
            or process["stdout_length"] != len(result_raw)
            or process["stdout_sha256"] != hashlib.sha256(result_raw).hexdigest()
            or process["stderr_length"] != 0
            or process["stderr_sha256"] != empty_digest
        ):
            raise ValueError(f"{label} item {index} process binding drift")
        if expected_id == "literal-rendered-shim" and (
            result["detail_stdout_length"] != len(rendered_raw)
            or result["detail_stdout_sha256"]
            != hashlib.sha256(rendered_raw).hexdigest()
        ):
            raise ValueError(f"{label} rendered-shim result binding drift")
    if expected_total != target_expected_total:
        raise ValueError(f"{label} aggregate count drift")
    _bounded_projection(value, HOST_RECEIPT_MEMBER_CAPS["suite_results"], label)
    return value


def _parse_host_observations(
    value: object,
    *,
    qualification_candidate: dict,
    candidate_closure: dict,
    bridge_history: dict,
    suite_inventory: dict,
    profile: dict,
    credential_state: dict,
    evidence: dict,
    main_executable_observation: dict,
    label: str,
) -> dict:
    observations = _exact_object(
        value,
        {"contract", "inputs", "before", "after"},
        label,
    )
    if observations["contract"] != "task-witness-tw4-host-input-stability-v1":
        raise ValueError(f"{label} contract drift")
    inputs = _exact_object(
        observations["inputs"],
        {"candidate", "bridge_history", "suite_inventory", "platform", "runtime"},
        f"{label} inputs",
    )
    for name, maximum in HOST_OBSERVATION_INPUT_CAPS.items():
        _bounded_projection(inputs[name], maximum, f"{label} input {name}")

    candidate = _exact_object(
        inputs["candidate"],
        {
            "contract",
            "root_path",
            "root",
            "qualification_candidate",
            "candidate_closure",
            "worktree",
        },
        f"{label} candidate",
    )
    if candidate["contract"] != "task-witness-tw4-candidate-observation-v1":
        raise ValueError(f"{label} candidate contract drift")
    root_path = _absolute_path(candidate["root_path"], f"{label} candidate root path")
    root_binding = _parse_directory_binding(
        candidate["root"], f"{label} candidate root"
    )
    if (
        root_binding["path"] != root_path
        or candidate["qualification_candidate"] != qualification_candidate
        or candidate["candidate_closure"] != candidate_closure
        or candidate["worktree"] != {"tracked": "clean", "untracked": "none"}
    ):
        raise ValueError(f"{label} candidate binding drift")

    history_observation = _exact_object(
        inputs["bridge_history"],
        {"contract", "bridge_history", "identity_file", "provenance_file"},
        f"{label} bridge history",
    )
    if (
        history_observation["contract"]
        != "task-witness-tw4-bridge-history-observation-v1"
        or history_observation["bridge_history"] != bridge_history
    ):
        raise ValueError(f"{label} bridge-history binding drift")
    identity_binding = _parse_regular_binding(
        history_observation["identity_file"], f"{label} bridge identity file"
    )
    provenance_binding = _parse_regular_binding(
        history_observation["provenance_file"], f"{label} bridge provenance file"
    )
    expected_identity_path = (Path(root_path) / BRIDGE_IDENTITY_RELATIVE).as_posix()
    expected_provenance_path = (Path(root_path) / BRIDGE_PROVENANCE_RELATIVE).as_posix()
    if (
        identity_binding["path"] != expected_identity_path
        or identity_binding["sha256"] != bridge_history["bridge_identity_sha256"]
        or provenance_binding["path"] != expected_provenance_path
        or provenance_binding["sha256"] != bridge_history["bridge_provenance_sha256"]
    ):
        raise ValueError(f"{label} bridge file binding drift")

    inventory_observation = _exact_object(
        inputs["suite_inventory"],
        {"contract", "file", "suite_inventory"},
        f"{label} suite inventory",
    )
    inventory_binding = _parse_regular_binding(
        inventory_observation["file"], f"{label} suite inventory file"
    )
    expected_inventory_path = (Path(root_path) / SUITE_INVENTORY_RELATIVE).as_posix()
    if (
        inventory_observation["contract"]
        != "task-witness-tw4-suite-inventory-observation-v1"
        or inventory_observation["suite_inventory"] != suite_inventory
        or inventory_binding["path"] != expected_inventory_path
        or inventory_binding["length"] != suite_inventory["length"]
        or inventory_binding["sha256"] != suite_inventory["sha256"]
    ):
        raise ValueError(f"{label} suite-inventory binding drift")

    platform_observation = _exact_object(
        inputs["platform"],
        {"contract", "profile_file", "home", "credential_state", "system_tools"},
        f"{label} platform",
    )
    profile_binding = _parse_regular_binding(
        platform_observation["profile_file"], f"{label} platform profile file"
    )
    profile_raw = _canonical_bytes(profile, "Task Witness platform profile")
    home_binding = _parse_directory_binding(
        platform_observation["home"], f"{label} platform home"
    )
    tool_observations = _parse_tool_observations(
        platform_observation["system_tools"], profile, f"{label} system tools"
    )
    if (
        platform_observation["contract"] != "task-witness-tw4-platform-observation-v1"
        or profile_binding["length"] != len(profile_raw)
        or profile_binding["sha256"] != hashlib.sha256(profile_raw).hexdigest()
        or home_binding["path"] != profile["passwd_user"]["home"]
        or platform_observation["credential_state"] != credential_state
    ):
        raise ValueError(f"{label} platform binding drift")

    runtime_observation = _exact_object(
        inputs["runtime"],
        {
            "contract",
            "evidence_file",
            "main_executable_observation",
            "closure_observation",
        },
        f"{label} runtime",
    )
    evidence_binding = _parse_regular_binding(
        runtime_observation["evidence_file"], f"{label} runtime evidence file"
    )
    evidence_raw = _canonical_bytes(evidence, "Task Witness runtime evidence")
    closure_observation = _parse_runtime_closure_observation(
        runtime_observation["closure_observation"],
        evidence,
        f"{label} runtime closure",
    )
    if (
        runtime_observation["contract"] != "task-witness-tw4-runtime-observation-v1"
        or evidence_binding["length"] != len(evidence_raw)
        or evidence_binding["sha256"] != hashlib.sha256(evidence_raw).hexdigest()
        or runtime_observation["main_executable_observation"]
        != main_executable_observation
    ):
        raise ValueError(f"{label} runtime binding drift")

    digest_fields = {
        "candidate": "candidate_sha256",
        "bridge_history": "bridge_history_sha256",
        "suite_inventory": "suite_inventory_sha256",
        "platform": "platform_sha256",
        "runtime": "runtime_sha256",
    }
    before = _exact_object(
        observations["before"], set(digest_fields.values()), f"{label} before"
    )
    after = _exact_object(
        observations["after"], set(digest_fields.values()), f"{label} after"
    )
    if before != after:
        raise ValueError(f"{label} before/after drift")
    for input_name, digest_field in digest_fields.items():
        expected_digest = hashlib.sha256(
            _canonical_bytes(inputs[input_name], f"{label} input {input_name}")
        ).hexdigest()
        if (
            require_sha256_hex(before[digest_field], f"{label} {digest_field}")
            != expected_digest
        ):
            raise ValueError(f"{label} {input_name} digest drift")
    _bounded_projection(observations, HOST_RECEIPT_MEMBER_CAPS["observations"], label)
    return {
        "value": observations,
        "tool_observations": tool_observations,
        "closure_observation": closure_observation,
    }


def parse_host_qualification_receipt(value: object) -> dict:
    """Validate one complete internally self-bound TW4 native-host receipt."""

    label = "Task Witness host qualification receipt"
    receipt = _exact_object(
        value,
        {
            "schema_version",
            "contract",
            "qualification_candidate",
            "candidate_closure",
            "bridge_history",
            "suite_inventory",
            "target",
            "platform",
            "runtime",
            "rendered_shim",
            "observations",
            "suite_results",
            "disposition",
            "content_sha256",
        },
        label,
    )
    _bounded_projection(receipt, HOST_RECEIPT_MAX_BYTES, label)
    for name, maximum in HOST_RECEIPT_MEMBER_CAPS.items():
        _bounded_projection(receipt[name], maximum, f"{label} {name}")
    if (
        type(receipt["schema_version"]) is not int
        or receipt["schema_version"] != 1
        or receipt["contract"] != "task-witness-tw4-host-qualification-receipt-v1"
        or receipt["target"] not in {"macos-arm64", "linux-x86_64"}
        or receipt["disposition"] != "qualified"
    ):
        raise ValueError(f"{label} contract drift")
    candidate = _parse_qualification_candidate(
        receipt["qualification_candidate"], f"{label} qualification candidate"
    )
    candidate_closure = _parse_candidate_closure(
        receipt["candidate_closure"], f"{label} candidate closure"
    )
    bridge_history = _parse_bridge_history_projection(
        receipt["bridge_history"], f"{label} bridge history"
    )
    suite_inventory = _parse_suite_inventory_summary(
        receipt["suite_inventory"], f"{label} suite inventory"
    )
    if candidate["suite_inventory_sha256"] != suite_inventory["sha256"]:
        raise ValueError(f"{label} candidate suite-inventory binding drift")

    platform = _exact_object(
        receipt["platform"],
        {
            "profile_sha256",
            "profile",
            "credential_state",
            "system_tool_observation_sha256",
        },
        f"{label} platform",
    )
    profile = _parse_platform_profile(platform["profile"])
    profile_raw = _canonical_bytes(profile, f"{label} platform profile")
    if (
        profile["target"] != receipt["target"]
        or require_sha256_hex(platform["profile_sha256"], f"{label} profile digest")
        != hashlib.sha256(profile_raw).hexdigest()
    ):
        raise ValueError(f"{label} platform-profile binding drift")
    credential = _parse_credential_state(
        platform["credential_state"],
        receipt["target"],
        profile,
        f"{label} credential state",
    )
    require_sha256_hex(
        platform["system_tool_observation_sha256"],
        f"{label} system-tool observation",
    )

    runtime = _exact_object(
        receipt["runtime"],
        {
            "evidence_sha256",
            "evidence",
            "main_executable_observation",
            "closure_observation_sha256",
        },
        f"{label} runtime",
    )
    evidence = _parse_runtime_evidence(runtime["evidence"])
    evidence_raw = _canonical_bytes(evidence, f"{label} runtime evidence")
    if (
        require_sha256_hex(
            runtime["evidence_sha256"], f"{label} runtime evidence digest"
        )
        != hashlib.sha256(evidence_raw).hexdigest()
    ):
        raise ValueError(f"{label} runtime-evidence binding drift")
    main_observation = _parse_main_executable_observation(
        runtime["main_executable_observation"], evidence, f"{label} runtime executable"
    )
    require_sha256_hex(
        runtime["closure_observation_sha256"], f"{label} closure observation"
    )
    rendered_shim = _parse_rendered_shim(
        receipt["rendered_shim"], profile, evidence, f"{label} rendered shim"
    )
    observations = _parse_host_observations(
        receipt["observations"],
        qualification_candidate=candidate,
        candidate_closure=candidate_closure,
        bridge_history=bridge_history,
        suite_inventory=suite_inventory,
        profile=profile,
        credential_state=credential,
        evidence=evidence,
        main_executable_observation=main_observation,
        label=f"{label} observations",
    )
    if (
        platform["system_tool_observation_sha256"]
        != hashlib.sha256(
            _canonical_bytes(observations["tool_observations"], f"{label} system tools")
        ).hexdigest()
        or runtime["closure_observation_sha256"]
        != hashlib.sha256(
            _canonical_bytes(
                observations["closure_observation"], f"{label} closure observation"
            )
        ).hexdigest()
    ):
        raise ValueError(f"{label} retained observation digest drift")
    _parse_suite_results(
        receipt["suite_results"],
        receipt["target"],
        rendered_shim,
        suite_inventory,
        f"{label} suite results",
    )
    _validate_content_document(receipt, label)
    return receipt


def validate_qualification_candidate_binding(
    candidate_root: Path,
    receipt: dict,
    candidate_evidence: dict[str, object],
) -> None:
    """Bind one parsed host receipt to one independently observed candidate."""

    expected_fields = {
        "qualification_candidate",
        "candidate_closure",
        "suite_inventory",
        "bridge_history",
        "candidate_observation",
        "bridge_identity_file",
        "bridge_provenance_file",
        "suite_inventory_file",
        "template",
        "client",
    }
    require_exact_fields(
        candidate_evidence,
        expected_fields,
        "Task Witness qualification candidate evidence",
    )
    comparisons = (
        ("qualification_candidate", "qualification candidate"),
        ("candidate_closure", "candidate closure"),
        ("suite_inventory", "suite inventory"),
        ("bridge_history", "bridge history"),
    )
    for field, label in comparisons:
        if candidate_evidence[field] != receipt[field]:
            raise ValueError(f"Task Witness qualification {label} drift")

    inputs = receipt["observations"]["inputs"]
    candidate_observation = inputs["candidate"]
    if (
        candidate_observation["root_path"] != str(candidate_root)
        or candidate_observation["root"]["path"] != str(candidate_root)
        or candidate_evidence["candidate_observation"] != candidate_observation
    ):
        raise ValueError("Task Witness qualification candidate observation drift")
    observation_bindings = (
        (
            "bridge_identity_file",
            inputs["bridge_history"]["identity_file"],
            "bridge identity",
        ),
        (
            "bridge_provenance_file",
            inputs["bridge_history"]["provenance_file"],
            "bridge provenance",
        ),
        (
            "suite_inventory_file",
            inputs["suite_inventory"]["file"],
            "suite inventory",
        ),
    )
    for field, expected, label in observation_bindings:
        if candidate_evidence[field] != expected:
            raise ValueError(f"Task Witness qualification {label} file drift")
    if candidate_evidence["template"] != receipt["rendered_shim"]["template"]:
        raise ValueError("Task Witness qualification shim template drift")
    client = _exact_object(
        candidate_evidence["client"],
        {"length", "sha256"},
        "Task Witness qualification client evidence",
    )
    if any(
        client[field] != receipt["rendered_shim"]["client"][field]
        for field in ("length", "sha256")
    ):
        raise ValueError("Task Witness qualification client drift")


def validate_final_receipt_candidate_binding(
    receipt: dict,
    candidate_evidence: dict[str, object],
) -> None:
    """Bind a portable parsed receipt to the coordinator's candidate evidence."""

    expected_fields = {
        "qualification_candidate",
        "candidate_closure",
        "suite_inventory",
        "bridge_history",
        "candidate_observation",
        "bridge_identity_file",
        "bridge_provenance_file",
        "suite_inventory_file",
        "template",
        "client",
    }
    require_exact_fields(
        candidate_evidence,
        expected_fields,
        "Task Witness final-release candidate evidence",
    )
    for field, label in (
        ("qualification_candidate", "candidate"),
        ("candidate_closure", "candidate closure"),
        ("suite_inventory", "suite inventory"),
        ("bridge_history", "bridge history"),
    ):
        if receipt[field] != candidate_evidence[field]:
            raise ValueError(f"Task Witness final-release {label} drift")
    inputs = receipt["observations"]["inputs"]
    for field, observed, label in (
        (
            "bridge_identity_file",
            inputs["bridge_history"]["identity_file"],
            "bridge identity",
        ),
        (
            "bridge_provenance_file",
            inputs["bridge_history"]["provenance_file"],
            "bridge provenance",
        ),
        (
            "suite_inventory_file",
            inputs["suite_inventory"]["file"],
            "suite inventory",
        ),
    ):
        expected = candidate_evidence[field]
        if any(
            observed[key] != expected[key]
            for key in ("length", "sha256")
        ):
            raise ValueError(f"Task Witness final-release {label} file drift")
    if receipt["rendered_shim"]["template"] != candidate_evidence["template"]:
        raise ValueError("Task Witness final-release shim template drift")
    if any(
        receipt["rendered_shim"]["client"][key]
        != candidate_evidence["client"][key]
        for key in ("length", "sha256")
    ):
        raise ValueError("Task Witness final-release client drift")


def _json_object_from_bytes(raw: bytes, label: str) -> dict:
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError(f"{label} is too large")

    def unique_object(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} contains a duplicate key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"{label} contains a non-finite number: {value}")

    def reject_nonfinite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"{label} contains a non-finite number: {value}")
        return parsed

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
            parse_float=reject_nonfinite_float,
        )
    except (RecursionError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is invalid JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain an object")
    return value


def _validate_marketplace_promotion(candidate: dict, final: dict) -> None:
    if set(candidate) != set(final):
        raise ValueError("Task Witness marketplace root drift")
    if any(
        candidate[field] != final[field]
        for field in candidate
        if field != "plugins"
    ):
        raise ValueError("Task Witness marketplace metadata drift")
    candidate_plugins = candidate.get("plugins")
    final_plugins = final.get("plugins")
    if not isinstance(candidate_plugins, list) or not isinstance(final_plugins, list):
        raise ValueError("Task Witness marketplace plugin inventory drift")
    names: set[str] = set()
    for entry in candidate_plugins:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"name", "source", "category"}
            or not isinstance(entry["name"], str)
            or entry["name"] in names
            or entry["category"] != "developer-tools"
            or entry["source"] != f"./plugins/{entry['name']}"
        ):
            raise ValueError("Task Witness candidate marketplace inventory drift")
        names.add(entry["name"])
    if "task-witness" in names:
        raise ValueError("Task Witness candidate marketplace eligibility drift")
    if final_plugins != [*candidate_plugins, TASK_WITNESS_MARKETPLACE_ROUTE]:
        raise ValueError("Task Witness marketplace promotion drift")


def validate_promotion_delta(
    candidate_tree: dict[str, tuple[str, str, str]],
    final_tree: dict[str, tuple[str, str, str]],
    candidate_raw: dict[str, bytes],
    final_raw: dict[str, bytes],
) -> dict[str, object]:
    """Validate and return the exact metadata-only public promotion projection."""

    if set(candidate_tree) != set(final_tree):
        raise ValueError("Task Witness promotion path drift")
    candidate_files = {
        path: entry for path, entry in candidate_tree.items() if entry[1] != "tree"
    }
    final_files = {
        path: entry for path, entry in final_tree.items() if entry[1] != "tree"
    }
    changed_files = {
        path
        for path in set(candidate_files) | set(final_files)
        if candidate_files.get(path) != final_files.get(path)
    }
    if (
        set(candidate_files) != set(final_files)
        or changed_files != set(PROMOTION_PATHS)
        or any(
            candidate_tree.get(path, ())[:2] != ("040000", "tree")
            or final_tree.get(path, ())[:2] != ("040000", "tree")
            for path in PROMOTION_ANCESTOR_PATHS
        )
    ):
        raise ValueError("Task Witness promotion path drift")
    changed_trees = {
        path
        for path, entry in candidate_tree.items()
        if entry[1] == "tree" and entry != final_tree[path]
    }
    if changed_trees != set(PROMOTION_ANCESTOR_PATHS):
        raise ValueError("Task Witness promotion path drift")
    if set(candidate_raw) != set(PROMOTION_PATHS) or set(final_raw) != set(
        PROMOTION_PATHS
    ):
        raise ValueError("Task Witness promotion byte inventory drift")

    entries: list[dict[str, str]] = []
    for path in PROMOTION_PATHS:
        before_mode, before_kind, before_oid = candidate_files[path]
        after_mode, after_kind, after_oid = final_files[path]
        before_raw = candidate_raw[path]
        after_raw = final_raw[path]
        if (
            (before_mode, before_kind, after_mode, after_kind)
            != ("100644", "blob", "100644", "blob")
            or _git_object_sha1("blob", before_raw) != before_oid
            or _git_object_sha1("blob", after_raw) != after_oid
            or before_raw == after_raw
        ):
            raise ValueError("Task Witness promotion Git blob drift")
        entries.append(
            {
                "after_mode": after_mode,
                "after_sha256": hashlib.sha256(after_raw).hexdigest(),
                "before_mode": before_mode,
                "before_sha256": hashlib.sha256(before_raw).hexdigest(),
                "path": path,
            }
        )

    candidate_registration = _json_object_from_bytes(
        candidate_raw[PUBLIC_RELEASE_REGISTRATION_RELATIVE.as_posix()],
        "Task Witness candidate public-release registration",
    )
    final_registration = _json_object_from_bytes(
        final_raw[PUBLIC_RELEASE_REGISTRATION_RELATIVE.as_posix()],
        "Task Witness final public-release registration",
    )
    if candidate_registration != EXPECTED_PUBLIC_RELEASE_REGISTRATION:
        raise ValueError("Task Witness candidate public-release registration drift")
    if final_registration != {
        **EXPECTED_PUBLIC_RELEASE_REGISTRATION,
        "production_eligible": True,
    }:
        raise ValueError("Task Witness final public-release registration drift")

    _validate_marketplace_promotion(
        _json_object_from_bytes(
            candidate_raw[MARKETPLACE_RELATIVE.as_posix()],
            "Task Witness candidate marketplace",
        ),
        _json_object_from_bytes(
            final_raw[MARKETPLACE_RELATIVE.as_posix()],
            "Task Witness final marketplace",
        ),
    )
    return {
        "contract": "task-witness-tw4-promotion-delta-v1",
        "entries": entries,
    }


def _live_stat_projection(metadata: os.stat_result) -> dict[str, int]:
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


def _capture_live_regular_binding(path: Path, label: str) -> dict[str, object]:
    if not path.is_absolute() or path == Path(path.anchor):
        raise ValueError(f"{label} path is invalid")
    current = Path(path.anchor)
    for component in path.parts[1:-1]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as error:
            raise ValueError(f"{label} is unavailable") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(f"{label} path has an unsafe ancestor")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink < 1
            or before.st_size > MAX_OBSERVED_TOOL_BYTES
        ):
            raise ValueError(f"{label} is not a supported regular file")
        remaining = before.st_size
        digest = hashlib.sha256()
        observed = 0
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise ValueError(f"{label} is incomplete")
            digest.update(chunk)
            observed += len(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        visible = path.lstat()
        if (
            _live_stat_projection(before) != _live_stat_projection(after)
            or _live_stat_projection(after) != _live_stat_projection(visible)
            or observed != before.st_size
        ):
            raise ValueError(f"{label} identity drift")
        return {
            "path": str(path),
            "stat": _live_stat_projection(after),
            "length": observed,
            "sha256": digest.hexdigest(),
        }
    except OSError as error:
        raise ValueError(f"{label} is unavailable") from error
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as error:
                if sys.exception() is None:
                    raise ValueError(f"{label} cannot be closed") from error


def _recorded_git_executable(receipt: dict) -> Path:
    profile_tools = {
        item["id"]: item for item in receipt["platform"]["profile"]["system_tools"]
    }
    observed_tools = {
        item["id"]: item
        for item in receipt["observations"]["inputs"]["platform"]["system_tools"]
    }
    if set(profile_tools) != set(SYSTEM_TOOL_IDS) or set(observed_tools) != set(
        SYSTEM_TOOL_IDS
    ):
        raise ValueError("Task Witness qualification system-tool inventory drift")
    profile = profile_tools["git"]
    observed = observed_tools["git"]
    path = Path(profile["resolved_path"])
    if (
        observed["resolved_path"] != str(path)
        or observed["file"] != _capture_live_regular_binding(path, "recorded Git executable")
    ):
        raise ValueError("Task Witness qualification recorded Git identity drift")
    if not os.access(path, os.X_OK):
        raise ValueError("Task Witness qualification recorded Git is not executable")
    _require_path_outside_user_write_authority(
        path,
        "Task Witness qualification recorded Git",
    )
    return path


def _require_path_outside_user_write_authority(path: Path, label: str) -> None:
    if not path.is_absolute() or path == Path(path.anchor):
        raise ValueError(f"{label} path is invalid")
    current = Path(path.anchor)
    euid = os.geteuid()
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as error:
            raise ValueError(f"{label} is unavailable") from error
        try:
            writable = os.access(current, os.W_OK, effective_ids=True)
        except (NotImplementedError, TypeError) as error:
            raise ValueError(f"{label} write-authority check is unavailable") from error
        if (
            stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid == euid
            or writable
        ):
            raise ValueError(f"{label} is mutable by the validation user")


def _local_git_executable() -> Path:
    path = Path("/usr/bin/git")
    binding = _capture_live_regular_binding(path, "local system Git executable")
    if not os.access(path, os.X_OK) or binding["path"] != str(path):
        raise ValueError("Task Witness local system Git is not executable")
    _require_path_outside_user_write_authority(path, "Task Witness local system Git")
    return path


def _qualification_candidate_evidence(
    candidate_root: Path,
    receipt: dict,
    git_executable: Path | None = None,
) -> dict[str, object]:
    """Independently reconstruct the frozen candidate projection from its checkout."""

    if git_executable is None:
        git_executable = _recorded_git_executable(receipt)
    else:
        _require_path_outside_user_write_authority(
            git_executable,
            "Task Witness candidate observer Git",
        )
    runner = _load_qualification_runner()
    try:
        administration = runner["_candidate_administration"](
            candidate_root,
            git_executable,
        )
        first, first_raw, first_bindings = runner["_candidate_core"](
            candidate_root,
            git_executable,
        )
        bridge_history = runner["validate_bridge_history_evidence"](
            first_raw,
            validate_bridge_history(candidate_root),
        )
        runner["_require_candidate_administration_stable"](administration)
        runner["_require_candidate_object_bindings_stable"](first)
        runner["_require_candidate_visible_bindings_stable"](first)
        second, second_raw, second_bindings = runner["_candidate_core"](
            candidate_root,
            git_executable,
        )
        runner["_require_candidate_administration_stable"](administration)
        runner["_require_candidate_object_bindings_stable"](second)
        runner["_require_candidate_visible_bindings_stable"](second)
    except Exception as error:
        raise ValueError(f"Task Witness qualification candidate is invalid: {error}") from error
    if (
        _canonical_json(first) != _canonical_json(second)
        or first_raw != second_raw
        or _canonical_json(first_bindings) != _canonical_json(second_bindings)
    ):
        raise ValueError("Task Witness qualification candidate changed during validation")

    candidate = first["candidate"]
    template_raw = first_raw["plugins/task-witness/client/task_witness_shim.sh.in"]
    client_raw = first_raw["plugins/task-witness/client/task_witness_client.py"]
    return {
        "qualification_candidate": candidate["qualification_candidate"],
        "candidate_closure": candidate["candidate_closure"],
        "suite_inventory": first["suite_inventory"],
        "bridge_history": bridge_history,
        "candidate_observation": candidate,
        "bridge_identity_file": first_bindings[
            "release/task-witness/tw4-bridge-identity.json"
        ],
        "bridge_provenance_file": first_bindings[
            "release/task-witness/tw4-bridge-provenance.json"
        ],
        "suite_inventory_file": first_bindings[
            "release/task-witness/tw4-suite-inventory.json"
        ],
        "template": {
            "path": "plugins/task-witness/client/task_witness_shim.sh.in",
            "length": len(template_raw),
            "sha256": hashlib.sha256(template_raw).hexdigest(),
        },
        "client": {
            "length": len(client_raw),
            "sha256": hashlib.sha256(client_raw).hexdigest(),
        },
    }


def _select_final_git_executable() -> Path:
    return _local_git_executable()


def _load_qualification_runner() -> dict[str, object]:
    distribution_root = SCRIPT_DIRECTORY.parent
    path = verified_reviewed_path(
        distribution_root,
        "scripts/run_task_witness_qualification.py",
    )
    _require_path_outside_user_write_authority(
        path,
        "Task Witness validator-owned qualification runner",
    )
    runner = runpy.run_path(str(path))
    required = {
        "_candidate_administration",
        "_require_candidate_administration_stable",
        "_require_safe_candidate_config",
        "_git_text",
        "_git_tree_entries",
        "_require_clean_candidate",
        "run_recorded_git",
        "_git_blob",
        "_bounded_process",
    }
    if any(not callable(runner.get(name)) for name in required):
        raise ValueError("Task Witness qualification runner observation seam drift")
    return runner


def _task_witness_front_door() -> Path:
    """Return the sole installed Task Witness public front door."""

    try:
        home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    except (KeyError, OSError) as error:
        raise ValueError("Task Witness passwd home is unavailable") from error
    if (
        not home.is_absolute()
        or home == Path(home.anchor)
        or str(home).startswith("//")
        or ".." in home.parts
        or str(home) != str(Path(str(home)))
    ):
        raise ValueError("Task Witness passwd home is invalid")
    return home / ".local" / "libexec" / "task-witness" / "task-witness"


def _run_canonical_task_witness(bundle_root: Path) -> dict:
    """Run the installed canonical client with bounded terminal capture."""

    runner = _load_qualification_runner()
    bounded_process = runner["_bounded_process"]
    try:
        status, stdout, stderr = bounded_process(
            [
                str(_task_witness_front_door()),
                "validate",
                "--bundle",
                str(bundle_root),
            ],
            env={},
            stdout_maximum=MAX_JSON_BYTES,
            stderr_maximum=TASK_WITNESS_REVIEW_STDERR_MAX_BYTES,
            label="Task Witness canonical review validation",
            stdin=None,
            timeout_seconds=TASK_WITNESS_REVIEW_TIMEOUT_SECONDS,
            own_process_group=True,
            cwd=Path("/"),
        )
    except Exception as error:
        raise ValueError(
            f"Task Witness canonical review validation process failed: {error}"
        ) from error
    if status != 0 or stderr:
        raise ValueError("Task Witness canonical review validation process failed")
    if not stdout.endswith(b"\n") or stdout.count(b"\n") != 1:
        raise ValueError("Task Witness canonical review launch envelope framing drift")
    try:
        envelope = json.loads(stdout[:-1].decode("utf-8"))
        canonical = _canonical_json(envelope)
    except (
        RecursionError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        raise ValueError(
            "Task Witness canonical review launch envelope is invalid"
        ) from error
    if not isinstance(envelope, dict) or stdout != canonical + b"\n":
        raise ValueError("Task Witness canonical review launch envelope is invalid")
    return envelope


def _review_identity(value: object, label: str) -> dict:
    identity = _exact_object(value, {"kind", "value", "content_sha256"}, label)
    _token(identity["kind"], f"{label} kind")
    _bounded_text(identity["value"], f"{label} value")
    require_sha256_hex(identity["content_sha256"], f"{label} content")
    return identity


def _expected_tw4_review_subject(
    qualification_candidate: dict,
) -> tuple[dict, dict, dict]:
    candidate = _parse_qualification_candidate(
        qualification_candidate,
        "Task Witness canonical review qualification candidate",
    )
    candidate_identity = {
        "kind": "task-witness-tw4-qualification-candidate-v1",
        "value": f"{candidate['repository_id']}@{candidate['commit_sha1']}",
        "content_sha256": hashlib.sha256(_canonical_json(candidate)).hexdigest(),
    }
    review_input_identity = {
        "kind": "task-witness-tw4-review-input-v1",
        "value": (
            f"{candidate['repository_id']}@{candidate['commit_sha1']}:"
            f"{candidate['tree_sha1']}"
        ),
        "content_sha256": hashlib.sha256(_canonical_json(candidate)).hexdigest(),
    }
    required_profile = {
        "contract": "task-witness-tw4-publication-review-profile-v2",
        "execution_mode": "independent",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "target": {
            "product_family": "codex",
            "surface": "chatgpt-codex",
        },
        "topology": {
            "relationship": "child",
            "ownership": "leader-owned",
            "transport": "native-tool",
        },
        "assurance": {
            "target": "product-attested",
            "model": "product-attested",
            "topology": "product-attested",
            "authority": "product-attested",
            "execution_result": "product-attested",
        },
        "required_axes": ["intent", "runtime", "structure"],
    }
    requirements_identity = {
        "kind": "task-witness-tw4-publication-review-profile-v2",
        "value": "chatgpt-codex-product-attested-sol-high-independent-all-axes",
        "content_sha256": hashlib.sha256(_canonical_json(required_profile)).hexdigest(),
    }
    return candidate_identity, review_input_identity, requirements_identity


def _validate_review_dispatch(
    value: object,
    selected_specialists: list[str],
) -> None:
    dispatch = _exact_object(
        value,
        {
            "schema_version",
            "contract",
            "evidence_contract",
            "manifest_sha256",
            "plan_sha256",
            "subject",
            "producer",
            "executions",
            "content_sha256",
        },
        "Task Witness canonical review final dispatch",
    )
    if (
        type(dispatch["schema_version"]) is not int
        or dispatch["schema_version"] != 1
        or dispatch["contract"] != "rolecasting-dispatch-projection-v2"
        or dispatch["evidence_contract"] != "rolecasting-dispatch-evidence-v2"
    ):
        raise ValueError("Task Witness canonical review dispatch contract drift")
    for field in ("manifest_sha256", "plan_sha256"):
        require_sha256_hex(dispatch[field], f"Task Witness review dispatch {field}")
    _review_identity(dispatch["subject"], "Task Witness review dispatch subject")
    producer = _exact_object(
        dispatch["producer"],
        {"producer_id", "contract", "implementation_sha256"},
        "Task Witness review dispatch producer",
    )
    _token(producer["producer_id"], "Task Witness review dispatch producer ID")
    if producer["contract"] != "rolecasting-dispatch-evidence-v2":
        raise ValueError("Task Witness canonical review dispatch producer drift")
    require_sha256_hex(
        producer["implementation_sha256"],
        "Task Witness review dispatch producer implementation",
    )
    _validate_content_document(dispatch, "Task Witness canonical review final dispatch")
    executions = dispatch["executions"]
    if not isinstance(executions, dict):
        raise ValueError("Task Witness canonical review execution inventory drift")
    expected_roles = {
        "critic-intent",
        "critic-runtime",
        "critic-structure",
        *(f"specialist-{specialist}" for specialist in selected_specialists),
    }
    roles: list[str] = []
    sessions: list[str] = []
    contexts: list[str] = []
    for execution_id, execution_value in executions.items():
        _token(execution_id, "Task Witness canonical review execution ID")
        if not isinstance(execution_value, dict):
            raise ValueError("Task Witness canonical review execution inventory drift")
        if execution_value.get("execution_id") != execution_id:
            raise ValueError("Task Witness canonical review execution identity drift")
        role = _token(
            execution_value.get("role"),
            "Task Witness canonical review execution role",
        )
        target = _exact_object(
            execution_value.get("target"),
            {"product_family", "surface", "executor", "version"},
            "Task Witness canonical review execution target",
        )
        topology = _exact_object(
            execution_value.get("topology"),
            {"relationship", "ownership", "transport"},
            "Task Witness canonical review execution topology",
        )
        assurance = _exact_object(
            execution_value.get("assurance"),
            {
                "target",
                "model",
                "topology",
                "authority",
                "execution_result",
                "evidence",
            },
            "Task Witness canonical review execution assurance",
        )
        assurance_minimum = _exact_object(
            execution_value.get("assurance_minimum"),
            {
                "target",
                "model",
                "topology",
                "authority",
                "execution_result",
            },
            "Task Witness canonical review execution assurance minimum",
        )
        isolation = _exact_object(
            execution_value.get("isolation"),
            {"session", "context", "enforceable"},
            "Task Witness canonical review execution isolation",
        )
        session = _bounded_text(
            isolation["session"], "Task Witness canonical review execution session"
        )
        context = _bounded_text(
            isolation["context"], "Task Witness canonical review execution context"
        )
        if (
            target["product_family"] != "codex"
            or target["surface"] != "chatgpt-codex"
            or target["executor"] != "codex"
            or not _bounded_text(
                target["version"], "Task Witness canonical review target version"
            )
            or topology
            != {
                "relationship": "child",
                "ownership": "leader-owned",
                "transport": "native-tool",
            }
            or any(
                assurance[field] != "product-attested"
                for field in (
                    "target",
                    "model",
                    "topology",
                    "authority",
                    "execution_result",
                )
            )
            or any(
                assurance_minimum[field] != "product-attested"
                for field in (
                    "target",
                    "model",
                    "topology",
                    "authority",
                    "execution_result",
                )
            )
            or execution_value.get("user_authority") is not None
            or execution_value.get("model") != "gpt-5.6-sol"
            or execution_value.get("reasoning_effort") != "high"
            or isolation["enforceable"] is not True
        ):
            raise ValueError("Task Witness canonical review execution profile drift")
        _review_identity(
            assurance["evidence"],
            "Task Witness canonical review execution assurance evidence",
        )
        roles.append(role)
        sessions.append(session)
        contexts.append(context)
    if (
        set(roles) != expected_roles
        or len(roles) != len(expected_roles)
        or len(sessions) != len(set(sessions))
        or len(contexts) != len(set(contexts))
    ):
        raise ValueError("Task Witness canonical review execution inventory drift")


def _validate_canonical_review_launch_envelope(
    envelope: object,
    manifest_sha256: str,
    qualification_candidate: dict,
) -> None:
    envelope = _exact_object(
        envelope,
        {"contract", "anchor", "witness"},
        "Task Witness canonical review launch envelope",
    )
    if envelope["contract"] != "task-witness-launch-envelope-v1":
        raise ValueError("Task Witness canonical review launch envelope contract drift")
    (
        candidate_identity,
        review_input_identity,
        requirements_identity,
    ) = _expected_tw4_review_subject(qualification_candidate)
    anchor = _exact_object(
        envelope["anchor"],
        {
            "contract",
            "generation",
            "active_record_sha256",
            "runtime_contract",
            "interpreter",
            "public_release",
            "runtime_implementation_sha256",
            "trust_context_sha256",
            "bundle_sha256",
            "historical",
        },
        "Task Witness canonical review complete anchor",
    )
    if (
        anchor["contract"] != "task-witness-complete-anchor-v1"
        or anchor["runtime_contract"] != "task-witness-runtime-v1"
        or anchor["historical"] is not False
    ):
        raise ValueError("Task Witness canonical review complete anchor drift")
    generation = _bounded_text(
        anchor["generation"], "Task Witness canonical review generation"
    )
    if (
        not generation.startswith("sha256-")
        or SHA256_HEX.fullmatch(generation.removeprefix("sha256-")) is None
    ):
        raise ValueError("Task Witness canonical review complete anchor drift")
    for field in (
        "active_record_sha256",
        "runtime_implementation_sha256",
        "trust_context_sha256",
        "bundle_sha256",
    ):
        require_sha256_hex(
            anchor[field], f"Task Witness canonical review anchor {field}"
        )
    interpreter = _exact_object(
        anchor["interpreter"],
        {"executable", "implementation", "version"},
        "Task Witness canonical review anchor interpreter",
    )
    _absolute_path(interpreter["executable"], "Task Witness review interpreter")
    _bounded_text(interpreter["implementation"], "Task Witness review implementation")
    version = _exact_object(
        interpreter["version"],
        {"major", "minor", "micro"},
        "Task Witness canonical review interpreter version",
    )
    for field in version:
        _uint64(version[field], f"Task Witness review interpreter {field}")
    release = _exact_object(
        anchor["public_release"],
        {"repository", "revision"},
        "Task Witness canonical review public release",
    )
    _bounded_text(release["repository"], "Task Witness review repository")
    _require_sha1(release["revision"], "Task Witness review public release revision")
    if (
        interpreter["implementation"] != "cpython"
        or version["major"] != 3
        or version["minor"] < 13
        or release["repository"] != qualification_candidate["repository_id"]
    ):
        raise ValueError("Task Witness canonical review complete anchor drift")

    witness = _exact_object(
        envelope["witness"],
        {
            "contract",
            "bundle_sha256",
            "producer",
            "validator",
            "projection",
            "trust_context_sha256",
            "historical",
        },
        "Task Witness canonical review witness",
    )
    if (
        witness["contract"] != "task-witness-canonical-projection-v2"
        or witness["bundle_sha256"] != anchor["bundle_sha256"]
        or witness["trust_context_sha256"] != anchor["trust_context_sha256"]
        or witness["historical"] is not False
    ):
        raise ValueError("Task Witness canonical review witness binding drift")
    producer = _exact_object(
        witness["producer"],
        {
            "producer_id",
            "contract",
            "implementation_sha256",
            "validator_id",
            "validator_contract",
            "validator_implementation_sha256",
        },
        "Task Witness canonical review producer",
    )
    validator = _exact_object(
        witness["validator"],
        {"validator_id", "contract", "implementation_sha256"},
        "Task Witness canonical review validator",
    )
    if (
        producer["producer_id"] != "tricritical-review-loop-v2"
        or producer["contract"] != "tricritical-terminal-review-evidence-v2"
        or producer["validator_id"]
        != "tricritical-terminal-review-evidence-validator-v2"
        or producer["validator_contract"] != "tricritical-terminal-review-evidence-v2"
        or validator["validator_id"] != producer["validator_id"]
        or validator["contract"] != producer["validator_contract"]
        or validator["implementation_sha256"]
        != producer["validator_implementation_sha256"]
    ):
        raise ValueError("Task Witness canonical review registered authority drift")
    for value, label in (
        (producer["implementation_sha256"], "producer implementation"),
        (producer["validator_implementation_sha256"], "validator implementation"),
    ):
        require_sha256_hex(value, f"Task Witness canonical review {label}")

    projection = _exact_object(
        witness["projection"],
        {
            "schema_version",
            "contract",
            "evidence_contract",
            "manifest_sha256",
            "subject",
            "review_profile",
            "final_dispatch",
            "terminal",
            "content_sha256",
        },
        "Task Witness canonical review terminal projection",
    )
    if (
        type(projection["schema_version"]) is not int
        or projection["schema_version"] != 1
        or projection["contract"] != "tricritical-terminal-review-projection-v2"
        or projection["evidence_contract"] != "tricritical-terminal-review-evidence-v2"
        or projection["manifest_sha256"] != manifest_sha256
    ):
        raise ValueError("Task Witness canonical review terminal projection drift")
    _validate_content_document(
        projection, "Task Witness canonical review terminal projection"
    )
    if (
        len(
            {
                manifest_sha256,
                anchor["bundle_sha256"],
                projection["content_sha256"],
            }
        )
        != 3
    ):
        raise ValueError("Task Witness canonical review digest domain collision")
    subject = _exact_object(
        projection["subject"],
        {"candidate", "review_input", "requirements"},
        "Task Witness canonical review subject",
    )
    if (
        _review_identity(subject["candidate"], "Task Witness review candidate")
        != candidate_identity
        or _review_identity(subject["review_input"], "Task Witness review input")
        != review_input_identity
        or _review_identity(subject["requirements"], "Task Witness review requirements")
        != requirements_identity
    ):
        raise ValueError("Task Witness canonical review subject drift")
    review_profile = _exact_object(
        projection["review_profile"],
        {"contract", "execution_mode", "required_axes", "selected_specialists"},
        "Task Witness canonical review profile",
    )
    specialists = review_profile["selected_specialists"]
    if (
        review_profile["contract"] != "tricritical-review-profile-v1"
        or review_profile["execution_mode"] != "independent"
        or review_profile["required_axes"] != ["intent", "runtime", "structure"]
        or not isinstance(specialists, list)
    ):
        raise ValueError("Task Witness canonical review profile drift")
    for specialist in specialists:
        _token(specialist, "Task Witness canonical review specialist")
    if specialists != sorted(set(specialists)):
        raise ValueError("Task Witness canonical review profile drift")
    _validate_review_dispatch(projection["final_dispatch"], specialists)
    terminal = _exact_object(
        projection["terminal"],
        {
            "state",
            "owner",
            "limitations",
            "missing_executions",
            "unresolved_actionable_findings",
            "verification",
        },
        "Task Witness canonical review terminal",
    )
    verification = _exact_object(
        terminal["verification"],
        {"status", "candidate", "evidence", "unchanged"},
        "Task Witness canonical review verification",
    )
    if (
        terminal["state"] != "clean"
        or terminal["owner"] != "none"
        or terminal["limitations"] != []
        or terminal["missing_executions"] != []
        or type(terminal["unresolved_actionable_findings"]) is not int
        or terminal["unresolved_actionable_findings"] != 0
        or verification["status"] != "passed"
        or verification["candidate"] != candidate_identity
        or verification["unchanged"] is not True
    ):
        raise ValueError("Task Witness canonical review terminal is not bare clean")
    _review_identity(
        verification["evidence"], "Task Witness canonical review verification evidence"
    )


def _checkout_projection(
    checkout_root: Path,
    git_executable: Path,
    runner: dict[str, object],
) -> dict[str, object]:
    """Observe one immutable clean SHA-1 Git checkout without changing it."""

    try:
        administration = runner["_candidate_administration"](
            checkout_root,
            git_executable,
        )
        runner["_require_safe_candidate_config"](checkout_root, git_executable)

        def capture() -> tuple[str, str, dict[str, tuple[str, str, str]], tuple[str, ...]]:
            commit = runner["_git_text"](
                git_executable,
                checkout_root,
                "rev-parse",
                "--verify",
                "HEAD^{commit}",
            )
            tree_sha1 = runner["_git_text"](
                git_executable,
                checkout_root,
                "rev-parse",
                "--verify",
                "HEAD^{tree}",
            )
            _require_sha1(commit, "Task Witness checkout commit")
            _require_sha1(tree_sha1, "Task Witness checkout tree")
            tree = runner["_git_tree_entries"](
                git_executable,
                checkout_root,
                commit,
            )
            runner["_require_clean_candidate"](
                checkout_root,
                git_executable,
                tree,
            )
            raw_parents = runner["run_recorded_git"](
                git_executable,
                checkout_root,
                "rev-list",
                "--parents",
                "-n",
                "1",
                commit,
            )
            try:
                parent_line = raw_parents.decode("ascii")
            except UnicodeDecodeError as error:
                raise ValueError("Task Witness checkout parent identity drift") from error
            if not parent_line.endswith("\n") or parent_line.count("\n") != 1:
                raise ValueError("Task Witness checkout parent identity drift")
            tokens = parent_line[:-1].split(" ")
            if not tokens or tokens[0] != commit or any(
                SHA1_HEX.fullmatch(token) is None for token in tokens
            ):
                raise ValueError("Task Witness checkout parent identity drift")
            return commit, tree_sha1, tree, tuple(tokens[1:])

        first = capture()
        runner["_require_candidate_administration_stable"](administration)
        second = capture()
        runner["_require_candidate_administration_stable"](administration)
    except Exception as error:
        raise ValueError(f"Task Witness final-release checkout is invalid: {error}") from error
    if first != second:
        raise ValueError("Task Witness final-release checkout changed during validation")
    return {
        "commit_sha1": first[0],
        "tree_sha1": first[1],
        "tree": first[2],
        "parents": first[3],
    }


def _promotion_raw_bytes(
    checkout_root: Path,
    git_executable: Path,
    runner: dict[str, object],
    tree: dict[str, tuple[str, str, str]],
) -> dict[str, bytes]:
    raw: dict[str, bytes] = {}
    try:
        for relative in PROMOTION_PATHS:
            entry = tree.get(relative)
            if entry is None or entry[1] != "blob":
                raise ValueError("Task Witness promotion path is unavailable")
            raw[relative] = runner["_git_blob"](
                git_executable,
                checkout_root,
                entry[2],
            )
    except Exception as error:
        raise ValueError(f"Task Witness promotion bytes are invalid: {error}") from error
    return raw


def _migration_suite_records(receipt: dict) -> dict[str, dict]:
    wanted = {
        "migration-freeze5-to-bridge",
        "migration-bridge-to-tw4",
    }
    records = {
        item["id"]: item for item in receipt["suite_results"] if item["id"] in wanted
    }
    if set(records) != wanted:
        raise ValueError("Task Witness final-release migration suite record drift")
    return records


def _validate_canonical_review_evidence(
    review_path: Path,
    expected_sha256: str,
    qualification_candidate: dict,
) -> None:
    if review_path.name != "manifest.json" or review_path.parent == Path(
        review_path.anchor
    ):
        raise ValueError(
            "Task Witness canonical review evidence path must name manifest.json"
        )
    _review_manifest, review_raw = _bounded_external_canonical_json_object(
        review_path,
        "Task Witness canonical review evidence manifest",
        private=True,
    )
    if hashlib.sha256(review_raw).hexdigest() != expected_sha256:
        raise ValueError(
            "Task Witness canonical review evidence raw manifest digest drift"
        )
    envelope = _run_canonical_task_witness(review_path.parent)
    _validate_canonical_review_launch_envelope(
        envelope,
        expected_sha256,
        qualification_candidate,
    )


def validate_final_release_evidence(
    final_root: Path,
    candidate_root: Path,
    manifest: dict,
    manifest_raw: bytes,
    macos_receipt: dict,
    macos_raw: bytes,
    linux_receipt: dict,
    linux_raw: bytes,
    review_path: Path,
) -> None:
    """Validate every specified final-release seam before canonical review authority."""

    if any(
        raw != _canonical_bytes(value, label)
        for value, raw, label in (
            (manifest, manifest_raw, "Task Witness TW4 release manifest"),
            (
                macos_receipt,
                macos_raw,
                "Task Witness macOS host qualification receipt",
            ),
            (
                linux_receipt,
                linux_raw,
                "Task Witness Linux host qualification receipt",
            ),
        )
    ):
        raise ValueError("Task Witness final-release canonical byte identity drift")
    parse_tw4_release_manifest(manifest)
    parse_host_qualification_receipt(macos_receipt)
    parse_host_qualification_receipt(linux_receipt)
    if (
        final_root == candidate_root
        or final_root.is_relative_to(candidate_root)
        or candidate_root.is_relative_to(final_root)
    ):
        raise ValueError("Task Witness final and candidate roots must be distinct")
    if (
        macos_receipt["target"] != "macos-arm64"
        or linux_receipt["target"] != "linux-x86_64"
        or macos_raw == linux_raw
        or hashlib.sha256(macos_raw).hexdigest()
        != manifest["targets"]["macos-arm64"]
        or hashlib.sha256(linux_raw).hexdigest()
        != manifest["targets"]["linux-x86_64"]
    ):
        raise ValueError("Task Witness final-release target receipt drift")
    if (
        macos_receipt["qualification_candidate"]
        != linux_receipt["qualification_candidate"]
        or manifest["qualification_candidate"]
        != macos_receipt["qualification_candidate"]
        or macos_receipt["bridge_history"] != linux_receipt["bridge_history"]
        or manifest["bridge_history"] != macos_receipt["bridge_history"]
    ):
        raise ValueError("Task Witness final-release manifest evidence drift")

    git_executable = _select_final_git_executable()
    candidate_evidence = _qualification_candidate_evidence(
        candidate_root,
        macos_receipt,
        git_executable,
    )
    validate_final_receipt_candidate_binding(macos_receipt, candidate_evidence)
    validate_final_receipt_candidate_binding(linux_receipt, candidate_evidence)
    derived_bridge_history = validate_candidate_source(
        candidate_root,
        include_suite_inventory=True,
    )
    if derived_bridge_history != manifest["bridge_history"]:
        raise ValueError("Task Witness final-release bridge history drift")

    runner = _load_qualification_runner()
    candidate_checkout = _checkout_projection(
        candidate_root,
        git_executable,
        runner,
    )
    final_checkout = _checkout_projection(
        final_root,
        git_executable,
        runner,
    )
    candidate_identity = candidate_evidence["qualification_candidate"]
    if any(
        candidate_checkout[f"{name}_sha1"] != candidate_identity[f"{name}_sha1"]
        for name in ("commit", "tree")
    ):
        raise ValueError("Task Witness final-release candidate Git identity drift")
    if any(
        final_checkout[f"{name}_sha1"]
        != manifest["final_public_release"][f"{name}_sha1"]
        for name in ("commit", "tree")
    ):
        raise ValueError("Task Witness final-release Git identity drift")
    if final_checkout["parents"] != (candidate_checkout["commit_sha1"],):
        raise ValueError("Task Witness final-release successor identity drift")

    promotion = validate_promotion_delta(
        candidate_checkout["tree"],
        final_checkout["tree"],
        _promotion_raw_bytes(
            candidate_root,
            git_executable,
            runner,
            candidate_checkout["tree"],
        ),
        _promotion_raw_bytes(
            final_root,
            git_executable,
            runner,
            final_checkout["tree"],
        ),
    )
    if hashlib.sha256(_canonical_json(promotion)).hexdigest() != manifest[
        "promotion_delta_sha256"
    ]:
        raise ValueError("Task Witness final-release promotion delta drift")

    macos_migration = _migration_suite_records(macos_receipt)
    linux_migration = _migration_suite_records(linux_receipt)
    for suite_id in sorted(macos_migration):
        if _canonical_json(macos_migration[suite_id]) != _canonical_json(
            linux_migration[suite_id]
        ):
            raise ValueError("Task Witness final-release migration result drift")

    _validate_canonical_review_evidence(
        review_path,
        manifest["canonical_review_evidence_sha256"],
        manifest["qualification_candidate"],
    )


def validate_suite_inventory(root: Path) -> dict:
    """Validate the candidate-owned suite inventory without executing it."""

    inventory = _bounded_canonical_json_object(
        root,
        SUITE_INVENTORY_RELATIVE,
        "Task Witness suite inventory",
    )
    require_exact_fields(
        inventory,
        {
            "schema_version",
            "contract",
            "runtime_status",
            "entries",
            "aggregates",
        },
        "Task Witness suite inventory",
    )
    if (
        type(inventory["schema_version"]) is not int
        or inventory["schema_version"] != 1
        or inventory["contract"] != SUITE_INVENTORY_CONTRACT
        or inventory["runtime_status"] != "retired-source-stage"
    ):
        raise ValueError("Task Witness suite inventory contract drift")
    entries = inventory["entries"]
    if not isinstance(entries, list) or len(entries) != len(SUITE_PROJECTIONS):
        raise ValueError("Task Witness suite inventory entry count drift")

    counts: list[dict[str, object]] = []
    expected_count_total = 0
    for index, (entry, projection) in enumerate(
        zip(entries, SUITE_PROJECTIONS, strict=True)
    ):
        label = f"Task Witness suite inventory entry {index}"
        if not isinstance(entry, dict):
            raise ValueError(f"{label} is not an object")
        require_exact_fields(
            entry,
            {
                "argv",
                "executor",
                "expected_count",
                "expected_terminal",
                "id",
                "phase",
                "targets",
            },
            label,
        )
        executor = entry["executor"]
        if not isinstance(executor, dict):
            raise ValueError(f"{label} executor is not an object")
        require_exact_fields(executor, {"kind"}, f"{label} executor")
        suite_id, phase, targets = projection
        if (
            executor["kind"] != "qualified-cpython"
            or entry["id"] != suite_id
            or entry["argv"] != [*SUITE_DRIVER_ARGV_PREFIX, suite_id]
            or entry["phase"] != phase
            or entry["targets"] != list(targets)
            or entry["expected_terminal"] != "passed"
        ):
            raise ValueError(f"{label} projection drift")
        expected_count = entry["expected_count"]
        if (
            type(expected_count) is not int
            or expected_count != SUITE_EXPECTED_COUNTS[suite_id]
        ):
            raise ValueError(f"{label} expected count is invalid")
        counts.append({"expected_count": expected_count, "id": suite_id})
        expected_count_total += expected_count

    aggregates = inventory["aggregates"]
    if not isinstance(aggregates, dict):
        raise ValueError("Task Witness suite inventory aggregates are not an object")
    require_exact_fields(
        aggregates,
        {
            "counts_sha256",
            "entries_sha256",
            "entry_count",
            "expected_count_total",
        },
        "Task Witness suite inventory aggregates",
    )
    if (
        require_sha256_hex(
            aggregates["counts_sha256"],
            "Task Witness suite inventory count aggregate",
        )
        != hashlib.sha256(_canonical_json(counts)).hexdigest()
        or require_sha256_hex(
            aggregates["entries_sha256"],
            "Task Witness suite inventory entry aggregate",
        )
        != hashlib.sha256(_canonical_json(entries)).hexdigest()
    ):
        raise ValueError("Task Witness suite inventory aggregate digest drift")
    if (
        type(aggregates["entry_count"]) is not int
        or aggregates["entry_count"] != len(entries)
        or type(aggregates["expected_count_total"]) is not int
        or aggregates["expected_count_total"] != expected_count_total
    ):
        raise ValueError("Task Witness suite inventory aggregate count drift")
    return inventory


def _require_sha1(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA1_HEX.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-1 hex digest")
    return value


def _git_object_sha1(kind: str, raw: bytes) -> str:
    framed = kind.encode("ascii") + b" " + str(len(raw)).encode("ascii") + b"\0" + raw
    return hashlib.sha1(framed).hexdigest()


def _parse_tree(raw: bytes, label: str) -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []
    offset = 0
    try:
        while offset < len(raw):
            separator = raw.index(b" ", offset)
            terminator = raw.index(b"\0", separator + 1)
            mode = raw[offset:separator].decode("ascii")
            name_raw = raw[separator + 1 : terminator]
            object_raw = raw[terminator + 1 : terminator + 21]
            if len(object_raw) != 20:
                raise ValueError
            name = name_raw.decode("utf-8")
            if (
                mode not in {"40000", "100644", "100755"}
                or not name
                or "/" in name
                or name in {".", ".."}
            ):
                raise ValueError
            entries.append((mode, name, object_raw.hex()))
            offset = terminator + 21
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"{label} tree payload is invalid") from error
    if len({name for _, name, _ in entries}) != len(entries):
        raise ValueError(f"{label} tree contains duplicate names")
    ordered = sorted(
        entries,
        key=lambda entry: (
            entry[1].encode("utf-8") + (b"/" if entry[0] == "40000" else b"")
        ),
    )
    if entries != ordered:
        raise ValueError(f"{label} tree entry order drift")
    return entries


def _bridge_snapshot(
    root: Path,
    generation: str,
    relative: str,
) -> tuple[bytes, int]:
    snapshot = (
        Path("release/task-witness/migration")
        / generation
        / BRIDGE_SNAPSHOT_PATHS[generation][relative]
    )
    path = verified_reviewed_path(root, snapshot.as_posix())
    mode = stat.S_IMODE(path.stat().st_mode)
    expected_mode = 0o755 if relative == "controller/task_witness_deploy.py" else 0o644
    if generation == "bridge" and relative == "client/task_witness_client.py":
        expected_mode = 0o755
    if mode != expected_mode:
        raise ValueError(f"Task Witness {generation} {relative} snapshot mode drift")
    return path.read_bytes(), expected_mode


def validate_migration_inventory(root: Path) -> None:
    migration = root / MIGRATION_RELATIVE
    try:
        metadata = migration.lstat()
    except FileNotFoundError as error:
        raise ValueError("Task Witness migration inventory drift") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("Task Witness migration inventory drift")

    directories: set[str] = set()
    files: set[str] = set()
    try:
        entries = migration.rglob("*")
        for path in entries:
            relative = path.relative_to(migration).as_posix()
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not (
                stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
            ):
                raise ValueError(
                    "Task Witness migration inventory contains an unsupported entry"
                )
            if stat.S_ISDIR(metadata.st_mode):
                directories.add(relative)
            else:
                if metadata.st_nlink != 1:
                    raise ValueError(
                        "Task Witness migration snapshot must have link count one"
                    )
                files.add(relative)
    except OSError as error:
        raise ValueError("Task Witness migration inventory is unavailable") from error

    if (
        directories != EXPECTED_MIGRATION_DIRECTORIES
        or files != EXPECTED_MIGRATION_FILES
    ):
        raise ValueError("Task Witness migration inventory drift")


def _bridge_source_bytes(root: Path, generation: str, relative: str) -> bytes:
    if relative in BRIDGE_SNAPSHOT_PATHS[generation]:
        return _bridge_snapshot(root, generation, relative)[0]
    if generation == "bridge" and relative == "controller/policy.json":
        return _bridge_snapshot(root, "freeze5", relative)[0]
    if relative not in BRIDGE_STABLE_PATHS:
        raise ValueError(f"Task Witness {generation} plugin inventory drift")
    return verified_reviewed_path(
        root, (PLUGIN_RELATIVE / relative).as_posix()
    ).read_bytes()


def _validate_current_client_boundary(current: bytes) -> None:
    profile = re.compile(rb'(?m)^CLIENT_RELEASE_PROFILE = "([a-z0-9-]+)"$')
    generation = re.compile(
        rb'(?m)^CLIENT_SOURCE_GENERATION_SHA256 = "([0-9a-f]{64})"$'
    )
    matches = list(profile.finditer(current))
    if len(matches) != 1 or matches[0].group(1) != b"tw4-current":
        raise ValueError("Task Witness current client release profile drift")
    matches = list(generation.finditer(current))
    if len(matches) != 1:
        raise ValueError("Task Witness current client generation identity drift")
    start, end = matches[0].span(1)
    normalized = current[:start] + (b"0" * 64) + current[end:]
    digest = hashlib.sha256(normalized).hexdigest().encode("ascii")
    if digest != matches[0].group(1):
        raise ValueError("Task Witness current client generation identity drift")


def _walk_plugin_tree(
    root_tree: str,
    objects: dict[str, tuple[str, bytes]],
    label: str,
) -> tuple[set[str], set[str], dict[str, tuple[str, str]]]:
    required: set[str] = set()
    directories: set[str] = set()
    files: dict[str, tuple[str, str]] = {}

    def tree_entries(oid: str) -> list[tuple[str, str, str]]:
        value = objects.get(oid)
        if value is None or value[0] != "tree":
            raise ValueError(f"Task Witness {label} provenance tree is unavailable")
        required.add(oid)
        return _parse_tree(value[1], f"Task Witness {label}")

    root_entries = tree_entries(root_tree)
    plugins = [entry for entry in root_entries if entry[:2] == ("40000", "plugins")]
    if len(plugins) != 1:
        raise ValueError(f"Task Witness {label} plugins tree drift")
    plugin_entries = tree_entries(plugins[0][2])
    task_witness = [
        entry for entry in plugin_entries if entry[:2] == ("40000", "task-witness")
    ]
    if len(task_witness) != 1:
        raise ValueError(f"Task Witness {label} plugin tree drift")

    def descend(oid: str, prefix: str) -> None:
        for mode, name, child in tree_entries(oid):
            relative = f"{prefix}/{name}" if prefix else name
            if mode == "40000":
                directories.add(relative)
                descend(child, relative)
            else:
                files[relative] = (mode, child)

    descend(task_witness[0][2], "")
    return required, directories, files


def _plugin_subtree_sha256(
    root: Path,
    generation: str,
    directories: set[str],
    files: dict[str, tuple[str, str]],
) -> str:
    entries: list[dict[str, object]] = [
        {"kind": "directory", "path": relative} for relative in directories
    ]
    for relative in files:
        raw = _bridge_source_bytes(root, generation, relative)
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
    return hashlib.sha256(_canonical_json(projection)).hexdigest()


def validate_bridge_history(root: Path) -> dict[str, object]:
    validate_migration_inventory(root)
    identity, identity_raw = _bounded_json_object_capture(
        root,
        BRIDGE_IDENTITY_RELATIVE,
        "Task Witness bridge identity",
        canonical=False,
    )
    provenance, provenance_raw = _bounded_json_object_capture(
        root,
        BRIDGE_PROVENANCE_RELATIVE,
        "Task Witness bridge provenance",
        canonical=False,
    )
    require_exact_fields(
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
        "Task Witness bridge identity",
    )
    require_exact_fields(
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
        "Task Witness bridge provenance",
    )
    _validate_content_document(identity, "Task Witness bridge identity")
    _validate_content_document(provenance, "Task Witness bridge provenance")
    if (
        type(identity["schema_version"]) is not int
        or identity["schema_version"] != 1
        or identity["contract"] != "task-witness-tw4-bridge-identity-v1"
    ):
        raise ValueError("Task Witness bridge identity contract drift")
    if (
        type(provenance["schema_version"]) is not int
        or provenance["schema_version"] != 1
        or provenance["contract"] != "task-witness-tw4-bridge-provenance-v1"
    ):
        raise ValueError("Task Witness bridge provenance contract drift")
    if provenance["repository_id"] != "nisavid/agents":
        raise ValueError("Task Witness bridge repository identity drift")
    if identity["allowed_edges"] != [
        {"from": "freeze5", "source_mode": "harness_snapshot", "to": "bridge"}
    ]:
        raise ValueError("Task Witness bridge allowed edge drift")
    provenance_sha256 = hashlib.sha256(provenance_raw).hexdigest()
    if provenance_sha256 != require_sha256_hex(
        identity["provenance_sha256"], "Task Witness bridge provenance"
    ):
        raise ValueError("Task Witness bridge provenance byte identity drift")

    identity_fields = {
        "repository_id",
        "commit_sha1",
        "tree_sha1",
        "plugin_subtree_sha256",
        "controller_sha256",
        "policy_sha256",
        "client_sha256",
        "source_mode",
    }
    proof_fields = {"commit_sha1", "tree_sha1"}
    parsed_identity: dict[str, dict] = {}
    for generation in ("freeze5", "bridge"):
        generation_identity = identity[generation]
        generation_proof = provenance[generation]
        if not isinstance(generation_identity, dict) or not isinstance(
            generation_proof, dict
        ):
            raise ValueError(f"Task Witness {generation} identity must be an object")
        require_exact_fields(
            generation_identity, identity_fields, f"Task Witness {generation} identity"
        )
        require_exact_fields(
            generation_proof, proof_fields, f"Task Witness {generation} provenance"
        )
        if (
            generation_identity["repository_id"] != "nisavid/agents"
            or generation_identity["source_mode"] != "harness_snapshot"
        ):
            raise ValueError(f"Task Witness {generation} source identity drift")
        for field in ("commit_sha1", "tree_sha1"):
            _require_sha1(
                generation_identity[field], f"Task Witness {generation} {field}"
            )
            if generation_identity[field] != generation_proof[field]:
                raise ValueError(f"Task Witness {generation} provenance identity drift")
        for field in (
            "plugin_subtree_sha256",
            "controller_sha256",
            "policy_sha256",
            "client_sha256",
        ):
            require_sha256_hex(
                generation_identity[field], f"Task Witness {generation} {field}"
            )
        for field, expected in EXPECTED_BRIDGE_IDENTITIES[generation].items():
            if generation_identity[field] != expected:
                raise ValueError(f"Task Witness {generation} frozen identity drift")
        parsed_identity[generation] = generation_identity
    if parsed_identity["freeze5"]["commit_sha1"] != FREEZE5_COMMIT_SHA1:
        raise ValueError("Task Witness Freeze 5 commit identity drift")
    current_controller = verified_reviewed_path(
        root, (PLUGIN_RELATIVE / "controller/task_witness_deploy.py").as_posix()
    ).read_bytes()
    bridge_controller = _bridge_snapshot(
        root, "bridge", "controller/task_witness_deploy.py"
    )[0]
    if current_controller != bridge_controller:
        raise ValueError("Task Witness bridge controller snapshot drift")
    current_client = verified_reviewed_path(
        root, (PLUGIN_RELATIVE / "client/task_witness_client.py").as_posix()
    ).read_bytes()
    _validate_current_client_boundary(current_client)

    encoded_objects = provenance["objects"]
    if not isinstance(encoded_objects, list):
        raise ValueError("Task Witness bridge provenance objects must be a list")
    objects: dict[str, tuple[str, bytes]] = {}
    observed_order: list[str] = []
    for index, encoded in enumerate(encoded_objects):
        if not isinstance(encoded, dict):
            raise ValueError("Task Witness bridge provenance object is invalid")
        require_exact_fields(
            encoded,
            {"type", "sha1", "raw_base64"},
            f"Task Witness bridge provenance object {index}",
        )
        kind = encoded["type"]
        oid = _require_sha1(
            encoded["sha1"], f"Task Witness bridge provenance object {index}"
        )
        if kind not in {"commit", "tree"} or not isinstance(encoded["raw_base64"], str):
            raise ValueError("Task Witness bridge provenance object type drift")
        try:
            raw = base64.b64decode(encoded["raw_base64"], validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError(
                "Task Witness bridge provenance object encoding drift"
            ) from error
        if (
            base64.b64encode(raw).decode("ascii") != encoded["raw_base64"]
            or _git_object_sha1(kind, raw) != oid
        ):
            raise ValueError("Task Witness bridge provenance object identity drift")
        if oid in objects:
            raise ValueError("Task Witness bridge provenance object inventory drift")
        objects[oid] = (kind, raw)
        observed_order.append(oid)
    if observed_order != sorted(observed_order):
        raise ValueError("Task Witness bridge provenance object order drift")

    freeze5_commit = objects.get(parsed_identity["freeze5"]["commit_sha1"])
    bridge_commit = objects.get(parsed_identity["bridge"]["commit_sha1"])
    if (
        freeze5_commit is None
        or freeze5_commit[0] != "commit"
        or bridge_commit is None
        or bridge_commit[0] != "commit"
    ):
        raise ValueError("Task Witness bridge provenance commit inventory drift")
    freeze5_tree_line = (
        b"tree " + parsed_identity["freeze5"]["tree_sha1"].encode("ascii") + b"\n"
    )
    if not freeze5_commit[1].startswith(freeze5_tree_line):
        raise ValueError("Task Witness Freeze 5 commit tree drift")
    expected_bridge_commit = (
        f"tree {parsed_identity['bridge']['tree_sha1']}\n"
        f"parent {FREEZE5_COMMIT_SHA1}\n"
        f"author Ivan D Vasin <ivan@nisavid.io> {BRIDGE_COMMIT_TIMESTAMP}\n"
        f"committer Ivan D Vasin <ivan@nisavid.io> {BRIDGE_COMMIT_TIMESTAMP}\n"
        "\nfeat(task-witness/b1): freeze transition bridge\n"
    ).encode("utf-8")
    if bridge_commit[1] != expected_bridge_commit:
        raise ValueError("Task Witness bridge commit raw drift")

    required_objects = {
        parsed_identity["freeze5"]["commit_sha1"],
        parsed_identity["bridge"]["commit_sha1"],
    }
    walked: dict[str, tuple[set[str], dict[str, tuple[str, str]]]] = {}
    for generation in ("freeze5", "bridge"):
        trees, directories, files = _walk_plugin_tree(
            parsed_identity[generation]["tree_sha1"], objects, generation
        )
        required_objects.update(trees)
        walked[generation] = (directories, files)
    if set(objects) != required_objects:
        raise ValueError("Task Witness bridge provenance object inventory drift")

    expected_files = BRIDGE_STABLE_PATHS | {
        ".claude-plugin/plugin.json",
        ".codex-plugin/plugin.json",
        "client/task_witness_client.py",
        "controller/policy.json",
        "controller/task_witness_deploy.py",
    }
    for generation in ("freeze5", "bridge"):
        directories, files = walked[generation]
        if set(files) != expected_files:
            raise ValueError(f"Task Witness {generation} plugin inventory drift")
        for relative, (mode, blob_oid) in files.items():
            raw = _bridge_source_bytes(root, generation, relative)
            expected_mode = (
                "100755"
                if relative == "controller/task_witness_deploy.py"
                else "100644"
            )
            if generation == "bridge" and relative == "client/task_witness_client.py":
                expected_mode = "100755"
            if mode != expected_mode or _git_object_sha1("blob", raw) != blob_oid:
                raise ValueError(f"Task Witness {generation} {relative} snapshot drift")
        subtree = _plugin_subtree_sha256(root, generation, directories, files)
        if subtree != parsed_identity[generation]["plugin_subtree_sha256"]:
            raise ValueError(f"Task Witness {generation} plugin subtree drift")
        for component, relative in (
            ("controller", "controller/task_witness_deploy.py"),
            ("policy", "controller/policy.json"),
            ("client", "client/task_witness_client.py"),
        ):
            digest = hashlib.sha256(
                _bridge_source_bytes(root, generation, relative)
            ).hexdigest()
            if digest != parsed_identity[generation][f"{component}_sha256"]:
                raise ValueError(
                    f"Task Witness {generation} {component} identity drift"
                )

    freeze5_files = walked["freeze5"][1]
    bridge_files = walked["bridge"][1]
    changed = {
        relative
        for relative in expected_files
        if freeze5_files[relative] != bridge_files[relative]
    }
    if changed != {
        ".claude-plugin/plugin.json",
        ".codex-plugin/plugin.json",
        "client/task_witness_client.py",
        "controller/task_witness_deploy.py",
    }:
        raise ValueError("Task Witness bridge four-path transition drift")
    return {
        "bridge_identity_sha256": hashlib.sha256(identity_raw).hexdigest(),
        "bridge_provenance_sha256": provenance_sha256,
        "freeze5": identity["freeze5"],
        "bridge": identity["bridge"],
    }


def validate_direct_test_inventory(root: Path) -> None:
    for package_relative, expected_files in EXPECTED_DIRECT_TEST_PACKAGES.items():
        package = root / package_relative
        try:
            metadata = package.lstat()
        except FileNotFoundError:
            raise ValueError("Task Witness direct-test inventory drift")
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("Task Witness direct-test inventory drift")

        closure: set[str] = set()
        for path in package.rglob("*"):
            relative = path.relative_to(package).as_posix()
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise ValueError("Task Witness direct-test inventory drift")
            if metadata.st_nlink != 1:
                raise ValueError("Task Witness direct-test inventory drift")
            closure.add(relative)
        if closure != expected_files:
            raise ValueError("Task Witness direct-test inventory drift")


def validate_reviewed_sources(
    root: Path,
    reviewed_paths: dict[str, Path] | None = None,
) -> None:
    """Validate the fixed, release-owned source and direct-test boundary.

    This validator deliberately does not infer behavior from keywords or an
    incomplete static scan. Exact inventory and release identities bind the
    executable bytes; release-owned executable tests cover Task Witness-owned
    package boundaries and behavior, not registered-validator isolation.
    """

    validate_direct_test_inventory(root)
    reviewed_paths = reviewed_paths or verified_reviewed_paths(root)
    tripwires = load_source_shape_record(root)
    current_entries: list[tuple[str, str]] = []
    current_line_counts: dict[str, int] = {}
    for relative_path in REVIEWED_SHAPE_PATHS:
        source_path = reviewed_paths[relative_path]
        source = source_path.read_text(encoding="utf-8")
        if source_path.suffix == ".py":
            try:
                compile(source, str(source_path), "exec")
            except SyntaxError as error:
                raise ValueError("Task Witness source syntax is invalid") from error
        review_lines = nonblank_noncomment_line_count(source)
        file_tripwire = tripwires["file_nonblank_noncomment_lines"].get(relative_path)
        if file_tripwire is not None and review_lines > file_tripwire:
            raise ValueError("Task Witness file source-line tripwire exceeded")
        current_line_counts[relative_path] = review_lines
        current_entries.append(
            (relative_path, hashlib.sha256(source_path.read_bytes()).hexdigest())
        )
    current_aggregates = {
        name: sum(current_line_counts[path] for path in paths)
        for name, paths in SOURCE_SHAPE_SETS.items()
    }
    for name, aggregate in current_aggregates.items():
        if aggregate > tripwires["aggregate_nonblank_noncomment_lines"][name]:
            raise ValueError(
                f"Task Witness {name} aggregate source-line tripwire exceeded; "
                "an independent source-shape review is required before rebaselining"
            )
    if current_line_counts != tripwires["reviewed_file_nonblank_noncomment_lines"]:
        raise ValueError("Task Witness source-line measurement drift")
    if current_aggregates != tripwires["reviewed_aggregate_nonblank_noncomment_lines"]:
        raise ValueError("Task Witness aggregate source-line measurement drift")
    if current_entries != tripwires["source_byte_entries"]:
        raise ValueError("Task Witness source byte identity drift")


def validate_candidate_source(
    root: Path,
    include_suite_inventory: bool,
) -> dict[str, object]:
    """Validate one candidate source closure and return its bridge history."""

    plugin = root / PLUGIN_RELATIVE
    validate_inventory(plugin)
    validate_manifests(plugin)
    validate_public_release_registration(root)
    if include_suite_inventory:
        validate_suite_inventory(root)
    validate_reviewed_sources(root)
    return validate_bridge_history(root)


def _validator_invocation(
    argv: list[str] | None,
) -> tuple[Path, str, dict[str, Path]]:
    arguments = list(sys.argv[1:] if argv is None else argv)

    def package_root(raw: str) -> Path:
        if not raw or raw.startswith("-"):
            raise ValueError("Task Witness validator arguments are invalid")
        return Path(raw).resolve()

    def absolute_operand(raw: str) -> Path:
        try:
            return Path(_absolute_path(raw, "Task Witness validator operand"))
        except ValueError as error:
            raise ValueError("Task Witness validator arguments are invalid") from error

    if not arguments:
        return Path.cwd(), "package", {}
    if len(arguments) == 1:
        return package_root(arguments[0]), "package", {}
    if len(arguments) == 2 and arguments[1] == "--source-stage":
        return absolute_operand(arguments[0]), "source-stage", {}
    if len(arguments) == 3 and arguments[1] == "--qualification":
        return (
            absolute_operand(arguments[0]),
            "qualification",
            {"host_receipt": absolute_operand(arguments[2])},
        )
    final_options = (
        "--candidate-root",
        "--release-manifest",
        "--macos-receipt",
        "--linux-receipt",
        "--review-evidence",
    )
    if (
        len(arguments) == 12
        and arguments[1] == "--final-release"
        and tuple(arguments[2::2]) == final_options
    ):
        return (
            absolute_operand(arguments[0]),
            "final-release",
            {
                "candidate_root": absolute_operand(arguments[3]),
                "release_manifest": absolute_operand(arguments[5]),
                "macos_receipt": absolute_operand(arguments[7]),
                "linux_receipt": absolute_operand(arguments[9]),
                "review_evidence": absolute_operand(arguments[11]),
            },
        )
    raise ValueError("Task Witness validator arguments are invalid")


def main(argv: list[str] | None = None) -> int:
    try:
        root, mode, evidence = _validator_invocation(argv)
        qualification_receipt: dict | None = None
        if mode == "final-release":
            candidate_root = evidence["candidate_root"]
            evidence_paths = {
                name: evidence[name]
                for name in (
                    "release_manifest",
                    "macos_receipt",
                    "linux_receipt",
                    "review_evidence",
                )
            }
            if (
                len(set(evidence_paths.values())) != len(evidence_paths)
                or any(
                    path.is_relative_to(root)
                    or path.is_relative_to(candidate_root)
                    for path in evidence_paths.values()
                )
            ):
                raise ValueError(
                    "Task Witness final-release evidence paths must be distinct and external"
                )
            manifest, manifest_raw = _bounded_external_canonical_json_object(
                evidence_paths["release_manifest"],
                "Task Witness TW4 release manifest",
                private=True,
            )
            macos_receipt, macos_raw = _bounded_external_canonical_json_object(
                evidence_paths["macos_receipt"],
                "Task Witness macOS host qualification receipt",
                maximum=HOST_RECEIPT_MAX_BYTES,
                private=True,
            )
            linux_receipt, linux_raw = _bounded_external_canonical_json_object(
                evidence_paths["linux_receipt"],
                "Task Witness Linux host qualification receipt",
                maximum=HOST_RECEIPT_MAX_BYTES,
                private=True,
            )
            validate_final_release_evidence(
                root,
                candidate_root,
                manifest,
                manifest_raw,
                macos_receipt,
                macos_raw,
                linux_receipt,
                linux_raw,
                evidence_paths["review_evidence"],
            )
        elif mode == "qualification":
            receipt_path = evidence["host_receipt"]
            if receipt_path.is_relative_to(root):
                raise ValueError(
                    "Task Witness host qualification receipt must be external"
                )
            qualification_receipt, _receipt_raw = _bounded_external_canonical_json_object(
                evidence["host_receipt"],
                "Task Witness host qualification receipt",
                maximum=HOST_RECEIPT_MAX_BYTES,
                private=True,
            )
            parse_host_qualification_receipt(qualification_receipt)
        if mode != "final-release":
            validate_candidate_source(
                root,
                include_suite_inventory=mode in {"source-stage", "qualification"},
            )
            if qualification_receipt is not None:
                validate_qualification_candidate_binding(
                    root,
                    qualification_receipt,
                    _qualification_candidate_evidence(root, qualification_receipt),
                )
    except Exception as error:
        print(f"Task Witness validation failed: {error}", file=sys.stderr)
        return 1
    print(f"Task Witness {mode} validation passed")
    return 0


def entrypoint_main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--qualification" in arguments or "--final-release" in arguments:
        print(
            "Task Witness validation failed: native qualification and final-release "
            "validation are unavailable in this source-stage release",
            file=sys.stderr,
        )
        return 1
    return main(arguments)


if __name__ == "__main__":
    raise SystemExit(entrypoint_main())
