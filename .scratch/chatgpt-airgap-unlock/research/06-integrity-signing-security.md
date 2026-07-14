# Integrity, signing, and security assessment

## Decision

Do not modify the target ChatGPT/Codex build until the exact build
`26.707.71524` (`5263`) is available as an immutable, separately named copy.
The current evidence is enough to define the safety contract, but not to select
or validate a mutation against that build.

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
- the merged `tooling/chatgpt-ffs` source at `305b754`;
- the historical, unmerged `origin/ivan/chatgpt-unflag` tail as negative and
  design-history evidence only;
- the companion [legacy patcher contract](https://github.com/nisavid/agents/blob/research/reconstruct-legacy-patcher-contract/.scratch/chatgpt-airgap-unlock/research/01-legacy-patcher-contract.md);
- read-only inspection of a separately named temporary copy.

The inspected copy is build `26.707.72221` (`5307`), bundle identifier
`com.openai.codex`. It is a comparative sample, not a proxy for target build
`26.707.71524` (`5263`). No target-build artifact was found in the installed
bundle, Downloads DMGs, Spotlight results, or updater cache. The two available
DMGs are consumer ChatGPT builds (`com.openai.chat`), so they were mounted
read-only, identified, and detached; they were not used as target evidence.

The installed production app was identified but never copied over, modified,
re-signed, launched, or stopped. The comparative copy was never launched. No
real app profile, Keychain item, credential, TCC grant, updater state, or
network session was used.

Evidence labels below mean:

- **Official contract**: a current platform or framework document establishes
  the behavior.
- **Observed on 5307**: reproduced against the separately named comparative
  copy only.
- **Historical**: present in repository history or its recorded observations,
  but not re-established against 5263.
- **Proof gap**: must be established on an exact 5263 copy before implementation
  or acceptance.

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
5307 sample, the plist value
`9d7676e404b1b984f571edc89db3786bc2478608d343762b5e7d6d1616780f78`
equals the SHA-256 of the 1,641,993-byte raw JSON header. The whole archive hash
is instead
`b5da51e5df6e996076e4cb19045cec46dd4c08cf61c19cdbc5cb426b8413b73c`.
This is a demonstrated integrity bug, even though enforcement happens to be
disabled on the comparative build.

### Sparkle update trust

The comparative copy contains `Sparkle.framework`, `Updater.app`,
`Downloader.xpc`, `Installer.xpc`, and an embedded `SUPublicEDKey`. Sparkle's
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

## Comparative observation: build 5307 only

The copied 5307 bundle passed `codesign --verify --deep --strict` and satisfied
its designated requirement. Its top-level signature reports:

- Developer ID Application: OpenAI OpCo, LLC (`2DC432GLL2`);
- hardened runtime enabled;
- a secure timestamp;
- a stapled notarization ticket;
- 8,231 sealed resource files.

`codesign` reports the stapled ticket, but `xcrun stapler validate` did not
complete successfully against the renamed copy. This assessment does not claim
independent stapler validation.

The top-level entitlements include:

- `allow-jit` and `allow-unsigned-executable-memory`;
- camera, audio input, Apple Events automation, network client, and
  user-selected read/write file access;
- keychain groups `2DC432GLL2.*` and `2DC432GLL2.com.openai.shared`;
- app groups for notifications and CUA service IPC;
- no app sandbox;
- no `disable-library-validation` and no
  `allow-dyld-environment-variables`.

The copy contains at least 50 `.node`/`.dylib` files and 17 app, framework, or
XPC bundle directories. Examined nested code, including Sparkle, Electron
helpers, native modules, and computer-use helpers, is signed by the same OpenAI
Team ID. This is a code graph, not merely “two frameworks and the app.”

The Electron fuse sentinel in `Codex Framework` has version `1`, length `9`,
and wire value `101100011`. Against Electron's current fuse ordering, this
means the comparative build enables `RunAsNode`, `NODE_OPTIONS`, CLI inspect,
file-protocol extra privileges, and the current ninth fuse, while disabling
cookie encryption, embedded ASAR integrity validation, `OnlyLoadAppFromAsar`,
and browser-specific V8 snapshots. The ninth fuse is intentionally not named
here because the local `@electron/fuses` reader could not be run with the
available npm engine; exact mapping should be repeated with a compatible
official reader. Most importantly, the presence of `ElectronAsarIntegrity` in
the plist does not mean 5307 enforces it.

None of these observations may be projected onto 5263. The target must be read
and recorded independently.

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
| `DYLD_*`, injected library, or runtime interposition | The 5307 vendor build deliberately lacks DYLD-environment and disabled-library-validation exceptions. Adding them defeats hardened-runtime protections. | Disposable research account/VM only; no production use; explicit accepted threat model; never carry the weakening into a distributable artifact. | **Rejected as preferred design.** |
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
fixes in this research ticket.

| Severity | Finding | Evidence and consequence | Required direction |
| --- | --- | --- | --- |
| Critical | Wrong bundle-level ASAR hash | `commit_changes` writes the whole-archive SHA-256 to `ElectronAsarIntegrity`. Official Electron behavior and the 5307 sample require the raw-header hash. A target with enforcement enabled terminates or fails to load. | Implement and independently test the official raw-header algorithm; verify the target fuse state. |
| High | Hardened runtime is weakened globally | Hard-coded app/framework entitlements add `disable-library-validation` and `allow-dyld-environment-variables`, which the 5307 vendor app does not have. App entitlements also add a network server. | Preserve least privilege per executable; reject unexplained new authority. |
| High | Incomplete nested-code signing graph | `_find_nested_code` returns only `.app` directories despite its docstring mentioning `.node`; signing is limited to two frameworks and the app, while 5307 has many native modules, XPCs, plugins, and helpers. Some signing failures are ignored. | Inventory every signed object and fail closed; sign inside-out with object-specific entitlements. |
| High | Production process can be stopped accidentally | `quit_app` calls `killall` on the selected app basename. A renamed copy can still execute the same internal binary, and name matching is not destination isolation. | Select PIDs by canonical executable path inside the chosen copy; refuse the harness path. |
| High | Failed signature verification does not fail the transaction | `commit_changes` logs a warning but writes state and returns success after verification failure. Verification is top-level `codesign --verify --verbose`, without `--deep --strict`. | Treat any integrity/signing failure as transaction failure and never publish the staged copy. |
| High | Backup and state are not artifact-bound | `app.asar.orig` is created adjacent to the live archive and global state is not keyed by bundle path, build, architecture, source digest, or identity. A stale or replaced backup can become the rebuild base. | External content-addressed pristine store plus artifact-bound state and exact lease checks. |
| Medium | Ad-hoc entitlements claim the wrong authority model | Re-signing drops OpenAI's Developer ID but the design does not explicitly handle loss of Team-ID keychain/app-group/TCC identity. | Use an isolated account and a new explicit identity model; do not access production credentials. |
| Medium | Update workflow overstates restoration | Restoring ASAR and ad-hoc re-signing does not restore original nested signatures, sealed resources, Developer ID, notarization, designated requirement, or updater identity. | Replace the entire app from a verified pristine vendor artifact before updating. |

The historical fixed-offset native approach remains a discovery lead only. It
was not bound to architecture, Mach-O slice, code-directory hash, or exact
artifact identity, and the recorded offset changed repeatedly as earlier code
paths proved dead. No fixed offset or byte sequence is reusable for 5263.

## Exact-build validation plan

All gates below must pass on an untouched, separately named 5263 copy before a
patch design is accepted.

### 1. Source and topology

- Record source URL/channel, download hash, quarantine metadata, bundle ID,
  versions, architectures, Team ID, designated requirement, code-directory
  hashes, secure timestamps, notarization ticket, and Gatekeeper assessment.
- Run strict deep signature verification and enumerate every nested signed code
  object, entitlement set, and enclosing bundle relationship.
- Record Electron/Chromium version, ASAR and unpacked topology, Sparkle version,
  feed configuration, EdDSA public key, and all native modules.
- Read Electron fuses with a compatible official `@electron/fuses` release and
  retain both raw wire value and named interpretation.

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
   5307 and historical observations are labelled comparative only.
3. The chosen modification is the smallest proven behavioral owner and does
   not silently disable a security control.
4. The transaction fails closed on any path, precondition, signing, integrity,
   or validation mismatch and cannot touch or stop the production app.
5. Runtime evidence comes from a disposable profile with no real credentials,
   and complete rollback has been demonstrated.

## Proof gaps

- Exact build 5263 has not been acquired. Its signatures, entitlements, fuses,
  ASAR format, native modules, updater key, and behavioral owner are unknown.
- No 5263 offline launch, process trace, filesystem trace, IPC trace, or network
  trace exists yet.
- No valid patch mechanism has been selected. Historical renderer gates and
  native offsets are leads, not contracts.
- No compatible official `@electron/fuses` reader was available in this pass;
  the 5307 raw wire was decoded only far enough to establish the integrity and
  only-load fuse states.
- No ad-hoc patched artifact was created or launched, so runtime entitlement,
  Keychain, TCC, updater, and rollback behavior remains deliberately untested.
- Legal and redistribution questions are outside this technical assessment.

These gaps block mutation and launch, not further read-only discovery.

## Reproduction notes

The comparative checks used only the separately named copy:

```text
codesign --verify --deep --strict --verbose=2 <copy>.app
codesign -dv --verbose=4 <copy>.app
codesign -d --entitlements - --xml <code-object>
find <copy>.app/Contents -type f \( -name '*.node' -o -name '*.dylib' \)
find <copy>.app/Contents -type d \( -name '*.app' -o -name '*.framework' -o -name '*.xpc' \)
```

The ASAR comparison parsed the first Chromium pickle records, hashed exactly
the raw JSON header declared by the header string length, and separately hashed
the complete archive. The raw-header digest matched `ElectronAsarIntegrity`; the
complete-archive digest did not.

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
