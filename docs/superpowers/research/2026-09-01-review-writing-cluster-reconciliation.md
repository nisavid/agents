# Review-Writing Cluster Reconciliation Evidence

## Purpose

This note records the source disposition for the Mergecraft writer and feedback
skills (`writing-reviewable-pr-descriptions`, `publishing-reviewable-prs`,
`addressing-pr-review-feedback`, `interacting-with-pr-review-feedback`,
`resuming-reviewed-prs`) and Tricritical's review-output surfaces, compared
against `nisavid/dotfiles` `main` at `f700471` and the installed projections.
It extends [the statline resync note](2026-08-26-mergecraft-statline-source-resync.md)
to the full review-writing cluster and flushes the parallel same-day hand
edits of 2026-08-31. It is evidence for canonical source reconciliation, not an
installation or release record. The comparison is tied to immutable Git
objects; later working-tree changes do not change this baseline.

## Evidence Baseline

| Source | Revision | Authority |
| --- | --- | --- |
| Canonical `nisavid/agents` plugin source | `44ee979cdae1d47f2ef3fdc713eaa6f04adf9892` (origin/main) | Canonical development source |
| [`nisavid/dotfiles`](https://github.com/nisavid/dotfiles.git) `main` | `f7004719af092d30c9037425e71c21c0aca4c7ff` | Pinned source input to classify, not a replacement canonical source |
| Installed `$HOME/.agents/skills` projections | No authenticated source revision | Evidence only; inspected without editing |

Per-skill tree objects at those revisions:

| Skill | Canonical tree (`plugins/mergecraft/skills/`, `plugins/tricritical/`) | Dotfiles tree (`home/dot_agents/skills/`) |
| --- | --- | --- |
| `writing-reviewable-pr-descriptions` | `9cb5af4a033ddae21a46bbe99ae3f26a50917c32` | `312b5b474153ba7a57d37177a72b68254337e62f` |
| `publishing-reviewable-prs` | `e3f2917260d23dde3ac195267b25558311c98f33` | `cba16890a5c93103360802ee83b8e6edcf6024f1` |
| `addressing-pr-review-feedback` | `504797ebfd55151331a5c94193f268fe3343f2ab` | Absent (plugin-original) |
| `interacting-with-pr-review-feedback` | `5eeed091b67af777087dcc8fa668534cfa13c61c` | Absent (plugin-original) |
| `resuming-reviewed-prs` | `0a8b3541dd6048ece10824b5cb58c704aaff7520` | `abd55f2aa09d2d825c8b993a3d564445a28804ee` |
| `reviewing-others-prs` | Absent (dotfiles-only) | `585b487dc2ef1854b0295a1a0c8fcfcc8bc686c3` |
| Tricritical plugin | `db58756a31a98fc8be81a226230a1eb9cef02df6` | No counterpart |

Installed-projection byte identity: the installed `resuming-reviewed-prs`
(`SKILL.md` only) and all three `reviewing-others-prs` files (`SKILL.md`,
`references/review-output.md`, `agents/openai.yaml`) are byte-identical to the
dotfiles trees at `f700471`. The installed writer equals the dotfiles writer
plus the stale `large-pr-description-example.md`; the installed publisher
remains the dotfiles publisher with `literal_create_reviewable_pr.py` renamed
to a byte-identical `create_reviewable_pr.py` plus the stale
`scripts/reviewable_pr.py`, exactly as in the statline resync note.
`addressing-pr-review-feedback` and `interacting-with-pr-review-feedback` have
no installed projection. The installed `receiving-code-review` is byte-identical
to the Superpowers 6.3.0 plugin cache copy: it is an external Superpowers skill,
not dotfiles-tracked, and stays evidence only.

## Same-Day Parallel Hand Edits (2026-08-31)

Both trees were hand-edited the same morning on the external-review request
policy. Reconciliation of each commit:

| Commit | What it changed | Canonical disposition |
| --- | --- | --- |
| dotfiles `51856f0` (03:07:11) "continue authorized review iterations" | Replaced review "budget" framing with authority/limit framing in `getting-prs-merged` `SKILL.md:52-53,72,88` and `pr-review-orchestration`; renamed the fixture `external-review-budget.md` to `stalled-external-review.md`; renamed "external-review budgets" to "external-review iteration" in `ralph-review-until-clean` `SKILL.md:27`, `resuming-reviewed-prs` `SKILL.md:16`, and `reviewing-others-prs` `SKILL.md:13` | Adopted for `getting-prs-merged`: agents `44ee979` (03:07:24) is the deliberate canonical counterpart — `SKILL.md:79-81` requires checking "readiness and any explicit operator or repository limit" plus explicit authority, and the same fixture rename landed in `evals/mergecraft/skills/getting-prs-merged/`. Superseded for the three routing renames: `pr-review-orchestration` has no canonical skill; its functions are distributed across the publisher `publication-audit`, the addressing snapshot, and the merge coordinator's gates, so no "iteration" terminology exists to rename. |
| dotfiles `a292413` (03:29:47) "expose audit evidence in behavior traces" | One `pr-review-orchestration` eval line | Superseded with the same distribution; the audit-evidence intent lives in the canonical `getting-prs-merged` evals. |
| dotfiles `f700471` (04:01:45) "clarify limits on reviewer requests" | Reordered the skipped-CodeRabbit bullet to verify readiness, authority, and explicit limits before any request (`getting-prs-merged` `SKILL.md:53,88`) | Adopted: canonical `SKILL.md:79-81` already encodes verify-limits-first. One residue is dotfiles-only: the clean-diff selection rule to request `@coderabbit-ai approve pls` instead of a re-review when approval is the only remaining branch-protection gate. Canonical `getting-prs-merged` contains no `approve` guidance (zero occurrences); its CodeRabbit helper binds caller-supplied comment bytes (`SKILL.md:79-84`), so carry the selection rule forward as caller-side request guidance. |

## Writer And Publisher Delta Since The Statline Resync

Canonical commit `2844759` implemented the statline resync dispositions:
migrated the dotfiles behavioral and trigger evals to canonical-owned
`evals/mergecraft/skills/`, corrected the diff observer to the reviewer-visible
merge-base three-dot inventory (`scripts/change_navigation/git_observer.py:44-58,118`),
and hardened statline parsing per that note. The dotfiles publisher tree is
unchanged since that note's baseline (same tree object `cba1689`), so its
dispositions stand with no new input.

The dotfiles writer gained two behaviors after that baseline
(`cd2071e..f700471`, writing-policy commits `b571f02`/`2d6e5ea`/`d6d5573`);
neither exists in the canonical writer:

| Behavior | Evidence | Canonical disposition |
| --- | --- | --- |
| For a fix, state what used to go wrong: realistic scenario, worst case, hypothetical/accidental/exploitable classification, threat surface and blast radius when security is implicated, severity stated honestly | Dotfiles writer `references/body-contract.md:10` | Adopted; carry forward into the canonical `body-contract.md` reviewer-concern list, which is silent on failure narratives for fixes. |
| When manual scenarios share steps, write the shared material once and state what the variant changes; an unexplained verbatim repeat reads as a mistake or a second run | Dotfiles writer `references/body-contract.md:60` | Adopted; carry forward into the canonical manual-plan scenario rules. |

## Resuming Reviewed PRs

The trees diverge structurally: dotfiles keeps an 86-line do-everything
workflow; the canonical skill is a 48-line read-only recovery coordinator.

| Behavior | Evidence | Canonical disposition |
| --- | --- | --- |
| Stop when PR and branch inputs disagree; preserve unrelated local work | Dotfiles `SKILL.md:28,34`; canonical `SKILL.md:14-17` | Adopted; canonical owns both. |
| Inventory every thread, top-level comment, review body, non-thread comment, requested-changes review, and check before disposition; stale items need refreshed evidence | Dotfiles `SKILL.md:35,37,69` | Adopted through the addressing skill's complete head-bound snapshot (`addressing-pr-review-feedback/SKILL.md:24-28`) and its fresh-snapshot rule (`SKILL.md:62-67`). |
| Refresh stale title/body facts through the publisher | Dotfiles `SKILL.md:17,44` | Adopted; canonical routes through the read-only `publication-audit` (`SKILL.md:22-26`) and returns title/body facts to the lifecycle caller (`addressing-pr-review-feedback/SKILL.md:53-56`). |
| "Rebase it" does not authorize a force-push by implication | Dotfiles `SKILL.md:30` | Adopted at the correct owner: canonical resume performs no Git mutation at all (`SKILL.md:46-48`); push authority lives in `versionkeeping:checkpointing-and-publishing-git-work`. |
| One invocation fixes, tightens, replies, resolves, marks ready, and merges | Dotfiles `SKILL.md:32-44` | Corrected: resume is recovery plus exactly one terminal owner handoff in fixed precedence — conflicts, failed required Actions, merge, feedback, readiness (`SKILL.md:27-44`) — never an inline continuation. |
| Route state, ledgers, and gates through `pr-review-orchestration` | Dotfiles `SKILL.md:16,48,62` | Superseded: no canonical counterpart skill; the triage taxonomy (`valid_fix_required`, ... `needs_human_decision`, `SKILL.md:46-50`) is replaced by the imported `tricritical:adjudicate` eight-disposition taxonomy (`addressing-pr-review-feedback/SKILL.md:37-45`). |
| Pause packet for non-agent-owned actuation (`SKILL.md:52-64`) | Dotfiles `SKILL.md:52-64` | Superseded by the status-only bound current-state report with terminal status `reported` (`SKILL.md:9-12`) plus per-owner terminal handoffs. |
| `receiving-code-review` before accepting or rejecting feedback | Dotfiles `SKILL.md:18` | Superseded by frozen-contract adjudication (`addressing-pr-review-feedback/SKILL.md:29-45`), which owns verify-before-implementing; the Superpowers skill remains external installed evidence. |
| Ralph review cycles for fixes and judgments (`SKILL.md:19,42`) | Dotfiles `SKILL.md:19,42` | Superseded by the imported Tricritical review and revision operations (`addressing-pr-review-feedback/SKILL.md:40-49`). |
| Ivan-specific autonomy defaults, pause rules, and `shepherd-pr` routing | Dotfiles `SKILL.md:22-24,30` | Intentionally excluded: user- and host-specific policy, and `shepherd-pr` does not exist canonically. Canonical replaces the defaults with explicit authority reads where unresolved authority is a gate (`SKILL.md:18-19`). |
| `tightening-code-for-review` after fixes (`SKILL.md:20,43`) | Dotfiles `SKILL.md:20,43` | Intentionally excluded from this cluster: reader-burden cleanup is a dotfiles-only skill lane owned by the convergence work (issue #51), not by the resume or feedback coordinators. |

## Reviewing Others' PRs (Dotfiles-Only)

The map assigns this skill a `supersede-and-remove` ledger fate;
`release/source-skill-disposition/disposition-ledger.json` carries no entry for
it yet. These are the behaviors the canonical tree must own before that fate
can execute. No canonical Mergecraft reviewer-side skill exists today, and no
canonical surface carries any comment-voice content.

| Behavior | Evidence | Canonical disposition |
| --- | --- | --- |
| Reviewer-side boundary: review, not repair; chat-only default; no branch edits, posting, approval, resolution, or merge without authority | Dotfiles `SKILL.md:8,25,39` | Adopted; carry forward into the planned Mergecraft `reviewing-others-prs` successor. Currently absent. |
| The Comment Voice thread shape: open with the finding or genuine credit, name what was verified and what remains unchecked, raise issues as the smallest declinable ask with its consequence, one ask per comment, literal `Nit:` labels, speech-like fragments, one finding-title heading at most, optional severity tags | Dotfiles `references/review-output.md:11-19` | Adopted; this is the key material for the Mergecraft-owned review-voice reference the map plans. A slice already exists author-side: natural, non-formulaic replies (`interacting-with-pr-review-feedback/SKILL.md:16-17`). |
| Review summary gives verdict, blockers, and residual gaps; re-review verdict opens with what the new head resolved; a reversal says so with the reason | Dotfiles `references/review-output.md:21` | Adopted; carry forward to the successor. |
| Separate review confidence from merge readiness | Dotfiles `references/review-output.md:23`; `SKILL.md:44` | Adopted; the canonical merge coordinator already refuses green-equals-ready (`getting-prs-merged/SKILL.md:88-92`), but the reviewer-side statement must travel with the successor. |
| Findings must be high-confidence, current on the PR head, deduplicated, file:line-backed, severity-ordered, with the smallest author-owned remedy; a clean review names residual gaps | Dotfiles `SKILL.md:34`; `references/review-output.md:7` | Superseded structurally by the Tricritical review-output contract (`plugins/tricritical/references/review-output-contract.md:3-12`), which adds severity calibration by impact, likelihood, recoverability, and detectability — never fix size — plus `nitpick` separation and uncertainty preservation. |
| Paired Thermos passes before synthesis | Dotfiles `SKILL.md:12,32-33` | Superseded by the Tricritical review operations; the family lineage is attributed in `plugins/tricritical/NOTICE` and gated by the `cursor-thermos` ledger entry. |
| Ledger categories and orchestration routing | Dotfiles `SKILL.md:13`; `references/review-output.md:5` | Superseded as in the resume skill: adjudication taxonomy plus distributed canonical operations. |
| Posting in Ivan's first person | Dotfiles `SKILL.md:25` | Corrected: the register belongs to operator policy and the planned Tidesmith writing plugin; canonical Mergecraft text stays portable while owning the thread shape. |
| `$reviewing-others-prs` invocation metadata | Dotfiles `agents/openai.yaml:1-4` | Intentionally excluded: install-surface projection; the canonical successor mints its own per-skill `agents/openai.yaml` like every Mergecraft skill. |

## Plugin-Original Feedback Skills

`addressing-pr-review-feedback` and `interacting-with-pr-review-feedback` have
no dotfiles counterpart trees and no installed projections; they are canonical
originals. Dispositioned against the nearest dotfiles behaviors: the resume
skill's reply, resolution, and ledger bullets, and the reviewer skill's
refresh-before-acting rule.

| Behavior | Evidence | Canonical disposition |
| --- | --- | --- |
| Draft replies in the author's voice; post only when actuation is owned; resolve only reviewer-owned threads with ownership and disposition evidence | Dotfiles `resuming-reviewed-prs/SKILL.md:74-75` | Superseded by one-leaf-write-per-invocation interaction with adjudicated-disposition and authority preconditions (`interacting-with-pr-review-feedback/SKILL.md:8-20`) and reread verification (`references/interaction-authority.md:3-8`). |
| Refresh head and thread state immediately before posting or resolving | Dotfiles `reviewing-others-prs/SKILL.md:35` | Adopted; canonical re-reads the exact repository, PR, feedback item, and head before the operation (`interacting-with-pr-review-feedback/SKILL.md:13-15`). |
| Answer questions from accepted requirements; escalate product-affecting answers as human decisions | Dotfiles `resuming-reviewed-prs/SKILL.md:38` | Adopted through the `needs operator decision` disposition and the keep-open rule (`addressing-pr-review-feedback/SKILL.md:42-45,61`). |

## Tricritical Review-Output Surfaces

The package contract `plugins/tricritical/references/review-output-contract.md`
and its five per-skill projections (`adjudicate`, `intent`, `review`, `runtime`,
`structure`) are byte-identical, SHA-256
`111bf26d2e3e5ed53d6493a6a09d55ad1d9055aedefcffa04fa20535cd7fb96e`, with the
sync enforced by `scripts/validate_tricritical.py:146,982`. Dotfiles has no
counterpart file; its nearest analog is the reviewing-others-prs
`references/review-output.md` finding shape, which the contract covers and
strengthens as noted above. The contract intentionally excludes voice
restatement: per the map, it stays structural and links out while Mergecraft
owns posted-text voice.

## Scope And Ownership Boundary

This disposition authorizes canonical source reconciliation only. It does not
authorize a plugin release, installation, installed-projection removal,
dotfiles mutation, or the `reviewing-others-prs` removal itself; that removal
stays with the convergence lane (issue #51) under install-before-remove, and
issue #45 remains the release owner.
