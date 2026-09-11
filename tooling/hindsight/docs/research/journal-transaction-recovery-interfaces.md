# Journal transaction and recovery interfaces

This record fixes the source interface for stopped-run journal reconciliation and rollback. The journal records, resolves, and, when the uniquely safe prefix permits it, advances `J -> P -> R -> M -> V`. It does not execute provider work, invoke the upstream retain operation, or introduce a second `RESERVED -> IN_FLIGHT -> PUBLISHED -> ACKNOWLEDGED` lifecycle.

The contract is specification-only. Its accepted-source inventory is anchored
only to revision
[`90108b516f5a1c460980a93670348f6e228124f2`](https://github.com/nisavid/agents/commit/90108b516f5a1c460980a93670348f6e228124f2).
The IA11/admission carrier, no-proof FW03 form, conditional body members,
protected-result partition, acknowledgement read, and retained-lock race rules
are issue-107 proposal members identified separately by the immutable
`ISSUE107_PROPOSAL_SOURCE_ARTIFACT_SHA256` in
[`journal-postgresql-schema-interfaces.md`](journal-postgresql-schema-interfaces.md);
they do not receive the accepted revision. Canonical bodies, stage semantics,
recovery bodies, and protected SQL records remain owned by
[`../journal-acceptance-evidence.md`](../journal-acceptance-evidence.md),
[`../journal-publication-design.md`](../journal-publication-design.md),
[`../journal-restart-design.md`](../journal-restart-design.md), and the schema
record. This record owns transaction acquisition, lock order,
acknowledgement, exact readback, and restart decisions for `FW01`–`FW08`,
`FJ01`–`FJ03`, `FM01`, and `FV01`.

Issue 108 still owns the exact target-row, writer, activation, and `target_generation` mapping. This record does not infer that mapping or claim that the target release already exposes it.

## Adapter and session ownership

The protected role split is fixed:

- `C16`, the publication adapter, owns `FW01`, `FW08`, and `FJ01`–`FJ03`; `FW02` and `FW03` admit only its J/P/R matrix rows below.
- `C17`, the continuity client, owns `FW01`, `FW08`, `FM01`, and `FX01`; `FW02` and `FW03` admit only its activation-session M rows.
- `C18`, the verification adapter, owns `FW01`, `FW08`, and `FV01`; `FW02` and `FW03` admit only its V rows.
- `C19`, the recovery adapter, owns `FW01` and `FW05`–`FW08`; `FW02` and `FW03` admit only its transaction-resolution, ambiguity-query, and reconciliation rows.
- `C22`, the status observer, owns the read-only `FS01`–`FS03` surface.

The recovery controller uses those same stage adapters rather than acquiring
stage authority through `C19`: recovered J, P, and R call C16's exact
`FJ01`–`FJ03` methods; recovered M calls C17's `FM01` on the
activation-bound session; and recovered verification calls C18's `FV01`.
C19 remains limited to `FW01`, the C19 rows admitted by `FW02`/`FW03`,
`FW05`–`FW08`, and `FX02`.
No generic recovery dispatcher or additional callable exists.

O10 owns FW02/FW03 work state but does not own activation or continuity state.
Under the issue-107 proposal, candidate C17/M admission invokes the read-only O16-owned
`IA11 project_current_m_work_admission(plan,work_identity_binding)` from
inside FW02 or FW03. O10 has no direct activation/continuity relation access,
IA11 is its only O16 callable, and IA11 returns only typed AW04 admission with
the opaque RX03 M-admission ID and authenticated RX05 adapter-incarnation ID,
or `ADMISSION_DENIED`. RW01 is O10's immutable durable carrier for that result.

`C16`, `C18`, and `C19` acquire a qualified connection to the exact admitted primary database for each protected transaction and release it only after the transaction has a definitive local outcome. Pool identity is never continuity evidence.

`C17` acquires one dedicated connection before `FX01`. The transaction that commits `FX01` and every later `FM01` transaction for that active epoch use that same PostgreSQL backend session and its session-local capability witness. `C17` does not return that connection to a pool, replace it, reconnect it, or transfer it to another adapter incarnation while the epoch is active. The `FW02` and `FW03` calls that prepare an `M` attempt also use that session. `FW01` and post-ambiguity `FW08` may use a separately qualified read-only connection because neither grants continuity or mutation authority.

Connection loss, pool reset, backend replacement, adapter restart, PostgreSQL restart, primary promotion, host reboot, boot-identity change, database clone, or uncertainty about any of them invalidates the old session witness. The adapter fences the old epoch and emits only the accepted nonauthorizing recovery evidence. A new connection cannot inherit the old witness or authorize another `M`.

## Protected transaction runner

Every mutating adapter method executes this sequence:

1. Resolve the exact admitted database, route generation, target surface, adapter generation, and caller role before acquisition.
2. Acquire the method's connection as specified above and start one explicit PostgreSQL transaction. `FM01` uses `SERIALIZABLE`. `FW02`, `FW03`, `FW05`–`FW07`, `FJ01`–`FJ03`, and `FV01` use `READ COMMITTED`. The adapter executes `SET LOCAL synchronous_commit = on` before any protected call.
3. Invoke exactly one adapter-facing protected entrypoint. The adapter does not acquire protected row locks or call IA11. The caller receives no connection and cannot insert SQL before, between, or after protected calls.
4. The entrypoint acquires every required row lock in the global order below, rederives all database-owned operands, and rechecks route, schema, authority, the FW02/FW03 caller-to-work matrix when applicable, currentness, deadline, continuity, predecessor, and exact-body equality. After the runner has entered FW02 or FW03, that entrypoint takes every applicable rank-1 and rank-2 dependency and then invokes its guarded IA11 site once for candidate AW04. IA11 acquires the O16-owned projection at rank 3, and the entrypoint reaches rank 5 only after IA11 succeeds. For FW02/FW03, an admission/currentness failure returns `ADMISSION_DENIED`, invokes no request, refusal, reservation, accounting, start, or stage effect, and rolls the transaction back. An admitted FW02 call may still take its separately specified request-keyed refusal branch. The entrypoint may invoke only the owner-internal routine allowed by the callable registry, including `FW04` for the exact stage result. Nested routines share the outer transaction and cannot commit.
5. Immediately after the protected call, verify `current_setting('synchronous_commit') = 'on'` in the same transaction. Commit explicitly and wait for the driver's commit outcome. The protected return and a successful driver COMMIT return are provisional for acknowledgement. Release the completed transaction, then perform a separate authoritative-primary protected read of the exact durable receipt or committed-result mapping. Acknowledge only when that read matches the complete expected chain. All entrypoint and nested-routine locks remain held until the commit or rollback and end with that transaction.

A local failure before `BEGIN` has no database effect. A confirmed rollback before a commit request is conclusive noncommit for that transaction. Once a commit request may have reached PostgreSQL, a connection or acknowledgement error is `AMBIGUOUS` until exact protected readback proves a committed result or separately accepted evidence proves conclusive noncommit. The adapter never turns a provisional return, timeout, client cancellation, or absent first read into success or noncommit.

`FW01`, `FW08`, and `FS01`–`FS03` use `REPEATABLE READ, READ ONLY` and one protected call. They set no current slot, charge no budget, perform no automatic resume, and need no durability acknowledgement. Their complete response is derived from one snapshot.

## Global lock order

A mutating entrypoint acquires only the rows it needs, but every entrypoint uses the same order. Locks are retained through commit or rollback. After acquiring a row from one rank, the transaction cannot acquire a row from an earlier rank.

Within a rank, rows are ordered by relation ID and then by the canonical UTF-8 bytes of the complete primary key. A transaction that touches an original and resolver work slot sorts the two complete `WorkSlotKey` values; semantic labels such as “original” and “resolver” do not affect order. Mutable current or state rows use `FOR UPDATE`. Immutable referenced rows use `FOR KEY SHARE`. Target rows selected for `M` use `FOR UPDATE`.

| Rank | Locked family |
| --- | --- |
| 1 | `RM02`, the selected `RM01` and `RM05` route records, the admitted database and target-surface binding, and every current schema or adapter-generation selector used by the call |
| 2 | Current operation authority, approval, plan, policy, attestation, evidence-tier, role-grant, writer-inventory, clock-envelope, and deadline rows, including the relevant `RA*`, `RE*`, and `RS*` anchors |
| 3 | Publication, activation, aggregate, predecessor, terminal, and continuity rows, including relevant `RP*`, `RX*`, `RS01`–`RS03`, `RS08`, `RS09`, `RS18`, and lineage heads |
| 4 | Protected preimage, ciphertext, conversion, target-image, target-generation, and exact target rows, including relevant `RB*`, `RS20`, and issue-108-selected target rows |
| 5 | Accounting and work rows, including `RS10`, `RW01`–`RW15`, and `RS13`–`RS15` |

The per-entrypoint lock footprint is:

| Entrypoint | Ordered ranks and required mutable rows |
| --- | --- |
| `FW02` | The runner enters FW02; FW02 acquires 1, 2, invokes IA11 for candidate AW04 at 3, then acquires 5. IA11 locks RS01, its RX01/RX02=`ACTIVE`/RX03/RX05 chain, RS08, and the selector's completed RC28-or-RC29 continuity binding, requiring `RX03.current_handoff_ref=RS08`; only then does FW02 lock current accounting and the request, reservation, and production work-slot keys before replay, charging, or writing a refusal |
| `FW03` | The runner enters FW03; FW03 acquires every applicable rank-1 and rank-2 dependency, invokes IA11 for candidate AW04 under rank-3 locks retained through the outer transaction, then acquires 5. Only then does FW03 lock and compare RW01 with the fresh projection, validate the exact reservation, accounting row, and production work slot, and persist RW04/RW05/RS14 before returning START or an exact repeat |
| `FJ01` | 1, 2, 3, 4, 5; lock authority/admission/current lineage, aggregate and J slot, candidate/preimage/ciphertext bindings, then the started work slot |
| `FJ02` | 1, 2, 3, 5; lock authority/admission/current lineage, exact aggregate/J and P slot, then the started work slot |
| `FJ03` | 1, 2, 3, 5; lock the nonrenewing envelope and authority/admission/current lineage, exact aggregate/P and R slot, then the started work slot |
| `FM01` | 1, 2, 3, 4, 5; lock the continuity and exact valid-R chain, lineage, protected material, issue-108-selected target rows and generation, then the started work slot |
| `FV01` | 1, 2, 3, 4, 5; lock current admission, exact M and terminal slot, target image/generation/lineage rows needed for comparison, then the started work slot |
| `FW05` | 1, 3, 5; lock the exact committed original chain, then the sorted original and resolver slots |
| `FW06` | 1, 3, 5; lock the exact protected transaction subject, then the sorted original and query slots |
| `FW07` | 1, 3, 5; lock the exact subject and absence proof, then the sorted original and resolver slots before closing both |

`FW04` acquires no earlier-rank lock. Its outer stage entrypoint has already
acquired ranks 1–4; `FW04` locks and closes rank 5 as the final database
action. It derives the close from invocation plus the protected result:
`RECOVERY_STAGE_CLOSE` is `RECOVERY/ADVANCE_STAGE` with CURRENT J/P,
R_VALID/R_LATE, or M_CREATED; `DIRECT_CLOSE` is every other admitted result,
including J/P equality or late under either invocation mode and every V. On a
forward stage or direct verification branch, FW04 maps the staged typed result
directly. On `RECOVERY_STAGE_CLOSE`, the outer stage-specific entrypoint stages
the exact stage, then FW04 derives BS125,
writes RW10's body and typed reservation, transaction-identity, result-body,
and branch-applicable reconciliation-subject projections, writes BS099/RW06
with `result_kind=RECOVERY_OBSERVATION` and `result` naming only BS125, and
closes RS15. The stage, BS125/RW10, BS099/RW06, and RS15 are one transaction;
a direct recovered-stage-to-RW06 edge is forbidden. A recovered late J/P
instead commits only RP03/BS108, direct BS099/RW06, and RS15 in the FJ01/FJ02
outer transaction; it creates no J/P stage, BS125/RW10, authority, refund,
replacement entitlement, deadline renewal, or prefix change. A missing earlier-rank
operand rolls back the whole group and restarts from rank 1 rather than
acquiring out of order.

The external-effect temporal locks of `FF01`–`FF03` are unchanged. Each accepted fence step retains its policy, attestation, clock, ACL, writer, and step-authority locks through the bounded external effect and exact durable outcome. This transaction record does not replace that guarantee with an intent row or release those locks early.

## Work reservation, start, and result

One logical work attempt uses the exact accepted chain:

1. `FW01` derives `RequestKey`, `ReservationKey`, production `WorkSlotKey`, and complete `WorkIdentityBinding` from the exact `BS094` request. A byte-identical committed result returns `COMMITTED_REPLAY` with the immutable RW01 admission carrier and without a write or charge. FW01 never invokes IA11. Anything unresolved proceeds with the same request bytes.
2. The runner enters `FW02` first. FW02 derives and checks the exact caller-to-work matrix row and takes ranks 1 and 2. For candidate AW04, its one guarded IA11 call then derives the current epoch, activation, backend, witness, continuity-session, capability, opaque M-admission ID, and adapter-incarnation equality. IA11 accepts no override, returns only the typed AW04 identity, and retains its rank-3 locks through the outer commit or rollback. A denied tuple writes nothing, including no request or refusal. A new admitted tuple commits RW01 with the immutable admission carrier and either one request-keyed pre-reservation refusal or one reservation in the same transaction. The refusal is nonauthorizing, consumes no observation, and changes no accounting. A reservation atomically consumes the checked-next ordinal and complete charge. Exact replay reacquires IA11 once for AW04, requires fresh-to-stored carrier equality, and never charges again.
3. The runner enters the issue-107 proposal form `FW03(reservation)` first; that proposed form has no incarnation-proof parameter. The accepted 90108 source interface retains its incarnation-proof parameter and is not reidentified as this form. Proposed FW03 rederives the same row, takes every applicable rank-1 and rank-2 dependency, and invokes its one guarded IA11 site for candidate AW04 under rank-3 locks retained through the outer transaction. FW03 then takes rank 5, owns equality with RW01, and copies `adapter_incarnation_id` from that typed result into its server-authenticated M/AW04 `TransactionIdentity/v1` and `OperationWorkStart/v1`. Only after that equality does it atomically persist RW04/RW05/RS14 and return START. Every J/P/R/V/RECONCILIATION transaction identity and start omits the field and calls IA11 zero times. A denied tuple writes nothing and preserves current state: before first start the reservation remains `RESERVED`, while an already-started repeat remains `STARTED`. An admitted durable start commits the only `RESERVED -> STARTED` transition and binds one dispatch and one logical protected transaction subject before stage invocation. An exact AW04 already-started repeat reacquires IA11 once before returning the stored start. The reservation never returns to `RESERVED` and is never reused.
4. Exactly one of `FJ01`, `FJ02`, `FJ03`, `FM01`, `FV01`, `FW05`, `FW06`, or `FW07` consumes that started chain. A forward stage or direct verification entrypoint stages its exact typed result and invokes `FW04` in the same transaction. A stage-producing recovered J/P/R/M outcome uses `RECOVERY_STAGE_CLOSE`: the outer entrypoint stages its exact stage; FW04 derives BS125/RW10, writes the sole BS099/RW06 mapping only to BS125, and closes RS15 in that atomic group. A recovered J/P equality-or-late outcome uses `DIRECT_CLOSE` and commits BS108 plus direct BS099/RW06 and RS15 with no stage or BS125/RW10. A recovered V uses `RECOVERY/VERIFY_STAGE` and the direct verification result. `FW04` cannot commit independently.
5. After the driver returns from COMMIT, the adapter separately exact-reads the authoritative durable mapping through `FW01`, `FW08`, or the applicable protected stage/status read. It acknowledges only a complete match or returns an ambiguity handle containing the immutable request, reservation, start, transaction identity, work identity, and expected result selector. `FW08` exact-reads that chain by `RequestKey` or production `WorkSlotKey`; the driver COMMIT return alone is not acknowledgement.

All four work callables return the one closed noncanonical
`OperationWorkProtectedResult` union: `PREFLIGHT`, `REFUSAL`, `RESERVATION`,
`START`, `READBACK`, `ADMISSION_DENIED`, or `DATABASE_CONFLICT`. FW01 and FW08
use its `DATABASE_CONFLICT` arm for immutable-chain failure, so FW01 need not
manufacture a preflight body. FW01 and FW08 do not evaluate current activation
or session continuity. After PostgreSQL ACL admission to FW02 or FW03, the
first matching exceptional predicate wins:
matrix/currentness/session/fresh-to-RW01 drift returns effect-free
`ADMISSION_DENIED`; the same `(plan,request_id)` with
changed exact request bytes has a different RequestKey because that key
includes the exact-body digest and commits a `REFUSAL` carrying
`REQUEST_CONFLICT`; a different `(plan,request_id)` whose ReservationKey
already exists commits a `REFUSAL` carrying
`WORK_ALREADY_RESERVED`; and remaining immutable-chain inconsistency returns
effect-free `DATABASE_CONFLICT`. These predicates do not overlap after the
first match. Neither refusal changes observation, accounting, start, stage, or
result state. A pre-start denial preserves `RESERVED`; an already-started
repeat denial preserves `STARTED`.

`FJ01` and `FJ02` sample the accepted durable-publication bound inside the stage transaction. A current result commits `J` or `P`. Equality or a greater value is late. Under FORWARD or RECOVERY, a late result commits exactly one `BS108 PreStageExpiryObservation/v1` and closes the started slot with `BS099.result_kind=PRE_STAGE_EXPIRY_OBSERVATION`. The embedded `BS108.stage` distinguishes `J` from `P`. This result creates no aggregate, `J`, `P`, adoption, refund, renewed deadline, replacement entitlement, or authority, and leaves the durable prefix unchanged.

`FJ03` commits only `R_VALID` or `R_LATE` with the exact qualified sample. No caller-supplied time extends the nonrenewing bound. `FM01` requires the exact valid `R` and the activation-bound session; `FV01` requires the exact `M` and records one accepted verification outcome. Apply and rollback authority remain independent and plan-bound.

## Acknowledgement and exact readback

An acknowledged mutation response requires a separate authoritative protected
read after the driver returns from COMMIT. That read contains the exact
committed body references, their complete work binding, and the immutable RW01
`OperationWorkAdmission`; neither the protected function return nor the
driver's COMMIT return alone is acknowledgement. The adapter compares every
carrier field, transaction mode and stage, and result field, not only the
stable key or result kind. For AW04 it compares the stored opaque M-admission
and adapter-incarnation IDs. FW01/FW08 validate and return historical committed
state; they do not call IA11, evaluate current activation/session continuity,
or assert that AW04 remains current. Immutable-chain inconsistency in either
historical read is `DATABASE_CONFLICT`. Any new or repeated M reservation or
start must reacquire IA11.

After a lost acknowledgement:

- replay `FW01` with the original `BS094`;
- use `FW08(REQUEST)` to distinguish a request-keyed refusal from a linked reservation/result;
- use `FW08(WORK)` to validate the complete production work chain;
- invoke the stage's exact-read replay path only with the identical aggregate, predecessor, attempt, and request binding; and
- use `FW05`–`FW07` only through a separately reserved, started, and charged reconciliation chain.

A readback is conclusive only from the admitted authoritative primary under current route and database identity. `COMMITTED_REPLAY` requires field-identical admission carrier and byte-identical request, reservation, start, transaction identity, complete work identity, result mapping, and typed result. FW01 and FW08 dispatch first by BS099 result kind plus the exact typed body, then validate the selected close against the immutable identity. `RECOVERY_OBSERVATION` requires BS125/RW10 and the same-transaction stage. A J/P `PRE_STAGE_EXPIRY_OBSERVATION` requires matching BS108 stage/body and forbids a stage or BS125/RW10 even when the identity is `RECOVERY/ADVANCE_STAGE`. A different request for an existing reservation remains unresolved until admitted FW02 handles it. A missing, extra, or internally inconsistent immutable carrier returns `DATABASE_CONFLICT`. A missing row after an uncertain commit is not conclusive noncommit.

If an AW04 reservation/start commit is ambiguous, exact readback may establish
only the stored committed mapping and RW01 admission identity. That carrier
survives loss of RX05 and records the exact admission and incarnation proved
at commit; it does not claim they remain current. A changed or lost current
activation session therefore does not invalidate the historical fact, but it
returns `ADMISSION_DENIED` for any new FW02/FW03 M admission and cannot be
repaired by replay, acknowledgement, or read permission.

The adapter never automatically repeats `M` or any external effect. It does not verify an ambiguous `M`, because verification could bless an effect that is not bound to the accepted receipt, generation, and lineage transaction.

## Crash and replay decisions

| Last conclusive fact | Required decision |
| --- | --- |
| No `FW02` commit request | Retry `FW01` with identical bytes; no reservation or charge is inferred |
| `FW02` commit acknowledged or exact-read | Reuse that exact admission carrier and refusal or reservation; never allocate another ordinal for the same request or treat historical AW04 as current authority |
| `FW03` commit acknowledged or exact-read, no terminal result | Require the stored admission and transaction/start mapping. M/AW04 requires the identical stored `adapter_incarnation_id`; J/P/R/V/RECONCILIATION omits that field from both bodies. Keep the original slot `STARTED`; do not invoke the forward stage again or reserve replacement work. |
| Stage commit acknowledged or byte-identical exact-read | Return its exact `BS099` mapping after result-kind/body dispatch and immutable-identity validation. For `RECOVERY_OBSERVATION`, require BS099 to name BS125, RW10 to name the same-transaction stage through `result_body`, and every chain field to match. For J/P `PRE_STAGE_EXPIRY_OBSERVATION`, require the exact BS108 body and no stage or BS125/RW10. For other direct work, return the direct typed result. Never recreate a body. |
| Stage commit may have reached PostgreSQL, exact read remains absent | Report `STAGE_AMBIGUOUS` within the finite resolution bound; do not infer rollback, refund, or a safe replacement |
| Separately proved conclusive noncommit | `FW07` atomically closes the original and resolver slots; only later ordinary gates may admit a new charged attempt |
| Late `J` or `P` under FORWARD or RECOVERY | Return the terminal `PRE_STAGE_EXPIRY_OBSERVATION` mapping; leave the prefix unchanged and grant no authority, replacement entitlement, renewal, or refund |
| Recovered `R_LATE` | Recognize or commit the exact R_LATE stage plus BS125/RW10 and BS099→BS125, advance the prefix to `LATE`, then stop; do not create `M` |
| `M` committed and exact-read | Advance only to `MUTATED` and permit `V` under its own authority |
| `M` ambiguous at the resolution bound | Return `STAGE_AMBIGUOUS(M)`; do not retry `M` or run `V` |
| `V` unable | Preserve that immutable attempt; a later attempt requires a new charged verification identity |
| `V` mismatch or terminal failure | Preserve the sticky terminal result; no retry can convert it to match |
| Continuity witness lost | Fence the old epoch; fresh acquisition is not recovery of the old capability |
| Recovery transaction itself becomes ambiguous | Treat its `RECONCILIATION` transaction subject exactly like any other started slot and resolve it under the same finite limits |

All reservations, observations, stage bodies, receipts, terminal results, and recovery links remain immutable. Recovery does not delete or rewrite historical evidence.

## Uniquely safe prefix and refusal

The durable prefix is one of `ABSENT`, `JOURNALED`, `PROVEN`, `VALID`, `LATE`, `MUTATED`, or `VERIFIED`. Automatic stage reconciliation may advance exactly one adjacent J, P, R, or M edge only when the next stage and its complete atomic recovery-close group already exist or are committed from complete protected operands:

- `ABSENT -> JOURNALED` through exact `J_CREATED`;
- `JOURNALED -> PROVEN` through exact `P_CREATED`;
- `PROVEN -> VALID` through exact `R_VALID_CREATED`;
- `PROVEN -> LATE` through exact `R_LATE_CREATED`; and
- `VALID -> MUTATED` through exact `M_CREATED`.

Each listed edge uses the matching FJ01–FJ03 or FM01 outer carrier and commits
the stage, BS125/RW10, BS099/RW06 pointing only to BS125, and RS15
all-or-neither. `R_LATE_CREATED` is recognized or committed before the
reconciler reaches `LATE`; `LATE` is then terminal and M is not attempted.
Verification is not an `ADVANCE_STAGE` edge. At `MUTATED`, the controller
may call C18/FV01 with `RECOVERY/VERIFY_STAGE`; its accepted V outcome is the
direct BS099 typed result and creates no BS125/RW10.

The reconciler repeats the stage check only while the prefix remains contiguous
and every current authority, admission, lineage, target, session, work, and
result binding is exact. It stops before the first absent, ambiguous,
conflicting, noncurrent, or unauthorized edge, and after a recognized or
committed R_LATE edge.

Recovery reports or refuses without advancement when any of these holds:

- the predecessor is missing, duplicated, noncurrent, or bound to different bytes;
- an original or resolver work slot is still unresolved;
- a stage result exists without its exact `BS099` mapping or the mapping names another typed body;
- the deadline or finite reconciliation bound is reached;
- current route, schema, database identity, policy, attestation, evidence tier, authority, lineage, target, generation, or continuity differs;
- `M` outcome or activation-session continuity is ambiguous;
- a gap would be skipped, a result would be rewritten, or a charge/refund would be inferred; or
- the proposed action would execute provider work, invoke retain, resample a fixed publication bound, or replay an external effect.

`FS01`–`FS03` and `FW08` only report this state. Resume is a distinct, explicitly authorized controller action. It calls the same adapters and protected entrypoints and gains no authority from status output.

## Verification seams

Implementation qualification must eventually exercise, at minimum:

- each entrypoint's exact connection source, isolation level, lock sequence, protected-call count, durability check, commit, and acknowledgement, proving the runner enters FW02/FW03 before either function's nested IA11 call;
- every lock-order pair plus concurrent original/resolver key ordering;
- crash injection before `BEGIN`, after each lock rank, after `FW02`, after `FW03`, before server commit, after server commit but before acknowledgement, and during exact readback;
- identical replay, changed-request conflict, changed-work conflict, digest/body collision, and request-keyed refusal;
- forward and recovered `J` and `P` equality/after-expiry terminal closure through the same `BS108` body with distinct embedded stages, direct BS099/RW06 and RS15, no J/P stage or BS125/RW10, and no authority, refund, replacement entitlement, renewal, or prefix change;
- conclusive noncommit, recursive reconciliation ambiguity, and every refusal above;
- all-or-neither stage-producing J/P/R/M recovery closure across the stage, BS125/RW10,
  BS099/RW06→BS125, and RS15, including exact replay and lost-acknowledgement
  readback for FJ02 `JOURNALED -> PROVEN` and recovered R_LATE;
- FW01/FW08 result-kind plus exact-body dispatch for the recovered J/P direct vectors, with conflict on an attached stage or BS125/RW10, a missing required recovery-stage carrier, or changed invocation mode, recovery mode, recovery request, transition, from/to prefix, BS125 body, or result body;
- independent FW02 and FW03 positive vectors for all 13 admitted caller/identity/invocation rows: the eleven non-AW04 rows construct complete J/P/R/V/RECONCILIATION transaction identities and starts with `adapter_incarnation_id` absent and zero IA11 calls; both C17/M rows require that field in both bodies and prove IA11's exact two-ID typed result and RX03/RX05 origin. Each candidate AW04 vector acquires every applicable rank-1 and rank-2 dependency, calls IA11 with its rank-3 locks retained through outer commit or rollback, and then acquires rank 5. FW02 owns atomic RW01 carrier insertion for a new request and fresh-to-RW01 equality on replay. The proposal-only FW03 form accepts no incarnation proof, owns fresh-to-RW01 equality after rank 5, copies the M/AW04-only incarnation, and only then persists RW04/RW05/RS14 and returns START; the accepted 90108 form retains its incarnation-proof parameter. The vectors also cover the complete caller, identity-kind, stage, invocation-mode, recovery-mode, recovery-request-presence, and continuity-session complement; every denial must precede RW01/RW02, RE05/RW03/RS10/RS13, and RW04/RW05/RS14 mutation;
- IA11 call-site evidence for exactly the FW02 and FW03 guarded sites: once per candidate-AW04 initial reservation, new refusal, exact reservation replay, exact refusal replay, repeated unresolved request, first start, acknowledgement-uncertain repeat, or already-started repeat; zero calls for every non-AW04, FW01/FW08, and adapter path;
- concurrent replacement of the active epoch, RX01/RX02/RX03 activation chain, completed RC28/RC29 binding, fence, backend, witness, continuity-session identity, capability, or adapter incarnation before IA11 and after IA11 holds rank-3 locks but before rank 5. A pre-lock replacement may become the binding IA11 subsequently locks or may produce admission denial. Once rank-3 locks are held, the replacement waits until the outer commit or rollback; rank 5 and any effect use the retained binding. No post-rank-3 vector expects replacement to race through and return `ADMISSION_DENIED` inside that transaction;
- default-deny ACL proof that O10 can execute IA11 only as a nested FW02/FW03 AW04 check, receives only typed admission, has no underlying O16 relation access or caller/session override, and that C19 cannot call FW04, FJ01–FJ03, FM01, or FV01 and has no stage-relation write path;
- direct recovered V through `RECOVERY/VERIFY_STAGE` with no BS125/RW10;
- no duplicate charge, reservation, stage, `M`, result, refund, or deadline renewal;
- activation-session loss, connection replacement, pool release/reset, restart, promotion, and fence behavior before FW02, between FW02 and FW03, during either IA11 call, and after acknowledgement; historical RW01 readback remains readable with the exact admission and, for AW04 only, incarnation identity, while non-AW04 transaction/start bodies retain the field's absence; readback never reauthorizes M;
- the closed `OperationWorkProtectedResult` union and ordered, non-overlapping effect-free `ADMISSION_DENIED`, durable `REQUEST_CONFLICT`, durable `WORK_ALREADY_RESERVED`, and effect-free `DATABASE_CONFLICT` vectors, with `ADMISSION_DENIED` currentness/session cases scoped to FW02/FW03, FW01/FW08 conflict representation for immutable-chain inconsistency, and RESERVED/STARTED preservation; and
- `SERIALIZABLE` `M` races over the issue-108-selected target, receipt, generation, and lineage rows.

The exact admission projection used throughout this interface is:

| Caller | Identity and stage | Invocation | Protected session |
| --- | --- | --- | --- |
| `C16` | `StageAttemptWorkIdentity(J|P|R)` | `FORWARD/NONE/NONE` or `RECOVERY/ADVANCE_STAGE/non-NONE` | C16 qualified transaction connection; no activation-session claim or M authority |
| `C17` | `StageAttemptWorkIdentity(M)` | `FORWARD/NONE/NONE` or `RECOVERY/ADVANCE_STAGE/non-NONE` | exact backend, session witness, durable `continuity_session_id`, and adapter incarnation selected by the active epoch |
| `C18` | `VerificationAttemptWorkIdentity`, derived V | `FORWARD/NONE/NONE` or `RECOVERY/VERIFY_STAGE/non-NONE` | C18 qualified evidence-only connection; no activation-session or mutation authority |
| `C19` | `TransactionResolutionWorkIdentity` | `RECOVERY/RESOLVE_TRANSACTION/non-NONE` | C19 qualified current connection; any `stage` field identifies only the subject |
| `C19` | `AmbiguityQueryWorkIdentity` | `RECOVERY/QUERY_AMBIGUITY/non-NONE` | C19 qualified current connection; any `stage` field identifies only the subject |
| `C19` | `ReconciliationWorkIdentity` | `RECOVERY/RECONCILE_SUBJECT/non-NONE` | C19 qualified current connection and exact reconciliation subject; no stage authority |

FW02 derives `work_class` only after one row matches. Every other caller,
identity, stage, invocation, recovery, request-presence, or session tuple fails
before a durable refusal, reservation, charge, or start mutation. FW03 repeats
the same check before consuming an admitted reservation. The C17/M row exists
only as IA11's O16-owned typed result under the rank-3 locks described above.
That result is
`{row_id=AW04,work_class=M,m_work_admission_identity={m_work_admission_id,adapter_incarnation_id}}`;
it exposes no other protected component. O10 stores it immutably in RW01 but
cannot reconstruct it. FW01/FW08 read access can inspect the carrier and a
cross-role chain for recovery without IA11, but it neither changes this matrix
nor grants FW02, FW03, FW04, or a stage callable.

The table expands to 13 positive paths. Its eleven non-AW04 expansions omit
`adapter_incarnation_id` from both complete bodies and never call IA11. Its two
AW04 expansions require the same IA11-derived value in the M transaction
identity and start. The complement treats a non-AW04 field as extra and an
AW04 missing, supplied, or changed field as denial before effect.

These are proposed contract and fault-injection vectors. This source record reports no executed test, PostgreSQL behavior, driver behavior, target-release compatibility, deployment qualification, or operational recovery.
