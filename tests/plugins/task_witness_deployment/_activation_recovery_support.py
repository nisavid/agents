from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

from ._activation_support import PreparedActivation

_RECOVERY_PROCESS_LOSS_EXIT = 91


def _process_loss() -> None:
    os._exit(_RECOVERY_PROCESS_LOSS_EXIT)


def _wait_for_process_loss(child: int, label: str) -> None:
    waited, status = os.waitpid(child, 0)
    if waited != child or not os.WIFEXITED(status):
        raise AssertionError(f"{label} did not exit normally")
    if os.WEXITSTATUS(status) != _RECOVERY_PROCESS_LOSS_EXIT:
        raise AssertionError(
            f"{label} exited at unexpected boundary {os.WEXITSTATUS(status)}"
        )


def run_activation_journal_process_loss_cut(
    deployment: ModuleType,
    request: object,
    phase: str,
) -> None:
    child = os.fork()
    if child == 0:
        original = deployment._write_activation_journal

        def observed(canonical_root_fd: int, journal: object) -> None:
            original(canonical_root_fd, journal)
            if journal.value["phase"] == phase:
                _process_loss()

        deployment._write_activation_journal = observed
        deployment._spawn_activation_smoke_child = lambda *args, **kwargs: os._exit(92)
        try:
            deployment.activate_staged(request)
        except (AssertionError, OSError, TypeError, deployment.DeploymentError):
            os._exit(93)
        os._exit(94)
    _wait_for_process_loss(child, f"activation {phase} journal cut")


