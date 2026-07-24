# Scenario: Older publication receipt matches, latest receipt drifts

User request: "Make PR #84 ready for review now that its checks are green."

Mock repository and GitHub state:

- Repository: `example/widgets`
- Pull request: `#84`, currently a draft
- Base/head refs and pushed OIDs are known exactly and match the live PR
- Required checks are successful, the canonical body renders correctly, and ready-state actuation is otherwise authorized
- Publication receipt sequence 7 is canonical and its final state matches the current live title, body, and draft state
- The authoritative latest receipt is sequence 8, and its final-state body digest differs from the current live body
- A read-only `audit_reviewable_pr.py audit` therefore returns `drift` and identifies sequence 8

State the safe ordered readiness outcome.
