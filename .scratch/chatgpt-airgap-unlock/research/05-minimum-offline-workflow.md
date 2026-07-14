# Minimum offline workflow capability classification

## Answer

The minimum offline Codex workflow is not intrinsically dependent on an OpenAI session or an OpenAI-hosted bootstrap service in exact build `26.707.71524` (`5263`). Its critical path is local:

1. provision a custom model provider before the first cold start;
2. let the bundled host truthfully return `account: null` and `requiresOpenaiAuth: false`;
3. use the app's local project, Git worktree, thread, mode, permission, skill, plugin, and configuration machinery; and
4. send model turns to a Responses-compatible local endpoint with a separately supplied provider credential.

The only likely narrow shim on that path is a loopback Responses/tool-stream compatibility adapter if the real GLM 5.2 service does not implement the dialect emitted by bundled Codex `0.144.2`. The app itself does not need an account bootstrap shim, fabricated OpenAI session, renderer gate replacement, native patch, or modified bundle to make the bundled host's supported no-OpenAI-auth path available.

The workflow is still degraded in three deliberate ways: custom-provider provisioning has no in-app editor, GLM 5.2 is absent from the bundled model catalog and therefore uses fallback metadata, and individual local skills/plugins can depend on networked tools even though their discovery and configuration are local. OpenAI-hosted tasks, remote control, remote extension catalogs and sharing, usage/billing, and similar hosted features remain hosted-only; they are not prerequisites for the minimum workflow.

Ticket **Classify the minimum offline workflow** can close. Remaining runtime checks belong in the implementation plan and validation matrix, not in another capability-classification pass.

## Classification and evidence rules

The canonical classes are the terms in the effort-local [context glossary](../CONTEXT.md):

- **Inherently local**: the required backing behavior is already local; ordinary local initialization is allowed.
- **Locally recoverable with a narrow shim**: the missing boundary is a bounded local protocol adaptation, not a hosted-service reimplementation.
- **Degraded but usable**: the local core works while metadata, discovery, presentation, or optional behavior is absent.
- **Impossible without an unavailable hosted service**: the behavior is the hosted service itself.

Evidence grades used in the matrix:

- **A — exact runtime**: observed on exact build 5263 in a disposable state root with egress denied or confined to an inert loopback endpoint.
- **B — exact host runtime plus exact static ownership**: the local host behavior ran, and exact renderer/main code identifies its consumer or producer, but the complete configured GUI journey was not exercised.
- **C — exact static/protocol**: exact packaged code, settings, or generated protocol establishes the local owner and contract; runtime completion remains open.
- **H — historical lead only**: legacy gate names or behavior claims are useful for discovery but do not classify build 5263 by themselves.

No positive classification below rests only on grade H evidence.

## Evidence base

