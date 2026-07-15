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
passes the copied app PID and canonical copied paths explicitly, and authorizes
the PID-targeted Command-Shift-G fallback separately. The helper checks path,
process-start, executable, running-bundle, static signature, and live signature
identity before checking trust or reading AX state, then rechecks process-start
and executable identity before and after every Open-panel readiness snapshot
and immediately before every AX mutation. After trust, it waits up to five
monotonic seconds at 100-millisecond intervals while no panel-shaped candidate
exists. Any malformed, duplicate, or ambiguous candidate fails immediately;
the readiness loop performs no AX or input action. It appends only structured,
path-hashed JSONL events beneath the disposable run root.

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
accessible name `Choose project`, proving that no project is selected. It then
sends one trusted CDP `Meta+O` accelerator to the copied app. The bundled
`Open Folder…` command routes directly through the local workspace bridge to
`pickLocalWorkspaceRoots` and its parented `showOpenDialog`; it does not depend
on the project-selector menu's feature-gated shape. The runner resolves exactly
one copied-app executable in the owned process group, invokes the helper, then
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
on a feature gate. The stable app-level `Open Folder…` accelerator bypasses both
menu variants. The renderer driver uses bounded state polling for its
precondition and contains no fixed delay between that assertion and the trusted
accelerator.

## Permission boundary

Do not run the live seam until Ivan has reviewed the final artifact hash and
manually granted that exact helper Accessibility access in System Settings.
Rebuilding or re-signing produces a new artifact and requires a new review and
grant. If macOS displays a TCC or SecurityAgent dialog, stop and hand control to
Ivan; the helper never requests a prompt itself.

## No-permission verdict

Green for the source, build, input-policy, selector-policy, and runner-seam
slice. The retained final no-permission artifact is arm64 and ad-hoc signed,
with SHA-256 `5394b55bddd330f4c6970bd66b5421193051070a6ce7f0fe344a6e34fae07372`
and CDHash `03acdb9a7f766c2bca926324f8de9e3b280cab30`. A clean build in a second
disposable directory produced the same SHA-256. The helper self-test, forbidden
API and sensitive-symbol allowlists, path-policy fixtures, renderer transition
oracle, authoritative project-state fixtures, runner shell syntax, and
cold-handoff self-test all passed.

The live native project selection remains intentionally unexecuted and blocked
on the manual Accessibility grant for this exact artifact.
