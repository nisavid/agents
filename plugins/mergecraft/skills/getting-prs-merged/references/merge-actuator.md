# Merge actuator interface

`internal:merge-actuator` owns two separate leaves. `merge-inspection` is
read-only acquisition of the bound PR's merge state. `merge-actuation` is the
sole `merge-write` authority. Neither is a public skill or lifecycle
coordinator.

## Contract

- **Trigger:** one final actuation request from `getting-prs-merged` after all
  merge gates pass
- **Modes:** the repository-authorized merge, squash, or rebase method selected
  by the coordinator
- **Authority:** exact repository, PR, base, head SHA, and merge method, with
  current repository and operator authorization
- **Inputs:** rebound repository and PR identity; expected base and head; current
  policy, feedback, checks, approvals, and mergeability evidence; selected merge
  method
- **Outputs:** a reread, head-bound merge receipt, or a `blocked` or `ambiguous`
  terminal with no success claim
- **Forbidden reverse calls:** no call to `getting-prs-merged`, readiness,
  feedback coordination, publication, CI repair, comments, Git/ref cleanup, or
  deployment
- **Loop owner:** none; execute at most once and never retry an ambiguous
  possible-mutation result
- **Terminal statuses:** `merged`, `blocked`, or `ambiguous`

## Boundary

Immediately before the write, rebind repository, PR, base, head SHA, policy,
feedback, checks, approvals, mergeability, method, and authority. Reject drift
or any missing gate. Perform exactly one merge write using the selected method,
then reread the PR and verify the merged state and head identity.

This interface cannot choose policy, adjudicate feedback, repair CI, publish PR
text or Git refs, post comments, resolve threads, start review loops, delete
branches, or deploy. It returns control to `getting-prs-merged` after the single
verified actuation or terminal failure.
