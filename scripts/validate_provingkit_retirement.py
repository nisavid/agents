#!/usr/bin/env python3
"""Validate that active Provingkit source lives only in its canonical repository."""

from __future__ import annotations

import argparse
import importlib.util
import marshal
import os
import subprocess
import sys
from pathlib import Path

CANONICAL_REPOSITORY = "https://github.com/nisavid/provingkit"

FORBIDDEN_ACTIVE_PATHS = (
    Path(".claude-plugin/marketplace.json"),
    Path(".github/workflows/source-skill-disposition.yml"),
    Path(".github/workflows/versionkeeping-native-credentials.yml"),
    Path("evals"),
    Path("plugins"),
    Path("release"),
)

PRESERVED_OWNER_PATHS = (
    Path(".scratch/chatgpt-airgap-unlock"),
    Path("tooling/chatgpt-ffs"),
    Path("tooling/codex-ns-proxy"),
    Path("tooling/hindsight"),
)

REQUIRED_REAL_DIRECTORIES = (
    Path("scripts"),
    Path("tests"),
    *PRESERVED_OWNER_PATHS,
    Path("docs/superpowers/research"),
    Path("docs/superpowers/specs"),
    Path("docs/plugin-system"),
)

ALLOWED_ROOT_SCRIPTS = {Path("scripts/validate_provingkit_retirement.py")}
ALLOWED_ROOT_TESTS = {Path("tests/test_validate_provingkit_retirement.py")}
ALLOWED_ROOT_EXECUTION_FILES = ALLOWED_ROOT_SCRIPTS | ALLOWED_ROOT_TESTS


