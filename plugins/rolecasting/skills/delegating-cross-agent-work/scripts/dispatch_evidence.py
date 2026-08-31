"""Validate one generic Rolecasting v3 dispatch-evidence bundle.

Task Witness injects the descriptor-bound bundle view and retained trust
snapshot. This module deliberately exposes no standalone path- or trust-taking
entrypoint.
"""

from __future__ import annotations

import hashlib
from typing import Any

BUNDLE_CONTRACT = "rolecasting-dispatch-evidence-v3"
PLAN_CONTRACT = "rolecasting-dispatch-plan-v3"
MODEL_CONTRACT = "rolecasting-model-selection-receipt-v3"
RESULT_CONTRACT = "rolecasting-execution-result-receipt-v3"
PROJECTION_CONTRACT = "rolecasting-dispatch-projection-v3"

_TARGET_PAIRS = {
    ("codex", "chatgpt-codex"),
    ("codex", "codex-cli-tui"),
    ("claude", "claude-code"),
    ("claude", "claude-desktop"),
    ("cursor", "cursor"),
    ("cursor", "cursor-agent"),
}
_RELATIONSHIPS = {"child", "peer", "external"}
_OWNERSHIPS = {"leader-owned", "user-owned"}
_TRANSPORTS = {"native-tool", "task-api", "cli", "app-server", "remote-api"}
_ASSURANCE_LEVELS = {
    "product-attested",
    "controller-observed",
    "self-reported",
}
_ASSURANCE_RANK = {
    "self-reported": 0,
    "controller-observed": 1,
    "product-attested": 2,
}
_ASSURANCE_FIELDS = {
    "target",
    "model",
    "topology",
    "authority",
    "execution_result",
}


def _witness() -> Any:
    witness = globals().get("_TASK_WITNESS")
    if witness is None:
        raise RuntimeError("Rolecasting dispatch evidence requires Task Witness")
    return witness


def _transition_guard() -> Any:
    modules = globals().get("_VERIFIED_MODULES")
    if not isinstance(modules, dict) or "model-transition" not in modules:
        raise RuntimeError("Rolecasting model-transition guard is unavailable")
    guard = modules["model-transition"]
    if not callable(getattr(guard, "validate_authorized_transition", None)):
        raise RuntimeError("Rolecasting model-transition guard API drift")
    return guard


def _authenticated_route_evidence(value: Any) -> dict[str, Any]:
    witness = _witness()
    modules = globals().get("_VERIFIED_MODULES")
    if not isinstance(modules, dict) or "route-evidence" not in modules:
        raise witness.EvidenceError(
            "Rolecasting authenticated route evidence is unavailable"
        )
    verifier = modules["route-evidence"]
    validate = getattr(verifier, "validate_authenticated_route_evidence", None)
    if not callable(validate):
        raise witness.EvidenceError(
            "Rolecasting authenticated route-evidence API drift"
        )
    try:
        authenticated = validate(value)
    except Exception as error:
        raise witness.EvidenceError(
            "Rolecasting route evidence is not authenticated"
        ) from error
    if authenticated != value:
        raise witness.EvidenceError(
            "Rolecasting authenticated route evidence is cross-bound"
        )
    return value


def _raw_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _identity(value: Any, label: str) -> dict[str, Any]:
    return _witness().identity(value, label)


def _target(value: Any, label: str) -> dict[str, Any]:
    witness = _witness()
    value = witness.exact(
        value,
        {"product_family", "surface", "executor", "version"},
        label,
    )
    product_family = witness.token(value["product_family"], f"{label}.product_family")
    surface = witness.token(value["surface"], f"{label}.surface")
    if (product_family, surface) not in _TARGET_PAIRS:
        raise witness.EvidenceError(f"{label} target pair is invalid")
    witness.text(value["executor"], f"{label}.executor")
    witness.text(value["version"], f"{label}.version")
    return value


