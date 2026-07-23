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
- the bundled native Keychain resolver on macOS, or a protected resolver backed
  by `pass` or Secret Service on Linux; and
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

Install the macOS resolver at a stable path before rendering the consumer
configuration. Verify the repository artifact, run its isolated ACL probe, and
record the installed digest without printing a credential:

```zsh
set -euo pipefail

checkout_root=/absolute/path/to/verified/agents-release
resolver_source="$checkout_root/tooling/hindsight/bin/hindsight-keychain-resolver"
resolver_directory="$HOME/.local/libexec"
resolver_target="$resolver_directory/hindsight-keychain-resolver"
expected_resolver_sha256=replace-with-approved-release-sha256

user_id="$(/usr/bin/id -u)"
validate_protected_directory() {
  local ancestor="$1" ancestor_owner ancestor_mode acl_entry
  local -a acl_listing
  while true; do
    [[ -d "$ancestor" && ! -L "$ancestor" ]]
    ancestor_owner="$(/usr/bin/stat -f '%u' "$ancestor")"
    [[ "$ancestor_owner" == "$user_id" || "$ancestor_owner" == 0 ]]
    ancestor_mode="$(/usr/bin/stat -f '%Lp' "$ancestor")"
    (( (8#$ancestor_mode & 8#022) == 0 ))
    acl_listing=("${(@f)$(/bin/ls -lde "$ancestor")}")
    for acl_entry in "${acl_listing[@]}"; do
      [[ ! "$acl_entry" =~ \
        '^[[:space:]]*[0-9]+:.*[[:space:]]allow[[:space:]]' ]]
    done
    [[ "$ancestor" == / ]] && break
    ancestor="${ancestor:h}"
  done
}

resolver_parent="${resolver_directory:h}"
if [[ ! -e "$resolver_parent" && ! -L "$resolver_parent" ]]; then
  resolver_parent_parent="${resolver_parent:h}"
  [[ "$resolver_parent_parent" == "${resolver_parent_parent:A}" ]]
  validate_protected_directory "$resolver_parent_parent"
  /bin/mkdir -m 700 "$resolver_parent"
fi
[[ "$resolver_parent" == "${resolver_parent:A}" ]]
validate_protected_directory "$resolver_parent"
if [[ -e "$resolver_directory" || -L "$resolver_directory" ]]; then
  [[ -d "$resolver_directory" && ! -L "$resolver_directory" ]]
else
  /bin/mkdir -m 700 "$resolver_directory"
fi
[[ "$resolver_directory" == "${resolver_directory:A}" ]]
validate_protected_directory "$resolver_directory"
[[ "$(/usr/bin/stat -f '%Lp' "$resolver_directory")" == 700 ]]
[[ ! -e "$resolver_target" && ! -L "$resolver_target" ]] || {
  print -u2 -- "resolver target already exists; use the rotation workflow"
  exit 1
}

resolver_sha256="$(
  set -euo pipefail
  resolver_temporary="$(/usr/bin/mktemp \
    "$resolver_directory/.hindsight-keychain-resolver.XXXXXX")"
  candidate_identity=
  cleanup_candidate() {
    if [[ -n "$candidate_identity" \
      && -f "$resolver_target" \
      && ! -L "$resolver_target" \
      && "$(/usr/bin/stat -f '%d:%i' "$resolver_target")" \
        == "$candidate_identity" ]]; then
      /bin/rm -f -- "$resolver_target" || true
    fi
    /bin/rm -f -- "$resolver_temporary" || true
  }
  trap cleanup_candidate EXIT

  [[ "$expected_resolver_sha256" =~ '^[0-9a-f]{64}$' ]]
  read -r source_sha256 _ < <(/usr/bin/shasum -a 256 "$resolver_source")
  [[ "$source_sha256" == "$expected_resolver_sha256" ]]
  /usr/bin/codesign --verify --strict "$resolver_source"
  signature_metadata="$(
    /usr/bin/codesign -d --verbose=4 "$resolver_source" 2>&1
  )"
  [[ "$signature_metadata" == *"Signature=adhoc"* ]]
  [[ "$signature_metadata" == *"TeamIdentifier=not set"* ]]
  resolver_architectures=" $(/usr/bin/lipo -archs "$resolver_source") "
  [[ "$resolver_architectures" == *" arm64 "* ]]
  [[ "$resolver_architectures" == *" x86_64 "* ]]
  [[ -x /usr/bin/python3 ]]

  /usr/bin/install -m 500 "$resolver_source" "$resolver_temporary"
  candidate_identity="$(
    /usr/bin/stat -f '%d:%i' "$resolver_temporary"
  )"
  /usr/bin/codesign --verify --strict "$resolver_temporary"
  [[ "$user_id" == "$(/usr/bin/stat -f '%u' "$resolver_temporary")" ]]
  [[ "$(/usr/bin/stat -f '%Lp' "$resolver_temporary")" == 500 ]]
  "$resolver_temporary" --self-test-acl >/dev/null
  read -r candidate_sha256 _ < <(
    /usr/bin/shasum -a 256 "$resolver_temporary"
  )
  [[ "$candidate_sha256" == "$expected_resolver_sha256" ]]

  /usr/bin/python3 -I -c '
import ctypes
import os
import sys

rename = ctypes.CDLL(None, use_errno=True).renameatx_np
rename.argtypes = [
    ctypes.c_int,
    ctypes.c_char_p,
    ctypes.c_int,
    ctypes.c_char_p,
    ctypes.c_uint,
]
rename.restype = ctypes.c_int
if rename(-2, os.fsencode(sys.argv[1]), -2, os.fsencode(sys.argv[2]), 4):
    error = ctypes.get_errno()
    raise OSError(error, os.strerror(error))
' "$resolver_temporary" "$resolver_target"

  /usr/bin/codesign --verify --strict "$resolver_target"
  [[ "$user_id" == "$(/usr/bin/stat -f '%u' "$resolver_target")" ]]
  [[ "$(/usr/bin/stat -f '%Lp' "$resolver_target")" == 500 ]]
  read -r published_sha256 _ < <(
    /usr/bin/shasum -a 256 "$resolver_target"
  )
  [[ "$published_sha256" == "$candidate_sha256" ]]
  "$resolver_target" --self-test-acl >/dev/null

  trap - EXIT
  print -r -- "$published_sha256"
)"
[[ "$resolver_sha256" == "$expected_resolver_sha256" ]]
print -r -- "$resolver_sha256"
```

