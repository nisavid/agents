"""Fail-closed validation for immutable OrbStack airlock launch plans."""

from dataclasses import InitVar, dataclass, field
import hmac
import ipaddress
import re
import secrets
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from .canonical import digest
from .model import deep_freeze, deep_thaw


TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "backend",
        "machine",
        "mounts",
        "egress",
        "harness",
        "probes",
        "state",
        "retention",
        "recall",
        "bootstrap",
        "export",
        "bridge",
        "teardown",
    }
)
TAMPER_PROBES = frozenset(
    {"firewall", "routes", "dns", "network_namespace", "broker_config"}
)
UNREACHABLE_PROBES = frozenset(
    {
        "host_loopback",
        "host_broker_socket",
        "core_profile_endpoints",
        "undeclared_destinations",
    }
)
PREFLIGHT_PROBES = (
    "machine.created_fresh",
    "machine.linux",
    "machine.runtime.ephemeral",
    "machine.runtime.separate_kernel",
    "machine.macos_integration.disabled",
    "network.host.denied",
    "network.peer.denied",
    "mounts.inputs.read_only",
    "mounts.output.narrow",
    "egress.root_owned",
    "harness.unprivileged",
    "harness.no_sudo",
    "tamper.firewall.denied",
    "tamper.routes.denied",
    "tamper.dns.denied",
    "tamper.network_namespace.denied",
    "tamper.broker_config.denied",
    "reachability.host_loopback.denied",
    "reachability.host_broker_socket.denied",
    "reachability.core_profile_endpoints.denied",
    "reachability.undeclared_destinations.denied",
    "state.profile.independent",
    "state.token.independent",
    "state.session.independent",
    "bootstrap.artifact.bound",
    "retention.chunk_only",
    "recall.core.denied",
)
CLOSEOUT_PROBES = (
    "export.encrypted.verified",
    "bridge.candidates.source_cited",
    "bridge.candidates.dispositioned",
    "teardown.bank.deleted",
    "teardown.profile.deleted",
    "teardown.machine.deleted",
    "teardown.immediate",
)
REQUIRED_PROBES = PREFLIGHT_PROBES + CLOSEOUT_PROBES
APPROVED_DESTINATION_CATALOG = MappingProxyType({
    "anthropic-api": ("api.anthropic.com", 443),
    "cohere-api": ("api.cohere.com", 443),
    "openai-api": ("api.openai.com", 443),
})
DESTINATION_KEYS = {"selector", "host", "port"}


class AirlockPlanError(ValueError):
    """The candidate or its runtime evidence cannot establish the airlock."""


