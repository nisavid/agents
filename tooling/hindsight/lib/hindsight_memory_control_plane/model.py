"""Immutable records shared by validation, planning, and apply slices."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Tuple

from .canonical import StrictJsonError, canonical_scalar


class FrozenDict(Mapping[str, Any]):
    """A composition-backed immutable mapping.

    Inheriting from ``dict`` would leave the base-class mutation primitives
    available to callers.  Keeping the copied payload private makes the
    immutability boundary structural rather than conventional.
    """

    __slots__ = ("__data",)

    def __init__(self, value: Mapping[str, Any] | None = None, /, **kwargs: Any) -> None:
        data = dict(value or {})
        data.update(kwargs)
        object.__setattr__(self, "_FrozenDict__data", MappingProxyType(data))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("FrozenDict is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("FrozenDict is immutable")

    def __getitem__(self, key: str) -> Any:
        return self.__data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.__data)

    def __len__(self) -> int:
        return len(self.__data)

    def __repr__(self) -> str:
        return f"FrozenDict({self.__data!r})"


def deep_freeze(value: Any) -> Any:
    if isinstance(value, FrozenDict):
        # Do not trust callers to have constructed FrozenDict recursively.
        if any(not isinstance(key, str) for key in value):
            raise TypeError("mapping keys must be strings")
        return FrozenDict({key: deep_freeze(item) for key, item in value.items()})
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("mapping keys must be strings")
        return FrozenDict({key: deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(deep_freeze(item) for item in value)
    if value is None or isinstance(value, (str, bool, int, float)):
        try:
            return canonical_scalar(value)
        except StrictJsonError as error:
            raise TypeError(str(error)) from None
    raise TypeError("value must contain only JSON-compatible types")


def deep_thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: deep_thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [deep_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class BankRef:
    profile_id: str
    bank_id: str
    endpoint: "EndpointIdentity | None" = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"profile_id": self.profile_id, "bank_id": self.bank_id}
        if self.endpoint is not None:
            value["endpoint"] = self.endpoint.to_dict()
        return value


@dataclass(frozen=True)
class EndpointIdentity:
    profile_id: str
    scheme: str
    host: str
    port: int
    tenant: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "scheme": self.scheme,
            "host": self.host,
            "port": self.port,
            "tenant": self.tenant,
        }


@dataclass(frozen=True)
class OperationSnapshot:
    idle: bool
    active: Tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "active", tuple(deep_freeze(item) for item in self.active))

    def to_dict(self) -> dict[str, Any]:
        return {"idle": self.idle, "active": [deep_thaw(item) for item in self.active]}


@dataclass(frozen=True)
class Action:
    id: str
    kind: str
    details: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.details, Mapping) or {"id", "kind"} & set(self.details):
            raise ValueError("action details cannot override action identity")
        object.__setattr__(self, "details", deep_freeze(self.details))

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, **deep_thaw(self.details)}


@dataclass(frozen=True)
class Inventory:
    schema_version: int
    machine: Mapping[str, Any]
    archetype: Mapping[str, Any]
    profiles: Tuple[Mapping[str, Any], ...]
    providers: Tuple[Mapping[str, Any], ...]
    banks: Tuple[Mapping[str, Any], ...]
    harnesses: Tuple[Mapping[str, Any], ...]
    migration: Mapping[str, Any]
    policy: Mapping[str, Any]
    inventory_digest: str
    artifact_digest: str

    def __post_init__(self) -> None:
        for field in ("machine", "archetype", "migration", "policy"):
            object.__setattr__(self, field, deep_freeze(getattr(self, field)))
        for field in ("profiles", "providers", "banks", "harnesses"):
            object.__setattr__(self, field, tuple(deep_freeze(item) for item in getattr(self, field)))

    def body(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "machine": deep_thaw(self.machine),
            "archetype": deep_thaw(self.archetype),
            "profiles": [deep_thaw(value) for value in self.profiles],
            "providers": [deep_thaw(value) for value in self.providers],
            "banks": [deep_thaw(value) for value in self.banks],
            "harnesses": [deep_thaw(value) for value in self.harnesses],
            "migration": deep_thaw(self.migration),
            "policy": deep_thaw(self.policy),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.body(),
            "inventory_digest": self.inventory_digest,
            "artifact_digest": self.artifact_digest,
        }


@dataclass(frozen=True)
class Plan:
    schema_version: int
    inventory_digest: str
    artifact_digest: str
    target_profile: str
    target_endpoint: EndpointIdentity
    live_state_digest: str
    operations: OperationSnapshot
    compatibility: Tuple[Mapping[str, Any], ...]
    actions: Tuple[Action, ...]
    destructive: bool
    plan_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "compatibility", tuple(deep_freeze(item) for item in self.compatibility))
        object.__setattr__(self, "actions", tuple(self.actions))

    def body(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "inventory_digest": self.inventory_digest,
            "artifact_digest": self.artifact_digest,
            "target_profile": self.target_profile,
            "target_endpoint": self.target_endpoint.to_dict(),
            "live_state_digest": self.live_state_digest,
            "operations": self.operations.to_dict(),
            "compatibility": [deep_thaw(value) for value in self.compatibility],
            "actions": [value.to_dict() for value in self.actions],
            "destructive": self.destructive,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.body(), "plan_digest": self.plan_digest}
