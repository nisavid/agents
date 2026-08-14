from __future__ import annotations

import os
import stat
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ._activation_support import canonical_value
from ._control_maintenance_support import (
    CONTROL_PREIMAGE_ROLES,
    MAINTENANCE_REPLACEMENT_ROLES,
    ControlMaintenanceFixture,
)
from ._routine_activation_support import (
    JOURNAL_KEYS,
    ROUTINE_CLEANUP_PROCESS_LOSS_CUTS,
    ROUTINE_JOURNAL_PROCESS_LOSS_CUTS,
    DirectorySnapshot,
    RegularFileSnapshot,
    RoutineCleanupStep,
    assert_routine_additive_process_loss_state,
    directory_snapshot,
    expected_active_receipt_inventory,
    receipt_digest_inventory,
    routine_install_temporary_path,
    selector_raws,
    staged_artifact,
    staged_candidate_selector_raws,
    staged_prior_selector_raws,
)
from ._routine_support import smoke_envelope
from ._support import canonical_document, content_document, sha256


@dataclass(frozen=True)
class PreparedControlMaintenanceActivation:
    initial: object
    active: object
    candidate: Path
    request: object
    prepared: object
    authorization_raw: bytes
    staged: object
    activation: object


@dataclass(frozen=True)
class ControlMaintenanceSmokeObservation:
    journal: dict[str, Any]
    active_raw: bytes
    deployment_raw: bytes
    receipt_digests: frozenset[str]


@dataclass(frozen=True)
class ControlMaintenanceCleanupStep:
    operation: str
    role: str
    relative_path: str


@dataclass(frozen=True)
class ControlMaintenanceChildExit:
    exit_status: int
    diagnostic: str


@dataclass(frozen=True)
class ControlMaintenanceRecoveryParentFsyncObservation:
    exit_status: int
    diagnostic: str
    controller_parent_fsyncs: int
    root_parent_fsyncs: int


@dataclass(frozen=True)
class ControlMaintenanceJournalCut:
    current_raw: bytes
    target_raw: bytes


CONTROL_MAINTENANCE_REPLACE_PROCESS_LOSS_EXIT = 73
CONTROL_MAINTENANCE_ADDITIVE_PUBLISH_PROCESS_LOSS_EXIT = 77
CONTROL_MAINTENANCE_CLEANUP_PROCESS_LOSS_EXIT = 78
CONTROL_MAINTENANCE_JOURNAL_PROCESS_LOSS_EXIT = 79
CONTROL_MAINTENANCE_SMOKE_PROCESS_LOSS_EXIT = 80
CONTROL_MAINTENANCE_TERMINAL_PROCESS_LOSS_EXIT = 81
CONTROL_MAINTENANCE_RECOVERY_PARENT_FSYNC_EXIT = 82
CONTROL_MAINTENANCE_REPLACEMENT_PERSISTENCE_CUTS = (
    "temp-create",
    "partial-write",
    "content-fsync",
    "ready-fsync",
    "replace",
    "parent-fsync",
)
_CONTROL_MAINTENANCE_CHILD_FAILURE_EXIT = 74
_CONTROL_MAINTENANCE_UNEXPECTED_RETURN_EXIT = 75
_CONTROL_MAINTENANCE_UNEXPECTED_SMOKE_EXIT = 76


