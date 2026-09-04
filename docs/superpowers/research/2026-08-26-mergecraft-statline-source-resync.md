# Mergecraft Statline Source Resync Evidence

> Historical record. The current document lives in
> [`nisavid/provingkit`](https://github.com/nisavid/provingkit/blob/main/docs/superpowers/research/2026-08-26-mergecraft-statline-source-resync.md).
> This copy preserves evidence tied to the former repository layout; it is not
> an active package, validation, or release route.

## Purpose

This note records the source disposition for the Mergecraft
`writing-reviewable-pr-descriptions` and `publishing-reviewable-prs` skills. It
compares the canonical plugin source with the pinned dotfiles source and the
installed projections. It is evidence for canonical source reconciliation, not
an installation or release record.

The comparison is tied to immutable Git objects. Later working-tree changes do
not change this baseline.

## Evidence Baseline

| Source | Revision | Writer tree | Publisher tree | Authority |
| --- | --- | --- | --- | --- |
| Canonical `nisavid/agents` plugin source | `3e8787ef8cb70db659125133c03027c703ed8233` | `a1ffcef70fd4abeaeaf8b50824e5092a54fec395` | `6022c1d35ec3200888ebccac05a8a8d3dd768bc4` | Canonical development source |
| [`nisavid/dotfiles`](https://github.com/nisavid/dotfiles.git) | `cd2071e4dd885d293325a3af063421db9579a7df` | `1e9452937e85aa4f77557ee6407db7fca36d7321` | `cba16890a5c93103360802ee83b8e6edcf6024f1` | Pinned source input to classify, not a replacement canonical source |
| Installed `$HOME/.agents/skills` projections | No authenticated source revision | No authenticated tree | No authenticated tree | Evidence only; inspected without editing |

The canonical paths are
`plugins/mergecraft/skills/writing-reviewable-pr-descriptions/` and
`plugins/mergecraft/skills/publishing-reviewable-prs/`. The pinned dotfiles
paths are
`home/dot_agents/skills/writing-reviewable-pr-descriptions/` and
`home/dot_agents/skills/publishing-reviewable-prs/`.

The installed projections are ordinary directories, not links back to either
Git source. They are unauthenticated runtime evidence and must not drive source
ownership. The installed writer equals the dotfiles writer plus the stale
`large-pr-description-example.md`. The installed publisher contains two stale
or projection-specific files: `scripts/reviewable_pr.py` and a renamed
`scripts/create_reviewable_pr.py`.

The raw dotfiles publisher contains
`scripts/literal_create_reviewable_pr.py`, while its `SKILL.md` invokes
`scripts/create_reviewable_pr.py`. The installed `create_reviewable_pr.py` is
byte-identical to the raw dotfiles literal creator, both with SHA-256
`83ae25c2cb49c60f1b79ebffb12d1a877be4eee8994d09ba9b0d13092580025d`.
The installed projection therefore masks a broken raw-source filename contract.

## Byte-Level Inventory

| Tree | Files | Lines | Distinguishing content |
| --- | ---: | ---: | --- |
| Canonical writer | 28 | 3,781 | Agent metadata, atlas extension schema, centralized categories, Git observer, HTML and URL validation, review-input manifest, and sensitive-content checks |
| Dotfiles writer | 34 | 5,307 | Behavioral and trigger evals, six fixtures, `diff_inventory.py`, and five local test files |
| Installed writer | 35 | 5,387 | Dotfiles writer bytes plus one stale example |
| Canonical publisher | 9 | 4,855 | Agent metadata, canonical creator, audit, receipts, publication support, and required-review support |
| Dotfiles publisher | 14 | 1,970 | Behavioral and trigger evals, six fixtures, literal creator, and two local test files |
| Installed publisher | 15 | 2,344 | Dotfiles content after creator rename plus stale `reviewable_pr.py` |

Only `scripts/change_navigation/__init__.py` and
`scripts/change_navigation/badge_wrappers.py` are byte-identical among common
canonical and dotfiles writer files. Eighteen other common writer files differ.
No common canonical and dotfiles publisher file is byte-identical.

## Disposition Vocabulary

- **Adopted** means the dotfiles behavior remains part of the canonical contract,
  whether the baseline already implements it or reconciliation must carry it
  forward.
- **Corrected** means the intent remains, but the dotfiles or canonical baseline
  mechanism is inaccurate, incomplete, or assigned to the wrong owner.
- **Superseded** means a stronger canonical mechanism replaces the dotfiles
  mechanism without discarding its user-facing outcome.
- **Intentionally excluded** means the behavior or artifact does not belong in
  the canonical source contract.

## Adopted Behaviors

| Behavior | Evidence | Canonical disposition |
| --- | --- | --- |
| Use the writer alone for chat-only title/body drafting; use writer and publisher for live text mutation; use neither for read-only inspection, comments, checks, threads, or merge-only work with unchanged text and state. | Dotfiles writer `SKILL.md:17-22`; publisher `SKILL.md:15-20`; both trigger eval suites | Preserve the routing. Canonical writer `SKILL.md:4-7,16-18,60-63` and publisher `SKILL.md:3,8-16,23-24` own the split. Preserve the trigger cases as canonical behavioral evidence. |
| Write the smallest reviewer path that explains timing, entry point, change, verification, remaining work, and approval-relevant value. | Dotfiles writer `SKILL.md:11`; `references/body-contract.md:7-29` | Retain the reviewer path and title guidance. Canonical writer `SKILL.md:14,31-43` already owns the body and complete title/body pair. |
| Distinguish core, optional, and required manual operational testing; state cleanup and evidence; never invent issue-closing language. | Dotfiles writer `references/body-contract.md:47-76,86-96` | Carry forward the externally oriented test and issue-closure rules where the canonical baseline is silent. |
| Write each paragraph, list item, table row, and blockquote as one GitHub comment source line; use `<br>` for an intentional visible break. | Dotfiles writer `SKILL.md:15`; `references/body-contract.md:66-70` | Carry forward as PR-body source-shape guidance. It is a GitHub rendering contract, not a repository line-length rule. |
| Put collapsed Stack then Diff, or Diff alone, in the first viewport; preserve exact stack order, direct base, additional dependencies, titles, URLs, operations, category totals, escaping, badge colors, and semantic fallback text. | Dotfiles writer `references/change-navigation.md:7-111` | Retain the behavior through the stronger canonical grammar in `references/change-navigation.md:35-185` and centralized taxonomy in `scripts/change_navigation/categories.py:18-65`. |
| Use singular metric nouns where appropriate, accept the documented legacy plural form, and require a blank separator before Markdown after a leading `</details>`. | Dotfiles writer `scripts/change_navigation/parsing.py:35-55`; `tests/test_validate_change_navigation.py:56-59,162-171`; `tests/test_bounded_diff.py:310-336` | Carry the grammar compatibility forward, but strengthen separation to a truly empty source line between canonical Stack and Diff blocks and before every nonempty suffix, including raw HTML. Parse only that byte-zero, full-line prefix; preserve the suffix as opaque bytes through exhaustive ordered baseline fragments, with narrow reserved navigation signatures rejected fail-closed. |
| Use one focused diagram only when it shortens review; preserve Stack/Diff and current body content when escalating to an atlas. | Dotfiles writer `SKILL.md:48-52` | Preserve the selection rule under the stronger canonical atlas contract and optional extension schema. |
| Capture the exact live preimage, mutate once, reread, avoid automatic retry or rollback, create drafts first, and substitute only the assigned PR-number token. | Dotfiles publisher `SKILL.md:13,24-45,47-85` | Preserve these guarded-publication invariants. Canonical publisher `SKILL.md:14-16,20-36,64-94` strengthens them with scoped text authority, manifests, sensitive-content gates, receipts, and review evidence. |
| Require qualified `OWNER:BRANCH`, live pushed OIDs, literal existing payload paths, custom-content preservation, and live rendering inspection after structured changes. | Dotfiles publisher `SKILL.md:93-105` | Preserve through canonical publisher preflight and writer baseline-fragment validation. |
| Preserve the dotfiles behavioral evals, trigger evals, fixtures, and targeted regression intent. | Dotfiles `evals/` and `tests/` in both skills | Migrate the behavior to canonical-owned test and eval locations. Do not retain dotfiles path ownership. |

## Corrected Behaviors

| Behavior | Evidence | Correction |
| --- | --- | --- |
| Ready-only publication uses the publisher “alone.” | Dotfiles writer `SKILL.md:21`; publisher `SKILL.md:19,27`; publisher ready eval | The publisher orchestrates ready-only actuation, but the writer remains the required validator/manifest dependency. Canonical publisher `SKILL.md:23-24,96-105` must not be presented as operational without its sibling writer. |
| Diff statistics use direct base/head endpoint comparison. | Canonical `scripts/change_navigation/git_observer.py:44-58,121-133` | Observe the pull request's merge-base/three-dot diff. GitHub Files and the immutable comparison URL describe that reviewer-visible change, not a two-endpoint diff after base divergence. Add a diverged-base regression. |
| A large Diff selects the first 100 files from locally sorted target paths. | Dotfiles writer `SKILL.md:31` and `scripts/change_navigation/diff_inventory.py:46-108`; canonical `git_observer.py:166-178` and `review_input.py:333-365` | Retain deterministic local target-path order without claiming it matches GitHub Files order. Binding reviewer-visible GitHub order requires the deferred observer/adapter identified below; neither source implements it. |
| SHA-256 of the target path always proves a Files anchor. | Dotfiles and canonical `references/change-navigation.md` require verification; both `diff_metrics.py` implementations hardcode the hash form | Accept an independently observed GitHub anchor or document a fail-closed supported-platform convention. Cover deletion, rename, copy, and unusual path cases. |
| Canonical change-navigation examples are executable specifications. | Canonical `references/change-navigation.md:46,48,50,52,102,104-107`; `badge_presentation.py:52-78`; `categories.py:61-65` | Add required category `title` attributes and use the exact taxonomy note. The Stack example has the old short taxonomy, and the Diff example omits it, so the baseline examples fail their own validator contract. |
| Rendering the first 100 rows is always enough to satisfy GitHub's body limit. | Canonical writer `SKILL.md:37-43`; `review_input.py:333-365` | Keep the 65,536-character fail-closed limit. Schema v3 has no second truncation path: stop publication when the canonical 100-row body plus its remaining content does not fit. |
| Standalone manifest validation independently proves live PR identity and authorization. | Canonical `review_input.py:556-664` | State that a plain content digest detects drift, not authorization. Independent Git observation occurs only when a Git repository is supplied, and noncurrent stack rows are not live-observed. Publisher adapters must bind live forge identity and the clean worktree. |
| A command error followed by the intended final state is successful publication. | Dotfiles publisher `SKILL.md:87-89` | Treat the result as causally ambiguous and mint no canonical receipt. Canonical publisher `SKILL.md:98-103` has the correct rule. |
| A no-op update is a successful updater path, or local leases make GitHub mutation atomic. | Dotfiles publisher `SKILL.md:13`; canonical publisher `SKILL.md:14-16,85-90,126-134` | Reject no-op before mutation. Keep the explicit statement that GitHub has no conditional atomicity. Local leases serialize only cooperating processes using the same local state root. |
| The writer owns post-publication stored/rendered verification. | Dotfiles writer `SKILL.md:44` | Assign stored-state and rendered-state verification to the publisher. Canonical writer `SKILL.md:60-63` and publisher `SKILL.md:31-36` establish the correct boundary. |
| Hardcoded `$HOME/.agents` paths and the raw literal creator filename form a portable package. | Dotfiles writer `SKILL.md:35`; publisher `SKILL.md:37,52,59,76`; dotfiles publisher `scripts/literal_create_reviewable_pr.py` | Use relocatable sibling-skill discovery and one canonical creator filename. Document that the publisher imports the writer and cannot operate as an isolated skill. Do not rely on installation-time renaming. |

## Superseded Behaviors

| Dotfiles mechanism | Canonical replacement |
| --- | --- |
| Markup and destination validation with optional caller-supplied base/head identity | Versioned review-input manifest binding repository, PR number, head repository/owner, pushed refs/OIDs, exact candidate title/body digest, complete Git and categorized inventories, stack, authorized text surface, and baseline-fragment dispositions; optional clean-worktree Git observation and sensitive-content rejection |
| Bounded first-100 presentation plus aggregate remainder badges | Schema-v3 complete sealed inventory, explicit selected targets, omitted count, immutable comparison URL, canonical omission record, and body-size enforcement |
| Broad title-and-body update helper | Explicit `body-only`, `title-only`, or `title-body` authority with unauthorized-field byte preservation |
| Ralph and generic workflow-ownership hooks | Imported exact Tricritical review-loop operation plus direct owner-boundary instructions in the canonical writer |
| Conceptual dotfiles atlas reference | Hardened canonical atlas reference and optional overlay schema, while retaining atlas generation as an extension rather than claiming an implementation |
| Guarded mutation with only final-state reporting | Canonical creation/update/ready operations with redacted append-only receipts, local leases, audit, reconciliation, explicit review modes, and required-review evidence gates |

## Intentionally Excluded Behaviors And Artifacts

| Item | Reason |
| --- | --- |
| Installed-only `large-pr-description-example.md` and `scripts/reviewable_pr.py` | Unauthenticated stale projection residue, not source input |
| Installation-time rename from `literal_create_reviewable_pr.py` to `create_reviewable_pr.py` | Masks a broken raw-source contract and prevents project-local operation |
| Personal PreToolUse-hook activation note in the dotfiles publisher | User-specific installed policy, not portable Mergecraft source behavior |
| Dotfiles acceptance of 64-hex Git OIDs | Outside the current GitHub PR identity contract; revisit only when the supported forge exposes SHA-256 PR commit OIDs |
| Built-in GitHub Files-order observation | Neither source observes that order. Keep it as a deferred extension instead of presenting deterministic local path order as reviewer-visible GitHub order. |
| Dotfiles path ownership for evals and tests | Preserve behavior, but canonical source owns the durable test/eval location |
| Publisher-alone packaging or operation | The publisher imports and depends on the sibling writer's validated pair and manifest |
| Claims that receipts authorize or globally serialize GitHub state | Receipts are local provenance and drift evidence, not a cryptographic authorization or cross-actor transaction boundary |

## Remaining Extension Boundary

Neither baseline source tree contains a complete deterministic generator,
pathname classifier, live stack-discovery adapter, GitHub Files observer, or
GitHub Action. The dotfiles `diff_inventory.py` only plans rows that another
component has already ordered and classified. The canonical plugin validates and
seals supplied review input and can observe a local Git diff, but it does not
render the body, classify paths, discover a live stack, fetch GitHub's file order,
or run as an Action.

Keep these as explicit inspect/adapt extension points. Do not imply that the
baseline has an operational generator or CI adapter. A future implementation
must bind GitHub's reviewer-visible merge-base diff and file order, preserve the
sealed manifest contract, and remain fail-closed when classification, stack
identity, or anchor observation is unavailable.

## Scope And Ownership Boundary

This disposition authorizes canonical source reconciliation only. It does not
authorize a Mergecraft plugin release, installation, installed-projection
removal, dotfiles mutation, migration, or a colleague-facing how-to. Issue #45
remains the release owner for qualification, publication, installation,
migration, and retirement decisions.
