---
name: delegating-cross-agent-work
description: Use when deciding whether and how to delegate across child, peer, external, leader-owned, or user-owned worker surfaces.
---

# Delegating Cross-Agent Work

## Scope

Keep consequential judgment and coordination local. Delegate bounded parallel,
specialist, or waiting work.

[choosing-agent-models](../choosing-agent-models/SKILL.md) owns model selection,
fresh capability proof, and transition policy. This skill owns target surface,
topology, authority, the pre-actuator transition gate, bounded handoffs,
assurance, and leader integration.

## Choose the Worker Contract

Freeze each axis independently:

- target: exact family, surface, version, and executor;
- relationship: `child`, `peer`, or `external`;
- ownership: `leader-owned` or `user-owned`;
- transport: `native-tool`, `task-api`, `cli`, `app-server`, or `remote-api`; and
- required assurance: `product-attested`, `controller-observed`, or `self-reported`.

Do not infer one axis from another. A leader-owned child is bounded work the
leader creates and ends. A leader-owned peer may have an independent session.
A user-owned peer requires explicit consent to create or steer. Cross a
product or control boundary only when it materially helps.

Task identity and purpose are route gates; model availability never makes an
unrelated task valid. Read
[foreign-harness-peers.md](references/foreign-harness-peers.md) before using a
non-native, separately owned, companion, sibling, or dedicated-task surface.

## Kickoff

Record leader, repository, branch, immutable base, dirty state, submodules, and
owning worktree. Compare the plan with live state and policy. Freeze topology
and consumer assurance minima before model choice or edit authority.

## Gate Every Payload

Use `choosing-agent-models` before every payload-bearing new dispatch,
follow-up, resume, retry, capacity recovery, or reclassification—even when a
model was previously resolved. Require complete route inventory and status
preflight plus fresh eligibility, selector, capability, account, target,
execution-authority, and capacity evidence. Bind the guard's authorized
decision immediately before the actuator accepts the payload; denial sends
none. Never treat denied status as model absence or capacity loss as a
downgrade route.

## Bounded Handoff

Every prompt binds goal, success criteria, worktree, immutable base, relevant
facts, target behavior, scope, read/write and subdelegation authority,
external-action authority, verification, output, and stop conditions.
Unstated authority is absent. Workers preserve existing edits.

For native ChatGPT Codex or Codex CLI/TUI children, follow
[native-codex-subagents.md](references/native-codex-subagents.md).

For frozen multi-worker plans, issue
[invocation-topology-receipt.md](references/invocation-topology-receipt.md).
The harness serializes it as `adapter:rolecasting-invocation-topology-receipt`.
Model selection never supplies dispatch authority. Use
[dispatch-evidence.md](references/dispatch-evidence.md) for witnessed execution.

Ask workers to return one status:

- `DONE`: complete and verified
- `DONE_WITH_CONCERNS`: complete with concrete concerns
- `NEEDS_CONTEXT`: a named fact or decision is missing
- `BLOCKED`: impossible within current scope, authority, or environment

Before retrying `NEEDS_CONTEXT` or `BLOCKED`, change the named missing input,
authority, boundary, or capability and authorize a new transition.

## Batch and Wait

Batch small independent same-shape work; separate work needing its own judgment,
tests, or review surface. Do useful local work before a bounded wait, then
reconcile live children and recover missed terminal results.

## Leader Integration

- Keep parallel edit scopes disjoint and record the immutable base before edits.
- Review returned patches, claims, captures, logs, and summaries.
- Treat results as inputs; reconcile, integrate, and verify centrally.
- Retain final authority for architecture, root cause, user-facing wording, and
  consequential external actions.
