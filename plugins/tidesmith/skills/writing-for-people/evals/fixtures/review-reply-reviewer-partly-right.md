# Brief: reply in a review thread on your retry-loop change

You opened a pull request that adds `fetch_with_retry` to `client/http.py`.
A reviewer left this comment on the retry loop:

> This will hammer the upstream on 5xx. Needs exponential backoff and a
> hard cap on attempts, say 3.

## What the code in the PR actually does

- The loop already backs off exponentially:
  `sleep(BASE_DELAY * 2 ** attempt)` with `BASE_DELAY = 0.2`, so the waits
  run 0.2s, 0.4s, 0.8s, and so on.
- There is no cap on attempts. The loop exits only on success or when
  `time.monotonic() > deadline`, and `deadline` is set 30 seconds after the
  first attempt. Under sustained 5xx that allows about eight attempts before
  the deadline expires.

## Where you stand

- You agree a hard attempt cap belongs in the loop, and you will add one in
  this PR.
- You have not chosen the cap value. You want it to fit the upstream
  service's published rate limit, which you have not looked up yet. Whether
  3 is the right number is not settled.
- Nothing else on the PR bears on this thread.
