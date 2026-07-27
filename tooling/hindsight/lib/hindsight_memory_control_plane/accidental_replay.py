"""Digest-bound replay of raw documents from an accidentally selected bank."""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .canonical import canonical_bytes, digest, strict_json_loads
from .model import BankRef


class ReplayError(RuntimeError):
    """The accidental-bank replay contract was not satisfied."""


PLAN_KEYS = {
    "schema_version",
    "profile_id",
    "source_bank_id",
    "target_bank_id",
    "source_generation",
    "documents",
    "plan_digest",
}
DOCUMENT_KEYS = {
    "source_document_id",
    "target_document_id",
    "chronological_position",
    "created_at",
    "original_timestamp",
    "content_digest",
    "metadata",
    "tags",
    "retain_options",
    "record_digest",
}
RECEIPT_KEYS = {
    "schema_version",
    "plan_digest",
    "source_document_id",
    "target_document_id",
    "content_digest",
    "backup_evidence_digest",
    "replay_authorization_digest",
    "submission_digest",
    "target_projection_digest",
    "operation_id",
    "pre_generation",
    "post_generation",
    "receipt_digest",
}
BACKUP_ARTIFACT_NAMES = {
    "codex_export",
    "engineering_export",
    "full_schema_backup",
}
CLOSEOUT_PLAN_KEYS = {
    "schema_version",
    "replay_plan_digest",
    "replay_verification_digest",
    "backup_evidence_digest",
    "pre_delete_generation",
    "pre_delete_bank_ids",
    "pre_delete_bank_set_digest",
    "closeout_plan_digest",
}


def _sha(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ReplayError(f"{label} is invalid")
    return value


def _identifier(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 4096
        or any(character in value for character in "\r\n\0")
    ):
        raise ReplayError(f"{label} is invalid")
    return value


def _normalized(value: Any) -> Any:
    try:
        return strict_json_loads(canonical_bytes(value))
    except Exception:
        raise ReplayError("replay value is not canonical JSON") from None


def _content_digest(document: Mapping[str, Any]) -> str:
    content = document.get("original_text")
    if not isinstance(content, str) or not content:
        raise ReplayError("replay source original text is unavailable")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _target_document_id(source_bank_id: str, source_document_id: str) -> str:
    identity = hashlib.sha256(
        canonical_bytes(
            {
                "source_bank_id": source_bank_id,
                "source_document_id": source_document_id,
            }
        )
    ).hexdigest()
    return f"misroute-{source_bank_id}-{identity[:40]}"


def _record_projection(
    document: Mapping[str, Any],
    *,
    content_digest: str,
) -> Mapping[str, Any]:
    return _normalized(
        {
            "id": document.get("id"),
            "bank_id": document.get("bank_id"),
            "created_at": document.get("created_at"),
            "updated_at": document.get("updated_at"),
            "content_digest": content_digest,
            "tags": document.get("tags", []),
            "document_metadata": document.get("document_metadata") or {},
            "retain_params": document.get("retain_params") or {},
            "observation_scopes": document.get("observation_scopes"),
        }
    )


def _document_descriptor(
    document: Mapping[str, Any],
    *,
    source_bank_id: str,
) -> Mapping[str, Any]:
    source_document_id = _identifier(
        document.get("id"),
        "source document ID",
    )
    if document.get("bank_id") != source_bank_id:
        raise ReplayError("source document bank identity changed")
    created_at = _identifier(document.get("created_at"), "source created-at")
    content_digest = _content_digest(document)
    projection = _record_projection(
        document,
        content_digest=content_digest,
    )
    metadata = document.get("document_metadata") or {}
    tags = document.get("tags") or []
    retain_params = document.get("retain_params") or {}
    if (
        not isinstance(metadata, Mapping)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in metadata.items()
        )
        or not isinstance(tags, list)
        or any(not isinstance(tag, str) or not tag for tag in tags)
        or not isinstance(retain_params, Mapping)
    ):
        raise ReplayError("source document manifest is invalid")
    original_timestamp = retain_params.get("event_date")
    if original_timestamp is None:
        original_timestamp = created_at
    original_timestamp = _identifier(
        original_timestamp,
        "source original timestamp",
    )
    observation_scopes = document.get("observation_scopes")
    if observation_scopes is None:
        observation_scopes = retain_params.get("observation_scopes")
    context = retain_params.get("context")
    strategy = retain_params.get("strategy")
    if (
        (context is not None and not isinstance(context, str))
        or (strategy is not None and not isinstance(strategy, str))
        or (
            observation_scopes is not None
            and not (
                isinstance(observation_scopes, str)
                or (
                    isinstance(observation_scopes, list)
                    and all(
                        isinstance(scope, list)
                        and scope
                        and all(
                            isinstance(tag, str) and tag
                            for tag in scope
                        )
                        for scope in observation_scopes
                    )
                )
            )
        )
    ):
        raise ReplayError("source document retain options are invalid")
    retain_options = {
        "context": context,
        "strategy": strategy,
        "observation_scopes": observation_scopes,
    }
    return {
        "source_document_id": source_document_id,
        "target_document_id": _target_document_id(
            source_bank_id,
            source_document_id,
        ),
        "created_at": created_at,
        "original_timestamp": original_timestamp,
        "content_digest": content_digest,
        "metadata": dict(metadata),
        "tags": list(tags),
        "retain_options": retain_options,
        "record_digest": digest(projection),
    }


