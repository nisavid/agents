# Hindsight Memory Control Plane Plan

This plan tracks the reusable control-plane implementation owned by
`nisavid/agents`. Machine inventory, secret locators, service manifests, rendered
harness bindings, and live migration approvals belong to consuming
configuration repositories.

## Safety contract

- Reusable code is configured through explicit arguments or environment values.
  It contains no user, machine, bank, profile, credential, or
  consumer-specific installation policy. Reusable policy contracts remain in
  this repository.
- Consumer rendering and installation never mutate Hindsight state or activate
  ambient memory.
- Every controller-directed mutation requires an immutable digest-bound plan,
  an unchanged live-state snapshot, idle affected operations, a verified
  rollback bundle, and explicit operator approval. This gate does not disable
  already-active ambient auto-retain behavior.
- Migration discovery is read-only and requires one server-backed opaque
  monotonic generation spanning the complete read. Before/after equality is an
  additional drift check, not the consistency primitive.
- If that generation is unavailable or changes, discovery fails closed, writes
  no result, and grants no mutation authority.

## Reusable source shape

- `bin/`: controller and lifecycle entry points.
- `lib/hindsight_memory_control_plane/`: desired-state, broker, policy, import,
  migration, and reconciliation modules.
- `lib/hindsight-embed-stack.zsh`: environment-driven fleet lifecycle library.
- `libexec/`: isolated migration and process helpers.
- `skills/`: import and onboarding instructions.
- `config/` and `examples/`: public benchmark schema and synthetic fixture.
- `tests/`: reusable Python, shell, controller, and disposable-stack contracts.
- `docs/PRD.md`: reusable product and safety contract.

## Completed reusable capabilities

- [x] Desired-state inventory validation, immutable plans, ledgers, rollback
  bundles, and strict canonical serialization.
- [x] HTTP and subprocess adapters with compatibility, provenance, authentication,
  size, timeout, and payload-redaction boundaries.
- [x] Unix-socket broker sessions with scoped capabilities, replay protection,
  method and route bounds, retain watermarks, and bounded diagnostics.
- [x] Inactive harness rendering and reversible owned-field activation planning.
- [x] Engineering, personal, and airlock policy; provider compatibility;
  deterministic benchmark evaluation; authenticated loopback control service.
- [x] Deterministic import projections and one-question-at-a-time onboarding.
- [x] Read-only migration inventory and immutable unapproved shadow planning.
- [x] Environment-driven broker, supervisor, profile fleet, sidecar, and service
  lifecycle support.
- [x] Explicit-policy legacy single-bank cleanup and migration helpers.

## Validation and closeout

- [ ] Run the Python suite with Python 3.11 or newer and attach an immutable
  commit/check-run evidence link before checking this item:

  `python3 -m unittest discover -s tooling/hindsight/tests -p 'test_hindsight_*.py' -v`

  Run the standalone publication-disclosure validator with its private catalog,
  public PRD, repository root, and immutable publication base. The private
  catalog must remain outside the publication tree, and both inputs must be
  nonempty before invoking the validator:

  `: "${HINDSIGHT_PRIVATE_CATALOG:?set the private catalog path outside the publication tree}"`

  `: "${PUBLICATION_BASE_SHA:?set the immutable publication base SHA}"`

  `python3 tooling/hindsight/tests/hindsight_memory_control_plane_prd_validation.py "$HINDSIGHT_PRIVATE_CATALOG" tooling/hindsight/docs/PRD.md . "$PUBLICATION_BASE_SHA"`

- [ ] Run reusable shell contracts and attach an immutable commit/check-run
  evidence link before checking this item:

  `zsh tooling/hindsight/tests/hindsight-memory-controller.zsh`

  `zsh tooling/hindsight/tests/hindsight-embed-stack.zsh`

  `zsh tooling/hindsight/tests/hindsight-memory-cleanup.zsh`

  `zsh tooling/hindsight/tests/hindsight-embed-service-intent.zsh`

- [ ] Run the disposable Hindsight/PostgreSQL contract smoke test and attach an
  immutable commit/check-run evidence link before checking this item:

  `zsh tooling/hindsight/tests/hindsight-memory-disposable-smoke.zsh`

- [ ] Run syntax, diff, security, and broad code review gates until clean.
- [ ] Create and publish a cohesive Graphite checkpoint, close review and CI,
  and rebase-merge it.
- [ ] Validate the consuming dotfiles binding in a temporary destination without
  applying it to the live home or starting services.

## Live migration gate

- [ ] Execute read-only live discovery only after the selected adapter exposes a
  server-backed monotonic generation for the full discovery window and the exact
  read-only gate can be satisfied.

This item remains intentionally incomplete. Do not run controller-directed live
`apply`, retain, consolidate, refresh, import, config patch, template import,
curation reapply, or delete operations. Do not create either completion-gate
half. Track the missing
server contract as reusable follow-up work in `nisavid/agents`.
