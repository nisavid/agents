#!/usr/bin/env python3
"""Validate Rolecasting's portable dual-harness contract."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:
    yaml = None


PLUGIN_RELATIVE = Path("plugins/rolecasting")
DELEGATING_SKILL = "delegating-cross-agent-work"
CHOOSING_SKILL = "choosing-agent-models"
SKILL_NAMES = {DELEGATING_SKILL, CHOOSING_SKILL}
MAX_INVOCATION_WORD_BUDGETS = {
    CHOOSING_SKILL: 386,
    DELEGATING_SKILL: 450,
}
INVOCATION_TOPOLOGY_RECEIPT = "adapter:rolecasting-invocation-topology-receipt"
INVOCATION_TOPOLOGY_REFERENCE = (
    "skills/delegating-cross-agent-work/references/invocation-topology-receipt.md"
)
EXPECTED_RECEIPT_CONTRACT = {
    "id": INVOCATION_TOPOLOGY_RECEIPT,
    "owner": DELEGATING_SKILL,
    "separate_from": "adapter:model-selection-receipt",
    "binds": [
        "dispatch-identity",
        "lifecycle",
        "isolation",
        "subdelegation-authority",
        "external-action-authority",
    ],
    "default_denies": ["subdelegation", "external-action"],
}
VERSION = re.compile(
    r"^(?P<release>\d+\.\d+\.\d+)\+(?P<harness>claude|codex)\.(?P<build>\d{14})$"
)
REFERENCE_LINK = re.compile(r"\[[^\]]+\]\((references/[^)]+\.md)\)")
PORTABILITY_PATTERNS = (
    re.compile(r"/users/", re.IGNORECASE),
    re.compile(r"\bchezmoi\b", re.IGNORECASE),
    re.compile(r"\bsystalyze\b", re.IGNORECASE),
    re.compile(r"\bapi[\s_-]*key\b", re.IGNORECASE),
    re.compile(r"\bauthorization\s*:\s*bearer\b", re.IGNORECASE),
)
BASE_FILES = {
    ".claude-plugin/plugin.json",
    ".codex-plugin/plugin.json",
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "content-lock.json",
    "evals/delivery.json",
    "topology.json",
}


class ContractError(ValueError):
    """Raised for a stable, user-actionable plugin contract failure."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def require_nonempty_string(value: object, message: str) -> str:
    require(isinstance(value, str) and bool(value), message)
    return value


def require_integer(value: object, message: str) -> int:
    require(isinstance(value, int) and not isinstance(value, bool), message)
    return value


def safe_relative_path(value: object, field: str) -> Path:
    text = require_nonempty_string(value, f"{field} must be a nonempty string")
    relative = Path(text)
    require(
        not relative.is_absolute()
        and ".." not in relative.parts
        and relative.as_posix() == text,
        f"{field} must be a normalized relative path",
    )
    return relative


def regular_directory(root: Path, relative: str | Path) -> Path:
    relative = safe_relative_path(Path(relative).as_posix(), "directory")
    current = root
    for part in relative.parts:
        current /= part
        require(
            not current.is_symlink(),
            f"directory path must not contain symlinks: {relative}",
        )
    resolved = current.resolve(strict=True)
    require(
        resolved.is_relative_to(root.resolve(strict=True)) and resolved.is_dir(),
        f"missing regular directory: {relative}",
    )
    return resolved


def reject_lexical_ancestor_symlinks(path: Path) -> Path:
    lexical_path = path if path.is_absolute() else Path.cwd() / path
    current = Path(lexical_path.anchor)
    for part in lexical_path.parts[1:]:
        if part in ("", "."):
            continue
        if part == "..":
            current = current.parent
            continue
        current /= part
        require(
            not current.is_symlink(),
            f"repository path contains a symlinked lexical ancestor: {current}",
        )
    return current


