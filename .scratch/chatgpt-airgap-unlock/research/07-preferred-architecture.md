# Preferred offline baseline architecture

## Decision

Use the exact pristine ChatGPT/Codex build `26.707.71524` (`5263`) through its
supported custom-provider configuration. Put one deep lifecycle module in
front of it. The module's public interface is one operation over a closed,
versioned profile; it owns artifact verification, isolated state, provider and
gateway lifecycle, prelaunch configuration, cold start, validation, evidence,
and cleanup.

The first profile is `optiq-qwen35-2b@1`. It runs the immutable
`mlx-community/Qwen3.5-2B-OptiQ-4bit` revision
`adc8669eb431e3168aeb4e320bd7b757914350e2` locally and selects its `:no-think`
variant. A separate `glm-5.2@1` profile describes the air-gapped GLM route. It
requires its own reachable endpoint and secret and must never fall back to the
OptiQ profile or an OpenAI model.

Both profiles route Codex through an authenticated loopback gateway. For OptiQ,
the gateway authenticates and constrains access to the otherwise
unauthenticated loopback server. For GLM, it additionally keeps the real GLM
secret at the provider adapter seam rather than exposing it to the app. A
Responses compatibility adapter is added only if direct contract validation
proves that an endpoint and bundled Codex `0.144.2` differ.

No renderer, main-process, ASAR, native, entitlement, fuse, updater, or runtime
interposition change is justified. The configured provider already makes the
bundled host truthfully return `account: null` and
`requiresOpenaiAuth: false`. A local account bootstrap shim would reproduce a
decision the supported host path already makes and would expand the trust
boundary without adding backing behavior.

Ticket **Choose the preferred bypass architecture** can close with this
selection. Ticket 08 must validate the complete configured Electron route and
may falsify this decision. Any fallback to bundle mutation requires a new,
explicitly scoped decision backed by exact-build ownership evidence.

## Evidence that constrains the design

The architecture is bound to the official arm64 artifact:

| Property | Required value |
| --- | --- |
| App version and build | `26.707.71524` (`5263`) |
| Official archive SHA-256 | `8981d832cfd061ff8fe80295cd675d5c283fd53ed2ea8c80cc9d1856e47cfe74` |
| `Contents/Resources/app.asar` SHA-256 | `d28f31b4bbb04c519be65c2af8277d8c5faf77b4239ee89b928f0a7423dacd84` |
| `Contents/Resources/codex` SHA-256 | `28699add67540b93390329a740649a9eb9bdbc5538d92c1679c8c6b6fa2c623c` |
| Bundled Codex | `codex-cli 0.144.2` |
| Signing identity | `Developer ID Application: OpenAI OpCo, LLC (2DC432GLL2)` |

The prior research establishes four decisive facts:

1. A pristine unconfigured host returns `account: null` and
   `requiresOpenaiAuth: true`; hiding the login presentation is insufficient.
2. A cold-started host with a custom `responses` provider and
   `requires_openai_auth = false` returns `requiresOpenaiAuth: false` and sends
   a turn to `<base_url>/responses` with the provider credential.
3. The minimum offline workflow's project, Git worktree, thread, mode,
   permission, skill, plugin, and configuration backing behavior is
   predominantly local. Hosted ChatGPT surfaces are not prerequisites.
4. Any bundle mutation loses the vendor signature and notarization chain and
   changes Team-ID, TCC, Keychain, app-group, and updater assumptions.

The selected OptiQ model has also completed the local Responses plumbing smoke:
exact text, a required function call with correct arguments, function output,
and a final answer. It is a plumbing fixture, not evidence that a 2B model is a
production-quality coding agent.

## The three interface designs

### 1. Minimal lifecycle methods

The first design exposed `initialize`, `launch`, and `validate` on an
`OfflineBaseline` module. It had the smallest explicit method count and placed
most behavior behind one seam.

Its weakness is ordering at the interface. A caller must retain an initialized
handle, know whether the provider is ready, decide when a cold start is
required, and call validation before cleanup. Those facts are lifecycle
invariants, so exposing the phases gives callers less leverage and creates
invalid intermediate states.

### 2. Extensible adapter catalog

The second design exposed provider, build, protocol, metadata, extension, and
validator adapter registration. It maximized future flexibility and made every
dependency replaceable.

