#!/usr/bin/env python3
"""Validate Tricritical's portable semantic and harness-adapter contract."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import stat
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIRECTORY))

from agent_plugins_standard import (  # noqa: E402
    discover_direct_skills,
    load_agent_plugin_manifest,
    validate_skill_resource_links,
)
from refresh_transaction import replace_generated_artifacts  # noqa: E402

try:
    import yaml
except ModuleNotFoundError:
    yaml = None


CORE_SKILLS = (
    "review",
    "intent",
    "runtime",
    "structure",
    "adjudicate",
    "revise",
    "loop",
)
PERSONA_SKILLS = {
    "oathfinder": "intent",
    "faultwalker": "runtime",
    "knotcutter": "structure",
    "claimweigher": "adjudicate",
    "formwright": "revise",
    "fathomkeeper": "loop",
}
MUTATOR_SKILL = "revise"
LOOP_SKILL = "loop"
STARTER_PROMPT_SKILLS = ("review", "adjudicate", "loop")
PORTABILITY_PATTERNS = (
    re.compile(r"/users/", re.IGNORECASE),
    re.compile(r"\bchezmoi\b", re.IGNORECASE),
    re.compile(r"\bsystalyze\b", re.IGNORECASE),
    re.compile(r"\bapi[\s_-]*key\b", re.IGNORECASE),
    re.compile(r"\bauthorization\s*:\s*bearer\b", re.IGNORECASE),
)
CLAUDE_MARKETPLACE_ENTRY = {
    "name": "tricritical",
    "source": "./plugins/tricritical",
    "category": "developer-tools",
}
EVAL_FIXTURES = (
    "review-read-only.md",
    "critic-isolation.md",
    "model-selection-receipt.md",
    "topology-unauthorized-user-owned-task.md",
    "topology-inadequate-foreign-isolation.md",
    "topology-prohibited-subdelegation-external-action.md",
    "topology-valid-native-dispatch.md",
    "adjudicate-external-feedback.md",
    "revise-authority.md",
    "loop-fixed-point.md",
    "adapter-thinness.md",
    "isolated-falsification.md",
    "terminal-needs-operator-decision.md",
    "terminal-blocked.md",
    "hostile-verification-command.md",
    "critic-reverse-edge.md",
    "review-reverse-edge.md",
    "hermetic-bundle-boundary.md",
    "pre-edit-identity-mismatch.md",
    "runner-shell-denied.md",
    "runner-network-browser-denied.md",
    "runner-filesystem-tools-denied.md",
    "runner-unrecorded-enforcement.md",
    "adaptive-budget-tiers.md",
    "adaptive-budget-invalid-override.md",
    "adaptive-budget-exhaustion.md",
    "adaptive-budget-no-progress.md",
    "adaptive-budget-repeat-extension.md",
    "adaptive-budget-tool-unavailable.md",
    "critic-timeout-incomplete.md",
    "specialist-unusable-incomplete.md",
    "corroboration-attention-not-truth.md",
    "disagreement-frozen-evidence.md",
    "intent-no-spec.md",
    "structure-repo-standard-override.md",
    "structure-smell-positive.md",
    "structure-smell-negative.md",
    "severity-calibration.md",
    "risk-specialist-selection.md",
    "runtime-green-test-false-positive.md",
    "loop-incomplete-successful-verification.md",
    "loop-degraded-completion.md",
    "loop-no-original-mutation-authority.md",
    "adaptive-budget-no-progress-irrelevant-clarification.md",
    "adaptive-budget-contract-changing-clarification.md",
)
EVAL_DELIVERY_CONTRACT = {
    "with_skill_executor_inputs": ["fixture", "candidate_skill_bundle"],
    "candidate_skill_delivery": "immutable_inline_bundle_or_explicit_mount",
    "candidate_skill_bundle": {
        "identity": "sha256",
        "contents": "entrypoint_and_transitive_markdown_references",
        "executor_access": "bundle_only",
        "outside_access": "forbidden",
    },
    "runner_enforcement": {
        "workspace": "isolated_ephemeral",
        "candidate_skill_mount": "read_only",
        "shell": "denied",
        "browser": "denied",
        "network": "denied",
        "filesystem_outside_bundle": "denied",
        "tools": {
            "default": "denied",
            "allowlist": [],
            "allowlist_policy": "explicit_minimal_enforced_contract_only",
        },
    },
    "clean_evidence_requires": [
        "recorded_runner_enforcement",
        "candidate_skill_bundle_identity",
    ],
    "grader_inputs_after_execution": [
        "fixture",
        "response",
        "grader_expectations",
    ],
}
SHARED_INPUT_BOUNDARY_PATH = "references/review-input-boundary.md"
SHARED_OUTPUT_CONTRACT_PATH = "references/review-output-contract.md"
SHARED_INVOCATION_BOUNDARY_PATH = "references/invocation-boundary.md"
SHARED_INPUT_BOUNDARY_LINK = (
    "[the shared review-input boundary](references/review-input-boundary.md)"
)
SHARED_OUTPUT_CONTRACT_LINK = (
    "[the shared review-output contract](references/review-output-contract.md)"
)
SHARED_INVOCATION_BOUNDARY_LINK = (
    "[the shared invocation boundary](references/invocation-boundary.md)"
)
SKILL_LOCAL_TOPOLOGY_LINK = "[topology.json](references/topology.json)"
REVIEW_COMPLETENESS_LINK = (
    "[the completeness and synthesis rules](references/completeness-and-synthesis.md)"
)
LOOP_OPERATOR_CHOICE_LINK = (
    "[the portable operator-choice contract](references/operator-choice.md)"
)
TERMINAL_EVIDENCE_REFERENCE = "skills/loop/references/review-evidence.md"
TERMINAL_EVIDENCE_RUNTIME = "skills/loop/scripts/review_evidence.py"
TASK_WITNESS_PROVIDER = "task-witness-provider.json"
TERMINAL_EVIDENCE_CONTRACT = "tricritical-terminal-review-evidence-v2"
TERMINAL_PROJECTION_CONTRACT = "tricritical-terminal-review-projection-v2"
ROLECASTING_EVIDENCE_CONTRACT = "rolecasting-dispatch-evidence-v2"
ROLECASTING_PROJECTION_CONTRACT = "rolecasting-dispatch-projection-v2"
TRICRITICAL_AUTHORITY_PROFILE = "tricritical-cooperative-review-v1"
TERMINAL_EVIDENCE_VALIDATOR_ID = "tricritical-terminal-review-evidence-validator-v2"
PROVIDER_CONTRACT = "task-witness-provider-declaration-v1"
VALIDATOR_MANIFEST_CONTRACT = "task-witness-validator-artifact-manifest-v1"
REVIEW_OUTPUT_SKILLS = ("review", "intent", "runtime", "structure", "adjudicate")
CONTENT_LOCK_PATH = "content-lock.json"
CODEX_LOOP_DISCOVERY_PROMPT = (
    "Use $tricritical:loop to review and revise to a terminal state."
)
CODEX_OPERATOR_CHOICE_MAPPING = (
    "Map its portable synchronous operator-choice capability to "
    "`request_user_input` with no timeout or automatic resolution."
)
CLAUDE_OPERATOR_CHOICE_MAPPING = (
    "Map the skill's portable synchronous operator-choice capability to "
    "`AskUserQuestion` or the current synchronous equivalent, with no timeout or "
    "automatic resolution."
)
HARNESS_API_TOKENS = ("request_user_input", "AskUserQuestion")
MODEL_SELECTION_REQUIREMENT = "adapter:model-selection-receipt"
INVOCATION_TOPOLOGY_REQUIREMENT = "adapter:rolecasting-invocation-topology-receipt"
SEMVER = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
STABLE_METADATA_FIELDS = (
    "st_dev",
    "st_ino",
    "st_mode",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)
DIRECTORY_DESCRIPTOR_FLAGS = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")
REGULAR_FILE_DESCRIPTOR_FLAGS = ("O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK")
HTML_RESOURCE_ATTRIBUTES = {
    "action",
    "archive",
    "background",
    "cite",
    "classid",
    "code",
    "codebase",
    "data",
    "formaction",
    "href",
    "icon",
    "longdesc",
    "manifest",
    "poster",
    "profile",
    "src",
    "usemap",
    "xlink:href",
}
HTML_SPACE_SEPARATED_RESOURCE_ATTRIBUTES = {"archive", "ping"}
HTML_UNSUPPORTED_RESOURCE_ATTRIBUTES = {"srcdoc", "style"}
HTML_SOURCE_SET_ATTRIBUTES = {"imagesrcset", "srcset"}


def fail(message: str) -> None:
    print(f"Tricritical contract validation failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def require_mapping(value, field: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a mapping")
    return value


def require_string(value, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def require_string_list(value, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a list of strings")
    return value


def require_exact_keys(value: dict, keys: set[str], field: str) -> None:
    if set(value) != keys:
        raise ValueError(f"{field} has unexpected or missing fields")


def required_descriptor_flag(name: str) -> int:
    value = getattr(os, name, None)
    if type(value) is not int or value == 0:
        raise ValueError(
            f"platform lacks required descriptor safety capability: {name}"
        )
    return value


def directory_open_flags() -> int:
    return os.O_RDONLY | sum(
        required_descriptor_flag(name) for name in DIRECTORY_DESCRIPTOR_FLAGS
    )


def regular_file_open_flags() -> int:
    return os.O_RDONLY | sum(
        required_descriptor_flag(name) for name in REGULAR_FILE_DESCRIPTOR_FLAGS
    )


class RepositoryAnchor:
    """Descriptor-bound repository inputs opened without following symlinks."""

    def __init__(
        self,
        lexical_path: Path,
        repository_descriptor: int,
        plugin_descriptor: int,
        marketplace_descriptor: int,
    ) -> None:
        self.lexical_path = lexical_path
        self.repository_descriptor = repository_descriptor
        self.plugin_descriptor = plugin_descriptor
        self.marketplace_descriptor = marketplace_descriptor

    def close(self) -> None:
        for descriptor in (
            self.marketplace_descriptor,
            self.plugin_descriptor,
            self.repository_descriptor,
        ):
            os.close(descriptor)


def metadata_changed(before, after) -> bool:
    return any(
        getattr(before, field) != getattr(after, field)
        for field in STABLE_METADATA_FIELDS
    )


def open_anchored_directory(path: Path) -> tuple[Path, int]:
    """Open every lexical component relative to its no-follow parent descriptor."""
    absolute = Path(os.path.abspath(os.fspath(path)))
    current_descriptor = os.open(absolute.anchor, directory_open_flags())
    try:
        for part in absolute.parts[1:]:
            try:
                next_descriptor = os.open(
                    part,
                    directory_open_flags(),
                    dir_fd=current_descriptor,
                )
            except OSError as error:
                raise ValueError(
                    "supplied repository path contains a symlink ancestor or invalid "
                    f"directory: {absolute}"
                ) from error
            metadata = os.fstat(next_descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(next_descriptor)
                raise ValueError(
                    "supplied repository path contains a symlink ancestor or invalid "
                    f"directory: {absolute}"
                )
            os.close(current_descriptor)
            current_descriptor = next_descriptor
        return absolute, current_descriptor
    except Exception:
        os.close(current_descriptor)
        raise


def open_relative_directory(
    parent_descriptor: int, components: tuple[str, ...], field: str
) -> int:
    current_descriptor = os.dup(parent_descriptor)
    try:
        for component in components:
            try:
                next_descriptor = os.open(
                    component,
                    directory_open_flags(),
                    dir_fd=current_descriptor,
                )
            except OSError as error:
                raise ValueError(
                    f"{field} path must not contain symlinks or non-directories"
                ) from error
            metadata = os.fstat(next_descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(next_descriptor)
                raise ValueError(
                    f"{field} path must not contain symlinks or non-directories"
                )
            os.close(current_descriptor)
            current_descriptor = next_descriptor
        return current_descriptor
    except Exception:
        os.close(current_descriptor)
        raise


def open_relative_regular_file(
    parent_descriptor: int, components: tuple[str, ...], field: str
) -> int:
    directory_descriptor = open_relative_directory(
        parent_descriptor, components[:-1], field
    )
    try:
        try:
            descriptor = os.open(
                components[-1],
                regular_file_open_flags(),
                dir_fd=directory_descriptor,
            )
        except OSError as error:
            raise ValueError(f"{field} must be a regular no-follow file") from error
    finally:
        os.close(directory_descriptor)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise ValueError(f"{field} must be a regular no-follow file")
    return descriptor


def open_repository_anchor(repo_root: Path) -> RepositoryAnchor:
    lexical_path, repository_descriptor = open_anchored_directory(repo_root)
    try:
        plugin_descriptor = open_relative_directory(
            repository_descriptor, ("plugins", "tricritical"), "plugin root"
        )
        try:
            marketplace_descriptor = open_relative_regular_file(
                repository_descriptor,
                (".claude-plugin", "marketplace.json"),
                "Claude marketplace",
            )
        except Exception:
            os.close(plugin_descriptor)
            raise
    except Exception:
        os.close(repository_descriptor)
        raise
    return RepositoryAnchor(
        lexical_path,
        repository_descriptor,
        plugin_descriptor,
        marketplace_descriptor,
    )


def descriptors_match(left_descriptor: int, right_descriptor: int) -> bool:
    left = os.fstat(left_descriptor)
    right = os.fstat(right_descriptor)
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_mode == right.st_mode
    )


def verify_repository_anchor_binding(anchor: RepositoryAnchor) -> None:
    """Reopen lexical bindings without weakening descriptor-anchored reads."""
    _, repository_descriptor = open_anchored_directory(anchor.lexical_path)
    try:
        if not descriptors_match(repository_descriptor, anchor.repository_descriptor):
            raise ValueError(
                "supplied repository path binding changed during validation"
            )
        plugin_descriptor = open_relative_directory(
            repository_descriptor, ("plugins", "tricritical"), "plugin root"
        )
        try:
            if not descriptors_match(plugin_descriptor, anchor.plugin_descriptor):
                raise ValueError("plugin root path binding changed during validation")
        finally:
            os.close(plugin_descriptor)
        marketplace_descriptor = open_relative_regular_file(
            repository_descriptor,
            (".claude-plugin", "marketplace.json"),
            "Claude marketplace",
        )
        try:
            if not descriptors_match(
                marketplace_descriptor, anchor.marketplace_descriptor
            ):
                raise ValueError("marketplace path binding changed during validation")
        finally:
            os.close(marketplace_descriptor)
    finally:
        os.close(repository_descriptor)


def reject_lexical_ancestor_symlinks(path: Path) -> Path:
    """Reject symlinks in every lexical component of the supplied path."""
    absolute = path if path.is_absolute() else Path.cwd() / path
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError(
                f"supplied repository path contains symlink ancestor: {current}"
            )
    return absolute


def locate_roots(repo_root: Path) -> tuple[Path, Path]:
    """Locate the repository and plugin without following in-repository symlinks."""
    repo_root = reject_lexical_ancestor_symlinks(repo_root)
    resolved_repo_root = repo_root.resolve(strict=True)
    if not resolved_repo_root.is_dir():
        raise ValueError("repository root must be a directory")
    plugin_root = repo_root
    for part in ("plugins", "tricritical"):
        plugin_root /= part
        if plugin_root.is_symlink():
            raise ValueError("plugin root path must not contain symlinks")
    resolved_plugin_root = plugin_root.resolve(strict=True)
    if (
        not resolved_plugin_root.is_relative_to(resolved_repo_root)
        or not resolved_plugin_root.is_dir()
    ):
        raise ValueError("plugin root must be a repository directory")
    return resolved_repo_root, resolved_plugin_root


def deterministic_tree_digest(root: Path) -> str:
    """Hash paths, entry types, modes, and file bytes deterministically."""
    digest = hashlib.sha256()
    for path in [root, *sorted(root.rglob("*"), key=lambda item: item.as_posix())]:
        relative = "." if path == root else path.relative_to(root).as_posix()
        metadata = path.lstat()
        if path.is_symlink():
            entry_type = "symlink"
            payload = os.readlink(path).encode()
        elif path.is_dir():
            entry_type = "directory"
            payload = b""
        elif path.is_file():
            entry_type = "file"
            payload = path.read_bytes()
        else:
            entry_type = "special"
            payload = b""
        digest.update(f"{relative}\0{entry_type}\0{metadata.st_mode:o}\0".encode())
        digest.update(payload)
        digest.update(b"\0")
    return digest.hexdigest()


def validation_input_digest(repository_root: Path, plugin_root: Path) -> str:
    digest = hashlib.sha256()
    digest.update(deterministic_tree_digest(plugin_root).encode())
    digest.update(b"\0marketplace\0")
    digest.update(
        read_regular_file(repository_root, ".claude-plugin/marketplace.json").encode()
    )
    return digest.hexdigest()


def tree_observation_digest(root: Path) -> str:
    """Hash live-entry metadata that exposes content-preserving ABA writes."""
    digest = hashlib.sha256()
    for path in [root, *sorted(root.rglob("*"), key=lambda item: item.as_posix())]:
        relative = "." if path == root else path.relative_to(root).as_posix()
        metadata = path.lstat()
        digest.update(
            (
                f"{relative}\0{metadata.st_dev}\0{metadata.st_ino}\0"
                f"{metadata.st_mode:o}\0{metadata.st_size}\0"
                f"{metadata.st_mtime_ns}\0{metadata.st_ctime_ns}\0"
            ).encode()
        )
        if path.is_symlink():
            digest.update(os.readlink(path).encode())
        digest.update(b"\0")
    return digest.hexdigest()


def validation_input_observation_digest(
    repository_root: Path, plugin_root: Path
) -> str:
    """Bind validation content to live metadata for the no-writer gate."""
    marketplace_path = repository_root / ".claude-plugin" / "marketplace.json"
    marketplace_metadata = marketplace_path.lstat()
    digest = hashlib.sha256()
    digest.update(validation_input_digest(repository_root, plugin_root).encode())
    digest.update(b"\0plugin-observation\0")
    digest.update(tree_observation_digest(plugin_root).encode())
    digest.update(b"\0marketplace-observation\0")
    digest.update(
        (
            f"{marketplace_metadata.st_dev}\0{marketplace_metadata.st_ino}\0"
            f"{marketplace_metadata.st_mode:o}\0{marketplace_metadata.st_size}\0"
            f"{marketplace_metadata.st_mtime_ns}\0"
            f"{marketplace_metadata.st_ctime_ns}"
        ).encode()
    )
    return digest.hexdigest()


def read_stable_descriptor_bytes(descriptor: int, expected_metadata=None) -> bytes:
    """Read a bound regular-file descriptor while verifying immutable metadata."""
    before_metadata = os.fstat(descriptor)
    if expected_metadata is not None and metadata_changed(
        expected_metadata, before_metadata
    ):
        raise ValueError("validation source changed while opening a regular file")
    if not stat.S_ISREG(before_metadata.st_mode):
        raise ValueError("validation source must be a regular file")
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks = []
    while chunk := os.read(descriptor, 1024 * 1024):
        chunks.append(chunk)
    after_metadata = os.fstat(descriptor)
    if metadata_changed(before_metadata, after_metadata):
        raise ValueError("validation source changed while reading a regular file")
    return b"".join(chunks)


def open_stable_child_descriptor(
    parent_descriptor: int, name: str, expected_metadata, directory: bool
) -> int:
    flags = directory_open_flags() if directory else regular_file_open_flags()
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as error:
        raise ValueError(f"validation source changed while opening: {name}") from error
    opened_metadata = os.fstat(descriptor)
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(opened_metadata.st_mode) or metadata_changed(
        expected_metadata, opened_metadata
    ):
        os.close(descriptor)
        raise ValueError(f"validation source changed while opening: {name}")
    return descriptor


def copy_regular_tree_from_descriptor(
    source_descriptor: int, destination: Path
) -> None:
    root_metadata = os.fstat(source_descriptor)
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise ValueError("validation plugin source must be a regular directory")
    destination.mkdir(parents=True)
    destination.chmod(stat.S_IMODE(root_metadata.st_mode))

    def copy_directory(directory_descriptor: int, target: Path) -> None:
        before_metadata = os.fstat(directory_descriptor)
        names = sorted(os.listdir(directory_descriptor))
        for name in names:
            metadata = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
            target_path = target / name
            if stat.S_ISDIR(metadata.st_mode):
                child_descriptor = open_stable_child_descriptor(
                    directory_descriptor, name, metadata, directory=True
                )
                try:
                    target_path.mkdir()
                    target_path.chmod(stat.S_IMODE(metadata.st_mode))
                    copy_directory(child_descriptor, target_path)
                finally:
                    os.close(child_descriptor)
            elif stat.S_ISREG(metadata.st_mode):
                child_descriptor = open_stable_child_descriptor(
                    directory_descriptor, name, metadata, directory=False
                )
                try:
                    content = read_stable_descriptor_bytes(child_descriptor, metadata)
                finally:
                    os.close(child_descriptor)
                target_path.write_bytes(content)
                target_path.chmod(stat.S_IMODE(metadata.st_mode))
            elif stat.S_ISLNK(metadata.st_mode):
                raise ValueError(f"validation source must not contain symlinks: {name}")
            else:
                raise ValueError(f"validation source contains a special entry: {name}")
        after_metadata = os.fstat(directory_descriptor)
        if metadata_changed(before_metadata, after_metadata):
            raise ValueError("validation source directory changed while copying")

    copy_directory(source_descriptor, destination)


def descriptor_tree_observation_digest(root_descriptor: int) -> str:
    """Hash bound tree content and metadata without reopening lexical ancestors."""
    digest = hashlib.sha256()

    def observe_directory(directory_descriptor: int, relative_path: str) -> None:
        before_metadata = os.fstat(directory_descriptor)
        digest.update(
            (
                f"{relative_path}\0directory\0{before_metadata.st_dev}\0"
                f"{before_metadata.st_ino}\0{before_metadata.st_mode:o}\0"
                f"{before_metadata.st_size}\0{before_metadata.st_mtime_ns}\0"
                f"{before_metadata.st_ctime_ns}\0"
            ).encode()
        )
        for name in sorted(os.listdir(directory_descriptor)):
            metadata = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
            child_relative_path = (
                name if relative_path == "." else f"{relative_path}/{name}"
            )
            if stat.S_ISDIR(metadata.st_mode):
                child_descriptor = open_stable_child_descriptor(
                    directory_descriptor, name, metadata, directory=True
                )
                try:
                    observe_directory(child_descriptor, child_relative_path)
                finally:
                    os.close(child_descriptor)
            elif stat.S_ISREG(metadata.st_mode):
                child_descriptor = open_stable_child_descriptor(
                    directory_descriptor, name, metadata, directory=False
                )
                try:
                    content = read_stable_descriptor_bytes(child_descriptor, metadata)
                finally:
                    os.close(child_descriptor)
                digest.update(
                    (
                        f"{child_relative_path}\0file\0{metadata.st_dev}\0"
                        f"{metadata.st_ino}\0{metadata.st_mode:o}\0"
                        f"{metadata.st_size}\0{metadata.st_mtime_ns}\0"
                        f"{metadata.st_ctime_ns}\0"
                    ).encode()
                )
                digest.update(content)
                digest.update(b"\0")
            elif stat.S_ISLNK(metadata.st_mode):
                raise ValueError(
                    "validation source must not contain symlinks: "
                    f"{child_relative_path}"
                )
            else:
                raise ValueError(
                    f"validation source contains a special entry: {child_relative_path}"
                )
        after_metadata = os.fstat(directory_descriptor)
        if metadata_changed(before_metadata, after_metadata):
            raise ValueError("validation source directory changed while observing")

    observe_directory(root_descriptor, ".")
    return digest.hexdigest()


def anchored_validation_input_observation_digest(
    anchor: RepositoryAnchor,
) -> str:
    marketplace_metadata = os.fstat(anchor.marketplace_descriptor)
    marketplace_content = read_stable_descriptor_bytes(
        anchor.marketplace_descriptor, marketplace_metadata
    )
    digest = hashlib.sha256()
    digest.update(descriptor_tree_observation_digest(anchor.plugin_descriptor).encode())
    digest.update(b"\0marketplace\0")
    digest.update(
        (
            f"{marketplace_metadata.st_dev}\0{marketplace_metadata.st_ino}\0"
            f"{marketplace_metadata.st_mode:o}\0{marketplace_metadata.st_size}\0"
            f"{marketplace_metadata.st_mtime_ns}\0"
            f"{marketplace_metadata.st_ctime_ns}\0"
        ).encode()
    )
    digest.update(marketplace_content)
    return digest.hexdigest()


def create_private_validation_snapshot_from_anchor(
    anchor: RepositoryAnchor, snapshot_repository_root: Path
) -> None:
    """Copy bound validator inputs into a private temporary root."""
    snapshot_repository_root.mkdir()
    copy_regular_tree_from_descriptor(
        anchor.plugin_descriptor,
        snapshot_repository_root / "plugins" / "tricritical",
    )
    marketplace_metadata = os.fstat(anchor.marketplace_descriptor)
    marketplace_content = read_stable_descriptor_bytes(
        anchor.marketplace_descriptor, marketplace_metadata
    )
    marketplace_path = snapshot_repository_root / ".claude-plugin" / "marketplace.json"
    marketplace_path.parent.mkdir(parents=True)
    marketplace_path.write_bytes(marketplace_content)
    marketplace_path.chmod(stat.S_IMODE(marketplace_metadata.st_mode))


def read_regular_bytes(root: Path, relative_path: str | Path) -> bytes:
    """Read a regular file without following symlinked path components."""
    if root.is_symlink():
        raise ValueError("content root must not be a symlink")
    root = root.resolve(strict=True)
    relative_path = Path(relative_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"path escapes content root: {relative_path}")
    current = root
    for part in relative_path.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"path must not contain symlinks: {relative_path}")
    resolved = (root / relative_path).resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError(f"path escapes content root: {relative_path}")
    return resolved.read_bytes()


def read_regular_file(root: Path, relative_path: str | Path) -> str:
    return read_regular_bytes(root, relative_path).decode("utf-8")


def load_json_document(root: Path, relative_path: str, field: str):
    """Load strict JSON, rejecting duplicate keys and non-finite numbers."""

    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"{field} contains duplicate key: {key}")
            value[key] = item
        return value

    def reject_constant(value: str):
        raise ValueError(f"{field} contains non-finite JSON value: {value}")

    return json.loads(
        read_regular_file(root, relative_path),
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
    )


def regular_directory(root: Path, relative_dir: str | Path) -> Path:
    """Locate a directory without following symlinked path components."""
    relative_dir = Path(relative_dir)
    if relative_dir.is_absolute() or ".." in relative_dir.parts:
        raise ValueError(f"directory escapes content root: {relative_dir}")
    current = root.resolve(strict=True)
    for part in relative_dir.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(
                f"directory path must not contain symlinks: {relative_dir}"
            )
    resolved = current.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve(strict=True)) or not resolved.is_dir():
        raise ValueError(f"missing regular directory: {relative_dir}")
    return resolved


def file_inventory(root: Path, relative_dir: str) -> set[str]:
    directory = regular_directory(root, relative_dir)
    inventory = set()
    for path in directory.rglob("*"):
        if path.is_symlink() or (not path.is_dir() and not path.is_file()):
            raise ValueError(f"invalid plugin entry: {path.relative_to(root)}")
        if path.is_file():
            inventory.add(path.relative_to(directory).as_posix())
    return inventory


def load_manifests(root: Path) -> tuple[dict, dict]:
    codex = require_mapping(load_agent_plugin_manifest(root), "Agent Plugin manifest")
    claude = require_mapping(
        load_json_document(root, ".claude-plugin/plugin.json", "Claude manifest"),
        "Claude manifest",
    )
    return codex, claude


def validate_marketplace(repo_root: Path) -> None:
    marketplace = require_mapping(
        load_json_document(
            repo_root, ".claude-plugin/marketplace.json", "Claude marketplace"
        ),
        "Claude marketplace",
    )
    require_exact_keys(
        marketplace, {"name", "owner", "description", "plugins"}, "Claude marketplace"
    )
    require_string(marketplace["name"], "Claude marketplace name")
    require_string(marketplace["description"], "Claude marketplace description")
    owner = require_mapping(marketplace["owner"], "Claude marketplace owner")
    require_exact_keys(owner, {"name"}, "Claude marketplace owner")
    require_string(owner["name"], "Claude marketplace owner name")
    plugins = marketplace["plugins"]
    if not isinstance(plugins, list):
        raise ValueError("Claude marketplace plugins must be a list")
    entries = [
        entry
        for entry in plugins
        if isinstance(entry, dict) and entry.get("name") == "tricritical"
    ]
    if entries != [CLAUDE_MARKETPLACE_ENTRY]:
        fail("Claude marketplace must contain the exact Tricritical local entry")


def validate_manifests(root: Path) -> dict:
    codex, claude = load_manifests(root)
    common_keys = {
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
    }
    require_exact_keys(claude, common_keys | {"displayName"}, "Claude manifest")
    require_exact_keys(
        codex,
        common_keys | {"$schema", "extensions"},
        "Agent Plugin manifest",
    )
    for label, manifest in (("Claude", claude), ("Agent Plugin", codex)):
        for field in (
            "name",
            "version",
            "description",
            "homepage",
            "repository",
            "license",
        ):
            require_string(manifest[field], f"{label} manifest {field}")
        author = require_mapping(manifest["author"], f"{label} manifest author")
        require_exact_keys(author, {"name", "url"}, f"{label} manifest author")
        require_string(author["name"], f"{label} manifest author name")
        require_string(author["url"], f"{label} manifest author URL")
        require_string_list(manifest["keywords"], f"{label} manifest keywords")
    require_string(claude["displayName"], "Claude manifest displayName")
    extensions = require_mapping(codex["extensions"], "Agent Plugin extensions")
    require_exact_keys(extensions, {"com.openai"}, "Agent Plugin extensions")
    openai = require_mapping(extensions["com.openai"], "Codex extension")
    require_exact_keys(openai, {"interface"}, "Codex extension")
    interface = require_mapping(openai["interface"], "Codex manifest interface")
    require_exact_keys(
        interface,
        {
            "displayName",
            "shortDescription",
            "longDescription",
            "developerName",
            "category",
            "capabilities",
            "websiteURL",
            "defaultPrompt",
        },
        "Codex manifest interface",
    )
    for field in (
        "displayName",
        "shortDescription",
        "longDescription",
        "developerName",
        "category",
        "websiteURL",
    ):
        require_string(interface[field], f"Codex manifest interface {field}")
    require_string_list(interface["capabilities"], "Codex manifest capabilities")
    require_string_list(interface["defaultPrompt"], "Codex manifest defaultPrompt")
    for field in common_keys:
        if claude[field] != codex[field]:
            fail(f"Claude manifest projection drift: {field}")
    version_pattern = re.compile(rf"{SEMVER}$")
    if version_pattern.fullmatch(codex["version"]) is None:
        fail("canonical manifest version is invalid")
    if codex.get("name") != "tricritical" or claude.get("name") != "tricritical":
        fail("manifest names must be tricritical")
    if "skills" in claude or "agents" in claude:
        fail("Claude manifest must use native discovery")
    discovered = discover_direct_skills(root)
    if discovered != tuple(sorted(CORE_SKILLS)):
        fail("Agent Plugins direct-child skill inventory drift")
    changelog = read_regular_file(root, "CHANGELOG.md")
    headings = re.findall(r"^##\s+(.+?)\s*$", changelog, re.M)
    if not headings or headings[0] != codex["version"]:
        fail("manifest versions do not match the latest changelog release")
    return codex


def authored_skill_files(skill: str) -> set[str]:
    files = {"SKILL.md", "agents/openai.yaml"}
    if skill == "review":
        files.add("references/completeness-and-synthesis.md")
    if skill in {"intent", "runtime", "structure"}:
        files.add("references/rubric.md")
    if skill == "adjudicate":
        files.add("references/dispositions.md")
    if skill == "loop":
        files.update(
            {
                "references/operator-choice.md",
                "references/review-evidence.md",
                "scripts/review_evidence.py",
            }
        )
    return files


def expected_skill_files(skill: str) -> set[str]:
    return authored_skill_files(skill) | set(skill_local_projection_sources(skill))


def skill_local_projection_sources(skill: str) -> dict[str, str]:
    """Return generated skill-local path to canonical package source mappings."""
    if skill not in CORE_SKILLS:
        raise ValueError(f"unknown Tricritical skill: {skill}")
    projections = {
        "references/invocation-boundary.md": SHARED_INVOCATION_BOUNDARY_PATH,
        "references/review-input-boundary.md": SHARED_INPUT_BOUNDARY_PATH,
        "references/topology.json": "topology.json",
    }
    if skill in REVIEW_OUTPUT_SKILLS:
        projections["references/review-output-contract.md"] = (
            SHARED_OUTPUT_CONTRACT_PATH
        )
    return dict(sorted(projections.items()))


def validate_skill_local_projections(root: Path) -> None:
    """Require generated installed-skill resources to equal canonical sources."""
    for skill in CORE_SKILLS:
        for local_path, source_path in skill_local_projection_sources(skill).items():
            projection_path = f"skills/{skill}/{local_path}"
            try:
                projection = read_regular_bytes(root, projection_path)
            except (FileNotFoundError, ValueError) as error:
                fail(f"skill-local projection is missing: {projection_path}")
                raise AssertionError("unreachable") from error
            if projection != read_regular_bytes(root, source_path):
                fail(
                    "skill-local projection drift: "
                    f"{projection_path} differs from {source_path}"
                )


def expected_plugin_tree() -> tuple[set[str], set[str]]:
    files = {
        ".claude-plugin/plugin.json",
        "CHANGELOG.md",
        "LICENSE",
        "NOTICE",
        "README.md",
        "plugin.json",
        TASK_WITNESS_PROVIDER,
        CONTENT_LOCK_PATH,
        "topology.json",
        SHARED_INPUT_BOUNDARY_PATH,
        SHARED_OUTPUT_CONTRACT_PATH,
        SHARED_INVOCATION_BOUNDARY_PATH,
        "evals/README.md",
        "evals/corpus.json",
        *(f"agents/{persona}.md" for persona in PERSONA_SKILLS),
        *(f"evals/fixtures/{fixture}" for fixture in EVAL_FIXTURES),
    }
    directories = {
        ".claude-plugin",
        "agents",
        "evals",
        "evals/fixtures",
        "references",
        "skills",
    }
    for skill in CORE_SKILLS:
        directories.update({f"skills/{skill}", f"skills/{skill}/agents"})
        for relative_path in expected_skill_files(skill):
            files.add(f"skills/{skill}/{relative_path}")
        if any(path.startswith("references/") for path in expected_skill_files(skill)):
            directories.add(f"skills/{skill}/references")
        if any(path.startswith("scripts/") for path in expected_skill_files(skill)):
            directories.add(f"skills/{skill}/scripts")
    return files, directories


def validate_inventory(root: Path) -> None:
    files = set()
    directories = set()
    for path in root.rglob("*"):
        relative_path = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError(f"plugin tree must not contain symlinks: {relative_path}")
        if path.is_dir():
            directories.add(relative_path)
        elif path.is_file():
            files.add(relative_path)
        else:
            raise ValueError(f"plugin tree contains a special entry: {relative_path}")
    expected_files, expected_directories = expected_plugin_tree()
    if files != expected_files or directories != expected_directories:
        fail("component inventory differs from the supported surface")


def load_yaml_document(content: str, field: str) -> dict:
    if yaml is None:
        raise ValueError("PyYAML is required to validate skill metadata")

    class UniqueKeyLoader(yaml.SafeLoader):
        pass

    def construct_unique_mapping(loader, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in mapping:
                raise ValueError(f"{field} contains duplicate key: {key}")
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    UniqueKeyLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_unique_mapping
    )
    documents = list(yaml.load_all(content, Loader=UniqueKeyLoader))
    if len(documents) != 1:
        raise ValueError(f"{field} must contain exactly one YAML document")
    return require_mapping(documents[0], field)


def load_default_prompts(root: Path) -> dict[str, str]:
    prompts = {}
    for skill in CORE_SKILLS:
        field = f"{skill} skill metadata"
        document = load_yaml_document(
            read_regular_file(root, f"skills/{skill}/agents/openai.yaml"), field
        )
        require_exact_keys(document, {"interface"}, field)
        interface = require_mapping(document["interface"], f"{field} interface")
        require_exact_keys(
            interface,
            {"display_name", "short_description", "default_prompt"},
            f"{field} interface",
        )
        require_string(interface["display_name"], f"{field} display_name")
        require_string(interface["short_description"], f"{field} short_description")
        prompt = require_string(interface["default_prompt"], f"{field} default_prompt")
        if re.findall(r"\$[A-Za-z0-9:-]+", prompt) != [f"$tricritical:{skill}"]:
            fail(f"{field} default_prompt must target exactly $tricritical:{skill}")
        prompts[skill] = prompt
    return prompts


def validate_prompt_pairing(root: Path, codex_manifest: dict) -> None:
    prompts = load_default_prompts(root)
    manifest_prompts = (
        codex_manifest.get("extensions", {})
        .get("com.openai", {})
        .get("interface", {})
        .get("defaultPrompt")
    )
    if not isinstance(manifest_prompts, list):
        fail("Codex manifest has no default prompts")
    expected = [
        prompts["review"],
        prompts["adjudicate"],
        CODEX_LOOP_DISCOVERY_PROMPT,
    ]
    if manifest_prompts != expected:
        fail("Codex default prompts differ from per-skill interface prompts")


def validate_public_skill_boundaries(root: Path, skill: str) -> None:
    content = read_regular_file(root, f"skills/{skill}/SKILL.md")
    if content.count(SHARED_INPUT_BOUNDARY_LINK) != 1:
        fail(f"{skill} must load the shared review-input boundary exactly once")
    if content.count(SHARED_INVOCATION_BOUNDARY_LINK) != 1:
        fail(f"{skill} must load the shared invocation boundary exactly once")
    expected_output_links = 1 if skill in REVIEW_OUTPUT_SKILLS else 0
    if content.count(SHARED_OUTPUT_CONTRACT_LINK) != expected_output_links:
        fail(f"{skill} does not preserve the shared review-output contract link")
    if content.count(SKILL_LOCAL_TOPOLOGY_LINK) != 1:
        fail(f"{skill} must load the skill-local topology projection exactly once")
    bundle = resolve_candidate_skill_bundle(root, skill)
    prefix = f"skills/{skill}/references"
    if (
        f"{prefix}/invocation-boundary.md" not in bundle
        or f"{prefix}/topology.json" not in bundle
    ):
        fail(f"{skill} bundle omits invocation policy or graph authority")


def validate_input_boundaries(root: Path) -> None:
    for skill in CORE_SKILLS:
        validate_public_skill_boundaries(root, skill)
    review = read_regular_file(root, "skills/review/SKILL.md")
    if review.count(REVIEW_COMPLETENESS_LINK) != 1:
        fail("review must load completeness and synthesis rules exactly once")
    loop = read_regular_file(root, "skills/loop/SKILL.md")
    if loop.count(LOOP_OPERATOR_CHOICE_LINK) != 1:
        fail("loop must load the portable operator-choice contract exactly once")


def expected_agent_content(persona: str, skill: str) -> str:
    instruction = f"Use `$tricritical:{skill}` for the supplied task."
    if skill == LOOP_SKILL:
        instruction = f"{instruction} {CLAUDE_OPERATOR_CHOICE_MAPPING}"
    return (
        "---\n"
        f"name: {persona}\n"
        f"description: Forward the supplied task to Tricritical's {skill} skill.\n"
        "---\n\n"
        f"{instruction}\n"
    )


def semantic_skill_paths() -> tuple[str, ...]:
    paths = {
        SHARED_INPUT_BOUNDARY_PATH,
        SHARED_OUTPUT_CONTRACT_PATH,
        SHARED_INVOCATION_BOUNDARY_PATH,
    }
    for skill in CORE_SKILLS:
        for relative_path in expected_skill_files(skill):
            if relative_path.endswith(".md") or relative_path in (
                "references/topology.json",
            ):
                paths.add(f"skills/{skill}/{relative_path}")
    return tuple(sorted(paths))


def authored_semantic_skill_paths() -> tuple[str, ...]:
    """Return authored semantic sources, excluding generated projections."""
    paths = {
        SHARED_INPUT_BOUNDARY_PATH,
        SHARED_OUTPUT_CONTRACT_PATH,
        SHARED_INVOCATION_BOUNDARY_PATH,
    }
    for skill in CORE_SKILLS:
        for relative_path in authored_skill_files(skill):
            if relative_path.endswith(".md"):
                paths.add(f"skills/{skill}/{relative_path}")
    return tuple(sorted(paths))


def semantic_release_paths() -> tuple[str, ...]:
    """Return the authoritative portable semantics and behavior corpus."""
    paths = set(semantic_skill_paths())
    paths.update(
        {
            "topology.json",
            TASK_WITNESS_PROVIDER,
            TERMINAL_EVIDENCE_RUNTIME,
            "evals/README.md",
            "evals/corpus.json",
            *(f"evals/fixtures/{fixture}" for fixture in EVAL_FIXTURES),
        }
    )
    return tuple(sorted(paths))


def content_lock_document(root: Path) -> dict:
    return {
        "schema_version": 1,
        "algorithm": "sha256",
        "files": {
            relative_path: hashlib.sha256(
                read_regular_file(root, relative_path).encode()
            ).hexdigest()
            for relative_path in semantic_release_paths()
        },
    }


def validate_content_lock(root: Path) -> None:
    lock = require_mapping(
        load_json_document(root, CONTENT_LOCK_PATH, "semantic content lock"),
        "semantic content lock",
    )
    require_exact_keys(
        lock,
        {"schema_version", "algorithm", "files"},
        "semantic content lock",
    )
    if type(lock["schema_version"]) is not int or lock["schema_version"] != 1:
        raise ValueError("semantic content lock schema_version must be integer 1")
    if lock["algorithm"] != "sha256":
        raise ValueError("semantic content lock algorithm must be sha256")
    files = require_mapping(lock["files"], "semantic content lock files")
    if set(files) != set(semantic_release_paths()):
        fail("semantic content lock inventory differs from authoritative inputs")
    if any(
        not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        for digest in files.values()
    ):
        fail("semantic content lock contains an invalid digest")
    if lock != content_lock_document(root):
        fail("semantic content lock mismatch")


def validate_portability(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in PORTABILITY_PATTERNS:
            if pattern.search(content):
                fail(f"portability leak in {relative}: {pattern.pattern}")


def validate_adapters(root: Path) -> None:
    for persona, skill in PERSONA_SKILLS.items():
        try:
            content = read_regular_file(root, f"agents/{persona}.md")
        except (FileNotFoundError, ValueError):
            fail("component inventory differs from the supported surface")
        if content != expected_agent_content(persona, skill):
            fail("adapter contract must be an exact minimal one-skill forwarder")
    for relative_path in authored_semantic_skill_paths():
        content = read_regular_file(root, relative_path)
        if "$tricritical:" in content:
            fail("semantic skill files must use unqualified portable edges")
        if any(token in content for token in HARNESS_API_TOKENS):
            fail("semantic skill files must not name harness-specific APIs")

    review_adapter = read_regular_file(root, "skills/review/agents/openai.yaml")
    if (
        "separate capability-proven model-selection and Rolecasting "
        "invocation-topology receipts" not in review_adapter
    ):
        fail("review adapter must supply separate model and topology receipts")

    codex_adapter_path = f"skills/{LOOP_SKILL}/agents/openai.yaml"
    codex_adapter = read_regular_file(root, codex_adapter_path)
    claude_adapter_path = "agents/fathomkeeper.md"
    claude_adapter = read_regular_file(root, claude_adapter_path)
    if (
        codex_adapter.count(CODEX_OPERATOR_CHOICE_MAPPING) != 1
        or claude_adapter.count(CLAUDE_OPERATOR_CHOICE_MAPPING) != 1
    ):
        fail("operator-choice API mappings must remain exact adapter policy")

    allowed_api_paths = {
        "request_user_input": {codex_adapter_path},
        "AskUserQuestion": {claude_adapter_path},
    }
    expected_files, _ = expected_plugin_tree()
    generated_projections = {
        f"skills/{skill}/{local_path}"
        for skill in CORE_SKILLS
        for local_path in skill_local_projection_sources(skill)
    }
    for relative_path in expected_files - generated_projections:
        content = read_regular_file(root, relative_path)
        for token, allowed_paths in allowed_api_paths.items():
            if token in content and relative_path not in allowed_paths:
                fail("harness-specific API token escaped its adapter surface")


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def validate_task_witness_provider(root: Path) -> None:
    runtime_raw = read_regular_bytes(root, TERMINAL_EVIDENCE_RUNTIME)
    runtime_sha256 = hashlib.sha256(runtime_raw).hexdigest()
    modules = [
        {
            "name": "validator",
            "relative_path": TERMINAL_EVIDENCE_RUNTIME,
            "length": len(runtime_raw),
            "sha256": runtime_sha256,
        }
    ]
    implementation = hashlib.sha256(
        canonical_bytes(
            {
                "contract": VALIDATOR_MANIFEST_CONTRACT,
                "validator_contract": TERMINAL_EVIDENCE_CONTRACT,
                "entrypoint_module": "validator",
                "modules": [{"name": "validator", "content_sha256": runtime_sha256}],
            }
        )
    ).hexdigest()
    unsigned = {
        "schema_version": 1,
        "contract": PROVIDER_CONTRACT,
        "plugin_id": "tricritical",
        "publisher": "nisavid",
        "repository": "https://github.com/nisavid/agents",
        "authority_profile": TRICRITICAL_AUTHORITY_PROFILE,
        "producers": [],
        "issuers": [],
        "validators": [
            {
                "validator_id": TERMINAL_EVIDENCE_VALIDATOR_ID,
                "contract": TERMINAL_EVIDENCE_CONTRACT,
                "implementation_sha256": implementation,
                "entrypoint": "validator",
                "modules": modules,
                "lifecycle": {
                    "state": "active",
                    "usable_for_new_publication": True,
                },
            }
        ],
    }
    provider = load_json_document(root, TASK_WITNESS_PROVIDER, "Task Witness provider")
    if (
        read_regular_bytes(root, TASK_WITNESS_PROVIDER)
        != canonical_bytes(provider) + b"\n"
    ):
        fail("Task Witness provider must be canonical JSON with one trailing LF")
    if provider.get("authority_profile") != TRICRITICAL_AUTHORITY_PROFILE:
        fail("provider authority profile drift")
    expected = {
        **unsigned,
        "content_sha256": hashlib.sha256(canonical_bytes(unsigned)).hexdigest(),
    }
    if provider != expected:
        fail("Task Witness provider declaration drift")
    source = runtime_raw.decode("utf-8")
    for forbidden in (
        "--trust-context",
        "--task-witness-runtime",
        "argparse",
        "importlib",
        "def validate_bundle(",
    ):
        if forbidden in source:
            fail(
                "terminal-evidence validator exposes forbidden production seam: "
                f"{forbidden}"
            )
    if (
        f'BUNDLE_CONTRACT = "{TERMINAL_EVIDENCE_CONTRACT}"' not in source
        or f'PROJECTION_CONTRACT = "{TERMINAL_PROJECTION_CONTRACT}"' not in source
        or f'ROLECASTING_EVIDENCE_CONTRACT = "{ROLECASTING_EVIDENCE_CONTRACT}"'
        not in source
        or f'ROLECASTING_PROJECTION_CONTRACT = "{ROLECASTING_PROJECTION_CONTRACT}"'
        not in source
        or "def _validate_bundle(" not in source
        or "invoke_registered_validator" not in source
    ):
        fail("terminal-evidence registered API drift")
    reference = " ".join(read_regular_file(root, TERMINAL_EVIDENCE_REFERENCE).split())
    for term in (
        TERMINAL_EVIDENCE_CONTRACT,
        TERMINAL_EVIDENCE_VALIDATOR_ID,
        TERMINAL_PROJECTION_CONTRACT,
        ROLECASTING_PROJECTION_CONTRACT,
        "preserves the complete registered Rolecasting projection verbatim as "
        "`final_dispatch`",
        "does not upgrade or collapse `product-attested`, "
        "`controller-observed`, or `self-reported` assurance",
        "consumer must apply its own minimum",
        "Self-reported evidence is diagnostic and cannot by itself satisfy a hard gate",
        "producer and issuer inventories are empty",
        "fixture/bootstrap bundles",
        "does not expose a new-publication producer chain",
        "does not claim canonical end-to-end reachability",
        "Model and reasoning-effort policy belongs to Rolecasting",
    ):
        if term not in reference:
            fail(f"terminal-evidence reference drift: {term}")


AUTHORITY_ROLES = {"coordinator", "critic", "adjudicator", "reviser", "loop"}


def validate_authority_topology_node(skill: str, node, skills: dict) -> None:
    node = require_mapping(node, f"authority topology {skill}")
    require_exact_keys(
        node,
        {
            "role",
            "mutates_directly",
            "can_cause_mutation",
            "requires_original_mutation_authority",
            "repeats",
            "requires",
            "calls",
        },
        f"authority topology {skill}",
    )
    if node["role"] not in AUTHORITY_ROLES:
        raise ValueError(f"authority topology {skill} has invalid role")
    if any(
        type(node[field]) is not bool
        for field in (
            "mutates_directly",
            "can_cause_mutation",
            "requires_original_mutation_authority",
            "repeats",
        )
    ):
        raise ValueError(f"authority topology {skill} flags must be booleans")
    calls = require_string_list(node["calls"], f"authority topology {skill} calls")
    requirements = require_string_list(
        node["requires"], f"authority topology {skill} requirements"
    )
    if len(calls) != len(set(calls)) or any(call not in skills for call in calls):
        raise ValueError(f"authority topology {skill} calls are invalid")
    if len(requirements) != len(set(requirements)):
        raise ValueError(f"authority topology {skill} requirements are invalid")


def authority_role_members(skills: dict) -> dict[str, list[str]]:
    return {
        role: [skill for skill, node in skills.items() if node["role"] == role]
        for role in AUTHORITY_ROLES
    }


def validate_authority_role_cardinality(role_members: dict[str, list[str]]) -> None:
    if (
        len(role_members["coordinator"]) != 1
        or len(role_members["critic"]) != 3
        or len(role_members["adjudicator"]) != 1
        or role_members["reviser"] != [MUTATOR_SKILL]
        or role_members["loop"] != [LOOP_SKILL]
    ):
        fail("authority topology role cardinality is invalid")


def validate_authority_edges(skills: dict, role_members: dict[str, list[str]]) -> None:
    coordinator = role_members["coordinator"][0]
    if set(skills[coordinator]["calls"]) != set(role_members["critic"]):
        fail("authority topology coordinator must call exactly the critics")
    expected_loop_calls = {
        coordinator,
        role_members["adjudicator"][0],
        role_members["reviser"][0],
    }
    if set(skills[LOOP_SKILL]["calls"]) != expected_loop_calls:
        fail("authority topology loop edges are invalid")
    for role in ("critic", "adjudicator", "reviser"):
        if any(skills[skill]["calls"] for skill in role_members[role]):
            fail(f"authority topology {role} skills must have no outgoing calls")
    if any(LOOP_SKILL in node["calls"] for node in skills.values()):
        fail("authority topology must not contain nested loop edges")
    for skill, node in skills.items():
        expected_requirements = (
            [MODEL_SELECTION_REQUIREMENT, INVOCATION_TOPOLOGY_REQUIREMENT]
            if skill == coordinator
            else []
        )
        if node["requires"] != expected_requirements:
            fail("authority topology adapter requirements are invalid")


def validate_mutation_reachability(skills: dict) -> None:
    direct_mutators = {
        skill for skill, node in skills.items() if node["mutates_directly"]
    }
    if direct_mutators != {MUTATOR_SKILL}:
        fail("authority topology must declare one approved direct mutator")

    def reaches_direct_mutator(skill: str, seen: set[str]) -> bool:
        if skill in direct_mutators:
            return True
        if skill in seen:
            return False
        return any(
            reaches_direct_mutator(target, seen | {skill})
            for target in skills[skill]["calls"]
        )

    for skill, node in skills.items():
        derived = reaches_direct_mutator(skill, set())
        if node["can_cause_mutation"] != derived:
            fail(f"authority topology mutation reachability drift: {skill}")
        if node["requires_original_mutation_authority"] != derived:
            fail(f"authority topology original mutation authority drift: {skill}")


def load_authority_topology(root: Path) -> dict:
    topology = require_mapping(
        load_json_document(root, "topology.json", "authority topology"),
        "authority topology",
    )
    require_exact_keys(topology, {"schema_version", "skills"}, "authority topology")
    if type(topology["schema_version"]) is not int or topology["schema_version"] != 2:
        raise ValueError("authority topology schema_version must be integer 2")
    skills = require_mapping(topology["skills"], "authority topology skills")
    if set(skills) != set(CORE_SKILLS):
        fail("authority topology skill inventory differs from the public surface")
    for skill, node in skills.items():
        validate_authority_topology_node(skill, node, skills)
    repeaters = [skill for skill, node in skills.items() if node["repeats"]]
    if repeaters != [LOOP_SKILL]:
        fail("authority topology must declare one approved repeater")
    role_members = authority_role_members(skills)
    validate_authority_role_cardinality(role_members)
    validate_authority_edges(skills, role_members)
    validate_mutation_reachability(skills)
    return topology


def validate_authority_topology(root: Path, topology: dict) -> None:
    skills = topology["skills"]
    contents = {
        skill: read_regular_file(root, f"skills/{skill}/SKILL.md")
        for skill in CORE_SKILLS
    }
    for skill, content in contents.items():
        if "$tricritical:" in content:
            fail(f"{skill} semantic body contains Claude-qualified skill syntax")
        linked_edges = re.findall(r"\[([a-z-]+)\]\(\.\./([a-z-]+)/SKILL\.md\)", content)
        if any(
            label != target or target not in CORE_SKILLS
            for label, target in linked_edges
        ):
            fail(f"{skill} contains an invalid portable skill edge")
        derived_calls = [target for _, target in linked_edges]
        if derived_calls != skills[skill]["calls"]:
            fail(f"{skill} executable call edges differ from topology.json")
    mutation_owners = [
        skill
        for skill, content in contents.items()
        if "## Mutation Authority" in content
    ]
    sole_mutator_claims = [
        skill
        for skill, content in contents.items()
        if "sole mutator" in content.lower()
    ]
    if mutation_owners != [MUTATOR_SKILL] or sole_mutator_claims != [MUTATOR_SKILL]:
        fail("executable skill bodies must project the topology mutator")
    review = contents["review"]
    invocation_boundary = read_regular_file(root, SHARED_INVOCATION_BOUNDARY_PATH)
    if (
        MODEL_SELECTION_REQUIREMENT not in review
        or MODEL_SELECTION_REQUIREMENT not in invocation_boundary
        or INVOCATION_TOPOLOGY_REQUIREMENT not in review
        or INVOCATION_TOPOLOGY_REQUIREMENT not in invocation_boundary
        or "does not select a provider-specific model" not in review
        or "not public\nskill calls" not in invocation_boundary
    ):
        fail("model-selection receipt contract drift")
    combined_invocation_contract = " ".join(
        (review + "\n" + invocation_boundary).split()
    )
    for term in (
        "Never fold it into a model-selection receipt",
        "candidate, review-input, and requirements identities",
        "closed-world dispatch set",
        "exactly one unique dispatch entry",
        "target product family, surface, version, and executor",
        "child, peer, or external relationship",
        "leader-owned or user-owned ownership",
        "transport",
        "return and verification contract",
        "stop conditions",
        "distinct isolation",
        "read-only authority",
        "consumer assurance minimum",
        "product-attested, controller-observed, or self-reported assurance",
        "default-denied subdelegation and external action",
        "explicit user authority",
        "new valid plan",
        "incomplete / non-clean",
    ):
        if term not in combined_invocation_contract:
            fail(f"invocation topology receipt contract drift: {term}")

    readme = read_regular_file(root, "README.md")
    row_matches = list(
        re.finditer(
            r"^\|\s*`(?P<skill>[a-z-]+)`\s*\|(?P<persona>[^|]+)\|(?P<role>[^|]+)\|"
            r"(?P<mutates_directly>[^|]+)\|(?P<can_cause_mutation>[^|]+)\|"
            r"(?P<requires_original_mutation_authority>[^|]+)\|"
            r"(?P<repeats>[^|]+)\|(?P<calls>[^|]+)\|$",
            readme,
            re.M,
        )
    )
    if len(row_matches) != len(skills):
        fail("README topology table differs from topology.json")
    rows = {}
    for match in row_matches:
        skill = match.group("skill")
        if skill in rows:
            fail("README topology table differs from topology.json")
        rows[skill] = {
            "persona": match.group("persona").strip(),
            "role": match.group("role").strip(),
            "mutates_directly": match.group("mutates_directly").strip(),
            "can_cause_mutation": match.group("can_cause_mutation").strip(),
            "requires_original_mutation_authority": match.group(
                "requires_original_mutation_authority"
            ).strip(),
            "repeats": match.group("repeats").strip(),
            "calls": match.group("calls").strip(),
        }
    persona_by_skill = {
        skill: persona.title() for persona, skill in PERSONA_SKILLS.items()
    }
    expected_rows = {
        skill: {
            "persona": persona_by_skill.get(skill, "—"),
            "role": node["role"],
            "mutates_directly": "yes" if node["mutates_directly"] else "no",
            "can_cause_mutation": "yes" if node["can_cause_mutation"] else "no",
            "requires_original_mutation_authority": (
                "yes" if node["requires_original_mutation_authority"] else "no"
            ),
            "repeats": "yes" if node["repeats"] else "no",
            "calls": ", ".join(node["calls"]) or "—",
        }
        for skill, node in skills.items()
    }
    if rows != expected_rows:
        fail("README topology table differs from topology.json")


def markdown_prose_without_code(content: str) -> str:
    """Remove fenced and inline code before resolving Markdown references."""
    prose_lines = []
    fence_character = None
    fence_length = 0
    for line in content.splitlines():
        fence_match = re.match(r"^[ \t]{0,3}(`{3,}|~{3,})", line)
        if fence_character is not None:
            if (
                fence_match
                and fence_match.group(1)[0] == fence_character
                and len(fence_match.group(1)) >= fence_length
            ):
                fence_character = None
                fence_length = 0
            continue
        if fence_match:
            fence_character = fence_match.group(1)[0]
            fence_length = len(fence_match.group(1))
            continue
        prose_lines.append(re.sub(r"`+[^`\n]*?`+", "", line))
    return "\n".join(prose_lines)


def normalize_reference_label(label: str) -> str:
    return re.sub(r"\s+", " ", label.strip()).casefold()


def source_set_targets(value: str) -> tuple[str, ...]:
    targets = []
    for candidate in value.split(","):
        target = candidate.strip().split(maxsplit=1)[0] if candidate.strip() else ""
        if target:
            targets.append(target)
    return tuple(targets)


class MarkdownHTMLResourceParser(HTMLParser):
    """Collect resource-bearing HTML attributes embedded in Markdown."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.targets: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        del tag
        for raw_name, value in attrs:
            name = raw_name.casefold()
            if name in HTML_UNSUPPORTED_RESOURCE_ATTRIBUTES:
                raise ValueError(
                    f"candidate skill bundle has unsupported HTML resource: {name}"
                )
            if value is None:
                continue
            if name in HTML_SOURCE_SET_ATTRIBUTES:
                self.targets.extend(source_set_targets(value))
            elif name in HTML_SPACE_SEPARATED_RESOURCE_ATTRIBUTES:
                self.targets.extend(value.split())
            elif name in HTML_RESOURCE_ATTRIBUTES:
                self.targets.append(value)

    def handle_startendtag(self, tag, attrs) -> None:
        self.handle_starttag(tag, attrs)


