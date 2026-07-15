#!/usr/bin/env python3
"""No-app contract test for the pinned local model catalog."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


HERE = Path(__file__).resolve().parent
BUILDER = HERE / "08-model-catalog.py"
SOURCE_APP = Path(
    "/private/tmp/ChatGPT-Codex-26.707.71524-5263-extracted/"
    "ChatGPT-Codex-26.707.71524-5263.app"
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


def assert_catalog(catalog: dict[str, object]) -> None:
    models = catalog["models"]
    assert isinstance(models, list) and len(models) == 1
    model = models[0]
    assert isinstance(model, dict)
    assert model["slug"] == MODEL_ID
    assert model["display_name"] == DISPLAY_NAME
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
    process: subprocess.Popen[str], request_id: int, method: str, params: dict[str, object]
) -> dict[str, object]:
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(
        json.dumps({"id": request_id, "method": method, "params": params}) + "\n"
    )
    process.stdin.flush()
    while line := process.stdout.readline():
        message = json.loads(line)
        if message.get("id") == request_id:
            if "error" in message:
                raise RuntimeError(f"{method} failed: {message['error']}")
            return message
    raise RuntimeError(f"app-server exited before replying to {method}")


def main() -> int:
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
        codex_home.mkdir()
        home.mkdir()
        (codex_home / "config.toml").write_text(
            f'model_catalog_json = "{catalog_path}"\n'
        )
        completed = subprocess.run(
            [str(CODEX), "debug", "models"],
            check=True,
            capture_output=True,
            env=os.environ
            | {
                "CODEX_HOME": str(codex_home),
                "HOME": str(home),
            },
            text=True,
        )
        assert_catalog(json.loads(completed.stdout))

        process = subprocess.Popen(
            [str(CODEX), "app-server", "--stdio"],
            cwd=root,
            env=os.environ
            | {
                "CODEX_HOME": str(codex_home),
                "HOME": str(home),
            },
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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
            process.wait(timeout=10)

    print("pinned local model catalog contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
