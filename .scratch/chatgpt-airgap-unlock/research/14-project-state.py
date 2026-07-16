#!/usr/bin/env python3
"""Validate exact project adoption through Codex's authoritative thread cwd."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import sys
import tempfile
from pathlib import Path


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def database_binding(database: Path) -> dict[str, object]:
    resolved = database.resolve(strict=True)
    metadata = resolved.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("authoritative database must be a regular file")
    if metadata.st_nlink != 1:
        raise ValueError("authoritative database must have exactly one link")
    birthtime_nanoseconds = getattr(metadata, "st_birthtime_ns", None)
    identity = {
        "path": str(resolved),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "birthtimeNanoseconds": birthtime_nanoseconds,
    }
    return {
        "databasePathSha256": digest(str(resolved)),
        "databaseFileIdentitySha256": digest(
            json.dumps(identity, sort_keys=True, separators=(",", ":"))
        ),
    }


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
    binding = database_binding(database)
    rows = exact_threads(database, fixture)
    if rows:
        raise ValueError("nonce fixture already has an authoritative thread record")
    if database_binding(database) != binding:
        raise ValueError("authoritative database file identity changed during capture")
    write_private(
        output,
        {
            "schema": 2,
            "fixtureSha256": digest(fixture),
            **binding,
            "exactThreadCount": 0,
        },
    )


def validate(database: Path, fixture: str, baseline_path: Path, output: Path) -> None:
    fixture = str(Path(fixture).resolve(strict=True))
    binding = database_binding(database)
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if baseline != {
        "schema": 2,
        "fixtureSha256": digest(fixture),
        **binding,
        "exactThreadCount": 0,
    }:
        raise ValueError("project baseline does not bind the exact database file and fixture")
    rows = exact_threads(database, fixture)
    if len(rows) != 1:
        raise ValueError(f"expected one authoritative fixture thread, found {len(rows)}")
    if database_binding(database) != binding:
        raise ValueError("authoritative database file identity changed during validation")
    write_private(
        output,
        {
            "schema": 2,
            "fixtureSha256": digest(fixture),
            **binding,
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
        alias = root / "state-alias.sqlite"
        os.link(database, alias)
        try:
            database_binding(database)
        except ValueError:
            pass
        else:
            raise AssertionError("linked authoritative database passed")
        alias.unlink()
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
        assert result["databasePathSha256"] == digest(str(database.resolve()))
        assert result["databaseFileIdentitySha256"] == database_binding(database)[
            "databaseFileIdentitySha256"
        ]
        with sqlite3.connect(database) as connection:
            connection.execute("INSERT INTO threads VALUES (?, ?)", ("duplicate", fixture_real))
        try:
            validate(database, fixture_real, baseline, output)
        except ValueError:
            pass
        else:
            raise AssertionError("duplicate authoritative cwd passed")
        replacement = root / "replacement.sqlite"
        with sqlite3.connect(replacement) as connection:
            connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT NOT NULL)")
            connection.execute("INSERT INTO threads VALUES (?, ?)", ("expected", fixture_real))
        os.replace(replacement, database)
        replacement_output = root / "replacement-result.json"
        try:
            validate(database, fixture_real, baseline, replacement_output)
        except ValueError:
            pass
        else:
            raise AssertionError("same-path database replacement passed")
        assert not replacement_output.exists()
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
