Type: prototype
Status: open
Assignee: nisavid
Blocked by: 11
Related: 08

## Question

Does the exact copied app complete the minimum offline workflow through its renderer and persist the resulting local state when configured for the pinned OptiQ profile through the hardened gateway?

## Current evidence

The scoped renderer slice passes. The exact copied app reached the main UI
without OpenAI state, submitted a semantic turn through the renderer, received
the expected assistant result, persisted a completed local thread, exposed that
thread in local task navigation, and displayed Settings, Plugins, Skills,
Tasks, and the model control. The request reached only the authenticated gateway
and pinned OptiQ upstream; gateway authentication, secret separation,
namespaced tool continuation, listener isolation, integrity, and cleanup checks
all passed. See
[`research/12-validate-offline-gui-workflow.md`](../research/12-validate-offline-gui-workflow.md).

The ticket remains open for native project-picker, worktree, and permission
interaction; Default and Plan mode behavior; project-local skill visibility;
exact renderer-visible model metadata; and cold GUI reopen and continuation.

## Acceptance

- Launch only the separately named exact app copy with fresh isolated state and the pinned OptiQ profile; keep the installed app, normal profile, global Codex state, and unrelated model servers untouched.
- Submit a deterministic text and tool turn from the renderer and verify the request reaches only the authenticated gateway and pinned OptiQ upstream.
- Materialize a local thread from a user message, reopen it after a cold restart, and continue it from the renderer.
- Select or create a local project and Git worktree without remote Git and verify their state survives a cold restart.
- Observe one allowed and one denied local operation by their effects, not merely by prompt visibility.
- Exercise Default and Plan modes and verify their observable local turn settings.
- Exercise bundled and preseeded local skill or plugin paths and verify network-dependent extensions fail explicitly without blocking the local core.
- Verify settings expose every control required by this workflow and that renderer-visible model metadata matches the pinned profile or fails red.
- Capture one to three focused screenshots plus artifact-bound, secret-redacted assertions; remove all owned processes, listeners, and disposable state at completion.
