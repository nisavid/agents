# GLM 5.2 provider path in ChatGPT/Codex build 26.707.71524

## Decision

The exact app build has a complete config-driven path from the desktop renderer to a non-OpenAI model provider. A custom provider configured with `requires_openai_auth = false` causes the bundled host to report `requiresOpenaiAuth: false`, resolve renderer requests that leave `modelProvider` null to the configured provider, and send the turn to that provider's local `base_url` with the provider credential. OpenAI login is not an intrinsic prerequisite for this host path.

Provider setup is file/config driven. This build does not expose an in-app custom-provider editor, and its `model/list` response does not add an arbitrary configured model to the picker catalog. The configured model still reaches the provider, but the host warns that `glm-5.2` lacks model metadata and uses fallback metadata.

The provider endpoint must implement the Responses API. This host rejects `wire_api = "chat"` and sends turns to `<base_url>/responses`. If the self-hosted GLM service does not implement the request and streaming response contract exercised here, a separately scoped local compatibility shim is required.

## Scope and safety boundary

This investigation used only:

- static analysis of a separately named, never-launched copy of the exact app;
- the bundled `codex` executable under disposable `HOME` and `CODEX_HOME` directories;
- generated app-server protocol schemas;
- a loopback-only inert HTTP server that returned a deliberate error;
- a dummy provider credential.

It did not launch or modify the installed app, use a real profile or keychain, copy OpenAI state, contact a real GLM endpoint, or change any provider service.

## Artifact identity

| Item | Value |
|---|---|
| App version | `26.707.71524` |
| Build | `5263` |
| Architecture | Apple silicon |
| Official archive | `https://persistent.oaistatic.com/codex-app-prod/ChatGPT-darwin-arm64-26.707.71524.zip` |
| Archive SHA-256 | `8981d832cfd061ff8fe80295cd675d5c283fd53ed2ea8c80cc9d1856e47cfe74` |
| `Contents/Resources/app.asar` SHA-256 | `d28f31b4bbb04c519be65c2af8277d8c5faf77b4239ee89b928f0a7423dacd84` |
| `Contents/Resources/codex` SHA-256 | `28699add67540b93390329a740649a9eb9bdbc5538d92c1679c8c6b6fa2c623c` |
| Bundled Codex version | `codex-cli 0.144.2` |
| Signature verification | `codesign --verify --deep --strict` passed |

## End-to-end path

```text
$CODEX_HOME/config.toml
  model + model_provider + model_providers.<id>
                         |
                         v
Electron main process locates Contents/Resources/codex
  codex -c features.code_mode_host=true app-server --analytics-default-enabled
                         |
                         v
renderer RPCs: account/read, config/read, model/list,
               modelProvider/capabilities/read
                         |
                         v
renderer thread/start: model="glm-5.2", modelProvider=null
                         |
                         v
bundled host resolves modelProvider="glm"
                         |
                         v
turn/start -> POST http://127.0.0.1:<port>/v1/responses
              Authorization: Bearer <provider credential>
```

## Exact configuration contract

The minimum proven user configuration is:

```toml
model = "glm-5.2"
model_provider = "glm"

[model_providers.glm]
name = "GLM 5.2"
base_url = "http://127.0.0.1:8765/v1"
env_key = "GLM_API_KEY"
wire_api = "responses"
requires_openai_auth = false
```

The runtime probe also set retry counts to one to keep the inert failure deterministic:

```toml
request_max_retries = 1
stream_max_retries = 1
```

The exact host accepted those fields and returned them through `config/read`. `config/read` identified their origin as the user layer at `$CODEX_HOME/config.toml`.

Credential behavior is separate from OpenAI authentication:

- With `GLM_API_KEY` present, `codex doctor --json` reported `auth.credentials` as OK, stated that auth was provided by the active model provider, and stated `model provider requires OpenAI auth: false`.
- With `GLM_API_KEY` absent, the same check failed with `active model provider auth env var is missing` and instructed the operator to set `GLM_API_KEY`.
- The successful local request carried the dummy value as a bearer credential. No OpenAI token or account state was involved.

Because Electron spawns the host with a copy of `process.env`, an `env_key` credential must be available to the desktop app process, not merely to an unrelated interactive shell. The implementation plan must define a safe launch-time credential source for a normal macOS GUI launch.

## Desktop host discovery and launch

The packaged main-process bundle at `Contents/Resources/app.asar/.vite/build/src-HagpvBpE.js` does the following:

1. Resolves `CODEX_HOME` from the process environment, otherwise `$HOME/.codex`.
2. Locates `codex` in the packaged resources, including `Contents/Resources/codex`.
3. Builds the default argument vector:

   ```text
   -c features.code_mode_host=true app-server --analytics-default-enabled
   ```

4. Copies the Electron process environment and adds JSON logging and the desktop originator.
5. Spawns the bundled executable over stdio unless an explicitly selected local daemon or override transport applies.

No provider settings are hard-coded in Electron main. The bundled host owns user-config loading, provider validation, provider authentication, model resolution, and request construction.

## Renderer and UI behavior

