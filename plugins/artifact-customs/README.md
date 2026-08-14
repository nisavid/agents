# Artifact Customs

Artifact Customs governs inbound third-party software components from first
clearance through retirement. It treats every component as an exact external
identity or byte sequence crossing a declared trust boundary.

The root `plugin.json` is the canonical Agent Plugins v1 package identity.
Codex reads its `com.openai` interface extension and the standard skills;
Claude Code uses the `.claude-plugin/plugin.json` identity projection and the
same skills. Each skill carries ordinary local projections of the shared
contracts it reads so standard clients do not depend on package-sibling access.
Release validation requires every projection to match its single authored
package-level source byte for byte.

The plugin has three public skills:

- `assessing-third-party-components` freezes and evaluates a candidate without
  mutation;
- `adopting-third-party-components` establishes or explicitly revises the
  policy for a new trust boundary; and
- `maintaining-third-party-components` updates, investigates, replaces, seals,
  or retires a component under an existing policy.

The scope includes packages and transitive dependencies, vendored source,
wheels and binaries, GitHub Actions pins, container-image digests, toolchain and
CLI pins, and agent-plugin pins. It excludes models and datasets, credentials,
SaaS-vendor governance, outbound builds and releases, general incident
response, deployment, generic Git and pull-request work, and generic code
review.

Artifact Customs does not duplicate those neighboring owners. It calls
`rolecasting:delegating-cross-agent-work` for bounded dispatch,
`tricritical:review`, `tricritical:adjudicate`, and `tricritical:loop` for
independent review through revision,
`versionkeeping:checkpointing-and-publishing-git-work` for Git and push
mechanics, and `mergecraft:publishing-reviewable-prs` plus
`mergecraft:getting-prs-merged` for pull-request publication and merge closeout.

Manual repository-and-PR invocation is permanent. Scheduled tasks in Codex via
ChatGPT, Claude Code via Claude Desktop, or another automation harness invoke
the same envelope and receive the same receipts. During onboarding, first
inspect for any existing or possibly existing dependency or component maintenance
schedule or analogous maintenance process. When one is found or suspected, offer
best-effort integration or alignment from available evidence; if its suitability
is uncertain, also offer a context-sensitive standalone cadence. When none is
found, recommend a context-sensitive cadence. The operator always chooses
activation, cadence, and `autonomyMode`; Artifact Customs remains
scheduler-neutral.

Every request, lock, receipt, and idempotence key binds the coarse mode, exact
requested lifecycle action, caller identity, `autonomyMode`, candidate identity,
policy identity, and authority identity. `maintain` must name exactly one action:
`update`, `advisory`, `replace`, `seal`, `retire`, or `pull-request`.

Offer exactly three autonomy modes. **High-confidence is the recommended
mode, not a default or implicit consent**: scheduled invocation and deployment state must
record the operator-selected `autonomyMode`.
**report-only** never upgrades
autonomously and can research/report/recommend; **high-confidence** upgrades
only after adequate verification, security review, and a high-confidence safe
judgment, otherwise escalating with a comprehensive report; and
**confidence-forward/deferred** upgrades when confident but defers uncertainty
to a later research cycle for ecosystem evidence. Treat compromised-artifact
risk fail-closed. No autonomy mode expands mutation authority beyond the named
existing policy.

Development evals, fixtures, tests, and generated content locks live in the
repository-level `evals/`, `tests/`, and `release/` trees rather than this
runtime plugin root.
