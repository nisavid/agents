# Integrity, signing, and security assessment

## Decision

The exact official ChatGPT/Codex build `26.707.71524` (`5263`) is now available
as an untouched, separately named artifact. Its provenance, signature,
entitlements, nested code graph, ASAR integrity metadata, Electron fuses, and
Sparkle configuration have been inspected without launching or modifying it.
This closes the artifact-availability blocker. It does not yet select or
authorize a mutation: behavioral-owner discovery still determines whether any
bundle change is needed.

The preferred solution order is:

1. supported configuration or an external, authenticated local service that
   leaves the app bundle byte-for-byte pristine;
2. the smallest JavaScript change inside `app.asar`, if runtime evidence proves
   configuration cannot supply the behavior;
3. a native module or framework change only when no smaller surface owns the
   required decision.

Every bundle mutation invalidates OpenAI's outer signature. An ad-hoc re-sign
creates a different code identity, loses the Developer ID and notarization
chain, cannot legitimately inherit OpenAI Team-ID-scoped keychain and app-group
trust, changes how TCC recognizes the app, and makes the vendor updater an
unsafe lifecycle mechanism for that copy. It is acceptable only as a local,
disposable research artifact in a separate macOS account or VM with no real
profile or credentials. It is not a transparent replacement for the installed
app.

The merged `tooling/chatgpt-ffs` implementation does not meet this contract. In
particular, it computes the wrong bundle-level ASAR hash, weakens hardened
runtime protections, does not inventory and sign all nested code, accepts
incomplete verification as success, keeps a weak adjacent backup, and can kill
the production process by name. It must not be run against the system
Applications directory or treated as the implementation basis without
redesign.

## Scope and evidence boundary

This assessment used:

- current official Apple, Electron, and Sparkle security documentation;
- the merged `tooling/chatgpt-ffs` source at the Wayfinder branch;
- the historical, unmerged `origin/ivan/chatgpt-unflag` tail as negative and
  design-history evidence only;
