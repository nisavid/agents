from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from unittest import mock

from ._activation_support import (
    filesystem_identity,
    process_descriptor_inventory,
)
from ._control_maintenance_activation_support import (
    ControlMaintenanceActivationFixture,
    ControlMaintenanceRecoveryPopenAdapter,
    PreparedControlMaintenanceActivation,
    assert_candidate_control_set_installed,
)
from ._control_maintenance_support import ControlMaintenanceFixture
from ._routine_activation_support import (
    assert_no_transaction_residue,
    selector_raws,
    staged_artifact,
    staged_candidate_selector_raws,
)
from ._support import sha256

_CANDIDATE_CONTROL_PATHS = {
    "controller": Path("controller/task_witness_deploy.py"),
    "policy": Path("controller/policy.json"),
    "launcher": Path("launcher/task_witness_launch.py"),
    "client": Path("client/task_witness_client.py"),
    "shim": Path("client/task_witness_shim.sh.in"),
}


@dataclass(frozen=True)
class ActivatedControlMaintenanceB:
    fixture: ControlMaintenanceActivationFixture
    prepared: PreparedControlMaintenanceActivation
    result: object
    deployment: ModuleType
    module_name: str
    stage_snapshot: dict[str, tuple[object, ...]]
    activation_lock_identity: tuple[int, ...]
    descriptor_inventory: tuple[tuple[int, tuple[int, ...], int], ...]
    smoke_markers: tuple[str, ...]

    @property
    def canonical_root(self) -> Path:
        return self.prepared.initial.canonical_root

    @property
    def active_receipt_sha256(self) -> str:
        digest = self.result.active_receipt_sha256
        if not isinstance(digest, str):
            raise TypeError("activated B receipt digest is unavailable")
        return digest

    def close(self) -> None:
        sys.modules.pop(self.module_name, None)

    def assert_clean_active_b(self, test: object) -> None:
        staged = self.prepared.staged
        root = self.canonical_root
        test.assertEqual(self.result.outcome, "candidate-active")
        test.assertEqual(
            self.result.active_receipt_sha256,
            sha256(staged.deployment_raw),
        )
        test.assertEqual(self.smoke_markers, ("B",))
        test.assertEqual(selector_raws(root), staged_candidate_selector_raws(staged))
        assert_candidate_control_set_installed(staged)
        assert_no_transaction_residue(root)
        test.assertEqual(
            self.fixture.control.routine.stage_snapshot(staged.stage_path),
            self.stage_snapshot,
        )
        self.fixture.control.assert_private_stage(test, staged.stage_path.parent)
        test.assertEqual(
            filesystem_identity(self.prepared.initial.activation_lock),
            self.activation_lock_identity,
        )
        test.assertEqual(
            process_descriptor_inventory(),
            self.descriptor_inventory,
        )
        controller = staged_artifact(staged, "controller")
        test.assertEqual(
            Path(controller.installed["path"]).read_bytes(), controller.raw
        )
        test.assertEqual(
            Path(self.deployment.__file__).resolve(),
            Path(controller.installed["path"]).resolve(),
        )

    def routine_candidate_and_request(self) -> tuple[Path, object]:
        candidate = self.fixture.control.routine.next_candidate(
            self.prepared.candidate,
            "candidate-c-routine",
            "1.0.2",
        )
        self.assert_candidate_controls(candidate, changed=frozenset())
        return candidate, self._request(candidate)

    def control_candidate_and_request(self) -> tuple[Path, object]:
        candidate = self.fixture.control.routine.next_candidate(
            self.prepared.candidate,
            "candidate-c-control",
            "1.0.2",
        )
        controller = candidate / _CANDIDATE_CONTROL_PATHS["controller"]
        controller.write_bytes(
            controller.read_bytes()
            + b"\n# follow-up complete-control-set maintenance candidate C\n"
        )

        def change_policy(policy: dict[str, object]) -> None:
            policy["providers"] = [
                {
                    "plugin_id": "followup-maintenance-provider",
                    "authority_profile": "followup-maintenance-authority",
                    "producers": [],
                    "issuers": [],
                    "validators": [],
                }
            ]

        ControlMaintenanceFixture._rewrite_policy(candidate, change_policy)
        self.assert_candidate_controls(
            candidate,
            changed=frozenset({"controller", "policy"}),
        )
        return candidate, self._request(candidate)

    def assert_candidate_controls(
        self,
        candidate: Path,
        *,
        changed: frozenset[str],
    ) -> None:
        for role, relative_path in _CANDIDATE_CONTROL_PATHS.items():
            expected = (
                (self.prepared.candidate / relative_path).read_bytes()
                if role == "shim"
                else staged_artifact(self.prepared.staged, role).raw
            )
            actual = (candidate / relative_path).read_bytes()
            if (actual != expected) != (role in changed):
                raise AssertionError(f"follow-up candidate {role} bytes disagree")

    def routine_authorization_raw(self, prepared: object) -> bytes:
        return self.fixture.control.routine.authorization_raw(prepared)

    def control_authorization_raw(self, prepared: object) -> bytes:
        return ControlMaintenanceFixture.authorization_raw(prepared)

    def _request(self, candidate: Path) -> object:
        request = self.fixture.control.routine.request_for_candidate(
            self.canonical_root,
            self.active_receipt_sha256,
            candidate,
            release_version="1.0.2",
            revision="c" * 40,
            sequence=9,
        )
        evidence = request.source_evidence
        if hasattr(evidence, "receipt_raw"):
            exact_evidence = self.deployment.HarnessSnapshotEvidence(
                binding_raw=bytes(evidence.binding_raw),
                receipt_raw=bytes(evidence.receipt_raw),
            )
        elif hasattr(evidence, "publisher_record_raw"):
            exact_evidence = self.deployment.PublisherChannelEvidence(
                binding_raw=bytes(evidence.binding_raw),
                publisher_record_raw=bytes(evidence.publisher_record_raw),
            )
        else:
            exact_evidence = self.deployment.ExactReleaseEvidence()
        exact = self.deployment.DeploymentRequest(
            candidate_root=request.candidate_root,
            canonical_root=request.canonical_root,
            source_selection_raw=request.source_selection_raw,
            source_evidence=exact_evidence,
            runtime_qualification_raw=request.runtime_qualification_raw,
            maintenance_transaction_sha256=request.maintenance_transaction_sha256,
            expected_active_receipt_sha256=(request.expected_active_receipt_sha256),
        )
        if type(exact) is not self.deployment.DeploymentRequest:
            raise AssertionError("follow-up request does not use installed B types")
        return exact


