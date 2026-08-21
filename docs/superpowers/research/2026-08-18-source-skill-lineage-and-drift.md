# Source-skill lineage and drift

Status: evidence snapshot for issue [#49](https://github.com/nisavid/agents/issues/49); no contribution disposition is decided here

Research inventory observed at: 2026-08-18T06:07:48Z

Candidate projection refreshed at: 2026-08-21T13:22:47Z

## Boundary

The refreshed release-candidate boundary is commit
[`8ec465ea915c6759a3693ac8515f0ee3901b8a4f`](https://github.com/nisavid/agents/commit/8ec465ea915c6759a3693ac8515f0ee3901b8a4f),
tree `acd75254067861dc33ef4a754734138ae3c37af3`. The research began from the
older candidate snapshot
[`b47f03519068b858cf0c070b5d331ee053ef6b7b`](https://github.com/nisavid/agents/commit/b47f03519068b858cf0c070b5d331ee053ef6b7b),
tree `b626f958d4d88c3de75ead85748e62eea5ad482b`; it is historical evidence, not
the current candidate identity. The coordinated-release map still invalidates
dependent evidence when reconciliation changes candidate bytes
([#41](https://github.com/nisavid/agents/issues/41)).

External-source research retains its August 18 observation time. Repository-owned
`ivan-*` current snapshots and candidate package projections were rebound to the
August 21 candidate at the separate refresh time above. Initial-host manifests
retain their own observation timestamps.

This report distinguishes:

1. public upstream Git identities;
2. repository-owned candidate identities;
3. observed installed route presence and canonical skill-ID membership, without
   public machine-local paths;
4. active discovery precedence, captured separately from route presence; and
5. derived reconciliation of unresolved evidence, which does not imply route
   activation or release eligibility.

`unresolved` means evidence is absent or does not bind the claimed relationship.
It is not a disposition. Time-adjacent upstream commits are context, not proof
that those exact bytes were used historically.

Content-manifest digests below are SHA-256 over a relative-path-sorted stream
of records formatted as `<file-sha256-hex>  <relative-path>\n`. They are
observation digests, not Git tree IDs. A final manifest must additionally bind
file mode, symlink type and target, exact captured skill IDs, manifest schema,
and an opaque host profile.

## Candidate source-stage chain

The local-only predecessor object
`31ff1f7915df9038505e94d4b03b3e2c837bdb3c` is the last committed head of the
five-plugin source-stage branch discovered during this research. It is not
available through the public repository API. Commit
[`ddd07d7d1f13658086248e02691a39ca3ce2d295`](https://github.com/nisavid/agents/commit/ddd07d7d1f13658086248e02691a39ca3ce2d295)
imports the reviewed successor into the current candidate lineage, but does not
name an immutable external source revision for the imported bytes.

| Distribution | Predecessor subtree | Imported subtree at `ddd07d7d` | Research snapshot at `b47f0351` | Refreshed candidate at `8ec465ea` | Recorded drift through the refreshed candidate |
| --- | --- | --- | --- | --- | --- |
| Rolecasting | `993557c22d72cfaa54c543f4dcee323e2517ba8e` | `d4cdbaee1642cc0c388e49410e5332e3a74be1d1` | `d4cdbaee1642cc0c388e49410e5332e3a74be1d1` | `d4cdbaee1642cc0c388e49410e5332e3a74be1d1` | None in the package subtree |
| Tricritical | `d1e5482fac3a17bffc9c4d20c4c3ad047ecc5b16` | `1b09c3a5b5a5c46b52c59e3328275c5615c54573` | `db58756a31a98fc8be81a226230a1eb9cef02df6` | `db58756a31a98fc8be81a226230a1eb9cef02df6` | Shared skill resources were projected after import |
| Versionkeeping | `5674ccdad4ed56165bea349d3e2151172d1f8741` | `0c6e2c27dcc4d7dabb78e2a756ff5bc4d9f1dfe7` | `f95e14a276c6586874ba48c91ccc9fd34f62d924` | `c46a89caa6a6bc842fe69eb2ff0037df1d75832c` | Publication safety and closed Git-operation ownership changed after the baseline |
| Mergecraft | `2f905f0d9544d29811ab8951d935a8ce7d10d277` | `5c0eeaeba279764a9e3bbe165deb45c00eebde7b` | `16051c291e314a69bc34cfbcdd48de98aceb2355` | `82ef6a75c03d2794ea6c792dc348903a9ddfe51c` | Publication, review, identity, and evidence boundaries changed after the baseline |
| Artifact Customs | `1df6df911ee3744e498db4144b67408bd929f5af` | `f874b4d7ee36c49ac09098fcacf7fa9c2601d1e4` | `f874b4d7ee36c49ac09098fcacf7fa9c2601d1e4` | `f874b4d7ee36c49ac09098fcacf7fa9c2601d1e4` | None in the package subtree |
| Task Witness | absent | `eac00106ab6a1b3ae6a1bbffd5e7904083d5199b` | `eac00106ab6a1b3ae6a1bbffd5e7904083d5199b` | `4ce4650fe524dfa817e715715bd7528639e0de04` | Qualification, capture, and deployment-control behavior changed after the initial implementation |

The five predecessor-to-import deltas are material: 27 Rolecasting files, 12
Tricritical files, 5 Versionkeeping files, 14 Mergecraft files, and 16 Artifact
Customs files changed. The imported commit is therefore an immutable candidate
baseline, but not a complete source-lineage receipt.

All six root manifests currently declare version `1.0.0`, repository
`https://github.com/nisavid/agents`, author Ivan D Vasin, and MIT. The five
prompt-bearing packages contain the same license bytes,
`sha256:68950fb14202e1a28da07612a6cc6978163fee3e678520c2740a0d1bde0680e8`.
Task Witness has no package-local license file; its manifest points to MIT and
the same digest is the repository-level license boundary.

## Source-family records

### Superpowers

Primary source: [obra/superpowers](https://github.com/obra/superpowers), MIT,
Copyright Jesse Vincent.

| Identity | Revision | Skill-tree identity | Evidence |
| --- | --- | --- | --- |
| Time-adjacent upstream before the first five-plugin source-stage commits | [`v6.2.0` / `3dcbd5c4b48e02263fbf4a3c01e3fe4f81d584d9`](https://github.com/obra/superpowers/commit/3dcbd5c4b48e02263fbf4a3c01e3fe4f81d584d9) | `9a5bbc376a639d0ea24e2989d2f5eba754638adf` | Context only; the candidate does not cite it |
| Installed lock baseline | [`44c9b2d6e889982ac18c27d05a19fefe335194e1`](https://github.com/obra/superpowers/commit/44c9b2d6e889982ac18c27d05a19fefe335194e1) | `9a5bbc376a639d0ea24e2989d2f5eba754638adf` | Pathless macOS lock resolves all 14 skills |
| Current upstream and current tagged release | [`v6.3.0` / `b36e0829c6d0140e93cfef2ca599b1b07d4a7797`](https://github.com/obra/superpowers/commit/b36e0829c6d0140e93cfef2ca599b1b07d4a7797) | `9483dd00b0bda60c6ea631d88900f0436630d064` | Tag and default branch resolve to the same commit |
| License blob | `abf0390320aa14406af7a520b9b0739fdda9bf08` | — | [MIT license](https://github.com/obra/superpowers/blob/b36e0829c6d0140e93cfef2ca599b1b07d4a7797/LICENSE) |

Upstream v6.3.0 is two commits ahead of v6.2.0 and one commit ahead of the
installed lock revision. Seven of the 14 installed skill directories changed;
the changed entrypoints are `brainstorming`, `finishing-a-development-branch`,
`subagent-driven-development`, `using-superpowers`, and `writing-plans`, with
supporting-file drift in two more directories.

One inspected macOS source-skill root contains all 14 Superpowers entrypoints
byte-identical to v6.2.0. Its 50-file observed manifest digest is
`sha256:11bf29558e6bc2e1a6eef2eddcae46c0fc55dfc3aa9bfd50bd322905d1fec89c`.
The pathless host manifest's mode-and-length-bound tree identity is
`sha256:e2480fa0bdca458a5392725b98160e5f703e422b5f97fcd54baaeb5bb4ea6b2c`
and matches the installed-lock baseline.
The same environment also contains a separately packaged Codex cache whose
manifest declares version `5.1.3` and the same upstream repository. Its 66
files are byte-identical to
[`openai/plugins@11c74d6ba24d3a6d48f54a194cd00ef3beea18f9`](https://github.com/openai/plugins/tree/11c74d6ba24d3a6d48f54a194cd00ef3beea18f9/plugins/superpowers),
subtree `22fc00f98286ed6a3c300682df47a20170cf4802`, with observed package
digest
`sha256:33f8b9339a976c13b09086108a4c78fe5712e2ee4f9eef85f26f1d72b180da3a`.
Its 60 skill files have pathless tree identity
`sha256:c2e66ef67db332857392cb82c2b6bd422f777451818fce8fb57d333e212f317e`
and do not match the standalone baseline.
That public package's most recent upstream-named sync commit
[`cc8b22955285a060a50d33b594c66db1e61c24c0`](https://github.com/openai/plugins/commit/cc8b22955285a060a50d33b594c66db1e61c24c0)
says it updates obra/superpowers to `v5.1.0`; the annotated upstream tag resolves
to commit `f2cbfbefebbfef77321e4c9abc9e949826bea9d7`, tree
`6c1ddfb571b84a5debf765c942c233124aeb08a1`. The public package also contains
Codex adapters and later metadata changes. Exact per-file
upstream-to-package mapping remains
`unresolved`. The two observed discovery-source byte sets do not establish
which route is active for each harness.

No repository artifact maps a Superpowers skill or contribution to a specific
distribution owner. Every such contribution mapping remains `unresolved`.

### Matt Pocock skills

Primary source: [mattpocock/skills](https://github.com/mattpocock/skills), MIT.

| Identity | Revision | Skill-tree identity | Evidence |
| --- | --- | --- | --- |
| Time-adjacent upstream before the first five-plugin source-stage commits | [`17f22a371b664caa1fc0dd53cc8f0d4ea0e9ef25`](https://github.com/mattpocock/skills/commit/17f22a371b664caa1fc0dd53cc8f0d4ea0e9ef25) | `8d3b24d50dfa4f9a654b6fddb5daf59d19792190` | Context only; the candidate does not cite it |
| Installed lock baseline | [`84fdeffd12f2ee307994d1eb6feb48173b6e0502`](https://github.com/mattpocock/skills/commit/84fdeffd12f2ee307994d1eb6feb48173b6e0502) | `5d76a9b8320eab13d8fc71037df154053e7fb3f7` | Pathless macOS lock resolves all 25 skills |
| Current upstream | [`9c9f36ccd3995266cd675468af71639c8dde1ec5`](https://github.com/mattpocock/skills/commit/9c9f36ccd3995266cd675468af71639c8dde1ec5) | `0e082b060e7d1f5e5f9e6b3be9418658920f5672` | Default branch at research time |
| License blob | `f1dd2c09108dde1a5f56097cee8461b3ea834499` | — | [MIT license](https://github.com/mattpocock/skills/blob/9c9f36ccd3995266cd675468af71639c8dde1ec5/LICENSE) |

Current upstream is 123 commits ahead of the time-adjacent snapshot and 19
commits ahead of the installed lock revision. The inspected macOS source-skill
root contains 25 locked Matt skill directories and 74 files, with observed
manifest digest
`sha256:fad1b1b1dee19260bd838f1574e979f400a3b7f79d8eecd7a80695f66d36579a`.
The pathless host manifest records mode-and-length-bound installed tree
`sha256:1c3bec9cd294a29a5365850caac7948de1b0bc78d992b535e8db780123242f04`;
the complete installed tree matches neither the lock baseline nor current
upstream. The lock still binds each installed skill to the baseline revision.
Twelve directories are byte-identical to current upstream: `ask-matt`,
`codebase-design`, `implement`, `prototype`, `research`,
`resolving-merge-conflicts`, `setup-matt-pocock-skills`, `wizard`, `teach`,
`to-questionnaire`, `wait-what`, and `writing-for-agents`. The remaining 13
intersections drift from current upstream.

The locally observed `code-review/SKILL.md` is exact to upstream commit
[`c0d69015e0cc8b66715beb3f93f9e53256e20f30`](https://github.com/mattpocock/skills/commit/c0d69015e0cc8b66715beb3f93f9e53256e20f30),
with SHA-256
`9cf46653dd9c710ea1e6c22423caf31a794c88773bc94bdaa23140277f470442`.
Current upstream SHA-256 is
`4e8eeefbf3bd32ec2c911ed6c7db103866ce11fedc98b1f8e358d2e1cada3895`;
the semantic delta is one instruction changing direct invocation of the setup
skill into a request that the user invoke it.

No candidate artifact binds Matt's `code-review` or any other Matt contribution
to Tricritical or another distribution. That relationship remains
`unresolved`, as required by [#50](https://github.com/nisavid/agents/issues/50).

### Ivan-authored source skills

The repository directly supports same-name lineage for these source families:

| Destination | Captured macOS installation membership |
| --- | --- |
| Rolecasting (2/2 registry skills) | `choosing-agent-models`, `delegating-cross-agent-work` |
| Versionkeeping (4/4) | `checkpointing-and-publishing-git-work`, `resolving-merge-conflicts`, `syncing-forks-with-upstream`, `using-persistent-git-worktrees` |
| Mergecraft (7/9) | `getting-prs-merged`, `getting-prs-ready-for-review`, `graphite`, `publishing-reviewable-prs`, `resuming-reviewed-prs`, `stacking-pr-fixups`, `writing-reviewable-pr-descriptions` |

The inspected macOS source-skill roots contain 80 files with observed manifest
digest
`sha256:f6070312c7d2f19385ae69d6b13c4184bb2b1ecb192ba2543b88c0f01d5861ae`.
All 13 same-name `SKILL.md` entrypoints differ from the corresponding candidate
package entrypoints. The macOS installed-host manifest binds those exact IDs
and bytes without exposing their paths. It explicitly records the other two
Mergecraft registry skills, `addressing-pr-review-feedback` and
`interacting-with-pr-review-feedback`, as unobserved. No immutable original
source revision or contribution-level semantic map establishes the captured
roots' relationship to the candidate packages. Same-name ownership is evidence
of a likely relationship; the three grouped contribution relationships
therefore remain `unresolved`. The nonempty Mergecraft unobserved set also keeps
`initial-work-macos-v1:ivan-mergecraft` in the reconciliation receipt's
machine-readable unresolved host frontier.

Artifact Customs and Task Witness begin as Ivan-authored repository commits
[`998fe484`](https://github.com/nisavid/agents/commit/998fe484208934554845cdcda6727c486fbc7467)
and
[`fcae9372`](https://github.com/nisavid/agents/commit/fcae93729ede8e21e1bdc1c774ff3e1d1e31e40b).
No external source-skill attribution for either distribution was found in the
candidate. Task Witness is code-only and exposes no portable Agent Skill.

### Graphite retained specialist

Primary source: [withgraphite/agent-skills](https://github.com/withgraphite/agent-skills).
The macOS lock and current upstream both resolve to commit
[`df3c9a36ec90af0b78df991608b3700b53e38ee5`](https://github.com/withgraphite/agent-skills/commit/df3c9a36ec90af0b78df991608b3700b53e38ee5),
root tree `a6c10f8772e760c770d1617de64e7f1f917a3e24`, skills tree
`22a8c483deab5cbc33210f5b07e00a4312232426`. There is no upstream revision
drift.

The observed operator-managed `graphite/SKILL.md` has SHA-256
`49f5b65c89b8fee318b2736a09a5f5bbc8122f557605a29245b033072eebaf23`;
its pathless host tree identity is
`sha256:706c612c1402ae82a66cc5e7390473647476961eeae70e1aed80d78c27588f7f`.
The
[upstream blob](https://github.com/withgraphite/agent-skills/blob/df3c9a36ec90af0b78df991608b3700b53e38ee5/skills/graphite/SKILL.md)
`b1020e57dad24642a12e1e72b3da7fb5081f1be1` has SHA-256
`47297f3556c3932fda12efdc6981910210e7d7ca5295fba7babc33467c6082ad`.
The locked upstream revision is stable, but the managed entrypoint bytes are
adapted. Upstream exposes no license file or detected license, so the license
boundary and contribution-level mapping to Mergecraft remain `unresolved`.

### Other installed source authorities

The macOS inventory also includes skills whose manager metadata names the
following public authorities. No candidate file or contribution ledger found in
this research binds them to a material contribution to the six distributions.
Their installed baselines and destination mappings remain `unresolved`; the
current public snapshots are recorded only to make a final refresh reproducible.
The table shows each repository tree for review context. The machine-readable
manifest binds the narrower immutable `skills` subtree used by each source set,
including its mode-aware SHA-256, entry count, and byte count.

| Authority | Current revision | Repository tree | License boundary |
| --- | --- | --- | --- |
| [firecrawl/cli](https://github.com/firecrawl/cli) | [`870979ed9bfa2d7a26e411c4f6b27381e147ade3`](https://github.com/firecrawl/cli/commit/870979ed9bfa2d7a26e411c4f6b27381e147ade3) | `9f63b698a0c521f58d1bf798524a96ebad7e3a47` | No root license detected |
| [firecrawl/skills](https://github.com/firecrawl/skills) | [`798926666a9a89645f0d618ab41d847f55fe3144`](https://github.com/firecrawl/skills/commit/798926666a9a89645f0d618ab41d847f55fe3144) | `980e57f4d35500d2c9f4cb79406c03c244153645` | [ISC](https://github.com/firecrawl/skills/blob/798926666a9a89645f0d618ab41d847f55fe3144/LICENSE); blob `49f8600931998aa1a104ee3ecdf4724066573f65` |
| [firecrawl/firecrawl-workflows](https://github.com/firecrawl/firecrawl-workflows) | [`18c0185c16f7a714d0a37b65149ca0f09b13e0e8`](https://github.com/firecrawl/firecrawl-workflows/commit/18c0185c16f7a714d0a37b65149ca0f09b13e0e8) | `cf16943f5cf49d663c3a74d479fe731e04492681` | [ISC](https://github.com/firecrawl/firecrawl-workflows/blob/18c0185c16f7a714d0a37b65149ca0f09b13e0e8/LICENSE); blob `ee221a2151d63ddc1db9857f9c5d346a3de9d816` |
| [heredotnow/skill](https://github.com/heredotnow/skill) | [`8ec223e927bb2247636aa0653536f63385f05298`](https://github.com/heredotnow/skill/commit/8ec223e927bb2247636aa0653536f63385f05298) | `53d2d4ef6383022808585f928c3487d094c2a7b4` | No root license detected |
| [heygen-com/hyperframes](https://github.com/heygen-com/hyperframes) | [`0d874adc685a94a519311cf9ed9e4107bb0347c9`](https://github.com/heygen-com/hyperframes/commit/0d874adc685a94a519311cf9ed9e4107bb0347c9) | `3e5dc2564a39cbcd49c57dbd43ceaf8f57995a3c` | [Apache-2.0](https://github.com/heygen-com/hyperframes/blob/0d874adc685a94a519311cf9ed9e4107bb0347c9/LICENSE); blob `ae06b37e1c5116ccfaa615ac25ec1aabf5658d8c` |
| [vectorize-io/hindsight](https://github.com/vectorize-io/hindsight) | [`997f27f1bf8cd52aede4a78d84eb0a0580c95ea5`](https://github.com/vectorize-io/hindsight/commit/997f27f1bf8cd52aede4a78d84eb0a0580c95ea5) | `c3bd39cf4ce26048c4537ded791bf505044c6e8a` | [MIT](https://github.com/vectorize-io/hindsight/blob/997f27f1bf8cd52aede4a78d84eb0a0580c95ea5/LICENSE); blob `8dce7b4d4c2632990bdf8b4101694ea19ba6ad46` |
| [vercel-labs/skills](https://github.com/vercel-labs/skills) | [`c6f69c631292444cc541ac6d91e2226b0ff247da`](https://github.com/vercel-labs/skills/commit/c6f69c631292444cc541ac6d91e2226b0ff247da) | `c28acf1413ed931a92fb8a4d7f1810513bac3ed8` | [MIT](https://github.com/vercel-labs/skills/blob/c6f69c631292444cc541ac6d91e2226b0ff247da/LICENSE); blob `eabe3008f6c0275929faa8b07d406e82717648cc` |
| [obra/the-elements-of-style](https://github.com/obra/the-elements-of-style) | [`05fc4f0d2b97b7c042dd9949ad658568e4a1324e`](https://github.com/obra/the-elements-of-style/commit/05fc4f0d2b97b7c042dd9949ad658568e4a1324e) | `13bb9a9a6d943f2fba80c5c449a9acd6ea1fd9e1` | No root license detected |

### Cursor Thermos

Tricritical's `NOTICE` explicitly says its review-workflow concepts derive from
the retired Thermos plugin, originally Copyright 2026 Cursor under MIT. The
ported Thermos README names
[cursor/plugins/thermos](https://github.com/cursor/plugins/tree/dc415439eebed0cd8fddb11e896dee43bce9d8b0/thermos)
as upstream.

The latest upstream commit touching `thermos/` is
[`dc415439eebed0cd8fddb11e896dee43bce9d8b0`](https://github.com/cursor/plugins/commit/dc415439eebed0cd8fddb11e896dee43bce9d8b0),
tree `82dd7121447abad1c551e5d6ba829de3c6f3e4c4`; that subtree is unchanged at
current `cursor/plugins` main,
[`c7f203457c16815379130697c41e74fd202e9978`](https://github.com/cursor/plugins/commit/c7f203457c16815379130697c41e74fd202e9978).
Its MIT license blob is
`ca2bba771cd39dbef6acf96b52481133983451f3`.

The local port starts at repository commit
[`4d5f5668`](https://github.com/nisavid/agents/commit/4d5f56686b514267e1506383f4841cfd7498c055)
and reaches version `1.0.4` at
[`30ee02a3`](https://github.com/nisavid/agents/commit/30ee02a3c5b67dfe52c25bff640104ad0350be03).
An inspected Codex Thermos cache contains exactly the 16 files and bytes from
`plugins/thermos/` at `30ee02a3`; its observed manifest digest is
`sha256:4149a14125cf536b78977d0a75503d58dcaf7ab64781eb4f1674d8a8eb00904b`.
Its eight skill files have pathless host tree identity
`sha256:9d5ee3c3279068b5182235d6f7bc2482174a89e18f71d778775baa3806c9955e`.

Tricritical first appears at
[`7e6db63f`](https://github.com/nisavid/agents/commit/7e6db63f31b7160241b3de9c17ab363282feb1a6),
separating independent observation, adjudication, authorized revision, and
iteration. The current notice proves family-level lineage. It does not map
individual Thermos rules or files to individual Tricritical contributions;
those mappings remain `unresolved`.

### OpenAI GitHub plugin and Review Atlas

`release/mergecraft-retirement-contribution-ledger.json` names source skills
`github`, `yeet`, `gh-address-comments`, and specialist `github:gh-fix-ci`, and
maps nine behavior contributions to destination operations. It contains no
source repository revision, source tree, installed content digest, or license
blob.

Two GitHub plugin caches were observed. One 28-file cache is byte-identical to
[`openai/plugins@11c74d6ba24d3a6d48f54a194cd00ef3beea18f9`](https://github.com/openai/plugins/tree/11c74d6ba24d3a6d48f54a194cd00ef3beea18f9/plugins/github),
subtree `5c2b448611eaee12cb93323684f8461fa4ab78e2`, with manifest version
`0.1.6` and observed digest
`sha256:0a1f889f6f4b7475f5b0fd79637e61acebba59faf16b98af3336f78da7184b6b`.
The other declares version `0.1.8-2841cf9749ae` and has 26 files with
observed digest
`sha256:2e09b59c3416016dde737fbd0287ff32a4583e87a0e3e6c3a271578481deaa55`.
Its suffix `2841cf9749ae` does not resolve as a public-repository commit.

The pathless host manifest shows that the two caches' 16 retained-specialist
files (`github`, `gh-address-comments`, and `gh-fix-ci`) are byte-identical,
with tree identity
`sha256:da22621dee80972a82c165c8f1f3d9bd98c11d4833521af3460fb845902cbdc8`.
Their five-file `yeet` subtrees differ: the versioned cache is
`sha256:8544f1b3d63a81bf18201622d026123b1cd0b29817aeeb8ce158e631a82db375`
and the public-commit cache is
`sha256:d83dcfd5c102dfcf264092f7998d5f68ceb035b516ad0c3e2c66f6b9083545fd`.

Both manifests declare MIT, but the public package has no package-root license.
The embedded licenses for
[`gh-address-comments`](https://github.com/openai/plugins/blob/11c74d6ba24d3a6d48f54a194cd00ef3beea18f9/plugins/github/skills/gh-address-comments/LICENSE.txt),
[`gh-fix-ci`](https://github.com/openai/plugins/blob/11c74d6ba24d3a6d48f54a194cd00ef3beea18f9/plugins/github/skills/gh-fix-ci/LICENSE.txt),
and
[`yeet`](https://github.com/openai/plugins/blob/11c74d6ba24d3a6d48f54a194cd00ef3beea18f9/plugins/github/skills/yeet/LICENSE.txt)
are Apache-2.0 blobs
`7a4a3ea2424c09fbe48d455aed1eaa94d9124835` and
`13e25df86ce06eb6488e6a6bc5c5847f5dedc352`. The ledger does not bind either
cache identity. Its selected package revision and package-level license
boundary therefore remain `unresolved`.

`release/mergecraft/review-atlas-contribution-ledger.json` maps public-core and
private-overlay sections, but identifies its source only by a 399-line count.
Its original source revision and content digest remain `unresolved`.

### Packaging specifications

These sources govern package shape, not distribution behavior:

| Authority | Current revision | Tree | License boundary |
| --- | --- | --- | --- |
| [Agent Plugins v1.0.0](https://github.com/agentplugins/agent-plugins-spec) | [`bd383552095128f6effe895b9257cfd580a6d179`](https://github.com/agentplugins/agent-plugins-spec/commit/bd383552095128f6effe895b9257cfd580a6d179) | `5acca111ff951b5dce38c0c5707225cbfc7594cf` | Specification/docs CC-BY-4.0; schemas/code Apache-2.0; license blob `b1c2f51d0884b9d5b04c960e5726ebd19b8565f4` |
| [Agent Skills](https://github.com/agentskills/agentskills) | [`69ef37e9424c0a7ea9dd2293b559e43ec8176379`](https://github.com/agentskills/agentskills/commit/69ef37e9424c0a7ea9dd2293b559e43ec8176379) | `65e11c9faad14a022055ce0ff3ebf99f2b55142f` | Apache-2.0; license blob `a20f4476df158a57a68409015ea607c738856f57` |

The repository's prior package-format research also pins Codex loader evidence
to `openai/codex` commit `74004b5397b24662a87a5264a6ae80664168c7f3`.
These format and loader sources must not be represented as behavior
contributions without a separate mapping.

## Distribution evidence matrix

| Distribution | Factual source evidence | Missing evidence |
| --- | --- | --- |
| Rolecasting | Validator-bound 2/2 same-name Ivan installation membership; immutable precursor, imported, and candidate trees | Contribution mapping for Superpowers delegation or other external concepts; authoritative current source revision; CachyOS inventory and final macOS activation receipt |
| Tricritical | Explicit Thermos family attribution and license; immutable Thermos, initial Tricritical, imported, and candidate trees | Rule-level Thermos mapping; Matt `code-review` relationship; Superpowers review-skill mapping; CachyOS inventory and final macOS activation receipt |
| Versionkeeping | Validator-bound 4/4 same-name Ivan installation membership; immutable precursor, imported, and candidate trees; an existing ledger names one GitHub-plugin contribution | Contribution-level source revisions and licenses; Superpowers worktree/finish-skill mapping; CachyOS inventory and final macOS activation receipt |
| Mergecraft | Validator-bound 7/9 same-name Ivan installation membership with two registry skills explicitly unobserved; existing GitHub-skill and Review Atlas ledgers; immutable precursor, imported, and candidate trees | Ledger-to-GitHub-cache binding and legacy cache identity; original Review Atlas source identity; Graphite license and adapted-byte mapping; Superpowers review-skill mapping; CachyOS inventory and final macOS activation receipt |
| Artifact Customs | Ivan-authored initial, precursor, imported, and candidate identities | Any external source-skill contribution map; authoritative current source revision; CachyOS inventory and final macOS activation receipt |
| Task Witness | Ivan-authored code-only initial, imported, and candidate identities; macOS source-route absence | Any external contribution map; CachyOS inventory and final release installation evidence |

## Installed-host evidence and privacy

The audit uses opaque profile IDs without publishing machine paths, hostnames,
or network identifiers. These are research receipts; issue
[#46](https://github.com/nisavid/agents/issues/46) still owns final installed-host
qualification.

| Host evidence | State |
| --- | --- |
| `initial-work-macos-v1` | A pathless inventory records 119 standalone skills, digest `sha256:d78dced0e85ff08f436964aa7ce5aeee381fca9c7804093ad87851a3a1d538e7`; the v3 manager lock's raw digest is `sha256:6b17e94f4bc1f55fa8597f999147a850bfa9700f8d593d9c9b35fb0983c5096d`. It resolves 25/25 Matt skills to `84fdeffd`, 14/14 Superpowers skills to `44c9b2d6`, and Graphite to `df3c9a36`. The grouped Ivan observations bind exact 7/9 Mergecraft, 2/2 Rolecasting, and 4/4 Versionkeeping route presence. Two pathless mode-, length-, and membership-bound observations were stable at `2026-08-18T05:17:05Z`; active discovery precedence was not observed and remains `unresolved` as `initial-work-macos-v1:discovery-precedence`. |
| `initial-personal-cachyos-v1` | Trusted transport was unavailable because its route was stopped. No remote route-presence facts were sampled, and active discovery precedence remains `unresolved` as `initial-personal-cachyos-v1:discovery-precedence`. |
| Six coordinated distributions | No coordinated plugin bundle was present in the macOS source-skill/cache surfaces. Three distributions have grouped same-name standalone source routes; the other three are absent there. CachyOS bundle and route presence remain `unresolved`. Reconciliation derives this unresolved frontier from the two host receipts; it does not assert active selection, release activation, or release eligibility. |

Public artifacts may contain repository URLs, immutable revisions and trees,
license identifiers and blobs, relative package paths, content digests,
semantic summaries, contribution IDs, destination owners, opaque host profile
IDs, domain-separated route digests, source IDs, and canonical public skill
IDs. Keep machine-local
absolute paths, usernames, hostnames, device
identifiers, manager-registry paths, SSH configuration, credentials, private
source names, and raw private manifests outside the public repository. Bind any
private evidence by opaque authority ID and digest only.

## Reproducible final refresh

1. Freeze the candidate commit and tree, ledger schema, source registry, and
   opaque host-profile definitions before reading any source.
2. For every public Git source, resolve the selected ref to a full commit and
   tree through the owning forge API. Record the repository, ref, commit, tree,
   retrieval date, and exact license blob. Never treat a tag, package version,
   cache directory name, or abbreviated suffix as a commit without resolution.
3. Materialize each relevant source subtree from that commit. Emit a canonical
   manifest of relative path, object type, mode, byte length, and SHA-256, then
   hash the canonical manifest. Reject symlink escapes, duplicate normalized
   paths, unsupported file types, and read-time drift. Represent a safe symlink
   as Git mode `120000`, with its bounded relative UTF-8 target supplying the
   entry length and digest.
4. Resolve each repository-owned baseline and destination with Git object
   reads, not working-tree bytes. Bind the predecessor, import commit, final
   candidate, package subtree, source skill, contribution ID, destination
   owner, license, and semantic change summary. Leave uncertain fields
   `unresolved`.
5. On each host, have the trusted deployment controller capture the selected
   source-skill and plugin roots under its opaque host profile. Publish each
   installation's sorted, unique skill IDs, the exhaustive unobserved-ID
   remainder, and the pathless manifest projection and digest; retain raw paths
   and manager state privately. Capture active discovery precedence separately
   from mere file presence.
6. Repeat source-ref resolution, source manifests, candidate identities, and
   installed-host route manifests after the first pass. Accept the public route
   snapshot only if both passes agree byte-for-byte on membership and content.
   `capture-host` does not observe or attest private manager-registry state;
   binding that state to the two passes is deferred to issue
   [#53](https://github.com/nisavid/agents/issues/53).
7. Validate that every ledger source points to an immutable source manifest,
   every installed observation points to one host manifest and exhaustively
   partitions its registry IDs into observed and unobserved membership, every
   contribution names a destination owner or `unresolved`, and no public
   artifact contains a forbidden local identifier.
8. Rerun the entire procedure at the last responsible moment defined by issue
   [#50](https://github.com/nisavid/agents/issues/50). If any source,
   contribution mapping, candidate byte, license boundary, installed skill-ID
   membership, installed identity, or discovery route changes, record drift and
   regenerate every downstream artifact required by the coordinated release
   contract. This report does not decide that invalidation set.

The checked-in refresher makes those observations reproducible without
publishing private paths. `capture-git` accepts an immutable revision and a
repository-relative source root, including `.`. `capture-host` accepts a
private path map and a profile-bound discovery-precedence reason, reads every
selected skill route twice, preserves canonical skill IDs, and emits only the
pathless installed-host document and canonical public precedence template.
It does not assert that private manager-registry or lock state stayed stable.
Validation requires each route's IDs to belong to its source, requires every
installed source's registry skill IDs to be explicitly observed or unobserved,
and keeps route presence distinct from active discovery precedence. `write`
renders twice and validates
the full cross-document set before replacing any checked-in artifact; `check`
proves the checked-in bytes match a fresh render. After the refreshed artifacts
and validator are committed, `receipt` create-new publishes an external record
that binds the exact commit, tree, artifact bytes, validator bytes, and derived
remaining unresolved IDs, including both discovery-precedence components,
while explicitly leaving route activation and release eligibility unasserted.

Checked-in publication keeps the verified, flocked `release` directory open as
the authority for transaction discovery, publication, rollback, recovery, and
retention. Staging is complete and validated before the transaction enters that
directory. Every durable rename and parent-directory fsync is descriptor
relative, moved directory identities are checked through retained descriptors,
and the visible `release` binding is rechecked before mutation and successful
return.

Ephemeral Git materialization and validation assume cooperative execution by
processes sharing the caller's EUID. They reject hostile environment,
repository, and path inputs and observable drift, but do not authenticate
against deliberate same-EUID namespace substitution. This boundary applies to
scratch work only; the external receipt retains the stronger publication
boundary below. The receipt is deterministic research evidence, not an
independent authenticity or release-eligibility assertion.

A receipt parent is accepted only when it is the refresher's canonical
root-owned sticky system temporary directory, the caller is non-root, and
every containing ancestor is root-owned and denies the caller write access.
That explicit OS-managed anchor prevents a concurrent same-user process from
relocating the opened parent into the checkout or common Git directory before
descriptor-relative publication. If a failure occurs after successful mode
enforcement and the original name remains linked, its exact mode-`0600` leaf
remains for operator disposition. A failure between create-new and mode
enforcement can instead leave a more restrictive empty leaf; publication never
widens it or unlinks a possibly replaced name.

## Blocking unresolved fields

- immutable source identity for the bytes imported by `ddd07d7d`;
- contribution-level Superpowers and Matt-to-distribution mappings;
- the Matt `code-review` to Tricritical relationship;
- rule-level Cursor Thermos to Tricritical mapping;
- ledger binding for `openai-github-specialists` and `yeet` to one observed
  OpenAI GitHub cache, plus the legacy cache's immutable public revision and
  package-level license boundary;
- original `review-atlas-private` source revision and content digest;
- immutable baselines and contribution mappings for the current-only
  `firecrawl-cli`, `firecrawl-skill-set`, `firecrawl-workflows`,
  `heredotnow-here-now`, `heygen-hyperframes`, `obra-elements-of-style`,
  `vectorize-hindsight`, and `vercel-labs-skill-set` authorities;
- immutable current revisions for the observed Ivan-authored source-skill
  roots and contribution mappings for
  `mergecraft-installed-source-relationship-unresolved`,
  `rolecasting-installed-source-relationship-unresolved`, and
  `versionkeeping-installed-source-relationship-unresolved`;
- `withgraphite-agent-skills` license boundary and mapping from its stable
  upstream revision to the adapted installed entrypoint;
- the two unobserved macOS Mergecraft registry skills bound by
  `initial-work-macos-v1:ivan-mergecraft`;
- the macOS active discovery-precedence receipt;
- all CachyOS installed-byte and route-presence observations plus its active
  discovery-precedence receipt; and
- the final post-reconciliation candidate identity.

These unresolved fields are explicit inputs to issue #50 rather than inferred
choices. The checked-in inventory is sufficient to expose that decision
frontier, but the unresolved fields still block final candidate qualification.
