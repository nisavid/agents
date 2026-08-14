# Changelog

## 1.0.0

Initial dual-harness release, 2026-07-20:

- Uses Agent Plugins v1 as the canonical package format, with a native Claude
  manifest projection and skill-local Codex metadata.
- Migrates the canonical checkpoint and publication planner with exact leases,
  literal path commits, target-only SHA authorization, and endpoint verification.
- Adds durable sibling worktree guidance with explicit harness, agent, user, and
  unknown provenance boundaries for cleanup.
- Adds history-preserving fork synchronization without pull-request text,
  readiness, or merge actuation.
- Adds a strict contract validator, portable evaluation corpus, and focused
  tests for ownership, cleanup provenance, and source-path leakage.
- Binds publication execution to the reviewed endpoint fingerprint and exact
  lease through an ephemeral config-env alias. Execution verifies a separately
  retained digest of the exact reviewed plan bytes before parsing, and every
  transport disables ambient hooks while preserving endpoint secrecy.
- Keeps strict JSON, sanitized Git environments, bounded timeouts, and no
  automatic retry across planning and execution.
- Carries discovered fork remotes and full refs through independent fetches and
  final verification, including non-default remote and branch coverage.
- Moves rare terminal cleanup and evaluation-integrity rules behind explicit
  skill references and records the plugin topology as canonical metadata.
