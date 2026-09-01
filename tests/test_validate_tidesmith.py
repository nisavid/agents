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

    def test_frontmatter_parser_accepts_crlf_and_missing_trailing_newline(self) -> None:
        try:
            import yaml  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("PyYAML is required for the strict frontmatter loader")
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
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
