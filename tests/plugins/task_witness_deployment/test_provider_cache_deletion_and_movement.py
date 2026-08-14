from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ._activation_support import thaw_json
from ._provider_cache_deletion_and_movement_support import (
    external_provider_bundle,
    external_provider_candidate_oracle,
    external_provider_candidates,
    invoke_installed_client,
    prepare_provider_first_install,
    regular_file_authority,
)
from ._routine_staged_client_support import installed_client_smoke_process
from ._routine_support import RoutineDeploymentFixture
from ._support import sha256


class ProviderCacheDeletionAndMovementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.fixture = RoutineDeploymentFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_installed_client_validates_active_and_historical_external_provider_after_cache_deletion_and_checkout_movement(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        candidates = external_provider_candidates(self.fixture)
        canonical_root = (
            self.fixture.first_install.root
            / "account-home"
            / ".local"
            / "libexec"
            / "task-witness"
        )
        oracle_a = external_provider_candidate_oracle(
            candidates.candidate_a,
            canonical_root,
            provider_revision="a",
        )
        oracle_b = external_provider_candidate_oracle(
            candidates.candidate_b,
            canonical_root,
            provider_revision="b",
        )
        self.assertNotEqual(
            oracle_a.validator_implementation_sha256,
            oracle_b.validator_implementation_sha256,
        )
        self.assertNotEqual(
            oracle_a.trust_context_sha256,
            oracle_b.trust_context_sha256,
        )
        first = prepare_provider_first_install(self.fixture, candidates)
        self.assertEqual(first.canonical_root, canonical_root)
        with installed_client_smoke_process(
            deployment,
            first.canonical_root,
            self.root / "first-activation-client-support",
        ) as first_smoke:
            active_a = deployment.activate_staged(first.activation)
        self.assertEqual(active_a.outcome, "candidate-active")
        self.assertEqual(first_smoke.phases, ["candidate-smoke"])
        self.assertEqual(first_smoke.calls[0].completed.returncode, 0)
        self.assertEqual(first_smoke.calls[0].filesystem_mutations, ())

        request_b = self.fixture.request_for_candidate(
            first.canonical_root,
            active_a.active_receipt_sha256,
            candidates.candidate_b,
            release_version="1.0.1",
            revision="b" * 40,
            sequence=8,
            **candidates.identity,
        )
        prepared_b = deployment.prepare_deployment(request_b)
        authorization_b = self.fixture.authorization_raw(prepared_b)
        staged_b = deployment.stage_deployment(
            request_b,
            authorization_b,
            self.root / "routine-stage-b",
        )
        activation_b = deployment.ActivationRequest(
            deployment=request_b,
            authorization_raw=authorization_b,
            stage_receipt=staged_b.stage_path,
        )
        with installed_client_smoke_process(
            deployment,
            first.canonical_root,
            self.root / "second-activation-client-support",
        ) as second_smoke:
            active_b = deployment.activate_staged(activation_b)
        self.assertEqual(active_b.outcome, "candidate-active")
        self.assertEqual(second_smoke.phases, ["candidate-smoke"])
        self.assertEqual(second_smoke.calls[0].completed.returncode, 0)
        self.assertEqual(second_smoke.calls[0].filesystem_mutations, ())

        trust_a = first.staged.deployment_value["trust_context"]
        trust_b = staged_b.deployment_value["trust_context"]
        self.assertEqual(trust_a, oracle_a.trust_context_binding)
        self.assertEqual(trust_b, oracle_b.trust_context_binding)
        for receipt, oracle in (
            (first.staged.deployment_value, oracle_a),
            (staged_b.deployment_value, oracle_b),
        ):
            external = next(
                provider
                for provider in receipt["providers"]
                if not provider["intrinsic"]
            )
            self.assertEqual(
                thaw_json(external),
                oracle.provider_receipt_projection,
            )
        live_receipt = json.loads(
            first.canonical_root.joinpath("deployment.json").read_bytes()
        )
        self.assertEqual(live_receipt["trust_context"], trust_b)
        self.assertIn(
            {**trust_a, "state": "historical-usable"},
            live_receipt["historical_trust_contexts"],
        )
        bundle = external_provider_bundle(self.root, oracle_b.declaration_value)
        provider_cache = candidates.manager_cache / "provider-cache"
        shutil.rmtree(provider_cache)
        self.assertFalse(provider_cache.exists())
        relocated_manager_cache = self.root / "relocated-manager-cache-shadow"
        relocated_authority = regular_file_authority(candidates.manager_cache)
        self.assertTrue(relocated_authority)
        candidates.manager_cache.rename(relocated_manager_cache)
        self.assertFalse(candidates.manager_cache.exists())
        self.assertEqual(
            regular_file_authority(relocated_manager_cache),
            relocated_authority,
        )
        deleted_stage_roots = (
            self.fixture.first_install.root / "stage",
            self.root / "routine-stage-b",
        )
        for path in deleted_stage_roots:
            shutil.rmtree(path)
            self.assertFalse(path.exists())
        source_and_stage_roots = (
            candidates.manager_cache,
            relocated_manager_cache,
            *deleted_stage_roots,
        )

        installed_client = first.canonical_root / "client" / "task_witness_client.py"
        installed_launcher = (
            first.canonical_root / "launcher" / "task_witness_launch.py"
        )
        staged_client = next(
            item for item in first.staged.artifacts if item.role == "client"
        )
        staged_launcher = next(
            item for item in first.staged.artifacts if item.role == "launcher"
        )
        self.assertEqual(
            sha256(installed_client.read_bytes()), sha256(staged_client.raw)
        )
        self.assertEqual(
            sha256(installed_launcher.read_bytes()),
            sha256(staged_launcher.raw),
        )
        authority_before = regular_file_authority(first.canonical_root)

        active = invoke_installed_client(
            first.canonical_root,
            bundle,
            self.root / "active-client-support",
            forbidden_probe_roots=(
                candidates.manager_cache,
                relocated_manager_cache,
            ),
        )
        historical = invoke_installed_client(
            first.canonical_root,
            bundle,
            self.root / "historical-client-support",
            forbidden_probe_roots=(
                candidates.manager_cache,
                relocated_manager_cache,
            ),
            historical_trust_context_sha256=trust_a["sha256"],
        )

        for invocation in (active, historical):
            self.assertEqual(
                {attempt.role for attempt in invocation.failed_probe_attempts},
                {"client", "launcher-runtime"},
            )
            self.assertEqual(len(invocation.dir_fd_forbidden_probe_attempts), 4)
            self.assertEqual(
                {
                    (attempt.role, attempt.source)
                    for attempt in invocation.dir_fd_forbidden_probe_attempts
                },
                {("client", "os.open"), ("launcher-runtime", "os.open")},
            )
            expected_dir_fd_probes = {
                root / "dir-fd-failed-open-probe": Path(
                    root.name,
                    "dir-fd-failed-open-probe",
                )
                for root in (candidates.manager_cache, relocated_manager_cache)
            }
            for attempt in invocation.dir_fd_forbidden_probe_attempts:
                self.assertFalse(Path(attempt.raw).is_absolute())
                self.assertIn(attempt.lexical, expected_dir_fd_probes)
                self.assertEqual(
                    Path(attempt.raw),
                    expected_dir_fd_probes[attempt.lexical],
                )
                self.assertEqual(attempt.resolved, attempt.lexical)
                self.assertTrue(
                    any(
                        attempt.lexical.is_relative_to(root)
                        for root in (candidates.manager_cache, relocated_manager_cache)
                    )
                )
                self.assertTrue(
                    any(
                        attempt.resolved.is_relative_to(root)
                        for root in (candidates.manager_cache, relocated_manager_cache)
                    )
                )
            self.assertTrue(
                any(
                    attempt.role == "client"
                    and attempt.source == "audit"
                    and attempt.resolved == installed_client
                    for attempt in invocation.open_read_attempts
                )
            )
            self.assertTrue(
                any(
                    attempt.role == "launcher-runtime"
                    and attempt.source == "audit"
                    and attempt.resolved == installed_launcher
                    for attempt in invocation.open_read_attempts
                )
            )
            intentional_forbidden_probes = set(
                invocation.dir_fd_forbidden_probe_attempts
            )
            for attempt in invocation.open_read_attempts:
                if attempt in intentional_forbidden_probes:
                    continue
                for forbidden in source_and_stage_roots:
                    self.assertFalse(attempt.lexical.is_relative_to(forbidden))
                    self.assertFalse(attempt.resolved.is_relative_to(forbidden))

        self.assertEqual(
            active.completed.returncode,
            0,
            active.completed.stderr.decode(),
        )
        self.assertEqual(active.completed.stderr, b"")
        self.assertEqual(
            historical.completed.returncode,
            0,
            historical.completed.stderr.decode(),
        )
        self.assertEqual(historical.completed.stderr, b"")
        active_envelope = json.loads(active.completed.stdout)
        historical_envelope = json.loads(historical.completed.stdout)
        for envelope, oracle, historical_mode in (
            (active_envelope, oracle_b, False),
            (historical_envelope, oracle_a, True),
        ):
            with self.subTest(historical=historical_mode):
                trust_path = oracle.trust_context_path
                self.assertTrue(trust_path.is_relative_to(first.canonical_root))
                self.assertEqual(
                    trust_path.read_bytes(),
                    oracle.trust_context_raw,
                )
                self.assertEqual(
                    envelope["contract"],
                    "task-witness-launch-envelope-v1",
                )
                self.assertEqual(
                    envelope["anchor"]["trust_context_sha256"],
                    oracle.trust_context_sha256,
                )
                self.assertIs(envelope["anchor"]["historical"], historical_mode)
                self.assertEqual(
                    envelope["witness"]["contract"],
                    "task-witness-canonical-projection-v2",
                )
                self.assertEqual(
                    envelope["witness"]["producer"]["producer_id"],
                    "demo-producer",
                )
                self.assertEqual(
                    envelope["witness"]["validator"]["validator_id"],
                    "demo-validator",
                )
                self.assertEqual(
                    envelope["witness"]["validator"]["implementation_sha256"],
                    oracle.validator_implementation_sha256,
                )
                self.assertEqual(
                    envelope["witness"]["projection"],
                    oracle.projection,
                )
                self.assertEqual(
                    envelope["witness"]["trust_context_sha256"],
                    oracle.trust_context_sha256,
                )
                self.assertIs(envelope["witness"]["historical"], historical_mode)

        self.assertNotEqual(
            active_envelope["witness"]["validator"]["implementation_sha256"],
            historical_envelope["witness"]["validator"]["implementation_sha256"],
        )
        self.assertEqual(
            regular_file_authority(first.canonical_root),
            authority_before,
        )
        for oracle in (oracle_a, oracle_b):
            self.assertEqual(
                oracle.trust_context_path.read_bytes(),
                oracle.trust_context_raw,
            )
            self.assertEqual(
                sha256(oracle.trust_context_path.read_bytes()),
                oracle.trust_context_sha256,
            )
            for module in oracle.modules:
                self.assertTrue(module.installed_path.is_file())
                self.assertEqual(module.installed_path.read_bytes(), module.raw)
                self.assertEqual(module.installed_path.stat().st_size, module.length)
                self.assertEqual(sha256(module.raw), module.sha256)
        for path in (candidates.manager_cache, *deleted_stage_roots):
            self.assertFalse(path.exists())
        self.assertEqual(
            regular_file_authority(relocated_manager_cache),
            relocated_authority,
        )


if __name__ == "__main__":
    unittest.main()
