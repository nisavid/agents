from __future__ import annotations

import json
import os
import stat
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._activation_support import canonical_value
from ._routine_support import smoke_envelope
from ._support import sha256

JOURNAL_KEYS = frozenset(
    {
        "schema_version",
        "contract",
        "transaction_id",
        "sequence",
        "previous_journal_sha256",
        "transaction_class",
        "phase",
        "canonical_root",
        "effective_uid",
        "activation_lock",
        "outer_maintenance_transaction_sha256",
        "stage",
        "prior",
        "candidate",
        "rollback_authority",
        "preimage",
        "pending_step",
        "smoke_handoff",
        "candidate_smoke_acceptance",
        "rollback_smoke_acceptance",
        "terminal_result",
        "content_sha256",
    }
)

ROUTINE_ADDITIVE_PROCESS_LOSS_CUTS = (
    "temp-create",
    "partial-write",
    "file-fsync",
    "publish",
    "temp-unlink",
    "parent-fsync",
)
ROUTINE_SELECTOR_PROCESS_LOSS_CUTS = (
    "temp-create",
    "partial-write",
    "file-fsync",
    "replace",
    "parent-fsync",
)
ROUTINE_JOURNAL_PROCESS_LOSS_CUTS = (
    "temp-create",
    "partial-write",
    "full-write",
    "replace",
    "parent-fsync",
)
ROUTINE_SMOKE_PROCESS_LOSS_CUTS = (
    "child-return",
    "accepted-journal",
)
ROUTINE_CLEANUP_PROCESS_LOSS_CUTS = (
    "before-unlink",
    "after-unlink",
    "parent-fsync",
)
_ROUTINE_ADDITIVE_PROCESS_LOSS_EXIT = 82
_ROUTINE_JOURNAL_PROCESS_LOSS_EXIT = 104
_ROUTINE_SMOKE_PROCESS_LOSS_EXIT = 111
_ROUTINE_CLEANUP_PROCESS_LOSS_EXIT = 116
_ROUTINE_SELECTOR_PROCESS_LOSS_EXIT = 88
_ROUTINE_TERMINAL_PROCESS_LOSS_EXIT = 101


@dataclass(frozen=True)
class SmokeObservation:
    phase: str
    journal_raw: bytes
    journal: dict[str, Any]
    active_raw: bytes
    deployment_raw: bytes
    receipt_digests: frozenset[str]


class PublicRoutineSmokeBoundary:
    """Observe the public smoke handoff and return phase-specific proof bytes."""

    def __init__(
        self,
        canonical_root: Path,
        staged: object,
        *,
        expected_receipt_digests: frozenset[str],
        candidate_accepted: bool,
        rollback_accepted: bool,
    ) -> None:
        self.canonical_root = canonical_root
        self.staged = staged
        self.expected_receipt_digests = expected_receipt_digests
        candidate_smoke = staged.deployment_value["smoke"]
        prior_smoke = staged.rollback_value["prior_activation_unit"]["smoke"]
        self.outputs = {
            "candidate-smoke": smoke_envelope(candidate_smoke),
            "rollback-smoke": smoke_envelope(prior_smoke),
        }
        self.accepted = {
            "candidate-smoke": candidate_accepted,
            "rollback-smoke": rollback_accepted,
        }
        self.observations: list[SmokeObservation] = []

    @property
    def phases(self) -> list[str]:
        return [item.phase for item in self.observations]

    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        pass_fds: tuple[int, ...],
    ) -> subprocess.CompletedProcess[bytes]:
        if argv != (str(self.canonical_root / "task-witness"), "activation-smoke"):
            raise AssertionError("routine smoke argv disagrees")
        if pass_fds != (3,):
            raise AssertionError("routine smoke descriptor set disagrees")
        journal_raw = (self.canonical_root / "transaction.json").read_bytes()
        journal = canonical_value(journal_raw)
        if frozenset(journal) != JOURNAL_KEYS:
            raise AssertionError("routine smoke journal key set disagrees")
        phase = journal["phase"]
        if phase not in self.outputs:
            raise AssertionError(f"routine smoke phase disagrees: {phase}")
        observation = SmokeObservation(
            phase=phase,
            journal_raw=journal_raw,
            journal=journal,
            active_raw=(self.canonical_root / "active.json").read_bytes(),
            deployment_raw=(self.canonical_root / "deployment.json").read_bytes(),
            receipt_digests=receipt_digest_inventory(self.canonical_root),
        )
        expected_selectors = (
            staged_candidate_selector_raws(self.staged)
            if phase == "candidate-smoke"
            else staged_prior_selector_raws(self.staged)
        )
        assert_smoke_observation(
            observation,
            staged=self.staged,
            phase=phase,
            live_selectors=expected_selectors,
            expected_receipt_digests=self.expected_receipt_digests,
        )
        self.observations.append(observation)
        accepted = self.accepted[phase]
        return subprocess.CompletedProcess(
            argv,
            0 if accepted else 70,
            stdout=self.outputs[phase] if accepted else b"",
            stderr=b"" if accepted else b"rejected\n",
        )


@dataclass(frozen=True)
class RegularFileSnapshot:
    identity: tuple[int, int]
    mode: int
    owner: int
    links: int
    raw: bytes


@dataclass(frozen=True)
class RawReceiptSnapshot:
    file_type: int
    identity: tuple[int, int]
    mode: int
    owner: int
    group: int
    links: int
    size: int
    modified_ns: int
    changed_ns: int
    raw: bytes | None
    digest: str | None


@dataclass(frozen=True)
class DirectorySnapshot:
    identity: tuple[int, int]
    mode: int
    owner: int
    group: int
    links: int
    modified_ns: int
    changed_ns: int


def regular_file_snapshot(root: Path) -> dict[str, RegularFileSnapshot]:
    result: dict[str, RegularFileSnapshot] = {}
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            continue
        result[path.relative_to(root).as_posix()] = RegularFileSnapshot(
            identity=(metadata.st_dev, metadata.st_ino),
            mode=stat.S_IMODE(metadata.st_mode),
            owner=metadata.st_uid,
            links=metadata.st_nlink,
            raw=(
                b""
                if stat.S_IMODE(metadata.st_mode) == 0 and metadata.st_size == 0
                else path.read_bytes()
            ),
        )
    return result


