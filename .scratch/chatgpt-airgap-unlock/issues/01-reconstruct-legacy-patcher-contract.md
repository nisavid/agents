Type: research
Status: closed
Assignee: nisavid

## Question

What exact offline workflow, gate classes, package surfaces, patch mechanisms, integrity repairs, signing steps, rollback guarantees, and version assumptions did `origin/ivan/chatgpt-unflag` establish, and which of those are reusable requirements rather than stale implementation details?

## Resolution

[Legacy ChatGPT FFS contract](https://github.com/nisavid/agents/blob/76edfcc3dd5c75a16d4a142df49677d8def39ee2/.scratch/chatgpt-airgap-unlock/research/01-legacy-patcher-contract.md)

Retain the legacy tool's artifact-bound, preconditioned, integrity-aware, nested-signing, rollback-first lifecycle as requirements. Treat its renderer gate registry and native no-auth patch only as discovery evidence: neither the historical IDs and globs nor the final fixed offset establishes the authoritative control point or a working offline GLM workflow for build `26.707.71524` (`5263`).
