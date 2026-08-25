# Agents

Personal agent tooling and plugin source.

This repository is the source of truth for reusable agent assets shared across
local harnesses. Its public plugins cover agent role selection (Rolecasting),
Git and worktree provenance (Versionkeeping), pull-request lifecycles
(Mergecraft), independent review through revision (Tricritical), and explicit
third-party component assessment, adoption, and maintenance (Artifact Customs).
For Task Witness's package role, release eligibility, and authority limits, see
the [package reference](plugins/task-witness/README.md).
For the principles that govern the suite as one system, see
[Plugin system design principles](docs/plugin-system/design-principles.md).

## Layout

- `.claude-plugin/marketplace.json` exposes this repository as a Claude Code
  marketplace.
- `plugins/tricritical/` contains the canonical Agent Plugins package, its
  Claude Code adapter manifest, and six Claude-native agent aliases.
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
- `plugins/task-witness/` contains the code-only validation package and its
  manifest identity. Its [package reference](plugins/task-witness/README.md)
  links the normative design and qualification specifications.
- `plugins/artifact-customs/` contains the public component assessment,
  adoption, and maintenance workflows. Its external runtime content lock is
  `release/plugin-content-locks/artifact-customs.json`.
- `release/source-skill-disposition/` records the settled source-contribution
  decisions and last-responsible-moment refresh contract. It grants no host
  mutation or release authority.
- `tooling/hindsight/` contains the reusable Hindsight control plane, local
  stack tooling, templates, schemas, skills, and validation.

## Installation State

No plugin in the first release slate is installed yet. Installation and live
verification begin only after the complete slate is frozen, reviewed,
published, and released.

## Development

Commits use the Conventional Commits format enforced by Cocogitto. Install
`cog` and ensure it is on `PATH`, then run `cog install-hook --all` once after
cloning to install the repository's `commit-msg` and `pre-push` hooks into
`.git/hooks`.

### Validate the source-stage candidate

PR 39 exposes source-stage validation only. Use an absolute, operator-qualified
CPython 3.13+ executable and a public candidate checkout:

```sh
/usr/bin/env -i LANG=C.UTF-8 LC_ALL=C.UTF-8 PATH=/usr/bin:/bin TZ=UTC /bin/sh \
  /absolute/path/to/public-candidate/scripts/run_prepared_release_validation.sh \
  source-stage \
  /absolute/path/to/qualified/cpython \
  /absolute/path/to/public-candidate
```

Run the wrapper only through the clean outer environment shown above. The
qualified CPython runtime and site-package closure remain external trusted
deployment TCB. Review and content-pin the public checkout before invoking it:
source-stage validation executes candidate-owned validation modules and grants
no runtime or release authority. An exact wrapper exit status of `0` confirms
only the source-stage checks. The
[Task Witness canonical client design](docs/superpowers/specs/2026-07-27-task-witness-canonical-client-design.md#prepared-release-supervision)
defines the supervision, cancellation, cleanup, and descendant-boundary
contract.

Native `public-release`, Phase 7, and Task Witness qualification/final-release
entrypoints are unavailable in this source-stage release. No supported
invocation accepts private evidence pathnames; retired routes fail before
candidate execution or any supervisor, launcher, or child launch. Task Witness
therefore remains `production_eligible: false`.

A later release must supply external authority that this repository does not
own: an installed, host-owned, content-pinned, network-denied OS sandbox;
review authorization bound to the candidate bytes; opaque inherited handles
for private evidence; authenticated host and evaluation evidence with managed
signing-key custody and anti-replay state; and independent provider
authorization bound to the exact candidate, policy, runtime, and endpoint. The
source-stage checks do not claim those controls.

## License

The repository license is MIT. Individual plugins may carry their own upstream
license and attribution files.
