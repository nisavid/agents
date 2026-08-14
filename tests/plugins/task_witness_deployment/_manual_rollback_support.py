from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from unittest import mock

from ._activation_support import (
    filesystem_identity,
    process_descriptor_inventory,
)
from ._control_maintenance_support import ControlMaintenanceFixture
from ._routine_activation_support import (
    PublicRoutineSmokeBoundary,
    assert_no_transaction_residue,
    expected_active_receipt_inventory,
    receipt_digest_inventory,
    selector_raws,
    staged_prior_selector_raws,
)
from ._routine_support import RoutineDeploymentFixture, smoke_envelope
from ._support import canonical_document, content_document, sha256

_MANUAL_TERMINAL_PROCESS_LOSS_EXIT = 109
_MANUAL_REPLACEMENT_PROCESS_LOSS_EXIT = 112
_MANUAL_SMOKE_PROCESS_LOSS_EXIT = 116
_MANUAL_CLEANUP_PROCESS_LOSS_EXIT = 119
_MANUAL_JOURNAL_PROCESS_LOSS_EXIT = 122
_MANUAL_EARLY_PROCESS_LOSS_EXIT = 126


@dataclass(frozen=True)
class ManualJournalCut:
    """Exact predecessor and successor bytes around one journal publication cut."""

    current_raw: bytes
    target_raw: bytes


def run_manual_early_process_loss(
    chain: ActivatedRoutineChain,
    request: object,
    authorization_raw: bytes,
    staging_root: Path,
    *,
    boundary: str,
) -> bytes:
    """Lose rollback at one exact early journal or additive publication boundary."""

    allowed = {
        "prepared",
        "frozen",
        "drained",
        "rollback-receipt",
        "deployment-receipt",
    }
    if boundary not in allowed:
        raise AssertionError("manual early process-loss boundary disagrees")
    child = os.fork()
    if child == 0:
        deployment = chain.deployment
        original_write = deployment._write_activation_journal
        original_link = deployment.os.link

        def observed_write(canonical_root_fd: int, journal: object) -> None:
            original_write(canonical_root_fd, journal)
            if boundary in {"prepared", "frozen", "drained"} and (
                journal.value["phase"] == boundary
            ):
                os._exit(_MANUAL_EARLY_PROCESS_LOSS_EXIT)

        def observed_link(*args: object, **kwargs: object) -> None:
            source = args[0] if args else kwargs.get("src")
            if not (
                isinstance(source, str) and source.startswith(".task-witness-install-")
            ):
                original_link(*args, **kwargs)
                return
            journal = json.loads(
                (chain.canonical_root / "transaction.json").read_bytes()
            )
            pending = journal["pending_step"]
            matches = (
                boundary in {"rollback-receipt", "deployment-receipt"}
                and journal["phase"] == "additive-installing"
                and pending is not None
                and pending["operation"] == "install"
                and pending["role"] == boundary
            )
            original_link(*args, **kwargs)
            if matches:
                os._exit(_MANUAL_EARLY_PROCESS_LOSS_EXIT)

        deployment._write_activation_journal = observed_write
        deployment.os.link = observed_link
        deployment._spawn_activation_smoke_child = lambda *args, **kwargs: os._exit(127)
        try:
            deployment.rollback_to(request, authorization_raw, staging_root)
        except Exception as error:  # noqa: BLE001 - child reports exact boundary
            os.write(2, f"manual early child: {error!r}\n".encode())
            os._exit(128)
        os._exit(129)
    waited, status = os.waitpid(child, 0)
    if waited != child or not os.WIFEXITED(status):
        raise AssertionError("manual early cut did not exit normally")
    if os.WEXITSTATUS(status) != _MANUAL_EARLY_PROCESS_LOSS_EXIT:
        raise AssertionError(
            f"manual early cut exited unexpectedly: {os.WEXITSTATUS(status)}"
        )
    return (chain.canonical_root / "transaction.json").read_bytes()


