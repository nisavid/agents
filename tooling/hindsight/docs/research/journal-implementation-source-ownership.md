# Hindsight journal implementation source ownership

At pinned source revision
[`afbe95ca1def16b4bc653f255b3337d9dd0b6586`](https://github.com/nisavid/agents/commit/afbe95ca1def16b4bc653f255b3337d9dd0b6586),
the bounded survey below found no component that owns the accepted successor
journal end to end. Surveyed source provides canonical JSON, filesystem
transaction/replay recovery, digest-bound mutation, immutable acquisition, and
append-once primitives, but lacks successor typed contracts, protected
PostgreSQL stages/evidence, frozen-reader dispatch, and independent oracles.
The accepted architecture keeps authoritative
`J -> P -> R -> M -> V` state in the target PostgreSQL database and makes the
trusted adapter own each stage from `BEGIN` through the driver COMMIT return,
then report acknowledgement only after a separate authoritative protected read
of the exact durable receipt or committed-result mapping;
filesystem success cannot substitute for that boundary.
([accepted publication owner and transaction boundary](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-publication-design.md#L20-L61))

The accepted-source roster remains anchored only to revision
`90108b516f5a1c460980a93670348f6e228124f2`. IA11, the RW01 admission-carrier
rules, conditional transaction/start members, protected-result partition,
separate acknowledgement rule, retained-lock race rule, and no-proof FW03
shape are the digest-bound issue-107 proposal overlay. They do not inherit the
accepted revision; accepted 90108 FW03 retains its incarnation-proof
parameter.

## Source and test ownership map

Every test citation below is an inspected source seam; no test was run for this
research.

The survey covered every production `*.py` module under
`tooling/hindsight/lib/hindsight_memory_control_plane/` and the matching
`test_hindsight_memory_*.py` files. I searched durable-write and
recovery/replay/acknowledgement paths, then inspected each candidate. A current
owner is included only when it both persists pre-effect state or a
transaction/replay/acknowledgement record and rereads that record after
interruption or retry to choose restoration, reissue, or acknowledgement. The
included effect boundaries are harness configuration (`harness_persistence`),
installer files/services (`portable_install`), package pointers
(`integration_upgrades`), migration archives and admin/data-plane rollback
(`migration_adapter`), broker work/ledger acknowledgements (`broker`,
`ledger`), and inspection-call replay (`import_runner`). One-shot artifact or
snapshot publication, locators, and same-call cleanup fail the reread rule.
This static survey is not a closed repository inventory: shell entrypoints,
nonancestor source, source outside this package, and indirect persistence not
matched here remain an unverified planning dependency.

| Obligation | Implemented or reusable owner | Successor owner and test seam still required |
| --- | --- | --- |
| Canonical bodies, references, and exact identities | `canonical.py` strictly parses UTF-8 JSON, rejects duplicate keys and unsafe numbers, orders keys by UTF-16BE, and hashes its canonical bytes. Its bytes have **no LF**, so changing `digest()` globally would change existing identities. The cycle-free package root exposes only `canonical` and `model`; the inspected canonical test body contains assertions for rejection, number bounds, ordering, and duplicate keys. ([implementation](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/lib/hindsight_memory_control_plane/canonical.py#L84-L102), [encoder and digest](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/lib/hindsight_memory_control_plane/canonical.py#L155-L204), [tests](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/tests/test_hindsight_memory_controller.py#L630-L693)) | Add an executable successor body/reference interface that closes every type, recursively resolves typed references, and owns the required one-LF representation without silently reinterpreting existing digests. Its test owner needs independently fixed vectors, one-field perturbations, replay/conflict cases, and a separate serializer/oracle implementation. |
| Publication, restart, and preservation | `harness_persistence.py` has a locked prepared/committed filesystem journal; `broker.py` persists work, idempotency keys, and ledger acknowledgements for retry. Their inspected test bodies contain assertions/checks for phase selection, close retry/barriers, and same-key restart cases. ([local recovery](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/lib/hindsight_memory_control_plane/harness_persistence.py#L379-L452), [harness test](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/tests/test_hindsight_memory_harness_persistence.py#L176-L240), [broker transaction](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/lib/hindsight_memory_control_plane/broker.py#L1399-L1442), [broker tests](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/tests/test_hindsight_memory_broker.py#L3637-L3733), [replay tests](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/tests/test_hindsight_memory_broker.py#L4045-L4092)) `portable_install.py` captures launcher, manifest, and active-file preimages and writes its journal before release/file/service mutation; recovery rereads and validates it before restoring installer effects. Its inspected tests construct interruption and preimage cases and assert prior-version, cleared-transaction, launcher-byte, and release-set values. ([journal before mutation](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/lib/hindsight_memory_control_plane/portable_install.py#L3745-L3815), [journal read](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/lib/hindsight_memory_control_plane/portable_install.py#L3367-L3424), [restore](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/lib/hindsight_memory_control_plane/portable_install.py#L3559-L3643), [tests](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/tests/test_hindsight_memory_portable_install.py#L1131-L1189)) | These local filesystem/fake-adapter owners are narrower than protected PostgreSQL stages. New ownership must cover nonauthorizing status; serialized reconciliation; immutable reservation-keyed observations; atomic candidate-to-`J` adoption of protected preimage bindings/ciphertext; and immutable replacement links. Preimages and decryptability remain retained until matching rollback `M` and `V`, or a separately approved permanent-retirement action; no retirement interface exists yet, so retention is unconditional. Future test seams include PostgreSQL constraints/races, lost acknowledgements, observation replay/conflict, all-or-nothing adoption, replacement preservation, status nonmutation, and ACL separation; physical durability is a separate campaign. Whether portable installer recovery is reused behind a successor adapter or kept separate is a downstream choice. ([status/reconciliation/observations](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-restart-design.md#L210-L253), [preimage adoption/retirement](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-restart-design.md#L887-L937), [replacement](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-restart-design.md#L946-L959), [PostgreSQL evidence](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L7501-L7523)) |
| Apply, rollback, and target effects | The current `Adapter` protocol exposes snapshot, plan binding, action, verification, rollback bundle, restore, and activation disable. `reconcile.apply_plan()` records a started action before adapter mutation and restores/verifies prestate on failure. Its inspected `FakeAdapter` test bodies construct commit-then-error and restore-failure cases and contain assertions for rolled-back/operator-blocked results and disabled activation. ([protocol](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/lib/hindsight_memory_control_plane/adapters.py#L84-L133), [flow](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/lib/hindsight_memory_control_plane/reconcile.py#L720-L809), [tests](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/tests/test_hindsight_memory_adapters.py#L5931-L5955), [failed restore](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/tests/test_hindsight_memory_adapters.py#L6129-L6141)) `migration_adapter.py` adds fsynced archive recovery names and monotonic rollback receipts; retries read receipts to stop at indeterminate admin mutation or continue data-plane reconciliation. Its inspected tests contain assertions for archive recovery, retained `admin_started` state on a fresh adapter, and restart acceptance of only receipted bundles. ([archive recovery](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/lib/hindsight_memory_control_plane/migration_adapter.py#L1294-L1392), [archive publication](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/lib/hindsight_memory_control_plane/migration_adapter.py#L1684-L1735), [receipt I/O](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/lib/hindsight_memory_control_plane/migration_adapter.py#L2021-L2145), [restore phases](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/lib/hindsight_memory_control_plane/migration_adapter.py#L2330-L2421), [archive test](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/tests/test_hindsight_memory_adapters.py#L4882-L4907), [indeterminate test](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/tests/test_hindsight_memory_adapters.py#L4249-L4300), [restart test](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/tests/test_hindsight_memory_adapters.py#L4443-L4488)) | Preserve these as possible orchestration/recovery seams, but add successor interfaces with distinct apply/rollback authorities and a protected transaction binding target images, plan payload, generation, lineage, `M`, and receipt. Downstream must choose whether existing protocols/adapters are extended, wrapped, or separate; none expresses the accepted PostgreSQL contract. |
| Compatibility and historical evidence | `integration_upgrades.py` demonstrates immutable digest-bound candidates, compatibility reports, current/last-known-good pointers, and explicit interrupted-transaction recovery. Its inspected recovery test body injects an interruption, invokes pending recovery, and asserts the prior version, a cleared transaction marker, and interrupted-candidate quarantine. Those schemas concern harness package upgrades, not journal artifacts. The accepted frozen readers instead pin historical source revision `7b165b3...`, exact selectors, and `authority=NONE` outputs. ([upgrade pattern](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/lib/hindsight_memory_control_plane/integration_upgrades.py#L719-L816), [recovery test](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/tests/test_hindsight_memory_integration_upgrades.py#L544-L567), [frozen-reader contract](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L160-L235)) | Add a closed registry/dispatcher, immutable reader-execution bindings, failure/success outputs, fieldwise restore conversion, inventory/manifest/closure interfaces, and exhaustive synthetic plus controlled-real test partitions. Distinct accepted capability owners are also required: the trusted fence adapter performs external writer/service fencing; the admission role performs metadata-only fence adoption; and the continuity-client adapter alone performs the combined manifest/genesis/epoch activation transaction. Required future test seams include each role's denial and effect cases, transaction interruption, and sequencing checks that role and service fencing precede manifest activation. Their precise package and SQL boundaries remain downstream choices. Historical reader outputs cannot enter a successor stage as authority. ([cutover capability boundaries](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L3548-L3599), [sequencing constraint](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-compatibility-design.md#L4228-L4232)) |
| Evidence, tiers, and admission | `file_evidence.verified_file_snapshot()` supplies bounded, symlink-safe, digest-checked acquisition; its inspected test body asserts descriptor reads after source change and closure on consumer error. `ledger.append_record_once()` supplies conflict-detecting append-once fsync; its inspected test body checks a same-record `False` return after an appended truncated tail. ([snapshot](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/lib/hindsight_memory_control_plane/file_evidence.py#L206-L359), [snapshot test](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/tests/test_hindsight_memory_adapters.py#L6295-L6329), [ledger](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/lib/hindsight_memory_control_plane/ledger.py#L1067-L1140), [ledger test](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/tests/test_hindsight_memory_controller.py#L3770-L3775)) `import_runner.py` atomically checkpoints projection-bound completed inspection identities and rereads them on a later call; its inspected test body asserts the resumed item list. This is inspection-call replay, not target mutation. ([checkpoint use](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/lib/hindsight_memory_control_plane/import_runner.py#L108-L182), [checkpoint I/O](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/lib/hindsight_memory_control_plane/import_runner.py#L200-L326), [test](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/tests/test_hindsight_memory_importing.py#L891-L928)) | Reuse acquisition, append, or inspection-replay semantics only where their effect boundary fits. Add a protected evidence registrar/store, immutable campaign/run/result bodies, atomic current tier slots, prerequisite evaluation, qualification receipts, deployment admission, and isolated producer/evaluator/export roles. Independent oracles must not share the production serializer, mutation path, or frozen reader they judge. ([missing successor surface](https://github.com/nisavid/agents/blob/90108b516f5a1c460980a93670348f6e228124f2/tooling/hindsight/docs/journal-acceptance-evidence.md#L7813-L7885)) |

## Dependency direction and planning choices

The literal imports establish the existing dependency layering: `model.py`
imports `canonical.py`; `adapters.py` imports `canonical.py` and `model.py`;
and `reconcile.py` imports adapters, canonical identities, file evidence,
models, and planning. The successor design should
preserve a low-level body/reference layer beneath protected PostgreSQL
interfaces and controller orchestration. Frozen historical readers are a
parallel, revision-pinned input to compatibility conversion. Evidence
registrars consume protected observations, while independent oracles remain
outside the production path. This states dependency constraints, not package
names or an architecture decision.
([model imports](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/lib/hindsight_memory_control_plane/model.py#L1-L10),
[adapter imports](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/lib/hindsight_memory_control_plane/adapters.py#L1-L9),
[orchestration imports](https://github.com/nisavid/agents/blob/afbe95ca1def16b4bc653f255b3337d9dd0b6586/tooling/hindsight/lib/hindsight_memory_control_plane/reconcile.py#L1-L26))

The proposed issue-107 protected C17/M seam has split ownership that none of the
inspected components implements. O16 owns FX01's immutable RX03 opaque
M-admission ID, RX05's authenticated live-session adapter-incarnation ID, and
IA11's currentness projection. O10 owns the immutable RW01
`OperationWorkAdmission` carrier and FW01/FW08 historical projection. The
adapter owns only transaction setup, the single FW02 or FW03 entrypoint call,
commit, and the following separate authoritative protected acknowledgement
read; it has no IA11 method. The proposal-only FW03 call supplies no incarnation
proof, while the accepted 90108 source interface retains that parameter. After entry, FW02/FW03 acquire every applicable rank-1 dependency and
then every applicable rank-2 dependency; neither rank may be skipped. Their two
candidate-AW04 branches invoke IA11 at rank 3. IA11 returns only the typed AW04
projection or denial and reads no RW01. FW02/FW03 then acquire rank 5. FW02
owns atomic RW01 insertion for a new request and fresh-to-RW01 equality on
replay. FW03 owns fresh-to-RW01 equality after rank 5 and only then persists
RW04/RW05/RS14 and returns START. Every ranked lock ends only at the outer
transaction's commit or rollback. Replacement attempted after IA11 holds
rank-3 locks waits for that boundary; only a replacement that wins before
rank-3 locking may determine the subsequently locked binding or deny
admission. The two M/AW04 positive expansions require
the IA11-derived `adapter_incarnation_id` in both complete identity and start;
the other eleven positive J/P/R/V/RECONCILIATION expansions omit it from both
bodies and call IA11 zero times. The RW01 carrier persists after session loss;
FW01/FW08 read it without IA11 but cannot authorize another M reservation or
start.

The issue-107 proposal source and future test seams must therefore cover RX03/RX05 origin, atomic
RW01 insertion with refusal or reservation, exact replay and start-repeat
comparison, FW03 incarnation copying, post-session-loss readback, and exact
IA11 call-site cardinality. The closed `OperationWorkProtectedResult` union
must scope effect-free `ADMISSION_DENIED` for
matrix/currentness/session/fresh-to-stored drift to FW02/FW03, then give
`REQUEST_CONFLICT` for
the same `(plan,request_id)` with changed exact request bytes and therefore a
different RequestKey, then `WORK_ALREADY_RESERVED` for a different
`(plan,request_id)` whose ReservationKey already exists, and finally
effect-free `DATABASE_CONFLICT` for remaining immutable-chain inconsistency.
The RequestKey includes the exact-body digest. FW01/FW08 evaluate no current
activation or session continuity and need a representable `DATABASE_CONFLICT`
arm for immutable-chain inconsistency. Pre-start denial preserves `RESERVED`;
denial after start preserves `STARTED`. Issue 108 may map the target and activation rows;
issue 109 may order implementation and evidence. Neither may move these owners
or infer a callable from the generic wrappers surveyed here.

The downstream tickets must choose:

1. whether successor LF-bearing bodies wrap the current encoder or live in a
   separate canonical package, and how typed executable reference contracts
   are resolved;
2. the concrete protected relations/functions/roles and whether stage,
   recovery, compatibility, and evidence interfaces share one adapter facade
   or several narrow ones;
3. how issue 108 maps exact target and activation rows into the fixed `FM01`
   and continuity-session lifecycle; transaction orchestration, lock order,
   acknowledgement, exact readback, and safe-prefix behavior follow
   [`journal-transaction-recovery-interfaces.md`](journal-transaction-recovery-interfaces.md);
4. how the pinned historical reader implementation is brought into the chosen
   integration base and isolated from successor authority; and
5. how implementation gates depend on vectors, PostgreSQL logic, fault/ACL
   evidence, historical differential coverage, then separately qualified
   release and deployment evidence. No inspected test body or source
   inspection establishes a passing later-tier result.

## Integration-base and evidence limits

Revisions
[`7b165b3...`](https://github.com/nisavid/agents/commit/7b165b3ee97e7e4bbddcb8eafe089f7f1237e0ab)
and
[`79b9071...`](https://github.com/nisavid/agents/commit/79b9071fd4a296df2064536cffe25d2cc8bc47d6),
which contain the frozen historical reader source and the earlier
real-PostgreSQL recovery suite, are not ancestors of `afbe95c...`; their merge
base with this research branch is `8c3c9a4...`.
They therefore identify reusable historical source, not code available on the
pinned implementation base. The PostgreSQL fixture also starts with `-F`, so
it is logical SQL/concurrency evidence only, not physical durability evidence.
([fixture settings](https://github.com/nisavid/agents/blob/79b9071fd4a296df2064536cffe25d2cc8bc47d6/tooling/hindsight/tests/test_hindsight_memory_operation_recovery_postgres.py#L137-L154))

I inspected source and tests but ran no tests, runtime imports, databases, or
providers. This note establishes source ownership and missing seams only; it
does not make the branch integration-ready, qualify the initial support
profile, select implementation sequencing, or authorize implementation or
operation. The eventual integration base, whether either sibling implementation
is incorporated, and whether the target Hindsight release exposes every needed
transaction seam remain unverified. The last question requires separately
approved source outside this repository and was not researched here.
