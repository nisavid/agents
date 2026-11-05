# Operate the quota safeguard

Keep one observer and the existing private state. Use the qualified coordinator:
Dispatch for the new route or Foreman for the legacy route. Use this procedure for a release handoff, connection
incident, pending reset, or uncertain delivery. [SPEC.md](SPEC.md) owns the policy.

## Configure and preflight

Copy the shape of `config.example.json` into the existing private installation
config. Preserve its accounts, native owner registration, parent/Foreman IDs,
policy and shared singleton lock. Supply `stateDir`, `nodeBinary`, and the
installed app's `nativeBridgeScript`. Main ignores profile overrides and reads
the default `.codex/auth.json`. Pin Daybreak email, ID, and auth file explicitly.
The example's registration values are placeholders and cannot be used live.

The config must be a user-owned file with mode `0600`, and state a user-owned
directory with mode `0700`. State must live outside the release directory.
`sharedLock` names the existing predecessor singleton lock; it must already exist
and differ from the current state's `observer.lock`. It must be a user-owned
regular file with no group or other permissions, in a user-owned directory with
no group or other permissions. Verify the existing lock and directory meet these
requirements without replacing the lock. Preserve both lock inodes.
Do not create a second state directory to get around a running watcher.

The config stores the originating caller's thread, turn, and host, separately
from the recipient IDs. The adapter forwards this caller context to the app;
there is no separate service-identity registration operation in this package.
These values must come from genuine app tool-call context with operator
authorization. MCP initialize does not supply fresh caller identity. Do not
infer or manufacture an identity from thread listings, copy another owner's
context, scrape a pipe from logs, or resume an owner into another app-server.

With the observer stopped and reset activity quiescent, run from a reviewed
staged release:

```sh
python3 preflight.py --config "$private_config" --state-dir "$existing_state"
python3 preflight.py --config "$private_config" --state-dir "$existing_state" --check-live
```

Preflight takes the existing shared singleton lock exclusively, without creating
or replacing it, and holds it through the local scan and optional live checks.
A running observer, including maintenance or observe-only mode, causes
`observer_stop_required_for_preflight`. It also takes the existing reset lock for
the ledger scan and validates the main ledger and selector as one snapshot.
Preflight never creates missing locks, custody markers, or ledger bindings.

The live check requires a genuinely inherited `CODEX_APP_TOOLS_PIPE_PATH`. It
reads both configured accounts and verifies exact Foreman and parent routes;
it sends nothing and redeems nothing. Capture its output privately. A successful
read is evidence only for that app generation and registration, not proof that
a later pause will be acknowledged.

## Hatchery adoption prerequisites

The deployed installation is the user account on Hatchery. This source candidate
has not inventoried its installed revision, unit state, private config, locks,
ledgers, or caller registration. Those observations and any required migration
are part of this delivery's deployment work. Source integration alone does not
qualify that installation, and these instructions authorize no live action.

Before cutover, record privately the installed revision and launcher target,
unit active/enablement states, config binding, state directory, shared-lock inode,
reset-lock presence, account/selector identities, all episode/reset keys, custody
records, closed histories, and submitted or uncertain receipts. Do not collect
credential contents into this record. Identify any in-flight reset invocation;
complete or reconcile it before migration. Use a private offline copy to prepare
and review the specific translation before applying it to the stopped original.
Retain a byte inventory before and after migration and a verified operator
reference approving the precise changes. Do not put these private records in Git.

The supported direct handoff has an existing private shared lock, an existing
private `reset.lock`, coherent main generation/selector state, structurally valid
account ledgers, paired reset records/custody markers, and matching surviving
reset digests. Markerless account ledgers are validated before the first normal
write adds custody; this proves current shape, not earlier completeness.

When the inventory shows older metadata, prepare the following offline adoption
under the same approved stop. Hold the existing shared singleton lock and, after
creation if required, the reset lock exclusively while applying it, with every
observer and reset writer stopped:

