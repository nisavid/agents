# Mergecraft

Mergecraft is the portable pull-request lifecycle plugin. It owns reviewer
navigation, guarded PR publication, Graphite draft transport, feedback
coordination and interaction, review readiness, merge closeout, and stacked
fixups. [topology.json](topology.json) is the only machine-readable graph and
operation-ownership authority.
Its `schema_version` versions Mergecraft's local topology shape, not a
repository-wide interchange schema.

## Public skills

| Public skill | Responsibility |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| writing-reviewable-pr-descriptions | Canonical title/body content and Stack/Diff navigation. |
| publishing-reviewable-prs | Standalone creation, exact title/body/draft-ready actuation, and publication evidence/audit/reconciliation. |
| graphite | Graphite topology and temporary stacked draft transport. |
| addressing-pr-review-feedback | Feedback-outcome coordination across acquisition, adjudication, revision, checkpoint, and interaction owners. |
| interacting-with-pr-review-feedback | Authorized reply, reaction, or thread-resolution leaf writes. |
| resuming-reviewed-prs | Exact target recovery and selection of the next lifecycle owner. |
| getting-prs-ready-for-review | Review-readiness outcome coordination. |
| getting-prs-merged | Merge outcome coordination through the internal merge actuator. |
| stacking-pr-fixups | Narrow fixup branches and stacked fixup PR coordination. |

There are no compatibility routers and no public PR-creation orchestrator.
Semantic sibling links remain relative within this plugin. Cross-plugin calls
use qualified identities.
## Operation registry

