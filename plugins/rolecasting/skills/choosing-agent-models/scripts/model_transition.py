"""Authorize one model transition before a payload-bearing dispatch.

The guard is pure: it does not inspect a catalog, select an account, launch a
worker, or authenticate external evidence.  Its caller supplies current,
content-addressed route evidence and retains the accepted predecessor state.
Every actuator must validate the returned decision immediately before it
accepts the bound payload.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

DECISION_CONTRACT = "rolecasting-model-transition-decision-v1"
STATE_CONTRACT = "rolecasting-model-transition-state-v1"
ROUTE_EVIDENCE_CONTRACT = "rolecasting-route-evidence-v1"
SCHEMA_VERSION = 1

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_INITIAL_EVENTS = {"new-task", "new-subagent"}
_CONTINUATION_EVENTS = {
    "follow-up",
    "resume",
    "retry",
    "capacity-recovery",
    "reclassification",
}
_EVENTS = _INITIAL_EVENTS | _CONTINUATION_EVENTS
_OPERATOR_ACTIONS = {"preserve", "select", "replace"}
_CLASSIFICATIONS = ("clerical", "recoverable", "consequential", "security")
_CLASSIFICATION_RANK = {
    name: rank for rank, name in enumerate(_CLASSIFICATIONS)
}
_ROLE_CLASSIFICATION_CEILING = {
    "luna": "clerical",
    "terra": "recoverable",
    "sol": "consequential",
    "daybreak": "security",
}
_ROLES = {"luna", "terra", "sol", "daybreak", "inherited-fixed", "other"}
_PROVENANCE = {"policy", "fallback", "operator", "inherited-fixed"}
_CAPACITY = {"available", "exhausted", "unknown"}
_CAPABILITY_STATUS = {"available", "absent", "probe-failed", "unknown"}
_INVENTORY_STATUS = {"fresh", "missing", "stale", "denied"}
_STATUS_SURFACE_SAFETY = {"side-effect-safe", "unverified", "unsafe"}
_STATE_CHANGE_STATUS = {"unchanged", "unknown", "changed"}
_SEMANTIC_PRERELEASE_IDENTIFIER = (
    r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
)
_CANONICAL_SEMANTIC_VERSION = re.compile(
    r"(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)"
    rf"(?:-{_SEMANTIC_PRERELEASE_IDENTIFIER}"
    rf"(?:\.{_SEMANTIC_PRERELEASE_IDENTIFIER})*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)
_MAX_STATUS_SURFACE_VERSION_LENGTH = 128
_KNOWN_UNSAFE_STATUS_SURFACE_CORES = {
    ("codex-app-server", ("0", "149", "0"))
}
_FAILURE_DISPOSITIONS = {"defer", "cross-harness", "tracker", "blocked"}
_UTC_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")


class ModelTransitionError(ValueError):
    """A transition document is malformed or an authorization was not accepted."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError) as error:
        raise ModelTransitionError(
            "model-transition input is not canonical-JSON compatible"
        ) from error


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _document(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "content_sha256": _digest(value)}


def _exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ModelTransitionError(f"{label} schema drift")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ModelTransitionError(f"{label} must be a non-empty string")
    return value


def _token(value: Any, label: str) -> str:
    value = _text(value, label)
    if _TOKEN.fullmatch(value) is None:
        raise ModelTransitionError(f"{label} is not a closed token")
    return value


def _sha(value: Any, label: str) -> str:
    value = _text(value, label)
    if _HEX.fullmatch(value) is None:
        raise ModelTransitionError(f"{label} must be a SHA-256 digest")
    return value


