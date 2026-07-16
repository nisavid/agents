# Validate the preferred offline route

## Question

Can ChatGPT/Codex build `26.707.71524` (`5263`) run a complete local GUI
workflow with no OpenAI account: select an exact Git fixture through the native
project picker, complete a renderer turn through the reviewed local gateway and
OptiQ model, preserve namespace-call continuation, deny hosted egress, and
leave the source archive and installed application untouched?

## Verdict

Green for the bounded local workflow. The verified route uses only a fresh,
separately named copy of ChatGPT/Codex `26.707.71524`. It never launches,
modifies, signs, or replaces the installed application.

The run proved all of the following in one disposable environment:

- the copied app reached its usable local renderer without an OpenAI account;
- the renderer selected the unique `Choose project` control, then the unique
  `New project` and `Use an existing folder` menu items;
- immediately before the final menu-item press, a PID-bound, fail-closed
  bounded Accessibility traversal proved that no native sheet existed;
- the copied app opened its native directory chooser at the fixture parent;
- the reviewed PID-bound helper found the chooser only through the copied
  app's bounded `AXSheet` topology, selected the exact fixture row through
  writable `AXSelectedChildren`, and pressed the enabled chooser action once;
- the renderer independently confirmed the exact nonce project before the
  prompt was submitted;
- the renderer turn completed through `codex-ns-proxy` and local OptiQ using
  `mlx-community/Qwen3.5-2B-OptiQ-4bit:no-think`;
- the namespace adapter reconstructed both calls and reused the response-ID
  mapping for the continuation;
- missing and incorrect gateway credentials were rejected before upstream;
- no remote socket or token leak was observed; and
- all owned processes and loopback listeners were closed during cleanup.

This is a smoke-test result for the selected 2B model and exact desktop build,
not a claim that the model is a production-quality coding agent or that the
disposable patch is a redistributable application build.

### Disposable native-picker seam

The vendor app does not expose a deterministic fixture-start location for this
test. The harness therefore copies the verified source bundle and applies one
fixed-width, 164-byte replacement to the copied `app.asar` main process. The
replacement adds `defaultPath: process.env.NDP` to the existing
`Select Project Root` dialog options while preserving the dialog properties,
title, parent-window overload, and payload length.

The patcher fails closed unless it finds exactly one source payload, validates
the ASAR topology and existing file integrity, and finds the old main-file hash
exactly twice in the ASAR header. It writes the equal-length payload, updates
both header hash occurrences, rereads the archive, and verifies the single
patched payload and computed integrity. The runner then updates
`ElectronAsarIntegrity:Resources/app.asar:hash` in the copied bundle's
`Info.plist`.

The copied outer bundle is ad-hoc signed with identifier `com.openai.codex` and
must satisfy its new designated requirement before launch. Nested vendor
artifacts remain part of the copied tree. The launch adds
`--use-mock-keychain` only to this disposable copy. The successful reviewed run
produced no Keychain alert, and the harness never resets or modifies the user's
real keychain.

### Preferred authenticated gateway

The route is bound to gateway commit
`6307d37b76918c19f2e3bc0fd506434531aadeb2`, file
`tooling/codex-ns-proxy/codex-ns-proxy.py`, and Git blob
`a368b8f8e919361425763e86ca1c80fcea81825f`. The runner materializes only that
committed blob, verifies its Git identity, and starts it on loopback with the
`codex-namespace` adapter.

Every run generates distinct inbound and upstream bearer values. Codex sees
only the inbound value; the gateway validates it and sends only the upstream
value to the fixed loopback observer and OptiQ authority. The observer records
booleans rather than credentials or request bodies. Gateway logs are redacted,
secret-bearing dumps are disabled, and terminal evidence is the sanitized line
`[codex-ns-proxy] SSE terminal_completed=true`.

The gateway must not invent token usage. Model metadata and usage remain a
separate local metadata contract unless an authoritative tokenizer-backed
source is available.

### Production isolation boundary

The semantic run uses outer macOS Seatbelt profiles to deny remote network and
general operator-home access. The provider alone may read the exact pinned model
repository and OptiQ runtime beneath `REAL_HOME`. Electron is launched with
Chromium `--no-sandbox` because Chromium's nested sandbox does not initialize
inside this outer test profile. This validates the application behavior, local
routing, and outer egress boundary; it is not the final production-isolation
result. A disconnected VM or the air-gapped target should retain the vendor
Chromium sandbox for that platform check.

## Exact artifacts

