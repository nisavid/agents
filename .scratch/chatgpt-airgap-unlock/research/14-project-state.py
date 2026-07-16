#!/usr/bin/env python3
"""Validate exact project adoption through Codex's authoritative thread cwd."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def exact_threads(database: Path, fixture: str) -> list[str]:
    uri = f"file:{database}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=5) as connection:
        rows = connection.execute(
            "SELECT id FROM threads WHERE cwd = ? ORDER BY id", (fixture,)
        ).fetchall()
    return [str(row[0]) for row in rows]


def ready(database: Path) -> bool:
    try:
        uri = f"file:{database}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1) as connection:
            columns = connection.execute("PRAGMA table_info(threads)").fetchall()
        return any(str(column[1]) == "cwd" for column in columns)
    except sqlite3.Error:
        return False


def write_private(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def capture(database: Path, fixture: str, output: Path) -> None:
    fixture = str(Path(fixture).resolve(strict=True))
    rows = exact_threads(database, fixture)
    if rows:
        raise ValueError("nonce fixture already has an authoritative thread record")
    write_private(
        output,
        {
            "schema": 1,
            "fixtureSha256": digest(fixture),
            "databasePathSha256": digest(str(database.resolve(strict=True))),
            "exactThreadCount": 0,
        },
    )


def validate(database: Path, fixture: str, baseline_path: Path, output: Path) -> None:
    fixture = str(Path(fixture).resolve(strict=True))
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if baseline != {
        "schema": 1,
        "fixtureSha256": digest(fixture),
        "databasePathSha256": digest(str(database.resolve(strict=True))),
        "exactThreadCount": 0,
    }:
        raise ValueError("project baseline does not bind the exact database path and fixture")
    rows = exact_threads(database, fixture)
    if len(rows) != 1:
        raise ValueError(f"expected one authoritative fixture thread, found {len(rows)}")
    write_private(
        output,
        {
            "schema": 1,
            "fixtureSha256": digest(fixture),
            "databasePathSha256": digest(str(database.resolve(strict=True))),
            "exactThreadCountBefore": 0,
            "exactThreadCountAfter": 1,
            "threadId": rows[0],
            "transitionValidated": True,
        },
    )


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="chatgpt-native-project-state.") as temporary:
        root = Path(temporary)
        database = root / "state.sqlite"
        fixture = root / "project-nonce"
        fixture.mkdir()
        fixture_real = str(fixture.resolve())
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT NOT NULL)")
        baseline = root / "baseline.json"
        output = root / "result.json"
        capture(database, fixture_real, baseline)
        with sqlite3.connect(database) as connection:
            connection.execute("INSERT INTO threads VALUES (?, ?)", ("wrong", str(root / "other")))
        try:
            validate(database, fixture_real, baseline, output)
        except ValueError:
            pass
        else:
            raise AssertionError("wrong authoritative cwd passed")
        with sqlite3.connect(database) as connection:
            connection.execute("INSERT INTO threads VALUES (?, ?)", ("expected", fixture_real))
        validate(database, fixture_real, baseline, output)
        result = json.loads(output.read_text())
        assert result["transitionValidated"] is True
        with sqlite3.connect(database) as connection:
            connection.execute("INSERT INTO threads VALUES (?, ?)", ("duplicate", fixture_real))
        try:
            validate(database, fixture_real, baseline, output)
        except ValueError:
            pass
        else:
            raise AssertionError("duplicate authoritative cwd passed")
    print("native project state self-test passed")


def main(arguments: list[str]) -> None:
    if arguments == ["--self-test"]:
        self_test()
        return
    if len(arguments) == 2 and arguments[0] == "ready":
        raise SystemExit(0 if ready(Path(arguments[1])) else 1)
    if len(arguments) == 4 and arguments[0] == "capture":
        capture(Path(arguments[1]), arguments[2], Path(arguments[3]))
        return
    if len(arguments) == 5 and arguments[0] == "validate":
        validate(Path(arguments[1]), arguments[2], Path(arguments[3]), Path(arguments[4]))
        return
    raise SystemExit(
        "usage: 14-project-state.py capture DB FIXTURE OUTPUT | "
        "validate DB FIXTURE BASELINE OUTPUT | ready DB | --self-test"
    )


if __name__ == "__main__":
    main(sys.argv[1:])
