from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPO_ROOT / "scripts" / "validate_rolecasting.py"


class ValidateRolecastingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.tempdir.name).resolve()
        self.repo = self.temp_root / "repo"
        self.plugin = self.repo / "plugins" / "rolecasting"
        shutil.copytree(REPO_ROOT / "plugins" / "rolecasting", self.plugin)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def validate(self, repo: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VALIDATOR), str(repo or self.repo)],
            text=True,
            capture_output=True,
            check=False,
        )

    def validate_without_argument(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VALIDATOR)],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_rejected(self, expected: str, repo: Path | None = None) -> None:
        result = self.validate(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(expected, result.stderr)

    def test_accepts_current_contract(self) -> None:
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_accepts_zero_arguments_from_repository_root(self) -> None:
        result = self.validate_without_argument()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_accepts_ordinary_physical_path_with_link_in_name(self) -> None:
        physical_repo = self.temp_root / "ordinary-link-name" / "repo"
        shutil.copytree(self.plugin, physical_repo / "plugins" / "rolecasting")
        result = self.validate(physical_repo)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_more_than_one_repo_root_argument(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(VALIDATOR),
                str(self.temp_root / "missing-repo"),
                "unexpected-second-argument",
            ],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn(
            "usage: validate_rolecasting.py [--write-content-lock] [repo-root]",
            result.stderr,
        )
        self.assertNotIn("Rolecasting contract validation passed", result.stdout)

    def test_rejects_invalid_skill_interface_yaml(self) -> None:
        path = (
            self.plugin / "skills" / "choosing-agent-models" / "agents" / "openai.yaml"
        )
        path.write_text("interface: [\n" + path.read_text())
        result = self.validate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("contains invalid YAML", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_rejects_duplicate_skill_interface_yaml_key(self) -> None:
        path = (
            self.plugin / "skills" / "choosing-agent-models" / "agents" / "openai.yaml"
        )
        path.write_text(
            path.read_text().replace(
                '  display_name: "Choose Agent Models"',
                '  display_name: "Choose Agent Models"\n  display_name: "Shadow"',
            )
        )
        self.assert_rejected("contains duplicate key: display_name")

    def test_rejects_missing_skill_frontmatter_opener(self) -> None:
        path = self.plugin / "skills" / "choosing-agent-models" / "SKILL.md"
        path.write_text(path.read_text().replace("---", "--", 1))
        self.assert_rejected("must have opening and closing frontmatter")

    def test_rejects_missing_skill_frontmatter_closer(self) -> None:
        path = self.plugin / "skills" / "choosing-agent-models" / "SKILL.md"
        path.write_text(
            path.read_text().replace("\n---\n\n# Choosing", "\n--\n\n# Choosing", 1)
        )
        self.assert_rejected("must have opening and closing frontmatter")

    def test_rejects_duplicate_skill_frontmatter_key(self) -> None:
        path = self.plugin / "skills" / "choosing-agent-models" / "SKILL.md"
        path.write_text(
            path.read_text().replace(
                "name: choosing-agent-models",
                "name: choosing-agent-models\nname: shadow",
                1,
            )
        )
        self.assert_rejected("contains duplicate key: name")

    def test_rejects_extra_skill_frontmatter_field(self) -> None:
        path = self.plugin / "skills" / "choosing-agent-models" / "SKILL.md"
        path.write_text(
            path.read_text().replace(
                "name: choosing-agent-models",
                "name: choosing-agent-models\nlicense: MIT",
                1,
            )
        )
        self.assert_rejected("frontmatter schema drift")

    def test_rejects_duplicate_key_in_trusted_manifest(self) -> None:
        path = self.plugin / ".codex-plugin" / "plugin.json"
        path.write_text(
            path.read_text().replace(
                '  "name": "rolecasting",',
                '  "name": "rolecasting",\n  "name": "shadow",',
                1,
            )
        )
        self.assert_rejected("contains duplicate key: name")

    def test_rejects_non_finite_json_values(self) -> None:
        path = self.plugin / "topology.json"
        original = path.read_text()
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                path.write_text(
                    original.replace(
                        '"schema_version": 2', f'"schema_version": {constant}'
                    )
                )
                self.assert_rejected(f"contains non-finite JSON value: {constant}")
                path.write_text(original)

    def test_rejects_wrong_json_schema_types(self) -> None:
        topology = self.plugin / "topology.json"
        topology.write_text(
            topology.read_text().replace(
                '"schema_version": 2', '"schema_version": true'
            )
        )
        self.assert_rejected("topology schema_version must be an integer")

    def test_rejects_eval_delivery_schema_drift(self) -> None:
        path = self.plugin / "evals" / "delivery.json"
        document = json.loads(path.read_text())
        document["executor"]["inputs"].append("expected_output")
        path.write_text(json.dumps(document, indent=2) + "\n")
        self.assert_rejected("executor inputs must withhold grader expectations")

    def test_rejects_unnamespaced_codex_prompt(self) -> None:
        path = self.plugin / ".codex-plugin" / "plugin.json"
        manifest = json.loads(path.read_text())
        manifest["interface"]["defaultPrompt"][0] = (
            "Use $delegating-cross-agent-work for this task."
        )
        path.write_text(json.dumps(manifest, indent=2) + "\n")
        self.assert_rejected("Codex default prompts")

    def test_rejects_plugin_ancestor_symlink(self) -> None:
        plugins = self.repo / "plugins"
        real_plugins = self.repo / "real-plugins"
        plugins.rename(real_plugins)
        plugins.symlink_to(real_plugins, target_is_directory=True)
        self.assert_rejected("plugin root path must not contain symlinks")

    def test_rejects_repo_lexical_ancestor_symlink(self) -> None:
        real_parent = self.temp_root / "real-parent"
        linked_repo = real_parent / "repo"
        shutil.copytree(self.plugin, linked_repo / "plugins" / "rolecasting")
        link = self.temp_root / "link"
        link.symlink_to(real_parent, target_is_directory=True)
        self.assert_rejected(
            "repository path contains a symlinked lexical ancestor",
            link / "repo",
        )

    def test_rejects_portability_leak(self) -> None:
        path = self.plugin / "README.md"
        path.write_text(path.read_text() + "\nInstall from /Users/example/private.\n")
        self.assert_rejected("portability or credential leak")

    def test_rejects_credential_markers_across_case_and_spacing(self) -> None:
        path = self.plugin / "README.md"
        original = path.read_text()
        markers = (
            "Authorization : Bearer secret",
            "aUtHoRiZaTiOn\t:\tBeArEr secret",
            "API KEY = secret",
            "api_key: secret",
        )
        for marker in markers:
            with self.subTest(marker=marker):
                path.write_text(original + f"\n{marker}\n")
                self.assert_rejected("portability or credential leak")
                path.write_text(original)

    def test_rejects_credential_markers_after_json_escape_decoding(self) -> None:
        path = self.plugin / "content-lock.json"
        path.write_text(path.read_text().replace("{", '{"probe": "api\\u005fkey",', 1))
        self.assertNotIn("api_key", path.read_text())
        self.assert_rejected("portability or credential leak")

    def test_rejects_candidate_two_model_semantic_loss(self) -> None:
        path = self.plugin / "skills" / "choosing-agent-models" / "SKILL.md"
        original = path.read_text()
        mutated = original.replace(
            "preferred bounded GPT-5.6 role snapshot as of 2026-07-20",
            "current model role family",
        )
        self.assertNotEqual(mutated, original)
        path.write_text(mutated)
        self.assert_rejected("semantic content lock mismatch")

    def test_rejects_non_codex_catalog_scope_regression(self) -> None:
        path = self.plugin / "skills" / "choosing-agent-models" / "SKILL.md"
        original = path.read_text()
        mutated = original.replace(
            "otherwise probe the target harness",
            "otherwise reuse the Codex live model catalog",
        )
        self.assertNotEqual(mutated, original)
        path.write_text(mutated)
        self.assert_rejected("semantic content lock mismatch")

    def test_rejects_missing_proof_invocation_authority_boundary(self) -> None:
        path = self.plugin / "skills" / "choosing-agent-models" / "SKILL.md"
        path.write_text(
            path.read_text().replace(
                "Model selection does not authorize a proof invocation",
                "Model selection may authorize a proof invocation",
            )
        )
        self.assert_rejected("semantic content lock mismatch")

    def test_rejects_external_invocation_authority_grant(self) -> None:
        path = self.plugin / "skills" / "choosing-agent-models" / "SKILL.md"
        original = path.read_text()
        mutated = original.replace(
            "or treat selection as\ninvocation authority",
            "and treat selection as invocation authority",
        )
        self.assertNotEqual(mutated, original)
        path.write_text(mutated)
        self.assert_rejected("semantic content lock mismatch")

    def test_rejects_detailed_fable_record_fields_in_hot_skill(self) -> None:
        path = self.plugin / "skills" / "choosing-agent-models" / "SKILL.md"
        path.write_text(path.read_text() + "\nRequire harness and version here.\n")
        self.assert_rejected("semantic content lock mismatch")

    def test_rejects_contradictory_semantic_append(self) -> None:
        path = (
            self.plugin
            / "skills"
            / "choosing-agent-models"
            / "references"
            / "capability-probes-and-fallbacks.md"
        )
        path.write_text(
            path.read_text()
            + "\nModel selection grants authority to dispatch every advertised model.\n"
        )
        self.assert_rejected("semantic content lock mismatch")

    def test_rejects_unlocked_rubric_or_fixture_changes(self) -> None:
        eval_path = (
            self.plugin / "skills" / "choosing-agent-models" / "evals" / "evals.json"
        )
        original_eval = eval_path.read_text()
        document = json.loads(original_eval)
        document["evals"][0]["expected_output"] += " Altered."
        eval_path.write_text(json.dumps(document, indent=2) + "\n")
        self.assert_rejected("semantic content lock mismatch")
        eval_path.write_text(original_eval)

        fixture_path = (
            self.plugin
            / "skills"
            / "choosing-agent-models"
            / "evals"
            / "fixtures"
            / "delegation-already-chosen.md"
        )
        fixture_path.write_text(
            fixture_path.read_text() + "\nUnreviewed fixture drift.\n"
        )
        self.assert_rejected("semantic content lock mismatch")

    def test_content_lock_covers_discovery_contracts(self) -> None:
        for relative_path in (
            "README.md",
            ".claude-plugin/plugin.json",
            ".codex-plugin/plugin.json",
        ):
            with self.subTest(relative_path=relative_path):
                path = self.plugin / relative_path
                original = path.read_text()
                path.write_text(original + "\n")
                self.assert_rejected("semantic content lock mismatch")
                path.write_text(original)

    def test_rejects_topology_without_conditional_selection_edge(self) -> None:
        path = self.plugin / "topology.json"
        document = json.loads(path.read_text())
        document["skills"]["delegating-cross-agent-work"]["may_call"] = []
        path.write_text(json.dumps(document, indent=2) + "\n")
        self.assert_rejected(
            "may call model selection only when model or effort is unresolved"
        )

    def test_rejects_reverse_selection_edge(self) -> None:
        path = self.plugin / "topology.json"
        document = json.loads(path.read_text())
        document["skills"]["choosing-agent-models"]["may_call"] = [
            {
                "skill": "delegating-cross-agent-work",
                "when": "delegation-unresolved",
            }
        ]
        path.write_text(json.dumps(document, indent=2) + "\n")
        self.assert_rejected("model selection must not define a reverse call edge")

    def test_rejects_shared_topology_owner(self) -> None:
        path = self.plugin / "topology.json"
        document = json.loads(path.read_text())
        document["skills"]["choosing-agent-models"]["owns"].append("authority")
        path.write_text(json.dumps(document, indent=2) + "\n")
        self.assert_rejected("topology owner is shared: authority")

    def test_invocation_topology_receipt_is_a_separate_closed_world_contract(
        self,
    ) -> None:
        topology = json.loads((self.plugin / "topology.json").read_text())
        self.assertEqual(topology["schema_version"], 2)
        self.assertEqual(
            topology["receipt_contract"],
            {
                "id": "adapter:rolecasting-invocation-topology-receipt",
                "owner": "delegating-cross-agent-work",
                "separate_from": "adapter:model-selection-receipt",
                "binds": [
                    "dispatch-identity",
                    "lifecycle",
                    "isolation",
                    "subdelegation-authority",
                    "external-action-authority",
                ],
                "default_denies": ["subdelegation", "external-action"],
            },
        )
        self.assertIn(
            "invocation-topology-receipt",
            topology["skills"]["delegating-cross-agent-work"]["owns"],
        )
        reference = (
            self.plugin / "skills/delegating-cross-agent-work/references/"
            "invocation-topology-receipt.md"
        ).read_text()
        for required in (
            "adapter:rolecasting-invocation-topology-receipt",
            "separate from `adapter:model-selection-receipt`",
            "exactly one unique dispatch entry",
            "closed-world dispatch set",
            "default-denied subdelegation and external-action authority",
            "explicit user authority",
            "new valid plan",
        ):
            self.assertIn(required, reference)

    def test_rejects_every_invocation_receipt_contract_drift(self) -> None:
        path = self.plugin / "topology.json"
        original = json.loads(path.read_text())

        cases = [("top-level omission", lambda value: value.pop("receipt_contract"))]
        for field in original.get("receipt_contract", {}):
            cases.append(
                (
                    f"missing {field}",
                    lambda value, field=field: value["receipt_contract"].pop(field),
                )
            )
        cases.extend(
            (
                ("id", lambda value: value["receipt_contract"].update({"id": "x"})),
                (
                    "owner",
                    lambda value: value["receipt_contract"].update(
                        {"owner": "choosing-agent-models"}
                    ),
                ),
                (
                    "separate_from",
                    lambda value: value["receipt_contract"].update(
                        {"separate_from": "adapter:combined-receipt"}
                    ),
                ),
            )
        )
        for field in ("binds", "default_denies"):
            for item in original.get("receipt_contract", {}).get(field, []):
                cases.append(
                    (
                        f"{field} without {item}",
                        lambda value, field=field, item=item: value["receipt_contract"][
                            field
                        ].remove(item),
                    )
                )

        for label, mutate in cases:
            with self.subTest(label=label):
                topology = copy.deepcopy(original)
                mutate(topology)
                path.write_text(json.dumps(topology, indent=2) + "\n")
                self.assert_rejected("invocation topology receipt metadata drift")

    def test_benign_invocation_receipt_prose_can_refresh_its_semantic_lock(
        self,
    ) -> None:
        path = (
            self.plugin / "skills/delegating-cross-agent-work/references/"
            "invocation-topology-receipt.md"
        )
        path.write_text(
            path.read_text().replace(
                "Preserve\nraw failure evidence.",
                "Preserve the\nraw failure evidence.",
            )
        )
        result = subprocess.run(
            [sys.executable, str(VALIDATOR), "--write-content-lock", str(self.repo)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.validate().returncode, 0)

    def test_rejects_invocation_body_over_maximum_budget(self) -> None:
        path = self.plugin / "skills" / "choosing-agent-models" / "SKILL.md"
        path.write_text(path.read_text() + "\n" + ("extra " * 400))
        self.assert_rejected("progressive-disclosure invocation budget exceeded")

    def test_malformed_eval_values_fail_with_stable_contract_errors(self) -> None:
        path = self.plugin / "skills" / "choosing-agent-models" / "evals" / "evals.json"
        original = json.loads(path.read_text())
        cases = []

        non_object_item = json.loads(json.dumps(original))
        non_object_item["evals"][0] = 42
        cases.append((non_object_item, "eval item 1 must be an object"))

        non_object_expectation = json.loads(json.dumps(original))
        non_object_expectation["evals"][0]["expectations"][0] = None
        cases.append((non_object_expectation, "expectation 1 must be an object"))

        non_string_fixture = json.loads(json.dumps(original))
        non_string_fixture["evals"][0]["fixture_paths"] = [42]
        cases.append((non_string_fixture, "fixture_paths must contain one string"))

        for document, expected in cases:
            with self.subTest(expected=expected):
                path.write_text(json.dumps(document, indent=2) + "\n")
                result = self.validate()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_rejects_invented_persona_agent(self) -> None:
        agents = self.plugin / "agents"
        agents.mkdir()
        (agents / "rolecaster.md").write_text("invented persona\n")
        self.assert_rejected("component inventory")


if __name__ == "__main__":
    unittest.main()
