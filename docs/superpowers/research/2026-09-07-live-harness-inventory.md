# Live harness inventory: Hatchery and work Mac

This report records the 12 selected live harness cells for [nisavid/agents#42](https://github.com/nisavid/agents/issues/42). It is a current installation and control-surface manifest, not a behavior qualification, release decision, deployment record, or host-change authority.

- **Manifest status:** Complete with live-control gaps.
- **Generated:** 2026-09-07T10:37:35Z.
- **Repository boundary:** Clean `nisavid/harness-inventory-42` checkout at immutable revision [`c60ce86fec20fdc4d16d280ae0e6399b9bc98ef6`](https://github.com/nisavid/agents/commit/c60ce86fec20fdc4d16d280ae0e6399b9bc98ef6), verified before research and again after the final host probes.
- **Hatchery observation window:** 2026-09-07T10:25:03Z–10:36:19Z; CachyOS Linux, Linux 7.1.8-1-cachyos, x86_64.
- **Work Mac observation window:** 2026-09-07T10:29:42Z–10:36:37Z; macOS 26.6.2, arm64. The existing preauthorized work-Mac SSH route was successfully revalidated with strict existing-host-key handling. Its identifier and connection fields are not published here.
- **Tracker boundary:** The public issue body and all four comments were read before host probing and re-read after it. The issue remained open with `updated_at` 2026-09-07T09:38:12Z and four comments.

## Evidence and status key

| Label | Meaning |
| --- | --- |
| **Observed** | Returned by installed package/application metadata, a manager receipt, a version command, or a narrowly selected host probe during the stated window. |
| **Documented** | Declared by installed help or metadata. It proves that the installed build exposes the named interface, not that the interface works in a GUI or live session. |
| **Applicable** | The selected surface has direct installation or invocation evidence on that host. It does not mean qualified, supported for release, authenticated, or healthy. |
| **Unavailable** | The selected control or validation probe could not be used inside this read-only/privacy boundary. |
| **Unsupported** | Installed documentation explicitly excludes the host or mode. This label is not inferred from absence. |
| **Unknown** | The evidence cannot responsibly decide the fact. |

No whole selected cell is classified **unsupported** by the current evidence. One subordinate control is explicitly unsupported on Hatchery: Claude Code's installed `add-from-claude-desktop` help limits that import bridge to macOS and WSL. GUI behavior is **unavailable** for all desktop cells, and several origins or capabilities remain **unknown** as called out below.

## Compact 12-cell manifest

| Host | Surface | Cell status | Exact observed version | Installation evidence and transaction origin | Discovery and invocation | Native manager/plugin capability and bounded result |
| --- | --- | --- | --- | --- | --- | --- |
| Hatchery | ChatGPT Codex | Applicable producer; Codex mode unknown | Package `26.901.51231-1`; provided app identity `26.901.51231` | Installed `chatgpt-desktop-bin` `26.901.51231-1`; package-manager metadata recorded `Validated By: Signature`. No transaction receipt was observed, so installation transaction origin is unknown. The current configured `cachyos` sync database exposed that name/version. | Desktop app **ChatGPT**; `chatgpt` on `$PATH`; `chatgpt.desktop`; installed metadata declares `codex:` handling | No non-GUI plugin manager was exposed by the inspected metadata. GUI control was unavailable, so Codex-mode discovery, invocation, and success are unobserved. |
| Hatchery | Codex | Applicable; observed and documented | `codex-cli 0.152.1` | Vite+ `0.3.0` receipt for npm package `@openai/codex` `0.152.1`, Node `24.20.0` | `codex` on `$PATH` through `~/.vite-plus/bin/codex`; TUI by default; installed help documents `exec` and `review` | Installed help documents plugin add/list/remove and marketplace add/list/upgrade/remove; MCP management; MCP server; shared app-server agent browsing. No configured inventory or live session was read. Version/help succeeded while the read-only sandbox denied an attempted PATH-alias write. |
| Hatchery | Claude Code | Applicable; observed and documented | `2.1.239` | `~/.local/bin/claude` links to the versioned `~/.local/share/claude/versions/2.1.239` build; installed help documents the native `install` and `update` commands | `claude` on `$PATH`; interactive by default; installed help documents `--print`, plan mode, restricted tool selection, and background agents | Installed help documents plugin details/enable/disable/eval/install/list/marketplace/update/validate, MCP management, and background-agent management. Configured plugins, MCP servers, agents, and authentication were not enumerated. |
| Hatchery | Claude Desktop | Applicable app; GUI unavailable | Package `1.40609.0-1` | Installed `claude-desktop` `1.40609.0-1`; package-manager metadata recorded `Validated By: Signature`. No transaction receipt was observed, so installation transaction origin is unknown. The current configured `cachyos` sync database exposed `claude-desktop` `1.46388.2-1`, not the installed version. | Desktop app **Claude**; `claude-desktop` on `$PATH`; desktop ID `com.anthropic.Claude.desktop`; `claude:` handler; installed metadata declares New Chat and New Claude Code Session actions | No safe non-GUI manager was exposed. Installed metadata documents launch entries only; GUI behavior, MCP/plugin UI, and successful Claude Code handoff are unknown. |
| Hatchery | Cursor | Applicable; observed and documented | Package and CLI `3.15.19`; build `de07bee81cefe43461ebf4f40c3d2d78d15052a0`; x64 | Installed `cursor-bin` `3.15.19`; package-manager metadata recorded `Validated By: Signature`. No transaction receipt was observed, so installation transaction origin is unknown. The current configured `cachyos` sync database exposed `cursor-bin` `3.18.9-1`, not the installed version. | Desktop app **Cursor**; `cursor.desktop`; `cursor` on `$PATH` first resolves through `~/.local/bin/cursor`, which forwards to the package-owned IDE launcher | Installed CLI help documents VSIX extension list/install/update/remove, `--add-mcp`, chat, and the `agent` subcommand. It does not prove GUI or Agent Plugin behavior; configured extensions and MCP definitions were not listed. |
| Hatchery | Cursor Agent | Applicable; observed and documented | `2026.08.11-e8db854` | `agent` and `cursor-agent` link to the versioned `~/.local/share/cursor-agent/versions/2026.08.11-e8db854` build; no independent package receipt observed | `agent`, `cursor-agent`, and documented `cursor agent`; installed help documents print, ask, and plan modes | Installed help documents local plugin directories, plugin-marketplace add/list/remove/update, and MCP login/list/tools/enable/disable. Status, model listing, authentication, configured inventory, and agent execution were intentionally not invoked. |
| Work Mac | ChatGPT Codex | Applicable producer; Codex mode unknown | App `26.901.51231`, build `8109` | System Applications bundle, public bundle ID `com.openai.codex`; no Homebrew cask, Mac App Store, or matching Installer receipt observed, so installer origin is unknown | Desktop app **ChatGPT**; public bundle ID `com.openai.codex`; installed metadata declares `codex:` handling | No non-GUI manager was exposed by the selected bundle fields. GUI control was unavailable, so Codex-mode discovery, invocation, and success are unobserved. |
| Work Mac | Codex | Applicable; observed and documented | `codex-cli 0.153.4` | Vite+ `0.3.0` receipt for npm package `@openai/codex` `0.153.4`, Node `24.20.0` | `codex` on `$PATH` through `~/.vite-plus/bin/codex`; installed help documents TUI, `exec`, `review`, and an `app` command | Installed help documents plugin add/list/remove and configured or remote marketplaces; MCP management; shared app-server agent browsing. No inventory or app launch was attempted. Version/help succeeded while the no-write sandbox denied an attempted PATH-alias write. |
| Work Mac | Claude Code | Applicable; observed and documented | `2.1.263` | `~/.local/bin/claude` links to the versioned `~/.local/share/claude/versions/2.1.263` build; installed help documents the native `install` and `update` commands | `claude` on `$PATH`; installed help documents interactive, print, plan, restricted, and background-agent modes | Installed help documents the full plugin manager, marketplace, MCP manager, background-agent lifecycle, and the macOS/WSL Claude Desktop MCP import bridge. Configured state and live execution were not sampled. |
| Work Mac | Claude Desktop | Applicable app; GUI unavailable | App and build `1.46388.4` | System Applications bundle, public bundle ID `com.anthropic.claudefordesktop`; no Homebrew cask, Mac App Store, or matching Installer receipt observed, so installer origin is unknown | Desktop app **Claude**; public bundle ID `com.anthropic.claudefordesktop`; installed metadata declares `claude:` handling | No safe non-GUI manager was exposed. GUI behavior, configured desktop integrations, and successful Claude Code handoff are unknown. |
| Work Mac | Cursor | Applicable app; `$PATH` IDE control unavailable | App/CLI `3.18.25`; build `3.18.25`; CLI commit `280eca2911f1774689696e5f1efa5a4f97a87af0`; arm64 | System Applications bundle, public bundle ID `com.todesktop.230313mzl4w4u92`; no Homebrew cask, Mac App Store, or matching Installer receipt observed, so installer origin is unknown | Desktop app **Cursor** and `cursor:` handler. The app-bundled CLI exists, but the current `~/.local/bin/cursor` shim finds no next IDE command on `$PATH` and exits; the bundled CLI was probed directly in a no-write/no-network sandbox | Bundled CLI help documents VSIX extension management, `--add-mcp`, chat, and `agent`. GUI behavior and configured inventories are unobserved; ordinary `cursor` IDE invocation through the current SSH `$PATH` is unavailable. |
| Work Mac | Cursor Agent | Applicable by installed metadata; help unavailable | Versioned installation `2026.09.02-c22c1a3` | `agent` and `cursor-agent` link to `~/.local/share/cursor-agent/versions/2026.09.02-c22c1a3`; no independent package receipt observed | `agent` and `cursor-agent` exist on `$PATH`; the Cursor bundle help also documents an `agent` subcommand | A no-write/no-network `--version` probe reached a launcher that attempted login-keychain access and returned `macOS login keychain is locked`. The probe stopped there: no unlock, auth action, model request, or agent run occurred. Exact-build plugin/MCP manager capability and successful invocation remain unknown. |

The current configured `cachyos` sync-database observations above establish only which package names and versions are currently available. They are not transaction receipts and do not prove where the installed packages originated. They are not update recommendations or release research.

## Per-surface findings

### ChatGPT Codex

The controlling #42 issue designates `chatgpt-desktop-bin` as Hatchery's live producer and `cachyos` as its configured CachyOS producer route. This inventory observed installed `chatgpt-desktop-bin` version `26.901.51231-1`; installed package-manager metadata recorded `Validated By: Signature`, and the current configured `cachyos` sync database exposed the same name/version. That current match is not a transaction receipt, so installation transaction origin and installed-byte provenance remain unknown. Package metadata provides both `chatgpt` and `openai-codex-desktop`, while the installed desktop entry binds the ChatGPT application to the `codex:` scheme. On the work Mac, the ChatGPT bundle reports public bundle ID `com.openai.codex` and the same app version family.

Those observations establish installed producers and launch metadata, not GUI behavior. No computer-use control was available, and no app, Codex-mode session, authentication check, or inference was launched. Codex mode itself is therefore unknown on both hosts.

### Codex CLI/TUI

Both hosts resolve `codex` through Vite+ `0.3.0` receipts for the public npm package `@openai/codex`, with Hatchery at `0.152.1` and the work Mac at `0.153.4`. The installed help on both builds exposes the interactive TUI, non-interactive execution, review, MCP, plugin marketplaces, and shared app-server agent browsing. The Mac build additionally documents `codex app`.

Every Codex version/help invocation ran under a no-write boundary. The binary still tried to create PATH aliases; the boundary denied that write and the requested help/version output completed. This proves a startup write attempt under the probe conditions and proves no alias was changed. It does not validate a TUI session, app launch, plugin install, marketplace refresh, MCP connection, or inference.

### Claude Code

Hatchery has `2.1.239`; the work Mac has `2.1.263`. Both are direct version-store links exposed as `claude`, and both installed builds document native install/update commands, plugins and marketplaces, MCP, plan/print execution, and background agents.

The exact configured inventories were not queried because list/get operations can expose private server, marketplace, project, or account data and can health-check configured endpoints. Authentication, usage, models, sessions, and inference were also not queried. The installed MCP help explicitly limits `add-from-claude-desktop` to macOS and WSL, so that one bridge is unsupported on native Hatchery Linux even though Claude Code and Claude Desktop are separately installed there.

### Claude Desktop

Hatchery has installed `claude-desktop` `1.40609.0-1`; its package-manager metadata recorded `Validated By: Signature`. The current configured `cachyos` sync database exposed `claude-desktop` `1.46388.2-1`, not the installed version. No transaction receipt was observed, so installation transaction origin remains unknown. The work Mac has the `com.anthropic.claudefordesktop` bundle. This supersedes using the historical handoff's “unsupported on Hatchery” statement as an installation-absence claim. It does not settle current vendor support or qualification: only installed package/bundle and launch metadata were observed.

No desktop GUI was opened. Plugin/MCP UI, authentication, conversations, the installed New Claude Code Session action, and cross-surface handoff behavior are unknown.

### Cursor

Hatchery's `cursor-bin` package owns the IDE launcher, while a small `~/.local/bin/cursor` shim forwards to that package-owned command. On the work Mac, the Cursor app bundle and its bundled CLI are present, but the same portable shim finds no separate IDE command on the noninteractive `$PATH`; direct bundled help was therefore used inside a no-write/no-network sandbox.

Both installed CLI help surfaces document VSIX extension management, adding MCP definitions, chat, and the Cursor Agent subcommand. This is installed CLI documentation, not proof of GUI plugin discovery, an Agent Plugins loader, configured extensions, configured MCP, or successful UI use.

### Cursor Agent

Hatchery's versioned build returned version and help normally within the read-only boundary. Its help documents ask/plan modes, explicit plugin directories, a Git-backed plugin-marketplace manager, and MCP controls.

The work Mac has a newer versioned installation and both public command aliases, but the launcher crossed the credential boundary even for `--version`. The exact value-free failure was `macOS login keychain is locked`. No unlock or alternate credential path was attempted. Version identity is therefore bound to installed link metadata, while this exact build's help, native plugin/MCP surface, authentication state, models, and execution remain unknown.

## Controlling issue and superseded checkpoints

The [current #42 body](https://github.com/nisavid/agents/issues/42) and [2026-09-07 owner checkpoint](https://github.com/nisavid/agents/issues/42#issuecomment-5568647222) control this report. The API returned four comments: the [initial bot plan](https://github.com/nisavid/agents/issues/42#issuecomment-5309870119), the [2026-08-24 research checkpoint](https://github.com/nisavid/agents/issues/42#issuecomment-5395580794), the [2026-08-31 scout](https://github.com/nisavid/agents/issues/42#issuecomment-5473861682), and the current owner checkpoint.

The 2026-08-24 comment's assignment to `chatgpt-linux#119` is superseded. It is not an active source, capture, migration, or ownership route. Under the controlling issue, Hatchery's designated live producer remains `chatgpt-desktop-bin`; residual routine-upgrade acceptance remains with [arch-pkgs#76](https://github.com/nisavid/arch-pkgs/issues/76), and fallback retirement remains with [arch-pkgs#77](https://github.com/nisavid/arch-pkgs/issues/77).

The 2026-08-31 scout remains useful history but not current proof for these versions or controls. Repository research copies are also explicitly historical: the [Agent Plugins terminology note](https://github.com/nisavid/agents/blob/c60ce86fec20fdc4d16d280ae0e6399b9bc98ef6/docs/superpowers/research/2026-08-12-agent-plugins-standard-adoption.md), [source-skill privacy note](https://github.com/nisavid/agents/blob/c60ce86fec20fdc4d16d280ae0e6399b9bc98ef6/docs/superpowers/research/2026-08-18-source-skill-lineage-and-drift.md), and [cross-repository handoff](https://github.com/nisavid/agents/blob/c60ce86fec20fdc4d16d280ae0e6399b9bc98ef6/docs/superpowers/research/2026-08-24-cross-repository-implementation-handoff.md) supplied vocabulary and evidence boundaries only. No historical host version was promoted into this manifest.

Public release/update documentation belongs to [nisavid/provingkit#1](https://github.com/nisavid/provingkit/issues/1) and was not duplicated here.

## Safe rerun method

1. Rebind the checkout with `git rev-parse HEAD` and `git status --porcelain=v2 --branch`; require the report's immutable revision and a clean worktree.
2. Re-read #42 and paginate all issue comments through the public GitHub API. Stop if the body, ownership, or selected surfaces change.
3. On Hatchery, use `pacman -Qi` only for the installed package name/version and the recorded `Validated By: Signature` field. Use `pacman -Si` only to observe which current configured sync database exposes a package name/version and to distinguish that version from the installed one. Treat current sync availability as neither a transaction receipt nor transaction-origin proof; classify installation transaction origin as unknown unless the evidence set already includes a transaction receipt. Read only the selected fields from `chatgpt.desktop`, `com.anthropic.Claude.desktop`, and `cursor.desktop`.
4. For CLI surfaces, resolve only `$PATH`, package ownership, versioned link targets, public manager-receipt fields, `--version`, top-level `--help`, and the needed plugin/MCP/agent subcommand help. Run executable probes with filesystem writes and network denied.
5. On the work Mac, use only the existing preauthorized work-Mac SSH route with batch mode, strict existing-host-key checking, host-key updates disabled, connection sharing disabled, and forwarding cleared. Do not publish its identifier or connection fields. Resolve apps by public name or bundle ID, then read only `CFBundleShortVersionString`, `CFBundleVersion`, `CFBundleIdentifier`, and declared public URL schemes from `Contents/Info.plist`. Check only receipt presence.
6. Run Mac CLI help through the native no-write/no-network process sandbox. The Cursor bundled CLI needs only null-device output in addition; retain every other write denial. Keep Mac Cursor Agent metadata-only unless a credential-free help surface is exposed under the same authority.
7. Do not invoke GUI applications, auth/login/logout, doctor/status/whoami, model or usage listing, configured plugin/marketplace/MCP inventory, endpoint health checks, app-server sessions, inference, updates, installers, or uninstallers.

## Limitations and gaps

- No GUI/computer-use controller was available. ChatGPT Codex mode, Claude Desktop behavior, Cursor GUI behavior, and every GUI manager remain unobserved on both hosts.
- The three work-Mac desktop bundles have no observed Homebrew cask, Mac App Store, or matching Installer receipt. Their location and bundle identity are observed; installer origin is unknown.
- For the three Hatchery desktop packages, `pacman -Qi` established installed names/versions and recorded `Validated By: Signature`; `pacman -Si` established only current configured `cachyos` sync availability. No transaction receipt was observed, so installation transaction origin and installed-byte provenance remain unknown.
- No fresh package-signature verification or installed-byte integrity verification was executed by this inventory.
- The work-Mac `cursor` shim is not connected to the installed IDE on the noninteractive `$PATH`; only the app-bundled CLI help was available.
- The work-Mac Cursor Agent launcher attempted keychain access, so its exact-build help and manager capabilities were not inspected.
- Configured plugin, extension, marketplace, MCP, skill, agent, model, session, account, and authentication inventories were intentionally excluded. Capability documentation is not installed-inventory evidence.
- No live plugin load, skill discovery, MCP connection, delegation, session, inference, GUI action, or cross-surface handoff was performed. Installation does not establish behavioral support or qualification.
- Codex attempted PATH-alias creation during read-only version/help probes on both hosts; the enforced boundary denied every write.
- The current configured `cachyos` sync database exposes different versions from the installed Hatchery Claude Desktop and Cursor packages. This manifest records current availability and version drift without treating either as transaction-origin proof or making a fitting, update, or acceptance judgment.

## Authority boundary

This report may inform #42, [nisavid/agents#46](https://github.com/nisavid/agents/issues/46), and relevant [nisavid/provingkit#8](https://github.com/nisavid/provingkit/issues/8) work. It does not close those issues, change their design, or supply release, deployment, installation, migration, producer, credential, host, fitting, qualification, readiness, acceptance, rollback, retirement, or production authority.

Nothing here changes #76/#77 ownership, qualifies a coordinated distribution, validates a GUI or authenticated route, or authorizes a later probe with broader data or controls.
