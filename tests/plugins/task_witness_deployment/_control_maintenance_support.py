from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from ._routine_support import RoutineDeploymentFixture
from ._support import canonical_document, content_document, sha256

CONTROL_ROLES = ("controller", "policy", "launcher", "client", "shim")
CONTROL_PREIMAGE_ROLES = (
    "controller",
    "policy",
    "launcher",
    "client",
    "smoke-bundle-manifest",
    "shim",
)
MAINTENANCE_REPLACEMENT_ROLES = (
    "controller",
    "policy",
    "launcher",
    "client",
    "smoke-bundle-manifest",
    "active-record",
    "deployment-alias",
    "shim",
)


def tree_state_with_result(
    baseline: tuple[tuple[str, str, int, object], ...],
    result: object,
) -> tuple[tuple[str, str, int, object], ...]:
    entries = list(baseline)
    if not any(item[0] == "transaction-results" for item in entries):
        entries.append(("transaction-results", "directory", stat.S_IFDIR | 0o700, None))
    raw = result.journal_raw
    entries.append(
        (
            f"transaction-results/sha256-{result.transaction_id}.json",
            "file",
            stat.S_IFREG | 0o600,
            (len(raw), sha256(raw)),
        )
    )
    return tuple(sorted(entries))


CONTROL_SURFACE_CONTRACT = "task-witness-control-surface-v1"
EXACT_PROCESS_PROFILE = {
    "contract": "task-witness-process-profile-v2",
    "interpreter_flags": ["-B", "-I", "-S", "-X", "disable-remote-debug"],
    "environment": {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
    },
    "cwd": "/",
    "stdin": "closed",
    "close_fds": True,
    "new_session": True,
    "restore_signals": True,
    "umask": 0o077,
    "validation_deadline_seconds": 60,
    "accepted_output_deadline_seconds": 60,
    "termination_grace_seconds": 2,
    "kill_reap_seconds": 1,
    "post_leader_pipe_drain_seconds": 1,
    "stdout_max_bytes": 4 * 1024 * 1024,
    "stderr_max_bytes": 256 * 1024,
    "diagnostic_max_bytes": 4 * 1024,
    "diagnostic_write_seconds": 0.05,
    "io_chunk_bytes": 64 * 1024,
    "exclusive_lock_seconds": 65,
    "shared_lock_seconds": 2,
}
EXACT_RECEIPT_CONTRACTS = {
    "active": "task-witness-launch-active-v1",
    "runtime": "task-witness-runtime-v1",
    "runtime_artifact_manifest": "task-witness-runtime-artifact-manifest-v2",
    "envelope": "task-witness-launch-envelope-v1",
    "anchor": "task-witness-complete-anchor-v1",
    "canonical_projection": "task-witness-canonical-projection-v2",
    "trust_context": "task-witness-trust-context-v2",
    "process_profile": "task-witness-process-profile-v2",
    "source_selection": "task-witness-source-selection-v1",
    "manager_binding": "task-witness-manager-binding-v1",
    "compatibility_policy": "task-witness-compatibility-policy-v2",
    "deployment_receipt": "task-witness-deployment-receipt-v2",
    "rollback_receipt": "task-witness-rollback-receipt-v1",
}
EXACT_RECEIPT_CLIENT_CONTRACTS = {
    **EXACT_RECEIPT_CONTRACTS,
    "activation_intent": "task-witness-activation-intent-v1",
    "activation_transaction": "task-witness-activation-transaction-v1",
    "bundle_inventory": "task-witness-bundle-inventory-v1",
    "control_surface": CONTROL_SURFACE_CONTRACT,
    "deployer_authorization": "task-witness-deployer-authorization-v1",
    "first_install_rollback": "task-witness-first-install-rollback-v1",
    "intrinsic_smoke_provider": "task-witness-intrinsic-smoke-provider-v1",
    "smoke_bundle": "task-witness-smoke-bundle-v1",
    "smoke_issuer": "task-witness-smoke-issuer-v1",
    "smoke_issuer_implementation": ("task-witness-smoke-issuer-implementation-v1"),
    "smoke_producer_implementation": ("task-witness-smoke-producer-implementation-v1"),
    "source_evidence": "task-witness-source-evidence-v1",
    "staged_deployment": "task-witness-staged-deployment-v1",
    "validator_artifact_manifest": ("task-witness-validator-artifact-manifest-v1"),
}
EXACT_CONTROL_SURFACE = {
    "schema_version": 1,
    "contract": CONTROL_SURFACE_CONTRACT,
    "process_profile": EXACT_PROCESS_PROFILE,
    "contracts": EXACT_RECEIPT_CLIENT_CONTRACTS,
}


