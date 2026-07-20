#!/usr/bin/env python3
"""Exercise the production runtime adapter against disposable Hindsight 0.8.4."""

from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hindsight_memory_control_plane.http_adapter import HttpAdapter  # noqa: E402
from hindsight_memory_control_plane.broker import Broker, BrokerError  # noqa: E402
from hindsight_memory_control_plane.inventory import load_inventory  # noqa: E402
from hindsight_memory_control_plane.runtime import (  # noqa: E402
    compile_runtime_configuration,
)
from hindsight_memory_control_plane.server import (  # noqa: E402
    JsonRpcClient,
    UnixJsonRpcServer,
)


class RestartGateAdapter(HttpAdapter):
    """Hold accepted operations pending until the broker is reconstructed."""

    hold_operation_status = True

    def operation_status(self, request):
        if self.hold_operation_status:
            return {"status": "pending"}
        return super().operation_status(request)


def required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value or "\n" in value or "\r" in value:
        raise RuntimeError(f"{name} is required")
    return value


def request_json(api_url: str, api_key: str, method: str, path: str, body=None):
    payload = None
    headers = {"Authorization": f"Bearer {api_key}"}
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(api_url + path, data=payload, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except HTTPError as error:
        raise RuntimeError(
            f"disposable runtime request failed with HTTP {error.code}"
        ) from None


def wait_for_operation(adapter: HttpAdapter, operation_id: str) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        status = adapter.operation_status({"operation_id": operation_id})["status"]
        if status == "completed":
            return
        if status in {"failed", "cancelled", "not_found"}:
            raise RuntimeError(f"disposable operation ended as {status}")
        time.sleep(0.25)
    raise RuntimeError("disposable operation did not complete")


def wait_for_state(path: Path, predicate, label: str) -> dict:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if path.exists():
            state = json.loads(path.read_text(encoding="utf-8"))
            if predicate(state):
                return state
        time.sleep(0.05)
    raise RuntimeError(f"disposable broker did not reach {label}")


def runtime_configuration(inventory, api_key: str):
    return compile_runtime_configuration(
        inventory=inventory,
        profiles=("core",),
        token_resolver=lambda: api_key,
        mint_authority_resolver=lambda: "runtime-control-authority",
        token_resolver_id="HINDSIGHT_DISPOSABLE_API_KEY",
        mint_authority_resolver_id="HINDSIGHT_DISPOSABLE_MINT_AUTHORITY",
        adapter_factory=RestartGateAdapter,
        verify_adapters=True,
    )


def session_request(session_id: str) -> dict:
    return {
        "session_id": session_id,
        "companion_id": "disposable-bridge",
        "route": "codex",
    }


def start_broker(root: Path, configuration, signing_key: bytes):
    socket_path = root / "broker.sock"
    broker = Broker(
        state_dir=root / "state", signing_key=signing_key,
        routes=configuration.routes,
        policy_digest=configuration.policy_digest,
        artifact_digest=configuration.artifact_digest,
        mint_authorizer=configuration.mint_authorizer,
    )
    server = UnixJsonRpcServer(socket_path, broker)
    server.start()
    return broker, server, JsonRpcClient(socket_path)


def inventory_document(port: int, bank_id: str, root: Path) -> dict:
    bank = {"profile_id": "core", "bank_id": bank_id}
    return {
        "schema_version": 1,
        "machine": {
            "id": "disposable-runtime-contract", "base_port": port,
            "engineering_memory_enabled": True,
        },
        "archetype": {"id": "trusted-workstation"},
        "profiles": [{
            "id": "core", "slot": 0, "enabled": True,
            "host": "127.0.0.1", "data_classes": ["engineering"],
            "roles": {},
        }],
        "providers": [],
        "banks": [{
            "id": bank_id, "profile_id": "core", "data_class": "engineering",
            "authority": "authoritative", "writable": True,
        }],
        "harnesses": [{
            "id": "codex", "profile_id": "core", "home_bank": bank,
            "write_bank": bank,
        }],
        "migration": {
            "artifact_dir": str(root / "artifacts"),
            "proposal_log": str(root / "proposals.md"),
        },
        "policy": {
            "engineering_memory_enabled": True,
            "allowed_placements": {"engineering": ["local"]},
        },
    }


def main() -> None:
    api_url = required_environment("HINDSIGHT_DISPOSABLE_API_URL").rstrip("/")
    api_key = required_environment("HINDSIGHT_DISPOSABLE_API_KEY")
    bank_id = required_environment("HINDSIGHT_DISPOSABLE_BANK_ID")
    parsed = urlsplit(api_url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
        raise RuntimeError("disposable runtime contract requires a loopback HTTP API")

    with tempfile.TemporaryDirectory() as temporary:
        temporary_root = Path(temporary)
        inventory_path = temporary_root / "inventory.json"
        inventory_path.write_text(
            json.dumps(
                inventory_document(parsed.port, bank_id, temporary_root)
            ),
            encoding="utf-8",
        )
        inventory = load_inventory(inventory_path)
        if bank_id != "engineering":
            raise RuntimeError(
                "disposable runtime contract requires canonical engineering"
            )
        signing_key = b"r" * 32
        configuration = runtime_configuration(inventory, api_key)
        broker, server, client = start_broker(
            temporary_root, configuration, signing_key
        )
        active = {"broker": broker, "server": server}

        def cleanup() -> None:
            try:
                active["server"].close()
            except Exception:
                pass
            try:
                active["broker"].shutdown(timeout_seconds=0)
            except Exception:
                pass

        atexit.register(cleanup)
        work_path = temporary_root / "state" / "durable_work.json"
        capability = client.session_exchange(
            client.session_mint(
                "runtime-control-authority",
                session_request("runtime-session"),
                ttl_seconds=120,
            )["payload"]["handle"]
        )["payload"]["capability"]

        document_id = "runtime-contract-transcript"
        first_phrase = "runtime-contract-original-phrase"
        replacement_phrase = "runtime-contract-replacement-phrase"
        first_request = {
            "document_id": document_id, "epoch": 7, "checkpoint": 1,
            "content": first_phrase,
        }
        client.transcript_checkpoint(
            capability, sequence=1, action_id="first-checkpoint",
            request=first_request,
        )
        wait_for_state(
            work_path,
            lambda state: bool(state["queue"])
            and state["queue"][0]["operation_id"] is not None,
            "accepted pending operation",
        )

        server.close()
        shutdown = broker.shutdown(timeout_seconds=0)
        if shutdown["undrained"] < 1:
            raise RuntimeError("disposable restart did not preserve pending work")
        RestartGateAdapter.hold_operation_status = False
        configuration = runtime_configuration(inventory, api_key)
        broker, server, client = start_broker(
            temporary_root, configuration, signing_key
        )
        active.update({"broker": broker, "server": server})
        recovered_state = wait_for_state(
            work_path,
            lambda state: not state["queue"],
            "recovered completed operation",
        )
        if not any(
            record["watermark"] == [7, 1]
            for record in recovered_state["completed"].values()
        ):
            raise RuntimeError(
                "disposable restart lost the completed retain watermark"
            )
        replay = client.transcript_checkpoint(
            capability, sequence=1, action_id="first-checkpoint",
            request=first_request,
        )
        if replay["disposition"] != "idempotent":
            raise RuntimeError("disposable checkpoint replay was not idempotent")

        recalled = client.recall(
            capability, sequence=2, action_id="recall-first",
            request={"query": first_phrase, "limit": 10},
        )["payload"]["memories"]
        if not any(first_phrase in item.get("text", "") for item in recalled):
            raise RuntimeError("disposable transcript was not recallable")

        client.transcript_checkpoint(
            capability, sequence=3, action_id="replacement-checkpoint",
            request={
                "document_id": document_id, "epoch": 7, "checkpoint": 2,
                "content": replacement_phrase,
            },
        )
        wait_for_state(
            work_path, lambda state: not state["queue"],
            "replacement checkpoint",
        )
        recalled = client.recall(
            capability, sequence=4, action_id="recall-replacement",
            request={"query": replacement_phrase, "limit": 10},
        )["payload"]["memories"]
        texts = [item.get("text", "") for item in recalled]
        if not any(replacement_phrase in text for text in texts):
            raise RuntimeError("replacement transcript was not recallable")
        if any(first_phrase in text for text in texts):
            raise RuntimeError("replace retained stale transcript content")
        stale = client.recall(
            capability, sequence=5, action_id="recall-original-after-replace",
            request={"query": first_phrase, "limit": 10},
        )["payload"]["memories"]
        if any(first_phrase in item.get("text", "") for item in stale):
            raise RuntimeError("replace left original transcript recallable")

        outcome_phrase = "runtime-contract-distinct-outcome"
        client.retain_outcome(
            capability, sequence=6, action_id="retain-outcome",
            request={
                "document_id": document_id, "epoch": 7,
                "checkpoint": 2, "outcome": outcome_phrase,
            },
        )
        wait_for_state(
            work_path, lambda state: not state["queue"], "outcome retain"
        )
        recalled = client.recall(
            capability, sequence=7, action_id="recall-outcome",
            request={"query": outcome_phrase, "limit": 10},
        )["payload"]["memories"]
        if not any(outcome_phrase in item.get("text", "") for item in recalled):
            raise RuntimeError("separate outcome document was not recallable")

        model_id = "runtime-contract-model"
        created = request_json(
            api_url, api_key, "POST",
            f"/v1/default/banks/{bank_id}/mental-models",
            {
                "id": model_id, "name": "Runtime contract model",
                "source_query": "Summarize the runtime contract memories",
                "max_tokens": 256,
            },
        )
        adapter = configuration.routes["codex"]["adapter"]
        wait_for_operation(adapter, created["operation_id"])
        models = client.mental_model_fetch(
            capability, sequence=8, action_id="fetch-model",
            request={"model_id": model_id},
        )["payload"]["models"]
        if len(models) != 1 or models[0]["id"] != model_id or not models[0]["content"]:
            raise RuntimeError("disposable mental model was not readable")

        reflection = client.reflect(
            capability, sequence=9, action_id="reflect",
            request={
                "reflection": "What does the runtime contract establish?",
            }, timeout_seconds=30,
        )
        if (
            reflection["disposition"] != "ok"
            or not reflection["payload"]["reflection"]
            or "based_on" not in reflection["payload"]
        ):
            raise RuntimeError("disposable reflect returned no content")

        status = client.session_status(
            capability, sequence=10, action_id="status", timeout_seconds=5
        )
        completed_writes = status["payload"]["writes"]["completed"]
        if (
            not all(item["watermark"] == [7, 2] for item in completed_writes)
            or {
                item["method"] for item in completed_writes
            } != {"transcript_checkpoint", "retain_outcome"}
        ):
            raise RuntimeError("disposable completed watermarks are absent")

        for changed, expected in (
            (
                {
                    **session_request("bad-route"),
                    "route": "uncompiled",
                },
                "MINT_DENIED",
            ),
            (
                {
                    **session_request("bad-method"),
                    "methods": [*configuration.methods, "admin"],
                },
                "SCHEMA_INVALID",
            ),
        ):
            try:
                client.session_mint(
                    "runtime-control-authority", changed, ttl_seconds=30
                )
            except BrokerError as error:
                if error.code != expected:
                    raise
            else:
                raise RuntimeError("disposable route or method expansion passed")

        revoked = client.session_exchange(
            client.session_mint(
                "runtime-control-authority",
                session_request("revoked-session"),
                ttl_seconds=30,
            )["payload"]["handle"]
        )["payload"]["capability"]
        client.session_close(
            revoked, sequence=1, action_id="revoke", timeout_seconds=0
        )
        try:
            client.recall(
                revoked, sequence=2, action_id="revoked-recall",
                request={"query": "denied"},
            )
        except BrokerError as error:
            if error.code != "REVOKED":
                raise
        else:
            raise RuntimeError("disposable revoked capability remained active")

        expiring = client.session_exchange(
            client.session_mint(
                "runtime-control-authority",
                session_request("expired-session"),
                ttl_seconds=2,
            )["payload"]["handle"]
        )["payload"]["capability"]
        time.sleep(2.1)
        try:
            client.recall(
                expiring, sequence=1, action_id="expired-recall",
                request={"query": "denied"},
            )
        except BrokerError as error:
            if error.code != "EXPIRED":
                raise
        else:
            raise RuntimeError("disposable expired capability remained active")

        closed = client.session_close(
            capability, sequence=11, action_id="close", timeout_seconds=5
        )
        if closed["payload"]["undrained"] != 0:
            raise RuntimeError("disposable session closed with undrained writes")
        server.close()
        broker.shutdown(timeout_seconds=5)
        atexit.unregister(cleanup)

    print(json.dumps({
        "passed": True, "hindsight": "0.8.4", "bank": bank_id,
        "broker": "inventory-compiled",
    }))


if __name__ == "__main__":
    main()
