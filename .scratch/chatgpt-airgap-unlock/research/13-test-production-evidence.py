#!/usr/bin/env python3
"""Deterministic tests for the provider-neutral production evidence slice."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE / "13-production-evidence.py"
SPEC = importlib.util.spec_from_file_location("production_evidence", MODULE_PATH)
assert SPEC and SPEC.loader
production_evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(production_evidence)


def bindings_for(relative_path: str, sha256: str) -> dict[str, object]:
    return {
        "schema": 1,
        "artifacts": [
            {
                "name": "application-payload",
                "path": relative_path,
                "sha256": sha256,
                "identity": {
                    "build": "fixture-build",
                    "role": "application",
                    "review": "fixture-review-immutable",
                },
            }
        ],
    }


def completed_state(
    path: Path,
    environment: dict[str, str],
    *,
    owned_pids: list[int] | None = None,
    owned_process_groups: list[int] | None = None,
    reserved_tcp_ports: list[int] | None = None,
) -> None:
    state = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "run_nonce": environment["PRODUCTION_EVIDENCE_RUN_NONCE"],
                "transition": "completed",
                "owned_pids": owned_pids or [],
                "owned_process_groups": owned_process_groups or [],
                "reserved_tcp_ports": reserved_tcp_ports or [],
                "cleanup_steps": [{"name": "fixture-cleanup", "completed": True}],
            }
        ),
        encoding="utf-8",
    )
    assert state["run_nonce"] == environment["PRODUCTION_EVIDENCE_RUN_NONCE"]
    assert state["transition"] == "prepared"


class ManifestTests(unittest.TestCase):
    def test_build_and_validate_seals_the_complete_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "stage"
            root.mkdir()
            payload = root / "payload.bin"
            payload.write_bytes(b"exact artifact\n")
            extra = root / "runtime" / "adapter.py"
            extra.parent.mkdir()
            extra.write_text("print('local')\n", encoding="utf-8")
            digest = production_evidence.file_sha256(payload)

            manifest = production_evidence.build_manifest(
                root, bindings_for("payload.bin", digest)
            )

            self.assertEqual(
                ["payload.bin", "runtime/adapter.py"],
                [entry["path"] for entry in manifest["entries"]],
            )
            self.assertNotIn(str(root), json.dumps(manifest))
            production_evidence.validate_manifest(root, manifest)

            extra.write_text("changed\n", encoding="utf-8")
            with self.assertRaises(production_evidence.EvidenceError):
                production_evidence.validate_manifest(root, manifest)

    def test_added_file_fails_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload.bin"
            payload.write_bytes(b"payload")
            manifest = production_evidence.build_manifest(
                root,
                bindings_for("payload.bin", production_evidence.file_sha256(payload)),
            )
            (root / "late-file").write_text("not sealed", encoding="utf-8")
            with self.assertRaises(production_evidence.EvidenceError):
                production_evidence.validate_manifest(root, manifest)

    def test_symlink_and_secret_binding_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload.bin"
            payload.write_bytes(b"payload")
            (root / "alias").symlink_to(payload)
            with self.assertRaises(production_evidence.EvidenceError):
                production_evidence.build_manifest(root, {"schema": 1, "artifacts": []})

            (root / "alias").unlink()
            secret_bindings = bindings_for(
                "payload.bin", production_evidence.file_sha256(payload)
            )
            secret_bindings["api_token"] = "must-not-persist"
            with self.assertRaises(production_evidence.EvidenceError):
                production_evidence.build_manifest(root, secret_bindings)

            for sensitive_key in ("accessToken", "clientSecret"):
                compound_bindings = bindings_for(
                    "payload.bin", production_evidence.file_sha256(payload)
                )
                compound_bindings["artifacts"][0]["identity"][sensitive_key] = (
                    "plain-value"
                )
                with self.assertRaises(production_evidence.EvidenceError):
                    production_evidence.build_manifest(root, compound_bindings)

    def test_empty_artifact_bindings_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "payload.bin").write_bytes(b"payload")
            with self.assertRaises(production_evidence.EvidenceError):
                production_evidence.build_manifest(root, {"schema": 1, "artifacts": []})

    def test_identity_requires_nonempty_role_and_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload.bin"
            payload.write_bytes(b"payload")
            for identity in (
                {},
                {"role": "application"},
                {"role": "", "review": "fixture-review-immutable"},
                {"role": "application", "review": ""},
            ):
                with self.subTest(identity=identity):
                    bindings = bindings_for(
                        "payload.bin", production_evidence.file_sha256(payload)
                    )
                    bindings["artifacts"][0]["identity"] = identity
                    with self.assertRaises(production_evidence.EvidenceError):
                        production_evidence.build_manifest(root, bindings)


class FinalizerTests(unittest.TestCase):
    def make_run(self, temporary: str) -> tuple[Path, Path, Path]:
        base = Path(temporary)
        stage = base / "stage"
        stage.mkdir()
        payload = stage / "payload.bin"
        payload.write_bytes(b"payload")
        manifest = production_evidence.build_manifest(
            stage, bindings_for("payload.bin", production_evidence.file_sha256(payload))
        )
        manifest_path = base / "staging-manifest.json"
        production_evidence.atomic_write_json(manifest_path, manifest)
        state_path = base / "owned-state.json"
        return stage, manifest_path, state_path

    def test_success_writes_all_terminal_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stage, manifest, state = self.make_run(temporary)
            evidence = Path(temporary) / "evidence"
            executed: list[list[str]] = []

            def execute(command: list[str], environment: dict[str, str]) -> int:
                executed.append(command)
                completed_state(state, environment)
                return 0

            result = production_evidence.run_guarded(
                stage,
                manifest,
                evidence,
                state,
                ["fixture-command"],
                execute=execute,
                collect_processes=lambda: (0, "PID PPID PGID STATE ELAPSED COMMAND\n"),
                collect_sockets=lambda: (
                    0,
                    "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n",
                ),
            )

            self.assertEqual(0, result)
            self.assertEqual([["fixture-command"]], executed)
            self.assertEqual(
                {
                    "cleanup-final.json",
                    "processes-final.txt",
                    "sockets-final.txt",
                    "verdict.json",
                },
                {path.name for path in evidence.iterdir()},
            )
            verdict = json.loads(
                (evidence / "verdict.json").read_text(encoding="utf-8")
            )
            self.assertTrue(verdict["passed"])

    def test_early_command_failure_still_writes_all_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stage, manifest, state = self.make_run(temporary)
            evidence = Path(temporary) / "evidence"

            def execute(_command: list[str], environment: dict[str, str]) -> int:
                completed_state(state, environment)
                return 7

            result = production_evidence.run_guarded(
                stage,
                manifest,
                evidence,
                state,
                ["fixture-command"],
                execute=execute,
                collect_processes=lambda: (0, "PID PPID PGID STATE ELAPSED COMMAND\n"),
                collect_sockets=lambda: (
                    0,
                    "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n",
                ),
            )

            self.assertEqual(7, result)
            self.assertEqual(4, len(list(evidence.iterdir())))
            verdict = json.loads(
                (evidence / "verdict.json").read_text(encoding="utf-8")
            )
            self.assertFalse(verdict["passed"])
            self.assertEqual(7, verdict["command_exit_code"])

    def test_invalid_manifest_prevents_execution_and_finalizes_red(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stage, manifest, state = self.make_run(temporary)
            (stage / "payload.bin").write_bytes(b"mutated")
            evidence = Path(temporary) / "evidence"
            executed = False

            def execute(_command: list[str], _environment: dict[str, str]) -> int:
                nonlocal executed
                executed = True
                return 0

            result = production_evidence.run_guarded(
                stage,
                manifest,
                evidence,
                state,
                ["must-not-run"],
                execute=execute,
                collect_processes=lambda: (0, "PID PPID PGID STATE ELAPSED COMMAND\n"),
                collect_sockets=lambda: (
                    0,
                    "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n",
                ),
            )

            self.assertEqual(production_evidence.PREFLIGHT_FAILURE, result)
            self.assertFalse(executed)
            self.assertEqual(4, len(list(evidence.iterdir())))
            verdict = json.loads(
                (evidence / "verdict.json").read_text(encoding="utf-8")
            )
            self.assertFalse(verdict["manifest_valid"])
            self.assertFalse(verdict["command_started"])

    def test_postflight_staging_mutation_fails_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stage, manifest, state = self.make_run(temporary)
            evidence = Path(temporary) / "evidence"

            def mutate_stage(_command: list[str], environment: dict[str, str]) -> int:
                completed_state(state, environment)
                (stage / "payload.bin").write_bytes(b"postflight mutation")
                return 0

            result = production_evidence.run_guarded(
                stage,
                manifest,
                evidence,
                state,
                ["fixture-command"],
                execute=mutate_stage,
                collect_processes=lambda: (0, "PID PPID PGID STATE ELAPSED COMMAND\n"),
                collect_sockets=lambda: (
                    0,
                    "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n",
                ),
            )

            self.assertEqual(production_evidence.EVIDENCE_FAILURE, result)
            verdict = json.loads(
                (evidence / "verdict.json").read_text(encoding="utf-8")
            )
            self.assertTrue(verdict["manifest_preflight_valid"])
            self.assertFalse(verdict["manifest_postflight_valid"])
            self.assertFalse(verdict["manifest_valid"])
            self.assertIn("staging-manifest-postflight-invalid", verdict["errors"])

    def test_surviving_pid_or_listener_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stage, manifest, state = self.make_run(temporary)
            evidence = Path(temporary) / "evidence"

            def execute(_command: list[str], environment: dict[str, str]) -> int:
                completed_state(
                    state,
                    environment,
                    owned_pids=[4242],
                    owned_process_groups=[4242],
                    reserved_tcp_ports=[18999],
                )
                return 0

            result = production_evidence.run_guarded(
                stage,
                manifest,
                evidence,
                state,
                ["fixture-command"],
                execute=execute,
                collect_processes=lambda: (0, "4242 1 4242 S 00:01 process\n"),
                collect_sockets=lambda: (
                    0,
                    "proc 4242 user 10u IPv4 0t0 TCP 127.0.0.1:18999 (LISTEN)\n",
                ),
            )

            self.assertEqual(production_evidence.EVIDENCE_FAILURE, result)
            cleanup = json.loads(
                (evidence / "cleanup-final.json").read_text(encoding="utf-8")
            )
            self.assertFalse(cleanup["owned_processes_exited"])
            self.assertFalse(cleanup["owned_listeners_closed"])

    def test_orphan_child_in_owned_process_group_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stage, manifest, state = self.make_run(temporary)
            evidence = Path(temporary) / "evidence"

            def execute(_command: list[str], environment: dict[str, str]) -> int:
                completed_state(state, environment, owned_process_groups=[4242])
                return 0

            result = production_evidence.run_guarded(
                stage,
                manifest,
                evidence,
                state,
                ["fixture-command"],
                execute=execute,
                collect_processes=lambda: (0, "5001 1 4242 S 00:01 orphan-child\n"),
                collect_sockets=lambda: (
                    0,
                    "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n",
                ),
            )

            self.assertEqual(production_evidence.EVIDENCE_FAILURE, result)
            cleanup = json.loads(
                (evidence / "cleanup-final.json").read_text(encoding="utf-8")
            )
            self.assertEqual([5001], cleanup["surviving_owned_process_group_pids"])
            self.assertFalse(cleanup["owned_process_groups_exited"])

    def test_reparented_child_in_unrecorded_group_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stage, manifest, state = self.make_run(temporary)
            evidence = Path(temporary) / "evidence"
            process_snapshots = iter(
                (
                    (0, "PID PPID PGID STARTED STATE ELAPSED COMMAND\n"),
                    (
                        0,
                        "5001 1 5001 Thu Jul 16 13:57:00 2026 S 00:01 "
                        "detached-child\n",
                    ),
                )
            )

            def execute(_command: list[str], environment: dict[str, str]) -> int:
                completed_state(
                    state,
                    environment,
                    owned_pids=[4242],
                    owned_process_groups=[4242],
                )
                return 0

            result = production_evidence.run_guarded(
                stage,
                manifest,
                evidence,
                state,
                ["fixture-command"],
                execute=execute,
                collect_processes=lambda: next(process_snapshots),
                collect_sockets=lambda: (
                    0,
                    "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n",
                ),
            )

            self.assertEqual(production_evidence.EVIDENCE_FAILURE, result)
            cleanup = json.loads(
                (evidence / "cleanup-final.json").read_text(encoding="utf-8")
            )
            self.assertEqual([5001], cleanup["surviving_run_created_pids"])
            self.assertFalse(cleanup["run_created_processes_exited"])
            self.assertFalse(cleanup["owned_processes_exited"])

    def test_collector_failure_and_secret_redaction_are_terminal_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stage, manifest, state = self.make_run(temporary)
            evidence = Path(temporary) / "evidence"

            def execute(_command: list[str], environment: dict[str, str]) -> int:
                completed_state(state, environment)
                return 0

            result = production_evidence.run_guarded(
                stage,
                manifest,
                evidence,
                state,
                ["fixture-command"],
                execute=execute,
                collect_processes=lambda: (1, "process --token sk-fixture-secret\n"),
                collect_sockets=lambda: (2, "socket capture failed\n"),
            )

            self.assertEqual(production_evidence.EVIDENCE_FAILURE, result)
            processes = (evidence / "processes-final.txt").read_text(encoding="utf-8")
            self.assertIn("[REDACTED]", processes)
            self.assertNotIn("sk-fixture-secret", processes)
            verdict = json.loads(
                (evidence / "verdict.json").read_text(encoding="utf-8")
            )
            self.assertTrue(verdict["sensitive_data_redacted"])
            self.assertFalse(verdict["process_baseline_captured"])
            self.assertFalse(verdict["process_snapshot_captured"])
            self.assertFalse(verdict["socket_snapshot_captured"])
            self.assertIn("process-baseline-failed", verdict["errors"])

    def test_whitespace_credentials_are_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stage, manifest, state = self.make_run(temporary)
            evidence = Path(temporary) / "evidence"

            def execute(_command: list[str], environment: dict[str, str]) -> int:
                completed_state(state, environment)
                return 0

            result = production_evidence.run_guarded(
                stage,
                manifest,
                evidence,
                state,
                ["fixture-command"],
                execute=execute,
                collect_processes=lambda: (
                    0,
                    "4242 1 4242 S 00:01 process --token plain-value "
                    "--api-key another-value\n",
                ),
                collect_sockets=lambda: (
                    0,
                    "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n",
                ),
            )

            self.assertEqual(production_evidence.EVIDENCE_FAILURE, result)
            processes = (evidence / "processes-final.txt").read_text(encoding="utf-8")
            self.assertNotIn("plain-value", processes)
            self.assertNotIn("another-value", processes)
            self.assertEqual(2, processes.count("[REDACTED]"))

    def test_lifecycle_environment_is_allowlisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stage, manifest, state = self.make_run(temporary)
            evidence = Path(temporary) / "evidence"
            captured: dict[str, str] = {}

            def execute(_command: list[str], environment: dict[str, str]) -> int:
                captured.update(environment)
                completed_state(state, environment)
                return 0

            inherited = {
                "DYLD_INSERT_LIBRARIES": "/private/tmp/injected.dylib",
                "HTTPS_PROXY": "http://127.0.0.1:9999",
                "OPENAI_API_KEY": "not-forwarded",
                "PYTHONPATH": "/private/tmp/imports",
                production_evidence.RUN_NONCE_ENV: "stale-nonce",
                production_evidence.STATE_PATH_ENV: "/private/tmp/stale-state",
            }
            with mock.patch.dict(os.environ, inherited, clear=False):
                result = production_evidence.run_guarded(
                    stage,
                    manifest,
                    evidence,
                    state,
                    ["fixture-command"],
                    execute=execute,
                    collect_processes=lambda: (
                        0,
                        "PID PPID PGID STARTED STATE ELAPSED COMMAND\n",
                    ),
                    collect_sockets=lambda: (
                        0,
                        "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n",
                    ),
                )

            self.assertEqual(0, result)
            self.assertEqual(
                {
                    "PATH",
                    "HOME",
                    "TMPDIR",
                    "LANG",
                    "LC_ALL",
                    production_evidence.RUN_NONCE_ENV,
                    production_evidence.STATE_PATH_ENV,
                },
                set(captured),
            )
            self.assertNotEqual("stale-nonce", captured[production_evidence.RUN_NONCE_ENV])
            self.assertNotEqual(
                "/private/tmp/stale-state",
                captured[production_evidence.STATE_PATH_ENV],
            )

    def test_raised_collector_still_writes_all_terminal_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stage, manifest, state = self.make_run(temporary)
            evidence = Path(temporary) / "evidence"

            def raise_collector() -> tuple[int, str]:
                raise OSError("fixture collector unavailable")

            def execute(_command: list[str], environment: dict[str, str]) -> int:
                completed_state(state, environment)
                return 0

            result = production_evidence.run_guarded(
                stage,
                manifest,
                evidence,
                state,
                ["fixture-command"],
                execute=execute,
                collect_processes=raise_collector,
                collect_sockets=raise_collector,
            )

            self.assertEqual(production_evidence.EVIDENCE_FAILURE, result)
            self.assertEqual(4, len(list(evidence.iterdir())))
            verdict = json.loads(
                (evidence / "verdict.json").read_text(encoding="utf-8")
            )
            self.assertIn("process-snapshot-failed", verdict["errors"])
            self.assertIn("socket-snapshot-failed", verdict["errors"])

    def test_empty_command_is_preflight_failure_with_terminal_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stage, manifest, state = self.make_run(temporary)
            evidence = Path(temporary) / "evidence"

            result = production_evidence.run_guarded(
                stage,
                manifest,
                evidence,
                state,
                [],
                collect_processes=lambda: (0, "PID PPID PGID STATE ELAPSED COMMAND\n"),
                collect_sockets=lambda: (
                    0,
                    "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n",
                ),
            )

            self.assertEqual(production_evidence.PREFLIGHT_FAILURE, result)
            self.assertEqual(4, len(list(evidence.iterdir())))
            verdict = json.loads(
                (evidence / "verdict.json").read_text(encoding="utf-8")
            )
            self.assertFalse(verdict["command_started"])
            self.assertIn("guarded-command-empty", verdict["errors"])

    def test_stale_completed_state_prevents_execution_and_finalizes_red(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stage, manifest, state = self.make_run(temporary)
            state.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "run_nonce": "stale-run",
                        "transition": "completed",
                        "owned_pids": [],
                        "owned_process_groups": [],
                        "reserved_tcp_ports": [],
                        "cleanup_steps": [{"name": "stale-cleanup", "completed": True}],
                    }
                ),
                encoding="utf-8",
            )
            evidence = Path(temporary) / "evidence"
            executed = False

            def execute(_command: list[str], _environment: dict[str, str]) -> int:
                nonlocal executed
                executed = True
                return 0

            result = production_evidence.run_guarded(
                stage,
                manifest,
                evidence,
                state,
                ["must-not-run"],
                execute=execute,
                collect_processes=lambda: (0, "PID PPID PGID STATE ELAPSED COMMAND\n"),
                collect_sockets=lambda: (
                    0,
                    "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n",
                ),
            )

            self.assertEqual(production_evidence.PREFLIGHT_FAILURE, result)
            self.assertFalse(executed)
            self.assertEqual(4, len(list(evidence.iterdir())))
            verdict = json.loads(
                (evidence / "verdict.json").read_text(encoding="utf-8")
            )
            self.assertFalse(verdict["cleanup_state_valid"])
            self.assertIn("owned-state-not-fresh", verdict["errors"])

    def test_current_state_must_transition_to_completed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stage, manifest, state = self.make_run(temporary)
            evidence = Path(temporary) / "evidence"

            result = production_evidence.run_guarded(
                stage,
                manifest,
                evidence,
                state,
                ["fixture-command"],
                execute=lambda _command, _environment: 0,
                collect_processes=lambda: (0, "PID PPID PGID STATE ELAPSED COMMAND\n"),
                collect_sockets=lambda: (
                    0,
                    "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n",
                ),
            )

            self.assertEqual(production_evidence.EVIDENCE_FAILURE, result)
            verdict = json.loads(
                (evidence / "verdict.json").read_text(encoding="utf-8")
            )
            self.assertFalse(verdict["cleanup_state_valid"])
            self.assertIn("owned-state-invalid", verdict["errors"])

    def test_evidence_paths_inside_staging_tree_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stage, manifest, state = self.make_run(temporary)
            with self.assertRaises(production_evidence.EvidenceError):
                production_evidence.run_guarded(
                    stage,
                    manifest,
                    stage / "evidence",
                    state,
                    ["must-not-run"],
                )

    def test_linked_evidence_path_is_rejected_before_artifact_guarantee(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stage, manifest, state = self.make_run(temporary)
            target = Path(temporary) / "evidence-target"
            target.mkdir()
            evidence = Path(temporary) / "evidence-link"
            evidence.symlink_to(target, target_is_directory=True)

            with self.assertRaises(production_evidence.EvidenceError):
                production_evidence.run_guarded(
                    stage,
                    manifest,
                    evidence,
                    state,
                    ["must-not-run"],
                )
            self.assertEqual([], list(target.iterdir()))


if __name__ == "__main__":
    unittest.main()
