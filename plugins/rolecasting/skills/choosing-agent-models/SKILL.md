---
name: choosing-agent-models
description: Use when selecting a model or reasoning effort for an agent, subagent, Task, peer, or agent definition after delegation has been chosen.
---

# Choosing Agent Models

## Scope

Own model/effort selection and capability proof post-delegation. [delegating-cross-agent-work](../delegating-cross-agent-work/SKILL.md) owns family, surface, version, executor, relationship, ownership, transport, assurance, authority, handoffs, and integration. Model choice is a separate decision; it cannot repair topology.

Choose the least costly model that safely preserves required judgment. Escalate for ambiguity, hard-to-reverse decisions, architecture, reviewer-facing work, or final integration.

## Preferred Codex Role Family

This is the preferred bounded GPT-5.6 role snapshot as of 2026-07-20, not a catalog:

- **Sol** (`gpt-5.6-sol`): consequential judgment, unresolved architecture or diagnosis, final review, and integration
- **Terra** (`gpt-5.6-terra`): defined, recoverable implementation, debugging, analysis, and review
- **Luna** (`gpt-5.6-luna`): tightly specified, non-judgment legwork with cheap-to-repair mistakes

Prefer these stable role semantics over enumerating volatile models. Luna is not a substitute for Terra or Sol on judgment work.

## Prove Target Capability

Use only a pair proven by both the target's live catalog and executor schema.
For Codex, probe `codex debug models`; otherwise probe the target harness.
Never infer executor support, invent a slug or effort, or treat selection as
invocation authority.

Read [capability-probes-and-fallbacks.md](references/capability-probes-and-fallbacks.md) only when selecting an explicit pair, handling an unavailable preference, using another harness, or evaluating Fable.

## Selection Matrix

- Use **Sol** for scope, architecture, root cause, readiness, or final integration.
- Use **Terra** for precise implementation, recoverable debugging, focused review, or tests against a settled contract.
- Use **Luna** for exact extraction, rubric classification, monitoring, or clerical edits.

Default independent code, specification, architecture, and closeout reviewers
to **Sol high**. In Cursor, proven `cursor-grok-4.5-high` is also strong for
consequential review, especially for an authorized foreign-harness perspective.
Keep the same authority, evidence, and integration boundaries. Use a weaker
reviewer only with concrete contrary evidence.

Select for the hardest judgment; use the lowest safe effort.

## Fallback And Authority Gates

Use the referenced fallback rules when the preferred role is unavailable. Report a material fallback when it changes confidence, cost, or speed.

Treat Claude Fable as unavailable unless a current proof is supplied. Model selection does not authorize a proof invocation; only the operator's explicit authorization for that exact invocation permits attempting one.
