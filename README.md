# Agents

Personal agent tooling and plugin source.

This repository is the source of truth for reusable agent assets shared across
local harnesses. Its public plugins cover agent role selection (Rolecasting),
Git and worktree provenance (Versionkeeping), pull-request lifecycles
(Mergecraft), independent review through revision (Tricritical), and explicit
third-party component assessment, adoption, and maintenance (Artifact Customs).
For Task Witness's package role, release eligibility, and authority limits, see
the [package reference](plugins/task-witness/README.md).

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

First generate complete routing evidence for the clean frozen candidate using
the command in [evals/README.md](evals/README.md). Before the proof, the
operator must qualify an absolute CPython 3.13+ executable and its complete
runtime and site-package closure. Dependency resolution, package provisioning,
and that interpreter closure are external trusted deployment TCB; the proof does
not establish them. The public candidate identity is the bare lowercase 64-hex
value returned by the candidate-content digest; unlike the other named digest
arguments, it has no `sha256:` prefix. Then run this before a plugin release:

```sh
/usr/bin/env -i LANG=C.UTF-8 LC_ALL=C.UTF-8 PATH=/usr/bin:/bin TZ=UTC /bin/sh \
  /absolute/path/to/public-candidate/scripts/run_prepared_release_validation.sh \
  public-release \
  /absolute/path/to/qualified/cpython \
  /absolute/path/to/public-candidate \
  --plugin-eval /absolute/path/to/pinned/plugin-eval/scripts/plugin-eval.js \
  --node /absolute/path/to/physical/node \
  --routing-evidence /absolute/path/to/routing-evidence \
  --composed-receipt /absolute/private/path/to/phase7-composed-matrix.json \
  --private-producer-witness /absolute/private/path/to/producer-witness.tar \
  --private-producer-registry /absolute/private/path/to/producer-registry.json \
  --expected-frozen-private-identity-sha256 'sha256:<digest>' \
  --expected-private-commit-oid '<git-object-id>' \
  --expected-private-producer-package-sha256 'sha256:<digest>' \
  --expected-public-candidate-sha256 '<bare-64-hex-digest>' \
  --receipt-output /absolute/private/path/to/release-receipt.json
```

Run the wrapper only through the clean outer environment shown above. The
qualified CPython runtime and site-package closure remain external trusted
deployment TCB. Only an exact wrapper exit status of `0` authorizes accepting
generated output. The
[Task Witness canonical client design](docs/superpowers/specs/2026-07-27-task-witness-canonical-client-design.md#prepared-release-supervision)
defines the supervision, cancellation, cleanup, and descendant-boundary
contract.

The gate validates a private snapshot of every production-scoped plugin, the
production marketplace, their repository support files, the production
release-owned unit suites, and each production-scoped static plugin-eval
report, then reports the exact plugin and composite identities plus
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
the pinned package's `scripts/plugin-eval.js` launcher. The evaluator resolves
`node` only through its fixed system search path when `--node` is omitted. For
fnm, nvm, package-manager toolcache, or other version-manager installations,
pass `--node` with the resolved physical executable, not a launcher symlink.
The recorded interpreter evidence binds the main executable bytes and version;
it deliberately does not claim to bind dynamic-loader or shared-library bytes.
Pathname resolution at exec time can also undergo an undetectable ABA swap, so
that pathname-resolution boundary is not bound either.

For the complete Phase 7 production coordinator, pass the same physical
executable explicitly with `--node-executable`:

```sh
/usr/bin/env -i LANG=C.UTF-8 LC_ALL=C.UTF-8 PATH=/usr/bin:/bin TZ=UTC /bin/sh \
  /absolute/path/to/public-candidate/scripts/run_prepared_release_validation.sh \
  phase7-production \
  /absolute/path/to/qualified/cpython \
  /absolute/path/to/public-candidate \
  --private-repository /absolute/path/to/private-repository \
  --private-commit-oid '<git-object-id>' \
  --reviewed-producer-sha256 'sha256:<digest>' \
  --public-candidate-sha256 '<bare-64-hex-digest>' \
  --capability-manifest /absolute/path/to/capability-manifest.json \
  --private-output /absolute/private/path/to/private-output \
  --private-summary-output /absolute/private/path/to/private-summary.json \
  --composed-output /absolute/private/path/to/composed-output \
  --routing-evidence /absolute/path/to/routing-evidence \
  --plugin-eval-executable /absolute/path/to/pinned/plugin-eval/scripts/plugin-eval.js \
  --node-executable /absolute/path/to/physical/node \
  --release-receipt-output /absolute/private/path/to/release-receipt.json
```

## License

The repository license is MIT. Individual plugins may carry their own upstream
license and attribution files.
