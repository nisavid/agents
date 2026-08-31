# Dispatch evidence

Rolecasting owns one domain-neutral evidence interface for a frozen worker
dispatch. It records what the leader authorized and what the selected adapter
returned. It does not know review axes, critic counts, specialist semantics,
findings, dispositions, or publication policy.

## Registered owner

The plugin currently registers only
`rolecasting-dispatch-evidence-validator-v3` for
`rolecasting-dispatch-evidence-v3`. Its provider declares empty producer and
issuer inventories. The retained validator closure includes the validator and
the bootstrap renderer, but a validator alone grants no producer or issuer
authority. Task Witness therefore has no current Rolecasting producer or
issuer to place in a new-publication trust snapshot, so no current Rolecasting
bundle can be accepted for publication.

The renderer uses `rolecasting-bootstrap-dispatch-v3` as its bootstrap producer
identity and `rolecasting-bootstrap-adapter-v3` as its bootstrap issuer ID and
contract. Those identities are not registered Task Witness authorities. Task
Witness owns bundle descriptors, canonical JSON parsing, trust selection,
retained validator bytes, and the complete bundle digest.

The sole canonical front door remains Task Witness. A current publication
attempt therefore fails closed:

```text
task-witness validate --bundle <absolute-bundle>
```

Tests may construct private, test-owned bootstrap trust fixtures to exercise
the historical parser. No Rolecasting historical trust has been installed or
retained, and those fixtures are never production or publication evidence.
Rolecasting exposes no runtime-path, trust-path, discovery, or fallback CLI.

## Support, issuance, and assurance

Rolecasting represents product family and surface independently from worker
relationship, ownership, transport, and model choice. Its current surface
vocabulary includes ChatGPT Codex and Codex CLI/TUI for the initial release,
Claude Code and Claude Desktop for the fast follow, and Cursor and Cursor Agent
for the next qualification tranche. `app-server` is a transport and does not
imply an external worker or create another Codex surface by itself.

Portable support means Rolecasting can plan and validate the route. Issuance
requires an adapter qualified for the exact family, surface, version, executor,
transport, and evidence source. Assurance describes how facts were established:

- `product-attested`: the product authenticates the target, model, topology,
  effective authority, and execution result through a qualified issuer;
- `controller-observed`: the leader or controller directly observes selection,
  launch, isolation, events, and result, without product attestation; and
- `self-reported`: the worker reports its own identity or behavior.

Consumers freeze an exact minimum for every assurance dimension before
dispatch. The validator rejects any observed tier below its corresponding
minimum. Ordinary consumers require at least `controller-observed` for every
fact they rely on. Self-reported dimensions are diagnostic and non-gating; a
route is usable only when those facts are not gate inputs. Canonical
publication requires a product-attested ChatGPT Codex child with
product-attested evidence for every assurance dimension.
The generic Rolecasting validator preserves every accepted assurance level and
does not silently promote one level to another.

The separate skill-mediated native Codex binding can freeze and record
same-leader operational dispatches on ChatGPT Codex and Codex CLI/TUI. Current
native tool schemas support controller-observed target, topology, and result
facts, but do not attest the effective model or enforceable per-spawn
authority; those dimensions remain self-reported. A consumer that relies on
either dimension must require stronger assurance and the binding rejects that
minimum before spawn. The binding emits no portable evidence and is not
registered as a producer or issuer. Native transport completion remains
separate from a same-leader verification observation and strict usability
Boolean; completion alone never implies a usable result. See
[native-codex-subagents.md](native-codex-subagents.md).

The bootstrap adapter exposes a pure deterministic renderer for one closed
`rolecasting-bootstrap-dispatch-request-v3`. It first validates the supplied
`rolecasting-model-transition-decision-v1` through verified transition and
route-evidence modules, then freezes supplied facts and binds its exact source
bytes. It does not launch a worker, choose a model, or authenticate facts
outside its process. No owning harness integration currently authenticates
those observations as portable evidence, and no production route-evidence
verifier or integration bytes are bound into a registered issuer closure.
Canonical new-publication evidence remains unreachable until such an
integration exists. Merely adding provider roles cannot satisfy that boundary.

## Closed bundle

One flat private bundle contains exactly:

- `manifest.json`, a `rolecasting-dispatch-evidence-v3` document;
- `plan.json`, a `rolecasting-dispatch-plan-v3` document; and
- one `transition-<execution-id>.json`, `model-<execution-id>.json`, and
  `result-<execution-id>.json` per dispatch.

Every stored document is a closed schema-version-1 canonical JSON object with
one trailing LF. Its `content_sha256` addresses the canonical object with that
field omitted. The manifest binds the raw plan and per-execution file digests.

The plan binds an opaque subject, an immutable pre-actuation plan identity, and
a nonempty ordered dispatch set. Each dispatch binds that plan identity, its
unique actuation ID and generic role; exact target product family,
surface, executor, and version; relationship, ownership, and transport;
subject, candidate, scope, and request; return, verification, and stop
contracts; authorized model-transition and model-selection receipts; read-only authority; conditional user
authority; enforceable distinct isolation; the complete observed assurance
map; and the exact per-dimension `assurance_minimum`.
Subdelegation and external action are denied. User authority is present exactly
for user-owned work. Externality comes only from the relationship and is never
inferred from the transport, surface, or isolation record.

Each transition document binds prior accepted state, exact task, event,
payload, plan, and actuation identities, current role classification, optional
sticky operator choice, authenticated fresh route and account evidence,
selected pair, authorization, and next state. The route preflight identifies
the exact status implementation, version, operations, and side-effect-safety
evidence. Codex app-server 0.149.0 fails closed even if a producer incorrectly
labels it side-effect-safe. The registered validator recomputes the pure
decision, authenticates the route-evidence issuer through Task Witness, and
rejects denial, stale or ineligible routes, unsafe or unverified status,
unavailable capacity, changed continuation target or account, a model below the
carried floor, a non-exact Daybreak Max selection, duplicate authorization,
cross-bound identity, or receipt tampering. This post-hoc check is defense in
depth; the actuator must validate and atomically consume the same authorization
before accepting payload data.

Each `rolecasting-model-selection-receipt-v3` binds the transition, target, selected model,
reasoning effort, and availability evidence from an issuer authorized for
`model`. It proves the recorded selection; its meaning is not upgraded to
effective product execution unless the dispatch assurance says
`product-attested` and the issuer is qualified for that claim.

Each `rolecasting-execution-result-receipt-v3` binds the raw plan, transition,
dispatch, and model; exact request; typed return, verification, and stop evidence; target,
topology, observed assurance, assurance minimum, user authority, isolation, and effective authority;
unchanged before and after candidate; and a strict Boolean usable status from
an issuer authorized for `execution-result`. `usable: false` is valid evidence
and preserves failed, timed-out, incomplete, or otherwise nonclean execution;
only non-Boolean values reject.

## Canonical projection

Successful validation emits the self-addressed
`rolecasting-dispatch-projection-v3`. It contains the evidence and raw manifest
and plan identities, immutable plan binding, opaque subject, producer, and an
execution map with exact transition authorization, authenticated route issuer,
event, task, dispatch, model, and result digests;
target, topology, observed assurance,
assurance minimum, authority, user authority, and isolation; model choice and issuer identities; typed
returned evidence; and exact `usable` status. Consumers use this projection and
do not parse or reproduce Rolecasting's stored schemas.
