---
name: using-persistent-git-worktrees
description: Use when starting isolated feature work or choosing, auditing, moving, repairing, and handing off persistent Git worktrees, especially when an agent would otherwise use an ephemeral, global, or ambiguous worktree location.
---

# Using Persistent Git Worktrees

Apply this policy for durable, human-discoverable coding-agent worktrees. Read
repository worktree guidance first. Precedence is user instruction, repository
policy, this skill, then a compatible base worktree workflow.

## Ownership Boundary

This skill owns persistent Git worktree location, creation, movement, repair,
and handoff. It does not own Graphite worktrees or stack operations, terminal
branch/worktree cleanup, review or pull-request actuation, or model and
delegation policy. For any completion, discard, or cleanup, hand off to the
checkpointing skill in this plugin and load its terminal-cleanup reference.

## Directory Policy

Default to a sibling `.wt` directory beside the main clone unless repository
documentation or the user requires another persistent location.

```text
<parent>/project
<parent>/project.wt/<branch-or-task-name>
```

Do not place persistent worktrees under temporary, cache, or automatically
cleaned locations. Use those only for disposable experiments with no branch
work. Report the full path and branch after creation.

If the sibling path or shared Git metadata needs additional permission, request
it. Do not reroute durable work to an ephemeral path merely because it is
writable.

## Creation And Checks

Create through the bundled guarded operation:

```sh
scripts/validate_worktree_target.py --create \
  --main-clone <main-clone> --name feature-x --branch feature-x
```

Add `--existing-branch` only when the exact branch already exists. The operation
rejects absolute, traversing, option-like, symlinked, non-sibling, or already
present destinations and unsafe branch refs. It requires the sibling parent,
main clone, and worktree root to be current-user-owned and not group- or
world-writable; creates a missing root with mode `0700`; revalidates the parent,
clone, root, and target immediately around `git worktree add`; disables ambient
Git hooks and terminal prompts; uses a credential-minimized environment; closes
stdin; bounds every Git call; and verifies the resulting worktree's exact
symbolic branch. Any timeout or failure after the mutator starts is `unknown`,
even when no target directory remains.
Do not reconstruct a receipt, run the Git command separately, or override a
blocked or `unknown` result by hand.

The guard prevents replacement by other local users once the immediate parent
and sibling root satisfy those permissions. A privileged process or another
process running as the same user remains outside this filesystem guarantee; do
not run concurrent worktree mutators. Initialize recursive submodules when the
task depends on them. At task start, handoff, and focus changes, inspect the main
checkout and active worktree and name the active path.

## Move, Repair, And Handoff

Prefer `git worktree move <old-path> <new-path>` for a wrongly placed worktree.
Never copy a worktree with `cp -a`. If moving fails and the worktree is clean,
remove and recreate only after confirming its identity. If it has uncommitted
work, stop and ask.

A handoff states the main checkout status, active worktree path and branch,
latest relevant commits, uncommitted files, and any stale copy or cleanup still
needed. This skill stops at that handoff; the checkpointing skill is the sole
terminal cleanup owner.
