#!/usr/bin/env python3
"""No-app contract test for the pinned local model catalog."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time


HERE = Path(__file__).resolve().parent
BUILDER = HERE / "08-model-catalog.py"
METADATA_PROFILE = HERE / "08-metadata-probe.sb"
SOURCE_APP = Path(
    os.environ.get(
        "SOURCE_APP",
        "/private/tmp/ChatGPT-Codex-26.707.71524-5263-extracted/"
        "ChatGPT-Codex-26.707.71524-5263.app",
    )
)
CODEX = SOURCE_APP / "Contents/Resources/codex"
MODEL_DIR = Path(
    os.environ.get(
        "MODEL_DIR",
        str(
            Path.home()
            / ".cache/huggingface/hub/"
            "models--mlx-community--Qwen3.5-2B-OptiQ-4bit/"
            "snapshots/adc8669eb431e3168aeb4e320bd7b757914350e2"
        ),
    )
)
MODEL_ID = f"{MODEL_DIR}:no-think"
DISPLAY_NAME = "Qwen3.5-2B-OptiQ-4bit (no-think)"
FALLBACK_PROMPT_SHA256 = (
    "ac8ae107a0d72fe3476b430afb161ea4e67da2e446d778aefc44828160559807"
)


def isolated_environment(home: Path, codex_home: Path, temporary: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "TMPDIR": str(temporary),
        "LANG": "en_US.UTF-8",
    }


def isolated_codex_command(*arguments: str) -> list[str]:
    return [
        "/usr/bin/sandbox-exec",
        "-f",
        str(METADATA_PROFILE),
        str(CODEX),
        *arguments,
    ]


def assert_catalog(
    catalog: dict[str, object],
    expected_model_id: str = MODEL_ID,
    expected_display_name: str = DISPLAY_NAME,
) -> None:
    models = catalog["models"]
    assert isinstance(models, list) and len(models) == 1
    model = models[0]
    assert isinstance(model, dict)
    assert model["slug"] == expected_model_id
    assert model["display_name"] == expected_display_name
    assert model.get("default_reasoning_level") is None
    assert model["supported_reasoning_levels"] == []
    assert model["context_window"] == 262_144
    assert model["max_context_window"] == 262_144
    assert model["input_modalities"] == ["text"]
    assert model["supports_parallel_tool_calls"] is False
    assert model["supports_reasoning_summaries"] is False
    assert hashlib.sha256(model["base_instructions"].encode()).hexdigest() == (
        FALLBACK_PROMPT_SHA256
    )


def request(
    process: subprocess.Popen[str],
    request_id: int,
    method: str,
    params: dict[str, object],
    timeout: float = 10,
) -> dict[str, object]:
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(
        json.dumps({"id": request_id, "method": method, "params": params}) + "\n"
    )
    process.stdin.flush()
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"timed out waiting for {method}")
        ready, _, _ = select.select([process.stdout], [], [], remaining)
        if not ready:
            raise TimeoutError(f"timed out waiting for {method}")
        line = process.stdout.readline()
        if not line:
            break
        message = json.loads(line)
        if message.get("id") == request_id:
            if "error" in message:
                raise RuntimeError(f"{method} failed: {message['error']}")
            return message
    raise RuntimeError(f"app-server exited before replying to {method}")


def run_full_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="chatgpt-model-catalog-test.") as raw_root:
        root = Path(raw_root)
        catalog_path = root / "model-catalog.json"
        subprocess.run(
            [
                sys.executable,
                str(BUILDER),
                "--codex-binary",
                str(CODEX),
                "--model-dir",
                str(MODEL_DIR),
                "--output",
                str(catalog_path),
            ],
            check=True,
        )
        assert_catalog(json.loads(catalog_path.read_text()))

        codex_home = root / "codex-home"
        home = root / "home"
        temporary = root / "tmp"
        codex_home.mkdir()
        home.mkdir()
        temporary.mkdir()
        (codex_home / "config.toml").write_text(
            f'model_catalog_json = "{catalog_path}"\n'
        )
        child_environment = isolated_environment(home, codex_home, temporary)
        assert set(child_environment) == {
            "PATH",
            "HOME",
            "CODEX_HOME",
            "TMPDIR",
            "LANG",
        }
        completed = subprocess.run(
            isolated_codex_command("debug", "models"),
            check=False,
            capture_output=True,
            env=child_environment,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "isolated codex debug models failed: " + completed.stderr.strip()
            )
        assert_catalog(json.loads(completed.stdout))

        process = subprocess.Popen(
            isolated_codex_command("app-server", "--stdio"),
            cwd=root,
            env=child_environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            request(
                process,
                1,
                "initialize",
                {
                    "clientInfo": {
                        "name": "model-catalog-contract",
                        "title": "Model catalog contract",
                        "version": "1",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            )
            response = request(
                process,
                2,
                "model/list",
                {"cursor": None, "limit": 10, "includeHidden": True},
            )
            items = response["result"]["data"]
            assert len(items) == 1
            assert items[0]["model"] == MODEL_ID
            assert items[0]["displayName"] == DISPLAY_NAME
            assert items[0]["defaultReasoningEffort"] == "none"
            assert items[0]["supportedReasoningEfforts"] == []
            assert items[0]["inputModalities"] == ["text"]
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def main() -> int:
    if sys.flags.optimize:
        raise SystemExit(
            "08-model-catalog-test.py requires assertions; "
            "rerun without -O or PYTHONOPTIMIZE"
        )
    if len(sys.argv) > 1:
        if len(sys.argv) != 5 or sys.argv[1] != "--catalog":
            raise SystemExit(
                "usage: 08-model-catalog-test.py "
                "[--catalog PATH EXPECTED_MODEL_ID EXPECTED_DISPLAY_NAME]"
            )
        assert_catalog(
            json.loads(Path(sys.argv[2]).read_text()),
            expected_model_id=sys.argv[3],
            expected_display_name=sys.argv[4],
        )
    else:
        run_full_contract()

    print("pinned local model catalog contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
