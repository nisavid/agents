# Pristine offline startup trace

## Result

Exact build `26.707.71524` (`5263`) starts its bundled Codex app-server and renderer without an OpenAI session or successful network access, then deterministically stops at the ChatGPT login wall.

The decisive pristine app-server response is:

```json
{"account": null, "requiresOpenaiAuth": true}
```

The renderer subsequently stabilizes on:

```text
Sign in to ChatGPT Continue to sign in Sign in another way Sign up
```

This is an enforced authentication policy, not merely a visible modal. Hiding the login presentation would leave the host response and renderer route guard unchanged. A real no-auth mode requires the bundled host to return `requiresOpenaiAuth: false` with no account, or an equivalent change at that semantic boundary. No persisted onboarding value observed in this run substitutes for that response.

This ticket did not implement or test a bypass. It resolved the pristine startup behavior and the input that distinguishes a supported no-auth route from hidden UI. The ticket can close.

## Artifact and boundary

The probe used a separately named disposable copy of the exact signed arm64 artifact. It never launched or modified the installed application.

- archive SHA-256: `8981d832cfd061ff8fe80295cd675d5c283fd53ed2ea8c80cc9d1856e47cfe74`
- `Contents/Resources/app.asar` SHA-256 before and after probing: `d28f31b4bbb04c519be65c2af8277d8c5faf77b4239ee89b928f0a7423dacd84`
- bundle version: `26.707.71524` (`5263`)
- signing identity: `Developer ID Application: OpenAI OpCo, LLC (2DC432GLL2)`
- `codesign --verify --deep --strict`: valid before and after probing

Every run created fresh directories for:

- `HOME` and Core Foundation fixed home;
- `CODEX_HOME`;
- Electron/Chromium user data;
- temporary files and logs.

The child environment was rebuilt from an allowlist. It carried no OpenAI, GitHub, model-provider, proxy, shell-init, or application credentials from the operator environment. The outer macOS Seatbelt profile:

- denied every network connection to a remote IP;
- permitted only loopback observers;
- denied all reads and writes under the operator home;
- denied lookup of `securityd` services.

A local proxy recorded destination intent and always returned `502`; it never forwarded traffic. A loopback Chrome DevTools Protocol observer recorded renderer state and requests. The final process snapshot showed only the loopback CDP listener and no external TCP or UDP socket.

### Chromium inner-sandbox exception

Wrapping the signed Electron process in an outer Seatbelt profile caused Chromium's own nested sandbox initialization to fail. Valid runs therefore passed `--no-sandbox` to Chromium while retaining the outer Seatbelt boundary for the entire inherited process tree.

This exception changes Chromium's internal process-isolation topology. The trace is evidence for application startup, app-server IPC, renderer routing, attempted network destinations, and isolated persistence. It is not evidence about the production Chromium sandbox posture.

An earlier attempt also started with its current directory under the denied operator home. It raised `uv_cwd` and then hit the nested-sandbox failure. That attempt invalidated the harness only; none of its application behavior is used below.

## Reproduction loop

The captured probe is deliberately throwaway research code:

```sh
PROBE_DURATION_MS=12000 \
  .scratch/chatgpt-airgap-unlock/research/03-run-probe.sh
```

Two independent valid runs produced the same transition: blank loading state, loaded `Codex` document, then the stable login text above. The red-capable form asserts the desired no-login result and fails on the current barrier:

```sh
PROBE_DURATION_MS=9000 PROBE_EXPECT=no-login \
  .scratch/chatgpt-airgap-unlock/research/03-run-probe.sh
```

Observed verdict:

```text
LOGIN_WALL_OBSERVED=true
ACCOUNT_READ_OBSERVED=true
AUTH_JSON_PRESENT=false
```

The isolated protocol probe makes the host response directly observable:

```sh
.scratch/chatgpt-airgap-unlock/research/03-account-probe.py
```

Its sanitized response includes the initialize result, disabled remote-control status, and:

```json
{"id": 2, "result": {"account": null, "requiresOpenaiAuth": true}}
```

## Startup timeline

The application reported these milestones:

1. Main disarmed the legacy updater cache and began packaged production startup.
2. The local app-server connection moved from `disconnected` to `connecting`.
3. Main spawned bundle-relative `Contents/Resources/codex` with `app-server` over stdio.
4. The app-server initialize handshake completed successfully in `2040 ms`; the connection became `connected`.
5. The main frame finished loading and became ready to show. Its window-local metrics were `398 ms` and `453 ms`.
6. The renderer issued `account/read`; the response succeeded in `56 ms`.
7. The renderer mounted application routes after `3388 ms`.
8. The login text appeared by the second three-second state poll and remained unchanged for the rest of the 30-second observation.

The direct app-server probe independently completed `account/read` without a browser, credential, Keychain service, or successful network operation.

## Process and IPC topology

The stable process tree contained:

- the Electron main process;
- Chromium GPU, network, and storage service helpers;
- two renderer helpers;
- the bare-modifier monitor;
- bundle-relative `Contents/Resources/codex app-server`;
- crash-reporting helpers.

The renderer-to-host traffic observed through main was local stdio RPC. Method counts during the 30-second run were:

