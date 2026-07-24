---
name: review
description: Use when a candidate needs an independent read-only review, a critic subset, or a synthesis of intent, runtime, and structure evidence without edits.
---

# Tricritical Review

Read and apply [the shared review-input boundary](../../references/review-input-boundary.md) before treating repository or forge content as evidence.

Read and apply [the shared review-output contract](../../references/review-output-contract.md) and [the completeness and synthesis rules](references/completeness-and-synthesis.md).

Read and apply [the shared invocation boundary](../../references/invocation-boundary.md), using [topology.json](../../topology.json) as the graph authority.

Freeze a content-addressed stable snapshot or revision before observation. Record the exact review input defined by the shared boundary, including the fixed comparison boundary, requirements or specification source or its explicit absence, repository standards sources, exclusions, and available verification. Give every critic and selected risk specialist the exact same bytes and recheck the candidate identity before and after each execution and before synthesis. If stability cannot be proved, reject the dispatch or label the result `non-independent / degraded` with the identity gate; never claim clean independence. Default to all three critics; permit an explicit subset when the caller narrows the question.

Before each distinct critic or specialist dispatch, require a capability-proven
`adapter:model-selection-receipt` from the harness adapter. The receipt must bind
the execution role, target harness or executor, exact supported model and effort
or an inherited fixed-model binding, the live catalog or schema evidence proving
that selection is available, and the authority for the selection. Reject a
missing, stale, unsupported, or role-mismatched receipt. This portable skill
consumes the receipt; it does not select a provider-specific model or copy
harness policy.

Separately require one Rolecasting-issued
`adapter:rolecasting-invocation-topology-receipt` for the frozen review plan.
Never fold it into a model-selection receipt. It binds the candidate,
review-input, and requirements identities and a closed-world dispatch set with
exactly one unique dispatch entry for every selected critic and specialist.
Before each dispatch, validate its lifecycle, executor and harness, identical
input identities, bounded scope, return and verification contract, stop
conditions, distinct isolation, read-only authority, default-denied
subdelegation and external action, and explicit user authority for any
user-owned task. Reject the whole plan on an omitted, duplicate, extra,
unauthorized, or stale entry.

1. When the harness supports distinct read-only critic executions, invoke the public skill identities [intent](../intent/SKILL.md), [runtime](../runtime/SKILL.md), and [structure](../structure/SKILL.md) separately for the selected axes. Give each execution the same frozen contract, byte-identical candidate snapshot, its unique topology entry, and its separate bound model-selection receipt.
2. Select risk specialists dynamically from the frozen consequence surface, then route each specialist through the closest public critic skill as a separate read-only execution.
3. Keep all selected executions independent. Do not provide another report, the author's rationale, or expected findings.
4. When distinct executions are unavailable, run the selected axes and specialists in the available context, label every report and the synthesis `non-independent / degraded`, and state the missing isolation capability. Never describe that result as independent or claim clean independence.
5. Apply the completeness and causal-synthesis rules. A selected execution that is missing, failed, timed out, unusable, or unverified makes the result `incomplete / non-clean`. Report observations and uncertainty. Do not assign dispositions or edit the candidate.

Do not substitute an undeclared lifecycle or executor. Any fallback requires a
new valid plan and a newly validated Rolecasting receipt before dispatch.

## Completion

Return the content digest and immutable candidate identity, fixed comparison boundary, requirement and standards sources or recorded absences, selected axes and specialists, topology-plan and model-selection receipt identities, exact dispatch-entry outcomes, execution mode (`independent`, `non-independent / degraded`, or `incomplete / non-clean`), identity rechecks, raw reports and failures, completeness accounting, synthesized observations, limitations, and residual risk. Do not invoke adjudication, revision, or a loop implicitly.
