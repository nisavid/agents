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
- [x] Immutable portable install, upgrade, verification, rollback, and
  data-preserving uninstall for macOS LaunchAgents and Linux systemd-user.
- [x] Explicit-policy legacy single-bank cleanup and migration helpers.

## Validation and closeout

- [ ] Run the Python suite with Python 3.11 or newer and attach an immutable
  commit/check-run evidence link before checking this item:

  `uv run --python '>=3.11' python -m unittest discover -s tooling/hindsight/tests -p 'test_hindsight_*.py' -v`

  Run the standalone publication-disclosure validator with its private catalog,
  public PRD, repository root, and immutable publication base. The private
  catalog must remain outside the publication tree, and both inputs must be
  nonempty before invoking the validator:

  `: "${HINDSIGHT_PRIVATE_CATALOG:?set the private catalog path outside the publication tree}"`

  `: "${PUBLICATION_BASE_SHA:?set the immutable publication base SHA}"`

  `uv run --python '>=3.11' python tooling/hindsight/tests/hindsight_memory_control_plane_prd_validation.py "$HINDSIGHT_PRIVATE_CATALOG" tooling/hindsight/docs/PRD.md . "$PUBLICATION_BASE_SHA"`

- [ ] Run reusable shell contracts and attach an immutable commit/check-run
  evidence link before checking this item:

  `zsh tooling/hindsight/tests/hindsight-memory-controller.zsh`

  `zsh tooling/hindsight/tests/hindsight-embed-stack.zsh`

  `zsh tooling/hindsight/tests/hindsight-embed-stack-linux.zsh`

  Native macOS LaunchAgent acceptance:

  `managed_python="$(uv python find --managed-python --resolve-links --no-python-downloads '>=3.11')" && env HINDSIGHT_PORTABLE_PLATFORM_ACCEPTANCE=1 HINDSIGHT_PORTABLE_ACCEPTANCE_PLATFORM=launchd HINDSIGHT_PORTABLE_ACCEPTANCE_MANAGED_PYTHON="$managed_python" "$managed_python" -m unittest tooling.hindsight.tests.test_hindsight_memory_portable_platform_acceptance -v`

  - [ ] Native CachyOS systemd-user acceptance remains pending evidence from a
    real non-root user manager and session bus:

    `managed_python="$(uv python find --managed-python --resolve-links --no-python-downloads '>=3.11')" && env HINDSIGHT_PORTABLE_PLATFORM_ACCEPTANCE=1 HINDSIGHT_PORTABLE_ACCEPTANCE_PLATFORM=systemd-user HINDSIGHT_PORTABLE_ACCEPTANCE_MANAGED_PYTHON="$managed_python" "$managed_python" -m unittest tooling.hindsight.tests.test_hindsight_memory_portable_platform_acceptance -v`

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
half. Track the missing server contract in
[`nisavid/agents` issue #11](https://github.com/nisavid/agents/issues/11). See
[Migration readiness](migration-readiness.md) for the
current operator boundary and remaining executable work.
