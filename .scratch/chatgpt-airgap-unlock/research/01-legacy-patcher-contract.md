# Legacy ChatGPT FFS contract

## Answer

`origin/ivan/chatgpt-unflag` established two different historical patcher
contracts:

1. A merged Electron contract that rebuilds `Contents/Resources/app.asar`
   from a sibling backup, forces selected renderer-side feature gates true,
   repairs both ASAR metadata and `Info.plist`, then ad-hoc signs the bundle in
   dependency order.
2. An unmerged native experiment that patches fixed bytes in
   `Contents/Frameworks/ChatGPT.framework/Versions/A/ChatGPT`, then ad-hoc
   signs the bundle.

The reusable contract is the safety and evidence envelope around a patch:
identify the exact artifact, distinguish authentication barriers from feature
visibility and hosted dependencies, verify preconditions before mutation,
preserve every package surface not intentionally changed, repair all integrity
layers, sign nested code coherently, validate behavior as well as visibility,
and provide a verified rollback from an immutable pristine source.

The historical gate IDs, bundle globs, fixed offsets, instruction bytes,
framework names, hard-coded entitlements, and claims about which function owns
the login decision are not reusable facts. They are discovery leads that must
be re-established against build `26.707.71524` (`5263`).

## Scope and evidence

This reconstruction reads:

- merged `tooling/chatgpt-ffs` at the Wayfinder branch parent;
- the complete 23-commit tail from that parent through
  `origin/ivan/chatgpt-unflag` at `811e2783c440142eeb1d9a1d4e178d2eba2278f6`;
- the tool README and all three test modules present at that tip;
- commit messages and diffs for the original ASAR implementation, integrity
  repairs, signing repair, update workflow, rollback/verification work, and
  three successive native no-auth attempts.

The production app bundle was neither inspected nor modified. The unmerged
source was exported to a temporary directory for tests; it was not checked out
over this worktree.

Evidence strength used below:

- **Tested here** means a pure-Python repository test exercised the behavior.
- **Historical runtime evidence** means a commit records a disposable-copy
  observation, but this session did not reproduce it.
- **Unverified** means source or history asserts the behavior without a test or
  surviving runtime evidence that establishes it for the mapped build.

## Offline workflow established by the legacy tool

### Operator workflow

The merged tool exposes a dependency-free Python CLI and curses TUI. The
operator can list or inspect patch state, select gates, preview pending changes,
apply or revert a selection, restore a pristine bundle before an update, and
reapply the saved selection after the update. The unmerged tail adds a
closed-loop `verify` command.

Installation is a symlink from `~/.local/bin/chatgpt-ffs` to the repository
entrypoint. It may create `~/.local/bin/env` and `env.fish`. Runtime state is
stored in `~/.codex/chatgpt-ffs/state.json` as the installed payload hash and
selected patch IDs. This state is global: it is not keyed by app path, bundle
identifier, build, architecture, or pristine-source hash.

For an Electron bundle, apply/revert is a deterministic rebuild from
`app.asar.orig`, not an edit layered on the current live ASAR:

1. Quit the named app process.
2. Create `app.asar.orig` on first use.
3. Extract the backup into a temporary directory, copying entries marked
   `unpacked` from the sibling `app.asar.unpacked` tree.
4. Apply every selected regex replacement to matching extracted JavaScript.
5. Repack a complete ASAR while preserving packed/unpacked placement and the
   executable flag.
6. Copy the rebuilt ASAR into the app, update its `Info.plist` integrity hash,
   and ad-hoc sign the bundle.
7. Verify the signature and persist payload hash plus selected patch IDs.

For the unmerged native path, the tool instead:

1. Copies the framework binary to a sibling `.orig` backup on first use.
2. Restores the live framework binary from that backup before every apply.
3. Checks fixed expected bytes at fixed file offsets and overwrites them.
4. Re-signs the bundle, verifies its top-level signature, and persists the
   framework-binary hash plus selected patch IDs.

The intended offline user journey was broader than startup: make local Codex
surfaces visible without authenticated Statsig evaluation, then use local
projects, worktrees, thread history, modes, permissions, plugins/skills, and a
custom provider. The legacy tool only established gate replacement and bundle
lifecycle. It did not establish that every revealed surface had a functioning
offline backing service, nor did it configure or validate GLM 5.2 end to end.

