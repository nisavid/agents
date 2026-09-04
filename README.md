# Agents

Personal agent systems that remain outside Provingkit live here.

The canonical source for Rolecasting, Tricritical, Versionkeeping, Mergecraft,
Artifact Customs, Task Witness, and Tidesmith is now
[`nisavid/provingkit`](https://github.com/nisavid/provingkit). This repository
does not distribute or validate those members.

## Current ownership

- [`tooling/hindsight`](tooling/hindsight/) contains the reusable Hindsight
  control plane, local-stack tooling, agent skills, schemas, examples, and
  validation.
- [Base Loadout issue #82](https://github.com/nisavid/agents/issues/82) owns the
  future portable declaration that will select one immutable Provingkit
  release. No Base Loadout declaration is present yet.
- [`tooling/chatgpt-ffs`](tooling/chatgpt-ffs/) and
  [`tooling/codex-ns-proxy`](tooling/codex-ns-proxy/) are personal tools with
  their own documentation and operating boundaries.
- `.scratch` contains unrelated experiments and is not a supported package
  surface.

The dated records under `docs/superpowers` remain available as historical
research and specifications. They can describe the former repository layout;
use Provingkit for current member source and policy.

## Development

Commits use Conventional Commits, enforced by Cocogitto. Validate the current
repository boundary with:

```sh
python -m unittest tests.test_validate_provingkit_retirement
python scripts/validate_provingkit_retirement.py .
```

## License

The repository license is MIT. Individual tools may carry their own upstream
license and attribution files.
