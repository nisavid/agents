# Production isolation design

## Current state

Environment design is accepted. Runtime acceptance is unrun.

The production-isolation run must use a disposable Apple-silicon macOS virtual
machine with no virtual network device. The app, authenticated gateway, model
runner, observers, and disposable state all run inside the guest. Their only
network path is guest loopback.

This is the narrowest practical boundary that preserves the vendor Chromium
sandbox without mutating the app bundle or the development Mac's global packet
filter. A physically disconnected Apple-silicon Mac is an accepted fallback if
the development machine cannot afford the VM's memory overhead.

No production-isolation run has occurred. This document fixes its environment,
oracles, and pass conditions; it does not satisfy them.

## Isolation boundary

Use Apple's
[Virtualization framework](https://developer.apple.com/documentation/virtualization/vzvirtualmachineconfiguration)
to run a macOS guest. Its
[`networkDevices`](https://developer.apple.com/documentation/virtualization/vzvirtualmachineconfiguration/networkdevices)
property defaults to an empty array, and a VM with no configured network device
has no guest network adapter. Apple's
[macOS VM sample](https://developer.apple.com/documentation/virtualization/running-macos-in-a-virtual-machine-on-apple-silicon)
provides the supported Apple-silicon guest and graphical interaction path.

The acceptance VM configuration must have empty arrays for:

- `networkDevices`;
- `socketDevices`;
- `directorySharingDevices`;
- `serialPorts` and `consoleDevices`;
- `customVirtioDevices` and `usbControllers`; and
- `audioDevices`.

`VZVirtioSocketDevice` creates
[port-based host/guest communication](https://developer.apple.com/documentation/virtualization/vzvirtiosocketdevice),
and `VZVirtioFileSystemDeviceConfiguration` exposes
[host directories to the guest](https://developer.apple.com/documentation/virtualization/vzvirtiofilesystemdeviceconfiguration).
Neither belongs in this boundary. Add no clipboard integration or other
host/guest data channel. Expose only the guest disk, auxiliary platform storage,
graphics, keyboard, and pointing devices required to boot and operate macOS.

Prepare the disposable guest before the acceptance boot:

1. Copy the exact verified app, model snapshot, runtime, gateway source, test
   fixture, and observers into the guest.
2. Shut down the guest and detach every staging disk and shared resource.
3. Clone or snapshot that sealed baseline.
4. Boot with the no-network, no-sharing configuration above.
5. Assert the configured and live counts of network, socket, and directory-share
   devices are zero before launching any tested process.

The guest disk is disposable evidence storage. It must contain no operator
profile, OpenAI state, real credential, host source tree, or writable shared
volume.

### Rejected native-host boundary

Do not use Packet Filter on the active development Mac for this gate. PF changes
privileged machine-wide state and cannot narrowly cover the full signed process
tree without a dedicated operating-system identity. Apple's built-in
[Application Firewall](https://support.apple.com/guide/mac-help/change-firewall-settings-on-mac-mh11783/mac)
is documented around incoming connections, not a fail-closed outbound boundary.
Either option couples the experiment to unrelated development processes and
machine state.

A second Mac with every physical and virtual network path removed is stronger
than the VM and remains acceptable. It costs more setup and hardware but does
not change the acceptance oracles.

## Why the semantic harness is insufficient

The preferred-route prototype in
[`08-validate-preferred-route.md`](08-validate-preferred-route.md) proves the
application, provider, gateway, namespace, credential, persistence, and cleanup
semantics. It does not prove production isolation.

Its outer `08-probe.sb` profile starts with `(allow default)`, then denies remote
IP connections, operator-home access, production-app writes, and login Keychain
lookup. The app is launched with `--no-sandbox` because Chromium's nested
sandbox does not initialize inside that outer profile.

Electron documents that Chromium normally isolates renderers and utility
processes from the privileged main process. It also states that
[`--no-sandbox`](https://www.electronjs.org/docs/latest/api/command-line-switches#--no-sandbox)
forces renderer and helper processes to run unsandboxed and is for testing only.
The
[sandbox guide](https://www.electronjs.org/docs/latest/tutorial/sandbox#disabling-chromiums-sandbox-testing-only)
states that the flag disables the sandbox for all processes, including utility
processes, and must never be used in production.

The outer profile proves its own coarse network boundary. It does not restore
Chromium's renderer-to-main privilege separation. Production acceptance must
therefore run the unmodified vendor process tree without `--no-sandbox` and put
egress denial outside that tree.

## Exact artifact baseline

The following values were re-read locally from the untouched, separately named
5263 artifact before writing this design:

| Property | Bound value |
| --- | --- |
| App version/build | `26.707.71524` (`5263`) |
| Architecture | thin `arm64` |
| Bundle identifier | `com.openai.codex` |
| Chromium | `150.0.7871.115` |
| Official archive SHA-256 | `8981d832cfd061ff8fe80295cd675d5c283fd53ed2ea8c80cc9d1856e47cfe74` |
| `Contents/Resources/app.asar` SHA-256 | `d28f31b4bbb04c519be65c2af8277d8c5faf77b4239ee89b928f0a7423dacd84` |
| `Contents/Resources/codex` SHA-256 | `28699add67540b93390329a740649a9eb9bdbc5538d92c1679c8c6b6fa2c623c` |
| Main CDHash | `b3d699c5b79a4d2edf33d316b58687f80be92538` |
| Renderer CDHash | `3787b07ed611dbfbe05abcda8de7cf231be6c61e` |
| Signing authority | OpenAI OpCo, LLC, Team ID `2DC432GLL2` |
| Runtime posture | hardened runtime, strict deep signature valid, designated requirement satisfied, stapled notarization ticket |

The main app and renderer helper are not App Sandbox applications. Both carry
`com.apple.security.network.client`, `com.apple.security.cs.allow-jit`, and
`com.apple.security.cs.allow-unsigned-executable-memory`. The main app also
declares `NSAllowsArbitraryLoads=true`. The absent virtual NIC is therefore the
egress boundary; entitlement inspection alone does not provide one.

The locally verified Electron fuse sentinel begins at framework offset
`209396646`, with version `1`, length `9`, raw bytes
`01 09 31 30 31 31 30 30 30 31 31`, and wire value `101100011`. The immutable
[exact-build assessment](https://github.com/nisavid/agents/blob/59e9fa5800b2806064236d1bab0e5f5845681e96/.scratch/chatgpt-airgap-unlock/research/06-integrity-signing-security.md#exact-5263-electron-fuses)
decodes that wire as:

| Fuse | State |
| --- | --- |
| `RunAsNode` | enabled |
| `EnableCookieEncryption` | disabled |
| `EnableNodeOptionsEnvironmentVariable` | enabled |
| `EnableNodeCliInspectArguments` | enabled |
| `EnableEmbeddedAsarIntegrityValidation` | disabled |
| `OnlyLoadAppFromAsar` | disabled |
| `LoadBrowserProcessSpecificV8Snapshot` | disabled |
| `GrantFileProtocolExtraPrivileges` | enabled |
| `WasmTrapHandlers` | enabled |

The earlier assessment's recorded offset points 32 bytes before the currently
verified sentinel. Its raw wire and decoded states remain correct. Acceptance
must compare the bytes at the currently verified offset before and after the
run; it must not modify any fuse.

## Acceptance procedure

### 1. Prove the structural boundary

The VM runner must fail closed unless its pre-start configuration and live VM
both report:

```text
networkDevices=0
socketDevices=0
directorySharingDevices=0
serialPorts=0
consoleDevices=0
customVirtioDevices=0
usbControllers=0
audioDevices=0
```

Inside the booted guest, capture:

```sh
ifconfig -a
scutil --nwi
netstat -rn -f inet
netstat -rn -f inet6
route -n get 1.1.1.1
route -n get -inet6 2606:4700:4700::1111
nc -G 3 -vz 1.1.1.1 443
nc -G 3 -6 -vz 2606:4700:4700::1111 443
```

Pass only if the guest has loopback but no non-loopback global address or
default route, and both literal-address connection attempts fail without a
route. DNS failure alone is not evidence.

### 2. Bind the pristine artifact

Before launch and after complete process cleanup:

```sh
codesign --verify --deep --strict --verbose=4 "$APP"
codesign -dv --verbose=4 "$APP"
codesign -d --entitlements :- "$APP"
file "$APP/Contents/MacOS/ChatGPT"
shasum -a 256 \
  "$APP/Contents/Resources/app.asar" \
  "$APP/Contents/Resources/codex"
```

Inventory every nested signed object and compare its identity, CDHash, and
entitlements to the prelaunch manifest. Re-read the fuse sentinel. Reject any
change to the bundle, signature, nested code, entitlements, architecture, ASAR,
bundled Codex, or fuse bytes.

### 3. Prove the vendor sandbox

Launch the copied app directly with its isolated guest profile and the accepted
local provider configuration. Do not add `--no-sandbox`, `--disable-sandbox`,
DYLD variables, injected code, new entitlements, re-signing, or bundle changes.

Capture the complete process tree and fail if any command line contains
`--no-sandbox` or `--disable-sandbox`, if any sandbox initialization error is
logged, or if expected renderer and utility processes are absent. Use the
loopback CDP observer to evaluate `process.sandboxed` in the renderer's isolated
Electron execution context. Electron documents that
[`process.sandboxed`](https://www.electronjs.org/docs/latest/api/process#processsandboxed-readonly)
is `true` in a sandboxed renderer and otherwise undefined. Require `true`.
Record the renderer's macOS sandbox-client process marker as supplementary
build-specific evidence.

### 4. Exercise the accepted route

Run the model, authenticated gateway, validation observer, CDP observer, and
app inside the guest. Bind every listener to `127.0.0.1` or `::1`. Use distinct
generated app-to-gateway and gateway-to-provider credentials, and persist only
boolean match results and sanitized terminal markers.

Require:

- a renderer-originated local turn completes with the exact sentinel;
- the completed thread survives a cold app-host restart;
- missing and wrong gateway credentials fail before upstream;
- the inbound credential never reaches the provider;
- every listener and established socket is loopback-only;
- `REMOTE_SOCKET_OBSERVED=false`;
- direct IPv4 and IPv6 egress probes remain unreachable during the turn; and
- no disposable or provider credential appears in state, logs, process
  arguments, or evidence.

Capture `ps`, `lsof -nP -iTCP -iUDP`, routes, interfaces, listener ownership,
gateway terminal state, provider terminal state, and the renderer result. A
proxy observer may record attempted hosted destinations, but it must remain an
inert guest-loopback service and never forward them.

### 5. Clean up and re-verify

Stop owned components in reverse dependency order: app and host, observers,
gateway, then provider. Signal only recorded root PIDs and process groups. Wait
for each group, verify every reserved listener is closed, then rerun the full
artifact and fuse checks.

Preserve the disposable guest disk and VM configuration digest as evidence.
Restore or delete the guest clone after review. Do not copy its profile into a
later run.

## Pass condition

Production isolation passes only when one artifact-bound run satisfies all of
these conditions together:

- the VM has no external network or live host/guest communication device;
- the vendor Chromium sandbox is positively observed;
- the renderer completes the accepted local workflow through guest loopback;
- hosted egress is structurally unavailable and every remote probe fails;
- credentials remain separated and absent from evidence;
- all owned processes and listeners terminate; and
- the complete signed artifact, entitlements, and fuse state match the bound
  prelaunch baseline.

Until that run occurs, the environment design is accepted and production
runtime acceptance remains unrun.
