from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.agent_plugins_standard import (
    AGENT_PLUGINS_V1_SCHEMA,
    AgentPluginContractError,
    discover_direct_skills,
    load_agent_plugin_manifest,
    validate_skill_resource_links,
)


class AgentPluginsStandardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name).resolve() / "plugin"
        self.root.mkdir()
        self.manifest = {
            "$schema": AGENT_PLUGINS_V1_SCHEMA,
            "name": "example-plugin",
            "version": "1.0.0",
            "description": "Example plugin.",
            "author": {
                "name": "Example Author",
                "email": "author@example.com",
                "url": "https://example.com/author",
            },
            "homepage": "https://example.com/plugin",
            "repository": "https://example.com/repository",
            "license": "MIT",
            "keywords": ["example", "agents"],
            "extensions": {"com.example": {"enabled": True}},
        }
        self.write_manifest(self.manifest)
        skill = self.root / "skills" / "example-skill"
        (skill / "references").mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: example-skill\ndescription: Use when testing.\n---\n\n"
            "Read [the reference](references/example.md) and "
            "[the specification](https://agent-plugins.org/specification).\n"
        )
        (skill / "references" / "example.md").write_text("# Example\n")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_manifest(self, manifest: dict) -> None:
        (self.root / "plugin.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

    def test_accepts_every_agent_plugins_v1_manifest_field(self) -> None:
        self.assertEqual(load_agent_plugin_manifest(self.root), self.manifest)

    def test_rejects_every_agent_plugins_v1_schema_boundary(self) -> None:
        cases = (
            ("required $schema", lambda value: value.pop("$schema")),
            ("required name", lambda value: value.pop("name")),
            ("unknown field", lambda value: value.update({"skills": "./skills/"})),
            (
                "schema identifier",
                lambda value: value.update(
                    {
                        "$schema": "https://agent-plugins.org/schemas/2.0.0/plugin.schema.json"
                    }
                ),
            ),
            ("name pattern", lambda value: value.update({"name": "example--plugin"})),
            ("name length", lambda value: value.update({"name": "x" * 65})),
            ("string field", lambda value: value.update({"version": 1})),
            ("author object", lambda value: value.update({"author": []})),
            (
                "author field",
                lambda value: value["author"].update({"handle": "example"}),
            ),
            ("author value", lambda value: value["author"].update({"name": 1})),
            ("keywords array", lambda value: value.update({"keywords": "example"})),
            ("keyword value", lambda value: value.update({"keywords": [1]})),
            ("extensions object", lambda value: value.update({"extensions": []})),
            (
                "extension value",
                lambda value: value.update({"extensions": {"com.example": []}}),
            ),
        )
        for label, mutate in cases:
            with self.subTest(label=label):
                manifest = copy.deepcopy(self.manifest)
                mutate(manifest)
                self.write_manifest(manifest)
                with self.assertRaisesRegex(
                    AgentPluginContractError, "Agent Plugins v1 manifest"
                ):
                    load_agent_plugin_manifest(self.root)

    def test_rejects_duplicate_and_non_finite_manifest_values(self) -> None:
        path = self.root / "plugin.json"
        path.write_text(
            path.read_text().replace(
                '  "name": "example-plugin",',
                '  "name": "example-plugin",\n  "name": "shadow",',
                1,
            )
        )
        with self.assertRaisesRegex(AgentPluginContractError, "duplicate key: name"):
            load_agent_plugin_manifest(self.root)

        path.write_text(json.dumps(self.manifest).replace('"1.0.0"', "NaN", 1))
        with self.assertRaisesRegex(AgentPluginContractError, "non-finite JSON value"):
            load_agent_plugin_manifest(self.root)

    def test_discovers_only_direct_child_agent_skills(self) -> None:
        nested = self.root / "skills" / "example-skill" / "references" / "nested"
        nested.mkdir()
        (nested / "SKILL.md").write_text(
            "---\nname: nested\ndescription: Use when nested.\n---\n"
        )
        self.assertEqual(discover_direct_skills(self.root), ("example-skill",))

        second = self.root / "skills" / "second-skill"
        second.mkdir()
        (second / "SKILL.md").write_text(
            "---\nname: second-skill\ndescription: Use when second.\n---\n"
        )
        self.assertEqual(
            discover_direct_skills(self.root), ("example-skill", "second-skill")
        )

    def test_skill_resources_must_resolve_inside_package(self) -> None:
        validate_skill_resource_links(self.root, ("example-skill",))

        outside = self.root.parent / "outside.md"
        outside.write_text("outside\n")
        skill = self.root / "skills" / "example-skill" / "SKILL.md"
        skill.write_text(
            skill.read_text().replace("references/example.md", "../../../outside.md", 1)
        )
        with self.assertRaisesRegex(
            AgentPluginContractError, "Agent Skill resource escapes plugin root"
        ):
            validate_skill_resource_links(self.root, ("example-skill",))


if __name__ == "__main__":
    unittest.main()