| Item | Bound value |
| --- | --- |
| App version/build | `26.707.71524` (`5263`) |
| Official archive SHA-256 | `8981d832cfd061ff8fe80295cd675d5c283fd53ed2ea8c80cc9d1856e47cfe74` |
| Source `Contents/Resources/app.asar` SHA-256 | `d28f31b4bbb04c519be65c2af8277d8c5faf77b4239ee89b928f0a7423dacd84` |
| Patched copied `app.asar` SHA-256 | `06c4fd5cbb3662911cc62c3569042bd5657f3476a99ef9edc47bd51d5380026f` |
| Source ASAR header SHA-256 | `e3023f2d1c334ba8ba80bd22a97553d412a4616a86d75ca81e258e974061f3c7` |
| Patched ASAR header SHA-256 | `c069ef0e4e826ec2fd8db41a626f3e26f3edead477053a12703830ce7e047b75` |
| Patched main-file SHA-256 | `a8082ef44bf3aa4e30e7c663472da502d15bed35073a4c125903f4b9291961cc` |
| Retained successful-run helper SHA-256 | `a4365c91bbc160045f4cf31bfc679c5d46adfc19d4b6271a2c79bb354fd0dff5` |
| `Contents/Resources/codex` SHA-256 | `28699add67540b93390329a740649a9eb9bdbc5538d92c1679c8c6b6fa2c623c` |
| Bundled host | `codex-cli 0.144.2` |
| Model | `mlx-community/Qwen3.5-2B-OptiQ-4bit:no-think` |
| Model revision | `adc8669eb431e3168aeb4e320bd7b757914350e2` |
| OptiQ | `mlx-optiq 0.2.15` |
| Python | `3.12.13` |
| MLX | `0.32.0` |
| MLX-LM | `0.31.3` at `ab1806e8f5d6aa035973af194a1b9198ab4754dc` |
| Gateway commit | `6307d37b76918c19f2e3bc0fd506434531aadeb2` |
| Gateway Git blob | `a368b8f8e919361425763e86ca1c80fcea81825f` |

## Pinned model metadata

The generated `model_catalog_json` and `codex debug models` gate bind the local
profile to `Qwen3.5-2B-OptiQ-4bit (no-think)`. The catalog publishes no default
reasoning level, an empty supported-reasoning set, text-only input, context and
maximum context windows of `262144`, and an effective-window percentage of
`95`.

The renderer independently displayed the exact pinned model name and no
fallback `Custom Light` metadata. It also appended its own `Medium` selection
label. Model identity therefore passes, while reconciliation of that independent
reasoning label with the catalog remains an open ticket 08/12 gate.

## Authenticated-gateway reproduction

Build and review the native helper first. Replace both angle-bracketed values
below with that reviewed artifact's absolute path and SHA-256 before running
the command:

```sh
ROUTE_MODE=gateway \
GATEWAY_COMMIT=6307d37b76918c19f2e3bc0fd506434531aadeb2 \
GUI_NATIVE_PROJECT_PICKER=true \
NATIVE_PICKER_DEFAULT_PATH_SEAM=true \
NATIVE_GUI_PROBE_BIN=<reviewed-helper-path> \
NATIVE_GUI_PROBE_SHA256=<reviewed-helper-sha256> \
GUI_WORKFLOW=true \
PROBE_EXPECT=renderer-native-project \
  .scratch/chatgpt-airgap-unlock/research/08-run-prototype.sh
```

The successful verdict includes:

```text
MAIN_UI_OBSERVED=true
RENDERER_PROMPT_COMPLETED=true
RENDERER_MODEL_METADATA_MATCHED=true
NATIVE_PROJECT_PICKER_EXERCISED=true
PROVIDER_REQUEST_OBSERVED=true
GATEWAY_LISTENER_OBSERVED=true
GATEWAY_TERMINAL_OBSERVED=true
NAMESPACE_TOOL_CONTINUATION=true
MODEL_LIST_ISOLATED=true
REMOTE_SOCKET_OBSERVED=false
TOKEN_LEAK_OBSERVED=false
OWNED_PROCESSES_EXITED=true
OWNED_LISTENERS_CLOSED=true
NATIVE_PICKER_DEFAULT_PATH_SEAM=true
NATIVE_PICKER_ASAR_HEADER_EXPECTED=true
SOURCE_APP_ASAR_UNCHANGED=true
APP_ASAR_EXPECTED=true
CODEX_UNCHANGED=true
```

`APP_ASAR_UNCHANGED=false` is expected in this mode: the disposable copy must
contain the exact reviewed patch. `APP_ASAR_EXPECTED=true` is the relevant
copy-integrity gate, while `SOURCE_APP_ASAR_UNCHANGED=true` proves the source
artifact was not changed.

## Integrity, isolation, and cleanup

The runner verifies the extracted source bundle's exact ASAR and bundled Codex
hashes before copying, verifies the exact source and patched ASAR states
independently, and validates the copied bundle's deep signature and designated
requirement. The official archive hash above is provenance evidence, not a
runner gate. The runner refuses the installed application as an input or
destination.

All app, host, gateway, observer, proxy, and OptiQ processes belong to the
disposable run. Cleanup terminates those process groups, verifies that every
reserved loopback listener is closed, and scans final sockets and regular
disposable state files for remote authorities and generated token canaries.

## Decision

Use the authenticated `codex-ns-proxy` to OptiQ route as the local development
and smoke-test path for this exact desktop build. Keep the fixed-width ASAR
seam, ad-hoc outer signing, mock keychain, and native helper confined to the
disposable copied application. The official archive remains immutable
provenance evidence; the extracted source bundle and installed application
remain untouched.
