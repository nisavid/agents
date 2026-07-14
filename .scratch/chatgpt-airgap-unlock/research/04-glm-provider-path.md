# GLM provider path in ChatGPT/Codex 26.707.71524

## Answer

The exact Apple-silicon app build can use a custom GLM 5.2 provider without an
OpenAI account or OpenAI API key. The working path is configuration-driven:

1. `config.toml` selects a model and provider.
2. The provider declares its base URL, credential environment variable,
   Responses API wire format, and `requires_openai_auth = false`.
3. The Electron main process launches the bundled Codex `app-server` with the
   app process environment and the selected `CODEX_HOME`.
4. The renderer calls `thread/start` with `modelProvider: null`; the host
   resolves the configured provider.
5. `turn/start` makes a bearer-authenticated `POST` to the provider's
   `/responses` endpoint.

No OpenAI-authentication prerequisite blocked this path. With the custom
provider loaded and its credential environment variable present,
`account/read` returned `account: null` and `requiresOpenaiAuth: false`.
Optional remote plugin refreshes failed under network denial but did not block
provider configuration, thread creation, or the provider request.

The app does not expose a general custom-provider editor in the inspected
renderer. The proven baseline is therefore file/config driven, not configured
through an in-app form. A cold app/host restart is required after changing the
provider configuration.

## Baseline and method

The inspected artifact was the official distribution at:

`https://persistent.oaistatic.com/codex-app-prod/ChatGPT-darwin-arm64-26.707.71524.zip`

| Item | Observed value |
| --- | --- |
| App version | `26.707.71524` |
| App build | `5263` |
| ZIP SHA-256 | `8981d832cfd061ff8fe80295cd675d5c283fd53ed2ea8c80cc9d1856e47cfe74` |
| `app.asar` SHA-256 | `d28f31b4bbb04c519be65c2af8277d8c5faf77b4239ee89b928f0a7423dacd84` |
| Bundled Codex version | `codex-cli 0.144.2` |
| Bundled Codex SHA-256 | `28699add67540b93390329a740649a9eb9bdbc5538d92c1679c8c6b6fa2c623c` |
| Signature check | `codesign --verify --deep --strict` passed |

All runtime probes used a separately named extracted app copy, disposable
`CODEX_HOME` directories, a dummy provider credential, an inert loopback HTTP
server, and outbound proxy variables pointing at a closed loopback port. The
installed application and normal user profile were not launched or modified.
The loopback server deliberately returned HTTP 400 after recording request
shape; it never implemented or proxied a model service.

## Configuration contract

This configuration was sufficient:

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

The probe also set these low retry values so a deliberate mock failure returned
quickly; they are not required by the provider contract:

```toml
request_max_retries = 1
stream_max_retries = 1
```

The effective keys and their roles are:

| Key | Role |
| --- | --- |
| `model` | Model string sent in the provider request. |
| `model_provider` | Selects the table below `model_providers`. |
| `model_providers.<id>.name` | Human-readable provider name. |
| `model_providers.<id>.base_url` | API root; the Responses client appends `/responses`. |
| `model_providers.<id>.env_key` | Environment variable containing the provider bearer credential. |
| `model_providers.<id>.wire_api` | Must be `responses` in this bundled Codex. |
| `model_providers.<id>.requires_openai_auth` | Makes local provider credentials sufficient when `false`. |

`wire_api = "chat"` failed deterministically with:

```text
wire_api = "chat" is no longer supported
```

The required correction is `wire_api = "responses"`.

With `GLM_API_KEY` absent, the host's credential diagnostic failed with:

```text
active model provider auth env var is missing
```

It still reported that the active provider did not require OpenAI auth. With the
variable present, the diagnostic reported that auth was provided by the active
model provider and loaded model `glm-5.2` from provider `glm`.

## Discovery and UI boundary

The Electron package entry point is `.vite/build/early-bootstrap.js`. Its main
process code resolves `CODEX_HOME` from the inherited environment, otherwise
using the user's `.codex` directory, and launches the packaged
`Contents/Resources/codex` binary as:

```text
-c features.code_mode_host=true app-server --analytics-default-enabled
```

The child inherits the app process environment, which is how `GLM_API_KEY`
reaches the host when the app itself is started with that variable.

The renderer-to-host bindings found in the extracted ASAR include:

| Renderer operation | App-server request |
| --- | --- |
| `read-config` | `config/read` |
| `list-models-for-host` | `model/list` |
| `read-model-provider-capabilities-for-host` | `modelProvider/capabilities/read` |

The normal local-thread creation code leaves `modelProvider` null so the host
can resolve it from configuration. Static searches found no general custom
provider ID, URL, or credential editor. The only renderer-side ephemeral
provider construction found was Copilot-specific and unrelated to this path.
The visible login surfaces offer ChatGPT or an OpenAI API key, not arbitrary
provider configuration.

