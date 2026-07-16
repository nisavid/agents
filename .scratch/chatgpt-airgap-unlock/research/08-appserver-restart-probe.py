#!/usr/bin/env python3
"""THROWAWAY PROTOTYPE ONLY: verify a completed thread in a cold host process."""

from __future__ import annotations

import json
import os
import pathlib
import select
import subprocess
import sys
import time
from typing import Any


def sandbox_command(
    profile: pathlib.Path,
    real_home: pathlib.Path,
    codex: pathlib.Path,
    protected_helper: pathlib.Path | None,
) -> list[str]:
    command = [
        "/usr/bin/sandbox-exec",
        "-f",
        str(profile),
        "-D",
        f"REAL_HOME={real_home}",
    ]
    if protected_helper is not None:
        command.extend(["-D", f"NATIVE_GUI_PROBE_BIN={protected_helper}"])
    command.extend(
        [
            str(codex),
            "-c",
            "features.code_mode_host=true",
            "app-server",
            "--stdio",
        ]
    )
    return command


def run_self_test() -> None:
    profile = pathlib.Path("/private/tmp/profile.sb")
    real_home = pathlib.Path("/Users/operator")
    codex = pathlib.Path("/private/tmp/Codex")
    without_helper = sandbox_command(profile, real_home, codex, None)
    assert not any(value.startswith("NATIVE_GUI_PROBE_BIN=") for value in without_helper)
    helper = pathlib.Path("/private/tmp/reviewed-helper")
    with_helper = sandbox_command(profile, real_home, codex, helper)
    assert with_helper.count("-D") == 2
    helper_define = f"NATIVE_GUI_PROBE_BIN={helper}"
    helper_index = with_helper.index(helper_define)
    assert with_helper[helper_index - 1] == "-D"
    assert with_helper[helper_index + 1] == str(codex)
    print("cold host app-server sandbox command self-test passed")


def stop_process(process: subprocess.Popen[bytes]) -> None:
    """Close and stop a host process even when the probe fails."""
    if process.stdin is not None:
        try:
            process.stdin.close()
        except OSError:
            pass
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def receive_message(
    stream: Any,
    raw: Any,
    messages: list[dict[str, Any]],
    deadline: float,
) -> dict[str, Any]:
    """Read one JSONL message from a binary, select-polled stream."""
    ready, _, _ = select.select([stream], [], [], max(0.0, deadline - time.monotonic()))
    if not ready:
        raise TimeoutError("cold host response timed out")
    line = stream.readline()
    if not line:
        raise RuntimeError("cold host closed stdout")
    message = json.loads(line.decode("utf-8"))
    messages.append(message)
    raw.write(json.dumps(message, sort_keys=True) + "\n")
    raw.flush()
    return message


def agent_texts(value: Any) -> list[str]:
    texts: list[str] = []
    if isinstance(value, dict):
        if value.get("type") == "agentMessage":
            if isinstance(value.get("text"), str):
                texts.append(value["text"])
            for content in value.get("content", []):
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    texts.append(content["text"])
        for child in value.values():
            texts.extend(agent_texts(child))
    elif isinstance(value, list):
        for child in value:
            texts.extend(agent_texts(child))
    return texts


def main() -> None:
    if len(sys.argv) not in (8, 9):
        raise SystemExit(
            "usage: 08-appserver-restart-probe.py CODEX WORKSPACE PROFILE REAL_HOME "
            "LOG_DIR THREAD_ID PROXY_PORT [PROTECTED_HELPER]"
        )
    codex = pathlib.Path(sys.argv[1]).resolve()
    workspace = pathlib.Path(sys.argv[2]).resolve()
    profile = pathlib.Path(sys.argv[3]).resolve()
    real_home = pathlib.Path(sys.argv[4]).resolve()
    log_dir = pathlib.Path(sys.argv[5]).resolve()
    thread_id = sys.argv[6]
    proxy_port = int(sys.argv[7])
    protected_helper = pathlib.Path(sys.argv[8]).resolve() if len(sys.argv) == 9 else None
    token_env_name = os.environ["CODEX_PROVIDER_TOKEN_ENV"]
    environment = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": os.environ["HOME"],
        "CFFIXED_USER_HOME": os.environ["HOME"],
        "CODEX_HOME": os.environ["CODEX_HOME"],
        "TMPDIR": os.environ["TMPDIR"],
        "USER": "offline-probe",
        "LOGNAME": "offline-probe",
        "SHELL": "/bin/sh",
        "LANG": "en_US.UTF-8",
        "HTTP_PROXY": f"http://127.0.0.1:{proxy_port}",
        "HTTPS_PROXY": f"http://127.0.0.1:{proxy_port}",
        "ALL_PROXY": f"http://127.0.0.1:{proxy_port}",
        "NO_PROXY": "127.0.0.1,localhost",
        "http_proxy": f"http://127.0.0.1:{proxy_port}",
        "https_proxy": f"http://127.0.0.1:{proxy_port}",
        "all_proxy": f"http://127.0.0.1:{proxy_port}",
        "no_proxy": "127.0.0.1,localhost",
        "RUST_LOG": "info",
    }
    environment[token_env_name] = os.environ[token_env_name]
    messages: list[dict[str, Any]] = []
    request_id = 0
    with (log_dir / "host-restart.stderr").open("w") as stderr, (
        log_dir / "host-restart-messages.jsonl"
    ).open("w") as raw:
        process = subprocess.Popen(
            sandbox_command(profile, real_home, codex, protected_helper),
            cwd=workspace,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            bufsize=0,
        )
        try:
            assert process.stdin is not None and process.stdout is not None

            def receive(deadline: float) -> dict[str, Any]:
                return receive_message(process.stdout, raw, messages, deadline)

            def request(method: str, params: dict[str, Any]) -> dict[str, Any]:
                nonlocal request_id
                request_id += 1
                wanted = request_id
                process.stdin.write(
                    (json.dumps({"id": wanted, "method": method, "params": params}) + "\n").encode(
                        "utf-8"
                    )
                )
                process.stdin.flush()
                deadline = time.monotonic() + 20
                while True:
                    message = receive(deadline)
                    if message.get("id") == wanted:
                        if "error" in message:
                            raise RuntimeError(f"{method} failed: {message['error']}")
                        return message

            request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "offline-route-restart-prototype",
                        "title": "Offline route restart prototype",
                        "version": "1",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            )
            account = request("account/read", {"refreshToken": False})
            listed = request("thread/list", {"cwd": str(workspace), "limit": 20})
            read = request("thread/read", {"threadId": thread_id, "includeTurns": True})
            resumed = request(
                "thread/resume",
                {
                    "threadId": thread_id,
                    "cwd": str(workspace),
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "runtimeWorkspaceRoots": [str(workspace)],
                },
            )
        finally:
            stop_process(process)

    persisted = agent_texts(read)
    assertions = {
        "account_requires_openai_auth_false": account["result"].get(
            "requiresOpenaiAuth"
        )
        is False,
        "thread_listed_after_cold_restart": thread_id in json.dumps(listed),
        "thread_read_after_cold_restart": "LOCAL_APP_OK" in persisted,
        "thread_resumed_after_cold_restart": thread_id in json.dumps(resumed),
    }
    summary = {
        "thread_id": thread_id,
        "returncode": process.returncode,
        "persisted_agent_texts": persisted,
        "assertions": assertions,
    }
    (log_dir / "host-restart-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary, sort_keys=True))
    failed = [name for name, passed in assertions.items() if not passed]
    if failed:
        raise AssertionError(f"cold host assertions failed: {', '.join(failed)}")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        run_self_test()
    else:
        main()
