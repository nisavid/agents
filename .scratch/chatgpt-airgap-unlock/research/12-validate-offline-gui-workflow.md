# Validate the renderer-only offline GUI slice

## Question

Can the exact copied app submit a deterministic prompt from its renderer,
persist the resulting task, and expose the local workflow surfaces through the
reviewed authenticated gateway without automating native dialogs?

## Verdict

Red. The renderer-to-gateway path, task persistence, local surfaces, trust
boundaries, integrity checks, and cleanup passed. The pinned 2B model did not
return the requested exact sentinel, so the renderer completion oracle failed
closed. Ticket 12 remains open.

The exact run used:

```sh
ROUTE_MODE=gateway \
GATEWAY_COMMIT=7c960b15267e82ef5d5a854bdd54bf53fb9e8135 \
GUI_WORKFLOW=true \
PROBE_EXPECT=renderer-workflow \
  .scratch/chatgpt-airgap-unlock/research/08-run-prototype.sh
```

## Evidence

- CDP inserted the prompt through the renderer's trusted input path and
  submitted it with a trusted Enter event.
- The app created renderer thread
  `019f6285-a06a-7290-a99f-bb1753ee15c3` and persisted a completed turn.
- The persisted `task_complete` event recorded the exact assistant text
  `I'll reply exactly with the requested token.` instead of the requested
  `LOCAL_RENDERER_OK`.
- The reviewed gateway emitted terminal completion, replaced the inbound
  credential, preserved the namespace continuation probe, and did not log a
  request body.
- Settings, Plugins, Skills, Tasks, and the renderer model-control surface were
  observed. The plugin catalog failed explicitly because remote catalog access
  requires ChatGPT authentication; the local core remained usable.
- The renderer did not expose the pinned OptiQ model ID or configured display
  name. Model-metadata matching remains red.
- The native project picker, permission decisions, worktree controls, cold GUI
  reopen, and continuation were not exercised and remain red.
- No `auth.json` or shell snapshot appeared. No remote socket or token leak was
  observed. All owned processes and listeners exited, and the copied app ASAR,
  bundled Codex binary, and strict signature remained unchanged.

The raw run ended with `RENDERER_PROMPT_COMPLETED=false` and
`RENDERER_TASKS_OBSERVED=false` because exact sentinel completion is a
prerequisite for the task-list assertion. The settings, plugin, skill, provider,
gateway, integrity, token, and cleanup assertions independently reported their
observed results.

## Discarded nested-sandbox attempt

The exact-copy run above and earlier direct development runs exposed brittle
submission and result selectors. Synthetic DOM insertion did not prove
submission; trusted renderer input and Enter did, but the exact-sentinel result
check missed non-exact model output. The then-current renderer oracle submits
`What is 73 plus 19? Your final answer must include the decimal result.` and
accepts only one standalone `92` occurrence in one assistant-message output
line. It anchors the output to the assistant's `Copy` action and excludes the
user message, prompt, sidebar, and unrelated DOM text.

A first attempt to run that oracle from inside the parent harness's nested
sandbox acquired the lock but exited with status 71 before starting the
provider, gateway listener, copied app, or renderer. The reviewed gateway
namespace preflight failed with:

```text
sandbox-exec: sandbox_apply: Operation not permitted
```

This infrastructure-only attempt produced no renderer thread or assistant
result and is discarded as GUI runtime evidence. It does not replace or weaken
the first run's transport, persistence, surface, gateway, integrity, or cleanup
evidence.

Before the preflight failure, the runner acquired the fixed-port lock, verified
the pinned app, model, runtime, reviewed gateway commit and blob, copied the app,
and verified its strict signature. After failure, the lock was released and no
owned fixed-port listener remained. Native project selection, permission,
worktree, exact model-metadata, cold reopen, and continuation gates remain red.
Ticket 12 remains open.

## Final valid semantic renderer run

Green for the scoped renderer workflow. The same locked gateway command ran
outside the parent harness's nested sandbox while retaining the runner's own
Seatbelt profiles. The copied app reached its main UI without a login wall,
submitted the arithmetic prompt through trusted renderer input and Enter, and
created thread `019f6297-5e11-7521-a781-a6dafc4a4d58`.