def _nullable_sha(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _sha(value, label)


def _strict_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise ModelTransitionError(f"{label} must be a strict Boolean")
    return value


def _classification(value: Any, label: str) -> str:
    value = _token(value, label)
    if value not in _CLASSIFICATION_RANK:
        raise ModelTransitionError(f"{label} is invalid")
    return value


def _target(value: Any, label: str) -> dict[str, Any]:
    value = _exact(
        value,
        {"product_family", "surface", "version", "executor"},
        label,
    )
    _token(value["product_family"], f"{label}.product_family")
    _token(value["surface"], f"{label}.surface")
    _text(value["version"], f"{label}.version")
    _text(value["executor"], f"{label}.executor")
    return value


def _issuer(value: Any, label: str) -> dict[str, Any]:
    value = _exact(
        value,
        {"issuer_id", "contract", "implementation_sha256"},
        label,
    )
    _token(value["issuer_id"], f"{label}.issuer_id")
    _token(value["contract"], f"{label}.contract")
    _sha(value["implementation_sha256"], f"{label}.implementation_sha256")
    return value


def _selection(value: Any, label: str) -> dict[str, Any]:
    value = _exact(
        value,
        {
            "role",
            "model",
            "reasoning_effort",
            "qualified_classification",
            "provenance",
            "operator_selection_sha256",
        },
        label,
    )
    role = _token(value["role"], f"{label}.role")
    if role not in _ROLES:
        raise ModelTransitionError(f"{label}.role is invalid")
    provenance = _token(value["provenance"], f"{label}.provenance")
    if provenance not in _PROVENANCE:
        raise ModelTransitionError(f"{label}.provenance is invalid")
    _classification(
        value["qualified_classification"],
        f"{label}.qualified_classification",
    )
    operator_sha = _nullable_sha(
        value["operator_selection_sha256"],
        f"{label}.operator_selection_sha256",
    )
    if role == "inherited-fixed":
        if (
            value["model"] is not None
            or value["reasoning_effort"] is not None
            or provenance != "inherited-fixed"
            or operator_sha is not None
        ):
            raise ModelTransitionError(
                f"{label} inherited fixed selection schema drift"
            )
    else:
        _text(value["model"], f"{label}.model")
        _text(value["reasoning_effort"], f"{label}.reasoning_effort")
        if provenance == "inherited-fixed":
            raise ModelTransitionError(
                f"{label} explicit selection cannot be inherited fixed"
            )
    if (provenance == "operator") != (operator_sha is not None):
        raise ModelTransitionError(f"{label} operator provenance is unbound")
    return value


def _operator_selection(value: Any, label: str) -> dict[str, Any] | None:
    if value is None:
        return None
    value = _exact(
        value,
        {
            "role",
            "model",
            "reasoning_effort",
            "qualified_classification",
            "selection_sha256",
        },
        label,
    )
    role = _token(value["role"], f"{label}.role")
    if role not in _ROLES - {"inherited-fixed"}:
        raise ModelTransitionError(f"{label}.role is invalid")
    _text(value["model"], f"{label}.model")
    _text(value["reasoning_effort"], f"{label}.reasoning_effort")
    _classification(
        value["qualified_classification"],
        f"{label}.qualified_classification",
    )
    unsigned = {key: item for key, item in value.items() if key != "selection_sha256"}
    if _sha(value["selection_sha256"], f"{label}.selection_sha256") != _digest(
        unsigned
    ):
        raise ModelTransitionError(f"{label} content digest mismatch")
    return value


def _scope(value: Any, label: str) -> dict[str, Any]:
    value = _exact(
        value,
        {
            "classification",
            "roles",
            "daybreak_required",
            "reclassification_sha256",
        },
        label,
    )
    classification = _classification(value["classification"], f"{label}.classification")
    if not isinstance(value["roles"], list) or not value["roles"]:
        raise ModelTransitionError(f"{label}.roles must be non-empty")
    seen: set[str] = set()
    role_classifications: list[str] = []
    for index, role in enumerate(value["roles"]):
        role = _exact(role, {"name", "classification"}, f"{label}.roles[{index}]")
        name = _token(role["name"], f"{label}.roles[{index}].name")
        if name in seen:
            raise ModelTransitionError(f"{label}.roles contains a duplicate")
        seen.add(name)
        role_classifications.append(
            _classification(
                role["classification"],
                f"{label}.roles[{index}].classification",
            )
        )
    hardest = max(role_classifications, key=_CLASSIFICATION_RANK.__getitem__)
    if classification != hardest:
        raise ModelTransitionError(
            f"{label}.classification does not match the hardest role"
        )
    daybreak_required = _strict_bool(
        value["daybreak_required"], f"{label}.daybreak_required"
    )
    if classification == "security" and not daybreak_required:
        raise ModelTransitionError(f"{label} security work requires Daybreak")
    _nullable_sha(
        value["reclassification_sha256"],
        f"{label}.reclassification_sha256",
    )
    return value


def _event(value: Any) -> dict[str, Any]:
    value = _exact(
        value,
        {
            "kind",
            "task_sha256",
            "payload_sha256",
            "plan_binding_sha256",
            "actuation_id",
            "predecessor_authorization_sha256",
            "operator_action",
        },
        "model-transition event",
    )
    kind = _token(value["kind"], "model-transition event.kind")
    if kind not in _EVENTS:
        raise ModelTransitionError("model-transition event.kind is invalid")
    _sha(value["task_sha256"], "model-transition event.task_sha256")
    _sha(value["payload_sha256"], "model-transition event.payload_sha256")
    _sha(
        value["plan_binding_sha256"],
        "model-transition event.plan_binding_sha256",
    )
    _token(value["actuation_id"], "model-transition event.actuation_id")
    _nullable_sha(
        value["predecessor_authorization_sha256"],
        "model-transition event.predecessor_authorization_sha256",
    )
    action = _token(value["operator_action"], "model-transition event.operator_action")
    if action not in _OPERATOR_ACTIONS:
        raise ModelTransitionError("model-transition event.operator_action is invalid")
    return value


def _status_surface(value: Any) -> dict[str, Any]:
    value = _exact(
        value,
        {"implementation", "version", "operations", "safety", "evidence_sha256"},
        "model-transition status surface",
    )
    _token(value["implementation"], "model-transition status surface.implementation")
    _text(value["version"], "model-transition status surface.version")
    operations = value["operations"]
    if not isinstance(operations, list) or not operations:
        raise ModelTransitionError(
            "model-transition status surface.operations must be non-empty"
        )
    seen: set[str] = set()
    for index, operation in enumerate(operations):
        operation = _text(
            operation,
            f"model-transition status surface.operations[{index}]",
        )
        if operation in seen:
            raise ModelTransitionError(
                "model-transition status surface.operations contains a duplicate"
            )
        seen.add(operation)
    safety = _token(value["safety"], "model-transition status surface.safety")
    if safety not in _STATUS_SURFACE_SAFETY:
        raise ModelTransitionError("model-transition status surface.safety is invalid")
    _sha(
        value["evidence_sha256"],
        "model-transition status surface.evidence_sha256",
    )
    return value


def _status_surface_is_unsafe_or_unversioned(value: dict[str, Any]) -> bool:
    implementation = value["implementation"]
    if implementation != "codex-app-server":
        return False
    version = value["version"]
    if len(version) > _MAX_STATUS_SURFACE_VERSION_LENGTH:
        return True
    match = _CANONICAL_SEMANTIC_VERSION.fullmatch(version)
    if match is None:
        return True
    core = tuple(match.group(name) for name in ("major", "minor", "patch"))
    return (implementation, core) in _KNOWN_UNSAFE_STATUS_SURFACE_CORES


def _preflight(value: Any) -> dict[str, Any]:
    value = _exact(
        value,
        {
            "inventory_complete",
            "inventory_sha256",
            "inventory_status",
            "selected_route_in_inventory",
            "status_authorization_sha256",
            "status_surface",
            "status_observed_at",
            "status_evidence_sha256",
            "task_data_shared",
            "caller_state_mutation_requested",
            "state_change_status",
        },
        "model-transition route preflight",
    )
    for field in (
        "inventory_complete",
        "selected_route_in_inventory",
        "task_data_shared",
        "caller_state_mutation_requested",
    ):
        _strict_bool(value[field], f"model-transition route preflight.{field}")
    for field in (
        "inventory_sha256",
        "status_authorization_sha256",
        "status_evidence_sha256",
    ):
        _sha(value[field], f"model-transition route preflight.{field}")
    _status_surface(value["status_surface"])
    status = _token(
        value["inventory_status"],
        "model-transition route preflight.inventory_status",
    )
    if status not in _INVENTORY_STATUS:
        raise ModelTransitionError(
            "model-transition route preflight.inventory_status is invalid"
        )
    observed_at = _text(
        value["status_observed_at"],
        "model-transition route preflight.status_observed_at",
    )
    if _UTC_TIMESTAMP.fullmatch(observed_at) is None:
        raise ModelTransitionError(
            "model-transition route preflight.status_observed_at is invalid"
        )
    state_change_status = _token(
        value["state_change_status"],
        "model-transition route preflight.state_change_status",
    )
    if state_change_status not in _STATE_CHANGE_STATUS:
        raise ModelTransitionError(
            "model-transition route preflight.state_change_status is invalid"
        )
    return value


def _route(value: Any) -> dict[str, Any]:
    value = _exact(
        value,
        {
            "schema_version",
            "contract",
            "evidence_issuer",
            "task_sha256",
            "payload_sha256",
            "plan_binding_sha256",
            "actuation_id",
            "fresh",
            "eligible",
            "capacity",
            "failure_disposition",
            "target",
            "account_binding_sha256",
            "content_sha256",
            "route_authorization_sha256",
            "selector_sha256",
            "capability_sha256",
            "capability_status",
            "execution_authorized",
            "preflight",
            "selection",
        },
        "model-transition route evidence",
    )
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != SCHEMA_VERSION
        or value["contract"] != ROUTE_EVIDENCE_CONTRACT
    ):
        raise ModelTransitionError("model-transition route evidence contract drift")
    _issuer(value["evidence_issuer"], "model-transition route evidence issuer")
    for field in ("task_sha256", "payload_sha256", "plan_binding_sha256"):
        _sha(value[field], f"model-transition route evidence.{field}")
    _token(value["actuation_id"], "model-transition route evidence.actuation_id")
    _strict_bool(value["fresh"], "model-transition route evidence.fresh")
    _strict_bool(value["eligible"], "model-transition route evidence.eligible")
    _strict_bool(
        value["execution_authorized"],
        "model-transition route evidence.execution_authorized",
    )
    capacity = _token(value["capacity"], "model-transition route evidence.capacity")
    if capacity not in _CAPACITY:
        raise ModelTransitionError("model-transition route evidence.capacity is invalid")
    disposition = _token(
        value["failure_disposition"],
        "model-transition route evidence.failure_disposition",
    )
    if disposition not in _FAILURE_DISPOSITIONS:
        raise ModelTransitionError(
            "model-transition route evidence.failure_disposition is invalid"
        )
    capability_status = _token(
        value["capability_status"],
        "model-transition route evidence.capability_status",
    )
    if capability_status not in _CAPABILITY_STATUS:
        raise ModelTransitionError(
            "model-transition route evidence.capability_status is invalid"
        )
    _preflight(value["preflight"])
    _target(value["target"], "model-transition route evidence.target")
    for field in (
        "account_binding_sha256",
        "content_sha256",
        "route_authorization_sha256",
        "selector_sha256",
        "capability_sha256",
    ):
        _sha(value[field], f"model-transition route evidence.{field}")
    _selection(value["selection"], "model-transition route evidence.selection")
    return value


