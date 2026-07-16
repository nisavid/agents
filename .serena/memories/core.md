# Agents repository

This repository is the source of truth for reusable personal agent tooling shared across local harnesses.

## Source map

- `plugins/thermos/` contains a shared review plugin with thin Codex and Claude Code adapters; see `mem:plugins/thermos/core`.
- `tooling/chatgpt-ffs/` contains the ChatGPT desktop ASAR patch manager; see `mem:tooling/chatgpt_ffs/core`.
- `tooling/codex-ns-proxy/` contains the local namespace-tool protocol adapter; see `mem:tooling/codex_ns_proxy/core`.
- `tooling/hindsight/` contains a chezmoi-shaped source bundle for the local Hindsight stack; see `mem:tooling/hindsight/core`.
- `.claude-plugin/marketplace.json` exposes repository plugins to Claude Code.

## Invariants

- Repository source and non-secret desired state belong here; generated install caches, profiles, credentials, logs, archives, and live service state do not.
- Harness-specific manifests remain thin adapters over shared plugin sources.
- The repository has no Git submodules or nested Git repositories. Each linked Git worktree is a separate Serena project and uses an ignored worktree-unique `project_name` override.
- Branch-local planning under `.scratch/` is not a project-wide invariant and should not be promoted into durable repository memory.
