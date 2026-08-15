# Exact-drain execution-lease expiry: diagnosis and recovery design

## Scope

This report analyzes the interrupted exact-drain run authorized by plan digest
`1969c3eb7a957b53004c4149fa8f6bba9bbbac24b53dc44d7f0957ae5bdfa067`.
The investigation used only payload-free run receipts supplied by the operator
and repository source, tests, and documentation. It did not inspect operation
payloads, mutate the interrupted run, start a worker, or change provider policy.

## Conclusion

The run hit its designed 24-hour mutation-authority boundary. The interruption
was then reported incorrectly because the worker detected the expired lease and
exited before the controller's own timeout path ran. That race converted an
expected lease interruption into a generic exit-2 worker failure, and the
generic worker classifier converted the lease exception into
`worker_initialization`, `retryable=false`. The recorded stage
`worker.memory.ready` is the last lifecycle stage written to the durable
recorder, not evidence that memory initialization failed
(`tooling/hindsight/bin/hindsight-memory:9321-9399`,
`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:1341-1359`,
`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:769-796`,
`tooling/hindsight/bin/hindsight-exact-drain-worker:508-538`).

This is not safely recoverable with the current post-abort schema. Schema 9
accepts exactly one failed, one owned-pending, and one processing retain row,
with exact retry and failure-category tuples; the run's payload-free final shape
has 22 failed, 16 pending, and one retrying operation. A new recovery schema
must express ownership, identity, digest, and state invariants instead of adding
another incident-shaped count tuple
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py:182-191`,
`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py:2818-2921`,
`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py:2977-3018`,
`tooling/hindsight/tests/test_hindsight_memory_operation_recovery.py:2001-2058`).

The best path is therefore: preserve the interrupted receipts; repair the
classification and recovery contracts; perform a newly planned, approved, and
verified invariant-based post-abort recovery; then create a fresh exact-drain
plan with a digest-bound lease sized from the plan's own worst-case work budget.
Do not resume or silently renew the expired authorization.

## 1. Lease contract

### Duration and start time

The current plan contract fixes both approval lifetime and execution lease at
86,400 seconds. It also fixes four worker attempts, three retries, a 120-second
transaction timeout, and a 3,600-second Phase 1 timeout
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py:36-44`).
`create_exact_drain_plan` has no lease-duration parameter and always writes the
constant into schema-9 plans; the verifier rejects any other value
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py:2058-2073`,
`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py:2193-2251`,
`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py:2492-2512`).

The execution lease begins at authorization, not plan creation. Its deadline is
`authorization.authorized_at + plan.execution_lease_seconds`; monitor status is
`active` before that instant and `expired` at or after it
(`tooling/hindsight/bin/hindsight-memory:8376-8420`). The worker and its claim
adapter independently derive the same deadline and fail closed at the boundary
(`tooling/hindsight/bin/hindsight-exact-drain-worker:241-250`,
`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:4534-4550`,
`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:4873-4927`).

### No renewal

There is no renewal mechanism. Apply creates one authorization only when no
authorization exists and installs it create-only; later apply/resume reads that
same receipt rather than replacing `authorized_at`
(`tooling/hindsight/bin/hindsight-memory:9039-9067`). The CLI can append
`--resume` for an existing journal, but it checks the unchanged authorization's
deadline before launching another attempt
(`tooling/hindsight/bin/hindsight-memory:9174-9225`). Tests explicitly require
an expired journal resume to fail before child launch
(`tooling/hindsight/tests/test_hindsight_memory_operation_recovery_cli.py:6958-6975`).

This is a sound authority property: an expired approval cannot silently regain
mutation power. It also means this run needs a fresh plan and authorization
after recovery, not another attempt under the expired digest.

## 2. Why the failure was misclassified

### The deadline race

The controller waits for the child until the lease deadline. Only if its
`process.wait` call raises `TimeoutExpired` does it set
`execution_lease_expired=true`, validate the exact process identity, and send
SIGTERM. If the child exits nonzero just before that timeout, the flag remains
false and the controller raises generic `exact drain worker failed`
(`tooling/hindsight/bin/hindsight-memory:9321-9354`,
`tooling/hindsight/bin/hindsight-memory:9389-9399`). Existing tests cover the
controller-timeout-first path and expired-before-launch path, but not the
worker-self-check-first race
(`tooling/hindsight/tests/test_hindsight_memory_operation_recovery_cli.py:6603-6723`,
`tooling/hindsight/tests/test_hindsight_memory_operation_recovery_cli.py:7017-7159`).

The child can win that race legitimately. Claiming and every claim-capable
mutation assert the adapter deadline; a breach raises the specific
`operation-recovery exact drain execution lease expired` exception
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:4873-4885`,
`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:5943-5969`).
The poller boundary stores that exception and requests graceful shutdown, and
the shutdown boundary re-raises the stored exception after cleanup
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:1646-1678`,
`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:1708-1788`).
The wrapper then seals a worker failure and exits 2
(`tooling/hindsight/bin/hindsight-exact-drain-worker:508-538`).

### Wrong category and stage

`exact_drain_worker_failure_evidence` first reduces an unrecognized exception
to `operation_error`, then rewrites every such worker-level error to
`worker_initialization`. It marks only capacity, transport, and initialization
timeout categories retryable, so lease expiry becomes the observed
`worker_initialization`, `retryable=false`
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:769-796`).
The progress schema does not contain an `execution_lease_expired` category
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_progress.py:45-66`).
A source-level reproduction returns exactly that misclassification and fails an
assertion that the category should be `execution_lease_expired`.

`worker.memory.ready` is likewise a lifecycle-reporting gap. The adapter records
that stage after successful memory initialization, and no later recorder stage
is written when the poller starts. The outer wrapper asks the recorder for its
current stage when sealing exit 2
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:1341-1359`,
`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:1646-1666`,
`tooling/hindsight/bin/hindsight-exact-drain-worker:522-538`). The stage means
"last successfully entered lifecycle stage," not "component at fault."