def route_evidence_sha256(value: Any) -> str:
    """Return the digest an authenticated route producer must bind."""

    route = _route(value)
    unsigned = {
        key: item
        for key, item in route.items()
        if key not in {"content_sha256", "route_authorization_sha256"}
    }
    return _digest(unsigned)


def _state(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    value = _exact(
        value,
        {
            "schema_version",
            "contract",
            "task_sha256",
            "sequence",
            "scope",
            "selection",
            "operator_selection",
            "target",
            "account_binding_sha256",
            "authorization_sha256",
            "state_sha256",
        },
        "prior model-transition state",
    )
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != SCHEMA_VERSION
        or value["contract"] != STATE_CONTRACT
    ):
        raise ModelTransitionError("prior model-transition state contract drift")
    _sha(value["task_sha256"], "prior model-transition state.task_sha256")
    if type(value["sequence"]) is not int or value["sequence"] < 1:
        raise ModelTransitionError("prior model-transition state.sequence is invalid")
    _scope(value["scope"], "prior model-transition state.scope")
    _selection(value["selection"], "prior model-transition state.selection")
    _operator_selection(
        value["operator_selection"],
        "prior model-transition state.operator_selection",
    )
    _target(value["target"], "prior model-transition state.target")
    _sha(
        value["account_binding_sha256"],
        "prior model-transition state.account_binding_sha256",
    )
    _sha(
        value["authorization_sha256"],
        "prior model-transition state.authorization_sha256",
    )
    unsigned = {key: item for key, item in value.items() if key != "state_sha256"}
    if _sha(value["state_sha256"], "prior model-transition state.state_sha256") != _digest(
        unsigned
    ):
        raise ModelTransitionError("prior model-transition state content digest mismatch")
    return value


