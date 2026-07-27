#!/usr/bin/env python3
"""Validate one immutable public release snapshot and its Phase 7 projection."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import math
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from evidence_transport import (
    candidate_content_identity,
)
from phase7_compatibility_projection import compatibility_bytes
from phase7_private_evidence_isolation import (
    IsolationError,
    validate_public_release_backend_evidence,
    validate_runtime_backend_release_evidence,
)
from private_phase7_evidence import (
    CHECK_ORDER,
    ROLE_ORDER,
    PrivateEvidenceError,
    validate_composed_replay_binding,
    verify_public_release_evidence,
)

CONTROL_PLUGINS = ("rolecasting", "versionkeeping", "mergecraft", "tricritical")
CANONICAL_REPOSITORY_URL = "https://github.com/nisavid/agents"
SKILL_PLUGINS = (
    "rolecasting",
    "versionkeeping",
    "mergecraft",
    "tricritical",
    "artifact-customs",
)
RUNTIME_PACKAGES = ("task-witness",)
VALIDATED_PLUGINS = SKILL_PLUGINS + RUNTIME_PACKAGES
MARKETPLACE_PLUGINS = {
    "tricritical": "./plugins/tricritical",
    "rolecasting": "./plugins/rolecasting",
    "versionkeeping": "./plugins/versionkeeping",
    "mergecraft": "./plugins/mergecraft",
    "artifact-customs": "./plugins/artifact-customs",
    "task-witness": "./plugins/task-witness",
}
COMMON_SUPPORT_PATHS = {
    ".claude-plugin/marketplace.json",
    "README.md",
    "evals/README.md",
    "evals/control-plane-matrix.json",
    "evals/skill-routing-matrix.json",
    "scripts/run_control_plane_eval.py",
    "scripts/evidence_transport.py",
    "scripts/phase7_control_plane.py",
    "scripts/phase7_compatibility_projection.py",
    "scripts/evidence_transport.py",
    "scripts/phase7_private_evidence_backend_contracts.json",
    "scripts/phase7_private_evidence_isolation.py",
    "scripts/phase7_private_evidence_producer.py",
    "scripts/private_phase7_evidence.py",
    "scripts/run_phase7_terminal_proof.py",
    "scripts/combine_phase7_terminal_proofs.py",
    "scripts/run_phase7_composed_matrix.py",
    "scripts/run_phase7_production_integration.py",
    "scripts/run_skill_routing_eval.py",
    "tests/test_evidence_transport.py",
    "tests/test_phase7_control_plane.py",
    "tests/test_phase7_compatibility_projection.py",
    "tests/test_phase7_composed_matrix.py",
    "tests/test_phase7_production_integration.py",
    "tests/test_phase7_terminal_proof.py",
    "tests/test_phase7_terminal_proof_combiner.py",
    "tests/test_private_phase7_evidence_v4.py",
    "tests/phase7_v4_fixture.py",
    "tests/fixtures/phase7-v4-compatibility.json",
    "tests/test_control_plane_behavior_eval.py",
    "tests/test_skill_routing_eval.py",
    "scripts/validate_public_release.py",
    "scripts/validate_plugin_runtime_roots.py",
    "tests/test_validate_public_release.py",
    "tests/test_validate_plugin_runtime_roots.py",
    "release/plugin-eval-policy.json",
    "release/plugin-eval-baseline-v1.json",
}
PLUGIN_SUPPORT_PATHS = {
    "rolecasting": {
        "scripts/validate_rolecasting.py",
        "tests/test_validate_rolecasting.py",
        "tests/test_rolecasting_eval_corpus.py",
    },
    "versionkeeping": {
        "scripts/validate_versionkeeping.py",
        "tests/test_validate_versionkeeping.py",
        "evals/versionkeeping",
        "tests/plugins/versionkeeping",
        "release/plugin-content-locks/versionkeeping.json",
    },
    "mergecraft": {
        "scripts/validate_mergecraft.py",
        "tests/test_validate_mergecraft.py",
        "evals/mergecraft",
        "tests/plugins/mergecraft",
        "release/plugin-content-locks/mergecraft.json",
        "release/mergecraft-retirement-contribution-ledger.json",
        "release/mergecraft",
    },
    "tricritical": {
        "scripts/validate_tricritical.py",
        "tests/test_validate_tricritical.py",
        "tests/test_tricritical_eval_corpus.py",
    },
    "artifact-customs": {
        "scripts/validate_artifact_customs.py",
        "tests/test_validate_artifact_customs.py",
        "tests/test_artifact_customs_eval_corpus.py",
        "tests/test_artifact_customs_behavior_eval.py",
        "scripts/run_artifact_customs_eval.py",
        "evals/artifact-customs",
        "release/plugin-content-locks/artifact-customs.json",
    },
    "task-witness": {
        "release/task-witness/source-shape-review.json",
        "scripts/validate_task_witness.py",
        "tests/plugins/test_task_witness_launcher.py",
        "tests/plugins/test_task_witness_runtime.py",
        "tests/test_task_witness_package.py",
    },
}
PLUGIN_EVAL_POLICY_PATH = "release/plugin-eval-policy.json"
PLUGIN_EVAL_BASELINE_PATH = "release/plugin-eval-baseline-v1.json"
BUDGET_METRICS = (
    "trigger_cost_tokens",
    "invoke_cost_tokens",
    "deferred_cost_tokens",
)
VALIDATOR_PATHS = {
    "rolecasting": "scripts/validate_rolecasting.py",
    "versionkeeping": "scripts/validate_versionkeeping.py",
    "mergecraft": "scripts/validate_mergecraft.py",
    "tricritical": "scripts/validate_tricritical.py",
    "artifact-customs": "scripts/validate_artifact_customs.py",
    "task-witness": "scripts/validate_task_witness.py",
}
SOURCE_STAGE_VALIDATOR_FLAGS = {
    **{plugin: () for plugin in VALIDATED_PLUGINS},
    "mergecraft": ("--source-stage",),
    "artifact-customs": ("--source-stage",),
}
SHA256 = re.compile(r"sha256:[0-9a-f]{64}$")
GIT_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})$")
NODE_SEMVER = re.compile(
    r"v?(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\.(?P<patch>0|[1-9][0-9]*)$"
)
MINIMUM_NODE_MAJOR = 20
SAFE_NODE_SEARCH_PATH = os.pathsep.join(
    ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin")
)
COMPOSED_FAMILIES = ("lifecycle-dispatch", "tricritical-contract")
COMPOSED_CLAIM = (
    "provider-free public composition with replay-payload-bound frozen private evidence"
)
COMPOSED_SCHEMA_VERSION = 7
COMPOSED_CONTRACT = "phase7-composed-evidence-v7"


class ReleaseError(ValueError):
    """A stable, user-actionable release validation failure."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseError(message)


def canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def canonical_document(value: object) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode()


