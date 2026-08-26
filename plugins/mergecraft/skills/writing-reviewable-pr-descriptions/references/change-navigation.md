# Change Navigation Reference

Use this reference only while constructing or revising the first-viewport
`STACK` and `DIFF` disclosures.

## Shared Badge Rules

- Every `<img>` has exactly one real `alt`, `src`, and `height="16"` attribute.
- Visible labels are uppercase.
- `STACK` and `DIFF` label shields use `style=for-the-badge` and neutral
  `57606A`; every metric shield uses `style=flat`.
- Category order is `IMPL`, `TEST`, `DOC`, `GEN`, `OTHER`, then `FILES`.
- Colors are stable: `IMPL 0969DA`, `TEST 6F5F9A`, `DOC 3F7770`,
  `GEN 76652F`, `OTHER 57606A`, and `FILES 5F6B78`.
- Operation badges `BINARY`, `MOVED`, and `COPIED` use neutral `5F6B78`.
- Encode badge text for URLs. Use the true minus sign `−` (`%E2%88%92`), not a
  hyphen, in visible deletion metrics.
- Separate the label shield from metrics with `&nbsp;`; use ordinary spaces
  between subsequent shields.
- Wrap non-navigation images in `<picture>`. Link only intentional PR
  navigation badges.
- Linked PR badges have matching descriptive `alt` and `title` text containing
  `#number — recognizable title`. Escape HTML special characters.
- Category badges have exactly one semantic `title` naming the category, metric,
  and taxonomy meaning. Atomic line badges and `BINARY`, `MOVED`, and `COPIED`
  badges have exactly one `title` matching their `alt`. Other badges have no
  `title`.
- Encode Shields paths canonically with uppercase percent escapes. Do not use
  alternate-but-equivalent encodings such as a raw `+` or lowercase `%2b`.
- Use real `src`, `height`, `alt`, and `title` attributes. Attributes such as
  `data-src` and `data-title` do not satisfy the contract.
- Every `<img>` inside either disclosure is a structurally valid Shields image
  with a real `src="https://img.shields.io/..."`; do not leave inert, fallback,
  or non-Shields images in recognized navigation markup.
- Keep every summary on one source line. GitHub disclosure rendering is less
  predictable when block markup appears inside `<summary>`. Each disclosure
  contains exactly one `<summary>...</summary>` pair.
- Start the body at byte zero with an exact full-line `<details>` opener. Each
  navigation disclosure ends on an exact full-line `</details>` closer; do not
  indent these tags, add attributes, change their case, put them inline, or nest
  another full-line `<details>` inside them.
- Render exactly one Stack disclosure when stacked and exactly one Diff
  disclosure in every body. The complete leading grammar is `[STACK, DIFF]` or
  `[DIFF]`. A truly empty source line separates Stack from Diff and separates
  the final navigation disclosure from any remaining body content, including
  raw HTML. No separator is required when the body ends at `</details>`.
- Everything after that deterministic boundary is an opaque suffix. Do not
  tokenize or normalize it while validating navigation; preserve its bytes
  through the baseline-fragment contract. Unrelated disclosures may live there.
- The Stack and Diff Shields source URLs are reserved navigation fingerprints,
  and a full-line Markdown `Stack` heading is a reserved ambiguity signature.
  Reject those signatures anywhere in the opaque suffix, including examples,
  code fences, and raw HTML, rather than interpreting suffix Markdown.

### Parser Scope Guard

The change-navigation parser is a strict prefix recognizer, never a CommonMark
or GFM parser. Do not add state for fences, lists, blockquotes, links, reference
definitions, tables, HTML blocks or comments, inline code, or other suffix
syntax. Review findings and ambiguity concerns do not authorize that expansion.

When a new residual-navigation ambiguity is proven, use the smallest explicit
byte signature that closes it and add adversarial opaque-suffix tests. If the
requirement truly depends on Markdown semantics, stop and make a separately
reviewed contract/dependency decision around a maintained standards-compliant
parser. Do not build or iterate a partial Markdown parser in this skill.

## Stack Disclosure

Render this only for a stacked PR, immediately before Diff:

