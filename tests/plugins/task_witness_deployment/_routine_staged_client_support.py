from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from tests.plugins.task_witness_client._support import (
    INVOCATION_PROFILE_DRIVER_SOURCE,
    LAUNCHER_MODULE_DRIVER_SOURCE,
    write_configured_driver,
)

from ._activation_support import canonical_value
from ._routine_activation_support import SmokeObservation, receipt_digest_inventory


@dataclass(frozen=True)
class InstalledClientSmokeCall:
    observation: SmokeObservation
    completed: subprocess.CompletedProcess[bytes]
    filesystem_mutations: tuple[str, ...]


class InstalledClientSmokeObserver:
    def __init__(self) -> None:
        self.calls: list[InstalledClientSmokeCall] = []

    @property
    def phases(self) -> list[str]:
        return [call.observation.phase for call in self.calls]


@contextmanager
def installed_client_smoke_process(
    deployment: object,
    canonical_root: Path,
    support_root: Path,
) -> Iterator[InstalledClientSmokeObserver]:
    """Virtualize only passwd-root discovery below the real smoke supervisor."""

    support_root.mkdir(mode=0o700)
    launcher_driver = support_root / "launcher_module_driver.py"
    shutil.copyfile(LAUNCHER_MODULE_DRIVER_SOURCE, launcher_driver)
    client_driver = support_root / "configured_client_driver.py"
    mutation_audit = support_root / "filesystem-mutation-audit.log"
    mutation_audit.write_bytes(b"")
    mutation_audit.chmod(0o600)
    write_configured_driver(
        client_driver,
        INVOCATION_PROFILE_DRIVER_SOURCE,
        {
            "scenario": "composed-client",
            "launcher_driver": str(launcher_driver),
            "filesystem_mutation_audit": str(mutation_audit),
        },
    )
    original_spawn = deployment._spawn_activation_smoke_process
    original_run = deployment._run_activation_smoke
    expected_argv = (str(canonical_root / "task-witness"), "activation-smoke")
    observer = InstalledClientSmokeObserver()

    def spawn_with_virtualized_root(
        argv: tuple[str, ...],
        pass_fds: tuple[int, ...],
    ):
        if argv != expected_argv:
            raise AssertionError("activation smoke process argv disagrees")
        if pass_fds != (3,):
            raise AssertionError("activation smoke process descriptor set disagrees")
        return original_spawn(
            (
                str(Path(sys.executable).resolve(strict=True)),
                "-B",
                "-I",
                "-S",
                "-X",
                "disable-remote-debug",
                str(client_driver),
                str(canonical_root / "client" / "task_witness_client.py"),
                str(canonical_root),
                "activation-smoke",
            ),
            pass_fds,
        )

    def observe_real_smoke(
        root: Path,
        activation_lock_fd: int,
    ) -> subprocess.CompletedProcess[bytes]:
        if root != canonical_root:
            raise AssertionError("activation smoke root disagrees")
        journal_raw = (root / "transaction.json").read_bytes()
        journal = canonical_value(journal_raw)
        observation = SmokeObservation(
            phase=journal["phase"],
            journal_raw=journal_raw,
            journal=journal,
            active_raw=(root / "active.json").read_bytes(),
            deployment_raw=(root / "deployment.json").read_bytes(),
            receipt_digests=receipt_digest_inventory(root),
        )
        audit_offset = mutation_audit.stat().st_size
        completed = original_run(root, activation_lock_fd)
        audit_lines = mutation_audit.read_bytes()[audit_offset:].decode().splitlines()
        audit_records = tuple(
            (line, line.split("\t", 1)[0], ast.literal_eval(line.split("\t", 1)[1]))
            for line in audit_lines
        )
        mutation_probe = str(root / ".test-filesystem-mutation-audit-probe")
        probe_lines = tuple(
            line
            for line, event, arguments in audit_records
            if event in {"open", "os.remove"}
            and arguments
            and arguments[0] == mutation_probe
        )
        if {line.split("\t", 1)[0] for line in probe_lines} != {
            "open",
            "os.remove",
        }:
            raise AssertionError("child filesystem mutation audit was not exercised")
        filesystem_mutations = tuple(
            line
            for line, event, arguments in audit_records
            if not (
                event in {"open", "os.remove"}
                and arguments
                and arguments[0] == mutation_probe
            )
            and not (
                event == "open"
                and len(arguments) == 3
                and arguments[0] == "activation.lock"
                and isinstance(arguments[2], int)
                and arguments[2] & os.O_ACCMODE == os.O_RDWR
                and not arguments[2] & (os.O_APPEND | os.O_CREAT | os.O_TRUNC)
            )
        )
        observer.calls.append(
            InstalledClientSmokeCall(
                observation=observation,
                completed=completed,
                filesystem_mutations=filesystem_mutations,
            )
        )
        return completed

    with (
        mock.patch.object(
            deployment,
            "_spawn_activation_smoke_process",
            spawn_with_virtualized_root,
        ),
        mock.patch.object(
            deployment,
            "_run_activation_smoke",
            observe_real_smoke,
        ),
    ):
        yield observer
