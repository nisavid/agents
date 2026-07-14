#!/usr/bin/env python3
"""RESEARCH PROBE ONLY: query pristine account/read inside the outer sandbox."""

from __future__ import annotations

import json
import pathlib
import select
import subprocess
import tempfile
import time


HERE = pathlib.Path(__file__).resolve().parent
PROFILE = HERE / "03-probe.sb"
REAL_HOME = pathlib.Path.home().resolve()
CODEX = pathlib.Path(
    "/private/tmp/ChatGPT-Codex-5263-Offgrid-Probe-03.app/Contents/Resources/codex"
)


def main() -> None:
    run_root = pathlib.Path(
        tempfile.mkdtemp(prefix="chatgpt-account-probe-03.", dir="/private/tmp")
    )
    home = run_root / "home"
    codex_home = run_root / "codex-home"
    tmp = run_root / "tmp"
    for path in (home, codex_home, tmp):
        path.mkdir(mode=0o700)

    environment = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(home),
        "CFFIXED_USER_HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "TMPDIR": str(tmp),
        "USER": "offline-probe",
        "LOGNAME": "offline-probe",
        "SHELL": "/bin/sh",
        "LANG": "en_US.UTF-8",
        "RUST_LOG": "info",
    }
    requests = [
        {
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {
                    "name": "offline-startup-research-probe",
                    "title": "Offline startup research probe",
                    "version": "1",
                },
                "capabilities": {"experimentalApi": True},
            },
        },
        {
            "id": 2,
            "method": "account/read",
            "params": {"refreshToken": False},
        },
    ]
    stderr_path = run_root / "app-server.stderr"
    with stderr_path.open("w") as stderr:
        process = subprocess.Popen(
            [
                "/usr/bin/sandbox-exec",
                "-f",
                str(PROFILE),
                "-D",
                f"REAL_HOME={REAL_HOME}",
                str(CODEX),
                "app-server",
                "--stdio",
            ],
            cwd=run_root,
            env=environment,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        responses: list[dict[str, object]] = []
        for request in requests:
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
            deadline = time.monotonic() + 10
            received = False
            while time.monotonic() < deadline:
                ready, _, _ = select.select(
                    [process.stdout], [], [], deadline - time.monotonic()
                )
                if not ready:
                    break
                line = process.stdout.readline()
                if not line:
                    break
                message = json.loads(line)
                responses.append(message)
                if message.get("id") == request["id"]:
                    received = True
                    break
            if not received:
                process.terminate()
                raise TimeoutError(f"no response for request {request['id']}")

        process.stdin.close()
        process.wait(timeout=5)

    for message in responses:
        if message.get("method") == "remoteControl/status/changed":
            params = message.get("params")
            if isinstance(params, dict):
                params["installationId"] = "<redacted>"
                params["serverName"] = "<redacted>"
        result = message.get("result")
        if isinstance(result, dict) and "codexHome" in result:
            result["codexHome"] = "$RUN_ROOT/codex-home"

    print(
        json.dumps(
            {
                "run_root": str(run_root),
                "returncode": process.returncode,
                "stderr": str(stderr_path),
                "messages": responses,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
