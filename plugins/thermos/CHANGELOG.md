# Changelog

Provenance of the dual-harness adaptation. The layout, adapter boundary, and maintenance contract live in [README.md](README.md).

## 1.0.2

Review-driven refinement, 2026-07-05:

- Made same-message parallel subagent dispatch mandatory on harnesses that ship the review subagents, and namespaced the dispatch targets.
- Inlined the synthesis rules into the `thermos` skill and removed `skills/thermos/references/`.
- Deduplicated the subagent files down to dispatch glue plus a degraded-mode fallback summary; the full rubrics live in `skills/*/SKILL.md` and `skills/*/references/`.
- Repointed manifest `author`, `homepage`, and `repository` at this repository and dropped Cursor's privacy/terms URLs from the Codex interface block; upstream credit stays in the README and this changelog.
- Unified the hyphenation of the human-facing workflow name, dropping the spaced "Thermo Nuclear" variant; titles use title case ("Thermo-Nuclear Review"), prose keeps sentence case ("Thermo-nuclear branch review").
- Named the subagent handoff sections in the `thermos` skill and paired the Codex default prompts across the manifest and per-skill interface files.
- Made the audit checklist's additive-workflow devex carve-out explicit.

## 1.0.1

Rubric-equivalence refresh, 2026-07-05:

- Expanded `skills/*/references/` so the port preserves Cursor's original review bar behind concise `SKILL.md` entrypoints.

Codex adapter refresh, 2026-07-05:

- Removed unsupported top-level `contributors` metadata from `.codex-plugin/plugin.json`.
- Documented the Codex install-cache behavior and runtime component surface in the README.
- Aligned the personal marketplace category with the Codex manifest.

Claude Code adapter, 2026-07-05:

- Added `.claude-plugin/plugin.json` relying on auto-discovery of `skills/` and `agents/` (the README adapter-boundary section owns the no-path-overrides rationale).
- Restored the upstream `agents/*.md` review subagents that the Codex adapter had omitted, adapted for Claude: `thermos:`-namespaced skill references, Cursor-Task-specific orchestration sections removed.
- Added the repo-root `.claude-plugin/marketplace.json`.

Codex adapter, 2026-06-25:

- Added `.codex-plugin/plugin.json` with an `interface` block, plus per-skill `skills/<name>/agents/openai.yaml` interface metadata.
- Omitted the upstream root `agents/*.md` subagents — Codex has no dispatchable-subagent construct.
- Rewrote the shared skills from upstream's prompt-style form into concise progressive-disclosure skills backed by `references/*.md`, dropping upstream's `disable-model-invocation: true` gate; on harnesses with description-triggered skills, all three skills are model-invocable.

## 1.0.0 (upstream)

Thermo-nuclear branch review by the Cursor team: deep correctness and security audits, a strict maintainability rubric, and parallel review subagents with orchestration. See [cursor/plugins](https://github.com/cursor/plugins/tree/main/thermos).
