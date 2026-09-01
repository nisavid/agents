# Hindsight Durable Journal Publication Design

Status: publication architecture selected in
[#73](https://github.com/nisavid/agents/issues/73). Ivan approved these product
choices and this architecture record on 2026-09-01. The complete interrupted
publication and restart contract is recorded in
[`journal-restart-design.md`](journal-restart-design.md) through
[#74](https://github.com/nisavid/agents/issues/74). Compatibility, evidence,
independent assessment, and final design acceptance remain open in
[#75](https://github.com/nisavid/agents/issues/75) through
[#78](https://github.com/nisavid/agents/issues/78). Implementation, deployment,
candidate assembly, and live recovery remain separately authorized work.

## Decision

Hindsight uses the exact target PostgreSQL database as the authoritative owner
of stopped-application and stopped-rollback publication state. A protected,
append-only schema records five ordered stages:

1. the exact authenticated journal (`J`);
2. a causally later durable proof (`P`);
3. a trusted post-proof deadline receipt (`R`);
4. the target mutation and its receipt (`M`); and
5. post-mutation verification (`V`).

The controller advances these stages inline. It does not add a background
qualification worker or another datastore. Private files are reproducible
exports and historical evidence only; no file presence or timestamp authorizes
successor mutation.

A trusted control-plane PostgreSQL adapter owns each stage transaction from
`BEGIN` through `COMMIT`. Runtime callers invoke stage operations; they do not
receive the database role credentials, connection, or an opportunity to run
SQL inside the transaction. The adapter sets `synchronous_commit=on`, checks
the effective setting immediately before `COMMIT`, and reports success only
after the commit acknowledgement. This transaction-owning boundary is part of
the protocol, not an optional client convention.

The authority claim is:

```text
durable_completion(J) <= durable_completion(P)
durable_completion(P) <= trusted_post_proof_upper_bound U
U < approval_expiry
```

`J` contains the exact final authenticated bytes. An intent, pending marker,
request time, transaction-start time, or earlier logical timestamp cannot
substitute for it.

`VALID R` means that the exact `P` was durable before `U`, and `U` was strictly
before expiry. It does not claim that `R` itself committed before expiry. `R`
may become durable after expiry. This distinction avoids an infinite demand
for a later proof of each proof while retaining an exact durable record that a
mutation can consume.

This record fixes the architecture and the constraints already decided while
selecting it. Companion design records supply the complete restart,
compatibility, and acceptance contracts; they do not silently revise the
invariant or replace PostgreSQL as the selected publication owner.

## Scope and failure model

The admitted durability domain is local PostgreSQL WAL on a verified database
host and storage stack. It covers client, server, and operating-system crashes,
reboot, and power loss when the admitted storage truthfully honors flushes.
Every stage transaction sets `synchronous_commit=on`; deployment admission also
requires and verifies:

- `fsync=on`;
- `full_page_writes=on`;
- an allowed `wal_sync_method` for the host and storage profile; and
- a support record for the filesystem, storage device, and write-cache
  behavior.

An operator-approved deployment-admission policy names the supported profiles.
A dedicated local admission controller, acting through a database role that no
publication, mutation, or verification runtime holds, authors immutable
deployment attestations and clock-health envelopes in the protected schema.
The deployment attestation binds the target database, active publication
epoch, host and storage profile, boot and synchronization epoch, policy digest,
issue time, validity interval, and admission generation. Runtime callers cannot
supply or alter those facts.

The clock envelope does not extend a wall-clock reading by trusting later wall
time. It binds a qualified monotonic clock, such as `CLOCK_BOOTTIME` on Linux or
a qualified platform equivalent, to the approval issuer's comparable time
basis. The envelope
records the host and boot identities, synchronization epoch, monotonic anchor,
conservative wall-time upper bound at that anchor, maximum forward rate error,
and monotonic validity deadline. Its bound remains forward-valid through that
deadline even if wall-clock synchronization is lost or wall time steps backward
after issuance. A platform without a qualified nondecreasing clock and a
conservative forward-error model is unsupported.

A protected admission-state row names the current immutable deployment
attestation and clock envelope, the active publication epoch, and whether that
epoch is active or fenced. Every authority-bearing `J`, `P`, `R`, or `M`
transaction locks that state and its exact deployment attestation through
commit, rejects an absent, expired, fenced, or mismatched state at its decisive
observation, and rechecks its required live PostgreSQL settings. `R` also locks
the named clock envelope. Attestation or clock-envelope replacement and
revocation take a conflicting lock, so they order either before the stage
observation or after the stage commit. Revocation fences the affected epoch; it
never edits historical evidence in place.

Verification after exact `M` is a separate evidence-only boundary. It locks the
exact aggregate, `M`, target rows, canonical mutation-lineage row, and terminal
verification slot and authenticates the target database identity, but it does
not require the old publication epoch, deployment attestation, incarnation
capability, or activation-bound session to remain live. It may therefore run
after expiry or fencing without reviving any mutation authority. Its role
cannot create or consume `R`, execute `M`, alter the target, or activate an
epoch.

Each authority-bearing stage records the exact admission generation and
deployment-attestation digest it used. `R` additionally records the exact
clock-envelope digest and monotonic sample used to derive `U`. Verification
evidence instead binds the exact `M`, target database identity, observed
generation, cohort, postimage, canonical mutation-lineage key and head, and
stable verification-attempt identity. A later ordinary evidence expiry does
not rewrite a historical fact, while a later revocation fences the epoch before
an unconsumed `R` can reach `M`. This keeps admission current through `P` and
`R` instead of treating a startup check as permanent authority. Issue #76 owns
the evidence used to qualify the admission controller, clock model, and each
supported profile; it does not transfer attestation authorship to an ordinary
runtime role.

The initial profile admits exactly one transaction-owning adapter instance. It
starts mutation-fenced, opens a dedicated database continuity session, and
generates an unguessable incarnation capability whose client-side copy is held
only in locked process memory. The admission controller authorizes activation,
but the adapter remains the capability's sole creator and sole client-side
holder. On the exact continuity session, the activation transaction installs
the only server-side copy in a session-local relation and writes only its
digest, with a fresh publication epoch, into the durable admission-state row.
The session-local witness is never WAL-logged or included in backup and
disappears atomically when that PostgreSQL backend session ends.

Every `M` executes on that exact activation-bound session. In the mutation
transaction, the protected interface requires the session-local capability,
hashes it, and compares it with the digest in the locked durable admission
state. A different or reconnected session has no witness and cannot satisfy the
predicate, even if client-side loss detection is delayed and the restored row
still says `ACTIVE`. The adapter also erases its memory copy on its own restart,
loss of the continuity session, database-server restart, endpoint-identity
change, or any uncertainty about continuity; it never reconnects while
retaining that capability. Runtime callers never receive it. The capability and
session-local witness form a fail-closed live gate, not a second durable
datastore.

The design does not claim that locally flushed WAL survives permanent loss of
the primary host or disk through an asynchronous WAL archive. Synchronous
replication, an external witness, and a second datastore are not required by
this fault model.

Privileged database, host, or approval-issuer compromise, including deliberate
clock manipulation, is outside the threat model. Ordinary clock
synchronization loss, rollback, reboot, suspend uncertainty, or excessive
error is inside the model and fails closed.

Initial support is limited to a locally bound PostgreSQL deployment where the
controller can attest the database host's durability profile and clock health.
A remote or managed PostgreSQL service remains unsupported until it can provide
equivalent evidence for the same clock domain and durability boundary.

## Publication aggregate and identity

Apply and rollback are distinct publication aggregates. Each aggregate has a
stable conflict key:

```text
(operation_identity, action, plan_digest, publication_epoch)
```

`action` is exactly one of `apply` or `rollback`. The immutable aggregate
binding digest covers every authority-bearing input fixed before `J`,
including:

- protocol and journal schema versions;
- operation and action identities;
- plan, approval, and authorization-receipt digests;
- approval expiry;
- the exact canonical `J` bytes and digest;
- target database and publication-epoch identities;
- the server-derived canonical mutation-lineage key, expected lineage
  generation and head `M`, and exact predecessor `V`, or the explicit genesis
  state;
- expected generation and exact selected and preserved cohort digests; and
- the exact predecessor, preimage, and rollback bindings required by the
  action.

The stable key and binding digest serve different purposes. The same stable
key and the same binding replay the existing aggregate. The same stable key
with a different binding is `CONFLICT`. Plan digest and publication epoch
appear in both for a self-contained binding, but they define request identity:
changing either requires a new separately approved aggregate rather than a
conflict under the old key. A content digest alone is not a stable request key:
changed bytes or other bindings within one request must conflict rather than
silently create an unrelated publication.

Each authority-bearing later stage stores a stage-binding digest that extends
the aggregate binding with every predecessor-stage digest and the exact
admission generation and deployment-attestation digest used by that
transaction. `R` additionally binds the clock-envelope digest, monotonic
sample, error terms, and `U`; `M` additionally binds the active admission state
and incarnation-capability digest it consumed. A stage from another retry,
action, database, epoch, approval, journal, attestation, clock, or incarnation
chain cannot be mixed into the aggregate.

The logical relations are a protected canonical mutation-lineage row, an
aggregate row plus immutable `J`, `P`, `R`, and `M` rows, a terminal
verification slot, immutable verification observations, and successful `V`
rows. An exact `MATCH` fills the terminal slot with `V`; an exact `MISMATCH`
fills it with a terminal mismatch observation. Database constraints prevent
both outcomes for one aggregate. Exact SQL names may follow repository
conventions, but the unique keys, foreign keys, append-only rules, role
boundaries, and stage predicates are part of this design.

Apply `J` atomically stores the exact encrypted rollback preimage and its
integrity bindings in the protected schema. The preimage cannot become
authoritative without the matching `J`, and apply `J` cannot commit without the
preimage. Rollback `J` binds that retained apply preimage rather than publishing
a second preimage boundary.

## Canonical mutation lineage

The initial profile maintains exactly one protected mutation-lineage row for
each target datastore mutation surface. PostgreSQL derives its stable key from
the attested target database identity, the canonical protected target relation
set, and the publication protocol family. A caller cannot supply, rename, or
alias the key.

Every successor Hindsight apply and rollback aggregate that can mutate that
surface shares the row. This includes identical, partially overlapping,
merging, and disjoint cohorts. The coarse lineage intentionally sacrifices
independent mutation concurrency so cohort overlap cannot be misclassified.
Partitioned lineages require a separately accepted design that proves canonical
membership and overlap, defines repartitioning, and migrates the durable head.

The row records a monotonically increasing lineage generation and its head `M`,
or explicit genesis. An aggregate created by `J` binds the exact lineage
generation and head it observed. A non-genesis head is eligible only when its
terminal slot contains matching `V`; the aggregate also binds that exact `V`.
An unverified or mismatched head blocks `J`. Multiple aggregates may bind the
same verified head, but only the first exact `M` can advance it. Every other
aggregate then observes lineage-head drift and requires a new plan and approval
rather than silently rebasing onto the winner. That drift comparison applies
only before the aggregate has its own `M`. A committed `M` atomically makes
itself the expected current head while its terminal slot is empty. Once that
slot is terminal, exact historical replay no longer requires the aggregate to
remain the lineage head.

## Inline publication protocol

### 1. Journal transaction (`J`)

The controller constructs and validates the complete successor journal before
opening the transaction. For apply, it also constructs and verifies the exact
encrypted rollback preimage. The journal has no claimed durable timestamp.

The protected publication interface locks the canonical mutation-lineage row.
It requires explicit genesis or an exact head `M` with matching terminal `V`,
then binds that lineage generation, head, and `V` into the aggregate. It creates
the aggregate and stores the exact canonical bytes and, for apply, the bound
encrypted preimage in the same transaction, or returns the byte-identical
existing rows. A different binding under the stable key returns `CONFLICT`.
Concurrent creators converge through the database unique constraint; they do
not infer absence while another creator is still committing.

The transaction sets `synchronous_commit=on` and verifies the admitted
durability profile. Its success means the local server acknowledged WAL flush
for `J`. A lost client acknowledgement remains ambiguous and is resolved by an
exact query through the same protected interface.

### 2. Durable-proof transaction (`P`)

`P` starts only after `J` commit acknowledgement or exact recovery of `J` from
the protected synchronous path. It reads the exact aggregate and journal,
locks the canonical mutation-lineage row, requires the exact generation, head,
and predecessor `V` bound by `J`, and inserts an immutable proof bound to their
identities and digests. Lineage drift refuses `P` without changing the durable
prefix.

`P` revalidates the current deployment attestation and durability profile, then
commits with `synchronous_commit=on`. Because PostgreSQL flushes WAL in order,
durable completion of the later `P` includes the earlier WAL for `J`. The
separate transaction is necessary because a transaction cannot observe that
its own commit has already completed durably.

A lost `P` acknowledgement is resolved by exact query. Generic visibility or a
row written outside the protected synchronous path is not proof.

### 3. Deadline-receipt transaction (`R`)

`R` begins only after durable `P` acknowledgement or exact recovery. The
transaction-owning adapter locks and reads that exact proof, the canonical
mutation-lineage row, the admission-state row, the current immutable deployment
attestation, and the named immutable clock-health envelope for the database
host. It requires the exact lineage generation, head, and predecessor `V` bound
by `J`; drift refuses `R` before any authority-bearing sample. After the proof
observation, while the transaction retains those locks, the adapter samples the
qualified host monotonic clock and submits that sample to the protected
finalization function. The interface computes without precision truncation:

```text
elapsed = monotonic_sample - monotonic_anchor
U = wall_upper_at_anchor
    + elapsed
    + forward_rate_error(elapsed)
```

The protected function requires the envelope to remain current in the locked
admission state, the host and boot identities to match, and the monotonic sample
to be no earlier than the anchor and no later than the monotonic validity
deadline. Wall-clock rollback cannot reduce `U`; wall time may be recorded as
diagnostic evidence but never enters the authority calculation. Monotonic-clock
rollback or reset, a boot or host change, an invalidated forward-error model,
stale evidence, suspend behavior outside the qualified clock contract, or
insufficient expiry headroom prevents `VALID` and fences the epoch.

The protected interface recomputes the bindings and records exactly one
immutable result:

- `VALID` when `U < approval_expiry`;
- `LATE` when the trusted upper bound is at or after expiry; or
- no authorizing receipt when the time or deployment evidence is unproven.

`R` commits with `synchronous_commit=on`. It may commit after approval expiry or
the envelope's monotonic validity deadline because the immutable receipt records
the already-established post-proof sample and conservative bound. If the
process crashes after sampling a timely `U` but before `R` becomes durable,
that authority is lost. Recovery must not recreate or backdate the sample. If
the commit completed but the acknowledgement was lost, an exact query recovers
`R`, including after expiry.

### 4. Mutation transaction (`M`)

Only an exact immutable `VALID R` admits mutation. `M` runs at serializable
isolation and, in one transaction:

1. locks the aggregate, exact `R`, covered target rows, and server-derived
   canonical mutation-lineage row;
2. requires the aggregate's epoch to equal the unfenced active publication
   epoch in the current admission state and locks that state through commit;
3. requires `M` to be running on the exact activation-bound PostgreSQL session,
   reads that session's nonpersistent capability witness, and verifies its hash
   against the digest bound by the locked admission state;
4. recomputes every aggregate and stage binding, including the exact
   deployment-attestation and clock-envelope digests, and the strict
   `U < approval_expiry` predicate
   rather than trusting a status label;
5. requires the current lineage generation and head to equal the values bound
   by `J` and, for a non-genesis head, requires the exact bound predecessor `V`;
6. checks the expected target generation and exact selected and preserved
   cohort;
7. selects the logical mutation timestamp inside the transaction;
8. performs the target compare-and-swap mutation; and
9. inserts the authoritative immutable mutation receipt and advances the
   lineage row to that `M` atomically.

The `M` receipt records the consumed `R`, canonical mutation-lineage key, prior
lineage generation and head, predecessor `V` or genesis, new lineage generation
and head, pre- and post-target generation, cohort and postimage digests, and
logical mutation timestamp. If the current lineage differs from the binding,
the pre-`M` interface returns `LINEAGE_HEAD_DRIFT` without mutation. A valid
`R`, intact session, or unchanged cohort cannot override that refusal. A
successful `M` is not drift: the transaction replaces the bound predecessor
with its own receipt as the lineage head.

The timestamp describes when the logical mutation was performed. It is not
described as PostgreSQL's commit time or durable-completion time.

`M` commits with `synchronous_commit=on`. A failure before commit changes
neither target state nor the mutation receipt. A lost commit acknowledgement
is resolved by reading the exact receipt, generation, and postimage. An exact
retry cannot apply the mutation twice.

### 5. Verification transaction (`V`)

Verification reads the authoritative `M`, independently derives the expected
postimage, and compares the exact live generation, cohort, and postimage while
holding the aggregate, covered target rows, protected target-publication
lineage, and terminal verification locks. The lineage head must be the exact
`M` under verification before a new observation reads the target. A different
head while its terminal slot is empty is an invariant violation because no
successor `M` may have committed first. An already filled terminal slot returns
its exact outcome without requiring that historical `M` to remain the current
head. The protected interface serializes every verification attempt for the
exact `M` and records one immutable outcome with `synchronous_commit=on`:

- `MATCH` fills the previously empty terminal slot and appends authoritative
  `V`;
- `MISMATCH` fills the previously empty terminal slot with a terminal mismatch
  observation and permanently forbids `V`; or
- `UNABLE_TO_VERIFY` appends a nonterminal observation and leaves the terminal
  slot empty for a later exact attempt.

The first conclusive outcome committed under the aggregate and target-lineage
locks is terminal. Concurrent attempts cannot commit both `V` and mismatch. A
same-attempt replay returns its exact observation; a later attempt after a
terminal outcome returns that outcome without rereading it as a new decision.
`V` remains authoritative only for completed successful verification. A file
export can be regenerated from `J` through `V`; losing an export never changes
authority or requires another mutation. `M` atomically installs itself as the
lineage head. While that head lacks matching `V`, a new `J` cannot bind it and
an already-created sibling fails its bound-head check at `P`, `R`, or `M`.
Therefore no successor mutation can overtake verification. The lineage lock
serializes each transaction's check and update; it is not held across the
interval between `M` and verification.

## State and recovery

These are the minimum recovery constraints implied by the selected
architecture. The complete state-by-state apply and rollback behavior is fixed
by the [durable journal restart design](journal-restart-design.md).

The aggregate's durable stages are monotonic:

```text
ABSENT -> JOURNALED -> PROVEN -> VALID -> MUTATED -> VERIFIED
                              \-> LATE

binding mismatch on any request -> CONFLICT for that request
                                   existing aggregate prefix unchanged
```

Absence of a visible `R` at expiry is not immediately terminal. A pre-expiry
`R` transaction may have sampled `U` and still be committing. Until the
protected same-key recovery boundary proves that no such transaction can
commit, the derived state is `QUALIFICATION_AMBIGUOUS`, which never authorizes
mutation. `UNPROVEN` becomes terminal only after that ambiguity is resolved and
no durable `VALID R` exists. Read-only status may report the ambiguity; it must
not collapse absence into `UNPROVEN` prematurely. The restart design fixes the
exact waiting, timeout, and recovery procedure.

Before expiry, exact recovery may advance `JOURNALED` to `PROVEN` and `PROVEN`
to `VALID` while all predicates still hold. At or after expiry:

- `ABSENT`, `JOURNALED`, and `PROVEN` cannot newly acquire mutation authority;
- an `R` transaction that sampled a valid pre-expiry `U` may finish committing,
  but an aborted or absent `R` cannot be reconstructed;
- a missing `R` remains `QUALIFICATION_AMBIGUOUS` until protected same-key
  recovery resolves any original attempt; a newly sampled post-expiry attempt
  cannot become `VALID`;
- an exact durable `VALID R` may proceed to `M` after expiry only while its
  bound lineage generation, head, and predecessor `V` remain current;
- `LATE` and `UNPROVEN` never admit `M`; a request-scoped `CONFLICT` cannot
  advance its mismatched request but does not freeze the correctly bound
  aggregate; and
- `MUTATED` may only exact-replay or proceed to verification.

Status, handoff, descendant planning, and recovery use these database states.
They do not fall back to files, legacy pending markers, or process-local
observations.

## Rollback

Rollback uses its own stable key, action, approval, journal, proof, receipt,
mutation, and verification chain. Its approval binds the authoritative apply
`M` and `V`, the exact rollback preimage and expected current postimage, the
grant evidence, and the existing retry, reconciliation, cohort, and budget
ceilings.

A valid rollback `M` restores the exact selected preimage once, advances the
generation, preserves all out-of-cohort rows, and leaves grant and ledger state
unchanged. An apply key, approval, or receipt cannot authorize rollback.

## Backup, restore, and publication epochs

The protected publication schema and target state share the same PostgreSQL
WAL and backup/PITR lineage. Full physical backup and PITR are supported only
when they restore the complete database consistently. Selective table,
logical, or publication-only restores are unsupported unless a future design
defines and verifies an equivalent fail-closed invariant audit.

An ordinary publication caller or worker crash may recover the exact prefix
when the dedicated adapter process, its incarnation capability, and its
database session all remain continuous. The initial profile deliberately does
not infer continuity across adapter restart, database connection loss,
database-server restart, operating-system reboot, endpoint-identity change,
clone, PITR, or primary promotion. Each event destroys or invalidates the live
capability and leaves mutation fenced. Already durable stages remain queryable
evidence, but an unconsumed `R` from the prior epoch cannot reach `M`.

The admission controller owns the active-epoch transition. For every fenced
start it verifies the current deployment through an operator- or
orchestrator-owned restoration and promotion signal and authorizes the fenced
adapter to generate a new in-memory incarnation capability. The adapter uses
the exact continuity session for a separately authorized activation transaction
that installs the session-local witness and records a fresh publication epoch
and only the capability digest before mutation traffic is admitted. The signal
coordinates activation; durable mutation authority still resides only in
PostgreSQL. A deployment that cannot provide the required signal remains
fenced.

`M` requires both the unfenced current epoch and proof of the live capability.
It rejects every aggregate whose epoch differs from the active value, every
restored admission row for which the new adapter lacks the secret preimage, and
every call while the deployment is fenced. These checks are required even when
the restored aggregate, `J`, `P`, `R`, and old admission row are internally
consistent. The restart design fixes the exact recovery and operator handoff
matrix; it cannot relax this initial fail-closed continuity boundary without a
separately accepted, rollback-resistant incarnation-witness design.

A prior-epoch `R` with no corresponding durable `M` in the restored timeline
therefore cannot authorize a new mutation. Existing `M` and `V` rows remain
historical evidence. Any desired state-changing continuation after a fenced
event requires explicit rebinding and a newly approved publication.

This fencing prevents a restore to the `J+P+R` prefix from replaying a
post-expiry mutation on a divergent timeline. It also avoids claiming that an
asynchronous archive necessarily contains every locally acknowledged WAL
record after permanent primary-disk loss.

## Legacy cutover

These are the accepted compatibility constraints on the architecture. Issue
[#75](https://github.com/nisavid/agents/issues/75) owns the complete successor
format, reader, versioning, and transition contract.

Historical schemas, canonical bytes, and readers remain unchanged. In
particular, legacy `applied_at` retains its historical pending-marker meaning;
it is never reinterpreted as journal durability, proof durability, receipt
durability, mutation time, or commit time.

Cutover handles each legacy prefix explicitly:

| Legacy state | Successor treatment |
| --- | --- |
| Complete application or rollback chain | Preserve and inspect under its original schema and semantics. |
| Pending marker or journal exists, target mutation absent | Freeze as nonauthorizing. A new successor plan and approval are required. |
| Target mutation already applied, receipts incomplete | Permit exact `already_applied` verification and nonauthorizing evidence closure only. Do not perform another state-changing continuation or manufacture successor stages. |
| Rollback is desired | Require a new, separately approved successor rollback publication. |

No migration or backfill creates `P`, `R`, `M`, or `V` for historical work.
Legacy files never become a compatibility authority for successor mutation.

## Access and enforcement

The publication schema is owned by a dedicated role. Runtime roles receive
only narrow protected interfaces:

- the admission role alone can author or revoke deployment attestations and
  clock envelopes, fence an incarnation, and activate a publication epoch from
  the required external deployment signal and new live capability;
- the publication role can advance and query `J`, `P`, and `R` and lock and read
  the lineage row only to bind `J` and revalidate that binding at `P` and `R`,
  but cannot update the lineage or mutate target state;
- the mutation role can consume an exact `VALID R` through the protected `M`
  interface and advance the lineage only atomically with `M`, but cannot update
  it separately or insert, update, delete, or forge prior stages;
- the evidence-only verification role can append an observation or `V` only
  for the exact immutable `M` and may only lock and read the lineage row, but
  cannot update it or perform any authority-bearing stage;
  and
- no runtime role can update or delete completed stages or bypass the stable
  conflict key.

The trusted PostgreSQL adapter owns connection acquisition, transaction setup,
the single stage call, the immediate pre-commit durability-setting check, and
`COMMIT`; callers cannot wrap a stage in a caller-managed transaction or alter
settings between the check and commit. It is also the sole client-side holder
and source of the live incarnation capability, and the trusted reader of the
qualified host monotonic clock. Activation and every `M` use the same dedicated
PostgreSQL backend session, and `M` atomically verifies that session's sole
nonpersistent server-side copy. The adapter cannot route `M` through a pool or
replacement connection. Server-side interfaces recompute canonical digests,
the conservative time bound, and transition predicates. PostgreSQL ACLs,
constraints, append-only relations, the adapter boundary, and the digest chain
are sufficient under the accepted threat model. The design introduces no
signing key or separate cryptographic authority.

## Retention

The initial protocol has no garbage collection. Aggregates and `J`, `P`, `R`,
`M`, and `V` rows remain append-only and are retained unconditionally. Any
backup, retention, archival, or deletion rule requires a separately accepted
design; implementation must not infer a safe collection horizon. The encrypted
rollback preimage remains durably retained until verified rollback or a
separately approved permanent retirement under the
[restart contract](journal-restart-design.md).

## Acceptance evidence

Issue [#76](https://github.com/nisavid/agents/issues/76) owns the final
acceptance-evidence contract. At minimum, that contract must make these
outcomes falsifiable:

- concurrent same-key, same-binding journal creation produces one `J` and
  exact replays;
- changed bytes, approval, expiry, target database, generation, or cohort under
  the same stable key is `CONFLICT`;
- a changed plan digest or publication epoch cannot reuse the old aggregate and
  requires a new separately approved stable key;
- lost acknowledgements for `J`, `P`, `R`, and `M` recover only the exact
  committed stage;
- a crash after each durable prefix never claims a later transition;
- timely `U` with a lost `R` acknowledgement remains recoverably `VALID`; an
  absent `R` at expiry is first `QUALIFICATION_AMBIGUOUS` and becomes
  `UNPROVEN` only after protected same-key recovery proves that no original
  receipt can still commit;
- `U == expiry` and `U > expiry` never produce `VALID`;
- wall-clock rollback after envelope issuance cannot lower the monotonic-derived
  `U`; monotonic reset or rollback, an invalid forward-error model,
  synchronization evidence outside its qualified lifetime, or host/boot change
  never produces `VALID`;
- a missing or invalid synchronous durability setting prevents authorization;
- generation, cohort, receipt, or database-binding drift aborts `M` without
  mutation;
- the server derives one canonical lineage for identical, partially
  overlapping, merging, and disjoint cohorts in the initial profile;
- `J` cannot bind a non-genesis lineage head until that exact `M` has matching
  `V`, and a terminal predecessor mismatch blocks ordinary successors;
- concurrent aggregates bound to the same verified head produce one lineage
  advance; every loser reports `LINEAGE_HEAD_DRIFT` and cannot reuse its
  approval;
- a sibling `M` racing a loser's `P` or `R` makes the loser fail its
  transaction-local lineage check, so no later pre-`M` authority is appended
  after drift;
- each `M` receipt proves the prior lineage head, consumed predecessor `V`, and
  atomic new lineage head;
- lost `M` acknowledgement recovers the exact postimage and never repeats the
  mutation;
- a successor aggregate cannot execute `M` before the immediately preceding
  mutation on the covered target reaches matching `V`;
- deleted exports reconstruct from database authority;
- caller or worker crash with an intact adapter capability and database session
  permits exact recovery;
- adapter restart, connection loss, database-server restart, operating-system
  reboot, endpoint change, PITR, clone, or promotion erases or invalidates the
  live capability and fences unconsumed prior-epoch receipts;
- `M` on any session other than the activation-bound PostgreSQL backend fails,
  including while the client has not yet detected loss of the old session;
- a restored `ACTIVE` admission row cannot satisfy `M` because the new adapter
  lacks both the bound capability preimage and its session-local witness;
- rollback cannot reuse apply authority and restores the selected preimage
  exactly once; and
- every historical schema retains byte-identical parsing and timestamp
  semantics.

Qualification must also exercise supported PostgreSQL, operating-system, and
storage profiles under process, server, reboot, and power-failure boundaries.
A disposable datastore or logic model is development evidence, not production
durability attestation.

## Rejected alternatives

### Background qualification worker

A worker could advance `J`, `P`, and `R`, but it would add queue ownership,
supervision, recovery lag, monitoring, and another ambiguity surface without
strengthening the invariant. The inline protocol has the same safety boundary
with fewer operational states. A worker may be reconsidered only for measured
throughput or latency needs after the inline protocol is qualified.

### Synchronous standby or external witness

An external witness or synchronously replicated PostgreSQL topology could add
survival of primary-host loss. That benefit is outside the accepted local-WAL
fault model and would add deployment, failover, clock-domain, and recovery
cost. It is not justified for the initial protocol.

### New durable datastore

SQLite largely repackages the private-file host, clock, and storage fault
domain. etcd adds quorum, certificate, snapshot, compaction, upgrade, and
incident responsibilities without supplying the required trusted epoch-time
bound. Cloud Spanner offers a meaningfully stronger managed durability and
time model, but the investigated contract does not expose a value that clearly
upper-bounds durable completion of the exact bytes; it also adds the largest
dependency and operating surface.

Because independence from target PostgreSQL is not required, none of those
benefits outweighs reusing the existing database.

### Private files

Files remain the negative baseline. Cross-platform file and directory flushes,
namespace publication, storage honesty, and host-clock error would require a
narrow support matrix. More importantly, the existing pending-marker time is
earlier than final authenticated publication and cannot establish the fixed
expiry claim.

## Evidence and implementation boundary

The decision is grounded in the revision-pinned
[durability research](https://github.com/nisavid/agents/blob/97e4a2d7b075f79d980657e2b584fa33abdfe9f8/tooling/hindsight/docs/research/journal-durability.md),
[journal-contract inventory](https://github.com/nisavid/agents/blob/a0ee372aaebfc88c35588474af61f710cf57f6ff/tooling/hindsight/docs/research/journal-contracts.md),
and [backend comparison spike](https://github.com/nisavid/agents/blob/2704a823d7fce8e522a1176c69a97c4288ee5c0d/tooling/hindsight/prototypes/PROTOTYPE-journal-publication-backends.md).
The spike's PostgreSQL run was disposable loopback evidence, and its etcd and
Spanner cases were modeled, not executed. The two known prototype presentation
defects do not affect this disposition: the prototype conflict receipt mixes
attempted-binding provenance with existing-record timing, and its committed
report still calls the artifacts uncommitted.

This record resolves the architecture question. The exhaustive interrupted
publication and restart behavior is selected in
[#74](https://github.com/nisavid/agents/issues/74) and recorded in the
[durable journal restart design](journal-restart-design.md). The remaining
design map owns:

- [#75](https://github.com/nisavid/agents/issues/75): historical format,
  reader, and transition compatibility;
- [#76](https://github.com/nisavid/agents/issues/76): falsifiable design and
  implementation evidence obligations;
- [#77](https://github.com/nisavid/agents/issues/77): independent assessment
  of the integrated design; and
- [#78](https://github.com/nisavid/agents/issues/78): Ivan's final acceptance
  for a separate implementation-planning map.

Only after that acceptance may a separately authorized effort translate the
design into successor schemas, protected SQL interfaces, source changes,
tests, deployment admission, and migration sequencing. No implementation may
reinterpret historical evidence, accept the local repaired-source commit as a
release, provision infrastructure, assemble a candidate, or act on a live
grant, claim, row, worker, or provider without its own authority.
