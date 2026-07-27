#!/usr/bin/env python3
"""Validate the code-only Task Witness package boundary."""

from __future__ import annotations

import hashlib
import json
import math
import re
import stat
import sys
from pathlib import Path

PLUGIN_RELATIVE = Path("plugins/task-witness")
SOURCE_SHAPE_RECORD_RELATIVE = Path("release/task-witness/source-shape-review.json")
EXPECTED_DIRECTORIES = {".claude-plugin", ".codex-plugin", "launcher", "runtime"}
LAUNCHER_FILES = {"launcher/task_witness_launch.py"}
RUNTIME_FILES = {
    "runtime/task_witness.py",
    "runtime/canonical.py",
    "runtime/bundle_io.py",
    "runtime/trust.py",
}
SOURCE_SHAPE_PATHS = tuple(
    sorted(
        (PLUGIN_RELATIVE / relative).as_posix()
        for relative in RUNTIME_FILES | LAUNCHER_FILES
    )
)
SOURCE_SHAPE_MEASUREMENT = (
    "python-nonblank-noncomment-lines-v1+ordered-source-byte-identity-v1"
)
SOURCE_SHAPE_REBASELINE_REQUIREMENT = "independent-source-shape-review"
SOURCE_BYTE_IDENTITY_FRAMING = "path-utf8-nul-sha256-hex-nul-v1"
CLIENT_ACCEPTANCE_CONTRACT = (
    "Only a canonical client invocation is acceptable. The client itself invokes "
    "the canonical Task Witness subprocess with deployment-owned exact `argv` and a "
    "scrubbed environment. It accepts the result only if that child exits with "
    "status 0, emits exactly one schema-valid canonical envelope, and returns exactly "
    "the expected complete anchor. The anchor does not authenticate invocation "
    "provenance or arbitrary caller ambient state."
)
CANONICAL_EXECUTOR_CONTRACT = (
    "`main()` is Task Witness's only supported subprocess entry point. The launcher "
    "defensively rejects noncanonical CPython warning options, implementation "
    "options, and semantic flags before loading payloads. It cannot prove the "
    "caller's exact argv, environment, cwd, stdin, inherited descriptors, or timeout; "
    "the canonical client and deployment own those invocation conditions."
)
DEPLOYMENT_BOUNDARY_CONTRACT = (
    "Task Witness's filesystem checks operate within a cooperative same-EUID "
    "deployment boundary. They cannot adversarially protect the launcher, active "
    "record, or generation state from an actor with the same EUID who can replace "
    "those files. Deployment policy and external deployment receipts own that "
    "trust; a successful envelope is not a deployment receipt."
)
SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")
EXPECTED_FILES = {
    ".claude-plugin/plugin.json",
    ".codex-plugin/plugin.json",
} | RUNTIME_FILES
EXPECTED_FILES |= LAUNCHER_FILES
SHARED_IDENTITY_FIELDS = {
    "name",
    "version",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
}
EXPECTED_CLAUDE_MANIFEST = {
    "name": "task-witness",
    "displayName": "Task Witness",
    "version": "0.1.0",
    "description": "Task Witness launches exact-byte-pinned registered validators against retained operator trust.",
    "author": {"name": "Ivan D Vasin", "url": "https://github.com/nisavid"},
    "homepage": "https://github.com/nisavid/agents/tree/main/plugins/task-witness",
    "repository": "https://github.com/nisavid/agents",
    "license": "MIT",
    "keywords": ["evidence", "provenance", "validation", "trust"],
}
EXPECTED_CODEX_MANIFEST = {
    "name": "task-witness",
    "version": "0.1.0",
    "description": "Run exact-byte-pinned registered validators against an operator trust snapshot.",
    "author": {"name": "Ivan D Vasin", "url": "https://github.com/nisavid"},
    "homepage": "https://github.com/nisavid/agents/tree/main/plugins/task-witness",
    "repository": "https://github.com/nisavid/agents",
    "license": "MIT",
    "keywords": ["evidence", "provenance", "validation", "trust"],
    "interface": {
        "displayName": "Task Witness",
        "shortDescription": "Validate registered task-evidence bundles.",
        "longDescription": "Task Witness runs exact-byte-pinned, operator-approved validators. Validators are trusted full-process Python code with ambient user authority; Task Witness grants no workflow authority and is not a sandbox.",
        "developerName": "Ivan D Vasin",
        "category": "Developer Tools",
        "capabilities": ["Validation"],
        "websiteURL": "https://github.com/nisavid/agents/tree/main/plugins/task-witness",
    },
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
    claude = load_json(plugin / ".claude-plugin/plugin.json", "Claude manifest")
    codex = load_json(plugin / ".codex-plugin/plugin.json", "Codex manifest")
    if {field: claude.get(field) for field in SHARED_IDENTITY_FIELDS} != {
        field: codex.get(field) for field in SHARED_IDENTITY_FIELDS
    }:
        raise ValueError("Task Witness manifest shared identity drift")
    if claude != EXPECTED_CLAUDE_MANIFEST:
        raise ValueError("Task Witness Claude manifest contract drift")
    if codex != EXPECTED_CODEX_MANIFEST:
        raise ValueError("Task Witness Codex manifest contract drift")


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
    if not isinstance(value, dict):
        raise ValueError("Task Witness source-shape review context must be an object")
    require_exact_fields(
        value,
        {"protocol", "architecture", "alternatives", "external_review"},
        "Task Witness source-shape review context",
    )

    protocol = value["protocol"]
    if not isinstance(protocol, dict):
        raise ValueError(
            "Task Witness source-shape protocol identity must be an object"
        )
    require_exact_fields(
        protocol,
        {"launch_envelope_contract", "canonical_projection_contract"},
        "Task Witness source-shape protocol identity",
    )
    if protocol != {
        "launch_envelope_contract": "task-witness-launch-envelope-v1",
        "canonical_projection_contract": "task-witness-canonical-projection-v2",
    }:
        raise ValueError("Task Witness source-shape protocol identity drift")

    architecture = value["architecture"]
    if not isinstance(architecture, dict):
        raise ValueError(
            "Task Witness source-shape architecture rationale must be an object"
        )
    require_exact_fields(
        architecture,
        {
            "rationale",
            "validator_execution_model",
            "client_acceptance",
            "canonical_executor",
            "deployment_boundary",
        },
        "Task Witness source-shape architecture rationale",
    )
    if not all(isinstance(item, str) and item for item in architecture.values()):
        raise ValueError("Task Witness source-shape architecture rationale is invalid")
    if architecture["client_acceptance"] != CLIENT_ACCEPTANCE_CONTRACT:
        raise ValueError("Task Witness source-shape client acceptance contract drift")
    if architecture["canonical_executor"] != CANONICAL_EXECUTOR_CONTRACT:
        raise ValueError("Task Witness canonical executor contract drift")
    if architecture["deployment_boundary"] != DEPLOYMENT_BOUNDARY_CONTRACT:
        raise ValueError("Task Witness source-shape deployment boundary drift")

    alternatives = value["alternatives"]
    if not isinstance(alternatives, list) or not alternatives:
        raise ValueError(
            "Task Witness source-shape alternatives must be a non-empty list"
        )
    for index, alternative in enumerate(alternatives):
        if not isinstance(alternative, dict):
            raise ValueError(
                f"Task Witness source-shape alternative {index} must be an object"
            )
        require_exact_fields(
            alternative,
            {"name", "status", "reason"},
            f"Task Witness source-shape alternative {index}",
        )
        if (
            not all(isinstance(item, str) and item for item in alternative.values())
            or alternative["status"] != "rejected"
        ):
            raise ValueError(
                f"Task Witness source-shape alternative {index} is invalid"
            )

    external_review = value["external_review"]
    if not isinstance(external_review, dict):
        raise ValueError("Task Witness external review boundary must be an object")
    require_exact_fields(
        external_review,
        {"authenticity_proof", "record_role"},
        "Task Witness external review boundary",
    )
    if external_review != {
        "authenticity_proof": "external-frozen-review-evidence",
        "record_role": "source-shape-measurement-not-review-authentication",
    }:
        raise ValueError("Task Witness external review boundary drift")


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
        != 2
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
        {"module_source_lines", "aggregate_source_lines"},
        "Task Witness reviewed source shape",
    )
    module_source_lines = reviewed_shape["module_source_lines"]
    if not isinstance(module_source_lines, dict):
        raise ValueError("Task Witness reviewed module source lines must be an object")
    if set(module_source_lines) != set(SOURCE_SHAPE_PATHS):
        raise ValueError("Task Witness reviewed module source path inventory drift")
    reviewed_module_lines = {
        path: require_nonnegative_integer(
            module_source_lines[path],
            f"Task Witness reviewed source line count for {path}",
        )
        for path in SOURCE_SHAPE_PATHS
    }
    reviewed_aggregate = require_nonnegative_integer(
        reviewed_shape["aggregate_source_lines"],
        "Task Witness reviewed aggregate source line count",
    )
    if reviewed_aggregate != sum(reviewed_module_lines.values()):
        raise ValueError(
            "Task Witness reviewed aggregate source line count is inconsistent"
        )

    recorded_identity = record["source_byte_identity"]
    if not isinstance(recorded_identity, dict):
        raise ValueError("Task Witness source byte identity must be an object")
    require_exact_fields(
        recorded_identity,
        {"algorithm", "framing", "entries", "aggregate_sha256"},
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
    if [path for path, _ in parsed_entries] != list(SOURCE_SHAPE_PATHS):
        raise ValueError("Task Witness ordered source-byte identity drift")
    aggregate_sha256 = require_sha256_hex(
        recorded_identity["aggregate_sha256"],
        "Task Witness source byte identity aggregate digest",
    )
    if source_byte_identity(parsed_entries) != aggregate_sha256:
        raise ValueError("Task Witness source byte identity aggregate drift")

    tripwires = record["tripwires"]
    if not isinstance(tripwires, dict):
        raise ValueError("Task Witness source-shape tripwires must be an object")
    require_exact_fields(
        tripwires,
        {"per_module_source_lines", "aggregate_source_lines"},
        "Task Witness source-shape tripwires",
    )
    per_module_tripwire = require_nonnegative_integer(
        tripwires["per_module_source_lines"],
        "Task Witness per-module source-line tripwire",
    )
    aggregate_tripwire = require_nonnegative_integer(
        tripwires["aggregate_source_lines"],
        "Task Witness aggregate source-line tripwire",
    )
    if (
        max(reviewed_module_lines.values()) > per_module_tripwire
        or reviewed_aggregate > aggregate_tripwire
    ):
        raise ValueError(
            "Task Witness reviewed source shape exceeds its declared tripwires"
        )
    return {
        "per_module_source_lines": per_module_tripwire,
        "aggregate_source_lines": aggregate_tripwire,
        "reviewed_module_source_lines": reviewed_module_lines,
        "reviewed_aggregate_source_lines": reviewed_aggregate,
        "source_byte_entries": parsed_entries,
    }


def source_line_count(source: str) -> int:
    return sum(
        bool(line.strip()) and not line.lstrip().startswith("#")
        for line in source.splitlines()
    )


def validate_runtime(root: Path, plugin: Path) -> None:
    """Validate the fixed, release-owned runtime source boundary.

    This validator deliberately does not infer behavior from keywords or an
    incomplete static scan. Exact inventory and release identities bind the
    executable bytes; release-owned executable tests cover Task Witness-owned
    package boundaries and behavior, not registered-validator isolation.
    """

    tripwires = load_source_shape_record(root)
    total_review_lines = 0
    current_entries: list[tuple[str, str]] = []
    current_line_counts: dict[str, int] = {}
    for relative in sorted(RUNTIME_FILES | LAUNCHER_FILES):
        runtime = plugin / relative
        source = runtime.read_text(encoding="utf-8")
        try:
            compile(source, str(runtime), "exec")
        except SyntaxError as error:
            raise ValueError("Task Witness runtime syntax is invalid") from error
        review_lines = source_line_count(source)
        if review_lines > tripwires["per_module_source_lines"]:
            raise ValueError("Task Witness module source-line tripwire exceeded")
        total_review_lines += review_lines
        relative_path = (PLUGIN_RELATIVE / relative).as_posix()
        current_line_counts[relative_path] = review_lines
        current_entries.append(
            (relative_path, hashlib.sha256(runtime.read_bytes()).hexdigest())
        )
    if total_review_lines > tripwires["aggregate_source_lines"]:
        raise ValueError(
            "Task Witness aggregate source-line tripwire exceeded; "
            "an independent source-shape review is required before rebaselining"
        )
    if current_line_counts != tripwires["reviewed_module_source_lines"]:
        raise ValueError("Task Witness source-line measurement drift")
    if total_review_lines != tripwires["reviewed_aggregate_source_lines"]:
        raise ValueError("Task Witness aggregate source-line measurement drift")
    if current_entries != tripwires["source_byte_entries"]:
        raise ValueError("Task Witness source byte identity drift")


def main(argv: list[str] | None = None) -> int:
    arguments = argv or sys.argv[1:]
    root = Path(arguments[0] if arguments else ".").resolve()
    plugin = root / PLUGIN_RELATIVE
    try:
        validate_inventory(plugin)
        validate_manifests(plugin)
        validate_runtime(root, plugin)
    except Exception as error:
        print(f"Task Witness validation failed: {error}", file=sys.stderr)
        return 1
    print("Task Witness package validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
