Type: prototype
Status: open
Assignee: nisavid
Related: 08, 12

## Question

Can a small repo-owned macOS Accessibility probe target only the already-running
exact copied app by PID, exercise the native project picker, and hand control
back to renderer automation without changing or ambiguously addressing the
installed app?

## Decision

Use public Accessibility APIs from a separately named helper. Bind it to the
copied app with `AXUIElementCreateApplication(pid)`, verify the PID's canonical
bundle and executable paths are beneath the owned run root, and fail closed on
missing trust, unexpected controls, duplicate matches, path drift, PID reuse, or
any attempt to target `/Applications/ChatGPT.app`.

Do not use bundle-ID targeting, system-wide Accessibility lookup, AppleScript,
System Events, XCUITest launch, coordinate fallback, TCC mutation, or an
automatic Accessibility prompt. Ivan manually grants Accessibility only to the
final reviewed helper artifact when it is ready to run.

## Acceptance

- Build a stable, separately named helper artifact without changing the copied
  vendor app, its identifier, signature, entitlements, ASAR, or native code.
- Accept the copied app PID, expected copied bundle and executable paths,
  disposable Git fixture root, phase, and append-only JSONL event-log path as
  explicit inputs.
- Refuse the installed app, paths outside the owned run root, exited or reused
  PIDs, signature or path mismatch, system-wide lookup, application launch or
  termination, and untrusted Accessibility state.
- Inspect and act on a unique standard Open panel through AX roles, identifiers,
  attributes, and advertised actions; allow only an audited PID-targeted
  Command-Shift-G path-entry sequence when AX actions cannot navigate directly.
- Prove project selection through renderer state and persisted desktop state,
  then exercise worktree and permission behavior through the renderer and verify
  their filesystem, Git, process, and app-server effects.
- Stop for manual handling if an OS TCC or SecurityAgent dialog appears.
- Capture focused renderer evidence without requiring Screen Recording access,
  and preserve the existing listener, process, credential, integrity, signature,
  cleanup, and production-app invariants.

## Permission gate

The live native run is blocked until the exact reviewed helper artifact exists
and Ivan manually grants that helper Accessibility access in System Settings.
Rebuilding or re-signing the helper invalidates the grant until reverified.

## Current prototype evidence

The repo-owned Swift helper, deterministic no-permission checks, disposable
build command, and opt-in runner seam are implemented in `research/14-*` and
the Ticket 08/12 runner. The helper binds to the copied executable by PID,
process start time, canonical running paths, and static/live code signatures;
uses only PID-scoped AX state; refuses path, control, or process ambiguity; and
requires a separate explicit flag before the audited PID-targeted
Command-Shift-G fallback is available.

No Accessibility request, TCC mutation, installed-app launch, or live AX action
has occurred. The reviewed no-permission artifact is recorded in
`research/14-native-gui-probe.md`; the manual permission gate above remains the
next live step.
