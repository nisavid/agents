# Prototype the PID-bound native GUI probe

## Question

Can one separately named helper bind to the exact already-running copied app by
PID, fail closed on identity or AX ambiguity, and select only the disposable Git
fixture through the copied app's standard Open panel?

## Source and artifact

The prototype source is `research/14-native-gui-probe.swift`. It uses
`AXUIElementCreateApplication(pid)` and never performs a system-wide AX lookup,
launches or terminates an app, prompts for Accessibility, mutates TCC, or targets
an application by bundle identifier.

Build an ad-hoc-signed disposable artifact with:

```sh
.scratch/chatgpt-airgap-unlock/research/14-build-native-gui-probe.sh \
  <absolute-output-directory>
```

Run the deterministic no-permission checks with:

```sh
.scratch/chatgpt-airgap-unlock/research/14-test-native-gui-probe.sh
```

The live runner seam is opt-in. It requires the exact artifact path and SHA-256,
passes the copied app PID and canonical copied paths explicitly, requires
`--press-open-folder-menu-item` for the selection phase, and authorizes the
PID-targeted Command-Shift-G fallback separately. The helper checks path, process-start,
executable, running-bundle, static signature, and live signature identity before
checking trust or reading AX state. After trust, it waits up to five monotonic
seconds for the exact source-backed `File` → `Open Folder…` menu path. The item
must be unique, direct, enabled, advertise `AXPress`, and expose Command-O
character, virtual-key, and modifier metadata. Missing or unpublished menu state
is retried; malformed, duplicate, disabled, or mismatched published state fails
immediately. The helper revalidates the exact AX identity and process, performs
one `AXPress`, rechecks the process immediately afterward, then records bounded
evidence. It also rechecks process-start
and executable identity before and after every Open-panel readiness snapshot and
immediately before every AX mutation. The readiness wait is limited to five
monotonic seconds at 100-millisecond intervals. Any malformed, duplicate, or
ambiguous candidate fails immediately; the readiness loop performs no AX or
input action. The helper appends only structured, path-hashed JSONL events
beneath the disposable run root.

After the exact artifact has been reviewed and manually granted, the seam is:

```sh
GUI_NATIVE_PROJECT_PICKER=true \
NATIVE_GUI_PROBE_BIN=<reviewed-artifact-path> \
NATIVE_GUI_PROBE_SHA256=<reviewed-sha256> \
NATIVE_GUI_PROBE_KEY_FALLBACK=true \
GUI_WORKFLOW=true \
PROBE_EXPECT=renderer-native-project \
  .scratch/chatgpt-airgap-unlock/research/08-run-prototype.sh
```

The runner first requires one enabled, visible renderer control with the exact
accessible name `Choose project` as the expected selector precondition. It
checks that control only to prove the per-run nonce fixture is not already
selected; it does not infer broader project state from the label or perform any
input action. The runner resolves exactly one copied-app executable in the owned
process group and invokes the helper with explicit Open Folder authorization.
The helper presses the exact PID-scoped menu item, records exactly one
`open-folder-menu-item-pressed` event, and validates the native panel afterward.
The runner requires that evidence order before accepting panel validation, then
requires a renderer transition from a nonmatching project to the per-run nonce
project. After the renderer creates its first task, the runner waits for the
authoritative database and required schema through a bounded, read-only
readiness check. `research/14-project-state.py` then requires the authoritative
`state_5.sqlite.threads.cwd` record to transition from zero exact fixture rows
to exactly one before recording the native project-picker gate as complete.
The copied app's Seatbelt profile denies writes to the exact reviewed helper
path, and the runner rechecks its inode, SHA-256, and signature immediately
before execution.

Before the first mutation, the same granted artifact can run a read-only menu
inspection through the runner:

```sh
GUI_NATIVE_PROJECT_PICKER=true \
NATIVE_GUI_PROBE_INSPECT_MENU_ONLY=true \
NATIVE_GUI_PROBE_BIN=<reviewed-artifact-path> \
NATIVE_GUI_PROBE_SHA256=<reviewed-sha256> \
GUI_WORKFLOW=false \
PROBE_EXPECT=native-menu-inspection \
  .scratch/chatgpt-airgap-unlock/research/08-run-prototype.sh
```

That mode validates the same bounded topology and metadata, emits one
`open-folder-menu-validated` event with `actionCount: 0`, performs no panel wait
or input action, and continues through the common process, listener, signature,
and copied-app integrity closeout before reporting success. Key fallback must be
disabled.

