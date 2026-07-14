# Rewritten app authentication architecture

## Result

Build `26.707.71524` (`5263`) makes the pristine-login decision in renderer JavaScript, using account state returned by the bundled Codex app-server.

The authoritative control flow is:

1. Electron main starts the local app-server backed by `Contents/Resources/codex` and connects it before creating the primary window.
2. Main creates the primary `BrowserWindow` regardless of account state and loads the packaged renderer.
3. The renderer auth provider sends app-server request `account/read` with `refreshToken: false`.
4. The renderer normalizes the response into `authMethod` and `requiresAuth`.
5. The renderer onboarding target function returns `login` exactly when loading is complete, `authMethod` is absent, and `requiresAuth` is true.
6. The renderer root guard redirects to `/login`, `/welcome`, `/select-workspace`, or `/` from that target.

Therefore:

- Electron main and preload carry lifecycle and transport, but neither chooses the pristine route.
- The bundled host owns the source account state and the renderer owns the authoritative route decision.
- Native modules do not participate in the login-versus-workspace branch found in this build.
- Renderer persisted atoms affect onboarding and workspace progression after authentication policy is satisfied, but they do not override a missing auth method when `requiresAuth` is true.

Confidence is high for this static control-flow result. Runtime behavior on a pristine, isolated, network-denied profile remains the responsibility of the separate startup-trace ticket.

## Baseline provenance

The exact target was recovered from OpenAI's live Sparkle feed, not inferred from another installed build.

- Appcast: <https://persistent.oaistatic.com/codex-app-prod/appcast.xml>
- Immutable arm64 archive: <https://persistent.oaistatic.com/codex-app-prod/ChatGPT-darwin-arm64-26.707.71524.zip>
- Appcast title/version: `26.707.71524`
- Appcast build: `5263`
- Appcast publication time: `Tue, 14 Jul 2026 00:50:23 +0000`
- Appcast declared length: `565562074`
- Appcast EdDSA signature: `NxNCnzQq00IfBgk5zalTiypj/d7SdE1R99JFEDsKsKVg9mvm2Yk7jZQl5yumV6OhoRQ91U+PBycZ8l4N//O2AQ==`
- Downloaded archive length: `565562074`
- Downloaded archive SHA-256: `8981d832cfd061ff8fe80295cd675d5c283fd53ed2ea8c80cc9d1856e47cfe74`

The extracted bundle was renamed outside `/Applications` and was never launched. `/Applications/ChatGPT.app` and the real user profile were not read or modified.

Bundle verification:

- `CFBundleIdentifier`: `com.openai.codex`
- `CFBundleShortVersionString`: `26.707.71524`
- `CFBundleVersion`: `5263`
- package brand: `chatgpt`
- Developer ID: `OpenAI OpCo, LLC (2DC432GLL2)`
- Team ID: `2DC432GLL2`
- signing timestamp: `Jul 13, 2026 at 8:07:18 PM`
- CDHash: `b3d699c5b79a4d2edf33d316b58687f80be92538`
- notarization ticket: stapled and validated
- `codesign --verify --deep --strict`: valid on disk and satisfies its designated requirement
- outer executable SHA-256: `dc975c4116bfb7f21cd492e14b4f7ec265813e839bf7b9e6e300e0e78b8498bf`
- `Contents/Resources/app.asar` SHA-256: `d28f31b4bbb04c519be65c2af8277d8c5faf77b4239ee89b928f0a7423dacd84`
- `Contents/Resources/codex` SHA-256: `28699add67540b93390329a740649a9eb9bdbc5538d92c1679c8c6b6fa2c623c`

The Info.plist ASAR-integrity hash is `e3023f2d1c334ba8ba80bd22a97553d412a4616a86d75ca81e258e974061f3c7`; it is an Electron ASAR header-integrity value, not a whole-file SHA-256. Gatekeeper's `spctl` returned an internal code-signing-subsystem error for the renamed temporary bundle, so it is not used as positive or negative evidence.

