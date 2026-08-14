from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from ._activation_support import (
    canonical_value,
    filesystem_identity,
    process_descriptor_inventory,
    thaw_json,
)
from ._routine_activation_support import (
    JOURNAL_KEYS,
    ROUTINE_ADDITIVE_PROCESS_LOSS_CUTS,
    ROUTINE_CLEANUP_PROCESS_LOSS_CUTS,
    ROUTINE_JOURNAL_PROCESS_LOSS_CUTS,
    ROUTINE_SELECTOR_PROCESS_LOSS_CUTS,
    ROUTINE_SMOKE_PROCESS_LOSS_CUTS,
    PublicRoutineSmokeBoundary,
    SmokeObservation,
    assert_existing_files_are_immutable,
    assert_no_transaction_residue,
    assert_routine_additive_process_loss_state,
    assert_routine_selector_process_loss_state,
    assert_selector_has_distinct_retained_copy,
    assert_smoke_observation,
    assert_staged_additions_installed,
    directory_snapshot,
    exact_live_journal,
    expected_active_receipt_inventory,
    raw_receipt_inventory,
    receipt_digest_inventory,
    regular_file_snapshot,
    routine_additive_artifacts,
    routine_cleanup_steps,
    routine_install_temporary_path,
    routine_selector_temporary_path,
    run_routine_additive_process_loss_cut,
    run_routine_cleanup_process_loss_cut,
    run_routine_journal_process_loss_cut,
    run_routine_selector_process_loss_after_replace,
    run_routine_selector_process_loss_cut,
    run_routine_smoke_process_loss_cut,
    run_routine_terminal_process_loss_cut,
    selector_raws,
    staged_artifact,
    staged_candidate_selector_raws,
)
from ._routine_support import RoutineDeploymentFixture
from ._support import canonical_bytes, canonical_document, sha256


def _reseal_routine_journal(value: dict[str, object]) -> bytes:
    unsigned = {key: item for key, item in value.items() if key != "content_sha256"}
    value["content_sha256"] = sha256(canonical_bytes(unsigned))
    return canonical_document(value)


class RoutineActivationRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.fixture = RoutineDeploymentFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def assert_public_recovery_rejects_without_mutation(
        self,
        *,
        fixture: RoutineDeploymentFixture,
        deployment: object,
        initial: object,
        staged: object,
        activation: object,
        journal_raw: bytes,
    ) -> None:
        root = initial.canonical_root
        stage_snapshot = fixture.stage_snapshot(staged.stage_path)
        lock_identity = filesystem_identity(initial.activation_lock)
        root_identity = filesystem_identity(root)[:6]
        descriptors = process_descriptor_inventory()
        before_recovery = regular_file_snapshot(root)
        selectors_before = selector_raws(root)
        receipts_before = raw_receipt_inventory(root)
        forbidden_smoke = mock.Mock(
            side_effect=AssertionError("contradictory recovery reached smoke")
        )

        with (
            mock.patch.object(
                deployment,
                "_spawn_activation_smoke_child",
                forbidden_smoke,
            ),
            self.assertRaises(deployment.DeploymentError),
        ):
            deployment.recover_transaction(
                deployment.RecoveryRequest(
                    activation=activation,
                    expected_journal_raw=journal_raw,
                )
            )

        forbidden_smoke.assert_not_called()
        self.assertEqual(exact_live_journal(root), journal_raw)
        self.assertEqual(regular_file_snapshot(root), before_recovery)
        self.assertEqual(selector_raws(root), selectors_before)
        self.assertEqual(raw_receipt_inventory(root), receipts_before)
        self.assertEqual(fixture.stage_snapshot(staged.stage_path), stage_snapshot)
        self.assertEqual(filesystem_identity(root)[:6], root_identity)
        self.assertEqual(filesystem_identity(initial.activation_lock), lock_identity)
        self.assertEqual(process_descriptor_inventory(), descriptors)

    def test_smoke_observer_rejects_rebound_b_transaction_authority(self) -> None:
        _, _, _, _, staged_b, _ = self.fixture.staged_routine()
        root = staged_b.plan.precondition.canonical_root
        selector_a = selector_raws(root)
        selector_b = staged_candidate_selector_raws(staged_b)
        expected_receipts = expected_active_receipt_inventory(selector_a[1], staged_b)
        receipt_b = sha256(staged_b.deployment_raw)
        rollback_b = sha256(staged_b.rollback_raw)
        candidate_smoke = thaw_json(staged_b.deployment_value["smoke"])
        baseline = {
            "candidate": {
                "state": "active",
                "deployment_receipt": thaw_json(
                    staged_artifact(staged_b, "deployment-alias").installed
                ),
                "active_record": thaw_json(
                    staged_artifact(staged_b, "active-record").installed
                ),
                "control_set": thaw_json(staged_b.deployment_value["control_set"]),
                "smoke": candidate_smoke,
            },
            "prior": thaw_json(staged_b.rollback_value["prior_activation_unit"]),
            "rollback_authority": {
                "receipt_path": staged_b.stage_value["rollback_receipt"]["path"],
                "receipt_sha256": rollback_b,
                "target_state": "active",
            },
            "preimage": {
                "manifest_path": staged_b.stage_value["rollback_receipt"]["path"],
                "manifest_sha256": rollback_b,
                "artifacts": thaw_json(staged_b.rollback_value["selector_preimage"]),
                "external_dependencies": thaw_json(
                    staged_b.rollback_value["external_dependencies"]
                ),
            },
            "stage": {
                "receipt_path": str(staged_b.stage_path),
                "receipt_sha256": sha256(staged_b.stage_raw),
                "plan_sha256": staged_b.stage_value["plan_sha256"],
                "authorization_sha256": staged_b.stage_value["authorization"]["sha256"],
                "maintenance_transaction_sha256": staged_b.stage_value[
                    "maintenance_transaction_sha256"
                ],
            },
            "outer_maintenance_transaction_sha256": staged_b.stage_value[
                "maintenance_transaction_sha256"
            ],
            "activation_lock": thaw_json(staged_b.rollback_value["activation_lock"]),
            "smoke_handoff": {
                "target_deployment_receipt_sha256": receipt_b,
                "smoke_bundle_sha256": candidate_smoke["bundle"]["sha256"],
                "smoke_trust_context_sha256": candidate_smoke["trust_context"][
                    "sha256"
                ],
            },
        }

        def observation(journal: dict[str, object]) -> SmokeObservation:
            return SmokeObservation(
                phase="candidate-smoke",
                journal_raw=b"independent characterization only",
                journal=journal,
                active_raw=selector_b[0],
                deployment_raw=selector_b[1],
                receipt_digests=expected_receipts,
            )

        assert_smoke_observation(
            observation(copy.deepcopy(baseline)),
            staged=staged_b,
            phase="candidate-smoke",
            live_selectors=selector_b,
            expected_receipt_digests=expected_receipts,
        )

        mutations = {
            "candidate-unit": lambda value: value["candidate"].update(
                {"deployment_receipt": value["prior"]["deployment_receipt"]}
            ),
            "deeper-prior": lambda value: value["prior"]["deployment_receipt"].update(
                {"sha256": "1" * 64}
            ),
            "rollback-authority": lambda value: value["rollback_authority"].update(
                {"receipt_sha256": "2" * 64}
            ),
            "preimage-manifest": lambda value: value["preimage"].update(
                {"manifest_sha256": "3" * 64}
            ),
            "preimage-selectors": lambda value: value["preimage"].update(
                {"artifacts": []}
            ),
            "preimage-dependencies": lambda value: value["preimage"].update(
                {"external_dependencies": {}}
            ),
            "stage": lambda value: value["stage"].update({"receipt_sha256": "4" * 64}),
            "outer-authority": lambda value: value.update(
                {"outer_maintenance_transaction_sha256": "5" * 64}
            ),
            "activation-lock": lambda value: value["activation_lock"].update(
                {"inode": value["activation_lock"]["inode"] + 1}
            ),
            "target-swap": lambda value: value["smoke_handoff"].update(
                {
                    "target_deployment_receipt_sha256": staged_b.plan.precondition.receipt_sha256
                }
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                changed = copy.deepcopy(baseline)
                mutate(changed)
                with self.assertRaises(AssertionError):
                    assert_smoke_observation(
                        observation(changed),
                        staged=staged_b,
                        phase="candidate-smoke",
                        live_selectors=selector_b,
                        expected_receipt_digests=expected_receipts,
                    )

    def test_public_routine_activation_commits_b_as_one_additive_unit(self) -> None:
        deployment = self.fixture.deployment()
        initial, _, _, _, staged_b, activation_b = self.fixture.staged_routine()
        root = initial.canonical_root
        selector_a = selector_raws(root)
        expected_receipts = expected_active_receipt_inventory(selector_a[1], staged_b)
        prior_files = regular_file_snapshot(root)
        stage_snapshot = self.fixture.stage_snapshot(staged_b.stage_path)
        lock_identity = filesystem_identity(initial.activation_lock)
        descriptors = process_descriptor_inventory()
        smoke = PublicRoutineSmokeBoundary(
            root,
            staged_b,
            expected_receipt_digests=expected_receipts,
            candidate_accepted=True,
            rollback_accepted=True,
        )

        with mock.patch.object(deployment, "_spawn_activation_smoke_child", smoke):
            result = deployment.activate_staged(activation_b)

        receipt_b = sha256(staged_b.deployment_raw)
        selector_b = staged_candidate_selector_raws(staged_b)
        self.assertEqual(result.outcome, "candidate-active")
        self.assertEqual(result.candidate_receipt_sha256, receipt_b)
        self.assertEqual(result.active_receipt_sha256, receipt_b)
        self.assertEqual(
            result.accepted_envelope_sha256,
            sha256(smoke.outputs["candidate-smoke"]),
        )
        self.assertEqual(frozenset(result.journal_value), JOURNAL_KEYS)
        self.assertEqual(result.journal_value["phase"], "terminal")
        self.assertEqual(
            result.journal_value["terminal_result"]["outcome"], "candidate-active"
        )
        self.assertEqual(smoke.phases, ["candidate-smoke"])
        assert_smoke_observation(
            smoke.observations[0],
            staged=staged_b,
            phase="candidate-smoke",
            live_selectors=selector_b,
            expected_receipt_digests=expected_receipts,
        )
        self.assertEqual(selector_raws(root), selector_b)
        self.assertNotEqual(selector_a, selector_b)
        self.assertEqual(
            receipt_digest_inventory(root),
            expected_receipts,
        )
        assert_selector_has_distinct_retained_copy(root, staged_b.deployment_raw)
        assert_staged_additions_installed(staged_b)
        assert_existing_files_are_immutable(
            prior_files,
            root,
            changed=frozenset({"active.json", "deployment.json"}),
        )
        assert_no_transaction_residue(root)
        self.assertEqual(
            self.fixture.stage_snapshot(staged_b.stage_path), stage_snapshot
        )
        self.assertEqual(filesystem_identity(initial.activation_lock), lock_identity)
        self.assertEqual(process_descriptor_inventory(), descriptors)

    def test_public_recovery_uses_stage_after_candidate_source_disappears(self) -> None:
        deployment = self.fixture.deployment()
        initial, _, request_b, _, staged_b, activation_b = self.fixture.staged_routine()
        root = initial.canonical_root
        selector_a = selector_raws(root)
        selector_b = staged_candidate_selector_raws(staged_b)
        expected_receipts = expected_active_receipt_inventory(selector_a[1], staged_b)
        prior_files = regular_file_snapshot(root)
        stage_snapshot = self.fixture.stage_snapshot(staged_b.stage_path)
        candidate_snapshot = regular_file_snapshot(request_b.candidate_root)
        lock_identity = filesystem_identity(initial.activation_lock)
        descriptors = process_descriptor_inventory()

        run_routine_selector_process_loss_after_replace(
            deployment,
            activation_b,
            direction="candidate",
            selector_index=0,
        )

        journal_raw = exact_live_journal(root)
        journal = canonical_value(journal_raw)
        self.assertEqual(journal["phase"], "selector-switching")
        self.assertEqual(
            journal["pending_step"],
            {
                "operation": "replace-selector",
                "index": 0,
                "role": "active-record",
            },
        )
        self.assertEqual(selector_raws(root), (selector_b[0], selector_a[1]))
        self.assertEqual(receipt_digest_inventory(root), expected_receipts)

        detached_candidate = request_b.candidate_root.with_name(
            f"{request_b.candidate_root.name}-detached"
        )
        request_b.candidate_root.rename(detached_candidate)
        source_inputs = (
            request_b.source_selection_raw,
            request_b.source_evidence,
            request_b.runtime_qualification_raw,
        )
        smoke = PublicRoutineSmokeBoundary(
            root,
            staged_b,
            expected_receipt_digests=expected_receipts,
            candidate_accepted=True,
            rollback_accepted=True,
        )
        snapshot_paths: list[Path] = []
        real_snapshot = deployment._snapshot_candidate_tree

        def observe_snapshot(path: Path) -> object:
            resolved = path.resolve()
            snapshot_paths.append(resolved)
            if resolved == request_b.candidate_root:
                raise AssertionError("recovery reread the unavailable candidate source")
            return real_snapshot(path)

        with (
            mock.patch.object(
                deployment,
                "_snapshot_candidate_tree",
                side_effect=observe_snapshot,
            ),
            mock.patch.object(deployment, "_bind_candidate_source") as bind_source,
            mock.patch.object(deployment, "_spawn_activation_smoke_child", smoke),
        ):
            result = deployment.recover_transaction(
                deployment.RecoveryRequest(
                    activation=activation_b,
                    expected_journal_raw=journal_raw,
                )
            )

        self.assertNotIn(request_b.candidate_root, snapshot_paths)
        self.assertIn(staged_b.stage_path.parent, snapshot_paths)
        bind_source.assert_not_called()
        self.assertEqual(result.outcome, "candidate-active")
        self.assertEqual(result.active_receipt_sha256, sha256(staged_b.deployment_raw))
        self.assertEqual(smoke.phases, ["candidate-smoke"])
        self.assertEqual(selector_raws(root), selector_b)
        self.assertEqual(receipt_digest_inventory(root), expected_receipts)
        assert_selector_has_distinct_retained_copy(root, staged_b.deployment_raw)
        assert_staged_additions_installed(staged_b)
        assert_existing_files_are_immutable(
            prior_files,
            root,
            changed=frozenset({"active.json", "deployment.json"}),
        )
        assert_no_transaction_residue(root)
        self.assertFalse(request_b.candidate_root.exists())
        self.assertEqual(regular_file_snapshot(detached_candidate), candidate_snapshot)
        self.assertEqual(
            (
                request_b.source_selection_raw,
                request_b.source_evidence,
                request_b.runtime_qualification_raw,
            ),
            source_inputs,
        )
        self.assertEqual(
            self.fixture.stage_snapshot(staged_b.stage_path), stage_snapshot
        )
        self.assertEqual(filesystem_identity(initial.activation_lock), lock_identity)
        self.assertEqual(process_descriptor_inventory(), descriptors)

    def test_public_recovery_converges_across_selector_persistence_cuts(self) -> None:
        cases = tuple(
            (direction, selector_index, cut)
            for direction in ("candidate", "prior")
            for selector_index in (0, 1)
            for cut in ROUTINE_SELECTOR_PROCESS_LOSS_CUTS
        )
        for direction, selector_index, cut in cases:
            with (
                self.subTest(
                    direction=direction,
                    selector_index=selector_index,
                    cut=cut,
                ),
                tempfile.TemporaryDirectory() as directory,
            ):
                fixture = RoutineDeploymentFixture(Path(directory).resolve())
                deployment = fixture.deployment()
                initial, active_a, _, _, staged_b, activation_b = (
                    fixture.staged_routine()
                )
                root = initial.canonical_root
                selector_a = selector_raws(root)
                selector_b = staged_candidate_selector_raws(staged_b)
                expected_receipts = expected_active_receipt_inventory(
                    selector_a[1], staged_b
                )
                prior_files = regular_file_snapshot(root)
                prior_directories = directory_snapshot(root)
                stage_snapshot = fixture.stage_snapshot(staged_b.stage_path)
                root_identity = filesystem_identity(root)[:6]
                lock_identity = filesystem_identity(initial.activation_lock)
                descriptors = process_descriptor_inventory()

                run_routine_selector_process_loss_cut(
                    deployment,
                    activation_b,
                    direction=direction,
                    selector_index=selector_index,
                    cut=cut,
                )

                journal_raw = exact_live_journal(root)
                journal = canonical_value(journal_raw)
                assert_routine_selector_process_loss_state(
                    root,
                    staged_b,
                    selector_a,
                    journal,
                    direction=direction,
                    selector_index=selector_index,
                    cut=cut,
                )
                self.assertEqual(receipt_digest_inventory(root), expected_receipts)
                self.assertEqual(
                    fixture.stage_snapshot(staged_b.stage_path), stage_snapshot
                )
                before_recovery = regular_file_snapshot(root)
                smoke = PublicRoutineSmokeBoundary(
                    root,
                    staged_b,
                    expected_receipt_digests=expected_receipts,
                    candidate_accepted=direction == "candidate",
                    rollback_accepted=True,
                )
                try:
                    with mock.patch.object(
                        deployment,
                        "_spawn_activation_smoke_child",
                        smoke,
                    ):
                        result = deployment.recover_transaction(
                            deployment.RecoveryRequest(
                                activation=activation_b,
                                expected_journal_raw=journal_raw,
                            )
                        )
                except deployment.DeploymentError:
                    self.assertEqual(exact_live_journal(root), journal_raw)
                    self.assertEqual(regular_file_snapshot(root), before_recovery)
                    self.assertEqual(receipt_digest_inventory(root), expected_receipts)
                    self.assertEqual(
                        fixture.stage_snapshot(staged_b.stage_path),
                        stage_snapshot,
                    )
                    raise

                if direction == "candidate":
                    self.assertEqual(result.outcome, "candidate-active")
                    self.assertEqual(smoke.phases, ["candidate-smoke"])
                    self.assertEqual(selector_raws(root), selector_b)
                    self.assertEqual(receipt_digest_inventory(root), expected_receipts)
                    assert_selector_has_distinct_retained_copy(
                        root, staged_b.deployment_raw
                    )
                    assert_staged_additions_installed(staged_b)
                else:
                    self.assertEqual(result.outcome, "restored-prior")
                    self.assertEqual(smoke.phases, ["rollback-smoke"])
                    self.assertEqual(selector_raws(root), selector_a)
                    self.assertEqual(
                        receipt_digest_inventory(root),
                        frozenset(
                            {
                                active_a.active_receipt_sha256,
                                staged_b.plan.precondition.receipt_value["rollback"][
                                    "sha256"
                                ],
                            }
                        ),
                    )
                    assert_selector_has_distinct_retained_copy(root, selector_a[1])
                    for artifact in routine_additive_artifacts(staged_b):
                        if artifact.relative_path in prior_files:
                            self.assertEqual(
                                regular_file_snapshot(root)[artifact.relative_path],
                                prior_files[artifact.relative_path],
                            )
                        else:
                            self.assertFalse(Path(artifact.installed_path).exists())
                            parent = Path(artifact.relative_path).parent
                            while parent != Path("."):
                                if parent.as_posix() not in prior_directories:
                                    self.assertFalse((root / parent).exists())
                                parent = parent.parent
                assert_existing_files_are_immutable(
                    prior_files,
                    root,
                    changed=frozenset({"active.json", "deployment.json"}),
                )
                assert_no_transaction_residue(root)
                self.assertEqual(
                    fixture.stage_snapshot(staged_b.stage_path), stage_snapshot
                )
                self.assertEqual(filesystem_identity(root)[:6], root_identity)
                self.assertEqual(
                    filesystem_identity(initial.activation_lock), lock_identity
                )
                self.assertEqual(process_descriptor_inventory(), descriptors)

    def test_public_recovery_rejects_selector_temporary_contradictions(self) -> None:
        cases = (
            ("misnamed", "replace"),
            ("wrong-direction", "replace"),
            ("wrong-index", "replace"),
            ("wrong-transaction", "replace"),
            ("invalid-prefix", "temp-create"),
            ("full-wrong-target", "temp-create"),
            ("extra-temp", "temp-create"),
            ("final-temp-contradiction", "replace"),
            ("symlink", "temp-create"),
            ("fifo", "temp-create"),
            ("wrong-mode", "temp-create"),
            ("multiple-links", "temp-create"),
        )
        for mutation, base_cut in cases:
            with self.subTest(mutation=mutation):  # noqa: SIM117
                with tempfile.TemporaryDirectory() as directory:
                    fixture = RoutineDeploymentFixture(Path(directory).resolve())
                    deployment = fixture.deployment()
                    initial, _, _, _, staged_b, activation_b = fixture.staged_routine()
                    root = initial.canonical_root
                    selector_a = selector_raws(root)
                    selector_b = staged_candidate_selector_raws(staged_b)
                    stage_snapshot = fixture.stage_snapshot(staged_b.stage_path)
                    lock_identity = filesystem_identity(initial.activation_lock)
                    descriptors = process_descriptor_inventory()

                    run_routine_selector_process_loss_cut(
                        deployment,
                        activation_b,
                        direction="candidate",
                        selector_index=0,
                        cut=base_cut,
                    )
                    journal_raw = exact_live_journal(root)
                    journal = canonical_value(journal_raw)
                    assert_routine_selector_process_loss_state(
                        root,
                        staged_b,
                        selector_a,
                        journal,
                        direction="candidate",
                        selector_index=0,
                        cut=base_cut,
                    )
                    exact = routine_selector_temporary_path(
                        root,
                        journal["transaction_id"],
                        "candidate",
                        0,
                    )

                    if mutation == "misnamed":
                        changed = root / (
                            ".task-witness-selector-"
                            f"{journal['transaction_id']}-candidate-x.tmp"
                        )
                        changed.write_bytes(selector_b[0])
                    elif mutation == "wrong-direction":
                        changed = routine_selector_temporary_path(
                            root, journal["transaction_id"], "prior", 0
                        )
                        changed.write_bytes(selector_a[0])
                    elif mutation == "wrong-index":
                        changed = routine_selector_temporary_path(
                            root, journal["transaction_id"], "candidate", 1
                        )
                        changed.write_bytes(selector_b[1])
                    elif mutation == "wrong-transaction":
                        changed = routine_selector_temporary_path(
                            root, "0" * 64, "candidate", 0
                        )
                        changed.write_bytes(selector_b[0])
                    elif mutation == "invalid-prefix":
                        changed = exact
                        changed.chmod(0o600)
                        changed.write_bytes(b"not a candidate selector prefix")
                    elif mutation == "full-wrong-target":
                        changed = exact
                        changed.chmod(0o600)
                        changed.write_bytes(selector_a[0])
                    elif mutation == "extra-temp":
                        changed = routine_selector_temporary_path(
                            root, journal["transaction_id"], "candidate", 1
                        )
                        changed.write_bytes(selector_b[1])
                    elif mutation == "final-temp-contradiction":
                        changed = exact
                        changed.write_bytes(selector_b[0])
                    elif mutation == "symlink":
                        changed = exact
                        changed.unlink()
                        changed.symlink_to(root / "active.json")
                    elif mutation == "fifo":
                        changed = exact
                        changed.unlink()
                        os.mkfifo(changed, 0o600)
                    elif mutation == "wrong-mode":
                        changed = exact
                        changed.chmod(0o644)
                    else:
                        changed = exact
                        changed.chmod(0o600)
                        changed.write_bytes(selector_b[0])
                        os.link(changed, root / f"{changed.name}.peer")

                    if not changed.is_symlink():
                        changed.chmod(0o600 if mutation != "wrong-mode" else 0o644)
                    changed_identity = filesystem_identity(changed)
                    before_recovery = regular_file_snapshot(root)
                    selectors_before = selector_raws(root)
                    receipts_before = receipt_digest_inventory(root)
                    with (
                        mock.patch.object(
                            deployment,
                            "_spawn_activation_smoke_child",
                            side_effect=AssertionError(
                                "contradictory selector state reached smoke"
                            ),
                        ) as smoke,
                        self.assertRaises(deployment.DeploymentError),
                    ):
                        deployment.recover_transaction(
                            deployment.RecoveryRequest(
                                activation=activation_b,
                                expected_journal_raw=journal_raw,
                            )
                        )

                    smoke.assert_not_called()
                    self.assertEqual(exact_live_journal(root), journal_raw)
                    self.assertEqual(regular_file_snapshot(root), before_recovery)
                    self.assertEqual(filesystem_identity(changed), changed_identity)
                    self.assertEqual(selector_raws(root), selectors_before)
                    self.assertEqual(receipt_digest_inventory(root), receipts_before)
                    self.assertEqual(
                        fixture.stage_snapshot(staged_b.stage_path), stage_snapshot
                    )
                    self.assertEqual(
                        filesystem_identity(initial.activation_lock), lock_identity
                    )
                    self.assertEqual(process_descriptor_inventory(), descriptors)

    def test_public_recovery_rejects_stale_or_reversed_live_selectors(self) -> None:
        cases = (
            ("candidate", 0, ("A", "B")),
            ("candidate", 0, ("B", "B")),
            ("candidate", 1, ("A", "A")),
            ("candidate", 1, ("A", "B")),
            ("prior", 0, ("B", "A")),
            ("prior", 0, ("A", "A")),
            ("prior", 1, ("B", "B")),
            ("prior", 1, ("B", "A")),
        )
        for direction, selector_index, state in cases:
            with self.subTest(  # noqa: SIM117
                direction=direction,
                selector_index=selector_index,
                state=state,
            ):
                with tempfile.TemporaryDirectory() as directory:
                    fixture = RoutineDeploymentFixture(Path(directory).resolve())
                    deployment = fixture.deployment()
                    initial, _, _, _, staged_b, activation_b = fixture.staged_routine()
                    root = initial.canonical_root
                    selector_a = selector_raws(root)
                    selector_b = staged_candidate_selector_raws(staged_b)

                    run_routine_selector_process_loss_cut(
                        deployment,
                        activation_b,
                        direction=direction,
                        selector_index=selector_index,
                        cut="temp-create",
                    )
                    journal_raw = exact_live_journal(root)
                    journal = canonical_value(journal_raw)
                    assert_routine_selector_process_loss_state(
                        root,
                        staged_b,
                        selector_a,
                        journal,
                        direction=direction,
                        selector_index=selector_index,
                        cut="temp-create",
                    )
                    options = {"A": selector_a, "B": selector_b}
                    active_raw = options[state[0]][0]
                    deployment_raw = options[state[1]][1]
                    (root / "active.json").write_bytes(active_raw)
                    (root / "active.json").chmod(0o600)
                    (root / "deployment.json").write_bytes(deployment_raw)
                    (root / "deployment.json").chmod(0o600)

                    self.assert_public_recovery_rejects_without_mutation(
                        fixture=fixture,
                        deployment=deployment,
                        initial=initial,
                        staged=staged_b,
                        activation=activation_b,
                        journal_raw=journal_raw,
                    )

    def test_public_recovery_converges_across_additive_persistence_cuts(self) -> None:
        _, _, _, _, probe_staged, _ = self.fixture.staged_routine()
        probe_artifacts = routine_additive_artifacts(probe_staged)
        new_artifacts = tuple(
            (index, artifact.role)
            for index, artifact in enumerate(probe_artifacts)
            if not Path(artifact.installed_path).exists()
        )
        self.assertEqual(
            new_artifacts[:4],
            (
                (0, "runtime-bundle-io"),
                (1, "runtime-canonical"),
                (2, "runtime-entrypoint"),
                (3, "runtime-trust"),
            ),
        )
        self.assertEqual(
            tuple(index for index, _ in new_artifacts),
            tuple(range(6)),
        )
        self.assertEqual(
            {probe_artifacts[4].role, probe_artifacts[5].role},
            {"rollback-receipt", "deployment-receipt"},
        )
        cases = tuple(
            (artifact_index, cut)
            for artifact_index, _ in new_artifacts
            for cut in ROUTINE_ADDITIVE_PROCESS_LOSS_CUTS
        )
        for artifact_index, cut in cases:
            with self.subTest(  # noqa: SIM117
                artifact_index=artifact_index,
                cut=cut,
            ):
                with tempfile.TemporaryDirectory() as directory:
                    fixture = RoutineDeploymentFixture(Path(directory).resolve())
                    deployment = fixture.deployment()
                    initial, _, _, _, staged_b, activation_b = fixture.staged_routine()
                    root = initial.canonical_root
                    selector_a = selector_raws(root)
                    selector_b = staged_candidate_selector_raws(staged_b)
                    artifacts = routine_additive_artifacts(staged_b)
                    expected_receipts = expected_active_receipt_inventory(
                        selector_a[1], staged_b
                    )
                    prior_receipts = receipt_digest_inventory(root)
                    prior_files = regular_file_snapshot(root)
                    stage_snapshot = fixture.stage_snapshot(staged_b.stage_path)
                    root_identity = filesystem_identity(root)[:6]
                    lock_identity = filesystem_identity(initial.activation_lock)
                    descriptors = process_descriptor_inventory()

                    run_routine_additive_process_loss_cut(
                        deployment,
                        activation_b,
                        artifact_index=artifact_index,
                        cut=cut,
                    )

                    journal_raw = exact_live_journal(root)
                    journal = canonical_value(journal_raw)
                    assert_routine_additive_process_loss_state(
                        artifacts,
                        prior_files,
                        journal,
                        artifact_index=artifact_index,
                        cut=cut,
                    )
                    self.assertEqual(selector_raws(root), selector_a)
                    crash_receipts = set(prior_receipts)
                    for index, artifact in enumerate(artifacts):
                        if artifact.role not in {
                            "rollback-receipt",
                            "deployment-receipt",
                        }:
                            continue
                        if index < artifact_index or (
                            index == artifact_index
                            and cut in {"publish", "temp-unlink", "parent-fsync"}
                        ):
                            crash_receipts.add(sha256(artifact.raw))
                    crash_temporary = routine_install_temporary_path(
                        artifacts[artifact_index],
                        journal["transaction_id"],
                        artifact_index,
                    )
                    receipt_temporary = (
                        crash_temporary
                        if crash_temporary.parent == root / "receipts"
                        and crash_temporary.exists()
                        else None
                    )
                    allowed_final_links = (
                        frozenset({1, 2})
                        if artifacts[artifact_index].role
                        in {"rollback-receipt", "deployment-receipt"}
                        and cut == "publish"
                        else frozenset({1})
                    )
                    self.assertEqual(
                        receipt_digest_inventory(
                            root,
                            allowed_temporary=receipt_temporary,
                            allowed_final_links=allowed_final_links,
                        ),
                        frozenset(crash_receipts),
                    )
                    self.assertEqual(
                        fixture.stage_snapshot(staged_b.stage_path), stage_snapshot
                    )
                    before_recovery = regular_file_snapshot(root)
                    smoke = PublicRoutineSmokeBoundary(
                        root,
                        staged_b,
                        expected_receipt_digests=expected_receipts,
                        candidate_accepted=True,
                        rollback_accepted=True,
                    )
                    try:
                        with mock.patch.object(
                            deployment,
                            "_spawn_activation_smoke_child",
                            smoke,
                        ):
                            result = deployment.recover_transaction(
                                deployment.RecoveryRequest(
                                    activation=activation_b,
                                    expected_journal_raw=journal_raw,
                                )
                            )
                    except deployment.DeploymentError:
                        self.assertEqual(exact_live_journal(root), journal_raw)
                        self.assertEqual(regular_file_snapshot(root), before_recovery)
                        self.assertEqual(
                            receipt_digest_inventory(
                                root,
                                allowed_temporary=receipt_temporary,
                                allowed_final_links=allowed_final_links,
                            ),
                            frozenset(crash_receipts),
                        )
                        self.assertEqual(
                            fixture.stage_snapshot(staged_b.stage_path),
                            stage_snapshot,
                        )
                        raise

                    self.assertEqual(result.outcome, "candidate-active")
                    self.assertEqual(smoke.phases, ["candidate-smoke"])
                    self.assertEqual(selector_raws(root), selector_b)
                    self.assertEqual(receipt_digest_inventory(root), expected_receipts)
                    assert_selector_has_distinct_retained_copy(
                        root, staged_b.deployment_raw
                    )
                    assert_staged_additions_installed(staged_b)
                    assert_existing_files_are_immutable(
                        prior_files,
                        root,
                        changed=frozenset({"active.json", "deployment.json"}),
                    )
                    assert_no_transaction_residue(root)
                    self.assertEqual(
                        fixture.stage_snapshot(staged_b.stage_path), stage_snapshot
                    )
                    self.assertEqual(filesystem_identity(root)[:6], root_identity)
                    self.assertEqual(
                        filesystem_identity(initial.activation_lock), lock_identity
                    )
                    self.assertEqual(process_descriptor_inventory(), descriptors)

    def test_public_recovery_converges_across_journal_generation_cuts(self) -> None:
        generations = (
            "frozen",
            "candidate-acceptance",
            "prior-restoring",
            "rollback-acceptance",
        )
        cases = tuple(
            (generation, cut)
            for generation in generations
            for cut in ROUTINE_JOURNAL_PROCESS_LOSS_CUTS
        )
        for generation, cut in cases:
            with self.subTest(generation=generation, cut=cut):  # noqa: SIM117
                with tempfile.TemporaryDirectory() as directory:
                    fixture = RoutineDeploymentFixture(Path(directory).resolve())
                    deployment = fixture.deployment()
                    initial, active_a, _, _, staged_b, activation_b = (
                        fixture.staged_routine()
                    )
                    root = initial.canonical_root
                    selector_a = selector_raws(root)
                    selector_b = staged_candidate_selector_raws(staged_b)
                    expected_receipts = expected_active_receipt_inventory(
                        selector_a[1], staged_b
                    )
                    stage_snapshot = fixture.stage_snapshot(staged_b.stage_path)
                    lock_identity = filesystem_identity(initial.activation_lock)
                    descriptors = process_descriptor_inventory()

                    target_raw = run_routine_journal_process_loss_cut(
                        deployment,
                        activation_b,
                        staged_b,
                        generation=generation,
                        cut=cut,
                    )

                    target = canonical_value(target_raw)
                    journal_raw = exact_live_journal(root)
                    journal = canonical_value(journal_raw)
                    temporaries = tuple(root.glob("transaction.*.tmp"))
                    if cut in {"temp-create", "partial-write", "full-write"}:
                        self.assertEqual(target["sequence"], journal["sequence"] + 1)
                        self.assertEqual(
                            target["previous_journal_sha256"], sha256(journal_raw)
                        )
                        self.assertEqual(
                            tuple(path.name for path in temporaries),
                            (
                                f"transaction.{target['transaction_id']}.{target['sequence']}.tmp",
                            ),
                        )
                        temporary = temporaries[0]
                        metadata = temporary.lstat()
                        self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
                        self.assertEqual(metadata.st_uid, os.geteuid())
                        self.assertEqual(metadata.st_nlink, 1)
                        temporary_raw = temporary.read_bytes()
                        if cut == "temp-create":
                            self.assertEqual(temporary_raw, b"")
                        elif cut == "partial-write":
                            self.assertTrue(target_raw.startswith(temporary_raw))
                            self.assertGreater(len(temporary_raw), 0)
                            self.assertLess(len(temporary_raw), len(target_raw))
                        else:
                            self.assertEqual(temporary_raw, target_raw)
                    else:
                        self.assertEqual(temporaries, ())
                        self.assertEqual(journal_raw, target_raw)

                    if generation == "frozen":
                        self.assertEqual(selector_raws(root), selector_a)
                        self.assertEqual(
                            receipt_digest_inventory(root),
                            frozenset(
                                {
                                    active_a.active_receipt_sha256,
                                    staged_b.plan.precondition.receipt_value[
                                        "rollback"
                                    ]["sha256"],
                                }
                            ),
                        )
                    elif generation in {
                        "candidate-acceptance",
                        "prior-restoring",
                    }:
                        self.assertEqual(selector_raws(root), selector_b)
                        self.assertEqual(
                            receipt_digest_inventory(root), expected_receipts
                        )
                    else:
                        self.assertEqual(selector_raws(root), selector_a)
                        self.assertEqual(
                            receipt_digest_inventory(root), expected_receipts
                        )

                    prepublication = cut in {
                        "temp-create",
                        "partial-write",
                        "full-write",
                    }
                    rollback_flow = generation in {
                        "prior-restoring",
                        "rollback-acceptance",
                    }
                    smoke = PublicRoutineSmokeBoundary(
                        root,
                        staged_b,
                        expected_receipt_digests=expected_receipts,
                        candidate_accepted=not rollback_flow,
                        rollback_accepted=True,
                    )
                    before_recovery = regular_file_snapshot(root)
                    try:
                        with mock.patch.object(
                            deployment,
                            "_spawn_activation_smoke_child",
                            smoke,
                        ):
                            result = deployment.recover_transaction(
                                deployment.RecoveryRequest(
                                    activation=activation_b,
                                    expected_journal_raw=journal_raw,
                                )
                            )
                    except deployment.DeploymentError:
                        self.assertEqual(exact_live_journal(root), journal_raw)
                        self.assertEqual(regular_file_snapshot(root), before_recovery)
                        self.assertEqual(
                            fixture.stage_snapshot(staged_b.stage_path),
                            stage_snapshot,
                        )
                        raise

                    if generation == "frozen":
                        expected_smoke_phases = ["candidate-smoke"]
                    elif generation == "candidate-acceptance":
                        expected_smoke_phases = (
                            ["candidate-smoke"] if prepublication else []
                        )
                    elif generation == "prior-restoring":
                        expected_smoke_phases = (
                            ["candidate-smoke", "rollback-smoke"]
                            if prepublication
                            else ["rollback-smoke"]
                        )
                    else:
                        expected_smoke_phases = (
                            ["rollback-smoke"] if prepublication else []
                        )
                    self.assertEqual(smoke.phases, expected_smoke_phases)
                    if rollback_flow:
                        self.assertEqual(result.outcome, "restored-prior")
                        self.assertEqual(selector_raws(root), selector_a)
                        self.assertEqual(
                            receipt_digest_inventory(root),
                            frozenset(
                                {
                                    active_a.active_receipt_sha256,
                                    staged_b.plan.precondition.receipt_value[
                                        "rollback"
                                    ]["sha256"],
                                }
                            ),
                        )
                    else:
                        self.assertEqual(result.outcome, "candidate-active")
                        self.assertEqual(selector_raws(root), selector_b)
                        self.assertEqual(
                            receipt_digest_inventory(root), expected_receipts
                        )
                    assert_no_transaction_residue(root)
                    self.assertEqual(
                        fixture.stage_snapshot(staged_b.stage_path), stage_snapshot
                    )
                    self.assertEqual(
                        filesystem_identity(initial.activation_lock), lock_identity
                    )
                    self.assertEqual(process_descriptor_inventory(), descriptors)

    def test_public_recovery_replays_terminal_before_unlink(self) -> None:
        for outcome in ("candidate-active", "restored-prior", "recovery-required"):
            with self.subTest(outcome=outcome):  # noqa: SIM117
                with tempfile.TemporaryDirectory() as directory:
                    fixture = RoutineDeploymentFixture(Path(directory).resolve())
                    deployment = fixture.deployment()
                    initial, active_a, _, _, staged_b, activation_b = (
                        fixture.staged_routine()
                    )
                    root = initial.canonical_root
                    selector_a = selector_raws(root)
                    selector_b = staged_candidate_selector_raws(staged_b)
                    expected_receipts = expected_active_receipt_inventory(
                        selector_a[1], staged_b
                    )
                    prior_receipts = frozenset(
                        {
                            active_a.active_receipt_sha256,
                            staged_b.plan.precondition.receipt_value["rollback"][
                                "sha256"
                            ],
                        }
                    )
                    prior_files = regular_file_snapshot(root)
                    stage_snapshot = fixture.stage_snapshot(staged_b.stage_path)
                    lock_identity = filesystem_identity(initial.activation_lock)
                    descriptors = process_descriptor_inventory()

                    run_routine_terminal_process_loss_cut(
                        deployment,
                        activation_b,
                        staged_b,
                        outcome=outcome,
                    )

                    journal_raw = exact_live_journal(root)
                    journal = canonical_value(journal_raw)
                    self.assertEqual(journal["phase"], "terminal")
                    self.assertEqual(journal["terminal_result"]["outcome"], outcome)
                    expected_selectors = (
                        selector_b if outcome == "candidate-active" else selector_a
                    )
                    expected_terminal_receipts = (
                        prior_receipts
                        if outcome == "restored-prior"
                        else expected_receipts
                    )
                    self.assertEqual(selector_raws(root), expected_selectors)
                    self.assertEqual(
                        receipt_digest_inventory(root), expected_terminal_receipts
                    )
                    before_recovery = regular_file_snapshot(root)
                    forbidden_smoke = mock.Mock(
                        side_effect=AssertionError("terminal recovery reran smoke")
                    )
                    recovered_results = []
                    attempts = 2 if outcome == "recovery-required" else 1
                    for _ in range(attempts):
                        with mock.patch.object(
                            deployment,
                            "_spawn_activation_smoke_child",
                            forbidden_smoke,
                        ):
                            recovered_results.append(
                                deployment.recover_transaction(
                                    deployment.RecoveryRequest(
                                        activation=activation_b,
                                        expected_journal_raw=journal_raw,
                                    )
                                )
                            )
                        if outcome == "recovery-required":
                            self.assertEqual(exact_live_journal(root), journal_raw)

                    forbidden_smoke.assert_not_called()
                    self.assertTrue(
                        all(
                            result.journal_raw == journal_raw
                            for result in recovered_results
                        )
                    )
                    self.assertEqual(recovered_results[0].outcome, outcome)
                    if outcome == "recovery-required":
                        self.assertEqual(recovered_results[0], recovered_results[1])
                        self.assertEqual(regular_file_snapshot(root), before_recovery)
                    else:
                        self.assertFalse((root / "transaction.json").exists())
                    self.assertEqual(selector_raws(root), expected_selectors)
                    self.assertEqual(
                        receipt_digest_inventory(root), expected_terminal_receipts
                    )
                    self.assertEqual(
                        tuple(root.rglob(".task-witness-*.tmp")),
                        (),
                    )
                    self.assertEqual(tuple(root.glob("transaction.*.tmp")), ())
                    assert_existing_files_are_immutable(
                        prior_files,
                        root,
                        changed=frozenset({"active.json", "deployment.json"}),
                    )
                    self.assertEqual(
                        fixture.stage_snapshot(staged_b.stage_path), stage_snapshot
                    )
                    self.assertEqual(
                        filesystem_identity(initial.activation_lock), lock_identity
                    )
                    self.assertEqual(process_descriptor_inventory(), descriptors)

    def test_public_recovery_finishes_r_first_b_last_cleanup(self) -> None:
        cases = tuple(
            (step_index, cut)
            for step_index in range(7)
            for cut in ROUTINE_CLEANUP_PROCESS_LOSS_CUTS
        )
        for step_index, cut in cases:
            with self.subTest(step_index=step_index, cut=cut):  # noqa: SIM117
                with tempfile.TemporaryDirectory() as directory:
                    fixture = RoutineDeploymentFixture(Path(directory).resolve())
                    deployment = fixture.deployment()
                    initial, active_a, _, _, staged_b, activation_b = (
                        fixture.staged_routine()
                    )
                    root = initial.canonical_root
                    selector_a = selector_raws(root)
                    expected_receipts = expected_active_receipt_inventory(
                        selector_a[1], staged_b
                    )
                    receipt_b = sha256(staged_b.deployment_raw)
                    rollback_b = sha256(staged_b.rollback_raw)
                    prior_files = regular_file_snapshot(root)
                    prior_directories = directory_snapshot(root)
                    stage_snapshot = fixture.stage_snapshot(staged_b.stage_path)
                    root_mapping_identity = filesystem_identity(root)[:4]
                    lock_identity = filesystem_identity(initial.activation_lock)
                    descriptors = process_descriptor_inventory()
                    cleanups = routine_cleanup_steps(
                        staged_b,
                        prior_files,
                        prior_directories,
                    )
                    generation_directory = Path(
                        next(
                            step.relative_path
                            for step in cleanups
                            if step.role == "runtime-entrypoint"
                        )
                    ).parent.as_posix()
                    self.assertEqual(
                        tuple(
                            (step.operation, step.index, step.role) for step in cleanups
                        ),
                        (
                            ("remove-artifact", 0, "rollback-receipt"),
                            ("remove-artifact", 1, "runtime-bundle-io"),
                            ("remove-artifact", 2, "runtime-canonical"),
                            ("remove-artifact", 3, "runtime-entrypoint"),
                            ("remove-artifact", 4, "runtime-trust"),
                            ("remove-directory", 5, generation_directory),
                            ("remove-artifact", 6, "deployment-receipt"),
                        ),
                    )

                    run_routine_cleanup_process_loss_cut(
                        deployment,
                        activation_b,
                        staged_b,
                        cleanup_steps=cleanups,
                        step_index=step_index,
                        cut=cut,
                    )

                    journal_raw = exact_live_journal(root)
                    journal = canonical_value(journal_raw)
                    target = cleanups[step_index]
                    self.assertEqual(journal["phase"], "rollback-cleaning")
                    self.assertEqual(
                        journal["pending_step"],
                        {
                            "operation": target.operation,
                            "index": step_index,
                            "role": target.role,
                        },
                    )
                    self.assertIsNotNone(journal["rollback_smoke_acceptance"])
                    self.assertEqual(selector_raws(root), selector_a)
                    crash_receipts = set(expected_receipts)
                    if step_index > 0 or cut != "before-unlink":
                        crash_receipts.remove(rollback_b)
                    if step_index == 6 and cut != "before-unlink":
                        crash_receipts.remove(receipt_b)
                    self.assertEqual(
                        receipt_digest_inventory(root), frozenset(crash_receipts)
                    )
                    for step in cleanups:
                        expected_present = step.index > step_index or (
                            step.index == step_index and cut == "before-unlink"
                        )
                        target_path = root / step.relative_path
                        self.assertEqual(target_path.exists(), expected_present)
                        if not expected_present:
                            continue
                        if step.operation == "remove-directory":
                            metadata = target_path.lstat()
                            self.assertTrue(stat.S_ISDIR(metadata.st_mode))
                            self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o700)
                            self.assertEqual(metadata.st_uid, os.geteuid())
                        else:
                            metadata = target_path.lstat()
                            self.assertTrue(stat.S_ISREG(metadata.st_mode))
                            self.assertEqual(metadata.st_nlink, 1)
                            self.assertEqual(
                                target_path.read_bytes(), step.artifact.raw
                            )
                    before_recovery = regular_file_snapshot(root)
                    directories_before_recovery = directory_snapshot(root)
                    smoke = PublicRoutineSmokeBoundary(
                        root,
                        staged_b,
                        expected_receipt_digests=expected_receipts,
                        candidate_accepted=False,
                        rollback_accepted=True,
                    )
                    try:
                        with mock.patch.object(
                            deployment,
                            "_spawn_activation_smoke_child",
                            smoke,
                        ):
                            result = deployment.recover_transaction(
                                deployment.RecoveryRequest(
                                    activation=activation_b,
                                    expected_journal_raw=journal_raw,
                                )
                            )
                    except deployment.DeploymentError:
                        self.assertEqual(exact_live_journal(root), journal_raw)
                        self.assertEqual(regular_file_snapshot(root), before_recovery)
                        self.assertEqual(
                            directory_snapshot(root),
                            directories_before_recovery,
                        )
                        self.assertEqual(
                            receipt_digest_inventory(root),
                            frozenset(crash_receipts),
                        )
                        self.assertEqual(
                            fixture.stage_snapshot(staged_b.stage_path),
                            stage_snapshot,
                        )
                        raise

                    self.assertEqual(smoke.phases, [])
                    self.assertEqual(result.outcome, "restored-prior")
                    self.assertEqual(selector_raws(root), selector_a)
                    self.assertEqual(
                        receipt_digest_inventory(root),
                        frozenset(
                            {
                                active_a.active_receipt_sha256,
                                staged_b.plan.precondition.receipt_value["rollback"][
                                    "sha256"
                                ],
                            }
                        ),
                    )
                    self.assertFalse(
                        (root / "receipts" / f"sha256-{rollback_b}.json").exists()
                    )
                    self.assertFalse(
                        (root / "receipts" / f"sha256-{receipt_b}.json").exists()
                    )
                    for step in cleanups:
                        self.assertFalse((root / step.relative_path).exists())
                    assert_existing_files_are_immutable(
                        prior_files,
                        root,
                        changed=frozenset({"active.json", "deployment.json"}),
                    )
                    assert_no_transaction_residue(root)
                    self.assertEqual(
                        fixture.stage_snapshot(staged_b.stage_path), stage_snapshot
                    )
                    self.assertEqual(
                        filesystem_identity(initial.activation_lock), lock_identity
                    )
                    self.assertEqual(
                        filesystem_identity(root)[:4],
                        root_mapping_identity,
                    )
                    self.assertEqual(process_descriptor_inventory(), descriptors)

    def test_public_recovery_respects_durable_smoke_acceptance(self) -> None:
        cases = tuple(
            (phase, cut)
            for phase in ("candidate-smoke", "rollback-smoke")
            for cut in ROUTINE_SMOKE_PROCESS_LOSS_CUTS
        )
        for phase, cut in cases:
            with self.subTest(phase=phase, cut=cut):  # noqa: SIM117
                with tempfile.TemporaryDirectory() as directory:
                    fixture = RoutineDeploymentFixture(Path(directory).resolve())
                    deployment = fixture.deployment()
                    initial, active_a, _, _, staged_b, activation_b = (
                        fixture.staged_routine()
                    )
                    root = initial.canonical_root
                    selector_a = selector_raws(root)
                    selector_b = staged_candidate_selector_raws(staged_b)
                    expected_receipts = expected_active_receipt_inventory(
                        selector_a[1], staged_b
                    )
                    prior_files = regular_file_snapshot(root)
                    stage_snapshot = fixture.stage_snapshot(staged_b.stage_path)
                    lock_identity = filesystem_identity(initial.activation_lock)
                    descriptors = process_descriptor_inventory()

                    trace = run_routine_smoke_process_loss_cut(
                        deployment,
                        activation_b,
                        staged_b,
                        phase=phase,
                        cut=cut,
                    )

                    self.assertEqual(
                        trace,
                        b"C" if phase == "candidate-smoke" else b"CR",
                    )
                    journal_raw = exact_live_journal(root)
                    journal = canonical_value(journal_raw)
                    self.assertEqual(journal["phase"], phase)
                    acceptance_key = (
                        "candidate_smoke_acceptance"
                        if phase == "candidate-smoke"
                        else "rollback_smoke_acceptance"
                    )
                    self.assertEqual(
                        journal[acceptance_key] is not None,
                        cut == "accepted-journal",
                    )
                    self.assertEqual(
                        selector_raws(root),
                        selector_b if phase == "candidate-smoke" else selector_a,
                    )
                    self.assertEqual(receipt_digest_inventory(root), expected_receipts)
                    smoke = PublicRoutineSmokeBoundary(
                        root,
                        staged_b,
                        expected_receipt_digests=expected_receipts,
                        candidate_accepted=phase == "candidate-smoke",
                        rollback_accepted=True,
                    )
                    before_recovery = regular_file_snapshot(root)
                    try:
                        with mock.patch.object(
                            deployment,
                            "_spawn_activation_smoke_child",
                            smoke,
                        ):
                            result = deployment.recover_transaction(
                                deployment.RecoveryRequest(
                                    activation=activation_b,
                                    expected_journal_raw=journal_raw,
                                )
                            )
                    except deployment.DeploymentError:
                        self.assertEqual(exact_live_journal(root), journal_raw)
                        self.assertEqual(regular_file_snapshot(root), before_recovery)
                        self.assertEqual(
                            fixture.stage_snapshot(staged_b.stage_path),
                            stage_snapshot,
                        )
                        raise

                    expected_recovery_smoke = [phase] if cut == "child-return" else []
                    self.assertEqual(smoke.phases, expected_recovery_smoke)
                    if phase == "candidate-smoke":
                        self.assertEqual(result.outcome, "candidate-active")
                        self.assertEqual(selector_raws(root), selector_b)
                        self.assertEqual(
                            receipt_digest_inventory(root), expected_receipts
                        )
                    else:
                        self.assertEqual(result.outcome, "restored-prior")
                        self.assertEqual(selector_raws(root), selector_a)
                        self.assertEqual(
                            receipt_digest_inventory(root),
                            frozenset(
                                {
                                    active_a.active_receipt_sha256,
                                    staged_b.plan.precondition.receipt_value[
                                        "rollback"
                                    ]["sha256"],
                                }
                            ),
                        )
                    assert_existing_files_are_immutable(
                        prior_files,
                        root,
                        changed=frozenset({"active.json", "deployment.json"}),
                    )
                    assert_no_transaction_residue(root)
                    self.assertEqual(
                        fixture.stage_snapshot(staged_b.stage_path), stage_snapshot
                    )
                    self.assertEqual(
                        filesystem_identity(initial.activation_lock), lock_identity
                    )
                    self.assertEqual(process_descriptor_inventory(), descriptors)

    def test_public_recovery_rejects_journal_generation_contradictions(self) -> None:
        mutations = (
            "stale-sequence",
            "future-sequence",
            "misnamed",
            "multiple",
            "divergent-prefix",
            "authority-rebound",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):  # noqa: SIM117
                with tempfile.TemporaryDirectory() as directory:
                    fixture = RoutineDeploymentFixture(Path(directory).resolve())
                    deployment = fixture.deployment()
                    initial, _, _, _, staged_b, activation_b = fixture.staged_routine()
                    root = initial.canonical_root
                    selector_a = selector_raws(root)
                    stage_snapshot = fixture.stage_snapshot(staged_b.stage_path)
                    lock_identity = filesystem_identity(initial.activation_lock)
                    descriptors = process_descriptor_inventory()

                    target_raw = run_routine_journal_process_loss_cut(
                        deployment,
                        activation_b,
                        staged_b,
                        generation="frozen",
                        cut="partial-write",
                    )
                    journal_raw = exact_live_journal(root)
                    journal = canonical_value(journal_raw)
                    target = canonical_value(target_raw)
                    exact = root / (
                        f"transaction.{target['transaction_id']}."
                        f"{target['sequence']}.tmp"
                    )
                    if mutation == "stale-sequence":
                        changed = exact.with_name(
                            f"transaction.{target['transaction_id']}."
                            f"{journal['sequence']}.tmp"
                        )
                        exact.rename(changed)
                    elif mutation == "future-sequence":
                        changed = exact.with_name(
                            f"transaction.{target['transaction_id']}."
                            f"{target['sequence'] + 1}.tmp"
                        )
                        exact.rename(changed)
                    elif mutation == "misnamed":
                        changed = exact.with_name(
                            f"transaction.{target['transaction_id']}.x.tmp"
                        )
                        exact.rename(changed)
                    elif mutation == "multiple":
                        changed = exact.with_name(
                            f"transaction.{target['transaction_id']}."
                            f"{target['sequence'] + 1}.tmp"
                        )
                        changed.write_bytes(target_raw)
                    elif mutation == "divergent-prefix":
                        changed = exact
                        changed.write_bytes(b"not a journal successor prefix")
                    else:
                        changed = exact
                        rebound = canonical_value(target_raw)
                        rebound["outer_maintenance_transaction_sha256"] = "0" * 64
                        rebound["content_sha256"] = sha256(
                            json.dumps(
                                {
                                    key: item
                                    for key, item in rebound.items()
                                    if key != "content_sha256"
                                },
                                ensure_ascii=False,
                                separators=(",", ":"),
                                sort_keys=True,
                            ).encode()
                        )
                        changed.write_bytes(
                            json.dumps(
                                rebound,
                                ensure_ascii=False,
                                separators=(",", ":"),
                                sort_keys=True,
                            ).encode()
                        )
                    changed.chmod(0o600)
                    changed_identity = filesystem_identity(changed)
                    before_recovery = regular_file_snapshot(root)
                    receipts_before = receipt_digest_inventory(root)
                    with (
                        mock.patch.object(
                            deployment,
                            "_spawn_activation_smoke_child",
                            side_effect=AssertionError(
                                "contradictory journal state reached smoke"
                            ),
                        ) as smoke,
                        self.assertRaises(deployment.DeploymentError),
                    ):
                        deployment.recover_transaction(
                            deployment.RecoveryRequest(
                                activation=activation_b,
                                expected_journal_raw=journal_raw,
                            )
                        )

                    smoke.assert_not_called()
                    self.assertEqual(exact_live_journal(root), journal_raw)
                    self.assertEqual(regular_file_snapshot(root), before_recovery)
                    self.assertEqual(filesystem_identity(changed), changed_identity)
                    self.assertEqual(selector_raws(root), selector_a)
                    self.assertEqual(receipt_digest_inventory(root), receipts_before)
                    self.assertEqual(
                        fixture.stage_snapshot(staged_b.stage_path), stage_snapshot
                    )
                    self.assertEqual(
                        filesystem_identity(initial.activation_lock), lock_identity
                    )
                    self.assertEqual(process_descriptor_inventory(), descriptors)

    def test_public_recovery_rejects_resealed_routine_journal_program_drift(
        self,
    ) -> None:
        for mutation in (
            "transaction-class",
            "phase",
            "sequence",
            "cursor",
            "previous-chain",
        ):
            with self.subTest(mutation=mutation):  # noqa: SIM117
                with tempfile.TemporaryDirectory() as directory:
                    fixture = RoutineDeploymentFixture(Path(directory).resolve())
                    deployment = fixture.deployment()
                    initial, _, _, _, staged_b, activation_b = fixture.staged_routine()
                    root = initial.canonical_root
                    if mutation == "cursor":
                        run_routine_additive_process_loss_cut(
                            deployment,
                            activation_b,
                            artifact_index=0,
                            cut="temp-create",
                        )
                    else:
                        run_routine_journal_process_loss_cut(
                            deployment,
                            activation_b,
                            staged_b,
                            generation="frozen",
                            cut="replace",
                        )
                    value = copy.deepcopy(canonical_value(exact_live_journal(root)))
                    if mutation == "transaction-class":
                        value["transaction_class"] = "control-set-maintenance"
                    elif mutation == "phase":
                        value["phase"] = "candidate-smoke"
                    elif mutation == "sequence":
                        value["sequence"] = 999
                    elif mutation == "cursor":
                        value["pending_step"]["role"] = "deployment-alias"
                    else:
                        value["previous_journal_sha256"] = "f" * 64
                    changed_raw = _reseal_routine_journal(value)
                    transaction = root / "transaction.json"
                    transaction.write_bytes(changed_raw)
                    transaction.chmod(0o600)

                    self.assert_public_recovery_rejects_without_mutation(
                        fixture=fixture,
                        deployment=deployment,
                        initial=initial,
                        staged=staged_b,
                        activation=activation_b,
                        journal_raw=changed_raw,
                    )

    def test_public_recovery_rejects_rollback_authority_or_handoff_swaps(
        self,
    ) -> None:
        for mutation in (
            "rollback-authority",
            "handoff-target",
            "handoff-context",
        ):
            with self.subTest(mutation=mutation):  # noqa: SIM117
                with tempfile.TemporaryDirectory() as directory:
                    fixture = RoutineDeploymentFixture(Path(directory).resolve())
                    deployment = fixture.deployment()
                    if mutation == "handoff-context":
                        candidate_a, candidate_b, source_identity = (
                            fixture.provider_candidate_pair()
                        )
                        initial, active_a = fixture.activate_initial(
                            candidate_a,
                            source_identity=source_identity,
                        )
                        request_b = fixture.request_for_candidate(
                            initial.canonical_root,
                            active_a.active_receipt_sha256,
                            candidate_b,
                            release_version="1.0.1",
                            revision="b" * 40,
                            sequence=8,
                            **source_identity,
                        )
                        prepared_b = deployment.prepare_deployment(request_b)
                        authorization_b = fixture.authorization_raw(prepared_b)
                        staged_b = deployment.stage_deployment(
                            request_b,
                            authorization_b,
                            fixture.root / "routine-provider-stage",
                        )
                        activation_b = deployment.ActivationRequest(
                            deployment=request_b,
                            authorization_raw=authorization_b,
                            stage_receipt=staged_b.stage_path,
                        )
                    else:
                        initial, _, _, _, staged_b, activation_b = (
                            fixture.staged_routine()
                        )
                    root = initial.canonical_root
                    trace = run_routine_smoke_process_loss_cut(
                        deployment,
                        activation_b,
                        staged_b,
                        phase="rollback-smoke",
                        cut="child-return",
                    )
                    self.assertEqual(trace, b"CR")
                    value = copy.deepcopy(canonical_value(exact_live_journal(root)))
                    receipt_b = sha256(staged_b.deployment_raw)
                    if mutation == "rollback-authority":
                        value["rollback_authority"]["receipt_sha256"] = receipt_b
                    elif mutation == "handoff-target":
                        value["smoke_handoff"]["target_deployment_receipt_sha256"] = (
                            receipt_b
                        )
                    else:
                        candidate_smoke = staged_b.deployment_value["smoke"]
                        candidate_handoff = (
                            candidate_smoke["bundle"]["sha256"],
                            candidate_smoke["trust_context"]["sha256"],
                        )
                        prior_handoff = (
                            value["smoke_handoff"]["smoke_bundle_sha256"],
                            value["smoke_handoff"]["smoke_trust_context_sha256"],
                        )
                        self.assertNotEqual(candidate_handoff, prior_handoff)
                        self.assertNotEqual(candidate_handoff[1], prior_handoff[1])
                        value["smoke_handoff"]["smoke_bundle_sha256"] = candidate_smoke[
                            "bundle"
                        ]["sha256"]
                        value["smoke_handoff"]["smoke_trust_context_sha256"] = (
                            candidate_smoke["trust_context"]["sha256"]
                        )
                    changed_raw = _reseal_routine_journal(value)
                    transaction = root / "transaction.json"
                    transaction.write_bytes(changed_raw)
                    transaction.chmod(0o600)

                    self.assert_public_recovery_rejects_without_mutation(
                        fixture=fixture,
                        deployment=deployment,
                        initial=initial,
                        staged=staged_b,
                        activation=activation_b,
                        journal_raw=changed_raw,
                    )

    def test_public_recovery_rejects_additive_temporary_contradictions(self) -> None:
        cases = (
            ("pending-misnamed", "pending"),
            ("pending-wrong-index", "pending"),
            ("pending-wrong-transaction", "pending"),
            ("pending-extra-temp", "pending"),
            ("pending-invalid-prefix", "pending"),
            ("pending-full-wrong-target", "pending"),
            ("pending-symlink", "pending"),
            ("pending-fifo", "pending"),
            ("pending-wrong-mode", "pending"),
            ("pending-multiple-links", "pending"),
            ("terminal-misnamed", "terminal"),
            ("terminal-stale-exact", "terminal"),
        )
        for mutation, state in cases:
            with self.subTest(mutation=mutation):  # noqa: SIM117
                with tempfile.TemporaryDirectory() as directory:
                    fixture = RoutineDeploymentFixture(Path(directory).resolve())
                    deployment = fixture.deployment()
                    initial, _, _, _, staged_b, activation_b = fixture.staged_routine()
                    root = initial.canonical_root
                    selector_a = selector_raws(root)
                    selector_b = staged_candidate_selector_raws(staged_b)
                    expected_receipts = expected_active_receipt_inventory(
                        selector_a[1], staged_b
                    )
                    artifacts = routine_additive_artifacts(staged_b)
                    target = artifacts[0]
                    stage_snapshot = fixture.stage_snapshot(staged_b.stage_path)
                    lock_identity = filesystem_identity(initial.activation_lock)
                    descriptors = process_descriptor_inventory()

                    if state == "pending":
                        run_routine_additive_process_loss_cut(
                            deployment,
                            activation_b,
                            artifact_index=0,
                            cut="temp-create",
                        )
                    else:
                        run_routine_terminal_process_loss_cut(
                            deployment,
                            activation_b,
                            staged_b,
                        )
                    journal_raw = exact_live_journal(root)
                    journal = canonical_value(journal_raw)
                    exact = routine_install_temporary_path(
                        target,
                        journal["transaction_id"],
                        0,
                    )
                    parent = exact.parent

                    if mutation == "pending-misnamed":
                        exact.unlink()
                        changed = parent / (
                            f".task-witness-install-{journal['transaction_id']}-x.tmp"
                        )
                        changed.write_bytes(target.raw)
                    elif mutation == "pending-wrong-index":
                        exact.unlink()
                        changed = routine_install_temporary_path(
                            target,
                            journal["transaction_id"],
                            1,
                        )
                        changed.write_bytes(artifacts[1].raw)
                    elif mutation == "pending-wrong-transaction":
                        exact.unlink()
                        changed = routine_install_temporary_path(
                            target,
                            "0" * 64,
                            0,
                        )
                        changed.write_bytes(target.raw)
                    elif mutation == "pending-extra-temp":
                        changed = routine_install_temporary_path(
                            target,
                            journal["transaction_id"],
                            1,
                        )
                        changed.write_bytes(artifacts[1].raw)
                    elif mutation == "pending-invalid-prefix":
                        changed = exact
                        changed.chmod(0o600)
                        changed.write_bytes(b"not an additive artifact prefix")
                    elif mutation == "pending-full-wrong-target":
                        changed = exact
                        changed.chmod(0o600)
                        changed.write_bytes(artifacts[1].raw)
                    elif mutation == "pending-symlink":
                        changed = exact
                        changed.unlink()
                        changed.symlink_to(Path(target.staged_path))
                    elif mutation == "pending-fifo":
                        changed = exact
                        changed.unlink()
                        os.mkfifo(changed, 0o600)
                    elif mutation == "pending-wrong-mode":
                        changed = exact
                        changed.chmod(0o644)
                    elif mutation == "pending-multiple-links":
                        changed = exact
                        changed.chmod(0o600)
                        changed.write_bytes(target.raw)
                        os.link(changed, parent / f"{changed.name}.peer")
                    elif mutation == "terminal-misnamed":
                        changed = parent / (
                            f".task-witness-install-{journal['transaction_id']}-x.tmp"
                        )
                        changed.write_bytes(target.raw)
                    else:
                        changed = exact
                        changed.write_bytes(target.raw)

                    if not changed.is_symlink():
                        changed.chmod(
                            0o644 if mutation == "pending-wrong-mode" else 0o600
                        )
                    changed_identity = filesystem_identity(changed)
                    before_recovery = regular_file_snapshot(root)
                    selectors_before = selector_raws(root)
                    receipts_before = receipt_digest_inventory(root)
                    if state == "pending":
                        self.assertEqual(journal["phase"], "additive-installing")
                        self.assertEqual(selectors_before, selector_a)
                    else:
                        self.assertEqual(journal["phase"], "terminal")
                        self.assertEqual(selectors_before, selector_b)
                        self.assertEqual(receipts_before, expected_receipts)
                    with (
                        mock.patch.object(
                            deployment,
                            "_spawn_activation_smoke_child",
                            side_effect=AssertionError(
                                "contradictory additive state reached smoke"
                            ),
                        ) as smoke,
                        self.assertRaises(deployment.DeploymentError),
                    ):
                        deployment.recover_transaction(
                            deployment.RecoveryRequest(
                                activation=activation_b,
                                expected_journal_raw=journal_raw,
                            )
                        )

                    smoke.assert_not_called()
                    self.assertEqual(exact_live_journal(root), journal_raw)
                    self.assertEqual(regular_file_snapshot(root), before_recovery)
                    self.assertEqual(filesystem_identity(changed), changed_identity)
                    self.assertEqual(selector_raws(root), selectors_before)
                    self.assertEqual(receipt_digest_inventory(root), receipts_before)
                    self.assertEqual(
                        fixture.stage_snapshot(staged_b.stage_path), stage_snapshot
                    )
                    self.assertEqual(
                        filesystem_identity(initial.activation_lock), lock_identity
                    )
                    self.assertEqual(process_descriptor_inventory(), descriptors)

    def test_public_recovery_rejects_unexpected_routine_artifact_residue(
        self,
    ) -> None:
        for residue in ("additive-final", "selector-final"):
            with self.subTest(residue=residue):  # noqa: SIM117
                with tempfile.TemporaryDirectory() as directory:
                    fixture = RoutineDeploymentFixture(Path(directory).resolve())
                    deployment = fixture.deployment()
                    initial, _, _, _, staged_b, activation_b = fixture.staged_routine()
                    root = initial.canonical_root
                    selector_b = staged_candidate_selector_raws(staged_b)
                    stage_snapshot = fixture.stage_snapshot(staged_b.stage_path)
                    lock_identity = filesystem_identity(initial.activation_lock)
                    descriptors = process_descriptor_inventory()

                    run_routine_terminal_process_loss_cut(
                        deployment,
                        activation_b,
                        staged_b,
                    )
                    journal_raw = exact_live_journal(root)
                    if residue == "additive-final":
                        artifact = routine_additive_artifacts(staged_b)[0]
                        changed = Path(artifact.installed_path).parent / (
                            "unexpected-routine-artifact"
                        )
                    else:
                        changed = root / "active.json.peer"
                    changed.write_bytes(b"unowned routine recovery residue\n")
                    changed.chmod(0o600)
                    changed_identity = filesystem_identity(changed)
                    before_recovery = regular_file_snapshot(root)
                    receipts_before = receipt_digest_inventory(root)

                    with (
                        mock.patch.object(
                            deployment,
                            "_spawn_activation_smoke_child",
                            side_effect=AssertionError(
                                "unexpected routine residue reached smoke"
                            ),
                        ) as smoke,
                        self.assertRaises(deployment.DeploymentError),
                    ):
                        deployment.recover_transaction(
                            deployment.RecoveryRequest(
                                activation=activation_b,
                                expected_journal_raw=journal_raw,
                            )
                        )

                    smoke.assert_not_called()
                    self.assertEqual(exact_live_journal(root), journal_raw)
                    self.assertEqual(regular_file_snapshot(root), before_recovery)
                    self.assertEqual(filesystem_identity(changed), changed_identity)
                    self.assertEqual(selector_raws(root), selector_b)
                    self.assertEqual(receipt_digest_inventory(root), receipts_before)
                    self.assertEqual(
                        fixture.stage_snapshot(staged_b.stage_path), stage_snapshot
                    )
                    self.assertEqual(
                        filesystem_identity(initial.activation_lock), lock_identity
                    )
                    self.assertEqual(process_descriptor_inventory(), descriptors)

    def test_public_recovery_rejects_receipt_authority_contradictions(self) -> None:
        mutations = (
            "missing-a",
            "mutated-a",
            "missing-r-a",
            "mutated-r-a",
            "missing-b",
            "mutated-b",
            "missing-r-b",
            "mutated-r-b",
            "extra",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):  # noqa: SIM117
                with tempfile.TemporaryDirectory() as directory:
                    fixture = RoutineDeploymentFixture(Path(directory).resolve())
                    deployment = fixture.deployment()
                    initial, active_a, _, _, staged_b, activation_b = (
                        fixture.staged_routine()
                    )
                    root = initial.canonical_root
                    run_routine_terminal_process_loss_cut(
                        deployment,
                        activation_b,
                        staged_b,
                        outcome="recovery-required",
                    )
                    journal_raw = exact_live_journal(root)
                    paths = {
                        "a": root
                        / "receipts"
                        / f"sha256-{active_a.active_receipt_sha256}.json",
                        "r-a": root
                        / "receipts"
                        / (
                            "sha256-"
                            f"{staged_b.plan.precondition.receipt_value['rollback']['sha256']}"
                            ".json"
                        ),
                        "b": Path(
                            staged_artifact(
                                staged_b,
                                "deployment-receipt",
                            ).installed_path
                        ),
                        "r-b": Path(
                            staged_artifact(
                                staged_b,
                                "rollback-receipt",
                            ).installed_path
                        ),
                    }
                    if mutation == "extra":
                        changed = root / "receipts" / f"sha256-{'f' * 64}.json"
                        changed.write_bytes(b"{}\n")
                        changed.chmod(0o600)
                    else:
                        operation, role = mutation.split("-", maxsplit=1)
                        if role == "r":
                            role = "r-a"
                        elif role == "r-b":
                            role = "r-b"
                        changed = paths[role]
                        if operation == "missing":
                            changed.unlink()
                        else:
                            changed.write_bytes(changed.read_bytes() + b" ")

                    self.assert_public_recovery_rejects_without_mutation(
                        fixture=fixture,
                        deployment=deployment,
                        initial=initial,
                        staged=staged_b,
                        activation=activation_b,
                        journal_raw=journal_raw,
                    )

    def test_public_recovery_rejects_wrong_cleanup_receipt_order(self) -> None:
        for mutation, step_index in (
            ("b-removed-before-r-b", 0),
            ("r-b-resurrected-before-b", 6),
        ):
            with self.subTest(mutation=mutation):  # noqa: SIM117
                with tempfile.TemporaryDirectory() as directory:
                    fixture = RoutineDeploymentFixture(Path(directory).resolve())
                    deployment = fixture.deployment()
                    initial, _, _, _, staged_b, activation_b = fixture.staged_routine()
                    root = initial.canonical_root
                    rollback_b = staged_artifact(staged_b, "rollback-receipt")
                    receipt_b = staged_artifact(staged_b, "deployment-receipt")
                    cleanups = routine_cleanup_steps(
                        staged_b,
                        regular_file_snapshot(root),
                        directory_snapshot(root),
                    )
                    run_routine_cleanup_process_loss_cut(
                        deployment,
                        activation_b,
                        staged_b,
                        cleanup_steps=cleanups,
                        step_index=step_index,
                        cut="before-unlink",
                    )
                    journal_raw = exact_live_journal(root)
                    if step_index == 0:
                        Path(receipt_b.installed_path).unlink()
                    else:
                        restored = Path(rollback_b.installed_path)
                        restored.write_bytes(rollback_b.raw)
                        restored.chmod(0o600)

                    self.assert_public_recovery_rejects_without_mutation(
                        fixture=fixture,
                        deployment=deployment,
                        initial=initial,
                        staged=staged_b,
                        activation=activation_b,
                        journal_raw=journal_raw,
                    )

    def test_public_recovery_rejects_terminal_live_selector_mismatch(self) -> None:
        cases = (
            ("candidate-active", "A"),
            ("restored-prior", "B"),
            ("recovery-required", "B"),
        )
        for outcome, live in cases:
            with self.subTest(outcome=outcome, live=live):  # noqa: SIM117
                with tempfile.TemporaryDirectory() as directory:
                    fixture = RoutineDeploymentFixture(Path(directory).resolve())
                    deployment = fixture.deployment()
                    initial, _, _, _, staged_b, activation_b = fixture.staged_routine()
                    root = initial.canonical_root
                    selector_a = selector_raws(root)
                    selector_b = staged_candidate_selector_raws(staged_b)
                    run_routine_terminal_process_loss_cut(
                        deployment,
                        activation_b,
                        staged_b,
                        outcome=outcome,
                    )
                    journal_raw = exact_live_journal(root)
                    selected = selector_a if live == "A" else selector_b
                    (root / "active.json").write_bytes(selected[0])
                    (root / "active.json").chmod(0o600)
                    (root / "deployment.json").write_bytes(selected[1])
                    (root / "deployment.json").chmod(0o600)

                    self.assert_public_recovery_rejects_without_mutation(
                        fixture=fixture,
                        deployment=deployment,
                        initial=initial,
                        staged=staged_b,
                        activation=activation_b,
                        journal_raw=journal_raw,
                    )

    def test_public_recovery_rejects_stage_or_occ_authority_drift(self) -> None:
        for mutation in ("stage-receipt", "stage-artifact", "active-occ"):
            with self.subTest(mutation=mutation):  # noqa: SIM117
                with tempfile.TemporaryDirectory() as directory:
                    fixture = RoutineDeploymentFixture(Path(directory).resolve())
                    deployment = fixture.deployment()
                    initial, _, _, _, staged_b, activation_b = fixture.staged_routine()
                    root = initial.canonical_root
                    run_routine_journal_process_loss_cut(
                        deployment,
                        activation_b,
                        staged_b,
                        generation="frozen",
                        cut="replace",
                    )
                    journal_raw = exact_live_journal(root)
                    changed_activation = activation_b
                    if mutation == "stage-receipt":
                        staged_b.stage_path.write_bytes(staged_b.stage_raw + b" ")
                    elif mutation == "stage-artifact":
                        artifact = staged_b.artifacts[0]
                        Path(artifact.staged_path).write_bytes(artifact.raw + b" ")
                    else:
                        stale = replace(
                            activation_b.deployment,
                            expected_active_receipt_sha256="0" * 64,
                        )
                        changed_activation = deployment.ActivationRequest(
                            deployment=stale,
                            authorization_raw=activation_b.authorization_raw,
                            stage_receipt=activation_b.stage_receipt,
                        )

                    self.assert_public_recovery_rejects_without_mutation(
                        fixture=fixture,
                        deployment=deployment,
                        initial=initial,
                        staged=staged_b,
                        activation=changed_activation,
                        journal_raw=journal_raw,
                    )

    def test_candidate_rejection_restores_a_and_cleans_only_b_authority(self) -> None:
        deployment = self.fixture.deployment()
        initial, active_a, _, _, staged_b, activation_b = self.fixture.staged_routine()
        root = initial.canonical_root
        selector_a = selector_raws(root)
        expected_receipts = expected_active_receipt_inventory(selector_a[1], staged_b)
        stage_snapshot = self.fixture.stage_snapshot(staged_b.stage_path)
        smoke = PublicRoutineSmokeBoundary(
            root,
            staged_b,
            expected_receipt_digests=expected_receipts,
            candidate_accepted=False,
            rollback_accepted=True,
        )

        with mock.patch.object(deployment, "_spawn_activation_smoke_child", smoke):
            result = deployment.activate_staged(activation_b)

        receipt_b = sha256(staged_b.deployment_raw)
        selector_b = staged_candidate_selector_raws(staged_b)
        self.assertEqual(result.outcome, "restored-prior")
        self.assertEqual(result.candidate_receipt_sha256, receipt_b)
        self.assertEqual(result.active_receipt_sha256, active_a.active_receipt_sha256)
        self.assertEqual(
            result.accepted_envelope_sha256, sha256(smoke.outputs["rollback-smoke"])
        )
        self.assertEqual(result.journal_value["phase"], "terminal")
        self.assertEqual(
            result.journal_value["terminal_result"]["outcome"], "restored-prior"
        )
        self.assertEqual(smoke.phases, ["candidate-smoke", "rollback-smoke"])
        assert_smoke_observation(
            smoke.observations[0],
            staged=staged_b,
            phase="candidate-smoke",
            live_selectors=selector_b,
            expected_receipt_digests=expected_receipts,
        )
        assert_smoke_observation(
            smoke.observations[1],
            staged=staged_b,
            phase="rollback-smoke",
            live_selectors=selector_a,
            expected_receipt_digests=expected_receipts,
        )
        self.assertEqual(selector_raws(root), selector_a)
        self.assertEqual(
            receipt_digest_inventory(root),
            frozenset(
                {
                    active_a.active_receipt_sha256,
                    staged_b.plan.precondition.receipt_value["rollback"]["sha256"],
                }
            ),
        )
        assert_selector_has_distinct_retained_copy(root, selector_a[1])
        self.assertFalse(
            (
                root / "receipts" / f"sha256-{sha256(staged_b.rollback_raw)}.json"
            ).exists()
        )
        self.assertFalse((root / "receipts" / f"sha256-{receipt_b}.json").exists())
        assert_no_transaction_residue(root)
        self.assertEqual(
            self.fixture.stage_snapshot(staged_b.stage_path), stage_snapshot
        )

    def test_restored_a_can_activate_c_without_b_transaction_residue(self) -> None:
        deployment = self.fixture.deployment()
        initial, active_a, request_b, _, staged_b, activation_b = (
            self.fixture.staged_routine()
        )
        root = initial.canonical_root
        selector_a = selector_raws(root)
        files_before_b = regular_file_snapshot(root)
        directories_before_b = directory_snapshot(root)
        stage_b_snapshot = self.fixture.stage_snapshot(staged_b.stage_path)
        root_mapping_identity = filesystem_identity(root)[:4]
        lock_identity = filesystem_identity(initial.activation_lock)
        descriptors = process_descriptor_inventory()
        additive_b = routine_additive_artifacts(staged_b)
        transaction_b = tuple(
            artifact
            for artifact in additive_b
            if artifact.relative_path not in files_before_b
        )
        shared_a = tuple(
            artifact
            for artifact in additive_b
            if artifact.relative_path in files_before_b
        )
        self.assertEqual(
            frozenset(artifact.role for artifact in transaction_b),
            frozenset(
                {
                    "runtime-bundle-io",
                    "runtime-canonical",
                    "runtime-entrypoint",
                    "runtime-trust",
                    "deployment-receipt",
                    "rollback-receipt",
                }
            ),
        )
        self.assertEqual(
            frozenset(artifact.role for artifact in shared_a),
            frozenset(
                {
                    "smoke-bundle-manifest",
                    "trust-context",
                    "validator-module",
                }
            ),
        )
        expected_b_receipts = expected_active_receipt_inventory(selector_a[1], staged_b)
        smoke_b = PublicRoutineSmokeBoundary(
            root,
            staged_b,
            expected_receipt_digests=expected_b_receipts,
            candidate_accepted=False,
            rollback_accepted=True,
        )

        with mock.patch.object(deployment, "_spawn_activation_smoke_child", smoke_b):
            restored_a = deployment.activate_staged(activation_b)

        self.assertEqual(restored_a.outcome, "restored-prior")
        self.assertEqual(
            restored_a.active_receipt_sha256, active_a.active_receipt_sha256
        )
        self.assertEqual(smoke_b.phases, ["candidate-smoke", "rollback-smoke"])
        self.assertEqual(selector_raws(root), selector_a)
        prior_receipts = frozenset(
            {
                active_a.active_receipt_sha256,
                staged_b.plan.precondition.receipt_value["rollback"]["sha256"],
            }
        )
        self.assertEqual(receipt_digest_inventory(root), prior_receipts)
        assert_no_transaction_residue(root)

        files_after_restore = regular_file_snapshot(root)
        directories_after_restore = directory_snapshot(root)
        residual_artifacts = tuple(
            artifact
            for artifact in transaction_b
            if artifact.role
            not in {
                "deployment-receipt",
                "rollback-receipt",
            }
        )
        self.assertEqual(len(residual_artifacts), 4)
        residual_files = {
            artifact.relative_path: files_after_restore[artifact.relative_path]
            for artifact in residual_artifacts
            if artifact.relative_path in files_after_restore
        }
        expected_transaction_directories: set[str] = set()
        for artifact in transaction_b:
            parent = Path(artifact.relative_path).parent
            while parent != Path("."):
                relative = parent.as_posix()
                if relative not in directories_before_b:
                    expected_transaction_directories.add(relative)
                parent = parent.parent
        residual_directories = {
            relative: snapshot
            for relative, snapshot in directories_after_restore.items()
            if relative not in directories_before_b
        }
        if residual_files or residual_directories:
            self.assertEqual(
                frozenset(residual_files),
                frozenset(artifact.relative_path for artifact in residual_artifacts),
            )
            self.assertEqual(
                frozenset(residual_directories),
                frozenset(expected_transaction_directories),
            )
            for artifact in residual_artifacts:
                snapshot = residual_files[artifact.relative_path]
                staged_metadata = Path(artifact.staged_path).lstat()
                self.assertEqual(snapshot.raw, artifact.raw)
                self.assertEqual(snapshot.mode, artifact.installed["mode"])
                self.assertEqual(snapshot.owner, os.geteuid())
                self.assertEqual(snapshot.links, 1)
                self.assertNotEqual(
                    snapshot.identity,
                    (staged_metadata.st_dev, staged_metadata.st_ino),
                )
                self.assertNotIn(artifact.relative_path, files_before_b)
            for relative, snapshot in residual_directories.items():
                self.assertNotIn(relative, directories_before_b)
                self.assertEqual(snapshot.mode, 0o700)
                self.assertEqual(snapshot.owner, os.geteuid())
        for artifact in shared_a:
            self.assertEqual(
                files_after_restore[artifact.relative_path],
                files_before_b[artifact.relative_path],
            )
        for artifact in transaction_b:
            if artifact.role in {"deployment-receipt", "rollback-receipt"}:
                self.assertFalse(Path(artifact.installed_path).exists())
        assert_existing_files_are_immutable(
            files_before_b,
            root,
            changed=frozenset({"active.json", "deployment.json"}),
        )
        self.assertEqual(
            self.fixture.stage_snapshot(staged_b.stage_path),
            stage_b_snapshot,
        )

        candidate_c = self.fixture.next_candidate(
            request_b.candidate_root,
            "candidate-c-after-restored-a",
            "1.0.2",
        )
        request_c = self.fixture.request_for_candidate(
            root,
            restored_a.active_receipt_sha256,
            candidate_c,
            release_version="1.0.2",
            revision="c" * 40,
            sequence=9,
        )
        prepared_c = deployment.prepare_deployment(request_c)
        authorization_c = self.fixture.authorization_raw(prepared_c)
        staged_c = deployment.stage_deployment(
            request_c,
            authorization_c,
            self.root / "routine-stage-c-after-restored-a",
        )
        activation_c = deployment.ActivationRequest(
            deployment=request_c,
            authorization_raw=authorization_c,
            stage_receipt=staged_c.stage_path,
        )
        stage_c_snapshot = self.fixture.stage_snapshot(staged_c.stage_path)
        expected_c_receipts = prior_receipts | frozenset(
            {sha256(staged_c.deployment_raw), sha256(staged_c.rollback_raw)}
        )
        smoke_c = PublicRoutineSmokeBoundary(
            root,
            staged_c,
            expected_receipt_digests=expected_c_receipts,
            candidate_accepted=True,
            rollback_accepted=True,
        )
        result_c = None
        activation_error = None
        try:
            with mock.patch.object(
                deployment,
                "_spawn_activation_smoke_child",
                smoke_c,
            ):
                result_c = deployment.activate_staged(activation_c)
        except deployment.DeploymentError as error:
            activation_error = error
            self.assertEqual(str(error), "routine activation tree inventory disagrees")
            self.assertEqual(smoke_c.phases, [])
            journal_raw = exact_live_journal(root)
            journal = canonical_value(journal_raw)
            self.assertEqual(journal["transaction_class"], "routine-payload")
            self.assertEqual(journal["phase"], "prepared")
            self.assertEqual(journal["sequence"], 1)
            self.assertIsNone(journal["previous_journal_sha256"])
            self.assertIsNone(journal["pending_step"])
            before_recovery_files = regular_file_snapshot(root)
            before_recovery_directories = directory_snapshot(root)
            before_recovery_receipts = raw_receipt_inventory(root)
            before_recovery_selectors = selector_raws(root)
            before_recovery_root_identity = filesystem_identity(root)
            recovery_smoke = mock.Mock(
                side_effect=AssertionError("residue recovery reached smoke")
            )
            with (
                mock.patch.object(
                    deployment,
                    "_spawn_activation_smoke_child",
                    recovery_smoke,
                ),
                self.assertRaisesRegex(
                    deployment.DeploymentError,
                    "^routine activation tree inventory disagrees$",
                ),
            ):
                deployment.recover_transaction(
                    deployment.RecoveryRequest(
                        activation=activation_c,
                        expected_journal_raw=journal_raw,
                    )
                )
            recovery_smoke.assert_not_called()
            self.assertEqual(exact_live_journal(root), journal_raw)
            self.assertEqual(regular_file_snapshot(root), before_recovery_files)
            self.assertEqual(directory_snapshot(root), before_recovery_directories)
            self.assertEqual(raw_receipt_inventory(root), before_recovery_receipts)
            self.assertEqual(selector_raws(root), before_recovery_selectors)
            self.assertEqual(
                self.fixture.stage_snapshot(staged_c.stage_path),
                stage_c_snapshot,
            )
            self.assertEqual(
                filesystem_identity(root),
                before_recovery_root_identity,
            )
            self.assertEqual(
                filesystem_identity(initial.activation_lock),
                lock_identity,
            )
            self.assertEqual(process_descriptor_inventory(), descriptors)
            after_recovery_files = regular_file_snapshot(root)
            after_recovery_directories = directory_snapshot(root)
            self.assertEqual(
                {
                    relative: after_recovery_files[relative]
                    for relative in residual_files
                },
                residual_files,
            )
            self.assertEqual(
                {
                    relative: after_recovery_directories[relative]
                    for relative in residual_directories
                },
                residual_directories,
            )

        self.assertIsNone(
            activation_error,
            f"restored A could not activate C: {activation_error}",
        )
        self.assertEqual(residual_files, {})
        self.assertEqual(residual_directories, {})
        self.assertIsNotNone(result_c)
        self.assertEqual(result_c.outcome, "candidate-active")
        self.assertEqual(smoke_c.phases, ["candidate-smoke"])
        self.assertEqual(selector_raws(root), staged_candidate_selector_raws(staged_c))
        self.assertEqual(receipt_digest_inventory(root), expected_c_receipts)
        for artifact in residual_artifacts:
            self.assertFalse(Path(artifact.installed_path).exists())
        for relative in expected_transaction_directories:
            self.assertFalse((root / relative).exists())
        assert_staged_additions_installed(staged_c)
        assert_existing_files_are_immutable(
            files_before_b,
            root,
            changed=frozenset({"active.json", "deployment.json"}),
        )
        assert_no_transaction_residue(root)
        self.assertEqual(
            self.fixture.stage_snapshot(staged_b.stage_path),
            stage_b_snapshot,
        )
        self.assertEqual(
            self.fixture.stage_snapshot(staged_c.stage_path),
            stage_c_snapshot,
        )
        self.assertEqual(filesystem_identity(root)[:4], root_mapping_identity)
        self.assertEqual(filesystem_identity(initial.activation_lock), lock_identity)
        self.assertEqual(process_descriptor_inventory(), descriptors)

    def test_rollback_rejection_fail_stops_and_recovery_replays_terminal(self) -> None:
        deployment = self.fixture.deployment()
        initial, _, _, _, staged_b, activation_b = self.fixture.staged_routine()
        root = initial.canonical_root
        selector_a = selector_raws(root)
        expected_receipts = expected_active_receipt_inventory(selector_a[1], staged_b)
        smoke = PublicRoutineSmokeBoundary(
            root,
            staged_b,
            expected_receipt_digests=expected_receipts,
            candidate_accepted=False,
            rollback_accepted=False,
        )

        with mock.patch.object(deployment, "_spawn_activation_smoke_child", smoke):
            first = deployment.activate_staged(activation_b)

        receipt_b = sha256(staged_b.deployment_raw)
        self.assertEqual(first.outcome, "recovery-required")
        self.assertEqual(first.candidate_receipt_sha256, receipt_b)
        self.assertIsNone(first.active_receipt_sha256)
        self.assertIsNone(first.accepted_envelope_sha256)
        self.assertEqual(first.journal_value["phase"], "terminal")
        self.assertEqual(
            first.journal_value["terminal_result"]["outcome"], "recovery-required"
        )
        self.assertEqual(smoke.phases, ["candidate-smoke", "rollback-smoke"])
        assert_smoke_observation(
            smoke.observations[0],
            staged=staged_b,
            phase="candidate-smoke",
            live_selectors=staged_candidate_selector_raws(staged_b),
            expected_receipt_digests=expected_receipts,
        )
        assert_smoke_observation(
            smoke.observations[1],
            staged=staged_b,
            phase="rollback-smoke",
            live_selectors=selector_a,
            expected_receipt_digests=expected_receipts,
        )
        self.assertEqual(selector_raws(root), selector_a)
        self.assertEqual(receipt_digest_inventory(root), expected_receipts)
        assert_selector_has_distinct_retained_copy(root, selector_a[1])
        journal_raw = exact_live_journal(root)
        self.assertEqual(journal_raw, first.journal_raw)

        with mock.patch.object(deployment, "_spawn_activation_smoke_child", smoke):
            recovered = deployment.recover_transaction(
                deployment.RecoveryRequest(
                    activation=activation_b,
                    expected_journal_raw=journal_raw,
                )
            )

        self.assertEqual(recovered, first)
        self.assertEqual(smoke.phases, ["candidate-smoke", "rollback-smoke"])
        self.assertEqual(selector_raws(root), selector_a)
        self.assertEqual(receipt_digest_inventory(root), expected_receipts)
        self.assertEqual(exact_live_journal(root), journal_raw)

    def test_public_c_rejection_restores_only_immediate_prior_b(self) -> None:
        deployment = self.fixture.deployment()
        initial, _, request_b, _, staged_b, activation_b = self.fixture.staged_routine()
        root = initial.canonical_root
        selector_a = selector_raws(root)
        expected_b_receipts = expected_active_receipt_inventory(selector_a[1], staged_b)
        smoke_b = PublicRoutineSmokeBoundary(
            root,
            staged_b,
            expected_receipt_digests=expected_b_receipts,
            candidate_accepted=True,
            rollback_accepted=True,
        )
        with mock.patch.object(deployment, "_spawn_activation_smoke_child", smoke_b):
            active_b = deployment.activate_staged(activation_b)

        selector_b = selector_raws(root)
        self.assertEqual(receipt_digest_inventory(root), expected_b_receipts)
        candidate_c = self.fixture.next_candidate(
            request_b.candidate_root,
            "candidate-c",
            "1.0.2",
        )
        request_c = self.fixture.request_for_candidate(
            root,
            active_b.active_receipt_sha256,
            candidate_c,
            release_version="1.0.2",
            revision="c" * 40,
            sequence=9,
        )
        prepared_c = deployment.prepare_deployment(request_c)
        authorization_c = self.fixture.authorization_raw(prepared_c)
        staged_c = deployment.stage_deployment(
            request_c,
            authorization_c,
            self.root / "routine-stage-c",
        )
        activation_c = deployment.ActivationRequest(
            deployment=request_c,
            authorization_raw=authorization_c,
            stage_receipt=staged_c.stage_path,
        )
        receipt_c = sha256(staged_c.deployment_raw)
        expected_c_receipts = expected_b_receipts | frozenset(
            {receipt_c, sha256(staged_c.rollback_raw)}
        )
        smoke_c = PublicRoutineSmokeBoundary(
            root,
            staged_c,
            expected_receipt_digests=expected_c_receipts,
            candidate_accepted=False,
            rollback_accepted=True,
        )

        with mock.patch.object(deployment, "_spawn_activation_smoke_child", smoke_c):
            result_c = deployment.activate_staged(activation_c)

        self.assertEqual(result_c.outcome, "restored-prior")
        self.assertEqual(result_c.candidate_receipt_sha256, receipt_c)
        self.assertEqual(result_c.active_receipt_sha256, active_b.active_receipt_sha256)
        self.assertEqual(smoke_c.phases, ["candidate-smoke", "rollback-smoke"])
        assert_smoke_observation(
            smoke_c.observations[0],
            staged=staged_c,
            phase="candidate-smoke",
            live_selectors=staged_candidate_selector_raws(staged_c),
            expected_receipt_digests=expected_c_receipts,
        )
        assert_smoke_observation(
            smoke_c.observations[1],
            staged=staged_c,
            phase="rollback-smoke",
            live_selectors=selector_b,
            expected_receipt_digests=expected_c_receipts,
        )
        self.assertEqual(selector_raws(root), selector_b)
        self.assertEqual(
            receipt_digest_inventory(root),
            expected_active_receipt_inventory(
                staged_b.plan.precondition.receipt_raw,
                staged_b,
            ),
        )
        assert_selector_has_distinct_retained_copy(root, selector_b[1])
        self.assertFalse(
            (
                root / "receipts" / f"sha256-{sha256(staged_c.rollback_raw)}.json"
            ).exists()
        )
        self.assertFalse((root / "receipts" / f"sha256-{receipt_c}.json").exists())
        assert_no_transaction_residue(root)

    def test_public_c_rollback_rejection_never_cascades_to_a(self) -> None:
        deployment = self.fixture.deployment()
        initial, _, request_b, _, staged_b, activation_b = self.fixture.staged_routine()
        root = initial.canonical_root
        selector_a = selector_raws(root)
        expected_b_receipts = expected_active_receipt_inventory(selector_a[1], staged_b)
        smoke_b = PublicRoutineSmokeBoundary(
            root,
            staged_b,
            expected_receipt_digests=expected_b_receipts,
            candidate_accepted=True,
            rollback_accepted=True,
        )
        with mock.patch.object(deployment, "_spawn_activation_smoke_child", smoke_b):
            active_b = deployment.activate_staged(activation_b)
        selector_b = selector_raws(root)

        candidate_c = self.fixture.next_candidate(
            request_b.candidate_root,
            "candidate-c-no-cascade",
            "1.0.2",
        )
        request_c = self.fixture.request_for_candidate(
            root,
            active_b.active_receipt_sha256,
            candidate_c,
            release_version="1.0.2",
            revision="c" * 40,
            sequence=9,
        )
        prepared_c = deployment.prepare_deployment(request_c)
        authorization_c = self.fixture.authorization_raw(prepared_c)
        staged_c = deployment.stage_deployment(
            request_c,
            authorization_c,
            self.root / "routine-stage-c-no-cascade",
        )
        activation_c = deployment.ActivationRequest(
            deployment=request_c,
            authorization_raw=authorization_c,
            stage_receipt=staged_c.stage_path,
        )
        expected_c_receipts = expected_b_receipts | frozenset(
            {sha256(staged_c.deployment_raw), sha256(staged_c.rollback_raw)}
        )
        smoke_c = PublicRoutineSmokeBoundary(
            root,
            staged_c,
            expected_receipt_digests=expected_c_receipts,
            candidate_accepted=False,
            rollback_accepted=False,
        )

        with mock.patch.object(deployment, "_spawn_activation_smoke_child", smoke_c):
            result = deployment.activate_staged(activation_c)

        self.assertEqual(result.outcome, "recovery-required")
        self.assertEqual(smoke_c.phases, ["candidate-smoke", "rollback-smoke"])
        assert_smoke_observation(
            smoke_c.observations[1],
            staged=staged_c,
            phase="rollback-smoke",
            live_selectors=selector_b,
            expected_receipt_digests=expected_c_receipts,
        )
        self.assertEqual(selector_raws(root), selector_b)
        self.assertEqual(receipt_digest_inventory(root), expected_c_receipts)
        journal_raw = exact_live_journal(root)
        forbidden_smoke = mock.Mock(
            side_effect=AssertionError("terminal C recovery cascaded to A smoke")
        )
        with mock.patch.object(
            deployment,
            "_spawn_activation_smoke_child",
            forbidden_smoke,
        ):
            replay = deployment.recover_transaction(
                deployment.RecoveryRequest(
                    activation=activation_c,
                    expected_journal_raw=journal_raw,
                )
            )
        forbidden_smoke.assert_not_called()
        self.assertEqual(replay, result)
        self.assertEqual(exact_live_journal(root), journal_raw)
        self.assertEqual(selector_raws(root), selector_b)

        cascade = copy.deepcopy(canonical_value(journal_raw))
        cascade["prior"] = thaw_json(staged_b.rollback_value["prior_activation_unit"])
        cascade["rollback_authority"] = {
            "receipt_path": staged_b.stage_value["rollback_receipt"]["path"],
            "receipt_sha256": sha256(staged_b.rollback_raw),
            "target_state": "active",
        }
        cascade["preimage"] = {
            "manifest_path": staged_b.stage_value["rollback_receipt"]["path"],
            "manifest_sha256": sha256(staged_b.rollback_raw),
            "artifacts": thaw_json(staged_b.rollback_value["selector_preimage"]),
            "external_dependencies": thaw_json(
                staged_b.rollback_value["external_dependencies"]
            ),
        }
        cascade_raw = _reseal_routine_journal(cascade)
        (root / "transaction.json").write_bytes(cascade_raw)
        (root / "transaction.json").chmod(0o600)
        self.assert_public_recovery_rejects_without_mutation(
            fixture=self.fixture,
            deployment=deployment,
            initial=initial,
            staged=staged_c,
            activation=activation_c,
            journal_raw=cascade_raw,
        )


if __name__ == "__main__":
    unittest.main()