def strict_json(content: str, label: str):
    def build_object(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    def reject_constant(value: str):
        raise ReleaseError(f"non-finite JSON value in {label}: {value}")

    def finite_float(value: str) -> float:
        parsed = float(value)
        require(math.isfinite(parsed), f"non-finite JSON value in {label}: {value}")
        return parsed

    return json.loads(
        content,
        object_pairs_hook=build_object,
        parse_constant=reject_constant,
        parse_float=finite_float,
    )


def normalize_snapshot_path(value: str, snapshot: Path) -> str:
    return value.replace(str(snapshot), "<snapshot>")


def runtime_tree_digest(root: Path, relative_paths: tuple[str, ...]) -> str:
    """Digest the exact callable runtime tree without following symlinks."""
    entries: dict[str, Path] = {}
    for relative in relative_paths:
        candidate = root / relative
        require(candidate.exists(), f"plugin-eval runtime path is missing: {relative}")
        for path in [
            candidate,
            *sorted(candidate.rglob("*"), key=lambda item: item.as_posix()),
        ]:
            resolved_relative = path.relative_to(root).as_posix()
            metadata = path.lstat()
            require(
                not stat.S_ISLNK(metadata.st_mode),
                f"plugin-eval runtime contains a symlink: {resolved_relative}",
            )
            require(
                stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode),
                f"plugin-eval runtime contains an unsupported entry: {resolved_relative}",
            )
            entries[resolved_relative] = path
    digest = hashlib.sha256()
    for relative, path in sorted(entries.items()):
        metadata = path.lstat()
        kind = "directory" if stat.S_ISDIR(metadata.st_mode) else "file"
        digest.update(f"{relative}\0{kind}\0{metadata.st_mode:o}\0".encode())
        if kind == "file":
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def load_plugin_eval_policy(snapshot: Path) -> dict[str, object]:
    path = snapshot / PLUGIN_EVAL_POLICY_PATH
    policy = strict_json(path.read_text(encoding="utf-8"), "plugin-eval policy")
    require(
        isinstance(policy, dict)
        and set(policy)
        == {
            "schema_version",
            "tool",
            "calibration",
            "native_budget_findings",
            "advisories",
        }
        and type(policy["schema_version"]) is int
        and policy["schema_version"] == 5,
        "plugin-eval policy schema drift",
    )
    tool = policy["tool"]
    require(
        isinstance(tool, dict)
        and set(tool)
        == {
            "name",
            "plugin_manifest_relpath",
            "plugin_manifest_version",
            "plugin_manifest_sha256",
            "package_manifest_version",
            "report_version",
            "executable_relpath",
            "runtime_paths",
            "runtime_tree_sha256",
        }
        and tool["name"] == "plugin-eval"
        and tool["plugin_manifest_relpath"] == ".codex-plugin/plugin.json"
        and isinstance(tool["plugin_manifest_version"], str)
        and tool["plugin_manifest_version"]
        and isinstance(tool["plugin_manifest_sha256"], str)
        and SHA256.fullmatch(tool["plugin_manifest_sha256"])
        and isinstance(tool["package_manifest_version"], str)
        and tool["package_manifest_version"]
        and isinstance(tool["report_version"], str)
        and tool["report_version"],
        "plugin-eval policy tool identity drift",
    )
    require(
        tool["executable_relpath"] == "scripts/plugin-eval.js"
        and tool["runtime_paths"] == ["package.json", "scripts/plugin-eval.js", "src"]
        and isinstance(tool["runtime_tree_sha256"], str)
        and SHA256.fullmatch(tool["runtime_tree_sha256"]),
        "plugin-eval runtime policy drift",
    )

    calibration = policy["calibration"]
    require(
        isinstance(calibration, dict)
        and set(calibration) == {"manifest_path", "manifest_sha256"}
        and calibration["manifest_path"] == PLUGIN_EVAL_BASELINE_PATH
        and isinstance(calibration["manifest_sha256"], str)
        and SHA256.fullmatch(calibration["manifest_sha256"]),
        "plugin-eval calibration policy drift",
    )
    baseline_path = snapshot / PLUGIN_EVAL_BASELINE_PATH
    baseline = strict_json(
        baseline_path.read_text(encoding="utf-8"), "plugin-eval calibration manifest"
    )
    baseline_fields = {
        "schema_version",
        "plugin_eval_runtime_sha256",
        "target_kind",
        "quantile_algorithm",
        "quantiles_basis_points",
        "sample_count",
        "measurements",
        "manifest_sha256",
    }
    require(
        isinstance(baseline, dict)
        and set(baseline) == baseline_fields
        and baseline["schema_version"] == 1,
        "plugin-eval calibration manifest schema drift",
    )
    baseline_unsigned = {
        key: value for key, value in baseline.items() if key != "manifest_sha256"
    }
    require(
        isinstance(baseline["manifest_sha256"], str)
        and SHA256.fullmatch(baseline["manifest_sha256"])
        and baseline["manifest_sha256"] == canonical_digest(baseline_unsigned),
        "plugin-eval calibration manifest digest mismatch",
    )
    require(
        baseline["manifest_sha256"] == calibration["manifest_sha256"],
        "plugin-eval calibration policy digest mismatch",
    )
    require(
        baseline["plugin_eval_runtime_sha256"] == tool["runtime_tree_sha256"],
        "plugin-eval calibration runtime digest mismatch",
    )
    require(
        baseline["target_kind"] == "plugin",
        "plugin-eval calibration target kind mismatch",
    )
    require(
        baseline["quantile_algorithm"] == "sorted-floor-n-minus-one-v1"
        and baseline["quantiles_basis_points"] == [5000, 7500, 9000],
        "plugin-eval calibration quantile algorithm drift",
    )
    measurements = baseline["measurements"]
    require(
        isinstance(measurements, dict) and set(measurements) == set(BUDGET_METRICS),
        "plugin-eval calibration measurements drift",
    )
    lengths = [
        len(values) if isinstance(values, list) else -1
        for values in measurements.values()
    ]
    require(
        len(set(lengths)) == 1 and lengths[0] > 0,
        "plugin-eval calibration arrays must be non-empty and equal-length",
    )
    require(
        type(baseline["sample_count"]) is int
        and baseline["sample_count"] == lengths[0],
        "plugin-eval calibration sample count mismatch",
    )
    for metric, values in measurements.items():
        require(
            all(type(value) is int and value >= 0 for value in values)
            and values == sorted(values),
            f"plugin-eval calibration {metric} must be sorted nonnegative integers",
        )
    quantile_names = ("goodMax", "moderateMax", "heavyMax")
    require(
        len(quantile_names) == len(baseline["quantiles_basis_points"]),
        "plugin-eval calibration quantile inventory drift",
    )
    thresholds = {
        metric: {
            name: values[((baseline["sample_count"] - 1) * basis_points) // 10000]
            for name, basis_points in zip(
                quantile_names, baseline["quantiles_basis_points"]
            )
        }
        for metric, values in measurements.items()
    }

    deduction_fields = {
        "id",
        "category",
        "severity",
        "status",
        "message",
        "penalty",
        "remediation",
        "source",
    }
    native_findings = policy["native_budget_findings"]
    require(
        isinstance(native_findings, list) and len(native_findings) == 3,
        "plugin-eval native budget findings drift",
    )
    native_budget_findings = {}
    for finding in native_findings:
        require(
            isinstance(finding, dict)
            and set(finding) == deduction_fields
            and finding["category"] == "budget"
            and finding["status"] == "fail"
            and finding["severity"] == "error"
            and type(finding["penalty"]) in {int, float}
            and finding["penalty"] >= 0
            and isinstance(finding["message"], str)
            and finding["message"]
            and isinstance(finding["remediation"], list)
            and all(isinstance(item, str) and item for item in finding["remediation"])
            and isinstance(finding["source"], str)
            and finding["source"],
            "plugin-eval native budget finding shape drift",
        )
        require(
            finding["id"] not in native_budget_findings,
            "duplicate plugin-eval native budget finding",
        )
        native_budget_findings[finding["id"]] = finding
    require(
        set(native_budget_findings)
        == {f"{metric}-budget-high" for metric in BUDGET_METRICS},
        "plugin-eval native budget finding inventory drift",
    )

    advisories = policy["advisories"]
    require(isinstance(advisories, list), "plugin-eval advisories must be an array")
    indexed = {}
    for advisory in advisories:
        kind = advisory.get("kind") if isinstance(advisory, dict) else None
        expected_advisory_fields = {"kind", "plugin", "deduction", "reason"}
        if kind == "bounded_runtime_cost":
            expected_advisory_fields = {
                "kind",
                "plugin",
                "metric",
                "reason",
                "required_components",
            }
        require(
            isinstance(advisory, dict)
            and kind in {"exact_false_positive", "bounded_runtime_cost"}
            and set(advisory) == expected_advisory_fields,
            "plugin-eval advisory shape drift",
        )
        plugin = advisory["plugin"]
        deduction = advisory.get("deduction")
        if kind == "bounded_runtime_cost":
            metric = advisory["metric"]
            require(
                plugin in SKILL_PLUGINS
                and metric in BUDGET_METRICS
                and isinstance(advisory["reason"], str)
                and advisory["reason"].strip(),
                "plugin-eval advisory identity drift",
            )
            deduction_id = f"{metric}-budget-high"
        else:
            deduction_id = deduction.get("id") if isinstance(deduction, dict) else None
            require(
                plugin in SKILL_PLUGINS
                and isinstance(deduction, dict)
                and set(deduction)
                in (deduction_fields, deduction_fields | {"targetPath"})
                and isinstance(deduction["id"], str)
                and deduction["id"]
                and deduction["status"] == "fail"
                and deduction["severity"] == "error"
                and isinstance(deduction["category"], str)
                and deduction["category"]
                and isinstance(deduction["message"], str)
                and deduction["message"]
                and type(deduction["penalty"]) in {int, float}
                and deduction["penalty"] >= 0
                and isinstance(deduction["remediation"], list)
                and all(
                    isinstance(item, str) and item for item in deduction["remediation"]
                )
                and isinstance(deduction["source"], str)
                and deduction["source"]
                and isinstance(advisory["reason"], str)
                and advisory["reason"].strip(),
                "plugin-eval advisory identity drift",
            )
            require(
                "targetPath" not in deduction
                or deduction["targetPath"] == f"plugins/{plugin}",
                "plugin-eval advisory target drift",
            )
        if kind == "bounded_runtime_cost":
            components = advisory["required_components"]
            require(
                isinstance(components, list) and components,
                "plugin-eval advisory components must be a non-empty array",
            )
            component_keys = set()
            for component in components:
                require(
                    isinstance(component, dict)
                    and set(component) == {"label", "path", "maximum_tokens"}
                    and isinstance(component["label"], str)
                    and component["label"]
                    and isinstance(component["path"], str)
                    and component["path"]
                    and type(component["maximum_tokens"]) is int
                    and component["maximum_tokens"] >= 0,
                    "plugin-eval advisory component drift",
                )
                component_key = (component["label"], component["path"])
                require(
                    component_key not in component_keys,
                    "duplicate plugin-eval advisory component",
                )
                component_keys.add(component_key)
        key = (plugin, deduction_id)
        require(key not in indexed, "duplicate plugin-eval advisory")
        indexed[key] = advisory
    return {
        "tool": tool,
        "advisories": indexed,
        "native_budget_findings": native_budget_findings,
        "thresholds": thresholds,
        "policy_sha256": canonical_digest(policy),
        "calibration_manifest_sha256": baseline["manifest_sha256"],
    }


def private_plugin_eval_environment(root: Path) -> dict[str, str]:
    """Create the intentionally small environment for pinned Node evaluation.

    The evaluator is a local executable, but inherited loader, npm, and Node
    settings can still change what its bytes execute.  It gets a fresh home and
    temporary directory instead of the release process's ambient environment.
    """

    root.mkdir(mode=0o700)
    home = root / "home"
    temporary = root / "tmp"
    home.mkdir(mode=0o700)
    temporary.mkdir(mode=0o700)
    return {
        "HOME": str(home),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "TEMP": str(temporary),
        "TMP": str(temporary),
        "TMPDIR": str(temporary),
    }


def _required_no_follow_directory_flags() -> int:
    """Return the primitives needed for descriptor-safe pathname traversal."""

    no_follow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if not isinstance(no_follow, int) or not isinstance(directory, int):
        raise ReleaseError(
            "safe Node interpreter resolution requires O_NOFOLLOW and O_DIRECTORY"
        )
    return os.O_RDONLY | directory | no_follow


def _open_no_follow_directory(path: Path, label: str) -> int:
    """Open an absolute directory without following any component symlink."""

    require(path.is_absolute(), f"{label} parent path must be absolute")
    flags = _required_no_follow_directory_flags()
    descriptor = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            try:
                metadata = os.fstat(next_descriptor)
                require(
                    stat.S_ISDIR(metadata.st_mode),
                    f"{label} parent contains a non-directory component",
                )
            except BaseException:
                os.close(next_descriptor)
                raise
            os.close(descriptor)
            descriptor = next_descriptor
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _read_nofollow_regular_file(path: Path, label: str) -> tuple[bytes, os.stat_result]:
    """Read one regular file through an O_NOFOLLOW descriptor.

    Python cannot portably `fexecve` a Node binary on macOS, Linux, and WSL2.
    We therefore bind its descriptor-derived bytes before and after every
    analysis invocation. That detects persistent pathname replacement; an ABA
    swap restored to the same bytes during exec is the unavoidable portable
    pre/post boundary without `fexecve`.
    """

    require(path.is_absolute(), f"{label} path must be absolute")
    path = Path(os.path.abspath(os.fspath(path)))
    try:
        parent_descriptor = _open_no_follow_directory(path.parent, label)
    except OSError as error:
        raise ReleaseError(f"{label} is unavailable or unsafe: {error}") from error
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
    except OSError as error:
        os.close(parent_descriptor)
        raise ReleaseError(f"{label} is unavailable or unsafe: {error}") from error
    try:
        metadata = os.fstat(descriptor)
        require(stat.S_ISREG(metadata.st_mode), f"{label} must be a regular file")
        require(
            metadata.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH),
            f"{label} must be executable",
        )
        entry_metadata = os.stat(
            path.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        require(
            _stable_file_metadata(entry_metadata) == _stable_file_metadata(metadata),
            f"{label} changed while it was opened",
        )
        contents = bytearray()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            contents.extend(chunk)
        final_metadata = os.fstat(descriptor)
        final_entry_metadata = os.stat(
            path.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        require(
            _stable_file_metadata(final_metadata) == _stable_file_metadata(metadata)
            and _stable_file_metadata(final_entry_metadata)
            == _stable_file_metadata(metadata),
            f"{label} changed while it was read",
        )
        return bytes(contents), metadata
    finally:
        os.close(descriptor)
        os.close(parent_descriptor)


def _stable_file_metadata(
    metadata: os.stat_result,
) -> tuple[int, int, int, int, int, int]:
    """Return the descriptor and directory-entry fields that bind a snapshot."""

    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _canonical_node_path(node: Path | None) -> Path:
    if node is None:
        discovered = shutil.which("node", path=SAFE_NODE_SEARCH_PATH)
        require(discovered is not None, "node executable is unavailable")
        # The fixed search list may intentionally contain a package-manager
        # launcher. Resolve it only as discovery, then bind and invoke the
        # resulting target through descriptor traversal below.
        return Path(os.path.realpath(os.path.abspath(discovered)))
    # An explicit operator path is itself an assertion about the executable;
    # reject any symlinked component rather than silently changing it.
    return Path(os.path.abspath(os.fspath(node)))


def _node_binary_digest(path: Path) -> str:
    contents, _metadata = _read_nofollow_regular_file(path, "node executable")
    require(
        not contents.startswith(b"#!"), "node executable must not be a shebang shim"
    )
    return "sha256:" + hashlib.sha256(contents).hexdigest()


def _node_version(path: Path, environment: dict[str, str]) -> str:
    result = subprocess.run(
        [str(path), "--version"],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    require(
        result.returncode == 0,
        f"node executable version probe failed:\n{result.stdout}{result.stderr}",
    )
    version = result.stdout.strip()
    match = NODE_SEMVER.fullmatch(version)
    require(match is not None, "node executable version must be strict semver")
    require(
        int(match["major"]) >= MINIMUM_NODE_MAJOR,
        f"node executable must be Node {MINIMUM_NODE_MAJOR} or newer",
    )
    return version


def resolve_node_interpreter(
    node: Path | None, environment: dict[str, str]
) -> dict[str, str]:
    """Resolve and bind one absolute Node binary for a release evaluation."""

    path = _canonical_node_path(node)
    digest = _node_binary_digest(path)
    version = _node_version(path, environment)
    require(
        _node_binary_digest(path) == digest,
        "node executable changed while its identity was derived",
    )
    return {"path": str(path), "sha256": digest, "version": version}


def revalidate_node_interpreter(identity: dict[str, str]) -> None:
    """Fail closed if the main executable bytes drift before or after a child."""

    path = Path(identity["path"])
    require(
        _node_binary_digest(path) == identity["sha256"],
        "node executable changed during plugin evaluation",
    )


def public_node_interpreter_evidence(identity: dict[str, str]) -> dict[str, object]:
    """Project portable Node evidence without exposing a machine-local path.

    The binding covers only the main executable's bytes and its single version
    probe. Node may still load platform libraries, and a pathname can undergo an
    undetectable ABA swap exactly while `execve` resolves it; those limits are
    deliberately retained in the receipt instead of being implied away.
    """

    require(
        set(identity) == {"path", "sha256", "version"},
        "internal Node interpreter identity is malformed",
    )
    return {
        "sha256": identity["sha256"],
        "version": identity["version"],
        "coverage": "main-executable-bytes-only",
        "limitations": [
            "dynamic-loader-and-shared-library-bytes-are-not-bound",
            "pathname-exec-time-ABA-is-not-bound",
        ],
    }


def verify_plugin_eval_runtime(
    executable: Path, policy: dict[str, object]
) -> dict[str, str]:
    tool = policy["tool"]
    assert isinstance(tool, dict)
    executable = Path(os.path.abspath(os.fspath(executable)))
    for ancestor in (executable, executable.parent, executable.parent.parent):
        require(
            not ancestor.is_symlink(),
            f"plugin-eval executable path contains a symlink: {ancestor}",
        )
    package_root = executable.parent.parent
    expected_executable = package_root / tool["executable_relpath"]
    require(
        executable == expected_executable and executable.is_file(),
        "plugin-eval executable does not match the pinned package layout",
    )
    plugin_manifest_path = package_root / tool["plugin_manifest_relpath"]
    require(
        plugin_manifest_path.parent.is_dir()
        and not plugin_manifest_path.parent.is_symlink()
        and plugin_manifest_path.is_file()
        and not plugin_manifest_path.is_symlink(),
        "plugin-eval distribution manifest is missing or unsafe",
    )
    plugin_manifest_bytes = plugin_manifest_path.read_bytes()
    plugin_manifest_digest = (
        "sha256:" + hashlib.sha256(plugin_manifest_bytes).hexdigest()
    )
    plugin_manifest = strict_json(
        plugin_manifest_bytes.decode("utf-8"), "plugin-eval distribution manifest"
    )
    require(
        isinstance(plugin_manifest, dict)
        and plugin_manifest.get("name") == tool["name"]
        and plugin_manifest.get("version") == tool["plugin_manifest_version"]
        and plugin_manifest_digest == tool["plugin_manifest_sha256"],
        "plugin-eval distribution manifest identity drift",
    )
    package = strict_json(
        (package_root / "package.json").read_text(encoding="utf-8"),
        "plugin-eval package manifest",
    )
    require(
        isinstance(package, dict)
        and package.get("name") == tool["name"]
        and package.get("version") == tool["package_manifest_version"]
        and package.get("bin") == {"plugin-eval": "./scripts/plugin-eval.js"},
        "plugin-eval package identity drift",
    )
    runtime_digest = runtime_tree_digest(package_root, tuple(tool["runtime_paths"]))
    require(
        runtime_digest == tool["runtime_tree_sha256"],
        "plugin-eval runtime package digest drift",
    )
    return {
        "plugin_manifest_version": tool["plugin_manifest_version"],
        "plugin_manifest_sha256": plugin_manifest_digest,
        "package_manifest_version": tool["package_manifest_version"],
        "runtime_tree_sha256": runtime_digest,
    }


def copy_pinned_plugin_eval_runtime(
    executable: Path, policy: dict[str, object], destination: Path
) -> tuple[Path, dict[str, str]]:
    """Copy the verified callable runtime before invoking any of its bytes.

    The installed plugin path is mutable.  Every invocation therefore runs from a
    newly materialized runtime whose digest is checked after the copy, rather than
    trusting a pathname that was merely checked before it was executed.
    """

    runtime = verify_plugin_eval_runtime(executable, policy)
    tool = policy["tool"]
    assert isinstance(tool, dict)
    package_root = executable.parent.parent
    destination.mkdir(mode=0o700)
    for relative in tuple(tool["runtime_paths"]):
        source = package_root / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, symlinks=False)
        else:
            shutil.copy2(source, target, follow_symlinks=False)
    require(
        runtime_tree_digest(destination, tuple(tool["runtime_paths"]))
        == runtime["runtime_tree_sha256"],
        "plugin-eval runtime changed while the pinned copy was created",
    )
    pinned_executable = destination / str(tool["executable_relpath"])
    require(
        pinned_executable.is_file() and not pinned_executable.is_symlink(),
        "pinned plugin-eval executable is unavailable",
    )
    return pinned_executable, runtime


def plugin_eval_metric(report: dict, metric: str) -> tuple[int, list[dict]]:
    budgets = report.get("budgets")
    require(isinstance(budgets, dict), "plugin-eval budgets are missing")
    value = budgets.get(metric)
    require(isinstance(value, dict), f"plugin-eval {metric} is missing")
    tokens = value.get("value")
    components = value.get("components")
    require(
        type(tokens) is int and tokens >= 0,
        f"plugin-eval {metric} value is malformed",
    )
    require(
        isinstance(components, list), f"plugin-eval {metric} components are malformed"
    )
    component_tokens = 0
    for component in components:
        require(
            isinstance(component, dict)
            and set(component) == {"label", "path", "tokens", "note"}
            and isinstance(component["label"], str)
            and component["label"]
            and isinstance(component["path"], str)
            and component["path"]
            and type(component["tokens"]) is int
            and component["tokens"] >= 0
            and isinstance(component["note"], str),
            f"plugin-eval {metric} components are malformed",
        )
        component_tokens += component["tokens"]
    require(
        component_tokens == tokens,
        f"plugin-eval {metric} component tokens do not sum to its value",
    )
    return tokens, components


def validate_plugin_eval_report(
    plugin: str, report: dict, policy: dict[str, object], snapshot: Path
) -> dict[str, object]:
    tool = policy["tool"]
    assert isinstance(tool, dict)
    require(
        type(report.get("schemaVersion")) is int and report["schemaVersion"] == 1,
        f"{plugin} plugin-eval report schema drift",
    )
    require(
        report.get("tool") == {"name": tool["name"], "version": tool["report_version"]},
        f"{plugin} plugin-eval report tool identity drift",
    )
    target_root = snapshot / "plugins" / plugin
    require(
        report.get("target")
        == {
            "kind": "plugin",
            "path": str(target_root),
            "entryPath": str(target_root / ".codex-plugin/plugin.json"),
            "name": plugin,
            "relativePath": f"plugins/{plugin}",
        },
        f"{plugin} plugin-eval report target identity drift",
    )
    checks = report.get("checks")
    summary = report.get("summary")
    require(
        isinstance(checks, list) and isinstance(summary, dict),
        f"{plugin} plugin-eval report sections are malformed",
    )
    check_ids: set[str] = set()
    deductions_by_id: dict[str, dict] = {}
    check_fields = {
        "id",
        "category",
        "severity",
        "status",
        "message",
        "evidence",
        "remediation",
        "source",
    }
    for check in checks:
        require(
            isinstance(check, dict)
            and set(check) in (check_fields, check_fields | {"targetPath"})
            and isinstance(check["id"], str)
            and check["id"]
            and check["status"] in {"pass", "warn", "fail", "info"}
            and check["severity"] in {"error", "warning", "info"}
            and isinstance(check["category"], str)
            and isinstance(check["message"], str)
            and isinstance(check["evidence"], list)
            and isinstance(check["remediation"], list)
            and isinstance(check["source"], str),
            f"{plugin} plugin-eval check shape drift",
        )
        require(
            "targetPath" not in check or check["targetPath"] == f"plugins/{plugin}",
            f"{plugin} plugin-eval check target drift",
        )
        require(
            check["id"] not in check_ids,
            f"{plugin} plugin-eval check IDs must be unique",
        )
        check_ids.add(check["id"])
    deductions = summary.get("deductions")
    require(
        isinstance(deductions, list), f"{plugin} plugin-eval deductions are malformed"
    )
    deduction_fields = check_fields - {"evidence"} | {"penalty"}
    for deduction in deductions:
        require(
            isinstance(deduction, dict)
            and set(deduction) in (deduction_fields, deduction_fields | {"targetPath"})
            and isinstance(deduction["id"], str)
            and deduction["id"] not in deductions_by_id
            and type(deduction["penalty"]) in {int, float},
            f"{plugin} plugin-eval deduction shape drift",
        )
        require(
            "targetPath" not in deduction
            or deduction["targetPath"] == f"plugins/{plugin}",
            f"{plugin} plugin-eval deduction target drift",
        )
        deductions_by_id[deduction["id"]] = deduction
    require(
        set(deductions_by_id) == check_ids,
        f"{plugin} plugin-eval summary/check IDs drift",
    )
    for check in checks:
        deduction = deductions_by_id[check["id"]]
        require(
            all(deduction[key] == check[key] for key in deduction if key != "penalty"),
            f"{plugin} plugin-eval summary/check detail drift",
        )
    budgets = {}
    for metric in BUDGET_METRICS:
        metric_value, metric_components = plugin_eval_metric(report, metric)
        budgets[metric] = {
            "value": metric_value,
            "components": metric_components,
        }
    return {"deductions": deductions, "budgets": budgets}


def plugin_eval_budget_band(value: int, thresholds: dict[str, int]) -> str:
    if value <= thresholds["goodMax"]:
        return "good"
    if value <= thresholds["moderateMax"]:
        return "moderate"
    if value <= thresholds["heavyMax"]:
        return "heavy"
    return "excessive"


def advisory_applies(
    plugin: str,
    failure: dict,
    policy: dict,
) -> bool:
    advisories = policy["advisories"]
    assert isinstance(advisories, dict)
    advisory = advisories.get((plugin, failure.get("id")))
    if advisory is None or advisory["kind"] != "exact_false_positive":
        return False
    require(failure == advisory["deduction"], f"{plugin} advisory deduction drift")
    return True


def runtime_cost_advisory(
    plugin: str,
    metric: str,
    value: int,
    components: list[dict],
    policy: dict,
    snapshot: Path,
) -> dict | None:
    advisory_id = f"{metric}-budget-high"
    advisory = policy["advisories"].get((plugin, advisory_id))
    if advisory is None or advisory["kind"] != "bounded_runtime_cost":
        return None
    require(
        advisory["metric"] == metric,
        f"{plugin} advisory metric drift for {advisory_id}",
    )
    matched_tokens = []
    for required in advisory["required_components"]:
        matches = [
            component
            for component in components
            if component.get("label") == required["label"]
            and normalize_snapshot_path(component.get("path", ""), snapshot)
            == required["path"]
        ]
        require(
            len(matches) == 1
            and type(matches[0].get("tokens")) is int
            and 0 <= matches[0]["tokens"] <= required["maximum_tokens"],
            f"{plugin} advisory runtime component evidence drift",
        )
        matched_tokens.append(matches[0]["tokens"])
    heavy_max = policy["thresholds"][metric]["heavyMax"]
    component_tokens = sum(matched_tokens)
    require(
        value > heavy_max and value - component_tokens <= heavy_max,
        f"{plugin} advisory components are not the causal budget excess",
    )
    require(
        all(
            value - (component_tokens - tokens) > heavy_max for tokens in matched_tokens
        ),
        f"{plugin} advisory contains a nonessential runtime component",
    )
    require(
        len(advisory["required_components"]) == len(matched_tokens),
        f"{plugin} advisory runtime component inventory drift",
    )
    return {
        "id": advisory_id,
        "components": [
            {"label": required["label"], "tokens": tokens}
            for required, tokens in zip(advisory["required_components"], matched_tokens)
        ],
    }


def reject_non_finite(value, label: str) -> None:
    if isinstance(value, float):
        require(math.isfinite(value), f"non-finite number in {label}")
    elif isinstance(value, dict):
        for child in value.values():
            reject_non_finite(child, label)
    elif isinstance(value, list):
        for child in value:
            reject_non_finite(child, label)


def lexical_repository(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        require(
            not current.is_symlink(), f"repository path contains a symlink: {current}"
        )
    require(absolute.is_dir(), "repository root must be a directory")
    return absolute


def canonical_repository_origin(origin: str) -> str:
    """Normalize only the credential-free transports for this release repository."""

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


def git_candidate_identity(repository: Path) -> dict[str, str]:
    """Bind production routing evidence to the exact clean Git candidate."""

    def run_git(*arguments: str) -> bytes:
        result = subprocess.run(
            ["git", *arguments],
            cwd=repository,
            capture_output=True,
            check=False,
        )
        require(
            result.returncode == 0,
            result.stderr.decode(errors="replace").strip()
            or f"git {' '.join(arguments)} failed",
        )
        return result.stdout

    status = run_git("status", "--porcelain=v1", "--untracked-files=all")
    require(not status, "production release candidate must be a clean Git checkout")
    revision = run_git("rev-parse", "--verify", "HEAD").decode("ascii").strip()
    tree_oid = (
        run_git("rev-parse", "--verify", f"{revision}^{{tree}}").decode("ascii").strip()
    )
    archive = run_git("archive", "--format=tar", revision)
    repository_url = canonical_repository_origin(
        run_git("remote", "get-url", "origin").decode("utf-8").strip()
    )
    require(
        re.fullmatch(r"[0-9a-f]{40,64}", revision) is not None
        and re.fullmatch(r"[0-9a-f]{40,64}", tree_oid) is not None,
        "Git candidate identity is malformed",
    )
    return {
        "revision": revision,
        "tree_oid": tree_oid,
        "archive_sha256": "sha256:" + hashlib.sha256(archive).hexdigest(),
        "repository": repository_url,
    }


def load_skill_routing_evaluator(snapshot: Path):
    runner = snapshot / "scripts/run_skill_routing_eval.py"
    require(
        runner.is_file() and not runner.is_symlink(), "skill-routing runner is missing"
    )
    module_name = "public_release_skill_routing_eval"
    specification = importlib.util.spec_from_file_location(module_name, runner)
    require(
        specification is not None and specification.loader is not None,
        "skill-routing runner cannot be loaded",
    )
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    previous_bytecode_policy = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        specification.loader.exec_module(module)
    except Exception as error:
        sys.modules.pop(module_name, None)
        raise ReleaseError(f"skill-routing runner cannot be loaded: {error}") from error
    finally:
        sys.dont_write_bytecode = previous_bytecode_policy
    require(
        callable(getattr(module, "validate_evidence", None)),
        "skill-routing runner does not expose validate_evidence",
    )
    return module


def validate_routing_evidence(
    snapshot: Path, evidence_root: Path, expected_candidate: dict[str, str]
) -> dict:
    evidence_root = Path(os.path.abspath(os.fspath(evidence_root)))
    require(evidence_root.is_dir(), "routing evidence must be an external directory")
    evaluator = load_skill_routing_evaluator(snapshot)
    try:
        manifest = evaluator.validate_evidence(
            evidence_root, expected_candidate, require_production=True
        )
    except Exception as error:
        routing_error = getattr(evaluator, "RoutingError", ())
        if isinstance(error, (OSError, json.JSONDecodeError)) or (
            isinstance(routing_error, type) and isinstance(error, routing_error)
        ):
            raise ReleaseError(
                f"skill-routing evidence validation failed: {error}"
            ) from error
        raise
    records = manifest["records"]
    observed_models = sorted(
        {
            model
            for record in records
            for model in [record["init_model"], *record["assistant_models"]]
        }
    )
    return {
        "claim": manifest["claim"],
        "evaluator_sha256": "sha256:"
        + hashlib.sha256(
            (snapshot / "scripts/run_skill_routing_eval.py").read_bytes()
        ).hexdigest(),
        "manifest": {
            "schema_version": manifest["schema_version"],
            "semantic_sha256": canonical_digest(manifest),
            "requested_model": manifest["requested_model"],
            "observed_models": observed_models,
            "counts": manifest["counts"],
            "accounting": {
                "input_tokens": sum(
                    record["usage"]["input_tokens"] for record in records
                ),
                "output_tokens": sum(
                    record["usage"]["output_tokens"] for record in records
                ),
                "total_cost_usd": math.fsum(
                    record["total_cost_usd"] for record in records
                ),
            },
        },
    }


def support_paths(plugin: str) -> tuple[str, ...]:
    return tuple(sorted(COMMON_SUPPORT_PATHS | PLUGIN_SUPPORT_PATHS[plugin]))


def release_test_paths() -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                relative
                for plugin in VALIDATED_PLUGINS
                for relative in support_paths(plugin)
                if Path(relative).parts[:1] == ("tests",)
            }
        )
    )


