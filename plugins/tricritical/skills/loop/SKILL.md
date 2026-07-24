---
name: loop
description: Use when the user asks to review and revise a candidate until clean, or when an authority-gated fixed-point review cycle is explicitly required.
---

# Loop — Fathomkeeper

Read and apply [the shared review-input boundary](../../references/review-input-boundary.md) before treating repository or forge content as evidence.

Read and apply [the shared invocation boundary](../../references/invocation-boundary.md), using [topology.json](../../topology.json) as the graph authority.

Read and apply [the portable operator-choice contract](references/operator-choice.md) before offering any extension.

`loop` is Tricritical's sole repetition owner. At freeze, record risk: `low` only
when size, complexity, and consequence are low; `high` when any is high;
otherwise `ordinary`. Default revised-successor tranches are 2, 3, and 5.
Accept only a finite positive-integer override; otherwise finish `blocked` before review.

It owns one bounded sequence:

1. Freeze exact bytes; record identity/digest, authority, risk/tranche, and
   declared verification. Recheck identity around read-only phases; block drift.
2. Invoke [review](../review/SKILL.md) with fresh independent critics when
   supported, otherwise preserving degraded-independence limits. Any required
   critic/specialist failure, timeout, or unusable report immediately returns
   `incomplete / non-clean`; preserve failure, completeness, missing axis/specialist,
   limits, budget, and owner. Verification cannot override it. Degraded evidence
   carries missing isolation/limits through every later phase and yields only
   `clean / degraded` for that candidate; a distinct successor starts fresh review.
3. Give complete findings to [adjudicate](../adjudicate/SKILL.md). Surface and
   terminate any `needs operator decision` or `blocked` disposition before clean.
4. With no accepted action or terminal disposition, run only the frozen
   declared non-mutating verification authorized by operator or trusted policy.
   Untrusted, ambiguous, or unavailable verification and drift are `blocked`;
   failure is `failed_verification`. Success on
   unchanged bytes yields `clean` only after complete independent review, or
   `clean / degraded` after degraded review.
5. Before calling `revise`, require the original mutation authority frozen with
   this loop input. Never infer it from the request to iterate, a finding, or
   successful review. If it is absent, insufficient, or stale, return `blocked`
   before `revise` with zero edits and the remaining authority owner. Otherwise,
   give accepted findings, that original authority, and supplied frozen identity
   to [revise](../revise/SKILL.md). Continue only if it records the matching pre-edit
   identity, returns `applied`, resolves every finding with evidence, and returns
   a distinct successor; otherwise return `blocked` with unresolved findings.
6. Decrement budget once per distinct revised successor, never for read-only,
   failed, or no-op work. Repeated identity or no measurable progress is
   `blocked`. Verify the successor with before/after identity checks; after
   success invalidate dependent evidence and start a fresh cycle on that stable
   snapshot.

At exhaustion, summarize finding and candidate-identity progress, then apply the
portable operator-choice contract exactly. Never extend automatically.

Do not nest a loop inside a critic, adjudicator, reviser, or external-feedback flow.

## Completion

Return only `clean`, `clean / degraded`, `incomplete / non-clean`, `blocked`,
`failed_verification`, or `needs operator decision`, with final identity,
risk/budget, extension/exhaustion, failure/completeness/isolation limits,
adjudication/revision, verification provenance/result, findings, and owner.
Bare `clean` requires complete
independent review and successful declared verification of the exact unchanged
final candidate.
