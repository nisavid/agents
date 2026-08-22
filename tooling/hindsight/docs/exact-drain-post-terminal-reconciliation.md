# Exact-Drain Post-Terminal Reconciliation

Status: approved design

## Purpose

An exact-drain recovery lineage can reach recovery epoch three with every
selected operation terminal and failed. Schema 12 must not reset those rows a
fourth time: its recovery authority is exhausted, and treating another reset as
epoch four would weaken the contract that made the first three transitions
auditable.

Schema 13 introduces one separate `post-terminal-reconciliation` cycle. The
cycle preserves the schema-12 terminal evidence, reopens the same operation IDs
under new authority, and retains each operation's committed checkpoint. It does
not extend the recovery-epoch ceiling.

## Goals

- Preserve the terminal schema-12 plan, authorization, application, progress,
  and status evidence.
- Reuse operation IDs and result metadata so retain tasks resume from their
  committed checkpoints.
- Permit exactly one reconciliation cycle after recovery epoch three.
- Complete a queued mental-model refresh when its target was deleted before
  execution.
- Block authorization unless Hatchery succeeds through the configured
  inference route.
- Launch one exact worker and retain the existing concurrency and timeout
  limits.

## Non-goals

- Schema 13 does not add recovery epoch four.
- It does not create replacement operations or copy task payloads.
- It does not increase worker concurrency, provider concurrency, or timeouts.
- It does not introduce a shared Hatchery admission service.
- It does not permit a second post-terminal reconciliation cycle.

## Authority

Schema 13 adds recovery-context schema 4 with these identifying values:

- `origin` is `post-terminal-reconciliation`.
- `recovery_epoch` is 3.
- `reconciliation_cycle` is 1.
- The context binds the terminal schema-12 plan digest, authorization receipt
  digest, application receipt digest, progress digest, status digest, selected
  row-set digest, checkpoint-set digest, candidate release digest, and stable
  database generation.

Planning fails unless the reference plan is schema 12, all selected rows are
failed, the reference worker is inactive, no selected row is pending or
processing, and the live row and checkpoint digests match the terminal status.
The planner also rejects a reference plan whose context already originated
from post-terminal reconciliation.

The reference artifacts remain immutable. Schema-13 receipts point to them by
digest and never rewrite them.

## Reconciliation transaction

The approved reconciliation application runs without a worker. It acquires the
same database authority and transaction bounds used by post-abort recovery,
then rechecks the complete source projection.

For every selected row, the transaction:

1. verifies the failed status, row digest, task-payload digest, result-metadata
   digest, checkpoint digest, and retry lineage;
2. preserves the operation ID, operation type, task payload, result metadata,
   creation time, and checkpoint fields;
3. sets the status to pending;
4. clears the worker ID, claim time, completion time, retry time, and terminal
   error fields; and
5. resets the worker retry count for the one reconciliation cycle.

The transaction rejects partial updates. Its application receipt records only
closed counts and digests. Verification rereads every selected row and proves
that all are pending, the preserved fields match, the execution-owned fields
are clear, the generation transition is exact, and no outside operation
changed.

## Candidate runtime

Candidate-runtime snapshot schema 8 extends schema 7 with one memory-engine
rule. When `refresh_mental_model` executes and the named model no longer exists
in the bank, the task returns normally. The worker then marks the operation
completed. A missing target is the desired end state, so repeating the refresh
has no effect.

All other refresh errors keep their existing behavior. Snapshot verification
continues to reproduce schema-5 through schema-7 patches with their historical
logic; schema 8 alone applies the missing-target rule. The new phase-repair
contract digest binds this distinction.

## Hatchery capability gate

Authorization requires a current Hatchery capability receipt. The probe sends
a fixed, non-sensitive request through the exact provider, model, and base URL
bound by the provider policy. The receipt contains no prompt or response. It
contains the policy digest, provider identity digest, model digest, observation
time, success state, and receipt digest.

The receipt expires with the plan's ordinary evidence window. A connection
failure, timeout, non-successful HTTP response, malformed completion, identity
drift, or stale receipt blocks authorization. The probe supplements provider
policy validation; it does not change runtime failover.

## Drain execution

After reconciliation verification, the controller takes a fresh stable
snapshot, creates the schema-13 exact-drain plan, and obtains a new approval
digest. The worker keeps two total slots, one concurrent retain, and at most two
Hatchery executions. Provider queue and execution timeouts remain unchanged.

The controller launches one one-shot worker. A second worker cannot claim the
plan. Terminal reconciliation closes the run as soon as every selected row is
completed, failed, or cancelled; it does not wait for a later empty claim.

### Pre-execution candidate repair

If that one-shot worker closes during initialization before any selected row
or provider request begins, the failed candidate remains immutable. A repaired
candidate may reuse the verified post-terminal row-state handoff under a new
exact-drain plan and a new approval digest. The new recovery context binds the
existing reconciliation-plan digest and the repaired candidate release digest.

This handoff does not update rows, consume recovery epoch four, or create a
second reconciliation cycle. The planner still proves that the reconciled row
states, task-payload digests, result-metadata digests, checkpoint set, preserved
rows, and installation authority match the verified reconciliation receipt. A
stable generation advance is accepted only with that complete durable-row
continuity, and the new plan binds the current generation. Candidate replacement
remains forbidden for the state-mutating recovery schemas before schema 13.

## Failure handling

The reconciliation cycle is the last automatic attempt represented by this
contract. Any selected failure ends the cycle and requires diagnosis. The
controller must not reset the rows again, invent reconciliation cycle two, or
create replacement operations implicitly.

The monitor retains the closed schema-5 failure families. Raw task payloads,
provider responses, prompts, and exception text remain outside plans, receipts,
progress, status, and operator reports.

## Tests

Tests exercise the public operation-recovery boundary.

- Planning rejects nonterminal source rows, active reference workers, source
  drift, checkpoint drift, a recovery epoch other than three, and a prior
  reconciliation origin.
- Application preserves operation IDs, task payloads, result metadata, and
  checkpoints while clearing only execution-owned fields.
- Verification rejects partial mutation, outside-row mutation, generation
  drift, and receipt drift.
- Snapshot verification accepts historical schema-7 candidates with historical
  patch logic and requires the missing-target rule for schema 8.
- A missing mental-model target completes through the candidate execution seam;
  other refresh failures remain failures.
- Authorization rejects missing, stale, unsuccessful, or identity-mismatched
  Hatchery capability receipts.
- Launch tests prove that one approved plan starts one worker and that terminal
  completion shuts it down without another claim.

## Delivery sequence

1. Add schema-13 and schema-8 contract tests.
2. Implement the candidate-runtime missing-target rule.
3. Implement reconciliation planning, application, and verification.
4. Implement the Hatchery capability receipt and authorization gate.
5. Run the focused operation-recovery suites and repository validation.
6. Assemble and verify a fresh candidate.
7. Create, review, and approve the reconciliation plan.
8. Apply and verify the transaction.
9. Create, review, and approve one exact-drain plan.
10. Launch and monitor one worker through terminal closeout.
