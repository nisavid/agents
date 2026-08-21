---
name: publishing-reviewable-prs
description: Use when standalone GitHub PR creation, canonical title/body/draft-ready publication, publication-evidence audit, or evidence reconciliation is required, including fork-sync and fixup PRs. Do not use for generic PR inspection, comments, checks, threads, or merge actuation.
---

# Publishing Reviewable PRs

Own standalone creation and canonical text/readiness after transport.
`versionkeeping:checkpointing-and-publishing-git-work` owns commits/pushes;
Graphite first creates stack draft transport.
[Writing Reviewable PR Descriptions](../writing-reviewable-pr-descriptions/SKILL.md)
owns the `pr-content` operation: the complete title/body pair and its authorized
text surface.
Preserve unauthorized fields byte-for-byte. Helpers preflight, mutate once,
reread, then atomically store a redacted local receipt; GitHub provides no
conditional atomicity.

## Workflow

1. Bind repository/base, qualified head, head repository/owner, pushed OIDs, PR,
   and remote commits. Read policy/templates; retain live title/body/draft
   preimage immediately before edit.
2. Use [Writing Reviewable PR Descriptions](../writing-reviewable-pr-descriptions/SKILL.md)
   for the validated pair, authorized surface, and manifest from the pushed diff.
   Choose exactly one publication-review mode and an explicit sorted specialist
   inventory before freezing the candidate. `required` needs an absolute Task
   Witness bundle root and a current, nonhistorical, bare-clean Tricritical
   terminal projection. `not-required` needs no bundle and makes no clean-review
   claim. A suspected credential in either live or candidate PR text blocks
   before mutation; never echo, preserve, or republish it.
3. Let the helper resolve and prove the canonical private receipt root before
   mutation, then invoke one owned operation. Production CLIs expose no receipt
   root override; internal test and controlled-migration APIs may supply one.
   Stop on drift, ambiguity, receipt-storage failure, or unexpected final state;
   never retry/rollback automatically. Re-read identity, text, and state; inspect
   live rendering after structured changes.

Every create, text, and ready API/CLI call must supply `--review-mode` and
`--selected-specialists` as a JSON array, including `[]`. There is no default or
downgrade. Required mode invokes only the effective user's authenticated
`~/.local/libexec/task-witness/task-witness` front door while holding the
publication lease. It consumes the generic canonical Task Witness wrapper and
then enforces Mergecraft's exact candidate, profile, and terminal rules; it
never reopens a trust root, copies an upstream schema, imports an owner
validator, or falls back to self-attestation. If the installed front door,
current evidence, or registered Tricritical producer chain is unavailable, stop
before mutation.

This release has fixture-level required-review success coverage but no reachable
new-publication Tricritical producer chain. Required live publication is
therefore intentionally unavailable; do not infer a production reachability
claim from the fixtures.

The authenticated Task Witness client owns bounded launcher supervision under
its exact `task-witness-process-profile-v2`; its registered validators are
operator-trusted, cooperative code in that launcher. Mergecraft's outer
supervisor has a closed internal call shape, accepts only the authenticated
front door plus the absolute bundle root, requires canonical envelope framing
on successful exit, and performs identity-safe best-effort cleanup within the
same-EUID install boundary. Its module-private capability is not a security
boundary. Do not claim arbitrary descendant containment or descendant-free
ordinary success.

## Create

Use `scripts/create_reviewable_pr.py` with exact identity, existing absolute
review-input, and an absolute template containing
`__PUBLISHING_REVIEWABLE_PRS_PR_NUMBER__` wherever the assigned number belongs.
In new-PR Stack review input, use it only for the sole current row's number and
same-repository URL; never use it in an existing or noncurrent row.
Preserve original template bytes and perform token-only rendering; never
reverse-replace an assigned number. It validates sentinel rendering,
rejects a matching PR, and creates one nonce
draft. Recover only when one draft matches nonce and all identity/text.
Validate assigned-number rendering, publish once, exact-reread, write a canonical
receipt, and leave draft.
The reviewed candidate remains the token-bearing pre-number candidate. After
GitHub assigns a number, prove the body is its unique token-only rendering,
validate the same evidence again, and perform one final exact nonce-state reread
immediately before the single canonical edit. That post-review reread is the
receipt preimage.

## Update Existing PR Text

Capture live title/body SHA-256, then run `scripts/update_reviewable_pr.py text`
with exact identity/OIDs, state, absolute body/manifest paths, and authorized
`body-only`, `title-only`, or `title-body`. Never default broad. It privately
snapshots validated bytes, proves unauthorized fields unchanged, and publishes
once. Reject a no-op before mutation. Preserve current custom content,
exact-reread, and write a canonical receipt. For a new PR created through
Graphite transport, pass the original token-bearing `--body-template`; the
publisher performs the sole token substitution after Graphite assigns the PR.
In required mode, reread the exact live preimage after review and block the
write if any field changed.

