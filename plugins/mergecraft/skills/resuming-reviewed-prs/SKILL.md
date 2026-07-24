---
name: resuming-reviewed-prs
description: Use when returning to a stale, neglected, conflicted, CI-blocked, or otherwise long-running reviewed pull request and needing to recover its exact target, local checkout, authority, and next lifecycle owner.
---

# Resuming Reviewed PRs

Recover the reviewed PR's exact target and operating boundary, then select one
outcome owner without replaying completed work. A status-only request remains
read-only: return the bound current-state report with terminal status
`reported`, then stop without selecting an owner or acquiring mutation or merge
authority.

1. Resolve the exact repository and PR from the supplied URL, number, branch,
   or checkout. Stop when inputs identify different targets.
2. Record local checkout, current branch and SHA, dirty state, unpublished work,
   and any in-progress operation. Preserve unrelated work.
3. Read current operator and repository policy. State local, Git, forge,
   reviewer, readiness, and merge authority; unresolved authority is a gate.
4. Re-read repository, PR, base/head refs and OIDs, exact head repository and
   owner, and open/draft state. Stop on mismatch or ambiguity.
5. When canonical publication evidence applies, call
   the read-only `publication-audit` operation owned by
   [Publish Reviewable PRs](../publishing-reviewable-prs/SKILL.md) for the
   authoritative latest receipt. Treat `drift` or
   `unavailable` as evidence to inspect, not permission to replay a mutation.
6. Unless this is a status-only request, return exactly one terminal owner
   handoff in this precedence order, then
   stop this invocation. The caller starts the named owner in a fresh invocation
   from live state; this coordinator never invokes another outcome coordinator.
   - an active conflict in a merge, rebase, cherry-pick, or revert operation:
     `versionkeeping:resolving-merge-conflicts` for conflict interpretation and
     authorized file edits, followed by a fresh resume invocation after its
     terminal Git-mechanics handoff completes;
   - failed required GitHub Actions:
     `operation:focused-ci`, the internal Actions-only upstream `gh-fix-ci`
     seam, followed by a fresh resume invocation after its terminal outcome;
   - an explicit merge, ship, or merge-closeout outcome, even when readiness or
     feedback work is also pending:
     [Get PRs Merged](../getting-prs-merged/SKILL.md);
   - otherwise, live feedback or requested changes:
     [Address PR Review Feedback](../addressing-pr-review-feedback/SKILL.md);
   - otherwise, a draft or readiness-only outcome:
     [Get PRs Ready For Review](../getting-prs-ready-for-review/SKILL.md).

This skill acquires no feedback detail, decides no disposition, and performs no
source, Git, publication, interaction, readiness, or merge mutation. Conflict,
CI-blocked, and status-only recovery therefore work without merge authority.
