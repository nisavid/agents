"""Digest-bound reconciliation with adapter-attested rollback and migration gates."""

from contextlib import ExitStack
from dataclasses import dataclass
import hmac
import os
from pathlib import Path
import re
from typing import Any, Mapping

from .action_contracts import MUTATION_ACTION_KINDS
from .adapters import Adapter, RollbackBundle
from .canonical import digest
from .file_evidence import (
    FileEvidenceError,
    read_file_evidence,
    verified_file_snapshot,
)
from .model import Action, EndpointIdentity, OperationSnapshot, Plan, deep_freeze, deep_thaw
from .planning import (
    PlanError,
    _compatibility,
    _endpoint,
    plan_from_dict,
    verify_plan,
)


DIGEST = re.compile(r"[0-9a-f]{64}\Z")
SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
BANK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
MAX_GATE_FILE_BYTES = 1024 * 1024


class ApplyError(ValueError):
    pass


@dataclass(frozen=True)
class MigrationGateDescriptor:
    completion_marker: str
    proposal_log: str
    completion_marker_digest: str
    proposal_log_digest: str


@dataclass(frozen=True)
class MutationPlan:
    """Closed destructive plan isolated from ordinary non-destructive plans."""

    base_plan: Plan
    plan_kind: str
    migration_run_id: str
    migration_artifact_digest: str
    rollback_archive_digest: str
    rollback_restore_evidence_digest: str
    migration_gate_evidence: Mapping[str, str] | None
    actions: tuple[Action, ...]
    plan_digest: str

    @property
    def target_endpoint(self): return self.base_plan.target_endpoint
    @property
    def live_state_digest(self): return self.base_plan.live_state_digest

    def body(self) -> dict[str, Any]:
        return {
            "base_plan": self.base_plan.to_dict(), "plan_kind": self.plan_kind,
            "migration_run_id": self.migration_run_id,
            "migration_artifact_digest": self.migration_artifact_digest,
            "rollback_archive_digest": self.rollback_archive_digest,
            "rollback_restore_evidence_digest": self.rollback_restore_evidence_digest,
            "migration_gate_evidence": deep_thaw(self.migration_gate_evidence),
            "actions": [action.to_dict() for action in self.actions], "destructive": True,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.body(), "plan_digest": self.plan_digest}


@dataclass(frozen=True)
class ApplyResult:
    status: str
    reason: str
    applied_action_ids: tuple[str, ...] = ()
    rollback_attempted: bool = False
    rollback_succeeded: bool = False
    activation_enabled: bool | None = None
    ledger: tuple[Mapping[str, str], ...] = ()
    recovery_action_ids: tuple[str, ...] = ()


def _mutation_actions(
    values: Any,
    target_profile: str,
    target_endpoint: EndpointIdentity,
) -> tuple[Action, ...]:
    if not isinstance(values, (list, tuple)) or not values:
        raise ApplyError("mutation actions must be a non-empty array")
    result = []
    seen = set()
    for value in values:
        if not isinstance(value, Mapping) or set(value) - {"id", "kind", "artifact_digest", "archive_digest", "restore_evidence_digest", "source_bank", "target_bank"}:
            raise ApplyError("mutation action schema is closed")
        identifier, kind = value.get("id"), value.get("kind")
        if not isinstance(identifier, str) or SAFE_IDENTIFIER.fullmatch(identifier) is None or identifier in seen:
            raise ApplyError("mutation action id is invalid or duplicated")
        if kind not in MUTATION_ACTION_KINDS:
            raise ApplyError("mutation action kind is not permitted")
        artifact = value.get("artifact_digest")
        if not isinstance(artifact, str) or DIGEST.fullmatch(artifact) is None:
            raise ApplyError("mutation action artifact digest is required")
        details = {key: deep_thaw(item) for key, item in value.items() if key not in {"id", "kind"}}
        if not isinstance(details.get("archive_digest"), str) or DIGEST.fullmatch(details["archive_digest"]) is None:
            raise ApplyError("mutation archive digest is required")
        if not isinstance(details.get("restore_evidence_digest"), str) or DIGEST.fullmatch(details["restore_evidence_digest"]) is None:
            raise ApplyError("mutation restore evidence digest is required")
        if not all(bank_key in details for bank_key in ("source_bank", "target_bank")):
            raise ApplyError("mutation source and target bank references are required")
        for bank_key in ("source_bank", "target_bank"):
            bank = details[bank_key]
            if (
                not isinstance(bank, dict)
                or set(bank) not in (
                    {"profile_id", "bank_id"},
                    {"profile_id", "bank_id", "endpoint"},
                )
            ):
                raise ApplyError("mutation bank reference is closed")
            if (
                not isinstance(bank["profile_id"], str)
                    or SAFE_IDENTIFIER.fullmatch(bank["profile_id"])
                    is None
                    or not isinstance(bank["bank_id"], str)
                    or BANK_ID.fullmatch(bank["bank_id"]) is None
            ):
                raise ApplyError("mutation bank reference is invalid")
            if bank["profile_id"] != target_profile:
                raise ApplyError("mutation bank reference must match the target profile")
            if "endpoint" in bank:
                try:
                    endpoint = _endpoint(bank["endpoint"], target_profile)
                except PlanError as error:
                    raise ApplyError("mutation bank endpoint is invalid") from error
                if endpoint != target_endpoint:
                    raise ApplyError(
                        "mutation bank endpoint must match the target endpoint"
                    )
        if details["source_bank"] == details["target_bank"]:
            raise ApplyError("mutation source and target banks must be distinct")
        result.append(Action(identifier, kind, deep_freeze(details)))
        seen.add(identifier)
    return tuple(result)