def run_activation_journal_temporary_process_loss_cut(
    deployment: ModuleType,
    request: object,
) -> None:
    child = os.fork()
    if child == 0:
        original_write_all = deployment._write_all
        journal_writes = 0

        def observed_write_all(descriptor: int, raw: bytes, label: str) -> None:
            nonlocal journal_writes
            if label == "activation transaction journal":
                journal_writes += 1
                if journal_writes == 2:
                    original_write_all(
                        descriptor,
                        raw[: max(1, len(raw) // 2)],
                        label,
                    )
                    _process_loss()
            original_write_all(descriptor, raw, label)

        deployment._write_all = observed_write_all
        deployment._spawn_activation_smoke_child = lambda *args, **kwargs: os._exit(92)
        try:
            deployment.activate_staged(request)
        except (AssertionError, OSError, TypeError, deployment.DeploymentError):
            os._exit(93)
        os._exit(94)
    _wait_for_process_loss(child, "activation journal temporary cut")


def run_activation_smoke_process_loss_cut(
    deployment: ModuleType,
    request: object,
    *,
    cut: str,
    envelope_raw: bytes,
) -> None:
    if cut not in {
        "accepted-child-return",
        "accepted-journal",
        "candidate-accepted-journal",
        "terminal-journal",
        "rejected-child-return",
        "absence-journal",
    }:
        raise AssertionError(f"unknown activation smoke cut: {cut}")
    child = os.fork()
    if child == 0:
        original_run = deployment._run_activation_smoke
        original_write = deployment._write_activation_journal

        def smoke_child(
            *args: object, **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            del args, kwargs
            if cut in {"rejected-child-return", "absence-journal"}:
                return subprocess.CompletedProcess(
                    ("activation-smoke",),
                    1,
                    b"",
                    b"candidate rejected",
                )
            return subprocess.CompletedProcess(
                ("activation-smoke",),
                0,
                envelope_raw,
                b"",
            )

        def observed_run(*args: object, **kwargs: object):
            result = original_run(*args, **kwargs)
            if cut in {"accepted-child-return", "rejected-child-return"}:
                _process_loss()
            return result

        def observed_write(canonical_root_fd: int, journal: object) -> None:
            original_write(canonical_root_fd, journal)
            value = journal.value
            if (
                (
                    cut == "accepted-journal"
                    and value["phase"] == "candidate-smoke"
                    and value["candidate_smoke_acceptance"] is not None
                )
                or (
                    cut == "candidate-accepted-journal"
                    and value["phase"] == "candidate-accepted"
                )
                or (cut == "terminal-journal" and value["phase"] == "terminal")
                or (
                    cut == "absence-journal"
                    and value["phase"] == "absence-restoring"
                    and value["pending_step"] is None
                )
            ):
                _process_loss()

        deployment._spawn_activation_smoke_child = smoke_child
        deployment._run_activation_smoke = observed_run
        deployment._write_activation_journal = observed_write
        try:
            deployment.activate_staged(request)
        except (AssertionError, OSError, TypeError, deployment.DeploymentError):
            os._exit(95)
        os._exit(96)
    _wait_for_process_loss(child, f"activation smoke {cut} cut")


def run_activation_absence_process_loss_cut(
    deployment: ModuleType,
    prepared: PreparedActivation,
    request: object,
    *,
    step_index: int,
    cut: str,
) -> None:
    if cut not in {"before-mutation", "after-mutation", "parent-fsync"}:
        raise AssertionError(f"unknown activation absence cut: {cut}")
    artifacts = deployment._ordered_activation_artifacts(prepared.verified)
    removals = deployment._ordered_activation_removal_steps(artifacts)
    target = removals[step_index]
    child = os.fork()
    if child == 0:
        original_write = deployment._write_activation_journal
        original_unlink = deployment.os.unlink
        original_rmdir = deployment.os.rmdir
        original_fsync = deployment.os.fsync
        state: dict[str, Any] = {
            "armed": False,
            "mutated": False,
            "parent": None,
        }

        def rejected_child(
            *args: object,
            **kwargs: object,
        ) -> subprocess.CompletedProcess[bytes]:
            del args, kwargs
            return subprocess.CompletedProcess(
                ("activation-smoke",),
                1,
                b"",
                b"candidate rejected",
            )

        def observed_write(canonical_root_fd: int, journal: object) -> None:
            original_write(canonical_root_fd, journal)
            pending = journal.value["pending_step"]
            if (
                journal.value["phase"] == "absence-restoring"
                and pending is not None
                and pending["index"] == step_index
            ):
                state["armed"] = True
                if cut == "before-mutation":
                    _process_loss()

        def matches_target(operation: str) -> bool:
            return bool(state["armed"]) and target.operation == operation

        def observed_unlink(*args: object, **kwargs: object) -> None:
            if matches_target("remove-artifact"):
                parent_fd = kwargs.get("dir_fd")
                if not isinstance(parent_fd, int):
                    os._exit(97)
                metadata = os.fstat(parent_fd)
                state["parent"] = (metadata.st_dev, metadata.st_ino)
            original_unlink(*args, **kwargs)
            if matches_target("remove-artifact"):
                state["mutated"] = True
                if cut == "after-mutation":
                    _process_loss()

        def observed_rmdir(*args: object, **kwargs: object) -> None:
            if matches_target("remove-directory"):
                parent_fd = kwargs.get("dir_fd")
                if not isinstance(parent_fd, int):
                    os._exit(98)
                metadata = os.fstat(parent_fd)
                state["parent"] = (metadata.st_dev, metadata.st_ino)
            original_rmdir(*args, **kwargs)
            if matches_target("remove-directory"):
                state["mutated"] = True
                if cut == "after-mutation":
                    _process_loss()

        def observed_fsync(descriptor: int) -> None:
            original_fsync(descriptor)
            if state["armed"] and state["mutated"] and cut == "parent-fsync":
                metadata = os.fstat(descriptor)
                if (metadata.st_dev, metadata.st_ino) == state["parent"]:
                    _process_loss()

        deployment._spawn_activation_smoke_child = rejected_child
        deployment._write_activation_journal = observed_write
        deployment.os.unlink = observed_unlink
        deployment.os.rmdir = observed_rmdir
        deployment.os.fsync = observed_fsync
        try:
            deployment.activate_staged(request)
        except (AssertionError, OSError, TypeError, deployment.DeploymentError):
            os._exit(99)
        os._exit(100)
    _wait_for_process_loss(
        child,
        f"activation absence step {step_index} {cut} cut",
    )


def install_temporary_paths(root: Path) -> tuple[Path, ...]:
    return tuple(
        path
        for path in root.rglob("*")
        if path.name.startswith(".task-witness-install-") and path.name.endswith(".tmp")
    )
