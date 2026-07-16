# Prototype the PID-bound native GUI probe

## Question

Can one separately named helper bind to the exact already-running disposable
copied app by PID, fail closed on process or Accessibility ambiguity, and select
only the generated Git fixture through the copied app's native directory
chooser?

## Source and artifact

The helper source is
`.scratch/chatgpt-airgap-unlock/research/14-native-gui-probe.swift`. It uses
`AXUIElementCreateApplication(pid)` and never performs a system-wide
Accessibility lookup, launches or terminates an app, prompts for Accessibility,
mutates TCC, or targets an application by bundle identifier.

Build an ad-hoc-signed disposable artifact with:

```sh
.scratch/chatgpt-airgap-unlock/research/14-build-native-gui-probe.sh \
  <absolute-output-directory>
```

Run the deterministic no-permission checks with:

```sh
.scratch/chatgpt-airgap-unlock/research/14-test-native-gui-probe.sh
```

The live runner accepts only an exact helper path and SHA-256. The copied app's
Seatbelt profile denies writes to that reviewed path, and the runner rechecks
its inode, hash, and signature immediately before execution.

## Verified project-selection contract

The native selection route is green for the disposable copy of ChatGPT/Codex
`26.707.71524` (`5263`). It separates renderer authorization from native panel
mutation:

1. The renderer validates and clicks the unique visible, enabled
   `Choose project` control.
2. The renderer selects the unique `New project` item and validates the unique
   `Use an existing folder` item without pressing it.
3. One helper invocation binds to the copied app's PID and completes a bounded
   Accessibility traversal proving that no `AXSheet` exists. It records that
   attestation and remains alive.
4. The runner confirms that exact helper process is still alive, then a fresh
   renderer invocation revalidates the exact final item. If the renderer
   popover did not survive the first CDP session, it reopens only the exact
   `Choose project` and `New project` controls before pressing the final item.
   No menu accelerator or keyboard fallback is authorized in this route.
5. The same continuously running helper waits for the unique native chooser,
   revalidates the same PID and identity boundary, selects the exact fixture
   row through `AXSelectedChildren`, and presses the chooser action once.
6. The renderer independently confirms the exact nonce project after the
   chooser closes.

The helper records structured, path-hashed JSONL beneath the disposable run
root. A successful selection emits, in order:

- `inputs-validated` before process inspection;
- `process-validated` with the exact PID, derived process start time,
  executable hash, and AppKit registration poll count after internal bundle
  and code-signature validation;
- `open-panel-absence-validated` before the final renderer request;
- `open-panel-validated` with title `Open` and
  `navigation: selected-children`; and
- `project-selection-requested` with `selectionActionCount: 1`.

## PID and process boundary

The helper receives the copied app PID plus canonical expected bundle and
executable paths explicitly. It derives and pins the kernel process start time,
then validates the running paths and static/live code identities before
Accessibility trust or AX reads. AppKit registration is polled only for that
PID and must publish the same bundle and executable paths.

The helper revalidates process start and executable identity around every
Open-panel readiness snapshot and immediately before each AX mutation. A
terminated process, PID reuse, changed executable, changed bundle, signature
drift, or published identity mismatch fails closed.

## Bounded native panel discovery

The verified route does not recursively traverse the Electron renderer tree.
It reads the exact copied application's windows, follows bounded `AXChildren`
edges, deduplicates the resulting sheets, and evaluates only the parented sheet
candidates. This avoids both global targeting and unbounded descent into the
web content accessibility tree. The pre-request absence traversal fails closed
on incomplete reads or depth, node, and fanout limits.

Readiness uses monotonic deadlines, fixed polling intervals, per-window node
limits, and explicit depth limits. No qualifying sheet is a retryable
unpublished state within the bound. A malformed, duplicate, unauthorized,
ambiguous, or drifted candidate fails immediately. The readiness loop contains
no AX mutation.

The accepted chooser must be one unique visible sheet with the expected Open
panel structure, one enabled chooser action, and a list exposing one exact
fixture row. Any published panel destination must equal the canonical fixture
parent. The exact row must publish only the canonical fixture path; the helper
rejects lookalike, ancestor, descendant, symlinked, remote, non-file,
malformed, or duplicate evidence.

## Exact fixture selection

The copied dialog starts at the canonical parent of the generated fixture. The
helper therefore selects the fixture by native list identity rather than
typing or navigating a path:

- find exactly one list and exactly one row whose canonical URL resolves to the
  expected fixture;
