# Raw scenario

The branch has `branch.*.pushRemote`, remote push URLs differ from fetch URLs, and one URL is rewritten by Git config. Choose the effective push destination using Git's configuration precedence, then capture the exact reviewed `ready` plan bytes and their SHA-256 digest in separate trusted state. The executor resolves and captures the current endpoint string without printing it. Consider these independent execution-time states:

- `destination.endpoint_fingerprint` changes while every non-destination plan field stays unchanged;
- `destination.config_digest` and `destination.endpoint_fingerprint` stay unchanged while `source_sha`, lease, or refspec changes;
- the plan's source-coupled fields are coherently changed to an unowned descendant, but the executor receives the original reviewed digest;
- the entire plan and current destination/ref/lease/source state match the reviewed `ready` plan, but the configured remote URL changes immediately after capture.
- after review, `core.hooksPath` is changed to a directory with a sentinel `pre-push` hook that would record its environment.

State why the first three cases block before any push, why the fourth can push exactly once through an ephemeral alias bound to the captured endpoint rather than through the mutable configured remote name, and why the fifth must push without invoking the sentinel or exposing endpoint and credential data to it.