def raw_receipt_inventory(canonical_root: Path) -> dict[str, RawReceiptSnapshot]:
    """Capture receipt evidence without assuming content-address validity."""

    result: dict[str, RawReceiptSnapshot] = {}
    for path in sorted((canonical_root / "receipts").iterdir()):
        metadata = path.lstat()
        raw = path.read_bytes() if stat.S_ISREG(metadata.st_mode) else None
        result[path.name] = RawReceiptSnapshot(
            file_type=stat.S_IFMT(metadata.st_mode),
            identity=(metadata.st_dev, metadata.st_ino),
            mode=stat.S_IMODE(metadata.st_mode),
            owner=metadata.st_uid,
            group=metadata.st_gid,
            links=metadata.st_nlink,
            size=metadata.st_size,
            modified_ns=metadata.st_mtime_ns,
            changed_ns=metadata.st_ctime_ns,
            raw=raw,
            digest=None if raw is None else sha256(raw),
        )
    return result


def directory_snapshot(root: Path) -> dict[str, DirectorySnapshot]:
    result: dict[str, DirectorySnapshot] = {}
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            continue
        result[path.relative_to(root).as_posix()] = DirectorySnapshot(
            identity=(metadata.st_dev, metadata.st_ino),
            mode=stat.S_IMODE(metadata.st_mode),
            owner=metadata.st_uid,
            group=metadata.st_gid,
            links=metadata.st_nlink,
            modified_ns=metadata.st_mtime_ns,
            changed_ns=metadata.st_ctime_ns,
        )
    return result


def routine_additive_artifacts(staged: object) -> tuple[object, ...]:
    selector_roles = {
        "active-record",
        "deployment-alias",
        "prior-active-record",
        "prior-deployment-alias",
    }
    return tuple(
        sorted(
            (item for item in staged.artifacts if item.role not in selector_roles),
            key=lambda item: item.relative_path,
        )
    )


@dataclass(frozen=True)
class RoutineCleanupStep:
    operation: str
    index: int
    role: str
    relative_path: str
    artifact: object | None


def routine_cleanup_steps(
    staged: object,
    baseline_files: Mapping[str, object],
    baseline_directories: Mapping[str, object],
) -> tuple[RoutineCleanupStep, ...]:
    artifacts = routine_additive_artifacts(staged)
    rollback = staged_artifact(staged, "rollback-receipt")
    deployment = staged_artifact(staged, "deployment-receipt")
    owned = tuple(
        artifact
        for artifact in artifacts
        if artifact.relative_path not in baseline_files
    )
    if rollback not in owned or deployment not in owned:
        raise AssertionError("routine cleanup receipt ownership disagrees")
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


def routine_install_temporary_path(
    artifact: object,
    transaction_id: str,
    step_index: int,
) -> Path:
    return Path(artifact.installed_path).parent / (
        f".task-witness-install-{transaction_id}-{step_index}.tmp"
    )


