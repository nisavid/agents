# Agents

Personal agent tooling and plugin source.

This repository is the source of truth for reusable agent assets shared across
local harnesses. Its public plugins cover agent role selection (Rolecasting),
Git and worktree provenance (Versionkeeping), pull-request lifecycles
(Mergecraft), independent review through revision (Tricritical), and explicit
third-party component assessment, adoption, and maintenance (Artifact Customs).

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
- `plugins/artifact-customs/` contains the public component assessment,
  adoption, and maintenance workflows. Its external runtime content lock is
  `release/plugin-content-locks/artifact-customs.json`.
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

First generate complete routing evidence for the clean frozen candidate using
the command in [evals/README.md](evals/README.md). Then run this before a plugin
release with `plugin-eval` available on `PATH`:

```sh
uv run --with PyYAML --with pytest python scripts/validate_public_release.py . \
  --routing-evidence /absolute/path/to/routing-evidence \
  --composed-receipt /absolute/private/path/to/phase7-composed-matrix.json \
  --private-producer-witness /absolute/private/path/to/producer-witness.tar \
  --private-producer-registry /absolute/private/path/to/producer-registry.json \
  --expected-frozen-private-identity-sha256 'sha256:<digest>' \
  --expected-private-commit-oid '<git-object-id>' \
  --expected-private-producer-package-sha256 'sha256:<digest>' \
  --expected-public-candidate-sha256 'sha256:<digest>' \
  --receipt-output /absolute/private/path/to/release-receipt.json
```

The gate validates a private snapshot of every public plugin, the marketplace,
their repository support files, the release-owned unit suites, and each static
plugin-eval report, then reports the exact plugin and composite identities plus
non-blocking warning IDs. It pins the selected `plugin-eval` launcher and
callable runtime tree before and after analysis, and returns raw and normalized
report digests bound to the private snapshot target.
A successful production run atomically writes the required external receipt as
a private (`0600`), deterministic JSON file. The receipt binds the clean Git
candidate, source and snapshot content, routing and plugin-eval evidence
digests, the externally composed public/private evidence receipt, the
release-owned validator and test contract, any supplied frozen identities, and
the final plugin and composite identities. The composed receipt exposes only
the private candidate identity, reviewed commit and producer digests, remote
observation, producer-witness, semantic, exported-schema, opaque evidence
bundle and inventory digests, terminal summary, and public composition
artifacts; private paths, member names, payloads, producer streams, and test
names remain opaque. Before producer execution, the private gate cross-binds
the independently supplied commit and producer roots through the remote
observation, trust anchor, deterministic Git inclusion witness, and frozen
identity. It verifies the Git object chain in pure Python, extracts only the
verified producer blob, and reruns it against the typed opaque bundle in a
fresh, bounded environment. The supplied frozen receipt passes only when the
producer reproduces its canonical bytes exactly. The release receipt retains
the cooperative-agent claim and excludes raw prompts, raw outputs,
credentials, local evidence paths, and timestamps.
An existing receipt is accepted only when its bytes match exactly. Source-stage
validation accepts neither composed evidence nor a release receipt.
A frozen release reruns the gate with an expected-identity document so the
evidence names the bytes that passed. The release-owned plugin-eval policy may
demote only an explicitly named static failure under frozen tool, metric, and
runtime-component caps; every other failed or error-severity check blocks. Use
`--plugin-eval /path/to/plugin-eval` when it is not on `PATH`; that path must be
the pinned package's `scripts/plugin-eval.js` launcher.

## License

The repository license is MIT. Individual plugins may carry their own upstream
license and attribution files.
