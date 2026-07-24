---
name: delegating-cross-agent-work
description: Use when deciding whether and how to delegate across native subagents, user-owned Codex tasks, foreign-harness peers, browser/computer-use workers, or separate worktrees.
---

# Delegating Cross-Agent Work

## Scope

Lead the work. Keep ambiguity, consequential judgment, architecture, design taste, human-facing copy, and coordination local. Delegate bounded execution that benefits from parallelism, specialist tooling, or waits outside the critical path.

[choosing-agent-models](../choosing-agent-models/SKILL.md) owns model and effort selection. This skill owns worker topology, lifecycle, authority, bounded handoffs, and leader integration.

## Choose the Worker Lifecycle

- **Native subagent:** use for tight, short-lived work in the current harness. The leader creates, scopes, reviews, and ends it.
- **User-owned Codex task or thread:** create only when the user explicitly asks. It belongs to the user; never create or steer one as an internal subtask.
- **Foreign-harness peer:** use when an independent lifecycle, browser/computer use, separate worktree, long wait, or cross-harness execution materially helps. It owns only its stated session and scope; it cannot widen scope, subdelegate, or take consequential external actions without explicit authority.

When selecting a foreign-harness peer, read [foreign-harness-peers.md](references/foreign-harness-peers.md) before probing or dispatch. A cross-harness CLI or API launch is always a foreign-harness peer.

## Kickoff

Record the leader, repository, branch, immutable base, dirty state, submodules, and owning worktree. Compare plans with live state and policy before dispatch. Choose the lifecycle and boundary before edit authority. If model or effort remains unresolved, use `choosing-agent-models`.

## Bounded Handoff

Every prompt is a bounded task contract: goal and success criteria; worktree and immutable base; relevant facts and target behavior; allowed scope and read/write authority; subdelegation and external-action authority; verification; output; and stop conditions. Unstated authority is absent. Workers preserve user and peer edits.

For a caller that needs a frozen multi-worker dispatch plan, issue the portable
contract defined by [invocation-topology-receipt.md](references/invocation-topology-receipt.md).
The harness adapter serializes it as
`adapter:rolecasting-invocation-topology-receipt`; model selection remains a
separate input and never supplies dispatch authority.

Ask workers to return one status:

- `DONE`: complete and verified
- `DONE_WITH_CONCERNS`: complete with concrete concerns
- `NEEDS_CONTEXT`: a named fact or decision is missing
- `BLOCKED`: impossible within current scope, authority, or environment

On `NEEDS_CONTEXT` or `BLOCKED`, change the input, authority, boundary, or capability before retrying.

## Leader Integration

- Keep parallel edit scopes disjoint.
- Record the immutable base before edit-capable delegation; never infer it as `HEAD~1`.
- Review returned patches, claims, captures, logs, and summaries.
- Treat every worker result as input, not an integrated decision.
- Reconcile and integrate centrally in the owning worktree, then verify the final contract.
- Retain final authority for architecture, root cause, user-facing wording, and consequential external actions.
