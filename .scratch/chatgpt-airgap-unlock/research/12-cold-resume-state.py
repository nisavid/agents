#!/usr/bin/env python3
"""THROWAWAY PROTOTYPE ONLY: bind cold resume to completed renderer turns."""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import tempfile


FIRST_PROMPT = "Reply exactly COLD_PHASE_ONE_OK and nothing else. Do not use tools."
FIRST_SENTINEL = "COLD_PHASE_ONE_OK"
SECOND_PROMPT = "Reply exactly COLD_PHASE_TWO_OK and nothing else. Do not use tools."
SECOND_SENTINEL = "COLD_PHASE_TWO_OK"


class ContractError(RuntimeError):
    pass


def normalized_sha256(value: str) -> str:
    return hashlib.sha256(" ".join(value.split()).encode()).hexdigest()


def exact_sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def sentinel_output_matches(message: str, expected_sentinel: str) -> bool:
    return message.strip() == expected_sentinel


def read_records(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def matching_user_messages(records: list[dict], prompt: str) -> list[dict]:
    matches = []
    for record in records:
        payload = record.get("payload", {})
        if record.get("type") != "response_item" or payload.get("type") != "message" or payload.get("role") != "user":
            continue
        texts = [item.get("text", "") for item in payload.get("content", []) if item.get("type") == "input_text"]
        if any(text.strip() == prompt for text in texts):
            matches.append(payload)
    return matches


def completed_turn(records: list[dict], prompt: str, expected_sentinel: str) -> tuple[str, str]:
    users = matching_user_messages(records, prompt)
    if len(users) != 1:
        raise ContractError(f"expected one matching user prompt, found {len(users)}")
    turn_id = users[0].get("internal_chat_message_metadata_passthrough", {}).get("turn_id")
    if not isinstance(turn_id, str) or not turn_id:
        raise ContractError("matching user prompt has no turn identity")
    completions = [
        record.get("payload", {})
        for record in records
        if record.get("type") == "event_msg"
        and record.get("payload", {}).get("type") == "task_complete"
        and record.get("payload", {}).get("turn_id") == turn_id
    ]
    if len(completions) != 1:
        raise ContractError(f"expected one task_complete for matching turn, found {len(completions)}")
    final_message = completions[0].get("last_agent_message", "")
    if not isinstance(final_message, str) or not sentinel_output_matches(
        final_message, expected_sentinel
    ):
        raise ContractError("completed turn final message failed the exact sentinel contract")
    return turn_id, final_message


def session_id(records: list[dict]) -> str:
    identifiers = [
        record.get("payload", {}).get("id") or record.get("payload", {}).get("session_id")
        for record in records
        if record.get("type") == "session_meta"
    ]
    if len(identifiers) != 1 or not isinstance(identifiers[0], str):
        raise ContractError(f"expected one session identity, found {len(identifiers)}")
    return identifiers[0]


def session_cwd(records: list[dict]) -> str:
    values = [
        record.get("payload", {}).get("cwd")
        for record in records
        if record.get("type") == "session_meta"
    ]
    if len(values) != 1 or not isinstance(values[0], str) or not pathlib.Path(values[0]).is_absolute():
        raise ContractError(f"expected one absolute session cwd, found {len(values)}")
    return values[0]


def renderer_oracle(cdp_path: pathlib.Path, phase: str, expected_sentinel: str) -> dict:
    phase_records = [
        record
        for record in read_records(cdp_path)
        if record.get("kind") == "assistant-output-oracle"
        and record.get("phase") == phase
    ]
    if len(phase_records) != 1:
        raise ContractError(
            f"expected one {phase}-phase renderer output oracle, found {len(phase_records)}"
        )
    oracle = phase_records[0]
    if (
        oracle.get("matched") is not True
        or oracle.get("exactMatch") is not True
        or oracle.get("textSha256") != exact_sha256(expected_sentinel)
    ):
        raise ContractError(f"{phase}-phase renderer output oracle failed the sentinel contract")
    return oracle


def matching_rollout(codex_dir: pathlib.Path, prompt: str) -> tuple[pathlib.Path, list[dict]]:
    matches = []
    for rollout in codex_dir.glob("sessions/*/*/*/rollout-*.jsonl"):
        records = read_records(rollout)
        if matching_user_messages(records, prompt):
            matches.append((rollout, records))
    if len(matches) != 1:
        raise ContractError(f"expected one persisted renderer thread, found {len(matches)}")
    return matches[0]


def capture(
    codex_dir: pathlib.Path,
    state_path: pathlib.Path,
    cdp_path: pathlib.Path,
    expected_cwd: pathlib.Path | None = None,
) -> None:
    rollout, records = matching_rollout(codex_dir, FIRST_PROMPT)
    thread_id = session_id(records)
    cwd = session_cwd(records)
    if expected_cwd is not None and cwd != str(expected_cwd.resolve(strict=True)):
        raise ContractError("persisted session cwd does not match the expected workspace")
    first_turn_id, first_final = completed_turn(records, FIRST_PROMPT, FIRST_SENTINEL)
    oracle = renderer_oracle(cdp_path, "first", FIRST_SENTINEL)
    state = {
        "threadId": thread_id,
        "rolloutPath": str(rollout),
        "firstTurnIdSha256": exact_sha256(first_turn_id),
        "firstPromptSha256": normalized_sha256(FIRST_PROMPT),
        "firstSentinelSha256": exact_sha256(FIRST_SENTINEL),
        "firstPersistedOutputSha256": exact_sha256(first_final),
        "firstRendererOutputSha256": oracle["textSha256"],
        "firstOutputBinding": "completed-turn",
        "cwdSha256": exact_sha256(cwd),
        "cwdBinding": "rollout-session-meta",
    }
    state_path.write_text(json.dumps(state, sort_keys=True) + "\n")
    state_path.chmod(0o600)


def validate(
    state_path: pathlib.Path,
    cdp_path: pathlib.Path,
    expected_cwd: pathlib.Path | None = None,
) -> None:
    state = json.loads(state_path.read_text())
    if state.get("firstOutputBinding") != "completed-turn":
        raise ContractError("original output is not bound to a completed turn")
    if (
        state.get("firstPromptSha256") != normalized_sha256(FIRST_PROMPT)
        or state.get("firstSentinelSha256") != exact_sha256(FIRST_SENTINEL)
    ):
        raise ContractError("original completed-turn contract changed across restart")
    rollout = pathlib.Path(state["rolloutPath"])
    records = read_records(rollout)
    if session_id(records) != state["threadId"]:
        raise ContractError("thread identity changed across restart")
    cwd = session_cwd(records)
    if state.get("cwdBinding") != "rollout-session-meta" or exact_sha256(cwd) != state.get("cwdSha256"):
        raise ContractError("session cwd changed across restart")
    if expected_cwd is not None and cwd != str(expected_cwd.resolve(strict=True)):
        raise ContractError("persisted session cwd does not match the expected workspace")
    first_turn_id, first_final = completed_turn(records, FIRST_PROMPT, FIRST_SENTINEL)
    if exact_sha256(first_turn_id) != state["firstTurnIdSha256"]:
        raise ContractError("original completed turn identity changed across restart")
    if exact_sha256(first_final) != state["firstPersistedOutputSha256"]:
        raise ContractError("original completed final output changed across restart")
    _, second_final = completed_turn(records, SECOND_PROMPT, SECOND_SENTINEL)
    oracle = renderer_oracle(cdp_path, "second", SECOND_SENTINEL)
    state.update(
        {
            "secondSentinelSha256": exact_sha256(SECOND_SENTINEL),
            "secondPromptSha256": normalized_sha256(SECOND_PROMPT),
            "secondPersistedOutputSha256": exact_sha256(second_final),
            "secondRendererOutputSha256": oracle["textSha256"],
            "secondOutputBinding": "completed-turn",
            "sameRolloutContinuationValidated": True,
        }
    )
    state_path.write_text(json.dumps(state, sort_keys=True) + "\n")


def fixture_turn(prompt: str, turn_id: str, intermediates: list[dict], final: str) -> list[dict]:
    records = [
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": prompt + "\n"}],
                "internal_chat_message_metadata_passthrough": {"turn_id": turn_id},
            },
        }
    ]
    records.extend(intermediates)
    records.append(
        {
            "type": "event_msg",
            "payload": {"type": "task_complete", "turn_id": turn_id, "last_agent_message": final},
        }
    )
    return records


