"""Read-only migration discovery and immutable, unapproved shadow planning."""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
from datetime import datetime
import errno
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any, Callable, Mapping, Sequence

from .adapters import AdapterError
from .canonical import StrictJsonError, canonical_bytes, digest
from .file_evidence import FileEvidenceError, read_file_evidence, reject_symlink_components
from .model import BankRef, deep_freeze, deep_thaw
from .planning import PlanError, _endpoint
from .policy import AGENT_TAGS, KIND_TAGS, LIFECYCLE_TAGS, SOURCE_TAGS


class MigrationError(ValueError):
    pass


DIGEST = re.compile(r"[0-9a-f]{64}\Z")
TIMESTAMP = re.compile(r"[0-9]{8}T[0-9]{6}Z\Z")
SEMANTIC_SCOPE = re.compile(
    r"(?:global|personal|repo:[a-z0-9][a-z0-9-]*|"
    r"workflow:[a-z0-9][a-z0-9._-]*)\Z"
)
CANONICAL_REPOSITORY_TAG = re.compile(r"repo:[a-z0-9][a-z0-9-]*\Z")
REPOSITORY_ALIAS = re.compile(r"(?:[a-z][a-z0-9-]*:)?[a-z0-9][a-z0-9-]*\Z")
SNAPSHOT_KEYS = {
    "schema_version",
    "endpoint",
    "provider_identity",
    "versions",
    "banks",
    "operations",
    "hooks",
    "schedules",
    "retain_watermarks",
}
BANK_KEYS = {
    "bank_ref",
    "config",
    "stats",
    "scopes",
    "scopes_digest",
    "tags",
    "documents",
    "models",
    "directives",
    "invalidated_memories",
}
DOCUMENT_REQUIRED_KEYS = {"document_id", "updated_at", "content_digest"}
DOCUMENT_OPTIONAL_KEYS = {
    "created_at",
    "text_length",
    "memory_unit_count",
    "tags",
    "document_metadata",
    "retain_params",
}
DOCUMENT_KEYS = DOCUMENT_REQUIRED_KEYS | DOCUMENT_OPTIONAL_KEYS
INVALIDATED_MEMORY_KEYS = {
    "item_id",
    "source_document_id",
    "reason_digest",
    "content_digest",
}
CONTENT_RECORD_KEYS = frozenset({"content_digest"})
MODEL_KEYS = frozenset({"model_id", "content_digest"})
DIRECTIVE_KEYS = frozenset({"directive_id", "content_digest"})
HOOK_KEYS = frozenset({
    "bank_role", "hook_id", "registration_digest", "registration",
})
HOOK_REGISTRATION_KEYS = frozenset({
    "target_digest", "activation_digest", "config_digest",
})
SCHEDULE_KEYS = frozenset({"bank_role", "model_id", "trigger_digest"})
STATIC_MIGRATION_TAGS = AGENT_TAGS | SOURCE_TAGS | LIFECYCLE_TAGS | KIND_TAGS
CATALOG_TAG = re.compile(r"(?:repo|workflow):[a-z0-9][a-z0-9-]*\Z")
PACKAGE_KEYS = {
    "schema_version",
    "approved_manifest_digest",
    "artifact_digest",
    "projection_digest",
    "tag_mapping_digest",
    "candidate_provenance_digest",
    "candidate_curation_digest",
    "invalidation_dispositions",
}
COVERAGE_KEYS = {
    "bank_role", "item_id", "content_digest", "disposition", "reason",
    "semantic_scope",
}
INVALIDATION_KEYS = {
    "bank_role", "item_id", "disposition", "reason",
    "reapply_content_digest",
}
PLAN_KEYS = {
    "schema_version",
    "kind",
    "approved",
    "mutation_authority",
    "complete",
    "blockers",
    "source_bank",
    "candidate_bank",
    "bindings",
    "coverage",
    "invalidation_dispositions",
    "semantic_diff",
    "operations",
    "legacy_observations_imported",
    "rollback_requirements",
    "cutover",
    "closeout",
    "archive_retirement",
    "plan_digest",
}
BINDING_KEYS = {
    "inventory_digest",
    "offline_package_manifest_digest",
    "offline_package_artifact_digest",
    "projection_digest",
    "tag_mapping_digest",
    "high_water_manifest_digest",
    "invalidation_manifest_digest",
    "source_coverage_digest",
    "candidate_coverage_digest",
    "invalidation_disposition_digest",
    "candidate_provenance_digest",
    "candidate_curation_digest",
    "private_catalog_digests",
    "endpoint_digest",
    "provider_identity_digest",
    "versions_digest",
}
ROLLBACK_REQUIREMENTS = deep_freeze([
    "source_full_bank_export",
    "shadow_full_bank_export",
    "full_schema_backup",
    "disposable_restore_proofs",
    "invalidated_memory_verification",
])
CUTOVER_REQUIREMENTS = deep_freeze({
    "freeze_retain_paths": True,
    "block_new_session_exchange": True,
    "revoke_existing_write_capabilities": True,
    "wait_for_idle_operations": True,
    "capture_final_high_water": True,
    "final_catch_up": True,
    "on_drift": "restart_verification",
})
CLOSEOUT_REQUIREMENTS = deep_freeze({
    "kind": "live-bank-closeout",
    "authority": "separate_digest_bound_approval",
    "archive_deletion_authority": False,
})
ARCHIVE_RETIREMENT_REQUIREMENTS = deep_freeze({
    "kind": "migration-archive-retirement",
    "authority": "separate_digest_bound_approval",
    "requires_accepted_cutover": True,
})


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or DIGEST.fullmatch(value) is None:
        raise MigrationError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _identifier(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 256
        or any(character in value for character in "\r\n\0")
    ):
        raise MigrationError(f"{label} must be a bounded identifier")
    return value


