---
name: getting-prs-ready-for-review
description: Use when the operator explicitly asks only to make a pull request ready for review end to end. Do not use for review-only, text-only, publish-only, merge, ship, or closeout requests, including composite requests that also mention readiness; getting-prs-merged owns those terminal outcomes.
---

# Getting PRs Ready For Review

Coordinate one review-readiness outcome. Read repository/operator policy first;
it defines who may publish commits and mark a draft ready.

1. Resolve the exact repository, branch, pushed base/head, exact head repository,
   and existing PR.
2. Stop on unresolved valid blockers, operator decisions, or overlapping source
   ownership. This skill does not perform an independent review or address
   existing review feedback.
3. When scoped changes need a checkpoint or push, call
   the imported `git-ref-push` operation
   (`versionkeeping:checkpointing-and-publishing-git-work`).
4. Give the exact pushed change to the `pr-content` operation owned by
   [Write Reviewable PR Descriptions](../writing-reviewable-pr-descriptions/SKILL.md).
5. Give the complete validated title/body to the operation-specific publisher
   surface in [Publish Reviewable PRs](../publishing-reviewable-prs/SKILL.md):
   use `pr-creation` for an absent PR or `pr-text-write` for an existing matching
   draft. Create every new PR as a draft; never create a duplicate.
6. Inspect the stored and rendered canonical body. If all readiness gates pass
   and actuation is authorized, refresh the exact identity/preimage and use the
   publisher's guarded `pr-readiness-write` operation.
7. Before reporting readiness, use the publisher's read-only
   `publication-audit` operation and require
   the authoritative latest receipt to verify the exact final live state.

Never invoke raw ready actuation or accept generated forge text as canonical.
Preserve the existing draft/ready state until the final authorized transition.
A mutation timeout requires an exact reread and never a blind retry.

Finish with exact repository/base/head identities, stored text digests,
receipt id/provenance/sequence and audit status, draft/ready state, and any
remaining operator-owned gate.
