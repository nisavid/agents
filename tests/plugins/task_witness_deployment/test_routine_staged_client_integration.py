from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.plugins.task_witness_client._support import parse_diagnostic

from ._routine_activation_support import (
    assert_no_transaction_residue,
    assert_smoke_observation,
    expected_active_receipt_inventory,
    receipt_digest_inventory,
    selector_raws,
    staged_candidate_selector_raws,
    staged_prior_selector_raws,
)
from ._routine_staged_client_support import installed_client_smoke_process
from ._routine_support import RoutineDeploymentFixture, smoke_envelope
from ._support import sha256


class RoutineStagedClientIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.fixture = RoutineDeploymentFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_public_routine_activation_accepts_b_through_installed_client_and_launcher(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        initial, _, _, _, staged_b, activation_b = self.fixture.staged_routine()
        selector_a = selector_raws(initial.canonical_root)
        expected_receipts = expected_active_receipt_inventory(
            selector_a[1],
            staged_b,
        )
        client = next(
            item for item in initial.staged.artifacts if item.role == "client"
        )
        launcher = next(
            item for item in initial.staged.artifacts if item.role == "launcher"
        )
        self.assertEqual(client.installed_path.read_bytes(), client.raw)
        self.assertEqual(
            sha256(client.raw),
            "2fabfdba708ea84c366b480c8a5d7aacf61706ba8e62e9cd97f2afe8dda87e9c",
        )
        self.assertEqual(launcher.installed_path.read_bytes(), launcher.raw)
        self.assertEqual(
            sha256(launcher.raw),
            "4d97e8df695276f8da9cb87ef1c27fa5979c5edf9f338127a0a19ae077d3172b",
        )

        with installed_client_smoke_process(
            deployment,
            initial.canonical_root,
            self.root / "smoke-process-support",
        ) as smoke:
            result = deployment.activate_staged(activation_b)

        receipt_b = sha256(staged_b.deployment_raw)
        candidate_envelope = smoke_envelope(staged_b.deployment_value["smoke"])
        self.assertEqual(result.outcome, "candidate-active")
        self.assertEqual(result.candidate_receipt_sha256, receipt_b)
        self.assertEqual(result.active_receipt_sha256, receipt_b)
        self.assertEqual(
            result.accepted_envelope_sha256,
            staged_b.deployment_value["smoke"]["expected_envelope_sha256"],
        )
        self.assertEqual(smoke.phases, ["candidate-smoke"])
        candidate_call = smoke.calls[0]
        assert_smoke_observation(
            candidate_call.observation,
            staged=staged_b,
            phase="candidate-smoke",
            live_selectors=staged_candidate_selector_raws(staged_b),
            expected_receipt_digests=expected_receipts,
        )
        self.assertEqual(candidate_call.completed.returncode, 0)
        self.assertEqual(candidate_call.completed.stdout, candidate_envelope)
        self.assertEqual(candidate_call.completed.stderr, b"")
        self.assertEqual(
            selector_raws(initial.canonical_root),
            staged_candidate_selector_raws(staged_b),
        )
        self.assertEqual(
            receipt_digest_inventory(initial.canonical_root),
            expected_receipts,
        )
        assert_no_transaction_residue(initial.canonical_root)
        self.assertEqual(client.installed_path.read_bytes(), client.raw)
        self.assertEqual(launcher.installed_path.read_bytes(), launcher.raw)

    def test_public_routine_activation_rejects_b_and_accepts_restored_a_through_installed_client_and_launcher(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        initial, active_a = self.fixture.activate_initial()
        candidate_b = self.fixture.candidate_root()
        runtime = candidate_b / "runtime" / "task_witness.py"
        runtime.write_bytes(
            runtime.read_bytes()
            + b"\n\ndef validate_bundle(*_args, **_kwargs):\n"
            + b'    raise EvidenceError("fixture candidate rejects smoke")\n'
        )
        request_b = self.fixture.request_for_candidate(
            initial.canonical_root,
            active_a.active_receipt_sha256,
            candidate_b,
            release_version="1.0.1",
            revision="b" * 40,
            sequence=8,
        )
        prepared_b = deployment.prepare_deployment(request_b)
        authorization_raw = self.fixture.authorization_raw(prepared_b)
        staged_b = deployment.stage_deployment(
            request_b,
            authorization_raw,
            self.root / "routine-stage",
        )
        selector_a = selector_raws(initial.canonical_root)
        expected_receipts = expected_active_receipt_inventory(
            selector_a[1],
            staged_b,
        )
        activation_b = deployment.ActivationRequest(
            deployment=request_b,
            authorization_raw=authorization_raw,
            stage_receipt=staged_b.stage_path,
        )
        client = next(
            item for item in initial.staged.artifacts if item.role == "client"
        )
        launcher = next(
            item for item in initial.staged.artifacts if item.role == "launcher"
        )
        self.assertEqual(client.installed_path.read_bytes(), client.raw)
        self.assertEqual(
            sha256(client.raw),
            "2fabfdba708ea84c366b480c8a5d7aacf61706ba8e62e9cd97f2afe8dda87e9c",
        )
        self.assertEqual(launcher.installed_path.read_bytes(), launcher.raw)
        self.assertEqual(
            sha256(launcher.raw),
            "4d97e8df695276f8da9cb87ef1c27fa5979c5edf9f338127a0a19ae077d3172b",
        )

        with installed_client_smoke_process(
            deployment,
            initial.canonical_root,
            self.root / "smoke-process-support",
        ) as smoke:
            result = deployment.activate_staged(activation_b)

        receipt_b = sha256(staged_b.deployment_raw)
        prior_smoke = staged_b.rollback_value["prior_activation_unit"]["smoke"]
        prior_envelope = smoke_envelope(prior_smoke)
        self.assertEqual(result.outcome, "restored-prior")
        self.assertEqual(result.candidate_receipt_sha256, receipt_b)
        self.assertEqual(
            result.active_receipt_sha256,
            active_a.active_receipt_sha256,
        )
        self.assertEqual(
            result.accepted_envelope_sha256,
            staged_b.rollback_value["prior_activation_unit"]["smoke"][
                "expected_envelope_sha256"
            ],
        )
        self.assertEqual(
            result.journal_value["terminal_result"]["failure_class"],
            "candidate-smoke-rejected",
        )
        self.assertEqual(smoke.phases, ["candidate-smoke", "rollback-smoke"])
        candidate_call, rollback_call = smoke.calls
        assert_smoke_observation(
            candidate_call.observation,
            staged=staged_b,
            phase="candidate-smoke",
            live_selectors=staged_candidate_selector_raws(staged_b),
            expected_receipt_digests=expected_receipts,
        )
        self.assertEqual(candidate_call.completed.returncode, 65)
        self.assertEqual(candidate_call.completed.stdout, b"")
        diagnostic, fields = parse_diagnostic(candidate_call.completed.stderr)
        self.assertEqual(
            diagnostic,
            "task witness client rejected: launcher rejected validation",
        )
        self.assertEqual(
            fields,
            {
                "validator_code_executed": "unknown",
                "active_state_changed": "unknown",
                "current_receipt": (
                    "sha256:" + staged_b.deployment_value["content_sha256"][:12]
                ),
                "candidate_receipt": "none",
                "rollback": "not-run",
                "next_action": (
                    "do not retry; inspect the bundle and validator evidence"
                ),
            },
        )
        assert_smoke_observation(
            rollback_call.observation,
            staged=staged_b,
            phase="rollback-smoke",
            live_selectors=staged_prior_selector_raws(staged_b),
            expected_receipt_digests=expected_receipts,
        )
        self.assertEqual(rollback_call.completed.returncode, 0)
        self.assertEqual(rollback_call.completed.stdout, prior_envelope)
        self.assertEqual(rollback_call.completed.stderr, b"")
        self.assertEqual(selector_raws(initial.canonical_root), selector_a)
        self.assertEqual(
            receipt_digest_inventory(initial.canonical_root),
            frozenset(
                {
                    active_a.active_receipt_sha256,
                    staged_b.plan.precondition.receipt_value["rollback"]["sha256"],
                }
            ),
        )
        assert_no_transaction_residue(initial.canonical_root)
        self.assertEqual(client.installed_path.read_bytes(), client.raw)
        self.assertEqual(launcher.installed_path.read_bytes(), launcher.raw)


if __name__ == "__main__":
    unittest.main()