- require `AXSelectedChildren` to be writable on that exact list;
- write a single-element selection containing only that row;
- reread `AXSelectedChildren` and require exact identity with that row;
- revalidate the app, panel, list, row, destination, and enabled chooser
  action; and
- invoke the chooser action once.

The helper never sends Command-Shift-G, Command-O, coordinates, pasteboard
data, or system-wide input in this route. It never presses the chooser action
when selection publication or destination validation is incomplete.

## Disposable default-path seam

The native dialog's deterministic starting directory is supplied only to the
copied app. The patcher
`.scratch/chatgpt-airgap-unlock/research/14-patch-native-picker-default.mjs`
replaces one exact 164-byte call site in the copied `app.asar` with an
equal-length payload that adds `defaultPath: process.env.NDP` to the existing
`Select Project Root` dialog options.

Before writing, the patcher validates the ASAR topology, the main entry's
integrity, one source payload, no patched payload, and two exact copies of the
old main-file hash in the header. After writing, it validates the patched main
file, both updated header hashes, the unchanged archive size, and one patched
payload. The runner also updates the copied bundle's
`ElectronAsarIntegrity:Resources/app.asar:hash` value.

The exact copied artifacts are:

| Item | SHA-256 |
| --- | --- |
| Source ASAR | `d28f31b4bbb04c519be65c2af8277d8c5faf77b4239ee89b928f0a7423dacd84` |
| Patched copied ASAR | `06c4fd5cbb3662911cc62c3569042bd5657f3476a99ef9edc47bd51d5380026f` |
| Source ASAR header | `e3023f2d1c334ba8ba80bd22a97553d412a4616a86d75ca81e258e974061f3c7` |
| Patched ASAR header | `c069ef0e4e826ec2fd8db41a626f3e26f3edead477053a12703830ce7e047b75` |
| Patched main file | `a8082ef44bf3aa4e30e7c663472da502d15bed35073a4c125903f4b9291961cc` |
| Native helper | `b461c021c0493e5c8998f028b3a59c1400dd8c91f1ce86e11c60c99103b8fc3b` |

The runner ad-hoc signs the copied outer bundle with identifier
`com.openai.codex` and requires its deep signature and designated requirement
to validate before launch. This signing step is confined to the disposable
copy. The source archive and installed application remain untouched.

## Keychain boundary

The copied app launches with Chromium `--use-mock-keychain`. That switch is
confined to the isolated copied-app command and does not change how the
installed application is launched. The successful reviewed run produced no
Keychain alert. The harness never resets or modifies the user's real keychain.

If macOS displays a TCC, SecurityAgent, or keychain dialog despite this
contract, stop the live run and leave the dialog to Ivan. Do not select
`Reset To Defaults` as part of automation.

## Live invocation

After Ivan has reviewed the final helper hash and manually granted that exact
artifact Accessibility access, run:

```sh
ROUTE_MODE=gateway \
GATEWAY_COMMIT=6307d37b76918c19f2e3bc0fd506434531aadeb2 \
GUI_NATIVE_PROJECT_PICKER=true \
NATIVE_PICKER_DEFAULT_PATH_SEAM=true \
NATIVE_GUI_PROBE_BIN=<reviewed-helper-path> \
NATIVE_GUI_PROBE_SHA256=<reviewed-helper-sha256> \
GUI_WORKFLOW=true \
PROBE_EXPECT=renderer-native-project \
  .scratch/chatgpt-airgap-unlock/research/08-run-prototype.sh
```

`NATIVE_PICKER_DEFAULT_PATH_SEAM=true` is required for this native-project
workflow. The discarded menu, go-to-folder, focus, and keyboard options no
longer exist.

## Permission boundary

Do not run the live seam until Ivan has reviewed the final helper hash and
manually granted that exact helper Accessibility access in System Settings.
Rebuilding or re-signing produces a new artifact and requires a new review and
grant. The helper never requests a TCC prompt or edits TCC state.

## Verdict and cleanup

Green for the exact helper identity, renderer authorization, bounded parented
sheet discovery, exact `AXSelectedChildren` selection, one chooser action, and
renderer confirmation. The current route does not rely on activation repair,
global focus, keyboard delivery, or coordinate input.

The successful full smoke test continued from project selection through the
authenticated local `codex-ns-proxy`, OptiQ
`Qwen3.5-2B-OptiQ-4bit:no-think`, and namespace continuation. It observed no
remote socket or token leak. Cleanup closed every owned app, host, proxy,
gateway, observer, and model process and every reserved listener. Final
integrity checks proved the copied ASAR matched the exact patched hash while the
extracted source bundle and installed application remained unchanged.
