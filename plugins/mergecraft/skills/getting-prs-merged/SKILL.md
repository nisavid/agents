---
name: getting-prs-merged
description: Use when the operator explicitly requests a GitHub branch or PR merge outcome, and only for that terminal outcome. Do not use for description, review, status, check, comment, readiness, draft, or publication work without merge.
---

# Getting PRs Merged

## Ownership

Name exact routes at first handoff. [getting-prs-ready-for-review](../getting-prs-ready-for-review/SKILL.md)
owns readiness; [addressing-pr-review-feedback](../addressing-pr-review-feedback/SKILL.md)
feedback; the imported `review-loop` operation (`tricritical:loop`) owns fresh
cycles; the
[`focused-ci` operation](references/gh-fix-ci-adapter.md) focused Actions; and
the imported `remote-ref-deletion` operation
(`versionkeeping:checkpointing-and-publishing-git-work`) separately authorized
post-merge cleanup.
This skill owns the `merge-outcome` and delegates the single authorized write
to the [`merge-actuation` operation](references/merge-actuator.md). It also owns the
CodeRabbit request decision and delegates its write to the
[`coderabbit-top-level-comment` actuator](scripts/post_coderabbit_comment.py).
PR text, Git/ref publication, feedback leaves, other comments, and deployment
remain outside.

Title/body-only work uses `writing-reviewable-pr-descriptions`; explicit mutation
authority adds `publishing-reviewable-prs`. Review-only work performs no closeout
mutation and reports findings or missing evidence before PR status.

## Workflow

1. Bind repository/checkout, PR or intended base/branch, pushed OIDs, head
   repository/owner, draft, and target. Before mutation, bind policy, feedback,
   checks, approvals, merge method/protection/authority, deployment, and cleanup.
   Use `merge-inspection` only for this read-only merge-state acquisition.
2. If no PR exists, return a terminal `readiness-handoff` naming
   [getting-prs-ready-for-review](../getting-prs-ready-for-review/SKILL.md), the
   bound repository/base/head target, and the missing PR state. The caller
   invokes readiness separately. After readiness succeeds, start a fresh
   merge invocation from live state; this coordinator never calls readiness.
3. Acquire a complete head-bound feedback snapshot through the read-only
   `feedback-acquisition` capability implemented by
   `addressing-pr-review-feedback/scripts/review_feedback_state.py`. Do not call
   the feedback outcome coordinator or perform any feedback interaction. When
   the snapshot contains actionable feedback or requested changes, return one
   terminal `feedback-handoff` naming
   [addressing-pr-review-feedback](../addressing-pr-review-feedback/SKILL.md)
   and stop. After its separate outcome invocation completes, start a fresh
   merge invocation that rereads live state.
4. Consume the current complete feedback, checks, approvals, and mergeability
   state for ordinary merge closeout; do not start or wrap `tricritical:loop`.
   If the operator explicitly requests fresh review-and-revise, or policy
   requires it, terminate this invocation with an exact handoff to
   `tricritical:loop`. A bare `clean` terminal permits a fresh merge-closeout
   invocation, which rereads all live state. Every other terminal remains
   blocked; `clean / degraded` never satisfies a required bare `clean`.
5. If the PR is absent, draft, or otherwise not review-ready, return the same
   terminal `readiness-handoff`; never continue merge closeout in this
   invocation. Use `check-inspection` for the read-only required-Actions state;
   route failed required Actions through `focused-ci` only after a fresh merge
   invocation observes a review-ready PR.
6. When canonical publication evidence applies, call
   [publishing-reviewable-prs](../publishing-reviewable-prs/SKILL.md) for a
   read-only audit and require the authoritative latest receipt to match live
   state before merge actuation. Do not substitute an older matching receipt.
7. Refresh policy, feedback, checks, approvals, base/head, and mergeability.
   When every gate and authority passes, call `merge-actuation` once
   with the exact repository, PR, base, head SHA, and selected method. Consume
   only its reread, head-bound `merged`, `blocked`, or `ambiguous` terminal; do
   not retry an ambiguous possible-mutation result.
8. When remote-ref deletion is authorized, return one terminal cleanup handoff
   to `operation:remote-ref-deletion` and stop. It requires verified merge plus
   separate repository/operator authorization bound to exact remote, full ref,
   and expected SHA. The caller invokes it separately from live state; this
   coordinator never performs cleanup. Missing authority leaves cleanup gated,
   not merge. Hand deployment to its repository-defined owner.

## CodeRabbit request

For a policy-required skipped CodeRabbit review, treat the skip as no completed
cycle, check readiness and any explicit operator or repository limit, and
require explicit authority for one top-level comment. Bind PR/base/head and
head repository/owner, caller-supplied expected
authenticated login, bytes, and SHA-256. Use the helper once; independently
verify the active login, then reread ID/URL, PR, head, author, body, and
timestamp. Possible-mutation timeout is ambiguous: do not retry. It cannot
edit PR text, feedback, CI, or merge.

## Stop conditions

Stop for valid feedback, unsatisfied loop terminal, missing gate/authority,
drift, conflicts, ambiguity, or unresolved ownership. Green checks never replace
complete feedback.

Return PR URL/final head, publication audit evidence, merge receipt or blocker,
cleanup receipt/gate, and any deployment handoff.
