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