The renderer uses the host as the source of truth. Static analysis of the extracted renderer bundles found handlers for:

- `read-config` -> `config/read`;
- `list-models-for-host` -> `model/list`;
- `read-model-provider-capabilities-for-host` -> `modelProvider/capabilities/read`;
- thread creation and turn submission through the app-server connection.

Relevant packed assets include:

- `webview/assets/app-initial~app-main~page-kMhXWEru.js`;
- `webview/assets/app-initial~app-main~new-thread-panel-page~appgen-library-page~hotkey-window-thread-page~ho~iufn7mg3-BWgIh_w6.js`;
- `webview/assets/app-initial~app-main~onboarding-page~hotkey-window-thread-page~quick-chat-window-page~chatg~gwqc41kz-CnQKtQ6U.js`.

The normal thread builder leaves `modelProvider` null. The only renderer-side provider construction found was a separate Copilot-specific override that injects an ephemeral provider. For normal local use, the host resolves null to the configured `model_provider`.

No `GLM`, custom-provider, provider URL, provider credential, or provider-ID editor was found in the renderer. The visible login surfaces accept ChatGPT login or an OpenAI API key, not a general provider definition. Therefore:

- custom-provider setup is proven through `$CODEX_HOME/config.toml` and host config RPCs;
- custom-provider setup through the app UI is not present in this build;
- changing provider config while the app is running is not sufficient for the auth route.

The last point is empirical: writing the provider with `config/batchWrite` and hot-reloading made `config/read` show the new provider, but the already-running host still returned `requiresOpenaiAuth: true`. After a cold host restart with the same file, `account/read` returned `requiresOpenaiAuth: false`. The route must therefore treat a full app/host restart after provider provisioning as required.

## App-server RPC evidence

### Cold-start account and config

An app-server cold-started from the user configuration above returned:

```json
{
  "id": 2,
  "result": {
    "account": null,
    "requiresOpenaiAuth": false
  }
}
```

`config/read` returned the effective values:

```json
{
  "model": "glm-5.2",
  "model_provider": "glm",
  "model_providers": {
    "glm": {
      "name": "GLM 5.2",
      "base_url": "http://127.0.0.1:8765/v1",
      "env_key": "GLM_API_KEY",
      "wire_api": "responses",
      "requires_openai_auth": false,
      "supports_websockets": false
    }
  }
}
```

This is the host-side condition that the renderer auth architecture can use to stay out of the OpenAI-login route. A full Electron route observation remains part of pristine-startup research; the provider contract itself does not require an OpenAI account.

### Model discovery

`model/list` succeeded without an OpenAI account but returned the bundled OpenAI-oriented catalog. It did not contain `glm-5.2`. Starting a GLM thread produced this warning:

```text
Unknown model glm-5.2 is used. This will use fallback model metadata.
```

That warning did not block thread creation or provider invocation. It does mean:

- the picker catalog is not authoritative for arbitrary configured providers;
- reasoning options, context limits, modalities, personality behavior, tool capability assumptions, and other model metadata use a fallback unless a compatible local model catalog is supplied;
- the eventual implementation must either accept and validate that fallback or provide a local catalog appropriate to GLM 5.2.

`modelProvider/capabilities/read` returned:

```json
{
  "namespaceTools": true,
  "imageGeneration": true,
  "webSearch": true
}
```

This is a host response, not proof that the self-hosted GLM service actually supports those capabilities. Endpoint compatibility must be validated separately.

### Renderer-shaped thread start

The decisive `thread/start` probe used the same provider shape as the normal renderer path:

```json
{
  "method": "thread/start",
  "params": {
    "model": "glm-5.2",
    "modelProvider": null,
    "allowProviderModelFallback": true,
    "cwd": "/path/to/disposable-workspace",
    "approvalPolicy": "never",
    "sandbox": "read-only",
    "runtimeWorkspaceRoots": [],
    "config": {},
    "personality": null,
    "ephemeral": true,
    "threadSource": "user",
    "experimentalRawEvents": false,
    "dynamicTools": null,
    "serviceTier": null
  }
}
```

The host response resolved both the thread and effective setting to:

```json
{
  "model": "glm-5.2",
  "modelProvider": "glm"
}
```

This proves that the renderer does not need to discover or transmit the provider ID itself for the configured path.

### Inert provider invocation

After `turn/start`, the loopback mock observed this redacted request summary:

```json
{
  "method": "POST",
  "path": "/v1/responses",
  "authorization": "Bearer <dummy provider credential>",
  "model": "glm-5.2",
  "tool_count": 7,
  "top_keys": [
    "client_metadata",
    "include",
    "input",
    "instructions",
    "model",
    "parallel_tool_calls",
    "prompt_cache_key",
    "reasoning",
    "store",
    "stream",
    "tool_choice",
    "tools"
  ]
}
```

The mock returned HTTP 400 with `inert local mock`; the host propagated that deliberate failure through `error` and `turn/completed`. The loop is fast, deterministic, agent-runnable, and red-capable at the provider seam.

The probe establishes request routing and shape only. It deliberately does not claim that a real GLM endpoint accepts every field, tool schema, streaming event, or response item this build emits.