def locate_root(repo_root: Path) -> Path:
    lexical_repo = reject_lexical_ancestor_symlinks(repo_root)
    resolved_repo = lexical_repo.resolve(strict=True)
    require(resolved_repo.is_dir(), "repository root must be a directory")
    current = lexical_repo
    for part in PLUGIN_RELATIVE.parts:
        current /= part
        require(not current.is_symlink(), "plugin root path must not contain symlinks")
    resolved_root = current.resolve(strict=True)
    require(
        resolved_root.is_relative_to(resolved_repo) and resolved_root.is_dir(),
        "plugin root is invalid",
    )
    return resolved_root


def read_bytes(root: Path, relative: str | Path) -> bytes:
    relative = safe_relative_path(Path(relative).as_posix(), "file")
    current = root
    for part in relative.parts:
        current /= part
        require(
            not current.is_symlink(),
            f"file path must not contain symlinks: {relative}",
        )
    resolved = current.resolve(strict=True)
    require(
        resolved.is_relative_to(root.resolve(strict=True)) and resolved.is_file(),
        f"required regular file is missing: {relative}",
    )
    return resolved.read_bytes()


def read(root: Path, relative: str | Path) -> str:
    return read_bytes(root, relative).decode("utf-8")


def decoded_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from decoded_strings(key)
            yield from decoded_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from decoded_strings(item)


def validate_decoded_portability(value, relative: str) -> None:
    for content in decoded_strings(value):
        for pattern in PORTABILITY_PATTERNS:
            require(
                pattern.search(content) is None,
                f"portability or credential leak in {relative}",
            )


