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
  /private/tmp/chatgpt-native-gui-probe-build
```

Run the deterministic no-permission checks with:

```sh
.scratch/chatgpt-airgap-unlock/research/14-test-native-gui-probe.sh
```

The live runner seam is opt-in. It requires the exact artifact path and SHA-256,
passes the copied app PID and canonical copied paths explicitly, and authorizes
the PID-targeted Command-Shift-G fallback separately. The helper checks path,
process-start, executable, running-bundle, static signature, and live signature
identity before checking trust or reading AX state. It appends only structured,
path-hashed JSONL events beneath the disposable run root.

After the exact artifact has been reviewed and manually granted, the seam is:

```sh
GUI_NATIVE_PROJECT_PICKER=true \
NATIVE_GUI_PROBE_BIN=/private/tmp/chatgpt-native-gui-probe-build/chatgpt-native-gui-probe \
NATIVE_GUI_PROBE_SHA256=<reviewed-sha256> \
NATIVE_GUI_PROBE_KEY_FALLBACK=true \
GUI_WORKFLOW=true \
PROBE_EXPECT=renderer-native-project \
  .scratch/chatgpt-airgap-unlock/research/08-run-prototype.sh
```

The runner asks the renderer's unique visible `Choose project` control to open
the native panel, resolves exactly one copied-app executable in the owned
process group, invokes the helper, and resumes renderer automation only after
the helper records `project-selection-issued`.

## Permission boundary

Do not run the live seam until Ivan has reviewed the final artifact hash and
manually granted that exact helper Accessibility access in System Settings.
Rebuilding or re-signing produces a new artifact and requires a new review and
grant. If macOS displays a TCC or SecurityAgent dialog, stop and hand control to
Ivan; the helper never requests a prompt itself.

## No-permission verdict

Green for the source, build, input-policy, selector-policy, and runner-seam
slice. The final no-permission build produced the arm64 ad-hoc-signed artifact
`/private/tmp/chatgpt-native-gui-probe-build/chatgpt-native-gui-probe` with
SHA-256 `dc53235409bc1fa5888ad6f16df8c491e72565ea48540c7a667fcf15cdda67c9`
and CDHash `b157076f06751402d0119af276d6d845ff03d2f9`. A clean build in a second
disposable directory produced the same SHA-256. The helper self-test, forbidden
API scan, path-policy fixtures, renderer oracle self-test, runner shell syntax,
and cold-handoff self-test all passed.

The live native project selection remains intentionally unexecuted and blocked
on the manual Accessibility grant for this exact artifact.