def build_mutation_plan(base_plan: Plan, *, migration_run_id: str, migration_artifact_digest: str,
                        rollback_archive_digest: str,
                        rollback_restore_evidence_digest: str,
                        actions: Any,
                        migration_gate: MigrationGateDescriptor | None = None) -> MutationPlan:
    verify_plan(base_plan)
    if base_plan.actions:
        raise ApplyError("mutation base plan must not contain actions")
    if not isinstance(migration_run_id, str) or SAFE_IDENTIFIER.fullmatch(migration_run_id) is None:
        raise ApplyError("migration run ID is invalid")
    if not isinstance(migration_artifact_digest, str) or DIGEST.fullmatch(migration_artifact_digest) is None:
        raise ApplyError("migration artifact digest is invalid")
    if not isinstance(rollback_archive_digest, str) or DIGEST.fullmatch(rollback_archive_digest) is None:
        raise ApplyError("rollback archive digest is invalid")
    if (
        not isinstance(rollback_restore_evidence_digest, str)
        or DIGEST.fullmatch(rollback_restore_evidence_digest) is None
    ):
        raise ApplyError("rollback restore evidence digest is invalid")
    gate_evidence = None
    if migration_gate is not None:
        gate_run_id, gate_artifact = parse_migration_gate(migration_gate)
        if gate_run_id != migration_run_id or gate_artifact != migration_artifact_digest:
            raise ApplyError("migration gate does not match the mutation plan")
        gate_evidence = deep_freeze({
            "completion_marker": migration_gate.completion_marker,
            "proposal_log": migration_gate.proposal_log,
            "completion_marker_digest": migration_gate.completion_marker_digest,
            "proposal_log_digest": migration_gate.proposal_log_digest,
        })
    normalized = _mutation_actions(
        actions, base_plan.target_profile, base_plan.target_endpoint
    )
    for action in normalized:
        if action.details["artifact_digest"] != migration_artifact_digest:
            raise ApplyError(
                "mutation action must match the migration artifact digest"
            )
        if action.details["archive_digest"] == rollback_archive_digest:
            raise ApplyError(
                "mutation archive must be distinct from the rollback archive"
            )
        if (
            action.details["restore_evidence_digest"]
            == rollback_restore_evidence_digest
        ):
            raise ApplyError(
                "mutation restore evidence must be distinct from rollback evidence"
            )
    body = {
        "base_plan": base_plan.to_dict(), "plan_kind": "migration", "migration_run_id": migration_run_id,
        "migration_artifact_digest": migration_artifact_digest,
        "rollback_archive_digest": rollback_archive_digest,
        "rollback_restore_evidence_digest": rollback_restore_evidence_digest,
        "migration_gate_evidence": deep_thaw(gate_evidence),
        "actions": [action.to_dict() for action in normalized], "destructive": True,
    }
    return MutationPlan(
        base_plan, "migration", migration_run_id, migration_artifact_digest,
        rollback_archive_digest, rollback_restore_evidence_digest,
        gate_evidence, normalized, digest(body),
    )


