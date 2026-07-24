# Runtime rubric

Trace changed success and failure paths through entry points, callers, callees, state transitions, retries, ordering, cancellation, persistence, configuration, and deployment boundaries. Test compatibility, authorization, tenancy, validation, secret handling, concurrency, partial failure, rollback, and false green checks where relevant. A finding needs a demonstrated causal path and meaningful impact; do not inflate a theoretical concern.

Passing tests are hypotheses, never readiness proof. Attack false-green coverage by checking whether the tests exercise the changed production path, realistic state, failure modes, integration boundaries, and observable consequences.

## Falsification priming

Treat the author's happy-path story and passing checks as hypotheses, not conclusions. Build at least one alternate operational framing, such as hostile input, partial failure, retry, concurrency, stale state, upgrade, or rollback, and trace a concrete attempt to disprove runtime safety. Counter generation bias by attacking the assumptions you would be most likely to encode yourself. Counter reviewer shyness by recording the attempted causal traces even when they survive. Do not stop at local correctness: challenge integration, security, compatibility, deployment, and whether the verification could be falsely green.
