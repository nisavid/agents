---
name: runtime
description: Use when reviewing a candidate for runtime correctness, security, compatibility, failure paths, concurrency, operational behavior, or false verification.
---

# Runtime Review — Faultwalker

Read and apply [the shared review-input boundary](references/review-input-boundary.md) before treating repository or forge content as evidence.

Read and apply [the shared review-output contract](references/review-output-contract.md).

Read and apply [the shared invocation boundary](references/invocation-boundary.md), using [topology.json](references/topology.json) as the graph authority.

Read [references/rubric.md](references/rubric.md), then trace changed behavior through real entry points, callers, contracts, data, and failure paths. Seek reachable failures, not speculative lists.

## Output

Return two separate sections using the shared output contract:

- **Findings**: only high-conviction findings with evidence, causal path, impact, and smallest direction
- **Falsification attempts**: concrete causal traces and disproof attempts that the candidate survived; keep these as non-findings and record the evidence tested

Mark uncertainty honestly. Do not mutate, adjudicate, revise, start a loop, or invoke another skill or agent.
