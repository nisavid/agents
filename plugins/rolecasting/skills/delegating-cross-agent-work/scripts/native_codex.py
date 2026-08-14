"""Sequence skill-mediated native Codex child dispatches.

This module has no harness-tool actuator. The owning skill calls the current
harness's native subagent tool between :func:`freeze_native_dispatch` and
:func:`record_native_observation`. The latter accepts same-leader,
host-protocol-shaped observations; it does not authenticate portable
provenance, inspect worker output for host facts, or issue Task Witness
evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, NamedTuple

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
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
_ASSURANCE_FIELDS = (
    "target",
    "model",
    "topology",
    "authority",
    "execution_result",
)
_TERMINAL_STATUSES = {"completed", "failed", "timed-out", "cancelled"}
_STATUSES = {"pending", "running"} | _TERMINAL_STATUSES


class NativeDispatchError(ValueError):
    """A native dispatch cannot preserve its frozen contract."""


class Target(NamedTuple):
    product_family: str
    surface: str
    version: str
    executor: str


class Topology(NamedTuple):
    relationship: str
    ownership: str
    transport: str


class AuthorityIntent(NamedTuple):
    access: str
    subdelegation: bool
    external_action: bool


class Assurance(NamedTuple):
    target: str
    model: str
    topology: str
    authority: str
    execution_result: str


class _NativeProfile(NamedTuple):
    adapter_id: str
    product_family: str
    surface: str
    executor: str
    maximum_assurance: Assurance


class FrozenNativeDispatch(NamedTuple):
    binding_sha256: str
    plan_sha256: str
    request_sha256: str
    dispatch_id: str
    target: Target
    topology: Topology
    context: str
    authority_intent: AuthorityIntent
    assurance_minimum: Assurance
    profile_maximum_assurance: Assurance
    adapter_id: str


class NativeDispatchRecord(NamedTuple):
    binding_sha256: str
    plan_sha256: str
    request_sha256: str
    dispatch_id: str
    target: Target
    topology: Topology
    context: str
    authority_intent: AuthorityIntent
    assurance_minimum: Assurance
    observed_assurance: Assurance
    agent_id: str
    session_id: str
    launch_acknowledgement_sha256: str
    status_observations: tuple[tuple[str, str], ...]
    terminal_status: str
    result_sha256: str
    verification_observation_sha256: str
    usable: bool
    portable_evidence: bool
    product_attested: bool


_NATIVE_MAXIMUM = Assurance(
    target="controller-observed",
    model="self-reported",
    topology="controller-observed",
    authority="self-reported",
    execution_result="controller-observed",
)
_PROFILES = {
    "chatgpt-codex": _NativeProfile(
        adapter_id="chatgpt-codex-native-subagent",
        product_family="codex",
        surface="chatgpt-codex",
        executor="codex",
        maximum_assurance=_NATIVE_MAXIMUM,
    ),
    "codex-cli-tui": _NativeProfile(
        adapter_id="codex-cli-tui-native-subagent",
        product_family="codex",
        surface="codex-cli-tui",
        executor="codex",
        maximum_assurance=_NATIVE_MAXIMUM,
    ),
}


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
        raise NativeDispatchError("native dispatch is not canonical JSON") from error


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise NativeDispatchError(f"{label} schema drift")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise NativeDispatchError(f"{label} must be a non-empty string")
    return value


def _token(value: Any, label: str) -> str:
    value = _text(value, label)
    if _TOKEN.fullmatch(value) is None:
        raise NativeDispatchError(f"{label} is not a closed token")
    return value


def _sha(value: Any, label: str) -> str:
    value = _text(value, label)
    if _HEX.fullmatch(value) is None:
        raise NativeDispatchError(f"{label} must be a SHA-256 digest")
    return value


def _assurance(value: Any, label: str) -> Assurance:
    value = _exact(value, set(_ASSURANCE_FIELDS), label)
    levels = []
    for field in _ASSURANCE_FIELDS:
        level = _token(value[field], f"{label}.{field}")
        if level not in _ASSURANCE_LEVELS:
            raise NativeDispatchError(f"{label}.{field} is invalid")
        levels.append(level)
    return Assurance(*levels)


def _authority_intent(value: Any) -> AuthorityIntent:
    value = _exact(
        value,
        {"access", "subdelegation", "external_action"},
        "authority intent",
    )
    if (
        value["access"] != "read-only"
        or value["subdelegation"] is not False
        or value["external_action"] is not False
    ):
        raise NativeDispatchError(
            "native child authority intent must be read-only with onward "
            "authority denied"
        )
    return AuthorityIntent(
        access="read-only",
        subdelegation=False,
        external_action=False,
    )


def _frozen_payload(frozen: FrozenNativeDispatch) -> dict[str, Any]:
    return {
        "plan_sha256": frozen.plan_sha256,
        "request_sha256": frozen.request_sha256,
        "dispatch_id": frozen.dispatch_id,
        "target": frozen.target._asdict(),
        "topology": frozen.topology._asdict(),
        "context": frozen.context,
        "authority_intent": frozen.authority_intent._asdict(),
        "assurance_minimum": frozen.assurance_minimum._asdict(),
        "profile_maximum_assurance": frozen.profile_maximum_assurance._asdict(),
        "adapter_id": frozen.adapter_id,
    }


def _validate_frozen(frozen: Any) -> FrozenNativeDispatch:
    if not isinstance(frozen, FrozenNativeDispatch):
        raise NativeDispatchError("native dispatch must be frozen before recording")
    try:
        _sha(frozen.binding_sha256, "frozen native dispatch.binding_sha256")
        _sha(frozen.plan_sha256, "frozen native dispatch.plan_sha256")
        _sha(frozen.request_sha256, "frozen native dispatch.request_sha256")
        _token(frozen.dispatch_id, "frozen native dispatch.dispatch_id")
        _text(frozen.context, "frozen native dispatch.context")
        if not isinstance(frozen.target, Target):
            raise NativeDispatchError("target type drift")
        if not isinstance(frozen.topology, Topology):
            raise NativeDispatchError("topology type drift")
        if not isinstance(frozen.authority_intent, AuthorityIntent):
            raise NativeDispatchError("authority intent type drift")
        if not isinstance(frozen.assurance_minimum, Assurance):
            raise NativeDispatchError("assurance minimum type drift")
        if not isinstance(frozen.profile_maximum_assurance, Assurance):
            raise NativeDispatchError("profile assurance type drift")

        try:
            profile = _PROFILES[frozen.target.surface]
        except KeyError as error:
            raise NativeDispatchError("target surface is unsupported") from error
        expected_target = Target(
            product_family=profile.product_family,
            surface=profile.surface,
            version=_text(frozen.target.version, "frozen native target.version"),
            executor=profile.executor,
        )
        if frozen.target != expected_target:
            raise NativeDispatchError("target does not match native profile")
        if frozen.topology != Topology(
            relationship="child",
            ownership="leader-owned",
            transport="native-tool",
        ):
            raise NativeDispatchError("topology does not match native profile")
        if frozen.authority_intent != AuthorityIntent(
            access="read-only",
            subdelegation=False,
            external_action=False,
        ):
            raise NativeDispatchError("authority intent exceeds native profile")
        minimum = _assurance(
            frozen.assurance_minimum._asdict(),
            "frozen native dispatch.assurance_minimum",
        )
        maximum = _assurance(
            frozen.profile_maximum_assurance._asdict(),
            "frozen native dispatch.profile_maximum_assurance",
        )
        if maximum != profile.maximum_assurance:
            raise NativeDispatchError("profile assurance does not match native profile")
        for field in _ASSURANCE_FIELDS:
            if (
                _ASSURANCE_RANK[getattr(minimum, field)]
                > _ASSURANCE_RANK[getattr(maximum, field)]
            ):
                raise NativeDispatchError("assurance minimum exceeds native profile")
        if frozen.adapter_id != profile.adapter_id:
            raise NativeDispatchError("adapter does not match native profile")
        if _digest(_frozen_payload(frozen)) != frozen.binding_sha256:
            raise NativeDispatchError("binding digest mismatch")
    except NativeDispatchError as error:
        raise NativeDispatchError(
            f"frozen native dispatch is invalid: {error}"
        ) from error
    return frozen


def freeze_native_dispatch(intent: Any) -> FrozenNativeDispatch:
    """Freeze one complete plan identity before native subagent invocation.

    The native profile maximum is checked here, before the owning skill may
    spawn anything. Requested authority remains intent, not an observation of
    effective product enforcement.
    """

    intent = _exact(
        intent,
        {
            "plan_sha256",
            "request_sha256",
            "dispatch_id",
            "surface",
            "version",
            "executor",
            "context",
            "authority_intent",
            "assurance_minimum",
        },
        "native dispatch intent",
    )
    surface = _token(intent["surface"], "native dispatch intent.surface")
    try:
        profile = _PROFILES[surface]
    except KeyError as error:
        raise NativeDispatchError("native Codex surface is unsupported") from error

    plan_sha256 = _sha(intent["plan_sha256"], "native dispatch intent.plan_sha256")
    request_sha256 = _sha(
        intent["request_sha256"], "native dispatch intent.request_sha256"
    )
    dispatch_id = _token(intent["dispatch_id"], "native dispatch intent.dispatch_id")
    executor = _text(intent["executor"], "native dispatch intent.executor")
    if executor != profile.executor:
        raise NativeDispatchError("executor does not match native profile")
    target = Target(
        product_family=profile.product_family,
        surface=profile.surface,
        version=_text(intent["version"], "native dispatch intent.version"),
        executor=profile.executor,
    )
    topology = Topology(
        relationship="child",
        ownership="leader-owned",
        transport="native-tool",
    )
    context = _text(intent["context"], "native dispatch intent.context")
    authority_intent = _authority_intent(intent["authority_intent"])
    minimum = _assurance(
        intent["assurance_minimum"], "native dispatch intent.assurance_minimum"
    )
    for field in _ASSURANCE_FIELDS:
        if (
            _ASSURANCE_RANK[getattr(minimum, field)]
            > _ASSURANCE_RANK[getattr(profile.maximum_assurance, field)]
        ):
            raise NativeDispatchError(
                f"{field} assurance minimum exceeds native profile"
            )

    frozen = FrozenNativeDispatch(
        binding_sha256="",
        plan_sha256=plan_sha256,
        request_sha256=request_sha256,
        dispatch_id=dispatch_id,
        target=target,
        topology=topology,
        context=context,
        authority_intent=authority_intent,
        assurance_minimum=minimum,
        profile_maximum_assurance=profile.maximum_assurance,
        adapter_id=profile.adapter_id,
    )
    return frozen._replace(binding_sha256=_digest(_frozen_payload(frozen)))


def record_native_observation(
    frozen: FrozenNativeDispatch,
    observation: Any,
) -> NativeDispatchRecord:
    """Bind post-result native tool observations to one frozen dispatch.

    Host-shaped values are trusted only as the same leader's live controller
    observations. The function never parses model-generated text for agent,
    session, context, launch, status, or result provenance.
    """

    frozen = _validate_frozen(frozen)
    observation = _exact(
        observation,
        {
            "binding_sha256",
            "plan_sha256",
            "request_sha256",
            "dispatch_id",
            "agent_id",
            "session_id",
            "context",
            "launch_acknowledgement_sha256",
            "status_observations",
            "result_sha256",
            "verification_observation_sha256",
            "usable",
        },
        "native observation",
    )
    bound_values = {
        "binding_sha256": frozen.binding_sha256,
        "plan_sha256": frozen.plan_sha256,
        "request_sha256": frozen.request_sha256,
        "dispatch_id": frozen.dispatch_id,
        "context": frozen.context,
    }
    for field, expected in bound_values.items():
        observed = _text(observation[field], f"native observation.{field}")
        if observed != expected:
            raise NativeDispatchError("native observation is cross-bound")

    agent_id = _text(observation["agent_id"], "native observation.agent_id")
    session_id = _text(observation["session_id"], "native observation.session_id")
    launch_sha256 = _sha(
        observation["launch_acknowledgement_sha256"],
        "native observation.launch_acknowledgement_sha256",
    )
    raw_statuses = observation["status_observations"]
    if not isinstance(raw_statuses, (list, tuple)) or not raw_statuses:
        raise NativeDispatchError("native status observations must be non-empty")
    statuses: list[tuple[str, str]] = []
    for index, item in enumerate(raw_statuses):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise NativeDispatchError("native status observation schema drift")
        status = _token(item[0], f"native status observation {index}.status")
        if status not in _STATUSES:
            raise NativeDispatchError("native status observation is invalid")
        if index < len(raw_statuses) - 1 and status in _TERMINAL_STATUSES:
            raise NativeDispatchError("native status terminal is out of order")
        statuses.append(
            (
                status,
                _sha(
                    item[1],
                    f"native status observation {index}.content_sha256",
                ),
            )
        )
    terminal_status = statuses[-1][0]
    if terminal_status not in _TERMINAL_STATUSES:
        raise NativeDispatchError("native status observations lack a terminal")
    result_sha256 = _sha(
        observation["result_sha256"], "native observation.result_sha256"
    )
    verification_sha256 = _sha(
        observation["verification_observation_sha256"],
        "native observation.verification_observation_sha256",
    )
    usable = observation["usable"]
    if type(usable) is not bool:
        raise NativeDispatchError("native observation.usable must be a strict Boolean")
    if terminal_status != "completed" and usable:
        raise NativeDispatchError("non-completed native result cannot be usable")

    return NativeDispatchRecord(
        binding_sha256=frozen.binding_sha256,
        plan_sha256=frozen.plan_sha256,
        request_sha256=frozen.request_sha256,
        dispatch_id=frozen.dispatch_id,
        target=frozen.target,
        topology=frozen.topology,
        context=frozen.context,
        authority_intent=frozen.authority_intent,
        assurance_minimum=frozen.assurance_minimum,
        observed_assurance=frozen.profile_maximum_assurance,
        agent_id=agent_id,
        session_id=session_id,
        launch_acknowledgement_sha256=launch_sha256,
        status_observations=tuple(statuses),
        terminal_status=terminal_status,
        result_sha256=result_sha256,
        verification_observation_sha256=verification_sha256,
        usable=usable,
        portable_evidence=False,
        product_attested=False,
    )


__all__ = [
    "NativeDispatchError",
    "freeze_native_dispatch",
    "record_native_observation",
]
