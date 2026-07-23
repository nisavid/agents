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
retention, and rolls back the controller-owned fields when a post-activation
check fails. Claude's upstream knowledge tools are disabled with its verified
empty-MCP-server mode when this path is activated.

`hindsight-memory harness-config stage` and `plan` persist inactive artifacts
and destination-bound activation records; `status` is read-only. Only `apply`
and `rollback` mutate controller-owned fields in real native configuration
files. The secret-free approved activation record binds the prestate and target
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
configured absolute Python, `uvx`, and Zsh executables' ownership, mode,
ancestry, and ACLs. The managed launcher binds those exact paths to release
wrappers and entrypoints without consulting `PATH`.
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
version. The current adapter supports only `0.8.4`; supporting another release
requires an explicit compatibility update and contract tests.

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
so supervisor reconciliation does not undo operator intent. A clean service
restart resets that intent before starting the fleet; a new login initializes
the configured autostart policy. Consumers bind the reusable control-server
helper through `HINDSIGHT_EMBED_CONTROL_SERVER` and do not fork its lifecycle
logic into machine configuration.

## Migration safety

Read-only migration discovery requires a server-backed opaque monotonic generation captured before and after the complete discovery read. If that generation is unavailable or changes, discovery fails closed. Do not run live migration mutations or mark the live-discovery checklist complete without satisfying that exact gate.

The current CLI can publish only an immutable, unapproved discovery shadow
plan. It has no migration apply or cutover command. Follow the status and gates
in [Migration readiness](docs/migration-readiness.md);
the generic desired-state `apply` command does not authorize a migration
shadow plan.

Generated plans, credentials, profile state, control tokens, logs, archives, and other runtime artifacts must not enter this repository.

The consuming installation must pass the exact out-of-band resolver callable
for the machine-local control-plane access key. The resolver is evaluated for
each authentication request and has no ambient fallback or precedence chain.
That key is independent of
every profile bearer token and is resolved directly by the controller; it is
never forwarded to a Hindsight backend, browser, harness, or child-process
argument vector and must never be written to rendered files or logs.
