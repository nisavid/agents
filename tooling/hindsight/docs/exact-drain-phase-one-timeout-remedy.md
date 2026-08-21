# Exact-drain Phase 1 timeout remedy

## Scope

This note evaluates the safest response to the active schema-10 exact-drain
run authorized by plan digest
`7ea7af8eba348b85c88356e95c54afbbc45ab9bf18eb389bceaa40f1dd83fd8b`.
The run has four Codex providers in usage-limit cooldown, a productive Hatchery
fallback, retryable timeout evidence, one processing operation, one retrying
operation, and 37 pending operations.

The investigation used only the authenticated payload-free
`operation-recovery drain monitor` output, the sealed plan's non-payload
invariants, launchd process evidence, and repository source, tests, and
documentation. It did not inspect task payloads or raw errors, import the bound
worker runtime, signal the worker, change provider policy, or mutate operation
state.

## Implementation disposition

The referenced run later interrupted with all 39 selected operations failed.
Schema 11 now provides one bounded recovery from that schema 10 plan at epoch
one. Planning requires the authenticated schema 10 recovery plan and its
application and verification receipts, chains the prior retry ledger with the
reference snapshot's preserved-row retry counts, advances epoch one to two,
and caps cumulative attempts at twelve per operation. The resulting schema 11
exact plan cannot authorize another post-abort recovery.

## Conclusion

**Continue the current run without intervention. Do not terminate it, change
provider policy, or start another worker.**

The current evidence is a designed transient-failure state, not a safety
failure:

- The authenticated monitor says both the attempt and worker are `running`,
  launchd still owns the original controller process, the execution lease is
  active, and the latest processing operation has already moved into a fresh
  provider attempt.
- Hatchery has completed 39 calls successfully. Its three recorded timeouts are
  bounded by the exact policy's 1,200-second total-call deadline, and the two
  calls still active at the observation were about 1,033 seconds old.
- Both affected operation records retain committed checkpoint evidence. Their
  failure evidence is `retryable=true`, and neither has reached the closed
  `retry_ceiling` disposition.
- The plan explicitly budgets every remaining operation attempt, bounded retry
  wait, claim and outcome transaction, startup attempt, and shutdown attempt.
  The active lease still has substantially more time than has elapsed.
- Most importantly, this run is already a verified post-abort handoff at
  recovery epoch 1. Schema 10 permits post-abort retry recovery only from epoch
  0 and seals epoch 1 as the ceiling. An elective abort would therefore leave
  no supported second post-abort recovery under the current contract
  (`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py:3392-3408`,
  `tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py:3444-3453`).

The immediate remedy is therefore observation, not mutation. A later candidate
should improve timeout classification and queue/run telemetry, but that work
must not alter this digest-bound attempt.

## Live evidence

The authenticated monitor observation at `2026-08-16T16:08:11Z` reported:

| Surface | Payload-free evidence |
| --- | --- |
| Attempt | `status=running`, `worker_status=running`, worker stage `worker.poller.running`, no worker failure, no worker exit code |
| Ownership | The original one-shot launchd job is `running`; controller PID `68742`; last exit is `never exited` |
| Lease | `active`, 967,712 seconds remaining |
| Selected rows | 1 processing, 1 retrying, 37 pending |
| Codex tier | Four members in `usage_limit` cooldown |
| Hatchery | 46 started, 39 succeeded, 2 failed, 3 timed out, 2 active |
| Active Hatchery ages | Approximately 1,033 seconds each, below the sealed 1,200-second total-call deadline |
| Processing row | Prior retryable timeout evidence and a committed checkpoint; current provider-attempt stage age approximately 12 seconds |
| Retrying row | Retryable timeout evidence and a committed checkpoint |
| Progress | Digest `6f3da2b821ae2270fe6536ba1583f89564f7591370c4f36527bd372acee3f511` |

The monitor is the supported source for this judgment: it authenticates the
application journal, exact process identity, selected task set, progress, and
prior attempts; it exposes provider counters, active request ages, cooldowns,
closed failure evidence, and payload-free checkpoints without prompts,
responses, raw errors, credentials, payloads, or raw worker IDs
(`tooling/hindsight/README.md:392-418`).

The sealed plan independently fixes these bounds:

