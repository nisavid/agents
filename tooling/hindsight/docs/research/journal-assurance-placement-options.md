# Journal assurance placement options

Option 1 is retained as the controlling implementation-planning baseline. The optional source-only Option 2 obligation/owner seam map is deferred and this comparison does not reopen it. Option 3 changes assurance and is not ready for implementation planning because the frozen sources define neither a replacement for full dormant-phase catalog classification nor an external-`PASS` invalidation and revocation contract.

This comparison is pinned to [revision `642c0d170fc41b0e98a33a71900ae567bdeb070d`](https://github.com/nisavid/agents/commit/642c0d170fc41b0e98a33a71900ae567bdeb070d) and packet manifest `1ae1818c15030a06c0486eb30af4688bcfd9aaabad23b92bc7c452bfb9ba742b`. It compares source obligations only. It authorizes no replacement, implementation, qualification, deployment, or live operation.

## Decision frame

The journal supports durable reconciliation of a stopped apply and a separately approved rollback. It is not the retain or provider worker. The target PostgreSQL database remains the sole successor publication and mutation store; no option adds a backend or a general-purpose control plane. The protected publication chain remains `J → P → R → M → V`, with the final authenticated `J`, causally later durable `P`, and protected post-proof upper bound `U < approval_expiry` governing continuation authority ([publication design, lines 20–61](https://github.com/nisavid/agents/blob/642c0d170fc41b0e98a33a71900ae567bdeb070d/tooling/hindsight/docs/journal-publication-design.md#L20-L61)).

Every candidate must also retain:

- distinct apply and rollback plans, approvals, journals, mutations, and verification; rollback restores only its exact selected preimage ([publication design, lines 1001–1025](https://github.com/nisavid/agents/blob/642c0d170fc41b0e98a33a71900ae567bdeb070d/tooling/hindsight/docs/journal-publication-design.md#L1001-L1025));
- charged, bounded attempts and exact ambiguity reconciliation before replacement work can start ([restart design, lines 384–463](https://github.com/nisavid/agents/blob/642c0d170fc41b0e98a33a71900ae567bdeb070d/tooling/hindsight/docs/journal-restart-design.md#L384-L463));
- exact replay, immutable prior evidence, current-pointer compare-and-set, and fail-closed stage admission; and
- immutable, nonauthorizing historical bytes plus the narrow, separately approved legacy-apply-to-successor-rollback bridge ([compatibility design, lines 18–86](https://github.com/nisavid/agents/blob/642c0d170fc41b0e98a33a71900ae567bdeb070d/tooling/hindsight/docs/journal-compatibility-design.md#L18-L86)).

An independently implemented deciding oracle remains mandatory. It must fix its expected projection before reading a stored verdict or current pointer and cannot share the protected registrar, evaluator, finalizer, admission path, production serializer, mutation path, or frozen reader that it judges ([acceptance evidence, lines 6758–6850](https://github.com/nisavid/agents/blob/642c0d170fc41b0e98a33a71900ae567bdeb070d/tooling/hindsight/docs/journal-acceptance-evidence.md#L6758-L6850), [lines 6916–6960](https://github.com/nisavid/agents/blob/642c0d170fc41b0e98a33a71900ae567bdeb070d/tooling/hindsight/docs/journal-acceptance-evidence.md#L6916-L6960)). Keeping that oracle does not make an externally supplied `PASS` equivalent to protected semantic recomputation.

The options analysis remains pinned to that frozen revision. For source
identity, the current correction separately preserves the accepted roster
`I_A` at revision `90108b516f5a1c460980a93670348f6e228124f2` and the
digest-bound issue-107 proposal roster `I_107`; only `I_A` is accepted source.
The correction supplies three controlling dependencies for any later
implementation. Recovery-stage closure is selected by protected outcome:
CURRENT J/P, R_VALID/R_LATE, and M use the atomic stage carrier, while J/P
equality or late remains a direct BS108 result under either invocation mode.
The deployment matrix retains 32 canonical final-graph predicates and 25
constructible pair witnesses; a distinct configured/resolved-path profile has
58 cases, while an equal-path profile has 57 cases plus an independently
verified NMR029 non-applicability proof. Those corrections preserve the valid-
profile domain, independent deciding verification, and the sibling inventory's
separate accepted-source and proposal closure equations. The accepted campaign-plan deployment basis owns
the governing matrix identity and exact policy/planned-run/row equality;
OR-DEP projection bytes omit a matrix or plan back-reference, so that closure
remains acyclic.

The third dependency is the issue-107 proposal for protected C17/M work
admission. PostgreSQL O16 owns
the read-only IA11 projection over the current active epoch, activation
binding, backend session, session witness, durable continuity-session
identity, completed fence binding, capability, and adapter incarnation. O10
may invoke it only inside FW02/FW03 with the exact plan and immutable work
binding and no caller/session override. The runner enters FW02/FW03 first.
Each function acquires every applicable rank-1 dependency and then every
applicable rank-2 dependency; neither rank may be skipped. Only its
candidate-AW04 branch invokes its guarded IA11 site. IA11 returns typed AW04
admission with only RX03's opaque M-admission ID and RX05's authenticated
adapter-incarnation ID, locks its projection at rank 3 before O10 reaches
rank-5 accounting or work state, and returns no equality verdict. The
proposal-only `FW03(reservation)` accepts no incarnation proof; the accepted
90108 interface retains its incarnation-proof parameter. FW02/FW03 acquire
rank 5 and own fresh-to-RW01 equality. Every ranked lock ends only at the outer
transaction's commit or rollback. A replacement that wins before IA11 rank-3
locking may determine the subsequently locked binding or deny before effect.
Once rank-3 locks are held, replacement waits until the outer transaction
ends; it cannot race through to denial before rank 5. After the driver returns
from COMMIT, historical acknowledgement requires a separate authoritative
protected read that validates the immutable RW01 admission carrier but
cannot reauthorize M after session loss;
new reservation or start requires a fresh IA11 result. `EV106-WORK` owns the
RX03/RX05 origin, atomic RW01 insertion, FW03 incarnation derivation,
positive, complement, race, fencing, replay, acknowledgement, readback, and
deterministic outcome vectors. `EV106-ACL` owns the single O10→IA11 grant, the
exact two guarded call sites and their branch cardinalities, the eleven
non-AW04 positive expansions whose complete identity/start bodies omit
`adapter_incarnation_id`, the two M/AW04 positive expansions whose complete
bodies require the IA11-derived field, typed identity-only return, no-override
checks, direct-relation denials, and every out-of-context and non-AW04 denial.

The protected outcome registry is one closed `OperationWorkProtectedResult`
union shared by FW01-FW03 and FW08. FW01/FW08 evaluate no current activation
or session continuity and use `DATABASE_CONFLICT` for immutable-chain
inconsistency. For FW02/FW03, the first matching exceptional predicate is
matrix/currentness/session/fresh-to-stored drift (`ADMISSION_DENIED`), the
same `(plan,request_id)` with changed exact request bytes and therefore a
different RequestKey (`REQUEST_CONFLICT`), a different `(plan,request_id)`
whose ReservationKey already exists (`WORK_ALREADY_RESERVED`), or remaining
immutable-chain inconsistency (`DATABASE_CONFLICT`), in that order. The
RequestKey includes the exact-body digest. FW01 and FW08 have a representable conflict
arm. Pre-start denial preserves `RESERVED`; denial after start preserves
`STARTED`.

These three dependencies do not change assurance placement, add a
store, or establish implementation or runtime behavior. The
`target_generation` source remains unassigned ([source-member inventory, lines 1560–1568](https://github.com/nisavid/agents/blob/642c0d170fc41b0e98a33a71900ae567bdeb070d/tooling/hindsight/docs/research/journal-postgresql-source-member-inventory.md#L1560-L1568)). Target activation and compatibility integration in issue 108 and implementation/evidence sequencing in issue 109 also remain undecided; the pinned schema plan assigns those choices to later tickets ([schema interfaces, lines 2955–2959](https://github.com/nisavid/agents/blob/642c0d170fc41b0e98a33a71900ae567bdeb070d/tooling/hindsight/docs/research/journal-postgresql-schema-interfaces.md#L2955-L2959)).

## Two schema-assurance sets

The current plan defines two different sets. A smaller option cannot narrow one by silently narrowing the other.

| Set | Current definition | Assurance supplied |
| --- | --- | --- |
| `ACTIVE_SURFACE` | The protected schema and database binding, every admitted entrypoint, every reachable internal routine, every object those routines read, lock, or write, all required owners, roles, ACLs, languages, extensions, and server primitives, and the complete transitive dependency closure. An unclassified reachable object or undeclared dynamic-SQL target rejects construction. | Any definition, ownership, ACL, dependency, provider, or version change inside the behaviorally reachable closure changes `ActiveSurfaceDigest` and invalidates current profile, receipt, and attestation equality ([schema interfaces, lines 2312–2336](https://github.com/nisavid/agents/blob/642c0d170fc41b0e98a33a71900ae567bdeb070d/tooling/hindsight/docs/research/journal-postgresql-schema-interfaces.md#L2312-L2336), [lines 2790–2796](https://github.com/nisavid/agents/blob/642c0d170fc41b0e98a33a71900ae567bdeb070d/tooling/hindsight/docs/research/journal-postgresql-schema-interfaces.md#L2790-L2796)). |
| `CATALOG` | The same grammar rooted across every object in the locked protected schema, including active and dormant objects, with associated owners, memberships, ACLs, privileges, and dependencies. Each reachable migration phase has an immutable `PhysicalSchemaIdentity`. | It classifies exact phase state and preparation residue even when the active digest is unchanged. Startup reconstructs the full catalog and rejects unrecorded drift, unclassifiable residue, illegal transitions, and incomplete metadata ([schema interfaces, lines 2798–2846](https://github.com/nisavid/agents/blob/642c0d170fc41b0e98a33a71900ae567bdeb070d/tooling/hindsight/docs/research/journal-postgresql-schema-interfaces.md#L2798-L2846)). |

The current evidence contract likewise defines behavior, not just placement. The protected evaluator derives complete run coverage, omission and failure dominance, invalidation effects, a unique contiguous supersession leaf, `STALE` and `PREREQUISITE_BLOCKED` states, and every affected current-result replacement. It commits result bodies and pointer changes atomically ([acceptance evidence, lines 5632–5720](https://github.com/nisavid/agents/blob/642c0d170fc41b0e98a33a71900ae567bdeb070d/tooling/hindsight/docs/journal-acceptance-evidence.md#L5632-L5720)). Qualification receipt finalization locks the complete design, implementation, and release partitions and every reachable prerequisite pointer, requires exact current-reference equality and `PASS`, and holds those locks through insertion ([acceptance evidence, lines 4503–4515](https://github.com/nisavid/agents/blob/642c0d170fc41b0e98a33a71900ae567bdeb070d/tooling/hindsight/docs/journal-acceptance-evidence.md#L4503-L4515), [lines 4578–4600](https://github.com/nisavid/agents/blob/642c0d170fc41b0e98a33a71900ae567bdeb070d/tooling/hindsight/docs/journal-acceptance-evidence.md#L4578-L4600)). These are accepted assurance guarantees.

## Option 1: exhaustive PostgreSQL baseline

Option 1 implements the current plan as written. Every startup re-extracts and compares the complete `ACTIVE_SURFACE` and phase `CATALOG`. Protected evidence registration recomputes semantic results and atomically replaces affected current pointers. Protected receipt and admission finalizers lock complete current partitions and prerequisites before creating consumable authority.

This option has the broadest specified PostgreSQL, migration, ACL, catalog-grammar, evaluator, and evidence-test surface. The sources establish that breadth, but they provide no measurements of development effort, startup time, operational load, failure rate, or maintenance cost. The baseline remains a source-level plan, not observed runtime behavior.

## Option 2: placement-only separation

Option 2 moves catalog extraction and campaign construction or execution into separately versioned deployment and qualification tools. It counts as placement-only only if every accepted guarantee and both schema-assurance sets remain intact.

For catalog work, a source-to-owner map must show how the tool reconstructs the complete current `ACTIVE_SURFACE` and `CATALOG` at every startup, compares exact bytes, lengths, and digests, and keeps route admission from racing a catalog, build, adapter, or generation change. A cached result, submitted digest, exit code, or prior successful run is insufficient. Unknown versions, missing output, unclassifiable objects, or unavailable authoritative verification must fail closed. PostgreSQL retains immutable manifests, migration state, route state, and current bindings. The map must identify the component that owns the current catalog snapshot, comparison, locks, commit, acknowledgement, and exact readback.

For qualification work, an external tool may expand campaigns, execute runs, and package registration inputs. The accepted protected registrar must still validate the complete accepted plan and ordered run expansion, recompute omission and failure dominance, apply invalidation and supersession rules, replace every affected current result, and let the protected finalizer determine receipt eligibility under complete-partition locks. The independent oracle remains a separate implementation.

The frozen sources contain no candidate seam demonstrating those properties. They also do not show whether this separation deletes duplicated construction code, duplicates it across the boundary, or merely relocates it. Option 2 therefore has no established risk or cost advantage. If the deferred study is reopened, that source map is the evidence needed to decide whether Option 2 is a coherent refactor.

## Option 3: complete active closure, narrower dormant coverage, and external `PASS`

Option 3 retains the complete `ACTIVE_SURFACE` transitive closure. Every behaviorally reachable routine, cast, collation, operator, provider, role, grant, and dependency remains covered, and drift inside that closure still blocks admission. It would narrow only dormant-phase coverage; the frozen sources do not yet define what replaces the full phase `CATALOG`.

That narrowing removes the baseline guarantee that every unrecorded change anywhere in the protected schema fails startup. It also removes the present meaning of an `EXACT` migration state as equality with an independently reconstructed full catalog. Preparation failure, classifiable residue, `UNCLASSIFIED_PREPARATION`, retry, abandonment, reversible cutover, rollback, and finalization can no longer claim the current classification guarantee for dormant objects outside the new scope. Such drift may survive startup when it cannot reach the active surface.

The frozen sources supply no replacement contract, so this part of Option 3 is not yet coherent for implementation. It needs one closed source requirement that, for every migration phase, defines:

- the exact dormant objects or migration deltas inside the reduced scope;
- the allowed before, after, partial, and residue classes after interruption;
- the immutable state and compare-and-set history for retry, rollback, abandonment, and finalization;
- the startup and route-admission rule for known and unclassifiable in-scope residue; and
- the dormant drift intentionally left outside detection.

Until that requirement exists, Option 3 cannot claim preserved migration classification, residue handling, upgrade safety, or post-restore phase identity. The retained publication contract permits only consistent full-database restoration and fences the prior publication epoch after restore; Option 3 would still need reduced-phase and residue revalidation rules for the restored state.

Moving qualification `PASS` outside protected SQL is a second, independent assurance revision. PostgreSQL may still authenticate an artifact, bind exact bytes and versions, compare expected pointers, enforce expiry, and install or revoke current references atomically. Those checks do not prove that the artifact covered every required run, applied failure dominance, selected the right supersession leaf, or carried the complete prerequisite partition.

| Event | Accepted protected guarantee | New dependency under external `PASS` |
| --- | --- | --- |
| Missing, invalid, or failing run | The evaluator derives `FAIL`; later passing records cannot hide the outcome. | The external finalizer becomes trusted to receive the complete run set and apply omission and failure dominance. Binding-only PostgreSQL admission cannot detect a false `PASS`. |
| Record invalidation | PostgreSQL removes the record from validity for its complete claim set, derives any resulting `FAIL`, and replaces all affected current pointers atomically. | Recording invalidation must synchronously fence the complete affected artifact set. An external finalizer must then derive replacement artifacts from the complete invalidation set. Delayed, omitted, or partial delivery otherwise leaves stale `PASS` authority. |
| Campaign supersession | PostgreSQL accepts only a contiguous, acyclic edge from the exact current claim-local result and recomputes solely from the replacement campaign. | The external finalizer becomes trusted not to skip, branch, merge old evidence, or cross claims or tiers. A PostgreSQL expected-pointer compare-and-set proves ordering, not replacement semantics. |
| Subject or prerequisite replacement | PostgreSQL atomically derives `STALE` or `PREREQUISITE_BLOCKED`, cascades dependent replacements, and makes the prior result noncurrent. | Each protected source change must synchronously fence old artifacts before admission. The external finalizer must later produce the complete dependent replacement set; otherwise currentness depends on an out-of-transaction report. |
| Current-result replacement | The protected evaluator constructs the result bytes and replaces every affected pointer with the result body in one transaction. | The design must assign complete affected-set derivation. If the external principal supplies it, that set joins the trusted input; if PostgreSQL derives it, this part of the protected evaluator remains. PostgreSQL can retain stable-key, exact-replay, expected-pointer, and atomic-install checks, but those checks do not establish semantic correctness. |
| Receipt finalization | The finalizer locks every required current partition and prerequisite pointer and verifies exact `PASS` equality. | PostgreSQL can lock the artifact references, but the external authority determines whether their partition and `PASS` semantics are complete. Omitting those references would weaken currentness further. |
| Finalizer defect or release revocation | Protected results remain coupled to protected invalidation, supersession, subject, prerequisite, policy, receipt, and stage gates. | The external finalizer release, authenticated principal, event feed, and revocation path join the trusted computing base. A discovered semantic defect needs an authenticated revocation that fences receipts, attestations, activation, and stages atomically; until report, revocation, or expiry, PostgreSQL cannot discover it by recomputation. Unavailable finalization must block new qualification and replacement. |

A coherent external-`PASS` design must define those event sources, complete affected-set rules, finalizer and oracle separation, artifact identity, stable keys, authentication, validity, synchronous fencing, atomic replacement, exact replay/readback, revocation, and restore behavior. No such contract exists in the frozen sources. Calling this a placement change would conceal weakened accepted guarantees.

## Guarantee and ownership comparison

| Concern | Option 1 | Option 2 | Option 3 |
| --- | --- | --- | --- |
| Target store and `J → P → R → M → V` mechanics | Target PostgreSQL | Same by definition | Same mechanics, but weaker evidence admission can weaken the authority they consume |
| Behaviorally reachable dependency coverage | Complete `ACTIVE_SURFACE` closure | Complete closure | Complete closure; no unnamed active dependency may be excluded |
| Dormant migration coverage | Full phase `CATALOG` and exact residue classification | Same coverage through a mapped external extraction seam | Narrower; replacement phase and residue contract unresolved |
| Semantic verdict owner | Protected evaluator and protected finalizer | Same owners; tools construct and orchestrate | External finalizer; PostgreSQL checks bindings and current references |
| Invalidation, supersession, and dependent currentness | Protected recomputation and atomic affected-pointer replacement | Same | External semantic recomputation plus mandatory synchronous PostgreSQL fencing |
| Complete-partition locking | Protected receipt, admission, activation, and stage gates | Same | Reference locks can remain, but completeness and `PASS` truth move outside |
| Issue-107 proposal C17/M protected work admission | FW02/FW03 acquire every applicable rank-1 then rank-2 dependency, call O16-owned IA11 for the typed AW04 admission identity or denial under rank-3 locks retained through the outer transaction, and then acquire rank 5. FW02 owns atomic RW01 insertion for a new request and fresh-to-RW01 equality on replay. Proposal-only FW03 owns fresh-to-RW01 equality after rank 5 and only then persists RW04/RW05/RS14 and returns START; accepted 90108 FW03 retains its proof parameter. All locks end at commit or rollback, and replacement attempted after rank 3 waits. | Same PostgreSQL origin, durable carrier, ACL, currentness, continuity, call-site, lock, equality, and race boundary | Must remain the same even if evidence `PASS` moves; an external verdict cannot supply session continuity, historical admission identity, or bypass IA11 |
| Additional trust | Current protected owners, adapter, source resolver, and independent oracle | Adds the mapped tool release, freshness, availability, and cross-boundary compatibility | Adds external finalizer, authenticated submission and revocation, event delivery, and reduced-phase classifier |
| Present disposition | Approved controlling planning baseline | Deferred optional source-map study | Changed assurance; two source contracts missing |

## Unmeasured cost comparison

| Option | Development | Operations | Debugging | Maintenance |
| --- | --- | --- | --- | --- |
| 1 | Implements the full specified catalog, migration, evaluator, ACL, and evidence surface | Performs complete startup comparison and protected qualification | Keeps authoritative state in one store but has many refusal predicates | Tracks full PostgreSQL catalog/build and evidence contracts |
| 2 | Retains the same obligations and adds tool/database interfaces; deletion versus duplication is unknown | Retains full startup and qualification gates and adds tool version and availability dependencies | May expose tool-local diagnostics, but failures cross a versioned boundary | Separates ownership; total contract breadth does not shrink, and compatibility work grows |
| 3 | Could remove full dormant-catalog and protected PostgreSQL evaluator code only by adding reduced-phase and external-result lifecycle contracts | Reduces one catalog scope but adds finalizer delivery, fencing, revocation, and renewal dependencies | Splits diagnosis across evidence, oracle, finalizer, artifact, database gate, and migration classifier | Reduces dormant catalog coupling while increasing external trust and compatibility obligations |

These are source-based directional comparisons, not measurements. The frozen survey found no component that owns the successor journal end to end and ran no tests, databases, providers, qualification, or deployment work ([source ownership, lines 3–14](https://github.com/nisavid/agents/blob/642c0d170fc41b0e98a33a71900ae567bdeb070d/tooling/hindsight/docs/research/journal-implementation-source-ownership.md#L3-L14), [lines 80–100](https://github.com/nisavid/agents/blob/642c0d170fc41b0e98a33a71900ae567bdeb070d/tooling/hindsight/docs/research/journal-implementation-source-ownership.md#L80-L100)).

## Operational consequences

- **Catalog drift:** All three options reject drift anywhere in the complete active closure. Options 1 and 2 also reject an unrecorded dormant change anywhere in the protected-schema catalog. Option 3 detects dormant drift only inside its future reduced phase scope.
- **Interrupted upgrade:** Options 1 and 2 classify exact phase state and residue through `CATALOG`. Option 3 has no defined retry, rollback, abandonment, or finalization behavior until its reduced phase contract identifies allowed partial states and residue.
- **Restore, clone, or PITR:** The retained publication contract supports only consistent full-database restoration; selective table, logical, or publication-only restoration remains unsupported. Adapter restart or loss of its in-memory capability, loss or replacement of the activation-bound database connection, PostgreSQL server restart, operating-system reboot, controller-host, PostgreSQL-host, endpoint identity, address, port, transport, target, or deployment-topology change, remote or managed endpoint substitution, clone, PITR, primary promotion, or uncertainty about the exact continuity chain fences the old epoch, so its unconsumed `R` cannot reach `M`. An ordinary caller or worker restart does not fence the epoch while the dedicated adapter, incarnation capability, activation-bound PostgreSQL session, session-local witness, admission generation, and publication epoch remain live and exact ([publication design, lines 1027–1042](https://github.com/nisavid/agents/blob/642c0d170fc41b0e98a33a71900ae567bdeb070d/tooling/hindsight/docs/journal-publication-design.md#L1027-L1042), [restart design, lines 711–738](https://github.com/nisavid/agents/blob/642c0d170fc41b0e98a33a71900ae567bdeb070d/tooling/hindsight/docs/journal-restart-design.md#L711-L738)). Options 1 and 2 preserve that baseline contract and revalidate both schema sets. Option 3 retains complete active-closure revalidation but still lacks the reduced-phase/residue and external-result restore/revalidation contracts needed to claim the same end-to-end behavior.
- **Qualification rerun:** Options 1 and 2 recompute from retained inputs; unchanged evidence cannot extend validity, and renewal requires a newly accepted plan and new evidence ([acceptance evidence, lines 4563–4576](https://github.com/nisavid/agents/blob/642c0d170fc41b0e98a33a71900ae567bdeb070d/tooling/hindsight/docs/journal-acceptance-evidence.md#L4563-L4576)). Option 3 also needs a new artifact, but its external finalizer becomes the semantic authority and its release and revocation state must remain current.
- **Lost acknowledgement:** Options 1 and 2 treat the protected return and driver COMMIT return as provisional, then require a separate authoritative protected read of the one committed result and pointer set or the prior state; retry never accepts a remembered verdict ([acceptance evidence, lines 6938–6954](https://github.com/nisavid/agents/blob/642c0d170fc41b0e98a33a71900ae567bdeb070d/tooling/hindsight/docs/journal-acceptance-evidence.md#L6938-L6954)). Option 3 must retain the same stable-key, atomic-install, exact-readback, and conflict behavior for artifacts, but the source does not yet define it.
- **Stale admission:** Options 1 and 2 hold complete current partitions and prerequisites through receipt, attestation, activation, and stage commits; a protected pointer change makes old authority noncurrent ([acceptance evidence, lines 4900–4929](https://github.com/nisavid/agents/blob/642c0d170fc41b0e98a33a71900ae567bdeb070d/tooling/hindsight/docs/journal-acceptance-evidence.md#L4900-L4929)). For M reservation/start, FW02/FW03 acquire every applicable rank-1 then rank-2 dependency, invoke IA11 at rank 3, and acquire rank 5 before performing their own fresh-to-RW01 comparison; every lock remains through the outer commit or rollback. Concurrent epoch, activation, fence, session, witness, continuity, capability, or incarnation change either precedes that locked view or waits. Drift or fresh-to-RW01 mismatch is `ADMISSION_DENIED`; loss cannot transfer authority, and historical FW01/FW08 carrier readback needs no IA11 result and cannot renew authority. Option 3 must retain this PostgreSQL boundary and synchronous mechanical fencing for every protected change, and it additionally depends on authenticated external reporting for semantic defects that PostgreSQL cannot recompute.

## Evidence needed before any architecture change

If the deferred Option 2 study is later commissioned, its source-only evidence is one obligation/owner seam map. For catalog, campaign, verdict, freshness, replay, ACL, protected C17/M admission, startup, migration, restore, and oracle obligations, each row must name:

1. the constructing component;
2. the authoritative validator;
3. the exact durable PostgreSQL state;
4. every currentness lock and race boundary;
5. failure, commit, acknowledgement, replay, and readback behavior; and
6. the independently implemented oracle.

The issue-107 proposal protected-admission row must keep PostgreSQL O16 as owner, name IA11's
exact rank-3 typed AW04 projection-or-denial result, two guarded FW02/FW03 call
sites and branch cardinalities, the eleven field-absent and two field-present
positive expansions, RX03/RX05 identity origin, O10-owned RW01 carrier,
mandatory rank-1-then-rank-2 dependencies, IA11 under retained rank-3 locks,
rank 5, FW02-owned insertion/replay equality, proposal-only FW03-owned equality before
RW04/RW05/RS14 persistence and START return, lock release only at commit or
rollback, and FW03 incarnation copy without a caller proof while accepted
90108 FW03 retains its proof parameter. It must
carry the dedicated-session continuity and loss behavior and map its positive,
complement, pre-rank-3 replacement or post-rank-3 wait, replay,
acknowledgement only after a separate authoritative protected read,
readback, closed outcome-union, precedence, and state-preservation cases to
`EV106-WORK` and `EV106-ACL`.

A row that ends at a tool assertion, cached result, or submitted digest proves that the proposal changed assurance. A row that duplicates complete validation on both sides identifies moved or duplicated work rather than demonstrated savings. The map must remain source-only and must not choose issue 108 integration or issue 109 sequencing.

Option 3 needs two different source contracts before it is a candidate: the reduced dormant-phase migration and residue contract, and the external-result invalidation, supersession, currentness, replacement, and revocation contract. Ivan would then have to decide whether the narrower dormant-drift coverage and external `PASS` authority are acceptable.

A disposable implementation or measurement pass is later work. It requires separate authorization naming its environment and mutation scope. Only then could it measure startup and qualification duration, manifest and artifact sizes, duplicated code, failure behavior, diagnosis paths, and operational load. None of those measurements belongs to the current correction.

Any later comparison must be refreshed against the published revision containing the separately owned contract corrections. Their accepted outcomes are inputs to later work; their bytes are not in this frozen baseline.

## Approved disposition

Option 1 remains the controlling PostgreSQL baseline. The optional Option 2
source-only seam study is deferred; no assurance-placement decision remains in
this correction increment.

This disposition does not adopt Option 2, authorize implementation or
measurement, change PostgreSQL, resolve issue 108 or 109, or accept Option 3's
reduced assurance.