An earlier official Codex DMG was used only to discover the public appcast URL. It was build `26.623.141536` (`4753`), not the target baseline, and no control-flow conclusion below depends on it.

## Packaged topology

`Contents/Resources/app.asar/package.json` identifies:

- entry point: `.vite/build/early-bootstrap.js`
- product name: `Codex`
- package version: `26.707.71524`
- build flavor: `prod`
- build number: `5263`
- app brand: `chatgpt`
- Sparkle feed: `https://persistent.oaistatic.com/codex-app-prod/appcast.xml`

The ASAR was extracted with the repository's `tooling/chatgpt-ffs/chatgpt-ffs` reader. Relevant bundle-relative paths are:

- `.vite/build/early-bootstrap.js`
- `.vite/build/bootstrap-ovGg7JWM.js`
- `.vite/build/main-DcVqMbYE.js`
- `.vite/build/preload.js`
- `.vite/build/sqlite-B1YNeAip.js`
- `webview/index.html`
- `webview/assets/index-BvXyCFw4.js`
- `webview/assets/app-main-B9pWGGd7.js`
- `webview/assets/app-initial~app-main~page-kMhXWEru.js`
- `webview/assets/app-initial~app-main~onboarding-page~hotkey-window-thread-page~quick-chat-window-page~chatg~gwqc41kz-CnQKtQ6U.js`
- `webview/assets/login-route-B6uZ-KKr.js`
- `webview/assets/onboarding-login-content-DWFFh9lA.js`
- `webview/assets/select-workspace-page-C0uwIk57.js`

The asset names are content-addressed build output and should be discovered by semantic strings and imports in any future build, not hard-coded as a cross-version contract.

## Startup and window creation

`.vite/build/early-bootstrap.js` hands off to `.vite/build/bootstrap-ovGg7JWM.js`. Bootstrap:

- derives Electron `userData` from `CODEX_ELECTRON_USER_DATA_PATH` when set, otherwise from Electron's app-data directory and build flavor;
- obtains the single-instance lock;
- waits for Electron readiness;
- initializes the updater; and
- imports `.vite/build/main-DcVqMbYE.js` and calls `runMainAppStartup`.

Main startup then:

1. hydrates the shell environment;
2. runs local data migrations;
3. creates the window/application services;
4. starts and connects the local app-server;
5. initializes settings and the local SQLite state database; and
6. calls `windowServices.ensureWindow()`.

The call to `ensureWindow()` is not guarded by account state. The primary window uses:

- `contextIsolation: true`
- `nodeIntegration: false`
- `sandbox: false`
- `.vite/build/preload.js`
- the packaged `webview/index.html` through the registered app protocol

Main's handler for app-server notification `account/login/completed` calls `showPrimaryWindow`; it does not select the initial route. This is lifecycle behavior after a login attempt, not the pristine-startup authority.

## Preload and IPC

`.vite/build/preload.js` exposes `window.codexWindowType = "electron"` and `window.electronBridge` through Electron's context bridge.

The relevant channels are:

- renderer to main: `codex_desktop:message-from-view`
- main to renderer: `codex_desktop:message-for-view`
- shared-state snapshot: `codex_desktop:get-shared-object-snapshot`
- app-host connection: `codex_desktop:connect-app-host`
- worker transport channels
- build-flavor, theme, and diagnostics channels

Main-to-renderer messages are redispatched as browser `MessageEvent("message")` events. Renderer messages such as persisted-atom changes are invoked through main. The preload does not query account state, normalize authentication, or choose a route.

## Bundled host account state

The local app-server manager's `getAccount()` sends:

```text
account/read { refreshToken: false }
```

The bundled binary contains the `GetAccountResponse` shape with:

```text
account
requiresOpenaiAuth
```

It also contains account variants for `apiKey`, `chatgpt`, and `amazonBedrock`, plus the local `auth.json` storage contract. The visible API-key login copy explicitly says that the key is stored locally in `auth.json`.

