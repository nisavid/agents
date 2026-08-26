# Scenario: Ready State Only

User request: "The stored title and body are already current. Mark pull request #84 ready for review without rewriting them."

Mock repository state:

- Repository: `example/widgets`
- Pull request: `#84`
- Base: `main` at `1111111111111111111111111111111111111111`
- Head: `alice:retry-ledger` at `2222222222222222222222222222222222222222`
- Head owner: `alice`
- Head repository: `alice/widgets`
- Pull-request state: draft
- Stored title and body: canonical and current for the exact pushed diff
- Current writer validation: the existing validated title/body pair exactly
  matches the stored title and body; no new composition is required
- Sealed review-input manifest: binds `example/widgets`, pull request `#84`,
  the base and qualified head refs and OIDs, head owner and repository,
  complete pushed diff, and exact stored title/body bytes
- Required checks: successful
- Known valid blockers: none
- Live collapsed and expanded rendering: inspected successfully

Mock policy:

- The agent may mark a canonical draft ready after all readiness gates pass.
- This request does not authorize a title or body edit.
- Raw ready commands and automatic retry after ambiguity are prohibited.
