#!/usr/bin/env python3
"""Validate Tidesmith's Agent Plugins package and Claude projection."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIRECTORY))

from agent_plugins_standard import (  # noqa: E402
    AgentPluginContractError,
    discover_direct_skills,
    load_agent_plugin_manifest,
    validate_skill_resource_links,
)

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

PLUGIN_RELATIVE = Path("plugins/tidesmith")
PLUGIN_NAME = "tidesmith"
DISPLAY_NAME = "Tidesmith"
RELEASE_VERSION = "1.0.0"
TOPOLOGY_SCHEMA_VERSION = 1
DESCRIPTION_PREFIX = "Tidesmith: "
CODEX_CAPABILITIES = ["Writing"]
HOMEPAGE = "https://github.com/nisavid/agents/tree/main/plugins/tidesmith"
REPOSITORY = "https://github.com/nisavid/agents"
MAX_INVOCATION_WORDS = 1200
REGISTRY_START = "<!-- BEGIN GENERATED SKILL REGISTRY -->"
REGISTRY_END = "<!-- END GENERATED SKILL REGISTRY -->"
EMPTY_REGISTRY_LINE = (
    "No public skill is published yet. The planned initial roster is the generic "
    "human-facing writing skill and the adversarial draft-pass skill."
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
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "content-lock.json",
    "evals/delivery.json",
    "plugin.json",
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


def load_json(root: Path, relative: str, field: str) -> dict:
    try:
        value = json.loads(read(root, relative))
    except json.JSONDecodeError as error:
        raise ContractError(f"{field} is not valid JSON") from error
    require(isinstance(value, dict), f"{field} must be an object")
    return value


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


def validate_decoded_portability(value, field: str) -> None:
    for text in decoded_strings(value):
        for pattern in PORTABILITY_PATTERNS:
            require(
                pattern.search(text) is None,
                f"portability or credential leak in {field}",
            )


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
    match = re.match(r"\A---\r?\n(?P<yaml>.*?)\r?\n---(?:\r?\n|\Z)", content, re.DOTALL)
    require(match is not None, f"{skill} must have opening and closing frontmatter")
    require(content[match.end() :].strip(), f"{skill} must have a skill body")
    return load_yaml_mapping(match.group("yaml"), f"{skill} frontmatter")


def validate_topology(root: Path) -> dict:
    topology = load_json(root, "topology.json", "Tidesmith topology")
    require(set(topology) == {"schema_version", "skills"}, "topology keys drift")
    require_integer(
        topology["schema_version"], "topology schema_version must be an integer"
    )
    require(
        topology["schema_version"] == TOPOLOGY_SCHEMA_VERSION,
        "topology schema_version drift",
    )
    skills = topology["skills"]
    require(isinstance(skills, dict), "topology skills must be an object")
    owner_to_skill: dict[str, str] = {}
    for skill, node in skills.items():
        require(
            re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", skill) is not None,
            f"topology skill name is invalid: {skill}",
        )
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
        observed_targets: set[str] = set()
        for position, call in enumerate(calls, start=1):
            require(
                isinstance(call, dict) and set(call) == {"skill", "when"},
                f"topology call {position} keys drift: {skill}",
            )
            target = require_nonempty_string(
                call["skill"], f"topology call target must be a string: {skill}"
            )
            require_nonempty_string(
                call["when"], f"topology call condition must be a string: {skill}"
            )
            require(target in skills, f"topology call target is unknown: {target}")
            require(target != skill, f"topology self-call is forbidden: {skill}")
            require(target not in observed_targets, f"duplicate topology call: {skill}")
            observed_targets.add(target)
    return topology


def render_skill_registry(topology: dict) -> str:
    skills = topology["skills"]
    lines = [REGISTRY_START]
    if not skills:
        lines.append(EMPTY_REGISTRY_LINE)
    else:
        lines.append("| Skill | Owns | Calls |")
        lines.append("| --- | --- | --- |")
        for skill in sorted(skills):
            node = skills[skill]
            calls = ", ".join(call["skill"] for call in node["may_call"]) or "-"
            lines.append(
                f"| `{skill}` | {', '.join(node['owns'])} | {calls} |"
            )
    lines.append(REGISTRY_END)
    return "\n".join(lines)


def validate_readme_projection(root: Path, topology: dict) -> None:
    readme = read(root, "README.md")
    require("## Public skills" in readme, "README projection sections drift")
    require(
        readme.count(REGISTRY_START) == 1 and readme.count(REGISTRY_END) == 1,
        "README skill registry markers drift",
    )
    start = readme.index(REGISTRY_START)
    end = readme.index(REGISTRY_END) + len(REGISTRY_END)
    require(start < end, "README skill registry markers drift")
    require(
        readme[start:end] == render_skill_registry(topology),
        "README skill registry drift",
    )


def materialize_readme_projection(root: Path, topology: dict) -> None:
    readme_path = root / "README.md"
    readme = read(root, "README.md")
    require(
        readme.count(REGISTRY_START) == 1 and readme.count(REGISTRY_END) == 1,
        "README skill registry markers drift",
    )
    start = readme.index(REGISTRY_START)
    end = readme.index(REGISTRY_END) + len(REGISTRY_END)
    require(start < end, "README skill registry markers drift")
    readme_path.write_text(
        readme[:start] + render_skill_registry(topology) + readme[end:],
        encoding="utf-8",
    )


def validate_skills(root: Path, topology: dict) -> tuple[dict, set[str]]:
    skills_root = root / "skills"
    if topology["skills"]:
        require(
            skills_root.is_dir() and not skills_root.is_symlink(),
            "skills directory is missing",
        )
        discovered = discover_direct_skills(root)
        require(
            discovered == tuple(sorted(topology["skills"])),
            "Agent Plugins direct-child skill inventory drift",
        )
        validate_skill_resource_links(root, discovered)
    else:
        require(
            not skills_root.exists(),
            "skills directory must not exist while the topology publishes no skill",
        )
    prompts: dict[str, str] = {}
    semantic_files: set[str] = set()
    for skill in topology["skills"]:
        skill_path = f"skills/{skill}/SKILL.md"
        content = read(root, skill_path)
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
            len(content.split()) <= MAX_INVOCATION_WORDS,
            f"progressive-disclosure invocation budget exceeded: {skill}",
        )
        require(
            f"${PLUGIN_NAME}:" not in content,
            f"semantic body contains adapter-qualified syntax: {skill}",
        )
        adapter_path = f"skills/{skill}/agents/openai.yaml"
        adapter = load_yaml_mapping(read(root, adapter_path), f"{skill} skill interface")
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
        tokens = re.findall(rf"\${PLUGIN_NAME}:[a-z0-9-]+", prompt)
        require(
            tokens == [f"${PLUGIN_NAME}:{skill}"],
            f"{skill} default prompt namespace drift",
        )
        prompts[skill] = prompt
        for relative_reference in set(REFERENCE_LINK.findall(content)):
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
    return prompts, semantic_files


def validate_manifests(root: Path, topology: dict, prompts: dict) -> None:
    canonical = load_agent_plugin_manifest(root)
    claude = load_json(root, ".claude-plugin/plugin.json", "Claude manifest")
    identity_fields = {
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
    }
    require(
        set(canonical) == identity_fields | {"$schema", "extensions"},
        "canonical Agent Plugin manifest keys drift",
    )
    require(
        set(claude) == identity_fields | {"displayName"}, "Claude manifest keys drift"
    )
    for field in ("name", "version", "description", "homepage", "repository", "license"):
        require_nonempty_string(
            canonical[field], f"canonical manifest {field} must be a string"
        )
    require(canonical["name"] == PLUGIN_NAME, "canonical manifest name drift")
    require(canonical["version"] == RELEASE_VERSION, "canonical manifest version drift")
    require(canonical["license"] == "MIT", "canonical manifest license drift")
    require(canonical["repository"] == REPOSITORY, "canonical manifest repository drift")
    require(canonical["homepage"] == HOMEPAGE, "canonical manifest homepage drift")
    require(
        canonical["description"].startswith(DESCRIPTION_PREFIX),
        "canonical manifest description prefix drift",
    )
    keywords = canonical["keywords"]
    require(
        isinstance(keywords, list)
        and bool(keywords)
        and all(isinstance(item, str) and bool(item) for item in keywords),
        "canonical manifest keywords must be nonempty strings",
    )
    author = canonical["author"]
    require(
        isinstance(author, dict) and set(author) == {"name", "url"},
        "canonical manifest author schema drift",
    )
    for field in ("name", "url"):
        require_nonempty_string(
            author[field], f"canonical manifest author {field} must be a string"
        )
    for field in sorted(identity_fields):
        require(
            claude[field] == canonical[field],
            f"Claude manifest projection drift: {field}",
        )
    require_nonempty_string(claude["displayName"], "Claude displayName must be a string")
    require(claude["displayName"] == DISPLAY_NAME, "Claude displayName drift")
    extensions = canonical["extensions"]
    require(
        isinstance(extensions, dict) and set(extensions) == {"com.openai"},
        "canonical manifest extension inventory drift",
    )
    openai = extensions["com.openai"]
    require(
        isinstance(openai, dict) and set(openai) == {"interface"},
        "Codex extension keys drift",
    )
    interface = openai["interface"]
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
        interface["displayName"] == DISPLAY_NAME and interface["websiteURL"] == HOMEPAGE,
        "Codex interface metadata drift",
    )
    require(
        interface["capabilities"] == CODEX_CAPABILITIES,
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
    changelog = read(root, "CHANGELOG.md")
    require(
        re.search(rf"^## {re.escape(RELEASE_VERSION)}$", changelog, re.MULTILINE)
        is not None,
        "changelog release drift",
    )


def validate_delivery(root: Path) -> None:
    delivery = load_json(root, "evals/delivery.json", "eval delivery contract")
    require(
        set(delivery) == {"schema_version", "executor", "grader"},
        "eval delivery contract keys drift",
    )
    require(delivery["schema_version"] == 1, "eval delivery schema_version drift")
    executor = delivery["executor"]
    grader = delivery["grader"]
    require(
        isinstance(executor, dict)
        and executor.get("tools") == "denied"
        and isinstance(executor.get("inputs"), list),
        "eval executor contract drift",
    )
    require(
        isinstance(grader, dict)
        and grader.get("distinct_from_executor") is True
        and grader.get("available_after") == "executor-complete"
        and isinstance(grader.get("inputs"), list),
        "eval grader contract drift",
    )


def validate_inventory(
    root: Path,
    semantic_files: set[str],
    *,
    allow_missing_content_lock: bool = False,
) -> None:
    require(
        not (root / "agents").exists(), "Tidesmith must not define persona agents"
    )
    expected_files = BASE_FILES | semantic_files
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
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
    expected_directories: set[str] = set()
    for relative in expected_files:
        parent = Path(relative).parent
        while parent != Path("."):
            expected_directories.add(parent.as_posix())
            parent = parent.parent
    require(actual_directories == expected_directories, "component directory drift")


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


def semantic_file_set(skill_files: set[str]) -> set[str]:
    return (BASE_FILES - {"content-lock.json"}) | skill_files


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


def inspect_contract(root: Path, *, materialize: bool = False) -> set[str]:
    topology = validate_topology(root)
    prompts, skill_files = validate_skills(root, topology)
    validate_manifests(root, topology, prompts)
    validate_delivery(root)
    if materialize:
        materialize_readme_projection(root, topology)
    validate_readme_projection(root, topology)
    return semantic_file_set(skill_files)


def usage() -> None:
    print(
        "usage: validate_tidesmith.py [--write-content-lock] [repo-root]",
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
        semantic_files = inspect_contract(root, materialize=write_lock)
        validate_inventory(
            root, semantic_files, allow_missing_content_lock=write_lock
        )
        validate_portability(root)
        if write_lock:
            lock_path = root / "content-lock.json"
            lock_path.write_text(
                json.dumps(content_lock_document(root, semantic_files), indent=2)
                + "\n",
                encoding="utf-8",
            )
            validate_inventory(root, semantic_files)
        validate_content_lock(root, semantic_files)
    except (
        AgentPluginContractError,
        ContractError,
        OSError,
        UnicodeDecodeError,
    ) as error:
        print(f"Tidesmith contract validation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    if write_lock:
        print("Tidesmith semantic content lock updated")
    print("Tidesmith contract validation passed")


if __name__ == "__main__":
    main()
