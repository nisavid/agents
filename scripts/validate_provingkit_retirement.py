#!/usr/bin/env python3
"""Validate that active Provingkit source lives only in its canonical repository."""

from __future__ import annotations

import argparse
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
)

ALLOWED_ROOT_SCRIPTS = {Path("scripts/validate_provingkit_retirement.py")}
ALLOWED_ROOT_TESTS = {Path("tests/test_validate_provingkit_retirement.py")}


class RetirementError(RuntimeError):
    """The repository still exposes an active Provingkit-owned surface."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RetirementError(message)


def path_present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def files_below(repository: Path, relative_root: Path) -> set[Path]:
    root = repository / relative_root
    if not root.is_dir():
        return set()
    return {
        path.relative_to(repository)
        for path in root.rglob("*")
        if (path.is_file() or path.is_symlink())
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
    }


def validate_retirement(repository: Path) -> None:
    repository = repository.resolve()
    require(repository.is_dir(), "repository root is not a directory")

    for relative in FORBIDDEN_ACTIVE_PATHS:
        require(
            not path_present(repository / relative),
            f"active Provingkit path remains: {relative.as_posix()}",
        )

    for relative in REQUIRED_REAL_DIRECTORIES:
        path = repository / relative
        require(
            path.is_dir() and not path.is_symlink(),
            f"path must be a real directory: {relative.as_posix()}",
        )

    require(
        files_below(repository, Path("scripts")) == ALLOWED_ROOT_SCRIPTS,
        "unexpected active root script remains",
    )
    require(
        files_below(repository, Path("tests")) == ALLOWED_ROOT_TESTS,
        "unexpected active root test remains",
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