## Gate classes

The history contains three distinct classes. They must remain separate in the
new design.

### 1. Renderer feature-configuration gates

The merged Electron registry grew from one worktree gate to 30 named patches
covering 33 numeric Statsig IDs. They are grouped as:

| Family | Historical examples | Contract established |
| --- | --- | --- |
| Workspace and local execution | worktree choice, thread catalog, recent threads, local project actions, modes, auth-state visibility, background-subagent panel | Auth-less Statsig defaults can hide otherwise local UI. |
| Conversation and thread management | timeline, full review, new window, thread v2, per-conversation settings, notification tracking | Visibility and format selection were patched independently. |
| Input and interaction | dictation, permission prompts, image input, ambient home | A visible control does not prove its native or hosted dependency is available. |
| Plugins and skills | plugins, installer, skills/apps page, scheduled tasks | Local plugin surfaces were treated as independently gated. |
| Experimental, security, and configuration | experiments, sandbox configuration, profile sidebar | Configuration visibility and enforcement are separate concerns. |
| Platform and desktop companion | appshots, local-host features, pet/avatar overlays | Platform-local and hosted companion behavior were mixed in the registry and need capability-level classification. |

The patch taxonomy added four dimensions—requirements, provisions,
affordances, and audience—but the labels are declarative metadata, not proof of
offline operability.

### 2. Startup authentication or presentation barrier

The final unmerged patch targets a native function said to evaluate
`chatgpt_ios_login_screen_presentation_style`. It replaces the first eight
bytes at file offset `0x457042c` with `mov w0, #1; ret`, intending to select
no-auth mode before the login dialog.

This is not the same class as renderer feature flags. It selects whether the
application enters the workspace at all. It is also unverified for the mapped
build. The patch description still says it forces
`chatgpt_ios_no_auth_holdout`, while the final commit says the patched function
actually evaluates `chatgpt_ios_login_screen_presentation_style`; that source
inconsistency is itself evidence that the semantic owner must be rediscovered.

### 3. Real runtime and hosted-service dependencies

The patcher never demonstrated that a gate replacement supplies missing data,
credentials, IPC handlers, provider bootstrap, server APIs, or paid/hosted
services. Image input was labelled `custom-provider`; other entries were
labelled `local-only`; neither label establishes backing behavior. These
dependencies must be classified through runtime tracing, not inferred from a
gate name or a rendered control.

## Package surfaces

### Electron surface

- `Contents/Resources/app.asar`: packed renderer and application resources.
- `Contents/Resources/app.asar.unpacked`: unpacked native modules and other
  payloads referenced by `unpacked: true` ASAR header entries.
- `Contents/Resources/app.asar.orig`: adjacent first-use backup created by the
  tool.
- `Contents/Info.plist`: `ElectronAsarIntegrity["Resources/app.asar"]` is
  rewritten after repacking.
- `Contents/Frameworks/Codex Framework.framework`: signed with framework
  entitlements.
- Nested helper apps inside Codex and Sparkle frameworks: signed before their
  containers to avoid Team-ID/library-validation mismatch.
- `Contents/Frameworks/Sparkle.framework`: re-signed after nested helpers.
- The top-level `.app`: signed last with app entitlements.

The unpacked tree is a first-class package surface. The initial packer dropped
639 `unpacked` entries, including `better_sqlite3.node`, and produced an app
that could not launch. The repair preserved the header flag, size, integrity,
and executable mode while leaving bytes in the sibling unpacked tree.

### Native surface from the unmerged experiment

- `Contents/Frameworks/ChatGPT.framework/Versions/A/ChatGPT`: fixed-offset
  Mach-O patch target.
- The same path plus `.orig`: first-use backup.
- `ChatGPT.framework`, other known frameworks, nested helper apps, and the
  top-level bundle: re-signed after mutation.

The source decides between these modes by presence: ASAR wins when present;
the native path is used only when `app.asar` is absent and the framework binary
exists. It does not validate bundle version, architecture, code-directory hash,
Mach-O slice, or complete artifact identity before choosing a mode.

### Tool-owned external surfaces

- `tooling/chatgpt-ffs/patches.d/*.json`: external regex or unmerged binary
  patch definitions.