def _same_operator(
    selected: dict[str, Any], operator: dict[str, Any]
) -> bool:
    return all(
        selected[field] == operator[field]
        for field in (
            "role",
            "model",
            "reasoning_effort",
            "qualified_classification",
        )
    ) and selected["operator_selection_sha256"] == operator["selection_sha256"]


def _scope_core(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "classification": value["classification"],
        "roles": value["roles"],
        "daybreak_required": value["daybreak_required"],
    }


def _deny_reason(
    prior: dict[str, Any] | None,
    event: dict[str, Any],
    scope: dict[str, Any],
    operator: dict[str, Any] | None,
    route: dict[str, Any],
) -> tuple[str | None, dict[str, Any] | None]:
    kind = event["kind"]
    initial = kind in _INITIAL_EVENTS
    if initial:
        if prior is not None or event["predecessor_authorization_sha256"] is not None:
            return "initial-event-has-predecessor", None
    else:
        if prior is None:
            return "prior-state-required", None
        if event["task_sha256"] != prior["task_sha256"]:
            return "task-identity-changed", None
        if event["predecessor_authorization_sha256"] != prior["authorization_sha256"]:
            return "predecessor-transition-mismatch", None

    for field in (
        "task_sha256",
        "payload_sha256",
        "plan_binding_sha256",
        "actuation_id",
    ):
        if route[field] != event[field]:
            return "route-evidence-binding-mismatch", None

    action = event["operator_action"]
    prior_operator = None if prior is None else prior["operator_selection"]
    if action == "preserve":
        if operator is not None:
            return "unexpected-operator-selection", None
        effective_operator = prior_operator
    elif action == "select":
        if operator is None or prior_operator is not None:
            return "operator-selection-action-invalid", None
        effective_operator = operator
    else:
        if operator is None or prior_operator is None:
            return "operator-replacement-action-invalid", None
        effective_operator = operator

    if kind == "reclassification":
        if prior is None or scope["reclassification_sha256"] is None:
            return "reclassification-evidence-required", effective_operator
        if _scope_core(scope) == _scope_core(prior["scope"]):
            return "reclassification-is-noop", effective_operator
        if prior["scope"]["classification"] == "security":
            return "security-floor-is-sticky", effective_operator
        if prior["scope"]["daybreak_required"] and not scope["daybreak_required"]:
            return "daybreak-requirement-is-sticky", effective_operator
    else:
        if scope["reclassification_sha256"] is not None:
            return "unexpected-reclassification-evidence", effective_operator
        if prior is not None and _scope_core(scope) != _scope_core(prior["scope"]):
            return "reclassification-required", effective_operator

    preflight = route["preflight"]
    surface = preflight["status_surface"]
    if _status_surface_is_unsafe_or_unversioned(surface):
        return "route-status-unverified", effective_operator
    if surface["safety"] in {"unsafe", "unverified"}:
        return "route-status-unverified", effective_operator
    if preflight["state_change_status"] == "unknown":
        return "route-status-unverified", effective_operator
    if (
        preflight["task_data_shared"]
        or preflight["caller_state_mutation_requested"]
        or preflight["state_change_status"] == "changed"
    ):
        return "route-status-refresh-exceeded-authority", effective_operator
    if preflight["inventory_status"] == "denied":
        return "route-status-denied", effective_operator
    if not preflight["inventory_complete"]:
        return "route-inventory-incomplete", effective_operator
    if preflight["inventory_status"] in {"missing", "stale"}:
        return "route-status-refresh-required", effective_operator
    if not preflight["selected_route_in_inventory"]:
        return "selected-route-is-not-in-inventory", effective_operator
    if not route["fresh"]:
        return "route-evidence-is-stale", effective_operator
    if not route["eligible"]:
        return "route-is-ineligible", effective_operator
    if route["capability_status"] == "probe-failed":
        return "route-capability-probe-failed", effective_operator
    if route["capability_status"] == "unknown":
        return "route-model-availability-unproven", effective_operator
    if route["capability_status"] == "absent":
        return "route-model-absent", effective_operator
    if route["capacity"] == "exhausted":
        return "route-capacity-exhausted", effective_operator
    if route["capacity"] == "unknown":
        return "route-capacity-unknown", effective_operator
    if not route["execution_authorized"]:
        return "route-execution-unauthorized", effective_operator
    if prior is not None:
        if route["target"] != prior["target"]:
            return "continuation-target-changed", effective_operator
        if route["account_binding_sha256"] != prior["account_binding_sha256"]:
            return "continuation-account-binding-changed", effective_operator

    selected = route["selection"]
    if selected["role"] == "daybreak" and (
        selected["model"] != "gpt-daybreak-blue-latest"
        or selected["reasoning_effort"] != "max"
        or selected["qualified_classification"] != "security"
    ):
        return "daybreak-selector-mismatch", effective_operator
    if _CLASSIFICATION_RANK[selected["qualified_classification"]] < _CLASSIFICATION_RANK[
        scope["classification"]
    ]:
        return "selection-below-judgment-floor", effective_operator
    if scope["daybreak_required"] and selected["role"] != "daybreak":
        return "daybreak-route-required", effective_operator
    ceiling = _ROLE_CLASSIFICATION_CEILING.get(selected["role"])
    if (
        ceiling is not None
        and _CLASSIFICATION_RANK[selected["qualified_classification"]]
        > _CLASSIFICATION_RANK[ceiling]
    ):
        return "selection-exceeds-role-ceiling", effective_operator
    if effective_operator is not None:
        if selected["provenance"] != "operator" or not _same_operator(
            selected, effective_operator
        ):
            return "operator-selection-is-sticky", effective_operator
    elif (
        selected["provenance"] == "operator"
        or selected["operator_selection_sha256"] is not None
    ):
        return "operator-selection-is-unproven", effective_operator

    if (
        prior is not None
        and effective_operator is None
        and selected != prior["selection"]
        and kind != "reclassification"
        and selected["provenance"] != "fallback"
    ):
        return "selection-change-requires-fallback", effective_operator
    return None, effective_operator


