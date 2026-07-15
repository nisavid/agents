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
`--invoke-open-folder` for the selection phase, and authorizes the PID-targeted
Command-Shift-G fallback separately. The helper checks path, process-start,
executable, running-bundle, static signature, and live signature identity before
checking trust or reading AX state. After trust, it posts one Command-O action to
the exact PID, with process-identity checks immediately before and after the two
keyboard events, then records bounded evidence. It also rechecks process-start
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
The helper posts the PID-targeted accelerator, records exactly one
`open-folder-accelerator-posted` event, and validates the native panel afterward.
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

The first live attempt clicked `Choose project` and started the helper after
that single renderer interaction. That control only opens the project-selector
menu, so no native panel existed for the helper to inspect. Driving the menu is
not deterministic: the empty-project menu can expose either a direct
`New project` action or a submenu containing `Use an existing folder`, depending
on a feature gate. A later third live run sent the stable `Meta+O` accelerator
through CDP, but it did not reach AppKit; the helper observed no native panel and
timed out after 45 readiness polls. The renderer is therefore limited to the
precondition, while the already PID-bound helper owns the real Command-O action
and the subsequent native-panel evidence.

## Permission boundary

Do not run the live seam until Ivan has reviewed the final artifact hash and
manually granted that exact helper Accessibility access in System Settings.
Rebuilding or re-signing produces a new artifact and requires a new review and
grant. If macOS displays a TCC or SecurityAgent dialog, stop and hand control to
Ivan; the helper never requests a prompt itself.

## No-permission verdict

Green for the source, build, input-policy, selector-policy, and runner-seam
slice. The retained final no-permission artifact is arm64 and ad-hoc signed,
with SHA-256 `d121b249e880c2042182e59dd974af781ef8c3ec930deae3c2c4c4c200f61d0f`
and CDHash `1f8ba727c27c1b26c437bf21795d1c301f4902b5`. A clean build in a second
disposable directory produced the same SHA-256. The helper self-test, forbidden
API and sensitive-symbol allowlists, path-policy fixtures, renderer transition
oracle, authoritative project-state fixtures, runner shell syntax, and
cold-handoff self-test all passed.

The live native project selection remains intentionally unexecuted with this
artifact and blocked on its manual Accessibility grant.