@dataclass(frozen=True)
class ActivatedRoutineChain:
    """One exact public A -> B -> C deployment history."""

    fixture: RoutineDeploymentFixture
    deployment: ModuleType
    initial: object
    active_a: object
    staged_b: object
    active_b: object
    staged_c: object
    active_c: object

    @property
    def canonical_root(self) -> Path:
        return self.initial.canonical_root

    @property
    def activation_lock(self) -> Path:
        return self.initial.activation_lock

    @property
    def receipt_a_raw(self) -> bytes:
        return self.staged_b.plan.precondition.receipt_raw

    @property
    def active_a_raw(self) -> bytes:
        return staged_prior_selector_raws(self.staged_b)[0]

    @property
    def receipt_c_raw(self) -> bytes:
        return self.staged_c.deployment_raw

    @property
    def receipt_b_raw(self) -> bytes:
        return self.staged_c.plan.precondition.receipt_raw

    @property
    def receipt_a_sha256(self) -> str:
        return sha256(self.receipt_a_raw)

    @property
    def receipt_c_sha256(self) -> str:
        return sha256(self.receipt_c_raw)

    @property
    def receipt_b_sha256(self) -> str:
        return sha256(self.receipt_b_raw)

    @property
    def stage_roots(self) -> tuple[Path, ...]:
        return (
            self.initial.staged.stage_path.parent,
            self.staged_b.stage_path.parent,
            self.staged_c.stage_path.parent,
        )


def activate_routine_a_b_c(
    root: Path,
    *,
    c_revision: str = "c" * 40,
) -> ActivatedRoutineChain:
    """Activate three releases through the production public interface."""

    fixture = RoutineDeploymentFixture(root)
    deployment = fixture.deployment()
    (
        initial,
        active_a,
        request_b,
        _,
        staged_b,
        activation_b,
    ) = fixture.staged_routine()
    canonical_root = initial.canonical_root
    selector_a = selector_raws(canonical_root)
    receipts_b = expected_active_receipt_inventory(selector_a[1], staged_b)
    smoke_b = PublicRoutineSmokeBoundary(
        canonical_root,
        staged_b,
        expected_receipt_digests=receipts_b,
        candidate_accepted=True,
        rollback_accepted=True,
    )
    with mock.patch.object(deployment, "_spawn_activation_smoke_child", smoke_b):
        active_b = deployment.activate_staged(activation_b)
    if active_b.outcome != "candidate-active":
        raise AssertionError("public B activation did not commit the candidate")

    candidate_c = fixture.next_candidate(
        request_b.candidate_root,
        "manual-rollback-candidate-c",
        "1.0.2",
    )
    request_c = fixture.request_for_candidate(
        canonical_root,
        active_b.active_receipt_sha256,
        candidate_c,
        release_version="1.0.2",
        revision=c_revision,
        sequence=9,
    )
    prepared_c = deployment.prepare_deployment(request_c)
    authorization_c = fixture.authorization_raw(prepared_c)
    staged_c = deployment.stage_deployment(
        request_c,
        authorization_c,
        root / "routine-stage-c",
    )
    activation_c = deployment.ActivationRequest(
        deployment=request_c,
        authorization_raw=authorization_c,
        stage_receipt=staged_c.stage_path,
    )
    receipts_c = receipts_b | frozenset(
        {sha256(staged_c.deployment_raw), sha256(staged_c.rollback_raw)}
    )
    smoke_c = PublicRoutineSmokeBoundary(
        canonical_root,
        staged_c,
        expected_receipt_digests=receipts_c,
        candidate_accepted=True,
        rollback_accepted=True,
    )
    with mock.patch.object(deployment, "_spawn_activation_smoke_child", smoke_c):
        active_c = deployment.activate_staged(activation_c)
    if active_c.outcome != "candidate-active":
        raise AssertionError("public C activation did not commit the candidate")
    if receipt_digest_inventory(canonical_root) != receipts_c:
        raise AssertionError("public A -> B -> C receipt history disagrees")
    assert_no_transaction_residue(canonical_root)
    return ActivatedRoutineChain(
        fixture=fixture,
        deployment=deployment,
        initial=initial,
        active_a=active_a,
        staged_b=staged_b,
        active_b=active_b,
        staged_c=staged_c,
        active_c=active_c,
    )