- If `reset.lock` is absent, explicitly approve creating that empty user-owned
  `0600` file in the existing `0700` state directory. Create it exclusively; if a
  lock appears meanwhile, stop and re-inventory. Never replace a surviving lock.
  Preflight remains read-only and cannot perform this step.
- If a main ledger has neither generation nor selector, validate its account and
  email against retained identity evidence and the fresh server-validated default
  identity. Bind a new UUID in `identityGeneration` and create
  `main-selection.json` with the same `generation`, `accountId`, `email`, and fresh
  `observedAt`, preserving the ledger's episode, reset key, and receipts. A
  mismatch needs an explicit account-transition decision; do not erase the old
  ledger or let an unqualified empty selector discard its active episode.
  `main_selection_adoption_required` blocks preflight and runtime for an active
  predecessor episode until this binding is complete.
- For each reset record missing custody, verify its complete retained request
  against its account/episode or closed history, including `episodeId`,
  `accountId`, and `idempotencyKey`, and inspect its known or uncertain result.
  Preserve the record and its status. The new `reset-custody-<episode>.json`
  contains exactly those three identity fields. A surviving conflicting marker,
  missing ledger, mismatched request, malformed delivery, or unexplained orphan
  requires reconciliation, not a replacement or a retry.
- Bind a validated active Daybreak record using the SHA-256 of its canonical JSON
  (`sort_keys=True`, separators `(',', ':')`) as `resetEvidence`, with the episode
  UUID in `resetEvidenceEpisode`; set `fallbackEvidenceValid` to `false` until a
  new successful quota observation. Preserve or add the same digest to the
  matching closed episode when adopting historical reset evidence. Never replace
  an existing disagreeing digest. Do not invent a digest for a no-reset episode,
  a missing record, or a missing history.
- Retain a separate adoption receipt identifying the reviewed input bytes,
  precise edits, operator reference, and the point at which custody begins.
  Neither newly created metadata nor a successful preflight proves that evidence
  was complete before adoption. Missing all copies and loss before this boundary
  remain unknown; never describe them as verified history.

No general legacy schema conversion is selected. If Hatchery's inventory does
not fit these shapes, prepare the smallest installation-specific translation and
its synthetic regression before applying it. The outstanding fact is the actual
installed state, not whether a manual lifecycle or migration step is allowed in
the deployment plan. Complete that migration and its verification before calling
the Hatchery delivery complete.

## Controlled release handoff

1. Complete local tests, independent review, source publication approval, and
   immutable agents commit/archive checksum selection. Preview the dotfiles
   adapter and selected targets. Prepare the new release and its locked Node
   dependencies before changing the live launcher. Publication and production
   installation require their own explicit approvals.
2. Capture the current unit, code selection, config and state evidence privately.
   Preserve account-custody and reset-custody records with the corresponding
   account and reset ledgers. If surviving custody evidence conflicts with a
   missing ledger, stop the affected account or reset and reconcile its history;
   do not recreate records or retry a consume request to clear the error. Local
   custody records cannot detect loss of every copy or loss before their adoption.
   Record open episode IDs, identity generations, reset keys, approval references,
   and delivery receipts. Backups are evidence; never restore them over later
   actions. Inspect paths and permissions before stopping; the coherent new-release
   preflight runs after the stop. Prepare any required adoption from the inventory
   above and obtain its exact approval as part of the deployment.
3. Before an intentional stop or withheld activation on a maintenance-aware
   release, persist the hold using the command below. For the **first upgrade
   from an older release that ignores activation.json**, explicitly stop and
   disable the same unit during the approved deployment, recording its previous
   enablement. Otherwise the old controller could restart at boot while the
   upgrade is incomplete. After the approved handoff starts, stop the existing
   `codex-usage-safeguard.service`. Confirm it stopped. Materialize only the reviewed
   launcher, immutable release and unit selection; retain the same unit identity,
   private config, state paths and both locks. Install locked dependencies if they
   were not already staged. Apply and verify the approved offline adoption, if
   needed, preserving the original state and surviving lock inodes. Run local
   preflight while stopped; a failure keeps the unit stopped and delivery held.
   Do not enable a parallel watcher.