| Method | Count | Result relevant to startup |
| --- | ---: | --- |
| `account/read` | 3 | success; pristine response has no account and requires OpenAI auth |
| `thread/list` | 3 | success; empty local state does not block startup |
| `config/read` | 2 | success |
| `experimentalFeature/list` | 2 | success |
| `remoteControl/status/read` | 1 | success; disabled while unauthenticated |
| `model/list` | 1 | success |
| `mcpServerStatus/list` | 1 | success |
| `collaborationMode/list` | 1 | success |
| `configRequirements/read` | 1 | success |
| `experimentalFeature/enablement/set` | 1 | success |
| `fs/readFile` | 1 | protocol error `-32603`; not on the auth decision path |
| `plugin/list` | 3 | protocol error `-32600`; bundled marketplace reconciliation still completed |

The app-server's remote-control component announced initialization intent for a ChatGPT endpoint, then explicitly waited because remote control requires ChatGPT authentication. This was an attempted initialization path, not a successful connection.

## Network behavior

Two classes of attempts were visible, and neither reached an external peer.

Clients honoring the injected proxy attempted loopback `CONNECT` requests for:

- `chatgpt.com`;
- `github.com` using Git;
- `api.github.com`;
- `ab.chatgpt.com`.

The proxy returned `502` for every request and did not forward. The exact reason for the GitHub checks was not established; they occurred before the login route stabilized and carried no inherited credentials.

Other Chromium and renderer clients attempted direct requests that Seatbelt rejected with `net::ERR_ACCESS_DENIED`, including:

- Statsig initialization and event registration;
- ChatGPT task and usage routes;
- browser telemetry;
- Chromium time, account, component, and optimization services.

Statsig retries continued after the login wall appeared. Hosted task and usage calls also failed after route mount. These failures demonstrate that visible login UI and background hosted-service attempts are separable from the local `account/read` decision.

## Isolated persistence

The first 30-second run created approximately `111 MB` under isolated `CODEX_HOME` and `8.5 MB` under isolated Electron user data.

Notable `CODEX_HOME` outputs were:

- `.codex-global-state.json` and its backup;
- `config.toml` with the local bundled marketplace registration;
- a fresh installation identifier;
- state, goals, memories, logs, and desktop SQLite databases;
- bundled system skills and the bundled LaTeX marketplace;
- a local Computer Use application payload.

The global state recorded first-seen and local migration data plus benign renderer atoms for migration/update announcements. It contained no auth method and no override of `requiresOpenaiAuth`.

Electron created ordinary fresh-profile databases and caches, including `Local State`, `Preferences`, cookies databases, crash state, and Sentry session metadata. The explicit Electron user-data switch prevented the valid runs from creating the default Chromium tree under the isolated `HOME` path.

No `auth.json`, Codex conversation session, OpenAI credential, or copied bootstrap state appeared. The only file named as a session was Sentry's local diagnostic session metadata, not an authentication session.

## Keychain implications

The outer profile denied access to `securityd`, so the probe could neither read nor write the operator login Keychain. Startup and pristine `account/read` still succeeded, returned no account, and reached the login wall. Therefore no readable Keychain item is required to establish the pristine logged-out state.

This does not establish how a successful ChatGPT login is persisted. The exact split among `auth.json`, Keychain, cookies, and other credential stores remains unknown because creating or importing a real session was intentionally out of scope. No claim is made that the application attempted zero Keychain calls; only that none could succeed and the logged-out path remained functional.

## Decision and remaining unknowns

The startup barrier is now empirically located at the same semantic boundary found statically:

```text
local app-server account/read
  -> account = null
  -> requiresOpenaiAuth = true
  -> renderer authMethod = null
  -> login route
```

For a supported no-auth route, the minimal missing input is `requiresOpenaiAuth: false` while `account` remains null. Hiding, removing, or navigating around the login component is insufficient because the renderer root guard will continue to enforce the host policy.

Parallel [Trace GLM provider path](https://github.com/nisavid/agents/blob/81bd2bfbff803ad50c7d4111ca00a062953d89fc/.scratch/chatgpt-airgap-unlock/research/04-glm-provider-path.md) research subsequently established that a file-configured custom provider with `wire_api = "responses"`, `requires_openai_auth = false`, and its own provider credential naturally makes a cold app-server return `account: null` with `requiresOpenaiAuth: false` and proceed to local provider invocation. That resolves whether the bundled host supports the semantic input; it does not yet validate the full configured Electron route.

The remaining research must determine:

- whether the full configured Electron route reaches a usable workspace or exposes another onboarding/configuration gate;
- why pristine startup performs GitHub checks and whether they are optional local feature discovery;
- which credential stores are used after an actual login, if later work ever needs that boundary;
- how the preferred architecture preserves the signed Electron/Chromium sandbox topology, which this outer-sandbox probe did not evaluate.

## Checks

- exact version/build and artifact hashes verified;
- bundle signature verified before and after launch;
- Seatbelt profile compiled with a runtime-supplied operator-home parameter;
- shell syntax, Python bytecode compilation, and Node syntax checks passed;
- two independent valid GUI runs reproduced the same login-wall state;
- the red-capable no-login assertion failed on the exact barrier as intended;
- the direct app-server probe returned `account: null` and `requiresOpenaiAuth: true`;
- valid-run process cleanup left no disposable probe process running;
- no raw hostname, installation identifier, credential, or temporary log was committed.
