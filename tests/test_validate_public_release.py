from __future__ import annotations

import copy
import hashlib
import importlib.util
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from tests import phase7_v4_fixture as fixture

REPOSITORY = Path(__file__).resolve().parents[1]
VALIDATOR = REPOSITORY / "scripts" / "validate_public_release.py"


def load_validator_module():
    specification = importlib.util.spec_from_file_location("public_release", VALIDATOR)
    module = importlib.util.module_from_spec(specification)
    assert specification.loader is not None
    specification.loader.exec_module(module)
    return module


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
            "entryPath": str(target / ".codex-plugin/plugin.json"),
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
                        "path": str(target / ".codex-plugin/plugin.json"),
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
                        "path": str(target / ".codex-plugin/plugin.json"),
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
            "entryPath: path.join(target, '.codex-plugin/plugin.json'), "
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
        self.assertEqual(set(identities["plugins"]), set(self.module.VALIDATED_PLUGINS))
        self.module.validate_release(
            self.repository,
            expected=identities,
            run_contracts=False,
        )

    def test_code_only_task_witness_is_marketplace_validated_without_expanding_phase7(
        self,
    ) -> None:
        self.assertEqual(
            self.module.CONTROL_PLUGINS,
            ("rolecasting", "versionkeeping", "mergecraft", "tricritical"),
            "Artifact Customs must not alter the four-plugin Phase 7 control projection.",
        )
        expected_public_plugins = self.module.CONTROL_PLUGINS + ("artifact-customs",)
        expected_validated = expected_public_plugins + ("task-witness",)
        self.assertEqual(self.module.SKILL_PLUGINS, expected_public_plugins)
        self.assertEqual(self.module.RUNTIME_PACKAGES, ("task-witness",))
        self.assertEqual(
            self.module.VALIDATED_PLUGINS,
            expected_validated,
            "Task Witness must be identity-validated without entering skill evaluation.",
        )
        self.assertNotIn("task-witness", self.module.SKILL_PLUGINS)
        self.assertEqual(
            self.module.MARKETPLACE_PLUGINS,
            {
                "tricritical": "./plugins/tricritical",
                "rolecasting": "./plugins/rolecasting",
                "versionkeeping": "./plugins/versionkeeping",
                "mergecraft": "./plugins/mergecraft",
                "artifact-customs": "./plugins/artifact-customs",
                "task-witness": "./plugins/task-witness",
            },
            "The public marketplace inventory must include the code-only Task Witness package.",
        )
        self.assertEqual(
            set(self.module.VALIDATOR_PATHS),
            set(expected_validated),
            "Every public plugin must have an exact validator entry; arbitrary validators are forbidden.",
        )
        self.assertIn(
            "tests/plugins/test_task_witness_launcher.py",
            self.module.PLUGIN_SUPPORT_PATHS["task-witness"],
            "The public Task Witness release gate must execute the launcher contract suite.",
        )
        self.assertIn(
            "release/task-witness/source-shape-review.json",
            self.module.PLUGIN_SUPPORT_PATHS["task-witness"],
            "The public Task Witness release scope must retain its reviewed source shape.",
        )
        self.assertTrue(
            {
                "tests/plugins/review_evidence_fixtures.py",
                "tests/plugins/test_rolecasting_review_evidence.py",
                "tests/plugins/test_task_witness_review_evidence.py",
                "tests/plugins/test_tricritical_review_evidence.py",
            }.isdisjoint(self.module.PLUGIN_SUPPORT_PATHS["task-witness"]),
            "The standalone Task Witness release must not own semantic-consumer tests.",
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
            self.module.validate_expected_identities(mismatched, identities)

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
            self.module.validate_expected_identities(malformed, identities)

    def test_strict_json_rejects_exponent_overflow_and_retains_booleans(self) -> None:
        with self.assertRaisesRegex(self.module.ReleaseError, "non-finite JSON value"):
            self.module.strict_json('{"probe": 1e999}', "strict boundary")
        self.assertEqual(
            self.module.strict_json('{"enabled": true}', "strict boundary"),
            {"enabled": True},
        )

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
        original_verify = self.module.verify_public_release_evidence
        verified_public_roots: list[Path] = []

        def verify_then_advance(**arguments):
            verified = original_verify(**arguments)
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
            return verified

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

        self.assertEqual(run.call_count, len(self.module.VALIDATED_PLUGINS))
        for call in run.call_args_list:
            command = call.args[0]
            self.assertEqual(Path(command[1]).parent, snapshot / "scripts")
            self.assertEqual(command[2], str(snapshot))

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
        completed = subprocess.run(
            [sys.executable, str(VALIDATOR), "--help"],
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
                    "entry_path": "plugins/tricritical/.codex-plugin/plugin.json",
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
        expected = self.module.validate_release(self.repository, run_contracts=False)
        with (
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

        def derive_then_mutate(snapshot: Path) -> dict:
            identities = original(snapshot)
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
            if len(call.args[0]) >= 2
            and call.args[0][0] == self.module.sys.executable
            and Path(call.args[0][1]).name.startswith("validate_")
        ]
        validators = {Path(call.args[0][1]).name for call in validator_calls}
        self.assertEqual(
            validators,
            {
                "validate_plugin_runtime_roots.py",
                "validate_rolecasting.py",
                "validate_versionkeeping.py",
                "validate_mergecraft.py",
                "validate_tricritical.py",
                "validate_artifact_customs.py",
                "validate_task_witness.py",
            },
        )
        pytest_calls = [
            call
            for call in run.call_args_list
            if call.args[0][:3] == [self.module.sys.executable, "-m", "pytest"]
        ]
        self.assertEqual(len(pytest_calls), 1)
        pytest_call = pytest_calls[0]
        self.assertEqual(
            set(pytest_call.args[0][6:]),
            set(self.module.release_test_paths()),
        )
        self.assertEqual(pytest_call.kwargs["cwd"], self.repository)
        self.assertEqual(pytest_call.kwargs["env"]["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(pytest_call.kwargs["env"]["PYTHONNOUSERSITE"], "1")
        self.assertEqual(
            pytest_call.kwargs["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"], "1"
        )
        self.assertNotIn("PYTHONPATH", pytest_call.kwargs["env"])
        self.assertEqual(
            pytest_call.kwargs["env"]["PLUGIN_EVAL_TEST_EXECUTABLE"],
            str(self.plugin_eval),
        )

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
        for plugin in self.module.VALIDATED_PLUGINS:
            self.assertTrue(common_paths.issubset(self.module.support_paths(plugin)))

        before = self.module.candidate_identities(self.repository)["plugins"]
        policy_path = self.repository / "release/plugin-eval-policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["advisories"][0]["reason"] += " Release-owned policy change."
        policy_path.write_bytes(self.module.canonical_document(policy))
        after = self.module.candidate_identities(self.repository)["plugins"]

        for plugin in self.module.VALIDATED_PLUGINS:
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
        parser_options = {
            line.split('"', 2)[1]
            for line in inspect.getsource(self.module.main).splitlines()
            if 'parser.add_argument("--' in line
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
        self.assertTrue(retired_names.isdisjoint(self.module.VALIDATED_PLUGINS))
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
            ".claude-plugin/plugin.json",
            ".codex-plugin/plugin.json",
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
            if command[:3] == [self.module.sys.executable, "-m", "pytest"]:
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
        self.assertNotIn(
            "task-witness", {Path(command[3]).name for command in analysis_commands}
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
        self.assertNotIn("task-witness", targets)

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
