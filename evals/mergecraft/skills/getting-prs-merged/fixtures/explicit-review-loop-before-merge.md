# Scenario: Explicit Review Loop Before Merge

User request: "Run a fresh review-and-revise cycle on this clean PR and merge it only after that cycle is clean."

Mock repository state:

- Repository: `example/widgets`
- PR: `#91`
- Base branch: `main`
- PR state: ready for review
- Local status: clean
- Local `HEAD`: matches the PR head SHA
- Current feedback: complete and clean
- Required checks: successful
- Review decision: approved
- Merge state: clean

Mock local policy:

- `AGENTS.md`: an explicitly requested fresh review-and-revise cycle is required before merge.
- `AGENTS.md`: the merge-closeout owner terminally hands a required fresh cycle
  to Tricritical and ends its invocation.
- `AGENTS.md`: merge actuation is agent-owned after all refreshed gates pass.
- `AGENTS.md`: use rebase merge for this repository.
- `AGENTS.md`: branch cleanup must use the Git publication and cleanup owner.

Mock loop outcomes:

- A clean terminal permits a fresh merge-closeout invocation, which rereads
  current merge state before closeout.
- A finding, blocked result, or `clean / degraded` terminal forbids merge; this
  request requires bare `clean`.