4. Register the companion through the normal supported Codex MCP configuration:

   ```toml
   [mcp_servers.codex_quota_connection]
   command = "codex-quota-safeguard"
   args = ["companion"]
   env_vars = ["CODEX_APP_TOOLS_PIPE_PATH"]
   ```

   Use the installed launcher's absolute path if the app's PATH does not contain
   it. Preserve all unrelated MCP settings. The app must load the new normal MCP
   entry; its startup publishes the route. A launch from a CLI without the app
   route is a no-op. Do not embed a socket path or caller identity in this block.
5. Reload systemd and start the same unit in maintenance. Inspect fresh quota
   observations and preserved state while delivery is held. Then stop that same
   unit again and confirm it stopped; maintenance alone does not release the
   snapshot lock. From the still-authorized app caller with its genuinely
   inherited route, review current episodes, obtain the explicit activation
   decision, and run the activation command below while the observer is stopped.
   It repeats the coherent local scan, live account reads, and exact destination
   preflight. A failed activation leaves maintenance intact and the unit stopped.
   After success, start the same unit and restore prior enablement only within
   the approved deployment. Neither activation nor this restart releases
   independent work holds or replays receipts. Inspect `codex-quota-safeguard status`
   and the journal. Require fresh samples for both accounts and transport health
   `healthy=true` with exact destination checks. Compare the preserved episode,
   reset and delivery fields; expect fresh observations, not identical whole-file
   hashes after polling. Submitted or uncertain deliveries must not reappear as
   new sends. No live reset or pause is a test.
6. If checks fail, persist maintenance, stop the new unit and restore the previously verified **code
   selection and unit**, retaining current state. Roll back only to code that
   understands this state schema and enforces activation.json. An older release
   that ignores maintenance cannot be restarted as a safe rollback; keep its
   unit disabled until a separately approved compatible recovery. Restart the previous observer and verify its
   genuine route and health. Never rewind receipts, approvals or reset keys.

The dotfiles adapter materializes files but does not enable/start services,
reload systemd, install Node dependencies, or change MCP configuration. Disabling
its selection does not stop an already installed observer.

## Connection and delivery incidents

`transport/foreman/accepted.json` and `transport/parent/accepted.json` retain
independently verified routes; the old shared accepted record is a read-only
fallback. The companion registry coalesces duplicate generations and retains at
most 16. Each destination checks liveness every 60 seconds, tries at most two
candidates per poll and backs off locally up to five minutes. RPC timeout is five
seconds; quota reads begin concurrently. Observation-failure fallback uses the
connections checked in that poll; unavailable recipients can be checked again
in a later poll. These are best-effort safeguards, not a hard spending cap or an
app-boot guarantee.

The controller can send through a verified Foreman connection even when the
parent is unreadable. An unavailable recipient remains unsent without an
`attempting` or uncertain receipt. A real send with unknown outcome retains its
original reconciliation requirement, even if another destination later works.
`status` reports persistent delivery mode, observation freshness and per-recipient
availability separately. A healthy systemd process alone is not acceptance.

Failed checks keep bounded `lastFailures` in each recipient's health: category,
stage, numeric RPC code and recipient role when available. Raw error text and
credentials are excluded. A socket access denial does not establish invalid
caller authorization. Use the supported approval flow for a bounded diagnostic;
never substitute caller metadata or discover an alternative socket.

The aggregate `transport/health.json` keeps one incident while either destination
is unavailable. It attempts one local desktop notification initially and at most
once per hour thereafter, including across process/host restarts. Each attempt's
intent and timestamp are durable before `notify-send`; failed and uncertain
attempts wait the same hour. Legacy notices use their incident start as the old
attempt time. `submitted` means command success, not user acknowledgment. Both
verified destinations close the incident. These reminders create no model turns
or pause/reset requests. Inspect status and the journal if desktop notifications
are unavailable.

