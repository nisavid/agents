# Validate the preferred offline route

## Question

Can exact ChatGPT/Codex build `26.707.71524` (`5263`) cold-start from fresh,
isolated state with no OpenAI account, reach its usable local UI, complete a
deterministic Responses turn against the selected local OptiQ model, preserve
the thread across a cold host restart, deny hosted egress, and leave the signed
vendor artifact unchanged?

The prototype answers this in two layers:

1. a direct OptiQ baseline that isolates the app/provider semantics; and
2. an authenticated loopback-gateway run that validates the preferred
   architecture's actual trust boundary.

Both layers are complete. The gateway result is bound to one reviewed commit
and verified Git blob; it is not inferred from the direct result.

This is a bounded route and trust-seam result. Ticket 08 remains partial
because the broader minimum local workflow still includes GUI-driven behavior
that this prototype did not exercise.

## Verdict

### Direct application semantics: pass

The exact packaged host and GUI operate without OpenAI state when cold-started
with the supported custom-provider configuration:

- `account/read` ran and the GUI did not show the ChatGPT login wall;
- no `auth.json` was created;
- the renderer passed local onboarding and reached the main `New task`,
  `Choose project`, and `Open settings` surface;
- the bundled Codex `0.144.2` host resolved the configured provider without
  model fallback and sent `POST /v1/responses` to loopback OptiQ;
- OptiQ returned HTTP 200 and the host streamed the exact agent message
  `LOCAL_APP_OK`;
- the exact turn persisted with status `completed` and no error;
- a new host process listed, read, and resumed the same thread and exact agent
  output after cold restart;
- the local Git fixture was the thread CWD and runtime workspace root;
- the host discovered and attached a repo-local file-only skill;
- permission profiles and both Default and Plan collaboration modes were
  listed locally;
- failed hosted requests did not prevent the main UI or local host workflow;
- the app and OptiQ listeners were loopback-only, no remote socket survived,
  and the per-run token was absent from every regular disposable state file;
- `app.asar`, the bundled `codex` binary, and the strict bundle signature were
  unchanged after the run.

This is a plumbing result for the selected 2B smoke model, not a claim that the
model is a production-quality coding agent.

### Preferred authenticated gateway: pass

The preferred route passed against reviewed gateway commit
`7c960b15267e82ef5d5a854bdd54bf53fb9e8135`, file
`tooling/codex-ns-proxy/codex-ns-proxy.py`, and Git blob
`3401400af5da1a9b95e343c324661f90fa986deb`. The run proved:

- authenticated app/host-to-gateway access with a generated per-run token;
- a distinct generated upstream token acceptable to OptiQ;
- the exact immutable model ID with provider fallback disabled;
- an explicit upstream authority allowlist and loopback-only listener;
- namespace-tool request and response transformations without changing the
  ordinary text turn;
- transparent `response.completed` handling with the exact sanitized stderr
  line `[codex-ns-proxy] SSE terminal_completed=true`;
- redacted logs and disabled secret-bearing request dumps; and
- the same host-turn, cold-resume, usable-UI, integrity, egress, token-canary,
  and exact process-group cleanup assertions as the direct baseline.

Missing and wrong inbound credentials both returned HTTP 401 without reaching
the upstream observer. The observer then saw the authorized request with the
exact upstream bearer and confirmed that the inbound bearer was not reused.
It recorded booleans only; it did not persist credentials or message bodies.

A separate deterministic probe loaded the immutable gateway module, flattened
a namespace function, reconstructed the first call, preserved its
`function_call_output`, reused the response-ID mapping for a continuation, and
reconstructed the second call. The ordinary model sentinel remained a separate
packaged-host turn through the live gateway.

Gateway mode is fail-closed before materialization. It accepts only the exact
reviewed 40-character commit. The gateway file, loopback validation-upstream
URL, environment names, namespace adapter, debug setting, and terminal oracle
are fixed in the harness. It resolves the commit, materializes only that
committed blob into the disposable run root, and verifies its Git blob ID
before execution. A moving worktree file cannot be selected implicitly.