The assistant output line was `73 plus 19 = 92`. The assistant-message oracle
found exactly one standalone `92` in that line, the persisted rollout recorded
the same assistant text and a completed task, and the task-navigation surface
exposed the renderer-created thread by its exact prompt prefix.

The valid run also established:

- The provider request, provider listener, reviewed gateway listener, gateway
  terminal event, upstream terminal event, credential replacement, negative
  authentication checks, namespace continuation, and isolated model list all
  passed.
- The gateway did not log a request body. No remote socket, credential leak,
  `auth.json`, or shell snapshot was observed.
- Settings, Plugins, Skills, the exact renderer-created task entry, and the
  model-control surface were observed. The project-local skill and exact
  configured model metadata were not visible.
- All owned processes and listeners exited. The copied app ASAR and bundled
  Codex binary remained unchanged; the strict copied-app signature had already
  been verified before launch.

Ticket 12 remains open for real native project-picker, permission, and worktree
interaction; project-local skill visibility and exact model metadata; and cold
GUI reopen and continuation. Those gates were not exercised or did not pass in
the scoped renderer run.

## Cold-restart continuation prototype design

The renderer workflow now has an opt-in, two-phase cold-restart path. It has
not been run yet and contributes no runtime evidence to the verdict above.

Phase one retains the validated renderer prompt, task, and surface assertions.
After it completes, the runner reads the isolated Codex rollout and writes a
mode-0600 state artifact binding the exact thread UUID and rollout path to
SHA-256 digests of the first prompt, unique persisted assistant output, and
unique normalized phase-one renderer text. The distinct source and rendered
digests avoid conflating Markdown with its rendered form. The runner then stops
only the copied app's process group, which includes its app-server, and requires
both that process group and its CDP listener to disappear. The pinned OptiQ
server, authenticated gateway, and observers remain alive.

Phase two relaunches the same copied bundle with the same isolated home, Codex
home, Electron user-data directory, provider configuration, Seatbelt profile,
and loopback endpoints. The CDP driver selects the persisted local thread by
its first-prompt task label, requires the bound first prompt and the same
normalized full renderer text observed in phase one to be visible, then submits
a different deterministic arithmetic prompt. Its assistant output must contain
one or more standalone `63` tokens, every standalone integer must be one of
`46`, `17`, or `63`, and the final standalone integer must be `63`.

The runner finally requires that the second prompt and result were persisted
exactly once in the original rollout with the original thread UUID, that the
original persisted output digest is unchanged, and that phase two records one
unique persisted-output digest plus one unique renderer-output digest. For the
gateway route it records gateway and upstream terminal baselines immediately
before the first renderer launch. It then requires at least one completion
beyond baseline for phase one, at least one additional completion after the
restart for phase two, and a total renderer delta of at least two. This keeps
the two renderer transports distinct from pre-renderer namespace traffic. The
new resume-state and assistant-oracle records retain only hashes, lengths, and
semantic results; the gateway and upstream observers log neither credentials
nor request bodies. Existing CDP snapshots continue to capture renderer text as
the GUI evidence surface.

The intended live command is:

```sh
ROUTE_MODE=gateway \
GATEWAY_COMMIT=8703dbe96841d591e77c1f274e22eb4b2aea9d64 \
GUI_WORKFLOW=true \
GUI_COLD_RESUME=true \
PROBE_EXPECT=renderer-cold-resume \
  .scratch/chatgpt-airgap-unlock/research/08-run-prototype.sh
```

This remains a development-only semantic probe: it launches only the copied
bundle and retains the existing outer Seatbelt plus `--no-sandbox` constraint.
It does not modify the copied bundle identifier, signature, ASAR, native code,
the installed app, or the operator's real profile and global state.

## Repeated-result arithmetic oracle correction

A later live phase-one attempt produced the correct response:

```text
73 plus 19 is 92.

73 + 19 = 92
```

The previous exact-one-occurrence oracle rejected that response solely because
`92` appeared twice, so the run stopped before the cold restart. The renderer,
persisted rollout, and completed task were present. First-turn transport was not
attributed because the CDP failure stopped the later accounting; that attempt is
not cold-restart evidence.

