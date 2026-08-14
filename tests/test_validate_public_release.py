from __future__ import annotations

import ast
import copy
import fcntl
import hashlib
import importlib.util
import inspect
import json
import os
import py_compile
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from tests import phase7_v4_fixture as fixture

REPOSITORY = Path(__file__).resolve().parents[1]
VALIDATOR = REPOSITORY / "scripts" / "validate_public_release.py"
PREPARED_RELEASE_ENTRYPOINT = (
    REPOSITORY / "scripts" / "run_prepared_release_validation.sh"
)
PREPARED_RELEASE_SUPERVISOR = (
    REPOSITORY / "scripts" / "supervise_prepared_release_validation.py"
)


def install_prepared_release_driver(repository: Path) -> tuple[Path, Path]:
    scripts = repository / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    entrypoint = scripts / PREPARED_RELEASE_ENTRYPOINT.name
    supervisor = scripts / PREPARED_RELEASE_SUPERVISOR.name
    shutil.copy2(PREPARED_RELEASE_ENTRYPOINT, entrypoint)
    shutil.copy2(PREPARED_RELEASE_SUPERVISOR, supervisor)
    return entrypoint, supervisor


def load_validator_module():
    specification = importlib.util.spec_from_file_location("public_release", VALIDATOR)
    module = importlib.util.module_from_spec(specification)
    assert specification.loader is not None
    specification.loader.exec_module(module)
    return module


def load_prepared_release_supervisor_module(
    path: Path = PREPARED_RELEASE_SUPERVISOR,
):
    specification = importlib.util.spec_from_file_location(
        "prepared_release_supervisor",
        path,
    )
    module = importlib.util.module_from_spec(specification)
    assert specification.loader is not None
    specification.loader.exec_module(module)
    return module