def all_scope_paths() -> tuple[str, ...]:
    return tuple(
        sorted(
            {f"plugins/{plugin}" for plugin in VALIDATED_PLUGINS}
            | set().union(*(set(support_paths(plugin)) for plugin in VALIDATED_PLUGINS))
        )
    )


def iter_scope_entries(repository: Path):
    for relative in all_scope_paths():
        path = repository / relative
        require(
            path.exists() or path.is_symlink(), f"release input is missing: {relative}"
        )
        entries = [path]
        if path.is_dir() and not path.is_symlink():
            entries.extend(sorted(path.rglob("*"), key=lambda item: item.as_posix()))
        for entry in entries:
            yield entry.relative_to(repository).as_posix(), entry


def validate_scope_entries(repository: Path) -> None:
    seen = set()
    for relative, path in iter_scope_entries(repository):
        if relative in seen:
            continue
        seen.add(relative)
        mode = path.lstat().st_mode
        require(not stat.S_ISLNK(mode), f"release input contains a symlink: {relative}")
        require(
            stat.S_ISDIR(mode) or stat.S_ISREG(mode),
            f"release input contains a special entry: {relative}",
        )
        require(
            "__pycache__" not in Path(relative).parts and not relative.endswith(".pyc"),
            f"release input contains generated Python state: {relative}",
        )


