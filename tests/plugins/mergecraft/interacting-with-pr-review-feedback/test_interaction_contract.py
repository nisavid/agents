import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[4]
SKILL_ROOT = (
    REPOSITORY / "plugins/mergecraft/skills/interacting-with-pr-review-feedback"
)
SKILL = SKILL_ROOT / "SKILL.md"
AUTHORITY = SKILL_ROOT / "references/interaction-authority.md"


class InteractionContractTests(unittest.TestCase):
    def test_limits_writes_to_feedback_interactions(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("reply", text)
        self.assertIn("reaction", text)
        self.assertIn("thread resolution", text)
        self.assertIn("repository/operator policy", text)
        self.assertIn("exactly one authorized", text)
        self.assertNotIn("thread author login must exactly match", text)

    def test_resolution_transition_is_operation_specific(self):
        text = AUTHORITY.read_text(encoding="utf-8")
        self.assertIn("reply or reaction", text)
        self.assertIn("resolution state is unchanged", text)
        self.assertIn("Only a requested\nresolution operation", text)


if __name__ == "__main__":
    unittest.main()
