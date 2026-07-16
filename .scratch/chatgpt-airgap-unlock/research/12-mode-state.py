#!/usr/bin/env python3
"""Validate renderer-visible Default/Plan turns against persisted rollout state."""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import tempfile


DEFAULT_PROMPT = "Confirm Default mode in one short sentence. Do not use tools."
PLAN_PROMPT = "Confirm Plan mode in one short sentence. Do not use tools."


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


def bind_turn(rollout: list[dict], prompt: str, expected_mode: str) -> dict:
    matches: list[dict] = []
    active_context: dict | None = None
    for index, record in enumerate(rollout):
        if record.get("type") == "turn_context":
            active_context = record.get("payload", {})
            continue
        input_text = exact_input_text(record)
        if input_text is None:
            continue
        context = active_context
        active_context = None
        if input_text != prompt:
            continue
        if context is None:
            raise ContractError(f"{expected_mode} prompt has no preceding turn context")
        turn_id = context.get("turn_id")
        if not isinstance(turn_id, str) or not turn_id:
            raise ContractError(f"{expected_mode} prompt has no valid turn identity")
        mode = context.get("collaboration_mode", {}).get("mode")
        completions = [
            candidate.get("payload", {})
            for candidate in rollout[index + 1 :]
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
    if not match["final"]:
        raise ContractError(f"{expected_mode} turn completed with empty output")
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
    mode_outputs = [record for record in cdp if record.get("kind") == "mode-turn-output"]
    if len(mode_outputs) != 2:
        raise ContractError(f"expected two renderer mode outputs, found {len(mode_outputs)}")
    renderer_by_mode: dict[str, dict] = {}
    for output in mode_outputs:
        mode = output.get("mode")
        if mode in renderer_by_mode or mode not in ("default", "plan"):
            raise ContractError("renderer mode outputs are not unique Default and Plan records")
        if output.get("matched") is not True or output.get("outputLength", 0) < 1:
            raise ContractError(f"renderer {mode} output is empty or unmatched")
        renderer_by_mode[mode] = output
    rollout = records(rollouts[0])
    default = bind_turn(rollout, DEFAULT_PROMPT, "default")
    plan = bind_turn(rollout, PLAN_PROMPT, "plan")
    for mode, prompt, persisted in (
        ("default", DEFAULT_PROMPT, default),
        ("plan", PLAN_PROMPT, plan),
    ):
        renderer = renderer_by_mode.get(mode, {})
        if renderer.get("promptSha256") != sha256_text(prompt):
            raise ContractError(f"renderer {mode} prompt hash does not match")
        if renderer.get("outputSha256") != persisted["outputSha256"]:
            raise ContractError(f"renderer {mode} output does not match persisted completion")
    return {
        "default": default,
        "plan": plan,
        "rolloutSha256": hashlib.sha256(rollouts[0].read_bytes()).hexdigest(),
        "rendererAndPersistenceMatched": True,
    }


def fixture_turn(turn_id: str, mode: str, prompt: str, output: str) -> list[dict]:
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
                "last_agent_message": output,
            },
        },
    ]


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="chatgpt-mode-state-") as root_name:
        root = pathlib.Path(root_name)
        sessions = root / "codex-home" / "sessions" / "2026" / "07" / "15"
        sessions.mkdir(parents=True)
        rollout_path = sessions / "rollout-fixture.jsonl"
        default_output = "Default mode is active."
        plan_output = "Plan mode is active."
        fixture = fixture_turn("default-turn", "default", DEFAULT_PROMPT, default_output)
        fixture += fixture_turn("plan-turn", "plan", PLAN_PROMPT, plan_output)
        rollout_path.write_text("".join(json.dumps(item) + "\n" for item in fixture))
        cdp_path = root / "cdp.jsonl"
        cdp_fixture = [
            {
                "kind": "gui-modes-summary",
                "mainUi": True,
                "defaultModeControlObserved": True,
                "defaultPromptCompleted": True,
                "planModeControlObserved": True,
                "planPromptCompleted": True,
            },
            {
                "kind": "mode-turn-output",
                "mode": "default",
                "matched": True,
                "promptSha256": sha256_text(DEFAULT_PROMPT),
                "outputLength": len(default_output),
                "outputSha256": sha256_text(default_output),
            },
            {
                "kind": "mode-turn-output",
                "mode": "plan",
                "matched": True,
                "promptSha256": sha256_text(PLAN_PROMPT),
                "outputLength": len(plan_output),
                "outputSha256": sha256_text(plan_output),
            },
        ]
        cdp_path.write_text("".join(json.dumps(item) + "\n" for item in cdp_fixture))
        result = validate(root / "codex-home", cdp_path)
        if not result["rendererAndPersistenceMatched"]:
            raise AssertionError("valid mode fixture did not pass")

        stale_context = fixture_turn(
            "unrelated-turn", "default", "Unrelated user message.", "Unrelated output."
        )
        stale_context.append(fixture_turn("target", "default", DEFAULT_PROMPT, "ok")[1])
        try:
            bind_turn(stale_context, DEFAULT_PROMPT, "default")
        except ContractError as error:
            if "default prompt has no preceding turn context" not in str(error):
                raise
        else:
            raise AssertionError("unrelated user message did not consume turn context")

        invalid_identity = fixture_turn("target", "default", DEFAULT_PROMPT, "ok")
        invalid_identity[0]["payload"]["turn_id"] = ""
        try:
            bind_turn(invalid_identity, DEFAULT_PROMPT, "default")
        except ContractError as error:
            if "default prompt has no valid turn identity" not in str(error):
                raise
        else:
            raise AssertionError("empty turn identity unexpectedly passed")

        out_of_order = [fixture[2], fixture[0], fixture[1]]
        try:
            bind_turn(out_of_order, DEFAULT_PROMPT, "default")
        except ContractError as error:
            if "turn expected one completion, found 0" not in str(error):
                raise
        else:
            raise AssertionError("completion before its prompt unexpectedly passed")

        fixture[3]["payload"]["collaboration_mode"]["mode"] = "default"
        rollout_path.write_text("".join(json.dumps(item) + "\n" for item in fixture))
        try:
            validate(root / "codex-home", cdp_path)
        except ContractError as error:
            if "plan prompt persisted mode 'default'" not in str(error):
                raise
        else:
            raise AssertionError("wrong persisted Plan mode unexpectedly passed")

        fixture[3]["payload"]["collaboration_mode"]["mode"] = "plan"
        rollout_path.write_text("".join(json.dumps(item) + "\n" for item in fixture))
        cdp_fixture[2]["outputSha256"] = sha256_text("different renderer output")
        cdp_path.write_text("".join(json.dumps(item) + "\n" for item in cdp_fixture))
        try:
            validate(root / "codex-home", cdp_path)
        except ContractError as error:
            if "renderer plan output does not match persisted completion" not in str(error):
                raise
        else:
            raise AssertionError("renderer/persisted output mismatch unexpectedly passed")
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
