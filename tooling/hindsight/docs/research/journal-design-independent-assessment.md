# Independent assessment of the Hindsight journal design

**Assessment: not yet supported for implementation planning.** The integrated
publication, restart, and compatibility design composes for fixed expiry and
distinct apply and rollback approval under its declared local PostgreSQL WAL
durability assumptions. In particular, an earlier `J` or `P` clock reading is
not the deciding proof: only a causally later `VALID R`, sampled after durable
`P`, can establish that the exact authenticated `J` reached its durable
publication boundary before expiry. The evidence contract, however, contains a
design-blocking contradiction in the closed authority-gate fixture grammar: its
keys for the operation-grant and evidence-tier-result current slots do not match
the normative protected lifecycles they are supposed to exercise.
[Accept the journal design for implementation planning](https://github.com/nisavid/agents/issues/78)
should remain open until that source-level contradiction is resolved and
independently rechecked.

## Assessed revision, question, and scope

I assessed the integrated design at
[`e34334e9fccec9e9c19b30e5d523b5c67720ba60`](https://github.com/nisavid/agents/commit/e34334e9fccec9e9c19b30e5d523b5c67720ba60),
against the original repair-source baseline
[`7b165b3ee97e7e4bbddcb8eafe089f7f1237e0ab`](https://github.com/nisavid/agents/commit/7b165b3ee97e7e4bbddcb8eafe089f7f1237e0ab).
The question was whether the proposed design satisfies the fixed-expiry and
separate-approval contract under its stated durability assumptions, and whether
the agreed evidence is sufficient for Ivan to accept it for implementation
planning. [Define the evidence required to accept the journal redesign](https://github.com/nisavid/agents/issues/76)
was closed when I performed this assessment; final acceptance remains the
separate, open [design-acceptance decision](https://github.com/nisavid/agents/issues/78).

This was an independent source-only review. I did not adopt an earlier verdict,
inspect private recovery material or live Hindsight state, run a candidate,
provision a service, or assess a deployment. I read the five integrated design
records completely from immutable Git blobs and followed only their relevant
source-contract, research, prototype, and test seams. This note does not accept
the design, authorize implementation, or supply any live operation authority.

## Supported claims

| Obligation | Independent assessment | Owning basis |
| --- | --- | --- |
| Fixed expiry for apply | **Supported under the declared assumptions, for an aggregate admitted by `VALID R`.** `J` contains the final authenticated bytes. `P` is a separately committed, causally later WAL record. `R` samples the protected conservative upper bound only after acknowledged or exactly recovered `P`; therefore `durable_completion(J) <= durable_completion(P) <= U < expiry`. The `J` and `P` pre-stage samples are explicitly nonauthorizing start decisions, not publication-time proof. A `J` that commits after expiry can remain queryable evidence, but it cannot acquire a new `P` or `R` after expiry and cannot admit `M`. | [Publication decision and inequality](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-publication-design.md#L22-L61), [pre-stage limit](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-publication-design.md#L654-L673), [`J`, `P`, and `R` ordering](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-publication-design.md#L675-L817), and [restart matrix](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-restart-design.md#L480-L505). |
| Nonrenewing apply authority | **Supported as a design contract.** Grant, plan, approval, authorization receipt, `J`, and `R` share one deadline. Their exact typed bodies and action binding must agree; changed plan or epoch creates a new separately approved aggregate. Revocation remains terminal, and retry, reconciliation, and budget ceilings are plan-bound rather than inferred from process state. | [Apply aggregate and authority chain](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-publication-design.md#L332-L410) and [closed limit schemas](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-acceptance-evidence.md#L1337-L1366). |
| Separate rollback approval | **Supported separately from apply.** Rollback has its own action-bound grant, plan, approval, authorization receipt, aggregate, `J/P/R/M/V`, and stable key. `SUCCESSOR_APPLY` requires the exact predecessor apply `M` and matching `V`; `LEGACY_COMPLETE_APPLY` requires the frozen, manifest-selected legacy chain at successor genesis. Neither a legacy approval nor a successor apply approval can authorize the rollback. | [Rollback publication contract](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-publication-design.md#L1001-L1025), [rollback restart and effect contract](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-restart-design.md#L834-L878), and [legacy bridge authority](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-compatibility-design.md#L3429-L3515). |
| Historical formats and exact replay | **Supported as an explicit compatibility boundary.** Historical and successor protocols are disjoint; historical bytes keep their original schema, canonicalization, timestamp meaning, and frozen readers. Legacy outputs remain `authority=NONE`, and the one rollback bridge creates no synthetic predecessor `M` or `V`. The successor vector obligations also retain the historical family-specific representations rather than migrating them to the successor serializer. | [Compatibility decision](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-compatibility-design.md#L18-L50), [fixed compatibility invariants](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-compatibility-design.md#L67-L86), [successor and historical vector separation](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-acceptance-evidence.md#L6489-L6515), and [repair-source version contract](https://github.com/nisavid/agents/blob/7b165b3ee97e7e4bbddcb8eafe089f7f1237e0ab/tooling/hindsight/docs/exact-drain-stopped-run-reconciliation.md#L260-L273). |
| Retry, reconciliation, cohort, and preservation limits | **Supported as closed design inputs, not as observed behavior.** Restart first resolves exact committed work, charges unresolved work through plan-bound finite reservations, never widens a plan's limits, and requires a new plan and approval after exhaustion or lineage drift. Apply and rollback both preserve the locked out-of-cohort projection and advance generation once. | [Restart transition and accounting rules](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-restart-design.md#L497-L527), [rollback preservation](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-restart-design.md#L870-L878), and [historical attempt ceilings](https://github.com/nisavid/agents/blob/7b165b3ee97e7e4bbddcb8eafe089f7f1237e0ab/tooling/hindsight/docs/exact-drain-stopped-run-reconciliation.md#L215-L238). |
| Durability model and evidence calibration | **Supported as explicit and falsifiable, not proved.** The design limits its durability claim to local PostgreSQL WAL on an admitted host and storage stack that truthfully honors flushes. It requires `synchronous_commit=on`, `fsync=on`, `full_page_writes=on`, an allowed `wal_sync_method`, and exact-profile cold-recovery evidence. It excludes permanent primary-disk loss and does not require independence from target PostgreSQL. The evidence tiers correctly keep disposable PostgreSQL tests below physical qualification, deployment admission, and live authority. | [Declared failure model](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-publication-design.md#L68-L95), [tier separation](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-acceptance-evidence.md#L16-L40), and [qualification limits](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-acceptance-evidence.md#L7444-L7466). PostgreSQL documents that normal synchronous commit waits for local WAL flush, WAL recovery replays flushed records, and storage integrity still depends on the filesystem and devices: [asynchronous versus synchronous commit](https://www.postgresql.org/docs/current/wal-async-commit.html), [WAL introduction](https://www.postgresql.org/docs/current/wal-intro.html), [WAL settings](https://www.postgresql.org/docs/current/runtime-config-wal.html), and [storage reliability](https://www.postgresql.org/docs/current/wal-reliability.html). |
| Source acceptance versus live authority | **Supported.** The contract distinguishes design, implementation, release qualification, deployment admission, and live-operation authorization. It also says the repository currently lacks the successor implementation and profile evidence, so green historical tests cannot be promoted into a durability or deployment claim. | [Evidence tiers](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-acceptance-evidence.md#L16-L40), [existing seam limits](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-acceptance-evidence.md#L7789-L7814), and [current evidence disposition](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-acceptance-evidence.md#L7816-L7828). The cited historical PostgreSQL fixture actually starts the server with `-F`, so it is not physical-durability evidence: [fixture startup](https://github.com/nisavid/agents/blob/79b9071fd4a296df2064536cffe25d2cc8bc47d6/tooling/hindsight/tests/test_hindsight_memory_operation_recovery_postgres.py#L137-L154). |

The expiry result deserves a narrow qualification. The protected pre-stage
observation may precede `J` commit and the design allows that commit or its
acknowledgement to occur after expiry. That observation alone does not satisfy
the fixed contract. The design avoids turning it into replay authority because
`P` must be durably later than the exact `J`, `R` must sample after exact `P`, and
only `VALID R` can admit `M`. A late `J` is therefore an immutable historical
prefix, not an approved publication eligible for mutation. The acceptance
contract states that distinction directly: [`R` is the only durable deadline
decision](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-acceptance-evidence.md#L6643-L6688).

## Finding

### Important, design-blocking: authority-gate fixtures use nonnormative current-slot keys

The acceptance record says each fixture current-slot key is an exact standalone
preimage, and the conformance registrar must enumerate every reachable current
slot with no missing or extra state. Its slot table assigns `OPERATION_GRANT` an
`AuthorityGateOperationGrantSlotKey` and `EVIDENCE_TIER_RESULT` an
`AuthorityGateEvidenceTierResultSlotKey`
([exact-fixture requirement and table](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-acceptance-evidence.md#L2096-L2126)).
The closed grammar then defines:

- `AuthorityGateOperationGrantSlotKey` by extending
  `AuthorityGatePlanSlotKey`, whose key is an `OperationPlan` reference; and
- `AuthorityGateEvidenceTierResultSlotKey` by extending
  `AuthorityGateClaimTierSlotKey`, whose key includes `subject_revision`.

Those definitions are explicit in the
[base key grammars](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-acceptance-evidence.md#L3333-L3349)
and [derived slot keys](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-acceptance-evidence.md#L3367-L3386).

The normative protected lifecycles require different keys. The grant slot is
keyed by `grant_id`, while the operation-authority slot is keyed by plan
([operation-authority lifecycle](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-acceptance-evidence.md#L2723-L2765)).
The evidence owner exposes the current tier result by `(claim_id, tier)`, not by
subject, and subject replacement updates that current pointer to a `STALE`
result rather than creating a parallel subject-keyed current slot
([protected read interface](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-acceptance-evidence.md#L4921-L4951),
[subject and current-result ownership](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-acceptance-evidence.md#L5122-L5137),
and [current-result recomputation](https://github.com/nisavid/agents/blob/e34334e9fccec9e9c19b30e5d523b5c67720ba60/tooling/hindsight/docs/journal-acceptance-evidence.md#L5635-L5652)).

**Consequence.** An authority-gated conformance plan cannot both instantiate the
closed fixture grammar and represent the exact protected state reached by the
normative interfaces. It will either seed and query nonproduction keys or fail
the required complete-state comparison. That breaks the claimed exactness of
the `EV-PG`/`EV-ACL` conformance evidence for `J`, `P`, `R`, `M`, qualification,
and deployment gates. This is not later implementation work: the accepted
design must first contain one coherent key contract from normative interface to
fixture, registrar, oracle, and vector.

**Decision or evidence needed.** Settle the canonical production keys, then
revise the fixture key bodies, slot table, complete-state enumeration,
registration predicates, and one-field negative vectors to use those same keys.
The likely reading of the existing normative text is `grant_id` for the grant
slot and `(claim_id, tier)` for the current tier-result slot, but the design
owner must make that choice explicit. A source-only independent recheck can then
decide whether the evidence contract is coherent. I found no other supported
core invariant contradicted by the integrated documents, but this bounded
review is not an absolute proof of absence.

## Residual evidence obligations

| Gate | Status after this assessment | What remains |
| --- | --- | --- |
| Design blocking | **Open** | Resolve the two current-slot key contradictions above and recheck every fixture, predicate, and oracle reference that depends on them. Until then, the agreed evidence is not a faithful executable acceptance bar. |
| Later implementation evidence | **Deliberately open** | Materialize the versioned schemas and protected interfaces; implement independent canonical vectors and an executable reference model; exercise real disposable PostgreSQL constraints, isolation, roles, exact replay, failpoints, lost acknowledgements, lineage races, apply and rollback effects, frozen readers, and complete unaffected-state comparisons. These results may establish logical behavior only. |
| Exact-profile qualification | **Deliberately open** | Accept a finite qualification plan and run `EV-CLK`, `EV-PHY`, and `EV-CAP` for one exact release, macOS, PostgreSQL, APFS, filesystem, storage-device, cache, clock, controller, and boot-configuration profile. Cold reboot and power-interruption recovery must observe each acknowledged stage and atomic `M` outcome. The support claim remains limited to that profile and excludes permanent primary-disk loss. |
| Deployment admission | **Deliberately open** | Prove that one installed target matches a passing release/profile receipt, exact settings and schema identities, current clock/storage evidence, complete role and writer inventories, topology, endpoint, policy, and deployment evidence. Admission still does not authorize an operation. |
| Live operation | **Out of scope and unauthorized** | A distinct current grant, plan, action-specific approval, authorization receipt, epoch, and live gates would still be required. No source acceptance, test result, qualification receipt, or deployment attestation substitutes for them. |

Numeric run allocations, concrete release and profile identities, storage
qualification results, deployment health, and live operation data are properly
assigned to later gates rather than missing design facts. They should not be
requested as a substitute for resolving the source-level key contradiction.

## Methods, sources, and limits

I read these files completely as immutable blobs at the assessed revision:

- `tooling/hindsight/README.md`;
- `tooling/hindsight/docs/journal-publication-design.md`;
- `tooling/hindsight/docs/journal-restart-design.md`;
- `tooling/hindsight/docs/journal-compatibility-design.md`; and
- `tooling/hindsight/docs/journal-acceptance-evidence.md`.

I also read the complete repair-source
`tooling/hindsight/docs/exact-drain-stopped-run-reconciliation.md` at
`7b165b3ee97e7e4bbddcb8eafe089f7f1237e0ab`, the complete assessed
`tooling/hindsight/lib/hindsight_memory_control_plane/CONTEXT.md`, the complete
pinned durability and contract research notes, and the complete pinned backend
prototype note. I inspected the relevant classifier and PostgreSQL seams in
`tooling/hindsight/prototypes/PROTOTYPE-journal-publication-probes.py` at
`2704a823d7fce8e522a1176c69a97c4288ee5c0d`.
That prototype remains supporting model evidence and does not affect the
design-blocking finding.

I checked the cited historical PostgreSQL test seam at its immutable source pin
and used Context7 to retrieve current first-party PostgreSQL documentation for
the WAL, synchronous-commit, and storage premises. I did not run repository
tests: the successor implementation does not exist, and the assessment contract
correctly says existing focused tests cannot prove its physical durability or
deployment state. I performed no runtime imports, private-data reads, provider
requests, database access, or live-state mutations.

## Recommendation

Hold [Accept the journal design for implementation planning](https://github.com/nisavid/agents/issues/78)
at **not yet supported**. The selected `J -> P -> R -> M -> V`
architecture and action-separated authority model are suitable inputs to the
next discussion, but the discussion should first require one source-only
correction that makes the conformance fixture keys identical to the normative
protected keys. After that correction and independent recheck, implementation
planning can preserve every implementation, exact-profile qualification,
deployment, and live-authorization obligation as a later blocking gate.
