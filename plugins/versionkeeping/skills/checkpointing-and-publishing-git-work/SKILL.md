---
name: checkpointing-and-publishing-git-work
description: "Use when Git-backed changes require a baseline, task-only commit, exact-lease publication, separately authorized remote-ref deletion, local merge/keep/discard choice, or provenance-aware branch/worktree cleanup. Owns Git/index/ref/push safety, local integration, remote deletion, and terminal cleanup."
---

# Checkpoint, Publish, And Finish Git Work

Own baseline, task-only commits, exact-lease publication, local Git
integration/discard, and provenance-aware cleanup/deletion.

## Ownership Boundary

This skill owns local Git integration, fork-target publication, and separately
authorized terminal remote-ref deletion. Graphite stack operations; code or
artifact review; pull-request creation, text, readiness, review resolution, or
merge actuation; and model and delegation policy stay outside. Preserve active
PR work. Pull-request merge actuation is excluded; a selected local Git merge
and verification are owned here. The conflict resolver owns interpretation and
authorized file edits; this skill alone owns the resulting stage, continue,
commit, abort, and push mechanics.

## Baseline And Gates

Before work, record repository/worktree, source SHA, branch/detached state,
index/worktree, unpublished state, push configuration, and Git operations.
Pre-existing state is unrelated unless adopted.
Honor explicit keep-local and keep-uncommitted constraints.

Read-only Git tasks never mutate or publish or invoke the publication planner.
Stop on pre-existing operations, incomplete/alternate graphs, failed identity or
required policy/protection/review/verification, unrouted conflicts, ambiguous
ownership or destination, or inability to preserve remote work. An active
conflict operation proceeds only from a complete, exact
`resolving-merge-conflicts` resolution or authorized-abort handoff.

The publication request always carries `default_branch_policy`: use `null` for
an ordinary destination or when no repository policy decision has been
verified. Set it to the exact observed default-branch ref and
`direct_push_permitted: true` only after repository policy/protection has been
verified to permit the direct push. A missing, false, or ref-mismatched decision
cannot authorize publication to the observed default branch.

## Conflict Integration Handoffs

Never interpret conflict intent or edit conflicted files. For a resolution
handoff, reread and match the exact operation identity, HEAD, index stages,
resolved path modes and digests, remaining-unmerged inventory, authority, and
validation before staging only the authorized paths and running the correct
continuation. For an authorized-abort handoff, match the operation and explicit
abort authority before running the operation-specific abort and verifying its
post-state. Stop on any drift, unexpected path, new conflict, or ambiguous
continuation. Commit and publish only through the ordinary checkpoint and
exact-lease gates below.

The planner observes the push endpoint's symbolic default branch and binds it
into the reviewed destination identity. The executor re-observes it before
actuation. An unavailable, malformed, or changed observation blocks; the
selected branch name alone never proves that a direct default-branch push is
permitted.

## Follow The Checkpoint Workflow

1. Resolve constraints, capture baseline, and run applicable verification.
2. Create a literal-path, task-only commit; bind and rerun final verification on
   that immutable commit.
3. Run and review the planner. If reconciliation is required, rerun affected
   gates and replace the plan only after it returns `ready` again.
4. Bind exact reviewed `ready` bytes to a separately retained SHA-256 digest,
   execute once, and require terminal `verified` evidence for the captured
   endpoint and full destination ref.

Audit index/worktree. Use `git --literal-pathspecs commit --only -- <owned paths>`
only for wholly task-owned paths; verify committed paths and unrelated-index
preservation. Mixed path ownership blocks.

## Plan And Execute Publication

Before planning/executing, read [publication execution](references/publication-execution.md)
for script routes, effects, trusted handoff, transport, push, and verification.
The adapter establishes a closed Git configuration before inspection, disables
implicit commit/tag/push signing, rejects command-valued local/worktree settings
(including signing programs) and unsafe includes without exposing their values,
and permits only HTTPS, SSH, or ancestry-guarded local endpoints.

Follow its typed gates exactly. Ordinary publication never deletes a remote ref;
the separate terminal route requires verified merge and explicit
repository/operator authority for exact remote, full ref, and expected SHA.
A normal configured remote name is discovery metadata. Every `target_only_shas`
removal requires its exact SHA in `removal_authorized_commits`.

## Completion And Evaluation Routes

For completed, verified named branches without an outcome, offer local merge, keep, or
discard. Detached HEAD permits keep/report or explicit branch publication, never
discard. Before merge/discard/deletion/cleanup, read [terminal cleanup](references/terminal-cleanup.md)
and apply its provenance and confirmation rules.

Before preparing or grading behavior evaluations, read
[evaluation integrity](references/evaluation-integrity.md). It owns isolation,
tool-use, trace, and grading constraints.