def markdown_link_targets(content: str) -> tuple[str, ...]:
    """Return every Markdown or HTML resource target, failing closed on ambiguity."""
    prose = markdown_prose_without_code(content)
    definitions = {}
    definition_pattern = re.compile(
        r"^[ \t]{0,3}\[([^\]\n]+)\]:[ \t]*(?:<([^>\n]+)>|(\S+))"
        r"(?:[ \t]+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^\)\n]*\)))?[ \t]*$"
    )
    definition_prefix_pattern = re.compile(r"^[ \t]{0,3}\[[^\]\n]+\]:")
    targets = []
    for line in prose.splitlines():
        if not definition_prefix_pattern.match(line):
            continue
        match = definition_pattern.match(line)
        if match is None:
            raise ValueError(
                "candidate skill bundle has unsupported reference definition"
            )
        label = normalize_reference_label(match.group(1))
        if label in definitions:
            raise ValueError(
                "candidate skill bundle has duplicate reference definition"
            )
        target = f"<{match.group(2)}>" if match.group(2) is not None else match.group(3)
        definitions[label] = target
        targets.append(target)

    for match in re.finditer(r"!?\[[^\]\n]+\]\(([^)\n]+)\)", prose):
        targets.append(match.group(1))

    for match in re.finditer(r"!?\[([^\]\n]+)\]\[([^\]\n]*)\]", prose):
        label = normalize_reference_label(match.group(2) or match.group(1))
        if label not in definitions:
            raise ValueError(
                f"candidate skill bundle has unresolved Markdown reference: {label}"
            )
    for label in definitions:
        shortcut_pattern = re.compile(
            rf"(?<!!)\[{re.escape(label)}\](?![\[(])", re.IGNORECASE
        )
        if shortcut_pattern.search(prose):
            targets.append(definitions[label])

    targets.extend(
        match.group(1)
        for match in re.finditer(r"<((?:[A-Za-z][A-Za-z0-9+.-]*):[^<>\s]+)>", prose)
    )
    parser = MarkdownHTMLResourceParser()
    parser.feed(prose)
    parser.close()
    targets.extend(parser.targets)
    return tuple(targets)