def authorize_model_transition(
    prior_state: Any,
    event: Any,
    current_scope: Any,
    operator_selection: Any,
    route_evidence: Any,
) -> dict[str, Any]:
    """Return a deterministic authorization decision or a fail-closed denial."""

    prior = _state(prior_state)
    event = _event(event)
    scope = _scope(current_scope, "current model-transition scope")
    operator = _operator_selection(operator_selection, "operator selection")
    route = _route(route_evidence)
    request = {
        "prior_state": prior,
        "event": event,
        "current_scope": scope,
        "operator_selection": operator,
        "route_evidence": route,
    }
    request_sha256 = _digest(request)
    reason, effective_operator = _deny_reason(prior, event, scope, operator, route)
    authorized = reason is None
    authorization = {
        "status": "authorized" if authorized else "denied",
        "reason": "transition-authorized" if authorized else reason,
        "disposition": "dispatch" if authorized else route["failure_disposition"],
    }
    accepted_selection = route["selection"] if authorized else None
    accepted_target = route["target"] if authorized else None
    authorization_sha256 = _digest(
        {
            "request_sha256": request_sha256,
            "authorization": authorization,
            "selection": accepted_selection,
            "target": accepted_target,
            "account_binding_sha256": (
                route["account_binding_sha256"] if authorized else None
            ),
        }
    )
    next_state = None
    if authorized:
        state = {
            "schema_version": SCHEMA_VERSION,
            "contract": STATE_CONTRACT,
            "task_sha256": event["task_sha256"],
            "sequence": 1 if prior is None else prior["sequence"] + 1,
            "scope": scope,
            "selection": accepted_selection,
            "operator_selection": effective_operator,
            "target": accepted_target,
            "account_binding_sha256": route["account_binding_sha256"],
            "authorization_sha256": authorization_sha256,
        }
        next_state = {**state, "state_sha256": _digest(state)}
    return _document(
        {
            "schema_version": SCHEMA_VERSION,
            "contract": DECISION_CONTRACT,
            "request": request,
            "request_sha256": request_sha256,
            "authorization": authorization,
            "authorization_sha256": authorization_sha256,
            "selection": accepted_selection,
            "target": accepted_target,
            "next_state": next_state,
        }
    )


