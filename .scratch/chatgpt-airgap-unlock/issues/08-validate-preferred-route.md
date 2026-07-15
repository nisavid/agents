Type: prototype
Status: open
Related: 11, 12, 13

## Question

Does a disposable proof of the preferred route launch the exact app build without OpenAI state, reach GLM 5.2, and exercise the minimum local workflow under explicit network denial while remaining reversible and producing enough evidence to define stable patch preconditions and failure checks?

## Current evidence

The bounded semantic route passes against the pinned local OptiQ fixture. The exact copied app reaches the main UI without OpenAI state, bundled Codex completes Responses text and namespaced tool-call continuations through the hardened authenticated gateway, listener and token boundaries hold, cold resume works, cleanup is complete, and the copied app plus embedded Codex remain byte-identical. See [research/08-validate-preferred-route.md](../research/08-validate-preferred-route.md).

The scoped renderer workflow also passes. A renderer-originated semantic turn
reached only the authenticated gateway and pinned OptiQ upstream, produced the
expected assistant result, persisted as a completed local thread, appeared in
the local task navigation, and exposed Settings, Plugins, Skills, Tasks, and the
model control. Every gateway, credential, integrity, and cleanup assertion
remained green. See
[research/12-validate-offline-gui-workflow.md](../research/12-validate-offline-gui-workflow.md).

The renderer model identity and fallback gates now pass through a run-local
`model_catalog_json`: the copied app displays the pinned OptiQ name instead of
`Custom Light`, while the app-server catalog reports default reasoning `none`,
no reasoning levels, text-only input, and the model-config context limit. The
full renderer capability presentation remains open because the composer still
appends its independent `Medium` selection label. See
[research/08-validate-preferred-route.md](../research/08-validate-preferred-route.md#pinned-model-metadata).

## Remaining acceptance

- Reopen and continue a local thread through the GUI.
- Exercise local project and worktree controls.
- Exercise permission denial and approval through the GUI.
- Exercise Default and Plan modes through the GUI.
- Verify a project-local skill after selecting a real local project.
- Reconcile the renderer's appended reasoning-selection label with the pinned
  catalog's default `none` and empty supported-reasoning set.
- Preserve the vendor Chromium sandbox on a disconnected VM or true air-gapped machine; the current local semantic harness uses an outer Seatbelt profile and disables Chromium's nested sandbox.
- Validate the explicit GLM 5.2 profile in its target environment when that endpoint becomes available.
