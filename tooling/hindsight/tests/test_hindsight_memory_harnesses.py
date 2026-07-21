import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import os
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from hindsight_memory_control_plane.harnesses import (  # noqa: E402
    ActivationCASMismatch,
    NativeActivationPlan,
    OWNED_KEYS,
    apply_activation,
    activation_plan,
    apply_native_activation,
    native_activation_plan,
    render_native_harness_artifact,
    stage_native_harness_artifacts,
    verify_native_harness_generation,
    render_harness,
    render_harnesses,
    rollback_activation,
)
from hindsight_memory_control_plane.canonical import digest  # noqa: E402
from hindsight_memory_control_plane.model import deep_thaw  # noqa: E402


DIGESTS = {
    "inventory_digest": "1" * 64,
    "artifact_digest": "2" * 64,
    "policy_digest": "3" * 64,
}


class HarnessRenderingTest(unittest.TestCase):
    def setUp(self):
        self.existing = {
            "codex": {
                "hindsightApiUrl": "http://localhost:7979",
                "bankId": "engineering",
                "schema_version": None,
                "broker": {"transport": "tcp", "unknown_broker_option": 7},
                "adapter": {"id": "legacy", "registration": "keep"},
                "active": True,
                "unknown_setting": {"nested": [1, 2]},
                "registrations": [{"id": "third-party"}],
                "serviceEndpoint": "/other/service",
                "bankingPreference": "credit-union",
            },
            "claude-code": {"active": None, "theme": "warm"},
            "cursor": {"telemetry": False},
        }
        self.bindings = {
            "codex": "hindsight-codex",
            "claude-code": "hindsight-claude-code",
            "cursor": "hindsight-cursor",
        }

    def test_render_preserves_unknowns_and_records_exact_owned_prestate(self):
        outcome = render_harnesses(
            self.existing,
            self.bindings,
            socket_path="/Users/example/.local/state/hindsight-memory/broker.sock",
        )

        codex = deep_thaw(outcome["codex"].rendered)
        self.assertEqual(codex["unknown_setting"], {"nested": [1, 2]})
        self.assertEqual(codex["registrations"], [{"id": "third-party"}])
        self.assertEqual(codex["serviceEndpoint"], "/other/service")
        self.assertEqual(codex["bankingPreference"], "credit-union")
        self.assertTrue(
            {
                "hindsightApiUrl",
                "bankId",
            }.isdisjoint(codex)
        )
        self.assertEqual(
            set(codex) - set(self.existing["codex"]), set(OWNED_KEYS) - set(self.existing["codex"])
        )
        self.assertEqual(codex["schemaVersion"], 1)
        self.assertEqual(
            codex["broker"],
            {
                "transport": "unix",
                "path": "/Users/example/.local/state/hindsight-memory/broker.sock",
                "scope": "user",
            },
        )
        self.assertEqual(codex["adapter"], "hindsight-codex")
        self.assertIs(codex["active"], False)
        self.assertEqual(
            outcome["codex"].prestate["schemaVersion"], {"present": False}
        )
        self.assertNotIn("schema_version", outcome["codex"].prestate)
        self.assertEqual(outcome["cursor"].prestate["active"], {"present": False})
        serialized = json.dumps(
            {key: value.to_dict() for key, value in outcome.items()}
        ).lower()
        for forbidden in ("http://", "https://", "bankid", "bank_id", "bearer", "token", "signing"):
            self.assertNotIn(forbidden, serialized)

    def test_render_rejects_network_or_unsupported_harness_bindings(self):
        with self.assertRaisesRegex(ValueError, "Unix socket"):
            render_harnesses({}, self.bindings, socket_path="http://localhost:7979")
        with self.assertRaisesRegex(ValueError, "unsupported harness"):
            render_harnesses({}, {**self.bindings, "other": "x"}, socket_path="/tmp/broker.sock")

    def test_render_retires_direct_secret_fields_without_serializing_their_values(self):
        for key in ("tenantToken", "bearerToken", "apiKey", "signingKey"):
            with self.subTest(key=key):
                outcome = render_harness(
                    {key: "secret-value"},
                    harness_id="codex",
                    adapter="hindsight-codex",
                    socket_path="/Users/example/.local/state/hindsight-memory/broker.sock",
                )
                self.assertNotIn(key, outcome.rendered)
                serialized = json.dumps(outcome.to_dict()).lower()
                self.assertNotIn(key.lower(), serialized)
                self.assertNotIn("secret-value", serialized)

    def test_serialization_exposes_only_closed_preserved_config_projection(self):
        outcome = render_harness(
            {
                "registrations": [{
                    "id": "third-party",
                    "options": {
                        "api_key": "nested-secret",
                        "metadata": {"region": "us-east"},
                    },
                }],
                "custom": {"password": "password-secret", "enabled": True},
            },
            harness_id="codex",
            adapter="hindsight-codex",
            socket_path="/Users/example/.local/state/hindsight-memory/broker.sock",
        )
        serialized = outcome.to_dict()
        self.assertEqual(
            set(serialized),
            {
                "harness_id",
                "configuration_digest",
                "preserved_config_digest",
                "preserved_config_present",
                "activation_state",
                "expected_prestate_digest",
                "retired_keys_digest",
                "retired_keys_present",
            },
        )
        self.assertTrue(serialized["preserved_config_present"])
        self.assertEqual(
            serialized["preserved_config_digest"],
            digest({
                "registrations": [{
                    "id": "third-party",
                    "options": {
                        "api_key": "nested-secret",
                        "metadata": {"region": "us-east"},
                    },
                }],
                "custom": {"password": "password-secret", "enabled": True},
            }),
        )
        for forbidden in ("third-party", "api_key", "nested-secret", "password-secret", "us-east"):
            self.assertNotIn(forbidden, json.dumps(serialized))
        self.assertNotIn("nested-secret", repr(outcome))
        self.assertNotIn("password-secret", repr(outcome))

    def test_native_artifacts_use_only_controller_hooks_and_disable_upstream_authority(self):
        artifacts = {
            harness: render_native_harness_artifact(
                harness,
                executable="/opt/hindsight/bin/hindsight-memory",
                state_dir="/var/lib/hindsight-controller",
                locator_dir="/run/user/1000/hindsight-locators",
            )
            for harness in ("codex", "claude-code", "cursor")
        }
        for harness, artifact in artifacts.items():
            with self.subTest(harness=harness):
                rendered = deep_thaw(artifact.rendered)
                serialized = json.dumps(rendered)
                self.assertIn(f"harness {harness}", serialized)
                self.assertNotIn("http://", serialized)
                self.assertNotIn("https://", serialized)
                self.assertNotIn("bankId", serialized)
                self.assertNotIn("token", serialized.lower())
                self.assertFalse(rendered["settings"]["autoRecall"])
                self.assertFalse(rendered["settings"]["autoRetain"])
                self.assertTrue(rendered["controllerOwned"])
        self.assertFalse(
            deep_thaw(artifacts["claude-code"].rendered)["settings"]["enableKnowledgeTools"]
        )
        codex_hooks = deep_thaw(artifacts["codex"].rendered)["hooks"]["hooks"]
        self.assertIn("PreCompact", codex_hooks)
        self.assertNotIn("SessionEnd", codex_hooks)
        self.assertNotIn("async", codex_hooks["Stop"][0]["hooks"][0])
        claude_hooks = deep_thaw(artifacts["claude-code"].rendered)["hooks"]["hooks"]
        self.assertIn("SessionEnd", claude_hooks)
        cursor = deep_thaw(artifacts["cursor"].rendered)
        self.assertEqual(
            set(cursor["tools"]),
            {"recall", "reflect", "model", "status"},
        )
        self.assertIn("sessionStart", cursor["hooks"]["hooks"])
        self.assertIn("preCompact", cursor["hooks"]["hooks"])
        self.assertNotIn("UserPromptSubmit", cursor["hooks"]["hooks"])

    def test_native_artifact_serialization_exposes_digests_not_commands_or_paths(self):
        artifact = render_native_harness_artifact(
            "codex",
            executable="/private/controller/hindsight-memory",
            state_dir="/private/controller/state",
            locator_dir="/private/controller/locators",
        )
        serialized = artifact.to_dict()
        self.assertEqual(
            set(serialized),
            {"schema_version", "harness_id", "artifact_digest", "hooks_digest", "settings_digest", "tools_digest"},
        )
        self.assertNotIn("/private/controller", json.dumps(serialized))

    def test_native_artifacts_stage_as_one_immutable_content_addressed_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.chmod(root, 0o700)
            artifacts = {
                harness: render_native_harness_artifact(
                    harness,
                    executable="/opt/hindsight/bin/hindsight-memory",
                    state_dir="/var/lib/hindsight-controller",
                    locator_dir="/run/user/1000/hindsight-locators",
                )
                for harness in ("codex", "claude-code", "cursor")
            }
            staged = stage_native_harness_artifacts(root, artifacts)
            self.assertRegex(staged.name, r"^[0-9a-f]{64}$")
            self.assertEqual(stage_native_harness_artifacts(root, artifacts), staged)
            for harness in artifacts:
                for name in ("hooks.json", "settings.json", "tools.json", "artifact.json"):
                    path = staged / harness / name
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            manifest = json.loads((staged / "manifest.json").read_text())
            self.assertEqual(set(manifest["artifacts"]), set(artifacts))
            serialized = json.dumps(manifest).lower()
            self.assertNotIn("/opt/", serialized)
            self.assertNotIn("bank", serialized)
            self.assertNotIn("token", serialized)
            verified = verify_native_harness_generation(staged)
            self.assertEqual(set(verified), set(artifacts))

            original_lstat = Path.lstat

            def remove_during_verification(path):
                if path == staged / "codex":
                    raise FileNotFoundError(path)
                return original_lstat(path)

            with patch.object(Path, "lstat", remove_during_verification):
                with self.assertRaisesRegex(
                    ValueError, "staged harness directory is invalid"
                ):
                    verify_native_harness_generation(staged)

            (staged / "codex" / "hooks.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "digest|conflict"):
                verify_native_harness_generation(staged)
            with self.assertRaisesRegex(ValueError, "digest|conflict"):
                stage_native_harness_artifacts(root, artifacts)

    def test_native_artifacts_reject_a_symlinked_staging_root(self):
        with tempfile.TemporaryDirectory() as directory:
            actual = Path(directory) / "actual"
            actual.mkdir(mode=0o700)
            linked = Path(directory) / "linked"
            linked.symlink_to(actual, target_is_directory=True)
            artifact = render_native_harness_artifact(
                "codex",
                executable="/opt/hindsight/bin/hindsight-memory",
                state_dir="/var/lib/hindsight-controller",
                locator_dir="/run/user/1000/hindsight-locators",
            )
            with self.assertRaisesRegex(ValueError, "private current-user"):
                stage_native_harness_artifacts(linked, {"codex": artifact})


class HarnessActivationTest(unittest.TestCase):
    def setUp(self):
        self.current = {"active": False, "unknown": {"registration": "keep"}}
        self.rendered = render_harness(
            self.current,
            harness_id="codex",
            adapter="hindsight-codex",
            socket_path="/Users/example/.local/state/hindsight-memory/broker.sock",
        )
        self.plan = activation_plan(self.rendered, **DIGESTS)

    def apply(self, current=None, **overrides):
        selected_current = self.current if current is None else current
        destination = deep_thaw(selected_current)

        def write(expected_digest, configuration):
            if digest(destination) != expected_digest:
                raise ActivationCASMismatch("configuration CAS mismatch")
            destination.clear()
            destination.update(deep_thaw(configuration))
            return configuration

        gates = {
            **DIGESTS,
            "approved_plan_digest": self.plan.plan_digest,
            "broker_healthy": True,
            "profile_healthy": True,
            "adapter_self_test": True,
            "persist_rollback_prestate": lambda _prestate: None,
            "read_rollback_prestate": lambda: selected_current,
            "write_configuration": write,
            "read_configuration": lambda: destination,
            "postcheck": lambda *_evidence: True,
            "destination_harness_id": self.plan.harness_id,
        }
        gates.update(overrides)
        return apply_activation(self.plan, selected_current, **gates)

    def test_plan_is_immutable_digest_bound_and_names_required_gates(self):
        self.assertEqual(self.plan.harness_id, "codex")
        self.assertEqual(self.plan.inventory_digest, DIGESTS["inventory_digest"])
        self.assertRegex(self.plan.plan_digest, r"^[0-9a-f]{64}$")
        self.assertEqual(
            self.plan.requirements,
            ("broker_healthy", "profile_healthy", "adapter_self_test"),
        )
        self.assertIs(self.plan.owned_target["active"], True)
        with self.assertRaises(FrozenInstanceError):
            self.plan.plan_digest = "0" * 64

    def test_apply_changes_only_owned_active_after_all_fresh_gates_pass(self):
        evidence = []
        outcome = self.apply(
            postcheck=lambda *binding: evidence.append(binding) is None
        )
        self.assertEqual((outcome.status, outcome.activation_state), ("activated", "active"))
        self.assertEqual(
            evidence,
            [
                (
                    self.plan.plan_digest,
                    digest(outcome.configuration),
                    self.plan.harness_id,
                )
            ],
        )
        self.assertIs(outcome.configuration["active"], True)
        self.assertEqual(outcome.configuration["unknown"], {"registration": "keep"})
        for key in OWNED_KEYS - {"active"}:
            self.assertEqual(outcome.configuration[key], self.rendered.rendered[key])
        with self.assertRaises(TypeError):
            outcome.configuration["active"] = False
        with self.assertRaises(TypeError):
            outcome.configuration["unknown"]["registration"] = "changed"

    def test_activation_dataclass_reprs_do_not_expose_configuration(self):
        rendered = render_harness(
            {"customSecret": "render-secret"},
            harness_id="codex",
            adapter="hindsight-codex",
            socket_path="/tmp/broker.sock",
        )
        plan = activation_plan(rendered, **DIGESTS)
        plan = replace(plan, owned_target={"secret": "plan-secret"})
        outcome = self.apply(current={"active": False, "secret": "outcome-secret"})
        for secret in ("render-secret", "plan-secret", "outcome-secret"):
            self.assertNotIn(secret, repr(rendered))
            self.assertNotIn(secret, repr(plan))
            self.assertNotIn(secret, repr(outcome))

    def test_apply_refuses_digest_health_self_test_or_exact_prestate_drift(self):
        cases = (
            ({"inventory_digest": "0" * 64}, "inventory_digest_changed"),
            ({"artifact_digest": "0" * 64}, "artifact_digest_changed"),
            ({"policy_digest": "0" * 64}, "policy_digest_changed"),
            ({"approved_plan_digest": "0" * 64}, "plan_not_approved"),
            ({"broker_healthy": False}, "broker_unhealthy"),
            ({"profile_healthy": False}, "profile_unhealthy"),
            ({"adapter_self_test": False}, "adapter_self_test_failed"),
        )
        for overrides, reason in cases:
            with self.subTest(reason=reason):
                outcome = self.apply(**overrides)
                self.assertEqual((outcome.status, outcome.reason), ("refused", reason))
                self.assertEqual(outcome.activation_state, "inactive")

        drifted = dict(self.current)
        drifted["adapter"] = None
        outcome = self.apply(drifted)
        self.assertEqual((outcome.status, outcome.reason), ("refused", "owned_prestate_changed"))
        self.assertEqual(outcome.activation_state, "inactive")

        registration_drifted = dict(self.current)
        registration_drifted["unknown"] = {"registration": "changed"}
        outcome = self.apply(registration_drifted)
        self.assertEqual((outcome.status, outcome.reason), ("refused", "prestate_changed"))
        self.assertEqual(outcome.activation_state, "inactive")

    def test_failed_postcheck_rolls_back_exact_owned_values_and_unknowns_stay(self):
        outcome = self.apply(postcheck=lambda *_evidence: False)
        self.assertEqual((outcome.status, outcome.reason), ("rolled_back", "postcheck_failed"))
        self.assertTrue(outcome.rollback_attempted)
        self.assertTrue(outcome.rollback_succeeded)
        self.assertEqual(outcome.activation_state, "inactive")
        self.assertEqual(outcome.configuration, self.current)

    def test_activation_readback_failure_uses_cas_and_reports_restored_prestate(self):
        destination = deep_thaw(self.current)
        reads = 0

        def write(expected_digest, configuration):
            self.assertEqual(digest(destination), expected_digest)
            destination.clear()
            destination.update(deep_thaw(configuration))
            return deep_thaw(destination)

        def read():
            nonlocal reads
            reads += 1
            if reads == 2:
                raise OSError("readback unavailable")
            return deep_thaw(destination)

        outcome = self.apply(write_configuration=write, read_configuration=read)
        self.assertEqual(
            (outcome.status, outcome.reason),
            ("rolled_back", "activation_readback_failed"),
        )
        self.assertTrue(outcome.rollback_succeeded)
        self.assertEqual(outcome.configuration, self.current)

    def test_plan_serialization_contains_no_owned_prestate_credentials(self):
        current = {
            "broker": {"transport": "tcp", "token": "broker-secret"},
            "adapter": {"apiKey": "adapter-secret"},
            "active": False,
        }
        rendered = render_harness(
            current,
            harness_id="codex",
            adapter="hindsight-codex",
            socket_path="/Users/example/.local/state/hindsight-memory/broker.sock",
        )
        plan = activation_plan(rendered, **DIGESTS)

        serialized = json.dumps({"rendered": rendered.to_dict(), "plan": plan.to_dict()})
        self.assertNotIn("broker-secret", serialized)
        self.assertNotIn("adapter-secret", serialized)

    def test_apply_rejects_a_self_consistent_plan_without_the_approved_digest(self):
        target = dict(self.plan.owned_target)
        target["broker"] = {"transport": "unix", "path": "/tmp/unapproved.sock", "scope": "user"}
        tampered = replace(self.plan, owned_target=target, plan_digest="0" * 64)
        tampered = replace(tampered, plan_digest=digest(tampered.body()))

        outcome = apply_activation(
            tampered,
            self.current,
            **DIGESTS,
            approved_plan_digest=self.plan.plan_digest,
            broker_healthy=True,
            profile_healthy=True,
            adapter_self_test=True,
            persist_rollback_prestate=lambda _prestate: None,
            write_configuration=lambda configuration: configuration,
            postcheck=lambda *_evidence: True,
            destination_harness_id=tampered.harness_id,
        )
        self.assertEqual((outcome.status, outcome.reason), ("refused", "plan_not_approved"))

    def test_apply_rejects_self_consistent_unsupported_owned_targets(self):
        cases = {
            "boolean schema": {"schemaVersion": True},
            "network broker": {
                "broker": {
                    "transport": "tcp",
                    "path": "/tmp/hindsight.sock",
                    "scope": "user",
                }
            },
            "unknown broker option": {
                "broker": {
                    "transport": "unix",
                    "path": "/tmp/hindsight.sock",
                    "scope": "user",
                    "token": "not-supported",
                }
            },
            "relative broker path": {
                "broker": {
                    "transport": "unix",
                    "path": "relative.sock",
                    "scope": "user",
                }
            },
            "unsupported scope": {
                "broker": {
                    "transport": "unix",
                    "path": "/tmp/hindsight.sock",
                    "scope": "system",
                }
            },
            "non-string adapter": {"adapter": {"id": "hindsight-codex"}},
        }
        for label, replacements in cases.items():
            with self.subTest(label=label):
                target = {**self.plan.owned_target, **replacements}
                forged = replace(self.plan, owned_target=target)
                forged = replace(forged, plan_digest=digest(forged.body()))
                outcome = apply_activation(
                    forged,
                    self.current,
                    **DIGESTS,
                    approved_plan_digest=forged.plan_digest,
                    broker_healthy=True,
                    profile_healthy=True,
                    adapter_self_test=True,
                    persist_rollback_prestate=lambda _prestate: None,
                    write_configuration=lambda configuration: configuration,
                    postcheck=lambda *_evidence: True,
                    destination_harness_id=forged.harness_id,
                )
                self.assertEqual(
                    (outcome.status, outcome.reason),
                    ("refused", "invalid_plan"),
                )

        forged = replace(self.plan, harness_id="unsupported")
        forged = replace(forged, plan_digest=digest(forged.body()))
        outcome = apply_activation(
            forged,
            self.current,
            **DIGESTS,
            approved_plan_digest=forged.plan_digest,
            broker_healthy=True,
            profile_healthy=True,
            adapter_self_test=True,
            persist_rollback_prestate=lambda _prestate: None,
            write_configuration=lambda configuration: configuration,
            postcheck=lambda *_evidence: True,
            destination_harness_id=forged.harness_id,
        )
        self.assertEqual((outcome.status, outcome.reason), ("refused", "invalid_plan"))

    def test_invalid_plan_objects_are_refused_without_dereferencing_them(self):
        for invalid in (None, object(), {"plan_digest": "not-a-plan"}):
            with self.subTest(invalid=type(invalid).__name__):
                outcome = apply_activation(
                    invalid,
                    self.current,
                    **DIGESTS,
                    approved_plan_digest="0" * 64,
                    broker_healthy=True,
                    profile_healthy=True,
                    adapter_self_test=True,
                    persist_rollback_prestate=lambda _prestate: None,
                    write_configuration=lambda configuration: configuration,
                    postcheck=lambda *_evidence: True,
                    destination_harness_id="invalid",
                )
                self.assertEqual(
                    (outcome.status, outcome.reason, outcome.plan_digest),
                    ("refused", "invalid_plan", ""),
                )
                rollback = rollback_activation(
                    invalid,
                    self.current,
                    approved_plan_digest="0" * 64,
                    prestate=self.current,
                    destination_harness_id="invalid",
                )
                self.assertEqual(
                    (rollback.status, rollback.reason, rollback.plan_digest),
                    ("refused", "invalid_plan", ""),
                )

        forged = replace(self.plan, plan_digest=None)
        outcome = apply_activation(
            forged,
            self.current,
            **DIGESTS,
            approved_plan_digest="0" * 64,
            broker_healthy=True,
            profile_healthy=True,
            adapter_self_test=True,
            persist_rollback_prestate=lambda _prestate: None,
            write_configuration=lambda configuration: configuration,
            postcheck=lambda *_evidence: True,
            destination_harness_id=forged.harness_id,
        )
        self.assertEqual(
            (outcome.status, outcome.reason), ("refused", "invalid_plan")
        )

    def test_failed_automatic_rollback_is_not_reported_as_rolled_back(self):
        failed = type(
            "FailedRollback",
            (),
            {"status": "refused", "configuration": {"active": True}},
        )()
        with patch(
            "hindsight_memory_control_plane.harnesses.rollback_activation",
            return_value=failed,
        ):
            outcome = self.apply(postcheck=lambda *_evidence: False)
        self.assertEqual(outcome.status, "rollback_failed")
        self.assertTrue(outcome.rollback_attempted)
        self.assertFalse(outcome.rollback_succeeded)
        self.assertIs(outcome.configuration["active"], True)

    def test_activation_persists_prestate_and_writes_before_postcheck(self):
        events = []
        destination = deep_thaw(self.current)

        def persist(prestate):
            events.append(("prestate", deep_thaw(prestate)))

        def write(expected_digest, configuration):
            events.append(("write", expected_digest, deep_thaw(configuration)))
            destination.update(deep_thaw(configuration))
            return configuration

        outcome = self.apply(
            persist_rollback_prestate=persist,
            write_configuration=write,
            read_configuration=lambda: destination,
            postcheck=lambda *_evidence: events.append(("postcheck", None)) is None,
        )
        self.assertEqual(outcome.status, "activated")
        self.assertEqual([event[0] for event in events], ["prestate", "write", "postcheck"])
        self.assertEqual(events[0][1], self.current)
        self.assertEqual(events[1][1], digest(self.current))
        self.assertIs(events[1][2]["active"], True)

    def test_activation_requires_postcheck_before_persistence_or_write(self):
        events = []
        outcome = self.apply(
            persist_rollback_prestate=lambda _prestate: events.append(
                "prestate"
            ),
            write_configuration=lambda *_args: events.append("write"),
            postcheck=None,
        )
        self.assertEqual(
            (outcome.status, outcome.reason),
            ("refused", "activation_postcheck_unavailable"),
        )
        self.assertEqual(events, [])

    def test_activation_requires_durable_rollback_prestate_readback(self):
        writes = []
        unavailable = self.apply(
            read_rollback_prestate=None,
            write_configuration=lambda *_args: writes.append("write"),
        )
        self.assertEqual(
            (unavailable.status, unavailable.reason),
            ("refused", "rollback_prestate_reader_unavailable"),
        )

        unreadable = self.apply(
            read_rollback_prestate=lambda: (_ for _ in ()).throw(
                OSError("durable receipt unavailable")
            ),
            write_configuration=lambda *_args: writes.append("write"),
        )
        self.assertEqual(
            (unreadable.status, unreadable.reason),
            ("refused", "rollback_prestate_readback_failed"),
        )

        mismatched = self.apply(
            read_rollback_prestate=lambda: {**self.current, "active": True},
            write_configuration=lambda *_args: writes.append("write"),
        )
        self.assertEqual(
            (mismatched.status, mismatched.reason),
            ("refused", "rollback_prestate_readback_mismatch"),
        )
        self.assertEqual(writes, [])

    def test_activation_write_exception_restores_and_verifies_prestate(self):
        destination = deep_thaw(self.current)

        def commit_then_raise(_expected_digest, configuration):
            destination.clear()
            destination.update(deep_thaw(configuration))
            destination["unknown"] = {"registration": "concurrent"}
            raise OSError("write interrupted after commit")

        writes = [commit_then_raise]

        def write(expected_digest, configuration):
            writer = writes.pop(0) if writes else None
            if writer is not None:
                return writer(expected_digest, configuration)
            if digest(destination) != expected_digest:
                raise ActivationCASMismatch("configuration CAS mismatch")
            destination.clear()
            destination.update(deep_thaw(configuration))
            return configuration

        outcome = self.apply(
            write_configuration=write,
            read_configuration=lambda: destination,
        )
        self.assertEqual(
            (outcome.status, outcome.reason),
            ("rolled_back", "activation_write_failed"),
        )
        self.assertTrue(outcome.rollback_succeeded)
        expected = deep_thaw(self.current)
        expected["unknown"] = {"registration": "concurrent"}
        self.assertEqual(outcome.configuration, expected)

    def test_activation_readback_mismatch_rolls_back_only_owned_fields(self):
        destination = deep_thaw(self.current)
        writes = 0

        def write(expected_digest, configuration):
            nonlocal writes
            self.assertEqual(digest(destination), expected_digest)
            destination.clear()
            destination.update(deep_thaw(configuration))
            writes += 1
            if writes == 1:
                destination["adapter"] = "wrong-adapter"
                destination["unknown"] = {"registration": "concurrent"}
            return deep_thaw(destination)

        outcome = self.apply(
            write_configuration=write,
            read_configuration=lambda: deep_thaw(destination),
        )
        self.assertEqual(
            (outcome.status, outcome.reason),
            ("rollback_failed", "activation_readback_mismatch"),
        )
        self.assertFalse(outcome.rollback_succeeded)
        expected = deep_thaw(destination)
        self.assertEqual(outcome.configuration, expected)

    def test_activation_cas_rechecks_persisted_prestate_before_writing(self):
        destination = deep_thaw(self.current)
        writes = []

        def persist(_prestate):
            destination["concurrent"] = "preserve"

        outcome = self.apply(
            persist_rollback_prestate=persist,
            write_configuration=lambda _expected_digest, configuration: writes.append(configuration),
            read_configuration=lambda: destination,
        )

        self.assertEqual(
            (outcome.status, outcome.reason), ("refused", "prestate_changed")
        )
        self.assertEqual(writes, [])
        self.assertEqual(destination["concurrent"], "preserve")

    def test_postcheck_rollback_cas_preserves_concurrent_destination_change(self):
        destination = deep_thaw(self.current)
        writes = []

        def write(expected_digest, configuration):
            if digest(destination) != expected_digest:
                raise ActivationCASMismatch("configuration CAS mismatch")
            writes.append(deep_thaw(configuration))
            destination.clear()
            destination.update(deep_thaw(configuration))
            return configuration

        def postcheck(*_evidence):
            destination["concurrent"] = "preserve"
            return False

        outcome = self.apply(
            write_configuration=write,
            read_configuration=lambda: destination,
            postcheck=postcheck,
        )

        self.assertEqual(
            (outcome.status, outcome.reason),
            ("rolled_back", "postcheck_failed"),
        )
        self.assertEqual(len(writes), 2)
        self.assertEqual(destination["concurrent"], "preserve")
        self.assertEqual(outcome.configuration, destination)
        self.assertFalse(destination["active"])

    def test_invalid_unhashable_retired_keys_fail_closed(self):
        forged = replace(self.plan, retired_keys=([],))
        outcome = apply_activation(
            forged,
            self.current,
            **DIGESTS,
            approved_plan_digest=self.plan.plan_digest,
            broker_healthy=True,
            profile_healthy=True,
            adapter_self_test=True,
            persist_rollback_prestate=lambda _prestate: None,
            write_configuration=lambda configuration: configuration,
            postcheck=lambda *_evidence: True,
            destination_harness_id=forged.harness_id,
        )
        self.assertEqual((outcome.status, outcome.reason), ("refused", "invalid_plan"))

    def test_apply_requires_exact_retired_direct_credential_keys(self):
        current = {**self.current, "tenantToken": "direct-secret"}
        rendered = render_harness(
            current,
            harness_id="codex",
            adapter="hindsight-codex",
            socket_path="/tmp/hindsight.sock",
        )
        plan = activation_plan(rendered, current=current, **DIGESTS)
        forged = replace(plan, retired_keys=())
        forged = replace(forged, plan_digest=digest(forged.body()))
        outcome = apply_activation(
            forged,
            current,
            **DIGESTS,
            approved_plan_digest=forged.plan_digest,
            broker_healthy=True,
            profile_healthy=True,
            adapter_self_test=True,
            persist_rollback_prestate=lambda _prestate: None,
            write_configuration=lambda configuration: configuration,
            postcheck=lambda *_evidence: True,
            destination_harness_id=forged.harness_id,
        )
        self.assertEqual(
            (outcome.status, outcome.reason),
            ("refused", "retired_keys_changed"),
        )

    def test_failed_postcheck_restores_an_exact_active_prestate(self):
        original = {
            "schemaVersion": None,
            "active": True,
            "unknown": {"registration": "keep"},
        }
        rendered = render_harness(
            original,
            harness_id="codex",
            adapter="hindsight-codex",
            socket_path="/Users/example/.local/state/hindsight-memory/broker.sock",
        )
        plan = activation_plan(rendered, **DIGESTS)
        activated = dict(rendered.rendered)
        activated["active"] = True
        destination = deep_thaw(original)

        def write(_expected_digest, configuration):
            destination.clear()
            destination.update(deep_thaw(configuration))
            return configuration

        exact_rollback = rollback_activation(
            plan,
            activated,
            approved_plan_digest=plan.plan_digest,
            prestate=original,
            destination_harness_id=plan.harness_id,
        )
        self.assertEqual(exact_rollback.configuration, original)

        outcome = apply_activation(
            plan,
            original,
            **DIGESTS,
            approved_plan_digest=plan.plan_digest,
            broker_healthy=True,
            profile_healthy=True,
            adapter_self_test=True,
            persist_rollback_prestate=lambda _prestate: None,
            read_rollback_prestate=lambda: original,
            write_configuration=write,
            read_configuration=lambda: destination,
            postcheck=lambda *_evidence: False,
            destination_harness_id=plan.harness_id,
        )

        self.assertEqual((outcome.status, outcome.reason), ("rolled_back", "postcheck_failed"))
        self.assertEqual(outcome.activation_state, "active")
        self.assertTrue(outcome.rollback_attempted)
        self.assertTrue(outcome.rollback_succeeded)
        self.assertEqual(outcome.configuration, original)

    def test_rollback_success_is_verified_from_persisted_destination(self):
        destination = deep_thaw(self.current)
        writes = 0

        def write(_expected_digest, configuration):
            nonlocal writes
            writes += 1
            destination.clear()
            destination.update(deep_thaw(configuration))
            if writes == 2:
                destination["active"] = True
            return configuration

        outcome = self.apply(
            write_configuration=write,
            read_configuration=lambda: destination,
            postcheck=lambda *_evidence: False,
        )

        self.assertEqual(outcome.status, "rollback_failed")
        self.assertFalse(outcome.rollback_succeeded)
        self.assertIs(outcome.configuration["active"], True)

    def test_postcheck_rollback_requires_complete_persisted_target(self):
        destination = deep_thaw(self.current)
        writes = 0

        def write(_expected_digest, configuration):
            nonlocal writes
            writes += 1
            destination.clear()
            destination.update(deep_thaw(configuration))
            if writes == 2:
                destination["concurrent-registration"] = {"id": "preserve"}
            return configuration

        outcome = self.apply(
            write_configuration=write,
            read_configuration=lambda: destination,
            postcheck=lambda *_evidence: False,
        )

        self.assertEqual(outcome.status, "rollback_failed")
        self.assertFalse(outcome.rollback_succeeded)
        self.assertEqual(
            outcome.configuration["concurrent-registration"],
            {"id": "preserve"},
        )

    def test_rollback_restores_missing_and_explicit_null_without_unknown_changes(self):
        prestate = render_harness(
            {"schemaVersion": None, "unknown": ["keep"]},
            harness_id="cursor",
            adapter="hindsight-cursor",
            socket_path="/Users/example/.local/state/hindsight-memory/broker.sock",
        )
        original = {"schemaVersion": None, "unknown": ["keep"]}
        plan = activation_plan(prestate, **DIGESTS)
        activated = {**prestate.rendered, "active": True, "unknown": ["keep"]}
        outcome = rollback_activation(
            plan,
            activated,
            approved_plan_digest=plan.plan_digest,
            prestate=original,
            destination_harness_id=plan.harness_id,
        )
        self.assertEqual(outcome.status, "rollback_ready")
        self.assertFalse(outcome.rollback_attempted)
        self.assertFalse(outcome.rollback_succeeded)
        self.assertIsNone(outcome.configuration["schemaVersion"])
        self.assertNotIn("broker", outcome.configuration)
        self.assertNotIn("adapter", outcome.configuration)
        self.assertNotIn("active", outcome.configuration)
        self.assertEqual(outcome.configuration["unknown"], ("keep",))
        self.assertNotIn("configuration", outcome.to_dict())

    def test_activation_removes_retired_direct_fields_and_rollback_restores_snapshot(self):
        original = {
            "hindsightApiUrl": "http://localhost:7979",
            "bankId": "engineering",
            "tenantToken": "tenant-secret",
            "active": False,
            "registrations": [{"id": "third-party"}],
        }
        rendered = render_harness(
            original,
            harness_id="cursor",
            adapter="hindsight-cursor",
            socket_path="/Users/example/.local/state/hindsight-memory/broker.sock",
        )
        plan = activation_plan(rendered, **DIGESTS)
        destination = deep_thaw(original)

        def write(_expected_digest, configuration):
            destination.clear()
            destination.update(deep_thaw(configuration))
            return configuration

        outcome = apply_activation(
            plan,
            original,
            **DIGESTS,
            approved_plan_digest=plan.plan_digest,
            broker_healthy=True,
            profile_healthy=True,
            adapter_self_test=True,
            persist_rollback_prestate=lambda _prestate: None,
            read_rollback_prestate=lambda: original,
            write_configuration=write,
            read_configuration=lambda: destination,
            postcheck=lambda *_evidence: True,
            destination_harness_id=plan.harness_id,
        )
        self.assertEqual((outcome.status, outcome.activation_state), ("activated", "active"))
        self.assertNotIn("hindsightApiUrl", outcome.configuration)
        self.assertNotIn("bankId", outcome.configuration)
        self.assertNotIn("tenantToken", outcome.configuration)
        self.assertEqual(outcome.configuration["registrations"][0]["id"], "third-party")
        self.assertNotIn("configuration", outcome.to_dict())

        rollback = rollback_activation(
            plan,
            outcome.configuration,
            approved_plan_digest=plan.plan_digest,
            prestate=original,
            destination_harness_id=plan.harness_id,
        )
        serialized = rollback.to_dict()
        self.assertEqual(
            set(serialized),
            {
                "status",
                "reason",
                "configuration_digest",
                "preserved_config_digest",
                "preserved_config_present",
                "activation_state",
                "plan_digest",
                "rollback_attempted",
                "rollback_succeeded",
            },
        )
        self.assertNotIn("configuration", serialized)
        self.assertNotIn("tenant-secret", json.dumps(serialized))
        self.assertNotIn("third-party", json.dumps(serialized))
        self.assertTrue(serialized["preserved_config_present"])
        self.assertEqual(rollback.configuration["tenantToken"], "tenant-secret")
        self.assertEqual(rollback.configuration["registrations"][0]["id"], "third-party")

    def test_rollback_requires_approval_and_preserves_unrelated_current_changes(self):
        original = {
            "hindsightApiUrl": "http://localhost:7979",
            "active": False,
            "registrations": [{"id": "before"}],
        }
        rendered = render_harness(
            original,
            harness_id="codex",
            adapter="hindsight-codex",
            socket_path="/Users/example/.local/state/hindsight-memory/broker.sock",
        )
        plan = activation_plan(rendered, **DIGESTS)
        activated = dict(rendered.rendered)
        activated["active"] = True
        activated["registrations"] = [{"id": "after"}]

        refused = rollback_activation(
            plan,
            activated,
            approved_plan_digest="0" * 64,
            prestate=original,
            destination_harness_id=plan.harness_id,
        )
        self.assertEqual((refused.status, refused.reason), ("refused", "plan_not_approved"))

        target = dict(plan.owned_target)
        target["adapter"] = "unapproved-adapter"
        tampered = replace(plan, owned_target=target, plan_digest="0" * 64)
        tampered = replace(tampered, plan_digest=digest(tampered.body()))
        refused = rollback_activation(
            tampered,
            activated,
            approved_plan_digest=plan.plan_digest,
            prestate=original,
            destination_harness_id=tampered.harness_id,
        )
        self.assertEqual((refused.status, refused.reason), ("refused", "plan_not_approved"))

        outcome = rollback_activation(
            plan,
            activated,
            approved_plan_digest=plan.plan_digest,
            prestate=original,
            destination_harness_id=plan.harness_id,
        )
        self.assertEqual((outcome.status, outcome.reason), ("rollback_ready", "ok"))
        self.assertFalse(outcome.rollback_attempted)
        self.assertFalse(outcome.rollback_succeeded)
        self.assertEqual(outcome.configuration["registrations"][0]["id"], "after")
        self.assertNotIn("configuration", outcome.to_dict())
        self.assertEqual(outcome.configuration["hindsightApiUrl"], "http://localhost:7979")
        self.assertIs(outcome.configuration["active"], False)


class NativeHarnessActivationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.staging_root = Path(self.temporary.name)
        os.chmod(self.staging_root, 0o700)
        self.artifact = render_native_harness_artifact(
            "claude-code",
            executable="/opt/hindsight/bin/hindsight-memory",
            state_dir="/var/lib/hindsight-controller",
            locator_dir="/run/user/1000/hindsight-locators",
        )
        self.current = {
            "hooks": {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/session_start.py\" || python \"${CLAUDE_PLUGIN_ROOT}/scripts/session_start.py\"",
                                    "timeout": 5,
                                }
                            ]
                        }
                    ],
                    "UserPromptSubmit": [
                        {"hooks": [{"type": "command", "command": "third-party recall", "timeout": 3}]},
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python3 \"/tmp/hindsight-unrelated/scripts/retain.py\"",
                                    "timeout": 4,
                                }
                            ]
                        },
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/recall.py\" || python \"${CLAUDE_PLUGIN_ROOT}/scripts/recall.py\"",
                                    "timeout": 12,
                                }
                            ]
                        },
                    ],
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/retain.py\" || python \"${CLAUDE_PLUGIN_ROOT}/scripts/retain.py\"",
                                    "timeout": 15,
                                    "async": True,
                                }
                            ]
                        }
                    ],
                },
                "thirdPartyManifest": True,
            },
            "settings": {
                "theme": "warm",
                "autoRecall": True,
                "autoRetain": True,
                "enableKnowledgeTools": True,
            },
            "tools": {"unrelated": {"command": "other-tool"}},
            "registrations": [{"id": "unrelated"}],
        }
        self.staged_generation = stage_native_harness_artifacts(
            self.staging_root, {"claude-code": self.artifact}
        )
        self.plan = native_activation_plan(
            self.artifact,
            self.current,
            staged_generation=self.staged_generation,
            **DIGESTS,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_plan_semantically_merges_only_memory_owned_surfaces(self):
        self.assertIsInstance(self.plan, NativeActivationPlan)
        target = deep_thaw(self.plan.target)
        self.assertEqual(target["settings"]["theme"], "warm")
        self.assertFalse(target["settings"]["autoRecall"])
        self.assertFalse(target["settings"]["autoRetain"])
        self.assertFalse(target["settings"]["enableKnowledgeTools"])
        self.assertEqual(target["registrations"], [{"id": "unrelated"}])
        self.assertIn("unrelated", target["tools"])
        prompt_hooks = target["hooks"]["hooks"]["UserPromptSubmit"]
        self.assertEqual(prompt_hooks[0]["hooks"][0]["command"], "third-party recall")
        self.assertEqual(len(prompt_hooks), 3)
        self.assertIn("hindsight-unrelated", prompt_hooks[1]["hooks"][0]["command"])
        self.assertIn("hindsight-memory", prompt_hooks[2]["hooks"][0]["command"])
        serialized_hooks = json.dumps(target["hooks"])
        self.assertNotIn("CLAUDE_PLUGIN_ROOT", serialized_hooks)
        self.assertNotIn("SessionStart", target["hooks"]["hooks"])
        self.assertFalse(target["settings"]["enableKnowledgeTools"])
        self.assertNotEqual(self.plan.retired_direct_hooks_digest, digest({}))
        self.assertEqual(
            self.plan.staged_generation_digest,
            self.staged_generation.name,
        )
        self.assertNotIn("/opt/hindsight", json.dumps(self.plan.to_dict()))

    def test_plan_rejects_an_unverified_or_corrupt_staged_generation(self):
        (self.staged_generation / "claude-code" / "tools.json").unlink()
        with self.assertRaisesRegex(ValueError, "unavailable|unexpected|invalid"):
            native_activation_plan(
                self.artifact,
                self.current,
                staged_generation=self.staged_generation,
                **DIGESTS,
            )

    def test_plan_retires_absolute_hooks_only_under_verified_integration_roots(self):
        integration_root = self.staging_root / "upstream-integration"
        scripts = integration_root / "scripts"
        scripts.mkdir(parents=True, mode=0o700)
        (scripts / "recall.py").write_text("# upstream\n", encoding="utf-8")
        current = deep_thaw(self.current)
        current["hooks"]["hooks"]["UserPromptSubmit"].append(
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": f'python3 "{scripts / "recall.py"}"',
                        "timeout": 12,
                    }
                ]
            }
        )
        plan = native_activation_plan(
            self.artifact,
            current,
            staged_generation=self.staged_generation,
            upstream_integration_roots=(integration_root,),
            **DIGESTS,
        )
        self.assertNotIn(
            str(integration_root),
            json.dumps(deep_thaw(plan.target)["hooks"]),
        )

    def test_plan_rejects_degenerate_roots_and_preserves_substring_matches(self):
        with self.assertRaisesRegex(ValueError, "unsafe|unavailable"):
            native_activation_plan(
                self.artifact,
                self.current,
                staged_generation=self.staged_generation,
                upstream_integration_roots=(Path("/"),),
                **DIGESTS,
            )

        integration_root = self.staging_root / "verified-upstream"
        scripts = integration_root / "scripts"
        scripts.mkdir(parents=True, mode=0o700)
        script = scripts / "retain.py"
        script.write_text("# upstream\n", encoding="utf-8")
        current = deep_thaw(self.current)
        malicious = {
            "hooks": [
                {
                    "type": "command",
                    "command": f'python3 "{script}.old"',
                    "timeout": 12,
                }
            ]
        }
        current["hooks"]["hooks"]["Stop"].append(malicious)
        plan = native_activation_plan(
            self.artifact,
            current,
            staged_generation=self.staged_generation,
            upstream_integration_roots=(integration_root,),
            **DIGESTS,
        )
        self.assertIn(malicious, deep_thaw(plan.target)["hooks"]["hooks"]["Stop"])

    def test_apply_uses_cas_readback_and_postcheck(self):
        persisted = deep_thaw(self.current)
        rollback = []

        def write(expected_digest, value):
            nonlocal persisted
            if digest(persisted) != expected_digest:
                raise ActivationCASMismatch("changed")
            persisted = deep_thaw(value)

        outcome = apply_native_activation(
            self.plan,
            self.current,
            staged_generation=self.staged_generation,
            approved_plan_digest=self.plan.plan_digest,
            inventory_digest=DIGESTS["inventory_digest"],
            artifact_digest=DIGESTS["artifact_digest"],
            policy_digest=DIGESTS["policy_digest"],
            broker_healthy=True,
            profile_healthy=True,
            adapter_self_test=True,
            persist_rollback=lambda value: rollback.append(deep_thaw(value)),
            read_rollback=lambda: deep_thaw(rollback[-1]),
            read_configuration=lambda: deep_thaw(persisted),
            write_configuration=write,
            postcheck=lambda plan_digest, target_digest, harness: (
                plan_digest == self.plan.plan_digest
                and target_digest == self.plan.target_digest
                and harness == "claude-code"
            ),
        )
        self.assertEqual((outcome.status, outcome.reason), ("activated", "ok"))
        self.assertEqual(rollback, [self.current])
        self.assertEqual(persisted, deep_thaw(self.plan.target))

    def test_failed_postcheck_restores_exact_prestate(self):
        persisted = deep_thaw(self.current)
        rollback = []

        def write(expected_digest, value):
            nonlocal persisted
            if digest(persisted) != expected_digest:
                raise ActivationCASMismatch("changed")
            persisted = deep_thaw(value)

        outcome = apply_native_activation(
            self.plan,
            self.current,
            staged_generation=self.staged_generation,
            approved_plan_digest=self.plan.plan_digest,
            inventory_digest=DIGESTS["inventory_digest"],
            artifact_digest=DIGESTS["artifact_digest"],
            policy_digest=DIGESTS["policy_digest"],
            broker_healthy=True,
            profile_healthy=True,
            adapter_self_test=True,
            persist_rollback=lambda value: rollback.append(deep_thaw(value)),
            read_rollback=lambda: deep_thaw(rollback[-1]),
            read_configuration=lambda: deep_thaw(persisted),
            write_configuration=write,
            postcheck=lambda *_: False,
        )
        self.assertEqual((outcome.status, outcome.reason), ("rolled_back", "postcheck_failed"))
        self.assertTrue(outcome.rollback_attempted)
        self.assertTrue(outcome.rollback_succeeded)
        self.assertEqual(persisted, self.current)

    def test_write_then_raise_rolls_back_the_possibly_persisted_target(self):
        persisted = deep_thaw(self.current)
        rollback = []
        writes = 0

        def write(expected_digest, value):
            nonlocal persisted, writes
            if digest(persisted) != expected_digest:
                raise ActivationCASMismatch("changed")
            persisted = deep_thaw(value)
            writes += 1
            if writes == 1:
                raise OSError("interrupted after persistence")

        outcome = apply_native_activation(
            self.plan,
            self.current,
            staged_generation=self.staged_generation,
            approved_plan_digest=self.plan.plan_digest,
            inventory_digest=DIGESTS["inventory_digest"],
            artifact_digest=DIGESTS["artifact_digest"],
            policy_digest=DIGESTS["policy_digest"],
            broker_healthy=True,
            profile_healthy=True,
            adapter_self_test=True,
            persist_rollback=lambda value: rollback.append(deep_thaw(value)),
            read_rollback=lambda: deep_thaw(rollback[-1]),
            read_configuration=lambda: deep_thaw(persisted),
            write_configuration=write,
            postcheck=lambda *_: True,
        )
        self.assertEqual(
            (outcome.status, outcome.reason),
            ("rolled_back", "activation_write_failed"),
        )
        self.assertTrue(outcome.rollback_succeeded)
        self.assertEqual(persisted, self.current)

    def test_partial_surface_write_rolls_back_exact_hook_order(self):
        persisted = deep_thaw(self.current)
        rollback = []
        writes = 0

        def write(expected_digest, value):
            nonlocal writes
            if digest(persisted) != expected_digest:
                raise ActivationCASMismatch("changed")
            writes += 1
            if writes == 1:
                persisted["hooks"] = deep_thaw(value["hooks"])
                raise OSError("interrupted after hooks")
            persisted.clear()
            persisted.update(deep_thaw(value))

        outcome = apply_native_activation(
            self.plan,
            self.current,
            staged_generation=self.staged_generation,
            approved_plan_digest=self.plan.plan_digest,
            inventory_digest=DIGESTS["inventory_digest"],
            artifact_digest=DIGESTS["artifact_digest"],
            policy_digest=DIGESTS["policy_digest"],
            broker_healthy=True,
            profile_healthy=True,
            adapter_self_test=True,
            persist_rollback=lambda value: rollback.append(deep_thaw(value)),
            read_rollback=lambda: deep_thaw(rollback[-1]),
            read_configuration=lambda: deep_thaw(persisted),
            write_configuration=write,
            postcheck=lambda *_: True,
        )
        self.assertEqual(outcome.status, "rolled_back")
        self.assertEqual(persisted["hooks"], self.current["hooks"])

    def test_partial_rollback_restores_duplicate_direct_hook_occurrences(self):
        current = deep_thaw(self.current)
        upstream = deep_thaw(current["hooks"]["hooks"]["Stop"][0])
        current["hooks"]["hooks"]["Stop"].insert(0, deep_thaw(upstream))
        plan = native_activation_plan(
            self.artifact,
            current,
            staged_generation=self.staged_generation,
            **DIGESTS,
        )
        persisted = deep_thaw(current)
        rollback = []
        writes = 0

        def write(expected_digest, value):
            nonlocal writes
            if digest(persisted) != expected_digest:
                raise ActivationCASMismatch("changed")
            writes += 1
            if writes == 1:
                partial = deep_thaw(value)
                partial["hooks"]["hooks"]["Stop"].insert(
                    0, deep_thaw(upstream)
                )
                persisted.clear()
                persisted.update(partial)
                raise OSError("interrupted with one duplicate restored")
            persisted.clear()
            persisted.update(deep_thaw(value))

        outcome = apply_native_activation(
            plan,
            current,
            staged_generation=self.staged_generation,
            approved_plan_digest=plan.plan_digest,
            inventory_digest=DIGESTS["inventory_digest"],
            artifact_digest=DIGESTS["artifact_digest"],
            policy_digest=DIGESTS["policy_digest"],
            broker_healthy=True,
            profile_healthy=True,
            adapter_self_test=True,
            persist_rollback=lambda value: rollback.append(deep_thaw(value)),
            read_rollback=lambda: deep_thaw(rollback[-1]),
            read_configuration=lambda: deep_thaw(persisted),
            write_configuration=write,
            postcheck=lambda *_: True,
        )
        self.assertEqual(outcome.status, "rolled_back")
        self.assertEqual(persisted["hooks"], current["hooks"])

    def test_true_postcheck_requires_a_final_exact_readback(self):
        persisted = deep_thaw(self.current)
        rollback = []

        def write(expected_digest, value):
            nonlocal persisted
            if digest(persisted) != expected_digest:
                raise ActivationCASMismatch("changed")
            persisted = deep_thaw(value)

        def drifting_postcheck(*_):
            persisted["settings"]["theme"] = "cool"
            return True

        outcome = apply_native_activation(
            self.plan,
            self.current,
            staged_generation=self.staged_generation,
            approved_plan_digest=self.plan.plan_digest,
            inventory_digest=DIGESTS["inventory_digest"],
            artifact_digest=DIGESTS["artifact_digest"],
            policy_digest=DIGESTS["policy_digest"],
            broker_healthy=True,
            profile_healthy=True,
            adapter_self_test=True,
            persist_rollback=lambda value: rollback.append(deep_thaw(value)),
            read_rollback=lambda: deep_thaw(rollback[-1]),
            read_configuration=lambda: deep_thaw(persisted),
            write_configuration=write,
            postcheck=drifting_postcheck,
        )
        self.assertEqual(
            (outcome.status, outcome.reason),
            ("rolled_back", "postcheck_drift"),
        )
        self.assertEqual(persisted["settings"]["theme"], "cool")
        self.assertTrue(persisted["settings"]["autoRecall"])

    def test_postcheck_rollback_preserves_concurrent_unrelated_changes(self):
        persisted = deep_thaw(self.current)
        rollback = []

        def write(expected_digest, value):
            nonlocal persisted
            if digest(persisted) != expected_digest:
                raise ActivationCASMismatch("changed")
            persisted = deep_thaw(value)

        def postcheck(*_):
            persisted["settings"]["theme"] = "cool"
            persisted["registrations"].append({"id": "concurrent"})
            return False

        outcome = apply_native_activation(
            self.plan,
            self.current,
            staged_generation=self.staged_generation,
            approved_plan_digest=self.plan.plan_digest,
            inventory_digest=DIGESTS["inventory_digest"],
            artifact_digest=DIGESTS["artifact_digest"],
            policy_digest=DIGESTS["policy_digest"],
            broker_healthy=True,
            profile_healthy=True,
            adapter_self_test=True,
            persist_rollback=lambda value: rollback.append(deep_thaw(value)),
            read_rollback=lambda: deep_thaw(rollback[-1]),
            read_configuration=lambda: deep_thaw(persisted),
            write_configuration=write,
            postcheck=postcheck,
        )
        self.assertEqual(outcome.status, "rolled_back")
        self.assertEqual(persisted["settings"]["theme"], "cool")
        self.assertTrue(persisted["settings"]["autoRecall"])
        self.assertEqual(
            persisted["registrations"],
            [{"id": "unrelated"}, {"id": "concurrent"}],
        )


if __name__ == "__main__":
    unittest.main()
