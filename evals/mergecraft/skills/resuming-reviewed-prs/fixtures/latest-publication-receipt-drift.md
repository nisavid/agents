# Scenario: Resume a PR whose latest publication receipt drifts

User request: "Resume PR #84 and hand it to whichever lifecycle owner should continue."

Mock state:

- Repository, checkout, PR number, refs, pushed OIDs, head repository, and owner are known exactly
- Local status is clean and no Git or forge operation is in progress
- Publication receipt sequence 7 matches the current live title, body, and draft state
- The authoritative latest receipt is sequence 8, whose final-state body digest does not match live state
- A read-only `audit_reviewable_pr.py audit` returns `drift` for sequence 8
- No mutation or reconciliation authority was supplied

State the safe resume and handoff outcome.