```md
<details>
<summary><picture><img alt="STACK" src="https://img.shields.io/badge/STACK-57606A?style=for-the-badge" height="16"></picture>&nbsp;<picture><img alt="STACK POSITION: 2 OF 2" src="https://img.shields.io/badge/2%20OF%202-5F6B78?style=flat" height="16"></picture> <a href="https://github.com/OWNER/REPO/pull/100"><img alt="BASE: #100 — feat(api): add request contract" title="#100 — feat(api): add request contract" src="https://img.shields.io/badge/BASE-%23100-5F6B78?style=flat" height="16"></a> <picture><img alt="STACK STATUS: TOP" src="https://img.shields.io/badge/TOP-5F6B78?style=flat" height="16"></picture></summary>

- **[#100 — feat(api): add request contract](https://github.com/OWNER/REPO/pull/100)**<br><picture><img alt="IMPL: 32 additions, 4 deletions" title="Implementation: 32 additions, 4 deletions (non-test source and configuration)" src="https://img.shields.io/badge/IMPL-%2B32%20%E2%88%924-0969DA?style=flat" height="16"></picture> <picture><img alt="TEST: 18 additions, 0 deletions" title="Tests: 18 additions, 0 deletions (automated verification)" src="https://img.shields.io/badge/TEST-%2B18%20%E2%88%920-6F5F9A?style=flat" height="16"></picture> <picture><img alt="FILES: 2 added, 1 modified, 0 removed" src="https://img.shields.io/badge/FILES-%2B2%20~1%20%E2%88%920-5F6B78?style=flat" height="16"></picture>

- **[#101 — feat(web): consume request contract](https://github.com/OWNER/REPO/pull/101)** **← this PR**<br><picture><img alt="IMPL: 9 additions, 3 deletions" title="Implementation: 9 additions, 3 deletions (non-test source and configuration)" src="https://img.shields.io/badge/IMPL-%2B9%20%E2%88%923-0969DA?style=flat" height="16"></picture> <picture><img alt="TEST: 16 additions, 22 deletions" title="Tests: 16 additions, 22 deletions (automated verification)" src="https://img.shields.io/badge/TEST-%2B16%20%E2%88%9222-6F5F9A?style=flat" height="16"></picture> <picture><img alt="FILES: 0 added, 2 modified, 0 removed" src="https://img.shields.io/badge/FILES-%2B0%20~2%20%E2%88%920-5F6B78?style=flat" height="16"></picture>

<sup>IMPL means non-test source and configuration. TEST means automated verification. DOC means reviewer and user documentation. GEN means generated artifacts. OTHER means files outside those categories. FILES shows added, modified, and removed files as +, ~, and −.</sup>

</details>
```

### Stack Semantics

- Position is the current PR's one-based index over the complete current stack.
- `BASE` is the direct Git base. A PR-valued `BASE` always links to that PR;
  only a branch-valued base such as `main` is a neutral unlinked badge.
- Add `DEP` badges immediately after `BASE` only for additional PR dependencies.
  Do not repeat the direct base or any member of the Stack inventory as a
  dependency; ancestry already represented by the direct-base chain is
  transitive. For the bottom inventory item, a PR-valued `BASE` is outside the
  inventory.
- `NEXT` links to the next PR when one follows. `TOP` is an unlinked endpoint.
- Every intentionally linked `BASE`, `DEP`, or `NEXT` badge uses the destination
  PR's title in `alt` and `title`.
- Expanded content lists the complete stack from bottom to top. Each item has a
  bold title link, one `<br>`, then an unlabeled metric row on the same source
  line. Mark exactly one item `**← this PR**`.
- Escape `\`, backticks, `*`, `_`, `[`, and `]` with one backslash in each
  visible inventory title, and use canonical HTML entities for `&`, `<`, and
  `>`. The resulting plain title must exactly match the corresponding
  navigation badge's semantic title.
- Stack `FILES` always shows added, modified, and removed counts, even when zero.
  Append `MOVED N` and `COPIED N` in that order when nonzero; for example,
  `+0 ~1 −0 MOVED 1 COPIED 2`.
- Added, modified, removed, moved, and copied are disjoint file operations. For
  the current Stack item, their sum equals the Diff summary's touched-file
  count. `MOVED` and `COPIED` counts exactly match the Diff file rows carrying
  those operation badges; the remaining unique Diff target paths equal the
  added-plus-modified-plus-removed subtotal.
- Use the exact taxonomy line shown in the example. A short current contextual
  note, such as a recently merged former base, may follow it using inline prose,
  links, and code only.
- Do not put Stack or Diff label shields inside the expanded list.
- Do not repeat this inventory in a separate `## Stack` section.
- The expansion contains only its canonical inventory rows, the exact taxonomy
  `<sup>` line, and at most one short inline contextual line after the taxonomy.
  Do not use headings, tables, quotes, fences, HTML blocks, images, alternate
  list markers, or text or extra badges appended to an inventory row.

