---
name: graphite
description: >-
  Use when working with Graphite `gt` stacks: creating or tracking stacked
  branches, navigating or reparenting a stack, restacking, submitting or
  updating stacked PRs, fixing Graphite metadata, or diagnosing stack ancestry
  and publication state.
---

# Graphite Stacks

## Boundary

Graphite owns stack topology, ancestry operations, restacking, submission, and
temporary draft transport only. The imported `git-ref-push` operation
(`versionkeeping:checkpointing-and-publishing-git-work`) owns commit/push safety.
[Publishing Reviewable PRs](../publishing-reviewable-prs/SKILL.md) immediately
owns canonical title/body and readiness repair; its writer owns content.

Do not maintain another PR-body format. `gt submit` is only draft transport;
complete it before invoking
[Publishing Reviewable PRs](../publishing-reviewable-prs/SKILL.md); the
publisher never wraps it. Publication requires verified canonical bodies.
The submit-draft path calls only the publisher's `pr-text-write`,
`pr-readiness-write`, and `publication-audit` operations; it does not inherit
every publisher mode and cannot mint provenance by reconciliation.

## Establish Live State

Before mutation, run `git status --short --branch`, `gt log short`, and `gt trunk`.
Bind branch, parent, order, tracking, PRs, and unrelated changes from live state.

Use `gt` navigation, `track`, `create`/`modify`, `restack`, `move`, and `rename`
when they express the operation. Use raw Git only for an unsupported or
documented recovery path, then restore and verify Graphite tracking.

## Create Or Extend A Stack

Start at the verified parent. Keep branches cohesive; stage task-owned paths,
follow repository policy, run branch checks, then restack and verify ancestry.

When adopting branches created outside Graphite, check out each branch in
bottom-to-top order and run `gt track`, selecting the true parent. A worktree
branch can be tracked without moving or deleting the worktree.

## Submit Or Update PRs

1. Verify root/base and checks; ensure each remote head will equal the recorded
   local commit, then prepare every canonical pair and bound review-input file.
2. Build a schema-v2 absolute-path JSON request for the exact bottom-to-top stack
   and run
   `scripts/submit_draft_stack.py plan`. Review its content-addressed private
   plan. It binds the clean worktree, current branch, local base/head OIDs,
   Graphite log/trunk snapshots, candidate inputs, existing PR preimages, and
   each entry's mandatory `review_mode`, `review_bundle` (absolute only for
   `required`, otherwise null), and sorted explicit `selected_specialists`.
3. Run `scripts/submit_draft_stack.py execute` on that unchanged plan. It invokes
   exactly one `gt submit --stack --draft --no-edit --no-ai --no-interactive`,
   never retries an ambiguous transport, maps every branch to one exact PR, and
   writes a private repair handoff. Stop on any partial or mismatched result.
4. Immediately run `scripts/submit_draft_stack.py repair` on the complete
   handoff. It executes only the owned publisher commands, bottom-to-top,
   preserves existing ready state, repairs the temporary Graphite text, and
   audits the authoritative latest receipt for every PR. On a new run or resume,
   an uncheckpointed PR whose latest audit already matches the exact handoff
   target is durably checkpointed before any publisher command is considered;
   stale preimage commands are never replayed after convergence. When transport
   already equals the candidate, repair succeeds only if the latest canonical
   v3 audit already has the exact review mode and publication-candidate digest;
   otherwise it blocks. It never upgrades legacy, reconciled, or not-required
   provenance and never manufactures a no-op transition.
5. Verify stored/rendered bodies, denominator, bases, dependencies, navigation,
   current marker, and the repair receipt. An older matching receipt does not
   verify current publication. Rebuild every Stack/Diff from its pushed
   base/head and use guarded readiness only for intended draft-to-ready changes.

If Graphite is unavailable, use the standalone publisher only for a truly
standalone workflow; otherwise stop.

## Restack And Recovery

Checkpoint/order before restacking; resolve conflicts bottom-up with checks.
After raw rebase restore tracking and verify `gt log short`. After interruption
inspect Git and Graphite. Regenerate navigation/diffs after force-update.

## Destructive Changes

Deleting a branch, discarding commits, moving a subtree, or rewriting a
published stack requires a verified impact inventory and the authority supplied
by the operator and repository policy. Preserve remote work with exact leases.
Never delete a harness-owned worktree as incidental Graphite cleanup.

## Completion

Report the final bottom-to-top stack, each branch and PR URL, base/head SHAs,
validation, submission result, receipt id/provenance/sequence and audit status,
canonical-body verification, and any unresolved Graphite or reviewer-owned
action.