def scope_observation_digest(repository: Path) -> str:
    digest = hashlib.sha256()
    seen = set()
    for relative, path in iter_scope_entries(repository):
        if relative in seen:
            continue
        seen.add(relative)
        metadata = path.lstat()
        digest.update(
            (
                f"{relative}\0{metadata.st_dev}\0{metadata.st_ino}\0"
                f"{metadata.st_mode:o}\0{metadata.st_size}\0"
                f"{metadata.st_mtime_ns}\0{metadata.st_ctime_ns}\0"
            ).encode()
        )
        if stat.S_ISREG(metadata.st_mode):
            before = os.lstat(path)
            content = path.read_bytes()
            after = os.lstat(path)
            require(
                (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                )
                == (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ),
                f"release input changed while reading: {relative}",
            )
            digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def scope_content_digest(repository: Path) -> str:
    """Digest release-scope bytes and metadata without volatile filesystem identity."""
    digest = hashlib.sha256()
    seen = set()
    for relative, path in iter_scope_entries(repository):
        if relative in seen:
            continue
        seen.add(relative)
        metadata = path.lstat()
        kind = "directory" if stat.S_ISDIR(metadata.st_mode) else "file"
        digest.update(f"{relative}\0{kind}\0{metadata.st_mode:o}\0".encode())
        if kind == "file":
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def digest_selected_paths(root: Path, relative_paths: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    entries: dict[str, Path] = {}
    for relative in relative_paths:
        candidate = root / relative
        require(candidate.exists(), f"release contract path is missing: {relative}")
        for path in [
            candidate,
            *(
                sorted(candidate.rglob("*"), key=lambda item: item.as_posix())
                if candidate.is_dir()
                else []
            ),
        ]:
            entry_relative = path.relative_to(root).as_posix()
            metadata = path.lstat()
            require(
                not stat.S_ISLNK(metadata.st_mode),
                f"release contract contains a symlink: {entry_relative}",
            )
            require(
                stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode),
                f"release contract contains a special entry: {entry_relative}",
            )
            entries[entry_relative] = path
    for relative, path in sorted(entries.items()):
        metadata = path.lstat()
        kind = "directory" if stat.S_ISDIR(metadata.st_mode) else "file"
        digest.update(f"{relative}\0{kind}\0{metadata.st_mode:o}\0".encode())
        if kind == "file":
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def release_contract_identity(snapshot: Path) -> dict:
    validators = tuple(
        sorted(
            {
                "scripts/validate_public_release.py",
                "scripts/validate_plugin_runtime_roots.py",
                *VALIDATOR_PATHS.values(),
            }
        )
    )
    tests = release_test_paths()
    return {
        "sha256": digest_selected_paths(snapshot, tuple(sorted({*validators, *tests}))),
        "validators": list(validators),
        "tests": list(tests),
    }