def _normalized(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise MigrationError("normalized mapping keys must be strings")
        return {key: _normalized(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalized(item) for item in value]
    return value


def _normalized_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize mappings while sorting only schema-declared set-like arrays."""

    value = _normalized(snapshot)
    for role in ("source", "candidate"):
        bank = value["banks"][role]
        for key in ("scopes", "tags"):
            bank[key] = sorted(bank[key])
        for key, identity in (
            ("documents", "document_id"),
            ("models", "model_id"),
            ("directives", "directive_id"),
            ("invalidated_memories", "item_id"),
        ):
            bank[key] = sorted(bank[key], key=lambda item: item[identity])
        for document in bank["documents"]:
            if "tags" in document:
                document["tags"] = sorted(document["tags"])
    value["hooks"] = sorted(
        value["hooks"], key=lambda item: (item["bank_role"], item["hook_id"])
    )
    value["schedules"] = sorted(
        value["schedules"],
        key=lambda item: (item["bank_role"], item["model_id"]),
    )
    return value


def _offline_package_digest(manifest: Mapping[str, Any]) -> str:
    body: dict[str, Any] = {
        key: value
        for key, value in _normalized(manifest).items()
        if key != "approved_manifest_digest"
    }
    dispositions = body.get("invalidation_dispositions")
    if isinstance(dispositions, list):
        body["invalidation_dispositions"] = sorted(
            dispositions,
            key=lambda item: (
                item.get("bank_role", "") if isinstance(item, Mapping) else "",
                item.get("item_id", "") if isinstance(item, Mapping) else "",
                canonical_bytes(item),
            ),
        )
    return digest(body)


def _read_gate(path: str, label: str) -> dict[str, Any]:
    try:
        evidence = read_file_evidence(path, label, allow_missing=True)
    except FileEvidenceError as error:
        raise MigrationError(str(error)) from None
    if evidence is None:
        return {"exists": False, "digest": None}
    return {"exists": True, "digest": evidence[1]}


def _reject_symlink_components(path: Path, label: str) -> None:
    try:
        reject_symlink_components(path, label, allow_missing=True)
    except FileEvidenceError as error:
        raise MigrationError(str(error)) from None


def _gate_snapshot(paths: Mapping[str, Any]) -> dict[str, Any]:
    required = {"artifact_dir", "completion_marker", "proposal_log"}
    if not isinstance(paths, Mapping) or set(paths) != required:
        raise MigrationError("migration paths are closed")
    return {
        "completion_marker": _read_gate(paths["completion_marker"], "completion marker"),
        "proposal_log": _read_gate(paths["proposal_log"], "proposal log"),
    }


def _retain_watermark_snapshot(reader: Callable[[], Mapping[str, Any]]) -> dict[str, Any]:
    if not callable(reader):
        raise MigrationError("retain watermark reader is required")
    try:
        value = reader()
    except Exception:
        raise MigrationError("retain watermark snapshot is unavailable") from None
    if not isinstance(value, Mapping):
        raise MigrationError("retain watermark snapshot must be an object")
    return _normalized(value)


def _adapter_generation_snapshot(adapter: Any) -> str:
    reader = getattr(adapter, "read_migration_generation", None)
    if not callable(reader):
        raise MigrationError("adapter migration generation is unavailable")
    try:
        value = reader()
    except Exception:
        raise MigrationError("adapter migration generation is unavailable") from None
    try:
        encoded_value = value.encode("utf-8")
    except (AttributeError, UnicodeEncodeError):
        raise MigrationError("adapter migration generation is unavailable") from None
    if (
        not isinstance(value, str)
        or not value
        or len(encoded_value) > 256
        or not value.isprintable()
    ):
        raise MigrationError("adapter migration generation is unavailable")
    return value


def _with_retain_watermarks(snapshot: Any, watermarks: Mapping[str, Any]) -> Any:
    if not isinstance(snapshot, Mapping):
        return snapshot
    if "retain_watermarks" in snapshot:
        raise MigrationError("adapter snapshot must not contain retain watermarks")
    return {**snapshot, "retain_watermarks": watermarks}


def _mapping(value: Any, label: str, blockers: list[str]) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        blockers.append(f"invalid:{label}")
        return None
    return value


def _valid_migration_tag(value: Any) -> bool:
    return isinstance(value, str) and (
        value in STATIC_MIGRATION_TAGS or CATALOG_TAG.fullmatch(value) is not None
    )


def _aware_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _validate_document(
    role: str,
    raw: Any,
    blockers: list[str],
) -> None:
    record = _mapping(raw, f"{role}.documents", blockers)
    if record is None:
        return
    if not DOCUMENT_REQUIRED_KEYS <= set(record) or not set(record) <= DOCUMENT_KEYS:
        blockers.append(f"invalid:{role}.documents.keys")
    for key in sorted(DOCUMENT_REQUIRED_KEYS):
        if key not in record:
            blockers.append(f"missing:{role}.documents.{key}")
    if "document_id" in record:
        try:
            _identifier(record["document_id"], "document ID")
        except MigrationError:
            blockers.append(f"invalid:{role}.documents.document_id")
    if "updated_at" in record:
        if not _aware_timestamp(record["updated_at"]):
            blockers.append(f"invalid:{role}.documents.updated_at")
    if "content_digest" in record:
        try:
            _sha(record["content_digest"], "document content digest")
        except MigrationError:
            blockers.append(f"invalid:{role}.documents.content_digest")
    if "tags" in record:
        tags = record["tags"]
        if not isinstance(tags, list) or not all(
            isinstance(tag, str) for tag in tags
        ):
            blockers.append(f"invalid:{role}.documents.tags")
        elif len(tags) != len(set(tags)) or any(
            not _valid_migration_tag(tag) for tag in tags
        ):
            blockers.append(f"invalid:{role}.documents.tags")
    if "created_at" in record and record["created_at"] is not None:
        if not _aware_timestamp(record["created_at"]):
            blockers.append(f"invalid:{role}.documents.created_at")
    for key in ("text_length", "memory_unit_count"):
        if key in record and record[key] is not None and (
            type(record[key]) is not int or record[key] < 0
        ):
            blockers.append(f"invalid:{role}.documents.{key}")
    for key in ("document_metadata", "retain_params"):
        if key in record:
            nested = record[key]
            if (
                not isinstance(nested, Mapping)
                or set(nested) != CONTENT_RECORD_KEYS
            ):
                blockers.append(f"invalid:{role}.documents.{key}")
            else:
                try:
                    _sha(
                        nested["content_digest"],
                        f"document {key} digest",
                    )
                except MigrationError:
                    blockers.append(f"invalid:{role}.documents.{key}")


def _validate_invalidation(role: str, raw: Any, blockers: list[str]) -> None:
    record = _mapping(raw, f"{role}.invalidated_memories", blockers)
    if record is None:
        return
    if set(record) != INVALIDATED_MEMORY_KEYS:
        blockers.append(f"invalid:{role}.invalidated_memories.keys")
    for key in sorted(INVALIDATED_MEMORY_KEYS):
        if key not in record:
            blockers.append(f"missing:{role}.invalidated_memories.{key}")
    for key in ("item_id", "source_document_id"):
        if key in record:
            try:
                _identifier(record[key], key.replace("_", " "))
            except MigrationError:
                blockers.append(f"invalid:{role}.invalidated_memories.{key}")
    for key in ("reason_digest", "content_digest"):
        if key in record:
            try:
                _sha(record[key], key.replace("_", " "))
            except MigrationError:
                blockers.append(f"invalid:{role}.invalidated_memories.{key}")


def _validate_content_records(
    role: str,
    collection: str,
    identity_key: str,
    value: Any,
    blockers: list[str],
) -> None:
    if not isinstance(value, list):
        return
    expected_keys = MODEL_KEYS if collection == "models" else DIRECTIVE_KEYS
    identities: list[str] = []
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != expected_keys:
            blockers.append(f"invalid:{role}.{collection}.keys")
            continue
        try:
            identities.append(_identifier(raw[identity_key], identity_key))
        except MigrationError:
            blockers.append(f"invalid:{role}.{collection}.{identity_key}")
        try:
            _sha(raw["content_digest"], f"{collection} content digest")
        except MigrationError:
            blockers.append(f"invalid:{role}.{collection}.content_digest")
    if len(identities) != len(set(identities)):
        blockers.append(f"invalid:{role}.{collection}.duplicate")


def _validate_hooks(value: Any, blockers: list[str]) -> None:
    if not isinstance(value, list):
        return
    identities: list[tuple[str, str]] = []
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != HOOK_KEYS:
            blockers.append("invalid:hooks.keys")
            continue
        role = raw.get("bank_role")
        if role not in {"source", "candidate"}:
            blockers.append("invalid:hooks.bank_role")
            continue
        try:
            hook_id = _identifier(raw["hook_id"], "hook ID")
            identities.append((role, hook_id))
        except MigrationError:
            blockers.append("invalid:hooks.hook_id")
        try:
            _sha(raw["registration_digest"], "hook registration digest")
        except MigrationError:
            blockers.append("invalid:hooks.registration_digest")
        registration = raw.get("registration")
        if (
            not isinstance(registration, Mapping)
            or set(registration) != HOOK_REGISTRATION_KEYS
        ):
            blockers.append("invalid:hooks.registration")
        else:
            for key in sorted(HOOK_REGISTRATION_KEYS):
                try:
                    _sha(registration[key], f"hook {key}")
                except MigrationError:
                    blockers.append(f"invalid:hooks.registration.{key}")
            try:
                if not hmac.compare_digest(
                    raw["registration_digest"], digest(registration)
                ):
                    blockers.append("invalid:hooks.registration_digest")
            except (TypeError, ValueError):
                blockers.append("invalid:hooks.registration_digest")
    if len(identities) != len(set(identities)):
        blockers.append("invalid:hooks.duplicate")


def _validate_schedules(value: Any, blockers: list[str]) -> None:
    if not isinstance(value, list):
        return
    identities: list[tuple[str, str]] = []
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != SCHEDULE_KEYS:
            blockers.append("invalid:schedules.keys")
            continue
        role = raw.get("bank_role")
        if role not in {"source", "candidate"}:
            blockers.append("invalid:schedules.bank_role")
            continue
        try:
            model_id = _identifier(raw["model_id"], "schedule model ID")
            identities.append((role, model_id))
        except MigrationError:
            blockers.append("invalid:schedules.model_id")
        try:
            _sha(raw["trigger_digest"], "schedule trigger digest")
        except MigrationError:
            blockers.append("invalid:schedules.trigger_digest")
    if len(identities) != len(set(identities)):
        blockers.append("invalid:schedules.duplicate")


def _snapshot_blockers(snapshot: Any, source_bank: BankRef, candidate_bank: BankRef) -> list[str]:
    blockers: list[str] = []
    if not isinstance(snapshot, Mapping):
        return ["invalid:snapshot"]
    for key in sorted(SNAPSHOT_KEYS):
        if key not in snapshot:
            blockers.append(f"missing:{key}")
    if blockers:
        return blockers
    if set(snapshot) != SNAPSHOT_KEYS:
        blockers.append("invalid:snapshot_keys")
    try:
        canonical_bytes(snapshot)
    except (StrictJsonError, TypeError, ValueError):
        blockers.append("invalid:snapshot_values")
    if type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != 1:
        blockers.append("invalid:schema_version")
    for key in ("endpoint", "provider_identity", "versions", "banks", "operations", "retain_watermarks"):
        _mapping(snapshot[key], key, blockers)
    for key in ("hooks", "schedules"):
        if not isinstance(snapshot[key], list):
            blockers.append(f"invalid:{key}")
        elif any(not isinstance(item, Mapping) for item in snapshot[key]):
            blockers.append(f"invalid:{key}")
    _validate_hooks(snapshot.get("hooks"), blockers)
    _validate_schedules(snapshot.get("schedules"), blockers)
    banks = snapshot.get("banks")
    if isinstance(banks, Mapping):
        if set(banks) != {"source", "candidate"}:
            blockers.append("invalid:banks")
        for role, expected in (("source", source_bank), ("candidate", candidate_bank)):
            bank = banks.get(role)
            if not isinstance(bank, Mapping):
                blockers.append(f"missing:banks.{role}")
                continue
            for key in sorted(BANK_KEYS):
                if key not in bank:
                    blockers.append(f"missing:{role}.{key}")
            if set(bank) != BANK_KEYS:
                blockers.append(f"invalid:{role}.bank_keys")
            if bank.get("bank_ref") != expected.to_dict():
                blockers.append(f"invalid:{role}.bank_ref")
            for key in ("config", "stats"):
                if not isinstance(bank.get(key), Mapping):
                    blockers.append(f"invalid:{role}.{key}")
            try:
                _sha(bank.get("scopes_digest"), f"{role} scopes digest")
            except MigrationError:
                blockers.append(f"invalid:{role}.scopes_digest")
            for key in ("scopes", "tags", "documents", "models", "directives", "invalidated_memories"):
                if not isinstance(bank.get(key), list):
                    blockers.append(f"invalid:{role}.{key}")
            _validate_content_records(
                role, "models", "model_id", bank.get("models"), blockers
            )
            _validate_content_records(
                role,
                "directives",
                "directive_id",
                bank.get("directives"),
                blockers,
            )
            for key in ("scopes", "tags"):
                values = bank.get(key)
                if isinstance(values, list) and any(
                    not isinstance(value, str) for value in values
                ):
                    blockers.append(f"invalid:{role}.{key}")
                elif isinstance(values, list) and len(values) != len(set(values)):
                    blockers.append(f"invalid:{role}.{key}.duplicate")
            scopes = bank.get("scopes")
            if isinstance(scopes, list) and all(
                isinstance(value, str) for value in scopes
            ) and bank.get("scopes_digest") != digest(
                {"scopes": sorted(scopes)}
            ):
                blockers.append(f"invalid:{role}.scopes_digest")
            documents = bank.get("documents", [])
            if isinstance(documents, list):
                for document in documents:
                    _validate_document(role, document, blockers)
                identifiers = [
                    item["document_id"]
                    for item in documents
                    if isinstance(item, Mapping)
                    and isinstance(item.get("document_id"), str)
                ]
                if len(identifiers) != len(set(identifiers)):
                    blockers.append(f"invalid:{role}.documents.duplicate")
            invalidations = bank.get("invalidated_memories", [])
            if isinstance(invalidations, list):
                for item in invalidations:
                    _validate_invalidation(role, item, blockers)
                identifiers = [
                    item["item_id"]
                    for item in invalidations
                    if isinstance(item, Mapping)
                    and isinstance(item.get("item_id"), str)
                ]
                if len(identifiers) != len(set(identifiers)):
                    blockers.append(f"invalid:{role}.invalidated_memories.duplicate")
    operations = snapshot.get("operations")
    if isinstance(operations, Mapping):
        if set(operations) != {"idle", "active"} or type(operations.get("idle")) is not bool or not isinstance(operations.get("active"), list):
            blockers.append("invalid:operations")
        elif not operations["idle"] or operations["active"]:
            blockers.append("operations:not_idle")
    return sorted(set(blockers))


def _high_water(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for role in ("source", "candidate"):
        for document in snapshot["banks"][role]["documents"]:
            rows.append(
                {
                    "bank_role": role,
                    "document_id": document["document_id"],
                    "updated_at": document["updated_at"],
                    "content_digest": document["content_digest"],
                }
            )
    return sorted(rows, key=lambda item: (item["bank_role"], item["document_id"]))


def _invalidations(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for role in ("source", "candidate"):
        for item in snapshot["banks"][role]["invalidated_memories"]:
            rows.append({"bank_role": role, **{key: item[key] for key in ("item_id", "source_document_id", "reason_digest", "content_digest")}})
    return sorted(rows, key=lambda item: (item["bank_role"], item["item_id"]))


def _operation_ids(snapshot: Mapping[str, Any]) -> list[str]:
    result = []
    for operation in snapshot["operations"]["active"]:
        if isinstance(operation, Mapping):
            result.append(str(operation.get("operation_id", operation.get("id", "unknown"))))
        else:
            result.append(str(operation))
    return sorted(result)


def _drift_blockers(before: Mapping[str, Any], after: Mapping[str, Any], before_gate: Any, after_gate: Any) -> list[str]:
    def bank_values(
        snapshot: Mapping[str, Any], keys: tuple[str, ...]
    ) -> dict[str, dict[str, Any]]:
        return {
            role: {key: snapshot["banks"][role][key] for key in keys}
            for role in ("source", "candidate")
        }

    checks = {
        "completion_gate": (before_gate, after_gate),
        "bank_stats": (
            {role: before["banks"][role]["stats"] for role in ("source", "candidate")},
            {role: after["banks"][role]["stats"] for role in ("source", "candidate")},
        ),
        "operation_ids": (_operation_ids(before), _operation_ids(after)),
        "document_high_water": (_high_water(before), _high_water(after)),
        "bank_documents": (
            bank_values(before, ("documents",)),
            bank_values(after, ("documents",)),
        ),
        "bank_configuration": (
            bank_values(before, ("config",)),
            bank_values(after, ("config",)),
        ),
        "bank_scope": (
            bank_values(before, ("scopes", "scopes_digest", "tags")),
            bank_values(after, ("scopes", "scopes_digest", "tags")),
        ),
        "bank_models_and_directives": (
            bank_values(before, ("models", "directives")),
            bank_values(after, ("models", "directives")),
        ),
        "invalidated_memories": (_invalidations(before), _invalidations(after)),
        "hooks": (before["hooks"], after["hooks"]),
        "schedules": (before["schedules"], after["schedules"]),
        "retain_watermarks": (before["retain_watermarks"], after["retain_watermarks"]),
        "identity": (
            {key: before[key] for key in ("endpoint", "provider_identity", "versions")},
            {key: after[key] for key in ("endpoint", "provider_identity", "versions")},
        ),
    }
    return [f"drift:{name}" for name, values in checks.items() if digest(_normalized(values[0])) != digest(_normalized(values[1]))]


def _package_blockers(manifest: Any, approved_digest: Any) -> list[str]:
    blockers: list[str] = []
    if not isinstance(manifest, Mapping) or set(manifest) != PACKAGE_KEYS:
        return ["offline_package:invalid_manifest"]
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        blockers.append("offline_package:invalid_schema")
    for key in (
        "approved_manifest_digest",
        "artifact_digest",
        "projection_digest",
        "tag_mapping_digest",
        "candidate_provenance_digest",
        "candidate_curation_digest",
    ):
        try:
            _sha(manifest[key], f"offline package {key}")
        except MigrationError:
            blockers.append(f"offline_package:invalid_{key}")
    try:
        actual_digest = _offline_package_digest(manifest)
    except (MigrationError, StrictJsonError, TypeError, ValueError):
        actual_digest = None
        blockers.append("offline_package:invalid_manifest")
    if (
        not isinstance(actual_digest, str)
        or not isinstance(approved_digest, str)
        or DIGEST.fullmatch(approved_digest) is None
        or not isinstance(manifest["approved_manifest_digest"], str)
        or not hmac.compare_digest(actual_digest, approved_digest)
        or not hmac.compare_digest(
            manifest["approved_manifest_digest"], actual_digest
        )
    ):
        blockers.append("offline_package:digest_mismatch")
    dispositions = manifest["invalidation_dispositions"]
    if not isinstance(dispositions, list) or any(
        not isinstance(item, Mapping)
        or set(item) != INVALIDATION_KEYS
        or not isinstance(item["bank_role"], str)
        or item["bank_role"] not in {"source", "candidate"}
        or not isinstance(item["item_id"], str)
        or not isinstance(item["disposition"], str)
        or not isinstance(item["reason"], str)
        or (
            item["reapply_content_digest"] is not None
            and not isinstance(item["reapply_content_digest"], str)
        )
        for item in dispositions
    ):
        blockers.append("offline_package:invalid_invalidation_dispositions")
    return sorted(set(blockers))


def _validate_repository_catalog(
    value: Any, expected_digest: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "canonical", "aliases", "drop_aliases",
    }:
        raise MigrationError("repository catalog keys are closed")
    canonical = value["canonical"]
    aliases = value["aliases"]
    drop_aliases = value["drop_aliases"]
    if (
        not isinstance(canonical, list)
        or not canonical
        or any(
            not isinstance(item, str)
            or CANONICAL_REPOSITORY_TAG.fullmatch(item) is None
            for item in canonical
        )
        or len(canonical) != len(set(canonical))
    ):
        raise MigrationError("repository catalog canonical tags are invalid")
    canonical_set = set(canonical)
    if (
        not isinstance(aliases, Mapping)
        or any(
            not isinstance(source, str)
            or REPOSITORY_ALIAS.fullmatch(source) is None
            or not isinstance(target, str)
            or target not in canonical_set
            for source, target in aliases.items()
        )
        or set(aliases) & canonical_set
    ):
        raise MigrationError("repository catalog aliases are invalid")
    if (
        not isinstance(drop_aliases, list)
        or any(
            not isinstance(item, str)
            or REPOSITORY_ALIAS.fullmatch(item) is None
            for item in drop_aliases
        )
        or len(drop_aliases) != len(set(drop_aliases))
        or set(drop_aliases) & (set(aliases) | canonical_set)
    ):
        raise MigrationError("repository catalog drop aliases are invalid")
    _sha(expected_digest, "repository catalog mapping digest")
    if not hmac.compare_digest(digest(value), expected_digest):
        raise MigrationError(
            "repository catalog does not match tag mapping digest"
        )
    return value


def _coverage_scope(
    bank: Mapping[str, Any],
    document: Mapping[str, Any],
    repository_catalog: Mapping[str, Any],
) -> tuple[str, str, str | None]:
    candidates: set[str] = set()
    for values in (bank.get("scopes"), bank.get("tags"), document.get("tags")):
        if not isinstance(values, list):
            continue
        candidates.update(
            value
            for value in values
            if isinstance(value, str)
        )
    canonical = set(repository_catalog["canonical"])
    aliases = repository_catalog["aliases"]
    dropped = set(repository_catalog["drop_aliases"])
    alias_namespaces = {
        value.partition(":")[0]
        for value in set(aliases) | dropped
        if ":" in value
    }
    repository_candidates = {
        value
        for value in candidates
        if value.startswith("repo:")
        or value in aliases
        or value in dropped
        or (
            ":" in value
            and value.partition(":")[0] in alias_namespaces
        )
    }
    resolved = {
        value if value in canonical else aliases[value]
        for value in repository_candidates
        if value in canonical or value in aliases
    }
    if len(resolved) > 1:
        raise MigrationError("migration item has conflicting repository scopes")
    unknown = sorted(
        value
        for value in repository_candidates
        if value not in canonical and value not in aliases and value not in dropped
    )
    dropped_candidates = sorted(repository_candidates & dropped)
    if unknown:
        return "omit", "unknown-repository-scope", None
    if dropped_candidates:
        return "omit", "dropped-repository-scope", None
    workflow_candidates = sorted(
        value
        for value in candidates
        if isinstance(value, str) and value.startswith("workflow:")
    )
    if len(workflow_candidates) > 1:
        raise MigrationError("migration item has conflicting workflow scopes")
    bare_scopes = {value for value in candidates if value in {"global", "personal"}}
    if len(bare_scopes) > 1:
        raise MigrationError("migration item has conflicting semantic scopes")
    if len(resolved) + len(workflow_candidates) + len(bare_scopes) > 1:
        raise MigrationError("migration item has conflicting semantic scopes")
    semantic_scope = (
        next(iter(resolved))
        if resolved
        else workflow_candidates[0]
        if workflow_candidates
        else next(iter(bare_scopes))
        if bare_scopes
        else "global"
    )
    return (
        "retain",
        "stable-live-document",
        semantic_scope,
    )


def _live_coverage(
    snapshot: Mapping[str, Any], repository_catalog: Mapping[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for role in ("source", "candidate"):
        bank = snapshot["banks"][role]
        result[role] = sorted(
            (
                dict(zip(
                    ("disposition", "reason", "semantic_scope"),
                    _coverage_scope(bank, document, repository_catalog),
                )) | {
                    "bank_role": role,
                    "item_id": document["document_id"],
                    "content_digest": document["content_digest"],
                }
                for document in bank["documents"]
            ),
            key=lambda item: item["item_id"],
        )
    return result


def _coverage_blockers(snapshot: Mapping[str, Any], manifest: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    raw_dispositions = manifest["invalidation_dispositions"]
    if isinstance(raw_dispositions, list):
        records = []
        for item in raw_dispositions:
            if not isinstance(item, Mapping) or set(item) != INVALIDATION_KEYS:
                blockers.append("curation:invalid_record")
                continue
            valid_record = True
            try:
                if (
                    not isinstance(item["bank_role"], str)
                    or item["bank_role"] not in {"source", "candidate"}
                ):
                    raise MigrationError("invalidation bank role is invalid")
                _identifier(item["item_id"], "invalidation item ID")
                _identifier(item["reason"], "invalidation reason")
            except MigrationError:
                blockers.append("curation:invalid_record")
                valid_record = False
            if (
                not isinstance(item["disposition"], str)
                or item["disposition"] not in {"exclude", "supersede", "reapply"}
            ):
                blockers.append("curation:invalid_disposition")
                valid_record = False
            if item["disposition"] == "reapply":
                try:
                    _sha(item["reapply_content_digest"], "reapply content digest")
                except MigrationError:
                    blockers.append("curation:invalid_reapply_digest")
                    valid_record = False
            elif item["reapply_content_digest"] is not None:
                blockers.append("curation:unexpected_reapply_digest")
                valid_record = False
            if valid_record:
                records.append(item)
        observed = {
            (item["bank_role"], item["item_id"])
            for item in _invalidations(snapshot)
        }
        supplied = [
            (item["bank_role"], item["item_id"])
            for item in records
        ]
        if len(supplied) != len(set(supplied)):
            blockers.append("curation:duplicate")
        if set(supplied) != observed:
            blockers.append("curation:not_bijective")
    return sorted(set(blockers))


@dataclass(frozen=True)
class ShadowPlan:
    body: Mapping[str, Any]
    plan_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "body", deep_freeze(self.body))

    def to_dict(self) -> dict[str, Any]:
        return {**deep_thaw(self.body), "plan_digest": self.plan_digest}


@dataclass(frozen=True)
class MigrationDiscovery:
    complete: bool
    blockers: tuple[str, ...]
    run_dir: str | None = None
    inventory_digest: str | None = None
    shadow_plan_digest: str | None = None
    plan: ShadowPlan | None = None


def _shadow_plan(
    snapshot: Mapping[str, Any],
    source_bank: BankRef,
    candidate_bank: BankRef,
    manifest: Mapping[str, Any],
    manifest_digest: str,
    inventory_digest: str,
    high_water: Sequence[Mapping[str, Any]],
    invalidations: Sequence[Mapping[str, Any]],
    private_catalog_digests: Mapping[str, str],
    repository_catalog: Mapping[str, Any],
) -> ShadowPlan:
    coverage = _live_coverage(snapshot, repository_catalog)
    curation = sorted(
        (_normalized(item) for item in manifest["invalidation_dispositions"]),
        key=lambda item: (item["bank_role"], item["item_id"]),
    )
    body = {
        "schema_version": 1,
        "kind": "migration-shadow-plan",
        "approved": False,
        "mutation_authority": "none",
        "complete": True,
        "blockers": [],
        "source_bank": source_bank.to_dict(),
        "candidate_bank": candidate_bank.to_dict(),
        "bindings": {
            "inventory_digest": inventory_digest,
            "offline_package_manifest_digest": manifest_digest,
            "offline_package_artifact_digest": manifest["artifact_digest"],
            "projection_digest": manifest["projection_digest"],
            "tag_mapping_digest": manifest["tag_mapping_digest"],
            "high_water_manifest_digest": digest(high_water),
            "invalidation_manifest_digest": digest(invalidations),
            "source_coverage_digest": digest(coverage["source"]),
            "candidate_coverage_digest": digest(coverage["candidate"]),
            "invalidation_disposition_digest": digest(curation),
            "candidate_provenance_digest": manifest["candidate_provenance_digest"],
            "candidate_curation_digest": manifest["candidate_curation_digest"],
            "private_catalog_digests": _normalized(private_catalog_digests),
            "endpoint_digest": digest(snapshot["endpoint"]),
            "provider_identity_digest": digest(snapshot["provider_identity"]),
            "versions_digest": digest(snapshot["versions"]),
        },
        "coverage": coverage,
        "invalidation_dispositions": curation,
        "semantic_diff": {
            "source_items": len(coverage["source"]),
            "candidate_items": len(coverage["candidate"]),
            "proposed_retains": sum(item["disposition"] == "retain" for rows in coverage.values() for item in rows),
            "invalidations": len(curation),
            "reapplications": sum(item["disposition"] == "reapply" for item in curation),
        },
        "operations": {"idle": True, "active_operation_ids": []},
        "legacy_observations_imported": False,
        "rollback_requirements": deep_thaw(ROLLBACK_REQUIREMENTS),
        "cutover": deep_thaw(CUTOVER_REQUIREMENTS),
        "closeout": deep_thaw(CLOSEOUT_REQUIREMENTS),
        "archive_retirement": deep_thaw(ARCHIVE_RETIREMENT_REQUIREMENTS),
    }
    return ShadowPlan(body, digest(body))


def verify_shadow_plan(
    value: Any, *, repository_catalog: Mapping[str, Any]
) -> None:
    if not isinstance(value, Mapping) or set(value) != PLAN_KEYS:
        raise MigrationError("shadow plan keys are closed")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["kind"] != "migration-shadow-plan":
        raise MigrationError("shadow plan schema is invalid")
    if value["approved"] is not False or value["mutation_authority"] != "none" or value["complete"] is not True:
        raise MigrationError("shadow plan cannot carry mutation authority")
    if value["blockers"] != [] or value["legacy_observations_imported"] is not False:
        raise MigrationError("shadow plan safety gates are invalid")
    for role in ("source_bank", "candidate_bank"):
        bank = value[role]
        if (
            not isinstance(bank, Mapping)
            or set(bank) not in (
                {"profile_id", "bank_id"},
                {"profile_id", "bank_id", "endpoint"},
            )
        ):
            raise MigrationError("shadow plan bank reference is invalid")
        _identifier(bank["profile_id"], "bank profile ID")
        _identifier(bank["bank_id"], "bank ID")
        if "endpoint" in bank:
            try:
                _endpoint(bank["endpoint"], bank["profile_id"])
            except PlanError as error:
                raise MigrationError("shadow plan bank endpoint is invalid") from error
    if value["source_bank"] == value["candidate_bank"]:
        raise MigrationError("shadow plan banks must be distinct")
    bindings = value["bindings"]
    if not isinstance(bindings, Mapping) or set(bindings) != BINDING_KEYS:
        raise MigrationError("shadow plan bindings are closed")
    for key in BINDING_KEYS - {"private_catalog_digests"}:
        _sha(bindings[key], f"shadow plan {key}")
    repository_catalog = _validate_repository_catalog(
        repository_catalog, bindings["tag_mapping_digest"]
    )
    catalogs = bindings["private_catalog_digests"]
    if not isinstance(catalogs, Mapping) or not catalogs:
        raise MigrationError("shadow plan private catalog digests are invalid")
    for key, item in catalogs.items():
        _identifier(key, "private catalog digest name")
        _sha(item, "private catalog digest")
    coverage = value["coverage"]
    if not isinstance(coverage, Mapping) or set(coverage) != {"source", "candidate"}:
        raise MigrationError("shadow plan coverage is closed")
    for role in ("source", "candidate"):
        if not isinstance(coverage[role], list):
            raise MigrationError("shadow plan coverage is invalid")
        identifiers: list[str] = []
        for item in coverage[role]:
            if not isinstance(item, Mapping) or set(item) != COVERAGE_KEYS:
                raise MigrationError("shadow plan coverage record is invalid")
            identifiers.append(_identifier(item["item_id"], "coverage item ID"))
            if not isinstance(item["bank_role"], str) or item["bank_role"] != role:
                raise MigrationError("shadow plan coverage bank role is invalid")
            _sha(item["content_digest"], "coverage content digest")
            _identifier(item["reason"], "coverage reason")
            if not isinstance(item["disposition"], str) or item["disposition"] not in {"retain", "omit", "duplicate", "supersede"}:
                raise MigrationError("shadow plan coverage disposition is invalid")
            if item["disposition"] == "retain":
                if not isinstance(item["semantic_scope"], str) or SEMANTIC_SCOPE.fullmatch(item["semantic_scope"]) is None:
                    raise MigrationError("shadow plan semantic scope is invalid")
                if (
                    item["semantic_scope"].startswith("repo:")
                    and item["semantic_scope"] not in repository_catalog["canonical"]
                ):
                    raise MigrationError(
                        "shadow plan semantic scope is not in the repository catalog"
                    )
            elif item["semantic_scope"] is not None:
                raise MigrationError("shadow plan semantic scope is invalid")
        if len(identifiers) != len(set(identifiers)):
            raise MigrationError("shadow plan coverage contains duplicates")
        if identifiers != sorted(identifiers):
            raise MigrationError("shadow plan coverage must be canonically ordered")
        if not hmac.compare_digest(digest(coverage[role]), bindings[f"{role}_coverage_digest"]):
            raise MigrationError("shadow plan coverage digest does not match")
    curation = value["invalidation_dispositions"]
    if not isinstance(curation, list):
        raise MigrationError("shadow plan invalidation dispositions are invalid")
    invalidation_ids: list[tuple[str, str]] = []
    for item in curation:
        if not isinstance(item, Mapping) or set(item) != INVALIDATION_KEYS:
            raise MigrationError("shadow plan invalidation disposition is invalid")
        if (
            not isinstance(item["bank_role"], str)
            or item["bank_role"] not in {"source", "candidate"}
        ):
            raise MigrationError("shadow plan invalidation bank role is invalid")
        invalidation_ids.append((
            item["bank_role"],
            _identifier(item["item_id"], "invalidation item ID"),
        ))
        _identifier(item["reason"], "invalidation reason")
        if not isinstance(item["disposition"], str) or item["disposition"] not in {"exclude", "supersede", "reapply"}:
            raise MigrationError("shadow plan invalidation disposition is invalid")
        if item["disposition"] == "reapply":
            _sha(item["reapply_content_digest"], "reapply content digest")
        elif item["reapply_content_digest"] is not None:
            raise MigrationError("shadow plan invalidation disposition is invalid")
    if len(invalidation_ids) != len(set(invalidation_ids)):
        raise MigrationError("shadow plan invalidation dispositions contain duplicates")
    if invalidation_ids != sorted(invalidation_ids):
        raise MigrationError("shadow plan invalidation dispositions must be canonically ordered")
    if not hmac.compare_digest(digest(curation), bindings["invalidation_disposition_digest"]):
        raise MigrationError("shadow plan invalidation disposition digest does not match")
    semantic = value["semantic_diff"]
    expected_semantic = {
        "source_items": len(coverage["source"]),
        "candidate_items": len(coverage["candidate"]),
        "proposed_retains": sum(item["disposition"] == "retain" for rows in coverage.values() for item in rows),
        "invalidations": len(curation),
        "reapplications": sum(item["disposition"] == "reapply" for item in curation),
    }
    if canonical_bytes(semantic) != canonical_bytes(expected_semantic):
        raise MigrationError("shadow plan semantic diff is invalid")
    if canonical_bytes(value["operations"]) != canonical_bytes({"idle": True, "active_operation_ids": []}):
        raise MigrationError("shadow plan operations must be idle")
    if canonical_bytes(value["rollback_requirements"]) != canonical_bytes(ROLLBACK_REQUIREMENTS):
        raise MigrationError("shadow plan rollback requirements are invalid")
    if canonical_bytes(value["cutover"]) != canonical_bytes(CUTOVER_REQUIREMENTS):
        raise MigrationError("shadow plan cutover requirements are invalid")
    if canonical_bytes(value["closeout"]) != canonical_bytes(CLOSEOUT_REQUIREMENTS):
        raise MigrationError("shadow plan closeout requirements are invalid")
    if canonical_bytes(value["archive_retirement"]) != canonical_bytes(ARCHIVE_RETIREMENT_REQUIREMENTS):
        raise MigrationError("shadow plan archive retirement requirements are invalid")
    _sha(value["plan_digest"], "shadow plan digest")
    body = {key: deep_thaw(item) for key, item in value.items() if key != "plan_digest"}
    if not hmac.compare_digest(digest(body), value["plan_digest"]):
        raise MigrationError("shadow plan digest does not match its body")


def _validate_private_directory(metadata: os.stat_result) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise MigrationError("migration artifact path must be a directory")
    if metadata.st_uid != os.geteuid():
        raise MigrationError(
            "migration artifact directory must be owned by the current user"
        )
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise MigrationError("migration artifact directory must have mode 0700")


def _reject_git_worktree_descriptor(descriptor: int) -> None:
    try:
        os.stat(".git", dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError:
        raise MigrationError(
            "migration artifact Git-worktree boundary is unavailable"
        ) from None
    raise MigrationError(
        "migration artifact directory must be outside a Git worktree"
    )


DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


def _verify_directory_entry(
    parent_descriptor: int,
    name: str,
    descriptor: int,
    label: str,
) -> None:
    try:
        entry = os.stat(
            name, dir_fd=parent_descriptor, follow_symlinks=False
        )
        opened = os.fstat(descriptor)
    except OSError:
        raise MigrationError(f"{label} identity changed") from None
    if (
        not stat.S_ISDIR(entry.st_mode)
        or (entry.st_dev, entry.st_ino) != (opened.st_dev, opened.st_ino)
    ):
        raise MigrationError(f"{label} identity changed")


@contextmanager
def _directory_chain(path: Path):
    """Open every absolute-path component without following symlinks."""
    if not path.is_absolute():
        raise MigrationError("migration artifact directory must be absolute")
    descriptors: list[int] = []
    links: list[tuple[int, str, int]] = []
    try:
        descriptors.append(os.open(path.anchor, DIRECTORY_FLAGS))
        _reject_git_worktree_descriptor(descriptors[-1])
        for component in path.parts[1:]:
            parent_descriptor = descriptors[-1]
            descriptor = os.open(
                component, DIRECTORY_FLAGS, dir_fd=parent_descriptor
            )
            descriptors.append(descriptor)
            _verify_directory_entry(
                parent_descriptor,
                component,
                descriptor,
                "migration artifact directory ancestor",
            )
            _reject_git_worktree_descriptor(descriptor)
            links.append((parent_descriptor, component, descriptor))
    except OSError:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise MigrationError("migration artifact directory is unavailable") from None
    except MigrationError:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise
    def verify_chain() -> None:
        for descriptor in descriptors:
            _reject_git_worktree_descriptor(descriptor)
        for parent_descriptor, component, descriptor in links:
            _verify_directory_entry(
                parent_descriptor,
                component,
                descriptor,
                "migration artifact directory ancestor",
            )

    try:
        yield descriptors[-1], verify_chain
        verify_chain()
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


@contextmanager
def _private_directory(path: Path):
    if not path.is_absolute():
        raise MigrationError("migration artifact directory must be absolute")
    _reject_symlink_components(path, "migration artifact directory")
    try:
        canonical = Path(os.path.abspath(os.fspath(path)))
        if len(canonical.parts) > 1:
            top_level = Path(canonical.anchor) / canonical.parts[1]
            if top_level.is_symlink():
                canonical = top_level.resolve(strict=True).joinpath(
                    *canonical.parts[2:]
                )
    except (OSError, RuntimeError):
        raise MigrationError("migration artifact directory is unavailable") from None
    with _directory_chain(canonical.parent) as (
        parent_descriptor,
        verify_parent_chain,
    ):
        directory_name = canonical.name
        created = False
        post_validation_rollbacks: list[Callable[[], None]] = []
        try:
            descriptor = os.open(
                directory_name, DIRECTORY_FLAGS, dir_fd=parent_descriptor
            )
        except FileNotFoundError:
            try:
                os.mkdir(directory_name, 0o700, dir_fd=parent_descriptor)
                created = True
                os.fsync(parent_descriptor)
                descriptor = os.open(
                    directory_name, DIRECTORY_FLAGS, dir_fd=parent_descriptor
                )
            except OSError:
                if created:
                    try:
                        os.rmdir(directory_name, dir_fd=parent_descriptor)
                        os.fsync(parent_descriptor)
                    except OSError:
                        pass
                raise MigrationError(
                    "migration artifact directory is unavailable"
                ) from None
        except OSError:
            raise MigrationError(
                "migration artifact directory is unavailable"
            ) from None
        try:
            _verify_directory_entry(
                parent_descriptor,
                directory_name,
                descriptor,
                "migration artifact directory",
            )
            _reject_git_worktree_descriptor(descriptor)
            _validate_private_directory(os.fstat(descriptor))
            def verify_artifact_root() -> None:
                _verify_directory_entry(
                    parent_descriptor,
                    directory_name,
                    descriptor,
                    "migration artifact directory",
                )
                _reject_git_worktree_descriptor(descriptor)
                _validate_private_directory(os.fstat(descriptor))
                verify_parent_chain()

            try:
                yield (
                    canonical,
                    descriptor,
                    post_validation_rollbacks.append,
                    verify_artifact_root,
                )
            except BaseException:
                raise
            else:
                try:
                    verify_artifact_root()
                except BaseException:
                    for rollback in reversed(post_validation_rollbacks):
                        try:
                            rollback()
                        except BaseException:
                            pass
                    raise
        finally:
            os.close(descriptor)


def _write_exclusive(
    directory_descriptor: int, name: str, value: Any
) -> tuple[int, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(
        name, flags, 0o600, dir_fd=directory_descriptor
    )
    identity = os.fstat(descriptor)
    published = False

    def entry_is_owned() -> bool:
        try:
            entry = os.stat(
                name, dir_fd=directory_descriptor, follow_symlinks=False
            )
        except FileNotFoundError:
            return False
        return (
            stat.S_ISREG(entry.st_mode)
            and (entry.st_dev, entry.st_ino) == (identity.st_dev, identity.st_ino)
        )

    try:
        data = canonical_bytes(value) + b"\n"
        written = 0
        while written < len(data):
            count = os.write(descriptor, data[written:])
            if count <= 0:
                raise OSError("migration artifact write made no progress")
            written += count
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or (metadata.st_dev, metadata.st_ino)
            != (identity.st_dev, identity.st_ino)
        ):
            raise MigrationError("migration artifact file is not private")
        if not entry_is_owned():
            raise MigrationError("migration artifact file identity changed")
        os.fsync(directory_descriptor)
        if not entry_is_owned():
            raise MigrationError("migration artifact file identity changed")
        published = True
    except BaseException:
        if entry_is_owned():
            try:
                os.unlink(name, dir_fd=directory_descriptor)
                os.fsync(directory_descriptor)
            except FileNotFoundError:
                pass
        raise
    finally:
        os.close(descriptor)
    if not published:
        raise MigrationError("migration artifact file was not published")
    return identity.st_dev, identity.st_ino


def _rename_directory_no_replace(
    directory_descriptor: int, source: str, destination: str
) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin" and hasattr(library, "renameatx_np"):
        result = library.renameatx_np(
            directory_descriptor,
            source_bytes,
            directory_descriptor,
            destination_bytes,
            0x00000004,  # RENAME_EXCL
        )
    elif hasattr(library, "renameat2"):
        result = library.renameat2(
            directory_descriptor,
            source_bytes,
            directory_descriptor,
            destination_bytes,
            0x00000001,  # RENAME_NOREPLACE
        )
    else:
        raise MigrationError("atomic no-replace publication is unavailable")
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number == errno.EEXIST:
            raise MigrationError("migration discovery run already exists")
        raise MigrationError("migration discovery run publication failed")


def _write_artifacts(root: Path, timestamp: str, inventory: Mapping[str, Any], plan: ShadowPlan) -> Path:
    run_name = f"controller-discovery-{timestamp}"
    staging_name = f".{run_name}.{secrets.token_hex(8)}.tmp"
    with _private_directory(root) as (
        canonical_root,
        root_descriptor,
        register_post_validation_rollback,
        verify_artifact_root,
    ):
        try:
            os.mkdir(staging_name, 0o700, dir_fd=root_descriptor)
            os.fsync(root_descriptor)
        except OSError as error:
            raise MigrationError(
                "migration discovery staging directory is unavailable"
            ) from error
        try:
            run_descriptor = os.open(
                staging_name, DIRECTORY_FLAGS, dir_fd=root_descriptor
            )
        except BaseException:
            os.rmdir(staging_name, dir_fd=root_descriptor)
            os.fsync(root_descriptor)
            raise
        written: list[tuple[str, tuple[int, int]]] = []
        published = False
        try:
            _verify_directory_entry(
                root_descriptor,
                staging_name,
                run_descriptor,
                "migration discovery staging directory",
            )
            _validate_private_directory(os.fstat(run_descriptor))
            verify_artifact_root()
            published_identity = os.fstat(run_descriptor)

            def rollback_published_run() -> None:
                try:
                    descriptor = os.open(
                        run_name, DIRECTORY_FLAGS, dir_fd=root_descriptor
                    )
                except OSError:
                    return
                try:
                    current = os.fstat(descriptor)
                    if (current.st_dev, current.st_ino) != (
                        published_identity.st_dev,
                        published_identity.st_ino,
                    ):
                        return
                    for name, identity in reversed(written):
                        try:
                            entry = os.stat(
                                name,
                                dir_fd=descriptor,
                                follow_symlinks=False,
                            )
                            if (entry.st_dev, entry.st_ino) == identity:
                                os.unlink(name, dir_fd=descriptor)
                        except OSError:
                            pass
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                try:
                    os.rmdir(run_name, dir_fd=root_descriptor)
                except OSError:
                    return
                os.fsync(root_descriptor)

            register_post_validation_rollback(rollback_published_run)
            for name, value in (
                ("inventory.json", inventory),
                ("shadow-plan.json", plan.to_dict()),
            ):
                identity = _write_exclusive(run_descriptor, name, value)
                written.append((name, identity))
            os.fsync(run_descriptor)
            _rename_directory_no_replace(
                root_descriptor, staging_name, run_name
            )
            published = True
            os.fsync(root_descriptor)
            _verify_directory_entry(
                root_descriptor,
                run_name,
                run_descriptor,
                "migration discovery run directory",
            )
            _validate_private_directory(os.fstat(run_descriptor))
        except BaseException:
            for name, identity in reversed(written):
                try:
                    entry = os.stat(
                        name, dir_fd=run_descriptor, follow_symlinks=False
                    )
                    if (entry.st_dev, entry.st_ino) == identity:
                        os.unlink(name, dir_fd=run_descriptor)
                except (FileNotFoundError, OSError):
                    pass
            run_entry_is_current = True
            current_name = run_name if published else staging_name
            try:
                _verify_directory_entry(
                    root_descriptor,
                    current_name,
                    run_descriptor,
                    "migration discovery run directory",
                )
            except MigrationError:
                run_entry_is_current = False
            os.close(run_descriptor)
            if run_entry_is_current:
                try:
                    os.rmdir(current_name, dir_fd=root_descriptor)
                except OSError:
                    pass
                else:
                    os.fsync(root_descriptor)
            raise
        else:
            os.close(run_descriptor)
        return canonical_root / run_name


def discover_migration_state(
    adapter: Any,
    *,
    source_bank: BankRef,
    candidate_bank: BankRef,
    offline_package_manifest: Mapping[str, Any],
    approved_offline_package_digest: str,
    migration_paths: Mapping[str, Any],
    retain_watermark_reader: Callable[[], Mapping[str, Any]],
    private_catalog_digests: Mapping[str, str],
    timestamp: str,
    repository_catalog: Mapping[str, Any] | None = None,
) -> MigrationDiscovery:
    if not isinstance(source_bank, BankRef) or not isinstance(candidate_bank, BankRef) or source_bank == candidate_bank:
        raise MigrationError("source and candidate bank references must be explicit and distinct")
    if not isinstance(timestamp, str) or TIMESTAMP.fullmatch(timestamp) is None:
        raise MigrationError("timestamp must use YYYYmmddTHHMMSSZ")
    if not isinstance(private_catalog_digests, Mapping) or not private_catalog_digests:
        raise MigrationError("private catalog digests are required")
    if repository_catalog is None:
        repository_catalog = private_catalog_digests.get("repository_catalog")
        private_catalog_digests = {
            key: value
            for key, value in private_catalog_digests.items()
            if key != "repository_catalog"
        }
    if repository_catalog is None:
        raise MigrationError("resolved repository catalog is required")
    if not private_catalog_digests:
        raise MigrationError("private catalog digests are required")
    for key, value in private_catalog_digests.items():
        _identifier(key, "private catalog digest name")
        _sha(value, "private catalog digest")
    approved_package = _normalized(offline_package_manifest)

    try:
        before_generation = _adapter_generation_snapshot(adapter)
    except MigrationError:
        return MigrationDiscovery(
            False, ("adapter:migration_generation_unavailable",)
        )
    before_gate = _gate_snapshot(migration_paths)
    before_watermarks = _retain_watermark_snapshot(retain_watermark_reader)
    package_blockers = _package_blockers(
        approved_package, approved_offline_package_digest
    )
    try:
        before = _normalized(_with_retain_watermarks(
            adapter.read_migration_inventory(source_bank, candidate_bank),
            before_watermarks,
        ))
        after = _normalized(_with_retain_watermarks(
            adapter.read_migration_inventory(source_bank, candidate_bank),
            _retain_watermark_snapshot(retain_watermark_reader),
        ))
    except AdapterError:
        return MigrationDiscovery(False, ("adapter:migration_inventory_unavailable",))
    after_gate = _gate_snapshot(migration_paths)
    try:
        after_generation = _adapter_generation_snapshot(adapter)
    except MigrationError:
        return MigrationDiscovery(
            False, ("adapter:migration_generation_unavailable",)
        )

    blockers = _snapshot_blockers(before, source_bank, candidate_bank)
    blockers.extend(_snapshot_blockers(after, source_bank, candidate_bank))
    blockers.extend(package_blockers)
    if not blockers:
        repository_catalog = _validate_repository_catalog(
            repository_catalog,
            approved_package.get("tag_mapping_digest"),
        )
        if not hmac.compare_digest(
            before_generation.encode("utf-8"), after_generation.encode("utf-8")
        ):
            blockers.append("drift:adapter_generation")
        normalized_before = _normalized_snapshot(before)
        normalized_after = _normalized_snapshot(after)
        blockers.extend(
            _drift_blockers(
                normalized_before, normalized_after, before_gate, after_gate
            )
        )
        blockers.extend(_coverage_blockers(before, approved_package))
        try:
            _live_coverage(before, repository_catalog)
        except MigrationError as error:
            if str(error) == "migration item has conflicting repository scopes":
                blockers.append("coverage:conflicting_repository_scopes")
            else:
                raise
    blockers = sorted(set(blockers))
    if blockers:
        return MigrationDiscovery(False, tuple(blockers))

    normalized_snapshot = _normalized_snapshot(before)
    high_water = _high_water(normalized_snapshot)
    invalidations = _invalidations(normalized_snapshot)
    inventory = {
        "schema_version": 1,
        "adapter_generation": before_generation,
        "snapshot": normalized_snapshot,
        "high_water_manifest": high_water,
        "invalidation_manifest": invalidations,
        "completion_gate_snapshot": before_gate,
    }
    inventory_digest = digest(inventory)
    manifest_digest = _offline_package_digest(approved_package)
    plan = _shadow_plan(
        normalized_snapshot,
        source_bank,
        candidate_bank,
        approved_package,
        manifest_digest,
        inventory_digest,
        high_water,
        invalidations,
        private_catalog_digests,
        repository_catalog,
    )
    verify_shadow_plan(plan.to_dict(), repository_catalog=repository_catalog)
    artifact_root = Path(str(migration_paths["artifact_dir"])).expanduser()
    run_dir = _write_artifacts(artifact_root, timestamp, inventory, plan)
    return MigrationDiscovery(True, (), str(run_dir), inventory_digest, plan.plan_digest, plan)
