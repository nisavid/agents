# Scenario: Resume with failed required Actions

User request: "Resume PR #94 and make progress on its current blocker."

Mock state:

- Repository, checkout, PR, base/head refs and OIDs, head repository, and owner are bound exactly
- The worktree is clean and has no in-progress Git operation
- One required GitHub Actions job fails on the bound head OID
- The PR otherwise has an explicit merge outcome request, but no merge authority is available
- No Actions diagnosis, source or workflow edit, rerun, publication, readiness, feedback, or merge action has run in this invocation

State the safe first recovery outcome and the boundary for resuming later work.
