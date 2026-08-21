# Hindsight Control Plane

Reusable Hindsight control-plane code, local-stack tooling, agent skills, policy templates, schemas, examples, and validation live here. Machine profiles, user-specific banks, launchd values, harness socket bindings, and installation wiring belong in the consuming dotfiles repository.

## Layout

- `bin/` contains the control-plane CLI and local-stack commands.
- `lib/hindsight_memory_control_plane/` contains the reusable Python package.
- `lib/hindsight-embed-stack.zsh` contains environment-driven stack lifecycle helpers.
- `libexec/` contains helper programs used by the stack commands.
- `skills/` contains reusable agent instructions.
- `config/` and `examples/` contain public schemas and synthetic fixtures.
- `docs/` contains the durable architecture and migration contract.
- `tests/` contains repository-owned contract and disposable-stack validation.

## Documentation

- [Adopt the control-plane architecture](docs/adoption.md) for a fresh or
  existing installation. Adoption changes code ownership and service wiring; it
  does not move Hindsight memory.
- [Migration readiness](docs/migration-readiness.md) for the current migration
  status, the read-only discovery gate, and the remaining cutover work.
- [Control-plane plan](docs/control-plane-plan.md) for repository delivery and
  validation status.
- [Product requirements](docs/PRD.md) for the complete safety and migration
  design.

## Harness authority

The reusable harness path is:

`harness hook → controller adapter → private session bridge → capability broker → authenticated Hindsight API`

The broker resolves the endpoint and canonical bank from a validated inventory.
Harness configuration and hook payloads cannot choose a URL, bank, route, token,
scope, or tag. Each session bridge owns one exchanged capability, sequence, and
idempotency history. CLI launches receive only the bridge locator in their
environment; GUI launches resolve an equally non-secret, user-only locator on
the first hook after atomically consuming a controller-only one-use envelope.
Session authority defaults to 12 hours and is capped at 24
hours; normal close revokes it earlier.

`hindsight-memory harness` exposes native Codex, Claude Code, and Cursor hook
adapters plus explicit recall, reflect, mental-model, and status tools. The
controller derives and retains a bounded clean outcome only from a terminal
assistant record observed by a clean stop checkpoint. Ambient
recall and checkpoint failures are visible but do not terminate the harness.
Transcript checkpoints retain the complete cleaned user and assistant epoch,
replace stable bounded segment documents, reject overflow explicitly, and
report final-checkpoint, pending-write, or undrained-write failures at close. Close/revocation
is still attempted when the final checkpoint is unavailable.

Rendered harness artifacts are inactive, content-addressed generations.
Activation is a separate digest- and compare-and-swap-bound operation that
preserves unrelated hooks and settings, disables upstream automatic recall and
retention, and leaves Hindsight authority disabled when a post-activation check
fails. Claude's upstream knowledge tools are disabled with its verified
empty-MCP-server mode when this path is activated.

`hindsight-memory harness-config stage` and `plan` persist inactive artifacts
and destination-bound activation records; `status` is read-only. `apply`
activates the controller-owned surface. `verify` is read-only while the
semantic routing contract is healthy and atomically disables recognized
Hindsight hooks when direct authority, controller-hook drift, or automatic
upstream memory settings return. `rollback` also leaves Hindsight disabled
instead of restoring a retired direct integration. Every automatic supported
harness route in the validated inventory must name the literal `engineering`
bank. The secret-free approved activation record binds the prestate and target
digests to the exact destination paths. An owner-only transaction journal makes
multi-file updates recoverable across interruption. The
`hindsight-memory-runtime` skill exposes the explicit session tools without
giving a harness direct endpoint, bank, or credential authority.

## Integration upgrades

`hindsight-memory integration-upgrade` stages upstream harness integrations as
immutable, content-addressed candidates. A closed reusable catalog pins the
publisher, HTTPS origin, same-origin update-manifest URL, verifier identity,
and allowed transport modes. The
consumer policy contains only the catalog ID, initial version, channel, allowed
major, `retained_generations`, and one of `pinned`, `manual`, or
`automatic-compatible`.

