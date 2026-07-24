---
name: stacking-pr-fixups
description: Use when preparing a narrow stacked PR fixup or follow-up patch whose base is another open PR head.
---

# Stacking PR Fixups

A fixup branch is a narrow patch stacked on the reviewed PR's head. Branch
prefixes come from repository/operator policy; never infer a personal prefix.

## Workflow

1. Resolve the base PR by exact repository, number, open state, head ref/OID,
   head repository, owner, and URL. If a branch is supplied, continue only when
   exact filtering selects one open PR.
2. Confirm the fixup can target that head repository and branch. Stop on fork
   ambiguity or when repository policy requires a same-repository base.
3. Derive the branch prefix and naming grammar from repository/operator policy.
   Remove a trailing fixup ordinal before appending the next unused ordinal;
   scan local heads, relevant remote heads, Graphite topology, and open PR heads.
4. Keep the patch limited to the authorized review fixes. Call the imported
   `git-ref-push` operation
   (`versionkeeping:checkpointing-and-publishing-git-work`) for task-owned
   checkpoints and exact ref/push safety.
5. For a Graphite-managed stack, call [Graphite](../graphite/SKILL.md) for
   `graphite-topology` and temporary `graphite-transport`.
6. Give the exact pushed diff to the `pr-content` operation owned by
   [Write Reviewable PR Descriptions](../writing-reviewable-pr-descriptions/SKILL.md),
   then give its validated title/body and readiness decision to the publisher's
   exact `pr-creation`, `pr-text-write`, or `pr-readiness-write` operation in
   [Publish Reviewable PRs](../publishing-reviewable-prs/SKILL.md).
7. Verify base/head identities, draft/ready state, canonical body, and URL, then
   use the publisher's read-only `publication-audit` operation to require the
   authoritative latest publication receipt to match that live state.

## Readiness

- A complete bounded fix list may proceed through the publisher's guarded ready
  operation when repository/operator policy authorizes it.
- Uncertain or likely incomplete fixes stop at a draft.
- With no fixes, prepare only the authorized branch state; do not open an empty
  PR.

Top-level comments, review requests, feedback interactions, and merge actuation
belong to other operation owners and are not performed here.
