# Terminal cleanup

Terminal branch and worktree cleanup belongs only to the checkpointing skill.
Do not begin cleanup while ordinary iteration, pull-request work, or feedback is
active.

## Provenance matrix

Classify the workspace from creation records, harness metadata, or an explicit
operator statement; path-name heuristics are not evidence.

| Provenance                                                       | Allowed terminal action                                                                                                                                                                             |
| ---------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Normal checkout                                                  | Check out the verified safe base before deleting only the selected branch.                                                                                                                          |
| Directly agent-created worktree                                  | Raw Git cleanup is allowed only when there is an explicit record that this agent ran `git worktree add`. A clean worktree may be removed only after independently authorized terminal cleanup. A dirty worktree is quarantined and retained; it is never force-removed or branch-deleted. |
| Harness-created worktree                                         | Use only the harness's native cleanup actuator; never run raw Git worktree removal.                                                                                                                 |
| User-created, externally managed, or unknown-provenance worktree | Preserve it and hand off without cleanup.                                                                                                                                                           |

For local merge, integrate into the verified intended base and run required
verification on the merged result before terminal cleanup. Cleanup is
target-local. Never run global `git worktree prune`, alter a different branch or
worktree, or infer authority from a generic completion request.

## Remote branch cleanup

Pull-request merge actuation remains outside Versionkeeping. After a merge has
been independently verified, terminal deletion of its exact remote branch is
available only through this skill's separate remote-ref deletion route. Require
both explicit repository and operator authorization for the exact remote, full
ref, and expected target SHA before planning. A generic cleanup request, a
verified merge outcome by itself, or authorization for a different target does
not grant deletion authority.

## Destructive discard

Enumerate the branch, commits, uncommitted files, and exact worktree path, then
wait for the operator to choose a terminal action. A clean target may use the
independently authorized terminal route in the provenance matrix. A dirty,
directly agent-created worktree has a retention-only route: atomically move the
complete worktree to a durable, operator-visible, same-filesystem quarantine,
retain its Git worktree registration and branch, and require a later separately
authorized cleanup only after writers are quiescent.

Before asking for that quarantine confirmation, choose one exact quarantine
path beneath a durable operator-visible quarantine root on the same filesystem
as the worktree. Prove that the selected path is absent, every existing parent
in the route is a safe non-symlink directory, and the destination cannot escape
the approved quarantine root. Bind the original registered worktree path, the
selected quarantine path, that absence/safety proof, and the retention-only
semantics into the confirmation. The operator must type exactly
`quarantine <selected-quarantine-path>`; the placeholder is replaced with the
complete bound path. This grants only that move, never deletion of retained
bytes, worktree registration, or branch.

The confirmation binds one complete content-addressed dirty snapshot. At the
top level and recursively for every submodule, it contains a stable, lexically
sorted repository-relative path inventory of every index, worktree, untracked,
and ignored entry that discard or quarantine handling could affect. Bind each
entry's bytes, type, and executable mode; record an explicit absent
marker for a deleted side; and bind the inventory itself so additions, removals,
and deletions change identity. This includes an ignored top-level sentinel when
present. Hash regular files by streaming their bytes, including large files
within the finite observation budgets; never substitute size, mtime,
path/status, sampling, or a byte prefix for content identity. Observe symlinks
as entries without following them outside the top-level worktree or submodule
root.

Define each submodule identity recursively as its recorded gitlink, worktree
HEAD, and a recursive content-addressed dirty snapshot. Apply the
top-level inventory, byte, type, executable-mode, absence-marker, symlink
no-follow, and finite-budget rules unchanged to each nested submodule. If any
inventory or content is unreadable or cannot be fully enumerated, a path is
unsafe or escapes its root, or recursion is cyclic, the identity is unknown and
the worktree is preserved in place. A gitlink and worktree HEAD alone are never
sufficient.

The complete recursive snapshot has finite observation budgets:

- Recursive submodule depth is at most 16 levels, counting the first submodule
  as level 1.
- The stable lexical inventory is at most 100,000 entries across the complete
  recursive snapshot.
- Each observed regular-file byte stream is at most 1,073,741,824 bytes
  (1 GiB).
- All observed regular-file byte streams total at most 8,589,934,592 bytes
  (8 GiB) across the complete recursive snapshot.

These are observation limits, not authority to truncate, sample, omit, or hash
only a prefix. Exceeding any limit makes the identity unknown and preserves the
worktree in place.

At most three complete attempts may be made to establish stability. Accept the
identity only after two consecutive complete canonical snapshots match
byte-for-byte, including their lexical inventories and every bound identity.
After a mismatch, discard that observation and re-enumerate from the beginning.
If no consecutive pair matches by the third attempt, or unstable growth exceeds
a budget during any attempt, the identity is unknown and the worktree is
preserved in place.

If a path is deleted unexpectedly, unreadable, changes type, cannot be fully
hashed, or has a submodule identity that cannot be read, its identity is unknown
and the worktree is preserved in place.

Immediately after confirmation, re-enumerate the confirmed branch, registered
worktree, commit, selected quarantine path, destination absence/safety proof,
and every bound dirty-path identity before moving anything. An earlier snapshot
or confirmation is stale once state may have changed. A same-path identity
change, including changed bytes with the same path and status, requires stopping
and obtaining a fresh exact `quarantine <selected-quarantine-path>` confirmation
for the new enumeration. This includes a same-HEAD, same-status nested byte
change such as `vendor/x/notes.md` inside submodule `vendor/x`.

Only after that re-enumeration matches, use a single same-filesystem atomic
worktree move to the exact bound quarantine path. The move does not update
Git's common-directory registration. From the already verified owning
repository, immediately run the exact-target registration repair
`git worktree repair <selected-quarantine-path>`; do not prune or repair any
other worktree. Verify through `git worktree list --porcelain` and the
reciprocal `.git`/common-directory `gitdir` records that the same registered
worktree now resolves only at the quarantine path and that its branch remains
retained.

A file such as `late.bin` created before or during that atomic move travels
with the complete directory and remains in quarantine. A file created at the
old path after the move is outside the moved worktree; do not delete it, and
report it for the operator. Never run `git worktree remove --force`, delete the
quarantined worktree, or delete its branch for this dirty-worktree route. On
ambiguity, drift, or failure before the move, preserve the original worktree
in place. If the exact-target registration repair fails after the move,
preserve the quarantined directory and branch in place, report the stale
registration, and do not move, prune, or delete anything else.
