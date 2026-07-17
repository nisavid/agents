"""Closed-schema inventory loading and cross-reference validation."""

from pathlib import Path
import re
from typing import Any, Mapping

from .canonical import digest, strict_json_loads
from .endpoint_host import canonical_endpoint_host, is_bare_endpoint_host
from .model import Inventory


ROOT_KEYS = {
    "schema_version",
    "machine",
    "archetype",
    "profiles",
    "providers",
    "banks",
    "harnesses",
    "migration",
    "policy",
}
ROLES = {"llm", "embedding", "reranking"}
PLACEMENTS = {"local", "third-party-hosted", "private-remote"}
DATA_CLASSES = {"engineering", "personal", "airlock"}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
RECORD_KEYS = {
    "machine": {"id", "base_port", "engineering_memory_enabled"},
    "archetype": {"id"},
    "profiles": {"id", "slot", "port", "enabled", "scheme", "host", "tenant", "roles", "provider_roles", "data_classes"},
    "providers": {"id", "role", "placement", "data_classes"},
    "banks": {"id", "profile_id", "profile", "data_class", "kind", "authority", "writable", "enable_auto_consolidation", "memory_defense", "models", "directives"},
    "harnesses": {"id", "profile_id", "profile", "home_bank", "write_bank", "bank"},
    "migration": {"artifact_dir", "artifact_path", "proposal_log", "proposal_path"},
    "policy": {"engineering_memory_enabled", "allowed_placements", "provider_placements", "approved_tls_endpoints"},
}


class InventoryError(ValueError):
    pass


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        raise InventoryError(f"{label} must be a bounded identifier")
    return value


