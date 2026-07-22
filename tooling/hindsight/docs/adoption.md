# Adopt the Hindsight Control-Plane Architecture

Use this guide to install the reusable control plane on macOS with LaunchAgents
or on Linux, including CachyOS, with systemd user units. The same lifecycle can
create a fresh installation or adopt an existing Hindsight data root without
changing its identity.

Adoption changes code ownership and managed service wiring. It does not copy,
rename, merge, or delete Hindsight banks, profiles, documents, facts, or
observations.

## Install platform prerequisites

Start from a current OS installation and a normal login for the account that
will own Hindsight. Both platforms require:

- Git, Python 3.11 or newer, Zsh with `zsh/stat` and `zsh/system`, `curl`,
  `lsof`, `uv`/`uvx`, and Node.js with `npm`/`npx`; the declared `npx`
  executable and its ancestry must not be group or world writable;
- an absolute Python 3.11-or-newer executable whose file and non-sticky
  ancestors are not group or world writable, plus an immutable checkout or
  release tree of this repository;
- a working Hindsight Embed profile runtime;
- a protected credential resolver backed by `pass`, macOS Keychain, or Secret
  Service; and
- a user service-manager session. Do not install the control plane as root.

On a clean current macOS installation, install Apple's Command Line Tools and a
current Python and `uv`. If `brew` is absent, install Homebrew using its
[official macOS instructions](https://brew.sh/), then run:

```zsh
xcode-select --install
brew install git python pass gnupg
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh
uv_executable="$HOME/.local/bin/uv"
uvx_executable="$HOME/.local/bin/uvx"
"$uv_executable" python install '3.14'
"$uv_executable" tool install --force 'hindsight-embed==0.8.4'
/usr/bin/zsh -fc 'zmodload zsh/stat && zmodload zsh/system'
/bin/launchctl print "gui/$UID" >/dev/null
managed_python="$("$uv_executable" python find --managed-python --resolve-links '3.14')"
embed_python="$("$uv_executable" tool dir)/hindsight-embed/bin/python3"
"$managed_python" -I -c 'import sys; assert sys.version_info >= (3, 11)'
"$embed_python" -I -c 'import hindsight_embed'
"$uvx_executable" 'hindsight-api@0.8.4' --help >/dev/null
```

Wait for the Command Line Tools installer to finish before running the remaining
commands. Use `managed_python` as the consumer configuration's
`python_executable` and `embed_python` as `HINDSIGHT_EMBED_PYTHON`; Homebrew's
group-writable Cellar ancestry does not satisfy the managed-runtime trust
contract. The API and UI commands stage the exact child runtimes before the
first managed activation. A GUI login owns the LaunchAgent domain;
remote-only sessions without that domain cannot perform native LaunchAgent
acceptance.

Install Node.js for macOS from a system-managed package that provides a
protected `npx` executable, normally `/usr/local/bin/npx`. Do not add Homebrew
or an interactive version-manager directory to the managed service `PATH`.
Declare the protected executable as `npx_executable`; the launcher adds only its
validated directory to the child process path.

```zsh
npx_executable=/usr/local/bin/npx
"$npx_executable" -y '@vectorize-io/hindsight-control-plane@0.8.4' --help >/dev/null
```

On a clean current CachyOS installation, update the system and install the
distribution packages:

```zsh
sudo pacman -Syu --needed git python zsh curl lsof uv nodejs npm pass gnupg
uv python install '3.14'
uv tool install --force 'hindsight-embed==0.8.4'
zsh -fc 'zmodload zsh/stat && zmodload zsh/system'
python -c 'import sys; assert sys.version_info >= (3, 11)'
systemctl --user show-environment >/dev/null
managed_python="$(uv python find --managed-python --resolve-links '3.14')"
embed_python="$(uv tool dir)/hindsight-embed/bin/python3"
uvx_executable=/usr/bin/uvx
"$managed_python" -I -c 'import sys; assert sys.version_info >= (3, 11)'
"$embed_python" -I -c 'import hindsight_embed'
"$uvx_executable" 'hindsight-api@0.8.4' --help >/dev/null
npx_executable=/usr/bin/npx
"$npx_executable" -y '@vectorize-io/hindsight-control-plane@0.8.4' --help >/dev/null
```

The account must have a real `systemd --user` manager and session bus. Enable
linger with `loginctl enable-linger "$USER"` only when services must remain
available after logout. A container PID 1 or a root system manager is not a
substitute for the account's user manager.

Keep the staged Embed, API, and UI versions aligned at `0.8.4`. Changing any of
these versions is a separate server or integration upgrade and requires an
explicit plan.

After installing and configuring the consumer, run the native gated lifecycle
test on the target host. It creates isolated manifests and data, exercises the
real user service manager, and removes its jobs and files:

```zsh
# macOS
env HINDSIGHT_PORTABLE_PLATFORM_ACCEPTANCE=1 \
  HINDSIGHT_PORTABLE_ACCEPTANCE_PLATFORM=launchd \
  HINDSIGHT_PORTABLE_ACCEPTANCE_MANAGED_PYTHON="$managed_python" \
  "$managed_python" -m unittest \
  tooling.hindsight.tests.test_hindsight_memory_portable_platform_acceptance -v

# CachyOS
env HINDSIGHT_PORTABLE_PLATFORM_ACCEPTANCE=1 \
  HINDSIGHT_PORTABLE_ACCEPTANCE_PLATFORM=systemd-user \
  HINDSIGHT_PORTABLE_ACCEPTANCE_MANAGED_PYTHON="$managed_python" \
  "$managed_python" -m unittest \
  tooling.hindsight.tests.test_hindsight_memory_portable_platform_acceptance -v
```

The systemd user timer makes its first check two minutes after that account's
user manager starts, then follows the configured daily calendar schedule.

## Prepare the consumer configuration

Start from `examples/portable-consumer/`. Copy the inventory and the matching
platform installation file into a private consumer configuration repository.
Replace every example path and choose:

- one immutable release tree and version;
- one Hindsight profile and its existing or intended canonical bank;
- `fresh` for a new, empty data root or `adopt` for an existing data root;
- private install, state, and data roots; for `systemd-user`, use the unit
  directory under the user manager's `$XDG_CONFIG_HOME` (defaulting to
  `~/.config/systemd/user`), while launchd may bootstrap owned plists from the
  configured service root;
