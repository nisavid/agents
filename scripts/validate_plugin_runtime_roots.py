#!/usr/bin/env python3
"""Fail closed when public runtime roots contain development-only content."""

from __future__ import annotations

import argparse
import ast
import json
import posixpath
import re
import stat
import sys
from pathlib import Path

PLUGINS = ("versionkeeping", "mergecraft")
ROOT_FILES = {
    ".claude-plugin/plugin.json",
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "plugin.json",
    "topology.json",
}
COMPONENT_FIELDS = {
    "name",
    "entrypoint",
    "interface",
    "references",
    "scripts",
    "modules",
    "calls",
    "operations",
}
OPTIONAL_COMPONENT_FIELDS = {"conditional_calls", "contract"}
MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]*\]\((?P<target>[^)\s]+)(?:\s+[^)]*)?\)")
SCRIPT_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_./-])"
    r"(?P<target>(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.py)"
    r"(?![A-Za-z0-9_.-])"
)


class RuntimeRootError(ValueError):
    """Raised for a stable, actionable runtime-root violation."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeRootError(message)


def strict_json(content: str, label: str):
    def build_object(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    def reject_constant(value: str):
        raise RuntimeRootError(f"non-finite JSON value in {label}: {value}")

    return json.loads(
        content, object_pairs_hook=build_object, parse_constant=reject_constant
    )


def valid_runtime_path(path: object) -> bool:
    if not isinstance(path, str) or not path:
        return False
    candidate = Path(path)
    parts = candidate.parts
    return (
        not candidate.is_absolute()
        and ".." not in parts
        and len(parts) >= 3
        and parts[0] == "skills"
    )


def skill_name(relative: str) -> str:
    parts = Path(relative).parts
    return parts[1]


def resolve_markdown_target(source: str, raw_target: str) -> str | None:
    target = raw_target.removeprefix("<").removesuffix(">")
    target = target.split("#", 1)[0].split("?", 1)[0]
    if (
        not target
        or target.startswith(("/", "#", "//"))
        or ":" in target.split("/", 1)[0]
    ):
        return None
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source), target))
    if resolved == ".." or resolved.startswith("../"):
        return None
    return resolved


def python_module_name(relative: str) -> str:
    parts = list(Path(relative).parts)
    scripts_index = parts.index("scripts")
    module_parts = parts[scripts_index + 1 :]
    module_parts[-1] = Path(module_parts[-1]).stem
    if module_parts[-1] == "__init__":
        module_parts.pop()
    return ".".join(module_parts)


def local_module_index(python_files: set[str]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for relative in python_files:
        module = python_module_name(relative)
        require(module, f"invalid local Python module declaration: {relative}")
        index.setdefault(module, set()).add(relative)
    return index


def resolve_local_module(
    module: str,
    module_index: dict[str, set[str]],
) -> set[str]:
    candidates = module_index.get(module, set())
    require(
        len(candidates) <= 1,
        f"ambiguous local Python import: {module}",
    )
    if not candidates:
        return set()
    resolved = set(candidates)
    parts = module.split(".")
    for length in range(1, len(parts)):
        package = ".".join(parts[:length])
        package_candidates = {
            relative
            for relative in module_index.get(package, set())
            if relative.endswith("/__init__.py")
        }
        require(
            len(package_candidates) <= 1,
            f"ambiguous local Python import: {package}",
        )
        resolved.update(package_candidates)
    return resolved


def imported_local_modules(
    relative: str,
    content: str,
    module_index: dict[str, set[str]],
) -> set[str]:
    try:
        tree = ast.parse(content, filename=relative)
    except SyntaxError as error:
        raise RuntimeRootError(f"invalid runtime Python syntax: {relative}") from error
    current_module = python_module_name(relative)
    if relative.endswith("/__init__.py"):
        current_package = current_module
    else:
        current_package = current_module.rpartition(".")[0]
    resolved: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                resolved.update(resolve_local_module(imported.name, module_index))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package_parts = current_package.split(".") if current_package else []
                retained = len(package_parts) - (node.level - 1)
                require(
                    retained >= 0,
                    f"invalid relative Python import: {relative}",
                )
                base_parts = package_parts[:retained]
                if node.module:
                    base_parts.extend(node.module.split("."))
                base = ".".join(base_parts)
            else:
                base = node.module or ""
            if base:
                resolved.update(resolve_local_module(base, module_index))
            for imported in node.names:
                if imported.name == "*":
                    continue
                child = ".".join(part for part in (base, imported.name) if part)
                resolved.update(resolve_local_module(child, module_index))
    return resolved


def markdown_runtime_edges(
    relative: str,
    content: str,
    declared_files: set[str],
    declared_scripts: set[str],
) -> set[str]:
    edges = {
        target
        for match in MARKDOWN_LINK_PATTERN.finditer(content)
        if (target := resolve_markdown_target(relative, match.group("target")))
        in declared_files
    }
    source_skill = skill_name(relative)
    literal_paths = {
        match.group("target") for match in SCRIPT_PATH_PATTERN.finditer(content)
    }
    for script in declared_scripts:
        aliases = {script, "/".join(Path(script).parts[1:])}
        if skill_name(script) == source_skill:
            aliases.add("/".join(Path(script).parts[2:]))
        if aliases & literal_paths:
            edges.add(script)
    return edges


def validate_runtime_reachability(
    root: Path,
    plugin: str,
    entrypoints: set[str],
    dependencies: set[str],
    declared_files: set[str],
    declared_scripts: set[str],
    python_files: set[str],
) -> None:
    module_index = local_module_index(python_files)
    reachable = set(entrypoints)
    pending = list(entrypoints)
    while pending:
        relative = pending.pop()
        content = (root / relative).read_text(encoding="utf-8")
        if relative.endswith(".md"):
            edges = markdown_runtime_edges(
                relative,
                content,
                declared_files,
                declared_scripts,
            )
        elif relative.endswith(".py"):
            edges = imported_local_modules(relative, content, module_index)
        else:
            edges = set()
        for edge in edges:
            if edge not in reachable:
                reachable.add(edge)
                pending.append(edge)
    unreachable = sorted(dependencies - reachable)
    require(
        not unreachable,
        "declared runtime dependency is unreachable: "
        f"{plugin}/{unreachable[0] if unreachable else ''}",
    )


def validate_plugin(repository: Path, plugin: str) -> None:
    plugins_root = repository / "plugins"
    require(
        plugins_root.is_dir() and not plugins_root.is_symlink(),
        "plugins root must be a real directory",
    )
    root = plugins_root / plugin
    require(
        root.is_dir() and not root.is_symlink(), f"runtime root is invalid: {plugin}"
    )
    actual_files: set[str] = set()
    for entry in root.rglob("*"):
        relative_path = entry.relative_to(root)
        relative = relative_path.as_posix()
        mode = entry.lstat().st_mode
        require(
            not stat.S_ISLNK(mode),
            f"runtime root contains a symlink: {plugin}/{relative}",
        )
        require(
            stat.S_ISREG(mode) or stat.S_ISDIR(mode),
            f"runtime root contains a special entry: {plugin}/{relative}",
        )
        require(
            "__pycache__" not in relative_path.parts and not relative.endswith(".pyc"),
            f"runtime root contains generated Python state: {plugin}/{relative}",
        )
        require(
            "evals" not in relative_path.parts and "tests" not in relative_path.parts,
            f"runtime root contains a development subtree: {plugin}/{relative}",
        )
        require(
            entry.name != "content-lock.json",
            f"runtime root contains a generated content lock: {plugin}/{relative}",
        )
        require(
            "fixtures" not in relative_path.parts
            and not entry.name.startswith("test_"),
            f"runtime root contains test-only content: {plugin}/{relative}",
        )
        if stat.S_ISREG(mode):
            actual_files.add(relative)

    topology_path = root / "topology.json"
    topology = strict_json(
        topology_path.read_text(encoding="utf-8"), f"{plugin} topology"
    )
    require(
        isinstance(topology, dict) and isinstance(topology.get("skills"), list),
        f"runtime topology shape drift: {plugin}",
    )
    declared = set(ROOT_FILES)
    entrypoints: set[str] = set()
    dependencies: set[str] = set()
    declared_scripts: set[str] = set()
    python_files: set[str] = set()
    seen_names: set[str] = set()
    for component in topology["skills"]:
        require(
            isinstance(component, dict)
            and set(component)
            in (COMPONENT_FIELDS, COMPONENT_FIELDS | OPTIONAL_COMPONENT_FIELDS),
            f"runtime topology component shape drift: {plugin}",
        )
        name = component["name"]
        require(
            isinstance(name, str)
            and Path(name).parts == (name,)
            and name not in {".", ".."}
            and name not in seen_names,
            f"runtime topology skill inventory drift: {plugin}",
        )
        seen_names.add(name)
        expected_prefix = f"skills/{name}/"
        for field in ("entrypoint", "interface"):
            value = component[field]
            require(
                valid_runtime_path(value) and value.startswith(expected_prefix),
                f"invalid runtime declaration: {plugin}/{field}",
            )
            require(
                value not in declared,
                f"duplicate runtime declaration: {plugin}/{value}",
            )
            declared.add(value)
            if field == "entrypoint":
                entrypoints.add(value)
        for field in ("references", "scripts", "modules"):
            values = component[field]
            require(
                isinstance(values, list),
                f"runtime declaration list drift: {plugin}/{name}/{field}",
            )
            for value in values:
                require(
                    valid_runtime_path(value) and value.startswith(expected_prefix),
                    f"invalid runtime declaration: {plugin}/{name}/{field}",
                )
                require(
                    value not in declared,
                    f"duplicate runtime declaration: {plugin}/{value}",
                )
                declared.add(value)
                dependencies.add(value)
                if field == "scripts":
                    declared_scripts.add(value)
                if field in {"scripts", "modules"}:
                    python_files.add(value)
    require(actual_files == declared, f"runtime root inventory drift: {plugin}")
    validate_runtime_reachability(
        root,
        plugin,
        entrypoints,
        dependencies,
        declared,
        declared_scripts,
        python_files,
    )


def validate(repository: Path) -> None:
    require(
        repository.is_dir() and not repository.is_symlink(),
        "repository root must be a real directory",
    )
    for plugin in PLUGINS:
        validate_plugin(repository, plugin)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", nargs="?", type=Path, default=Path.cwd())
    arguments = parser.parse_args()
    try:
        validate(arguments.repository)
    except (RuntimeRootError, OSError, json.JSONDecodeError) as error:
        print(f"Plugin runtime-root validation failed: {error}", file=sys.stderr)
        return 1
    print("Plugin runtime-root validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
