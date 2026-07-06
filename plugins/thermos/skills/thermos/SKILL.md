---
name: thermos
description: Run paired thermo-nuclear review passes and synthesize findings. Use when the user asks for thermos, double thermo review, or combined risk and maintainability branch review.
---

# Thermos

Run two independent review passes, then synthesize one findings-first verdict.

## Workflow

1. Define the review scope. Complete this when the base, head, and included paths are explicit.
2. Gather the diff and changed-file context needed for both reviewers to evaluate without guessing. Hand each reviewer the same context under labeled sections: `### Git / diff output` and `### Changed file contents`.
3. On harnesses that ship the Thermos review subagents (Claude Code: `thermos:thermo-nuclear-review-subagent` and `thermos:thermo-nuclear-code-quality-review-subagent`), dispatch both in a single message so they run in parallel, each with the same scoped diff and file context.
4. If the harness has generic/native subagent dispatch but does not ship named Thermos subagents, dispatch two generic reviewer subagents in parallel with the same scoped diff and file context. Tell one to apply `thermo-nuclear-review` for risk (correctness, security, breaking behavior, devex regressions, feature-gate leaks), and tell the other to apply `thermo-nuclear-code-quality-review` for maintainability (structure, code-judo opportunities, file-size pressure, abstractions, boundaries, codebase health).
5. Only when subagent dispatch is unavailable or clearly not worth the overhead, run those two passes sequentially in the main context while keeping their findings separate until synthesis.

## Synthesis

- Keep reviewer findings separate until both passes finish.
- Deduplicate findings that share the same cause, even when one reviewer frames it as risk and the other as maintainability.
- Weight independently confirmed issues more heavily, but do not merge weaker claims into a stronger finding unless the evidence supports it.
- Resolve disagreements with direct source inspection, not by averaging reviewer confidence.
- Do not restate reviewer output that is already visible to the user.

## Output

Lead with deduplicated findings ordered by severity. Include file:line evidence, impact, and the smallest actionable remedy. After the findings, name only residual risk that could change the verdict, such as missing tests, unreachable context, or unresolved disagreement. If no high-conviction findings survive, say so directly.