## Mark Existing Draft Ready

After readiness/rendering gates, refresh preimage and run
`scripts/update_reviewable_pr.py ready` with exact identity/OIDs, digests, draft,
and manifest. It validates then immediately preflights. Error plus exact intended
state remains causally ambiguous and receives no canonical receipt; do not retry.
Only a zero-exit mutation followed by an exact final reread receives a canonical
receipt.
In required mode, reread the exact live draft after review and before the ready
mutation; the reread must equal the reviewed preimage.

## Receipts, Audit, And Reconciliation

Receipts form an ordered append-only ledger per stable repository/PR number.
Ordered identity epochs retain historical base/head OIDs while requiring the
authoritative latest receipt to match the current complete identity. Their strict,
versioned schema binds sequence and predecessor/content hashes, the exact
mutation preimage and final reread, identity/OIDs, title/body and review-input
SHA-256 digests, state, publisher/policy/schema versions, operation, timestamp,
and provenance—never title/body bytes or credentials. Canonical v3 receipts
record either explicit `not-required` or `required`; required also binds the
canonical publication-candidate digest, Task Witness launch-envelope digest,
anchor generation/active record/trust/bundle, and Tricritical manifest/projection
digests. V2-only ledgers remain `legacy-unrecorded`; once v3 appears, a later v2
receipt invalidates the ledger. Reconciliation records
`unwitnessed-reconciliation` and never mints review provenance.
Write them atomically only after the helper's exact final reread. A storage
failure after a verified mutation is not a reason to retry the mutation: inspect,
then reconcile if appropriate.

The canonical root is
`$XDG_STATE_HOME/mergecraft/pr-publication-receipts`, falling back to
`~/.local/state/mergecraft/pr-publication-receipts`. Helpers prepare a private,
writable store and hold an exclusive per-PR lease from final preflight through
mutation, reread, and append. Standalone creation cannot address the per-PR
ledger until GitHub assigns a number, so it first holds a head/base creation
lease and proves the store, then prepares and locks the exact ledger before the
canonical edit. The chain detects accidental gaps, forks, swaps, timestamp
rollback, reordering, and edits; it is not a cryptographic security boundary
against a hostile process running as the same user.

Use `scripts/audit_reviewable_pr.py audit` with the same exact identity and
receipt root before resuming or closing a publication task. It reads only the
authoritative latest receipt and returns `verified`, `drift`, or `unavailable`.

Use `scripts/audit_reviewable_pr.py reconcile` only after independently
confirming exact live identity/state and supplying the bound `--review-input`.
It validates the first read, requires an identical second live reread, and
refuses when the authoritative latest receipt already matches. Otherwise it may
append one permanent `reconciled-unreceipted` receipt even when older canonical
receipts exist. It performs no forge mutation and can never upgrade that
provenance to `canonical`; a later actual canonical transition gets a new
receipt.

## Hard Rules

- Never use raw PR create, title/body edit, or ready commands or connectors.
- `--head` must use `OWNER:BRANCH`; its owner must exactly match
  `--head-owner` and the owner of `--head-repository`.
- Resolve OIDs/digests from live pushed/stored state immediately before mutation.
- Scan both title and body, live and candidate, for suspected secrets before
  mutation or reconciliation; never echo matched bytes.
- Only Graphite transport may be temporary; repair it before handoff/review.
- Required review binds the exact repository, PR number or create token,
  base/head/OIDs, title, raw body-source bytes, published UTF-8 body, review
  input, publication profile, and specialist inventory. Never substitute raw
  and published digests or review a numbered create candidate.
- Treat the required-review supervisor as cooperative bounded liveness, not an
  arbitrary-process containment boundary. Never infer descendant quiescence
  from ordinary successful completion.
- File inputs must be existing absolute literal paths, never variables, `~`,
  relative paths, substitution, stdin, or inline multiline content.
- Never use a repository-local receipt root. An explicit absolute override is
  reserved for tests and controlled migration. Never copy receipts into a
  repository, PR, issue, comment, log, or attachment.
- Never describe unpushed changes or discard current custom content.
- Stop when base, stack membership, preservation, or authority cannot be
  established safely.

## Completion Evidence

Report URL, base/head OIDs, title/body digests, state, receipt id/provenance/
sequence, audit or result status, checks, and remaining action. Comments,
feedback, CI, merge, and Git/ref publication retain distinct owners; the bundled
comment helper is internal.