def load_json(root: Path, relative: str, field: str) -> dict:
    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, f"{field} contains duplicate key: {key}")
            value[key] = item
        return value

    def reject_constant(value: str):
        raise ContractError(f"{field} contains non-finite JSON value: {value}")

    try:
        document = json.loads(
            read(root, relative),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as error:
        raise ContractError(f"invalid JSON in {relative}: {error.msg}") from error
    require(isinstance(document, dict), f"{field} must contain an object")
    validate_decoded_portability(document, relative)
    return document


def load_yaml_mapping(content: str, field: str) -> dict:
    require(yaml is not None, "PyYAML is required to validate discovery metadata")

    class UniqueKeyLoader(yaml.SafeLoader):
        pass

    def construct_unique_mapping(loader, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as error:
                raise ContractError(f"{field} keys must be scalar values") from error
            require(not duplicate, f"{field} contains duplicate key: {key}")
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    UniqueKeyLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_unique_mapping,
    )
    try:
        documents = list(yaml.load_all(content, Loader=UniqueKeyLoader))
    except yaml.YAMLError as error:
        raise ContractError(f"{field} contains invalid YAML: {error}") from error
    require(len(documents) == 1, f"{field} must contain exactly one YAML document")
    document = documents[0]
    require(isinstance(document, dict), f"{field} must contain a mapping")
    validate_decoded_portability(document, field)
    return document


def load_skill_frontmatter(content: str, skill: str) -> dict:
    match = re.match(r"\A---\r?\n(?P<yaml>.*?)\r?\n---(?:\r?\n|\Z)", content, re.S)
    require(match is not None, f"{skill} must have opening and closing frontmatter")
    require(content[match.end() :].strip(), f"{skill} must have a skill body")
    return load_yaml_mapping(match.group("yaml"), f"{skill} frontmatter")


def validate_topology(root: Path) -> dict:
    topology = load_json(root, "topology.json", "Rolecasting topology")
    require(
        "receipt_contract" in topology,
        "invocation topology receipt metadata drift",
    )
    require(
        set(topology) == {"schema_version", "receipt_contract", "skills"},
        "topology keys drift",
    )
    require_integer(
        topology["schema_version"], "topology schema_version must be an integer"
    )
    require(topology["schema_version"] == 2, "topology schema_version drift")
    require(
        topology["receipt_contract"] == EXPECTED_RECEIPT_CONTRACT,
        "invocation topology receipt metadata drift",
    )
    skills = topology["skills"]
    require(isinstance(skills, dict), "topology skills must be an object")
    require(set(skills) == SKILL_NAMES, "topology skill inventory drift")

    owner_to_skill = {}
    edges = []
    for skill, node in skills.items():
        require(isinstance(node, dict), f"topology node must be an object: {skill}")
        require(set(node) == {"owns", "may_call"}, f"topology node keys drift: {skill}")
        owners = node["owns"]
        require(
            isinstance(owners, list)
            and bool(owners)
            and all(isinstance(owner, str) and bool(owner) for owner in owners),
            f"topology owners must be nonempty strings: {skill}",
        )
        require(len(owners) == len(set(owners)), f"duplicate topology owner: {skill}")
        for owner in owners:
            require(owner not in owner_to_skill, f"topology owner is shared: {owner}")
            owner_to_skill[owner] = skill

        calls = node["may_call"]
        require(isinstance(calls, list), f"topology may_call must be a list: {skill}")
        observed_targets = set()
        for position, call in enumerate(calls, start=1):
            require(
                isinstance(call, dict),
                f"topology call {position} must be an object: {skill}",
            )
            require(
                set(call) == {"skill", "when"},
                f"topology call {position} keys drift: {skill}",
            )
            target = require_nonempty_string(
                call["skill"], f"topology call target must be a string: {skill}"
            )
            condition = require_nonempty_string(
                call["when"], f"topology call condition must be a string: {skill}"
            )
            require(target in skills, f"topology call target is unknown: {target}")
            require(target != skill, f"topology self-call is forbidden: {skill}")
            require(target not in observed_targets, f"duplicate topology call: {skill}")
            observed_targets.add(target)
            edges.append((skill, target, condition))

    delegating_calls = skills[DELEGATING_SKILL]["may_call"]
    require(
        len(delegating_calls) == 1
        and delegating_calls[0]["skill"] == CHOOSING_SKILL
        and delegating_calls[0]["when"] == "model-or-effort-unresolved",
        "delegation may call model selection only when model or effort is unresolved",
    )
    require(
        skills[CHOOSING_SKILL]["may_call"] == [],
        "model selection must not define a reverse call edge",
    )
    require(
        "invocation-topology-receipt" in skills[DELEGATING_SKILL]["owns"]
        and "invocation-topology-receipt" not in skills[CHOOSING_SKILL]["owns"],
        "invocation topology receipt ownership drift",
    )
    require(
        "invocation-topology-receipt"
        in skills[topology["receipt_contract"]["owner"]]["owns"],
        "invocation topology receipt metadata owner drift",
    )

    adjacency = {skill: [] for skill in skills}
    for caller, target, _condition in edges:
        adjacency[caller].append(target)
    visiting = set()
    visited = set()

    def visit(skill: str) -> None:
        require(skill not in visiting, "topology call graph must be acyclic")
        if skill in visited:
            return
        visiting.add(skill)
        for target in adjacency[skill]:
            visit(target)
        visiting.remove(skill)
        visited.add(skill)

    for skill in skills:
        visit(skill)
    return topology


def validate_skills(root: Path, topology: dict) -> tuple[dict, dict, set[str]]:
    bodies = {}
    prompts = {}
    semantic_files = set()
    for skill in topology["skills"]:
        skill_path = f"skills/{skill}/SKILL.md"
        content = read(root, skill_path)
        bodies[skill] = content
        semantic_files.add(skill_path)
        frontmatter = load_skill_frontmatter(content, skill)
        require(
            set(frontmatter) == {"name", "description"},
            f"{skill} frontmatter schema drift",
        )
        require(frontmatter["name"] == skill, f"{skill} frontmatter name drift")
        description = require_nonempty_string(
            frontmatter["description"], f"{skill} description must be a string"
        )
        require(
            description.startswith("Use when "), f"{skill} description trigger drift"
        )
        require(
            len(content.split()) <= MAX_INVOCATION_WORD_BUDGETS[skill],
            f"progressive-disclosure invocation budget exceeded: {skill}",
        )
        require(
            "$rolecasting:" not in content,
            f"semantic body contains adapter-qualified syntax: {skill}",
        )

        adapter_path = f"skills/{skill}/agents/openai.yaml"
        adapter = load_yaml_mapping(
            read(root, adapter_path), f"{skill} skill interface"
        )
        semantic_files.add(adapter_path)
        require(set(adapter) == {"interface"}, f"{skill} skill interface keys drift")
        interface = adapter["interface"]
        require(isinstance(interface, dict), f"{skill} interface must be a mapping")
        require(
            set(interface) == {"display_name", "short_description", "default_prompt"},
            f"{skill} interface schema drift",
        )
        for field in ("display_name", "short_description", "default_prompt"):
            require_nonempty_string(
                interface[field], f"{skill} interface {field} must be a string"
            )
        prompt = interface["default_prompt"]
        tokens = re.findall(r"\$rolecasting:[a-z0-9-]+", prompt)
        require(
            tokens == [f"$rolecasting:{skill}"],
            f"{skill} default prompt namespace drift",
        )
        prompts[skill] = prompt

        reference_paths = set(REFERENCE_LINK.findall(content))
        require(reference_paths, f"{skill} must defer detailed guidance to a reference")
        for relative_reference in reference_paths:
            reference = safe_relative_path(relative_reference, f"{skill} reference")
            plugin_relative = (Path("skills") / skill / reference).as_posix()
            read(root, plugin_relative)
            semantic_files.add(plugin_relative)

        for call in topology["skills"][skill]["may_call"]:
            target = call["skill"]
            require(
                f"](../{target}/SKILL.md)" in content,
                f"topology call is not projected in skill body: {skill} -> {target}",
            )

    delegating = bodies[DELEGATING_SKILL]
    foreign_peers = read(
        root,
        "skills/delegating-cross-agent-work/references/foreign-harness-peers.md",
    )
    receipt = read(root, INVOCATION_TOPOLOGY_REFERENCE)
    receipt_contract = topology["receipt_contract"]
    require(
        "[invocation-topology-receipt.md](references/invocation-topology-receipt.md)"
        in delegating
        and INVOCATION_TOPOLOGY_RECEIPT in delegating,
        "invocation topology receipt handoff drift",
    )
    for term in (
        receipt_contract["id"],
        f"separate from `{receipt_contract['separate_from']}`",
        "exactly one unique dispatch entry",
        "closed-world dispatch set",
        "candidate identity",
        "review-input identity",
        "requirements identity",
        "exact executor and harness",
        "return shape, verification, and stop conditions",
        "distinct session and context isolation",
        "read-only authority",
        "default-denied subdelegation and external-action authority",
        "explicit user authority",
        "new valid plan",
    ):
        require(
            term in receipt, f"invocation topology receipt contract missing: {term}"
        )
    binding_terms = {
        "dispatch-identity": "exactly one unique dispatch entry",
        "lifecycle": "execution identity and lifecycle",
        "isolation": "distinct session and context isolation",
        "subdelegation-authority": "default-denied subdelegation",
        "external-action-authority": "external-action authority",
    }
    for binding in receipt_contract["binds"]:
        require(
            binding_terms[binding] in receipt,
            f"invocation topology receipt prose binding drift: {binding}",
        )
    require(
        receipt_contract["default_denies"] == ["subdelegation", "external-action"]
        and "default-denied subdelegation and external-action authority" in receipt,
        "invocation topology receipt prose default-deny drift",
    )
    require(
        "Broaden authority or permissions only with explicit operator or repository "
        "authorization." in " ".join(foreign_peers.split()),
        "foreign peer authority-escalation boundary drift",
    )

    return bodies, prompts, semantic_files


def validate_manifests(root: Path, topology: dict, prompts: dict) -> None:
    claude = load_json(root, ".claude-plugin/plugin.json", "Claude manifest")
    codex = load_json(root, ".codex-plugin/plugin.json", "Codex manifest")
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
    require(set(claude) == common_keys | {"displayName"}, "Claude manifest keys drift")
    require(
        set(codex) == common_keys | {"skills", "interface"},
        "Codex manifest keys drift",
    )
    for label, manifest in (("Claude", claude), ("Codex", codex)):
        for field in (
            "name",
            "version",
            "description",
            "homepage",
            "repository",
            "license",
        ):
            require_nonempty_string(
                manifest[field], f"{label} manifest {field} must be a string"
            )
        require(manifest["name"] == "rolecasting", f"{label} manifest name drift")
        require(manifest["license"] == "MIT", f"{label} manifest license drift")
        require(
            manifest["repository"] == "https://github.com/nisavid/agents",
            f"{label} manifest repository drift",
        )
        keywords = manifest["keywords"]
        require(
            isinstance(keywords, list)
            and bool(keywords)
            and all(isinstance(item, str) and bool(item) for item in keywords),
            f"{label} manifest keywords must be nonempty strings",
        )
        author = manifest["author"]
        require(
            isinstance(author, dict) and set(author) == {"name", "url"},
            f"{label} manifest author schema drift",
        )
        for field in ("name", "url"):
            require_nonempty_string(
                author[field], f"{label} manifest author {field} must be a string"
            )

    claude_version = VERSION.fullmatch(claude["version"])
    codex_version = VERSION.fullmatch(codex["version"])
    require(
        claude_version is not None and codex_version is not None,
        "manifest version is invalid",
    )
    require(
        claude_version["harness"] == "claude" and codex_version["harness"] == "codex",
        "manifest harness metadata drift",
    )
    require(
        claude_version["release"] == codex_version["release"]
        and claude_version["build"] == codex_version["build"],
        "manifest versions are not paired",
    )
    require_nonempty_string(
        claude["displayName"], "Claude displayName must be a string"
    )
    require(claude["displayName"] == "Rolecasting", "Claude displayName drift")
    require(codex["skills"] == "./skills/", "Codex skills path drift")

    interface = codex["interface"]
    require(isinstance(interface, dict), "Codex interface must be an object")
    require(
        set(interface)
        == {
            "displayName",
            "shortDescription",
            "longDescription",
            "developerName",
            "category",
            "capabilities",
            "websiteURL",
            "defaultPrompt",
        },
        "Codex interface keys drift",
    )
    for field in (
        "displayName",
        "shortDescription",
        "longDescription",
        "developerName",
        "category",
        "websiteURL",
    ):
        require_nonempty_string(
            interface[field], f"Codex interface {field} must be a string"
        )
    require(
        interface["displayName"] == "Rolecasting"
        and interface["websiteURL"]
        == "https://github.com/nisavid/agents/tree/main/plugins/rolecasting",
        "Codex interface metadata drift",
    )
    require(
        interface["capabilities"] == ["Orchestration"],
        "Codex interface capabilities drift",
    )
    default_prompts = interface["defaultPrompt"]
    require(
        isinstance(default_prompts, list)
        and all(isinstance(prompt, str) for prompt in default_prompts),
        "Codex default prompts must be strings",
    )
    require(
        default_prompts == [prompts[skill] for skill in topology["skills"]],
        "Codex default prompts drift",
    )
    require(
        claude["description"]
        == codex["description"].replace("Rolecasting for Codex:", "Rolecasting:", 1),
        "manifest descriptions are not paired",
    )
    changelog = read(root, "CHANGELOG.md")
    require(
        re.search(rf"^## {re.escape(codex_version['release'])}$", changelog, re.M)
        is not None,
        "changelog release drift",
    )


def validate_delivery(root: Path) -> dict:
    delivery = load_json(root, "evals/delivery.json", "eval delivery contract")
    require(
        set(delivery) == {"schema_version", "executor", "grader"},
        "eval delivery contract keys drift",
    )
    require_integer(
        delivery["schema_version"], "eval delivery schema_version must be an integer"
    )
    require(delivery["schema_version"] == 1, "eval delivery schema_version drift")
    executor = delivery["executor"]
    grader = delivery["grader"]
    require(isinstance(executor, dict), "eval executor contract must be an object")
    require(isinstance(grader, dict), "eval grader contract must be an object")
    require(set(executor) == {"inputs", "tools"}, "eval executor contract keys drift")
    require(
        set(grader) == {"distinct_from_executor", "available_after", "inputs"},
        "eval grader contract keys drift",
    )
    require(
        isinstance(executor["inputs"], list)
        and all(isinstance(item, str) for item in executor["inputs"]),
        "eval executor inputs must be strings",
    )
    require(
        executor["inputs"] == ["prompt", "fixture", "candidate_bundle"],
        "eval executor inputs must withhold grader expectations",
    )
    require(executor["tools"] == "denied", "eval executor tools must be denied")
    require(
        isinstance(grader["distinct_from_executor"], bool)
        and grader["distinct_from_executor"],
        "eval grader must be distinct from executor",
    )
    require(
        grader["available_after"] == "executor-complete",
        "eval grader inputs must remain unavailable until execution completes",
    )
    require(
        isinstance(grader["inputs"], list)
        and all(isinstance(item, str) for item in grader["inputs"]),
        "eval grader inputs must be strings",
    )
    require(
        grader["inputs"]
        == [
            "prompt",
            "fixture",
            "candidate_bundle",
            "response",
            "expected_output",
            "expectations",
        ],
        "eval grader inputs drift",
    )
    return delivery


def build_executor_payload(
    *,
    prompt: str,
    fixture: str,
    candidate_bundle: dict[str, str],
) -> dict:
    """Build the isolated input available to the executor."""
    return {
        "prompt": prompt,
        "fixture": fixture,
        "candidate_bundle": candidate_bundle,
    }


def build_grader_payload(
    *,
    executor_payload: dict,
    response: str,
    expected_output: str,
    expectations: list[dict],
) -> dict:
    """Build grading input only after the executor has returned a response."""
    return {
        **executor_payload,
        "response": response,
        "expected_output": expected_output,
        "expectations": expectations,
    }


def validate_evals(
    root: Path,
    topology: dict,
    bodies: dict,
    delivery: dict,
    semantic_files: set[str],
) -> set[str]:
    names = set()
    for skill in topology["skills"]:
        eval_path = f"skills/{skill}/evals/evals.json"
        document = load_json(root, eval_path, f"{skill} evals")
        semantic_files.add(eval_path)
        require(
            set(document) == {"skill_name", "evals"},
            f"{skill} eval manifest keys drift",
        )
        require(
            isinstance(document["skill_name"], str) and document["skill_name"] == skill,
            f"{skill} eval skill name drift",
        )
        evals = document["evals"]
        require(isinstance(evals, list), f"{skill} evals must be a list")
        require(len(evals) >= 5, f"{skill} requires at least five boundary evals")

        direct_references = sorted(
            path
            for path in semantic_files
            if path.startswith(f"skills/{skill}/references/")
        )
        candidate_bundle = {f"skills/{skill}/SKILL.md": bodies[skill]}
        candidate_bundle.update({path: read(root, path) for path in direct_references})
        referenced_fixtures = set()
        observed_ids = []
        for position, item in enumerate(evals, start=1):
            require(
                isinstance(item, dict),
                f"{skill} eval item {position} must be an object",
            )
            require(
                set(item)
                == {
                    "id",
                    "name",
                    "prompt",
                    "fixture_paths",
                    "expected_output",
                    "expectations",
                },
                f"{skill} eval item {position} shape drift",
            )
            eval_id = require_integer(
                item["id"], f"{skill} eval item {position} id must be an integer"
            )
            name = require_nonempty_string(
                item["name"],
                f"{skill} eval item {position} name must be a nonempty string",
            )
            prompt = require_nonempty_string(
                item["prompt"],
                f"{skill} eval item {position} prompt must be a nonempty string",
            )
            paths = item["fixture_paths"]
            require(
                isinstance(paths, list)
                and len(paths) == 1
                and all(isinstance(path, str) and bool(path) for path in paths),
                f"{skill} eval item {position} fixture_paths must contain one string",
            )
            fixture_relative = safe_relative_path(
                paths[0], f"{skill} eval item {position} fixture"
            )
            require(
                len(fixture_relative.parts) == 3
                and fixture_relative.parts[:2] == ("evals", "fixtures")
                and fixture_relative.suffix == ".md",
                f"{skill} eval item {position} fixture must be a direct Markdown "
                "fixture",
            )
            fixture_path = (Path("skills") / skill / fixture_relative).as_posix()
            fixture = read(root, fixture_path)
            semantic_files.add(fixture_path)
            referenced_fixtures.add(fixture_relative.name)
            expected_output = require_nonempty_string(
                item["expected_output"],
                f"{skill} eval item {position} expected_output must be a nonempty "
                "string",
            )
            expectations = item["expectations"]
            require(
                isinstance(expectations, list) and len(expectations) == 3,
                f"{skill} eval item {position} expectations must contain three objects",
            )
            expectation_ids = set()
            for expectation_position, expectation in enumerate(expectations, start=1):
                require(
                    isinstance(expectation, dict),
                    f"{skill} eval item {position} expectation "
                    f"{expectation_position} must be an object",
                )
                require(
                    set(expectation) == {"id", "text", "severity"},
                    f"{skill} eval item {position} expectation "
                    f"{expectation_position} shape drift",
                )
                expectation_id = require_nonempty_string(
                    expectation["id"],
                    f"{skill} eval item {position} expectation "
                    f"{expectation_position} id must be a nonempty string",
                )
                require_nonempty_string(
                    expectation["text"],
                    f"{skill} eval item {position} expectation "
                    f"{expectation_position} text must be a nonempty string",
                )
                require(
                    isinstance(expectation["severity"], str)
                    and expectation["severity"] in {"quality", "safety"},
                    f"{skill} eval item {position} expectation "
                    f"{expectation_position} severity drift",
                )
                require(
                    expectation_id not in expectation_ids,
                    f"{skill} eval expectations contain duplicate ids: {name}",
                )
                expectation_ids.add(expectation_id)

            require(name not in names, f"duplicate Rolecasting eval name: {name}")
            names.add(name)
            observed_ids.append(eval_id)
            for marker in ("expected_output", "expectations", "pass if"):
                require(
                    marker not in fixture.lower(),
                    f"grader answer leaked into fixture: {fixture_relative.name}",
                )

            executor_payload = build_executor_payload(
                prompt=prompt,
                fixture=fixture,
                candidate_bundle=candidate_bundle,
            )
            grader_payload = build_grader_payload(
                executor_payload=executor_payload,
                response="candidate response",
                expected_output=expected_output,
                expectations=expectations,
            )
            require(
                list(executor_payload) == delivery["executor"]["inputs"],
                f"executor payload isolation drift: {name}",
            )
            require(
                list(grader_payload) == delivery["grader"]["inputs"],
                f"grader payload isolation drift: {name}",
            )
            require(
                "expected_output" not in executor_payload
                and "expectations" not in executor_payload
                and executor_payload is not grader_payload,
                f"grader answers exposed to executor: {name}",
            )

        require(
            observed_ids == list(range(1, len(evals) + 1)),
            f"{skill} eval ids drift",
        )
        fixture_dir = regular_directory(root, f"skills/{skill}/evals/fixtures")
        actual_fixtures = {
            path.name for path in fixture_dir.iterdir() if path.is_file()
        }
        require(actual_fixtures == referenced_fixtures, f"{skill} fixture drift")
    require(len(names) >= 10, "Rolecasting requires at least ten evals")
    return semantic_files


def validate_inventory(
    root: Path,
    topology: dict,
    semantic_files: set[str],
    *,
    allow_missing_content_lock: bool = False,
) -> None:
    expected_files = BASE_FILES | semantic_files
    expected_files |= {
        f"skills/{skill}/agents/openai.yaml" for skill in topology["skills"]
    }
    actual_files = set()
    actual_directories = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        require(
            not path.is_symlink(), f"plugin inventory contains a symlink: {relative}"
        )
        if path.is_file():
            actual_files.add(relative)
        else:
            require(
                path.is_dir(), f"plugin inventory contains a special entry: {relative}"
            )
            actual_directories.add(relative)

    if allow_missing_content_lock:
        require(
            actual_files in (expected_files, expected_files - {"content-lock.json"}),
            "component inventory drift",
        )
    else:
        require(actual_files == expected_files, "component inventory drift")

    expected_directories = set()
    for relative in expected_files:
        parent = Path(relative).parent
        while parent != Path("."):
            expected_directories.add(parent.as_posix())
            parent = parent.parent
    require(actual_directories == expected_directories, "component directory drift")
    require(
        not (root / "agents").exists(), "Rolecasting must not define persona agents"
    )


def validate_portability(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        content = path.read_text(encoding="utf-8")
        for pattern in PORTABILITY_PATTERNS:
            require(
                pattern.search(content) is None,
                f"portability or credential leak in {path.relative_to(root)}",
            )
    combined = "\n".join(
        read(root, f"skills/{skill}/SKILL.md").lower() for skill in SKILL_NAMES
    )
    for forbidden_claim in (
        "rolecasting owns git",
        "rolecasting owns pull-request",
        "rolecasting owns pull request",
    ):
        require(forbidden_claim not in combined, "Git or pull-request ownership leak")


def content_lock_document(root: Path, semantic_files: set[str]) -> dict:
    return {
        "schema_version": 1,
        "algorithm": "sha256",
        "files": {
            relative: hashlib.sha256(read_bytes(root, relative)).hexdigest()
            for relative in sorted(semantic_files)
        },
    }


def validate_content_lock(root: Path, semantic_files: set[str]) -> None:
    lock = load_json(root, "content-lock.json", "semantic content lock")
    require(
        set(lock) == {"schema_version", "algorithm", "files"},
        "semantic content lock keys drift",
    )
    require_integer(
        lock["schema_version"], "content lock schema_version must be an integer"
    )
    require(lock["schema_version"] == 1, "content lock schema_version drift")
    require(
        isinstance(lock["algorithm"], str), "content lock algorithm must be a string"
    )
    require(lock["algorithm"] == "sha256", "content lock algorithm drift")
    files = lock["files"]
    require(
        isinstance(files, dict) and bool(files), "content lock files must be an object"
    )
    for relative, digest in files.items():
        safe_relative_path(relative, "content lock path")
        require(
            isinstance(digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
            f"content lock digest is invalid: {relative}",
        )
    require(set(files) == semantic_files, "semantic content lock inventory drift")
    expected = content_lock_document(root, semantic_files)
    require(files == expected["files"], "semantic content lock mismatch")


def inspect_contract(root: Path) -> tuple[dict, set[str]]:
    topology = validate_topology(root)
    bodies, prompts, semantic_files = validate_skills(root, topology)
    validate_manifests(root, topology, prompts)
    delivery = validate_delivery(root)
    semantic_files |= {
        ".claude-plugin/plugin.json",
        ".codex-plugin/plugin.json",
        "README.md",
        "topology.json",
        "evals/delivery.json",
    }
    semantic_files = validate_evals(root, topology, bodies, delivery, semantic_files)
    return topology, semantic_files


def usage() -> None:
    print(
        "usage: validate_rolecasting.py [--write-content-lock] [repo-root]",
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
    try:
        repo_root = Path(arguments[0]) if arguments else Path.cwd()
        root = locate_root(repo_root)
        topology, semantic_files = inspect_contract(root)
        validate_inventory(
            root,
            topology,
            semantic_files,
            allow_missing_content_lock=write_lock,
        )
        validate_portability(root)
        if write_lock:
            lock_path = root / "content-lock.json"
            lock_path.write_text(
                json.dumps(content_lock_document(root, semantic_files), indent=2)
                + "\n",
                encoding="utf-8",
            )
            validate_inventory(root, topology, semantic_files)
        validate_content_lock(root, semantic_files)
    except (ContractError, OSError, UnicodeDecodeError) as error:
        print(f"Rolecasting contract validation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    if write_lock:
        print("Rolecasting semantic content lock updated")
    print("Rolecasting contract validation passed")


if __name__ == "__main__":
    main()