This establishes a precise limitation: custom provider support is present in
the host and usable by the app, but this build's inspected UI does not provide a
general editor for it.

## Host RPC evidence

### Account state

After a cold start with the custom provider file and environment credential:

```json
{
  "method": "account/read",
  "params": {"refreshToken": false}
}
```

returned:

```json
{
  "account": null,
  "requiresOpenaiAuth": false
}
```

This is the key OpenAI-auth result: an OpenAI account can remain absent while
the app-server considers the selected provider authenticated.

### Config and model discovery

`config/read` returned `model = "glm-5.2"`, `model_provider = "glm"`, and the
custom `model_providers.glm` table with user-file origins. `model/list` still
returned only the bundled OpenAI model catalog; it did not add `glm-5.2` to the
catalog. The explicit model string nevertheless worked at thread creation.

The request:

```json
{
  "method": "modelProvider/capabilities/read",
  "params": {"modelProvider": "glm"}
}
```

returned:

```json
{
  "namespaceTools": true,
  "imageGeneration": true,
  "webSearch": true
}
```

These are the host's declared provider capabilities. They do not prove that a
particular GLM deployment implements image generation, web search, or every
tool behavior. Each capability still needs a real endpoint contract test.

### Thread creation

This renderer-shaped request was accepted. The disposable working directory is
redacted here rather than recording a machine-local path:

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
    "config": {},
    "personality": null,
    "ephemeral": true,
    "threadSource": "user",
    "experimentalRawEvents": false,
    "dynamicTools": null,
    "serviceTier": null,
    "runtimeWorkspaceRoots": []
  }
}
```

The response contained an effective thread with:

```json
{
  "model": "glm-5.2",
  "modelProvider": "glm"
}
```

The host logged this warning:

```text
Unknown model glm-5.2 is used. This will use fallback model metadata.
```

The warning did not block `thread/start` or the later provider request. It means
the host lacks first-class catalog metadata for the model and will use fallback
context/capability metadata.

### Provider invocation

Starting a turn on that thread caused the inert loopback server to observe:

```http
POST /v1/responses
Authorization: Bearer <redacted-provider-credential>
Content-Type: application/json
```

The JSON request used `model: "glm-5.2"`, contained seven tool definitions, and
had these top-level keys:

```text
client_metadata
include
input
instructions
model
parallel_tool_calls
prompt_cache_key
reasoning
store
stream
tool_choice
tools
```

The mock's deliberate HTTP 400 propagated through the turn as expected. This
proves provider selection, URL construction, bearer credential injection, and
Responses API request construction without sending data to any external
service.

## Reload and remote-bootstrap behavior

Writing the same values through `config/batchWrite` made them visible through
`config/read` with user-file origins. In the already running host,
`account/read` continued to return `requiresOpenaiAuth: true` after
`reloadUserConfig`. A cold host restart using the resulting file returned
`requiresOpenaiAuth: false`.

Therefore, initialization must write the provider configuration before starting
the app, or restart the app after changing it. Treating `config/batchWrite` plus
hot reload as sufficient would leave the account gate stale.

Under denied outbound access, the host attempted optional curated-plugin and
featured-plugin refreshes. The former failed at its Git sync and the latter
received an authentication failure. Neither failure blocked these local RPCs:

- `account/read`
- `config/read`
- `model/list`
- `modelProvider/capabilities/read`
- `thread/start`
- `turn/start`

No remote bootstrap response or cached OpenAI session was necessary for the
custom-provider request path.

## Resulting initialization sequence

For the exact baseline build, initialize the air-gapped machine in this order:

1. Create the target `CODEX_HOME` and its `config.toml` before app launch.
2. Set the model, provider selection, provider URL, `env_key`,
   `wire_api = "responses"`, and `requires_openai_auth = false`.
3. Supply the GLM credential under the named environment variable to the app
   process. Do not place an OpenAI credential or session in the baseline.
4. Cold-start the app so the bundled host evaluates account requirements from
   the configured provider.
5. Accept that `glm-5.2` may not appear in `model/list`; pass/select the explicit
   model string through the local thread path.
6. Validate the real endpoint with a narrow Responses API prompt before testing
   tools, images, search, or more complex workflows.

## What remains unknown

- Whether the actual self-hosted GLM 5.2 endpoint accepts every field and tool
  schema emitted by bundled Codex `0.144.2`.
- Which fallback metadata limits apply to context size, reasoning controls, or
  tool capabilities for the unknown `glm-5.2` model string.
- Whether a future notional UI should expose provider editing, or whether the
  supported product shape should intentionally remain configuration-managed.
- Which optional plugin/skill behaviors are fully local versus degraded without
  their own network dependencies; the provider path alone does not answer that
  broader workflow question.

These are compatibility and product-shape follow-ups. They do not reopen the
core question of whether OpenAI auth or remote bootstrap blocks a configured
custom provider in this build: it does not.
