# Validate the renderer-only offline GUI slice

## Question

Can the exact copied app submit a deterministic renderer turn, persist it,
cold-stop and relaunch only the app process group, reopen the same local thread,
complete a second turn through the reviewed authenticated gateway, and expose
the local workflow surfaces without automating native dialogs?

## Verdict

Green for the scoped renderer cold-restart continuation. Run-root suffix
`OBXLwu` exited zero after completing both exact sentinel turns, cold-stopping
the copied app, reopening the same persisted thread and rollout, and preserving
the provider, gateway, observer, isolation, integrity, and cleanup contracts.

The project-local skill, exact configured model metadata, native project
picker, native permission decision, and native worktree controls remain red or
unexercised. Ticket 12 remains open for those separate surfaces.

The successful run used:

```sh
ROUTE_MODE=gateway \
GATEWAY_COMMIT=6307d37b76918c19f2e3bc0fd506434531aadeb2 \
GUI_WORKFLOW=true \
GUI_COLD_RESUME=true \
PROBE_EXPECT=renderer-cold-resume \
  .scratch/chatgpt-airgap-unlock/research/08-run-prototype.sh
```

## Successful cold-restart evidence

- The first renderer phase completed `COLD_PHASE_ONE_OK`, persisted its task,
  and observed the Tasks, Settings, Plugins, Skills, and model-control surfaces.
  The second phase relaunched the copied app, reopened the same thread with its
  first output visible, and completed `COLD_PHASE_TWO_OK` in the same rollout.
- The structured handoff recorded ordered before/after markers for terminal
  capture and checks, resume-state capture, app-group stop, and relaunch. The
  first app process group exited and its CDP listener closed before relaunch.
- The gateway and upstream each recorded three terminal completions: two before
  the cold stop and one continuation completion. Both first-phase deltas were
  two, both continuation deltas were one, and continuation transport was
  observed.
- The exact gateway source was commit
  `6307d37b76918c19f2e3bc0fd506434531aadeb2`, blob
  `a368b8f8e919361425763e86ca1c80fcea81825f`, with a 300-second upstream
  timeout and the default 15-second heartbeat. The namespace preflight retained
  continuation reconstruction and exact data-only `[DONE]` checks.
- The persisted thread was `019f6349-e2f2-7c52-b4d1-077c23194a85`. Its rollout
  contained two exact smoke prompts, two user turn events, two assistant
  messages, and two `task_complete` events, with no function calls. The rollout
  SHA-256 was
  `829696a486967d155d83c7cb608667213fa436c70cdcb4b547c33aa7246e63ee`.
- The first turn-identity SHA-256 was
  `34ba4ebb17fb767b8eb39c2958927a6ae35e172936c5ceaf3d18cd9afffb3cac`.
  Its persisted, renderer, and sentinel output SHA-256 values were all
  `6b3f8de7d7bc4217399ef704cf467ee931bef4171d6fe39c6b2e386fc632b6cc`.
  The second persisted, renderer, and sentinel output SHA-256 values were all
  `ee67dab48c757720854e0106fbf36eb4e278b68a0158291e5ae937423e3912e1`.
- No request-body, credential, or remote-socket leak was observed. No
  `auth.json` or shell snapshot appeared. Outbound attempts were blocked; every
  captured socket was loopback. All owned processes were absent and all owned
  listeners were closed after cleanup.
- The copied app ASAR retained SHA-256
  `d28f31b4bbb04c519be65c2af8277d8c5faf77b4239ee89b928f0a7423dacd84`;
  the bundled Codex binary retained SHA-256
  `28699add67540b93390329a740649a9eb9bdbc5538d92c1679c8c6b6fa2c623c`.
  The copied bundle also retained its strict deep signature.

The project-local skill was not visible, exact configured model metadata did
not match, and the three native surfaces were not exercised. Those failures do
not weaken the completed renderer cold-restart contract.

## Historical initial red run

The initial exact run used:

```sh
ROUTE_MODE=gateway \
GATEWAY_COMMIT=7c960b15267e82ef5d5a854bdd54bf53fb9e8135 \
GUI_WORKFLOW=true \
PROBE_EXPECT=renderer-workflow \
  .scratch/chatgpt-airgap-unlock/research/08-run-prototype.sh
```

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

## Historical discarded nested-sandbox attempt

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

## Historical valid semantic renderer run

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
completed the full cold-restart contract in run `OBXLwu`; the successful
evidence is summarized above.

Phase one retains the validated renderer prompt, task, and surface assertions.
It now asks the model to return exactly `COLD_PHASE_ONE_OK` and forbids tool
use. The renderer accepts only that sentinel after trimming outer whitespace
and removing one renderer-added terminal timestamp.
After it completes, the runner reads the isolated Codex rollout and writes a
mode-0600 state artifact binding the exact thread UUID and rollout path to
SHA-256 digests of the first prompt, exact completed turn, its
`task_complete.last_agent_message`, sentinel, and exact trimmed phase-one
renderer text. Intermediate tool calls or `agent_message` events cannot bind
the output; only one `task_complete` for the prompt's exact turn can do so, and
its trimmed final message must equal the sentinel. The distinct persisted and
rendered digests avoid conflating source text with its rendered form. The
runner then stops only the copied app's process group, which includes its
app-server, and requires both that process group and its CDP listener to
disappear. The pinned OptiQ server, authenticated gateway, and observers remain
alive.

Phase two relaunches the same copied bundle with the same isolated home, Codex
home, Electron user-data directory, provider configuration, Seatbelt profile,
and loopback endpoints. The CDP driver selects the persisted local thread by
its first-prompt task label, requires the bound first prompt and the same
exact trimmed renderer text observed in phase one to be visible, then asks the
model to return exactly `COLD_PHASE_TWO_OK` without tools. Extra text, a wrong
phase sentinel, or repeated combined sentinels fail closed.

