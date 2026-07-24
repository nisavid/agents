# Invocation topology receipt

Rolecasting issues one portable dispatch plan. The harness adapter serializes
that plan as `adapter:rolecasting-invocation-topology-receipt`. This receipt is
separate from `adapter:model-selection-receipt`: model selection proves an
available model configuration, while this receipt alone binds who may be
dispatched, through which lifecycle, and with what authority. Neither receipt
may be embedded in or substituted for the other.

## Frozen plan

Bind one content-addressed plan identity to the immutable candidate identity,
review-input identity, requirements identity or explicit absence, selected
critic and specialist execution identities, and issue time. Record exactly one unique dispatch entry
for every selected execution. Duplicate, omitted,
unselected, or extra entries invalidate the whole plan; the entries form a
closed-world dispatch set.

Every dispatch entry records:

- selected critic or specialist execution identity and lifecycle;
- exact executor and harness, with a separately supplied model-selection
  receipt identity when selection is required;
- the same candidate, review-input, and requirements identities as the plan;
- bounded scope and read-only authority;
- return shape, verification, and stop conditions;
- distinct session and context isolation from every other selected execution;
- default-denied subdelegation and external-action authority; and
- for a user-owned task or thread, the exact explicit user authority to create
  or steer it.

Unstated authority is absent. A user-owned lifecycle without explicit user
authority, a foreign lifecycle without enforceable distinct isolation, or any
entry that permits subdelegation or external action is invalid before dispatch.

## Dispatch and change control

The adapter verifies the plan and relevant live executor capabilities before
each dispatch, then records the bound entry and result. Dispatch no identity
outside the closed set and use each entry at most once. A lifecycle, executor,
harness, scope, isolation, authority, or selected-execution change is not a
fallback under the existing receipt: freeze and issue a new valid plan. Preserve
raw failure evidence. Any selected execution that is absent, failed, timed out,
unusable, or unverified leaves the requested ensemble incomplete.