class ReceiptOutput:
    def __init__(self, path: Path, parent_descriptor: int) -> None:
        self.path = path
        self.parent_descriptor = parent_descriptor

    @property
    def name(self) -> str:
        return self.path.name

    def close(self) -> None:
        os.close(self.parent_descriptor)


def open_no_follow_directory(path: Path) -> int:
    """Open an absolute directory by descriptor traversal without symlink hops."""

    return _open_no_follow_directory(path, "release receipt")


def receipt_entry_metadata(parent_descriptor: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None


def read_receipt_entry(parent_descriptor: int, name: str) -> bytes:
    descriptor = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_descriptor,
    )
    with os.fdopen(descriptor, "rb") as file:
        return file.read()


def prepare_receipt_output(repository: Path, output: Path | None) -> ReceiptOutput:
    require(
        output is not None, "production release validation requires --receipt-output"
    )
    output = Path(output)
    require(output.is_absolute(), "release receipt output must be an absolute path")
    output = Path(os.path.abspath(os.fspath(output)))
    require(
        not output.is_relative_to(repository),
        "release receipt output must be outside the release repository",
    )
    try:
        parent_descriptor = open_no_follow_directory(output.parent)
    except OSError as error:
        raise ReleaseError(
            f"release receipt parent path is unavailable or contains a symlink: {error}"
        ) from error
    metadata = receipt_entry_metadata(parent_descriptor, output.name)
    if metadata is not None:
        require(
            not stat.S_ISLNK(metadata.st_mode),
            f"release receipt output path contains a symlink: {output}",
        )
        require(
            stat.S_ISREG(metadata.st_mode),
            "release receipt output must be a regular file",
        )
        require(
            stat.S_IMODE(metadata.st_mode) == 0o600,
            "existing release receipt must have mode 0600",
        )
    return ReceiptOutput(path=output, parent_descriptor=parent_descriptor)


def validate_receipt_output(repository: Path, output: Path | None) -> Path:
    receipt_output = prepare_receipt_output(repository, output)
    try:
        return receipt_output.path
    finally:
        receipt_output.close()


@contextlib.contextmanager
def held_receipt_output(repository: Path, output: Path | None):
    receipt_output = prepare_receipt_output(repository, output)
    try:
        yield receipt_output
    finally:
        receipt_output.close()


def validate_composed_receipt(repository: Path, path: Path | None) -> dict:
    require(
        path is not None,
        "production release validation requires --composed-receipt",
    )
    path = Path(path)
    require(path.is_absolute(), "composed receipt must be an absolute external path")
    path = Path(os.path.abspath(os.fspath(path)))
    require(
        not path.is_relative_to(repository),
        "composed receipt must be outside the release repository",
    )
    require(path.is_file() and not path.is_symlink(), "composed receipt is unavailable")
    receipt = strict_json(path.read_text(encoding="utf-8"), "composed receipt")
    fields = {
        "schema_version",
        "contract",
        "claim",
        "public_candidate_identity",
        "private_commit_oid",
        "private_candidate_identity",
        "producer_package_sha256",
        "producer_registry_sha256",
        "producer_witness_sha256",
        "private_receipt_sha256",
        "private_trust_anchor_sha256",
        "frozen_identity_sha256",
        "private_evidence_bundle_sha256",
        "private_replay_payload_sha256",
        "private_replay_summary_sha256",
        "compatibility_sha256",
        "checks",
        "role_payloads",
        "runtime_isolation",
        "conformance",
        "records",
        "passed",
        "receipt_sha256",
    }
    require(
        isinstance(receipt, dict)
        and set(receipt) == fields
        and type(receipt["schema_version"]) is int
        and receipt["schema_version"] == COMPOSED_SCHEMA_VERSION
        and receipt["contract"] == COMPOSED_CONTRACT
        and receipt["claim"] == COMPOSED_CLAIM
        and receipt["passed"] is True,
        "composed receipt schema or terminal drift",
    )
    for field in ("public_candidate_identity", "private_candidate_identity"):
        require(
            isinstance(receipt[field], str)
            and re.fullmatch(r"[0-9a-f]{64}", receipt[field]) is not None,
            f"composed receipt {field} is malformed",
        )
    for field in (
        "producer_package_sha256",
        "producer_registry_sha256",
        "producer_witness_sha256",
        "private_receipt_sha256",
        "private_trust_anchor_sha256",
        "frozen_identity_sha256",
        "private_evidence_bundle_sha256",
        "private_replay_payload_sha256",
        "private_replay_summary_sha256",
        "compatibility_sha256",
        "receipt_sha256",
    ):
        require(
            isinstance(receipt[field], str) and SHA256.fullmatch(receipt[field]),
            f"composed receipt {field} is malformed",
        )
    require(
        isinstance(receipt["private_commit_oid"], str)
        and GIT_OID.fullmatch(receipt["private_commit_oid"]),
        "composed receipt private_commit_oid is malformed",
    )
    checks = receipt["checks"]
    require(
        isinstance(checks, list) and len(checks) == len(CHECK_ORDER),
        "composed private check inventory is incomplete",
    )
    for index, identifier in enumerate(CHECK_ORDER):
        check = checks[index]
        require(
            isinstance(check, dict)
            and set(check)
            == {
                "id",
                "registry_entry_sha256",
                "test_inventory_sha256",
                "exit_code",
                "terminal",
            }
            and check["id"] == identifier
            and isinstance(check["registry_entry_sha256"], str)
            and SHA256.fullmatch(check["registry_entry_sha256"])
            and isinstance(check["test_inventory_sha256"], str)
            and SHA256.fullmatch(check["test_inventory_sha256"])
            and type(check["exit_code"]) is int
            and check["exit_code"] == 0
            and check["terminal"] == "passed",
            "composed private check is not terminally passed",
        )
    role_payloads = receipt["role_payloads"]
    require(
        isinstance(role_payloads, list) and len(role_payloads) == len(ROLE_ORDER),
        "composed private role inventory is incomplete",
    )
    for index, role_name in enumerate(ROLE_ORDER):
        role = role_payloads[index]
        require(
            isinstance(role, dict)
            and set(role) == {"role", "sha256"}
            and role["role"] == role_name
            and isinstance(role["sha256"], str)
            and SHA256.fullmatch(role["sha256"]),
            "composed private role digest drift",
        )
    runtime = receipt["runtime_isolation"]
    require(
        isinstance(runtime, dict)
        and set(runtime)
        == {
            "backend",
            "policy_sha256",
            "capability_manifest_sha256",
            "host_identity_sha256",
            "kernel_identity_sha256",
            "conformance_sha256",
        },
        "composed runtime/isolation identity drift",
    )
    backend = runtime["backend"]
    require(
        isinstance(backend, dict)
        and set(backend) == {"target", "binary", "version", "sha256", "version_sha256"}
        and (backend["target"], backend["binary"])
        in {
            ("macos-seatbelt", "sandbox-exec"),
            ("linux-bubblewrap", "bwrap"),
            ("wsl2-bubblewrap", "bwrap"),
        }
        and isinstance(backend["version"], str)
        and backend["version"]
        and "/" not in backend["version"]
        and "\\" not in backend["version"]
        and isinstance(backend["sha256"], str)
        and SHA256.fullmatch(backend["sha256"]),
        "composed isolation backend identity drift",
    )
    require(
        isinstance(backend["version_sha256"], str)
        and SHA256.fullmatch(backend["version_sha256"]),
        "composed isolation backend version identity drift",
    )
    for field in (
        "policy_sha256",
        "capability_manifest_sha256",
        "host_identity_sha256",
        "kernel_identity_sha256",
        "conformance_sha256",
    ):
        require(
            isinstance(runtime[field], str) and SHA256.fullmatch(runtime[field]),
            f"composed runtime/isolation {field} is malformed",
        )
    conformance = receipt["conformance"]
    require(
        isinstance(conformance, dict)
        and set(conformance)
        == {"sha256", "sealed_root_count", "read_count", "probe_count"}
        and isinstance(conformance["sha256"], str)
        and SHA256.fullmatch(conformance["sha256"])
        and canonical_digest(conformance) == runtime["conformance_sha256"]
        and all(
            type(conformance[field]) is int and conformance[field] > 0
            for field in ("sealed_root_count", "read_count", "probe_count")
        )
        and conformance["probe_count"] == conformance["read_count"],
        "composed public conformance drift",
    )
    records = receipt["records"]
    require(
        isinstance(records, list)
        and [
            record.get("family") if isinstance(record, dict) else None
            for record in records
        ]
        == list(COMPOSED_FAMILIES),
        "composed receipt was skipped or incomplete",
    )
    for record in records:
        require(
            isinstance(record, dict)
            and set(record) == {"family", "returncode", "artifact", "artifact_sha256"}
            and isinstance(record["family"], str)
            and record["family"]
            and record["returncode"] == 0
            and isinstance(record["artifact"], str)
            and Path(record["artifact"]).name == record["artifact"]
            and isinstance(record["artifact_sha256"], str)
            and SHA256.fullmatch(record["artifact_sha256"]),
            "composed receipt record drift",
        )
        artifact = path.parent / record["artifact"]
        require(
            artifact.is_file()
            and not artifact.is_symlink()
            and "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
            == record["artifact_sha256"],
            "composed receipt artifact drift",
        )
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    require(
        receipt["receipt_sha256"] == canonical_digest(unsigned),
        "composed receipt content digest mismatch",
    )
    require(
        receipt["public_candidate_identity"]
        == candidate_content_identity(repository, error_factory=ReleaseError),
        "composed receipt public candidate identity mismatch",
    )
    public_compatibility = compatibility_bytes(repository)
    try:
        validate_composed_replay_binding(
            receipt,
            public_compatibility=public_compatibility,
        )
    except PrivateEvidenceError as error:
        raise ReleaseError(str(error)) from error
    require(
        receipt["compatibility_sha256"]
        == "sha256:" + hashlib.sha256(public_compatibility).hexdigest(),
        "composed receipt public compatibility mismatch",
    )
    return receipt