def run_routine_additive_process_loss_cut(
    deployment: object,
    activation: object,
    *,
    artifact_index: int,
    cut: str,
) -> None:
    """Lose the real activation process after one additive persistence seam."""

    if cut not in ROUTINE_ADDITIVE_PROCESS_LOSS_CUTS:
        raise AssertionError(f"unknown routine additive cut: {cut}")
    if type(artifact_index) is not int or artifact_index < 0:
        raise AssertionError("routine additive artifact index is invalid")
    child = os.fork()
    if child == 0:
        original_open = deployment.os.open
        original_write = deployment.os.write
        original_fsync = deployment.os.fsync
        original_fstat = deployment.os.fstat
        original_link = deployment.os.link
        original_unlink = deployment.os.unlink
        original_write_all = deployment._write_all
        state: dict[str, Any] = {
            "temp_fd": None,
            "temp_name": None,
            "temp_parent_identity": None,
            "temp_unlinked": False,
        }

        def process_loss() -> None:
            os._exit(_ROUTINE_ADDITIVE_PROCESS_LOSS_EXIT)

        def observed_open(*args: object, **kwargs: object) -> int:
            descriptor = original_open(*args, **kwargs)
            name = args[0] if args else kwargs.get("path")
            if (
                isinstance(name, str)
                and name.startswith(".task-witness-install-")
                and name.endswith(f"-{artifact_index}.tmp")
            ):
                parent_fd = kwargs.get("dir_fd")
                if type(parent_fd) is not int:
                    os._exit(83)
                parent = original_fstat(parent_fd)
                state.update(
                    {
                        "temp_fd": descriptor,
                        "temp_name": name,
                        "temp_parent_identity": (parent.st_dev, parent.st_ino),
                    }
                )
                if cut == "temp-create":
                    process_loss()
            return descriptor

        def observed_write_all(descriptor: int, raw: bytes, label: str) -> None:
            if descriptor == state["temp_fd"] and cut == "partial-write":
                length = max(1, len(raw) // 2)
                offset = 0
                while offset < length:
                    written = original_write(descriptor, raw[offset:length])
                    if written <= 0:
                        os._exit(84)
                    offset += written
                process_loss()
            original_write_all(descriptor, raw, label)

        def observed_fsync(descriptor: int) -> None:
            original_fsync(descriptor)
            if descriptor == state["temp_fd"] and cut == "file-fsync":
                process_loss()
            if state["temp_unlinked"] and cut == "parent-fsync":
                synchronized = original_fstat(descriptor)
                if (
                    synchronized.st_dev,
                    synchronized.st_ino,
                ) == state["temp_parent_identity"]:
                    process_loss()

        def observed_link(*args: object, **kwargs: object) -> None:
            original_link(*args, **kwargs)
            source = args[0] if args else kwargs.get("src")
            if source == state["temp_name"] and cut == "publish":
                process_loss()

        def observed_unlink(*args: object, **kwargs: object) -> None:
            original_unlink(*args, **kwargs)
            name = args[0] if args else kwargs.get("path")
            if name == state["temp_name"]:
                state["temp_unlinked"] = True
                if cut == "temp-unlink":
                    process_loss()

        deployment.os.open = observed_open
        deployment.os.fsync = observed_fsync
        deployment.os.link = observed_link
        deployment.os.unlink = observed_unlink
        deployment._write_all = observed_write_all

        def unexpected_smoke(*args: object, **kwargs: object) -> None:
            del args, kwargs
            os._exit(85)

        deployment._spawn_activation_smoke_child = unexpected_smoke
        try:
            deployment.activate_staged(activation)
        except (AssertionError, OSError, TypeError, deployment.DeploymentError):
            os._exit(86)
        os._exit(87)
    waited, status = os.waitpid(child, 0)
    if waited != child or not os.WIFEXITED(status):
        raise AssertionError(f"routine additive cut {cut} did not exit normally")
    exit_status = os.WEXITSTATUS(status)
    if exit_status != _ROUTINE_ADDITIVE_PROCESS_LOSS_EXIT:
        raise AssertionError(
            f"routine additive cut {cut} exited at unexpected boundary {exit_status}"
        )


def run_routine_selector_process_loss_after_replace(
    deployment: object,
    activation: object,
    *,
    direction: str,
    selector_index: int,
) -> None:
    """Lose the real activation process after one durable selector replacement."""

    if direction not in {"candidate", "prior"}:
        raise AssertionError("routine selector direction is invalid")
    if selector_index not in {0, 1}:
        raise AssertionError("routine selector index is invalid")
    child = os.fork()
    if child == 0:
        original_replace = deployment._replace_activation_selector

        def observed_replace(*args: object, **kwargs: object) -> None:
            original_replace(*args, **kwargs)
            if (
                kwargs.get("direction") == direction
                and kwargs.get("index") == selector_index
            ):
                os._exit(_ROUTINE_SELECTOR_PROCESS_LOSS_EXIT)

        deployment._replace_activation_selector = observed_replace

        def unexpected_smoke(*args: object, **kwargs: object) -> None:
            del args, kwargs
            os._exit(89)

        deployment._spawn_activation_smoke_child = unexpected_smoke
        try:
            deployment.activate_staged(activation)
        except (AssertionError, OSError, TypeError, deployment.DeploymentError):
            os._exit(90)
        os._exit(91)
    waited, status = os.waitpid(child, 0)
    if waited != child or not os.WIFEXITED(status):
        raise AssertionError("routine selector cut did not exit normally")
    exit_status = os.WEXITSTATUS(status)
    if exit_status != _ROUTINE_SELECTOR_PROCESS_LOSS_EXIT:
        raise AssertionError(
            f"routine selector cut exited at unexpected boundary {exit_status}"
        )


def run_routine_terminal_process_loss_cut(
    deployment: object,
    activation: object,
    staged: object,
    *,
    outcome: str = "candidate-active",
) -> None:
    """Lose the real activation process after the terminal journal is durable."""

    if outcome not in {"candidate-active", "restored-prior", "recovery-required"}:
        raise AssertionError(f"unknown routine terminal outcome: {outcome}")
    child = os.fork()
    if child == 0:
        original_write = deployment._write_activation_journal
        candidate_output = smoke_envelope(staged.deployment_value["smoke"])
        prior_output = smoke_envelope(
            staged.rollback_value["prior_activation_unit"]["smoke"]
        )

        def accepted_smoke(
            argv: tuple[str, ...],
            *,
            pass_fds: tuple[int, ...],
        ) -> subprocess.CompletedProcess[bytes]:
            del pass_fds
            phase = canonical_value(
                (
                    Path(activation.deployment.canonical_root) / "transaction.json"
                ).read_bytes()
            )["phase"]
            if phase == "candidate-smoke":
                candidate_active = outcome == "candidate-active"
                return subprocess.CompletedProcess(
                    argv,
                    0 if candidate_active else 70,
                    stdout=candidate_output if candidate_active else b"",
                    stderr=b"" if candidate_active else b"rejected\n",
                )
            if phase == "rollback-smoke":
                restored = outcome == "restored-prior"
                return subprocess.CompletedProcess(
                    argv,
                    0 if restored else 70,
                    stdout=prior_output if restored else b"",
                    stderr=b"" if restored else b"rejected\n",
                )
            os._exit(121)

        def observed_write(canonical_root_fd: int, journal: object) -> None:
            original_write(canonical_root_fd, journal)
            if journal.value["phase"] == "terminal":
                os._exit(_ROUTINE_TERMINAL_PROCESS_LOSS_EXIT)

        deployment._spawn_activation_smoke_child = accepted_smoke
        deployment._write_activation_journal = observed_write
        try:
            deployment.activate_staged(activation)
        except (AssertionError, OSError, TypeError, deployment.DeploymentError):
            os._exit(102)
        os._exit(103)
    waited, status = os.waitpid(child, 0)
    if waited != child or not os.WIFEXITED(status):
        raise AssertionError("routine terminal cut did not exit normally")
    exit_status = os.WEXITSTATUS(status)
    if exit_status != _ROUTINE_TERMINAL_PROCESS_LOSS_EXIT:
        raise AssertionError(
            f"routine terminal cut exited at unexpected boundary {exit_status}"
        )


def run_routine_journal_process_loss_cut(
    deployment: object,
    activation: object,
    staged: object,
    *,
    generation: str,
    cut: str,
) -> bytes:
    """Lose the real process within one selected routine journal generation."""

    generations = {
        "frozen",
        "candidate-acceptance",
        "prior-restoring",
        "rollback-acceptance",
    }
    if generation not in generations:
        raise AssertionError(f"unknown routine journal generation: {generation}")
    if cut not in ROUTINE_JOURNAL_PROCESS_LOSS_CUTS:
        raise AssertionError(f"unknown routine journal cut: {cut}")
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        original_open = deployment.os.open
        original_write = deployment.os.write
        original_fsync = deployment.os.fsync
        original_replace = deployment.os.replace
        original_write_all = deployment._write_all
        original_journal_write = deployment._write_activation_journal
        state: dict[str, Any] = {
            "armed": False,
            "target_name": None,
            "target_fd": None,
            "replaced": False,
        }

        def matches(value: Mapping[str, Any]) -> bool:
            phase = value["phase"]
            if generation == "frozen":
                return phase == "frozen"
            if generation == "candidate-acceptance":
                return (
                    phase == "candidate-smoke"
                    and value["candidate_smoke_acceptance"] is not None
                )
            if generation == "prior-restoring":
                return (
                    phase == "prior-restoring" and value["pending_step"]["index"] == 0
                )
            return (
                phase == "rollback-smoke"
                and value["rollback_smoke_acceptance"] is not None
            )

        def process_loss() -> None:
            os.close(write_fd)
            os._exit(_ROUTINE_JOURNAL_PROCESS_LOSS_EXIT)

        def report(raw: bytes) -> None:
            offset = 0
            while offset < len(raw):
                written = original_write(write_fd, raw[offset:])
                if written <= 0:
                    os._exit(105)
                offset += written

        def observed_journal_write(canonical_root_fd: int, journal: object) -> None:
            if matches(journal.value):
                if state["armed"]:
                    os._exit(106)
                state["armed"] = True
                state["target_name"] = (
                    f"transaction.{journal.value['transaction_id']}."
                    f"{journal.value['sequence']}.tmp"
                )
                report(journal.raw)
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
                        os._exit(107)
                    offset += written
                process_loss()
            original_write_all(descriptor, raw, label)
            if descriptor == state["target_fd"] and cut == "full-write":
                process_loss()

        def observed_replace(*args: object, **kwargs: object) -> None:
            original_replace(*args, **kwargs)
            source = args[0] if args else kwargs.get("src")
            if state["armed"] and source == state["target_name"]:
                state["replaced"] = True
                if cut == "replace":
                    process_loss()

        def observed_fsync(descriptor: int) -> None:
            original_fsync(descriptor)
            if state["replaced"] and cut == "parent-fsync":
                process_loss()

        rollback_flow = generation in {
            "prior-restoring",
            "rollback-acceptance",
        }
        candidate_output = smoke_envelope(staged.deployment_value["smoke"])
        prior_output = smoke_envelope(
            staged.rollback_value["prior_activation_unit"]["smoke"]
        )

        def phase_smoke(
            argv: tuple[str, ...],
            *,
            pass_fds: tuple[int, ...],
        ) -> subprocess.CompletedProcess[bytes]:
            del pass_fds
            phase = canonical_value(
                (
                    Path(activation.deployment.canonical_root) / "transaction.json"
                ).read_bytes()
            )["phase"]
            if phase == "candidate-smoke":
                return subprocess.CompletedProcess(
                    argv,
                    70 if rollback_flow else 0,
                    stdout=b"" if rollback_flow else candidate_output,
                    stderr=b"rejected\n" if rollback_flow else b"",
                )
            if phase == "rollback-smoke":
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    stdout=prior_output,
                    stderr=b"",
                )
            os._exit(108)

        deployment.os.open = observed_open
        deployment.os.fsync = observed_fsync
        deployment.os.replace = observed_replace
        deployment._write_all = observed_write_all
        deployment._write_activation_journal = observed_journal_write
        deployment._spawn_activation_smoke_child = phase_smoke
        try:
            deployment.activate_staged(activation)
        except (AssertionError, OSError, TypeError, deployment.DeploymentError):
            os._exit(109)
        os._exit(110)
    os.close(write_fd)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(read_fd, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    os.close(read_fd)
    waited, status = os.waitpid(child, 0)
    if waited != child or not os.WIFEXITED(status):
        raise AssertionError(f"routine journal cut {cut} did not exit normally")
    exit_status = os.WEXITSTATUS(status)
    if exit_status != _ROUTINE_JOURNAL_PROCESS_LOSS_EXIT:
        raise AssertionError(
            f"routine journal cut {cut} exited at unexpected boundary {exit_status}"
        )
    target_raw = b"".join(chunks)
    canonical_value(target_raw)
    return target_raw


def run_routine_smoke_process_loss_cut(
    deployment: object,
    activation: object,
    staged: object,
    *,
    phase: str,
    cut: str,
) -> bytes:
    """Lose the real process before or after durable smoke acceptance."""

    if phase not in {"candidate-smoke", "rollback-smoke"}:
        raise AssertionError(f"unknown routine smoke phase: {phase}")
    if cut not in ROUTINE_SMOKE_PROCESS_LOSS_CUTS:
        raise AssertionError(f"unknown routine smoke cut: {cut}")
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        original_run = deployment._run_activation_smoke
        original_write = deployment._write_activation_journal
        candidate_output = smoke_envelope(staged.deployment_value["smoke"])
        prior_output = smoke_envelope(
            staged.rollback_value["prior_activation_unit"]["smoke"]
        )

        def process_loss() -> None:
            os.close(write_fd)
            os._exit(_ROUTINE_SMOKE_PROCESS_LOSS_EXIT)

        def report(marker: bytes) -> None:
            if os.write(write_fd, marker) != len(marker):
                os._exit(112)

        def smoke_child(
            argv: tuple[str, ...],
            *,
            pass_fds: tuple[int, ...],
        ) -> subprocess.CompletedProcess[bytes]:
            del pass_fds
            live_phase = canonical_value(
                (
                    Path(activation.deployment.canonical_root) / "transaction.json"
                ).read_bytes()
            )["phase"]
            if live_phase == "candidate-smoke":
                report(b"C")
                rollback_flow = phase == "rollback-smoke"
                return subprocess.CompletedProcess(
                    argv,
                    70 if rollback_flow else 0,
                    stdout=b"" if rollback_flow else candidate_output,
                    stderr=b"rejected\n" if rollback_flow else b"",
                )
            if live_phase == "rollback-smoke":
                report(b"R")
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    stdout=prior_output,
                    stderr=b"",
                )
            os._exit(113)

        def observed_run(*args: object, **kwargs: object):
            result = original_run(*args, **kwargs)
            live_phase = canonical_value(
                (
                    Path(activation.deployment.canonical_root) / "transaction.json"
                ).read_bytes()
            )["phase"]
            if cut == "child-return" and live_phase == phase:
                process_loss()
            return result

        def observed_write(canonical_root_fd: int, journal: object) -> None:
            original_write(canonical_root_fd, journal)
            value = journal.value
            accepted = (
                value["candidate_smoke_acceptance"]
                if phase == "candidate-smoke"
                else value["rollback_smoke_acceptance"]
            )
            if (
                cut == "accepted-journal"
                and value["phase"] == phase
                and accepted is not None
            ):
                process_loss()

        deployment._spawn_activation_smoke_child = smoke_child
        deployment._run_activation_smoke = observed_run
        deployment._write_activation_journal = observed_write
        try:
            deployment.activate_staged(activation)
        except (AssertionError, OSError, TypeError, deployment.DeploymentError):
            os._exit(114)
        os._exit(115)
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
        raise AssertionError(f"routine smoke cut {cut} did not exit normally")
    exit_status = os.WEXITSTATUS(status)
    if exit_status != _ROUTINE_SMOKE_PROCESS_LOSS_EXIT:
        raise AssertionError(
            f"routine smoke cut {cut} exited at unexpected boundary {exit_status}"
        )
    return b"".join(chunks)