| Plan invariant | Value |
| --- | ---: |
| Schema / progress schema | 10 / 3 |
| Selected / preserved completed | 39 / 9 |
| Recovery origin / epoch | `post-abort` / 1 |
| Maximum retries / attempts per operation | 3 / 4 |
| Phase 1 statement / client / overall timeout | 120 / 125 / 3,600 seconds |
| Maximum retry delay | 3,600 seconds |
| Effective operation concurrency | 1 |
| Remaining attempts / possible retry waits at planning | 148 / 109 |
| Calculated / maximum execution window | 975,600 / 1,209,600 seconds |
| Window anchor / renewal | authorization receipt / false |

The planner derives the window from the selected rows' persisted retry counts.
For each row it counts `max_retries - retry_count + 1` remaining attempts, then
adds a full Phase 1 bound for every effective-concurrency wave, a full maximum
retry delay for every possible retry, two transaction margins per attempt,
startup margin, and shutdown margin. It rejects a window over 14 days rather
than truncating it
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py:2176-2247`).

## Why the present fallback behavior is expected

The exact-drain authority requires four Codex members followed by Hatchery. It
also fixes the Codex usage-limit cooldown at 300 seconds and Hatchery at a
1,200-second timeout, zero provider-internal retries, and one concurrent
execution
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:344-375`).

Usage-limit reset hints do not exclude a Codex member indefinitely. The runtime
caps the hint to the 300-second cooldown and later permits one probe; other
requests skip that member while it is cooling down
(`tooling/hindsight/lib/hindsight_memory_control_plane/provider_runtime.py:797-823`,
`tooling/hindsight/lib/hindsight_memory_control_plane/provider_runtime.py:969-990`,
`tooling/hindsight/lib/hindsight_memory_control_plane/provider_runtime.py:1052-1115`).
Tests verify both the cooldown skip and the capped reset behavior
(`tooling/hindsight/tests/test_hindsight_memory_provider_runtime.py:900-975`,
`tooling/hindsight/tests/test_hindsight_memory_provider_runtime.py:1055-1078`).

Hatchery's timeout covers the complete member call, including time waiting for
its one concurrency slot
(`tooling/hindsight/lib/hindsight_memory_control_plane/provider_runtime.py:940-967`,
`tooling/hindsight/README.md:242-251`). Consequently, more than one active
Hatchery request in the monitor does not mean the policy is running multiple
model executions concurrently: requests are recorded before entering the
member gate, so one can execute while others wait within their own total
deadline. The tests require the timeout to be a total wall-clock deadline and
confirm that a timeout clears the active-request record and increments the
timeout counter
(`tooling/hindsight/tests/test_hindsight_memory_provider_runtime.py:1155-1235`).

This queue-budget coupling is deterministic, not merely inferred from the live
counter. A throwaway no-network harness using the owning `_ProviderRuntime.call`
seam, a one-second member timeout, one concurrency slot, and two concurrent
fallback requests admitted only the first request into the provider operation;
both calls raised `TimeoutError` within 1.3 seconds because the second call's
deadline elapsed while it waited for the gate. That outcome follows directly
from the total-call timeout wrapping `invoke()`, while `invoke()` acquires the
priority gate before calling the provider
(`tooling/hindsight/lib/hindsight_memory_control_plane/provider_runtime.py:940-967`).
The live sequence is consistent with the same mechanism: three Hatchery
requests began at nearly the same time and later produced exactly three
1,200-second timeout outcomes. This establishes the immediate failure seam,
but it does not establish that every future fallback request will fail; the
same run has already recorded 39 Hatchery successes.

