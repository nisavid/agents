#!/usr/bin/env python3
"""Produce and validate observable Claude Skill-routing evidence.

Production evidence is bound to one clean Git commit, its tree, the exact
``git archive`` bytes, the real Claude Code executable selected from ``PATH``,
and a complete 113-case routing definition loaded only from that archive.
Fixture mode is a deterministic, provider-free transport for a proper subset.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import math
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from evidence_transport import (  # noqa: E402
    AttemptAllocation,
    AttemptSuccess,
    ProviderTransportFailure,
    allocate_attempt_journal,
    canonical_bytes as canonical,
    digest_bytes as digest,
    exact_claude_model as shared_exact_claude_model,
    failure_streams,
    frozen_atomic_write,
    json_file_bytes,
    lexical_absolute_path,
    lexical_artifact_path,
    next_attempt_envelope,
    normalize_repository_origin,
    private_atomic_write,
    prepare_private_directory,
    read_strict_json,
    resolve_executable_identity,
    resolved_path_is_within,
    run_attempt_journal,
    safe_regular_file,
    safe_relative_path,
    strict_json_bytes,
)


MANIFEST_NAME = "routing-evidence-v3.json"
DEFINITION_DEFAULT = "evals/skill-routing-matrix.json"
CANONICAL_REPOSITORY_URL = "https://github.com/nisavid/agents"
PHASE = "phase-2-observable-claude-skill-routing"
CLAIM = (
    "This records observable Claude CLI Skill tool use under a cooperative-agent "
    "threat model; it does not prove provider identity, billing, or Codex routing."
)
REPLAY_SEMANTICS = (
    "A durable successful checkpoint is adopted before any replay; a provider call "
    "without a durable successful checkpoint may replay once on resume."
)
EXPECTED_COUNTS = {
    "cold_start": 21,
    "explicit_invocation": 21,
    "trigger": 71,
    "total": 113,
}
REVIEWED_BUILTIN_AGENTS = frozenset(("claude", "Explore", "general-purpose", "Plan"))
CLAUDE_VERSION = re.compile(r"^(\d+\.\d+\.\d+) \(Claude Code\)$")
SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")


class RoutingError(RuntimeError):
    """A stable routing-evidence contract failure."""


class TransportFailure(ProviderTransportFailure, RoutingError):
    """A provider transport failure whose partial streams must be retained."""

    def __init__(
        self,
        message: str,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: Optional[int] = None,
        timed_out: bool = False,
    ) -> None:
        super().__init__(
            message,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            timed_out=timed_out,
        )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RoutingError(message)


def exact_claude_model(value: Any) -> bool:
    return shared_exact_claude_model(value)


def parse_json(content: str, label: str) -> Any:
    return strict_json_bytes(
        content.encode("utf-8"), label=label, error_factory=RoutingError
    )


def hex_digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def signed(value: dict[str, Any]) -> dict[str, Any]:
    require("sha256" not in value, "cannot sign a document that already has sha256")
    return {**value, "sha256": digest(canonical(value))}


def validate_signature(value: Any, label: str) -> dict[str, Any]:
    require(isinstance(value, dict), f"{label} must be an object")
    claimed = value.get("sha256")
    require(isinstance(claimed, str), f"{label} has no sha256")
    unsigned = {key: item for key, item in value.items() if key != "sha256"}
    require(claimed == digest(canonical(unsigned)), f"{label} sha256 mismatch")
    return value


def write_atomic(path: Path, content: bytes) -> None:
    private_atomic_write(path, content, error_factory=RoutingError)


def write_json(path: Path, value: Any) -> None:
    write_atomic(path, json_file_bytes(value))


def read_json(path: Path, label: Optional[str] = None) -> Any:
    return read_strict_json(path, label=label or str(path), error_factory=RoutingError)


def safe_relative(value: Any, label: str) -> Path:
    return Path(safe_relative_path(value, label=label, error_factory=RoutingError))


def safe_file(root: Path, relative_value: Any, label: str) -> Path:
    return safe_regular_file(
        root, relative_value, label=label, error_factory=RoutingError
    )


def ensure_artifact(root: Path, relative: str, content: bytes) -> Path:
    path = lexical_artifact_path(
        root, relative, label="artifact path", error_factory=RoutingError
    )
    try:
        frozen_atomic_write(path, content, error_factory=RoutingError)
    except RoutingError as error:
        raise RoutingError(
            f"unsafe or mismatched artifact: {relative}: {error}"
        ) from error
    return path


def git(repo: Path, *arguments: str, binary: bool = False) -> Any:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RoutingError(message or f"git {' '.join(arguments)} failed")
    return completed.stdout if binary else completed.stdout.decode("utf-8").strip()


def canonical_repository_origin(origin: str) -> str:
    if not origin:
        return ""
    aliases = {
        CANONICAL_REPOSITORY_URL,
        f"{CANONICAL_REPOSITORY_URL}.git",
        "git@github.com:nisavid/agents.git",
        "ssh://git@github.com/nisavid/agents.git",
    }
    return normalize_repository_origin(
        origin,
        canonical=CANONICAL_REPOSITORY_URL,
        aliases=aliases,
        error_factory=RoutingError,
        error_message=(
            "candidate repository origin is not the canonical nisavid/agents repository"
        ),
    )


def candidate_git_identity(
    repo: Path, revision: Optional[str] = None
) -> tuple[dict[str, str], bytes]:
    require(
        repo.is_dir() and not repo.is_symlink(), "candidate must be a real directory"
    )
    require(not git(repo, "status", "--porcelain"), "candidate checkout is not clean")
    head = git(repo, "rev-parse", "HEAD")
    if revision is not None:
        require(revision == head, "candidate revision is not the exact checkout HEAD")
    tree_oid = git(repo, "rev-parse", "HEAD^{tree}")
    archive = git(repo, "archive", "--format=tar", head, binary=True)
    origin_result = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        cwd=repo,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return (
        {
            "revision": head,
            "tree_oid": tree_oid,
            "archive_sha256": digest(archive),
            "archive_relpath": "artifacts/candidate/tree.tar",
            "repository": canonical_repository_origin(origin_result.stdout.strip())
            if origin_result.returncode == 0
            else "",
        },
        archive,
    )


def extract_archive(content: bytes, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="routing-candidate-", suffix=".tar") as raw:
        raw.write(content)
        raw.flush()
        with tarfile.open(raw.name, mode="r:") as archive:
            members = archive.getmembers()
            names: set[str] = set()
            for member in members:
                relative = safe_relative(
                    member.name.rstrip("/"), "candidate archive member"
                )
                require(
                    member.name not in names, "candidate archive has duplicate members"
                )
                names.add(member.name)
                require(
                    member.isdir() or member.isfile(),
                    "candidate archive contains links or special entries",
                )
                target = destination / relative
                require(not target.exists(), "candidate archive member collision")
                if member.isdir():
                    target.mkdir(parents=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                extracted = archive.extractfile(member)
                require(
                    extracted is not None, "candidate archive member has no content"
                )
                write_atomic(target, extracted.read())
                os.chmod(target, member.mode & 0o777)


@dataclass(frozen=True)
class RoutingCase:
    id: str
    tier: str
    target: str
    query: str
    expected_skills: tuple[str, ...]

    @property
    def skill_name(self) -> str:
        return self.target.partition(":")[2]


@dataclass(frozen=True)
class DefinitionBundle:
    path: str
    sha256: str
    source_files: tuple[dict[str, str], ...]
    skill_ids: tuple[str, ...]
    skill_names: tuple[str, ...]
    cases: tuple[RoutingCase, ...]

    def evidence(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "sources": list(self.source_files),
        }


def require_string(value: Any, label: str) -> str:
    require(isinstance(value, str) and value.strip() != "", f"{label} must be nonempty")
    return value


def reviewed_credential_provenance(value: Any) -> str:
    """Return one reviewed provenance descriptor without retaining raw input."""

    source = require_string(value, "Claude init apiKeySource")
    if source == "fixture":
        return "fixture"
    if source == "none":
        return "none"
    if source == "ANTHROPIC_API_KEY":
        return "ANTHROPIC_API_KEY"
    if source == "apiKeyHelper":
        return "apiKeyHelper"
    if source == "/login managed key":
        return "/login managed key"
    raise RoutingError("Claude init apiKeySource is not a reviewed descriptor")


def nonnegative_int(value: Any, label: str) -> int:
    require(type(value) is int and value >= 0, f"{label} must be a nonnegative integer")
    return value


def usage_accounting(result: dict[str, Any]) -> tuple[dict[str, Any], float | int]:
    usage = result.get("usage")
    require(isinstance(usage, dict), "Claude result usage is missing or malformed")
    nonnegative_int(usage.get("input_tokens"), "Claude result input_tokens")
    nonnegative_int(usage.get("output_tokens"), "Claude result output_tokens")
    total_cost = result.get("total_cost_usd")
    require(
        type(total_cost) in (int, float)
        and math.isfinite(total_cost)
        and total_cost >= 0,
        "Claude result total cost is missing or malformed",
    )
    return usage, total_cost


def expected_sequence(value: Any, skill_names: set[str], label: str) -> tuple[str, ...]:
    require(isinstance(value, list), f"{label} must be an array")
    require(
        all(isinstance(item, str) for item in value), f"{label} must contain strings"
    )
    sequence = tuple(value)
    require(len(sequence) == len(set(sequence)), f"{label} contains duplicates")
    require(
        set(sequence) <= skill_names, f"{label} names a skill outside the inventory"
    )
    return sequence


def load_definition(
    snapshot: Path, definition_relative: Any = DEFINITION_DEFAULT
) -> DefinitionBundle:
    definition_path = safe_file(snapshot, definition_relative, "routing definition")
    definition_bytes = definition_path.read_bytes()
    definition = parse_json(definition_bytes.decode("utf-8"), "routing definition")
    require(
        isinstance(definition, dict)
        and set(definition)
        == {
            "schema_version",
            "semantic_definition",
            "existing_trigger_sources",
            "skills",
        }
        and definition.get("schema_version") == 2,
        "routing definition schema drift",
    )
    semantic_source = definition["semantic_definition"]
    require(
        isinstance(semantic_source, dict)
        and set(semantic_source) == {"path", "sha256"},
        "semantic definition source schema drift",
    )
    semantic_path = safe_file(snapshot, semantic_source["path"], "semantic definition")
    semantic_bytes = semantic_path.read_bytes()
    require(
        semantic_source["sha256"] == hex_digest(semantic_bytes),
        "semantic definition source sha256 mismatch",
    )
    semantic = parse_json(semantic_bytes.decode("utf-8"), "semantic definition")
    semantic_skills = semantic.get("skills") if isinstance(semantic, dict) else None
    require(isinstance(semantic_skills, list), "semantic skill inventory is invalid")

    skills = definition["skills"]
    require(
        isinstance(skills, list) and len(skills) == 21,
        "routing definition must contain 21 skills",
    )
    skill_ids: list[str] = []
    for index, item in enumerate(skills):
        require(
            isinstance(item, dict)
            and set(item) == {"id", "cold_start", "explicit", "supplemental"},
            f"routing skill {index} schema drift",
        )
        identifier = require_string(item["id"], f"routing skill {index} id")
        require(identifier.count(":") == 1, "routing skill id must be plugin-qualified")
        require_string(item["cold_start"], f"{identifier} cold_start")
        require_string(item["explicit"], f"{identifier} explicit")
        skill_ids.append(identifier)
    require(
        len(skill_ids) == len(set(skill_ids)), "routing skill inventory has duplicates"
    )
    semantic_ids = [
        item.get("id") for item in semantic_skills if isinstance(item, dict)
    ]
    require(
        skill_ids == semantic_ids,
        "routing inventory differs from the frozen semantic matrix",
    )
    skill_names = [identifier.partition(":")[2] for identifier in skill_ids]
    require(
        len(skill_names) == len(set(skill_names)), "installed skill names are ambiguous"
    )
    skill_name_set = set(skill_names)

    sources = definition["existing_trigger_sources"]
    require(
        isinstance(sources, list) and len(sources) == 2,
        "existing trigger source inventory drift",
    )
    source_records: list[dict[str, str]] = [
        {
            "kind": "semantic",
            "path": semantic_source["path"],
            "sha256": digest(semantic_bytes),
        }
    ]
    sourced_skills: set[str] = set()
    existing_cases: list[RoutingCase] = []
    for source_index, source in enumerate(sources):
        require(
            isinstance(source, dict) and set(source) == {"skill", "path", "sha256"},
            "existing trigger source schema drift",
        )
        source_skill = require_string(source["skill"], "trigger source skill")
        require(
            source_skill in skill_ids, "trigger source skill is outside the inventory"
        )
        require(source_skill not in sourced_skills, "duplicate trigger source skill")
        sourced_skills.add(source_skill)
        trigger_path = safe_file(snapshot, source["path"], "trigger source")
        trigger_bytes = trigger_path.read_bytes()
        require(
            source["sha256"] == hex_digest(trigger_bytes),
            "trigger source sha256 mismatch",
        )
        triggers = parse_json(trigger_bytes.decode("utf-8"), "trigger source")
        require(isinstance(triggers, list), "trigger source must be an array")
        target_name = source_skill.partition(":")[2]
        for trigger_index, trigger in enumerate(triggers):
            require(
                isinstance(trigger, dict)
                and set(trigger) == {"query", "should_trigger"},
                "existing trigger case schema drift",
            )
            query = require_string(trigger["query"], "existing trigger query")
            require(
                type(trigger["should_trigger"]) is bool,
                "should_trigger must be a boolean",
            )
            expected = (target_name,) if trigger["should_trigger"] else ()
            existing_cases.append(
                RoutingCase(
                    f"trigger:existing:{source_skill}:{trigger_index}",
                    "trigger",
                    source_skill,
                    query,
                    expected,
                )
            )
        source_records.append(
            {
                "kind": "trigger",
                "path": source["path"],
                "sha256": digest(trigger_bytes),
                "skill": source_skill,
                "source_index": str(source_index),
            }
        )
    require(len(existing_cases) == 33, "frozen trigger sources must contain 33 cases")

    cases: list[RoutingCase] = []
    supplemental_queries: set[str] = set()
    base_queries = {
        item[key]
        for item in skills
        for key in ("cold_start", "explicit")
        if isinstance(item, dict)
    }
    for item in skills:
        identifier = item["id"]
        name = identifier.partition(":")[2]
        cases.extend(
            [
                RoutingCase(
                    f"cold:{identifier}",
                    "cold_start",
                    identifier,
                    item["cold_start"],
                    (name,),
                ),
                RoutingCase(
                    f"explicit:{identifier}",
                    "explicit_invocation",
                    identifier,
                    item["explicit"],
                    (name,),
                ),
            ]
        )
        supplemental = item["supplemental"]
        if identifier in sourced_skills:
            require(
                supplemental is None,
                "sourced skill must not replace frozen trigger cases",
            )
            continue
        require(
            isinstance(supplemental, dict)
            and set(supplemental)
            == {
                "positive",
                "positive_expected_skills",
                "negative",
                "negative_expected_skills",
            },
            f"{identifier} supplemental trigger schema drift",
        )
        positive = require_string(supplemental["positive"], f"{identifier} positive")
        negative = require_string(supplemental["negative"], f"{identifier} negative")
        require(positive != negative, f"{identifier} trigger pair is not distinct")
        require(
            positive not in base_queries and negative not in base_queries,
            f"{identifier} supplemental query reuses a cold or explicit query",
        )
        require(
            positive not in supplemental_queries
            and negative not in supplemental_queries,
            "supplemental trigger queries must be unique",
        )
        supplemental_queries.update((positive, negative))
        positive_expected = expected_sequence(
            supplemental["positive_expected_skills"],
            skill_name_set,
            f"{identifier} positive expectation",
        )
        negative_expected = expected_sequence(
            supplemental["negative_expected_skills"],
            skill_name_set,
            f"{identifier} negative expectation",
        )
        require(
            positive_expected == (name,),
            f"{identifier} positive must select its target",
        )
        require(
            name not in negative_expected,
            f"{identifier} negative expectation still selects its target",
        )
        cases.extend(
            [
                RoutingCase(
                    f"trigger:supplemental-positive:{identifier}",
                    "trigger",
                    identifier,
                    positive,
                    positive_expected,
                ),
                RoutingCase(
                    f"trigger:supplemental-negative:{identifier}",
                    "trigger",
                    identifier,
                    negative,
                    negative_expected,
                ),
            ]
        )
    cases.extend(existing_cases)
    counts = case_counts(cases)
    require(counts == EXPECTED_COUNTS, f"routing case counts drifted: {counts}")
    require(
        len({case.id for case in cases}) == len(cases),
        "routing case ids are not unique",
    )
    require(
        len({case_slug(case.id) for case in cases}) == len(cases),
        "routing case ids collide after artifact normalization",
    )
    return DefinitionBundle(
        path=Path(definition_relative).as_posix(),
        sha256=digest(definition_bytes),
        source_files=tuple(source_records),
        skill_ids=tuple(skill_ids),
        skill_names=tuple(skill_names),
        cases=tuple(cases),
    )


def case_counts(cases: Iterable[RoutingCase]) -> dict[str, int]:
    materialized = list(cases)
    return {
        "cold_start": sum(case.tier == "cold_start" for case in materialized),
        "explicit_invocation": sum(
            case.tier == "explicit_invocation" for case in materialized
        ),
        "trigger": sum(case.tier == "trigger" for case in materialized),
        "total": len(materialized),
    }


def install_skill_inventory(
    snapshot: Path, workspace: Path, skill_ids: Sequence[str]
) -> tuple[str, ...]:
    names: list[str] = []
    for target in skill_ids:
        plugin, name = target.split(":", 1)
        source = safe_file(
            snapshot,
            f"plugins/{plugin}/skills/{name}/SKILL.md",
            f"candidate skill {target}",
        )
        destination = workspace / ".claude" / "skills" / name / "SKILL.md"
        write_atomic(destination, source.read_bytes())
        names.append(name)
    return tuple(names)


def named_inventory(value: Any, label: str) -> tuple[str, ...]:
    require(isinstance(value, list), f"{label} must be an array")
    names: list[str] = []
    for item in value:
        if isinstance(item, str):
            names.append(item)
        elif (
            isinstance(item, dict)
            and set(item) == {"name"}
            and isinstance(item["name"], str)
        ):
            names.append(item["name"])
        else:
            raise RoutingError(f"{label} contains an unreviewed item shape")
    require(len(names) == len(set(names)), f"{label} contains duplicates")
    return tuple(names)


def nested_tool_calls(
    value: Any, path: tuple[Any, ...] = ()
) -> Iterable[tuple[tuple[Any, ...], dict[str, Any]]]:
    if isinstance(value, dict):
        if value.get("type") == "tool_use":
            yield path, value
        for key, child in value.items():
            yield from nested_tool_calls(child, path + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from nested_tool_calls(child, path + (index,))


def parse_jsonl(stream: bytes) -> list[dict[str, Any]]:
    try:
        text = stream.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RoutingError(f"Claude stream is not UTF-8: {error}") from error
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        value = parse_json(line, f"Claude JSONL line {line_number}")
        require(
            isinstance(value, dict), f"Claude JSONL line {line_number} is not an object"
        )
        events.append(value)
    require(events, "Claude stream is empty")
    return events


def observe_route(
    stream: bytes,
    case: RoutingCase,
    expected_skills: Sequence[str],
    *,
    expected_cwd: Path,
    expected_claude_version: Optional[str],
    expected_model: str,
) -> dict[str, Any]:
    require(
        expected_model == "fixture-model" or exact_claude_model(expected_model),
        "routing observation requires an exact Claude model identifier",
    )
    events = parse_jsonl(stream)
    init_events = [
        event
        for event in events
        if event.get("type") == "system" and event.get("subtype") == "init"
    ]
    result_events = [event for event in events if event.get("type") == "result"]
    require(len(init_events) == 1, "Claude stream must contain exactly one init event")
    require(
        len(result_events) == 1, "Claude stream must contain exactly one result event"
    )
    init = init_events[0]
    result = result_events[0]
    allowed_init = {
        "type",
        "subtype",
        "session_id",
        "tools",
        "skills",
        "mcp_servers",
        "plugins",
        "agents",
        "slash_commands",
        "cwd",
        "model",
        "permissionMode",
        "apiKeySource",
        "claude_code_version",
        "output_style",
    }
    require(
        not (set(init) - allowed_init),
        f"Claude init has unreviewed fields: {sorted(set(init) - allowed_init)}",
    )
    required_init = {
        "session_id",
        "tools",
        "skills",
        "mcp_servers",
        "plugins",
        "agents",
        "slash_commands",
        "cwd",
        "model",
        "apiKeySource",
        "claude_code_version",
    }
    require(required_init <= set(init), "Claude init inventory is incomplete")
    session_id = require_string(init["session_id"], "Claude init session_id")
    require(
        result.get("session_id") == session_id, "Claude init/result session mismatch"
    )
    for event in events:
        if "session_id" in event:
            require(event["session_id"] == session_id, "Claude event session mismatch")
    require(result.get("is_error") is False, "Claude result is not successful")
    require(
        result.get("subtype") == "success", "Claude result success subtype is missing"
    )
    usage, total_cost_usd = usage_accounting(result)
    require(
        named_inventory(init["tools"], "Claude init tools") == ("Skill",),
        "Claude init must expose exactly the Skill tool",
    )
    init_skills = named_inventory(init["skills"], "Claude init skills")
    require(
        set(init_skills) == set(expected_skills)
        and len(init_skills) == len(expected_skills),
        "Claude init skill inventory mismatch",
    )
    for capability in ("mcp_servers", "plugins", "slash_commands"):
        require(
            named_inventory(init[capability], f"Claude init {capability}") == (),
            f"Claude init exposed forbidden {capability}",
        )
    agents = named_inventory(init["agents"], "Claude init agents")
    require(
        all(isinstance(agent, str) for agent in init["agents"])
        and (not agents or set(agents) == REVIEWED_BUILTIN_AGENTS),
        "Claude agent discovery metadata is not the reviewed built-in set",
    )
    require(
        init["cwd"] == str(expected_cwd),
        "Claude init cwd does not match the isolated workspace",
    )
    init_model = require_string(init["model"], "Claude init model")
    require(
        init_model == expected_model,
        "Claude init model differs from the requested model",
    )
    cli_version = require_string(
        init["claude_code_version"], "Claude init claude_code_version"
    )
    credential_provenance = reviewed_credential_provenance(init["apiKeySource"])
    if expected_claude_version is not None:
        require(
            cli_version == expected_claude_version,
            "Claude init version differs from the executable identity",
        )

    selected: list[str] = []
    assistant_ids: list[str] = []
    assistant_models: list[str] = []
    assistant_count = 0
    final_assistant_content: list[Any] = []
    for event in events:
        direct_paths: set[tuple[Any, ...]] = set()
        if event.get("type") == "assistant":
            assistant_count += 1
            message = event.get("message")
            require(isinstance(message, dict), "assistant event message is malformed")
            assistant_ids.append(
                require_string(message.get("id"), "assistant message id")
            )
            assistant_model = require_string(
                message.get("model"), "assistant message model"
            )
            require(
                assistant_model == expected_model,
                "Claude assistant model differs from the requested model",
            )
            assistant_models.append(assistant_model)
            content = message.get("content")
            require(isinstance(content, list), "assistant message content is malformed")
            final_assistant_content = content
            for index, item in enumerate(content):
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    direct_paths.add(("message", "content", index))
        for path, call in nested_tool_calls(event):
            require(
                path in direct_paths,
                "nested or non-assistant tool_use event is forbidden",
            )
            require(call.get("name") == "Skill", "Claude invoked a non-Skill tool")
            payload = call.get("input")
            require(
                isinstance(payload, dict) and set(payload) == {"skill"},
                "Skill tool input schema drift",
            )
            skill = require_string(payload["skill"], "Skill tool selection")
            require(
                skill in expected_skills,
                "Skill tool selected a skill outside the frozen inventory",
            )
            selected.append(skill)
    require(assistant_count > 0, "Claude stream has no assistant message")
    require(
        len(set(assistant_ids)) == 1,
        "Claude stream reported mixed assistant response IDs",
    )
    result_text = require_string(result.get("result"), "Claude result")
    assistant_text_parts = [
        block.get("text")
        for block in final_assistant_content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    require(
        bool(assistant_text_parts)
        and all(isinstance(part, str) for part in assistant_text_parts)
        and "".join(assistant_text_parts) == result_text,
        "Claude final assistant text does not bind the result",
    )
    require(
        tuple(selected) == case.expected_skills,
        f"routing selection mismatch for {case.id}: {selected}",
    )
    return {
        "session_id": session_id,
        "selected_skills": selected,
        "init": {
            "tools": init["tools"],
            "skills": init["skills"],
            "mcp_servers": init["mcp_servers"],
            "plugins": init["plugins"],
            "agents": init["agents"],
            "slash_commands": init["slash_commands"],
            "cwd": init["cwd"],
            "model": init_model,
            "claude_code_version": cli_version,
            "apiKeySource": credential_provenance,
        },
        "assistant_message_ids": assistant_ids,
        "assistant_models": assistant_models,
        "usage": usage,
        "total_cost_usd": total_cost_usd,
    }


def fixture_stream(case: RoutingCase, skills: Sequence[str], cwd: Path) -> bytes:
    session = "fixture-" + hashlib.sha256(case.id.encode()).hexdigest()[:16]
    events: list[dict[str, Any]] = [
        {
            "type": "system",
            "subtype": "init",
            "session_id": session,
            "tools": ["Skill"],
            "skills": list(skills),
            "mcp_servers": [],
            "plugins": [],
            "agents": [],
            "slash_commands": [],
            "cwd": str(cwd),
            "model": "fixture-model",
            "apiKeySource": "fixture",
            "claude_code_version": "fixture-1",
        }
    ]
    content: list[dict[str, Any]] = []
    for index, skill in enumerate(case.expected_skills):
        content.append(
            {
                "type": "tool_use",
                "id": f"fixture-tool-{index}",
                "name": "Skill",
                "input": {"skill": skill},
            }
        )
    content.append({"type": "text", "text": "fixture"})
    events.extend(
        [
            {
                "type": "assistant",
                "message": {
                    "id": "fixture-message-"
                    + hashlib.sha256(case.id.encode()).hexdigest()[:12],
                    "model": "fixture-model",
                    "content": content,
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "session_id": session,
                "result": "fixture",
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "total_cost_usd": 0.0,
            },
        ]
    )
    return b"\n".join(canonical(event) for event in events) + b"\n"


def fixture_transport(
    case: RoutingCase, skills: Sequence[str], cwd: Path
) -> tuple[bytes, bytes, list[str]]:
    return fixture_stream(case, skills, cwd), b"", ["fixture"]


FIXTURE_TRANSPORT: Callable[
    [RoutingCase, Sequence[str], Path], tuple[bytes, bytes, list[str]]
] = fixture_transport


def resolve_claude_identity() -> dict[str, str]:
    return resolve_executable_identity(
        "claude",
        error_factory=RoutingError,
        display_name="Claude Code",
        version_validator=lambda value: CLAUDE_VERSION.fullmatch(value) is not None,
    )


def runtime_claude_code_version(identity: dict[str, str]) -> str:
    match = CLAUDE_VERSION.fullmatch(identity.get("version", ""))
    require(match is not None, "Claude Code runtime version identity is invalid")
    return match.group(1)


def claude_command(executable: str, model: str) -> list[str]:
    return [
        executable,
        "-p",
        "--safe-mode",
        "--tools",
        "Skill",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--no-session-persistence",
        "--setting-sources",
        "project",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        model,
    ]


def claude_transport(
    case: RoutingCase,
    workspace: Path,
    isolation: dict[str, str],
    binary_identity: dict[str, str],
    requested_model: str,
    timeout_seconds: int,
) -> tuple[bytes, bytes, list[str]]:
    require(
        resolve_claude_identity() == binary_identity,
        "Claude executable identity drift",
    )
    command = claude_command(binary_identity["path"], requested_model)
    environment = {
        "HOME": isolation["home"],
        "CLAUDE_CONFIG_DIR": isolation["claude_config"],
        "XDG_CONFIG_HOME": isolation["xdg_config"],
        "XDG_CACHE_HOME": isolation["xdg_cache"],
        "XDG_DATA_HOME": isolation["xdg_data"],
        "XDG_STATE_HOME": isolation["xdg_state"],
        "TMPDIR": isolation["tmp"],
        "PATH": str(Path(binary_identity["path"]).parent) + os.pathsep + os.defpath,
        "LANG": "C.UTF-8",
    }
    try:
        completed = subprocess.run(
            command,
            input=case.query.encode("utf-8"),
            cwd=workspace,
            env=environment,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        stdout, stderr = failure_streams(error)
        try:
            require(
                resolve_claude_identity() == binary_identity,
                "Claude executable identity drift",
            )
        except RoutingError as identity_error:
            raise TransportFailure(
                str(identity_error),
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
            ) from identity_error
        raise TransportFailure(
            f"Claude routing transport timed out after {timeout_seconds} seconds",
            stdout=stdout,
            stderr=stderr,
            timed_out=True,
        ) from error
    try:
        require(
            resolve_claude_identity() == binary_identity,
            "Claude executable identity drift",
        )
    except RoutingError as error:
        raise TransportFailure(
            str(error),
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        ) from error
    if completed.returncode != 0:
        raise TransportFailure(
            f"Claude routing transport failed with exit {completed.returncode}",
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )
    return completed.stdout, completed.stderr, command


def timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def case_slug(case_id: str) -> str:
    return SAFE_ID.sub("--", case_id).strip("-")


def next_attempt(output: Path, case: RoutingCase) -> tuple[int, str, AttemptAllocation]:
    directory = output / "attempts" / case_slug(case.id)
    index = next_attempt_envelope(
        directory,
        pattern=re.compile(r"attempt-(\d{4})\.json"),
        error_factory=RoutingError,
    )
    stem = f"attempts/{case_slug(case.id)}/attempt-{index:04d}"
    allocation = allocate_attempt_journal(
        output,
        attempt_relpath=f"{stem}.json",
        stream_relpaths={
            "stdout": f"{stem}.stdout.jsonl",
            "stderr": f"{stem}.stderr.txt",
        },
        error_factory=RoutingError,
    )
    return index, stem, allocation


def configuration(
    adapter: str,
    requested_model: Optional[str],
    binary_identity: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    if adapter == "claude-cli":
        require(
            exact_claude_model(requested_model),
            "production configuration requires an exact Claude model identifier",
        )
        runtime = validate_binary_identity(binary_identity, True)
    else:
        runtime = None
    return {
        "adapter": adapter,
        "requested_model": requested_model,
        "runtime": runtime,
        "tool_allowlist": ["Skill"],
        "mcp": {"strict": True, "servers": {}},
        "session_persistence": False,
        "setting_sources": ["project"],
        "slash_commands": False,
        "output_format": "stream-json",
    }


def isolation_contract() -> dict[str, Any]:
    return {
        "scope": "fresh-per-case",
        "home": "fresh",
        "claude_config": "fresh",
        "xdg_config": "fresh",
        "xdg_cache": "fresh",
        "xdg_data": "fresh",
        "xdg_state": "fresh",
        "tmp": "fresh",
        "workspace": "fresh",
        "cleanup": "verified-before-checkpoint",
        "session_persistence": "disabled",
        "mcp": "strict-empty",
        "tools": ["Skill"],
        "setting_sources": ["project"],
    }


def state_document(
    run_contract: dict[str, Any], completed: Sequence[str]
) -> dict[str, Any]:
    return signed(
        {
            "schema_version": 2,
            "run_contract": run_contract,
            "run_contract_sha256": digest(canonical(run_contract)),
            "completed_case_ids": list(completed),
        }
    )


def load_or_create_state(output: Path, run_contract: dict[str, Any]) -> dict[str, Any]:
    state_path = output / "run-state.json"
    if state_path.exists() or state_path.is_symlink():
        require(
            state_path.is_file() and not state_path.is_symlink(), "run-state is unsafe"
        )
        state = validate_signature(read_json(state_path, "run-state"), "run-state")
        require(
            state.get("schema_version") == 2
            and state.get("run_contract") == run_contract
            and state.get("run_contract_sha256") == digest(canonical(run_contract)),
            "existing output belongs to a different routing run",
        )
        completed = state.get("completed_case_ids")
        require(
            isinstance(completed, list)
            and all(isinstance(item, str) for item in completed)
            and len(completed) == len(set(completed)),
            "run-state completed case inventory is malformed",
        )
        return state
    output.mkdir(parents=True, exist_ok=True)
    require(
        not any(output.iterdir()),
        "refusing to overwrite an output without matching run-state",
    )
    state = state_document(run_contract, [])
    write_json(state_path, state)
    return state


def observed_credential_provenance(observation: dict[str, Any]) -> str:
    """Revalidate the reviewed descriptor at every evidence projection."""

    init = observation.get("init")
    require(isinstance(init, dict), "routing observation init is malformed")
    return reviewed_credential_provenance(init["apiKeySource"])


def identity_document(
    case: RoutingCase, stream: bytes, observation: dict[str, Any]
) -> dict[str, Any]:
    return {
        "case_id": case.id,
        "query_sha256": digest(case.query.encode("utf-8")),
        "stream_sha256": digest(stream),
        "session_id": observation["session_id"],
        "assistant_message_ids": observation["assistant_message_ids"],
        "assistant_models": observation["assistant_models"],
        "init_model": observation["init"]["model"],
        "claude_code_version": observation["init"]["claude_code_version"],
        "apiKeySource": observed_credential_provenance(observation),
        "cwd": observation["init"]["cwd"],
        "usage": observation["usage"],
        "total_cost_usd": observation["total_cost_usd"],
    }


def execute_case(
    output: Path,
    snapshot: Path,
    bundle: DefinitionBundle,
    case: RoutingCase,
    adapter: str,
    binary_identity: dict[str, str],
    requested_model: Optional[str],
    timeout_seconds: int,
) -> dict[str, Any]:
    attempt_index, attempt_stem, allocation = next_attempt(output, case)
    stdout_relpath = allocation.stream_relpaths["stdout"]
    stderr_relpath = allocation.stream_relpaths["stderr"]
    query_relpath = f"artifacts/queries/{case_slug(case.id)}.txt"
    query_bytes = case.query.encode("utf-8")
    ensure_artifact(output, query_relpath, query_bytes)
    temporary_path = ""
    observation: Optional[dict[str, Any]] = None
    stream = b""
    stderr = b""
    command: list[str] = []
    try:
        with tempfile.TemporaryDirectory(
            prefix=f"skill-routing-{case_slug(case.id)}-"
        ) as temporary:
            temporary_path = temporary
            root = Path(temporary).resolve(strict=True)
            workspace = root / "workspace"
            paths = {
                "workspace": str(workspace),
                "home": str(root / "home"),
                "claude_config": str(root / "claude-config"),
                "xdg_config": str(root / "xdg-config"),
                "xdg_cache": str(root / "xdg-cache"),
                "xdg_data": str(root / "xdg-data"),
                "xdg_state": str(root / "xdg-state"),
                "tmp": str(root / "tmp"),
            }
            for path in paths.values():
                Path(path).mkdir(parents=True, exist_ok=True)
            installed = install_skill_inventory(snapshot, workspace, bundle.skill_ids)
            require(installed == bundle.skill_names, "installed skill inventory drift")
            if adapter == "fixture":
                planned_command = ["fixture"]
            else:
                assert requested_model is not None
                planned_command = claude_command(
                    binary_identity["path"], requested_model
                )
            initial_attempt = {
                "schema_version": 1,
                "attempt_index": attempt_index,
                "case_id": case.id,
                "adapter": adapter,
                "query_sha256": digest(query_bytes),
                "command": planned_command,
                "timeout_seconds": timeout_seconds,
                "isolation": paths,
            }

            def invoke_attempt() -> AttemptSuccess:
                if adapter == "fixture":
                    observed_stream, observed_stderr, observed_command = (
                        FIXTURE_TRANSPORT(case, installed, workspace)
                    )
                else:
                    assert requested_model is not None
                    observed_stream, observed_stderr, observed_command = (
                        claude_transport(
                            case,
                            workspace,
                            paths,
                            binary_identity,
                            requested_model,
                            timeout_seconds,
                        )
                    )
                try:
                    observed_route = observe_route(
                        observed_stream,
                        case,
                        installed,
                        expected_cwd=workspace,
                        expected_claude_version=(
                            None
                            if adapter == "fixture"
                            else runtime_claude_code_version(binary_identity)
                        ),
                        expected_model=(
                            "fixture-model" if adapter == "fixture" else requested_model
                        ),
                    )
                except RoutingError as error:
                    raise TransportFailure(
                        str(error),
                        stdout=observed_stream,
                        stderr=observed_stderr,
                        returncode=0,
                    ) from error
                return AttemptSuccess(
                    value=(
                        observed_stream,
                        observed_stderr,
                        observed_command,
                        observed_route,
                    ),
                    streams={
                        "stdout": observed_stream,
                        "stderr": observed_stderr,
                    },
                    fields={
                        "returncode": 0,
                        "timed_out": False,
                        "command": observed_command,
                    },
                )

            def failure_fields(
                error: BaseException, classification: str
            ) -> dict[str, Any]:
                return {
                    "error": str(error),
                    "returncode": getattr(error, "returncode", None),
                    "timed_out": classification == "timeout",
                }

            attempt_value, attempt_document = run_attempt_journal(
                allocation,
                initial=initial_attempt,
                invoke=invoke_attempt,
                document_writer=write_json,
                artifact_writer=lambda path, content: frozen_atomic_write(
                    path, content, error_factory=RoutingError
                ),
                clock=timestamp,
                status_names={
                    "started": "running",
                    "success": "success",
                    "failure": "failed",
                    "timeout": "timeout",
                },
                stream_fields={
                    "stdout": ("stdout_relpath", "stdout_sha256"),
                    "stderr": ("stderr_relpath", "stderr_sha256"),
                },
                digest=digest,
                signer=signed,
                failure_fields=failure_fields,
            )
            stream, stderr, command, observation = attempt_value
        require(not Path(temporary_path).exists(), "case isolation cleanup failed")
    except BaseException:
        if temporary_path:
            require(not Path(temporary_path).exists(), "case isolation cleanup failed")
        raise
    assert observation is not None
    identity = identity_document(case, stream, observation)
    identity_relpath = f"artifacts/identities/{case_slug(case.id)}.json"
    identity_bytes = json_file_bytes(identity)
    ensure_artifact(output, identity_relpath, identity_bytes)
    record = {
        "id": case.id,
        "tier": case.tier,
        "target": case.target,
        "query_sha256": digest(query_bytes),
        "expected_skills": list(case.expected_skills),
        "selected_skills": observation["selected_skills"],
        "session_id": observation["session_id"],
        "init_model": observation["init"]["model"],
        "assistant_models": observation["assistant_models"],
        "claude_code_version": observation["init"]["claude_code_version"],
        "apiKeySource": observed_credential_provenance(observation),
        "usage": observation["usage"],
        "total_cost_usd": observation["total_cost_usd"],
    }
    checkpoint = signed(
        {
            "schema_version": 1,
            "case_id": case.id,
            "query_relpath": query_relpath,
            "query_sha256": digest(query_bytes),
            "attempt_relpath": allocation.attempt_relpath,
            "attempt_sha256": digest(json_file_bytes(attempt_document)),
            "stream_relpath": stdout_relpath,
            "stream_sha256": digest(stream),
            "stderr_relpath": stderr_relpath,
            "stderr_sha256": digest(stderr),
            "identity_relpath": identity_relpath,
            "identity_sha256": digest(identity_bytes),
            "expected_workspace": observation["init"]["cwd"],
            "cleanup_verified": True,
            "observation": observation,
            "record": record,
        }
    )
    write_json(output / f"checkpoints/{case_slug(case.id)}.json", checkpoint)
    return checkpoint


def validate_checkpoint(
    evidence: Path,
    case: RoutingCase,
    bundle: DefinitionBundle,
    binary_identity: dict[str, str],
    adapter: str,
    requested_model: Optional[str],
) -> dict[str, Any]:
    checkpoint_relpath = f"checkpoints/{case_slug(case.id)}.json"
    checkpoint_path = safe_file(evidence, checkpoint_relpath, "case checkpoint")
    checkpoint = validate_signature(
        read_json(checkpoint_path, checkpoint_relpath), checkpoint_relpath
    )
    require(
        checkpoint.get("schema_version") == 1 and checkpoint.get("case_id") == case.id,
        "case checkpoint identity mismatch",
    )
    require(checkpoint.get("cleanup_verified") is True, "case cleanup was not verified")

    def artifact(relative_key: str, digest_key: str, label: str) -> bytes:
        path = safe_file(evidence, checkpoint.get(relative_key), label)
        content = path.read_bytes()
        require(
            digest(content) == checkpoint.get(digest_key), f"{label} sha256 mismatch"
        )
        return content

    query = artifact("query_relpath", "query_sha256", "case query")
    require(
        query == case.query.encode("utf-8"),
        "case query bytes differ from the frozen definition",
    )
    stream = artifact("stream_relpath", "stream_sha256", "case stream")
    artifact("stderr_relpath", "stderr_sha256", "case stderr")
    identity_bytes = artifact("identity_relpath", "identity_sha256", "case identity")
    attempt_bytes = artifact("attempt_relpath", "attempt_sha256", "case attempt")
    attempt = validate_signature(
        parse_json(attempt_bytes.decode("utf-8"), "case attempt"), "case attempt"
    )
    require(
        attempt.get("status") == "success"
        and attempt.get("case_id") == case.id
        and attempt.get("stdout_sha256") == digest(stream),
        "checkpoint references an unsuccessful or mismatched attempt",
    )
    expected_workspace = checkpoint.get("expected_workspace")
    require(
        isinstance(expected_workspace, str) and expected_workspace,
        "checkpoint workspace is missing",
    )
    observation = observe_route(
        stream,
        case,
        bundle.skill_names,
        expected_cwd=Path(expected_workspace),
        expected_claude_version=(
            None
            if adapter == "fixture"
            else runtime_claude_code_version(binary_identity)
        ),
        expected_model=("fixture-model" if adapter == "fixture" else requested_model),
    )
    require(
        observation == checkpoint.get("observation"), "checkpoint observation mismatch"
    )
    identity = parse_json(identity_bytes.decode("utf-8"), "case identity")
    require(
        identity == identity_document(case, stream, observation),
        "case identity mismatch",
    )
    expected_record = {
        "id": case.id,
        "tier": case.tier,
        "target": case.target,
        "query_sha256": digest(case.query.encode("utf-8")),
        "expected_skills": list(case.expected_skills),
        "selected_skills": observation["selected_skills"],
        "session_id": observation["session_id"],
        "init_model": observation["init"]["model"],
        "assistant_models": observation["assistant_models"],
        "claude_code_version": observation["init"]["claude_code_version"],
        "apiKeySource": observed_credential_provenance(observation),
        "usage": observation["usage"],
        "total_cost_usd": observation["total_cost_usd"],
    }
    require(checkpoint.get("record") == expected_record, "checkpoint record mismatch")
    return checkpoint


def validate_attempt_inventory(
    evidence: Path,
    bundle: DefinitionBundle,
    run_contract: dict[str, Any],
    cases: Sequence[RoutingCase],
) -> list[dict[str, Any]]:
    attempts_root = evidence / "attempts"
    require(
        attempts_root.is_dir() and not attempts_root.is_symlink(),
        "routing attempts root is unsafe or missing",
    )
    actual_files: set[str] = set()
    attempt_paths: list[Path] = []
    for path in sorted(attempts_root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(evidence).as_posix()
        require(
            not path.is_symlink(),
            f"routing attempt inventory contains a symlink: {relative}",
        )
        if path.is_dir():
            continue
        require(
            path.is_file(),
            f"routing attempt inventory contains a special entry: {relative}",
        )
        actual_files.add(relative)
        if path.suffix == ".json":
            attempt_paths.append(path)

    case_by_slug = {case_slug(case.id): case for case in cases}
    case_order = {case.id: index for index, case in enumerate(cases)}
    expected_command = (
        ["fixture"]
        if run_contract["adapter"] == "fixture"
        else claude_command(
            run_contract["binary_identity"]["path"],
            run_contract["requested_model"],
        )
    )
    common_keys = {
        "schema_version",
        "attempt_index",
        "case_id",
        "adapter",
        "status",
        "started_at",
        "query_sha256",
        "command",
        "timeout_seconds",
        "isolation",
        "sha256",
    }
    completed_keys = common_keys | {
        "finished_at",
        "returncode",
        "timed_out",
        "stdout_relpath",
        "stdout_sha256",
        "stderr_relpath",
        "stderr_sha256",
    }
    indexed: list[tuple[int, int, dict[str, Any]]] = []
    referenced_files: set[str] = set()
    statuses_by_case: dict[str, list[tuple[int, str]]] = {case.id: [] for case in cases}

    for path in attempt_paths:
        relative = path.relative_to(evidence).as_posix()
        parts = path.relative_to(attempts_root).parts
        require(len(parts) == 2, f"unreviewed attempt artifact path: {relative}")
        case = case_by_slug.get(parts[0])
        require(case is not None, f"attempt belongs to an unknown case: {relative}")
        match = re.fullmatch(r"attempt-(\d{4})\.json", parts[1])
        require(match is not None, f"unreviewed attempt artifact: {relative}")
        attempt_index = int(match.group(1))
        raw_document = path.read_bytes()
        attempt = validate_signature(
            parse_json(raw_document.decode("utf-8"), relative), relative
        )
        status = attempt.get("status")
        require(
            status in ("running", "failed", "timeout", "success"),
            f"attempt status is invalid: {relative}",
        )
        expected_keys = (
            common_keys
            if status == "running"
            else completed_keys
            | ({"error"} if status in ("failed", "timeout") else set())
        )
        require(set(attempt) == expected_keys, f"attempt schema drift: {relative}")
        require(
            attempt.get("schema_version") == 1
            and attempt.get("attempt_index") == attempt_index
            and attempt.get("case_id") == case.id
            and attempt.get("adapter") == run_contract["adapter"]
            and attempt.get("query_sha256") == digest(case.query.encode("utf-8"))
            and attempt.get("command") == expected_command
            and attempt.get("timeout_seconds") == run_contract["timeout_seconds"],
            f"attempt contract mismatch: {relative}",
        )
        require_string(attempt.get("started_at"), f"attempt started_at: {relative}")
        isolation = attempt.get("isolation")
        require(
            isinstance(isolation, dict)
            and set(isolation)
            == {
                "workspace",
                "home",
                "claude_config",
                "xdg_config",
                "xdg_cache",
                "xdg_data",
                "xdg_state",
                "tmp",
            }
            and all(
                isinstance(value, str) and Path(value).is_absolute()
                for value in isolation.values()
            ),
            f"attempt isolation mismatch: {relative}",
        )
        inventory = {
            "attempt_relpath": relative,
            "attempt_sha256": digest(raw_document),
            "attempt_index": attempt_index,
            "case_id": case.id,
            "status": status,
        }
        referenced_files.add(relative)
        if status != "running":
            require_string(
                attempt.get("finished_at"), f"attempt finished_at: {relative}"
            )
            require(
                attempt.get("returncode") is None
                or type(attempt.get("returncode")) is int,
                f"attempt returncode is malformed: {relative}",
            )
            require(
                type(attempt.get("timed_out")) is bool,
                f"attempt timeout flag is malformed: {relative}",
            )
            if status == "success":
                require(
                    attempt["returncode"] == 0 and attempt["timed_out"] is False,
                    f"successful attempt exit state is malformed: {relative}",
                )
            else:
                require_string(attempt.get("error"), f"attempt error: {relative}")
                require(
                    attempt["timed_out"] is (status == "timeout"),
                    f"attempt timeout classification is malformed: {relative}",
                )
            for stream_name in ("stdout", "stderr"):
                stream_relative = attempt[f"{stream_name}_relpath"]
                stream_path = safe_file(
                    evidence, stream_relative, f"attempt {stream_name}"
                )
                stream_bytes = stream_path.read_bytes()
                require(
                    digest(stream_bytes) == attempt[f"{stream_name}_sha256"],
                    f"attempt {stream_name} sha256 mismatch: {relative}",
                )
                referenced_files.add(stream_relative)
                inventory[f"{stream_name}_relpath"] = stream_relative
                inventory[f"{stream_name}_sha256"] = digest(stream_bytes)
        indexed.append((case_order[case.id], attempt_index, inventory))
        statuses_by_case[case.id].append((attempt_index, status))

    require(
        actual_files == referenced_files,
        "routing attempt inventory has unreviewed files",
    )
    for case in cases:
        statuses = sorted(statuses_by_case[case.id])
        require(statuses, f"routing case has no attempts: {case.id}")
        require(
            [index for index, _ in statuses] == list(range(1, len(statuses) + 1)),
            f"routing attempt sequence is incomplete: {case.id}",
        )
        require(
            statuses[-1][1] == "success",
            f"routing case has no final successful attempt: {case.id}",
        )
        require(
            all(status != "success" for _, status in statuses[:-1]),
            f"routing case continued after a successful attempt: {case.id}",
        )
    return [item for _, _, item in sorted(indexed)]


def validate_binary_identity(value: Any, require_production: bool) -> dict[str, str]:
    require(isinstance(value, dict), "binary identity must be an object")
    if not require_production:
        require(value == {"adapter": "fixture"}, "fixture binary identity drift")
        return value
    require(
        set(value) == {"path", "sha256", "version"},
        "Claude Code binary identity schema drift",
    )
    require(
        isinstance(value.get("path"), str) and Path(value["path"]).is_absolute(),
        "Claude Code binary realpath is invalid",
    )
    require(
        isinstance(value.get("sha256"), str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", value["sha256"]),
        "Claude Code binary sha256 is invalid",
    )
    match = CLAUDE_VERSION.fullmatch(str(value.get("version", "")))
    require(
        match is not None,
        "Claude Code version identity is invalid",
    )
    return value


def build_manifest(
    output: Path,
    run_contract: dict[str, Any],
    bundle: DefinitionBundle,
    cases: Sequence[RoutingCase],
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for case in cases:
        checkpoint = validate_checkpoint(
            output,
            case,
            bundle,
            run_contract["binary_identity"],
            run_contract["adapter"],
            run_contract["requested_model"],
        )
        records.append(checkpoint["record"])
    state_path = safe_file(output, "run-state.json", "run-state")
    state = validate_signature(read_json(state_path, "run-state"), "run-state")
    require(
        state["completed_case_ids"] == [case.id for case in cases],
        "run-state is not complete for the selected matrix",
    )
    complete = len(cases) == len(bundle.cases)
    attempts = validate_attempt_inventory(output, bundle, run_contract, cases)
    manifest = signed(
        {
            "schema_version": 3,
            "phase": PHASE,
            "adapter": run_contract["adapter"],
            "claim": CLAIM,
            "complete": complete,
            "candidate": run_contract["candidate"],
            "definition": bundle.evidence(),
            "binary_identity": run_contract["binary_identity"],
            "requested_model": run_contract["requested_model"],
            "configuration_relpath": "artifacts/configuration.json",
            "configuration_sha256": digest(
                (output / "artifacts/configuration.json").read_bytes()
            ),
            "isolation_relpath": "artifacts/isolation.json",
            "isolation_sha256": digest(
                (output / "artifacts/isolation.json").read_bytes()
            ),
            "run_state_relpath": "run-state.json",
            "run_state_sha256": digest(state_path.read_bytes()),
            "run_contract_sha256": digest(canonical(run_contract)),
            "replay_semantics": REPLAY_SEMANTICS,
            "counts": case_counts(cases),
            "records": records,
            "attempts": attempts,
        }
    )
    manifest_path = output / MANIFEST_NAME
    if manifest_path.exists() or manifest_path.is_symlink():
        require(
            manifest_path.is_file() and not manifest_path.is_symlink(),
            "routing manifest is unsafe",
        )
        require(
            read_json(manifest_path, "routing manifest") == manifest,
            "existing routing manifest mismatch",
        )
    else:
        write_json(manifest_path, manifest)
    return manifest


def validate_evidence(
    evidence_root: Path,
    expected_candidate: Optional[dict[str, str]] = None,
    require_production: bool = True,
) -> dict[str, Any]:
    evidence = Path(os.path.abspath(os.fspath(evidence_root)))
    require(
        evidence.is_dir() and not evidence.is_symlink(),
        "routing evidence must be a real directory",
    )
    manifest_path = safe_file(evidence, MANIFEST_NAME, "routing evidence manifest")
    manifest = validate_signature(
        read_json(manifest_path, "routing evidence manifest"),
        "routing evidence manifest",
    )
    required_manifest = {
        "schema_version",
        "phase",
        "adapter",
        "claim",
        "complete",
        "candidate",
        "definition",
        "binary_identity",
        "requested_model",
        "configuration_relpath",
        "configuration_sha256",
        "isolation_relpath",
        "isolation_sha256",
        "run_state_relpath",
        "run_state_sha256",
        "run_contract_sha256",
        "replay_semantics",
        "counts",
        "records",
        "attempts",
        "sha256",
    }
    require(
        set(manifest) == required_manifest
        and manifest.get("schema_version") == 3
        and manifest.get("phase") == PHASE
        and manifest.get("claim") == CLAIM
        and manifest.get("replay_semantics") == REPLAY_SEMANTICS,
        "routing manifest schema drift",
    )
    adapter = manifest.get("adapter")
    require(adapter in ("fixture", "claude-cli"), "routing manifest adapter is invalid")
    if require_production:
        require(
            adapter == "claude-cli",
            "public release requires Claude CLI routing evidence",
        )
        require(
            manifest.get("complete") is True,
            "public release requires complete routing evidence",
        )
        require(
            manifest.get("counts") == EXPECTED_COUNTS,
            "public release routing counts are incomplete",
        )
        require_string(manifest.get("requested_model"), "requested Claude model")
    binary_identity = validate_binary_identity(
        manifest.get("binary_identity"), require_production
    )

    candidate = manifest.get("candidate")
    require(
        isinstance(candidate, dict)
        and set(candidate)
        == {"revision", "tree_oid", "archive_sha256", "archive_relpath", "repository"},
        "routing candidate identity schema drift",
    )
    for key in ("revision", "tree_oid", "archive_sha256", "archive_relpath"):
        require_string(candidate.get(key), f"routing candidate {key}")
    if expected_candidate is not None:
        for key in ("revision", "tree_oid", "archive_sha256", "repository"):
            require(
                candidate.get(key) == expected_candidate.get(key),
                f"routing evidence candidate {key} mismatch",
            )
    archive_path = safe_file(
        evidence, candidate["archive_relpath"], "retained candidate archive"
    )
    archive_bytes = archive_path.read_bytes()
    require(
        digest(archive_bytes) == candidate["archive_sha256"],
        "retained candidate archive sha256 mismatch",
    )
    with tempfile.TemporaryDirectory(prefix="validate-routing-candidate-") as temporary:
        snapshot = Path(temporary).resolve(strict=True) / "candidate"
        extract_archive(archive_bytes, snapshot)
        definition = manifest.get("definition")
        require(
            isinstance(definition, dict), "routing definition evidence is malformed"
        )
        bundle = load_definition(snapshot, definition.get("path"))
        require(
            definition == bundle.evidence(),
            "routing definition evidence differs from the retained candidate",
        )

        configuration_path = safe_file(
            evidence, manifest["configuration_relpath"], "routing configuration"
        )
        isolation_path = safe_file(
            evidence, manifest["isolation_relpath"], "routing isolation"
        )
        state_path = safe_file(
            evidence, manifest["run_state_relpath"], "routing run-state"
        )
        require(
            digest(configuration_path.read_bytes()) == manifest["configuration_sha256"],
            "routing configuration sha256 mismatch",
        )
        require(
            digest(isolation_path.read_bytes()) == manifest["isolation_sha256"],
            "routing isolation sha256 mismatch",
        )
        require(
            digest(state_path.read_bytes()) == manifest["run_state_sha256"],
            "routing run-state sha256 mismatch",
        )
        config = read_json(configuration_path, "routing configuration")
        isolation = read_json(isolation_path, "routing isolation")
        require(
            config
            == configuration(adapter, manifest.get("requested_model"), binary_identity),
            "routing configuration differs from the enforced contract",
        )
        require(
            isolation == isolation_contract(),
            "routing isolation differs from the enforced contract",
        )
        state = validate_signature(
            read_json(state_path, "routing run-state"), "routing run-state"
        )
        run_contract = state.get("run_contract")
        require(isinstance(run_contract, dict), "routing run contract is malformed")
        require(
            state.get("run_contract_sha256") == digest(canonical(run_contract)),
            "routing run contract sha256 mismatch",
        )
        require(
            manifest["run_contract_sha256"] == digest(canonical(run_contract)),
            "manifest run contract sha256 mismatch",
        )
        require(
            run_contract.get("candidate") == candidate
            and run_contract.get("definition") == bundle.evidence()
            and run_contract.get("adapter") == adapter
            and run_contract.get("binary_identity") == binary_identity
            and run_contract.get("requested_model") == manifest.get("requested_model")
            and run_contract.get("configuration") == config
            and run_contract.get("isolation") == isolation,
            "routing run contract differs from retained evidence",
        )
        selected_ids = run_contract.get("selected_case_ids")
        require(
            isinstance(selected_ids, list)
            and len(selected_ids) == len(set(selected_ids)),
            "routing selected case inventory is malformed",
        )
        case_by_id = {case.id: case for case in bundle.cases}
        require(
            all(case_id in case_by_id for case_id in selected_ids),
            "routing run selects an unknown case",
        )
        if require_production:
            require(
                selected_ids == [case.id for case in bundle.cases],
                "public release routing matrix is not complete",
            )
        selected_cases = [case_by_id[case_id] for case_id in selected_ids]
        require(
            manifest["counts"] == case_counts(selected_cases),
            "routing manifest counts mismatch",
        )
        require(
            manifest["complete"] is (len(selected_cases) == len(bundle.cases)),
            "routing manifest completeness mismatch",
        )
        require(
            state.get("completed_case_ids") == selected_ids,
            "routing run-state completion inventory mismatch",
        )
        records = manifest.get("records")
        require(
            isinstance(records, list) and len(records) == len(selected_cases),
            "routing manifest record inventory mismatch",
        )
        rebuilt_records = [
            validate_checkpoint(
                evidence,
                case,
                bundle,
                binary_identity,
                adapter,
                manifest.get("requested_model"),
            )["record"]
            for case in selected_cases
        ]
        require(
            records == rebuilt_records,
            "routing manifest is not derived from durable checkpoints",
        )
        attempts = validate_attempt_inventory(
            evidence, bundle, run_contract, selected_cases
        )
        require(
            manifest.get("attempts") == attempts,
            "routing manifest is not derived from the complete attempt inventory",
        )
        checkpoint_names = (
            {path.name for path in (evidence / "checkpoints").glob("*.json")}
            if (evidence / "checkpoints").is_dir()
            else set()
        )
        require(
            checkpoint_names
            == {f"{case_slug(case.id)}.json" for case in selected_cases},
            "routing checkpoint inventory has missing or unreviewed files",
        )
    return manifest


def run(arguments: argparse.Namespace) -> int:
    repo = lexical_absolute_path(arguments.repo)
    output = lexical_absolute_path(arguments.output)
    require(
        repo.is_dir() and not repo.is_symlink(),
        "candidate repository must be a real directory",
    )
    if resolved_path_is_within(repo, output):
        raise RoutingError("routing evidence must be outside the candidate checkout")
    prepare_private_directory(output, error_factory=RoutingError)
    definition_relative = safe_relative(
        arguments.definition, "routing definition argument"
    ).as_posix()
    candidate, archive = candidate_git_identity(repo, arguments.candidate_revision)
    if arguments.adapter == "fixture":
        require(arguments.model is None, "fixture mode does not accept --model")
        binary_identity = {"adapter": "fixture"}
        requested_model = None
    else:
        require(
            candidate["repository"] == CANONICAL_REPOSITORY_URL,
            "production candidate must use the canonical nisavid/agents repository",
        )
        requested_model = require_string(arguments.model, "production --model")
        require(
            exact_claude_model(requested_model),
            "production --model must be an exact Claude model identifier",
        )
        binary_identity = resolve_claude_identity()

    with tempfile.TemporaryDirectory(prefix="skill-routing-candidate-") as temporary:
        snapshot = Path(temporary).resolve(strict=True) / "candidate"
        extract_archive(archive, snapshot)
        bundle = load_definition(snapshot, definition_relative)
        if arguments.adapter == "fixture":
            require(
                arguments.case_limit is not None
                and 0 < arguments.case_limit < len(bundle.cases),
                "fixture mode requires a proper --case-limit subset",
            )
            cases = bundle.cases[: arguments.case_limit]
        else:
            require(
                arguments.case_limit is None,
                "Claude CLI routing evidence must run all 113 cases",
            )
            cases = bundle.cases
        config = configuration(arguments.adapter, requested_model, binary_identity)
        isolation = isolation_contract()
        run_contract = {
            "schema_version": 2,
            "candidate": candidate,
            "definition": bundle.evidence(),
            "adapter": arguments.adapter,
            "binary_identity": binary_identity,
            "requested_model": requested_model,
            "configuration": config,
            "isolation": isolation,
            "timeout_seconds": arguments.timeout_seconds,
            "selected_case_ids": [case.id for case in cases],
        }
        state = load_or_create_state(output, run_contract)
        ensure_artifact(output, candidate["archive_relpath"], archive)
        ensure_artifact(output, "artifacts/configuration.json", json_file_bytes(config))
        ensure_artifact(output, "artifacts/isolation.json", json_file_bytes(isolation))
        completed = list(state["completed_case_ids"])
        case_by_id = {case.id: case for case in cases}
        require(
            all(case_id in case_by_id for case_id in completed),
            "run-state names a case outside this run",
        )
        require(
            completed == [case.id for case in cases[: len(completed)]],
            "run-state completion order is invalid",
        )
        for case in cases:
            if case.id in completed:
                validate_checkpoint(
                    output,
                    case,
                    bundle,
                    binary_identity,
                    arguments.adapter,
                    requested_model,
                )
                continue
            checkpoint_path = output / f"checkpoints/{case_slug(case.id)}.json"
            if checkpoint_path.exists() or checkpoint_path.is_symlink():
                validate_checkpoint(
                    output,
                    case,
                    bundle,
                    binary_identity,
                    arguments.adapter,
                    requested_model,
                )
                completed.append(case.id)
                state = state_document(run_contract, completed)
                write_json(output / "run-state.json", state)
                continue
            execute_case(
                output,
                snapshot,
                bundle,
                case,
                arguments.adapter,
                binary_identity,
                requested_model,
                arguments.timeout_seconds,
            )
            completed.append(case.id)
            state = state_document(run_contract, completed)
            write_json(output / "run-state.json", state)
        build_manifest(output, run_contract, bundle, cases)
    validate_evidence(
        output, candidate, require_production=arguments.adapter == "claude-cli"
    )
    print(output / MANIFEST_NAME)
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--repo", type=Path, default=Path(__file__).resolve().parents[1]
    )
    result.add_argument("--definition", default=DEFINITION_DEFAULT)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--candidate-revision", required=True)
    result.add_argument("--adapter", choices=("fixture", "claude-cli"), required=True)
    result.add_argument("--model")
    result.add_argument("--case-limit", type=int)
    result.add_argument("--timeout-seconds", type=int, default=300)
    return result


if __name__ == "__main__":
    try:
        raise SystemExit(run(parser().parse_args()))
    except (
        RoutingError,
        OSError,
        subprocess.SubprocessError,
        UnicodeDecodeError,
    ) as error:
        print(f"Routing evaluation failed: {error}", file=sys.stderr)
        raise SystemExit(2)