class OrbStackProbeRunner(Protocol):
    """Probe seam with no production process execution."""

    def attest(self) -> Mapping[str, Any]:
        """Return the exact runtime instance and session identity."""

    def probe(
        self, probe: str, evidence: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Return the result and runtime attestation from the same probe call."""


_VALIDATED_PLAN = object()


@dataclass(frozen=True)
class AirlockLaunchPlan:
    """Deeply immutable, disclosure-safe launch plan."""

    _body: Mapping[str, Any]
    _validation: InitVar[object] = None
    _attestation: InitVar[Mapping[str, Any] | None] = None
    _plan_digest: str = field(init=False, repr=False)
    _resource_identity: Mapping[str, Any] = field(init=False, repr=False)
    _runner_attestation: Mapping[str, Any] = field(init=False, repr=False)

    def __post_init__(
        self,
        _validation: object,
        _attestation: Mapping[str, Any] | None,
    ) -> None:
        if _validation is not _VALIDATED_PLAN:
            raise AirlockPlanError("launch plan construction requires validated preflight evidence")
        if _attestation is None:
            raise AirlockPlanError("launch plan requires runner attestation")
        body = deep_freeze(self._body)
        attestation = deep_freeze(_attestation)
        object.__setattr__(self, "_body", body)
        object.__setattr__(self, "_runner_attestation", attestation)
        object.__setattr__(
            self,
            "_plan_digest",
            digest({
                "candidate": deep_thaw(body),
                "runner_attestation": deep_thaw(attestation),
            }),
        )
        resource_identity = {
            key: deep_thaw(body[key])
            for key in (
                "machine", "mounts", "egress", "bootstrap", "export",
                "teardown",
            )
        }
        resource_identity["runner_attestation"] = deep_thaw(attestation)
        object.__setattr__(
            self,
            "_resource_identity",
            deep_freeze(resource_identity),
        )

    def __getattr__(self, name: str) -> Any:
        if name in TOP_LEVEL_KEYS:
            return self._body[name]
        raise AttributeError(name)

    def to_dict(self) -> dict[str, Any]:
        return deep_thaw(self._body)

    @property
    def plan_digest(self) -> str:
        return self._plan_digest

    @property
    def resource_identity(self) -> Mapping[str, Any]:
        return self._resource_identity

    def probe_evidence(
        self, probe: str, challenge: str
    ) -> Mapping[str, Any]:
        return deep_freeze({
            "schema_version": 1,
            "probe": probe,
            "challenge": challenge,
            "plan_digest": self._plan_digest,
            "resource_identity": deep_thaw(self._resource_identity),
            "runner_attestation": deep_thaw(self._runner_attestation),
        })


def _validate_runner_attestation(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"schema_version", "instance_id", "session_id"}
        or value.get("schema_version") != 1
        or any(
            not isinstance(value.get(key), str)
            or not value[key].strip()
            or len(value[key].encode("utf-8")) > 256
            or any(character in value[key] for character in "\r\n\0")
            for key in ("instance_id", "session_id")
        )
    ):
        raise AirlockPlanError("OrbStack runner attestation is invalid")
    return dict(value)


def _runner_attestation(runner: OrbStackProbeRunner) -> dict[str, Any]:
    method = getattr(runner, "attest", None)
    if not callable(method):
        raise AirlockPlanError("OrbStack runner must attest instance and session")
    try:
        return _validate_runner_attestation(method())
    except AirlockPlanError:
        raise
    except Exception:
        raise AirlockPlanError("runner attestation is unavailable") from None


def _runner_probe(
    runner: OrbStackProbeRunner,
    probe: str,
    evidence: Mapping[str, Any],
) -> Mapping[str, Any]:
    try:
        return runner.probe(probe, evidence)
    except AirlockPlanError:
        raise
    except Exception:
        raise AirlockPlanError("runner probe is unavailable") from None


def _probe_result(
    plan: AirlockLaunchPlan,
    probe: str,
    challenge: str,
    value: Any,
) -> bool:
    if (
        not isinstance(value, Mapping)
        or set(value) != {
            "passed", "runner_attestation", "challenge", "probe",
            "plan_digest",
        }
        or type(value.get("passed")) is not bool
        or value.get("challenge") != challenge
        or value.get("probe") != probe
        or value.get("plan_digest") != plan.plan_digest
    ):
        raise AirlockPlanError(
            f"probe {probe} must return a closed result and attestation"
        )
    returned_attestation = _validate_runner_attestation(
        value.get("runner_attestation")
    )
    if (
        returned_attestation != plan._runner_attestation
        or not hmac.compare_digest(
            digest(returned_attestation),
            digest(deep_thaw(plan._runner_attestation)),
        )
    ):
        raise AirlockPlanError(
            f"probe {probe} runner attestation changed"
        )
    return value["passed"]


def _mapping(
    value: Any, keys: set[str] | frozenset[str], label: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AirlockPlanError(f"{label} must be an object")
    unknown = set(value) - set(keys)
    missing = set(keys) - set(value)
    if unknown or missing:
        message = (
            f"{label} keys are closed (missing={sorted(missing)}, "
            f"unknown={sorted(unknown)})"
        )
        raise AirlockPlanError(message)
    return value


def _literal(value: Any, expected: Any, label: str) -> None:
    if type(expected) is bool:
        valid = type(value) is bool and value is expected
    else:
        valid = value == expected
    if not valid:
        raise AirlockPlanError(f"{label} is required")


def _strings(
    value: Any, label: str, *, nonempty: bool = False
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or (nonempty and not value):
        raise AirlockPlanError(
            f"{label} must be {'a non-empty ' if nonempty else 'an '}array"
        )
    result = tuple(value)
    if any(not isinstance(item, str) or not item.strip() for item in result):
        raise AirlockPlanError(f"{label} entries must be non-empty strings")
    if len(set(result)) != len(result):
        raise AirlockPlanError(f"{label} entries must be unique")
    return result


def _validate_machine(candidate: Mapping[str, Any]) -> None:
    machine = _mapping(
        candidate["machine"],
        {
            "os",
            "fresh",
            "ephemeral",
            "macos_integration",
            "host_network",
            "peer_network",
            "separate_guest_kernel_required",
        },
        "machine",
    )
    _literal(machine["os"], "linux", "Linux machine")
    _literal(machine["fresh"], True, "fresh machine")
    _literal(machine["ephemeral"], True, "ephemeral machine")
    _literal(machine["macos_integration"], False, "macOS integration disabled")
    _literal(machine["host_network"], False, "host networking disabled")
    _literal(machine["peer_network"], False, "peer networking disabled")
    _literal(
        machine["separate_guest_kernel_required"],
        True,
        "separate guest kernel",
    )


def _validate_mounts(candidate: Mapping[str, Any]) -> set[str]:
    mounts = _mapping(candidate["mounts"], {"inputs", "output"}, "mounts")
    inputs = mounts["inputs"]
    if not isinstance(inputs, (list, tuple)) or not inputs:
        raise AirlockPlanError(
            "explicit read-only inputs must be a non-empty array"
        )
    input_ids: set[str] = set()
    for item in inputs:
        record = _mapping(item, {"id", "mode", "reviewed"}, "input mount")
        identifier = record["id"]
        if (
            not isinstance(identifier, str)
            or not identifier.strip()
            or identifier in input_ids
        ):
            raise AirlockPlanError(
                "input mount IDs must be unique non-empty strings"
            )
        input_ids.add(identifier)
        _literal(record["mode"], "read-only", "read-only inputs")
        _literal(record["reviewed"], True, "reviewed input")
    output = _mapping(
        mounts["output"], {"id", "mode", "narrow"}, "output mount"
    )
    if not isinstance(output["id"], str) or not output["id"].strip():
        raise AirlockPlanError("narrow output requires a non-empty ID")
    if output["id"] in input_ids:
        raise AirlockPlanError(
            "narrow output must be distinct from every input"
        )
    _literal(output["mode"], "write-only", "narrow output mode")
    _literal(output["narrow"], True, "narrow output")
    return input_ids


def _validate_egress(candidate: Mapping[str, Any]) -> None:
    egress = _mapping(
        candidate["egress"],
        {
            "enforcement_owner",
            "default_deny",
            "approved_destinations",
            "harness_can_modify",
        },
        "egress",
    )
    _literal(egress["enforcement_owner"], "root", "root egress enforcement")
    _literal(egress["default_deny"], True, "default-deny egress")
    _literal(egress["harness_can_modify"], False, "root egress enforcement")
    destinations = egress["approved_destinations"]
    if not isinstance(destinations, list):
        raise AirlockPlanError("approved destinations must be an array")
    seen: set[tuple[str, int]] = set()
    for value in destinations:
        destination = _mapping(
            value, DESTINATION_KEYS, "approved destination"
        )
        selector = destination["selector"]
        host = destination["host"]
        port = destination["port"]
        if (
            not isinstance(selector, str)
            or selector not in APPROVED_DESTINATION_CATALOG
            or not isinstance(host, str)
            or re.fullmatch(
                r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host
            ) is None
            or "*" in host
            or host in {"localhost", "default"}
            or type(port) is not int
            or not 1 <= port <= 65535
            or APPROVED_DESTINATION_CATALOG[selector] != (host, port)
        ):
            raise AirlockPlanError(
                "approved destination must exactly match a catalog host and port"
            )
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and (
            address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_unspecified
            or address.is_multicast
        ):
            raise AirlockPlanError(
                "approved destination cannot select a non-public address"
            )
        identity = (host, port)
        if identity in seen:
            raise AirlockPlanError("approved destinations must be unique")
        seen.add(identity)


def _validate_harness(candidate: Mapping[str, Any]) -> None:
    harness = _mapping(
        candidate["harness"],
        {
            "kind",
            "host_gui",
            "principal",
            "unprivileged",
            "sudo",
            "setuid_escalation",
            "network_admin",
            "container_socket",
        },
        "harness",
    )
    if harness["kind"] != "cli" or harness["host_gui"] is not False:
        raise AirlockPlanError("host GUI harnesses are not airlock-capable")
    principal = harness["principal"]
    if (
        not isinstance(principal, str)
        or not principal.strip()
        or principal == "root"
    ):
        raise AirlockPlanError("unprivileged harness principal is required")
    _literal(harness["unprivileged"], True, "unprivileged harness")
    _literal(harness["sudo"], False, "no sudo")
    _literal(
        harness["setuid_escalation"],
        False,
        "unprivileged harness setuid isolation",
    )
    _literal(
        harness["network_admin"],
        False,
        "unprivileged harness network administration",
    )
    _literal(
        harness["container_socket"],
        False,
        "unprivileged harness container isolation",
    )


def _validate_declared_probes(candidate: Mapping[str, Any]) -> None:
    probes = _mapping(
        candidate["probes"], {"tamper_denied", "unreachable"}, "probes"
    )
    tamper = frozenset(
        _strings(probes["tamper_denied"], "tamper probe set", nonempty=True)
    )
    unreachable = frozenset(
        _strings(probes["unreachable"], "reachability probe set", nonempty=True)
    )
    if tamper != TAMPER_PROBES or unreachable != UNREACHABLE_PROBES:
        raise AirlockPlanError(
            "tamper and reachability probe sets must be exact"
        )


def _validate_state_and_memory(candidate: Mapping[str, Any]) -> None:
    state = _mapping(
        candidate["state"],
        {
            "independent_profile",
            "independent_token",
            "independent_session",
            "reuses_oauth_home",
            "reuses_data_plane_token",
        },
        "state",
    )
    _literal(state["independent_profile"], True, "independent profile")
    _literal(state["independent_token"], True, "independent token")
    _literal(state["independent_session"], True, "independent session")
    _literal(
        state["reuses_oauth_home"], False, "independent profile OAuth home"
    )
    _literal(
        state["reuses_data_plane_token"], False, "independent token data plane"
    )

    retention = _mapping(
        candidate["retention"],
        {
            "mode",
            "enable_observations",
            "enable_auto_consolidation",
            "models",
            "refresh_routes",
            "mental_model_generation",
            "memory_defense",
        },
        "retention",
    )
    _literal(retention["mode"], "chunk-only", "chunk-only retention")
    _literal(retention["enable_observations"], False, "observations disabled")
    _literal(
        retention["enable_auto_consolidation"], False, "consolidation disabled"
    )
    _literal(retention["models"], [], "models disabled")
    _literal(retention["refresh_routes"], [], "models disabled refresh routes")
    _literal(
        retention["mental_model_generation"],
        False,
        "models disabled generation",
    )
    _literal(
        retention["memory_defense"],
        "sensitive_data",
        "sensitive-data memory defense",
    )

    recall = _mapping(
        candidate["recall"], {"engineering", "personal", "core"}, "recall"
    )
    _literal(recall["engineering"], False, "no core recall from engineering")
    _literal(recall["personal"], False, "no core recall from personal")
    _literal(recall["core"], False, "no core recall")


def _validate_bootstrap(
    candidate: Mapping[str, Any], input_ids: set[str]
) -> None:
    bootstrap = _mapping(
        candidate["bootstrap"],
        {
            "mount_id",
            "artifact_id",
            "artifact_version",
            "artifact_digest",
            "reviewed",
            "content_classes",
            "excluded_classes",
        },
        "bootstrap",
    )
    for field_name in ("mount_id", "artifact_id", "artifact_version"):
        value = bootstrap[field_name]
        if not isinstance(value, str) or not value.strip():
            raise AirlockPlanError(
                f"bootstrap {field_name} must be non-empty"
            )
    if bootstrap["mount_id"] not in input_ids:
        raise AirlockPlanError(
            "bootstrap mount_id must reference a declared read-only input"
        )
    if (
        not isinstance(bootstrap["artifact_digest"], str)
        or len(bootstrap["artifact_digest"]) != 64
        or any(
            character not in "0123456789abcdef"
            for character in bootstrap["artifact_digest"]
        )
    ):
        raise AirlockPlanError(
            "bootstrap artifact_digest must be a lowercase SHA-256 digest"
        )
    _literal(bootstrap["reviewed"], True, "reviewed bootstrap")
    content_classes = frozenset(
        _strings(
            bootstrap["content_classes"],
            "bootstrap content classes",
            nonempty=True,
        )
    )
    if content_classes != {
        "transferable_engineering_principles",
        "security_rules",
    }:
        raise AirlockPlanError(
            "bootstrap content classes must be exact and non-sensitive"
        )
    excluded_classes = frozenset(
        _strings(
            bootstrap["excluded_classes"],
            "bootstrap excluded classes",
            nonempty=True,
        )
    )
    if excluded_classes != {
        "personal_content",
        "project_facts",
        "credentials",
        "operational_state",
    }:
        raise AirlockPlanError(
            "bootstrap excluded classes must cover every sensitive class"
        )


def _validate_closeout(candidate: Mapping[str, Any]) -> None:
    export = _mapping(
        candidate["export"], {"encrypted", "verify_before_teardown"}, "export"
    )
    _literal(export["encrypted"], True, "encrypted export")
    _literal(export["verify_before_teardown"], True, "verified export")
    bridge = _mapping(
        candidate["bridge"],
        {
            "source_citations_required",
            "candidate_dispositions_required",
            "promotion_is_separate",
        },
        "bridge",
    )
    _literal(bridge["source_citations_required"], True, "source-cited bridge")
    _literal(
        bridge["candidate_dispositions_required"],
        True,
        "bridge candidate dispositions",
    )
    _literal(bridge["promotion_is_separate"], True, "separate bridge promotion")
    teardown = _mapping(
        candidate["teardown"],
        {"immediate", "delete_bank", "delete_profile", "delete_machine"},
        "teardown",
    )
    _literal(teardown["immediate"], True, "immediate teardown")
    _literal(teardown["delete_bank"], True, "immediate teardown bank deletion")
    _literal(
        teardown["delete_profile"], True, "immediate teardown profile deletion"
    )
    _literal(
        teardown["delete_machine"], True, "immediate teardown machine deletion"
    )


def validate_airlock_plan(
    candidate: Mapping[str, Any], runner: OrbStackProbeRunner
) -> AirlockLaunchPlan:
    """Validate configuration and evidence, returning a deep-frozen plan.

    The runner is a probe-only dependency. This module neither imports an
    OrbStack client nor executes a process. Validation stays independently
    testable, and plan construction cannot launch a machine.
    """

    candidate = deep_thaw(_mapping(candidate, TOP_LEVEL_KEYS, "airlock plan"))
    if (
        type(candidate["schema_version"]) is not int
        or candidate["schema_version"] != 1
    ):
        raise AirlockPlanError("schema_version must be integer 1")
    _literal(candidate["backend"], "orbstack", "OrbStack backend")
    _validate_machine(candidate)
    input_ids = _validate_mounts(candidate)
    _validate_egress(candidate)
    _validate_harness(candidate)
    _validate_declared_probes(candidate)
    _validate_state_and_memory(candidate)
    _validate_bootstrap(candidate, input_ids)
    _validate_closeout(candidate)

    probe_method = getattr(runner, "probe", None)
    if not callable(probe_method):
        raise AirlockPlanError("OrbStack runner must provide the probe seam")
    attestation = _runner_attestation(runner)
    plan = AirlockLaunchPlan(candidate, _VALIDATED_PLAN, attestation)
    for probe in PREFLIGHT_PROBES:
        challenge = secrets.token_hex(32)
        outcome = _runner_probe(
            runner,
            probe, plan.probe_evidence(probe, challenge)
        )
        if not _probe_result(plan, probe, challenge, outcome):
            raise AirlockPlanError(f"required airlock probe failed: {probe}")
    return plan


def validate_airlock_closeout(
    plan: AirlockLaunchPlan, runner: OrbStackProbeRunner
) -> None:
    """Verify ordered post-execution export, disposition, and teardown gates."""

    if not isinstance(plan, AirlockLaunchPlan):
        raise AirlockPlanError("closeout requires a validated launch plan")
    _validate_closeout(plan._body)
    probe_method = getattr(runner, "probe", None)
    if not callable(probe_method):
        raise AirlockPlanError("OrbStack runner must provide the probe seam")
    if not hmac.compare_digest(
        digest(_runner_attestation(runner)),
        digest(deep_thaw(plan._runner_attestation)),
    ):
        raise AirlockPlanError("OrbStack runner attestation changed")
    for probe in CLOSEOUT_PROBES:
        challenge = secrets.token_hex(32)
        outcome = _runner_probe(
            runner,
            probe, plan.probe_evidence(probe, challenge)
        )
        if not _probe_result(plan, probe, challenge, outcome):
            raise AirlockPlanError(f"required airlock probe failed: {probe}")
