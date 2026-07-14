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

## Not yet specified

- Whether the authoritative startup barrier lives in renderer JavaScript, Electron main/preload code, a native module, the bundled Codex host, or a combination cannot be specified until static and runtime tracing agree.
- The exact cross-version discovery strategy depends on whether the stable identifier is a semantic gate name, a control-flow shape, a resource contract, or a native function signature.
- Any local bootstrap/session shim cannot be specified until the app's pristine startup requests, persisted state, and failure transitions are known.
- Provider-compatibility work beyond existing GLM 5.2 support cannot be specified until the self-hosted provider path is traced through both UI and bundled host.
- Update, rollback, and integrity-repair mechanics depend on which signed resources the preferred route must modify.

## Out of scope

- Implementing, distributing, or publishing the replacement patcher in this effort.
- Enabling OpenAI-hosted features, paid entitlements, or remote services that are unavailable in the air-gapped environment.
- Bypassing model-provider authorization; locally supplied GLM credentials remain required.
- Supporting operating systems, CPU architectures, or app builds other than the exact baseline before the baseline route is established.
- Modifying the self-hosted GLM 5.2 service unless provider tracing proves a narrowly defined compatibility shim is required; that would become a separately scoped effort.
