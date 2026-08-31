# Native Codex subagents

This is the initial operational binding for ChatGPT Codex and Codex CLI/TUI.
They are separate target surfaces with separate adapter profiles, but both use
the harness's native subagent tool as a leader-owned child transport. The
binding is skill-mediated: `native_codex.py` sequences freeze and record state,
while the skill invokes the native tool between those operations. The module is
not a launcher and does not turn caller-supplied facts into portable
attestation.

The normal topology for either profile is:

| Dimension | ChatGPT Codex | Codex CLI/TUI |
| --- | --- | --- |
| Product family | `codex` | `codex` |
| Surface | `chatgpt-codex` | `codex-cli-tui` |
| Relationship | `child` | `child` |
| Ownership | `leader-owned` | `leader-owned` |
| Transport | `native-tool` | `native-tool` |

Do not use this binding to create or steer a user-owned task. That ownership
requires explicit user consent and a separately classified route.
App-server is an optional later transport for dispatch, not a Codex surface and
not an initial-release dependency. Its narrowly supported metadata-only use for
permitted-account status inventory is a preflight inspection surface, not a
dispatch or task-work transport.

## Freeze before launch

First apply the verified `model-transition` module to the prior state, exact
lifecycle event, current role classification, optional operator selection, and
fresh route evidence with its complete permitted-route preflight. Do this for
new subagents, follow-ups, resumes, retries, capacity recovery, and
reclassification. A denial stops before the native tool accepts task data. A
`new-task` decision is invalid here because this binding owns only leader-owned
children.

Then freeze the complete content-addressed Rolecasting plan, including its
exact request, target version and executor, topology, authorized transition,
bounded return and stop contracts, requested authority, and consumer assurance
minima. Requested authority is intent; it is not evidence that the product
enforced effective authority.

Call `freeze_native_dispatch` before invoking the native subagent tool. The
freeze validates and binds the complete
`rolecasting-model-transition-decision-v1`, plan and request digests, dispatch
ID, exact Codex surface, version, executor, context, read-only authority
intent, and assurance minima. The transition payload digest must equal the
request digest, and its target must equal the native profile.
It derives the fixed child, leader-owned, native-tool topology. It rejects the
dispatch before launch when any consumer minimum exceeds the selected native
profile.

The current native tool schemas do not return a product-owned model execution
attestation or a per-spawn authority-enforcement callback. Their truthful
profile maximum is therefore:

| Evidence dimension | Maximum |
| --- | --- |
| Target | `controller-observed` |
| Model | `self-reported` |
| Topology | `controller-observed` |
| Effective authority | `self-reported` |
| Execution result | `controller-observed` |

The complete plan may bind a selected model and reasoning effort, but an opaque
plan digest does not prove that the product executed them. Likewise, prompt
denials of subdelegation and external action do not prove that the product
enforced those denials. If a consumer requires stronger model or authority
assurance, return `BLOCKED` before spawning. Do not weaken the frozen minimum
as a fallback.

## Invoke and observe

After a successful freeze, invoke exactly one native subagent for the frozen
dispatch. Pass the bounded worker request and least authority allowed by the
live tool schema. Preserve the frozen context; a changed worktree, scope,
request, target, ownership, or authority requires a new freeze.

The caller supplies the guard to `native_codex.py` as the verified
`model-transition` module. Missing or changed guard bytes fail closed before
spawn. The repository module remains a sequencing seam, not the harness
actuator; the owning harness must make the freeze mandatory at its payload
boundary.

Take the agent or worker ID, session or task ID, context binding, and
launch acknowledgement only from the native tool protocol. Continue with the
same native controller until it returns terminal status observations and the
result observation. Preserve failed, timed-out, and cancelled terminals rather
than turning them into clean completion.

Transport completion is not usability. After the result, apply the frozen
verification and stop contracts through the same leader, retain the exact
verification observation digest, and record usability as a strict Boolean. A
`completed` transport may still be blocked, incomplete, or unverified and must
then record `usable: false`. Every non-completed terminal must also be unusable.
Never infer usability from the terminal label or model prose.

Model-generated text is never host evidence. Do not parse worker prose for its
agent ID, session, target, model, permissions, status, or result provenance.
Worker statements may remain self-reported evidence, but they cannot populate
controller-observed fields.

Call `record_native_observation` only after the terminal native result. Supply
the exact frozen binding and transition identities plus the host-protocol
launch, status, result, and verification digests and the strict usability
Boolean. The recorder rejects cross-bound plan, request, transition, dispatch,
or context values, missing or out-of-order terminal status, malformed
verification, non-Boolean usability, and any usable non-completed result. Its
output is an in-process same-leader record with
`portable_evidence: false` and `product_attested: false`.

## Assurance boundary

Same-leader live controller observation is useful for ordinary operational
delegation, but it is not portable persisted attestation. The recorder is not
registered as a Task Witness producer or issuer. It cannot satisfy canonical
publication, cannot create product-attested facts, cannot promote self-reported
model or authority facts, and cannot authenticate a result after the live
controller boundary is lost.

Keep the Rolecasting provider's producer and issuer inventories empty until a
qualified product integration authenticates the exact execution and binds its
own implementation identity. A controller transcript, unit test, or model
claim is not a substitute for that integration.
