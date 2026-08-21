# Publication Planning And Execution

Use the planner and executor from the loaded plugin root. Never rely on a
machine-local installation path:

- `skills/checkpointing-and-publishing-git-work/scripts/plan_git_publication.py`
- `skills/checkpointing-and-publishing-git-work/scripts/execute_git_publication.py`
- `skills/checkpointing-and-publishing-git-work/scripts/plan_git_remote_ref_deletion.py`
- `skills/checkpointing-and-publishing-git-work/scripts/execute_git_remote_ref_deletion.py`

## Planner Effects And States

The planner is not a read-only command. It may fetch bounded remote objects and
create target-local refs under `refs/versionkeeping/publication/`. It preserves
`FETCH_HEAD`, removes only the exact refs it reserved, and may leave fetched
objects in the repository object store.

Follow only `blocked`, `needs_reconciliation`, `ready`, or `verified`. Never
infer around a gate. Missing authorization for any SHA in `target_only_shas`
alone yields `needs_reconciliation`; another concurrent gate yields `blocked`.
The ordinary publication planner and executor never delete a remote ref.

## Default-Branch Policy Gate

Publication request schema version 2 includes one closed
`default_branch_policy` field:

```json
null
```

or:

```json
{
  "ref": "refs/heads/main",
  "direct_push_permitted": true
}
```

Use the object only for a separately verified repository policy/protection
decision. Its `ref` must be the exact full ref reported as the push endpoint's
symbolic default branch, and `direct_push_permitted` must be boolean. An
ordinary non-default destination may use `null`. The observed default branch
is blocked when the field is absent, false, or ref-mismatched; this does not
blanket-ban a branch named `main` when the endpoint reports another default or
when verified policy explicitly permits the direct push.

The planner fails closed if the endpoint's symbolic default branch is
unavailable or malformed. It records that ref in `destination.default_branch_ref`
and includes it in `destination.config_digest`. The executor re-observes the
same endpoint after validating the target lease and immediately before push
actuation, and blocks if the default branch changed.

Git does not provide a lease for symbolic `HEAD`, so the endpoint can still
change its default branch after that final observation and before it processes
the push. The executor closes every observable pre-actuation interval but
cannot turn symbolic `HEAD` into an atomic compare-and-swap condition.
Server-enforced repository policy/protection is the hard guarantee for direct
default-branch pushes; this client-side gate detects observed drift and never
substitutes for that enforcement.

## Trusted Plan Handoff

Capture the complete `ready` JSON file as exact reviewed plan bytes. Separately
capture its SHA-256 digest in trusted review state; the digest must not be read
from the plan or from mutable metadata beside it. Invoke the executor with both
the plan file and `--reviewed-plan-sha256 sha256:<64-lowercase-hex>`. It verifies
the digest before JSON decoding or repository access. Any byte change requires
a fresh planner run and review; never recompute the trusted digest for an edited
plan.

The executor then requires the current destination remote, full ref, endpoint
fingerprint, default-branch ref, config digest, expected lease, and immutable
source SHA to match.
It resolves the current push URL without printing it and binds an ephemeral
config-env remote alias to the captured endpoint string. A normal configured
remote name is discovery metadata, not a publication actuator.

## Authenticated HTTPS Boundary

The ordinary publication executor supports noninteractive HTTPS credentials on
macOS through one closed provider: the `git-credential-osxkeychain` binary in
the trusted `/usr/bin/git --exec-path` directory. Before enabling it, the
executor requires the helper and every directory in its absolute ancestry to
be root-owned, non-writable by group or other, non-symlinked, and free of shell
metacharacters; the helper itself must be a regular, executable, non-set-id
file. A missing, redirected, mutable, or unsupported provider yields
`HTTPS_CREDENTIAL_PROVIDER_UNAVAILABLE` before any push attempt.

The executor first installs an empty command-scope `credential.helper` to clear
all ambient helpers, retains the existing rejection of repository or worktree
`credential.*.helper` configuration, and then appends only that validated
absolute helper path for HTTPS execution. `GIT_ASKPASS=false` and
`GIT_TERMINAL_PROMPT=0` remain in force. Credential bytes travel only between
Git and the system helper through Git's credential protocol: they are never
read by the planner or executor and never enter arguments, diagnostics, plans,
receipts, or repository configuration. SSH and ancestry-guarded local
endpoints do not enable a credential helper and retain their existing
contracts.

## Terminal Remote-Ref Deletion

Remote-ref deletion is a separate planner and executor surface owned by this
skill. It is not an ordinary publication update and must not be supplied to
`execute_git_publication.py`. Before planning, the deletion request requires a
verified merge outcome plus explicit repository and operator authorization. Each
authorization repeats and exactly binds the remote, full `refs/heads/...` ref,
and `expected_target_sha`; the verified merged source SHA must equal that
expected target SHA.

The deletion planner captures the exact remote/ref, endpoint fingerprint, and
configuration digest. It returns terminal `verified` when the exact ref is
already absent. Otherwise it returns `ready` only if that exact ref resolves to
the expected target SHA. Capture complete `ready` JSON as exact reviewed plan
bytes and separately retain its SHA-256 digest. Invoke
`execute_git_remote_ref_deletion.py` with
`--reviewed-plan-sha256 sha256:<64-lowercase-hex>`; it validates the digest
before parsing or repository access.

The executor rechecks configuration and endpoint identity, probes the exact ref,
and treats absence as terminal verified. It deletes only `:<full-ref>` using the
exact `--force-with-lease=<full-ref>:<expected-target-sha>` lease, with one
endpoint-bound attempt and no blind retry. It then verifies that the exact ref
is absent. A moved, ambiguous, malformed, unauthorized, or drifted request
blocks before actuation. Any timeout or failure after deletion actuation begins
returns `POST_DELETION_STATE_UNKNOWN` with `deletion_attempted: true`; re-probe
the captured endpoint and ref before deciding whether another reviewed plan is
safe.

## Transport And Push Boundary

All planner and executor Git subprocesses scrub ambient repository, index,
object, config, SSH, credential, and editor routing; disable prompts; close
stdin; and use a bounded timeout. Every transport command overrides
`core.hooksPath` with a controlled empty directory, so ambient Git hooks cannot
observe endpoint or credential-bearing environment data. A timeout before push
actuation is a deterministic gate. A timeout after push actuation begins has an
unknown remote outcome. Neither is retried automatically.

Execute one immutable source SHA, one explicit nonempty
`<source_sha>:<full-ref>` branch-update refspec, the exact existing or absent
`--force-with-lease`, options before `--`, no followed tags, and submodule mode
`check`. Never use a deletion refspec, mutable `HEAD`, bare, `--all`, `--mirror`,
a wildcard, multiple refspecs, or unconditional force.

The executor performs one push with no blind retry, then re-probes the same
captured endpoint and full ref. Completion requires terminal `verified`
evidence for the immutable source SHA.

If an unexpected failure occurs after the push begins, return
`POST_PUSH_STATE_UNKNOWN` with `push_attempted: true`, the expected SHA, exact
ref, endpoint fingerprint, and safe re-probe instruction. Never expose the raw
exception or retry. A typed blocked execution exits 1; malformed input exits 2;
an internal failure before a possible push exits 3, so automation cannot
mistake an implementation failure for an ordinary publication gate.
