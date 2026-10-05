# Operate the quota safeguard

Keep one observer, the existing private state, and the verified Codex Foreman as
the sole pause coordinator. Use this procedure for a release handoff, connection
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
and differ from the current state's `observer.lock`. Preserve both lock inodes.
Do not create a second state directory to get around a running watcher.

The config stores the originating caller's thread, turn, and host, separately
from the recipient IDs. The adapter forwards this caller context to the app;
there is no separate service-identity registration operation in this package.
These values must come from genuine app tool-call context with operator
authorization. MCP initialize does not supply fresh caller identity. Do not
infer or manufacture an identity from thread listings, copy another owner's
context, scrape a pipe from logs, or resume an owner into another app-server.

From a reviewed staged release, run:

```sh
python3 preflight.py --config "$private_config" --state-dir "$existing_state"
python3 preflight.py --config "$private_config" --state-dir "$existing_state" --check-live
```

The live check requires a genuinely inherited `CODEX_APP_TOOLS_PIPE_PATH`. It
reads both configured accounts and verifies exact Foreman and parent routes;
it sends nothing and redeems nothing. Capture its output privately. A successful
read is evidence only for that app generation and registration, not proof that
a later pause will be acknowledged.

## Controlled release handoff

1. Complete local tests, independent review, source publication approval, and
   immutable agents commit/archive checksum selection. Preview the dotfiles
   adapter and selected targets. Prepare the new release and its locked Node
   dependencies before changing the live launcher. Publication and production
   installation require their own explicit approvals.
2. Capture the current unit, code selection, config and state evidence privately.
   Record open episode IDs, identity generations, reset keys, approval references,
   and delivery receipts. Backups are evidence; never restore them over later
   actions. Run the new release's read-only preflight with the actual config and
   state. Resolve failures before stopping the existing observer.
3. Before an intentional stop or withheld activation on a maintenance-aware
   release, persist the hold using the command below. For the **first upgrade
   from an older release that ignores activation.json**, explicitly stop and
   disable the same unit during the approved deployment, recording its previous
   enablement. Otherwise the old controller could restart at boot while the
   upgrade is incomplete. After the approved handoff starts, stop the existing
   `codex-usage-safeguard.service`. Confirm it stopped. Materialize only the reviewed
   launcher, immutable release and unit selection; retain the same unit identity,
   private config, state paths and both locks. Install locked dependencies if they
   were not already staged. Do not enable a parallel watcher.
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
5. Reload systemd and start the same unit in maintenance. Restore previous
   enablement only within the approved deployment. Quota observation should be
   fresh, with delivery held. After reviewing current episodes and obtaining the
   explicit activation decision, run the activation command below; it repeats
   exact live preflight and the running observer notices the transition.
   Inspect `codex-quota-safeguard status`
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
seconds; quota reads begin concurrently. These are best-effort safeguards, not a
hard spending cap or an app-boot guarantee.

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

Activation writes maintenance first, then performs fresh read-only account and
exact-destination preflight, then binds the approved activation to this private
configuration. A failed check leaves maintenance intact. Inspect existing
pending episodes and uncertain receipts before activation; do not rewind or
replay them. Fresh restored or unknown core allowance blocks new sends from an
old pending episode while preserving its receipts and any separate hold. Configuration edits require a new activation decision/preflight.
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
