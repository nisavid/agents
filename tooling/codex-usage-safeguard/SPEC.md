# Quota safeguard contract

One local observer protects a default Codex account and a separately pinned
Daybreak profile. A normal MCP companion can renew its connection to the app.
It owns no quota policy, never launches another observer, and exposes no action
tools. Configuration and all runtime state remain private and outside releases.

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
  Codex work. The parent receives approval requests and delivery status.
- Delivery intent is durable before sending. Submitted is not acknowledged or
  stopped. An uncertain result requires reconciliation against the same episode
  marker, and cannot be automatically retried after restart or renewal.
- Restored allowance does not resume work or rearm an episode. Exact episode
  release evidence and any required Foreman hold release are mandatory.

## Connection renewal

The companion publishes only its inherited `CODEX_APP_TOOLS_PIPE_PATH`, bound
to the existing genuine owner registration and exact configured destinations.
Multiple copies coalesce the same socket generation; a CLI invocation without
an inherited route publishes nothing. There is no socket discovery, caller
identity synthesis, credential refresh, permission change, owner recreation,
or model polling.

The observer validates the exact Foreman ID and title `Codex Foreman`, plus the
exact parent ID, before adopting a candidate. A healthy accepted route remains
stable. Route failures permit another candidate with the same configuration
binding. Validation and local retries are bounded. Rejection cannot cause a
send or overwrite quota, approval, episode, or delivery state.

An unavailable connection produces one durable incident and one attempted local
desktop notice. Notification intent is persisted before the attempt; failure or
uncertainty is recorded, never described as delivered. Repeated failures reuse
the incident until verified recovery. Loss of caller authorization requires
explicit supported re-registration; access is never expanded to recover.

## Acceptance and installation

Tests use fixture accounts, subprocesses, and sockets. No real reset or pause is
an acceptance test. Required evidence covers quota/account boundaries, durable
receipts, concurrent publication, missing/stale routes, exact targets, rejected
registration, restart/recovery, one notice per incident, and preservation of open
episodes through code upgrade and rollback.

Source belongs in `nisavid/agents`; the opt-in immutable-release adapter belongs
in `nisavid/dotfiles`. Keep the same systemd identity and both existing singleton
locks. Upgrade/rollback changes code, never restores a prior ledger. Publication,
release selection, production MCP registration, and activation remain explicit
deployment gates; committing this package does not authorize them.