def normalize_markdown_target(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<"):
        closing_index = target.find(">")
        if closing_index < 0:
            raise ValueError("candidate skill bundle has unsupported Markdown target")
        target = target[1:closing_index]
    else:
        target = target.split(maxsplit=1)[0]
    target = html.unescape(unquote(target))
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return target
    return parsed.path


def resolve_candidate_skill_bundle(root: Path, skill: str) -> tuple[str, ...]:
    if skill not in CORE_SKILLS:
        raise ValueError(f"unknown candidate skill: {skill}")
    pending = [Path(f"skills/{skill}/SKILL.md")]
    resolved_files = set()
    while pending:
        relative_path = pending.pop()
        normalized = Path(os.path.normpath(relative_path.as_posix()))
        if normalized.is_absolute() or ".." in normalized.parts:
            raise ValueError(
                f"candidate skill bundle escapes plugin root: {relative_path}"
            )
        normalized_text = normalized.as_posix()
        if normalized_text in resolved_files:
            continue
        content_bytes = read_regular_bytes(root, normalized_text)
        resolved_files.add(normalized_text)
        if normalized.suffix.casefold() not in {
            ".json",
            ".md",
            ".txt",
            ".yaml",
            ".yml",
        }:
            continue
        content = content_bytes.decode("utf-8")
        for raw_target in markdown_link_targets(content):
            target = normalize_markdown_target(raw_target)
            if not target:
                continue
            if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target):
                raise ValueError(
                    f"candidate skill bundle contains external reference: {target}"
                )
            pending.append(normalized.parent / target)
    return tuple(sorted(resolved_files))


