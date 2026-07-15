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

## Permission boundary

Do not run the live seam until Ivan has reviewed the final artifact hash and
manually granted that exact helper Accessibility access in System Settings.
Rebuilding or re-signing produces a new artifact and requires a new review and
grant. If macOS displays a TCC or SecurityAgent dialog, stop and hand control to
Ivan; the helper never requests a prompt itself.

## No-permission verdict

Green for the source, build, input-policy, selector-policy, and runner-seam
slice. The retained final no-permission artifact is arm64 and ad-hoc signed,
with SHA-256 `7127f8d2f6903a4381dc86d2030e98c3be4a16a3ae964a9fed6721aa4d682c3c`
and CDHash `0379633126d0a4758dcd001d0118f9fe310eccf6`. A clean build in a second
disposable directory produced the same SHA-256. The helper self-test, forbidden
API and sensitive-symbol allowlists, path-policy fixtures, renderer transition
oracle, authoritative project-state fixtures, runner shell syntax, and
cold-handoff self-test all passed.

## First live invocation

The manually granted first invocation proved the exact helper was trusted and
bound to the copied app's validated PID, paths, and live/static signatures. The
renderer issued one trusted click on the unique project control, but the helper
performed its first AX enumeration before the native Open panel was published
and failed closed with zero qualifying panels. It emitted no panel-validated or
project-selection event, and authoritative project state remained at zero exact
fixture rows. All owned processes and listeners closed; route isolation and
source/copied app integrity remained intact.

The runner no longer relies on a fixed delay after the renderer click. The
helper now polls read-only for at most five monotonic seconds, validates the
same process identity before and after every AX snapshot, retries only while no
panel-shaped candidate exists, and fails immediately on malformed, duplicate,
unauthorized, ambiguous, or drifted state. The wait contains no AX or input
action. The live native project selection remains unexecuted with the rebuilt
artifact above and blocked on its fresh manual Accessibility grant.
