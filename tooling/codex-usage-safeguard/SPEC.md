# Quota safeguard contract

One local observer protects a default Codex account and a separately pinned
Daybreak profile. A normal MCP companion can renew its connection to the app.
It owns no quota policy, never launches another observer, and normally exposes
no tools. Opt-in caller capture returns a read-only candidate. Configuration and
all runtime state remain private and outside releases.

## Quota and account policy

- Main follows the credentials at the user's default `.codex/auth.json`, ignoring
  `CODEX_HOME`. Each read verifies email and account ID against the server. An
  account change archives its previous identity generation and invalidates old
  pending approval notifications. The app identity must also be rechecked before
  main-account actions; a null native account ID is not identity proof.
- Daybreak pins both email and account ID in a distinct profile. If main selects
  that same account, Daybreak owns the single canonical episode and also applies
  the main no-reset rule.
- Zero means a finite core primary or secondary `used_percent >= 100`. Rounded
  99.999%, chatpass, model-specific usage, and `allowed=false` alone do not qualify.
- At zero, a positive `applicable_available_count` requests confirmation for one
  reset for that account and episode. Total inventory does not establish
  applicability. The observer cannot redeem a reset. Every redemption requires
  a verified actual human reply and all tool-level approval gates.
- Main at zero with no applicable reset requests a Foreman pause. Daybreak alone
  with no reset does not pause or begin a confirmation-wait budget. When a reset
  becomes applicable while Daybreak remains zero, confirmation and its budget
  begin. Loss of an applicable reset while waiting does not itself authorize the
  Daybreak no-reset pause.
- The waiting budget totals positive account-wide credit-balance debits. A
  configured, evidenced dollar equivalent maps these debits to a conservative
  threshold below $10 (this deployment selects $8 at $0.04/credit). This is
  consumption value, not a purchase charge. Grants, delayed settlement, sampling,
  and safe-stop lag preclude a hard dollar cap. Missing-meter pause fallback is
  disabled unless separately approved with a decision reference.
- The verified Codex Foreman alone coordinates protective pauses using native
  Steer to active Codex root threads, including other accounts and owned children.
  Claude-only work is excluded; only the established relay covers Claude-managed
  Codex work. The explicitly configured approval/status chat (`parentThreadId`)
  receives approval requests and delivery status. Notifications name that chat's
  exact ID; Foreman receives the same ID for reporting and escalation.
- Delivery intent is durable before sending. Submitted is not acknowledged or
  stopped. An uncertain result requires reconciliation against the same episode
  marker, and cannot be automatically retried after restart or renewal.
- Restored allowance does not resume work or rearm an episode. Exact episode
  release evidence and any required Foreman hold release are mandatory.

## Local caller and approval inbox

A genuine local Codex chat may be explicitly appointed as both native caller
and approval/status inbox. It remains an ordinary chat whose native access must
succeed through the running app. `nativeContext` stores its originating thread,
turn, and host; `parentThreadId` names the appointed inbox. The configured
Foreman remains the sole pause coordinator and must have a distinct ID. Account
selection does not discover or replace this inbox. A previous inbox is retained
as history, without an automatic delivery fallback.

`companion.py --capture-receipt ... --node-binary ... --native-bridge-script ...`
selects capture mode without production configuration or state. Initialization
publishes no route in this mode. Its sole tool, `capture_native_context`, accepts
an empty argument object and reads actual invocation metadata from `params._meta`.
Caller identity arguments are rejected. The expected final local creation receipt
is a private enrollment constraint, never the identity source. Queued creation
IDs and another chat's receipt cannot enroll a caller.

Capture requires a complete runtime thread, turn, and model/native tool-call ID.
The supported native metadata aliases are recorded with the candidate. Matching
aliases may corroborate each other; malformed, duplicate, or conflicting values
fail closed. `x-codex-turn-metadata` supports an object or JSON-encoded object
with `thread_id` and `turn_id`. Its `session_id` is not a substitute thread source.
RPC IDs, generated UUIDs, shell identity, and transcript contents cannot complete
missing invocation metadata. The installed receiver's fallback call/turn IDs are
not capture evidence. An ordinary companion is not proven to receive a required
model/native tool-call ID; a live invocation may return unsupported capture.

