#!/usr/bin/env python3
"""Validate renderer-visible Default/Plan turns against persisted rollout state."""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import tempfile


DEFAULT_PROMPT = "Reply exactly MODE_DEFAULT_OK and nothing else. Do not use tools."
DEFAULT_SENTINEL = "MODE_DEFAULT_OK"
PLAN_PROMPT = "Reply exactly MODE_PLAN_OK and nothing else. Do not use tools."
PLAN_SENTINEL = "MODE_PLAN_OK"


class ContractError(RuntimeError):
    pass


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def records(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def exact_input_text(record: dict) -> str | None:
    if record.get("type") != "response_item":
        return None
    payload = record.get("payload", {})
    if payload.get("type") != "message" or payload.get("role") != "user":
        return None
    content = payload.get("content", [])
    if len(content) != 1 or content[0].get("type") != "input_text":
        return None
    return content[0].get("text", "").strip()


def bind_turn(rollout: list[dict], prompt: str, sentinel: str, expected_mode: str) -> dict:
    matches: list[dict] = []
    active_context: dict | None = None
    for record in rollout:
        if record.get("type") == "turn_context":
            active_context = record.get("payload", {})
            continue
        if exact_input_text(record) != prompt:
            continue
        if active_context is None:
            raise ContractError(f"{expected_mode} prompt has no preceding turn context")
        turn_id = active_context.get("turn_id")
        mode = active_context.get("collaboration_mode", {}).get("mode")
        completions = [
            candidate.get("payload", {})
            for candidate in rollout
            if candidate.get("type") == "event_msg"
            and candidate.get("payload", {}).get("type") == "task_complete"
            and candidate.get("payload", {}).get("turn_id") == turn_id
        ]
        if len(completions) != 1:
            raise ContractError(
                f"{expected_mode} turn expected one completion, found {len(completions)}"
            )
        final = completions[0].get("last_agent_message", "").strip()
        matches.append({"turn_id": turn_id, "mode": mode, "final": final})
    if len(matches) != 1:
        raise ContractError(f"{expected_mode} prompt expected once, found {len(matches)}")
    match = matches[0]
    if match["mode"] != expected_mode:
        raise ContractError(f"{expected_mode} prompt persisted mode {match['mode']!r}")
    if match["final"] != sentinel:
        raise ContractError(f"{expected_mode} turn did not complete with exact sentinel")
    return {
        "mode": expected_mode,
        "turnIdSha256": sha256_text(match["turn_id"]),
        "promptSha256": sha256_text(prompt),
        "outputSha256": sha256_text(match["final"]),
    }


def validate(codex_home: pathlib.Path, cdp_path: pathlib.Path) -> dict:
    rollouts = sorted((codex_home / "sessions").rglob("rollout-*.jsonl"))
    if len(rollouts) != 1:
        raise ContractError(f"expected one rollout, found {len(rollouts)}")
    cdp = records(cdp_path)
    summaries = [record for record in cdp if record.get("kind") == "gui-modes-summary"]
    if len(summaries) != 1:
        raise ContractError(f"expected one renderer mode summary, found {len(summaries)}")
    summary = summaries[0]
    required = (
        "mainUi",
        "defaultModeControlObserved",
        "defaultPromptCompleted",
        "planModeControlObserved",
        "planPromptCompleted",
    )
    if any(summary.get(field) is not True for field in required):
        raise ContractError("renderer mode summary is not fully green")
    rollout = records(rollouts[0])
    return {
        "default": bind_turn(rollout, DEFAULT_PROMPT, DEFAULT_SENTINEL, "default"),
        "plan": bind_turn(rollout, PLAN_PROMPT, PLAN_SENTINEL, "plan"),
        "rolloutSha256": hashlib.sha256(rollouts[0].read_bytes()).hexdigest(),
        "rendererAndPersistenceMatched": True,
    }


def fixture_turn(turn_id: str, mode: str, prompt: str, sentinel: str) -> list[dict]:
    return [
        {
            "type": "turn_context",
            "payload": {"turn_id": turn_id, "collaboration_mode": {"mode": mode}},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": prompt + "\n"}],
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": turn_id,
                "last_agent_message": sentinel,
            },
        },
    ]


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="chatgpt-mode-state-") as root_name:
        root = pathlib.Path(root_name)
        sessions = root / "codex-home" / "sessions" / "2026" / "07" / "15"
        sessions.mkdir(parents=True)
        rollout_path = sessions / "rollout-fixture.jsonl"
        fixture = fixture_turn("default-turn", "default", DEFAULT_PROMPT, DEFAULT_SENTINEL)
        fixture += fixture_turn("plan-turn", "plan", PLAN_PROMPT, PLAN_SENTINEL)
        rollout_path.write_text("".join(json.dumps(item) + "\n" for item in fixture))
        cdp_path = root / "cdp.jsonl"
        cdp_path.write_text(
            json.dumps(
                {
                    "kind": "gui-modes-summary",
                    "mainUi": True,
                    "defaultModeControlObserved": True,
                    "defaultPromptCompleted": True,
                    "planModeControlObserved": True,
                    "planPromptCompleted": True,
                }
            )
            + "\n"
        )
        result = validate(root / "codex-home", cdp_path)
        if not result["rendererAndPersistenceMatched"]:
            raise AssertionError("valid mode fixture did not pass")

        fixture[3]["payload"]["collaboration_mode"]["mode"] = "default"
        rollout_path.write_text("".join(json.dumps(item) + "\n" for item in fixture))
        try:
            validate(root / "codex-home", cdp_path)
        except ContractError as error:
            if "plan prompt persisted mode 'default'" not in str(error):
                raise
        else:
            raise AssertionError("wrong persisted Plan mode unexpectedly passed")
    print("mode persistence self-test passed")


def main() -> None:
    if sys.argv[1:] == ["--self-test"]:
        self_test()
        return
    if len(sys.argv) != 3:
        raise SystemExit("usage: 12-mode-state.py --self-test | CODEX_HOME CDP_JSONL")
    print(json.dumps(validate(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])), indent=2))


if __name__ == "__main__":
    main()
