#!/usr/bin/env python3
"""Validate or atomically coordinate one persistent sibling worktree creation."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path


CONTROL = re.compile(r"[\x00-\x20\x7f]")
FORBIDDEN_REF_TOKENS = ("..", "@{", "\\", "~", "^", ":", "?", "*", "[")
GIT_TIMEOUT_SECONDS = 120
OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
GIT_ENV_ALLOWLIST = {
    "COMSPEC",
    "HOME",
    "LOGNAME",
    "PATH",
    "PATHEXT",
    "SHELL",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USER",
}


class InvalidTarget(ValueError):
    pass


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise InvalidTarget(message)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise InvalidTarget(message)


def lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def reject_symlink_ancestors(path: Path, label: str) -> Path:
    absolute = lexical_absolute(path)
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.exists() or current.is_symlink():
            require(not current.is_symlink(), f"{label} contains a symlink: {current}")
    return absolute


def directory_identity(path: Path, label: str) -> tuple[int, int]:
    require(not path.is_symlink(), f"{label} must not be a symlink")
    metadata = path.lstat()
    require(stat.S_ISDIR(metadata.st_mode), f"{label} must be a directory")
    require(
        metadata.st_uid == os.getuid(), f"{label} must be owned by the current user"
    )
    require(
        stat.S_IMODE(metadata.st_mode) & 0o022 == 0,
        f"{label} must not be group- or world-writable",
    )
    return metadata.st_dev, metadata.st_ino


def require_same_directory(
    path: Path, expected_identity: tuple[int, int], label: str
) -> None:
    require(
        directory_identity(path, label) == expected_identity,
        f"{label} changed during worktree creation",
    )


def validate_name(value: str) -> str:
    require(isinstance(value, str) and bool(value), "name must be nonempty")
    path = Path(value)
    require(
        not path.is_absolute()
        and len(path.parts) == 1
        and path.parts[0] not in {".", ".."}
        and not value.startswith("-")
        and CONTROL.search(value) is None,
        "name must be one safe relative path component",
    )
    return value


def validate_branch(value: str) -> str:
    require(isinstance(value, str) and bool(value), "branch must be nonempty")
    require(
        not Path(value).is_absolute()
        and not value.startswith("-")
        and value not in {"@", "HEAD"}
        and CONTROL.search(value) is None
        and not any(token in value for token in FORBIDDEN_REF_TOKENS)
        and not value.startswith("/")
        and not value.endswith(("/", "."))
        and "//" not in value
        and all(
            component
            and not component.startswith(".")
            and not component.endswith(".lock")
            for component in value.split("/")
        ),
        "branch must be one safe branch ref without option or traversal syntax",
    )
    return value


def validated_target(main_clone: Path, name: str, branch: str) -> dict:
    clone = reject_symlink_ancestors(main_clone, "main clone")
    require(clone.is_dir(), "main clone must be an existing directory")
    name = validate_name(name)
    branch = validate_branch(branch)

    worktree_root = clone.with_name(f"{clone.name}.wt")
    reject_symlink_ancestors(worktree_root.parent, "worktree root parent")
    require(
        not worktree_root.is_symlink(),
        "sibling worktree root must not be a symlink",
    )
    if worktree_root.exists():
        require(worktree_root.is_dir(), "sibling worktree root must be a directory")
        resolved_root = worktree_root.resolve(strict=True)
    else:
        resolved_root = worktree_root

    target = worktree_root / name
    require(not target.is_symlink(), "worktree target must not be a symlink")
    if target.exists():
        require(target.is_dir(), "worktree target must be a directory when present")
        try:
            target.resolve(strict=True).relative_to(resolved_root)
        except ValueError as error:
            raise InvalidTarget("worktree target escapes sibling .wt root") from error
    require(target.parent == worktree_root, "worktree target escapes sibling .wt root")
    return {
        "schema_version": 1,
        "main_clone": str(clone),
        "worktree_root": str(worktree_root),
        "worktree_path": str(target),
        "branch": branch,
    }


Runner = Callable[..., subprocess.CompletedProcess[str]]


def run_command(arguments: list[str], **options) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, **options)


def git_environment() -> dict[str, str]:
    environment = {
        name: value for name, value in os.environ.items() if name in GIT_ENV_ALLOWLIST
    }
    environment.update(
        {
            "GIT_ASKPASS": "false",
            "GIT_EDITOR": "true",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "never",
            "LC_ALL": "C",
        }
    )
    return environment


def git_arguments(clone: Path, hooks_path: Path, *arguments: str) -> list[str]:
    return [
        "git",
        "-c",
        f"core.hooksPath={hooks_path}",
        "-C",
        str(clone),
        *arguments,
    ]


def invoke_git(
    runner: Runner,
    clone: Path,
    hooks_path: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return runner(
        git_arguments(clone, hooks_path, *arguments),
        check=False,
        capture_output=True,
        text=True,
        env=git_environment(),
        stdin=subprocess.DEVNULL,
        timeout=GIT_TIMEOUT_SECONDS,
    )


def local_branch_oid(
    runner: Runner, clone: Path, hooks_path: Path, branch: str
) -> str | None:
    try:
        result = invoke_git(
            runner,
            clone,
            hooks_path,
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
        )
    except subprocess.TimeoutExpired as error:
        raise InvalidTarget("local branch preflight timed out") from error
    if result.returncode == 1:
        return None
    require(result.returncode == 0, "local branch preflight failed")
    try:
        identity = invoke_git(
            runner,
            clone,
            hooks_path,
            "show-ref",
            "--verify",
            "--hash",
            f"refs/heads/{branch}",
        )
    except subprocess.TimeoutExpired as error:
        raise InvalidTarget("local branch identity check timed out") from error
    require(identity.returncode == 0, "local branch identity check failed")
    object_id = identity.stdout.strip()
    require(
        OBJECT_ID.fullmatch(object_id) is not None,
        "local branch preflight returned a malformed object identity",
    )
    return object_id


def create_worktree(
    receipt: dict,
    *,
    existing_branch: bool = False,
    runner: Runner = run_command,
) -> dict:
    require(
        set(receipt)
        == {
            "schema_version",
            "main_clone",
            "worktree_root",
            "worktree_path",
            "branch",
        }
        and type(receipt["schema_version"]) is int
        and receipt["schema_version"] == 1,
        "worktree receipt schema is invalid",
    )
    expected = validated_target(
        Path(receipt["main_clone"]),
        Path(receipt["worktree_path"]).name,
        receipt["branch"],
    )
    require(receipt == expected, "worktree receipt no longer matches the target")

    clone = Path(receipt["main_clone"])
    worktree_root = Path(receipt["worktree_root"])
    target = Path(receipt["worktree_path"])
    branch = receipt["branch"]

    parent_identity = directory_identity(clone.parent, "worktree root parent")
    clone_identity = directory_identity(clone, "main clone")
    if not worktree_root.exists():
        try:
            worktree_root.mkdir(mode=0o700)
        except FileExistsError:
            pass
    root_identity = directory_identity(worktree_root, "sibling worktree root")
    require_same_directory(clone.parent, parent_identity, "worktree root parent")
    require_same_directory(clone, clone_identity, "main clone")
    require(
        not target.exists() and not target.is_symlink(),
        "worktree target already exists",
    )

    with tempfile.TemporaryDirectory(prefix="versionkeeping-empty-hooks-") as hooks:
        hooks_path = Path(hooks).resolve()
        branch_before = local_branch_oid(runner, clone, hooks_path, branch)
        require(
            (branch_before is not None) == existing_branch,
            "branch existence does not match --existing-branch",
        )
        add_arguments = ["worktree", "add"]
        if existing_branch:
            add_arguments.extend(["--", str(target), branch])
        else:
            add_arguments.extend(["-b", branch, "--", str(target)])

        require_same_directory(clone.parent, parent_identity, "worktree root parent")
        require_same_directory(clone, clone_identity, "main clone")
        require_same_directory(worktree_root, root_identity, "sibling worktree root")
        try:
            result = invoke_git(runner, clone, hooks_path, *add_arguments)
        except subprocess.TimeoutExpired:
            return {
                "schema_version": 1,
                "status": "unknown",
                "reason": "git worktree add timed out after mutation began",
                "worktree_path": str(target),
            }
        if result.returncode != 0:
            return {
                "schema_version": 1,
                "status": "unknown",
                "reason": "git worktree add failed after mutation began",
                "returncode": result.returncode,
                "stderr": result.stderr,
                "worktree_path": str(target),
            }

        try:
            require_same_directory(
                clone.parent, parent_identity, "worktree root parent"
            )
            require_same_directory(clone, clone_identity, "main clone")
            require_same_directory(
                worktree_root, root_identity, "sibling worktree root"
            )
            require(
                not target.is_symlink() and target.is_dir(),
                "created worktree path is invalid",
            )
            require(
                target.resolve(strict=True).parent
                == worktree_root.resolve(strict=True),
                "created worktree escapes sibling root",
            )
            listing = invoke_git(
                runner, clone, hooks_path, "worktree", "list", "--porcelain"
            )
            status_result = invoke_git(runner, target, hooks_path, "status", "--short")
            symbolic_branch = invoke_git(
                runner,
                target,
                hooks_path,
                "symbolic-ref",
                "--quiet",
                "--short",
                "HEAD",
            )
        except (InvalidTarget, OSError, subprocess.TimeoutExpired) as error:
            return {
                "schema_version": 1,
                "status": "unknown",
                "reason": f"worktree creation succeeded but verification failed: {error}",
                "worktree_path": str(target),
            }
        if (
            listing.returncode != 0
            or status_result.returncode != 0
            or symbolic_branch.returncode != 0
            or symbolic_branch.stdout.strip() != branch
            or f"worktree {target}\n" not in f"{listing.stdout}\n"
        ):
            return {
                "schema_version": 1,
                "status": "unknown",
                "reason": "worktree creation succeeded but verification failed",
                "worktree_path": str(target),
            }
    return {
        **receipt,
        "status": "created",
        "existing_branch": existing_branch,
        "status_short": status_result.stdout,
    }


def main(argv=None) -> int:
    parser = ArgumentParser()
    parser.add_argument("--main-clone", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--existing-branch", action="store_true")
    try:
        arguments = parser.parse_args(argv)
        require(
            arguments.create or not arguments.existing_branch,
            "--existing-branch requires --create",
        )
        result = validated_target(
            arguments.main_clone, arguments.name, arguments.branch
        )
        if arguments.create:
            result = create_worktree(
                result,
                existing_branch=arguments.existing_branch,
            )
    except (InvalidTarget, OSError) as error:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "blocked",
                    "reason": str(error),
                },
                sort_keys=True,
            )
        )
        return 2
    if not arguments.create:
        result = {"status": "valid", **result}
    print(json.dumps(result, sort_keys=True))
    return (
        3
        if result["status"] == "unknown"
        else 2
        if result["status"] == "blocked"
        else 0
    )


if __name__ == "__main__":
    sys.exit(main())
