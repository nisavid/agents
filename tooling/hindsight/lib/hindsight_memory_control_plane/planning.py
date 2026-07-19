"""Construction, closed deserialization, and verification of immutable plans."""

import hmac
import re
from typing import Any, Mapping, Sequence

from .action_contracts import ACTION_SCHEMAS, DESTRUCTIVE_ACTION_KINDS
from .canonical import digest
from .endpoint_host import canonical_endpoint_host, is_bare_endpoint_host
from .inventory import _resolved_artifact
from .model import (
    Action,
    EndpointIdentity,
    Inventory,
    OperationSnapshot,
    Plan,
    deep_freeze,
    deep_thaw,
)


PLAN_KEYS = {
    "schema_version", "inventory_digest", "artifact_digest", "target_profile",
    "target_endpoint", "live_state_digest", "operations", "compatibility",
    "actions", "destructive", "plan_digest",
}
ENDPOINT_KEYS = {"profile_id", "scheme", "host", "port", "tenant"}
BANK_KEYS = {"profile_id", "bank_id"}
OPERATION_KEYS = {"id", "kind", "status", "profile_id", "bank", "endpoint", "artifact_digest"}
OPERATION_KINDS = {"apply", "consolidate", "export", "import", "migration", "reflect", "refresh", "retain"}
ACTIVE_OPERATION_STATUSES = {"pending", "running"}
COMPATIBILITY_KEYS = {"check", "compatible", "reason_code", "profile_id", "provider_id", "model_id", "artifact_digest", "endpoint", "status", "provider_state", "provider_role", "activatable", "blocked_by", "fallback_provider_id", "placement", "revision"}
COMPATIBILITY_STATUSES = {"pass", "fail", "blocked", "unknown", "degraded"}
PROVIDER_COMPATIBILITY_KEYS = {
    "provider_id", "role", "state", "compatible", "activatable",
    "blocked_by", "fallback_provider_id", "placement", "artifact_id",
    "revision",
}
PROVIDER_STATE_TO_STATUS = {
    "current": "pass",
    "fallback": "pass",
    "desired_candidate": "pass",
    "blocked_candidate": "blocked",
    "incompatible": "fail",
}
PROVIDER_ROLES = {"llm", "embedding", "reranking"}
PROVIDER_PLACEMENTS = {"local", "third-party-hosted", "private-remote"}
NORMALIZED_PROVIDER_SEMANTIC_KEYS = {
    "provider_state", "provider_role", "activatable", "blocked_by",
    "placement", "revision", "fallback_provider_id",
}
NORMALIZED_PROVIDER_REQUIRED_KEYS = {
    "check", "provider_id", "model_id", "compatible", "status",
    "provider_state", "provider_role", "activatable", "blocked_by",
    "placement", "revision",
}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class PlanError(ValueError):
    pass


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise PlanError(f"{label} must be a bounded identifier")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise PlanError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _generated_identifier(value: str, label: str) -> str:
    """Keep a readable generated ID while bounding it with a stable suffix."""

    if IDENTIFIER.fullmatch(value):
        return value
    suffix = digest({"label": label, "value": value})[:16]
    prefix = value[: 128 - len(suffix) - 1].rstrip("._:/-")
    return _identifier(f"{prefix}-{suffix}", label)


def _validate_normalized_provider_compatibility(
    result: Mapping[str, Any],
) -> None:
    if not set(result).intersection(NORMALIZED_PROVIDER_SEMANTIC_KEYS):
        return
    missing = NORMALIZED_PROVIDER_REQUIRED_KEYS - set(result)
    if missing:
        raise PlanError(
            "normalized provider compatibility is incomplete "
            f"(missing={sorted(missing)})"
        )
    provider_state = result["provider_state"]
    if provider_state not in PROVIDER_STATE_TO_STATUS:
        raise PlanError("provider compatibility state is unsupported")
    if result["provider_role"] not in PROVIDER_ROLES:
        raise PlanError("provider compatibility role is unsupported")
    if result["placement"] not in PROVIDER_PLACEMENTS:
        raise PlanError("provider compatibility placement is unsupported")
    expected_status = PROVIDER_STATE_TO_STATUS[provider_state]
    compatible = result["compatible"]
    activatable = result["activatable"]
    blocked_by = result["blocked_by"]
    if (
        result["check"]
        != _generated_identifier(
            f"provider:{result['provider_id']}", "compatibility check"
        )
        or compatible != (expected_status == "pass")
        or result["status"] != expected_status
        or activatable != compatible
        or bool(blocked_by) == compatible
    ):
        raise PlanError("provider compatibility state contradicts its gates")


