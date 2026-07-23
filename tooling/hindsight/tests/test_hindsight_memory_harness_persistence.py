from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from tooling.hindsight.lib.hindsight_memory_control_plane.canonical import digest
from tooling.hindsight.lib.hindsight_memory_control_plane import harness_persistence
from tooling.hindsight.lib.hindsight_memory_control_plane.harness_persistence import (
    HarnessPersistenceError,
    NativeHarnessDestination,
)
from tooling.hindsight.lib.hindsight_memory_control_plane.harnesses import (
    load_native_activation_receipt,
    native_activation_plan,
    render_native_harness_artifact,
    stage_native_harness_artifacts,
)


class NativeHarnessDestinationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.rollback = self.root / "rollback"
        self.rollback.mkdir(mode=0o700)
        self.hooks = self.root / "hooks.json"
        self.settings = self.root / "settings.json"
        self.tools = self.root / "tools.json"
        self.hooks.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {"hooks": [{"command": "echo hindsight-memory"}]}
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        self.settings.write_text(json.dumps({"unrelated": True}), encoding="utf-8")
        self.tools.write_text(
            json.dumps({"other": {"command": "other"}}), encoding="utf-8"
        )
        for path in (self.hooks, self.settings, self.tools):
            path.chmod(0o600)
        self.destination = NativeHarnessDestination.load(
            {
                "schema_version": 1,
                "harness_id": "codex",
                "hooks_path": str(self.hooks),
                "settings_path": str(self.settings),
                "tools_path": str(self.tools),
                "rollback_root": str(self.rollback),
            }
        )

    def test_round_trip_preserves_unrelated_surfaces_and_uses_cas(self) -> None:
        current = self.destination.read_configuration()
        target = {
            "hooks": {
                "hooks": {
                    **current["hooks"]["hooks"],
                    "UserPromptSubmit": [{"hooks": [{"command": "controller"}]}],
                }
            },
            "settings": {**current["settings"], "autoRecall": False},
            "tools": {**current["tools"], "reflect": {"command": "reflect"}},
        }

        self.destination.write_configuration(digest(current), target)

        self.assertEqual(self.destination.read_configuration(), target)
        self.assertTrue(self.destination.persist_rollback(current).is_file())
        self.assertEqual(self.destination.read_rollback(digest(current)), current)
        with self.assertRaisesRegex(HarnessPersistenceError, "changed"):
            self.destination.write_configuration(digest(current), current)

    def test_partial_document_write_restores_exact_prestate(self) -> None:
        current = self.destination.read_configuration()
        target = {
            "hooks": {"hooks": {"UserPromptSubmit": []}},
            "settings": {"autoRecall": False},
            "tools": {"recall": {"command": "controller"}},
        }
        original_atomic_write = harness_persistence._atomic_write
        failed = False

        def fail_settings_once(path, value, **kwargs):
            nonlocal failed
            if path == self.settings and not failed:
                failed = True
                raise OSError("injected settings write failure")
            return original_atomic_write(path, value, **kwargs)

        with mock.patch.object(
            harness_persistence, "_atomic_write", side_effect=fail_settings_once
        ):
            with self.assertRaisesRegex(OSError, "injected"):
                self.destination.write_configuration(digest(current), target)

        self.assertEqual(self.destination.read_configuration(), current)
        self.assertFalse((self.rollback / "codex.transaction.json").exists())

    def test_claude_hooks_are_a_section_of_user_settings(self) -> None:
        self.hooks.write_text(
            json.dumps({"enabledPlugins": {"other": True}, "hooks": {"Stop": []}}),
            encoding="utf-8",
        )
        destination = NativeHarnessDestination.load(
            {
                "schema_version": 1,
                "harness_id": "claude-code",
                "hooks_path": str(self.hooks),
                "settings_path": str(self.settings),
                "tools_path": str(self.tools),
                "rollback_root": str(self.rollback),
            }
        )
        current = destination.read_configuration()
        target = {**current, "hooks": {"hooks": {"SessionEnd": []}}}

        destination.write_configuration(digest(current), target)

        persisted = json.loads(self.hooks.read_text(encoding="utf-8"))
        self.assertEqual(persisted["enabledPlugins"], {"other": True})
        self.assertEqual(persisted["hooks"], {"SessionEnd": []})

    def test_rejects_symlinks_and_unsafe_modes(self) -> None:
        self.settings.chmod(0o666)
        with self.assertRaisesRegex(HarnessPersistenceError, "protected"):
            self.destination.read_configuration()
        self.settings.chmod(0o600)
        linked = self.root / "linked.json"
        linked.symlink_to(self.settings)
        with self.assertRaisesRegex(HarnessPersistenceError, "regular"):
            NativeHarnessDestination.load(
                {
                    "schema_version": 1,
                    "harness_id": "cursor",
                    "hooks_path": str(self.hooks),
                    "settings_path": str(linked),
                    "tools_path": str(self.tools),
                    "rollback_root": str(self.rollback),
                }
            ).read_configuration()

        actual = self.root / "actual"
        actual.mkdir(mode=0o700)
        nested_settings = actual / "settings.json"
        nested_settings.write_text("{}", encoding="utf-8")
        nested_settings.chmod(0o600)
        alias = self.root / "alias"
        alias.symlink_to(actual, target_is_directory=True)
        with self.assertRaisesRegex(HarnessPersistenceError, "parent"):
            NativeHarnessDestination.load(
                {
                    "schema_version": 1,
                    "harness_id": "cursor",
                    "hooks_path": str(self.hooks),
                    "settings_path": str(alias / "settings.json"),
                    "tools_path": str(self.tools),
                    "rollback_root": str(self.rollback),
                }
            ).read_configuration()

        with self.assertRaisesRegex(HarnessPersistenceError, "rollback digest"):
            self.destination.read_rollback("../" + "a" * 61)

    def test_interrupted_transaction_recovers_by_recorded_phase(self) -> None:
        original_documents = {
            "hooks": json.loads(self.hooks.read_text(encoding="utf-8")),
            "settings": json.loads(self.settings.read_text(encoding="utf-8")),
            "tools": json.loads(self.tools.read_text(encoding="utf-8")),
        }
        target_documents = {
            "hooks": {"hooks": {"UserPromptSubmit": []}},
            "settings": {"autoRecall": False},
            "tools": {"recall": {"command": "controller"}},
        }
        journal = self.rollback / "codex.transaction.json"

        for path, value in (
            (self.hooks, target_documents["hooks"]),
            (self.settings, target_documents["settings"]),
            (self.tools, target_documents["tools"]),
        ):
            path.write_text(json.dumps(value), encoding="utf-8")
            path.chmod(0o600)
        journal.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "phase": "prepared",
                    "original": original_documents,
                    "target": target_documents,
                }
            ),
            encoding="utf-8",
        )
        journal.chmod(0o600)

        self.assertEqual(
            self.destination.read_configuration(),
            {
                "hooks": original_documents["hooks"],
                "settings": original_documents["settings"],
                "tools": original_documents["tools"],
            },
        )
        self.assertFalse(journal.exists())

        journal.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "phase": "committed",
                    "original": original_documents,
                    "target": target_documents,
                }
            ),
            encoding="utf-8",
        )
        journal.chmod(0o600)

        self.assertEqual(
            self.destination.read_configuration(),
            {
                "hooks": target_documents["hooks"],
                "settings": target_documents["settings"],
                "tools": target_documents["tools"],
            },
        )
        self.assertFalse(journal.exists())

    def test_read_removes_only_regular_atomic_write_remnants(self) -> None:
        orphan = self.root / ".settings.json.hindsight-123-deadbeef"
        orphan.write_text("partial", encoding="utf-8")
        orphan.chmod(0o600)
        unrelated = self.root / ".settings.json.other"
        unrelated.write_text("keep", encoding="utf-8")

        self.destination.read_configuration()

        self.assertFalse(orphan.exists())
        self.assertTrue(unrelated.exists())

    def test_recovery_accepts_journal_larger_than_one_document(self) -> None:
        original_documents = {
            "hooks": json.loads(self.hooks.read_text(encoding="utf-8")),
            "settings": {"blob": "a" * 600_000},
            "tools": json.loads(self.tools.read_text(encoding="utf-8")),
        }
        target_documents = {
            **original_documents,
            "settings": {"blob": "b" * 600_000},
        }
        self.settings.write_text(
            json.dumps(target_documents["settings"]), encoding="utf-8"
        )
        self.settings.chmod(0o600)
        journal = self.rollback / "codex.transaction.json"
        journal.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "phase": "prepared",
                    "original": original_documents,
                    "target": target_documents,
                }
            ),
            encoding="utf-8",
        )
        journal.chmod(0o600)
        self.assertGreater(journal.stat().st_size, 1024 * 1024)

        recovered = self.destination.read_configuration()

        self.assertEqual(recovered["settings"], original_documents["settings"])
        self.assertFalse(journal.exists())

    def test_secret_free_activation_receipt_round_trips_for_separate_apply(
        self,
    ) -> None:
        staging = self.root / "staging"
        staging.mkdir(mode=0o700)
        artifact = render_native_harness_artifact(
            "codex",
            executable="/private/controller/hindsight-memory",
            state_dir="/private/state",
            locator_dir="/private/locators",
        )
        generation = stage_native_harness_artifacts(staging, {"codex": artifact})
        current = self.destination.read_configuration()
        plan = native_activation_plan(
            artifact,
            current,
            staged_generation=generation,
            inventory_digest="1" * 64,
            artifact_digest="2" * 64,
            policy_digest="3" * 64,
        )

        loaded = load_native_activation_receipt(plan.to_dict())

        self.assertEqual(loaded, plan.to_dict())
        tampered = plan.to_dict()
        tampered["target_digest"] = "4" * 64
        with self.assertRaisesRegex(ValueError, "invalid"):
            load_native_activation_receipt(tampered)
        invalid_schema = plan.to_dict()
        invalid_schema["schema_version"] = True
        with self.assertRaisesRegex(ValueError, "invalid"):
            load_native_activation_receipt(invalid_schema)
        invalid_harness = plan.to_dict()
        invalid_harness["harness_id"] = []
        with self.assertRaisesRegex(ValueError, "invalid"):
            load_native_activation_receipt(invalid_harness)

    def test_native_plan_activates_a_staged_controller_binding(self) -> None:
        staged_binding = {
            "schemaVersion": 1,
            "broker": {
                "transport": "unix",
                "path": "/private/broker.sock",
                "scope": "user",
            },
            "adapter": "hindsight-codex",
            "active": False,
        }
        self.settings.write_text(json.dumps(staged_binding), encoding="utf-8")
        self.settings.chmod(0o600)
        staging = self.root / "staging-active"
        staging.mkdir(mode=0o700)
        artifact = render_native_harness_artifact(
            "codex",
            executable="/private/controller/hindsight-memory",
            state_dir="/private/state",
            locator_dir="/private/locators",
        )
        generation = stage_native_harness_artifacts(staging, {"codex": artifact})

        plan = native_activation_plan(
            artifact,
            self.destination.read_configuration(),
            staged_generation=generation,
            inventory_digest="1" * 64,
            artifact_digest="2" * 64,
            policy_digest="3" * 64,
        )

        self.assertIs(plan.target["settings"]["active"], True)
        self.assertIs(plan.target["settings"]["autoRecall"], False)
        self.assertIs(plan.target["settings"]["autoRetain"], False)

        direct_settings = {
            **staged_binding,
            "hindsightApiUrl": "http://127.0.0.1:7979",
            "hindsightApiToken": "upstream-token",
            "bankId": "legacy",
            "bankIdPrefix": "legacy-",
            "dynamicBankId": True,
            "tenantToken": "secret-value",
        }
        self.settings.write_text(json.dumps(direct_settings), encoding="utf-8")
        self.settings.chmod(0o600)
        direct_plan = native_activation_plan(
            artifact,
            self.destination.read_configuration(),
            staged_generation=generation,
            inventory_digest="1" * 64,
            artifact_digest="2" * 64,
            policy_digest="3" * 64,
        )
        for key in (
            "hindsightApiUrl",
            "hindsightApiToken",
            "bankId",
            "bankIdPrefix",
            "dynamicBankId",
            "tenantToken",
        ):
            self.assertNotIn(key, direct_plan.target["settings"])
        self.assertNotIn("secret-value", json.dumps(direct_plan.to_dict()))
        self.assertNotIn("upstream-token", json.dumps(direct_plan.to_dict()))

        bool_schema_binding = {**staged_binding, "schemaVersion": True}
        self.settings.write_text(json.dumps(bool_schema_binding), encoding="utf-8")
        self.settings.chmod(0o600)
        bool_schema_plan = native_activation_plan(
            artifact,
            self.destination.read_configuration(),
            staged_generation=generation,
            inventory_digest="1" * 64,
            artifact_digest="2" * 64,
            policy_digest="3" * 64,
        )
        self.assertIs(bool_schema_plan.target["settings"]["active"], False)

    def test_cli_stages_plans_applies_and_reports_real_files(self) -> None:
        repository = Path(__file__).resolve().parents[3]
        cli = repository / "tooling/hindsight/bin/hindsight-memory"
        state = self.root / "state"
        staging = self.root / "staging"
        locators = self.root / "locators"
        for path in (state, staging, locators):
            path.mkdir(mode=0o700)
        destination_path = self.root / "destination.json"
        destination_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "harness_id": "codex",
                    "hooks_path": str(self.hooks),
                    "settings_path": str(self.settings),
                    "tools_path": str(self.tools),
                    "rollback_root": str(self.rollback),
                }
            ),
            encoding="utf-8",
        )
        destination_path.chmod(0o600)
        self.settings.write_text(
            json.dumps({"unrelated": True, "tenantToken": "plan-secret"}),
            encoding="utf-8",
        )
        self.settings.chmod(0o600)
        base = [sys.executable, str(cli), "--state-dir", str(state), "harness-config"]
        shared = [
            "--destination",
            str(destination_path),
            "--executable",
            str(cli),
            "--locator-dir",
            str(locators),
        ]
        initial_status = subprocess.run(
            [*base, "status", "--destination", str(destination_path)],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            timeout=10,
        )
        self.assertFalse(json.loads(initial_status.stdout)["controller_hooks_present"])
        staged = subprocess.run(
            [*base, "stage", *shared, "--staging-root", str(staging)],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            timeout=10,
        )
        generation = json.loads(staged.stdout)["generation"]
        plan_path = self.root / "plan.json"
        inventory = (
            repository / "tooling/hindsight/examples/portable-consumer/inventory.json"
        )
        policy = repository / "tooling/hindsight/examples/provider-runtime-policy.json"
        planned = subprocess.run(
            [
                *base,
                "plan",
                *shared,
                "--generation",
                generation,
                "--inventory",
                str(inventory),
                "--policy",
                str(policy),
                "--output",
                str(plan_path),
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            timeout=10,
        )
        plan_output = json.loads(planned.stdout)
        approval_digest = plan_output["approval_digest"]
        plan_record = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertNotIn("plan-secret", plan_path.read_text(encoding="utf-8"))
        self.assertNotIn("prestate", plan_record["plan"])
        self.assertNotIn("target", plan_record["plan"])
        self.assertEqual(
            plan_record["destination_digest"], self.destination.destination_digest
        )
        self.assertEqual(plan_record["approval_digest"], approval_digest)
        applied = subprocess.run(
            [
                *base,
                "apply",
                "--destination",
                str(destination_path),
                "--generation",
                generation,
                "--inventory",
                str(inventory),
                "--policy",
                str(policy),
                "--plan",
                str(plan_path),
                "--approval-digest",
                approval_digest,
                "--broker-healthy",
                "--profile-healthy",
                "--adapter-self-test",
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            timeout=10,
        )

        self.assertEqual(json.loads(applied.stdout)["status"], "activated")
        self.assertNotIn(
            "tenantToken", self.destination.read_configuration()["settings"]
        )
        status = subprocess.run(
            [
                *base,
                "status",
                "--destination",
                str(destination_path),
                "--plan",
                str(plan_path),
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            timeout=10,
        )
        self.assertTrue(json.loads(status.stdout)["target_matches"])
        self.assertTrue(json.loads(status.stdout)["destination_matches"])
        rolled_back = subprocess.run(
            [
                *base,
                "rollback",
                "--destination",
                str(destination_path),
                "--generation",
                generation,
                "--inventory",
                str(inventory),
                "--policy",
                str(policy),
                "--plan",
                str(plan_path),
                "--approval-digest",
                approval_digest,
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            timeout=10,
        )
        self.assertEqual(json.loads(rolled_back.stdout)["status"], "rolled_back")
        self.assertEqual(
            self.destination.read_configuration()["settings"],
            {"unrelated": True, "tenantToken": "plan-secret"},
        )


if __name__ == "__main__":
    unittest.main()
