from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPO_ROOT / "scripts" / "validate_tidesmith.py"
REGISTRY_START = "<!-- BEGIN GENERATED SKILL REGISTRY -->"
REGISTRY_END = "<!-- END GENERATED SKILL REGISTRY -->"


class ValidateTidesmithTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.tempdir.name).resolve()
        self.repo = self.temp_root / "repo"
        self.plugin = self.repo / "plugins" / "tidesmith"
        shutil.copytree(REPO_ROOT / "plugins" / "tidesmith", self.plugin)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def validate(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VALIDATOR), *arguments, str(self.repo)],
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_rejected(self, expected: str) -> None:
        result = self.validate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(expected, result.stderr)

    def readme(self) -> str:
        return (self.plugin / "README.md").read_text(encoding="utf-8")

    def write_readme(self, content: str) -> None:
        (self.plugin / "README.md").write_text(content, encoding="utf-8")

    def test_accepts_current_contract(self) -> None:
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Tidesmith contract validation passed\n")

    def test_committed_lock_matches_semantic_files(self) -> None:
        lock = json.loads((self.plugin / "content-lock.json").read_text())
        self.assertEqual(set(lock), {"schema_version", "algorithm", "files"})
        self.assertEqual(
            set(lock["files"]),
            {
                ".claude-plugin/plugin.json",
                "CHANGELOG.md",
                "LICENSE",
                "README.md",
                "evals/delivery.json",
                "plugin.json",
                "topology.json",
            },
        )

    def test_publishes_no_skill_yet(self) -> None:
        topology = json.loads((self.plugin / "topology.json").read_text())
        self.assertEqual(topology, {"schema_version": 1, "skills": {}})
        self.assertFalse((self.plugin / "skills").exists())
        manifest = json.loads((self.plugin / "plugin.json").read_text())
        self.assertEqual(
            manifest["extensions"]["com.openai"]["interface"]["defaultPrompt"], []
        )

    def test_rejects_claude_projection_drift(self) -> None:
        path = self.plugin / ".claude-plugin" / "plugin.json"
        claude = json.loads(path.read_text())
        claude["description"] = "Tidesmith: something else."
        path.write_text(json.dumps(claude, indent=2) + "\n")
        self.assert_rejected("Claude manifest projection drift: description")

    def test_rejects_display_name_drift(self) -> None:
        path = self.plugin / ".claude-plugin" / "plugin.json"
        claude = json.loads(path.read_text())
        claude["displayName"] = "Copydesk"
        path.write_text(json.dumps(claude, indent=2) + "\n")
        self.assert_rejected("Claude displayName drift")

    def test_rejects_topology_schema_drift(self) -> None:
        path = self.plugin / "topology.json"
        path.write_text(json.dumps({"schema_version": 2, "skills": {}}) + "\n")
        self.assert_rejected("topology schema_version drift")

    def test_rejects_skill_declared_without_skill_directory(self) -> None:
        path = self.plugin / "topology.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "skills": {"writing-for-people": {"owns": ["register"], "may_call": []}},
                }
            )
            + "\n"
        )
        self.assert_rejected("skills directory is missing")

    def test_rejects_stale_skill_registry(self) -> None:
        readme = self.readme()
        start = readme.index(REGISTRY_START) + len(REGISTRY_START)
        end = readme.index(REGISTRY_END)
        self.write_readme(readme[:start] + "\nstale registry\n" + readme[end:])
        self.assert_rejected("README skill registry drift")

    def test_rejects_registry_marker_drift(self) -> None:
        self.write_readme(self.readme().replace(REGISTRY_END, ""))
        self.assert_rejected("README skill registry markers drift")

    def test_rejects_semantic_drift_without_lock_refresh(self) -> None:
        self.write_readme(self.readme() + "\nAn unlocked paragraph.\n")
        self.assert_rejected("semantic content lock mismatch")

    def test_rejects_inventory_drift(self) -> None:
        (self.plugin / "NOTES.md").write_text("stray\n")
        self.assert_rejected("component inventory drift")

    def test_rejects_symlinked_inventory(self) -> None:
        target = self.plugin / "LICENSE"
        link = self.plugin / "LICENSE.link"
        os.symlink(target, link)
        self.assert_rejected("plugin inventory contains a symlink")

    def test_rejects_persona_agents(self) -> None:
        (self.plugin / "agents").mkdir()
        (self.plugin / "agents" / "writer.md").write_text("persona\n")
        self.assert_rejected("Tidesmith must not define persona agents")

    def test_rejects_missing_release_heading(self) -> None:
        path = self.plugin / "CHANGELOG.md"
        path.write_text(path.read_text().replace("## 1.0.0", "## Unreleased"))
        self.assert_rejected("changelog release drift")

    def test_rejects_portability_leak(self) -> None:
        self.write_readme(self.readme() + "\nSee /Users/someone/notes for details.\n")
        self.assert_rejected("portability or credential leak in README.md")

    def test_write_content_lock_regenerates_registry_and_lock(self) -> None:
        readme = self.readme()
        start = readme.index(REGISTRY_START) + len(REGISTRY_START)
        end = readme.index(REGISTRY_END)
        self.write_readme(readme[:start] + "\nstale registry\n" + readme[end:])
        (self.plugin / "content-lock.json").unlink()
        result = self.validate("--write-content-lock")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Tidesmith semantic content lock updated", result.stdout)
        self.assertEqual(self.readme(), readme)
        self.assertTrue((self.plugin / "content-lock.json").is_file())
        self.assertEqual(self.validate().returncode, 0)

    def test_failed_write_leaves_generated_files_untouched(self) -> None:
        readme = self.readme()
        start = readme.index(REGISTRY_START) + len(REGISTRY_START)
        end = readme.index(REGISTRY_END)
        stale = readme[:start] + "\nstale registry\n" + readme[end:]
        self.write_readme(stale)
        lock_before = (self.plugin / "content-lock.json").read_bytes()
        (self.plugin / "NOTES.md").write_text("stray\n")
        result = self.validate("--write-content-lock")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("component inventory drift", result.stderr)
        self.assertEqual(self.readme(), stale)
        self.assertEqual((self.plugin / "content-lock.json").read_bytes(), lock_before)

    def publish_one_skill(self, *, description: str = "Use when prose must meet the house register.") -> str:
        skill = "writing-for-people"
        prompt = f"Use $tidesmith:{skill} to write this message to its reader."
        topology_path = self.plugin / "topology.json"
        topology_path.write_text(
            json.dumps(
                {"schema_version": 1, "skills": {skill: {"owns": ["register"], "may_call": []}}},
                indent=2,
            )
            + "\n"
        )
        root = self.plugin / "skills" / skill
        (root / "agents").mkdir(parents=True)
        (root / "references").mkdir()
        (root / "SKILL.md").write_text(
            "---\n"
            f"name: {skill}\n"
            f"description: {description}\n"
            "---\n\n"
            "# Writing for people\n\n"
            "Open with the finding. Details live in "
            "[the register reference](references/register.md).\n",
            encoding="utf-8",
        )
        (root / "references" / "register.md").write_text("# Register\n\nPlain speech.\n")
        (root / "agents" / "openai.yaml").write_text(
            "interface:\n"
            '  display_name: "Writing for people"\n'
            '  short_description: "Write human-facing prose to house standards."\n'
            f'  default_prompt: "{prompt}"\n'
        )
        manifest_path = self.plugin / "plugin.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["extensions"]["com.openai"]["interface"]["defaultPrompt"] = [prompt]
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        (root / "evals" / "fixtures").mkdir(parents=True)
        evals = []
        for number in range(1, 6):
            fixture = f"case-{number}.md"
            (root / "evals" / "fixtures" / fixture).write_text(
                f"# Case {number}\n\nA reader needs a short status message; facts: item {number} is done.\n"
            )
            evals.append(
                {
                    "id": number,
                    "name": f"case-{number}",
                    "prompt": "Using the fixture, write the message. Do not use tools.",
                    "fixture_paths": [f"evals/fixtures/{fixture}"],
                    "expected_output": "A plain message that opens with the outcome.",
                    "expectations": [
                        {"id": "opens-with-outcome", "text": "Opens with the outcome.", "severity": "quality"},
                        {"id": "no-invention", "text": "States only fixture facts.", "severity": "safety"},
                        {"id": "no-tells", "text": "No mic-drop closer.", "severity": "quality"},
                    ],
                }
            )
        (root / "evals" / "evals.json").write_text(
            json.dumps({"skill_name": skill, "evals": evals}, indent=2) + "\n"
        )
        return skill

    def test_publishing_one_skill_regenerates_registry_and_locks_skill_files(self) -> None:
        try:
            import yaml  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("PyYAML is required for skill validation")
        skill = self.publish_one_skill()
        result = self.validate("--write-content-lock")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"| `{skill}` | register | - |", self.readme())
        lock = json.loads((self.plugin / "content-lock.json").read_text())
        self.assertIn(f"skills/{skill}/SKILL.md", lock["files"])
        self.assertIn(f"skills/{skill}/agents/openai.yaml", lock["files"])
        self.assertIn(f"skills/{skill}/references/register.md", lock["files"])
        self.assertIn(f"skills/{skill}/evals/evals.json", lock["files"])
        self.assertIn(f"skills/{skill}/evals/fixtures/case-1.md", lock["files"])
        self.assertEqual(self.validate().returncode, 0)

    def test_published_skill_requires_three_expectations_per_eval(self) -> None:
        try:
            import yaml  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("PyYAML is required for skill validation")
        skill = self.publish_one_skill()
        path = self.plugin / "skills" / skill / "evals" / "evals.json"
        document = json.loads(path.read_text())
        document["evals"][0]["expectations"].pop()
        path.write_text(json.dumps(document) + "\n")
        result = self.validate("--write-content-lock")
        self.assertIn("expectations must contain three objects", result.stderr)

    def test_published_skill_rejects_grader_answer_in_fixture(self) -> None:
        try:
            import yaml  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("PyYAML is required for skill validation")
        skill = self.publish_one_skill()
        fixture = self.plugin / "skills" / skill / "evals" / "fixtures" / "case-1.md"
        fixture.write_text(fixture.read_text() + "\nPass if the message is short.\n")
        result = self.validate("--write-content-lock")
        self.assertIn("grader answer leaked into fixture", result.stderr)

    def test_published_skill_rejects_unreferenced_fixture(self) -> None:
        try:
            import yaml  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("PyYAML is required for skill validation")
        skill = self.publish_one_skill()
        (self.plugin / "skills" / skill / "evals" / "fixtures" / "orphan.md").write_text("# Orphan\n")
        result = self.validate("--write-content-lock")
        self.assertIn("fixture drift", result.stderr)

    def test_published_skill_description_must_start_with_the_trigger_phrase(self) -> None:
        try:
            import yaml  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("PyYAML is required for skill validation")
        self.publish_one_skill(description="Writes prose to the house register.")
        result = self.validate("--write-content-lock")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("description trigger drift", result.stderr)

    def test_frontmatter_parser_accepts_crlf_and_missing_trailing_newline(self) -> None:
        try:
            import yaml  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("PyYAML is required for the strict frontmatter loader")
        scripts_dir = str(REPO_ROOT / "scripts")
        sys.path.insert(0, scripts_dir)
        self.addCleanup(lambda: sys.path.remove(scripts_dir))
        import validate_tidesmith as module

        crlf = "---\r\nname: writing-for-people\r\ndescription: Use when x.\r\n---\r\nBody\r\n"
        bare = "---\nname: writing-for-people\ndescription: Use when x.\n---\nBody"
        for content in (crlf, bare):
            frontmatter = module.load_skill_frontmatter(content, "writing-for-people")
            self.assertEqual(frontmatter["name"], "writing-for-people")
        with self.assertRaises(module.ContractError):
            module.load_skill_frontmatter("---\nname: x\ndescription: Use when x.\n---", "x")

    def test_rejects_unknown_flag(self) -> None:
        result = self.validate("--unknown")
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