If opening the app and loading a Codex session supplies a valid route, renewal
is automatic for the existing registration. If caller authorization is rejected,
stop at that boundary. Establish whether the app can supply fresh genuine caller
context through its normal integration, and verify that context's permitted use
before changing the stored caller. Do not assume a separate persistent grant is
required or available. Creating or reading a task alone does not establish this
context. The current sending method requires an explicit recipient thread ID;
successful sending does not imply automatic discovery of the current dot.
Normal companion startup supplies a connection route, not that recipient.
Until a supported account-to-destination mapping is verified, preserve the
explicitly approved destination. Repeat exact Foreman/parent preflight before
the controlled restart. Do not relax peer checks or invent caller metadata.

An account switch can leave a cloud owner inaccessible with an app diagnostic
such as `thread placement belongs to a different actor`. A fresh socket cannot
repair that actor boundary. Stop retrying that owner during maintenance and
identify a supported context-refresh path under the intended account/owner;
never copy this repair task's context into the config. Approval to
repair the service is not approval to transfer owner authorization or change the
notification parent. Resolve those decisions separately, preserve the ledger and
both locks, then verify the exact owner/Foreman/parent route before restarting.

Ordinary new sends require a successful exhausted core-window reading from the
current observer poll. Retained `lastSample`, activation preflight, and earlier
successful polls cannot supply that evidence after a failed read. During an
existing reset-confirmation wait, a separately approved observation-failure
fallback can send the newly generated pause and its parent status notification,
including the existing confirmation coalescing. That exception does not admit
separate sends for other pending events. Submitted and uncertain receipts retain
their no-replay rules.

A Daybreak no-reset pause is conditional on Daybreak still being the default
main account. Dispatch checks that identity before each unsent recipient. A
verified mismatch durably invalidates the stale event while preserving existing
receipts. It is never replayed after switching back; a later fresh policy
condition can create a distinct event. Missing identity evidence blocks dispatch.
An uncertain combined pause/confirmation receipt also remains uncertain for
the confirmation; connection recovery does not generate a second request.

For `attempting` or `outcome_unknown` delivery, read the exact destination thread
and search for that episode/event marker. Reconcile the receipt from actual
thread evidence before any explicit resend decision. A reconnect is never resend
permission. Parent submission is not a second Foreman or proof of a stopped root.

## Hold or activate delivery

Use the selected release's helper, the existing private config and state, and a
reference to the verified operator decision. A typed reference is audit evidence,
not consent by itself. These commands do not change enablement or service state.

```sh
python3 activation.py --config "$private_config" --state-dir "$existing_state" \
  --mode maintenance --reference "$verified_maintenance_decision"
python3 activation.py --config "$private_config" --state-dir "$existing_state" \
  --mode active --reference "$verified_activation_decision"
```

Persist maintenance **before** stopping the service or staging an upgrade. The
controller checks it each poll and serializes each new intent/send/receipt with
maintenance decisions. The maintenance command waits for an already-started
send to finish; inspect its receipt before claiming a stop. A newer maintenance
decision supersedes an older activation still running preflight. Keep
both locks and all account/approval/delivery state. Observe the recorded
maintenance mode before proceeding with a handoff.

Activation requires the observer to be stopped. It writes maintenance first,
then performs a locked local snapshot, fresh read-only account and
exact-destination preflight, then binds the approved activation to this private
configuration. A failed check leaves maintenance intact. Inspect existing
pending episodes and uncertain receipts before activation; do not rewind or
replay them. A current observer reading with restored or unknown core allowance
blocks new sends from an old pending episode while preserving its receipts and
any separate hold. A successful preflight does not replace the next observer
poll's evidence. Configuration edits require a new activation decision/preflight.
A route renewal alone never clears maintenance. This helper does not release
Foreman holds or redeem quota resets.

## Confirm a reset and release a hold