def activate_a_b_control_c(root: Path) -> ActivatedRoutineChain:
    """Activate routine B, then a C whose complete controls differ from A/B."""

    fixture = RoutineDeploymentFixture(root)
    deployment = fixture.deployment()
    initial, active_a, request_b, _, staged_b, activation_b = fixture.staged_routine()
    canonical_root = initial.canonical_root
    selector_a = selector_raws(canonical_root)
    receipts_b = expected_active_receipt_inventory(selector_a[1], staged_b)
    smoke_b = PublicRoutineSmokeBoundary(
        canonical_root,
        staged_b,
        expected_receipt_digests=receipts_b,
        candidate_accepted=True,
        rollback_accepted=True,
    )
    with mock.patch.object(deployment, "_spawn_activation_smoke_child", smoke_b):
        active_b = deployment.activate_staged(activation_b)

    candidate_c = fixture.next_candidate(
        request_b.candidate_root,
        "manual-control-candidate-c",
        "1.0.2",
    )
    controller = candidate_c / "controller" / "task_witness_deploy.py"
    controller.write_bytes(
        controller.read_bytes() + b"\n# manual recovery distinct controller C\n"
    )
    control = ControlMaintenanceFixture(root)
    control._rewrite_policy(candidate_c, control._declare_exact_control_surface)
    request_c = fixture.request_for_candidate(
        canonical_root,
        active_b.active_receipt_sha256,
        candidate_c,
        release_version="1.0.2",
        revision="c" * 40,
        sequence=9,
    )
    prepared_c = deployment.prepare_deployment(request_c)
    authorization_c = control.authorization_raw(prepared_c)
    staged_c = deployment.stage_deployment(
        request_c,
        authorization_c,
        root / "control-stage-c",
    )
    activation_c = deployment.ActivationRequest(
        deployment=request_c,
        authorization_raw=authorization_c,
        stage_receipt=staged_c.stage_path,
    )
    receipts_c = receipts_b | frozenset(
        {sha256(staged_c.deployment_raw), sha256(staged_c.rollback_raw)}
    )
    smoke_c = PublicManualRollbackSmokeBoundary(
        canonical_root,
        staged_c.plan.precondition.receipt_raw,
        staged_c.deployment_raw,
        candidate_accepted=True,
        rollback_accepted=True,
    )
    with mock.patch.object(deployment, "_spawn_activation_smoke_child", smoke_c):
        active_c = deployment.activate_staged(activation_c)
    if active_c.outcome != "candidate-active":
        raise AssertionError("public control C activation did not commit")
    if receipt_digest_inventory(canonical_root) != receipts_c:
        raise AssertionError("public A -> B -> control C receipt history disagrees")
    if (
        json.loads(staged_c.deployment_raw)["control_set"]["controller"]["sha256"]
        == json.loads(staged_b.plan.precondition.receipt_raw)["control_set"][
            "controller"
        ]["sha256"]
    ):
        raise AssertionError("manual recovery fixture controls are not distinct")
    assert_no_transaction_residue(canonical_root)
    return ActivatedRoutineChain(
        fixture=fixture,
        deployment=deployment,
        initial=initial,
        active_a=active_a,
        staged_b=staged_b,
        active_b=active_b,
        staged_c=staged_c,
        active_c=active_c,
    )


def endpoint_identity_from_receipt(raw: bytes) -> dict[str, object]:
    """Project the exact operator-readable identity fixed by the design."""

    receipt = json.loads(raw)
    source = receipt["source"]
    active = receipt["active"]
    return {
        "receipt_sha256": sha256(raw),
        "sequence": receipt["sequence"],
        "source": {
            "mode": source["mode"],
            "plugin_id": source["plugin_id"],
            "publisher_id": source["publisher_id"],
            "repository_id": source["repository_id"],
            "repository_url": source["repository_url"],
            "release_version": source["release_version"],
            "revision": source["revision"],
            "subtree_sha256": source["subtree_sha256"],
            "source_authority": source["source_authority"],
            "details": source["details"],
            "source_evidence_sha256": source["source_evidence"][
                "source_evidence_sha256"
            ],
        },
        "active": {
            "generation": active["generation"],
            "runtime_implementation_sha256": active["runtime_implementation_sha256"],
            "public_release": active["public_release"],
        },
        "control_set": {
            role: binding["sha256"]
            for role, binding in sorted(receipt["control_set"].items())
        },
        "compatibility_policy_sha256": receipt["compatibility_policy"]["sha256"],
        "trust_context_sha256": receipt["trust_context"]["sha256"],
        "content_sha256": receipt["content_sha256"],
    }


def endpoint_identity_observation(endpoint: object) -> dict[str, object]:
    return {
        "receipt_sha256": endpoint.receipt_sha256,
        "sequence": endpoint.sequence,
        "source": dict(endpoint.source),
        "active": dict(endpoint.active),
        "control_set": dict(endpoint.control_set),
        "compatibility_policy_sha256": endpoint.compatibility_policy_sha256,
        "trust_context_sha256": endpoint.trust_context_sha256,
        "content_sha256": endpoint.content_sha256,
    }


