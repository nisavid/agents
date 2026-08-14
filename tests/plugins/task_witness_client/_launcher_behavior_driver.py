#!/usr/bin/env python3
from __future__ import annotations

import base64
import fcntl
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path


def _bytes(value: object) -> bytes:
    if not isinstance(value, dict) or set(value) != {"bytes_base64"}:
        raise TypeError("encoded bytes are required")
    encoded = value["bytes_base64"]
    if not isinstance(encoded, str):
        raise TypeError("encoded bytes must be text")
    return base64.b64decode(encoded, validate=True)


def _path(configuration: dict[str, object], name: str) -> Path:
    value = configuration[name]
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a path")
    return Path(value)


def _marker_gate(configuration: dict[str, object]) -> None:
    started = _path(configuration, "started")
    continuation = _path(configuration, "continuation")
    started.write_text("started", encoding="utf-8")
    deadline_seconds = configuration.get("deadline_seconds")
    deadline = (
        time.monotonic() + float(deadline_seconds)
        if deadline_seconds is not None
        else None
    )
    while not continuation.exists():
        if deadline is not None and time.monotonic() >= deadline:
            raise SystemExit(2)
        time.sleep(0.01)
    sys.stdout.buffer.write(_bytes(configuration["output"]))


def _expected_argv(configuration: dict[str, object]) -> None:
    expected = configuration["expected"]
    if not isinstance(expected, list) or not all(
        isinstance(argument, str) for argument in expected
    ):
        raise TypeError("expected must be a string list")
    if sys.argv[1:] != expected:
        sys.stderr.write("launcher argument mismatch")
        raise SystemExit(2)
    sys.stdout.buffer.write(_bytes(configuration["output"]))


def _fixed_profile(configuration: dict[str, object]) -> None:
    expected = configuration["expected"]
    sentinel_descriptor = configuration["sentinel_descriptor"]
    if not isinstance(sentinel_descriptor, int):
        raise TypeError("sentinel_descriptor must be an integer")
    environment = dict(os.environ)
    if sys.platform == "darwin":
        environment.pop("__CF_USER_TEXT_ENCODING", None)
    open_descriptors = []
    for descriptor in range(3, 64):
        try:
            fcntl.fcntl(descriptor, fcntl.F_GETFD)
        except OSError:
            continue
        open_descriptors.append(descriptor)
    try:
        fcntl.fcntl(sentinel_descriptor, fcntl.F_GETFD)
        sentinel_is_open = True
    except OSError:
        sentinel_is_open = False
    previous_umask = os.umask(0o077)
    os.umask(previous_umask)
    accepted = (
        sys.argv[1:] == expected
        and environment
        == {
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
        }
        and os.getcwd() == "/"
        and sys.stdin.buffer.read(1) == b""
        and open_descriptors == []
        and not sentinel_is_open
        and os.getsid(0) == os.getpid()
        and signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
        and signal.pthread_sigmask(signal.SIG_BLOCK, set()) == set()
        and previous_umask == 0o077
        and sys.flags.dont_write_bytecode == 1
        and sys.flags.isolated == 1
        and sys.flags.no_site == 1
        and sys._xoptions == {"disable-remote-debug": True}
    )
    if not accepted:
        sys.stderr.write("fixed process profile mismatch")
        raise SystemExit(2)
    sys.stdout.buffer.write(_bytes(configuration["output"]))


