import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from hindsight_memory_control_plane.airlock import (
    APPROVED_DESTINATION_CATALOG,
    AirlockLaunchPlan,
    AirlockPlanError,
    CLOSEOUT_PROBES,
    PREFLIGHT_PROBES,
    validate_airlock_closeout,
    validate_airlock_plan,
)


class FakeOrbStackRunner:
    def __init__(self, failing=(), *, instance_id="airlock-1", session_id="session-1"):
        self.failing = set(failing)
        self.instance_id = instance_id
        self.session_id = session_id
        self.calls = []
        self.evidence = []

    def attest(self):
        return {
            "schema_version": 1,
            "instance_id": self.instance_id,
            "session_id": self.session_id,
        }

    def probe(self, probe, evidence):
        self.calls.append(probe)
        self.evidence.append(evidence)
        return {
            "passed": probe not in self.failing,
            "runner_attestation": self.attest(),
            "challenge": evidence["challenge"],
            "probe": evidence["probe"],
            "plan_digest": evidence["plan_digest"],
        }


class MutatingOrbStackRunner(FakeOrbStackRunner):
    def __init__(self, candidate, **identity):
        super().__init__(**identity)
        self.candidate = candidate

    def probe(self, probe, evidence):
        self.candidate["machine"]["fresh"] = False
        return super().probe(probe, evidence)


class TamperingOrbStackRunner(FakeOrbStackRunner):
    def __init__(self, field, **identity):
        super().__init__(**identity)
        self.field = field

    def probe(self, probe, evidence):
        result = super().probe(probe, evidence)
        result[self.field] = {
            "challenge": "0" * 64,
            "probe": "unexpected.probe",
            "plan_digest": "0" * 64,
        }[self.field]
        return result


def valid_candidate():
    return {
        "schema_version": 1,
        "backend": "orbstack",
        "machine": {
            "os": "linux",
            "fresh": True,
            "ephemeral": True,
            "macos_integration": False,
            "host_network": False,
            "peer_network": False,
            "separate_guest_kernel_required": True,
        },
        "mounts": {
            "inputs": [
                {"id": "task-source", "mode": "read-only", "reviewed": True},
                {
                    "id": "reviewed-bootstrap",
                    "mode": "read-only",
                    "reviewed": True,
                },
            ],
            "output": {
                "id": "encrypted-export",
                "mode": "write-only",
                "narrow": True,
            },
        },
        "egress": {
            "enforcement_owner": "root",
            "default_deny": True,
            "approved_destinations": [{
                "selector": "openai-api",
                "host": "api.openai.com",
                "port": 443,
            }],
            "harness_can_modify": False,
        },
        "harness": {
            "kind": "cli",
            "host_gui": False,
            "principal": "airlock-agent",
            "unprivileged": True,
            "sudo": False,
            "setuid_escalation": False,
            "network_admin": False,
            "container_socket": False,
        },
        "probes": {
            "tamper_denied": [
                "firewall",
                "routes",
                "dns",
                "network_namespace",
                "broker_config",
            ],
            "unreachable": [
                "host_loopback",
                "host_broker_socket",
                "core_profile_endpoints",
                "undeclared_destinations",
            ],
        },
        "state": {
            "independent_profile": True,
            "independent_token": True,
            "independent_session": True,
            "reuses_oauth_home": False,
            "reuses_data_plane_token": False,
        },
        "retention": {
            "mode": "chunk-only",
            "enable_observations": False,
            "enable_auto_consolidation": False,
            "models": [],
            "refresh_routes": [],
            "mental_model_generation": False,
            "memory_defense": "sensitive_data",
        },
        "recall": {"engineering": False, "personal": False, "core": False},
        "bootstrap": {
            "mount_id": "reviewed-bootstrap",
            "artifact_id": "airlock-bootstrap",
            "artifact_version": "v1",
            "artifact_digest": "b" * 64,
            "reviewed": True,
            "content_classes": [
                "transferable_engineering_principles",
                "security_rules",
            ],
            "excluded_classes": [
                "personal_content",
                "project_facts",
                "credentials",
                "operational_state",
            ],
        },
        "export": {"encrypted": True, "verify_before_teardown": True},
        "bridge": {
            "source_citations_required": True,
            "candidate_dispositions_required": True,
            "promotion_is_separate": True,
        },
        "teardown": {
            "immediate": True,
            "delete_bank": True,
            "delete_profile": True,
            "delete_machine": True,
        },
    }


