Type: research
Status: closed
Assignee: nisavid

## Question

In app build `26.707.71524` (`5263`), what renderer, Electron main/preload, native-module, bundled-host, persisted-state, and IPC components participate in deciding whether a pristine user sees the login wall or the local Codex workspace, and which decision point is authoritative?

## Resolution

[Rewritten app authentication architecture](https://github.com/nisavid/agents/blob/a5155cf28c5d960a42953fbd8a5ea97d3a03e5fc/.scratch/chatgpt-airgap-unlock/research/02-auth-architecture.md)

The bundled Codex app-server supplies account state through `account/read`; the renderer normalizes the host field `requiresOpenaiAuth` into `requiresAuth`, defaulting to true when the host field is absent. The renderer is the authoritative route decision point: it redirects to login exactly when `authMethod` is absent and `requiresAuth` is true. Electron main and preload provide lifecycle and transport, persisted atoms affect only later onboarding/workspace progression, and native modules do not feed the login-versus-workspace branch in build `5263`.