def validate_private_provenance_path(
    repository: Path, path: Path | None, label: str
) -> Path:
    require(path is not None, f"production release validation requires {label}")
    candidate = Path(path)
    require(candidate.is_absolute(), f"{label} must be an absolute external path")
    candidate = Path(os.path.abspath(os.fspath(candidate)))
    require(
        not candidate.is_relative_to(repository),
        f"{label} must be outside the release repository",
    )
    require(
        candidate.is_file() and not candidate.is_symlink(),
        f"{label} is unavailable",
    )
    return candidate


def validate_backend_release_evidence_path(
    repository: Path,
    path: Path | None,
    expected_sha256: str | None,
) -> list[dict]:
    evidence = validate_private_provenance_path(
        repository, path, "--backend-release-evidence"
    )
    require(
        isinstance(expected_sha256, str) and SHA256.fullmatch(expected_sha256),
        "production release validation requires --expected-backend-release-evidence-sha256",
    )
    try:
        return validate_public_release_backend_evidence(
            evidence.read_bytes(), expected_sha256=expected_sha256
        )
    except IsolationError as error:
        raise ReleaseError(f"backend release evidence failed: {error}") from error


def write_release_receipt(output: ReceiptOutput | Path, body: dict) -> None:
    if isinstance(output, Path):
        path = Path(os.path.abspath(os.fspath(output)))
        parent_descriptor = open_no_follow_directory(path.parent)
        receipt_output = ReceiptOutput(path, parent_descriptor)
        try:
            write_held_release_receipt(receipt_output, body)
        finally:
            receipt_output.close()
        return
    write_held_release_receipt(output, body)


def write_held_release_receipt(output: ReceiptOutput, body: dict) -> None:
    receipt = body | {"sha256": canonical_digest(body)}
    content = canonical_document(receipt)
    metadata = receipt_entry_metadata(output.parent_descriptor, output.name)
    if metadata is not None:
        require(
            stat.S_ISREG(metadata.st_mode),
            "existing release receipt is unsafe",
        )
        require(
            stat.S_IMODE(metadata.st_mode) == 0o600,
            "existing release receipt must have mode 0600",
        )
        require(
            read_receipt_entry(output.parent_descriptor, output.name) == content,
            "existing release receipt mismatch",
        )
        return
    temporary_name = f".{output.name}.{secrets.token_hex(16)}"
    descriptor = os.open(
        temporary_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=output.parent_descriptor,
    )
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        try:
            os.link(
                temporary_name,
                output.name,
                src_dir_fd=output.parent_descriptor,
                dst_dir_fd=output.parent_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError as error:
            raise ReleaseError(
                "release receipt output appeared before publication"
            ) from error
        os.unlink(temporary_name, dir_fd=output.parent_descriptor)
        os.fsync(output.parent_descriptor)
    finally:
        try:
            os.unlink(temporary_name, dir_fd=output.parent_descriptor)
        except FileNotFoundError:
            pass


def deterministic_tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    entries = [root, *sorted(root.rglob("*"), key=lambda item: item.as_posix())]
    for path in entries:
        relative = "." if path == root else path.relative_to(root).as_posix()
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            kind = "directory"
            content = b""
        elif stat.S_ISREG(metadata.st_mode):
            kind = "file"
            content = path.read_bytes()
        else:
            raise ReleaseError(
                f"candidate tree contains an unsupported entry: {relative}"
            )
        digest.update(f"{relative}\0{kind}\0{metadata.st_mode:o}\0".encode())
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def copy_release_scope(source: Path, destination: Path) -> None:
    for relative in all_scope_paths():
        source_path = source / relative
        destination_path = destination / relative
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if source_path.is_dir():
            shutil.copytree(source_path, destination_path, symlinks=True)
        else:
            shutil.copy2(source_path, destination_path, follow_symlinks=False)
    validate_scope_entries(destination)


def snapshot_git_candidate(
    repository: Path, destination: Path, candidate: dict[str, str]
) -> None:
    """Materialize the exact clean Git candidate into a self-contained snapshot."""

    result = subprocess.run(
        ["git", "archive", "--format=tar", candidate["revision"]],
        cwd=repository,
        capture_output=True,
        check=False,
    )
    require(
        result.returncode == 0,
        result.stderr.decode(errors="replace").strip()
        or "cannot archive the Git candidate",
    )
    archive = result.stdout
    require(
        "sha256:" + hashlib.sha256(archive).hexdigest() == candidate["archive_sha256"],
        "Git candidate archive changed while the snapshot was created",
    )
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
            require(member.isfile(), "Git candidate archive contains an unsafe entry")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            require(source is not None, "Git candidate archive member is unreadable")
            target.write_bytes(source.read())
            target.chmod(member.mode & 0o777)

    tree = subprocess.run(
        ["git", "ls-tree", "-rz", "--full-tree", candidate["revision"]],
        cwd=repository,
        capture_output=True,
        check=False,
    )
    require(tree.returncode == 0, "cannot inspect the Git candidate tree modes")
    file_modes: dict[str, int] = {}
    for record in tree.stdout.split(b"\0"):
        if not record:
            continue
        header, raw_path = record.split(b"\t", maxsplit=1)
        mode, kind, _object = header.decode("ascii").split(maxsplit=2)
        require(kind == "blob", "Git candidate tree contains a non-file entry")
        relative = raw_path.decode("utf-8")
        file_modes[relative] = int(mode, 8) & 0o777
    # Git does not represent empty directories.  Preserve their required scope
    # presence, but assign the canonical directory mode rather than importing
    # mutable source metadata.
    for _relative, source_path in iter_scope_entries(repository):
        if source_path.is_dir():
            (destination / _relative).mkdir(parents=True, exist_ok=True)
    for relative, target in iter_scope_entries(destination):
        metadata = target.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            target.chmod(0o755)
        else:
            require(
                relative in file_modes,
                "Git candidate snapshot contains an untracked release entry",
            )
            target.chmod(file_modes[relative])

    validate_scope_entries(destination)
    for arguments in (("init", "--quiet"), ("add", "--force", "--all")):
        result = subprocess.run(
            ["git", *arguments], cwd=destination, capture_output=True, check=False
        )
        require(
            result.returncode == 0,
            result.stderr.decode(errors="replace").strip()
            or f"cannot prepare the Git candidate snapshot: git {' '.join(arguments)}",
        )


def validate_marketplace(repository: Path) -> None:
    path = repository / ".claude-plugin/marketplace.json"
    marketplace = strict_json(path.read_text(encoding="utf-8"), "marketplace")
    require(isinstance(marketplace, dict), "marketplace must be an object")
    entries = marketplace.get("plugins")
    require(isinstance(entries, list), "marketplace plugins must be an array")
    parsed = {}
    for entry in entries:
        require(
            isinstance(entry, dict) and set(entry) == {"name", "source", "category"},
            "marketplace plugin entry shape drift",
        )
        name = entry["name"]
        require(
            isinstance(name, str) and name not in parsed,
            "marketplace plugin names must be unique",
        )
        require(
            entry["category"] == "developer-tools",
            f"marketplace category drift: {name}",
        )
        parsed[name] = entry["source"]
    require(parsed == MARKETPLACE_PLUGINS, "marketplace plugin inventory drift")


def validate_repository_projection(repository: Path) -> None:
    readme = (repository / "README.md").read_text(encoding="utf-8")
    for plugin in MARKETPLACE_PLUGINS:
        require(
            f"`plugins/{plugin}/`" in readme,
            f"repository README omits public plugin: {plugin}",
        )


def candidate_identities(snapshot: Path) -> dict:
    identities = {}
    for plugin in VALIDATED_PLUGINS:
        plugin_digest = deterministic_tree_digest(snapshot / "plugins" / plugin)
        composite = hashlib.sha256()
        composite.update(f"plugin\0{plugin_digest}\0".encode())
        for relative in support_paths(plugin):
            path = snapshot / relative
            metadata = path.lstat()
            require(
                stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode),
                f"support path must be a regular file or directory: {relative}",
            )
            kind = "directory" if stat.S_ISDIR(metadata.st_mode) else "file"
            digest = (
                deterministic_tree_digest(path)
                if kind == "directory"
                else hashlib.sha256(path.read_bytes()).hexdigest()
            )
            composite.update(
                f"{relative}\0{kind}\0{metadata.st_mode:o}\0{digest}\0".encode()
            )
        identities[plugin] = {
            "plugin_sha256": plugin_digest,
            "composite_sha256": composite.hexdigest(),
        }
    return {"schema_version": 1, "plugins": identities}


