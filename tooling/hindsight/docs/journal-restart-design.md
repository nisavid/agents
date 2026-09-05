# Hindsight Durable Journal Restart Design

Status: interrupted publication and restart behavior selected in
[#74](https://github.com/nisavid/agents/issues/74). Ivan approved automatic
safe advancement, match-only authoritative verification, and explicit
retirement of rollback preimages on 2026-09-01. The publication architecture
selected in [#73](https://github.com/nisavid/agents/issues/73) remains fixed.
The accepted compatibility contract is recorded in
[`journal-compatibility-design.md`](journal-compatibility-design.md) through
[#75](https://github.com/nisavid/agents/issues/75). The accepted evidence bar
is recorded in
[`journal-acceptance-evidence.md`](journal-acceptance-evidence.md) through
[#76](https://github.com/nisavid/agents/issues/76). Independent assessment and
final design acceptance remain open in
[#77](https://github.com/nisavid/agents/issues/77) and
[#78](https://github.com/nisavid/agents/issues/78). Implementation, deployment,
candidate assembly, and live recovery remain separately authorized work.

## Decision

Hindsight automatically advances an interrupted apply or rollback aggregate
when the authoritative PostgreSQL state proves exactly one safe successor
transition. It refuses when protected recovery establishes that no durable
receipt records a timely pre-expiry post-proof sample,
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
`V`. Only the acceptance contract's closed retryable unable categories permit
a later attempt. A conclusive mismatch, invariant violation, or unproven target
identity fills the terminal slot and cannot later be converted into successful
verification or mismatch for the same aggregate.

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
  x mutation-lineage and predecessor-verification context
  x verification outcome
```

`action` is exactly `apply` or `rollback`. They remain distinct publication
aggregates with the stable key selected in #73:

```text
(operation_identity, action_binding.action, plan.body_digest, publication_epoch)
```

A rollback additionally binds exactly one predecessor variant.
`SUCCESSOR_APPLY` names an authoritative successor apply `M` with matching `V`
and retains the ordinary restart contract. `LEGACY_COMPLETE_APPLY` names a
complete application chain admitted by the accepted
[journal compatibility design](journal-compatibility-design.md); it never
creates or assumes a synthetic predecessor `M` or `V`.

The exact `OperationPlan/v1` carries the same closed action binding as `J`:
apply, `SUCCESSOR_APPLY` rollback, or `LEGACY_COMPLETE_APPLY` rollback with its
complete predecessor and preimage inputs, the apply variant's exact
`TargetApplyPayload/v1`, exact operation grant, retry limits, reconciliation
limits, and row and elapsed-time budgets. Recovery requires byte equality
between the plan and `J.action_binding`; it never reconstructs a variant from
the top-level `action`. Every preimage reference must already resolve to a
complete immutable `RollbackPreimageBinding/v1` with `authority=NONE` whose
`ProtectedRollbackCiphertext/v1` has exact digest-and-length-verified bytes in
the protected PostgreSQL candidate store before plan insertion. Apply and
legacy rollback preparation create and verify that candidate and byte row
before plan issuance; successor rollback uses the predecessor apply's retained
binding and protected-byte adoption. The operation approval points to that
exact plan, and the authorization receipt points to both. Their issuance order
is strictly
`grant.issued < plan.created < approval.issued < authorization.issued <
plan.valid_until`, and the grant, plan, approval, authorization,
`J.approval_expiry`, and `R.approval_expiry` values are exactly equal. Approval
has stable key
`plan`; authorization has stable key `(plan, approval)`. Exact replay returns
the same body and changed bytes conflict. No independently earlier or later
deadline is valid.

Grant issuance, plan issuance, approval, authorization, grant revocation, and
operation-authority revocation are separate authenticated keyed transitions
owned by mutually isolated principals. Every authority-bearing recovery stage
locks the exact plan's current grant and authorization slots and rejects a
revoked, displaced, differently keyed, or incompletely linked body. A new `J`,
`P`, or `R` decision also requires its exact strict pre-expiry rule. `M` does
not recheck elapsed expiry: it consumes the immutable timely `VALID R`, checks
the grant, plan, approval, and authorization receipt for exact identity,
current selector, and nonrevocation, and does not resample their shared
deadline. Independently timed deployment-policy, attestation, evidence, clock,
capability, identity, and epoch gates retain their current-time checks, and
every other live continuity, target, cohort, protected-ciphertext, preimage,
and lineage gate must still pass.
There is no ambient grant, retry default, reconciliation default, budget
default, or principal that can combine these lifecycle powers.

Mutation state and retained restore content have distinct byte contracts.
`TargetMutationImage/v1` is the complete closed state projection: it contains
the target, surface, lineage key, current generation, and exact selected and
preserved cohort memberships and values. `M.before_image_digest`,
`M.after_image_digest`, and each compatibility `snapshot_digest` are SHA-256
over independently reconstructed successor-canonical image bytes, including
one LF. `TargetRestorePayload/v1` instead contains the selected cohort content
to restore and no generation, preserved cohort, or mutation-image digest. Its
own digest is over its complete successor-canonical bytes with one LF.

Apply desired content has a third, distinct contract. The exact
`TargetApplyPayload/v1` in `ApplyBinding` is generation-free and contains the
complete desired selected projection. Its target, surface, lineage key, and
selected membership equal the restore payload and locked before image. Its
digest is over its complete successor-canonical bytes with one LF. Recovery
cannot reconstruct or replace it from current target values, a procedure name,
or an implementation default.

`RollbackPreimageBinding/v1` binds the protected ciphertext, decrypted source
digest and length, registered source body and wire contract, exact
`RestorePayloadConversion/v1`, and resulting restore payload. A successor
source is the exact LF-terminated payload body. A legacy source is the exact
historical no-LF wire body decoded as `LegacyRestoreContent/v1` and converted
field by field; historical bytes are never reinterpreted as successor bytes.
Recovery recomputes each source, conversion, payload, membership, and state
digest independently. Apply resolves the immutable plan-bound apply payload,
proves that the before image, apply payload, and restore payload have the same
selected membership, preserves the locked preserved cohort, substitutes only
the apply payload's complete selected values, increments generation once, and
derives the after image. Rollback locks the current before image, proves its selected membership
equals the payload membership, preserves the locked preserved cohort, replaces
the selected content from the payload, increments generation once, and derives
the after `TargetMutationImage/v1`. It never requires an earlier-generation
restore payload to equal the new-generation after image.

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
| `UNPROVEN` | The original `R` already has its exact committed `CONCLUSIVE_NONCOMMIT` result from the sole conclusive close, and a later protected reconciliation bound that result and proved that no durable authorizing `R` exists. |
| `FENCED` | Admission, epoch, adapter, session, or incarnation continuity required for mutation is lost. |
| `PREDECESSOR_VERIFICATION_PENDING` | The canonical lineage head has `M` but no conclusive verification outcome. A bounded evidence-only attempt may verify it; no successor `J` or `M` may begin. |
| `PREDECESSOR_VERIFICATION_MISMATCH` | The canonical lineage head has a terminal mismatch. Ordinary successor publication is blocked pending separately approved remediation. |
| `LINEAGE_HEAD_DRIFT` | Before its own `M`, a `JOURNALED`, `PROVEN`, or `VALID` aggregate's bound predecessor generation, head, or `V` no longer equals the canonical current state. The old aggregate cannot rebase or mutate. |
| `VERIFICATION_BLOCKED` | `M` exists, but the evidence-only verifier recorded one of the closed retryable unable categories. |
| `VERIFICATION_MISMATCH` | A conclusive target mismatch was recorded after `M`. |
| `TARGET_IDENTITY_UNPROVEN` | Verification could not authenticate the exact target database identity and recorded a terminal verification failure. |
| `CONFLICT` | A caller presented a different binding under an existing stable key. The existing aggregate is unchanged. |
| `INVARIANT_VIOLATION` | The protected schema or target exposes a state that the protocol cannot produce. When discovery occurs after `M`, the verifier records a terminal verification failure. |

`STAGE_AMBIGUOUS` is never silently mapped to absence. `FENCED` does not erase
or downgrade a durable prefix. `CONFLICT` is scoped to the mismatched
request and does not freeze the correctly bound aggregate. `LATE`, `UNPROVEN`,
`PREDECESSOR_VERIFICATION_MISMATCH`, `LINEAGE_HEAD_DRIFT`,
`VERIFICATION_MISMATCH`, `TARGET_IDENTITY_UNPROVEN`, and
`INVARIANT_VIOLATION` are terminal for ordinary advancement of the affected
aggregate. `PREDECESSOR_VERIFICATION_PENDING` is a
bounded retry state, not mutation authority. `LINEAGE_HEAD_DRIFT` never applies
after the aggregate has committed its own `M`: a `MUTATED` aggregate with an
empty terminal slot requires the current lineage head to be its own `M`, and a
terminal historical aggregate need not remain the current head.

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
4. resolves the canonical lineage head's verification state before creating a
   successor `J`;
5. revalidates every predicate required for the next stage;
6. performs at most the uniquely permitted exact transition;
7. commits or resolves that transition before considering another; and
8. records a bounded, queryable recovery observation for every post-reservation
   refusal, ambiguity, fence, or successful advancement.

Each post-reservation recovery observation is exactly one member of the
acceptance contract's closed successor-canonical `RecoveryObservation/v1`
union. `STAGE_AMBIGUOUS` and
`QUALIFICATION_AMBIGUOUS` use the ambiguity kind; `UNPROVEN` uses its dedicated
kind; continuity loss uses the fence kind; a safely committed transition uses
the advancement kind; exact replay returns that existing committed binding
without creating an observation; and every other refusal uses the refusal
kind with its exact closed code. A conclusive-noncommit close uses the
advancement kind with `CONCLUSIVE_NONCOMMIT_RECORDED` and names the original
attempt's typed terminal result. Every kind uses `reservation` as its sole
primary stable key, repeats that reservation's complete work identity, and
becomes replayable only through its unique
`OperationWorkCommittedResult/v1` binding. Exact retry returns byte-identical
evidence for that reservation while changed bytes conflict. Aggregate,
request, code, stage, transaction, fence, and transition fields are content,
not alternate observation keys; two distinct charged reservations therefore
remain two distinct results. All members
are immutable, queryable, and explicitly `authority=NONE`. They cannot replace
`J`, `P`, `R`, `M`, `V`, a deployment attestation, an active epoch, a lineage
head, a fence binding, or any stage-admission predicate.

`UNPROVEN` is ordered after, not combined with, that conclusive close. Its
observation repeats the exact original `R` reservation, start, transaction,
complete work identity, and digest and binds both its already committed
`OperationWorkCommittedResult/v1` with
`result_kind=CONCLUSIVE_NONCOMMIT` and the referenced
`OperationWorkConclusiveNoncommitResult/v1`. The original slot is already
`COMMITTED`; the `UNPROVEN` transaction closes only its own distinct charged
reservation. A still-`STARTED` original, an empty or different result, or a
committed `R_VALID` or `R_LATE` result cannot produce `UNPROVEN`.

A transaction resolution or ambiguity query that proves its original
transaction committed has a different closed result. Its reservation commits
exactly one typed `OperationWorkTransactionResolutionOutcome/v1` or
`OperationWorkAmbiguityQueryOutcome/v1`, which names the byte-identical
original committed-result binding and repeats both the original and resolver
reservation, start, transaction, and work identities. It creates no recovery
observation and no duplicate stage. Exact retry returns the same typed outcome;
a changed original result or identity conflicts. Resolution of a resolver that
is itself left `STARTED` uses that resolver's exact transaction subject and
must terminate in the same original-committed or conclusive-noncommit branch.

Every observation carries the acceptance contract's discriminated aggregate
identity. When `J` exists, `COMMITTED_J.journal_digest` equals `digest(J)`.
For `ABSENT`, `ABSENT_REQUEST.request_key_digest` is SHA-256 over the
successor-canonical bytes, including the trailing LF, of exactly the action,
operation identity, plan body digest, and publication epoch. An absent
observation cannot carry a journal digest. The `J_CREATED` advancement retains
that request identity and points to the committed `J` as its result; later
observations use the committed-journal identity.

It may continue through multiple safe stages during one recovery pass, but each
stage remains its own transaction and durable boundary. It does not become a
queue, qualification daemon, or generic background worker. Concurrent startup
paths converge through the same aggregate serialization and stage uniqueness
rules.

The initial profile has one protected mutation lineage for the entire Hindsight
mutation surface in each target datastore. PostgreSQL derives its key from the
attested database identity, canonical protected target relation set, and
publication protocol family; callers cannot define a mutation domain. Exact,
partially overlapping, merging, and disjoint cohorts all share the lineage.
This deliberately conservative serialization avoids any need to infer overlap.

Before creating `J`, recovery locks the lineage row and binds explicit genesis
or the exact current head `M` and its matching `V`. A
`LEGACY_COMPLETE_APPLY` rollback may bind genesis only when the frozen legacy
reader, authenticated cutover manifest, complete legacy application chain,
exact current cutover generation and postimage, and exact encrypted preimage all
reverify. If a non-genesis head is unverified,
recovery reports `PREDECESSOR_VERIFICATION_PENDING` and may make a bounded
evidence-only verification attempt for that predecessor. A match permits the
lineage gate to be reevaluated; retryable `UNABLE_TO_VERIFY` leaves it pending
for a later pass. A terminal mismatch reports
`PREDECESSOR_VERIFICATION_MISMATCH`; a terminal invariant or identity failure
reports its exact category. Each requires separately approved remediation.

Every target generation is the acceptance and compatibility contracts' common
`TargetGeneration`: a canonical nonnegative safe JSON integer. Recovery rejects
negative, quoted, signed, fractional, exponent, leading-zero, overflowing, or
otherwise aliased forms. A `LEGACY_COMPLETE_APPLY` rollback additionally
requires its plan, preimage binding, `J`, manifest projection, protected
successor target-generation slot initialized at activation, and live target to
name the same exact generation.

Recovery uses the same bijective target bridge as activation. The compatibility
database name, database OID, and PostgreSQL system identifier must project
without normalization to the exact successor-canonical
`TargetDatabaseIdentityBody` stored in the successor `TARGET_DATABASE`
reference. Every activation and `LEGACY_COMPLETE_APPLY` rollback compares the
full projection; no subset, alias, or newly resolved identity is accepted.

Every pre-`M` authority-bearing `J`, `P`, and `R` transaction locks that same
row and requires its generation and exact bound state—genesis, or head and
predecessor `V`—to equal the values bound by `J`. Every `J`, `P`, `R`, and `M`
also locks the exact current operation grant and authorization slots, the
target-and-surface-keyed current deployment-policy slot, and the attestation reference and
the complete deployment, design, implementation, and release result pointers;
it requires exact reference equality and `PASS` through commit, resolves every
tier result's complete ordered prerequisite-result references, and requires
each referenced prerequisite to remain the protected current `PASS`. It also
resolves the support profile's exact controller-host, PostgreSQL-host,
PostgreSQL-endpoint, and topology bodies and requires them to equal the
attestation and protected live deployment. For
`macos-local-postgresql-v1`, only exact `SAME_HOST_LOCAL` deployment with equal
controller/database host identities, both host operating-system references
equal to the top-level operating-system component, the PostgreSQL host's
PostgreSQL and storage references equal to their top-level components, both
stable boot-configuration references equal to the top-level boot component's
configuration, the live projection's actual boot identity equal to the
attestation and clock envelope, and its exact
`PostgresqlComponentConfiguration/v1` and live projection bind the same
complete effective canonical Unix-socket-directory sequence. That sequence has
exactly one member; its configured path, symlink-resolved absolute path, and
device/file identity equal the endpoint's embedded directory member, and the
endpoint address is the complete pathname derived from that resolved path and
ending in `.s.PGSQL.<configured-port>` with separate port field `NONE`.
TCP, including literal
loopback, is unsupported. The qualification
plan, receipt, attestation, and live deployment must also share the support
profile's exact qualified `ClosurePolicyLimits/v1`. Remote or managed topology,
hostname/proxy/tunnel substitution, or host, target, configured or resolved
socket path, directory identity, count or order, complete address, port, or
transport drift is fenced. Those stages also resolve the attestation's exact
typed `RoleGrantSet/v1`,
`WriterInventory/v1`, and complete deployment-acquisition sequence. Any
missing, extra, duplicate, unresolved, unclassifiable, or changed inherited,
`PUBLIC`, ownership, default-privilege, function-mediated, background,
replication, or service writer path, or any replaced acquisition, projection,
procedure, clock envelope, or boot identity, fences the stage. For a
compatibility-activated epoch, those stages
also lock the admission and legacy-writer fence rows, exact current per-epoch
handoff, and epoch-independent persistent fence evidence. They recompute its
bound fence generation and live service-disable evidence,
login/connection/write-admission, database-role ACL, and zero-live-writer drain
evidence, including the exact drain-observation generation. Restored admission,
a write regrant, a newly live writer path, or evidence drift refuses the stage.
At `M`, recovery repeats every check, then atomically mutates the target, writes
`M`, and advances the lineage head. If
another aggregate advanced first, the next attempted `P`, `R`, or `M` reports
`LINEAGE_HEAD_DRIFT`. If the fence changed, the stage refuses without consuming
old authority. It never substitutes the new head or reuses the old plan and
approval. Consequently, two
aggregates may bind one verified head, but only the first exact `M` can win.

Recovery is bounded. Lock or transaction-resolution timeout leaves the exact
stage ambiguous and returns control; it does not guess an outcome, start a
replacement aggregate, or broaden the action. A later pass may try resolution
again.

Every invocation first submits one immutable `OperationWorkRequest/v1` to the
acceptance contract's uncharged, side-effect-free committed-result preflight.
The request binds the complete work identity, including forward or recovery
mode and the exact recovery request when present. A byte-identical committed
result returns immediately and writes nothing. Only an unresolved request
enters the protected accounting transaction. Under the plan accounting lock,
that transaction either commits one charged
`OperationWorkReservation/v1`, or commits one separately request-keyed,
`authority=NONE` `OperationWorkPreReservationRefusal/v1` without a charge or
reservation. Exhaustion, arithmetic overflow, invalid clock binding, stale
ordinal, request conflict, and unavailable reservation all use that refusal
path and perform no work. Reservation-keyed recovery-observation guarantees
apply only after a reservation commits.

The charged transaction atomically consumes the checked-next retry or
reconciliation count and complete declared duration before any query, wait,
stage attempt, external effect, or verification work begins. It derives an
ambiguity deadline from the remaining reconciliation-duration, elapsed-time,
and per-attempt limits with checked UInt128 arithmetic; equality is exhausted.
The reservation binds the unchanged request, protected reservation-time
observation, exact clock envelope, actual boot identity, and complete canonical
work identity. Starts and deadline comparisons require that same current
envelope and boot; reboot or envelope replacement rejects comparison and
permits only a newly identified, separately charged reconciliation under the
new binding. Authority-bearing `J`, `P`, `R`, and `M` also require the current
attestation; evidence-only `V` and recovery may bind a fresh current qualified
clock after the aggregate attestation expires or is fenced, without reviving
that authority. Committed charges never roll back after failed work or reset
after a crash, controller replacement, new request ID, or lost acknowledgement.

The protected start slot advances once from `RESERVED` to `STARTED`, atomically
inserting its exact `OperationWorkStart/v1` and registered
`TransactionIdentity/v1` and binding the sole dispatch to that attempt and
adapter invocation. Transaction resolution and ambiguity queries reference
that exact transaction body. Reconciliation references one exact registered
`ReconciliationSubject/v1`. Every observation, terminal result, and protected
recovery-state comparison resolves and recomputes the same transaction or
subject body. The only following slot change is the terminal
`STARTED -> COMMITTED` result binding. A forward invocation ordinarily inserts
its typed result and `OperationWorkCommittedResult/v1` together. A recovered
stage advancement instead commits one `RecoveryAdvancementObservation/v1` as
the reservation's sole result; its `result_body` points to the stage body
inserted in the same transaction. A crash or controller replacement cannot
start it again. Exact replay terminates at preflight and creates no new
observation. An absent, uncommitted, ambiguous, or started attempt requires a
separately charged typed resolution.

If that resolution or its exact ambiguity query proves the original
transaction committed, its one transaction inserts the exact typed
original-committed outcome and resolver committed-result binding and closes
the resolver slot. The outcome maps the original reservation, start,
transaction, work-identity digest, and already committed result to the
resolver's own reservation, start, transaction, and work identity. It inserts
no duplicate stage or recovery observation. Byte-identical replay converges on
that result; any changed original result or mapping conflicts. A later resolver
for a resolver left `STARTED` uses the earlier resolver's exact transaction as
its reconciliation subject and obeys the same closed mapping.

Conclusive noncommit uses a distinct charged reconciliation reservation and
start. Its close transaction locks the original and resolution `STARTED`
slots; proves that the original transaction cannot commit and has no result;
inserts the original attempt's
`OperationWorkConclusiveNoncommitResult/v1` and committed-result binding;
changes the original slot to `COMMITTED`; records the resolution's
`CONCLUSIVE_NONCOMMIT_RECORDED` advancement observation and committed-result
binding; and changes the resolution slot to `COMMITTED`. All writes commit or
abort together. Only then may recovery allocate a new attempt identity,
ordinal, reservation, and full charge or, once no authorizing `R` can still
commit, allocate the separate publication-qualification reconciliation that
may record `UNPROVEN`. Before terminal conditions, the same close leaves the
replacement-`R` path available under the ordinary gates. Exact replay returns
the terminal non-effect and never redispatches the original. If the resolution
itself
crashes while `STARTED`, another distinct charged reconciliation must close it
before either original can be replaced; the same finite reconciliation count
and duration prevent an unbounded chain. A distinct verification-attempt ID,
transaction resolution, ambiguity query, or reconciliation can never reuse
the original reservation. Recovery refuses work when a binding, start state,
counter, duration, or deadline would mismatch, overflow, or exceed its plan
ceiling.

Compatibility-closure restart remains a separate nonauthorizing path. It
resolves the exact policy-bound case and request, recomputes the protected
reservation or fresh resolution deadline from the recorded qualified sample
and every `ClosurePolicyLimits/v1` operand with checked UInt128 arithmetic, and
requires all copied limits and the deployment-attestation policy reference to
remain exact. A wall-derived horizon reserves the separately rounded full
margin `q + ceil(q*n/d)`; subtracting raw `q` or the single combined rounding
is invalid. Before reservation expiry, observer invalidation permits only a
newly fenced same-ordinal takeover. Every abandonment branch requires a fresh
qualified observation whose conservative lower bound is at or after the
reservation deadline. Equality at any
lease, reservation, case, attestation, connection, call, or clock-validity
deadline is expired; overflow, policy drift, or missing full-resolution margin
creates no synthetic observation or successor authority.

## Restart matrix

The matrix below governs apply and rollback after their respective admission
requirements have been met. `Continuous` means the exact admission generation,
publication epoch, current operation-grant and authorization slots, exact
retry, reconciliation, and budget bodies, target-surface current
deployment-policy slot and attestation reference,
complete current deployment, design, implementation, and release `PASS`
partitions and their exact current prerequisite-result references, exact
same-host local support-profile binding, adapter incarnation capability,
activation-bound PostgreSQL session, and session-local witness still satisfy
#73 and #76. `Before expiry` for `J` and `P` means their exact qualified
`PreStageExpiryObservation/v1` records a protected pre-commit sample with
conservative `U` strictly below the one shared grant/plan/approval/authorization
expiry; equality is late. It does not bound later macOS scheduling, commit,
WAL flush, or acknowledgement.

| Durable prefix | Continuous and before expiry | Continuous at or after expiry | Fenced or discontinuous |
| --- | --- | --- | --- |
| `ABSENT` | Revalidate the exact grant, plan, approval, authorization receipt, equal shared expiry, complete action binding, immutable nonauthorizing preimage body, digest-and-length-verified protected PostgreSQL ciphertext, target generation, exact selected and preserved cohorts, preparation evidence, and canonical lineage gate; create the exact `J`, its protected binding and byte adoptions, and a `CURRENT` pre-stage observation atomically only from genesis or an exactly verified head. | Resolve an original `J` transaction only. If none committed, refuse; no new durable authority may begin, and a replacement aggregate requires separate approval. | Preserve nonauthorizing preparation evidence only; require a new epoch and separately approved aggregate. |
| `JOURNALED` | Exact-replay `J`, then create `P` only with its own `CURRENT` pre-stage observation under the same authority chain. | Resolve an original `P` transaction only. If none committed, preserve and query `J`; it cannot newly gain mutation authority. | Preserve and query; require a new epoch and separately approved aggregate. |
| `PROVEN` | Exact-replay `JP`, then make one protected `R` attempt with a fresh post-proof monotonic sample. | Resolve only an original `R` attempt. If it committed, use its exact `R`. If it cannot commit, first commit the sole conclusive close; only a later distinct reconciliation that binds that exact terminal result may record `UNPROVEN`. Never take a fresh sample. | Resolve an original attempt for evidence only. Even a recovered `VALID R` remains fenced from `M`; `UNPROVEN` still requires the prior exact conclusive close. |
| `VALID` | Automatically perform exact `M` only while the grant, plan, approval, and authorization receipt retain exact current unrevoked identities; independently timed deployment policy, attestation, evidence, clock, capability, identity, and epoch gates remain unexpired; and continuity, target/cohort/protected-ciphertext/preimage, any legacy fence, and the bound lineage generation and exact genesis or head-plus-`V` state remain current. | Same live gates; the shared operation-authority expiry is not sampled again and does not revoke a durable timely receipt. | Preserve `R`, refuse `M`, and require a new epoch and separately approved replacement aggregate. |
| `LATE` | Preserve as terminal and nonauthorizing. | Preserve as terminal and nonauthorizing. | Preserve unchanged. |
| `MUTATED` | Never repeat `M`; resolve its receipt and automatically attempt evidence-only verification. | Same. | Never repeat `M`; a fresh evidence-only verifier may still verify it. |
| `VERIFIED` | Return exact terminal replay and status. | Return exact terminal replay and status. | Preserve as terminal historical evidence. |

Every matrix transition also requires its work reservation to be available. A
`STARTED` reservation admits no replacement even when PostgreSQL has already
proved noncommit. Recovery must first commit the separately charged atomic
conclusive-noncommit close described above. The matrix may then admit a new
fully charged attempt when its remaining authority and limits permit one, or
at terminal qualification conditions a distinct charged reconciliation whose
`UNPROVEN` body binds that already committed close. It may not do both after a
terminal `UNPROVEN` result.

Contextual dispositions override the prefix row's ordinary transition. In
particular, `PREDECESSOR_VERIFICATION_PENDING` permits only bounded
evidence-only predecessor verification, `PREDECESSOR_VERIFICATION_MISMATCH`
permits separately approved remediation only, and `LINEAGE_HEAD_DRIFT` requires
a new plan and approval. `MUTATED + VERIFICATION_MISMATCH`, `MUTATED +
TARGET_IDENTITY_UNPROVEN`, and `MUTATED + INVARIANT_VIOLATION` are terminal
rather than eligible for another successful verification or mismatch attempt.
They permit diagnosis and separately approved remediation only.

A wrong binding under an existing stable key returns `CONFLICT` for
that request. It neither changes the durable prefix nor prevents a correctly
bound caller from taking the matrix transition.

For `PROVEN` at or after expiry, protected same-key resolution takes the same
aggregate lock used by `R` creation. If an original transaction still owns the
lock, recovery waits only for the configured bound. A timeout leaves
`QUALIFICATION_AMBIGUOUS`. Once PostgreSQL proves the transaction committed,
recovery uses its exact immutable `R`; once PostgreSQL proves it aborted and no
receipt exists, recovery first invokes the sole conclusive-noncommit close.
That transaction commits the original reservation's exact terminal noncommit
body and committed-result binding and closes its own resolution. Only after
those bodies preexist may a separately charged publication-qualification
reconciliation reserve and record `UNPROVEN`. That observation binds both
bodies and repeats the exact original `R` reservation, start, transaction,
complete stage-attempt work identity, and work-identity digest. Its transaction
changes only its own reservation and result. An evidence campaign, run,
fixture, or tier coordinate is invalid. Elapsed time alone proves neither
outcome.

The strict comparison remains `U < approval_expiry`. Equality is `LATE`.
`R` may commit after expiry only when it records the pre-expiry post-proof
sample established by the original transaction. Restart never recreates or
backdates that sample.

`J` and `P` have a distinct closed, nonauthorizing pre-stage expiry procedure.
Each exact `PreStageExpiryObservation/v1` binds stage, plan, approval,
authorization receipt, active epoch, stage predecessor (`NONE` for `J`, exact
`J` digest for `P`), and caller-known observation request ID. Its protected
stable key is exactly `(stage, plan, approval, authorization_receipt,
admission.publication_epoch, stage_predecessor_digest,
observation_request_id)`. It recomputes the acceptance design's exact
qualified-clock UInt128 upper bound and upward-rounded forward error from a
fresh protected sample taken under the stage locks immediately before starting
the `J` or `P` write. `CURRENT` requires that `U` be strictly below the shared
grant/plan/approval/authorization expiry; equality is `LATE`. No scheduler,
transaction-duration, commit, WAL-flush, or acknowledgement bound is claimed.

Under the same locks, `CURRENT` and its stage commit atomically. `LATE` appends
only the observation. An invalid clock, continuity, current-policy,
prerequisite, deployment, authority, or arithmetic gate appends neither. Exact
same-key replay returns the canonical observation and any atomic stage;
different bytes conflict. Recovery first resolves an original ambiguous stage
transaction. If it committed, the durable prefix already includes the stage.
That remains true when commit or acknowledgement completed after expiry;
post-commit verification, revocation, or pointer replacement can fence the next
transition but cannot erase committed stage visibility.
If it conclusively aborted, recovery may take a fresh sample only while the
shared authority remains current and the new conservative `U` is strictly
before expiry. At or after expiry it may exact-query or resolve, but
cannot create another observation request or stage. The observation has
`authority=NONE` and cannot substitute for `J`, `P`, or `R`.

## Preparation before `J`

Preparation is nonauthoritative. It may validate inputs, construct every
non-clock journal input, encrypt the rollback preimage, and calculate bindings,
but no cached preparation fact can substitute for `J`. Before plan issuance,
apply and
`LEGACY_COMPLETE_APPLY` preparation must create and fully verify the exact
immutable `RollbackPreimageBinding/v1` in the protected candidate registry;
its `ProtectedRollbackCiphertext/v1` and the exact ciphertext bytes occupy the
protected PostgreSQL candidate store keyed and verified by digest and length.
Both bodies have `authority=NONE`. A `SUCCESSOR_APPLY` plan instead names the
retained predecessor-apply binding and byte row.
Apply preparation also creates the complete generation-free
`TargetApplyPayload/v1`, verifies its target, surface, lineage key, selected
membership, and digest, and places its exact reference and digest in
`ApplyBinding`. This immutable body has `authority=NONE`; it is not stored as
an executable operation or inferred later from current target state.
This pre-plan registration is not an authoritative preimage-publication stage.
Only the later `J` transaction atomically adopts that exact approved binding
and protected byte row into journal-owned state.

When the prefix is `ABSENT` and the exact grant, plan, approval, and
authorization receipt remain current and their one shared validity remains
live, automatic
recovery may discard or reconstruct partial preparation other than the
plan-bound apply payload, preimage body, or protected ciphertext. It may only
exact-query and reverify those immutable bodies and the stored PostgreSQL
bytes; changing or recreating any of them requires a new plan. Immediately
before creating `J`,
it revalidates the exact grant, plan, approval, authorization receipt,
closed action binding, database, publication epoch, expected generation,
selected and preserved cohort, non-clock journal inputs, apply payload when
present, preimage, retry limits, reconciliation limits, budgets, and every typed authority
reference. It also locks and revalidates the exact current deployment policy
and attestation plus their complete current
deployment, design, implementation, and release result partitions and every
exact prerequisite-result pointer. It locks the server-derived canonical
mutation-lineage row and requires explicit genesis or the exact head
`M` with matching `V`. The new aggregate binds that lineage generation, head,
and `V`. An unverified head triggers bounded evidence-only predecessor
verification; mismatch blocks ordinary publication. A mismatch in any binding
refuses the old aggregate; it does not repair the binding in place.

Only after deriving the protected `CURRENT` pre-stage expiry observation under
those locks does the transaction insert that observation into the otherwise
complete journal body, successor-canonicalize it, append its one trailing LF,
and derive `digest(J)`. It requires `J.action_binding` to equal the plan's
closed action binding byte for byte, enforces the exact issuance order and
shared deadline, and creates finalized `J`, its exact journal-preimage
binding and protected-byte adoptions, and the observation atomically. No
complete `J`, journal digest, or
observation-bearing journal bytes exist before the protected sample. The
candidate body alone remains nonauthorizing. The plan, approval,
authorization receipt, grant, limit bodies, clock, deployment, and all result
and prerequisite pointers stay locked through synchronous commit.

At or after expiry, preparation cannot be promoted to `J`, regardless of an
earlier request time, cached non-clock journal inputs, pending marker, or
process-local observation. A replacement requires a typed plan reference with a new body
digest or a new publication epoch as appropriate, separate approval, and a
distinct aggregate linked to the old one.

`SUCCESSOR_APPLY` rollback preparation additionally revalidates the
authoritative apply `M` and matching `V`, the retained exact preimage, and the
expected current apply postimage. That rule is unchanged.

`LEGACY_COMPLETE_APPLY` rollback preparation instead revalidates the exact
authenticated cutover-manifest entry, complete legacy application chain through
the frozen read-only decoder, exact target database and cutover generation,
complete current postimage and selected and preserved cohorts, explicit lineage
genesis, and the exact decryptable encrypted legacy preimage. Its new successor
plan and approval bind every one of those facts. A legacy approval cannot be
reused. A pending marker, corrupt, unknown, excluded, or incomplete chain,
receipt-incomplete `already_applied` closure, missing preimage, current-state
drift, or non-genesis lineage permits remediation only and cannot begin
rollback.

## Transaction ambiguity and lost acknowledgements

Every protected stage uses the aggregate row lock and a unique stage key. A
same-key, same-binding caller returns the exact committed stage; a same-key,
different-binding caller receives request-scoped `CONFLICT`, while the
existing aggregate retains its prefix and authority. Two recovery clients can
never create two stage rows or apply the target effect twice. Each `J`, `P`,
`R`, `M`, and conclusive verification transaction takes the protected lineage
lock for its own check or update. The lock is not held between transactions.
Instead, the first committed `M` atomically advances the head; a new `J` cannot
bind the unverified head, and every preexisting sibling fails its bound-head
check at its next `P`, `R`, or `M`. Thus another Hindsight aggregate cannot
mutate the datastore before verification.

The resolver treats each uncertain stage as follows:

- **`J`**: query the exact aggregate and journal. If `J` committed, replay it.
  If PostgreSQL conclusively aborted it, retry only while the exact plan,
  approval, and authorization receipt remain current, every binding
  revalidates, and a fresh pre-stage observation is `CURRENT`.
- **`P`**: query the exact proof. If it committed, replay it. If it conclusively
  aborted, retry only while the same authority remains current and a fresh
  pre-stage observation is `CURRENT`.
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

Whenever an original attempt in this list reached `STARTED`, conclusive abort
evidence is not itself permission to retry. The separately charged resolver
must first commit the original attempt's typed conclusive-noncommit result and
both atomic slot closes. Only the resulting terminal binding makes a later
fully charged attempt eligible.

If the bounded wait expires for `M`, recovery reports `STAGE_AMBIGUOUS(M)` and
performs neither a retry nor verification. Only conclusive transaction outcome
allows the state to advance.

Any visible hole that the protocol cannot produce fails closed. Examples
include `P` without exact `J`, `R` without exact `JP`, `M` without its consumed
exact `VALID R`, a target postimage without the atomic `M` receipt, a target and
receipt generation split, or `V` without its exact `M`. Ordinary recovery does
not synthesize missing stages, rewrite the target, or declare the closest
plausible prefix. It records `INVARIANT_VIOLATION` and permits diagnosis only.
After `M`, that disposition is backed by the exact terminal verification
failure body and fills the aggregate's terminal slot; it is never a retryable
unable observation.

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
- controller-host, PostgreSQL-host, endpoint-identity, endpoint-address, port,
  transport, target, or deployment-topology change;
- remote or managed endpoint substitution;
- replacement or revocation of the exact target-surface deployment-policy
  slot;
- deployment-attestation revocation or incompatible replacement;
- deployment, design, implementation, or release current-result replacement,
  invalidation, staleness, failure, or supersession;
- database clone, PITR, or primary promotion; or
- any uncertainty about the exact continuity chain.

Fencing is monotonic for the old epoch. Restart does not reactivate the old
capability, reconnect while retaining it, or copy `R` into a new epoch. A new
adapter activation records a fresh epoch, and any desired state-changing
continuation uses a separately approved aggregate. The replacement links its
predecessor for audit but derives no mutation authority from it.

A new activation begins only after the deployment-attestation finalizer has
atomically allocated one epoch, inserted its protected `RESERVED_FENCED` row,
issued the bound attestation, and installed both the current-attestation and
target-surface current-reserved-activation selectors. Its immutable target,
surface, predecessor current-active epoch, continuity session, capability,
deployment attestation, support profile, and exact four host/endpoint/topology
bindings, actual boot identity and clock envelope, typed role-grant set and
writer inventory, and complete immutable deployment-acquisition sequence must
equal the proposal and, for compatibility cutover, the current
origin-or-adoption manifest binding. The attestation's
`proposed_publication_epoch`, proposal's `reserved_publication_epoch`, selected
row epoch, and selector are equal. Recovery never treats that row as active and
cannot run `J`, `P`, `R`, or `M` against it. Only exact recovery of the combined
activation transaction may establish that it atomically stored manifest and
genesis, changed the selected row once to `ACTIVE`, compare-and-swapped the
current-active pointer from its bound predecessor, and cleared the reserved
selector. An uncertain abort leaves the exact `RESERVED_FENCED` row selected
and blocks a new reservation. After recovery conclusively proves noncommit and
that the proposal is no longer admissible, one protected transaction changes
the selected row to permanent `ABANDONED_FENCED`, clears the selector, makes
the attestation noncurrent, and preserves the prior active pointer. The epoch
high-water mark never moves backward, so that value is never reused,
activated, renumbered, or reopened. Lost acknowledgement is resolved by
exact-querying the complete row, selectors, manifest, genesis, and pointer; no
component is replayed independently.

Recovery of an interrupted compatibility fence never carries the original
pre-fence check past its first access-revocation transaction. Immediately
before every later external observation, cancellation, termination, drain, or
service-disable effect, it authenticates and locks the current fence,
manifest, proposal, keyed policy, attestation, result partitions, qualified
clock, admission and ACL state, and exact step record; takes a fresh qualified
sample; and proves the conservative bound remains below every authority and
finite effect deadline through completion. The locks remain held until the
outcome is durably recorded. Failure creates no effect, and v1 has no reusable
continuation receipt.

Recovery also locks the fixed fence-binding slot for the target surface.
`FRESH` is available only when that slot has no active binding under any epoch.
Whenever it is occupied, recovery requires `COMPATIBILITY`, the exact protected
current binding, and a separate equality check between that binding's
`reserved_publication_epoch`, now active, and the aggregate's publication
epoch. An old binding cannot be ignored merely because recovery proposes
another epoch.

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
requires the lineage head to remain that exact `M` before creating a new
observation; a different head with an empty terminal slot is
`INVARIANT_VIOLATION`, because a successor could not legitimately have passed
the predecessor gate. An already terminal attempt exact-replays its outcome
without requiring that historical `M` to remain the current head. A new attempt
appends one immutable outcome with `synchronous_commit=on`:

| Outcome | Effect |
| --- | --- |
| `MATCH` | Append authoritative `V` bound to the exact `M`, generation, cohort, and postimage. |
| `UNABLE_TO_VERIFY` | Only `EXPECTED_STATE_UNAVAILABLE`, `TARGET_READ_UNAVAILABLE`, `TIMEOUT`, or `VERIFIER_INTERNAL_ERROR` may append a nonconclusive observation and report `VERIFICATION_BLOCKED`; a later exact attempt may retry. |
| `MISMATCH` | Append a conclusive mismatch observation and report `VERIFICATION_MISMATCH`. No `V` may later be appended for this aggregate. |
| `TERMINAL_FAILURE` | Append the exact terminal `INVARIANT_VIOLATION` or `TARGET_IDENTITY_UNPROVEN` body. No later `V`, mismatch, or fresh verification read is permitted for this aggregate. |

A mismatch or terminal failure is sticky even if a later read appears to
resolve it. Later diagnostic evidence cannot erase the terminal body, rewrite
`M`, or rehabilitate the aggregate. The controller performs no automatic
rollback and does not treat any failure as completed verification. Diagnosis
and any repair use a separately approved remediation contract.

`MATCH`, `MISMATCH`, and `TERMINAL_FAILURE` are mutually exclusive terminal
outcomes. The first conclusive outcome committed under the aggregate and
target-lineage locks fills the terminal verification slot. Database constraints
prohibit more than one of `V`, terminal mismatch, and terminal failure for the
same aggregate. `UNABLE_TO_VERIFY` is limited to the closed retryable categories
and leaves the slot empty. A successor `M` cannot commit while that slot is
empty or contains any failure, so normal Hindsight progression cannot overtake
verification.

An interrupted verification first resolves the exact attempt identity. A lost
acknowledgement returns the existing unable observation, terminal observation,
or `V`; it never creates a second record with a newly chosen outcome.

## Apply and rollback

Apply and rollback use the same restart matrix after their distinct admission
requirements are satisfied. They never share a plan action binding, approval,
authorization receipt, aggregate, `R`, `M`, or `V`.

For predecessor variant `SUCCESSOR_APPLY`, rollback may create `J` only when the
referenced apply aggregate has exact authoritative `M` and matching `V`. Its
separately approved binding covers:

- the exact apply aggregate and `M` and `V` digests;
- the exact encrypted rollback source, restore payload, conversion, and digest
  binding;
- the expected current apply postimage and generation;
- the selected and preserved cohorts;
- grant evidence and existing retry, reconciliation, cohort, and budget
  ceilings; and
- the rollback grant, plan, approval, authorization receipt, shared expiry,
  database, and publication epoch.

That successor-predecessor rule is unchanged. For predecessor variant
`LEGACY_COMPLETE_APPLY`, rollback may create `J` only from the exact admissible
legacy state defined above. Its separately approved binding additionally covers
the predecessor variant; authenticated manifest entry and complete legacy-chain
digests; exact frozen-reader version; target database, cutover generation and
explicit genesis; deterministic current legacy postimage; and exact encrypted
historical source, ciphertext, decryption, `LegacyRestoreContent/v1`,
`RestorePayloadConversion/v1`, `TargetRestorePayload/v1`, and digest bindings.
Those values must
already form the exact immutable nonauthorizing binding referenced by the plan.
The rollback `J` atomically adopts that binding and its verified protected
PostgreSQL ciphertext row into journal-owned state. No compatibility scan,
cutover manifest creation, or receipt-incomplete
closure stores a legacy candidate; candidate creation belongs only to pre-plan
preparation for this exact rollback and creates no predecessor `M` or `V`.

Rollback `M` uses serializable compare-and-swap semantics to restore the exact
selected payload content once and advance generation once. It reconstructs
the locked before `TargetMutationImage/v1`, substitutes only the selected
cohort from `TargetRestorePayload/v1`, preserves the locked preserved cohort,
and derives the new-generation after image. It preserves grants, prior
publication evidence, failed and completed rows, and every out-of-cohort row.
Intervening drift refuses the transaction; rollback never overwrites the drift
or expands its cohort. A committed rollback `M` is never repeated and reaches
terminal completion only through its own matching `V`.

A successor apply `M` without matching `V`, an apply verification mismatch,
unavailable preimage, or unexpected current postimage cannot authorize ordinary
rollback. A legacy predecessor that fails any complete-chain, manifest,
generation, genesis, preimage, or current-postimage check likewise cannot
authorize rollback. Those states require diagnosis and, if desired, a
separately approved remediation design.

## Preimage retention and permanent retirement

Before plan issuance, apply preparation stores the exact immutable encrypted
rollback-preimage binding named by the action input, with its target, surface,
canonical lineage key, selected-cohort membership, protected ciphertext,
decryption procedure, source wire body and digest, deterministic
`RestorePayloadConversion/v1`, and exact `TargetRestorePayload/v1`, in the
protected candidate registry with `authority=NONE`. In the same protected
PostgreSQL store, it writes the exact ciphertext octets under the
`ProtectedRollbackCiphertext/v1` digest-and-length key, computes both values
from the stored bytes, and requires equality with that body and its nested
immutable-artifact descriptor before plan issuance.
Apply preparation also creates and verifies the exact immutable,
nonauthorizing `TargetApplyPayload/v1`; its reference and digest are carried in
`ApplyBinding`, and its selected membership must equal the rollback restore
payload's membership. Both content payloads are complete before plan issuance.
`LEGACY_COMPLETE_APPLY` preparation does the same from the complete
frozen-reader-authenticated legacy chain; there is no cutover-time legacy byte
capsule. `SUCCESSOR_APPLY` rollback continues to reuse the predecessor apply's
retained binding and protected byte row.

The matching `J` transaction is the first successor authority boundary. It
atomically creates the exact journal, binding adoption, and protected-byte
adoption, or creates none of them. `J` cannot commit without both adoptions,
and neither adoption may exist without `J`; candidate
registration or plan approval alone never makes the binding authoritative.
Under the PostgreSQL publication architecture, the adopted journal state and
exact retained PostgreSQL bytes are authoritative for rollback-preimage
availability. A private-file copy may be an export or backup, but its presence
or loss cannot change rollback eligibility and it cannot repair a missing,
truncated, or digest-mismatched protected byte row.

The preimage, exact protected ciphertext bytes, and ability to authenticate and
decrypt them remain required until either:

1. the exact rollback aggregate reaches matching `M` and `V`; or
2. a separately approved permanent-retirement action completes and leaves an
   immutable retirement record bound to the exact preimage, protected
   ciphertext body, digest, length, and predecessor aggregate.

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

If the restore source or payload, protected ciphertext body or bytes, digest,
length, conversion recomputation, or required decryption capability is missing
or fails verification
before approved retirement completes, recovery reports rollback unavailable
and fails closed. It never synthesizes a preimage from current target rows or
falls back to a private-file export.

## Preservation and replacement

Every aggregate and committed stage remains immutable and queryable, including
partial prefixes, `LATE`, every closed refusal, ambiguity, fence, advancement,
and `UNPROVEN` recovery observation, failed verification observations,
successful `V`, and retirement evidence. Recovery does not delete superseded
state or rewrite a historical timestamp, digest, result, or status.

A new approval creates a distinct stable key through its typed plan
reference's new body digest or publication epoch as required and binds an
immutable predecessor link. It does not copy an old `R`, reopen an expired
lifetime, reset retry or reconciliation
counts, broaden the cohort, renew budgets, change completed or failed rows, or
turn prior evidence into new authority.

Historical formats keep their original meaning. This successor restart
contract does not backfill `P`, `R`, `M`, or `V` into a legacy chain or promote
a historical file into PostgreSQL authority. The accepted
[journal compatibility design](journal-compatibility-design.md) owns the
complete format, frozen-reader, manifest, and transition contract.

## Observable recovery report

Every read-only status or authority-bearing recovery result reports enough
information to distinguish the product state without interpretation from
ambient artifacts:

- action and stable aggregate key;
- rollback predecessor variant and, for `LEGACY_COMPLETE_APPLY`, authenticated
  manifest-entry and frozen-reader contract digests;
- aggregate and stage-binding digests;
- exact attempted-operation identity, reservation reference and charge,
  `RESERVED`, `STARTED`, or `COMMITTED` state, committed-result identity and
  kind if any, and exact conclusive-noncommit result and closing-resolution
  references when present;
- longest exact durable prefix;
- unresolved stage and ambiguity disposition, if any;
- shared operation-deadline relation and recorded `R` outcome;
- admission generation, publication epoch, and continuity or fence reason;
- authoritative `M` generation and postimage binding, if present;
- canonical mutation-lineage key and generation, expected and observed head,
  and bound predecessor `V` or genesis, interpreted by durable prefix: the
  bound predecessor before `M`, the aggregate's own `M` while verification is
  pending, and no current-head requirement after a terminal outcome;
- predecessor verification state and its permitted evidence-only or
  remediation action;
- verification outcome and evidence identity;
- rollback-preimage availability and retirement state;
- exact permitted next action or terminal refusal reason;
- exact recovery-observation kind, reservation primary key, body digest, and
  `authority=NONE` classification; and
- predecessor or replacement aggregate link.

The report does not include raw retained content or claim that a process is
quiescent merely because its PID is absent. Status remains read-only even when
it reports that automatic recovery would be allowed.

## Acceptance obligations

The accepted
[acceptance-evidence contract](journal-acceptance-evidence.md) from
[#76](https://github.com/nisavid/agents/issues/76) distinguishes, for both apply
and rollback:

- crash before commit, after commit before acknowledgement, and after
  acknowledgement for every `J`, `P`, `R`, `M`, and `V` transition;
- `U` immediately before expiry, exactly at expiry, and after expiry;
- a grant deadline earlier or later than the plan, approval, authorization,
  `J`, or `R` deadline refusing the chain, and a durable timely `R` remaining
  eligible after that shared deadline while each independent live gate is
  checked at current time;
- a timely `R` that commits after expiry, aborts, loses acknowledgement, or
  remains locked beyond the recovery wait bound;
- caller restart with intact adapter continuity from every prefix;
- every enumerated fencing event from every prefix;
- two recovery clients racing at every stage, with one committed stage and one
  target effect;
- a successor aggregate racing predecessor verification, with no later `M`
  before the predecessor reaches matching `V`;
- identical, partially overlapping, merging, and disjoint cohorts deriving the
  same canonical lineage in the initial profile;
- two aggregates bound to one verified head, with exactly one `M` and a
  `LINEAGE_HEAD_DRIFT` refusal that cannot reuse the losing approval;
- a sibling `M` racing the loser's `P` or `R`, with the loser taking the
  transaction-local lineage lock and appending no later pre-`M` authority after
  drift;
- successful `M` advancing the lineage to itself without deriving drift, a
  `MUTATED` empty-terminal aggregate rejecting any other current head as an
  invariant violation, and a historical terminal aggregate replaying after a
  legitimate successor advances the lineage;
- predecessor `MATCH`, repeated retryable `UNABLE_TO_VERIFY`, sticky
  `MISMATCH`, and terminal invariant or target-identity failure yielding,
  respectively, successor admission, bounded
  `PREDECESSOR_VERIFICATION_PENDING`, remediation-only
  `PREDECESSOR_VERIFICATION_MISMATCH`, and remediation-only terminal failure;
- every `M` receipt proving its canonical lineage key, prior lineage
  state—genesis, or head plus predecessor `V`—and atomic successor head;
- admission replacement or revocation racing with `P`, `R`, and `M`;
- ambiguous `M` with eventual commit, eventual abort, and bounded resolution
  timeout;
- fencing after `M` but before `V`;
- `MATCH`, repeated retryable `UNABLE_TO_VERIFY`, sticky `MISMATCH`, terminal
  `INVARIANT_VIOLATION`, terminal `TARGET_IDENTITY_UNPROVEN`, and lost
  verification acknowledgement;
- impossible stage holes and target/receipt splits failing closed;
- generation or cohort drift before `M`, postimage drift after `M`, rollback
  compare-and-swap drift, independent recomputation of every before-image,
  after-image, and compatibility-snapshot digest from its complete
  `TargetMutationImage/v1`, independent `TargetApplyPayload/v1` resolution and
  digest recomputation for apply, and independent source, conversion, and
  `TargetRestorePayload/v1` digest recomputation;
- apply deriving its complete selected postimage only from the immutable
  plan-bound apply payload, with membership equal to the locked before image
  and rollback restore payload;
- rollback interruption at every stage producing exactly one restoration and
  one generation advance;
- `SUCCESSOR_APPLY` rollback retaining the exact predecessor `M` and matching
  `V` requirement;
- `LEGACY_COMPLETE_APPLY` admitting only an authenticated manifest entry,
  complete frozen-reader-verified chain, exact cutover generation and postimage,
  explicit genesis, exact preimage, and new successor rollback approval;
- pending, unknown, corrupt, excluded, incomplete, receipt-closure-only,
  drifted, non-genesis, or preimage-unavailable legacy predecessors refusing
  before `J` and permitting remediation only;
- legacy-predecessor preparation creating and verifying the exact
  `authority=NONE` encrypted-preimage binding and digest-and-length-keyed
  protected PostgreSQL ciphertext before plan issuance, and `J` atomically
  adopting both without cutover byte capsules, approval reuse, or synthetic
  legacy `M` or `V`;
- descriptor-only, missing, truncated, digest- or length-mismatched, private-
  file-only, and explicitly retired rollback ciphertext and preimages;
- reservation-before-start, start-before-effect, effect-before-acknowledgement,
  stale-incarnation replay, distinct-attempt charging, separately charged
  resolution and ambiguity query, same-envelope deadline comparison,
  cross-boot refusal, atomic committed-result binding, conclusive noncommit
  atomically closing the original and resolution before replacement,
  `UNPROVEN` reservation only after that exact original `R` committed and
  terminal result pair exists, rejection of a still-started, empty,
  `R_VALID`, `R_LATE`, or identity-mismatched prerequisite, one result per
  reservation, replacement-`R` eligibility before terminal conditions,
  resolution-crash recursion bounded by the same finite limits,
  request-keyed uncharged preflight refusal, reservation-primary
  recovery-observation identity, exact transaction and reconciliation-subject
  identity, recovered-stage observation-to-result mapping,
  distinct-reservation preservation, bare-observation rejection, and
  byte-identical committed-binding-only free replay;
- multi-reboot qualification with one stable boot configuration and contiguous
  per-boot clock epochs, plus rejection of reused, missing, or cross-bound boot
  identities in runs, live projections, and attestations;
- delayed deployment registration, completion, aggregation, or signing leaving
  the original acquisition age unchanged, and rejection of a missing,
  substituted, cross-boot, or over-age acquisition;
- independent profiler/finalizer equality for the complete typed role-grant
  set and writer inventory, including negative inherited, `PUBLIC`, ownership,
  default-privilege, function-mediated, background, replication, and service
  paths; omitted or extra service identities; broken login-to-underlying-path
  reachability; wrong call-edge tags; and same-digest, different-relation-set or
  out-of-surface paths;
- exact initial-profile socket-directory sequence and endpoint recovery, plus
  relative, dotted, repeated-separator, noncanonical trailing-slash, symlink-
  retargeted, missing, added, removed, reordered, and identity-drift cases;
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

- the accepted [#75 compatibility record](journal-compatibility-design.md):
  historical format, frozen-reader, manifest, and transition compatibility;
- the accepted [#76 evidence record](journal-acceptance-evidence.md):
  falsifiable design, implementation, release, and deployment evidence
  obligations;
- [#77](https://github.com/nisavid/agents/issues/77): independent assessment
  of the integrated design; and
- [#78](https://github.com/nisavid/agents/issues/78): Ivan's final acceptance
  for a separate implementation-planning map.

Only after that acceptance may a separately authorized implementation effort
translate this contract into successor schemas, protected PostgreSQL
interfaces, controller behavior, tests, deployment admission, and migration
sequencing.
