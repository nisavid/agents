# Brief: edit pass on a finished review comment

You drafted the review comment below for a pull request. Every fact in it
has been checked against the PR and is correct. The draft is finished
except for your post-draft edit pass.

## The draft

```text
Worth noting — `retry_budget` defaults to 3 here — but the three callers in
`ingest/` all pass their own value, so the default only bites new call
sites. That said, the integration test for the default path hasn't been run
in this PR, and it's the one that would catch a zero budget. I'd add a
`retry_budget > 0` guard in `RetryPolicy.__post_init__` — cheap, and it
turns a silent no-retry into a loud failure at construction time. That's the
case where defaults matter most. Also, the docstring still says the default
is 5.
```

## Facts the draft relies on

- `retry_budget` defaults to 3 in the PR.
- The three call sites under `ingest/` each pass an explicit `retry_budget`.
- The integration test covering the default path was not run in this PR.
  That test would catch a budget of zero.
- A `retry_budget > 0` check in `RetryPolicy.__post_init__` would raise at
  construction time instead of silently skipping retries.
- The `RetryPolicy` docstring still states the default as 5.
