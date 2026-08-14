from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ._control_maintenance_activation_support import (
    CONTROL_MAINTENANCE_REPLACE_PROCESS_LOSS_EXIT,
    assert_candidate_control_set_installed,
    run_control_maintenance_activation_replace_process_loss,
)
from ._routine_activation_support import (
    assert_no_transaction_residue,
    exact_live_journal,
    regular_file_snapshot,
    run_routine_selector_process_loss_after_replace,
    selector_raws,
    staged_artifact,
    staged_candidate_selector_raws,
)
from ._routine_support import RoutineSmokeBoundary, smoke_envelope
from ._source_recovery_support import (
    InstalledRecoveryClientProcess,
    SourceRecoveryFixture,
)
from ._support import sha256


class SourceEvidenceRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.fixture = SourceRecoveryFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_publisher_downgrade_recovers_from_exact_stage_without_candidate_reread(
        self,
    ) -> None:
        deployment = self.fixture.deployment
        prepared = self.fixture.staged_publisher_downgrade()
        staged = prepared.staged
        request = prepared.request
        canonical_root = prepared.initial.canonical_root
        stage_snapshot = regular_file_snapshot(staged.stage_path.parent)
        candidate_snapshot = regular_file_snapshot(request.candidate_root)

        self.assertEqual(
            staged.deployment_value["authorization"]["purpose"],
            "source-boundary-change",
        )
        self.assertNotIn("control_preimage", staged.rollback_value)
        run_routine_selector_process_loss_after_replace(
            deployment,
            prepared.activation,
            direction="candidate",
            selector_index=0,
        )
        journal_raw = exact_live_journal(canonical_root)
        journal = json.loads(journal_raw)
        self.assertEqual(journal["transaction_class"], "routine-payload")
        self.assertEqual(
            journal["pending_step"],
            {
                "operation": "replace-selector",
                "index": 0,
                "role": "active-record",
            },
        )

        detached = request.candidate_root.with_name(
            f"{request.candidate_root.name}-detached"
        )
        request.candidate_root.rename(detached)
        snapshot_paths: list[Path] = []
        real_snapshot = deployment._snapshot_candidate_tree

        def observe_snapshot(path: Path) -> object:
            resolved = path.resolve()
            snapshot_paths.append(resolved)
            if resolved == request.candidate_root:
                raise AssertionError("recovery reread the unavailable candidate source")
            return real_snapshot(path)

        smoke = RoutineSmokeBoundary(
            canonical_root,
            staged.deployment_value["smoke"],
            prepared.prepared.plan.precondition.receipt_value["smoke"],
            candidate_accepted=True,
            rollback_accepted=True,
        )
        with (
            mock.patch.object(
                deployment,
                "_snapshot_candidate_tree",
                side_effect=observe_snapshot,
            ),
            mock.patch.object(
                deployment,
                "_spawn_activation_smoke_child",
                side_effect=smoke,
            ),
        ):
            result = deployment.recover_transaction(
                deployment.RecoveryRequest(
                    activation=prepared.activation,
                    expected_journal_raw=journal_raw,
                )
            )

        self.assertEqual(result.outcome, "candidate-active")
        self.assertEqual(result.active_receipt_sha256, sha256(staged.deployment_raw))
        self.assertEqual(smoke.calls, ["candidate-smoke"])
        self.assertNotIn(request.candidate_root, snapshot_paths)
        self.assertIn(staged.stage_path.parent, snapshot_paths)
        self.assertFalse(request.candidate_root.exists())
        self.assertEqual(regular_file_snapshot(detached), candidate_snapshot)
        self.assertEqual(
            deployment.verify_deployment_stage(staged.stage_path).raw,
            staged.stage_raw,
        )
        self.assertEqual(
            regular_file_snapshot(staged.stage_path.parent),
            stage_snapshot,
        )
        self.assertEqual(
            selector_raws(canonical_root),
            staged_candidate_selector_raws(staged),
        )
        assert_no_transaction_residue(canonical_root)

    def test_cross_mode_control_recovery_uses_preimage_engine_rehydrates_evidence_and_client_accepts(
        self,
    ) -> None:
        deployment = self.fixture.deployment
        recovery_load_marker = self.root / "prior-controller-recovery-load.txt"
        prepared = self.fixture.staged_cross_mode_control_change(recovery_load_marker)
        staged = prepared.staged
        canonical_root = prepared.initial.canonical_root
        stage_snapshot = regular_file_snapshot(staged.stage_path.parent)

        self.assertEqual(
            prepared.prepared.plan.classification.reason,
            "source-authority",
        )
        self.assertEqual(
            prepared.prepared.plan.value["operation"],
            "complete-control-set-maintenance",
        )
        self.assertEqual(
            staged.deployment_value["authorization"]["purpose"],
            "source-boundary-change",
        )
        self.assertIn("control_preimage", staged.rollback_value)

        process_loss = run_control_maintenance_activation_replace_process_loss(
            deployment,
            prepared,
            direction="candidate",
            replacement_index=0,
        )
        self.assertEqual(
            process_loss.exit_status,
            CONTROL_MAINTENANCE_REPLACE_PROCESS_LOSS_EXIT,
            process_loss.diagnostic,
        )
        self.assertEqual(process_loss.diagnostic, "")
        journal_raw = exact_live_journal(canonical_root)
        journal = json.loads(journal_raw)
        self.assertEqual(journal["transaction_class"], "control-set-maintenance")
        self.assertEqual(
            journal["pending_step"],
            {
                "operation": "replace-control",
                "index": 0,
                "role": "controller",
            },
        )
        self.assertFalse(recovery_load_marker.exists())

        client = InstalledRecoveryClientProcess(
            deployment,
            canonical_root,
            self.root / "recovery-client-support",
        )
        with (
            mock.patch.object(
                deployment.subprocess,
                "Popen",
                side_effect=client,
            ),
            mock.patch.object(
                deployment.os,
                "read",
                side_effect=client.observe_read,
            ),
        ):
            result = deployment.recover_transaction(
                deployment.RecoveryRequest(
                    activation=prepared.activation,
                    expected_journal_raw=journal_raw,
                )
            )

        self.assertEqual(
            result.outcome,
            "candidate-active",
            (
                client.phases,
                result.journal_value["terminal_result"],
                {name: bytes(raw) for name, raw in client.output.items()},
                recovery_load_marker.read_text(encoding="utf-8")
                if recovery_load_marker.is_file()
                else "prior controller was not freshly loaded",
            ),
        )
        self.assertEqual(result.active_receipt_sha256, sha256(staged.deployment_raw))
        self.assertEqual(client.phases, ["candidate-smoke"])
        self.assertEqual(
            bytes(client.output["stdout"]),
            smoke_envelope(staged.deployment_value["smoke"]),
        )
        self.assertEqual(bytes(client.output["stderr"]), b"")
        self.assertTrue(recovery_load_marker.is_file())
        self.assertTrue(
            recovery_load_marker.read_text(encoding="utf-8").startswith(
                "_task_witness_control_recovery_"
            )
        )
        self.assertEqual(
            deployment.verify_deployment_stage(staged.stage_path).raw,
            staged.stage_raw,
        )
        self.assertEqual(
            regular_file_snapshot(staged.stage_path.parent),
            stage_snapshot,
        )
        self.assertEqual(
            selector_raws(canonical_root),
            staged_candidate_selector_raws(staged),
        )
        assert_candidate_control_set_installed(staged)
        client_artifact = staged_artifact(staged, "client")
        self.assertEqual(
            Path(client_artifact.installed["path"]).read_bytes(),
            client_artifact.raw,
        )
        assert_no_transaction_residue(canonical_root)


if __name__ == "__main__":
    unittest.main()
