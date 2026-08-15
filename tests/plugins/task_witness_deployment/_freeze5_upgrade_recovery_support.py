from __future__ import annotations

import dataclasses
import importlib.util
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from ._activation_support import (
    PreparedActivation,
    SmokeChildBoundary,
    expected_smoke_envelope,
)
from ._routine_support import (
    RoutineDeploymentFixture,
    _freeze5_source_raw,
    materialize_freeze5_plugin,
)
from ._support import (
    PLUGIN,
    canonical_document,
    content_document,
    sha256,
)

FREEZE5_COMMIT = "96608a9b91d4dcf3f468a4fab1f0e008c9c32b36"
FREEZE5_CONTROLLER_SHA256 = (
    "8dc51b2a644e30d1f7c4f3b71711698b4130b43f1517e9f5361c6d1a0f7d6cfe"
)
FREEZE5_POLICY_SHA256 = (
    "23e84f210ba69ef79e02bfc3039b2c8be3b91153d7649009b3a22850f5086245"
)
FREEZE5_CLIENT_SHA256 = (
    "778186f6a460655a8b390c831e05c233171236898663ad4155bd45695597c6cf"
)
CLIENT_GENERATION_ASSIGNMENT = re.compile(
    rb'(?m)^CLIENT_SOURCE_GENERATION_SHA256 = "([0-9a-f]{64})"$',
)
CLIENT_PROFILE_ASSIGNMENT = re.compile(
    rb'(?m)^CLIENT_RELEASE_PROFILE = "([a-z0-9-]+)"$',
)


@dataclass(frozen=True)
class PreparedFreeze5DirectAttempt:
    initial: object
    active: object
    prior_candidate: Path
    candidate: Path
    request: object


@dataclass(frozen=True)
class PreparedFreeze5BridgeActivation:
    deployment: ModuleType
    initial: PreparedActivation
    active: object
    candidate: Path
    current_request: object
    request: object
    prepared: object
    authorization_raw: bytes
    staged: object
    activation: object


def _freeze5_file_raw(relative_path: str) -> bytes:
    return _freeze5_source_raw(relative_path)


def set_resealed_client_profile(path: Path, profile: str) -> None:
    raw = path.read_bytes()
    profile_matches = list(CLIENT_PROFILE_ASSIGNMENT.finditer(raw))
    if len(profile_matches) != 1 or re.fullmatch(r"[a-z0-9-]+", profile) is None:
        raise AssertionError("client release profile assignment is not exact")
    profile_start, profile_end = profile_matches[0].span(1)
    raw = raw[:profile_start] + profile.encode("ascii") + raw[profile_end:]
    matches = list(CLIENT_GENERATION_ASSIGNMENT.finditer(raw))
    if len(matches) != 1:
        raise AssertionError("bridge client generation assignment is not exact")
    digest_start, digest_end = matches[0].span(1)
    normalized = raw[:digest_start] + (b"0" * 64) + raw[digest_end:]
    raw = raw[:digest_start] + sha256(normalized).encode("ascii") + raw[digest_end:]
    path.write_bytes(raw)
    path.chmod(0o755)


def materialize_bridge_shape_candidate(destination: Path) -> None:
    """Build the unfrozen B1 shape without asserting a future bridge digest."""

    if not destination.exists():
        shutil.copytree(PLUGIN, destination)
    bindings = (("controller/policy.json", FREEZE5_POLICY_SHA256, 0o644),)
    for relative, expected_sha256, mode in bindings:
        target = destination / relative
        target.write_bytes(_freeze5_file_raw(relative))
        target.chmod(mode)
        if sha256(target.read_bytes()) != expected_sha256:
            raise AssertionError(f"Freeze 5 {relative} digest disagrees")
    client = destination / "client" / "task_witness_client.py"
    set_resealed_client_profile(client, "b1-transition")
    raw = client.read_bytes()
    if sha256(raw) in {
        FREEZE5_CLIENT_SHA256,
        sha256((PLUGIN / "client" / "task_witness_client.py").read_bytes()),
    }:
        raise AssertionError("bridge transition client is not distinct")