def _canonical_padding(configuration: dict[str, object]) -> None:
    envelope = configuration["envelope"]
    padding_length = configuration["padding_length"]
    if not isinstance(envelope, dict) or not isinstance(padding_length, int):
        raise TypeError("canonical padding configuration is invalid")
    witness = envelope["witness"]
    if not isinstance(witness, dict):
        raise TypeError("witness must be an object")
    projection = witness["projection"]
    if not isinstance(projection, dict):
        raise TypeError("projection must be an object")
    projection["padding"] = "x" * padding_length
    raw = (
        json.dumps(
            envelope,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    sys.stdout.buffer.write(raw)


def _simultaneous_streams() -> None:
    def write_stderr() -> None:
        for _ in range(2):
            os.write(2, b"e" * (64 * 1024))

    thread = threading.Thread(target=write_stderr)
    thread.start()
    for _ in range(8):
        os.write(1, b"o" * (64 * 1024))
    thread.join()


def _emit_output(configuration: dict[str, object]) -> None:
    sys.stdout.buffer.write(_bytes(configuration["output"]))
    stderr = configuration.get("stderr")
    if stderr is not None:
        sys.stderr.buffer.write(_bytes(stderr))
    exit_code = configuration.get("exit_code", 0)
    if not isinstance(exit_code, int):
        raise TypeError("exit_code must be an integer")
    raise SystemExit(exit_code)


def _marker_output(configuration: dict[str, object]) -> None:
    marker = _path(configuration, "marker")
    marker.write_text(
        str(configuration.get("marker_content", "ran")),
        encoding="utf-8",
    )
    output = configuration.get("output")
    if output is not None:
        sys.stdout.buffer.write(_bytes(output))


def _counted_output(configuration: dict[str, object]) -> None:
    count = _path(configuration, "count")
    previous = int(count.read_text()) if count.exists() else 0
    count.write_text(str(previous + 1), encoding="utf-8")
    sys.stdout.buffer.write(_bytes(configuration["output"]))


def _unbounded_stream(configuration: dict[str, object]) -> None:
    descriptor = configuration["descriptor"]
    if descriptor not in (1, 2):
        raise TypeError("descriptor must be stdout or stderr")
    prefix = configuration.get("prefix")
    if prefix is not None:
        os.write(descriptor, _bytes(prefix))
    chunk = b"x" * (64 * 1024)
    while True:
        os.write(descriptor, chunk)


def _blocking() -> None:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        time.sleep(1)


def _inherited_open_pipes(configuration: dict[str, object]) -> None:
    if os.fork() == 0:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(30)
        os._exit(0)
    sys.stdout.buffer.write(_bytes(configuration["output"]))
    sys.stdout.buffer.flush()
    os._exit(0)


def _terminal_descendant(configuration: dict[str, object]) -> None:
    pid_path = _path(configuration, "pid_path")
    if os.fork() == 0:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        os.close(1)
        os.close(2)
        pid_path.write_text(str(os.getpid()), encoding="utf-8")
        time.sleep(30)
        os._exit(0)
    deadline = time.monotonic() + 3
    while not pid_path.exists():
        if time.monotonic() >= deadline:
            os._exit(2)
        time.sleep(0.01)
    os.write(1, _bytes(configuration["output"]))
    exit_code = configuration["exit_code"]
    if not isinstance(exit_code, int):
        raise TypeError("exit_code must be an integer")
    os._exit(exit_code)


def _counted_blocking(configuration: dict[str, object]) -> None:
    count_value = configuration.get("count")
    count = Path(count_value) if isinstance(count_value, str) else None
    pid = _path(configuration, "pid")
    if count is not None:
        previous = int(count.read_text()) if count.exists() else 0
        count.write_text(str(previous + 1), encoding="utf-8")
    pid.write_text(str(os.getpid()), encoding="utf-8")
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if configuration.get("close_streams"):
        os.close(1)
        os.close(2)
    while True:
        time.sleep(1)


def _aba_restore(configuration: dict[str, object]) -> None:
    launcher = _path(configuration, "launcher")
    started = _path(configuration, "started")
    started.write_text("started", encoding="utf-8")
    if configuration["restore_mode"] == "rename":
        os.replace(_path(configuration, "backup"), launcher)
    else:
        launcher.chmod(0o700)
        launcher.write_bytes(_bytes(configuration["original"]))
        launcher.chmod(0o500)
        times = configuration["times"]
        if (
            not isinstance(times, list)
            or len(times) != 2
            or not all(isinstance(value, int) for value in times)
        ):
            raise TypeError("times must contain atime and mtime nanoseconds")
        os.utime(launcher, ns=(times[0], times[1]))
    sys.stdout.buffer.write(_bytes(configuration["output"]))


def _run_configured_launcher(configuration: dict[str, object]) -> None:
    mode = configuration["mode"]
    if mode == "marker_gate":
        _marker_gate(configuration)
    elif mode == "expected_argv":
        _expected_argv(configuration)
    elif mode == "fixed_profile":
        _fixed_profile(configuration)
    elif mode == "canonical_padding":
        _canonical_padding(configuration)
    elif mode == "simultaneous_streams":
        _simultaneous_streams()
    elif mode == "emit_output":
        _emit_output(configuration)
    elif mode == "marker_output":
        _marker_output(configuration)
    elif mode == "counted_output":
        _counted_output(configuration)
    elif mode == "unbounded_stream":
        _unbounded_stream(configuration)
    elif mode == "blocking":
        _blocking()
    elif mode == "inherited_open_pipes":
        _inherited_open_pipes(configuration)
    elif mode == "terminal_descendant":
        _terminal_descendant(configuration)
    elif mode == "counted_blocking":
        _counted_blocking(configuration)
    elif mode == "aba_restore":
        _aba_restore(configuration)
    else:
        raise ValueError(f"unknown launcher fixture mode: {mode}")