The resolver is intentionally ad-hoc signed. Strict signature verification and
the expected `Signature=adhoc` metadata provide internal code-integrity evidence
only; they do not authenticate a producer or establish provenance. The release
approval process binds the expected SHA-256 to the trusted artifact because
there is no Team ID.

Set `credential_resolver.path` to `resolver_target` and
`credential_resolver.sha256` to `resolver_sha256`. Only after the final path and
digest are approved should the activation task create the three internal
credentials:

```zsh
resolver_target="$HOME/.local/libexec/hindsight-keychain-resolver"
"$resolver_target" --initialize
"$resolver_target" --status
```

The Keychain ACL binds the exact installed executable. Do not replace that path
as an ordinary release update. A resolver upgrade requires an explicit
credential-rotation and rollback plan.

### Rotate the macOS resolver

Treat a resolver upgrade as a credential rotation, not an in-place release
update:

1. Stop new harness sessions, drain broker writes, and stop every managed
   service that consumes the three credentials.
2. Install the successor at a new immutable path. Verify its trusted-release
   SHA-256, ad-hoc signature, `arm64` and `x86_64` slices, owner, mode, and ACL
   self-test before changing any credential.
3. Update and approve the consumer resolver path and digest, but do not start
   services.
4. Retire all three existing items with `"$prior_resolver" --retire`, then
   require `"$prior_resolver" --retired-status` to succeed. Only then
   recreate them under the successor's exact ACL with
   `"$successor_resolver" --initialize`. Verify the successor with
   `"$successor_resolver" --status`. Cleanup must finish before initialization;
   never print or persist credential values outside Keychain.
5. Start the managed services once and verify API, UI, broker, control, and
   fleet health plus a credential-free resolver status.
6. Retain the prior binary and consumer prestate until the rollback window
   closes. Rollback performs another credential rotation; it does not restore
   the retired values. Stop services, run
   `"$successor_resolver" --retire`, then
   require `"$successor_resolver" --retired-status` to succeed before running
   `"$prior_resolver" --initialize` and `"$prior_resolver" --status`, restoring
   the prior path and digest, and repeating the same health checks.

Never overwrite either resolver path in place. A partial rotation is a failed
activation. Keep services stopped; invoke `--retire` with both the prior and
successor resolvers even if the first reports mixed ownership, then require
`--retired-status` to succeed. Initialize all three credentials under one
verified resolver and require its `--status` to succeed before retrying.

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

Bind `HINDSIGHT_API_TENANT_EXTENSION` to
`hindsight_api.extensions.builtin.tenant:ApiKeyTenantExtension` in both the
managed service and its health check. The launcher supplies the resolved
`HINDSIGHT_API_TENANT_API_KEY` only to the API child. Without the explicit
extension selector, upstream Hindsight remains unauthenticated and the broker
refuses activation.

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
worker_id='replace-with-stable-consumer-and-profile-id'
uvx_executable=/absolute/path/to/uvx

HINDSIGHT_EMBED_UVX_EXECUTABLE="$uvx_executable" \
  tooling/hindsight/bin/hindsight-embed-uvx hindsight-embed configure \
  --profile "$profile" --port "$api_port"
HINDSIGHT_EMBED_UVX_EXECUTABLE="$uvx_executable" \
  tooling/hindsight/bin/hindsight-embed-uvx hindsight-embed profile set-env \
  "$profile" HINDSIGHT_BANK_ID "$bank_id"
HINDSIGHT_EMBED_UVX_EXECUTABLE="$uvx_executable" \
  tooling/hindsight/bin/hindsight-embed-uvx hindsight-embed profile set-env \
  "$profile" HINDSIGHT_API_AUDIT_LOG_ENABLED false