Only the inherited `CODEX_APP_TOOLS_PIPE_PATH` is eligible. Capture validates its
owned socket before and after one bounded native `read_thread` call. Returned
`thread.id` must match both the runtime thread and creation receipt;
`thread.kind` must be `codex`, and `thread.hostId` must be `local`. A preferred
local host argument alone is insufficient because native readback can resolve
another host or history source. Raw thread content and native errors are excluded
from the candidate.

An explicit inherited `CODEX_APP_TOOLS_CALLER_HOST_ID=local` records
`explicit_runtime_host` provenance. When that variable is absent, only the
matching expected local receipt, complete runtime metadata, and exact local
native readback jointly qualify `constrained_local_protocol_default`. A present
empty, durable, or unrecognized host fails. Absence alone never classifies a chat
as local.

The tool returns `captured_candidate`, `candidateJson`, and lowercase hexadecimal
`sha256`. `candidateJson` is the exact UTF-8 JSON string to retain and hash, with
sorted keys, compact separators, ASCII escapes, and no trailing newline. It
contains `schemaVersion: 1`, the proposed `nativeContext`, runtime tool-call ID
and source fields, host provenance, the SHA-256 of the exact creation-receipt
file bytes, exact local readback fields, and inherited socket identity. Identical
evidence yields identical candidate bytes. Unavailable evidence returns
`unsupported_capture` with a bounded reason and no candidate; malformed MCP
frames return a parse error. Candidate return grants no persistent authorization.

Capture writes no production configuration, state, routes, activation, or
receipts, and sends no message or quota action. Caller adoption and approval-inbox
transfer require explicit decisions. Retain submitted and uncertain receipts
with their original targets; uncertainty blocks automatic retry across recipient
changes and restart. Known-unsent active events need an explicit transfer decision.
Archived episodes are not backfilled. Changing caller invalidates caller-bound
routes; changing configuration invalidates activation. Strict fresh preflight
still requires both exact recipients before delivery can activate.

## Protective-stop completion

Transport receipts remain unchanged. A Foreman completion record describes an
observed protective stop; it never grants pause, reset, release, or route authority.
The record binds its schema version, account ID, identity generation, episode,
current pause-event ID, configured Foreman ID, observation time, and evidence
references. Main records require the current main-selection generation; pinned
Daybreak records bind the account and exact episode rather than inventing a main
generation.

Foreman records the full observed root inventory, its coverage limits, each root's
stop status, owned-worker/process coverage, and continuation holds. A root without
verified worker and continuation evidence cannot count as stopped. An empty root
inventory can count as complete only with an explicit verified-empty inventory
observation. Missing inventory coverage remains unknown. Denied authorization is
blocked; uncertain outcomes remain unknown; mixed coverage remains partial.
An instruction-based refusal is distinguished from an actual tool/reviewer denial.

The local producer validates a supplied private record against current private
configuration and state. It retains each accepted record immutably and updates a
separate latest-record pointer. For the same episode and event, observation time
must increase unless the submission has the identical currently selected record
digest. A conflicting equal-time submission is rejected without changing retained
records or the pointer. Observation time is compared directly with the active
episode's `openedAt`: earlier observations are rejected by the producer and remain
unconfirmed when read from retained records; equal and later times satisfy this
lower bound. There is no clock tolerance or expiry interval. This comparison does
not establish ordering after the current pause request; the caller must still
collect evidence after that request.

Restart may reread records but cannot replay pause
requests or rewrite transport receipts. The observer and local status consumer
validate that pointer and retained bytes against the current binding. Missing,
malformed, stale, superseded, or mismatched records never indicate completed stops.
Unconfirmed coverage remains visible even when the transport is healthy.

