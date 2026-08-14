from __future__ import annotations

import json
import platform
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from unittest import mock

from ._support import (
    PLUGIN,
    ProviderFixture,
    canonical_bytes,
    canonical_document,
    content_document,
    copy_agent_plugins_candidate,
    load_deployment_module,
    sha256,
    validator_identity,
    write_agent_plugins_manifest,
)


class ReceiptStagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.template = (PLUGIN / "client" / "task_witness_shim.sh.in").read_bytes()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def deployment(self):
        return load_deployment_module()

    def tree_state(self, root: Path) -> tuple[tuple[str, str, int, object], ...]:
        state = []
        for path in sorted(root.rglob("*")):
            relative_path = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            if path.is_symlink():
                kind = "symlink"
                payload: object = str(path.readlink())
            elif path.is_dir():
                kind = "directory"
                payload = None
            else:
                kind = "file"
                raw = path.read_bytes()
                payload = (len(raw), sha256(raw))
            state.append((relative_path, kind, mode, payload))
        return tuple(state)

    def readdress_staged_deployment_receipt(
        self,
        staged,
        deployment_receipt: dict,
        *,
        changed_artifacts: tuple = (),
    ) -> None:
        stage = json.loads(staged.stage_path.read_bytes())
        canonical_root = Path(stage["canonical_root"])
        deployment_receipt.pop("content_sha256", None)
        deployment_receipt = content_document(deployment_receipt)
        deployment_raw = canonical_document(deployment_receipt)
        deployment_sha256 = sha256(deployment_raw)
        receipt_relative_path = f"receipts/sha256-{deployment_sha256}.json"
        receipt_staged_path = staged.stage_path.parent / receipt_relative_path
        receipt_installed_path = canonical_root / receipt_relative_path
        old_receipt = next(
            item for item in staged.artifacts if item.role == "deployment-receipt"
        )
        old_receipt.staged_path.rename(receipt_staged_path)
        receipt_staged_path.write_bytes(deployment_raw)
        receipt_staged_path.chmod(0o600)
        alias = next(
            item for item in staged.artifacts if item.role == "deployment-alias"
        )
        alias.staged_path.write_bytes(deployment_raw)
        alias.staged_path.chmod(0o600)
        binding = {
            "length": len(deployment_raw),
            "sha256": deployment_sha256,
            "owner": old_receipt.staged["owner"],
            "mode": 0o600,
        }
        changed_by_role = {item.role: item for item in changed_artifacts}
        for artifact in stage["artifacts"]:
            if artifact["role"] == "deployment-receipt":
                artifact["relative_path"] = receipt_relative_path
                artifact["staged"] = {
                    "path": str(receipt_staged_path),
                    **binding,
                }
                artifact["installed"] = {
                    "path": str(receipt_installed_path),
                    **binding,
                }
            elif artifact["role"] == "deployment-alias":
                artifact["staged"] = {
                    "path": str(alias.staged_path),
                    **binding,
                }
                artifact["installed"] = {
                    "path": str(alias.installed_path),
                    **binding,
                }
            elif artifact["role"] in changed_by_role:
                changed = changed_by_role[artifact["role"]]
                changed_raw = changed.staged_path.read_bytes()
                changed_binding = {
                    "length": len(changed_raw),
                    "sha256": sha256(changed_raw),
                    "owner": changed.staged["owner"],
                    "mode": 0o600,
                }
                artifact["staged"] = {
                    "path": str(changed.staged_path),
                    **changed_binding,
                }
                artifact["installed"] = {
                    "path": str(changed.installed_path),
                    **changed_binding,
                }
        stage["artifacts"].sort(key=lambda item: item["relative_path"])
        stage["deployment_receipt"] = {
            "path": str(receipt_installed_path),
            "sha256": deployment_sha256,
        }
        stage.pop("content_sha256")
        staged.stage_path.write_bytes(canonical_document(content_document(stage)))
        staged.stage_path.chmod(0o600)

    def source_selection(self, mode: str) -> dict:
        details = {
            "harness_snapshot": {
                "harness": "codex",
                "manager": "codex-plugin-manager",
                "channel": "stable",
                "manager_trust_class": "operator-installed",
                "manager_receipt_sha256": sha256(b"untouched manager receipt"),
                "lineage": {"lineage_id": "agents-stable", "sequence": 7},
            },
            "publisher_channel": {
                "channel": "stable",
                "source_trust_class": "publisher-controlled",
                "lineage": {"lineage_id": "agents-stable", "sequence": 7},
            },
            "exact_release": {
                "source_trust_class": "operator-pinned",
            },
        }[mode]
        return content_document(
            {
                "schema_version": 1,
                "contract": "task-witness-source-selection-v1",
                "mode": mode,
                "publisher_id": "nisavid",
                "manifest_author": {
                    "name": "Ivan D Vasin",
                    "url": "https://github.com/nisavid",
                },
                "repository_id": "nisavid/agents",
                "repository_url": "https://github.com/nisavid/agents",
                "release_version": "0.1.0",
                "revision": "a" * 40,
                "subtree_sha256": "b" * 64,
                "source_authority": "github-nisavid-agents",
                "details": details,
            }
        )

    def write_manifest_pair(
        self,
        provider: ProviderFixture,
        *,
        version: str = "0.1.0",
    ) -> tuple[Path, Path]:
        author = {
            "name": "Demo Publisher",
            "url": f"https://github.com/{provider.value['publisher']}",
        }
        common = {
            "name": provider.value["plugin_id"],
            "version": version,
            "description": "Demonstration provider.",
            "author": author,
            "homepage": f"{provider.value['repository']}/tree/main/plugin",
            "repository": provider.value["repository"],
            "license": "MIT",
            "keywords": ["demo", "validation"],
        }
        agent_plugin = {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            **common,
            "extensions": {
                "com.openai": {
                    "interface": {
                        "displayName": "Demo Provider",
                        "shortDescription": "Validate demo bundles.",
                        "longDescription": "Validate exact demo provider bundles.",
                        "developerName": author["name"],
                        "category": "Developer Tools",
                        "capabilities": ["Validation"],
                        "websiteURL": common["homepage"],
                        "defaultPrompt": ["Use the demo validator."],
                    }
                },
            },
        }
        write_agent_plugins_manifest(provider.root, agent_plugin)
        agent_plugin_path = provider.root / "plugin.json"
        claude_path = provider.root / ".claude-plugin" / "plugin.json"
        return agent_plugin_path, claude_path

    def harness_source_documents(
        self,
        provider: ProviderFixture,
        subtree_sha256: str,
        manager_receipt: bytes,
        *,
        version: str = "0.1.0",
    ) -> tuple[bytes, bytes]:
        lineage = {"lineage_id": "agents-stable", "sequence": 7}
        shared = {
            "plugin_id": provider.value["plugin_id"],
            "release_version": version,
            "revision": "a" * 40,
            "subtree_sha256": subtree_sha256,
            "channel": "stable",
            "manager_trust_class": "operator-installed",
            "source_authority": "github-demo-provider",
            "lineage": lineage,
        }
        selection = content_document(
            {
                "schema_version": 1,
                "contract": "task-witness-source-selection-v1",
                "mode": "harness_snapshot",
                "publisher_id": provider.value["publisher"],
                "manifest_author": {
                    "name": "Demo Publisher",
                    "url": f"https://github.com/{provider.value['publisher']}",
                },
                "repository_id": "demo/provider",
                "repository_url": provider.value["repository"],
                "release_version": version,
                "revision": shared["revision"],
                "subtree_sha256": subtree_sha256,
                "source_authority": shared["source_authority"],
                "details": {
                    "harness": "codex",
                    "manager": "codex-plugin-manager",
                    "channel": shared["channel"],
                    "manager_trust_class": shared["manager_trust_class"],
                    "manager_receipt_sha256": sha256(manager_receipt),
                    "lineage": lineage,
                },
            }
        )
        binding = content_document(
            {
                "schema_version": 1,
                "contract": "task-witness-manager-binding-v1",
                "harness": "codex",
                "manager": "codex-plugin-manager",
                "adapter_sha256": sha256(b"exact private harness adapter"),
                "manager_receipt_sha256": sha256(manager_receipt),
                "claims": shared,
            }
        )
        return canonical_document(selection), canonical_document(binding)

    def task_witness_candidate_inputs(
        self,
        candidate_root: Path | None = None,
        *,
        revision: str = "a" * 40,
    ) -> tuple[bytes, bytes, bytes]:
        if candidate_root is None or candidate_root == PLUGIN:
            candidate_root = self.root / "task-witness-agent-plugins-candidate"
            if not candidate_root.exists():
                copy_agent_plugins_candidate(PLUGIN, candidate_root)
        manifest_path = candidate_root / "plugin.json"
        if not manifest_path.is_file():
            manifest_path = candidate_root / ".claude-plugin" / "plugin.json"
        release_version = json.loads(manifest_path.read_text(encoding="utf-8"))[
            "version"
        ]
        if not isinstance(release_version, str) or not release_version:
            raise AssertionError("Task Witness fixture release version is invalid")
        deployment = self.deployment()
        snapshot = deployment._snapshot_candidate_tree(candidate_root)
        receipt = b"opaque Task Witness manager receipt\n"
        lineage = {"lineage_id": "agents-stable", "sequence": 7}
        selection_raw = canonical_document(
            content_document(
                {
                    "schema_version": 1,
                    "contract": "task-witness-source-selection-v1",
                    "mode": "harness_snapshot",
                    "publisher_id": "nisavid",
                    "manifest_author": {
                        "name": "Ivan D Vasin",
                        "url": "https://github.com/nisavid",
                    },
                    "repository_id": "nisavid/agents",
                    "repository_url": "https://github.com/nisavid/agents",
                    "release_version": release_version,
                    "revision": revision,
                    "subtree_sha256": snapshot.subtree_sha256,
                    "source_authority": "github-nisavid-agents",
                    "details": {
                        "harness": "codex",
                        "manager": "codex-plugin-manager",
                        "channel": "stable",
                        "manager_trust_class": "operator-installed",
                        "manager_receipt_sha256": sha256(receipt),
                        "lineage": lineage,
                    },
                }
            )
        )
        binding_raw = canonical_document(
            content_document(
                {
                    "schema_version": 1,
                    "contract": "task-witness-manager-binding-v1",
                    "harness": "codex",
                    "manager": "codex-plugin-manager",
                    "adapter_sha256": sha256(b"exact private harness adapter"),
                    "manager_receipt_sha256": sha256(receipt),
                    "claims": {
                        "plugin_id": "task-witness",
                        "release_version": release_version,
                        "revision": revision,
                        "subtree_sha256": snapshot.subtree_sha256,
                        "channel": "stable",
                        "manager_trust_class": "operator-installed",
                        "source_authority": "github-nisavid-agents",
                        "lineage": lineage,
                    },
                }
            )
        )
        return selection_raw, binding_raw, receipt

    def task_witness_candidate_source(self, candidate_root: Path | None = None):
        deployment = self.deployment()
        selection_raw, binding_raw, receipt = self.task_witness_candidate_inputs(
            candidate_root
        )
        if candidate_root is None or candidate_root == PLUGIN:
            candidate_root = self.root / "task-witness-agent-plugins-candidate"
        return deployment._bind_candidate_source(
            deployment._snapshot_candidate_tree(candidate_root),
            selection_raw,
            binding_raw,
            receipt,
        )

    def first_install_request(
        self,
        canonical_root: Path,
        *,
        candidate_root: Path | None = None,
    ):
        deployment = self.deployment()
        selection_raw, binding_raw, receipt = self.task_witness_candidate_inputs(
            candidate_root
        )
        if candidate_root is None or candidate_root == PLUGIN:
            candidate_root = self.root / "task-witness-agent-plugins-candidate"
        return deployment.FirstInstallRequest(
            candidate_root=candidate_root,
            canonical_root=canonical_root,
            source_selection_raw=selection_raw,
            source_evidence=deployment.HarnessSnapshotEvidence(
                binding_raw=binding_raw,
                receipt_raw=receipt,
            ),
            runtime_qualification_raw=self.runtime_qualification_raw(),
            maintenance_transaction_sha256="9" * 64,
        )

    def first_install_authorization_raw(self, prepared) -> bytes:
        facts = prepared.authorization_facts
        return canonical_document(
            content_document(
                {
                    "schema_version": 1,
                    "contract": "task-witness-deployer-authorization-v1",
                    "purpose": "first-install",
                    "canonical_root": str(facts.canonical_root),
                    "effective_uid": facts.effective_uid,
                    "plan_sha256": facts.plan_sha256,
                    "maintenance_transaction_sha256": (
                        facts.maintenance_transaction_sha256
                    ),
                    "candidate_controller_sha256": (facts.candidate_controller_sha256),
                    "candidate_policy_sha256": facts.candidate_policy_sha256,
                    "source_selection_sha256": facts.source_selection_sha256,
                    "source_evidence_sha256": facts.source_evidence_sha256,
                }
            )
        )

    def runtime_qualification_raw(self) -> bytes:
        executable = Path(sys.executable).resolve(strict=True)
        executable_raw = executable.read_bytes()
        return canonical_document(
            content_document(
                {
                    "schema_version": 1,
                    "contract": "task-witness-runtime-qualification-v1",
                    "platform": {
                        "system": platform.system().lower(),
                        "machine": platform.machine().lower(),
                        "qualified_filesystem_class": "local-private-filesystem",
                    },
                    "main_executable": {
                        "path": str(executable),
                        "length": len(executable_raw),
                        "sha256": sha256(executable_raw),
                        "implementation": "cpython",
                        "version": {
                            "major": sys.version_info.major,
                            "minor": sys.version_info.minor,
                            "micro": sys.version_info.micro,
                        },
                    },
                    "runtime_closure": {
                        "supplier": "homebrew",
                        "provenance": "locally-qualified-package",
                        "qualification_class": "two-platform-release-gate",
                        "evidence_sha256": "8" * 64,
                    },
                    "dependency_classes": [
                        "cpython-stdlib",
                        "dynamic-loader",
                        "system-libraries",
                    ],
                }
            )
        )

    def test_renderer_emits_exact_pinned_shim_bytes(self) -> None:
        interpreter = self.root / "runtime's python"
        client = self.root / "installed client" / "task_witness_client.py"

        rendered = self.deployment().render_pinned_shim(
            self.template,
            interpreter,
            client,
        )

        self.assertEqual(
            rendered,
            (
                "#!/bin/sh\n"
                "exec /usr/bin/env -i LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC "
                f"'{str(interpreter).replace(chr(39), chr(39) + chr(34) + chr(39) + chr(34) + chr(39))}' "
                "-B -I -S -X disable-remote-debug "
                f"'{client}' \"$@\"\n"
            ).encode(),
        )

    def test_renderer_rejects_ambiguous_or_uninstalled_inputs(self) -> None:
        deployment = self.deployment()
        interpreter = self.root / "python"
        client = self.root / "client.py"
        cases = {
            "missing-placeholder": self.template.replace(
                b"@TASK_WITNESS_CLIENT@", b"client"
            ),
            "duplicate-placeholder": self.template.replace(
                b"@TASK_WITNESS_CLIENT@",
                b"@TASK_WITNESS_CLIENT@@TASK_WITNESS_CLIENT@",
            ),
            "unknown-placeholder": self.template.replace(
                b'"$@"', b'@TASK_WITNESS_UNKNOWN@ "$@"'
            ),
            "missing-final-lf": self.template.rstrip(b"\n"),
        }
        for name, template in cases.items():
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(
                    deployment.DeploymentError,
                    "shim|placeholder|template",
                ),
            ):
                deployment.render_pinned_shim(template, interpreter, client)

        with self.assertRaisesRegex(deployment.DeploymentError, "absolute|normalized"):
            deployment.render_pinned_shim(
                self.template,
                Path("relative-python"),
                client,
            )

    def test_source_selection_accepts_only_one_closed_mode_shape(self) -> None:
        deployment = self.deployment()
        for mode in ("harness_snapshot", "publisher_channel", "exact_release"):
            with self.subTest(mode=mode):
                selection = deployment._parse_source_selection(
                    canonical_document(self.source_selection(mode))
                )

                self.assertEqual(selection.mode, mode)
                self.assertEqual(selection.publisher_id, "nisavid")
                self.assertEqual(
                    selection.repository_url,
                    "https://github.com/nisavid/agents",
                )
                self.assertEqual(selection.revision, "a" * 40)

    def test_source_selection_rejects_cross_mode_or_noncanonical_fields(self) -> None:
        deployment = self.deployment()
        cases = {}
        for mode in ("harness_snapshot", "publisher_channel", "exact_release"):
            extra = self.source_selection(mode)
            extra.pop("content_sha256")
            extra["details"]["unexpected"] = "not-owned-by-this-mode"
            cases[f"{mode}-extra"] = canonical_document(content_document(extra))
        wrong_digest = self.source_selection("exact_release")
        wrong_digest["content_sha256"] = "0" * 64
        cases["wrong-content-digest"] = canonical_document(wrong_digest)
        cases["pretty-json"] = canonical_document(
            self.source_selection("exact_release")
        ).replace(b"{", b"{ ", 1)

        for name, raw in cases.items():
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(
                    deployment.DeploymentError,
                    "source selection|schema|canonical|digest|mode",
                ),
            ):
                deployment._parse_source_selection(raw)

    def test_candidate_tree_digest_binds_sorted_inventory_and_exact_bytes(self) -> None:
        provider = ProviderFixture(self.root / "plugin")
        expected_entries = [
            {
                "path": "task-witness-provider.json",
                "kind": "file",
                "length": len(provider.provider_path.read_bytes()),
                "sha256": sha256(provider.provider_path.read_bytes()),
            },
            {"path": "validators", "kind": "directory"},
            {
                "path": "validators/helper.py",
                "kind": "file",
                "length": len(provider.helper.read_bytes()),
                "sha256": sha256(provider.helper.read_bytes()),
            },
            {
                "path": "validators/validator.py",
                "kind": "file",
                "length": len(provider.entrypoint.read_bytes()),
                "sha256": sha256(provider.entrypoint.read_bytes()),
            },
        ]

        snapshot = self.deployment()._snapshot_candidate_tree(provider.root)

        self.assertEqual(list(snapshot.entries), expected_entries)
        self.assertEqual(
            snapshot.subtree_sha256,
            sha256(
                canonical_bytes(
                    {
                        "contract": "task-witness-plugin-subtree-v1",
                        "entries": expected_entries,
                    }
                )
            ),
        )
        self.assertEqual(
            snapshot.files["task-witness-provider.json"],
            provider.provider_path.read_bytes(),
        )

    def test_candidate_tree_rejects_symlink_or_special_inventory(self) -> None:
        provider = ProviderFixture(self.root / "plugin")
        (provider.root / "alias.py").symlink_to(provider.entrypoint)

        with self.assertRaisesRegex(
            self.deployment().DeploymentError,
            "candidate tree|symlink|special",
        ):
            self.deployment()._snapshot_candidate_tree(provider.root)

    def test_agent_plugin_manifest_and_claude_projection_are_strict_json(self) -> None:
        provider = ProviderFixture(self.root / "plugin")
        agent_plugin_path, claude_path = self.write_manifest_pair(provider)
        deployment = self.deployment()

        agent_plugin, claude = deployment._parse_agent_plugin_manifests(
            agent_plugin_path.read_bytes(),
            claude_path.read_bytes(),
        )

        self.assertEqual(agent_plugin.name, provider.value["plugin_id"])
        self.assertEqual(claude.version, "0.1.0")
        self.assertEqual(agent_plugin.author, claude.author)
        malformed = agent_plugin_path.read_bytes().replace(
            b'"name": "demo-plugin",',
            b'"name": "demo-plugin",\n  "name": "demo-plugin",',
        )
        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "manifest|duplicate|schema",
        ):
            deployment._parse_agent_plugin_manifests(
                malformed,
                claude_path.read_bytes(),
            )

    def test_manager_binding_binds_the_untouched_receipt_bytes(self) -> None:
        provider = ProviderFixture(self.root / "plugin")
        self.write_manifest_pair(provider)
        snapshot = self.deployment()._snapshot_candidate_tree(provider.root)
        receipt = b"opaque manager receipt\n"
        _, binding_raw = self.harness_source_documents(
            provider,
            snapshot.subtree_sha256,
            receipt,
        )

        binding = self.deployment()._parse_manager_binding(binding_raw, receipt)

        self.assertEqual(binding.claims["plugin_id"], provider.value["plugin_id"])
        self.assertEqual(binding.manager_receipt_sha256, sha256(receipt))
        with self.assertRaisesRegex(
            self.deployment().DeploymentError,
            "manager receipt|digest",
        ):
            self.deployment()._parse_manager_binding(
                binding_raw,
                receipt + b"changed",
            )

    def test_candidate_source_cross_binds_every_shared_authority_field(self) -> None:
        provider = ProviderFixture(self.root / "plugin")
        self.write_manifest_pair(provider)
        deployment = self.deployment()
        snapshot = deployment._snapshot_candidate_tree(provider.root)
        receipt = b"opaque manager receipt\n"
        selection_raw, binding_raw = self.harness_source_documents(
            provider,
            snapshot.subtree_sha256,
            receipt,
        )

        source = deployment._bind_candidate_source(
            snapshot,
            selection_raw,
            binding_raw,
            receipt,
        )

        self.assertEqual(source.plugin_id, provider.value["plugin_id"])
        self.assertEqual(source.publisher_id, provider.value["publisher"])
        self.assertEqual(source.release_version, "0.1.0")
        self.assertEqual(source.subtree_sha256, snapshot.subtree_sha256)
        self.assertEqual(source.source_record_sha256, sha256(receipt))

        changed = json.loads(selection_raw)
        changed["subtree_sha256"] = "b" * 64
        changed.pop("content_sha256")
        changed = canonical_document(content_document(changed))
        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "source|subtree|cross-binding|disagree",
        ):
            deployment._bind_candidate_source(
                snapshot,
                changed,
                binding_raw,
                receipt,
            )

    def test_candidate_source_without_declaration_registers_no_provider(self) -> None:
        source = self.task_witness_candidate_source()

        self.assertEqual(source.plugin_id, "task-witness")
        self.assertIsNone(source.provider)
        self.assertIsNone(source.authority_profile)

    def test_provider_receipt_projection_binds_exact_role_ownership(self) -> None:
        deployment = self.deployment()
        trust_root = self.root / "trust"
        provider_fixture = ProviderFixture(self.root / "plugin")
        external = deployment.materialize_provider(
            provider_fixture.root,
            trust_root,
        )
        if external is None:
            self.fail("external provider fixture was not materialized")
        intrinsic = deployment.materialize_intrinsic_smoke_provider(trust_root)

        def thaw(value):
            if isinstance(value, Mapping):
                return {key: thaw(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [thaw(item) for item in value]
            return value

        def expected_projection(provider, *, intrinsic: bool) -> dict:
            return {
                "plugin_id": provider.plugin_id,
                "publisher": provider.publisher,
                "repository": provider.repository,
                "authority_profile": provider.authority_profile,
                "intrinsic": intrinsic,
                "declaration_sha256": provider.declaration_sha256,
                "declaration_content_sha256": (provider.declaration_content_sha256),
                "producers": thaw(provider.producers),
                "issuers": thaw(provider.issuers),
                "validators": thaw(provider.validators),
                "retained_modules": [
                    {
                        "name": module.name,
                        "path": str(module.path),
                        "length": len(module.raw),
                        "sha256": module.sha256,
                    }
                    for module in sorted(
                        provider.modules,
                        key=lambda item: str(item.path),
                    )
                ],
            }

        external_projection = deployment._provider_receipt_projection(
            external,
            intrinsic=False,
        )
        intrinsic_projection = deployment._provider_receipt_projection(
            intrinsic,
            intrinsic=True,
        )

        self.assertEqual(
            external_projection,
            expected_projection(external, intrinsic=False),
        )
        self.assertEqual(
            intrinsic_projection,
            expected_projection(intrinsic, intrinsic=True),
        )

        smoke_module_raw = (
            PLUGIN / "smoke" / "task_witness_smoke_validator.py"
        ).read_bytes()
        smoke_module_sha256 = sha256(smoke_module_raw)
        smoke_implementation_sha256 = validator_identity(
            "task-witness-smoke-bundle-v1",
            "task-witness-smoke-validator",
            [("task-witness-smoke-validator", smoke_module_sha256)],
        )
        smoke_lifecycle = {
            "state": "active",
            "usable_for_new_publication": True,
        }
        self.assertEqual(
            {
                category: intrinsic_projection[category]
                for category in ("producers", "issuers", "validators")
            },
            {
                "producers": [
                    {
                        "producer_id": "task-witness-smoke-producer",
                        "contract": "task-witness-smoke-bundle-v1",
                        "implementation_sha256": sha256(
                            canonical_bytes(
                                {
                                    "contract": (
                                        "task-witness-smoke-producer-implementation-v1"
                                    ),
                                    "validator_implementation_sha256": (
                                        smoke_implementation_sha256
                                    ),
                                }
                            )
                        ),
                        "validator_id": "task-witness-smoke-validator",
                        "validator_contract": "task-witness-smoke-bundle-v1",
                        "validator_implementation_sha256": (
                            smoke_implementation_sha256
                        ),
                        **smoke_lifecycle,
                    }
                ],
                "issuers": [
                    {
                        "issuer_id": "task-witness-smoke-issuer",
                        "contract": "task-witness-smoke-issuer-v1",
                        "implementation_sha256": sha256(
                            canonical_bytes(
                                {
                                    "contract": (
                                        "task-witness-smoke-issuer-implementation-v1"
                                    )
                                }
                            )
                        ),
                        "capabilities": ["activation-smoke"],
                        **smoke_lifecycle,
                    }
                ],
                "validators": [
                    {
                        "validator_id": "task-witness-smoke-validator",
                        "contract": "task-witness-smoke-bundle-v1",
                        "implementation_sha256": smoke_implementation_sha256,
                        "entrypoint": "task-witness-smoke-validator",
                        "modules": [
                            {
                                "name": "task-witness-smoke-validator",
                                "path": str(
                                    trust_root
                                    / "validators"
                                    / f"sha256-{smoke_implementation_sha256}"
                                    / "task-witness-smoke-validator.py"
                                ),
                                "sha256": smoke_module_sha256,
                            }
                        ],
                        **smoke_lifecycle,
                    }
                ],
            },
        )

    def test_public_policy_is_canonical_and_covers_task_witness_source(self) -> None:
        deployment = self.deployment()
        raw = (PLUGIN / "controller" / "policy.json").read_bytes()
        policy = deployment._parse_compatibility_policy(raw)
        source = self.task_witness_candidate_source()

        self.assertEqual(policy.raw_sha256, sha256(raw))
        self.assertEqual(policy.providers, ())
        self.assertTrue(deployment._policy_covers_source(policy, source))

    def test_candidate_policy_cannot_self_authorize_first_install(self) -> None:
        deployment = self.deployment()
        raw = (PLUGIN / "controller" / "policy.json").read_bytes()
        source = self.task_witness_candidate_source()

        disposition = deployment._classify_candidate_source(
            active_source=None,
            active_policy=None,
            active_policy_sha256=None,
            candidate_source=source,
            candidate_policy_sha256=sha256(raw),
        )

        self.assertEqual(
            (disposition.outcome, disposition.reason),
            ("approval-required", "first-install"),
        )

    def test_active_policy_classification_has_closed_precedence(self) -> None:
        deployment = self.deployment()
        raw = (PLUGIN / "controller" / "policy.json").read_bytes()
        policy = deployment._parse_compatibility_policy(raw)
        active = self.task_witness_candidate_source()
        digest = sha256(raw)

        exact = deployment._classify_candidate_source(
            active_source=active,
            active_policy=policy,
            active_policy_sha256=digest,
            candidate_source=active,
            candidate_policy_sha256=digest,
        )
        self.assertEqual((exact.outcome, exact.reason), ("no-op", "exact-release"))

        forward = replace(
            active,
            release_version="1.0.1",
            revision="b" * 40,
            subtree_sha256="c" * 64,
            lineage={"lineage_id": "agents-stable", "sequence": 8},
            agent_plugin_manifest_sha256="c" * 64,
            claude_manifest_sha256="d" * 64,
        )
        compatible = deployment._classify_candidate_source(
            active_source=active,
            active_policy=policy,
            active_policy_sha256=digest,
            candidate_source=forward,
            candidate_policy_sha256=digest,
        )
        self.assertEqual(
            (compatible.outcome, compatible.reason),
            ("compatible-forward", "active-policy"),
        )

        future_policy = deployment._classify_candidate_source(
            active_source=active,
            active_policy=policy,
            active_policy_sha256=digest,
            candidate_source=forward,
            candidate_policy_sha256="f" * 64,
        )
        self.assertEqual(
            (future_policy.outcome, future_policy.reason),
            ("approval-required", "future-update-policy"),
        )
        boundary = deployment._classify_candidate_source(
            active_source=active,
            active_policy=policy,
            active_policy_sha256=digest,
            candidate_source=replace(forward, publisher_id="another-publisher"),
            candidate_policy_sha256=digest,
        )
        self.assertEqual(
            (boundary.outcome, boundary.reason),
            ("approval-required", "source-authority"),
        )
        reused = deployment._classify_candidate_source(
            active_source=active,
            active_policy=policy,
            active_policy_sha256=digest,
            candidate_source=replace(active, subtree_sha256="0" * 64),
            candidate_policy_sha256=digest,
        )
        self.assertEqual(
            (reused.outcome, reused.reason),
            ("integrity-rejected", "immutable-release-reused"),
        )

    def test_first_install_precondition_is_read_only_and_exact(self) -> None:
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        before = list(canonical_root.iterdir())

        precondition = self.deployment()._capture_first_install_precondition(
            canonical_root
        )

        self.assertEqual(list(canonical_root.iterdir()), before)
        self.assertEqual(precondition.canonical_root, canonical_root)
        self.assertEqual(precondition.activation_lock["path"], str(activation_lock))
        self.assertEqual(precondition.activation_lock["mode"], 0o600)
        self.assertTrue(precondition.deployment_receipt_absent)
        (canonical_root / "deployment.json").write_bytes(b"occupied\n")
        with self.assertRaisesRegex(
            self.deployment().DeploymentError,
            "first install|deployment|absent",
        ):
            self.deployment()._capture_first_install_precondition(canonical_root)

    def test_first_install_authorization_binds_every_external_fact(self) -> None:
        deployment = self.deployment()
        expected = {
            "canonical_root": str(self.root / "canonical"),
            "effective_uid": 501,
            "plan_sha256": "1" * 64,
            "maintenance_transaction_sha256": "2" * 64,
            "candidate_controller_sha256": "3" * 64,
            "candidate_policy_sha256": "4" * 64,
            "source_selection_sha256": "5" * 64,
            "source_evidence_sha256": "6" * 64,
        }
        raw = canonical_document(
            content_document(
                {
                    "schema_version": 1,
                    "contract": "task-witness-deployer-authorization-v1",
                    "purpose": "first-install",
                    **expected,
                }
            )
        )

        authorization = deployment._validate_first_install_authorization(
            raw,
            **expected,
        )

        self.assertEqual(authorization.plan_sha256, expected["plan_sha256"])
        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "authorization|policy|disagree",
        ):
            deployment._validate_first_install_authorization(
                raw,
                **{**expected, "candidate_policy_sha256": "7" * 64},
            )

    def test_runtime_qualification_and_active_record_bind_exact_payloads(self) -> None:
        deployment = self.deployment()
        qualification = deployment._parse_runtime_qualification(
            self.runtime_qualification_raw()
        )
        source = self.task_witness_candidate_source()

        active = deployment._build_active_runtime(source, qualification)

        self.assertEqual(active.value["contract"], "task-witness-launch-active-v1")
        self.assertEqual(active.value["public_release"]["repository"], "nisavid/agents")
        self.assertEqual(active.value["public_release"]["revision"], "a" * 40)
        self.assertEqual(active.value["generation"], active.generation)
        self.assertEqual(
            active.runtime_implementation_sha256,
            sha256(
                canonical_bytes(
                    {
                        "contract": "task-witness-runtime-artifact-manifest-v2",
                        "runtime_contract": "task-witness-runtime-v1",
                        "entrypoint_role": "entrypoint",
                        "payloads": [dict(item) for item in active.payloads],
                    }
                )
            ),
        )
        self.assertEqual(
            active.files["task_witness.py"],
            (PLUGIN / "runtime" / "task_witness.py").read_bytes(),
        )

    def test_absolute_capture_ignores_only_shared_ancestor_change_tokens(self) -> None:
        deployment = self.deployment()
        shared = self.root / "shared"
        parent = shared / "private"
        parent.mkdir(parents=True)
        target = parent / "selected"
        target.write_bytes(b"selected bytes")
        original_read = deployment._read_descriptor
        changed = False

        def change_shared_ancestor(descriptor: int, limit: int, label: str) -> bytes:
            nonlocal changed
            raw = original_read(descriptor, limit, label)
            if label == "selected executable" and not changed:
                (shared / "unrelated").write_bytes(b"noise")
                changed = True
            return raw

        with mock.patch.object(
            deployment,
            "_read_descriptor",
            side_effect=change_shared_ancestor,
        ):
            self.assertEqual(
                deployment._capture_absolute_regular(
                    target,
                    1024,
                    "selected executable",
                ),
                b"selected bytes",
            )

        (shared / "unrelated").unlink()
        changed = False

        def change_immediate_parent(descriptor: int, limit: int, label: str) -> bytes:
            nonlocal changed
            raw = original_read(descriptor, limit, label)
            if label == "selected executable" and not changed:
                (parent / "unrelated").write_bytes(b"noise")
                changed = True
            return raw

        with (
            mock.patch.object(
                deployment,
                "_read_descriptor",
                side_effect=change_immediate_parent,
            ),
            self.assertRaisesRegex(
                deployment.DeploymentError,
                "directory changed|mapping changed",
            ),
        ):
            deployment._capture_absolute_regular(
                target,
                1024,
                "selected executable",
            )

    def test_read_only_trust_plan_matches_two_root_materialization(self) -> None:
        deployment = self.deployment()
        source = self.task_witness_candidate_source()
        canonical_root = self.root / "canonical"
        staging_trust = self.root / "stage" / "trust"

        planned = deployment._plan_trust_context(source, canonical_root)

        self.assertFalse(staging_trust.exists())
        staging_trust.parent.mkdir(mode=0o700)
        deployment.materialize_intrinsic_smoke_provider(
            staging_trust,
            installed_trust_root=canonical_root / "trust",
        )
        materialized = deployment.materialize_trust_context(
            [],
            staging_trust,
            installed_trust_root=canonical_root / "trust",
        )
        self.assertEqual(materialized.raw, planned.context.raw)
        self.assertEqual(materialized.path, planned.context.path)
        self.assertNotIn(str(staging_trust).encode(), planned.context.raw)

    def test_first_install_plan_is_read_only_and_binds_all_nonreceipt_bytes(
        self,
    ) -> None:
        deployment = self.deployment()
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(canonical_root)
        before = list(canonical_root.iterdir())

        prepared = deployment.prepare_first_install(request)
        repeated = deployment.prepare_first_install(request)
        plan = prepared.plan

        self.assertEqual(plan.plan_sha256, repeated.plan.plan_sha256)
        self.assertEqual(prepared.authorization_facts.plan_sha256, plan.plan_sha256)
        self.assertEqual(
            prepared.authorization_facts.candidate_controller_sha256,
            next(item.sha256 for item in plan.artifacts if item.role == "controller"),
        )
        self.assertEqual(list(canonical_root.iterdir()), before)
        self.assertEqual(
            (plan.classification.outcome, plan.classification.reason),
            ("approval-required", "first-install"),
        )
        roles = [item.role for item in plan.artifacts]
        for required in (
            "client",
            "controller",
            "policy",
            "launcher",
            "runtime-entrypoint",
            "runtime-canonical",
            "runtime-bundle-io",
            "runtime-trust",
            "validator-module",
            "trust-context",
            "active-record",
            "shim",
        ):
            self.assertIn(required, roles)
        for artifact in plan.artifacts:
            self.assertEqual(artifact.installed_path.parent != self.root, True)
            self.assertEqual(artifact.sha256, sha256(artifact.raw))
            self.assertTrue(artifact.installed_path.is_relative_to(canonical_root))
        shim = next(item for item in plan.artifacts if item.role == "shim")
        self.assertIn(
            str(canonical_root / "client" / "task_witness_client.py").encode(),
            shim.raw,
        )
        self.assertNotIn(str(self.root / "stage").encode(), shim.raw)

    def test_first_install_rejects_a_nonempty_activation_lock_without_mutation(
        self,
    ) -> None:
        deployment = self.deployment()
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"not lock state")
        activation_lock.chmod(0o600)
        request = self.first_install_request(canonical_root)

        def identity(path: Path) -> tuple[int, ...]:
            metadata = path.lstat()
            return (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
                metadata.st_uid,
                metadata.st_nlink,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            )

        root_identity = identity(canonical_root)
        lock_identity = identity(activation_lock)
        inventory = self.tree_state(canonical_root)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "^first-install activation lock must be empty$",
        ):
            deployment.prepare_first_install(request)

        self.assertEqual(identity(canonical_root), root_identity)
        self.assertEqual(identity(activation_lock), lock_identity)
        self.assertEqual(self.tree_state(canonical_root), inventory)
        self.assertEqual(activation_lock.read_bytes(), b"not lock state")
        self.assertFalse((canonical_root / "transaction.json").exists())
        self.assertFalse((self.root / "stage").exists())

    def test_first_install_rejects_an_executable_activation_lock_without_mutation(
        self,
    ) -> None:
        deployment = self.deployment()
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o700)
        request = self.first_install_request(canonical_root)

        def identity(path: Path) -> tuple[int, ...]:
            metadata = path.lstat()
            return (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
                metadata.st_uid,
                metadata.st_nlink,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            )

        root_identity = identity(canonical_root)
        lock_identity = identity(activation_lock)
        inventory = self.tree_state(canonical_root)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "^first-install activation lock mode must be 0600$",
        ):
            deployment.prepare_first_install(request)

        self.assertEqual(identity(canonical_root), root_identity)
        self.assertEqual(identity(activation_lock), lock_identity)
        self.assertEqual(self.tree_state(canonical_root), inventory)
        self.assertEqual(activation_lock.read_bytes(), b"")
        self.assertFalse((canonical_root / "transaction.json").exists())
        self.assertFalse((self.root / "stage").exists())

    def test_first_install_rejects_a_read_only_activation_lock_without_mutation(
        self,
    ) -> None:
        deployment = self.deployment()
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o400)
        request = self.first_install_request(canonical_root)

        def identity(path: Path) -> tuple[int, ...]:
            metadata = path.lstat()
            return (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
                metadata.st_uid,
                metadata.st_nlink,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            )

        root_identity = identity(canonical_root)
        lock_identity = identity(activation_lock)
        inventory = self.tree_state(canonical_root)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "^first-install activation lock mode must be 0600$",
        ):
            deployment.prepare_first_install(request)

        self.assertEqual(identity(canonical_root), root_identity)
        self.assertEqual(identity(activation_lock), lock_identity)
        self.assertEqual(self.tree_state(canonical_root), inventory)
        self.assertEqual(activation_lock.read_bytes(), b"")
        self.assertFalse((canonical_root / "transaction.json").exists())
        self.assertFalse((self.root / "stage").exists())

    @unittest.skipUnless(sys.platform == "darwin", "macOS ACL semantics required")
    def test_first_install_rejects_a_permissive_root_acl_without_mutation(
        self,
    ) -> None:
        deployment = self.deployment()
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(canonical_root)
        subprocess.run(
            ["/bin/chmod", "+a", "everyone allow read", str(canonical_root)],
            check=True,
        )

        def identity(path: Path) -> tuple[int, ...]:
            metadata = path.lstat()
            return (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
                metadata.st_uid,
                metadata.st_nlink,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            )

        try:
            root_identity = identity(canonical_root)
            lock_identity = identity(activation_lock)
            inventory = self.tree_state(canonical_root)
            acl_evidence = subprocess.run(
                ["/bin/ls", "-led", str(canonical_root)],
                check=True,
                capture_output=True,
            ).stdout

            with self.assertRaisesRegex(
                deployment.DeploymentError,
                "^first-install canonical root has a permissive ACL entry$",
            ):
                deployment.prepare_first_install(request)

            self.assertEqual(identity(canonical_root), root_identity)
            self.assertEqual(identity(activation_lock), lock_identity)
            self.assertEqual(self.tree_state(canonical_root), inventory)
            self.assertEqual(
                subprocess.run(
                    ["/bin/ls", "-led", str(canonical_root)],
                    check=True,
                    capture_output=True,
                ).stdout,
                acl_evidence,
            )
            self.assertFalse((canonical_root / "transaction.json").exists())
            self.assertFalse((self.root / "stage").exists())
        finally:
            subprocess.run(["/bin/chmod", "-N", str(canonical_root)], check=True)

    @unittest.skipUnless(sys.platform == "darwin", "macOS ACL semantics required")
    def test_first_install_rejects_a_permissive_lock_acl_without_mutation(
        self,
    ) -> None:
        deployment = self.deployment()
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(canonical_root)
        subprocess.run(
            ["/bin/chmod", "+a", "everyone allow read", str(activation_lock)],
            check=True,
        )

        def identity(path: Path) -> tuple[int, ...]:
            metadata = path.lstat()
            return (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
                metadata.st_uid,
                metadata.st_nlink,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            )

        try:
            root_identity = identity(canonical_root)
            lock_identity = identity(activation_lock)
            inventory = self.tree_state(canonical_root)
            acl_evidence = subprocess.run(
                ["/bin/ls", "-led", str(activation_lock)],
                check=True,
                capture_output=True,
            ).stdout

            with self.assertRaisesRegex(
                deployment.DeploymentError,
                "^first-install activation lock has a permissive ACL entry$",
            ):
                deployment.prepare_first_install(request)

            self.assertEqual(identity(canonical_root), root_identity)
            self.assertEqual(identity(activation_lock), lock_identity)
            self.assertEqual(self.tree_state(canonical_root), inventory)
            self.assertEqual(
                subprocess.run(
                    ["/bin/ls", "-led", str(activation_lock)],
                    check=True,
                    capture_output=True,
                ).stdout,
                acl_evidence,
            )
            self.assertFalse((canonical_root / "transaction.json").exists())
            self.assertFalse((self.root / "stage").exists())
        finally:
            subprocess.run(["/bin/chmod", "-N", str(activation_lock)], check=True)

    @unittest.skipUnless(sys.platform == "darwin", "macOS ACL semantics required")
    def test_first_install_accepts_deny_only_acl(self) -> None:
        deployment = self.deployment()
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(canonical_root)
        for target in (canonical_root, activation_lock):
            subprocess.run(
                ["/bin/chmod", "+a", "everyone deny write", str(target)],
                check=True,
            )

        try:
            prepared = deployment.prepare_first_install(request)

            self.assertEqual(prepared.plan.precondition.canonical_root, canonical_root)
            self.assertEqual(
                prepared.plan.precondition.activation_lock["path"],
                str(activation_lock),
            )
            self.assertFalse((canonical_root / "transaction.json").exists())
            self.assertFalse((self.root / "stage").exists())
        finally:
            for target in (activation_lock, canonical_root):
                subprocess.run(["/bin/chmod", "-N", str(target)], check=True)

    def test_first_install_rejects_when_root_acl_cannot_be_verified(self) -> None:
        deployment = self.deployment()
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(canonical_root)
        before = self.tree_state(canonical_root)

        with (
            mock.patch.object(
                deployment,
                "_macos_descriptor_has_allow_acl",
                side_effect=OSError("ACL lookup unavailable"),
            ),
            self.assertRaisesRegex(
                deployment.DeploymentError,
                "^first-install canonical root ACL cannot be verified$",
            ),
        ):
            deployment.prepare_first_install(request)

        self.assertEqual(self.tree_state(canonical_root), before)
        self.assertFalse((canonical_root / "transaction.json").exists())
        self.assertFalse((self.root / "stage").exists())

    def test_first_install_receipt_binds_controller_owned_smoke_bundle(self) -> None:
        deployment = self.deployment()

        def thaw(value):
            if isinstance(value, Mapping):
                return {key: thaw(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [thaw(item) for item in value]
            return value

        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(canonical_root)
        prepared = deployment.prepare_first_install(request)
        authorization_raw = self.first_install_authorization_raw(prepared)

        staged = deployment.stage_first_install(
            request,
            authorization_raw,
            self.root / "stage",
        )

        producer = dict(staged.plan.trust.smoke.producers[0])
        producer_identity = {
            key: producer[key]
            for key in (
                "producer_id",
                "contract",
                "implementation_sha256",
                "validator_id",
                "validator_contract",
                "validator_implementation_sha256",
            )
        }
        validator = dict(staged.plan.trust.smoke.validators[0])
        validator_identity = {
            key: validator[key]
            for key in ("validator_id", "contract", "implementation_sha256")
        }
        manifest_value = {
            "schema_version": 1,
            "contract": "task-witness-smoke-bundle-v1",
            "producer": {
                key: producer_identity[key]
                for key in ("producer_id", "contract", "implementation_sha256")
            },
            "challenge": "task-witness-activation-smoke-v1",
        }
        manifest_raw = canonical_document(manifest_value)
        manifest_sha256 = sha256(manifest_raw)
        bundle_sha256 = sha256(
            canonical_bytes(
                {
                    "contract": "task-witness-bundle-inventory-v1",
                    "files": [
                        {
                            "name": "manifest.json",
                            "length": len(manifest_raw),
                            "sha256": manifest_sha256,
                        }
                    ],
                }
            )
        )
        expected_projection = {
            "schema_version": 1,
            "contract": "task-witness-smoke-projection-v1",
            "challenge": "task-witness-activation-smoke-v1",
            "accepted": True,
        }
        expected_anchor = {
            "contract": "task-witness-complete-anchor-v1",
            "generation": staged.plan.active.generation,
            "active_record_sha256": staged.plan.active.sha256,
            "runtime_contract": "task-witness-runtime-v1",
            "interpreter": thaw(staged.plan.active.value["interpreter"]),
            "public_release": thaw(staged.plan.active.value["public_release"]),
            "runtime_implementation_sha256": (
                staged.plan.active.runtime_implementation_sha256
            ),
            "trust_context_sha256": staged.plan.trust.context.sha256,
            "bundle_sha256": bundle_sha256,
            "historical": False,
        }
        expected_witness = {
            "contract": "task-witness-canonical-projection-v2",
            "bundle_sha256": bundle_sha256,
            "producer": producer_identity,
            "validator": validator_identity,
            "projection": expected_projection,
            "trust_context_sha256": staged.plan.trust.context.sha256,
            "historical": False,
        }
        expected_envelope_sha256 = sha256(
            canonical_document(
                {
                    "contract": "task-witness-launch-envelope-v1",
                    "anchor": expected_anchor,
                    "witness": expected_witness,
                }
            )
        )
        manifest = next(
            item for item in staged.artifacts if item.role == "smoke-bundle-manifest"
        )

        self.assertEqual(manifest.relative_path, "smoke/bundle/manifest.json")
        self.assertEqual(manifest.raw, manifest_raw)
        self.assertEqual(
            staged.deployment_value["smoke"],
            {
                "bundle": {
                    "path": str(canonical_root / "smoke" / "bundle"),
                    "sha256": bundle_sha256,
                    "manifest": {
                        "path": str(
                            canonical_root / "smoke" / "bundle" / "manifest.json"
                        ),
                        "length": len(manifest_raw),
                        "sha256": manifest_sha256,
                        "owner": manifest.installed["owner"],
                        "mode": 0o600,
                    },
                },
                "trust_context": {
                    "path": str(staged.plan.trust.context.path),
                    "sha256": staged.plan.trust.context.sha256,
                },
                "producer": producer_identity,
                "validator": validator_identity,
                "expected_projection": expected_projection,
                "expected_anchor": expected_anchor,
                "expected_envelope_sha256": expected_envelope_sha256,
            },
        )
        verified = deployment.verify_deployment_stage(staged.stage_path)
        self.assertEqual(verified.raw, staged.stage_raw)

    def test_first_install_stage_rejects_receipt_process_profile_policy_disagreement(
        self,
    ) -> None:
        deployment = self.deployment()
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(canonical_root)
        prepared = deployment.prepare_first_install(request)
        staged = deployment.stage_first_install(
            request,
            self.first_install_authorization_raw(prepared),
            self.root / "stage",
        )
        deployment_receipt = json.loads(staged.deployment_raw)
        deployment_receipt["process_profile"]["shared_lock_seconds"] = 3
        self.readdress_staged_deployment_receipt(staged, deployment_receipt)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "policy authority|process profile|disagree",
        ):
            deployment.verify_deployment_stage(staged.stage_path)

    def test_first_install_stage_rejects_receipt_source_authority_outside_policy(
        self,
    ) -> None:
        deployment = self.deployment()
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(canonical_root)
        prepared = deployment.prepare_first_install(request)
        staged = deployment.stage_first_install(
            request,
            self.first_install_authorization_raw(prepared),
            self.root / "stage",
        )
        self.assertEqual(
            (staged.classification.outcome, staged.classification.reason),
            ("authorized-first-install", "exact-deployer-authorization"),
        )
        deployment_receipt = json.loads(staged.deployment_raw)
        deployment_receipt["source"]["source_authority"] = "alternate-authority"
        self.readdress_staged_deployment_receipt(staged, deployment_receipt)
        stage_before = self.tree_state(staged.stage_path.parent)
        live_before = self.tree_state(canonical_root)

        with self.assertRaises(deployment.DeploymentError):
            deployment.verify_deployment_stage(staged.stage_path)

        self.assertEqual(self.tree_state(staged.stage_path.parent), stage_before)
        self.assertEqual(self.tree_state(canonical_root), live_before)

    def test_first_install_stage_rejects_receipt_contract_policy_disagreement(
        self,
    ) -> None:
        deployment = self.deployment()
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(canonical_root)
        prepared = deployment.prepare_first_install(request)
        staged = deployment.stage_first_install(
            request,
            self.first_install_authorization_raw(prepared),
            self.root / "stage",
        )
        deployment_receipt = json.loads(staged.deployment_raw)
        deployment_receipt["contracts"]["deployment_receipt"] = (
            "task-witness-deployment-receipt-v999"
        )
        self.readdress_staged_deployment_receipt(staged, deployment_receipt)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "policy authority|contract|disagree",
        ):
            deployment.verify_deployment_stage(staged.stage_path)

    def test_stage_rejects_coherently_readdressed_smoke_context_tampering(
        self,
    ) -> None:
        deployment = self.deployment()
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(canonical_root)
        prepared = deployment.prepare_first_install(request)
        staged = deployment.stage_first_install(
            request,
            self.first_install_authorization_raw(prepared),
            self.root / "stage",
        )
        deployment_receipt = json.loads(staged.deployment_raw)
        smoke = deployment_receipt["smoke"]
        fake_context_sha256 = "0" * 64
        smoke["trust_context"]["sha256"] = fake_context_sha256
        smoke["expected_anchor"]["trust_context_sha256"] = fake_context_sha256
        expected_witness = {
            "contract": "task-witness-canonical-projection-v2",
            "bundle_sha256": smoke["bundle"]["sha256"],
            "producer": smoke["producer"],
            "validator": smoke["validator"],
            "projection": smoke["expected_projection"],
            "trust_context_sha256": fake_context_sha256,
            "historical": False,
        }
        smoke["expected_envelope_sha256"] = sha256(
            canonical_document(
                {
                    "contract": "task-witness-launch-envelope-v1",
                    "anchor": smoke["expected_anchor"],
                    "witness": expected_witness,
                }
            )
        )
        self.readdress_staged_deployment_receipt(staged, deployment_receipt)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "smoke|trust.context|cross.binding|disagree",
        ):
            deployment.verify_deployment_stage(staged.stage_path)

    def test_stage_rejects_coherently_readdressed_smoke_manifest_tampering(
        self,
    ) -> None:
        deployment = self.deployment()
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(canonical_root)
        prepared = deployment.prepare_first_install(request)
        staged = deployment.stage_first_install(
            request,
            self.first_install_authorization_raw(prepared),
            self.root / "stage",
        )
        manifest = next(
            item for item in staged.artifacts if item.role == "smoke-bundle-manifest"
        )
        manifest_value = json.loads(manifest.raw)
        manifest_value["challenge"] = "task-witness-activation-smoke-tampered-v1"
        manifest_raw = canonical_document(manifest_value)
        manifest.staged_path.write_bytes(manifest_raw)
        manifest.staged_path.chmod(0o600)
        manifest_sha256 = sha256(manifest_raw)
        bundle_sha256 = sha256(
            canonical_bytes(
                {
                    "contract": "task-witness-bundle-inventory-v1",
                    "files": [
                        {
                            "name": "manifest.json",
                            "length": len(manifest_raw),
                            "sha256": manifest_sha256,
                        }
                    ],
                }
            )
        )
        deployment_receipt = json.loads(staged.deployment_raw)
        smoke = deployment_receipt["smoke"]
        smoke["bundle"]["sha256"] = bundle_sha256
        smoke["bundle"]["manifest"] = {
            "path": str(manifest.installed_path),
            "length": len(manifest_raw),
            "sha256": manifest_sha256,
            "owner": manifest.installed["owner"],
            "mode": 0o600,
        }
        smoke["expected_anchor"]["bundle_sha256"] = bundle_sha256
        expected_witness = {
            "contract": "task-witness-canonical-projection-v2",
            "bundle_sha256": bundle_sha256,
            "producer": smoke["producer"],
            "validator": smoke["validator"],
            "projection": smoke["expected_projection"],
            "trust_context_sha256": smoke["trust_context"]["sha256"],
            "historical": False,
        }
        smoke["expected_envelope_sha256"] = sha256(
            canonical_document(
                {
                    "contract": "task-witness-launch-envelope-v1",
                    "anchor": smoke["expected_anchor"],
                    "witness": expected_witness,
                }
            )
        )
        self.readdress_staged_deployment_receipt(
            staged,
            deployment_receipt,
            changed_artifacts=(manifest,),
        )

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "smoke|manifest|challenge|bundle|cross.binding|disagree",
        ):
            deployment.verify_deployment_stage(staged.stage_path)

    def test_stage_rejects_coherently_readdressed_smoke_identity_tampering(
        self,
    ) -> None:
        deployment = self.deployment()
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(canonical_root)
        prepared = deployment.prepare_first_install(request)
        staged = deployment.stage_first_install(
            request,
            self.first_install_authorization_raw(prepared),
            self.root / "stage",
        )
        deployment_receipt = json.loads(staged.deployment_raw)
        smoke = deployment_receipt["smoke"]
        smoke["producer"]["implementation_sha256"] = "0" * 64
        expected_witness = {
            "contract": "task-witness-canonical-projection-v2",
            "bundle_sha256": smoke["bundle"]["sha256"],
            "producer": smoke["producer"],
            "validator": smoke["validator"],
            "projection": smoke["expected_projection"],
            "trust_context_sha256": smoke["trust_context"]["sha256"],
            "historical": False,
        }
        smoke["expected_envelope_sha256"] = sha256(
            canonical_document(
                {
                    "contract": "task-witness-launch-envelope-v1",
                    "anchor": smoke["expected_anchor"],
                    "witness": expected_witness,
                }
            )
        )
        self.readdress_staged_deployment_receipt(staged, deployment_receipt)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "smoke|producer|identity|cross.binding|disagree",
        ):
            deployment.verify_deployment_stage(staged.stage_path)

    def test_stage_rejects_coherently_readdressed_smoke_anchor_tampering(
        self,
    ) -> None:
        deployment = self.deployment()
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(canonical_root)
        prepared = deployment.prepare_first_install(request)
        staged = deployment.stage_first_install(
            request,
            self.first_install_authorization_raw(prepared),
            self.root / "stage",
        )
        deployment_receipt = json.loads(staged.deployment_raw)
        smoke = deployment_receipt["smoke"]
        smoke["expected_anchor"]["generation"] = f"sha256-{'0' * 64}"
        expected_witness = {
            "contract": "task-witness-canonical-projection-v2",
            "bundle_sha256": smoke["bundle"]["sha256"],
            "producer": smoke["producer"],
            "validator": smoke["validator"],
            "projection": smoke["expected_projection"],
            "trust_context_sha256": smoke["trust_context"]["sha256"],
            "historical": False,
        }
        smoke["expected_envelope_sha256"] = sha256(
            canonical_document(
                {
                    "contract": "task-witness-launch-envelope-v1",
                    "anchor": smoke["expected_anchor"],
                    "witness": expected_witness,
                }
            )
        )
        self.readdress_staged_deployment_receipt(staged, deployment_receipt)

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "smoke|anchor|active|envelope|cross.binding|disagree",
        ):
            deployment.verify_deployment_stage(staged.stage_path)

    def test_materialized_first_install_stage_is_complete_inert_and_repeatable(
        self,
    ) -> None:
        deployment = self.deployment()
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(canonical_root)
        prepared = deployment.prepare_first_install(request)
        authorization_raw = self.first_install_authorization_raw(prepared)
        staging_root = self.root / "stage"

        staged = deployment.stage_first_install(
            request,
            authorization_raw,
            staging_root,
        )
        repeated = deployment.stage_first_install(
            request,
            authorization_raw,
            staging_root,
        )

        self.assertEqual(staged.stage_raw, repeated.stage_raw)
        self.assertEqual(
            (staged.classification.outcome, staged.classification.reason),
            ("authorized-first-install", "exact-deployer-authorization"),
        )
        self.assertEqual(
            {item.name for item in canonical_root.iterdir()},
            {"activation.lock"},
        )
        for artifact in staged.artifacts:
            self.assertTrue(artifact.staged_path.is_file())
            self.assertFalse(artifact.installed_path.exists())
            self.assertEqual(artifact.staged_path.read_bytes(), artifact.raw)
            self.assertEqual(artifact.staged["mode"], 0o600)
        alias = next(
            item for item in staged.artifacts if item.role == "deployment-alias"
        )
        receipt = next(
            item for item in staged.artifacts if item.role == "deployment-receipt"
        )
        self.assertEqual(alias.raw, receipt.raw)
        self.assertNotEqual(
            alias.staged_path.stat().st_ino, receipt.staged_path.stat().st_ino
        )
        self.assertNotIn(str(staging_root).encode(), staged.deployment_raw)
        self.assertNotIn(str(staging_root).encode(), staged.plan.active.raw)
        self.assertNotIn(str(staging_root).encode(), staged.plan.trust.context.raw)
        verified = deployment.verify_deployment_stage(staged.stage_path)
        self.assertEqual(verified.raw, staged.stage_raw)
        missing = next(item for item in staged.artifacts if item.role == "policy")
        missing.staged_path.unlink()
        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "staged|missing|unavailable",
        ):
            deployment.verify_deployment_stage(staged.stage_path)
        self.assertFalse(missing.staged_path.exists())

    def test_materialization_rejects_a_stage_inside_the_candidate_source(self) -> None:
        deployment = self.deployment()
        candidate_root = self.root / "candidate"
        copy_agent_plugins_candidate(PLUGIN, candidate_root)
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(
            canonical_root,
            candidate_root=candidate_root,
        )
        prepared = deployment.prepare_first_install(request)
        authorization_raw = self.first_install_authorization_raw(prepared)
        staging_root = candidate_root / "staging"

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "staging root|candidate source|disjoint",
        ):
            deployment.stage_first_install(
                request,
                authorization_raw,
                staging_root,
            )

        self.assertFalse(staging_root.exists())

    def test_stage_rejects_canonical_ancestor_alias_before_writes(self) -> None:
        deployment = self.deployment()
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(canonical_root)
        prepared = deployment.prepare_first_install(request)
        authorization_raw = self.first_install_authorization_raw(prepared)
        canonical_alias = self.root / "canonical-alias"
        canonical_alias.symlink_to(canonical_root, target_is_directory=True)
        staging_root = canonical_alias / "stage"
        before = self.tree_state(canonical_root)

        with self.assertRaises(deployment.DeploymentError) as raised:
            deployment.stage_first_install(
                request,
                authorization_raw,
                staging_root,
            )

        self.assertFalse((canonical_root / "stage").exists())
        self.assertEqual(self.tree_state(canonical_root), before)
        self.assertRegex(
            str(raised.exception),
            "staging root|canonical|installation|disjoint",
        )

    def test_stage_rejects_candidate_ancestor_alias_before_writes(self) -> None:
        deployment = self.deployment()
        candidate_root = self.root / "candidate"
        copy_agent_plugins_candidate(PLUGIN, candidate_root)
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(
            canonical_root,
            candidate_root=candidate_root,
        )
        prepared = deployment.prepare_first_install(request)
        authorization_raw = self.first_install_authorization_raw(prepared)
        candidate_alias = self.root / "candidate-alias"
        candidate_alias.symlink_to(candidate_root, target_is_directory=True)
        staging_root = candidate_alias / "stage"
        before = self.tree_state(candidate_root)

        with self.assertRaises(deployment.DeploymentError) as raised:
            deployment.stage_first_install(
                request,
                authorization_raw,
                staging_root,
            )

        self.assertFalse((candidate_root / "stage").exists())
        self.assertEqual(self.tree_state(candidate_root), before)
        self.assertRegex(
            str(raised.exception),
            "staging root|candidate source|disjoint",
        )

    def test_stage_rejects_external_move_into_canonical_root_before_writes(
        self,
    ) -> None:
        deployment = self.deployment()
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(canonical_root)
        prepared = deployment.prepare_first_install(request)
        authorization_raw = self.first_install_authorization_raw(prepared)
        staging_root = self.root / "stage"
        moved_stage = canonical_root / "externally-moved-stage"
        before_move = self.tree_state(canonical_root)
        post_move_states = []
        open_stage = deployment._open_disjoint_private_stage_root

        def move_open_stage(
            path: Path,
            *,
            canonical_root: Path,
            candidate_root: Path,
        ):
            stage = open_stage(
                path,
                canonical_root=canonical_root,
                candidate_root=candidate_root,
            )
            stage.path.rename(moved_stage)
            post_move_states.append(self.tree_state(canonical_root))
            return stage

        with mock.patch.object(
            deployment,
            "_open_disjoint_private_stage_root",
            side_effect=move_open_stage,
        ):
            with self.assertRaises(deployment.DeploymentError):
                deployment.stage_first_install(
                    request,
                    authorization_raw,
                    staging_root,
                )

        self.assertEqual(len(post_move_states), 1)
        self.assertNotEqual(post_move_states[0], before_move)
        self.assertFalse(staging_root.exists())
        self.assertEqual(self.tree_state(canonical_root), post_move_states[0])
        self.assertEqual(self.tree_state(moved_stage), ())

    def test_stage_reports_possible_residue_when_same_euid_move_follows_precheck(
        self,
    ) -> None:
        deployment = self.deployment()
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(canonical_root)
        prepared = deployment.prepare_first_install(request)
        authorization_raw = self.first_install_authorization_raw(prepared)
        staging_root = self.root / "stage"
        moved_stage = canonical_root / "externally-moved-stage"
        active = next(
            item for item in prepared.plan.artifacts if item.role == "active-record"
        )
        post_move_states = []
        recheck_stage = deployment._recheck_disjoint_private_stage_root

        def move_stage_after_real_recheck(stage) -> None:
            recheck_stage(stage)
            if not post_move_states:
                stage.path.rename(moved_stage)
                post_move_states.append(self.tree_state(canonical_root))

        accepted_stages = []
        with mock.patch.object(
            deployment,
            "_recheck_disjoint_private_stage_root",
            side_effect=move_stage_after_real_recheck,
        ):
            with self.assertRaises(deployment.DeploymentError) as raised:
                accepted_stages.append(
                    deployment.stage_first_install(
                        request,
                        authorization_raw,
                        staging_root,
                    )
                )

        self.assertEqual(
            str(raised.exception),
            "deployment staging root mapping changed; fail-stop with possible "
            "residue from same-EUID namespace mutation",
        )
        self.assertEqual(accepted_stages, [])
        self.assertEqual(len(post_move_states), 1)
        self.assertFalse(staging_root.exists())
        self.assertFalse((moved_stage / "stage.json").exists())
        residue = self.tree_state(moved_stage)
        self.assertEqual(len(residue), 1)
        self.assertEqual(residue[0][0], active.relative_path)
        self.assertEqual(residue[0][1], "file")
        self.assertEqual(residue[0][2] & 0o777, 0o600)
        self.assertEqual(residue[0][3], (len(active.raw), active.sha256))
        residue_path = f"{moved_stage.name}/{active.relative_path}"
        self.assertEqual(
            tuple(
                item
                for item in self.tree_state(canonical_root)
                if item[0] != residue_path
            ),
            post_move_states[0],
        )

    def test_final_stage_receipt_move_leaves_only_unverifiable_residue(
        self,
    ) -> None:
        deployment = self.deployment()
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(canonical_root)
        prepared = deployment.prepare_first_install(request)
        authorization_raw = self.first_install_authorization_raw(prepared)
        control_root = self.root / "other"
        control = deployment.stage_first_install(
            request,
            authorization_raw,
            control_root,
        )
        expected_artifact_state = tuple(
            item for item in self.tree_state(control_root) if item[0] != "stage.json"
        )
        staging_root = self.root / "stage"
        moved_stage = canonical_root / "externally-moved-stage"
        complete_inventory_checks = []
        post_move_states = []
        recheck_stage = deployment._recheck_disjoint_private_stage_root

        def move_stage_after_final_artifact_recheck(stage) -> None:
            recheck_stage(stage)
            artifact_files = tuple(
                path for path in stage.path.rglob("*") if path.is_file()
            )
            if (
                len(artifact_files) == len(control.artifacts)
                and not (stage.path / "stage.json").exists()
            ):
                complete_inventory_checks.append(self.tree_state(stage.path))
                if len(complete_inventory_checks) == 2:
                    stage.path.rename(moved_stage)
                    post_move_states.append(self.tree_state(canonical_root))

        accepted_stages = []
        with mock.patch.object(
            deployment,
            "_recheck_disjoint_private_stage_root",
            side_effect=move_stage_after_final_artifact_recheck,
        ):
            with self.assertRaises(deployment.DeploymentError) as raised:
                accepted_stages.append(
                    deployment.stage_first_install(
                        request,
                        authorization_raw,
                        staging_root,
                    )
                )

        self.assertEqual(
            str(raised.exception),
            "deployment staging root mapping changed; fail-stop with possible "
            "residue from same-EUID namespace mutation",
        )
        self.assertEqual(accepted_stages, [])
        self.assertEqual(len(complete_inventory_checks), 2)
        self.assertEqual(
            complete_inventory_checks,
            [expected_artifact_state, expected_artifact_state],
        )
        self.assertEqual(len(post_move_states), 1)
        self.assertFalse(staging_root.exists())
        stage_receipt = moved_stage / "stage.json"
        self.assertTrue(stage_receipt.is_file())
        self.assertEqual(len(stage_receipt.read_bytes()), 11_706)
        residue = self.tree_state(moved_stage)
        self.assertEqual(
            tuple(item for item in residue if item[0] != "stage.json"),
            expected_artifact_state,
        )
        stage_entries = tuple(item for item in residue if item[0] == "stage.json")
        self.assertEqual(len(stage_entries), 1)
        self.assertEqual(stage_entries[0][1], "file")
        self.assertEqual(stage_entries[0][2] & 0o777, 0o600)
        self.assertEqual(stage_entries[0][3][0], 11_706)
        verified_stages = []
        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "staged deployment root binding disagrees",
        ):
            verified_stages.append(deployment.verify_deployment_stage(stage_receipt))
        self.assertEqual(verified_stages, [])

    def test_prepare_records_physical_ancestor_aliased_candidate_root(self) -> None:
        deployment = self.deployment()
        physical_parent = self.root / "physical-parent"
        physical_parent.mkdir(mode=0o700)
        candidate_root = physical_parent / "candidate"
        copy_agent_plugins_candidate(PLUGIN, candidate_root)
        parent_alias = self.root / "candidate-parent-alias"
        parent_alias.symlink_to(physical_parent, target_is_directory=True)
        aliased_candidate_root = parent_alias / "candidate"
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(
            canonical_root,
            candidate_root=aliased_candidate_root,
        )

        prepared = deployment.prepare_first_install(request)
        authorization_raw = self.first_install_authorization_raw(prepared)
        staging_root = candidate_root / "stage"
        before = self.tree_state(candidate_root)

        self.assertEqual(
            prepared.plan.source.tree.root,
            candidate_root.resolve(strict=True),
        )
        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "staging root|candidate source|disjoint",
        ):
            deployment.stage_first_install(
                request,
                authorization_raw,
                staging_root,
            )

        self.assertFalse(staging_root.exists())
        self.assertEqual(self.tree_state(candidate_root), before)

    def test_first_install_plan_rejects_installation_inside_candidate_source(
        self,
    ) -> None:
        deployment = self.deployment()
        candidate_root = self.root / "candidate"
        copy_agent_plugins_candidate(PLUGIN, candidate_root)
        canonical_root = candidate_root / "canonical-install"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(
            canonical_root,
            candidate_root=candidate_root,
        )

        with self.assertRaisesRegex(
            deployment.DeploymentError,
            "canonical root|candidate source|disjoint",
        ):
            deployment.prepare_first_install(request)

        self.assertEqual(
            {item.name for item in canonical_root.iterdir()},
            {"activation.lock"},
        )

    def test_stage_recomputes_inputs_and_rejects_stale_authorization_before_writes(
        self,
    ) -> None:
        deployment = self.deployment()
        candidate_root = self.root / "candidate"
        copy_agent_plugins_candidate(PLUGIN, candidate_root)
        canonical_root = self.root / "canonical"
        canonical_root.mkdir(mode=0o700)
        activation_lock = canonical_root / "activation.lock"
        activation_lock.write_bytes(b"")
        activation_lock.chmod(0o600)
        request = self.first_install_request(
            canonical_root,
            candidate_root=candidate_root,
        )
        prepared = deployment.prepare_first_install(request)
        authorization_raw = self.first_install_authorization_raw(prepared)
        changed_request = replace(
            request,
            source_evidence=deployment.HarnessSnapshotEvidence(
                binding_raw=request.source_evidence.binding_raw,
                receipt_raw=request.source_evidence.receipt_raw + b"changed\n",
            ),
        )
        staging_root = self.root / "stage"

        with self.assertRaises(deployment.DeploymentError):
            deployment.stage_first_install(
                changed_request,
                authorization_raw,
                staging_root,
            )

        self.assertFalse(staging_root.exists())


if __name__ == "__main__":
    unittest.main()
