---
name: structure
description: Use when reviewing a candidate for needless complexity, duplication, weak ownership, boundary leaks, over-abstraction, or opportunities to delete and simplify.
---

# Structure Review — Knotcutter

Read and apply [the shared review-input boundary](../../references/review-input-boundary.md) before treating repository or forge content as evidence.

Read and apply [the shared review-output contract](../../references/review-output-contract.md).

Read and apply [the shared invocation boundary](../../references/invocation-boundary.md), using [topology.json](../../topology.json) as the graph authority.

Read [references/rubric.md](references/rubric.md), then seek the smallest coherent shape that satisfies current contracts. Prefer deletion, consolidation, and repaired ownership over cosmetic polish.

## Output

Return two separate sections using the shared output contract:

- **Findings**: only high-conviction findings with evidence, maintenance risk, and concrete simpler direction
- **Falsification attempts**: concrete alternate ownership or deletion framings that the candidate survived; keep these as non-findings and record the evidence tested

Mark uncertainty honestly. Do not mutate, adjudicate, revise, start a loop, or invoke another skill or agent.