At the operation layer, transport, availability, connection, and timeout
failures consume the plan-bound retry lineage. The third retry seals a
non-retryable terminal `retry_ceiling`; there is no unbounded loop
(`tooling/hindsight/README.md:318-323`). The runtime increments the retry count
and returns an owned row to pending when capacity remains, but changes it to
failed and records `retry_ceiling` once the count is exhausted
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:5634-5759`).
Tests assert both the retryable timeout checkpoint projection and the terminal
retry-ceiling disposition
(`tooling/hindsight/tests/test_hindsight_memory_operation_recovery_runtime.py:930-995`,
`tooling/hindsight/tests/test_hindsight_memory_operation_recovery_runtime.py:1025-1100`).

## Decision gates

### Continue automatically

Continue the existing worker while all of these remain true:

1. `monitor.status` and `worker_status` remain `running`, the execution lease is
   `active`, and launchd retains the original process identity.
2. Provider counters, the progress digest, or the current task stage continue
   to advance within their governing bounds.
3. Every active Hatchery request is still within its 1,200-second total-call
   budget, or a just-crossed request is reconciled by the next observation as a
   success or timeout.
4. Operation failures remain `retryable=true` and no task records
   `retry_ceiling`, `terminal_state_persistence`, or
   `nonquiescent_shutdown`.
5. The lease remains active. Resume and progress never extend its fixed
   deadline (`tooling/hindsight/lib/hindsight_memory_control_plane/CONTEXT.md:35-38`).

Four Codex cooldowns alone are not an escalation condition. Neither is the age
of a pending `retrying` row by itself: a row is claimable only after its
`next_retry_at` and the exact worker has effective operation concurrency 1, so
another processing row can legitimately delay that claim
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:6078-6103`).

### Diagnose without signaling

Preserve the authenticated monitor output and inspect only existing process
evidence before considering any signal when one of these occurs:

- the same progress digest, provider counters, and processing stage persist for
  more than 3,600 seconds;
- an active Hatchery request remains over 1,200 seconds across two monitor
  observations without a matching success or timeout counter change;
- the monitor and launchd disagree about whether the exact process identity is
  live;
- the worker reports a closed startup or task-level non-retryable failure; or
- the execution lease approaches or reaches zero without terminal progress.

The two-observation rule avoids treating a sampling race at an exact timeout
boundary as a stuck provider. The 3,600-second diagnostic horizon is the
plan-bound Phase 1 limit. When Phase 1 itself exceeds that limit, the worker
requests shutdown and raises a closed error; it does not silently continue
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:1500-1530`).

### Stop only for a proven safety condition

Termination is appropriate only after diagnosis proves that continued
execution is unsafe, such as an authenticated process-identity mismatch,
expired mutation authority, nonquiescent task execution, or a worker failure
whose contract requires shutdown. Do not terminate merely to make the fallback
faster or to clear a retryable timeout.

If the attempt becomes terminal, run the supported exact-drain verification and
payload-free classification before unloading the one-shot job. If it becomes
interrupted, preserve its receipts and checkpoints and **do not attempt another
post-abort recovery with the current schema**. The current plan's recovery
context is already epoch 1; schema 10 rejects a post-abort plan whose reference
epoch is not zero
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py:3392-3405`).

## Later bounded repair

The current run does not justify a live policy change. It does expose two
diagnostic improvements for a future candidate and fresh plan.

### 1. Distinguish provider timeout from Phase 1 timeout

