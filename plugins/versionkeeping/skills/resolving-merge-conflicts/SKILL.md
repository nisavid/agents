---
name: resolving-merge-conflicts
description: Use when an active Git merge, rebase, cherry-pick, or revert has conflicts that require intent-aware interpretation and authorized file edits before Git integration can continue or abort.
---

# Resolve Merge Conflicts

## Ownership Boundary

This skill owns conflict interpretation and authorized file edits. It never
stages paths and never commits its own resolution. The
[checkpointing-and-publishing-git-work](../checkpointing-and-publishing-git-work/SKILL.md)
owner exclusively performs the Git mechanics to stage, continue, commit, and push
after a verified resolution handoff.

An authorized abort remains a valid outcome. Return an exact abort handoff to
the checkpointing owner instead of editing files or running the operation's
abort command here. Do not impose a never-abort rule, and do not infer abort
authority from the existence of a conflict.

## Bind The Active Operation

Read current Git state without mutation. Bind repository and worktree identity,
HEAD, branch or detached state, operation kind, operation metadata, index
stages, every unmerged path, unrelated staged and dirty state, and the intended
integration outcome. Distinguish merge, rebase, cherry-pick, and revert
semantics before interpreting ours, theirs, base, or continuation intent.

Bind repository policy, requirements, exact owned conflict paths, file-edit
authority, validation, and whether abort is authorized. A missing intent,
mixed-ownership path, unrelated conflict, operation drift, or missing authority
blocks with zero edits.

## Resolve Or Abort

For a resolution outcome:

1. Inspect every stage and the surrounding source contract.
2. Choose the intended combined behavior; do not select one side mechanically.
3. Edit only authorized conflicted paths, remove all conflict markers, and
   preserve unrelated index and worktree state.
4. Run the declared focused validation and reread the active operation and
   unmerged-path inventory. New conflicts or identity drift invalidate the
   handoff.

For an authorized abort outcome, edit nothing. Freeze the exact operation and
abort authority for the Git-mechanics owner.

## Terminal Handoff

Return exactly one terminal handoff to
`checkpointing-and-publishing-git-work`. A resolution handoff binds the active
operation identity, pre-edit and resolved path identities, path modes and
digests, remaining-unmerged inventory, validation, and allowed continuation.
An abort handoff binds the same operation identity and the explicit authorized
abort choice. The recipient rereads live state before any index or operation
mutation and stops on drift.
