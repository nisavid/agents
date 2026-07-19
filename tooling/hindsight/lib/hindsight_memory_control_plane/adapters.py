"""Explicit data-plane adapter contract and deterministic in-memory test adapter."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

from .action_contracts import ACTION_METHODS, DIRECT_ACTION_KINDS
from .canonical import DIGEST, digest
from .model import BankRef, EndpointIdentity, deep_thaw


RUNTIME_SCHEMAS = {
    "recall": ({"query"}, {"limit"}),
    "mental_model_fetch": ({"model_id"}, set()),
    "session_status": ({"session_id"}, set()),
    "transcript_checkpoint": ({"document_id", "epoch", "checkpoint", "idempotency_key"}, set()),
    "retain_outcome": ({"document_id", "epoch", "checkpoint", "outcome", "idempotency_key"}, set()),
    "reflect": ({"reflection", "idempotency_key"}, set()),
}


def validate_runtime_request(method: str, request: Mapping[str, Any]) -> dict[str, Any]:
    if method not in RUNTIME_SCHEMAS or not isinstance(request, Mapping):
        raise AdapterError("runtime request schema is invalid")
    required, optional = RUNTIME_SCHEMAS[method]
    if not required <= set(request) or set(request) - required - optional:
        raise AdapterError("runtime request schema is invalid")
    value = deepcopy(dict(request))
    for key, item in value.items():
        if key in {"epoch", "checkpoint", "limit"}:
            if type(item) is not int or item < 0 or item > 1_000_000:
                raise AdapterError("runtime request schema is invalid")
        elif key == "idempotency_key":
            if not isinstance(item, str) or len(item) != 64 or any(char not in "0123456789abcdef" for char in item):
                raise AdapterError("runtime request schema is invalid")
        elif not isinstance(item, str) or not item or len(item.encode("utf-8")) > 65_536:
            raise AdapterError("runtime request schema is invalid")
    return value


class AdapterError(RuntimeError):
    """A redacted adapter failure safe to show to an operator."""


class AuthenticationError(AdapterError):
    """The endpoint rejected the resolved bearer token."""


@dataclass(frozen=True)
class RollbackBundle:
    """Opaque adapter-attested handle to an adapter-owned prestate snapshot."""

    rollback_id: str
    plan_digest: str
    action_ids: tuple[str, ...]
    prestate_digest: str
    endpoint_digest: str
    bundle_digest: str
    restore_proof_digest: str
    archive_digest: str | None = None
    restore_evidence_digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = {
            "rollback_id": self.rollback_id,
            "plan_digest": self.plan_digest,
            "action_ids": list(self.action_ids),
            "prestate_digest": self.prestate_digest,
            "endpoint_digest": self.endpoint_digest,
            "bundle_digest": self.bundle_digest,
            "restore_proof_digest": self.restore_proof_digest,
        }
        if self.archive_digest is not None:
            value["archive_digest"] = self.archive_digest
        if self.restore_evidence_digest is not None:
            value["restore_evidence_digest"] = self.restore_evidence_digest
        return value


@runtime_checkable
class Adapter(Protocol):
    def schema_version(self) -> int: ...
    def endpoint_identity(self) -> EndpointIdentity: ...
    def snapshot(self) -> Mapping[str, Any]: ...
    def read_config(self) -> Mapping[str, Any]: ...
    def read_stats(self) -> Mapping[str, Any]: ...
    def read_tags(self) -> Any: ...
    def read_scopes(self) -> Any: ...
    def read_documents(self) -> Any: ...
    def read_models(self) -> Any: ...
    def read_directives(self) -> Any: ...
    def read_operations(self) -> Mapping[str, Any]: ...
    # Opaque monotonic revision that changes on every committed live-bank mutation.
    def read_migration_generation(self) -> str: ...
    def read_migration_inventory(self, source_bank: BankRef, candidate_bank: BankRef) -> Mapping[str, Any]: ...
    def template_dry_run(self, template: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def export_template(self) -> Mapping[str, Any]: ...
    def import_template(self, template: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def patch_config(self, patch: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def upsert_model(self, model: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def upsert_directive(self, directive: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def transfer_documents(self, transfer: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def read_invalidated_memories(self) -> Any: ...
    def invalidated_memory_inventory(self) -> Any: ...
    def reapply_invalidated_memories(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def delete_bank(self, bank: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def apply_action(self, action: Any) -> None: ...
    def attest_external_action(self, action: Any, mutation: Any) -> None: ...
    def preflight_action(self, action: Any) -> None: ...
    def bind_apply_plan(self, rollback: RollbackBundle) -> None: ...
    def verify_postcondition(self, action: Any) -> bool: ...
    def create_rollback_bundle(
        self,
        plan_digest: str,
        action_ids: tuple[str, ...],
        *,
        archive_digest: str | None = None,
        restore_evidence_digest: str | None = None,
    ) -> RollbackBundle: ...
    def verify_rollback_bundle(self, rollback: RollbackBundle) -> bool: ...
    def restore(self, rollback: RollbackBundle) -> None: ...
    def disable_activation(self) -> Mapping[str, Any]: ...
    def recall(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def mental_model_fetch(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def transcript_checkpoint(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def retain_outcome(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def reflect(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def session_status(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


class FakeAdapter:
    """Adapter fake that records only operation names and bounded structural metadata."""

    def __init__(self, *, schema: int = 1, endpoint: Mapping[str, Any], state: Mapping[str, Any] | None = None,
                 operations: Mapping[str, Any] | None = None,
                 compatibility: Any = None,
                 restore_proof_valid: bool = True) -> None:
        self.schema = schema
        self.endpoint = EndpointIdentity(**dict(endpoint))
        self.state = deepcopy(dict(state or {}))
        self.operations = deepcopy(dict(operations or {"idle": True, "active": []}))
        self.compatibility = deepcopy(
            [{
                "check": "fake-adapter-contract",
                "compatible": True,
                "status": "pass",
            }] if compatibility is None else compatibility
        )
        self.restore_proof_valid = restore_proof_valid
        self.calls: list[dict[str, Any]] = []
        self.fail_postcondition_for: str | None = None
        self.fail_restore = False
        self.fail_disable_activation = False
        self.activation_enabled = True
        self._rollbacks: dict[str, dict[str, Any]] = {}
        self._runtime_results: dict[str, tuple[str, Mapping[str, Any]]] = {}
        self._migration_generation_seed = self.state.get("migration_generation")
        self._migration_generation_index = 0
        self._apply_binding: RollbackBundle | None = None
        self._apply_action_ids: list[str] = []
        self._apply_expected_state_digest: str | None = None
        self._pending_action_id: str | None = None
        self._verified_action_ids: set[str] = set()

    def _record(self, method: str, metadata: Mapping[str, Any] | None = None) -> None:
        self.calls.append({"method": method, "metadata": dict(metadata or {})})

    def _advance_migration_generation(self) -> None:
        if not isinstance(self._migration_generation_seed, str):
            return
        self._migration_generation_index += 1
        self.state["migration_generation"] = (
            f"{self._migration_generation_seed}:{self._migration_generation_index}"
        )

    @staticmethod
    def _keys(value: Mapping[str, Any]) -> dict[str, Any]:
        return {"keys": sorted(str(key) for key in value)}

    def schema_version(self) -> int:
        self._record("schema_version")
        return self.schema

    def endpoint_identity(self) -> EndpointIdentity:
        self._record("endpoint_identity")
        return self.endpoint

    def snapshot(self) -> Mapping[str, Any]:
        self._record("snapshot")
        return {
            "endpoint": self.endpoint.to_dict(),
            "state": self._rollback_state(),
            "operations": deepcopy(self.operations),
            "compatibility": deepcopy(self.compatibility),
        }

    def _read(self, name: str, default: Any) -> Any:
        self._record(f"read_{name}")
        return deepcopy(self.state.get(name, default))

    def read_config(self): return self._read("config", {})
    def read_stats(self): return self._read("stats", {})
    def read_tags(self): return self._read("tags", [])
    def read_scopes(self): return self._read("scopes", [])
    def read_documents(self): return self._read("documents", [])
    def read_models(self): return self._read("models", [])
    def read_directives(self): return self._read("directives", [])

    def read_operations(self) -> Mapping[str, Any]:
        self._record("read_operations")
        return deepcopy(self.operations)

    def read_migration_generation(self) -> str:
        self._record("read_migration_generation")
        value = self.state.get("migration_generation")
        if not isinstance(value, str) or not value:
            raise AdapterError("migration generation is unavailable")
        return value

    def read_migration_inventory(self, source_bank: BankRef, candidate_bank: BankRef) -> Mapping[str, Any]:
        if not isinstance(source_bank, BankRef) or not isinstance(candidate_bank, BankRef):
            raise AdapterError("migration inventory requires explicit bank references")
        if "migration_inventory" not in self.state:
            raise AdapterError("migration inventory is unavailable")
        self._record(
            "read_migration_inventory",
            {"source_bank": source_bank.to_dict(), "candidate_bank": candidate_bank.to_dict()},
        )
        return deepcopy(self.state["migration_inventory"])

    def template_dry_run(self, template):
        self._record("template_dry_run", self._keys(template))
        return {"valid": True, "digest": digest(template)}

    def export_template(self):
        self._record("export_template")
        return deepcopy(self.state.get("template", {}))

    def import_template(self, template):
        self._record("import_template", self._keys(template))
        self.state["template"] = deep_thaw(template)
        self._advance_migration_generation()
        return {"imported": True}

    def patch_config(self, patch):
        self._record("patch_config", self._keys(patch))
        self.state.setdefault("config", {}).update(deep_thaw(patch))
        self._advance_migration_generation()
        return deepcopy(self.state["config"])

    def _upsert(self, collection: str, value: Mapping[str, Any]):
        self._record(f"upsert_{collection[:-1]}", self._keys(value))
        identifier = value.get("id", value.get(f"{collection[:-1]}_id"))
        stored = self.state.setdefault(collection, [])
        items = stored.setdefault(collection, []) if isinstance(stored, dict) else stored
        items[:] = [item for item in items if item.get("id", item.get(f"{collection[:-1]}_id")) != identifier]
        items.append(deep_thaw(value))
        self._advance_migration_generation()
        return {"upserted": identifier}

    def upsert_model(self, model): return self._upsert("models", model)
    def upsert_directive(self, directive): return self._upsert("directives", directive)

    def transfer_documents(self, transfer):
        self._record("transfer_documents", self._keys(transfer))
        self._advance_migration_generation()
        return {"transferred": transfer.get("count", 0)}

    def read_invalidated_memories(self): return self._read("invalidated_memories", [])

    # The migration inventory contract names this read by its domain surface.
    def invalidated_memory_inventory(self): return self.read_invalidated_memories()

    def reapply_invalidated_memories(self, request):
        self._record("reapply_invalidated_memories", self._keys(request))
        self._advance_migration_generation()
        return {"reapplied": request.get("count", 0)}

    def delete_bank(self, bank):
        self._record("delete_bank", self._keys(bank))
        self._advance_migration_generation()
        return {"deleted": True}

    def apply_action(self, action) -> None:
        self._require_bound_action(action)
        details = dict(action.details)
        method_name = ACTION_METHODS.get(action.kind)
        if method_name is not None:
            getattr(self, method_name)(details)
        elif action.kind in DIRECT_ACTION_KINDS:
            self._record(action.kind, self._keys(details))
            self._advance_migration_generation()
        else:
            raise AdapterError(f"unsupported apply action: {action.kind}")
        self._apply_action_ids.pop(0)
        self._apply_expected_state_digest = digest(self._binding_snapshot())
        self._pending_action_id = action.id

    def preflight_action(self, action) -> None:
        binding = self._apply_binding
        if (
            binding is None
            or self._pending_action_id is not None
            or action.id not in self._apply_action_ids
            or digest(self.endpoint.to_dict()) != binding.endpoint_digest
            or digest(self._binding_snapshot())
            != self._apply_expected_state_digest
        ):
            raise AdapterError("action is not bound to the approved plan state")
        if (
            action.kind not in ACTION_METHODS
            and action.kind not in DIRECT_ACTION_KINDS
        ):
            raise AdapterError(f"unsupported apply action: {action.kind}")

    def _require_bound_action(self, action: Any) -> None:
        binding = self._apply_binding
        if (
            binding is None
            or self._pending_action_id is not None
            or not self._apply_action_ids
            or self._apply_action_ids[0] != action.id
            or digest(self.endpoint.to_dict()) != binding.endpoint_digest
            or digest(self._binding_snapshot())
            != self._apply_expected_state_digest
        ):
            raise AdapterError("action is not bound to the approved plan state")

    def attest_external_action(self, action: Any, mutation: Any) -> None:
        if not callable(mutation):
            raise AdapterError("external action mutation callback is required")
        self._require_bound_action(action)
        self._apply_action_ids.pop(0)
        self._pending_action_id = action.id
        mutation()
        self._apply_expected_state_digest = digest(self._binding_snapshot())

    def verify_postcondition(self, action) -> bool:
        if (
            self._pending_action_id is None
            and action.id in self._verified_action_ids
        ):
            self._record("verify_postcondition", {"action_id": action.id})
            return True
        if self._pending_action_id != action.id:
            raise AdapterError("postcondition is not bound to the applied action")
        self._record("verify_postcondition", {"action_id": action.id})
        verified = self.fail_postcondition_for != action.id
        if verified:
            self._pending_action_id = None
            self._verified_action_ids.add(action.id)
        return verified

    def _rollback_state(self) -> dict[str, Any]:
        prestate = deepcopy(self.state)
        prestate.pop("migration_generation", None)
        return prestate

    def _binding_snapshot(self) -> dict[str, Any]:
        return {
            "state": self._rollback_state(),
            "operations": deepcopy(self.operations),
        }

    def create_rollback_bundle(
        self,
        plan_digest: str,
        action_ids: tuple[str, ...],
        *,
        archive_digest: str | None = None,
        restore_evidence_digest: str | None = None,
    ) -> RollbackBundle:
        if (archive_digest is None) != (restore_evidence_digest is None) or any(
            not isinstance(value, str) or DIGEST.fullmatch(value) is None
            for value in (archive_digest, restore_evidence_digest)
            if value is not None
        ):
            raise AdapterError("rollback archive bindings are invalid")
        prestate = self._rollback_state()
        prestate_digest = digest(prestate)
        endpoint_digest = digest(self.endpoint.to_dict())
        rollback_id = f"rollback-{len(self._rollbacks) + 1}"
        body = {
            "rollback_id": rollback_id, "plan_digest": plan_digest, "action_ids": list(action_ids),
            "prestate_digest": prestate_digest, "endpoint_digest": endpoint_digest,
            "archive_digest": archive_digest,
            "restore_evidence_digest": restore_evidence_digest,
        }
        bundle_digest = digest(body)
        proof_digest = digest({"rollback_id": rollback_id, "bundle_digest": bundle_digest, "attested": "fake-adapter"})
        bundle = RollbackBundle(
            rollback_id,
            plan_digest,
            action_ids,
            prestate_digest,
            endpoint_digest,
            bundle_digest,
            proof_digest,
            archive_digest,
            restore_evidence_digest,
        )
        self._rollbacks[rollback_id] = {
            "bundle": bundle, "state": prestate,
            "binding_digest": digest(self._binding_snapshot()),
            "verifiable": self.restore_proof_valid,
            "verified": False,
        }
        self._record("create_rollback_bundle", {"action_count": len(action_ids)})
        return bundle

    def verify_rollback_bundle(self, rollback: RollbackBundle) -> bool:
        record = self._rollbacks.get(rollback.rollback_id)
        verified = bool(
            record and record["bundle"] == rollback and record["verifiable"]
        )
        if verified:
            record["verified"] = True
        self._record("verify_rollback_bundle", {"rollback_id": rollback.rollback_id})
        return verified

    def bind_apply_plan(self, rollback: RollbackBundle) -> None:
        if not isinstance(rollback, RollbackBundle) or not rollback.action_ids:
            raise AdapterError("apply plan binding is invalid")
        record = self._rollbacks.get(rollback.rollback_id)
        if (
            record is None
            or record["bundle"] != rollback
            or record["verified"] is not True
            or rollback.prestate_digest != digest(self._rollback_state())
            or record["binding_digest"] != digest(self._binding_snapshot())
            or rollback.endpoint_digest != digest(self.endpoint.to_dict())
        ):
            raise AdapterError("apply plan requires the exact verified rollback")
        if self._apply_binding is not None and (
            self._apply_action_ids or self._pending_action_id is not None
        ):
            raise AdapterError("an apply plan binding is already active")
        self._apply_binding = rollback
        self._apply_action_ids = list(rollback.action_ids)
        self._apply_expected_state_digest = record["binding_digest"]
        self._pending_action_id = None
        self._verified_action_ids.clear()
        self._record("bind_apply_plan", {"action_count": len(rollback.action_ids)})

    def restore(self, rollback: RollbackBundle):
        self._record("restore", {"rollback_id": rollback.rollback_id})
        if not self.restore_proof_valid:
            raise AdapterError("rollback restore proof is invalid")
        if self.fail_restore:
            raise AdapterError("rollback failed")
        record = self._rollbacks.get(rollback.rollback_id)
        if not record or record["bundle"] != rollback:
            raise AdapterError("rollback attestation is unknown")
        self.state = deepcopy(record["state"])
        self._advance_migration_generation()
        self._apply_binding = None
        self._apply_action_ids.clear()
        self._apply_expected_state_digest = None
        self._pending_action_id = None
        self._verified_action_ids.clear()

    def disable_activation(self) -> Mapping[str, Any]:
        self._record("disable_activation")
        if self.fail_disable_activation:
            raise AdapterError("activation disable failed")
        self.activation_enabled = False
        return {"activation_enabled": False}

    def recall(self, request):
        request = validate_runtime_request("recall", request)
        self._record("recall", self._keys(request))
        return deepcopy(self.state.get("recall", {"memories": [{"id": "m1"}]}))

    def mental_model_fetch(self, request):
        request = validate_runtime_request("mental_model_fetch", request)
        self._record("mental_model_fetch", self._keys(request))
        return deepcopy(self.state.get("mental_model_fetch", {"models": [{"id": "model1"}]}))

    def session_status(self, request):
        request = validate_runtime_request("session_status", request)
        self._record("session_status", self._keys(request))
        return deepcopy(self.state.get("session_status", {"status": "ready"}))

    def _runtime_write(self, method: str, request: Mapping[str, Any], result: Mapping[str, Any]):
        request = validate_runtime_request(method, request)
        key = request["idempotency_key"]
        request_digest = digest(request)
        if key in self._runtime_results:
            stored_digest, stored_result = self._runtime_results[key]
            if stored_digest != request_digest:
                raise AdapterError("runtime idempotency digest drift")
            return deepcopy(stored_result)
        self._record(method, self._keys(request))
        self._runtime_results[key] = (request_digest, deepcopy(result))
        self._advance_migration_generation()
        return deepcopy(result)

    def transcript_checkpoint(self, request):
        return self._runtime_write("transcript_checkpoint", request, {"applied": True})

    def retain_outcome(self, request):
        return self._runtime_write("retain_outcome", request, {"retained": True})

    def reflect(self, request):
        return self._runtime_write("reflect", request, {"accepted": True})