Static evidence establishes that `Contents/Resources/codex` returns the account policy consumed by the renderer. It does not establish every storage backend used for every ChatGPT credential type. In particular, whether ChatGPT session material for this exact macOS build is exclusively in `auth.json`, exclusively in Keychain, or split across both remains an implementation detail to verify without copying a real profile.

The app-server lifecycle also handles:

- `account/updated`, which publishes a new auth method and causes account state to be reread;
- `account/login/completed`, which completes an active login operation; and
- `account/logout`, which clears the active account path.

## Authoritative renderer decision

The decisive code is split between the common renderer chunk and `webview/assets/app-initial~app-main~page-kMhXWEru.js`.

The common chunk's auth loader:

1. calls the app-server manager's `getAccount()`;
2. maps `account.type === "apiKey"` to `authMethod = "apikey"`;
3. maps `account.type === "chatgpt"` to `authMethod = "chatgpt"`;
4. maps Amazon Bedrock to `authMethod = "amazonBedrock"`;
5. optionally selects Copilot when configured and available; and
6. sets `requiresAuth` from `requiresOpenaiAuth`, defaulting to true when the field is absent.

In minified build terms, `RZt` performs the async load, `VZt` calls `getAccount()`, `UZt` normalizes the response, and `VG` is the logged-out fallback:

```text
{ openAIAuth: null, authMethod: null, requiresAuth: true, ... }
```

In `webview/assets/app-initial~app-main~page-kMhXWEru.js`, function `Nge` computes the onboarding target. Its first completed-state branch is equivalent to:

```text
if (!auth.authMethod && auth.requiresAuth) return "login"
```

Only after that branch does it consider forced onboarding overrides, backend onboarding completion, projectless onboarding completion, or existing workspace roots. Function `Fge` then redirects:

- `login` to `/login`
- `welcome` to `/welcome`
- `workspace` to `/select-workspace`
- `app` away from onboarding routes to `/`

A nested route guard independently enforces the same contract: render the application when `authMethod` exists or `requiresAuth === false`; otherwise replace the route with `/login`.

This duplicated renderer enforcement makes the authoritative condition unambiguous: a pristine account response with no method and `requiresOpenaiAuth` absent or true reaches the login wall. A host response with no method and `requiresOpenaiAuth: false` is allowed past the auth guard, after which onboarding/workspace state chooses `welcome` or `app`.

`webview/assets/login-route-B6uZ-KKr.js` is presentation and login orchestration after routing. It offers ChatGPT browser login, device-code login, GitHub Copilot, and API-key login. Successful login refreshes account state and navigates toward `/first-run` or `/welcome`; the component itself is not the pristine route authority.

## Persisted state

Three storage domains must remain separate:

### Electron user data

Bootstrap's `CODEX_ELECTRON_USER_DATA_PATH` override isolates Electron/Chromium `userData`. This is the correct seam for future disposable runtime tests, but it is not itself the Codex account database.

### Codex home and desktop global state

The desktop resolves Codex home through the bundled host's `CODEX_HOME` topology. Under that directory, main uses:

- `.codex-global-state.json` and `.codex-global-state.json.bak`
- `config.toml`
- the app-server SQLite state database
- the host authentication store, including the `auth.json` contract

`.vite/build/sqlite-B1YNeAip.js` implements `.codex-global-state.json` as an atomically replaced JSON object with a backup. Main's `electron-persisted-atom-state` entry is a record of renderer atom values; updates are debounced and synchronized across windows.

### Renderer atoms and onboarding

Main handles `persisted-atom-updated`, writes the key under `electron-persisted-atom-state`, and broadcasts changes to renderer windows. These values include onboarding and workspace preferences. They can affect the decision among `welcome`, `select-workspace`, and `app` only after the renderer auth condition allows progress. They are not a substitute for the host's account response.

No real `.codex-global-state.json`, `config.toml`, SQLite database, Keychain item, or `auth.json` was inspected or copied.

## Native and framework boundary

