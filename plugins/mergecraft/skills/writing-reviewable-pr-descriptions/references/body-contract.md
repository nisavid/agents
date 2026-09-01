# Reviewer Body Contract

Read this reference while drafting the prose and review path around the required
first-viewport change navigation.

## Reviewer Decision Path

Write for an unfamiliar, skeptical, time-constrained peer deciding whether the
reviewed commit deserves approval. Lead with the resulting behavior and why it
matters. Explain the concrete problem, current scope, unchanged boundaries,
efficient review entry point, material contracts or risks, observed evidence,
and remaining work where each concern naturally belongs.
For a fix, say what used to go wrong: the realistic scenario, how bad it could
get, and whether the failure was hypothetical, accidental, or exploitable,
naming threat surface and blast radius when security is implicated, with
severity stated honestly, neither inflated nor waved off.

Do not turn those concerns into a fixed section inventory. A tiny change may
answer them in one sentence. A large change may need an ordered review path,
explicit boundaries, or a temporal comparison. Use direct peer-engineer
language and falsifiable claims; omit salesmanship, reassurance, and author
effort.

Write the title as the smallest repository-conforming line that names the
primary reviewer-visible behavior or outcome. Preserve a required stack index
or still-current scope prefix. Do not promote urgency, implementation inventory,
or impact beyond the reviewed commit.

For a large or stacked change, order review by authored responsibility. Put a
generated surface after its contract or generator input, and name the evidence
that makes de-emphasis safe. Explain the prerequisite or Before state, this
PR's exact transition, and later stack members. A current-path description plus
future work is incomplete without its prerequisite.

Treat the explanation as a gate on readiness and approval claims. State an
unsupported abstraction, excessive scope or permissions, unresolved authority,
or missing material evidence as a blocker. For a prose-only request, return a
truthful draft and the controlling open decision; do not market around the gap
or mutate source without authority.

## Proportional Shape

- **Tiny:** navigation, one short paragraph or 1-3 bullets, and verification.
- **Straightforward:** `Summary`, `Changes`, and `Verification`; add blockers or
  follow-up only when real.
- **Large, stacked, cross-cutting, or readiness-ambiguous:** add only the
  reviewer aids justified by the change: review path, contracts, dependencies,
  risks, rollout, blockers, or follow-up.

Prefer bullets and short sections. Group changes by interface or responsibility
boundary, not package inventory or commit order. Use concrete headings such as
`API Contracts` or `Worker Lifecycle`.

## Scope And Classification

Use the exact pushed PR base/head. Refresh remote refs before local merge-base
work. Establish the intended base explicitly when no PR exists.

Classify changed lines in this order:

1. **IMPL:** non-test source/configuration affecting runtime, build, deployment,
   migration, tooling, or CI.
2. **TEST:** tests, fixtures, helpers, test-only setup/configuration, and
   test-only dependency changes.
3. **DOC:** documentation and prose-only examples.
4. **GEN:** lockfiles and generated artifacts/data.
5. **OTHER:** assets, manifests, or metadata not covered above.

Inspect mixed files. Split lines only when the patch makes the split auditable;
otherwise use `OTHER`. Pure moves/copies are operations, not changed lines.
Edited moves/copies count only modified lines. Binary files count as operations.

## Links And Evidence

- Link every actionable reviewer reference: changed files, PRs, issues, unusual
  CI, docs, media, dashboards, and specs.
- Changed files should open the PR's Files changed anchor. Supporting unchanged
  files may use immutable blob/tree links.
- Summarize routine green CI. Link jobs only when they explain a failure,
  pending gate, flake, or unusual validation.
- Write verification as command plus observed result; include a working
  directory when it was not run at repository root.
- Separate PR-readiness blockers from follow-up that belongs outside this PR.

## Manual Operational Testing

Include a manual plan only when interaction or operational behavior adds useful
confidence beyond automated checks.

1. State the affected surface, prerequisites, safe execution context, and
   non-root working directory.
2. Put the shortest coherent scenario that establishes the defining behavior or
   risk first. Include runnable commands, intended results, observed results,
   and the non-obvious quality a human should judge. When the claim depends on a
   comparison, transaction, concurrency, or recovery sequence, exercise that
   complete relationship rather than one successful step. Keep cleanup for
   state and live processes created by the core in the core path; if the source
   does not establish cleanup, end with literal `Cleanup gap:` without inventing
   a readiness gate.
