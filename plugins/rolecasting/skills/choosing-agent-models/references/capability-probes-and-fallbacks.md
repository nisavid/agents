# Capability Probes And Fallbacks

## Prove An Exact Pair

1. Identify the exact target family, surface, version, executor, and transport
   from the invocation topology receipt.
2. Inventory every route permitted by the operator and worker contract. For the ambient Codex account, `codex debug models` may provide its live catalog. For another permitted Codex account, use only an authenticated, version-bound metadata surface whose provider-side effects are independently proven compatible with the status-only authority described below. For every non-Codex target, refresh that target harness's live model catalog instead.
3. Inspect the target executor tool or schema for accepted model slugs and reasoning efforts.
4. Record the exact live-catalog and target-executor-schema intersection, and pass only a pair present in both.

Catalog presence never proves that a native subagent, task, CLI, app-server,
remote API, or peer accepts the pair. Proof for one product surface does not
prove its paired surface. Model support also does not prove relationship,
ownership, transport, or assurance. When the target exposes no selection
fields, omit them and use an appropriate inherited or environment-fixed model.

Model selection does not grant authority to invoke a model, peer, CLI, API, or external harness. Status inspection, capability proof, and task execution are separate authorities.

The standing Codex status authority carries no task data and authorizes no
caller-requested state change. Select only a permitted account home from the
private binding catalog, expose it only to the bounded status process, and use
an operating-system temporary working directory. The status surface must bind
its implementation, exact version, requested operations, and evidence that
startup and those operations cannot refresh or persist authentication, migrate
configuration, synchronize plugins, write cache or database state, create or
mutate task or turn state, or otherwise change provider state. Do not
read `auth.json` or another credential file directly, request or permit token
refresh, create a task or turn, send task data, access workspace files or
tools, change login or configuration, delegate or execute work, or treat the
refresh as the separately authorized harmless task-work probe.

Codex app-server 0.149.0 does not satisfy that boundary. Its
`account/rateLimits/read` path obtains managed authentication through a path
that may proactively refresh stale authentication and persist the rotated
token. `account/read` with `refreshToken: false` limits only that request; it
does not make the complete exchange token-refresh-free. Do not launch that
version under the standing status authority, even if a caller labels the
operation read-only. No supported flag or RPC in that version disables the
provider-side persistence while inspecting the existing managed account.

The current package also registers no production route-evidence issuer or
verifier, mandatory payload actuator hook, or atomic authorization-consumption
store. A content-addressed route record or trusted issuer label does not replace
those controls. Until a separately qualified product or harness integration
provides them together with a side-effect-safe exact-version status observer,
alternate-account Daybreak routing remains blocked rather than repaired.

Record a timestamped redacted inventory digest, status-authorization digest,
status-surface evidence digest, and status-evidence digest. Raw account
identifiers and credentials remain local. Missing or stale status triggers the
narrow refresh automatically when an authenticated side-effect-safe surface
exists, even when task execution or project-data authority is absent. If the
surface is unsafe or its effects are unverified, do not invoke it; record
`route-status-unverified`, while retaining the precise surface evidence in the
authenticated route record. If an otherwise eligible refresh is refused,
record `route-status-denied`. Neither outcome proves the model absent or
unavailable. Keep `route-inventory-incomplete`, `route-status-refresh-required`,
`route-execution-unauthorized`, `route-capability-probe-failed`,
`route-model-availability-unproven`, `route-model-absent`,
`route-capacity-unknown`, and `route-capacity-exhausted` distinct. Both
`route-status-unverified` and `route-status-denied` leave the affected route's
inventory incomplete.

During an already authorized no-task-data refresh, you may inspect route metadata exposed for unrelated tasks, including advertised model availability; this does not authorize reading task data. An existing task is eligible only when delegation created it for the current source task and bounded purpose, or the operator identified it as a same-purpose companion. Do not use an unrelated task as an execution or authorization route for current-task work, including to execute with its model or under its account, entitlement, permissions, or context.

## Re-prove Continuation Capability

Initial capability proof does not prove continuation capability. Treat every
follow-up, resume, retry, and capacity recovery as a new transition. Before
sending task data, run a fresh no-task-data refresh against the same private
account binding only through a qualified side-effect-safe surface, and require
the exact selection to remain exposed on the continuation surface. If no
qualified surface exists, capability remains unproven and the continuation
fails closed. A task or session ID and its stored provider or model are identity
facts, not account-affinity or current-capacity evidence.

