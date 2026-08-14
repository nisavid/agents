from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from scripts.validate_rolecasting import build_executor_payload, build_grader_payload

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "rolecasting"
SKILLS = ("choosing-agent-models", "delegating-cross-agent-work")


class RolecastingEvalCorpusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.documents = {
            skill: json.loads(
                (PLUGIN_ROOT / "skills" / skill / "evals" / "evals.json").read_text()
            )
            for skill in SKILLS
        }

    def test_rolecasting_has_exactly_twenty_detailed_scenarios(self) -> None:
        observed = {
            item["name"]
            for document in self.documents.values()
            for item in document["evals"]
        }
        self.assertEqual(
            observed,
            {
                "choose-after-delegation",
                "unsupported-luna-surface-fallback",
                "fable-proof-requires-invocation-authority",
                "inherited-fixed-model-selection",
                "unavailable-non-codex-capability",
                "high-risk-independent-review",
                "cursor-grok-consequential-review",
                "no-user-owned-task-without-explicit-request",
                "foreign-peer-bounded-authority",
                "leader-integrates-worker-results",
                "absent-foreign-executable-native-fallback",
                "insufficient-foreign-isolation-blocked",
                "blocked-authority-escalation-requires-authorization",
                "leader-owned-peer-topology",
                "user-owned-peer-requires-consent",
                "app-server-is-not-external",
                "planned-surface-pairs-remain-separate",
                "assurance-consumer-minima",
                "chatgpt-codex-native-child-dispatch",
                "codex-cli-tui-native-child-dispatch",
            },
        )

    def test_delegation_surface_topology_and_assurance_contract_is_explicit(
        self,
    ) -> None:
        delegating_root = PLUGIN_ROOT / "skills" / "delegating-cross-agent-work"
        choosing_root = PLUGIN_ROOT / "skills" / "choosing-agent-models"
        sources = {
            "delegating": (delegating_root / "SKILL.md").read_text(),
            "topology_receipt": (
                delegating_root / "references" / "invocation-topology-receipt.md"
            ).read_text(),
            "foreign_peers": (
                delegating_root / "references" / "foreign-harness-peers.md"
            ).read_text(),
            "dispatch_evidence": (
                delegating_root / "references" / "dispatch-evidence.md"
            ).read_text(),
            "native_codex": (
                delegating_root / "references" / "native-codex-subagents.md"
            ).read_text(),
            "choosing": (choosing_root / "SKILL.md").read_text(),
            "capabilities": (
                choosing_root / "references" / "capability-probes-and-fallbacks.md"
            ).read_text(),
            "readme": (PLUGIN_ROOT / "README.md").read_text(),
        }
        combined = "\n".join(sources.values())

        for dimension in (
            "family",
            "surface",
            "version",
            "executor",
            "relationship",
            "ownership",
            "transport",
            "assurance",
        ):
            with self.subTest(dimension=dimension):
                self.assertIn(dimension, sources["topology_receipt"])
        for vocabulary in (
            "child",
            "peer",
            "external",
            "leader-owned",
            "user-owned",
            "native-tool",
            "task-api",
            "cli",
            "app-server",
            "remote-api",
            "product-attested",
            "controller-observed",
            "self-reported",
        ):
            with self.subTest(vocabulary=vocabulary):
                self.assertIn(vocabulary, combined)

        self.assertIn(
            "An app-server transport does not make an execution external.",
            sources["topology_receipt"],
        )
        self.assertIn(
            "Model choice is a separate decision",
            sources["choosing"],
        )
        self.assertIn(
            "Ordinary consumers require at least `controller-observed` for every",
            sources["dispatch_evidence"],
        )
        self.assertIn(
            "Self-reported dimensions are diagnostic and non-gating",
            sources["dispatch_evidence"],
        )
        self.assertIn(
            "product-attested ChatGPT Codex child",
            sources["dispatch_evidence"],
        )
        self.assertIn("ChatGPT Codex", sources["foreign_peers"])
        self.assertIn("Codex CLI/TUI", sources["foreign_peers"])
        self.assertIn("Claude Code", sources["foreign_peers"])
        self.assertIn("Claude Desktop", sources["foreign_peers"])
        self.assertIn("Cursor Agent", sources["foreign_peers"])
        self.assertNotIn("Codex" + " Desktop", combined)

        native_codex = sources["native_codex"]
        self.assertIn("ChatGPT Codex", native_codex)
        self.assertIn("Codex CLI/TUI", native_codex)
        self.assertIn("before invoking the native subagent tool", native_codex)
        self.assertIn("agent or worker ID", native_codex)
        self.assertIn("session or task ID", native_codex)
        self.assertIn("launch acknowledgement", native_codex)
        self.assertIn("status observations", native_codex)
        self.assertIn("result observation", native_codex)
        self.assertIn("Transport completion is not usability", native_codex)
        self.assertIn("verification observation", native_codex)
        self.assertIn("strict Boolean", native_codex)
        self.assertIn("Model-generated text is never host evidence", native_codex)
        self.assertIn("controller-observed", native_codex)
        self.assertIn("product-attested", native_codex)
        self.assertIn("explicit user consent", native_codex)
        self.assertIn("App-server is an optional later transport", native_codex)
        self.assertLess(
            native_codex.index("before invoking the native subagent tool"),
            native_codex.index("launch acknowledgement"),
        )

        topology = json.loads((PLUGIN_ROOT / "topology.json").read_text())
        self.assertTrue(
            {
                "product-family",
                "surface",
                "version",
                "executor",
                "relationship",
                "ownership",
                "transport",
                "assurance",
                "consumer-assurance-minimum",
            }.issubset(topology["receipt_contract"]["binds"])
        )
        delegation_owners = topology["skills"]["delegating-cross-agent-work"]["owns"]
        self.assertTrue(
            {
                "target-surface",
                "worker-relationship",
                "worker-ownership",
                "dispatch-transport",
                "dispatch-assurance",
                "consumer-assurance-minimum",
            }.issubset(delegation_owners)
        )

    def test_each_manifest_covers_its_raw_fixtures(self) -> None:
        for skill, document in self.documents.items():
            with self.subTest(skill=skill):
                eval_root = PLUGIN_ROOT / "skills" / skill / "evals"
                fixture_files = {
                    path.name for path in (eval_root / "fixtures").iterdir()
                }
                referenced = {
                    Path(item["fixture_paths"][0]).name for item in document["evals"]
                }
                self.assertEqual(referenced, fixture_files)

    def test_safety_expectations_cover_selection_and_foreign_surface_failures(
        self,
    ) -> None:
        expectations = {
            expectation["id"]
            for document in self.documents.values()
            for item in document["evals"]
            for expectation in item["expectations"]
        }
        self.assertTrue(
            {
                "surface-intersection",
                "no-invented-luna",
                "fallback-disclosure",
                "fable-catalog-insufficient",
                "fable-unavailable",
                "no-ungranted-proof-invocation",
                "task-authority",
                "no-onward-authority",
                "input-not-integration",
                "final-verification",
                "inherited-selection-omitted",
                "non-codex-capability-unproven",
                "no-invented-selection-or-dispatch",
                "absent-executable-evidence",
                "contract-preserving-native-fallback",
                "isolation-capability-failed",
                "no-unsafe-foreign-dispatch",
                "blocked-with-probe-evidence",
                "no-unauthorized-authority-escalation",
                "explicit-authorization-before-retry",
                "reviewer-sol-high-default",
                "clerical-luna-separation",
                "no-unproven-review-overescalation",
                "cursor-grok-high-fit",
                "cursor-surface-proof",
                "foreign-review-authority-preserved",
                "leader-owned-peer",
                "user-owned-peer-consent",
                "transport-relationship-independent",
                "surface-pairs-separated",
                "support-not-issuance",
                "ordinary-controller-observed",
                "self-report-diagnostic-only",
                "publication-product-attested",
                "model-choice-separate",
                "plan-frozen-before-native-spawn",
                "native-host-observation-binding",
                "no-model-text-as-host-evidence",
                "native-child-truthful-assurance",
            }.issubset(expectations)
        )

    def test_fable_scenario_denies_ungranted_proof_invocation(self) -> None:
        choosing_evals = self.documents["choosing-agent-models"]["evals"]
        fable = next(
            item
            for item in choosing_evals
            if item["name"] == "fable-proof-requires-invocation-authority"
        )
        self.assertIn("does not invoke", fable["expected_output"])
        self.assertIn("no exact proof-invocation authority", fable["expected_output"])
        fixture = " ".join(
            (
                PLUGIN_ROOT
                / "skills"
                / "choosing-agent-models"
                / fable["fixture_paths"][0]
            )
            .read_text()
            .split()
        )
        self.assertIn("help and model catalog advertise Fable", fixture)
        self.assertIn("did not authorize an external invocation", fixture)

    def test_content_lock_pins_complete_eval_and_fixture_bytes(self) -> None:
        lock = json.loads((PLUGIN_ROOT / "content-lock.json").read_text())
        expected_paths = {f"skills/{skill}/evals/evals.json" for skill in SKILLS}
        for skill, document in self.documents.items():
            expected_paths.update(
                f"skills/{skill}/{item['fixture_paths'][0]}"
                for item in document["evals"]
            )
        self.assertTrue(expected_paths.issubset(lock["files"]))
        for relative in expected_paths:
            with self.subTest(path=relative):
                self.assertEqual(
                    hashlib.sha256((PLUGIN_ROOT / relative).read_bytes()).hexdigest(),
                    lock["files"][relative],
                )

    def test_executor_payload_withholds_rubrics_until_distinct_grader(self) -> None:
        delivery = json.loads((PLUGIN_ROOT / "evals" / "delivery.json").read_text())
        self.assertEqual(delivery["executor"]["tools"], "denied")
        self.assertTrue(delivery["grader"]["distinct_from_executor"])
        self.assertEqual(delivery["grader"]["available_after"], "executor-complete")
        for skill, document in self.documents.items():
            for item in document["evals"]:
                fixture = (
                    PLUGIN_ROOT / "skills" / skill / item["fixture_paths"][0]
                ).read_text()
                executor = build_executor_payload(
                    prompt=item["prompt"],
                    fixture=fixture,
                    candidate_bundle={"SKILL.md": "candidate"},
                )
                grader = build_grader_payload(
                    executor_payload=executor,
                    response="candidate response",
                    expected_output=item["expected_output"],
                    expectations=item["expectations"],
                )
                with self.subTest(eval=item["name"]):
                    self.assertEqual(list(executor), delivery["executor"]["inputs"])
                    self.assertEqual(list(grader), delivery["grader"]["inputs"])
                    self.assertNotIn("expected_output", executor)
                    self.assertNotIn("expectations", executor)
                    self.assertIsNot(executor, grader)
                    self.assertEqual(grader["expected_output"], item["expected_output"])
                    self.assertEqual(grader["expectations"], item["expectations"])

    def test_failure_terminal_fixtures_cover_required_surfaces(self) -> None:
        scenarios = {
            item["name"]: item
            for document in self.documents.values()
            for item in document["evals"]
        }
        expected_fragments = {
            "inherited-fixed-model-selection": ("no model", "environment is fixed"),
            "unavailable-non-codex-capability": ("exits nonzero", "cannot be parsed"),
            "absent-foreign-executable-native-fallback": (
                "not installed",
                "native subagent",
            ),
            "insufficient-foreign-isolation-blocked": (
                "cannot bind a worktree",
                "no native",
            ),
            "blocked-authority-escalation-requires-authorization": (
                "needs_context",
                "operator or repository authorization",
            ),
        }
        for name, fragments in expected_fragments.items():
            item = scenarios[name]
            skill = next(
                skill
                for skill, document in self.documents.items()
                if item in document["evals"]
            )
            fixture = (
                (PLUGIN_ROOT / "skills" / skill / item["fixture_paths"][0])
                .read_text()
                .lower()
            )
            with self.subTest(eval=name):
                for fragment in fragments:
                    self.assertIn(fragment, fixture)

    def test_raw_fixtures_do_not_leak_grader_answers(self) -> None:
        for skill, document in self.documents.items():
            for item in document["evals"]:
                fixture = PLUGIN_ROOT / "skills" / skill / item["fixture_paths"][0]
                content = fixture.read_text().lower()
                with self.subTest(fixture=fixture.name):
                    self.assertGreaterEqual(len(content.strip()), 200)
                    for marker in ("expected_output", "expectations", "pass if"):
                        self.assertNotIn(marker, content)


if __name__ == "__main__":
    unittest.main()