def create_replay_plan(
    adapter: Any,
    *,
    source_bank: BankRef,
    target_bank: BankRef,
) -> Mapping[str, Any]:
    if (
        not isinstance(source_bank, BankRef)
        or not isinstance(target_bank, BankRef)
        or source_bank.profile_id != target_bank.profile_id
        or source_bank.bank_id != "codex"
        or target_bank.bank_id != "engineering"
    ):
        raise ReplayError(
            "accidental replay requires exact codex to engineering banks"
        )
    before_generation = _identifier(
        adapter.read_migration_generation(),
        "source generation",
    )
    document_ids = adapter.list_replay_document_ids(source_bank)
    if (
        not isinstance(document_ids, Sequence)
        or isinstance(document_ids, (str, bytes))
        or not document_ids
    ):
        raise ReplayError("source document inventory is empty")
    normalized_ids = tuple(
        _identifier(document_id, "source document ID")
        for document_id in document_ids
    )
    if len(normalized_ids) != len(set(normalized_ids)):
        raise ReplayError("source document inventory contains duplicates")
    descriptors = [
        _document_descriptor(
            adapter.read_replay_document(source_bank, document_id),
            source_bank_id=source_bank.bank_id,
        )
        for document_id in normalized_ids
    ]
    descriptors.sort(
        key=lambda entry: (
            entry["created_at"],
            entry["source_document_id"],
        )
    )
    descriptors = [
        {**entry, "chronological_position": position}
        for position, entry in enumerate(descriptors, start=1)
    ]
    after_generation = _identifier(
        adapter.read_migration_generation(),
        "source generation",
    )
    if not hmac.compare_digest(
        before_generation.encode("utf-8"),
        after_generation.encode("utf-8"),
    ):
        raise ReplayError("source generation changed during replay planning")
    body = {
        "schema_version": 1,
        "profile_id": source_bank.profile_id,
        "source_bank_id": source_bank.bank_id,
        "target_bank_id": target_bank.bank_id,
        "source_generation": before_generation,
        "documents": descriptors,
    }
    return _normalized({**body, "plan_digest": digest(body)})


def verify_replay_plan(value: Mapping[str, Any]) -> Mapping[str, Any]:
    plan = _normalized(value)
    if (
        not isinstance(plan, Mapping)
        or set(plan) != PLAN_KEYS
        or plan.get("schema_version") != 1
        or plan.get("source_bank_id") != "codex"
        or plan.get("target_bank_id") != "engineering"
    ):
        raise ReplayError("replay plan is invalid")
    profile_id = _identifier(plan.get("profile_id"), "profile ID")
    source_generation = _identifier(
        plan.get("source_generation"),
        "source generation",
    )
    documents = plan.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ReplayError("replay plan document set is invalid")
    normalized_documents = []
    identities: set[str] = set()
    targets: set[str] = set()
    for entry in documents:
        if not isinstance(entry, Mapping) or set(entry) != DOCUMENT_KEYS:
            raise ReplayError("replay plan document is invalid")
        source_id = _identifier(
            entry["source_document_id"],
            "source document ID",
        )
        target_id = _identifier(
            entry["target_document_id"],
            "target document ID",
        )
        if target_id != _target_document_id("codex", source_id):
            raise ReplayError("replay plan target document ID is invalid")
        if source_id in identities or target_id in targets:
            raise ReplayError("replay plan document identity is duplicated")
        identities.add(source_id)
        targets.add(target_id)
        normalized_documents.append(
            {
                "source_document_id": source_id,
                "target_document_id": target_id,
                "chronological_position": entry["chronological_position"],
                "created_at": _identifier(
                    entry["created_at"],
                    "source created-at",
                ),
                "original_timestamp": _identifier(
                    entry["original_timestamp"],
                    "source original timestamp",
                ),
                "content_digest": _sha(
                    entry["content_digest"],
                    "content digest",
                ),
                "metadata": _normalized(entry["metadata"]),
                "tags": _normalized(entry["tags"]),
                "retain_options": _normalized(
                    entry["retain_options"]
                ),
                "record_digest": _sha(
                    entry["record_digest"],
                    "record digest",
                ),
            }
        )
        if (
            not isinstance(normalized_documents[-1]["metadata"], Mapping)
            or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in normalized_documents[-1][
                    "metadata"
                ].items()
            )
            or not isinstance(normalized_documents[-1]["tags"], list)
            or any(
                not isinstance(tag, str) or not tag
                for tag in normalized_documents[-1]["tags"]
            )
            or not isinstance(
                normalized_documents[-1]["retain_options"],
                Mapping,
            )
            or set(normalized_documents[-1]["retain_options"])
            != {"context", "strategy", "observation_scopes"}
        ):
            raise ReplayError("replay plan document manifest is invalid")
        retain_options = normalized_documents[-1]["retain_options"]
        for key in ("context", "strategy"):
            if retain_options[key] is not None and not isinstance(
                retain_options[key],
                str,
            ):
                raise ReplayError(
                    "replay plan retain options are invalid"
                )
        observation_scopes = retain_options["observation_scopes"]
        if observation_scopes is not None and not (
            isinstance(observation_scopes, str)
            or (
                isinstance(observation_scopes, list)
                and all(
                    isinstance(scope, list)
                    and scope
                    and all(
                        isinstance(tag, str) and tag
                        for tag in scope
                    )
                    for scope in observation_scopes
                )
            )
        ):
            raise ReplayError("replay plan retain options are invalid")
        if type(entry["chronological_position"]) is not int or (
            entry["chronological_position"] != len(normalized_documents)
        ):
            raise ReplayError("replay plan chronological position is invalid")
    if normalized_documents != sorted(
        normalized_documents,
        key=lambda entry: (
            entry["created_at"],
            entry["source_document_id"],
        ),
    ):
        raise ReplayError("replay plan documents are not chronological")
    body = {
        "schema_version": 1,
        "profile_id": profile_id,
        "source_bank_id": "codex",
        "target_bank_id": "engineering",
        "source_generation": source_generation,
        "documents": normalized_documents,
    }
    plan_digest = _sha(plan.get("plan_digest"), "replay plan digest")
    if not hmac.compare_digest(
        plan_digest.encode("ascii"),
        digest(body).encode("ascii"),
    ):
        raise ReplayError("replay plan digest does not match")
    return _normalized({**body, "plan_digest": plan_digest})


