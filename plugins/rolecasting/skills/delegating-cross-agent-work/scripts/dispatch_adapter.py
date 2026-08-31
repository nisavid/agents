"""Freeze supplied execution facts into one Rolecasting v3 evidence bundle.

This pure bootstrap renderer does not launch a worker, choose a model, or
authenticate facts outside its process. Its caller must supply already-observed
execution facts and content-addressed evidence. The renderer validates that
closed request, binds its own exact source identity as producer and issuer, and
returns a complete canonical file map for bootstrap tests. It is not registered
as a Task Witness producer or issuer and cannot create publication evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

REQUEST_CONTRACT = "rolecasting-bootstrap-dispatch-request-v3"
BUNDLE_CONTRACT = "rolecasting-dispatch-evidence-v3"
PLAN_CONTRACT = "rolecasting-dispatch-plan-v3"
MODEL_CONTRACT = "rolecasting-model-selection-receipt-v3"
RESULT_CONTRACT = "rolecasting-execution-result-receipt-v3"
PRODUCER_ID = "rolecasting-bootstrap-dispatch-v3"
ISSUER_CONTRACT = "rolecasting-bootstrap-adapter-v3"
ISSUER_ID = "rolecasting-bootstrap-adapter-v3"

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
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


class AdapterError(ValueError):
    """The supplied execution facts cannot truthfully produce evidence."""


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
        raise AdapterError(
            "dispatch request is not canonical-JSON compatible"
        ) from error


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _transition_guard() -> Any:
    modules = globals().get("_VERIFIED_MODULES")
    if not isinstance(modules, dict) or "model-transition" not in modules:
        raise AdapterError("verified model-transition guard is unavailable")
    guard = modules["model-transition"]
    if not callable(getattr(guard, "validate_authorized_transition", None)):
        raise AdapterError("verified model-transition guard API drift")
    return guard


def _authorized_transition(value: Any) -> dict[str, Any]:
    try:
        guard = _transition_guard()
        transition = guard.validate_authorized_transition(value)
        modules = globals().get("_VERIFIED_MODULES")
        if not isinstance(modules, dict) or "route-evidence" not in modules:
            raise AdapterError("authenticated route evidence is unavailable")
        verifier = modules["route-evidence"]
        validate = getattr(verifier, "validate_authenticated_route_evidence", None)
        if not callable(validate):
            raise AdapterError("authenticated route-evidence API drift")
        route = transition["request"]["route_evidence"]
        if validate(route) != route:
            raise AdapterError("authenticated route evidence is cross-bound")
        route_digest = getattr(guard, "route_evidence_sha256", None)
        if (
            not callable(route_digest)
            or route_digest(route) != route["content_sha256"]
        ):
            raise AdapterError("authenticated route-evidence digest mismatch")
        return transition
    except Exception as error:
        if isinstance(error, AdapterError):
            raise
        raise AdapterError("dispatch model transition is not authorized") from error


def _document(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "content_sha256": _digest(value)}


def _raw(value: dict[str, Any]) -> bytes:
    return _canonical(value) + b"\n"


def _exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise AdapterError(f"{label} schema drift")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise AdapterError(f"{label} must be a non-empty string")
    return value


def _token(value: Any, label: str) -> str:
    value = _text(value, label)
    if _TOKEN.fullmatch(value) is None:
        raise AdapterError(f"{label} is not a closed token")
    return value


def _sha(value: Any, label: str) -> str:
    value = _text(value, label)
    if _HEX.fullmatch(value) is None:
        raise AdapterError(f"{label} must be a SHA-256 digest")
    return value


def _identity(value: Any, label: str) -> dict[str, Any]:
    value = _exact(value, {"kind", "value", "content_sha256"}, label)
    _text(value["kind"], f"{label}.kind")
    _text(value["value"], f"{label}.value")
    _sha(value["content_sha256"], f"{label}.content_sha256")
    return value


def _target(value: Any, label: str) -> dict[str, Any]:
    value = _exact(
        value,
        {"product_family", "surface", "executor", "version"},
        label,
    )
    product_family = _token(value["product_family"], f"{label}.product_family")
    surface = _token(value["surface"], f"{label}.surface")
    if (product_family, surface) not in _TARGET_PAIRS:
        raise AdapterError(f"{label} target pair is invalid")
    _text(value["executor"], f"{label}.executor")
    _text(value["version"], f"{label}.version")
    return value


def _topology(value: Any, label: str) -> dict[str, Any]:
    value = _exact(value, {"relationship", "ownership", "transport"}, label)
    relationship = _token(value["relationship"], f"{label}.relationship")
    ownership = _token(value["ownership"], f"{label}.ownership")
    transport = _token(value["transport"], f"{label}.transport")
    if relationship not in _RELATIONSHIPS:
        raise AdapterError(f"{label}.relationship is invalid")
    if ownership not in _OWNERSHIPS:
        raise AdapterError(f"{label}.ownership is invalid")
    if transport not in _TRANSPORTS:
        raise AdapterError(f"{label}.transport is invalid")
    return value


def _assurance(value: Any, label: str) -> dict[str, Any]:
    value = _exact(value, _ASSURANCE_FIELDS | {"evidence"}, label)
    for field in _ASSURANCE_FIELDS:
        level = _token(value[field], f"{label}.{field}")
        if level not in _ASSURANCE_LEVELS:
            raise AdapterError(f"{label}.{field} assurance is invalid")
    _identity(value["evidence"], f"{label}.evidence")
    return value


def _assurance_minimum(
    value: Any,
    observed: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    value = _exact(value, _ASSURANCE_FIELDS, label)
    for field in _ASSURANCE_FIELDS:
        minimum = _token(value[field], f"{label}.{field}")
        if minimum not in _ASSURANCE_LEVELS:
            raise AdapterError(f"{label}.{field} assurance is invalid")
        if _ASSURANCE_RANK[observed[field]] < _ASSURANCE_RANK[minimum]:
            raise AdapterError(
                f"dispatch.assurance.{field} is below its assurance minimum"
            )
    return value


def _source_bytes() -> bytes:
    path = Path(__file__)
    if not path.is_absolute() or ".." in path.parts:
        raise AdapterError("bootstrap adapter source path is not canonical")
    flags = os.O_RDONLY
    for name in ("O_CLOEXEC", "O_NOFOLLOW"):
        if not hasattr(os, name):
            raise AdapterError("bootstrap adapter source identity is unsupported")
        flags |= int(getattr(os, name))
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise AdapterError("bootstrap adapter source cannot be opened") from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_mode & 0o022
            or before.st_nlink != 1
        ):
            raise AdapterError("bootstrap adapter source disposition is unsafe")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        identity_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
        if tuple(getattr(before, field) for field in identity_fields) != tuple(
            getattr(after, field) for field in identity_fields
        ):
            raise AdapterError("bootstrap adapter source changed during capture")
        visible = os.stat(path, follow_symlinks=False)
        if tuple(getattr(after, field) for field in identity_fields) != tuple(
            getattr(visible, field) for field in identity_fields
        ):
            raise AdapterError("bootstrap adapter source changed during capture")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def implementation_sha256() -> str:
    """Return the source identity embedded in bootstrap producer and issuer docs."""

    return hashlib.sha256(_source_bytes()).hexdigest()


def _actor(
    value: Any,
    identifier: str,
    contract: str,
    label: str,
) -> dict[str, Any]:
    value = _exact(value, {label + "_id", "contract", "implementation_sha256"}, label)
    if (
        _token(value[label + "_id"], f"{label}.{label}_id") != identifier
        or value["contract"] != contract
        or _sha(value["implementation_sha256"], f"{label}.implementation_sha256")
        != implementation_sha256()
    ):
        raise AdapterError(f"{label} is not bound to this bootstrap adapter")
    return value


def _authority(value: Any) -> dict[str, Any]:
    value = _exact(
        value,
        {"access", "subdelegation", "external_action", "evidence"},
        "dispatch.authority",
    )
    if (
        value["access"] != "read-only"
        or value["subdelegation"] is not False
        or value["external_action"] is not False
    ):
        raise AdapterError("dispatch authority exceeds read-only execution")
    _identity(value["evidence"], "dispatch.authority.evidence")
    return value


def _user_authority(value: Any, ownership: str) -> dict[str, Any] | None:
    if ownership == "user-owned":
        if value is None:
            raise AdapterError(
                "dispatch user authority is required for user-owned dispatch"
            )
        return _identity(value, "dispatch.user authority")
    if value is not None:
        raise AdapterError(
            "dispatch user authority must be absent for leader-owned dispatch"
        )
    return None


def _isolation(value: Any) -> dict[str, Any]:
    value = _exact(
        value,
        {"session", "context", "enforceable"},
        "dispatch.isolation",
    )
    _text(value["session"], "dispatch.isolation.session")
    _text(value["context"], "dispatch.isolation.context")
    if value["enforceable"] is not True:
        raise AdapterError("dispatch isolation is not enforceable")
    return value


def _fact(
    value: Any,
    subject: dict[str, Any],
    seen: set[str],
) -> dict[str, Any]:
    value = _exact(
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
            "authority",
            "user_authority",
            "isolation",
            "assurance",
            "assurance_minimum",
            "model",
            "reasoning_effort",
            "model_capability",
            "model_transition",
            "returned",
            "verification",
            "stop",
            "before_candidate",
            "after_candidate",
            "usable",
        },
        "dispatch fact",
    )
    execution_id = _token(value["execution_id"], "dispatch.execution_id")
    if execution_id in seen:
        raise AdapterError("dispatch execution ID is duplicated")
    seen.add(execution_id)
    plan_binding_sha256 = _sha(
        value["plan_binding_sha256"], "dispatch.plan_binding_sha256"
    )
    _token(value["role"], "dispatch.role")
    _target(value["target"], "dispatch.target")
    topology = _topology(value["topology"], "dispatch.topology")
    if _identity(value["subject"], "dispatch.subject") != subject:
        raise AdapterError("dispatch subject is cross-bound")
    candidate = _identity(value["candidate"], "dispatch.candidate")
    _identity(value["scope"], "dispatch.scope")
    _identity(value["request"], "dispatch.request")
    return_contract = _text(value["return_contract"], "dispatch.return_contract")
    verification_contract = _text(
        value["verification_contract"], "dispatch.verification_contract"
    )
    stop_contract = _text(value["stop_contract"], "dispatch.stop_contract")
    _authority(value["authority"])
    _user_authority(value["user_authority"], topology["ownership"])
    _isolation(value["isolation"])
    assurance = _assurance(value["assurance"], "dispatch.assurance")
    _assurance_minimum(
        value["assurance_minimum"],
        assurance,
        "dispatch.assurance_minimum",
    )
    model = _text(value["model"], "dispatch.model")
    reasoning_effort = _text(
        value["reasoning_effort"], "dispatch.reasoning_effort"
    )
    capability = _exact(
        value["model_capability"], {"status", "evidence"}, "model capability"
    )
    if capability["status"] != "available":
        raise AdapterError("selected model is not evidenced as available")
    capability_evidence = _identity(
        capability["evidence"], "model capability.evidence"
    )
    transition = _authorized_transition(value["model_transition"])
    transition_event = transition["request"]["event"]
    if transition_event["payload_sha256"] != value["request"][
        "content_sha256"
    ]:
        raise AdapterError("dispatch model transition is bound to another request")
    if transition_event["task_sha256"] != subject["content_sha256"]:
        raise AdapterError("dispatch model transition task identity mismatch")
    if (
        transition_event["plan_binding_sha256"] != plan_binding_sha256
        or transition_event["actuation_id"] != execution_id
    ):
        raise AdapterError("dispatch model transition plan or actuation mismatch")
    if transition["target"] != value["target"]:
        raise AdapterError("dispatch model transition target mismatch")
    if (
        transition["selection"]["model"] != model
        or transition["selection"]["reasoning_effort"] != reasoning_effort
    ):
        raise AdapterError("dispatch model transition selection mismatch")
    if (
        transition["request"]["route_evidence"]["capability_sha256"]
        != capability_evidence["content_sha256"]
    ):
        raise AdapterError("dispatch model capability is cross-bound")
    returned = _identity(value["returned"], "dispatch.returned")
    verification = _identity(value["verification"], "dispatch.verification")
    stop = _identity(value["stop"], "dispatch.stop")
    if (
        returned["kind"] != return_contract
        or verification["kind"] != verification_contract
        or stop["kind"] != stop_contract
    ):
        raise AdapterError("dispatch returned evidence does not match its contracts")
    if (
        _identity(value["before_candidate"], "dispatch.before_candidate") != candidate
        or _identity(value["after_candidate"], "dispatch.after_candidate") != candidate
    ):
        raise AdapterError("dispatch result changed candidate")
    if type(value["usable"]) is not bool:
        raise AdapterError("dispatch usable must be a strict Boolean")
    return value


def render_dispatch_bundle(request: Any) -> dict[str, bytes]:
    """Render one complete flat bundle from already-observed execution facts."""

    request = _exact(
        request,
        {
            "schema_version",
            "contract",
            "content_sha256",
            "producer",
            "issuer",
            "subject",
            "dispatches",
        },
        "bootstrap dispatch request",
    )
    if type(request["schema_version"]) is not int or request["schema_version"] != 1:
        raise AdapterError("bootstrap dispatch request schema version mismatch")
    if request["contract"] != REQUEST_CONTRACT:
        raise AdapterError("bootstrap dispatch request contract mismatch")
    unsigned = {key: item for key, item in request.items() if key != "content_sha256"}
    if _sha(
        request["content_sha256"], "bootstrap dispatch request.content_sha256"
    ) != _digest(unsigned):
        raise AdapterError("bootstrap dispatch request content digest mismatch")
    producer = _actor(request["producer"], PRODUCER_ID, BUNDLE_CONTRACT, "producer")
    issuer = _actor(request["issuer"], ISSUER_ID, ISSUER_CONTRACT, "issuer")
    subject = _identity(request["subject"], "bootstrap dispatch request.subject")
    if not isinstance(request["dispatches"], list) or not request["dispatches"]:
        raise AdapterError("bootstrap dispatch request must contain dispatches")
    seen: set[str] = set()
    facts = [_fact(value, subject, seen) for value in request["dispatches"]]
    sessions = [value["isolation"]["session"] for value in facts]
    contexts = [value["isolation"]["context"] for value in facts]
    if len(sessions) != len(set(sessions)) or len(contexts) != len(set(contexts)):
        raise AdapterError("dispatch isolation is not distinct")
    plan_bindings = {value["plan_binding_sha256"] for value in facts}
    if len(plan_bindings) != 1:
        raise AdapterError("dispatch facts do not share one plan binding")
    plan_binding_sha256 = next(iter(plan_bindings))

    files: dict[str, bytes] = {}
    plan_dispatches = []
    transition_digests: dict[str, str] = {}
    model_digests: dict[str, str] = {}
    model_raw: dict[str, bytes] = {}
    for fact in facts:
        execution_id = fact["execution_id"]
        transition = fact["model_transition"]
        transition_bytes = _raw(transition)
        transition_digests[execution_id] = hashlib.sha256(
            transition_bytes
        ).hexdigest()
        files[f"transition-{execution_id}.json"] = transition_bytes
        model = _document(
            {
                "schema_version": 1,
                "contract": MODEL_CONTRACT,
                "issuer": issuer,
                "subject": subject,
                "execution_id": execution_id,
                "target": fact["target"],
                "model": fact["model"],
                "reasoning_effort": fact["reasoning_effort"],
                "capability": fact["model_capability"],
                "model_transition_sha256": transition_digests[execution_id],
            }
        )
        raw = _raw(model)
        model_raw[execution_id] = raw
        model_digests[execution_id] = hashlib.sha256(raw).hexdigest()
        files[f"model-{execution_id}.json"] = raw
        plan_dispatches.append(
            {
                key: fact[key]
                for key in (
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
                )
            }
            | {
                "model_transition_sha256": transition_digests[execution_id],
                "model_sha256": model_digests[execution_id],
                "authority": fact["authority"],
                "user_authority": fact["user_authority"],
                "isolation": fact["isolation"],
                "assurance": fact["assurance"],
                "assurance_minimum": fact["assurance_minimum"],
            }
        )
    plan = _document(
        {
            "schema_version": 1,
            "contract": PLAN_CONTRACT,
            "subject": subject,
            "plan_binding_sha256": plan_binding_sha256,
            "dispatches": plan_dispatches,
        }
    )
    plan_raw = _raw(plan)
    files["plan.json"] = plan_raw
    result_digests: dict[str, str] = {}
    for fact, dispatch in zip(facts, plan_dispatches, strict=True):
        execution_id = fact["execution_id"]
        result = _document(
            {
                "schema_version": 1,
                "contract": RESULT_CONTRACT,
                "issuer": issuer,
                "subject": subject,
                "execution_id": execution_id,
                "plan_sha256": hashlib.sha256(plan_raw).hexdigest(),
                "dispatch_sha256": _digest(dispatch),
                "model_sha256": model_digests[execution_id],
                "model_transition_sha256": transition_digests[execution_id],
                "request": fact["request"],
                "returned": fact["returned"],
                "verification": fact["verification"],
                "stop": fact["stop"],
                "target": fact["target"],
                "topology": fact["topology"],
                "assurance": fact["assurance"],
                "assurance_minimum": fact["assurance_minimum"],
                "user_authority": fact["user_authority"],
                "session": fact["isolation"]["session"],
                "context": fact["isolation"]["context"],
                "authority": fact["authority"],
                "before_candidate": fact["before_candidate"],
                "after_candidate": fact["after_candidate"],
                "usable": fact["usable"],
            }
        )
        raw = _raw(result)
        result_digests[execution_id] = hashlib.sha256(raw).hexdigest()
        files[f"result-{execution_id}.json"] = raw
    manifest = _document(
        {
            "schema_version": 1,
            "contract": BUNDLE_CONTRACT,
            "producer": producer,
            "subject": subject,
            "plan_sha256": hashlib.sha256(plan_raw).hexdigest(),
            "transitions": transition_digests,
            "models": model_digests,
            "results": result_digests,
        }
    )
    files["manifest.json"] = _raw(manifest)
    return files
