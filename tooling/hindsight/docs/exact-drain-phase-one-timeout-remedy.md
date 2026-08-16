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

Do not simply increase Hatchery concurrency or its 1,200-second timeout. The
current values are exact-drain authority, not incidental configuration, and a
change would require source-contract changes and a new candidate and plan
(`tooling/hindsight/lib/hindsight_memory_control_plane/operation_recovery_runtime.py:344-375`).
Only revisit those values if terminal evidence shows repeated provider queue
budget exhaustion after Codex probes remain unavailable. If revisited, retain a
separate bounded queue deadline and execution deadline whose combined worst
case remains within the plan's 3,600-second Phase 1 and execution-window math.

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