def _validate_source_manifest(
    adapter: Any,
    plan: Mapping[str, Any],
    source_bank: BankRef,
) -> None:
    current_ids = adapter.list_replay_document_ids(source_bank)
    if not isinstance(current_ids, Sequence) or isinstance(
        current_ids,
        (str, bytes),
    ) or any(type(document_id) is not str for document_id in current_ids):
        raise ReplayError("source manifest changed before replay")
    planned_ids = {
        entry["source_document_id"] for entry in plan["documents"]
    }
    if set(current_ids) != planned_ids or len(current_ids) != len(planned_ids):
        raise ReplayError("source manifest changed before replay")
    planned = {
        entry["source_document_id"]: entry for entry in plan["documents"]
    }
    for source_document_id in current_ids:
        document = adapter.read_replay_document(
            source_bank,
            source_document_id,
        )
        descriptor = _document_descriptor(
            document,
            source_bank_id=source_bank.bank_id,
        )
        planned_descriptor = {
            key: value
            for key, value in planned[source_document_id].items()
            if key != "chronological_position"
        }
        if descriptor != planned_descriptor:
            raise ReplayError("source manifest changed before replay")


def _verify_receipt_chain(
    plan: Mapping[str, Any],
    receipts: Sequence[Mapping[str, Any]],
    *,
    live_generation: str | None = None,
) -> None:
    if receipts and receipts[0]["pre_generation"] != plan["source_generation"]:
        raise ReplayError(
            "replay receipt chain does not start at plan generation"
        )
    for previous, current in zip(receipts, receipts[1:]):
        if previous["post_generation"] != current["pre_generation"]:
            raise ReplayError("replay receipt generation chain is broken")
    if (
        live_generation is not None
        and receipts
        and receipts[-1]["post_generation"] != live_generation
    ):
        raise ReplayError("current generation does not match replay receipts")


def _replay_item(
    document: Mapping[str, Any],
    descriptor: Mapping[str, Any],
    plan_digest: str,
) -> Mapping[str, Any]:
    metadata = descriptor["metadata"]
    provenance = {
        "hindsight_replay_plan_digest": plan_digest,
        "hindsight_replay_source_bank": "codex",
        "hindsight_replay_source_document_id":
            descriptor["source_document_id"],
    }
    if set(metadata) & set(provenance):
        raise ReplayError("source document metadata conflicts with replay provenance")
    tags = descriptor["tags"]
    retained_tags = list(dict.fromkeys([
        *tags,
        "source:codex",
        "lifecycle:misroute-replay",
    ]))
    timestamp = descriptor["original_timestamp"]
    if not isinstance(timestamp, str):
        raise ReplayError("source timestamp cannot be replayed safely")
    item: dict[str, Any] = {
        "content": document["original_text"],
        "document_id": descriptor["target_document_id"],
        "timestamp": timestamp,
        "metadata": {**metadata, **provenance},
        "tags": retained_tags,
        "update_mode": "replace",
    }
    retain_options = descriptor["retain_options"]
    context = retain_options["context"]
    if context is not None:
        if not isinstance(context, str):
            raise ReplayError("source context cannot be replayed safely")
        item["context"] = context
    strategy = retain_options["strategy"]
    if strategy is not None:
        if not isinstance(strategy, str):
            raise ReplayError("source strategy cannot be replayed safely")
        item["strategy"] = strategy
    observation_scopes = retain_options["observation_scopes"]
    if observation_scopes is not None:
        if not (
            isinstance(observation_scopes, str)
            or (
                isinstance(observation_scopes, list)
                and all(
                    isinstance(scope, list)
                    and scope
                    and all(
                        isinstance(tag, str) and tag
                        for tag in scope
                    )
                    for scope in observation_scopes
                )
            )
        ):
            raise ReplayError(
                "source observation scopes cannot be replayed safely"
            )
        item["observation_scopes"] = observation_scopes
    return _normalized(item)


def _submission_projection(item: Mapping[str, Any]) -> Mapping[str, Any]:
    content = item.get("content")
    if not isinstance(content, str) or not content:
        raise ReplayError("replay submission content is invalid")
    return _normalized(
        {
            **{
                key: value
                for key, value in item.items()
                if key != "content"
            },
            "content_digest": hashlib.sha256(
                content.encode("utf-8")
            ).hexdigest(),
        }
    )


def _expected_submission_projection(
    descriptor: Mapping[str, Any],
    plan_digest: str,
) -> Mapping[str, Any]:
    projection = _replay_item(
        {"original_text": "content is represented by its digest"},
        descriptor,
        plan_digest,
    )
    return _normalized(
        {
            **{
                key: value
                for key, value in projection.items()
                if key != "content"
            },
            "content_digest": descriptor["content_digest"],
        }
    )


