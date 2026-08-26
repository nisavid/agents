# Portable consumer example

These files show the closed consumer shape for macOS LaunchAgents and
CachyOS/systemd-user. They are templates, not runnable defaults.

The `/absolute/path/to/...` values and all-zero resolver digest are deliberate
unresolved sentinels. Do not replace them with paths or digests from the
machine publishing this repository. A consumer must resolve them for its own
host; portable install preflight rejects missing or unprotected executables and
rejects a resolver whose bytes do not match the configured digest before any
managed state or service is changed.

The portable manager owns service manifests and lifecycle directly. Do not add
the standalone `hindsight-embed-service` launchd bindings to these service
environments, including the systemd-user template.

Before use:

1. Copy `inventory.json` and the platform installation file into the consumer
   configuration repository.
2. Replace every example home, state, executable, runner, catalog, and policy
   path, including `npx_executable`, `uvx_executable`, and `zsh_executable`.
   Select `fresh` only for an empty data root; use `adopt` for an existing
   profile database.
3. Install a resolver implementing the credential protocol below. Replace the
   all-zero resolver digest with the resolver's SHA-256 digest.
4. For `fresh`, configure the selected Hindsight profile and bind its canonical
   bank to `engineering`. For `adopt`, inspect the existing profile and bank,
   leave them unchanged, and make the inventory match their identity.
5. Validate `inventory.json`, then run `hindsight-memory install` against an
   immutable release tree.

Set every `HINDSIGHT_EMBED_UVX` binding to
`release://bin/hindsight-embed-uvx`. The release-owned wrapper keeps managed
stack commands on exactly `hindsight-embed==0.8.4`, defaults and allowlists the
nested API runtime to `hindsight-api==0.9.2`, and leaves the UI runtime at
`0.8.4`; upgrading any component remains an explicit, separately validated
release decision. An installation environment may bind
`HINDSIGHT_EMBED_API_VERSION` only to `0.9.2`. A managed profile must omit that
key or bind the same value because upstream profile configuration takes
precedence, and the existing daemon must be stopped before changing versions.
The top-level `uvx_executable` selects the protected absolute `uvx` runtime; the
installer validates it and the managed launcher injects it without consulting
ambient `PATH`. The top-level `npx_executable` selects the protected UI package
runner. The launcher supplies both exact executable bindings to the
release-owned embed wrapper, which constructs its child `PATH` from only their
validated directories and protected system directories. The top-level
`zsh_executable` similarly pins Zsh entrypoints to a protected runtime invoked
with startup files disabled.

The resolver receives one strict JSON object on standard input:

```json
{"credentials":[{"environment":"HINDSIGHT_DATA_PLANE_TOKEN","locator":"pass://hindsight/data-plane"}],"schema_version":1}
```

It returns exactly one value for each requested environment name:

```json
{"schema_version":1,"values":{"HINDSIGHT_DATA_PLANE_TOKEN":"resolved-at-runtime"}}
```

The resolver should retrieve each locator from a protected store such as
`pass`, the macOS Keychain, or Secret Service. It must write no diagnostics or
secret values to logs. Its file and ancestry must be owned by the current user
or root and must not be group- or world-writable. The installer verifies its
configured path and digest before activation and keeps a protected copy as
installer-owned rollback and uninstall evidence. Every managed launch repeats
the configured-path verification and executes that exact resolver in place so
native credential-store ACLs remain bound to its final installed path.
The managed launcher supplies a trusted `HOME`, `USER`, and `LOGNAME`, a
minimal system `PATH`, and a validated bound user-session bus when one is
available. Resolver implementations must invoke non-system helpers such as a
Homebrew `pass` or `secret-tool` by protected absolute path rather than relying
on ambient `PATH`.
The source configuration stores only its absolute path, digest, and opaque
locators; the managed launcher injects resolved values only into the authorized
service process.

Credential bindings may target only `HINDSIGHT_API_KEY`,
`HINDSIGHT_DATA_PLANE_TOKEN`, `HINDSIGHT_MINT_AUTHORITY`, or
`HINDSIGHT_UI_ACCESS_KEY`. This positive contract prevents a resolved secret
from becoming a language-runtime or dynamic-loader control value.

Keep `HINDSIGHT_API_TENANT_EXTENSION` bound to
`hindsight_api.extensions.builtin.tenant:ApiKeyTenantExtension` in both the
service and health-check environments. The launcher maps the resolved data
plane token to `HINDSIGHT_API_TENANT_API_KEY` only for the API child; the
extension selector is non-secret and does not grant authority by itself.
Keep upstream audit logging explicitly disabled. The example also disables LLM
request tracing; a consumer that enables it must declare a bounded retention
policy. Replace the example worker ID with a stable consumer-and-profile
identity.
Keep the broker startup budget long enough for the first authenticated runtime
probe and route compilation; the examples use five minutes.

A launchd integration job checks one catalog when loaded and at its configured
daily time. A systemd-user timer checks two minutes after its user manager
starts and at its configured daily time. Create one timer per enabled harness
when distinct upstream catalogs are used.