Its weakness is a shallow public interface. Most variation is not yet real:
only build 5263 is supported, the Responses translator is conditional, and the
metadata mechanism remains to be validated. Public registration would make
every caller understand internal composition and would turn test seams into
product configuration.

### 3. Profile-driven runner

The third design exposed a single command over a declarative profile. It made
the default caller trivial and encoded ordering centrally.

Its weakness, alone, is that a sufficiently open profile becomes an adapter
catalog serialized as YAML. Hidden orchestration can also become difficult to
test if the runner has no typed lifecycle implementation and returns only a
process exit code.

### Recommended hybrid

Keep the third design's one-command caller experience and closed profiles. Put
the first design's typed lifecycle behind that interface, where the phases are
private states rather than caller obligations. Borrow internal adapters from
the second design only at proven seams.

This hybrid has the best **depth** because one operation exercises the entire
offline baseline. It has the best **locality** because lifecycle rules and
failure handling live in one module. Its external **seam** is the run request;
provider and platform seams remain private to the implementation.

The deletion test is favorable: deleting the module would spread artifact
checks, environment isolation, provider startup, configuration rendering,
process ownership, evidence capture, and cleanup across every caller.

## Public interface

### CLI

The ordinary caller runs one command:

```sh
chatgpt-offline run --manifest offline-run.yml
```

Equivalent flags may generate the same manifest for interactive development,
but they do not add behavior:

```sh
chatgpt-offline run \
  --profile optiq-qwen35-2b@1 \
  --source <verified-5263-archive> \
  --project <local-project> \
  --run-root <empty-disposable-directory> \
  --evidence <evidence-directory>
```

There is no default app under `/Applications`, no implicit normal profile, and
no global configuration mutation. `run` refuses the installed or currently
running app and derives a separately named copy inside the disposable run root.
Renaming the outer bundle directory does not alter bundle contents.

### Programmatic interface

The same external seam has one operation:

```text
runOfflineBaseline(request: RunRequest) -> RunResult
```

`RunRequest` contains only:

- a schema version and closed profile ID;
- the source artifact and disposable run root;
- the local project path;
- staged artifact references required by that profile;
- an output directory for evidence; and
- secret references, never secret values.

`RunResult` contains:

- `passed` or `failed`;
- the terminal lifecycle phase;
- a typed failure when present;
- the evidence-manifest path and digest; and
- an explicit cleanup verdict.

The interface never returns a live app, server, gateway, or process handle.
Process ownership cannot escape the module.

### Run manifest

The operator-owned manifest is intentionally smaller than the internal profile:

```yaml
schema_version: 1
profile: optiq-qwen35-2b@1
source:
  archive: <path-to-staged-official-archive>
project:
  path: <path-to-local-project>
run_root: <empty-disposable-directory>
evidence_dir: <evidence-directory>
artifacts:
  model_snapshot: <path-to-verified-model-snapshot>
```

The GLM form adds only its required external input:

```yaml
schema_version: 1
profile: glm-5.2@1
source:
  archive: <path-to-staged-official-archive>
project:
  path: <path-to-local-project>
run_root: <empty-disposable-directory>
evidence_dir: <evidence-directory>
provider:
  endpoint: <explicit-air-gapped-glm-endpoint>
  secret_ref: env:GLM_API_KEY
```

Unknown fields are errors. Callers cannot override `wire_api`, auth policy,
model fallback, network policy, runner arguments, or artifact digests. Those
are profile invariants.

### Closed profiles

Profiles are versioned implementation data, not user-authored adapter graphs.
The initial OptiQ profile fixes:

- exact app build and hashes above;
- model repository and revision
  `mlx-community/Qwen3.5-2B-OptiQ-4bit@adc8669eb431e3168aeb4e320bd7b757914350e2`;
- `:no-think`, `--kv-bits 4`, `--max-concurrent 1`, no MTP, no vision, and no
  Anthropic listener;
- Python `3.12.13`, `mlx-optiq 0.2.15`, and `mlx-lm` fix revision
  `ab1806e8f5d6aa035973af194a1b9198ab4754dc`;
- a Responses-only authenticated gateway;
- prelaunch `requires_openai_auth = false` configuration; and
- hosted egress denial and evidence requirements.

The GLM profile fixes the same app, isolation, gateway, config, and evidence
policy, while requiring the explicit endpoint and provider secret. It has no
model or provider fallback. If GLM is unavailable, it returns
`ExternalProviderUnavailable` before app launch.

