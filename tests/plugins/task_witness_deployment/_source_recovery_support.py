from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from tests.plugins.task_witness_client._support import (
    INVOCATION_PROFILE_DRIVER_SOURCE,
    LAUNCHER_MODULE_DRIVER_SOURCE,
    write_configured_driver,
)

from ._activation_support import canonical_value
from ._control_maintenance_activation_support import (
    PreparedControlMaintenanceActivation,
)
from ._source_transition_support import SourceTransitionFixture


@dataclass(frozen=True)
class StagedSourceRecovery:
    initial: object
    active: object
    candidate: Path
    request: object
    prepared: object
    authorization_raw: bytes
    staged: object
    activation: object


class SourceRecoveryFixture(SourceTransitionFixture):
    """Build public source-boundary transactions at real recovery cuts."""

    def staged_publisher_downgrade(self) -> StagedSourceRecovery:
        initial, active, _, _ = self.activate_initial(
            "publisher_channel",
            sequence=7,
        )
        candidate = self.candidate(
            "publisher_channel",
            "publisher-recovery-downgrade",
            "1.0.1",
        )
        request = self.request(
            candidate_root=candidate,
            canonical_root=self.first.canonical_root,
            active_receipt_sha256=active.active_receipt_sha256,
            mode="publisher_channel",
            revision="b" * 40,
            sequence=6,
        )
        return self._stage_source_boundary(initial, active, candidate, request)

    def staged_cross_mode_control_change(
        self,
        recovery_load_marker: Path,
    ) -> PreparedControlMaintenanceActivation:
        prior_controller = (
            self.first.publisher_candidate_root
            / "controller"
            / "task_witness_deploy.py"
        )
        marker_literal = json.dumps(str(recovery_load_marker))
        prior_controller.write_bytes(
            prior_controller.read_bytes()
            + (
                "\nif __name__.startswith('_task_witness_control_recovery_'):\n"
                f"    Path({marker_literal}).write_text(__name__, encoding='utf-8')\n"
            ).encode()
        )
        initial, active, _, _ = self.activate_initial(
            "publisher_channel",
            sequence=7,
        )
        candidate = self.candidate(
            "exact_release",
            "cross-mode-recovery-control",
            "1.0.1",
        )
        candidate_controller = candidate / "controller" / "task_witness_deploy.py"
        candidate_controller.write_bytes(
            candidate_controller.read_bytes()
            + b"\n# source-boundary recovery candidate controller\n"
        )
        request = self.request(
            candidate_root=candidate,
            canonical_root=self.first.canonical_root,
            active_receipt_sha256=active.active_receipt_sha256,
            mode="exact_release",
            revision="b" * 40,
        )
        staged = self._stage_source_boundary(initial, active, candidate, request)
        return PreparedControlMaintenanceActivation(
            staged.initial,
            staged.active,
            staged.candidate,
            staged.request,
            staged.prepared,
            staged.authorization_raw,
            staged.staged,
            staged.activation,
        )

    def _stage_source_boundary(
        self,
        initial: object,
        active: object,
        candidate: Path,
        request: object,
    ) -> StagedSourceRecovery:
        prepared = self.deployment.prepare_deployment(request)
        authorization_raw = self.authorization_raw(
            prepared,
            "source-boundary-change",
        )
        staged = self.deployment.stage_deployment(
            request,
            authorization_raw,
            self.root / f"source-recovery-stage-{candidate.name}",
        )
        activation = self.deployment.ActivationRequest(
            deployment=request,
            authorization_raw=authorization_raw,
            stage_receipt=staged.stage_path,
        )
        return StagedSourceRecovery(
            initial,
            active,
            candidate,
            request,
            prepared,
            authorization_raw,
            staged,
            activation,
        )


class InstalledRecoveryClientProcess:
    """Run each recovery smoke through the real installed client and launcher."""

    def __init__(
        self,
        deployment: object,
        canonical_root: Path,
        support_root: Path,
    ) -> None:
        support_root.mkdir(mode=0o700)
        launcher_driver = support_root / "launcher_module_driver.py"
        shutil.copyfile(LAUNCHER_MODULE_DRIVER_SOURCE, launcher_driver)
        self.client_driver = support_root / "configured_client_driver.py"
        write_configured_driver(
            self.client_driver,
            INVOCATION_PROFILE_DRIVER_SOURCE,
            {
                "scenario": "composed-client",
                "launcher_driver": str(launcher_driver),
            },
        )
        self.canonical_root = canonical_root
        self.original_popen = deployment.subprocess.Popen
        self.original_read = deployment.os.read
        self.phases: list[str] = []
        self.streams: dict[int, str] = {}
        self.output = {"stdout": bytearray(), "stderr": bytearray()}

    def __call__(
        self,
        argv: tuple[str, ...],
        *args: object,
        **kwargs: object,
    ) -> subprocess.Popen[bytes]:
        expected = (str(self.canonical_root / "task-witness"), "activation-smoke")
        if args:
            raise AssertionError("recovery client Popen positional options disagree")
        if argv != expected:
            raise AssertionError("recovery client shim argv disagrees")
        if kwargs.get("pass_fds") != (3,):
            raise AssertionError("recovery client descriptor set disagrees")
        journal = canonical_value(
            (self.canonical_root / "transaction.json").read_bytes()
        )
        self.phases.append(journal["phase"])
        client = self.canonical_root / "client" / "task_witness_client.py"
        process = self.original_popen(
            (
                str(Path(sys.executable).resolve(strict=True)),
                "-B",
                "-I",
                "-S",
                "-X",
                "disable-remote-debug",
                str(self.client_driver),
                str(client),
                str(self.canonical_root),
                "activation-smoke",
            ),
            **kwargs,
        )
        if process.stdout is None or process.stderr is None:
            raise AssertionError("recovery client output pipes are unavailable")
        self.streams[process.stdout.fileno()] = "stdout"
        self.streams[process.stderr.fileno()] = "stderr"
        return process

    def observe_read(self, descriptor: int, length: int) -> bytes:
        chunk = self.original_read(descriptor, length)
        stream = self.streams.get(descriptor)
        if stream is not None:
            if chunk:
                self.output[stream].extend(chunk)
            else:
                self.streams.pop(descriptor, None)
        return chunk
