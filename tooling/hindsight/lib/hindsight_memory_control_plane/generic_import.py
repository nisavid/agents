"""Controller-owned planning and execution for generic file-memory imports."""

from __future__ import annotations

import hmac
import math
import time
from typing import Any, Callable, Mapping, Sequence

from .canonical import StrictJsonError, canonical_bytes, digest, strict_json_loads
from .importing import (
    ImportPlan,
    ImportProjection,
    build_import_plan,
    import_item_digest,
    import_plan_from_dict,
    reconcile_import,
    validate_projection,
)
from .model import BankRef


TARGET_BANK = BankRef("systalyze", "engineering")


class GenericImportError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        receipt_prefix: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        super().__init__(message)
        self.receipt_prefix = tuple(receipt_prefix)


def _normalized(value: Any) -> Any:
    try:
        return strict_json_loads(canonical_bytes(value))
    except (StrictJsonError, TypeError, ValueError):
        raise GenericImportError("generic import artifact is invalid") from None


def _sha(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise GenericImportError(f"{label} is invalid")
    return value


def _identifier(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 4096
        or any(character in value for character in "\r\n\0")
    ):
        raise GenericImportError(f"{label} is invalid")
    return value


def _source_item_set(projection: ImportProjection) -> list[dict[str, str]]:
    validate_projection(projection)
    descriptors = []
    for item in sorted(projection.items, key=lambda item: item.item_id):
        descriptor = item.to_dict()
        descriptor.pop("coverage")
        descriptors.append(
            {
                "item_id": item.item_id,
                "source_descriptor_digest": digest(descriptor),
            }
        )
    return descriptors


def source_item_set_digest(projection: ImportProjection) -> str:
    return digest(_source_item_set(projection))


def _controller_identity(adapter: Any, target_bank: BankRef) -> dict[str, Any]:
    identity_reader = getattr(adapter, "inventory_identity", None)
    if not callable(identity_reader):
        raise GenericImportError("generic import controller identity is unavailable")
    inventory = _normalized(identity_reader())
    endpoint = adapter.endpoint_identity()
    if (
        not isinstance(inventory, Mapping)
        or set(inventory) != {"inventory_digest", "artifact_digest"}
        or target_bank != TARGET_BANK
        or endpoint.profile_id != target_bank.profile_id
    ):
        raise GenericImportError("generic import controller identity is invalid")
    return {
        "inventory_digest": _sha(
            inventory["inventory_digest"],
            "generic import inventory digest",
        ),
        "artifact_digest": _sha(
            inventory["artifact_digest"],
            "generic import artifact digest",
        ),
        "endpoint": endpoint.to_dict(),
    }


def _operations_snapshot(adapter: Any) -> dict[str, Any]:
    operations = _normalized(adapter.read_operations())
    if (
        not isinstance(operations, Mapping)
        or set(operations) != {"idle", "active"}
        or operations["idle"] is not True
        or operations["active"] != []
    ):
        raise GenericImportError("generic import operations are not idle")
    return {
        "operations_digest": digest(operations),
        "operations_idle": True,
    }


def _controller_snapshot(adapter: Any, target_bank: BankRef) -> dict[str, Any]:
    before = _identifier(
        adapter.read_migration_generation(),
        "generic import controller generation",
    )
    identity = _controller_identity(adapter, target_bank)
    bank_ids = adapter.list_replay_bank_ids()
    operations = _operations_snapshot(adapter)
    after = _identifier(
        adapter.read_migration_generation(),
        "generic import controller generation",
    )
    if before != after:
        raise GenericImportError(
            "generic import controller generation changed during snapshot"
        )
    if "codex" in bank_ids or target_bank.bank_id not in bank_ids:
        raise GenericImportError("generic import migration gate is open")
    return {
        **identity,
        **operations,
        "target_bank": target_bank.to_dict(),
        "target_generation": before,
        "bank_set_digest": digest(sorted(bank_ids)),
    }


def _controller_snapshot_matches(
    live: Mapping[str, Any],
    bound: Mapping[str, Any],
    *,
    expected_generation: str,
) -> bool:
    return (
        live.get("target_generation") == expected_generation
        and {
            key: value
            for key, value in live.items()
            if key != "target_generation"
        }
        == {
            key: value
            for key, value in bound.items()
            if key != "target_generation"
        }
    )


def _verify_controller_snapshot(value: Any) -> Mapping[str, Any]:
    snapshot = _normalized(value)
    if (
        not isinstance(snapshot, Mapping)
        or set(snapshot)
        != {
            "inventory_digest",
            "artifact_digest",
            "endpoint",
            "operations_digest",
            "operations_idle",
            "target_bank",
            "target_generation",
            "bank_set_digest",
        }
        or snapshot["target_bank"]
        != TARGET_BANK.to_dict()
        or snapshot["operations_idle"] is not True
        or not isinstance(snapshot["endpoint"], Mapping)
        or set(snapshot["endpoint"])
        != {"profile_id", "scheme", "host", "port", "tenant"}
        or snapshot["endpoint"]["profile_id"] != TARGET_BANK.profile_id
        or not isinstance(snapshot["endpoint"]["scheme"], str)
        or not isinstance(snapshot["endpoint"]["host"], str)
        or type(snapshot["endpoint"]["port"]) is not int
        or not 1 <= snapshot["endpoint"]["port"] <= 65535
        or not isinstance(snapshot["endpoint"]["tenant"], str)
    ):
        raise GenericImportError("generic import controller snapshot is invalid")
    for key in (
        "inventory_digest",
        "artifact_digest",
        "operations_digest",
        "bank_set_digest",
    ):
        _sha(snapshot[key], f"generic import controller {key}")
    _identifier(
        snapshot["target_generation"],
        "generic import target generation",
    )
    return snapshot


def inspect_import_target(
    adapter: Any,
    projection: ImportProjection,
    target_bank: BankRef,
) -> Mapping[str, Any]:
    validate_projection(projection)
    if (
        not isinstance(target_bank, BankRef)
        or target_bank != TARGET_BANK
    ):
        raise GenericImportError("generic import target bank is invalid")
    before = _controller_snapshot(adapter, target_bank)
    document_ids = adapter.list_replay_document_ids(target_bank)
    content_documents: dict[str, list[str]] = {}
    for document_id in document_ids:
        document = adapter.read_replay_document(target_bank, document_id)
        content_digest = digest(document["original_text"])
        content_documents.setdefault(content_digest, []).append(document_id)
    after = _controller_snapshot(adapter, target_bank)
    if before != after:
        raise GenericImportError(
            "controller state changed during generic import inspection"
        )
    source_descriptors = {
        descriptor["item_id"]: descriptor["source_descriptor_digest"]
        for descriptor in _source_item_set(projection)
    }
    findings = [
        {
            "item_id": item.item_id,
            "source_descriptor_digest": source_descriptors[item.item_id],
            "content_digest": item.content_digest,
            "status": (
                "exact_duplicate"
                if item.content_digest in content_documents
                else "novelty_candidate"
            ),
            "target_document_ids": sorted(
                content_documents.get(item.content_digest, ())
            ),
        }
        for item in sorted(projection.items, key=lambda item: item.item_id)
    ]
    body = {
        "schema_version": 1,
        "kind": "generic-import-inspection",
        "projection_digest": projection.projection_digest,
        "source_item_set_digest": source_item_set_digest(projection),
        "controller_snapshot": before,
        "target_document_set_digest": digest(sorted(document_ids)),
        "findings": findings,
    }
    return _normalized({**body, "inspection_digest": digest(body)})


def verify_import_inspection(
    value: Mapping[str, Any],
) -> Mapping[str, Any]:
    expected = {
        "schema_version",
        "kind",
        "projection_digest",
        "source_item_set_digest",
        "controller_snapshot",
        "target_document_set_digest",
        "findings",
        "inspection_digest",
    }
    inspection = _normalized(value)
    if (
        not isinstance(inspection, Mapping)
        or set(inspection) != expected
        or type(inspection["schema_version"]) is not int
        or inspection["schema_version"] != 1
        or inspection["kind"] != "generic-import-inspection"
        or not isinstance(inspection["findings"], list)
    ):
        raise GenericImportError("generic import inspection is invalid")
    controller_snapshot = _verify_controller_snapshot(
        inspection["controller_snapshot"]
    )
    for key in (
        "projection_digest",
        "source_item_set_digest",
        "target_document_set_digest",
        "inspection_digest",
    ):
        _sha(inspection[key], f"generic import {key}")
    _identifier(
        controller_snapshot["target_generation"],
        "generic import target generation",
    )
    item_ids: list[str] = []
    for finding in inspection["findings"]:
        if (
            not isinstance(finding, Mapping)
            or set(finding)
            != {
                "item_id",
                "source_descriptor_digest",
                "content_digest",
                "status",
                "target_document_ids",
            }
            or finding["status"] not in {
                "exact_duplicate",
                "novelty_candidate",
            }
            or not isinstance(finding["target_document_ids"], list)
            or finding["target_document_ids"]
            != sorted(finding["target_document_ids"])
            or len(finding["target_document_ids"])
            != len(set(finding["target_document_ids"]))
            or (
                finding["status"] == "exact_duplicate"
                and not finding["target_document_ids"]
            )
            or (
                finding["status"] == "novelty_candidate"
                and finding["target_document_ids"]
            )
        ):
            raise GenericImportError("generic import finding is invalid")
        item_ids.append(_sha(finding["item_id"], "generic import item identity"))
        _sha(
            finding["source_descriptor_digest"],
            "generic import source descriptor digest",
        )
        _sha(finding["content_digest"], "generic import content digest")
        for document_id in finding["target_document_ids"]:
            _identifier(document_id, "generic import target document")
    if item_ids != sorted(item_ids) or len(item_ids) != len(set(item_ids)):
        raise GenericImportError("generic import findings are not canonical")
    body = {
        key: inspection[key]
        for key in expected - {"inspection_digest"}
    }
    if not hmac.compare_digest(
        digest(body),
        inspection["inspection_digest"],
    ):
        raise GenericImportError(
            "generic import inspection digest does not match"
        )
    return inspection


def _verify_closeout_receipt(value: Mapping[str, Any]) -> Mapping[str, Any]:
    expected = {
        "schema_version",
        "closeout_plan_digest",
        "deleted_bank_id",
        "deleted_count",
        "pre_delete_generation",
        "post_delete_generation",
        "remaining_bank_set_digest",
        "cleanup_status",
        "closeout_receipt_digest",
    }
    receipt = _normalized(value)
    if (
        not isinstance(receipt, Mapping)
        or set(receipt) != expected
        or type(receipt["schema_version"]) is not int
        or receipt["schema_version"] != 1
        or receipt["deleted_bank_id"] != "codex"
        or type(receipt["deleted_count"]) is not int
        or receipt["deleted_count"] < 0
        or receipt["cleanup_status"] not in {
            "completed",
            "degraded",
            "deferred",
        }
    ):
        raise GenericImportError("replay closeout receipt is invalid")
    for key in (
        "closeout_plan_digest",
        "remaining_bank_set_digest",
        "closeout_receipt_digest",
    ):
        _sha(receipt[key], f"replay closeout {key}")
    _identifier(
        receipt["pre_delete_generation"],
        "replay pre-delete generation",
    )
    _identifier(
        receipt["post_delete_generation"],
        "replay post-delete generation",
    )
    body = {
        key: receipt[key]
        for key in expected - {"closeout_receipt_digest"}
    }
    if digest(body) != receipt["closeout_receipt_digest"]:
        raise GenericImportError("replay closeout receipt digest does not match")
    return receipt


def _verify_backup_evidence(
    value: Mapping[str, Any],
    *,
    target_bank: BankRef,
    target_generation: str,
) -> Mapping[str, Any]:
    expected = {
        "schema_version",
        "kind",
        "target_bank",
        "target_generation",
        "backup_digest",
        "restore_tested",
    }
    evidence = _normalized(value)
    if (
        not isinstance(evidence, Mapping)
        or set(evidence) != expected
        or type(evidence["schema_version"]) is not int
        or evidence["schema_version"] != 1
        or evidence["kind"] != "generic-import-backup"
        or evidence["target_bank"] != target_bank.to_dict()
        or evidence["target_generation"] != target_generation
        or evidence["restore_tested"] is not True
    ):
        raise GenericImportError("generic import backup evidence is invalid")
    _sha(evidence["backup_digest"], "generic import backup digest")
    return evidence


def create_generic_import_plan(
    adapter: Any,
    projection: ImportProjection,
    inspection_value: Mapping[str, Any],
    closeout_receipt_value: Mapping[str, Any],
    backup_evidence_value: Mapping[str, Any],
) -> Mapping[str, Any]:
    validate_projection(projection)
    inspection = verify_import_inspection(inspection_value)
    target_bank = BankRef(
        inspection["controller_snapshot"]["target_bank"]["profile_id"],
        inspection["controller_snapshot"]["target_bank"]["bank_id"],
    )
    if source_item_set_digest(projection) != inspection["source_item_set_digest"]:
        raise GenericImportError(
            "reviewed projection does not match target inspection"
        )
    findings = {
        finding["item_id"]: finding for finding in inspection["findings"]
    }
    source_descriptors = {
        descriptor["item_id"]: descriptor["source_descriptor_digest"]
        for descriptor in _source_item_set(projection)
    }
    if set(findings) != {item.item_id for item in projection.items}:
        raise GenericImportError(
            "reviewed projection coverage does not match target inspection"
        )
    for item in projection.items:
        finding = findings[item.item_id]
        if (
            item.content_digest != finding["content_digest"]
            or source_descriptors[item.item_id]
            != finding["source_descriptor_digest"]
        ):
            raise GenericImportError(
                "reviewed projection source does not match target inspection"
            )
        if (
            finding["status"] == "exact_duplicate"
            and item.coverage_disposition != "omitted"
        ):
            raise GenericImportError(
                "exact target duplicates must be explicitly omitted"
            )
        if (
            finding["status"] == "novelty_candidate"
            and item.coverage_disposition
            not in {"proposed_novel", "omitted"}
        ):
            raise GenericImportError(
                "generic import review remains unresolved"
            )
    closeout = _verify_closeout_receipt(closeout_receipt_value)
    backup = _verify_backup_evidence(
        backup_evidence_value,
        target_bank=target_bank,
        target_generation=inspection["controller_snapshot"]["target_generation"],
    )
    live_snapshot = _controller_snapshot(adapter, target_bank)
    if live_snapshot != inspection["controller_snapshot"]:
        raise GenericImportError(
            "generic import inspection controller state drifted"
        )
    controller_binding = {
        "schema_version": 1,
        "kind": "generic-import-controller-binding",
        "controller_snapshot": live_snapshot,
        "inspection_digest": inspection["inspection_digest"],
        "source_item_set_digest": inspection["source_item_set_digest"],
        "closeout_receipt_digest": closeout["closeout_receipt_digest"],
        "backup_evidence_digest": digest(backup),
    }
    controller_plan_digest = digest(controller_binding)
    import_plan = build_import_plan(
        projection,
        controller_plan_digest=controller_plan_digest,
        target_bank=target_bank,
    )
    body = {
        "schema_version": 1,
        "kind": "generic-import-plan",
        "source_item_set_digest": inspection["source_item_set_digest"],
        "inspection_digest": inspection["inspection_digest"],
        "target_generation": live_snapshot["target_generation"],
        "closeout_receipt_digest": closeout["closeout_receipt_digest"],
        "backup_evidence_digest": digest(backup),
        "controller_binding": controller_binding,
        "import_plan": import_plan.to_dict(),
    }
    return _normalized({**body, "plan_digest": digest(body)})


def verify_generic_import_plan(
    value: Mapping[str, Any],
) -> Mapping[str, Any]:
    expected = {
        "schema_version",
        "kind",
        "source_item_set_digest",
        "inspection_digest",
        "target_generation",
        "closeout_receipt_digest",
        "backup_evidence_digest",
        "controller_binding",
        "import_plan",
        "plan_digest",
    }
    plan = _normalized(value)
    if (
        not isinstance(plan, Mapping)
        or set(plan) != expected
        or type(plan["schema_version"]) is not int
        or plan["schema_version"] != 1
        or plan["kind"] != "generic-import-plan"
        or not isinstance(plan["controller_binding"], Mapping)
        or not isinstance(plan["import_plan"], Mapping)
    ):
        raise GenericImportError("generic import plan is invalid")
    for key in (
        "source_item_set_digest",
        "inspection_digest",
        "closeout_receipt_digest",
        "backup_evidence_digest",
        "plan_digest",
    ):
        _sha(plan[key], f"generic import plan {key}")
    _identifier(plan["target_generation"], "generic import plan generation")
    import_plan = import_plan_from_dict(plan["import_plan"])
    binding = plan["controller_binding"]
    if (
        set(binding)
        != {
            "schema_version",
            "kind",
            "controller_snapshot",
            "inspection_digest",
            "source_item_set_digest",
            "closeout_receipt_digest",
            "backup_evidence_digest",
        }
        or type(binding["schema_version"]) is not int
        or binding["schema_version"] != 1
        or binding["kind"] != "generic-import-controller-binding"
        or binding["inspection_digest"] != plan["inspection_digest"]
        or binding["source_item_set_digest"] != plan["source_item_set_digest"]
        or binding["closeout_receipt_digest"] != plan["closeout_receipt_digest"]
        or binding["backup_evidence_digest"] != plan["backup_evidence_digest"]
        or _verify_controller_snapshot(
            binding["controller_snapshot"]
        )["target_generation"]
        != plan["target_generation"]
    ):
        raise GenericImportError("generic import controller binding is invalid")
    if import_plan.controller_plan_digest != digest(binding):
        raise GenericImportError(
            "generic import controller plan digest does not match"
        )
    body = {key: plan[key] for key in expected - {"plan_digest"}}
    if digest(body) != plan["plan_digest"]:
        raise GenericImportError("generic import plan digest does not match")
    return plan


def _target_document_id(item_id: str) -> str:
    return f"generic-import:{_sha(item_id, 'generic import item identity')}"


def _expected_metadata(
    plan: Mapping[str, Any],
    item: Any,
) -> dict[str, str]:
    return {
        "generic_import_plan_digest": plan["plan_digest"],
        "generic_import_item_id": item.item_id,
        "generic_import_item_digest": import_item_digest(item),
        "generic_import_source_kind": item.source_kind,
        "generic_import_source_locator": item.provenance["source_locator"],
        "generic_import_source_native_id": item.source_native_id,
        "generic_import_source_line_start": str(
            item.provenance["line_start"]
        ),
        "generic_import_source_line_end": str(item.provenance["line_end"]),
        "generic_import_relationships": canonical_bytes(
            list(item.relationships)
        ).decode("utf-8"),
        "generic_import_scope": item.intended_scope,
    }


def _target_projection(
    document: Mapping[str, Any],
    target_document_id: str,
) -> dict[str, Any]:
    retain_params = document.get("retain_params") or {}
    if not isinstance(retain_params, Mapping):
        raise GenericImportError(
            "generic import target retain parameters are invalid"
        )
    return {
        "target_document_id": target_document_id,
        "content_digest": digest(document["original_text"]),
        "timestamp": retain_params.get("event_date"),
        "metadata": document.get("document_metadata"),
        "tags": sorted(document["tags"]),
    }


def _expected_target_projection(
    plan: Mapping[str, Any],
    item: Any,
) -> dict[str, Any]:
    return {
        "target_document_id": _target_document_id(item.item_id),
        "content_digest": item.content_digest,
        "timestamp": item.timestamp,
        "metadata": _expected_metadata(plan, item),
        "tags": sorted(item.tags),
    }


def _require_exact_import_plan(
    plan: Mapping[str, Any],
    projection: ImportProjection,
) -> ImportPlan:
    import_plan = import_plan_from_dict(plan["import_plan"])
    expected = build_import_plan(
        projection,
        controller_plan_digest=import_plan.controller_plan_digest,
        target_bank=import_plan.target_bank,
    )
    if expected.to_dict() != import_plan.to_dict():
        raise GenericImportError(
            "generic import actions do not match reviewed projection"
        )
    return import_plan


def _validate_live_receipts(
    adapter: Any,
    plan: Mapping[str, Any],
    projection: ImportProjection,
    receipts: Sequence[Mapping[str, Any]],
) -> None:
    import_plan = _require_exact_import_plan(plan, projection)
    item_by_id = {item.item_id: item for item in projection.items}
    for receipt in receipts:
        item = item_by_id.get(receipt["item_id"])
        if item is None:
            raise GenericImportError("generic import receipt item is unavailable")
        operation = adapter.read_replay_operation(
            import_plan.target_bank,
            receipt["operation_id"],
        )
        document = adapter.find_replay_document(
            import_plan.target_bank,
            receipt["target_document_id"],
        )
        if (
            operation.get("status") != "completed"
            or document is None
        ):
            raise GenericImportError("generic import receipt target does not match")
        target = _target_projection(document, receipt["target_document_id"])
        if (
            target != _expected_target_projection(plan, item)
            or digest(target) != receipt["target_projection_digest"]
        ):
            raise GenericImportError("generic import receipt target does not match")


def _receipt_status(
    plan: Mapping[str, Any],
    receipts: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    import_plan = import_plan_from_dict(plan["import_plan"])
    actions = list(import_plan.actions)
    if len(receipts) > len(actions):
        raise GenericImportError("generic import receipt prefix is invalid")
    verified: list[Mapping[str, Any]] = []
    previous_generation = plan["target_generation"]
    operation_ids: set[str] = set()
    for action, receipt in zip(actions, receipts, strict=False):
        expected = {
            "item_id",
            "item_digest",
            "target_document_id",
            "operation_id",
            "pre_generation",
            "post_generation",
            "target_projection_digest",
            "plan_digest",
            "receipt_digest",
        }
        if (
            not isinstance(receipt, Mapping)
            or set(receipt) != expected
            or receipt["item_id"] != action["item_id"]
            or receipt["item_digest"] != action["item_digest"]
            or receipt["target_document_id"]
            != _target_document_id(action["item_id"])
            or receipt["plan_digest"] != plan["plan_digest"]
            or receipt["pre_generation"] != previous_generation
            or receipt["post_generation"] == receipt["pre_generation"]
            or receipt["operation_id"] in operation_ids
        ):
            raise GenericImportError("generic import receipt is invalid")
        for key in ("item_id", "item_digest", "target_projection_digest"):
            _sha(receipt[key], f"generic import receipt {key}")
        _identifier(receipt["operation_id"], "generic import operation")
        _identifier(receipt["post_generation"], "generic import generation")
        body = {
            key: receipt[key] for key in expected - {"receipt_digest"}
        }
        if digest(body) != receipt["receipt_digest"]:
            raise GenericImportError(
                "generic import receipt digest does not match"
            )
        previous_generation = receipt["post_generation"]
        operation_ids.add(receipt["operation_id"])
        verified.append(_normalized(receipt))
    body = {
        "schema_version": 1,
        "plan_digest": plan["plan_digest"],
        "planned_item_count": len(actions),
        "completed_item_count": len(verified),
        "complete": len(verified) == len(actions),
        "receipt_set_digest": digest(verified),
        "current_generation": previous_generation,
    }
    return _normalized({**body, "status_digest": digest(body)})


def generic_import_status(
    plan_value: Mapping[str, Any],
    receipts: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    plan = verify_generic_import_plan(plan_value)
    return _receipt_status(plan, receipts)


def apply_generic_import_plan(
    adapter: Any,
    plan_value: Mapping[str, Any],
    projection: ImportProjection,
    closeout_receipt_value: Mapping[str, Any],
    backup_evidence_value: Mapping[str, Any],
    *,
    approval_digest: str,
    existing_receipts: Sequence[Mapping[str, Any]] = (),
    timeout_seconds: float = 1800,
    poll_interval_seconds: float = 1,
    receipt_writer: Callable[[Sequence[Mapping[str, Any]]], None] | None = None,
) -> list[Mapping[str, Any]]:
    plan = verify_generic_import_plan(plan_value)
    if not hmac.compare_digest(approval_digest, plan["plan_digest"]):
        raise GenericImportError(
            "exact digest-bound generic import approval is required"
        )
    validate_projection(projection)
    import_plan = import_plan_from_dict(plan["import_plan"])
    if (
        projection.projection_digest != import_plan.projection_digest
        or source_item_set_digest(projection) != plan["source_item_set_digest"]
    ):
        raise GenericImportError(
            "generic import plan does not match reviewed projection"
        )
    closeout = _verify_closeout_receipt(closeout_receipt_value)
    if closeout["closeout_receipt_digest"] != plan["closeout_receipt_digest"]:
        raise GenericImportError(
            "generic import replay closeout authority does not match"
        )
    backup = _verify_backup_evidence(
        backup_evidence_value,
        target_bank=import_plan.target_bank,
        target_generation=plan["target_generation"],
    )
    if digest(backup) != plan["backup_evidence_digest"]:
        raise GenericImportError(
            "generic import backup evidence does not match"
        )
    if (
        type(timeout_seconds) not in (int, float)
        or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= 86400
        or type(poll_interval_seconds) not in (int, float)
        or not math.isfinite(poll_interval_seconds)
        or not 0 <= poll_interval_seconds <= 60
    ):
        raise GenericImportError("generic import wait policy is invalid")
    receipts = list(existing_receipts)
    status = _receipt_status(plan, receipts)
    live_snapshot = _controller_snapshot(adapter, import_plan.target_bank)
    if not _controller_snapshot_matches(
        live_snapshot,
        plan["controller_binding"]["controller_snapshot"],
        expected_generation=status["current_generation"],
    ):
        raise GenericImportError("generic import controller authority drifted")
    live_generation = live_snapshot["target_generation"]
    if live_generation != status["current_generation"]:
        raise GenericImportError("generic import target generation drifted")
    _validate_live_receipts(adapter, plan, projection, receipts)
    if adapter.read_migration_generation() != live_generation:
        raise GenericImportError(
            "generic import generation changed during receipt validation"
        )
    item_by_id = {item.item_id: item for item in projection.items}
    for action in list(import_plan.actions)[len(receipts):]:
        item = item_by_id[action["item_id"]]
        target_document_id = _target_document_id(item.item_id)
        pre_generation = _identifier(
            adapter.read_migration_generation(),
            "generic import pre-generation",
        )
        if pre_generation != live_generation:
            raise GenericImportError(
                "generic import target generation changed between items"
            )
        if (
            adapter.find_replay_document(
                import_plan.target_bank,
                target_document_id,
            )
            is not None
        ):
            raise GenericImportError(
                "unexpected generic import target collision"
            )
        if adapter.read_migration_generation() != pre_generation:
            raise GenericImportError(
                "generic import target generation changed before submit"
            )
        submission = adapter.submit_generic_import_document(
            import_plan.target_bank,
            {
                "content": item.content,
                "document_id": target_document_id,
                "timestamp": item.timestamp,
                "metadata": _expected_metadata(plan, item),
                "tags": list(item.tags),
            },
            expected_generation=pre_generation,
            expected_bank_set_digest=plan["controller_binding"][
                "controller_snapshot"
            ]["bank_set_digest"],
            plan_digest=plan["plan_digest"],
            item_digest=import_item_digest(item),
        )
        operation_id = _identifier(
            submission.get("operation_id"),
            "generic import operation",
        )
        deadline = time.monotonic() + float(timeout_seconds)
        while True:
            try:
                operation = adapter.read_replay_operation(
                    import_plan.target_bank,
                    operation_id,
                )
            except Exception as error:
                classifier = getattr(
                    adapter,
                    "replay_operation_status_error_is_transient",
                    None,
                )
                transient = False
                if callable(classifier):
                    try:
                        transient = classifier(error) is True
                    except Exception:
                        transient = False
                if not transient:
                    raise
                if time.monotonic() >= deadline:
                    raise GenericImportError(
                        "generic import operation timed out"
                    ) from error
                time.sleep(
                    min(
                        max(float(poll_interval_seconds), 0.01),
                        max(0.0, deadline - time.monotonic()),
                    )
                )
                continue
            status_name = operation.get("status")
            if status_name == "completed":
                break
            if status_name in {"failed", "cancelled", "not_found"}:
                raise GenericImportError("generic import operation failed")
            if status_name not in {"pending", "processing"}:
                raise GenericImportError(
                    "generic import operation status is invalid"
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GenericImportError("generic import operation timed out")
            time.sleep(
                min(
                    max(float(poll_interval_seconds), 0.01),
                    remaining,
                )
            )
        document = adapter.read_replay_document(
            import_plan.target_bank,
            target_document_id,
        )
        target_projection = _target_projection(document, target_document_id)
        if target_projection != _expected_target_projection(plan, item):
            raise GenericImportError(
                "generic import target projection does not match"
            )
        post_generation = _identifier(
            adapter.read_migration_generation(),
            "generic import post-generation",
        )
        if post_generation == pre_generation:
            raise GenericImportError(
                "generic import generation did not advance"
            )
        body = {
            "item_id": item.item_id,
            "item_digest": import_item_digest(item),
            "target_document_id": target_document_id,
            "operation_id": operation_id,
            "pre_generation": pre_generation,
            "post_generation": post_generation,
            "target_projection_digest": digest(target_projection),
            "plan_digest": plan["plan_digest"],
        }
        receipts.append(_normalized({**body, "receipt_digest": digest(body)}))
        if receipt_writer is not None:
            try:
                receipt_writer(tuple(receipts))
            except Exception:
                raise GenericImportError(
                    "generic import receipt checkpoint failed",
                    receipt_prefix=receipts,
                ) from None
        live_generation = post_generation
    return receipts


def verify_generic_import_receipts(
    adapter: Any,
    plan_value: Mapping[str, Any],
    projection: ImportProjection,
    receipts: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    plan = verify_generic_import_plan(plan_value)
    validate_projection(projection)
    import_plan: ImportPlan = _require_exact_import_plan(plan, projection)
    if (
        projection.projection_digest != import_plan.projection_digest
        or source_item_set_digest(projection) != plan["source_item_set_digest"]
    ):
        raise GenericImportError(
            "generic import verification projection does not match"
        )
    status = _receipt_status(plan, receipts)
    if not status["complete"]:
        raise GenericImportError("generic import receipt coverage is incomplete")
    live_snapshot = _controller_snapshot(adapter, import_plan.target_bank)
    if not _controller_snapshot_matches(
        live_snapshot,
        plan["controller_binding"]["controller_snapshot"],
        expected_generation=status["current_generation"],
    ):
        raise GenericImportError(
            "generic import verification authority drifted"
        )
    live_generation = live_snapshot["target_generation"]
    item_by_id = {item.item_id: item for item in projection.items}
    for receipt in receipts:
        item = item_by_id[receipt["item_id"]]
        operation = adapter.read_replay_operation(
            import_plan.target_bank,
            receipt["operation_id"],
        )
        document = adapter.read_replay_document(
            import_plan.target_bank,
            receipt["target_document_id"],
        )
        if not isinstance(document, Mapping):
            raise GenericImportError(
                "generic import processing verification failed"
            )
        target_projection = _target_projection(
            document,
            receipt["target_document_id"],
        )
        memory_unit_count = document.get("memory_unit_count", 0)
        if (
            operation.get("status") != "completed"
            or target_projection != _expected_target_projection(plan, item)
            or digest(target_projection)
            != receipt["target_projection_digest"]
            or type(memory_unit_count) is not int
            or memory_unit_count < 1
        ):
            raise GenericImportError(
                "generic import processing verification failed"
            )
    if receipts:
        processing_evidence = _normalized(
            adapter.read_generic_import_processing_evidence(
                import_plan.target_bank,
                sorted(
                    receipt["target_document_id"] for receipt in receipts
                ),
            )
        )
    else:
        empty_processing_body = {
            "schema_version": 1,
            "generation_before": live_generation,
            "generation_after": live_generation,
            "documents": [],
            "representative_recall": None,
        }
        processing_evidence = {
            **empty_processing_body,
            "processing_evidence_digest": digest(empty_processing_body),
        }
    if (
        not isinstance(processing_evidence, Mapping)
        or set(processing_evidence)
        != {
            "schema_version",
            "generation_before",
            "generation_after",
            "documents",
            "representative_recall",
            "processing_evidence_digest",
        }
        or type(processing_evidence["schema_version"]) is not int
        or processing_evidence["schema_version"] != 1
        or processing_evidence["generation_before"] != live_generation
        or processing_evidence["generation_after"] != live_generation
        or not isinstance(processing_evidence["documents"], list)
        or (
            receipts
            and not isinstance(
                processing_evidence["representative_recall"],
                Mapping,
            )
        )
        or (
            not receipts
            and processing_evidence["representative_recall"] is not None
        )
    ):
        raise GenericImportError(
            "generic import processing evidence is invalid"
        )
    processing_body = {
        key: processing_evidence[key]
        for key in processing_evidence
        if key != "processing_evidence_digest"
    }
    if (
        _sha(
            processing_evidence["processing_evidence_digest"],
            "generic import processing evidence digest",
        )
        != digest(processing_body)
    ):
        raise GenericImportError(
            "generic import processing evidence digest does not match"
        )
    representative = processing_evidence["representative_recall"]
    if receipts:
        if (
            set(representative)
            != {
                "target_document_id",
                "query_digest",
                "result_count",
                "result_projection_digest",
            }
            or representative["target_document_id"]
            not in {
                receipt["target_document_id"] for receipt in receipts
            }
            or type(representative["result_count"]) is not int
            or representative["result_count"] < 1
        ):
            raise GenericImportError(
                "generic import semantic evidence is invalid"
            )
        _sha(
            representative["query_digest"],
            "generic import recall query digest",
        )
        _sha(
            representative["result_projection_digest"],
            "generic import recall result digest",
        )
    if live_generation != status["current_generation"]:
        raise GenericImportError(
            "generic import generation changed before verification"
        )
    after_evidence_generation = _identifier(
        adapter.read_migration_generation(),
        "generic import post-evidence generation",
    )
    if after_evidence_generation != live_generation:
        raise GenericImportError(
            "generic import generation changed during verification"
        )
    reconciliation_receipts = [
        {
            "item_id": receipt["item_id"],
            "item_digest": receipt["item_digest"],
            "status": "imported",
            "import_plan_digest": import_plan.plan_digest,
            "target_bank": import_plan.target_bank.to_dict(),
        }
        for receipt in receipts
    ]
    reconciliation = reconcile_import(
        projection,
        import_plan,
        reconciliation_receipts,
        approved_plan_digest=import_plan.plan_digest,
    )
    if not reconciliation.complete:
        raise GenericImportError("generic import reconciliation is incomplete")
    body = {
        "schema_version": 1,
        "plan_digest": plan["plan_digest"],
        "projection_digest": projection.projection_digest,
        "receipt_set_digest": status["receipt_set_digest"],
        "verified_item_count": status["completed_item_count"],
        "verification_generation": live_generation,
        "processing_evidence_digest":
            processing_evidence["processing_evidence_digest"],
        "reconciliation_digest": reconciliation.reconciliation_digest,
        "complete": True,
    }
    return _normalized({**body, "verification_digest": digest(body)})