def exact_tree_state(
    root: Path,
) -> tuple[tuple[str, str, tuple[int, ...], object], ...]:
    """Capture exact path inventory, ownership, modes, and file contents."""

    if not root.exists():
        return ()
    state: list[tuple[str, str, tuple[int, ...], object]] = []
    for entry in (root, *sorted(root.rglob("*"))):
        metadata = entry.lstat()
        relative = "." if entry == root else entry.relative_to(root).as_posix()
        if entry.is_symlink():
            kind = "symlink"
            payload: object = str(entry.readlink())
        elif entry.is_dir():
            kind = "directory"
            payload = None
        else:
            if not stat.S_ISREG(metadata.st_mode):
                raise AssertionError(f"unexpected filesystem object: {entry}")
            kind = "file"
            payload = entry.read_bytes()
        state.append((relative, kind, filesystem_identity(entry), payload))
    return tuple(state)


def stage_states(chain: ActivatedRoutineChain) -> tuple[tuple[object, ...], ...]:
    return tuple(exact_tree_state(path) for path in chain.stage_roots)


def boundary_state(chain: ActivatedRoutineChain) -> dict[str, object]:
    return {
        "root": exact_tree_state(chain.canonical_root),
        "stages": stage_states(chain),
        "lock": filesystem_identity(chain.activation_lock),
        "fds": process_descriptor_inventory(),
        "effective_uid": os.geteuid(),
    }


def rollback_authorization_raw(prepared: object) -> bytes:
    facts = prepared.authorization_facts
    return canonical_document(
        content_document(
            {
                "schema_version": 1,
                "contract": "task-witness-deployer-authorization-v1",
                "purpose": "manual-exact-target-rollback",
                "canonical_root": str(facts.canonical_root),
                "effective_uid": facts.effective_uid,
                "plan_sha256": facts.plan_sha256,
                "maintenance_transaction_sha256": (
                    facts.maintenance_transaction_sha256
                ),
                "expected_active_receipt_sha256": (
                    facts.expected_active_receipt_sha256
                ),
                "target_receipt_sha256": facts.target_receipt_sha256,
            }
        )
    )


class PublicManualRollbackSmokeBoundary:
    """Return exact target/current smoke through the production child seam."""

    def __init__(
        self,
        canonical_root: Path,
        current_receipt_raw: bytes,
        target_receipt_raw: bytes,
        *,
        candidate_accepted: bool,
        rollback_accepted: bool,
    ) -> None:
        self.canonical_root = canonical_root
        self.current = json.loads(current_receipt_raw)
        self.target = json.loads(target_receipt_raw)
        self.accepted = {
            "candidate-smoke": candidate_accepted,
            "rollback-smoke": rollback_accepted,
        }
        self.outputs = {
            "candidate-smoke": smoke_envelope(self.target["smoke"]),
            "rollback-smoke": smoke_envelope(self.current["smoke"]),
        }
        self.observations: list[dict[str, object]] = []

    @property
    def phases(self) -> list[str]:
        return [str(item["phase"]) for item in self.observations]

    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        pass_fds: tuple[int, ...],
    ) -> subprocess.CompletedProcess[bytes]:
        if argv != (str(self.canonical_root / "task-witness"), "activation-smoke"):
            raise AssertionError("manual rollback smoke argv disagrees")
        if pass_fds != (3,):
            raise AssertionError("manual rollback smoke descriptor set disagrees")
        journal_raw = (self.canonical_root / "transaction.json").read_bytes()
        journal = json.loads(journal_raw)
        phase = journal["phase"]
        if phase not in self.outputs:
            raise AssertionError(f"manual rollback smoke phase disagrees: {phase}")
        deployment_raw = (self.canonical_root / "deployment.json").read_bytes()
        deployment = json.loads(deployment_raw)
        active_raw = (self.canonical_root / "active.json").read_bytes()
        self.observations.append(
            {
                "phase": phase,
                "journal_raw": journal_raw,
                "journal": journal,
                "deployment_raw": deployment_raw,
                "deployment": deployment,
                "active_raw": active_raw,
                "receipt_digests": receipt_digest_inventory(self.canonical_root),
            }
        )
        accepted = self.accepted[phase]
        return subprocess.CompletedProcess(
            argv,
            0 if accepted else 70,
            stdout=self.outputs[phase] if accepted else b"",
            stderr=b"" if accepted else b"rejected\n",
        )