The runner finally requires one matching second prompt and one
`task_complete` for that exact turn in the original rollout with the original
thread UUID, that the original completed-turn identity and exact-byte
final-output digest are unchanged, and that phase two records sentinel,
persisted-output, and renderer-output digests. A duplicate completion or any
non-exact trimmed final message fails closed. For the gateway route it records
gateway and upstream terminal baselines immediately before the first renderer
launch. It then
requires at least one completion beyond baseline for phase one, at least one
additional completion after the restart for phase two, and a total renderer
delta of at least two. This keeps the two renderer transports distinct from
pre-renderer namespace traffic. The resume-state and assistant-oracle records
retain only hashes, lengths, and semantic results; the gateway and upstream
observers log neither credentials nor request bodies. Existing CDP snapshots
continue to capture renderer text as the GUI evidence surface.

The intended live command is:

```sh
ROUTE_MODE=gateway \
GATEWAY_COMMIT=6307d37b76918c19f2e3bc0fd506434531aadeb2 \
GUI_WORKFLOW=true \
GUI_COLD_RESUME=true \
PROBE_EXPECT=renderer-cold-resume \
  .scratch/chatgpt-airgap-unlock/research/08-run-prototype.sh
```

This remains a development-only semantic probe: it launches only the copied
bundle and retains the existing outer Seatbelt plus `--no-sandbox` constraint.
It does not modify the copied bundle identifier, signature, ASAR, native code,
the installed app, or the operator's real profile and global state.

## Historical repeated-result arithmetic oracle correction

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

## Historical trailing renderer timestamp correction

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
app ASAR and bundled Codex binary unchanged.

## Phase-one semantic success and handoff lifecycle correction

Run-root suffix `syzpUh` used reviewed gateway commit
`8703dbe96841d591e77c1f274e22eb4b2aea9d64`, blob
`b5428c5f938ddf0c27fc3b8e8effe64006ca4382`, with the 300-second
runner-selected upstream timeout. Phase one reached every required GUI surface
and persisted one exact user prompt plus one matching `task_complete` whose
`last_agent_message` was `73 + 19 = 92`.
The rollout also contained two semantically correct intermediate
`agent_message` events. This is phase-one semantic success.

The run stopped before resume-state capture or the copied-app cold stop. Its
structured handoff log ended with `terminal-delta-check` in `failed` state:
the old gateway emitted its terminal record only during later cleanup. The
gateway and upstream logs now contain three completed totals, but those
post-cleanup totals are not the handoff counts captured by the runner and are
not cold-restart evidence.

The next runner checkpoint pinned reviewed gateway commit
`a69e710dbe6a43e513a6f12c118b1abce81241ea`, blob
`ae013796f62c760c7a9424c0c97d01ad155d6ed1`, whose terminal lifecycle fix is a
descendant of `8703dbe96841d591e77c1f274e22eb4b2aea9d64`. The no-app
completed-turn fixtures cover the `syzpUh` repeated-intermediate shape, a
conflicting final, duplicate completion, continuation, and mutation of the
original final across restart.

## Tool-call loop and corrected transport inference

Run-root suffix `OsZeWx` used reviewed gateway commit
`a69e710dbe6a43e513a6f12c118b1abce81241ea`, blob
`ae013796f62c760c7a9424c0c97d01ad155d6ed1`. It never reached a valid renderer
completion: the first renderer terminal deltas were zero, the assistant oracle
matched no output, and the GUI reported `rendererPromptCompleted=false`.

The persisted rollout contains 105 unique `function_call` records, 105 matching
failed `function_call_output` records, and 105 token-count events, but no
`agent_message` or `task_complete`. Every tool call failed with exit 71 because
the nested `sandbox-exec` could not apply its profile. The gateway and upstream
observers each recorded 106 completed Responses calls: one pre-renderer request
plus 105 model/tool steps. A 107th request was incomplete at cleanup.

The verdict assigned all 106 completions to the continuation delta and left the
first-renderer delta at zero because the runner captures its after-first
counters only after a successful CDP phase. No continuation phase actually
ran. This was a model/tool loop, not 106 cold-resume continuations.

The prior inference that a missing downstream `[DONE]` caused this loop was
wrong. OptiQ consumes mlx-lm's Chat Completions `[DONE]` internally, translates
it into a Responses `response.completed` event, and ends the upstream response
without exposing `[DONE]` to the gateway. The run therefore provides no
evidence that gateway commit `a69e710...` dropped an available sentinel. It
never entered the cold-stop or persisted-thread reopen phases. Cleanup closed
the owned processes and listeners, and both the copied app ASAR and bundled
Codex binary retained their original hashes.

The runner now pins reviewed gateway commit
`6307d37b76918c19f2e3bc0fd506434531aadeb2`, blob
`a368b8f8e919361425763e86ca1c80fcea81825f`, which is a descendant of
`a69e710dbe6a43e513a6f12c118b1abce81241ea`. It forwards `[DONE]` only when an
SSE frame contains exactly one data field with that payload and no other
nonempty fields. This is the correct general Responses SSE behavior, but it is
not an `OsZeWx` fix because that OptiQ path exposes `response.completed` and
EOF, not `[DONE]`. The immutable namespace preflight retains tool-calling
coverage and now asserts the exact data-only sentinel shape. `OBXLwu` is the
first live GUI run against this commit.

Current and future cold-resume runs use exact nonnumeric phase sentinels instead
of arithmetic. This keeps the 2B model's GUI smoke deterministic and prevents
a tool-selection loop from masquerading as transport progress. Tool calling
remains validated separately by the namespace preflight.