- [Reconstruct the legacy patcher contract](https://github.com/nisavid/agents/blob/76edfcc3dd5c75a16d4a142df49677d8def39ee2/.scratch/chatgpt-airgap-unlock/research/01-legacy-patcher-contract.md) defines the safety envelope and proves why visible historical gates are not backing-behavior evidence.
- [Map the rewritten app auth architecture](https://github.com/nisavid/agents/blob/a5155cf28c5d960a42953fbd8a5ea97d3a03e5fc/.scratch/chatgpt-airgap-unlock/research/02-auth-architecture.md) locates the exact renderer route condition and bundled-host account source.
- [Trace pristine offline startup](https://github.com/nisavid/agents/blob/716456a6af776b72a6643f85a0019f79bfb0564a/.scratch/chatgpt-airgap-unlock/research/03-pristine-offline-startup.md) proves the logged-out login wall, local startup topology, optional failed network traffic, and fresh local state.
- [Trace the GLM provider path](https://github.com/nisavid/agents/blob/81bd2bfbff803ad50c7d4111ca00a062953d89fc/.scratch/chatgpt-airgap-unlock/research/04-glm-provider-path.md) proves the custom-provider account policy, provider selection, credential requirement, and Responses request seam.
- [Assess integrity, signing, and security](https://github.com/nisavid/agents/blob/59e9fa5800b2806064236d1bab0e5f5845681e96/.scratch/chatgpt-airgap-unlock/research/06-integrity-signing-security.md) establishes that supported config is the preferred unmodified-bundle route and constrains any eventual shim or mutation.
- A ticket-specific exact-build app-server probe used a fresh `HOME`, `CODEX_HOME`, Git workspace, dummy provider credential, copied local marketplace, and explicit egress denial. It made no request to a real provider and did not launch the production app. The probe established offline mode and permission enumeration, local skill and marketplace discovery, and thread persistence across a complete host restart.

The ticket-specific probe also sharpened one important thread distinction: `thread/start` allocates an in-memory thread, but it does not enter persistent history until the first user message materializes the rollout. After an inert `turn/start`, `thread/list` and `thread/read` found the thread after restart, and `thread/resume` restored the interrupted turn.

## Capability matrix

| Minimum capability | Class | Exact control or gate | Backing dependency | Offline behavior and initialization | Evidence | Unresolved validation |
| --- | --- | --- | --- | --- | --- | --- |
| Launch without an OpenAI session | **Inherently local** | Bundled host `account/read` returns `account: null`, `requiresOpenaiAuth: false`; renderer guard admits the app when `authMethod` exists **or** `requiresAuth` is false | Cold-started bundled host plus a configured provider whose `requires_openai_auth` is false | Preprovision provider config before launch and cold-start the host. A pristine unconfigured host instead returns `requiresOpenaiAuth: true` and reaches `/login`. No fake account is needed. | **B** | Run the complete configured Electron first-launch route with egress denied and prove it reaches project/workspace selection without another presentation gate. |
| Provision GLM 5.2 | **Degraded but usable** | `$CODEX_HOME/config.toml`: `model`, `model_provider`, and `[model_providers.<id>]`; host `config/read` reports the effective user layer | Local configuration file | Provision before first launch. There is no general custom-provider editor in this renderer, and hot reload did not update the already-running auth policy; a cold restart is required. | **A** host, **C** renderer | Decide the operator-facing provisioning mechanism and validate it without requiring a shell-oriented manual workflow. |
| Supply GLM provider authorization | **Inherently local** | Provider `env_key`; host diagnostics reject a missing value; Electron passes its launch environment to the bundled host | Locally scoped provider credential | Make the credential available to the GUI-launched Electron process. It is independent of OpenAI account state and was observed as bearer authorization at the loopback provider seam. | **A** | Select a Finder/LaunchServices-compatible secret source that does not persist the value in committed config, logs, or inherited broad process state. |
| Invoke GLM 5.2 | **Locally recoverable with a narrow shim** | Configured provider resolves renderer `modelProvider: null` to `glm`; bundled host sends `POST <base_url>/responses` with model, instructions, tools, and streaming enabled | Self-hosted GLM endpoint implementing the Codex 0.144.2 Responses/tool-stream dialect, or a loopback adapter | Direct use works if the endpoint is compatible. Otherwise adapt only the Responses request, SSE events, response items, and tool-call semantics on loopback. `wire_api = "chat"` is rejected. | **A** through inert request capture | Test the real GLM 5.2 service for request fields, streaming, cancellation, multi-turn tool calls, error recovery, context limits, and compaction. This determines whether the shim is needed. |
| Select GLM in the model experience | **Degraded but usable** | `model = "glm-5.2"` selects the model even though `model/list` omits it; `allowProviderModelFallback` permits host fallback metadata | Local config plus bundled fallback model metadata | The thread starts and reaches the provider, but the picker/catalog is not authoritative and model capabilities may be described incorrectly. | **A** | Supply and validate local GLM metadata for context window, reasoning options, modalities, compaction, and tool assumptions, or explicitly accept each fallback. |
| Add and reopen local projects | **Inherently local** | Main/renderer local state keys `local-projects`, `electron-saved-workspace-roots`, `electron-workspace-root-labels`, `active-workspace-roots`; local filesystem and Git-root discovery | Local directories, Git, and desktop global state | Select an existing local directory; the project and workspace-root records are local. Fresh startup already creates its local global state without account or network success. | **C** | Exercise add, remove, relabel, restart, non-Git directory handling, nested roots, and inaccessible-root recovery in the configured GUI route. |
| Create and manage local worktrees | **Inherently local** | Renderer/main Git-worker requests `create-worktree`, `codex-worktrees`, `list-worktrees`, `set-worktree-owner-thread`, and deletion/cleanup operations; settings `git-worktree-root`, `worktree-auto-cleanup-enabled`, `worktree-keep-count` | Local Git executable, repository metadata, filesystem, optional local setup scripts | Worktree allocation, detached creation, owner tracking, enumeration, and cleanup are local. Setup or cleanup scripts can degrade independently if the repository itself makes them fetch remote dependencies. | **C** | Run create/use/restart/archive/cleanup against a disposable local repository with remote URLs absent and confirm the UI exposes the flow in no-auth mode. |
| Create a local thread | **Inherently local** | App-server `thread/start` with local `cwd`, provider, approval policy, sandbox or permission profile, and workspace roots | Bundled host, local workspace, local state store | Exact host startup returned an idle non-ephemeral GLM thread with workspace-write sandbox and no network access. The thread becomes durable only after its first user message. | **A** | Confirm the configured Electron composer materializes the same local thread and does not require a hosted thread-create route. |
| Retain and list local thread history | **Inherently local** | `history.persistence = "save-all"`; local rollout JSONL and state database; `thread/list`, `thread/read`, `thread/turns/list`, and search RPCs | Writable isolated `CODEX_HOME` | After one inert local-provider turn materialized a rollout, list/read returned it without network access. An interrupted provider turn remained valid local history. | **A** | Validate pagination, naming, search, archive/delete, corrupted index repair, and longer completed histories. |
| Restart and resume a local thread | **Inherently local** | `thread/resume` by thread id; local rollout path plus state database | Persisted local history and the same local workspace/provider configuration | A second fresh app-server process listed, read, and resumed the materialized thread and its interrupted turn. No account or hosted thread store was involved. | **A** | Validate resume after a completed GLM response, after app crash, after workspace relocation, and after provider/config version changes. |
| Select Default and Plan modes | **Inherently local** | `collaborationMode/list` returns local `default` and `plan` presets; `turn/start.collaborationMode` selects a preset | Bundled preset definitions; the selected turn still depends on the configured model provider | Exact host enumeration succeeded offline: Default had no forced effort and Plan selected medium effort. The mode control is local; successful model execution remains provider-dependent. | **A** enumerate, **C** end-to-end select | Run one completed GLM turn in each mode and verify local instructions, reasoning effort, transitions, and persisted thread settings. |
| Choose and enforce local permission profiles | **Inherently local** | `permissionProfile/list`; `thread/start`/`turn/start` `permissions` or legacy `sandbox`; `approvalPolicy`, `approvalsReviewer`, `runtimeWorkspaceRoots`; host tool-approval callbacks | Local sandbox/policy engine, filesystem roots, and the desktop approval interaction | Exact host listed `:read-only`, `:workspace`, and `:danger-full-access`. A workspace-write thread returned network denied and the selected local root. Policy selection and enforcement are local. | **A** enumerate/start, **C** approval interaction | Exercise read denial, allowed workspace write, command escalation, `request_permissions`, cancellation, and persistence through the actual desktop prompt loop. Never infer enforcement from a visible prompt alone. |
| Discover bundled and repo-local skills | **Inherently local** | `skills/list`, `skills/config/write`, `skills/extraRoots/set`, and local skill roots | Local `SKILL.md` files and local settings | A fresh host discovered a synthetic repo skill under `.agents/skills` plus the locally materialized system skills without network access. Skills are content plus dependency declarations, not hosted entitlements. | **A** | Validate enable/disable, extra roots, reload, explicit invocation, and one completed GLM turn that consumes a purely local skill. |
| Execute arbitrary local skills | **Degraded but usable** | Skill selection enters the turn as local skill input; each skill's declared tools and workflow determine its backing behavior | Local files for the skill itself; capability-specific binaries, MCP servers, network services, or permissions for its actions | Purely local skills remain usable. A skill whose purpose is an unavailable web API, hosted documentation, image service, or installer download cannot complete that part offline; its presence does not make the dependency local. | **C** plus **A** discovery | Classify the initial skill allowlist by dependency and run representative file-only, local-binary, local-MCP, and blocked-network cases. |
| Discover and configure local plugins | **Degraded but usable** | Local marketplace config; `plugin/list/read/install/uninstall`, `plugin/skill/read`, skill and MCP config RPCs; renderer plugin settings depend on plugin availability | Local marketplace manifest and local plugin payload | Exact host listed a copied materialized bundled local marketplace and its LaTeX plugin with no load error or hosted catalog. Local discovery is viable; the direct probe did not claim every bundled candidate is seeded or installed in no-auth GUI startup. | **A** discovery, **C** UI/install | Validate Electron marketplace seeding, no-account local install/uninstall, restart, plugin settings visibility, and local-only plugin execution. |
| Execute arbitrary local plugins | **Degraded but usable** | Plugin manifest composes skills, MCP servers, hooks, apps, and other interfaces; each interface has its own backing contract | Plugin-local code plus any declared binaries, services, credentials, or permissions | Local components can work offline. Components such as optional TeX Live downloads, remote research, hosted deployment, remote catalogs, sharing, or web MCP servers degrade or become hosted-only independently. | **C** | Produce a component-level allowlist for the bundled/user plugins selected for the baseline and prove their required binaries and services are locally available. |
| Access the settings needed by the minimum workflow | **Degraded but usable** | Host `config/read`, `config/value/write`, `config/batchWrite`, `skills/config/write`, marketplace/MCP reload; desktop global-state keys for projects/worktrees; packaged settings routes for worktrees, plugins, skills, MCPs, and permissions | Local config layers, desktop global state, and renderer settings routes | Every required value has a local storage or RPC contract. Provider definition and credential setup are not fully represented by the UI, so the offline baseline needs preprovisioned config and an external local secret source. | **B/C** | Run the configured GUI and record exactly which settings pages are reachable without account state, which writes persist, and which sections merely show hosted subscription/profile controls. |

## Hosted-only surfaces are not hidden prerequisites

The exact app attempts ChatGPT, GitHub, Statsig, telemetry, plugin refresh, and other network work during startup, but the startup and provider traces prove those failures do not block local host initialization, account policy, config reads, thread creation, or provider invocation. They must be treated by behavior:

| Surface | Classification | Reason |
| --- | --- | --- |
| ChatGPT remote tasks, usage/billing, workspace messages, and authenticated remote control | **Impossible without an unavailable hosted service** | Their backing records and control plane live in the unavailable ChatGPT service. Local route visibility cannot supply them. |
| Remote plugin catalogs, sharing, and remote marketplace identities | **Impossible without an unavailable hosted service** | The desired behavior is remote discovery or collaboration itself. A local marketplace is an alternative capability, not an emulation of the hosted catalog. |
| Statsig evaluation and telemetry | **Impossible without an unavailable hosted service**, but nonessential | Offline failure removes remote evaluation and reporting. It is not evidence that the local capability behind a packaged surface is absent. |
| GitHub-backed convenience actions | **Degraded but usable** | Local Git projects and worktrees remain local; remote PR, collaborator, and repository queries are unavailable unless a separately reachable Git service is intentionally provided. |
| Network-dependent skill or plugin components | **Component-specific: locally recoverable or hosted-only** | A loopback service can recover a protocol-compatible dependency; a behavior whose purpose is a particular unavailable hosted product cannot be recovered by showing its UI. |

None of these hosted-only surfaces is a prerequisite for the stated minimum offline workflow.

## Required initialization order

1. Verify the exact pristine 5263 archive and run only a separately named copy in an isolated account or VM boundary.
2. Create isolated Electron user data and `CODEX_HOME`; do not copy account, Keychain, cookie, or app-group state.
3. Before first launch, write the local provider definition with `requires_openai_auth = false`, `wire_api = "responses"`, and the selected local model.
4. Make the provider credential available through the chosen local launch-time secret source.
5. Start the real GLM endpoint or the authenticated loopback compatibility shim, then cold-start the app.
6. Select a local project; initialize any project-local skills, local marketplace entries, permission profile, and worktree root.
7. Materialize the first durable thread by submitting a user message; thread allocation alone is not durable history.
8. Allow optional hosted refreshes to fail closed or disable them through supported configuration where available. Never replace them with copied OpenAI session state.

## Decisions for the implementation plan

- **Preferred route:** unmodified vendor bundle plus local configuration. This preserves the vendor signature, notarization, updater trust, Team-ID identity, and Electron security posture.
- **Startup authority:** use the bundled host's supported `requiresOpenaiAuth: false` result. Do not hide the login component or patch a historical feature gate.
- **Provider boundary:** preprovision config; cold restart after provider/auth-policy changes; provide the provider credential separately.
- **Compatibility boundary:** if needed, adapt only the local Responses/tool-stream protocol on loopback. Bind loopback only, authenticate each launch, redact values, and deny egress.
- **Model metadata:** do not equate successful fallback with correct GLM behavior. Provide or validate a local catalog before calling the workflow implementation-ready.
- **Local extension policy:** classify skills and plugin components by their backing dependencies. Local discovery does not certify every component as offline-capable.
- **Hosted surfaces:** leave genuinely hosted features unavailable and make their failure explicit. They do not belong in the minimum-workflow acceptance gate.
- **No bundle mutation is justified by current evidence.** A renderer, ASAR, native, entitlement, or signing change becomes eligible only if the configured full-GUI validation falsifies the supported local route and exact ownership evidence identifies the smallest necessary surface.

## Validation gates carried forward

The implementation plan must keep these red-capable checks explicit:

1. configured full Electron startup reaches the local project/workspace route with no OpenAI session and egress denied;
2. a real GLM 5.2 turn streams, uses tools, handles approval, cancels, recovers from error, and resumes after restart;
3. model metadata matches GLM limits and behavior rather than silently relying on OpenAI fallback assumptions;
4. local project and worktree create/use/archive/cleanup survive restart without remote Git;
5. permission enforcement is proven through allowed and denied operations, not through prompt visibility;
6. a durable completed thread lists, reads, searches, archives, and resumes locally;
7. Default and Plan each produce the expected local turn settings;
8. selected local skills and plugins work from preseeded local payloads, while network-dependent components fail explicitly;
9. optional external requests neither receive credentials nor block the local workflow; and
10. the production app, normal profile, Keychain, app-group data, and updater remain untouched.

These are validation tasks, not unresolved classification decisions. The capability map is now sufficiently sharp for the final implementation-ready plan.
