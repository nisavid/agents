# Agent setup output inventory: nisavid repos (2026-09-26)

Sources: `gh api repos/nisavid/<r>/git/trees/<sha>` and `contents/<path>?ref=<sha>` against each default-branch SHA listed below, `gh label list --limit 200`, `gh api repos/nisavid/<r>`, `gh pr view`, and gh 2.97.0 `--help`. Mark: **V** = verified by reading the source; **I** = inferred. Everything is V unless marked I. `fathomwork` is private, so this report gives only presence and defect flags for it.

## Summary

Sixteen repos have root agent instructions on the default branch: the 15 named repos (for `agents`, only through PR #123) plus `systools`, which has an `AGENTS.md` but no setup block. Six carry no `## Operating Policy`: dotfiles, codiquary, sacrysty, chatgpt-linux, arch-pkgs and arch-strix-halo-pkgs (systools also lacks one). Where the section exists, it is byte-identical to provingkit's copy, except that fork-ops adds two bullets.

The PRs that remove personal Git identity and branch-prefix text are still **open** for dotfiles (#360), provingkit (#235), codiquary (#45) and sacrysty (#27), so those default branches still carry `Ivan D Vasin` and the `ivan/` prefix. Only cqmgr#114 has merged (2026-09-26T22:22Z). dotfiles#362, which adds an identical Operating Policy, is also open.

No tracker doc is clean on D1–D5:
- **provingkit, agents#123 and dotfiles** fix D1, D3, D4 and D5 and mitigate the Resolve race, but they still carry D2 and do not name the repository in their commands.
- **arch-pkgs and arch-strix-halo-pkgs** fix D2. However, they close a ticket before appending to the map and put angle-bracket placeholders inside commands.
- **The `--repo`-qualified docs** (cqmgr, mastic, computer-use-linux, lemonade, fork-ops, chatgpt-linux) still carry D1 and D3.

Only two repos have label gaps: `agents` is missing 4 of the 5 canonical labels, and `dotfiles` is missing `needs-info`. No repo exposes the auto-close setting through the API.

**Best-known variants (I):**
- **Operating Policy:** provingkit's copy.
- **Git section:** the shape in provingkit#235 and agents#123: Conventional Commits, the `cog install-hook --all` sentence, `git diff --check` and repository validation, with no personal identity, prefix, checkpointing or worktree rules.
- **Tracker:** a combination: provingkit's text, arch-pkgs's D2 fix, and `--repo` on every command.

## 1. Files, sections, fork status (items 1–3, 6, 7)

**Git codes:**
- **CC:** Conventional Commits.
- **DCO:** `--signoff`.
- **dc:** `git diff --check`.
- **val:** repository validation commands.
- **cog:** the `cog install-hook --all` sentence.
- **hk:** `cog.toml` defines `[git_hooks.commit-msg]` and `[git_hooks.pre-push]`.
- **id:** personal identity or GitHub account.
- **pfx:** the `ivan/` prefix.
- **ck:** `checkpointing-and-publishing-git-work`.
- **wt:** the `.wt/` worktree layout.

No repo uses a `nisavid/` prefix.

**Other abbreviations:**
- **A:** AGENTS.md is present.
- **C→A:** CLAUDE.md is a symlink to AGENTS.md (mode 120000).
- **OP:** `## Operating Policy`.
- **Auto-close:** "n/e" means not exposed. No key matching `close` appears in the REST repo object for any of these repos, and the GraphQL `Repository` type has no such field; the only near matches are `autoMergeAllowed`, `contactLinks` and `fundingLinks`.

Every repo that has docs/agents has issue-tracker, triage-labels and domain; the column lists only the extra files.

| Repo @ SHA | Instr | `## Agent skills` | OP | Git section | Other docs/agents | CONTEXT / ADRs | Fork | Auto-close |
|---|---|---|---|---|---|---|---|---|
| dotfiles @4a5710c492 | A; C→A | ✓ | ✗ (#362 open adds an identical copy) | "Git and validation": CC dc val hk id pfx ck wt (chezmoi-specific `.wt/`); no cog sentence | hook-free-pr-publication | CONTEXT-MAP / 1 | no | n/e |
| provingkit @7ce7f568fc | A | ✓ | ✓ baseline | CC cog hk dc val id pfx ck wt (`<checkout>.wt/`); #235 drops id pfx ck wt | provingkit-test-execution | ✓ / 2 | no | n/e |
| agents main @c60ce86fec | none | ✗ | ✗ | none (hk only) | no docs/agents | ✗ / ✗ | no | n/e |
| agents #123 @8091d2bc59 | A | ✓ | ✓ | CC cog hk dc val | none; tracker, triage and domain byte-identical to provingkit | ✗ / ✗ | no | n/e |
| lemonade @b33524f521 | A; CLAUDE.md is a regular file containing `@AGENTS.md` | ✓ | ✓ | none (only an upstream-style "Contributing" section) | architecture-map, fork-stewardship, profiling-no-target-gtt-noise, research-map | ✓ / 1 | lemonade-sdk/lemonade | n/e |
| fork-ops @546d9ecaf1 | A | ✓ | ≠ (+2 bullets) | "Validation": dc val | fork-ops-32-equipment-migration-case-study, merge-admission, security-exceptions.{md,toml}, validation-evidence | ✓ / ✗ | no | n/e |
| computer-use-linux @e240312a67 | A; C→A | ✓ (+Fork stewardship) | ✓ | none | fork-stewardship | ✗ / ✗ | agent-sh/computer-use-linux | n/e |
| chatgpt-linux @200f226587 | A | ✗ | ✗ | "Git And Pull Requests" and "Validation": CC ck dc val; `--repo` rule | generated-and-runtime-notes, repository-map, validation-playbook | ✓ / ✗ | ilysenko/codex-desktop-linux; **archived** | n/e |
| agent-armory @bfbf70f68a | A | ✓ (+Reflection findings) | ✓ | "Commit and PR Policy": CC only | none | ✓ / 22 | no | n/e |
| fathomwork @6631b36144 (private) | A | ✓ | ✓ | none | none | ✓ / 5 | no | n/e |
| codiquary @266dd2b3d5 | A | ✗ (flat `## Issue tracker`) | ✗ | CC DCO dc val hk id pfx; no cog sentence; #45 drops id pfx | none | ✓ / 1 + README | no | n/e |
| sacrysty @c4c0c2c968 | A | ✗ (flat `## Issue tracker`) | ✗ | CC DCO dc val hk id pfx; #27 drops id pfx | dco-provisioning | ✓ / 1 + README | no | n/e |
| arch-strix-halo-pkgs @9c2a53c911 | A | ✓ (#155) | ✗ | no Git heading; "Follow These Rules" has CC, topic branch in a separate worktree, and the PR flow | none | ✗ / ✗ | no | n/e |
| arch-pkgs @7aeab8bd0f | A | ✓ | ✗ | no Git heading; "Rules" has CC; "Verification" has makepkg checks | none | ✗ / ✗ | no | n/e |
| mastic @07a615418b | A (30 lines, no H1); C→A | ✓ | ✓ | none, although hk is present | none | ✓ / 6 | no | n/e |
| cqmgr @db18e9b4c7 (after #114) | A; C→A | ✓ | ✓ | CC dc; hk present but no cog sentence; no val | none | ✓ / 5 | no | n/e |
| systools @95c686b8ab (extra) | A; C→A | ✗ | ✗ | "Validate the owning subproject": CC hk dc val | no docs/agents | CONTEXT-MAP / ✗ | no | n/e |

**Operating Policy comparison.** Each section runs from its `## Operating Policy` line to the next H1 or H2, or to the end of the file, with trailing newlines collapsed to one.
- provingkit's section, with 10 bullets, has sha256 `a58e36e9360022dd4728f4151ca3306a63ff6eb282c00566be038d32ff017dd5`.
- The same hash appears in agent-armory, agents#123, computer-use-linux, cqmgr, fathomwork, lemonade, mastic, and the dotfiles#362 and provingkit#235 heads.
- fork-ops has sha256 `b9257c0a5e6c00783451bb4d3b01a71f010dcc8c102b3fec4e8ec03e95d8c8b2`. It is identical through the 10 bullets, then appends two bullets: keep human-facing docs user-first, and do not capitalize `CONTEXT.md` domain terms in prose.

No other `nisavid` repo qualifies. `oai-plugins` has nested `AGENTS.md` files only inside upstream plugin content.

## 2. docs/agents/issue-tracker.md defects (item 4)

| Repo | D1 | D2 | D3 | D4 | D5 | Resolve | Repo-qualified / guard | `## Wayfinding operations` |
|---|---|---|---|---|---|---|---|---|
| provingkit = agents#123 (byte-identical) | fixed | **present** | fixed (`--limit 500`) | fixed (REST `author_association`) | fixed (GraphQL `subIssues`, in map order) | read-modify-write with a `<!-- decision:$n -->` marker and a re-read, then close last; still last-writer-wins (acknowledged in the doc) | no (inferred from the working directory) | ✓ |
| dotfiles | fixed | **present** | fixed | fixed | fixed, but `blockedBy` has no `pageInfo` | same as provingkit | no | ✓ |
| arch-pkgs | fixed | fixed | fixed (`--limit 1000`) | fixed (`gh search prs`) | fixed (REST `sub_issues` with `issue_dependencies_summary`) | close, then append; no read-modify-write | no; `<owner>/<repo>` placeholders | ✓ |
| arch-strix-halo-pkgs | fixed | fixed | fixed | fixed, but omits `FIRST_TIMER` | fixed | close, then append; no read-modify-write | no | ✓ |
| cqmgr, mastic | **present** | prose only | **present** | n/a | prose only | close, then append; no read-modify-write | yes (`--repo`) | ✓ |
| computer-use-linux | **present** | prose only | **present** | n/a | prose ("first open child") | close, then append; no read-modify-write | yes; upstream `agent-sh` fenced off | ✓ |
| lemonade | **present** | **present** | **present** | n/a | REST `sub_issues` plus per-child `blocked_by` | close, then append; no read-modify-write | yes; upstream only with explicit direction | ✓ |
| fork-ops | **present** | **present** | **present** | n/a | n/a | n/a | yes, plus a pre-mutation `gh repo view` check | ✗ |
| chatgpt-linux | **present** | **present** | **present** | n/a | n/a | creation forbidden (retired) | yes | ✗ |
| codiquary, sacrysty | no commands | – | – | – | prose (map children plus native blocked-by) | comment, update map index, close last; no read-modify-write | n/a | ✗ |
| agent-armory | custom adapter (`tools/issue_tracker_ops.py`, dry-run by default) | – | – | – | – | – | adapter-mediated | ✗ |
| fathomwork | no commands | – | – | – | OK (no command) | no read-modify-write (flag) | ✓ (no command) | ✓ |

The D1 premise is confirmed: in a non-TTY context, `gh issue view --comments` printed only the comment record (`author:` / `association:` …) and no title or body.

**Additional defects (commands quoted):**
- **provingkit, agents#123 and dotfiles:**
  - `gh pr view "$n" --comments` has the D1 problem on PRs.
  - The frontier query runs `gh api graphql -f owner="$owner" -f repo="$repo"`, but `$owner` and `$repo` are never defined. gh 2.97's help says only `-F` fills `{owner}` and `{repo}`.
  - In dotfiles, the prose says to paginate `blockedBy`, but the query omits `pageInfo`.
- **arch-strix-halo-pkgs:** `gh pr view <number> --comments`, and `gh pr comment` / `gh pr close` are given without a number.
- **Angle-bracket placeholders inside commands** appear in cqmgr, mastic, both arch repos, computer-use-linux, lemonade, fork-ops and chatgpt-linux (`<number>`, `<path>`, `<owner>/<repo>`, `<ticket-number>`). provingkit's own conventions section warns against this.
- **fork-ops:** `gh issue view <number> --repo nisavid/fork-ops --comments`, "filtering comments by `jq`", but `--comments` output is not JSON. (I) Its guard `gh repo view nisavid/fork-ops --json nameWithOwner` names the repository it checks, so it only catches renames or redirects.
- **provingkit triage-labels.md** still says "The tracker does not define every label" although all five labels exist. The sentence is accurate for `agents`.

## 3. Triage labels vs live labels (item 5)

**Declared mappings:**
- **Five canonical roles:** every repo maps each canonical role to an identical label string (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix).
- **codiquary and sacrysty:** a bullet list instead of a table.
- **agent-armory:** also declares category labels (`bug`, `enhancement`) and 22 axis labels (`depth:L0–L3`, `kind:*` ×7, `mode:*` ×5, `brief:*` ×4, `dependency:*` ×4), with `config/agent-equipment.toml` as the authority.

**Extra label groups declared in tracker docs:**
- **arch-pkgs:** `wayfinder:seed` and `destination:{deployment,merge,decision}`.
- **arch-strix-halo-pkgs:** `execution:{umbrella,wave,work-unit,incident}`.
- **lemonade:** `question` for launcher issues.

**Gaps against live labels:**
- **agents:** missing `needs-triage`, `needs-info`, `ready-for-agent` and `ready-for-human`; only `wontfix` exists.
- **dotfiles:** missing `needs-info`.
- **All other repos:** no declared label is missing, fathomwork included.
- **`wayfinder:*`:** all five labels exist in every repo except agent-armory, which has only `wayfinder:grilling` and declares none. arch-pkgs also has `wayfinder:seed`.

## 4. Notable divergences

- **agent-armory:** uses its own tracker. `tools/issue_tracker_ops.py` carries mutation gates, and `config/agent-equipment.toml` overrides the docs. It adds a triage-record header and a local follow-up fallback, and has no wayfinding section.
- **chatgpt-linux:** a retired fork (archived=true). The tracker is read-only evidence, publication is forbidden, and there is no Agent skills block or Operating Policy.
- **fork-ops:** two extra Operating Policy bullets, no wayfinding section, and a tracker doc with D1, D2 and D3.
- **arch-pkgs:** `destination:*` map labels (the default is `destination:deployment`), nested maps, and `wayfinder:seed`. arch-strix-halo-pkgs instead uses `execution:*` labels.
- **codiquary and sacrysty:** a flat `## Issue tracker` section instead of an `## Agent skills` block, a prose-only tracker, DCO sign-off, and no Operating Policy.
- **cqmgr and mastic:** a condensed repo-qualified tracker, which reintroduces D1 and D3 and appends to the map after closing. Their `cog.toml` hooks exist, but no cog sentence mentions them.
- **lemonade and computer-use-linux:** fork trackers with upstream fences. lemonade creates issues through `gh api` rather than `gh issue create`.
- **provingkit:** `<checkout>.wt/` and checkpointing still sit on main until #235 merges. dotfiles keeps a chezmoi-specific worktree rule even after #360.