def verify_mutation_plan(plan: MutationPlan) -> None:
    _rebuild_mutation_plan(plan)


def _rebuild_mutation_plan(plan: MutationPlan) -> MutationPlan:
    if type(plan) is not MutationPlan or plan.plan_kind != "migration":
        raise ApplyError("mutation plan kind is invalid")
    gate_evidence = deep_thaw(plan.migration_gate_evidence)
    if gate_evidence is not None and (
        not isinstance(gate_evidence, dict)
        or set(gate_evidence) != {
            "completion_marker", "proposal_log", "completion_marker_digest",
            "proposal_log_digest",
        }
        or not all(isinstance(value, str) for value in gate_evidence.values())
        or not all(
            DIGEST.fullmatch(gate_evidence[key]) is not None
            for key in ("completion_marker_digest", "proposal_log_digest")
        )
    ):
        raise ApplyError("mutation gate evidence is invalid")
    migration_gate = (
        None
        if gate_evidence is None
        else MigrationGateDescriptor(**gate_evidence)
    )
    rebuilt = build_mutation_plan(
        plan_from_dict(plan.base_plan.to_dict()),
        migration_run_id=plan.migration_run_id,
        migration_artifact_digest=plan.migration_artifact_digest,
        rollback_archive_digest=plan.rollback_archive_digest,
        rollback_restore_evidence_digest=plan.rollback_restore_evidence_digest,
        actions=[action.to_dict() for action in plan.actions],
        migration_gate=migration_gate,
    )
    if not hmac.compare_digest(digest(rebuilt.to_dict()), digest(plan.to_dict())):
        raise ApplyError("mutation plan digest does not match")
    return rebuilt


def bind_migration_gate(
    plan: MutationPlan, migration_gate: MigrationGateDescriptor
) -> MutationPlan:
    """Return the same mutation proposal bound to trusted file evidence."""

    verify_mutation_plan(plan)
    return build_mutation_plan(
        plan.base_plan,
        migration_run_id=plan.migration_run_id,
        migration_artifact_digest=plan.migration_artifact_digest,
        rollback_archive_digest=plan.rollback_archive_digest,
        rollback_restore_evidence_digest=plan.rollback_restore_evidence_digest,
        actions=[action.to_dict() for action in plan.actions],
        migration_gate=migration_gate,
    )