def _target_projection(document: Mapping[str, Any]) -> Mapping[str, Any]:
    retain_params = document.get("retain_params") or {}
    if not isinstance(retain_params, Mapping):
        raise ReplayError("replay target retain parameters are invalid")
    observation_scopes = document.get("observation_scopes")
    if observation_scopes is None:
        observation_scopes = retain_params.get("observation_scopes")
    return _normalized(
        {
            "document_id": document.get("id"),
            "content_digest": _content_digest(document),
            "timestamp": retain_params.get("event_date"),
            "metadata": document.get("document_metadata") or {},
            "tags": sorted(document.get("tags") or []),
            "context": retain_params.get("context"),
            "observation_scopes": observation_scopes,
        }
    )


def _expected_target_projection(
    descriptor: Mapping[str, Any],
    plan_digest: str,
) -> Mapping[str, Any]:
    submission = _expected_submission_projection(
        descriptor,
        plan_digest,
    )
    return _normalized(
        {
            "document_id": submission["document_id"],
            "content_digest": submission["content_digest"],
            "timestamp": submission["timestamp"],
            "metadata": submission["metadata"],
            "tags": sorted(submission["tags"]),
            "context": submission.get("context"),
            "observation_scopes": submission.get(
                "observation_scopes"
            ),
        }
    )


def _receipt_body(
    *,
    plan_digest: str,
    descriptor: Mapping[str, Any],
    backup_evidence_digest: str,
    replay_authorization_digest: str,
    submission_digest: str,
    target_projection_digest: str,
    operation_id: str,
    pre_generation: str,
    post_generation: str,
) -> Mapping[str, Any]:
    return {
        "schema_version": 1,
        "plan_digest": plan_digest,
        "source_document_id": descriptor["source_document_id"],
        "target_document_id": descriptor["target_document_id"],
        "content_digest": descriptor["content_digest"],
        "backup_evidence_digest": backup_evidence_digest,
        "replay_authorization_digest": replay_authorization_digest,
        "submission_digest": submission_digest,
        "target_projection_digest": target_projection_digest,
        "operation_id": operation_id,
        "pre_generation": pre_generation,
        "post_generation": post_generation,
    }


def _verify_receipt(
    receipt: Mapping[str, Any],
    plan: Mapping[str, Any],
    descriptor: Mapping[str, Any],
) -> Mapping[str, Any]:
    normalized = _normalized(receipt)
    if not isinstance(normalized, Mapping) or set(normalized) != RECEIPT_KEYS:
        raise ReplayError("replay receipt is invalid")
    body = {key: normalized[key] for key in RECEIPT_KEYS - {"receipt_digest"}}
    if (
        body["schema_version"] != 1
        or body["plan_digest"] != plan["plan_digest"]
        or body["source_document_id"] != descriptor["source_document_id"]
        or body["target_document_id"] != descriptor["target_document_id"]
        or body["content_digest"] != descriptor["content_digest"]
        or body["submission_digest"]
        != digest(
            _expected_submission_projection(
                descriptor,
                plan["plan_digest"],
            )
        )
    ):
        raise ReplayError("replay receipt does not match plan")
    backup_evidence_digest = _sha(
        body["backup_evidence_digest"],
        "replay backup evidence digest",
    )
    expected_authorization_digest = digest(
        {
            "replay_plan_digest": plan["plan_digest"],
            "backup_evidence_digest": backup_evidence_digest,
        }
    )
    if not hmac.compare_digest(
        _sha(
            body["replay_authorization_digest"],
            "replay authorization digest",
        ).encode("ascii"),
        expected_authorization_digest.encode("ascii"),
    ):
        raise ReplayError("replay receipt authorization does not match")
    _sha(body["target_projection_digest"], "replay target projection digest")
    receipt_digest = _sha(
        normalized["receipt_digest"],
        "replay receipt digest",
    )
    if not hmac.compare_digest(
        receipt_digest.encode("ascii"),
        digest(body).encode("ascii"),
    ):
        raise ReplayError("replay receipt digest does not match")
    _identifier(body["operation_id"], "replay operation ID")
    _identifier(body["pre_generation"], "pre-replay generation")
    _identifier(body["post_generation"], "post-replay generation")
    return normalized


