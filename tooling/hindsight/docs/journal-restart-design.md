# Hindsight Durable Journal Restart Design

Status: interrupted publication and restart behavior selected in
[#74](https://github.com/nisavid/agents/issues/74). Ivan approved automatic
safe advancement, match-only authoritative verification, and explicit
retirement of rollback preimages on 2026-09-01. The publication architecture
selected in [#73](https://github.com/nisavid/agents/issues/73) remains fixed.
Compatibility, acceptance evidence, independent assessment, and final design
acceptance remain open in [#75](https://github.com/nisavid/agents/issues/75)
through [#78](https://github.com/nisavid/agents/issues/78). Implementation,
deployment, candidate assembly, and live recovery remain separately authorized
work.

## Decision

Hindsight automatically advances an interrupted apply or rollback aggregate
when the authoritative PostgreSQL state proves exactly one safe successor
transition. It refuses when no durable timely receipt exists before expiry,
live mutation continuity has been fenced, a predecessor outcome remains
ambiguous, or the target or binding has drifted. It never infers authority from
a file, process, log, reconstructed timestamp, or absence observed outside the
protected PostgreSQL interface.

The durable publication chain remains:

```text
J -> P -> R -> M -> V
```

where `J` is the exact canonical journal, `P` is its causally later durable
proof, `R` records the trusted deadline qualification, `M` atomically records
the target mutation and its receipt, and `V` independently verifies the exact
postimage. Restart does not add another durable authority or revise any stage's
meaning.

Automatic recovery has one intentionally strong consequence: an ordinary
caller or controller restart can lead to `M` without another operator gesture
when an exact durable `VALID R` and its activation-bound PostgreSQL session,
incarnation capability, epoch, and admission state all remain continuous. A
caller restart does not itself revoke that already-established authority. An
adapter restart or any loss of the protected session does revoke the live
mutation path and permanently fences an unconsumed old `R`.

Verification is evidence-only. Only an exact `MATCH` creates authoritative
`V`. An inability to verify may be retried; a conclusive mismatch is immutable
and sticky and cannot later be converted into successful verification for the
same aggregate.

The exact encrypted rollback preimage remains retained and verifiably bound
until rollback reaches matching `M` and `V`, or a separately approved permanent
retirement completes. Expiry, successful apply verification, fencing, elapsed
time, or creation of a replacement aggregate never implies permission to
delete it.

## Authoritative state model

Restart state is a product rather than a longer linear status enum:

```text
action
  x durable prefix
  x unresolved next-stage outcome
  x continuity and admission context
  x deadline context
  x verification outcome
```

`action` is exactly `apply` or `rollback`. They remain distinct publication
aggregates with the stable key selected in #73:

```text
(operation_identity, action, plan_digest, publication_epoch)
```

The durable prefix is the longest exact, binding-consistent chain present in
the protected schema:

| Prefix | Meaning |
| --- | --- |
| `ABSENT` | No authoritative `J` exists for the approved aggregate. |
| `JOURNALED` | Exact `J` exists and no exact `P` exists. |
| `PROVEN` | Exact `JP` exists and no resolved `R` exists. |
| `VALID` | Exact `JPR` exists and `R` proves `U < approval_expiry`. |
| `LATE` | Exact `JPR` exists and `R` proves `U >= approval_expiry`. |
| `MUTATED` | Exact `JPRM` exists and no authoritative `V` exists. |
| `VERIFIED` | Exact `JPRMV` exists. |

The prefix never folds contextual facts into historical evidence. For example,
an aggregate with durable `VALID R` remains `VALID` after an adapter restart,
but its recovery disposition becomes `FENCED` and it can no longer reach `M`.

The protected recovery interface also derives these observable dispositions:

| Disposition | Meaning |
| --- | --- |
| `STAGE_AMBIGUOUS` | A predecessor transaction may still commit, so absence is not conclusive. |
| `QUALIFICATION_AMBIGUOUS` | Specifically, an original `R` attempt may still commit. It never authorizes `M`. |
| `UNPROVEN` | Protected resolution proved that no original `R` can commit and no durable authorizing `R` exists. |
| `FENCED` | Admission, epoch, adapter, session, or incarnation continuity required for mutation is lost. |
| `VERIFICATION_BLOCKED` | `M` exists, but the evidence-only verifier could not reach a conclusion. |
| `VERIFICATION_MISMATCH` | A conclusive target mismatch was recorded after `M`. |
| `CONFLICT` | A caller presented a different binding under an existing stable key. The existing aggregate is unchanged. |
| `INVARIANT_VIOLATION` | The protected schema or target exposes a state that the protocol cannot produce. |

`STAGE_AMBIGUOUS` is never silently mapped to absence. `FENCED` does not erase
or downgrade a durable prefix. `CONFLICT` is scoped to the mismatched
request and does not freeze the correctly bound aggregate. `LATE`, `UNPROVEN`,
`VERIFICATION_MISMATCH`, and `INVARIANT_VIOLATION` are terminal for ordinary
advancement of the affected aggregate.

## Recovery entry points

Read-only status and authority-bearing recovery are separate interfaces.

`status` reads the protected PostgreSQL state and reports the durable prefix,
derived disposition, continuity, deadline relation, verification outcome, and
permitted next action. It acquires no transition lock, creates no stage, samples
no authority-bearing clock, resolves no ambiguity by changing state, and never
imports a runtime merely to inspect it.

The controller's startup or explicit resume path invokes a serialized automatic
reconciler. The reconciler:

1. authenticates the exact aggregate key and binding;
2. reads and locks the protected aggregate through the stage interface;
3. resolves any predecessor transaction outcome before considering a retry;
4. revalidates every predicate required for the next stage;
5. performs at most the uniquely permitted exact transition;
6. commits or resolves that transition before considering another; and
7. records a bounded, queryable recovery observation for every refusal,
   ambiguity, fence, or successful advancement.

It may continue through multiple safe stages during one recovery pass, but each
stage remains its own transaction and durable boundary. It does not become a
queue, qualification daemon, or generic background worker. Concurrent startup
paths converge through the same aggregate serialization and stage uniqueness
rules.

`M` and conclusive verification also take a protected target-lineage lock keyed
to the exact database and mutation domain; overlapping cohorts share one
lineage. A later Hindsight mutation cannot overtake verification of the
immediately preceding `M`. A successor `M` requires the predecessor either to
have no mutation receipt or to have matching `V`; terminal mismatch requires a
separately approved remediation path.

Recovery is bounded. Lock or transaction-resolution timeout leaves the exact
stage ambiguous and returns control; it does not guess an outcome, start a
replacement aggregate, or broaden the action. A later pass may try resolution
again.

## Restart matrix

The matrix below governs apply and rollback after their respective admission
requirements have been met. `Continuous` means the exact admission generation,
publication epoch, adapter incarnation capability, activation-bound PostgreSQL
session, and session-local witness still satisfy #73.

| Durable prefix | Continuous and before expiry | Continuous at or after expiry | Fenced or discontinuous |
| --- | --- | --- | --- |
| `ABSENT` | Revalidate the approval, complete binding, target generation, exact selected and preserved cohorts, and preparation evidence; create the exact `J`. | Refuse. No new durable authority may begin; require a separately approved replacement aggregate. | Preserve preparation evidence only; require a new epoch and separately approved aggregate. |
| `JOURNALED` | Exact-replay `J`, then create `P`. | Preserve and query `J`; it cannot newly gain mutation authority. | Preserve and query; require a new epoch and separately approved aggregate. |
| `PROVEN` | Exact-replay `JP`, then make one protected `R` attempt with a fresh post-proof monotonic sample. | Resolve only an original `R` attempt. If no such transaction can commit and no durable `R` exists, record `UNPROVEN`. Never take a fresh sample. | Resolve an original attempt for evidence only. Even a recovered `VALID R` remains fenced from `M`. |
| `VALID` | Automatically perform exact `M`. | Automatically perform exact `M`; expiry does not revoke a durable timely receipt. | Preserve `R`, refuse `M`, and require a new epoch and separately approved replacement aggregate. |
| `LATE` | Preserve as terminal and nonauthorizing. | Preserve as terminal and nonauthorizing. | Preserve unchanged. |
| `MUTATED` | Never repeat `M`; resolve its receipt and automatically attempt evidence-only verification. | Same. | Never repeat `M`; a fresh evidence-only verifier may still verify it. |
| `VERIFIED` | Return exact terminal replay and status. | Return exact terminal replay and status. | Preserve as terminal historical evidence. |

Contextual dispositions override the prefix row's ordinary transition. In
particular, `MUTATED + VERIFICATION_MISMATCH` is terminal rather than eligible
for another successful verification attempt, and any `INVARIANT_VIOLATION`
permits diagnosis only.

A wrong binding under an existing stable key returns `CONFLICT` for
that request. It neither changes the durable prefix nor prevents a correctly
bound caller from taking the matrix transition.

For `PROVEN` at or after expiry, protected same-key resolution takes the same
aggregate lock used by `R` creation. If an original transaction still owns the
lock, recovery waits only for the configured bound. A timeout leaves
`QUALIFICATION_AMBIGUOUS`. Once PostgreSQL proves the transaction committed,
recovery uses its exact immutable `R`; once PostgreSQL proves it aborted and no
receipt exists, recovery records `UNPROVEN`. Elapsed time alone proves neither
outcome.

The strict comparison remains `U < approval_expiry`. Equality is `LATE`.
`R` may commit after expiry only when it records the pre-expiry post-proof
sample established by the original transaction. Restart never recreates or
backdates that sample.

## Preparation before `J`

Preparation is nonauthoritative. It may validate inputs, construct canonical
bytes, encrypt the rollback preimage in memory, and calculate bindings, but no
cached preparation fact can substitute for `J`. For apply, the `J` transaction
atomically stores the encrypted preimage and its integrity bindings with the
aggregate and journal. There is no separate authoritative preimage-publication
stage before `J`.

When the prefix is `ABSENT` and approval remains valid, automatic recovery may
discard or reconstruct partial preparation. Immediately before creating `J`,
it revalidates the exact plan and approval, action, database, publication
epoch, expected generation, selected and preserved cohort, journal bytes,
preimage, and every authority-bearing digest. A mismatch refuses the old
aggregate; it does not repair the binding in place.

At or after expiry, preparation cannot be promoted to `J`, regardless of an
earlier request time, cached journal bytes, pending marker, or process-local
observation. A replacement requires a new plan digest or publication epoch as
appropriate, separate approval, and a distinct aggregate linked to the old
one.

Rollback preparation additionally revalidates the authoritative apply `M` and
matching `V`, the retained exact preimage, and the expected current apply
postimage. Without all of them, rollback cannot begin.

## Transaction ambiguity and lost acknowledgements

Every protected stage uses the aggregate row lock and a unique stage key. A
same-key, same-binding caller returns the exact committed stage; a same-key,
different-binding caller receives request-scoped `CONFLICT`, while the
existing aggregate retains its prefix and authority. Two recovery clients can
never create two stage rows or apply the target effect twice. `M` and
conclusive verification additionally share the protected target-lineage lock,
so another Hindsight aggregate cannot mutate the covered target between them.

The resolver treats each uncertain stage as follows:

- **`J`**: query the exact aggregate and journal. If `J` committed, replay it.
  If PostgreSQL conclusively aborted it, retry only while approval is still
  valid and every binding revalidates.
- **`P`**: query the exact proof. If it committed, replay it. If it conclusively
  aborted, retry only while the aggregate may still advance before expiry.
- **`R`**: take the protected same-key lock, wait boundedly for any original
  transaction, and return the exact durable receipt or conclusive absence.
  Conclusive absence permits a fresh attempt only before expiry.
- **`M`**: query and lock the exact mutation receipt, generation, and postimage
  before any retry. If `M` committed, never remutate. If it conclusively
  aborted, retry only when exact `VALID R` and every live continuity predicate
  still hold, including after expiry. If continuity was lost, fence it.
- **`V` or verification observation**: resolve by its stable evidence identity.
  Replay an existing observation; never duplicate it to compensate for a lost
  acknowledgement.

If the bounded wait expires for `M`, recovery reports `STAGE_AMBIGUOUS(M)` and
performs neither a retry nor verification. Only conclusive transaction outcome
allows the state to advance.

Any visible hole that the protocol cannot produce fails closed. Examples
include `P` without exact `J`, `R` without exact `JP`, `M` without its consumed
exact `VALID R`, a target postimage without the atomic `M` receipt, a target and
receipt generation split, or `V` without its exact `M`. Ordinary recovery does
not synthesize missing stages, rewrite the target, or declare the closest
plausible prefix. It records `INVARIANT_VIOLATION` and permits diagnosis only.

## Continuity and fencing

An ordinary caller or worker crash does not fence mutation authority when the
dedicated adapter, incarnation capability, activation-bound PostgreSQL backend
session, session-local witness, admission generation, and publication epoch all
remain live and exact.

Any of these events fences the affected epoch before an unconsumed `R` may
reach `M`:

- adapter restart or loss of its in-memory capability;
- loss or replacement of the activation-bound database connection;
- PostgreSQL server restart;
- operating-system reboot;
- endpoint-identity change;
- deployment-attestation revocation or incompatible replacement;
- database clone, PITR, or primary promotion; or
- any uncertainty about the exact continuity chain.

Fencing is monotonic for the old epoch. Restart does not reactivate the old
capability, reconnect while retaining it, or copy `R` into a new epoch. A new
adapter activation records a fresh epoch, and any desired state-changing
continuation uses a separately approved aggregate. The replacement links its
predecessor for audit but derives no mutation authority from it.

Fencing after `M` does not prevent evidence-only verification because `M`
already contains the complete immutable mutation binding. The verification
role receives no capability to consume `R`, alter `M`, or mutate the target.

## Verification after `M`

Verification independently reads the authoritative `M`, derives its exact
expected generation, cohort, and postimage, and compares them with the target.
A fresh or fenced adapter may invoke the narrow evidence-only interface. That
interface can append observations for the exact `M` but cannot write `J`, `P`,
`R`, or `M`, alter the target, activate an epoch, or restore mutation
continuity.

Each verification attempt has a stable evidence identity. The protected
transaction locks the aggregate, exact `M`, covered target rows, target lineage,
and the aggregate's terminal verification slot before reading the target. It
then appends one immutable outcome with `synchronous_commit=on`:

| Outcome | Effect |
| --- | --- |
| `MATCH` | Append authoritative `V` bound to the exact `M`, generation, cohort, and postimage. |
| `UNABLE_TO_VERIFY` | Append a nonconclusive observation and report `VERIFICATION_BLOCKED`; a later exact attempt may retry. |
| `MISMATCH` | Append a conclusive mismatch observation and report `VERIFICATION_MISMATCH`. No `V` may later be appended for this aggregate. |

A mismatch is sticky even if a later read happens to match. Later observations
may add diagnostic evidence but cannot erase the mismatch, rewrite `M`, or
rehabilitate the aggregate. The controller performs no automatic rollback and
does not treat mismatch as completed verification. Diagnosis and any repair use
a separately approved remediation contract.

`MATCH` and `MISMATCH` are mutually exclusive terminal outcomes. The first
conclusive outcome committed under the aggregate and target-lineage locks fills
the terminal verification slot. Database constraints prohibit both a terminal
mismatch and `V` for the same aggregate. `UNABLE_TO_VERIFY` leaves the slot
empty. A successor `M` cannot commit while that slot is empty or contains
mismatch, so normal Hindsight progression cannot manufacture a mismatch by
overtaking verification.

An interrupted verification first resolves the exact attempt identity. A lost
acknowledgement returns the existing observation or `V`; it never creates a
second record with a newly chosen outcome.

## Apply and rollback

Apply and rollback use the same restart matrix after their distinct admission
requirements are satisfied. They never share an approval, aggregate, `R`, `M`,
or `V`.

Rollback may create `J` only when the referenced apply aggregate has exact
authoritative `M` and matching `V`, and its separately approved binding covers:

- the exact apply aggregate and `M` and `V` digests;
- the exact encrypted rollback preimage and integrity bindings;
- the expected current apply postimage and generation;
- the selected and preserved cohorts;
- grant evidence and existing retry, reconciliation, cohort, and budget
  ceilings; and
- the rollback plan, approval, database, and publication epoch.

Rollback `M` uses serializable compare-and-swap semantics to restore the exact
selected preimage once and advance generation once. It preserves grants, prior
publication evidence, failed and completed rows, and every out-of-cohort row.
Intervening drift refuses the transaction; rollback never overwrites the drift
or expands its cohort. A committed rollback `M` is never repeated and reaches
terminal completion only through its own matching `V`.

An apply `M` without matching `V`, an apply verification mismatch, unavailable
preimage, or unexpected current postimage cannot authorize ordinary rollback.
Those states require diagnosis and, if desired, a separately approved
remediation design.

## Preimage retention and permanent retirement

The apply `J` transaction atomically stores the exact encrypted rollback
preimage with its action, aggregate, target, generation, cohort, ciphertext,
and integrity bindings. `J` cannot commit without that protected preimage
record, and the preimage cannot become authoritative without the matching `J`.
Under the PostgreSQL publication architecture, that database record is
authoritative for the bindings and continued availability of the ciphertext. A
private-file copy may be an export or backup, but its presence or loss cannot
change rollback eligibility.

The preimage and the ability to authenticate and decrypt it remain required
until either:

1. the exact rollback aggregate reaches matching `M` and `V`; or
2. a separately approved permanent-retirement action completes and leaves an
   immutable retirement record bound to the exact preimage and predecessor
   aggregate.

No ordinary recovery, expiry, fencing event, successful apply `V`, replacement
aggregate, backup rotation, or elapsed retention interval authorizes deletion.
The implementation has no inferred garbage-collection horizon.

Permanent retirement is distinct from apply and rollback. It does not mutate
the target, revive or change a grant, or reinterpret the publication chain.
This record fixes retirement as the only separately approved alternative to
verified rollback, but does not design that third action's authority,
backup-and-ciphertext boundary, interruption states, or destruction proof. The
later implementation-planning map must create and accept that dedicated design
before any retirement interface exists. Until then, preimages remain retained.

If the preimage, its integrity evidence, or required decryption capability is
missing or fails verification before approved retirement completes, recovery
reports rollback unavailable and fails closed. It never synthesizes a preimage
from current target rows.

## Preservation and replacement

Every aggregate and committed stage remains immutable and queryable, including
partial prefixes, `LATE`, `UNPROVEN`, request-conflict observations where the
implementation retains them, ambiguity observations, fences, failed
verification observations, successful `V`, and retirement evidence. Recovery
does not delete superseded state or rewrite a historical timestamp, digest,
result, or status.

A new approval creates a distinct stable key through its new plan digest or
publication epoch as required and binds an immutable predecessor link. It does
not copy an old `R`, reopen an expired lifetime, reset retry or reconciliation
counts, broaden the cohort, renew budgets, change completed or failed rows, or
turn prior evidence into new authority.

Historical formats keep their original meaning. This successor restart
contract does not backfill `P`, `R`, `M`, or `V` into a legacy chain or promote
a historical file into PostgreSQL authority. Issue #75 owns the complete
format, reader, and transition compatibility design.

## Observable recovery report

Every read-only status or authority-bearing recovery result reports enough
information to distinguish the product state without interpretation from
ambient artifacts:

- action and stable aggregate key;
- aggregate and stage-binding digests;
- longest exact durable prefix;
- unresolved stage and ambiguity disposition, if any;
- approval expiry relation and recorded `R` outcome;
- admission generation, publication epoch, and continuity or fence reason;
- authoritative `M` generation and postimage binding, if present;
- verification outcome and evidence identity;
- rollback-preimage availability and retirement state;
- exact permitted next action or terminal refusal reason; and
- predecessor or replacement aggregate link.

The report does not include raw retained content or claim that a process is
quiescent merely because its PID is absent. Status remains read-only even when
it reports that automatic recovery would be allowed.

## Acceptance obligations

Issue [#76](https://github.com/nisavid/agents/issues/76) owns the executable
acceptance-evidence contract. At minimum it must distinguish, for both apply
and rollback:

- crash before commit, after commit before acknowledgement, and after
  acknowledgement for every `J`, `P`, `R`, `M`, and `V` transition;
- `U` immediately before expiry, exactly at expiry, and after expiry;
- a timely `R` that commits after expiry, aborts, loses acknowledgement, or
  remains locked beyond the recovery wait bound;
- caller restart with intact adapter continuity from every prefix;
- every enumerated fencing event from every prefix;
- two recovery clients racing at every stage, with one committed stage and one
  target effect;
- a successor aggregate racing predecessor verification, with no later `M`
  before the predecessor reaches matching `V`;
- admission replacement or revocation racing with `P`, `R`, and `M`;
- ambiguous `M` with eventual commit, eventual abort, and bounded resolution
  timeout;
- fencing after `M` but before `V`;
- `MATCH`, repeated `UNABLE_TO_VERIFY`, sticky `MISMATCH`, and lost verification
  acknowledgement;
- impossible stage holes and target/receipt splits failing closed;
- generation or cohort drift before `M`, postimage drift after `M`, and
  rollback compare-and-swap drift;
- rollback interruption at every stage producing exactly one restoration and
  one generation advance;
- missing, corrupted, and explicitly retired rollback preimages;
- replacement aggregates retaining every predecessor prefix and limit without
  inheriting its mutation authority.

The test oracle must assert the durable prefix, contextual disposition,
permitted next action, target generation and postimage, and immutable evidence
set. Merely observing a successful command exit or a present export is not
acceptance evidence.

## Implementation boundary

This record fixes restart behavior and preservation obligations; it does not
select SQL relation names, schema numbers, CLI spelling, process supervision,
or migration sequencing. It authorizes no source implementation, deployment,
candidate assembly, grant or claim change, datastore mutation, worker launch,
provider call, rollback, preimage retirement, or live recovery action.

The remaining design map owns:

- [#75](https://github.com/nisavid/agents/issues/75): historical format,
  reader, and transition compatibility;
- [#76](https://github.com/nisavid/agents/issues/76): falsifiable design and
  implementation evidence obligations;
- [#77](https://github.com/nisavid/agents/issues/77): independent assessment
  of the integrated design; and
- [#78](https://github.com/nisavid/agents/issues/78): Ivan's final acceptance
  for a separate implementation-planning map.

Only after that acceptance may a separately authorized implementation effort
translate this contract into successor schemas, protected PostgreSQL
interfaces, controller behavior, tests, deployment admission, and migration
sequencing.
