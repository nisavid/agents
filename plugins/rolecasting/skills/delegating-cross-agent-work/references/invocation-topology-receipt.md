# Invocation topology receipt

Rolecasting issues one portable dispatch plan. The harness adapter serializes
that plan as `adapter:rolecasting-invocation-topology-receipt`. This receipt is
separate from `adapter:model-selection-receipt`: model selection proves an
available model configuration, while this receipt alone binds who may be
dispatched, through which topology and transport, with what authority, and at
what assurance. Neither receipt may be embedded in or substituted for the
other.

## Independent dimensions

Every selected execution freezes these independent dimensions:

- **target:** exact product family, named surface, observed version, and
  concrete executor;
- **relationship:** child, peer, or external;
- **ownership:** leader-owned or user-owned;
- **transport:** native-tool, task-api, cli, app-server, or remote-api;
- **assurance:** the consumer assurance minimum and observed product-attested,
  controller-observed, or self-reported evidence.

An app-server transport does not make an execution external. A leader-owned
peer remains leader-controlled despite its independent session. A user-owned
peer requires explicit user authority to create or steer it. Model choice is a
separate receipt and does not change any topology dimension.

## Frozen plan

Bind one content-addressed plan identity to the immutable candidate identity,
review-input identity, requirements identity or explicit absence, selected
critic and specialist execution identities, and issue time. Record exactly one unique dispatch entry
for every selected execution. Duplicate, omitted,
unselected, or extra entries invalidate the whole plan; the entries form a
closed-world dispatch set.

Every dispatch entry records:

- selected critic or specialist execution identity;
- the exact product family, named surface, observed version, and concrete
  executor;
- the child, peer, or external relationship;
- leader-owned or user-owned ownership;
- native-tool, task-api, cli, app-server, or remote-api transport;
- product-attested, controller-observed, or self-reported assurance and the
  consumer assurance minimum;
- a separately supplied model-selection
  receipt identity when selection is required;
- the same candidate, review-input, and requirements identities as the plan;
- bounded scope and read-only authority;
- return shape, verification, and stop conditions;
- distinct session and context isolation from every other selected execution;
- default-denied subdelegation and external-action authority; and
- for a user-owned task or thread, the exact explicit user authority to create
  or steer it.

Unstated authority is absent. User-owned ownership without explicit user
authority, any execution without enforceable distinct isolation, an
assurance level below the consumer minimum, or any entry that permits
subdelegation or external action is invalid before dispatch.

Portable policy may describe a surface before Rolecasting can issue authentic
evidence for it. Issuance requires a real adapter qualification for the exact
family, surface, version, executor, transport, and assurance source. Current
provider registration is validator-only; it does not qualify or attest any
surface.

## Dispatch and change control

The adapter verifies the plan and relevant live executor capabilities before
each dispatch, then records the bound entry and result. Dispatch no identity
outside the closed set and use each entry at most once. A target, relationship,
ownership, transport, scope, isolation, authority, assurance minimum,
or selected-execution change is not a fallback under the existing receipt:
freeze and issue a new valid plan. Preserve raw failure evidence. Any selected
execution that is absent, failed, timed out, unusable, or unverified leaves the
requested ensemble incomplete.
