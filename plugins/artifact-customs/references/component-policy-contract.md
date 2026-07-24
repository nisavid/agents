# Component Policy Contract

A component policy is the durable, named authority boundary for one component
or narrowly defined component class. Policy content must be reviewable and
machine-readable where the repository supports it. A receipt, successful test,
Dependabot configuration, or prior merge is not a substitute for policy.

## Required Policy Surface

Record, as applicable:

- governed component identities and consumer scope;
- permitted registries, canonical sources, publishers, artifact forms, and
  platforms;
- required immutable identities, hashes, attestations, and the exact rule for
  any permitted attestation absence;
- allowed licenses and dependency-closure constraints;
- malware, vulnerability, metadata, archive, compatibility, and conformance
  gates;
- authorized update modes, repositories, paths, PR authors, head namespaces,
  base branches, identities, and each exact lifecycle action;
- whether routine accepted updates may be amended, reviewed, approved, and
  merged without another operator decision;
- hard no-go conditions and exceptional escalation conditions;
- evidence retention, rollback, replacement, and retirement requirements; and
- runtime, network, privilege, persistence, and update capabilities that must
  not expand silently.

Reject a policy that delegates an undefined class of trust, legal, runtime, or
mutation authority. Narrow predicates are deliberate; deterministic mismatch
starts investigation rather than silently widening them.

## Authority Modes

- **Assessment** is read-only and needs no mutation authority.
- **Adoption** requires explicit authority for the named new or revised trust
  boundary before any repository or policy mutation.
- **Maintenance** requires both a named existing policy and matching authority
  for the requested update, advisory, replacement, sealing, retirement, or PR
  operation. The coarse `maintain` route never aliases an action: record exactly
  one of `update`, `advisory`, `replace`, `seal`, `retire`, or `pull-request`.
  Do not infer one mode from another.

An advisory-only request stays read-only. A request to update one component
does not authorize changing its policy, replacing it, or editing unrelated
dependencies. A policy may authorize routine merge completion, but it cannot
grant authority outside the operator and repository boundaries that adopted it.
A hard no-go disposition grants no forge authority. Closing or rejecting a
candidate requires separate matching authority for that exact forge action,
followed by an immediate candidate, policy, and exact forge-action authority
rebind before the authorized action. Without both, make zero forge mutation.

## Composition Boundary

Artifact Customs owns candidate binding, component-policy evaluation, evidence
disposition, and the lifecycle decision. It delegates:

- bounded worker topology to `rolecasting:delegating-cross-agent-work`;
- independent review, adjudication, and the sole review-revise loop to
  `tricritical:review`, `tricritical:adjudicate`, and `tricritical:loop`;
- worktree, commit, ref, and push mechanics to
  `versionkeeping:checkpointing-and-publishing-git-work`; and
- pull-request publication and merge closeout to
  `mergecraft:publishing-reviewable-prs` and `mergecraft:getting-prs-merged`.

Those are outward calls only. Do not make the neighboring plugins call Artifact
Customs implicitly, and do not duplicate their Git, review, or forge semantics
inside this plugin.

## Invocation And Scheduling

Manual repository and PR-number invocation remains available. Native Codex via
ChatGPT and Claude Code via Claude Desktop scheduled-task adapters, and foreign
harnesses such as Hermes or OpenClaw, must all use the same locked, idempotent
invocation envelope and receipt contract.

Transport creates no authority. During onboarding, offer the current harness's
native scheduler only after first inspecting for any existing or possibly
existing dependency or component maintenance schedule or analogous maintenance
process. When one is found or suspected, offer best-effort integration or
alignment from available evidence. If suitability is uncertain, also offer a
context-sensitive standalone cadence; when none is found, recommend a
context-sensitive cadence. The operator always chooses activation, cadence,
and `autonomyMode`. Never declare a preferred scheduler or activate more than
the user selected.
Multiple callers may be safe through the common lock, but duplicate work is not
presumed desirable. Each request, lock, receipt, and idempotence key binds the
coarse mode, exact requested lifecycle action, caller identity, `autonomyMode`,
canonical candidate identity, policy identity, and authority identity; re-resolve
the last three before every source, policy, retained-evidence, or forge write.

Offer exactly three autonomy modes, with **high-confidence** as the recommended
mode but never a default or implicit consent. Scheduled invocation and deployment state
must record an operator-selected `autonomyMode`:
**report-only** never upgrades autonomously and can research/report/recommend;
**high-confidence** upgrades only after
adequate verification, security review, and a high-confidence safe judgment,
otherwise escalating with a comprehensive report; and
**confidence-forward/deferred** upgrades when confident but defers uncertainty
to a later research cycle for ecosystem evidence. Treat compromised-artifact
risk fail-closed. No autonomy mode expands mutation authority beyond the named
existing policy.
