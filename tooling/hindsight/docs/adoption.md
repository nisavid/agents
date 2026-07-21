# Adopt the Hindsight Control-Plane Architecture

Use this guide to bind a fresh or existing Hindsight installation to the
reusable tooling in this repository. Adoption changes where implementation is
owned and how the local services are managed. It does not copy, rename, merge,
or delete Hindsight banks, profiles, documents, facts, or observations.

## Platform status

| Environment | Status |
| --- | --- |
| macOS | The reusable contract is available, but the operator must provide its own consumer configuration, wrappers, and LaunchAgent manifest. |
| Linux, including CachyOS | The controller modules can be consumed independently, but the managed service command is not portable: it currently uses launchd and macOS-specific trust checks. No systemd installation path is available. |

The repository does not yet provide a versioned installer, upgrader, or
uninstaller. A consumer configuration repository owns the installation steps
below. Portable installation and a Linux service backend are tracked in
[`nisavid/agents` issue #22](https://github.com/nisavid/agents/issues/22).

The [`nisavid/dotfiles` consumer runbook](https://github.com/nisavid/dotfiles/blob/main/docs/HINDSIGHT.md)
is one machine-specific example; it owns that consumer's values, commands, and
current migration boundary.

## Choose the installation identity

Choose and record these values before rendering any files:

- an immutable or explicitly versioned `nisavid/agents` checkout;
- the Hindsight profile and canonical bank;
- loopback API, UI, and control ports;
- private runtime and state directories;
- the control-plane access-key resolver;
- managed and legacy service labels and manifest paths;
- the enabled profile fleet and daemon/UI autostart policy.

Keep these values in the consumer repository. Do not add machine inventory,
credentials, private catalog data, runtime state, or generated migration
artifacts here.

## Bind the reusable sources

Install regular consumer-owned launchers, or equivalent trusted wrappers, that
delegate to the selected checkout:

- `tooling/hindsight/bin/`
- `tooling/hindsight/lib/`
- `tooling/hindsight/libexec/`
- `tooling/hindsight/skills/`

Supply the complete environment contract in the
[installation reference](../README.md#installation-contract). Partial provider
presets, invalid booleans, non-loopback hosts, colliding profile names, unsafe
paths, and missing service values fail during preflight.

The access-key resolver is an out-of-band callable. Do not place its resolved
secret in a rendered file, process argument, repository, log, or fallback
environment variable.

## Configure a fresh Hindsight profile

Configure the selected upstream Hindsight profile interactively, then set its
canonical bank explicitly. The consumer decides how to invoke and version
`hindsight-embed`; for example:

```zsh
uvx hindsight-embed configure --profile "$profile" --port "$api_port"
uvx hindsight-embed profile set-env "$profile" HINDSIGHT_BANK_ID "$bank_id"
```

Interactive configuration keeps provider credentials out of process arguments.

For an existing installation, do not run either command. Inspect the selected
profile and the consumer's canonical-bank binding without printing provider
credentials. Fail adoption on any identity mismatch. Changing the profile or
bank is a separately approved data migration, not an installation repair.

## Render and preflight the consumer configuration

Render the wrappers and service manifest without starting services. Validate
the inventory before planning or migration work:

```zsh
"$HINDSIGHT_MEMORY_CLI" --state-dir "$HINDSIGHT_MEMORY_STATE_DIR" validate \
  --inventory /absolute/path/to/inventory.json
```

Inspect the rendered manifest and wrappers in a temporary destination. Applying
consumer configuration must not activate harness memory, migrate data, or run a
controller-directed mutation.

## Stage broker-mediated harness integrations

Start the broker in active inventory-backed mode only after the consumer can
resolve both the data-plane token and mint authority without rendering either
secret. The active broker must report the expected inventory, profile-set,
route, policy, and artifact digests before a harness session is minted.

Render the Codex, Claude Code, and Cursor controller artifacts into one
content-addressed staging generation. The rendered hooks call
`hindsight-memory harness`; they contain no endpoint, bank, bearer token,
signing material, envelope, or session capability. Keep the artifacts inactive
while checking their native hook schemas and adapter self-tests.

CLI consumers must start through `hindsight-memory harness <harness> launch`.
The launcher mints the bounded session, transfers its one-use handle to the
private bridge over an inherited descriptor, and gives the harness only the
bridge locator. GUI consumers use `stage-gui`; it reserves the session identity
before minting and writes a controller-only, user-private one-use envelope
carrying the broker-issued expiry. The first hook atomically consumes that
envelope, starts the bridge, and publishes a non-secret locator for later hooks.
Replays fail, expired abandoned envelopes may be safely replaced, and final
close removes the locator. The bridge exchanges its handle only when the first
hook arrives. If startup or locator publication fails after consumption, the
controller terminates any bridge, exchanges and closes the minted session, and
publishes no locator. Recovery requires a fresh `stage-gui`; the consumed
envelope is never restored or replayed.

Activation requires an approved digest-bound plan, unchanged inventory,
policy, artifact, and prestate digests, healthy broker and profile checks, and a
passing adapter self-test. It merges only controller-owned hooks, settings, and
tools. A failed readback or postcheck restores the recorded controller-owned
prestate while preserving unrelated configuration.

The native event coverage is intentionally harness-specific:

- Codex uses prompt recall, synchronous stop-hook submission to the broker's
  asynchronous write queue, and pre-compaction checkpoints. The controller
  launcher closes CLI sessions because Codex does not currently expose a
  session-end hook.
- Claude Code adds its native session-end close hook and disables upstream
  knowledge tools together with upstream automatic recall and retention. It
  sets the upstream `enableKnowledgeTools=false` contract, which keeps the MCP
  process healthy in its verified empty-server mode while advertising no
  knowledge tools.
- Cursor uses startup recall, stop and pre-compaction checkpoints, session-end
  close, and wrapped explicit tools.

All three artifacts expose controller-owned recall, reflect, mental-model,
and status tools. At a clean stop checkpoint, the controller derives a bounded
outcome only when the final structured transcript record is an assistant result
and retains it separately; harness input cannot supply arbitrary outcome
content, routing, scopes, or tags. Close always attempts revocation, even when a
pending checkpoint cannot be reconciled, and reports that condition visibly.

Do not disable the existing direct integration until all staged artifacts pass
the activation gates and a rollback snapshot exists. Do not activate this
section as part of ordinary consumer rendering.

## Start and verify the macOS service

For a launchd consumer, install the rendered service and verify every managed
surface:

```zsh
hindsight-embed-service install
hindsight-embed-service status
```

A successful adoption reports the launchd service as loaded and the broker,
control service, configured profile APIs, configured UIs, and fleet as healthy.
It also leaves the canonical and last-known-good manifests identical and no
staged manifest behind.

If the installation contains legacy banks or profiles that must be combined or
renamed, stop here. Architecture adoption preserves them in place. Continue
only after the gates in
[Migration readiness](migration-readiness.md) are
satisfied.
