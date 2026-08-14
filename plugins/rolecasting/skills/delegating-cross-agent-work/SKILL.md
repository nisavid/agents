---
name: delegating-cross-agent-work
description: Use when deciding whether and how to delegate across child, peer, external, leader-owned, or user-owned worker surfaces.
---

# Delegating Cross-Agent Work

## Scope

Keep ambiguity, consequential judgment, architecture, copy, and coordination local. Delegate bounded parallel, specialist, or waiting work.

[choosing-agent-models](../choosing-agent-models/SKILL.md) owns model and effort selection. This skill owns target surface, worker topology, authority, bounded handoffs, assurance, and leader integration.

## Choose the Worker Contract

Choose each axis independently:

- target: exact family, surface, version, and executor;
- relationship: `child`, `peer`, or `external`;
- ownership: `leader-owned` or `user-owned`;
- transport: `native-tool`, `task-api`, `cli`, `app-server`, or `remote-api`; and
- required assurance: `product-attested`, `controller-observed`, or `self-reported`.

Do not infer one axis from another. Use a leader-owned child for bounded work the leader creates and ends. A leader-owned peer may have an independent session while remaining leader-controlled. A user-owned peer or task requires explicit user consent to create or steer. Use an external worker only when crossing the selected product or control boundary materially helps.

Read [foreign-harness-peers.md](references/foreign-harness-peers.md) before probing a non-native or separately owned surface.

## Kickoff

Record the leader, repository, branch, immutable base, dirty state, submodules, and owning worktree. Compare plans with live state and policy before dispatch. Freeze every topology axis and the consumer's minimum assurance before model choice or edit authority. If model or effort remains unresolved, use `choosing-agent-models`.

## Bounded Handoff

Every prompt is a bounded task contract: goal and success criteria; worktree and immutable base; relevant facts and target behavior; allowed scope and read/write authority; subdelegation and external-action authority; verification; output; and stop conditions. Unstated authority is absent. Workers preserve user and peer edits.

For native ChatGPT Codex or Codex CLI/TUI children, follow
[native-codex-subagents.md](references/native-codex-subagents.md): freeze before
spawning and record only host-protocol observations.

For a caller that needs a frozen multi-worker dispatch plan, issue the portable
contract defined by [invocation-topology-receipt.md](references/invocation-topology-receipt.md).
The harness adapter serializes it as `adapter:rolecasting-invocation-topology-receipt`; model selection remains separate and never supplies dispatch authority. For witnessed execution and assurance minima, use [dispatch-evidence.md](references/dispatch-evidence.md).

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