The current classifier maps any bounded error containing `TimeoutError` or
`statement timeout` to `phase_one_timeout`, regardless of whether the recorded
failure stage is a provider call, Phase 1 resolution, or a later retain stage
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:743-777`).
That explains why the current payload-free evidence can say
`phase_one_timeout` at `retain.phase2.insert_facts` while provider counters also
record a Hatchery timeout. The retry decision remains safe, but the category is
too broad for diagnosis.

For a later candidate:

1. Wrap the provider member's total-call timeout in a dedicated bounded
   exception that carries no provider response or payload.
2. Add a closed `provider_timeout` category, keeping it retryable at the task
   layer.
3. Reserve `phase_one_timeout` for the exact 3,600-second Phase 1 guard and
   bounded Phase 1 database waits.
4. Add tests for a provider timeout during Phase 2, an actual Phase 1 deadline,
   checkpoint preservation, retry-ceiling behavior, and payload redaction.
5. Revise the closed failure-evidence contract/progress schema as required,
   rebuild and verify the candidate, and create a fresh plan digest. Do not
   retrofit the active plan.

### 2. Separate provider queue age from execution age

The monitor currently records a provider request before the call enters the
concurrency gate, while the member timeout bounds both the gate wait and the
model call
(`tooling/hindsight/lib/hindsight_memory_control_plane/provider_runtime.py:1052-1076`,
`tooling/hindsight/lib/hindsight_memory_control_plane/provider_runtime.py:940-967`).
A future progress contract should expose payload-free `queued` versus
`executing` state and separate ages. That would distinguish a slow model call
from a request whose budget was consumed waiting behind another call.

Schema 11 raises only the Hindsight-side Hatchery execution gate from one to
two. The operation-level drain concurrency remains one, and Hatchery retains a
separate 3,600-second queue deadline, 1,200-second execution deadline, and the
outer 3,600-second attempt deadline. The new gate is exact-drain authority,
bound into the plan and provider-policy digests; it requires a new candidate,
plan, and authorization and cannot affect the active schema-10 worker.

## Recommended path

1. Keep attempt 1 running under the existing 15-minute payload-free monitor.
2. Treat the current timeouts as bounded retries while progress continues.
3. Apply the diagnosis gates above; do not signal on cooldowns or a single
   retryable timeout.
4. On terminal state, perform supported verification and classification, then
   unload the one-shot job and retire the heartbeat.
5. After the active run is settled, implement the diagnostic repair in a new
   candidate. Change provider queue or execution policy only if the terminal
   evidence demonstrates that classification alone is insufficient.

This path preserves the only currently supported recovery lineage, lets the
productive fallback use its sealed retry budget, and defers contract changes to
a fresh approval boundary.

## Validation

Focused primary-source contract tests passed for:

- usage-limit reset hints capped to the probe cooldown;
- provider timeout as a total wall-clock deadline;
- retry-ceiling terminal disposition;
- Phase 1 shutdown waiting for bounded statement cancellation;
- execution-window claim and outcome transaction budgeting;
- closed, recomputed execution-window verification; and
- schema-10 invariant-derived post-abort recovery.

The base `uv` test environment lacked the optional `httpx` dependency needed by
the provider split-timeout fixture. Six tests passed there; the provider
total-wall-clock timeout test was rerun in an ephemeral `uv --with httpx`
environment and passed. The first result was dependency-only, not a behavioral
failure. `git diff --check` also passed, and the repository remained unchanged
except for this uncommitted research note.

## Schema 11 design addendum

### What schema 10 actually bounds

The exact Phase 1 guard does **not** preserve one deadline across Phase 1 LLM
breadcrumbs. It creates the deadline while the holder starts with
`retain.phase1.`, but clears it on every other stage. A transition such as
`retain.phase1.resolve` to `llm.codex.retain.attempt=1/1` therefore discards the
old deadline; returning to `retain.phase1.*` starts a new 3,600-second clock
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:1500-1530`).
The existing stage-projection test proves that this transition is normal,
while the timeout test covers only a holder that remains continuously in one
Phase 1 stage. There is no test for deadline continuity across the transition
(`tooling/hindsight/tests/test_hindsight_memory_operation_recovery_runtime.py:2960-3074`).

There is nevertheless a separate whole-retain backstop in the bound upstream
poller. `_run_executor` wraps the complete task executor in the configured
retain wall timeout and distinguishes that outer timeout from a nested
`TimeoutError`. The bound candidate defaults that wall timeout to 3,600 seconds.
The exact worker's closed environment does not permit the corresponding
override, so the default is effective for this candidate
(`bound candidate lib/hindsight_api/worker/poller.py:54-70`,
`bound candidate lib/hindsight_api/config.py:1273-1280`,
`tooling/hindsight/bin/hindsight-exact-drain-worker:144-198`). The exact
candidate patch also requires the upstream `_wall_timeout_for` seam to exist
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:2960-2976`).

Consequently, schema 10's arithmetic happens to reserve 3,600 seconds per
whole operation attempt, not merely per Phase 1: the execution-window term is
one 3,600-second value per remaining-attempt wave
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery.py:2176-2247`).
That equality is implicit, however. The plan calls the field
`phase_one_timeout_seconds`; neither its closed verifier nor the runtime guard
states that it is also the upstream whole-task ceiling. Schema 11 should make
that authority explicit instead of relying on two independent constants that
currently share a value.

### Exact deadline semantics

Schema 11 should use three monotonic deadlines, all anchored once and never
extended by retries, breadcrumbs, or provider transitions:

1. **Operation-attempt deadline:** `operation_attempt_timeout_seconds = 3600`.
   It starts immediately before the complete claimed task executor and bounds
   every phase, provider wait, provider execution, and cancellation path for
   that one attempt. It replaces the implicit reliance on the upstream default.
