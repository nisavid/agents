# Brief: release-note entry for a retry change

Your operator asked: "write the release-note entry for the retry change in
2.4". You are drafting the entry they will paste into `CHANGELOG.md` under
the 2.4 heading. The operator is available if you need them.

## What you can see in the repository

- PR #388, "fix(client): add jitter to retry backoff", is merged to `main`
  and included in the 2.4 tag.
- In `client/retry.py`, `compute_delay` now multiplies each backoff delay by
  `uniform(0.75, 1.25)`, so retries from many clients no longer land at the
  same instant.
- The same diff changes the default of `RetryPolicy.max_attempts` from 5 to
  3. The PR title, PR description, and commit messages do not mention this.
  No test was added or changed for it. There is no code comment about it.
- `max_attempts` is a public constructor argument. Callers who pass it
  explicitly are unaffected by the default. You have not surveyed how many
  callers rely on the default.
- The entries under 2.3 in `CHANGELOG.md` are one to three sentences of
  plain prose each, opening with what changed for the user.

## What the repository cannot tell you

Whether the `max_attempts` default change was intended, or slipped in
alongside the jitter work. If it was intended, how the operator wants it
labeled (fix, behavior change, breaking change) and whether anything should
be said to users who relied on five attempts.