def run_routine_cleanup_process_loss_cut(
    deployment: object,
    activation: object,
    staged: object,
    *,
    cleanup_steps: tuple[RoutineCleanupStep, ...],
    step_index: int,
    cut: str,
) -> None:
    """Lose the real process around one R-first/B-last cleanup unlink."""

    if step_index not in range(len(cleanup_steps)):
        raise AssertionError("routine cleanup index is invalid")
    if cut not in ROUTINE_CLEANUP_PROCESS_LOSS_CUTS:
        raise AssertionError(f"unknown routine cleanup cut: {cut}")
    target = cleanup_steps[step_index]
    child = os.fork()
    if child == 0:
        original_write = deployment._write_activation_journal
        original_unlink = deployment.os.unlink
        original_rmdir = deployment.os.rmdir
        original_fsync = deployment.os.fsync
        state = {
            "armed": False,
            "removed": False,
            "target_parent_identity": None,
        }
        prior_output = smoke_envelope(
            staged.rollback_value["prior_activation_unit"]["smoke"]
        )

        def process_loss() -> None:
            os._exit(_ROUTINE_CLEANUP_PROCESS_LOSS_EXIT)

        def phase_smoke(
            argv: tuple[str, ...],
            *,
            pass_fds: tuple[int, ...],
        ) -> subprocess.CompletedProcess[bytes]:
            del pass_fds
            phase = canonical_value(
                (
                    Path(activation.deployment.canonical_root) / "transaction.json"
                ).read_bytes()
            )["phase"]
            if phase == "candidate-smoke":
                return subprocess.CompletedProcess(
                    argv,
                    70,
                    stdout=b"",
                    stderr=b"rejected\n",
                )
            if phase == "rollback-smoke":
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    stdout=prior_output,
                    stderr=b"",
                )
            os._exit(117)

        def observed_write(canonical_root_fd: int, journal: object) -> None:
            original_write(canonical_root_fd, journal)
            pending = journal.value["pending_step"]
            if (
                journal.value["phase"] == "rollback-cleaning"
                and pending["index"] == step_index
            ):
                parent = (
                    Path(activation.deployment.canonical_root)
                    / Path(target.relative_path).parent
                ).lstat()
                state["target_parent_identity"] = (
                    parent.st_dev,
                    parent.st_ino,
                )
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
                    os._exit(118)
                parent = os.fstat(parent_fd)
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
                    os._exit(121)
                parent = os.fstat(parent_fd)
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
                parent = os.fstat(descriptor)
                if (parent.st_dev, parent.st_ino) == state["target_parent_identity"]:
                    process_loss()

        deployment._spawn_activation_smoke_child = phase_smoke
        deployment._write_activation_journal = observed_write
        deployment.os.unlink = observed_unlink
        deployment.os.rmdir = observed_rmdir
        deployment.os.fsync = observed_fsync
        try:
            deployment.activate_staged(activation)
        except (AssertionError, OSError, TypeError, deployment.DeploymentError):
            os._exit(119)
        os._exit(120)
    waited, status = os.waitpid(child, 0)
    if waited != child or not os.WIFEXITED(status):
        raise AssertionError(f"routine cleanup cut {cut} did not exit normally")
    exit_status = os.WEXITSTATUS(status)
    if exit_status != _ROUTINE_CLEANUP_PROCESS_LOSS_EXIT:
        raise AssertionError(
            f"routine cleanup cut {cut} exited at unexpected boundary {exit_status}"
        )