- `~/.codex/chatgpt-ffs/state.json`: mutable lifecycle state.
- `~/.local/bin/chatgpt-ffs` and shell PATH fragments: installation surface.

These are historical layout choices, not necessary locations for a future
solution.

## Patch mechanisms

### Regex replacement in ASAR JavaScript

Each `Modification` declares a glob, regex, replacement, and description. The
historical feature bypasses replace minified calls containing numeric gate IDs
with `!0`. Detection reports:

- `not_applied` if a target file contains the original regex;
- `applied` if target files exist but the original regex is absent;
- `unknown` if no target file is found or ASAR parsing fails.

The unmerged tail broadened the call regex from a single word to
`[\w$?.]+` after optional-chaining syntax could otherwise produce invalid
JavaScript, and added a two-phase gate-ID scan so a surviving numeric literal in
a non-call context did not imply the patch was unapplied.

The historical background-subagent patch also widened its file glob after a
bundler rechunk moved the gate. That is evidence that semantic identifiers and
validated control-flow shapes are more durable than chunk names, not evidence
that a broad `*.js` glob is safe across builds.

### Fixed-offset native bytes

`BinaryModification` records a bundle-relative file, offset, expected original
bytes, and patched bytes. It intends to refuse a write when bytes at that
offset match neither state. This is a useful mutation-precondition shape, but
it is not artifact discovery or version validation. The implementation also
compares prefixes without first rejecting a short read in `apply` and `revert`,
so even its local byte guard needs hardening before reuse.

The native history is especially important negative evidence:

1. An accessor at `0x456dea0`/patch at `0x456ded0` was judged unused.
2. A read at `0x57244` was then patched.
3. A second read at `0x45705b8` was added because the first did not remove the
   login wall.
4. Both reads were then reverted as dead code, in favor of replacing the
   function prologue at `0x457042c`.

The final commit records no successful disposable-copy launch, air-gap trace,
or GLM workflow. Its offset and bytes are leads, not an accepted patch.

## Integrity repairs

The Electron path repairs two independent integrity layers.

### ASAR entry integrity

Every packed or unpacked file receives:

- algorithm `SHA256`;
- a lowercase hex whole-file digest;
- a 4 MiB block size;
- lowercase hex digest per block;
- one SHA-256 block even for an empty file.

Executable bits and `unpacked: true` placement are preserved. Repository tests
cover round trips, nested unpacked entries, missing unpacked directories, hex
encoding, empty-file blocks, and executable metadata.

The use of hex is not cosmetic: the earlier base64 implementation caused
Electron integrity validation to freeze during module loading.

### Bundle-level ASAR integrity

After copying the rebuilt ASAR, the tool hashes the installed destination and
updates `ElectronAsarIntegrity["Resources/app.asar"].hash` in `Info.plist`.
The native path does not use this layer.

The unmerged `verify` command checks ASAR readability or framework-binary
presence, backup presence, top-level signature, payload hash drift, and actual
versus recorded patch IDs. Hash mismatch is diagnostic; an applied patch absent
from state is a failure, while state entries absent from the payload are
treated as pending reapply.

## Signing steps

Mutation invalidates OpenAI's Developer ID signature, so the tool replaces it
with an ad-hoc signature.

The established ordering is:

1. Sign nested helper `.app` bundles inside known frameworks.
2. Sign `Codex Framework.framework` with framework entitlements when present.
3. Sign `Sparkle.framework` when present.
4. In the native experiment, sign `ChatGPT.framework` with framework
   entitlements when present.
5. Sign the top-level app last with app entitlements.
6. Run `codesign --verify --verbose` on the top-level app.

The ordering is reusable. A historical disposable-copy observation attributes
a launch freeze and 100% CPU loop to helper apps retaining OpenAI's Team ID
while their parent was ad-hoc signed; nested-first signing reportedly restored
normal launch and helper processes.

The exact entitlement templates are not reusable requirements. They are a
hard-coded superset including JIT, unsigned executable memory, disabled library
validation, DYLD environment variables, audio, camera, Apple Events, network
client/server, and user-selected file access. The tool does not derive and
compare the original entitlements, justify each expansion, or minimize them.

The verification is also incomplete as a future contract: it omits `--deep`
and `--strict`, does not explicitly verify every nested signed object, and some
nested-helper and Sparkle signing subprocesses ignore errors. A valid top-level
check therefore should not be treated as complete supply-chain or runtime
validation.