Changing a pinned artifact, runner, protocol policy, or validation contract
creates a new profile version. It does not silently change an existing profile.

## Deep lifecycle module

Internally, `OfflineBaselineRunner` is a state machine:

```text
resolve -> preflight -> derive -> initialize -> ready -> launch
        -> validate -> capture -> cleanup -> complete
```

Every transition is owned by the module. Failure at any phase moves through
`capture` and `cleanup`; evidence or cleanup failure keeps the overall result
red.

The implementation hides:

1. profile resolution and closed-schema validation;
2. exact artifact and model-manifest verification;
3. safe destination checks and separately named bundle derivation;
4. fresh `HOME`, `CODEX_HOME`, Electron user data, temporary, and log roots;
5. an allowlisted child environment with no inherited OpenAI, GitHub, proxy,
   shell-init, model, or application credential;
6. provider, gateway, and observer process startup and exact-PID ownership;
7. prelaunch `config.toml` rendering and disposable gateway credential delivery;
8. a full app/host cold start after configuration exists;
9. runtime probes and minimum offline workflow assertions;
10. redacted, artifact-bound evidence capture; and
11. reverse-order shutdown and isolated-state cleanup.

The generated Codex configuration always uses the gateway:

```toml
model = "<profile-exact-model-id>"
model_provider = "offline-gateway"
request_max_retries = 1
stream_max_retries = 1

[model_providers.offline-gateway]
name = "Offline baseline gateway"
base_url = "http://127.0.0.1:<allocated-port>/v1"
env_key = "OFFLINE_GATEWAY_TOKEN"
wire_api = "responses"
requires_openai_auth = false
```

The app receives only a per-launch gateway token. The OptiQ adapter needs no
upstream secret. The GLM adapter reads the real GLM secret at its seam and
applies it to the one allowlisted upstream endpoint; the app, generated config,
manifest, logs, and evidence never receive it.

## Internal seams

Internal seams exist only where behavior really varies:

| Concern | Design now | Reason |
| --- | --- | --- |
| Provider lifecycle | Private `ProviderAdapter` with OptiQ and GLM adapters | Two real lifecycles exist: start and own local OptiQ, or health-check an operator-owned GLM endpoint. |
| Build | No adapter; exact `Build5263Policy` data | Only one build is supported. A second verified build would justify a build seam. |
| Responses dialect | Gateway pass-through first; no translator registry | Both selected paths claim Responses. Add a translation adapter only after a captured incompatibility proves a second behavior. |
| Model metadata | Pinned profile facts plus a validator | A local catalog injection seam is not yet proven. Ticket 08 must fail visibly if fallback metadata violates the contract. |
| Extensions | Private bundled-only and preseeded-local staging adapters | These are two real local payload sources; neither allows remote discovery. |
| Validation | Private static and runtime validators behind one result | Artifact, process, protocol, UI, workflow, egress, and cleanup checks have different mechanisms but one caller-facing verdict. |

Tests cross the runner interface. Focused tests may use the internal seams, but
those seams are not promoted into the public interface for test convenience.

## Dependency classification

### In-process

Profile parsing, state transitions, manifest comparison, failure
classification, evidence indexing, redaction policy, and deterministic config
rendering are in-process. Test them directly through the runner interface with
fixed inputs.

### Local-substitutable

The filesystem, process supervisor, local Git/worktree operations, loopback
gateway, OptiQ runner, local project state, and preseeded extension payloads are
local-substitutable. Tests use temporary roots, fixture bundles, fake child
processes, and an inert loopback Responses endpoint. The final Ticket 08 check
uses the real exact artifacts on Apple silicon.

### True external

The air-gapped GLM endpoint is true external. The GLM provider adapter accepts
its endpoint and secret reference. Tests use a mock adapter and a contract
endpoint; a real GLM validation is possible only when that environment exists.
Unavailability is an explicit profile failure and never selects OptiQ.

No OpenAI-hosted service is a dependency of the minimum offline workflow.
ChatGPT tasks, remote control, usage, billing, remote catalogs, sharing,
Statsig, and telemetry remain hosted-only surfaces. They are denied or allowed
to fail closed; the runner does not emulate them.

### Apple platform