### Correct classification

Lease expiry should be a first-class closed category,
`execution_lease_expired`, with `retryable=false` for the expired plan. The
overall attempt state should be `interrupted`, not a failed initialization.
`retryable=false` remains appropriate at the same-plan layer because the
authorization no longer permits mutation; a fresh approved plan may still
reattempt the recovered rows. The controller should reconcile nonzero child
exit against authenticated progress and the absolute deadline before choosing
the generic worker-failure branch.

## 3. Recovery and checkpoint guarantees

### What same-plan resume supports

The implementation supports journal resume while the original authorization is
still active, carrying prior attempt evidence into the next attempt. It does
not support resume after the lease deadline
(`tooling/hindsight/bin/hindsight-memory:9174-9225`,
`tooling/hindsight/tests/test_hindsight_memory_operation_recovery_cli.py:6958-6975`).
At graceful shutdown, the adapter has a narrow expired-lease cleanup allowance:
within one transaction-timeout window it may release only processing rows owned
by the exact worker back to pending; it verifies plan membership, bank, type,
payload digest, and retry bounds before doing so
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:4890-4927`,
`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:5402-5477`).

If that cleanup does not leave a fresh-plan-compatible snapshot, the supported
surface is `drain post-abort plan/apply/status/verify`; planning accepts an
expired reference plan but refuses an active reference worker
(`tooling/hindsight/bin/hindsight-memory:9800-9829`,
`tooling/hindsight/bin/hindsight-memory:12695-12758`).

### What work is durable

The worker reads payload-free checkpoints from persisted `result_metadata`:
facts-committed, committed document count, unit count, stage, processed, and
total (`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:155-174`).
The progress recorder validates and digests those fields, and writes each
progress state through a private temporary file, `fsync`, atomic replace, and
directory `fsync`
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_progress.py:133-188`,
`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_progress.py:246-307`,
`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_progress.py:560-567`).
Task terminal-failure tests prove that a committed checkpoint is retained in
the closed outcome evidence
(`tooling/hindsight/tests/test_hindsight_memory_operation_recovery_runtime.py:809-895`).

