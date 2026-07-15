"""Inactive broker-only harness rendering and reversible activation."""

from dataclasses import dataclass, field
import hmac
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from .canonical import digest
from .model import FrozenDict, deep_freeze, deep_thaw


SUPPORTED_HARNESSES = frozenset({"codex", "claude-code", "cursor"})
OWNED_KEYS = frozenset({"schemaVersion", "broker", "adapter", "active"})
RETIRED_DIRECT_KEYS = frozenset(
    {"hindsightApiUrl", "bankId", "tenantToken", "bearerToken", "apiKey", "signingKey"}
)
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
ACTIVATION_REQUIREMENTS = ("broker_healthy", "profile_healthy", "adapter_self_test")


class ActivationCASMismatch(ValueError):
    """The destination changed before an atomic configuration update."""


def _configuration_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    preserved = {
        key: deep_thaw(item)
        for key, item in value.items()
        if key not in OWNED_KEYS
    }
    return {
        "configuration_digest": digest(value),
        "preserved_config_digest": digest(preserved),
        "preserved_config_present": bool(preserved),
    }


@dataclass(frozen=True)
class RenderedHarness:
    harness_id: str
    rendered: Mapping[str, Any] = field(repr=False)
    prestate: Mapping[str, Mapping[str, Any]] = field(repr=False)
    expected_prestate_digest: str
    retired_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rendered", deep_freeze(self.rendered))
        object.__setattr__(self, "prestate", deep_freeze(self.prestate))
        object.__setattr__(self, "retired_keys", tuple(self.retired_keys))

    def to_dict(self) -> dict[str, Any]:
        return {
            "harness_id": self.harness_id,
            **_configuration_projection(self.rendered),
            "activation_state": (
                "active" if self.rendered.get("active") is True else "inactive"
            ),
            "expected_prestate_digest": self.expected_prestate_digest,
            "retired_keys_digest": digest(list(self.retired_keys)),
            "retired_keys_present": bool(self.retired_keys),
        }


@dataclass(frozen=True)
class ActivationPlan:
    harness_id: str
    inventory_digest: str
    artifact_digest: str
    policy_digest: str
    expected_prestate_digest: str
    expected_owned_prestate_digest: str
    owned_target: Mapping[str, Any] = field(repr=False)
    retired_keys: tuple[str, ...]
    requirements: tuple[str, ...]
    plan_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "owned_target", deep_freeze(self.owned_target))
        object.__setattr__(self, "retired_keys", tuple(self.retired_keys))
        object.__setattr__(self, "requirements", tuple(self.requirements))

    def body(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "harness_id": self.harness_id,
            "inventory_digest": self.inventory_digest,
            "artifact_digest": self.artifact_digest,
            "policy_digest": self.policy_digest,
            "expected_prestate_digest": self.expected_prestate_digest,
            "expected_owned_prestate_digest": self.expected_owned_prestate_digest,
            "owned_target": deep_thaw(self.owned_target),
            "retired_keys": list(self.retired_keys),
            "requirements": list(self.requirements),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.body(), "plan_digest": self.plan_digest}


@dataclass(frozen=True)
class ActivationOutcome:
    status: str
    reason: str
    configuration: Mapping[str, Any] = field(repr=False)
    activation_state: str
    plan_digest: str
    rollback_attempted: bool = False
    rollback_succeeded: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "configuration", deep_freeze(self.configuration))

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            **_configuration_projection(self.configuration),
            "activation_state": self.activation_state,
            "plan_digest": self.plan_digest,
            "rollback_attempted": self.rollback_attempted,
            "rollback_succeeded": self.rollback_succeeded,
        }


def _socket_path(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or "://" in value
        or not Path(value).is_absolute()
    ):
        raise ValueError("broker locator must be an absolute Unix socket path")
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def render_harness(
    current: Mapping[str, Any], *, harness_id: str, adapter: str, socket_path: str
) -> RenderedHarness:
    """Merge the exact managed keys into one inactive harness configuration."""

    harness_id = _identifier(harness_id, "harness ID")
    if harness_id not in SUPPORTED_HARNESSES:
        raise ValueError(f"unsupported harness: {harness_id}")
    adapter = _identifier(adapter, "adapter identity")
    socket_path = _socket_path(socket_path)
    if not isinstance(current, Mapping):
        raise ValueError("current harness configuration must be an object")

    prestate = {
        key: ({"present": True, "value": deep_thaw(current[key])} if key in current else {"present": False})
        for key in OWNED_KEYS
    }
    rendered = deep_thaw(current)
    retired_keys = tuple(sorted(RETIRED_DIRECT_KEYS.intersection(rendered)))
    for key in retired_keys:
        rendered.pop(key, None)
    rendered.update(
        {
            "schemaVersion": 1,
            "broker": {"transport": "unix", "path": socket_path, "scope": "user"},
            "adapter": adapter,
            "active": False,
        }
    )
    return RenderedHarness(harness_id, rendered, prestate, digest(current), retired_keys)


