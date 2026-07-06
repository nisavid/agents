# Thermos plugin

Cursor's thermo-nuclear review workflow ported to Claude Code and Codex-like harnesses: paired risk and maintainability review passes synthesized into one findings-first verdict.

This tree is a **single common core served to multiple agent harnesses** (Claude Code and Codex-like harnesses) through thin per-harness adapter files. Each harness reads only its own manifest and ignores the other's files, so the shared skills stay authored once.

## Layout

```
plugins/thermos/
├── skills/                         # Shared core — authored once (see Adapter boundary)
│   ├── thermos/                    #   orchestrator: paired passes + inline synthesis rules
│   ├── thermo-nuclear-review/      #   correctness/security rubric
│   └── thermo-nuclear-code-quality-review/  # maintainability rubric
│       ├── SKILL.md                #   every skill dir: the skill entrypoint
│       ├── references/*.md         #   the two rubric skills only: progressive-disclosure detail
│       └── agents/openai.yaml      #   every skill dir: Codex interface metadata (see note)
├── agents/*.md                     # Claude subagents (shared source, Claude-only consumer)
├── .claude-plugin/plugin.json      # Claude adapter manifest
├── .codex-plugin/plugin.json       # Codex adapter manifest
├── assets/logo.png                 # Shared
├── CHANGELOG.md                    # Provenance of each adaptation
└── LICENSE
```

## Adapter boundary

| Concern | Claude Code | Codex-like |
|:--------|:------------|:-----------|
| Manifest | `.claude-plugin/plugin.json` | `.codex-plugin/plugin.json` |
| Skills | auto-discovered from `skills/` | declared via `"skills": "./skills/"` |
| Review subagents | auto-discovered from `agents/*.md` | root `agents/*.md` not consumed; use native/generic subagent dispatch when available |
| Per-skill interface | not consumed | `skills/<name>/agents/openai.yaml` |
| Marketplace | repo-root `.claude-plugin/marketplace.json` | `~/.agents/plugins/marketplace.json` |

The Claude manifest deliberately carries **no** `agents`/`skills` path overrides: a directory-string override for `agents` fails `claude plugin validate`, and default auto-discovery of `skills/` and `agents/` already covers both. The Codex manifest declares only `skills` plus its `interface` block, so Codex never loads the root `agents/` as plugin components. That is separate from Codex's harness-level subagent support: when generic/native subagent dispatch is available, the shared `thermos` skill can use it.

"Authored once" does not mean strictly harness-neutral: the shared `thermos` skill names the Claude subagents for conditional dispatch, describes the generic/native subagent branch for Codex-like harnesses, and each skill directory carries a Codex-only `agents/openai.yaml`. The harness-conditional lines live in the shared file on purpose — one inline branch beats diverging per-harness skill bodies.

### Two directories named `agents/`

The tree contains two unrelated things both named `agents/`, at different depths — a collision imposed by the two harness formats, **not renamable** without breaking discovery:

- `agents/*.md` (plugin root) — Claude dispatchable **subagents**. Shared source; only Claude discovers them.
- `skills/<name>/agents/openai.yaml` (nested) — Codex per-skill **interface metadata** (display name, default prompt). Tiny, and consumed only by Codex.

When editing "the agents," confirm which of the two you mean.

## Architecture

```mermaid
flowchart TB
  subgraph L2["Orchestrator skill"]
    TH[thermos]
  end
  subgraph L1["Claude subagents"]
    SNR[thermo-nuclear-review-subagent]
    SNCQ[thermo-nuclear-code-quality-review-subagent]
  end
  DIFF[diff + file contents]
  subgraph L0["Shared skills"]
    TNR[thermo-nuclear-review]
    TNCQ[thermo-nuclear-code-quality-review]
  end
  TH --> SNR
  TH --> SNCQ
  SNR --> TNR
  SNR --> DIFF
  SNCQ --> TNCQ
  SNCQ --> DIFF
```

On Claude the `thermos` orchestrator dispatches the two named subagents in parallel; each applies its shared skill rubric to the same scoped diff. On Codex-like harnesses with generic/native subagent dispatch, `thermos` uses two generic reviewer subagents instead. Sequential in-context passes are the fallback when subagent dispatch is unavailable or clearly not worth the overhead.

## Components

### Skills (shared)

| Skill | Purpose |
|:------|:--------|
| `thermo-nuclear-review` | Deep branch audit: bugs, breakages, security, devex regressions, feature-gate leaks. |
| `thermo-nuclear-code-quality-review` | Strict maintainability audit: code-judo, file-size pressure, spaghetti, boundaries. |
| `thermos` | Run both passes and synthesize deduplicated, severity-ordered findings. |

### Subagents (Claude-discovered)

| Subagent | Purpose |
|:---------|:--------|
| `thermo-nuclear-review-subagent` | Dispatchable wrapper that applies the `thermo-nuclear-review` rubric to a scoped diff. |
| `thermo-nuclear-code-quality-review-subagent` | Dispatchable wrapper that applies the `thermo-nuclear-code-quality-review` rubric to a scoped diff. |

## Maintenance notes

- **Keep the two manifest `description` fields paired.** They differ only by the ` for Codex` qualifier and the omission of the `parallel review subagents` clause because the root `agents/*.md` wrappers are Claude-only plugin components. Codex can still use native/generic subagent dispatch when the harness exposes it. Mirror any other wording change across both.
- **Keep the Codex default prompts paired.** Each entry in the Codex manifest's `interface.defaultPrompt` duplicates a per-skill `skills/<name>/agents/openai.yaml` `default_prompt` on a second Codex surface; keep each pair verbatim-identical.
- **Scope enumerations are paraphrases, not pairs.** The review-dimension lists in the skill descriptions, the `thermos` routing step, the subagent descriptions, and the Components tables above are intentionally loose restatements for their own surfaces. Keep each accurate; do not sync them verbatim.
- **Versioning.** Both manifests use semver build metadata (`<semver>+claude.<ts>`, `<semver>+codex.<ts>`) to disambiguate the artifacts. Build metadata after `+` does not affect version precedence, so a real release that should trigger an update check must bump `MAJOR.MINOR.PATCH` (or use a pre-release identifier before the `+`) — a new timestamp alone reads as the same version.
- **Codex installs are cached copies.** Codex reads the local marketplace source when installing, validates the Codex manifest and declared skill surface, and writes a copied cache under `~/.codex/plugins/cache/`. Source edits need a fresh Codex build-metadata cachebuster, `codex plugin add thermos@nisavid-agents`, and a new Codex thread. The cache may include root Claude files and docs copied from the source tree, but the Codex runtime component surface remains the Codex-declared surface: `.codex-plugin/plugin.json`, `skills/`, nested `skills/<name>/agents/openai.yaml`, referenced skill files, and shared assets.
- **Claude installs are cached copies.** First-time install: `claude plugin marketplace add nisavid/agents` (or the local checkout path), then install `thermos` from the `nisavid-agents` marketplace. After editing this tree, run `claude plugin marketplace update nisavid-agents` and update/reinstall the plugin; the running session needs a restart to pick up a new plugin version.
- **Attribution.** Both manifests attribute this diverged port to its maintainer and point at this repository. Upstream Cursor credit lives in the [Upstream](#upstream) section and [CHANGELOG.md](CHANGELOG.md); the Codex manifest uses only fields accepted by Codex validation.

## Upstream

Adapted from the Cursor team's Thermos plugin: [cursor/plugins](https://github.com/cursor/plugins/tree/main/thermos). See [CHANGELOG.md](CHANGELOG.md) for the provenance of each adaptation.

## License

MIT
