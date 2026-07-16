Type: prototype
Status: closed
Assignee: nisavid
Related: 08, 12

## Question

Can one repo-owned macOS Accessibility helper bind to the exact already-running
disposable copied app by PID, causally bridge the renderer's native-picker
request, and select only the generated Git fixture without targeting or changing
the installed application?

## Decision

Use one PID-scoped Accessibility helper invocation for the complete native
boundary. After the renderer prepares and validates the unique
`Use an existing folder` control without pressing it, the runner starts the
helper. That same helper:

1. validates the copied process, canonical paths, process start, executable,
   and static and live code identity;
2. proves through bounded strict Accessibility traversal that no `AXSheet`
   exists and records `open-panel-absence-validated`;
3. remains alive while a fresh renderer invocation revalidates the exact picker
   path and presses only the exact final control;
4. waits for one unique native Open panel owned by the same PID;
5. selects only the canonical fixture row through `AXSelectedChildren`, rereads
   the selection, revalidates the complete identity boundary, and presses the
   chooser action once.

No File-menu inspection or press, activation or focus repair, keyboard input,
coordinates, pasteboard use, system-wide Accessibility lookup, app launch, app
termination, AppleScript, or TCC mutation is authorized.

Exact-build evidence showed that the vendor dialog provides no deterministic
fixture starting directory and publishes no acceptable native navigation path.
That evidence supersedes the earlier mutation-free-copy assumption for this test
seam only. The harness applies one exact equal-length replacement adding
`defaultPath: process.env.NDP` to the copied `app.asar`, updates the copied ASAR
integrity records, and ad-hoc signs only the disposable outer bundle. The patcher
validates the complete old state before its first write and the complete expected
state afterward. The extracted source bundle, official archive, and
the installed ChatGPT application remain untouched.

This is an exact-build-bound disposable validation seam, not a redistributable
replacement patcher or production application build.

## Acceptance

- Bind exclusively to the explicit copied-app PID, bundle, executable, fixture,
  run root, and append-only event log.
- Refuse the installed app, paths outside the owned run root, PID reuse,
  termination, path or signature drift, incomplete AX snapshots, traversal
  limits, malformed topology, and duplicate or ambiguous controls.
- Obtain the zero-sheet attestation and subsequent native selection from the
  same continuously running helper invocation.
- Require any published panel document or URL to equal the canonical fixture
  parent and require the selected row to publish only the canonical fixture.
- Set and reread exactly one `AXSelectedChildren` row, then press exactly one
  enabled chooser action after final process, panel, list, row, destination, and
  action revalidation.
- Confirm the exact nonce project independently in the renderer and in the
  authoritative persisted thread CWD.
- Require Ivan's manual Accessibility grant for the final reviewed helper
  artifact; never prompt for or mutate TCC.
- Launch only the disposable copied app with `--use-mock-keychain`; never reset
  or modify the user's real keychain.
- Preserve source and installed-app integrity, local gateway isolation, secret
  separation, owned-process cleanup, and listener cleanup.

## Resolution

The deterministic helper, patcher, and path-confinement suites pass. Two builds
of the final helper are byte-identical at SHA-256
`906c9af5c02e3ea3dcc520d0a5f86ba68b67ae0d1d493e08c8e3d0e71c978501`.
The full copied-app smoke test proves the ordered same-helper absence, request,
native selection, renderer confirmation, local OptiQ turn, and namespace
continuation contract. It also proves no remote socket or token leak, expected
source/copy integrity, and complete owned-process and listener cleanup. Detailed
artifact and run evidence is recorded in `research/14-native-gui-probe.md` and
`research/08-validate-preferred-route.md`.

Tickets 08 and 12 retain worktree, permission, project-local skill, and
reasoning-label acceptance. Ticket 08 also carries production-isolation and
explicit GLM-profile acceptance when its target environment becomes available,
and ticket 13 retains the dedicated production-isolation work.