def validate_authorized_transition(value: Any) -> dict[str, Any]:
    """Recompute and return an authorized decision, rejecting drift or denial."""

    value = _exact(
        value,
        {
            "schema_version",
            "contract",
            "request",
            "request_sha256",
            "authorization",
            "authorization_sha256",
            "selection",
            "target",
            "next_state",
            "content_sha256",
        },
        "model-transition decision",
    )
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != SCHEMA_VERSION
        or value["contract"] != DECISION_CONTRACT
    ):
        raise ModelTransitionError("model-transition decision contract drift")
    request = _exact(
        value["request"],
        {
            "prior_state",
            "event",
            "current_scope",
            "operator_selection",
            "route_evidence",
        },
        "model-transition decision.request",
    )
    recomputed = authorize_model_transition(
        request["prior_state"],
        request["event"],
        request["current_scope"],
        request["operator_selection"],
        request["route_evidence"],
    )
    if _canonical(value) != _canonical(recomputed):
        raise ModelTransitionError("model-transition decision content mismatch")
    if recomputed["authorization"]["status"] != "authorized":
        raise ModelTransitionError("model transition is not authorized")
    return recomputed


__all__ = [
    "DECISION_CONTRACT",
    "ROUTE_EVIDENCE_CONTRACT",
    "STATE_CONTRACT",
    "ModelTransitionError",
    "authorize_model_transition",
    "route_evidence_sha256",
    "validate_authorized_transition",
]
