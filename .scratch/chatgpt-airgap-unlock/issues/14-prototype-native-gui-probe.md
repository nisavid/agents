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
  Command-Shift-G path-entry sequence when AX actions cannot navigate directly,
  with separate authorization and exact application/panel focus proof before
  posting it.
- Prove project selection through renderer state and persisted desktop state,
  then exercise worktree and permission behavior through the renderer and verify
  their filesystem, Git, process, and app-server effects.
- Stop for manual handling if an OS TCC or SecurityAgent dialog appears.
- Capture focused renderer evidence without requiring Screen Recording access,
  and preserve the existing listener, process, credential, integrity, signature,
  cleanup, and production-app invariants.

## Permission gate

The exact reviewed helper artifact is installed at the canonical path. Its
mutation-free inspection and no-permission focus contract are green. The latest
rebuild invalidated the prior Accessibility grant until Ivan re-adds this exact
artifact.

## Current prototype evidence

The repo-owned Swift helper, deterministic no-permission checks, disposable
build command, and opt-in runner seam are implemented in `research/14-*` and
the Ticket 08/12 runner. The helper binds to the copied executable by PID,
process start time, canonical running paths, and static/live code signatures;
uses only PID-scoped AX state; refuses path, control, or process ambiguity; and
requires a separate explicit flag before the audited PID-scoped Open Folder
menu press, another flag before the Command-Shift-G fallback, and explicit focus
authorization before that fallback are available.
The same binary provides a bounded read-only menu-inspection phase that records
the exact validated topology and metadata with zero actions, then uses the
runner's common audited teardown. The helper records the Open Folder action before validating the
native panel. The fallback accepts only one exact path field and Go control
introduced either within the original panel or one newly related child, and the
final press remains bound to that original panel's exact AX identity. Project adoption requires a nonce renderer transition plus
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

The next attempt was stopped before app launch because the outer harness sandbox
denied the runner's nested `sandbox-exec`; running the unchanged inspection
outside that outer sandbox cleared the harness-only restriction. The copied app
then completed its local sentinel workflow, the helper validated the exact
process and trust state, and all 41 menu polls remained read-only before timing
out in an unpublished state. No menu press or project selection occurred. All
owned processes and listeners closed, all isolated databases passed
`quick_check`, and source/copy hashes remained exact.

The timeout now records only the final member of a bounded pending-state enum,
distinguishing the application menu bar, File item, direct menu, Open Folder
item, command character, and modifiers. It records no AX titles or history and
does not change the immediate rejection or action policy. The next mutation-free
inspection isolated the persistent state to an unpublished command virtual key
after 38 polls.

Apple documents the command character as the primary shortcut key and the
virtual key as physical-key evidence used when two keys can produce the same
character. `O` is unambiguous, so absent virtual-key publication is accepted only
for `attributeUnsupported` or `noValue`; a published key must remain integral
`31`. All other AX read errors, wrong values, and malformed types fail
immediately. Evidence records publication explicitly instead of fabricating a
key value.

The final inspection run, with run-root suffix `CFu4Bp`, validated the direct
File → Open Folder path after three polls. It recorded character `O`, modifiers
`0`, enabled `AXPress`, an explicitly unpublished virtual key, and
`actionCount: 0`. The pinned model and host sentinel contracts passed, all owned
processes and listeners closed, remote sockets and token leaks remained absent,
and source/copy app hashes remained exact.

The subsequent project-selection runs validated the exact menu press and Open
panel. One run showed that a recursive AX snapshot consumed the readiness
budget; bounded raw-window reads and one cached descendant traversal removed
that observer cost. Run-root suffix `fWZ5kY` then completed four bounded polls
with the final state `new-fields=0 go-buttons=0 extra-cancels=0
chooser-enabled=true`. Command-Shift-G produced no accessibility-tree delta,
so the helper failed closed before any path mutation or chooser press. Project
adoption remained zero and all isolation, integrity, database, process, and
listener closeout gates passed.

The helper now strictly proves the exact copied application is frontmost, its
focused window is the original panel, and that panel is focused. If those
postconditions are absent, separate authorization permits only setting the
exact application's `AXFrontmost` attribute and performing `AXRaise` on the
exact panel, after both capabilities are preflighted and with process identity
checks around each action. It then recaptures the path-entry baseline and checks
focus inside the final keyboard-post boundary. Two reproducible builds matched
SHA-256 `b797cca31ba50627b4bb7d17d870d67778e055fff5431094568a25bc9c00e085`;
the canonical arm64 ad-hoc artifact has CDHash
`c9f6321572d96e89e34d1cd919df5f3d6039df47` and awaits a fresh manual grant.

The first focus-enabled run, suffix `XX7Egp`, reached the exact menu press and
Open panel, then failed before focus mutation because the inactive exact app's
strict focused-window reference was not in its current window enumeration. The
helper never acts on that reference. It now retains the original panel's unique
membership and strict typed focus reads while treating a different focused
window as pending until the authorized exact-app frontmost and exact-panel raise
actions establish equality. Cleanup closed every owned process and listener;
the isolated database passed `quick_check` with zero threads, and source/copy
ASAR hashes matched.
