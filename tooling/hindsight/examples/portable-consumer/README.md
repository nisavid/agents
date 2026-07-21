# Portable consumer example

These files show the closed consumer shape for macOS LaunchAgents and
CachyOS/systemd-user. They are templates, not runnable defaults.

The portable manager owns service manifests and lifecycle directly. Do not add
the standalone `hindsight-embed-service` launchd bindings to these service
environments, including the systemd-user template.

Before use:

1. Copy `inventory.json` and the platform installation file into the consumer
   configuration repository.
2. Replace every example home, state, executable, runner, catalog, and policy
   path, including `uvx_executable` and `zsh_executable`. Select `fresh` only
   for an empty data root; use `adopt` for an existing profile database.
3. Install a resolver implementing the credential protocol below. Replace the
   all-zero resolver digest with the resolver's SHA-256 digest.
4. For `fresh`, configure the selected Hindsight profile and bind its canonical
   bank to `engineering`. For `adopt`, inspect the existing profile and bank,
   leave them unchanged, and make the inventory match their identity.
5. Validate `inventory.json`, then run `hindsight-memory install` against an
   immutable release tree.

Set every `HINDSIGHT_EMBED_UVX` binding to
`release://bin/hindsight-embed-uvx`. The release-owned wrapper keeps managed
stack commands on exactly `hindsight-embed==0.8.4`; upgrading the Hindsight
server remains an explicit, separately validated release decision. The
top-level `uvx_executable` selects the protected absolute `uvx` runtime; the
installer validates it and the managed launcher injects it without consulting
the service's `PATH`. The top-level `zsh_executable` similarly pins Zsh
entrypoints to a protected runtime invoked with startup files disabled.

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
digest and copies it into the private managed install root before activation.
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

A launchd integration job checks one catalog when loaded and at its configured
daily time. A systemd-user timer checks two minutes after its user manager
starts and at its configured daily time. Create one timer per enabled harness
when distinct upstream catalogs are used.