<!-- BEGIN GENERATED OPERATION REGISTRY -->
| Semantic ID | GitHub aliases | Surface | Access | Authority | Disposition | Owner | Implementation/import | Callers |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pr-content | - | workflow | coordinate | candidate PR title and body bytes only | public-skill | writing-reviewable-pr-descriptions | skills/writing-reviewable-pr-descriptions/SKILL.md | getting-prs-ready-for-review, publishing-reviewable-prs, stacking-pr-fixups, writing-reviewable-pr-descriptions |
| pr-creation | pr-create | github | write | exact repository, base, qualified head, head repository, and draft PR creation | public-skill | publishing-reviewable-prs | skills/publishing-reviewable-prs/SKILL.md | getting-prs-ready-for-review, publishing-reviewable-prs, stacking-pr-fixups |
| pr-text-read | pr-text-read | github | read | bound repository and PR identity for exact title and body acquisition | public-skill | publishing-reviewable-prs | skills/publishing-reviewable-prs/SKILL.md | publishing-reviewable-prs |
| pr-text-write | pr-text-write | github | write | exact repository, PR, title and body preimage, candidate bytes, and authorized text surface | public-skill | publishing-reviewable-prs | skills/publishing-reviewable-prs/SKILL.md | getting-prs-ready-for-review, graphite, publishing-reviewable-prs, stacking-pr-fixups |
| pr-readiness-read | pr-readiness-read | github | read | bound repository and PR identity for exact draft or ready state acquisition | public-skill | publishing-reviewable-prs | skills/publishing-reviewable-prs/SKILL.md | publishing-reviewable-prs |
| pr-readiness-write | pr-readiness-write | github | write | exact repository, PR, head, draft preimage, readiness gates, and mark-ready authority | public-skill | publishing-reviewable-prs | skills/publishing-reviewable-prs/SKILL.md | getting-prs-ready-for-review, graphite, publishing-reviewable-prs, stacking-pr-fixups |
| publication-evidence | - | workflow | coordinate | exact requested forge mutation or read-only audit | public-skill | publishing-reviewable-prs | skills/publishing-reviewable-prs/SKILL.md | publishing-reviewable-prs |
| publication-audit | - | workflow | read | bound repository, PR, base, head, text, readiness, and authoritative receipt identity | public-skill | publishing-reviewable-prs | skills/publishing-reviewable-prs/SKILL.md | getting-prs-merged, getting-prs-ready-for-review, graphite, publishing-reviewable-prs, resuming-reviewed-prs, stacking-pr-fixups |
| publication-reconciliation | - | workflow | coordinate | exact requested forge mutation or read-only audit | public-skill | publishing-reviewable-prs | skills/publishing-reviewable-prs/SKILL.md | graphite, publishing-reviewable-prs |
| graphite-topology | - | workflow | coordinate | bound Graphite stack identity, ancestry, topology, and authorized topology operation | public-skill | graphite | skills/graphite/SKILL.md | graphite, stacking-pr-fixups |
| graphite-transport | - | workflow | coordinate | bound Graphite stack topology and intended draft transport | public-skill | graphite | skills/graphite/SKILL.md | graphite, stacking-pr-fixups |
| feedback-acquisition | review-comment-read | github | read | separately bound authority for feedback-acquisition | internal-helper | internal:feedback-state-acquisition | skills/addressing-pr-review-feedback/scripts/review_feedback_state.py | addressing-pr-review-feedback, getting-prs-merged |
| feedback-outcome | - | workflow | coordinate | read-only snapshot or separately authorized finding revisions and interactions | public-skill | addressing-pr-review-feedback | skills/addressing-pr-review-feedback/SKILL.md | addressing-pr-review-feedback |
| feedback-interaction | reactions-write, review-reply-write, review-thread-resolution | github | write | exact selected feedback leaf and verified reviewer identity | public-skill | interacting-with-pr-review-feedback | skills/interacting-with-pr-review-feedback/SKILL.md | addressing-pr-review-feedback, interacting-with-pr-review-feedback |
| readiness-outcome | - | workflow | coordinate | readiness-only coordination and separately authorized ready mutation | public-skill | getting-prs-ready-for-review | skills/getting-prs-ready-for-review/SKILL.md | getting-prs-ready-for-review |
| check-inspection | check-inspection | github | read | bound repository, PR, head, required GitHub Actions run, job, and annotations | internal-helper | internal:gh-fix-ci-adapter | skills/getting-prs-merged/references/gh-fix-ci-adapter.md | getting-prs-merged |
| focused-ci | check-rerun | github | write | exact failed GitHub Actions target and separately authorized scoped rerun or repair | internal-helper | internal:gh-fix-ci-adapter | skills/getting-prs-merged/references/gh-fix-ci-adapter.md | getting-prs-merged |
| merge-inspection | merge-inspection | github | read | bound repository, PR, base, head, mergeability, and repository merge policy | internal-helper | internal:merge-actuator | skills/getting-prs-merged/references/merge-actuator.md | getting-prs-merged |
| merge-actuation | merge-write | github | write | exact repository, PR, base, head, merge method, passed gates, and merge authority | internal-helper | internal:merge-actuator | skills/getting-prs-merged/references/merge-actuator.md | getting-prs-merged |
| coderabbit-top-level-comment | bot-review-request, top-level-comment-write | github | write | separately bound authority for coderabbit-top-level-comment | internal-helper | internal:coderabbit-top-level-comment | skills/getting-prs-merged/scripts/post_coderabbit_comment.py | getting-prs-merged |
| merge-outcome | - | workflow | coordinate | merge-outcome coordination with separately bound leaf authorities | public-skill | getting-prs-merged | skills/getting-prs-merged/SKILL.md | getting-prs-merged |
| stack-fixup | - | workflow | coordinate | task-owned fixup paths and intended stack only | public-skill | stacking-pr-fixups | skills/stacking-pr-fixups/SKILL.md | stacking-pr-fixups |
| conflict-resolution | - | workflow | write | authority defined by imported owner versionkeeping:resolving-merge-conflicts | imported-operation | versionkeeping:resolving-merge-conflicts | versionkeeping:resolving-merge-conflicts | - |
| git-ref-push | - | git | write | authority defined by imported owner versionkeeping:checkpointing-and-publishing-git-work | imported-operation | versionkeeping:checkpointing-and-publishing-git-work | versionkeeping:checkpointing-and-publishing-git-work | addressing-pr-review-feedback, getting-prs-ready-for-review, graphite, stacking-pr-fixups |
| finding-adjudication | - | workflow | coordinate | authority defined by imported owner tricritical:adjudicate | imported-operation | tricritical:adjudicate | tricritical:adjudicate | addressing-pr-review-feedback |
| source-revision | - | workflow | write | authority defined by imported owner tricritical:revise | imported-operation | tricritical:revise | tricritical:revise | addressing-pr-review-feedback |
| review-loop | - | workflow | coordinate | authority defined by imported owner tricritical:loop | imported-operation | tricritical:loop | tricritical:loop | writing-reviewable-pr-descriptions |
| remote-ref-deletion | - | git | write | exact remote endpoint and full ref deletion authority | imported-operation | versionkeeping:checkpointing-and-publishing-git-work | versionkeeping:checkpointing-and-publishing-git-work | - |
| github:repository-orientation | repository-orientation | github | read | bound repository identity | ordinary-tool | internal:github-read | ordinary operation-specific GitHub read tool | - |
| github:pr-orientation | pr-orientation | github | read | bound repository and PR identity | ordinary-tool | internal:github-read | ordinary operation-specific GitHub read tool | - |
| github:issue-orientation | issue-orientation | github | read | bound repository and issue identity | ordinary-tool | internal:github-read | ordinary operation-specific GitHub read tool | - |
| github:repository-summary | repository-summary | github | read | bound repository identity | ordinary-tool | internal:github-read | ordinary operation-specific GitHub read tool | - |
| github:pr-summary | pr-summary | github | read | bound repository and PR identity | ordinary-tool | internal:github-read | ordinary operation-specific GitHub read tool | - |
| github:issue-summary | issue-summary | github | read | bound repository and issue identity | ordinary-tool | internal:github-read | ordinary operation-specific GitHub read tool | - |
| github:patch-inspection | patch-inspection | github | read | bound repository and base/head identity | ordinary-tool | internal:github-read | ordinary operation-specific GitHub read tool | - |
| github:top-level-comment-read | top-level-comment-read | github | read | bound repository and PR or issue identity | ordinary-tool | internal:github-read | ordinary operation-specific GitHub read tool | - |
| github:labels-read | labels-read | github | read | bound repository target identity | ordinary-tool | internal:github-read | ordinary operation-specific GitHub read tool | - |
| github:labels-write | labels-write | github | write | exact target and label set | ordinary-tool | internal:github-label-write | ordinary operation-specific GitHub label actuator | - |
| github:reactions-read | reactions-read | github | read | bound feedback or comment identity | ordinary-tool | internal:github-read | ordinary operation-specific GitHub read tool | - |
| github:review-submit-comment | review-submit-comment | github | write | bound PR head and exact review body | ordinary-tool | internal:github-review-submit | ordinary operation-specific GitHub review actuator | - |
| github:review-submit-approve | review-submit-approve | github | write | bound PR head and explicit approval authority | ordinary-tool | internal:github-review-submit | ordinary operation-specific GitHub review actuator | - |
| github:review-submit-request-changes | review-submit-request-changes | github | write | bound PR head and explicit request-changes authority | ordinary-tool | internal:github-review-submit | ordinary operation-specific GitHub review actuator | - |
<!-- END GENERATED OPERATION REGISTRY -->

