---
name: maintaining-third-party-components
description: Use when updating, investigating an advisory for a third-party software component governed by a named existing policy, replacing, sealing, retiring, or completing its dependency PR. Includes routine Dependabot work and scheduled or foreign-harness wake-ups; do not use to invent a new trust boundary, silently revise policy, or perform generic Git/PR/review work.
---

# Maintain Third-Party Components

## Boundary

Own policy-bound lifecycle decisions for an already governed component. Require
the named existing policy and matching authority for the requested mode. An
advisory grants investigation authority only; a Dependabot PR is an untrusted
candidate, not update or merge authority.

Read and apply [the component policy contract](references/component-policy-contract.md),
[the component clearance contract](references/component-clearance-contract.md),
[the invocation envelope](references/invocation-envelope.json), and
[the scheduler adapter contract](references/scheduler-adapters.json).

## Intake And Binding

1. Accept manual repository/PR invocation or the same envelope from an
   authorized native or foreign harness. Require caller identity, coarse mode,
   exact requested lifecycle action, autonomy mode, repository, and the
   applicable target. `maintain` is only a route: its action is exactly one of
   `update`, `advisory`, `replace`, `seal`, `retire`, or `pull-request`; it is
   never an ambiguous alias. Transport adds no authority.
2. Acquire the common component/target lock. Bind the repository, base, head
   namespace and commit, PR author, changed paths, canonical candidate identity,
   canonical policy identity, and exact authority identity. Include the coarse
   mode, exact requested lifecycle action, caller identity, autonomy mode, and
   all three identities in the request, lock, receipt, and idempotence key.
   Reject an ineligible or ambiguous candidate before mutation.
3. Bind the named existing policy and the task or standing authority identity
   that matches `update`, `advisory`, `replace`, `seal`, `retire`, or
   `pull-request`. Missing or mismatched authority stops without policy or
   repository writes.

## Evaluate And Act

4. A governed lifecycle request or advisory enters maintenance and then calls assessment
   through `assessing-third-party-components` for the exact candidate. Only an
   ungoverned standalone read-only clearance enters assessment directly.
   Continue safe autonomous investigation for incomplete evidence and
   deterministic deviations; do not escalate merely because a result differs
   from baseline.
5. Apply the assessment disposition. Before every write, including retained-evidence, source, policy,
   forge-close, forge-reject, forge-publication, approval, or merge writes,
   re-resolve every bound identity: candidate identity, policy identity, and
   authority identity. If any identity, including PR head, author, paths,
   source, artifact, metadata, license, closure, or conformance input changed,
   invalidate dependent evidence and reassess. No write may precede this
   rebind; drift produces zero mutation from the stale clearance.
6. A hard no-go grants no forge authority. Close or reject a candidate only
   under separate matching authority for that exact forge action, then rebind
   the candidate, policy, and exact forge-action authority immediately before
   the authorized close or reject. Without both, make zero forge mutation.
   Retain evidence only after its own rebind. Escalate only undelegated
   trust/legal/runtime expansion, a fully investigated critical-fix deadlock, or
   irreducible ambiguity after every authorized path is exhausted.
7. Use `rolecasting:delegating-cross-agent-work` for a bounded implementer and keep changes within policy:
   - **update / PR:** amend the same eligible branch when allowed; update the
     exact pin or sealed artifact, metadata, conformance, and retained evidence;
   - **advisory:** remain read-only unless separate matching remediation
     authority exists;
   - **replace:** clear the replacement, verify consumers and compatibility,
     remove the old component, and retain both decisions;
   - **seal:** reproduce and bind the allowed exact artifact and license under
     policy without importing mutable cache or network authority; and
   - **retire:** prove consumer absence, remove runtime and update-discovery
     surfaces, and retain the final decision and advisory record.

8. Give the exact successor to `tricritical:loop` under the original bounded
   mutation authority. Then hand Git/ref/push mechanics to
   `versionkeeping:checkpointing-and-publishing-git-work`, PR publication to
   `mergecraft:publishing-reviewable-prs`, and authorized merge closeout to
   `mergecraft:getting-prs-merged`. When policy expressly delegates a routine accepted update, the
   flow may amend, review, approve, and merge autonomously through those owners;
   otherwise stop at the authorized boundary.
9. Re-read final repository, component, ref, PR, and receipt state before
   releasing the common lock. The same candidate converges or rejects; it does
   not create duplicate work.

## Scheduling

Manual invocation remains permanent. During onboarding, first inspect for any
existing or possibly existing dependency or component maintenance schedule or
analogous maintenance process. When one is found or suspected, offer
best-effort integration or alignment from available evidence. If suitability is
uncertain, also offer a context-sensitive standalone cadence. When none is
found, recommend a context-sensitive cadence. The operator always chooses
activation, cadence, and `autonomyMode`. Codex via ChatGPT, Claude Code via
Claude Desktop, and other harnesses such as Hermes or OpenClaw use the same
envelope. Artifact Customs never prefers a scheduler or treats a wake-up
mechanism as semantic authority.

Offer exactly three autonomy modes. **High-confidence is the recommended
mode, never a default or implicit consent**: the operator must select and record
`autonomyMode` in the invocation and deployment state before scheduled work:

- **report-only:** never upgrade autonomously; research/report/recommend;
- **high-confidence:** autonomously upgrade only after adequate verification,
  security review, and a high-confidence safe judgment; otherwise escalate with
  a comprehensive report; and
- **confidence-forward/deferred:** autonomously upgrade when confident, but if
  uncertain defer to a later research cycle for ecosystem evidence.

Treat compromised-artifact risk fail-closed. No autonomy mode expands mutation
authority beyond the named existing policy.

## Completion

Return `maintained`, `no-go`, `deeper autonomous investigation`, `operator
decision`, or `blocked`, with invocation/lock receipt, exact pre/post candidate
and PR identities, policy and authority, assessment and retained decision
evidence, implementation/conformance result, Tricritical result, external
Git/PR receipts, final reread, and remaining owner.