The authenticated-gateway reproduction is:

```sh
ROUTE_MODE=gateway \
GATEWAY_COMMIT=7c960b15267e82ef5d5a854bdd54bf53fb9e8135 \
PROBE_DURATION_MS=15000 \
  .scratch/chatgpt-airgap-unlock/research/08-run-prototype.sh
```

Gateway mode generates two distinct values beginning with `sk-optiq-` for
every run. The Codex process receives only the inbound value under its
configured `env_key`. The gateway receives both values; it validates the
inbound bearer and applies only the upstream bearer. The isolated validation
observer also receives both strictly as expected and forbidden comparison
values, records booleans only, and forwards only to fixed loopback OptiQ.
OptiQ receives no inbound value, and Codex receives no upstream value. Neither
value appears on the process command line or in committed configuration.

The exact gateway environment contract remains bound to its reviewed commit:
`NS_PROXY_UPSTREAM`, `NS_PROXY_INBOUND_TOKEN`, the distinct
`NS_PROXY_UPSTREAM_TOKEN`, `NS_PROXY_HOST`, `NS_PROXY_PORT`,
`NS_PROXY_ADAPTER=codex-namespace`, and `NS_PROXY_DEBUG=true`. The listener is
loopback-only, and the explicit upstream URL is its sole forwarding authority.
The gateway, validation upstream, and OptiQ run as separate processes under
loopback-only Seatbelt profiles.

The gateway must not invent token usage. Model metadata and usage remain a
separate local metadata contract unless the gateway has an authoritative
tokenizer-backed source.

OptiQ permits headerless loopback requests for local development; upstream
authentication is not an OptiQ prerequisite. Gateway validation deliberately
uses a separate generated upstream bearer to exercise credential replacement
and prove that Codex's inbound gateway token never reaches OptiQ.

### Direct versus gateway comparison

| Property | Direct baseline | Gateway run |
| --- | --- | --- |
| Codex endpoint | OptiQ loopback listener | authenticated gateway loopback listener |
| Codex credential | generated OptiQ-compatible local bearer | generated inbound gateway bearer only |
| OptiQ credential | same direct bearer | distinct generated upstream bearer only |
| Namespace adapter | absent | explicitly `codex-namespace` |
| Upstream authority | fixed OptiQ loopback URL in Codex config | fixed validation-observer URL; observer fixed to OptiQ loopback |
| Immutable gateway source | not applicable | required commit, file, and verified Git blob |
| Terminal evidence | host item, idle state, persisted completed turn | same plus sanitized gateway terminal marker |
| Leak oracle | generated token absent from disposable state | all tokens absent from state; bodies absent from gateway logs |
| Current grade | pass for bounded semantics | pass for bounded semantics and trust seams |

### Production isolation: blocked

The semantic runs use an outer macOS Seatbelt profile to deny remote network
and operator-home access. Electron must be launched with Chromium
`--no-sandbox`: Chromium's nested sandbox fails when the signed Electron tree
is already wrapped by the outer profile.

That is sufficient evidence for application behavior, RPCs, isolated state,
provider routing, and outer-boundary egress denial. It is not a production
isolation pass. The preferred architecture requires the vendor Chromium
sandbox to remain enabled. A disconnected VM or true air-gapped target can
provide the later artifact-red platform check without weakening Chromium or
mutating global packet-filter state.

## Exact artifacts

