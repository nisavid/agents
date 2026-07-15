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

## Installation contract

Consumers install or link these files from a checkout of `nisavid/agents`.
Consuming configuration supplies machine values through this environment
contract without editing reusable implementation.

| Surface | Required bindings | Optional bindings and defaults |
| --- | --- | --- |
| Runtime tools and state | `HINDSIGHT_EMBED_UVX`, `HINDSIGHT_EMBED_PYTHON`, `HINDSIGHT_EMBED_CONTROL_SERVER`, `HINDSIGHT_EMBED_STOP_HELPER`, `HINDSIGHT_MEMORY_CLI`, `HINDSIGHT_MEMORY_STATE_DIR`, `HINDSIGHT_MEMORY_BROKER_SOCKET`, `HINDSIGHT_EMBED_STATE_DIR` | `HINDSIGHT_EMBED_PROFILE_SLOT_DIR` defaults to `$HINDSIGHT_EMBED_STATE_DIR/profile-slots`; `HINDSIGHT_EMBED_DESIRED_STATE_DIR` defaults to `$HINDSIGHT_EMBED_STATE_DIR/desired` |
| Control-plane access key | Exact out-of-band secret locator for a resolver that returns 32 through 4096 bytes. A string is UTF-8 encoded, but the transmitted and resolved bytes must represent only `[A-Za-z0-9._~+/=-]`. | CLI/API clients send those exact bytes only as `Authorization: Bearer <access-key>`; there is no inline value, reusable default, or browser-auth bootstrap. |
| Fleet and profile | `HINDSIGHT_EMBED_PRIMARY_PROFILE`, `HINDSIGHT_EMBED_FLEET_PROFILES`, `HINDSIGHT_EMBED_AUTOSTART_DAEMON`, `HINDSIGHT_EMBED_AUTOSTART_UI` | `HINDSIGHT_EMBED_PROFILE` defaults to the primary profile |
| Hosts and ports | `HINDSIGHT_EMBED_CONTROL_PORT`, `HINDSIGHT_EMBED_CONTROL_HOSTNAME`, `HINDSIGHT_EMBED_API_BASE_PORT`, `HINDSIGHT_EMBED_UI_BASE_PORT`, `HINDSIGHT_EMBED_UI_HOSTNAME` | `HINDSIGHT_EMBED_PROFILE_<NORMALIZED_PROFILE>_{API,UI}_PORT` overrides the allocated base-plus-slot port for that profile. `HINDSIGHT_EMBED_API_PORT` and `HINDSIGHT_EMBED_UI_PORT` are resolved outputs for the selected profile, not fleet-wide overrides. Hostnames must be literal loopback addresses. |
| Wait policy | none | `HINDSIGHT_EMBED_CONTROL_WAIT_SECONDS=30`, `HINDSIGHT_EMBED_DAEMON_WAIT_SECONDS=120`, `HINDSIGHT_EMBED_SIDECAR_WAIT_SECONDS=120`, `HINDSIGHT_EMBED_UI_WAIT_SECONDS=60`, `HINDSIGHT_MEMORY_BROKER_WAIT_SECONDS=30`, `HINDSIGHT_EMBED_STOP_WAIT_SECONDS=30`, `HINDSIGHT_EMBED_START_COOLDOWN_SECONDS=20`, `HINDSIGHT_EMBED_LIFECYCLE_COMMAND_TIMEOUT_SECONDS=30` |
| Cleanup timeout policy | none | `HINDSIGHT_CLEANUP_ARCHIVE_TIMEOUT_SECONDS=3600`, `HINDSIGHT_CLEANUP_MIGRATION_TIMEOUT_SECONDS=3600` |
| Supervisor | `HINDSIGHT_EMBED_STACK_LIB` | `HINDSIGHT_EMBED_POLL_SECONDS=10`, `HINDSIGHT_EMBED_MAX_CONSECUTIVE_FAILURES=3` |
| Launch service | `HINDSIGHT_EMBED_STACK_LABEL`, `HINDSIGHT_EMBED_LEGACY_LABEL`, `HINDSIGHT_EMBED_SERVICE_MANIFEST`, `HINDSIGHT_EMBED_LEGACY_MANIFEST`, `HINDSIGHT_EMBED_SUPERVISOR`, `HINDSIGHT_EMBED_STACK_LIB`, `HINDSIGHT_EMBED_STATE_DIR`, `HINDSIGHT_EMBED_SERVICE_LOG` | none |
| Canonical bank | `HINDSIGHT_BANK_ID` for the explicit single-bank cleanup/migration workflow | No reusable default; ordinary stack startup reads the bank binding from the selected Hindsight profile. |
| Migration inventory | `migration.artifact_dir` and `migration.proposal_log`, each a nonempty absolute path | `artifact_path` and `proposal_path` are compatibility aliases; supplying a canonical key and its alias with different values fails validation. |

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

Generated plans, credentials, profile state, control tokens, logs, archives, and other runtime artifacts must not enter this repository.

The consuming installation must provide an exact out-of-band secret locator
for the machine-local control-plane access key. That key is independent of
every profile bearer token and is resolved directly by the controller; it is
never forwarded to a Hindsight backend, browser, harness, or child-process
argument vector and must never be written to rendered files or logs.
