# Independent recheck of the journal current-slot contract

**Disposition: the original design-blocking finding is resolved at
`90108b516f5a1c460980a93670348f6e228124f2`.** The acceptance specification now
defines one operation-grant current slot by `grant_id` and one
evidence-tier-result current slot by `(claim_id, tier)`. `subject_revision`
remains part of the immutable result value, so a subject change replaces that
same pointer with `STALE` instead of allocating a second current slot. The
practical consequence is that the protected lifecycles and their disposable
fixtures can use the same selectors through setup, preflight, and independent
oracle comparison; I found no contradiction introduced in an affected
publication, restart, compatibility, or evidence dependency.
([typed slot and value contract](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L2096-L2138),
[corrected key grammars](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L3340-L3389),
[subject-change lifecycle](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L5632-L5684))

## Assessed source and bounded scope

I assessed the immutable source commit
[`90108b516f5a1c460980a93670348f6e228124f2`](https://github.com/nisavid/agents/commit/90108b516f5a1c460980a93670348f6e228124f2)
against original design base
[`e34334e9fccec9e9c19b30e5d523b5c67720ba60`](https://github.com/nisavid/agents/commit/e34334e9fccec9e9c19b30e5d523b5c67720ba60).
The correction changes only `tooling/hindsight/docs/journal-acceptance-evidence.md`.
I read the original independent report as the question and evidence trail,
including its exact finding and requested recheck surface, without adopting its
verdict or reading reports from the correction work.
([original finding](https://github.com/nisavid/agents/blob/a95e4d136be8b5af8d8ae7cb85035e8e0c22e693/tooling/hindsight/docs/research/journal-design-independent-assessment.md#L61-L107))

This was a source-only recheck of every correction hunk and the affected
fixture, predicate, current-state enumeration, oracle, canonical vector,
negative case, and lifecycle seam. I followed the scoped composition into
`journal-publication-design.md`, `journal-restart-design.md`,
`journal-compatibility-design.md`, and `tooling/hindsight/README.md`. I did not
inspect runtime state, use Hindsight code or services, run a database or broad
test suite, or assess unrelated design surfaces.

## Original finding disposition

The original base is reproducibly inconsistent. Its fixture grammar derives
the operation-grant selector from a plan reference and includes
`subject_revision` in the evidence-tier-result selector, while its protected
operation lifecycle keys the grant by `grant_id` and its evidence read interface
selects one result by claim and tier. Subject replacement is defined to update
the current result rather than choose a revision-keyed result.
([old key grammars](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-acceptance-evidence.md#L3333-L3378),
[old operation lifecycle](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-acceptance-evidence.md#L2723-L2765),
[old evidence interface and replacement rules](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-acceptance-evidence.md#L4921-L4951),
[old current-result recomputation](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-acceptance-evidence.md#L5635-L5652))

The corrected base types contain only `grant_id` or `(claim_id, tier)` plus
their structural tags. The derived slot types use those bases, and present
values must match the same business fields. Registration, evaluator
replacement, and reads now state the same claim/tier key; `subject_revision`
participates in immutable result identity but not current-pointer identity.
([typed value equality](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L2128-L2138),
[key and slot definitions](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L3327-L3495),
[evidence interface](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L4932-L4965),
[subject ownership](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L5136-L5146),
[result and pointer identities](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L5632-L5684))

The corrected vectors close the former evidence gap rather than merely renaming
types. They display the complete LF-terminated key bytes and digests, require
one-field perturbations, reject the former plan-shaped and revision-bearing
keys, and require subject replacement to retain one key and one current slot.
The complete-state predicate separately classifies those old shapes and a
parallel old/new revision pair as extra state, while setup, protected preflight,
and `OR-ID` bind the exact fixture body.
([vectors and negatives](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L6536-L6567),
[fault-matrix rejection](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L6932-L6948),
[complete-state and oracle binding](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L6962-L7008))

The relevant static path is therefore closed and internally consistent:
fixture key body to typed key grammar and LF-inclusive digest, then to exact
setup/preflight state equality and independent oracle evidence. This is a
fixture and lifecycle consistency result, not a runtime exploit or attacker
claim.

## Affected-contract composition

- Publication resolves and locks the exact current grant and plan-authority
  slots and the exact current evidence partitions, while delegating their keyed
  interfaces to the acceptance contract. It does not define a competing grant
  or result selector.
  ([publication authority](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-publication-design.md#L371-L410),
  [stage predicates](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-publication-design.md#L620-L673))
- Restart defines continuity using those current operation and evidence slots.
  Its matrix preserves fixed expiry, exact replay, nonrenewing limits, and a new
  approval after authority or lineage loss; no restart row introduces a
  revision-keyed result or plan-keyed grant.
  ([restart matrix](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-restart-design.md#L480-L527))
- Compatibility keeps historical and successor formats disjoint. Its only
  legacy bridge creates a separately approved successor rollback and then uses
  the ordinary successor authority chain, including the exact current
  selector checks.
  ([compatibility invariants](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L18-L86),
  [new rollback authority](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L3429-L3515))
- The top-level Hindsight record still classifies this design as nonruntime and
  the fixture as `authority=NONE`; fixture bytes cannot become live authority.
  ([repository-facing boundary](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/README.md#L27-L47),
  [fixture/live separation](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/README.md#L209-L215))

The correction leaves the selected architecture intact: target PostgreSQL owns
the five-stage chain, and only a post-`P` `VALID R` decides whether the actual
durable publication occurred before the shared expiry. Apply and rollback keep
separate authority and effects, historical formats remain immutable, and
plan-bound retry, reconciliation, budget, and cohort limits remain bounded.
Design evidence remains distinct from implementation, qualification,
deployment, and live-operation authority.
([publication decision](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-publication-design.md#L22-L61),
[separate rollback](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-restart-design.md#L834-L878),
[historical preservation](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L18-L50),
[tier and authority separation](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L16-L40))

## Checks run and evidence limits

I verified the clean branch and full source SHA before assessment, confirmed no
Git operation was in progress, inspected the complete correction diff, and
confirmed its changed path set contains only the acceptance specification. I
read the original report from its immutable commit and reproduced the original
mismatch directly from the base source definitions above.

A separate Python standard-library check independently serialized the two
corrected ASCII key objects with compact sorted JSON plus one LF and recomputed
their SHA-256 digests. Both matched the documented vectors. The same check
compared the former plan-shaped grant key with the grant-ID key and obtained one
missing plus one extra slot, confirmed that two former revision-bearing tier
keys differ, updated a single `(claim_id, tier)` map entry to a `STALE` value
while retaining exactly one slot, and challenged one-field and former-shape
alternatives. Every assertion passed.

Those checks validate the written contract and the displayed ASCII vectors.
They do not prove a future serializer's full Unicode ordering, a PostgreSQL
schema or transition, executable setup/preflight/oracle behavior, physical
durability, deployment state, or live authority. The acceptance record assigns
those to later evidence tiers and says the source contract is not itself an
acceptance result.
([canonical-byte limits](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L74-L102),
[current evidence limits](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L7873-L7914))

I found no new inconsistency in the bounded affected surface. This report
supplies source-level evidence that the original blocking finding is resolved
for the later human design-acceptance decision; it does not make that decision.
