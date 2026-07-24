# Invocation boundary

Treat `topology.json` as the sole graph authority. Invoke or delegate public skills only through the current skill's declared outgoing edges and the frozen contract. Undeclared delegation or invocation is forbidden. Dynamic risk specialists are isolated execution roles routed through `review`'s declared critic edges; they do not add public skills or reverse edges.

Topology `requires` entries are adapter-supplied capability inputs, not public
skill calls. `adapter:model-selection-receipt` proves the harness can bind an
execution role to an available model configuration under current authority. The
portable skill may validate and consume that receipt, but must not copy
provider-specific model policy, invent support, or treat the receipt as an
outgoing invocation edge.

`adapter:rolecasting-invocation-topology-receipt` is a second, orthogonal
adapter input issued from Rolecasting's frozen delegation plan. It is never
folded into `adapter:model-selection-receipt` and is not an outgoing skill call.
It binds the candidate, review-input, and requirements identities plus a
closed-world dispatch set containing exactly one unique dispatch entry per
selected critic or specialist. Every entry fixes lifecycle, executor and
harness, the same immutable inputs, scope, return and verification contract,
stop conditions, distinct isolation, read-only authority, default-denied
subdelegation and external action, and explicit user authority for a user-owned
task.

Reject the entire plan before dispatch when any selected execution is missing,
duplicated, extra, stale, insufficiently isolated, over-authorized, or bound to
different input. Do not silently fall back to another executor or lifecycle;
that change requires a new valid plan and new adapter receipt. A selected
execution that does not return a usable, verified result keeps the review
`incomplete / non-clean`.