def routine_selector_temporary_path(
    canonical_root: Path,
    transaction_id: str,
    direction: str,
    selector_index: int,
) -> Path:
    return canonical_root / (
        f".task-witness-selector-{transaction_id}-{direction}-{selector_index}.tmp"
    )


def run_routine_selector_process_loss_cut(
    deployment: object,
    activation: object,
    *,
    direction: str,
    selector_index: int,
    cut: str,
) -> None:
    """Lose the real activation process within one selector replacement."""

    if direction not in {"candidate", "prior"}:
        raise AssertionError("routine selector direction is invalid")
    if selector_index not in {0, 1}:
        raise AssertionError("routine selector index is invalid")
    if cut not in ROUTINE_SELECTOR_PROCESS_LOSS_CUTS:
        raise AssertionError(f"unknown routine selector cut: {cut}")
    child = os.fork()
    if child == 0:
        original_open = deployment.os.open
        original_write = deployment.os.write
        original_fsync = deployment.os.fsync
        original_replace = deployment.os.replace
        original_write_all = deployment._write_all
        state: dict[str, Any] = {
            "temp_fd": None,
            "temp_name": None,
            "replaced": False,
            "smoke_count": 0,
        }
        expected_suffix = f"-{direction}-{selector_index}.tmp"

        def process_loss() -> None:
            os._exit(_ROUTINE_SELECTOR_PROCESS_LOSS_EXIT)

        def observed_open(*args: object, **kwargs: object) -> int:
            descriptor = original_open(*args, **kwargs)
            name = args[0] if args else kwargs.get("path")
            if (
                isinstance(name, str)
                and name.startswith(".task-witness-selector-")
                and name.endswith(expected_suffix)
            ):
                state["temp_fd"] = descriptor
                state["temp_name"] = name
                if cut == "temp-create":
                    process_loss()
            return descriptor

        def observed_write_all(descriptor: int, raw: bytes, label: str) -> None:
            if descriptor == state["temp_fd"] and cut == "partial-write":
                length = max(1, len(raw) // 2)
                offset = 0
                while offset < length:
                    written = original_write(descriptor, raw[offset:length])
                    if written <= 0:
                        os._exit(92)
                    offset += written
                process_loss()
            original_write_all(descriptor, raw, label)

        def observed_fsync(descriptor: int) -> None:
            original_fsync(descriptor)
            if descriptor == state["temp_fd"] and cut == "file-fsync":
                process_loss()
            if state["replaced"] and cut == "parent-fsync":
                process_loss()

        def observed_replace(*args: object, **kwargs: object) -> None:
            original_replace(*args, **kwargs)
            source = args[0] if args else kwargs.get("src")
            if source == state["temp_name"]:
                state["replaced"] = True
                if cut == "replace":
                    process_loss()

        deployment.os.open = observed_open
        deployment.os.fsync = observed_fsync
        deployment.os.replace = observed_replace
        deployment._write_all = observed_write_all

        def phase_smoke(
            argv: tuple[str, ...],
            *,
            pass_fds: tuple[int, ...],
        ) -> subprocess.CompletedProcess[bytes]:
            del pass_fds
            state["smoke_count"] += 1
            if direction == "prior" and state["smoke_count"] == 1:
                return subprocess.CompletedProcess(
                    argv,
                    70,
                    stdout=b"",
                    stderr=b"rejected\n",
                )
            os._exit(93)

        deployment._spawn_activation_smoke_child = phase_smoke
        try:
            deployment.activate_staged(activation)
        except (AssertionError, OSError, TypeError, deployment.DeploymentError):
            os._exit(94)
        os._exit(95)
    waited, status = os.waitpid(child, 0)
    if waited != child or not os.WIFEXITED(status):
        raise AssertionError(f"routine selector cut {cut} did not exit normally")
    exit_status = os.WEXITSTATUS(status)
    if exit_status != _ROUTINE_SELECTOR_PROCESS_LOSS_EXIT:
        raise AssertionError(
            f"routine selector cut {cut} exited at unexpected boundary {exit_status}"
        )


def assert_routine_selector_process_loss_state(
    canonical_root: Path,
    staged: object,
    prior_selectors: tuple[bytes, bytes],
    journal: dict[str, Any],
    *,
    direction: str,
    selector_index: int,
    cut: str,
) -> None:
    candidate_selectors = staged_candidate_selector_raws(staged)
    targets = candidate_selectors if direction == "candidate" else prior_selectors
    currents = list(
        prior_selectors if direction == "candidate" else candidate_selectors
    )
    for index in range(selector_index):
        currents[index] = targets[index]
    phase = "selector-switching" if direction == "candidate" else "prior-restoring"
    role = ("active-record", "deployment-alias")[selector_index]
    if journal["phase"] != phase or journal["pending_step"] != {
        "operation": "replace-selector",
        "index": selector_index,
        "role": role,
    }:
        raise AssertionError("routine selector journal cursor disagrees")
    additive_count = len(routine_additive_artifacts(staged))
    expected_sequence = (
        4 + additive_count + selector_index
        if direction == "candidate"
        else 7 + additive_count + selector_index
    )
    if journal["sequence"] != expected_sequence:
        raise AssertionError("routine selector journal sequence disagrees")
    temporary = routine_selector_temporary_path(
        canonical_root,
        journal["transaction_id"],
        direction,
        selector_index,
    )
    if cut in {"temp-create", "partial-write", "file-fsync"}:
        expected_live = tuple(currents)
        metadata = temporary.lstat()
        target_raw = targets[selector_index]
        if cut == "temp-create":
            expected_raw = b""
        elif cut == "partial-write":
            expected_raw = target_raw[: max(1, len(target_raw) // 2)]
        else:
            expected_raw = target_raw
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or temporary.read_bytes() != expected_raw
        ):
            raise AssertionError("routine selector temporary disposition disagrees")
    else:
        currents[selector_index] = targets[selector_index]
        expected_live = tuple(currents)
        if temporary.exists():
            raise AssertionError("routine selector temporary remains after replace")
    if selector_raws(canonical_root) != expected_live:
        raise AssertionError("routine selector mixed live state disagrees")


def assert_routine_additive_process_loss_state(
    artifacts: tuple[object, ...],
    baseline: dict[str, RegularFileSnapshot],
    journal: dict[str, Any],
    *,
    artifact_index: int,
    cut: str,
) -> None:
    pending = journal["pending_step"]
    artifact = artifacts[artifact_index]
    if journal["phase"] != "additive-installing" or pending != {
        "operation": "install",
        "index": artifact_index,
        "role": artifact.role,
    }:
        raise AssertionError("routine additive journal cursor disagrees")
    if journal["sequence"] != 4 + artifact_index:
        raise AssertionError("routine additive journal sequence disagrees")
    temporary = routine_install_temporary_path(
        artifact,
        journal["transaction_id"],
        artifact_index,
    )
    final = Path(artifact.installed_path)
    for index, item in enumerate(artifacts):
        relative = item.relative_path
        if index < artifact_index:
            if not Path(item.installed_path).exists():
                raise AssertionError(f"routine additive prefix is absent: {item.role}")
            if Path(item.installed_path).read_bytes() != item.raw:
                raise AssertionError(f"routine additive prefix disagrees: {item.role}")
        elif index > artifact_index and relative not in baseline:
            if Path(item.installed_path).exists():
                raise AssertionError(f"routine additive suffix is present: {item.role}")
    if cut in {"temp-create", "partial-write", "file-fsync"}:
        if final.exists():
            raise AssertionError("routine additive final exists before publication")
        metadata = temporary.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise AssertionError("routine additive temporary disposition disagrees")
        if cut == "temp-create":
            expected_raw: bytes | None = None
            expected_mode = 0
        elif cut == "partial-write":
            expected_raw = artifact.raw[: max(1, len(artifact.raw) // 2)]
            expected_mode = 0o600
        else:
            expected_raw = artifact.raw
            expected_mode = artifact.installed["mode"]
        if (
            metadata.st_size != 0
            if expected_raw is None
            else temporary.read_bytes() != expected_raw
        ) or stat.S_IMODE(metadata.st_mode) != expected_mode:
            raise AssertionError("routine additive temporary bytes/mode disagree")
        return
    final_metadata = final.lstat()
    if final.read_bytes() != artifact.raw:
        raise AssertionError("routine additive published bytes disagree")
    if cut == "publish":
        temporary_metadata = temporary.lstat()
        if (
            final_metadata.st_nlink != 2
            or temporary_metadata.st_nlink != 2
            or (final_metadata.st_dev, final_metadata.st_ino)
            != (temporary_metadata.st_dev, temporary_metadata.st_ino)
        ):
            raise AssertionError("routine additive publication links disagree")
    else:
        if temporary.exists() or final_metadata.st_nlink != 1:
            raise AssertionError("routine additive publication cleanup disagrees")


def receipt_digest_inventory(
    canonical_root: Path,
    *,
    allowed_temporary: Path | None = None,
    allowed_final_links: frozenset[int] = frozenset({1}),
) -> frozenset[str]:
    receipts = canonical_root / "receipts"
    result: set[str] = set()
    for path in receipts.iterdir():
        if allowed_temporary is not None and path == allowed_temporary:
            continue
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or not path.name.startswith("sha256-"):
            raise AssertionError("receipt inventory contains a non-receipt entry")
        if (
            stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink not in allowed_final_links
        ):
            raise AssertionError("receipt disposition disagrees")
        digest = path.name.removeprefix("sha256-").removesuffix(".json")
        if path.name != f"sha256-{digest}.json" or sha256(path.read_bytes()) != digest:
            raise AssertionError("receipt filename/content binding disagrees")
        result.add(digest)
    return frozenset(result)


def staged_artifact(staged: object, role: str) -> object:
    matches = [item for item in staged.artifacts if item.role == role]
    if len(matches) != 1:
        raise AssertionError(f"staged {role} artifact is not unique")
    return matches[0]


def selector_raws(canonical_root: Path) -> tuple[bytes, bytes]:
    return (
        (canonical_root / "active.json").read_bytes(),
        (canonical_root / "deployment.json").read_bytes(),
    )


def staged_candidate_selector_raws(staged: object) -> tuple[bytes, bytes]:
    return (
        staged_artifact(staged, "active-record").raw,
        staged_artifact(staged, "deployment-alias").raw,
    )


def staged_prior_selector_raws(staged: object) -> tuple[bytes, bytes]:
    return (
        staged_artifact(staged, "prior-active-record").raw,
        staged_artifact(staged, "prior-deployment-alias").raw,
    )


def expected_active_receipt_inventory(
    prior_deployment_raw: bytes,
    staged: object,
) -> frozenset[str]:
    prior = canonical_value(prior_deployment_raw)
    return frozenset(
        {
            sha256(prior_deployment_raw),
            prior["rollback"]["sha256"],
            sha256(staged.deployment_raw),
            sha256(staged.rollback_raw),
        }
    )


def assert_existing_files_are_immutable(
    before: dict[str, RegularFileSnapshot],
    canonical_root: Path,
    *,
    changed: frozenset[str],
) -> None:
    after = regular_file_snapshot(canonical_root)
    for relative, expected in before.items():
        if relative in changed:
            continue
        if after.get(relative) != expected:
            raise AssertionError(f"preexisting file changed: {relative}")


def assert_staged_additions_installed(staged: object) -> None:
    for artifact in staged.artifacts:
        if artifact.role in {
            "prior-active-record",
            "prior-deployment-alias",
        }:
            continue
        target = Path(artifact.installed["path"])
        metadata = target.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise AssertionError(f"installed {artifact.role} is not a regular file")
        if metadata.st_uid != artifact.installed["owner"]:
            raise AssertionError(f"installed {artifact.role} owner disagrees")
        if stat.S_IMODE(metadata.st_mode) != artifact.installed["mode"]:
            raise AssertionError(f"installed {artifact.role} mode disagrees")
        if metadata.st_nlink != 1:
            raise AssertionError(f"installed {artifact.role} link count disagrees")
        if target.read_bytes() != artifact.raw:
            raise AssertionError(f"installed {artifact.role} bytes disagree")


def assert_no_transaction_residue(canonical_root: Path) -> None:
    if (canonical_root / "transaction.json").exists():
        raise AssertionError("activation journal remains installed")
    temporaries = tuple(
        sorted(
            {
                *canonical_root.rglob(".task-witness-*.tmp"),
                *canonical_root.glob("transaction.*.tmp"),
            }
        )
    )
    if temporaries:
        raise AssertionError(f"activation temporaries remain: {temporaries!r}")


def exact_live_journal(canonical_root: Path) -> bytes:
    path = canonical_root / "transaction.json"
    raw = path.read_bytes()
    value = canonical_value(raw)
    if frozenset(value) != JOURNAL_KEYS:
        raise AssertionError("live routine journal key set disagrees")
    if value["content_sha256"] != sha256(
        json.dumps(
            {key: item for key, item in value.items() if key != "content_sha256"},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ):
        raise AssertionError("live routine journal content digest disagrees")
    return raw


def assert_smoke_observation(
    observation: SmokeObservation,
    *,
    staged: object,
    phase: str,
    live_selectors: tuple[bytes, bytes],
    expected_receipt_digests: frozenset[str],
) -> None:
    if observation.phase != phase:
        raise AssertionError("routine smoke phase disagrees")
    journal = observation.journal
    candidate_receipt_sha256 = sha256(staged.deployment_raw)
    prior_receipt_sha256 = staged.plan.precondition.receipt_sha256
    rollback_receipt_sha256 = sha256(staged.rollback_raw)
    candidate_smoke = _plain(staged.deployment_value["smoke"])
    prior_smoke = _plain(staged.rollback_value["prior_activation_unit"]["smoke"])
    target_receipt_sha256, target_smoke = (
        (candidate_receipt_sha256, candidate_smoke)
        if phase == "candidate-smoke"
        else (prior_receipt_sha256, prior_smoke)
    )
    expected_candidate = {
        "state": "active",
        "deployment_receipt": _plain(
            staged_artifact(staged, "deployment-alias").installed
        ),
        "active_record": _plain(staged_artifact(staged, "active-record").installed),
        "control_set": _plain(staged.deployment_value["control_set"]),
        "smoke": candidate_smoke,
    }
    expected_prior = _plain(staged.rollback_value["prior_activation_unit"])
    if journal["candidate"] != expected_candidate:
        raise AssertionError("routine candidate transaction authority disagrees")
    if journal["prior"] != expected_prior:
        raise AssertionError("routine prior transaction authority disagrees")
    expected_rollback_authority = {
        "receipt_path": staged.stage_value["rollback_receipt"]["path"],
        "receipt_sha256": rollback_receipt_sha256,
        "target_state": "active",
    }
    if journal["rollback_authority"] != expected_rollback_authority:
        raise AssertionError("routine rollback authority disagrees")
    expected_preimage = {
        "manifest_path": staged.stage_value["rollback_receipt"]["path"],
        "manifest_sha256": rollback_receipt_sha256,
        "artifacts": _plain(staged.rollback_value["selector_preimage"]),
        "external_dependencies": _plain(staged.rollback_value["external_dependencies"]),
    }
    if journal["preimage"] != expected_preimage:
        raise AssertionError("routine rollback preimage disagrees")
    expected_stage = {
        "receipt_path": str(staged.stage_path),
        "receipt_sha256": sha256(staged.stage_raw),
        "plan_sha256": staged.stage_value["plan_sha256"],
        "authorization_sha256": staged.stage_value["authorization"]["sha256"],
        "maintenance_transaction_sha256": staged.stage_value[
            "maintenance_transaction_sha256"
        ],
    }
    if journal["stage"] != expected_stage:
        raise AssertionError("routine stage transaction authority disagrees")
    if (
        journal["outer_maintenance_transaction_sha256"]
        != staged.stage_value["maintenance_transaction_sha256"]
    ):
        raise AssertionError("routine outer maintenance authority disagrees")
    expected_lock = _plain(staged.rollback_value["activation_lock"])
    if journal["activation_lock"] != expected_lock:
        raise AssertionError("routine activation lock authority disagrees")
    expected_projection = {
        "schema_version": 1,
        "contract": "task-witness-smoke-projection-v1",
        "challenge": "task-witness-activation-smoke-v1",
        "accepted": True,
    }
    if target_smoke["expected_projection"] != expected_projection:
        raise AssertionError("routine smoke challenge disagrees")
    expected_handoff = {
        "target_deployment_receipt_sha256": target_receipt_sha256,
        "smoke_bundle_sha256": target_smoke["bundle"]["sha256"],
        "smoke_trust_context_sha256": target_smoke["trust_context"]["sha256"],
    }
    if journal["smoke_handoff"] != expected_handoff:
        raise AssertionError("routine phase-selected smoke handoff disagrees")
    if (observation.active_raw, observation.deployment_raw) != live_selectors:
        raise AssertionError("routine smoke live selector target disagrees")
    if observation.receipt_digests != expected_receipt_digests:
        raise AssertionError("routine smoke retained receipt closure disagrees")
    if sha256(observation.deployment_raw) != target_receipt_sha256:
        raise AssertionError("routine smoke selected the wrong current receipt")
    if target_smoke["expected_anchor"]["active_record_sha256"] != sha256(
        observation.active_raw
    ):
        raise AssertionError("routine smoke selected the wrong active record")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def assert_selector_has_distinct_retained_copy(
    canonical_root: Path,
    deployment_raw: bytes,
) -> None:
    digest = sha256(deployment_raw)
    alias = canonical_root / "deployment.json"
    retained = canonical_root / "receipts" / f"sha256-{digest}.json"
    if alias.read_bytes() != deployment_raw or retained.read_bytes() != deployment_raw:
        raise AssertionError("deployment alias/retained receipt bytes disagree")
    alias_metadata = alias.lstat()
    retained_metadata = retained.lstat()
    for label, metadata in (
        ("deployment alias", alias_metadata),
        ("retained deployment receipt", retained_metadata),
    ):
        if not stat.S_ISREG(metadata.st_mode):
            raise AssertionError(f"{label} is not a regular file")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise AssertionError(f"{label} mode disagrees")
        if metadata.st_uid != os.geteuid():
            raise AssertionError(f"{label} owner disagrees")
        if metadata.st_nlink != 1:
            raise AssertionError(f"{label} link count disagrees")
    if (alias_metadata.st_dev, alias_metadata.st_ino) == (
        retained_metadata.st_dev,
        retained_metadata.st_ino,
    ):
        raise AssertionError("deployment alias and retained receipt share an inode")