- the companion [legacy patcher contract](https://github.com/nisavid/agents/blob/research/reconstruct-legacy-patcher-contract/.scratch/chatgpt-airgap-unlock/research/01-legacy-patcher-contract.md);
- read-only inspection of the exact official 5263 archive and its untouched,
  separately named extraction.

The exact source is the official archive at
<https://persistent.oaistatic.com/codex-app-prod/ChatGPT-darwin-arm64-26.707.71524.zip>.
Its SHA-256 is
`8981d832cfd061ff8fe80295cd675d5c283fd53ed2ea8c80cc9d1856e47cfe74`.
The extracted bundle reports build `26.707.71524` (`5263`), bundle identifier
`com.openai.codex`, and a thin arm64 main executable. Every empirical value in
the exact-build sections below is bound to that archive digest.

The installed production app was identified but never copied over, modified,
re-signed, launched, or stopped. The 5263 artifact was never launched or
modified. No real app profile, Keychain item, credential, TCC grant, updater
state, or network session was used.

Evidence labels below mean:

- **Official contract**: a current platform or framework document establishes
  the behavior.
- **Observed on 5263**: reproduced read-only against the exact archive above.
- **Historical**: present in repository history or its recorded observations,
  but not re-established against the exact archive.
- **Proof gap**: requires mutation or launch and was deliberately not attempted
  in this research ticket.

## Security assets, actors, and trust boundaries

### Assets

| Asset | Required property |
| --- | --- |
| Vendor artifact | Exact build, architecture, digest, signature, notarization ticket, and provenance remain independently verifiable. |
| Bundle execution | Every loaded executable, framework, helper, XPC service, native addon, ASAR file, and entitlement has an expected identity and digest. |
| User secrets | Authentication tokens, API keys, cookies, Keychain items, provider credentials, and local model credentials never enter a patched test profile. |
| Local machine | Repositories, shell authority, TCC-protected resources, and IPC endpoints are not exposed by the experiment. |
| Update trust | Sparkle's embedded EdDSA key, feed, Developer ID checks, and atomic update behavior remain vendor-owned. |
| Rollback state | Both bundle bytes and mutable profile state can be restored to a known pre-launch snapshot. |
| Research result | Claims remain bound to the exact artifact and can be reproduced without the production app. |

### Actors and systems

- OpenAI, Apple, Electron, and Sparkle are upstream trust authorities for the
  distributed artifact and update chain.
- The local operator and patch tool are highly privileged mutators.
- A malicious local process may race, replace, or interpose on mutable files,
  IPC, temporary directories, or update state.
- A malicious or compromised patch definition, dependency, model endpoint, or
  build tool can turn a narrow bypass into arbitrary code execution.
- A network attacker or hostile local endpoint can impersonate a service if the
  app is redirected without authenticated transport and endpoint binding.
- An accidental operator action can target the production app or production
  process even when the intended input is a copy.

### Trust boundaries

1. **Vendor distribution to immutable source**: download, signature,
   notarization, and artifact digest.
2. **Immutable source to working copy**: copy creation and exact-build binding.
3. **Patch definition to bundle bytes**: semantic preconditions, expected
   before/after digests, and complete change manifest.
4. **ASAR to native runtime**: ASAR header hash, per-file integrity, unpacked
   native payloads, Electron fuses, and resource sealing.
5. **Nested code to top-level app**: inside-out signing, library validation,
   entitlements, and designated requirements.
6. **App to OS trust stores**: Gatekeeper, notarization, TCC, Keychain, app
   groups, and quarantine.
7. **App to updater**: Developer ID identity, Sparkle EdDSA key, feed, XPC
   helpers, and replacement transaction.
8. **App to local/remote service**: credentials, protocol authenticity,
   loopback exposure, TLS, and data egress.
9. **App to user state**: Chromium/Electron profile, SQLite databases, caches,
   logs, preferences, Keychain, and migrations.

### Threat scenarios

| ID | Scenario | Likelihood | Impact | Primary controls | Residual risk |
| --- | --- | --- | --- | --- | --- |
| T1 | A path, symlink, or process-name mistake mutates or stops the production harness. | Medium | Critical | Canonical path plus device/inode checks; deny the system Applications directory and running harness identity; stop only PIDs executing inside the selected copy. | Low after fail-closed destination checks; operator override must not exist. |
| T2 | A stale or malicious patch definition matches a new build and changes unrelated control flow. | High | High | Exact artifact binding, semantic preconditions, expected match count, before/after digests, and independent manifest review. | Medium for minified code; runtime tracing remains mandatory. |
| T3 | Re-signing silently weakens hardened runtime or grants broader process authority. | High with the current tool | High | Per-executable entitlement diff; reject new DYLD, library-validation, server, debugging, or file authority without explicit acceptance. | Medium because Electron legitimately needs some executable-memory exceptions. |
| T4 | Incomplete ASAR or nested-code repair yields a launch failure or an exploitable mixed-trust code graph. | High with the current tool | High | Official raw-header algorithm, per-entry validation, complete code inventory, inside-out signing, strict deep verification, and isolated launch. | Low to medium after exact-build verification. |
| T5 | A patched copy reads production tokens, Keychain data, app-group data, or TCC grants. | Medium | Critical | Separate macOS account or VM, empty Keychain, synthetic profile, network denied, and no state copying. | Low if OS-account isolation is enforced. |
| T6 | A local provider shim exposes an unauthenticated endpoint or leaks prompts and credentials. | Medium | High | Loopback binding, per-launch authentication, protocol allowlist, egress policy, value redaction, and disposable credentials. | Medium; local same-user processes remain in the trust boundary. |
| T7 | The updater overwrites the patch, rejects the changed identity, or is modified into a persistence channel. | High if updater remains active | High | Never mutate update roots; disable update use on patched copies; update only pristine vendor artifacts; derive a fresh copy per version. | Low when the patched copy is disposable and updater-inert. |
| T8 | Rollback restores ASAR bytes but leaves changed signatures, plist, profile migrations, credentials, or OS grants. | High with ASAR-only rollback | High | Delete the entire copy, restore a VM/account snapshot, and verify an external immutable vendor artifact. | Low after snapshot restoration; external services may still retain test data. |
| T9 | Fixed-offset native patching corrupts a different architecture or code path. | High across builds | Critical | Architecture/slice binding, code-directory hash, instruction and control-flow preconditions, and last-resort disposition. | Medium even with guards; prefer non-native surfaces. |

## Platform integrity model

### Apple code identity and hardened runtime

Apple's [hardened runtime](https://developer.apple.com/documentation/security/hardened-runtime)
protects against code injection, dynamic-library hijacking, and memory
tampering. Exceptions are security decisions, not generic Electron boilerplate.
`allow-jit` and `allow-unsigned-executable-memory` expand executable-memory
authority. `allow-dyld-environment-variables` permits `DYLD_*` influence, and
`disable-library-validation` allows code from a different signing team to load.
Only an executable that demonstrably needs an exception should receive it.

Apple's [notarization workflow](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)
requires valid Developer ID signing, hardened runtime, secure timestamps, and
valid nested executables. A stapled ticket permits Gatekeeper to validate the
artifact without contacting Apple, which matters for an air-gapped first
launch. Any post-signing bundle mutation invalidates that chain. An ad-hoc
signature is not notarizable and provides no publisher identity.

Apple's [code-signing guidance](https://developer.apple.com/library/archive/technotes/tn2206/_index.html)
requires nested code to be signed before its enclosing container. Signing with
`--deep` is an emergency repair, not a substitute for understanding the code
graph or applying distinct entitlements to distinct executables. Verification
should use strict, deep validation, inspect every code object's identity and
entitlements, and separately validate Gatekeeper/notarization expectations.

A designated requirement is how macOS recognizes an app as the same code over
time. Apple explicitly uses it for persisted privacy grants such as microphone
access; see [TN3127](https://developer.apple.com/documentation/technotes/tn3127-inside-code-signing-requirements/).
Replacing OpenAI's Developer ID signature with ad-hoc signing changes that
identity. Existing TCC grants must neither be assumed to carry over nor be used
as validation evidence.

Keychain and app-group authority is also signing-team scoped. Apple's
[keychain sharing model](https://developer.apple.com/documentation/security/sharing-access-to-keychain-items-among-a-collection-of-apps)
forms the application identifier from Team ID plus bundle ID and restricts
shared access groups to apps from the same development team. For macOS app
groups using a Team-ID prefix, the system verifies that the prefix matches the
signing team; see [Accessing app group containers](https://developer.apple.com/documentation/xcode/accessing-app-group-containers).
An ad-hoc copy cannot safely retain or exercise `2DC432GLL2.*` authority. The
correct test condition is an empty, isolated account with no OpenAI Keychain or
app-group data, not an attempt to preserve those entitlements.

### Electron ASAR integrity and fuses

Electron's [ASAR integrity](https://www.electronjs.org/docs/latest/tutorial/asar-integrity)
has two independent layers:

1. each ASAR header entry may contain whole-file and block hashes; and
2. on macOS, `ElectronAsarIntegrity` in `Info.plist` contains the SHA-256 of the
   raw ASAR header, not the SHA-256 of the complete archive.

Enforcement is controlled by the
`EnableEmbeddedAsarIntegrityValidation` [fuse](https://www.electronjs.org/docs/latest/tutorial/fuses).
Electron recommends enabling it together with `OnlyLoadAppFromAsar`; otherwise
an attacker can bypass the protected archive by placing an `app` directory or
`default_app.asar` in the application search path. Fuses are package-time
settings and should be inspected before signing. A patch must never disable
integrity or `OnlyLoadAppFromAsar` simply to make modified content load.

The merged tool contradicts this official contract. It computes
`sha256_file(patched_asar)` and writes that whole-archive hash to
`ElectronAsarIntegrity` (`tooling/chatgpt-ffs/chatgpt-ffs:1498-1505`). On the
exact 5263 artifact, the plist value
`e3023f2d1c334ba8ba80bd22a97553d412a4616a86d75ca81e258e974061f3c7`
equals the SHA-256 of the 1,641,660-byte raw JSON header. The 195,069,720-byte
archive's whole-file SHA-256 is instead
`d28f31b4bbb04c519be65c2af8277d8c5faf77b4239ee89b928f0a7423dacd84`.
The header describes 6,017 files, including 639 unpacked entries, and every file
entry has integrity metadata. This confirms the tool's hash algorithm is wrong.
It also narrows the immediate effect: 5263 disables embedded ASAR validation,
so the wrong plist hash is a dormant integrity/forward-compatibility defect on
this build rather than a demonstrated launch blocker.

### Sparkle update trust

The exact 5263 artifact contains `Sparkle.framework` 2.9.1 (`2054`),
`Updater.app`, `Downloader.xpc`, `Installer.xpc`, and an embedded
`SUPublicEDKey`. Sparkle's
[security model](https://sparkle-project.org/documentation/security-and-reliability/)
uses atomic updates and double verification: Apple code signing plus Sparkle's
EdDSA signature. Its [publishing guidance](https://sparkle-project.org/documentation/publishing/)
also requires preserving permissions and symlinks. Sparkle's
[sandboxing/signing guide](https://sparkle-project.org/documentation/sandboxing/)
requires its helper code to be signed inside-out with its own entitlements and
warns against using `--deep` to sign.

The patched-copy lifecycle must therefore treat each vendor update as a new
immutable source transaction:

1. stop and archive the disposable patched copy;
2. obtain a fresh vendor artifact through the vendor updater or a separately
   verified download;
3. verify Developer ID, notarization, version, architecture, and artifact hash;
4. create a new separately named working copy;
5. rediscover and revalidate the minimal patch against that copy;
6. migrate only explicitly reviewed, non-secret test state.

Do not patch the feed URL, `SUPublicEDKey`, Sparkle helpers, or update verifier.
Do not let Sparkle update an ad-hoc modified copy in place. Do not claim that
restoring only `app.asar` makes an ad-hoc bundle safe to update; the original
outer and nested signatures, sealed resources, plist, and code identity must be
restored byte-for-byte from the immutable source.

### Exact 5263 updater configuration

The top-level plist has no `SUFeedURL`; updater configuration is supplied by
the packaged application metadata and native `sparkle.node` bridge. The ASAR's
package metadata records:

- `codexBuildFlavor`: `prod`;
- `codexBuildNumber`: `5263`;
- `codexAppBrand`: `chatgpt`;
- `codexSparkleFeedUrl`:
  `https://persistent.oaistatic.com/codex-app-prod/appcast.xml`;
- `codexSparklePublicKey`:
  `mNfr1v9t63BfgDtlw4C8lRvSY6uMggIXABDOCi3tS6k=`, exactly matching
  `SUPublicEDKey` in `Info.plist`.

The app also contains the production download URL
`https://persistent.oaistatic.com/codex-app-prod/Codex.dmg` for its
update-required UI. That is not evidence that Sparkle uses the DMG URL as its
feed. The native bridge requires a non-empty feed URL and supports explicit
fallback-feed switching; no fallback feed value was established by this
read-only pass.

## Exact observation: build 5263

The exact artifact passed `codesign --verify --deep --strict` and satisfied its
designated requirement. Its top-level signature reports:

- Developer ID Application: OpenAI OpCo, LLC (`2DC432GLL2`);
- hardened runtime enabled;
- a secure timestamp;
- a stapled notarization ticket;
- full code-directory SHA-256
  `b3d699c5b79a4d2edf33d316b58687f80be92538f333deb4cf746c0c0716018f`;
- 8,231 sealed resource files.

The recovery stream recorded a positive stapler validation on its recovered
artifact. This branch independently confirmed `Notarization Ticket=stapled`
through `codesign`, but its own `xcrun stapler validate` invocation against the
renamed bundle returned `LSDataUnavailable`; `spctl` likewise returned an
internal code-signing error for that renamed path. The positive stapler result
is therefore upstream evidence, not independently reproduced here. The strict
signature and designated-requirement checks did reproduce successfully.

The top-level entitlements include:

- `allow-jit` and `allow-unsigned-executable-memory`;
- camera, audio input, Apple Events automation, network client, and
  user-selected read/write file access;
- keychain groups `2DC432GLL2.*` and `2DC432GLL2.com.openai.shared`;
- app groups for notifications and CUA service IPC;
- no app sandbox;
- no `disable-library-validation` and no
  `allow-dyld-environment-variables`;
- no network-server entitlement and no `get-task-allow`.

### Complete signed-code topology

The artifact contains 18 `.app`, `.framework`, `.xpc`, or `.plugin` bundle
directories and 60 executable or loadable Mach-O files after excluding one
unsigned `dSYM` companion file. All 60 Mach-O files have hardened-runtime
signatures from OpenAI Team ID `2DC432GLL2`. The graph is:

- the arm64 `ChatGPT` main executable and `CodexDockTilePlugin.plugin`;
- `Codex Framework.framework`, four Electron helper apps, three standalone
  framework helper executables, and three framework dylibs;
- `Sparkle.framework`, `Updater.app`, `Downloader.xpc`, `Installer.xpc`, and
  the universal `Autoupdate` executable;
- seven top-level native `.node` modules plus two native helper executables;
- six Darwin native addons under `app.asar.unpacked`—`better_sqlite3`,
  `node-pty`, `node-hid`, serialport, macOS permissions, and Objective-C
  bridging—plus a `spawn-helper`;
- the `codex`, code-mode-host, chronicle, ripgrep, CUA Node, Node REPL, Sharp,
  libvips, Canvas, and fsevents executable/native payloads;
- browser and Chrome plugin native databases, the Chrome extension host, and
  the bundled Tectonic executable;
- two bundled Computer Use app trees, each containing the service, installer,
  authorization plugin and installer tool, CLI client, and lock-screen
  guardian.

The top-level app has the Team-ID-scoped keychain and app-group entitlements
listed above. The Computer Use service and client have their own application
identifier, keychain group, and app group. Other examined bundles either have
no entitlements or a vendor-provided Electron entitlement set. This is a
per-object graph; it cannot be reconstructed safely by signing two framework
directories and the outer app.

### Exact 5263 Electron fuses

The Electron fuse sentinel in `Codex Framework` has version `1`, length `9`,
raw bytes `01 09 31 30 31 31 30 30 30 31 31`, and wire value `101100011` at
framework offset `209396614`. The official `@electron/fuses` 2.1.3 library was
run read-only against the customized framework binary and decoded:

| Fuse | 5263 state |
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

The standard CLI reader assumes a framework named `Electron Framework` and
could not locate this app's renamed `Codex Framework`; calling the same official
library on the actual framework binary produced the table above. The presence
of `ElectronAsarIntegrity` in the plist does not mean 5263 enforces it.

### What remains nonportable from 5307

The earlier 5307 comparison happened to match 5263 on the top-level entitlement
shape, Electron framework version, fuse wire, Sparkle public key, and broad
bundle topology. That agreement is useful counterevidence to accidental drift,
but it does not make 5307 values portable. Its code-directory hash, signatures,
timestamps, ASAR bytes and hashes, source offsets, minified chunks, native-code
control flow, and behavioral ownership remain specific to 5307. All mutation
preconditions must use the 5263 archive digest and exact 5263 before bytes.

## Plausible modification surfaces

| Surface | Integrity and security consequences | Required controls | Disposition |
| --- | --- | --- | --- |
| Supported config, environment, or CLI | Leaves vendor bundle, signature, notarization, and updater intact. Configuration can still expose secrets or broaden endpoint trust. | Exact allowlist; isolated config and logs; no inherited credentials; verify no bundle bytes change. | **Preferred.** Explore first. |
| External local bootstrap or provider shim | Preserves bundle signing but adds a privileged protocol and credential boundary. A loopback service can still be reached by other local processes. | Bind loopback only; per-launch unguessable authentication; minimal protocol; no remote listener; no real tokens; explicit egress deny/allow rules; structured redaction. | Viable if runtime tracing proves the app supports it. |
| Renderer/main/preload JavaScript in `app.asar` | Changes a sealed resource and ASAR header; may trip per-entry integrity and embedded validation. Re-signing changes the outer code identity. | Exact artifact and semantic preconditions; preserve `app.asar.unpacked`; correct per-entry hashes; raw-header plist hash; inspect fuses; manifest every changed path; sign/verify complete code graph. | Smallest acceptable bundle mutation. |
| `app.asar.unpacked` or native `.node` payload | Changes native code loaded by Electron; adds ABI, architecture, install-name, and library-validation risk in addition to ASAR metadata. | Establish Electron/Node ABI and architecture; hash and sign each native object; validate load paths and dependencies; keep library validation enabled unless a precise exception is accepted. | Avoid unless ownership evidence requires it. |
| `Info.plist` | Invalidates the outer signature. Bundle ID, ASAR integrity, update key/feed, URL handlers, and privacy declarations are security-sensitive. | Change only a proven required key; never remove integrity, change updater trust roots, or impersonate the vendor identity; include exact before/after plist diff. | Supporting change only, not a bypass surface. |
| Electron framework or fuse bytes | Invalidates Mach-O code pages and framework signature; can silently weaken process-wide controls. Fixed offsets are build- and architecture-fragile. | Symbol/control-flow evidence; exact code-directory and slice binding; instruction-level preconditions; never weaken integrity/only-load fuses for convenience; full native validation. | Last resort. |
| Helper app, plugin, XPC service, framework, or native addon | Each is an independently signed code object with its own entitlements and enclosing seals. Wrong signing order or entitlement reuse can create privilege or load failures. | Enumerate all code objects; preserve least-privilege entitlements per executable; sign deepest first; verify each object and enclosing bundle. | Only if it owns the required behavior. |
| `DYLD_*`, injected library, or runtime interposition | The exact 5263 vendor artifact lacks DYLD-environment and disabled-library-validation exceptions across all inspected Mach-O entitlements. Adding them defeats hardened-runtime protections. | Disposable research account/VM only; no production use; explicit accepted threat model; never carry the weakening into a distributable artifact. | **Rejected as preferred design.** |
| Sparkle feed, EdDSA key, updater, or XPC helpers | Replaces or bypasses the software-supply-chain root and can persist arbitrary code. | No mutation. Vendor updates remain separate verified source transactions. | **Out of bounds.** |
| Ad-hoc re-sign of the complete app | Removes publisher identity and notarization; changes designated requirement; invalidates OpenAI Team-ID keychain/app groups; affects TCC and updater behavior. | Local disposable copy only; isolated macOS account/VM; strip unusable Team-ID entitlements rather than claim them; never distribute; explicit launch approval. | Research-only consequence, not a product state. |
| Re-sign with another Developer ID | Can establish a new publisher identity and potentially new notarization, but cannot preserve OpenAI's Team-ID-scoped authority or transparently remain in the vendor updater chain. | Own entitlements, own bundle identity, own update channel, own notarization, licensing review, full release engineering. | Separate product/fork decision, not this bypass. |

## Mandatory design invariants

### Artifact and destination

- Refuse the system Applications directory, the resolved path of the running
  harness, symlinks into either location, and any path whose file identity
  matches them.
- Require a separately named `.app` under an explicitly allowed working root.
- Canonicalize paths and compare device/inode identities before every mutation,
  not only at startup.
- Bind the patch to bundle ID, marketing version, build number, architecture,
  full source-artifact SHA-256, top-level code-directory hash, ASAR header hash,
  and target-file before hashes.
- Never infer build compatibility from nearby version strings or surviving
  feature-gate IDs.
- Refuse a running target. Stop only PIDs whose executable path resolves inside
  the selected copy. Never use `killall` or process-name matching.

### Mutation transaction

- Copy the pristine artifact to a new staging bundle on the same filesystem.
- Verify the pristine source before mutation and make it read-only to the tool.
- Apply semantic preconditions and record an exact before/after manifest.
- Rebuild ASAR deterministically; preserve unpacked placement, file modes,
  symlinks, and per-entry integrity metadata.
- Compute `ElectronAsarIntegrity` from the raw ASAR header and verify it
  independently after writing.
- Discover the full nested-code graph from the actual bundle. Do not hard-code
  known frameworks or use `--deep --force` as the signing strategy.
- Preserve original entitlements per executable as an audit input, then derive
  an explicit least-privilege entitlement set for the new identity. Never copy
  OpenAI Team-ID keychain/app-group entitlements into an ad-hoc signature and
  assume they remain valid.
- Sign deepest code first and the app last. Abort on any signing error.
- Atomically publish the completed staged copy under a new name only after all
  validation gates pass.

### Credentials and profile isolation

- Do not run a patched copy in the operator's normal macOS account. A custom
  Electron `--user-data-dir`, `HOME`, or environment alone does not isolate
  system Keychain, TCC, app-group containers, login items, or other per-user OS
  services.
- Use a disposable VM or separate macOS user with an empty login Keychain, no
  OpenAI app-group data, no inherited browser/session state, and no access to
  the production source checkout beyond a minimal test fixture.
- Generate synthetic provider credentials scoped to a disposable local service.
  Never copy cookies, tokens, SQLite databases, preferences, or Keychain items
  from the real profile.
- Start with network denied. Add only evidence-based destinations. Capture DNS,
  TCP/TLS, filesystem, and IPC attempts without recording secret values.
- Deny camera, microphone, automation, screen recording, accessibility, and
  broad file access unless a specific test requires one; perform such a test in
  a fresh snapshot and discard it afterward.

### Rollback

- Rollback means deleting the disposable working copy and restoring the entire
  isolated test-account or VM snapshot. Copying back `app.asar` is not rollback.
- Keep the pristine artifact outside the bundle in a content-addressed,
  read-only store. An adjacent `app.asar.orig` is mutable, unsigned tool state
  inside a signed-bundle boundary and is not an immutable source.
- Record pristine and patched manifests, signatures, entitlements, fuses, ASAR
  hashes, updater key, and profile snapshot identifiers.
- Before every test launch, take a profile snapshot. On rollback, restore app
  data, preferences, caches, Keychain, containers, and TCC state together.
- A successful rollback re-verifies the pristine vendor artifact from the
  external store. Do not re-sign it; byte-for-byte vendor signatures must
  validate as originally distributed.

## Findings in the merged tool

These are design blockers for the air-gap work, not authorization to implement
fixes in this research ticket. Exact-build reconciliation confirms every
finding, but retracts the earlier `Critical` severity for the ASAR plist hash on
5263 because that build disables the enforcing fuse.

Validation rubric:

- [x] The official archive source and SHA-256 identify one immutable 5263
  artifact.
- [x] Strict signature, designated requirement, nested code, and entitlement
  evidence comes directly from that untouched extraction.
- [x] ASAR header, whole-file, per-entry, fuse, and updater values are computed
  or decoded independently and agree with bundle metadata.
- [x] Each current-tool finding is traced to the merged source and reconciled
  with exact-build counterevidence.
- [x] No validation step launched or modified the artifact or touched the real
  profile or production app.

| Disposition | Finding | Exact 5263 evidence and consequence | Required direction |
| --- | --- | --- | --- |
| **Confirmed; dormant on 5263** | Wrong bundle-level ASAR hash | `commit_changes` writes the whole-archive SHA-256 to `ElectronAsarIntegrity`. The exact artifact proves the official value is the raw-header hash. Because 5263 disables embedded ASAR validation, this mismatch is not a demonstrated 5263 launch failure; it is still an integrity-contract and forward-compatibility defect. | Implement and independently test the official raw-header algorithm before any mutation. |
| **Confirmed; high regression** | Hardened runtime is weakened globally | Hard-coded app/framework entitlements add `disable-library-validation` and `allow-dyld-environment-variables`; app entitlements also add a network server. None appears in any of the 60 signed 5263 Mach-O objects inspected. The tool also discards object-specific vendor entitlements. | Preserve least privilege per executable; reject unexplained new authority. |
| **Confirmed; high regression** | Incomplete nested-code signing graph | `_find_nested_code` returns only `.app` directories despite its docstring mentioning `.node`; signing is limited to two frameworks and the app. Exact 5263 has 60 signed Mach-O objects across frameworks, XPCs, plugins, native addons, helpers, tools, and two Computer Use trees. Some signing failures are ignored. | Inventory every signed object and fail closed; sign inside-out with object-specific entitlements. |
| **Confirmed; high safety risk** | Production process can be stopped accidentally | `quit_app` calls `killall` on the selected app basename. A conventionally named copy can stop every same-named production process; a separately renamed copy can fail to stop its internal `ChatGPT` executable at all. | Select PIDs by canonical executable path inside the chosen copy; refuse the harness path. |
| **Confirmed; high integrity risk** | Failed signature verification does not fail the transaction | `commit_changes` logs a warning but writes state and returns success after verification failure. Verification is top-level `codesign --verify --verbose`, without `--deep --strict`, despite the exact artifact's large nested graph. | Treat any integrity/signing failure as transaction failure and never publish the staged copy. |
| **Confirmed; high provenance risk** | Backup and state are not artifact-bound | `app.asar.orig` is created adjacent to the live archive and global state is not keyed by bundle path, build, architecture, official archive digest, or code identity. A stale or replaced backup can become the rebuild base. | External content-addressed pristine store plus artifact-bound state and exact lease checks. |
| **Confirmed** | Ad-hoc entitlements use the wrong authority model | Re-signing drops OpenAI's Developer ID and Team ID, while exact 5263 uses Team-ID-scoped app identifiers, keychain groups, app groups, and designated requirements. The current design does not model that loss. | Use an isolated account and a new explicit identity model; do not access production credentials. |
| **Confirmed** | Update workflow overstates restoration | Exact 5263 uses OpenAI Developer ID signing, a stapled ticket, Sparkle 2.9.1, an embedded EdDSA key, a packaged production feed, and signed updater XPCs. Restoring ASAR and ad-hoc re-signing restores none of those identities. | Replace the entire app from the verified official archive before updating. |

The historical fixed-offset native approach remains a discovery lead only. It
was not bound to architecture, Mach-O slice, code-directory hash, or exact
artifact identity, and the recorded offset changed repeatedly as earlier code
paths proved dead. No fixed offset or byte sequence is reusable for 5263.

## Exact-build validation plan

The read-only source/topology gates below now pass for the archive digest named
above. They remain required preflight checks and must be rerun against the
immutable source immediately before deriving a patched copy. Mutation and
runtime gates remain open.

### 1. Source and topology

- Record source URL/channel, download hash, quarantine metadata, bundle ID,
  versions, architectures, Team ID, designated requirement, code-directory
  hashes, secure timestamps, notarization ticket, and Gatekeeper assessment.
- Run strict deep signature verification and enumerate every nested signed code
  object, entitlement set, and enclosing bundle relationship.
- Record Electron/Chromium version, ASAR and unpacked topology, Sparkle version,
  feed configuration, EdDSA public key, and all native modules.
- Read Electron fuses with a compatible official `@electron/fuses` release and
  retain both raw wire value and named interpretation. The 5263 baseline is the
  nine-state table above.

### 2. Mutation preconditions

- Prove the required behavior owner through offline runtime tracing and source
  correlation. Distinguish startup/auth presentation, feature visibility, local
  runtime capability, and hosted dependency.
- Prefer a configuration/external-service experiment that produces zero bundle
  changes. Record its complete process, file, IPC, and network boundary.
- For a bundle change, require an exact semantic match count and expected
  before digest. Refuse zero, multiple, partial, or ambiguous matches.
- For native code, bind expected instructions to architecture and control flow,
  not file offset alone.

### 3. Post-mutation static validation

- Compare pristine and patched manifests and prove only approved paths changed.
- Parse the rebuilt ASAR independently; verify every entry size, offset,
  executable bit, unpacked placement, whole-file hash, and block hash.
- Recompute the raw-header hash independently and compare it with the plist.
- Re-read fuses and prove no security fuse changed unless that exact change was
  explicitly accepted.
- Verify every nested signature, requirement, entitlement set, architecture,
  dynamic dependency, and enclosing seal; then run strict deep app verification.
- Demonstrate that Sparkle feed/key/helper bytes are unchanged.

### 4. Isolated runtime validation

- Boot a fresh disposable VM/account snapshot with network denied and no real
  profile or credentials.
- Launch only the separately named copy by canonical path. Verify the production
  app and process remain untouched.
- Exercise startup, local project selection, provider bootstrap, a synthetic
  prompt, streaming response, cancellation, error recovery, restart, and state
  persistence with a disposable endpoint.
- Record attempted network destinations, IPC endpoints, filesystem writes,
  Keychain requests, TCC prompts, crashes, code-signing violations, and updater
  activity. Redact values at collection time.
- Prove unavailable hosted services fail closed and explain themselves rather
  than silently falling back online or exposing credentials.

### 5. Rollback and update validation

- Revert by deleting the patched copy and restoring the VM/account snapshot.
- Re-verify the external pristine artifact byte-for-byte, including original
  Developer ID and notarization state.
- Exercise the vendor updater only on a pristine copy. Verify the update as a
  new artifact, then derive a new disposable patched copy. Never update the
  patched artifact in place.

## Acceptance rubric

The implementation ticket is ready only when all five statements are true:

1. Official platform contracts support every claimed signing, integrity,
   updater, Keychain, TCC, and rollback behavior.
2. Every empirical claim is bound to exact build 5263 and its artifact digest;
   5307 and historical observations are not used as mutation preconditions.
3. The chosen modification is the smallest proven behavioral owner and does
   not silently disable a security control.
4. The transaction fails closed on any path, precondition, signing, integrity,
   or validation mismatch and cannot touch or stop the production app.
5. Runtime evidence comes from a disposable profile with no real credentials,
   and complete rollback has been demonstrated.

## Proof gaps

- The exact build, archive digest, signature, entitlements, fuses, ASAR format,
  nested Mach-O graph, Sparkle version, feed, and updater key are established.
  The renamed extraction could not be independently assessed by `stapler` or
  `spctl`; strict deep code-signing verification passed, `codesign` reports a
  stapled ticket, and the recovery stream's positive stapler result remains the
  only positive stapler validation.
- This signing-assessment lane did not launch 5263 or produce process,
  filesystem, IPC, or network traces. That was deliberate: this ticket
  authorized read-only artifact analysis and prohibited launch. Isolated
  runtime evidence belongs to ticket 03 and should be consumed from that lane,
  not inferred from this report.
- No valid patch mechanism has been selected. Historical renderer gates and
  native offsets are leads, not contracts.
- No ad-hoc patched artifact was created or launched, so runtime entitlement,
  Keychain, TCC, updater, and rollback behavior remains deliberately untested.
- Legal and redistribution questions are outside this technical assessment.

These gaps require the implementation and isolated-runtime tickets to retain
their fail-closed gates. They do not block continued behavioral-owner discovery
against the verified immutable source.

## Reproduction notes

The exact-build checks used only the separately named 5263 extraction and did
not launch it:

```text
codesign --verify --deep --strict --verbose=2 <copy>.app
codesign -dv --verbose=4 <copy>.app
codesign -d --entitlements - --xml <code-object>
find <copy>.app/Contents -type f \( -name '*.node' -o -name '*.dylib' \)
find <copy>.app/Contents -type d \( -name '*.app' -o -name '*.framework' -o -name '*.xpc' \)
```

The archive SHA-256 was checked before inspection. The ASAR comparison parsed
the first Chromium pickle records, hashed exactly the raw JSON header declared
by the header string length, and separately hashed the complete archive. The
raw-header digest matched `ElectronAsarIntegrity`; the complete-archive digest
did not. A read-only traversal identified every Mach-O file excluding `dSYM`
contents, checked each signature, and confirmed all 60 were signed by
`2DC432GLL2`. The official `@electron/fuses` 2.1.3 API read the customized
framework binary directly; no fuse write API was called.

The archive and extraction had `com.apple.provenance` metadata but no
`com.apple.quarantine` attribute. Renaming the bundle prevented this branch's
Launch Services-based stapler and Gatekeeper tools from completing; that tool
error is not evidence against the embedded ticket or signature.

Primary references:

- Apple: [Hardened Runtime](https://developer.apple.com/documentation/security/hardened-runtime),
  [Notarizing macOS software](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution),
  [TN2206](https://developer.apple.com/library/archive/technotes/tn2206/_index.html),
  [TN3127](https://developer.apple.com/documentation/technotes/tn3127-inside-code-signing-requirements/),
  [Keychain sharing](https://developer.apple.com/documentation/security/sharing-access-to-keychain-items-among-a-collection-of-apps),
  [App groups](https://developer.apple.com/documentation/xcode/accessing-app-group-containers).
- Electron: [ASAR integrity](https://www.electronjs.org/docs/latest/tutorial/asar-integrity),
  [Fuses](https://www.electronjs.org/docs/latest/tutorial/fuses),
  [Code signing](https://www.electronjs.org/docs/latest/tutorial/code-signing).
- Sparkle: [Security and reliability](https://sparkle-project.org/documentation/security-and-reliability/),
  [Publishing](https://sparkle-project.org/documentation/publishing/),
  [Sandboxing and signing](https://sparkle-project.org/documentation/sandboxing/).
