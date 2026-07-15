#!/usr/bin/env python3
"""Build a run-local Codex catalog for the pinned OptiQ smoke model."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


MODEL_REVISION = "adc8669eb431e3168aeb4e320bd7b757914350e2"
MODEL_BASE = "Qwen/Qwen3.5-2B"
DISPLAY_NAME = "Qwen3.5-2B-OptiQ-4bit (no-think)"
FALLBACK_PROMPT_SHA256 = (
    "ac8ae107a0d72fe3476b430afb161ea4e67da2e446d778aefc44828160559807"
)
PROMPT_PREFIX = (
    b"You are a coding agent running in the Codex CLI, a terminal-based coding "
    b"assistant."
)
PROMPT_SUFFIX = (
    b"If all steps are complete, ensure you call `update_plan` to mark all steps "
    b"as `completed`.\n"
)


def extract_fallback_instructions(codex_binary: Path) -> str:
    binary = codex_binary.read_bytes()
    candidates: set[bytes] = set()
    start = 0
    while True:
        start = binary.find(PROMPT_PREFIX, start)
        if start < 0:
            break
        end = binary.find(PROMPT_SUFFIX, start)
        if end >= 0:
            candidate = binary[start : end + len(PROMPT_SUFFIX)]
            if hashlib.sha256(candidate).hexdigest() == FALLBACK_PROMPT_SHA256:
                candidates.add(candidate)
        start += len(PROMPT_PREFIX)
    if len(candidates) != 1:
        raise ValueError(
            "bundled Codex binary did not contain the pinned fallback instructions"
        )
    return next(iter(candidates)).decode()


def build_catalog(codex_binary: Path, model_dir: Path) -> dict[str, object]:
    config = json.loads((model_dir / "config.json").read_text())
    optiq = json.loads((model_dir / "optiq_metadata.json").read_text())
    readme = (model_dir / "README.md").read_text()

    if model_dir.name != MODEL_REVISION:
        raise ValueError(f"unexpected model revision: {model_dir.name}")
    if config.get("model_type") != "qwen3_5":
        raise ValueError(f"unexpected model type: {config.get('model_type')}")
    if config.get("architectures") != ["Qwen3_5ForConditionalGeneration"]:
        raise ValueError(f"unexpected architecture: {config.get('architectures')}")
    if optiq.get("method") != "optiq_mixed_precision":
        raise ValueError(f"unexpected quantization method: {optiq.get('method')}")
    if optiq.get("base_model") != MODEL_BASE:
        raise ValueError(f"unexpected base model: {optiq.get('base_model')}")
    if "pipeline_tag: text-generation" not in readme:
        raise ValueError("model card does not declare a text-generation pipeline")

    context_window = config.get("text_config", {}).get("max_position_embeddings")
    if context_window != 262_144:
        raise ValueError(f"unexpected context window: {context_window}")

    model_id = f"{model_dir}:no-think"
    model = {
        "slug": model_id,
        "display_name": DISPLAY_NAME,
        "description": (
            "Pinned local mlx-community Qwen3.5 2B mixed-precision OptiQ model; "
            "no-think variant."
        ),
        "default_reasoning_level": None,
        "supported_reasoning_levels": [],
        "shell_type": "default",
        "visibility": "list",
        "supported_in_api": True,
        "priority": 0,
        "additional_speed_tiers": [],
        "service_tiers": [],
        "default_service_tier": None,
        "availability_nux": None,
        "upgrade": None,
        "base_instructions": extract_fallback_instructions(codex_binary),
        "model_messages": None,
        "include_skills_usage_instructions": False,
        "supports_reasoning_summaries": False,
        "default_reasoning_summary": "auto",
        "support_verbosity": False,
        "default_verbosity": None,
        "apply_patch_tool_type": None,
        "web_search_tool_type": "text",
        "truncation_policy": {"mode": "bytes", "limit": 10_000},
        "supports_parallel_tool_calls": False,
        "supports_image_detail_original": False,
        "context_window": context_window,
        "max_context_window": context_window,
        "auto_compact_token_limit": None,
        "comp_hash": None,
        "effective_context_window_percent": 95,
        "experimental_supported_tools": [],
        "input_modalities": ["text"],
        "supports_search_tool": False,
        "use_responses_lite": False,
        "auto_review_model_override": None,
        "tool_mode": None,
        "multi_agent_version": None,
    }
    return {"models": [model]}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-binary", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    catalog = build_catalog(args.codex_binary, args.model_dir)
    args.output.write_text(json.dumps(catalog, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
