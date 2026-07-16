# Validate the offline GUI workflow

## Question

Can the exact disposable copied app select a local project through its real
renderer and native Open panel, confirm that project in the renderer, submit a
deterministic local turn, and retain the gateway, namespace, isolation,
integrity, and cleanup contracts?

## Verdict

Green for the native-project renderer workflow on ChatGPT/Codex
`26.707.71524` (`5263`). The same harness also retains green dedicated results
for cold renderer continuation and Default/Plan mode persistence. Native
permission decisions and native worktree controls remain separate acceptance
surfaces.

The native-project run completed this trusted sequence:

1. The renderer reached the local main UI and proved that the nonce fixture was
   not already selected.
2. It clicked exactly one visible, enabled `Choose project` control.
3. It selected exactly one `New project` menu item and exactly one
   `Use an existing folder` menu item.
4. The copied app opened a native directory chooser at the disposable
   fixture's parent directory.
5. The PID-bound helper found the unique chooser as a sheet of the validated
   copied-app window, found the exact fixture row, wrote only that row to
   `AXSelectedChildren`, reread the selection, and pressed the enabled chooser
   action once.
6. The renderer independently exposed the exact nonce project name before it
   submitted the prompt.
7. The local turn completed through the reviewed authenticated gateway and
   OptiQ model. The renderer displayed the pinned model identity and no
   fallback model metadata.

The native helper does not drive the renderer. The renderer driver does not
drive the native panel. Their evidence is joined only by the generated fixture
identity and the runner's ordered stage gates.

## Native project-picker acceptance harness

The runner creates a fresh local-only Git fixture, copies the verified source
application to a separately named disposable bundle, and applies the exact
default-path seam described in `08-validate-preferred-route.md`. The seam points
the native dialog at the fixture parent, not at the fixture itself, so the
helper must identify and select the exact row.

The renderer driver fails closed unless each control in the sequence is unique,
visible, enabled, and has the exact accessible name or text. Its preparation
phase emits `native-project-picker-precondition-ready`,
`native-project-picker-control-clicked`, `renderer-project-menu-observed`,
`renderer-new-project-menu-opened`, and
`native-project-picker-final-control-ready` without pressing the final item.
The runner then obtains `open-panel-absence-validated` from the exact PID-bound
helper before a fresh driver invocation revalidates `Use an existing folder`.
If the renderer popover closed with the first CDP session, that fresh invocation
reopens only the same exact `Choose project` and `New project` controls, then
presses the exact final item and emits `native-project-picker-requested`. No
keyboard accelerator, fixed coordinate, blind timing assumption, or broad text
match is part of this route.

The native helper binds to the already validated copied-app PID. It validates
the process start, executable, bundle, and static and live signature identities
before reading Accessibility state or performing its one selection mutation.
Its sheet discovery and row search are bounded by monotonic deadlines and node
limits. A missing, malformed, duplicate, disabled, nonwritable, or ambiguous
candidate fails immediately or remains pending only where the contract permits
unpublished intermediate state.

After the native chooser closes, the renderer confirmation phase requires one
control naming the exact nonce project. `14-project-state.py` separately binds
that selection to exactly one authoritative `state_5.sqlite.threads.cwd` row
for the fixture.

The verified run is:

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

The copied app is ad-hoc signed only after its ASAR payload, ASAR header
integrity, and `Info.plist` integrity value are updated. It launches with
`--use-mock-keychain` only inside the disposable run. The source archive and
installed application are never rewritten or launched by this workflow.

## Successful native-project evidence

The renderer and native evidence agree on the exact fixture without sharing an
input mechanism:

- renderer: unique `Choose project` → `New project` →
  `Use an existing folder`;
- native helper: unique `Open` sheet, `navigation: selected-children`, one
  `project-selection-requested` event, and `selectionActionCount: 1`;
- renderer confirmation: `renderer-project-selection-confirmed` with
  `matched: true` and `uniqueControl: true`;
- persistence: exactly one authoritative thread CWD for the fixture; and
- turn: a nonempty completed renderer response through the pinned local model.

The gateway and upstream observer each recorded completed terminal responses.
Inbound credentials were replaced before upstream, negative authentication
checks remained closed, and the namespace probe reconstructed its second call
using the continuation mapping established by the first response.

No remote socket, credential, request-body, or token-canary leak was observed.
The source ASAR remained exact, the copied ASAR and header matched the reviewed
patched hashes, the bundled Codex binary remained exact, and every owned
process and listener exited.

## Cold-restart continuation

