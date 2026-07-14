#!/usr/bin/env python3
"""THROWAWAY PROTOTYPE ONLY: deterministically probe namespace continuation."""

from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import sys


def load_gateway(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location("ticket08_gateway", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load immutable gateway module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    gateway = load_gateway(pathlib.Path(sys.argv[1]).resolve())
    declaration = {
        "model": "fixture-model",
        "tools": [
            {
                "type": "namespace",
                "name": "fixture_repo",
                "description": "Deterministic namespace fixture",
                "tools": [
                    {
                        "type": "function",
                        "name": "status",
                        "description": "Return fixture status",
                        "parameters": {"type": "object", "properties": {}},
                    }
                ],
            }
        ],
        "input": [{"role": "user", "content": "fixture"}],
    }
    original = copy.deepcopy(declaration)
    flattened, mapping = gateway.flatten_request(declaration)
    assert declaration == original
    assert flattened["tools"][0]["name"] == "fixture_repo__status"
    assert mapping == {"fixture_repo__status": ("fixture_repo", "status")}

    first_upstream = {
        "id": "resp_ticket08_a",
        "output": [
            {
                "type": "function_call",
                "id": "item_ticket08_a",
                "call_id": "call_ticket08_a",
                "name": "fixture_repo__status",
                "arguments": "{}",
                "status": "completed",
            }
        ],
    }
    first_downstream = gateway.transform_response(first_upstream, mapping)
    first_call = first_downstream["output"][0]
    assert first_call["namespace"] == "fixture_repo"
    assert first_call["name"] == "status"
    assert first_call["call_id"] == "call_ticket08_a"

    state = gateway._NamespaceState(4)
    state.remember(first_upstream["id"], mapping)
    continuation = {
        "model": "fixture-model",
        "previous_response_id": first_upstream["id"],
        "input": [
            {
                "type": "function_call_output",
                "call_id": "call_ticket08_a",
                "output": "fixture-ok",
            }
        ],
    }
    continued_flat, current = gateway.flatten_request(continuation)
    inherited = state.get(continuation["previous_response_id"])
    assert inherited is not None
    continued_mapping = gateway._merge_reconstruction(inherited, current)
    assert continued_flat["input"] == continuation["input"]

    second_upstream = {
        "id": "resp_ticket08_b",
        "previous_response_id": first_upstream["id"],
        "output": [
            {
                "type": "function_call",
                "id": "item_ticket08_b",
                "call_id": "call_ticket08_b",
                "name": "fixture_repo__status",
                "arguments": "{}",
                "status": "completed",
            }
        ],
    }
    second_downstream = gateway.transform_response(second_upstream, continued_mapping)
    second_call = second_downstream["output"][0]
    assert second_call["namespace"] == "fixture_repo"
    assert second_call["name"] == "status"
    assert second_downstream["previous_response_id"] == first_upstream["id"]

    print(
        json.dumps(
            {
                "declaration_unchanged": True,
                "flattened_name": "fixture_repo__status",
                "first_call_reconstructed": True,
                "function_output_preserved": True,
                "continuation_mapping_reused": True,
                "second_call_reconstructed": True,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