class ControlMaintenanceFixture:
    """Build one public compatible-forward candidate with changed controls."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.routine = RoutineDeploymentFixture(root)

    def deployment(self):
        return self.routine.deployment()

    @staticmethod
    def _rewrite_policy(
        candidate: Path,
        mutator: Callable[[dict[str, object]], None],
    ) -> None:
        policy_path = candidate / "controller" / "policy.json"
        policy = json.loads(policy_path.read_bytes())
        policy.pop("content_sha256", None)
        mutator(policy)
        policy_path.write_bytes(canonical_document(content_document(policy)))

    @staticmethod
    def _declare_exact_control_surface(policy: dict[str, object]) -> None:
        policy["schema_version"] = 2
        policy["contract"] = "task-witness-compatibility-policy-v2"
        policy["control_surface"] = json.loads(json.dumps(EXACT_CONTROL_SURFACE))

    def _scenario(
        self,
        policy_mutator: Callable[[dict[str, object]], None],
    ):
        initial, active = self.routine.activate_initial()
        candidate = self.routine.candidate_root()
        controller = candidate / "controller" / "task_witness_deploy.py"
        controller.write_bytes(
            controller.read_bytes()
            + b"\n# complete-control-set maintenance candidate B\n"
        )
        self._rewrite_policy(candidate, policy_mutator)
        request = self.routine.request_for_candidate(
            initial.canonical_root,
            active.active_receipt_sha256,
            candidate,
            release_version="1.0.1",
            revision="b" * 40,
            sequence=8,
        )
        return initial, active, candidate, request

    def scenario(self):
        return self._scenario(self._declare_exact_control_surface)

    def v1_scenario(self):
        def retain_v1(policy: dict[str, object]) -> None:
            policy["schema_version"] = 1
            policy["contract"] = "task-witness-compatibility-policy-v1"
            policy.pop("control_surface", None)

        return self._scenario(retain_v1)

    def invalid_v2_scenario(
        self,
        mutator: Callable[[dict[str, object]], None],
    ):
        def invalidate(policy: dict[str, object]) -> None:
            self._declare_exact_control_surface(policy)
            mutator(policy)

        return self._scenario(invalidate)

    def policy_scenario(self):
        def change_policy(policy: dict[str, object]) -> None:
            self._declare_exact_control_surface(policy)
            policy["providers"] = [
                {
                    "plugin_id": "reserved-maintenance-provider",
                    "authority_profile": "reserved-maintenance-authority",
                    "producers": [],
                    "issuers": [],
                    "validators": [],
                }
            ]

        return self._scenario(change_policy)

    def runtime_qualification_scenario(self):
        initial, active = self.routine.activate_initial()
        candidate = self.routine.candidate_root()
        self._rewrite_policy(candidate, self._declare_exact_control_surface)
        request = self.routine.request_for_candidate(
            initial.canonical_root,
            active.active_receipt_sha256,
            candidate,
            release_version="1.0.1",
            revision="b" * 40,
            sequence=8,
        )
        qualification = json.loads(request.runtime_qualification_raw)
        qualification["runtime_closure"]["evidence_sha256"] = "7" * 64
        qualification_raw = canonical_document(
            content_document(
                {
                    key: value
                    for key, value in qualification.items()
                    if key != "content_sha256"
                }
            )
        )
        return (
            initial,
            active,
            candidate,
            replace(request, runtime_qualification_raw=qualification_raw),
        )

    def no_op_runtime_qualification_scenario(self):
        initial, active = self.routine.activate_initial()
        candidate = Path(initial.request.candidate_root)
        request = self.routine.request_for_candidate(
            initial.canonical_root,
            active.active_receipt_sha256,
            candidate,
            release_version="1.0.0",
            revision="a" * 40,
            sequence=7,
        )
        qualification = json.loads(request.runtime_qualification_raw)
        qualification["runtime_closure"]["evidence_sha256"] = "7" * 64
        qualification_raw = canonical_document(
            content_document(
                {
                    key: value
                    for key, value in qualification.items()
                    if key != "content_sha256"
                }
            )
        )
        return (
            initial,
            active,
            candidate,
            replace(request, runtime_qualification_raw=qualification_raw),
        )

    def rewrite_stage(self, staged: object, mutator) -> Path:
        return self.routine.rewrite_routine_stage(staged, mutator)

    @staticmethod
    def authorization_raw(prepared: object) -> bytes:
        facts = prepared.authorization_facts
        purpose = (
            "source-boundary-change"
            if prepared.plan.classification.reason
            in {"downgrade", "exact-release-pin", "source-authority"}
            else "complete-control-set-maintenance"
        )
        return canonical_document(
            content_document(
                {
                    "schema_version": 1,
                    "contract": "task-witness-deployer-authorization-v1",
                    "purpose": purpose,
                    "canonical_root": str(facts.canonical_root),
                    "effective_uid": facts.effective_uid,
                    "plan_sha256": facts.plan_sha256,
                    "maintenance_transaction_sha256": (
                        facts.maintenance_transaction_sha256
                    ),
                    "candidate_controller_sha256": (facts.candidate_controller_sha256),
                    "candidate_policy_sha256": facts.candidate_policy_sha256,
                    "source_selection_sha256": facts.source_selection_sha256,
                    "source_evidence_sha256": facts.source_evidence_sha256,
                    "expected_active_receipt_sha256": (
                        facts.expected_active_receipt_sha256
                    ),
                }
            )
        )

    @staticmethod
    def tree_state(root: Path) -> tuple[tuple[str, str, int, object], ...]:
        if not root.exists():
            return ()
        state = []
        for entry in sorted(root.rglob("*")):
            relative = entry.relative_to(root).as_posix()
            metadata = entry.lstat()
            if entry.is_symlink():
                kind = "symlink"
                payload: object = str(entry.readlink())
            elif entry.is_dir():
                kind = "directory"
                payload = None
            else:
                kind = "file"
                raw = entry.read_bytes()
                payload = (len(raw), sha256(raw))
            state.append((relative, kind, metadata.st_mode, payload))
        return tuple(state)

    @staticmethod
    def assert_private_stage(test, stage_root: Path) -> None:
        root_metadata = stage_root.lstat()
        test.assertTrue(stat.S_ISDIR(root_metadata.st_mode))
        test.assertEqual(root_metadata.st_uid, os.geteuid())
        test.assertEqual(stat.S_IMODE(root_metadata.st_mode), 0o700)
        for entry in stage_root.rglob("*"):
            metadata = entry.lstat()
            test.assertEqual(metadata.st_uid, os.geteuid())
            test.assertFalse(entry.is_symlink())
            if entry.is_dir():
                test.assertEqual(stat.S_IMODE(metadata.st_mode), 0o700)
            else:
                test.assertTrue(stat.S_ISREG(metadata.st_mode))
                test.assertEqual(metadata.st_nlink, 1)
                test.assertEqual(stat.S_IMODE(metadata.st_mode) & 0o077, 0)

    @staticmethod
    def canonical_stage_value(staged: object) -> dict[str, object]:
        value = json.loads(staged.stage_raw)
        if canonical_document(value) != staged.stage_raw:
            raise AssertionError("control maintenance stage is not canonical")
        return value