Planning invokes three absolute, digest-bound executables with a scrubbed
environment: a source verifier, a disposable compatibility runner, and a
post-activation smoke runner. The compatibility report must prove hook schema,
transcript behavior, security isolation, and broker transport. A direct-only
package may become the selected upstream package, but it cannot replace the
controller-owned or previously certified memory authority. Broker-compatible
packages receive authority only after their post-activation smoke test passes.
That smoke runner invokes the approved `harness-config verify` command.
Managed startup reconciliation invokes the same command before exposing
Hindsight hooks; an unsafe result remains disabled and cannot fall back to the
package's direct/default bank routing.
The update policy sets bounded recent-generation retention; current, certified
last-known-good, and pending generations are always retained.

The lifecycle is:

```text
hindsight-memory --state-dir STATE integration-upgrade plan ...
hindsight-memory --state-dir STATE integration-upgrade apply ...
hindsight-memory --state-dir STATE integration-upgrade check ...
hindsight-memory --state-dir STATE integration-upgrade status --harness codex
hindsight-memory --state-dir STATE integration-upgrade rollback ...
```

`check` fetches a bounded strict-JSON manifest from the catalog, verifies and
tests its exact artifact, and atomically activates it only under an
`automatic-compatible` policy. Manual and pinned policies leave an approved
plan staged; incompatible candidates are quarantined. Rechecking an already
active artifact is a no-op. Consumers run `check` after managed startup and
from their daily launchd or systemd-user timer.

`status` is read-only and loads no executable. Interrupted writes remain
visible until `apply --recover-pending --harness HARNESS` explicitly restores
the recorded prestate. An active broker given `--integration-upgrade-state`
derives a closed authority-set digest from certified per-harness status. It
binds that generation into route, policy, artifact, and profile-set identity,
so capabilities minted under another generation fail closed. No caller may
supply an authority digest directly.

See `examples/integration-catalog-codex.json` and
`examples/integration-update-policy-codex.json` for the closed configuration
shapes. Source, compatibility, and smoke runners exchange one JSON object on
standard input/output. They receive no inherited credentials, home directory,
or caller environment.

## Installation contract

`hindsight-memory install`, `upgrade`, `verify`, `rollback`, and `uninstall`
manage immutable releases on macOS LaunchAgents and Linux systemd-user. These
commands take no global `--state-dir`; their closed consumer configuration owns
every installed path and runtime binding, while `install` and `upgrade` receive
the release source and version through `--release-root` and `--version`. See
`examples/portable-consumer/` and the [adoption guide](docs/adoption.md).

After installation, the `hindsight-memory service` `status`, `start`,
`restart`, and `stop` subcommands own ordinary service control without changing
the installed release, manifests, or data root. Each command takes the
installation path through `--config`. `stop` disables the managed user services
so they remain stopped until an explicit `start` or `restart`; process failure
is still recovered by launchd or systemd while the service is running. Start
and restart reset managed API/UI component intent to the configured autostart
policy before requiring complete managed health. An optional `--profile`
asserts the named profile; it is accepted only when the installation manages
that single profile and never narrows a command to a subset of services.

An installation configuration contains:

- a schema version, consumer ID, platform, and `fresh` or `adopt` mode;
- separate absolute install, state, data, service, inventory, Python, `uvx`, and
  Zsh paths;
- one absolute, executable, SHA-256-bound credential resolver;
- nonempty managed services plus optional daily timers and health checks;
- only non-secret environment values and protected credential locators.

Services, timers, and health checks name `bin/...` release-relative
entrypoints. A `release://` environment value resolves within the
digest-verified active release. The example stack binds
`HINDSIGHT_EMBED_UVX=release://bin/hindsight-embed-uvx`; this release-owned
wrapper pins managed server commands to `hindsight-embed==0.8.4`.
The installer requires a working Python 3.11 or newer and validates the
configured absolute Python, `npx`, `uvx`, and Zsh executables' ownership, mode,
ancestry, and ACLs. The managed launcher binds those exact paths to release
wrappers and entrypoints without consulting ambient `PATH`; the embed wrapper
constructs its child `PATH` from only the validated `uvx` and `npx`
directories plus protected system directories.
Credential resolution receives one bounded
strict-JSON request and
must return one bounded strict-JSON response containing exactly the requested
environment names. Process-control names cannot be credential targets. The
launcher inherits only a narrow locale and user environment, discards resolver
stderr, and injects resolved values only into the authorized child process.

