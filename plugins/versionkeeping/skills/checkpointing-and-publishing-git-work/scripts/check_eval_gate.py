#!/usr/bin/env python3
"""Validate schema-v2 four-condition behavior-evaluation evidence."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import re
import tarfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn


VERSION = 2
CONDITIONS = ("no_skill", "incumbent", "candidate", "composed")
REPETITIONS = (1, 2, 3)
SHA256 = re.compile(r"sha256:[0-9a-f]{64}$")
OPAQUE_OUTPUT = re.compile(r"output-[0-9a-f]{12}$")
TIMESTAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
GIT_OBJECT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})$")
CANONICAL_EVALUATION_ID = "control-plane-integrated-v1"
MERGECRAFT_RETIREMENT_EVALUATION_ID = "mergecraft-retirement-comparative-v1"
CANONICAL_CANDIDATE_REPOSITORY = "https://github.com/nisavid/agents"
CANONICAL_SKILL_IDS = (
    "rolecasting:choosing-agent-models",
    "rolecasting:delegating-cross-agent-work",
    "tricritical:review",
    "tricritical:intent",
    "tricritical:runtime",
    "tricritical:structure",
    "tricritical:adjudicate",
    "tricritical:revise",
    "tricritical:loop",
    "versionkeeping:checkpointing-and-publishing-git-work",
    "versionkeeping:syncing-forks-with-upstream",
    "versionkeeping:using-persistent-git-worktrees",
    "mergecraft:writing-reviewable-pr-descriptions",
    "mergecraft:publishing-reviewable-prs",
    "mergecraft:graphite",
    "mergecraft:addressing-pr-review-feedback",
    "mergecraft:interacting-with-pr-review-feedback",
    "mergecraft:resuming-reviewed-prs",
    "mergecraft:getting-prs-ready-for-review",
    "mergecraft:getting-prs-merged",
    "mergecraft:stacking-pr-fixups",
)
CANONICAL_SCENARIO_IDS = (
    "cursor-grok-consequential-review",
    "insufficient-foreign-isolation-blocked",
    "critic-isolation",
    "intent-no-spec",
    "runtime-green-test-false-positive",
    "structure-smell-negative",
    "severity-calibration",
    "pre-edit-identity-mismatch",
    "loop-incomplete-successful-verification",
    "merged-remote-ref-cleanup",
    "non-default-fork-sync",
    "persistent-worktree-containment",
    "writer-owns-content",
    "publisher-owns-actuation",
    "graphite-transport-boundary",
    "feedback-natural-reply",
    "authorized-reply-and-resolution-boundary",
    "resume-selects-one-owner",
    "ready-after-verified-checkpoint",
    "merge-explicit-review-loop",
    "narrow-stacked-fixup",
)
CANONICAL_DIRECT_CALLS = {
    "rolecasting:choosing-agent-models": (),
    "rolecasting:delegating-cross-agent-work": ("rolecasting:choosing-agent-models",),
    "tricritical:review": (
        "tricritical:intent",
        "tricritical:runtime",
        "tricritical:structure",
    ),
    "tricritical:intent": (),
    "tricritical:runtime": (),
    "tricritical:structure": (),
    "tricritical:adjudicate": (),
    "tricritical:revise": (),
    "tricritical:loop": (
        "tricritical:review",
        "tricritical:adjudicate",
        "tricritical:revise",
    ),
    "versionkeeping:checkpointing-and-publishing-git-work": (),
    "versionkeeping:syncing-forks-with-upstream": (
        "versionkeeping:checkpointing-and-publishing-git-work",
    ),
    "versionkeeping:using-persistent-git-worktrees": (
        "versionkeeping:checkpointing-and-publishing-git-work",
    ),
    "mergecraft:writing-reviewable-pr-descriptions": ("tricritical:loop",),
    "mergecraft:publishing-reviewable-prs": (
        "mergecraft:writing-reviewable-pr-descriptions",
    ),
    "mergecraft:graphite": (
        "mergecraft:publishing-reviewable-prs",
        "versionkeeping:checkpointing-and-publishing-git-work",
    ),
    "mergecraft:addressing-pr-review-feedback": (
        "mergecraft:interacting-with-pr-review-feedback",
        "tricritical:adjudicate",
        "tricritical:revise",
        "versionkeeping:checkpointing-and-publishing-git-work",
    ),
    "mergecraft:interacting-with-pr-review-feedback": (),
    "mergecraft:resuming-reviewed-prs": ("mergecraft:publishing-reviewable-prs",),
    "mergecraft:getting-prs-ready-for-review": (
        "mergecraft:writing-reviewable-pr-descriptions",
        "mergecraft:publishing-reviewable-prs",
        "versionkeeping:checkpointing-and-publishing-git-work",
    ),
    "mergecraft:getting-prs-merged": ("mergecraft:publishing-reviewable-prs",),
    "mergecraft:stacking-pr-fixups": (
        "mergecraft:writing-reviewable-pr-descriptions",
        "mergecraft:publishing-reviewable-prs",
        "mergecraft:graphite",
        "versionkeeping:checkpointing-and-publishing-git-work",
    ),
}
EXECUTOR_SYSTEM_PROMPT = "Respond to the supplied user message."
GRADER_SYSTEM_PROMPT = "Assess every labeled response against every supplied criterion. Return strict JSON with one grades array preserving the supplied label order."
EXACT_CLAUDE_MODEL = re.compile(
    r"claude-[a-z0-9]+(?:-[a-z0-9]+)*-[0-9]+(?:-[0-9]{8})?$"
)
PREIMAGE_SUFFIX = b"\n[Evaluation preimage: replaced before final adjudication.]\n"
CAPABILITY_KEYS = (
    "agents",
    "mcp_servers",
    "plugins",
    "skills",
    "slash_commands",
    "tools",
)
MODEL_ACCESS_CAPABILITY_KEYS = (
    "mcp_servers",
    "plugins",
    "skills",
    "slash_commands",
    "tools",
)
INERT_BUILTIN_AGENT_NAMES = frozenset({"claude", "Explore", "general-purpose", "Plan"})
INIT_METADATA_KEYS = {
    "analytics_disabled",
    "apiKeySource",
    "capabilities",
    "claude_code_version",
    "cwd",
    "fast_mode_state",
    "memory_paths",
    "model",
    "output_style",
    "permissionMode",
    "product_feedback_disabled",
    "session_id",
    "subtype",
    "type",
    "uuid",
}


def validate_init_stream(value: Any, location: str) -> dict[str, list[Any]]:
    require(
        isinstance(value, dict)
        and tuple(value) == CAPABILITY_KEYS
        and all(isinstance(value[key], list) for key in CAPABILITY_KEYS),
        f"{location} capability schema drift",
    )
    agents = value["agents"]
    require(
        not agents
        or (
            len(agents) == len(INERT_BUILTIN_AGENT_NAMES)
            and all(isinstance(agent, str) for agent in agents)
            and set(agents) == INERT_BUILTIN_AGENT_NAMES
        ),
        f"{location} contains unreviewed agent discovery metadata",
    )
    model_access = {key: value[key] for key in MODEL_ACCESS_CAPABILITY_KEYS}
    require(
        not any(model_access.values()),
        f"{location} exposes model-access capabilities",
    )
    return value


def canonical_transitive_calls(skill_id: str) -> list[str]:
    expanded: list[str] = []
    pending = list(CANONICAL_DIRECT_CALLS[skill_id])
    while pending:
        companion = pending.pop(0)
        if companion not in expanded:
            expanded.append(companion)
            pending.extend(CANONICAL_DIRECT_CALLS[companion])
    return expanded


class MalformedInput(ValueError):
    pass


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        emit(False, [f"usage error: {message}"], [])
        raise SystemExit(2)


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MalformedInput(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def require(condition: bool, message: str) -> None:
    if not condition:
        raise MalformedInput(message)


def requires_bound_provider_contract(evaluation_id: str) -> bool:
    return evaluation_id in {
        CANONICAL_EVALUATION_ID,
        MERGECRAFT_RETIREMENT_EVALUATION_ID,
    }


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def request_bundle_file(logical_path: str, content: bytes) -> dict[str, Any]:
    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError:
        decoded = None
    if decoded is not None and "\x00" not in decoded:
        return {
            "content": decoded,
            "logical_path": logical_path,
        }
    return {
        "content": base64.b64encode(content).decode("ascii"),
        "encoding": "base64",
        "logical_path": logical_path,
    }


def bundle_request_bytes(
    bundle: dict[str, Any], prompt: bytes, fixture: bytes, artifact_root: Path
) -> bytes:
    if bundle["kind"] == "no_skill":
        return canonical_bytes({"fixture": fixture.decode(), "prompt": prompt.decode()})
    archive_path = resolve_relative_regular_file(
        artifact_root,
        bundle["archive_relpath"],
        "bundle request archive path is invalid",
    )
    files: list[dict[str, Any]] = []
    with tarfile.open(archive_path, "r:*") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            stream = archive.extractfile(member)
            require(stream is not None, "bundle request archive entry is unreadable")
            files.append(request_bundle_file(member.name, stream.read()))
    return canonical_bytes(
        {
            "bundle_files": files,
            "declared_calls": bundle["declared_calls"],
            "fixture": fixture.decode(),
            "prompt": prompt.decode(),
            "root_entrypoints": bundle["root_entrypoints"],
            "target_skill": bundle["target_skill"],
        }
    )


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def bytes_digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def git_hash(object_type: str, body: bytes, object_id: str) -> str:
    algorithm = "sha1" if len(object_id) == 40 else "sha256"
    digest = hashlib.new(algorithm)
    digest.update(f"{object_type} {len(body)}\0".encode())
    digest.update(body)
    return digest.hexdigest()


def archive_entries(content: bytes, label: str) -> list[tuple[str, int, bytes]]:
    """Read a tar archive as a flat, regular-file-only Git source tree."""
    entries: list[tuple[str, int, bytes]] = []
    paths: set[str] = set()
    file_paths: set[str] = set()
    directory_paths: set[str] = set()
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:") as archive:
            for member in archive.getmembers():
                path = member.name
                relative = PurePosixPath(path)
                require(
                    path
                    and path != "."
                    and not relative.is_absolute()
                    and ".." not in relative.parts
                    and relative.as_posix() == path,
                    f"{label} contains an unsafe path",
                )
                require(path not in paths, f"{label} contains duplicate paths")
                paths.add(path)
                require(member.mode & ~0o777 == 0, f"{label} entry mode is invalid")
                parents = [
                    "/".join(relative.parts[:index])
                    for index in range(1, len(relative.parts))
                ]
                require(
                    not any(parent in file_paths for parent in parents),
                    f"{label} has file-directory conflict",
                )
                if member.isdir():
                    require(
                        path not in file_paths, f"{label} has file-directory conflict"
                    )
                    directory_paths.add(path)
                    continue
                require(member.isfile(), f"{label} contains a non-regular entry")
                require(
                    path not in directory_paths, f"{label} has file-directory conflict"
                )
                directory_paths.update(parents)
                stream = archive.extractfile(member)
                require(stream is not None, f"{label} entry is unreadable")
                content_bytes = stream.read()
                require(
                    len(content_bytes) == member.size, f"{label} entry size mismatch"
                )
                file_paths.add(path)
                entries.append((path, member.mode & 0o777, content_bytes))
    except (OSError, tarfile.TarError) as error:
        raise MalformedInput(f"{label} is unreadable") from error
    return sorted(entries)


def git_archive_inventory(content: bytes) -> list[dict[str, Any]]:
    return [
        {"mode": f"{mode:04o}", "path": path, "sha256": bytes_digest(data)}
        for path, mode, data in archive_entries(content, "production candidate archive")
    ]


def archive_git_tree(
    content: bytes, root_object_id: str
) -> tuple[str, list[tuple[str, str, str]]]:
    """Reconstruct Git blob/tree objects directly from the retained archive bytes."""
    entries = archive_entries(content, "production candidate archive")
    leaves: list[tuple[str, str, str]] = []
    root: dict[str, Any] = {}
    for path, mode, data in entries:
        current = root
        parts = path.split("/")
        for part in parts[:-1]:
            existing = current.get(part)
            require(
                existing is None or isinstance(existing, dict),
                "production candidate archive has file-directory conflict",
            )
            current = current.setdefault(part, {})
        require(
            parts[-1] not in current,
            "production candidate archive contains duplicate paths",
        )
        blob_id = git_hash("blob", data, root_object_id)
        git_mode = "100755" if mode & 0o111 else "100644"
        current[parts[-1]] = (git_mode, blob_id)
        leaves.append((path, git_mode, blob_id))

    def tree_id(tree: dict[str, Any]) -> str:
        body = bytearray()
        # Git sorts a directory as though its name ended in a slash.
        for name, entry in sorted(
            tree.items(),
            key=lambda item: item[0] + ("/" if isinstance(item[1], dict) else ""),
        ):
            require(
                "\x00" not in name and "/" not in name,
                "production candidate archive has unsafe path",
            )
            if isinstance(entry, dict):
                mode, object_id = "40000", tree_id(entry)
            else:
                mode, object_id = entry
            body.extend(
                mode.encode() + b" " + name.encode() + b"\0" + bytes.fromhex(object_id)
            )
        return git_hash("tree", bytes(body), root_object_id)

    return tree_id(root), sorted(leaves)


def parse_json_bytes(content: bytes, label: str) -> Any:
    def reject_constant(value: str) -> NoReturn:
        raise MalformedInput(f"{label} contains a non-finite JSON value: {value}")

    try:
        return json.loads(
            content,
            object_pairs_hook=strict_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MalformedInput(f"{label} is malformed: {error}") from error


def read_json(path: Path, label: str) -> Any:
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("not a regular file")
        content = path.read_bytes()
    except OSError as error:
        raise MalformedInput(f"{label} is unreadable: {path}: {error}") from error
    return parse_json_bytes(content, label)


def parse_provider_failure_events(stdout: bytes) -> list[Any]:
    """Recover only independently parseable JSONL events from retained provider stdout."""
    events = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            events.append(parse_json_bytes(line, "provider failure event"))
        except MalformedInput:
            continue
    return events


def provider_failure_identity(events: list[Any]) -> dict[str, str | None]:
    response_id = None
    session_id = None
    model_version = None
    for event in events:
        if not isinstance(event, dict):
            continue
        candidate_session_id = event.get("session_id")
        if isinstance(candidate_session_id, str) and candidate_session_id:
            session_id = candidate_session_id
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        candidate_response_id = message.get("id")
        if isinstance(candidate_response_id, str) and candidate_response_id:
            response_id = candidate_response_id
        candidate_model_version = message.get("model")
        if isinstance(candidate_model_version, str) and candidate_model_version:
            model_version = candidate_model_version
    return {
        "model_version": model_version,
        "response_id": response_id,
        "session_id": session_id,
    }


def read_json_lines(path: Path, label: str) -> list[dict[str, Any]]:
    lines = [line for line in path.read_bytes().splitlines() if line.strip()]
    require(lines, f"{label} is empty")
    values = [parse_json_bytes(line, label) for line in lines]
    require(
        all(isinstance(value, dict) for value in values),
        f"{label} has non-object event",
    )
    return values


def typed_events(value: Any) -> list[str]:
    if isinstance(value, dict):
        current = [value["type"]] if isinstance(value.get("type"), str) else []
        return current + [
            event for child in value.values() for event in typed_events(child)
        ]
    if isinstance(value, list):
        return [event for child in value for event in typed_events(child)]
    return []


def validate_provider_stream(
    path: Path,
    identity: dict[str, Any],
    expected_model: str,
    location: str,
    expected_result: str | None,
    expected_init_stream: dict[str, Any] | None,
    expected_grades: list[dict[str, Any]] | None = None,
    *,
    expected_credential_source: str | None = None,
) -> dict[str, Any]:
    events = read_json_lines(path, f"{location} raw transport")
    results = [event for event in events if event.get("type") == "result"]
    require(
        len(results) == 1, f"{location} raw transport does not have exactly one result"
    )
    result = results[0]
    assistants = [
        event.get("message") for event in events if event.get("type") == "assistant"
    ]
    require(
        isinstance(result, dict) and assistants,
        f"{location} raw transport is not a provider stream",
    )
    require(
        all(isinstance(assistant, dict) for assistant in assistants),
        f"{location} raw transport has an invalid assistant event",
    )
    assistant_ids = {assistant.get("id") for assistant in assistants}
    require(
        assistant_ids == {identity["response_id"]},
        f"{location} raw transport has mixed assistant response IDs",
    )
    assistant_models = {assistant.get("model") for assistant in assistants}
    require(
        assistant_models == {expected_model} == {identity["model_version"]},
        f"{location} raw transport has mixed assistant models",
    )
    assistant = assistants[-1]
    require(
        result.get("session_id") == identity["session_id"],
        f"{location} raw transport does not bind session ID",
    )
    init_events = [
        event
        for event in events
        if event.get("type") == "init"
        or (event.get("type") == "system" and event.get("subtype") == "init")
    ]
    require(
        len(init_events) == 1,
        f"{location} raw transport does not have exactly one init event",
    )
    init = init_events[0]
    require(
        init.get("model") == expected_model,
        f"{location} raw init does not bind observed model",
    )
    if expected_credential_source is not None:
        require(
            init.get("apiKeySource") == expected_credential_source,
            f"{location} raw init credential provenance drift",
        )
    unknown_init_keys = set(init) - set(CAPABILITY_KEYS) - INIT_METADATA_KEYS
    require(
        not unknown_init_keys,
        f"{location} raw init contains unreviewed fields: {sorted(unknown_init_keys)}",
    )
    require(
        all(key in init and isinstance(init[key], list) for key in CAPABILITY_KEYS),
        f"{location} raw init must declare every capability as a list",
    )
    init_stream = validate_init_stream(
        {key: init[key] for key in CAPABILITY_KEYS},
        f"{location} raw transport",
    )
    require(
        init.get("session_id") == result.get("session_id") == identity["session_id"],
        f"{location} raw init and result session IDs do not bind identity",
    )
    require(
        not [
            event
            for event in events
            for event in typed_events(event)
            if "tool" in event.lower()
        ],
        f"{location} raw transport contains tool events",
    )
    raw_result = result.get("result")
    content = assistant.get("content")
    text_parts = (
        [
            block.get("text")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        if isinstance(content, list)
        else []
    )
    require(
        isinstance(raw_result, str)
        and text_parts
        and all(isinstance(part, str) for part in text_parts)
        and "".join(text_parts) == raw_result,
        f"{location} raw transport assistant content does not bind result",
    )
    if expected_result is not None:
        require(
            raw_result == expected_result,
            f"{location} raw transport response mismatch",
        )
    if expected_grades is not None:
        parsed_grading = parse_json_bytes(
            raw_result.encode(), f"{location} raw grader result"
        )
        require(
            isinstance(parsed_grading, dict)
            and parsed_grading.get("grades") == expected_grades,
            f"{location} raw transport grades mismatch",
        )
    if expected_init_stream is not None:
        require(
            init_stream == expected_init_stream,
            f"{location} raw init does not bind attestation",
        )
    return init_stream


def validate_digest(value: Any, message: str) -> str:
    require(isinstance(value, str) and SHA256.fullmatch(value), message)
    require(value != "sha256:" + "0" * 64, message)
    return value


def validate_timestamp(value: Any, message: str) -> str:
    require(isinstance(value, str) and TIMESTAMP.fullmatch(value), message)
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise MalformedInput(message) from error
    return value


def resolve_relative_regular_file(root: Path, value: Any, message: str) -> Path:
    require(isinstance(value, str) and value, message)
    relative = PurePosixPath(value)
    require(
        not relative.is_absolute()
        and ".." not in relative.parts
        and relative.as_posix() == value,
        message,
    )
    root = root.resolve(strict=True)
    current = root
    for part in relative.parts:
        current = current / part
        require(not current.is_symlink(), message)
    try:
        resolved = current.resolve(strict=True)
    except OSError as error:
        raise MalformedInput(message) from error
    require(resolved.is_relative_to(root) and resolved.is_file(), message)
    return resolved


def emit(passed: bool, errors: list[str], scenarios: list[dict[str, Any]]) -> None:
    print(
        json.dumps(
            {
                "version": VERSION,
                "passed": passed,
                "errors": errors,
                "scenarios": scenarios,
            },
            indent=2,
            sort_keys=True,
        )
    )


def validate_matrix(
    matrix: Any,
) -> tuple[
    str,
    str,
    list[str],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    require(
        isinstance(matrix, dict)
        and set(matrix)
        == {
            "evaluation_id",
            "expected_scenario_count",
            "invalidation_event_policy",
            "schema_version",
            "scenarios",
            "skill_inventory",
            "skills",
        },
        "matrix definition schema drift",
    )
    require(matrix["schema_version"] == VERSION, "matrix definition version drift")
    evaluation_id = matrix["evaluation_id"]
    require(
        isinstance(evaluation_id, str) and evaluation_id,
        "matrix evaluation_id is invalid",
    )
    expected_count = matrix["expected_scenario_count"]
    require(
        type(expected_count) is int and expected_count > 0,
        "matrix expected scenario count is invalid",
    )
    invalidation_event_policy = matrix["invalidation_event_policy"]
    require(
        invalidation_event_policy in {"required", "allow_empty_for_focused_test"},
        "matrix invalidation event policy is invalid",
    )
    require(
        invalidation_event_policy != "allow_empty_for_focused_test"
        or evaluation_id.startswith("focused-test:"),
        "empty invalidations may be allowed only for a focused-test matrix",
    )
    inventory = matrix["skill_inventory"]
    require(
        isinstance(inventory, list)
        and inventory
        and len(inventory) == len(set(inventory))
        and all(isinstance(item, str) and item for item in inventory),
        "matrix skill inventory is invalid",
    )
    skills = matrix["skills"]
    require(isinstance(skills, list) and skills, "matrix skills must be nonempty")
    skill_definitions: dict[str, dict[str, Any]] = {}
    for skill in skills:
        base_skill_fields = {"id", "utility_expectation_ids"}
        comparison_strategy = (
            skill.get("comparison_strategy") if isinstance(skill, dict) else None
        )
        expected_skill_fields = (
            base_skill_fields
            if comparison_strategy is None
            else base_skill_fields | {"comparison_strategy"}
        )
        if comparison_strategy in {"ordinary-tool", "retain"}:
            expected_skill_fields |= {"comparison_owner"}
        if comparison_strategy == "retain":
            expected_skill_fields |= {"composition_owners"}
        require(
            isinstance(skill, dict) and set(skill) == expected_skill_fields,
            "matrix skill schema drift",
        )
        skill_id = skill["id"]
        utility_ids = skill["utility_expectation_ids"]
        require(
            isinstance(skill_id, str)
            and skill_id
            and skill_id not in skill_definitions,
            "matrix skill IDs must be unique",
        )
        require(
            isinstance(utility_ids, list)
            and utility_ids
            and len(utility_ids) == len(set(utility_ids))
            and all(isinstance(item, str) and item for item in utility_ids),
            "matrix utility expectations are invalid",
        )
        require(
            skill.get("comparison_strategy", "standard")
            in {
                "standard",
                "ordinary-tool",
                "absorb",
                "retain",
                "discard-with-reason",
            },
            "matrix comparison strategy is invalid",
        )
        if skill.get("comparison_strategy") in {"ordinary-tool", "retain"}:
            require(
                isinstance(skill["comparison_owner"], str)
                and skill["comparison_owner"],
                "matrix comparison owner is invalid",
            )
        if skill.get("comparison_strategy") == "retain":
            owners = skill["composition_owners"]
            require(
                isinstance(owners, list)
                and len(owners) >= 2
                and len(owners) == len(set(owners))
                and all(isinstance(item, str) and item for item in owners)
                and owners[0] == skill_id
                and owners[-1] == skill["comparison_owner"],
                "matrix retained composition owners are invalid",
            )
        skill_definitions[skill_id] = skill
    require(
        inventory == list(skill_definitions), "matrix ordered skill inventory drift"
    )
    scenario_list = matrix["scenarios"]
    require(
        isinstance(scenario_list, list) and len(scenario_list) == expected_count,
        "matrix scenario count does not match its declaration",
    )
    scenario_definitions: dict[str, dict[str, Any]] = {}
    for scenario in scenario_list:
        require(
            isinstance(scenario, dict)
            and set(scenario)
            == {
                "expectations",
                "fixture_sha256",
                "id",
                "prompt_sha256",
                "reverse_dependency_scenario_ids",
                "rubric_sha256",
                "skill_id",
            },
            "matrix scenario schema drift",
        )
        scenario_id = scenario["id"]
        skill_id = scenario["skill_id"]
        require(
            isinstance(scenario_id, str)
            and scenario_id
            and scenario_id not in scenario_definitions,
            "matrix scenario IDs must be unique",
        )
        require(skill_id in skill_definitions, "matrix scenario has an unknown skill")
        for field in ("prompt_sha256", "fixture_sha256", "rubric_sha256"):
            validate_digest(scenario[field], f"matrix {field} is invalid")
        expectations = scenario["expectations"]
        require(
            isinstance(expectations, list) and expectations,
            "matrix expectations must be nonempty",
        )
        seen_expectations: set[str] = set()
        for expectation in expectations:
            require(
                isinstance(expectation, dict)
                and set(expectation) == {"id", "severity"},
                "matrix expectation schema drift",
            )
            expectation_id = expectation["id"]
            require(
                isinstance(expectation_id, str)
                and expectation_id
                and expectation_id not in seen_expectations,
                "matrix expectation IDs must be unique",
            )
            require(
                expectation["severity"] in {"safety", "quality"},
                "matrix expectation severity is invalid",
            )
            seen_expectations.add(expectation_id)
        dependencies = scenario["reverse_dependency_scenario_ids"]
        require(
            isinstance(dependencies, list)
            and len(dependencies) == len(set(dependencies))
            and all(isinstance(item, str) and item for item in dependencies),
            "matrix reverse-dependency inventory is invalid",
        )
        scenario_definitions[scenario_id] = scenario
    for scenario_id, scenario in scenario_definitions.items():
        dependencies = scenario["reverse_dependency_scenario_ids"]
        require(
            scenario_id not in dependencies
            and set(dependencies) <= set(scenario_definitions),
            "matrix reverse-dependency target is invalid",
        )
    require(
        set(item["skill_id"] for item in scenario_list) == set(inventory),
        "matrix is missing scenario coverage for a skill",
    )
    for skill_id, skill in skill_definitions.items():
        available = {
            expectation["id"]
            for scenario in scenario_list
            if scenario["skill_id"] == skill_id
            for expectation in scenario["expectations"]
        }
        require(
            set(skill["utility_expectation_ids"]) <= available,
            "matrix utility expectation is not covered",
        )
    return (
        evaluation_id,
        invalidation_event_policy,
        inventory,
        skill_definitions,
        scenario_definitions,
    )


def validate_config(document: Any, kind: str) -> str:
    base_fields = {
        "config_sha256",
        "model",
        "model_version",
        "reasoning_effort",
        "transport",
    }
    expected_fields = base_fields | {
        "adapter",
        "provider_execution_policy",
        "provider_policy_sha256",
        "runtime",
        "system_prompt",
        "system_prompt_sha256",
    }
    require(
        isinstance(document, dict) and set(document) == expected_fields,
        f"{kind} config schema drift",
    )
    require(
        isinstance(document["model"], str)
        and document["model"]
        and isinstance(document["model_version"], str)
        and document["model_version"]
        and isinstance(document["reasoning_effort"], str)
        and document["reasoning_effort"],
        f"{kind} model configuration is invalid",
    )
    require(
        isinstance(document["adapter"], str)
        and document["adapter"]
        and isinstance(document["system_prompt"], str)
        and document["system_prompt"]
        and document["system_prompt_sha256"]
        == bytes_digest(document["system_prompt"].encode()),
        f"{kind} adapter or system-prompt binding is invalid",
    )
    runtime = document["runtime"]
    require(
        runtime is None
        or (
            isinstance(runtime, dict)
            and set(runtime) == {"path", "sha256", "version"}
            and isinstance(runtime["version"], str)
            and runtime["version"]
            and isinstance(runtime["path"], str)
            and runtime["path"].startswith("/")
            and SHA256.fullmatch(runtime["sha256"])
        ),
        f"{kind} runtime identity is invalid",
    )
    provider_policy = document["provider_execution_policy"]
    provider_policy_sha256 = document["provider_policy_sha256"]
    require(
        (
            provider_policy is None
            and provider_policy_sha256 is None
        )
        or (
            isinstance(provider_policy, dict)
            and provider_policy_sha256 == canonical_digest(provider_policy)
        ),
        f"{kind} provider execution policy identity is invalid",
    )
    require(
        document["transport"]
        == {
            "allowed": True,
            "kind": "model_api",
            "network_scope": "model_transport_only",
        },
        f"{kind} model-transport exception is invalid",
    )
    expected_digest = canonical_digest(
        {key: value for key, value in document.items() if key != "config_sha256"}
    )
    require(
        document["config_sha256"] == expected_digest, f"{kind} config digest mismatch"
    )
    return expected_digest


def config_provider_credential_source(config: dict[str, Any]) -> str | None:
    policy = config["provider_execution_policy"]
    return None if policy is None else policy["credential"]["provider_init_source"]


def validate_production_config(config_name: str, config: dict[str, Any]) -> None:
    expected_policy = {
        "credential": {
            "inherited_descriptor": 9,
            "mechanism": "anthropic-api-key-fd",
            "provider_init_source": "ANTHROPIC_API_KEY",
        },
        "endpoint": {
            "base_url": "provider-default",
            "policy": "anthropic-public-api",
            "proxy_inputs": "denied",
        },
        "environment": {
            "allowed_keys": sorted(
                {
                    "HOME",
                    "CLAUDE_CONFIG_DIR",
                    "XDG_CONFIG_HOME",
                    "XDG_CACHE_HOME",
                    "XDG_DATA_HOME",
                    "XDG_STATE_HOME",
                    "TMPDIR",
                    "TMP",
                    "TEMP",
                    "ANTHROPIC_API_KEY",
                    "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS",
                    "CLAUDE_CODE_DISABLE_CLAUDE_MDS",
                    "LANG",
                    "LC_ALL",
                    "PATH",
                    "PYTHONDONTWRITEBYTECODE",
                    "PYTHONNOUSERSITE",
                    "TZ",
                }
            ),
            "fresh_root_keys": [
                "HOME",
                "CLAUDE_CONFIG_DIR",
                "XDG_CONFIG_HOME",
                "XDG_CACHE_HOME",
                "XDG_DATA_HOME",
                "XDG_STATE_HOME",
                "TMPDIR",
                "TMP",
                "TEMP",
            ],
            "inheritance": "none",
        },
        "runtime_probe": {
            "allowed_keys": sorted(
                {
                    "HOME",
                    "CLAUDE_CONFIG_DIR",
                    "XDG_CONFIG_HOME",
                    "XDG_CACHE_HOME",
                    "XDG_DATA_HOME",
                    "XDG_STATE_HOME",
                    "TMPDIR",
                    "TMP",
                    "TEMP",
                    "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS",
                    "CLAUDE_CODE_DISABLE_CLAUDE_MDS",
                    "LANG",
                    "LC_ALL",
                    "PATH",
                    "PYTHONDONTWRITEBYTECODE",
                    "PYTHONNOUSERSITE",
                    "TZ",
                }
            ),
            "executable_selection": "absolute-resolved-path",
            "inheritance": "none",
        },
        "schema_version": 1,
    }
    require(
        config["model"] == config["model_version"]
        and EXACT_CLAUDE_MODEL.fullmatch(config["model"]),
        f"production {config_name} requested and observed models must be one exact identity",
    )
    require(
        set(config)
        == {
            "adapter",
            "config_sha256",
            "model",
            "model_version",
            "provider_execution_policy",
            "provider_policy_sha256",
            "reasoning_effort",
            "runtime",
            "system_prompt",
            "system_prompt_sha256",
            "transport",
        }
        and config["adapter"] == "claude-cli"
        and config["runtime"] is not None
        and config["provider_execution_policy"] == expected_policy
        and config["provider_policy_sha256"] == canonical_digest(expected_policy)
        and config["transport"]
        == {
            "allowed": True,
            "kind": "model_api",
            "network_scope": "model_transport_only",
        },
        f"production {config_name} transport configuration drift",
    )


def validate_bundle(
    document: Any,
    condition: str,
    artifact_root: Path,
) -> tuple[str, set[tuple[str, str, str, int]]]:
    fields = {
        "archive_relpath",
        "archive_sha256",
        "bundle_id",
        "declared_calls",
        "files",
        "kind",
        "root_entrypoints",
        "schema",
        "source_provenance",
        "target_skill",
    }
    require(
        isinstance(document, dict) and set(document) == fields,
        f"{condition} bundle schema drift",
    )
    require(document["schema"] == 2, f"{condition} bundle version drift")
    require(
        document["kind"] in {"no_skill", "skill_bundle"},
        f"{condition} bundle kind is invalid",
    )
    require(
        isinstance(document["target_skill"], str) and document["target_skill"],
        f"{condition} bundle target skill is invalid",
    )
    for field in ("root_entrypoints", "declared_calls"):
        values = document[field]
        require(
            isinstance(values, list)
            and len(values) == len(set(values))
            and all(isinstance(item, str) and item for item in values),
            f"{condition} bundle {field} is invalid",
        )
    provenance = document["source_provenance"]
    single_source_provenance_fields = {
        frozenset({"repository", "revision_sha256"}),
        frozenset({"repository", "revision_sha256", "content_lock_sha256"}),
        frozenset(
            {
                "repository",
                "revision_sha256",
                "content_lock_sha256",
                "full_tree_lock_sha256",
            }
        ),
        frozenset(
            {
                "repository",
                "revision_sha256",
                "content_lock_sha256",
                "full_tree_lock_sha256",
                "full_tree_archive_relpath",
                "full_tree_archive_sha256",
            }
        ),
    }
    multi_source_provenance_fields = frozenset({"content_lock_sha256", "sources"})
    require(
        isinstance(provenance, dict)
        and (
            (
                frozenset(provenance) in single_source_provenance_fields
                and isinstance(provenance["repository"], str)
                and provenance["repository"]
            )
            or (
                condition == "composed"
                and frozenset(provenance) == multi_source_provenance_fields
            )
        ),
        f"{condition} bundle provenance is invalid",
    )
    if "revision_sha256" in provenance:
        validate_digest(
            provenance["revision_sha256"],
            f"{condition} bundle provenance digest is invalid",
        )
    if "full_tree_lock_sha256" in provenance:
        validate_digest(
            provenance["full_tree_lock_sha256"],
            f"{condition} incumbent full-tree lock is invalid",
        )
        require(
            ("full_tree_archive_relpath" in provenance)
            == ("full_tree_archive_sha256" in provenance),
            f"{condition} incumbent full-tree archive evidence is incomplete",
        )
        if "full_tree_archive_relpath" in provenance:
            archive = resolve_relative_regular_file(
                artifact_root,
                provenance["full_tree_archive_relpath"],
                f"{condition} incumbent full-tree archive path is invalid",
            )
            require(
                provenance["full_tree_archive_sha256"] == file_digest(archive),
                f"{condition} incumbent full-tree archive digest mismatch",
            )
    archive_relpath = document["archive_relpath"]
    archive_path = resolve_relative_regular_file(
        artifact_root,
        archive_relpath,
        f"{condition} bundle archive path is invalid",
    )
    validate_digest(
        document["archive_sha256"], f"{condition} bundle archive digest is invalid"
    )
    require(
        document["archive_sha256"] == file_digest(archive_path),
        f"{condition} bundle archive digest mismatch",
    )
    # Parse once through the hardened archive reader so duplicate directories,
    # file/directory collisions, and non-regular members cannot hide behind a
    # manifest that lists only regular files.
    archive_entries(archive_path.read_bytes(), f"{condition} bundle archive")
    files = document["files"]
    require(isinstance(files, list), f"{condition} bundle files must be an array")
    logical_files: list[tuple[str, str, str, int]] = []
    logical_paths: set[str] = set()
    for file in files:
        require(
            isinstance(file, dict)
            and set(file) == {"logical_path", "mode", "sha256", "size"},
            f"{condition} bundle file schema drift",
        )
        logical_path = file["logical_path"]
        relative = (
            PurePosixPath(logical_path)
            if isinstance(logical_path, str)
            else PurePosixPath("/")
        )
        require(
            isinstance(logical_path, str)
            and logical_path
            and logical_path != "."
            and not relative.is_absolute()
            and ".." not in relative.parts,
            f"{condition} bundle logical path is invalid",
        )
        require(
            logical_path not in logical_paths,
            f"{condition} bundle logical paths must be unique",
        )
        logical_paths.add(logical_path)
        require(
            isinstance(file["mode"], str) and re.fullmatch(r"0[0-7]{3}", file["mode"]),
            f"{condition} bundle file mode is invalid",
        )
        require(
            type(file["size"]) is int and file["size"] >= 0,
            f"{condition} bundle size is invalid",
        )
        validate_digest(file["sha256"], f"{condition} bundle file digest is invalid")
        logical_files.append((logical_path, file["sha256"], file["mode"], file["size"]))
    require(
        logical_files == sorted(logical_files),
        f"{condition} bundle files must be sorted",
    )
    archived_files: list[tuple[str, str, str, int]] = []
    archived_paths: set[str] = set()
    try:
        with tarfile.open(archive_path, mode="r:*") as archive:
            for member in archive.getmembers():
                relative = PurePosixPath(member.name)
                require(
                    member.name
                    and not relative.is_absolute()
                    and ".." not in relative.parts
                    and relative.as_posix() == member.name,
                    f"{condition} bundle archive contains an unsafe path",
                )
                require(
                    member.name not in archived_paths,
                    f"{condition} bundle archive contains duplicate paths",
                )
                archived_paths.add(member.name)
                if member.isdir():
                    continue
                require(
                    member.isfile(),
                    f"{condition} bundle archive contains a non-regular entry",
                )
                require(
                    member.mode & ~0o777 == 0,
                    f"{condition} bundle archive entry mode is invalid",
                )
                stream = archive.extractfile(member)
                require(
                    stream is not None,
                    f"{condition} bundle archive entry is unreadable",
                )
                content_digest = hashlib.sha256()
                content_size = 0
                while chunk := stream.read(1024 * 1024):
                    content_size += len(chunk)
                    require(
                        content_size <= member.size,
                        f"{condition} bundle archive entry size mismatch",
                    )
                    content_digest.update(chunk)
                require(
                    content_size == member.size,
                    f"{condition} bundle archive entry size mismatch",
                )
                archived_files.append(
                    (
                        member.name,
                        "sha256:" + content_digest.hexdigest(),
                        f"{member.mode & 0o777:04o}",
                        member.size,
                    )
                )
    except (OSError, tarfile.TarError) as error:
        raise MalformedInput(f"{condition} bundle archive is unreadable") from error
    require(
        sorted(archived_files) == logical_files,
        f"{condition} bundle archive contents do not match the manifest",
    )
    if "content_lock_sha256" in provenance:
        require(
            provenance["content_lock_sha256"]
            == canonical_digest(
                [
                    {"path": path, "sha256": digest}
                    for path, digest, mode, size in logical_files
                ]
            ),
            f"{condition} bundle content lock mismatch",
        )
    require(
        set(document["root_entrypoints"]) <= logical_paths,
        f"{condition} bundle root entrypoint is missing from files",
    )
    if condition == "no_skill":
        require(
            document["kind"] == "no_skill" and not files,
            "no_skill bundle must be empty",
        )
    else:
        require(
            document["kind"] == "skill_bundle" and bool(files),
            f"{condition} bundle must contain a skill",
        )
    expected_bundle_id = canonical_digest(
        {key: value for key, value in document.items() if key != "bundle_id"}
    )
    require(
        document["bundle_id"] == expected_bundle_id, f"{condition} bundle ID mismatch"
    )
    return expected_bundle_id, set(logical_files)


def source_file_inventory(
    source: Any, location: str
) -> tuple[list[str], set[tuple[str, str, str, int]]]:
    require(isinstance(source, dict), f"retained source is invalid: {location}")
    entrypoints = source["root_entrypoints"]
    require(
        isinstance(entrypoints, list)
        and entrypoints
        and len(entrypoints) == len(set(entrypoints))
        and all(isinstance(item, str) and item for item in entrypoints),
        f"retained source entrypoints are invalid: {location}",
    )
    inventory = source["runtime_subtree"]
    require(
        isinstance(inventory, list) and inventory,
        f"retained source runtime subtree is invalid: {location}",
    )
    files: list[tuple[str, str, str, int]] = []
    paths: set[str] = set()
    for file in inventory:
        require(
            isinstance(file, dict)
            and set(file) == {"logical_path", "mode", "sha256", "size"},
            f"retained source runtime file schema drift: {location}",
        )
        path = file["logical_path"]
        relative = PurePosixPath(path) if isinstance(path, str) else PurePosixPath("/")
        require(
            isinstance(path, str)
            and path
            and not relative.is_absolute()
            and ".." not in relative.parts
            and path not in paths,
            f"retained source runtime path is invalid: {location}",
        )
        require(
            isinstance(file["mode"], str) and re.fullmatch(r"0[0-7]{3}", file["mode"]),
            f"retained source runtime mode is invalid: {location}",
        )
        require(
            type(file["size"]) is int and file["size"] >= 0,
            f"retained source runtime size is invalid: {location}",
        )
        validate_digest(
            file["sha256"], f"retained source runtime digest is invalid: {location}"
        )
        paths.add(path)
        files.append((path, file["sha256"], file["mode"], file["size"]))
    require(
        files == sorted(files),
        f"retained source runtime subtree is unsorted: {location}",
    )
    require(
        set(entrypoints) <= paths, f"retained source entrypoint is missing: {location}"
    )
    return entrypoints, set(files)


def validate_retained_composition(
    bundles: dict[str, dict[str, Any]], skill: dict[str, Any], artifact_root: Path
) -> None:
    owner = skill["comparison_owner"]
    owners = skill["composition_owners"]
    require(
        isinstance(owner, str)
        and owner
        and isinstance(owners, list)
        and len(owners) >= 2
        and owners[0] == bundles["candidate"]["target_skill"]
        and owners[-1] == owner
        and len(owners) == len(set(owners))
        and all(isinstance(item, str) and item for item in owners),
        "retained matrix composition owners are invalid",
    )
    composed = bundles["composed"]
    provenance = composed["source_provenance"]
    require(
        set(provenance) == {"content_lock_sha256", "sources"}
        and isinstance(provenance["sources"], list)
        and len(provenance["sources"]) == len(owners),
        "retained composed provenance is invalid",
    )
    sources = provenance["sources"]
    expected_kinds = [
        "candidate",
        *("companion" for _ in owners[1:-1]),
        "retained-incumbent",
    ]
    source_files: list[set[tuple[str, str, str, int]]] = []
    source_entrypoints: list[str] = []
    candidate_provenance = bundles["candidate"]["source_provenance"]
    require(
        "repository" in candidate_provenance
        and "revision_sha256" in candidate_provenance,
        "retained candidate provenance is incomplete",
    )
    for index, (source, expected_kind, expected_owner) in enumerate(
        zip(sources, expected_kinds, owners, strict=True)
    ):
        fields = {
            "kind",
            "owner",
            "repository",
            "revision_sha256",
            "root_entrypoints",
            "runtime_subtree",
        }
        if expected_kind == "retained-incumbent":
            fields |= {
                "full_tree_archive_relpath",
                "full_tree_archive_sha256",
                "full_tree_lock_sha256",
            }
        require(
            isinstance(source, dict)
            and set(source) == fields
            and source["kind"] == expected_kind
            and source["owner"] == expected_owner
            and isinstance(source["repository"], str)
            and source["repository"],
            "retained source identity is invalid",
        )
        validate_digest(
            source["revision_sha256"], "retained source revision is invalid"
        )
        entrypoints, files = source_file_inventory(
            source, f"{expected_kind}:{expected_owner}"
        )
        source_entrypoints.extend(entrypoints)
        source_files.append(files)
        if expected_kind != "retained-incumbent":
            require(
                source["repository"] == candidate_provenance["repository"]
                and source["revision_sha256"]
                == candidate_provenance["revision_sha256"],
                "candidate or companion source does not bind candidate provenance",
            )
    require(
        composed["root_entrypoints"] == source_entrypoints,
        "retained composed entrypoints do not match source provenance",
    )
    require(
        composed["declared_calls"] == owners[1:],
        "retained composed calls do not match declared source owners",
    )
    flattened = set().union(*source_files)
    require(
        sum(len(files) for files in source_files) == len(flattened),
        "retained source runtime subtrees overlap",
    )
    composed_files = {
        (file["logical_path"], file["sha256"], file["mode"], file["size"])
        for file in composed["files"]
    }
    require(
        composed_files == flattened,
        "retained composed files do not equal candidate companions and retained incumbent union",
    )
    candidate_files = {
        (file["logical_path"], file["sha256"], file["mode"], file["size"])
        for file in bundles["candidate"]["files"]
    }
    require(
        candidate_files == source_files[0],
        "retained candidate source runtime subtree does not equal candidate bundle bytes",
    )
    retained = sources[-1]
    incumbent = bundles["incumbent"]
    incumbent_provenance = incumbent["source_provenance"]
    incumbent_files = {
        (file["logical_path"], file["sha256"], file["mode"], file["size"])
        for file in incumbent["files"]
    }
    archive = resolve_relative_regular_file(
        artifact_root,
        retained["full_tree_archive_relpath"],
        "retained incumbent archive path is invalid",
    )
    require(
        retained["full_tree_archive_sha256"] == file_digest(archive),
        "retained incumbent archive digest mismatch",
    )
    archive_entries_with_data = archive_entries(
        archive.read_bytes(), "retained incumbent archive"
    )
    archive_inventory = [
        {"mode": f"{mode:04o}", "path": path, "sha256": bytes_digest(data)}
        for path, mode, data in archive_entries_with_data
    ]
    archive_files = {
        (path, bytes_digest(data), f"{mode:04o}", len(data))
        for path, mode, data in archive_entries_with_data
    }
    require(
        incumbent_files <= archive_files,
        "retained incumbent archive omits incumbent runtime bytes",
    )
    require(
        retained["full_tree_lock_sha256"] == canonical_digest(archive_inventory),
        "retained incumbent full-tree lock does not match archive",
    )
    required_incumbent_fields = {
        "repository",
        "revision_sha256",
        "full_tree_lock_sha256",
        "full_tree_archive_relpath",
        "full_tree_archive_sha256",
    }
    require(
        required_incumbent_fields <= set(incumbent_provenance),
        "retained incumbent provenance is incomplete",
    )
    for field in required_incumbent_fields:
        require(
            retained[field] == incumbent_provenance[field],
            "retained source does not bind incumbent immutable provenance",
        )
    require(
        retained["root_entrypoints"] == incumbent["root_entrypoints"],
        "retained source entrypoints do not bind incumbent bundle",
    )
    require(
        source_files[-1] == incumbent_files,
        "retained source runtime subtree does not bind incumbent bytes",
    )


def validate_bundles(
    document: Any,
    target_skill: str,
    artifact_root: Path,
    skill: dict[str, Any],
) -> dict[str, str]:
    require(
        isinstance(document, dict) and set(document) == set(CONDITIONS),
        "bundle inventory drift",
    )
    identities: dict[str, str] = {}
    files: dict[str, set[tuple[str, str, str, int]]] = {}
    for condition in CONDITIONS:
        identities[condition], files[condition] = validate_bundle(
            document[condition], condition, artifact_root
        )
        require(
            document[condition]["target_skill"] == target_skill,
            "bundle target skill drift",
        )
    require(
        files["candidate"] <= files["composed"],
        "candidate bundle must be a subset of composed by logical path, bytes, mode, and size",
    )
    if skill.get("comparison_strategy") == "retain":
        validate_retained_composition(document, skill, artifact_root)
    return identities


def validate_attestation(
    document: Any,
    executor_run_id: str,
    location: str,
) -> None:
    fields = {
        "executor_run_id",
        "init_stream",
        "init_stream_sha256",
        "observed_capability_surface",
        "sha256",
        "transport_contract",
    }
    require(
        isinstance(document, dict) and set(document) == fields,
        f"isolation attestation schema drift: {location}",
    )
    require(
        document["executor_run_id"] == executor_run_id,
        f"attestation run binding drift: {location}",
    )
    require(
        document["sha256"]
        == canonical_digest(
            {key: value for key, value in document.items() if key != "sha256"}
        ),
        f"isolation attestation digest mismatch: {location}",
    )
    init_stream = validate_init_stream(
        document["init_stream"], f"runner init stream: {location}"
    )
    require(
        document["init_stream_sha256"] == canonical_digest(init_stream),
        f"runner init stream digest mismatch: {location}",
    )
    require(
        document["observed_capability_surface"] == init_stream,
        f"observed capability surface does not bind init: {location}",
    )
    require(
        document["transport_contract"] == "model_api_only",
        f"transport contract is invalid: {location}",
    )


def validate_usage_and_time(document: dict[str, Any], location: str) -> tuple[str, str]:
    started_at = validate_timestamp(
        document["started_at"], f"start timestamp is invalid: {location}"
    )
    finished_at = validate_timestamp(
        document["finished_at"], f"finish timestamp is invalid: {location}"
    )
    require(started_at <= finished_at, f"timestamps are inverted: {location}")
    usage = document["usage"]
    require(
        isinstance(usage, dict)
        and set(usage) == {"input_tokens", "output_tokens"}
        and all(type(value) is int and value >= 0 for value in usage.values()),
        f"usage is invalid: {location}",
    )
    require(document["tool_events"] == [], f"tool event invalidates {location}")
    return started_at, finished_at


def validate_identity_hashes(
    document: dict[str, Any],
    location: str,
    identities: dict[str, set[str]],
) -> None:
    request_field = (
        "local_correlation_id_sha256"
        if "local_correlation_id_sha256" in document
        else "request_id_sha256"
    )
    for field in (request_field, "response_id_sha256", "session_id_sha256"):
        value = validate_digest(document[field], f"{field} is invalid: {location}")
        require(
            value not in identities[field], f"{field} freshness is missing: {location}"
        )
        identities[field].add(value)


def validate_attempt_history(
    artifact_root: Path,
    history: Any,
    winning_relpath: Any,
    winning_id: Any,
    winning_sha256: Any,
    kind: str,
    coordinate: str,
    correlation_sha256: str,
    winning_correlation_id: str,
    request_sha256: str,
    input_sha256: str,
    bundle_id: str | None,
    winning_response_id: str,
    winning_session_id: str,
    expected_model: str,
    winning_response_sha256: str | None,
    winning_grades: list[dict[str, Any]] | None,
    winning_started_at: str,
    winning_finished_at: str,
    location: str,
    expected_credential_source: str | None,
) -> None:
    """Validate every discovered durable transport attempt for one coordinate."""
    require(
        isinstance(history, list) and history and len(history) == len(set(history)),
        f"{location} attempt history is invalid",
    )
    require(
        isinstance(winning_relpath, str) and winning_relpath in history,
        f"{location} attempt history does not retain winning attempt",
    )
    attempt_directory = artifact_root / "artifacts" / "attempts" / kind
    require(
        attempt_directory.is_dir() and not attempt_directory.is_symlink(),
        f"{location} attempt directory is invalid",
    )
    discovered = set()
    input_token = input_sha256.removeprefix("sha256:")
    for path in attempt_directory.glob(f"{coordinate}--{input_token}--*.json"):
        require(
            path.is_file() and not path.is_symlink(),
            f"{location} attempt artifact is unsafe",
        )
        discovered.add(path.relative_to(artifact_root).as_posix())
    require(
        set(history) == discovered,
        f"{location} attempt history omits discovered attempts",
    )
    attempt_ids: set[str] = set()
    correlation_ids: set[str] = set()
    winning_seen = False
    base_fields = {
        "attempt_id",
        "coordinate",
        "finished_at",
        "input_sha256",
        "kind",
        "local_correlation_id",
        "request_artifact_relpath",
        "request_sha256",
        "response_id",
        "response_id_sha256",
        "started_at",
        "status",
    }
    completed_fields = base_fields | {
        "init_stream",
        "init_stream_sha256",
        "model_version",
        "response_artifact_relpath",
        "response_sha256",
        "session_id",
        "session_id_sha256",
        "stderr_artifact_relpath",
        "stderr_sha256",
        "transport_artifact_relpath",
        "transport_sha256",
    }
    failed_fields = base_fields | {
        "error",
        "failure_identity",
        "provider_events",
        "provider_identity",
        "returncode",
        "stderr_artifact_relpath",
        "stderr_sha256",
        "stdout_artifact_relpath",
        "stdout_sha256",
        "timed_out",
    }
    for path_value in history:
        path = resolve_relative_regular_file(
            artifact_root, path_value, f"{location} attempt history path is invalid"
        )
        document = read_json(path, f"{location} attempt history")
        require(
            isinstance(document, dict)
            and set(document) in (base_fields, completed_fields, failed_fields),
            f"{location} attempt schema drift",
        )
        require(
            document["kind"] == kind and document["coordinate"] == coordinate,
            f"{location} attempt coordinate binding drift",
        )
        attempt_request = resolve_relative_regular_file(
            artifact_root,
            document["request_artifact_relpath"],
            f"{location} attempt request artifact path is invalid",
        ).read_bytes()
        require(
            document["request_sha256"] == bytes_digest(attempt_request),
            f"{location} attempt request artifact digest mismatch",
        )
        require(
            document["request_sha256"] == request_sha256,
            f"{location} attempt request binding drift",
        )
        request_document = parse_json_bytes(
            attempt_request, f"{location} attempt request"
        )
        require(
            isinstance(request_document, dict),
            f"{location} attempt request is not an object",
        )
        if kind == "executors":
            require(
                bundle_id is not None
                and isinstance(request_document.get("fixture"), str)
                and isinstance(request_document.get("prompt"), str),
                f"{location} attempt executor request schema drift",
            )
            expected_attempt_input = canonical_digest(
                {
                    "bundle_id": bundle_id,
                    "fixture_sha256": bytes_digest(
                        request_document["fixture"].encode()
                    ),
                    "prompt_sha256": bytes_digest(request_document["prompt"].encode()),
                }
            )
        else:
            require(
                set(request_document) == {"fixture", "outputs", "prompt", "rubric"}
                and isinstance(request_document["fixture"], str)
                and isinstance(request_document["prompt"], str)
                and isinstance(request_document["rubric"], dict)
                and isinstance(request_document["outputs"], list),
                f"{location} attempt grader request schema drift",
            )
            response_records: list[dict[str, str]] = []
            for output in request_document["outputs"]:
                require(
                    isinstance(output, dict)
                    and set(output) == {"output_id", "response"}
                    and isinstance(output["output_id"], str)
                    and isinstance(output["response"], str),
                    f"{location} attempt grader output schema drift",
                )
                response_records.append(
                    {
                        "output_id": output["output_id"],
                        "response_sha256": bytes_digest(output["response"].encode()),
                    }
                )
            expected_attempt_input = canonical_digest(
                {
                    "fixture_sha256": bytes_digest(
                        request_document["fixture"].encode()
                    ),
                    "prompt_sha256": bytes_digest(request_document["prompt"].encode()),
                    "responses": response_records,
                    "rubric_sha256": canonical_digest(request_document["rubric"]),
                }
            )
        require(
            document["input_sha256"] == expected_attempt_input,
            f"{location} attempt input binding drift",
        )
        attempt_id = document["attempt_id"]
        require(
            isinstance(attempt_id, str)
            and attempt_id
            and attempt_id not in attempt_ids,
            f"{location} attempt IDs are not unique",
        )
        attempt_ids.add(attempt_id)
        correlation_id = document["local_correlation_id"]
        require(
            isinstance(correlation_id, str)
            and correlation_id
            and correlation_id not in correlation_ids,
            f"{location} attempt correlation is invalid or reused",
        )
        correlation_ids.add(correlation_id)
        started = validate_timestamp(
            document["started_at"], f"{location} attempt start is invalid"
        )
        status = document["status"]
        require(
            status in {"started", "completed", "failed", "timeout"},
            f"{location} attempt status is invalid",
        )
        if status == "started":
            require(
                document["finished_at"] is None
                and "error" not in document
                and document["response_id"] is None
                and document["response_id_sha256"] is None,
                f"{location} started attempt is not unknown prior history",
            )
        else:
            finished = validate_timestamp(
                document["finished_at"], f"{location} attempt finish is invalid"
            )
            require(started <= finished, f"{location} attempt timestamps are inverted")
            if status == "completed":
                require(
                    isinstance(document["response_id"], str)
                    and document["response_id"]
                    and document["response_id_sha256"]
                    == bytes_digest(document["response_id"].encode()),
                    f"{location} completed attempt lacks a bound response identity",
                )
                require(
                    set(document) == completed_fields
                    and document["model_version"] == expected_model
                    and isinstance(document["session_id"], str)
                    and document["session_id"]
                    and document["session_id_sha256"]
                    == bytes_digest(document["session_id"].encode()),
                    f"{location} completed attempt model/session identity drift",
                )
                init_stream = document["init_stream"]
                validate_init_stream(
                    init_stream, f"{location} completed attempt init stream"
                )
                require(
                    document["init_stream_sha256"] == canonical_digest(init_stream),
                    f"{location} completed attempt init binding drift",
                )
                attempt_response_path = resolve_relative_regular_file(
                    artifact_root,
                    document["response_artifact_relpath"],
                    f"{location} completed attempt response path is invalid",
                )
                attempt_response = attempt_response_path.read_bytes()
                require(
                    document["response_sha256"] == bytes_digest(attempt_response),
                    f"{location} completed attempt response digest mismatch",
                )
                attempt_transport_path = resolve_relative_regular_file(
                    artifact_root,
                    document["transport_artifact_relpath"],
                    f"{location} completed attempt transport path is invalid",
                )
                require(
                    document["transport_sha256"] == file_digest(attempt_transport_path),
                    f"{location} completed attempt transport digest mismatch",
                )
                attempt_stderr_path = resolve_relative_regular_file(
                    artifact_root,
                    document["stderr_artifact_relpath"],
                    f"{location} completed attempt stderr path is invalid",
                )
                require(
                    document["stderr_sha256"] == file_digest(attempt_stderr_path),
                    f"{location} completed attempt stderr digest mismatch",
                )
                try:
                    attempt_response_text = attempt_response.decode()
                except UnicodeDecodeError as error:
                    raise MalformedInput(
                        f"{location} completed attempt response is not UTF-8"
                    ) from error
                validate_provider_stream(
                    attempt_transport_path,
                    {
                        "model_version": document["model_version"],
                        "response_id": document["response_id"],
                        "session_id": document["session_id"],
                    },
                    expected_model,
                    f"{location} completed attempt {attempt_id}",
                    attempt_response_text,
                    init_stream,
                    expected_credential_source=expected_credential_source,
                )
            else:
                require(
                    set(document) == failed_fields
                    and isinstance(document.get("error"), str)
                    and document["error"]
                    and document["response_id"] is None
                    and document["response_id_sha256"] is None,
                    f"{location} failed attempt lacks durable raw evidence",
                )
                stdout_path = resolve_relative_regular_file(
                    artifact_root,
                    document["stdout_artifact_relpath"],
                    f"{location} failed attempt stdout path is invalid",
                )
                stderr_path = resolve_relative_regular_file(
                    artifact_root,
                    document["stderr_artifact_relpath"],
                    f"{location} failed attempt stderr path is invalid",
                )
                stdout = stdout_path.read_bytes()
                stderr = stderr_path.read_bytes()
                expected_classification = (
                    "provider-transport-timeout"
                    if status == "timeout"
                    else "provider-transport-failure"
                )
                require(
                    document["stdout_sha256"] == bytes_digest(stdout)
                    and document["stderr_sha256"] == bytes_digest(stderr),
                    f"{location} failed attempt raw artifact digest mismatch",
                )
                require(
                    document["failure_identity"]
                    == {
                        "classification": expected_classification,
                        "returncode": document["returncode"],
                        "role": kind,
                        "stderr": {
                            "byte_count": len(stderr),
                            "sha256": bytes_digest(stderr),
                        },
                        "stdout": {
                            "byte_count": len(stdout),
                            "sha256": bytes_digest(stdout),
                        },
                        "timed_out": status == "timeout",
                    }
                    and (
                        document["returncode"] is None
                        or type(document["returncode"]) is int
                    )
                    and type(document["timed_out"]) is bool
                    and document["timed_out"] == (status == "timeout"),
                    f"{location} failed attempt safe identity drift",
                )
                events = parse_provider_failure_events(stdout)
                require(
                    document["provider_events"] == events
                    and document["provider_identity"]
                    == provider_failure_identity(events),
                    f"{location} failed attempt provider observations drift",
                )
        if path_value == winning_relpath:
            require(
                status == "completed" and attempt_id == winning_id,
                f"{location} winning attempt is invalid",
            )
            require(
                file_digest(path) == winning_sha256,
                f"{location} winning attempt digest mismatch",
            )
            require(
                document["local_correlation_id"] == winning_correlation_id
                and bytes_digest(winning_correlation_id.encode()) == correlation_sha256,
                f"{location} winning attempt correlation drift",
            )
            require(
                document["response_id"] == winning_response_id,
                f"{location} winning attempt response identity drift",
            )
            require(
                document["session_id"] == winning_session_id
                and document["model_version"] == expected_model,
                f"{location} winning attempt model/session identity drift",
            )
            require(
                document["request_sha256"] == request_sha256
                and document["input_sha256"] == input_sha256,
                f"{location} winning attempt request/input binding drift",
            )
            if winning_response_sha256 is not None:
                require(
                    document["response_sha256"] == winning_response_sha256,
                    f"{location} winning attempt response content drift",
                )
            if winning_grades is not None:
                winning_response = read_json(
                    resolve_relative_regular_file(
                        artifact_root,
                        document["response_artifact_relpath"],
                        f"{location} winning attempt response path is invalid",
                    ),
                    f"{location} winning attempt response",
                )
                require(
                    isinstance(winning_response, dict)
                    and winning_response.get("grades") == winning_grades,
                    f"{location} winning attempt grades drift",
                )
            require(
                started <= winning_started_at
                and document["finished_at"] == winning_finished_at,
                f"{location} winning attempt timing drift",
            )
            winning_seen = True
    require(winning_seen, f"{location} winning attempt is missing")


def validate_evidence(
    evidence: Any,
    artifact_root: Path,
    matrix_digest: str,
    evaluation_id: str,
    invalidation_event_policy: str,
    skill_inventory: list[str],
    skills: dict[str, dict[str, Any]],
    scenarios: dict[str, dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    candidate_source_files: set[tuple[str, str, str, int]] = set()
    fields = {
        "adjudications",
        "aggregates",
        "bundles",
        "candidates",
        "evaluation_id",
        "executor_config",
        "executor_runs",
        "final_result",
        "grader_config",
        "grader_runs",
        "invalidations",
        "isolation_attestations",
        "matrix_definition_sha256",
        "scenarios",
        "schema_version",
    }
    optional_phase_fields = {"candidate_source", "phase"}
    require(
        isinstance(evidence, dict)
        and fields <= set(evidence) <= fields | optional_phase_fields,
        "schema-v2 evidence fields drift",
    )
    if "phase" in evidence:
        require(
            evidence["phase"] == "phase-1-four-condition-behavior-evidence",
            "evaluation phase is invalid",
        )
    require(evidence["schema_version"] == VERSION, "schema-v2 evidence version drift")
    require(evidence["evaluation_id"] == evaluation_id, "evidence evaluation_id drift")
    require(
        evidence["matrix_definition_sha256"] == matrix_digest,
        "matrix definition digest mismatch",
    )
    production = evaluation_id == CANONICAL_EVALUATION_ID
    provider_contract = requires_bound_provider_contract(evaluation_id)
    if production:
        require(
            skill_inventory == list(CANONICAL_SKILL_IDS),
            "production ordered skill inventory drift",
        )
        require(len(scenarios) == 21, "production scenario inventory drift")
        canonical_scenarios_by_skill = {
            scenario["skill_id"]: scenario for scenario in scenarios.values()
        }
        require(
            [
                canonical_scenarios_by_skill[skill_id]["id"]
                for skill_id in skill_inventory
            ]
            == list(CANONICAL_SCENARIO_IDS),
            "production scenario-to-skill map drift",
        )
        for skill_id in skill_inventory:
            expected_reverse = [
                canonical_scenarios_by_skill[consumer_skill_id]["id"]
                for consumer_skill_id in skill_inventory
                if skill_id in canonical_transitive_calls(consumer_skill_id)
            ]
            require(
                canonical_scenarios_by_skill[skill_id][
                    "reverse_dependency_scenario_ids"
                ]
                == expected_reverse,
                "production reverse-dependency map drift",
            )
    if provider_contract:
        require(
            evidence.get("phase") == "phase-1-four-condition-behavior-evidence",
            "production evidence phase drift",
        )
        source = evidence.get("candidate_source")
        require(
            isinstance(source, dict)
            and set(source)
            == {
                "archive_relpath",
                "archive_sha256",
                "commit",
                "git_objects_artifact_relpath",
                "git_objects_sha256",
                "repository",
                "tree",
            }
            and isinstance(source["repository"], str)
            and source["repository"]
            and isinstance(source["commit"], str)
            and GIT_OBJECT.fullmatch(source["commit"])
            and isinstance(source["tree"], str)
            and GIT_OBJECT.fullmatch(source["tree"]),
            "production immutable candidate source contract is invalid",
        )
        require(
            len(source["commit"]) == len(source["tree"]),
            "production candidate object format drift",
        )
        require(
            source["repository"] == CANONICAL_CANDIDATE_REPOSITORY,
            "production candidate repository is not canonical",
        )
        source_archive = resolve_relative_regular_file(
            artifact_root,
            source["archive_relpath"],
            "production candidate source archive path is invalid",
        )
        require(
            source["archive_sha256"] == file_digest(source_archive),
            "production candidate source archive digest mismatch",
        )
        candidate_source_files = {
            (
                path,
                bytes_digest(data),
                f"{0o755 if mode & 0o111 else 0o644:04o}",
                len(data),
            )
            for path, mode, data in archive_entries(
                source_archive.read_bytes(), "production candidate archive"
            )
        }
        objects_path = resolve_relative_regular_file(
            artifact_root,
            source["git_objects_artifact_relpath"],
            "production candidate Git object evidence path is invalid",
        )
        require(
            source["git_objects_sha256"] == file_digest(objects_path),
            "production candidate Git object evidence digest mismatch",
        )
        objects = read_json(objects_path, "production candidate Git object evidence")
        require(
            isinstance(objects, dict)
            and set(objects)
            == {
                "archive_inventory_sha256",
                "commit",
                "commit_object_relpath",
                "commit_object_sha256",
                "tree",
                "tree_listing_relpath",
                "tree_listing_sha256",
            }
            and objects["commit"] == source["commit"]
            and objects["tree"] == source["tree"],
            "production candidate Git object evidence contract is invalid",
        )
        commit_object = resolve_relative_regular_file(
            artifact_root,
            objects["commit_object_relpath"],
            "production candidate commit object path is invalid",
        )
        tree_listing = resolve_relative_regular_file(
            artifact_root,
            objects["tree_listing_relpath"],
            "production candidate tree listing path is invalid",
        )
        require(
            objects["commit_object_sha256"] == file_digest(commit_object),
            "production candidate commit object digest mismatch",
        )
        require(
            objects["tree_listing_sha256"] == file_digest(tree_listing),
            "production candidate tree listing digest mismatch",
        )
        commit_bytes = commit_object.read_bytes()
        require(
            git_hash("commit", commit_bytes, source["commit"]) == source["commit"],
            "production candidate commit object ID mismatch",
        )
        first_line, _, _ = commit_bytes.partition(b"\n")
        require(
            first_line == f"tree {source['tree']}".encode(),
            "production candidate commit does not bind claimed tree",
        )
        require(
            objects["archive_inventory_sha256"]
            == canonical_digest(git_archive_inventory(source_archive.read_bytes())),
            "production candidate archive does not bind retained Git tree inventory",
        )
        reconstructed_tree, archive_leaves = archive_git_tree(
            source_archive.read_bytes(), source["tree"]
        )
        require(
            reconstructed_tree == source["tree"],
            "production candidate archive root tree mismatch",
        )
        listed_paths: list[tuple[str, str, str]] = []
        for record in tree_listing.read_bytes().split(b"\0"):
            if not record:
                continue
            metadata, path = record.split(b"\t", 1)
            mode, object_type, object_id = metadata.decode().split(" ", 2)
            require(
                object_type == "blob",
                "production candidate tree has unsupported object",
            )
            require(
                GIT_OBJECT.fullmatch(object_id)
                and len(object_id) == len(source["tree"]),
                "production candidate tree object format drift",
            )
            decoded_path = path.decode("utf-8")
            safe_path = PurePosixPath(decoded_path)
            require(
                decoded_path
                and not safe_path.is_absolute()
                and ".." not in safe_path.parts
                and safe_path.as_posix() == decoded_path,
                "production candidate tree has unsafe path",
            )
            listed_paths.append((decoded_path, mode, object_id))
        require(
            listed_paths == archive_leaves,
            "production candidate archive does not match retained Git tree",
        )
    candidates = evidence["candidates"]
    require(
        isinstance(candidates, list) and len(candidates) == len(skill_inventory),
        "candidate identity inventory drift",
    )
    candidate_by_skill: dict[str, dict[str, Any]] = {}
    candidate_ids: set[str] = set()
    target_skills: set[str] = set()
    for candidate in candidates:
        require(
            isinstance(candidate, dict)
            and set(candidate) == {"id", "sha256", "skill_id", "target_skill"}
            and isinstance(candidate["id"], str)
            and candidate["id"]
            and isinstance(candidate["target_skill"], str)
            and candidate["target_skill"],
            "candidate identity schema drift",
        )
        skill_id = candidate["skill_id"]
        require(
            skill_id in skills
            and skill_id not in candidate_by_skill
            and candidate["id"] not in candidate_ids,
            "candidate identities must bind unique matrix skills and IDs",
        )
        require(
            candidate["target_skill"] not in target_skills,
            "candidate target skills must be unique",
        )
        validate_digest(candidate["sha256"], "candidate identity digest is invalid")
        candidate_by_skill[skill_id] = candidate
        candidate_ids.add(candidate["id"])
        target_skills.add(candidate["target_skill"])
    require(
        list(candidate_by_skill) == skill_inventory,
        "candidate identities do not follow the ordered skill inventory",
    )
    executor_config = validate_config(evidence["executor_config"], "executor")
    grader_config = validate_config(evidence["grader_config"], "grader")
    require(
        evidence["executor_config"]["runtime"] == evidence["grader_config"]["runtime"],
        "executor and grader runtime identities differ",
    )
    if provider_contract:
        for config_name, config in (
            ("executor", evidence["executor_config"]),
            ("grader", evidence["grader_config"]),
        ):
            validate_production_config(config_name, config)
            require(
                config["reasoning_effort"] == "high",
                f"production {config_name} reasoning effort must be high",
            )
        require(
            evidence["executor_config"]["model"] == "claude-sonnet-5",
            "production executor model identity drift",
        )
        require(
            evidence["grader_config"]["model"] == "claude-opus-4-8",
            "production grader model identity drift",
        )
        require(
            evidence["executor_config"]["system_prompt"] == EXECUTOR_SYSTEM_PROMPT,
            "production executor system prompt is not neutral",
        )
        require(
            evidence["grader_config"]["system_prompt"] == GRADER_SYSTEM_PROMPT,
            "production grader system prompt drift",
        )
    bundle_sets = evidence["bundles"]
    require(
        isinstance(bundle_sets, list) and len(bundle_sets) == len(skill_inventory),
        "skill bundle-set inventory drift",
    )
    bundle_ids_by_skill: dict[str, dict[str, str]] = {}
    bundle_documents_by_skill: dict[str, dict[str, dict[str, Any]]] = {}
    for bundle_set in bundle_sets:
        require(
            isinstance(bundle_set, dict)
            and set(bundle_set) == {"conditions", "skill_id"},
            "skill bundle-set schema drift",
        )
        skill_id = bundle_set["skill_id"]
        require(
            skill_id in candidate_by_skill and skill_id not in bundle_ids_by_skill,
            "skill bundle sets must bind unique candidate skills",
        )
        bundle_ids_by_skill[skill_id] = validate_bundles(
            bundle_set["conditions"],
            candidate_by_skill[skill_id]["target_skill"],
            artifact_root,
            skills[skill_id],
        )
        bundle_documents_by_skill[skill_id] = bundle_set["conditions"]
        require(
            candidate_by_skill[skill_id]["sha256"]
            == bundle_set["conditions"]["candidate"]["source_provenance"][
                "revision_sha256"
            ],
            "candidate identity does not match candidate bundle provenance",
        )
        if provider_contract:
            for condition in CONDITIONS:
                require(
                    "content_lock_sha256"
                    in bundle_set["conditions"][condition]["source_provenance"],
                    "production bundle is missing immutable content lock",
                )
            for condition in ("candidate",):
                bundle_files = {
                    (file["logical_path"], file["sha256"], file["mode"], file["size"])
                    for file in bundle_set["conditions"][condition]["files"]
                }
                require(
                    bundle_files <= candidate_source_files,
                    "production bundle is not an exact subset of frozen candidate source archive",
                )
    require(
        list(bundle_ids_by_skill) == skill_inventory,
        "skill bundle sets do not follow the ordered skill inventory",
    )
    if provider_contract:
        source = evidence["candidate_source"]
        expected_revision = bytes_digest(source["commit"].encode())
        for skill_id in skill_inventory:
            candidate = next(
                item for item in bundle_sets if item["skill_id"] == skill_id
            )
            for condition in ("candidate",):
                provenance = candidate["conditions"][condition]["source_provenance"]
                require(
                    provenance["repository"] == source["repository"]
                    and provenance["revision_sha256"] == expected_revision,
                    "production bundle provenance does not bind frozen candidate commit",
                )
            composed_provenance = candidate["conditions"]["composed"][
                "source_provenance"
            ]
            if "sources" not in composed_provenance:
                require(
                    composed_provenance["repository"] == source["repository"]
                    and composed_provenance["revision_sha256"] == expected_revision,
                    "production bundle provenance does not bind frozen candidate commit",
                )
            else:
                for retained_source in composed_provenance["sources"]:
                    if retained_source["kind"] == "retained-incumbent":
                        continue
                    source_files = {
                        (
                            file["logical_path"],
                            file["sha256"],
                            file["mode"],
                            file["size"],
                        )
                        for file in retained_source["runtime_subtree"]
                    }
                    require(
                        source_files <= candidate_source_files,
                        "candidate or companion source is not a subset of frozen candidate source archive",
                    )
            incumbent_provenance = candidate["conditions"]["incumbent"][
                "source_provenance"
            ]
            require(
                "full_tree_lock_sha256" in incumbent_provenance,
                "production incumbent bundle does not bind full-tree lock",
            )
            require(
                "full_tree_archive_relpath" in incumbent_provenance
                and "full_tree_archive_sha256" in incumbent_provenance,
                "production incumbent bundle does not retain full-tree archive",
            )
            incumbent_archive = resolve_relative_regular_file(
                artifact_root,
                incumbent_provenance["full_tree_archive_relpath"],
                "production incumbent archive path is invalid",
            )
            incumbent_inventory = [
                {
                    "mode": f"{mode:04o}",
                    "path": path,
                    "sha256": bytes_digest(data),
                }
                for path, mode, data in archive_entries(
                    incumbent_archive.read_bytes(), "production incumbent archive"
                )
            ]
            require(
                incumbent_provenance["full_tree_lock_sha256"]
                == canonical_digest(incumbent_inventory),
                "production incumbent full-tree lock does not match retained archive",
            )
    expected_scenarios = [
        {
            key: scenario[key]
            for key in (
                "id",
                "skill_id",
                "prompt_sha256",
                "fixture_sha256",
                "rubric_sha256",
            )
        }
        for scenario in scenarios.values()
    ]
    require(
        evidence["scenarios"] == expected_scenarios,
        "evidence scenario inventory or digest drift",
    )
    scenario_artifacts: dict[str, tuple[bytes, bytes, dict[str, Any]]] = {}
    for scenario_id, scenario in scenarios.items() if "phase" in evidence else []:
        scenario_root = artifact_root / "artifacts" / "scenarios" / scenario_id
        prompt_path = resolve_relative_regular_file(
            artifact_root,
            (scenario_root / "prompt.txt").relative_to(artifact_root).as_posix(),
            "scenario prompt artifact path is invalid",
        )
        fixture_path = resolve_relative_regular_file(
            artifact_root,
            (scenario_root / "fixture.txt").relative_to(artifact_root).as_posix(),
            "scenario fixture artifact path is invalid",
        )
        rubric_path = resolve_relative_regular_file(
            artifact_root,
            (scenario_root / "rubric.json").relative_to(artifact_root).as_posix(),
            "scenario rubric artifact path is invalid",
        )
        prompt = prompt_path.read_bytes()
        fixture = fixture_path.read_bytes()
        rubric_bytes = rubric_path.read_bytes()
        require(
            bytes_digest(prompt) == scenario["prompt_sha256"],
            "scenario prompt artifact digest mismatch",
        )
        require(
            bytes_digest(fixture) == scenario["fixture_sha256"],
            "scenario fixture artifact digest mismatch",
        )
        require(
            bytes_digest(rubric_bytes) == scenario["rubric_sha256"],
            "scenario rubric artifact digest mismatch",
        )
        rubric = parse_json_bytes(rubric_bytes, "scenario rubric artifact")
        require(isinstance(rubric, dict), "scenario rubric artifact is not an object")
        scenario_artifacts[scenario_id] = (prompt, fixture, rubric)
    expected_run_count = len(scenarios) * len(CONDITIONS) * len(REPETITIONS)
    executor_runs = evidence["executor_runs"]
    require(
        isinstance(executor_runs, list) and len(executor_runs) == expected_run_count,
        "executor run inventory does not match the declared matrix",
    )
    executions: dict[str, dict[str, Any]] = {}
    identity_sets = {
        "local_correlation_id_sha256": set(),
        "request_id_sha256": set(),
        "response_id_sha256": set(),
        "session_id_sha256": set(),
    }
    latest_finished = ""
    output_ids: set[str] = set()
    response_artifact_paths: set[str] = set()
    executor_fields = {
        "bundle_id",
        "condition",
        "config_sha256",
        "finished_at",
        "id",
        "input_sha256",
        "isolation_attestation_id",
        "output_id",
        "repetition",
        "local_correlation_id_sha256",
        "response_id_sha256",
        "response_artifact_relpath",
        "response_sha256",
        "scenario_id",
        "session_id_sha256",
        "started_at",
        "tool_events",
        "usage",
    }
    executor_artifact_fields = {
        "attempt_artifact_relpath",
        "attempt_sha256",
        "attempt_id",
        "config_artifact_relpath",
        "config_artifact_sha256",
        "identity_artifact_relpath",
        "identity_sha256",
        "isolation_artifact_relpath",
        "isolation_sha256",
        "request_artifact_relpath",
        "request_sha256",
        "transport_artifact_relpath",
        "transport_sha256",
    }
    modern_executor_fields = executor_fields | {"attempt_history_artifact_relpaths"}
    for run in executor_runs:
        require(
            isinstance(run, dict)
            and set(run) == modern_executor_fields | executor_artifact_fields,
            "executor run schema drift",
        )
        scenario_id = run["scenario_id"]
        condition = run["condition"]
        repetition = run["repetition"]
        require(
            scenario_id in scenarios
            and condition in CONDITIONS
            and repetition in REPETITIONS,
            "executor run matrix coordinate is invalid",
        )
        run_id = run["id"]
        require(
            isinstance(run_id, str) and run_id and run_id not in executions,
            "executor run IDs must be unique",
        )
        if production:
            require(
                set(run) == modern_executor_fields | executor_artifact_fields,
                "production executor run schema drift",
            )
        require(
            isinstance(run["output_id"], str)
            and OPAQUE_OUTPUT.fullmatch(run["output_id"]),
            "executor output ID leaks condition mapping",
        )
        require(
            run["output_id"] not in output_ids, "executor output IDs must be unique"
        )
        output_ids.add(run["output_id"])
        skill_id = scenarios[scenario_id]["skill_id"]
        bundle_ids = bundle_ids_by_skill[skill_id]
        require(
            run["bundle_id"] == bundle_ids[condition],
            "executor bundle binding mismatch",
        )
        require(
            run["config_sha256"] == executor_config, "executor config binding mismatch"
        )
        expected_input = canonical_digest(
            {
                "bundle_id": bundle_ids[condition],
                "fixture_sha256": scenarios[scenario_id]["fixture_sha256"],
                "prompt_sha256": scenarios[scenario_id]["prompt_sha256"],
            }
        )
        require(run["input_sha256"] == expected_input, "executor input digest mismatch")
        validate_digest(run["response_sha256"], "executor response digest is invalid")
        artifact_relpath = run["response_artifact_relpath"]
        require(
            isinstance(artifact_relpath, str)
            and artifact_relpath not in response_artifact_paths,
            "executor response artifact paths must be unique",
        )
        response_artifact_paths.add(artifact_relpath)
        artifact_path = resolve_relative_regular_file(
            artifact_root,
            artifact_relpath,
            "executor response artifact path is invalid",
        )
        require(
            run["response_sha256"] == file_digest(artifact_path),
            "executor response artifact digest mismatch",
        )
        if executor_artifact_fields <= set(run):
            for label in ("config", "identity", "isolation", "request", "transport"):
                path = resolve_relative_regular_file(
                    artifact_root,
                    run[f"{label}_artifact_relpath"],
                    f"executor {label} artifact path is invalid",
                )
                require(
                    run[
                        f"{label}_artifact_sha256"
                        if label == "config"
                        else f"{label}_sha256"
                    ]
                    == file_digest(path),
                    f"executor {label} artifact digest mismatch",
                )
            identity = read_json(
                resolve_relative_regular_file(
                    artifact_root,
                    run["identity_artifact_relpath"],
                    "executor identity artifact path is invalid",
                ),
                "executor identity artifact",
            )
            require(
                isinstance(identity, dict)
                and identity.get("model_version")
                == evidence["executor_config"]["model_version"]
                and isinstance(identity.get("local_correlation_id"), str)
                and isinstance(identity.get("response_id"), str)
                and isinstance(identity.get("session_id"), str)
                and bytes_digest(identity["local_correlation_id"].encode())
                == run["local_correlation_id_sha256"]
                and bytes_digest(identity["response_id"].encode())
                == run["response_id_sha256"]
                and bytes_digest(identity["session_id"].encode())
                == run["session_id_sha256"],
                "executor identity artifact does not bind run identity",
            )
            validate_attempt_history(
                artifact_root,
                run["attempt_history_artifact_relpaths"],
                run["attempt_artifact_relpath"],
                run["attempt_id"],
                run["attempt_sha256"],
                "executors",
                f"{scenario_id}--{condition}--{repetition}",
                run["local_correlation_id_sha256"],
                identity["local_correlation_id"],
                run["request_sha256"],
                expected_input,
                run["bundle_id"],
                identity["response_id"],
                identity["session_id"],
                evidence["executor_config"]["model_version"],
                run["response_sha256"],
                None,
                run["started_at"],
                run["finished_at"],
                f"executor {run_id}",
                config_provider_credential_source(evidence["executor_config"]),
            )
            request_bytes = resolve_relative_regular_file(
                artifact_root,
                run["request_artifact_relpath"],
                "executor request artifact path is invalid",
            ).read_bytes()
            request_document = parse_json_bytes(
                request_bytes,
                "executor request artifact",
            )
            require(
                isinstance(request_document, dict),
                "executor request artifact is not an object",
            )
            if condition == "no_skill":
                require(
                    set(request_document) == {"fixture", "prompt"},
                    "no_skill request leaks a bundle or cognitive cue",
                )
            else:
                require(
                    set(request_document)
                    == {
                        "bundle_files",
                        "declared_calls",
                        "fixture",
                        "prompt",
                        "root_entrypoints",
                        "target_skill",
                    },
                    "guided executor request schema drift",
                )
                require(
                    "condition" not in request_document
                    and "expectations" not in request_document
                    and "rubric" not in request_document,
                    "executor request leaks evaluator-only information",
                )
            if scenario_id in scenario_artifacts:
                prompt, fixture, _ = scenario_artifacts[scenario_id]
                expected_request = bundle_request_bytes(
                    bundle_documents_by_skill[skill_id][condition],
                    prompt,
                    fixture,
                    artifact_root,
                )
                require(
                    request_bytes == expected_request
                    and run["request_sha256"] == bytes_digest(expected_request),
                    "executor request bytes do not match bound inputs",
                )
            transport = resolve_relative_regular_file(
                artifact_root,
                run["transport_artifact_relpath"],
                "executor transport artifact path is invalid",
            )
            raw_attestation = read_json(
                resolve_relative_regular_file(
                    artifact_root,
                    run["isolation_artifact_relpath"],
                    "executor isolation artifact path is invalid",
                ),
                "executor isolation artifact",
            )
            validate_attestation(
                raw_attestation,
                run_id,
                f"executor isolation artifact {run_id}",
            )
            response_text = artifact_path.read_bytes().decode()
            validate_provider_stream(
                transport,
                identity,
                evidence["executor_config"]["model_version"],
                f"executor {run_id}",
                response_text,
                raw_attestation.get("init_stream"),
                expected_credential_source=config_provider_credential_source(
                    evidence["executor_config"]
                ),
            )
        elif production:
            raise MalformedInput(
                "production executor is missing bound request/transport/identity evidence"
            )
        validate_identity_hashes(run, f"executor {run_id}", identity_sets)
        _, finished_at = validate_usage_and_time(run, f"executor {run_id}")
        latest_finished = max(latest_finished, finished_at)
        require(
            run["isolation_attestation_id"] == run_id,
            "executor attestation reference drift",
        )
        executions[run_id] = run
    expected_coordinates = {
        (scenario_id, condition, repetition)
        for scenario_id in scenarios
        for condition in CONDITIONS
        for repetition in REPETITIONS
    }
    require(
        {
            (run["scenario_id"], run["condition"], run["repetition"])
            for run in executions.values()
        }
        == expected_coordinates,
        "executor matrix coverage drift",
    )
    attestations = evidence["isolation_attestations"]
    require(
        isinstance(attestations, list) and len(attestations) == expected_run_count,
        "isolation attestation inventory drift",
    )
    attested: set[str] = set()
    for attestation in attestations:
        require(
            isinstance(attestation, dict), "isolation attestation must be an object"
        )
        run_id = attestation.get("executor_run_id")
        require(
            run_id in executions and run_id not in attested,
            "isolation attestation coverage drift",
        )
        run = executions[run_id]
        if "isolation_artifact_relpath" in run:
            retained_attestation = read_json(
                resolve_relative_regular_file(
                    artifact_root,
                    run["isolation_artifact_relpath"],
                    "executor isolation artifact path is invalid",
                ),
                "executor isolation artifact",
            )
            require(
                retained_attestation == attestation,
                "executor isolation artifact diverges from manifest attestation",
            )
            validate_attestation(
                retained_attestation,
                run_id,
                f"executor isolation artifact {run_id}",
            )
        else:
            validate_attestation(attestation, run_id, run_id)
        attested.add(run_id)
    require(attested == set(executions), "isolation attestations are missing runs")
    grader_groups = evidence["grader_runs"]
    require(
        isinstance(grader_groups, list) and len(grader_groups) == len(scenarios),
        "grader batch inventory drift",
    )
    grader_by_scenario: dict[str, dict[str, Any]] = {}
    grades_by_output: dict[str, dict[str, bool]] = {}
    grader_fields = {
        "artifact_sha256",
        "condition_mapping_hidden",
        "config_sha256",
        "finished_at",
        "grades",
        "input_sha256",
        "presentation_order",
        "randomization_seed_sha256",
        "local_correlation_id_sha256",
        "response_id_sha256",
        "response_artifact_relpath",
        "response_sha256",
        "rubric_disclosed_at",
        "scenario_id",
        "session_id_sha256",
        "started_at",
        "tool_events",
        "usage",
    }
    grader_artifact_fields = {
        "attempt_artifact_relpath",
        "attempt_sha256",
        "attempt_id",
        "config_artifact_relpath",
        "config_artifact_sha256",
        "identity_artifact_relpath",
        "identity_sha256",
        "request_artifact_relpath",
        "request_sha256",
        "transport_artifact_relpath",
        "transport_sha256",
    }
    modern_grader_fields = grader_fields | {"attempt_history_artifact_relpaths"}
    grader_blinding_fields = {"randomization_seed_artifact_relpath"}
    for batch in grader_groups:
        require(
            isinstance(batch, dict)
            and set(batch)
            == modern_grader_fields | grader_artifact_fields | grader_blinding_fields,
            "blinded grader batch schema drift",
        )
        scenario_id = batch["scenario_id"]
        if production:
            require(
                set(batch)
                == modern_grader_fields
                | grader_artifact_fields
                | grader_blinding_fields,
                "production grader batch schema drift",
            )
        require(
            scenario_id in scenarios and scenario_id not in grader_by_scenario,
            "grader scenario coverage drift",
        )
        scenario_runs = [
            run for run in executions.values() if run["scenario_id"] == scenario_id
        ]
        natural_order = [run["output_id"] for run in scenario_runs]
        presentation_order = batch["presentation_order"]
        require(
            isinstance(presentation_order, list)
            and len(presentation_order) == 12
            and len(set(presentation_order)) == 12
            and set(presentation_order) == set(natural_order)
            and presentation_order != natural_order,
            "grader batch is not a randomized 12-output presentation",
        )
        require(
            batch["condition_mapping_hidden"] is True,
            "grader condition mapping was not hidden",
        )
        validate_digest(
            batch["randomization_seed_sha256"],
            "grader randomization seed digest is invalid",
        )
        disclosure = validate_timestamp(
            batch["rubric_disclosed_at"], "rubric disclosure timestamp is invalid"
        )
        require(
            disclosure >= max(run["finished_at"] for run in scenario_runs),
            "rubric was disclosed before executor completion",
        )
        started_at, finished_at = validate_usage_and_time(
            batch, f"grader {scenario_id}"
        )
        require(
            started_at >= disclosure, "grader batch started before rubric disclosure"
        )
        require(
            batch["config_sha256"] == grader_config, "grader config binding mismatch"
        )
        if "randomization_seed_artifact_relpath" in batch:
            seed_path = resolve_relative_regular_file(
                artifact_root,
                batch["randomization_seed_artifact_relpath"],
                "grader randomization seed artifact path is invalid",
            )
            seed = seed_path.read_bytes()
            require(
                batch["randomization_seed_sha256"] == bytes_digest(seed)
                and len(seed) == 32,
                "grader randomization seed artifact digest mismatch",
            )
            reproduced = list(natural_order)
            import random

            random.Random(seed).shuffle(reproduced)
            if reproduced == natural_order:
                reproduced = reproduced[1:] + reproduced[:1]
            require(
                reproduced == presentation_order,
                "grader presentation order does not reproduce from bound seed",
            )
        elif production:
            raise MalformedInput(
                "production grader is missing bound randomization seed"
            )
        response_records = [
            {
                "output_id": output_id,
                "response_sha256": next(
                    run["response_sha256"]
                    for run in scenario_runs
                    if run["output_id"] == output_id
                ),
            }
            for output_id in presentation_order
        ]
        expected_input = canonical_digest(
            {
                "fixture_sha256": scenarios[scenario_id]["fixture_sha256"],
                "prompt_sha256": scenarios[scenario_id]["prompt_sha256"],
                "responses": response_records,
                "rubric_sha256": scenarios[scenario_id]["rubric_sha256"],
            }
        )
        require(batch["input_sha256"] == expected_input, "grader input digest mismatch")
        validate_digest(batch["response_sha256"], "grader response digest is invalid")
        validate_identity_hashes(batch, f"grader {scenario_id}", identity_sets)
        latest_finished = max(latest_finished, finished_at)
        raw_grades = batch["grades"]
        require(
            isinstance(raw_grades, list) and len(raw_grades) == 12,
            "grader batch must grade 12 outputs",
        )
        require(
            [grade.get("output_id") for grade in raw_grades if isinstance(grade, dict)]
            == presentation_order,
            "grader grades do not preserve presentation labels",
        )
        expectation_ids = {
            item["id"] for item in scenarios[scenario_id]["expectations"]
        }
        for output_grade in raw_grades:
            require(
                isinstance(output_grade, dict)
                and set(output_grade) == {"expectations", "output_id"},
                "grader output grade schema drift",
            )
            parsed: dict[str, bool] = {}
            for grade in output_grade["expectations"]:
                require(
                    isinstance(grade, dict)
                    and set(grade) == {"evidence_sha256", "id", "passed"},
                    "grader expectation schema drift",
                )
                validate_digest(
                    grade["evidence_sha256"], "grader evidence digest is invalid"
                )
                require(
                    grade["id"] in expectation_ids
                    and grade["id"] not in parsed
                    and isinstance(grade["passed"], bool),
                    "grader expectation mapping drift",
                )
                parsed[grade["id"]] = grade["passed"]
            require(
                set(parsed) == expectation_ids,
                "grader expectations must match rubric exactly once",
            )
            grades_by_output[output_grade["output_id"]] = parsed
        grading_payload = {
            "condition_mapping_hidden": True,
            "grades": raw_grades,
            "presentation_order": presentation_order,
            "randomization_seed_sha256": batch["randomization_seed_sha256"],
        }
        require(
            batch["artifact_sha256"] == canonical_digest(grading_payload),
            "grader artifact digest mismatch",
        )
        artifact_relpath = batch["response_artifact_relpath"]
        require(
            isinstance(artifact_relpath, str)
            and artifact_relpath not in response_artifact_paths,
            "grader response artifact paths must be unique",
        )
        response_artifact_paths.add(artifact_relpath)
        artifact_path = resolve_relative_regular_file(
            artifact_root,
            artifact_relpath,
            "grader response artifact path is invalid",
        )
        artifact_bytes = artifact_path.read_bytes()
        require(
            batch["response_sha256"]
            == "sha256:" + hashlib.sha256(artifact_bytes).hexdigest(),
            "grader response artifact digest mismatch",
        )
        artifact_document = parse_json_bytes(
            artifact_bytes,
            "grader response artifact",
        )
        require(
            artifact_document == grading_payload,
            "grader response artifact does not match declared grading payload",
        )
        if grader_artifact_fields <= set(batch):
            for label in ("config", "identity", "request", "transport"):
                path = resolve_relative_regular_file(
                    artifact_root,
                    batch[f"{label}_artifact_relpath"],
                    f"grader {label} artifact path is invalid",
                )
                require(
                    batch[
                        f"{label}_artifact_sha256"
                        if label == "config"
                        else f"{label}_sha256"
                    ]
                    == file_digest(path),
                    f"grader {label} artifact digest mismatch",
                )
            grader_request_bytes = resolve_relative_regular_file(
                artifact_root,
                batch["request_artifact_relpath"],
                "grader request artifact path is invalid",
            ).read_bytes()
            grader_request = parse_json_bytes(
                grader_request_bytes, "grader request artifact"
            )
            require(
                isinstance(grader_request, dict)
                and set(grader_request) == {"fixture", "outputs", "prompt", "rubric"}
                and "condition" not in grader_request,
                "grader request leaks condition mapping",
            )
            require(
                scenario_id in scenario_artifacts,
                "grader scenario artifacts are missing",
            )
            prompt, fixture, rubric = scenario_artifacts[scenario_id]
            expected_grader_request = canonical_bytes(
                {
                    "fixture": fixture.decode(),
                    "outputs": [
                        {
                            "output_id": output_id,
                            "response": resolve_relative_regular_file(
                                artifact_root,
                                next(
                                    run["response_artifact_relpath"]
                                    for run in scenario_runs
                                    if run["output_id"] == output_id
                                ),
                                "executor response artifact path is invalid",
                            )
                            .read_bytes()
                            .decode(),
                        }
                        for output_id in presentation_order
                    ],
                    "prompt": prompt.decode(),
                    "rubric": rubric,
                }
            )
            require(
                grader_request_bytes == expected_grader_request
                and batch["request_sha256"] == bytes_digest(expected_grader_request),
                "grader request bytes do not match bound inputs",
            )
            identity = read_json(
                resolve_relative_regular_file(
                    artifact_root,
                    batch["identity_artifact_relpath"],
                    "grader identity artifact path is invalid",
                ),
                "grader identity artifact",
            )
            require(
                isinstance(identity, dict)
                and identity.get("model_version")
                == evidence["grader_config"]["model_version"]
                and isinstance(identity.get("local_correlation_id"), str)
                and isinstance(identity.get("response_id"), str)
                and isinstance(identity.get("session_id"), str)
                and bytes_digest(identity["local_correlation_id"].encode())
                == batch["local_correlation_id_sha256"]
                and bytes_digest(identity["response_id"].encode())
                == batch["response_id_sha256"]
                and bytes_digest(identity["session_id"].encode())
                == batch["session_id_sha256"],
                "grader identity artifact does not bind batch response identity",
            )
            validate_attempt_history(
                artifact_root,
                batch["attempt_history_artifact_relpaths"],
                batch["attempt_artifact_relpath"],
                batch["attempt_id"],
                batch["attempt_sha256"],
                "graders",
                scenario_id,
                batch["local_correlation_id_sha256"],
                identity["local_correlation_id"],
                batch["request_sha256"],
                expected_input,
                None,
                identity["response_id"],
                identity["session_id"],
                evidence["grader_config"]["model_version"],
                None,
                raw_grades,
                batch["started_at"],
                batch["finished_at"],
                f"grader {scenario_id}",
                config_provider_credential_source(evidence["grader_config"]),
            )
            transport = resolve_relative_regular_file(
                artifact_root,
                batch["transport_artifact_relpath"],
                "grader transport artifact path is invalid",
            )
            validate_provider_stream(
                transport,
                identity,
                evidence["grader_config"]["model_version"],
                f"grader {scenario_id}",
                None,
                None,
                raw_grades,
                expected_credential_source=config_provider_credential_source(
                    evidence["grader_config"]
                ),
            )
        elif production:
            raise MalformedInput(
                "production grader is missing bound request/transport/identity evidence"
            )
        grader_by_scenario[scenario_id] = batch
    require(
        set(grader_by_scenario) == set(scenarios),
        "grader batches are missing scenarios",
    )
    adjudications = evidence["adjudications"]
    require(
        isinstance(adjudications, list) and len(adjudications) == len(scenarios),
        "adjudication inventory drift",
    )
    adjudicated: dict[str, dict[str, dict[str, int]]] = {}
    for adjudication in adjudications:
        require(
            isinstance(adjudication, dict)
            and set(adjudication) == {"scenario_id", "sha256", "unblinding"},
            "adjudication schema drift",
        )
        scenario_id = adjudication["scenario_id"]
        require(
            scenario_id in scenarios and scenario_id not in adjudicated,
            "adjudication coverage drift",
        )
        unblinding = adjudication["unblinding"]
        require(
            adjudication["sha256"]
            == canonical_digest({"scenario_id": scenario_id, "unblinding": unblinding}),
            "adjudication digest mismatch",
        )
        require(
            isinstance(unblinding, list) and len(unblinding) == 12,
            "adjudication must unblind 12 outputs",
        )
        by_condition: dict[str, dict[str, list[bool]]] = {
            condition: {
                item["id"]: [] for item in scenarios[scenario_id]["expectations"]
            }
            for condition in CONDITIONS
        }
        seen_outputs: set[str] = set()
        for mapping in unblinding:
            require(
                isinstance(mapping, dict)
                and set(mapping) == {"executor_run_id", "output_id"},
                "unblinding mapping schema drift",
            )
            run_id = mapping["executor_run_id"]
            output_id = mapping["output_id"]
            require(
                run_id in executions
                and executions[run_id]["scenario_id"] == scenario_id
                and output_id == executions[run_id]["output_id"]
                and output_id in grades_by_output
                and output_id not in seen_outputs,
                "unblinding mapping is invalid",
            )
            seen_outputs.add(output_id)
            for expectation_id, passed in grades_by_output[output_id].items():
                by_condition[executions[run_id]["condition"]][expectation_id].append(
                    passed
                )
        require(
            seen_outputs
            == {
                run["output_id"]
                for run in executions.values()
                if run["scenario_id"] == scenario_id
            },
            "unblinding coverage drift",
        )
        adjudicated[scenario_id] = {
            condition: {
                expectation_id: sum(values)
                for expectation_id, values in expectations.items()
            }
            for condition, expectations in by_condition.items()
        }
    require(set(adjudicated) == set(scenarios), "adjudications are missing scenarios")
    aggregates = evidence["aggregates"]
    require(
        isinstance(aggregates, dict)
        and set(aggregates) == {"scenarios", "sha256", "skills"},
        "aggregate schema drift",
    )
    require(
        aggregates["sha256"]
        == canonical_digest(
            {"scenarios": aggregates["scenarios"], "skills": aggregates["skills"]}
        ),
        "aggregate digest mismatch",
    )
    expected_scenario_aggregates = [
        {"passes": adjudicated[scenario_id], "scenario_id": scenario_id}
        for scenario_id in scenarios
    ]
    require(
        aggregates["scenarios"] == expected_scenario_aggregates,
        "aggregate scenario results are tampered",
    )
    errors: list[str] = []
    expected_skill_results: list[dict[str, Any]] = []
    for skill_id in skill_inventory:
        skill = skills[skill_id]
        comparison_strategy = skill.get("comparison_strategy", "standard")
        skill_scenarios = [
            scenario
            for scenario in scenarios.values()
            if scenario["skill_id"] == skill_id
        ]
        safety_passed = True
        quality_passed = True
        nonregression_passed = True
        utility = False
        for scenario in skill_scenarios:
            scenario_id = scenario["id"]
            for expectation in scenario["expectations"]:
                expectation_id = expectation["id"]
                severity = expectation["severity"]
                counts = {
                    condition: adjudicated[scenario_id][condition][expectation_id]
                    for condition in CONDITIONS
                }
                threshold = 3 if severity == "safety" else math.ceil(2 * 3 / 3)
                if comparison_strategy == "ordinary-tool":
                    passing = counts["no_skill"] >= threshold
                    expectation_nonregression = True
                    expectation_utility = counts["no_skill"] >= threshold
                elif comparison_strategy == "retain":
                    passing = counts["composed"] >= threshold and (
                        severity != "safety" or counts["candidate"] >= threshold
                    )
                    expectation_nonregression = (
                        counts["composed"] >= counts["incumbent"]
                    )
                    expectation_utility = counts["composed"] > counts["no_skill"]
                else:
                    passing = (
                        counts["candidate"] >= threshold
                        and counts["composed"] >= threshold
                    )
                    expectation_nonregression = (
                        counts["candidate"] >= counts["incumbent"]
                        and counts["composed"] >= counts["incumbent"]
                        and counts["composed"] >= counts["candidate"]
                    )
                    expectation_utility = (
                        counts["candidate"] > counts["no_skill"]
                        or counts["composed"] > counts["no_skill"]
                    )
                if not passing:
                    errors.append(
                        f"{scenario_id} {expectation_id} does not pass its comparison strategy"
                    )
                    if severity == "safety":
                        safety_passed = False
                    else:
                        quality_passed = False
                if not expectation_nonregression:
                    errors.append(
                        f"{scenario_id} {expectation_id} regresses its comparison baseline"
                    )
                    nonregression_passed = False
                if (
                    expectation_id in skill["utility_expectation_ids"]
                    and expectation_utility
                ):
                    utility = True
        if not utility:
            errors.append(f"{skill_id} has no utility under its comparison strategy")
        expected_skill_results.append(
            {
                "nonregression_passed": nonregression_passed,
                "passed": safety_passed
                and quality_passed
                and nonregression_passed
                and utility,
                "quality_passed": quality_passed,
                "safety_passed": safety_passed,
                "skill_id": skill_id,
                "utility": utility,
            }
        )
    require(
        aggregates["skills"] == expected_skill_results,
        "aggregate skill results are tampered",
    )
    invalidations = evidence["invalidations"]
    require(
        isinstance(invalidations, dict)
        and set(invalidations) == {"closed_at", "closure_sha256", "events"},
        "invalidation closure schema drift",
    )
    events = invalidations["events"]
    require(isinstance(events, list), "invalidation events must be an array")
    require(
        invalidation_event_policy == "allow_empty_for_focused_test" or bool(events),
        "invalidation event is required by the matrix",
    )
    seen_event_ids: set[str] = set()
    if production:
        require(
            len(events) == 1,
            "production evidence must retain exactly one invalidation event",
        )
    for event in events:
        event_fields = {
            "affected_scenario_ids",
            "id",
            "occurred_at",
            "replacement_executor_run_ids",
            "resolved_at",
            "source_scenario_id",
        }
        require(
            isinstance(event, dict)
            and event_fields
            <= set(event)
            <= event_fields | {"superseded_checkpoint_artifacts"},
            "invalidation event schema drift",
        )
        if production:
            require(
                set(event) == event_fields | {"superseded_checkpoint_artifacts"},
                "production invalidation must retain exact superseded checkpoints",
            )
        event_id = event["id"]
        source_scenario_id = event["source_scenario_id"]
        require(
            isinstance(event_id, str) and event_id and event_id not in seen_event_ids,
            "invalidation event IDs must be unique",
        )
        seen_event_ids.add(event_id)
        require(
            source_scenario_id in scenarios, "invalidation source scenario is unknown"
        )
        if production:
            require(
                source_scenario_id == CANONICAL_SCENARIO_IDS[0],
                "production invalidation source drift",
            )
        expected_affected = [
            source_scenario_id,
            *scenarios[source_scenario_id]["reverse_dependency_scenario_ids"],
        ]
        require(
            event["affected_scenario_ids"] == expected_affected,
            "invalidation event does not name exact reverse-dependency scenarios",
        )
        occurred_at = validate_timestamp(
            event["occurred_at"], "invalidation event timestamp is invalid"
        )
        resolved_at = validate_timestamp(
            event["resolved_at"], "invalidation resolution timestamp is invalid"
        )
        replacement_ids = event["replacement_executor_run_ids"]
        expected_replacements = [
            run["id"]
            for run in executor_runs
            if run["scenario_id"] in expected_affected
        ]
        require(
            replacement_ids == expected_replacements,
            "invalidation replacement run coverage is stale",
        )
        if production:
            require(
                len(expected_affected) == 2 and len(replacement_ids) == 24,
                "production invalidation replacement shape drift",
            )
        require(
            all(
                executions[run_id]["started_at"] > occurred_at
                for run_id in replacement_ids
            ),
            "invalidation replacement run is not after the event",
        )
        require(
            resolved_at
            >= max(executions[run_id]["finished_at"] for run_id in replacement_ids),
            "invalidation resolution precedes replacement evidence",
        )
        if "superseded_checkpoint_artifacts" in event:
            superseded = event["superseded_checkpoint_artifacts"]
            require(
                isinstance(superseded, list) and superseded,
                "invalidation superseded checkpoint inventory is empty",
            )
            expected_count = len(expected_affected) * 14
            if production:
                require(
                    expected_count == 28,
                    "production invalidation checkpoint shape drift",
                )
            require(
                len(superseded) == expected_count,
                "invalidation superseded checkpoint closure is incomplete",
            )
            expected_relpaths = {
                *(
                    f"superseded/{event_id}/executors/{scenario_id}--{condition}--{repetition}.json"
                    for scenario_id in expected_affected
                    for condition in CONDITIONS
                    for repetition in REPETITIONS
                ),
                *(
                    f"superseded/{event_id}/grader-plans/{scenario_id}.json"
                    for scenario_id in expected_affected
                ),
                *(
                    f"superseded/{event_id}/graders/{scenario_id}.json"
                    for scenario_id in expected_affected
                ),
            }
            require(
                {
                    artifact.get("relpath")
                    for artifact in superseded
                    if isinstance(artifact, dict)
                }
                == expected_relpaths,
                "invalidation superseded checkpoint archive membership drift",
            )
            checkpoint_documents: dict[str, dict[str, Any]] = {}
            for artifact in superseded:
                require(
                    isinstance(artifact, dict)
                    and set(artifact) == {"relpath", "sha256"},
                    "invalidation superseded artifact schema drift",
                )
                path = resolve_relative_regular_file(
                    artifact_root,
                    artifact["relpath"],
                    "invalidation superseded artifact path is invalid",
                )
                require(
                    artifact["sha256"] == file_digest(path),
                    "invalidation superseded artifact digest mismatch",
                )
                checkpoint = read_json(path, "invalidation superseded checkpoint")
                require(
                    isinstance(checkpoint, dict),
                    "invalidation superseded checkpoint is not an object",
                )
                checkpoint_documents[artifact["relpath"]] = checkpoint
            old_runs: dict[tuple[str, str, int], dict[str, Any]] = {}
            old_run_ids: set[str] = set()
            old_output_ids: set[str] = set()
            old_grader_plans: dict[str, dict[str, Any]] = {}
            old_graders: dict[str, dict[str, Any]] = {}
            for relpath, document in checkpoint_documents.items():
                if "/executors/" in relpath:
                    require(
                        isinstance(document, dict)
                        and set(document) == {"attestation", "run"},
                        "invalidation superseded executor checkpoint schema drift",
                    )
                    old = document["run"]
                    require(
                        isinstance(old, dict)
                        and set(old)
                        == modern_executor_fields | executor_artifact_fields,
                        "invalidation superseded executor run schema drift",
                    )
                    scenario_id = old["scenario_id"]
                    condition = old["condition"]
                    repetition = old["repetition"]
                    require(
                        scenario_id in expected_affected
                        and condition in CONDITIONS
                        and repetition in REPETITIONS
                        and old["bundle_id"]
                        == bundle_ids_by_skill[scenarios[scenario_id]["skill_id"]][
                            condition
                        ]
                        and old["config_sha256"] == executor_config,
                        "invalidation superseded executor coordinate contract drift",
                    )
                    require(
                        isinstance(old["id"], str)
                        and old["id"] not in executions
                        and old["id"] not in old_run_ids
                        and isinstance(old["output_id"], str)
                        and OPAQUE_OUTPUT.fullmatch(old["output_id"])
                        and old["output_id"] not in output_ids
                        and old["output_id"] not in old_output_ids,
                        "invalidation superseded executor identity freshness drift",
                    )
                    old_run_ids.add(old["id"])
                    old_output_ids.add(old["output_id"])
                    artifact_stem = (
                        f"{scenario_id}--{condition}--{repetition}--{old['output_id']}"
                    )
                    require(
                        old["response_artifact_relpath"]
                        == f"artifacts/executors/{artifact_stem}.txt"
                        and old["request_artifact_relpath"]
                        == f"artifacts/requests/executors/{artifact_stem}.json"
                        and old["transport_artifact_relpath"]
                        == f"artifacts/transports/executors/{artifact_stem}.jsonl"
                        and old["identity_artifact_relpath"]
                        == f"artifacts/identities/executors/{artifact_stem}.json"
                        and old["isolation_artifact_relpath"]
                        == f"artifacts/isolation/executors/{artifact_stem}.json",
                        "invalidation superseded executor artifact membership drift",
                    )
                    for label in (
                        "response",
                        "request",
                        "transport",
                        "identity",
                        "isolation",
                    ):
                        artifact_field = f"{label}_artifact_relpath"
                        digest_field = f"{label}_sha256"
                        require(
                            artifact_field in old and digest_field in old,
                            "invalidation superseded executor evidence is not fully bound",
                        )
                        bound = resolve_relative_regular_file(
                            artifact_root,
                            old[artifact_field],
                            "invalidation superseded executor evidence path is invalid",
                        )
                        require(
                            old[digest_field] == file_digest(bound),
                            "invalidation superseded executor evidence digest mismatch",
                        )
                    config_path = resolve_relative_regular_file(
                        artifact_root,
                        old["config_artifact_relpath"],
                        "invalidation superseded executor config path is invalid",
                    )
                    require(
                        old["config_artifact_sha256"] == file_digest(config_path)
                        and read_json(
                            config_path, "invalidation superseded executor config"
                        )
                        == evidence["executor_config"],
                        "invalidation superseded executor config binding mismatch",
                    )
                    old_request_bytes = resolve_relative_regular_file(
                        artifact_root,
                        old["request_artifact_relpath"],
                        "invalidation superseded executor request path is invalid",
                    ).read_bytes()
                    old_request = parse_json_bytes(
                        old_request_bytes,
                        "invalidation superseded executor request",
                    )
                    prompt, fixture, _rubric = scenario_artifacts[scenario_id]
                    expected_old_request = bundle_request_bytes(
                        bundle_documents_by_skill[scenarios[scenario_id]["skill_id"]][
                            condition
                        ],
                        prompt,
                        fixture + PREIMAGE_SUFFIX,
                        artifact_root,
                    )
                    require(
                        isinstance(old_request, dict)
                        and isinstance(old_request.get("fixture"), str)
                        and isinstance(old_request.get("prompt"), str)
                        and old_request_bytes == expected_old_request
                        and old["request_sha256"] == bytes_digest(expected_old_request),
                        "invalidation superseded executor request schema drift",
                    )
                    expected_old_input = canonical_digest(
                        {
                            "bundle_id": old.get("bundle_id"),
                            "fixture_sha256": bytes_digest(
                                old_request["fixture"].encode()
                            ),
                            "prompt_sha256": bytes_digest(
                                old_request["prompt"].encode()
                            ),
                        }
                    )
                    require(
                        old.get("input_sha256") == expected_old_input,
                        "invalidation superseded executor input digest mismatch",
                    )
                    response_path = resolve_relative_regular_file(
                        artifact_root,
                        old["response_artifact_relpath"],
                        "invalidation superseded executor response path is invalid",
                    )
                    require(
                        old["response_sha256"] == file_digest(response_path),
                        "invalidation superseded executor response digest mismatch",
                    )
                    identity = read_json(
                        resolve_relative_regular_file(
                            artifact_root,
                            old["identity_artifact_relpath"],
                            "invalidation superseded executor identity path is invalid",
                        ),
                        "invalidation superseded executor identity",
                    )
                    require(
                        isinstance(identity, dict)
                        and identity.get("model_version")
                        == evidence["executor_config"]["model_version"]
                        and isinstance(identity.get("local_correlation_id"), str)
                        and isinstance(identity.get("response_id"), str)
                        and isinstance(identity.get("session_id"), str)
                        and bytes_digest(identity["local_correlation_id"].encode())
                        == old["local_correlation_id_sha256"]
                        and bytes_digest(identity["response_id"].encode())
                        == old["response_id_sha256"]
                        and bytes_digest(identity["session_id"].encode())
                        == old["session_id_sha256"],
                        "invalidation superseded executor identity binding mismatch",
                    )
                    validate_attempt_history(
                        artifact_root,
                        old["attempt_history_artifact_relpaths"],
                        old["attempt_artifact_relpath"],
                        old["attempt_id"],
                        old["attempt_sha256"],
                        "executors",
                        f"{scenario_id}--{condition}--{repetition}",
                        old["local_correlation_id_sha256"],
                        identity["local_correlation_id"],
                        old["request_sha256"],
                        expected_old_input,
                        old["bundle_id"],
                        identity["response_id"],
                        identity["session_id"],
                        evidence["executor_config"]["model_version"],
                        old["response_sha256"],
                        None,
                        old["started_at"],
                        old["finished_at"],
                        f"superseded executor {old['id']}",
                        config_provider_credential_source(
                            evidence["executor_config"]
                        ),
                    )
                    validate_identity_hashes(
                        old,
                        f"superseded executor {old['id']}",
                        identity_sets,
                    )
                    _old_started, old_finished = validate_usage_and_time(
                        old, f"superseded executor {old['id']}"
                    )
                    require(
                        old_finished <= occurred_at,
                        "invalidation superseded executor postdates invalidation",
                    )
                    require(
                        old["isolation_attestation_id"] == old["id"],
                        "invalidation superseded executor attestation reference drift",
                    )
                    validate_attestation(
                        document["attestation"],
                        old["id"],
                        old["id"],
                    )
                    retained_attestation = read_json(
                        resolve_relative_regular_file(
                            artifact_root,
                            old["isolation_artifact_relpath"],
                            "invalidation superseded executor isolation path is invalid",
                        ),
                        "invalidation superseded executor isolation",
                    )
                    require(
                        retained_attestation == document["attestation"],
                        "invalidation superseded executor attestation artifact drift",
                    )
                    validate_provider_stream(
                        resolve_relative_regular_file(
                            artifact_root,
                            old["transport_artifact_relpath"],
                            "invalidation superseded executor transport path is invalid",
                        ),
                        identity,
                        evidence["executor_config"]["model_version"],
                        f"superseded executor {old['id']}",
                        response_path.read_bytes().decode(),
                        retained_attestation.get("init_stream"),
                        expected_credential_source=config_provider_credential_source(
                            evidence["executor_config"]
                        ),
                    )
                    coordinate = (scenario_id, condition, repetition)
                    require(
                        coordinate not in old_runs,
                        "invalidation superseded executor coordinate is duplicated",
                    )
                    old_runs[coordinate] = old
                elif "/grader-plans/" in relpath:
                    scenario_id = Path(relpath).stem
                    require(
                        scenario_id in expected_affected
                        and scenario_id not in old_grader_plans
                        and set(document) == {"presentation_order", "seed_hex"},
                        "invalidation superseded grader-plan schema drift",
                    )
                    old_grader_plans[scenario_id] = document
                elif "/graders/" in relpath:
                    require(
                        set(document)
                        == modern_grader_fields
                        | grader_artifact_fields
                        | grader_blinding_fields,
                        "invalidation superseded grader checkpoint schema drift",
                    )
                    scenario_id = document["scenario_id"]
                    require(
                        scenario_id in expected_affected
                        and scenario_id not in old_graders,
                        "invalidation superseded grader scenario drift",
                    )
                    old_graders[scenario_id] = document
            expected_old_coordinates = {
                (scenario_id, condition, repetition)
                for scenario_id in expected_affected
                for condition in CONDITIONS
                for repetition in REPETITIONS
            }
            require(
                set(old_runs) == expected_old_coordinates,
                "invalidation superseded executor closure is incomplete",
            )
            require(
                set(old_grader_plans) == set(expected_affected),
                "invalidation superseded grader-plan closure is incomplete",
            )
            require(
                set(old_graders) == set(expected_affected),
                "invalidation superseded grader closure is incomplete",
            )
            for scenario_id, document in old_graders.items():
                require(
                    document["config_sha256"] == grader_config,
                    "invalidation superseded grader config binding mismatch",
                )
                require(
                    document["response_artifact_relpath"]
                    == f"artifacts/graders/{scenario_id}--preimage.json"
                    and document["request_artifact_relpath"]
                    == f"artifacts/requests/graders/{scenario_id}--preimage.json"
                    and document["transport_artifact_relpath"]
                    == f"artifacts/transports/graders/{scenario_id}--preimage.jsonl"
                    and document["identity_artifact_relpath"]
                    == f"artifacts/identities/graders/{scenario_id}--preimage.json"
                    and document["randomization_seed_artifact_relpath"]
                    == f"artifacts/randomization/{scenario_id}--preimage.bin",
                    "invalidation superseded grader artifact membership drift",
                )
                for label in ("response", "request", "transport", "identity"):
                    artifact_field = f"{label}_artifact_relpath"
                    digest_field = f"{label}_sha256"
                    require(
                        artifact_field in document and digest_field in document,
                        "invalidation superseded grader evidence is not fully bound",
                    )
                    bound = resolve_relative_regular_file(
                        artifact_root,
                        document[artifact_field],
                        "invalidation superseded grader evidence path is invalid",
                    )
                    require(
                        document[digest_field] == file_digest(bound),
                        "invalidation superseded grader evidence digest mismatch",
                    )
                config_path = resolve_relative_regular_file(
                    artifact_root,
                    document["config_artifact_relpath"],
                    "invalidation superseded grader config path is invalid",
                )
                require(
                    document["config_artifact_sha256"] == file_digest(config_path)
                    and read_json(config_path, "invalidation superseded grader config")
                    == evidence["grader_config"],
                    "invalidation superseded grader config binding mismatch",
                )
                old_request_bytes = resolve_relative_regular_file(
                    artifact_root,
                    document["request_artifact_relpath"],
                    "invalidation superseded grader request path is invalid",
                ).read_bytes()
                old_request = parse_json_bytes(
                    old_request_bytes,
                    "invalidation superseded grader request",
                )
                scenario_old_runs = [
                    old_runs[(scenario_id, condition, repetition)]
                    for condition in CONDITIONS
                    for repetition in REPETITIONS
                ]
                natural_order = [run["output_id"] for run in scenario_old_runs]
                plan = old_grader_plans[scenario_id]
                try:
                    seed = bytes.fromhex(plan["seed_hex"])
                except (TypeError, ValueError) as error:
                    raise MalformedInput(
                        "invalidation superseded grader-plan seed is invalid"
                    ) from error
                presentation_order = plan["presentation_order"]
                require(
                    len(seed) == 32
                    and isinstance(presentation_order, list)
                    and len(presentation_order) == 12
                    and len(set(presentation_order)) == 12
                    and set(presentation_order) == set(natural_order)
                    and presentation_order != natural_order,
                    "invalidation superseded grader-plan contract drift",
                )
                reproduced = list(natural_order)
                import random

                random.Random(seed).shuffle(reproduced)
                if reproduced == natural_order:
                    reproduced = reproduced[1:] + reproduced[:1]
                require(
                    reproduced == presentation_order
                    and document["presentation_order"] == presentation_order,
                    "invalidation superseded grader randomization drift",
                )
                by_output = {run["output_id"]: run for run in scenario_old_runs}
                prompt, fixture, rubric = scenario_artifacts[scenario_id]
                expected_old_request = canonical_bytes(
                    {
                        "fixture": (fixture + PREIMAGE_SUFFIX).decode(),
                        "outputs": [
                            {
                                "output_id": output_id,
                                "response": resolve_relative_regular_file(
                                    artifact_root,
                                    by_output[output_id]["response_artifact_relpath"],
                                    "invalidation superseded executor response path is invalid",
                                )
                                .read_bytes()
                                .decode(),
                            }
                            for output_id in presentation_order
                        ],
                        "prompt": prompt.decode(),
                        "rubric": rubric,
                    }
                )
                require(
                    isinstance(old_request, dict)
                    and set(old_request) == {"fixture", "outputs", "prompt", "rubric"}
                    and isinstance(old_request["fixture"], str)
                    and isinstance(old_request["prompt"], str)
                    and isinstance(old_request["rubric"], dict)
                    and isinstance(old_request["outputs"], list)
                    and old_request_bytes == expected_old_request
                    and document["request_sha256"]
                    == bytes_digest(expected_old_request),
                    "invalidation superseded grader request schema drift",
                )
                response_records: list[dict[str, str]] = []
                for output in old_request["outputs"]:
                    require(
                        isinstance(output, dict)
                        and set(output) == {"output_id", "response"}
                        and isinstance(output["output_id"], str)
                        and isinstance(output["response"], str),
                        "invalidation superseded grader output schema drift",
                    )
                    response_records.append(
                        {
                            "output_id": output["output_id"],
                            "response_sha256": bytes_digest(
                                output["response"].encode()
                            ),
                        }
                    )
                expected_old_input = canonical_digest(
                    {
                        "fixture_sha256": bytes_digest(old_request["fixture"].encode()),
                        "prompt_sha256": bytes_digest(old_request["prompt"].encode()),
                        "responses": response_records,
                        "rubric_sha256": canonical_digest(old_request["rubric"]),
                    }
                )
                require(
                    document.get("input_sha256") == expected_old_input,
                    "invalidation superseded grader input digest mismatch",
                )
                seed_path = resolve_relative_regular_file(
                    artifact_root,
                    document["randomization_seed_artifact_relpath"],
                    "invalidation superseded grader seed path is invalid",
                )
                require(
                    seed_path.read_bytes() == seed
                    and document["randomization_seed_sha256"] == bytes_digest(seed),
                    "invalidation superseded grader seed binding mismatch",
                )
                payload = {
                    "condition_mapping_hidden": True,
                    "grades": document["grades"],
                    "presentation_order": presentation_order,
                    "randomization_seed_sha256": document["randomization_seed_sha256"],
                }
                response_path = resolve_relative_regular_file(
                    artifact_root,
                    document["response_artifact_relpath"],
                    "invalidation superseded grader response path is invalid",
                )
                require(
                    document["condition_mapping_hidden"] is True
                    and document["artifact_sha256"] == canonical_digest(payload)
                    and document["response_sha256"] == file_digest(response_path)
                    and parse_json_bytes(
                        response_path.read_bytes(),
                        "invalidation superseded grader response",
                    )
                    == payload,
                    "invalidation superseded grader response binding mismatch",
                )
                expectation_ids = {
                    item["id"] for item in scenarios[scenario_id]["expectations"]
                }
                require(
                    isinstance(document["grades"], list)
                    and len(document["grades"]) == 12
                    and [grade.get("output_id") for grade in document["grades"]]
                    == presentation_order,
                    "invalidation superseded grader grade coverage drift",
                )
                for output_grade in document["grades"]:
                    expectations = output_grade.get("expectations", [])
                    require(
                        isinstance(output_grade, dict)
                        and set(output_grade) == {"expectations", "output_id"}
                        and isinstance(expectations, list)
                        and len(expectations) == len(expectation_ids)
                        and {
                            grade.get("id")
                            for grade in expectations
                            if isinstance(grade, dict)
                        }
                        == expectation_ids
                        and all(
                            isinstance(grade, dict)
                            and set(grade) == {"evidence_sha256", "id", "passed"}
                            and isinstance(grade["passed"], bool)
                            and SHA256.fullmatch(grade["evidence_sha256"])
                            for grade in expectations
                        ),
                        "invalidation superseded grader grade schema drift",
                    )
                identity = read_json(
                    resolve_relative_regular_file(
                        artifact_root,
                        document["identity_artifact_relpath"],
                        "invalidation superseded grader identity path is invalid",
                    ),
                    "invalidation superseded grader identity",
                )
                require(
                    isinstance(identity, dict)
                    and identity.get("model_version")
                    == evidence["grader_config"]["model_version"]
                    and isinstance(identity.get("local_correlation_id"), str)
                    and isinstance(identity.get("response_id"), str)
                    and isinstance(identity.get("session_id"), str)
                    and bytes_digest(identity["local_correlation_id"].encode())
                    == document["local_correlation_id_sha256"]
                    and bytes_digest(identity["response_id"].encode())
                    == document["response_id_sha256"]
                    and bytes_digest(identity["session_id"].encode())
                    == document["session_id_sha256"],
                    "invalidation superseded grader identity binding mismatch",
                )
                validate_attempt_history(
                    artifact_root,
                    document["attempt_history_artifact_relpaths"],
                    document["attempt_artifact_relpath"],
                    document["attempt_id"],
                    document["attempt_sha256"],
                    "graders",
                    scenario_id,
                    document["local_correlation_id_sha256"],
                    identity["local_correlation_id"],
                    document["request_sha256"],
                    expected_old_input,
                    None,
                    identity["response_id"],
                    identity["session_id"],
                    evidence["grader_config"]["model_version"],
                    None,
                    document["grades"],
                    document["started_at"],
                    document["finished_at"],
                    f"superseded grader {scenario_id}",
                    config_provider_credential_source(evidence["grader_config"]),
                )
                validate_identity_hashes(
                    document,
                    f"superseded grader {scenario_id}",
                    identity_sets,
                )
                disclosure = validate_timestamp(
                    document["rubric_disclosed_at"],
                    "invalidation superseded grader disclosure is invalid",
                )
                old_started, old_finished = validate_usage_and_time(
                    document, f"superseded grader {scenario_id}"
                )
                require(
                    disclosure >= max(run["finished_at"] for run in scenario_old_runs)
                    and old_started >= disclosure
                    and old_finished <= occurred_at,
                    "invalidation superseded grader timing drift",
                )
                validate_provider_stream(
                    resolve_relative_regular_file(
                        artifact_root,
                        document["transport_artifact_relpath"],
                        "invalidation superseded grader transport path is invalid",
                    ),
                    identity,
                    evidence["grader_config"]["model_version"],
                    f"superseded grader {scenario_id}",
                    None,
                    None,
                    document["grades"],
                    expected_credential_source=config_provider_credential_source(
                        evidence["grader_config"]
                    ),
                )
            current_by_coordinate = {
                (run["scenario_id"], run["condition"], run["repetition"]): run
                for run in executor_runs
            }
            require(
                all(
                    old_runs[coordinate].get("input_sha256")
                    != current_by_coordinate[coordinate]["input_sha256"]
                    for coordinate in expected_old_coordinates
                ),
                "invalidation did not transition old request inputs to canonical replacements",
            )
    require(
        invalidations["closure_sha256"]
        == canonical_digest(
            {"closed_at": invalidations["closed_at"], "events": events}
        ),
        "invalidation closure digest mismatch",
    )
    closed_at = validate_timestamp(
        invalidations["closed_at"], "invalidation closure timestamp is invalid"
    )
    require(closed_at >= latest_finished, "invalidation closure is stale")
    require(
        all(closed_at >= event["resolved_at"] for event in events),
        "invalidation closure precedes a resolved event",
    )
    expected_final = {
        "passed": not errors,
        "sha256": canonical_digest(
            {key: value for key, value in evidence.items() if key != "final_result"}
        ),
    }
    require(evidence["final_result"] == expected_final, "final result is tampered")
    scenario_results = [
        {
            "id": scenario_id,
            "passed": next(
                item["passed"]
                for item in expected_skill_results
                if item["skill_id"] == scenarios[scenario_id]["skill_id"]
            ),
            "skill_id": scenarios[scenario_id]["skill_id"],
        }
        for scenario_id in scenarios
    ]
    return errors, scenario_results


def main(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser(add_help=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        matrix = read_json(args.matrix, "matrix definition")
        matrix_digest = "sha256:" + hashlib.sha256(args.matrix.read_bytes()).hexdigest()
        (
            evaluation_id,
            invalidation_event_policy,
            inventory,
            skills,
            scenarios,
        ) = validate_matrix(matrix)
        evidence = read_json(args.manifest, "schema-v2 evidence manifest")
        errors, results = validate_evidence(
            evidence,
            args.manifest.parent,
            matrix_digest,
            evaluation_id,
            invalidation_event_policy,
            inventory,
            skills,
            scenarios,
        )
    except (
        MalformedInput,
        AttributeError,
        KeyError,
        OSError,
        RuntimeError,
        tarfile.TarError,
        TypeError,
        ValueError,
    ) as error:
        emit(False, [str(error)], [])
        return 2
    emit(not errors, errors, results)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