2. **Phase 1 deadline:** `phase_one_timeout_seconds = 3600`. It starts on the
   first `retain.phase1.*` observation and remains fixed until an explicit
   Phase 1 completion transition. `llm.*` is a nested breadcrumb, not a Phase 1
   exit, so it must not clear this deadline. The operation-attempt deadline may
   fire first when both values are 3,600; the Phase 1 deadline still preserves
   correct phase authority if a later schema gives the whole attempt a larger
   allowance.
3. **Provider deadlines:** each member call gets
   `provider_queue_timeout_seconds = 3600` from admission until its concurrency
   gate is acquired, then a fresh
   `provider_execution_timeout_seconds = member.timeout_seconds` only while the
   provider operation runs. Every effective deadline is additionally capped by
   the remaining operation-attempt deadline. Queue expiry is
   `provider_queue_timeout`; execution expiry is
   `provider_execution_timeout`; outer expiry is
   `operation_attempt_timeout`. All three are closed, payload-free categories.

The critical rule is that waiting for `_PriorityGate(max_concurrent=2)` must
not consume the member's execution budget. Today the member timeout wraps the
gate wait and operation together
(`tooling/hindsight/lib/hindsight_memory_control_plane/provider_runtime.py:940-967`).
Requests are recorded before that gate, so the monitor's active age likewise
mixes queue and execution time
(`tooling/hindsight/lib/hindsight_memory_control_plane/provider_runtime.py:1052-1076`).
The deterministic two-call harness demonstrated the defect: with a one-second
member timeout and one slot, only the first call entered the provider operation,
yet both calls timed out in under 1.3 seconds. Separate clocks make the queued
call retain its full execution allowance after admission while the outer
3,600-second attempt ceiling still prevents an unbounded queue.

This is safe under intra-operation fanout because every queued call is capped
by the same immutable attempt deadline and the provider gate still admits only
one execution. The upstream extraction path can create all chunk calls
concurrently, so schema 11 must not pretend that the three calls observed in
this run are a universal fanout bound. No fanout multiplier is needed in the
lease formula: the whole-attempt deadline is the authoritative cap regardless
of how many children are queued. At attempt expiry, the worker must cancel the
executor, wait for the existing bounded quiescence path, and retain claim
ownership if quiescence cannot be proved
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:1557-1583`).

### Closed plan and verifier contract

Use plan schema 11 with progress schema 4 and a new failure-evidence contract
digest. The plan must close and verify these fields:

| Surface | Required closed fields |
| --- | --- |
| Attempt authority | `operation_attempt_timeout_seconds=3600`, `phase_one_timeout_seconds=3600`, `phase_one_deadline_anchor=first-phase-one-entry`, `phase_one_nested_stage_prefixes=["llm."]` |
| Provider authority | per-member `queue_timeout_seconds=3600`, `execution_timeout_seconds` (the existing member timeout, including Hatchery's 1,200 seconds), `max_concurrent` |
| Failure evidence | closed categories `provider_queue_timeout`, `provider_execution_timeout`, `operation_attempt_timeout`, and the existing categories; retryability and failure stage remain explicit |
| Progress | payload-free request state `queued` or `executing`, queue-start/acquire/finish timestamps, queue age, execution age, and separate queue/execution timeout counters |
| Execution window | `remaining_attempt_count`, `retry_wait_count`, `effective_concurrency`, `operation_attempt_timeout_seconds`, transaction/retry/startup/shutdown margins, `calculated_seconds`, and `maximum_seconds` |

The schema-11 window formula is:

```text
execution_waves = ceil(remaining_attempt_count / effective_concurrency)
transaction_margin = 2 * remaining_attempt_count * transaction_timeout
shutdown_margin = shutdown_attempt_count * transaction_timeout
calculated_seconds =
    execution_waves * operation_attempt_timeout
    + retry_wait_count * maximum_retry_delay
    + startup_margin
    + transaction_margin
    + shutdown_margin
```

The verifier must recompute every count and term from the sealed selected rows,
require `0 < execution_timeout_seconds <= operation_attempt_timeout_seconds`,
`0 < queue_timeout_seconds <= operation_attempt_timeout_seconds`, and
`0 < phase_one_timeout_seconds <= operation_attempt_timeout_seconds`, and reject
`calculated_seconds > maximum_seconds`. Queue and execution budgets are nested
within the whole-attempt ceiling, so they must **not** be added again to the
execution-window formula. With the recommended 3,600-second whole-attempt value,
schema 11 preserves schema 10's current 975,600-second calculation for this row
set while fixing what that term explicitly means.

The runtime must distinguish which timeout scope fired rather than classifying
any `TimeoutError` as Phase 1. The current classifier collapses both typed and
plain timeout evidence into `phase_one_timeout`
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:743-777`).
Schema 11 should use dedicated bounded exception types, check each timeout
context's `expired()` state, and project only category, retryability, optional
HTTP status, error digest, failure stage, and payload-free checkpoints.