def _closed(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise InventoryError(f"{label} keys are closed (unknown={sorted(unknown)})")


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InventoryError(f"{label} must be an object")
    return value


def _records(
    value: Any, label: str, *, globally_unique_ids: bool = True
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise InventoryError(f"{label} must be an array")
    records = [_mapping(record, f"{label} entry") for record in value]
    seen: set[str] = set()
    for record in records:
        identifier = record.get("id")
        _identifier(identifier, f"{label} id")
        if globally_unique_ids and identifier in seen:
            raise InventoryError(f"duplicate {label} id: {identifier}")
        seen.add(identifier)
    return records


def _alias(
    record: Mapping[str, Any],
    canonical: str,
    legacy: str,
    label: str,
    *,
    default: Any = None,
) -> Any:
    if canonical in record and legacy in record and record[canonical] != record[legacy]:
        raise InventoryError(
            f"{label} has conflicting {canonical} and {legacy} values"
        )
    if canonical in record:
        return record[canonical]
    if legacy in record:
        return record[legacy]
    return default


def _reference(value: Any, label: str) -> tuple[str, str]:
    record = _mapping(value, label)
    _closed(record, {"profile_id", "profile", "bank_id", "bank"}, label)
    profile_id = _alias(record, "profile_id", "profile", label)
    bank_id = _alias(record, "bank_id", "bank", label)
    if not isinstance(profile_id, str) or not isinstance(bank_id, str):
        raise InventoryError(f"{label} must name profile_id and bank_id")
    return profile_id, bank_id


def _enabled(record: Mapping[str, Any]) -> bool:
    value = record.get("enabled", True)
    if not isinstance(value, bool):
        raise InventoryError(f"profile {record.get('id')} enabled must be boolean")
    return value


def _profile_port(profile: Mapping[str, Any], base_port: int) -> int:
    if "slot" in profile and (
        type(profile["slot"]) is not int or profile["slot"] < 0
    ):
        raise InventoryError(
            f"profile {profile.get('id')} slot must be a non-negative integer"
        )
    port = profile.get("port")
    if port is None:
        slot = profile.get("slot")
        if type(slot) is not int or slot < 0:
            raise InventoryError(f"profile {profile.get('id')} requires a non-negative integer slot or port")
        port = base_port + slot
    if type(port) is not int or not 1 <= port <= 65535:
        raise InventoryError(f"profile {profile.get('id')} port must be an integer from 1 to 65535")
    return port


def _validate_migration(migration: Mapping[str, Any]) -> None:
    artifact = _alias(
        migration, "artifact_dir", "artifact_path", "migration artifact"
    )
    proposal = _alias(
        migration, "proposal_log", "proposal_path", "migration proposal"
    )
    for value, label in ((artifact, "migration artifact"), (proposal, "migration proposal")):
        if not isinstance(value, str) or not value.strip():
            raise InventoryError(f"{label} path must be a non-empty absolute path")
        if not Path(value).is_absolute():
            raise InventoryError(f"{label} path must be absolute")


def _allowed_placements(policy: Mapping[str, Any], data_class: str) -> set[str] | None:
    table = _alias(
        policy,
        "allowed_placements",
        "provider_placements",
        "policy placements",
    )
    if table is None:
        return None
    if not isinstance(table, dict):
        raise InventoryError("policy allowed_placements must be an object")
    allowed = table.get(data_class)
    if allowed is None:
        raise InventoryError(
            f"policy allowed_placements is missing data class {data_class}"
        )
    if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
        raise InventoryError(f"allowed placements for {data_class} must be an array of strings")
    return set(allowed)


def _validate_policy(policy: Mapping[str, Any]) -> None:
    placements = _alias(
        policy,
        "allowed_placements",
        "provider_placements",
        "policy placements",
    )
    if placements is None:
        return
    if not isinstance(placements, dict):
        raise InventoryError("policy allowed_placements must be an object")
    unknown_data_classes = set(placements) - DATA_CLASSES
    if unknown_data_classes:
        raise InventoryError(
            "policy allowed_placements has unknown data classes: "
            f"{sorted(unknown_data_classes)}"
        )
    for data_class, allowed in placements.items():
        if (
            not isinstance(allowed, list)
            or any(
                not isinstance(value, str) or value not in PLACEMENTS
                for value in allowed
            )
            or len(allowed) != len(set(allowed))
        ):
            raise InventoryError(
                f"allowed placements for {data_class} must contain unique supported placements"
            )


def _validate_providers(
    providers: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    provider_by_id = {record["id"]: record for record in providers}
    for provider in providers:
        if (
            not isinstance(provider.get("role"), str)
            or provider["role"] not in ROLES
        ):
            raise InventoryError(f"provider {provider['id']} has invalid role")
        if (
            not isinstance(provider.get("placement"), str)
            or provider["placement"] not in PLACEMENTS
        ):
            raise InventoryError(f"provider {provider['id']} has invalid placement")
        data_classes = provider.get("data_classes")
        if (
            not isinstance(data_classes, list)
            or not all(isinstance(item, str) and item for item in data_classes)
            or len(set(data_classes)) != len(data_classes)
            or any(item not in DATA_CLASSES for item in data_classes)
        ):
            raise InventoryError(
                f"provider {provider['id']} data_classes must contain unique supported data classes"
            )
    return provider_by_id


def _index_bank_data_classes(
    banks: list[dict[str, Any]],
    profile_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, set[str]]:
    bank_data_classes: dict[str, set[str]] = {
        profile_id: set() for profile_id in profile_by_id
    }
    for bank in banks:
        profile_id = _alias(
            bank, "profile_id", "profile", f"bank {bank['id']} profile"
        )
        if not isinstance(profile_id, str) or profile_id not in profile_by_id:
            raise InventoryError(
                f"bank {bank['id']} references unknown profile {profile_id}"
            )
        data_class = _alias(
            bank, "data_class", "kind", f"bank {bank['id']} data class"
        )
        if not isinstance(data_class, str) or data_class not in DATA_CLASSES:
            raise InventoryError(
                f"bank {bank['id']} data_class must be one of {sorted(DATA_CLASSES)}"
            )
        bank_data_classes[profile_id].add(data_class)
    return bank_data_classes


def _validate_profiles(
    profiles: list[dict[str, Any]],
    *,
    base_port: int,
    bank_data_classes: Mapping[str, set[str]],
    provider_by_id: Mapping[str, Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> set[tuple[str, str, str, int, str]]:
    endpoints: dict[tuple[str, int], str] = {}
    enabled_tls_endpoints: set[tuple[str, str, str, int, str]] = set()
    for profile in profiles:
        scheme = profile.get("scheme", "http")
        if not isinstance(scheme, str) or scheme not in {"http", "https"}:
            raise InventoryError(
                f"profile {profile['id']} scheme must be http or https"
            )
        host = profile.get("host", "127.0.0.1")
        if not is_bare_endpoint_host(host):
            raise InventoryError(
                f"profile {profile['id']} host must be a bare DNS name or IP literal"
            )
        canonical_host = canonical_endpoint_host(host)
        if scheme == "http" and canonical_host not in {"127.0.0.1", "::1"}:
            raise InventoryError(
                f"profile {profile['id']} plain HTTP host must be literal loopback"
            )
        tenant = profile.get("tenant", "default")
        _identifier(tenant, f"profile {profile['id']} tenant")
        port = _profile_port(profile, base_port)
        if _enabled(profile):
            endpoint = (canonical_host, port)
            if endpoint in endpoints:
                raise InventoryError(
                    f"profile endpoint collision: {endpoints[endpoint]} and {profile['id']}"
                )
            endpoints[endpoint] = profile["id"]
            if scheme == "https":
                enabled_tls_endpoints.add(
                    (profile["id"], scheme, canonical_host, port, tenant)
                )

        roles = _alias(
            profile,
            "roles",
            "provider_roles",
            f"profile {profile['id']} provider roles",
            default={},
        )
        if not isinstance(roles, dict):
            raise InventoryError(f"profile {profile['id']} roles must be an object")
        unknown_roles = set(roles) - ROLES
        if unknown_roles:
            raise InventoryError(
                f"profile {profile['id']} has unknown provider roles: {sorted(unknown_roles)}"
            )
        for role, selected in roles.items():
            selected_ids = selected if isinstance(selected, list) else [selected]
            if not selected_ids or any(
                not isinstance(item, str) or not item for item in selected_ids
            ):
                raise InventoryError(
                    f"profile {profile['id']} role {role} must name providers"
                )
            if len(selected_ids) != len(set(selected_ids)):
                raise InventoryError(
                    f"profile {profile['id']} role {role} provider IDs "
                    "must be unique"
                )
        data_classes = profile.get("data_classes", [])
        if (
            not isinstance(data_classes, list)
            or any(not isinstance(item, str) for item in data_classes)
            or len(data_classes) != len(set(data_classes))
            or any(item not in DATA_CLASSES for item in data_classes)
        ):
            raise InventoryError(
                f"profile {profile['id']} data_classes must be an array of strings"
            )
        effective_data_classes = (
            set(data_classes) | bank_data_classes[profile["id"]]
        )
        for role, provider_id in roles.items():
            provider_ids = (
                provider_id if isinstance(provider_id, list) else [provider_id]
            )
            for selected_id in provider_ids:
                if selected_id not in provider_by_id:
                    raise InventoryError(
                        f"profile {profile['id']} references unknown provider {selected_id}"
                    )
                provider = provider_by_id[selected_id]
                if provider["role"] != role:
                    raise InventoryError(
                        f"provider {selected_id} cannot serve role {role}"
                    )
                placement = provider["placement"]
                permitted = provider["data_classes"]
                for data_class in effective_data_classes:
                    if data_class not in permitted:
                        raise InventoryError(
                            f"provider {selected_id} cannot receive {data_class} data"
                        )
                    allowed = _allowed_placements(policy, data_class)
                    if allowed is not None and placement not in allowed:
                        raise InventoryError(
                            f"provider {selected_id} placement is forbidden for {data_class}"
                        )
    return enabled_tls_endpoints


def _validate_banks(
    banks: list[dict[str, Any]],
    *,
    profile_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[
    set[tuple[str, str]],
    dict[tuple[str, str], tuple[bool, str]],
    int,
]:
    bank_refs: set[tuple[str, str]] = set()
    bank_routes: dict[tuple[str, str], tuple[bool, str]] = {}
    engineering_authorities = 0
    for bank in banks:
        profile_id = _alias(
            bank, "profile_id", "profile", f"bank {bank['id']} profile"
        )
        if not isinstance(profile_id, str):
            raise InventoryError(
                f"bank {bank['id']} references unknown profile {profile_id}"
            )
        bank_ref = (profile_id, bank["id"])
        if bank_ref in bank_refs:
            raise InventoryError(
                f"duplicate canonical bank reference: {profile_id}/{bank['id']}"
            )
        bank_refs.add(bank_ref)
        authority = bank.get("authority", "none")
        if not isinstance(authority, str) or authority not in {
            "authoritative", "replica", "none"
        }:
            raise InventoryError(f"bank {bank['id']} has invalid authority")
        writable = bank.get("writable", True)
        if not isinstance(writable, bool):
            raise InventoryError(f"bank {bank['id']} writable must be boolean")
        if "enable_auto_consolidation" in bank and not isinstance(
            bank["enable_auto_consolidation"], bool
        ):
            raise InventoryError(
                f"bank {bank['id']} enable_auto_consolidation must be boolean"
            )
        if bank.get("memory_defense", "sensitive_data") != "sensitive_data":
            raise InventoryError(
                f"bank {bank['id']} memory_defense must be sensitive_data"
            )
        models = bank.get("models", [])
        directives = bank.get("directives", [])
        if not isinstance(models, list) or not isinstance(directives, list):
            raise InventoryError(
                f"bank {bank['id']} models and directives must be arrays"
            )
        model_ids: set[str] = set()
        for model in models:
            if not isinstance(model, dict) or set(model) != {"id", "revision"}:
                raise InventoryError(f"bank {bank['id']} model schema is invalid")
            model_id = _identifier(
                model["id"], f"bank {bank['id']} model id"
            )
            if model_id in model_ids:
                raise InventoryError(
                    f"bank {bank['id']} model ids must be unique"
                )
            model_ids.add(model_id)
            _identifier(
                model["revision"], f"bank {bank['id']} model revision"
            )
        directive_ids: set[str] = set()
        for directive in directives:
            if (
                not isinstance(directive, dict)
                or set(directive) != {"id", "text"}
                or not isinstance(directive["text"], str)
                or not directive["text"].strip()
            ):
                raise InventoryError(
                    f"bank {bank['id']} directive schema is invalid"
                )
            directive_id = _identifier(
                directive["id"], f"bank {bank['id']} directive id"
            )
            if directive_id in directive_ids:
                raise InventoryError(
                    f"bank {bank['id']} directive ids must be unique"
                )
            directive_ids.add(directive_id)
        data_class = _alias(
            bank, "data_class", "kind", f"bank {bank['id']} data class"
        )
        bank_routes[bank_ref] = (writable, data_class)
        if (
            data_class == "engineering"
            and writable
            and authority != "authoritative"
        ):
            raise InventoryError(
                "writable engineering banks must be authoritative"
            )
        if (
            data_class == "engineering"
            and authority == "authoritative"
            and writable
            and _enabled(profile_by_id[profile_id])
        ):
            engineering_authorities += 1
    return bank_refs, bank_routes, engineering_authorities


def _engineering_enabled(
    policy: Mapping[str, Any],
    *,
    machine_engineering_enabled: bool,
    engineering_authorities: int,
) -> bool:
    engineering_enabled = policy.get(
        "engineering_memory_enabled", machine_engineering_enabled
    )
    if not isinstance(engineering_enabled, bool):
        raise InventoryError("engineering_memory_enabled must be boolean")
    if engineering_enabled and engineering_authorities != 1:
        raise InventoryError(
            "engineering memory requires exactly one authoritative write bank"
        )
    if not engineering_enabled and engineering_authorities:
        raise InventoryError(
            "enabled authoritative write banks are disabled by engineering memory policy"
        )
    return engineering_enabled


def _validate_tls_endpoints(
    policy: Mapping[str, Any],
    *,
    profile_by_id: Mapping[str, Mapping[str, Any]],
    enabled_tls_endpoints: set[tuple[str, str, str, int, str]],
) -> None:
    approved_endpoints = policy.get("approved_tls_endpoints", [])
    if not isinstance(approved_endpoints, list):
        raise InventoryError("approved_tls_endpoints must be an array")
    approved_tls_endpoints: set[tuple[str, str, str, int, str]] = set()
    for endpoint in approved_endpoints:
        if not isinstance(endpoint, dict) or set(endpoint) != {
            "profile_id", "scheme", "host", "port", "tenant"
        }:
            raise InventoryError("approved TLS endpoint keys are closed")
        if (
            not isinstance(endpoint["profile_id"], str)
            or not endpoint["profile_id"]
            or endpoint["profile_id"] not in profile_by_id
            or endpoint["scheme"] != "https"
            or not is_bare_endpoint_host(endpoint["host"])
            or type(endpoint["port"]) is not int
            or not 1 <= endpoint["port"] <= 65535
            or not isinstance(endpoint["tenant"], str)
            or not endpoint["tenant"]
        ):
            raise InventoryError("approved TLS endpoint is invalid")
        approved_endpoint = (
            endpoint["profile_id"],
            endpoint["scheme"],
            canonical_endpoint_host(endpoint["host"]),
            endpoint["port"],
            endpoint["tenant"],
        )
        if approved_endpoint in approved_tls_endpoints:
            raise InventoryError("approved TLS endpoints must be unique")
        approved_tls_endpoints.add(approved_endpoint)
    if approved_tls_endpoints != enabled_tls_endpoints:
        raise InventoryError(
            "approved TLS endpoints must exactly match enabled HTTPS profiles"
        )


def _validate_harnesses(
    harnesses: list[dict[str, Any]],
    *,
    profile_by_id: Mapping[str, Mapping[str, Any]],
    bank_refs: set[tuple[str, str]],
    bank_routes: Mapping[tuple[str, str], tuple[bool, str]],
    engineering_enabled: bool,
) -> None:
    for harness in harnesses:
        profile_id = _alias(
            harness,
            "profile_id",
            "profile",
            f"harness {harness['id']} profile",
        )
        if not isinstance(profile_id, str) or profile_id not in profile_by_id:
            raise InventoryError(
                f"harness {harness['id']} references unknown profile {profile_id}"
            )
        if not _enabled(profile_by_id[profile_id]):
            raise InventoryError(
                f"harness {harness['id']} references disabled profile {profile_id}"
            )
        if "home_bank" not in harness:
            raise InventoryError(f"harness {harness['id']} requires home_bank")
        home_ref = _reference(
            harness["home_bank"], f"harness {harness['id']} home_bank"
        )
        write_value = _alias(
            harness,
            "write_bank",
            "bank",
            f"harness {harness['id']} write bank",
        )
        if write_value is None:
            raise InventoryError(f"harness {harness['id']} requires write_bank")
        write_ref = _reference(
            write_value, f"harness {harness['id']} write_bank"
        )
        for ref in (home_ref, write_ref):
            if ref not in bank_refs:
                raise InventoryError(
                    f"harness {harness['id']} references unknown bank "
                    f"{ref[0]}/{ref[1]}"
                )
            if ref[0] != profile_id:
                raise InventoryError(
                    f"harness {harness['id']} bank reference must belong to "
                    f"profile {profile_id}"
                )
        write_writable, write_data_class = bank_routes[write_ref]
        if not write_writable:
            raise InventoryError(
                f"harness {harness['id']} write_bank must reference a writable bank"
            )
        if write_data_class == "engineering" and not engineering_enabled:
            raise InventoryError(
                f"harness {harness['id']} write_bank is disabled by engineering memory policy"
            )
        _home_writable, home_data_class = bank_routes[home_ref]
        if home_data_class == "engineering" and not engineering_enabled:
            raise InventoryError(
                f"harness {harness['id']} home_bank is disabled by engineering memory policy"
            )


def _resolved_reference(value: Mapping[str, Any], label: str) -> dict[str, str]:
    profile_id, bank_id = _reference(value, label)
    return {"profile_id": profile_id, "bank_id": bank_id}


def _resolved_artifact(
    raw: Mapping[str, Any],
    *,
    base_port: int,
    machine_engineering_enabled: bool,
) -> dict[str, Any]:
    profiles = []
    for profile in raw["profiles"]:
        profile_host = profile.get("host", "127.0.0.1")
        roles = _alias(
            profile,
            "roles",
            "provider_roles",
            f"profile {profile['id']} provider roles",
            default={},
        )
        profiles.append({
            "id": profile["id"],
            "port": _profile_port(profile, base_port),
            "enabled": _enabled(profile),
            "scheme": profile.get("scheme", "http"),
            "host": (
                canonical_endpoint_host(profile_host)
                if is_bare_endpoint_host(profile_host)
                else profile_host
            ),
            "tenant": profile.get("tenant", "default"),
            "roles": {
                role: sorted(
                    provider_ids
                    if isinstance(provider_ids, list)
                    else [provider_ids]
                )
                for role, provider_ids in roles.items()
            },
            "data_classes": sorted(profile.get("data_classes", [])),
        })
    profiles.sort(key=lambda profile: profile["id"])
    banks = []
    for bank in raw["banks"]:
        banks.append({
            "id": bank["id"],
            "profile_id": _alias(
                bank, "profile_id", "profile", f"bank {bank['id']} profile"
            ),
            "data_class": _alias(
                bank, "data_class", "kind", f"bank {bank['id']} data class"
            ),
            "authority": bank.get("authority", "none"),
            "writable": bank.get("writable", True),
            "enable_auto_consolidation": bank.get(
                "enable_auto_consolidation", False
            ),
            "memory_defense": bank.get("memory_defense", "sensitive_data"),
            "models": sorted(
                bank.get("models", []), key=lambda model: model["id"]
            ),
            "directives": sorted(
                bank.get("directives", []),
                key=lambda directive: directive["id"],
            ),
        })
    banks.sort(key=lambda bank: (bank["profile_id"], bank["id"]))
    harnesses = []
    for harness in raw["harnesses"]:
        harnesses.append({
            "id": harness["id"],
            "profile_id": _alias(
                harness,
                "profile_id",
                "profile",
                f"harness {harness['id']} profile",
            ),
            "home_bank": _resolved_reference(
                harness["home_bank"], f"harness {harness['id']} home_bank"
            ),
            "write_bank": _resolved_reference(
                _alias(
                    harness,
                    "write_bank",
                    "bank",
                    f"harness {harness['id']} write bank",
                ),
                f"harness {harness['id']} write_bank",
            ),
        })
    harnesses.sort(key=lambda harness: harness["id"])
    policy = raw["policy"]
    allowed_placements = _alias(
        policy,
        "allowed_placements",
        "provider_placements",
        "policy placements",
    )
    if allowed_placements is not None:
        allowed_placements = {
            data_class: sorted(placements)
            for data_class, placements in sorted(allowed_placements.items())
        }
    approved_tls_endpoints = []
    for endpoint in policy.get("approved_tls_endpoints", []):
        try:
            host = canonical_endpoint_host(endpoint["host"])
        except (TypeError, ValueError):
            # The inventory validator owns rejection. Artifact rendering remains
            # total so downstream boundary tests can exercise invalid inputs.
            host = endpoint["host"]
        approved_tls_endpoints.append({**endpoint, "host": host})
    approved_tls_endpoints.sort(
        key=lambda endpoint: (
            endpoint["profile_id"],
            endpoint["scheme"],
            endpoint["host"],
            endpoint["port"],
            endpoint["tenant"],
        )
    )
    return {
        "schema_version": raw["schema_version"],
        "machine_bindings": {
            "base_port": base_port,
            "engineering_memory_enabled": machine_engineering_enabled,
        },
        "archetype": raw["archetype"],
        "profiles": profiles,
        "providers": sorted(
            (
                {
                    **provider,
                    "data_classes": sorted(provider["data_classes"]),
                }
                for provider in raw["providers"]
            ),
            key=lambda provider: provider["id"],
        ),
        "banks": banks,
        "harnesses": harnesses,
        "policy": {
            "engineering_memory_enabled": policy.get(
                "engineering_memory_enabled", machine_engineering_enabled
            ),
            "allowed_placements": allowed_placements,
            "approved_tls_endpoints": approved_tls_endpoints,
        },
    }


def _validate(raw: dict[str, Any]) -> Inventory:
    if set(raw) != ROOT_KEYS:
        missing = sorted(ROOT_KEYS - set(raw))
        unknown = sorted(set(raw) - ROOT_KEYS)
        raise InventoryError(f"inventory root keys are closed (missing={missing}, unknown={unknown})")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise InventoryError("schema_version must be integer 1")

    machine = _mapping(raw["machine"], "machine")
    archetype = _mapping(raw["archetype"], "archetype")
    migration = _mapping(raw["migration"], "migration")
    policy = _mapping(raw["policy"], "policy")
    profiles = _records(raw["profiles"], "profiles")
    providers = _records(raw["providers"], "providers")
    banks = _records(raw["banks"], "banks", globally_unique_ids=False)
    harnesses = _records(raw["harnesses"], "harnesses")
    for label, record in (("machine", machine), ("archetype", archetype), ("migration", migration), ("policy", policy)):
        _closed(record, RECORD_KEYS[label], label)
    for label, records in (("profiles", profiles), ("providers", providers), ("banks", banks), ("harnesses", harnesses)):
        for record in records:
            _closed(record, RECORD_KEYS[label], f"{label} entry")
    _validate_migration(migration)

    if "id" in machine and (not isinstance(machine["id"], str) or not machine["id"]):
        raise InventoryError("machine id must be a non-empty string")
    if "id" in archetype and (not isinstance(archetype["id"], str) or not archetype["id"]):
        raise InventoryError("archetype id must be a non-empty string")

    _validate_policy(policy)
    profile_by_id = {record["id"]: record for record in profiles}
    provider_by_id = _validate_providers(providers)
    base_port = machine.get("base_port", 7979)
    if type(base_port) is not int or not 1 <= base_port <= 65_535:
        raise InventoryError(
            "machine base_port must be an integer from 1 to 65535"
        )
    machine_engineering_enabled = machine.get(
        "engineering_memory_enabled", False
    )
    if not isinstance(machine_engineering_enabled, bool):
        raise InventoryError(
            "machine engineering_memory_enabled must be boolean"
        )
    bank_data_classes = _index_bank_data_classes(banks, profile_by_id)

    enabled_tls_endpoints = _validate_profiles(
        profiles,
        base_port=base_port,
        bank_data_classes=bank_data_classes,
        provider_by_id=provider_by_id,
        policy=policy,
    )

    bank_refs, bank_routes, engineering_authorities = _validate_banks(
        banks, profile_by_id=profile_by_id
    )
    engineering_enabled = _engineering_enabled(
        policy,
        machine_engineering_enabled=machine_engineering_enabled,
        engineering_authorities=engineering_authorities,
    )
    _validate_tls_endpoints(
        policy,
        profile_by_id=profile_by_id,
        enabled_tls_endpoints=enabled_tls_endpoints,
    )

    _validate_harnesses(
        harnesses,
        profile_by_id=profile_by_id,
        bank_refs=bank_refs,
        bank_routes=bank_routes,
        engineering_enabled=engineering_enabled,
    )

    artifact = _resolved_artifact(
        raw,
        base_port=base_port,
        machine_engineering_enabled=machine_engineering_enabled,
    )
    return Inventory(
        schema_version=1,
        machine=machine,
        archetype=archetype,
        profiles=tuple(profiles),
        providers=tuple(providers),
        banks=tuple(banks),
        harnesses=tuple(harnesses),
        migration=migration,
        policy=policy,
        inventory_digest=digest(raw),
        artifact_digest=digest(artifact),
    )


def load_inventory(path: str | Path) -> Inventory:
    try:
        raw = strict_json_loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise InventoryError(f"cannot load inventory: {error}") from error
    return _validate(_mapping(raw, "inventory"))