On macOS, `bin/hindsight-keychain-resolver` is the supplied universal native
resolver. It stores the data-plane token, mint authority, and UI access key as
generic-password items with an explicit Keychain ACL bound to the exact native
executable. The trusted-application list names no shared shell or Python
interpreter. Install the verified binary at its final stable path before running
`--initialize`. Bind that path and its SHA-256 digest in an approved consumer
configuration before creating any credential. `--status` reports presence
only. `--retire` deletes only credentials whose exact ACL matches that resolver
and never emits their values. `--retired-status` succeeds only when all three
credential items are absent.
`--self-test-acl` creates and deletes isolated canaries and verifies exact
trusted-application and authorization ACLs while `/usr/bin/python3` cannot read
the protected canary. The self-test requires the Command Line Tools
`/usr/bin/python3`.

The resolver is an executable capability for its owning account: any same-user
process able to execute it can request the fixed credential set. It prevents
ambient inheritance and direct interpreter access to Keychain; it is not a
caller-authenticating IPC service.

The ACL deliberately does not follow a replaced executable. Upgrading this
resolver therefore requires a separate credential-rotation plan that installs
and verifies the successor, coordinates all service consumers, rotates the
three values, and retains the prior binary until rollback is no longer needed.
Do not overwrite the stable path in place. Follow the ordered
[resolver rotation runbook](docs/adoption.md#rotate-the-macos-resolver).

Install and upgrade copy regular files into content-addressed read-only release
directories, atomically switch the active pointer, render only declared unit
files, and require managed health. Failed or interrupted transitions recover
the last verified prestate. Explicit rollback uses a compare-and-swap digest.
Rollback disables harness hooks before quiescing services. An installer-owned
authority service keeps them disabled while a legacy runtime is active, and
the launcher omits candidate-only migration extensions from that legacy
runtime. Hook activation requires repair or forward activation of the
authority release; rollback never restores direct upstream hooks.
Uninstall removes only unchanged installer-owned files and always preserves the
data root, consumer inputs, protected resolver, and external state root.

The following environment contract configures the managed stack inside those
services without editing reusable implementation.

| Surface | Required bindings | Optional bindings and defaults |
| --- | --- | --- |
| Runtime tools and state | `HINDSIGHT_EMBED_UVX`, `HINDSIGHT_EMBED_PYTHON`, `HINDSIGHT_EMBED_CONTROL_SERVER`, `HINDSIGHT_EMBED_STOP_HELPER`, `HINDSIGHT_MEMORY_CLI`, `HINDSIGHT_MEMORY_STATE_DIR`, `HINDSIGHT_MEMORY_BROKER_SOCKET`, `HINDSIGHT_EMBED_STATE_DIR` | `HINDSIGHT_EMBED_PROFILE_SLOT_DIR` defaults to `$HINDSIGHT_EMBED_STATE_DIR/profile-slots`; `HINDSIGHT_EMBED_DESIRED_STATE_DIR` defaults to `$HINDSIGHT_EMBED_STATE_DIR/desired` |
| Control-plane access key | Pass an out-of-band `access_key_resolver` callable to `ControlServer`; it must return 32 through 4096 bytes for each authentication request. A returned string is UTF-8 encoded, and the resolved bytes must represent only `[A-Za-z0-9._~+/=-]`. | The resolver is the sole authoritative binding. No environment variable, file, inline value, reusable default, or browser bootstrap takes precedence or acts as a fallback. CLI/API clients send the exact resolved bytes only as `Authorization: Bearer <access-key>`. |
| Fleet and profile | `HINDSIGHT_EMBED_PRIMARY_PROFILE`, `HINDSIGHT_EMBED_FLEET_PROFILES`, `HINDSIGHT_EMBED_AUTOSTART_DAEMON`, `HINDSIGHT_EMBED_AUTOSTART_UI` | `HINDSIGHT_EMBED_PROFILE` defaults to the primary profile |
| Provider preset | When a consumer exposes one named provider preset, it supplies all of `HINDSIGHT_EMBED_PROVIDER_PRESET_ID`, `HINDSIGHT_EMBED_PROVIDER_PRESET_LABEL`, `HINDSIGHT_EMBED_PROVIDER_PRESET_RUNTIME_PROVIDER`, `HINDSIGHT_EMBED_PROVIDER_PRESET_BASE_URL`, and `HINDSIGHT_EMBED_PROVIDER_PRESET_MODEL`. | Omit all five bindings to expose no preset. Partial presets fail closed. The preset ID must not collide with a built-in or upstream provider. The base URL must use HTTPS, or HTTP with a literal loopback host, and must contain no userinfo, query, fragment, whitespace, credential material, or port zero. Concrete alias, endpoint, runtime, and model values remain consumer-owned. |
| Hosts and ports | `HINDSIGHT_EMBED_CONTROL_PORT`, `HINDSIGHT_EMBED_CONTROL_HOSTNAME`, `HINDSIGHT_EMBED_API_BASE_PORT`, `HINDSIGHT_EMBED_UI_BASE_PORT`, `HINDSIGHT_EMBED_UI_HOSTNAME` | `HINDSIGHT_EMBED_PROFILE_<NORMALIZED_PROFILE>_{API,UI}_PORT` overrides the allocated base-plus-slot port for that profile. `HINDSIGHT_EMBED_API_PORT` and `HINDSIGHT_EMBED_UI_PORT` are resolved outputs for the selected profile, not fleet-wide overrides. Hostnames must be literal loopback addresses. |
| Managed-stack wait policy | none | `HINDSIGHT_EMBED_CONTROL_WAIT_SECONDS=30`, `HINDSIGHT_EMBED_DAEMON_WAIT_SECONDS=300`, `HINDSIGHT_EMBED_SIDECAR_WAIT_SECONDS=120`, `HINDSIGHT_EMBED_UI_WAIT_SECONDS=60`, `HINDSIGHT_MEMORY_BROKER_WAIT_SECONDS=30`, `HINDSIGHT_EMBED_STOP_WAIT_SECONDS=30`, `HINDSIGHT_EMBED_START_COOLDOWN_SECONDS=20`, `HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS=300` |
| Single-bank cleanup wait policy | none | The cleanup wrapper uses `HINDSIGHT_EMBED_DAEMON_WAIT_SECONDS=300` and `HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS=300` when those values are unset; explicit consumer values still take precedence. |
| Cleanup timeout policy | none | `HINDSIGHT_CLEANUP_ARCHIVE_TIMEOUT_SECONDS=3600`, `HINDSIGHT_CLEANUP_MIGRATION_TIMEOUT_SECONDS=3600` |
| Supervisor | `HINDSIGHT_EMBED_STACK_LIB` | `HINDSIGHT_EMBED_POLL_SECONDS=10`, `HINDSIGHT_EMBED_MAX_CONSECUTIVE_FAILURES=3` |
| Standalone launchd helper (`hindsight-embed-service`) | `HINDSIGHT_EMBED_STACK_LABEL`, `HINDSIGHT_EMBED_LEGACY_LABEL`, `HINDSIGHT_EMBED_SERVICE_MANIFEST`, `HINDSIGHT_EMBED_LEGACY_MANIFEST`, `HINDSIGHT_EMBED_SUPERVISOR`, `HINDSIGHT_EMBED_STACK_LIB`, `HINDSIGHT_EMBED_SERVICE_LOG` | Do not set these helper-specific bindings for portable installations; the portable manager owns launchd and systemd-user manifests directly. Portable services must still supply `HINDSIGHT_EMBED_STATE_DIR` through the runtime-tools contract above. |
| Canonical bank | `HINDSIGHT_BANK_ID` for the explicit single-bank cleanup/migration workflow | No reusable default; ordinary stack startup reads the bank binding from the selected Hindsight profile. |
| Migration inventory | `migration.artifact_dir` and `migration.proposal_log`, each a nonempty absolute path | `artifact_path` and `proposal_path` are compatibility aliases; supplying a canonical key and its alias with different values fails validation. |

## Provider runtime policy

`hindsight_memory_control_plane.provider_runtime` owns reusable LLM failover,
quota cooldown, exact provider matching, per-member concurrency and priority,
timeout, and retry mechanics. Consumers supply the closed policy shape shown in
`examples/provider-runtime-policy.json` and a protected credential resolver.
The policy contains OAuth-home locators, never resolved paths or credential
values.

The Hindsight strategy selects linear failover or tiered round-robin request
starts. Both modes use the policy's declared member order and try each member at
most once per request. Round-robin rotates only across quota-managed OAuth-home
members, then tries the remaining members as an ordered fallback tier. A
provider-reported usage reset is a hint, not a durable exclusion: the runtime
caps it to `default_usage_limit_cooldown_seconds` and probes the account again.
Member request timeouts use independent connect, pool, write, and read budgets;
the policy's `timeout_seconds` also bounds the complete provider member call,
including its concurrency-gate wait. Startup verification is bounded
independently, so an offline fallback cannot prevent the API from starting.

The repository policy is a schema example, not a deployable failover chain.
Its `example.invalid` endpoint is deliberately non-routable. Consumers must
replace every example member identity, endpoint, model, locator, and ordering
entry with a deployed provider that is valid on that installation before
installing the adapter.

For an OAuth-backed Codex member, the Hindsight provider credential field
contains only `provider-policy:<member-id>`. At construction time the adapter
resolves that member's `oauth-home:` locator, scopes `CODEX_HOME` while the
Codex client initializes, and restores the prior environment. No resolved home
is retained in the policy or logged. Other providers are matched by the exact
provider, model, and normalized base URL declared by the consumer.

Call `ProviderRuntimePolicy.load(...)`, then install
`HindsightProviderAdapter` with the protected resolver during Hindsight process
startup. Installation fails before changing Hindsight classes unless the
installed `hindsight-api` version and the policy both name an adapter-supported
version. The current adapter supports `0.8.4`, `0.9.0`, and `0.9.1`;
supporting another release requires an explicit compatibility update and
contract tests.

### Exact-drain candidate runtime snapshot

Release assembly must copy the exact provider runtime sources into the
candidate before the portable installer computes the candidate release
manifest:

```sh
tooling/hindsight/bin/hindsight-exact-drain-snapshot \
  --provider-runtime-root "$PROVIDER_RUNTIME_ROOT" \
  --candidate-library "$RELEASE_ROOT/lib"
```

The helper creates `lib/exact_drain_runtime/` once. It copies only
`sitecustomize.py` and `hindsight_llm_failover.py`, applies the exact bounded
Phase 1 entity-resolver and PostgreSQL-write overlays to the detached
candidate, and writes a canonical payload-free manifest for the provider and
both original/patched Hindsight sources. The resolver overlay removes candidate
metadata JSONB from the PostgreSQL trigram and full-bank projections, preserves
the scalar mention count used by full-bank overflow ranking, and fetches only
the candidate-induced cooccurrence graph. Trigram cooccurrences use
deterministic 128-ID first-endpoint batches; both lookup strategies emit
observational candidate, cooccurrence, and scoring breadcrumbs and bound each
server query wait to 120 seconds and each client wait to 125 seconds. Fuzzy
candidate lookup is sealed to at most ten candidate names per query. In-batch
fuzzy clustering excludes individual names over 4,096 code points and skips a
batch over 65,536 code points; excluded names remain exact-only and are not
truncated. The exact worker keeps SIGTERM ownership in worker-main instead of
allowing Uvicorn to replace the graceful-shutdown handler. The write overlay
keeps entity insert and reassertion compatible with the bound pre-`entity_kind`
database schema. These overlays are not a durable Phase 1 checkpoint or replay
receipt.

Release assembly runs this helper before `hindsight-portable-install`; the
existing release manifest then seals the completed snapshot and patched
candidate bytes. Assembly fails closed on unsupported or changed resolver or
PostgreSQL-write source. An interruption after the snapshot manifest but before
either atomic source replacement is retryable from the sealed original/patched
evidence. A current exact-drain plan binds this legacy-schema repair contract
and reads and executes only these candidate provider and Hindsight sources.
Exact operation-state mutations also lock the singleton migration-generation
row in their bounded serializable transaction, so terminal writes cannot race
another generation-triggered operation commit. Provider policy and credential
material remain external protected data and are never copied into the candidate
snapshot.

Current exact-drain workers route transport, connection, availability, and
timeout failures through the plan-bound retry counter. Deterministic provider
HTTP 400 responses fail immediately instead of consuming the same request
again. The third transient retry seals a terminal failure; no transient failure
bypasses that ceiling or creates an unbounded retry loop. Candidate error
records retain the exception type even when the exception text is empty.

Post-abort recovery preserves every completed exact-drain checkpoint. A schema
10 recovery plan selects only the reference worker's failed, owned-pending, and
processing rows; completed rows and all other cohort rows remain digest-exact.
It advances recovery epoch zero to one. Within that transaction, failed rows
may reset once while pending and processing rows preserve their retry counts.

Schema 11 permits an authenticated recovery from epoch one to epoch two.
Schema 12 permits one final recovery from epoch two to epoch three. For either
chained transition, the operator must provide `--prior-recovery-plan`;
planning authenticates the complete prior retry ledger and its application and
verification receipts. A chained recovery resets only failed rows from the
reference exact plan. Owned pending or processing rows are released without
resetting their retry counts or stored causes, while still-pending unowned rows
remain unchanged. The next exact-drain plan selects the complete pending set.
The epoch-three ledger caps cumulative attempts at sixteen, including the four
attempts made available by the final reset. Epoch four is not representable.
An existing nonterminal schema-11 application journal is not resumable. The
controller and worker require the authenticated schema-12 post-abort recovery
path; terminal reconciliation remains available without restarting task work.

A current exact-drain plan uses a fixed, nonrenewing execution window instead
of the legacy 24-hour lease. Planning recomputes the window from the selected
rows' persisted retry counts, effective concurrency, bounded phase timeouts,
possible retry waits, worker shutdown attempts, and transaction margins. The
window begins when the authorization receipt is created, never at resume, and
planning rejects a result over 14 days instead of truncating it.
The transaction margin reserves separate bounded claim and outcome
transactions for every remaining task attempt; shutdown transactions are
accounted for separately.
Current-plan retry and defer timestamps must be timezone-aware, no more than
one hour ahead, and strictly before the absolute execution deadline. The
worker rejects an out-of-window timestamp instead of clamping or persisting it.

Schema 12 treats operation-attempt and Phase 1 deadlines as retryable task
outcomes after bounded child-task quiescence. A task that completed before the
deadline observation wins the boundary race. Cancellation while a provider
request is queued or executing closes that request as cancellation rather than
as a provider failure or timeout. If an operation reaches its retry ceiling,
the terminal row and progress artifact preserve the underlying closed cause
instead of replacing it with a generic retry-ceiling cause.

Schema-12 status artifacts expose a closed, payload-free failure projection.
Each entry contains only `cause_family`, `error_digest`, and
`occurrence_count`. The server-side classifier distinguishes operation and
Phase 1 deadlines, database statement timeouts, provider queue and execution
timeouts, provider authentication/capacity/request/transport failures,
structured-output validation, upstream timeouts, database integrity failures,
cancellation, and unknown failures. Raw error text is never returned by the
status or monitor surface.

After post-abort verification, add `--recovery-plan "$POST_ABORT_PLAN"` to the
next read-only `operation-recovery drain plan` command. The fresh plan then
seals the recovery epoch, application and verification receipts, selected
checkpoint set, preserved row set, generation, and recovered IDs into its
approval digest. Omit `--recovery-plan` only when the entire cohort satisfies
the sealed initial-origin proof. Any post-abort mutation makes the verified
recovery plan mandatory, including after a recovered row later completes.

The managed exact-drain worker runtime is a separate trusted authority. For a
current plan, planning, apply, and the gated child each stream-hash the complete
canonical worker `site-packages` tree, including relative paths, entry types,
permission modes, sizes, and regular-file contents. The worker starts with
`-S`, so bound `.pth` files are inert. Symlinks, unsupported entries, tree
changes, missing files, additions, content changes, permission changes, and
imports outside the exact candidate, dependency, or Python runtime roots fail
closed. Legacy exact-drain plans retain their original evidence algorithm.

### Exact-drain progress

Exact-drain `status` and `monitor` expose payload-free lifecycle evidence.
`status` reads the live database under the operation-recovery and portable
manager locks. `monitor` reads durable progress without taking those locks:

```sh
hindsight-memory operation-recovery drain status \
  --config "$HINDSIGHT_INSTALLATION_CONFIG" \
  --candidate-release-root "$CANDIDATE_RELEASE_ROOT" \
  --candidate-release-identity "$CANDIDATE_RELEASE_IDENTITY" \
  --plan "$EXACT_DRAIN_PLAN"

hindsight-memory operation-recovery drain monitor \
  --config "$HINDSIGHT_INSTALLATION_CONFIG" \
  --candidate-release-root "$CANDIDATE_RELEASE_ROOT" \
  --candidate-release-identity "$CANDIDATE_RELEASE_IDENTITY" \
  --plan "$EXACT_DRAIN_PLAN"
```

For a current exact-drain plan, both commands keep top-level `expires_at` as
the approval expiry and include `execution_lease_status`,
`execution_lease_started_at`, `execution_lease_expires_at`, and
`execution_lease_remaining_seconds`. Before authorization, the lease status is
`not-authorized` and the other three lease values are null. A durably consumed
authorization without an application journal is `authorization-only`. Once
authorized, the lease status is `active` until its deadline and `expired`
afterward; remaining seconds clamp to zero.

Before authorization, `monitor` returns `not-started`. After the application
journal is durable but before the worker creates progress, it returns
`starting`. During and after an attempt it authenticates the application
journal, worker PID and process start identity, plan-selected task set, current
progress artifact, and archived prior-attempt evidence. It reports `running`
only while that exact process identity is live, `interrupted` after an
interrupted attempt, and `terminal` for an authenticated completed application.
Task stages, provider attempt counters, active request ages, cooldown
categories, prior-attempt evidence, and artifact digests are included. Neither
command exposes prompts, responses, error text, database URLs, credentials,
provider secrets, task payloads, or raw worker IDs.

Current progress also records a closed failure category, retryability, optional
HTTP status, and a digest of the bounded database-safe error. A closed
checkpoint projection reports only whether facts committed, committed document
and unit counts, and the last stored stage/counts. These fields let an operator
distinguish a provider rejection from a Phase 1 timeout and see committed work
without disclosing entity names, document IDs, payloads, results, or error text.
Legacy progress remains on its original schema and output contract.

Current workers also seal their startup lifecycle before the first claim.
`monitor` reports the worker status, current startup stage, stage timestamp,
and, after failure, the failure stage, exit code, closed category,
retryability, optional HTTP status, and error digest. Archived attempts retain
the same closed worker evidence. This distinguishes import, provider
activation, API import, guard installation, worker-main, and memory-engine
initialization failures without retaining raw exception text or task payloads.

If an exact task cannot persist its terminal state, progress records the closed
stage `failure.terminal-state` and failure category
`terminal_state_persistence` before requesting worker shutdown. If a cancelled
task does not quiesce within its plan-bound Phase 1 statement timeout, it records
`failure.nonquiescent` with category `nonquiescent_shutdown` and leaves the claim
intact for guarded recovery. Both records retain the last committed checkpoint
without exposing error text. Phase 1 cancellation waits for that bounded database
interval before deciding that release is unsafe. Public graceful shutdown retains
one polling interval of scheduling grace beyond the same bound. External signals
stop new claims, cancel the tracked task wrappers, and then use that same bounded
wait; shutdown never releases a claim while task code may still advance.

The legacy launchd label and manifest are migration bindings, not evidence that
a legacy installation exists. A fresh installation still supplies a distinct
legacy label and the absolute path where that legacy manifest would exist; the
path may be absent. `hindsight-embed-service install` validates, unloads, and
archives the legacy manifest only when it is present.

Explicit normalized per-profile port overrides have first precedence. Without an
override, each port is its base port plus that profile's persisted slot; a
nonzero persisted slot therefore never falls back to the bare base port.
`NORMALIZED_PROFILE` is the profile ID uppercased, with every character outside
`A-Z` and `0-9` replaced by `_`. For example, both `second-profile` and
`second.profile` normalize to `SECOND_PROFILE`; fleet preflight rejects enabled
profiles that collide after this transform before resolving their overrides.

Preflight the inventory before planning or migration work:

```sh
"$HINDSIGHT_MEMORY_CLI" --state-dir "$HINDSIGHT_MEMORY_STATE_DIR" validate \
  --inventory /absolute/path/to/inventory.json
```

This validates both required migration bindings and the closed inventory
contract. Discovery separately creates or validates the artifact directory as a
current-user-owned `0700` directory, rejects symlink components and any Git
worktree boundary, and rechecks the proposal-log and completion-marker snapshots
before publishing artifacts.

Missing required values, invalid booleans or ports, non-loopback hosts, unsafe
paths, profile collisions, and absent bank bindings for cleanup fail before a
service or migration is started.

The managed Embed control-server wrapper and stack share the desired-state
directory. Explicit daemon and UI stops persist for the current login session,
so supervisor reconciliation does not undo operator intent. Stopping the API
also stops its dependent UI intent; stopping only the UI leaves the API
running. A clean service start or restart resets that intent before starting
the fleet; a new login initializes the configured autostart policy. Consumers
bind the reusable control-server helper through
`HINDSIGHT_EMBED_CONTROL_SERVER` and do not fork its lifecycle logic into
machine configuration. Managed UI startup also prepares the selected
published control-plane package under a no-credential scope. The preparer
accepts only the exact authenticated locale-routing contract it can repair,
applies that change atomically, and rejects unknown package shapes before the
UI receives credentials or starts.

## Migration safety

`hindsight-memory migration replay` exposes separate `plan`, `apply`, `status`,
`verify`, and `closeout` phases for the exact `codex` to `engineering`
accidental-bank repair. Planning freezes a chronological, content-digested raw
document manifest without copying payloads into the plan. Apply submits each
source document through the normal asynchronous retain API with deterministic
target IDs and checkpoints a generation-chained receipt after every successful
operation. Verify requires complete receipt, operation, and target-content
coverage.

Closeout is separately prepared and approved. Its digest binds the verified
replay, encrypted restore-tested exports for both banks, the restore-tested
full-schema backup, the exact pre-delete bank set, and the server generation.
Closeout apply deletes only literal `codex` and proves that every other bank
remains.

The replay lifecycle is explicit:

```sh
hindsight-memory --state-dir STATE migration replay plan --inventory INVENTORY --profile PROFILE --token-env TOKEN_ENV --output replay-plan.json
hindsight-memory --state-dir STATE migration replay apply --inventory INVENTORY --profile PROFILE --token-env TOKEN_ENV --plan replay-plan.json --backup-evidence backup-evidence.json --approval-digest APPROVED_REPLAY_DIGEST --receipts replay-receipts.json
hindsight-memory --state-dir STATE migration replay status --plan replay-plan.json --receipts replay-receipts.json
hindsight-memory --state-dir STATE migration replay verify --inventory INVENTORY --profile PROFILE --token-env TOKEN_ENV --plan replay-plan.json --receipts replay-receipts.json --output replay-verification.json
hindsight-memory --state-dir STATE migration replay closeout --inventory INVENTORY --profile PROFILE --token-env TOKEN_ENV --plan replay-plan.json --receipts replay-receipts.json --verification replay-verification.json --backup-evidence backup-evidence.json --prepare --output replay-closeout.json
hindsight-memory --state-dir STATE migration replay closeout --inventory INVENTORY --profile PROFILE --token-env TOKEN_ENV --plan replay-plan.json --receipts replay-receipts.json --verification replay-verification.json --backup-evidence backup-evidence.json --closeout-plan replay-closeout.json --approval-digest APPROVED_DIGEST --output replay-closeout-receipt.json
```

The replay approval digest is the canonical digest of the replay-plan digest
and the validated backup-evidence digest. Apply rejects missing, unencrypted,
or untested rollback evidence before its first target read or write.

Only `status` is offline. Read-only migration discovery requires a server-backed
opaque monotonic generation captured before and after the complete discovery
read. It also recomputes the configured inventory, policy, native-hook,
activation, and schedule state before and after the snapshot and requires an
exact match with the server-recorded controller state. If either authority is
unavailable or changes, discovery and replay fail closed. The generic
desired-state `apply` command does not authorize a migration replay or closeout.

Generated plans, credentials, profile state, control tokens, logs, archives, and other runtime artifacts must not enter this repository.

The consuming installation must pass the exact out-of-band resolver callable
for the machine-local control-plane access key. The resolver is evaluated for
each authentication request and has no ambient fallback or precedence chain.
That key is independent of
every profile bearer token and is resolved directly by the controller; it is
never forwarded to a Hindsight backend, browser, harness, or child-process
argument vector and must never be written to rendered files or logs.