The arithmetic oracle now accepts one or more expected-result occurrences only
when every standalone integer in the assistant message belongs to the two
operands or expected result and the final standalone integer equals the expected
result. Phase one therefore allows only `{73, 19, 92}` and must end in `92`;
phase two allows only `{46, 17, 63}` and must end in `63`. A wrong intermediate
integer, a correction from a wrong integer, an unrelated integer, or a wrong
final integer fails closed. Persisted-rollout selection uses the same predicate.

The driver includes a no-app self-test covering repeated-correct, single-result,
wrong-then-corrected, conflicting, and wrong-final responses. It also builds a
synthetic DOM where the exact user prompt contains out-of-set `999`; the oracle
passes the valid assistant node because prompt text outside that anchored node
is excluded.

Run the static oracle cases with:

```sh
node .scratch/chatgpt-airgap-unlock/research/12-cdp-gui-driver.mjs --self-test
```

The cold-restart workflow has not been rerun after this correction.

## Trailing renderer timestamp correction

A second live phase-one attempt persisted the correct response, `73 plus 19
equals 92.`, and completed its task, but the CDP arithmetic oracle returned no
assistant output. The run stopped before the cold restart, so it is not
cold-restart evidence.

The earlier artifact proves that the assistant container can append a rendered
timestamp: its 30-character model output became a 38-character DOM value after
the renderer added `6:23 PM`. The second attempt is consistent with the same
renderer behavior, but its exact appended timestamp was not captured.

The renderer-only oracle now removes exactly one terminal 12-hour timestamp in
uppercase `AM` or `PM` before applying the arithmetic predicate and hashing the
rendered answer. It records whether a timestamp was removed, along with the raw
container length and hashes of the raw container and removed timestamp. The
structured `assistant-output-oracle` event does not emit the raw container body
or timestamp value. Existing renderer-state snapshots continue to capture the
visible GUI for workflow evidence. A timestamp in the middle, a malformed or
out-of-range timestamp, or a conflicting model-produced integer still fails
closed. Persisted-rollout selection is unchanged.

The no-app self-test covers the terminal timestamp, verifies that the renderer
answer hash excludes it, and rejects middle, malformed, and conflicting-number
cases. The next workflow attempt is documented below; it stopped during phase
one before exercising the cold restart.

## Heartbeat gateway low-RAM timeout diagnosis

The first cold-restart attempt against reviewed heartbeat gateway commit
`8703dbe96841d591e77c1f274e22eb4b2aea9d64`, blob
`b5428c5f938ddf0c27fc3b8e8effe64006ca4382`, stopped in phase one. The run-root
suffix was `MbrNDI`; it contributes no cold-restart evidence.

The renderer request reached OptiQ at 19:16:42. OptiQ began a 13,558-token
prefill, and Codex issued the retry at 19:17:12, exactly 30 seconds later. Both
attempts completed the full prefill and then raised `BrokenPipeError` while
writing their first response frames. The gateway recorded two renderer streams
with `terminal_completed=false`; its one completed stream was the separate
namespaces-zero request. The upstream observer likewise recorded one completed
stream and two incomplete renderer streams. The phase-one gateway and upstream
terminal deltas were both zero, and the GUI reported `stream disconnected before
completion: stream closed before response.completed`.

The gateway's 15-second SSE heartbeat kept the downstream Codex connection
alive during silent prefill, but it did not change the independent 30-second
upstream socket timeout. That default closed the OptiQ connection before this
low-RAM smoke workload could produce its first SSE frame.

The disposable runner now sets `NS_PROXY_UPSTREAM_TIMEOUT=300`. This leaves the
gateway's reviewed default unchanged, retains its default 15-second heartbeat,
and prevents the gateway from becoming the first deadline; the renderer oracle
still bounds each GUI phase to 120 seconds. The runtime manifest and immutable
gateway-source record include the selected timeout. The namespace preflight
loads the materialized gateway and asserts that it parses the upstream timeout
as 300 seconds while retaining the 15-second default heartbeat.

The failed run cleaned up all owned processes and listeners and left the copied
app ASAR and bundled Codex binary unchanged. A later attempt passed phase one
but ended before writing its resume-state or cold-stop artifacts. The next run's
structured cold-handoff phases and top-level signal record will disambiguate
that handoff without changing its cleanup behavior.