def fixture_failed_tool_calls(count: int) -> list[dict]:
    records = []
    for index in range(count):
        call_id = f"call-{index}"
        records.extend(
            [
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "call_id": call_id,
                        "arguments": '{"cmd":"false"}',
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": "Process exited with code 71",
                    },
                },
            ]
        )
    return records


def fixture_agent_message(message: str) -> dict:
    return {"type": "event_msg", "payload": {"type": "agent_message", "message": message}}


def fixture_oracle(phase: str, sentinel: str) -> dict:
    return {
        "kind": "assistant-output-oracle",
        "phase": phase,
        "matched": True,
        "exactMatch": True,
        "textSha256": exact_sha256(sentinel),
    }


def expect_contract_error(action, expected_fragment: str) -> None:
    try:
        action()
    except ContractError as error:
        if expected_fragment not in str(error):
            raise AssertionError(f"expected {expected_fragment!r}, got {str(error)!r}") from error
    else:
        raise AssertionError(f"expected ContractError containing {expected_fragment!r}")


def run_self_tests() -> None:
    with tempfile.TemporaryDirectory(prefix="chatgpt-cold-resume-state-") as directory:
        root = pathlib.Path(directory)
        codex_dir = root / "codex"
        rollout = codex_dir / "sessions/2026/07/14/rollout-fixture.jsonl"
        rollout.parent.mkdir(parents=True)
        state_path = root / "state.json"
        cdp_path = root / "cdp.jsonl"
        workspace = root / "workspace"
        workspace.mkdir()

        first_records = [
            {
                "type": "session_meta",
                "payload": {
                    "id": "019f0000-0000-7000-8000-000000000001",
                    "cwd": str(workspace.resolve()),
                },
            },
            *fixture_turn(
                FIRST_PROMPT,
                "turn-first",
                [
                    *fixture_failed_tool_calls(2),
                    fixture_agent_message("still working"),
                    fixture_agent_message(FIRST_SENTINEL),
                ],
                FIRST_SENTINEL,
            ),
        ]
        rollout.write_text("\n".join(json.dumps(record) for record in first_records) + "\n")
        cdp_path.write_text(json.dumps(fixture_oracle("first", FIRST_SENTINEL)) + "\n")
        capture(codex_dir, state_path, cdp_path, workspace)
        state = json.loads(state_path.read_text())
        assert state["firstOutputBinding"] == "completed-turn"
        assert state["firstSentinelSha256"] == exact_sha256(FIRST_SENTINEL)
        assert state["firstPersistedOutputSha256"] == exact_sha256(FIRST_SENTINEL)
        assert state["cwdBinding"] == "rollout-session-meta"
        assert state["cwdSha256"] == exact_sha256(str(workspace.resolve()))

        wrong_intermediate = [
            *first_records[:-1],
            fixture_agent_message("COLD_PHASE_WRONG"),
            first_records[-1],
        ]
        _, bound_final = completed_turn(wrong_intermediate, FIRST_PROMPT, FIRST_SENTINEL)
        assert bound_final == FIRST_SENTINEL

        conflicting = [dict(record) for record in first_records]
        conflicting[-1] = {
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": "turn-first",
                "last_agent_message": f"{FIRST_SENTINEL}\n{FIRST_SENTINEL}",
            },
        }
        expect_contract_error(
            lambda: completed_turn(conflicting, FIRST_PROMPT, FIRST_SENTINEL), "final message"
        )
        wrong_final = [*first_records[:-1]]
        wrong_final.append(
            {
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "turn_id": "turn-first",
                    "last_agent_message": SECOND_SENTINEL,
                },
            }
        )
        expect_contract_error(
            lambda: completed_turn(wrong_final, FIRST_PROMPT, FIRST_SENTINEL), "final message"
        )
        duplicate = [*first_records, first_records[-1]]
        expect_contract_error(
            lambda: completed_turn(duplicate, FIRST_PROMPT, FIRST_SENTINEL), "found 2"
        )
        duplicate_prompt = [*first_records, first_records[1]]
        expect_contract_error(
            lambda: completed_turn(duplicate_prompt, FIRST_PROMPT, FIRST_SENTINEL), "found 2"
        )

        no_completion = [
            first_records[0],
            *fixture_turn(FIRST_PROMPT, "turn-loop", fixture_failed_tool_calls(105), FIRST_SENTINEL)[
                :-1
            ],
        ]
        expect_contract_error(
            lambda: completed_turn(no_completion, FIRST_PROMPT, FIRST_SENTINEL), "found 0"
        )

        cdp_path.write_text(json.dumps(fixture_oracle("first", SECOND_SENTINEL)) + "\n")
        expect_contract_error(
            lambda: capture(codex_dir, state_path, cdp_path), "renderer output oracle"
        )
        cdp_path.write_text(
            "\n".join(
                json.dumps(fixture_oracle("first", FIRST_SENTINEL)) for _ in range(2)
            )
            + "\n"
        )
        expect_contract_error(lambda: capture(codex_dir, state_path, cdp_path), "found 2")
        cdp_path.write_text(json.dumps(fixture_oracle("first", FIRST_SENTINEL)) + "\n")

        second_records = fixture_turn(
            SECOND_PROMPT,
            "turn-second",
            [fixture_agent_message(SECOND_SENTINEL)],
            SECOND_SENTINEL,
        )
        continued_records = [*first_records, *second_records]
        rollout.write_text("\n".join(json.dumps(record) for record in continued_records) + "\n")
        cdp_path.write_text(
            "\n".join(
                json.dumps(record)
                for record in (
                    fixture_oracle("first", FIRST_SENTINEL),
                    fixture_oracle("second", SECOND_SENTINEL),
                )
            )
            + "\n"
        )
        validate(state_path, cdp_path, workspace)
        state = json.loads(state_path.read_text())
        assert state["secondOutputBinding"] == "completed-turn"
        assert state["secondSentinelSha256"] == exact_sha256(SECOND_SENTINEL)
        assert state["secondPersistedOutputSha256"] == exact_sha256(SECOND_SENTINEL)

        conflicting_second = [
            *first_records,
            *fixture_turn(SECOND_PROMPT, "turn-second", [], f"extra {SECOND_SENTINEL}"),
        ]
        rollout.write_text("\n".join(json.dumps(record) for record in conflicting_second) + "\n")
        expect_contract_error(lambda: validate(state_path, cdp_path), "final message")

        duplicate_second = [*continued_records, second_records[-1]]
        rollout.write_text("\n".join(json.dumps(record) for record in duplicate_second) + "\n")
        expect_contract_error(lambda: validate(state_path, cdp_path), "found 2")

        changed_first = [*first_records[:-1]]
        changed_first.append(
            {
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "turn_id": "turn-first",
                    "last_agent_message": f"{FIRST_SENTINEL}\n",
                },
            }
        )
        rollout.write_text(
            "\n".join(json.dumps(record) for record in [*changed_first, *second_records]) + "\n"
        )
        expect_contract_error(lambda: validate(state_path, cdp_path), "final output changed")

        changed_cwd = [dict(record) for record in [*first_records, *second_records]]
        changed_cwd[0] = {
            "type": "session_meta",
            "payload": {
                "id": "019f0000-0000-7000-8000-000000000001",
                "cwd": str(root.resolve()),
            },
        }
        rollout.write_text("\n".join(json.dumps(record) for record in changed_cwd) + "\n")
        expect_contract_error(lambda: validate(state_path, cdp_path), "session cwd changed")

    print("cold resume completed-turn self-test passed")


def main() -> None:
    if sys.argv[1:] == ["--self-test"]:
        run_self_tests()
    elif len(sys.argv) in (5, 6) and sys.argv[1] == "capture":
        capture(
            pathlib.Path(sys.argv[2]),
            pathlib.Path(sys.argv[3]),
            pathlib.Path(sys.argv[4]),
            pathlib.Path(sys.argv[5]) if len(sys.argv) == 6 else None,
        )
    elif len(sys.argv) in (4, 5) and sys.argv[1] == "validate":
        validate(
            pathlib.Path(sys.argv[2]),
            pathlib.Path(sys.argv[3]),
            pathlib.Path(sys.argv[4]) if len(sys.argv) == 5 else None,
        )
    else:
        raise SystemExit(
            "usage: 12-cold-resume-state.py --self-test | "
            "capture CODEX_DIR STATE CDP [EXPECTED_CWD] | "
            "validate STATE CDP [EXPECTED_CWD]"
        )


if __name__ == "__main__":
    main()
