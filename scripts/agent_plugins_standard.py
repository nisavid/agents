"""Agent Plugins v1 manifest, discovery, and resource-containment primitives."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

AGENT_PLUGINS_V1_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
_MANIFEST_FIELDS = {
    "$schema",
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "extensions",
}
_MANIFEST_STRING_FIELDS = {
    "version",
    "description",
    "homepage",
    "repository",
    "license",
}
_AUTHOR_FIELDS = {"name", "email", "url"}
_NAME = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\((?P<destination>[^)]+)\)")
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


class AgentPluginContractError(ValueError):
    """Raised when a package violates the pinned Agent Plugins v1 contract."""


def _fail(detail: str) -> None:
    raise AgentPluginContractError(f"Agent Plugins v1 manifest {detail}")


def _package_root(plugin_root: Path) -> Path:
    if plugin_root.is_symlink():
        raise AgentPluginContractError("Agent Plugin root must not be a symlink")
    try:
        resolved = plugin_root.resolve(strict=True)
    except OSError as error:
        raise AgentPluginContractError("Agent Plugin root is missing") from error
    if not resolved.is_dir():
        raise AgentPluginContractError("Agent Plugin root must be a directory")
    return resolved


def _decode_json_object(content: str) -> dict:
    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise AgentPluginContractError(
                    f"Agent Plugins v1 manifest contains duplicate key: {key}"
                )
            value[key] = item
        return value

    def reject_constant(value: str):
        raise AgentPluginContractError(
            f"Agent Plugins v1 manifest contains non-finite JSON value: {value}"
        )

    try:
        document = json.loads(
            content,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as error:
        raise AgentPluginContractError(
            f"Agent Plugins v1 manifest contains invalid JSON: {error.msg}"
        ) from error
    if not isinstance(document, dict):
        _fail("must contain an object")
    return document


def validate_agent_plugin_manifest(document: dict) -> None:
    """Validate the exact JSON-schema surface pinned by Agent Plugins v1.0.0."""

    if not isinstance(document, dict):
        _fail("must contain an object")
    unknown = set(document) - _MANIFEST_FIELDS
    if unknown:
        _fail(f"contains unknown field: {min(unknown)}")
    for required in ("$schema", "name"):
        if required not in document:
            _fail(f"is missing required field: {required}")
    if document["$schema"] != AGENT_PLUGINS_V1_SCHEMA:
        _fail("schema identifier drift")

    name = document["name"]
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= 64
        or _NAME.fullmatch(name) is None
    ):
        _fail("name is invalid")

    for field in _MANIFEST_STRING_FIELDS:
        if field in document and not isinstance(document[field], str):
            _fail(f"{field} must be a string")

    if "author" in document:
        author = document["author"]
        if not isinstance(author, dict):
            _fail("author must be an object")
        unknown_author = set(author) - _AUTHOR_FIELDS
        if unknown_author:
            _fail(f"author contains unknown field: {min(unknown_author)}")
        for field, value in author.items():
            if not isinstance(value, str):
                _fail(f"author {field} must be a string")

    if "keywords" in document:
        keywords = document["keywords"]
        if not isinstance(keywords, list):
            _fail("keywords must be an array")
        if not all(isinstance(keyword, str) for keyword in keywords):
            _fail("keywords entries must be strings")

    if "extensions" in document:
        extensions = document["extensions"]
        if not isinstance(extensions, dict):
            _fail("extensions must be an object")
        for namespace, value in extensions.items():
            if not isinstance(value, dict):
                _fail(f"extension {namespace} must be an object")


def load_agent_plugin_manifest(plugin_root: Path) -> dict:
    """Load and validate ``plugin.json`` without following package symlinks."""

    root = _package_root(plugin_root)
    path = root / "plugin.json"
    if path.is_symlink() or not path.is_file():
        raise AgentPluginContractError(
            "Agent Plugins v1 manifest must be a regular root plugin.json"
        )
    try:
        document = _decode_json_object(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise AgentPluginContractError(
            "Agent Plugins v1 manifest plugin.json is unreadable"
        ) from error
    validate_agent_plugin_manifest(document)
    return document


def discover_direct_skills(plugin_root: Path) -> tuple[str, ...]:
    """Return the standard skill names discovered as direct ``skills/`` children."""

    root = _package_root(plugin_root)
    skills = root / "skills"
    if not skills.exists():
        return ()
    if skills.is_symlink() or not skills.is_dir():
        raise AgentPluginContractError(
            "Agent Plugins skills location must be a regular directory"
        )

    discovered = []
    for child in sorted(skills.iterdir(), key=lambda path: path.name):
        if child.is_symlink():
            raise AgentPluginContractError(
                f"Agent Plugins skill child must not be a symlink: {child.name}"
            )
        if not child.is_dir():
            continue
        descriptor = child / "SKILL.md"
        if not descriptor.exists():
            continue
        if descriptor.is_symlink() or not descriptor.is_file():
            raise AgentPluginContractError(
                f"Agent Plugins skill descriptor must be a regular file: {child.name}"
            )
        discovered.append(child.name)
    return tuple(discovered)


def _link_path(destination: str) -> str | None:
    destination = destination.strip()
    if destination.startswith("<") and ">" in destination:
        destination = destination[1 : destination.index(">")]
    else:
        destination = destination.split(maxsplit=1)[0]
    if not destination or destination.startswith(("#", "//")):
        return None
    if _URI_SCHEME.match(destination):
        return None
    return unquote(urlsplit(destination).path)


def _resolve_skill_resource(root: Path, skill_root: Path, destination: str) -> Path:
    relative_text = _link_path(destination)
    if relative_text is None:
        return root
    relative = Path(relative_text)
    if relative.is_absolute():
        raise AgentPluginContractError(
            f"Agent Skill resource escapes plugin root: {destination}"
        )

    lexical = skill_root
    for part in relative.parts:
        if part in ("", "."):
            continue
        if part == "..":
            lexical = lexical.parent
            continue
        lexical /= part
        if lexical.is_symlink():
            raise AgentPluginContractError(
                f"Agent Skill resource path contains a symlink: {destination}"
            )

    try:
        resolved = lexical.resolve(strict=True)
    except OSError as error:
        raise AgentPluginContractError(
            f"Agent Skill resource is missing: {destination}"
        ) from error
    if not resolved.is_relative_to(root):
        raise AgentPluginContractError(
            f"Agent Skill resource escapes plugin root: {destination}"
        )
    if not resolved.is_file():
        raise AgentPluginContractError(
            f"Agent Skill resource must be a regular file: {destination}"
        )
    return resolved


def validate_skill_resource_links(
    plugin_root: Path, skill_names: tuple[str, ...]
) -> None:
    """Require every relative Markdown resource named by a skill to stay contained."""

    root = _package_root(plugin_root)
    for skill_name in skill_names:
        skill_root = root / "skills" / skill_name
        descriptor = skill_root / "SKILL.md"
        if descriptor.is_symlink() or not descriptor.is_file():
            raise AgentPluginContractError(
                f"Agent Plugins skill descriptor is missing: {skill_name}"
            )
        try:
            content = descriptor.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise AgentPluginContractError(
                f"Agent Plugins skill descriptor is unreadable: {skill_name}"
            ) from error
        for match in _MARKDOWN_LINK.finditer(content):
            _resolve_skill_resource(root, skill_root, match.group("destination"))