def candidate_skill_bundle_digest(root: Path, skill: str) -> str:
    digest = hashlib.sha256()
    for relative_path in resolve_candidate_skill_bundle(root, skill):
        digest.update(relative_path.encode())
        digest.update(b"\0")
        digest.update(read_regular_bytes(root, relative_path))
        digest.update(b"\0")
    return digest.hexdigest()


def validate_eval_scenario(root: Path, fixture: str, scenario) -> None:
    if not isinstance(scenario, dict):
        fail(f"eval corpus scenario must be a mapping: {fixture}")
    require_exact_keys(
        scenario,
        {"candidate_skill", "grader_expectations"},
        f"eval corpus scenario {fixture}",
    )
    candidate_skill = require_string(
        scenario["candidate_skill"],
        f"eval corpus scenario {fixture} candidate_skill",
    )
    resolve_candidate_skill_bundle(root, candidate_skill)
    expectations = scenario.get("grader_expectations")
    if (
        not isinstance(expectations, list)
        or not expectations
        or not all(isinstance(expectation, str) for expectation in expectations)
    ):
        fail(f"eval corpus lacks grader expectations for {fixture}")
    prompt = read_regular_file(root, f"evals/fixtures/{fixture}")
    normalized_prompt = prompt.lower()
    if len(prompt.strip()) < 100 or any(
        marker in normalized_prompt
        for marker in ("pass if", "expected output", "reference answer")
    ):
        fail(f"eval fixture is not a concrete raw executor prompt: {fixture}")