def validate_expected_identities(expected: dict, actual: dict) -> None:
    require(
        isinstance(expected, dict)
        and set(expected) == {"schema_version", "plugins"}
        and type(expected["schema_version"]) is int
        and expected["schema_version"] == 1,
        "expected identity document schema drift",
    )
    plugins = expected["plugins"]
    require(
        isinstance(plugins, dict) and set(plugins) == set(VALIDATED_PLUGINS),
        "expected identity plugin inventory drift",
    )
    for plugin, identity in plugins.items():
        require(
            isinstance(identity, dict)
            and set(identity) == {"plugin_sha256", "composite_sha256"}
            and all(
                isinstance(value, str)
                and len(value) == 64
                and all(character in "0123456789abcdef" for character in value)
                for value in identity.values()
            ),
            f"expected identity shape drift: {plugin}",
        )
    require(expected == actual, "frozen release identity mismatch")


def run_contract_validators(
    snapshot: Path, plugin_eval_executable: Path | None = None
) -> dict:
    identity = release_contract_identity(snapshot)
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    if plugin_eval_executable is not None:
        environment["PLUGIN_EVAL_TEST_EXECUTABLE"] = str(plugin_eval_executable)
    runtime_root_validator = snapshot / "scripts/validate_plugin_runtime_roots.py"
    result = subprocess.run(
        [sys.executable, str(runtime_root_validator), str(snapshot)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    require(
        result.returncode == 0,
        f"plugin runtime-root validator failed:\n{result.stdout}{result.stderr}",
    )
    for plugin in VALIDATED_PLUGINS:
        result = subprocess.run(
            [sys.executable, str(snapshot / VALIDATOR_PATHS[plugin]), str(snapshot)],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
        require(
            result.returncode == 0,
            f"{plugin} validator failed:\n{result.stdout}{result.stderr}",
        )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            *release_test_paths(),
        ],
        cwd=snapshot,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=600,
    )
    require(
        result.returncode == 0,
        f"release-owned unit suites failed:\n{result.stdout}{result.stderr}",
    )
    require(
        release_contract_identity(snapshot) == identity,
        "release-owned validator or test contract changed while validation ran",
    )
    return identity


def run_plugin_evals(
    snapshot: Path,
    executable: Path,
    *,
    node_executable: Path | None = None,
) -> dict[str, dict]:
    """Run all evaluations from one verified, private copy of the runtime."""

    policy = load_plugin_eval_policy(snapshot)
    with tempfile.TemporaryDirectory(prefix="pinned-plugin-eval-") as temporary:
        temporary_root = Path(temporary)
        environment = private_plugin_eval_environment(temporary_root / "environment")
        interpreter = resolve_node_interpreter(node_executable, environment)
        pinned_executable, runtime = copy_pinned_plugin_eval_runtime(
            executable, policy, temporary_root / "runtime"
        )
        runtime = {
            **runtime,
            "interpreter": public_node_interpreter_evidence(interpreter),
        }
        return run_pinned_plugin_evals(
            snapshot,
            pinned_executable,
            policy,
            runtime,
            environment,
            node_interpreter=interpreter,
        )


def run_pinned_plugin_evals(
    snapshot: Path,
    executable: Path,
    policy: dict[str, object],
    runtime: dict[str, object],
    environment: dict[str, str],
    *,
    node_interpreter: dict[str, str],
) -> dict[str, dict]:
    interpreter = runtime.get("interpreter")
    require(
        isinstance(interpreter, dict)
        and set(interpreter) == {"sha256", "version", "coverage", "limitations"}
        and isinstance(interpreter["sha256"], str)
        and isinstance(interpreter["version"], str)
        and interpreter["coverage"] == "main-executable-bytes-only"
        and interpreter["limitations"]
        == [
            "dynamic-loader-and-shared-library-bytes-are-not-bound",
            "pathname-exec-time-ABA-is-not-bound",
        ],
        "plugin-eval Node interpreter identity is malformed",
    )
    require(
        set(node_interpreter) == {"path", "sha256", "version"}
        and node_interpreter["sha256"] == interpreter["sha256"]
        and node_interpreter["version"] == interpreter["version"],
        "internal plugin-eval Node interpreter binding is malformed",
    )
    evidence = {}
    for plugin in SKILL_PLUGINS:
        revalidate_node_interpreter(node_interpreter)
        result = subprocess.run(
            [
                node_interpreter["path"],
                str(executable),
                "analyze",
                str(snapshot / "plugins" / plugin),
                "--format",
                "json",
            ],
            text=True,
            capture_output=True,
            check=False,
            cwd=snapshot,
            env=environment,
            timeout=120,
        )
        revalidate_node_interpreter(node_interpreter)
        require(
            result.returncode == 0,
            f"{plugin} plugin-eval invocation failed:\n{result.stdout}{result.stderr}",
        )
        report = strict_json(result.stdout, f"{plugin} plugin-eval report")
        require(
            isinstance(report, dict), f"{plugin} plugin-eval report must be an object"
        )
        validated = validate_plugin_eval_report(plugin, report, policy, snapshot)
        deductions = validated["deductions"]
        non_budget_findings = []
        warnings = []
        advisories = []
        blocking_failures = []
        for finding in deductions:
            if finding["category"] == "budget":
                native = policy["native_budget_findings"].get(finding["id"])
                require(
                    native is not None,
                    f"{plugin} unknown plugin-eval budget finding: {finding['id']}",
                )
                require(
                    finding == native,
                    f"{plugin} plugin-eval native budget finding drift: "
                    f"{finding['id']}",
                )
                continue
            non_budget_findings.append(finding)
            failed = finding["status"] == "fail" or finding["severity"] == "error"
            if failed:
                if advisory_applies(plugin, finding, policy):
                    advisories.append(finding["id"])
                else:
                    blocking_failures.append(f"{finding['id']}: {finding['message']}")
            elif finding["status"] == "warn":
                warnings.append(finding["id"])

        budget_projection = {}
        for metric in BUDGET_METRICS:
            metric_evidence = validated["budgets"][metric]
            value = metric_evidence["value"]
            thresholds = policy["thresholds"][metric]
            band = plugin_eval_budget_band(value, thresholds)
            decision = "pass"
            exception = None
            if band == "excessive":
                exception = runtime_cost_advisory(
                    plugin,
                    metric,
                    value,
                    metric_evidence["components"],
                    policy,
                    snapshot,
                )
                if exception is None:
                    blocking_failures.append(
                        f"{metric}-budget-high: {value} exceeds release heavyMax "
                        f"{thresholds['heavyMax']}"
                    )
                    decision = "block"
                else:
                    advisories.append(exception["id"])
                    decision = "advisory"
            budget_projection[metric] = {
                "value": value,
                "band": band,
                "decision": decision,
                "exception_components": (
                    [] if exception is None else exception["components"]
                ),
            }
        require(
            not blocking_failures,
            f"{plugin} plugin-eval has blocking checks: "
            + ", ".join(blocking_failures),
        )
        warnings = sorted(warnings)
        advisories = sorted(advisories)
        policy_projection = {
            "schema_version": 2,
            "plugin": plugin,
            "target_kind": "plugin",
            "outcome": "pass",
            "policy_sha256": policy["policy_sha256"],
            "calibration_manifest_sha256": policy["calibration_manifest_sha256"],
            "tool_runtime": runtime,
            "budgets": budget_projection,
            "non_budget_findings": sorted(
                non_budget_findings, key=lambda finding: finding["id"]
            ),
            "warnings": warnings,
            "advisories": advisories,
        }
        evidence[plugin] = {
            "analyzed_plugin": {
                "entry_path": f"plugins/{plugin}/.codex-plugin/plugin.json",
                "name": plugin,
                "path": f"plugins/{plugin}",
                "target_kind": "plugin",
            },
            "outcome": "pass",
            "warnings": warnings,
            "advisories": advisories,
            "policy_sha256": policy["policy_sha256"],
            "calibration_manifest_sha256": policy["calibration_manifest_sha256"],
            "policy_projection_sha256": canonical_digest(policy_projection),
            "tool_runtime": runtime,
        }
    return evidence


def validate_release(
    repository: Path,
    *,
    expected: dict | None = None,
    run_contracts: bool = True,
    plugin_eval_executable: Path | None = None,
    node_executable: Path | None = None,
    routing_evidence: Path | None = None,
    receipt_output: Path | None = None,
    composed_receipt: Path | None = None,
    private_producer_witness: Path | None = None,
    private_producer_registry: Path | None = None,
    expected_frozen_private_identity_sha256: str | None = None,
    expected_private_commit_oid: str | None = None,
    expected_private_producer_package_sha256: str | None = None,
    expected_public_candidate_sha256: str | None = None,
    backend_release_evidence: Path | None = None,
    expected_backend_release_evidence_sha256: str | None = None,
    after_snapshot: Callable[[], None] | None = None,
    source_stage_validator: Callable[[Path], None] | None = None,
) -> dict:
    repository = lexical_repository(repository)
    if plugin_eval_executable is not None:
        plugin_eval_executable = Path(
            os.path.abspath(os.fspath(plugin_eval_executable))
        )
    expected_candidate: dict[str, str] | None = None
    evidence_root: Path | None = None
    composed_summary: dict | None = None
    if run_contracts:
        require(
            routing_evidence is not None,
            "production release validation requires external skill-routing evidence",
        )
        require(
            plugin_eval_executable is not None,
            "production release validation requires a pinned plugin-eval executable",
        )
        require(
            source_stage_validator is None,
            "production release validation does not accept source-stage validators",
        )
        # Preserve the public argument-error contract.  The descriptor retained
        # below repeats this validation immediately before long-running work.
        validate_receipt_output(repository, receipt_output)
        require(
            composed_receipt is not None,
            "production release validation requires --composed-receipt",
        )
        require(
            all(
                value is not None
                for value in (
                    private_producer_witness,
                    private_producer_registry,
                    expected_frozen_private_identity_sha256,
                    expected_private_commit_oid,
                    expected_private_producer_package_sha256,
                    expected_public_candidate_sha256,
                )
            ),
            "production release validation requires public-safe private provenance roots",
        )
        evidence_root = Path(os.path.abspath(os.fspath(routing_evidence))).resolve()
        require(
            not evidence_root.is_relative_to(repository),
            "routing evidence must be outside the release repository",
        )
    else:
        require(
            node_executable is None,
            "source-stage validation does not accept a Node executable",
        )
        require(
            receipt_output is None,
            "source-stage validation does not accept a release receipt output",
        )
        require(
            composed_receipt is None,
            "source-stage validation does not accept composed evidence",
        )
        require(
            all(
                value is None
                for value in (
                    private_producer_witness,
                    private_producer_registry,
                    expected_frozen_private_identity_sha256,
                    expected_private_commit_oid,
                    expected_private_producer_package_sha256,
                    expected_public_candidate_sha256,
                )
            ),
            "source-stage validation does not accept private provenance evidence",
        )
    validate_scope_entries(repository)
    source_observation = scope_observation_digest(repository)
    source_content = scope_content_digest(repository)
    if run_contracts:
        expected_candidate = git_candidate_identity(repository)
    temporary_parent = Path(tempfile.gettempdir()).resolve()
    receipt_context = (
        held_receipt_output(repository, receipt_output)
        if run_contracts
        else contextlib.nullcontext(None)
    )
    with (
        receipt_context as receipt_target,
        tempfile.TemporaryDirectory(
            prefix="public-plugin-release-", dir=temporary_parent
        ) as temporary,
    ):
        snapshot = Path(temporary) / "repository"
        if run_contracts:
            assert expected_candidate is not None
            snapshot_git_candidate(repository, snapshot, expected_candidate)
        else:
            copy_release_scope(repository, snapshot)
        if after_snapshot is not None:
            after_snapshot()
        require(
            scope_observation_digest(repository) == source_observation,
            "release input changed while the private snapshot was created",
        )
        validate_marketplace(snapshot)
        validate_repository_projection(snapshot)
        snapshot_observation = scope_observation_digest(snapshot)
        snapshot_content = scope_content_digest(snapshot)
        require(
            snapshot_content == source_content,
            "private snapshot content differs from the release input",
        )
        contract_identity = None
        routing_summary = None
        if run_contracts:
            composed_summary = validate_composed_receipt(snapshot, composed_receipt)
            backend_proofs = validate_backend_release_evidence_path(
                repository,
                backend_release_evidence,
                expected_backend_release_evidence_sha256,
            )
            try:
                validate_runtime_backend_release_evidence(
                    composed_summary["runtime_isolation"],
                    backend_proofs,
                    public_candidate_identity=composed_summary[
                        "public_candidate_identity"
                    ],
                )
            except IsolationError as error:
                raise ReleaseError(
                    f"backend release evidence failed: {error}"
                ) from error
            try:
                verify_public_release_evidence(
                    composed_receipt=composed_summary,
                    producer_witness_path=validate_private_provenance_path(
                        repository,
                        private_producer_witness,
                        "--private-producer-witness",
                    ),
                    producer_registry_path=validate_private_provenance_path(
                        repository,
                        private_producer_registry,
                        "--private-producer-registry",
                    ),
                    expected_frozen_identity_sha256=str(
                        expected_frozen_private_identity_sha256
                    ),
                    expected_commit_oid=str(expected_private_commit_oid),
                    expected_producer_package_sha256=str(
                        expected_private_producer_package_sha256
                    ),
                    public_root=snapshot,
                    expected_public_candidate_sha256=str(
                        expected_public_candidate_sha256
                    ),
                )
            except PrivateEvidenceError as error:
                raise ReleaseError(
                    f"private provenance validation failed: {error}"
                ) from error
            contract_identity = run_contract_validators(
                snapshot, plugin_eval_executable
            )
            if contract_identity is None:
                contract_identity = release_contract_identity(snapshot)
            assert evidence_root is not None and expected_candidate is not None
            routing_summary = validate_routing_evidence(
                snapshot, evidence_root, expected_candidate
            )
        plugin_eval_evidence = None
        if plugin_eval_executable is not None:
            plugin_eval_evidence = run_plugin_evals(
                snapshot,
                plugin_eval_executable,
                node_executable=node_executable,
            )
        if source_stage_validator is not None:
            source_stage_validator(snapshot)
        require(
            scope_observation_digest(snapshot) == snapshot_observation,
            "private snapshot changed while contract validators ran",
        )
        identities = candidate_identities(snapshot)
        require(
            scope_observation_digest(snapshot) == snapshot_observation,
            "private snapshot changed while release identities were derived",
        )
        require(
            scope_observation_digest(repository) == source_observation,
            "release input changed while the private snapshot was validated",
        )
        if run_contracts:
            require(
                git_candidate_identity(repository) == expected_candidate,
                "Git candidate changed while release validation ran",
            )
        if expected is not None:
            validate_expected_identities(expected, identities)
        if run_contracts:
            assert (
                receipt_target is not None
                and expected_candidate is not None
                and contract_identity is not None
                and routing_summary is not None
                and plugin_eval_evidence is not None
                and composed_summary is not None
            )
            expected_summary = (
                None
                if expected is None
                else {
                    "semantic_sha256": canonical_digest(expected),
                    "identities": expected,
                }
            )
            write_release_receipt(
                receipt_target,
                {
                    "schema_version": 5,
                    "claim": routing_summary["claim"],
                    "candidate": expected_candidate,
                    "release_scope": {
                        "source_sha256": source_content,
                        "snapshot_sha256": snapshot_content,
                    },
                    "routing": routing_summary,
                    "composed": composed_summary,
                    "plugin_evals": plugin_eval_evidence,
                    "release_contract": contract_identity,
                    "expected_identities": expected_summary,
                    "identities": identities,
                },
            )
        if plugin_eval_evidence is not None:
            for plugin, evidence in plugin_eval_evidence.items():
                warnings = ",".join(evidence["warnings"]) or "none"
                advisories = ",".join(evidence["advisories"]) or "none"
                print(
                    f"plugin-eval {plugin}: outcome={evidence['outcome']} "
                    f"warnings={warnings} advisories={advisories}",
                    file=sys.stderr,
                )
        return identities


def run_source_stage_validators(snapshot: Path) -> None:
    """Run source-stage validators against the retained candidate snapshot only."""

    for plugin in VALIDATED_PLUGINS:
        result = subprocess.run(
            [
                sys.executable,
                str(snapshot / VALIDATOR_PATHS[plugin]),
                str(snapshot),
                *SOURCE_STAGE_VALIDATOR_FLAGS[plugin],
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
        require(
            result.returncode == 0,
            f"{plugin} source-stage validator failed:\n{result.stdout}{result.stderr}",
        )


def validate_source_stage(repository: Path) -> dict:
    """Validate one retained candidate snapshot without release-only evidence gates."""

    identities = validate_release(
        repository,
        run_contracts=False,
        source_stage_validator=run_source_stage_validators,
    )
    return identities


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", nargs="?", type=Path, default=Path.cwd())
    parser.add_argument("--expected-identities", type=Path)
    parser.add_argument("--plugin-eval", type=Path)
    parser.add_argument("--node", type=Path)
    parser.add_argument("--routing-evidence", type=Path)
    parser.add_argument("--receipt-output", type=Path)
    parser.add_argument("--composed-receipt", type=Path)
    parser.add_argument("--private-producer-witness", type=Path)
    parser.add_argument("--private-producer-registry", type=Path)
    parser.add_argument("--expected-frozen-private-identity-sha256")
    parser.add_argument("--expected-private-commit-oid")
    parser.add_argument("--expected-private-producer-package-sha256")
    parser.add_argument("--expected-public-candidate-sha256")
    parser.add_argument("--backend-release-evidence", type=Path)
    parser.add_argument("--expected-backend-release-evidence-sha256")
    parser.add_argument("--source-stage", action="store_true")
    arguments = parser.parse_args()
    try:
        expected = None
        if arguments.source_stage:
            require(
                arguments.expected_identities is None
                and arguments.plugin_eval is None
                and arguments.node is None
                and arguments.routing_evidence is None
                and arguments.receipt_output is None,
                "source-stage validation does not accept release identity, "
                "plugin-eval, node, routing-evidence, composed-receipt, or receipt-output inputs",
            )
            require(
                arguments.composed_receipt is None,
                "source-stage validation does not accept composed evidence",
            )
            require(
                all(
                    value is None
                    for value in (
                        arguments.private_producer_witness,
                        arguments.private_producer_registry,
                        arguments.expected_frozen_private_identity_sha256,
                        arguments.expected_private_commit_oid,
                        arguments.expected_private_producer_package_sha256,
                        arguments.expected_public_candidate_sha256,
                        arguments.backend_release_evidence,
                        arguments.expected_backend_release_evidence_sha256,
                    )
                ),
                "source-stage validation does not accept private provenance evidence",
            )
            identities = validate_source_stage(arguments.repository)
            print(json.dumps(identities, indent=2, sort_keys=True))
            return 0
        require(
            arguments.routing_evidence is not None,
            "production release validation requires --routing-evidence",
        )
        require(
            arguments.receipt_output is not None,
            "production release validation requires --receipt-output",
        )
        require(
            arguments.composed_receipt is not None,
            "production release validation requires --composed-receipt",
        )
        if arguments.expected_identities is not None:
            expected = strict_json(
                arguments.expected_identities.read_text(encoding="utf-8"),
                "expected identities",
            )
            reject_non_finite(expected, "expected identities")
        plugin_eval = arguments.plugin_eval
        if plugin_eval is None:
            discovered = shutil.which("plugin-eval")
            require(discovered is not None, "plugin-eval executable is unavailable")
            plugin_eval = Path(discovered)
        require(
            plugin_eval.is_file() and os.access(plugin_eval, os.X_OK),
            "plugin-eval must be an executable file",
        )
        identities = validate_release(
            arguments.repository,
            expected=expected,
            plugin_eval_executable=plugin_eval,
            node_executable=arguments.node,
            routing_evidence=arguments.routing_evidence,
            receipt_output=arguments.receipt_output,
            composed_receipt=arguments.composed_receipt,
            private_producer_witness=arguments.private_producer_witness,
            private_producer_registry=arguments.private_producer_registry,
            expected_frozen_private_identity_sha256=(
                arguments.expected_frozen_private_identity_sha256
            ),
            expected_private_commit_oid=arguments.expected_private_commit_oid,
            expected_private_producer_package_sha256=(
                arguments.expected_private_producer_package_sha256
            ),
            expected_public_candidate_sha256=(
                arguments.expected_public_candidate_sha256
            ),
            backend_release_evidence=arguments.backend_release_evidence,
            expected_backend_release_evidence_sha256=(
                arguments.expected_backend_release_evidence_sha256
            ),
        )
    except (
        ReleaseError,
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
        subprocess.SubprocessError,
        UnicodeDecodeError,
    ) as error:
        print(f"Public plugin release validation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(identities, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
