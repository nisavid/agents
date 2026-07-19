"""Provider compatibility with explicit blocked candidate state."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Sequence

from .inventory import DATA_CLASSES, PLACEMENTS, ROLES
from .model import deep_freeze, deep_thaw


class ProviderCompatibilityError(ValueError):
    pass


PROFILE_KEYS = {
    "id",
    "data_classes",
    "roles",
    "allowed_placements",
    "llm_failover",
}
PROVIDER_KEYS = {
    "id",
    "role",
    "placement",
    "data_classes",
    "transport",
    "tls",
    "credential",
    "readiness",
    "model",
    "contract",
    "state",
    "gates",
    "fallback",
}
ROLE_BINDING_KEYS = {"current", "desired"}
TRANSPORT_KEYS = {"protocol", "api"}
TLS_KEYS = {"server_name", "trust_roots"}
CREDENTIAL_KEYS = {"mode", "locator"}
READINESS_KEYS = {"ready", "version_compatible", "license_ready"}
MODEL_KEYS = {
    "artifact_id",
    "active_artifact_id",
    "revision",
    "active_revision",
    "reasoning_effort",
}
CONTRACT_KEYS = {
    "readiness_probe",
    "timeout_seconds",
    "no_payload_log",
    "api_compatible",
}
PROBE_KEYS = {"kind", "target"}
PROTOCOLS = {"https", "loopback"}
APIS = {
    "anthropic-messages",
    "openai-responses",
    "openai-compatible",
    "cohere-compatible",
}
CREDENTIAL_MODES = {"keychain", "oauth-home"}
SWITCH_KEYS = {
    "provider_id",
    "from_artifact_id",
    "from_revision",
    "to_artifact_id",
    "to_revision",
    "blue_green_rebuild",
    "approved",
}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")


def _closed(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ProviderCompatibilityError(
            f"{label} keys are closed (missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)})"
        )


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderCompatibilityError(f"{label} must be a non-empty string")
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ProviderCompatibilityError(
            f"{label} must be a bounded identifier"
        )
    return value


def _enum(value: Any, allowed: set[str], label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ProviderCompatibilityError(f"{label} is invalid")
    return value


@dataclass(frozen=True)
class CompatibilityResult:
    provider_id: str
    role: str
    state: str
    compatible: bool
    activatable: bool
    blocked_by: tuple[str, ...]
    fallback_provider_id: str | None
    placement: str
    artifact_id: str
    revision: str

    def __post_init__(self) -> None:
        _identifier(self.provider_id, "compatibility provider id")
        _enum(self.role, ROLES, "compatibility role")
        _enum(
            self.state,
            {
                "current",
                "fallback",
                "desired_candidate",
                "blocked_candidate",
                "incompatible",
            },
            "compatibility state",
        )
        if type(self.compatible) is not bool or type(self.activatable) is not bool:
            raise ProviderCompatibilityError(
                "compatibility flags must be boolean"
            )
        if not isinstance(self.blocked_by, tuple):
            raise ProviderCompatibilityError(
                "compatibility blocked_by must be a canonical tuple"
            )
        for gate in self.blocked_by:
            _identifier(gate, "compatibility blocked_by gate")
        if len(self.blocked_by) != len(set(self.blocked_by)):
            raise ProviderCompatibilityError(
                "compatibility blocked_by gates must be unique"
            )
        if self.fallback_provider_id is not None:
            _identifier(
                self.fallback_provider_id,
                "compatibility fallback provider id",
            )
        _enum(self.placement, PLACEMENTS, "compatibility placement")
        _identifier(self.artifact_id, "compatibility artifact id")
        _identifier(self.revision, "compatibility revision")

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "role": self.role,
            "state": self.state,
            "compatible": self.compatible,
            "activatable": self.activatable,
            "blocked_by": list(self.blocked_by),
            "fallback_provider_id": self.fallback_provider_id,
            "placement": self.placement,
            "artifact_id": self.artifact_id,
            "revision": self.revision,
        }


@dataclass(frozen=True)
class CompatibilityReport:
    profile_id: str
    role_bindings: Mapping[str, Mapping[str, str]]
    results: tuple[CompatibilityResult, ...]
    reranking_disposition: Mapping[str, Any]

    def __post_init__(self) -> None:
        _identifier(self.profile_id, "compatibility profile id")
        if not isinstance(self.role_bindings, Mapping):
            raise ProviderCompatibilityError(
                "compatibility role bindings must be an object"
            )
        for role, binding in self.role_bindings.items():
            _enum(role, ROLES, "compatibility role binding")
            if not isinstance(binding, Mapping):
                raise ProviderCompatibilityError(
                    "compatibility role binding must be an object"
                )
            for selection, provider_id in binding.items():
                _enum(
                    selection,
                    ROLE_BINDING_KEYS,
                    "compatibility role selection",
                )
                _identifier(
                    provider_id,
                    "compatibility role binding provider id",
                )
        if any(
            not isinstance(result, CompatibilityResult)
            for result in self.results
        ):
            raise ProviderCompatibilityError(
                "compatibility results must be CompatibilityResult objects"
            )
        disposition = self.reranking_disposition
        if not isinstance(disposition, Mapping) or set(disposition) != {
            "state",
            "provider_id",
            "visible_degradation",
        }:
            raise ProviderCompatibilityError(
                "reranking disposition keys are closed"
            )
        _enum(
            disposition["state"],
            {"current", "fallback", "disabled"},
            "reranking disposition state",
        )
        if disposition["provider_id"] is not None:
            _identifier(
                disposition["provider_id"],
                "reranking disposition provider id",
            )
        if type(disposition["visible_degradation"]) is not bool:
            raise ProviderCompatibilityError(
                "reranking disposition degradation flag must be boolean"
            )
        object.__setattr__(
            self, "role_bindings", deep_freeze(self.role_bindings)
        )
        object.__setattr__(self, "results", tuple(self.results))
        object.__setattr__(
            self,
            "reranking_disposition",
            deep_freeze(self.reranking_disposition),
        )

    def result(self, provider_id: str) -> CompatibilityResult:
        for result in self.results:
            if result.provider_id == provider_id:
                return result
        raise ProviderCompatibilityError(
            f"no compatibility result for provider {provider_id}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "role_bindings": deep_thaw(self.role_bindings),
            "results": [result.to_dict() for result in self.results],
            "reranking_disposition": deep_thaw(self.reranking_disposition),
        }


def _validate_provider(provider: Mapping[str, Any]) -> None:
    _closed(provider, PROVIDER_KEYS, "provider")
    provider_id = _identifier(provider["id"], "provider id")
    role = _enum(provider["role"], ROLES, f"provider {provider_id} role")
    placement = _enum(
        provider["placement"], PLACEMENTS, f"provider {provider_id} placement"
    )
    data_classes = provider["data_classes"]
    if (
        not isinstance(data_classes, list)
        or not data_classes
        or any(
            not isinstance(value, str) or value not in DATA_CLASSES
            for value in data_classes
        )
        or len(data_classes) != len(set(data_classes))
    ):
        raise ProviderCompatibilityError(
            f"provider {provider_id} data classes must be unique"
        )

    transport = provider["transport"]
    if not isinstance(transport, Mapping):
        raise ProviderCompatibilityError(
            f"provider {provider_id} transport must be an object"
        )
    _closed(transport, TRANSPORT_KEYS, "transport")
    protocol = _enum(
        transport["protocol"],
        PROTOCOLS,
        f"provider {provider_id} transport protocol",
    )
    _enum(
        transport["api"], APIS, f"provider {provider_id} transport API"
    )
    if placement == "local" and protocol != "loopback":
        raise ProviderCompatibilityError(
            f"local provider {provider_id} requires loopback transport"
        )
    if placement != "local" and protocol != "https":
        raise ProviderCompatibilityError(
            f"remote provider {provider_id} requires https transport"
        )

    tls = provider["tls"]
    if placement == "private-remote" and not isinstance(tls, Mapping):
        raise ProviderCompatibilityError(
            "private-remote provider "
            f"{provider_id} requires TLS identity and trust roots"
        )
    if tls is not None:
        if not isinstance(tls, Mapping):
            raise ProviderCompatibilityError(
                f"provider {provider_id} TLS policy must be null or an object"
            )
        if "server_name" not in tls:
            raise ProviderCompatibilityError(
                f"provider {provider_id} requires TLS identity"
            )
        if "trust_roots" not in tls:
            raise ProviderCompatibilityError(
                f"provider {provider_id} requires TLS trust roots"
            )
        _closed(tls, TLS_KEYS, "TLS")
        server_name = _nonempty(tls["server_name"], "TLS server identity")
        if not server_name.strip():
            raise ProviderCompatibilityError(
                "TLS server identity must be a non-empty string"
            )
        roots = tls["trust_roots"]
        if (
            not isinstance(roots, list)
            or not roots
            or any(
                not isinstance(root, str) or not root.strip()
                for root in roots
            )
        ):
            raise ProviderCompatibilityError(
                "provider "
                f"{provider_id} requires TLS identity and trust roots"
            )
    if placement == "private-remote":
        if protocol != "https":
            raise ProviderCompatibilityError(
                "private-remote provider "
                f"{provider_id} requires TLS identity over https"
            )

    credential = provider["credential"]
    if placement == "local":
        if credential is not None:
            if not isinstance(credential, Mapping):
                raise ProviderCompatibilityError(
                    f"provider {provider_id} credential must be a locator"
                )
            _closed(credential, CREDENTIAL_KEYS, "credential")
            _validate_credential(credential)
    else:
        if not isinstance(credential, Mapping):
            raise ProviderCompatibilityError(
                f"provider {provider_id} credential must be a locator "
                "without a value"
            )
        _closed(credential, CREDENTIAL_KEYS, "credential")
        _validate_credential(credential)

    readiness = provider["readiness"]
    if not isinstance(readiness, Mapping):
        raise ProviderCompatibilityError(
            f"provider {provider_id} readiness must be an object"
        )
    _closed(readiness, READINESS_KEYS, "readiness")
    if any(not isinstance(readiness[key], bool) for key in READINESS_KEYS):
        raise ProviderCompatibilityError(
            f"provider {provider_id} readiness gates must be boolean"
        )

    model = provider["model"]
    if not isinstance(model, Mapping):
        raise ProviderCompatibilityError(
            f"provider {provider_id} model must be an object"
        )
    _closed(model, MODEL_KEYS, "model")
    for key in MODEL_KEYS - {"reasoning_effort"}:
        _identifier(model[key], f"model {key}")
    effort = model["reasoning_effort"]
    if role == "llm":
        _nonempty(effort, "LLM reasoning effort")
    elif effort is not None:
        raise ProviderCompatibilityError(
            f"provider {provider_id} non-LLM reasoning effort must be null"
        )

    contract = provider["contract"]
    if not isinstance(contract, Mapping):
        raise ProviderCompatibilityError(
            f"provider {provider_id} contract must be an object"
        )
    _closed(contract, CONTRACT_KEYS, "provider contract")
    probe = contract["readiness_probe"]
    if not isinstance(probe, Mapping):
        raise ProviderCompatibilityError(
            f"provider {provider_id} requires a readiness probe"
        )
    _closed(probe, PROBE_KEYS, "readiness probe")
    _enum(
        probe["kind"],
        {"http", "process"},
        f"provider {provider_id} readiness probe kind",
    )
    _nonempty(probe["target"], "readiness probe target")
    timeout = contract["timeout_seconds"]
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise ProviderCompatibilityError(
            f"provider {provider_id} timeout must be from 1 to 300 seconds"
        )
    if contract["no_payload_log"] is not True:
        raise ProviderCompatibilityError(
            f"provider {provider_id} must guarantee no payload logging"
        )
    if contract["api_compatible"] is not True:
        raise ProviderCompatibilityError(
            f"provider {provider_id} API compatibility gate failed"
        )
    state = _enum(
        provider["state"],
        {"current", "desired", "fallback"},
        f"provider {provider_id} state",
    )
    gates = provider["gates"]
    if not isinstance(gates, Mapping):
        raise ProviderCompatibilityError(
            f"provider {provider_id} gates must map unique names to booleans"
        )
    for gate in gates:
        _identifier(gate, f"provider {provider_id} gate")
    if any(not isinstance(passed, bool) for passed in gates.values()):
        raise ProviderCompatibilityError(
            f"provider {provider_id} gates must map unique names to booleans"
        )
    if state == "desired" and not gates:
        raise ProviderCompatibilityError(
            f"provider {provider_id} must declare candidate gates"
        )
    if provider["fallback"] is not None:
        _identifier(provider["fallback"], "fallback provider id")


def _validate_credential(credential: Mapping[str, Any]) -> None:
    mode = _enum(credential["mode"], CREDENTIAL_MODES, "credential mode")
    locator = credential["locator"]
    expected_prefix = f"{mode}:"
    if (
        not isinstance(locator, str)
        or not locator.startswith(expected_prefix)
        or not re.fullmatch(
            rf"{re.escape(expected_prefix)}[a-z0-9][a-z0-9._-]*",
            locator,
        )
    ):
        raise ProviderCompatibilityError("credential locator shape is invalid")


def _switches_by_provider(
    revision_switches: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    switches: dict[str, Mapping[str, Any]] = {}
    for switch in revision_switches:
        if not isinstance(switch, Mapping):
            raise ProviderCompatibilityError(
                "revision switches must be objects"
            )
        _closed(switch, SWITCH_KEYS, "revision switch")
        provider_id = _identifier(
            switch["provider_id"], "revision switch provider"
        )
        if provider_id in switches:
            raise ProviderCompatibilityError(
                f"duplicate revision switch for {provider_id}"
            )
        for key in (
            "from_artifact_id",
            "from_revision",
            "to_artifact_id",
            "to_revision",
        ):
            _identifier(switch[key], f"revision switch {key}")
        if not isinstance(switch["blue_green_rebuild"], bool) or not isinstance(
            switch["approved"], bool
        ):
            raise ProviderCompatibilityError(
                "revision switch gates must be boolean"
            )
        switches[provider_id] = switch
    return switches


def _exact_switch(
    switch: Mapping[str, Any] | None,
    *,
    from_artifact_id: str,
    from_revision: str,
    to_artifact_id: str,
    to_revision: str,
    require_blue_green: bool,
) -> bool:
    if switch is None:
        return False
    return bool(
        switch["approved"]
        and (not require_blue_green or switch["blue_green_rebuild"])
        and switch["from_artifact_id"] == from_artifact_id
        and switch["from_revision"] == from_revision
        and switch["to_artifact_id"] == to_artifact_id
        and switch["to_revision"] == to_revision
    )


def validate_provider_compatibility(
    profile: Mapping[str, Any],
    providers: Sequence[Mapping[str, Any]],
    storage_state: Mapping[str, Any],
    *,
    revision_switches: Sequence[Mapping[str, Any]] = (),
) -> CompatibilityReport:
    if not isinstance(profile, Mapping):
        raise ProviderCompatibilityError("profile must be an object")
    _closed(profile, PROFILE_KEYS, "profile")
    profile_id = _identifier(profile["id"], "profile id")
    data_classes = profile["data_classes"]
    if (
        not isinstance(data_classes, list)
        or not data_classes
        or any(
            not isinstance(value, str) or value not in DATA_CLASSES
            for value in data_classes
        )
        or len(data_classes) != len(set(data_classes))
    ):
        raise ProviderCompatibilityError(
            "profile data_classes must be unique and non-empty"
        )
    roles = profile["roles"]
    if not isinstance(roles, Mapping) or set(roles) != ROLES:
        raise ProviderCompatibilityError(
            "profile must bind llm, embedding, and reranking independently"
        )
    allowed_placements = profile["allowed_placements"]
    if not isinstance(allowed_placements, Mapping) or set(
        allowed_placements
    ) != set(data_classes):
        raise ProviderCompatibilityError(
            "profile allowed_placements must define every data class"
        )
    for data_class, placements in allowed_placements.items():
        if (
            not isinstance(placements, list)
            or not placements
            or any(
                not isinstance(value, str) or value not in PLACEMENTS
                for value in placements
            )
        ):
            raise ProviderCompatibilityError(
                f"allowed placements for {data_class} are invalid"
            )
    llm_failover = profile["llm_failover"]
    if not isinstance(llm_failover, list):
        raise ProviderCompatibilityError(
            "llm_failover must be a unique ordered provider list"
        )
    for provider_id in llm_failover:
        _identifier(provider_id, "llm_failover provider")
    if len(llm_failover) != len(set(llm_failover)):
        raise ProviderCompatibilityError(
            "llm_failover must be a unique ordered provider list"
        )

    provider_by_id: dict[str, Mapping[str, Any]] = {}
    for provider in providers:
        if not isinstance(provider, Mapping):
            raise ProviderCompatibilityError("providers must be objects")
        _validate_provider(provider)
        if provider["id"] in provider_by_id:
            raise ProviderCompatibilityError(
                f"duplicate provider id: {provider['id']}"
            )
        provider_by_id[provider["id"]] = provider
    for provider in providers:
        fallback_id = provider["fallback"]
        if fallback_id is None:
            continue
        fallback = provider_by_id.get(fallback_id)
        if (
            provider["role"] != "reranking"
            or fallback is None
            or fallback["role"] != "reranking"
            or fallback["state"] != "fallback"
        ):
            raise ProviderCompatibilityError(
                f"reranker fallback for {provider['id']} must reference a "
                "reranking provider in fallback state"
            )
    for provider in providers:
        seen: set[str] = set()
        current = provider
        while current["fallback"] is not None:
            if current["id"] in seen:
                raise ProviderCompatibilityError("provider fallback graph contains a cycle")
            seen.add(current["id"])
            current = provider_by_id[current["fallback"]]
        if current["id"] in seen:
            raise ProviderCompatibilityError("provider fallback graph contains a cycle")

    selected: list[Mapping[str, Any]] = []
    normalized_bindings: dict[str, dict[str, str]] = {}
    for role in sorted(ROLES):
        binding = roles[role]
        if (
            not isinstance(binding, Mapping)
            or not set(binding).issubset(ROLE_BINDING_KEYS)
            or "current" not in binding
        ):
            raise ProviderCompatibilityError(
                f"role {role} binding must have current and optional "
                "desired provider"
            )
        normalized_bindings[role] = {
            selection: binding[selection]
            for selection in ("current", "desired")
            if selection in binding
        }
        for selection, provider_id in normalized_bindings[role].items():
            _identifier(
                provider_id, f"role {role} {selection} provider"
            )
            if provider_id not in provider_by_id:
                raise ProviderCompatibilityError(
                    f"role {role} references unknown provider {provider_id}"
                )
            provider = provider_by_id[provider_id]
            if provider["role"] != role:
                raise ProviderCompatibilityError(
                    f"provider {provider_id} cannot serve role {role}"
                )
            if selection == "current" and provider["state"] not in {
                "current",
                "fallback",
            }:
                raise ProviderCompatibilityError(
                    f"current role {role} must reference current provider state"
                )
            if selection == "desired" and provider["state"] != "desired":
                raise ProviderCompatibilityError(
                    f"desired role {role} must reference desired provider state"
                )
            if provider not in selected:
                selected.append(provider)

    for provider_id in llm_failover:
        provider = provider_by_id.get(provider_id)
        if provider is None or provider["role"] != "llm":
            raise ProviderCompatibilityError(
                "llm_failover must reference declared LLM providers"
            )
        if provider not in selected:
            selected.append(provider)
    selected_index = 0
    while selected_index < len(selected):
        provider = selected[selected_index]
        selected_index += 1
        fallback_id = provider["fallback"]
        if fallback_id is not None:
            fallback = provider_by_id[fallback_id]
            if fallback not in selected:
                selected.append(fallback)
    switches = _switches_by_provider(revision_switches)
    selected_ids = {provider["id"] for provider in selected}
    for provider_id in switches:
        if provider_id not in selected_ids:
            raise ProviderCompatibilityError(
                f"revision switch references unselected provider {provider_id}"
            )
    if not isinstance(storage_state, Mapping) or set(storage_state) != {
        "populated",
        "embedding_artifact_id",
        "embedding_revision",
    }:
        raise ProviderCompatibilityError("storage state keys are closed")
    if not isinstance(storage_state["populated"], bool):
        raise ProviderCompatibilityError("storage populated must be boolean")
    _identifier(
        storage_state["embedding_artifact_id"], "storage embedding artifact"
    )
    _identifier(
        storage_state["embedding_revision"], "storage embedding revision"
    )
    for provider_id in switches:
        provider = provider_by_id[provider_id]
        model = provider["model"]
        active_identity = (
            storage_state["embedding_artifact_id"],
            storage_state["embedding_revision"],
        ) if provider["role"] == "embedding" and storage_state["populated"] else (
            model["active_artifact_id"], model["active_revision"]
        )
        if active_identity == (model["artifact_id"], model["revision"]):
            raise ProviderCompatibilityError(
                f"revision switch for {provider_id} has no target drift"
            )

    results: list[CompatibilityResult] = []
    for provider in selected:
        provider_id = provider["id"]
        role = provider["role"]
        placement = provider["placement"]
        blocked: list[str] = []
        for data_class in data_classes:
            if data_class not in provider["data_classes"]:
                raise ProviderCompatibilityError(
                    f"provider {provider_id} cannot receive {data_class} data"
                )
            if placement not in allowed_placements[data_class]:
                raise ProviderCompatibilityError(
                    f"provider {provider_id} placement is forbidden for "
                    f"{data_class}"
                )
        for gate in ("ready", "version_compatible", "license_ready"):
            if not provider["readiness"][gate]:
                blocked.append(gate)
        blocked.extend(
            gate
            for gate, passed in sorted(provider["gates"].items())
            if not passed
        )

        model = provider["model"]
        switch = switches.get(provider_id)
        from_artifact = model["active_artifact_id"]
        from_revision = model["active_revision"]
        if role == "embedding" and storage_state["populated"]:
            from_artifact = storage_state["embedding_artifact_id"]
            from_revision = storage_state["embedding_revision"]
        identity_changed = (
            model["artifact_id"] != from_artifact
            or model["revision"] != from_revision
        )
        if identity_changed:
            if not _exact_switch(
                switch,
                from_artifact_id=from_artifact,
                from_revision=from_revision,
                to_artifact_id=model["artifact_id"],
                to_revision=model["revision"],
                require_blue_green=False,
            ):
                blocked.append("revision_switch_not_approved")
        if role == "embedding" and storage_state["populated"]:
            if identity_changed and not _exact_switch(
                switch,
                from_artifact_id=storage_state["embedding_artifact_id"],
                from_revision=storage_state["embedding_revision"],
                to_artifact_id=model["artifact_id"],
                to_revision=model["revision"],
                require_blue_green=True,
            ):
                blocked.append("embedding_identity_immutable")

        blocked_tuple = tuple(dict.fromkeys(blocked))
        if blocked_tuple:
            state = (
                "blocked_candidate"
                if provider["state"] == "desired"
                else "incompatible"
            )
        else:
            state = (
                provider["state"]
                if provider["state"] in {"current", "fallback"}
                else "desired_candidate"
            )
        results.append(
            CompatibilityResult(
                provider_id=provider_id,
                role=role,
                state=state,
                compatible=not blocked_tuple,
                activatable=not blocked_tuple,
                blocked_by=blocked_tuple,
                fallback_provider_id=provider["fallback"],
                placement=placement,
                artifact_id=model["artifact_id"],
                revision=model["revision"],
            )
        )

    result_by_id = {result.provider_id: result for result in results}
    reranking_binding = normalized_bindings["reranking"]
    current_reranker = result_by_id[reranking_binding["current"]]
    desired_id = reranking_binding.get("desired")
    desired_reranker = (
        result_by_id.get(desired_id) if desired_id is not None else None
    )

    def activatable_fallback(
        result: CompatibilityResult,
    ) -> CompatibilityResult | None:
        seen = {result.provider_id}
        fallback_id = result.fallback_provider_id
        while fallback_id is not None and fallback_id not in seen:
            seen.add(fallback_id)
            fallback = result_by_id.get(fallback_id)
            if fallback is None:
                return None
            if fallback.activatable:
                return fallback
            fallback_id = fallback.fallback_provider_id
        return None

    if current_reranker.activatable:
        reranking_disposition = {
            "state": current_reranker.state,
            "provider_id": current_reranker.provider_id,
            "visible_degradation": current_reranker.state == "fallback",
        }
    elif (fallback := activatable_fallback(current_reranker)) is not None:
        reranking_disposition = {
            "state": "fallback",
            "provider_id": fallback.provider_id,
            "visible_degradation": True,
        }
    else:
        reranking_disposition = {
            "state": "disabled",
            "provider_id": None,
            "visible_degradation": True,
        }

    return CompatibilityReport(
        profile_id=profile_id,
        role_bindings=normalized_bindings,
        results=tuple(results),
        reranking_disposition=reranking_disposition,
    )
