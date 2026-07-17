# Agents

Personal agent tooling and plugin source.

This repository is the source of truth for reusable agent assets that are shared
across local harnesses. It currently contains Thermos, a port of Cursor's
thermo-nuclear review workflow for Codex-like harnesses and Claude Code.

## Layout

- `.claude-plugin/marketplace.json` exposes this repository as a Claude Code
  marketplace.
- `plugins/thermos/` contains the shared Thermos plugin source plus the Codex
  and Claude Code adapter manifests.
- `plugins/thermos/README.md` documents the Thermos adapter boundary, layout, and
  maintenance notes.
- `tooling/hindsight/` contains the reusable Hindsight control plane, local
  stack tooling, templates, schemas, skills, and validation.

## Installed References

The live local installations point at this repository as their source. The
install-cache and refresh workflow lives in the maintenance notes of
[plugins/thermos/README.md](plugins/thermos/README.md).

## Development

Commits use the Conventional Commits format enforced by Cocogitto. After
cloning, run `cog install-hook --all` once to install the repository's
`commit-msg` and `pre-push` hooks into `.git/hooks`.

## License

The repository license is MIT. Individual plugins may carry their own upstream
license and attribution files.