| Item | Bound value |
| --- | --- |
| App version/build | `26.707.71524` (`5263`) |
| Official archive SHA-256 | `8981d832cfd061ff8fe80295cd675d5c283fd53ed2ea8c80cc9d1856e47cfe74` |
| `Contents/Resources/app.asar` SHA-256 | `d28f31b4bbb04c519be65c2af8277d8c5faf77b4239ee89b928f0a7423dacd84` |
| `Contents/Resources/codex` SHA-256 | `28699add67540b93390329a740649a9eb9bdbc5538d92c1679c8c6b6fa2c623c` |
| Bundled host | `codex-cli 0.144.2` |
| Model | `mlx-community/Qwen3.5-2B-OptiQ-4bit` |
| Model revision | `adc8669eb431e3168aeb4e320bd7b757914350e2` |
| Variant | `:no-think` |
| OptiQ | `mlx-optiq 0.2.15` |
| Python | `3.12.13` |
| MLX | `0.32.0` |
| MLX-LM | `0.31.3` at `ab1806e8f5d6aa035973af194a1b9198ab4754dc` |
| Gateway commit | `7c960b15267e82ef5d5a854bdd54bf53fb9e8135` |
| Gateway Git blob | `3401400af5da1a9b95e343c324661f90fa986deb` |

The prototype copies the verified source to a separately named bundle inside a
fresh disposable run root. It refuses to use the installed application.

## One-command direct reproduction

The primary-source prototype is intentionally throwaway:

```sh
PROBE_DURATION_MS=15000 \
  .scratch/chatgpt-airgap-unlock/research/08-run-prototype.sh
```

The successful direct run returned:

```text
LOGIN_WALL_OBSERVED=false
ACCOUNT_READ_OBSERVED=true
MAIN_UI_OBSERVED=true
CDP_OBSERVER_HEALTHY=true
HOST_SENTINEL_COMPLETED=true
COLD_HOST_RESUME_OBSERVED=true
PROVIDER_REQUEST_OBSERVED=true
AUTH_JSON_PRESENT=false
SHELL_SNAPSHOT_ABSENT=true
PROVIDER_LISTENER_OBSERVED=true
MODEL_LIST_ISOLATED=true
REMOTE_SOCKET_OBSERVED=false
TOKEN_LEAK_OBSERVED=false
OWNED_PROCESSES_EXITED=true
OWNED_LISTENERS_CLOSED=true
APP_ASAR_UNCHANGED=true
CODEX_UNCHANGED=true
```

After the command exited, every reserved listener was closed. Local raw
logs stay under the disposable run root and are not committed.

## One-command authenticated-gateway reproduction

```sh
ROUTE_MODE=gateway \
GATEWAY_COMMIT=7c960b15267e82ef5d5a854bdd54bf53fb9e8135 \
PROBE_DURATION_MS=15000 \
  .scratch/chatgpt-airgap-unlock/research/08-run-prototype.sh
```

The successful gateway run returned:

```text
LOGIN_WALL_OBSERVED=false
ACCOUNT_READ_OBSERVED=true
MAIN_UI_OBSERVED=true
CDP_OBSERVER_HEALTHY=true
HOST_SENTINEL_COMPLETED=true
COLD_HOST_RESUME_OBSERVED=true
PROVIDER_REQUEST_OBSERVED=true
AUTH_JSON_PRESENT=false
SHELL_SNAPSHOT_ABSENT=true
PROVIDER_LISTENER_OBSERVED=true
GATEWAY_LISTENER_OBSERVED=true
GATEWAY_TERMINAL_OBSERVED=true
GATEWAY_BODY_LEAK_OBSERVED=false
GATEWAY_MISSING_AUTH_REJECTED=true
GATEWAY_WRONG_AUTH_REJECTED=true
UPSTREAM_TOKEN_REPLACED=true
UPSTREAM_TERMINAL_COMPLETED=true
NAMESPACE_TOOL_CONTINUATION=true
MODEL_LIST_ISOLATED=true
REMOTE_SOCKET_OBSERVED=false
TOKEN_LEAK_OBSERVED=false
OWNED_PROCESSES_EXITED=true
OWNED_LISTENERS_CLOSED=true
APP_ASAR_UNCHANGED=true
CODEX_UNCHANGED=true
```

## Direct route evidence

### Cold account and UI

The app received `account/read` under the prelaunch provider config. CDP first
observed the local role-onboarding prompt, clicked its local `Skip` action, and
then observed one renderer state containing all of:

- `New task`;
- `Choose project`;
- `Open settings`; and
- no role-onboarding prompt and no ChatGPT login wall.