def activate_control_maintenance_b(root: Path) -> ActivatedControlMaintenanceB:
    fixture = ControlMaintenanceActivationFixture(root)
    deployment_a = fixture.deployment()
    prepared = fixture.staged_activation()
    staged = prepared.staged
    stage_snapshot = fixture.control.routine.stage_snapshot(staged.stage_path)
    activation_lock_identity = filesystem_identity(prepared.initial.activation_lock)
    descriptor_inventory = process_descriptor_inventory()
    adapter = ControlMaintenanceRecoveryPopenAdapter(
        prepared,
        deployment_a.subprocess.Popen,
        candidate_accepted=True,
    )
    with mock.patch.object(
        deployment_a.subprocess,
        "Popen",
        side_effect=adapter,
    ):
        result = deployment_a.activate_staged(prepared.activation)

    controller = staged_artifact(staged, "controller")
    installed_controller = Path(controller.installed["path"])
    module_name = (
        "task_witness_deploy_followup_b_"
        + sha256(str(installed_controller).encode("utf-8"))[:16]
    )
    deployment_b = _load_exact_module(installed_controller, module_name)
    return ActivatedControlMaintenanceB(
        fixture=fixture,
        prepared=prepared,
        result=result,
        deployment=deployment_b,
        module_name=module_name,
        stage_snapshot=stage_snapshot,
        activation_lock_identity=activation_lock_identity,
        descriptor_inventory=descriptor_inventory,
        smoke_markers=tuple(adapter.markers),
    )


def _load_exact_module(path: Path, module_name: str) -> ModuleType:
    specification = importlib.util.spec_from_file_location(module_name, path)
    if specification is None or specification.loader is None:
        raise AssertionError("installed B controller cannot be loaded")
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        specification.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    finally:
        sys.dont_write_bytecode = previous
    for name in (
        "DeploymentRequest",
        "prepare_deployment",
        "stage_deployment",
        "verify_deployment_stage",
    ):
        if not hasattr(module, name):
            sys.modules.pop(module_name, None)
            raise AssertionError(f"installed B controller is missing {name}")
    return module
