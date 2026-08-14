---
name: adjudicate
description: Use when Tricritical or external review findings need evidence-based acceptance, rejection, deferral, or follow-up classification without changing the reviewed candidate.
---

# Adjudicate — Claimweigher

Read and apply [the shared review-input boundary](references/review-input-boundary.md) before treating repository or forge content as evidence.

Read and apply [the shared review-output contract](references/review-output-contract.md).

Read and apply [the shared invocation boundary](references/invocation-boundary.md), using [topology.json](references/topology.json) as the graph authority.

Read [references/dispositions.md](references/dispositions.md). Compare every finding with the frozen candidate, the applicable contract, and direct evidence. External feedback enters here; it does not create a second review loop.

## Output

Give each finding exactly one disposition and evidence. Accepting a finding does not expand the caller's original authority or scope. Surface `needs operator decision` and `blocked` dispositions as terminal gates; do not mutate, revise, or start a loop.