def validate_eval_corpus(root: Path) -> None:
    corpus = load_json_document(root, "evals/corpus.json", "eval corpus")
    if not isinstance(corpus, dict):
        raise ValueError("eval corpus manifest must be a JSON object")
    if corpus.get("execution_isolation") != EVAL_DELIVERY_CONTRACT:
        fail("eval corpus does not preserve executor and grader isolation")
    scenarios = corpus.get("scenarios")
    if not isinstance(scenarios, dict) or set(scenarios) != set(EVAL_FIXTURES):
        fail("eval corpus inventory differs from the raw fixture set")
    if file_inventory(root, "evals/fixtures") != set(EVAL_FIXTURES):
        fail("eval corpus inventory differs from the raw fixture set")
    for fixture, scenario in scenarios.items():
        validate_eval_scenario(root, fixture, scenario)


def validate(repo_root: Path, *, emit_success: bool = True) -> None:
    anchor = open_repository_anchor(repo_root)
    try:
        source_observation = anchored_validation_input_observation_digest(anchor)
        temporary_parent = Path(tempfile.gettempdir()).resolve()
        with tempfile.TemporaryDirectory(
            prefix="tricritical-validation-", dir=temporary_parent
        ) as temporary_directory:
            snapshot_repository_path = Path(temporary_directory) / "repository"
            create_private_validation_snapshot_from_anchor(
                anchor, snapshot_repository_path
            )
            copied_source_observation = anchored_validation_input_observation_digest(
                anchor
            )
            if copied_source_observation != source_observation:
                fail("validation input tree drifted; concurrent writers are forbidden")

            repository_root, root = locate_roots(snapshot_repository_path)
            snapshot_identity = validation_input_digest(repository_root, root)
            snapshot_observation = validation_input_observation_digest(
                repository_root, root
            )
            plugin_identity = deterministic_tree_digest(root)

            validate_marketplace(repository_root)
            codex_manifest = validate_manifests(root)
            topology = load_authority_topology(root)
            validate_prompt_pairing(root, codex_manifest)
            validate_adapters(root)
            validate_authority_topology(root, topology)
            load_json_document(root, "evals/corpus.json", "eval corpus")
            load_json_document(root, CONTENT_LOCK_PATH, "semantic content lock")
            validate_skill_local_projections(root)
            validate_inventory(root)
            validate_skill_resource_links(root, tuple(sorted(CORE_SKILLS)))
            validate_input_boundaries(root)
            validate_task_witness_provider(root)
            validate_eval_corpus(root)
            validate_portability(root)
            validate_content_lock(root)

            if validation_input_digest(repository_root, root) != snapshot_identity:
                fail("private validation snapshot changed during validation")
            if (
                validation_input_observation_digest(repository_root, root)
                != snapshot_observation
            ):
                fail("private validation snapshot changed during validation")
            if (
                anchored_validation_input_observation_digest(anchor)
                != copied_source_observation
            ):
                fail("validation input tree drifted; concurrent writers are forbidden")
            verify_repository_anchor_binding(anchor)
            if emit_success:
                print(
                    "Tricritical contract validation passed: "
                    f"snapshot_sha256={snapshot_identity} "
                    f"plugin_sha256={plugin_identity}"
                )
    finally:
        anchor.close()