def load_controller(path: Path, name: str) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise AssertionError("controller module cannot be loaded")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    try:
        specification.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def freeze5_deployment_request_fields(module: ModuleType) -> tuple[str, ...]:
    return tuple(field.name for field in dataclasses.fields(module.DeploymentRequest))


class Freeze5UpgradeRecoveryFixture:
    """Build one real harness-snapshot Freeze 5 -> current control upgrade."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.routine = RoutineDeploymentFixture(root)
        self.deployment = self.routine.deployment()

    def direct_current_upgrade_request(self) -> PreparedFreeze5DirectAttempt:
        """Build an exact Freeze 5 active root and one direct TW4 request."""

        prior_candidate = self.root / "freeze5-candidate"
        materialize_freeze5_plugin(prior_candidate)
        initial, active = self._activate_freeze5(prior_candidate)
        candidate = self.routine.candidate_root()
        request = self.routine.request_for_candidate(
            initial.canonical_root,
            active.active_receipt_sha256,
            candidate,
            release_version="1.0.0",
            revision="b" * 40,
            sequence=8,
        )
        return PreparedFreeze5DirectAttempt(
            initial,
            active,
            prior_candidate,
            candidate,
            request,
        )

    def prepared_first_hop(
        self,
        *,
        candidate_mutator: Callable[[Path], None] | None = None,
    ) -> PreparedFreeze5BridgeActivation:
        """Prepare and stage the B1 shape entirely through exact staged F5."""

        prior_candidate = self.root / "freeze5-candidate"
        materialize_freeze5_plugin(prior_candidate)
        initial, active = self._activate_freeze5(prior_candidate)
        candidate = self.routine.candidate_root(legacy_manifest=True)
        materialize_bridge_shape_candidate(candidate)
        if candidate_mutator is not None:
            candidate_mutator(candidate)
        current_request = self.routine.request_for_candidate(
            initial.canonical_root,
            active.active_receipt_sha256,
            candidate,
            release_version="0.1.1",
            revision="b" * 40,
            sequence=8,
        )
        deployment = load_controller(
            prior_candidate / "controller" / "task_witness_deploy.py",
            "_task_witness_freeze5_first_hop",
        )
        try:
            evidence = current_request.source_evidence
            request = deployment.DeploymentRequest(
                candidate_root=deployment.Path(str(candidate)),
                canonical_root=deployment.Path(str(initial.canonical_root)),
                source_selection_raw=bytes(current_request.source_selection_raw),
                manager_binding_raw=bytes(evidence.binding_raw),
                manager_receipt_raw=bytes(evidence.receipt_raw),
                runtime_qualification_raw=bytes(
                    current_request.runtime_qualification_raw
                ),
                maintenance_transaction_sha256=(
                    current_request.maintenance_transaction_sha256
                ),
                expected_active_receipt_sha256=(
                    current_request.expected_active_receipt_sha256
                ),
            )
            prepared = deployment.prepare_deployment(request)
            facts = prepared.authorization_facts
            authorization_raw = canonical_document(
                content_document(
                    {
                        "schema_version": 1,
                        "contract": "task-witness-deployer-authorization-v1",
                        "purpose": "complete-control-set-maintenance",
                        "canonical_root": str(facts.canonical_root),
                        "effective_uid": facts.effective_uid,
                        "plan_sha256": facts.plan_sha256,
                        "maintenance_transaction_sha256": (
                            facts.maintenance_transaction_sha256
                        ),
                        "candidate_controller_sha256": (
                            facts.candidate_controller_sha256
                        ),
                        "candidate_policy_sha256": facts.candidate_policy_sha256,
                        "source_selection_sha256": facts.source_selection_sha256,
                        "manager_receipt_sha256": facts.manager_receipt_sha256,
                        "expected_active_receipt_sha256": (
                            facts.expected_active_receipt_sha256
                        ),
                    }
                )
            )
            staged = deployment.stage_deployment(
                request,
                authorization_raw,
                deployment.Path(str(self.root / "freeze5-bridge-stage")),
            )
            activation = deployment.ActivationRequest(
                deployment=request,
                authorization_raw=authorization_raw,
                stage_receipt=staged.stage_path,
            )
            return PreparedFreeze5BridgeActivation(
                deployment,
                initial,
                active,
                candidate,
                current_request,
                request,
                prepared,
                authorization_raw,
                staged,
                activation,
            )
        except BaseException:
            remove_loaded_controller(deployment)
            raise

    def _activate_freeze5(
        self,
        candidate: Path,
    ) -> tuple[PreparedActivation, object]:
        controller = candidate / "controller" / "task_witness_deploy.py"
        prior = load_controller(controller, "_task_witness_freeze5_first_install")
        try:
            account_home = self.routine.first_install.root / "account-home"
            canonical_root = account_home / ".local" / "libexec" / "task-witness"
            canonical_root.mkdir(parents=True, mode=0o700)
            for directory in (
                account_home,
                account_home / ".local",
                account_home / ".local" / "libexec",
                canonical_root,
            ):
                directory.chmod(0o700)
            activation_lock = canonical_root / "activation.lock"
            activation_lock.write_bytes(b"")
            activation_lock.chmod(0o600)

            selection_raw, binding_raw, receipt_raw = (
                self.routine.first_install.task_witness_candidate_inputs(
                    candidate,
                    revision=FREEZE5_COMMIT,
                )
            )
            request = prior.FirstInstallRequest(
                candidate_root=prior.Path(str(candidate)),
                canonical_root=prior.Path(str(canonical_root)),
                source_selection_raw=bytes(selection_raw),
                manager_binding_raw=bytes(binding_raw),
                manager_receipt_raw=bytes(receipt_raw),
                runtime_qualification_raw=bytes(
                    self.routine.first_install.runtime_qualification_raw()
                ),
                maintenance_transaction_sha256="9" * 64,
            )
            first = prior.prepare_first_install(request)
            facts = first.authorization_facts
            authorization_raw = canonical_document(
                content_document(
                    {
                        "schema_version": 1,
                        "contract": "task-witness-deployer-authorization-v1",
                        "purpose": "first-install",
                        "canonical_root": str(facts.canonical_root),
                        "effective_uid": facts.effective_uid,
                        "plan_sha256": facts.plan_sha256,
                        "maintenance_transaction_sha256": (
                            facts.maintenance_transaction_sha256
                        ),
                        "candidate_controller_sha256": (
                            facts.candidate_controller_sha256
                        ),
                        "candidate_policy_sha256": facts.candidate_policy_sha256,
                        "source_selection_sha256": facts.source_selection_sha256,
                        "manager_receipt_sha256": facts.manager_receipt_sha256,
                    }
                )
            )
            staged = prior.stage_first_install(
                request,
                authorization_raw,
                prior.Path(str(self.routine.first_install.root / "stage")),
            )
            prepared = PreparedActivation(
                request=request,
                authorization_raw=authorization_raw,
                staged=staged,
                verified=prior.verify_deployment_stage(staged.stage_path),
                canonical_root=canonical_root,
                activation_lock=activation_lock,
            )
            activation = prior.ActivationRequest(
                deployment=request,
                authorization_raw=authorization_raw,
                stage_receipt=staged.stage_path,
            )
            smoke = SmokeChildBoundary(
                prepared,
                expected_smoke_envelope(staged),
            )
            original_smoke = prior._spawn_activation_smoke_child
            prior._spawn_activation_smoke_child = smoke
            try:
                active = prior.activate_staged(activation)
            finally:
                prior._spawn_activation_smoke_child = original_smoke
            return prepared, active
        finally:
            remove_loaded_controller(prior)


def detach_candidate(candidate: Path) -> Path:
    detached = candidate.with_name(f"{candidate.name}-detached")
    candidate.rename(detached)
    return detached


def remove_loaded_controller(module: ModuleType) -> None:
    sys.modules.pop(module.__name__, None)
    shutil.rmtree(Path(module.__file__).parent / "__pycache__", ignore_errors=True)