class AirlockPlanTests(unittest.TestCase):
    def test_launch_requires_preflight_evidence_and_returns_an_immutable_plan(
        self,
    ):
        candidate = valid_candidate()
        runner = FakeOrbStackRunner()

        plan = validate_airlock_plan(candidate, runner)

        self.assertEqual(plan.to_dict(), candidate)
        self.assertEqual(runner.calls, list(PREFLIGHT_PROBES))
        self.assertFalse(set(runner.calls) & set(CLOSEOUT_PROBES))
        self.assertTrue(
            {
                "harness.setuid_escalation.denied",
                "harness.network_admin.denied",
                "harness.container_socket.denied",
            }.issubset(runner.calls)
        )
        candidate["machine"]["fresh"] = False
        self.assertTrue(plan.machine["fresh"])
        with self.assertRaises(TypeError):
            plan.machine["fresh"] = False

    def test_each_probe_is_bound_to_exact_plan_and_immutable_resource_identity(self):
        candidate = valid_candidate()
        runner = FakeOrbStackRunner()

        plan = validate_airlock_plan(candidate, runner)
        validate_airlock_closeout(plan, runner)

        self.assertRegex(plan.plan_digest, r"^[0-9a-f]{64}$")
        self.assertEqual(
            set(plan.resource_identity),
            {
                "machine", "mounts", "egress", "bootstrap", "export",
                "teardown", "runner_attestation",
            },
        )
        self.assertEqual(
            len(runner.evidence), len(PREFLIGHT_PROBES + CLOSEOUT_PROBES)
        )
        challenges = []
        for probe, evidence in zip(runner.calls, runner.evidence, strict=True):
            self.assertEqual(evidence["probe"], probe)
            self.assertEqual(evidence["plan_digest"], plan.plan_digest)
            self.assertEqual(evidence["resource_identity"], plan.resource_identity)
            self.assertRegex(evidence["challenge"], r"^[0-9a-f]{64}$")
            challenges.append(evidence["challenge"])
            self.assertEqual(
                evidence["runner_attestation"],
                {
                    "schema_version": 1,
                    "instance_id": "airlock-1",
                    "session_id": "session-1",
                },
            )
            with self.assertRaises(TypeError):
                evidence["resource_identity"]["machine"] = {}
        self.assertEqual(len(challenges), len(set(challenges)))
        candidate["machine"]["fresh"] = False
        candidate["mounts"]["output"]["id"] = "changed"
        self.assertTrue(plan.resource_identity["machine"]["fresh"])
        self.assertEqual(
            plan.resource_identity["mounts"]["output"]["id"],
            "encrypted-export",
        )
        self.assertEqual(
            plan.resource_identity["bootstrap"]["artifact_digest"],
            "b" * 64,
        )
        self.assertNotIn("changed", json.dumps(plan.to_dict()))

    def test_preflight_rejects_tampered_probe_bindings(self):
        for field in ("challenge", "probe", "plan_digest"):
            for identity in (
                {},
                {"instance_id": "airlock-37", "session_id": "session-91"},
            ):
                with self.subTest(
                    field=field, identity=identity
                ), self.assertRaisesRegex(
                    AirlockPlanError,
                    "must return a closed result and attestation",
                ):
                    validate_airlock_plan(
                        valid_candidate(),
                        TamperingOrbStackRunner(field, **identity),
                    )

    def test_closeout_rejects_tampered_probe_bindings(self):
        for identity in (
            {},
            {"instance_id": "airlock-37", "session_id": "session-91"},
        ):
            plan = validate_airlock_plan(
                valid_candidate(), FakeOrbStackRunner(**identity)
            )
            for field in ("challenge", "probe", "plan_digest"):
                with self.subTest(
                    field=field, identity=identity
                ), self.assertRaisesRegex(
                    AirlockPlanError,
                    "must return a closed result and attestation",
                ):
                    validate_airlock_closeout(
                        plan, TamperingOrbStackRunner(field, **identity)
                    )

    def test_launch_owns_candidate_before_external_probes(self):
        candidate = valid_candidate()

        plan = validate_airlock_plan(
            candidate, MutatingOrbStackRunner(candidate)
        )

        self.assertFalse(candidate["machine"]["fresh"])
        self.assertTrue(plan.machine["fresh"])

    def test_closeout_verifies_export_bridge_dispositions_and_then_teardown(
        self,
    ):
        preflight_runner = FakeOrbStackRunner()
        plan = validate_airlock_plan(valid_candidate(), preflight_runner)
        closeout_runner = FakeOrbStackRunner()

        validate_airlock_closeout(plan, closeout_runner)

        self.assertEqual(closeout_runner.calls, list(CLOSEOUT_PROBES))
        export_index = closeout_runner.calls.index("export.encrypted.verified")
        bridge_index = closeout_runner.calls.index(
            "bridge.candidates.dispositioned"
        )
        immediate_index = closeout_runner.calls.index("teardown.immediate")
        self.assertLess(export_index, bridge_index)
        for probe in (
            "teardown.bank.deleted",
            "teardown.profile.deleted",
            "teardown.machine.deleted",
        ):
            deletion_index = closeout_runner.calls.index(probe)
            self.assertLess(bridge_index, deletion_index)
            self.assertLess(deletion_index, immediate_index)

    def test_closeout_requires_the_preflight_instance_and_session(self):
        plan = validate_airlock_plan(valid_candidate(), FakeOrbStackRunner())
        for runner in (
            FakeOrbStackRunner(instance_id="airlock-2"),
            FakeOrbStackRunner(session_id="session-2"),
        ):
            with self.subTest(attestation=runner.attest()), self.assertRaisesRegex(
                AirlockPlanError, "attestation changed"
            ):
                validate_airlock_closeout(plan, runner)

    def test_runner_attestation_rejects_whitespace_only_identities(self):
        for field in ("instance_id", "session_id"):
            runner = FakeOrbStackRunner()
            setattr(runner, field, " \t ")
            with self.subTest(field=field), self.assertRaisesRegex(
                AirlockPlanError, "attestation is invalid"
            ):
                validate_airlock_plan(valid_candidate(), runner)

    def test_runner_attestation_requires_an_exact_integer_schema_version(self):
        class BooleanVersionRunner(FakeOrbStackRunner):
            def attest(self):
                return {**super().attest(), "schema_version": True}

        with self.assertRaisesRegex(
            AirlockPlanError, "attestation is invalid"
        ):
            validate_airlock_plan(valid_candidate(), BooleanVersionRunner())

    def test_rejects_each_closed_airlock_boundary(self):
        cases = {
            "fresh machine": ("machine", "fresh", False),
            "Linux machine": ("machine", "os", "darwin"),
            "ephemeral machine": ("machine", "ephemeral", False),
            "macOS integration": ("machine", "macos_integration", True),
            "host networking": ("machine", "host_network", True),
            "peer networking": ("machine", "peer_network", True),
            "separate guest kernel": (
                "machine",
                "separate_guest_kernel_required",
                False,
            ),
            "read-only inputs": (
                "mounts",
                "inputs",
                [{"id": "task-source", "mode": "read-write", "reviewed": True}],
            ),
            "narrow output": (
                "mounts",
                "output",
                {"id": "export", "mode": "write-only", "narrow": False},
            ),
            "root egress": ("egress", "enforcement_owner", "airlock-agent"),
            "default-deny egress": ("egress", "default_deny", False),
            "root egress enforcement": (
                "egress",
                "harness_can_modify",
                True,
            ),
            "unprivileged harness": ("harness", "unprivileged", False),
            "no sudo": ("harness", "sudo", True),
            "setuid isolation": ("harness", "setuid_escalation", True),
            "network administration": ("harness", "network_admin", True),
            "container isolation": ("harness", "container_socket", True),
            "independent profile": ("state", "independent_profile", False),
            "independent token": ("state", "independent_token", False),
            "independent session": ("state", "independent_session", False),
            "profile OAuth home": ("state", "reuses_oauth_home", True),
            "token data plane": (
                "state",
                "reuses_data_plane_token",
                True,
            ),
            "chunk-only retention": ("retention", "mode", "observation"),
            "observations disabled": ("retention", "enable_observations", True),
            "consolidation disabled": (
                "retention",
                "enable_auto_consolidation",
                True,
            ),
            "models disabled": ("retention", "models", ["summarizer"]),
            "models disabled refresh routes": (
                "retention",
                "refresh_routes",
                ["summarizer"],
            ),
            "models disabled generation": (
                "retention",
                "mental_model_generation",
                True,
            ),
            "sensitive-data memory defense": (
                "retention",
                "memory_defense",
                "disabled",
            ),
            "core recall from engineering": ("recall", "engineering", True),
            "core recall from personal": ("recall", "personal", True),
            "no core recall": ("recall", "core", True),
            "encrypted export": ("export", "encrypted", False),
            "verified export": ("export", "verify_before_teardown", False),
            "source-cited bridge": (
                "bridge",
                "source_citations_required",
                False,
            ),
            "bridge candidate dispositions": (
                "bridge",
                "candidate_dispositions_required",
                False,
            ),
            "separate bridge promotion": (
                "bridge",
                "promotion_is_separate",
                False,
            ),
            "immediate teardown": ("teardown", "immediate", False),
            "bank deletion": ("teardown", "delete_bank", False),
            "profile deletion": ("teardown", "delete_profile", False),
            "machine deletion": ("teardown", "delete_machine", False),
        }
        for label, (section, field, value) in cases.items():
            with self.subTest(label=label):
                candidate = valid_candidate()
                candidate[section][field] = value
                with self.assertRaisesRegex(AirlockPlanError, label):
                    validate_airlock_plan(candidate, FakeOrbStackRunner())

    def test_requires_complete_tamper_and_reachability_probes(self):
        for group, probe in (
            ("tamper_denied", "firewall"),
            ("unreachable", "host_loopback"),
        ):
            with self.subTest(group=group, probe=probe):
                candidate = valid_candidate()
                candidate["probes"][group].remove(probe)
                with self.assertRaisesRegex(AirlockPlanError, "probe set"):
                    validate_airlock_plan(candidate, FakeOrbStackRunner())

        runner = FakeOrbStackRunner({"tamper.firewall.denied"})
        with self.assertRaisesRegex(AirlockPlanError, "tamper.firewall.denied"):
            validate_airlock_plan(valid_candidate(), runner)

        with self.assertRaisesRegex(
            AirlockPlanError, "bootstrap.artifact.bound"
        ):
            validate_airlock_plan(
                valid_candidate(),
                FakeOrbStackRunner({"bootstrap.artifact.bound"}),
            )

    def test_requires_distinct_runtime_ephemeral_and_kernel_evidence(self):
        for probe in (
            "machine.runtime.ephemeral",
            "machine.runtime.separate_kernel",
        ):
            with self.subTest(probe=probe), self.assertRaisesRegex(
                AirlockPlanError, probe
            ):
                validate_airlock_plan(
                    valid_candidate(), FakeOrbStackRunner({probe})
                )

    def test_requires_one_digest_bound_reviewed_non_sensitive_bootstrap(self):
        cases = {
            "reviewed": False,
            "artifact_version": "",
            "artifact_digest": "not-a-digest",
            "mount_id": "missing-bootstrap-input",
            "content_classes": ["security_rules"],
            "excluded_classes": ["personal_content"],
        }
        for field, value in cases.items():
            with self.subTest(field=field):
                candidate = valid_candidate()
                candidate["bootstrap"][field] = value
                with self.assertRaisesRegex(AirlockPlanError, "bootstrap"):
                    validate_airlock_plan(candidate, FakeOrbStackRunner())

    def test_requires_verified_bank_profile_and_machine_deletion_after_export(
        self,
    ):
        for probe in (
            "teardown.bank.deleted",
            "teardown.profile.deleted",
            "teardown.machine.deleted",
        ):
            with self.subTest(probe=probe):
                plan = validate_airlock_plan(
                    valid_candidate(), FakeOrbStackRunner()
                )
                with self.assertRaisesRegex(AirlockPlanError, probe):
                    validate_airlock_closeout(
                        plan,
                        FakeOrbStackRunner({probe}),
                    )

    def test_rejects_host_gui_harnesses_before_any_probe(self):
        candidate = valid_candidate()
        candidate["harness"].update({"kind": "gui", "host_gui": True})
        runner = FakeOrbStackRunner()

        with self.assertRaisesRegex(AirlockPlanError, "host GUI"):
            validate_airlock_plan(candidate, runner)
        self.assertEqual(runner.calls, [])

    def test_egress_destinations_require_exact_catalog_host_and_port(self):
        invalid = (
            ["api.openai.com:443"],
            [{"selector": "unknown", "host": "api.openai.com", "port": 443}],
            [{"selector": "openai-api", "host": "*.openai.com", "port": 443}],
            [{"selector": "openai-api", "host": "127.0.0.1", "port": 443}],
            [{"selector": "openai-api", "host": "api.openai.com", "port": 80}],
        )
        for destinations in invalid:
            with self.subTest(destinations=destinations):
                candidate = valid_candidate()
                candidate["egress"]["approved_destinations"] = destinations
                with self.assertRaises(AirlockPlanError):
                    validate_airlock_plan(candidate, FakeOrbStackRunner())

    def test_egress_destinations_may_be_empty_for_an_offline_airlock(self):
        candidate = valid_candidate()
        candidate["egress"]["approved_destinations"] = []

        plan = validate_airlock_plan(candidate, FakeOrbStackRunner())

        self.assertEqual(plan.to_dict()["egress"]["approved_destinations"], [])

    def test_rejects_extra_unreviewed_input_mounts(self):
        candidate = valid_candidate()
        candidate["mounts"]["inputs"].append({
            "id": "unreviewed-context",
            "mode": "read-only",
            "reviewed": False,
        })
        with self.assertRaisesRegex(AirlockPlanError, "reviewed input"):
            validate_airlock_plan(candidate, FakeOrbStackRunner())

    def test_approved_destination_catalog_is_immutable(self):
        with self.assertRaises(TypeError):
            APPROVED_DESTINATION_CATALOG["openai-api"] = (
                "attacker.invalid", 443
            )
        self.assertEqual(
            APPROVED_DESTINATION_CATALOG["openai-api"],
            ("api.openai.com", 443),
        )

    def test_each_probe_atomically_reasserts_instance_and_session(self):
        class SwappingRunner(FakeOrbStackRunner):
            def probe(self, probe, evidence):
                result = super().probe(probe, evidence)
                result["runner_attestation"] = {
                    **result["runner_attestation"],
                    "session_id": "swapped-session",
                }
                return result

        with self.assertRaisesRegex(
            AirlockPlanError, "probe .* attestation changed"
        ):
            validate_airlock_plan(valid_candidate(), SwappingRunner())

    def test_runner_callback_failures_are_wrapped_as_airlock_errors(self):
        class ExplodingAttestation(FakeOrbStackRunner):
            def attest(self):
                raise RuntimeError("private attestation failure")

        class ExplodingProbe(FakeOrbStackRunner):
            def probe(self, probe, evidence):
                raise RuntimeError("private probe failure")

        with self.assertRaisesRegex(
            AirlockPlanError, "runner attestation is unavailable"
        ) as attestation:
            validate_airlock_plan(valid_candidate(), ExplodingAttestation())
        self.assertNotIn("private", str(attestation.exception))

        with self.assertRaisesRegex(
            AirlockPlanError, "runner probe is unavailable"
        ) as probe:
            validate_airlock_plan(valid_candidate(), ExplodingProbe())
        self.assertNotIn("private", str(probe.exception))

    def test_rejects_unknown_plan_fields_and_non_boolean_probe_results(self):
        candidate = valid_candidate()
        candidate["host_home"] = "/Users/example"
        with self.assertRaisesRegex(AirlockPlanError, "closed"):
            validate_airlock_plan(candidate, FakeOrbStackRunner())

        class AmbiguousRunner:
            def attest(self):
                return {
                    "schema_version": 1,
                    "instance_id": "airlock-ambiguous",
                    "session_id": "session-ambiguous",
                }

            def probe(self, probe, evidence):
                return 1

        with self.assertRaisesRegex(AirlockPlanError, "closed result"):
            validate_airlock_plan(valid_candidate(), AmbiguousRunner())

    def test_direct_plan_construction_cannot_bypass_preflight(self):
        with self.assertRaisesRegex(AirlockPlanError, "validated preflight"):
            AirlockLaunchPlan(valid_candidate())


if __name__ == "__main__":
    unittest.main()
