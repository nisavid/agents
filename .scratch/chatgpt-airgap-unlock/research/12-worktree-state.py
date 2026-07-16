#!/usr/bin/env python3
"""Bind Ticket 12 worktree UI markers to authoritative local state."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sqlite3
import subprocess
import tempfile
from typing import Any


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class ContractError(RuntimeError):
    pass


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def resolved(path: pathlib.Path | str) -> pathlib.Path:
    return pathlib.Path(path).resolve(strict=True)


def git(repo: pathlib.Path, *arguments: str) -> bytes:
    process = subprocess.run(
        ["/usr/bin/git", "-C", str(repo), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.returncode != 0:
        raise ContractError(
            f"git {' '.join(arguments)} failed with {process.returncode}: "
            f"{process.stderr.decode(errors='replace').strip()}"
        )
    return process.stdout


def worktree_porcelain(repo: pathlib.Path) -> list[dict[str, str | bool]]:
    fields = git(repo, "worktree", "list", "--porcelain", "-z").decode().split("\0")
    records: list[dict[str, str | bool]] = []
    current: dict[str, str | bool] = {}
    for field in fields:
        if not field:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = field.partition(" ")
        current[key] = value if value else True
    if current:
        records.append(current)
    if not records or any("worktree" not in record or "HEAD" not in record for record in records):
        raise ContractError("git worktree porcelain did not publish complete records")
    return records


def status_porcelain(repo: pathlib.Path) -> dict[str, Any]:
    raw = git(repo, "status", "--porcelain=v2", "--branch", "-z").decode()
    fields = [field for field in raw.split("\0") if field]
    headers = [field for field in fields if field.startswith("# ")]
    entries = [field for field in fields if not field.startswith("# ")]
    head = next((field.removeprefix("# branch.head ") for field in headers if field.startswith("# branch.head ")), None)
    oid = next((field.removeprefix("# branch.oid ") for field in headers if field.startswith("# branch.oid ")), None)
    if head is None or oid is None:
        raise ContractError("git status porcelain omitted branch identity")
    return {"branch": head, "head": oid, "entryCount": len(entries), "rawSha256": sha256(raw)}


def remotes(repo: pathlib.Path) -> list[str]:
    return [line for line in git(repo, "remote").decode().splitlines() if line]


def require_main_fixture(repo: pathlib.Path) -> dict[str, Any]:
    status = status_porcelain(repo)
    if status["branch"] != "main":
        raise ContractError(f"fixture branch must be main, got {status['branch']!r}")
    if status["entryCount"] != 0:
        raise ContractError("fixture main worktree is not clean")
    if remotes(repo):
        raise ContractError("fixture repository has a configured remote")
    records = worktree_porcelain(repo)
    main = [record for record in records if resolved(str(record["worktree"])) == repo]
    if len(main) != 1 or main[0].get("branch") != "refs/heads/main":
        raise ContractError("fixture main worktree porcelain is not uniquely bound to refs/heads/main")
    return {"head": status["head"], "statusSha256": status["rawSha256"]}


def read_cdp(path: pathlib.Path) -> list[dict[str, Any]]:
    records = []
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ContractError(f"invalid CDP JSON at line {line_number}") from error
        if isinstance(value, dict):
            records.append(value)
    return records


def exact_marker(records: list[dict[str, Any]], kind: str, **values: Any) -> dict[str, Any]:
    phase = values.get("phase")
    matches = [
        record for record in records
        if record.get("kind") == kind and (phase is None or record.get("phase") == phase)
    ]
    if len(matches) != 1:
        raise ContractError(f"expected exactly one {kind} marker, found {len(matches)}")
    marker = matches[0]
    mismatches = {
        key: {"expected": value, "actual": marker.get(key)}
        for key, value in values.items()
        if marker.get(key) != value
    }
    if mismatches:
        raise ContractError(f"{kind} marker fields do not match: {mismatches}")
    return marker


def thread_rows(database: pathlib.Path, cwd: pathlib.Path) -> list[str]:
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT id FROM threads WHERE cwd = ? ORDER BY id", (str(cwd),)
            ).fetchall()
    except sqlite3.Error as error:
        raise ContractError(f"authoritative threads database is unavailable: {error}") from error
    return [str(row[0]) for row in rows]


def read_rollouts(codex_home: pathlib.Path) -> list[tuple[pathlib.Path, list[dict[str, Any]]]]:
    values = []
    for path in sorted(codex_home.glob("sessions/**/rollout-*.jsonl")):
        records = []
        for line_number, line in enumerate(path.read_text().splitlines(), 1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ContractError(f"invalid rollout JSON at {path}:{line_number}") from error
            if isinstance(value, dict):
                records.append(value)
        values.append((path, records))
    return values


def matching_rollout(codex_home: pathlib.Path, thread_id: str, cwd: pathlib.Path) -> pathlib.Path:
    matches = []
    for path, records in read_rollouts(codex_home):
        metas = [record.get("payload", {}) for record in records if record.get("type") == "session_meta"]
        if len(metas) == 1 and metas[0].get("id") == thread_id and metas[0].get("cwd") == str(cwd):
            matches.append(path)
    if len(matches) != 1:
        raise ContractError(f"expected one rollout session_meta for worktree thread, found {len(matches)}")
    return matches[0]


def linked_worktree(repo: pathlib.Path, worktree_root: pathlib.Path, expected_head: str) -> pathlib.Path:
    records = worktree_porcelain(repo)
    linked = [record for record in records if resolved(str(record["worktree"])) != repo]
    if len(linked) != 1:
        raise ContractError(f"expected one linked worktree, found {len(linked)}")
    worktree = resolved(str(linked[0]["worktree"]))
    if worktree == worktree_root or not worktree.is_relative_to(worktree_root):
        raise ContractError("linked worktree is outside the exact configured worktree root")
    if linked[0].get("HEAD") != expected_head:
        raise ContractError("linked worktree does not start from the fixture main HEAD")
    status = status_porcelain(worktree)
    if status["entryCount"] != 0:
        raise ContractError("linked worktree is not clean")
    if remotes(worktree):
        raise ContractError("linked worktree unexpectedly exposes a configured remote")
    return worktree


def owner_thread(worktree: pathlib.Path) -> tuple[str, pathlib.Path]:
    git_dir_value = git(worktree, "rev-parse", "--git-dir").decode().strip()
    git_dir = pathlib.Path(git_dir_value)
    if not git_dir.is_absolute():
        git_dir = worktree / git_dir
    config_path = resolved(git_dir) / "codex-thread.json"
    if not config_path.is_file() or config_path.is_symlink():
        raise ContractError("linked worktree has no regular codex-thread.json")
    try:
        config = json.loads(config_path.read_text())
    except json.JSONDecodeError as error:
        raise ContractError("linked worktree codex-thread.json is invalid") from error
    if set(config) != {"version", "ownerThreadId"} or config.get("version") != 1:
        raise ContractError("linked worktree codex-thread.json has the wrong schema")
    thread_id = config.get("ownerThreadId")
    if not isinstance(thread_id, str) or not UUID_RE.fullmatch(thread_id):
        raise ContractError("linked worktree ownerThreadId is not a UUID")
    return thread_id, config_path


def write_state(path: pathlib.Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, sort_keys=True) + "\n")
    path.chmod(0o600)


def capture_baseline(repo_path: pathlib.Path, worktree_root_path: pathlib.Path, state_path: pathlib.Path) -> None:
    repo = resolved(repo_path)
    worktree_root = resolved(worktree_root_path)
    if any(worktree_root.iterdir()):
        raise ContractError("configured worktree root is not empty at baseline")
    fixture = require_main_fixture(repo)
    write_state(state_path, {
        "schemaVersion": 1,
        "repoRootSha256": sha256(str(repo)),
        "worktreeRootSha256": sha256(str(worktree_root)),
        "mainHead": fixture["head"],
        "mainStatusSha256": fixture["statusSha256"],
        "baselineValidated": True,
    })


def validate_first(
    repo_path: pathlib.Path,
    worktree_root_path: pathlib.Path,
    database: pathlib.Path,
    codex_home_path: pathlib.Path,
    state_path: pathlib.Path,
    cdp_path: pathlib.Path,
) -> None:
    repo = resolved(repo_path)
    worktree_root = resolved(worktree_root_path)
    codex_home = resolved(codex_home_path)
    state = json.loads(state_path.read_text())
    if state.get("schemaVersion") != 1 or state.get("baselineValidated") is not True:
        raise ContractError("worktree baseline state is missing or invalid")
    if state.get("repoRootSha256") != sha256(str(repo)) or state.get("worktreeRootSha256") != sha256(str(worktree_root)):
        raise ContractError("worktree baseline path binding changed")
    fixture = require_main_fixture(repo)
    if fixture["head"] != state.get("mainHead"):
        raise ContractError("fixture main HEAD changed during worktree creation")
    worktree = linked_worktree(repo, worktree_root, str(state["mainHead"]))
    thread_id, config_path = owner_thread(worktree)
    rows = thread_rows(resolved(database), worktree)
    if rows != [thread_id]:
        raise ContractError("SQLite worktree cwd is not uniquely bound to the owner thread")
    rollout = matching_rollout(codex_home, thread_id, worktree)
    cdp = read_cdp(resolved(cdp_path))
    exact_marker(
        cdp, "worktree-mode-selected", phase="worktree-first", selected=True, uniqueControl=True
    )
    exact_marker(
        cdp,
        "worktree-first-summary",
        phase="worktree-first",
        worktreeRootSaved=True,
        worktreeModeSelected=True,
        rendererPromptCompleted=True,
        tasksSurfaceObserved=True,
    )
    state.update({
        "threadId": thread_id,
        "worktreePath": str(worktree),
        "worktreePathSha256": sha256(str(worktree)),
        "codexThreadPathSha256": sha256(str(config_path)),
        "rolloutPathSha256": sha256(str(rollout)),
        "firstValidated": True,
    })
    write_state(state_path, state)


def validate_cold(
    repo_path: pathlib.Path,
    worktree_root_path: pathlib.Path,
    database: pathlib.Path,
    codex_home_path: pathlib.Path,
    state_path: pathlib.Path,
    cdp_path: pathlib.Path,
) -> None:
    repo = resolved(repo_path)
    worktree_root = resolved(worktree_root_path)
    codex_home = resolved(codex_home_path)
    state = json.loads(state_path.read_text())
    if state.get("firstValidated") is not True:
        raise ContractError("first-phase worktree state was not validated")
    fixture = require_main_fixture(repo)
    if fixture["head"] != state.get("mainHead"):
        raise ContractError("fixture main HEAD changed across cold restart")
    worktree = linked_worktree(repo, worktree_root, str(state["mainHead"]))
    if str(worktree) != state.get("worktreePath") or sha256(str(worktree)) != state.get("worktreePathSha256"):
        raise ContractError("linked worktree identity changed across cold restart")
    thread_id, config_path = owner_thread(worktree)
    if thread_id != state.get("threadId") or sha256(str(config_path)) != state.get("codexThreadPathSha256"):
        raise ContractError("linked worktree owner changed across cold restart")
    if thread_rows(resolved(database), worktree) != [thread_id]:
        raise ContractError("SQLite worktree cwd changed across cold restart")
    rollout = matching_rollout(codex_home, thread_id, worktree)
    if sha256(str(rollout)) != state.get("rolloutPathSha256"):
        raise ContractError("worktree rollout identity changed across cold restart")
    cdp = read_cdp(resolved(cdp_path))
    exact_marker(
        cdp, "worktree-mode-selected", phase="worktree-first", selected=True, uniqueControl=True
    )
    exact_marker(
        cdp,
        "worktree-first-summary",
        phase="worktree-first",
        worktreeRootSaved=True,
        worktreeModeSelected=True,
        rendererPromptCompleted=True,
        tasksSurfaceObserved=True,
    )
    exact_marker(
        cdp,
        "worktree-thread-reopened",
        phase="worktree-second",
        reopened=True,
        threadId=thread_id,
        cwdSha256=state["worktreePathSha256"],
    )
    exact_marker(
        cdp,
        "worktree-second-summary",
        phase="worktree-second",
        rendererThreadReopened=True,
        persistedOutputVisible=True,
        rendererContinuationCompleted=True,
    )
    state["coldValidated"] = True
    write_state(state_path, state)


def expect_error(action, fragment: str) -> None:
    try:
        action()
    except ContractError as error:
        if fragment not in str(error):
            raise AssertionError(f"expected {fragment!r}, got {str(error)!r}") from error
    else:
        raise AssertionError(f"expected ContractError containing {fragment!r}")


def run_self_tests() -> None:
    with tempfile.TemporaryDirectory(prefix="chatgpt-worktree-state-") as directory:
        root = pathlib.Path(directory)
        repo = root / "fixture"
        worktree_root = root / "worktrees"
        codex_home = root / "codex"
        database = codex_home / "state_5.sqlite"
        state_path = root / "state.json"
        cdp_path = root / "cdp.jsonl"
        repo.mkdir()
        worktree_root.mkdir()
        codex_home.mkdir()
        subprocess.run(["/usr/bin/git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
        subprocess.run(["/usr/bin/git", "-C", str(repo), "config", "user.name", "Ivan D Vasin"], check=True)
        subprocess.run(["/usr/bin/git", "-C", str(repo), "config", "user.email", "ivan@nisavid.io"], check=True)
        (repo / "README.md").write_text("fixture\n")
        subprocess.run(["/usr/bin/git", "-C", str(repo), "add", "README.md"], check=True)
        subprocess.run(["/usr/bin/git", "-C", str(repo), "commit", "-qm", "test: fixture"], check=True)
        capture_baseline(repo, worktree_root, state_path)

        linked = worktree_root / "managed"
        subprocess.run(["/usr/bin/git", "-C", str(repo), "worktree", "add", "-q", "--detach", str(linked)], check=True)
        thread_id = "019f0000-0000-7000-8000-000000000012"
        git_dir_value = git(linked, "rev-parse", "--git-dir").decode().strip()
        git_dir = pathlib.Path(git_dir_value)
        if not git_dir.is_absolute():
            git_dir = linked / git_dir
        (git_dir.resolve() / "codex-thread.json").write_text(
            json.dumps({"version": 1, "ownerThreadId": thread_id}) + "\n"
        )
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT NOT NULL)")
            connection.execute("INSERT INTO threads (id, cwd) VALUES (?, ?)", (thread_id, str(linked.resolve())))
        rollout = codex_home / "sessions/2026/07/15/rollout-fixture.jsonl"
        rollout.parent.mkdir(parents=True)
        rollout.write_text(json.dumps({
            "type": "session_meta",
            "payload": {"id": thread_id, "cwd": str(linked.resolve())},
        }) + "\n")
        first_records = [
            {
                "kind": "worktree-mode-selected",
                "phase": "worktree-first",
                "selected": True,
                "uniqueControl": True,
            },
            {
                "kind": "worktree-first-summary",
                "phase": "worktree-first",
                "worktreeRootSaved": True,
                "worktreeModeSelected": True,
                "rendererPromptCompleted": True,
                "tasksSurfaceObserved": True,
            },
        ]
        cdp_path.write_text("\n".join(json.dumps(record) for record in first_records) + "\n")
        validate_first(repo, worktree_root, database, codex_home, state_path, cdp_path)
        first_state = json.loads(state_path.read_text())
        assert first_state["threadId"] == thread_id
        assert first_state["firstValidated"] is True

        cold_records = [
            *first_records,
            {
                "kind": "worktree-thread-reopened",
                "phase": "worktree-second",
                "reopened": True,
                "threadId": thread_id,
                "cwdSha256": first_state["worktreePathSha256"],
            },
            {
                "kind": "worktree-second-summary",
                "phase": "worktree-second",
                "rendererThreadReopened": True,
                "persistedOutputVisible": True,
                "rendererContinuationCompleted": True,
            },
        ]
        cdp_path.write_text("\n".join(json.dumps(record) for record in cold_records) + "\n")
        validate_cold(repo, worktree_root, database, codex_home, state_path, cdp_path)
        assert json.loads(state_path.read_text())["coldValidated"] is True

        cdp_path.write_text("\n".join(json.dumps(record) for record in [*cold_records, first_records[0]]) + "\n")
        expect_error(
            lambda: validate_cold(repo, worktree_root, database, codex_home, state_path, cdp_path),
            "expected exactly one worktree-mode-selected marker",
        )
        contradictory_mode = {
            **first_records[0],
            "selected": False,
        }
        cdp_path.write_text(
            "\n".join(json.dumps(record) for record in [*cold_records, contradictory_mode]) + "\n"
        )
        expect_error(
            lambda: validate_cold(repo, worktree_root, database, codex_home, state_path, cdp_path),
            "expected exactly one worktree-mode-selected marker",
        )
        contradictory_summary = {
            **cold_records[-1],
            "rendererContinuationCompleted": False,
        }
        cdp_path.write_text(
            "\n".join(json.dumps(record) for record in [*cold_records, contradictory_summary]) + "\n"
        )
        expect_error(
            lambda: validate_cold(repo, worktree_root, database, codex_home, state_path, cdp_path),
            "expected exactly one worktree-second-summary marker",
        )
        cdp_path.write_text("\n".join(json.dumps(record) for record in cold_records) + "\n")
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE threads SET cwd = ? WHERE id = ?", (str(repo.resolve()), thread_id))
        expect_error(
            lambda: validate_cold(repo, worktree_root, database, codex_home, state_path, cdp_path),
            "SQLite worktree cwd changed",
        )
        print("worktree state self-test passed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("baseline", "first", "cold"), nargs="?")
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--self-test", action="store_true")
    arguments = parser.parse_args()
    if arguments.self_test:
        run_self_tests()
        return
    expected_counts = {"baseline": 3, "first": 6, "cold": 6}
    if arguments.command is None or len(arguments.paths) != expected_counts[arguments.command]:
        parser.error(f"{arguments.command or 'command'} received the wrong number of paths")
    paths = [pathlib.Path(value) for value in arguments.paths]
    if arguments.command == "baseline":
        capture_baseline(paths[0], paths[1], paths[2])
    elif arguments.command == "first":
        validate_first(*paths)
    else:
        validate_cold(*paths)


if __name__ == "__main__":
    main()
