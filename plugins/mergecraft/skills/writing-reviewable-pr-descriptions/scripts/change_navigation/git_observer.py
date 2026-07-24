"""Deterministically observe an exact committed Git diff."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any


OID = re.compile(r"[0-9a-f]{40}")


class GitObservationError(ValueError):
    """The bound repository or exact object inventory is unavailable."""


def _run(repository: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise GitObservationError(detail or f"git {' '.join(arguments)} failed")
    return completed.stdout


def _path(value: bytes) -> str:
    try:
        path = value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise GitObservationError("Git diff path is not UTF-8") from error
    if not path or "\r" in path or "\n" in path or path.startswith("/"):
        raise GitObservationError(
            "Git diff path is not a safe repository-relative path"
        )
    if ".." in Path(path).parts:
        raise GitObservationError("Git diff path escapes the repository")
    return path


def _numstat(
    repository: Path, base_oid: str, head_oid: str
) -> dict[tuple[str | None, str], tuple[int | None, int | None, bool]]:
    output = _run(
        repository,
        "diff",
        "--numstat",
        "-z",
        "--find-renames=50%",
        "--find-copies=50%",
        "--find-copies-harder",
        base_oid,
        head_oid,
        "--",
    )
    tokens = output.split(b"\0")
    if tokens and tokens[-1] == b"":
        tokens.pop()
    result: dict[tuple[str | None, str], tuple[int | None, int | None, bool]] = {}
    index = 0
    while index < len(tokens):
        fields = tokens[index].split(b"\t", 2)
        if len(fields) != 3:
            raise GitObservationError("Git numstat output is malformed")
        added, deleted, path = fields
        index += 1
        source_path: str | None = None
        if path == b"":
            if index + 1 >= len(tokens):
                raise GitObservationError("Git rename numstat output is incomplete")
            source_path = _path(tokens[index])
            target_path = _path(tokens[index + 1])
            index += 2
        else:
            target_path = _path(path)
        binary = added == b"-" and deleted == b"-"
        if binary:
            metrics = (None, None, True)
        else:
            try:
                metrics = (int(added), int(deleted), False)
            except ValueError as error:
                raise GitObservationError(
                    "Git numstat metrics are malformed"
                ) from error
        key = (source_path, target_path)
        if key in result:
            raise GitObservationError("Git numstat contains a duplicate path")
        result[key] = metrics
    return result


def observe_git_diff(
    repository: Path,
    *,
    base_oid: str,
    head_oid: str,
    require_clean: bool = True,
) -> list[dict[str, Any]]:
    """Return the exact raw file inventory for two commits in one bound repository."""
    repository = repository.resolve(strict=True)
    if not repository.is_dir() or repository.is_symlink():
        raise GitObservationError("bound Git repository is invalid")
    top = Path(
        _run(repository, "rev-parse", "--show-toplevel").decode().strip()
    ).resolve()
    if top != repository:
        raise GitObservationError(
            "bound Git repository must be its exact worktree root"
        )
    for name, oid in (("base", base_oid), ("head", head_oid)):
        if not OID.fullmatch(oid):
            raise GitObservationError(f"{name} OID is not full-length lowercase hex")
        _run(repository, "cat-file", "-e", f"{oid}^{{commit}}")
    if require_clean and _run(repository, "status", "--porcelain=v1", "-z"):
        raise GitObservationError("bound Git repository has dirty-state ambiguity")

    numstat = _numstat(repository, base_oid, head_oid)
    output = _run(
        repository,
        "diff",
        "--name-status",
        "-z",
        "--find-renames=50%",
        "--find-copies=50%",
        "--find-copies-harder",
        base_oid,
        head_oid,
        "--",
    )
    tokens = output.split(b"\0")
    if tokens and tokens[-1] == b"":
        tokens.pop()
    rows: list[dict[str, Any]] = []
    index = 0
    operations = {"A": "added", "M": "modified", "D": "deleted", "T": "type-changed"}
    while index < len(tokens):
        status = tokens[index].decode("ascii", "strict")
        index += 1
        code = status[:1]
        source_path: str | None = None
        if code in {"R", "C"}:
            if index + 1 >= len(tokens):
                raise GitObservationError(
                    "Git name-status rename/copy output is incomplete"
                )
            source_path = _path(tokens[index])
            target_path = _path(tokens[index + 1])
            index += 2
            operation = "renamed" if code == "R" else "copied"
        elif code in operations:
            if index >= len(tokens):
                raise GitObservationError("Git name-status output is incomplete")
            target_path = _path(tokens[index])
            index += 1
            operation = operations[code]
        else:
            raise GitObservationError(f"unsupported Git diff status: {status}")
        metrics = numstat.pop((source_path, target_path), None)
        if metrics is None:
            raise GitObservationError("Git name-status and numstat inventories differ")
        additions, deletions, binary = metrics
        rows.append(
            {
                "source_path": source_path,
                "target_path": target_path,
                "operation": operation,
                "additions": additions,
                "deletions": deletions,
                "binary": binary,
            }
        )
    if numstat:
        raise GitObservationError("Git numstat contains unpaired paths")
    return sorted(rows, key=lambda row: (row["target_path"], row["source_path"] or ""))