class ControlMaintenanceActivationFixture:
    """Build one exact public complete-control activation input."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.control = ControlMaintenanceFixture(root)

    def deployment(self):
        return self.control.deployment()

    def staged_activation(
        self,
        *,
        distinct_replacement_bytes: bool = False,
    ) -> PreparedControlMaintenanceActivation:
        deployment = self.deployment()
        if distinct_replacement_bytes:
            initial, active, candidate, request = self._distinct_control_scenario()
        else:
            initial, active, candidate, request = self.control.scenario()
        prepared = deployment.prepare_deployment(request)
        authorization_raw = self.control.authorization_raw(prepared)
        staged = deployment.stage_deployment(
            request,
            authorization_raw,
            self.root / "control-maintenance-stage",
        )
        activation = deployment.ActivationRequest(
            deployment=request,
            authorization_raw=authorization_raw,
            stage_receipt=staged.stage_path,
        )
        return PreparedControlMaintenanceActivation(
            initial,
            active,
            candidate,
            request,
            prepared,
            authorization_raw,
            staged,
            activation,
        )

    def _distinct_control_scenario(self):
        initial, active = self.control.routine.activate_initial()
        candidate = self.control.routine.candidate_root()
        comment_targets = (
            candidate / "controller" / "task_witness_deploy.py",
            candidate / "launcher" / "task_witness_launch.py",
            candidate / "client" / "task_witness_client.py",
            candidate / "smoke" / "task_witness_smoke_validator.py",
        )
        for path in comment_targets:
            path.write_bytes(
                path.read_bytes()
                + b"\n# distinct complete-control replacement candidate B\n"
            )

        def change_policy(policy: dict[str, object]) -> None:
            self.control._declare_exact_control_surface(policy)
            policy["providers"] = [
                {
                    "plugin_id": "replacement-cut-provider",
                    "authority_profile": "replacement-cut-authority",
                    "producers": [],
                    "issuers": [],
                    "validators": [],
                }
            ]

        self.control._rewrite_policy(candidate, change_policy)
        request = self.control.routine.request_for_candidate(
            initial.canonical_root,
            active.active_receipt_sha256,
            candidate,
            release_version="1.0.1",
            revision="b" * 40,
            sequence=8,
        )
        qualification = canonical_value(request.runtime_qualification_raw)
        alternative_interpreter = self.root / "qualified-python-b"
        executable = Path(qualification["main_executable"]["path"])
        alternative_interpreter.write_bytes(executable.read_bytes())
        alternative_interpreter.chmod(0o700)
        qualification.pop("content_sha256")
        qualification["main_executable"]["path"] = str(alternative_interpreter)
        request = replace(
            request,
            runtime_qualification_raw=canonical_document(
                content_document(qualification)
            ),
        )
        return initial, active, candidate, request


class AcceptedControlMaintenanceSmoke:
    """Accept candidate smoke while capturing exact control-journal evidence."""

    def __init__(self, prepared: PreparedControlMaintenanceActivation) -> None:
        self.prepared = prepared
        self.output = smoke_envelope(prepared.staged.deployment_value["smoke"])
        self.observations: list[ControlMaintenanceSmokeObservation] = []

    @property
    def phases(self) -> list[str]:
        return [item.journal["phase"] for item in self.observations]

    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        pass_fds: tuple[int, ...],
    ) -> subprocess.CompletedProcess[bytes]:
        root = self.prepared.initial.canonical_root
        if argv != (str(root / "task-witness"), "activation-smoke"):
            raise AssertionError("control maintenance smoke argv disagrees")
        if pass_fds != (3,):
            raise AssertionError("control maintenance smoke descriptor set disagrees")
        journal = canonical_value((root / "transaction.json").read_bytes())
        if frozenset(journal) != JOURNAL_KEYS:
            raise AssertionError("control maintenance journal key set disagrees")
        if journal["transaction_class"] != "control-set-maintenance":
            raise AssertionError("control maintenance transaction class disagrees")
        if journal["phase"] != "candidate-smoke":
            raise AssertionError("control maintenance smoke phase disagrees")
        observation = ControlMaintenanceSmokeObservation(
            journal=journal,
            active_raw=(root / "active.json").read_bytes(),
            deployment_raw=(root / "deployment.json").read_bytes(),
            receipt_digests=receipt_digest_inventory(root),
        )
        assert_control_maintenance_smoke_observation(
            observation,
            self.prepared,
        )
        assert_candidate_control_set_installed(self.prepared.staged)
        self.observations.append(observation)
        return subprocess.CompletedProcess(argv, 0, self.output, b"")


class _ControlMaintenanceRollbackSmoke:
    """Reject exact B, then decide exact A without a phase oracle."""

    def __init__(
        self,
        prepared: PreparedControlMaintenanceActivation,
        *,
        rollback_accepted: bool,
    ) -> None:
        self.prepared = prepared
        self.rollback_accepted = rollback_accepted
        prior_smoke = prepared.staged.rollback_value["prior_activation_unit"]["smoke"]
        self.rollback_output = smoke_envelope(prior_smoke)
        self.observations: list[ControlMaintenanceSmokeObservation] = []

    @property
    def phases(self) -> list[str]:
        return [item.journal["phase"] for item in self.observations]

    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        pass_fds: tuple[int, ...],
    ) -> subprocess.CompletedProcess[bytes]:
        root = self.prepared.initial.canonical_root
        if argv != (str(root / "task-witness"), "activation-smoke"):
            raise AssertionError("control maintenance smoke argv disagrees")
        if pass_fds != (3,):
            raise AssertionError("control maintenance smoke descriptor set disagrees")
        journal = canonical_value((root / "transaction.json").read_bytes())
        if frozenset(journal) != JOURNAL_KEYS:
            raise AssertionError("control maintenance journal key set disagrees")
        if journal["transaction_class"] != "control-set-maintenance":
            raise AssertionError("control maintenance transaction class disagrees")
        candidate_live = control_maintenance_unit_is_installed(
            self.prepared.staged,
            prior=False,
        )
        prior_live = control_maintenance_unit_is_installed(
            self.prepared.staged,
            prior=True,
        )
        if candidate_live == prior_live:
            raise AssertionError(
                "control maintenance smoke did not observe one exact control unit"
            )
        expected_phase = "candidate-smoke" if candidate_live else "rollback-smoke"
        if journal["phase"] != expected_phase:
            raise AssertionError(
                "control maintenance smoke phase disagrees with installed controls"
            )
        observation = ControlMaintenanceSmokeObservation(
            journal=journal,
            active_raw=(root / "active.json").read_bytes(),
            deployment_raw=(root / "deployment.json").read_bytes(),
            receipt_digests=receipt_digest_inventory(root),
        )
        if candidate_live:
            assert_control_maintenance_smoke_observation(
                observation,
                self.prepared,
            )
            assert_candidate_control_set_installed(self.prepared.staged)
            result = subprocess.CompletedProcess(
                argv,
                70,
                b"",
                b"fixture candidate rejects smoke\n",
            )
        else:
            assert_control_maintenance_rollback_smoke_observation(
                observation,
                self.prepared,
            )
            assert_prior_control_set_installed(self.prepared.staged)
            if self.rollback_accepted:
                result = subprocess.CompletedProcess(
                    argv,
                    0,
                    self.rollback_output,
                    b"",
                )
            else:
                result = subprocess.CompletedProcess(
                    argv,
                    70,
                    b"",
                    b"fixture prior rejects smoke\n",
                )
        self.observations.append(observation)
        return result


class RejectCandidateAcceptPriorControlMaintenanceSmoke(
    _ControlMaintenanceRollbackSmoke
):
    """Reject exact B controls, then accept only the fully restored A unit."""

    def __init__(self, prepared: PreparedControlMaintenanceActivation) -> None:
        super().__init__(prepared, rollback_accepted=True)


class RejectCandidateAndPriorControlMaintenanceSmoke(_ControlMaintenanceRollbackSmoke):
    """Reject both exact B and fully restored A control units."""

    def __init__(self, prepared: PreparedControlMaintenanceActivation) -> None:
        super().__init__(prepared, rollback_accepted=False)


class ControlMaintenanceRecoveryPopenAdapter:
    """Run a tiny real child selected only by exact installed control bytes."""

    def __init__(
        self,
        prepared: PreparedControlMaintenanceActivation,
        original_popen: object,
        *,
        candidate_accepted: bool,
    ) -> None:
        self.prepared = prepared
        self.original_popen = original_popen
        self.candidate_accepted = candidate_accepted
        self.markers: list[str] = []
        self.candidate_output = smoke_envelope(
            prepared.staged.deployment_value["smoke"]
        )
        self.prior_output = smoke_envelope(
            prepared.staged.rollback_value["prior_activation_unit"]["smoke"]
        )

    def __call__(self, argv: tuple[str, ...], *args: object, **kwargs: object):
        root = self.prepared.initial.canonical_root
        if args:
            raise AssertionError("control recovery Popen positional options disagree")
        if argv != (str(root / "task-witness"), "activation-smoke"):
            raise AssertionError("control recovery Popen shim argv disagrees")
        if kwargs.get("pass_fds") != (3,):
            raise AssertionError("control recovery Popen descriptor set disagrees")
        candidate_live = control_maintenance_unit_is_installed(
            self.prepared.staged,
            prior=False,
        )
        prior_live = control_maintenance_unit_is_installed(
            self.prepared.staged,
            prior=True,
        )
        if candidate_live == prior_live:
            raise AssertionError(
                "control recovery Popen did not observe one exact control unit"
            )
        if candidate_live:
            self.markers.append("B")
            if self.candidate_accepted:
                stdout = self.candidate_output
                stderr = b""
                exit_status = 0
            else:
                stdout = b""
                stderr = b"fixture candidate rejects smoke\n"
                exit_status = 70
        else:
            self.markers.append("A")
            stdout = self.prior_output
            stderr = b""
            exit_status = 0
        source = (
            "import sys;"
            f"sys.stdout.buffer.write(bytes.fromhex('{stdout.hex()}'));"
            f"sys.stderr.buffer.write(bytes.fromhex('{stderr.hex()}'));"
            f"raise SystemExit({exit_status})"
        )
        return self.original_popen((sys.executable, "-c", source), **kwargs)


def assert_control_maintenance_smoke_observation(
    observation: ControlMaintenanceSmokeObservation,
    prepared: PreparedControlMaintenanceActivation,
) -> None:
    candidate_receipt_sha256, candidate_smoke, _ = (
        _assert_control_maintenance_journal_authority(
            observation,
            prepared,
        )
    )
    root = prepared.initial.canonical_root
    staged = prepared.staged
    journal = observation.journal
    if journal["phase"] != "candidate-smoke":
        raise AssertionError("control maintenance candidate smoke phase disagrees")
    if journal["smoke_handoff"] != {
        "target_deployment_receipt_sha256": candidate_receipt_sha256,
        "smoke_bundle_sha256": candidate_smoke["bundle"]["sha256"],
        "smoke_trust_context_sha256": candidate_smoke["trust_context"]["sha256"],
    }:
        raise AssertionError("control maintenance smoke handoff disagrees")
    if (observation.active_raw, observation.deployment_raw) != (
        staged_candidate_selector_raws(staged)
    ):
        raise AssertionError("control maintenance candidate selectors disagree")
    if selector_raws(root) != staged_candidate_selector_raws(staged):
        raise AssertionError("control maintenance live selectors changed during smoke")


def assert_control_maintenance_rollback_smoke_observation(
    observation: ControlMaintenanceSmokeObservation,
    prepared: PreparedControlMaintenanceActivation,
) -> None:
    _, _, prior_smoke = _assert_control_maintenance_journal_authority(
        observation,
        prepared,
    )
    root = prepared.initial.canonical_root
    staged = prepared.staged
    journal = observation.journal
    prior = _plain(staged.rollback_value["prior_activation_unit"])
    if journal["phase"] != "rollback-smoke":
        raise AssertionError("control maintenance rollback smoke phase disagrees")
    if journal["smoke_handoff"] != {
        "target_deployment_receipt_sha256": prior["deployment_receipt"]["sha256"],
        "smoke_bundle_sha256": prior_smoke["bundle"]["sha256"],
        "smoke_trust_context_sha256": prior_smoke["trust_context"]["sha256"],
    }:
        raise AssertionError("control maintenance rollback handoff disagrees")
    expected_selectors = staged_prior_selector_raws(staged)
    if (observation.active_raw, observation.deployment_raw) != expected_selectors:
        raise AssertionError("control maintenance prior selectors disagree")
    if selector_raws(root) != expected_selectors:
        raise AssertionError("control maintenance prior selectors changed during smoke")


def _assert_control_maintenance_journal_authority(
    observation: ControlMaintenanceSmokeObservation,
    prepared: PreparedControlMaintenanceActivation,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    staged = prepared.staged
    journal = observation.journal
    candidate_receipt_sha256 = sha256(staged.deployment_raw)
    rollback_receipt_sha256 = sha256(staged.rollback_raw)
    prior_deployment_raw = staged_artifact(
        staged,
        "prior-deployment-alias",
    ).raw
    expected_receipts = expected_active_receipt_inventory(
        prior_deployment_raw,
        staged,
    )
    candidate_smoke = _plain(staged.deployment_value["smoke"])
    expected_candidate = {
        "state": "active",
        "deployment_receipt": _plain(
            staged_artifact(staged, "deployment-alias").installed
        ),
        "active_record": _plain(staged_artifact(staged, "active-record").installed),
        "control_set": _plain(staged.deployment_value["control_set"]),
        "smoke": candidate_smoke,
    }
    if journal["candidate"] != expected_candidate:
        raise AssertionError("control maintenance candidate authority disagrees")
    if journal["prior"] != _plain(staged.rollback_value["prior_activation_unit"]):
        raise AssertionError("control maintenance prior authority disagrees")
    if journal["rollback_authority"] != {
        "receipt_path": staged.stage_value["rollback_receipt"]["path"],
        "receipt_sha256": rollback_receipt_sha256,
        "target_state": "active",
    }:
        raise AssertionError("control maintenance rollback authority disagrees")
    control_preimage = _plain(staged.rollback_value["control_preimage"])
    selector_preimage = _plain(staged.rollback_value["selector_preimage"])
    expected_preimage_artifacts = [
        *control_preimage[:-1],
        *selector_preimage,
        control_preimage[-1],
    ]
    if journal["preimage"] != {
        "manifest_path": staged.stage_value["rollback_receipt"]["path"],
        "manifest_sha256": rollback_receipt_sha256,
        "artifacts": expected_preimage_artifacts,
        "external_dependencies": _plain(staged.rollback_value["external_dependencies"]),
    }:
        raise AssertionError("control maintenance complete preimage disagrees")
    if journal["stage"] != {
        "receipt_path": str(staged.stage_path),
        "receipt_sha256": sha256(staged.stage_raw),
        "plan_sha256": staged.stage_value["plan_sha256"],
        "authorization_sha256": staged.stage_value["authorization"]["sha256"],
        "maintenance_transaction_sha256": staged.stage_value[
            "maintenance_transaction_sha256"
        ],
    }:
        raise AssertionError("control maintenance stage authority disagrees")
    if journal["outer_maintenance_transaction_sha256"] != staged.stage_value[
        "maintenance_transaction_sha256"
    ] or journal["activation_lock"] != _plain(staged.rollback_value["activation_lock"]):
        raise AssertionError("control maintenance outer authority disagrees")
    if observation.receipt_digests != expected_receipts:
        raise AssertionError("control maintenance receipt closure disagrees")
    return (
        candidate_receipt_sha256,
        candidate_smoke,
        _plain(staged.rollback_value["prior_activation_unit"]["smoke"]),
    )


def assert_candidate_control_set_installed(staged: object) -> None:
    for role in MAINTENANCE_REPLACEMENT_ROLES:
        artifact = staged_artifact(staged, role)
        path = Path(artifact.installed["path"])
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != artifact.installed["mode"]
            or metadata.st_nlink != 1
            or path.read_bytes() != artifact.raw
        ):
            raise AssertionError(f"installed candidate {role} disagrees")


def assert_prior_control_set_installed(staged: object) -> None:
    for role in MAINTENANCE_REPLACEMENT_ROLES:
        target = staged_artifact(staged, role)
        prior_role = {
            "active-record": "prior-active-record",
            "deployment-alias": "prior-deployment-alias",
        }.get(role, f"prior-{role}")
        prior = staged_artifact(staged, prior_role)
        path = Path(target.installed["path"])
        metadata = path.lstat()
        if (
            Path(prior.installed["path"]) != path
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != prior.installed["mode"]
            or metadata.st_nlink != 1
            or path.read_bytes() != prior.raw
        ):
            raise AssertionError(f"installed prior {role} disagrees")


def assert_control_maintenance_replacement_prefix(
    staged: object,
    *,
    completed_index: int,
) -> None:
    """Assert candidate controls through one index and prior controls after it."""

    if completed_index not in range(len(MAINTENANCE_REPLACEMENT_ROLES)):
        raise AssertionError("control maintenance replacement prefix is invalid")
    for index, role in enumerate(MAINTENANCE_REPLACEMENT_ROLES):
        target = staged_artifact(staged, role)
        selected_role = role
        if index > completed_index:
            selected_role = _prior_control_maintenance_replacement_role(role)
        selected = staged_artifact(staged, selected_role)
        path = Path(target.installed["path"])
        metadata = path.lstat()
        if (
            Path(selected.installed["path"]) != path
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != selected.installed["owner"]
            or stat.S_IMODE(metadata.st_mode) != selected.installed["mode"]
            or metadata.st_nlink != 1
            or path.read_bytes() != selected.raw
        ):
            raise AssertionError(
                f"control maintenance replacement prefix {role} disagrees"
            )


def control_maintenance_unit_is_installed(
    staged: object,
    *,
    prior: bool,
) -> bool:
    for role in MAINTENANCE_REPLACEMENT_ROLES:
        target = staged_artifact(staged, role)
        selected_role = role
        if prior:
            selected_role = {
                "active-record": "prior-active-record",
                "deployment-alias": "prior-deployment-alias",
            }.get(role, f"prior-{role}")
        selected = staged_artifact(staged, selected_role)
        path = Path(target.installed["path"])
        if not path.is_file() or path.read_bytes() != selected.raw:
            return False
    return True


def control_maintenance_additive_artifacts(staged: object) -> tuple[object, ...]:
    replacement_roles = set(MAINTENANCE_REPLACEMENT_ROLES)
    prior_roles = {
        "prior-active-record",
        "prior-deployment-alias",
        *(f"prior-{role}" for role in CONTROL_PREIMAGE_ROLES),
    }
    return tuple(
        item
        for item in staged.artifacts
        if item.role not in replacement_roles and item.role not in prior_roles
    )


def ordered_control_maintenance_additive_artifacts(
    staged: object,
) -> tuple[object, ...]:
    """Project the exact production additive program from the public stage."""

    additive = control_maintenance_additive_artifacts(staged)
    rollback = next(
        (item for item in additive if item.role == "rollback-receipt"),
        None,
    )
    deployment = next(
        (item for item in additive if item.role == "deployment-receipt"),
        None,
    )
    if rollback is None or deployment is None:
        raise AssertionError("control maintenance additive receipts are absent")
    middle = tuple(
        sorted(
            (
                item
                for item in additive
                if item is not rollback and item is not deployment
            ),
            key=lambda item: item.relative_path,
        )
    )
    return (rollback, *middle, deployment)


def control_maintenance_cleanup_steps(
    staged: object,
    baseline_files: Mapping[str, object],
    baseline_directories: Mapping[str, object],
) -> tuple[RoutineCleanupStep, ...]:
    """Derive the exact R-first, deepest-directory, B-last cleanup program."""

    additive = ordered_control_maintenance_additive_artifacts(staged)
    rollback = additive[0]
    deployment = additive[-1]
    owned = tuple(
        artifact
        for artifact in additive
        if artifact.relative_path not in baseline_files
    )
    if rollback not in owned or deployment not in owned:
        raise AssertionError("control maintenance cleanup receipt ownership disagrees")
    middle = tuple(
        sorted(
            (
                artifact
                for artifact in owned
                if artifact is not rollback and artifact is not deployment
            ),
            key=lambda artifact: artifact.relative_path,
        )
    )
    owned_directories: set[str] = set()
    for artifact in owned:
        parent = Path(artifact.relative_path).parent
        while parent != Path("."):
            relative = parent.as_posix()
            if relative not in baseline_directories:
                owned_directories.add(relative)
            parent = parent.parent
    steps: list[RoutineCleanupStep] = []

    def append_artifact(artifact: object) -> None:
        steps.append(
            RoutineCleanupStep(
                operation="remove-artifact",
                index=len(steps),
                role=artifact.role,
                relative_path=artifact.relative_path,
                artifact=artifact,
            )
        )

    append_artifact(rollback)
    for artifact in middle:
        append_artifact(artifact)
    for relative in sorted(
        owned_directories,
        key=lambda item: (item.count("/"), item),
        reverse=True,
    ):
        steps.append(
            RoutineCleanupStep(
                operation="remove-directory",
                index=len(steps),
                role=relative,
                relative_path=relative,
                artifact=None,
            )
        )
    append_artifact(deployment)
    return tuple(steps)


def assert_control_maintenance_additive_persistence_state(
    prepared: PreparedControlMaintenanceActivation,
    baseline_files: dict[str, RegularFileSnapshot],
    *,
    artifact_index: int,
    cut: str,
) -> bytes:
    """Assert one exact journal-bound control additive persistence state."""

    artifacts = ordered_control_maintenance_additive_artifacts(prepared.staged)
    root = prepared.initial.canonical_root
    journal_raw = (root / "transaction.json").read_bytes()
    journal = canonical_value(journal_raw)
    if (
        frozenset(journal) != JOURNAL_KEYS
        or journal["transaction_class"] != "control-set-maintenance"
        or journal["prior"]["state"] != "active"
    ):
        raise AssertionError(
            "control maintenance additive persistence journal disagrees"
        )
    assert_routine_additive_process_loss_state(
        artifacts,
        baseline_files,
        journal,
        artifact_index=artifact_index,
        cut=cut,
    )
    post_publication = {"publish", "temp-unlink", "parent-fsync"}
    for index, artifact in enumerate(artifacts[: artifact_index + 1]):
        if index == artifact_index and cut not in post_publication:
            continue
        path = Path(artifact.installed_path)
        metadata = path.lstat()
        expected_links = 2 if index == artifact_index and cut == "publish" else 1
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != artifact.installed["owner"]
            or stat.S_IMODE(metadata.st_mode) != artifact.installed["mode"]
            or metadata.st_nlink != expected_links
            or path.read_bytes() != artifact.raw
        ):
            raise AssertionError(
                f"control maintenance installed additive {artifact.role} disagrees"
            )
    current = artifacts[artifact_index]
    temporary = routine_install_temporary_path(
        current,
        journal["transaction_id"],
        artifact_index,
    )
    if cut in {"temp-create", "partial-write", "file-fsync", "publish"}:
        metadata = temporary.lstat()
        expected_mode = (
            0
            if cut == "temp-create"
            else (0o600 if cut == "partial-write" else current.installed["mode"])
        )
        expected_links = 2 if cut == "publish" else 1
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != current.installed["owner"]
            or stat.S_IMODE(metadata.st_mode) != expected_mode
            or metadata.st_nlink != expected_links
        ):
            raise AssertionError(
                "control maintenance additive temporary binding disagrees"
            )
    elif temporary.exists():
        raise AssertionError("control maintenance additive temporary remains")
    return journal_raw


def assert_control_maintenance_additive_receipt_inventory(
    prepared: PreparedControlMaintenanceActivation,
    baseline_receipts: frozenset[str],
    *,
    artifact_index: int,
    cut: str,
) -> None:
    """Assert only phase-authorized receipt names and hard-link counts exist."""

    artifacts = ordered_control_maintenance_additive_artifacts(prepared.staged)
    root = prepared.initial.canonical_root
    journal = canonical_value((root / "transaction.json").read_bytes())
    post_publication = {"publish", "temp-unlink", "parent-fsync"}
    expected = set(baseline_receipts)
    for index, artifact in enumerate(artifacts):
        if artifact.role not in {"rollback-receipt", "deployment-receipt"}:
            continue
        if index < artifact_index or (
            index == artifact_index and cut in post_publication
        ):
            expected.add(sha256(artifact.raw))
    current = artifacts[artifact_index]
    temporary = routine_install_temporary_path(
        current,
        journal["transaction_id"],
        artifact_index,
    )
    allowed_temporary = (
        temporary
        if temporary.parent == root / "receipts" and temporary.exists()
        else None
    )
    allowed_final_links = (
        frozenset({1, 2})
        if current.role in {"rollback-receipt", "deployment-receipt"}
        and cut == "publish"
        else frozenset({1})
    )
    observed = receipt_digest_inventory(
        root,
        allowed_temporary=allowed_temporary,
        allowed_final_links=allowed_final_links,
    )
    if observed != frozenset(expected):
        raise AssertionError("control maintenance additive receipt inventory disagrees")


def assert_control_maintenance_additive_directory_state(
    prepared: PreparedControlMaintenanceActivation,
    baseline: dict[str, DirectorySnapshot],
    *,
    through_index: int,
) -> None:
    """Assert the exact private parent-directory prefix for additive install."""

    artifacts = ordered_control_maintenance_additive_artifacts(prepared.staged)
    root = prepared.initial.canonical_root
    expected = set(baseline)
    for artifact in artifacts[: through_index + 1]:
        parent = Path(artifact.installed_path).parent
        while parent != root:
            expected.add(parent.relative_to(root).as_posix())
            parent = parent.parent
    observed = directory_snapshot(root)
    if set(observed) != expected:
        raise AssertionError(
            "control maintenance additive directory inventory disagrees"
        )
    for relative, snapshot in observed.items():
        prior = baseline.get(relative)
        if prior is not None:
            if (
                snapshot.identity != prior.identity
                or snapshot.mode != prior.mode
                or snapshot.owner != prior.owner
                or snapshot.group != prior.group
            ):
                raise AssertionError(
                    f"control maintenance baseline directory {relative} changed"
                )
        elif snapshot.mode != 0o700 or snapshot.owner != os.geteuid():
            raise AssertionError(
                f"control maintenance created directory {relative} is not private"
            )


def assert_control_maintenance_cleanup_persistence_state(
    prepared: PreparedControlMaintenanceActivation,
    cleanup_steps: tuple[RoutineCleanupStep, ...],
    *,
    step_index: int,
    cut: str,
) -> bytes:
    """Assert one exact durable rollback-cleanup prefix and pending cursor."""

    if step_index not in range(len(cleanup_steps)):
        raise AssertionError("control maintenance cleanup index is invalid")
    if cut not in ROUTINE_CLEANUP_PROCESS_LOSS_CUTS:
        raise AssertionError(f"unknown control maintenance cleanup cut: {cut}")
    root = prepared.initial.canonical_root
    journal_raw = (root / "transaction.json").read_bytes()
    journal = canonical_value(journal_raw)
    target = cleanup_steps[step_index]
    if (
        frozenset(journal) != JOURNAL_KEYS
        or journal["transaction_class"] != "control-set-maintenance"
        or journal["prior"]["state"] != "active"
        or journal["phase"] != "rollback-cleaning"
        or journal["pending_step"]
        != {
            "operation": target.operation,
            "index": step_index,
            "role": target.role,
        }
        or journal["candidate_smoke_acceptance"] is not None
        or journal["rollback_smoke_acceptance"] is None
    ):
        raise AssertionError(
            "control maintenance cleanup persistence journal disagrees"
        )
    for step in cleanup_steps:
        expected_present = step.index > step_index or (
            step.index == step_index and cut == "before-unlink"
        )
        path = root / step.relative_path
        if path.exists() != expected_present:
            raise AssertionError(
                f"control maintenance cleanup presence disagrees: {step.role}"
            )
        if not expected_present:
            continue
        metadata = path.lstat()
        if step.operation == "remove-directory":
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise AssertionError(
                    f"control maintenance cleanup directory {step.role} disagrees"
                )
        elif (
            step.artifact is None
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != step.artifact.installed["owner"]
            or stat.S_IMODE(metadata.st_mode) != step.artifact.installed["mode"]
            or metadata.st_nlink != 1
            or path.read_bytes() != step.artifact.raw
        ):
            raise AssertionError(
                f"control maintenance cleanup artifact {step.role} disagrees"
            )
    assert_no_control_maintenance_temporaries(root)
    return journal_raw


def assert_control_maintenance_additive_set_installed(staged: object) -> None:
    for artifact in control_maintenance_additive_artifacts(staged):
        path = Path(artifact.installed["path"])
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != artifact.installed["mode"]
            or metadata.st_nlink != 1
            or path.read_bytes() != artifact.raw
        ):
            raise AssertionError(
                f"installed control maintenance additive {artifact.role} disagrees"
            )


def assert_no_control_maintenance_temporaries(canonical_root: Path) -> None:
    temporaries = tuple(
        sorted(
            {
                *canonical_root.rglob(".task-witness-*.tmp"),
                *canonical_root.glob("transaction.*.tmp"),
            }
        )
    )
    if temporaries:
        raise AssertionError(f"control maintenance temporaries remain: {temporaries!r}")


def expected_control_maintenance_cleanup(
    staged: object,
    canonical_root: Path,
) -> tuple[ControlMaintenanceCleanupStep, ...]:
    baseline_files = {
        path.relative_to(canonical_root).as_posix()
        for path in canonical_root.rglob("*")
        if path.is_file()
    }
    baseline_directories = {
        path.relative_to(canonical_root).as_posix()
        for path in canonical_root.rglob("*")
        if path.is_dir()
    }
    additive = control_maintenance_additive_artifacts(staged)
    rollback = staged_artifact(staged, "rollback-receipt")
    deployment = staged_artifact(staged, "deployment-receipt")

    def live_relative(artifact: object) -> str:
        return Path(artifact.installed["path"]).relative_to(canonical_root).as_posix()

    owned = tuple(
        item for item in additive if live_relative(item) not in baseline_files
    )
    if rollback not in owned or deployment not in owned:
        raise AssertionError("control maintenance cleanup receipt ownership disagrees")
    middle = tuple(
        sorted(
            (item for item in owned if item is not rollback and item is not deployment),
            key=live_relative,
        )
    )
    owned_directories: set[str] = set()
    for artifact in owned:
        parent = Path(live_relative(artifact)).parent
        while parent != Path("."):
            relative = parent.as_posix()
            if relative not in baseline_directories:
                owned_directories.add(relative)
            parent = parent.parent
    steps = [
        ControlMaintenanceCleanupStep(
            "remove-artifact",
            rollback.role,
            live_relative(rollback),
        )
    ]
    steps.extend(
        ControlMaintenanceCleanupStep(
            "remove-artifact",
            artifact.role,
            live_relative(artifact),
        )
        for artifact in middle
    )
    steps.extend(
        ControlMaintenanceCleanupStep(
            "remove-directory",
            relative,
            relative,
        )
        for relative in sorted(
            owned_directories,
            key=lambda item: (item.count("/"), item),
            reverse=True,
        )
    )
    steps.append(
        ControlMaintenanceCleanupStep(
            "remove-artifact",
            deployment.role,
            live_relative(deployment),
        )
    )
    return tuple(steps)


def run_control_maintenance_activation_replace_process_loss(
    deployment: object,
    prepared: PreparedControlMaintenanceActivation,
    *,
    direction: str,
    replacement_index: int,
) -> ControlMaintenanceChildExit:
    """Lose activation immediately after one real control replacement."""

    _validate_control_maintenance_process_loss_target(
        direction,
        replacement_index,
    )
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        _install_control_maintenance_replace_process_loss_hook(
            deployment,
            prepared,
            direction=direction,
            replacement_index=replacement_index,
        )

        def unexpected_smoke(*args: object, **kwargs: object) -> None:
            del args, kwargs
            os._exit(_CONTROL_MAINTENANCE_UNEXPECTED_SMOKE_EXIT)

        deployment._spawn_activation_smoke_child = (
            RejectCandidateAcceptPriorControlMaintenanceSmoke(prepared)
            if direction == "prior"
            else unexpected_smoke
        )
        try:
            deployment.activate_staged(prepared.activation)
        except Exception as error:  # noqa: BLE001 - child reports the exact boundary
            _report_control_maintenance_child_error(write_fd, error)
            os._exit(_CONTROL_MAINTENANCE_CHILD_FAILURE_EXIT)
        os._exit(_CONTROL_MAINTENANCE_UNEXPECTED_RETURN_EXIT)
    os.close(write_fd)
    return _wait_for_control_maintenance_child(child, read_fd)


def run_control_maintenance_recovery_replace_process_loss(
    deployment: object,
    prepared: PreparedControlMaintenanceActivation,
    journal_raw: bytes,
    *,
    direction: str,
    replacement_index: int,
) -> ControlMaintenanceChildExit:
    """Lose public recovery after the next real staged-A control replacement."""

    _validate_control_maintenance_process_loss_target(
        direction,
        replacement_index,
    )
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        _install_control_maintenance_replace_process_loss_hook(
            deployment,
            prepared,
            direction=direction,
            replacement_index=replacement_index,
        )
        try:
            deployment.recover_transaction(
                deployment.RecoveryRequest(
                    activation=prepared.activation,
                    expected_journal_raw=journal_raw,
                )
            )
        except Exception as error:  # noqa: BLE001 - child reports the exact boundary
            _report_control_maintenance_child_error(write_fd, error)
            os._exit(_CONTROL_MAINTENANCE_CHILD_FAILURE_EXIT)
        os._exit(_CONTROL_MAINTENANCE_UNEXPECTED_RETURN_EXIT)
    os.close(write_fd)
    return _wait_for_control_maintenance_child(child, read_fd)


def run_control_maintenance_recovery_already_target_parent_fsync_probe(
    deployment: object,
    prepared: PreparedControlMaintenanceActivation,
    journal_raw: bytes,
) -> ControlMaintenanceRecoveryParentFsyncObservation:
    """Observe parent persistence before recovery advances an already-live target."""

    root = prepared.initial.canonical_root
    role = MAINTENANCE_REPLACEMENT_ROLES[0]
    target = staged_artifact(prepared.staged, role)
    target_path = Path(target.installed["path"])
    root_metadata = root.lstat()
    controller_parent_metadata = target_path.parent.lstat()
    root_identity = (root_metadata.st_dev, root_metadata.st_ino)
    controller_parent_identity = (
        controller_parent_metadata.st_dev,
        controller_parent_metadata.st_ino,
    )
    if root_identity == controller_parent_identity:
        raise AssertionError("control maintenance controller parent is not nested")

    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        original_fsync = os.fsync
        original_fstat = os.fstat
        state = {
            "controller_parent_fsyncs": 0,
            "root_parent_fsyncs": 0,
        }

        def write_pipe(raw: bytes) -> None:
            offset = 0
            while offset < len(raw):
                written = os.write(write_fd, raw[offset:])
                if written <= 0:
                    raise AssertionError(
                        "control maintenance recovery persistence report stalled"
                    )
                offset += written

        def observed_fsync(descriptor: int) -> None:
            metadata = original_fstat(descriptor)
            identity = (metadata.st_dev, metadata.st_ino)
            original_fsync(descriptor)
            if identity == controller_parent_identity:
                state["controller_parent_fsyncs"] += 1
            if identity != root_identity:
                return
            state["root_parent_fsyncs"] += 1
            advanced_raw = (root / "transaction.json").read_bytes()
            if advanced_raw == journal_raw:
                return
            previous = canonical_value(journal_raw)
            advanced = canonical_value(advanced_raw)
            if (
                advanced["transaction_id"] != previous["transaction_id"]
                or advanced["sequence"] != previous["sequence"] + 1
                or advanced["previous_journal_sha256"] != sha256(journal_raw)
                or advanced["phase"] != "control-switching"
                or advanced["pending_step"]
                != {
                    "operation": "replace-control",
                    "index": 1,
                    "role": MAINTENANCE_REPLACEMENT_ROLES[1],
                }
                or target_path.read_bytes() != target.raw
            ):
                raise AssertionError(
                    "control maintenance recovery journal boundary disagrees"
                )
            packet = (
                b"P"
                + state["controller_parent_fsyncs"].to_bytes(8, "big")
                + state["root_parent_fsyncs"].to_bytes(8, "big")
            )
            write_pipe(packet)
            os._exit(CONTROL_MAINTENANCE_RECOVERY_PARENT_FSYNC_EXIT)

        deployment.os.fsync = observed_fsync
        try:
            deployment.recover_transaction(
                deployment.RecoveryRequest(
                    activation=prepared.activation,
                    expected_journal_raw=journal_raw,
                )
            )
        except Exception as error:  # noqa: BLE001 - child reports exact boundary
            write_pipe(
                b"E"
                + f"{type(error).__name__}: {error}".encode(
                    "utf-8",
                    errors="replace",
                )
            )
            os._exit(_CONTROL_MAINTENANCE_CHILD_FAILURE_EXIT)
        os._exit(_CONTROL_MAINTENANCE_UNEXPECTED_RETURN_EXIT)

    os.close(write_fd)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(read_fd, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    os.close(read_fd)
    waited, status = os.waitpid(child, 0)
    payload = b"".join(chunks)
    if waited != child:
        raise AssertionError("control maintenance recovery persistence child changed")
    exit_status = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
    if payload.startswith(b"P") and len(payload) == 17:
        diagnostic = ""
        controller_parent_fsyncs = int.from_bytes(payload[1:9], "big")
        root_parent_fsyncs = int.from_bytes(payload[9:17], "big")
    else:
        diagnostic = (
            payload[1:].decode("utf-8", errors="replace")
            if payload.startswith(b"E")
            else payload.decode("utf-8", errors="replace")
        )
        controller_parent_fsyncs = -1
        root_parent_fsyncs = -1
    return ControlMaintenanceRecoveryParentFsyncObservation(
        exit_status=exit_status,
        diagnostic=diagnostic,
        controller_parent_fsyncs=controller_parent_fsyncs,
        root_parent_fsyncs=root_parent_fsyncs,
    )


def run_control_maintenance_recovery_additive_publish_process_loss(
    deployment: object,
    prepared: PreparedControlMaintenanceActivation,
    journal_raw: bytes,
    *,
    artifact_index: int | None,
) -> ControlMaintenanceChildExit:
    """Lose public recovery at the next additive publish or first control replace."""

    additive = ordered_control_maintenance_additive_artifacts(prepared.staged)
    if artifact_index is not None and (
        type(artifact_index) is not int or artifact_index not in range(len(additive))
    ):
        raise AssertionError("control maintenance additive index is invalid")
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        if artifact_index is None:
            _install_control_maintenance_replace_process_loss_hook(
                deployment,
                prepared,
                direction="candidate",
                replacement_index=0,
            )
        else:
            _install_control_maintenance_additive_publish_process_loss_hook(
                deployment,
                prepared,
                artifact_index=artifact_index,
            )

        def unexpected_smoke(*args: object, **kwargs: object) -> None:
            del args, kwargs
            os._exit(_CONTROL_MAINTENANCE_UNEXPECTED_SMOKE_EXIT)

        deployment._spawn_activation_smoke_child = unexpected_smoke
        try:
            deployment.recover_transaction(
                deployment.RecoveryRequest(
                    activation=prepared.activation,
                    expected_journal_raw=journal_raw,
                )
            )
        except Exception as error:  # noqa: BLE001 - child reports the exact boundary
            _report_control_maintenance_child_error(write_fd, error)
            os._exit(_CONTROL_MAINTENANCE_CHILD_FAILURE_EXIT)
        os._exit(_CONTROL_MAINTENANCE_UNEXPECTED_RETURN_EXIT)
    os.close(write_fd)
    return _wait_for_control_maintenance_child(child, read_fd)


def run_control_maintenance_cleanup_process_loss_cut(
    deployment: object,
    prepared: PreparedControlMaintenanceActivation,
    *,
    cleanup_steps: tuple[RoutineCleanupStep, ...],
    step_index: int,
    cut: str,
) -> ControlMaintenanceChildExit:
    """Lose activation around one exact byte-validated rollback cleanup step."""

    if step_index not in range(len(cleanup_steps)):
        raise AssertionError("control maintenance cleanup index is invalid")
    if cut not in ROUTINE_CLEANUP_PROCESS_LOSS_CUTS:
        raise AssertionError(f"unknown control maintenance cleanup cut: {cut}")
    target = cleanup_steps[step_index]
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        original_write = deployment._write_activation_journal
        original_unlink = deployment.os.unlink
        original_rmdir = deployment.os.rmdir
        original_fsync = deployment.os.fsync
        original_fstat = deployment.os.fstat
        root = prepared.initial.canonical_root
        state: dict[str, object] = {
            "armed": False,
            "removed": False,
            "target_parent_identity": None,
        }

        def process_loss() -> None:
            os._exit(CONTROL_MAINTENANCE_CLEANUP_PROCESS_LOSS_EXIT)

        def observed_write(canonical_root_fd: int, journal: object) -> None:
            original_write(canonical_root_fd, journal)
            pending = journal.value["pending_step"]
            if journal.value["phase"] == "rollback-cleaning" and pending == {
                "operation": target.operation,
                "index": step_index,
                "role": target.role,
            }:
                parent = (root / Path(target.relative_path).parent).lstat()
                state["target_parent_identity"] = (parent.st_dev, parent.st_ino)
                state["armed"] = True
                if cut == "before-unlink":
                    process_loss()

        def observed_unlink(*args: object, **kwargs: object) -> None:
            name = args[0] if args else kwargs.get("path")
            parent_fd = kwargs.get("dir_fd")
            matches = False
            if (
                state["armed"]
                and target.operation == "remove-artifact"
                and name == Path(target.relative_path).name
            ):
                if type(parent_fd) is not int:
                    raise AssertionError(
                        "control maintenance cleanup parent descriptor is absent"
                    )
                parent = original_fstat(parent_fd)
                matches = (parent.st_dev, parent.st_ino) == state[
                    "target_parent_identity"
                ]
            original_unlink(*args, **kwargs)
            if matches:
                state["removed"] = True
                if cut == "after-unlink":
                    process_loss()

        def observed_rmdir(*args: object, **kwargs: object) -> None:
            name = args[0] if args else kwargs.get("path")
            parent_fd = kwargs.get("dir_fd")
            matches = False
            if (
                state["armed"]
                and target.operation == "remove-directory"
                and name == Path(target.relative_path).name
            ):
                if type(parent_fd) is not int:
                    raise AssertionError(
                        "control maintenance cleanup parent descriptor is absent"
                    )
                parent = original_fstat(parent_fd)
                matches = (parent.st_dev, parent.st_ino) == state[
                    "target_parent_identity"
                ]
            original_rmdir(*args, **kwargs)
            if matches:
                state["removed"] = True
                if cut == "after-unlink":
                    process_loss()

        def observed_fsync(descriptor: int) -> None:
            original_fsync(descriptor)
            if state["removed"] and cut == "parent-fsync":
                parent = original_fstat(descriptor)
                if (parent.st_dev, parent.st_ino) == state["target_parent_identity"]:
                    process_loss()

        deployment._spawn_activation_smoke_child = (
            RejectCandidateAcceptPriorControlMaintenanceSmoke(prepared)
        )
        deployment._write_activation_journal = observed_write
        deployment.os.unlink = observed_unlink
        deployment.os.rmdir = observed_rmdir
        deployment.os.fsync = observed_fsync
        try:
            deployment.activate_staged(prepared.activation)
        except Exception as error:  # noqa: BLE001 - child reports the exact boundary
            _report_control_maintenance_child_error(write_fd, error)
            os._exit(_CONTROL_MAINTENANCE_CHILD_FAILURE_EXIT)
        os._exit(_CONTROL_MAINTENANCE_UNEXPECTED_RETURN_EXIT)
    os.close(write_fd)
    return _wait_for_control_maintenance_child(child, read_fd)


def run_control_maintenance_journal_process_loss_cut(
    deployment: object,
    prepared: PreparedControlMaintenanceActivation,
    *,
    generation: str,
    cut: str,
) -> ControlMaintenanceJournalCut:
    """Lose activation within one exact control journal successor write."""

    if generation not in {"mixed-candidate-control", "candidate-active-terminal"}:
        raise AssertionError(
            f"unknown control maintenance journal generation: {generation}"
        )
    if cut not in ROUTINE_JOURNAL_PROCESS_LOSS_CUTS:
        raise AssertionError(f"unknown control maintenance journal cut: {cut}")
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        original_open = deployment.os.open
        original_write = deployment.os.write
        original_fsync = deployment.os.fsync
        original_fstat = deployment.os.fstat
        original_replace = deployment.os.replace
        original_write_all = deployment._write_all
        original_journal_write = deployment._write_activation_journal
        root = prepared.initial.canonical_root
        root_metadata = root.lstat()
        state: dict[str, object] = {
            "armed": False,
            "target_name": None,
            "target_fd": None,
            "replaced": False,
        }

        def write_pipe(raw: bytes) -> None:
            offset = 0
            while offset < len(raw):
                written = original_write(write_fd, raw[offset:])
                if written <= 0:
                    os._exit(_CONTROL_MAINTENANCE_CHILD_FAILURE_EXIT)
                offset += written

        def process_loss() -> None:
            os.close(write_fd)
            os._exit(CONTROL_MAINTENANCE_JOURNAL_PROCESS_LOSS_EXIT)

        def matches(value: Mapping[str, Any]) -> bool:
            if generation == "mixed-candidate-control":
                return value["phase"] == "control-switching" and value[
                    "pending_step"
                ] == {
                    "operation": "replace-control",
                    "index": 1,
                    "role": MAINTENANCE_REPLACEMENT_ROLES[1],
                }
            terminal = value["terminal_result"]
            return (
                value["phase"] == "terminal"
                and terminal is not None
                and terminal["outcome"] == "candidate-active"
            )

        def observed_journal_write(canonical_root_fd: int, journal: object) -> None:
            if matches(journal.value):
                if state["armed"]:
                    raise AssertionError(
                        "control maintenance journal generation repeated"
                    )
                current_raw = (root / "transaction.json").read_bytes()
                packet = (
                    b"J"
                    + len(current_raw).to_bytes(8, "big")
                    + len(journal.raw).to_bytes(8, "big")
                    + current_raw
                    + journal.raw
                )
                write_pipe(packet)
                state["armed"] = True
                state["target_name"] = (
                    f"transaction.{journal.value['transaction_id']}."
                    f"{journal.value['sequence']}.tmp"
                )
            original_journal_write(canonical_root_fd, journal)

        def observed_open(*args: object, **kwargs: object) -> int:
            descriptor = original_open(*args, **kwargs)
            name = args[0] if args else kwargs.get("path")
            if state["armed"] and name == state["target_name"]:
                state["target_fd"] = descriptor
                if cut == "temp-create":
                    process_loss()
            return descriptor

        def observed_write_all(descriptor: int, raw: bytes, label: str) -> None:
            if descriptor == state["target_fd"] and cut == "partial-write":
                length = max(1, len(raw) // 2)
                offset = 0
                while offset < length:
                    written = original_write(descriptor, raw[offset:length])
                    if written <= 0:
                        raise AssertionError(
                            "control maintenance journal partial write stalled"
                        )
                    offset += written
                process_loss()
            original_write_all(descriptor, raw, label)
            if descriptor == state["target_fd"] and cut == "full-write":
                process_loss()

        def observed_replace(*args: object, **kwargs: object) -> None:
            original_replace(*args, **kwargs)
            source = args[0] if args else kwargs.get("src")
            destination = args[1] if len(args) > 1 else kwargs.get("dst")
            if state["armed"] and source == state["target_name"]:
                if destination != "transaction.json":
                    raise AssertionError(
                        "control maintenance journal replacement target disagrees"
                    )
                state["replaced"] = True
                if cut == "replace":
                    process_loss()

        def observed_fsync(descriptor: int) -> None:
            original_fsync(descriptor)
            if state["replaced"] and cut == "parent-fsync":
                synchronized = original_fstat(descriptor)
                if (synchronized.st_dev, synchronized.st_ino) == (
                    root_metadata.st_dev,
                    root_metadata.st_ino,
                ):
                    process_loss()

        deployment.os.open = observed_open
        deployment.os.fsync = observed_fsync
        deployment.os.replace = observed_replace
        deployment._write_all = observed_write_all
        deployment._write_activation_journal = observed_journal_write
        if generation == "candidate-active-terminal":
            deployment._spawn_activation_smoke_child = AcceptedControlMaintenanceSmoke(
                prepared
            )
        else:

            def unexpected_smoke(*args: object, **kwargs: object) -> None:
                del args, kwargs
                os._exit(_CONTROL_MAINTENANCE_UNEXPECTED_SMOKE_EXIT)

            deployment._spawn_activation_smoke_child = unexpected_smoke
        try:
            deployment.activate_staged(prepared.activation)
        except Exception as error:  # noqa: BLE001 - child reports exact boundary
            if not state["armed"]:
                diagnostic = (f"E{type(error).__name__}: {error}").encode(
                    "utf-8", errors="replace"
                )
                write_pipe(diagnostic)
            os._exit(_CONTROL_MAINTENANCE_CHILD_FAILURE_EXIT)
        os._exit(_CONTROL_MAINTENANCE_UNEXPECTED_RETURN_EXIT)
    os.close(write_fd)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(read_fd, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    os.close(read_fd)
    waited, status = os.waitpid(child, 0)
    payload = b"".join(chunks)
    if waited != child or not os.WIFEXITED(status):
        raise AssertionError(
            f"control maintenance journal cut {cut} did not exit normally"
        )
    exit_status = os.WEXITSTATUS(status)
    if exit_status != CONTROL_MAINTENANCE_JOURNAL_PROCESS_LOSS_EXIT:
        diagnostic = (
            payload[1:].decode("utf-8", errors="replace")
            if payload.startswith(b"E")
            else ""
        )
        raise AssertionError(
            f"control maintenance journal cut {cut} exited at "
            f"unexpected boundary {exit_status}: {diagnostic}"
        )
    if len(payload) < 17 or payload[:1] != b"J":
        raise AssertionError("control maintenance journal cut report disagrees")
    current_length = int.from_bytes(payload[1:9], "big")
    target_length = int.from_bytes(payload[9:17], "big")
    current_start = 17
    target_start = current_start + current_length
    current_raw = payload[current_start:target_start]
    target_raw = payload[target_start : target_start + target_length]
    if target_start + target_length != len(payload):
        raise AssertionError("control maintenance journal cut framing disagrees")
    canonical_value(current_raw)
    canonical_value(target_raw)
    return ControlMaintenanceJournalCut(current_raw, target_raw)


def run_control_maintenance_smoke_process_loss_cut(
    deployment: object,
    prepared: PreparedControlMaintenanceActivation,
    *,
    phase: str,
    cut: str,
) -> tuple[str, ...]:
    """Lose activation after a real exact-byte smoke return or acceptance write."""

    if phase not in {"candidate-smoke", "rollback-smoke"}:
        raise AssertionError(f"unknown control maintenance smoke phase: {phase}")
    if cut not in {"child-return", "accepted-journal"}:
        raise AssertionError(f"unknown control maintenance smoke cut: {cut}")
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        original_run = deployment._run_activation_smoke
        original_write = deployment._write_activation_journal
        smoke = (
            AcceptedControlMaintenanceSmoke(prepared)
            if phase == "candidate-smoke"
            else RejectCandidateAcceptPriorControlMaintenanceSmoke(prepared)
        )

        def process_loss() -> None:
            os.close(write_fd)
            os._exit(CONTROL_MAINTENANCE_SMOKE_PROCESS_LOSS_EXIT)

        def observed_smoke(
            argv: tuple[str, ...],
            *,
            pass_fds: tuple[int, ...],
        ) -> subprocess.CompletedProcess[bytes]:
            candidate_live = control_maintenance_unit_is_installed(
                prepared.staged,
                prior=False,
            )
            prior_live = control_maintenance_unit_is_installed(
                prepared.staged,
                prior=True,
            )
            if candidate_live == prior_live:
                os._exit(_CONTROL_MAINTENANCE_CHILD_FAILURE_EXIT)
            marker = b"B" if candidate_live else b"A"
            if os.write(write_fd, marker) != 1:
                os._exit(_CONTROL_MAINTENANCE_CHILD_FAILURE_EXIT)
            return smoke(argv, pass_fds=pass_fds)

        def observed_run(*args: object, **kwargs: object):
            result = original_run(*args, **kwargs)
            live_phase = canonical_value(
                (prepared.initial.canonical_root / "transaction.json").read_bytes()
            )["phase"]
            if cut == "child-return" and live_phase == phase:
                process_loss()
            return result

        def observed_write(canonical_root_fd: int, journal: object) -> None:
            original_write(canonical_root_fd, journal)
            acceptance = (
                journal.value["candidate_smoke_acceptance"]
                if phase == "candidate-smoke"
                else journal.value["rollback_smoke_acceptance"]
            )
            if (
                cut == "accepted-journal"
                and journal.value["phase"] == phase
                and acceptance is not None
            ):
                process_loss()

        deployment._spawn_activation_smoke_child = observed_smoke
        deployment._run_activation_smoke = observed_run
        deployment._write_activation_journal = observed_write
        try:
            deployment.activate_staged(prepared.activation)
        except Exception:  # noqa: BLE001 - child reports the exact boundary
            os._exit(_CONTROL_MAINTENANCE_CHILD_FAILURE_EXIT)
        os._exit(_CONTROL_MAINTENANCE_UNEXPECTED_RETURN_EXIT)
    os.close(write_fd)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(read_fd, 1024)
        if not chunk:
            break
        chunks.append(chunk)
    os.close(read_fd)
    waited, status = os.waitpid(child, 0)
    if waited != child or not os.WIFEXITED(status):
        raise AssertionError(
            f"control maintenance smoke cut {cut} did not exit normally"
        )
    exit_status = os.WEXITSTATUS(status)
    if exit_status != CONTROL_MAINTENANCE_SMOKE_PROCESS_LOSS_EXIT:
        raise AssertionError(
            f"control maintenance smoke cut {cut} exited at "
            f"unexpected boundary {exit_status}"
        )
    markers = b"".join(chunks)
    if any(value not in b"BA" for value in markers):
        raise AssertionError("control maintenance smoke markers disagree")
    return tuple(chr(value) for value in markers)


def run_control_maintenance_restored_prior_terminal_process_loss(
    deployment: object,
    prepared: PreparedControlMaintenanceActivation,
) -> tuple[str, ...]:
    """Lose activation after the durable restored-prior terminal journal."""

    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        original_write = deployment._write_activation_journal
        smoke = RejectCandidateAcceptPriorControlMaintenanceSmoke(prepared)

        def process_loss() -> None:
            os.close(write_fd)
            os._exit(CONTROL_MAINTENANCE_TERMINAL_PROCESS_LOSS_EXIT)

        def observed_smoke(
            argv: tuple[str, ...],
            *,
            pass_fds: tuple[int, ...],
        ) -> subprocess.CompletedProcess[bytes]:
            candidate_live = control_maintenance_unit_is_installed(
                prepared.staged,
                prior=False,
            )
            prior_live = control_maintenance_unit_is_installed(
                prepared.staged,
                prior=True,
            )
            if candidate_live == prior_live:
                os._exit(_CONTROL_MAINTENANCE_CHILD_FAILURE_EXIT)
            marker = b"B" if candidate_live else b"A"
            if os.write(write_fd, marker) != 1:
                os._exit(_CONTROL_MAINTENANCE_CHILD_FAILURE_EXIT)
            return smoke(argv, pass_fds=pass_fds)

        def observed_write(canonical_root_fd: int, journal: object) -> None:
            original_write(canonical_root_fd, journal)
            terminal = journal.value["terminal_result"]
            if (
                journal.value["phase"] == "terminal"
                and terminal is not None
                and terminal["outcome"] == "restored-prior"
            ):
                process_loss()

        deployment._spawn_activation_smoke_child = observed_smoke
        deployment._write_activation_journal = observed_write
        try:
            deployment.activate_staged(prepared.activation)
        except Exception:  # noqa: BLE001 - child reports the exact boundary
            os._exit(_CONTROL_MAINTENANCE_CHILD_FAILURE_EXIT)
        os._exit(_CONTROL_MAINTENANCE_UNEXPECTED_RETURN_EXIT)
    os.close(write_fd)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(read_fd, 1024)
        if not chunk:
            break
        chunks.append(chunk)
    os.close(read_fd)
    waited, status = os.waitpid(child, 0)
    if waited != child or not os.WIFEXITED(status):
        raise AssertionError(
            "control maintenance restored-prior terminal cut did not exit normally"
        )
    exit_status = os.WEXITSTATUS(status)
    if exit_status != CONTROL_MAINTENANCE_TERMINAL_PROCESS_LOSS_EXIT:
        raise AssertionError(
            "control maintenance restored-prior terminal cut exited at "
            f"unexpected boundary {exit_status}"
        )
    markers = b"".join(chunks)
    if any(value not in b"BA" for value in markers):
        raise AssertionError(
            "control maintenance restored-prior terminal markers disagree"
        )
    return tuple(chr(value) for value in markers)


def run_control_maintenance_activation_replacement_persistence_cut(
    deployment: object,
    prepared: PreparedControlMaintenanceActivation,
    *,
    direction: str,
    replacement_index: int,
    cut: str,
) -> ControlMaintenanceChildExit:
    """Lose activation at one exact control replacement persistence seam."""

    _validate_control_maintenance_process_loss_target(
        direction,
        replacement_index,
    )
    if cut not in CONTROL_MAINTENANCE_REPLACEMENT_PERSISTENCE_CUTS:
        raise AssertionError(f"unknown control maintenance replacement cut: {cut}")
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        _install_control_maintenance_replacement_persistence_hook(
            deployment,
            prepared,
            direction=direction,
            replacement_index=replacement_index,
            cut=cut,
        )

        def unexpected_smoke(*args: object, **kwargs: object) -> None:
            del args, kwargs
            os._exit(_CONTROL_MAINTENANCE_UNEXPECTED_SMOKE_EXIT)

        deployment._spawn_activation_smoke_child = (
            RejectCandidateAcceptPriorControlMaintenanceSmoke(prepared)
            if direction == "prior"
            else unexpected_smoke
        )
        try:
            deployment.activate_staged(prepared.activation)
        except Exception as error:  # noqa: BLE001 - child reports the exact boundary
            _report_control_maintenance_child_error(write_fd, error)
            os._exit(_CONTROL_MAINTENANCE_CHILD_FAILURE_EXIT)
        os._exit(_CONTROL_MAINTENANCE_UNEXPECTED_RETURN_EXIT)
    os.close(write_fd)
    return _wait_for_control_maintenance_child(child, read_fd)


def assert_control_maintenance_replacement_persistence_state(
    prepared: PreparedControlMaintenanceActivation,
    *,
    direction: str,
    replacement_index: int,
    cut: str,
) -> bytes:
    """Assert the exact durable state at one control replacement cut."""

    _validate_control_maintenance_process_loss_target(
        direction,
        replacement_index,
    )
    if cut not in CONTROL_MAINTENANCE_REPLACEMENT_PERSISTENCE_CUTS:
        raise AssertionError(f"unknown control maintenance replacement cut: {cut}")
    staged = prepared.staged
    root = prepared.initial.canonical_root
    journal_raw = (root / "transaction.json").read_bytes()
    journal = canonical_value(journal_raw)
    phase = "control-switching" if direction == "candidate" else "prior-restoring"
    role = MAINTENANCE_REPLACEMENT_ROLES[replacement_index]
    additive_count = len(control_maintenance_additive_artifacts(staged))
    expected_sequence = (
        4 + additive_count + replacement_index
        if direction == "candidate"
        else 5 + additive_count + len(MAINTENANCE_REPLACEMENT_ROLES) + replacement_index
    )
    if (
        frozenset(journal) != JOURNAL_KEYS
        or journal["transaction_class"] != "control-set-maintenance"
        or journal["prior"]["state"] != "active"
        or journal["phase"] != phase
        or journal["sequence"] != expected_sequence
        or journal["pending_step"]
        != {
            "operation": "replace-control",
            "index": replacement_index,
            "role": role,
        }
    ):
        raise AssertionError(
            "control maintenance replacement persistence journal disagrees"
        )
    pre_replace = cut in {
        "temp-create",
        "partial-write",
        "content-fsync",
        "ready-fsync",
    }
    completed_index = replacement_index - 1 if pre_replace else replacement_index
    for index, replacement_role in enumerate(MAINTENANCE_REPLACEMENT_ROLES):
        candidate_live = (
            index <= completed_index
            if direction == "candidate"
            else index > completed_index
        )
        expected = staged_artifact(
            staged,
            (
                replacement_role
                if candidate_live
                else _prior_control_maintenance_replacement_role(replacement_role)
            ),
        )
        target = staged_artifact(staged, replacement_role)
        path = Path(target.installed["path"])
        metadata = path.lstat()
        if (
            Path(expected.installed["path"]) != path
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != expected.installed["mode"]
            or metadata.st_nlink != 1
            or path.read_bytes() != expected.raw
        ):
            raise AssertionError(
                f"control maintenance persistence {replacement_role} bytes disagree"
            )
    temporaries = tuple(
        sorted(
            {
                *root.rglob(".task-witness-*.tmp"),
                *root.glob("transaction.*.tmp"),
            }
        )
    )
    if not pre_replace:
        if temporaries:
            raise AssertionError(
                "control maintenance post-replace temporary inventory disagrees"
            )
        return journal_raw
    target = staged_artifact(staged, role)
    target_path = Path(target.installed["path"])
    temporary = target_path.parent / (
        f".task-witness-control-{journal['transaction_id']}-"
        f"{direction}-{replacement_index}.tmp"
    )
    if temporaries != (temporary,):
        raise AssertionError(
            "control maintenance pre-replace temporary inventory disagrees"
        )
    metadata = temporary.lstat()
    desired_role = (
        role
        if direction == "candidate"
        else _prior_control_maintenance_replacement_role(role)
    )
    desired = staged_artifact(staged, desired_role)
    raw = temporary.read_bytes()
    expected_mode = desired.installed["mode"] if cut == "ready-fsync" else 0o600
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != expected_mode
        or metadata.st_nlink != 1
    ):
        raise AssertionError(
            "control maintenance replacement temporary binding disagrees"
        )
    if cut == "temp-create":
        valid_raw = raw == b""
    elif cut == "partial-write":
        valid_raw = 0 < len(raw) < len(desired.raw) and desired.raw.startswith(raw)
    else:
        valid_raw = raw == desired.raw
    if not valid_raw:
        raise AssertionError("control maintenance replacement temporary bytes disagree")
    return journal_raw


def assert_control_maintenance_mixed_replacement_state(
    prepared: PreparedControlMaintenanceActivation,
    *,
    direction: str,
    replacement_index: int,
) -> bytes:
    """Assert one exact journal-indexed A/B control replacement prefix."""

    _validate_control_maintenance_process_loss_target(
        direction,
        replacement_index,
    )
    staged = prepared.staged
    root = prepared.initial.canonical_root
    journal_raw = (root / "transaction.json").read_bytes()
    journal = canonical_value(journal_raw)
    phase = "control-switching" if direction == "candidate" else "prior-restoring"
    role = MAINTENANCE_REPLACEMENT_ROLES[replacement_index]
    additive_count = len(control_maintenance_additive_artifacts(staged))
    expected_sequence = (
        4 + additive_count + replacement_index
        if direction == "candidate"
        else 5 + additive_count + len(MAINTENANCE_REPLACEMENT_ROLES) + replacement_index
    )
    if (
        frozenset(journal) != JOURNAL_KEYS
        or journal["transaction_class"] != "control-set-maintenance"
        or journal["prior"]["state"] != "active"
        or journal["phase"] != phase
        or journal["sequence"] != expected_sequence
        or journal["pending_step"]
        != {
            "operation": "replace-control",
            "index": replacement_index,
            "role": role,
        }
    ):
        raise AssertionError("control maintenance mixed replacement journal disagrees")
    for index, replacement_role in enumerate(MAINTENANCE_REPLACEMENT_ROLES):
        candidate_live = (
            index <= replacement_index
            if direction == "candidate"
            else index > replacement_index
        )
        expected = staged_artifact(
            staged,
            (
                replacement_role
                if candidate_live
                else _prior_control_maintenance_replacement_role(replacement_role)
            ),
        )
        target = staged_artifact(staged, replacement_role)
        path = Path(target.installed["path"])
        metadata = path.lstat()
        if (
            Path(expected.installed["path"]) != path
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != expected.installed["mode"]
            or metadata.st_nlink != 1
            or path.read_bytes() != expected.raw
        ):
            raise AssertionError(
                f"control maintenance mixed {replacement_role} bytes disagree"
            )
    assert_no_control_maintenance_temporaries(root)
    return journal_raw


def _validate_control_maintenance_process_loss_target(
    direction: str,
    replacement_index: int,
) -> None:
    if direction not in {"candidate", "prior"}:
        raise AssertionError("control maintenance replacement direction is invalid")
    if replacement_index not in range(len(MAINTENANCE_REPLACEMENT_ROLES)):
        raise AssertionError("control maintenance replacement index is invalid")


def _prior_control_maintenance_replacement_role(role: str) -> str:
    return {
        "active-record": "prior-active-record",
        "deployment-alias": "prior-deployment-alias",
    }.get(role, f"prior-{role}")


def _install_control_maintenance_additive_publish_process_loss_hook(
    deployment: object,
    prepared: PreparedControlMaintenanceActivation,
    *,
    artifact_index: int,
) -> None:
    original_link = deployment.os.link
    original_fstat = deployment.os.fstat
    artifacts = ordered_control_maintenance_additive_artifacts(prepared.staged)
    artifact = artifacts[artifact_index]
    target = Path(artifact.installed_path)
    root = prepared.initial.canonical_root

    def observed_link(*args: object, **kwargs: object) -> None:
        source = args[0] if args else kwargs.get("src")
        destination = args[1] if len(args) > 1 else kwargs.get("dst")
        if not (
            isinstance(source, str) and source.startswith(".task-witness-install-")
        ):
            original_link(*args, **kwargs)
            return
        journal = canonical_value((root / "transaction.json").read_bytes())
        expected_source = (
            f".task-witness-install-{journal['transaction_id']}-{artifact_index}.tmp"
        )
        source_fd = kwargs.get("src_dir_fd")
        destination_fd = kwargs.get("dst_dir_fd")
        parent = target.parent.lstat()
        if (
            source != expected_source
            or destination != target.name
            or type(source_fd) is not int
            or type(destination_fd) is not int
            or kwargs.get("follow_symlinks") is not False
            or (original_fstat(source_fd).st_dev, original_fstat(source_fd).st_ino)
            != (parent.st_dev, parent.st_ino)
            or (
                original_fstat(destination_fd).st_dev,
                original_fstat(destination_fd).st_ino,
            )
            != (parent.st_dev, parent.st_ino)
            or journal["phase"] != "additive-installing"
            or journal["pending_step"]
            != {
                "operation": "install",
                "index": artifact_index,
                "role": artifact.role,
            }
        ):
            raise AssertionError(
                "control maintenance recovery additive publish boundary disagrees"
            )
        original_link(*args, **kwargs)
        os._exit(CONTROL_MAINTENANCE_ADDITIVE_PUBLISH_PROCESS_LOSS_EXIT)

    deployment.os.link = observed_link


def _install_control_maintenance_replace_process_loss_hook(
    deployment: object,
    prepared: PreparedControlMaintenanceActivation,
    *,
    direction: str,
    replacement_index: int,
) -> None:
    original_replace = deployment.os.replace
    original_fstat = deployment.os.fstat
    root = prepared.initial.canonical_root
    role = MAINTENANCE_REPLACEMENT_ROLES[replacement_index]
    target = Path(staged_artifact(prepared.staged, role).installed["path"])
    target_parent = target.parent.lstat()

    def observed_replace(*args: object, **kwargs: object) -> None:
        source = args[0] if args else kwargs.get("src")
        if not (
            isinstance(source, str) and source.startswith(".task-witness-control-")
        ):
            original_replace(*args, **kwargs)
            return
        journal = canonical_value((root / "transaction.json").read_bytes())
        expected_source = (
            f".task-witness-control-{journal['transaction_id']}-"
            f"{direction}-{replacement_index}.tmp"
        )
        if source != expected_source:
            original_replace(*args, **kwargs)
            return
        destination = args[1] if len(args) > 1 else kwargs.get("dst")
        source_fd = kwargs.get("src_dir_fd")
        destination_fd = kwargs.get("dst_dir_fd")
        if (
            destination != target.name
            or type(source_fd) is not int
            or type(destination_fd) is not int
            or source_fd != destination_fd
            or (original_fstat(source_fd).st_dev, original_fstat(source_fd).st_ino)
            != (target_parent.st_dev, target_parent.st_ino)
        ):
            raise AssertionError(
                "control maintenance replacement process-loss binding disagrees"
            )
        original_replace(*args, **kwargs)
        os._exit(CONTROL_MAINTENANCE_REPLACE_PROCESS_LOSS_EXIT)

    deployment.os.replace = observed_replace


def _install_control_maintenance_replacement_persistence_hook(
    deployment: object,
    prepared: PreparedControlMaintenanceActivation,
    *,
    direction: str,
    replacement_index: int,
    cut: str,
) -> None:
    original_open = deployment.os.open
    original_write = deployment.os.write
    original_fsync = deployment.os.fsync
    original_fchmod = deployment.os.fchmod
    original_fstat = deployment.os.fstat
    original_replace = deployment.os.replace
    original_write_all = deployment._write_all
    root = prepared.initial.canonical_root
    role = MAINTENANCE_REPLACEMENT_ROLES[replacement_index]
    target = Path(staged_artifact(prepared.staged, role).installed["path"])
    target_parent = target.parent.lstat()
    state: dict[str, object] = {
        "temporary_fd": None,
        "temporary_name": None,
        "temporary_fsyncs": 0,
        "temporary_fchmods": 0,
        "replaced": False,
    }

    def expected_temporary() -> str:
        transaction_id = canonical_value((root / "transaction.json").read_bytes())[
            "transaction_id"
        ]
        return (
            f".task-witness-control-{transaction_id}-"
            f"{direction}-{replacement_index}.tmp"
        )

    def parent_matches(descriptor: object) -> bool:
        if type(descriptor) is not int:
            return False
        metadata = original_fstat(descriptor)
        return (metadata.st_dev, metadata.st_ino) == (
            target_parent.st_dev,
            target_parent.st_ino,
        )

    def observed_open(*args: object, **kwargs: object) -> int:
        descriptor = original_open(*args, **kwargs)
        name = args[0] if args else kwargs.get("path")
        if (
            isinstance(name, str)
            and name.startswith(".task-witness-control-")
            and name == expected_temporary()
        ):
            parent_fd = kwargs.get("dir_fd")
            if not parent_matches(parent_fd):
                raise AssertionError(
                    "control maintenance replacement temporary parent disagrees"
                )
            state["temporary_fd"] = descriptor
            state["temporary_name"] = name
            if cut == "temp-create":
                os._exit(CONTROL_MAINTENANCE_REPLACE_PROCESS_LOSS_EXIT)
        return descriptor

    def observed_write_all(descriptor: int, raw: bytes, label: str) -> None:
        if descriptor == state["temporary_fd"] and cut == "partial-write":
            length = max(1, len(raw) // 2)
            offset = 0
            while offset < length:
                written = original_write(descriptor, raw[offset:length])
                if written <= 0:
                    raise OSError("control maintenance partial write did not advance")
                offset += written
            os._exit(CONTROL_MAINTENANCE_REPLACE_PROCESS_LOSS_EXIT)
        original_write_all(descriptor, raw, label)

    def observed_fsync(descriptor: int) -> None:
        original_fsync(descriptor)
        if descriptor == state["temporary_fd"]:
            fsyncs = int(state["temporary_fsyncs"]) + 1
            state["temporary_fsyncs"] = fsyncs
            if (cut == "content-fsync" and fsyncs == 1) or (
                cut == "ready-fsync" and int(state["temporary_fchmods"]) >= 2
            ):
                os._exit(CONTROL_MAINTENANCE_REPLACE_PROCESS_LOSS_EXIT)
        elif state["replaced"] and parent_matches(descriptor):
            if cut == "parent-fsync":
                os._exit(CONTROL_MAINTENANCE_REPLACE_PROCESS_LOSS_EXIT)

    def observed_fchmod(descriptor: int, mode: int) -> None:
        original_fchmod(descriptor, mode)
        if descriptor == state["temporary_fd"]:
            state["temporary_fchmods"] = int(state["temporary_fchmods"]) + 1

    def observed_replace(*args: object, **kwargs: object) -> None:
        original_replace(*args, **kwargs)
        source = args[0] if args else kwargs.get("src")
        if source != state["temporary_name"]:
            return
        destination = args[1] if len(args) > 1 else kwargs.get("dst")
        if (
            destination != target.name
            or not parent_matches(kwargs.get("src_dir_fd"))
            or kwargs.get("src_dir_fd") != kwargs.get("dst_dir_fd")
        ):
            raise AssertionError(
                "control maintenance replacement persistence binding disagrees"
            )
        state["replaced"] = True
        if cut == "replace":
            os._exit(CONTROL_MAINTENANCE_REPLACE_PROCESS_LOSS_EXIT)

    deployment.os.open = observed_open
    deployment.os.fchmod = observed_fchmod
    deployment.os.fsync = observed_fsync
    deployment.os.replace = observed_replace
    deployment._write_all = observed_write_all


def _report_control_maintenance_child_error(
    descriptor: int,
    error: Exception,
) -> None:
    raw = f"{type(error).__name__}: {error}".encode("utf-8", errors="replace")
    offset = 0
    while offset < len(raw):
        written = os.write(descriptor, raw[offset:])
        if written <= 0:
            break
        offset += written


def _wait_for_control_maintenance_child(
    child: int,
    read_fd: int,
) -> ControlMaintenanceChildExit:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(read_fd, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    os.close(read_fd)
    waited, status = os.waitpid(child, 0)
    if waited != child:
        raise AssertionError("control maintenance process-loss child changed")
    if os.WIFEXITED(status):
        exit_status = os.WEXITSTATUS(status)
    elif os.WIFSIGNALED(status):
        exit_status = -os.WTERMSIG(status)
    else:
        exit_status = -1
    return ControlMaintenanceChildExit(
        exit_status=exit_status,
        diagnostic=b"".join(chunks).decode("utf-8", errors="replace"),
    )


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value
