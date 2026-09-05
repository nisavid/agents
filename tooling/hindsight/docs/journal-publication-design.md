# Hindsight Durable Journal Publication Design

Status: publication architecture selected in
[#73](https://github.com/nisavid/agents/issues/73). Ivan approved these product
choices and this architecture record on 2026-09-01. The complete interrupted
publication and restart contract is recorded in
[`journal-restart-design.md`](journal-restart-design.md) through
[#74](https://github.com/nisavid/agents/issues/74). The accepted compatibility
contract is recorded in
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
The deployment attestation binds the target database, proposed reserved
publication epoch, host and storage profile, boot and synchronization epoch,
issue time,
validity interval, admission generation, and typed references to the exact
current `DeploymentAdmissionPolicy/v1`, support profile, accepted
qualification plan, `PASS` qualification receipt, deployment campaign, and
complete exact `PASS` deployment-tier result partition. The receipt must match
the installed release and its exact `EV-CLK`, `EV-PHY`, and `EV-CAP` class
results, plus the complete current `PASS` design-, implementation-, and
release-tier result partitions. The deployment campaign's authenticated plan
basis must name that same exact policy reference.

The attestation also carries typed references to the exact controller-host,
PostgreSQL-host, PostgreSQL-endpoint, and deployment-topology bodies in its
support profile. For `macos-local-postgresql-v1`, the protected live binding
must equal those four bodies: locality is `SAME_HOST_LOCAL`; controller and
PostgreSQL host identities are identical; both host operating-system
references equal the profile's top-level operating-system component; the
PostgreSQL host's PostgreSQL and storage references equal the corresponding
top-level components; both host boot-configuration references equal the stable
configuration nested in the profile's top-level boot component, while the live
projection's actual boot identity equals the attestation and its clock
envelope; the endpoint targets this database and
the PostgreSQL configuration binds the complete effective canonical
Unix-socket-directory sequence. That sequence has exactly one member, whose
configured path, symlink-resolved absolute path, and device/file identity equal
the protected live projection and the endpoint's embedded directory binding.
The endpoint address is derived from that resolved path and ends in
`.s.PGSQL.<configured-port>`, with the separate port field `NONE`; and no
network path exists.
Every TCP endpoint, remote or managed PostgreSQL, hostname or address, proxy,
tunnel, or any host, target, configured or resolved socket path, directory
identity, directory count or order, complete socket address, port, transport,
or topology drift is unsupported and makes the attestation noncurrent.
Reachability alone is not equality.

The protected policy-administration boundary alone compare-and-sets the
current deployment-policy slot keyed by exact target database and surface; its
call carries that key, the expected current reference or `NONE`, and the
replacement reference or `NONE`. A `NONE` replacement clears only that slot.
The admission controller cannot choose it. Attestation issuance, combined activation, and every stage admission
require the attestation's typed policy reference to equal that exact protected
current reference. A policy change or revocation therefore makes every older
attestation noncurrent without editing it. The displaced policy body remains
immutable audit evidence, but its retired slot-reference pair cannot become
current in that slot again. The same policy may remain current for another
covered slot. Those same boundaries require exact equality with every protected
current deployment, design, implementation, release, and qualification-result
pointer. An attestation becomes current only
in the transaction that inserts it and atomically installs its protected
current-attestation pointer after the complete deployment partition passes.
It remains consumable only while all of those current v1 references remain
exact and both the qualification receipt and attestation remain unexpired.

The attestation's `role_grant_set` and `writer_inventory` are typed references
to the closed successor-canonical bodies. The protected profiler enumerates
the complete live PostgreSQL role, attribute, recursive membership, `PUBLIC`,
ownership, ACL, grant-option, default-privilege, mutation-capable routine, and
service writer graph. While holding the catalog and service-registry locks, the
admission finalizer independently repeats that enumeration and canonicalization
and requires byte-identical references. An inherited, assumable,
function-mediated, direct-SQL, scheduled, background, prepared, replication,
or service path that is missing, extra, duplicate, unresolved, unclassifiable,
or unattributed prevents issuance. Every later stage resolves those exact
bodies; compatibility projects its complete role and service fence sets from
them rather than accepting a caller-selected inventory.

Every deciding live deployment projection also names one immutable
`DeploymentEvidenceAcquisition/v1` created by the protected profiler in the
same collection as its qualified acquisition observation. The evidence
registrar verifies and retains that reference but cannot mint or replace it.
The admission finalizer computes the policy's maximum-age check from the
oldest deciding acquisition's conservative monotonic lower bound to the fresh
issuance upper bound under the same clock envelope and actual boot identity.
Registration, completion, aggregation, signing, or retry never refreshes that
age. A missing, reordered, substituted, cross-boot, wrong-envelope, or
over-age acquisition prevents attestation issuance.

A record-wide evidence invalidation, subject change, or campaign supersession
makes an older receipt or attestation noncurrent without rewriting either
body. The attestation's issue time must be strictly below its validity deadline,
and its validity must not outlive the policy or qualification receipt.
The protected attestation finalizer, while holding the exact current policy,
qualification, deployment, clock, and target locks, samples the qualified
clock and constructs the acceptance contract's
`ProtectedTimeObservation/v1`. `issued_at_unix_ns` equals that observation's
conservative trusted upper bound, and the finalizer constructs both bodies in
one transaction. A caller supplies neither value; a future, substituted,
reissued, or cross-target observation is rejected.
Qualification-plan acceptance expiry bounds execution of the qualification
campaign. After every class completes within that bound, the acceptance remains
a historical binding and need not be unexpired when the receipt or deployment
attestation is consumed. Runtime callers cannot supply or alter those facts.

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

A protected admission-state row names the current immutable deployment policy,
deployment attestation, clock envelope, exact four-part deployment binding,
and current active publication epoch. A separate protected row represents a
proposed epoch as `RESERVED_FENCED`; reservation does not change the current
active pointer or admit a stage. Every authority-bearing `J`, `P`, `R`,
or `M` transaction locks that state and all three exact bodies through commit;
revalidates the attestation's complete deployment-tier partition and the
receipt's complete design-, implementation-, and release-tier partitions
against the protected current-result pointers; resolves every tier result's
complete ordered prerequisite-result references and requires each to remain
the protected current `PASS` result; revalidates all four deployment bindings;
rejects an absent, expired, fenced, reserved-only, policy-noncurrent,
evidence-noncurrent, prerequisite-blocked, remote, managed, endpoint-drifted,
or mismatched state at its
decisive observation; and rechecks its required live PostgreSQL settings. `R`
also locks the named clock envelope. Policy, attestation, or clock-envelope
replacement and revocation take a conflicting lock, so they order either before
the stage observation or after the stage commit. Revocation fences the affected
epoch; it never edits historical evidence in place.

Stage admission is a closed conditional union. `FRESH` locks the protected
fixed target-surface fence-binding slot and proves that it has no active
legacy-fence binding under any epoch. Whenever that slot is occupied,
`COMPATIBILITY` carries a typed reference to the exact current
origin-or-adoption per-epoch handoff, resolves its exact epoch-independent
persistent fence evidence, revalidates the continuously closed legacy barrier
under the same lock, and separately proves that binding's
`reserved_publication_epoch`, now active, equals the stage epoch. Omission, an obsolete kind or version, a comparison
only with the proposed epoch, or a caller assertion of absence cannot satisfy
either branch.

Verification after exact `M` is a separate evidence-only boundary. It locks the
exact aggregate, `M`, target rows, canonical mutation-lineage row, and terminal
verification slot and authenticates the target database identity, but it does
not require the old publication epoch, deployment attestation, incarnation
capability, or activation-bound session to remain live. It may therefore run
after expiry or fencing without reviving any mutation authority. Its role
cannot create or consume `R`, execute `M`, alter the target, or activate an
epoch.

Each authority-bearing stage records the exact admission generation and typed
deployment-attestation reference it used. `R` additionally records the exact
typed clock-envelope reference and monotonic sample used to derive `U`.
Verification evidence instead binds the exact `M`, target database identity,
observed generation, cohort, postimage, canonical mutation-lineage key and
head, and stable verification-attempt identity. A later ordinary evidence
expiry does not rewrite a historical fact, while a later revocation fences
the epoch before an unconsumed `R` can reach `M`. This keeps admission current
through `P` and `R` instead of treating a startup check as permanent
authority. The [#76 acceptance-evidence contract](journal-acceptance-evidence.md)
defines the
evidence used to qualify the admission controller, clock model, and each
supported profile; it does not transfer attestation authorship to an ordinary
runtime role.

The support profile also names one exact successor-canonical
`ClosurePolicyLimits/v1`. The qualification plan, receipt, and deployment
attestation carry that same typed reference. Deployment admission proves that
the named PostgreSQL and adapter releases implement every positive finite
attempt, case, reservation, resolution, call, observer-lease, timeout, and
connection-lifetime bound; a changed value, disabling zero, unsupported guard,
or reference mismatch makes the attestation noncurrent and closure unavailable.

Every compatibility projection and successor authority body uses #76's one
exact `TargetGeneration` type. It is a canonical nonnegative safe JSON integer;
negative, quoted, signed, fractional, exponent, leading-zero, overflowing, or
otherwise aliased forms are invalid. Compatibility activation initializes the
protected successor target-generation slot from that exact manifest value,
without converting or resetting it.

The compatibility `TargetDatabaseIdentity/v1` and successor target identity
also share one bijective bridge. The compatibility database name, database
OID, and PostgreSQL system identifier project without normalization into the
successor-canonical `TargetDatabaseIdentityBody`; the successor
`TARGET_DATABASE` reference must contain exactly those canonical JSON bytes.
Activation and every legacy rollback boundary require equality of the full
projection, not a subset or a caller-supplied alias.

The initial profile admits exactly one transaction-owning adapter instance. It
starts mutation-fenced, opens a dedicated database continuity session, and
draws at least 256 bits from the operating system CSPRNG for a fresh
incarnation capability. Persistent cleartext residence is limited to one
locked, nondumpable client allocation owned by that adapter process and one
session-local, non-WAL-logged PostgreSQL relation owned by the activation
session. Activation may create bounded transient copies only inside the
adapter, local Unix-domain transport, PostgreSQL protocol handling, and that
backend process. The qualified profile disables or denies capability exposure
through SQL text, statement or parameter logging, error detail, core dumps,
swap, temporary spill, files, WAL, and backups, and tests those exclusions.
The accepted release-qualification plan has a positive `EV-CAP` cell for each
of those surfaces plus entropy, persistent and transient-copy domains,
controllable zeroization, session-witness installation, reconnect and restart
loss, and the fail-closed `M` predicate. Read-only deployment admission cannot
substitute for that profile-specific qualification and does not inspect a
session witness; no witness exists until combined activation installs it.
The admission controller authors the exact activation proposal but never
receives the capability. The continuity-client adapter consumes that proposal
through a narrow protected function. Combined activation proves that the
proposal digest equals SHA-256 over the exact capability octets; locks and
revalidates the exact current policy and attestation plus the complete current
deployment, design, implementation, and release result partitions; installs
those same octets as the server witness on that session; and writes that digest,
with a fresh publication epoch and session identity, into the durable
admission-state row. A policy, attestation, or result-pointer change refuses
activation. No other protocol participant may receive the capability.

Every `M` executes on that exact activation-bound session. In the mutation
transaction, the protected interface requires the session-local capability,
hashes it, and compares it with the digest in the locked durable admission
state. A different or reconnected session has no witness and cannot satisfy the
predicate, even if client-side loss detection is delayed and the restored row
still says `ACTIVE`. When the PostgreSQL backend session ends, its witness is
unavailable to every later transaction. The adapter zeroizes controllable
transient buffers after each protected call. On its own restart, continuity
loss, database-server restart, endpoint-identity change, or any uncertainty
about continuity, it invalidates the capability, zeroizes its retained
allocation to the extent the qualified platform guarantees before releasing
it, and never reconnects while retaining it. Safety depends on the absent
session witness, not on forensic erasure of former memory. The capability and
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
controller and database share the exact attested host and boot identities and
connect only by the support profile's exact absolute Unix-domain socket. TCP,
including literal loopback, and remote and managed PostgreSQL are unsupported
topologies; similar durability or clock evidence cannot make them equal to the
initial profile.

## Publication aggregate and identity

Apply and rollback are distinct publication aggregates. Each aggregate has a
stable conflict key:

```text
(operation_identity, action_binding.action, plan.body_digest, publication_epoch)
```

`action_binding.action` is exactly `apply` or `rollback`, while the complete
closed union distinguishes the two rollback predecessor variants. The complete canonical `J`
body is the immutable aggregate binding, and `digest(J)` is the aggregate
binding digest. The exact body and digest rules are fixed by the
[acceptance-evidence contract](journal-acceptance-evidence.md). `J` covers
every authority-bearing input, including values resolved under its transaction
locks:

- protocol and journal schema versions;
- operation and action identities;
- typed operation-grant, plan, approval, and authorization-receipt references;
- their one shared operation deadline;
- target database and publication-epoch identities;
- the server-derived canonical mutation-lineage key, expected lineage
  generation and head `M`, and exact predecessor `V`, or the explicit genesis
  state;
- expected generation and exact selected and preserved cohort digests; and
- the exact typed predecessor, preimage, and rollback authority references
  required by the action.

The stable key and binding digest serve different purposes. The same stable
key and the same binding replay the existing aggregate. The same stable key
with a different binding is `CONFLICT`. The typed plan reference and
publication epoch appear in the complete binding, while the plan reference's
body digest and publication epoch define request identity. Changing either
requires a new separately approved aggregate rather than a conflict under the
old key. A content digest alone is not authority: the protected verifier must
resolve each reference's exact current kind, version, complete body, nested
references, and protected authority record. Changed bytes or other bindings
within one request conflict rather than silently creating an unrelated
publication.

The `OperationPlan/v1` carries the same closed action binding as `J`: apply,
`SUCCESSOR_APPLY` rollback, or `LEGACY_COMPLETE_APPLY` rollback, including its
exact predecessor and preimage inputs, the apply variant's exact typed
`TargetApplyPayload/v1`, plus exact typed operation-grant, retry,
reconciliation, and budget-limit references. `J.action_binding` must equal
`body(J.plan).action_binding` byte for byte. The plan, approval, and
authorization receipt form one ordered authority chain with the exact operation
grant `G = body(plan.action_binding.grant)`:

```text
G.issued_at_unix_ns
  < plan.created_at_unix_ns
  < approval.issued_at_unix_ns
  < authorization_receipt.issued_at_unix_ns
  < plan.valid_until_unix_ns

G.valid_until_unix_ns
  = approval.valid_until_unix_ns
  = authorization_receipt.valid_until_unix_ns
  = plan.valid_until_unix_ns
  = J.approval_expiry_unix_ns
  = R.approval_expiry_unix_ns
```

The approval's exact stable key is `plan`; the authorization receipt's is
`(plan, approval)`. Both authenticate the complete canonical plan and therefore
its action binding. An exact retry returns the same body; changed bytes under
the stable key conflict. The grant, plan, approval, and receipt carry exactly
one shared, nonextendable deadline; none can choose an independently earlier or
later expiry.

The operation grant and plan, approval, authorization, and revocation state
exist only behind the acceptance contract's keyed protected interfaces. Their
isolated issuer, approver, authorizer, and revoker principals cannot exercise
one another's transition. The exact grant slot and plan-authority slot must
remain current and unrevoked through every `J`, `P`, `R`, and `M` commit. A
terminal grant or authority revocation orders under those locks, fences further
consumption, preserves every immutable body, and admits no reinstatement.
Restart consumes the plan-bound retry, reconciliation, and budget ceilings; it
cannot infer, renew, or widen them from process state.

Those ceilings are enforced by the acceptance contract's durable plan-scoped
`OperationAccountingState`, immutable `OperationWorkRequest/v1` and
`OperationWorkReservation/v1`, and one-to-one `OperationWorkStart/v1`,
`TransactionIdentity/v1`, and `OperationWorkCommittedResult/v1` records. Every
invocation first performs the one uncharged, side-effect-free committed-result
preflight. A byte-identical committed result returns immediately and writes
nothing. Only unresolved work sends the exact same request to the protected
reservation transaction. Under the accounting lock, that transaction either
charges the checked-next counter and complete declared duration and commits one
reservation, or commits one separately request-keyed
`OperationWorkPreReservationRefusal/v1` with no charge or reservation.
Overflow, exhaustion, stale ordinals, clock failure, and request conflict take
that refusal path and perform no work. Abort, timeout, lost acknowledgement,
process restart, and failed reserved work never refund a committed charge.
Each reservation carries its protected reservation-time observation, exact
clock envelope, and actual boot identity; its deadline is comparable only
under that same envelope and boot. Authority-bearing `J`, `P`, `R`, and `M`
also require the current attestation binding. Evidence-only `V` and recovery
may use a fresh current qualified binding after the aggregate attestation is
expired or fenced, but gain no publication authority from it.
The stable reservation key contains the plan and complete canonical
attempted-operation identity. That identity binds the exact forward or recovery
mode, recovery request when present, stage aggregate and predecessor, `M` and
verification-attempt ID, or original transaction and typed reconciliation
subject. The protected start slot advances once from `RESERVED` to `STARTED`
and atomically stores the canonical transaction identity and binds the only
dispatch to that exact reservation and adapter invocation. Every resolution,
ambiguity query, observation, terminal result, and protected recovery-state
comparison resolves and recomputes that same transaction or subject body. A
crash cannot reopen it. Its only terminal transition is the acceptance
contract's atomic `STARTED -> COMMITTED` result binding. A forward invocation
inserts its exact typed result and committed-result binding together. A
recovery stage advancement instead stores the recovery advancement observation
as the reservation's sole committed result; that observation's `result_body`
names the stage inserted in the same transaction. Exact replay terminates at
preflight and creates no recovery observation. A
conclusive-noncommit resolver instead uses its own separately charged
`STARTED` reconciliation and one transaction to insert the original attempt's
typed terminal noncommit result and committed-result binding, close the
original slot, record and bind the resolution observation, and close the
resolution slot. A replacement attempt and a terminal publication-
qualification reconciliation are both inadmissible until that transaction
commits. A replacement then requires a distinct server-derived identity,
ordinal, reservation, and full charge. An `UNPROVEN` reconciliation is also
distinct and charged; it repeats the complete original `R` identity and binds
the already committed original `CONCLUSIVE_NONCOMMIT` result and terminal body
without changing either. A resolution that crashes while `STARTED` must
itself be closed by another separately charged reconciliation within the same
finite ceilings. Only a byte-identical committed-result binding and its
referenced result replay without a new reservation. Every post-reservation recovery
observation's sole primary stable key is its reservation; aggregate, request,
code, stage, and transition fields are validated content, so distinct charged
reservations remain distinct results. Recovery and a new controller
incarnation query these protected rows rather than reconstructing budget from
logs or memory.

When a transaction resolution or ambiguity query instead proves that its
original transaction committed, the resolver transaction inserts exactly one
typed `OperationWorkTransactionResolutionOutcome/v1` or
`OperationWorkAmbiguityQueryOutcome/v1` and the resolver reservation's
`OperationWorkCommittedResult/v1` together. That outcome repeats the exact
original reservation, start, transaction, work-identity digest, and already
committed original result, plus the resolver's own reservation, start,
transaction, and work identity. It does not insert or bind another `J`, `P`,
`R`, `M`, `V`, or recovery observation. The resolver slot becomes
`COMMITTED`; an exact retry returns the same outcome, while any changed
original result, transaction identity, or resolver identity conflicts. A
resolver started for a still-ambiguous resolver uses that resolver's exact
transaction reconciliation subject and reaches the same closed choice:
original-committed outcome or conclusive-noncommit close.

Each authority-bearing later stage stores a stage-binding digest that extends
the aggregate binding with every predecessor-stage digest and the exact
admission generation and typed deployment-attestation reference used by that
transaction. `R` additionally binds the typed clock-envelope reference,
monotonic sample, error terms, and `U`; `M` additionally binds the active
admission state and incarnation-capability digest it consumed. A stage from
another retry, action, database, epoch, approval, journal, attestation, clock,
or incarnation chain cannot be mixed into the aggregate.

The logical relations are a protected canonical mutation-lineage row, an
aggregate row plus immutable `J`, `P`, `R`, and `M` rows, a terminal
verification slot, immutable verification observations, and successful `V`
rows. An exact `MATCH` fills the terminal slot with `V`; an exact `MISMATCH`
fills it with a terminal mismatch observation; and an invariant or unproven
target-identity result fills it with an exact terminal verification failure.
Database constraints permit only one of those outcomes for one aggregate.
Exact SQL names may follow repository
conventions, but the unique keys, foreign keys, append-only rules, role
boundaries, and stage predicates are part of this design.

Every action binding names one exact immutable
`RollbackPreimageBinding/v1` with `authority=NONE`. For apply and
`LEGACY_COMPLETE_APPLY` rollback, the complete binding and recursively
referenced `ProtectedRollbackCiphertext/v1`, decryption, target, surface,
canonical lineage key, retained plaintext, reader when applicable,
restore-payload conversion, selected membership, and payload-digest inputs are
created and verified in a protected
candidate registry before the operation plan is issued. The exact ciphertext
octets are stored in a protected PostgreSQL byte row keyed by their digest and
length; the constructor and plan issuer compute both values from those stored
bytes and require equality with the typed body and its nested
`ImmutableArtifact/v1` descriptor. Approval therefore resolves the complete
action binding and protected bytes; a descriptor, external path, or preimage
discovered after approval cannot repair it. A `SUCCESSOR_APPLY` rollback reuses
the retained binding and byte row already adopted by the predecessor apply
rather than publishing another body or ciphertext.

Before an apply plan is issued, its constructor also creates and verifies the
complete `TargetApplyPayload/v1`. Its digest, target, surface, lineage key, and
selected membership are fixed in `ApplyBinding`; the selected membership must
equal the rollback restore payload's selected membership. The payload is an
immutable nonauthorizing plan input, not a later database read or executable
transformation.

The two `M` image digests and the compatibility manifest snapshot digest each
hash an exact `TargetMutationImage/v1` state body at that body's generation.
The acceptance contract's `TargetSurfaceContract/v1` fixes every relation,
column, key column, PostgreSQL value type, row identity, membership body, and
order. Each image embeds complete selected and preserved cohort projections;
their membership digests have exact standalone preimages. Production and
oracle readers reconstruct all bodies independently from PostgreSQL values.
The retained `TargetRestorePayload/v1` is a separate generation-free content
body containing only the selected cohort projection and its membership. Its
digest is SHA-256 over its complete successor-canonical bytes with one LF. For
successor apply, those bytes are the encrypted plaintext. For legacy rollback,
the encrypted plaintext remains the exact historical no-LF wire value; the
frozen reader emits registered `LegacyRestoreContent/v1`, and the exact
`RestorePayloadConversion/v1` derives the successor payload without treating
historical bytes as successor JSON. The binding has no mutation-image digest.
The protected verifier authenticates ciphertext digest and length, decrypted
source digest and length, exact source typed body, conversion, and payload
digest independently.

An apply action also carries one exact generation-free
`TargetApplyPayload/v1` and its digest in `ApplyBinding`. It has the same
target, surface, lineage key, and selected membership as the restore payload,
but its complete selected projection is the desired apply postimage. It is
immutable and nonauthorizing; the plan and byte-identical `J.action_binding`
make it the sole source of selected values for apply. No query, procedure,
default, or transformation outside that body contributes values to the
mutation.

The matching `J` transaction atomically inserts the journal and a protected
journal-preimage adoption of that exact binding and protected ciphertext row.
The journal, binding adoption, and protected-byte adoption are all durable or
all absent. The pre-plan bodies remain immutable and nonauthorizing; the
binding and exact bytes become authoritative protected journal state only
through the matching `J` and cannot authorize `P`, `R`, or mutation by
themselves. PostgreSQL retains the adopted bytes through matching rollback `M`
and `V` or a separately authorized permanent retirement. A private file is
only a nonauthoritative export or backup; its presence or loss cannot establish
or remove rollback eligibility. A legacy candidate is not created at cutover
and is not a general historical byte capsule; it may be created only while
preparing the exact rollback plan that will require separate approval.

## Canonical mutation lineage

The initial profile maintains exactly one protected mutation-lineage row for
each target datastore mutation surface. PostgreSQL derives its stable key from
the attested target database identity, the canonical protected target relation
set, and the publication protocol family. A caller cannot supply, rename, or
alias the key.

The derivation input is this one closed body:

```text
CanonicalLineageKeyBody := {
  "protocol_family": "hindsight-postgresql-publication",
  "protocol_version": 1,
  "target_database_identity": EvidenceRef,
  "target_surface_digest": Digest
}
```

Its bytes use the acceptance contract's successor canonical JSON with exactly
one trailing LF. `lineage_key_digest` is SHA-256 over exactly those bytes,
including the LF. The target reference is the exact admitted
`TARGET_DATABASE` identity, and the surface digest is the exact canonical live
surface. Deployment attestations, compatibility activation and rollback,
`J`, `P`, `R`, `M`, `V`, mismatch and unable observations, protected lineage
rows, status, and verification all use this same preimage and digest. No
compatibility-canonical target object, key tuple, delimiter encoding,
implementation serialization, or digest without these bytes is an alias.
Every `TargetMutationImage/v1`, rollback-preimage binding, and compatibility
target snapshot carries this exact `lineage_key_digest`; no second lineage
hash or caller-selected lineage preimage is permitted.

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

Before any `J`, `P`, `R`, or `M` stage-specific predicate, the protected stage
gate locks the current deployment-policy and attestation slots. It requires the
stage's attestation reference to equal the protected current attestation, the
attestation's typed policy reference to equal the protected current policy, the
attestation's complete deployment-tier partition to equal the complete current
`PASS` deployment partition, and its qualification receipt's complete design-,
implementation-, and release-tier partitions to equal their protected current
`PASS` partitions. It resolves and locks every result's complete ordered
prerequisite-result references and requires each to be the exact protected
current `PASS` result. It resolves and locks the support profile's four exact
deployment bindings and requires byte equality with the attestation and live
deployment; it also requires both nested operating-system references, the
nested PostgreSQL and storage references, and both stable boot-configuration
references to equal the profile's corresponding top-level components. The
live projection's actual boot identity must equal the attestation and its clock
envelope. The PostgreSQL configuration,
endpoint, and live projection must also share the complete canonical
socket-directory sequence, its sole initial-profile member and resolved
identity, and the one pathname derived from that member and the configured
port. The gate also requires the qualification plan, receipt, attestation, and
live deployment to share the exact qualified closure-policy reference. It
resolves the attestation's exact `RoleGrantSet/v1`, `WriterInventory/v1`, and
complete ordered deployment-acquisition preimages; requires their target,
surface, role, service, projection, procedure, clock, and boot bindings to
remain exact; and rejects any missing, extra, duplicate, unresolved,
unclassifiable, or differently projected writer or grant path. It
holds every pointer through stage commit. `OPEN`, `FAIL`,
`STALE`, `PREREQUISITE_BLOCKED`, superseded, invalidated, omitted, extra,
duplicated, reordered, policy-noncurrent, remote, managed, or endpoint-drifted
evidence refuses and fences the stage before it can create or consume
authority.

For `J`, `P`, `R`, and `M`, that gate also resolves and authenticates the exact
current, unrevoked operation grant, plan, approval, and authorization receipt;
verifies the plan's exact retry, reconciliation, and budget-limit bodies; and
requires all four authority records to carry the one exact deadline above.
`J` and `P` each take a fresh
protected qualified-clock sample under those locks immediately before the
transaction starts the stage write that may become visible. The resulting
nonauthorizing `PreStageExpiryObservation/v1` uses checked UInt128 arithmetic
and upward rounding to derive `U`. `CURRENT` requires `U` strictly below the
shared expiry; equality is late. Only `CURRENT` may be stored atomically with
the stage, while `LATE` stores the observation and no stage. The profile claims
no macOS scheduler, transaction-duration, commit, WAL-flush, or acknowledgement
bound after that start decision. A committed `J` or `P` remains visible even if
commit or acknowledgement occurs after expiry. Post-commit verification and
revocation fence later gates but cannot undo that visibility. An invalid clock,
continuity, policy, prerequisite, deployment, authority, or arithmetic gate
stores neither. The grant, plan, approval, authorization, and limit slots remain
locked, current, and unrevoked through synchronous stage commit. `R` makes the
one durable post-proof expiry decision below; `M` consumes that decision and
does not resample or compare current time with the shared authority deadline.

### 1. Journal transaction (`J`)

Before opening the transaction, the controller constructs only the complete
non-clock journal inputs: the exact plan-bound operation, action, target,
surface, epoch, expected generation, cohorts, authority references, and
preimage reference. It does not construct canonical `J`, choose its lineage
projection, or populate `pre_stage_expiry_observation`. It resolves the exact
immutable pre-plan `RollbackPreimageBinding/v1` named by the action and verifies
its protected ciphertext body's exact PostgreSQL bytes by digest and length.
For apply it revalidates that candidate's encrypted preimage; for a
`LEGACY_COMPLETE_APPLY` rollback it also reverifies the manifest-bound legacy
ciphertext through the frozen reader; and for `SUCCESSOR_APPLY` rollback it
requires the predecessor apply's retained binding and byte adoption. The
journal has no claimed durable timestamp.

The operation plan's exact action union, predecessor references, target,
surface, epoch, generation, cohorts, operation identity, and expiry must equal
`J`'s complete binding. The exact operation approval and authorization receipt
must point to that plan and to each other as specified above. A digest-only,
transitively different, or later-deadline authority chain is invalid.

The protected publication interface locks the canonical mutation-lineage row.
It requires explicit genesis or an exact head `M` with matching terminal `V`,
then binds that lineage generation, head, and `V` into the pending journal
inputs. While that lock and every authority, admission, and qualified-clock
lock remain held, it derives and stores the exact protected expiry observation.
Only after that observation reference exists does the interface finalize the
closed `J` object, canonicalize it, and compute `digest(J)`. It creates the
aggregate, stores those exact canonical bytes, and atomically adopts the
action binding's exact preimage reference and protected ciphertext row into
journal-owned state in the same transaction, or returns the byte-identical
existing rows. Both adoptions are required for every action variant; a
different binding or byte row under the stable key returns `CONFLICT`.
Concurrent creators converge through the database unique constraint; they do
not infer absence while another creator is still committing.

The same transaction derives and stores `J`'s exact
`PreStageExpiryObservation/v1` with `stage=J` and
`stage_predecessor_digest=NONE`. Its stable key is
`(J, plan, approval, authorization_receipt, admission.publication_epoch, NONE,
observation_request_id)`, where `J` is the literal stage discriminator and
`NONE` is the literal predecessor marker. Same-key same-body replay returns the exact
nonauthorizing observation and, if committed, `J`; same-key changed bytes are
`CONFLICT`. A `LATE` observation, including equality at the shared expiry,
does not create an aggregate or `J`.

The transaction sets `synchronous_commit=on` and verifies the admitted
durability profile. Its success means the local server acknowledged WAL flush
for `J`. A lost client acknowledgement remains ambiguous and is resolved by an
exact query through the same protected interface.

### 2. Durable-proof transaction (`P`)

`P` starts only after `J` commit acknowledgement or exact recovery of `J` from
the protected synchronous path. It reads the exact aggregate and journal,
locks the canonical mutation-lineage row, requires the exact lineage state
bound by `J`—genesis, or generation, head, and predecessor `V`—and inserts an
immutable proof bound to their identities and digests. Lineage drift refuses
`P` without changing the durable prefix.

The `P` transaction derives and stores another exact
`PreStageExpiryObservation/v1` with `stage=P` and
`stage_predecessor_digest=digest(J)`. It authenticates the same grant, plan,
approval, authorization receipt, action binding, and shared expiry as `J`; its stable key
is `(P, plan, approval, authorization_receipt,
admission.publication_epoch, digest(J),
observation_request_id)`, with literal stage discriminator `P`.
Only `CURRENT` may start and commit the protected `P` write; a sampled `U`
equal to or greater than the shared expiry stores `LATE` and no proof. Exact
replay and conflict rules are identical to `J`; no later scheduling or
acknowledgement delay changes the already protected start decision.

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
host. It requires the exact lineage state bound by `J`—genesis, or generation,
head, and predecessor `V`; drift refuses `R` before any authority-bearing
sample. After the proof observation, while the transaction retains those locks,
the adapter samples the qualified host monotonic clock and submits that sample
to the protected finalization function. The interface uses the checked
integer-nanosecond representation, conservative bounds, upward rounding, and
overflow rejection fixed by the
[acceptance-evidence contract](journal-acceptance-evidence.md):

```text
elapsed_upper_ns =
    monotonic_sample_upper_ns - monotonic_anchor_lower_ns
forward_rate_error_upper_ns = ceil_mul_div(
    elapsed_upper_ns,
    forward_rate_error_numerator,
    forward_rate_error_denominator
)
trusted_upper_bound_unix_ns =
    wall_upper_at_anchor_unix_ns
    + elapsed_upper_ns
    + forward_rate_error_upper_ns
```

The protected function requires the envelope to remain current in the locked
admission state, the host and boot identities to match, and the monotonic sample
to be no earlier than the anchor and strictly earlier than the monotonic
validity deadline. `U` is the exact `trusted_upper_bound_unix_ns` derived above.
Wall-clock rollback cannot reduce it; wall time may be recorded as diagnostic
evidence but never enters the authority calculation. Monotonic-clock
rollback or reset, a boot or host change, an invalidated forward-error model,
stale evidence, suspend behavior outside the qualified clock contract, or
insufficient expiry headroom prevents `VALID` and fences the epoch.

The protected interface recomputes the bindings and records exactly one
immutable result:

- `VALID` when `U < approval_expiry`;
- `LATE` when the trusted upper bound is at or after expiry; or
- no authorizing receipt when the time or deployment evidence is unproven.

The function also resolves the exact grant, plan, approval, and authorization
receipt named transitively by `J`, authenticates their stable-key records and
action binding, requires all four validity fields and
`R.approval_expiry_unix_ns` to equal the plan expiry, and locks them through
its decisive sample. A replacement, revocation, mismatched deadline, or
independently earlier or later expiry cannot make `R` valid. The same shared
expiry is the right operand in `U < approval_expiry`.

`R` commits with `synchronous_commit=on`. It may commit after the shared
operation deadline or the envelope's monotonic validity deadline because the
immutable receipt records the already-established post-proof sample and
conservative bound. If the
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
   against the digest bound by the locked admission state; the
   `M.incarnation_capability_digest` field, locked state digest, proposal
   digest, and SHA-256 over the exact witness octets must all be equal under
   the normative #76 predicates;
4. recomputes every aggregate and stage binding, including the exact typed
   deployment-attestation and clock-envelope references, and verifies from the
   immutable `R` bytes that its recorded protected `U` satisfied the strict
   `U < approval_expiry` predicate; it does not compare current time with that
   expiry again or manufacture a renewed sample;
5. requires the current lineage generation and head to equal the values bound
   by `J` and, for a non-genesis head, requires the exact bound predecessor `V`;
6. checks the expected target generation and exact selected and preserved
   memberships, resolves the adopted `ProtectedRollbackCiphertext/v1`, and
   verifies the exact retained PostgreSQL bytes by digest and length; decrypts
   and authenticates the source plaintext, resolves its exact source typed
   body, applies the bound deterministic conversion, and independently
   reconstructs the generation-free `TargetRestorePayload/v1`; reconstructs
   the closed before `TargetMutationImage/v1` from the locked target and the
   after image at the checked next generation; and successor-canonicalizes each
   state body with one LF and recomputes both SHA-256 digests; for apply, it
   resolves the exact plan-bound `TargetApplyPayload/v1`, requires the before,
   apply-payload, and restore-payload selected memberships to match, substitutes
   only the apply-payload selected values, and requires the after selected
   projection to equal that payload; for rollback, the before image's selected
   membership must equal the restore-payload membership and the after selected
   projection must equal the restore-payload projection; both variants require
   the after preserved projection to equal the before preserved projection byte
   for byte;
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

The timely `VALID R` crystallizes the shared operation-authority expiry
predicate. At `M`, the operation grant, plan, approval, and authorization
receipt must still have the exact identities, current selectors, and
nonrevoked states under lock, but `M` does not resample or compare current time
with their shared deadline. The independently timed deployment policy and
attestation, evidence, clock, capability, identity, and epoch gates still
perform their ordinary current-time expiry checks. The active epoch,
continuity capability, legacy fence when present, target generation, cohort,
protected ciphertext, restore payload and conversion, and lineage must also remain exact and
current under lock. Failure of any of those live gates refuses mutation. An
otherwise valid `M` may commit after the shared operation deadline without
turning `R` late.

The interface derives both image digests from the exact locked
`TargetMutationImage/v1` bodies; a caller cannot supply either body, digest, or
equality. It separately derives the restore payload and conversion. A rollback
that restores selected content other than the plan-bound payload, changes a
preserved row, or uses any generation other than checked
`pre_target_generation + 1` rolls back before target, receipt, generation, or
lineage change. An apply rolls back on the same boundary when the captured
before-image selected membership differs from either the apply-payload or
restore-payload membership, when it changes a preserved row, or when its after
selected projection differs from the apply payload. The captured before
selected values need not equal the desired apply values.

The timestamp describes when the logical mutation was performed. It is not
described as PostgreSQL's commit time or durable-completion time.

`M` commits with `synchronous_commit=on`. A failure before commit changes
neither target state nor the mutation receipt. A lost commit acknowledgement
is resolved by reading the exact receipt, generation, and postimage. An exact
retry cannot apply the mutation twice.

### 5. Verification transaction (`V`)

Verification reads the authoritative `M`, independently reconstructs and
hashes the expected `TargetMutationImage/v1` postimage, and compares the exact
live generation, cohort, lineage key, and postimage while
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
  observation and permanently forbids `V`;
- `TERMINAL_FAILURE` fills the previously empty terminal slot with an exact
  `INVARIANT_VIOLATION` or `TARGET_IDENTITY_UNPROVEN` body and permanently
  forbids `V` and mismatch; or
- `UNABLE_TO_VERIFY` applies only to the acceptance contract's closed retryable
  failure set, appends a nonterminal observation, and leaves the terminal slot
  empty for a later exact attempt.

The first conclusive outcome committed under the aggregate and target-lineage
locks is terminal. Concurrent attempts cannot commit more than one of `V`,
mismatch, and terminal failure. A same-attempt replay returns its exact
observation; a later attempt after a terminal outcome returns that outcome
without rereading it as a new decision.
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
mutation. `UNPROVEN` becomes terminal only after the original `R` has first
been atomically closed by the sole conclusive-noncommit mechanism, its exact
committed-result and terminal-result bodies already exist, and no durable
`R_VALID` or `R_LATE` result exists. The later `UNPROVEN` transaction closes
only its own reservation. Read-only status may report the ambiguity; it must
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
- an exact durable `VALID R` may proceed to `M` after expiry without another
  expiry sample; the grant, plan, approval, and authorization receipt are
  checked only for exact identity, current selector, and nonrevocation, while
  independently timed deployment policy and attestation, evidence, clock,
  capability, identity, and epoch gates retain their current-time checks. All
  other live gates must remain exact and current, including
  target/cohort/preimage state, any legacy fence, and the lineage state bound
  by its aggregate—genesis, or head plus predecessor `V`;
- `LATE` and `UNPROVEN` never admit `M`; a request-scoped `CONFLICT` cannot
  advance its mismatched request but does not freeze the correctly bound
  aggregate; and
- `MUTATED` may only exact-replay or proceed to verification.

After `J`, status, handoff, descendant planning, and recovery use these database
states. They do not fall back to files, legacy pending markers, or process-local
observations. The separately approved `LEGACY_COMPLETE_APPLY` admission is the
only pre-`J` exception: the frozen compatibility reader authenticates the exact
manifest-bound legacy chain and preimage, then `J` makes the protected
PostgreSQL binding authoritative for all later stages.

## Rollback

Rollback uses its own stable key, action, approval, journal, proof, receipt,
mutation, and verification chain. Its predecessor variant is exactly one of:

- `SUCCESSOR_APPLY`, which retains the existing rule and binds the authoritative
  predecessor apply `M` and matching `V`; or
- `LEGACY_COMPLETE_APPLY`, which binds an exact entry in the authenticated
  cutover manifest, a complete chain authenticated by the frozen legacy reader,
  the exact encrypted legacy preimage, and the explicit cutover genesis.

`LEGACY_COMPLETE_APPLY` is admitted only while the target database identity,
generation, complete selected and preserved cohort, and deterministic legacy
postimage still equal the manifest-bound cutover state and the canonical lineage
remains at that explicit genesis. The new successor rollback plan and approval
bind the predecessor variant, manifest and chain digests, preimage and postimage,
target and genesis identities, grant evidence, and existing retry,
reconciliation, cohort, and budget ceilings. A legacy apply or rollback
approval, or a successor apply approval, cannot authorize it. A missing,
corrupt, unknown, excluded, incomplete, or drifted legacy predecessor requires
separately approved remediation.

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

The admission controller authors the single-use epoch-activation proposal. For
every fenced start it verifies the current deployment through an operator- or
orchestrator-owned restoration and promotion signal and authorizes the fenced
adapter to generate a new in-memory incarnation capability. The
continuity-client adapter is the sole caller of the separately authorized
combined activation transaction on that exact session. It consumes the
proposal, installs the session-local witness, and records a fresh publication
epoch and only the capability digest before mutation traffic is admitted. The
admission role never calls that transaction or receives the capability; its
only compatibility-fence mutation is the metadata-only `ADOPT_ACTIVE_FENCE`
compare-and-swap for an unactivated proposed epoch. The signal coordinates
activation; durable mutation authority still resides only in PostgreSQL. A
deployment that cannot provide the required signal remains fenced.

The deployment-attestation finalizer performs one atomic
reservation-plus-attestation transition. It locks the epoch high-water mark,
the target-surface current-reserved-activation selector, the current
deployment policy and qualification receipt, and the current attestation
slot and the fixed legacy-fence slot; allocates the next unused numeric epoch; inserts its protected row in
`RESERVED_FENCED`; constructs and inserts the deployment attestation whose
`proposed_publication_epoch` names that row; and installs both the current
attestation and current-reserved-activation selectors. The row immutably binds
the target database and surface, exact predecessor current-active epoch or
`NONE`, continuity session, capability digest, deployment attestation, support
profile, all four host/endpoint/topology references, and target-generation
slot. For an occupied legacy-fence slot, it also resolves the exact
epoch-independent persistent fence evidence and atomically constructs and
selects the compatibility contract's per-epoch adoption handoff for this new
attestation and reserved epoch. It repeats no fence effect and exposes none of
the attestation, row, selectors, or handoff without all the others. A proposal
may name only that exact selected row, attestation, and handoff.
Reservation does not install the active pointer, create genesis, or admit any
publication stage. An unselected, `ABANDONED_FENCED`, already active,
previously activated, reused, renumbered, or differently bound epoch is
invalid.

The initial compatibility cutover specializes that same activation boundary.
Its approved manifest binds one fenced epoch-activation proposal containing the
reserved epoch, deployment attestation, capability digest, and one proposed
legacy-writer fence whose services and database roles are proven mechanically
partitioned to the target surface. A shared cross-surface writer blocks v1
cutover. Before any external fence step, a read-only pre-fence gate
authenticates the exact manifest, approvals, target, proposal, and bound
deployment attestation; locks and revalidates its exact current policy,
complete deployment partition, and qualification receipt's complete design,
implementation, and release partitions; rejects any expired, revoked,
replaced, policy-noncurrent, evidence-noncurrent, or scope-drifted attestation;
and requires a conservative `U_prefence` strictly below its expiry and every
other applicable validity bound. Only
its exact short-lived, single-use invocation may reach the trusted fence
adapter. The adapter's first effect is one synchronous PostgreSQL transaction
that creates the protected pending fence generation and atomically removes
login, connection, and write admission for the complete target-partitioned role
set. Invocation consumption takes an exclusive adapter-local effect-attempt
lock that remains held through that transaction. Before changing admission and
again immediately before commit, the transaction locks and revalidates the
exact consumed invocation, qualified clock envelope, deployment attestation,
current policy and all four current result partitions, proposal, target
partition, and validity bounds, with fresh conservative
`U_fence_start` and `U_fence_commit` checks bounded through the finite
transaction duration. Expiry, revocation, replacement, drift, or clock
uncertainty rolls the whole transaction back without a fence effect; that abort
still spends the invocation, so a retry requires a new pre-fence issue. The
pending row binds the consumed invocation digest, and lost commit
acknowledgement resolves only from that row plus the exact realized admission
and ACL state.
That reconnect barrier does not claim already-authorized work is stopped.
Before every later external effect—the observation or cancellation of one
attributed session, statement, transaction, prepared transaction, replication
path, or background writer, and the disablement of one exact service—the
adapter must pass a fresh fail-closed gate. The gate authenticates and locks the
current fence generation, manifest binding, proposal, deployment policy and
attestation, all four current result partitions, qualified clock envelope,
admission and ACL state, and the exact step record; samples the qualified clock;
and proves its conservative upper bound remains strictly below every validity
and finite operation-time bound through the effect. Those locks remain held
until the effect outcome is durably recorded. No checked authority may be
carried from one external effect to the next, and v1 defines no continuation
receipt. Only exact zero-live-writer evidence produced under those gates and
unchanged ACLs establishes the database-side barrier. With that barrier held,
the adapter disables each exact service under the same per-effect rule,
durably compare-and-swap records each attestation, and advances the generation
to active only after revalidating the drain, services, and ACLs. Lost
acknowledgement at any step resolves from the pending row plus exact live state;
an abort before the access-revocation commit changes nothing, a crash before
drain completion creates no successor authority and requires a fresh target
observation, and any crash after the drain leaves the database write barrier
held. If a manifest drifts after this row reaches `FENCE_ACTIVE`, a fresh
approved manifest uses the compatibility design's protected
`ADOPT_ACTIVE_FENCE` compare-and-swap rather than the origin fence invocation.
That synchronous metadata-only transaction locks the occupied fence and current
manifest binding, resolves and recomputes the exact epoch-independent
`PersistentLegacyFenceEvidence`, and revalidates the exact proposal,
generation, admission, ACL, drain, service, attestation, policy, all four
current result partitions, clock, epoch, manifest, and genesis state. For a
same-epoch refresh it requires no authority for that proposed epoch; for a
later epoch it is part of the atomic deployment-attestation finalizer described
above. It constructs the compatibility design's exact canonical
`ActiveFenceManifestAdoption` body and advances the current binding to that
body digest without changing any service, privilege, target, genesis, or
lineage state. Exact replay returns the same body and digest only while it
remains the current binding; a later adoption makes an old exact retry
`SUPERSEDED_BINDING`. A stale binding, concurrent candidate, broken
origin/adoption digest chain, or any live-state drift conflicts.

Once that active, exactly queryable prerequisite and its current manifest
binding are acknowledged or recovered, the continuity-client adapter alone
calls one combined transaction on the exact continuity session. The
transaction locks the fence; revalidates the
exact held artifact bytes, current origin-or-adopted manifest binding, manifest,
target snapshot, proposal, and capability preimage; locks and revalidates the
exact deployment attestation, its protected current deployment-policy
reference, its complete deployment-tier partition, and its qualification
receipt's complete design-, implementation-, and release-tier partitions;
requires every result reference to equal the protected current `PASS` pointer;
resolves each result's exact ordered prerequisite-result references and
requires those protected pointers to remain current `PASS`; resolves the
support profile's exact controller-host, PostgreSQL-host, endpoint, and
topology bindings and requires equality with the attestation, protected
reserved row, and live same-host local deployment, including the PostgreSQL
configuration's complete canonical socket-directory sequence, sole embedded
endpoint member, resolved directory identity, and derived complete pathname;
then samples the
manifest-bound qualified monotonic clock and derives the conservative
`U_cutover`. It requires the attestation to remain current and unrevoked and
its policy to remain current and unrevoked. `U_cutover` must be strictly below
the policy and attestation expiries and every manifest and approval validity
bound. The transaction installs the session-local witness,
stores the manifest, creates canonical lineage genesis, and makes the reserved
epoch active. In that same commit it locks the reserved row,
current-reserved-activation selector, and active pointer; requires the selector
to name that exact unused `RESERVED_FENCED` row and its predecessor to equal the
active pointer; changes the row once to `ACTIVE`; compare-and-swaps the active
pointer to it; and clears the reserved selector. The protected interface has
no second-activation or active-to-reserved path. The fence and evidence
descriptors remain held through synchronous durable commit. An uncertain abort
leaves the exact row selected in `RESERVED_FENCED`, leaves the prior active
pointer unchanged, and blocks a new reservation until transaction recovery
resolves the outcome. Once recovery conclusively proves noncommit and the
proposal can no longer be admitted, one protected transaction changes the
selected row to permanent `ABANDONED_FENCED`, clears the selector, makes the
attestation noncurrent, and leaves the epoch high-water mark and prior active
pointer unchanged. That epoch is never reused, activated, renumbered, or
reopened. Neither branch creates a partial active epoch, manifest, or genesis
or automatically restores a service or role. No separate cutover transaction
may activate any subset of those three authority facts.

Manifest and exact-exclusion authorization reuse the existing authenticated
operator-approval boundary and its immutable, domain-separated approval
records. The compatibility design fixes their canonical bytes, digest
subjects, authenticated principal and receipt bindings, and protected
cutover-admission verifier. A content digest is not authentication, and these
records add no signing key or separate cryptographic authority.

Compatibility activation also binds the pre-existing protected monotonic
legacy-writer fence generation named by the admission state. Every successor
`J`, `P`, `R`, and `M` locks and revalidates that generation plus live
service-disable, login/connection/write-admission, database-role ACL, and
zero-live-writer drain evidence, including the exact drain-observation
generation, and binds all of their digests into its aggregate or stage
identity. Restored admission, a write regrant, a newly live writer path, or
evidence drift refuses the stage. This record authorizes no writer re-enable.
Any future fence-removal design must first synchronously fence the publication
epoch and advance the fence generation under those same locks before it can
consider returning a service or role. A fence race after `R` thus prevents
`M`, and a crash after that guard leaves successor mutation fenced. Legacy
reactivation and non-genesis successor recutover require a separately accepted
transition design and approval.

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

These are the accepted compatibility constraints on the architecture. The
[journal compatibility design](journal-compatibility-design.md) records the
historical reader, compatibility-body canonical format, manifest, and
transition contract accepted in
[#75](https://github.com/nisavid/agents/issues/75). The distinct successor
stage-body format is fixed by the
[acceptance-evidence contract](journal-acceptance-evidence.md).

Historical schemas, canonical bytes, and readers remain unchanged. In
particular, the legacy pending marker contains only `kind` and
`schema_version`; its presence is never reinterpreted as journal durability,
proof durability, receipt durability, mutation time, or commit time.

Cutover handles each legacy prefix explicitly:

| Legacy state | Successor treatment |
| --- | --- |
| Complete application or rollback chain | Preserve and inspect under its original schema and semantics. |
| Pending marker or journal exists, target mutation absent | Freeze as nonauthorizing. A new successor plan, approval, and authorization receipt are required. |
| Target mutation already applied, receipts incomplete | Permit exact `already_applied` verification and nonauthorizing evidence closure only. The closure is remediation evidence, not a `LEGACY_COMPLETE_APPLY` predecessor; do not perform another state-changing continuation or manufacture successor stages. |
| Rollback of a complete legacy application is desired | Require a new, separately approved `LEGACY_COMPLETE_APPLY` successor rollback publication. The frozen reader, authenticated manifest, exact current cutover state, explicit genesis, and exact encrypted preimage must all reverify before `J`. |

No migration or backfill creates `P`, `R`, `M`, or `V` for historical work.
Legacy files never become a compatibility authority for successor mutation.

## Access and enforcement

The publication schema and protected functions are owned by dedicated
`NOLOGIN`, `NOINHERIT` roles that no runtime principal may assume. Login and
runtime roles receive only narrow protected interfaces:

- the exact `hindsight_journal_evidence_owner` is a `NOLOGIN`, `NOINHERIT`
  registrar/evaluator owner that cannot be assumed. Its callable evidence
  interfaces, the isolated plan-authority, evidence-authority, and
  evidence-producer grants, and every reciprocal denial are fixed by the
  acceptance-evidence access model. Qualification,
  admission, combined-activation-function, and stage-function owners may only
  read the deterministic current result for an exact claim and tier; no caller
  may submit or select a verdict or current pointer, and no login has direct
  evidence-relation access;
- the exact `hindsight_journal_operation_authority_owner` is an unassumable
  `NOLOGIN`, `NOINHERIT` owner. Five mutually isolated nonruntime logins issue
  grants, issue plans, approve plans, authorize operations, and revoke grants
  or operation authority only through the acceptance contract's keyed
  `ISSUE_OPERATION_GRANT`, `ISSUE_OPERATION_PLAN`,
  `APPROVE_OPERATION_PLAN`, `AUTHORIZE_OPERATION`,
  `REVOKE_OPERATION_GRANT`, and `REVOKE_OPERATION_AUTHORITY` transitions.
  Protected stage owners may only read the exact current authority for the
  plan already bound by the request. Every plan binds its exact grant, retry,
  reconciliation, and budget-limit bodies; a stale, mismatched, expired, or
  revoked keyed slot fails closed. No operation-authority identity has a
  stage, target, evidence, policy, admission, activation, closure, fence, or
  runtime interface, and all reciprocal grants are denied;
- controlled private evidence is owned by a separate unassumable private
  evidence owner. Its mutually isolated registrar, reviewer, and public
  exporter logins can respectively register a private package, read and
  register an independent review, or export only the exact current public
  projection and review receipt. They have no direct relation or cross-role
  privilege. Export returns both public bodies or neither after locking and
  revalidating the package mapping, deciding evidence, selected campaign and
  subject, policies, reviewer authorization, and current receipt pointer;
- the qualification-finalizer owner alone can issue canonical qualification
  class results and the final qualification receipt through the two protected
  finalization interfaces. Receipt finalization consumes the complete exact
  current passing design-, implementation-, and release-tier partitions. It
  cannot create or accept a qualification plan,
  run a test, author deployment admission or clock evidence, create or consume
  activation, install a witness, advance a stage, verify `M`, or mutate target
  state. The admission role and every runtime login are denied both
  finalization interfaces, and the qualification finalizer is denied every
  admission, activation, publication, mutation, verification, closure, and
  fence interface;
- the nonruntime qualification-submitter login alone can invoke those two
  finalization interfaces after the evidence registrar accepts the referenced
  campaign results. It has no direct relation privilege and cannot choose a
  verdict, create or accept a plan, run tests, or invoke any admission,
  activation, publication, mutation, verification, closure, or fence
  interface;
- the nonruntime deployment-policy-authority login alone may authenticate and
  compare-and-set one protected current `DeploymentAdmissionPolicy/v1` slot
  keyed by exact target database and target surface. Its call carries that key,
  the expected current reference or `NONE`, and the replacement reference or
  `NONE`; a `NONE` replacement clears only the named slot. It cannot author an
  attestation or clock envelope, finalize
  evidence or qualification, activate an epoch, advance a stage, receive a
  capability, or mutate or verify a target. No admission or runtime identity
  may call its policy-mutation interface;
- the admission-authoring role alone can author or revoke deployment
  attestations and clock envelopes, fence an incarnation, publish an exact
  single-use activation proposal, and invoke the compatibility design's
  metadata-only protected `ADOPT_ACTIVE_FENCE` compare-and-swap before any
  successor authority exists. It cannot consume the activation proposal,
  receive the live capability, create or select the deployment policy, execute
  `M`, or use adoption to change services, privileges, target data, stages,
  lineage, epoch, manifest, or genesis state, and cannot issue a qualification
  class result or receipt;
- the continuity-client login is used only by the dedicated adapter on the
  activation-bound session. It has `NOINHERIT`, no role memberships or
  `SET ROLE` path, no direct relation or sequence privileges, and only the
  exact `CONNECT`, schema `USAGE`, and protected-function `EXECUTE` grants
  needed to consume one activation proposal and later execute `M` on that same
  session. It cannot author or revoke admission evidence, create an activation
  proposal, or invoke publication or verification interfaces;
- the publication role can advance and query `J`, `P`, and `R` and lock and read
  the lineage row only to bind `J` and revalidate that binding at `P` and `R`,
  but cannot update the lineage or mutate target state;
- the mutation-function owner can consume an exact `VALID R` only inside the
  protected `M` function and advance the lineage only atomically with `M`. It
  is a non-login owner, not a runtime identity, and cannot be assumed by the
  continuity client or any other login;
- the evidence-only verification role can append an observation or `V` only
  for the exact immutable `M` and may only lock and read the lineage row, but
  cannot update it or perform any authority-bearing stage;
  and
- no runtime role can update or delete completed stages or bypass the stable
  conflict key.

The trusted PostgreSQL adapter owns connection acquisition, transaction setup,
the single stage call, the immediate pre-commit durability-setting check, and
`COMMIT`; callers cannot wrap a stage in a caller-managed transaction or alter
settings between the check and commit. It is the only protocol participant
permitted to retain the live capability's client copy and the trusted reader of
the qualified host monotonic clock. Activation and every `M` use the same
dedicated PostgreSQL backend session, and `M` atomically verifies that session's
server-side witness. The continuity-client login is a transport principal, not
an admission author or mutation owner: it never switches effective role and
cannot make either protected call succeed without, respectively, the exact
single-use activation proposal or the exact `VALID R` plus session witness.
The activation-consumption and mutation functions are separately owned
`SECURITY DEFINER` functions whose owners are distinct `NOLOGIN`, `NOINHERIT`
roles; each fixes a safe `search_path`, revokes `PUBLIC`, exposes only its
declared operation, and grants the continuity client no underlying privileges.
The adapter cannot route `M` through a pool or replacement connection.
Server-side interfaces recompute canonical digests, the conservative time
bound, and transition predicates. PostgreSQL ACLs, constraints, append-only
relations, the adapter boundary, and the digest chain are sufficient under the
accepted threat model. The design introduces no signing key or separate
cryptographic authority.

## Retention

The initial protocol has no garbage collection. Aggregates and `J`, `P`, `R`,
`M`, and `V` rows remain append-only and are retained unconditionally. Any
backup, retention, archival, or deletion rule requires a separately accepted
design; implementation must not infer a safe collection horizon. The encrypted
rollback preimage remains durably retained until verified rollback or a
separately approved permanent retirement under the
[restart contract](journal-restart-design.md).

## Acceptance evidence

The accepted
[acceptance-evidence contract](journal-acceptance-evidence.md) from
[#76](https://github.com/nisavid/agents/issues/76) makes these outcomes
falsifiable:

- concurrent same-key, same-binding journal creation produces one `J` and
  exact replays;
- changed bytes, approval, expiry, target database, generation, or cohort under
  the same stable key is `CONFLICT`;
- no evidence campaign can start without its authenticated immutable plan
  prebinding every run, claim, exact canonical claim definition and executable
  tier predicate, tool, procedure, limit, oracle, predicate-bound expected
  projection, and stable run identity; every tool, procedure, and generator
  recursively resolves its exact typed input, output, invocation, or step
  contract bodies and recomputes their canonical body digests before use;
  release qualification expands a
  literal `RELEASE` tier, closed case order, seed ordinal and value, allocation
  ordinal, and canonical typed stimulus, while the closed two-member composite
  basis supplies mixed ordinary and `EV-LEG` implementation evidence;
- historical campaign registration rejects any omission, extra, duplicate,
  alias, or reordering in the exact required-category, reader-execution,
  artifact-variant, boundary, generator, seed, budget, shrink, or policy
  coverage projection; the registry includes the exact kindless `requeue-plan`
  dependency member and member count, and each reader-execution binding ties
  the full member selector and pinned source to the immutable executed tool;
  controlled private artifacts are reachable only by the
  isolated registrar and reviewer, and the isolated exporter returns only an
  exact current reviewed public projection and receipt;
- record invalidation removes that record from every bound claim atomically
  and recomputes all affected current results without deleting immutable
  bodies;
- qualification campaigns, run results, class results, and receipts derive
  their stable keys, intervals, issue time, and validity bounds from protected
  registration or qualified-clock observations; unchanged evidence cannot
  renew them, and no producer timestamp can extend them;
- every deciding deployment projection retains the protected acquisition body
  created with the live evidence; delayed registration, completion,
  aggregation, signing, or retry cannot move its lower bound, and the oldest
  acquisition must remain strictly within the policy maximum at issuance;
- the profiler and admission finalizer independently enumerate and
  canonicalize the complete typed role-grant set and writer inventory;
  inherited, `PUBLIC`, ownership, default-privilege, function-mediated,
  background, replication, or service paths that are omitted, extra,
  duplicate, unresolved, or unclassifiable prevent issuance and every later
  fence projection;
- deployment attestation publication, activation, and every stage fail closed
  unless the exact policy and attestation references and all deployment,
  design, implementation, and release `PASS` pointers remain current;
- deployment-attestation issuance takes its issue time only from the fresh
  qualified-clock observation protected by its finalization transaction;
- each reservation binds one exact typed attempted-operation identity, one
  durable `RESERVED -> STARTED` transition, and one atomic
  `STARTED -> COMMITTED` typed-result binding; a distinct attempt or resolution
  consumes a distinct charge, conclusive noncommit atomically closes the
  original and its separately charged resolution before a replacement is
  admissible, and only the exact committed binding replays free;
- reservation and attestation issuance atomically install one selected
  `RESERVED_FENCED` row; activation clears the selector, while conclusive
  noncommit permanently abandons the selected epoch without reuse;
- a changed typed plan reference's body digest or publication epoch cannot
  reuse the old aggregate and requires a new separately approved stable key;
- lost acknowledgements for `J`, `P`, `R`, and `M` recover only the exact
  committed stage;
- a crash after each durable prefix never claims a later transition;
- timely `U` with a lost `R` acknowledgement remains recoverably `VALID`; an
  absent `R` at expiry is first `QUALIFICATION_AMBIGUOUS` and becomes
  `UNPROVEN` only after the existing conclusive-close transaction has already
  committed the exact original `R` reservation's sole
  `CONCLUSIVE_NONCOMMIT` result and the later distinct reconciliation binds
  that result, its terminal body, and the exact original reservation, start,
  transaction, work identity, and digest; campaign or run coordinates cannot
  identify that subject;
- `U == expiry` and `U > expiry` never produce `VALID`;
- the operation grant deadline differs from the plan, approval, authorization
  receipt, `J`, or `R` deadline only by making the chain invalid; after a
  durable timely `R`, crossing that shared deadline does not by itself refuse
  `M`, while expiry of an independent live gate does;
- closure wall horizons reserve `q + ceil(q*n/d)` so separately rounded
  reservation and resolution-rate error cannot consume an extra nanosecond;
  pre-expiry observer invalidation permits only same-ordinal takeover, and
  every abandonment branch requires expiry;
- wall-clock rollback after envelope issuance cannot lower the monotonic-derived
  `U`; monotonic reset or rollback, an invalid forward-error model,
  synchronization evidence outside its qualified lifetime, or host/boot change
  never produces `VALID`;
- a missing or invalid synchronous durability setting prevents authorization;
- an immutable ciphertext descriptor without its exact digest-and-length-
  verified protected PostgreSQL bytes prevents plan issuance or `J`; loss of a
  private export or backup does not affect an intact adopted byte row;
- the initial profile accepts exactly one canonical effective socket-directory
  member and its resolved identity and derived complete pathname; relative,
  dotted, repeated-separator, noncanonical trailing-slash, symlink-retargeted,
  missing, added, removed, reordered, or identity-drifted members refuse
  admission;
- generation, cohort, receipt, or database-binding drift aborts `M` without
  mutation;
- before-image, after-image, and compatibility-snapshot digests independently
  recompute from closed `TargetMutationImage/v1` state bodies at their exact
  generations, while the rollback payload digest independently recomputes from
  generation-free `TargetRestorePayload/v1` content; successor LF plaintext
  and historical no-LF plaintext follow their exact source contracts and the
  latter passes through registered reader output and deterministic conversion;
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
- each `M` receipt proves the prior lineage state—genesis, or head plus consumed
  predecessor `V`—and atomic new lineage head;
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
- apply takes every selected postimage value from its exact plan-bound
  `TargetApplyPayload/v1`; rollback cannot reuse apply authority and restores
  the selected restore payload exactly once; both preserve every locked
  preserved value and advance the generation once;
- `SUCCESSOR_APPLY` rollback continues to require the exact predecessor apply
  `M` and matching `V`, while `LEGACY_COMPLETE_APPLY` admits only a complete
  manifest-bound legacy chain at exact cutover generation and genesis;
- an incomplete-receipt `already_applied` closure, pending marker, corrupt or
  unknown legacy artifact, current-state drift, or unavailable legacy preimage
  never admits rollback;
- a legacy-predecessor rollback's exact encrypted preimage binding is created
  and verified with `authority=NONE` before plan issuance together with its
  digest-and-length-verified protected PostgreSQL ciphertext row, and `J`
  atomically adopts both, with no cutover-time byte capsule and no legacy `M`
  or `V` backfill;
- no legacy apply or rollback approval can authorize a successor rollback;
- every post-reservation recovery refusal, ambiguity, fence, successful advancement, and
  `UNPROVEN` result leaves an exact immutable, queryable, nonauthorizing
  recovery observation keyed primarily and only by its charged reservation;
  distinct reservations never collapse under aggregate, request, code, stage,
  or transition fields, while committed replay is side-effect-free and every
  pre-reservation refusal uses its separate request key;
- every transaction-resolution or ambiguity-query reservation that discovers
  its original committed reaches its own exact typed committed result, bound
  to the original committed result without duplicating the original stage; and
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

- the accepted [#75 compatibility record](journal-compatibility-design.md):
  historical format, reader, manifest, and transition compatibility;
- the accepted [#76 evidence record](journal-acceptance-evidence.md):
  falsifiable design, implementation, release, and deployment evidence
  obligations;
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
