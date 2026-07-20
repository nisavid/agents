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
