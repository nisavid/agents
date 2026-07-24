from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
VALIDATOR = REPOSITORY / "scripts" / "validate_artifact_customs.py"
PLUGIN_ROOT = REPOSITORY / "plugins" / "artifact-customs"
SKILLS = (
    "assessing-third-party-components",
    "adopting-third-party-components",
    "maintaining-third-party-components",
)
OUTWARD_PLUGINS = {
    "rolecasting",
    "tricritical",
    "versionkeeping",
    "mergecraft",
}
EXPECTED_EXTERNAL_CALLS = {
    "assessing-third-party-components": [
        "rolecasting:delegating-cross-agent-work",
        "tricritical:review",
        "tricritical:adjudicate",
    ],
    "adopting-third-party-components": [
        "rolecasting:delegating-cross-agent-work",
        "tricritical:loop",
        "versionkeeping:checkpointing-and-publishing-git-work",
        "mergecraft:publishing-reviewable-prs",
        "mergecraft:getting-prs-merged",
    ],
    "maintaining-third-party-components": [
        "rolecasting:delegating-cross-agent-work",
        "tricritical:loop",
        "versionkeeping:checkpointing-and-publishing-git-work",
        "mergecraft:publishing-reviewable-prs",
        "mergecraft:getting-prs-merged",
    ],
}
REQUIRED_RUNTIME_FILES = {
    ".claude-plugin/plugin.json",
    ".codex-plugin/plugin.json",
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "topology.json",
    "references/invocation-envelope.json",
    "references/scheduler-adapters.json",
    *(f"skills/{skill}/SKILL.md" for skill in SKILLS),
    *(f"skills/{skill}/agents/openai.yaml" for skill in SKILLS),
}


