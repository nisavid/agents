#!/usr/bin/env python3
"""THROWAWAY PROTOTYPE ONLY: bind cold resume to completed renderer turns."""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys
import tempfile


FIRST_PROMPT = "What is 73 plus 19? Your final answer must include the decimal result."
SECOND_PROMPT = "What is 46 plus 17? Your final answer must include the decimal result."


class ContractError(RuntimeError):
    pass


def normalized_sha256(value: str) -> str:
    return hashlib.sha256(" ".join(value.split()).encode()).hexdigest()


def exact_sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def arithmetic_output_matches(message: str, operands: tuple[int, int], expected_result: int) -> bool:
    integers = [int(value) for value in re.findall(r"(?<![\w.])[+-]?\d+(?!\w|\.\d)", message)]
    allowed = {*operands, expected_result}
    return bool(integers) and all(value in allowed for value in integers) and integers[-1] == expected_result


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


def completed_turn(
    records: list[dict], prompt: str, operands: tuple[int, int], expected_result: int
) -> tuple[str, str]:
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
    if not isinstance(final_message, str) or not arithmetic_output_matches(final_message, operands, expected_result):
        raise ContractError("completed turn final message failed the arithmetic contract")
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


def renderer_oracle(cdp_path: pathlib.Path, phase: str, expected_result: int) -> dict:
    matches = [
        record
        for record in read_records(cdp_path)
        if record.get("kind") == "assistant-output-oracle"
        and record.get("phase") == phase
        and record.get("matched") is True
        and record.get("expectedOccurrenceCount", 0) >= 1
        and record.get("conflictingIntegers") == []
        and record.get("finalInteger") == expected_result
        and isinstance(record.get("textSha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", record["textSha256"])
    ]
    if len(matches) != 1:
        raise ContractError(f"expected one {phase}-phase renderer output oracle, found {len(matches)}")
    return matches[0]


def matching_rollout(codex_dir: pathlib.Path, prompt: str) -> tuple[pathlib.Path, list[dict]]:
    matches = []
    for rollout in codex_dir.glob("sessions/*/*/*/rollout-*.jsonl"):
        records = read_records(rollout)
        if matching_user_messages(records, prompt):
            matches.append((rollout, records))
    if len(matches) != 1:
        raise ContractError(f"expected one persisted renderer thread, found {len(matches)}")
    return matches[0]


def capture(codex_dir: pathlib.Path, state_path: pathlib.Path, cdp_path: pathlib.Path) -> None:
    rollout, records = matching_rollout(codex_dir, FIRST_PROMPT)
    thread_id = session_id(records)
    first_turn_id, first_final = completed_turn(records, FIRST_PROMPT, (73, 19), 92)
    oracle = renderer_oracle(cdp_path, "first", 92)
    state = {
        "threadId": thread_id,
        "rolloutPath": str(rollout),
        "firstTurnIdSha256": exact_sha256(first_turn_id),
        "firstPromptSha256": normalized_sha256(FIRST_PROMPT),
        "firstResult": "92",
        "firstPersistedOutputSha256": exact_sha256(first_final),
        "firstRendererOutputSha256": oracle["textSha256"],
        "firstOutputBinding": "completed-turn",
    }
    state_path.write_text(json.dumps(state, sort_keys=True) + "\n")
    state_path.chmod(0o600)


def validate(state_path: pathlib.Path, cdp_path: pathlib.Path) -> None:
    state = json.loads(state_path.read_text())
    if state.get("firstOutputBinding") != "completed-turn":
        raise ContractError("original output is not bound to a completed turn")
    if state.get("firstPromptSha256") != normalized_sha256(FIRST_PROMPT) or state.get("firstResult") != "92":
        raise ContractError("original completed-turn contract changed across restart")
    rollout = pathlib.Path(state["rolloutPath"])
    records = read_records(rollout)
    if session_id(records) != state["threadId"]:
        raise ContractError("thread identity changed across restart")
    first_turn_id, first_final = completed_turn(records, FIRST_PROMPT, (73, 19), 92)
    if exact_sha256(first_turn_id) != state["firstTurnIdSha256"]:
        raise ContractError("original completed turn identity changed across restart")
    if exact_sha256(first_final) != state["firstPersistedOutputSha256"]:
        raise ContractError("original completed final output changed across restart")
    _, second_final = completed_turn(records, SECOND_PROMPT, (46, 17), 63)
    oracle = renderer_oracle(cdp_path, "second", 63)
    state.update(
        {
            "secondResult": "63",
            "secondPromptSha256": normalized_sha256(SECOND_PROMPT),
            "secondPersistedOutputSha256": exact_sha256(second_final),
            "secondRendererOutputSha256": oracle["textSha256"],
            "secondOutputBinding": "completed-turn",
            "sameRolloutContinuationValidated": True,
        }
    )
    state_path.write_text(json.dumps(state, sort_keys=True) + "\n")


def fixture_turn(prompt: str, turn_id: str, intermediates: list[str], final: str) -> list[dict]:
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
    records.extend(
        {"type": "event_msg", "payload": {"type": "agent_message", "message": message}}
        for message in intermediates
    )
    records.append(
        {
            "type": "event_msg",
            "payload": {"type": "task_complete", "turn_id": turn_id, "last_agent_message": final},
        }
    )
    return records


def fixture_oracle(phase: str, result: int) -> dict:
    return {
        "kind": "assistant-output-oracle",
        "phase": phase,
        "matched": True,
        "expectedOccurrenceCount": 1,
        "conflictingIntegers": [],
        "finalInteger": result,
        "textSha256": hashlib.sha256(f"renderer-{phase}".encode()).hexdigest(),
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

        first_records = [
            {"type": "session_meta", "payload": {"id": "019f0000-0000-7000-8000-000000000001"}},
            *fixture_turn(
                FIRST_PROMPT,
                "turn-first",
                ["73 plus 19 equals 92.", "73 + 19 = 92"],
                "73 + 19 = 92",
            ),
        ]
        rollout.write_text("\n".join(json.dumps(record) for record in first_records) + "\n")
        cdp_path.write_text(json.dumps(fixture_oracle("first", 92)) + "\n")
        capture(codex_dir, state_path, cdp_path)
        state = json.loads(state_path.read_text())
        assert state["firstOutputBinding"] == "completed-turn"
        assert state["firstPersistedOutputSha256"] == exact_sha256("73 + 19 = 92")

        wrong_intermediate = [
            *first_records[:-1],
            {
                "type": "event_msg",
                "payload": {"type": "agent_message", "message": "73 + 19 = 91"},
            },
            first_records[-1],
        ]
        _, bound_final = completed_turn(wrong_intermediate, FIRST_PROMPT, (73, 19), 92)
        assert bound_final == "73 + 19 = 92"

        conflicting = [dict(record) for record in first_records]
        conflicting[-1] = {
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": "turn-first",
                "last_agent_message": "73 + 19 = 91. Correction: 92",
            },
        }
        expect_contract_error(
            lambda: completed_turn(conflicting, FIRST_PROMPT, (73, 19), 92), "final message"
        )
        duplicate = [*first_records, first_records[-1]]
        expect_contract_error(
            lambda: completed_turn(duplicate, FIRST_PROMPT, (73, 19), 92), "found 2"
        )

        second_records = fixture_turn(
            SECOND_PROMPT,
            "turn-second",
            ["46 plus 17 equals 63.", "46 + 17 = 63"],
            "63",
        )
        continued_records = [*first_records, *second_records]
        rollout.write_text("\n".join(json.dumps(record) for record in continued_records) + "\n")
        cdp_path.write_text(
            "\n".join(
                json.dumps(record)
                for record in (fixture_oracle("first", 92), fixture_oracle("second", 63))
            )
            + "\n"
        )
        validate(state_path, cdp_path)
        state = json.loads(state_path.read_text())
        assert state["secondOutputBinding"] == "completed-turn"
        assert state["secondPersistedOutputSha256"] == exact_sha256("63")

        conflicting_second = [
            *first_records,
            *fixture_turn(SECOND_PROMPT, "turn-second", [], "46 + 17 = 62. Correction: 63"),
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
                    "last_agent_message": "73 + 19 = 92\n",
                },
            }
        )
        rollout.write_text(
            "\n".join(json.dumps(record) for record in [*changed_first, *second_records]) + "\n"
        )
        expect_contract_error(lambda: validate(state_path, cdp_path), "final output changed")

    print("cold resume completed-turn self-test passed")


def main() -> None:
    if sys.argv[1:] == ["--self-test"]:
        run_self_tests()
    elif len(sys.argv) == 5 and sys.argv[1] == "capture":
        capture(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]), pathlib.Path(sys.argv[4]))
    elif len(sys.argv) == 4 and sys.argv[1] == "validate":
        validate(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]))
    else:
        raise SystemExit(
            "usage: 12-cold-resume-state.py --self-test | "
            "capture CODEX_DIR STATE CDP | validate STATE CDP"
        )


if __name__ == "__main__":
    main()
