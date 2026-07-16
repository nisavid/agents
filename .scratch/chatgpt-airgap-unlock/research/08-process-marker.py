#!/usr/bin/env python3
"""Filter a process snapshot for the ticket 08 inherited ownership marker."""

from __future__ import annotations

import re
import sys
from pathlib import Path


MARKER_NAME = "CHATGPT_ROUTE_RUN_MARKER"
MARKER_PATTERN = re.compile(r"^chatgpt-route-ownership-[0-9a-f]{32}$")


def matching_pids(snapshot: str, marker: str, excluded_pids: set[int]) -> list[int]:
    if not MARKER_PATTERN.fullmatch(marker):
        raise ValueError("invalid route ownership marker")
    token = re.compile(
        rf"(?:^| ){re.escape(MARKER_NAME)}={re.escape(marker)}(?: |$)"
    )
    matches: list[int] = []
    for line in snapshot.splitlines():
        fields = line.strip().split(maxsplit=1)
        if len(fields) != 2 or not fields[0].isdigit():
            continue
        pid = int(fields[0])
        if pid not in excluded_pids and token.search(fields[1]):
            matches.append(pid)
    return sorted(set(matches))


def run_self_test() -> None:
    marker = "chatgpt-route-ownership-0123456789abcdef0123456789abcdef"
    snapshot = "\n".join(
        (
            "100 /bin/child CHATGPT_ROUTE_RUN_MARKER=" + marker + " HOME=/tmp",
            "101 /bin/unrelated CHATGPT_ROUTE_RUN_MARKER=other",
            "102 /bin/argument --label=CHATGPT_ROUTE_RUN_MARKER=" + marker,
            "103 /bin/reparented HOME=/tmp CHATGPT_ROUTE_RUN_MARKER=" + marker,
            "104 /bin/excluded CHATGPT_ROUTE_RUN_MARKER=" + marker,
        )
    )
    assert matching_pids(snapshot, marker, {104}) == [100, 103]
    try:
        matching_pids(snapshot, "unsafe marker", set())
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe marker was accepted")
    print("process ownership marker self-test passed")


def audit_runner(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    missing: list[int] = []
    for index, line in enumerate(lines):
        if "/usr/bin/env -i" not in line or "PATH=" in line:
            continue
        if index + 1 >= len(lines) or MARKER_NAME not in lines[index + 1]:
            missing.append(index + 1)
    if missing:
        raise AssertionError(f"isolated environment omitted marker after lines {missing}")
    if not lines or not any(MARKER_NAME in line for line in lines):
        raise AssertionError("runner has no ownership marker")


def main() -> int:
    if sys.flags.optimize:
        raise SystemExit("08-process-marker.py requires assertions; rerun without -O")
    if sys.argv[1:] == ["--self-test"]:
        run_self_test()
        return 0
    if len(sys.argv) == 3 and sys.argv[1] == "--audit-runner":
        audit_runner(Path(sys.argv[2]))
        print("process ownership marker propagation audit passed")
        return 0
    if len(sys.argv) < 4 or sys.argv[1] != "filter":
        raise SystemExit("usage: 08-process-marker.py filter MARKER_FILE EXCLUDED_PID...")
    marker_file = Path(sys.argv[2])
    marker = marker_file.read_text(encoding="utf-8").strip()
    excluded = {int(value) for value in sys.argv[3:]}
    for pid in matching_pids(sys.stdin.read(), marker, excluded):
        print(pid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