def generated_artifact_paths(root: Path) -> tuple[Path, ...]:
    paths = {root / CONTENT_LOCK_PATH}
    for skill in CORE_SKILLS:
        for local_path in skill_local_projection_sources(skill):
            paths.add(root / "skills" / skill / local_path)
    return tuple(sorted(paths, key=lambda path: path.as_posix()))


def _refresh_input_observation(
    repository_root: Path,
    root: Path,
    ignored: set[Path],
) -> str:
    """Observe authored bytes and identities while ignoring owned artifacts."""

    digest = hashlib.sha256()
    paths = [root, *sorted(root.rglob("*"), key=lambda path: path.as_posix())]
    for path in paths:
        if path in ignored:
            continue
        relative = "." if path == root else path.relative_to(root).as_posix()
        before = path.lstat()
        if stat.S_ISDIR(before.st_mode):
            fields = (
                before.st_dev,
                before.st_ino,
                stat.S_IFMT(before.st_mode),
                stat.S_IMODE(before.st_mode),
            )
            payload = b""
        elif stat.S_ISREG(before.st_mode):
            payload = read_regular_bytes(root, relative)
            after = path.lstat()
            if metadata_changed(before, after):
                raise ValueError("refresh input changed while it was observed")
            fields = tuple(getattr(after, name) for name in STABLE_METADATA_FIELDS)
        elif stat.S_ISLNK(before.st_mode):
            fields = tuple(getattr(before, name) for name in STABLE_METADATA_FIELDS)
            payload = os.fsencode(os.readlink(path))
        else:
            fields = tuple(getattr(before, name) for name in STABLE_METADATA_FIELDS)
            payload = b""
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update("\0".join(str(field) for field in fields).encode("ascii"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")

    marketplace = repository_root / ".claude-plugin" / "marketplace.json"
    before = marketplace.lstat()
    payload = read_regular_bytes(
        repository_root, ".claude-plugin/marketplace.json"
    )
    after = marketplace.lstat()
    if metadata_changed(before, after):
        raise ValueError("refresh input changed while it was observed")
    digest.update(b"marketplace\0")
    digest.update(
        "\0".join(
            str(getattr(after, name)) for name in STABLE_METADATA_FIELDS
        ).encode("ascii")
    )
    digest.update(b"\0")
    digest.update(payload)
    return digest.hexdigest()


def _materialize_generated_artifacts(root: Path) -> None:
    for skill in CORE_SKILLS:
        for local_path, source_path in skill_local_projection_sources(skill).items():
            projection_path = root / "skills" / skill / local_path
            projection_path.parent.mkdir(parents=True, exist_ok=True)
            projection_path.write_bytes(read_regular_bytes(root, source_path))
    lock_path = root / CONTENT_LOCK_PATH
    lock_path.write_text(
        json.dumps(content_lock_document(root), indent=2) + "\n",
        encoding="utf-8",
    )


def _generated_artifact_plan(root: Path) -> dict[Path, tuple[bytes, int]]:
    return {
        path.relative_to(root): (
            read_regular_bytes(root, path.relative_to(root)),
            stat.S_IMODE(path.lstat().st_mode),
        )
        for path in generated_artifact_paths(root)
    }


def write_content_lock(repo_root: Path) -> None:
    repository_root, root = locate_roots(repo_root)
    generated = set(generated_artifact_paths(root))
    authored_snapshot = _refresh_input_observation(
        repository_root, root, generated
    )
    anchor = open_repository_anchor(repository_root)
    try:
        source_observation = anchored_validation_input_observation_digest(anchor)
        with tempfile.TemporaryDirectory(
            prefix="tricritical-refresh-", dir=Path(tempfile.gettempdir()).resolve()
        ) as temporary_directory:
            snapshot_repository = Path(temporary_directory) / "repository"
            create_private_validation_snapshot_from_anchor(
                anchor, snapshot_repository
            )
            if (
                anchored_validation_input_observation_digest(anchor)
                != source_observation
            ):
                raise ValueError(
                    "validation input tree drifted; concurrent writers are forbidden"
                )
            snapshot_repository_root, snapshot_root = locate_roots(
                snapshot_repository
            )
            _materialize_generated_artifacts(snapshot_root)
            validate(snapshot_repository_root, emit_success=False)
            plan = _generated_artifact_plan(snapshot_root)
            expected_identity = validation_input_digest(
                snapshot_repository_root, snapshot_root
            )

        if anchored_validation_input_observation_digest(anchor) != source_observation:
            raise ValueError(
                "validation input tree drifted; concurrent writers are forbidden"
            )
        verify_repository_anchor_binding(anchor)

        replacements = {
            root / relative: (content, mode)
            for relative, (content, mode) in plan.items()
        }

        def recheck(temporary_paths: frozenset[Path]) -> None:
            ignored = generated | set(temporary_paths)
            if (
                _refresh_input_observation(repository_root, root, ignored)
                != authored_snapshot
            ):
                raise ValueError(
                    "validated inputs changed before generated artifact replacement"
                )

        def verify() -> None:
            if (
                _refresh_input_observation(repository_root, root, generated)
                != authored_snapshot
            ):
                raise ValueError(
                    "validated inputs changed during generated artifact replacement"
                )
            if validation_input_digest(repository_root, root) != expected_identity:
                raise ValueError("generated artifact replacement identity is invalid")

        replace_generated_artifacts(
            replacements,
            recheck=recheck,
            verify=verify,
        )
    finally:
        anchor.close()


def usage() -> None:
    print(
        "usage: validate_tricritical.py [--write-content-lock] [repo-root]",
        file=sys.stderr,
    )


def main() -> None:
    arguments = sys.argv[1:]
    write_lock = bool(arguments and arguments[0] == "--write-content-lock")
    if write_lock:
        arguments = arguments[1:]
    if len(arguments) > 1 or (arguments and arguments[0].startswith("-")):
        usage()
        raise SystemExit(2)
    repo_root = Path(arguments[0]) if arguments else Path(__file__).parents[1]
    try:
        if write_lock:
            write_content_lock(repo_root)
        validate(repo_root)
    except Exception as error:
        fail(f"invalid plugin data: {error}")
    if write_lock:
        print("Tricritical semantic content lock updated")


if __name__ == "__main__":
    main()
