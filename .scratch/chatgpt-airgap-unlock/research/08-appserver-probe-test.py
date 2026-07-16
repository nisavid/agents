#!/usr/bin/env python3
"""Focused regressions for the Ticket 08 app-server probe transports."""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import subprocess
import sys
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


def main() -> None:
    for name, filename in (
        ("ticket08_app_probe", "08-appserver-probe.py"),
        ("ticket08_restart_probe", "08-appserver-restart-probe.py"),
    ):
        module = load(name, filename)
        test_unconditional_kill_fallback(module)
        test_binary_jsonl_receive(module)
    print("app-server cleanup and binary JSONL regressions passed")


if __name__ == "__main__":
    main()