The bundle contains Electron's `Codex Framework.framework`, helper applications, `Sparkle.framework`, and these native resources:

| Resource | SHA-256 | Observed responsibility |
| --- | --- | --- |
| `avatar-overlay.node` | `e16e42c2f41d0fff45a848610ba1db24d70bc0b59c5169602d758ac9237ca1f0` | avatar/pet composition |
| `sparkle.node` | `eb6f705ebe751c27eeb4cf1bafa6611d332386e1b047229dc9939cd9fd9ac58c` | updates |
| `devicecheck.node` | `d3e38c8baf0a7bfa2af5c6b39e9bf7eaf975d7ea97d24d02f7fa3ab6681c5bb3` | DeviceCheck attestation/token attachment |
| `browser-use-peer-authorization.node` | `cf70071d77d39336466ceae81c20b8f72c66e0ce51b9642b4e373ae17b6db385` | browser-use socket peer authorization |
| `sky.node` | `fd7cf63ef58637368f8933144dad1732df20a83f55d26168f0b940e8c5f95c22` | native Sky/CUA integration |
| `input-monitoring-permission.node` | `9fdcfd198e4d5a3596e011cd03113b5f7c05e107ca80d333e87a14819b7a4ada` | macOS input-monitoring permission |
| `remote-control-device-key.node` | `60037c1bc0f528da105c546ed669f6671c8a8634787445e716d1736aa10808a6` | remote-control device keys |
| `bare-modifier-monitor` | `26d557bb54a14f8dd9bf019485735f08a0ccc8dd956265395559cc3604d42ade` | keyboard modifier monitoring |
| `launch-services-helper` | `ef8d481b025111f2a14917e7240998f12a72efdc86fa2bc7318e0935d9879c3d` | Launch Services integration |

`devicecheck.node` is relevant to authenticated OpenAI network requests and attestation, but static call sites place it in request decoration and cookie registration, not in the renderer's initial route branch. The other native components likewise serve updates, browser/CUA, remote control, input, and window integration. No native call site feeds `authMethod` or `requiresAuth` into `Nge`/`Fge`.

## Historical branch evidence

`origin/ivan/chatgpt-unflag` and `tooling/chatgpt-ffs/README.md` establish that older builds used remotely evaluated feature gates and that authenticated context was needed for some gate evaluation. That branch remains useful for ASAR discovery, binary-safe extraction, integrity repair, and the warning that visible UI is not equivalent to offline capability.

It does not establish the startup-auth mechanism in build `5263`. In particular, historical Statsig gate IDs and the historical feature-visibility auth gate are not the authoritative `account/read` route branch described here.

## Implications for subsequent tickets

- Startup tracing should observe `account/read` and its response before looking for Statsig traffic.
- Any candidate offline architecture must decide whether to change the bundled host's `requiresOpenaiAuth` policy, change renderer interpretation, or provide a narrowly equivalent local app-server response. Persisted onboarding atoms alone cannot bypass the login wall.
- Provider-path research should test whether the bundled host naturally returns `requiresOpenaiAuth: false` for a configured self-hosted model provider. The renderer already has an explicit non-OpenAI-auth path; this report does not prove GLM configuration reaches it.
- Integrity analysis should treat renderer-ASAR modification and bundled-host modification as different signing and update surfaces.
- Runtime validation must use both an isolated Electron `userData` directory and an isolated `CODEX_HOME`; isolating only one leaves state contamination possible.

## Unknowns and limits

- No runtime trace was performed in this ticket, by design.
- The exact macOS persistence split for ChatGPT session credentials remains unverified.
- The app-server logic that computes `requiresOpenaiAuth` from model-provider configuration is not resolved here; that belongs to provider-path research.
- Hosted feature availability after the auth guard is intentionally not inferred from route visibility.
- Cross-version stable identifiers are semantic (`account/read`, `requiresOpenaiAuth`, `authMethod`, and the login redirect shape), while minified function and chunk names are baseline-only evidence.
