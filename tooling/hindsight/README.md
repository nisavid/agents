# Hindsight Local Stack

First-pass source bundle for the local Hindsight embed service and client
configuration. This is not a standalone installer yet; it preserves the current
chezmoi-managed source shape so it can become scripted tooling later.

## Layout

- `chezmoi/home/.chezmoidata/hindsight.toml` contains non-secret desired state:
  profile `systalyze`, bank `engineering`, control/API/UI ports, and autostart
  flags.
- `chezmoi/home/Library/LaunchAgents/com.hindsight.embed.stack.plist.tmpl`
  defines the per-user LaunchAgent.
- `chezmoi/home/private_dot_local/bin/` contains the service controller,
  supervisor, and one-shot single-bank cleanup runbook.
- `chezmoi/home/private_dot_local/lib/` contains the shared stack lifecycle
  helper library.
- `chezmoi/home/private_dot_local/libexec/` contains Python helpers used by the
  controller and cleanup runbook.
- `chezmoi/home/private_dot_hindsight/` contains stable non-secret client
  configs for Claude Code, Codex, and Cursor.

## Current Stack

- LaunchAgent label: `com.hindsight.embed.stack`
- Hindsight profile: `systalyze`
- Canonical bank: `engineering`
- Control center: `7878`
- API: `7979`
- Control-plane UI: `17979`

## Boundaries

The live machine still uses chezmoi as the installed-state mechanism. This
directory is a source bundle, not the active apply path.

Do not add generated plugin install state, profile env files, control tokens,
logs, archives, or other secret-bearing/runtime state here.
