# Capability Probes And Fallbacks

## Prove An Exact Pair

1. Identify the exact target family, surface, version, executor, and transport
   from the invocation topology receipt.
2. For a Codex target, refresh the live catalog with `codex debug models`. For every non-Codex target, refresh that target harness's live model catalog instead.
3. Inspect the target executor tool or schema for accepted model slugs and reasoning efforts.
4. Record the exact live-catalog and target-executor-schema intersection, and pass only a pair present in both.

Catalog presence never proves that a native subagent, task, CLI, app-server,
remote API, or peer accepts the pair. Proof for one product surface does not
prove its paired surface. Model support also does not prove relationship,
ownership, transport, or assurance. When the target exposes no selection
fields, omit them and use an appropriate inherited or environment-fixed model.

Model selection does not grant authority to invoke a model, peer, CLI, API, or external harness. Capability inspection and any proof invocation must remain within the authority already granted by the operator and worker contract.

During an already authorized no-task-data refresh, you may inspect route metadata exposed for unrelated tasks, including advertised model availability; this does not authorize reading task data. An existing task is eligible only when delegation created it for the current source task and bounded purpose, or the operator identified it as a same-purpose companion. Do not use an unrelated task as an execution or authorization route for current-task work, including to execute with its model or under its account, entitlement, permissions, or context.

## Re-prove Continuation Capability

Initial capability proof does not prove continuation capability. Before sending continuation task data, run a fresh no-task-data refresh against the same private account binding and require the exact selected model to remain exposed on the continuation surface. A task or session ID and its stored provider or model are useful identity facts, but a shared session database is not account-affinity evidence.

Require the continuation actuator to select and observe the same private account binding used for the successful initial dispatch. If it cannot, treat capability as unproven and fail closed without sending the continuation payload. A newly created dedicated task or session still requires the owning workflow's creation authority and a fresh capability proof; it does not make an unrelated task eligible.

## Handle Probe Failure

Treat capability as unproven when the executable is absent, a probe exits nonzero, output is malformed or unparseable, or the surface cannot isolate the requested scope and authority. Record the attempted surface, probe, and result. Never invent a slug, selection field, or dispatch.

When a task that must be isolated cannot be created or verified, do not dispatch: return `NEEDS_CONTEXT` or `BLOCKED` so the owning workflow can select its no-runnable-route disposition. Unrelated-task reuse is never a fallback.

Use a sufficient native route only when it preserves the original task contract. When an appropriate inherited or environment-fixed model preserves that contract, omit unsupported model and effort fields. Otherwise, omit model and effort selection and do not dispatch: return `NEEDS_CONTEXT` with the missing fact when new evidence could establish capability, or `BLOCKED` with the failed-probe evidence when no permitted route can satisfy the contract.

## Apply Fallbacks

Choose the lowest accepted effort that preserves the task's judgment margin. Raise effort before widening scope or changing models when reasoning can resolve the uncertainty.

When Luna fits the task but is absent from the native-subagent schema, use accepted Terra at the lowest safe effort. If explicit selection is unavailable, inherit an appropriate fixed model. Never invent a slug or effort, and report a fallback that materially changes confidence, cost, or speed.

For another harness, inspect its local capability surface and use only its exact supported values. Report an unavailable user-requested model rather than silently substituting.

## Prove Fable

Treat Fable as unavailable unless a current proof record for the exact target surface is supplied. The proof record must contain:

- harness and version;
- exact slug and requested effort;
- invocation result; and
- an observable successful response.

Help or catalog advertisement is insufficient. If no current successful proof exists, do not invoke Fable merely to establish one. Only the operator's explicit authorization for the exact proof invocation permits the attempt; that authorization does not itself prove availability. Until all fields are recorded after an authorized successful invocation, treat Fable as unavailable and assign it no fallback priority.