class ManualRecoveryPopenAdapter:
    """Run exact smoke output while recording which installed unit was live."""

    def __init__(
        self,
        chain: ActivatedRoutineChain,
        prepared: object,
        original_popen: object,
        *,
        target_accepted: bool,
        current_accepted: bool = True,
    ) -> None:
        self.chain = chain
        self.prepared = prepared
        self.original_popen = original_popen
        self.target_accepted = target_accepted
        self.current_accepted = current_accepted
        self.markers: list[str] = []

    def __call__(self, argv: tuple[str, ...], *args: object, **kwargs: object):
        if args:
            raise AssertionError("manual recovery Popen positional options disagree")
        root = self.chain.canonical_root
        if argv != (str(root / "task-witness"), "activation-smoke"):
            raise AssertionError("manual recovery Popen argv disagrees")
        if kwargs.get("pass_fds") != (3,):
            raise AssertionError("manual recovery Popen descriptor set disagrees")
        journal = json.loads((root / "transaction.json").read_bytes())
        phase = journal["phase"]
        if phase == "candidate-smoke":
            expected_controller = self.prepared.plan.target_authority.control_raws[
                "controller"
            ]
            receipt = json.loads(self.chain.receipt_a_raw)
            accepted = self.target_accepted
            marker = "A"
        elif phase == "rollback-smoke":
            expected_controller = self.prepared.plan.precondition.control_raws[
                "controller"
            ]
            receipt = json.loads(self.chain.receipt_c_raw)
            accepted = self.current_accepted
            marker = "C"
        else:
            raise AssertionError("manual recovery Popen phase disagrees")
        live_controller = (root / "controller" / "task_witness_deploy.py").read_bytes()
        if live_controller != expected_controller:
            raise AssertionError("manual recovery Popen live controller disagrees")
        self.markers.append(marker)
        stdout = smoke_envelope(receipt["smoke"]) if accepted else b""
        stderr = b"" if accepted else b"manual target rejected\n"
        exit_status = 0 if accepted else 70
        source = (
            "import sys;"
            f"sys.stdout.buffer.write(bytes.fromhex('{stdout.hex()}'));"
            f"sys.stderr.buffer.write(bytes.fromhex('{stderr.hex()}'));"
            f"raise SystemExit({exit_status})"
        )
        return self.original_popen((sys.executable, "-c", source), **kwargs)


def run_manual_terminal_process_loss(
    chain: ActivatedRoutineChain,
    request: object,
    authorization_raw: bytes,
    staging_root: Path,
    *,
    target_accepted: bool,
) -> bytes:
    """Lose the real rollback process after its terminal journal is durable."""

    expected_outcome = (
        "manual-target-active" if target_accepted else "manual-current-restored"
    )
    child = os.fork()
    if child == 0:
        deployment = chain.deployment
        original_write = deployment._write_activation_journal
        smoke = PublicManualRollbackSmokeBoundary(
            chain.canonical_root,
            chain.receipt_c_raw,
            chain.receipt_a_raw,
            candidate_accepted=target_accepted,
            rollback_accepted=True,
        )

        def observed_write(canonical_root_fd: int, journal: object) -> None:
            original_write(canonical_root_fd, journal)
            terminal = journal.value["terminal_result"]
            if (
                journal.value["phase"] == "terminal"
                and terminal is not None
                and terminal["outcome"] == expected_outcome
            ):
                os._exit(_MANUAL_TERMINAL_PROCESS_LOSS_EXIT)

        deployment._spawn_activation_smoke_child = smoke
        deployment._write_activation_journal = observed_write
        try:
            deployment.rollback_to(request, authorization_raw, staging_root)
        except Exception:  # noqa: BLE001 - child reports the exact boundary
            os._exit(110)
        os._exit(111)
    waited, status = os.waitpid(child, 0)
    if waited != child or not os.WIFEXITED(status):
        raise AssertionError("manual terminal cut did not exit normally")
    if os.WEXITSTATUS(status) != _MANUAL_TERMINAL_PROCESS_LOSS_EXIT:
        raise AssertionError("manual terminal cut exited at an unexpected boundary")
    return (chain.canonical_root / "transaction.json").read_bytes()


