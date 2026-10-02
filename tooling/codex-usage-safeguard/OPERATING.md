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

The config binds a genuine existing owner thread, turn, and host. These values
must come from the supported app tool-call context with operator authorization.
MCP initialize does not supply fresh caller identity. Do not infer or manufacture
an identity from thread listings, copy another owner's registration, scrape a
pipe from logs, or resume an owner into an unrelated app-server instance.

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
3. After the approved handoff starts, stop the existing
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
5. Reload systemd and start the same unit. Inspect `codex-quota-safeguard status`
   and the journal. Require fresh samples for both accounts and transport health
   `healthy=true` with exact destination checks. Compare the preserved episode,
   reset and delivery fields; expect fresh observations, not identical whole-file
   hashes after polling. Submitted or uncertain deliveries must not reappear as
   new sends. No live reset or pause is a test.
6. If checks fail, stop the new unit and restore the previously verified **code
   selection and unit**, retaining current state. Roll back only to code that
   understands this state schema. Restart the previous observer and verify its
   genuine route and health. Never rewind receipts, approvals or reset keys.

The dotfiles adapter materializes files but does not enable/start services,
reload systemd, install Node dependencies, or change MCP configuration. Disabling
its selection does not stop an already installed observer.

## Connection and delivery incidents

`transport/accepted.json` records the last verified route. The companion registry
is private, coalesces duplicate socket generations, and retains at most 16.
The observer checks liveness every 60 seconds, tries at most two candidate
connections per poll, and backs off locally up to five minutes after failure.
Native RPC timeout is five seconds; quota reads begin concurrently. These are
best-effort safeguards, not a hard spending cap or an app-boot guarantee.

An incident is persisted in `transport/health.json`. The observer attempts one
`notify-send` alert per incident, persisting intent before delivery. `submitted`
means the notification command succeeded, not that a person saw it; `failed`
means no delivery claim is possible. A crash after intent leaves it uncertain
and does not trigger repeated alerts. Inspect local status/journal if the desktop
notification service is unavailable. Verified connection recovery closes the
incident without resetting quota state.

If opening the app and loading a Codex session supplies a valid route, renewal
is automatic for the existing registration. If caller authorization is rejected,
use the supported app tool workflow to register this safeguard owner again and
obtain the genuine context. Have the operator explicitly approve replacement of
the registration; update only the private registration, repeat exact Foreman and
parent preflight, and perform the controlled restart. There is no implemented
unattended route for creating new caller authorization. Do not relax peer checks
or create a replacement owner to make recovery appear successful.

For `attempting` or `outcome_unknown` delivery, read the exact destination thread
and search for that episode/event marker. Reconcile the receipt from actual
thread evidence before any explicit resend decision. A reconnect is never resend
permission. Parent submission is not a second Foreman or proof of a stopped root.

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