def mutation_plan_from_dict(value: Any) -> MutationPlan:
    keys = {"base_plan", "plan_kind", "migration_run_id", "migration_artifact_digest", "rollback_archive_digest", "rollback_restore_evidence_digest", "migration_gate_evidence", "actions", "destructive", "plan_digest"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ApplyError("mutation plan schema is closed")
    if value["plan_kind"] != "migration" or value["destructive"] is not True:
        raise ApplyError("mutation plan kind and destructive marker are required")
    try:
        base = plan_from_dict(value["base_plan"])
    except PlanError as error:
        raise ApplyError("mutation base plan is invalid") from error
    migration_gate = None
    evidence = value["migration_gate_evidence"]
    if evidence is not None:
        if not isinstance(evidence, dict) or set(evidence) != {
            "completion_marker", "proposal_log", "completion_marker_digest",
            "proposal_log_digest",
        }:
            raise ApplyError("mutation gate evidence is invalid")
        migration_gate = capture_migration_gate(
            evidence["completion_marker"], evidence["proposal_log"]
        )
        if any(
            getattr(migration_gate, key) != evidence[key]
            for key in evidence
        ):
            raise ApplyError("migration gate evidence changed")
    plan = build_mutation_plan(
        base, migration_run_id=value["migration_run_id"],
        migration_artifact_digest=value["migration_artifact_digest"],
        rollback_archive_digest=value["rollback_archive_digest"],
        rollback_restore_evidence_digest=value["rollback_restore_evidence_digest"],
        actions=value["actions"], migration_gate=migration_gate,
    )
    if not isinstance(value["plan_digest"], str) or not hmac.compare_digest(plan.plan_digest, value["plan_digest"]):
        raise ApplyError("mutation plan digest does not match")
    return plan


def _absolute_gate_path(value: str | Path, label: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise ApplyError(f"{label} path must be absolute")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ApplyError(f"{label} path must be absolute")
    return path


def _read_gate_file(path: Path, label: str) -> tuple[str, str]:
    try:
        evidence = read_file_evidence(path, label, max_bytes=MAX_GATE_FILE_BYTES)
    except FileEvidenceError as error:
        raise ApplyError(str(error)) from None
    assert evidence is not None
    raw, artifact_digest = evidence
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ApplyError(f"{label} must be UTF-8 text") from None
    return text, artifact_digest


def _gate_record(lines: list[str], label: str) -> tuple[str, str]:
    content = [line for line in lines if line]
    if len(content) != 2 or any("=" not in line for line in content):
        raise ApplyError(f"{label} must contain run and artifact")
    fields: dict[str, str] = {}
    for line in content:
        key, value = line.split("=", 1)
        if key in fields or key not in {"run", "artifact"}:
            raise ApplyError(f"{label} fields are closed")
        fields[key] = value
    if set(fields) != {"run", "artifact"}:
        raise ApplyError(f"{label} must contain run and artifact")
    run_id, artifact = fields["run"], fields["artifact"]
    if SAFE_IDENTIFIER.fullmatch(run_id) is None or DIGEST.fullmatch(artifact) is None:
        raise ApplyError(f"{label} run or artifact is invalid")
    return run_id, artifact


def _parse_completion_marker(value: str) -> tuple[str, str]:
    return _gate_record(value.splitlines(), "completion marker")


def _parse_proposal_log(value: str) -> tuple[tuple[str, str], ...]:
    lines = value.splitlines()
    entries: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        if line != "## Migration complete":
            continue
        end = index + 1
        while end < len(lines) and not lines[end].startswith("## "):
            end += 1
        entries.append(_gate_record(lines[index + 1:end], "proposal log migration-complete entry"))
    if not entries:
        raise ApplyError("proposal log has no Migration complete entry")
    return tuple(entries)


def capture_migration_gate(completion_marker: str | Path, proposal_log: str | Path) -> MigrationGateDescriptor:
    marker_path = _absolute_gate_path(completion_marker, "completion marker")
    proposal_path = _absolute_gate_path(proposal_log, "proposal log")
    marker_text, marker_digest = _read_gate_file(marker_path, "completion marker")
    proposal_text, proposal_digest = _read_gate_file(proposal_path, "proposal log")
    marker = _parse_completion_marker(marker_text)
    if marker not in _parse_proposal_log(proposal_text):
        raise ApplyError("migration gate sources do not match")
    return MigrationGateDescriptor(
        str(marker_path),
        str(proposal_path),
        marker_digest,
        proposal_digest,
    )


def parse_migration_gate(gate: MigrationGateDescriptor) -> tuple[str, str]:
    if type(gate) is not MigrationGateDescriptor:
        raise ApplyError("migration gate requires a file-backed descriptor")
    if not all(
        isinstance(value, str) and DIGEST.fullmatch(value) is not None
        for value in (gate.completion_marker_digest, gate.proposal_log_digest)
    ):
        raise ApplyError("migration gate descriptor digests are invalid")
    marker_path = _absolute_gate_path(gate.completion_marker, "completion marker")
    proposal_path = _absolute_gate_path(gate.proposal_log, "proposal log")
    marker_text, marker_digest = _read_gate_file(marker_path, "completion marker")
    proposal_text, proposal_digest = _read_gate_file(proposal_path, "proposal log")
    if not hmac.compare_digest(marker_digest, gate.completion_marker_digest) or not hmac.compare_digest(
        proposal_digest, gate.proposal_log_digest
    ):
        raise ApplyError("migration gate sources changed")
    marker = _parse_completion_marker(marker_text)
    if marker not in _parse_proposal_log(proposal_text):
        raise ApplyError("migration gate sources do not match")
    return marker


def _verify_execution_plan(plan: Plan | MutationPlan) -> None:
    if isinstance(plan, MutationPlan):
        verify_mutation_plan(plan)
    else:
        verify_plan(plan)


def _canonical_execution_plan(plan: Plan | MutationPlan) -> Plan | MutationPlan:
    if type(plan) is MutationPlan:
        return _rebuild_mutation_plan(plan)
    if type(plan) is not Plan:
        raise ApplyError("execution plan type is invalid")
    return plan_from_dict(plan.to_dict())


def create_rollback_bundle(plan: Plan | MutationPlan, adapter: Adapter) -> RollbackBundle:
    _verify_execution_plan(plan)
    bindings = {}
    if isinstance(plan, MutationPlan):
        bindings = {
            "archive_digest": plan.rollback_archive_digest,
            "restore_evidence_digest": plan.rollback_restore_evidence_digest,
        }
    return adapter.create_rollback_bundle(
        plan.plan_digest,
        tuple(action.id for action in plan.actions),
        **bindings,
    )


def _refused(reason: str) -> ApplyResult:
    return ApplyResult("refused", reason)


def _closed_compatibility(value: Any) -> tuple[Mapping[str, Any], ...] | None:
    try:
        return _compatibility(deep_thaw(value))
    except (PlanError, TypeError, AttributeError):
        return None


def _compatibility_satisfied(
    compatibility: tuple[Mapping[str, Any], ...],
) -> bool:
    return bool(compatibility) and not any(
        result.get("compatible") is not True
        or result.get("status")
        in {"fail", "blocked", "unknown", "degraded"}
        or result.get("activatable", True) is not True
        or bool(result.get("blocked_by", ()))
        or result.get("provider_state")
        in {"blocked_candidate", "incompatible"}
        for result in compatibility
    )


def _compatibility_identity(
    compatibility: tuple[Mapping[str, Any], ...],
) -> tuple[Mapping[str, Any], ...]:
    outcome_keys = {"compatible", "reason_code", "status", "provider_state"}
    return tuple(
        {
            key: deep_thaw(value)
            for key, value in result.items()
            if key not in outcome_keys
        }
        for result in compatibility
    )


def _operations_idle(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == {"idle", "active"}
        and value["idle"] is True
        and value["active"] == []
    )


@dataclass(frozen=True)
class _MigrationGatePhase:
    descriptor: MigrationGateDescriptor
    run_id: str
    artifact_digest: str
    completion_marker_text: str
    proposal_log_text: str


def _pin_migration_gate(
    phase: _MigrationGatePhase,
) -> ExitStack:
    """Retain immutable evidence snapshots for the entire mutation window."""
    stack = ExitStack()
    try:
        marker = stack.enter_context(
            verified_file_snapshot(
                phase.descriptor.completion_marker,
                "completion marker",
                phase.descriptor.completion_marker_digest,
                max_bytes=MAX_GATE_FILE_BYTES,
            )
        )
        proposal = stack.enter_context(
            verified_file_snapshot(
                phase.descriptor.proposal_log,
                "proposal log",
                phase.descriptor.proposal_log_digest,
                max_bytes=MAX_GATE_FILE_BYTES,
            )
        )
        marker_raw = os.pread(marker.fileno(), MAX_GATE_FILE_BYTES + 1, 0)
        proposal_raw = os.pread(proposal.fileno(), MAX_GATE_FILE_BYTES + 1, 0)
        marker_text = marker_raw.decode("utf-8")
        proposal_text = proposal_raw.decode("utf-8")
        expected = (phase.run_id, phase.artifact_digest)
        if (
            len(marker_raw) > MAX_GATE_FILE_BYTES
            or len(proposal_raw) > MAX_GATE_FILE_BYTES
            or _parse_completion_marker(marker_text) != expected
            or expected not in _parse_proposal_log(proposal_text)
        ):
            raise ApplyError("migration gate sources changed")
        return stack
    except (ApplyError, FileEvidenceError, OSError, UnicodeDecodeError):
        stack.close()
        raise ApplyError("migration gate sources changed") from None


def _prepare_migration_gate_phase(
    plan: Plan | MutationPlan, gate: Mapping[str, Any] | None
) -> _MigrationGatePhase | ApplyResult | None:
    if not isinstance(plan, MutationPlan):
        if isinstance(gate, Mapping) and set(gate) - {"rollback_bundle"}:
            return _refused("apply_gate_invalid")
        return None
    if plan.migration_gate_evidence is None:
        return _refused("migration_gate_required")
    if not isinstance(gate, Mapping) or "migration_gate" not in gate:
        return _refused("migration_gate_required")
    if set(gate) != {"rollback_bundle", "migration_gate"}:
        return _refused("migration_gate_mismatch")
    descriptor = gate["migration_gate"]
    try:
        run_id, artifact = parse_migration_gate(descriptor)
    except ApplyError:
        return _refused("migration_gate_mismatch")
    if run_id != plan.migration_run_id or artifact != plan.migration_artifact_digest:
        return _refused("migration_gate_mismatch")
    supplied_evidence = {
        "completion_marker": descriptor.completion_marker,
        "proposal_log": descriptor.proposal_log,
        "completion_marker_digest": descriptor.completion_marker_digest,
        "proposal_log_digest": descriptor.proposal_log_digest,
    }
    if not hmac.compare_digest(
        digest(supplied_evidence), digest(plan.migration_gate_evidence)
    ):
        return _refused("migration_gate_mismatch")
    try:
        marker_text, marker_digest = _read_gate_file(
            Path(descriptor.completion_marker), "completion marker"
        )
        proposal_text, proposal_digest = _read_gate_file(
            Path(descriptor.proposal_log), "proposal log"
        )
        marker_record = _parse_completion_marker(marker_text)
        proposal_records = _parse_proposal_log(proposal_text)
    except ApplyError:
        return _refused("migration_gate_mismatch")
    if (
        not hmac.compare_digest(
            marker_digest, descriptor.completion_marker_digest
        )
        or not hmac.compare_digest(
            proposal_digest, descriptor.proposal_log_digest
        )
        or marker_record != (run_id, artifact)
        or (run_id, artifact) not in proposal_records
    ):
        return _refused("migration_gate_mismatch")
    return _MigrationGatePhase(
        descriptor, run_id, artifact, marker_text, proposal_text
    )


def _prepare_rollback_phase(
    plan: Plan | MutationPlan, gate: Mapping[str, Any] | None
) -> RollbackBundle | ApplyResult:
    if not isinstance(gate, Mapping) or not isinstance(
        gate.get("rollback_bundle"), RollbackBundle
    ):
        return _refused("rollback_bundle_required")
    rollback = gate["rollback_bundle"]
    if (
        rollback.plan_digest != plan.plan_digest
        or rollback.action_ids != tuple(action.id for action in plan.actions)
    ):
        return _refused("rollback_bundle_mismatch")
    if isinstance(plan, MutationPlan) and (
        rollback.archive_digest != plan.rollback_archive_digest
        or rollback.restore_evidence_digest
        != plan.rollback_restore_evidence_digest
    ):
        return _refused("rollback_bundle_mismatch")
    return rollback


def apply_plan(plan: Plan | MutationPlan, adapter: Adapter, approval_digest: str,
               gate: Mapping[str, Any] | None) -> ApplyResult:
    try:
        plan = _canonical_execution_plan(plan)
    except ApplyError as error:
        if type(plan) is MutationPlan and "migration gate" in str(error):
            return _refused("migration_gate_mismatch")
        return _refused("invalid_or_destructive_plan")
    except (PlanError, TypeError, AttributeError):
        return _refused("invalid_or_destructive_plan")
    if isinstance(plan, MutationPlan) and plan.base_plan.actions:
        return _refused("invalid_or_destructive_plan")
    if not isinstance(approval_digest, str) or not hmac.compare_digest(approval_digest, plan.plan_digest):
        return _refused("approval_digest_mismatch")
    compatibility = (
        plan.base_plan.compatibility
        if isinstance(plan, MutationPlan)
        else plan.compatibility
    )
    planned_compatibility = _closed_compatibility(compatibility)
    if (
        planned_compatibility is None
        or not _compatibility_satisfied(planned_compatibility)
    ):
        return _refused("compatibility_not_satisfied")
    migration_gate_phase = _prepare_migration_gate_phase(plan, gate)
    if isinstance(migration_gate_phase, ApplyResult):
        return migration_gate_phase
    rollback_phase = _prepare_rollback_phase(plan, gate)
    if isinstance(rollback_phase, ApplyResult):
        return rollback_phase
    rollback = rollback_phase
    try:
        fresh = adapter.snapshot()
    except Exception:
        return _refused("fresh_state_unavailable")
    if not isinstance(fresh, Mapping):
        return _refused("fresh_state_unavailable")
    fresh_compatibility = _closed_compatibility(
        fresh.get("compatibility")
    )
    if fresh_compatibility is None:
        return _refused("fresh_compatibility_unavailable")
    if not _compatibility_satisfied(fresh_compatibility):
        return _refused("compatibility_not_satisfied")
    if not hmac.compare_digest(
        digest(_compatibility_identity(fresh_compatibility)),
        digest(_compatibility_identity(planned_compatibility)),
    ):
        return _refused("fresh_compatibility_changed")
    if fresh.get("endpoint") != plan.target_endpoint.to_dict():
        return _refused("endpoint_identity_drift")
    fresh_state_digest = digest(fresh.get("state"))
    if fresh_state_digest != plan.live_state_digest:
        return _refused("live_state_drift")
    operations = fresh.get("operations")
    if not _operations_idle(operations):
        return _refused("operations_not_idle")
    if rollback.prestate_digest != plan.live_state_digest or rollback.prestate_digest != fresh_state_digest:
        return _refused("rollback_prestate_mismatch")
    fresh_endpoint_digest = digest(fresh.get("endpoint"))
    expected_endpoint_digest = digest(plan.target_endpoint.to_dict())
    if rollback.endpoint_digest != expected_endpoint_digest or rollback.endpoint_digest != fresh_endpoint_digest:
        return _refused("rollback_endpoint_mismatch")
    try:
        if adapter.verify_rollback_bundle(rollback) is not True:
            return _refused("disposable_restore_proof_required")
    except Exception:
        return _refused("disposable_restore_proof_required")
    migration_gate_pin: ExitStack | None = None
    if migration_gate_phase is not None:
        run_id = migration_gate_phase.run_id
        artifact = migration_gate_phase.artifact_digest
        if run_id != plan.migration_run_id or artifact != plan.migration_artifact_digest:
            return _refused("migration_gate_mismatch")
        if (
            _parse_completion_marker(
                migration_gate_phase.completion_marker_text
            )
            != (run_id, artifact)
            or (run_id, artifact)
            not in _parse_proposal_log(migration_gate_phase.proposal_log_text)
        ):
            return _refused("migration_gate_mismatch")
        try:
            migration_gate_pin = _pin_migration_gate(migration_gate_phase)
        except ApplyError:
            return _refused("migration_gate_mismatch")

    def finish(result: ApplyResult) -> ApplyResult:
        if migration_gate_pin is not None:
            migration_gate_pin.close()
        return result

    bind_apply_plan = getattr(adapter, "bind_apply_plan", None)
    if plan.actions and not callable(bind_apply_plan):
        return finish(_refused("apply_plan_binding_unavailable"))
    if plan.actions:
        try:
            bind_apply_plan(rollback)
        except BaseException as failure:
            if isinstance(failure, Exception):
                return finish(_refused("apply_plan_binding_failed"))
            if migration_gate_pin is not None:
                migration_gate_pin.close()
            raise
        preflight_action = getattr(adapter, "preflight_action", None)
        if not callable(preflight_action):
            return finish(_refused("mutation_preflight_unavailable"))
        try:
            for action in plan.actions:
                preflight_action(action)
        except BaseException as failure:
            if isinstance(failure, Exception):
                return finish(_refused("mutation_preflight_failed"))
            if migration_gate_pin is not None:
                migration_gate_pin.close()
            raise

    ledger: list[Mapping[str, str]] = []
    applied: list[str] = []
    recovery_actions: list[str] = []
    try:
        for action in plan.actions:
            recovery_actions.append(action.id)
            ledger.append({"action_id": action.id, "status": "started"})
            adapter.apply_action(action)
            applied.append(action.id)
            ledger.append({"action_id": action.id, "status": "applied"})
            if adapter.verify_postcondition(action) is not True:
                raise ApplyError("postcondition failed")
            ledger.append({"action_id": action.id, "status": "verified"})
        applied_snapshot = adapter.snapshot()
        applied_compatibility = (
            _closed_compatibility(applied_snapshot.get("compatibility"))
            if isinstance(applied_snapshot, Mapping)
            else None
        )
        if (
            applied_compatibility is None
            or not _compatibility_satisfied(applied_compatibility)
            or not hmac.compare_digest(
                digest(_compatibility_identity(applied_compatibility)),
                digest(_compatibility_identity(planned_compatibility)),
            )
            or applied_snapshot.get("endpoint")
            != plan.target_endpoint.to_dict()
            or not _operations_idle(applied_snapshot.get("operations"))
        ):
            raise ApplyError("post-apply provider attestation failed")
        for action in plan.actions:
            if adapter.verify_postcondition(action) is not True:
                raise ApplyError("final postcondition recheck failed")
    except BaseException as failure:
        if not recovery_actions:
            if not isinstance(failure, Exception):
                if migration_gate_pin is not None:
                    migration_gate_pin.close()
                raise
            return finish(_refused("mutation_failed_before_start"))
        ledger.append({"status": "rollback_started"})
        try:
            adapter.restore(rollback)
            restored = adapter.snapshot()
            restored_compatibility = (
                _closed_compatibility(restored.get("compatibility"))
                if isinstance(restored, Mapping)
                else None
            )
            if (
                not isinstance(restored, Mapping)
                or digest(restored.get("endpoint")) != rollback.endpoint_digest
                or digest(restored.get("state")) != rollback.prestate_digest
                or not _operations_idle(restored.get("operations"))
                or restored_compatibility is None
                or not _compatibility_satisfied(restored_compatibility)
                or not hmac.compare_digest(
                    digest(_compatibility_identity(restored_compatibility)),
                    digest(_compatibility_identity(planned_compatibility)),
                )
                or restored.get("endpoint")
                != plan.target_endpoint.to_dict()
            ):
                raise ApplyError("restored rollback snapshot failed verification")
        except BaseException as restore_failure:
            try:
                disabled = adapter.disable_activation()
                if (
                    not isinstance(disabled, Mapping)
                    or set(disabled) != {"activation_enabled"}
                    or disabled["activation_enabled"] is not False
                ):
                    raise ApplyError("activation disable was not attested")
            except BaseException as disable_failure:
                ledger.append({"status": "emergency_disable_failed"})
                interruption = next(
                    (
                        value
                        for value in (failure, restore_failure, disable_failure)
                        if not isinstance(value, Exception)
                    ),
                    None,
                )
                if interruption is not None:
                    if migration_gate_pin is not None:
                        migration_gate_pin.close()
                    raise interruption.with_traceback(interruption.__traceback__)
                return finish(ApplyResult(
                    "operator_blocked", "emergency_disable_failed",
                    tuple(applied), True, False, None, tuple(ledger),
                    tuple(recovery_actions),
                ))
            ledger.append({"status": "operator_blocked"})
            interruption = next(
                (
                    value
                    for value in (failure, restore_failure)
                    if not isinstance(value, Exception)
                ),
                None,
            )
            if interruption is not None:
                if migration_gate_pin is not None:
                    migration_gate_pin.close()
                raise interruption.with_traceback(interruption.__traceback__)
            return finish(ApplyResult(
                "operator_blocked", "rollback_failed", tuple(applied), True,
                False, False, tuple(ledger), tuple(recovery_actions),
            ))
        else:
            ledger.append({"status": "rollback_succeeded"})
            if not isinstance(failure, Exception):
                if migration_gate_pin is not None:
                    migration_gate_pin.close()
                raise failure.with_traceback(failure.__traceback__)
            return finish(ApplyResult(
                "rolled_back",
                "apply_or_postcondition_failed",
                tuple(applied),
                True,
                True,
                None,
                tuple(ledger),
                tuple(recovery_actions),
            ))
    return finish(ApplyResult(
        "applied", "ok", tuple(applied), False, False, None,
        tuple(ledger), tuple(recovery_actions),
    ))