## Rollback and update guarantees

### Merged state

The merged branch always preserves the first adjacent ASAR backup and can
rebuild a chosen patch set from it. `revert all` therefore means rebuild with no
selected modifications. `restore` copies the backup into place, repairs the
plist hash, re-signs, and saves applied IDs for later `reapply`. `update`
guides restore, external app update, and reapply.

It does **not** guarantee automatic rollback. Signature verification failure is
only reported as a warning and the operation can still return success.

### Unmerged ASAR state

The unmerged tail improves the ASAR transaction:

- zero-match modifications produce warnings;
- install/sign/verification failure triggers a reinstall from `app.asar.orig`;
- rollback updates the recorded installed hash;
- rollback signature is checked;
- incomplete rollback is returned as a distinct failure;
- synthetic tests cover successful commit, signature-triggered rollback,
  incomplete rollback, zero matches, empty selection, and verification drift.

That is the strongest reusable lifecycle shape in the history, but it still
depends on the adjacent backup actually being pristine and applicable.

### Unmerged native state

The native path has no equivalent automatic rollback block. It restores the
backup before applying, but if binary mutation, signing, or verification fails,
`commit_binary_changes` catches the exception and returns failure without
reinstalling the backup. Native restore/reapply commands exist, but they are a
later manual recovery path, not a transaction guarantee. No native workflow
tests cover this path.

### Update assumptions and gaps

`reapply` compares the current payload hash with the one in global state. On a
mismatch, it deletes the sibling backup, creates a fresh backup from the current
payload, and reapplies saved IDs. This assumes the mismatch means a legitimate
app update, saved IDs remain semantically valid, and the current payload is
pristine. It records neither the previous artifact identity nor the expected
new one.

A future solution must treat rollback and update as explicit artifact
transactions: immutable source identity, verified destination identity,
mutation preconditions, atomic replacement where possible, complete nested
signature verification, behavioral health checks, and automatic restoration
for every mutation surface.

## Version assumptions

### Electron implementation

The merged registry has no explicit supported-build manifest. Compatibility is
implicitly assumed from:

- the presence and parseability of `app.asar`;
- bundle paths and known framework names;
- minified JavaScript chunk globs;
- numeric Statsig IDs and regex call shapes;
- the shape of `ElectronAsarIntegrity`;
- the ASAR header and unpacked-tree layout.

History shows these assumptions drift: a gate moved chunks after an update,
the first packer dropped 639 unpacked entries, base64 integrity hashes froze
loading, and incomplete nested signing froze launch. A successful regex match
is therefore necessary but insufficient compatibility evidence.

### Native experiment

The source says `v1.2026.183+`, assumes Apple-silicon instruction bytes, and
hard-codes one file offset. It has no build-number check, whole-binary hash,
architecture check, symbol or control-flow discovery, or isolated runtime test.
The three successive target changes demonstrate that an expected byte sequence
at an offset does not establish reachability or authority.

There is no evidence in the branch that `v1.2026.183+` is equivalent to the
Wayfinder baseline `26.707.71524` (`5263`). The two package models must be
reconciled by inspecting the disposable baseline artifact, not by selecting one
from naming or chronology.

## Reusable requirements versus historical details