Require the continuation actuator to select and observe the same private account binding used for the successful initial dispatch. If it cannot, treat capability as unproven and fail closed without sending the continuation payload. A newly created dedicated task or session still requires the owning workflow's creation authority and a fresh capability proof; it does not make an unrelated task eligible.

## Authorize The Transition

The pure `scripts/model_transition.py` guard accepts the prior accepted state,
event, current role classification, optional content-addressed operator
selection, and fresh authenticated route evidence. Its route preflight binds a
complete permitted-route inventory, redacted timestamped status evidence, the
status authorization, the exact status implementation, version, operations,
side-effect-safety evidence, absence of task-data transfer or caller-requested
mutation, observed provider state-change status, and membership of the selected
route. The remaining route evidence binds its issuer, task, payload, immutable
plan identity, actuation ID, eligibility, capability status, execution
authority, capacity, exact target, account, selector, capability, and
selection. The selection records role, exact model and effort or explicit
inherited-fixed absence, qualified classification, provenance, and operator
selection identity. Daybreak means exactly `gpt-daybreak-blue-latest` at Max
for security work.

Apply it before every payload-bearing new task, new subagent, follow-up,
resume, retry, capacity recovery, or reclassification. The actuator validates
the complete `rolecasting-model-transition-decision-v1`, binds its exact
payload and authorization identities, and sends nothing on denial. The
consumer must authenticate the route-evidence issuer and atomically consume
the plan- and actuation-bound authorization; content addressing alone does not
prevent replay. Preserve the returned predecessor state for the next event.

Carry the prior judgment floor unless an explicit content-addressed
reclassification changes it. Security and a prior Daybreak requirement remain
sticky. For mixed-role work, the declared classification equals the hardest
role. Preserve an explicit operator selection until an explicit replacement;
an unavailable operator-selected pair blocks rather than falling through.
Ordinary eligible fallback may change a non-operator selection only when it
still satisfies the current floor. Capacity failover never relaxes the floor,
task identity, target, account binding, or Daybreak requirement.

## Handle Probe Failure

Treat capability as unproven when the executable is absent, a probe exits nonzero, output is malformed or unparseable, or the surface cannot isolate the requested scope and authority. Record the attempted surface, probe, and result. Never invent a slug, selection field, or dispatch.

When a task that must be isolated cannot be created or verified, do not dispatch: return `NEEDS_CONTEXT` or `BLOCKED` so the owning workflow can select its no-runnable-route disposition. Unrelated-task reuse is never a fallback.

Use a sufficient native route only when it preserves the original task contract. When an appropriate inherited or environment-fixed model preserves that contract, omit unsupported model and effort fields. Otherwise, omit model and effort selection and do not dispatch: return `NEEDS_CONTEXT` with the missing fact when new evidence could establish capability, or `BLOCKED` with the failed-probe evidence when no permitted route can satisfy the contract.

## Apply Fallbacks

Choose the lowest accepted effort that preserves the task's judgment margin. Raise effort before widening scope or changing models when reasoning can resolve the uncertainty.

When Luna fits but is absent from the native schema, an accepted Terra pair may
be an eligible fallback. If explicit selection is unavailable, use an
appropriate proven inherited or environment-fixed role only when it preserves
the floor. Never invent a slug or effort.

Daybreak has no fallback. When policy or the operator requires the exact
Daybreak route and its fresh capability or capacity proof fails, return the
chosen no-runnable-route disposition without sending task data. Sol, Terra,
and Luna are not replacements.

For another harness, inspect its local capability surface and use only its exact supported values. Report an unavailable user-requested model rather than silently substituting.

## Prove Fable

Treat Fable as unavailable unless a current proof record for the exact target surface is supplied. The proof record must contain:

- harness and version;
- exact slug and requested effort;
- invocation result; and
- an observable successful response.

Help or catalog advertisement is insufficient. If no current successful proof exists, do not invoke Fable merely to establish one. Only the operator's explicit authorization for the exact proof invocation permits the attempt; that authorization does not itself prove availability. Until all fields are recorded after an authorized successful invocation, treat Fable as unavailable and assign it no fallback priority.
