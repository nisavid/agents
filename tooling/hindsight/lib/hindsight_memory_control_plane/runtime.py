"""Inventory-compiled production runtime routes for the capability broker."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hmac
import logging
import re
from types import MappingProxyType
from typing import Any

from .broker import (
    CAPABILITY_METHODS,
    MAX_SESSION_TTL_SECONDS,
    MINT_REQUEST_KEYS,
)
from .canonical import digest
from .http_adapter import HttpAdapter
from .model import Inventory, deep_freeze, deep_thaw
from .planning import inventory_endpoint


LOGGER = logging.getLogger(__name__)
ENVIRONMENT_RESOLVER_ID = re.compile(r"[A-Z_][A-Z0-9_]{0,127}", re.ASCII)

HARNESS_METHODS = {
    # The controller policy intentionally exposes the same bounded memory
    # methods to each supported bridge. Harness-specific payload parsing stays
    # outside the broker and cannot expand this capability set.
    harness_id: tuple(sorted(CAPABILITY_METHODS))
    for harness_id in ("claude-code", "codex", "cursor")
}


class RuntimeConfigurationError(ValueError):
    """A production broker binding is incomplete or contradicts inventory."""


@dataclass(frozen=True)
class RuntimeConfiguration:
    routes: Mapping[str, Mapping[str, Any]]
    policy_digest: str
    artifact_digest: str
    route_digest: str
    profile_set_digest: str
    methods: tuple[str, ...]
    mint_authorizer: Callable[[str, Mapping[str, Any], float], Mapping[str, Any]]
    status: Mapping[str, Any]


def _validate_runtime_inputs(
    inventory: Inventory,
    profiles: tuple[str, ...] | list[str],
    token_resolver_id: str,
    mint_authority_resolver_id: str,
) -> tuple[str, ...]:
    if not isinstance(inventory, Inventory):
        raise RuntimeConfigurationError("validated inventory is required")
    if any(
        not isinstance(locator, str)
        or ENVIRONMENT_RESOLVER_ID.fullmatch(locator) is None
        for locator in (token_resolver_id, mint_authority_resolver_id)
    ):
        raise RuntimeConfigurationError("runtime resolver locator is invalid")
    if hmac.compare_digest(
        token_resolver_id.encode("utf-8"),
        mint_authority_resolver_id.encode("utf-8"),
    ):
        raise RuntimeConfigurationError(
            "data-plane and mint-authority resolver locators must be distinct"
        )
    if not isinstance(profiles, (tuple, list)) or not profiles:
        raise RuntimeConfigurationError(
            "at least one selected profile is required"
        )
    if any(not isinstance(profile, str) or not profile for profile in profiles):
        raise RuntimeConfigurationError("selected profile is invalid")
    selected_profiles = tuple(sorted(set(profiles)))
    declared_profiles = {
        profile["id"]: profile for profile in inventory.profiles
        if profile.get("enabled", True) is True
    }
    if any(profile not in declared_profiles for profile in selected_profiles):
        raise RuntimeConfigurationError(
            "selected profile is not enabled in inventory"
        )
    return selected_profiles


def _compile_route_specs(
    inventory: Inventory,
    selected_profiles: tuple[str, ...],
) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]],
    dict[str, tuple[str, ...]], tuple[str, ...],
]:
    harnesses = sorted(
        (
            deep_thaw(harness) for harness in inventory.harnesses
            if harness["profile_id"] in selected_profiles
        ),
        key=lambda harness: harness["id"],
    )
    if not harnesses:
        raise RuntimeConfigurationError(
            "selected profiles declare no harness routes"
        )
    route_specs: list[dict[str, Any]] = []
    route_bindings: list[dict[str, Any]] = []
    method_sets: dict[str, tuple[str, ...]] = {}
    seen_routes: set[str] = set()
    declared_banks = {
        (bank["profile_id"], bank["id"]): bank
        for bank in inventory.banks
    }
    for harness in harnesses:
        home_bank = harness["home_bank"]
        if (
            home_bank != harness["write_bank"]
            or home_bank["profile_id"] != harness["profile_id"]
        ):
            raise RuntimeConfigurationError(
                "runtime harness home and write banks must be identical and profile-bound"
            )
        bank = declared_banks.get(
            (home_bank["profile_id"], home_bank["bank_id"])
        )
        if (
            bank is None
            or home_bank["bank_id"] != "engineering"
            or bank["data_class"] != "engineering"
            or bank["writable"] is not True
        ):
            raise RuntimeConfigurationError(
                "runtime harness writes require the canonical engineering bank"
            )
        route_id = harness["id"]
        if route_id in seen_routes:
            raise RuntimeConfigurationError(
                "duplicate harness route across selected profiles"
            )
        seen_routes.add(route_id)
        allowed = HARNESS_METHODS.get(harness["id"])
        if allowed is None:
            raise RuntimeConfigurationError(
                "runtime harness adapter is unsupported"
            )
        canonical_bank = {
            **home_bank,
            "endpoint": inventory_endpoint(
                inventory, harness["profile_id"]
            ).to_dict(),
        }
        route_spec = {
            "route": route_id,
            "harness_id": harness["id"],
            "bank": canonical_bank,
            "methods": list(allowed),
        }
        route_specs.append(route_spec)
        method_sets[route_id] = allowed
        route_bindings.append({
            "route": route_id,
            "harness_id": harness["id"],
            "profile_id": harness["profile_id"],
            "bank": canonical_bank,
        })
    methods = tuple(sorted(set().union(*map(set, method_sets.values()))))
    return route_specs, route_bindings, method_sets, methods


def _construct_routes(
    inventory: Inventory,
    route_bindings: list[dict[str, Any]],
    token_resolver: Callable[[], str],
    adapter_factory: Callable[..., Any],
    verify_adapters: bool,
) -> dict[str, Mapping[str, Any]]:
    routes: dict[str, Mapping[str, Any]] = {}
    for binding in route_bindings:
        adapter = adapter_factory(
            inventory=inventory,
            profile_id=binding["profile_id"],
            token_resolver=token_resolver,
            runtime_bank_id=binding["bank"]["bank_id"],
            runtime_harness_id=binding["harness_id"],
        )
        if verify_adapters:
            verifier = getattr(adapter, "verify_runtime_compatibility", None)
            if not callable(verifier):
                raise RuntimeConfigurationError(
                    "runtime adapter compatibility gate is unavailable"
                )
            verifier()
        routes[binding["route"]] = MappingProxyType({
            "bank": deep_freeze(binding["bank"]), "adapter": adapter,
        })
    return routes


def _compile_digests(
    inventory: Inventory,
    selected_profiles: tuple[str, ...],
    route_specs: list[dict[str, Any]],
    token_resolver_id: str,
    mint_authority_resolver_id: str,
) -> tuple[str, str, str, str]:
    route_digest = digest({"routes": route_specs})
    policy_digest = digest({
        "policy": deep_thaw(inventory.policy),
        "route_digest": route_digest,
    })
    artifact_digest = inventory.artifact_digest
    profile_set_digest = digest({
        "mode": "active",
        "profiles": list(selected_profiles),
        "inventory_digest": inventory.inventory_digest,
        "route_digest": route_digest,
        "resolver_locators": {
            "data_plane_token": token_resolver_id,
            "mint_authority": mint_authority_resolver_id,
        },
    })
    return (
        route_digest, policy_digest, artifact_digest, profile_set_digest,
    )


def _runtime_status(
    selected_profiles: tuple[str, ...],
    route_specs: list[dict[str, Any]],
    *, policy_digest: str, artifact_digest: str, route_digest: str,
    profile_set_digest: str,
) -> Mapping[str, Any]:
    return deep_freeze({
        "mode": "active",
        "profiles": list(selected_profiles),
        "routes": [item["route"] for item in route_specs],
        "policy_digest": policy_digest,
        "artifact_digest": artifact_digest,
        "route_digest": route_digest,
        "profile_set_digest": profile_set_digest,
    })


def compile_runtime_status(
    *,
    inventory: Inventory,
    profiles: tuple[str, ...] | list[str],
    token_resolver_id: str,
    mint_authority_resolver_id: str,
) -> Mapping[str, Any]:
    """Compile active status and digests without constructing adapters."""

    selected_profiles = _validate_runtime_inputs(
        inventory, profiles, token_resolver_id, mint_authority_resolver_id
    )
    route_specs, _bindings, _method_sets, _methods = _compile_route_specs(
        inventory, selected_profiles
    )
    (
        route_digest, policy_digest, artifact_digest, profile_set_digest,
    ) = _compile_digests(
        inventory, selected_profiles, route_specs, token_resolver_id,
        mint_authority_resolver_id,
    )
    return _runtime_status(
        selected_profiles, route_specs,
        policy_digest=policy_digest, artifact_digest=artifact_digest,
        route_digest=route_digest, profile_set_digest=profile_set_digest,
    )


def compile_runtime_configuration(
    *,
    inventory: Inventory,
    profiles: tuple[str, ...] | list[str],
    token_resolver: Callable[[], str],
    mint_authority_resolver: Callable[[], str],
    token_resolver_id: str,
    mint_authority_resolver_id: str,
    adapter_factory: Callable[..., Any] = HttpAdapter,
    verify_adapters: bool = False,
) -> RuntimeConfiguration:
    """Compile immutable route authority from one validated inventory."""

    if not callable(token_resolver) or not callable(mint_authority_resolver):
        raise RuntimeConfigurationError("runtime resolvers must be callable")
    selected_profiles = _validate_runtime_inputs(
        inventory, profiles, token_resolver_id, mint_authority_resolver_id
    )
    route_specs, route_bindings, method_sets, methods = _compile_route_specs(
        inventory, selected_profiles
    )
    routes = _construct_routes(
        inventory, route_bindings, token_resolver, adapter_factory,
        verify_adapters,
    )
    (
        route_digest, policy_digest, artifact_digest, profile_set_digest,
    ) = _compile_digests(
        inventory, selected_profiles, route_specs, token_resolver_id,
        mint_authority_resolver_id,
    )
    route_by_id = {item["route"]: item for item in route_specs}

    def authorize_mint(
        control_capability: str,
        requested: Mapping[str, Any],
        ttl_seconds: float,
    ) -> Mapping[str, Any]:
        try:
            authority = mint_authority_resolver()
        except Exception as error:
            LOGGER.warning(
                "mint authority resolver failed closed (%s)",
                type(error).__name__,
            )
            return {}
        if (
            not isinstance(authority, str)
            or not authority
            or not isinstance(control_capability, str)
            or not authority.isascii()
            or not control_capability.isascii()
            or not hmac.compare_digest(authority, control_capability)
            or type(ttl_seconds) not in (int, float)
            or not 0 < ttl_seconds <= MAX_SESSION_TTL_SECONDS
            or not isinstance(requested, Mapping)
            or set(requested) != MINT_REQUEST_KEYS
            or any(
                not isinstance(requested[key], str) or not requested[key]
                for key in MINT_REQUEST_KEYS
            )
        ):
            return {}
        route = route_by_id.get(requested.get("route"))
        if route is None:
            return {}
        return {
            "session_id": requested["session_id"],
            "harness_id": route["harness_id"],
            "home_bank": deep_thaw(route["bank"]),
            "trust_class": "local",
            "companion_id": requested["companion_id"],
            "policy_digest": policy_digest,
            "artifact_digest": artifact_digest,
            "methods": list(method_sets[route["route"]]),
            "route": route["route"],
        }

    status = _runtime_status(
        selected_profiles, route_specs,
        policy_digest=policy_digest, artifact_digest=artifact_digest,
        route_digest=route_digest, profile_set_digest=profile_set_digest,
    )
    return RuntimeConfiguration(
        routes=MappingProxyType(dict(routes)),
        policy_digest=policy_digest,
        artifact_digest=artifact_digest,
        route_digest=route_digest,
        profile_set_digest=profile_set_digest,
        methods=methods,
        mint_authorizer=authorize_mint,
        status=status,
    )