The internal merge actuator, top-level comment actuator, and CI adapter are
operation-specific and are not public skills. The merge actuator performs one
fully bound write and returns a reread receipt to the merge-outcome coordinator.
The CI seam preserves `github:gh-fix-ci` as its upstream, independently
updateable implementation while limiting delegation to Actions diagnosis and
separately authorized scoped fixes.

Each public component in `topology.json` declares its trigger, modes, authority,
inputs, outputs, forbidden reverse calls, loop owner, and terminal statuses.
Together with the call graph and sole operation owners, these contracts are the
caller/callee boundary. Lifecycle coordination stays with the current public
owners; there is no additional public review-orchestration layer.

## Layout

    plugins/mergecraft/
    ├── .claude-plugin/plugin.json
    ├── .codex-plugin/plugin.json
    ├── skills/
    ├── topology.json
    ├── CHANGELOG.md
    └── LICENSE

## Installed validation

From the installed plugin root:

    python3 skills/writing-reviewable-pr-descriptions/scripts/validate_change_navigation.py --help
    python3 skills/publishing-reviewable-prs/scripts/create_reviewable_pr.py --help
    python3 skills/publishing-reviewable-prs/scripts/update_reviewable_pr.py --help
    python3 skills/publishing-reviewable-prs/scripts/audit_reviewable_pr.py --help
    python3 skills/graphite/scripts/submit_draft_stack.py --help
    python3 skills/getting-prs-merged/scripts/post_coderabbit_comment.py --help
    python3 skills/addressing-pr-review-feedback/scripts/review_feedback_state.py --help

Repository release validation additionally runs scripts/validate_mergecraft.py
and the repository-owned unit suites. Canonical development and release
evidence lives at `evals/mergecraft/`, `tests/plugins/mergecraft/`, and
`release/plugin-content-locks/mergecraft.json`; none of it is installed in the
runtime root.
