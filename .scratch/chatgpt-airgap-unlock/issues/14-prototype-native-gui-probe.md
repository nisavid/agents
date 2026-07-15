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
- Require explicit Open Folder authorization for project selection, validate
  the exact direct `File` → `Open Folder…` AX menu item and its Command-O
  metadata after identity and trust validation, and bracket one `AXPress` with
  process-identity checks.
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

The exact reviewed helper artifact is installed at the canonical path. The live
native run remains blocked until Ivan manually grants that helper Accessibility
access in System Settings. Rebuilding or re-signing the helper invalidates the
grant until reverified.

## Current prototype evidence

The repo-owned Swift helper, deterministic no-permission checks, disposable
build command, and opt-in runner seam are implemented in `research/14-*` and
the Ticket 08/12 runner. The helper binds to the copied executable by PID,
process start time, canonical running paths, and static/live code signatures;
uses only PID-scoped AX state; refuses path, control, or process ambiguity; and
requires a separate explicit flag before the audited PID-scoped Open Folder
menu press and another flag before the Command-Shift-G fallback are available.
The same binary provides a bounded read-only menu-inspection phase that records
the exact validated topology and metadata with zero actions, then uses the
runner's common audited teardown. The helper records the Open Folder action before validating the
native panel. The fallback accepts only one newly created child of the original
validated panel, and the final press remains bound to that original panel's
exact AX identity. Project adoption requires a nonce renderer transition plus
an exact authoritative `threads.cwd` transition.

A manually granted first live invocation validated the exact copied-app process
and helper trust, then failed closed before any AX mutation because the Open
panel was not yet visible. It issued no project selection, created no project
record, preserved app integrity and isolation, and cleaned up every owned
process and listener. The helper now performs one bounded, read-only readiness
wait while retaining the exact process identity; ambiguity, malformed state,
or drift still fails immediately.

The fourth run proved that one Command-O keyboard action posted by the exact
trusted helper to the focused copied PID still did not produce an AppKit panel.
Extracted build source confirms a direct `File` → `Open Folder…` command, so the
helper now rejects keyboard delivery for that step and performs one exact
PID-scoped AX menu press instead.

The fifth inspection run did not reach the copied app or helper. Both host
app-server drivers omitted the shared profile's required protected-helper path
definition, so the initial sandbox command failed before app launch. The runner
now propagates the exact protected path to the initial and cold-restart host
sandbox commands without exporting it into their isolated child environments.

The sixth run reached the exact granted helper after the kernel executable
already matched, but AppKit had not registered the copied process yet. A
read-only diagnostic observed no `NSRunningApplication` for samples 0–5 at
100-millisecond intervals, exact copied bundle and executable URLs from sample
6, and `finishedLaunching` only at sample 13. Process validation now performs a
bounded five-second registration wait. Missing registration or URLs retries;
termination or a published path mismatch fails immediately. Immutable kernel
identity is checked around every sample and around the unchanged code-signature
validation. No AX object or action is available before that gate completes.

The seventh inspection run passed process validation after seven AppKit polls
and passed Accessibility trust, then observed a direct File menu with other
published children but no exact `Open Folder…` item. The readiness policy now
retries zero matching items as an unpublished intermediate within the existing
deadline. Duplicate matches and matching items with malformed role, enabled,
action, or Command-O metadata remain immediate failures. Identity validation
still brackets every read, and the inspection phase remains mutation-free with
`actionCount: 0`.

The eighth inspection run reached the exact `Open Folder…` item but failed the
combined Command-O metadata check before any action. That check could not
distinguish missing publication from a present wrong value. Missing or empty
command character, missing virtual key, and missing modifiers now retry within
the existing deadline. Any present wrong value fails immediately with a
field-specific diagnostic, and a present value of the wrong CF type fails as
malformed; case-normalized `O`/`o`, integral virtual key `31`, and integral
modifiers `0` remain required. The inspection phase still authorizes no action.

No live AX action has occurred for this revision. The reviewed retained artifact
is recorded in `research/14-native-gui-probe.md`; read-only menu inspection
requires a manual grant for that exact artifact before the next live step.