def _exact_mapping(value: Any, keys: set[str], label: str, required: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PlanError(f"{label} must be an object")
    unknown = set(value) - keys
    missing = (required or set()) - set(value)
    if unknown or missing:
        raise PlanError(f"{label} keys are closed (missing={sorted(missing)}, unknown={sorted(unknown)})")
    return dict(value)


def _endpoint(value: Any, profile_id: str | None = None) -> EndpointIdentity:
    record = _exact_mapping(value, ENDPOINT_KEYS, "endpoint", ENDPOINT_KEYS)
    actual_profile = _identifier(record["profile_id"], "endpoint profile_id")
    if profile_id is not None and actual_profile != profile_id:
        raise PlanError("endpoint profile identity does not match target profile")
    scheme = record["scheme"]
    if not isinstance(scheme, str) or scheme not in {"http", "https"}:
        raise PlanError("endpoint scheme must be http or https")
    host = record["host"]
    if not is_bare_endpoint_host(host):
        raise PlanError("endpoint host must be a bare DNS name or IP literal")
    host = canonical_endpoint_host(host)
    if scheme == "http" and host not in {"127.0.0.1", "::1"}:
        raise PlanError("plain HTTP endpoint host must be literal loopback")
    port = record["port"]
    if type(port) is not int or not 1 <= port <= 65535:
        raise PlanError("endpoint port must be an integer from 1 to 65535")
    tenant = _identifier(record["tenant"], "endpoint tenant")
    return EndpointIdentity(actual_profile, scheme, host, port, tenant)


def _bank(value: Any, label: str) -> dict[str, str]:
    record = _exact_mapping(value, BANK_KEYS, label, BANK_KEYS)
    return {
        "profile_id": _identifier(record["profile_id"], f"{label} profile_id"),
        "bank_id": _identifier(record["bank_id"], f"{label} bank_id"),
    }


def _operations(
    value: Any,
    target_profile: str | None = None,
    expected_endpoint: EndpointIdentity | None = None,
) -> OperationSnapshot:
    if isinstance(value, OperationSnapshot):
        value = value.to_dict()
    record = _exact_mapping(value, {"idle", "active"}, "operations snapshot", {"idle", "active"})
    if not isinstance(record["idle"], bool):
        raise PlanError("operations idle must be boolean")
    active = record["active"]
    if not isinstance(active, list):
        raise PlanError("operations active must be an array")
    resolved = []
    operation_ids: set[str] = set()
    for item in active:
        operation = _exact_mapping(item, OPERATION_KEYS, "operations entry", {"id", "kind", "status"})
        normalized: dict[str, Any] = {
            "id": _identifier(operation["id"], "operations id"),
            "kind": operation["kind"],
            "status": operation["status"],
        }
        if normalized["id"] in operation_ids:
            raise PlanError("operations active IDs must be unique")
        operation_ids.add(normalized["id"])
        if (
            not isinstance(normalized["kind"], str)
            or normalized["kind"] not in OPERATION_KINDS
        ):
            raise PlanError("operations kind is not a supported enum")
        if (
            not isinstance(normalized["status"], str)
            or normalized["status"] not in ACTIVE_OPERATION_STATUSES
        ):
            raise PlanError("active operations status must be pending or running")
        if "profile_id" in operation:
            normalized["profile_id"] = _identifier(operation["profile_id"], "operations profile_id")
            if target_profile is not None and normalized["profile_id"] != target_profile:
                raise PlanError("operations profile identity does not match target profile")
        if "bank" in operation:
            normalized["bank"] = _bank(operation["bank"], "operations bank")
            if target_profile is not None and normalized["bank"]["profile_id"] != target_profile:
                raise PlanError("operations bank profile identity does not match target profile")
        if "endpoint" in operation:
            operation_endpoint = _endpoint(
                operation["endpoint"], target_profile
            )
            if expected_endpoint is not None and operation_endpoint != expected_endpoint:
                raise PlanError("operations endpoint identity does not match target endpoint")
            normalized["endpoint"] = operation_endpoint.to_dict()
        if "artifact_digest" in operation:
            normalized["artifact_digest"] = _digest(operation["artifact_digest"], "operations artifact_digest")
        resolved.append(normalized)
    if record["idle"] != (len(resolved) == 0):
        raise PlanError("operations idle state disagrees with active operations")
    resolved.sort(key=lambda operation: operation["id"])
    return OperationSnapshot(record["idle"], tuple(resolved))


def _compatibility(
    value: Any, target_profile: str | None = None
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        raise PlanError("compatibility must be an array")
    resolved = []
    for item in value:
        if isinstance(item, dict) and set(item) == PROVIDER_COMPATIBILITY_KEYS:
            provider_id = _identifier(
                item["provider_id"], "compatibility provider_id"
            )
            provider_state = item["state"]
            if (
                not isinstance(provider_state, str)
                or provider_state not in PROVIDER_STATE_TO_STATUS
            ):
                raise PlanError("provider compatibility state is unsupported")
            compatible = item["compatible"]
            activatable = item["activatable"]
            blocked_by = item["blocked_by"]
            if (
                type(compatible) is not bool
                or type(activatable) is not bool
                or not isinstance(blocked_by, list)
                or any(not isinstance(reason, str) for reason in blocked_by)
                or len(blocked_by) != len(set(blocked_by))
            ):
                raise PlanError("provider compatibility result is invalid")
            if (
                not isinstance(item["role"], str)
                or item["role"] not in PROVIDER_ROLES
            ):
                raise PlanError("provider compatibility role is unsupported")
            if (
                not isinstance(item["placement"], str)
                or item["placement"] not in PROVIDER_PLACEMENTS
            ):
                raise PlanError("provider compatibility placement is unsupported")
            status = PROVIDER_STATE_TO_STATUS[provider_state]
            if (
                compatible != (status == "pass")
                or activatable != compatible
                or bool(blocked_by) == compatible
            ):
                raise PlanError(
                    "provider compatibility state contradicts its gates"
                )
            fallback = item["fallback_provider_id"]
            normalized_provider = {
                "check": _generated_identifier(
                    f"provider:{provider_id}", "compatibility check"
                ),
                "provider_id": provider_id,
                "model_id": _identifier(
                    item["artifact_id"], "compatibility model_id"
                ),
                "compatible": compatible,
                "status": status,
                "provider_state": provider_state,
                "provider_role": _identifier(
                    item["role"], "compatibility provider role"
                ),
                "activatable": activatable,
                "blocked_by": tuple(sorted(
                    _identifier(reason, "compatibility blocked reason")
                    for reason in blocked_by
                )),
                "placement": _identifier(
                    item["placement"], "compatibility placement"
                ),
                "revision": _identifier(
                    item["revision"], "compatibility revision"
                ),
            }
            if fallback is not None:
                normalized_provider["fallback_provider_id"] = _identifier(
                    fallback, "compatibility fallback provider"
                )
            resolved.append(normalized_provider)
            continue
        result = _exact_mapping(item, COMPATIBILITY_KEYS, "compatibility result", {"check", "compatible"})
        if not isinstance(result["compatible"], bool):
            raise PlanError("compatibility compatible must be boolean")
        normalized: dict[str, Any] = {
            "check": _identifier(result["check"], "compatibility check"),
            "compatible": result["compatible"],
        }
        for key in ("reason_code", "profile_id", "provider_id", "model_id"):
            if key in result:
                normalized[key] = _identifier(result[key], f"compatibility {key}")
        if (
            target_profile is not None
            and normalized.get("profile_id", target_profile) != target_profile
        ):
            raise PlanError("compatibility profile identity does not match target profile")
        if "artifact_digest" in result:
            normalized["artifact_digest"] = _digest(result["artifact_digest"], "compatibility artifact_digest")
        if "endpoint" in result:
            normalized["endpoint"] = _endpoint(
                result["endpoint"], target_profile
            ).to_dict()
        if "status" in result:
            if (
                not isinstance(result["status"], str)
                or result["status"] not in COMPATIBILITY_STATUSES
            ):
                raise PlanError("compatibility status is not a supported enum")
            if (
                result["compatible"]
                and result["status"] not in {"pass", "degraded"}
            ) or (
                not result["compatible"]
                and result["status"] not in {"fail", "blocked", "unknown"}
            ):
                raise PlanError(
                    "compatibility status contradicts compatible state"
                )
            normalized["status"] = result["status"]
        for key in ("provider_state", "provider_role", "placement", "revision"):
            if key in result:
                normalized[key] = _identifier(
                    result[key], f"compatibility {key}"
                )
        if "activatable" in result:
            if type(result["activatable"]) is not bool:
                raise PlanError("compatibility activatable must be boolean")
            normalized["activatable"] = result["activatable"]
        if "blocked_by" in result:
            blocked = result["blocked_by"]
            if (
                not isinstance(blocked, (list, tuple))
                or any(not isinstance(reason, str) for reason in blocked)
                or len(blocked) != len(set(blocked))
            ):
                raise PlanError("compatibility blocked_by must be unique identifiers")
            normalized["blocked_by"] = tuple(sorted(
                _identifier(reason, "compatibility blocked reason")
                for reason in blocked
            ))
        if "fallback_provider_id" in result:
            normalized["fallback_provider_id"] = _identifier(
                result["fallback_provider_id"],
                "compatibility fallback provider",
            )
        _validate_normalized_provider_compatibility(normalized)
        resolved.append(normalized)
    checks = [item["check"] for item in resolved]
    if len(checks) != len(set(checks)):
        raise PlanError("compatibility checks must be unique after normalization")
    return tuple(sorted(resolved, key=lambda item: item["check"]))


def _validate_compatibility_endpoints(
    compatibility: Sequence[Mapping[str, Any]],
    expected_endpoint: EndpointIdentity,
) -> None:
    for result in compatibility:
        if "endpoint" in result and _endpoint(
            result["endpoint"], expected_endpoint.profile_id
        ) != expected_endpoint:
            raise PlanError(
                "compatibility endpoint identity does not match target endpoint"
            )


def _validate_compatibility_fallbacks(
    compatibility: Sequence[Mapping[str, Any]],
) -> None:
    providers = {
        result["provider_id"]: result
        for result in compatibility
        if "provider_id" in result and "provider_role" in result
    }
    fallbacks: dict[str, str] = {}
    for provider_id, result in providers.items():
        fallback_id = result.get("fallback_provider_id")
        if fallback_id is None:
            continue
        if fallback_id == provider_id:
            raise PlanError("provider fallback cannot reference itself")
        fallback = providers.get(fallback_id)
        if fallback is None:
            raise PlanError("provider fallback target must exist")
        if result["provider_role"] != "reranking":
            raise PlanError("only reranking providers may declare fallbacks")
        if fallback["provider_role"] != "reranking":
            raise PlanError("provider fallback target must support reranking")
        if fallback["provider_state"] != "fallback":
            raise PlanError("provider fallback target must be in fallback state")
        fallbacks[provider_id] = fallback_id

    for provider_id in fallbacks:
        seen: set[str] = set()
        current = provider_id
        while current in fallbacks:
            if current in seen:
                raise PlanError("provider fallback graph contains a cycle")
            seen.add(current)
            current = fallbacks[current]


def _actions(
    value: Any, target_profile: str | None = None
) -> tuple[Action, ...]:
    if not isinstance(value, list):
        raise PlanError("actions must be an array")
    result: list[Action] = []
    ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise PlanError("action must be an object")
        identifier = _identifier(item.get("id"), "action id")
        kind = item.get("kind")
        if isinstance(kind, str) and kind in DESTRUCTIVE_ACTION_KINDS:
            raise PlanError(f"ordinary plan contains destructive action kind: {kind}")
        if not isinstance(kind, str) or kind not in ACTION_SCHEMAS:
            raise PlanError(f"action kind is not supported: {kind}")
        if identifier in ids:
            raise PlanError(f"duplicate action id: {identifier}")
        ids.add(identifier)
        fields = ACTION_SCHEMAS[kind]
        details = {key: value for key, value in item.items() if key not in {"id", "kind"}}
        _exact_mapping(details, fields, "action details", fields)
        normalized: dict[str, Any] = {}
        for key, value in details.items():
            if key == "bank":
                normalized[key] = _bank(value, "action bank")
                if target_profile is not None and normalized[key]["profile_id"] != target_profile:
                    raise PlanError("action bank profile identity does not match target profile")
            elif key.endswith("_digest"):
                normalized[key] = _digest(value, f"action {key}")
            elif key == "enabled":
                if not isinstance(value, bool):
                    raise PlanError("action enabled must be boolean")
                normalized[key] = value
            else:
                normalized[key] = _identifier(value, f"action {key}")
        if (
            target_profile is not None
            and normalized.get("profile_id", target_profile) != target_profile
        ):
            raise PlanError("action profile identity does not match target profile")
        result.append(Action(identifier, kind, normalized))
    return tuple(result)


def _observed_banks(
    state: Mapping[str, Any],
    target_profile: str,
    expected_endpoint: EndpointIdentity | None = None,
) -> dict[str, Mapping[str, Any]]:
    values = state.get("banks", [])
    if not isinstance(values, list):
        raise PlanError("live state banks must be an array")
    result: dict[str, Mapping[str, Any]] = {}
    for value in values:
        if not isinstance(value, Mapping):
            raise PlanError("live state bank must be an object")
        if (
            "profile_id" in value
            and "profile" in value
            and value["profile_id"] != value["profile"]
        ):
            raise PlanError("live state bank has conflicting profile identities")
        profile_id = value.get("profile_id", value.get("profile", target_profile))
        if profile_id != target_profile:
            raise PlanError("live state bank profile identity does not match target profile")
        if "endpoint" in value:
            if (
                expected_endpoint is not None
                and _endpoint(value["endpoint"], target_profile)
                != expected_endpoint
            ):
                raise PlanError("live state bank endpoint does not match target endpoint")
            _endpoint(value["endpoint"], target_profile)
        if "bank" in value:
            embedded_bank = _bank(value["bank"], "live state embedded bank")
            if embedded_bank["profile_id"] != target_profile:
                raise PlanError(
                    "live state embedded bank profile identity does not match target profile"
                )
        if (
            "bank_id" in value
            and "id" in value
            and value["bank_id"] != value["id"]
        ):
            raise PlanError("live state bank has conflicting bank identities")
        bank_id = _identifier(value.get("bank_id", value.get("id")), "live state bank id")
        if "bank" in value and embedded_bank["bank_id"] != bank_id:
            raise PlanError(
                "live state embedded bank identity does not match enclosing bank"
            )
        if bank_id in result:
            raise PlanError("live state bank identities must be unique")
        result[bank_id] = value
    return result


def _desired_collection(value: Any, label: str) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise PlanError(f"desired bank {label} must be an array")
    result: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise PlanError(f"desired bank {label} entry must be an object")
        identifier = _identifier(item.get("id"), f"desired bank {label} id")
        if identifier in seen:
            raise PlanError(f"desired bank {label} identities must be unique")
        seen.add(identifier)
        result.append(item)
    return sorted(result, key=lambda item: item["id"])


def _observed_collection(
    bank: Mapping[str, Any] | None,
    label: str,
) -> dict[str, Mapping[str, Any]]:
    values = [] if bank is None else bank.get(label, [])
    if not isinstance(values, list):
        raise PlanError(f"live state bank {label} must be an array")
    result: dict[str, Mapping[str, Any]] = {}
    for item in values:
        if not isinstance(item, Mapping):
            raise PlanError(f"live state bank {label} entry must be an object")
        identifier = _identifier(item.get("id"), f"live state bank {label} id")
        if identifier in result:
            raise PlanError(f"live state bank {label} identities must be unique")
        result[identifier] = item
    return result


def _derive_actions(
    inventory: Inventory,
    target_profile: str,
    expected_endpoint: EndpointIdentity,
    state: Mapping[str, Any],
    resolved_artifact: Mapping[str, Any],
) -> tuple[Action, ...]:
    if not isinstance(state, Mapping):
        raise PlanError("live state must contain an object state")
    observed = _observed_banks(state, target_profile, expected_endpoint)
    desired = sorted(
        (
            bank
            for bank in resolved_artifact["banks"]
            if bank["profile_id"] == target_profile
        ),
        key=lambda bank: bank["id"],
    )
    proposed: list[dict[str, Any]] = []

    profile_artifact = state.get("profile_artifact_digest")
    profile = next(
        item for item in resolved_artifact["profiles"]
        if item["id"] == target_profile
    )
    expected = digest(profile)
    if profile_artifact != expected:
        proposed.append(
            {
                "kind": "configure_profile",
                "profile_id": target_profile,
                "artifact_digest": expected,
                "_label": f"configure-profile-{target_profile}",
            }
        )

    desired_ids: set[str] = set()
    for bank in desired:
        bank_id = _identifier(bank["id"], "desired bank id")
        desired_ids.add(bank_id)
        bank_ref = {"profile_id": target_profile, "bank_id": bank_id}
        actual = observed.get(bank_id)
        bank_artifact = digest(bank)
        if actual is None:
            proposed.append(
                {
                    "kind": "create_bank",
                    "bank": bank_ref,
                    "_label": f"create-bank-{bank_id}",
                }
            )
        if actual is None or actual.get("artifact_digest") != bank_artifact:
            proposed.append(
                {
                    "kind": "configure_bank",
                    "bank": bank_ref,
                    "artifact_digest": bank_artifact,
                    "_label": f"configure-bank-{bank_id}",
                }
            )

        for desired_key, actual_key, kind in (
            ("enable_auto_consolidation", "enable_auto_consolidation", "set_auto_consolidation"),
        ):
            enabled = bank[desired_key]
            if not isinstance(enabled, bool):
                raise PlanError(f"desired bank {desired_key} must be boolean")
            if actual is None or actual.get(actual_key) is not enabled:
                proposed.append(
                    {
                        "kind": kind,
                        "bank": bank_ref,
                        "enabled": enabled,
                        "_label": f"{kind.replace('_', '-')}-{bank_id}",
                    }
                )

        memory_defense = bank.get("memory_defense", "sensitive_data")
        if memory_defense != "sensitive_data":
            raise PlanError(
                "desired bank memory_defense must be sensitive_data"
            )
        if actual is None or actual.get("memory_defense") != memory_defense:
            proposed.append(
                {
                    "kind": "set_memory_defense",
                    "bank": bank_ref,
                    "enabled": True,
                    "_label": f"set-memory-defense-{bank_id}",
                }
            )

        observed_models = _observed_collection(actual, "models")
        desired_models = _desired_collection(bank.get("models"), "models")
        desired_model_ids = {model["id"] for model in desired_models}
        for model in desired_models:
            model_id = model["id"]
            revision = _identifier(model.get("revision"), "desired model revision")
            artifact = digest(model)
            current = observed_models.get(model_id)
            if current is None or current.get("revision") != revision or current.get("artifact_digest") != artifact:
                proposed.append(
                    {
                        "kind": "upsert_model",
                        "bank": bank_ref,
                        "model_id": model_id,
                        "revision": revision,
                        "artifact_digest": artifact,
                        "_label": f"upsert-model-{bank_id}-{model_id}",
                    }
                )

        for model_id in sorted(set(observed_models) - desired_model_ids):
            proposed.append(
                {
                    "kind": "report_unmanaged",
                    "profile_id": target_profile,
                    "reason_code": _generated_identifier(
                        f"unmanaged-model-{bank_id}-{model_id}",
                        "unmanaged model reason code",
                    ),
                    "_label": f"report-unmanaged-model-{bank_id}-{model_id}",
                }
            )

        observed_directives = _observed_collection(actual, "directives")
        desired_directives = _desired_collection(
            bank.get("directives"), "directives"
        )
        desired_directive_ids = {
            directive["id"] for directive in desired_directives
        }
        for directive in desired_directives:
            directive_id = directive["id"]
            artifact = digest(directive)
            current = observed_directives.get(directive_id)
            if current is None or current.get("artifact_digest") != artifact:
                proposed.append(
                    {
                        "kind": "upsert_directive",
                        "bank": bank_ref,
                        "directive_id": directive_id,
                        "artifact_digest": artifact,
                        "_label": f"upsert-directive-{bank_id}-{directive_id}",
                    }
                )

        for directive_id in sorted(
            set(observed_directives) - desired_directive_ids
        ):
            proposed.append(
                {
                    "kind": "report_unmanaged",
                    "profile_id": target_profile,
                    "reason_code": _generated_identifier(
                        f"unmanaged-directive-{bank_id}-{directive_id}",
                        "unmanaged directive reason code",
                    ),
                    "_label": (
                        f"report-unmanaged-directive-{bank_id}-{directive_id}"
                    ),
                }
            )

    for bank_id in sorted(set(observed) - desired_ids):
        reason_code = _generated_identifier(
            f"unmanaged-bank-{bank_id}",
            "unmanaged bank reason code",
        )
        proposed.append(
            {
                "kind": "report_unmanaged",
                "profile_id": target_profile,
                "reason_code": reason_code,
                "_label": f"report-unmanaged-bank-{bank_id}",
            }
        )

    records = []
    for index, proposal in enumerate(proposed, 1):
        label = proposal.pop("_label")
        identifier = _generated_identifier(
            f"{index:02d}-{label}", "generated action id"
        )
        records.append({"id": identifier, **proposal})
    return _actions(records, target_profile)


def inventory_endpoint(inventory: Inventory, profile_id: str) -> EndpointIdentity:
    profile = next((item for item in inventory.profiles if item["id"] == profile_id), None)
    if profile is None:
        raise PlanError("target profile is not declared")
    if profile.get("enabled", True) is not True:
        raise PlanError("target profile is disabled")
    base_port = inventory.machine.get("base_port", 7979)
    port = profile.get("port")
    if port is None:
        port = base_port + profile["slot"]
    host = profile.get("host", "127.0.0.1")
    if is_bare_endpoint_host(host):
        host = canonical_endpoint_host(host)
    return EndpointIdentity(
        profile_id=profile_id,
        scheme=profile.get("scheme", "http"),
        host=host,
        port=port,
        tenant=profile.get("tenant", "default"),
    )


def build_plan(inventory: Inventory, live_state: Mapping[str, Any], operations: Any) -> Plan:
    if not isinstance(inventory, Inventory):
        raise PlanError("validated inventory is required")
    raw_inventory = inventory.body()
    if not hmac.compare_digest(
        digest(raw_inventory), inventory.inventory_digest
    ):
        raise PlanError("inventory digest is not canonical")
    base_port = raw_inventory["machine"].get("base_port", 7979)
    engineering_enabled = raw_inventory["machine"].get(
        "engineering_memory_enabled", False
    )
    canonical_artifact = _resolved_artifact(
        raw_inventory,
        base_port=base_port,
        machine_engineering_enabled=engineering_enabled,
    )
    if not hmac.compare_digest(
        digest(canonical_artifact), inventory.artifact_digest
    ):
        raise PlanError("inventory artifact digest is not canonical")
    if not isinstance(live_state, Mapping):
        raise PlanError("live state must be an object")
    if "actions" in live_state:
        raise PlanError("live state cannot supply proposed actions")
    supplied_profiles = [
        _identifier(live_state[key], f"live state {key}")
        for key in ("profile_id", "target_profile")
        if key in live_state
    ]
    if not supplied_profiles:
        raise PlanError("target profile must be a bounded identifier")
    if len(set(supplied_profiles)) != 1:
        raise PlanError("live state profile identities do not match")
    target_profile = supplied_profiles[0]
    expected_endpoint = inventory_endpoint(inventory, target_profile)
    supplied_endpoints = [
        _endpoint(live_state[key], target_profile)
        for key in ("endpoint", "target_endpoint")
        if key in live_state
    ]
    if not supplied_endpoints:
        raise PlanError("endpoint must be an object")
    if any(endpoint != expected_endpoint for endpoint in supplied_endpoints):
        raise PlanError("live endpoint identity does not match inventory")
    operation_snapshot = _operations(
        operations, target_profile, expected_endpoint
    )
    compatibility = _compatibility(
        live_state.get("compatibility", []), target_profile
    )
    _validate_compatibility_endpoints(compatibility, expected_endpoint)
    if "state" in live_state and "live_state" in live_state:
        if live_state["state"] != live_state["live_state"]:
            raise PlanError("live state has conflicting state aliases")
        state = live_state["state"]
    elif "state" in live_state:
        state = live_state["state"]
    elif "live_state" in live_state:
        state = live_state["live_state"]
    else:
        raise PlanError("live state must explicitly include state")
    if not isinstance(state, Mapping):
        raise PlanError("live state state must be an object")
    try:
        state = deep_thaw(deep_freeze(state))
    except TypeError as error:
        raise PlanError("live state must contain canonical JSON values") from error
    actions = _derive_actions(
        inventory,
        target_profile,
        expected_endpoint,
        state,
        canonical_artifact,
    )
    body = {
        "schema_version": 1,
        "inventory_digest": inventory.inventory_digest,
        "artifact_digest": inventory.artifact_digest,
        "target_profile": target_profile,
        "target_endpoint": expected_endpoint.to_dict(),
        "live_state_digest": digest(state),
        "operations": operation_snapshot.to_dict(),
        "compatibility": [dict(value) for value in compatibility],
        "actions": [action.to_dict() for action in actions],
        "destructive": False,
    }
    plan = Plan(
        schema_version=1,
        inventory_digest=inventory.inventory_digest,
        artifact_digest=inventory.artifact_digest,
        target_profile=target_profile,
        target_endpoint=expected_endpoint,
        live_state_digest=body["live_state_digest"],
        operations=operation_snapshot,
        compatibility=compatibility,
        actions=actions,
        destructive=False,
        plan_digest=digest(body),
    )
    verify_plan(plan)
    return plan


def plan_from_dict(value: Any) -> Plan:
    record = _exact_mapping(value, PLAN_KEYS, "plan", PLAN_KEYS)
    if type(record["schema_version"]) is not int or record["schema_version"] != 1:
        raise PlanError("plan schema_version must be integer 1")
    if record["destructive"] is not False:
        raise PlanError("ordinary plan destructive must be false")
    target_profile = _identifier(record["target_profile"], "target profile")
    compatibility = _compatibility(record["compatibility"], target_profile)
    _validate_compatibility_fallbacks(compatibility)
    plan = Plan(
        schema_version=1,
        inventory_digest=_digest(record["inventory_digest"], "inventory_digest"),
        artifact_digest=_digest(record["artifact_digest"], "artifact_digest"),
        target_profile=target_profile,
        target_endpoint=_endpoint(record["target_endpoint"], target_profile),
        live_state_digest=_digest(record["live_state_digest"], "live_state_digest"),
        operations=_operations(
            record["operations"],
            target_profile,
            _endpoint(record["target_endpoint"], target_profile),
        ),
        compatibility=compatibility,
        actions=_actions(record["actions"], target_profile),
        destructive=False,
        plan_digest=_digest(record["plan_digest"], "plan_digest"),
    )
    verify_plan(plan)
    return plan


def verify_plan(plan: Plan) -> None:
    if type(plan.schema_version) is not int or plan.schema_version != 1:
        raise PlanError("plan schema_version must be integer 1")
    _digest(plan.inventory_digest, "inventory_digest")
    _digest(plan.artifact_digest, "artifact_digest")
    _identifier(plan.target_profile, "target profile")
    endpoint = _endpoint(plan.target_endpoint.to_dict(), plan.target_profile)
    if endpoint != plan.target_endpoint:
        raise PlanError("plan endpoint is not canonical")
    _digest(plan.live_state_digest, "live_state_digest")
    operations = _operations(
        plan.operations, plan.target_profile, plan.target_endpoint
    )
    if operations != plan.operations:
        raise PlanError("plan operations are not canonical")
    compatibility = _compatibility(
        [dict(value) for value in plan.compatibility], plan.target_profile
    )
    _validate_compatibility_fallbacks(compatibility)
    _validate_compatibility_endpoints(compatibility, plan.target_endpoint)
    if compatibility != plan.compatibility:
        raise PlanError("plan compatibility is not canonical")
    actions = _actions(
        [action.to_dict() for action in plan.actions], plan.target_profile
    )
    if actions != plan.actions:
        raise PlanError("plan actions are not canonical")
    if plan.destructive is not False:
        raise PlanError("ordinary plan destructive must be false")
    _digest(plan.plan_digest, "plan_digest")
    expected = digest(plan.body())
    if not hmac.compare_digest(expected, plan.plan_digest):
        raise PlanError("plan digest does not match plan body")