def _topology(value: Any, label: str) -> dict[str, Any]:
    witness = _witness()
    value = witness.exact(
        value,
        {"relationship", "ownership", "transport"},
        label,
    )
    relationship = witness.token(value["relationship"], f"{label}.relationship")
    ownership = witness.token(value["ownership"], f"{label}.ownership")
    transport = witness.token(value["transport"], f"{label}.transport")
    if relationship not in _RELATIONSHIPS:
        raise witness.EvidenceError(f"{label}.relationship is invalid")
    if ownership not in _OWNERSHIPS:
        raise witness.EvidenceError(f"{label}.ownership is invalid")
    if transport not in _TRANSPORTS:
        raise witness.EvidenceError(f"{label}.transport is invalid")
    return value


def _assurance(value: Any, label: str) -> dict[str, Any]:
    witness = _witness()
    value = witness.exact(value, _ASSURANCE_FIELDS | {"evidence"}, label)
    for field in _ASSURANCE_FIELDS:
        level = witness.token(value[field], f"{label}.{field}")
        if level not in _ASSURANCE_LEVELS:
            raise witness.EvidenceError(f"{label}.{field} assurance is invalid")
    _identity(value["evidence"], f"{label}.evidence")
    return value


def _assurance_minimum(
    value: Any,
    observed: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    witness = _witness()
    value = witness.exact(value, _ASSURANCE_FIELDS, label)
    for field in _ASSURANCE_FIELDS:
        minimum = witness.token(value[field], f"{label}.{field}")
        if minimum not in _ASSURANCE_LEVELS:
            raise witness.EvidenceError(f"{label}.{field} assurance is invalid")
        if _ASSURANCE_RANK[observed[field]] < _ASSURANCE_RANK[minimum]:
            raise witness.EvidenceError(
                f"dispatch.assurance.{field} is below its assurance minimum"
            )
    return value


def _authority(value: Any, label: str) -> dict[str, Any]:
    witness = _witness()
    value = witness.exact(
        value,
        {"access", "subdelegation", "external_action", "evidence"},
        label,
    )
    if (
        value["access"] != "read-only"
        or value["subdelegation"] is not False
        or value["external_action"] is not False
    ):
        raise witness.EvidenceError(f"{label} exceeds read-only dispatch authority")
    _identity(value["evidence"], f"{label}.evidence")
    return value


def _user_authority(
    value: Any,
    ownership: str,
    label: str,
) -> dict[str, Any] | None:
    witness = _witness()
    if ownership == "user-owned":
        if value is None:
            raise witness.EvidenceError(f"{label} is required for user-owned dispatch")
        return _identity(value, label)
    if value is not None:
        raise witness.EvidenceError(f"{label} must be absent for leader-owned dispatch")
    return None


def _isolation(value: Any, label: str) -> dict[str, Any]:
    witness = _witness()
    value = witness.exact(value, {"session", "context", "enforceable"}, label)
    witness.text(value["session"], f"{label}.session")
    witness.text(value["context"], f"{label}.context")
    if value["enforceable"] is not True:
        raise witness.EvidenceError(f"{label} is not enforceable")
    return value


def _dispatch(
    value: Any,
    subject: dict[str, Any],
    seen: set[str],
) -> dict[str, Any]:
    witness = _witness()
    value = witness.exact(
        value,
        {
            "execution_id",
            "plan_binding_sha256",
            "role",
            "target",
            "topology",
            "subject",
            "candidate",
            "scope",
            "request",
            "return_contract",
            "verification_contract",
            "stop_contract",
            "model_transition_sha256",
            "model_sha256",
            "authority",
            "user_authority",
            "isolation",
            "assurance",
            "assurance_minimum",
        },
        "Rolecasting dispatch",
    )
    execution_id = witness.token(value["execution_id"], "dispatch.execution_id")
    if execution_id in seen:
        raise witness.EvidenceError("Rolecasting dispatch execution ID is duplicated")
    seen.add(execution_id)
    plan_binding_sha256 = witness.sha(
        value["plan_binding_sha256"], "dispatch.plan_binding_sha256"
    )
    role = witness.token(value["role"], "dispatch.role")
    target = _target(value["target"], "dispatch.target")
    topology = _topology(value["topology"], "dispatch.topology")
    if _identity(value["subject"], "dispatch.subject") != subject:
        raise witness.EvidenceError("Rolecasting dispatch subject is cross-bound")
    candidate = _identity(value["candidate"], "dispatch.candidate")
    scope = _identity(value["scope"], "dispatch.scope")
    request = _identity(value["request"], "dispatch.request")
    return_contract = witness.text(value["return_contract"], "dispatch.return_contract")
    verification_contract = witness.text(
        value["verification_contract"], "dispatch.verification_contract"
    )
    stop_contract = witness.text(value["stop_contract"], "dispatch.stop_contract")
    model_transition_sha256 = witness.sha(
        value["model_transition_sha256"], "dispatch.model_transition_sha256"
    )
    model_sha256 = witness.sha(value["model_sha256"], "dispatch.model_sha256")
    authority = _authority(value["authority"], "dispatch.authority")
    user_authority = _user_authority(
        value["user_authority"],
        topology["ownership"],
        "dispatch.user authority",
    )
    isolation = _isolation(value["isolation"], "dispatch.isolation")
    assurance = _assurance(value["assurance"], "dispatch.assurance")
    assurance_minimum = _assurance_minimum(
        value["assurance_minimum"],
        assurance,
        "dispatch.assurance_minimum",
    )
    return {
        "execution_id": execution_id,
        "plan_binding_sha256": plan_binding_sha256,
        "role": role,
        "target": target,
        "topology": topology,
        "candidate": candidate,
        "scope": scope,
        "request": request,
        "return_contract": return_contract,
        "verification_contract": verification_contract,
        "stop_contract": stop_contract,
        "model_transition_sha256": model_transition_sha256,
        "model_sha256": model_sha256,
        "authority": authority,
        "user_authority": user_authority,
        "isolation": isolation,
        "assurance": assurance,
        "assurance_minimum": assurance_minimum,
        "dispatch_sha256": witness.digest(value),
    }


def _issuer(
    value: Any,
    trust_snapshot: dict[str, Any],
    label: str,
    capability: str,
) -> dict[str, Any]:
    return _witness().issuer(value, trust_snapshot, label, capability)


def _transition(
    value: Any,
    raw: bytes,
    dispatch: dict[str, Any],
    subject: dict[str, Any],
    trust_snapshot: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    witness = _witness()
    try:
        guard = _transition_guard()
        transition = guard.validate_authorized_transition(value)
    except Exception as error:
        raise witness.EvidenceError(
            "Rolecasting model transition is not authorized"
        ) from error
    transition_sha256 = _raw_sha256(raw)
    if transition_sha256 != dispatch["model_transition_sha256"]:
        raise witness.EvidenceError("Rolecasting model-transition digest mismatch")
    route = _authenticated_route_evidence(
        transition["request"]["route_evidence"]
    )
    route_digest = getattr(guard, "route_evidence_sha256", None)
    if (
        not callable(route_digest)
        or route_digest(route) != route["content_sha256"]
    ):
        raise witness.EvidenceError(
            "Rolecasting authenticated route-evidence digest mismatch"
        )
    route_issuer = _issuer(
        route["evidence_issuer"],
        trust_snapshot,
        "Rolecasting route-evidence issuer",
        "route-evidence",
    )
    event = transition["request"]["event"]
    if (
        event["payload_sha256"] != dispatch["request"]["content_sha256"]
        or transition["target"] != dispatch["target"]
    ):
        raise witness.EvidenceError("Rolecasting model transition is cross-bound")
    if event["task_sha256"] != subject["content_sha256"]:
        raise witness.EvidenceError(
            "Rolecasting model transition task identity mismatch"
        )
    if (
        event["plan_binding_sha256"] != dispatch["plan_binding_sha256"]
        or event["actuation_id"] != dispatch["execution_id"]
    ):
        raise witness.EvidenceError(
            "Rolecasting model transition plan or actuation mismatch"
        )
    return (
        {
            "model_transition_sha256": transition_sha256,
            "model_transition_authorization_sha256": transition[
                "authorization_sha256"
            ],
            "model_transition_event": transition["request"]["event"]["kind"],
            "model_transition_task_sha256": transition["request"]["event"][
                "task_sha256"
            ],
            "route_evidence_issuer": route_issuer,
        },
        transition,
    )


def _model(
    value: Any,
    raw: bytes,
    dispatch: dict[str, Any],
    subject: dict[str, Any],
    trust_snapshot: dict[str, Any],
    transition: dict[str, Any],
) -> dict[str, Any]:
    witness = _witness()
    value = witness.document(
        value,
        {
            "issuer",
            "subject",
            "execution_id",
            "target",
            "model",
            "reasoning_effort",
            "capability",
            "model_transition_sha256",
        },
        "Rolecasting model selection receipt",
        MODEL_CONTRACT,
    )
    if (
        _identity(value["subject"], "model.subject") != subject
        or value["execution_id"] != dispatch["execution_id"]
        or _target(value["target"], "model.target") != dispatch["target"]
    ):
        raise witness.EvidenceError(
            "Rolecasting model selection receipt is cross-bound"
        )
    model = witness.text(value["model"], "model.model")
    reasoning_effort = witness.text(value["reasoning_effort"], "model.reasoning_effort")
    capability = witness.exact(
        value["capability"], {"status", "evidence"}, "model.capability"
    )
    if capability["status"] != "available":
        raise witness.EvidenceError("Rolecasting model capability is unavailable")
    _identity(capability["evidence"], "model.capability.evidence")
    if (
        witness.sha(
            value["model_transition_sha256"],
            "model.model_transition_sha256",
        )
        != dispatch["model_transition_sha256"]
        or transition["selection"]["model"] != model
        or transition["selection"]["reasoning_effort"] != reasoning_effort
        or transition["request"]["route_evidence"]["capability_sha256"]
        != capability["evidence"]["content_sha256"]
    ):
        raise witness.EvidenceError(
            "Rolecasting model selection receipt transition mismatch"
        )
    model_sha256 = _raw_sha256(raw)
    if model_sha256 != dispatch["model_sha256"]:
        raise witness.EvidenceError(
            "Rolecasting model selection receipt digest mismatch"
        )
    return {
        "model": model,
        "reasoning_effort": reasoning_effort,
        "model_sha256": model_sha256,
        "model_issuer": _issuer(
            value["issuer"], trust_snapshot, "Rolecasting model issuer", "model"
        ),
    }


def _result(
    value: Any,
    raw: bytes,
    plan_raw: bytes,
    model_raw: bytes,
    transition_raw: bytes,
    dispatch: dict[str, Any],
    subject: dict[str, Any],
    trust_snapshot: dict[str, Any],
) -> dict[str, Any]:
    witness = _witness()
    value = witness.document(
        value,
        {
            "issuer",
            "subject",
            "execution_id",
            "plan_sha256",
            "dispatch_sha256",
            "model_sha256",
            "model_transition_sha256",
            "request",
            "returned",
            "verification",
            "stop",
            "target",
            "topology",
            "assurance",
            "assurance_minimum",
            "user_authority",
            "session",
            "context",
            "authority",
            "before_candidate",
            "after_candidate",
            "usable",
        },
        "Rolecasting execution-result receipt",
        RESULT_CONTRACT,
    )
    if (
        _identity(value["subject"], "result.subject") != subject
        or value["execution_id"] != dispatch["execution_id"]
    ):
        raise witness.EvidenceError(
            "Rolecasting execution-result receipt is cross-bound"
        )
    if (
        value["plan_sha256"] != _raw_sha256(plan_raw)
        or value["dispatch_sha256"] != dispatch["dispatch_sha256"]
        or value["model_sha256"] != _raw_sha256(model_raw)
        or value["model_transition_sha256"] != _raw_sha256(transition_raw)
    ):
        raise witness.EvidenceError(
            "Rolecasting execution-result receipt provenance mismatch"
        )
    request = _identity(value["request"], "result.request")
    returned = _identity(value["returned"], "result.returned")
    verification = _identity(value["verification"], "result.verification")
    stop = _identity(value["stop"], "result.stop")
    if request != dispatch["request"]:
        raise witness.EvidenceError(
            "Rolecasting execution-result receipt request mismatch"
        )
    if (
        returned["kind"] != dispatch["return_contract"]
        or verification["kind"] != dispatch["verification_contract"]
        or stop["kind"] != dispatch["stop_contract"]
    ):
        raise witness.EvidenceError(
            "Rolecasting execution-result receipt contract mismatch"
        )
    if (
        value["target"] != dispatch["target"]
        or value["topology"] != dispatch["topology"]
        or value["assurance"] != dispatch["assurance"]
        or value["assurance_minimum"] != dispatch["assurance_minimum"]
        or value["user_authority"] != dispatch["user_authority"]
        or value["session"] != dispatch["isolation"]["session"]
        or value["context"] != dispatch["isolation"]["context"]
        or value["authority"] != dispatch["authority"]
    ):
        raise witness.EvidenceError(
            "Rolecasting execution-result target, topology, or authority mismatch"
        )
    if (
        _identity(value["before_candidate"], "result.before_candidate")
        != dispatch["candidate"]
        or _identity(value["after_candidate"], "result.after_candidate")
        != dispatch["candidate"]
    ):
        raise witness.EvidenceError("Rolecasting execution-result changed candidate")
    if type(value["usable"]) is not bool:
        raise witness.EvidenceError(
            "Rolecasting execution-result usable must be a strict Boolean"
        )
    return {
        "result_sha256": _raw_sha256(raw),
        "result_issuer": _issuer(
            value["issuer"],
            trust_snapshot,
            "Rolecasting execution-result issuer",
            "execution-result",
        ),
        "returned": returned,
        "verification": verification,
        "stop": stop,
        "usable": value["usable"],
    }


def _validate_bundle(bundle: Any, *, trust_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return Rolecasting's sole canonical projection for a captured bundle."""

    witness = _witness()
    manifest, manifest_raw = bundle.read_json(
        "manifest.json", "Rolecasting dispatch-evidence manifest"
    )
    manifest = witness.document(
        manifest,
        {
            "producer",
            "subject",
            "plan_sha256",
            "transitions",
            "models",
            "results",
        },
        "Rolecasting dispatch-evidence manifest",
        BUNDLE_CONTRACT,
    )
    witness.producer(
        manifest["producer"], trust_snapshot, "Rolecasting dispatch-evidence producer"
    )
    producer = manifest["producer"]
    subject = _identity(manifest["subject"], "Rolecasting evidence subject")
    plan, plan_raw = bundle.read_json("plan.json", "Rolecasting dispatch plan")
    if witness.sha(manifest["plan_sha256"], "Rolecasting plan digest") != _raw_sha256(
        plan_raw
    ):
        raise witness.EvidenceError("Rolecasting dispatch plan digest mismatch")
    plan = witness.document(
        plan,
        {"subject", "plan_binding_sha256", "dispatches"},
        "Rolecasting dispatch plan",
        PLAN_CONTRACT,
    )
    if _identity(plan["subject"], "Rolecasting plan subject") != subject:
        raise witness.EvidenceError("Rolecasting dispatch plan subject is cross-bound")
    plan_binding_sha256 = witness.sha(
        plan["plan_binding_sha256"], "Rolecasting plan binding"
    )
    if not isinstance(plan["dispatches"], list) or not plan["dispatches"]:
        raise witness.EvidenceError("Rolecasting dispatch plan must be nonempty")
    seen: set[str] = set()
    dispatches = [_dispatch(value, subject, seen) for value in plan["dispatches"]]
    if any(
        dispatch["plan_binding_sha256"] != plan_binding_sha256
        for dispatch in dispatches
    ):
        raise witness.EvidenceError("Rolecasting dispatch plan binding is cross-bound")
    sessions = [value["isolation"]["session"] for value in dispatches]
    contexts = [value["isolation"]["context"] for value in dispatches]
    if len(sessions) != len(set(sessions)) or len(contexts) != len(set(contexts)):
        raise witness.EvidenceError("Rolecasting dispatch isolation is not distinct")
    if (
        not isinstance(manifest["transitions"], dict)
        or not isinstance(manifest["models"], dict)
        or not isinstance(manifest["results"], dict)
        or set(manifest["transitions"]) != seen
        or set(manifest["models"]) != seen
        or set(manifest["results"]) != seen
    ):
        raise witness.EvidenceError(
            "Rolecasting transition/model/result inventory does not match the dispatch plan"
        )

    expected_names = {"manifest.json", "plan.json"}
    executions: dict[str, Any] = {}
    transition_authorizations: set[str] = set()
    for dispatch in dispatches:
        execution_id = dispatch["execution_id"]
        transition_name = f"transition-{execution_id}.json"
        model_name = f"model-{execution_id}.json"
        result_name = f"result-{execution_id}.json"
        expected_names.update((transition_name, model_name, result_name))
        transition, transition_raw = bundle.read_json(
            transition_name, f"Rolecasting model transition {execution_id}"
        )
        model, model_raw = bundle.read_json(
            model_name, f"Rolecasting model {execution_id}"
        )
        result, result_raw = bundle.read_json(
            result_name, f"Rolecasting result {execution_id}"
        )
        if witness.sha(
            manifest["transitions"][execution_id],
            f"Rolecasting transition inventory {execution_id}",
        ) != _raw_sha256(transition_raw) or witness.sha(
            manifest["models"][execution_id],
            f"Rolecasting model inventory {execution_id}",
        ) != _raw_sha256(model_raw) or witness.sha(
            manifest["results"][execution_id],
            f"Rolecasting result inventory {execution_id}",
        ) != _raw_sha256(result_raw):
            raise witness.EvidenceError(
                "Rolecasting artifact inventory digest mismatch"
            )
        transition_projection, transition = _transition(
            transition,
            transition_raw,
            dispatch,
            subject,
            trust_snapshot,
        )
        authorization_sha256 = transition["authorization_sha256"]
        if authorization_sha256 in transition_authorizations:
            raise witness.EvidenceError(
                "Rolecasting model-transition authorization is duplicated"
            )
        transition_authorizations.add(authorization_sha256)
        model_projection = _model(
            model,
            model_raw,
            dispatch,
            subject,
            trust_snapshot,
            transition,
        )
        result_projection = _result(
            result,
            result_raw,
            plan_raw,
            model_raw,
            transition_raw,
            dispatch,
            subject,
            trust_snapshot,
        )
        executions[execution_id] = {
            key: dispatch[key]
            for key in (
                "execution_id",
                "plan_binding_sha256",
                "role",
                "target",
                "topology",
                "candidate",
                "scope",
                "request",
                "return_contract",
                "verification_contract",
                "stop_contract",
                "model_transition_sha256",
                "authority",
                "user_authority",
                "isolation",
                "assurance",
                "assurance_minimum",
                "dispatch_sha256",
            )
        }
        executions[execution_id].update(transition_projection)
        executions[execution_id].update(model_projection)
        executions[execution_id].update(result_projection)
    if bundle.names != expected_names:
        raise witness.EvidenceError("Rolecasting bundle has missing or extra files")

    projection = {
        "schema_version": 1,
        "contract": PROJECTION_CONTRACT,
        "evidence_contract": BUNDLE_CONTRACT,
        "manifest_sha256": _raw_sha256(manifest_raw),
        "plan_sha256": _raw_sha256(plan_raw),
        "plan_binding_sha256": plan_binding_sha256,
        "subject": subject,
        "producer": producer,
        "executions": executions,
    }
    return {**projection, "content_sha256": witness.digest(projection)}