The producer stores Foreman's observations; it does not authenticate an arbitrary
writer as Foreman or independently attest the described native actions. Publication
of the record requires the existing acting workflow's authority. Its evidence
references must permit the supervising agent to audit actual native returns.
No transcript scraping, additional model polling, timed reminders, caller rebind,
or automatic pause or release is introduced by this interface.

## Connection renewal

The companion publishes only its inherited `CODEX_APP_TOOLS_PIPE_PATH`, bound
to the existing genuine owner and adapter. Each destination has an independent
connection: exact Foreman ID and title `Codex Foreman`, or exact parent ID,
must pass before that recipient can receive a message. An unavailable parent
cannot block a verified Foreman, and an unavailable Foreman cannot prevent the
parent being informed. Known-unavailable recipients acquire no send intent;
uncertain actual sends still require reconciliation and are never retried.

Multiple companions coalesce the same socket generation; a CLI without an
inherited route publishes nothing. Destination edits do not grant access or
change caller identity. Legacy route records require their original complete
configuration binding; newer records bind the caller/adapter separately and
still verify each exact current destination. There is no socket discovery,
caller synthesis, credential refresh, permission change, or model polling.

Each destination independently checks liveness every 60 seconds and tries at
most two candidate routes per poll, with retry backoff capped at five minutes.
Observation-failure fallback reuses that poll's verified recipient connections;
it does not begin another round of route checks.
Health separates quota observation, delivery mode and destination availability.
An outage retains one incident until both destinations recover. A desktop
notice is attempted initially and at most hourly thereafter, including after
restart and after a failed or uncertain notice. Intent and timestamp precede
each attempt. Submission does not prove a person saw it. Reminders perform no
model turn, reset, resume or repeated pause send.

## Persistent maintenance

`activation.json` outside the release controls delivery. Missing, malformed,
explicitly held or configuration-mismatched activation means maintenance:
quota observation continues under the same singleton locks, but native
connection and delivery are inhibited. Restart, reboot, MCP route publication
and successful quota reads cannot activate delivery.

The operator records maintenance before an intentional service stop or a
withheld activation. Activation requires an explicit verified decision
reference and fresh read-only preflight of both accounts and exact recipients.
The helper persists maintenance before preflight, so a failed check stays held.
Activation never changes credentials, caller identity, destinations or ledgers.
The controller serializes each intent/send/receipt with maintenance decisions;
an older activation preflight cannot overwrite a newer decision. It rechecks
maintenance before every new intent. Ordinary new delivery attempts also require
a successful reading from the current observer poll with a finite exhausted core
window. Retained `lastSample` and earlier preflight readings do not qualify.
During an existing reset-confirmation wait, the separately approved
observation-failure fallback admits only its newly generated pause and that
pause's parent status notification, including the existing confirmation
coalescing. It cannot admit separate sends for unrelated pending events.
A current observer reading with restored or unknown allowance blocks unsent old
events without releasing holds or changing receipts.
A send already in flight may finish before the maintenance command returns; stopping work remains the Foreman's job.


## Acceptance and installation

Tests use fixture accounts, subprocesses, and sockets. No real reset or pause is
an acceptance test. Required evidence covers quota/account boundaries, durable
receipts, concurrent publication, missing/stale routes, exact targets, rejected
registration, independent destination outages, maintenance across boots, hourly
notice bounds across restart, and preservation of open
episodes through code upgrade and rollback.

Caller-capture acceptance uses the companion process/MCP boundary with synthetic
metadata, receipts, native subprocesses, and sockets. It covers normal empty tool
discovery, opt-in discovery, success, unsupported capture, host provenance,
conflicts, foreign enrollment/readback, stable output after a fixture restart and
changed account environment, and preservation of production-file fixtures. These
checks do not qualify live metadata, self-delivery, caller reuse after the turn,
app restart survival, or persistence across actual account switches.

Source belongs in `nisavid/agents`; the opt-in immutable-release adapter belongs
in `nisavid/dotfiles`. Keep the same systemd identity and both existing singleton
locks. Upgrade/rollback changes code, never restores a prior ledger. Publication,
release selection, production MCP registration, and activation remain explicit
deployment gates; committing this package does not authorize them.
