import copy
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPO_ROOT / "scripts" / "validate_tricritical.py"
sys.path.insert(0, str(REPO_ROOT))
import scripts.validate_tricritical as validator_module  # noqa: E402
from scripts.validate_tricritical import (  # noqa: E402
    CLAUDE_OPERATOR_CHOICE_MAPPING,
    CODEX_OPERATOR_CHOICE_MAPPING,
    CORE_SKILLS,
    MUTATOR_SKILL,
    PERSONA_SKILLS,
    SHARED_INPUT_BOUNDARY_LINK,
    SHARED_INVOCATION_BOUNDARY_LINK,
    SHARED_OUTPUT_CONTRACT_LINK,
    STARTER_PROMPT_SKILLS,
)


class ValidateTricriticalTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(
            dir=Path(tempfile.gettempdir()).resolve()
        )
        self.repo = Path(self.temp_dir.name) / "repo"
        self.repo.mkdir()
        self.plugin_root = self.repo / "plugins" / "tricritical"
        shutil.copytree(
            REPO_ROOT / "plugins" / "tricritical",
            self.plugin_root,
            symlinks=True,
        )
        marketplace_root = self.repo / ".claude-plugin"
        marketplace_root.mkdir()
        shutil.copy2(
            REPO_ROOT / ".claude-plugin" / "marketplace.json",
            marketplace_root / "marketplace.json",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_validator(self):
        return subprocess.run(
            [
                "uv",
                "run",
                "--with",
                "PyYAML",
                "python",
                str(VALIDATOR),
                str(self.repo),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

    @staticmethod
    def write_json(path, value):
        path.write_text(json.dumps(value, indent=2) + "\n")

    def test_accepts_current_repository_contract(self):
        result = self.run_validator()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"snapshot_sha256=[0-9a-f]{64}")
        self.assertRegex(result.stdout, r"plugin_sha256=[0-9a-f]{64}")

    def test_reported_snapshot_identity_matches_validated_bytes(self):
        expected = validator_module.validation_input_digest(self.repo, self.plugin_root)

        result = self.run_validator()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"snapshot_sha256={expected}", result.stdout)

    def test_public_skill_inventory_and_personas_are_exact(self):
        self.assertEqual(
            CORE_SKILLS,
            (
                "review",
                "intent",
                "runtime",
                "structure",
                "adjudicate",
                "revise",
                "loop",
            ),
        )
        self.assertEqual(MUTATOR_SKILL, "revise")
        self.assertEqual(STARTER_PROMPT_SKILLS, ("review", "adjudicate", "loop"))
        self.assertEqual(
            PERSONA_SKILLS,
            {
                "oathfinder": "intent",
                "faultwalker": "runtime",
                "knotcutter": "structure",
                "claimweigher": "adjudicate",
                "formwright": "revise",
                "fathomkeeper": "loop",
            },
        )

    def test_rejects_manifest_prompt_drift(self):
        manifest_path = self.plugin_root / ".codex-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["interface"]["defaultPrompt"][0] = (
            "Use $tricritical:review differently."
        )
        self.write_json(manifest_path, manifest)

        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("default prompts", result.stderr)

    def test_rejects_invalid_metadata_for_nonstarter_skill(self):
        metadata_path = (
            self.plugin_root / "skills" / "intent" / "agents" / "openai.yaml"
        )
        metadata_path.write_text(
            metadata_path.read_text().replace("  default_prompt:", "  prompt:")
        )

        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("skill metadata", result.stderr)

    def test_rejects_duplicate_or_multiple_yaml_documents(self):
        metadata_path = (
            self.plugin_root / "skills" / "intent" / "agents" / "openai.yaml"
        )
        original = metadata_path.read_text()
        cases = (
            original + 'interface:\n  default_prompt: "Use $tricritical:intent."\n',
            original + "---\ninterface: {}\n",
        )
        for content in cases:
            with self.subTest(content=content[-30:]):
                metadata_path.write_text(content)
                result = self.run_validator()
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)

    def test_rejects_malformed_yaml_and_wrong_skill_target(self):
        metadata_path = (
            self.plugin_root / "skills" / "intent" / "agents" / "openai.yaml"
        )
        original = metadata_path.read_text()
        for content in (
            "interface: [\n",
            original.replace("$tricritical:intent", "$tricritical:runtime"),
        ):
            with self.subTest(content=content):
                metadata_path.write_text(content)
                result = self.run_validator()
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)

    def test_rejects_unnamespaced_codex_skill_prompt(self):
        metadata_path = (
            self.plugin_root / "skills" / "intent" / "agents" / "openai.yaml"
        )
        metadata_path.write_text(
            metadata_path.read_text().replace("$tricritical:intent", "$intent")
        )

        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("target exactly $tricritical:intent", result.stderr)

    def test_rejects_missing_persona_adapter(self):
        (self.plugin_root / "agents" / "oathfinder.md").unlink()

        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("component inventory", result.stderr)

    def test_rejects_adapter_with_copied_policy_without_marker_headings(self):
        agent_path = self.plugin_root / "agents" / "oathfinder.md"
        agent_path.write_text(agent_path.read_text() + "\nExtra adapter policy.\n")

        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exact minimal one-skill forwarder", result.stderr)

    def test_operator_choice_api_mappings_live_only_in_adapter_surfaces(self):
        codex_adapter = (
            self.plugin_root / "skills" / "loop" / "agents" / "openai.yaml"
        ).read_text()
        codex_manifest = (
            self.plugin_root / ".codex-plugin" / "plugin.json"
        ).read_text()
        claude_adapter = (self.plugin_root / "agents" / "fathomkeeper.md").read_text()

        self.assertEqual(codex_adapter.count(CODEX_OPERATOR_CHOICE_MAPPING), 1)
        self.assertEqual(codex_manifest.count(CODEX_OPERATOR_CHOICE_MAPPING), 0)
        self.assertEqual(claude_adapter.count(CLAUDE_OPERATOR_CHOICE_MAPPING), 1)

    def test_rejects_harness_api_tokens_in_semantic_skills(self):
        skill_path = self.plugin_root / "skills" / "intent" / "SKILL.md"
        original = skill_path.read_text()
        for token in ("request_user_input", "AskUserQuestion"):
            with self.subTest(token=token):
                skill_path.write_text(f"{original}\nUse {token}.\n")
                result = self.run_validator()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("harness-specific APIs", result.stderr)
        skill_path.write_text(original)

    def test_rejects_machine_local_or_credential_content(self):
        skill_path = self.plugin_root / "skills" / "intent" / "SKILL.md"
        original = skill_path.read_text()
        readme_path = self.plugin_root / "README.md"
        original_readme = readme_path.read_text()
        for path, original_content, marker in (
            (skill_path, original, "/Users/ivan/private"),
            (readme_path, original_readme, "Authorization: Bearer secret"),
        ):
            with self.subTest(path=path.name, marker=marker):
                path.write_text(f"{original_content}\n{marker}\n")
                result = self.run_validator()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("portability leak", result.stderr)
                path.write_text(original_content)

    def test_rejects_missing_shared_input_boundary_link_from_public_skill(self):
        skill_path = self.plugin_root / "skills" / "runtime" / "SKILL.md"
        skill_path.write_text(
            skill_path.read_text().replace(SHARED_INPUT_BOUNDARY_LINK, "")
        )

        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("shared review-input boundary", result.stderr)

    def test_content_lock_covers_every_authoritative_semantic_byte(self):
        lock = json.loads((self.plugin_root / "content-lock.json").read_text())
        self.assertEqual(
            set(lock["files"]), set(validator_module.semantic_release_paths())
        )
        self.assertEqual(lock, validator_module.content_lock_document(self.plugin_root))

        path = self.plugin_root / "skills" / "runtime" / "references" / "rubric.md"
        path.write_text(path.read_text() + "\n")
        result = self.run_validator()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("semantic content lock mismatch", result.stderr)

    def test_content_lock_writer_refreshes_authoritative_bytes(self):
        path = self.plugin_root / "skills" / "runtime" / "references" / "rubric.md"
        path.write_text(path.read_text() + "\nUpdated semantic contract.\n")
        result = subprocess.run(
            [
                sys.executable,
                str(VALIDATOR),
                "--write-content-lock",
                str(self.repo),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("semantic content lock updated", result.stdout)

    def test_rejects_missing_undeclared_invocation_boundary(self):
        skill_path = self.plugin_root / "skills" / "adjudicate" / "SKILL.md"
        skill_path.write_text(
            skill_path.read_text().replace(SHARED_INVOCATION_BOUNDARY_LINK, "")
        )

        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("shared invocation boundary", result.stderr)

    def test_rejects_missing_shared_output_contract_link(self):
        skill_path = self.plugin_root / "skills" / "structure" / "SKILL.md"
        skill_path.write_text(
            skill_path.read_text().replace(SHARED_OUTPUT_CONTRACT_LINK, "")
        )

        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("shared review-output contract link", result.stderr)

    def test_rejects_alternate_mutator_declaration(self):
        topology_path = self.plugin_root / "topology.json"
        topology = json.loads(topology_path.read_text())
        topology["skills"]["intent"]["mutates_directly"] = True
        self.write_json(topology_path, topology)

        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("authority topology", result.stderr)

    def test_topology_separates_direct_transitive_and_original_authority(self):
        topology = json.loads((self.plugin_root / "topology.json").read_text())
        self.assertEqual(topology["schema_version"], 2)
        expected = {
            "review": (False, False, False),
            "intent": (False, False, False),
            "runtime": (False, False, False),
            "structure": (False, False, False),
            "adjudicate": (False, False, False),
            "revise": (True, True, True),
            "loop": (False, True, True),
        }
        for skill, values in expected.items():
            node = topology["skills"][skill]
            self.assertEqual(
                (
                    node["mutates_directly"],
                    node["can_cause_mutation"],
                    node["requires_original_mutation_authority"],
                ),
                values,
                skill,
            )
        self.assertIn(
            "loop-no-original-mutation-authority.md",
            validator_module.EVAL_FIXTURES,
        )

    def test_rejects_transitive_mutation_reachability_drift(self):
        topology_path = self.plugin_root / "topology.json"
        topology = json.loads(topology_path.read_text())
        topology["skills"]["loop"]["can_cause_mutation"] = False
        self.write_json(topology_path, topology)

        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mutation reachability", result.stderr)

    def test_rejects_alternate_loop_owner_declaration(self):
        topology_path = self.plugin_root / "topology.json"
        topology = json.loads(topology_path.read_text())
        topology["skills"]["review"]["repeats"] = True
        self.write_json(topology_path, topology)

        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("authority topology", result.stderr)

    def test_rejects_nested_calls_from_leaf_roles(self):
        topology_path = self.plugin_root / "topology.json"
        original = json.loads(topology_path.read_text())
        for skill in ("intent", "runtime", "structure", "adjudicate", "revise"):
            with self.subTest(skill=skill):
                topology = copy.deepcopy(original)
                topology["skills"][skill]["calls"] = ["review"]
                self.write_json(topology_path, topology)

                result = self.run_validator()

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("authority topology", result.stderr)

    def test_rejects_unapproved_loop_edges(self):
        topology_path = self.plugin_root / "topology.json"
        topology = json.loads(topology_path.read_text())
        topology["skills"]["loop"]["calls"] = ["review", "revise"]
        self.write_json(topology_path, topology)

        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("authority topology", result.stderr)

    def test_rejects_missing_model_selection_requirement(self):
        topology_path = self.plugin_root / "topology.json"
        topology = json.loads(topology_path.read_text())
        topology["skills"]["review"]["requires"] = []
        self.write_json(topology_path, topology)

        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("adapter requirements", result.stderr)

    def test_rejects_model_selection_requirement_on_leaf(self):
        topology_path = self.plugin_root / "topology.json"
        topology = json.loads(topology_path.read_text())
        topology["skills"]["intent"]["requires"] = ["adapter:model-selection-receipt"]
        self.write_json(topology_path, topology)

        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("adapter requirements", result.stderr)

    def test_review_requires_separate_invocation_topology_receipt(self):
        topology = json.loads((self.plugin_root / "topology.json").read_text())
        rolecasting_contract = json.loads(
            (REPO_ROOT / "plugins/rolecasting/topology.json").read_text()
        )["receipt_contract"]
        self.assertEqual(
            topology["skills"]["review"]["requires"],
            [
                rolecasting_contract["separate_from"],
                rolecasting_contract["id"],
            ],
        )
        self.assertEqual(
            rolecasting_contract["default_denies"],
            ["subdelegation", "external-action"],
        )
        for skill, node in topology["skills"].items():
            if skill != "review":
                self.assertEqual(node["requires"], [], skill)

        review = (self.plugin_root / "skills/review/SKILL.md").read_text()
        boundary = (self.plugin_root / "references/invocation-boundary.md").read_text()
        for required in (
            "adapter:rolecasting-invocation-topology-receipt",
            "closed-world dispatch set",
            "exactly one unique dispatch entry",
            "new valid plan",
            "incomplete / non-clean",
        ):
            self.assertIn(required, review + boundary)

        for fixture in (
            "topology-unauthorized-user-owned-task.md",
            "topology-inadequate-foreign-isolation.md",
            "topology-prohibited-subdelegation-external-action.md",
            "topology-valid-native-dispatch.md",
        ):
            self.assertIn(fixture, validator_module.EVAL_FIXTURES)

    def test_rejects_review_adapter_without_model_selection_receipt(self):
        path = self.plugin_root / "skills/review/agents/openai.yaml"
        receipt_prompt = (
            " with separate capability-proven model-selection and Rolecasting "
            "invocation-topology receipts"
        )
        path.write_text(
            path.read_text().replace(
                receipt_prompt,
                " with a Rolecasting invocation-topology receipt",
            )
        )
        manifest_path = self.plugin_root / ".codex-plugin/plugin.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["interface"]["defaultPrompt"][0] = manifest["interface"][
            "defaultPrompt"
        ][0].replace(
            receipt_prompt,
            " with a Rolecasting invocation-topology receipt",
        )
        self.write_json(manifest_path, manifest)

        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("review adapter", result.stderr)

    def test_rejects_executable_call_edge_drift(self):
        review_path = self.plugin_root / "skills" / "review" / "SKILL.md"
        review_path.write_text(
            review_path.read_text().replace("[intent](../intent/SKILL.md)", "intent", 1)
        )
        result = self.run_validator()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("executable call edges", result.stderr)

    def test_rejects_readme_projection_drift(self):
        readme_path = self.plugin_root / "README.md"
        original = readme_path.read_text()
        review_row = next(
            line
            for line in original.splitlines()
            if line.lstrip().startswith("| `review`")
        )
        mutated_row = review_row.replace("intent, runtime, structure", "intent")
        self.assertNotEqual(review_row, mutated_row)
        mutated = original.replace(review_row, mutated_row, 1)
        self.assertNotEqual(original, mutated)
        readme_path.write_text(mutated)

        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("README topology table differs", result.stderr)

    def test_rejects_duplicate_readme_projection_row(self):
        readme_path = self.plugin_root / "README.md"
        review_row = next(
            line for line in readme_path.read_text().splitlines() if "`review`" in line
        )
        readme_path.write_text(readme_path.read_text() + f"\n{review_row}\n")

        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("README topology table differs", result.stderr)

    def test_rejects_codex_namespaced_syntax_in_semantic_core(self):
        review_path = self.plugin_root / "skills" / "review" / "SKILL.md"
        review_path.write_text(
            review_path.read_text() + "\nInvoke `$tricritical:intent`.\n"
        )
        result = self.run_validator()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("semantic skill files", result.stderr)

    def test_rejects_second_executable_mutator(self):
        intent_path = self.plugin_root / "skills" / "intent" / "SKILL.md"
        intent_path.write_text(
            intent_path.read_text() + "\n## Mutation Authority\n\nEdit the candidate.\n"
        )
        result = self.run_validator()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("topology mutator", result.stderr)

    def test_rejects_relaxed_eval_runner_contract(self):
        corpus_path = self.plugin_root / "evals" / "corpus.json"
        original = json.loads(corpus_path.read_text())
        cases = (
            ("shell", "allowed"),
            ("browser", "allowed"),
            ("network", "allowed"),
            ("filesystem_outside_bundle", "allowed"),
        )
        for field, value in cases:
            with self.subTest(field=field):
                corpus = copy.deepcopy(original)
                corpus["execution_isolation"]["runner_enforcement"][field] = value
                self.write_json(corpus_path, corpus)

                result = self.run_validator()

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("executor and grader isolation", result.stderr)

    def test_rejects_unrecorded_eval_runner_enforcement_contract(self):
        corpus_path = self.plugin_root / "evals" / "corpus.json"
        corpus = json.loads(corpus_path.read_text())
        corpus["execution_isolation"]["clean_evidence_requires"] = []
        self.write_json(corpus_path, corpus)

        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("executor and grader isolation", result.stderr)

    def test_candidate_skill_bundle_is_minimal_and_transitive(self):
        intent_bundle = validator_module.resolve_candidate_skill_bundle(
            self.plugin_root, "intent"
        )
        self.assertEqual(
            intent_bundle,
            (
                "references/invocation-boundary.md",
                "references/review-input-boundary.md",
                "references/review-output-contract.md",
                "skills/intent/SKILL.md",
                "skills/intent/references/rubric.md",
                "topology.json",
            ),
        )

        review_bundle = validator_module.resolve_candidate_skill_bundle(
            self.plugin_root, "review"
        )
        self.assertEqual(
            review_bundle,
            (
                "references/invocation-boundary.md",
                "references/review-input-boundary.md",
                "references/review-output-contract.md",
                "skills/intent/SKILL.md",
                "skills/intent/references/rubric.md",
                "skills/review/SKILL.md",
                "skills/review/references/completeness-and-synthesis.md",
                "skills/runtime/SKILL.md",
                "skills/runtime/references/rubric.md",
                "skills/structure/SKILL.md",
                "skills/structure/references/rubric.md",
                "topology.json",
            ),
        )
        self.assertNotIn("README.md", review_bundle)
        self.assertNotIn("evals/corpus.json", review_bundle)

        for skill in CORE_SKILLS:
            with self.subTest(skill=skill):
                bundle = validator_module.resolve_candidate_skill_bundle(
                    self.plugin_root, skill
                )
                self.assertIn("references/invocation-boundary.md", bundle)
                self.assertIn("topology.json", bundle)

    def test_candidate_skill_bundle_rejects_reference_escape(self):
        intent_path = self.plugin_root / "skills" / "intent" / "SKILL.md"
        intent_path.write_text(
            intent_path.read_text() + "\n[escape](../../../README.md)\n"
        )

        with self.assertRaisesRegex(ValueError, "bundle escapes plugin root"):
            validator_module.resolve_candidate_skill_bundle(self.plugin_root, "intent")

    def test_candidate_skill_bundle_rejects_external_reference(self):
        intent_path = self.plugin_root / "skills" / "intent" / "SKILL.md"
        intent_path.write_text(
            intent_path.read_text() + "\n[external](https://example.invalid/policy)\n"
        )

        with self.assertRaisesRegex(ValueError, "external reference"):
            validator_module.resolve_candidate_skill_bundle(self.plugin_root, "intent")

    def test_candidate_skill_bundle_accounts_for_images_and_html_resources(self):
        intent_path = self.plugin_root / "skills" / "intent" / "SKILL.md"
        asset_path = (
            self.plugin_root / "skills" / "intent" / "references" / "diagram.png"
        )
        asset_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        original = intent_path.read_text()
        cases = (
            "![diagram](references/diagram.png)",
            "![diagram][asset]\n\n[asset]: references/diagram.png",
            '<img src="references/diagram.png" alt="diagram">',
            '<source srcset="references/diagram.png 1x">',
        )
        for markup in cases:
            with self.subTest(markup=markup):
                intent_path.write_text(f"{original}\n{markup}\n")
                bundle = validator_module.resolve_candidate_skill_bundle(
                    self.plugin_root, "intent"
                )
                self.assertIn("skills/intent/references/diagram.png", bundle)

    def test_candidate_skill_bundle_rejects_external_autolinks_and_html(self):
        intent_path = self.plugin_root / "skills" / "intent" / "SKILL.md"
        original = intent_path.read_text()
        cases = (
            "<https://example.invalid/policy>",
            '<a href="https://example.invalid/policy">policy</a>',
            '<img src="/rooted.png" alt="rooted">',
            '<img src="%2e%2e/%2e%2e/%2e%2e/rooted.png" alt="escaped">',
        )
        for markup in cases:
            with self.subTest(markup=markup):
                intent_path.write_text(f"{original}\n{markup}\n")
                with self.assertRaisesRegex(
                    ValueError, "external reference|escapes plugin root"
                ):
                    validator_module.resolve_candidate_skill_bundle(
                        self.plugin_root, "intent"
                    )

    def test_candidate_skill_bundle_rejects_unsupported_html_resource_attrs(self):
        intent_path = self.plugin_root / "skills" / "intent" / "SKILL.md"
        original = intent_path.read_text()
        for markup in (
            '<div style="background: url(references/diagram.png)"></div>',
            '<iframe srcdoc="<img src=references/diagram.png>"></iframe>',
        ):
            with self.subTest(markup=markup):
                intent_path.write_text(f"{original}\n{markup}\n")
                with self.assertRaisesRegex(ValueError, "unsupported HTML resource"):
                    validator_module.resolve_candidate_skill_bundle(
                        self.plugin_root, "intent"
                    )

    def test_candidate_skill_bundle_resolves_reference_style_links(self):
        intent_path = self.plugin_root / "skills" / "intent" / "SKILL.md"
        reference_path = (
            self.plugin_root / "skills" / "intent" / "references" / "reference-style.md"
        )
        reference_path.write_text("# Reference-style closure\n")
        intent_path.write_text(
            intent_path.read_text()
            + "\nRead [the closure supplement][closure].\n\n"
            + '[closure]: <references/reference-style.md> "Closure policy"\n'
        )

        bundle = validator_module.resolve_candidate_skill_bundle(
            self.plugin_root, "intent"
        )

        self.assertIn("skills/intent/references/reference-style.md", bundle)

    def test_candidate_skill_bundle_rejects_external_reference_definition(self):
        intent_path = self.plugin_root / "skills" / "intent" / "SKILL.md"
        intent_path.write_text(
            intent_path.read_text()
            + "\nRead [the external supplement][external].\n\n"
            + "[external]: https://example.invalid/policy\n"
        )

        with self.assertRaisesRegex(ValueError, "external reference"):
            validator_module.resolve_candidate_skill_bundle(self.plugin_root, "intent")

    def test_candidate_skill_bundle_rejects_unsupported_reference_definition(self):
        intent_path = self.plugin_root / "skills" / "intent" / "SKILL.md"
        intent_path.write_text(
            intent_path.read_text()
            + "\nRead [the closure supplement][closure].\n\n"
            + "[closure]:\n  references/rubric.md\n"
        )

        with self.assertRaisesRegex(ValueError, "unsupported reference definition"):
            validator_module.resolve_candidate_skill_bundle(self.plugin_root, "intent")

    def test_candidate_skill_bundle_rejects_unresolved_reference_style_link(self):
        intent_path = self.plugin_root / "skills" / "intent" / "SKILL.md"
        intent_path.write_text(
            intent_path.read_text() + "\nRead [the missing supplement][missing].\n"
        )

        with self.assertRaisesRegex(ValueError, "unresolved Markdown reference"):
            validator_module.resolve_candidate_skill_bundle(self.plugin_root, "intent")

    def test_candidate_skill_bundle_digest_covers_transitive_references(self):
        before = validator_module.candidate_skill_bundle_digest(
            self.plugin_root, "intent"
        )
        self.assertEqual(
            before,
            validator_module.candidate_skill_bundle_digest(self.plugin_root, "intent"),
        )

        rubric_path = (
            self.plugin_root / "skills" / "intent" / "references" / "rubric.md"
        )
        rubric_path.write_text(rubric_path.read_text() + "\nDigest boundary.\n")

        self.assertNotEqual(
            before,
            validator_module.candidate_skill_bundle_digest(self.plugin_root, "intent"),
        )

    def test_tree_digest_is_deterministic_and_content_sensitive(self):
        before = validator_module.deterministic_tree_digest(self.plugin_root)
        self.assertEqual(
            before, validator_module.deterministic_tree_digest(self.plugin_root)
        )

        readme_path = self.plugin_root / "README.md"
        readme_path.write_text(readme_path.read_text() + "\nDigest boundary.\n")

        self.assertNotEqual(
            before, validator_module.deterministic_tree_digest(self.plugin_root)
        )

    def test_rejects_marketplace_entry_drift(self):
        marketplace_path = self.repo / ".claude-plugin" / "marketplace.json"
        original = json.loads(marketplace_path.read_text())
        cases = {
            "name": "tricritical-copy",
            "source": "../tricritical",
            "category": "productivity",
        }
        for field, value in cases.items():
            with self.subTest(field=field):
                marketplace = copy.deepcopy(original)
                entry = next(
                    item
                    for item in marketplace["plugins"]
                    if item["name"] == "tricritical"
                )
                entry[field] = value
                self.write_json(marketplace_path, marketplace)

                result = self.run_validator()

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Claude marketplace", result.stderr)

    def test_manifest_type_errors_are_reported_without_tracebacks(self):
        codex_path = self.plugin_root / ".codex-plugin" / "plugin.json"
        codex = json.loads(codex_path.read_text())
        codex["interface"]["defaultPrompt"] = "not-a-list"
        self.write_json(codex_path, codex)
        result = self.run_validator()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be a list of strings", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_rejects_duplicate_keys_in_every_json_contract(self):
        cases = (
            (
                self.plugin_root / ".codex-plugin" / "plugin.json",
                '  "name": "tricritical",',
                '  "name": "tricritical",\n  "name": "duplicate",',
            ),
            (
                self.plugin_root / ".claude-plugin" / "plugin.json",
                '  "name": "tricritical",',
                '  "name": "tricritical",\n  "name": "duplicate",',
            ),
            (
                self.repo / ".claude-plugin" / "marketplace.json",
                '  "name": "nisavid-agents",',
                '  "name": "nisavid-agents",\n  "name": "duplicate",',
            ),
            (
                self.plugin_root / "topology.json",
                '  "schema_version": 2,',
                '  "schema_version": 2,\n  "schema_version": 3,',
            ),
            (
                self.plugin_root / "evals" / "corpus.json",
                '  "execution_isolation": {',
                '  "execution_isolation": {},\n  "execution_isolation": {',
            ),
            (
                self.plugin_root / "content-lock.json",
                '  "schema_version": 1,',
                '  "schema_version": 1,\n  "schema_version": 2,',
            ),
        )
        for path, marker, replacement in cases:
            with self.subTest(path=path):
                original = path.read_text()
                path.write_text(original.replace(marker, replacement, 1))
                result = self.run_validator()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("duplicate key", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                path.write_text(original)

    def test_rejects_non_finite_values_in_every_json_contract(self):
        paths = (
            self.plugin_root / ".codex-plugin" / "plugin.json",
            self.plugin_root / ".claude-plugin" / "plugin.json",
            self.repo / ".claude-plugin" / "marketplace.json",
            self.plugin_root / "topology.json",
            self.plugin_root / "evals" / "corpus.json",
            self.plugin_root / "content-lock.json",
        )
        for path in paths:
            original = path.read_text()
            for constant in ("NaN", "Infinity", "-Infinity"):
                with self.subTest(path=path, constant=constant):
                    path.write_text(original.replace("{", f'{{"probe": {constant},', 1))
                    result = self.run_validator()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("non-finite JSON value", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    path.write_text(original)

    def test_missing_descriptor_safety_capabilities_fail_closed(self):
        for flag, factory in (
            ("O_NOFOLLOW", validator_module.directory_open_flags),
            ("O_NONBLOCK", validator_module.regular_file_open_flags),
        ):
            with self.subTest(flag=flag):
                with (
                    mock.patch.object(validator_module.os, flag, None),
                    self.assertRaisesRegex(
                        ValueError, f"required descriptor safety capability: {flag}"
                    ),
                ):
                    factory()

    def test_unrelated_marketplace_entries_are_not_type_policed(self):
        marketplace_path = self.repo / ".claude-plugin" / "marketplace.json"
        marketplace = json.loads(marketplace_path.read_text())
        unrelated = next(
            item for item in marketplace["plugins"] if item["name"] == "rolecasting"
        )
        unrelated["source"] = {"path": "./plugins/rolecasting"}
        self.write_json(marketplace_path, marketplace)
        result = self.run_validator()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_marketplace_container_errors_are_reported_without_tracebacks(self):
        marketplace_path = self.repo / ".claude-plugin" / "marketplace.json"
        marketplace = json.loads(marketplace_path.read_text())
        marketplace["plugins"] = {}
        self.write_json(marketplace_path, marketplace)
        result = self.run_validator()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("plugins must be a list", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_rejects_unexpected_root_and_nested_entries(self):
        for path in (
            self.plugin_root / "unexpected.txt",
            self.plugin_root / "skills" / "intent" / "references" / "extra.md",
        ):
            with self.subTest(path=path):
                path.write_text("unexpected\n")
                result = self.run_validator()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("component inventory", result.stderr)
                path.unlink()

    def test_rejects_special_plugin_entries(self):
        special_path = self.plugin_root / "unexpected-pipe"
        try:
            os.mkfifo(special_path)
        except OSError as error:
            self.skipTest(f"special-file creation is unavailable: {error}")
        result = self.run_validator()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("special entry", result.stderr)

    def test_rejects_symlinked_repository_root(self):
        real_repo = Path(self.temp_dir.name) / "repo-real"
        self.repo.rename(real_repo)
        try:
            self.repo.symlink_to(real_repo, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlink creation is unavailable on this platform: {error}")

        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("repository path", result.stderr)
        self.assertIn("symlink", result.stderr)

    def test_rejects_symlink_in_lexical_ancestor_of_repository_root(self):
        real_parent = Path(self.temp_dir.name) / "real-parent"
        real_parent.mkdir()
        real_repo = real_parent / "repo"
        self.repo.rename(real_repo)
        linked_parent = Path(self.temp_dir.name) / "linked-parent"
        try:
            linked_parent.symlink_to(real_parent, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlink creation is unavailable on this platform: {error}")
        self.repo = linked_parent / "repo"

        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink ancestor", result.stderr)

    def test_parent_path_substitution_cannot_redirect_snapshot_reads(self):
        original_copy = validator_module.create_private_validation_snapshot_from_anchor
        original_content_lock_validation = validator_module.validate_content_lock
        original_loop_content = (
            self.plugin_root / "skills" / "loop" / "SKILL.md"
        ).read_text()
        substituted_loop_content = original_loop_content + "\nSubstituted bytes.\n"
        displaced_repo = Path(self.temp_dir.name) / "repo-displaced"
        observed_snapshot_content = []

        def substitute_parent_then_copy(anchor, snapshot_repository_path):
            self.repo.rename(displaced_repo)
            shutil.copytree(displaced_repo, self.repo)
            (self.plugin_root / "skills" / "loop" / "SKILL.md").write_text(
                substituted_loop_content
            )
            original_copy(anchor, snapshot_repository_path)

        def inspect_bound_snapshot(snapshot_root):
            observed_snapshot_content.append(
                (snapshot_root / "skills" / "loop" / "SKILL.md").read_text()
            )
            original_content_lock_validation(snapshot_root)

        with (
            mock.patch.object(
                validator_module,
                "create_private_validation_snapshot_from_anchor",
                side_effect=substitute_parent_then_copy,
            ),
            mock.patch.object(
                validator_module,
                "validate_content_lock",
                side_effect=inspect_bound_snapshot,
            ),
            self.assertRaisesRegex(ValueError, "repository path binding changed"),
        ):
            validator_module.validate(self.repo)

        self.assertEqual(observed_snapshot_content, [original_loop_content])
        self.assertNotEqual(observed_snapshot_content, [substituted_loop_content])

    def test_blocks_when_validation_tree_drifts(self):
        original = validator_module.validate_eval_corpus

        def mutate_after_eval(root):
            original(root)
            readme_path = self.plugin_root / "README.md"
            readme_path.write_text(readme_path.read_text() + "\nconcurrent drift\n")

        stderr = io.StringIO()
        with (
            mock.patch.object(
                validator_module, "validate_eval_corpus", side_effect=mutate_after_eval
            ),
            mock.patch.object(validator_module, "validate_prompt_pairing"),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit),
        ):
            validator_module.validate(self.repo)

        self.assertIn("concurrent writers are forbidden", stderr.getvalue())

    def test_blocks_content_preserving_live_aba_drift(self):
        original_validator = validator_module.validate_eval_corpus
        readme_path = self.plugin_root / "README.md"
        original_content = readme_path.read_text()
        original_digest = validator_module.deterministic_tree_digest(self.plugin_root)
        original_validation_identity = validator_module.validation_input_digest(
            self.repo, self.plugin_root
        )

        def mutate_and_restore_after_eval(root):
            original_validator(root)
            readme_path.write_text(original_content + "\ntransient invalid state\n")
            replacement_path = readme_path.with_name("README.replacement.md")
            replacement_path.write_text(original_content)
            replacement_path.replace(readme_path)

        stderr = io.StringIO()
        with (
            mock.patch.object(
                validator_module,
                "validate_eval_corpus",
                side_effect=mutate_and_restore_after_eval,
            ),
            mock.patch.object(validator_module, "validate_prompt_pairing"),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit),
        ):
            validator_module.validate(self.repo)

        self.assertEqual(
            original_digest,
            validator_module.deterministic_tree_digest(self.plugin_root),
        )
        self.assertEqual(
            original_validation_identity,
            validator_module.validation_input_digest(self.repo, self.plugin_root),
        )
        self.assertIn("concurrent writers are forbidden", stderr.getvalue())

    def test_invalid_valid_invalid_aba_cannot_change_validated_snapshot(self):
        loop_path = self.plugin_root / "skills" / "loop" / "SKILL.md"
        valid_content = loop_path.read_text()
        invalid_content = valid_content + "\nInvalid transient bytes.\n"
        loop_path.write_text(invalid_content)
        invalid_digest = validator_module.deterministic_tree_digest(self.plugin_root)
        original_validator = validator_module.validate_content_lock

        def transiently_validate_snapshot(snapshot_root):
            snapshot_loop_path = snapshot_root / "skills" / "loop" / "SKILL.md"
            self.assertEqual(snapshot_loop_path.read_text(), invalid_content)
            snapshot_loop_path.write_text(valid_content)
            try:
                original_validator(snapshot_root)
            finally:
                snapshot_loop_path.write_text(invalid_content)

        stderr = io.StringIO()
        with (
            mock.patch.object(
                validator_module,
                "validate_content_lock",
                side_effect=transiently_validate_snapshot,
            ),
            mock.patch.object(validator_module, "validate_prompt_pairing"),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit),
        ):
            validator_module.validate(self.repo)

        self.assertEqual(
            invalid_digest,
            validator_module.deterministic_tree_digest(self.plugin_root),
        )
        self.assertIn("private validation snapshot changed", stderr.getvalue())

    def test_rejects_symlinked_plugin_root_parent(self):
        plugins_root = self.repo / "plugins"
        external_plugins_root = Path(self.temp_dir.name) / "external-plugins"
        plugins_root.rename(external_plugins_root)
        try:
            plugins_root.symlink_to(external_plugins_root, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlink creation is unavailable on this platform: {error}")

        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("plugin root", result.stderr)
        self.assertIn("symlinks", result.stderr)

    def test_rejects_nested_symlinked_plugin_directory(self):
        runtime_root = self.plugin_root / "skills" / "runtime"
        external_runtime_root = Path(self.temp_dir.name) / "runtime-real"
        runtime_root.rename(external_runtime_root)
        try:
            runtime_root.symlink_to(external_runtime_root, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlink creation is unavailable on this platform: {error}")

        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlinks", result.stderr)

    def test_rejects_symlinked_marketplace_parent(self):
        marketplace_root = self.repo / ".claude-plugin"
        external_marketplace_root = Path(self.temp_dir.name) / "claude-plugin-real"
        marketplace_root.rename(external_marketplace_root)
        try:
            marketplace_root.symlink_to(
                external_marketplace_root, target_is_directory=True
            )
        except OSError as error:
            self.skipTest(f"symlink creation is unavailable on this platform: {error}")

        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlinks", result.stderr)


if __name__ == "__main__":
    unittest.main()
