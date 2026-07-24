---
name: addressing-pr-review-feedback
description: Use when a caller needs a complete read-only GitHub review-feedback snapshot for orientation, or an author needs to adjudicate and address requested changes, unresolved threads, or stale review comments.
---

# Addressing PR Review Feedback

Coordinate author-side feedback without becoming a second reviewer, publisher,
or merge workflow.

The read-only `feedback-acquisition` capability is a leaf that other workflows
may invoke directly. An actionable snapshot from another outcome coordinator
arrives here only as a terminal handoff; start this coordinator in a fresh
invocation, and never return inline into the caller's stale state.

Select one mode at invocation:

- **Snapshot/orientation:** acquire and return complete head-bound state, then
  stop. This mode has no disposition, adjudication, revision, checkpoint,
  interaction, publication, or other mutation authority.
- **Author outcome:** continue through the author-side workflow below. Use this
  mode only when the request authorizes addressing feedback or source changes.

1. Read [the feedback flow](references/feedback-flow.md), then acquire the complete
   head-bound live snapshot with `scripts/review_feedback_state.py`. Include inline threads, top-level
   comments, review bodies, review requests, checks, and complete pagination.
   In snapshot/orientation mode, report bound identities, acquisition
   completeness, the live snapshot, and limitations, then stop before step 2.
2. Before adjudication, freeze one feedback-revision contract containing the exact
   source candidate identity over the normalized owned paths, entry types, modes,
   and bytes; the requirements or specification source or its explicit absence;
   the repository standards sources or their explicit absence; the exact owned
   paths; the original mutation authority; and the declared verification. Every
   field must be present. A missing input blocks before adjudication or revision.
   Recheck the source candidate identity before each handoff; drift blocks the
   affected work with zero edits.
3. Cluster related feedback into independence groups according to whether one
   disposition or operator decision could change another group's accepted work.
   Give each group and the complete byte-identical frozen contract to
   the imported `finding-adjudication` operation (`tricritical:adjudicate`);
   adjudicate each group separately.
   It assigns exactly one disposition per finding: `accept`, `reject`, `already
addressed`, `stale`, `duplicate`, `needs operator decision`, `blocked`, or
   `follow-up outside scope`. A feedback-specific reason may explain the
   disposition; it is never a second disposition taxonomy.
4. Combine independently safe `accept` findings that remain in scope and share
   the frozen source contract. Give those findings and that complete
   byte-identical contract to the imported `source-revision` operation
   (`tricritical:revise`). Complete, verify, checkpoint,
   and publish that work before returning an unrelated operator decision. Never
   preempt or work around a decision that controls an accepted change. Hand the
   verified source changes to
   the imported `git-ref-push` operation
   (`versionkeeping:checkpointing-and-publishing-git-work`). Return changed
   title/body facts to the lifecycle caller; this feedback coordinator does not
   publish PR text.
5. Hand the exact `feedback-interaction` operation for explicit reply,
   reaction, or thread-resolution actions to
   [Interacting With PR Review Feedback](../interacting-with-pr-review-feedback/SKILL.md).
   Replies state evidence naturally, without a formulaic acknowledgement.
   Keep a human-decision item open.
6. Re-read the feedback state and return the next owner or blocker. Do not
   repeat review/revision or create a nested review loop.

In author-outcome mode, finish with the current snapshot, each disposition and
evidence, interaction receipts, and any remaining owner or authority gate. Do
not persist a review ledger; a later run must acquire a fresh snapshot.