Post-abort apply resets only selected rows to pending. It leaves unselected rows
digest-exact, preserves each selected row's task-payload digest and
`result_metadata_digest`, and checks the post-state transactionally
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:6849-6933`,
`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:6968-7093`).
Therefore completed rows remain committed, and checkpoints already persisted
in result metadata are not discarded by recovery. The README states the same
operator contract: post-abort preserves every completed checkpoint and selects
only the reference worker's failed, owned-pending, and processing rows
(`tooling/hindsight/README.md:325-327`).

This round was not wholly wasted. Completed operations and their persisted
checkpoints survive. Work that never reached a database checkpoint may need to
be repeated, and failed rows are deliberately reset to retry count zero by
post-abort apply while their result-metadata digest remains unchanged
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:6968-6992`,
`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:7047-7093`).

### Why current post-abort cannot recover this run

Although the transactional mutation is invariant-oriented, plan construction
is incident-shaped. Schema 9 hardcodes selected and preserved count maps and
retry tuples; `_post_abort_contract` rejects any other counts, categories, or
retry distributions
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py:182-191`,
`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py:2900-2921`,
`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py:2977-3018`).
The current unit fixture only demonstrates the exact 1/1/1 shape
(`tooling/hindsight/tests/test_hindsight_memory_operation_recovery.py:2001-2058`).
The observed 22-failed/16-pending/one-retrying shape is therefore a planning
contract mismatch, not evidence that recovery would be unsafe if expressed by
the actual invariants.

## 4. Recommended remedy

### A. Repair recovery before any reattempt

Add a new post-abort schema that derives counts from the authenticated snapshot
and enforces these predicates instead of literal tuples:

1. The reference plan, authorization, application journal, progress digest,
   cohort, installation authority, and generation all authenticate and agree.
   The exact reference process identity is no longer active. The current
   planner already enforces the stopped-worker gate and reference-artifact
   binding (`tooling/hindsight/bin/hindsight-memory:9800-9829`).
2. Select only reference-plan rows whose live state is failed, processing owned
   by the reference worker, or pending with a live reference-worker claim.
   Require exact operation ID, type, row digest, task-payload digest, owner
   digest, claim presence, and bounded retry count. The current selector already
   has the ownership shape, but gates it behind schema-specific branches
   (`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py:2818-2890`).
3. Preserve completed rows, unowned pending rows, and every row outside the
   reference selection digest-exact. Require all current IDs to equal the
   reference cohort and preserve operation type and task-payload digest. Those
   guards already exist in the current contract
   (`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py:3018-3120`).
4. Bind the derived selected-count, status-count, type-count, and row-set digest
   into the new plan for review and approval, but do not compare them to
   constants. The current plan already records these derived fields
   (`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py:3260-3275`).
5. Preserve `result_metadata_digest` for every selected row and the full digest
   for every preserved row, as apply already verifies
   (`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:7034-7093`).

After implementation and tests, generate a fresh payload-free snapshot and
rollback evidence, plan the post-abort transaction, obtain explicit approval of
its new digest, apply once, and run supported status/verify. Only after that
verification should a new exact-drain plan be created.

### B. Size the fresh lease from the plan budget

The current 24-hour lease is shorter than the plan's own conservative work
envelope. A plan allows four attempts and a one-hour Phase 1 timeout per
operation (`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py:36-44`).
The exact worker limits retain concurrency to one
(`tooling/hindsight/bin/hindsight-exact-drain-worker:161-170`). For 42 selected
operations, a conservative serialized Phase 1 envelope alone is
`42 * 4 * 3,600 = 604,800` seconds (seven days), before bounded shutdown,
transaction, retry-delay, and startup margin.

For the reattempt, introduce a new exact-drain schema whose plan binds:

- selected work units and operation-type counts;
- maximum remaining attempts per operation;
- each plan-bound operation timeout;
- effective per-type concurrency;
- bounded startup, retry-delay, transaction, and shutdown margins;
- calculated `execution_lease_seconds` and an absolute allowed maximum.

The verifier should recompute the duration from those fields and reject values
outside a documented minimum/maximum. For this cohort, the first repaired plan
should use at least the conservative seven-day work envelope plus its explicit
bounded margins, not another fixed 24-hour lease. This changes only the new
plan's approved mutation window; it does not change provider policy.

### C. Do not silently renew

The immediate design should remain nonrenewing: a fresh digest and approval are
clearer and safer than extending the expired authorization. If future operations
need renewal, make it an explicit, append-only, authenticated renewal receipt
chained to the plan digest, authorization receipt, prior lease epoch, new
deadline, and a plan-approved absolute maximum horizon. The controller and
worker must consume the same receipt and deadline, and renewal must occur before
expiry. Never overwrite `authorized_at`, renew after expiry, or permit the
renewal chain to expand the maximum mutation authority approved in the plan.

## 5. Required code and test changes

### Classification and lifecycle

- Add `execution_lease_expired` to the closed failure categories and classify
  the specific lease exception before the generic `operation_error` to
  `worker_initialization` rewrite
  (`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_progress.py:45-66`,
  `tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:769-796`).
- Add an operational recorder stage after the control connection is reserved
  and before `upstream_run`, so an in-service failure is not left at
  `worker.memory.ready`
  (`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:1646-1666`).
- In the controller, read and authenticate progress after nonzero child exit.
  If the closed category is `execution_lease_expired`, or the absolute deadline
  has been reached, route through the same interrupted path as controller-side
  `TimeoutExpired`; do not raise generic worker failure first
  (`tooling/hindsight/bin/hindsight-memory:9321-9399`).
- Add a deterministic test for both orderings: controller timeout first and
  worker self-check first. Assert identical `interrupted` state and closed lease
  category, with no raw error text. Existing tests cover only the first ordering
  (`tooling/hindsight/tests/test_hindsight_memory_operation_recovery_cli.py:7017-7159`).
- Add a unit test that passes the exact lease exception to
  `exact_drain_worker_failure_evidence` and expects
  `execution_lease_expired`, `retryable=false`, plus an operational failure
  stage. The current classifier seam is at
  `tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:769-796`.

### Recovery

- Add the invariant-based post-abort schema described above and retain old
  schema verifiers unchanged so historical digests remain verifiable.
- Add fixtures for the observed 22-failed/16-pending/one-retrying shape,
  including any exact-worker-owned processing or pending claim. Assert selection
  is derived from ownership/state predicates, not literal counts.
- Assert completed rows and unowned pending rows stay digest-exact; selected
  rows become unowned pending; task-payload and result-metadata digests remain
  unchanged; no outside row changes; generation advances exactly once. These
  are the apply invariants at
  `tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:6849-7093`.
- Add checkpoint fixtures for completed, failed, retrying, and processing rows,
  and assert the post-abort plan/apply/verify cycle preserves each checkpoint
  digest. The existing schema-9 test covers only its fixed three-row incident
  (`tooling/hindsight/tests/test_hindsight_memory_operation_recovery.py:2001-2058`).

### Lease sizing and authorization

- Add a new exact-drain schema with a recomputable lease-sizing projection;
  leave schemas 1-9 and their fixed 86,400-second verifier unchanged.
- Test minimum, maximum, arithmetic drift, effective concurrency, timeout, and
  margin changes; any change must alter the plan digest and require new
  approval. The existing test only asserts the fixed value
  (`tooling/hindsight/tests/test_hindsight_memory_operation_recovery.py:378-392`).
- Preserve the no-renewal invariant for existing plans and assert an expired
  authorization cannot launch or resume. Existing boundary tests already cover
  those two fail-closed cases
  (`tooling/hindsight/tests/test_hindsight_memory_operation_recovery_cli.py:6603-6723`,
  `tooling/hindsight/tests/test_hindsight_memory_operation_recovery_cli.py:6958-6975`).
- If renewal is later implemented, test create-only append semantics, receipt
  chaining, pre-expiry consumption, absolute-horizon enforcement, controller /
  worker deadline equality, forged/stale renewal rejection, and crash recovery
  between receipt creation and worker observation.

## Decision

Pause the live workflow. Do not run current post-abort schema 9 and do not
reattempt under the expired plan. Implement and verify the narrow recovery,
classification, and lease-schema repairs first. Then recover with a newly
approved invariant-based post-abort plan and reattempt with a freshly approved,
budget-sized, nonrenewing exact-drain plan.
