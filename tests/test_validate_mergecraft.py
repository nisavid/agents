from __future__ import annotations

import ast
import contextlib
import copy
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPO_ROOT / "scripts" / "validate_mergecraft.py"
CONTROL_PLANE_EVALUATOR = REPO_ROOT / "scripts" / "run_control_plane_eval.py"
PLUGIN = Path("plugins/mergecraft")
EVAL_ROOT = Path("evals/mergecraft")
CONTENT_LOCK = Path("release/plugin-content-locks/mergecraft.json")
ATLAS_RELEASE = Path("release/mergecraft")
RETIREMENT_LEDGER = Path("release/mergecraft-retirement-contribution-ledger.json")
AGENT_PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
CANONICAL_IDENTITY_FIELDS = (
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
)
SPEC = importlib.util.spec_from_file_location("validate_mergecraft", VALIDATOR)
assert SPEC is not None and SPEC.loader is not None
VALIDATE_MERGECRAFT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATE_MERGECRAFT)


class AuthorityGuardRemover(ast.NodeTransformer):
    def __init__(self, field: str) -> None:
        self.field = field
        self.removed = 0

    def visit_Compare(self, node: ast.Compare) -> ast.expr:
        self.generic_visit(node)
        left = node.left
        if (
            isinstance(left, ast.Subscript)
            and isinstance(left.value, ast.Name)
            and left.value.id == "authority"
            and isinstance(left.slice, ast.Constant)
            and left.slice.value == self.field
        ):
            self.removed += 1
            return ast.copy_location(ast.Constant(value=False), node)
        return node


class CheckedInMergecraftReleaseTests(unittest.TestCase):
    def test_checked_in_content_lock_matches_current_plugin_before_test_mutation(
        self,
    ) -> None:
        plugin = VALIDATE_MERGECRAFT.locate_plugin(REPO_ROOT)
        VALIDATE_MERGECRAFT.validate_content_lock(REPO_ROOT, plugin)


class ValidateMergecraftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary_directory.name) / "repo"
        shutil.copytree(
            REPO_ROOT / PLUGIN,
            self.repo / PLUGIN,
            symlinks=True,
        )
        shutil.copytree(REPO_ROOT / EVAL_ROOT, self.repo / EVAL_ROOT, symlinks=True)
        shutil.copytree(
            REPO_ROOT / ATLAS_RELEASE,
            self.repo / ATLAS_RELEASE,
            symlinks=True,
        )
        (self.repo / RETIREMENT_LEDGER).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / RETIREMENT_LEDGER, self.repo / RETIREMENT_LEDGER)
        for plugin in ("tricritical", "versionkeeping"):
            external = self.repo / "plugins" / plugin
            external.mkdir(parents=True)
            shutil.copy2(REPO_ROOT / "plugins" / plugin / "topology.json", external)
        (self.repo / CONTENT_LOCK).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / CONTENT_LOCK, self.repo / CONTENT_LOCK)
        self.plugin = self.repo / PLUGIN
        self.retirement_ledger = self.repo / RETIREMENT_LEDGER
        VALIDATE_MERGECRAFT.write_content_lock(self.repo, self.plugin)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def run_validator(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VALIDATOR), str(self.repo), *extra],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )

    def assert_rejected(self, expected: str) -> None:
        result = self.run_validator()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(expected, result.stderr)

    def write_json(self, relative: str, value: object) -> None:
        (self.plugin / relative).write_text(
            json.dumps(value, indent=2) + "\n",
            encoding="utf-8",
        )

    def write_ledger(self, value: object) -> None:
        self.retirement_ledger.write_text(
            json.dumps(value, indent=2) + "\n",
            encoding="utf-8",
        )

    def write_eval_json(self, relative: str, value: object) -> None:
        (self.repo / EVAL_ROOT / relative).write_text(
            json.dumps(value, indent=2) + "\n",
            encoding="utf-8",
        )

    def write_atlas_json(self, relative: str, value: object) -> None:
        (self.repo / ATLAS_RELEASE / relative).write_text(
            json.dumps(value, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_accepts_current_public_contract_and_each_skill_quick_check(self) -> None:
        result = self.run_validator()
        self.assertEqual(result.returncode, 0, result.stderr)
        topology = json.loads((self.plugin / "topology.json").read_text())
        for component in topology["skills"]:
            skill = component["name"]
            with self.subTest(skill=skill):
                result = self.run_validator("--skill", skill)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_uses_canonical_agent_plugins_v1_manifest_and_discovery(self) -> None:
        canonical = json.loads((self.plugin / "plugin.json").read_text())
        topology = json.loads((self.plugin / "topology.json").read_text())

        self.assertEqual(canonical["$schema"], AGENT_PLUGIN_SCHEMA)
        self.assertEqual(canonical["name"], "mergecraft")
        self.assertEqual(canonical["version"], "1.0.0")
        self.assertEqual(set(canonical["extensions"]), {"com.openai"})
        self.assertEqual(set(canonical["extensions"]["com.openai"]), {"interface"})
        self.assertFalse((self.plugin / ".codex-plugin").exists())
        self.assertEqual(
            sorted(
                path.name
                for path in (self.plugin / "skills").iterdir()
                if path.is_dir() and (path / "SKILL.md").is_file()
            ),
            sorted(component["name"] for component in topology["skills"]),
        )

    def test_claude_manifest_is_exact_canonical_projection(self) -> None:
        canonical = json.loads((self.plugin / "plugin.json").read_text())
        claude = json.loads(
            (self.plugin / ".claude-plugin" / "plugin.json").read_text()
        )
        self.assertEqual(set(claude), set(CANONICAL_IDENTITY_FIELDS) | {"displayName"})
        self.assertEqual(claude["displayName"], "Mergecraft")
        self.assertEqual(
            {field: claude[field] for field in CANONICAL_IDENTITY_FIELDS},
            {field: canonical[field] for field in CANONICAL_IDENTITY_FIELDS},
        )

    def test_rejects_duplicate_json_at_any_depth(self) -> None:
        path = self.plugin / "plugin.json"
        path.write_text(
            path.read_text().replace(
                '"name": "mergecraft"',
                '"name": "mergecraft", "name": "other"',
            )
        )
        self.assert_rejected("duplicate JSON key")

    def test_rejects_inconsistent_change_navigation_reference_example(self) -> None:
        path = self.plugin / (
            "skills/writing-reviewable-pr-descriptions/references/change-navigation.md"
        )
        path.write_text(
            path.read_text().replace(
                "IMPL: 9 additions, 3 deletions",
                "IMPL: 10 additions, 3 deletions",
                1,
            )
        )
        result = self.run_validator("--source-stage")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("change-navigation reference example", result.stderr)

    def test_rejects_duplicate_or_contradictory_diff_summary_category_badges(
        self,
    ) -> None:
        path = self.plugin / (
            "skills/writing-reviewable-pr-descriptions/references/change-navigation.md"
        )
        original = path.read_text()
        before_diff, diff_section = original.split("## Diff Disclosure", 1)
        duplicate = (
            '<picture><img alt="IMPL: 9 additions, 3 deletions" '
            'src="https://img.shields.io/badge/'
            'IMPL-%2B9%20%E2%88%923-0969DA?style=flat" '
            'height="16"></picture> '
        )
        for metric in (
            duplicate,
            duplicate.replace(
                "9 additions, 3 deletions", "10 additions, 3 deletions"
            ).replace("%2B9%20%E2%88%923", "%2B10%20%E2%88%923"),
        ):
            with self.subTest(metric=metric):
                path.write_text(
                    before_diff
                    + "## Diff Disclosure"
                    + diff_section.replace(
                        '<picture><img alt="TEST: 16 additions, 22 deletions"',
                        metric + '<picture><img alt="TEST: 16 additions, 22 deletions"',
                        1,
                    )
                )
                result = self.run_validator("--source-stage")
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("summary category badges", result.stderr)

    def test_rejects_non_finite_json_at_any_boundary(self) -> None:
        path = self.plugin / "plugin.json"
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                path.write_text(
                    (REPO_ROOT / PLUGIN / "plugin.json")
                    .read_text()
                    .replace('"version":', f'"probe": {constant}, "version":', 1)
                )
                self.assert_rejected("non-finite JSON value")

    def test_rejects_exponent_overflow_json_at_any_boundary(self) -> None:
        path = self.plugin / "plugin.json"
        path.write_text(
            (REPO_ROOT / PLUGIN / "plugin.json")
            .read_text()
            .replace('"version":', '"probe": 1e999, "version":', 1)
        )
        self.assert_rejected("non-finite JSON value")

    def test_rejects_duplicate_yaml_key(self) -> None:
        path = self.plugin / "skills/graphite/agents/openai.yaml"
        path.write_text(path.read_text() + "\ninterface: {}\n")
        self.assert_rejected("duplicate YAML key")

    def test_rejects_non_finite_yaml_value(self) -> None:
        path = self.plugin / "skills/graphite/agents/openai.yaml"
        path.write_text(path.read_text() + "\nprobe: .nan\n")
        self.assert_rejected("non-finite YAML value")

    def test_rejects_manifest_projection_version_drift(self) -> None:
        path = self.plugin / ".claude-plugin/plugin.json"
        value = json.loads(path.read_text())
        value["version"] = "1.0.1"
        self.write_json(".claude-plugin/plugin.json", value)
        self.assert_rejected("Claude manifest projection drift: version")

    def test_rejects_unnamespaced_codex_prompt(self) -> None:
        path = self.plugin / "skills/graphite/agents/openai.yaml"
        path.write_text(path.read_text().replace("$mergecraft:graphite", "$graphite"))
        self.assert_rejected("namespaced Codex prompt")

    def test_rejects_missing_or_extra_public_skill(self) -> None:
        shutil.rmtree(self.plugin / "skills/graphite")
        self.assert_rejected("public skill inventory")

    def test_rejects_runtime_or_test_inventory_drift(self) -> None:
        path = self.plugin / "skills/graphite/extra.py"
        path.write_text("pass\n")
        self.assert_rejected("skill file inventory")

    def test_rejects_symlink_and_special_entry(self) -> None:
        link = self.plugin / "skills/graphite/linked.md"
        link.symlink_to("SKILL.md")
        self.assert_rejected("symlink")
        link.unlink()
        fifo = self.plugin / "skills/graphite/special"
        os.mkfifo(fifo)
        self.addCleanup(lambda: fifo.unlink(missing_ok=True))
        self.assertTrue(stat.S_ISFIFO(fifo.lstat().st_mode))
        self.assert_rejected("special entry")

    def test_rejects_topology_edge_or_owner_reversal(self) -> None:
        path = self.plugin / "topology.json"
        value = json.loads(path.read_text())
        next(
            component
            for component in value["skills"]
            if component["name"] == "writing-reviewable-pr-descriptions"
        )["calls"] = ["publishing-reviewable-prs"]
        self.write_json("topology.json", value)
        self.assert_rejected("forbidden reverse call")

        shutil.copy2(REPO_ROOT / PLUGIN / "topology.json", path)
        value = json.loads(path.read_text())
        operation = next(
            item for item in value["operations"] if item["semantic_id"] == "pr-creation"
        )
        operation["owner"] = "versionkeeping:checkpointing-and-publishing-git-work"
        self.write_json("topology.json", value)
        self.assert_rejected("operation owner drift")

    def test_rejects_merge_coordinator_self_actuation(self) -> None:
        path = self.plugin / "topology.json"
        value = json.loads(path.read_text())
        merge = next(
            component
            for component in value["skills"]
            if component["name"] == "getting-prs-merged"
        )
        merge["calls"] = [
            "operation:merge-actuation" if call == "internal:merge-actuator" else call
            for call in merge["calls"]
        ]
        operation = next(
            item
            for item in value["operations"]
            if item["semantic_id"] == "merge-actuation"
        )
        operation.update(
            {
                "owner": "getting-prs-merged",
                "implementation": "skills/getting-prs-merged/SKILL.md",
                "disposition": "public-skill",
            }
        )
        self.write_json("topology.json", value)
        self.assert_rejected("self-actuator ownership cycle")

    def test_operation_registry_rejects_missing_duplicate_unresolved_and_drift(
        self,
    ) -> None:
        path = self.plugin / "topology.json"

        value = json.loads(path.read_text())
        value["operations"] = [
            item for item in value["operations"] if item["semantic_id"] != "pr-creation"
        ]
        self.write_json("topology.json", value)
        self.assert_rejected("GitHub operation alias coverage drift")

        shutil.copy2(REPO_ROOT / PLUGIN / "topology.json", path)
        value = json.loads(path.read_text())
        value["operations"].append(copy.deepcopy(value["operations"][0]))
        self.write_json("topology.json", value)
        self.assert_rejected("duplicate operation export")

        shutil.copy2(REPO_ROOT / PLUGIN / "topology.json", path)
        value = json.loads(path.read_text())
        remote = next(
            item
            for item in value["operations"]
            if item["semantic_id"] == "remote-ref-deletion"
        )
        remote["owner"] = remote["import"] = "missing:owner"
        self.write_json("topology.json", value)
        self.assert_rejected("unresolved operation import")

        shutil.copy2(REPO_ROOT / PLUGIN / "topology.json", path)
        value = json.loads(path.read_text())
        publisher = next(
            item
            for item in value["skills"]
            if item["name"] == "publishing-reviewable-prs"
        )
        publisher["operations"].remove("pr-creation")
        self.write_json("topology.json", value)
        self.assert_rejected("public operation owner is not declared")

        shutil.copy2(REPO_ROOT / PLUGIN / "topology.json", path)
        value = json.loads(path.read_text())
        operation = next(
            item for item in value["operations"] if item["semantic_id"] == "pr-creation"
        )
        operation["github_aliases"] = ["pr-text-write"]
        self.write_json("topology.json", value)
        self.assert_rejected("GitHub operation alias collision")

        shutil.copy2(REPO_ROOT / PLUGIN / "topology.json", path)
        value = json.loads(path.read_text())
        operation = next(
            item for item in value["operations"] if item["semantic_id"] == "pr-creation"
        )
        operation["callers"] = []
        self.write_json("topology.json", value)
        self.assert_rejected("operation caller drift")

    def test_rejects_caller_callee_contract_drift(self) -> None:
        path = self.plugin / "topology.json"
        value = json.loads(path.read_text())
        publisher = next(
            component
            for component in value["skills"]
            if component["name"] == "publishing-reviewable-prs"
        )
        del publisher["contract"]["authority"]
        self.write_json("topology.json", value)
        self.assert_rejected("caller/callee contract drift")

    def test_rejects_nested_merge_loop_or_invalid_terminal_handoff(self) -> None:
        path = self.plugin / "topology.json"
        value = json.loads(path.read_text())
        merge = next(
            component
            for component in value["skills"]
            if component["name"] == "getting-prs-merged"
        )
        merge["calls"].append("tricritical:loop")
        self.write_json("topology.json", value)
        self.assert_rejected("nested loop ownership")

        shutil.copy2(REPO_ROOT / PLUGIN / "topology.json", path)
        value = json.loads(path.read_text())
        handoff = next(
            component
            for component in value["skills"]
            if component["name"] == "getting-prs-merged"
        )["contract"]["terminal_handoffs"][0]
        handoff["owner"] = "internal:merge-actuator"
        self.write_json("topology.json", value)
        self.assert_rejected("terminal handoff drift")

    def test_absent_pr_is_terminal_readiness_handoff_not_nested_coordination(
        self,
    ) -> None:
        topology = json.loads((self.plugin / "topology.json").read_text())
        merge = next(
            component
            for component in topology["skills"]
            if component["name"] == "getting-prs-merged"
        )
        self.assertNotIn("getting-prs-ready-for-review", merge["calls"])
        self.assertIn("readiness-handoff", merge["contract"]["terminal_statuses"])
        handoff = next(
            item
            for item in merge["contract"]["terminal_handoffs"]
            if item["owner"] == "getting-prs-ready-for-review"
        )
        self.assertEqual(handoff["trigger"], "pr-absent-or-not-review-ready")
        fixture = (
            self.repo
            / EVAL_ROOT
            / "skills/getting-prs-merged/fixtures/new-branch-publish-and-closeout.md"
        ).read_text()
        self.assertIn("returns one terminal `readiness-handoff`", fixture)
        self.assertIn("calls each required leaf once", fixture)
        self.assertIn("No lifecycle coordinator calls another", fixture)

    def test_outcome_coordinators_terminate_with_one_owner_handoff(self) -> None:
        topology = json.loads((self.plugin / "topology.json").read_text())
        skills = {item["name"]: item for item in topology["skills"]}
        outcome_coordinators = {
            "resuming-reviewed-prs",
            "addressing-pr-review-feedback",
            "getting-prs-ready-for-review",
            "getting-prs-merged",
        }
        for name in outcome_coordinators:
            with self.subTest(name=name):
                self.assertFalse(set(skills[name]["calls"]) & outcome_coordinators)

        resume = skills["resuming-reviewed-prs"]
        self.assertEqual(
            {item["owner"] for item in resume["contract"]["terminal_handoffs"]},
            {
                "addressing-pr-review-feedback",
                "getting-prs-ready-for-review",
                "getting-prs-merged",
                "operation:focused-ci",
                "versionkeeping:resolving-merge-conflicts",
            },
        )
        merge = skills["getting-prs-merged"]
        self.assertIn("operation:feedback-acquisition", merge["calls"])
        self.assertIn(
            "addressing-pr-review-feedback",
            {item["owner"] for item in merge["contract"]["terminal_handoffs"]},
        )
        feedback_acquisition = next(
            item
            for item in topology["operations"]
            if item["semantic_id"] == "feedback-acquisition"
        )
        self.assertEqual(feedback_acquisition["access"], "read")
        self.assertEqual(
            feedback_acquisition["callers"],
            ["addressing-pr-review-feedback", "getting-prs-merged"],
        )

        resume_evals = json.loads(
            (
                self.repo / EVAL_ROOT / "skills/resuming-reviewed-prs/evals.json"
            ).read_text()
        )
        by_name = {item["name"]: item for item in resume_evals["evals"]}
        for name in ("feedback-terminal-handoff", "readiness-terminal-handoff"):
            with self.subTest(eval=name):
                expected = by_name[name]["expected_output"]
                self.assertIn("exactly one terminal handoff", expected)
                self.assertIn("stops", expected)

    def test_rejects_outcome_coordinator_call_edge_and_feedback_handoff_drift(
        self,
    ) -> None:
        path = self.plugin / "topology.json"
        topology = json.loads(path.read_text())
        readiness = next(
            item
            for item in topology["skills"]
            if item["name"] == "getting-prs-ready-for-review"
        )
        readiness["calls"].append("operation:feedback-outcome")
        self.write_json("topology.json", topology)
        with self.assertRaisesRegex(
            VALIDATE_MERGECRAFT.ContractError,
            "outcome coordinator call edge",
        ):
            VALIDATE_MERGECRAFT.validate_topology(self.plugin)

        shutil.copy2(REPO_ROOT / PLUGIN / "topology.json", path)
        topology = json.loads(path.read_text())
        merge = next(
            item for item in topology["skills"] if item["name"] == "getting-prs-merged"
        )
        merge["calls"].remove("operation:feedback-acquisition")
        acquisition = next(
            item
            for item in topology["operations"]
            if item["semantic_id"] == "feedback-acquisition"
        )
        acquisition["callers"].remove("getting-prs-merged")
        self.write_json("topology.json", topology)
        with self.assertRaisesRegex(
            VALIDATE_MERGECRAFT.ContractError,
            "merge feedback terminal handoff drift",
        ):
            VALIDATE_MERGECRAFT.validate_topology(self.plugin)

    def test_resume_routes_conflict_ci_and_status_without_merge_authority(
        self,
    ) -> None:
        topology = json.loads((self.plugin / "topology.json").read_text())
        resume = next(
            item
            for item in topology["skills"]
            if item["name"] == "resuming-reviewed-prs"
        )
        handoffs = resume["contract"]["terminal_handoffs"]
        self.assertEqual(
            [(item["trigger"], item["owner"]) for item in handoffs[:2]],
            [
                (
                    "active-git-conflict-operation",
                    "versionkeeping:resolving-merge-conflicts",
                ),
                ("failed-required-github-actions", "operation:focused-ci"),
            ],
        )
        self.assertIn("status-only", resume["contract"]["modes"])
        self.assertIn("reported", resume["contract"]["terminal_statuses"])
        self.assertEqual(resume["calls"], ["operation:publication-audit"])

        skill = (self.plugin / "skills/resuming-reviewed-prs/SKILL.md").read_text()
        self.assertIn("status-only request", skill)
        self.assertIn("active conflict", skill)
        self.assertIn("failed required GitHub Actions", skill)
        self.assertIn("without merge authority", skill)

        evals = json.loads(
            (
                self.repo / EVAL_ROOT / "skills/resuming-reviewed-prs/evals.json"
            ).read_text()
        )
        by_name = {item["name"]: item for item in evals["evals"]}
        self.assertIn("active-conflict-terminal-handoff", by_name)
        self.assertIn("required-actions-terminal-handoff", by_name)
        self.assertIn("status-only-read-only", by_name)

    def test_routing_corpus_separates_pr_ship_from_issue_and_release_publication(
        self,
    ) -> None:
        corpus = json.loads((self.repo / EVAL_ROOT / "corpus.json").read_text())
        scenarios = {item["id"]: item for item in corpus["scenarios"]}
        self.assertIn(
            "getting-prs-merged", scenarios["pr-ship-positive-route"]["must_include"]
        )
        for scenario_id in (
            "issue-edit-negative-route",
            "non-pr-publication-negative-route",
        ):
            self.assertIn(
                "publishing-reviewable-prs", scenarios[scenario_id]["must_not_include"]
            )
            self.assertIn(
                "getting-prs-merged", scenarios[scenario_id]["must_not_include"]
            )

    def test_rejects_merge_handoff_prose_drift_through_content_identity(self) -> None:
        path = self.plugin / "skills/getting-prs-merged/SKILL.md"
        path.write_text(
            path.read_text().replace(
                "terminate this invocation with an exact handoff to\n"
                "   `tricritical:loop`",
                "for every merge request.",
            )
        )
        self.assert_rejected("semantic content lock mismatch")

    def test_invalid_write_candidate_preserves_existing_content_lock_bytes(
        self,
    ) -> None:
        lock_path = self.repo / CONTENT_LOCK
        original_lock = lock_path.read_bytes()
        manifest_path = self.plugin / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["version"] = "invalid-candidate"
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

        result = self.run_validator("--write-content-lock")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("manifest", result.stderr.lower())
        self.assertEqual(lock_path.read_bytes(), original_lock)

    def test_write_content_lock_rejects_symlinked_release_ancestor_without_external_changes(
        self,
    ) -> None:
        release = self.repo / "release"
        external_release = self.repo.parent / "outside-release"
        release.rename(external_release)
        try:
            release.symlink_to(external_release, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlink creation is unavailable: {error}")
        external_lock = external_release / "plugin-content-locks/mergecraft.json"
        external_lock.write_bytes(b"outside sentinel\n")
        original_entries = tuple(sorted(external_lock.parent.iterdir()))

        result = self.run_validator("--source-stage", "--write-content-lock")

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(external_lock.read_bytes(), b"outside sentinel\n")
        self.assertEqual(
            tuple(sorted(external_lock.parent.iterdir())),
            original_entries,
        )

    def test_write_content_lock_accepts_relative_repository_root(self) -> None:
        lock_path = self.repo / CONTENT_LOCK
        expected_lock = lock_path.read_bytes()
        lock_path.write_text("{}\n", encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                str(VALIDATOR),
                ".",
                "--write-content-lock",
            ],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Mergecraft semantic content lock updated", result.stdout)
        self.assertEqual(lock_path.read_bytes(), expected_lock)

    def test_unknown_repository_user_uses_contract_error_surface(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(VALIDATOR),
                "~mergecraft-path-regression-user-2f74c3db2e5b4b8cb6d0",
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("Traceback (most recent call last)", result.stderr)
        self.assertTrue(
            result.stderr.startswith("Mergecraft contract validation failed: "),
            result.stderr,
        )
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)

    def test_detected_late_refresh_failure_restores_content_lock_bytes(
        self,
    ) -> None:
        lock_path = self.repo / CONTENT_LOCK
        original_lock = lock_path.read_bytes()
        skill = self.plugin / "skills/getting-prs-merged/SKILL.md"
        skill.write_text(
            skill.read_text(encoding="utf-8") + "\nValid semantic change.\n",
            encoding="utf-8",
        )
        real_replace = os.replace

        def replace_then_drift(
            source: object,
            destination: object,
            **kwargs: object,
        ) -> None:
            real_replace(source, destination, **kwargs)
            if Path(destination).name == lock_path.name:
                skill.write_text(
                    skill.read_text(encoding="utf-8")
                    + "\nConcurrent semantic change.\n",
                    encoding="utf-8",
                )

        stderr = io.StringIO()
        with (
            mock.patch.object(
                os,
                "replace",
                side_effect=replace_then_drift,
            ),
            mock.patch.object(
                VALIDATE_MERGECRAFT.sys,
                "argv",
                [
                    "validate_mergecraft.py",
                    str(self.repo),
                    "--write-content-lock",
                ],
            ),
            contextlib.redirect_stderr(stderr),
        ):
            result = VALIDATE_MERGECRAFT.main()

        self.assertNotEqual(result, 0)
        self.assertIn("validated inputs changed", stderr.getvalue())
        self.assertEqual(lock_path.read_bytes(), original_lock)

    def test_github_operation_inventory_is_complete_and_collision_free(self) -> None:
        topology = json.loads((self.plugin / "topology.json").read_text())
        operations = {
            alias: (item["access"], item["owner"])
            for item in topology["operations"]
            for alias in item["github_aliases"]
        }
        self.assertEqual(
            len(operations),
            sum(len(item["github_aliases"]) for item in topology["operations"]),
        )
        required = {
            "pr-create",
            "repository-orientation",
            "pr-orientation",
            "issue-orientation",
            "repository-summary",
            "pr-summary",
            "issue-summary",
            "patch-inspection",
            "top-level-comment-read",
            "top-level-comment-write",
            "review-comment-read",
            "review-reply-write",
            "labels-read",
            "labels-write",
            "reactions-read",
            "reactions-write",
            "review-submit-comment",
            "review-submit-approve",
            "review-submit-request-changes",
            "review-thread-resolution",
            "check-inspection",
            "check-rerun",
            "bot-review-request",
            "pr-text-read",
            "pr-text-write",
            "pr-readiness-read",
            "pr-readiness-write",
            "merge-inspection",
            "merge-write",
        }
        self.assertEqual(set(operations), required)
        self.assertEqual(
            operations["pr-text-write"],
            ("write", "publishing-reviewable-prs"),
        )
        for alias in (
            "pr-text-read",
            "pr-readiness-read",
            "check-inspection",
            "merge-inspection",
        ):
            self.assertEqual(operations[alias][0], "read")
        for alias in (
            "pr-text-write",
            "pr-readiness-write",
            "check-rerun",
            "merge-write",
        ):
            self.assertEqual(operations[alias][0], "write")
        self.assertEqual(
            next(
                item["import"]
                for item in topology["operations"]
                if item["semantic_id"] == "git-ref-push"
            ),
            "versionkeeping:checkpointing-and-publishing-git-work",
        )
        remote = next(
            item
            for item in topology["operations"]
            if item["semantic_id"] == "remote-ref-deletion"
        )
        self.assertEqual(remote["import"], remote["owner"])
        self.assertEqual(remote["access"], "write")

    def test_operation_calls_are_mode_specific_and_recovery_routes_are_complete(
        self,
    ) -> None:
        topology = json.loads((self.plugin / "topology.json").read_text())
        skills = {item["name"]: item for item in topology["skills"]}
        operations = {item["semantic_id"]: item for item in topology["operations"]}

        self.assertEqual(
            skills["resuming-reviewed-prs"]["calls"],
            ["operation:publication-audit"],
        )
        merge_publication_calls = {
            call
            for call in skills["getting-prs-merged"]["calls"]
            if operations[call.removeprefix("operation:")]["owner"]
            == "publishing-reviewable-prs"
        }
        self.assertEqual(
            merge_publication_calls,
            {"operation:publication-audit"},
        )
        self.assertEqual(
            operations["feedback-acquisition"]["callers"],
            ["addressing-pr-review-feedback", "getting-prs-merged"],
        )
        self.assertEqual(operations["feedback-acquisition"]["access"], "read")

        graphite_modes = set(skills["graphite"]["contract"]["modes"])
        self.assertEqual(
            graphite_modes,
            {
                "create",
                "track",
                "navigate",
                "reparent",
                "metadata-repair",
                "diagnose",
                "restack",
                "submit-draft",
            },
        )
        cleanup = next(
            item
            for item in skills["getting-prs-merged"]["contract"]["terminal_handoffs"]
            if item["owner"] == "operation:remote-ref-deletion"
        )
        self.assertEqual(
            cleanup["trigger"],
            "verified-merge-and-authorized-remote-ref-deletion",
        )

        for skill in skills.values():
            self.assertTrue(
                all(call.startswith("operation:") for call in skill["calls"]),
                skill["name"],
            )

    def test_rejects_alias_access_drift_and_callee_wide_fanout(self) -> None:
        topology = json.loads((self.plugin / "topology.json").read_text())
        text_read = next(
            item
            for item in topology["operations"]
            if item["semantic_id"] == "pr-text-read"
        )
        text_read["access"] = "write"
        self.write_json("topology.json", topology)
        self.assert_rejected("GitHub operation access drift")

        topology = json.loads((REPO_ROOT / PLUGIN / "topology.json").read_text())
        graphite = next(
            item for item in topology["skills"] if item["name"] == "graphite"
        )
        graphite["calls"].append("publishing-reviewable-prs")
        self.write_json("topology.json", topology)
        self.assert_rejected("callee-wide operation fanout")

    def test_rejects_merge_eval_grader_answer_leak(self) -> None:
        path = (
            self.repo
            / EVAL_ROOT
            / "skills/getting-prs-merged/fixtures/external-review-budget.md"
        )
        path.write_text(path.read_text() + "\nExpected behavior: merge now.\n")
        self.assert_rejected("merge eval grader answer leaked into fixture")

    def test_rejects_raw_leaf_eval_grader_answer_leak(self) -> None:
        path = (
            self.repo
            / EVAL_ROOT
            / "skills/interacting-with-pr-review-feedback/fixtures/"
            / "authorized-reply-and-resolution-boundary.md"
        )
        path.write_text(path.read_text() + "\nExpected behavior: resolve it.\n")

        with self.assertRaisesRegex(
            VALIDATE_MERGECRAFT.ContractError,
            "raw eval grader answer leaked into fixture",
        ):
            VALIDATE_MERGECRAFT.validate_raw_skill_eval_isolation(self.repo)

    def test_rejects_writer_forge_terminal_boundary_regression(self) -> None:
        path = self.plugin / "skills/writing-reviewable-pr-descriptions/SKILL.md"
        path.write_text(
            path.read_text().replace(
                "Do not mutate the forge or verify stored/rendered state",
                "Publish and verify the stored state",
            )
        )
        self.assert_rejected("writer content-only terminal boundary drift")

    def test_rejects_writer_independent_review_gate_regression(self) -> None:
        path = self.plugin / "skills/writing-reviewable-pr-descriptions/SKILL.md"
        path.write_text(
            path.read_text().replace("bare `clean` receipt", "review receipt")
        )
        self.assert_rejected("writer independent review gate drift")

    def test_rejects_pr_text_secret_blocker_regression(self) -> None:
        path = (
            self.plugin
            / "skills/writing-reviewable-pr-descriptions/references/body-contract.md"
        )
        path.write_text(
            path.read_text().replace(
                "Never quote, echo, preserve, or republish it",
                "preserve the live value",
            )
        )
        self.assert_rejected("PR text secret blocker drift")

    def test_rejects_feedback_snapshot_mode_regression(self) -> None:
        path = self.plugin / "skills/addressing-pr-review-feedback/SKILL.md"
        path.write_text(path.read_text().replace("stop before step 2", "continue"))
        self.assert_rejected("feedback snapshot mode drift")

    def test_rejects_gh_fix_ci_adapter_broadening(self) -> None:
        path = self.plugin / "skills/getting-prs-merged/references/gh-fix-ci-adapter.md"
        path.write_text(path.read_text().replace("merge actuation", "merge handling"))
        self.assert_rejected("gh-fix-ci adapter authority drift")

    def test_rejects_merge_actuator_broadening(self) -> None:
        path = self.plugin / "skills/getting-prs-merged/references/merge-actuator.md"
        path.write_text(
            path.read_text().replace(
                "execute at most once",
                "execute repeatedly",
            )
        )
        self.assert_rejected("merge actuator authority drift")

    def test_rejects_retired_review_orchestration_route(self) -> None:
        path = self.plugin / "skills/resuming-reviewed-prs/SKILL.md"
        path.write_text(path.read_text() + "\nCall pr-review-orchestration.\n")
        self.assert_rejected("retired route")

    def test_rejects_publisher_text_scope_behavior_drift(self) -> None:
        path = (
            self.plugin
            / "skills/publishing-reviewable-prs/scripts/update_reviewable_pr.py"
        )
        path.write_text(
            path.read_text()
            + "\nupdate_text = lambda **kwargs: {'accepted': True}\n"
        )
        self.assert_rejected("candidate runtime behavior")

    def test_rejects_boolean_topology_schema_version(self) -> None:
        path = self.plugin / "topology.json"
        value = json.loads(path.read_text())
        value["schema_version"] = True
        self.write_json("topology.json", value)
        self.assert_rejected("topology identity drift")

    def test_rejects_readme_projection_drift(self) -> None:
        path = self.plugin / "README.md"
        path.write_text(
            path.read_text().replace(
                "| graphite | Graphite topology",
                "| stack-tool | Graphite topology",
            )
        )
        self.assert_rejected("README skill projection")

    def test_rejects_broken_relative_skill_link(self) -> None:
        path = self.plugin / "skills/graphite/SKILL.md"
        path.write_text(
            path.read_text().replace("../publishing-reviewable-prs", "../missing")
        )
        self.assert_rejected("Agent Skill resource is missing")

    def test_rejects_private_or_personal_content(self) -> None:
        path = self.plugin / "skills/graphite/SKILL.md"
        path.write_text(path.read_text() + "\n/Users/ivan/private\n")
        self.assert_rejected("portability leak")

        path.write_text(
            (REPO_ROOT / PLUGIN / "skills/graphite/SKILL.md").read_text()
            + "\nUse the ivan/ branch prefix.\n"
        )
        self.assert_rejected("portability leak")

    def test_rejects_retired_route_fill_or_compatibility_shim(self) -> None:
        path = self.plugin / "skills/graphite/SKILL.md"
        path.write_text(path.read_text() + "\nUse resolving-workflow-ownership.\n")
        self.assert_rejected("retired route")

        path.write_text(
            (REPO_ROOT / PLUGIN / "skills/graphite/SKILL.md").read_text()
            + "\nRun gh pr create --fill.\n"
        )
        self.assert_rejected("generic generated-text route")

        shim = self.plugin / "skills/getting-prs-merged/scripts/trigger_eval_core.py"
        shim.parent.mkdir(exist_ok=True)
        shim.write_text("pass\n")
        self.assert_rejected("compatibility shim")

    def test_atlas_evidence_is_repository_owned_and_byte_preserved(self) -> None:
        runtime_references = (
            self.plugin / "skills/writing-reviewable-pr-descriptions/references"
        )
        self.assertFalse((runtime_references / "review-atlas-contract.json").exists())
        self.assertFalse(
            (runtime_references / "review-atlas-contribution-ledger.json").exists()
        )
        topology = json.loads((self.plugin / "topology.json").read_text())
        writer = next(
            component
            for component in topology["skills"]
            if component["name"] == "writing-reviewable-pr-descriptions"
        )
        self.assertNotIn(
            "skills/writing-reviewable-pr-descriptions/references/"
            "review-atlas-contract.json",
            writer["references"],
        )
        self.assertNotIn(
            "skills/writing-reviewable-pr-descriptions/references/"
            "review-atlas-contribution-ledger.json",
            writer["references"],
        )
        # These literals intentionally bind the reviewed release evidence bytes.
        # Review each changed artifact against its owning sources before updating them.
        expected_digests = {
            "review-atlas-contract.json": (
                "d8f1f44707134a9546f444751688af067b5f7f640ff6d39f17c8c7e18bdf30c2"
            ),
            "review-atlas-contribution-ledger.json": (
                "5804803a8abb18e26c2b7700670d036aadf6d44cab2b0457f7b8a69e1a9e0046"
            ),
        }
        self.assertEqual(
            {
                path.name: VALIDATE_MERGECRAFT.hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in (self.repo / ATLAS_RELEASE).iterdir()
            },
            expected_digests,
        )

    def test_rejects_atlas_ledger_gap_or_private_overlay_leak(self) -> None:
        path = self.repo / ATLAS_RELEASE / "review-atlas-contribution-ledger.json"
        value = json.loads(path.read_text())
        value["contributions"][0]["source_headings"] = []
        self.write_atlas_json(
            "review-atlas-contribution-ledger.json",
            value,
        )
        self.assert_rejected("atlas contribution mapping")

        shutil.copy2(
            REPO_ROOT / ATLAS_RELEASE / "review-atlas-contribution-ledger.json",
            path,
        )
        atlas = (
            self.plugin / "skills/writing-reviewable-pr-descriptions/"
            "review-atlas-reference-design.md"
        )
        atlas.write_text(atlas.read_text() + "\nprivate attachment URL\n")
        self.assert_rejected("private atlas detail")

    def test_rejects_synced_atlas_contribution_mapping_swap(self) -> None:
        ledger_relative = "review-atlas-contribution-ledger.json"
        canonical_relative = "review-atlas-contract.json"
        ledger = json.loads((self.repo / ATLAS_RELEASE / ledger_relative).read_text())
        canonical = json.loads(
            (self.repo / ATLAS_RELEASE / canonical_relative).read_text()
        )
        ledger["contributions"][2]["destination_anchor"] = "architecture"
        ledger["contributions"][3]["destination_anchor"] = "design-principles"
        canonical["contribution_ledger"] = ledger
        self.write_atlas_json(ledger_relative, ledger)
        self.write_atlas_json(canonical_relative, canonical)
        self.assert_rejected("atlas contribution mapping drift")

    def test_rejects_self_updated_atlas_prose_digest_for_changed_bytes(self) -> None:
        writer_relative = "skills/writing-reviewable-pr-descriptions/SKILL.md"
        canonical_relative = "review-atlas-contract.json"
        writer = self.plugin / writer_relative
        writer.write_text(
            writer.read_text() + "\nOverlay policy outranks public policy.\n"
        )
        canonical = json.loads(
            (self.repo / ATLAS_RELEASE / canonical_relative).read_text()
        )
        canonical["prose_sha256"]["writer"] = VALIDATE_MERGECRAFT.hashlib.sha256(
            writer.read_bytes()
        ).hexdigest()
        self.write_atlas_json(canonical_relative, canonical)
        result = self.run_validator("--source-stage")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("atlas public prose bytes drift", result.stderr)

    def test_rejects_missing_concrete_atlas_visual_budget(self) -> None:
        path = (
            self.plugin
            / "skills"
            / "writing-reviewable-pr-descriptions"
            / "review-atlas-reference-design.md"
        )
        path.write_text(
            path.read_text().replace(
                "at least 16 CSS pixels between\n  centerlines", ""
            )
        )
        self.assert_rejected("atlas visual budget missing")

    def test_rejects_atlas_writer_firewall_or_body_preservation_drift(self) -> None:
        writer = self.plugin / "skills/writing-reviewable-pr-descriptions/SKILL.md"
        original = writer.read_text()
        weakened = original.replace(
            (
                "Keep atlas source,\ntests, docs, manifests, and generated assets "
                "outside application repositories."
            ),
            "Keep atlas assets available.",
        )
        self.assertNotEqual(weakened, original)
        writer.write_text(weakened)
        self.assert_rejected("atlas writer contract drift")

        shutil.copy2(
            REPO_ROOT / PLUGIN / "skills/writing-reviewable-pr-descriptions/SKILL.md",
            writer,
        )
        body_contract = (
            self.plugin
            / "skills/writing-reviewable-pr-descriptions/references/body-contract.md"
        )
        body_contract.write_text(
            body_contract.read_text().replace(
                "Preserve an unauthorized existing field byte-for-byte.",
                "Preserve fields.",
            )
        )
        self.assert_rejected("atlas body preservation contract drift")

    def test_atlas_extension_contract_is_portable_and_additive(self) -> None:
        path = (
            self.plugin / "skills/writing-reviewable-pr-descriptions/references/"
            "review-atlas-extension.json"
        )
        extension = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(extension["schema_version"], 1)
        self.assertEqual(
            extension["default_overlay_path"],
            "~/.config/mergecraft/review-atlas-overlay.md",
        )
        self.assertEqual(extension["absence"], "continue-public-core")
        self.assertEqual(extension["precedence"], "public-core")
        self.assertEqual(
            set(extension["allowed_authority"]),
            {"instance-data", "stricter-local-policy"},
        )
        self.assertIn("public-contract-redefinition", extension["forbidden_authority"])
        extension["precedence"] = "private-overlay"
        self.write_json(
            "skills/writing-reviewable-pr-descriptions/references/"
            "review-atlas-extension.json",
            extension,
        )
        self.assert_rejected("atlas extension contract drift")

    def test_rejects_boolean_or_float_atlas_ledger_integers(self) -> None:
        relative = "review-atlas-contribution-ledger.json"
        path = self.repo / ATLAS_RELEASE / relative
        original = json.loads(path.read_text())
        for field, malformed in (
            ("schema_version", True),
            ("schema_version", 1.0),
            ("source_line_count", 399.0),
        ):
            with self.subTest(field=field, malformed=malformed):
                ledger = json.loads(json.dumps(original))
                ledger[field] = malformed
                self.write_atlas_json(relative, ledger)
                self.assert_rejected("atlas ledger schema drift")

    def test_rejects_behavior_corpus_coverage_drift(self) -> None:
        path = self.repo / EVAL_ROOT / "corpus.json"
        value = json.loads(path.read_text())
        value["scenarios"] = value["scenarios"][:-1]
        self.write_eval_json("corpus.json", value)
        self.assert_rejected("behavior corpus")

    def test_rejects_boolean_and_float_behavior_corpus_versions(self) -> None:
        path = self.repo / EVAL_ROOT / "corpus.json"
        original = json.loads(path.read_text())
        for malformed in (True, 1.0):
            with self.subTest(malformed=malformed):
                corpus = json.loads(json.dumps(original))
                corpus["version"] = malformed
                self.write_eval_json("corpus.json", corpus)
                self.assert_rejected("behavior corpus schema drift")

    def test_rejects_publisher_parser_behavior_drift(self) -> None:
        state = (
            self.plugin
            / "skills/publishing-reviewable-prs/scripts/reviewable_pr_state.py"
        )
        state.write_text(
            state.read_text()
            + "\nstrict_json = lambda output, source: {'accepted': True}\n"
        )
        self.assert_rejected("candidate runtime behavior")

    def test_rejects_reconciliation_behavior_drift(self) -> None:
        audit = (
            self.plugin
            / "skills/publishing-reviewable-prs/scripts/audit_reviewable_pr.py"
        )
        audit.write_text(audit.read_text() + "\nreconcile = lambda **kwargs: None\n")
        self.assert_rejected("candidate runtime behavior")

    def test_rejects_required_review_authority_behavior_drift(self) -> None:
        required_review = (
            self.plugin
            / "skills/publishing-reviewable-prs/scripts/required_review.py"
        )
        required_review.write_text(
            required_review.read_text()
            + "\nvalidate_required_review = lambda **kwargs: None\n"
        )
        self.assert_rejected("candidate runtime behavior")

    def assert_removed_authority_guard_rejected(self, field: str) -> None:
        required_review = (
            self.plugin
            / "skills/publishing-reviewable-prs/scripts/required_review.py"
        )
        tree = ast.parse(required_review.read_text(encoding="utf-8"))
        remover = AuthorityGuardRemover(field)
        tree = remover.visit(tree)
        self.assertEqual(remover.removed, 1)
        required_review.write_text(
            ast.unparse(ast.fix_missing_locations(tree)) + "\n",
            encoding="utf-8",
        )

        result = self.run_validator("--source-stage")

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("candidate runtime behavior", result.stderr)

    def test_source_stage_rejects_removed_required_review_access_guard(self) -> None:
        self.assert_removed_authority_guard_rejected("access")

    def test_source_stage_rejects_removed_required_review_subdelegation_guard(
        self,
    ) -> None:
        self.assert_removed_authority_guard_rejected("subdelegation")

    def test_source_stage_rejects_removed_required_review_external_action_guard(
        self,
    ) -> None:
        self.assert_removed_authority_guard_rejected("external_action")

    def test_rejects_create_lifecycle_behavior_drift(self) -> None:
        create = (
            self.plugin
            / "skills/publishing-reviewable-prs/scripts/create_reviewable_pr.py"
        )
        create.write_text(
            create.read_text() + "\npublish = lambda **kwargs: {'accepted': True}\n"
        )
        self.assert_rejected("candidate runtime behavior")

    def test_rejects_receipt_audit_behavior_drift(self) -> None:
        receipts = (
            self.plugin
            / "skills/publishing-reviewable-prs/scripts/publication_receipts.py"
        )
        receipts.write_text(
            receipts.read_text()
            + "\naudit_publication = lambda **kwargs: {'accepted': True}\n"
        )
        self.assert_rejected("candidate runtime behavior")

    def test_rejects_graphite_scope_and_authority_behavior_drift(self) -> None:
        graphite = self.plugin / "skills/graphite/scripts/submit_draft_stack.py"
        graphite.write_text(
            graphite.read_text() + "\nbuild_plan = lambda request: {'accepted': True}\n"
        )
        self.assert_rejected("candidate runtime behavior")

    def test_rejects_comment_publication_behavior_drift(self) -> None:
        comment = (
            self.plugin
            / "skills/getting-prs-merged/scripts/post_coderabbit_comment.py"
        )
        comment.write_text(
            comment.read_text() + "\npost_comment = lambda **kwargs: {'accepted': True}\n"
        )
        self.assert_rejected("candidate runtime behavior")

    def test_rejects_publisher_exact_identity_behavior_drift(self) -> None:
        state = (
            self.plugin
            / "skills/publishing-reviewable-prs/scripts/reviewable_pr_state.py"
        )
        state.write_text(
            state.read_text()
            + "\nidentity_matches = lambda stored, expected: True\n"
        )
        self.assert_rejected("candidate runtime behavior")

    def test_rejects_feedback_parser_behavior_drift(self) -> None:
        state = (
            self.plugin
            / "skills/addressing-pr-review-feedback/scripts/review_feedback_state.py"
        )
        state.write_text(
            state.read_text()
            + "\nstrict_json = lambda content, source: {'accepted': True}\n"
        )
        self.assert_rejected("candidate runtime behavior")

    def test_rejects_feedback_state_behavior_drift(self) -> None:
        state = (
            self.plugin
            / "skills/addressing-pr-review-feedback/scripts/review_feedback_state.py"
        )
        state.write_text(
            state.read_text() + "\nstate_from_pages = lambda *args, **kwargs: {}\n"
        )
        self.assert_rejected("candidate runtime behavior")

    def test_rejects_review_input_parser_behavior_drift(self) -> None:
        parser = self.plugin / (
            "skills/writing-reviewable-pr-descriptions/scripts/"
            "change_navigation/review_input.py"
        )
        parser.write_text(
            parser.read_text() + "\nload_review_input = lambda path: object()\n"
        )
        self.assert_rejected("candidate runtime behavior")

    def test_rejects_semantic_drift_not_named_by_phrase_checks(self) -> None:
        path = self.plugin / "README.md"
        path.write_text(
            path.read_text() + "\nPublication may skip final verification.\n"
        )
        self.assert_rejected("semantic content lock mismatch")

    def test_rejects_retirement_ledger_missing_or_duplicate_contribution(self) -> None:
        path = self.retirement_ledger
        original = json.loads(path.read_text())

        missing = copy.deepcopy(original)
        missing["contributions"].pop()
        self.write_ledger(missing)
        self.assert_rejected("retirement contribution coverage drift")

        duplicate = copy.deepcopy(original)
        duplicate["contributions"].append(copy.deepcopy(duplicate["contributions"][0]))
        self.write_ledger(duplicate)
        self.assert_rejected("retirement contribution schema drift")

    def test_rejects_retirement_ledger_unmapped_fixture_or_owner(self) -> None:
        path = self.retirement_ledger
        original = json.loads(path.read_text())

        unmapped_fixture = copy.deepcopy(original)
        unmapped_fixture["contributions"][0]["fixture"] = "missing-fixture"
        self.write_ledger(unmapped_fixture)
        self.assert_rejected("retirement contribution schema drift")

        unmapped_owner = copy.deepcopy(original)
        unmapped_owner["contributions"][0]["destination_owner"] = "unmapped-owner"
        self.write_ledger(unmapped_owner)
        self.assert_rejected("retirement contribution schema drift")

    def test_rejects_retirement_fixture_assertion_polarity_or_evaluation_drift(
        self,
    ) -> None:
        corpus_path = self.repo / EVAL_ROOT / "retirement-comparative-corpus.json"
        corpus = json.loads(corpus_path.read_text())
        (
            corpus["scenarios"][0]["must_include"],
            corpus["scenarios"][0]["must_not_include"],
        ) = (
            corpus["scenarios"][0]["must_not_include"],
            corpus["scenarios"][0]["must_include"],
        )
        self.write_eval_json("retirement-comparative-corpus.json", corpus)
        self.assert_rejected("retirement fixture assertion polarity drift")

        shutil.copy2(
            REPO_ROOT / EVAL_ROOT / "retirement-comparative-corpus.json", corpus_path
        )
        fixtures_path = self.repo / EVAL_ROOT / "retirement-fixtures.json"
        fixtures = json.loads(fixtures_path.read_text())
        fixtures["fixtures"][0]["topology_operations"] = ["missing-operation"]
        self.write_eval_json("retirement-fixtures.json", fixtures)
        self.assert_rejected("retirement fixture coverage drift")

    def test_retirement_control_plane_definition_validates_without_a_provider(
        self,
    ) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(CONTROL_PLANE_EVALUATOR),
                "--repo",
                str(REPO_ROOT),
                "--definition",
                str(REPO_ROOT / EVAL_ROOT / "retirement-control-plane.json"),
                "validate-definition",
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"skills": 15', result.stdout)

    def test_rejects_retirement_destination_owner_missing_from_bundle(self) -> None:
        definition_path = self.repo / EVAL_ROOT / "retirement-control-plane.json"
        original = json.loads(definition_path.read_text())
        mutations = (
            (
                "ordinary-tool",
                lambda skills: skills["mergecraft:resuming-reviewed-prs"][
                    "comparison"
                ].update(owner="wrong-ordinary-owner"),
            ),
            (
                "feedback-acquisition",
                lambda skills: skills[
                    "mergecraft:addressing-pr-review-feedback"
                ].update(
                    companions=[
                        "tricritical:adjudicate",
                        "tricritical:revise",
                        "versionkeeping:checkpointing-and-publishing-git-work",
                    ]
                ),
            ),
            (
                "checkpointing",
                lambda skills: skills["mergecraft:getting-prs-ready-for-review"].update(
                    companions=[
                        "mergecraft:writing-reviewable-pr-descriptions",
                        "mergecraft:publishing-reviewable-prs",
                    ]
                ),
            ),
            (
                "retained-specialist",
                lambda skills: skills["mergecraft:getting-prs-merged"][
                    "comparison"
                ].update(owner="github:other-specialist"),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                definition = copy.deepcopy(original)
                skills = {skill["id"]: skill for skill in definition["skills"]}
                mutate(skills)
                self.write_eval_json("retirement-control-plane.json", definition)
                self.assert_rejected(
                    "retirement destination owner is absent from evaluated bundle"
                )

    def test_rejects_feedback_leaf_as_the_acquisition_target(self) -> None:
        fixtures_path = self.repo / EVAL_ROOT / "retirement-fixtures.json"
        fixtures = json.loads(fixtures_path.read_text())
        fixtures["fixtures"][1]["evaluation_skill_id"] = (
            "mergecraft:interacting-with-pr-review-feedback"
        )
        self.write_eval_json("retirement-fixtures.json", fixtures)
        self.assert_rejected("retirement fixture coverage drift")


if __name__ == "__main__":
    unittest.main()
