# Agents

Personal agent tooling and plugin source.

This repository is the source of truth for reusable agent assets shared across
local harnesses. Its public plugins cover agent role selection (Rolecasting),
Git and worktree provenance (Versionkeeping), pull-request lifecycles
(Mergecraft), independent review through revision (Tricritical), and explicit
third-party component assessment, adoption, and maintenance (Artifact Customs).
Task Witness is a manifest-only Agent Plugin identity for the code-only
task-evidence validation package shared by registered providers. It has no
portable skill or MCP surface; its controller, client, launcher, runtime, and
smoke validator remain Task Witness-owned package support. It loads the exact
byte-pinned, operator-approved validator selected by a retained trust snapshot
to validate one supplied task-evidence bundle and return its canonical
projection. The supplied bundle directory and every direct child are
current-EUID-owned and inaccessible to group and other users, and each direct
file has link count one. Link-count-one enforcement rejects ordinary hard-link
aliases; it does not detect bind-mount aliases. Deployment qualification must
ensure caller bundles contain no mount alias to canonical installation state.
Registered validators are a full-process Python trusted computing base with
the invoking user's ambient authority. Task Witness grants no workflow
authority and is not a sandbox. The receipt-bound `task-witness validate ...`
shim is the only public subprocess entry; the canonical client uses an
externally qualified CPython 3.13+ runtime closure as the externally qualified
deployment TCB. The
already-running client does not authenticate that code or pre-module history:
it can detect relevant current state and post-startup drift, not a startup
action that has already erased itself.

Its checks operate within a cooperative same-EUID deployment boundary. They
make substitution and ambiguous execution fail closed where observable, but do
not provide authenticity against a same-EUID actor before detection. Deployment
policy and external deployment receipts own that trust; a successful envelope
is not a deployment receipt. The qualification candidate remains
source-stage-only and production-ineligible until the detached native-host,
review, and final-manifest gates close. Final promotion changes only its
eligibility declaration and marketplace route. Its deployment-owned provider
importer accepts only
canonical, content-addressed declarations, copies declared validator bytes into
private retained generations, explicitly stages the non-user-selectable Task
Witness smoke provider and exact trust context, and verifies every retained
object with creation disabled before accepting composition. The controller
strictly cross-binds manager, source, policy, runtime-qualification, and exact
first-install authorization evidence through a two-phase public preparation
facade, then writes and independently rereads one inert stage with complete
deployment and absent-state rollback receipts. Staging always recomputes the
read-only plan from the raw source, manager, and runtime inputs before accepting
authorization. It records the verified physical candidate root, resolves and
pins the stage parent and both protected roots, and rejects physical overlap
before creating the stage directory. The private deployer must preserve those
namespace mappings throughout materialization under the cooperative same-EUID
boundary. Pre/post checks reject observable drift, but they cannot prevent a
same-EUID actor from relocating the stage inside a check-to-syscall window. If
that occurs, staging fails closed without returning an accepted stage receipt
and may leave inert residue for explicit recovery. Fail-stop residue may include
an unaccepted receipt-shaped file, but it is never an accepted stage or
promotion proof. The canonical client independently consumes that exact receipt
contract and rederives the controller-owned smoke validator, producer, issuer,
module, and declaration identities. Stage
verification proves the private inventory and bindings; it is not promotion
authority. For selected external providers, the client requires one matching
active-policy identity, exact receipt-owned producer, issuer, and validator
objects, an exact disjoint partition of every non-smoke trust role, and each
provider's own validator-module union. Unselected policy entries remain an
allowlist rather than active inventory. The implemented activation surface
includes first-install `absent`-to-active activation and routine
active-to-active payload activation, plus derived active-to-active
complete-control-set maintenance. Routine A-to-B selection replaces
`active.json` before `deployment.json`; B-to-A restoration replaces the prior
`active.json` before the prior `deployment.json`.

Routine preparation and recovery creation-disabled verify the exact recursive
retained deployment and rollback receipt chain. After staging, recovery uses
only the original `ActivationRequest`, exact expected current journal bytes,
and the independently verified stage; it does not require the external
candidate source. Candidate smoke is bound to the candidate receipt and runs
only after candidate selectors are complete. After candidate rejection and
exact selector restoration, rollback smoke is separately bound to the
immediate-prior receipt; candidate smoke acceptance cannot authorize prior
restoration.

After prior smoke acceptance, cleanup removes the candidate rollback receipt
`R_B` first, then only transaction-owned files, then newly owned directories
deepest-first, and the candidate deployment receipt `B` last. It leaves every
shared prior file and the external stage unchanged. If rollback smoke rejects,
the durable terminal outcome is `recovery-required`; recovery returns that
outcome without rerunning smoke and never cascades to an older retained unit.

Routine activation and recovery enforce a closed live-tree, receipt, and
temporary inventory. Only the active baseline, the exact completed transaction
prefix, the optional current step, and exact journal, install, and selector
temporaries are admissible; any other file, directory, receipt, or temporary
fails closed before mutation.

