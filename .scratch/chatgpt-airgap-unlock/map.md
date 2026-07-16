Label: wayfinder:map

## Destination

Produce an implementation-ready, empirically validated plan for running the exact Apple-silicon ChatGPT/Codex app build `26.707.71524` (`5263`) on a pristine, never-authenticated, air-gapped macOS machine with a self-hosted GLM 5.2 endpoint and the minimum viable offline Codex workflow.

## Notes

- This effort plans and validates the route; it does not deliver or publish a replacement patcher.
- The baseline artifact is the exact signed app build above. Cross-version resilience is considered only after the baseline mechanism is understood.
- The legacy evidence source is `origin/ivan/chatgpt-unflag`, especially `tooling/chatgpt-ffs`. Treat its gate IDs, byte signatures, and package assumptions as historical evidence that must be re-verified.
- Keep three concepts separate: the startup authentication barrier, remotely evaluated feature-configuration gates, and runtime capabilities that genuinely require authenticated services.
- Minimum viable offline Codex workflow means: launch without an OpenAI session; configure and use GLM 5.2; use local projects and worktrees; retain local threads; select modes; handle local permissions; use locally available skills/plugins; and access the settings required for those behaviors.
- Do not treat visible UI as proof that its backing behavior works offline. Record each capability as local, locally recoverable with a shim, degraded, or dependent on an unavailable hosted service.
- Use `wayfinder` and `domain-modeling` throughout. Use `checkpointing-and-publishing-git-work` for every repository session. Use `diagnosing-bugs` and `systematic-debugging` for runtime investigation, `prototype` for disposable validation, and an appropriate `codex-security` skill for trust-boundary and signing analysis. Use `context7-mcp` only for current public documentation.
- Never test against the production app bundle or a real user profile. Use a copy, an isolated profile, and an explicit network-denial/observation setup.
- Do not copy OpenAI credentials, session state, or expiring bootstrap data into the air-gapped baseline.

## Decisions so far

- [Reconstruct the legacy patcher contract](issues/01-reconstruct-legacy-patcher-contract.md) — Keep the artifact-bound, integrity-aware, rollback-first lifecycle as requirements; rediscover every historical gate, path, entitlement, and native control point against the exact baseline.
- [Map the rewritten app auth architecture](issues/02-map-rewritten-app-auth-architecture.md) — The bundled app-server supplies `account/read`; the renderer routes to login only when no auth method exists and OpenAI auth remains required.
- [Trace pristine offline startup](issues/03-trace-pristine-offline-startup.md) — A fresh isolated profile returns no account with OpenAI auth required and deterministically reaches the login wall; only changing the semantic host policy, not hiding UI, enables no-auth startup.
- [Trace the GLM provider path](issues/04-trace-glm-provider-path.md) — A cold file-configured custom provider naturally disables the OpenAI-auth requirement and reaches a loopback Responses endpoint; UI provisioning and arbitrary model metadata remain absent.
- [Classify the minimum offline workflow](issues/05-classify-minimum-offline-workflow.md) — The minimum workflow is predominantly local and needs no account shim; the exact-build-bound `defaultPath` seam is confined to disposable native-picker validation.
- [Assess integrity, signing, and security](issues/06-assess-integrity-signing-and-security.md) — Prefer a pristine-bundle configuration or loopback-service route; any derived bundle must use exact-artifact binding, complete least-privilege signing, strict fail-closed verification, and whole-copy rollback.
- [Select a local smoke-test model](issues/10-select-local-smoke-model.md) — Use Qwen3.5-2B-OptiQ-4bit at immutable revision `adc8669eb431e3168aeb4e320bd7b757914350e2` through an isolated OptiQ runner; it is the smallest compared candidate with a credible tool-calling margin and passed deterministic Responses text, function-call, and function-output checks.
- [Choose the preferred architecture](issues/07-choose-bypass-architecture.md) — Use the closed profile-driven runner around a separately named verified copy; keep the source and installed app immutable, permit only the exact fixed-width disposable native-picker seam, use OptiQ locally, and keep GLM explicit with no fallback.
- [Harden the Responses gateway](issues/11-harden-responses-gateway.md) — `codex-ns-proxy` is now an authenticated, allowlisted, secret-separating loopback boundary with bounded slow-prefill liveness, exact semantic and transport terminal handling, deterministic coverage, and an end-to-end OptiQ validation through the preferred route.
- [Stabilize local Responses stream liveness](issues/15-stabilize-local-responses-stream-liveness.md) — Emit downstream SSE heartbeats during silent prefill, configure a longer low-memory upstream bound explicitly, log semantic completion immediately, and preserve the exact data-only `[DONE]` transport sentinel; the final gateway and cold-resume workflow pass together.
- [Prototype the native GUI probe](issues/14-prototype-native-gui-probe.md) — One PID-bound helper proves zero native sheets, remains alive across the renderer's exact final picker request, and selects only the canonical fixture through `AXSelectedChildren`; deterministic starting location uses an exact-build-bound seam confined to the disposable copy.

## Active work

- [Validate the preferred route](issues/08-validate-preferred-route.md) — Complete worktree, permission, project-local skill, reasoning-label, production-isolation, and target-GLM acceptance against the pinned local route.
- [Validate the offline GUI workflow](issues/12-validate-offline-gui-workflow.md) — Complete worktree, permission, project-local skill, and reasoning-label acceptance against the pinned local route.
- [Validate production isolation](issues/13-validate-production-isolation.md) — Preserve the vendor Chromium sandbox and signed code posture while a disconnected VM or true air gap makes hosted egress unavailable.

## Not yet specified

- Real GLM 5.2 compatibility with bundled Codex's exact Responses streaming and tool-call dialect remains environment-dependent because that air-gapped endpoint is not locally available.
- The configured renderer route still needs GUI-level validation for worktrees, permission decisions, project-local skills, and reconciliation of the appended reasoning-selection label with the pinned catalog.
- Local semantic validation currently requires reconciling outer network confinement with Chromium's nested sandbox. Production acceptance must preserve the vendor Chromium sandbox on a disconnected VM or true air-gapped machine.
- Cross-version discovery and migration remain deferred until a second exact app build is explicitly brought into scope.

## Out of scope

- Publishing a redistributable replacement patcher or derived application build; the exact-build-bound disposable validation seam is prototype evidence only.
- Enabling OpenAI-hosted features, paid entitlements, or remote services that are unavailable in the air-gapped environment.
- Bypassing model-provider authorization; locally supplied GLM credentials remain required.
- Supporting operating systems, CPU architectures, or app builds other than the exact baseline before the baseline route is established.
- Modifying the self-hosted GLM 5.2 service unless provider tracing proves a narrowly defined compatibility shim is required; that would become a separately scoped effort.
