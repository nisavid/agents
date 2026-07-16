# Thermos plugin

`plugins/thermos/` is one shared review workflow served to Claude Code and Codex-like harnesses through thin adapters.

- Shared skills live under `skills/`.
- Claude-discovered subagents live under root `agents/*.md`.
- Codex per-skill interface metadata lives under `skills/<name>/agents/openai.yaml`; this directory name collision is required by the harness formats.
- Claude and Codex manifests intentionally expose different adapter surfaces while sharing the same core skill bodies.
- Source edits require manifest version cachebusting, harness-specific validation, reinstall/update, and a fresh session as documented in `plugins/thermos/README.md`.
- Keep secrets and generated plugin cache/install state out of the repository.