Apple code signing, Gatekeeper/notarization assessment, Electron's signed code
graph, process inspection, loopback socket ownership, and the egress-denial
mechanism are platform dependencies. They are not mocked away in acceptance.
The final isolation mechanism must preserve the vendor Chromium sandbox; a run
that requires `--no-sandbox`, DYLD injection, disabled library validation, new
entitlements, or re-signing fails this architecture.

## Ordering and invariants

The lifecycle order is mandatory:

1. Resolve the closed profile and all secret references without logging values.
2. Refuse the system Applications directory, normal app state, nonempty run
   roots, running targets, symlink escapes, and the currently executing harness.
3. Verify the official archive digest, exact build, architecture, strict deep
   signature, notarization expectation, nested code, ASAR, Codex, and security
   metadata before deriving a run copy.
4. Derive a separately named copy under the disposable run root and prove its
   bundle contents match the verified source.
5. Create fresh isolated `HOME`, `CODEX_HOME`, Electron state, temporary, and
   evidence roots. Never copy cookies, Keychain items, app-group data, account
   state, or normal Codex state.
6. Verify staged model, runner, and extension artifacts before starting any
   process. Runtime download and resolution of a moving remote branch are
   forbidden.
7. Start the provider adapter, then the authenticated gateway, then readiness
   and deterministic Responses probes.
8. Render configuration and an allowlisted child environment before the first
   app/host start. Provider or auth-policy changes require another cold start.
9. Establish hosted egress denial and observation without weakening the vendor
   sandbox, then launch the separately named app copy.
10. Validate account policy, workspace entry, model turn, workflow behavior,
    persistence, isolation, and integrity.
11. Capture evidence on success or failure, stop exact owned PIDs in reverse
    order, close sockets, and verify cleanup. Never kill by process name.

Other invariants:

- The vendor bundle remains byte-for-byte pristine before and after every run.
- The production app, profile, Keychain, app-group data, updater, global Codex
  configuration, and machine-wide model runner remain untouched.
- Only the gateway may receive the disposable gateway token. Only the GLM
  adapter may receive the GLM secret.
- The gateway binds loopback, authenticates every request, allowlists the
  Responses paths and methods, redacts values, and permits only the
  profile-selected upstream.
- Any successful hosted connection, unexpected inherited credential, artifact
  drift, model fallback, unowned process, or incomplete cleanup makes the run
  red.
- Expected denied hosted attempts may be recorded as degraded behavior; they
  may not receive credentials or block the local workflow.

## Error model

Failures are typed and phase-bound:

| Failure | Meaning |
| --- | --- |
| `ManifestInvalid` | Schema, profile, unknown field, or required input is invalid. |
| `UnsafeDestination` | Source, destination, state root, symlink, running app, or harness path violates isolation. |
| `ArtifactMismatch` | App, model, runner, extension, signature, or security metadata differs from the profile. |
| `SecretUnavailable` | A required secret reference cannot be resolved at the adapter seam. |
| `ExternalProviderUnavailable` | The explicit GLM endpoint is absent or fails its readiness contract. |
| `ProviderStartFailed` | OptiQ fails to bind, load, or report the exact model ID. |
| `GatewayContractFailed` | Authentication, allowlist, Responses probe, or upstream policy fails. |
| `ConfigRejected` | Bundled Codex rejects the generated provider configuration. |
| `AuthPolicyMismatch` | `account/read` does not return `account: null` and `requiresOpenaiAuth: false`. |
| `StartupFailed` | The exact app fails to reach the local workspace route after cold start. |
| `WorkflowAssertionFailed` | A minimum offline workflow behavior fails its observable contract. |
| `HostedEgressSucceeded` | A hosted request escaped the deny policy or received a credential. |
| `IntegrityDrift` | The bundle or security posture changes during the run. |
| `EvidenceIncomplete` | A required artifact, assertion, or redaction check is absent. |
| `CleanupIncomplete` | An owned process, socket, mount, or state path remains unexpectedly live. |

Errors include profile, phase, safe artifact identifiers, causal diagnostics,
and evidence pointers. They never include secrets, raw authorization headers,
prompt bodies, or inherited environment values. Provider failure, metadata
failure, and GLM unavailability never trigger an automatic fallback.

## Performance and resource contract

The OptiQ profile is intentionally bounded for a scant-RAM Apple-silicon
machine:

- one model server and `--max-concurrent 1`;
- 4-bit model and KV cache;
- short deterministic smoke prompts;
- no vision, MTP, or Anthropic compatibility listener;
- one app run at a time; and
- exact-PID shutdown as soon as validation completes.

