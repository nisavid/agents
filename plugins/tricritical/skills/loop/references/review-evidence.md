# Terminal review evidence

Task Witness validates `tricritical-terminal-review-evidence-v2` with the
registered `tricritical-terminal-review-evidence-validator-v2`. The bundle is
one canonical `manifest.json` and returns
`tricritical-terminal-review-projection-v2`. Each cycle binds a frozen subject, a registered
`rolecasting-dispatch-projection-v3`, including its authenticated route issuer
and task, plan, actuation, and transition bindings, raw critic reports, a disposition-free
ensemble, post-freeze feedback, independent adjudication, optional revision,
budget state, and one closed owner. Non-occurring phases are explicit `null`.

The v2 terminal projection preserves the complete registered Rolecasting
projection verbatim as `final_dispatch`. The generic owner accepts all
registered Rolecasting assurance tiers and does not upgrade or collapse
`product-attested`, `controller-observed`, or `self-reported` assurance. A
Tricritical terminal state describes review completeness and findings; it does
not claim that the dispatch evidence is strong enough for a downstream gate.
Each consumer must apply its own minimum before relying on that state.
Self-reported evidence is diagnostic and cannot by itself satisfy a hard gate.

The subject has exact `candidate`, `review_input`, and `requirements`
identities. The ensemble owns the selected critic axes and specialists. Missing,
failed, timed-out, and unusable selected executions remain evidence and derive
`incomplete / non-clean`; they are never discarded to obtain clean evidence.
Adjudication uses exactly one of the eight public dispositions and the fixed
next owner for every finding. A revision requires the original retained
mutation authority, covers every accepted finding, produces a distinct
successor, and invalidates prior review evidence. The successor must receive a
fresh review with no reused execution id, session, or context.

The validator derives one of `clean`, `clean / degraded`,
`incomplete / non-clean`, `blocked`, `failed_verification`, or
`needs operator decision`, including its exact owner, limitations, missing
executions, unresolved actionable count, and conditional verification. Bare
`clean` requires all three selected axes (`intent`, `runtime`, and `structure`),
every selected specialist, independent complete execution, no limitation or
unresolved actionable finding, successful declared verification of unchanged
candidate bytes, and owner `none`. Model and reasoning-effort policy belongs to
Rolecasting and the consuming workflow, not this generic owner validator.

Default revised-successor tranches are 2, 3, and 5 for low, ordinary, and high
risk. Usage increases once per distinct revised successor and is monotonic. An
exhausted tranche can continue only through one fresh synchronous, no-timeout
operator choice and fresh eligibility evidence for a same-sized extension.

This package currently registers only the validator. Its producer and issuer inventories are empty because no installed integration authenticates native
Rolecasting execution and Tricritical production end to end. Existing retained
or fixture/bootstrap bundles may be validated, but the package does not expose a
new-publication producer chain and does not claim canonical end-to-end reachability.