def apply_replay_plan(
    adapter: Any,
    plan_value: Mapping[str, Any],
    *,
    approval_digest: str,
    backup_evidence: Mapping[str, Any],
    existing_receipts: Sequence[Mapping[str, Any]] = (),
    timeout_seconds: float = 1800,
    poll_interval_seconds: float = 1,
    receipt_writer: Callable[
        [Sequence[Mapping[str, Any]]],
        None,
    ]
    | None = None,
) -> list[Mapping[str, Any]]:
    plan = verify_replay_plan(plan_value)
    backup_evidence_digest = _backup_evidence_digest(backup_evidence)
    replay_authorization_digest = digest(
        {
            "replay_plan_digest": plan["plan_digest"],
            "backup_evidence_digest": backup_evidence_digest,
        }
    )
    if not (
        isinstance(approval_digest, str)
        and hmac.compare_digest(
            approval_digest.encode("utf-8"),
            replay_authorization_digest.encode("ascii"),
        )
    ):
        raise ReplayError("replay mutation authorization does not match")
    if (
        not isinstance(existing_receipts, Sequence)
        or isinstance(existing_receipts, (str, bytes))
    ):
        raise ReplayError("replay receipt prefix is invalid")
    if (
        type(timeout_seconds) not in (int, float)
        or not 0 < timeout_seconds <= 86400
        or type(poll_interval_seconds) not in (int, float)
        or not 0 <= poll_interval_seconds <= 60
    ):
        raise ReplayError("replay wait policy is invalid")
    source_bank = BankRef(plan["profile_id"], plan["source_bank_id"])
    target_bank = BankRef(plan["profile_id"], plan["target_bank_id"])
    receipts = [
        _verify_receipt(receipt, plan, descriptor)
        for receipt, descriptor in zip(
            existing_receipts,
            plan["documents"],
            strict=False,
        )
    ]
    if any(
        receipt["backup_evidence_digest"] != backup_evidence_digest
        or receipt["replay_authorization_digest"]
        != replay_authorization_digest
        for receipt in receipts
    ):
        raise ReplayError("replay receipts use different backup evidence")
    if len(receipts) != len(existing_receipts) or len(receipts) > len(
        plan["documents"]
    ):
        raise ReplayError("replay receipt prefix is invalid")
    before_source_read = _identifier(
        adapter.read_migration_generation(),
        "current source generation",
    )
    expected_generation = (
        receipts[-1]["post_generation"]
        if receipts
        else plan["source_generation"]
    )
    if before_source_read != expected_generation:
        raise ReplayError("current generation does not match replay authority")
    _verify_receipt_chain(
        plan,
        receipts,
        live_generation=before_source_read,
    )
    _validate_source_manifest(adapter, plan, source_bank)
    after_source_read = _identifier(
        adapter.read_migration_generation(),
        "current source generation",
    )
    if after_source_read != before_source_read:
        raise ReplayError("source generation changed while loading replay content")
    for descriptor, receipt in zip(
        plan["documents"],
        receipts,
        strict=False,
    ):
        operation = adapter.read_replay_operation(
            target_bank,
            receipt["operation_id"],
        )
        if operation.get("status") != "completed":
            raise ReplayError("receipt operation is not complete")
        target_document = adapter.find_replay_document(
            target_bank,
            descriptor["target_document_id"],
        )
        if target_document is None:
            raise ReplayError("receipt target does not match")
        target_projection = _target_projection(target_document)
        if (
            target_projection
            != _expected_target_projection(
                descriptor,
                plan["plan_digest"],
            )
            or digest(target_projection)
            != receipt["target_projection_digest"]
        ):
            raise ReplayError("receipt target does not match")
    current_generation = after_source_read
    for descriptor in plan["documents"][len(receipts):]:
        if (
            adapter.find_replay_document(
                target_bank,
                descriptor["target_document_id"],
            )
            is not None
        ):
            raise ReplayError("unexpected replay target collision")
        pre_generation = _identifier(
            adapter.read_migration_generation(),
            "pre-replay generation",
        )
        if pre_generation != current_generation:
            raise ReplayError("target generation changed between replay batches")
        source_document = adapter.read_replay_document(
            source_bank,
            descriptor["source_document_id"],
        )
        current_descriptor = _document_descriptor(
            source_document,
            source_bank_id=source_bank.bank_id,
        )
        planned_descriptor = {
            key: value
            for key, value in descriptor.items()
            if key != "chronological_position"
        }
        if current_descriptor != planned_descriptor:
            raise ReplayError("source manifest changed before replay")
        replay_item = _replay_item(
            source_document,
            descriptor,
            plan["plan_digest"],
        )
        submission_digest = digest(_submission_projection(replay_item))
        submission = adapter.submit_replay_document(
            target_bank,
            replay_item,
        )
        operation_id = _identifier(
            submission.get("operation_id"),
            "replay operation ID",
        )
        deadline = time.monotonic() + float(timeout_seconds)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReplayError("replay operation timed out")
            try:
                status = adapter.read_replay_operation(
                    target_bank,
                    operation_id,
                ).get("status")
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
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ReplayError("replay operation timed out") from error
                if poll_interval_seconds:
                    time.sleep(
                        min(float(poll_interval_seconds), remaining)
                    )
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReplayError("replay operation timed out")
            if status == "completed":
                break
            if status in {"failed", "cancelled", "not_found"}:
                raise ReplayError("replay operation failed")
            if status not in {"pending", "processing"}:
                raise ReplayError("replay operation status is invalid")
            if poll_interval_seconds:
                time.sleep(
                    min(float(poll_interval_seconds), remaining)
                )
        target_document = adapter.read_replay_document(
            target_bank,
            descriptor["target_document_id"],
        )
        target_projection = _target_projection(target_document)
        if target_projection != _expected_target_projection(
            descriptor,
            plan["plan_digest"],
        ):
            raise ReplayError("replay target projection does not match")
        target_projection_digest = digest(target_projection)
        post_generation = _identifier(
            adapter.read_migration_generation(),
            "post-replay generation",
        )
        _validate_source_manifest(adapter, plan, source_bank)
        after_source_validation = _identifier(
            adapter.read_migration_generation(),
            "post-replay source generation",
        )
        if after_source_validation != post_generation:
            raise ReplayError(
                "source generation changed during replay validation"
            )
        body = _receipt_body(
            plan_digest=plan["plan_digest"],
            descriptor=descriptor,
            backup_evidence_digest=backup_evidence_digest,
            replay_authorization_digest=replay_authorization_digest,
            submission_digest=submission_digest,
            target_projection_digest=target_projection_digest,
            operation_id=operation_id,
            pre_generation=pre_generation,
            post_generation=post_generation,
        )
        receipt = _normalized({**body, "receipt_digest": digest(body)})
        receipts.append(receipt)
        if receipt_writer is not None:
            try:
                receipt_writer(tuple(receipts))
            except Exception:
                raise ReplayError("replay receipt checkpoint failed") from None
        current_generation = post_generation
    return receipts