The main-UI oracle requires that combined post-onboarding state. An earlier
broad string heuristic was discarded because it could mark the role screen as
the main UI.

Hosted startup intent still reached the inert loopback proxy for ChatGPT,
GitHub, API GitHub, and feature-flag endpoints. The proxy never forwarded, the
outer Seatbelt profile denied direct remote connections, and those failures did
not prevent the local main UI.

### Exact host turn

The bundled host used a proxy-facing-free direct model ID for this baseline,
`allowProviderModelFallback: false`, the local Git fixture as `cwd`, read-only
sandboxing, approval policy `never`, and the attached `local-sentinel` skill.
The local skill says only to avoid tools and return the sentinel.

OptiQ recorded one successful Responses request. The host emitted an
`item/completed` agent message whose exact text was `LOCAL_APP_OK`, then changed
the thread to idle. `thread/read` returned the same turn ID with status
`completed`, no error, and the same exact agent message.

The app-server did not emit its public `turn/completed` notification for that
successful turn even though its rollout recorded internal `task_complete`, the
thread became idle, and the persisted turn was complete. OptiQ's installed
Responses shim explicitly emits `response.completed`. This is recorded as an
app-server notification discrepancy, not a proven Responses-dialect failure.

### Cold persistence

The first app-server process exited before persistence validation. A separate
process against the same isolated `CODEX_HOME` then:

- returned `requiresOpenaiAuth: false`;
- listed the exact thread under the fixture CWD;
- read the completed turn and exact `LOCAL_APP_OK` agent message; and
- resumed the exact thread successfully.

The GUI process used the same isolated state, but its task list did not expose
the app-server-created `vscode`-source thread. GUI rendering and reopening of a
completed thread therefore remain unproven rather than being inferred from the
host RPC pass.

### Local capability evidence

| Capability | Evidence | Grade |
| --- | --- | --- |
| No-account startup | `account/read`, no login wall, no `auth.json` | Pass |
| Main local UI | Combined post-onboarding renderer state | Pass |
| Project backing | Git fixture as host CWD and runtime workspace root | Pass at host seam |
| Thread persistence | Separate cold host list/read/resume | Pass at host seam |
| Local skill | Repo-local skill discovered and attached to the turn | Pass |
| Permission profiles | Read-only, workspace, and danger-full-access listed | Discovery pass |
| Collaboration modes | Default and Plan listed | Discovery pass |
| Hosted failure behavior | Hosted intent denied; local UI and host turn continue | Pass for observed paths |
| Renderer-originated model turn | No CDP-driven composer submission | Red/unproven |
| GUI thread reopen | Host-created source not shown in GUI task list | Red/unproven |
| Project picker/worktree UI | Native picker and worktree controls not driven | Red/unproven |
| Mode selection | Modes listed but no turn run in each mode | Partial |
| Permission enforcement/approval | Profiles listed; no denial and approval scenario run | Red/unproven |
| Plugin install/settings UI | Local capability exists in prior research; not exercised here | Red/unproven |

## Metadata and usage degradation

The host warned that the exact local model has no bundled metadata and used
fallback metadata with a `258400` context window. OptiQ reported
`input_tokens: 0` even though its log showed roughly 3.4k prompt tokens
processed. The short sentinel remains valid, but this run does not validate:

- context limits;
- compaction thresholds;
- reasoning or modality metadata; or
- input-token usage accounting.

The implementation needs pinned local metadata appropriate to the exact model.
Usage normalization belongs only at a seam with authoritative tokenizer-backed
counts.

## Failed-probe history

The red-capable loop found and corrected nine harness or contract defects:

1. Isolating OptiQ's `HOME` moved Hugging Face cache discovery. The runner now
   passes the absolute pinned snapshot directly, gives OptiQ an empty per-run
   cache, and sets both Hugging Face and Transformers offline modes. The app
   remains unable to read the operator home.
2. OptiQ rejects a present bearer token unless it starts with `sk-optiq-`.
   The first host attempt made six HTTP 401 requests. The fixed harness creates
   a new local-only compatible value per run and proves it is absent from logs.
   This is a runner protocol constraint, not a real external credential.