- `bin/...` entrypoints for installer-managed executables and scripts, plus
  `release://...` environment values for release-owned runtime dependencies;
- a protected credential resolver and opaque credential locators;
- integration catalogs, policies, and digest-bound compatibility runners.

Keep machine inventory, locators, service values, runtime state, and generated
artifacts in the consumer repository or protected local state. Keep resolved
credentials out of configuration, arguments, logs, and source control.

The installer validates a closed JSON schema. Use paths such as
`bin/hindsight-memory`, never absolute paths or `release://` values, for
release-owned entrypoints. Use `release://bin/hindsight-embed-uvx` for the
`HINDSIGHT_EMBED_UVX` environment binding; that protected wrapper always runs
`hindsight-embed==0.8.4`. `release://` environment values resolve only inside
the verified active release. The credential resolver must match its declared
SHA-256 digest and implement the request and response protocol in the portable
consumer example.

The installer queries the systemd user manager for its `XDG_CONFIG_HOME` and
requires `service_root` to match that manager-visible unit directory before any
filesystem or service mutation. A custom `SYSTEMD_UNIT_PATH` fails closed and
requires an explicit installation plan because it can replace or reorder the
normal search path.

## Establish the Hindsight identity

For a fresh installation, configure the selected upstream profile
interactively, then bind its canonical bank explicitly. For example:

```zsh
profile='replace-with-profile-name'
api_port=7979
bank_id=engineering
uvx_executable=/absolute/path/to/uvx

HINDSIGHT_EMBED_UVX_EXECUTABLE="$uvx_executable" \
  tooling/hindsight/bin/hindsight-embed-uvx hindsight-embed configure \
  --profile "$profile" --port "$api_port"
HINDSIGHT_EMBED_UVX_EXECUTABLE="$uvx_executable" \
  tooling/hindsight/bin/hindsight-embed-uvx hindsight-embed profile set-env \
  "$profile" HINDSIGHT_BANK_ID "$bank_id"
```

Interactive setup keeps provider credentials out of process arguments. The
configured profile may exist outside the installer-managed data root, but the
declared fresh data root must be empty.

For adoption, do not run either command. Inspect the existing profile, bank,
ports, and data root without printing provider credentials. Set
`installation_mode` to `adopt` and point `data_root` at the existing database
root. The installer records its filesystem identity, rechecks that digest
immediately before first service activation, and refuses later lifecycle
operations if the identity changes. This proves that the declared root was not
replaced; it does not infer the Hindsight profile, bank, schema, or content
identity. The operator must establish those semantic identities through the
read-only preflight. A mismatch is a migration decision, not an installation
repair.

## Validate before installation

Validate the inventory with a private controller state directory:

```zsh
tooling/hindsight/bin/hindsight-memory \
  --state-dir /absolute/private/state \
  validate \
  --inventory /absolute/path/to/inventory.json
```

Read the installation configuration through the CLI without supplying the
global `--state-dir`; portable lifecycle commands obtain all paths from
`--config`.

Check that no Hindsight or harness session is active before adopting an
existing service. Snapshot the current service manifests, hook registrations,
provider state, and authentication state. Do not disable a direct harness
integration until the controller-owned adapters are staged, tested, and ready
for an atomic activation with a rollback snapshot.

