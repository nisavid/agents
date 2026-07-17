#!/usr/bin/env python3
"""Focused regressions for the Ticket 08 app-server probe transports."""

from __future__ import annotations

import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


HERE = Path(__file__).resolve().parent


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeInput:
    def __init__(self, actions: list[str]) -> None:
        self.actions = actions

    def close(self) -> None:
        self.actions.append("stdin.close")


class FakeProcess:
    def __init__(self) -> None:
        self.actions: list[str] = []
        self.stdin = FakeInput(self.actions)
        self.wait_calls = 0

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        self.actions.append("terminate")

    def wait(self, timeout: float) -> None:
        self.wait_calls += 1
        if self.wait_calls == 1:
            raise subprocess.TimeoutExpired("fixture", timeout)
        self.actions.append("wait")

    def kill(self) -> None:
        self.actions.append("kill")


def test_unconditional_kill_fallback(module) -> None:
    process = FakeProcess()
    module.stop_process(process)
    assert process.actions == ["stdin.close", "terminate", "kill", "wait"]


def test_binary_jsonl_receive(module) -> None:
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'{\"id\":1}\\n{\"id\":2}\\n'); sys.stdout.buffer.flush()",
        ],
        stdout=subprocess.PIPE,
        bufsize=0,
    )
    assert child.stdout is not None
    raw = io.StringIO()
    messages: list[dict[str, object]] = []
    try:
        first = module.receive_message(child.stdout, raw, messages, time.monotonic() + 2)
        second = module.receive_message(child.stdout, raw, messages, time.monotonic() + 2)
    finally:
        child.kill()
        child.wait()
    assert [first["id"], second["id"]] == [1, 2]
    assert messages == [first, second]
    assert raw.getvalue() == '{"id": 1}\n{"id": 2}\n'


def test_ownership_witness_propagation(filename: str) -> None:
    source = (HERE / filename).read_text(encoding="utf-8")
    assert '"CHATGPT_ROUTE_RUN_MARKER": run_marker' in source
    assert '"CHATGPT_ROUTE_RUN_MARKER_FD": str(run_marker_fd)' in source
    assert "os.fstat(run_marker_fd)" in source
    assert "process = start_owned_process(" in source


def test_owned_process_fd_survives_environment_scrub(module) -> None:
    with tempfile.TemporaryDirectory(prefix="ticket08-owned-fd-") as temporary:
        root = Path(temporary)
        marker = root / "marker.txt"
        marker.write_text("witness\n", encoding="utf-8")
        marker_fd = os.open(marker, os.O_RDONLY)
        process = module.start_owned_process(
            [
                sys.executable,
                "-c",
                "import os; os.execve('/usr/bin/env', "
                "['/usr/bin/env', '-i', 'PATH=/usr/bin:/bin', '/bin/sleep', '5'], {})",
            ],
            cwd=root,
            environment={"PATH": "/usr/bin:/bin"},
            stderr=subprocess.DEVNULL,
            run_marker_fd=marker_fd,
        )
        os.close(marker_fd)
        try:
            holders: set[int] = set()
            for _ in range(50):
                result = subprocess.run(
                    ["/usr/sbin/lsof", "-t", "--", str(marker)],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=1.0,
                )
                holders = {
                    int(value) for value in result.stdout.splitlines() if value.isdigit()
                }
                if process.pid in holders:
                    break
                time.sleep(0.02)
            assert process.pid in holders
        finally:
            module.stop_process(process)


def main() -> None:
    for name, filename in (
        ("ticket08_app_probe", "08-appserver-probe.py"),
        ("ticket08_restart_probe", "08-appserver-restart-probe.py"),
    ):
        module = load(name, filename)
        test_unconditional_kill_fallback(module)
        test_binary_jsonl_receive(module)
        test_ownership_witness_propagation(filename)
        test_owned_process_fd_survives_environment_scrub(module)
    print("app-server cleanup and binary JSONL regressions passed")


if __name__ == "__main__":
    main()