class ValidateArtifactCustomsContractTests(unittest.TestCase):
    def test_production_validator_is_present_for_the_public_contract(self) -> None:
        self.assertTrue(
            VALIDATOR.is_file(),
            "Artifact Customs production validator is absent: "
            "scripts/validate_artifact_customs.py must validate the public contract.",
        )

    def test_public_plugin_root_is_present_for_the_public_contract(self) -> None:
        self.assertTrue(
            PLUGIN_ROOT.is_dir(),
            "Artifact Customs public plugin is absent: "
            "plugins/artifact-customs must supply the three-skill public surface.",
        )

    def test_source_stage_contract_accepts_the_current_candidate(self) -> None:
        self.assertTrue(
            VALIDATOR.is_file() and PLUGIN_ROOT.is_dir(),
            "Artifact Customs source-stage contract cannot run until its production validator and public plugin exist.",
        )
        result = subprocess.run(
            [sys.executable, str(VALIDATOR), str(REPOSITORY), "--source-stage"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("source-stage", result.stdout)


class ArtifactCustomsExactContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repository = Path(self.temporary.name) / "repository"
        shutil.copytree(
            REPOSITORY,
            self.repository,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
        )
        self.plugin = self.repository / "plugins" / "artifact-customs"
        self.validator = self.repository / "scripts" / "validate_artifact_customs.py"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def validate(
        self, *, source_stage: bool = False
    ) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(self.validator), str(self.repository)]
        if source_stage:
            command.append("--source-stage")
        return subprocess.run(command, text=True, capture_output=True, check=False)

    def assert_rejected(self, expected: str, *, source_stage: bool = False) -> None:
        result = self.validate(source_stage=source_stage)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(expected, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def load_json(self, relative: str) -> dict:
        return json.loads((self.plugin / relative).read_text(encoding="utf-8"))

    def write_json(self, relative: str, value: dict) -> None:
        (self.plugin / relative).write_text(
            json.dumps(value, indent=2) + "\n", encoding="utf-8"
        )

    def test_runtime_root_has_required_contract_and_no_development_artifacts(
        self,
    ) -> None:
        files = {
            path.relative_to(self.plugin).as_posix()
            for path in self.plugin.rglob("*")
            if path.is_file()
        }
        self.assertTrue(REQUIRED_RUNTIME_FILES.issubset(files))
        self.assertFalse(
            any(
                part in {"evals", "tests", "fixtures", "release"}
                for relative in files
                for part in Path(relative).parts
            )
        )
        self.assertFalse(
            any(
                relative.endswith((".whl", ".tar", ".tar.gz"))
                or "tomlkit" in relative.lower()
                or "phase7" in relative.lower()
                or "private-evidence" in relative.lower()
                for relative in files
            )
        )

    def test_manifests_expose_exact_three_namespaced_prompts(self) -> None:
        codex = self.load_json(".codex-plugin/plugin.json")
        claude = self.load_json(".claude-plugin/plugin.json")
        self.assertEqual(codex["name"], "artifact-customs")
        self.assertEqual(claude["name"], "artifact-customs")
        prompts = codex["interface"]["defaultPrompt"]
        self.assertEqual(len(prompts), len(SKILLS))
        self.assertEqual(
            {
                token
                for prompt in prompts
                for token in prompt.split()
                if token.startswith("$artifact-customs:")
            },
            {f"$artifact-customs:{skill}" for skill in SKILLS},
        )
        for skill in SKILLS:
            interface = (
                self.plugin / "skills" / skill / "agents" / "openai.yaml"
            ).read_text(encoding="utf-8")
            self.assertIn(f"$artifact-customs:{skill}", interface)

    def test_topology_separates_assessment_adoption_and_maintenance_authority(
        self,
    ) -> None:
        topology = self.load_json("topology.json")
        self.assertEqual(topology["plugin"], "artifact-customs")
        self.assertEqual(set(topology["skills"]), set(SKILLS))
        assessment = topology["skills"]["assessing-third-party-components"]
        self.assertEqual(assessment["authority"], "read-only")
        self.assertFalse(assessment["mutates_directly"])
        self.assertFalse(assessment["can_cause_mutation"])
        self.assertEqual(assessment["calls"], [])
        adoption = topology["skills"]["adopting-third-party-components"]
        maintenance = topology["skills"]["maintaining-third-party-components"]
        self.assertEqual(adoption["authority"], "explicit-new-or-revised-boundary")
        self.assertEqual(maintenance["authority"], "named-existing-policy")
        for mutator in (adoption, maintenance):
            self.assertIn("assessing-third-party-components", mutator["calls"])
            self.assertTrue(mutator["requires_matching_mutation_authority"])
        for skill, expected_calls in EXPECTED_EXTERNAL_CALLS.items():
            self.assertEqual(
                topology["skills"][skill]["external_calls"], expected_calls
            )
        self.assertEqual(set(topology["outward_plugins"]), OUTWARD_PLUGINS)
        self.assertTrue(topology["reverse_edges_forbidden"])
        self.assertEqual(
            topology["phase7_control_projection"],
            ["rolecasting", "versionkeeping", "mergecraft", "tricritical"],
        )
        vocabulary = topology["terminal_status_vocabulary"]
        machine_statuses = {
            status
            for skill in topology["skills"].values()
            for status in skill["terminal_statuses"]
        }
        self.assertEqual(set(vocabulary), machine_statuses)
        for skill_name, skill in topology["skills"].items():
            completion = (self.plugin / "skills" / skill_name / "SKILL.md").read_text(
                encoding="utf-8"
            )
            normalized_completion = " ".join(completion.split())
            for status in skill["terminal_statuses"]:
                self.assertIn(vocabulary[status], normalized_completion)

    def test_invocation_and_scheduler_contract_has_no_preferred_harness(self) -> None:
        envelope = self.load_json("references/invocation-envelope.json")
        adapters = self.load_json("references/scheduler-adapters.json")
        self.assertEqual(
            envelope["manual_invocation"], "permanent-repository-and-pr-number"
        )
        self.assertEqual(envelope["lock_owner"], "artifact-customs")
        self.assertEqual(envelope["receipt_owner"], "artifact-customs")
        self.assertEqual(
            envelope["identity_binding"],
            [
                "mode",
                "requestedLifecycleAction",
                "callerIdentity",
                "autonomyMode",
                "candidateIdentity",
                "policyIdentity",
                "authorityIdentity",
            ],
        )
        self.assertIn("requestedLifecycleAction", envelope["request_required"])
        self.assertIn("autonomyMode", envelope["request_required"])
        self.assertEqual(
            envelope["autonomy_mode_selection"],
            "operator-selected-required-for-scheduled-invocation-and-deployment",
        )
        self.assertEqual(
            envelope["idempotence"],
            "same-bound-invocation-candidate-policy-and-authority-converges-or-rejects",
        )
        self.assertEqual(
            envelope["mode_authority"],
            {
                "assess": {
                    "authority": "read-only",
                    "authority_identity": "read-only-clearance",
                },
                "adopt": {
                    "authority": "explicit-new-or-revised-boundary",
                    "authority_identity": "explicit-new-or-revised-boundary",
                },
                "maintain": {
                    "authority": "named-existing-policy",
                    "authority_identity": "named-existing-policy",
                },
            },
        )
        self.assertNotIn("mutation_authority", envelope)
        self.assertEqual(
            envelope["lifecycle_actions"],
            {
                "assess": ["clearance"],
                "adopt": ["adopt"],
                "maintain": [
                    "update",
                    "advisory",
                    "replace",
                    "seal",
                    "retire",
                    "pull-request",
                ],
            },
        )
        self.assertEqual(
            envelope["pre_write_rebind"],
            {
                "resolves": [
                    "candidateIdentity",
                    "policyIdentity",
                    "authorityIdentity",
                ],
                "before": [
                    "source-write",
                    "policy-write",
                    "retained-evidence-write",
                    "forge-close-or-reject",
                    "forge-publish-approve-or-merge",
                ],
                "on_drift": "invalidate-and-reassess-before-any-mutation",
            },
        )
        self.assertEqual(envelope["preferred_scheduler"], None)
        self.assertEqual(envelope["preferred_cadence"], None)
        self.assertEqual(
            envelope["autonomy_modes"],
            [
                {
                    "id": "report-only",
                    "upgrade": "never-autonomous",
                    "outcome": "research-report-recommend",
                },
                {
                    "id": "high-confidence",
                    "upgrade": "only-after-adequate-verification-security-review-and-high-confidence-safe-judgment",
                    "otherwise": "escalate-with-comprehensive-report",
                },
                {
                    "id": "confidence-forward-deferred",
                    "upgrade": "when-confident",
                    "otherwise": "defer-to-later-research-cycle-for-ecosystem-evidence",
                },
            ],
        )
        self.assertEqual(envelope["recommended_autonomy_mode"], "high-confidence")
        self.assertFalse(envelope["recommendation_is_consent"])
        self.assertEqual(envelope["compromised_artifact"], "fail-closed")
        self.assertEqual(
            set(adapters["native_adapters"]), {"codex-chatgpt", "claude-desktop"}
        )
        self.assertEqual(
            adapters["onboarding"],
            {
                "inspect_first": "existing-or-possible-dependency-or-component-maintenance-schedule-or-analogous-process",
                "found_or_suspected": "offer-best-effort-integration-or-alignment-from-available-evidence",
                "uncertain_suitability": "also-offer-context-sensitive-standalone-cadence",
                "none_found": "recommend-context-sensitive-cadence",
                "operator_selects": ["activation", "cadence", "autonomyMode"],
            },
        )
        self.assertEqual(adapters["foreign_harness"], "common-envelope-only")
        self.assertEqual(adapters["semantic_owner"], "artifact-customs")
        self.assertEqual(adapters["preferred_scheduler"], None)
        self.assertEqual(adapters["preferred_cadence"], None)
        for adapter in adapters["native_adapters"].values():
            self.assertEqual(
                adapter["deployment_state"]["autonomyMode"],
                "operator-selected-required",
            )

    def test_contract_requires_rebind_before_every_write_and_mode_scoped_authority(
        self,
    ) -> None:
        for relative in (
            "references/component-clearance-contract.md",
            "skills/adopting-third-party-components/SKILL.md",
            "skills/maintaining-third-party-components/SKILL.md",
        ):
            content = (self.plugin / relative).read_text(encoding="utf-8")
            with self.subTest(relative=relative):
                self.assertIn("policy identity", content)
                self.assertIn("authority identity", content)
                self.assertIn("retained-evidence", content)
        maintenance = (
            self.plugin / "skills/maintaining-third-party-components/SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "advisory enters maintenance and then calls assessment", maintenance
        )
        self.assertIn("every write", maintenance)

    def test_public_prose_states_onboarding_and_autonomy_limits(self) -> None:
        required_terms = (
            "existing or possibly existing dependency or component maintenance schedule",
            "best-effort integration or alignment",
            "context-sensitive standalone cadence",
            "report-only",
            "high-confidence",
            "confidence-forward/deferred",
            "compromised-artifact risk fail-closed",
            "named existing policy",
        )
        for relative in (
            "README.md",
            "skills/maintaining-third-party-components/SKILL.md",
            "references/component-policy-contract.md",
        ):
            with self.subTest(relative=relative):
                content = (self.plugin / relative).read_text(encoding="utf-8")
                normalized_content = " ".join(content.split())
                for term in required_terms:
                    self.assertIn(term, normalized_content)

    def test_trigger_descriptions_separate_governed_advisories_from_standalone_clearance(
        self,
    ) -> None:
        assessing = (
            self.plugin / "skills/assessing-third-party-components/SKILL.md"
        ).read_text(encoding="utf-8")
        maintaining = (
            self.plugin / "skills/maintaining-third-party-components/SKILL.md"
        ).read_text(encoding="utf-8")
        assessing_description = assessing.split("---", 2)[1]
        maintaining_description = maintaining.split("---", 2)[1]
        self.assertIn(
            "standalone ungoverned read-only clearance", assessing_description
        )
        self.assertNotIn("advisories", assessing_description)
        self.assertIn(
            "advisory for a third-party software component governed by a named existing policy",
            maintaining_description,
        )

    def test_validator_rejects_fourth_public_skill(self) -> None:
        topology = self.load_json("topology.json")
        topology["skills"]["posture-audit"] = dict(
            topology["skills"]["assessing-third-party-components"]
        )
        self.write_json("topology.json", topology)
        self.assert_rejected("skill inventory", source_stage=True)

    def test_validator_rejects_assessment_mutation_or_reverse_call(self) -> None:
        topology = self.load_json("topology.json")
        assessment = topology["skills"]["assessing-third-party-components"]
        assessment["can_cause_mutation"] = True
        assessment["calls"] = ["adopting-third-party-components"]
        self.write_json("topology.json", topology)
        self.assert_rejected("assessment authority", source_stage=True)

    def test_validator_rejects_mutation_without_matching_boundary(self) -> None:
        topology = self.load_json("topology.json")
        topology["skills"]["adopting-third-party-components"]["authority"] = (
            "named-existing-policy"
        )
        topology["skills"]["maintaining-third-party-components"][
            "requires_matching_mutation_authority"
        ] = False
        self.write_json("topology.json", topology)
        self.assert_rejected("mutation authority", source_stage=True)

    def test_validator_rejects_unknown_outward_owner_or_reverse_edge(self) -> None:
        topology = self.load_json("topology.json")
        topology["outward_plugins"].append("github")
        topology["reverse_edges_forbidden"] = False
        self.write_json("topology.json", topology)
        self.assert_rejected("outward composition", source_stage=True)

    def test_validator_rejects_skill_role_or_external_composition_drift(self) -> None:
        topology = self.load_json("topology.json")
        topology["skills"]["assessing-third-party-components"]["role"] = "maintenance"
        topology["skills"]["assessing-third-party-components"]["external_calls"].append(
            "versionkeeping:git-publication"
        )
        self.write_json("topology.json", topology)
        self.assert_rejected("external call", source_stage=True)

    def test_validator_resolves_every_declared_external_call_to_an_installed_skill(
        self,
    ) -> None:
        topology = self.load_json("topology.json")
        topology["skills"]["assessing-third-party-components"]["external_calls"][0] = (
            "rolecasting:not-a-real-skill"
        )
        self.write_json("topology.json", topology)
        self.assert_rejected("external call", source_stage=True)

    def test_validator_rejects_a_symlinked_external_skill_target(self) -> None:
        skill = (
            self.repository
            / "plugins"
            / "rolecasting"
            / "skills"
            / "delegating-cross-agent-work"
        )
        replacement = skill.with_name("delegating-cross-agent-work-real")
        skill.rename(replacement)
        skill.symlink_to(replacement, target_is_directory=True)
        self.assert_rejected("external call", source_stage=True)

    def test_validator_rejects_scheduler_preference_or_semantic_duplication(
        self,
    ) -> None:
        adapters = self.load_json("references/scheduler-adapters.json")
        adapters["preferred_scheduler"] = "codex-chatgpt"
        adapters["native_adapters"]["codex-chatgpt"]["semantic_owner"] = "codex-chatgpt"
        self.write_json("references/scheduler-adapters.json", adapters)
        self.assert_rejected("scheduler adapter", source_stage=True)

    def test_validator_rejects_malformed_adapter_or_invocation_envelope(self) -> None:
        adapters = self.load_json("references/scheduler-adapters.json")
        adapters["native_adapters"]["claude-desktop"] = "trusted-by-name"
        self.write_json("references/scheduler-adapters.json", adapters)
        self.assert_rejected("scheduler adapter", source_stage=True)

        shutil.copy2(
            REPOSITORY
            / "plugins"
            / "artifact-customs"
            / "references"
            / "scheduler-adapters.json",
            self.plugin / "references" / "scheduler-adapters.json",
        )
        envelope = self.load_json("references/invocation-envelope.json")
        envelope["request_required"].remove("callerIdentity")
        envelope["modes"].append("publish")
        self.write_json("references/invocation-envelope.json", envelope)
        self.assert_rejected("invocation envelope", source_stage=True)

    def test_validator_rejects_development_or_private_artifact_in_runtime_root(
        self,
    ) -> None:
        private = self.plugin / "evals" / "tomlkit-private-phase7.json"
        private.parent.mkdir()
        private.write_text("{}\n", encoding="utf-8")
        self.assert_rejected("runtime root", source_stage=True)

    def test_validator_rejects_duplicate_keys_and_portability_leaks(self) -> None:
        topology = self.plugin / "topology.json"
        topology.write_text(
            topology.read_text(encoding="utf-8").replace(
                '"plugin": "artifact-customs"',
                '"plugin": "artifact-customs",\n  "plugin": "shadow"',
                1,
            ),
            encoding="utf-8",
        )
        self.assert_rejected("duplicate key", source_stage=True)
        shutil.copy2(
            REPOSITORY / "plugins" / "artifact-customs" / "topology.json", topology
        )
        readme = self.plugin / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8") + "\n/Users/example/private\n",
            encoding="utf-8",
        )
        self.assert_rejected("portability", source_stage=True)

    def test_validator_rejects_symlinked_plugin_ancestor(self) -> None:
        plugins = self.repository / "plugins"
        real_plugins = self.repository / "real-plugins"
        plugins.rename(real_plugins)
        plugins.symlink_to(real_plugins, target_is_directory=True)
        self.assert_rejected("symlink", source_stage=True)

    def test_validator_rejects_symlinked_repository_argument(self) -> None:
        linked_repository = Path(self.temporary.name) / "linked-repository"
        linked_repository.symlink_to(self.repository, target_is_directory=True)
        result = subprocess.run(
            [
                sys.executable,
                str(self.validator),
                str(linked_repository),
                "--source-stage",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_release_content_lock_is_external_and_exact(self) -> None:
        lock_path = (
            self.repository
            / "release"
            / "plugin-content-locks"
            / "artifact-customs.json"
        )
        self.assertTrue(lock_path.is_file())
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        runtime_files = {
            path.relative_to(self.plugin).as_posix()
            for path in self.plugin.rglob("*")
            if path.is_file()
        }
        self.assertEqual(set(lock["files"]), runtime_files)
        self.assertFalse((self.plugin / "content-lock.json").exists())

    def test_validator_rejects_missing_or_stale_external_content_lock(self) -> None:
        lock_path = (
            self.repository
            / "release"
            / "plugin-content-locks"
            / "artifact-customs.json"
        )
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock["files"].pop(next(iter(lock["files"])))
        lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
        self.assert_rejected("content lock")

    def test_validator_regenerates_the_external_content_lock_deterministically(
        self,
    ) -> None:
        lock_path = (
            self.repository
            / "release"
            / "plugin-content-locks"
            / "artifact-customs.json"
        )
        lock_path.unlink()
        command = [
            sys.executable,
            str(self.validator),
            str(self.repository),
            "--write-content-lock",
        ]
        first = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertEqual(first.returncode, 0, first.stderr)
        first_bytes = lock_path.read_bytes()
        second = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first_bytes, lock_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