## Diff Disclosure

Render this for every PR, immediately after Stack when present. Resolve the
exact pushed base tip and head, then require one unique merge base and compute
the reviewer-visible merge-base-to-head diff. Stop rather than publish when any
identity or the unique merge base is unavailable:

```md
<details>
<summary><picture><img alt="DIFF" src="https://img.shields.io/badge/DIFF-57606A?style=for-the-badge" height="16"></picture>&nbsp;<picture><img alt="IMPL: 9 additions, 3 deletions" title="Implementation: 9 additions, 3 deletions (non-test source and configuration)" src="https://img.shields.io/badge/IMPL-%2B9%20%E2%88%923-0969DA?style=flat" height="16"></picture> <picture><img alt="TEST: 16 additions, 22 deletions" title="Tests: 16 additions, 22 deletions (automated verification)" src="https://img.shields.io/badge/TEST-%2B16%20%E2%88%9222-6F5F9A?style=flat" height="16"></picture> <picture><img alt="FILES: 2 touched" src="https://img.shields.io/badge/FILES-2-5F6B78?style=flat" height="16"></picture></summary>

- <picture><img alt="IMPL: 9 additions, 3 deletions" title="Implementation: 9 additions, 3 deletions (non-test source and configuration)" src="https://img.shields.io/badge/IMPL-%2B9%20%E2%88%923-0969DA?style=flat" height="16"></picture> <picture><img alt="FILES: 1 implementation file" src="https://img.shields.io/badge/FILES-1-5F6B78?style=flat" height="16"></picture>
  - [`src/widget.ts`](https://github.com/OWNER/REPO/pull/101/files#diff-c58057923cf3465c660a0574f12a0bc228e2005e0e2685a82691938232e2ac0c) <picture><img alt="9 additions, 3 deletions" title="9 additions, 3 deletions" src="https://img.shields.io/badge/%2B9-%E2%88%923-CF222E?style=flat&labelColor=1A7F37" height="16"></picture>
- <picture><img alt="TEST: 16 additions, 22 deletions" title="Tests: 16 additions, 22 deletions (automated verification)" src="https://img.shields.io/badge/TEST-%2B16%20%E2%88%9222-6F5F9A?style=flat" height="16"></picture> <picture><img alt="FILES: 1 test file" src="https://img.shields.io/badge/FILES-1-5F6B78?style=flat" height="16"></picture>
  - [`tests/widget.test.ts`](https://github.com/OWNER/REPO/pull/101/files#diff-e9a6bc8c53dbfc140a01d61d4ec98e6204dea1ca9f889db10c9fce8ea4786ba2) <picture><img alt="16 additions, 22 deletions" title="16 additions, 22 deletions" src="https://img.shields.io/badge/%2B16-%E2%88%9222-CF222E?style=flat&labelColor=1A7F37" height="16"></picture>

<sup>IMPL means non-test source and configuration. TEST means automated verification. DOC means reviewer and user documentation. GEN means generated artifacts. OTHER means files outside those categories. FILES shows added, modified, and removed files as +, ~, and −.</sup>

</details>
```

### Diff Semantics

- Summary category totals are additions/deletions from the unique merge base to
  the exact pushed head: the same three-dot comparison reviewers see. Preserve
  the base-tip identity for publication leases and immutable comparison links.
  Omit categories with no changed lines. `FILES` is total touched files,
  including binary and operation-only files.
- Expanded top-level items follow fixed category order. Each has the same
  category total plus the number of files included in that category. Use these
  exact descriptors: `implementation`, `test`, `documentation`, `generated`,
  and `other`, with singular `file` or plural `files`.
- Nested items link every changed path to its actual Files changed anchor. The
  current schema v3 supports only GitHub's confirmed
  `diff-<sha256(target path)>` anchor convention. Verify rendered anchors
  against GitHub and stop if GitHub renders another anchor. Supporting another
  convention requires a versioned manifest field and a live GitHub observer; do
  not guess or silently substitute it.
- Render an ordinary path as Markdown inline code inside the link. When the
  semantic path contains a backtick, use
  `<a href="FILES_URL"><code>HTML-ESCAPED_PATH</code></a>` instead; HTML-escape
  `&`, `<`, and `>` canonically and hash the unescaped target path. Do not use
  the HTML form for paths that the ordinary Markdown form can represent.