| Reusable requirement | Historical or unverified detail to rediscover |
| --- | --- |
| Bind all decisions to an immutable app build, architecture, bundle ID, payload hashes, and pristine provenance. | `v1.2026.183+`, build compatibility inferred from file presence, and a global unkeyed state file. |
| Separate startup authentication, remote feature configuration, provider authorization, and genuinely hosted runtime capabilities. | Numeric gate IDs, label claims such as `local-only`, and the assertion that one presentation-style function is authoritative. |
| Discover a semantic control point and prove reachability before defining a patch. | Chunk globs, minified call regexes, `0x457042c`, its eight expected bytes, and predecessor dead-code offsets. |
| Refuse mutation when exact preconditions do not match; never count zero matches as success without an explicit decision. | Broad JS globs and the native fixed-offset byte check as sufficient compatibility detection. |
| Rebuild from an immutable pristine input or use an atomic, reversible mutation transaction. | First-use sibling `.orig` assumed pristine. |
| Preserve every unmodified package surface, including unpacked native modules, executable metadata, and resource layout. | The observed counts of 639 unpacked and 5,382 packed entries. |
| Repair each integrity layer using the exact format expected by the baseline. | Electron ASAR hex/block metadata and plist keys if the baseline is not that Electron format. |
| Preserve or minimally reproduce required entitlements; sign deepest dependencies first and verify every resulting code object. | Known framework names, hard-coded broad entitlement templates, ad-hoc signing as the only possible strategy, and top-level non-strict verification. |
| Automatic rollback must cover ASAR, plist, native binary, signatures, state, and any later shim/config surfaces. | The unmerged ASAR-only rollback implementation and manual native recovery. |
| Keep state scoped to the artifact and app copy; detect interrupted operations and externally changed payloads. | `~/.codex/chatgpt-ffs/state.json` with only one hash and patch-ID list. |
| Validate visible UI and backing behavior in a never-authenticated isolated profile under explicit network denial. | Commit-message observations from earlier disposable copies and any assumption that a visible gate-backed surface works offline. |
| Validate launch, GLM configuration/inference, projects, worktrees, threads, modes, permissions, and local skills/plugins as one acceptance matrix. | “All patches apply” and signature success as proxies for the minimum offline workflow. |

## Validation evidence from this research

Merged source at the Wayfinder branch head:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tooling/chatgpt-ffs -p 'test_*.py' -v

Ran 23 tests in 0.019s — OK
```

Isolated export of `origin/ivan/chatgpt-unflag` at
`811e2783c440142eeb1d9a1d4e178d2eba2278f6`:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tooling/chatgpt-ffs -p 'test_*.py' -v

Ran 42 tests in 0.060s — OK
```

The 42 tests exercise ASAR round trips and metadata, regex patch definitions
and detection, state serialization, ASAR install rollback with stubbed signing,
and `verify` decisions. They do not exercise fixed-offset native patching,
native backup/install/rollback, actual `codesign`, app launch, network denial,
authentication bypass, or GLM 5.2.

Both source ranges pass `git diff --check`.

## Primary history

- [Initial ASAR patch manager](https://github.com/nisavid/agents/commit/128e9dbaa5e253a4029570129d8c9474ee673467)
- [Preserve unpacked ASAR entries](https://github.com/nisavid/agents/commit/0d05e8f3c8caff044e4e7b12d6fa0be25c7714e2)
- [Expand the feature-gate registry and taxonomy](https://github.com/nisavid/agents/commit/805ac061eb0cd3eac8eae56006ba32e4b0b2546c)
- [Repair ASAR integrity encoding and executable metadata](https://github.com/nisavid/agents/commit/2ab408781c7f0a55ca2baa17a757c69933ef1531)
- [Repair nested signing order](https://github.com/nisavid/agents/commit/ce5e80bc709db2a17da63521a4350b8084f76c38)
- [Add restore, reapply, and update](https://github.com/nisavid/agents/commit/ef2336e4426af9cbdc21b838731af69bd91dda73)
- [Document bundler rechunk drift](https://github.com/nisavid/agents/commit/044d5b016c3ea6788582a7e156a815d3d7d721f7)
- [Add ASAR rollback and closed-loop verification](https://github.com/nisavid/agents/commit/410241e30f29896f4b7277103df1dbbb0c55c94d)
- [Add workflow tests](https://github.com/nisavid/agents/commit/dafb3470c53b55d3f6f9f425600a77c6ea1ea8f8)
- [Introduce the native fixed-offset experiment](https://github.com/nisavid/agents/commit/13461865573cedd822eeac0b659668e39303f5f3)
- [Record the second ineffective native target](https://github.com/nisavid/agents/commit/fb4fea1fefe61063d9d455ffefd75e50a55194a6)
- [Select the final unverified native function target](https://github.com/nisavid/agents/commit/811e2783c440142eeb1d9a1d4e178d2eba2278f6)

## Resolution gist

Retain the legacy tool's artifact-bound, preconditioned, integrity-aware,
nested-signing, rollback-first lifecycle as requirements. Treat its renderer
gate registry and native no-auth patch only as discovery evidence: neither the
historical IDs/globs nor the final fixed offset establish the authoritative
control point or a working offline GLM workflow for build `26.707.71524`.