3. Separate every non-core scenario. Begin it with literal `Optional` unless
   verified source makes it a readiness gate; then begin it with `Required` and
   name the gate. Mark slow, destructive, credentialed, shared-environment, and
   host-mutating work. State the blast radius. Every executed stateful scenario
   ends with `Cleanup:` or `Cleanup gap:`; optionality never makes cleanup
   optional.
   When scenarios share steps, write the shared material once and state what the
   variant changes; an unexplained verbatim repeat reads as a mistake or a
   second run.
4. Distinguish evidence observed at the reviewed commit from unrun or proposed
   work. Do not make reviewers repeat author-run evidence unless independent
   reproduction is a real gate. Run every safe, authorized command relied on by
   the body; label other commands unrun and say why.

## Source Shape

GitHub Markdown treats ordinary source newlines within prose as soft breaks, so
they must not encode an intended visible break. Put each paragraph, list item,
table row, and blockquote line on one source line, however long, and express
every intended intra-block break as `<br>`.

Fenced code, raw HTML blocks, and Mermaid retain their required line structure.
A blank line separates block elements. Repository docs, fixtures, and diffs may
wrap; their shape is not a model for the rendered PR body.

## Preservation

The live body is not disposable source. Carry forward still-current custom or
user-authored sections unless removal is explicit or current facts make them
stale: links, images, recordings, demo cards, captions, demonstrably non-secret
access instructions, issue references, caveats, review instructions, and
rollout notes.

For an existing body, isolate the strict leading Stack/Diff prefix at its
mandatory empty-line boundary and treat the remaining suffix as opaque bytes.
Baseline fragments must exhaustively partition the stored body. Their ordered
retain, replace, and remove dispositions must derive the candidate exactly;
retain the suffix byte-for-byte, including line endings and final-newline state,
unless an authorized disposition explicitly changes it. Navigation validation
does not parse or normalize suffix Markdown.

A suspected credential in the live baseline or candidate is a hard security
gate. Never quote, echo, preserve, or republish it. Stop with a generic
non-echoing error and require authorized removal and, when exposure is
plausible, rotation before publication continues.

Before returning the candidate, compare baseline and proposal for unintended
deletion. Publication, stored-state reread, rendering inspection, and any
follow-up mutation belong to the publisher or its authorized lifecycle caller.

## Temporal And Visual Explanations

Use prose for one simple relationship. Use a compact table or explicit
Before / This PR / Later explanation when temporal state, ownership, or stack
position causes the confusion. Use a visual only when relationships are
materially harder to understand in prose and the visual shortens the review
path.

Prefer the smallest focused interface, lifecycle, flow, or state diagram that
answers one review question. Preserve ownership boundaries, branch labels, and
distinctions among current scope, successful results, failures, and later work.
Pair semantic color with text or structure. Verify every node and edge against
the current contract; never infer a connection because two components changed.
Split incompatible perspectives rather than producing one dense canvas. Use the
atlas route in `SKILL.md` only after static views fail this test.

## Hard Rules And Acceptance

- Write every authorized title/body field from the exact pushed diff, not
  filenames alone. Preserve an unauthorized existing field byte-for-byte.
- Do not publish machine-local paths, scratch artifacts, template instructions,
  placeholders, invented stack facts, or claims about unpushed changes.
- Do not infer issue closure from a PR number, branch name, or nearby identifier.
  Preserve only issue links and closure semantics supplied by verified source or
  the live body.
- Reject suspected credentials from both the preservation preimage and the
  candidate without reproducing their bytes in output.
- State observed verification and unresolved work precisely.
- The body must be proportional, scannable, preservation-safe, and faithful to
  the stored pushed state.
- Existing-body fragments must exhaustively partition the live preimage and
  derive the candidate in exact order; an opaque retained suffix is unchanged
  byte-for-byte.
- Links must be useful; required disclosures must validate; stacked navigation
  must be complete and mark one current PR.
- Manual evidence must identify the core confidence path, intended and observed
  results, literal non-core classifications, and cleanup for executed state.
- Every prose block occupies one source line, with explicit `<br>` for an
  intended intra-block break.
- Return complete validated title/body bytes, the authorized text surface, and
  their bound review-input manifest without forge mutation.