The canonical model probe used about `1,881 MB` current physical footprint and
`3,054 MB` peak. The exploratory process later reached a conservative
`4,730 MB` lifetime peak after additional alias and cache probes. Ticket 08
must measure the complete app, provider, gateway, and observer run under memory
pressure and report both steady state and peak.

Observed local request times were about `0.564 s` for deterministic text,
`3.267 s` for the first required tool call including model load, and `0.706 s`
for continuation. These seed timeout budgets but are not throughput promises.
Timeouts must be phase-specific, leave evidence, and enter cleanup.

The complete staged model repository is about `2.1 GB`, including optional
sidecars; the language-model artifact is about `1.4 GB`. A reduced air-gap
manifest may omit vision or MTP files only after a separate verified load test.

## Artifact-bound, red-capable evidence

Every run emits an evidence directory even when it fails. Its top-level
manifest contains:

- profile and runner versions;
- source archive, app, ASAR, Codex, model, runner, and extension digests;
- signature, notarization expectation, entitlements, fuses, architecture, and
  nested-code inventory results;
- isolated path roles using redacted or run-relative names;
- lifecycle transitions and monotonic timestamps;
- provider and gateway readiness, bound addresses, and exact owned PIDs;
- sanitized request/response shapes for the deterministic text and tool probes;
- `account/read`, route, workflow, persistence, permission, and mode assertions;
- denied and unexpected network destinations without credentials or prompt
  bodies;
- before/after bundle manifests;
- cleanup checks for processes, sockets, state, and production-artifact
  noninteraction; and
- one terminal pass/fail verdict with the first typed failure and all cleanup
  failures.

Evidence is **artifact-red**: changing the exact app, selected model revision,
runner pins, profile version, or staged payload without updating the expected
profile makes preflight fail rather than producing a comparable green run.
Evidence is **red-capable**: a failed auth decision, tool call, permission
denial, egress rule, integrity check, or cleanup assertion produces a failed
bundle instead of an absent or hand-interpreted result.

Redaction is tested with generated canary values for the gateway token and any
mock upstream secret. Finding a canary in committed evidence is itself
`EvidenceIncomplete` and keeps the run red.

## Rejected alternatives

- **Renderer or main-process login patch:** rejects the host's truthful
  supported no-auth policy and modifies a sealed resource to reproduce
  behavior available through config.
- **Native patch, fuse change, or framework mutation:** has no behavioral-owner
  justification and carries the highest exact-build and signing risk.
- **DYLD or launch-time interposition:** requires hardened-runtime weakening
  absent from the vendor entitlements and is incompatible with the pristine
  acceptance contract.
- **Fabricated OpenAI account/bootstrap state:** broadens credential and account
  trust while supplying none of the hosted backing behavior.
- **Ad-hoc re-signed app as the normal result:** loses vendor identity,
  notarization, Team-ID authority, updater trust, and normal TCC semantics.
- **Direct use of unauthenticated OptiQ:** any same-user process could submit
  prompts to the server; the authenticated allowlisting gateway is a narrow
  local trust control.
- **Open adapter catalog in user config:** exposes lifecycle internals, allows
  unsupported combinations, and creates hypothetical seams.
- **Automatic OptiQ fallback for GLM:** hides true-external unavailability and
  invalidates the meaning of a GLM validation result.
- **Use of the installed app or normal profile:** risks the live development
  harness, credentials, state, updater, and user data.
- **Hosted-service emulation:** remote tasks, billing, sharing, usage, and
  catalogs are outside the minimum offline workflow and should remain
  explicitly unavailable.

## Implementation slices

These are plan slices, not implementation authorization:

1. **Contract and fixtures:** define closed manifest/profile schemas, typed
   results, lifecycle states, evidence schema, exact 5263 policy, and red test
   fixtures.
2. **Static preflight:** implement safe-path checks, artifact verification,
   profile resolution, isolated-root planning, and deterministic config
   rendering without launching anything.
3. **Local provider path:** reproduce the pinned OptiQ environment from a
   staged lock or wheelhouse, supervise the exact server, and implement the
   authenticated pass-through gateway.
4. **Isolated app lifecycle:** derive the separately named pristine copy,
   create isolated state, build the allowlisted environment, cold-start the
   app, observe it, and stop exact owned processes.
