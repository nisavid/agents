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
check missed non-exact model output. The replacement renderer oracle submits
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
