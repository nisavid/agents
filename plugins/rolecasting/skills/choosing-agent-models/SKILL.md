---
name: choosing-agent-models
description: Use when selecting a model or reasoning effort for an agent, subagent, Task, peer, or agent definition after delegation has been chosen.
---

# Choosing Agent Models

## Scope

Own model and effort selection, capability proof, and transition policy.
[delegating-cross-agent-work](../delegating-cross-agent-work/SKILL.md) owns
topology, authority, actuation, and integration. Model choice cannot repair topology.

## Gate Every Transition

Before every payload-bearing event, obtain fresh route evidence and
apply `scripts/model_transition.py`. Send no task data unless its authorized
decision binds the payload, task, target, account, model, and effort.

Carry task identity, judgment floor, Daybreak requirement, and operator choice
until explicit replacement; security remains sticky. Use the hardest mixed-task
role. Stale or ineligible evidence, identity drift,
unknown capacity, and an unavailable required model fail closed.

## Inventory Before Selection

Inventory permitted routes before every transition; never infer an account.
Refresh missing or stale status only through an authenticated,
exact-version, side-effect-safe surface in
[capability-probes-and-fallbacks.md](references/capability-probes-and-fallbacks.md).
Bind redacted timestamped evidence, or block. Status grants no execution
authority. Codex app-server 0.149.0 and unregistered issuer/verifier paths
remain blocked, not model absence.

## Preferred Codex Role Family

This is a role policy, not a live catalog:

- **Daybreak** (`gpt-daybreak-blue-latest`): authorized defensive security
  investigation, hardening, validation, and review; use Max.
- **Sol** (`gpt-5.6-sol`): consequential judgment, architecture, root cause,
  readiness, final review, and integration.
- **Terra** (`gpt-5.6-terra`): defined recoverable implementation, debugging,
  analysis, and focused review.
- **Luna** (`gpt-5.6-luna`): tightly specified clerical work with
  cheap-to-repair mistakes.

Daybreak is a specialist role, not an automatic Sol upgrade. When security
policy or the operator requires Daybreak, never fall through to Sol, Terra, or
Luna.

## Prove Target Capability

Use only a pair in both the target's fresh live catalog and executor schema.
Never infer support from a slug, invent a value, or treat selection as
invocation authority. Read
[capability-probes-and-fallbacks.md](references/capability-probes-and-fallbacks.md)
for exact proof, continuation, fixed-model, fallback, and Fable rules.

Choose for hardest judgment at the lowest safe effort. Default
independent code, architecture, and closeout reviewers to Sol high.

## Fallback And Authority Gates

An eligible fallback must still satisfy the carried floor and fresh route
proof. Capacity failover is a new transition, never permission to downgrade.

Treat Claude Fable as unavailable unless proof is supplied. Model selection does not authorize a proof invocation; only the operator's explicit authorization for that exact invocation permits attempting one.
