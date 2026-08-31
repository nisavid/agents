---
name: choosing-agent-models
description: Use when selecting a model or reasoning effort for an agent, subagent, Task, peer, or agent definition after delegation has been chosen.
---

# Choosing Agent Models

## Scope

Own model and reasoning-effort selection, fresh capability proof, and
transition policy. [delegating-cross-agent-work](../delegating-cross-agent-work/SKILL.md)
owns topology, authority, the pre-actuator enforcement point, handoffs, and
integration. Model choice cannot repair topology or make an unrelated task an
eligible route.

## Gate Every Transition

Before every payload-bearing new task, new subagent, follow-up, resume, retry,
capacity recovery, or reclassification, obtain fresh route evidence and apply
`scripts/model_transition.py`. Send no task data unless its
decision is authorized and the actuator binds it to the exact payload, task,
target, account, model, and effort.

Carry task identity, judgment floor, Daybreak requirement, and operator
selection. Preserve an operator choice until an explicit
replacement. Reclassification requires its own evidence; security remains
sticky. Classify mixed-role work at its hardest role. A stale selector,
ineligible task, changed continuation account or target, unknown capacity, or
unavailable required model fails closed.

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

Choose for the hardest judgment and use the lowest safe effort. Default
independent code, architecture, and closeout reviewers to Sol high.

## Fallback And Authority Gates

An eligible fallback must still satisfy the carried floor and fresh route
proof. Capacity failover is a new transition, never permission to downgrade.

Treat Claude Fable as unavailable unless a current proof is supplied. Model selection does not authorize a proof invocation; only the operator's explicit authorization for that exact invocation permits attempting one.
