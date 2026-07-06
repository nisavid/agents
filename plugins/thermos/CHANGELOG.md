# Changelog

Provenance of the dual-harness adaptation. See [README.md](README.md) for the layout contract.

## 1.0.1

Rubric-equivalence refresh, 2026-07-05:

- Expanded the shared correctness and maintainability references so the Codex and Claude Code port preserves Cursor's original review bar while keeping concise `SKILL.md` entrypoints.
- Kept harness-specific orchestration in the manifests, Codex `openai.yaml` files, and Claude subagents; the detailed review criteria remain authored once under `skills/*/references/`.

## Codex adaptation refresh

Refreshed the Codex adapter by Ivan D Vasin, 2026-07-05:

- Removed unsupported top-level `contributors` metadata from `.codex-plugin/plugin.json`; Codex validation accepts attribution in docs, not that manifest field.
- Documented Codex's local install cache behavior and runtime component boundary: Codex consumes the declared `skills/` surface, nested `skills/<name>/agents/openai.yaml`, referenced skill files, and shared assets while ignoring Claude-only root files as runtime components.
- Refreshed the personal marketplace category to match the Codex manifest's developer-tool positioning.

## Claude Code adaptation

Added the Claude Code adapter by Ivan D Vasin, 2026-07-05, alongside the existing Codex adapter on the shared skills:

- `.claude-plugin/plugin.json` manifest. Unlike upstream, it declares no component-path overrides and relies on auto-discovery of `skills/` and `agents/` — a directory-string `agents` override fails `claude plugin validate`.
- Restored the upstream `agents/*.md` review subagents that the Codex adapter had omitted, adapted for Claude: `thermos:`-namespaced skill references, and the Cursor-Task-specific orchestration sections removed. Claude auto-discovers them from the shared tree.
- Repo-root `.claude-plugin/marketplace.json` exposing this tree as a Claude marketplace.

## Codex adaptation

Added the Codex adapter by Ivan D Vasin, 2026-06-25:

- `.codex-plugin/plugin.json` with an `interface` block, plus per-skill `skills/<name>/agents/openai.yaml` interface metadata.
- Omitted the upstream root `agents/*.md` subagents — Codex has no dispatchable-subagent construct — so its orchestrator consumes the shared skills directly.

Also rewrote the shared skills from the upstream Cursor prompt-style form into concise, progressive-disclosure skills backed by `references/*.md`. That rewrite removed upstream's `disable-model-invocation: true` gate, so on harnesses with description-triggered skills (including Claude) all three skills are model-invocable by description match.

## 1.0.0 (upstream)

Thermo-nuclear branch review by the Cursor team: deep correctness and security audits, a strict maintainability rubric, and parallel review subagents (`agents/*.md`) with orchestration. See [cursor/plugins](https://github.com/cursor/plugins/tree/main/thermos).