The first live attempt clicked `Choose project` and started the helper after
that single renderer interaction. That control only opens the project-selector
menu, so no native panel existed for the helper to inspect. Driving the menu is
not deterministic: the empty-project menu can expose either a direct
`New project` action or a submenu containing `Use an existing folder`, depending
on a feature gate. A later third live run sent the stable `Meta+O` accelerator
through CDP, but it did not reach AppKit; the helper observed no native panel and
timed out after 45 readiness polls. The renderer is therefore limited to the
precondition. A fourth run proved that the exact trusted helper posted one
Command-O action to the focused copied PID, but AppKit still exposed no native
panel. PID-targeted keyboard delivery is therefore not a sufficient menu-command
contract. Extracted build 26.707.71524 (5263) source confirms the command is a
direct `File` → `Open Folder…` item; the PID-bound helper now invokes that exact
item through AX and owns the subsequent native-panel evidence.

A fifth inspection-only run stopped before launching either the copied app or
the helper. The host app-server sandbox rejected the shared profile because its
`NATIVE_GUI_PROBE_BIN` path parameter had no value, then the host probe timed
out. This was runner plumbing, not native-menu evidence. The runner now passes
the exact protected helper path as a sandbox profile definition to both the
initial and cold-restart host app-server commands; the helper path is not added
to either isolated child environment.

The sixth inspection run reached the exact granted helper and confirmed the
kernel PID executable path matched the copied executable, but failed before AX
inspection because AppKit had not registered an `NSRunningApplication` yet. A
separate non-AX diagnostic sampled registration every 100 milliseconds: samples
0–5 were unavailable, sample 6 onward exposed the exact copied bundle and
executable, and `finishedLaunching` remained false until sample 13. The helper
now waits up to five monotonic seconds for a nonterminated AppKit registration
with those exact canonical paths. Missing registration or either missing URL is
retryable; termination or any published mismatch fails immediately. The helper
revalidates PID, process start, and kernel executable before and after every
sample and around the unchanged static/dynamic signature validation. Launch
completion is diagnostic only and is not part of this identity gate.

The seventh inspection run passed process validation after seven AppKit polls
and passed the existing Accessibility trust gate. Its first menu snapshot
published the direct File menu and other children but no exact `Open Folder…`
item, so the helper failed before any action. Zero matching items is now treated
as an unpublished intermediate and retried within the existing five-second
monotonic bound. A published duplicate or a matching item with the wrong role,
enabled state, action, or Command-O metadata still fails on its first snapshot.
The retry loop continues to validate process identity around every read and
contains no AX mutation; inspection evidence continues to require
`actionCount: 0`.

The eighth inspection run reached the exact `Open Folder…` item after process
validation and Accessibility trust, then failed the combined Command-O metadata
check before any action. The prior check did not identify whether an optional
attribute was absent or a published value was wrong. Missing or empty command
character, missing virtual key, and missing modifiers now remain retryable
within the same bounded read-only wait. Once present, a wrong character, key,
or modifier value fails on that snapshot with a field-specific diagnostic; a
present value of the wrong CF type fails as malformed. Case-normalized `O`/`o`,
integral virtual key `31`, and integral modifiers `0` remain the only accepted
command metadata.

## Permission boundary

Do not run the live seam until Ivan has reviewed the final artifact hash and
manually granted that exact helper Accessibility access in System Settings.
Rebuilding or re-signing produces a new artifact and requires a new review and
grant. If macOS displays a TCC or SecurityAgent dialog, stop and hand control to
Ivan; the helper never requests a prompt itself.

## No-permission verdict

Green for the source, build, input-policy, selector-policy, and runner-seam
slice. The temporary no-permission artifact for this revision is arm64 and ad-hoc signed,
with SHA-256 `160d8a49d9730b48efa8e85120f352c3e21fb5fd43c6a0854e9b496462f7b4b1`
and CDHash `d4fff90e19ef9fd245285e18cf2d122bbbf6efd6`. A clean build in a second
disposable directory produced the same SHA-256. The helper self-test, forbidden
API and sensitive-symbol allowlists, path-policy fixtures, renderer transition
oracle, authoritative project-state fixtures, runner shell syntax, and
cold-handoff self-test all passed. The previously granted canonical artifact
was not rebuilt or modified.

The live native project selection remains intentionally unexecuted with this
temporary artifact and blocked on separate review and a manual Accessibility
grant.