3. The initial assertion matched `LOCAL_APP_OK` in the user prompt and matched
   `completed` in the method name even though the turn had status `failed`.
   The fixed oracle accepts only an exact agent message and the persisted exact
   turn ID with status `completed` and no error.
4. `request_max_retries` and `stream_max_retries` at the top level appeared in
   the user layer but did not populate the effective provider values. They now
   live inside `[model_providers.local-optiq]`, and the host probe requires both
   effective values to equal one.
5. Denying the operator home initially blocked the venv interpreter symlink,
   whose target is a versioned uv-managed CPython tree. The fixed runner grants
   read and process-exec only to that exact runtime tree, invokes its resolved
   interpreter with only the pinned venv site-packages path, and grants model
   reads only to the selected Hugging Face model repository.
6. Pointing OptiQ at the ordinary Hugging Face cache made `/v1/models` expose
   thirteen unrelated cached models. The fixed runner supplies an empty
   per-run cache and requires exactly the loaded base model plus its four
   declared variants.
7. The first expanded token-canary scan found the provider bearer in Codex's
   shell snapshot. The bundled snapshotter captures the raw launch environment
   separately from `shell_environment_policy`. The fixed configuration keeps
   the provider variable excluded from spawned shells and disables the shell
   snapshot feature; the runner requires no snapshot files and no generated
   bearer bytes in any regular disposable state file.
8. The first authenticated-gateway attempt buffered short SSE responses in the
   validation upstream until EOF or 64 KiB. The gateway correctly timed out and
   Codex retried once. The fixed observer forwards SSE one line at a time with
   immediate flush and records structural booleans only.
9. The next gateway attempt completed the host turn, but the harness evaluated
   the sanitized terminal line before controlled teardown while OptiQ kept the
   HTTP connection alive. The fixed oracle evaluates final diagnostics after
   exact process-group cleanup and records the upstream terminal event
   immediately when observed.

The successful direct run still exposes the missing public `turn/completed`
notification described above. That discrepancy is not hidden by the stronger
persisted-state oracle.

## Integrity, isolation, and cleanup

The harness verifies strict code signing before and after launch and binds both
critical resource hashes. It verifies the model revision, selected manifest
hashes, safetensors blob link, Python, OptiQ, MLX-LM, MLX, and the MLX-LM source
commit before launch. It creates fresh `HOME`, `CODEX_HOME`, Electron user data,
Hugging Face cache, temporary, log, and Git fixture roots before first launch.
Every child environment is rebuilt from an explicit allowlist. Credential
values are present only in the processes that require their side of the seam.

The app process tree, observers, gateway, validation upstream, and OptiQ run
under Seatbelt profiles that deny remote network and allow loopback. Wildcard
listeners count as remote. The app, observers, and gateway deny the operator
home; OptiQ receives only the exact model and runtime exceptions. Every profile
explicitly denies writes to the installed production ChatGPT bundle. The app
profile also denies login Keychain services. An inert HTTP proxy records external
destination intent and never forwards it.

Each long-lived process starts in a separately owned process group. Cleanup
signals only those groups and root PIDs, waits for them, verifies every PID and
group is gone, and verifies every reserved listener is closed. The verified
model cache remains in place for the next prototype. The installed ChatGPT
application, ordinary profiles, Keychain, machine-wide configuration, and
global packet filter are outside the harness and remain untouched.

## Decision

The supported custom-provider path is sufficient for the no-account local
application semantics. No renderer, main-process, ASAR, native, entitlement,
fuse, updater, or account-shim mutation is justified.

The bounded application, provider, gateway, namespace, credential, and cleanup
semantics pass. Ticket 08 remains open/partial until the renderer submits a
model turn, the GUI reopens its completed thread, project/worktree controls are
driven, permission denial and approval are exercised, and plugin/settings UI
is validated. Production isolation also stays blocked until the same artifact
is tested with the vendor Chromium sandbox enabled under a real air-gap or
equivalent noninvasive egress boundary.