## Install and verify

Install an immutable release from that release's installer runtime:

```zsh
candidate_cli=/absolute/path/to/release/tooling/hindsight/bin/hindsight-memory
"$candidate_cli" install \
  --config /absolute/path/to/installation.json \
  --release-root /absolute/path/to/release/tooling/hindsight \
  --version 1.0.0

installed_cli=/absolute/install/root/bin/hindsight-memory

"$installed_cli" verify \
  --config /absolute/path/to/installation.json
```

The installer copies regular release files into a content-addressed immutable
directory, publishes an atomic active pointer, renders only installer-owned
LaunchAgent or systemd-user files, starts the managed services and timers, and
runs every declared health check. `verify` rechecks the release, configuration,
inventory, service manifests, data identity, and managed health.

The managed supervisor health check covers the control service, broker,
configured APIs and UIs, and the complete fleet. A launchd integration job runs
once when loaded and at its configured daily time. A systemd-user timer runs two
minutes after its user manager starts and at its configured daily time. Create
one timer per enabled harness catalog when the catalogs differ.
Set the stack health-check deadline long enough for asynchronous API, UI,
control, and broker readiness. The portable manager retries the isolated check
until that deadline and rolls back the generation if readiness never converges.

## Upgrade and roll back

Upgrade from another immutable release tree:

```zsh
candidate_cli=/absolute/path/to/new-release/tooling/hindsight/bin/hindsight-memory
"$candidate_cli" upgrade \
  --config /absolute/path/to/installation.json \
  --release-root /absolute/path/to/new-release/tooling/hindsight \
  --version 1.1.0 \
  --expected-current-binding-generation-digest \
  "$current_binding_generation_digest"
```

Copy the current binding-generation digest from `verify`. This compare-and-swap
guard admits an intentional configuration or inventory update while rejecting
a stale plan before service-manager mutation. The installer records the prior
verified release as last known good. It restores that generation automatically
if rendering, service activation, or health verification fails. An interrupted
transition is recovered before the next lifecycle command.
Upgrade must run from the candidate release's CLI so its installer runtime,
launcher payloads, and service rendering become authoritative in the same
transaction. The installed CLI remains available for `install`, `verify`,
`rollback`, and `uninstall` while an install, upgrade, or rollback transaction
is pending; other commands fail closed until recovery completes.

For an explicit compare-and-swap rollback, copy the current release digest from
`verify` and run:

```zsh
"$installed_cli" rollback \
  --config /absolute/path/to/installation.json \
  --expected-current-release-digest "$current_release_digest"
```

Rollback fails if the active digest changed or no distinct last-known-good
release exists.

## Uninstall without deleting data

```zsh
"$installed_cli" uninstall \
  --config /absolute/path/to/installation.json
```

Uninstall stops the managed units and removes only unchanged installer-owned
service files and release state. It preserves the data root, consumer
configuration, inventory, credential resolver, and external state root. Any
unowned path or owned-file drift fails closed for operator review.

An uninstall transaction renames the installation root before deleting it, so
the installed CLI intentionally disappears during that narrow window. If the
process is interrupted, rerun `uninstall` with the trusted CLI from the same
pinned release tree used to install or upgrade:

```zsh
bootstrap_cli=/absolute/path/to/pinned/release/tooling/hindsight/bin/hindsight-memory
"$bootstrap_cli" uninstall \
  --config /absolute/path/to/installation.json
```

The external uninstall journal restores a prepared transaction or finishes a
committed removal before reporting the installation absent.

## Activate broker-mediated harnesses

Start the broker only after the consumer can resolve both the data-plane token
and mint authority through protected locators. Its status must report the
expected inventory, profile-set, route, policy, artifact, and integration
authority digests.

Render Codex, Claude Code, and Cursor controller artifacts into a disabled,
content-addressed staging generation. Validate native hook schemas and run the
adapter self-tests. Activation requires an approved digest-bound plan, unchanged
prestate and policy digests, healthy runtime checks, and a rollback snapshot.
It changes only controller-owned hook fields and preserves unrelated plugin
configuration.

CLI consumers start through
`hindsight-memory --state-dir /absolute/private/state harness <harness> launch`.
GUI consumers use a staged one-use envelope consumed by the first hook. Hooks
receive only a private bridge locator; endpoint, bank, bearer token, signing
material, envelope, and session capability remain outside the harness.

After activation, run fresh Codex, Claude Code, and Cursor smoke sessions and
verify recall, checkpoints, explicit reflect, mental models, close and
revocation, durable watermarks, and writes to the canonical `engineering` bank
only.

If the installation contains legacy banks or profiles that must be combined,
renamed, or retired, stop here. Follow [Migration readiness](migration-readiness.md).
No portable install, adoption, harness activation, or ordinary controller apply
authorizes data migration.