The renderer workflow retains an opt-in two-phase cold-restart path. Phase one
submits the deterministic `COLD_PHASE_ONE_OK` turn, binds the exact thread UUID,
rollout, prompt, completed output, and task-complete message, then terminates
only the copied app process group. Phase two relaunches the same disposable
copy and isolated state, reopens the exact thread, submits
`COLD_PHASE_TWO_OK`, and requires both turns to remain in the same rollout.

Use:

```sh
ROUTE_MODE=gateway \
GATEWAY_COMMIT=6307d37b76918c19f2e3bc0fd506434531aadeb2 \
GUI_WORKFLOW=true \
GUI_COLD_RESUME=true \
PROBE_EXPECT=renderer-cold-resume \
  .scratch/chatgpt-airgap-unlock/research/08-run-prototype.sh
```

The handoff is accepted only when the first app group and its descendants are
gone, shared provider processes remain alive, the second renderer reopens the
same task, both completed output hashes match persisted state, and cleanup
closes the remaining owned groups and listeners.

## Dedicated mode workflow

The dedicated mode path validates one persisted Default turn and one persisted
Plan turn. It requires the exact renderer controls for entering and leaving
Plan mode, a nonempty completion in each mode, matching renderer and persisted
output hashes, exact collaboration-mode metadata, and one shared rollout.

Use:

```sh
ROUTE_MODE=gateway \
GATEWAY_COMMIT=6307d37b76918c19f2e3bc0fd506434531aadeb2 \
GUI_WORKFLOW=true \
GUI_MODES=true \
PROBE_EXPECT=renderer-modes \
  .scratch/chatgpt-airgap-unlock/research/08-run-prototype.sh
```

## Worktree acceptance harness

The worktree slice remains a separate native surface. It creates a clean
local-only `main` fixture, uses the verified native project-selection route,
saves an isolated worktree root through renderer settings, selects the unique
`/worktree` → `New worktree` control, completes a first turn, cold-restarts the
copied app group, reopens the same thread, and completes a second turn.

`12-worktree-state.py` accepts that slice only when:

- `git worktree list --porcelain -z` exposes exactly the fixture checkout and
  one clean linked worktree at the original commit;
- the linked worktree's `codex-thread.json` owner matches the only database
  thread for that exact worktree CWD;
- exactly one rollout carries the same thread identity and exact worktree CWD;
  and
- renderer selection, cold reopen, deterministic turns, and persisted output
  bindings all agree.

The intended run is:

```sh
ROUTE_MODE=gateway \
GATEWAY_COMMIT=6307d37b76918c19f2e3bc0fd506434531aadeb2 \
GUI_WORKFLOW=true \
GUI_COLD_RESUME=true \
GUI_WORKTREE=true \
GUI_NATIVE_PROJECT_PICKER=true \
NATIVE_PICKER_DEFAULT_PATH_SEAM=true \
NATIVE_GUI_PROBE_BIN=<reviewed-helper-path> \
NATIVE_GUI_PROBE_SHA256=<reviewed-helper-sha256> \
PROBE_EXPECT=renderer-worktree \
  .scratch/chatgpt-airgap-unlock/research/08-run-prototype.sh
```

Native worktree control remains false until its baseline, first phase, cold
phase, transport, integrity, process, listener, and copied-app cleanup gates
all pass in that dedicated mode.

## Local model and transport

All renderer workflows use the immutable model
`mlx-community/Qwen3.5-2B-OptiQ-4bit:no-think` at revision
`adc8669eb431e3168aeb4e320bd7b757914350e2`. The UI must display
`Qwen3.5-2B-OptiQ-4bit (no-think)` and must not expose fallback model metadata.

Codex sends Responses requests only to the authenticated loopback
`codex-ns-proxy`. The gateway commit and blob are pinned by
`08-run-prototype.sh`; the gateway replaces the inbound bearer with a distinct
upstream bearer, forwards only to the fixed loopback observer and OptiQ, emits
sanitized terminal markers, and preserves namespace-call continuation.

## Integrity, isolation, and cleanup

The runner treats the modified copied ASAR as an expected disposable artifact,
not as an unchanged vendor file. It requires the exact patched ASAR and header
hashes, the matching `ElectronAsarIntegrity` value, a valid deep signature and
designated requirement, and the unchanged copied Codex binary. It separately
requires the source ASAR to retain its vendor hash.

Final cleanup owns the copied app, host, renderer, proxy, gateway, observer,
and OptiQ process groups. The verdict stays red unless all groups are gone, all
reserved listeners are closed, no remote socket remains, no generated token is
present in regular disposable state, and all isolated databases pass their
integrity checks. The run root and copied app remain as disposable local
evidence until manually removed.