def run_manual_candidate_controller_replacement_process_loss(
    chain: ActivatedRoutineChain,
    request: object,
    authorization_raw: bytes,
    staging_root: Path,
) -> bytes:
    """Lose rollback after C controller is replaced by A, before journal advance."""

    child = os.fork()
    if child == 0:
        deployment = chain.deployment
        original_replace = deployment._replace_control_maintenance_artifact

        def observed_replace(*args: object, **kwargs: object) -> None:
            original_replace(*args, **kwargs)
            if kwargs.get("direction") == "candidate" and kwargs.get("index") == 0:
                os._exit(_MANUAL_REPLACEMENT_PROCESS_LOSS_EXIT)

        deployment._replace_control_maintenance_artifact = observed_replace
        deployment._spawn_activation_smoke_child = lambda *args, **kwargs: os._exit(113)
        try:
            deployment.rollback_to(request, authorization_raw, staging_root)
        except Exception as error:  # noqa: BLE001 - child reports exact boundary
            os.write(2, f"manual replacement child: {error!r}\n".encode())
            os._exit(114)
        os._exit(115)
    waited, status = os.waitpid(child, 0)
    if waited != child or not os.WIFEXITED(status):
        raise AssertionError("manual replacement cut did not exit normally")
    if os.WEXITSTATUS(status) != _MANUAL_REPLACEMENT_PROCESS_LOSS_EXIT:
        raise AssertionError(
            f"manual replacement cut exited unexpectedly: {os.WEXITSTATUS(status)}"
        )
    return (chain.canonical_root / "transaction.json").read_bytes()


def run_manual_prior_controller_replacement_process_loss(
    chain: ActivatedRoutineChain,
    request: object,
    authorization_raw: bytes,
    staging_root: Path,
) -> bytes:
    """Lose rollback after A controller restores to C, before journal advance."""

    child = os.fork()
    if child == 0:
        deployment = chain.deployment
        original_replace = deployment._replace_control_maintenance_artifact
        smoke = PublicManualRollbackSmokeBoundary(
            chain.canonical_root,
            chain.receipt_c_raw,
            chain.receipt_a_raw,
            candidate_accepted=False,
            rollback_accepted=True,
        )

        def observed_replace(*args: object, **kwargs: object) -> None:
            original_replace(*args, **kwargs)
            if kwargs.get("direction") == "prior" and kwargs.get("index") == 0:
                os._exit(_MANUAL_REPLACEMENT_PROCESS_LOSS_EXIT)

        deployment._replace_control_maintenance_artifact = observed_replace
        deployment._spawn_activation_smoke_child = smoke
        try:
            deployment.rollback_to(request, authorization_raw, staging_root)
        except Exception as error:  # noqa: BLE001 - child reports exact boundary
            os.write(2, f"manual prior replacement child: {error!r}\n".encode())
            os._exit(114)
        os._exit(115)
    waited, status = os.waitpid(child, 0)
    if waited != child or not os.WIFEXITED(status):
        raise AssertionError("manual prior replacement cut did not exit normally")
    if os.WEXITSTATUS(status) != _MANUAL_REPLACEMENT_PROCESS_LOSS_EXIT:
        raise AssertionError(
            "manual prior replacement cut exited unexpectedly: "
            f"{os.WEXITSTATUS(status)}"
        )
    return (chain.canonical_root / "transaction.json").read_bytes()


def run_manual_candidate_smoke_process_loss(
    chain: ActivatedRoutineChain,
    request: object,
    authorization_raw: bytes,
    staging_root: Path,
    *,
    cut: str,
) -> bytes:
    """Lose rollback before or after the durable target-smoke acceptance."""

    if cut not in {"child-return", "accepted-journal"}:
        raise AssertionError("manual candidate smoke cut disagrees")
    child = os.fork()
    if child == 0:
        deployment = chain.deployment
        original_write = deployment._write_activation_journal
        smoke = PublicManualRollbackSmokeBoundary(
            chain.canonical_root,
            chain.receipt_c_raw,
            chain.receipt_a_raw,
            candidate_accepted=True,
            rollback_accepted=True,
        )

        def observed_smoke(*args: object, **kwargs: object):
            result = smoke(*args, **kwargs)
            if cut == "child-return":
                os._exit(_MANUAL_SMOKE_PROCESS_LOSS_EXIT)
            return result

        def observed_write(canonical_root_fd: int, journal: object) -> None:
            original_write(canonical_root_fd, journal)
            if (
                cut == "accepted-journal"
                and journal.value["phase"] == "candidate-smoke"
                and journal.value["candidate_smoke_acceptance"] is not None
            ):
                os._exit(_MANUAL_SMOKE_PROCESS_LOSS_EXIT)

        deployment._spawn_activation_smoke_child = observed_smoke
        deployment._write_activation_journal = observed_write
        try:
            deployment.rollback_to(request, authorization_raw, staging_root)
        except Exception as error:  # noqa: BLE001 - child reports exact boundary
            os.write(2, f"manual smoke child: {error!r}\n".encode())
            os._exit(117)
        os._exit(118)
    waited, status = os.waitpid(child, 0)
    if waited != child or not os.WIFEXITED(status):
        raise AssertionError("manual smoke cut did not exit normally")
    if os.WEXITSTATUS(status) != _MANUAL_SMOKE_PROCESS_LOSS_EXIT:
        raise AssertionError(
            f"manual smoke cut exited unexpectedly: {os.WEXITSTATUS(status)}"
        )
    return (chain.canonical_root / "transaction.json").read_bytes()