The standing monitor request is not per-use reset confirmation. Ask once in the
parent for one reset for the exact account and episode after refreshing applicable
availability. Return; the local observer tracks the wait without model polling.
Main uses the native `consume_usage_reset` tool with its tool-level confirmation
only after fresh exact-zero and app/default identity checks. Native allowance
thresholds alone may be weaker than this safeguard's exact-zero rule.

For the separately pinned Daybreak profile, verify the actual human reply and
episode first, then use the foreground helper from the reviewed package:

```sh
python3 reset_once.py --config "$private_config" --state-dir "$existing_state" \
  --account daybreak --episode "$episode" \
  --confirmation-reference "$verified_human_reply" --execute
```

An `attempting` or `outcome_unknown` saved reset is marked
`reconciliation_required` on a later invocation. That invocation performs no
consume request and preserves the exact saved request, credit, key and approval.
Determine the actual provider outcome before any separately authorized retry;
restored quota or a missing credit is not a reason to bypass eligibility checks.
Known `reset`, `already_redeemed`, `no_credit`, and `nothing_to_reset` results
are terminal for the saved logical attempt. Later helper invocations return the
saved result without a new request; an unrecognized saved status blocks execution.

The reference is an audit pointer; typing an arbitrary string does not establish
consent. The caller must verify the reply and honor any execution approval gate.
The helper rechecks exact identity, zero, applicable availability and credit
eligibility, and saves the same redemption idempotency key before POST. An
uncertain attempt requires investigation; never choose another key or credit as
a retry. This command is not part of the observer and must never be scheduled.

After verified restored allowance and explicit release of any pause hold, record
the exact release evidence with `request_rearm.py --state-dir ... --account ...
--episode ... --evidence-reference ... --hold-released`. This only records a
request for the observer to verify. It does not resume threads or redeem a reset.

## Lessons and evidence limits

The installed app accepted an existing genuine caller registration after one
real restart in a temporary, read-only companion experiment. Several normal MCP
copies inherited the new socket, and exact Foreman reads succeeded. Startup had
no new thread/turn metadata. Separate CLI app-server instances exposed different
loaded-thread state and could not substitute for the running app connection.
The temporary registration was removed and the original config restored after
the experiment. The production module is tested with fixtures; deployment must
establish its own current-app evidence.

Raw WHAM usage exposes core windows, applicable resets and credit balance that a
summary CLI may omit. Inventory count, chatpass allowance and native account IDs
that are null are not substitutes for that evidence. Keep API assumptions in the
small adapters and fail visibly when they no longer hold. Account-wide debit
estimates include other concurrent activity and can undercount when grants mask
consumption; the configured $8 threshold leaves headroom, not a guarantee.

## Qualify Dispatch and Concierge routing

The new route sends quota-control requests to the accepted Dispatch and fault
incidents to Concierge. The repair owner accepts incidents and records their
continuation. Keep the existing caller, account pins, episode and receipt
history, and independent holds. This procedure qualifies a selected release and
configuration; it does not itself approve installation, sends, or activation.

With the observer stopped, delivery held, and reset writers quiescent, set
`incidentRoutingEnabled` to boolean `true` before collecting configuration
bindings. Configure the accepted
Dispatch ID as `foremanThreadId`, the Concierge ID as `conciergeThreadId`, the
existing parent ID and `parentHostId`, the private `incidentReceiptStore`, and
`roleBindings` for `foreman` (role `dispatch`), `concierge`, and `repairOwner`.
Each binding names its accepted thread, host, role, and acceptance reference.
Preserve the genuinely enrolled `nativeContext`; these fields do not themselves
prove caller access or recipient acceptance.

From the selected release, run the local check. Add `--check-live` only from the
genuine app caller with its inherited route and authorization to read the two
accounts and exact recipients:

```sh
python3 preflight.py --config "$private_config" --state-dir "$existing_state" --qualify-routing
python3 preflight.py --config "$private_config" --state-dir "$existing_state" --qualify-routing --check-live
```