HINDSIGHT_EMBED_UVX_EXECUTABLE="$uvx_executable" \
  tooling/hindsight/bin/hindsight-embed-uvx hindsight-embed profile set-env \
  "$profile" HINDSIGHT_API_LLM_TRACE_ENABLED false
HINDSIGHT_EMBED_UVX_EXECUTABLE="$uvx_executable" \
  tooling/hindsight/bin/hindsight-embed-uvx hindsight-embed profile set-env \
  "$profile" HINDSIGHT_API_WORKER_ID "$worker_id"
```

Interactive setup keeps provider credentials out of process arguments. The
configured profile may exist outside the installer-managed data root, but the
declared fresh data root must be empty. The broker requires the upstream worker
feature while rejecting native audit logging and LLM request tracing; keep both
privacy features explicitly disabled and give each managed profile a stable,
consumer-scoped worker ID.
The broker validates the selected runtime and compiles its routes before it
publishes the socket. Give that first-start gate a bounded five-minute budget
with `HINDSIGHT_MEMORY_BROKER_WAIT_SECONDS=300`; later health probes remain
bounded independently.

For adoption, do not configure the profile or change its bank. Inspect the
existing profile, bank, ports, worker ID, privacy flags, and data root without
printing provider credentials. Broker activation requires audit logging and
LLM request tracing to be explicitly disabled; include any required privacy or
stable-worker correction in the approved activation plan. Set
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

Create one private destination object for each harness with this closed shape:

```json
{
  "schema_version": 1,
  "harness_id": "codex",
  "hooks_path": "/absolute/path/to/native-hooks.json",
  "settings_path": "/absolute/path/to/hindsight-user-settings.json",
  "tools_path": "/absolute/path/to/controller-tools.json",
  "rollback_root": "/absolute/private/state/harness-rollbacks"
}
```

Codex and Cursor use their native hooks documents directly. For Claude Code,
`hooks_path` names Claude's user `settings.json`, while `settings_path` names
the separate stable Hindsight upstream settings document; the closed schema
requires every path to be distinct, so these two paths cannot alias. The
adapter owns only the `hooks` member within Claude's user settings and preserves
all other Claude settings. Across harnesses it owns the controller hook entries,
the upstream auto-recall/auto-retain switches, the direct Hindsight endpoint,
bank, and credential keys it retires, and the controller tool entries. It
preserves unrelated values in all three documents. Claude also enters the
verified empty-MCP-server mode. The tools destination records the controller
commands exposed through the installed Hindsight memory skill. Destination
files must be current-user-owned regular files without group or world write
access, and the rollback root must be mode `0700`.

Use the production persistence commands to separate staging, review, apply,
status, and rollback:

```zsh
controller=/absolute/install/root/bin/hindsight-memory
state=/absolute/private/state
destination=/absolute/path/to/codex-destination.json

if ! stage="$($controller --state-dir "$state" harness-config stage \
  --destination "$destination" \
  --executable "$controller" \
  --locator-dir "$state/bridge-locators" \
  --staging-root "$state/harness-staging")"; then
  print -u2 -- 'harness artifact staging failed'
  return 1
fi
if ! generation="$(print -r -- "$stage" | \
  jq -er '.generation | select(type == "string" and length > 0)')"; then
  print -u2 -- 'harness artifact staging returned no valid generation'
  return 1
fi

$controller --state-dir "$state" harness-config plan \
  --destination "$destination" \
  --executable "$controller" \
  --locator-dir "$state/bridge-locators" \
  --generation "$generation" \
  --inventory /absolute/path/to/inventory.json \
  --policy /absolute/path/to/provider-runtime-policy.json \
  --output "$state/codex-activation-plan.json"

# Supply the printed approval digest only after reviewing the destination-bound
# activation record. The three health flags are emitted by the consumer's fresh
# broker, profile, and adapter checks.
$controller --state-dir "$state" harness-config apply \
  --destination "$destination" \
  --generation "$generation" \
  --inventory /absolute/path/to/inventory.json \
  --policy /absolute/path/to/provider-runtime-policy.json \
  --plan "$state/codex-activation-plan.json" \
  --approval-digest "$approved_activation_digest" \
  --broker-healthy --profile-healthy --adapter-self-test

$controller --state-dir "$state" harness-config status \
  --destination "$destination" \
  --plan "$state/codex-activation-plan.json"

$controller --state-dir "$state" harness-config rollback \
  --destination "$destination" \
  --generation "$generation" \
  --inventory /absolute/path/to/inventory.json \
  --policy /absolute/path/to/provider-runtime-policy.json \
  --plan "$state/codex-activation-plan.json" \
  --approval-digest "$approved_activation_digest"
```

The activation record contains only digests and binds the plan to the exact
destination paths. Apply reconstructs it from the current configuration;
rollback reconstructs it from the private rollback snapshot. Direct endpoint,
bank, and credential values never enter the record. The persistence adapter
serializes concurrent activation with an owner-only lock,
uses compare-and-swap on the complete projected configuration, and keeps a
phase-marked recovery journal. A process interrupted before commit restores the
exact prestate; one interrupted after commit finishes the target. Explicit
rollback requires the same approved activation digest and removes only
activation-owned changes while preserving later unrelated configuration.

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