Preparation derives complete-control-set maintenance internally when the exact
candidate maintenance authority differs; callers continue to submit
`DeploymentRequest` and cannot select the transaction class.
Compatibility-policy v2 declares one exact control-surface v1 with the supported process
profile and complete client-interpreted contract catalog; deployment receipts
carry the exact receipt-contract subset.

After transaction-owned additive artifacts are installed, maintenance replaces
controller, policy, launcher, client, smoke bundle manifest, `active.json`,
`deployment.json`, and the canonical shim in that exact order; the shim is
always last. Candidate smoke runs through the installed B client, policy,
launcher, and receipt authority; after exact restoration, rollback smoke runs
through the staged-and-restored A authority.

Process-loss recovery executes through a freshly loaded, exact staged prior
controller and validates each mixed A/B prefix against the journal and
independently authenticated prior and candidate policy epochs before mutation.
The same closed live-tree, receipt, journal, temporary, cleanup, and durable
terminal rules apply across maintenance. A successful B installation can
prepare and stage a later routine payload or another complete-control-set
maintenance transaction under B's authenticated policy epoch.

The implemented activation slice includes operator-selected exact-target
`rollback_to` and K.1 post-unlink transaction-result reconciliation. It does
not claim retained-history garbage collection or TW4 platform qualification.

`prepare_rollback_to(RollbackToRequest)` resolves one exact retained ancestor
through its authenticated successor rollback edge, follows validated
control-policy epochs, displays the exact current and target identities, and
writes nothing. `rollback_to(...)` requires fresh exact authorization and a
private external stage; it mints a new deployment receipt whose prior is the
current receipt and whose endpoint authority equals the selected target, plus a
rollback receipt preserving the complete current activation unit.

Before unlinking a successful terminal transaction journal, the controller
durably retains its exact bytes at
`transaction-results/sha256-<transaction_id>.json`.
`reconcile_transaction_result(ResultReconciliationRequest)` accepts only the
original activation authority and exact expected terminal bytes, rederives the
stage-bound intent and closed historical-result baseline, and verifies that the
current live state still matches the outcome.

No source-stage operation authorizes or changes a live installation.

The canonical root is a current-EUID-owned mode-`0700` directory, and the
canonical `activation.lock` is an empty, current-EUID-owned, single-link regular
file with exact mode `0600`; both reject permissive macOS extended `ALLOW` ACL
entries, permit deny-only ACLs, and fail closed on ACL-inspection failure or
observable drift in their complete filesystem identities.

Every completed-prefix directory has exact mode `0700` and is opened with
creation disabled. Only the exact validated `control-installing` pending
artifact's new parent suffix may use final-path `mkdirat`; each newly observed
directory must have an umask-derived mode subset of `0700`, be normalized to
exact mode `0700`, and be synchronized with child-then-parent `fsync`. Recovery
first performs a provisional audit, rechecks the exact lock and journal,
reconciles that suffix with creation disabled, and then performs the ordinary
full audit. Under the cooperative same-EUID and check-to-syscall nonclaim, a
hidden child beneath an opaque mode-`000` pending directory can cause mode
normalization before fail-stop, but no artifact installation, journal advance,
smoke, or acceptance. Directory repair uses no temporary directory or
process-global `umask` change and makes no arbitrary same-EUID authenticity
claim.

For the exact invocation, validation, supervision, cancellation, cleanup, and
output contract, see the
[Task Witness canonical client design](docs/superpowers/specs/2026-07-27-task-witness-canonical-client-design.md).

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
- `plugins/task-witness/` contains the canonical Agent Plugins identity, exact
  Claude adapter projection, code-only validation runtime, canonical client,
  deployment controller, and intrinsic activation-smoke validator. The
  standard manifest does not expose those control-plane files as portable
  Agent Plugins equipment. The generic
  `release/public-release-runtime-packages.json` catalog authorizes its
  package-specific `release/task-witness/public-release-registration.json`;
  both must agree before source-stage discovery or snapshotting. Shared release
  code derives its executable validator as
  `scripts/validate_<package-name-with-hyphens-replaced-by-underscores>.py`,
  its runtime-package kind, and its name from the catalogued package directory.
  The registration retains only qualification, source-stage flags, and support
  paths. The shared release validator independently requires Task Witness in
  the source-stage catalog; the package registration owns its qualification
  fields. The qualification candidate remains production-ineligible until all
  TW4 gates close. While ineligible, Task Witness is omitted from the
  production-scoped plugin,
  validator, test, marketplace, plugin-eval, expected-identity, receipt
  plugin-identity, and scoped release-contract and release-scope inventories.
  Generic registration validation still binds the package, and the
  whole-repository Git candidate identity and release-receipt `candidate` field
  still bind its committed bytes. The effective source-stage support closure
  always includes the catalog, registration, and Task Witness package
  validator.
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
