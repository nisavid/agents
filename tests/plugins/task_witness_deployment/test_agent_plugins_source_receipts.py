from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.agent_plugins_standard import (
    AGENT_PLUGINS_V1_SCHEMA,
    load_agent_plugin_manifest,
)
from tests.plugins.task_witness_client._support import load_client_module

from ._routine_support import RoutineDeploymentFixture
from ._support import sha256

TEST_ONLY_RELEASE_VERSION = "test-only-agent-plugins-v1-source"


class AgentPluginsSourceReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.fixture = RoutineDeploymentFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _agent_plugins_candidate(self) -> Path:
        source = Path(__file__).resolve().parents[3] / "plugins" / "task-witness"
        candidate = self.root / "agent-plugins-candidate"
        shutil.copytree(source, candidate)

        canonical = {
            "$schema": AGENT_PLUGINS_V1_SCHEMA,
            "name": "task-witness",
            "version": TEST_ONLY_RELEASE_VERSION,
            "description": (
                "Task Witness launches exact-byte-pinned registered validators "
                "against retained operator trust."
            ),
            "author": {
                "name": "Ivan D Vasin",
                "url": "https://github.com/nisavid",
            },
            "homepage": (
                "https://github.com/nisavid/agents/tree/main/plugins/task-witness"
            ),
            "repository": "https://github.com/nisavid/agents",
            "license": "MIT",
            "keywords": ["evidence", "provenance", "validation", "trust"],
            "extensions": {
                "com.openai": {
                    "interface": {
                        "displayName": "Task Witness",
                        "shortDescription": (
                            "Validate registered task-evidence bundles."
                        ),
                        "longDescription": (
                            "Task Witness runs exact-byte-pinned, "
                            "operator-approved validators. Validators are "
                            "trusted full-process Python code with ambient user "
                            "authority; Task Witness grants no workflow authority "
                            "and is not a sandbox."
                        ),
                        "developerName": "Ivan D Vasin",
                        "category": "Developer Tools",
                        "capabilities": ["Validation"],
                        "websiteURL": (
                            "https://github.com/nisavid/agents/tree/main/"
                            "plugins/task-witness"
                        ),
                    }
                }
            },
        }
        claude = {
            "name": canonical["name"],
            "displayName": canonical["extensions"]["com.openai"]["interface"][
                "displayName"
            ],
            **{
                key: value
                for key, value in canonical.items()
                if key not in {"$schema", "name", "extensions"}
            },
        }
        (candidate / "plugin.json").write_text(
            json.dumps(canonical, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (candidate / ".claude-plugin" / "plugin.json").write_text(
            json.dumps(claude, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        codex_manifest_root = candidate / ".codex-plugin"
        if codex_manifest_root.exists():
            shutil.rmtree(codex_manifest_root)
        return candidate

    def _first_install_request(self, candidate: Path) -> object:
        deployment = self.fixture.deployment()
        canonical_root = (
            self.root / "account-home" / ".local" / "libexec" / "task-witness"
        )
        canonical_root.mkdir(parents=True, mode=0o700)
        for directory in (
            self.root / "account-home",
            self.root / "account-home" / ".local",
            self.root / "account-home" / ".local" / "libexec",
            canonical_root,
        ):
            directory.chmod(0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)

        routine = self.fixture.request_for_candidate(
            canonical_root,
            "0" * 64,
            candidate,
            release_version=TEST_ONLY_RELEASE_VERSION,
            revision="e" * 40,
            sequence=37,
        )
        return deployment.FirstInstallRequest(
            candidate_root=routine.candidate_root,
            canonical_root=routine.canonical_root,
            source_selection_raw=routine.source_selection_raw,
            source_evidence=routine.source_evidence,
            runtime_qualification_raw=routine.runtime_qualification_raw,
            maintenance_transaction_sha256="9" * 64,
        )

    def test_public_first_install_accepts_agent_plugins_source_without_codex_projection(
        self,
    ) -> None:
        deployment = self.fixture.deployment()
        candidate = self._agent_plugins_candidate()
        canonical = load_agent_plugin_manifest(candidate)
        claude = json.loads(
            (candidate / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        expected_claude = {
            "name": canonical["name"],
            "displayName": canonical["extensions"]["com.openai"]["interface"][
                "displayName"
            ],
            **{
                key: value
                for key, value in canonical.items()
                if key not in {"$schema", "name", "extensions"}
            },
        }
        self.assertEqual(claude, expected_claude)
        self.assertFalse((candidate / ".codex-plugin").exists())

        request = self._first_install_request(candidate)
        prepared = deployment.prepare_first_install(request)

        self.assertEqual(prepared.plan.source.plugin_id, "task-witness")
        self.assertEqual(
            prepared.plan.source.release_version,
            TEST_ONLY_RELEASE_VERSION,
        )
        self.assertEqual(
            prepared.plan.source.agent_plugin_manifest_sha256,
            sha256((candidate / "plugin.json").read_bytes()),
        )
        self.assertEqual(
            prepared.plan.source.claude_manifest_sha256,
            sha256((candidate / ".claude-plugin" / "plugin.json").read_bytes()),
        )
        self.assertNotIn(
            "codex_manifest_sha256",
            prepared.plan.source.__dataclass_fields__,
        )

        staged = deployment.stage_first_install(
            request,
            self.fixture.first_install.first_install_authorization_raw(prepared),
            self.root / "agent-plugins-stage",
        )
        receipt = json.loads(staged.deployment_raw)
        source = receipt["source"]
        self.assertEqual(receipt["schema_version"], 2)
        self.assertEqual(
            receipt["contract"],
            "task-witness-deployment-receipt-v2",
        )
        self.assertEqual(
            receipt["contracts"]["deployment_receipt"],
            "task-witness-deployment-receipt-v2",
        )
        self.assertEqual(
            source["agent_plugin_manifest_sha256"],
            sha256((candidate / "plugin.json").read_bytes()),
        )
        self.assertEqual(
            source["claude_manifest_sha256"],
            sha256((candidate / ".claude-plugin" / "plugin.json").read_bytes()),
        )
        self.assertNotIn("codex_manifest_sha256", source)

        client = load_client_module("task_witness_client_agent_plugins_receipt")
        self.assertEqual(
            client.DEPLOYMENT_RECEIPT_CONTRACT,
            "task-witness-deployment-receipt-v2",
        )
        self.assertEqual(client._validate_receipt_source(source), source)

    def test_public_first_install_rejects_claude_projection_drift(self) -> None:
        deployment = self.fixture.deployment()
        candidate = self._agent_plugins_candidate()
        path = candidate / ".claude-plugin" / "plugin.json"
        claude = json.loads(path.read_text(encoding="utf-8"))
        claude["description"] += " Drifted."
        path.write_text(
            json.dumps(claude, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "Claude manifest projection disagrees",
        ):
            deployment.prepare_first_install(self._first_install_request(candidate))

    def test_public_first_install_rejects_legacy_codex_projection(self) -> None:
        deployment = self.fixture.deployment()
        candidate = self._agent_plugins_candidate()
        codex = candidate / ".codex-plugin" / "plugin.json"
        codex.parent.mkdir()
        codex.write_text("{}\n", encoding="utf-8")

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "legacy Codex manifest must be absent",
        ):
            deployment.prepare_first_install(self._first_install_request(candidate))


if __name__ == "__main__":
    unittest.main()
