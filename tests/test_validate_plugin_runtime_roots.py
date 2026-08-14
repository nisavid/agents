from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
VALIDATOR = REPOSITORY / "scripts/validate_plugin_runtime_roots.py"


class ValidatePluginRuntimeRootsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository = Path(self.temporary_directory.name) / "repository"
        shutil.copytree(
            REPOSITORY / "plugins", self.repository / "plugins", symlinks=True
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def validate(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VALIDATOR), str(self.repository)],
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_rejected(self, message: str) -> None:
        result = self.validate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(message, result.stderr)

    def test_accepts_declared_runtime_roots(self) -> None:
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_legacy_codex_adapter_in_standard_runtime_root(self) -> None:
        legacy = self.repository / "plugins/versionkeeping/.codex-plugin"
        legacy.mkdir()
        (legacy / "plugin.json").write_text("{}\n")

        self.assert_rejected("runtime root inventory drift")

    def test_rejects_development_and_generated_content(self) -> None:
        root = self.repository / "plugins/versionkeeping"
        (root / "evals").mkdir()
        (root / "evals/corpus.json").write_text("{}\n")
        self.assert_rejected("development subtree")
        shutil.rmtree(root / "evals")
        (root / "content-lock.json").write_text("{}\n")
        self.assert_rejected("generated content lock")

    def test_rejects_undeclared_or_cross_root_runtime_files(self) -> None:
        root = self.repository / "plugins/mergecraft"
        (root / "skills/graphite/extra.py").write_text("pass\n")
        self.assert_rejected("runtime root inventory drift")

        (root / "skills/graphite/extra.py").unlink()
        topology = root / "topology.json"
        content = topology.read_text().replace(
            '"skills/graphite/agents/openai.yaml"',
            '"../versionkeeping/skills/graphite/agents/openai.yaml"',
            1,
        )
        topology.write_text(content)
        self.assert_rejected("invalid runtime declaration")

    def test_rejects_omitted_graphite_helper_declaration(self) -> None:
        topology = self.repository / "plugins/mergecraft/topology.json"
        content = json.loads(topology.read_text())
        graphite = next(
            skill for skill in content["skills"] if skill["name"] == "graphite"
        )
        graphite["scripts"] = []
        topology.write_text(json.dumps(content, indent=2) + "\n")
        self.assert_rejected("runtime root inventory drift")

    def test_rejects_degenerate_declaration_without_crashing(self) -> None:
        topology = self.repository / "plugins/versionkeeping/topology.json"
        content = topology.read_text().replace(
            '"skills/checkpointing-and-publishing-git-work/SKILL.md"',
            '"."',
            1,
        )
        topology.write_text(content)
        self.assert_rejected("invalid runtime declaration")

    def test_rejects_unknown_component_metadata(self) -> None:
        topology = self.repository / "plugins/mergecraft/topology.json"
        content = topology.read_text().replace(
            '      "name": "writing-reviewable-pr-descriptions",',
            '      "name": "writing-reviewable-pr-descriptions",\n'
            '      "unexpected": "metadata",',
            1,
        )
        topology.write_text(content)
        self.assert_rejected("runtime topology component shape drift")

    def test_rejects_declared_but_unreachable_reference(self) -> None:
        root = self.repository / "plugins/mergecraft"
        relative = (
            "skills/interacting-with-pr-review-feedback/references/"
            "declared-but-unreachable.md"
        )
        (root / relative).write_text("# Unreachable\n")
        topology_path = root / "topology.json"
        topology = json.loads(topology_path.read_text())
        component = next(
            item
            for item in topology["skills"]
            if item["name"] == "interacting-with-pr-review-feedback"
        )
        component["references"].append(relative)
        topology_path.write_text(json.dumps(topology, indent=2) + "\n")

        self.assert_rejected("declared runtime dependency is unreachable")

    def test_rejects_ambiguous_local_python_import(self) -> None:
        root = self.repository / "plugins/mergecraft"
        relative = "skills/graphite/scripts/reviewable_pr_state.py"
        (root / relative).write_text("# Conflicting local module\n")
        topology_path = root / "topology.json"
        topology = json.loads(topology_path.read_text())
        component = next(
            item for item in topology["skills"] if item["name"] == "graphite"
        )
        component["modules"].append(relative)
        topology_path.write_text(json.dumps(topology, indent=2) + "\n")

        self.assert_rejected("ambiguous local Python import")

    def test_repository_release_evidence_is_not_a_runtime_dependency(self) -> None:
        release = self.repository / "release/mergecraft"
        release.mkdir(parents=True)
        (release / "repository-evidence.json").write_text("{}\n")

        result = self.validate()

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_symlinked_plugins_ancestor(self) -> None:
        plugins = self.repository / "plugins"
        target = self.repository / "plugins-real"
        plugins.rename(target)
        plugins.symlink_to(target, target_is_directory=True)
        self.assert_rejected("plugins root must be a real directory")

    def test_rejects_symlinks_and_test_only_files(self) -> None:
        root = self.repository / "plugins/mergecraft/skills/graphite"
        (root / "linked.md").symlink_to("SKILL.md")
        self.assert_rejected("symlink")
        (root / "linked.md").unlink()
        (root / "fixtures").mkdir()
        (root / "fixtures/example.json").write_text("{}\n")
        self.assert_rejected("test-only content")


if __name__ == "__main__":
    unittest.main()
