#!/usr/bin/env python3
"""THROWAWAY PROTOTYPE ONLY: drive the packaged host through one local turn."""

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
    proxy_port: int,
    optiq_port: int,
    gateway_port: int,
    protected_helper: pathlib.Path | None,
) -> list[str]:
    command = [
        "/usr/bin/sandbox-exec",
        "-f",
        str(profile),
        "-D",
        f"REAL_HOME={real_home}",
        "-D",
        f"PROXY_PORT={proxy_port}",
        "-D",
        f"OPTIQ_PORT={optiq_port}",
        "-D",
        f"GATEWAY_PORT={gateway_port}",
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
    without_helper = sandbox_command(profile, real_home, codex, 49309, 18998, 18999, None)
    assert not any(value.startswith("NATIVE_GUI_PROBE_BIN=") for value in without_helper)
    assert without_helper.count("-D") == 4
    helper = pathlib.Path("/private/tmp/reviewed-helper")
    with_helper = sandbox_command(profile, real_home, codex, 49309, 18998, 18999, helper)
    assert with_helper.count("-D") == 5
    helper_define = f"NATIVE_GUI_PROBE_BIN={helper}"
    helper_index = with_helper.index(helper_define)
    assert with_helper[helper_index - 1] == "-D"
    assert with_helper[helper_index + 1] == str(codex)
    print("host app-server sandbox command self-test passed")


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
    while time.monotonic() < deadline:
        ready, _, _ = select.select([stream], [], [], max(0.0, deadline - time.monotonic()))
        if not ready:
            break
        line = stream.readline()
        if not line:
            break
        message = json.loads(line.decode("utf-8"))
        messages.append(message)
        raw.write(json.dumps(message, sort_keys=True) + "\n")
        raw.flush()
        return message
    raise TimeoutError("timed out waiting for app-server message")


def start_owned_process(
    command: list[str],
    *,
    cwd: pathlib.Path,
    environment: dict[str, str],
    stderr: Any,
    run_marker_fd: int,
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=stderr,
        bufsize=0,
        pass_fds=(run_marker_fd,),
    )


def main() -> None:
    if len(sys.argv) not in (10, 11):
        raise SystemExit(
            "usage: 08-appserver-probe.py CODEX WORKSPACE PROFILE REAL_HOME "
            "LOG_DIR MODEL_ID PROXY_PORT OPTIQ_PORT GATEWAY_PORT [PROTECTED_HELPER]"
        )
    codex = pathlib.Path(sys.argv[1]).resolve()
    workspace = pathlib.Path(sys.argv[2]).resolve()
    profile = pathlib.Path(sys.argv[3]).resolve()
    real_home = pathlib.Path(sys.argv[4]).resolve()
    log_dir = pathlib.Path(sys.argv[5]).resolve()
    model_id = sys.argv[6]
    proxy_port = int(sys.argv[7])
    optiq_port = int(sys.argv[8])
    gateway_port = int(sys.argv[9])
    protected_helper = pathlib.Path(sys.argv[10]).resolve() if len(sys.argv) == 11 else None
    token_env_name = os.environ["CODEX_PROVIDER_TOKEN_ENV"]
    run_marker = os.environ["CHATGPT_ROUTE_RUN_MARKER"]
    run_marker_fd = int(os.environ["CHATGPT_ROUTE_RUN_MARKER_FD"])
    os.fstat(run_marker_fd)
    skill_path = workspace / ".agents/skills/local-sentinel/SKILL.md"

    environment = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": os.environ["HOME"],
        "CFFIXED_USER_HOME": os.environ["HOME"],
        "XDG_CONFIG_HOME": os.environ["HOME"] + "/.config",
        "XDG_CACHE_HOME": os.environ["HOME"] + "/.cache",
        "XDG_DATA_HOME": os.environ["HOME"] + "/.local/share",
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
        "CHATGPT_ROUTE_RUN_MARKER": run_marker,
        "CHATGPT_ROUTE_RUN_MARKER_FD": str(run_marker_fd),
    }
    environment[token_env_name] = os.environ[token_env_name]
    stderr_path = log_dir / "host.stderr"
    messages_path = log_dir / "host-messages.jsonl"
    summary_path = log_dir / "host-summary.json"
    messages: list[dict[str, Any]] = []
    request_id = 0

    with stderr_path.open("w") as stderr, messages_path.open("w") as raw:
        process = start_owned_process(
            sandbox_command(
                profile, real_home, codex, proxy_port, optiq_port, gateway_port, protected_helper
            ),
            cwd=workspace,
            environment=environment,
            stderr=stderr,
            run_marker_fd=run_marker_fd,
        )
        try:
            assert process.stdin is not None
            assert process.stdout is not None

            def receive(deadline: float) -> dict[str, Any]:
                return receive_message(process.stdout, raw, messages, deadline)

            def request(method: str, params: dict[str, Any], timeout: float = 15) -> dict[str, Any]:
                nonlocal request_id
                request_id += 1
                wanted = request_id
                process.stdin.write(
                    (json.dumps({"id": wanted, "method": method, "params": params}) + "\n").encode(
                        "utf-8"
                    )
                )
                process.stdin.flush()
                deadline = time.monotonic() + timeout
                while True:
                    message = receive(deadline)
                    if message.get("id") == wanted:
                        if "error" in message:
                            raise RuntimeError(f"{method} failed: {message['error']}")
                        return message

            initialize = request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "offline-route-prototype",
                        "title": "Offline route prototype",
                        "version": "1",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            )
            account = request("account/read", {"refreshToken": False})
            config = request("config/read", {"cwd": str(workspace), "includeLayers": True})
            permissions = request("permissionProfile/list", {"cwd": str(workspace)})
            modes = request("collaborationMode/list", {})
            skills = request(
                "skills/list", {"cwds": [str(workspace)], "forceReload": True}
            )
            started = request(
                "thread/start",
                {
                    "model": model_id,
                    "modelProvider": None,
                    "allowProviderModelFallback": False,
                    "cwd": str(workspace),
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "runtimeWorkspaceRoots": [str(workspace)],
                    "baseInstructions": (
                        "This is a deterministic local plumbing check. Never use tools. "
                        "Reply exactly LOCAL_APP_OK and nothing else."
                    ),
                    "developerInstructions": (
                        "Never use tools. Reply exactly LOCAL_APP_OK and nothing else."
                    ),
                    "ephemeral": False,
                    "experimentalRawEvents": False,
                    "threadSource": "user",
                    "dynamicTools": [],
                },
            )
            thread = started["result"]["thread"]
            thread_id = thread["id"]
            turn = request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [
                        {
                            "type": "skill",
                            "name": "local-sentinel",
                            "path": str(skill_path),
                        },
                        {
                            "type": "text",
                            "text": "Reply with exactly LOCAL_APP_OK and nothing else.",
                            "text_elements": [],
                        },
                    ],
                    "cwd": str(workspace),
                    "approvalPolicy": "never",
                    "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
                    "runtimeWorkspaceRoots": [str(workspace)],
                },
                timeout=30,
            )
            turn_id = turn["result"]["turn"]["id"]
            completed: dict[str, Any] | None = None
            agent_item_completed = False
            thread_became_idle = False
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                message = receive(deadline)
                if message.get("method") == "turn/completed":
                    params = message.get("params", {})
                    if params.get("threadId") == thread_id:
                        completed = message
                        break
                if message.get("method") == "item/completed":
                    item = message.get("params", {}).get("item", {})
                    if (
                        item.get("type") == "agentMessage"
                        and item.get("text") == "LOCAL_APP_OK"
                    ):
                        agent_item_completed = True
                if message.get("method") == "thread/status/changed":
                    params = message.get("params", {})
                    if (
                        params.get("threadId") == thread_id
                        and params.get("status", {}).get("type") == "idle"
                    ):
                        thread_became_idle = True
                if agent_item_completed and thread_became_idle:
                    break
                if "id" in message and "method" in message:
                    raise RuntimeError(
                        f"unexpected server request during no-tool sentinel: {message['method']}"
                    )
            if completed is None and not (agent_item_completed and thread_became_idle):
                raise TimeoutError("sentinel turn produced no terminal host state")

            listed = request("thread/list", {"cwd": str(workspace), "limit": 20})
            read = request("thread/read", {"threadId": thread_id, "includeTurns": True})
        finally:
            stop_process(process)

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

    streamed_agent_texts = agent_texts(messages)
    persisted_agent_texts = agent_texts(read)
    persisted_turns = read.get("result", {}).get("thread", {}).get("turns", [])
    persisted_turn = next(
        (candidate for candidate in persisted_turns if candidate.get("id") == turn_id),
        {},
    )
    effective_provider = (
        config.get("result", {})
        .get("config", {})
        .get("model_providers", {})
        .get("local-optiq", {})
    )
    assertions = {
        "account_requires_openai_auth_false": account["result"].get(
            "requiresOpenaiAuth"
        )
        is False,
        "configured_local_provider_visible": "local-optiq" in json.dumps(config),
        "provider_retry_limits_effective": effective_provider.get(
            "request_max_retries"
        )
        == 1
        and effective_provider.get("stream_max_retries") == 1,
        "permission_profiles_visible": bool(permissions.get("result")),
        "default_and_plan_modes_visible": all(
            name in json.dumps(modes).lower() for name in ("default", "plan")
        ),
        "local_skill_discovered": "local-sentinel" in json.dumps(skills),
        "sentinel_completed": persisted_turn.get("status") == "completed"
        and persisted_turn.get("error") is None,
        "sentinel_text_observed": "LOCAL_APP_OK" in streamed_agent_texts,
        "thread_listed": thread_id in json.dumps(listed),
        "thread_read_with_turn": thread_id in json.dumps(read)
        and "LOCAL_APP_OK" in persisted_agent_texts,
    }
    summary = {
        "thread_id": thread_id,
        "turn_id": turn_id,
        "returncode": process.returncode,
        "streamed_agent_texts": streamed_agent_texts,
        "terminal_signal": (
            "turn/completed"
            if completed is not None
            else "agent item/completed plus thread idle"
        ),
        "assertions": assertions,
        "initialize_result_keys": sorted(initialize.get("result", {}).keys()),
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    failed = [name for name, passed in assertions.items() if not passed]
    if failed:
        raise AssertionError(f"host assertions failed: {', '.join(failed)}")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        run_self_test()
    else:
        main()