def replay_receipt_status(
    plan_value: Mapping[str, Any],
    receipt_values: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    plan = verify_replay_plan(plan_value)
    if (
        not isinstance(receipt_values, Sequence)
        or isinstance(receipt_values, (str, bytes))
        or len(receipt_values) > len(plan["documents"])
    ):
        raise ReplayError("replay receipt prefix is invalid")
    receipts = [
        _verify_receipt(receipt, plan, descriptor)
        for receipt, descriptor in zip(
            receipt_values,
            plan["documents"],
            strict=False,
        )
    ]
    if receipts and len(
        {receipt["backup_evidence_digest"] for receipt in receipts}
    ) != 1:
        raise ReplayError("replay receipts use different backup evidence")
    _verify_receipt_chain(plan, receipts)
    result = {
        "schema_version": 1,
        "plan_digest": plan["plan_digest"],
        "planned_document_count": len(plan["documents"]),
        "completed_document_count": len(receipts),
        "complete": len(receipts) == len(plan["documents"]),
        "receipt_set_digest": digest(receipts),
    }
    return _normalized({**result, "status_digest": digest(result)})


def verify_replay_receipts(
    adapter: Any,
    plan_value: Mapping[str, Any],
    receipt_values: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    plan = verify_replay_plan(plan_value)
    if (
        not isinstance(receipt_values, Sequence)
        or isinstance(receipt_values, (str, bytes))
        or len(receipt_values) != len(plan["documents"])
    ):
        raise ReplayError("replay receipt coverage is incomplete")
    target_bank = BankRef(plan["profile_id"], plan["target_bank_id"])
    source_bank = BankRef(plan["profile_id"], plan["source_bank_id"])
    receipts = [
        _verify_receipt(receipt, plan, descriptor)
        for receipt, descriptor in zip(
            receipt_values,
            plan["documents"],
            strict=True,
        )
    ]
    if len(
        {receipt["backup_evidence_digest"] for receipt in receipts}
    ) != 1:
        raise ReplayError("replay receipts use different backup evidence")
    before_verification = _identifier(
        adapter.read_migration_generation(),
        "current replay generation",
    )
    _verify_receipt_chain(
        plan,
        receipts,
        live_generation=before_verification,
    )
    _validate_source_manifest(adapter, plan, source_bank)
    for descriptor, receipt in zip(
        plan["documents"],
        receipts,
        strict=True,
    ):
        operation = adapter.read_replay_operation(
            target_bank,
            receipt["operation_id"],
        )
        if operation.get("status") != "completed":
            raise ReplayError("replay operation is not complete")
        target_document = adapter.read_replay_document(
            target_bank,
            descriptor["target_document_id"],
        )
        target_projection = _target_projection(target_document)
        if (
            target_projection
            != _expected_target_projection(
                descriptor,
                plan["plan_digest"],
            )
            or digest(target_projection)
            != receipt["target_projection_digest"]
        ):
            raise ReplayError("replay target projection does not match")
    processing_evidence = _normalized(
        adapter.read_replay_processing_evidence(
            source_bank,
            target_bank,
            plan["documents"],
        )
    )
    expected_processing_keys = {
        "schema_version",
        "snapshot_generation",
        "documents",
        "representative_recall",
        "processing_evidence_digest",
    }
    if (
        not isinstance(processing_evidence, Mapping)
        or set(processing_evidence) != expected_processing_keys
        or processing_evidence["schema_version"] != 1
        or processing_evidence["snapshot_generation"]
        != before_verification
        or not isinstance(processing_evidence["documents"], list)
        or len(processing_evidence["documents"]) != len(plan["documents"])
        or not isinstance(
            processing_evidence["representative_recall"],
            Mapping,
        )
    ):
        raise ReplayError("replay processing evidence is invalid")
    expected_targets = [
        descriptor["target_document_id"]
        for descriptor in plan["documents"]
    ]
    for expected_target, evidence in zip(
        expected_targets,
        processing_evidence["documents"],
        strict=True,
    ):
        if (
            not isinstance(evidence, Mapping)
            or set(evidence)
            != {
                "target_document_id",
                "memory_unit_count",
                "embedded_memory_unit_count",
            }
            or evidence["target_document_id"] != expected_target
            or type(evidence["memory_unit_count"]) is not int
            or evidence["memory_unit_count"] < 1
            or evidence["embedded_memory_unit_count"]
            != evidence["memory_unit_count"]
        ):
            raise ReplayError("replay processing evidence is invalid")
    representative = processing_evidence["representative_recall"]
    if (
        set(representative)
        != {
            "target_document_id",
            "query_digest",
            "result_count",
            "result_projection_digest",
        }
        or representative["target_document_id"] not in expected_targets
        or type(representative["result_count"]) is not int
        or representative["result_count"] < 1
    ):
        raise ReplayError("replay processing evidence is invalid")
    _sha(representative["query_digest"], "replay recall query digest")
    _sha(
        representative["result_projection_digest"],
        "replay recall result digest",
    )
    processing_body = {
        key: processing_evidence[key]
        for key in expected_processing_keys
        - {"processing_evidence_digest"}
    }
    if not hmac.compare_digest(
        _sha(
            processing_evidence["processing_evidence_digest"],
            "processing evidence digest",
        ).encode("ascii"),
        digest(processing_body).encode("ascii"),
    ):
        raise ReplayError("replay processing evidence digest does not match")
    after_verification = _identifier(
        adapter.read_migration_generation(),
        "current replay generation",
    )
    if after_verification != before_verification:
        raise ReplayError("replay generation changed during verification")
    result = {
        "schema_version": 1,
        "status": "verified",
        "plan_digest": plan["plan_digest"],
        "document_count": len(receipts),
        "receipt_set_digest": digest(receipts),
        "backup_evidence_digest": receipts[0]["backup_evidence_digest"],
        "final_generation": receipts[-1]["post_generation"],
        "processing_evidence": processing_evidence,
    }
    return _normalized({**result, "verification_digest": digest(result)})


def _backup_evidence_digest(value: Mapping[str, Any]) -> str:
    evidence = _normalized(value)
    if (
        not isinstance(evidence, Mapping)
        or set(evidence) != {"schema_version", "artifacts"}
        or evidence["schema_version"] != 1
        or not isinstance(evidence["artifacts"], Mapping)
        or set(evidence["artifacts"]) != BACKUP_ARTIFACT_NAMES
    ):
        raise ReplayError("replay backup evidence is invalid")
    for artifact in evidence["artifacts"].values():
        if (
            not isinstance(artifact, Mapping)
            or set(artifact)
            != {
                "artifact_digest",
                "encrypted",
                "restore_evidence_digest",
                "restore_tested",
            }
            or artifact["encrypted"] is not True
            or artifact["restore_tested"] is not True
        ):
            raise ReplayError("replay backup evidence is invalid")
        _sha(artifact["artifact_digest"], "backup artifact digest")
        _sha(
            artifact["restore_evidence_digest"],
            "backup restore evidence digest",
        )
    return digest(evidence)


def replay_apply_authorization_digest(
    replay_plan_value: Mapping[str, Any],
    backup_evidence: Mapping[str, Any],
) -> str:
    replay_plan = verify_replay_plan(replay_plan_value)
    return digest(
        {
            "replay_plan_digest": replay_plan["plan_digest"],
            "backup_evidence_digest":
                _backup_evidence_digest(backup_evidence),
        }
    )


def _verification_digest(value: Mapping[str, Any], plan_digest: str) -> str:
    verification = _normalized(value)
    expected = {
        "schema_version",
        "status",
        "plan_digest",
        "document_count",
        "receipt_set_digest",
        "backup_evidence_digest",
        "final_generation",
        "processing_evidence",
        "verification_digest",
    }
    if (
        not isinstance(verification, Mapping)
        or set(verification) != expected
        or verification["schema_version"] != 1
        or verification["status"] != "verified"
        or verification["plan_digest"] != plan_digest
        or type(verification["document_count"]) is not int
        or verification["document_count"] < 1
    ):
        raise ReplayError("replay verification is invalid")
    body = {
        key: verification[key]
        for key in expected - {"verification_digest"}
    }
    verification_digest = _sha(
        verification["verification_digest"],
        "replay verification digest",
    )
    if not hmac.compare_digest(
        verification_digest.encode("ascii"),
        digest(body).encode("ascii"),
    ):
        raise ReplayError("replay verification digest does not match")
    _sha(verification["receipt_set_digest"], "receipt set digest")
    _sha(
        verification["backup_evidence_digest"],
        "backup evidence digest",
    )
    _identifier(verification["final_generation"], "final generation")
    return verification_digest


def create_replay_closeout_plan(
    adapter: Any,
    replay_plan_value: Mapping[str, Any],
    verification_value: Mapping[str, Any],
    backup_evidence: Mapping[str, Any],
) -> Mapping[str, Any]:
    replay_plan = verify_replay_plan(replay_plan_value)
    verification_digest = _verification_digest(
        verification_value,
        replay_plan["plan_digest"],
    )
    backup_digest = _backup_evidence_digest(backup_evidence)
    if verification_value.get("backup_evidence_digest") != backup_digest:
        raise ReplayError(
            "replay verification does not match backup evidence"
        )
    bank_ids = adapter.list_replay_bank_ids()
    if (
        not isinstance(bank_ids, Sequence)
        or isinstance(bank_ids, (str, bytes))
        or "codex" not in bank_ids
        or "engineering" not in bank_ids
        or len(bank_ids) != len(set(bank_ids))
    ):
        raise ReplayError("replay closeout bank set is invalid")
    generation = _identifier(
        adapter.read_migration_generation(),
        "pre-delete generation",
    )
    body = {
        "schema_version": 1,
        "replay_plan_digest": replay_plan["plan_digest"],
        "replay_verification_digest": verification_digest,
        "backup_evidence_digest": backup_digest,
        "pre_delete_generation": generation,
        "pre_delete_bank_ids": sorted(bank_ids),
        "pre_delete_bank_set_digest": digest(sorted(bank_ids)),
    }
    return _normalized(
        {**body, "closeout_plan_digest": digest(body)}
    )


def _verify_closeout_plan(
    value: Mapping[str, Any],
    *,
    replay_plan_digest: str,
    replay_verification_digest: str,
    backup_evidence_digest: str,
) -> Mapping[str, Any]:
    closeout = _normalized(value)
    if (
        not isinstance(closeout, Mapping)
        or set(closeout) != CLOSEOUT_PLAN_KEYS
        or closeout["schema_version"] != 1
        or closeout["replay_plan_digest"] != replay_plan_digest
        or closeout["replay_verification_digest"]
        != replay_verification_digest
        or closeout["backup_evidence_digest"] != backup_evidence_digest
    ):
        raise ReplayError("replay closeout plan is invalid")
    body = {
        key: closeout[key]
        for key in CLOSEOUT_PLAN_KEYS - {"closeout_plan_digest"}
    }
    closeout_digest = _sha(
        closeout["closeout_plan_digest"],
        "closeout plan digest",
    )
    if not hmac.compare_digest(
        closeout_digest.encode("ascii"),
        digest(body).encode("ascii"),
    ):
        raise ReplayError("replay closeout plan digest does not match")
    _identifier(
        closeout["pre_delete_generation"],
        "pre-delete generation",
    )
    _sha(
        closeout["pre_delete_bank_set_digest"],
        "pre-delete bank-set digest",
    )
    bank_ids = closeout["pre_delete_bank_ids"]
    if (
        not isinstance(bank_ids, list)
        or bank_ids != sorted(bank_ids)
        or len(bank_ids) != len(set(bank_ids))
        or "codex" not in bank_ids
        or "engineering" not in bank_ids
        or digest(bank_ids) != closeout["pre_delete_bank_set_digest"]
    ):
        raise ReplayError("replay closeout bank authority is invalid")
    return closeout


def apply_replay_closeout(
    adapter: Any,
    replay_plan_value: Mapping[str, Any],
    receipt_values: Sequence[Mapping[str, Any]],
    verification_value: Mapping[str, Any],
    backup_evidence: Mapping[str, Any],
    closeout_plan_value: Mapping[str, Any],
    *,
    approval_digest: str,
) -> Mapping[str, Any]:
    replay_plan = verify_replay_plan(replay_plan_value)
    expected_verification_digest = _verification_digest(
        verification_value,
        replay_plan["plan_digest"],
    )
    if (
        not isinstance(receipt_values, Sequence)
        or isinstance(receipt_values, (str, bytes))
        or len(receipt_values) != len(replay_plan["documents"])
        or digest(
            [
                _verify_receipt(receipt, replay_plan, descriptor)
                for receipt, descriptor in zip(
                    receipt_values,
                    replay_plan["documents"],
                    strict=True,
                )
            ]
        )
        != verification_value.get("receipt_set_digest")
    ):
        raise ReplayError("replay closeout receipts do not match verification")
    backup_digest = _backup_evidence_digest(backup_evidence)
    closeout = _verify_closeout_plan(
        closeout_plan_value,
        replay_plan_digest=replay_plan["plan_digest"],
        replay_verification_digest=expected_verification_digest,
        backup_evidence_digest=backup_digest,
    )
    approval = _sha(
        approval_digest,
        "replay closeout approval digest",
    )
    if not hmac.compare_digest(
        approval.encode("ascii"),
        closeout["closeout_plan_digest"].encode("ascii"),
    ):
        raise ReplayError("replay closeout approval digest does not match")
    pre_generation = closeout["pre_delete_generation"]
    before_bank_ids = closeout["pre_delete_bank_ids"]
    deletion = adapter.conditional_replay_closeout(
        {
            "schema_version": 1,
            "expected_generation": pre_generation,
            "expected_bank_ids": sorted(before_bank_ids),
            "source_documents": [
                {
                    "source_document_id":
                        descriptor["source_document_id"],
                    "record_digest": descriptor["record_digest"],
                }
                for descriptor in replay_plan["documents"]
            ],
            "replay_plan_digest": replay_plan["plan_digest"],
            "verification_digest": expected_verification_digest,
            "backup_evidence_digest": backup_digest,
            "closeout_plan_digest": closeout[
                "closeout_plan_digest"
            ],
        }
    )
    if not isinstance(deletion, Mapping):
        raise ReplayError("replay closeout deletion response is invalid")
    deleted_count = deletion.get("deleted_count")
    post_generation = _identifier(
        deletion.get("post_delete_generation"),
        "post-delete generation",
    )
    after_bank_ids = deletion.get("remaining_bank_ids")
    if (
        type(deleted_count) is not int
        or deleted_count < 0
        or deletion.get("deleted_bank_id") != "codex"
        or deletion.get("pre_delete_generation") != pre_generation
        or post_generation == pre_generation
        or not isinstance(after_bank_ids, list)
        or "codex" in after_bank_ids
        or set(after_bank_ids) != set(before_bank_ids) - {"codex"}
        or deletion.get("cleanup_status") not in {
            "completed",
            "degraded",
            "deferred",
        }
    ):
        raise ReplayError("replay closeout deletion attestation failed")
    _identifier(
        adapter.read_migration_generation(),
        "live post-delete generation",
    )
    live_bank_ids = adapter.list_replay_bank_ids()
    if (
        "codex" in live_bank_ids
        or not set(after_bank_ids).issubset(live_bank_ids)
    ):
        raise ReplayError("replay closeout postcondition drifted")
    body = {
        "schema_version": 1,
        "closeout_plan_digest": closeout["closeout_plan_digest"],
        "deleted_bank_id": "codex",
        "deleted_count": deleted_count,
        "pre_delete_generation": pre_generation,
        "post_delete_generation": post_generation,
        "remaining_bank_set_digest": digest(sorted(after_bank_ids)),
        "cleanup_status": deletion["cleanup_status"],
    }
    return _normalized({**body, "closeout_receipt_digest": digest(body)})