## Responses API requirement

This exact host rejects the legacy value before making a request:

```text
Error loading config.toml: `wire_api = "chat"` is no longer supported.
How to fix: set `wire_api = "responses"` in your provider config.
```

The minimum endpoint contract is therefore:

1. accept `POST <base_url>/responses`;
2. accept bearer authorization from the configured provider credential source;
3. accept the Responses request fields and function-tool schemas produced by Codex 0.144.2;
4. stream Responses-compatible server-sent events when `stream: true`;
5. preserve the response-item and tool-call semantics the host expects across multiple turns.

If the self-hosted GLM service exposes only Chat Completions or a partial Responses dialect, the preferred route needs a loopback compatibility shim. Changing the app to emit Chat Completions is not available through configuration in this build.

## Remote bootstrap and offline behavior

With an empty disposable home and outbound proxy variables pointed at an inert loopback address, app-server startup attempted optional remote refreshes:

- curated plugin Git synchronization;
- a featured-plugin request to a ChatGPT backend.

Those attempts failed or returned unauthorized, but initialization, `account/read`, `config/read`, `model/list`, `modelProvider/capabilities/read`, `thread/start`, and the local provider request all continued. They are offline noise/degradation, not prerequisites for the local provider path.

The same probe showed that the bundled model catalog remains available without a successful remote model refresh. It is sufficient to start an unknown configured model with fallback metadata, but it does not describe GLM 5.2 accurately.

## Blocker classification

| Candidate prerequisite | Result | Classification |
|---|---|---|
| ChatGPT/OpenAI account | `account/read` returned no account and `requiresOpenaiAuth: false`; local request still occurred | Not a provider-path prerequisite |
| OpenAI API key | Not used | Not a provider-path prerequisite |
| Provider credential | Missing `env_key` failed diagnostics; dummy credential reached mock | Required local input |
| Remote provider bootstrap | No remote bootstrap was needed to resolve or invoke `glm` | Not required |
| Remote model catalog | Failure did not block; GLM used fallback metadata | Degraded, locally recoverable |
| Remote plugin catalog | Failure did not block provider invocation | Degraded, optional for provider path |
| In-app provider editor | Not present | Setup limitation; preprovision config |
| Host restart after provisioning | Hot reload left stale auth requirement; cold start fixed it | Required lifecycle step |
| Responses-compatible endpoint | `wire_api = "chat"` rejected; host posted to `/responses` | Required endpoint contract |
| Exact GLM tool/stream compatibility | Real endpoint deliberately not contacted | Still unknown |

## Implications for the route

The implementation-ready route should assume:

1. Provision the user provider configuration before the first app launch.
2. Provision the provider credential through a launch-time source visible to the Electron process.
3. Cold-start the app/host after any provider or `requires_openai_auth` change.
4. Preserve the normal renderer request shape; do not inject a provider ID into renderer calls unless later evidence requires it.
5. Treat the renderer login bypass and provider configuration as separate controls: the provider config makes the host truthfully report that OpenAI auth is unnecessary, while pristine-startup research determines whether the packaged route honors that response without another gate.
6. Validate or supply GLM model metadata locally instead of relying on the bundled OpenAI catalog.
7. Put any Responses/tool-schema translation in a loopback shim if the real endpoint cannot consume the exact request shape.
8. Disable or tolerate optional remote plugin/model refreshes for the air-gapped baseline; they are not on the critical provider path.

## Remaining unknowns

This ticket does not establish:

- whether the full pristine Electron UI reaches the main route with only this cold-start config; that belongs to `Trace pristine offline startup`;
- whether the actual self-hosted GLM 5.2 service implements the exact Responses streaming and tool-call dialect;
- whether fallback model metadata is acceptable for context limits, reasoning effort, modalities, and compaction;
- which local credential-delivery mechanism is safest and most usable for Finder/LaunchServices startup;
- whether optional remote refresh attempts should be disabled, intercepted, or merely allowed to fail quietly in the final air-gap design.

These unknowns do not prevent closing the provider-path research question. The discovery, configuration, validation, auth decision, host resolution, and invocation path are now known. Actual GLM compatibility and full-app startup validation should remain explicit checks in later tickets.

## Reproduction outline

Use a disposable app copy and isolated state. The key commands are shown with placeholders so the report remains machine-independent:

```sh
export APP_COPY=/path/to/ChatGPT-Codex-26.707.71524-5263.app
export CODEX_BIN="$APP_COPY/Contents/Resources/codex"
export CODEX_HOME=/path/to/disposable-home/.codex
export GLM_API_KEY='<provider-credential>'

"$CODEX_BIN" doctor --json
"$CODEX_BIN" app-server generate-json-schema --experimental --out /path/to/schema-output
"$CODEX_BIN" app-server --stdio
```

For the decisive feedback loop, start an inert listener on `127.0.0.1:8765`, initialize app-server, send the `thread/start` payload above, then send `turn/start` with a harmless text input. The expected red signal is an observed `POST /v1/responses` followed by the listener's deliberate error. No real provider or OpenAI credential is needed.
