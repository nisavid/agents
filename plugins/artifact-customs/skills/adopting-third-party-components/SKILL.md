---
name: adopting-third-party-components
description: Use when explicitly authorized to add an exact third-party software component or materially revise its trust policy, including a first package, vendored artifact, Action, image, toolchain, CLI, or agent-plugin pin. Establishes the durable policy and first admitted identity; do not use for read-only comparison, ordinary updates under an existing policy, or generic Git/PR work.
---

# Adopt Third-Party Components

## Boundary

Own the decision and coordination for a named new or materially revised trust
boundary. Do not mutate until the task supplies explicit authority for that
boundary. A request to assess, compare, or recommend is read-only and belongs
to `assessing-third-party-components`.

Read and apply [the component policy contract](../../references/component-policy-contract.md)
and [the component clearance contract](../../references/component-clearance-contract.md).

## Workflow

1. Bind the exact `adopt` lifecycle action, caller identity, autonomy mode,
   requested component, consumers, repository, owned paths, canonical policy identity,
   and exact authority identity for the new or revised policy
   boundary. Put every one of those values in the request, lock, receipt, and
   idempotence key. If trust, legal terms, publisher,
   runtime download, network, privilege, persistence, or another capability
   would expand beyond that authority, stop before writes with `operator
decision`.
2. Establish why a third-party component is warranted. Compare viable reuse,
   implementation, and replacement options in proportion to consequence;
   define rollback and exit before making the dependency durable.
3. Draft the narrow component policy described by the shared contract. A draft
   policy supplies evaluation criteria but no mutation authority.
4. Invoke `assessing-third-party-components` on the exact candidate under that
   proposed policy. Consume only a receipt whose candidate identity, policy
   identity, and authority identity still match. A no-go remains a no-go; do
   not broaden policy merely to admit the candidate.
5. Immediately before every write, re-resolve the candidate identity, policy
   identity, and authority identity. This applies to policy, source,
   retained-evidence, and forge writes. Drift invalidates the assessment and returns to
   step 4 with zero mutation from the stale binding.
6. Use `rolecasting:delegating-cross-agent-work` for a bounded implementer to add only the authorized policy,
   exact pin or sealed artifact, required consumer changes, conformance checks,
   and retained decision evidence. This coordinator does not absorb generic
   edit, Git, or forge semantics.
7. Give the frozen successor to `tricritical:loop` with the
   original bounded mutation authority. A non-clean or authority-gated result
   stops publication.
8. Hand verified worktree, commit, ref, and push operations to
   `versionkeeping:checkpointing-and-publishing-git-work`; hand canonical PR
   publication to `mergecraft:publishing-reviewable-prs` and an authorized
   merge closeout to `mergecraft:getting-prs-merged`. Preserve their receipts
   and identity gates.

## Completion

Return `adopted`, `no-go`, `operator decision`, or `blocked`, with the exact
component and policy identities, clearance receipt, authority boundary,
implementation and conformance evidence, retained rollback/exit decision,
Tricritical result, and Versionkeeping/Mergecraft handoff receipts when used.

Do not claim `adopted` until the authorized durable policy and exact component
identity are present in the intended repository state and all required gates
are verified. No-go and operator-decision paths perform no component or policy
mutation.
