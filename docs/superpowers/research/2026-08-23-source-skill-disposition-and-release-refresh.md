# Source-skill disposition and release refresh

Status: settled contribution decisions for issue [#50](https://github.com/nisavid/agents/issues/50); not a release-eligibility or host-convergence receipt

## Why this layer exists

Issue [#49](https://github.com/nisavid/agents/issues/49) records what source material and installed routes were observed. It deliberately leaves contribution decisions unresolved. This report and `release/source-skill-disposition/disposition-ledger.json` answer the next question: what should the coordinated plugin suite do with every recorded contribution?

The separation is intentional. Historical evidence remains reproducible even when a later owner decision changes. The Issue #50 validator reads the Issue #49 artifacts without modifying them, requires exactly one decision for each of their 30 contribution records, and still grants no source-mutation, installation, removal, or release authority.

## Outcome

The six coordinated distributions remain the complete plugin suite. No seventh plugin is needed.

| Source group | Decision | Durable owner or boundary |
| --- | --- | --- |
| Candidate-authored package lineages | Equivalent or stronger | Their current coordinated distributions |
| Current personal Mergecraft and Rolecasting sources | Absorb or refresh | Three-way reconcile historical import, current personal source, and current plugin; never overwrite the plugin's stronger contracts |
| Current personal Versionkeeping source | Equivalent or stronger | Later prose and standalone eval-harness changes are already covered by stronger plugin contracts |
| Cursor Thermos review concepts | Defer, blocking | Tricritical has family-level attribution, but Daybreak issue #56 must complete the rule-level map before release eligibility |
| Review Atlas | Split by component | Mergecraft absorbs or refreshes the public core; the opaque private overlay remains external and may only impose stricter local policy |
| OpenAI GitHub and Yeet behaviors already covered by the retirement ledger | Equivalent, retained specialist, ordinary tool, or rejected | Mergecraft and Versionkeeping own bounded replacements; `gh-fix-ci` remains independent; unbound PR creation is rejected |
| Firecrawl, Here.now, Hyperframes, Elements of Style, Hindsight, and Find Skills | Retain side by side | Their existing external specialist owners |
| Graphite source | Equivalent or stronger | Mergecraft's adapted `graphite` owner already covers the later prose-only change |
| Matt Pocock skills | Retain side by side, with one explicit derivative | The managed upstream remains immutable; Versionkeeping's `resolving-merge-conflicts` is a maintained derivative |
| Superpowers | Supersede and remove | Existing global, managed-skill, Mergecraft, Rolecasting, Tricritical, Versionkeeping, and honing owners; actual removal belongs to #51 |

The machine-readable ledger carries the exact decision, owner, evidence paths, rationale, and follow-up issue for every Issue #49 contribution. Its nested records cover all 25 Matt skills and all 14 Superpowers skills without treating either aggregate record as an implicit blanket decision.

## Managed Matt skills stay immutable

The managed Matt Pocock source is not a place to store personal behavior. `diagnosing-bugs`, `tdd`, `writing-for-agents`, `code-review`, and the other managed skills remain unchanged and independently owned.

Matt's `code-review` and Tricritical are intentionally side by side, not a migration pair. The immutable Matt skill owns an independent Standards/Spec two-axis review. Tricritical owns the coordinated intent/runtime/structure review plus adjudication, revision, and loop closure. Neither owner absorbs or mutates Matt's skill.

One relationship needs a more precise classification. Versionkeeping's `resolving-merge-conflicts` shares the upstream skill's behavioral skeleton and substantially rewrites it behind Versionkeeping's closed Git-operation contract. It is therefore a maintained derivative, not an unrelated same-name skill. Before candidate qualification, the release work must:

1. bind the imported Matt revision, using `84fdeffd12f2ee307994d1eb6feb48173b6e0502` as the conservative baseline unless a more exact historical receipt is recovered;
2. add the required MIT attribution to Versionkeeping;
3. correct Versionkeeping's provenance statement; and
4. leave the managed upstream skill untouched.

Matt's current upstream revision observed during this disposition audit is `5b15a47f2d7150f545fbcacbfe381787fc0230dc`. The Issue #49 manifest still names its older research-time revision. Issue #45 must refresh that evidence rather than treating this report as a substitute source receipt.

## Component decisions and blocking evidence

Review Atlas has two explicit nested decisions. Mergecraft absorbs or refreshes the publishable public core through its checked-in contribution ledger. The opaque private overlay remains with its external owner and can only add stricter local policy. The aggregate record is therefore `split-by-component`; automation must not interpret the private overlay as absorbable.

Thermos is not yet closed at the same resolution. Tricritical's `NOTICE` proves family-level attribution, but Issue #49 requires a rule-level source-to-destination map that does not exist yet. The ledger therefore marks Thermos `defer-blocking` and assigns that mapping and review to Daybreak issue [#56](https://github.com/nisavid/agents/issues/56). Issue #45 cannot infer release eligibility from the existing family notice.

## Superpowers closure

All 14 Superpowers skills are superseded. Nine had not previously received explicit destinations:

| Retiring skill | Disposition |
| --- | --- |
| `brainstorming` | Existing requirements and brainstorming workflows are sufficient |
| `systematic-debugging` | Keep immutable `diagnosing-bugs`; remove the obsolete global route in #51 |
| `test-driven-development` | Keep immutable `tdd`; add only the accepted pre-change failure-provenance rule to global instructions in #51 |
| `writing-skills` | Keep immutable `writing-for-agents` and system `skill-creator`; move the two accepted refinements to the personal honing skill in #51 |
| `verification-before-completion` | Keep verification with each task's owning workflow and external acceptance contract |
| `writing-plans` | Keep native plan state and durable requirements workflows; add no planning skill |
| `executing-plans` | Keep native plan state and standing implementation workflows |
| `subagent-driven-development` | Rolecasting retains same-shape batching and useful-local-work, bounded-wait, and live-child reconciliation behavior |
| `using-superpowers` | Use standing skill routing directly |

The other five were already represented by coordinated owners:

- `dispatching-parallel-agents` → Rolecasting;
- `finishing-a-development-branch` → Mergecraft and Versionkeeping;
- `receiving-code-review` → Mergecraft and Tricritical;
- `requesting-code-review` → Mergecraft and Tricritical; and
- `using-git-worktrees` → Versionkeeping.

These are source-contribution decisions, not removal commands. Issue [#51](https://github.com/nisavid/agents/issues/51) owns install-before-remove ordering, duplicate and shadow checks, rollback, global route edits, personal honing changes, and proof that the retired routes are absent.

## Source changes that the final refresh must preserve

The initial coordinated-package import does not bind an exact personal-source revision. For a conservative comparison, use dotfiles commit `83192a22633b5aa2e808553c1acdfbecdc455b8d`, immediately before the current-lineage Mergecraft and Versionkeeping imports. It is a comparison baseline, not an exact byte-sync receipt.

The current personal sources contain later changes. The three-way audit classified them as follows.

### Mergecraft imports still required

The final refresh must import these behaviors into the plugin-native owners and eval system without replacing the stronger publication runtime:

- reviewer decision path, readiness truthfulness, manual operational-test classification and cleanup, temporal or visual explanation, and issue-closure provenance from `a8d3065c364e0ee4c56c0aa507887248b3bf5d83` and `6ef9085750202e69e0c7cade9e3defe037d28809`;
- routing in which text mutation remains with the publisher and writer, ready-only uses the publisher without recomposition, and chat-only uses the writer without publication, from `0ce01eaaed9d7ae8514b25046b92d0345ca8942f`;
- chat-only/no-PR behavior and inert literal PR-number handling from `de0ee9355bab580bc821e8163567df181dbc9beb`, `909ed98e5e05b3d806f7b4dd6f2b5fd0261c2cd9`, and `662d2c331abffb9188e355c35c892332965f5f45`;
- the final source-shape contract—one source line per prose block and explicit `<br>` for intentional breaks—from `5609e44db9ad79150d5e47e826f2982add7c8caf`, `f1f3f56fd86ca1f8d3bf922ff69ef13072e21400`, and `50984434fba38ea9bf9dee651725e700d28e4c6f`; and
- disclosure-separation validation through `leading_details_spans` and `validate_details_separation` from `b2aa00a3e6542a5228f02aeefd9a941732d67e76`.

Do not restore the superseded wrapping scanner from `33b84805b9ac4b79f3d3d21be9488045d833cb28`, `8a5d3e9b9c5d023a2788ddc67500e2ad3683a9d2`, `71cd9a66bffa90222711dcb473461bdb0777b4f8`, or `33e5553a408eeb027c1e4530a4f69e511d7a882b`. The current plugin's identity, preimage, large-diff, navigation, and publication contracts are already stronger than the remaining post-cutoff source changes.

### Rolecasting and Versionkeeping

Rolecasting keeps its stronger target, topology, authority, assurance, and evidence contracts. This Issue #50 change imports the accepted same-shape batching and bounded-wait/live-child reconciliation behaviors. The final refresh must still compare the broader current personal model/task matrix, mixed-task splitting, harness routing, lifecycle distinctions, handoff guidance, and prompt requirements one behavior at a time.

Versionkeeping's personal checkpoint, fork-sync, and worktree changes after the conservative cutoff are prose or standalone eval-harness changes already covered by stronger plugin behavior. They need no byte import. The maintained `resolving-merge-conflicts` derivative still needs the provenance and attribution closure described above.

Installed projections are observation evidence, not import authority. The final refresh compares immutable source revisions and current canonical plugin bytes; it does not copy whichever installed route happens to be visible.

## Last-responsible-moment refresh

Issue [#45](https://github.com/nisavid/agents/issues/45) owns the final refresh and candidate qualification. Complete it after candidate bytes and contribution decisions have settled, and capture the qualification receipt no more than 86,400 seconds after the upstream refresh and full installed-library rescout.

The rescout includes:

- global `AGENTS.md` instructions;
- plugin-provided skills;
- skills-managed skills;
- external-tool-managed skills;
- standalone skills; and
- every recursively referenced additional instruction, conditional instruction, and supporting material.

Every material discovery receives one terminal decision: retain as-is, migrate to plugin equipment, migrate to an external personal owner, or drop with a reason. Later source changes use a three-way semantic reconciliation among the historical import, current source, and current plugin. A whole-file copy is not an admissible sync when either side has independently strengthened its contract.

The 24-hour ceiling is not a grace period for known drift. Refresh immediately if any of these changes:

- source revision, tree, content, or license;
- contribution decision;
- candidate package byte or identity artifact, including Task Witness source-shape and suite-inventory evidence;
- installed membership or identity; or
- instruction discovery route.

Requalification follows the affected dependency closure derived from `evals/control-plane-matrix.json` plus the concrete package, test, documentation, evaluation, topology/owner, identity-artifact, and validator paths registered for each distribution in the refresh contract. The contract also supplies provider-to-consumer dependency edges and fixed-point traversal semantics. This closes distributions omitted by the matrix: a Tricritical change pulls in Mergecraft and then Artifact Customs; Task Witness and Versionkeeping changes pull in Mergecraft and Artifact Customs; and Rolecasting changes pull in Tricritical, Mergecraft, and Artifact Customs. It is not a blanket rerun merely because one unrelated source changed.

The refresh contract makes that closure actionable. It names all six coordinated distributions; the repository and per-package candidate-identity tuple; each package's lock or source-shape identity artifact; and the source manifest, contribution ledger, disposition ledger, refresh contract, candidate identity, and installed-library evidence that the future reconciliation receipt must bind. The two checked-in `initial-*` host manifests remain historical baselines. They do not prove a later rescout. Issue #45 must produce the versioned `final-candidate-rescout-v1.json` artifact under the raw-byte-bound `final-installed-library-rescout.schema.json` contract. That schema fixes field types and constants, complete/sorted profile and instruction inventories, explicit zero-or-more counts for every rescouted surface, timestamp ordering, decision vocabulary, candidate/source/ledger/contract digests, and canonical content digests. The publication receipt must bind both the schema and completed artifact by raw-byte digest.

Every immediate trigger maps to an explicit change class, and each class carries concrete JSON-pointer output selectors into the registered distribution closure, identity artifacts, receipt bindings, and required output inventory. A disposition-only change rebinds policy and downstream evidence without pretending package bytes changed. Package-byte and identity-artifact changes are separate: the former refreshes affected skills, tests, docs, locks, candidate projections, and the source manifest, while the latter keeps `changes_package_bytes` false and regenerates the candidate projection and source-manifest bindings around the changed identity evidence. Source-evidence changes rerun upstream identity/license capture and three-way reconciliation even when package bytes stay stable. Installed-membership, identity, or discovery-route changes rerun the complete installed-library rescout. Because the versioned final-rescout artifact embeds the source, contribution, disposition, refresh-contract, and candidate digests, every change class regenerates it after those inputs settle. Every class then reruns affected plugin evaluation, independent review, qualification, routing/composed compatibility, and final release evidence.

## Validation and authority boundary

Run:

```sh
uv run --with jsonschema==4.26.0 python -m unittest tests.test_validate_source_skill_disposition
python3 scripts/validate_source_skill_disposition.py .
```

The `Source skill disposition` pull-request check installs that pinned schema engine and runs both commands. Schema behavior is therefore required evidence rather than an optional local test.

The validator proves exact Issue #49 contribution coverage, source-ID agreement, aggregate managed-skill coverage, raw-byte evidence bindings, refresh scope, and the no-removal/no-release authority boundary.

It intentionally remains separate from `refresh_source_skill_lineage.py`, the hardened reconciliation receipt, and `validate_public_release.py`. Integrating those security-sensitive publication boundaries requires the Daybreak review owned by issue [#56](https://github.com/nisavid/agents/issues/56). Until that follow-up lands, this artifact is a checked-in, read-only decision contract and `release_eligibility` remains `not-asserted`.