class RetirementError(RuntimeError):
    """The repository still exposes an active Provingkit-owned surface."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RetirementError(message)


def path_present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def is_real_directory(repository: Path, relative: Path) -> bool:
    current = repository
    for component in relative.parts:
        current /= component
        if not current.is_dir() or current.is_symlink():
            return False
    return True


def is_real_file(repository: Path, relative: Path) -> bool:
    path = repository / relative
    return path.is_file() and not path.is_symlink()


def git_tracked_paths(repository: Path) -> set[Path] | None:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("GIT_")
    }
    try:
        repository_check = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "rev-parse",
                "--is-inside-work-tree",
                "--show-prefix",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
        if repository_check.returncode != 0 or repository_check.stdout != b"true\n\n":
            return None
        completed = subprocess.run(
            ["git", "-C", str(repository), "ls-files", "-z", "--", "scripts", "tests"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return {
        Path(os.fsdecode(record))
        for record in completed.stdout.split(b"\0")
        if record
    }


def cache_optimization(path: Path, source: Path) -> int | None:
    cache_stem = f"{source.stem}.{sys.implementation.cache_tag}"
    if path.name == f"{cache_stem}.pyc":
        return 0
    for optimization in (1, 2):
        if path.name == f"{cache_stem}.opt-{optimization}.pyc":
            return optimization
    return None


def matches_compiled_source(
    path: Path,
    source: Path,
    source_names: tuple[str, ...],
    optimization: int,
) -> bool:
    try:
        bytecode = path.read_bytes()
        source_bytes = source.read_bytes()
        source_stat = source.stat()
        compiled_codes = [
            compile(
                source_bytes,
                source_name,
                "exec",
                dont_inherit=True,
                optimize=optimization,
            )
            for source_name in source_names
        ]
        compiled_payloads = {marshal.dumps(code) for code in compiled_codes}
    except (OSError, SyntaxError, UnicodeError, ValueError):
        return False

    if len(bytecode) < 16 or bytecode[:4] != importlib.util.MAGIC_NUMBER:
        return False
    flags = int.from_bytes(bytecode[4:8], "little")
    if flags == 0:
        metadata_matches = (
            bytecode[8:12]
            == (int(source_stat.st_mtime) & 0xFFFFFFFF).to_bytes(4, "little")
            and bytecode[12:16]
            == (len(source_bytes) & 0xFFFFFFFF).to_bytes(4, "little")
        )
    elif flags in (1, 3):
        metadata_matches = bytecode[8:16] == importlib.util.source_hash(source_bytes)
    else:
        return False
    return metadata_matches and bytecode[16:] in compiled_payloads


def is_verified_untracked_bytecode_cache(
    repository: Path,
    path: Path,
    relative: Path,
    tracked_paths: set[Path] | None,
) -> bool:
    if (
        tracked_paths is None
        or relative in tracked_paths
        or path.parent.name != "__pycache__"
        or not path.is_file()
        or path.is_symlink()
        or path.suffix != ".pyc"
    ):
        return False
    try:
        source = Path(importlib.util.source_from_cache(str(path)))
        source_relative = source.relative_to(repository)
    except ValueError:
        return False
    if (
        source_relative not in ALLOWED_ROOT_EXECUTION_FILES
        or source_relative not in tracked_paths
        or not source.is_file()
        or source.is_symlink()
    ):
        return False
    optimization = cache_optimization(path, source)
    return optimization is not None and matches_compiled_source(
        path,
        source,
        (str(source), str(source_relative)),
        optimization,
    )


def walk_entries(root: Path) -> list[Path]:
    entries = []
    directories = [root]
    while directories:
        directory = directories.pop()
        with os.scandir(directory) as scanner:
            for entry in scanner:
                path = Path(entry.path)
                entries.append(path)
                if entry.is_dir(follow_symlinks=False):
                    directories.append(path)
    return entries


def entries_below(
    repository: Path, relative_root: Path, tracked_paths: set[Path] | None
) -> set[Path]:
    root = repository / relative_root
    if not root.is_dir():
        return set()
    entries = set()
    allowed_cache_directory = relative_root / "__pycache__"
    for path in walk_entries(root):
        relative = path.relative_to(repository)
        if (
            relative == allowed_cache_directory
            and tracked_paths is not None
            and relative not in tracked_paths
            and path.is_dir()
            and not path.is_symlink()
        ):
            continue
        if not is_verified_untracked_bytecode_cache(
            repository, path, relative, tracked_paths
        ):
            entries.add(relative)
    return entries


def validate_retirement(repository: Path) -> None:
    repository = repository.resolve()
    require(repository.is_dir(), "repository root is not a directory")
    tracked_paths = git_tracked_paths(repository)

    for relative in FORBIDDEN_ACTIVE_PATHS:
        require(
            not path_present(repository / relative),
            f"active Provingkit path remains: {relative.as_posix()}",
        )

    for relative in REQUIRED_REAL_DIRECTORIES:
        require(
            is_real_directory(repository, relative),
            f"path must be a real directory: {relative.as_posix()}",
        )

    for relative in ALLOWED_ROOT_EXECUTION_FILES:
        require(
            is_real_file(repository, relative),
            f"allowed root execution path must be a real file: {relative.as_posix()}",
        )

    require(
        entries_below(repository, Path("scripts"), tracked_paths)
        == ALLOWED_ROOT_SCRIPTS,
        "unexpected active root script remains",
    )
    require(
        entries_below(repository, Path("tests"), tracked_paths)
        == ALLOWED_ROOT_TESTS,
        "unexpected active root test remains",
    )

    for relative in ALLOWED_ROOT_EXECUTION_FILES:
        require(
            tracked_paths is not None and relative in tracked_paths,
            f"allowed root execution path must be tracked: {relative.as_posix()}",
        )

    readme = (repository / "README.md").read_text(encoding="utf-8")
    require(
        CANONICAL_REPOSITORY in readme,
        "README does not name the canonical Provingkit repository",
    )
    require(
        "https://github.com/nisavid/agents/issues/82" in readme,
        "README does not preserve the Base Loadout owner",
    )
    for stale_route in (".claude-plugin/marketplace.json", "plugins/", "release/"):
        require(
            stale_route not in readme,
            f"README still advertises retired route: {stale_route}",
        )

    redirect = repository / "docs/plugin-system/design-principles.md"
    require(
        redirect.is_file() and not redirect.is_symlink(),
        "design-principles redirect must be a real file",
    )
    require(
        f"{CANONICAL_REPOSITORY}/blob/main/docs/plugin-system/design-principles.md"
        in redirect.read_text(encoding="utf-8"),
        "design-principles redirect does not point to canonical source",
    )

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("repository", nargs="?", type=Path, default=Path.cwd())
    arguments = parser.parse_args()

    try:
        validate_retirement(arguments.repository)
    except (OSError, RetirementError, UnicodeError) as error:
        print(f"provingkit-source-retirement: {error}", file=sys.stderr)
        return 1

    print("Provingkit source retirement valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