class PreparedReleaseCancellationFixture:
    VALIDATOR_SELF_EXPIRY_SECONDS = 2.5
    DESCENDANT_SELF_EXPIRY_SECONDS = 6.0
    WRAPPER_WAIT_SECONDS = 4.0
    SUPERVISOR_TEARDOWN_SECONDS = 2.0
    DESCENDANT_TERMINATION_SECONDS = 1.5

    def __init__(
        self,
        signal_number: int,
        *,
        inherited_blocked: bool = False,
        publish_output: bool = False,
    ) -> None:
        self.signal_number = signal_number
        self.inherited_blocked = inherited_blocked
        self.publish_output = publish_output
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name).resolve()
        self.repository = self.root / "repository"
        self.metadata_path = self.root / "started.json"
        self.descendant_ready_path = self.root / "descendant-ready"
        self.descendant_lock_path = self.root / "descendant.lock"
        self.forwarded_path = self.root / "forwarded"
        self.late_marker_path = self.root / "late-marker"
        self.output_path = self.root / "generated-receipt.json"
        self.wrapper: subprocess.Popen[str] | None = None
        self.process_record: dict[str, int | bool] | None = None
        self.cancellation_elapsed_seconds: float | None = None

        try:
            scripts = self.repository / "scripts"
            scripts.mkdir(parents=True)
            self.entrypoint, _ = install_prepared_release_driver(self.repository)
            (scripts / "validate_public_release.py").write_text(
                self._validator_source(),
                encoding="utf-8",
            )
            self.wrapper = self._start_wrapper()
            self.process_record = self._wait_for_metadata()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> PreparedReleaseCancellationFixture:
        return self

    def __exit__(self, *_exception: object) -> None:
        self.close()

    def _validator_source(self) -> str:
        descendant_source = (
            "import fcntl, os, signal, sys, time\n"
            "lock_path, ready_path = sys.argv[1:3]\n"
            "for candidate in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):\n"
            "    signal.signal(candidate, signal.SIG_IGN)\n"
            "with open(lock_path, 'a+b') as held_lock:\n"
            "    fcntl.flock(held_lock.fileno(), fcntl.LOCK_EX)\n"
            "    temporary_ready_path = ready_path + '.tmp'\n"
            "    with open(temporary_ready_path, 'w', encoding='utf-8') as ready:\n"
            "        ready.write('ready\\n')\n"
            "        ready.flush()\n"
            "        os.fsync(ready.fileno())\n"
            "    os.replace(temporary_ready_path, ready_path)\n"
            f"    deadline = time.monotonic() + {self.DESCENDANT_SELF_EXPIRY_SECONDS!r}\n"
            "    while time.monotonic() < deadline:\n"
            "        time.sleep(0.02)\n"
            "    os._exit(0)\n"
        )
        output_publication = ""
        if self.publish_output:
            output_publication = (
                "with open(generated_output_path, 'w', encoding='utf-8') as output:\n"
                "    json.dump({'status': 'generated'}, output)\n"
                "    output.flush()\n"
                "    os.fsync(output.fileno())\n"
            )

        return (
            "import json, os, signal, subprocess, sys, time\n"
            f"metadata_path = {str(self.metadata_path)!r}\n"
            f"descendant_ready_path = {str(self.descendant_ready_path)!r}\n"
            f"descendant_lock_path = {str(self.descendant_lock_path)!r}\n"
            f"forwarded_path = {str(self.forwarded_path)!r}\n"
            f"late_marker_path = {str(self.late_marker_path)!r}\n"
            f"generated_output_path = {str(self.output_path)!r}\n"
            f"descendant_source = {descendant_source!r}\n"
            "def cancel(received, _frame):\n"
            "    with open(forwarded_path, 'w', encoding='utf-8') as forwarded:\n"
            "        forwarded.write(str(received))\n"
            "        forwarded.flush()\n"
            "        os.fsync(forwarded.fileno())\n"
            "    raise SystemExit(51)\n"
            "for candidate in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):\n"
            "    signal.signal(candidate, cancel)\n"
            "descendant = subprocess.Popen([\n"
            "    sys.executable,\n"
            "    '-I',\n"
            "    '-B',\n"
            "    '-c',\n"
            "    descendant_source,\n"
            "    descendant_lock_path,\n"
            "    descendant_ready_path,\n"
            "])\n"
            "readiness_deadline = time.monotonic() + 1.0\n"
            "while not os.path.isfile(descendant_ready_path):\n"
            "    if descendant.poll() is not None:\n"
            "        raise SystemExit('descendant exited before acquiring its lock')\n"
            "    if time.monotonic() >= readiness_deadline:\n"
            "        raise SystemExit('descendant did not acquire its lock')\n"
            "    time.sleep(0.01)\n"
            + output_publication
            + "current_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())\n"
            "metadata = {\n"
            "    'leader_pid': os.getpid(),\n"
            "    'leader_parent_pid': os.getppid(),\n"
            "    'leader_pgid': os.getpgrp(),\n"
            "    'leader_session': os.getsid(0),\n"
            "    'descendant_pid': descendant.pid,\n"
            "    'descendant_pgid': os.getpgid(descendant.pid),\n"
            "    'descendant_session': os.getsid(descendant.pid),\n"
            "    'cancellation_signal_unblocked': "
            f"{int(self.signal_number)!r} not in current_mask,\n"
            "}\n"
            "temporary_metadata_path = metadata_path + '.tmp'\n"
            "with open(temporary_metadata_path, 'w', encoding='utf-8') as output:\n"
            "    json.dump(metadata, output, sort_keys=True)\n"
            "    output.flush()\n"
            "    os.fsync(output.fileno())\n"
            "os.replace(temporary_metadata_path, metadata_path)\n"
            f"deadline = time.monotonic() + {self.VALIDATOR_SELF_EXPIRY_SECONDS!r}\n"
            "while time.monotonic() < deadline:\n"
            "    time.sleep(0.02)\n"
            "with open(late_marker_path, 'w', encoding='utf-8') as late_marker:\n"
            "    late_marker.write('late\\n')\n"
        )

    def _start_wrapper(self) -> subprocess.Popen[str]:
        popen_options: dict[str, object] = {}
        if self.inherited_blocked:

            def block_cancellation_signal() -> None:
                signal.pthread_sigmask(signal.SIG_BLOCK, {self.signal_number})

            popen_options["preexec_fn"] = block_cancellation_signal

        return subprocess.Popen(
            [
                "/bin/sh",
                str(self.entrypoint),
                "public-release",
                sys.executable,
                str(self.repository),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            **popen_options,
        )

    def _wait_for_metadata(self) -> dict[str, int | bool]:
        assert self.wrapper is not None
        readiness_deadline = time.monotonic() + 5.0
        while not self.metadata_path.is_file():
            if self.wrapper.poll() is not None:
                diagnostic = (
                    self.wrapper.stderr.read()
                    if self.wrapper.stderr is not None
                    else ""
                )
                raise AssertionError(
                    "prepared release wrapper exited before validation started: "
                    + diagnostic
                )
            if time.monotonic() >= readiness_deadline:
                raise AssertionError("prepared release validation did not start")
            time.sleep(0.01)
        return json.loads(self.metadata_path.read_text(encoding="utf-8"))

    def cancel(self) -> int:
        assert self.wrapper is not None
        if self.wrapper.poll() is not None:
            diagnostic = (
                self.wrapper.stderr.read() if self.wrapper.stderr is not None else ""
            )
            raise AssertionError(
                "prepared release wrapper exited before cancellation: " + diagnostic
            )
        cancellation_started = time.monotonic()
        os.kill(self.wrapper.pid, self.signal_number)
        returncode = self.wrapper.wait(timeout=self.WRAPPER_WAIT_SECONDS)
        self.cancellation_elapsed_seconds = time.monotonic() - cancellation_started
        return returncode

    def forwarded_signal(self) -> int:
        return int(self.forwarded_path.read_text(encoding="utf-8"))

    def wait_for_leader_reap(self, timeout_seconds: float = 1.0) -> bool:
        assert self.process_record is not None
        leader_pid = int(self.process_record["leader_pid"])
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                os.kill(leader_pid, 0)
            except ProcessLookupError:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)

    def wait_for_descendant_termination(
        self,
        timeout_seconds: float = DESCENDANT_TERMINATION_SECONDS,
    ) -> bool:
        assert self.process_record is not None
        return self._wait_for_descendant_lock(timeout_seconds)

    def _wait_for_descendant_lock(self, timeout_seconds: float) -> bool:
        lock_descriptor = os.open(
            self.descendant_lock_path,
            os.O_RDWR,
        )
        try:
            deadline = time.monotonic() + timeout_seconds
            while True:
                try:
                    fcntl.flock(
                        lock_descriptor,
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                    return True
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        return False
                    time.sleep(0.01)
        finally:
            os.close(lock_descriptor)

    def close(self) -> None:
        wrapper = self.wrapper
        if wrapper is not None and wrapper.poll() is None:
            try:
                os.kill(wrapper.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                wrapper.wait(timeout=self.SUPERVISOR_TEARDOWN_SECONDS)
            except subprocess.TimeoutExpired:
                if wrapper.poll() is None:
                    try:
                        os.kill(wrapper.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                try:
                    wrapper.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass

        if self.descendant_ready_path.is_file():
            self._wait_for_descendant_lock(self.DESCENDANT_SELF_EXPIRY_SECONDS + 0.5)
        if wrapper is not None and wrapper.stderr is not None:
            wrapper.stderr.close()
        self.temporary_directory.cleanup()


def plugin_eval_report(
    deductions: list[dict],
    target: Path,
    *,
    trigger: int = 313,
    invoke: int = 3071,
    deferred: int = 58894,
    component_tokens: int = 38792,
    required_components: list[tuple[str, str, int]] | None = None,
) -> dict:
    normalized_deductions = []
    checks = []
    for item in deductions:
        deduction = {
            "category": "budget",
            "id": "unclassified",
            "message": "documented budget warning",
            "penalty": 1,
            "remediation": ["Document the issue."],
            "severity": "warning",
            "source": "core",
            "status": "warn",
            **item,
        }
        normalized_deductions.append(deduction)
        checks.append(
            {key: value for key, value in deduction.items() if key != "penalty"}
            | {"evidence": ["fixture"]}
        )
    count = {"pass": 0, "warn": 0, "fail": 0, "info": 0, "error": 0, "warning": 0}
    for check in checks:
        count[check["status"]] += 1
        count[check["severity"]] += 1
    if required_components is None:
        required_components = [
            (
                "skills/checkpointing-and-publishing-git-work/scripts/check_eval_gate.py",
                "skills/checkpointing-and-publishing-git-work/scripts/check_eval_gate.py",
                component_tokens,
            )
        ]
    deferred_components = [
        {
            "label": label,
            "path": str(target / relative_path),
            "tokens": tokens,
            "note": "Deferred supporting file",
        }
        for label, relative_path, tokens in required_components
    ]
    required_tokens = sum(tokens for _, _, tokens in required_components)
    if deferred != required_tokens:
        deferred_components.append(
            {
                "label": "other",
                "path": str(target / "README.md"),
                "tokens": deferred - required_tokens,
                "note": "Deferred supporting file",
            }
        )

    def budget(value: int, thresholds: dict, components: list[dict]) -> dict:
        band = (
            "good"
            if value <= thresholds["goodMax"]
            else "moderate"
            if value <= thresholds["moderateMax"]
            else "heavy"
            if value <= thresholds["heavyMax"]
            else "excessive"
        )
        return {
            "value": value,
            "band": band,
            "thresholds": thresholds,
            "components": components,
        }

    total_penalty = sum(item["penalty"] for item in normalized_deductions)
    return {
        "schemaVersion": 1,
        "tool": {"name": "plugin-eval", "version": "0.1.0"},
        "createdAt": "2026-07-21T00:00:00Z",
        "target": {
            "kind": "plugin",
            "path": str(target),
            "entryPath": str(target / "plugin.json"),
            "name": target.name,
            "relativePath": f"plugins/{target.name}",
        },
        "checks": checks,
        "summary": {
            "score": round(100 - total_penalty),
            "grade": "D",
            "riskLevel": "high",
            "checkCounts": {"total": len(checks), **count},
            "scoreBreakdown": {
                "startingScore": 100,
                "totalDeductions": total_penalty,
                "finalScore": round(100 - total_penalty),
            },
            "deductions": normalized_deductions,
        },
        "budgets": {
            "trigger_cost_tokens": budget(
                trigger,
                {"goodMax": 66, "moderateMax": 254, "heavyMax": 614},
                [
                    {
                        "label": "trigger",
                        "path": str(target / "plugin.json"),
                        "tokens": trigger,
                        "note": "Fixture",
                    }
                ],
            ),
            "invoke_cost_tokens": budget(
                invoke,
                {"goodMax": 462, "moderateMax": 4493, "heavyMax": 17204},
                [
                    {
                        "label": "invoke",
                        "path": str(target / "plugin.json"),
                        "tokens": invoke,
                        "note": "Fixture",
                    }
                ],
            ),
            "deferred_cost_tokens": {
                "value": deferred,
                "band": "excessive",
                "thresholds": {"goodMax": 27, "moderateMax": 7622, "heavyMax": 58894},
                "components": deferred_components,
            },
        },
    }


class ValidatePublicReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_validator_module()
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository = Path(self.temporary_directory.name).resolve() / "repository"
        for relative in self.module.all_scope_paths():
            source = REPOSITORY / relative
            destination = self.repository / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, destination, symlinks=True)
            else:
                shutil.copy2(source, destination, follow_symlinks=False)
        baseline_relative = Path("release/plugin-eval-baseline-v1.json")
        baseline_destination = self.repository / baseline_relative
        if not baseline_destination.exists():
            baseline_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPOSITORY / baseline_relative, baseline_destination)
        self.plugin_eval_root = (
            Path(self.temporary_directory.name).resolve() / "content-addressed-runtime"
        )
        self.plugin_eval_root.mkdir()
        (self.plugin_eval_root / ".codex-plugin").mkdir()
        (self.plugin_eval_root / ".codex-plugin/plugin.json").write_text(
            json.dumps({"name": "plugin-eval", "version": "0.1.2"}),
            encoding="utf-8",
        )
        (self.plugin_eval_root / "package.json").write_text(
            json.dumps(
                {
                    "name": "plugin-eval",
                    "version": "0.1.0",
                    "bin": {"plugin-eval": "./scripts/plugin-eval.js"},
                }
            ),
            encoding="utf-8",
        )
        (self.plugin_eval_root / "scripts").mkdir()
        (self.plugin_eval_root / "scripts/plugin-eval.js").write_text(
            "#!/usr/bin/env node\n",
            encoding="utf-8",
        )
        (self.plugin_eval_root / "src").mkdir()
        (self.plugin_eval_root / "src/index.js").write_text(
            "export {};\n", encoding="utf-8"
        )
        self.plugin_eval = self.plugin_eval_root / "scripts/plugin-eval.js"
        self.plugin_eval_original = self.plugin_eval.read_bytes()
        self.plugin_eval_manifest = self.plugin_eval_root / ".codex-plugin/plugin.json"
        self.plugin_eval_manifest_original = self.plugin_eval_manifest.read_bytes()
        policy_path = self.repository / "release/plugin-eval-policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["tool"]["plugin_manifest_sha256"] = (
            "sha256:" + hashlib.sha256(self.plugin_eval_manifest_original).hexdigest()
        )
        policy["tool"]["runtime_tree_sha256"] = self.module.runtime_tree_digest(
            self.plugin_eval_root,
            tuple(policy["tool"]["runtime_paths"]),
        )
        baseline_path = self.repository / policy["calibration"]["manifest_path"]
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        baseline["plugin_eval_runtime_sha256"] = policy["tool"]["runtime_tree_sha256"]
        baseline["manifest_sha256"] = self.module.canonical_digest(
            {key: value for key, value in baseline.items() if key != "manifest_sha256"}
        )
        baseline_path.write_bytes(self.module.canonical_document(baseline))
        policy["calibration"]["manifest_sha256"] = baseline["manifest_sha256"]
        policy_path.write_bytes(self.module.canonical_document(policy))
        self.calibration_path = baseline_path
        self.calibration = baseline
        self.receipt = (
            Path(self.temporary_directory.name).resolve() / "release-receipt.json"
        )
        subprocess.run(
            ["git", "init", "--quiet", str(self.repository)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repository), "config", "maintenance.auto", "false"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repository),
                "remote",
                "add",
                "origin",
                self.module.CANONICAL_REPOSITORY_URL,
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repository), "add", "--all"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repository),
                "-c",
                "user.name=Release Test",
                "-c",
                "user.email=release-test@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "test: freeze release candidate",
            ],
            check=True,
            capture_output=True,
        )
        private_root = (
            Path(self.temporary_directory.name).resolve() / "private-v4-public"
        )
        private_fixture = fixture.build_public_verifier_fixture(
            private_root,
            self.module.compatibility_bytes(self.repository),
        )
        self.private_producer_registry = private_fixture["registry_path"]
        self.private_producer_witness = private_fixture["witness_path"]
        replay = private_fixture["summary"]
        transient = replay["conformance"]
        opaque_conformance_sha256 = self.module.canonical_digest(transient)
        replay["conformance"] = {
            "sha256": opaque_conformance_sha256,
            "sealed_root_count": len(transient["sealed_request_roots"]),
            "read_count": len(transient["read_inventory"]),
            "probe_count": len(transient["probe_results"]),
        }
        conformance_binding_sha256 = self.module.canonical_digest(replay["conformance"])
        replay["runtime_isolation"]["conformance_sha256"] = conformance_binding_sha256

        def fixture_digest(label: str) -> str:
            return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()

        replay["runtime_isolation"]["backend"] = {
            "target": "macos-seatbelt",
            "binary": "sandbox-exec",
            "version": "macos-seatbelt-v1",
            "sha256": "sha256:8290e4be7387a0df83cd1559e86afd880464f269450573d012795761fe298f16",
            "version_sha256": fixture_digest("macos-version"),
        }
        replay["runtime_isolation"]["host_identity_sha256"] = fixture_digest(
            "macos-host"
        )
        replay["runtime_isolation"]["kernel_identity_sha256"] = fixture_digest(
            "macos-kernel"
        )
        replay["frozen_identity_sha256"] = fixture.frozen_identity_sha256(replay)
        replay_unsigned = {
            key: value for key, value in replay.items() if key != "summary_sha256"
        }
        replay["summary_sha256"] = self.module.canonical_digest(replay_unsigned)
        fixture.write_private(
            private_fixture["summary_path"], fixture.json_file_bytes(replay)
        )
        public_candidate_identity = self.module.candidate_content_identity(
            self.repository,
            error_factory=self.module.ReleaseError,
        )
        self.private_provenance_arguments = {
            "private_producer_witness": self.private_producer_witness,
            "private_producer_registry": self.private_producer_registry,
            "expected_frozen_private_identity_sha256": (
                replay["frozen_identity_sha256"]
            ),
            "expected_private_commit_oid": replay["private_commit_oid"],
            "expected_private_producer_package_sha256": replay[
                "producer_package_sha256"
            ],
            "expected_public_candidate_sha256": public_candidate_identity,
        }
        backend_evidence = {
            "schema_version": 2,
            "contract": "phase7-public-backend-release-evidence-v2",
            "public_candidate_identity": public_candidate_identity,
            "targets": [
                {
                    "schema_version": 2,
                    "contract": "phase7-public-terminal-direct-proof-v2",
                    "target": "macos-seatbelt",
                    "binary": "sandbox-exec",
                    "version": "macos-seatbelt-v1",
                    "binary_sha256": "sha256:8290e4be7387a0df83cd1559e86afd880464f269450573d012795761fe298f16",
                    "version_sha256": fixture_digest("macos-version"),
                    "policy_sha256": replay["runtime_isolation"]["policy_sha256"],
                    "capability_manifest_sha256": replay["runtime_isolation"][
                        "capability_manifest_sha256"
                    ],
                    "public_candidate_identity": public_candidate_identity,
                    "host_identity_sha256": replay["runtime_isolation"][
                        "host_identity_sha256"
                    ],
                    "kernel_identity_sha256": replay["runtime_isolation"][
                        "kernel_identity_sha256"
                    ],
                    "conformance_sha256": conformance_binding_sha256,
                    "terminal_direct_proof_sha256": fixture_digest(
                        "macos-terminal-proof"
                    ),
                },
                {
                    "schema_version": 2,
                    "contract": "phase7-public-terminal-direct-proof-v2",
                    "target": "linux-bubblewrap",
                    "binary": "bwrap",
                    "version": "bubblewrap-v1",
                    "binary_sha256": "sha256:85580dd52ed366ece8844e90fa75ac7c4de8802963071344e123221fb9f6f11e",
                    "version_sha256": "sha256:9d3b32565ddaece919cfc7d8fed50f5fe2a9ac9529cfbe9067c5eda7ccf0c530",
                    "policy_sha256": fixture_digest("linux-policy"),
                    "capability_manifest_sha256": fixture_digest("linux-capability"),
                    "public_candidate_identity": public_candidate_identity,
                    "host_identity_sha256": fixture_digest("non-final-linux-host"),
                    "kernel_identity_sha256": fixture_digest("non-final-linux-kernel"),
                    "conformance_sha256": fixture_digest("non-final-linux-conformance"),
                    "terminal_direct_proof_sha256": fixture_digest(
                        "non-final-linux-terminal-proof"
                    ),
                },
                {
                    "schema_version": 2,
                    "contract": "phase7-public-terminal-direct-proof-v2",
                    "target": "wsl2-bubblewrap",
                    "binary": "bwrap",
                    "version": "bubblewrap-wsl2-v1",
                    "binary_sha256": fixture_digest("test-only-wsl2-binary"),
                    "version_sha256": fixture_digest("test-only-wsl2-version"),
                    "policy_sha256": fixture_digest("wsl2-policy"),
                    "capability_manifest_sha256": fixture_digest("wsl2-capability"),
                    "public_candidate_identity": public_candidate_identity,
                    "host_identity_sha256": fixture_digest("test-only-wsl2-host"),
                    "kernel_identity_sha256": fixture_digest("test-only-wsl2-kernel"),
                    "conformance_sha256": fixture_digest("test-only-wsl2-conformance"),
                    "terminal_direct_proof_sha256": fixture_digest(
                        "test-only-wsl2-terminal-proof"
                    ),
                },
            ],
        }
        for proof in backend_evidence["targets"]:
            terminal_payload = {
                key: value
                for key, value in proof.items()
                if key != "terminal_direct_proof_sha256"
            }
            proof["terminal_direct_proof_sha256"] = self.module.canonical_digest(
                terminal_payload
            )
        self.backend_release_evidence = (
            Path(self.temporary_directory.name) / "backend-evidence.json"
        )
        self.backend_release_evidence.write_bytes(
            self.module.canonical_document(backend_evidence)
        )
        self.private_provenance_arguments |= {
            "backend_release_evidence": self.backend_release_evidence,
            "expected_backend_release_evidence_sha256": "sha256:"
            + hashlib.sha256(self.backend_release_evidence.read_bytes()).hexdigest(),
        }
        self.composed_artifact = (
            Path(self.temporary_directory.name).resolve() / "lifecycle-dispatch.log"
        )
        self.composed_artifact.write_bytes(b"provider-free fixture\n")
        second_artifact = self.composed_artifact.with_name("tricritical-contract.log")
        second_artifact.write_bytes(b"contract fixture\n")
        composed_unsigned = {
            "schema_version": 7,
            "contract": self.module.COMPOSED_CONTRACT,
            "claim": self.module.COMPOSED_CLAIM,
            "public_candidate_identity": public_candidate_identity,
            "private_commit_oid": replay["private_commit_oid"],
            "private_candidate_identity": replay["private_candidate_identity"],
            "producer_package_sha256": replay["producer_package_sha256"],
            "producer_registry_sha256": replay["producer_registry_sha256"],
            "producer_witness_sha256": replay["producer_witness_sha256"],
            "private_receipt_sha256": replay["private_receipt_sha256"],
            "private_trust_anchor_sha256": replay["private_trust_anchor_sha256"],
            "frozen_identity_sha256": replay["frozen_identity_sha256"],
            "private_evidence_bundle_sha256": replay["private_evidence_bundle_sha256"],
            "private_replay_payload_sha256": fixture.replay_payload_sha256(replay),
            "private_replay_summary_sha256": replay["summary_sha256"],
            "compatibility_sha256": replay["compatibility_sha256"],
            "checks": replay["checks"],
            "role_payloads": replay["role_payloads"],
            "runtime_isolation": replay["runtime_isolation"],
            "conformance": replay["conformance"],
            "records": [
                {
                    "family": "lifecycle-dispatch",
                    "returncode": 0,
                    "artifact": self.composed_artifact.name,
                    "artifact_sha256": "sha256:"
                    + hashlib.sha256(self.composed_artifact.read_bytes()).hexdigest(),
                },
                {
                    "family": "tricritical-contract",
                    "returncode": 0,
                    "artifact": second_artifact.name,
                    "artifact_sha256": "sha256:"
                    + hashlib.sha256(second_artifact.read_bytes()).hexdigest(),
                },
            ],
            "passed": True,
        }
        self.composed_receipt = self.composed_artifact.with_name(
            "phase7-composed-matrix.json"
        )
        self.composed_receipt.write_bytes(
            self.module.canonical_document(
                composed_unsigned
                | {"receipt_sha256": self.module.canonical_digest(composed_unsigned)}
            )
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def assume_private_provenance_verified(self):
        """Isolate downstream gates from the intentionally retained v4 evidence."""

        return mock.patch.object(
            self.module,
            "verify_public_release_evidence",
            autospec=True,
        )

    def run_plugin_reports(self, factory):
        def result(command, **_kwargs):
            if command[1:] == ["--version"]:
                return subprocess.CompletedProcess(command, 0, "v20.0.0\n", "")
            return subprocess.CompletedProcess(
                command, 0, json.dumps(factory(Path(command[3]))), ""
            )

        with mock.patch.object(
            self.module.subprocess,
            "run",
            side_effect=result,
        ):
            return self.module.run_plugin_evals(self.repository, self.plugin_eval)

    def install_real_plugin_eval_fixture(self) -> None:
        """Install a small real executable that emits an otherwise-valid report."""

        self.plugin_eval.write_text(
            "const path = require('node:path');\n"
            "const target = path.resolve(process.argv[3]);\n"
            "const name = path.basename(target);\n"
            "const report = {\n"
            "  schemaVersion: 1,\n"
            "  tool: {name: 'plugin-eval', version: '0.1.0'},\n"
            "  target: {kind: 'plugin', path: target, "
            "entryPath: path.join(target, 'plugin.json'), "
            "name, relativePath: 'plugins/' + name},\n"
            "  checks: [],\n"
            "  summary: {deductions: []},\n"
            "  budgets: Object.fromEntries(['trigger_cost_tokens', "
            "'invoke_cost_tokens', 'deferred_cost_tokens'].map((name) => "
            "[name, {value: 0, components: []}])),\n"
            "};\n"
            "console.log(JSON.stringify(report));\n",
            encoding="utf-8",
        )
        os.chmod(self.plugin_eval, 0o755)
        policy_path = self.repository / "release/plugin-eval-policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["tool"]["runtime_tree_sha256"] = self.module.runtime_tree_digest(
            self.plugin_eval_root,
            tuple(policy["tool"]["runtime_paths"]),
        )
        baseline_path = self.repository / policy["calibration"]["manifest_path"]
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        baseline["plugin_eval_runtime_sha256"] = policy["tool"]["runtime_tree_sha256"]
        baseline["manifest_sha256"] = self.module.canonical_digest(
            {key: value for key, value in baseline.items() if key != "manifest_sha256"}
        )
        baseline_path.write_bytes(self.module.canonical_document(baseline))
        policy["calibration"]["manifest_sha256"] = baseline["manifest_sha256"]
        policy_path.write_bytes(self.module.canonical_document(policy))

    def store_calibration(
        self,
        calibration: dict,
        *,
        refresh_manifest_digest: bool = True,
        refresh_policy_digest: bool = True,
    ) -> None:
        if refresh_manifest_digest:
            calibration["manifest_sha256"] = self.module.canonical_digest(
                {
                    key: value
                    for key, value in calibration.items()
                    if key != "manifest_sha256"
                }
            )
        self.calibration_path.write_bytes(self.module.canonical_document(calibration))
        if refresh_policy_digest:
            policy_path = self.repository / "release/plugin-eval-policy.json"
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["calibration"]["manifest_sha256"] = calibration["manifest_sha256"]
            policy_path.write_bytes(self.module.canonical_document(policy))

    @staticmethod
    def routing_manifest(model: str = "claude-sonnet-5") -> dict:
        return {
            "schema_version": 3,
            "claim": "cooperative evidence only",
            "requested_model": model,
            "counts": {
                "cold_start": 21,
                "explicit_invocation": 21,
                "trigger": 71,
            },
            "records": [
                {
                    "init_model": model,
                    "assistant_models": [model],
                    "usage": {"input_tokens": 1, "output_tokens": 2},
                    "total_cost_usd": 0.25,
                }
            ],
        }

    @staticmethod
    def versionkeeping_advisory(component_tokens: int = 38792) -> dict:
        return {
            "id": "deferred_cost_tokens-budget-high",
            "category": "budget",
            "status": "fail",
            "severity": "error",
            "message": "deferred_cost_tokens is excessive relative to the current Codex baseline.",
            "penalty": 14,
            "remediation": [
                "Reduce repeated instruction text and move detail into deferred supporting files."
            ],
            "source": "core",
            "component_tokens": component_tokens,
        }

    @staticmethod
    def optional_legal_field_deductions(plugin: str) -> list[dict]:
        return [
            {
                "id": f"interface-missing-{field}",
                "category": "manifest",
                "status": "fail",
                "severity": "error",
                "message": f"plugin.json interface is missing `{field}`.",
                "penalty": 14,
                "remediation": [f"Add interface.{field} to plugin.json."],
                "source": "core",
                "targetPath": f"plugins/{plugin}",
            }
            for field in ("privacyPolicyURL", "termsOfServiceURL")
        ]

    def test_current_candidate_has_complete_marketplace_and_exact_identities(
        self,
    ) -> None:
        identities = self.module.validate_release(
            self.repository,
            run_contracts=False,
        )
        self.assertEqual(
            set(identities["plugins"]),
            set(self.module.SOURCE_STAGE_VALIDATED_PLUGINS),
        )
        self.module.validate_release(
            self.repository,
            expected=identities,
            run_contracts=False,
        )

    def test_rejects_expected_identity_mismatch(self) -> None:
        identities = self.module.validate_release(
            self.repository,
            run_contracts=False,
        )
        mismatched = json.loads(json.dumps(identities))
        mismatched["plugins"]["mergecraft"]["plugin_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            self.module.ReleaseError, "frozen release identity mismatch"
        ):
            self.module.validate_expected_identities(
                mismatched,
                identities,
                self.module.SOURCE_STAGE_VALIDATED_PLUGINS,
            )

    def test_rejects_boolean_expected_schema_version(self) -> None:
        identities = self.module.validate_release(
            self.repository,
            run_contracts=False,
        )
        malformed = json.loads(json.dumps(identities))
        malformed["schema_version"] = True
        with self.assertRaisesRegex(
            self.module.ReleaseError, "expected identity document schema drift"
        ):
            self.module.validate_expected_identities(
                malformed,
                identities,
                self.module.SOURCE_STAGE_VALIDATED_PLUGINS,
            )

    def test_strict_json_rejects_exponent_overflow_and_retains_booleans(self) -> None:
        with self.assertRaisesRegex(self.module.ReleaseError, "non-finite JSON value"):
            self.module.strict_json('{"probe": 1e999}', "strict boundary")
        self.assertEqual(
            self.module.strict_json('{"enabled": true}', "strict boundary"),
            {"enabled": True},
        )

    def public_release_registration_fixture(
        self,
        name: str = "fixture-runtime",
        *,
        production_eligible: bool = False,
    ) -> Path:
        root = Path(
            tempfile.mkdtemp(
                prefix="public-release-registration-",
                dir=self.temporary_directory.name,
            )
        )
        (root / "plugins" / name).mkdir(parents=True)
        (root / "release" / name).mkdir(parents=True)
        (root / "scripts").mkdir()
        (root / "tests").mkdir()
        (root / "scripts" / f"validate_{name.replace('-', '_')}.py").write_text(
            "#!/usr/bin/env python3\n", encoding="utf-8"
        )
        (root / "tests" / f"test_{name.replace('-', '_')}.py").write_text(
            "", encoding="utf-8"
        )
        registration = {
            "production_eligible": production_eligible,
            "schema_version": 1,
            "source_stage_validator_flags": [],
            "support_paths": [f"tests/test_{name.replace('-', '_')}.py"],
        }
        (root / "release" / name / "public-release-registration.json").write_text(
            json.dumps(registration, sort_keys=True) + "\n", encoding="utf-8"
        )
        (root / "release" / "public-release-runtime-packages.json").write_text(
            json.dumps(
                {"schema_version": 1, "runtime_packages": [name]}, sort_keys=True
            )
            + "\n",
            encoding="utf-8",
        )
        return root

    def test_public_release_registration_discovery_derives_runtime_inventory(
        self,
    ) -> None:
        root = self.public_release_registration_fixture()

        registrations = self.module.load_public_release_registrations(root)

        self.assertEqual(set(registrations), {"fixture-runtime"})
        registration = registrations["fixture-runtime"]
        self.assertEqual(
            json.loads(
                (
                    root / "release/fixture-runtime/public-release-registration.json"
                ).read_text(encoding="utf-8")
            ),
            {
                "production_eligible": False,
                "schema_version": 1,
                "source_stage_validator_flags": [],
                "support_paths": ["tests/test_fixture_runtime.py"],
            },
        )
        self.assertEqual(registration["name"], "fixture-runtime")
        self.assertEqual(registration["package_kind"], "runtime-package")
        self.assertEqual(
            registration["validator_path"], "scripts/validate_fixture_runtime.py"
        )
        self.assertEqual(
            registration["support_paths"],
            (
                "release/fixture-runtime/public-release-registration.json",
                "scripts/validate_fixture_runtime.py",
                "tests/test_fixture_runtime.py",
            ),
        )

    def test_registration_schema_rejection_precedes_fixture_package_validation(
        self,
    ) -> None:
        root = self.public_release_registration_fixture()
        registration_path = (
            root / "release/fixture-runtime/public-release-registration.json"
        )
        registration = json.loads(registration_path.read_text(encoding="utf-8"))
        registration["validator_path"] = "scripts/validate_success.py"
        registration_path.write_text(
            json.dumps(registration, sort_keys=True) + "\n", encoding="utf-8"
        )
        shutil.rmtree(root / "plugins/fixture-runtime")
        (root / "plugins/fixture-runtime").write_text("not a plugin directory\n")

        with self.assertRaisesRegex(
            self.module.ReleaseError, "registration schema drift"
        ):
            self.module.load_public_release_registrations(root)

    def test_runtime_package_catalog_rejects_mutated_boundaries(self) -> None:
        cases = (
            ("missing", lambda path: path.unlink(), "catalog is missing"),
            (
                "duplicate key",
                lambda path: path.write_text(
                    '{"runtime_packages":["fixture-runtime"],'
                    '"runtime_packages":["fixture-runtime"],"schema_version":1}\n'
                ),
                "duplicate JSON key",
            ),
            (
                "unknown field",
                lambda path: path.write_text(
                    '{"ambient":true,"runtime_packages":["fixture-runtime"],"schema_version":1}\n'
                ),
                "catalog schema drift",
            ),
            (
                "boolean version",
                lambda path: path.write_text(
                    '{"runtime_packages":["fixture-runtime"],"schema_version":true}\n'
                ),
                "catalog schema version drift",
            ),
            (
                "unsorted duplicate names",
                lambda path: path.write_text(
                    '{"runtime_packages":["zeta","fixture-runtime","zeta"],"schema_version":1}\n'
                ),
                "sorted unique names",
            ),
            (
                "invalid name",
                lambda path: path.write_text(
                    '{"runtime_packages":["Fixture_Runtime"],"schema_version":1}\n'
                ),
                "sorted unique names",
            ),
        )
        for label, mutate, message in cases:
            with self.subTest(label=label):
                root = self.public_release_registration_fixture()
                path = root / "release/public-release-runtime-packages.json"
                mutate(path)
                with self.assertRaisesRegex(self.module.ReleaseError, message):
                    self.module.load_public_release_runtime_packages(root)

    def test_runtime_package_catalog_rejects_a_symlink(self) -> None:
        root = self.public_release_registration_fixture()
        path = root / "release/public-release-runtime-packages.json"
        target = root / "runtime-package-catalog-target.json"
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)

        with self.assertRaisesRegex(
            self.module.ReleaseError, "catalog must be a regular file"
        ):
            self.module.load_public_release_runtime_packages(root)

    def test_runtime_package_catalog_rejects_missing_and_unexpected_registration(
        self,
    ) -> None:
        root = self.public_release_registration_fixture()
        (root / "release/fixture-runtime/public-release-registration.json").unlink()
        with self.assertRaisesRegex(self.module.ReleaseError, "catalog drift"):
            self.module.load_public_release_registrations(root)
        root = self.public_release_registration_fixture()
        extra = root / "release" / "extra-runtime"
        extra.mkdir()
        (extra / "public-release-registration.json").write_text("{}\n")
        with self.assertRaisesRegex(self.module.ReleaseError, "catalog drift"):
            self.module.load_public_release_registrations(root)

    def test_fresh_process_rejects_catalog_required_registration_before_help(
        self,
    ) -> None:
        root = self.public_release_registration_fixture()
        shutil.copytree(REPOSITORY / "scripts", root / "validator-scripts")
        validator = root / "validator-scripts/validate_public_release.py"
        (root / "release/fixture-runtime/public-release-registration.json").unlink()

        result = subprocess.run(
            [sys.executable, "-I", "-B", str(validator), "--help"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("registration catalog drift", result.stderr)

    def test_loaded_public_release_registration_inventory_derives_projection(
        self,
    ) -> None:
        runtime_packages = self.module._runtime_package_inventory(
            self.module.PUBLIC_RELEASE_REGISTRATIONS,
            production_only=False,
        )
        production_runtime_packages = self.module._runtime_package_inventory(
            self.module.PUBLIC_RELEASE_REGISTRATIONS,
            production_only=True,
        )

        self.assertEqual(self.module.REGISTERED_RUNTIME_PACKAGES, runtime_packages)
        self.assertEqual(
            self.module.PRODUCTION_RUNTIME_PACKAGES,
            production_runtime_packages,
        )
        self.assertEqual(
            self.module.SOURCE_STAGE_VALIDATED_PLUGINS,
            self.module.SKILL_PLUGINS + runtime_packages,
        )
        self.assertEqual(
            self.module.PRODUCTION_VALIDATED_PLUGINS,
            self.module.SKILL_PLUGINS + production_runtime_packages,
        )
        self.assertTrue(set(runtime_packages).isdisjoint(self.module.SKILL_PLUGINS))
        self.assertEqual(
            self.module.MARKETPLACE_PLUGINS,
            {
                plugin: f"./plugins/{plugin}"
                for plugin in (self.module.SKILL_PLUGINS + production_runtime_packages)
            },
        )
        for name, registration in self.module.PUBLIC_RELEASE_REGISTRATIONS.items():
            with self.subTest(name=name):
                if registration["production_eligible"]:
                    self.assertEqual(
                        self.module.MARKETPLACE_PLUGINS[name],
                        f"./plugins/{name}",
                    )
                else:
                    self.assertNotIn(name, self.module.MARKETPLACE_PLUGINS)
                self.assertIn(
                    registration["registration_path"], registration["support_paths"]
                )
                self.assertIn(
                    registration["validator_path"], registration["support_paths"]
                )
                self.assertEqual(
                    self.module.VALIDATOR_PATHS[name], registration["validator_path"]
                )
                self.assertEqual(
                    self.module.SOURCE_STAGE_VALIDATOR_FLAGS[name],
                    registration["source_stage_validator_flags"],
                )

    def test_public_release_registration_projection_is_immutable(self) -> None:
        runtime_package = next(iter(self.module.PUBLIC_RELEASE_REGISTRATIONS))
        registration = self.module.PUBLIC_RELEASE_REGISTRATIONS[runtime_package]
        with self.assertRaises(TypeError):
            self.module.PUBLIC_RELEASE_REGISTRATIONS[runtime_package] = registration
        with self.assertRaises(TypeError):
            registration["validator_path"] = "scripts/other.py"
        with self.assertRaises(TypeError):
            self.module.MARKETPLACE_PLUGINS["unexpected"] = "./plugins/unexpected"
        with self.assertRaises(TypeError):
            self.module.PLUGIN_SUPPORT_PATHS[runtime_package] = frozenset()
        with self.assertRaises(TypeError):
            self.module.VALIDATOR_PATHS[runtime_package] = "scripts/other.py"
        with self.assertRaises(TypeError):
            self.module.SOURCE_STAGE_VALIDATOR_FLAGS[runtime_package] = ()
        with self.assertRaises(AttributeError):
            self.module.PLUGIN_SUPPORT_PATHS[runtime_package].add("tests/other.py")

    def test_fixture_registration_loader_returns_mutable_records_before_projection(
        self,
    ) -> None:
        registrations = self.module.load_public_release_registrations(
            self.public_release_registration_fixture()
        )

        registrations["fixture-runtime"]["validator_path"] = "scripts/other.py"

        self.assertEqual(
            registrations["fixture-runtime"]["validator_path"], "scripts/other.py"
        )

    def test_runtime_package_projection_includes_only_production_eligible_registrations(
        self,
    ) -> None:
        for production_eligible in (False, True):
            with self.subTest(production_eligible=production_eligible):
                root = self.public_release_registration_fixture(
                    production_eligible=production_eligible
                )
                registrations = self.module.load_public_release_registrations(root)

                self.assertEqual(
                    self.module._runtime_package_inventory(
                        registrations,
                        production_only=False,
                    ),
                    ("fixture-runtime",),
                )
                self.assertEqual(
                    self.module._runtime_package_inventory(
                        registrations,
                        production_only=True,
                    ),
                    ("fixture-runtime",) if production_eligible else (),
                )

    def test_public_release_registration_discovery_rejects_mutated_boundaries(
        self,
    ) -> None:
        cases = (
            (
                "unknown schema field",
                lambda registration, root: registration.update({"ambient": True}),
                "schema drift",
            ),
            (
                "repeated support path",
                lambda registration, root: registration.update(
                    {"support_paths": ["tests/test_fixture_runtime.py"] * 2}
                ),
                "sorted and unique",
            ),
            (
                "boolean schema version",
                lambda registration, root: registration.update(
                    {"schema_version": True}
                ),
                "schema version drift",
            ),
            (
                "redundant package kind",
                lambda registration, root: registration.update(
                    {"package_kind": "runtime-package"}
                ),
                "schema drift",
            ),
            (
                "redundant package name",
                lambda registration, root: registration.update(
                    {"name": "fixture-runtime"}
                ),
                "schema drift",
            ),
            (
                "redundant validator path",
                lambda registration, root: registration.update(
                    {"validator_path": "scripts/validate_fixture_runtime.py"}
                ),
                "schema drift",
            ),
            (
                "non-boolean production eligibility",
                lambda registration, root: registration.update(
                    {"production_eligible": 1}
                ),
                "production eligibility is invalid",
            ),
            (
                "noncanonical support path",
                lambda registration, root: registration.update(
                    {"support_paths": ["tests//test_fixture_runtime.py"]}
                ),
                "canonical repository-relative",
            ),
            (
                "missing support path",
                lambda registration, root: registration.update(
                    {"support_paths": ["tests/missing.py"]}
                ),
                "support path is missing",
            ),
            (
                "automatic path listed by package",
                lambda registration, root: registration.update(
                    {
                        "support_paths": [
                            "release/fixture-runtime/public-release-registration.json",
                            "tests/test_fixture_runtime.py",
                        ]
                    }
                ),
                "redundantly list automatic",
            ),
            (
                "validator symlink",
                lambda registration, root: (
                    (root / "scripts" / "validator-target.py").write_text(
                        "", encoding="utf-8"
                    ),
                    (root / "scripts" / "validate_fixture_runtime.py").unlink(),
                    (root / "scripts" / "validate_fixture_runtime.py").symlink_to(
                        root / "scripts" / "validator-target.py"
                    ),
                ),
                "validator contains a symlink",
            ),
            (
                "support path with an intermediate symlink",
                lambda registration, root: (
                    (root / "tests" / "outside").mkdir(),
                    (root / "tests" / "outside" / "contained.py").write_text(
                        "", encoding="utf-8"
                    ),
                    (root / "tests" / "link").symlink_to(
                        root / "tests" / "outside", target_is_directory=True
                    ),
                    registration.update({"support_paths": ["tests/link/contained.py"]}),
                ),
                "support path contains a symlink: tests/link",
            ),
        )
        for label, mutate, message in cases:
            with self.subTest(label=label):
                root = self.public_release_registration_fixture()
                registration_path = (
                    root / "release/fixture-runtime/public-release-registration.json"
                )
                registration = json.loads(registration_path.read_text(encoding="utf-8"))
                mutate(registration, root)
                registration_path.write_text(
                    json.dumps(registration, sort_keys=True) + "\n", encoding="utf-8"
                )
                with self.assertRaisesRegex(self.module.ReleaseError, message):
                    self.module.load_public_release_registrations(root)

    def test_public_release_registration_discovery_derives_names_from_catalogued_directories(
        self,
    ) -> None:
        root = self.public_release_registration_fixture()
        duplicate = root / "release" / "second-runtime"
        duplicate.mkdir()
        (root / "plugins" / "second-runtime").mkdir()
        (root / "scripts" / "validate_second_runtime.py").write_text(
            "#!/usr/bin/env python3\n", encoding="utf-8"
        )
        (root / "tests" / "test_second_runtime.py").write_text("", encoding="utf-8")
        (duplicate / "public-release-registration.json").write_text(
            json.dumps(
                {
                    "production_eligible": False,
                    "schema_version": 1,
                    "source_stage_validator_flags": [],
                    "support_paths": ["tests/test_second_runtime.py"],
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        (root / "release/public-release-runtime-packages.json").write_text(
            '{"runtime_packages":["fixture-runtime","second-runtime"],"schema_version":1}\n',
            encoding="utf-8",
        )

        self.assertEqual(
            tuple(self.module.load_public_release_registrations(root)),
            ("fixture-runtime", "second-runtime"),
        )

    def test_release_rejects_new_discovered_registration_outside_its_frozen_inventory(
        self,
    ) -> None:
        name = "unreviewed-runtime"
        (self.repository / "plugins" / name).mkdir(parents=True)
        (self.repository / "release" / name).mkdir(parents=True)
        (self.repository / "scripts" / "validate_unreviewed_runtime.py").write_text(
            "#!/usr/bin/env python3\n", encoding="utf-8"
        )
        (
            self.repository / "release" / name / "public-release-registration.json"
        ).write_text(
            json.dumps(
                {
                    "production_eligible": False,
                    "schema_version": 1,
                    "source_stage_validator_flags": [],
                    "support_paths": [],
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            self.module.ReleaseError, "registration catalog drift"
        ):
            self.module.validate_release(self.repository, run_contracts=False)

    def test_production_registration_validation_rejects_missing_or_malformed_records(
        self,
    ) -> None:
        name, registration = next(
            iter(self.module.PUBLIC_RELEASE_REGISTRATIONS.items())
        )
        registration_path = self.repository / registration["registration_path"]
        original = registration_path.read_bytes()
        cases = (
            ("missing", lambda: registration_path.unlink(), "catalog drift"),
            (
                "malformed",
                lambda: registration_path.write_text(
                    '{"schema_version":1,"schema_version":1}',
                    encoding="utf-8",
                ),
                "duplicate JSON key",
            ),
        )
        for label, mutate, message in cases:
            with self.subTest(label=label, name=name):
                mutate()
                try:
                    with self.assertRaisesRegex(self.module.ReleaseError, message):
                        self.module.validate_public_release_registration_inventory(
                            self.repository
                        )
                finally:
                    registration_path.write_bytes(original)

    def test_rejects_content_preserving_source_aba_after_snapshot(self) -> None:
        marketplace = self.repository / ".claude-plugin/marketplace.json"
        original = marketplace.read_bytes()

        def rewrite_same_bytes() -> None:
            marketplace.write_bytes(original)

        with self.assertRaisesRegex(self.module.ReleaseError, "release input changed"):
            self.module.validate_release(
                self.repository,
                run_contracts=False,
                after_snapshot=rewrite_same_bytes,
            )

    def test_rejects_symlinked_release_input(self) -> None:
        readme = self.repository / "README.md"
        content = readme.read_text()
        readme.unlink()
        target = self.repository / "README-target.md"
        target.write_text(content)
        readme.symlink_to(target)
        with self.assertRaisesRegex(self.module.ReleaseError, "symlink"):
            self.module.validate_release(self.repository, run_contracts=False)

    def test_rejects_validator_mutation_of_private_snapshot(self) -> None:
        def mutate(snapshot: Path, _plugin_eval: Path | None = None) -> None:
            readme = snapshot / "README.md"
            readme.write_text(readme.read_text(encoding="utf-8") + "\nmutated\n")

        with (
            self.assume_private_provenance_verified(),
            mock.patch.object(
                self.module, "run_contract_validators", side_effect=mutate
            ),
            mock.patch.object(self.module, "validate_routing_evidence"),
            mock.patch.object(self.module, "run_plugin_evals", return_value={}),
        ):
            routing_evidence = Path(self.temporary_directory.name) / "routing-evidence"
            routing_evidence.mkdir()
            with self.assertRaisesRegex(
                self.module.ReleaseError, "private snapshot changed"
            ):
                self.module.validate_release(
                    self.repository,
                    routing_evidence=routing_evidence,
                    plugin_eval_executable=self.plugin_eval,
                    receipt_output=self.receipt,
                    composed_receipt=self.composed_receipt,
                    **self.private_provenance_arguments,
                )

    def test_production_release_requires_external_routing_evidence(self) -> None:
        with self.assertRaisesRegex(
            self.module.ReleaseError, "requires external skill-routing evidence"
        ):
            self.module.validate_release(self.repository)

        internal_evidence = self.repository / "routing-evidence"
        internal_evidence.mkdir()
        with self.assertRaisesRegex(
            self.module.ReleaseError, "outside the release repository"
        ):
            self.module.validate_release(
                self.repository,
                routing_evidence=internal_evidence,
                plugin_eval_executable=self.plugin_eval,
                receipt_output=self.receipt,
                composed_receipt=self.composed_receipt,
                **self.private_provenance_arguments,
            )

    def test_production_release_requires_pinned_plugin_eval_runtime(self) -> None:
        evidence = Path(self.temporary_directory.name) / "routing-evidence"
        evidence.mkdir()
        with self.assertRaisesRegex(
            self.module.ReleaseError, "requires a pinned plugin-eval executable"
        ):
            self.module.validate_release(
                self.repository,
                routing_evidence=evidence,
                receipt_output=self.receipt,
            )

    def test_production_release_requires_external_composed_receipt(self) -> None:
        evidence = Path(self.temporary_directory.name) / "routing-evidence"
        evidence.mkdir()
        with self.assertRaisesRegex(
            self.module.ReleaseError, "requires --composed-receipt"
        ):
            self.module.validate_release(
                self.repository,
                routing_evidence=evidence,
                plugin_eval_executable=self.plugin_eval,
                receipt_output=self.receipt,
            )

    def test_composed_receipt_rejects_artifact_and_public_source_drift(self) -> None:
        self.composed_artifact.write_bytes(b"drift\n")
        with self.assertRaisesRegex(
            self.module.ReleaseError, "composed receipt artifact drift"
        ):
            self.module.validate_composed_receipt(
                self.repository, self.composed_receipt
            )

    def test_composed_receipt_uses_one_compatibility_observation(self) -> None:
        compatibility_a = self.module.canonical_document({"observation": "A"})
        compatibility_b = self.module.canonical_document({"observation": "B"})
        receipt = json.loads(self.composed_receipt.read_text(encoding="utf-8"))
        replay = fixture.replay_summary(compatibility_a)
        for field in (
            "private_commit_oid",
            "private_candidate_identity",
            "producer_package_sha256",
            "producer_registry_sha256",
            "producer_witness_sha256",
            "private_receipt_sha256",
            "private_trust_anchor_sha256",
            "private_evidence_bundle_sha256",
            "checks",
            "role_payloads",
            "runtime_isolation",
            "conformance",
        ):
            replay[field] = receipt[field]
        replay["compatibility_sha256"] = (
            "sha256:" + hashlib.sha256(compatibility_b).hexdigest()
        )
        replay["frozen_identity_sha256"] = fixture.frozen_identity_sha256(replay)
        replay["summary_sha256"] = self.module.canonical_digest(
            {key: value for key, value in replay.items() if key != "summary_sha256"}
        )
        receipt |= {
            "compatibility_sha256": replay["compatibility_sha256"],
            "frozen_identity_sha256": replay["frozen_identity_sha256"],
            "private_replay_payload_sha256": fixture.replay_payload_sha256(replay),
            "private_replay_summary_sha256": replay["summary_sha256"],
        }
        unsigned = {
            key: value for key, value in receipt.items() if key != "receipt_sha256"
        }
        receipt["receipt_sha256"] = self.module.canonical_digest(unsigned)
        self.composed_receipt.write_bytes(self.module.canonical_document(receipt))

        with (
            mock.patch.object(
                self.module,
                "compatibility_bytes",
                side_effect=(compatibility_a, compatibility_b),
            ) as compatibility,
            self.assertRaisesRegex(
                self.module.ReleaseError,
                "composed receipt public compatibility mismatch",
            ),
        ):
            self.module.validate_composed_receipt(
                self.repository, self.composed_receipt
            )

        self.assertEqual(compatibility.call_count, 1)

    def test_release_rejects_frozen_field_mutations_with_only_composed_digest_resigned(
        self,
    ) -> None:
        mutations = {
            "private_receipt_sha256": "sha256:" + "f" * 64,
            "private_trust_anchor_sha256": "sha256:" + "e" * 64,
            "private_evidence_bundle_sha256": "sha256:" + "d" * 64,
            "private_candidate_identity": "c" * 64,
            "private_replay_payload_sha256": "sha256:" + "b" * 64,
        }
        original = json.loads(self.composed_receipt.read_text(encoding="utf-8"))
        for field, value in mutations.items():
            with self.subTest(field=field):
                composed = original | {field: value}
                unsigned = {
                    key: value
                    for key, value in composed.items()
                    if key != "receipt_sha256"
                }
                composed["receipt_sha256"] = self.module.canonical_digest(unsigned)
                self.composed_receipt.write_bytes(
                    self.module.canonical_document(composed)
                )
                with self.assertRaisesRegex(
                    self.module.ReleaseError, "binding reconstruction mismatch"
                ):
                    self.module.validate_composed_receipt(
                        self.repository, self.composed_receipt
                    )

                with self.assertRaisesRegex(
                    self.module.PrivateEvidenceError,
                    "reconstruction mismatch",
                ):
                    self.module.verify_public_release_evidence(
                        composed_receipt=composed,
                        producer_witness_path=self.private_producer_witness,
                        producer_registry_path=self.private_producer_registry,
                        expected_frozen_identity_sha256=(
                            self.private_provenance_arguments[
                                "expected_frozen_private_identity_sha256"
                            ]
                        ),
                        expected_commit_oid=self.private_provenance_arguments[
                            "expected_private_commit_oid"
                        ],
                        expected_producer_package_sha256=(
                            self.private_provenance_arguments[
                                "expected_private_producer_package_sha256"
                            ]
                        ),
                        public_root=self.repository,
                        expected_public_candidate_sha256=(
                            self.private_provenance_arguments[
                                "expected_public_candidate_sha256"
                            ]
                        ),
                    )

        self.composed_artifact.write_bytes(b"provider-free fixture\n")
        readme = self.repository / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8") + "\ndrift\n")
        with self.assertRaisesRegex(
            self.module.ReleaseError, "public candidate identity mismatch"
        ):
            self.module.validate_composed_receipt(
                self.repository, self.composed_receipt
            )

    def test_rejects_resigned_copied_replay_fields(self) -> None:
        original = json.loads(self.composed_receipt.read_text(encoding="utf-8"))
        mutations = []
        for index, role in enumerate(original["role_payloads"]):
            mutations.append(
                (
                    f"role_payloads[{role['role']}].sha256",
                    lambda receipt, index=index: receipt["role_payloads"][index].update(
                        {"sha256": "sha256:" + f"{index:x}" * 64}
                    ),
                )
            )
        mutations.extend(
            (
                (
                    "runtime_isolation.policy_sha256",
                    lambda receipt: receipt["runtime_isolation"].update(
                        {"policy_sha256": "sha256:" + "e" * 64}
                    ),
                ),
                (
                    "runtime_isolation.capability_manifest_sha256",
                    lambda receipt: receipt["runtime_isolation"].update(
                        {"capability_manifest_sha256": "sha256:" + "d" * 64}
                    ),
                ),
                (
                    "private_replay_summary_sha256",
                    lambda receipt: receipt.update(
                        {"private_replay_summary_sha256": "sha256:" + "c" * 64}
                    ),
                ),
            )
        )

        for label, mutate in mutations:
            with self.subTest(label=label):
                composed = json.loads(json.dumps(original))
                mutate(composed)
                unsigned = {
                    key: value
                    for key, value in composed.items()
                    if key != "receipt_sha256"
                }
                composed["receipt_sha256"] = self.module.canonical_digest(unsigned)
                self.composed_receipt.write_bytes(
                    self.module.canonical_document(composed)
                )

                with self.assertRaisesRegex(
                    self.module.ReleaseError, "replay.*binding"
                ):
                    self.module.validate_composed_receipt(
                        self.repository, self.composed_receipt
                    )
                with self.assertRaisesRegex(
                    self.module.PrivateEvidenceError, "replay.*binding"
                ):
                    self.module.verify_public_release_evidence(
                        composed_receipt=composed,
                        producer_witness_path=self.private_producer_witness,
                        producer_registry_path=self.private_producer_registry,
                        expected_frozen_identity_sha256=(
                            self.private_provenance_arguments[
                                "expected_frozen_private_identity_sha256"
                            ]
                        ),
                        expected_commit_oid=self.private_provenance_arguments[
                            "expected_private_commit_oid"
                        ],
                        expected_producer_package_sha256=(
                            self.private_provenance_arguments[
                                "expected_private_producer_package_sha256"
                            ]
                        ),
                        public_root=self.repository,
                        expected_public_candidate_sha256=(
                            self.private_provenance_arguments[
                                "expected_public_candidate_sha256"
                            ]
                        ),
                    )

    def test_production_rejects_registry_or_witness_drift(self) -> None:
        evidence = Path(self.temporary_directory.name) / "routing-evidence"
        evidence.mkdir()
        for path in (
            self.private_producer_registry,
            self.private_producer_witness,
        ):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(original + b"drift")
                try:
                    with self.assertRaisesRegex(
                        self.module.ReleaseError,
                        "registry/witness digest mismatch|registry contract drift|invalid JSON",
                    ):
                        self.module.validate_release(
                            self.repository,
                            routing_evidence=evidence,
                            plugin_eval_executable=self.plugin_eval,
                            receipt_output=self.receipt,
                            composed_receipt=self.composed_receipt,
                            **self.private_provenance_arguments,
                        )
                finally:
                    path.write_bytes(original)

    def test_production_release_requires_absolute_external_receipt_output(self) -> None:
        evidence = Path(self.temporary_directory.name) / "routing-evidence"
        evidence.mkdir()
        for output, message in (
            (None, "requires --receipt-output"),
            (Path("receipt.json"), "absolute path"),
            (self.repository / "receipt.json", "outside the release repository"),
        ):
            with (
                self.subTest(output=output),
                self.assertRaisesRegex(self.module.ReleaseError, message),
            ):
                self.module.validate_release(
                    self.repository,
                    routing_evidence=evidence,
                    plugin_eval_executable=self.plugin_eval,
                    receipt_output=output,
                )

    def test_git_candidate_identity_binds_head_tree_archive_and_cleanliness(
        self,
    ) -> None:
        repository = Path(self.temporary_directory.name) / "git-candidate"
        repository.mkdir()

        def git(*arguments: str, capture_output: bool = False):
            return subprocess.run(
                ["git", *arguments],
                cwd=repository,
                check=True,
                capture_output=capture_output,
            )

        git("init", "--quiet")
        git("config", "maintenance.auto", "false")
        git(
            "remote",
            "add",
            "origin",
            self.module.CANONICAL_REPOSITORY_URL + ".git",
        )
        (repository / "candidate.txt").write_text("frozen\n", encoding="utf-8")
        git("add", "candidate.txt")
        git(
            "-c",
            "user.name=Release Test",
            "-c",
            "user.email=release-test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "test: freeze candidate",
        )

        revision = git("rev-parse", "HEAD", capture_output=True).stdout.decode().strip()
        tree_oid = (
            git("rev-parse", "HEAD^{tree}", capture_output=True).stdout.decode().strip()
        )
        archive = git("archive", "--format=tar", revision, capture_output=True).stdout
        self.assertEqual(
            self.module.git_candidate_identity(repository),
            {
                "revision": revision,
                "tree_oid": tree_oid,
                "archive_sha256": "sha256:" + hashlib.sha256(archive).hexdigest(),
                "repository": self.module.CANONICAL_REPOSITORY_URL,
            },
        )

        for alias in (
            self.module.CANONICAL_REPOSITORY_URL,
            "git@github.com:nisavid/agents.git",
            "ssh://git@github.com/nisavid/agents.git",
        ):
            with self.subTest(alias=alias):
                git("remote", "set-url", "origin", alias)
                self.assertEqual(
                    self.module.git_candidate_identity(repository)["repository"],
                    self.module.CANONICAL_REPOSITORY_URL,
                )

        git("remote", "set-url", "origin", "https://github.com/fork/agents.git")
        with self.assertRaisesRegex(self.module.ReleaseError, "not nisavid/agents"):
            self.module.git_candidate_identity(repository)
        git("remote", "set-url", "origin", self.module.CANONICAL_REPOSITORY_URL)

        (repository / "candidate.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(self.module.ReleaseError, "clean Git checkout"):
            self.module.git_candidate_identity(repository)

    def test_public_candidate_identity_uses_bare_lowercase_sha256(self) -> None:
        bare = "a" * 64

        self.assertEqual(self.module.parse_public_candidate_sha256(bare), bare)
        for malformed in (
            "sha256:" + bare,
            "A" * 64,
            "a" * 63,
            "a" * 65,
        ):
            with (
                self.subTest(malformed=malformed),
                self.assertRaises(self.module.argparse.ArgumentTypeError),
            ):
                self.module.parse_public_candidate_sha256(malformed)

    def test_production_release_rejects_a_different_prepared_git_candidate(
        self,
    ) -> None:
        expected_candidate = self.module.git_candidate_identity(self.repository)
        expected_candidate["revision"] = "0" * len(expected_candidate["revision"])
        evidence_root = Path(self.temporary_directory.name) / "routing-evidence"
        evidence_root.mkdir()

        with self.assertRaisesRegex(
            self.module.ReleaseError,
            "Git candidate differs from the prepared Phase 7 candidate",
        ):
            self.module.validate_release(
                self.repository,
                routing_evidence=evidence_root,
                plugin_eval_executable=self.plugin_eval,
                receipt_output=self.receipt,
                composed_receipt=self.composed_receipt,
                expected_git_candidate=expected_candidate,
                **self.private_provenance_arguments,
            )

    def test_source_stage_only_runtime_change_updates_git_candidate_not_production_identity(
        self,
    ) -> None:
        source_stage_only = tuple(
            name
            for name, registration in self.module.PUBLIC_RELEASE_REGISTRATIONS.items()
            if not registration["production_eligible"]
        )
        self.assertTrue(source_stage_only)
        package = source_stage_only[0]
        candidate_before = self.module.git_candidate_identity(self.repository)
        production_identity_before = self.module.candidate_identities(
            self.repository,
            self.module.PRODUCTION_VALIDATED_PLUGINS,
        )
        manifest = self.repository / "plugins" / package / "plugin.json"
        manifest.write_bytes(manifest.read_bytes() + b"\n")
        subprocess.run(
            ["git", "add", manifest.relative_to(self.repository).as_posix()],
            cwd=self.repository,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Release Test",
                "-c",
                "user.email=release-test@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "test: change source-stage-only runtime",
            ],
            cwd=self.repository,
            check=True,
            capture_output=True,
        )

        candidate_after = self.module.git_candidate_identity(self.repository)
        production_identity_after = self.module.candidate_identities(
            self.repository,
            self.module.PRODUCTION_VALIDATED_PLUGINS,
        )

        self.assertNotEqual(candidate_after, candidate_before)
        self.assertEqual(production_identity_after, production_identity_before)

    def test_production_release_propagates_exact_candidate_to_routing_validator(
        self,
    ) -> None:
        evidence_root = Path(self.temporary_directory.name) / "routing-evidence"
        evidence_root.mkdir()
        candidate = self.module.git_candidate_identity(self.repository)
        validate_evidence = mock.Mock(return_value=self.routing_manifest())
        evaluator = types.SimpleNamespace(
            RoutingError=type("RoutingError", (RuntimeError,), {}),
            validate_evidence=validate_evidence,
        )
        with (
            self.assume_private_provenance_verified(),
            mock.patch.object(
                self.module,
                "git_candidate_identity",
                wraps=self.module.git_candidate_identity,
            ) as identity,
            mock.patch.object(
                self.module, "run_contract_validators", return_value=None
            ),
            mock.patch.object(
                self.module, "load_skill_routing_evaluator", return_value=evaluator
            ),
            mock.patch.object(self.module, "run_plugin_evals", return_value={}),
        ):
            self.module.validate_release(
                self.repository,
                routing_evidence=evidence_root,
                plugin_eval_executable=self.plugin_eval,
                receipt_output=self.receipt,
                composed_receipt=self.composed_receipt,
                **self.private_provenance_arguments,
            )

        self.assertEqual(identity.call_args_list, [mock.call(self.repository)] * 2)
        validate_evidence.assert_called_once_with(
            evidence_root.resolve(), candidate, require_production=True
        )

    def test_rejects_candidate_advance_after_private_provenance_verification(
        self,
    ) -> None:
        evidence_root = Path(self.temporary_directory.name) / "routing-evidence"
        evidence_root.mkdir()
        candidate_a = self.module.git_candidate_identity(self.repository)
        verified_public_roots: list[Path] = []

        def verify_then_advance(**arguments):
            verified_public_roots.append(arguments["public_root"])
            readme = self.repository / "README.md"
            readme.write_text(
                readme.read_text(encoding="utf-8") + "\ncandidate B\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "add", "README.md"],
                cwd=self.repository,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Release Test",
                    "-c",
                    "user.email=release-test@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "test: advance release candidate",
                ],
                cwd=self.repository,
                check=True,
                capture_output=True,
            )

        with (
            mock.patch.object(
                self.module,
                "verify_public_release_evidence",
                side_effect=verify_then_advance,
            ),
            mock.patch.object(self.module, "run_contract_validators", return_value={}),
            mock.patch.object(
                self.module,
                "validate_routing_evidence",
                return_value={"claim": "cooperative evidence only"},
            ),
            mock.patch.object(self.module, "run_plugin_evals", return_value={}),
            self.assertRaisesRegex(
                self.module.ReleaseError,
                "release input changed while the private snapshot was validated",
            ),
        ):
            self.module.validate_release(
                self.repository,
                routing_evidence=evidence_root,
                plugin_eval_executable=self.plugin_eval,
                receipt_output=self.receipt,
                composed_receipt=self.composed_receipt,
                **self.private_provenance_arguments,
            )

        self.assertEqual(len(verified_public_roots), 1)
        self.assertNotEqual(verified_public_roots[0], self.repository)
        self.assertNotEqual(
            self.module.git_candidate_identity(self.repository), candidate_a
        )
        self.assertFalse(self.receipt.exists())

    def test_loading_routing_evaluator_does_not_mutate_snapshot(self) -> None:
        snapshot = Path(self.temporary_directory.name) / "routing-snapshot"
        scripts = snapshot / "scripts"
        scripts.mkdir(parents=True)
        runner = scripts / "run_skill_routing_eval.py"
        runner.write_text(
            "class RoutingError(RuntimeError):\n"
            "    pass\n\n"
            "def validate_evidence(evidence_root, expected_candidate, "
            "require_production=True):\n"
            "    return None\n",
            encoding="utf-8",
        )

        evaluator = self.module.load_skill_routing_evaluator(snapshot)

        self.assertTrue(callable(evaluator.validate_evidence))
        self.assertFalse((scripts / "__pycache__").exists())

    def test_cli_does_not_execute_ignored_unchecked_support_bytecode(self) -> None:
        scripts = self.repository / "scripts"
        marker = Path(self.temporary_directory.name) / "unchecked-pyc-executed"
        malicious_source = Path(self.temporary_directory.name) / "malicious.py"
        malicious_source.write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).touch()\n"
            "candidate_content_identity = lambda *args, **kwargs: None\n"
            "canonical_bytes = lambda *args, **kwargs: b''\n"
            "digest_bytes = lambda *args, **kwargs: 'sha256:' + '0' * 64\n"
            "json_file_bytes = lambda *args, **kwargs: b'{}'\n"
            "strict_json_bytes = lambda *args, **kwargs: {}\n",
            encoding="utf-8",
        )
        bytecode = Path(
            importlib.util.cache_from_source(str(scripts / "evidence_transport.py"))
        )
        bytecode.parent.mkdir(parents=True, exist_ok=True)
        py_compile.compile(
            str(malicious_source),
            cfile=str(bytecode),
            doraise=True,
            invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
        )

        control = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                "import importlib.machinery, importlib.util; "
                "loader = importlib.machinery.SourcelessFileLoader("
                f"'unchecked_probe', {str(bytecode)!r}); "
                "spec = importlib.util.spec_from_loader('unchecked_probe', loader); "
                "module = importlib.util.module_from_spec(spec); "
                "loader.exec_module(module)",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(control.returncode, 0, control.stderr)
        self.assertTrue(marker.exists())
        marker.unlink()

        loader_probe = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                "import importlib.util, sys; "
                f"validator = {str(VALIDATOR)!r}; "
                "spec = importlib.util.spec_from_file_location('release_probe', validator); "
                "module = importlib.util.module_from_spec(spec); "
                "spec.loader.exec_module(module); "
                "[sys.modules.pop(name, None) for name, _ in module.RELEASE_SUPPORT_SOURCES]; "
                f"module._install_frozen_release_support({str(self.repository)!r})",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(loader_probe.returncode, 0, loader_probe.stderr)
        self.assertFalse(marker.exists())

        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                str(scripts / "validate_public_release.py"),
                "--help",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(marker.exists())

    def test_loaded_validator_generation_must_match_frozen_snapshot(self) -> None:
        snapshot = Path(self.temporary_directory.name) / "validator-snapshot"
        validator = snapshot / "scripts/validate_public_release.py"
        validator.parent.mkdir(parents=True)
        shutil.copy2(VALIDATOR, validator)
        self.module.require_loaded_validator_generation(snapshot)
        validator.write_bytes(
            validator.read_bytes().replace(
                b"Validate one immutable public release snapshot",
                b"Validate one different public release snapshot",
                1,
            )
        )

        with self.assertRaisesRegex(
            self.module.ReleaseError,
            "loaded public-release validator differs from the frozen candidate",
        ):
            self.module.require_loaded_validator_generation(snapshot)

    def test_prepared_supervisor_generation_must_match_frozen_snapshot(self) -> None:
        snapshot = Path(self.temporary_directory.name) / "supervisor-snapshot"
        supervisor = snapshot / "scripts/supervise_prepared_release_validation.py"
        supervisor.parent.mkdir(parents=True)
        shutil.copy2(PREPARED_RELEASE_SUPERVISOR, supervisor)
        expected = "sha256:" + hashlib.sha256(supervisor.read_bytes()).hexdigest()
        self.module.require_prepared_supervisor_generation(snapshot, expected)
        supervisor.write_bytes(
            supervisor.read_bytes().replace(
                b"Supervise one prepared public-release validation process group",
                b"Supervise one different public-release validation process group",
                1,
            )
        )

        with self.assertRaisesRegex(
            self.module.ReleaseError,
            "loaded prepared-release supervisor differs from the frozen candidate",
        ):
            self.module.require_prepared_supervisor_generation(snapshot, expected)

    def test_production_release_rejects_routing_evidence_validation_failures(
        self,
    ) -> None:
        evidence_root = Path(self.temporary_directory.name) / "routing-evidence"
        evidence_root.mkdir()
        routing_error = type("RoutingError", (RuntimeError,), {})
        for message in (
            "candidate mismatch",
            "manifest is malformed",
            "113-case matrix is incomplete",
        ):
            with self.subTest(message=message):
                evaluator = types.SimpleNamespace(
                    RoutingError=routing_error,
                    validate_evidence=mock.Mock(side_effect=routing_error(message)),
                )
                with (
                    self.assume_private_provenance_verified(),
                    mock.patch.object(self.module, "run_contract_validators"),
                    mock.patch.object(
                        self.module,
                        "load_skill_routing_evaluator",
                        return_value=evaluator,
                    ),
                    self.assertRaisesRegex(
                        self.module.ReleaseError,
                        "skill-routing evidence validation failed",
                    ),
                ):
                    self.module.validate_release(
                        self.repository,
                        routing_evidence=evidence_root,
                        plugin_eval_executable=self.plugin_eval,
                        receipt_output=self.receipt,
                        composed_receipt=self.composed_receipt,
                        **self.private_provenance_arguments,
                    )

    def test_source_stage_does_not_request_paid_routing_evidence(self) -> None:
        identities = {"schema_version": 1, "plugins": {}}
        with (
            mock.patch.object(
                self.module, "validate_release", return_value=identities
            ) as validate_release,
        ):
            self.assertEqual(
                self.module.validate_source_stage(self.repository), identities
            )

        validate_release.assert_called_once_with(
            self.repository,
            run_contracts=False,
            source_stage_validator=self.module.run_source_stage_validators,
        )

    def test_source_stage_validator_commands_are_bound_to_the_snapshot(self) -> None:
        snapshot = Path(self.temporary_directory.name).resolve() / "snapshot"
        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(
            self.module.subprocess, "run", return_value=completed
        ) as run:
            self.module.run_source_stage_validators(snapshot)

        self.assertEqual(
            run.call_count,
            len(self.module.SOURCE_STAGE_VALIDATED_PLUGINS),
        )
        for plugin, call in zip(
            self.module.SOURCE_STAGE_VALIDATED_PLUGINS,
            run.call_args_list,
            strict=True,
        ):
            command = call.args[0]
            self.assertEqual(
                command[:3],
                [self.module.sys.executable, "-I", "-B"],
            )
            self.assertEqual(Path(command[3]).parent, snapshot / "scripts")
            self.assertEqual(command[4], str(snapshot))
            self.assertEqual(
                command[5:],
                list(self.module.SOURCE_STAGE_VALIDATOR_FLAGS[plugin]),
            )
            self.assertEqual(call.kwargs["cwd"], snapshot)
            self.assertEqual(
                set(call.kwargs["env"]),
                {
                    "HOME",
                    "LANG",
                    "LC_ALL",
                    "PATH",
                    "TEMP",
                    "TMP",
                    "TMPDIR",
                    "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
                },
            )

    def test_isolated_frozen_validator_loads_shared_agent_plugins_contract(
        self,
    ) -> None:
        snapshot = Path(self.temporary_directory.name).resolve() / "source-snapshot"
        snapshot.mkdir()
        self.module.copy_release_scope(
            self.repository,
            snapshot,
            self.module.SOURCE_STAGE_VALIDATED_PLUGINS,
        )
        validator = snapshot / "scripts/validate_versionkeeping.py"
        shared_contract = snapshot / "scripts/agent_plugins_standard.py"
        self.assertTrue(shared_contract.is_file())

        result = self.module.run_private_python_child(
            snapshot,
            [str(validator), str(snapshot)],
            environment=self.module.private_python_child_environment(
                Path(self.temporary_directory.name) / "isolated-environment"
            ),
            timeout=120,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Versionkeeping contract validation passed", result.stdout)

    def test_source_stage_validator_success_cannot_promote_ineligible_runtime_packages(
        self,
    ) -> None:
        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(
            self.module.subprocess,
            "run",
            return_value=completed,
        ):
            self.module.run_source_stage_validators(self.repository)

        source_stage_only = {
            name
            for name, registration in self.module.PUBLIC_RELEASE_REGISTRATIONS.items()
            if not registration["production_eligible"]
        }
        self.assertTrue(source_stage_only)
        self.assertTrue(
            source_stage_only.issubset(self.module.SOURCE_STAGE_VALIDATED_PLUGINS)
        )
        self.assertTrue(
            source_stage_only.isdisjoint(self.module.PRODUCTION_VALIDATED_PLUGINS)
        )
        self.assertTrue(source_stage_only.isdisjoint(self.module.MARKETPLACE_PLUGINS))

    def test_production_contract_omits_source_stage_only_runtime_support(self) -> None:
        production_contract = self.module.release_contract_identity(self.repository)
        source_stage_contract = self.module.release_contract_identity(
            self.repository,
            self.module.SOURCE_STAGE_VALIDATED_PLUGINS,
        )

        for name, registration in self.module.PUBLIC_RELEASE_REGISTRATIONS.items():
            if registration["production_eligible"]:
                continue
            with self.subTest(name=name):
                validator = self.module.VALIDATOR_PATHS[name]
                package_tests = {
                    path
                    for path in registration["support_paths"]
                    if Path(path).parts[:1] == ("tests",)
                }
                self.assertNotIn(validator, production_contract["validators"])
                self.assertIn(validator, source_stage_contract["validators"])
                self.assertTrue(package_tests.isdisjoint(production_contract["tests"]))
                self.assertTrue(package_tests.issubset(source_stage_contract["tests"]))
                self.assertNotIn(
                    f"plugins/{name}",
                    self.module.all_scope_paths(
                        self.module.PRODUCTION_VALIDATED_PLUGINS
                    ),
                )
                self.assertIn(
                    f"plugins/{name}",
                    self.module.all_scope_paths(
                        self.module.SOURCE_STAGE_VALIDATED_PLUGINS
                    ),
                )

    def test_release_contract_binds_shared_agent_plugins_validator(self) -> None:
        before = self.module.release_contract_identity(self.repository)
        shared = "scripts/agent_plugins_standard.py"
        self.assertEqual(before["support_modules"], [shared])

        path = self.repository / shared
        path.write_bytes(path.read_bytes() + b"\n")
        after = self.module.release_contract_identity(self.repository)

        self.assertNotEqual(before["sha256"], after["sha256"])
        self.assertEqual(after["support_modules"], [shared])

    def test_source_stage_rejects_live_mutation_after_snapshot_validators_start(
        self,
    ) -> None:
        def mutate_live_source(_snapshot: Path) -> None:
            readme = self.repository / "README.md"
            readme.write_text(
                readme.read_text(encoding="utf-8") + "\nlate mutation\n",
                encoding="utf-8",
            )

        with self.assertRaisesRegex(
            self.module.ReleaseError,
            "release input changed while the private snapshot was validated",
        ):
            self.module.validate_release(
                self.repository,
                run_contracts=False,
                source_stage_validator=mutate_live_source,
            )

    def test_release_api_and_cli_expose_only_public_safe_v4_inputs(self) -> None:
        parameters = inspect.signature(self.module.validate_release).parameters
        for name in (
            "private_producer_witness",
            "private_producer_registry",
            "expected_private_commit_oid",
            "expected_private_producer_package_sha256",
            "expected_public_candidate_sha256",
        ):
            self.assertIn(name, parameters)
        for forbidden in (
            "private_receipt",
            "private_trust_anchor",
            "private_remote_observation",
            "private_evidence_bundle",
            "frozen_private_identity",
            "private_replay_summary",
            "private_source_archive",
        ):
            self.assertNotIn(forbidden, parameters)
        unflagged = subprocess.run(
            [sys.executable, str(VALIDATOR), "--help"],
            cwd=REPOSITORY,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(unflagged.returncode, 0)
        self.assertIn("Python -I -B", unflagged.stderr)
        completed = subprocess.run(
            [sys.executable, "-I", "-B", str(VALIDATOR), "--help"],
            cwd=REPOSITORY,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        for flag in (
            "--private-producer-witness",
            "--private-producer-registry",
            "--expected-private-commit-oid",
            "--expected-private-producer-package-sha256",
            "--expected-public-candidate-sha256",
        ):
            self.assertIn(flag, completed.stdout)
        for forbidden in (
            "--private-receipt",
            "--private-trust-anchor",
            "--private-remote-observation",
            "--private-evidence-bundle",
            "--frozen-private-identity",
            "--private-replay-summary",
            "--private-source-archive",
        ):
            self.assertNotIn(forbidden, completed.stdout)

    def test_cli_guard_uses_immutable_interpreter_startup_flags(self) -> None:
        script = (
            "import runpy, sys; "
            "sys.dont_write_bytecode = True; "
            f"sys.argv = [{str(VALIDATOR)!r}, '--help']; "
            f"runpy.run_path({str(VALIDATOR)!r}, run_name='__main__')"
        )

        completed = subprocess.run(
            [sys.executable, "-I", "-c", script],
            cwd=REPOSITORY,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Python -I -B", completed.stderr)

    def test_prepared_release_entrypoint_uses_a_clean_private_python_runtime(
        self,
    ) -> None:
        for mode, script_name, expected_arguments in (
            (
                "public-release",
                "validate_public_release.py",
                lambda repository: [
                    str(repository),
                    "--fixture",
                    "public",
                    "--prepared-supervisor-source-sha256",
                    "sha256:"
                    + hashlib.sha256(
                        (
                            repository
                            / "scripts/supervise_prepared_release_validation.py"
                        ).read_bytes()
                    ).hexdigest(),
                ],
            ),
            (
                "phase7-production",
                "run_phase7_production_integration.py",
                lambda repository: [
                    "--public-root",
                    str(repository),
                    "--fixture",
                    "phase7",
                    "--prepared-supervisor-source-sha256",
                    "sha256:"
                    + hashlib.sha256(
                        (
                            repository
                            / "scripts/supervise_prepared_release_validation.py"
                        ).read_bytes()
                    ).hexdigest(),
                ],
            ),
        ):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                repository = root / "repository"
                scripts = repository / "scripts"
                scripts.mkdir(parents=True)
                entrypoint, _ = install_prepared_release_driver(repository)
                capture = root / f"{mode}.json"
                startup_marker = root / f"{mode}-startup-marker"
                shell_startup = root / "shell-startup"
                shell_startup.write_text(
                    f"touch {str(startup_marker)!r}\n", encoding="utf-8"
                )
                python_startup = root / "python-startup"
                python_startup.mkdir()
                (python_startup / "sitecustomize.py").write_text(
                    f"from pathlib import Path\nPath({str(startup_marker)!r}).touch()\n",
                    encoding="utf-8",
                )
                (scripts / script_name).write_text(
                    "import json, os, signal, stat, sys\n"
                    "temporary = os.path.dirname(os.environ['HOME'])\n"
                    "current_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())\n"
                    f"capture = {str(capture)!r}\n"
                    "with open(capture, 'w', encoding='utf-8') as output:\n"
                    "    json.dump({\n"
                    "        'arguments': sys.argv,\n"
                    "        'environment': dict(os.environ),\n"
                    "        'flags': [sys.flags.isolated, sys.flags.dont_write_bytecode],\n"
                    "        'modes': {name: stat.S_IMODE(os.stat(path).st_mode) for name, path in {\n"
                    "            'root': temporary,\n"
                    "            'home': os.environ['HOME'],\n"
                    "            'temporary': os.environ['TMPDIR'],\n"
                    "        }.items()},\n"
                    "        'signal_state': {\n"
                    "            'owned_unblocked': all(candidate not in current_mask for candidate in (\n"
                    "                signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGCHLD,\n"
                    "            )),\n"
                    "            'sigint_normal': signal.getsignal(signal.SIGINT) is signal.default_int_handler,\n"
                    "            'sigterm_default': signal.getsignal(signal.SIGTERM) == signal.SIG_DFL,\n"
                    "            'sighup_default': signal.getsignal(signal.SIGHUP) == signal.SIG_DFL,\n"
                    "            'sigchld_default': signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL,\n"
                    "        },\n"
                    "        'temporary_root': temporary,\n"
                    "    }, output, sort_keys=True)\n"
                    "raise SystemExit(37)\n",
                    encoding="utf-8",
                )
                hostile_environment = os.environ | {
                    "BASH_ENV": str(shell_startup),
                    "ENV": str(shell_startup),
                    "HTTPS_PROXY": "http://proxy.invalid",
                    "PYTHONHOME": str(root / "python-home"),
                    "PYTHONPATH": str(python_startup),
                    "PYTHONSTARTUP": str(root / "startup.py"),
                    "UV_INDEX_URL": "https://index.invalid",
                }
                completed = subprocess.run(
                    [
                        "/usr/bin/env",
                        "-i",
                        "LANG=C.UTF-8",
                        "LC_ALL=C.UTF-8",
                        "PATH=/usr/bin:/bin",
                        "TZ=UTC",
                        "/bin/sh",
                        str(entrypoint),
                        mode,
                        sys.executable,
                        str(repository),
                        "--fixture",
                        "public" if mode == "public-release" else "phase7",
                    ],
                    capture_output=True,
                    env=hostile_environment,
                    text=True,
                    check=False,
                )

                self.assertEqual(completed.returncode, 37, completed.stderr)
                self.assertFalse(startup_marker.exists())
                recorded = json.loads(capture.read_text(encoding="utf-8"))
                self.assertEqual(
                    recorded["arguments"][1:], expected_arguments(repository)
                )
                self.assertEqual(recorded["flags"], [1, 1])
                expected_environment = {
                    "HOME": recorded["environment"]["HOME"],
                    "TEMP": recorded["environment"]["TEMP"],
                    "TMP": recorded["environment"]["TMP"],
                    "TMPDIR": recorded["environment"]["TMPDIR"],
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "PATH": "/usr/bin:/bin",
                    "TZ": "UTC",
                }
                self.assertTrue(
                    expected_environment.items() <= recorded["environment"].items()
                )
                self.assertEqual(
                    set(recorded["environment"]) - set(expected_environment),
                    {"__CF_USER_TEXT_ENCODING"} & set(recorded["environment"]),
                )
                self.assertEqual(
                    recorded["environment"]["TEMP"],
                    recorded["environment"]["TMP"],
                )
                self.assertEqual(
                    recorded["environment"]["TMP"],
                    recorded["environment"]["TMPDIR"],
                )
                self.assertEqual(set(recorded["modes"].values()), {0o700})
                self.assertEqual(
                    recorded["signal_state"],
                    {
                        "owned_unblocked": True,
                        "sigchld_default": True,
                        "sighup_default": True,
                        "sigint_normal": True,
                        "sigterm_default": True,
                    },
                )
                self.assertFalse(Path(recorded["temporary_root"]).exists())

    def test_prepared_release_entrypoint_retains_output_but_rejects_cancellation(
        self,
    ) -> None:
        with PreparedReleaseCancellationFixture(
            signal.SIGTERM,
            publish_output=True,
        ) as cancellation:
            returncode = cancellation.cancel()

            self.assertEqual(returncode, 128 + signal.SIGTERM)
            self.assertNotEqual(returncode, 0)
            self.assertEqual(cancellation.forwarded_signal(), signal.SIGTERM)
            self.assertTrue(cancellation.wait_for_leader_reap())
            self.assertTrue(cancellation.wait_for_descendant_termination())
            self.assertEqual(
                json.loads(cancellation.output_path.read_text(encoding="utf-8")),
                {"status": "generated"},
            )
            self.assertFalse(cancellation.late_marker_path.exists())

    def test_prepared_release_entrypoint_rejects_supervisor_from_another_checkout(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repository = root / "candidate"
            scripts = repository / "scripts"
            scripts.mkdir(parents=True)
            marker = root / "validator-ran"
            (scripts / "validate_public_release.py").write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('ran\\n', encoding='utf-8')\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    "/bin/sh",
                    str(PREPARED_RELEASE_ENTRYPOINT),
                    "public-release",
                    sys.executable,
                    str(repository),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(
                completed.stderr,
                "prepared release validation supervisor does not belong to "
                "the selected repository\n",
            )
            self.assertFalse(marker.exists())

    def test_prepared_release_supervisor_rejects_loaded_generation_replacement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory).resolve() / "candidate"
            _, supervisor_path = install_prepared_release_driver(repository)
            supervisor = load_prepared_release_supervisor_module(supervisor_path)
            self.assertTrue(supervisor.supervisor_belongs_to_repository(repository))
            supervisor_path.write_bytes(
                supervisor_path.read_bytes().replace(
                    b"Supervise one prepared public-release validation process group",
                    b"Supervise one replaced public-release validation process group",
                    1,
                )
            )

            self.assertFalse(supervisor.supervisor_belongs_to_repository(repository))

    def test_prepared_release_entrypoint_forwards_cancellation_signal_matrix(
        self,
    ) -> None:
        for signal_number in (
            signal.SIGINT,
            signal.SIGTERM,
            signal.SIGHUP,
        ):
            for inherited_blocked in (False, True):
                with (
                    self.subTest(
                        signal=signal.Signals(signal_number).name,
                        inherited_blocked=inherited_blocked,
                    ),
                    PreparedReleaseCancellationFixture(
                        signal_number,
                        inherited_blocked=inherited_blocked,
                    ) as cancellation,
                ):
                    returncode = cancellation.cancel()
                    process_record = cancellation.process_record
                    assert process_record is not None
                    assert cancellation.wrapper is not None
                    assert cancellation.cancellation_elapsed_seconds is not None

                    self.assertEqual(returncode, 128 + signal_number)
                    self.assertEqual(
                        cancellation.forwarded_signal(),
                        signal_number,
                    )
                    self.assertLess(
                        cancellation.cancellation_elapsed_seconds,
                        1.5,
                    )
                    self.assertEqual(
                        process_record["leader_parent_pid"],
                        cancellation.wrapper.pid,
                    )
                    self.assertEqual(
                        process_record["leader_pid"],
                        process_record["leader_pgid"],
                    )
                    self.assertEqual(
                        process_record["leader_pid"],
                        process_record["leader_session"],
                    )
                    self.assertEqual(
                        process_record["leader_pid"],
                        process_record["descendant_pgid"],
                    )
                    self.assertEqual(
                        process_record["leader_pid"],
                        process_record["descendant_session"],
                    )
                    self.assertTrue(process_record["cancellation_signal_unblocked"])
                    self.assertTrue(cancellation.wait_for_leader_reap())
                    self.assertTrue(cancellation.wait_for_descendant_termination())
                    self.assertFalse(cancellation.late_marker_path.exists())

    def test_prepared_release_entrypoint_rejects_inherited_ignored_sigterm(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repository = root / "repository"
            scripts = repository / "scripts"
            scripts.mkdir(parents=True)
            entrypoint, _ = install_prepared_release_driver(repository)
            started = root / "started"
            (scripts / "validate_public_release.py").write_text(
                "from pathlib import Path\n"
                f"Path({str(started)!r}).write_text('started\\n', encoding='utf-8')\n",
                encoding="utf-8",
            )

            def ignore_sigterm() -> None:
                signal.signal(signal.SIGTERM, signal.SIG_IGN)

            completed = subprocess.run(
                [
                    "/bin/sh",
                    str(entrypoint),
                    "public-release",
                    sys.executable,
                    str(repository),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                preexec_fn=ignore_sigterm,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(
                completed.stderr,
                "prepared release validation inherited signal disposition "
                "is inadmissible\n",
            )
            self.assertFalse(started.exists())

    def test_prepared_release_entrypoint_rejects_inherited_ignored_sigchld(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repository = root / "repository"
            scripts = repository / "scripts"
            scripts.mkdir(parents=True)
            _, supervisor_path = install_prepared_release_driver(repository)
            marker = root / "validator-ran"
            (scripts / "validate_public_release.py").write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('ran\\n', encoding='utf-8')\n",
                encoding="utf-8",
            )

            def ignore_sigchld() -> None:
                signal.signal(signal.SIGCHLD, signal.SIG_IGN)

            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(supervisor_path),
                    "public-release",
                    str(repository),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                preexec_fn=ignore_sigchld,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(
                completed.stderr,
                "prepared release validation inherited signal disposition "
                "is inadmissible\n",
            )
            self.assertFalse(marker.exists())

    def test_prepared_release_supervisor_rejects_callable_signal_disposition_before_setup(
        self,
    ) -> None:
        supervisor = load_prepared_release_supervisor_module()
        original_disposition = signal.getsignal(signal.SIGTERM)

        def custom_handler(_signal_number: int, _frame) -> None:
            pass

        signal.signal(signal.SIGTERM, custom_handler)
        try:
            with (
                mock.patch.object(supervisor.tempfile, "mkdtemp") as mkdtemp,
                mock.patch.object(supervisor, "fail", return_value=2) as fail,
            ):
                returncode = supervisor.run_prepared_validation(["validator"])
        finally:
            signal.signal(signal.SIGTERM, original_disposition)

        self.assertEqual(returncode, 2)
        mkdtemp.assert_not_called()
        fail.assert_called_once_with(
            "prepared release validation inherited signal disposition is inadmissible"
        )

    def test_prepared_release_supervisor_unblocks_inherited_blocked_sigchld_for_validator(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repository = root / "repository"
            scripts = repository / "scripts"
            scripts.mkdir(parents=True)
            _, supervisor_path = install_prepared_release_driver(repository)
            marker = root / "sigchld-blocked"
            (scripts / "validate_public_release.py").write_text(
                "import signal\n"
                "from pathlib import Path\n"
                "current_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())\n"
                f"Path({str(marker)!r}).write_text(\n"
                "    'blocked' if signal.SIGCHLD in current_mask else 'unblocked',\n"
                "    encoding='utf-8',\n"
                ")\n"
                "raise SystemExit(37)\n",
                encoding="utf-8",
            )

            def block_sigchld() -> None:
                signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGCHLD})

            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(supervisor_path),
                    "public-release",
                    str(repository),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                preexec_fn=block_sigchld,
            )

            self.assertEqual(completed.returncode, 37, completed.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "unblocked")
            self.assertEqual(completed.stderr, "")

    def test_prepared_release_supervisor_cancels_at_terminal_status_boundary(
        self,
    ) -> None:
        supervisor = load_prepared_release_supervisor_module()
        original_handlers = {
            signal_number: signal.getsignal(signal_number)
            for signal_number in supervisor.CANCELLATION_SIGNALS
        }
        signal_sent = False

        class TerminalProcess:
            pid = 424242

            def __init__(self) -> None:
                self.wait_arguments: list[float | None] = []

            def wait(self, timeout: float | None = None) -> int:
                nonlocal signal_sent
                self.wait_arguments.append(timeout)
                if timeout is not None and not signal_sent:
                    signal_sent = True
                    os.kill(os.getpid(), signal.SIGTERM)
                return 0

        process = TerminalProcess()

        def observe_terminal(*_arguments):
            nonlocal signal_sent
            if not signal_sent:
                signal_sent = True
                os.kill(os.getpid(), signal.SIGTERM)
            return types.SimpleNamespace(si_pid=process.pid, si_status=0)

        try:
            with (
                mock.patch.object(
                    supervisor.subprocess,
                    "Popen",
                    return_value=process,
                ) as popen,
                mock.patch.object(
                    supervisor.os, "waitid", side_effect=observe_terminal
                ),
                mock.patch.object(supervisor.os, "killpg") as kill_process_group,
                mock.patch.object(supervisor.time, "sleep"),
            ):
                returncode = supervisor.supervise(["validator"], {})
        finally:
            for signal_number, handler in original_handlers.items():
                signal.signal(signal_number, handler)

        self.assertEqual(returncode, 128 + signal.SIGTERM)
        self.assertEqual(
            kill_process_group.call_args_list,
            [
                mock.call(process.pid, signal.SIGTERM),
                mock.call(process.pid, signal.SIGKILL),
            ],
        )
        self.assertEqual(process.wait_arguments, [None])
        child_command = popen.call_args.args[0]
        self.assertEqual(
            child_command[:5],
            [
                sys.executable,
                "-I",
                "-B",
                str(PREPARED_RELEASE_SUPERVISOR.resolve()),
                supervisor.VALIDATION_CHILD_MODE,
            ],
        )
        self.assertTrue(popen.call_args.kwargs["close_fds"])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertNotIn("preexec_fn", popen.call_args.kwargs)

    def test_prepared_release_supervisor_preserves_post_terminal_signal_disposition(
        self,
    ) -> None:
        supervisor = load_prepared_release_supervisor_module()

        class TerminalProcess:
            pid = 424244

            def wait(self) -> int:
                os.kill(os.getpid(), signal.SIGTERM)
                return 0

        process = TerminalProcess()
        original_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
        try:
            with (
                mock.patch.object(
                    supervisor.subprocess,
                    "Popen",
                    return_value=process,
                ),
                mock.patch.object(
                    supervisor.os,
                    "waitid",
                    return_value=types.SimpleNamespace(
                        si_pid=process.pid,
                        si_status=0,
                    ),
                ),
                mock.patch.object(supervisor.os, "killpg") as kill_process_group,
            ):
                returncode = supervisor.supervise(["validator"], {})

            self.assertEqual(returncode, 0)
            kill_process_group.assert_not_called()
            self.assertIn(signal.SIGTERM, signal.sigpending())
            signal.sigwait({signal.SIGTERM})
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)

    def test_prepared_release_supervisor_does_not_spawn_after_setup_cancellation(
        self,
    ) -> None:
        supervisor = load_prepared_release_supervisor_module()
        temporary_root = Path(
            tempfile.mkdtemp(
                prefix="prepared-release-validation-test.",
                dir="/tmp",
            )
        )

        def signal_after_setup(_temporary_root: Path) -> dict[str, str]:
            os.kill(os.getpid(), signal.SIGTERM)
            return {}

        def consume_unexpected_spawn(
            _command: list[str],
            _environment: dict[str, str],
        ) -> int:
            if signal.SIGTERM in signal.sigpending():
                signal.sigwait({signal.SIGTERM})
            return 0

        with (
            mock.patch.object(
                supervisor.tempfile,
                "mkdtemp",
                return_value=str(temporary_root),
            ),
            mock.patch.object(
                supervisor,
                "private_environment",
                side_effect=signal_after_setup,
            ),
            mock.patch.object(
                supervisor,
                "supervise_blocked",
                side_effect=consume_unexpected_spawn,
            ) as supervise_blocked,
        ):
            returncode = supervisor.run_prepared_validation(["validator"])

        self.assertEqual(returncode, 128 + signal.SIGTERM)
        supervise_blocked.assert_not_called()
        self.assertFalse(temporary_root.exists())

    def test_prepared_release_supervisor_does_not_signal_after_echild(
        self,
    ) -> None:
        supervisor = load_prepared_release_supervisor_module()

        class LostProcess:
            pid = 424243

            def __init__(self) -> None:
                self.wait = mock.Mock(return_value=0)

        process = LostProcess()
        with (
            mock.patch.object(
                supervisor.subprocess,
                "Popen",
                return_value=process,
            ),
            mock.patch.object(
                supervisor.os,
                "waitid",
                side_effect=ChildProcessError,
            ),
            mock.patch.object(supervisor.os, "killpg") as kill_process_group,
            mock.patch.object(supervisor.os, "kill") as kill_process,
            mock.patch.object(supervisor.time, "sleep"),
        ):
            with self.assertRaises(supervisor.SupervisionError):
                supervisor.supervise(["validator"], {})

        kill_process_group.assert_not_called()
        kill_process.assert_not_called()
        process.wait.assert_not_called()

    def test_prepared_release_supervisor_revokes_ownership_on_wait_mismatch_or_reap_echild(
        self,
    ) -> None:
        supervisor = load_prepared_release_supervisor_module()

        mismatched_process = types.SimpleNamespace(pid=424245, wait=mock.Mock())
        mismatched_child = supervisor.OwnedChild(mismatched_process)
        with mock.patch.object(
            supervisor.os,
            "waitid",
            return_value=types.SimpleNamespace(si_pid=424246, si_status=0),
        ):
            with self.assertRaises(supervisor.OwnershipLost):
                mismatched_child.observe_terminal()
        self.assertEqual(mismatched_child.state, supervisor.ChildState.LOST)
        mismatched_process.wait.assert_not_called()

        externally_reaped_process = types.SimpleNamespace(
            pid=424247,
            wait=mock.Mock(side_effect=ChildProcessError),
        )
        externally_reaped_child = supervisor.OwnedChild(externally_reaped_process)
        with self.assertRaises(supervisor.OwnershipLost):
            externally_reaped_child.reap()
        self.assertEqual(externally_reaped_child.state, supervisor.ChildState.LOST)

        for lost_child in (mismatched_child, externally_reaped_child):
            with (
                self.subTest(pid=lost_child.pid),
                mock.patch.object(supervisor.os, "killpg") as kill_process_group,
            ):
                with self.assertRaises(supervisor.OwnershipLost):
                    supervisor.signal_anchored_process_group(
                        lost_child,
                        signal.SIGKILL,
                    )
                kill_process_group.assert_not_called()

    def test_prepared_release_supervisor_cleans_up_after_validation_child_failure(
        self,
    ) -> None:
        supervisor = load_prepared_release_supervisor_module()
        temporary_roots: list[Path] = []
        original_private_environment = supervisor.private_environment

        def record_private_environment(temporary_root: Path) -> dict[str, str]:
            temporary_roots.append(temporary_root)
            return original_private_environment(temporary_root)

        with mock.patch.object(
            supervisor,
            "private_environment",
            side_effect=record_private_environment,
        ):
            returncode = supervisor.run_prepared_validation(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    "raise SystemExit(2)",
                ]
            )

        self.assertEqual(returncode, 2)
        self.assertEqual(len(temporary_roots), 1)
        self.assertFalse(temporary_roots[0].exists())

    def test_prepared_release_supervisor_overrides_prior_status_on_cleanup_failure(
        self,
    ) -> None:
        supervisor = load_prepared_release_supervisor_module()
        for prior_status in (0, 37, 128 + signal.SIGTERM):
            with self.subTest(prior_status=prior_status):
                temporary_root = Path(
                    tempfile.mkdtemp(
                        prefix="prepared-release-validation-test.",
                        dir="/tmp",
                    )
                )
                try:
                    with (
                        mock.patch.object(
                            supervisor.tempfile,
                            "mkdtemp",
                            return_value=str(temporary_root),
                        ),
                        mock.patch.object(
                            supervisor,
                            "supervise_blocked",
                            return_value=prior_status,
                        ),
                        mock.patch.object(
                            supervisor.shutil,
                            "rmtree",
                            side_effect=OSError,
                        ),
                        mock.patch.object(
                            supervisor,
                            "fail",
                            return_value=2,
                        ) as fail,
                    ):
                        returncode = supervisor.run_prepared_validation(["validator"])
                finally:
                    shutil.rmtree(temporary_root)

                self.assertEqual(returncode, 2)
                fail.assert_called_once_with(
                    "prepared release validation cleanup failed"
                )

    def test_prepared_release_validation_child_execve_failure_is_fixed_nonzero(
        self,
    ) -> None:
        script = (
            "import importlib.util\n"
            "import sys\n"
            f"path = {str(PREPARED_RELEASE_SUPERVISOR)!r}\n"
            "specification = importlib.util.spec_from_file_location(\n"
            "    'prepared_release_supervisor_exec_failure', path\n"
            ")\n"
            "module = importlib.util.module_from_spec(specification)\n"
            "specification.loader.exec_module(module)\n"
            "def fail_execve(*_arguments):\n"
            "    raise OSError\n"
            "module.os.execve = fail_execve\n"
            "raise SystemExit(module.run_validation_child([\n"
            "    sys.executable, '-I', '-B', '-c', 'pass'\n"
            "]))\n"
        )

        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(
            completed.stderr,
            "prepared release validation child exec failed\n",
        )

    def test_prepared_release_entrypoint_rejects_invalid_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repository = root / "repository"
            (repository / "scripts").mkdir(parents=True)
            (repository / "scripts/validate_public_release.py").write_text(
                "raise SystemExit(0)\n",
                encoding="utf-8",
            )
            nonexecutable = root / "python"
            nonexecutable.write_text("not executable\n", encoding="utf-8")
            nonexecutable.chmod(0o600)
            cases = (
                (
                    "unknown",
                    sys.executable,
                    str(repository),
                    (),
                    "mode must be",
                ),
                (
                    "public-release",
                    "python",
                    str(repository),
                    (),
                    "absolute executable",
                ),
                (
                    "public-release",
                    sys.executable,
                    "repository",
                    (),
                    "absolute directory",
                ),
                (
                    "public-release",
                    str(root / "missing-python"),
                    str(repository),
                    (),
                    "absolute executable",
                ),
                (
                    "public-release",
                    str(nonexecutable),
                    str(repository),
                    (),
                    "absolute executable",
                ),
                (
                    "public-release",
                    sys.executable,
                    str(root / "missing-repository"),
                    (),
                    "absolute directory",
                ),
                (
                    "phase7-production",
                    sys.executable,
                    str(repository),
                    (),
                    "selected release entrypoint is missing",
                ),
                (
                    "phase7-production",
                    sys.executable,
                    str(repository),
                    ("--public-root", str(repository)),
                    "receives --public-root",
                ),
                (
                    "phase7-production",
                    sys.executable,
                    str(repository),
                    (f"--public-root={repository}",),
                    "receives --public-root",
                ),
            )
            for mode, prepared_python, candidate, arguments, expected_error in cases:
                with self.subTest(
                    mode=mode,
                    prepared_python=prepared_python,
                    candidate=candidate,
                ):
                    completed = subprocess.run(
                        [
                            "/bin/sh",
                            str(PREPARED_RELEASE_ENTRYPOINT),
                            mode,
                            prepared_python,
                            candidate,
                            *arguments,
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(completed.returncode, 2)
                    self.assertIn(expected_error, completed.stderr)

    def test_cli_guard_rejects_unsupported_cpython_before_validator_imports(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            validator = root / "validate_public_release.py"
            validator.write_text(
                VALIDATOR.read_text(encoding="utf-8"), encoding="utf-8"
            )
            marker = root / "imported"
            (root / "evidence_transport.py").write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
                encoding="utf-8",
            )
            for implementation, version in (("pypy", (3, 14)), ("cpython", (3, 12))):
                with self.subTest(implementation=implementation, version=version):
                    script = (
                        "import runpy, sys, types; "
                        "sys.implementation = types.SimpleNamespace("
                        f"name={implementation!r}, cache_tag=sys.implementation.cache_tag); "
                        f"sys.version_info = {version!r}; "
                        f"sys.argv = [{str(validator)!r}, '--help']; "
                        f"runpy.run_path({str(validator)!r}, run_name='__main__')"
                    )
                    completed = subprocess.run(
                        [sys.executable, "-I", "-B", "-c", script],
                        capture_output=True,
                        text=True,
                        check=False,
                    )

                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn("CPython 3.13+", completed.stderr)
                    self.assertFalse(marker.exists())

    def test_supervisor_cli_guard_rejects_unsupported_cpython_before_imports(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "post-guard-import"
            for implementation, version in (("pypy", (3, 14)), ("cpython", (3, 12))):
                with self.subTest(implementation=implementation, version=version):
                    script = (
                        "import builtins\n"
                        "import runpy\n"
                        "import sys\n"
                        "import types\n"
                        "original_import = builtins.__import__\n"
                        "def audited_import(name, *arguments, **keywords):\n"
                        "    if name == 'shutil':\n"
                        f"        open({str(marker)!r}, 'w').close()\n"
                        "    return original_import(name, *arguments, **keywords)\n"
                        "builtins.__import__ = audited_import\n"
                        "sys.implementation = types.SimpleNamespace(\n"
                        f"    name={implementation!r},\n"
                        "    cache_tag=sys.implementation.cache_tag,\n"
                        ")\n"
                        f"sys.version_info = {version!r}\n"
                        f"sys.argv = [{str(PREPARED_RELEASE_SUPERVISOR)!r}]\n"
                        f"runpy.run_path({str(PREPARED_RELEASE_SUPERVISOR)!r}, "
                        "run_name='__main__')\n"
                    )
                    completed = subprocess.run(
                        [sys.executable, "-I", "-B", "-c", script],
                        capture_output=True,
                        text=True,
                        check=False,
                    )

                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn(
                        "supervise_prepared_release_validation.py must run with "
                        "CPython 3.13+",
                        completed.stderr,
                    )
                    self.assertFalse(marker.exists())

    def test_readme_uses_only_the_prepared_release_entrypoint(self) -> None:
        readme = (REPOSITORY / "README.md").read_text(encoding="utf-8")
        normalized_readme = " ".join(readme.split())

        self.assertEqual(readme.count("run_prepared_release_validation.sh"), 2)
        self.assertIn(
            "/usr/bin/env -i LANG=C.UTF-8 LC_ALL=C.UTF-8 PATH=/usr/bin:/bin TZ=UTC /bin/sh",
            readme,
        )
        self.assertIn("external trusted deployment TCB", readme)
        self.assertIn("Dependency resolution, package provisioning", readme)
        self.assertIn("Only an exact wrapper exit status of `0`", normalized_readme)
        self.assertNotIn("uv --no-config run", readme)
        self.assertNotIn("uv run --with PyYAML --with pytest", readme)
        self.assertIn(
            "scripts/run_prepared_release_validation.sh",
            self.module.COMMON_SUPPORT_PATHS,
        )
        self.assertIn(
            "scripts/supervise_prepared_release_validation.py",
            self.module.COMMON_SUPPORT_PATHS,
        )
        self.assertIn(
            "scripts/agent_plugins_standard.py",
            self.module.COMMON_SUPPORT_PATHS,
        )
        self.assertIn(
            "tests/test_agent_plugins_standard.py",
            self.module.COMMON_SUPPORT_PATHS,
        )
        self.assertIn(
            "tests/fixtures/phase7-v5-compatibility.json",
            self.module.COMMON_SUPPORT_PATHS,
        )

    def test_cli_refuses_routing_evidence_in_source_stage(self) -> None:
        evidence_root = Path(self.temporary_directory.name) / "routing-evidence"
        evidence_root.mkdir()
        with (
            mock.patch.object(
                self.module.sys,
                "argv",
                [
                    "validate_public_release.py",
                    str(self.repository),
                    "--source-stage",
                    "--routing-evidence",
                    str(evidence_root),
                ],
            ),
            mock.patch.object(self.module, "validate_source_stage") as source_stage,
        ):
            self.assertEqual(self.module.main(), 1)
        source_stage.assert_not_called()

    def test_cli_forwards_complete_task_witness_final_evidence_verbatim(self) -> None:
        routing_evidence = Path(self.temporary_directory.name) / "routing-evidence"
        routing_evidence.mkdir()
        self.plugin_eval.chmod(0o700)
        task_witness_evidence = (
            "/evidence/candidate/../candidate-root",
            "/evidence/release-manifest.json",
            "/evidence/macos-receipt.json",
            "/evidence/linux-receipt.json",
            "/evidence/review-evidence.json",
        )
        promoted_plugins = (*self.module.PRODUCTION_VALIDATED_PLUGINS, "task-witness")
        with (
            mock.patch.object(
                self.module.sys,
                "argv",
                [
                    "validate_public_release.py",
                    str(self.repository),
                    "--routing-evidence",
                    str(routing_evidence),
                    "--receipt-output",
                    str(self.receipt),
                    "--composed-receipt",
                    str(self.composed_receipt),
                    "--plugin-eval",
                    str(self.plugin_eval),
                    "--task-witness-candidate-root",
                    task_witness_evidence[0],
                    "--task-witness-release-manifest",
                    task_witness_evidence[1],
                    "--task-witness-macos-receipt",
                    task_witness_evidence[2],
                    "--task-witness-linux-receipt",
                    task_witness_evidence[3],
                    "--task-witness-review-evidence",
                    task_witness_evidence[4],
                ],
            ),
            mock.patch.object(
                self.module,
                "PRODUCTION_VALIDATED_PLUGINS",
                promoted_plugins,
            ),
            mock.patch.object(
                self.module,
                "validate_release",
                return_value={},
            ) as validate_release,
        ):
            self.assertEqual(self.module.main(), 0)

        self.assertEqual(
            validate_release.call_args.kwargs["task_witness_final_evidence"],
            task_witness_evidence,
        )

    def test_cli_rejects_partial_task_witness_final_evidence(self) -> None:
        routing_evidence = Path(self.temporary_directory.name) / "routing-evidence"
        routing_evidence.mkdir()
        self.plugin_eval.chmod(0o700)
        evidence_arguments = [
            "--task-witness-candidate-root",
            "/evidence/candidate-root",
            "--task-witness-release-manifest",
            "/evidence/release-manifest.json",
            "--task-witness-macos-receipt",
            "/evidence/macos-receipt.json",
            "--task-witness-linux-receipt",
            "/evidence/linux-receipt.json",
            "--task-witness-review-evidence",
            "/evidence/review-evidence.json",
        ]
        promoted_plugins = (*self.module.PRODUCTION_VALIDATED_PLUGINS, "task-witness")
        for partial in (evidence_arguments[:2], evidence_arguments[:-2]):
            with (
                self.subTest(partial=partial),
                mock.patch.object(
                    self.module.sys,
                    "argv",
                    [
                        "validate_public_release.py",
                        str(self.repository),
                        "--routing-evidence",
                        str(routing_evidence),
                        "--receipt-output",
                        str(self.receipt),
                        "--composed-receipt",
                        str(self.composed_receipt),
                        "--plugin-eval",
                        str(self.plugin_eval),
                        *partial,
                    ],
                ),
                mock.patch.object(
                    self.module,
                    "PRODUCTION_VALIDATED_PLUGINS",
                    promoted_plugins,
                ),
                mock.patch.object(self.module, "validate_release") as validate_release,
            ):
                self.assertEqual(self.module.main(), 1)
            validate_release.assert_not_called()

    def test_cli_requires_task_witness_final_evidence_after_promotion(self) -> None:
        promoted_plugins = (*self.module.PRODUCTION_VALIDATED_PLUGINS, "task-witness")
        with (
            mock.patch.object(
                self.module.sys,
                "argv",
                ["validate_public_release.py", str(self.repository)],
            ),
            mock.patch.object(
                self.module,
                "PRODUCTION_VALIDATED_PLUGINS",
                promoted_plugins,
            ),
            mock.patch.object(
                self.module,
                "validate_release",
                return_value={},
            ) as validate_release,
        ):
            self.assertEqual(self.module.main(), 1)
        validate_release.assert_not_called()

    def test_cli_rejects_reordered_or_duplicate_task_witness_evidence(self) -> None:
        ordered = [
            "--task-witness-candidate-root",
            "/evidence/candidate-root",
            "--task-witness-release-manifest",
            "/evidence/release-manifest.json",
            "--task-witness-macos-receipt",
            "/evidence/macos-receipt.json",
            "--task-witness-linux-receipt",
            "/evidence/linux-receipt.json",
            "--task-witness-review-evidence",
            "/evidence/review-evidence.json",
        ]
        malformed_cases = (
            [*ordered[2:4], *ordered[:2], *ordered[4:]],
            [*ordered, *ordered[:2]],
        )
        for malformed in malformed_cases:
            with (
                self.subTest(malformed=malformed),
                mock.patch.object(
                    self.module.sys,
                    "argv",
                    ["validate_public_release.py", str(self.repository), *malformed],
                ),
                mock.patch.object(
                    self.module,
                    "validate_release",
                    return_value={},
                ) as validate_release,
            ):
                self.assertEqual(self.module.main(), 1)
            validate_release.assert_not_called()

    def test_cli_rejects_joined_task_witness_evidence_option(self) -> None:
        with (
            mock.patch.object(
                self.module.sys,
                "argv",
                [
                    "validate_public_release.py",
                    str(self.repository),
                    "--task-witness-candidate-root=/evidence/candidate-root",
                    "--task-witness-release-manifest",
                    "/evidence/release-manifest.json",
                    "--task-witness-macos-receipt",
                    "/evidence/macos-receipt.json",
                    "--task-witness-linux-receipt",
                    "/evidence/linux-receipt.json",
                    "--task-witness-review-evidence",
                    "/evidence/review-evidence.json",
                ],
            ),
            mock.patch.object(
                self.module,
                "validate_release",
                return_value={},
            ) as validate_release,
        ):
            self.assertEqual(self.module.main(), 1)
        validate_release.assert_not_called()

    def test_cli_rejects_task_witness_final_evidence_in_source_stage(self) -> None:
        with (
            mock.patch.object(
                self.module.sys,
                "argv",
                [
                    "validate_public_release.py",
                    str(self.repository),
                    "--source-stage",
                    "--task-witness-candidate-root",
                    "/evidence/candidate-root",
                    "--task-witness-release-manifest",
                    "/evidence/release-manifest.json",
                    "--task-witness-macos-receipt",
                    "/evidence/macos-receipt.json",
                    "--task-witness-linux-receipt",
                    "/evidence/linux-receipt.json",
                    "--task-witness-review-evidence",
                    "/evidence/review-evidence.json",
                ],
            ),
            mock.patch.object(self.module, "validate_source_stage") as source_stage,
        ):
            self.assertEqual(self.module.main(), 1)
        source_stage.assert_not_called()

    def test_cli_rejects_abbreviated_task_witness_evidence_option(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                str(self.repository / "scripts/validate_public_release.py"),
                str(self.repository),
                "--task-witness-candidate",
                "/evidence/candidate-root",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn(
            "unrecognized arguments: --task-witness-candidate",
            completed.stderr,
        )

    def test_prepared_supervisor_forwards_task_witness_evidence_verbatim(self) -> None:
        supervisor = load_prepared_release_supervisor_module()
        arguments = [
            "--routing-evidence",
            "/evidence/routing",
            "--task-witness-candidate-root",
            "/evidence/candidate/../candidate-root",
            "--task-witness-release-manifest",
            "/evidence/release-manifest.json",
            "--task-witness-macos-receipt",
            "/evidence/macos-receipt.json",
            "--task-witness-linux-receipt",
            "/evidence/linux-receipt.json",
            "--task-witness-review-evidence",
            "/evidence/review-evidence.json",
            "--receipt-output",
            "/evidence/public-release-receipt.json",
        ]

        command = supervisor.validation_command(
            "public-release",
            REPOSITORY,
            arguments,
        )

        self.assertEqual(
            command,
            [
                supervisor.sys.executable,
                "-I",
                "-B",
                str(REPOSITORY / "scripts/validate_public_release.py"),
                str(REPOSITORY),
                *arguments,
                supervisor.PREPARED_SUPERVISOR_SOURCE_OPTION,
                str(supervisor._LOADED_SUPERVISOR_SOURCE["source_sha256"]),
            ],
        )

    def test_cli_rejects_abbreviated_source_stage_option(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                str(self.repository / "scripts/validate_public_release.py"),
                str(self.repository),
                "--source",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("unrecognized arguments: --source", result.stderr)

    def test_prepared_release_rejects_abbreviated_source_stage_option(
        self,
    ) -> None:
        result = subprocess.run(
            [
                "/bin/sh",
                str(self.repository / "scripts" / PREPARED_RELEASE_ENTRYPOINT.name),
                "public-release",
                sys.executable,
                str(self.repository),
                "--source",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("unrecognized arguments: --source", result.stderr)

    def test_cli_refuses_receipt_output_in_source_stage(self) -> None:
        with (
            mock.patch.object(
                self.module.sys,
                "argv",
                [
                    "validate_public_release.py",
                    str(self.repository),
                    "--source-stage",
                    "--receipt-output",
                    str(self.receipt),
                ],
            ),
            mock.patch.object(self.module, "validate_source_stage") as source_stage,
        ):
            self.assertEqual(self.module.main(), 1)
        source_stage.assert_not_called()

    def test_cli_refuses_composed_receipt_in_source_stage(self) -> None:
        with (
            mock.patch.object(
                self.module.sys,
                "argv",
                [
                    "validate_public_release.py",
                    str(self.repository),
                    "--source-stage",
                    "--composed-receipt",
                    str(self.composed_receipt),
                ],
            ),
            mock.patch.object(self.module, "validate_source_stage") as source_stage,
        ):
            self.assertEqual(self.module.main(), 1)
        source_stage.assert_not_called()

    def test_source_stage_api_refuses_receipt_output(self) -> None:
        with self.assertRaisesRegex(
            self.module.ReleaseError, "does not accept a release receipt output"
        ):
            self.module.validate_release(
                self.repository,
                run_contracts=False,
                receipt_output=self.receipt,
            )

    def test_source_stage_api_refuses_composed_receipt(self) -> None:
        with self.assertRaisesRegex(
            self.module.ReleaseError, "does not accept composed evidence"
        ):
            self.module.validate_release(
                self.repository,
                run_contracts=False,
                composed_receipt=self.composed_receipt,
                **self.private_provenance_arguments,
            )

    def test_release_receipt_rejects_symlink_output(self) -> None:
        target = self.receipt.with_name("receipt-target.json")
        target.write_text("placeholder\n", encoding="utf-8")
        self.receipt.symlink_to(target)
        with self.assertRaisesRegex(self.module.ReleaseError, "contains a symlink"):
            self.module.validate_receipt_output(self.repository, self.receipt)

    def test_held_receipt_parent_descriptor_survives_parent_symlink_swap(self) -> None:
        parent = Path(self.temporary_directory.name).resolve() / "receipt-parent"
        attacker = Path(self.temporary_directory.name).resolve() / "attacker"
        parent.mkdir()
        attacker.mkdir()
        output = parent / "receipt.json"
        receipt_output = self.module.prepare_receipt_output(self.repository, output)
        original_parent = parent.with_name("receipt-parent-original")
        parent.rename(original_parent)
        parent.symlink_to(attacker, target_is_directory=True)
        try:
            self.module.write_release_receipt(receipt_output, {"schema_version": 1})
        finally:
            receipt_output.close()

        self.assertTrue((original_parent / output.name).is_file())
        self.assertFalse((attacker / output.name).exists())

    def test_receipt_publication_refuses_atomic_destination_race(self) -> None:
        parent = Path(self.temporary_directory.name).resolve() / "receipt-parent"
        parent.mkdir()
        output = parent / "receipt.json"
        receipt_output = self.module.prepare_receipt_output(self.repository, output)
        real_link = self.module.os.link

        def create_racer_then_link(*arguments, **keywords):
            descriptor = os.open(
                output.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=receipt_output.parent_descriptor,
            )
            with os.fdopen(descriptor, "wb") as file:
                file.write(b"racer\n")
            return real_link(*arguments, **keywords)

        try:
            with (
                mock.patch.object(
                    self.module.os, "link", side_effect=create_racer_then_link
                ),
                self.assertRaisesRegex(
                    self.module.ReleaseError,
                    "output appeared before publication",
                ),
            ):
                self.module.write_release_receipt(receipt_output, {"schema_version": 1})
        finally:
            receipt_output.close()

        self.assertEqual(output.read_bytes(), b"racer\n")
        self.assertFalse(list(parent.glob(".receipt.json.*")))

    def test_git_snapshot_force_adds_tracked_ignored_scope_files(self) -> None:
        ignore = self.repository / ".gitignore"
        ignore.write_text("README.md\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "--force", ".gitignore"],
            cwd=self.repository,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Release Test",
                "-c",
                "user.email=release-test@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "test: ignore a tracked release file",
            ],
            cwd=self.repository,
            check=True,
            capture_output=True,
        )
        candidate = self.module.git_candidate_identity(self.repository)
        snapshot = Path(self.temporary_directory.name) / "ignored-tracked-snapshot"
        self.module.snapshot_git_candidate(self.repository, snapshot, candidate)
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "README.md"],
            cwd=snapshot,
            check=False,
            capture_output=True,
        )
        self.assertEqual(tracked.returncode, 0)

    def test_git_snapshot_preserves_committed_modes_and_detects_source_mode_drift(
        self,
    ) -> None:
        subprocess.run(
            ["git", "config", "core.fileMode", "false"],
            cwd=self.repository,
            check=True,
            capture_output=True,
        )
        readme = self.repository / "README.md"
        os.chmod(readme, 0o755)
        candidate = self.module.git_candidate_identity(self.repository)
        snapshot = Path(self.temporary_directory.name) / "mode-snapshot"
        self.module.snapshot_git_candidate(self.repository, snapshot, candidate)
        self.assertEqual(snapshot.joinpath("README.md").stat().st_mode & 0o777, 0o644)
        self.assertNotEqual(
            self.module.scope_content_digest(self.repository),
            self.module.scope_content_digest(snapshot),
        )
        evidence = Path(self.temporary_directory.name) / "routing-evidence"
        evidence.mkdir()
        with (
            mock.patch.object(self.module, "run_contract_validators", return_value={}),
            mock.patch.object(
                self.module,
                "validate_routing_evidence",
                return_value={"claim": "cooperative evidence only"},
            ),
            mock.patch.object(self.module, "run_plugin_evals", return_value={}),
            self.assertRaisesRegex(
                self.module.ReleaseError,
                "private snapshot content differs from the release input",
            ),
        ):
            self.module.validate_release(
                self.repository,
                routing_evidence=evidence,
                plugin_eval_executable=self.plugin_eval,
                receipt_output=self.receipt,
                composed_receipt=self.composed_receipt,
                **self.private_provenance_arguments,
            )

    def test_release_receipt_is_private_deterministic_and_content_bound(self) -> None:
        evidence_root = Path(self.temporary_directory.name) / "routing-evidence"
        evidence_root.mkdir()
        candidate = self.module.git_candidate_identity(self.repository)
        routing = {
            "claim": "cooperative evidence only",
            "evaluator_sha256": "sha256:" + "d" * 64,
            "manifest": {
                "schema_version": 3,
                "semantic_sha256": "sha256:" + "e" * 64,
                "requested_model": "claude-sonnet-5",
                "observed_models": ["claude-sonnet-5"],
                "counts": {"cold_start": 21, "explicit_invocation": 21, "trigger": 71},
                "accounting": {
                    "input_tokens": 113,
                    "output_tokens": 226,
                    "total_cost_usd": 1.25,
                },
            },
        }
        plugin_evals = {
            "tricritical": {
                "analyzed_plugin": {
                    "entry_path": "plugins/tricritical/plugin.json",
                    "name": "tricritical",
                    "path": "plugins/tricritical",
                    "target_kind": "plugin",
                },
                "outcome": "pass",
                "warnings": [],
                "advisories": [],
                "policy_sha256": "sha256:" + "f" * 64,
                "calibration_manifest_sha256": "sha256:" + "0" * 64,
                "policy_projection_sha256": "sha256:" + "1" * 64,
                "tool_runtime": {
                    "runtime_tree_sha256": "sha256:" + "2" * 64,
                    "interpreter": {
                        "sha256": "sha256:" + "3" * 64,
                        "version": "v20.0.0",
                        "coverage": "main-executable-bytes-only",
                        "limitations": [
                            "dynamic-loader-and-shared-library-bytes-are-not-bound",
                            "pathname-exec-time-ABA-is-not-bound",
                        ],
                    },
                },
            }
        }
        contracts = {
            "sha256": "sha256:" + "2" * 64,
            "validators": ["scripts/validate_public_release.py"],
            "tests": ["tests/test_validate_public_release.py"],
        }
        expected = self.module.candidate_identities(
            self.repository,
            self.module.PRODUCTION_VALIDATED_PLUGINS,
        )
        with (
            self.assume_private_provenance_verified(),
            mock.patch.object(
                self.module, "run_contract_validators", return_value=contracts
            ),
            mock.patch.object(
                self.module, "validate_routing_evidence", return_value=routing
            ),
            mock.patch.object(
                self.module, "run_plugin_evals", return_value=plugin_evals
            ),
        ):
            identities = self.module.validate_release(
                self.repository,
                expected=expected,
                routing_evidence=evidence_root,
                plugin_eval_executable=self.plugin_eval,
                receipt_output=self.receipt,
                composed_receipt=self.composed_receipt,
                **self.private_provenance_arguments,
            )
            first_bytes = self.receipt.read_bytes()
            self.module.validate_release(
                self.repository,
                expected=expected,
                routing_evidence=evidence_root,
                plugin_eval_executable=self.plugin_eval,
                receipt_output=self.receipt,
                composed_receipt=self.composed_receipt,
                **self.private_provenance_arguments,
            )

        self.assertEqual(identities, expected)
        self.assertEqual(self.receipt.read_bytes(), first_bytes)
        self.assertEqual(self.receipt.stat().st_mode & 0o777, 0o600)
        receipt = self.module.strict_json(first_bytes.decode(), "release receipt")
        claimed_digest = receipt.pop("sha256")
        self.assertEqual(claimed_digest, self.module.canonical_digest(receipt))
        self.assertEqual(receipt["schema_version"], 5)
        self.assertEqual(receipt["candidate"], candidate)
        self.assertEqual(receipt["routing"], routing)
        self.assertEqual(
            receipt["composed"]["receipt_sha256"],
            self.module.strict_json(
                self.composed_receipt.read_text(encoding="utf-8"),
                "composed fixture",
            )["receipt_sha256"],
        )
        self.assertEqual(receipt["plugin_evals"], plugin_evals)
        self.assertEqual(receipt["release_contract"], contracts)
        self.assertEqual(receipt["identities"], expected)
        self.assertEqual(receipt["expected_identities"]["identities"], expected)
        self.assertEqual(
            set(receipt["identities"]["plugins"]),
            set(self.module.PRODUCTION_VALIDATED_PLUGINS),
        )
        self.assertEqual(
            set(receipt["expected_identities"]["identities"]["plugins"]),
            set(self.module.PRODUCTION_VALIDATED_PLUGINS),
        )
        serialized = first_bytes.decode()
        self.assertNotIn(str(self.repository), serialized)
        self.assertNotIn(str(evidence_root), serialized)
        self.assertNotIn(str(self.plugin_eval), serialized)
        self.assertNotIn("/opt/node/bin/node", serialized)
        self.assertNotRegex(
            serialized,
            r'"(createdAt|generatedAt|timestamp|prompt|raw_prompt|raw_output)"',
        )

    def test_release_receipt_rejects_existing_mismatch_and_failed_gates_emit_nothing(
        self,
    ) -> None:
        self.receipt.write_text("mismatch\n", encoding="utf-8")
        os.chmod(self.receipt, 0o600)
        with self.assertRaisesRegex(
            self.module.ReleaseError, "existing release receipt mismatch"
        ):
            self.module.write_release_receipt(self.receipt, {"schema_version": 1})

        self.receipt.unlink()
        with (
            self.assume_private_provenance_verified(),
            self.assertRaisesRegex(self.module.ReleaseError, "gate failed"),
            mock.patch.object(
                self.module,
                "run_contract_validators",
                side_effect=self.module.ReleaseError("gate failed"),
            ),
        ):
            evidence = Path(self.temporary_directory.name) / "failed-evidence"
            evidence.mkdir()
            self.module.validate_release(
                self.repository,
                routing_evidence=evidence,
                plugin_eval_executable=self.plugin_eval,
                receipt_output=self.receipt,
                composed_receipt=self.composed_receipt,
                **self.private_provenance_arguments,
            )
        self.assertFalse(self.receipt.exists())

    def test_normal_cli_requires_routing_evidence(self) -> None:
        with (
            mock.patch.object(
                self.module.sys,
                "argv",
                ["validate_public_release.py", str(self.repository)],
            ),
            mock.patch.object(self.module, "validate_release") as validate_release,
        ):
            self.assertEqual(self.module.main(), 1)
        validate_release.assert_not_called()

    def test_rejects_snapshot_mutation_during_identity_derivation(self) -> None:
        original = self.module.candidate_identities

        def derive_then_mutate(snapshot: Path, plugins: tuple[str, ...]) -> dict:
            identities = original(snapshot, plugins)
            readme = snapshot / "README.md"
            readme.write_text(readme.read_text(encoding="utf-8") + "\nmutated\n")
            return identities

        with (
            mock.patch.object(
                self.module, "candidate_identities", side_effect=derive_then_mutate
            ),
            self.assertRaisesRegex(
                self.module.ReleaseError,
                "private snapshot changed while release identities were derived",
            ),
        ):
            self.module.validate_release(self.repository, run_contracts=False)

    def test_contract_validation_runs_validators_and_release_owned_unit_suites(
        self,
    ) -> None:
        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(
            self.module.subprocess, "run", return_value=completed
        ) as run:
            self.module.run_contract_validators(self.repository, self.plugin_eval)

        validator_calls = [
            call
            for call in run.call_args_list
            if len(call.args[0]) >= 4
            and call.args[0][0] == self.module.sys.executable
            and call.args[0][1:3] == ["-I", "-B"]
            and Path(call.args[0][3]).name.startswith("validate_")
        ]
        validators = {Path(call.args[0][3]).name for call in validator_calls}
        self.assertEqual(
            validators,
            {
                "validate_plugin_runtime_roots.py",
                *(
                    Path(self.module.VALIDATOR_PATHS[plugin]).name
                    for plugin in self.module.PRODUCTION_VALIDATED_PLUGINS
                ),
            },
        )
        pytest_calls = [
            call
            for call in run.call_args_list
            if call.args[0][:5]
            == [self.module.sys.executable, "-I", "-B", "-m", "pytest"]
        ]
        self.assertEqual(len(pytest_calls), 1)
        pytest_call = pytest_calls[0]
        self.assertEqual(
            pytest_call.args[0][:9],
            [
                self.module.sys.executable,
                "-I",
                "-B",
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "--import-mode=importlib",
            ],
        )
        self.assertEqual(
            set(pytest_call.args[0][9:]),
            set(self.module.release_test_paths()),
        )
        self.assertEqual(pytest_call.kwargs["cwd"], self.repository)
        self.assertEqual(
            set(pytest_call.kwargs["env"]),
            {
                "HOME",
                "LANG",
                "LC_ALL",
                "PATH",
                "TEMP",
                "TMP",
                "TMPDIR",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
                "PLUGIN_EVAL_TEST_EXECUTABLE",
            },
        )
        self.assertEqual(pytest_call.kwargs["env"]["LANG"], "C.UTF-8")
        self.assertEqual(pytest_call.kwargs["env"]["LC_ALL"], "C.UTF-8")
        self.assertEqual(
            pytest_call.kwargs["env"]["PATH"], self.module.SAFE_NODE_SEARCH_PATH
        )
        self.assertEqual(
            pytest_call.kwargs["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"], "1"
        )
        self.assertTrue(Path(pytest_call.kwargs["env"]["HOME"]).is_absolute())
        self.assertEqual(
            pytest_call.kwargs["env"]["TEMP"], pytest_call.kwargs["env"]["TMP"]
        )
        self.assertEqual(
            pytest_call.kwargs["env"]["TMP"], pytest_call.kwargs["env"]["TMPDIR"]
        )
        self.assertEqual(
            pytest_call.kwargs["env"]["PLUGIN_EVAL_TEST_EXECUTABLE"],
            str(self.plugin_eval),
        )

    def test_promoted_task_witness_uses_exact_final_release_dispatch(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "", "")
        final_evidence = (
            "/evidence/../candidate-root",
            "/evidence/release-manifest.json",
            "/evidence/macos-receipt.json",
            "/evidence/linux-receipt.json",
            "/evidence/review-evidence.json",
        )
        promoted_plugins = (*self.module.PRODUCTION_VALIDATED_PLUGINS, "task-witness")
        with (
            mock.patch.object(
                self.module,
                "PRODUCTION_VALIDATED_PLUGINS",
                promoted_plugins,
            ),
            mock.patch.object(
                self.module,
                "run_private_python_child",
                return_value=completed,
            ) as run_child,
        ):
            self.module.run_contract_validators(
                self.repository,
                self.plugin_eval,
                task_witness_final_evidence=final_evidence,
            )

        commands = [call.args[1] for call in run_child.call_args_list]
        self.assertIn(
            [
                str(self.repository / self.module.VALIDATOR_PATHS["task-witness"]),
                str(self.repository),
                "--final-release",
                "--candidate-root",
                "/evidence/../candidate-root",
                "--release-manifest",
                "/evidence/release-manifest.json",
                "--macos-receipt",
                "/evidence/macos-receipt.json",
                "--linux-receipt",
                "/evidence/linux-receipt.json",
                "--review-evidence",
                "/evidence/review-evidence.json",
            ],
            commands,
        )
        self.assertIn(
            [
                str(self.repository / self.module.VALIDATOR_PATHS["rolecasting"]),
                str(self.repository),
            ],
            commands,
        )

    def test_private_python_child_ignores_hostile_import_environment(self) -> None:
        snapshot = Path(self.temporary_directory.name) / "isolated-snapshot"
        snapshot.mkdir()
        child = snapshot / "child.py"
        clean_marker = snapshot / "clean-marker"
        child.write_text(
            "import json\n"
            f"from pathlib import Path\nPath({str(clean_marker)!r}).write_text('clean')\n",
            encoding="utf-8",
        )
        hostile_marker = snapshot / "hostile-marker"
        for module in ("sitecustomize.py", "json.py"):
            (snapshot / module).write_text(
                f"from pathlib import Path\nPath({str(hostile_marker)!r}).write_text('hostile')\n",
                encoding="utf-8",
            )

        environment = self.module.private_python_child_environment(
            Path(self.temporary_directory.name) / "isolated-environment"
        )
        environment.update(
            {
                "PYTHONBREAKPOINT": "missing.breakpoint",
                "PYTHONHOME": str(snapshot),
                "PYTHONPATH": str(snapshot),
                "PYTHONSTARTUP": str(snapshot / "sitecustomize.py"),
                "PYTHONWARNINGS": "error",
            }
        )
        completed = self.module.run_private_python_child(
            snapshot,
            [str(child)],
            environment=environment,
            timeout=30,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(clean_marker.is_file())
        self.assertFalse(hostile_marker.exists())

    def test_mergecraft_release_evidence_is_in_the_immutable_snapshot(self) -> None:
        self.assertIn(
            "release/mergecraft",
            self.module.support_paths("mergecraft"),
        )
        self.assertIn("release/mergecraft", self.module.all_scope_paths())

    def test_plugin_eval_policy_and_calibration_are_common_identity_inputs(
        self,
    ) -> None:
        common_paths = {
            "release/plugin-eval-policy.json",
            "release/plugin-eval-baseline-v1.json",
        }
        for plugin in self.module.PRODUCTION_VALIDATED_PLUGINS:
            self.assertTrue(common_paths.issubset(self.module.support_paths(plugin)))

        before = self.module.candidate_identities(self.repository)["plugins"]
        policy_path = self.repository / "release/plugin-eval-policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["advisories"][0]["reason"] += " Release-owned policy change."
        policy_path.write_bytes(self.module.canonical_document(policy))
        after = self.module.candidate_identities(self.repository)["plugins"]

        for plugin in self.module.PRODUCTION_VALIDATED_PLUGINS:
            self.assertEqual(
                before[plugin]["plugin_sha256"], after[plugin]["plugin_sha256"]
            )
            self.assertNotEqual(
                before[plugin]["composite_sha256"],
                after[plugin]["composite_sha256"],
            )

    def test_generic_identity_includes_artifact_customs_without_changing_phase7_projection(
        self,
    ) -> None:
        before = self.module.candidate_identities(self.repository)
        artifact_readme = self.repository / "plugins/artifact-customs/README.md"
        artifact_readme.write_text(
            artifact_readme.read_text(encoding="utf-8") + "\nidentity probe\n",
            encoding="utf-8",
        )
        after = self.module.candidate_identities(self.repository)
        self.assertIn("artifact-customs", before["plugins"])
        self.assertNotEqual(
            before["plugins"]["artifact-customs"]["composite_sha256"],
            after["plugins"]["artifact-customs"]["composite_sha256"],
        )
        for plugin in self.module.CONTROL_PLUGINS:
            self.assertEqual(
                before["plugins"][plugin]["composite_sha256"],
                after["plugins"][plugin]["composite_sha256"],
            )
        self.assertEqual(
            self.module.CONTROL_PLUGINS,
            ("rolecasting", "versionkeeping", "mergecraft", "tricritical"),
        )

    def test_root_readme_release_command_matches_the_v4_parser_contract(self) -> None:
        readme = (REPOSITORY / "README.md").read_text(encoding="utf-8")
        required = {
            "--routing-evidence",
            "--composed-receipt",
            "--private-producer-witness",
            "--private-producer-registry",
            "--expected-frozen-private-identity-sha256",
            "--expected-private-commit-oid",
            "--expected-private-producer-package-sha256",
            "--expected-public-candidate-sha256",
            "--receipt-output",
        }
        documented = {token for token in readme.split() if token.startswith("--")}
        main_syntax = ast.parse(inspect.getsource(self.module.main))
        parser_options = {
            node.args[0].value
            for node in ast.walk(main_syntax)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.startswith("--")
        }
        self.assertTrue(required.issubset(parser_options))
        self.assertTrue(required.issubset(documented))
        self.assertTrue(documented.intersection(required).issubset(parser_options))
        self.assertFalse(
            {
                "--private-receipt",
                "--private-trust-anchor",
                "--private-remote-observation",
                "--private-evidence-bundle",
                "--frozen-private-identity",
                "--expected-private-producer-sha256",
            }
            & documented
        )

    def test_plugin_eval_calibration_derives_the_release_thresholds(self) -> None:
        policy = self.module.load_plugin_eval_policy(self.repository)
        self.assertEqual(
            policy["thresholds"],
            {
                "trigger_cost_tokens": {
                    "goodMax": 66,
                    "moderateMax": 254,
                    "heavyMax": 614,
                },
                "invoke_cost_tokens": {
                    "goodMax": 462,
                    "moderateMax": 4493,
                    "heavyMax": 17204,
                },
                "deferred_cost_tokens": {
                    "goodMax": 27,
                    "moderateMax": 7622,
                    "heavyMax": 58894,
                },
            },
        )

    def test_plugin_eval_calibration_fails_closed_on_manifest_drift(self) -> None:
        cases = (
            (
                "algorithm",
                lambda manifest: manifest.update(
                    {"quantile_algorithm": "nearest-rank-v1"}
                ),
                "quantile algorithm",
                True,
            ),
            (
                "sample count",
                lambda manifest: manifest.update({"sample_count": 19}),
                "sample count",
                True,
            ),
            (
                "unequal arrays",
                lambda manifest: manifest["measurements"]["trigger_cost_tokens"].pop(),
                "equal-length",
                True,
            ),
            (
                "unsorted array",
                lambda manifest: manifest["measurements"][
                    "invoke_cost_tokens"
                ].__setitem__(1, 1000),
                "sorted nonnegative",
                True,
            ),
            (
                "negative measurement",
                lambda manifest: manifest["measurements"][
                    "deferred_cost_tokens"
                ].__setitem__(0, -1),
                "sorted nonnegative",
                True,
            ),
            (
                "tool runtime",
                lambda manifest: manifest.update(
                    {"plugin_eval_runtime_sha256": "sha256:" + "a" * 64}
                ),
                "runtime digest mismatch",
                True,
            ),
            (
                "manifest digest",
                lambda manifest: manifest["measurements"][
                    "trigger_cost_tokens"
                ].__setitem__(0, 7),
                "manifest digest mismatch",
                False,
            ),
            *(
                (
                    f"forbidden {field}",
                    lambda manifest, field=field: manifest.update({field: ["ambient"]}),
                    "manifest schema drift",
                    True,
                )
                for field in (
                    "names",
                    "paths",
                    "contents",
                    "timestamps",
                    "source_roots",
                )
            ),
        )
        for label, mutate, message, refresh_digest in cases:
            with self.subTest(label=label):
                manifest = copy.deepcopy(self.calibration)
                mutate(manifest)
                self.store_calibration(
                    manifest,
                    refresh_manifest_digest=refresh_digest,
                    refresh_policy_digest=refresh_digest,
                )
                with self.assertRaisesRegex(self.module.ReleaseError, message):
                    self.module.load_plugin_eval_policy(self.repository)
                self.store_calibration(copy.deepcopy(self.calibration))

    def test_retired_review_plugin_has_no_source_discovery_or_install_route(
        self,
    ) -> None:
        marketplace = json.loads(
            (self.repository / ".claude-plugin/marketplace.json").read_text()
        )
        retired_names = {
            "thermos",
            "thermo-nuclear-review",
            "thermo-nuclear-code-quality-review",
        }
        self.assertTrue(
            retired_names.isdisjoint(self.module.PRODUCTION_VALIDATED_PLUGINS)
        )
        self.assertNotIn("validate_thermos.py", self.module.VALIDATOR_PATHS.values())
        repository_paths = {
            path.relative_to(self.repository).as_posix()
            for path in self.repository.rglob("*")
        }
        self.assertFalse(
            any(
                retired in path.lower()
                for path in repository_paths
                for retired in retired_names
            )
        )
        discovery = {
            item["name"]: " ".join(
                [item["name"], item["source"], item.get("category", "")]
            ).lower()
            for item in marketplace["plugins"]
        }
        self.assertTrue(
            all(
                retired not in text
                for text in discovery.values()
                for retired in retired_names
            )
        )
        installed_skills = {
            path.parent.name
            for item in marketplace["plugins"]
            for path in (self.repository / item["source"] / "skills").glob("*/SKILL.md")
        }
        self.assertTrue(retired_names.isdisjoint(installed_skills))

    def test_former_generic_and_deep_review_intents_have_only_tricritical_route(
        self,
    ) -> None:
        marketplace = json.loads(
            (self.repository / ".claude-plugin/marketplace.json").read_text()
        )
        names = {item["name"] for item in marketplace["plugins"]}
        for manifest_path in (
            "plugin.json",
            ".claude-plugin/plugin.json",
        ):
            review_routes = set()
            for name in names:
                manifest = json.loads(
                    (self.repository / f"plugins/{name}/{manifest_path}").read_text()
                )
                discovery_text = " ".join(
                    [manifest["description"], *manifest.get("keywords", [])]
                ).lower()
                if "review" in discovery_text:
                    review_routes.add(name)
            for _intent in ("review this change", "perform a deep review"):
                self.assertEqual(review_routes, {"tricritical"})

    def test_contract_validation_fails_when_release_owned_unit_suites_fail(
        self,
    ) -> None:
        def run(command, **_kwargs):
            if command[:5] == [self.module.sys.executable, "-I", "-B", "-m", "pytest"]:
                return subprocess.CompletedProcess(command, 1, "failed test", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            mock.patch.object(self.module.subprocess, "run", side_effect=run),
            self.assertRaisesRegex(
                self.module.ReleaseError, "release-owned unit suites failed"
            ),
        ):
            self.module.run_contract_validators(self.repository, self.plugin_eval)

    def test_plugin_eval_runs_pinned_bytes_after_live_runtime_aba_swap(self) -> None:
        self.install_real_plugin_eval_fixture()
        original_copy = self.module.copy_pinned_plugin_eval_runtime

        def copy_then_replace(*arguments, **keywords):
            copied = original_copy(*arguments, **keywords)
            self.plugin_eval.write_text(
                "#!/usr/bin/env python3\nraise SystemExit(73)\n"
            )
            os.chmod(self.plugin_eval, 0o755)
            return copied

        with mock.patch.object(
            self.module,
            "copy_pinned_plugin_eval_runtime",
            side_effect=copy_then_replace,
        ):
            evidence = self.module.run_plugin_evals(self.repository, self.plugin_eval)

        self.assertEqual(set(evidence), set(self.module.SKILL_PLUGINS))
        self.assertIn("SystemExit(73)", self.plugin_eval.read_text(encoding="utf-8"))

    def test_node_interpreter_rejects_shims_and_noncompliant_versions(self) -> None:
        environment_root = Path(self.temporary_directory.name) / "node-environment"
        environment = self.module.private_plugin_eval_environment(environment_root)
        shim = Path(self.temporary_directory.name) / "node-shim"
        shim.write_text("#!/bin/sh\necho v99.0.0\n", encoding="utf-8")
        os.chmod(shim, 0o700)
        with self.assertRaisesRegex(
            self.module.ReleaseError, "must not be a shebang shim"
        ):
            self.module.resolve_node_interpreter(
                Path(os.path.realpath(shim)), environment
            )

        node = Path(os.path.realpath(shutil.which("node") or ""))
        self.assertTrue(node.is_file())
        for version, message in (
            ("v19.9.9\n", "Node 20 or newer"),
            ("v20.0.0-pre\n", "strict semver"),
        ):
            with (
                self.subTest(version=version),
                mock.patch.object(
                    self.module.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(
                        [str(node), "--version"], 0, version, ""
                    ),
                ),
                self.assertRaisesRegex(self.module.ReleaseError, message),
            ):
                self.module.resolve_node_interpreter(node, environment)

    def test_node_interpreter_uses_private_environment_and_absolute_command(
        self,
    ) -> None:
        node = Path(
            os.path.realpath(
                shutil.which("node", path=self.module.SAFE_NODE_SEARCH_PATH) or ""
            )
        )
        self.assertTrue(node.is_file())
        observed = []

        def result(command, **kwargs):
            observed.append((command, kwargs["env"]))
            if command[1:] == ["--version"]:
                return subprocess.CompletedProcess(command, 0, "v20.0.0\n", "")
            return subprocess.CompletedProcess(
                command, 0, json.dumps(plugin_eval_report([], Path(command[3]))), ""
            )

        with (
            mock.patch.dict(
                os.environ,
                {
                    "NODE_OPTIONS": "--require /attacker/preload.js",
                    "NODE_PATH": "/attacker/node-modules",
                    "DYLD_INSERT_LIBRARIES": "/attacker/dylib",
                    "LD_PRELOAD": "/attacker/library",
                    "npm_config_prefix": "/attacker/npm",
                    "PATH": "/attacker/bin",
                },
                clear=False,
            ),
            mock.patch.object(self.module.subprocess, "run", side_effect=result),
        ):
            evidence = self.module.run_plugin_evals(self.repository, self.plugin_eval)

        self.assertEqual(set(evidence), set(self.module.SKILL_PLUGINS))
        for command, environment in observed:
            self.assertEqual(command[0], str(node))
            self.assertNotIn("NODE_OPTIONS", environment)
            self.assertNotIn("NODE_PATH", environment)
            self.assertNotIn("DYLD_INSERT_LIBRARIES", environment)
            self.assertNotIn("LD_PRELOAD", environment)
            self.assertNotIn("npm_config_prefix", environment)
            self.assertEqual(environment["PATH"], "/usr/bin:/bin")
            self.assertTrue(Path(environment["HOME"]).is_absolute())
            self.assertTrue(Path(environment["TMPDIR"]).is_absolute())
        analysis_commands = [
            command
            for command, _environment in observed
            if len(command) > 2 and command[2] == "analyze"
        ]
        self.assertEqual(len(analysis_commands), len(self.module.SKILL_PLUGINS))
        self.assertTrue(
            all(
                command[1].endswith("scripts/plugin-eval.js")
                for command in analysis_commands
            )
        )
        self.assertTrue(
            {Path(command[3]).name for command in analysis_commands}.isdisjoint(
                self.module.REGISTERED_RUNTIME_PACKAGES
            )
        )
        serialized = json.dumps(evidence, sort_keys=True)
        self.assertNotIn(str(node), serialized)
        interpreter = evidence["rolecasting"]["tool_runtime"]["interpreter"]
        self.assertEqual(interpreter["coverage"], "main-executable-bytes-only")
        self.assertEqual(
            interpreter["limitations"],
            [
                "dynamic-loader-and-shared-library-bytes-are-not-bound",
                "pathname-exec-time-ABA-is-not-bound",
            ],
        )

    def test_node_resolution_uses_only_the_fixed_fallback_path(self) -> None:
        with (
            mock.patch.object(
                self.module.shutil,
                "which",
                return_value="/safe/node",
            ) as which,
            mock.patch.object(
                self.module.os.path, "realpath", return_value="/safe/node"
            ),
        ):
            self.assertEqual(self.module._canonical_node_path(None), Path("/safe/node"))

        which.assert_called_once_with("node", path=self.module.SAFE_NODE_SEARCH_PATH)

    def test_node_resolution_rejects_a_symlinked_parent_directory(self) -> None:
        root = Path(os.path.realpath(self.temporary_directory.name))
        target_directory = root / "target"
        target_directory.mkdir()
        node = target_directory / "node"
        node.write_bytes(b"not-a-shebang")
        os.chmod(node, 0o700)
        linked_parent = root / "linked"
        linked_parent.symlink_to(target_directory, target_is_directory=True)
        linked_leaf = root / "node-link"
        linked_leaf.symlink_to(node)

        contents, _metadata = self.module._read_nofollow_regular_file(
            node, "node executable"
        )
        self.assertEqual(contents, b"not-a-shebang")

        with self.assertRaisesRegex(self.module.ReleaseError, "unavailable or unsafe"):
            self.module._read_nofollow_regular_file(
                linked_parent / "node", "node executable"
            )
        with self.assertRaisesRegex(self.module.ReleaseError, "unavailable or unsafe"):
            self.module.resolve_node_interpreter(linked_parent / "node", {})
        with self.assertRaisesRegex(self.module.ReleaseError, "unavailable or unsafe"):
            self.module._read_nofollow_regular_file(linked_leaf, "node executable")
        with self.assertRaisesRegex(self.module.ReleaseError, "unavailable or unsafe"):
            self.module.resolve_node_interpreter(linked_leaf, {})

    def test_node_resolution_fails_closed_without_descriptor_primitives(self) -> None:
        with (
            mock.patch.object(self.module.os, "O_NOFOLLOW", None),
            self.assertRaisesRegex(
                self.module.ReleaseError, "requires O_NOFOLLOW and O_DIRECTORY"
            ),
        ):
            self.module._read_nofollow_regular_file(
                Path(os.path.realpath(shutil.which("node") or "")), "node executable"
            )

    def test_node_snapshot_rejects_an_in_place_mutation_while_reading(self) -> None:
        root = Path(os.path.realpath(self.temporary_directory.name))
        node = root / "mutable-node"
        node.write_bytes(b"first executable bytes\n")
        os.chmod(node, 0o700)
        original_read = self.module.os.read
        mutated = False

        def read(descriptor, size):
            nonlocal mutated
            if not mutated:
                mutated = True
                node.write_bytes(b"second executable bytes\n")
                os.chmod(node, 0o700)
            return original_read(descriptor, size)

        with (
            mock.patch.object(self.module.os, "read", side_effect=read),
            self.assertRaisesRegex(
                self.module.ReleaseError, "changed while it was read"
            ),
        ):
            self.module._read_nofollow_regular_file(node, "node executable")

    def test_node_interpreter_drift_blocks_evaluation_and_changes_evidence_digest(
        self,
    ) -> None:
        environment = self.module.private_plugin_eval_environment(
            Path(self.temporary_directory.name) / "drift-environment"
        )
        identity = {
            "path": str(Path(os.path.realpath(shutil.which("node") or ""))),
            "sha256": "sha256:" + "a" * 64,
            "version": "v20.0.0",
        }
        with (
            mock.patch.object(
                self.module,
                "_node_binary_digest",
                return_value="sha256:" + "b" * 64,
            ),
            self.assertRaisesRegex(
                self.module.ReleaseError, "changed during plugin evaluation"
            ),
        ):
            self.module.revalidate_node_interpreter(identity)

        policy = self.module.load_plugin_eval_policy(self.repository)
        base_runtime = {
            "plugin_manifest_version": "0.1.2",
            "plugin_manifest_sha256": "sha256:" + "1" * 64,
            "package_manifest_version": "0.1.0",
            "runtime_tree_sha256": "sha256:" + "2" * 64,
            "interpreter": self.module.public_node_interpreter_evidence(identity),
        }

        def evaluator(command, **_kwargs):
            return subprocess.CompletedProcess(
                command, 0, json.dumps(plugin_eval_report([], Path(command[3]))), ""
            )

        with (
            mock.patch.object(self.module, "revalidate_node_interpreter"),
            mock.patch.object(self.module.subprocess, "run", side_effect=evaluator),
        ):
            first = self.module.run_pinned_plugin_evals(
                self.repository,
                self.plugin_eval,
                policy,
                base_runtime,
                environment,
                node_interpreter=identity,
            )
            changed = {
                **base_runtime,
                "interpreter": {
                    **self.module.public_node_interpreter_evidence(identity),
                    "sha256": "sha256:" + "c" * 64,
                },
            }
            changed_identity = {**identity, "sha256": "sha256:" + "c" * 64}
            second = self.module.run_pinned_plugin_evals(
                self.repository,
                self.plugin_eval,
                policy,
                changed,
                environment,
                node_interpreter=changed_identity,
            )
        self.assertNotEqual(
            first["rolecasting"]["policy_projection_sha256"],
            second["rolecasting"]["policy_projection_sha256"],
        )
        self.assertEqual(
            first["rolecasting"]["tool_runtime"]["interpreter"]["sha256"],
            identity["sha256"],
        )

    def test_node_version_probe_is_bracketed_once_and_analysis_uses_byte_only_checks(
        self,
    ) -> None:
        environment = self.module.private_plugin_eval_environment(
            Path(self.temporary_directory.name) / "ordering-environment"
        )
        node = Path(os.path.realpath(shutil.which("node") or ""))
        events = []

        def digest(path):
            events.append(("read", path))
            return "sha256:" + "a" * 64

        def version(path, _environment):
            events.append(("version", path))
            return "v20.0.0"

        with (
            mock.patch.object(self.module, "_node_binary_digest", side_effect=digest),
            mock.patch.object(self.module, "_node_version", side_effect=version),
        ):
            identity = self.module.resolve_node_interpreter(node, environment)

        self.assertEqual([event[0] for event in events], ["read", "version", "read"])
        runtime = {
            "plugin_manifest_version": "0.1.2",
            "plugin_manifest_sha256": "sha256:" + "1" * 64,
            "package_manifest_version": "0.1.0",
            "runtime_tree_sha256": "sha256:" + "2" * 64,
            "interpreter": self.module.public_node_interpreter_evidence(identity),
        }
        policy = self.module.load_plugin_eval_policy(self.repository)
        events.clear()

        def evaluator(command, **_kwargs):
            events.append(("child", command[2]))
            return subprocess.CompletedProcess(
                command, 0, json.dumps(plugin_eval_report([], Path(command[3]))), ""
            )

        with (
            mock.patch.object(self.module, "_node_binary_digest", side_effect=digest),
            mock.patch.object(self.module.subprocess, "run", side_effect=evaluator),
        ):
            self.module.run_pinned_plugin_evals(
                self.repository,
                self.plugin_eval,
                policy,
                runtime,
                environment,
                node_interpreter=identity,
            )

        self.assertNotIn("version", [event[0] for event in events])
        self.assertEqual(
            [event[0] for event in events],
            [
                item
                for _plugin in self.module.SKILL_PLUGINS
                for item in ("read", "child", "read")
            ],
        )

    def test_plugin_eval_accepts_warnings_and_reports_each_control_plugin(self) -> None:
        def result(command, **_kwargs):
            if command[1:] == ["--version"]:
                return subprocess.CompletedProcess(command, 0, "v20.0.0\n", "")
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps(
                    plugin_eval_report(
                        [{"id": "deferred-cost", "category": "quality"}],
                        Path(command[3]),
                    )
                ),
                "",
            )

        with mock.patch.object(
            self.module.subprocess,
            "run",
            side_effect=result,
        ) as run:
            evidence = self.module.run_plugin_evals(self.repository, self.plugin_eval)

        self.assertEqual(set(evidence), set(self.module.SKILL_PLUGINS))
        self.assertTrue(
            all(item["warnings"] == ["deferred-cost"] for item in evidence.values())
        )
        self.assertTrue(
            all(
                item["policy_projection_sha256"].startswith("sha256:")
                and item["outcome"] == "pass"
                for item in evidence.values()
            )
        )
        self.assertTrue(
            all(
                item["analyzed_plugin"]["target_kind"] == "plugin"
                for item in evidence.values()
            )
        )
        targets = {
            Path(call.args[0][3]).name
            for call in run.call_args_list
            if len(call.args[0]) > 3 and call.args[0][2] == "analyze"
        }
        self.assertEqual(targets, set(self.module.SKILL_PLUGINS))
        self.assertTrue(targets.isdisjoint(self.module.REGISTERED_RUNTIME_PACKAGES))
        self.assertTrue(
            all(
                item["analyzed_plugin"]["entry_path"] == f"plugins/{name}/plugin.json"
                for name, item in evidence.items()
            )
        )

    def test_plugin_eval_rejects_legacy_analyzed_plugin_entry_path(self) -> None:
        target = self.repository / "plugins" / "tricritical"
        report = plugin_eval_report([], target)
        report["target"]["entryPath"] = str(target / ".codex-plugin" / "plugin.json")
        policy = self.module.load_plugin_eval_policy(self.repository)

        with self.assertRaisesRegex(
            self.module.ReleaseError, "plugin-eval report target identity drift"
        ):
            self.module.validate_plugin_eval_report(
                "tricritical", report, policy, self.repository
            )

    def test_plugin_eval_authoritative_projection_ignores_ambient_report_state(
        self,
    ) -> None:
        baseline_evidence = self.run_plugin_reports(
            lambda target: plugin_eval_report([], target)
        )
        policy = self.module.load_plugin_eval_policy(self.repository)
        native_findings = list(policy["native_budget_findings"].values())

        def ambient_variant(target: Path) -> dict:
            report = plugin_eval_report(native_findings, target)
            report["createdAt"] = "2099-12-31T23:59:59Z"
            report["summary"]["score"] = -500
            report["summary"]["grade"] = "ambient-grade"
            report["summary"]["riskLevel"] = "ambient-risk"
            report["summary"]["checkCounts"] = {"ambient": 999}
            report["summary"]["scoreBreakdown"] = {"ambient": 999}
            report["ambientCalibration"] = {
                "sampleRoots": ["/Users/ambient-home/plugin-eval/samples"],
                "timestamp": "2099-12-31T23:59:59Z",
            }
            for budget in report["budgets"].values():
                budget["thresholds"] = {
                    "goodMax": 0,
                    "moderateMax": 1,
                    "heavyMax": 2,
                }
                budget["band"] = "ambient-band"
                budget["sampleRoot"] = "/Users/ambient-home/plugin-eval/samples"
            return report

        varied_evidence = self.run_plugin_reports(ambient_variant)

        self.assertEqual(varied_evidence, baseline_evidence)
        serialized = json.dumps(varied_evidence, sort_keys=True)
        self.assertNotIn("ambient", serialized)
        self.assertNotIn("2099-12-31", serialized)
        for item in varied_evidence.values():
            self.assertTrue(
                {
                    "score",
                    "grade",
                    "risk_level",
                    "raw_report_sha256",
                    "semantic_report_sha256",
                    "thresholds",
                    "bands",
                    "sample_roots",
                }.isdisjoint(item)
            )

    def test_plugin_eval_release_band_boundaries(self) -> None:
        thresholds = self.module.load_plugin_eval_policy(self.repository)["thresholds"]
        for metric, metric_thresholds in thresholds.items():
            cases = (
                (metric_thresholds["goodMax"], "good"),
                (metric_thresholds["goodMax"] + 1, "moderate"),
                (metric_thresholds["moderateMax"], "moderate"),
                (metric_thresholds["moderateMax"] + 1, "heavy"),
                (metric_thresholds["heavyMax"], "heavy"),
                (metric_thresholds["heavyMax"] + 1, "excessive"),
            )
            for value, expected in cases:
                with self.subTest(metric=metric, value=value):
                    self.assertEqual(
                        self.module.plugin_eval_budget_band(value, metric_thresholds),
                        expected,
                    )

    def test_plugin_eval_unknown_budget_finding_blocks(self) -> None:
        def unknown_budget(target: Path) -> dict:
            return plugin_eval_report(
                [
                    {
                        "id": "future-budget-finding",
                        "category": "budget",
                        "status": "warn",
                        "severity": "warning",
                    }
                ],
                target,
            )

        with self.assertRaisesRegex(
            self.module.ReleaseError, "unknown plugin-eval budget finding"
        ):
            self.run_plugin_reports(unknown_budget)

    def test_plugin_eval_runtime_exception_rejects_component_drift(self) -> None:
        finding = copy.deepcopy(
            self.module.load_plugin_eval_policy(self.repository)[
                "native_budget_findings"
            ]["deferred_cost_tokens-budget-high"]
        )

        def drifted_component(target: Path) -> dict:
            if target.name != "versionkeeping":
                return plugin_eval_report([], target)
            return plugin_eval_report(
                [finding],
                target,
                deferred=66448,
                required_components=[
                    (
                        "skills/checkpointing-and-publishing-git-work/scripts/check_eval_gate.py",
                        "skills/checkpointing-and-publishing-git-work/scripts/other.py",
                        38792,
                    )
                ],
            )

        with self.assertRaisesRegex(
            self.module.ReleaseError, "runtime component evidence drift"
        ):
            self.run_plugin_reports(drifted_component)

    def test_plugin_eval_rejects_fail_or_error_checks(self) -> None:
        for status, severity in (("fail", "warning"), ("warn", "error")):

            def plugin_eval_result(
                command,
                status=status,
                severity=severity,
                **_kwargs,
            ):
                if command[1:] == ["--version"]:
                    return subprocess.CompletedProcess(command, 0, "v20.0.0\n", "")
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps(
                        plugin_eval_report(
                            [
                                {
                                    "id": "blocking-check",
                                    "category": "quality",
                                    "status": status,
                                    "severity": severity,
                                    "message": "must block",
                                }
                            ],
                            Path(command[3]),
                        )
                    ),
                    "",
                )

            with (
                self.subTest(status=status, severity=severity),
                mock.patch.object(
                    self.module.subprocess,
                    "run",
                    side_effect=plugin_eval_result,
                ),
                self.assertRaisesRegex(
                    self.module.ReleaseError, "blocking-check: must block"
                ),
            ):
                self.module.run_plugin_evals(self.repository, self.plugin_eval)

    def test_plugin_eval_accepts_only_the_bounded_runtime_cost_advisories(
        self,
    ) -> None:
        advisory = {
            "id": "deferred_cost_tokens-budget-high",
            "category": "budget",
            "status": "fail",
            "severity": "error",
            "message": "deferred_cost_tokens is excessive relative to the current Codex baseline.",
            "penalty": 14,
            "remediation": [
                "Reduce repeated instruction text and move detail into deferred supporting files."
            ],
            "source": "core",
        }

        def result(command, **kwargs):
            if command[1:] == ["--version"]:
                return subprocess.CompletedProcess(command, 0, "v20.0.0\n", "")
            self.assertEqual(kwargs["cwd"], self.repository)
            self.assertNotIn("NODE_OPTIONS", kwargs["env"])
            target = Path(command[3])
            if target.name == "versionkeeping":
                report = plugin_eval_report([advisory], target, deferred=66448)
            elif target.name == "mergecraft":
                report = plugin_eval_report(
                    [advisory],
                    target,
                    trigger=587,
                    invoke=8135,
                    deferred=81029,
                    required_components=[
                        (
                            "skills/addressing-pr-review-feedback/scripts/review_feedback_state.py",
                            "skills/addressing-pr-review-feedback/scripts/review_feedback_state.py",
                            10206,
                        ),
                        (
                            "skills/publishing-reviewable-prs/scripts/publication_receipts.py",
                            "skills/publishing-reviewable-prs/scripts/publication_receipts.py",
                            8068,
                        ),
                        (
                            "skills/graphite/scripts/submit_draft_stack.py",
                            "skills/graphite/scripts/submit_draft_stack.py",
                            7729,
                        ),
                    ],
                )
            else:
                report = plugin_eval_report([], target)
            return subprocess.CompletedProcess(command, 0, json.dumps(report), "")

        with mock.patch.object(self.module.subprocess, "run", side_effect=result):
            evidence = self.module.run_plugin_evals(self.repository, self.plugin_eval)

        self.assertEqual(
            evidence["versionkeeping"]["advisories"],
            ["deferred_cost_tokens-budget-high"],
        )
        self.assertEqual(
            evidence["mergecraft"]["advisories"],
            ["deferred_cost_tokens-budget-high"],
        )
        self.assertTrue(
            all(
                not evidence[name]["advisories"]
                for name in ("rolecasting", "tricritical")
            )
        )

    def test_plugin_eval_accepts_only_exact_optional_legal_field_advisories(
        self,
    ) -> None:
        def exact_findings(target: Path) -> dict:
            return plugin_eval_report(
                self.optional_legal_field_deductions(target.name), target
            )

        def exact_result(command, **_kwargs):
            if command[1:] == ["--version"]:
                return subprocess.CompletedProcess(command, 0, "v20.0.0\n", "")
            return subprocess.CompletedProcess(
                command, 0, json.dumps(exact_findings(Path(command[3]))), ""
            )

        with mock.patch.object(
            self.module.subprocess,
            "run",
            side_effect=exact_result,
        ):
            evidence = self.module.run_plugin_evals(self.repository, self.plugin_eval)

        self.assertTrue(
            all(
                item["advisories"]
                == [
                    "interface-missing-privacyPolicyURL",
                    "interface-missing-termsOfServiceURL",
                ]
                for item in evidence.values()
            )
        )

        def drifted_finding(target: Path) -> dict:
            deductions = self.optional_legal_field_deductions(target.name)
            if target.name == "rolecasting":
                deductions[0]["penalty"] = 13
            return plugin_eval_report(deductions, target)

        def drifted_result(command, **_kwargs):
            if command[1:] == ["--version"]:
                return subprocess.CompletedProcess(command, 0, "v20.0.0\n", "")
            return subprocess.CompletedProcess(
                command, 0, json.dumps(drifted_finding(Path(command[3]))), ""
            )

        with (
            mock.patch.object(
                self.module.subprocess,
                "run",
                side_effect=drifted_result,
            ),
            self.assertRaisesRegex(
                self.module.ReleaseError, "advisory deduction drift"
            ),
        ):
            self.module.run_plugin_evals(self.repository, self.plugin_eval)

    def test_plugin_eval_runtime_exception_uses_the_release_heavy_max(self) -> None:
        advisory = {
            "id": "deferred_cost_tokens-budget-high",
            "category": "budget",
            "status": "fail",
            "severity": "error",
            "message": "deferred_cost_tokens is excessive relative to the current Codex baseline.",
            "penalty": 14,
            "remediation": [
                "Reduce repeated instruction text and move detail into deferred supporting files."
            ],
            "source": "core",
        }

        def result(command, **_kwargs):
            if command[1:] == ["--version"]:
                return subprocess.CompletedProcess(command, 0, "v20.0.0\n", "")
            target = Path(command[3])
            report = plugin_eval_report(
                [advisory] if target.name == "versionkeeping" else [],
                target,
                deferred=90000 if target.name == "versionkeeping" else 58894,
            )
            report["budgets"]["deferred_cost_tokens"]["thresholds"] = {
                "goodMax": 1,
                "moderateMax": 2,
                "heavyMax": 100000,
            }
            report["budgets"]["deferred_cost_tokens"]["band"] = "good"
            return subprocess.CompletedProcess(command, 0, json.dumps(report), "")

        with mock.patch.object(self.module.subprocess, "run", side_effect=result):
            evidence = self.module.run_plugin_evals(self.repository, self.plugin_eval)

        self.assertEqual(
            evidence["versionkeeping"]["advisories"],
            ["deferred_cost_tokens-budget-high"],
        )

    def test_plugin_eval_rejects_wrong_runtime_package_and_target(self) -> None:
        manifest_directory = self.plugin_eval_manifest.parent
        manifest_directory_target = manifest_directory.with_name(".codex-plugin-real")
        manifest_directory.rename(manifest_directory_target)
        manifest_directory.symlink_to(
            manifest_directory_target, target_is_directory=True
        )
        with self.assertRaisesRegex(
            self.module.ReleaseError, "distribution manifest is missing or unsafe"
        ):
            self.module.run_plugin_evals(self.repository, self.plugin_eval)
        manifest_directory.unlink()
        manifest_directory_target.rename(manifest_directory)

        self.plugin_eval_manifest.write_text(
            self.plugin_eval_manifest.read_text() + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            self.module.ReleaseError, "distribution manifest identity drift"
        ):
            self.module.run_plugin_evals(self.repository, self.plugin_eval)

        self.plugin_eval_manifest.write_bytes(self.plugin_eval_manifest_original)
        self.plugin_eval.write_text(self.plugin_eval.read_text() + "\n// forged\n")
        with self.assertRaisesRegex(
            self.module.ReleaseError, "runtime package digest drift"
        ):
            self.module.run_plugin_evals(self.repository, self.plugin_eval)

        self.plugin_eval.write_bytes(self.plugin_eval_original)

        def forged_target(target: Path) -> dict:
            report = plugin_eval_report([], target)
            report["target"]["path"] = str(REPOSITORY / "plugins" / target.name)
            return report

        with self.assertRaisesRegex(
            self.module.ReleaseError, "report target identity drift"
        ):
            self.run_plugin_reports(forged_target)

    def test_plugin_eval_rejects_same_id_deduction_drift_and_incoherent_report(
        self,
    ) -> None:
        def same_id_drift(target: Path) -> dict:
            advisory = self.versionkeeping_advisory()
            if target.name != "versionkeeping":
                return plugin_eval_report([], target)
            advisory["penalty"] = 13
            advisory.pop("component_tokens")
            return plugin_eval_report([advisory], target)

        with self.assertRaisesRegex(self.module.ReleaseError, "budget finding drift"):
            self.run_plugin_reports(same_id_drift)

        def incoherent(target: Path) -> dict:
            report = plugin_eval_report([], target)
            report["budgets"]["trigger_cost_tokens"]["components"][0]["tokens"] += 1
            return report

        with self.assertRaisesRegex(
            self.module.ReleaseError, "component tokens do not sum"
        ):
            self.run_plugin_reports(incoherent)

    def test_plugin_eval_requires_the_named_gate_to_cause_the_excess(self) -> None:
        def noncausal(target: Path) -> dict:
            if target.name != "versionkeeping":
                return plugin_eval_report([], target)
            advisory = self.versionkeeping_advisory(1000)
            advisory.pop("component_tokens")
            return plugin_eval_report(
                [advisory], target, component_tokens=1000, deferred=66448
            )

        with self.assertRaisesRegex(
            self.module.ReleaseError, "not the causal budget excess"
        ):
            self.run_plugin_reports(noncausal)


if __name__ == "__main__":
    unittest.main()