### Compatibility boundary

Schemas 1 through 10 and progress schemas 1 through 3 are immutable historical
contracts. Do not migrate, reinterpret, or resume their plans under schema-11
semantics. Their plan digests, monolithic provider timeouts, failure categories,
execution-window field names, candidate snapshots, and progress journal shapes
must continue through their existing version-specific verifier paths. The
progress writer and verifier currently accept only versions 1, 2, and 3, so
version 4 must be an additive branch rather than a silent expansion of version
3 (`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_progress.py:377-395`,
`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_progress.py:1091-1123`).

Provider-policy compatibility should follow the same rule: legacy policy
schema keeps `timeout_seconds` as the total gate-plus-execution wall clock;
schema 11 binds a new policy schema with mutually required
`queue_timeout_seconds` and `execution_timeout_seconds`. Monitoring may read
both shapes, but archived progress must validate against the plan's original
progress schema. A schema-11 repair therefore requires a rebuilt candidate,
new runtime and failure-contract digests, a fresh verified plan, and new
authorization. It cannot be applied to the active schema-10 worker.

### Required proof before a schema-11 plan

The implementation gate is a deterministic test matrix, not another live
trial: deadline continuity across `retain.phase1.* -> llm.* ->
retain.phase1.*`; two executing and at least one queued provider call under
`max_concurrent=2`; queue timeout without provider entry; a full execution
budget after gate acquisition; outer attempt cancellation and quiescence;
phase-specific closed classification; schema-11 arithmetic recomputation and
tamper rejection; payload-redacted progress; and unchanged golden verification
for plan schemas 1-10 and progress schemas 1-3. Only after those tests and
candidate verification pass should a fresh schema-11 exact-drain plan be
offered for approval.

## Schema 12 interruption and evidence repair

Schema 12 preserves schema 11's split provider queue/execution deadlines and
raises the progress contract to schema 5. It changes the operation-attempt and
Phase 1 timeout disposition from worker fail-stop to task retry after bounded
quiescence. The runtime cancels the child task, waits for the existing bounded
shutdown proof, records a retryable task outcome, and continues the exact
drain. If the child completed before timeout observation, completion wins. A
nonquiescent child still fails closed without releasing ownership.

Provider cancellation is not a provider fault. Progress schema 5 records
`queue_cancelled` and `execution_cancelled` separately, removes the request
from the active set, and leaves provider failure and timeout counters
unchanged. Interrupted monitor projections freeze request ages at the last
authenticated observation and mark surviving active requests stale.

Retry-ceiling evidence preserves the underlying closed cause. A plain upstream
`TimeoutError` is recorded as `upstream_timeout`; a PostgreSQL statement
timeout is `database_statement_timeout`. The terminal database status artifact
uses schema 2 and adds a server-side, payload-free classification projection
containing only `cause_family`, `error_digest`, and `occurrence_count`. The
classifier's cause-family vocabulary is closed and raw exception text never
crosses the monitor boundary.

Schema 12 also represents one final authenticated recovery from epoch two to
epoch three. The post-abort transaction resets only failed exact-worker rows;
unowned pending rows are preserved. The subsequent exact plan selects the full
pending set and binds the epoch-three retry lineage. The cumulative ceiling is
sixteen attempts per operation, and no epoch-four recovery is accepted.
The controller and worker reject `--resume` for a nonterminal schema-11
application journal. Recovery must use the authenticated schema-12
epoch-two-to-three transaction; terminal reconciliation may still close an
already terminal schema-11 run without restarting task execution.

Schemas 1 through 11 and progress schemas 1 through 4 remain immutable
historical contracts. Schema-12 behavior requires a rebuilt candidate, fresh
runtime and contract digests, a new post-abort plan where recovery is needed,
a fresh exact-drain plan, and explicit approval of each mutation plan.