Retain the reported `configurationBinding` and `sourceBinding` privately. The
live variant checks configured host/thread/role bindings but sends nothing.
Neither command activates delivery or establishes a received incident.

Prepare a separately authorized harmless native qualification naming the actual
caller and recipients, exact payloads, incident/attempt identities, selected
private store and permitted operations. Establish Dispatch submission and its
correlated assistant receipt; establish Concierge submission, receipt, repair
owner acceptance, and a recoverable continuation. Confirm the actual writer and
consumer can commit, reopen and recover the same journal, and qualify writer
serialization. Use the journal and readback methods in
[INCIDENT-RECEIPT-DRAFT.md](INCIDENT-RECEIPT-DRAFT.md). An uncertain send stays
uncertain; reconcile it by its original identity rather than sending again.

Retain private JSON witnesses in `routing-evidence/` beneath the state directory,
named `caller-provenance`, `recipient-acceptance`, `writer-commit`,
`writer-restart`, `consumer-restart`, and `serialization`, each with a `.json`
extension. Each must record the actual source/configuration, actors, operations,
observations and evidence references for review. The program verifies private
file properties and witness content digests; it cannot authenticate the operator
or infer that a witness describes an observed event. Each witness is limited to
64 KiB, and the packet below to 256 KiB.

Construct private `routing-readiness.json` with exactly these fields:

- `version`: integer `1`.
- `configurationBinding` and `sourceBinding`: the final preflight bindings.
- `parentHost`: the configured, verified `parentHostId`.
- `dispatch`: `identity`, `attempt`, `submission` (`host`, `turn`, `message`),
  and the Dispatch assistant `receipt` (`turn`, `message`). Identity and
  attempt use the incident correlation fields in `INCIDENT-RECEIPT-DRAFT.md`.
- `conciergeIncident`: the selected incident ID already in the retained outbox.
- `conciergeRecordDigest`: canonical digest of that incident's raw journal record.
- `artifacts`: the six witness names mapped to their canonical JSON digests.

Canonical digests are SHA-256 over sorted, compact JSON with ASCII escaping and
nonfinite values rejected, as implemented by `activation.evidence_digest`.
Both packet and witnesses must be user-owned private regular files beneath a
private directory. Review their contents and the actual native and store evidence;
a digest binds content but does not authenticate an attestation.

With the same stopped observer and genuinely inherited route, run the full
read-only check after preparing the packet:

```sh
python3 preflight.py --config "$private_config" --state-dir "$existing_state" --check-live
python3 reconcile_incident.py --config "$private_config" --state-dir "$existing_state" --incident "$qualification_incident"
```

The first reports `routingReadiness` only after the configured recipients and
correlated native evidence pass; the second reports the retained incident's
reconciliation stage without sending. Verify the designated consumers can load
the same reviewed release and reopen the same journal. A separately reviewed
activation decision must select the reported packet digest:

```sh
python3 activation.py --config "$private_config" --state-dir "$existing_state" \
  --mode active --reference "$verified_activation_decision" \
  --qualification-sha256 "$reviewed_qualification_sha256"
```

Activation enters maintenance first, rechecks the real qualification, and commits
the decision only if the source, configuration and packet remain bound and no
newer maintenance decision supersedes it. Missing, malformed, changed or
oversized qualification inhibits delivery. The running guard reads the bounded
selected packet and witnesses; unrelated live journal growth does not revoke
that historical qualification. Live incident faults and uncertain sends remain
subject to their separate handling and must not be silently retried. Requalify
when the selected source, caller, configuration, bindings or evidence change.

The emergency alert threshold is 95% used for failed intake with active work.
An uncertain submission or a receipt check attempted without a confirmed receipt
also counts as failed for this alert when quota or activity is unknown. A known
unsent incident alone does not qualify for that exception. A matched receipt
remains matched while owner acceptance or continuation is pending. The observer
currently reports activity as unknown; it uses fresh quota samples when available.
An alert does not authorize a resend, reset, account switch or hold release.