def run_manual_rollback_smoke_process_loss(
    chain: ActivatedRoutineChain,
    request: object,
    authorization_raw: bytes,
    staging_root: Path,
    *,
    cut: str,
) -> bytes:
    """Lose rollback before or after the durable current-smoke acceptance."""

    if cut not in {"child-return", "accepted-journal"}:
        raise AssertionError("manual rollback smoke cut disagrees")
    child = os.fork()
    if child == 0:
        deployment = chain.deployment
        original_write = deployment._write_activation_journal
        smoke = PublicManualRollbackSmokeBoundary(
            chain.canonical_root,
            chain.receipt_c_raw,
            chain.receipt_a_raw,
            candidate_accepted=False,
            rollback_accepted=True,
        )

        def observed_smoke(*args: object, **kwargs: object):
            result = smoke(*args, **kwargs)
            phase = json.loads(
                (chain.canonical_root / "transaction.json").read_bytes()
            )["phase"]
            if cut == "child-return" and phase == "rollback-smoke":
                os._exit(_MANUAL_SMOKE_PROCESS_LOSS_EXIT)
            return result

        def observed_write(canonical_root_fd: int, journal: object) -> None:
            original_write(canonical_root_fd, journal)
            if (
                cut == "accepted-journal"
                and journal.value["phase"] == "rollback-smoke"
                and journal.value["rollback_smoke_acceptance"] is not None
            ):
                os._exit(_MANUAL_SMOKE_PROCESS_LOSS_EXIT)

        deployment._spawn_activation_smoke_child = observed_smoke
        deployment._write_activation_journal = observed_write
        try:
            deployment.rollback_to(request, authorization_raw, staging_root)
        except Exception as error:  # noqa: BLE001 - child reports exact boundary
            os.write(2, f"manual rollback smoke child: {error!r}\n".encode())
            os._exit(117)
        os._exit(118)
    waited, status = os.waitpid(child, 0)
    if waited != child or not os.WIFEXITED(status):
        raise AssertionError("manual rollback smoke cut did not exit normally")
    if os.WEXITSTATUS(status) != _MANUAL_SMOKE_PROCESS_LOSS_EXIT:
        raise AssertionError(
            f"manual rollback smoke cut exited unexpectedly: {os.WEXITSTATUS(status)}"
        )
    return (chain.canonical_root / "transaction.json").read_bytes()


def run_manual_rollback_cleanup_process_loss(
    chain: ActivatedRoutineChain,
    request: object,
    authorization_raw: bytes,
    staging_root: Path,
) -> bytes:
    """Lose rollback after R_D is unlinked, before the journal advances."""

    child = os.fork()
    if child == 0:
        deployment = chain.deployment
        original_remove = deployment._remove_activation_artifact
        smoke = PublicManualRollbackSmokeBoundary(
            chain.canonical_root,
            chain.receipt_c_raw,
            chain.receipt_a_raw,
            candidate_accepted=False,
            rollback_accepted=True,
        )

        def observed_remove(root_fd: int, artifact: object) -> None:
            original_remove(root_fd, artifact)
            if artifact.role == "rollback-receipt":
                os._exit(_MANUAL_CLEANUP_PROCESS_LOSS_EXIT)

        deployment._spawn_activation_smoke_child = smoke
        deployment._remove_activation_artifact = observed_remove
        try:
            deployment.rollback_to(request, authorization_raw, staging_root)
        except Exception as error:  # noqa: BLE001 - child reports exact boundary
            os.write(2, f"manual cleanup child: {error!r}\n".encode())
            os._exit(120)
        os._exit(121)
    waited, status = os.waitpid(child, 0)
    if waited != child or not os.WIFEXITED(status):
        raise AssertionError("manual cleanup cut did not exit normally")
    if os.WEXITSTATUS(status) != _MANUAL_CLEANUP_PROCESS_LOSS_EXIT:
        raise AssertionError(
            f"manual cleanup cut exited unexpectedly: {os.WEXITSTATUS(status)}"
        )
    return (chain.canonical_root / "transaction.json").read_bytes()