def render_harnesses(
    current_by_harness: Mapping[str, Mapping[str, Any]],
    bindings: Mapping[str, str],
    *,
    socket_path: str,
) -> Mapping[str, RenderedHarness]:
    """Render every declared Codex, Claude Code, or Cursor binding inactive."""

    if not isinstance(current_by_harness, Mapping) or not isinstance(bindings, Mapping):
        raise ValueError("harness configurations and bindings must be objects")
    unsupported = set(bindings) - SUPPORTED_HARNESSES
    if unsupported:
        raise ValueError(f"unsupported harness: {sorted(unsupported)[0]}")
    return FrozenDict(
        {
            harness_id: render_harness(
                current_by_harness.get(harness_id, {}),
                harness_id=harness_id,
                adapter=adapter,
                socket_path=socket_path,
            )
            for harness_id, adapter in bindings.items()
        }
    )


def _validate_digest(value: str, label: str) -> str:
    if not isinstance(value, str) or DIGEST.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _owned_prestate(configuration: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        key: (
            {"present": True, "value": deep_thaw(configuration[key])}
            if key in configuration
            else {"present": False}
        )
        for key in OWNED_KEYS
    }


def _activation_surface(
    configuration: Mapping[str, Any], retired_keys: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    keys = OWNED_KEYS.union(retired_keys)
    return {
        key: (
            {"present": True, "value": deep_thaw(configuration[key])}
            if key in configuration
            else {"present": False}
        )
        for key in keys
    }


def _validate_prestate(value: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != OWNED_KEYS:
        raise ValueError("owned prestate must contain the exact owned-key set")
    result: dict[str, dict[str, Any]] = {}
    for key in OWNED_KEYS:
        record = value[key]
        if not isinstance(record, Mapping) or record.get("present") not in {True, False}:
            raise ValueError("owned prestate entry is invalid")
        expected = {"present", "value"} if record["present"] is True else {"present"}
        if set(record) != expected:
            raise ValueError("owned prestate entry is invalid")
        result[key] = deep_thaw(record)
    return result


def activation_plan(
    rendered: RenderedHarness,
    *,
    inventory_digest: str,
    artifact_digest: str,
    policy_digest: str,
    current: Mapping[str, Any] | None = None,
) -> ActivationPlan:
    """Build an immutable activation plan without activating the harness."""

    if not isinstance(rendered, RenderedHarness):
        raise ValueError("rendered harness is required")
    digests = {
        "inventory_digest": _validate_digest(inventory_digest, "inventory digest"),
        "artifact_digest": _validate_digest(artifact_digest, "artifact digest"),
        "policy_digest": _validate_digest(policy_digest, "policy digest"),
    }
    if current is None:
        prestate = _validate_prestate(rendered.prestate)
        expected_prestate_digest = rendered.expected_prestate_digest
    else:
        if not isinstance(current, Mapping):
            raise ValueError("current harness configuration must be an object")
        expected_prestate_digest = digest(current)
        if not hmac.compare_digest(expected_prestate_digest, rendered.expected_prestate_digest):
            raise ValueError("current harness configuration does not match rendered prestate")
        prestate = _owned_prestate(current)
    target = {key: deep_thaw(rendered.rendered[key]) for key in OWNED_KEYS}
    target["active"] = True
    body = {
        "schema_version": 1,
        "harness_id": rendered.harness_id,
        **digests,
        "expected_prestate_digest": expected_prestate_digest,
        "expected_owned_prestate_digest": digest(prestate),
        "owned_target": target,
        "retired_keys": list(rendered.retired_keys),
        "requirements": list(ACTIVATION_REQUIREMENTS),
    }
    return ActivationPlan(
        rendered.harness_id,
        inventory_digest,
        artifact_digest,
        policy_digest,
        expected_prestate_digest,
        body["expected_owned_prestate_digest"],
        target,
        rendered.retired_keys,
        ACTIVATION_REQUIREMENTS,
        digest(body),
    )


def _valid_plan(plan: ActivationPlan) -> bool:
    if not isinstance(plan, ActivationPlan):
        return False
    if plan.harness_id not in SUPPORTED_HARNESSES:
        return False
    try:
        _validate_digest(plan.inventory_digest, "inventory digest")
        _validate_digest(plan.artifact_digest, "artifact digest")
        _validate_digest(plan.policy_digest, "policy digest")
        _validate_digest(plan.expected_prestate_digest, "prestate digest")
        _validate_digest(plan.expected_owned_prestate_digest, "owned prestate digest")
        _validate_digest(plan.plan_digest, "plan digest")
    except (TypeError, ValueError):
        return False
    target = plan.owned_target
    if (
        not isinstance(target, Mapping)
        or set(target) != OWNED_KEYS
        or type(target.get("schemaVersion")) is not int
        or target.get("schemaVersion") != 1
        or target.get("active") is not True
    ):
        return False
    broker = target.get("broker")
    if (
        not isinstance(broker, Mapping)
        or set(broker) != {"transport", "path", "scope"}
        or broker.get("transport") != "unix"
        or broker.get("scope") != "user"
    ):
        return False
    try:
        _socket_path(broker.get("path"))
        _identifier(target.get("adapter"), "adapter identity")
    except (TypeError, ValueError):
        return False
    try:
        if len(set(plan.retired_keys)) != len(plan.retired_keys) or not set(
            plan.retired_keys
        ).issubset(RETIRED_DIRECT_KEYS):
            return False
        if plan.requirements != ACTIVATION_REQUIREMENTS:
            return False
        return hmac.compare_digest(digest(plan.body()), plan.plan_digest)
    except (TypeError, ValueError):
        return False


def _state(configuration: Mapping[str, Any]) -> str:
    active = configuration.get("active") if isinstance(configuration, Mapping) else None
    if active is True:
        return "active"
    if active is False:
        return "inactive"
    return "unknown"


def _outcome(
    status: str,
    reason: str,
    configuration: Mapping[str, Any],
    plan: Any,
    *,
    rollback_attempted: bool = False,
    rollback_succeeded: bool = False,
) -> ActivationOutcome:
    copied = deep_thaw(deep_freeze(configuration))
    return ActivationOutcome(
        status,
        reason,
        copied,
        _state(copied),
        plan.plan_digest
        if isinstance(plan, ActivationPlan)
        and isinstance(plan.plan_digest, str)
        else "",
        rollback_attempted,
        rollback_succeeded,
    )


def _owned_rollback_target(
    observed: Mapping[str, Any], prestate: Mapping[str, Any], plan: ActivationPlan
) -> dict[str, Any]:
    restored = deep_thaw(deep_freeze(observed))
    for key in OWNED_KEYS | set(plan.retired_keys):
        if key in prestate:
            restored[key] = deep_thaw(deep_freeze(prestate[key]))
        else:
            restored.pop(key, None)
    return restored


def apply_activation(
    plan: ActivationPlan,
    current: Mapping[str, Any],
    *,
    approved_plan_digest: str,
    inventory_digest: str,
    artifact_digest: str,
    policy_digest: str,
    broker_healthy: bool,
    profile_healthy: bool,
    adapter_self_test: bool,
    persist_rollback_prestate: Callable[[Mapping[str, Any]], None],
    write_configuration: Callable[
        [str, Mapping[str, Any]], Mapping[str, Any] | None
    ],
    postcheck: Callable[[str, str, str], bool],
    destination_harness_id: str,
    read_configuration: Callable[[], Mapping[str, Any]] | None = None,
) -> ActivationOutcome:
    """Apply only the plan's owned target after every fresh gate passes."""

    if not isinstance(current, Mapping):
        raise ValueError("current harness configuration must be an object")
    if not _valid_plan(plan):
        return _outcome("refused", "invalid_plan", current, plan)
    if destination_harness_id != plan.harness_id:
        return _outcome("refused", "destination_harness_changed", current, plan)
    if not isinstance(approved_plan_digest, str) or not hmac.compare_digest(
        approved_plan_digest, plan.plan_digest
    ):
        return _outcome("refused", "plan_not_approved", current, plan)
    for label, actual, expected in (
        ("inventory", inventory_digest, plan.inventory_digest),
        ("artifact", artifact_digest, plan.artifact_digest),
        ("policy", policy_digest, plan.policy_digest),
    ):
        if not isinstance(actual, str) or not hmac.compare_digest(actual, expected):
            return _outcome("refused", f"{label}_digest_changed", current, plan)
    for healthy, reason in (
        (broker_healthy, "broker_unhealthy"),
        (profile_healthy, "profile_unhealthy"),
        (adapter_self_test, "adapter_self_test_failed"),
    ):
        if healthy is not True:
            return _outcome("refused", reason, current, plan)
    if not hmac.compare_digest(digest(_owned_prestate(current)), plan.expected_owned_prestate_digest):
        return _outcome("refused", "owned_prestate_changed", current, plan)
    if not hmac.compare_digest(digest(current), plan.expected_prestate_digest):
        return _outcome("refused", "prestate_changed", current, plan)
    if tuple(sorted(RETIRED_DIRECT_KEYS.intersection(current))) != plan.retired_keys:
        return _outcome("refused", "retired_keys_changed", current, plan)
    if not callable(persist_rollback_prestate) or not callable(write_configuration):
        return _outcome("refused", "activation_writer_unavailable", current, plan)
    if not callable(read_configuration):
        return _outcome("refused", "activation_reader_unavailable", current, plan)

    activated = deep_thaw(deep_freeze(current))
    for key in plan.retired_keys:
        activated.pop(key, None)
    for key in OWNED_KEYS:
        activated[key] = deep_thaw(plan.owned_target[key])

    def rollback_owned(reason: str, fallback: Mapping[str, Any]) -> ActivationOutcome:
        configuration = deep_thaw(deep_freeze(fallback))
        rollback_succeeded = False
        try:
            observed = read_configuration()
            cas_observed = read_configuration()
            if (
                not isinstance(observed, Mapping)
                or not isinstance(cas_observed, Mapping)
                or not hmac.compare_digest(digest(observed), digest(cas_observed))
            ):
                raise ValueError("destination changed before rollback")
            target = _owned_rollback_target(observed, current, plan)
            write_result = write_configuration(digest(observed), target)
            if write_result is not None and (
                not isinstance(write_result, Mapping)
                or not hmac.compare_digest(digest(write_result), digest(target))
            ):
                raise ValueError("rollback write result is invalid")
            restored = read_configuration()
            if not isinstance(restored, Mapping):
                raise ValueError("persisted destination configuration is invalid")
            configuration = deep_thaw(deep_freeze(restored))
            rollback_succeeded = hmac.compare_digest(
                digest(configuration), digest(target)
            ) and hmac.compare_digest(
                digest(_activation_surface(configuration, plan.retired_keys)),
                digest(_activation_surface(current, plan.retired_keys)),
            )
        except Exception:
            try:
                observed = read_configuration()
                if isinstance(observed, Mapping):
                    configuration = deep_thaw(deep_freeze(observed))
            except Exception:
                pass
        return ActivationOutcome(
            "rolled_back" if rollback_succeeded else "rollback_failed",
            reason,
            configuration,
            _state(configuration),
            plan.plan_digest,
            True,
            rollback_succeeded,
        )
    try:
        persist_rollback_prestate(deep_thaw(deep_freeze(current)))
    except Exception:
        return _outcome("refused", "rollback_prestate_persistence_failed", current, plan)
    try:
        persisted_prestate = read_configuration()
    except Exception:
        return _outcome("refused", "activation_readback_failed", current, plan)
    if not isinstance(persisted_prestate, Mapping):
        return _outcome("refused", "activation_readback_failed", current, plan)
    if not hmac.compare_digest(digest(persisted_prestate), digest(current)):
        return _outcome("refused", "prestate_changed", persisted_prestate, plan)
    try:
        write_configuration(
            plan.expected_prestate_digest,
            deep_thaw(deep_freeze(activated)),
        )
    except ActivationCASMismatch:
        try:
            observed = read_configuration()
        except Exception:
            observed = current
        configuration = (
            deep_thaw(deep_freeze(observed))
            if isinstance(observed, Mapping)
            else deep_thaw(deep_freeze(current))
        )
        return _outcome("refused", "prestate_changed", configuration, plan)
    except Exception:
        return rollback_owned("activation_write_failed", activated)
    try:
        persisted_value = read_configuration()
        if not isinstance(persisted_value, Mapping):
            raise ValueError("persisted destination configuration is invalid")
        persisted = deep_thaw(deep_freeze(persisted_value))
    except Exception:
        return rollback_owned("activation_readback_failed", activated)
    if not hmac.compare_digest(
        digest(_activation_surface(persisted, plan.retired_keys)),
        digest(_activation_surface(activated, plan.retired_keys)),
    ):
        return rollback_owned("activation_readback_mismatch", persisted)
    try:
        postcheck_passed = callable(postcheck) and postcheck(
            plan.plan_digest,
            digest(persisted),
            destination_harness_id,
        ) is True
    except Exception:
        postcheck_passed = False
    if not postcheck_passed:
        try:
            rollback_current = read_configuration()
        except Exception:
            rollback_current = None
        if (
            not isinstance(rollback_current, Mapping)
            or not hmac.compare_digest(
                digest(rollback_current), digest(persisted)
            )
        ):
            configuration = (
                deep_thaw(deep_freeze(rollback_current))
                if isinstance(rollback_current, Mapping)
                else deep_thaw(deep_freeze(persisted))
            )
            return ActivationOutcome(
                "rollback_failed",
                "postcheck_failed",
                configuration,
                _state(configuration),
                plan.plan_digest,
                True,
                False,
            )
        rolled_back = rollback_activation(
            plan,
            rollback_current,
            approved_plan_digest=approved_plan_digest,
            prestate=current,
            destination_harness_id=destination_harness_id,
        )
        configuration = deep_thaw(deep_freeze(persisted))
        rollback_succeeded = False
        if rolled_back.status == "rolled_back":
            try:
                restored = deep_thaw(rolled_back.configuration)
                write_configuration(digest(rollback_current), restored)
                rollback_persisted = read_configuration()
                if not isinstance(rollback_persisted, Mapping):
                    raise ValueError("persisted destination configuration is invalid")
                configuration = deep_thaw(deep_freeze(rollback_persisted))
                rollback_succeeded = hmac.compare_digest(
                    digest(_activation_surface(configuration, plan.retired_keys)),
                    digest(_activation_surface(current, plan.retired_keys)),
                )
            except Exception:
                configuration = deep_thaw(deep_freeze(persisted))
        return ActivationOutcome(
            "rolled_back" if rollback_succeeded else "rollback_failed",
            "postcheck_failed",
            configuration,
            _state(configuration),
            plan.plan_digest,
            True,
            rollback_succeeded,
        )
    return _outcome("activated", "ok", persisted, plan)


def rollback_activation(
    plan: ActivationPlan,
    current: Mapping[str, Any],
    *,
    approved_plan_digest: str,
    prestate: Mapping[str, Any],
    destination_harness_id: str,
) -> ActivationOutcome:
    """Restore activation-owned fields from a digest-bound prestate snapshot."""

    if not isinstance(current, Mapping) or not isinstance(prestate, Mapping):
        raise ValueError("current and prestate harness configurations must be objects")
    if not _valid_plan(plan):
        return _outcome("refused", "invalid_plan", current, plan)
    if destination_harness_id != plan.harness_id:
        return _outcome("refused", "destination_harness_changed", current, plan)
    if not isinstance(approved_plan_digest, str) or not hmac.compare_digest(
        approved_plan_digest, plan.plan_digest
    ):
        return _outcome("refused", "plan_not_approved", current, plan)
    if not hmac.compare_digest(digest(prestate), plan.expected_prestate_digest):
        return _outcome("refused", "rollback_prestate_changed", current, plan)

    expected_surface = {
        **{
            key: {"present": True, "value": deep_thaw(value)}
            for key, value in plan.owned_target.items()
        },
        **{key: {"present": False} for key in plan.retired_keys},
    }
    if not hmac.compare_digest(
        digest(_activation_surface(current, plan.retired_keys)), digest(expected_surface)
    ):
        return _outcome("refused", "activation_state_changed", current, plan)

    restored = deep_thaw(deep_freeze(current))
    for key in OWNED_KEYS.union(plan.retired_keys):
        if key in prestate:
            restored[key] = deep_thaw(prestate[key])
        else:
            restored.pop(key, None)
    return _outcome("rolled_back", "ok", restored, plan, rollback_attempted=True, rollback_succeeded=True)
