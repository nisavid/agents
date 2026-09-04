# Agent Plugins Standard Adoption Research

> Historical record. The current document lives in
> [`nisavid/provingkit`](https://github.com/nisavid/provingkit/blob/main/docs/superpowers/research/2026-08-12-agent-plugins-standard-adoption.md).
> This copy preserves evidence tied to the former repository layout; it is not
> an active package, validation, or release route.

Status: research checkpoint; implementation and release versions remain
unsettled

Research date: 2026-08-12

This note evaluates whether the six packages in `plugins/` can use the Agent
Plugins v1 format as their canonical package shape and identifies the native
packaging that remains necessary. It is based on the current repository tree,
the official Agent Plugins and Agent Skills specifications, official client
documentation, and OpenAI Codex source at commit
[`74004b5397b24662a87a5264a6ae80664168c7f3`](https://github.com/openai/codex/tree/74004b5397b24662a87a5264a6ae80664168c7f3).
Generic OpenAI product documentation is not used to infer Codex loader
behavior. OpenAI's July 9 update to the current
[developer manual](https://developers.openai.com/) is used only for the public
product boundary: the former standalone Codex app is now part of the ChatGPT
desktop app, while the Codex CLI/TUI remains a separate execution surface.

## Conclusion

Adopt an Agent Plugins v1 root `plugin.json` as the canonical package manifest
for all six packages. Keep one portable `skills/` tree. Put Codex package UI
metadata under the official Codex extension namespace, `extensions.com.openai`,
and retain each skill's `agents/openai.yaml` as Codex-specific skill metadata.

Do not remove Claude Code packaging. Claude Code does not document support for
root Agent Plugins manifests and is absent from the standard's compatible-client
list. Every package therefore still needs `.claude-plugin/plugin.json` for the
Claude marketplace. Tricritical also needs its six native Claude agent files.
The Claude manifest should become a validated projection of the canonical
manifest rather than an independent identity source.

Codex can use the standard package for everything the current slate requires:
portable skills, portable MCP if one is added, package interface metadata, and
skill UI metadata. Current Codex source deliberately does not load apps or hooks
from a standard-format package. A future package that requires either capability
would need Codex-native packaging until that loader behavior changes and is
release-tested.

Hermes Agent and OpenClaw are referenced as scheduling targets, not as current
repository marketplace targets. Both officially load the standard's portable
skills and MCP subset, so the present portable payload alone does not require a
native package. That does not qualify the skills' ambient runtime dependencies.
Their native formats would be required if the product later promises their
non-portable agents, commands, hooks, rules, tools, or settings. ChatGPT and
Claude Desktop scheduling envelopes are not Agent Plugin components.
Independently, Rolecasting treats ChatGPT Codex and Claude Desktop as named
execution surfaces with separate capability and assurance qualification.

Task Witness is the exceptional package. A manifest-only Agent Plugin is valid,
but Task Witness has no portable component: it is a code-only source-stage
payload consumed by its own deployment controller. Standardizing its identity is
useful, but it does not make its Python and shell control plane loadable by an
Agent Plugins client. Its controller and release evidence must be migrated
explicitly.

## Canonical terminology

| Term | Meaning in this repository |
| --- | --- |
| Agent Plugin | One self-contained package directory with a root `plugin.json`, optional portable components, and optional client extensions. |
| Component | A unit standardized by Agent Plugins v1. There are exactly two component types: **Agent Skills** and **MCP servers**. |
| Agent Skill | A direct child of `skills/` containing `SKILL.md`; `scripts/`, `references/`, `assets/`, and other skill-local files are resources of that skill, not additional component types. |
| MCP server | An entry in root `mcp.json` using the standard's stdio, Streamable HTTP, or legacy SSE shape. |
| Client extension | Client-owned data under `plugin.json` `extensions.<reverse-domain-name>`, files under a same-named top-level directory, or both. The standard assigns no portable semantics to it. |
| Native component | A feature packaged by one client but not standardized by Agent Plugins v1: for example Claude agents and hooks, Codex apps and hooks, Cursor rules and commands, or Hermes tools. |
| Adapter manifest | A client-native manifest retained only because that client cannot consume the canonical standard package or needs a native component. It is a generated or validated projection, not a second product identity. |
| Package support artifact | README, license, changelog, topology, eval corpus, content lock, release receipt, or validation metadata. These ship with or validate a package but are not Agent Plugins component types. |
| Scheduling adapter | An invocation envelope for a host's scheduled-task system. It calls a skill but is not itself plugin equipment. |
| Ambient runtime capability | A client or environment facility that instructions call but the package does not provide: shell/file access, Git or forge access, user elicitation, native delegation, model selection, browser/computer use, or scheduling. Format support does not prove these capabilities. |

The normative [Agent Plugins v1.0.0 specification](https://agent-plugins.org/specification)
is currently labeled **Working Draft**. It requires a root `plugin.json`, fixes
portable discovery at `skills/` and `mcp.json`, permits missing component
locations, and defines exactly those two component types. Its manifest schema is
closed; client data belongs under a reverse-domain extension namespace. The
[client-extension guide](https://agent-plugins.org/plugin-authors/client-extensions)
does not create new portable semantics: the namespace owner still defines how
extension data and files load.

The [Agent Skills specification](https://agentskills.io/specification) makes
`SKILL.md` the only required file and permits `scripts/`, `references/`,
`assets/`, and arbitrary additional skill-local files. It requires `name` and
`description` frontmatter and recommends skill-root-relative, shallow resource
references.

## Standard core and extension model

The desired model has one portable identity and explicit client projections:

```text
plugin package
├── plugin.json                 canonical Agent Plugins identity and metadata
│   └── extensions
│       └── com.openai          Codex package interface metadata
├── skills/                     portable Agent Skills, when present
│   └── <skill>/
│       ├── SKILL.md
│       ├── scripts|references|assets/...
│       └── agents/openai.yaml  Codex skill UI metadata; ignored elsewhere
├── mcp.json                    portable MCP configuration, when present
├── .claude-plugin/plugin.json  required Claude adapter projection
├── agents/*.md                 Claude-native Tricritical aliases only
└── package support artifacts   validation and release data, not components
```

The root manifest owns name, release version, description, author, repository,
homepage, license, and keywords. A harness adapter may add only data that cannot
be represented in the portable manifest or its implemented extension namespace.
Release validation must prove that duplicated fields are exact projections.

Do not use symlinks to manufacture the projections. The standard requires every
resolved package path to remain inside the package root, and client packagers and
caches do not provide a uniform symlink contract. Generate ordinary files and
verify their bytes or semantic projection instead.

## Local equipment inventory

All 25 current skills use only the required `name` and `description`
frontmatter. Every skill contains `agents/openai.yaml`. No package currently
declares an MCP server, hook, command, app, LSP server, output style, theme,
monitor, or package settings file.

| Package | Portable skill equipment | Native or package-specific equipment | Standardization consequence |
| --- | --- | --- | --- |
| Artifact Customs | 3 skills; 3 Codex skill metadata files | Four package-level contracts under `plugins/artifact-customs/references/`; scheduler envelopes mention ChatGPT, Claude Desktop, Hermes, and OpenClaw; topology, docs, license, changelog | Standard skills fit. Package-level references must be proven accessible in every client or projected into consuming skill directories. Scheduling remains outside the plugin format. |
| Mergecraft | 9 skills; 30 bundled scripts; 7 bundled references; 9 Codex skill metadata files | Topology, docs, license, changelog | Standard skills fit. Scripts and references are Agent Skill resources. Cross-skill links need portability tests. |
| Rolecasting | 2 skills; 4 bundled references; 2 Codex skill metadata files | ChatGPT Codex, Codex CLI/TUI, Claude Code, Claude Desktop, Cursor, and Cursor Agent are planned execution surfaces; topology, eval data, content lock, docs, license, changelog | Standard skills carry portable topology policy. Peer launch, observation, and attestation are ambient behavior, not plugin equipment. Surface support does not imply qualified issuance or package distribution. |
| Task Witness | None | Python and shell code in `client/`, `controller/`, `launcher/`, `runtime/`, and `smoke/`; controller policy; Claude adapter projection | A minimal standard manifest is valid, but no standard component exposes this code. The root manifest is canonical identity; the controller retains only the explicit adapter evidence required by each source epoch. |
| Tricritical | 7 skills; 6 bundled references; 7 Codex skill metadata files | 6 Claude-native agent aliases in `agents/`; 3 package-level shared contracts and `topology.json`; evals, content lock, NOTICE, docs, license, changelog | Standard skills fit. Claude agents require native Claude packaging. Shared package-level resources need projection or cross-client proof. |
| Versionkeeping | 4 skills; 11 bundled scripts; 3 bundled references; 4 Codex skill metadata files | Topology, docs, license, changelog | Standard skills fit. Scripts and references are Agent Skill resources. Cross-skill links need portability tests. |

The repository marketplace lists the five prompt-bearing packages in
`.claude-plugin/marketplace.json`. Task Witness is intentionally source-stage
only and absent until qualification. Every current package has one canonical
root Agent Plugins manifest and an exact Claude adapter projection; current
packages have no independently authored `.codex-plugin` manifest. Task
Witness's current controller binds the root manifest and Claude projection.
Its explicit legacy migration parser retains exact Claude and Codex manifest
evidence only for the frozen F5 and B1 receipt epochs.

Two content layouts deserve explicit migration tests:

- Artifact Customs and Tricritical link from `skills/*/SKILL.md` to package-level
  `../../references/` files; Tricritical also links to package-level
  `../../topology.json`.
- Several packages link between sibling skill directories.

Those paths remain within the package root, so Agent Plugins containment does
not reject them. They are nevertheless weaker than self-contained Agent Skills:
the Agent Skills specification describes skill-root resources and not every
client promises arbitrary package-sibling reads. Prefer a build projection that
places each runtime-read file below each consuming skill while keeping a single
authored source, unless live qualification proves the shared layout on every
supported client.

### Ambient runtime capability inventory

These requirements affect whether a skill works on a client, but none is an
Agent Plugins component type:

| Capability | Consumers | Packaging implication |
| --- | --- | --- |
| File reads, shell execution, and Python/shell interpreters | Scripted Mergecraft and Versionkeeping flows; Task Witness control plane; most repository inspection | Agent Skills may carry scripts, but the Agent Skills specification says supported languages depend on the client. Qualify executability, permissions, cwd, and dependency behavior per client. |
| Git, GitHub/forge access, and Graphite CLI | Versionkeeping, Mergecraft, Artifact Customs | Ambient tools and credentials. Do not encode them as MCP unless a product decision intentionally changes the execution contract. |
| Native subagent lifecycle, bounded handoff, and model/effort selection | Rolecasting and Tricritical | The portable skills express the policy. Claude aliases are native convenience entry points. Each supported client still needs capability probes and topology tests; Agent Plugins conformance alone is insufficient. |
| Operator elicitation and approval boundaries | Rolecasting, Tricritical, Artifact Customs, publication flows | Keep instructions client-neutral. Map the same decision boundary to each client's native user-input mechanism; do not package a command solely to rename that UI. |
| Browser/computer-use worker or foreign-harness dispatch | Rolecasting and workflows that explicitly select those peers | Ambient, authority-gated execution. It is not an Agent Plugins component or an implied capability of a compatible client. |
| Scheduled task creation and invocation envelopes | Artifact Customs | Remain host-specific external configuration. The installed skill is the invoked behavior authority. |
| Persistent deployment state, staged rollback, and validator execution | Task Witness | Owned by Task Witness's controller/client contract, not by portable MCP `PLUGIN_DATA`. Standard manifest adoption must not reclassify or weaken that trust boundary. |
| Package and skill presentation metadata | All prompt packages | Root portable metadata plus Codex `com.openai.interface` and skill-local `agents/openai.yaml`; Claude presentation is a native manifest projection. |

Therefore, “use the standard package” means only that no additional package
format is required to transport the accepted behavior and resources. A client is
supported only after both its loader and all ambient capabilities used by the
relevant skills pass the release gates below.

### Rolecasting execution surfaces

Rolecasting's execution-surface roadmap is distinct from marketplace
distribution. Its initial release supports ChatGPT Codex and Codex CLI/TUI for
ordinary delegation, as separately qualified surfaces. The fast follow is
Claude Code plus Claude Desktop, again as separate surfaces. Cursor plus Cursor
Agent follows as a pair, either together or in close succession. The policy
records exact family, surface, version, executor, relationship, ownership,
transport, and assurance; model choice is a separate receipt.

Portable instructions may support these names now, but support is not evidence
issuance. A real adapter must qualify each exact surface before it can issue
controller-observed or product-attested evidence. Rolecasting's current
provider remains validator-only. Ordinary delegation may consume
controller-observed evidence; self-reported evidence is diagnostic only and
cannot satisfy a hard gate. Canonical publication remains limited to a
product-attested ChatGPT Codex child.

## Client support matrix

“Core” below means only the standard's two named component types. It does not
mean every feature that a client calls a plugin component.

| Client or host | Repository role | Standard core support | Client-specific features relevant here | Required packaging |
| --- | --- | --- | --- | --- |
| Codex | Current release target | Skills and MCP. Current source recognizes only the v1.0.0 schema, gives root `plugin.json` discovery precedence, and uses direct-child skill discovery. | `extensions.com.openai.interface` can carry current package UI metadata. `agents/openai.yaml` is Codex's product-specific skill metadata. Current standard-format loader returns no apps and no hooks even though extension parsing can describe them. | Standard package for the present slate. No `.codex-plugin` adapter after interface data is moved under `com.openai`, unless a later app/hook requirement appears or release qualification exposes another loader gap. |
| Claude Code | Current marketplace and release target | No official Agent Plugins support found; Claude Code is absent from the standard's compatible-client list. | Native skills/commands, agents, hooks, MCP, LSP, monitors, settings, themes, and output styles. Tricritical uses six native agents. | Keep `.claude-plugin/plugin.json` for all six. Keep Tricritical `agents/*.md`. Reuse the same `skills/` tree. |
| Cursor | Referenced foreign-harness peer; not a current package target | Officially supports Agent Plugins skills and MCP. | Cursor-native rules, agents, commands, hooks, and variables require `.cursor-plugin/plugin.json`. None is presently required by the repository. | No package added now. If scope expands to the current skill surface, use the standard package; add a native adapter only for a newly accepted Cursor-specific capability. |
| Hermes Agent | Referenced Artifact Customs scheduler/foreign host; not a current marketplace target | Official compatibility adapter loads standard skills plus stdio and Streamable HTTP MCP. It does not treat portable packages as native `plugin.yaml`/`register(ctx)` plugins. | Native tools, lifecycle hooks, slash commands, CLI commands, and other providers are outside the portable subset. None is package equipment here. | The standard package is sufficient for the referenced skill invocation. Scheduling configuration remains external. Task Witness remains inert as a code-only package. |
| OpenClaw | Referenced Artifact Customs scheduler/foreign host; not a current marketplace target | Official bundles load Agent Plugins skills and MCP and provide `PLUGIN_ROOT`/`PLUGIN_DATA` for portable MCP. | Native bundles can map additional Claude, Codex, or Cursor features, but Claude agents are detected and not executed. None is required for the referenced Artifact Customs invocation. | The standard package is sufficient for the referenced skill invocation. Scheduling configuration remains external. Task Witness remains inert as a code-only package. |
| ChatGPT scheduled tasks | Host for the Codex scheduling adapter | Not a separately packaged target in this repository. | Schedule activation, cadence, and autonomy mode live in Artifact Customs's invocation contract. | No new plugin package. Continue to invoke the canonical skill through a host-specific schedule envelope. |
| Claude Desktop scheduled tasks | Host for the Claude Code scheduling adapter | Not a separately packaged target in this repository. | Same scheduling boundary as ChatGPT. | No new plugin package beyond the Claude Code adapter; keep scheduling external. |

### Codex evidence

These conclusions come from current Codex source, not generic OpenAI docs:

- [`plugin_namespace.rs`](https://github.com/openai/codex/blob/74004b5397b24662a87a5264a6ae80664168c7f3/codex-rs/utils/plugins/src/plugin_namespace.rs#L9-L64)
  recognizes the v1.0.0 schema and selects root `plugin.json` before legacy
  manifests.
- [`agent_plugin_manifest.rs`](https://github.com/openai/codex/blob/74004b5397b24662a87a5264a6ae80664168c7f3/codex-rs/core-plugins/src/agent_plugin_manifest.rs#L16-L214)
  fixes the Codex namespace at `com.openai`, maps standard `skills/` and
  `mcp.json`, and applies package interface data from the extension. It can use a
  `.codex-plugin/plugin.json` overlay only as an implementation fallback.
- [`manifest.rs`](https://github.com/openai/codex/blob/74004b5397b24662a87a5264a6ae80664168c7f3/codex-rs/core-plugins/src/manifest.rs#L155-L184)
  shows that overlay lookup occurs only after root standard-manifest selection.
- [`loader.rs`](https://github.com/openai/codex/blob/74004b5397b24662a87a5264a6ae80664168c7f3/codex-rs/core-plugins/src/loader.rs#L875-L947)
  uses direct-child standard skill discovery and portable MCP, but gates app and
  hook loading to legacy format.
- [`openai_yaml.md`](https://github.com/openai/codex/blob/74004b5397b24662a87a5264a6ae80664168c7f3/codex-rs/skills/src/assets/samples/skill-creator/references/openai_yaml.md)
  identifies `agents/openai.yaml` as product-specific skill UI configuration.

### Other client evidence

- The official [compatible-client matrix](https://agent-plugins.org/compatible-clients)
  lists Codex, Cursor, Hermes Agent, and OpenClaw and their core subsets. It does
  not list Claude Code.
- The [Claude Code plugin reference](https://code.claude.com/docs/en/plugins-reference)
  documents `.claude-plugin/plugin.json` packaging and Claude-native skills,
  commands, agents, hooks, MCP, LSP, monitors, and related features.
- The [Cursor plugin documentation](https://cursor.com/docs/plugins) explicitly
  distinguishes standard skills/MCP from Cursor-native rules, agents, commands,
  hooks, and variables.
- The [Hermes plugin guide](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins#portable-agent-plugins-v1-packages)
  calls Agent Plugins a compatibility adapter for portable skills and MCP, not a
  replacement for native plugins.
- The [OpenClaw bundle documentation](https://docs.openclaw.ai/plugins/bundles)
  documents standard skills/MCP support and enumerates which foreign native
  features it maps, merely detects, or does not execute.

## Exact native-packaging gaps

1. **Claude installation and marketplace discovery.** The root standard
   manifest is not an official Claude Code package entry point. All six packages
   need a Claude adapter as long as Claude remains a release target.
2. **Tricritical Claude agents.** Agent Plugins v1 has no agent component type.
   Keep the six minimal `agents/*.md` aliases in Claude packaging. Codex and
   standard-only clients continue to invoke the seven public skills directly.
3. **Codex apps and hooks, if later introduced.** Current Codex source parses
   their extension paths but intentionally skips them for standard-format
   packages. No current package uses them, so this is a future conditional gap.
4. **Cursor, Hermes, or OpenClaw native features, if product scope expands.**
   Rules, tools, commands, agents, hooks, settings, and provider integrations are
   client-native. Current references to these clients require only skill
   invocation and do not justify native packages.
5. **Task Witness control-plane loading.** Python and shell files are not a
   standard component. Agent Plugins can carry them as package bytes, but a
   conforming client is not required to execute or expose them. Task Witness's
   own controller remains the loader and trust boundary.
6. **Shared package-level skill resources.** The standard does not define a
   shared-resource component. Either project runtime-read resources beneath each
   consuming skill or qualify the existing package-relative layout on every
   supported client.

## Recommended source-of-truth architecture

Use the checked-in standard package as the reviewable source, with deterministic
native projections:

1. Add root `plugin.json` to every `plugins/<name>/` and make it the sole
   authority for common identity and version fields.
2. Move the current Codex package `interface` object to
   `plugin.json` `extensions.com.openai.interface`. Keep skill-local
   `agents/openai.yaml` because it is already valid extra Agent Skill content and
   Codex reads it selectively.
3. Generate or validate `.claude-plugin/plugin.json` from the canonical fields,
   adding only Claude-native differences. Keep `.claude-plugin/marketplace.json`
   as the Claude repository catalog; its package versions must match the
   canonical versions.
4. Do not keep `.codex-plugin/plugin.json` as a second authored manifest when
   `com.openai` covers the current interface. Permit a generated native overlay
   only when a concrete, release-tested Codex feature cannot load from the
   standard package.
5. Keep `skills/` as the single authored portable behavior source. For shared
   runtime resources, choose one authored shared source and generate ordinary
   skill-local projections with byte-equality checks. Do not maintain divergent
   hand copies.
6. Keep Claude-only `agents/` small, exact forwarders to public Tricritical skill
   identities. They must not become a second review implementation.
7. Treat topologies, content locks, evals, release inventories, READMEs,
   changelogs, licenses, and Task Witness receipts as package-support contracts.
   Update their validators to understand the new shape; do not call them Agent
   Plugins components.
8. Give one immutable semantic release version to a package across its canonical
   manifest and all adapter projections. Client suffixes such as `+claude...` and
   `+codex...` describe packaging drift, not two product releases, and should not
   survive the canonicalization.

Task Witness should bind the canonical root manifest as release identity and
retain adapter digests as source-manager evidence only where a manager actually
consumes those adapters. This separates one product version from its packaging
projections without weakening byte-level provenance.

## Migration order

There is no existing plugin installation to preserve. The target is a clean
first installation of the completed slate. Historical Task Witness migration
fixtures still matter for release qualification, but they are not evidence of a
deployed user installation.

1. **Freeze the compatibility contract.** Pin Agent Plugins schema v1.0.0 and the
   exact client builds/source revisions used for qualification. Decide current
   distribution scope before adding new marketplace targets.
2. **Add format/projection tests first.** Validate the root schema, Agent Skills,
   projection equality, resource containment, direct-child discovery, and a
   fresh-install inventory before changing package manifests.
3. **Migrate Rolecasting.** It has only two skills, establishes delegation/model
   terminology used downstream, and has no native component.
4. **Migrate Versionkeeping, then Mergecraft.** Mergecraft composes publication
   and Git owners and has the largest script/resource surface; migrate it after
   the smaller scripted package proves the layout.
5. **Migrate Tricritical.** Preserve its Rolecasting dependency, qualify all
   seven portable skills, and separately prove all six Claude native aliases.
6. **Migrate Artifact Customs.** It composes the preceding owners and exercises
   package-level contracts plus scheduled-task adapters. Resolve its shared
   resource projection here.
7. **Migrate Task Witness last.** First freeze the TW4 behavior/control-plane
   candidate, then change canonical manifest identity, source-evidence parsing,
   immutable migration fixtures, and release receipts as one reviewed contract.
   This remains before the first public slate release.
8. **Regenerate release artifacts and qualify the complete slate.** Do not cut
   an intermediate public release merely to transport the source-shape change.

## Validation and release gates

### Format gates

- Every root `plugin.json` validates against
  `https://agent-plugins.org/schemas/1.0.0/plugin.schema.json`; unknown portable
  top-level fields are absent.
- Every direct child of `skills/` with `SKILL.md` passes the official
  `skills-ref validate` contract; names equal directory names.
- Every runtime-read path resolves inside the plugin root. Every client either
  loads shared package-level resources in a fresh install or the build projects
  them beneath the consuming skill.
- No component is claimed merely because a native client uses the same word.
  Standard inventories contain only skills and MCP entries.
- Canonical and adapter name, version, author, repository, description, and
  license projections agree exactly according to an explicit field map.

### Client gates

- **Codex:** on the pinned supported build, a fresh install selects root
  `plugin.json`, discovers the exact skill set as direct children, renders
  `com.openai.interface`, loads every `agents/openai.yaml`, can read/run every
  required skill resource, and reports no expected app/hook/native agent.
- **Claude Code:** `claude plugin validate` passes; the marketplace installs each
  listed package from scratch; each skill is invocable; all six Tricritical
  agents are discoverable and forward to the correct public skills; root
  `plugin.json` does not disturb native discovery.
- **Cursor, Hermes, OpenClaw:** no release claim is made until that target is in
  scope. If added, perform a real fresh install and representative skill/resource
  invocation on an exact supported build. A compatible-client-list entry is not
  a substitute for package qualification.
- **Scheduled hosts:** exercise the locked Artifact Customs invocation envelope
  through each supported host without copying behavior into scheduler config.

### Task Witness gates

- The controller treats root `plugin.json` as the canonical package identity and
  gives each retained native adapter an explicit evidence role.
- Frozen F5, bridge, and TW4 fixtures remain immutable. Any source-shape bridge
  is explicit; no validator silently treats a native manifest as equivalent to a
  root standard manifest.
- First-install, prepare, activate, rollback, interruption, reconciliation, and
  detached qualification receipts bind the exact new package bytes.
- Code-only Task Witness remains source-stage-only and absent from the public
  production-scoped marketplace/catalog until its detached qualification and
  registration gates close.

### Release gates

- Existing content locks, topology checks, evals, source-shape review, public
  release inventory, and package registration are regenerated from the unchanged
  candidate only after the source model is accepted.
- Clean-tree validation proves no generated adapter or projected resource is
  stale.
- The complete six-package slate passes on actual supported client builds from a
  fresh install. There is no upgrade claim against a user installation because
  none exists.
- One package version identifies one immutable release across standard and
  native projections. The exact first-release versions remain a separate release
  decision; this research does not assign F5, bridge, or TW4 numbers.

## Unresolved consequential decisions

1. **Adopt a Working Draft now?** Recommendation: yes, pin schema v1.0.0 and the
   qualified client versions. The shape is already implemented by the relevant
   standard clients, while adapters bound the unsupported surfaces.
2. **Distribution scope.** Recommendation: keep the first slate to its current
   Codex and Claude Code targets. Treat Cursor, Hermes, and OpenClaw as qualified
   only after an explicit marketplace/support decision; their mentions currently
   describe execution or scheduling, not a release promise.
3. **Shared skill resources.** Recommendation: make runtime-read resources
   skill-local projections from one authored source. Accept the present
   package-relative layout only if all supported clients prove it from installed
   packages and the release validator retains that proof.
4. **Claude agent aliases.** Recommendation: retain all six initially. They are
   small client-native entry points and removing them would change the public
   Claude interface. Any later simplification should be a separate product
   decision.
5. **Codex overlay.** Remove the independently authored `.codex-plugin`
   manifest before the first slate freezes, after the root interface projection
   passes package and source-evidence validation. Reintroduce a generated
   overlay only for a concrete native feature that current standard-format
   Codex cannot execute.
6. **Task Witness identity.** Root `plugin.json` is the canonical release
   identity; each adapter digest is retained as manager-specific evidence, not
   promoted to another version authority. F5 is internal version `0.1.0`, B1 is
   hidden bridge version `0.1.1`, and public TW4 is version `1.0.0`.
7. **Code-only package semantics.** Recommendation: continue calling Task Witness
   a code-only Agent Plugin package only when the phrase clearly refers to its
   standard package identity. Do not imply that its control-plane code is a
   portable Agent Plugins component.
