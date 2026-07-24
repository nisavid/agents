from __future__ import annotations

import json
import hashlib
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

    def test_rolecasting_has_exactly_thirteen_detailed_scenarios(self) -> None:
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
            },
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
