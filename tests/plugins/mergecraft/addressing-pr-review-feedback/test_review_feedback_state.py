import sys
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[4]
SKILL_DIR = REPOSITORY / "plugins/mergecraft/skills/addressing-pr-review-feedback"
sys.path.insert(0, str(SKILL_DIR / "scripts"))

import review_feedback_state  # noqa: E402


class ReviewFeedbackStateTests(unittest.TestCase):
    def test_query_requests_paginated_top_level_comments_and_review_bodies(self):
        query, _ = review_feedback_state.build_pr_query(
            repo="base-owner/base-repo",
            pr_number=7,
            cursors={
                "threads": None,
                "comments": None,
                "reviews": None,
                "checks": None,
                "review_requests": None,
            },
            include={
                "threads": True,
                "comments": True,
                "reviews": True,
                "checks": True,
                "review_requests": True,
            },
        )

        self.assertIn("comments(first: 100, after: $commentsCursor)", query)
        self.assertIn("body", query)

    def test_query_requests_exact_head_repository_identity(self):
        query, _ = review_feedback_state.build_pr_query(
            repo="base-owner/base-repo",
            pr_number=7,
            cursors={},
        )

        self.assertIn("headRepository {", query)
        self.assertIn("nameWithOwner", query)
        self.assertIn("owner { login }", query)


class FeedbackCompositionContractTests(unittest.TestCase):
    def test_tricritical_handoffs_preserve_the_complete_frozen_contract(self):
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        flow = (SKILL_DIR / "references" / "feedback-flow.md").read_text(
            encoding="utf-8"
        )
        contract = " ".join(f"{skill}\n{flow}".split()).lower()

        for required_input in (
            "exact source candidate identity",
            "requirements or specification source",
            "repository standards sources",
            "owned paths",
            "original mutation authority",
            "declared verification",
        ):
            with self.subTest(required_input=required_input):
                self.assertIn(required_input, contract)
        self.assertIn("byte-identical", contract)
        self.assertIn("tricritical:adjudicate", contract)
        self.assertIn("tricritical:revise", contract)
        self.assertIn(
            "a missing input blocks before adjudication or revision", contract
        )
        self.assertIn("drift blocks the affected work with zero edits", contract)

    def test_unrelated_operator_decision_does_not_hold_safe_accepted_work(self):
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        flow = (SKILL_DIR / "references" / "feedback-flow.md").read_text(
            encoding="utf-8"
        )
        contract = " ".join(f"{skill}\n{flow}".split()).lower()

        self.assertIn("independence groups", contract)
        self.assertIn("adjudicate each group separately", contract)
        self.assertIn("before returning an unrelated operator decision", contract)


if __name__ == "__main__":
    unittest.main()
