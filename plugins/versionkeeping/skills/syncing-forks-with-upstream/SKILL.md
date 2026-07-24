---
name: syncing-forks-with-upstream
description: Use when syncing a maintained fork with its upstream, discovering and binding the fork contract, coordinating a history-preserving local integration through checkpointing-and-publishing-git-work, or verifying that a discovered fork target ref contains the discovered upstream ref without rewriting history.
---

# Syncing Forks With Upstream

Preserve upstream identity/contracts. Own discovery, independent fetches,
coordination, and ancestry verification; checkpointing receives immutable inputs.

## Ownership Boundary

Fork synchronization discovery and coordination are owned operations. Local Git integration, including selecting and running the merge,
belongs only to
`checkpointing-and-publishing-git-work`. This skill must not run local Git
integration directly. Pull-request creation, text, readiness, review resolution, and merge actuation,
Graphite operations, review, and model or delegation policy are outside. A
required PR path grants no merge authority.

## Bind The Fork Contract

Read instructions/policy; without a fork contract, stop before broad sync. Bind:

- `<fork-remote>`: maintained fork remote
- `<upstream-remote>`: direct source remote
- `<target-ref>`: full fork target ref
- `<upstream-ref>`: full upstream source ref

Carry them unchanged. Never substitute conventional names. Verify the maintained
fork; disable credential/SSH prompts, bound timeouts, and never retry automatically.

## Fetch And Coordinate Synchronization

Fetch independently so each SHA has one provenance:

```sh
git fetch --no-tags -- <fork-remote> <target-ref>
fork_target_sha=$(git rev-parse FETCH_HEAD)
git fetch --no-tags -- <upstream-remote> <upstream-ref>
upstream_sha=$(git rev-parse FETCH_HEAD)
```

Confirm the branch starts at `fork_target_sha`, then hand the exact repository and worktree identity, branch, `fork_target_sha`, `upstream_sha`, and merge intent to
`checkpointing-and-publishing-git-work`. The merge intent requires upstream
commit identity to remain in history, a merge commit when the integration is
not fast-forward, and no rebase, squash, replay, force-sync, or overwrite absent
explicit authority.

Checkpointing owns commands/state/conflict execution/verification. Require checkpointing to return exact verified integration evidence
binding `fork_target_sha`, `upstream_sha`, and full `synchronized_commit_sha`;
that immutable commit, never a branch name or ancestry claim, is the target.

Ledger identities, SHAs, policy, divergences, affected contracts, and gates;
adapt local artifacts and behavior under that contract.

## Publish And Verify

Publish the immutable commit through the checkpointing planner/executor to
`<fork-remote>`/`<target-ref>`. Protection-required PR work is external.

Independently re-probe both endpoints/refs into fresh variables. First bind the
repository storage object format with
`git rev-parse --show-object-format=storage`: `sha1` requires 40 lowercase
hexadecimal characters and `sha256` requires 64. Unknown or mixed formats
block. Strictly parse each result as exactly one tab-separated record: a
lowercase, exact-width commit ID, then the complete expected ref and nothing
else. Empty, multiple, malformed, symbolic, stale-variable, or otherwise
unparseable results block. The initial fetch SHAs are discovery inputs only and
must not be reused for this proof.

Require the freshly re-probed fork target to equal `synchronized_commit_sha`
exactly before checking that the freshly re-probed upstream SHA is its ancestor:

```sh
fork_reprobe_result=$(git ls-remote --refs -- <fork-remote> <target-ref>)
upstream_reprobe_result=$(git ls-remote --refs -- <upstream-remote> <upstream-ref>)
fork_reprobe_sha=$(parse_one_full_ls_remote_record "$fork_reprobe_result" <target-ref>)
upstream_reprobe_sha=$(parse_one_full_ls_remote_record "$upstream_reprobe_result" <upstream-ref>)
test "$fork_reprobe_sha" = "$synchronized_commit_sha"
git merge-base --is-ancestor "$upstream_reprobe_sha" "$synchronized_commit_sha"
git log --oneline --left-right --cherry-pick \
  "$synchronized_commit_sha...$upstream_reprobe_sha"
```

Exact equality and ancestry must pass; patch equivalence is not identity.