5. **Minimum workflow validator:** exercise startup, workspace, turn/tool,
   permissions, worktrees, persistence, modes, and local extensions through
   observable interfaces.
6. **Evidence and cleanup:** make every phase red-capable, prove canary
   redaction, verify production noninteraction, and make cleanup part of the
   verdict.
7. **Explicit GLM profile:** add the true-external provider adapter and direct
   Responses contract test. Add translation only for captured incompatibilities
   and validate it separately; never add fallback.

Each slice must leave a green interface-level contract. Do not preserve shallow
phase tests once the runner interface covers the same behavior.

## Ticket 08 validation contract

Ticket 08 should use `optiq-qwen35-2b@1` and the exact staged artifacts to
validate the preferred route end to end. It passes only when one evidence
manifest proves all of the following:

1. The source and separately named run copy match exact build 5263, strict deep
   signature verification passes, and bundle contents do not change.
2. All state roots are fresh and isolated; the production app, normal profile,
   Keychain, app-group state, updater, global Codex config, and unrelated model
   servers remain untouched.
3. The pinned model and runner artifacts verify, OptiQ binds loopback at single
   concurrency, and the authenticated gateway rejects a missing or wrong token.
4. Config exists before first launch, a cold host returns `account: null` and
   `requiresOpenaiAuth: false`, and the full renderer reaches the local
   project/workspace route without hiding UI or changing the bundle.
5. A deterministic local turn streams through bundled Codex, invokes a real
   local tool with checked arguments, consumes its output, and completes.
6. Permission handling proves one allowed and one denied operation by observed
   effect, not by prompt visibility alone.
7. A local project and worktree can be selected or created and survive a cold
   restart without remote Git.
8. A thread is materialized by a user message, then lists, reads, archives, and
   resumes after restart. Allocation alone is not treated as persistence.
9. Default and Plan modes produce their expected local turn settings.
10. Bundled and one preseeded local extension path work; a network-dependent
    extension fails explicitly without blocking the local core.
11. Hosted attempts are denied, receive no credential, and do not block the
    minimum offline workflow. The acceptance run preserves the vendor Chromium
    sandbox and does not use `--no-sandbox`.
12. Model metadata is checked against the pinned profile. If bundled fallback
    metadata cannot be constrained safely, the run is red with
    `WorkflowAssertionFailed`; it is not silently accepted.
13. Whole-run current and peak memory, cold-load latency, warm turn latency,
    and disk use are recorded without imposing an unsupported performance
    claim.
14. Success and injected failures both emit complete redacted evidence, and
    cleanup leaves no owned process or listener running.

The GLM profile can be contract-tested against an inert local endpoint in
Ticket 08. With no real air-gapped GLM endpoint available, the only valid live
GLM outcome is `ExternalProviderUnavailable`; the runner must prove that it
does not switch to OptiQ. Real GLM compatibility remains a later environment-
dependent validation, not a blocker for local development.

If configured pristine startup fails, Ticket 08 must preserve the red evidence
and identify the exact semantic owner before proposing another surface. The
escalation order remains supported configuration, then the smallest proven
JavaScript owner, then native mutation only as a last resort. Interposition and
updater mutation remain rejected.

## Research basis

- [Pristine offline startup](https://github.com/nisavid/agents/blob/716456a6af776b72a6643f85a0019f79bfb0564a/.scratch/chatgpt-airgap-unlock/research/03-pristine-offline-startup.md)
- [GLM provider path](https://github.com/nisavid/agents/blob/81bd2bfbff803ad50c7d4111ca00a062953d89fc/.scratch/chatgpt-airgap-unlock/research/04-glm-provider-path.md)
- [Minimum offline workflow classification](https://github.com/nisavid/agents/blob/283511d244ab5974c4626730735312b70f7a421e/.scratch/chatgpt-airgap-unlock/research/05-minimum-offline-workflow.md)
- [Integrity, signing, and security assessment](https://github.com/nisavid/agents/blob/59e9fa5800b2806064236d1bab0e5f5845681e96/.scratch/chatgpt-airgap-unlock/research/06-integrity-signing-security.md)
- [Local smoke model selection](https://github.com/nisavid/agents/blob/e8c7e1a95b33738f4234a93c915eb7d86214eb32/.scratch/chatgpt-airgap-unlock/research/10-local-smoke-model.md)
