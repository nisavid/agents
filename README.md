# Agents

Personal agent tooling and plugin source.

This repository is the source of truth for reusable agent assets shared across
local harnesses. Its public plugins cover agent role selection (Rolecasting),
Git and worktree provenance (Versionkeeping), pull-request lifecycles
(Mergecraft), and independent review through revision (Tricritical).

## Layout

- `.claude-plugin/marketplace.json` exposes this repository as a Claude Code
  marketplace.
- `plugins/tricritical/` contains the shared Tricritical plugin source plus the
  Codex and Claude Code adapter manifests.
- `plugins/rolecasting/` contains delegation-topology and model-selection
  policy.
- `plugins/versionkeeping/` contains Git publication, persistent-worktree, and
  fork-synchronization runtime policy. Its canonical evals, tests, and content
  lock are `evals/versionkeeping/`, `tests/plugins/versionkeeping/`, and
  `release/plugin-content-locks/versionkeeping.json`.
- `plugins/mergecraft/` contains PR writing, publication, Graphite, feedback,
  readiness, and merge runtime workflows. Its canonical evals, tests, and
  content lock are `evals/mergecraft/`, `tests/plugins/mergecraft/`, and
  `release/plugin-content-locks/mergecraft.json`.
- `tooling/hindsight/` contains the reusable Hindsight control plane, local
  stack tooling, templates, schemas, skills, and validation.

## Installed References

The live local installations point at this repository as their source. Active
installation and refresh behavior is owned by the deployment repository.

## Development

Commits use the Conventional Commits format enforced by Cocogitto. Install
`cog` and ensure it is on `PATH`, then run `cog install-hook --all` once after
cloning to install the repository's `commit-msg` and `pre-push` hooks into
`.git/hooks`.

## License

The repository license is MIT. Individual plugins may carry their own upstream
license and attribution files.