- Each textual file has one atomic two-segment shield. Green `1A7F37` is the
  label segment and red `CF222E` the message segment. Because both values are one
  image, a browser cannot break a line between additions and deletions.
- A file row contains only that atomic shield, or one `BINARY`, `MOVED`, or
  `COPIED` operation shield followed by the permitted atomic shield. Do not add
  category, file-count, or navigation shields to a file row.
- The per-file badge has matching `alt` and `title`, both written as words:
  `N additions, M deletions`.
- Use `+0` or `−0` when one side is zero. For a binary file with no meaningful
  line counts, use one neutral `BINARY` badge with matching `alt` and `title`.
- For a move or copy, give the source and target separate code nodes inside one
  link: ``[`old` → `new`](FILES_URL)``. A literal `→` inside either code node
  remains part of that path. When either path contains a backtick, use
  `<a href="FILES_URL"><code>HTML-ESCAPED_OLD</code> → <code>HTML-ESCAPED_NEW</code></a>`.
  Add a neutral `MOVED` or `COPIED` badge. The operation badge comes first; if
  the file also has edits, append the atomic line badge. Count it in the target
  path's semantic category; use `OTHER` only when the target cannot be
  classified reliably.
- A category may appear only in the expanded view with `+0 −0` when it contains
  only binary or operation-only files. The summary still omits its zero-line
  metric; the summary `FILES` count preserves its presence.
- Use singular `file` and plural `files` correctly in group badges.
- The expansion contains only canonical category rows and their indented file
  rows. Reject alternate list markers, prose, or other residual content.
- Authenticate the summary touched count, summary category totals, and expanded
  category file counts against the complete sealed Git and categorized
  inventories, including when only a bounded subset of file rows is rendered.

## Edge Checks

- Empty diff: do not fabricate a Diff disclosure. State that the pushed
  base/head has no diff and resolve whether the PR target or push is wrong.
- Changed base or restack: recompute every PR independently; never reuse totals
  from a previous base.
- Mixed file: split additions/deletions by category only when the patch supports
  an auditable split. In that case, the same linked file may appear once in each
  applicable category, while the summary `FILES` badge counts its target path
  once. Every appearance of the same target path uses the same operation kind
  and, for a move or copy, the same source path. Never repeat a file within one
  category. Otherwise use `OTHER` for that file's changed lines.
- Deleted file: link the path GitHub uses for the deletion anchor and count it as
  removed in Stack operations.
- Renamed stack title: refresh every linked title's `alt` and `title`, not only
  the visible list link.
- Large stack or diff: keep disclosures collapsed. For at most 100 files,
  render the complete inventory. For more than 100, retain the complete exact
  Git and categorized inventories in review input, render only the first 100
  target paths in deterministic Git path order, then add exactly
  `- **N files omitted from this bounded inventory.** [View the complete immutable comparison](https://github.com/OWNER/REPO/compare/BASE_OID...HEAD_OID)`.
  A schema-v3 review input over 100 files must contain that canonical bounded
  presentation; one with at most 100 files must not. Place the omission record
  exactly once after every rendered category and file row, immediately before
  the taxonomy note except for blank lines. The count, repository, and
  immutable OIDs must match. The first-100 presentation is only a file-count
  bound. Schema v3 has no second truncation or shortening fallback: if the
  canonical bounded body still exceeds 65,536 characters, stop publication.
- Shields unavailable: meaningful `alt` text must leave the summaries and file
  metrics understandable.

## Validator Binding

Always bind validation to the destination PR so a self-consistent body for the
wrong PR cannot pass:

```bash
python3 "<plugin>/skills/writing-reviewable-pr-descriptions/scripts/validate_change_navigation.py" \
  --repository OWNER/REPO --pr PR_NUMBER --title "TITLE" \
  --review-input /absolute/path/to/review-input.json /path/to/pr-body.md
```

Both the Stack current item and every Diff file link must match that repository
and PR number.

For a new stacked PR, the manifest may use
`__PUBLISHING_REVIEWABLE_PRS_PR_NUMBER__` only as the number and matching URL
suffix of its sole current Stack row. Validation projects that row to the
supplied sentinel or assigned PR number before exact Stack comparison. Existing
manifests and noncurrent rows require positive integer PR numbers. A new,
unstacked PR may use an empty Stack inventory. Whenever a Stack inventory is
present, its sole current row's category and file-operation totals must match
the complete sealed categorized and Git inventories.
