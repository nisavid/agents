Type: prototype
Status: open
Assignee: nisavid
Blocked by: 11
Related: 08

## Question

Does the exact copied app complete the minimum offline workflow through its renderer and persist the resulting local state when configured for the pinned OptiQ profile through the hardened gateway?

## Current evidence

The scoped renderer cold-restart slice passes. The exact copied app reached the
main UI without OpenAI state, completed and persisted an exact sentinel turn,
cold-stopped, relaunched, reopened the same local thread and rollout, and
completed a second exact sentinel turn. It displayed Settings, Plugins, Skills,
Tasks, and the model control. Requests reached only the authenticated gateway
and pinned OptiQ upstream; gateway authentication, secret separation,
namespaced tool continuation, listener isolation, integrity, and cleanup checks
all passed. See
[`research/12-validate-offline-gui-workflow.md`](../research/12-validate-offline-gui-workflow.md).

The ticket remains open for worktree and permission interaction, project-local
skill visibility, and reconciliation of the renderer's appended
reasoning-selection label with the pinned catalog. Native project selection is
complete through the real renderer and native chooser.

The worktree slice now has a deterministic, no-remote acceptance harness. It
sets the exact worktree root through the renderer, selects the unique local
`New worktree` command, cold-reopens the resulting thread, and binds the UI
markers to Git worktree porcelain, `state_5.sqlite`, per-worktree
`codex-thread.json`, and rollout `session_meta.cwd`. Its self-tests pass; the
exact copied-app GUI run remains unexecuted and this is not runtime evidence.

Default and Plan mode behavior is complete. The copied-app probe observed both
renderer controls, completed one turn in each mode, and bound each renderer
output hash to a persisted completion with the exact corresponding
`collaboration_mode.mode` value. Exact renderer-visible model identity is also
complete: the renderer displayed `Qwen3.5-2B-OptiQ-4bit (no-think)` and did not
display the fallback `Custom Light` label. See
[`research/12-validate-offline-gui-workflow.md`](../research/12-validate-offline-gui-workflow.md).

## Acceptance

- Launch only the separately named exact app copy with fresh isolated state and the pinned OptiQ profile; keep the installed app, normal profile, global Codex state, and unrelated model servers untouched.
- Submit a deterministic text and tool turn from the renderer and verify the request reaches only the authenticated gateway and pinned OptiQ upstream.
- **Complete:** Materialize a local thread from a user message, reopen it after a cold restart, and continue it from the renderer.
- **Complete:** Select a local project through the native chooser and bind it to renderer and persisted thread state.
- Create a Git worktree without remote Git and verify its state survives a cold restart.
- Observe one allowed and one denied local operation by their effects, not merely by prompt visibility.
- **Complete:** Exercise Default and Plan modes and verify their observable local turn settings.
- Exercise bundled and preseeded local skill or plugin paths and verify network-dependent extensions fail explicitly without blocking the local core.
- Verify settings expose every control required by this workflow and that renderer-visible model metadata matches the pinned profile or fails red.
- Capture one to three focused screenshots plus artifact-bound, secret-redacted assertions; remove all owned processes, listeners, and disposable state at completion.