def run_manual_journal_process_loss_cut(
    chain: ActivatedRoutineChain,
    request: object,
    authorization_raw: bytes,
    staging_root: Path,
    *,
    generation: str,
    cut: str,
) -> ManualJournalCut:
    """Lose manual rollback within one exact shared journal publication."""

    generations = {
        "mixed-candidate-control",
        "target-active-terminal",
        "current-restored-terminal",
    }
    if generation not in generations:
        raise AssertionError("manual journal generation disagrees")
    allowed = {"temp-create", "partial-write", "full-write", "replace", "parent-fsync"}
    if cut not in allowed:
        raise AssertionError("manual journal cut disagrees")
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        deployment = chain.deployment
        original_open = deployment.os.open
        original_write = deployment.os.write
        original_fsync = deployment.os.fsync
        original_fstat = deployment.os.fstat
        original_replace = deployment.os.replace
        original_write_all = deployment._write_all
        original_journal_write = deployment._write_activation_journal
        root_metadata = chain.canonical_root.lstat()
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
                    os._exit(123)
                offset += written

        def process_loss() -> None:
            os.close(write_fd)
            os._exit(_MANUAL_JOURNAL_PROCESS_LOSS_EXIT)

        def matches(value: dict[str, object]) -> bool:
            if generation == "mixed-candidate-control":
                return value["phase"] == "control-switching" and value[
                    "pending_step"
                ] == {
                    "operation": "replace-control",
                    "index": 1,
                    "role": "policy",
                }
            terminal = value["terminal_result"]
            expected = (
                "manual-target-active"
                if generation == "target-active-terminal"
                else "manual-current-restored"
            )
            return (
                value["phase"] == "terminal"
                and terminal is not None
                and terminal["outcome"] == expected
            )

        def observed_journal_write(canonical_root_fd: int, journal: object) -> None:
            if matches(journal.value):
                if state["armed"]:
                    raise AssertionError("manual journal generation repeated")
                current_raw = (chain.canonical_root / "transaction.json").read_bytes()
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
                        raise AssertionError("manual journal partial write stalled")
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
                    raise AssertionError("manual journal replacement target disagrees")
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
        deployment._spawn_activation_smoke_child = PublicManualRollbackSmokeBoundary(
            chain.canonical_root,
            chain.receipt_c_raw,
            chain.receipt_a_raw,
            candidate_accepted=generation != "current-restored-terminal",
            rollback_accepted=True,
        )
        try:
            deployment.rollback_to(request, authorization_raw, staging_root)
        except Exception as error:  # noqa: BLE001 - child reports exact boundary
            if not state["armed"]:
                write_pipe(f"E{type(error).__name__}: {error}".encode())
            os._exit(124)
        os._exit(125)
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
        raise AssertionError("manual journal cut did not exit normally")
    if os.WEXITSTATUS(status) != _MANUAL_JOURNAL_PROCESS_LOSS_EXIT:
        diagnostic = payload[1:].decode(errors="replace") if payload[:1] == b"E" else ""
        raise AssertionError(
            "manual journal cut exited unexpectedly: "
            f"{os.WEXITSTATUS(status)} {diagnostic}"
        )
    if len(payload) < 17 or payload[:1] != b"J":
        raise AssertionError("manual journal cut report disagrees")
    current_length = int.from_bytes(payload[1:9], "big")
    target_length = int.from_bytes(payload[9:17], "big")
    current_start = 17
    target_start = current_start + current_length
    current_raw = payload[current_start:target_start]
    target_raw = payload[target_start : target_start + target_length]
    if target_start + target_length != len(payload):
        raise AssertionError("manual journal cut framing disagrees")
    json.loads(current_raw)
    json.loads(target_raw)
    return ManualJournalCut(current_raw, target_raw)
