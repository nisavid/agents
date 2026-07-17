#!/usr/bin/env python3
"""Deterministic Seatbelt contract tests for the ticket 08 role profiles."""

from __future__ import annotations

import contextlib
import socket
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
PYTHON = "/usr/bin/python3"
PINNED_RUNTIME = (
    Path.home() / ".local/share/uv/python/cpython-3.12.13-macos-aarch64-none"
)
PINNED_PYTHON = PINNED_RUNTIME / "bin/python3.12"
PINNED_SITE_PACKAGES = Path("/private/tmp/chatgpt-optiq-smoke/.venv/lib/python3.12/site-packages")
PINNED_MODEL = Path.home() / (
    ".cache/huggingface/hub/models--mlx-community--Qwen3.5-2B-OptiQ-4bit/"
    "snapshots/adc8669eb431e3168aeb4e320bd7b757914350e2"
)


def sandbox(profile: str, definitions: dict[str, str], *command: str) -> subprocess.CompletedProcess[str]:
    arguments = ["/usr/bin/sandbox-exec", "-f", str(HERE / profile)]
    for name, value in definitions.items():
        arguments.extend(["-D", f"{name}={value}"])
    arguments.extend(command)
    return subprocess.run(arguments, text=True, capture_output=True, check=False)


def unsandboxed(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def assert_allowed(result: subprocess.CompletedProcess[str], subject: str) -> None:
    assert result.returncode == 0, (
        f"{subject} was denied: stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def assert_denied(result: subprocess.CompletedProcess[str], subject: str) -> None:
    assert result.returncode != 0, f"{subject} unexpectedly succeeded"


def assert_denied_after_control(
    profile: str, definitions: dict[str, str], subject: str, *command: str
) -> None:
    assert_allowed(unsandboxed(*command), f"{subject} unsandboxed control")
    assert_denied(sandbox(profile, definitions, *command), subject)


def bind_code(port: int) -> str:
    return (
        "import socket; "
        "sock = socket.socket(); "
        f"sock.bind(('localhost', {port})); "
        "sock.listen(1); "
        "sock.close()"
    )


def connect_code(port: int) -> str:
    return (
        "import socket; "
        "sock = socket.socket(); "
        "sock.settimeout(1); "
        f"sock.connect(('127.0.0.1', {port})); "
        "sock.close()"
    )


@contextlib.contextmanager
def listener(port: int):
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(1)
    try:
        yield
    finally:
        server.close()


def assert_bind(profile: str, definitions: dict[str, str], port: int, allowed: bool) -> None:
    if not allowed:
        assert_allowed(unsandboxed(PYTHON, "-c", bind_code(port)), f"{profile} bind {port} control")
    result = sandbox(profile, definitions, PYTHON, "-c", bind_code(port))
    assertion = assert_allowed if allowed else assert_denied
    assertion(result, f"{profile} bind {port}")


def assert_connect(profile: str, definitions: dict[str, str], port: int, allowed: bool) -> None:
    with listener(port):
        if not allowed:
            assert_allowed(
                unsandboxed(PYTHON, "-c", connect_code(port)),
                f"{profile} connect {port} control",
            )
        result = sandbox(profile, definitions, PYTHON, "-c", connect_code(port))
    assertion = assert_allowed if allowed else assert_denied
    assertion(result, f"{profile} connect {port}")


def test_ports() -> None:
    ports = {
        "CDP_PORT": "49308",
        "PROXY_PORT": "49309",
        "UPSTREAM_OBSERVER_PORT": "18997",
        "OPTIQ_PORT": "18998",
        "GATEWAY_PORT": "18999",
    }
    real_home = "/private/tmp/ticket08-seatbelt-home"
    common = {
        "REAL_HOME": real_home,
        "NATIVE_GUI_PROBE_BIN": "/private/tmp/ticket08-seatbelt-helper",
        **ports,
    }
    roles = (
        ("08-app-direct.sb", common, ((49308, True),), ((49309, True), (18998, True), (18999, False))),
        ("08-app-gateway.sb", common, ((49308, True),), ((49309, True), (18998, False), (18999, True))),
        ("08-host-direct.sb", common, ((49308, False),), ((49309, True), (18998, True), (18999, False))),
        ("08-host-gateway.sb", common, ((49308, False),), ((49309, True), (18998, False), (18999, True))),
        ("08-proxy.sb", common, ((49309, True),), ((49308, False),)),
        ("08-upstream-observer.sb", common, ((18997, True),), ((18998, True),)),
        ("08-gateway.sb", common, ((18999, True),), ((18997, True),)),
        ("08-cdp-client.sb", common, ((49308, False),), ((49308, True),)),
        ("08-metadata-probe.sb", common, ((49308, False),), ((49308, False),)),
        ("08-namespace-probe.sb", common, ((49308, False),), ((49308, False),)),
    )
    for profile, definitions, bind_cases, connect_cases in roles:
        for port, allowed in bind_cases:
            assert_bind(profile, definitions, port, allowed)
        for port, allowed in connect_cases:
            assert_connect(profile, definitions, port, allowed)

    non_default_ports = {
        "CDP_PORT": "29408",
        "PROXY_PORT": "29409",
        "UPSTREAM_OBSERVER_PORT": "28997",
        "OPTIQ_PORT": "28998",
        "GATEWAY_PORT": "28999",
    }
    non_default_common = {**common, **non_default_ports}
    assert_connect("08-host-direct.sb", non_default_common, 29409, True)
    assert_connect("08-host-direct.sb", non_default_common, 28998, True)
    assert_connect("08-host-direct.sb", non_default_common, 49309, False)
    assert_connect("08-host-gateway.sb", non_default_common, 29409, True)
    assert_connect("08-host-gateway.sb", non_default_common, 28999, True)
    assert_connect("08-host-gateway.sb", non_default_common, 18999, False)
    assert_bind("08-proxy.sb", non_default_common, 29409, True)
    assert_bind("08-proxy.sb", non_default_common, 49309, False)
    assert_bind("08-gateway.sb", non_default_common, 28999, True)
    assert_connect("08-gateway.sb", non_default_common, 28997, True)
    assert_bind("08-gateway.sb", non_default_common, 18999, False)
    assert_bind("08-app-direct.sb", non_default_common, 29408, True)
    assert_connect("08-app-direct.sb", non_default_common, 29409, True)
    assert_connect("08-app-direct.sb", non_default_common, 28998, True)
    assert_bind("08-app-direct.sb", non_default_common, 49308, False)
    assert_bind("08-app-gateway.sb", non_default_common, 29408, True)
    assert_connect("08-app-gateway.sb", non_default_common, 29409, True)
    assert_connect("08-app-gateway.sb", non_default_common, 28999, True)
    assert_connect("08-app-gateway.sb", non_default_common, 18999, False)
    assert_bind("08-upstream-observer.sb", non_default_common, 28997, True)
    assert_connect("08-upstream-observer.sb", non_default_common, 28998, True)
    assert_bind("08-upstream-observer.sb", non_default_common, 18997, False)
    assert_connect("08-cdp-client.sb", non_default_common, 29408, True)
    assert_connect("08-cdp-client.sb", non_default_common, 49308, False)

    provider_definitions = {
        "REAL_HOME": real_home,
        "MODEL_REPO": str(PINNED_MODEL.parent.parent),
        "OPTIQ_RUNTIME": str(PINNED_RUNTIME),
        "OPTIQ_SITE_PACKAGES": str(PINNED_SITE_PACKAGES),
        "RUN_ROOT": "/private/tmp/ticket08-seatbelt-run",
        "PROVIDER_HOME": "/private/tmp/ticket08-seatbelt-run/provider-home",
        "PROVIDER_TMP": "/private/tmp/ticket08-seatbelt-run/provider-tmp",
        "HF_CACHE": "/private/tmp/ticket08-seatbelt-run/hf-cache",
        **ports,
    }
    provider_python = str(PINNED_PYTHON)
    assert_allowed(
        sandbox("08-provider.sb", provider_definitions, provider_python, "-P", "-c", bind_code(18998)),
        "08-provider.sb bind 18998",
    )
    non_default_provider = {**provider_definitions, **non_default_ports}
    assert_allowed(
        sandbox(
            "08-provider.sb",
            non_default_provider,
            provider_python,
            "-P",
            "-c",
            bind_code(28998),
        ),
        "08-provider.sb bind non-default OptiQ port",
    )
    assert_denied_after_control(
        "08-provider.sb",
        non_default_provider,
        "08-provider.sb bind fixed default OptiQ port",
        provider_python,
        "-P",
        "-c",
        bind_code(18998),
    )
    with listener(49308):
        assert_denied(
            sandbox(
                "08-provider.sb",
                provider_definitions,
                provider_python,
                "-P",
                "-c",
                connect_code(49308),
            ),
            "08-provider.sb connect 49308",
        )
    assert_denied(
        sandbox(
            "08-provider.sb",
            provider_definitions,
            provider_python,
            "-P",
            "-c",
            bind_code(18999),
        ),
        "08-provider.sb bind 18999",
    )

    for profile, definitions, denied_port in (
        ("08-app-direct.sb", common, 18997),
        ("08-app-gateway.sb", common, 18997),
        ("08-host-direct.sb", common, 18997),
        ("08-host-gateway.sb", common, 18997),
        ("08-proxy.sb", common, 18997),
        ("08-upstream-observer.sb", common, 18999),
        ("08-gateway.sb", common, 18998),
        ("08-cdp-client.sb", common, 49309),
        ("08-metadata-probe.sb", common, 49309),
        ("08-namespace-probe.sb", common, 49309),
    ):
        assert_bind(profile, definitions, denied_port, False)
        assert_connect(profile, definitions, denied_port, False)


def test_provider_files_and_exec() -> None:
    with tempfile.TemporaryDirectory(prefix="ticket08-seatbelt-") as temporary:
        root = Path(temporary).resolve()
        real_home = Path.home()
        model = PINNED_MODEL
        run_root = root / "run"
        provider_home = run_root / "provider-home"
        provider_tmp = run_root / "provider-tmp"
        hf_cache = run_root / "hf-cache"
        for directory in (provider_home, provider_tmp, hf_cache):
            directory.mkdir(parents=True)
        (run_root / "protected.txt").write_text("protected\n")
        definitions = {
            "REAL_HOME": str(real_home),
            "MODEL_REPO": str(model.parent.parent),
            "OPTIQ_RUNTIME": str(PINNED_RUNTIME),
            "OPTIQ_SITE_PACKAGES": str(PINNED_SITE_PACKAGES),
            "RUN_ROOT": str(run_root),
            "PROVIDER_HOME": str(provider_home),
            "PROVIDER_TMP": str(provider_tmp),
            "HF_CACHE": str(hf_cache),
            "OPTIQ_PORT": "18998",
        }
        assert PINNED_PYTHON.is_file() and PINNED_MODEL.is_dir()
        provider_python = str(PINNED_PYTHON)
        read = "import pathlib; pathlib.Path(__import__('sys').argv[1]).read_bytes()"
        write = "import pathlib; pathlib.Path(__import__('sys').argv[1]).write_text('ok')"
        assert_allowed(
            sandbox("08-provider.sb", definitions, provider_python, "-P", "-c", read, str(model / "config.json")),
            "provider model read",
        )
        assert_allowed(
            sandbox("08-provider.sb", definitions, provider_python, "-P", "-c", read, provider_python),
            "provider runtime read",
        )
        synthetic_real_home = root / "real-home"
        synthetic_real_home.mkdir()
        home_sentinel = synthetic_real_home / "sentinel.txt"
        home_sentinel.write_text("known existing sentinel\n")
        home_definitions = {**definitions, "REAL_HOME": str(synthetic_real_home)}
        assert_denied_after_control(
            "08-provider.sb",
            home_definitions,
            "provider unrelated home read",
            provider_python,
            "-P",
            "-c",
            read,
            str(home_sentinel),
        )
        assert_denied_after_control(
            "08-provider.sb",
            definitions,
            "provider run-root read",
            provider_python,
            "-P",
            "-c",
            read,
            str(run_root / "protected.txt"),
        )
        for directory in (provider_home, provider_tmp, hf_cache):
            assert_allowed(
                sandbox("08-provider.sb", definitions, provider_python, "-P", "-c", write, str(directory / "allowed.txt")),
                f"provider write {directory.name}",
            )
        assert_denied_after_control(
            "08-provider.sb",
            definitions,
            "provider run-root write",
            provider_python,
            "-P",
            "-c",
            write,
            str(run_root / "denied.txt"),
        )
        assert_allowed(
            sandbox("08-provider.sb", definitions, provider_python, "-P", "-c", "pass"),
            "provider pinned runtime execution",
        )
        assert_denied_after_control(
            "08-provider.sb",
            definitions,
            "provider execution outside pinned runtime",
            "/bin/echo",
            "denied",
        )


def test_metadata_real_home_denial() -> None:
    with tempfile.TemporaryDirectory(prefix="ticket08-metadata-") as temporary:
        real_home = Path(temporary).resolve() / "real-home"
        real_home.mkdir()
        sentinel = real_home / "sentinel.txt"
        sentinel.write_text("known existing sentinel\n")
        denied_write = real_home / "denied-write.txt"
        read = "import pathlib; pathlib.Path(__import__('sys').argv[1]).read_bytes()"
        write = "import pathlib; pathlib.Path(__import__('sys').argv[1]).write_text('ok')"
        definitions = {"REAL_HOME": str(real_home)}
        assert_denied_after_control(
            "08-metadata-probe.sb", definitions, "metadata real-home read", PYTHON, "-c", read, str(sentinel)
        )
        assert_denied_after_control(
            "08-metadata-probe.sb", definitions, "metadata real-home write", PYTHON, "-c", write, str(denied_write)
        )


def main() -> int:
    if sys.flags.optimize:
        raise SystemExit("08-seatbelt-test.py requires assertions")
    test_ports()
    test_provider_files_and_exec()
    test_metadata_real_home_denial()
    print("ticket 08 Seatbelt role contracts passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
