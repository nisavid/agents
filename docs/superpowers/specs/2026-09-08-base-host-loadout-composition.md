# Base and Host Loadout Composition

This contract defines how one exact portable Base Loadout and one dotfiles-owned Host Loadout produce a binding-free, host-scoped Resolved Loadout and a closed, desired-state Rig Manifest for `stlz-ivan-mbp` or `hatchery`.

It settles the initial two-layer composition model, target taxonomy, provider and secret handoff, deterministic validation, drift and promotion semantics, source ownership, and predecessor-source disposition for [agents issue 46](https://github.com/nisavid/agents/issues/46).

## Scope and ownership

Base and Host are the initial layers. Additional layers and behaviors may be added à la carte without waiting for Agentworks. Only the eventual fully flexible, customizable, and composable architecture is deferred to Agentworks.

| Artifact or contract | Normative owner |
|---|---|
| Initial bounded schemas, shared Rig Target Contracts, pure composer, canonical diagnostics, and contract vectors | `nisavid/agents` |
| Base Loadout instance and portable producer boundary | [agents issue 82](https://github.com/nisavid/agents/issues/82) |
| Host Intent and Binding instances | `nisavid/dotfiles` |
| Resolved Loadout | Derived output; never an independently edited source |
| Host-specific Rig Manifest instance | Derived output consumed and retained through dotfiles |
| Concrete projections, observations, fitting, migration, and host evidence | [dotfiles issue 68](https://github.com/nisavid/dotfiles/issues/68) |
| Provider release descriptor, whole-Kit completeness, and provider-side evidence rebinding | [provingkit issue 3](https://github.com/nisavid/provingkit/issues/3) |

The bounded v1 schemas and pure composer live in `nisavid/agents` without making Host instances or host materialization agents-owned. Later promotion of proven reusable contracts into Agentworks requires its own accepted promotion and migration decision. Agentworks is not an initial runtime or release dependency.

A change to the provider descriptor or another shared provider-side contract input invalidates the dependent consumer-seam review. This contract consumes that descriptor opaquely and does not define its fields.

This decision grants no implementation, publication, installation, credential access, fitting, release, migration, or host-mutation authority.

## Composition model

Base and Host use a versioned generic layer envelope. Each active layer supplies:

- stable identity and scope;
- schema version;
- normalized content digest;
- compatibility requirements; and
- its complete authored declaration or ordered typed operations.

The initial composer accepts exactly one Base and one Host. Host Intent must reference the exact Base digest. A different Base reference, a cycle, duplicate layer identity, or multiple active layers at one scope is invalid.

Additions must not collide after accepted composition. Suppressions and replacements identify the exact existing selection they change, whether inherited from Base or introduced earlier by Host Intent; ambiguous operands or silent identity substitution are invalid. The Resolved Loadout retains the originating layer and operation for every composition decision.

Authoring may use templates or fragments, but the composer consumes only complete normalized documents.

One normalized Host Loadout contains the generic envelope and two separately digested payloads.

### Intent

Host Intent:

- pins one exact Base Loadout digest;
- contains ordered additions, suppressions, and replacements;
- may introduce portable equipment and all dependency-closed portable data needed to resolve it;
- inherits omitted Base choices rather than copying them; and
- is the only Host payload that can change desired equipment.

A Host addition does not require promotion to Base merely because its equipment is portable. Dotfiles owns the explicit Host delta while Base retains authority over inherited choices.

Ordinary Host composition may narrow authority but may not grant credentials, weaken protected constraints, silently replace incompatible identities, or widen an inherited authority ceiling. An explicit Base Promotion is a separate Base-authoring operation.

### Binding

Host Binding contains:

- target host identity;
- platform, architecture, and version constraints;
- consumer-surface, named-profile, and configuration bindings;
- Rig Target membership and native reader mappings;
- provider-route adapters and managers;
- scope roots, paths, endpoints, and argument projections;
- capability declarations and evidence references;
- value-free secret-provider handles;
- manager declarations, locks, registrations, aliases, and selected writers; and
- eligible Host exceptions.

Binding references portable route and requirement identities instead of repeating distributions, releases, coverage, source tracking, or authority intent. It may realize or narrow resolved intent. It cannot select substitute equipment, packages, versions, or provider releases.

### Host identities

The initial Host Loadout identities are:

- `host/stlz-ivan-mbp`
- `host/hatchery`

They do not contain a principal suffix and require no machine/principal pairing. A user-scoped path, account selector, or `nisavid` binding may appear only where a concrete binding requires it; it does not identify the layer.

### Derived results

```text
Resolved Loadout = Base Loadout + Host Intent
Rig Manifest     = Resolved Loadout + matching Host Binding
```

The Resolved Loadout is:

- immutable and content-addressed;
- host-scoped but binding-free;
- secret-free;
- exact about selected equipment, identities, content digests, coverage, provider contracts, and composition decisions;
- explicit about the originating layer and operation for every decision; and
- not an independently authored source.

The Rig Manifest freezes the desired target-specific projection and Binding validation. It does not contain or claim installed state, current availability, runtime conformance, credentials, or permission to act.

An Intent change requires new dependent Resolved and Rig results. A Binding-only change leaves the Resolved Loadout unchanged and requires only a new Rig. Previously frozen outputs retain their immutable identities and remain descriptions of their original pinned inputs.

## Dependency invalidation

Invalidation follows exact dependency edges:

- a changed Base, Host Intent, selected distribution, content lock, source-evidence binding, Provider Route Contract, or composition contract requires a new Resolved Loadout and new dependent Rig Manifests;
- a changed Host Binding, Provider Route Binding, capability declaration, evidence dependency, Secret Binding, or exception requires only a new dependent Rig for that Host;
- a change to the shared provider release seam owned by [provingkit issue 3](https://github.com/nisavid/provingkit/issues/3) reopens this consumer seam for review; and
- a Runtime Observation differing from the Rig records dated runtime drift but rewrites no input and authorizes no fitting.

A newer external source, package, or Base revision does not stale an older exact object merely by existing. Staleness requires a changed or mismatched dependency within the claim being evaluated.

## Provider routes

Each provider route crosses the handoff through three distinct records.

### Provider Route Contract

The portable Provider Route Contract belongs to the selecting Intent and survives into the Resolved Loadout. It contains:

- route and consumer-surface identities;
- supplied equipment;
- immutable distribution, package, or Kit identity;
- component intent;
- required capabilities and accepted modes;
- source, version, or digest bindings; and
- ceilings on later authority.

### Provider Route Binding

The Host Binding owns:

- manager or adapter selection;
- native registration keys;
- scope roots and target paths;
- concrete host-local endpoints;
- host constraints;
- argument rendering;
- capability-evidence references; and
- secret bindings.

A rendered command or `argv` can encode both domains. The equipment identity, selected package/version or immutable digest, portable endpoint requirements, and authority ceilings remain Intent. The native target, wrapper, secret slots, and argument rendering remain Binding.

### Bound Provider Route

The Rig Manifest records the exact validated pairing of Contract and Binding. It contains no installed-state result or actuation authority.

## Coverage and capability matching

Composition uses a requirement/declaration/match model.

1. Base or Host Intent owns portable coverage, required capability and operation semantics, accepted modes, and authority ceilings.
2. Host Binding owns affirmative typed declarations for a particular surface, binding, operation, mode, scope, and applicable version range.
3. The Rig records the deterministic match or one permitted visible exception.
4. Runtime observations, Fitting Plans, Grants, attempts, and results remain later records.

A missing declaration means undeclared or unmet. It does not prove unsupported behavior.

Every affirmative capability declaration identifies:

- consumer surface and Rig Target;
- operation and mode;
- scope;
- applicable version or build range;
- evidence class; and
- immutable evidence reference.

Evidence retains its actual class and claim boundary:

- public vendor documentation may support a general documentary capability;
- pinned source or unexecuted tests may support only their version-bounded source or test-contract claim;
- installed help may support an exact-build interface claim;
- a dated Runtime Observation supports only the exact fact observed then.

Evidence is never promoted across those classes. Missing documentation does not prove absence, and documentary support does not become runtime qualification.

Authored paths, account selectors, secret handles, and manager bindings are desired declarations, not claims that a resource exists or works. Composition does not unlock credentials, launch applications, perform inference, or upgrade documentary evidence into production qualification.

## Secret handoff

Secret handling uses three value-free records:

1. **Credential Requirement** in Intent and the Resolved Loadout identifies the logical requirement and intended consumers.
2. **Secret Binding** in Host Binding maps it to an opaque host-local provider handle.
3. **Injection Projection** in the Rig identifies the native adapter input slot, such as an environment-variable name, without its value.

Composition validates the permitted mapping but never reads or tests a credential. Exact custody products, key delivery, and signing algorithms remain outside this contract.

## Identity and target taxonomy

Consumer surfaces, profiles, Rig Targets, and physical materializations are different typed identities. Every reference records its kind.

### Consumer Surface identities

- `openai.chatgpt.desktop`
- `anthropic.claude.code`
- `anthropic.claude.desktop`
- `cursor.desktop`
- `cursor.agent`

Codex CLI and IDE consumption are expressed by their route and capability declarations. The retired `openai.codex.cli` identity is not part of this contract.

### Profile identities

Named Codex profiles use the qualified identity:

```text
openai.codex.<profile>
```

For example, `openai.codex.review` identifies a selectable profile overlay, not a Rig Target or CLI surface. A concrete Codex invocation may select zero or one such profile, and only when that consumer declares the required capability. Shared base configuration is not fabricated as a `main` or `default` profile. ([Codex profiles](https://learn.chatgpt.com/docs/config-file/config-advanced#profiles))

### Rig Target Contracts

| Rig Target | Contract and primary documentary basis |
|---|---|
| `openai.codex` | Shared Codex base configuration for fields documented as common to the ChatGPT desktop app, Codex CLI, and IDE. ([configuration](https://learn.chatgpt.com/docs/config-file/config-basic), [MCP](https://learn.chatgpt.com/docs/extend/mcp), [models](https://learn.chatgpt.com/docs/models)) |
| `cursor` | Configuration slots documented as common to Cursor editor and Agent, including MCP, `.cursor/rules`, root `AGENTS.md`, and `workspaceOpen`. ([MCP](https://cursor.com/docs/cli/mcp#overview), [rules](https://cursor.com/docs/cli/using#rules), [`workspaceOpen`](https://cursor.com/docs/hooks#workspaceopen)) |
| `cursor.agent` | Agent-specific CLI configuration. ([CLI configuration](https://cursor.com/docs/cli/reference/configuration#file-location)) |
| `anthropic.claude.code` | Local Claude Code configuration shared by supported CLI, IDE, and local Desktop Code entrypoints. ([Desktop shared configuration](https://code.claude.com/docs/en/desktop#shared-configuration), [settings](https://code.claude.com/docs/en/settings)) |
| `anthropic.claude.desktop` | Separate Desktop JSON projection shared by Desktop Chat and local Desktop Code where documented. ([Desktop MCP behavior](https://code.claude.com/docs/en/desktop#mcp-servers-from-the-claude-desktop-chat-app)) |
| `agents.skills` | Common Agent Skills content target using evidence-qualified native readers or aliases. ([Agent Skills integration guidance](https://agentskills.io/client-implementation/adding-skills-support.md)) |
| `agents.plugins` | Common collection of exact Agent Plugin package identities and digests; every consumer still requires a client-specific loading or registration binding. ([Agent Plugins specification](https://agent-plugins.org/specification)) |

The same spelling may occur under different typed kinds without making the entities equivalent.

`openai.codex` is never CLI-exclusive. Its documented sharing does not imply that Chat or Work reads every Codex key or that every local plugin state is common. The shared public plugin catalog and each environment’s installation state remain distinct. ([OpenAI plugins](https://developers.openai.com/codex/plugins))

The `cursor` target is limited to documented common inputs. Root `CLAUDE.md` is an explicit Agent CLI input; the retained evidence does not establish it as a shared editor input. Cursor subagents are available in editor and CLI, but the retained documentation does not establish every listed definition path as one universal installed registry. ([CLI usage](https://cursor.com/docs/cli/using), [subagents](https://cursor.com/docs/subagents#custom-subagents))

Claude configuration overlaps rather than forming one hierarchy:

- local Desktop Code and local Claude Code share documented Code configuration;
- `claude_desktop_config.json` additionally feeds Desktop Chat and local Desktop Code, not the standalone CLI;
- claude.ai connectors can reach Code surfaces through different delivery paths without becoming one shared host store; ([claude.ai connectors](https://code.claude.com/docs/en/mcp#use-mcp-servers-from-claude-ai))
- Cowork and cloud account skills are distinct from local skill files; ([synced skills](https://code.claude.com/docs/en/skills#skills-in-cowork-and-cloud-sessions))
- MCPB bundles are separately installed Desktop extensions whose complete registry and Code-entrypoint sharing were not established. ([MCPB](https://claude.com/docs/connectors/building/mcpb))

A local Desktop Code route may therefore consume both Claude Rig Targets. Account Customize state and MCPB installation are not initial host targets.

Declaring any target or consumer surface makes it addressable. It does not prove support, installation, health, availability, or qualification on a host.

## Common Agent Skills and Agent Plugins

### Agent Skills

Agent Skills defines the `SKILL.md` package format but does not mandate a discovery directory. `.agents/skills/` is a documented cross-client interoperability convention. ([specification](https://agentskills.io/specification), [integration guidance](https://agentskills.io/client-implementation/adding-skills-support.md))

The user-scope Host Binding for `agents.skills` conventionally maps it to:

```text
~/.agents/skills
```

Each consuming surface carries its own evidence-qualified reader declaration. Codex and Cursor desktop have documented readers for the common root. The reviewed Cursor Agent material supports skill invocation but did not establish its complete root-discovery contract. ([Codex skill roots](https://learn.chatgpt.com/docs/build-skills.md), [Cursor skill roots](https://cursor.com/docs/skills), [Cursor Agent skill use](https://cursor.com/docs/cli/using))

Claude joins this content through native per-entry aliases:

```text
~/.claude/skills/<name> -> ~/.agents/skills/<name>
```

The common skill has one equipment identity and content digest. Each Claude alias is a separately digested Binding projection, not duplicated equipment. Claude local Code and local Desktop Code may consume those aliases where supported; Cowork does not read the local root. ([Claude skill roots and aliases](https://code.claude.com/docs/en/skills))

A discovery-root alias is distinct from a package-internal symlink. Package-internal link restrictions do not govern an ordinary native skill-entry alias.

### Agent Plugins

Agent Plugins defines a directory-rooted package with a root `plugin.json`. It does not prescribe a universal discovery root, installation route, registration mechanism, or enablement state. ([Agent Plugins specification](https://agent-plugins.org/specification))

The `agents.plugins` Binding conventionally materializes exact packages below:

```text
~/.agents/plugins
```

Every consumer requires an explicit, evidence-qualified client-specific loading or registration binding. A shared package format, marketplace, or filesystem location does not establish installation, enablement, or native discovery. Cursor and OpenAI document their own supported plugin routes rather than a universal reader for this common root. ([Cursor plugins](https://cursor.com/docs/plugins), [OpenAI plugins](https://developers.openai.com/codex/plugins))

Claude does not gain Agent Plugin support from a skill alias. Claude’s separate native skills-directory route requires `.claude-plugin/plugin.json` beneath a native skills directory and loads `<name>@skills-dir`; it does not adapt a standard root `plugin.json`. No such native bridge was established in the frozen predecessor source. ([Claude skills-directory plugins](https://code.claude.com/docs/en/plugins-reference#skills-directory-plugins))

## Catalogs, managers, and component records

Marketplaces, MCP configuration, hooks, instructions, agents, commands, manager state, enablement, and caches are cataloged as subordinate records around existing targets rather than receiving automatic top-level Target identities.

| Record | Meaning |
|---|---|
| Catalog Source and Catalog Entry | Discovery metadata and package location |
| Manager Binding | Exact manager implementation, scope, target, supported actions, write set, and authority ceiling |
| Manager Declaration | Derived or provenance input unless explicitly adopted later as the authored source for one bounded field |
| Manager Lock | Manager-owned provenance with its actual selector and hash meaning; never independently promoted into authored Loadout intent |
| Registration Projection | Marketplace entry, native config edit, symlink, copy, or equivalent binding |
| Installed/Enablement Observation | Dated factual state, not desired equipment |
| Runtime Capability Observation | Dated connected or discovered capability state |

A marketplace or registry entry does not prove that a bundle is installed or enabled. Installed cache state is not portable content identity. Claude, Cursor, and OpenAI each document distinct catalog, installation, and enablement mechanisms. ([Claude marketplaces](https://code.claude.com/docs/en/plugin-marketplaces), [Cursor plugins](https://cursor.com/docs/plugins), [OpenAI plugins](https://developers.openai.com/codex/plugins))

For MCP, the following records remain distinct where applicable:

1. optional registry or marketplace metadata;
2. the authored client endpoint or executable registration;
3. value-free secret slots; and
4. dated runtime discovery of connected capabilities.

A directly configured local, private, or unregistered server requires no invented registry record. The MCP Registry holds metadata pointing to packages or remote endpoints rather than hosting those packages, and registry lookup remains distinct from runtime capability discovery. ([MCP Registry](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/e76e9c572c6f2bfcb730357101acc90f2f802e02/docs/registry/about.mdx), [versioned runtime architecture](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/e76e9c572c6f2bfcb730357101acc90f2f802e02/docs/docs/2026-07-28/learn/architecture.mdx))

Hooks, instructions, agents, and commands remain typed components of their native targets or plugins. Similar filenames do not imply a cross-client standard; each reader, scope, precedence, trust rule, and event contract is explicit. ([Claude hooks](https://code.claude.com/docs/en/hooks), [Cursor hooks](https://cursor.com/docs/hooks), [Codex hooks](https://learn.chatgpt.com/docs/hooks))

### Manager authority and provenance

A selected Manager Binding may be the sole writer for its declared write set. Dotfiles or another manager cannot independently remain the final writer of the same projected field.

Manager declarations and locks remain derived or provenance inputs unless an explicit later decision makes a manager-owned declaration the authored source for one bounded field. That exception applies to the declaration, not the lock. Reference-only or externally managed records may be cataloged without granting mutation authority.

A manager checksum supports only its documented fileset and acquisition flow. It does not override the selected Loadout content identity or provider release binding.

The reviewed `vercel-labs/skills` source at commit `1682051d48c34f5eb135e6475c1a965dce05e820` distinguishes:

- project `skills-lock.json`;
- global `$XDG_STATE_HOME/skills/.skill-lock.json`, falling back to `~/.agents/.skill-lock.json`;
- mutable source selectors from content or tree hashes; and
- acquired or snapshot hashes from installed-target readback.

Its `check`, `update`, and `upgrade` dispatch through the same update path; `check` is not assumed read-only. Copy, symlink, and copy-fallback materializations retain their actual provenance. ([pinned source](https://github.com/vercel-labs/skills/tree/1682051d48c34f5eb135e6475c1a965dce05e820), [project lock](https://raw.githubusercontent.com/vercel-labs/skills/1682051d48c34f5eb135e6475c1a965dce05e820/src/local-lock.ts), [global lock](https://raw.githubusercontent.com/vercel-labs/skills/1682051d48c34f5eb135e6475c1a965dce05e820/src/skill-lock.ts), [command dispatch](https://raw.githubusercontent.com/vercel-labs/skills/1682051d48c34f5eb135e6475c1a965dce05e820/src/cli.ts))

The reviewed `plugins.sh` 0.3.1 distribution distinguishes:

- `~/.agents/plugins.json` as the manager declaration;
- `~/.agents/plugins.lock` as entries containing indexed plugin source-repository commit SHAs and related provenance; and
- `~/.agents/plugins/marketplace.json` as generated marketplace registration.

The manager implementation was reviewed from the content-pinned npm distribution, not a verified source-repository commit. Each plugin lock SHA pins the indexed source revision recorded by that manager; it does not prove an independently reviewed whole-package release or installed-byte verification. ([npm package](https://www.npmjs.com/package/plugins.sh), [0.3.1 distribution](https://registry.npmjs.org/plugins.sh/-/plugins.sh-0.3.1.tgz))

No listed manager, catalog, hook, or record grants installation, execution, credential, or protected-operation authority.

## Exceptions

A Host exception may only narrow or defer an exception-eligible portable requirement.

It records:

- the exact requirement and Provider Route;
- the unmatched or omitted condition;
- rationale and evidence class; and
- a concrete revalidation trigger.

An exception cannot:

- claim that an unmet requirement is satisfied;
- substitute another distribution;
- broaden authority;
- grant a credential;
- weaken a protected constraint; or
- bless contradictory writers.

An allowed exception produces a Rig that visibly retains the unmatched disposition. It cannot be presented as fully matched.

An unapproved exception or mismatch against a protected requirement prevents a valid Rig.

An eligible omission or deferral may remove an incompatible required projection before the final write set is formed. The omission remains visible and every remaining projected field must still be unambiguous.

## Rig closure

A valid Rig is a closed, value-free desired-state artifact.

Every value the composer must interpret appears directly or through an obtainable, digest-verified immutable reference. Protected evidence may remain external when its accepted evidence contract supplies a sufficient immutable binding.

If a required reference is unavailable, unresolved, or fails verification, composition returns an incomplete or invalid result and issues no valid Rig. It never substitutes a mutable catalog entry, manager state, cache result, discovery order, or inferred value.

A Rig includes:

- schema and contract versions;
- host identity and platform constraints;
- exact Base, Host Intent, Host Binding, and Resolved Loadout digests;
- selected consumer surfaces and Rig Targets;
- target projections, materialization bindings, and their digests;
- Bound Provider Routes and the opaque provider descriptor;
- capability matches, evidence classes, immutable references, and visible unmatched dispositions;
- opaque secret handles and injection slots;
- manager bindings, selected writers, declaration and lock provenance, registrations, and aliases;
- accepted exceptions;
- explicit precedence and composition decisions;
- dependency edges needed for scoped invalidation; and
- canonical diagnostics needed to reproduce the result.

Large packages, Kit content, and protected raw evidence need not be embedded. Their immutable identities, digests, and verification results close the reference under their own contracts.

Mutable marketplace listings, caches, installed state, runtime-observation bodies, Fitting Plans, Grants, checkpoints, execution results, and receipts remain outside the Rig.

## Composition and writer conflicts

Composition proceeds in this order:

1. validate the exact Base and Host document identities and schemas;
2. apply Base inheritance;
3. apply ordered Host additions, suppressions, and replacements;
4. apply accepted target-specific merge, override, and precedence semantics;
5. form the candidate final projections;
6. select one writer for every resulting field; and
7. reject any unresolved conflicting desired writes.

One declared materialization adapter owns each final output. It may accept multiple contributions only when:

- their scopes are disjoint; or
- their ordering, merge, replacement, or override semantics are explicit in the target contract.

An explicit alias or reference to canonical content is not another content writer. Runtime product precedence may be recorded but cannot silently settle a conflict left unresolved by the authored model.

Unequal overlapping contributions fail only when no accepted relationship resolves them. Identical claims may coalesce only when they share the same canonical identity and an explicit alias, inheritance, or deduplication relationship. Otherwise they remain a provenance conflict.

A potential source footprint reaching a path is not itself proof of a final collision. Accepted composition runs first. The resulting projection must still have exactly one selected writer per field and may never rely on whichever tool runs last.

An unresolved collision may produce canonical diagnostics and candidate projections, but never a valid Rig.

## Closed projection rules and later path discovery

A Rig may freeze a closed, evidence-qualified projection rule when a target’s desired semantics are rule-based. The rule includes:

- exact target configuration surface;
- selected writer;
- bounded root and matching semantics;
- exact equipment identities and desired states;
- applicable reader and manager bindings; and
- capability and evidence bindings.

For example, Codex path-specific disabled-skill rows may use a closed selector over an exact plugin-cache root and selected skill identities. The Rig does not freeze a mutable glob result or cache contents.

A later Runtime Observation may enumerate concrete installed paths. A Fitting Plan may bind only paths matching the frozen selector and observation. It cannot:

- choose new equipment;
- substitute a package, version, digest, or provider;
- widen the root or write scope;
- promote observed state into Intent;
- repair an incomplete Binding silently; or
- make an unavailable protected check appear complete.

If composition requires a concrete value and that value is unavailable or unverifiable, no valid Rig is issued for that required projection.

## Drift reconciliation

Runtime drift is represented as independently addressable typed differences against one exact Rig.

- **Capture** selects a representable difference for an authored Host Loadout revision. It never promotes a change into Base automatically.
- **Revert** selects a fitting effect that restores the corresponding runtime materialization to the frozen Rig’s desired state.
- **Ignore** durably acknowledges that exact drift fingerprint without changing desired state or creating an exception. A changed value, Rig, or evidence dependency reopens it.
- **Skip** leaves the item undecided for the current reconciliation pass.

An observation alone changes no authored state.

For common content and aliases:

- observed canonical bytes differing from the pinned content identity make that runtime materialization nonconformant and stale affected runtime-conformance evidence;
- a missing or incorrect alias makes that alias materialization nonconformant and affects only its dependent runtime claims;
- neither condition rewrites Intent, Binding, the Resolved Loadout, or the Rig; and
- only a subsequent authored revision invokes normal dependency invalidation.

Drift is surfaced during explicit reconciliation or when it affects a requested action, not repeatedly for unchanged acknowledged state.

A workflow may apply an authorized Capture or Revert under existing task, user, or trusted-policy authority. Additional authority is required only when the intended effect exceeds that scope; this contract creates no redundant approval.

## Host-to-Base promotion

Base Promotion is separate from runtime Capture.

It selects a dependency-closed semantic effect from authored Host Intent against that Intent’s exact pinned Base. Eligible input may include:

- additions;
- suppressions;
- replacements; and
- their required portable definitions.

It excludes:

- Host identity;
- Binding fields;
- target paths and endpoints;
- exceptions;
- capability evidence;
- secret handles;
- Runtime Observations; and
- fitting or execution state.

Promotion deliberately authors Base. Ordinary Host composition’s no-widen rule does not prohibit that intended Base edit. Any Base-constraint change must be explicit in the same authorized proposal and cannot be relaxed silently.

Creating a new Base revision does not repin any Host. A promotion proposal may include an explicit named adoption set under existing sufficient workflow authority. Hosts outside that set remain pinned to their prior Base.

When the originating Host adopts the promoted Base, it receives a semantic rebase limited to the selected effect and Host operations that actually interact with it. The rebase:

- preserves prior effective intent unless another intended behavior change is explicit;
- removes the former Host expression only when equivalence is established;
- does not minimize or reorder unrelated Host operations; and
- stops on unresolved equivalence rather than guessing.

If the originating Host does not adopt the new Base, its Base pin and Host Intent remain unchanged.

Promotion acceptance always binds:

- the prior Base and originating Host Intent digests;
- the selected dependency-closed effect;
- the new Base digest; and
- the explicit adoption set.

When the originating Host is in that adoption set, promotion acceptance additionally binds:

- the rebased Host Intent digest;
- the old and new Resolved Loadouts; and
- a comparison proving equivalent effective behavior except for explicitly enumerated intended changes.

The adopting Host’s Base and Host document identities, Base pin, and derivation records change. Unchanged equipment retains its own identity and content digest.

When the originating Host is not in the adoption set, no rebased Host Intent or new Resolved Loadout is required for that Host; acceptance records its unchanged Base pin and Host Intent.

Previously frozen outputs remain immutable descriptions of their original inputs.

## Deterministic two-host cohort

Each acceptance cohort explicitly names:

- `host/stlz-ivan-mbp`
- `host/hatchery`

The cohort pins one exact Base digest and one set of contract and schema versions.

Both Host Intents begin from that Base. An inherited Base selection left unchanged on both hosts retains the same equipment identity and content digest.

Each Host may still add, suppress, or replace equipment. Consequently, each has its own:

- Intent and Binding digests;
- host-scoped Resolved Loadout;
- Rig Manifest;
- paths and adapters;
- manager bindings;
- capability evidence;
- secret handles; and
- eligible exceptions.

If a Host replacement selects a different equipment or release identity, acceptance records a distinct host result and does not claim that both hosts qualified one common release.

Where both Hosts retain the same Provider Route, its portable contract and provider descriptor match. Host Bindings may differ. An explicit replacement may select a different route.

This cohort rule does not require global lockstep adoption. A Host outside a named cohort may remain pinned to another exact Base, and a later cohort may qualify a newer revision.

Two-host acceptance proves deterministic composition and the stated evidence-qualified matches. It does not prove installed state, availability, or runtime conformance.

## Deterministic acceptance matrix

The contract requires one representative frozen cohort, followed later by the same checks against the actual adoption documents.

The representative cohort uses one Base candidate plus normalized documents for both initial Hosts. It covers supported semantic branches without selecting the old catalog’s portfolio or forcing a Cartesian product across inapplicable targets.

### Canonical document identity

Loadout, Resolved, and Rig documents use:

- normalized UTF-8 JSON;
- sorted object keys;
- no insignificant whitespace;
- lowercase SHA-256 document digests; and
- preserved array order wherever order carries semantics.

Normalization applies only to syntax the declared parser defines as insignificant.

Equipment and package bytes, Git identities, supplied source or evidence digests, and manager-lock hashes retain their own exact identities and algorithms. They are not normalized into the document’s SHA-256 scheme.

Formatting-only source changes preserve identity only when parsing produces the same normalized document. Changing selected content or an applicable raw evidence dependency may change its identity and stale dependent acceptance.

A newer Base existing elsewhere does not stale a cohort that still pins an older exact Base. Staleness means mismatch within the cohort’s exact inputs or applicable evidence dependencies.

### Required passing cases

1. Compose both Host Intents from the same exact Base, then bind each Resolved Loadout only with its matching Host Binding.
2. Recompose identical inputs into byte-identical Resolved Loadouts, Rig Manifests, diagnostics, and document digests.
3. Preserve unchanged inherited equipment identity and content while retaining visible Host additions, suppressions, and replacements.
4. Exercise each supported record family at least once:
   - common and vendor-specific Rig Targets;
   - an `agents.skills` reader;
   - a Claude per-entry alias;
   - an `agents.plugins` registration;
   - a Bound Provider Route;
   - a value-free secret projection;
   - manager provenance; and
   - an eligible visible omission or deferral.
5. Prove dependency scoping:
   - Binding-only revision leaves Resolved unchanged and produces a new Rig;
   - Intent revision produces new derived Resolved and Rig results;
   - unchanged historical outputs retain their identities; and
   - ignored formatting preserves identity only under the declared normalization rule.
6. Permit host-specific evidence, bindings, and managers without making a false common-release claim.

### Required rejection cases

Composition rejects:

- an identity or digest that does not match the exact claimed cohort input or evidence policy;
- cross-host Binding substitution or host-identity mismatch;
- an unavailable, unresolved, or unverifiable required reference;
- ambiguous addition, replacement, Provider Route binding, or common-release claim;
- missing, version-inapplicable, or out-of-scope affirmative capability evidence;
- a closed-schema violation that introduces a literal credential value or value-bearing secret material into a prohibited field;
- an unapproved or protected mismatch;
- contradictory writers remaining after accepted composition;
- an exception that tries to bless a collision or claim satisfaction; and
- an alias or registration conflicting with its canonical target or selected writer.

A prohibited-secret fixture exercises defined value-bearing fields and explicit canaries. It does not promise a universal secret-string recognizer or inspect live credentials.

Every failure returns a canonical diagnostic result and no valid Rig. If a required input is missing, the result identifies downstream checks as unassessed rather than claiming that they ran.

A target declared applicable to both Hosts must pass on both fixtures. A host-specific target is tested only where applicable.

The actual Base and Host documents produced through agents issue 82 and dotfiles rerun the same matrix with their exact bytes. That later pass establishes instance-level composition only.

## Source partition

The frozen predecessor source at `nisavid/dotfiles@42d8f2595e362b036161ff0030297bcb54fa7fcb` combines responsibilities now assigned to different records.

Every field converts according to the decision it expresses, not merely whether its data is portable.

| Meaning after conversion | Destination |
|---|---|
| Choice inherited from Base, including its equipment identity, selected membership, version/source intent, portable requirements, Provider Route Contract, authority ceilings, and immutable bindings | Base Intent in `nisavid/agents` |
| Portable equipment introduced or replaced by a Host, including dependency-closed identity, digest, source/version facts, route requirements, and constraints | Host Intent in dotfiles |
| Suppression | The Intent layer authoring that suppression |
| Concrete reader, manager, adapter, scope, root, path, endpoint, secret binding, alias, registration, capability evidence, selected writer, or exception | Host Binding in dotfiles |
| Base plus Host Intent | Derived Resolved Loadout |
| Resolved Loadout plus Host Binding | Derived Rig Manifest |
| Runtime observation, losing physical surface, removal order, capture, restoration, compensation, execution, or receipt | Later dotfiles observation, fitting, or migration record |

Old operation and restore records split by meaning:

- portable required behavior and authority ceilings belong to the selecting Intent;
- evidenced host support belongs to Binding;
- actuation authority and compensation remain later.

A predecessor retirement converts only after identifying its operand:

- equipment intent may become an addition, suppression, or replacement in the appropriate Intent;
- local route, registration, projection, or alias changes may become Binding revisions;
- removal of a duplicate Claude alias while retaining canonical equipment is a Binding transition, not equipment suppression; and
- captured state, losing-surface identity, removal order, restore, and compensation never become Intent.

The predecessor catalog’s equipment records are conversion evidence, not automatic Base membership. Actual Base membership remains with agents issue 82.

## Predecessor formats and implementation reuse

New versioned document kinds replace the mixed responsibility of `catalog/v1` and `lock/v1`; those schemas are not redefined in place.

The selected frozen commit is immutable evidence for this decision, not a freeze on ongoing source work. A future cutover must capture and revalidate then-current source, account for intervening changes, and update its field-level conversion ledger.

The predecessor catalog, lock, schemas, and implementation remain conversion inputs until every relevant field has a destination or explicit retirement and the new two-host contract vectors establish the intended projection. They then leave active authority but may remain immutable historical evidence.

Only reviewed extraction or adaptation may reuse predecessor logic:

- canonical JSON and immutable-value handling;
- validation patterns;
- fact-only source-resolution boundaries;
- Source Manifest materialization;
- deterministic diagnostics; and
- applicable negative fixtures.

The mixed `model.py`, `resolver.py`, `authoring.py`, and `updater.py` files do not move wholesale. The predecessor resolver combines desired state with Runtime Inventory and mutation planning and is not the pure composer. The frozen public CLI exposes empty or unavailable runtime/source-resolution seams and an unavailable `apply` dispatch; this is source evidence, not installed-host behavior. ([status seam](https://github.com/nisavid/dotfiles/blob/42d8f2595e362b036161ff0030297bcb54fa7fcb/home/private_dot_local/lib/agent-equipment/agent_equipment/__init__.py#L115-L130), [source-resolution seam](https://github.com/nisavid/dotfiles/blob/42d8f2595e362b036161ff0030297bcb54fa7fcb/home/private_dot_local/lib/agent-equipment/agent_equipment/__init__.py#L318-L328), [`apply` dispatch](https://github.com/nisavid/dotfiles/blob/42d8f2595e362b036161ff0030297bcb54fa7fcb/home/private_dot_local/lib/agent-equipment/agent_equipment/__init__.py#L545-L549))

For each new active artifact there is one editable canonical source. Documentation snapshots may remain explicitly historical. Installed schemas and configuration are derived projections verified against canonical bytes and digests rather than independent editable authorities.

## Concrete writer disposition

The following mapping applies the source partition to the frozen public writer inventory. It authorizes no current writer transfer or retirement.

| Source family | Disposition |
|---|---|
| `home/dot_config/agent-equipment/catalog-v1.json`, `lock-v1.json`, schemas, and `home/private_dot_local/lib/agent-equipment/agent_equipment/` | Frozen conversion input. Portable and Host choices split into their owning Intents; concrete bindings split into Host Binding; Runtime Inventory, planning, and actuation remain later dotfiles concerns. |
| `home/modify_private_dot_claude.json.tmpl` and `home/dot_claude/modify_private_settings.json.tmpl` | Intent retains selected equipment, package/version/digest, route requirements, and desired component state. Binding owns native keys, target files, wrapper and argument slots, secret handles, and projection rules. Existing deletions and disabled flags require operand classification and do not automatically become equipment suppression. These templates remain current narrow writers until their replacement gates pass. ([MCP modifier](https://github.com/nisavid/dotfiles/blob/42d8f2595e362b036161ff0030297bcb54fa7fcb/home/modify_private_dot_claude.json.tmpl#L4-L33), [plugin modifier](https://github.com/nisavid/dotfiles/blob/42d8f2595e362b036161ff0030297bcb54fa7fcb/home/dot_claude/modify_private_settings.json.tmpl#L4-L10)) |
| `home/dot_codex/modify_private_config.toml.tmpl` and `home/dot_codex/modify_hooks.json.tmpl` | MCP and hook equipment follow the Intent/Binding split. Theme and editor preferences stay outside this transfer. Writable roots and project trust retain existing dotfiles/native ownership; when they constrain a selected route, scope, capability, or authority ceiling, Binding and matching retain that intersection visibly. A trust value is not an actuation grant. Current modifiers remain narrow writers. ([config modifier](https://github.com/nisavid/dotfiles/blob/42d8f2595e362b036161ff0030297bcb54fa7fcb/home/dot_codex/modify_private_config.toml.tmpl#L49-L203), [hook modifier](https://github.com/nisavid/dotfiles/blob/42d8f2595e362b036161ff0030297bcb54fa7fcb/home/dot_codex/modify_hooks.json.tmpl#L98-L131)) |
| `home/dot_config/modify_private_mcp-config.json.tmpl` | Existing concrete writer and authored bytes remain preserved. Its subordinate catalog record remains reader-unqualified until evidence supports an adopted reader binding. That status does not demote, disable, or authorize deletion of the writer. Its portable Firecrawl requirements convert to an owning Intent only if selected; the physical projection converts to Binding. ([modifier](https://github.com/nisavid/dotfiles/blob/42d8f2595e362b036161ff0030297bcb54fa7fcb/home/dot_config/modify_private_mcp-config.json.tmpl#L4-L20)) |
| `home/dot_agents/skills/`, `home/dot_claude/skills/symlink_*`, `home/run_after_sync-global-agent-skills-to-claude.zsh`, and `home/.chezmoiremove` | Skill selection/content belongs to the selecting Intent; Claude aliases belong to Binding. Explicit alias declarations and the blanket hook are predecessor writers with potentially overlapping footprints. Accepted composition and precedence run first; the resulting alias has one selected writer. `.chezmoiremove` remains retirement/fitting input, not Intent solely because it names paths. ([hook](https://github.com/nisavid/dotfiles/blob/42d8f2595e362b036161ff0030297bcb54fa7fcb/home/run_after_sync-global-agent-skills-to-claude.zsh#L8-L49), [alias declarations](https://github.com/nisavid/dotfiles/tree/42d8f2595e362b036161ff0030297bcb54fa7fcb/home/dot_claude/skills), [removals](https://github.com/nisavid/dotfiles/blob/42d8f2595e362b036161ff0030297bcb54fa7fcb/home/.chezmoiremove#L1-L10)) |
| `home/dot_agents/skills/symlink_hindsight-memory-*.tmpl` and Hindsight destination declarations | Common-root links are Binding projections to externally managed content. Membership remains with the selecting Intent. Destination documents are manager/binding references, not proof of runtime writing, reading, or availability. Encrypted path values remain unqualified. ([skill link](https://github.com/nisavid/dotfiles/blob/42d8f2595e362b036161ff0030297bcb54fa7fcb/home/dot_agents/skills/symlink_hindsight-memory-import.tmpl#L1-L6), [Cursor destination](https://github.com/nisavid/dotfiles/blob/42d8f2595e362b036161ff0030297bcb54fa7fcb/home/dot_config/private_hindsight-control-plane/private_harnesses/private_cursor-destination.json.tmpl#L7-L12)) |
| `home/run_onchange_after_restore-private-skills.sh.tmpl` and `scripts/private-skill-transaction*` | Public source establishes a writer footprint for canonical `agents.skills` entries and Claude aliases plus separate transaction/recovery behavior. Encrypted inputs do not reveal membership or determine Base versus Host ownership. After appropriate instance analysis, membership remains with agents issue 82 or the owning Host Intent; physical projections remain Binding. Potential overlaps undergo accepted composition and precedence before the final one-writer check. Unknown names prove neither conflict nor disjointness. The transaction lock is runtime coordination, not a package lock or Loadout authority. No whole-helper safety or reuse claim is made. ([restore entrypoint](https://github.com/nisavid/dotfiles/blob/42d8f2595e362b036161ff0030297bcb54fa7fcb/home/run_onchange_after_restore-private-skills.sh.tmpl#L1-L10), [declared targets](https://github.com/nisavid/dotfiles/blob/42d8f2595e362b036161ff0030297bcb54fa7fcb/scripts/private-skill-transaction.d/restore.bash#L176-L193)) |
| Native and third-party manager declarations, locks, caches, and receipts | Retain each declaration, lock, cache, and receipt with its actual manager, scope, selector, hash meaning, and provenance. Manager declarations and locks remain derived or provenance inputs unless an explicit later decision makes a manager-owned declaration the authored source for a bounded field. That decision cannot make the lock editable Loadout intent. A transaction lock is not a package lock. Cache observations never select equipment or versions. |

A public chezmoi filename attribute does not determine layer ownership or confidentiality. Private content does not force an entire document into Host Intent. Unknown encrypted or legacy inputs may already contain authored choices; the public audit cannot invent or qualify new Loadout entries from them and does not erase them.

Missing source-specific reader evidence does not override an accepted documentary reader contract. Conversely, an authored target or Binding never proves runtime consumption or conformance.

### Writer replacement constraints

At future cutover:

1. revalidate then-current source and applicable private instance data;
2. classify each decision as Base Intent, Host Intent, Binding, derived output, or later runtime/fitting state;
3. apply accepted inheritance, replacement, merge, and precedence;
4. select one final writer for every field or surface;
5. preserve unrelated keys and unmanaged state; and
6. stop when a required value, reader, capability, or final writer remains unresolved.

Existing writers remain until their documented narrow replacement gates pass. In particular:

- the blanket Claude skill hook remains until a catalog-driven replacement passes fresh-home, no-op, and rollback requirements;
- per-entry Claude declarations remain until each alias has an explicit selected replacement and dual ownership is removed in one cutover;
- Claude plugin and MCP branches remain until their narrow adapters pass applicable projection, compensation, and secret-value checks;
- Codex modifiers retain unrelated preferences and authority constraints while only accepted equipment branches transfer;
- the legacy shared MCP writer remains preserved without an inferred consumer; and
- no native cache, database, credential store, or manager-owned runtime state becomes chezmoi-authored merely through this conversion.

Existing workflow authority is sufficient when it already covers the future source changes. This contract adds no redundant approval, but it grants no such mutation authority itself.

## Evidence and input record

This decision uses the following bounded public inputs:

### Controlling ownership and provider boundaries

- [agents issue 46](https://github.com/nisavid/agents/issues/46)
- [cross-repository coordinator, agents issue 41](https://github.com/nisavid/agents/issues/41)
- [Base Loadout owner, agents issue 82](https://github.com/nisavid/agents/issues/82)
- [dotfiles realization owner, dotfiles issue 68](https://github.com/nisavid/dotfiles/issues/68)
- [Agentworks ownership boundary, agent-armory issue 216](https://github.com/nisavid/agent-armory/issues/216)
- [Provingkit release-contract owner, provingkit issue 3](https://github.com/nisavid/provingkit/issues/3)

### Accepted production-evidence boundaries

- [Provingkit production-evidence Q1–Q6](https://github.com/nisavid/provingkit/issues/8#issuecomment-5565178275)
- [Provingkit production-evidence Q7–Q10](https://github.com/nisavid/provingkit/issues/8#issuecomment-5567698314)
- [Provingkit production-evidence Q11](https://github.com/nisavid/provingkit/issues/8#issuecomment-5568191158)

Those decisions remain external inputs. This composition contract does not add mandatory services or trust domains, select a signing algorithm or custody provider, or claim production readiness.

### Host and release-route research

- [two-host public inventory](https://github.com/nisavid/agents/blob/3e30fec6f0379b15ded9ba71fbd83114872390a7/docs/superpowers/research/2026-09-07-live-harness-inventory.md)
- [harness release and update-route research](https://github.com/nisavid/provingkit/blob/8a7abac196be1fe6d89315f2937707048ee93a46/docs/superpowers/research/2026-09-07-harness-release-update-routes.md)

### Direct vendor and standard documentation

- [OpenAI configuration](https://learn.chatgpt.com/docs/config-file/config-basic)
- [OpenAI MCP](https://learn.chatgpt.com/docs/extend/mcp)
- [OpenAI profiles](https://learn.chatgpt.com/docs/config-file/config-advanced#profiles)
- [OpenAI plugins](https://developers.openai.com/codex/plugins)
- [Cursor Agent MCP](https://cursor.com/docs/cli/mcp#overview)
- [Cursor Agent rules](https://cursor.com/docs/cli/using#rules)
- [Cursor Agent configuration](https://cursor.com/docs/cli/reference/configuration#file-location)
- [Cursor hooks](https://cursor.com/docs/hooks)
- [Cursor skills](https://cursor.com/docs/skills)
- [Cursor plugins](https://cursor.com/docs/plugins)
- [Claude Desktop shared configuration](https://code.claude.com/docs/en/desktop#shared-configuration)
- [Claude Desktop MCP behavior](https://code.claude.com/docs/en/desktop#mcp-servers-from-the-claude-desktop-chat-app)
- [Claude Code MCP and account connectors](https://code.claude.com/docs/en/mcp)
- [Claude skills and symlink behavior](https://code.claude.com/docs/en/skills)
- [Claude plugins](https://code.claude.com/docs/en/plugins-reference)
- [Claude hooks](https://code.claude.com/docs/en/hooks)
- [Agent Skills specification](https://agentskills.io/specification)
- [Agent Skills integration guidance](https://agentskills.io/client-implementation/adding-skills-support.md)
- [Agent Plugins specification](https://agent-plugins.org/specification)
- [MCP Registry](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/e76e9c572c6f2bfcb730357101acc90f2f802e02/docs/registry/about.mdx)
- [versioned MCP runtime architecture](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/e76e9c572c6f2bfcb730357101acc90f2f802e02/docs/docs/2026-07-28/learn/architecture.mdx)

### Frozen source evidence

- [frozen dotfiles source](https://github.com/nisavid/dotfiles/tree/42d8f2595e362b036161ff0030297bcb54fa7fcb)
- [predecessor architecture](https://github.com/nisavid/dotfiles/blob/42d8f2595e362b036161ff0030297bcb54fa7fcb/docs/agent-equipment/ARCHITECTURE.md)
- [predecessor acceptance contract](https://github.com/nisavid/dotfiles/blob/42d8f2595e362b036161ff0030297bcb54fa7fcb/docs/agent-equipment/ACCEPTANCE.md)
- [predecessor implementation handoff](https://github.com/nisavid/dotfiles/blob/42d8f2595e362b036161ff0030297bcb54fa7fcb/docs/agent-equipment/IMPLEMENTATION_HANDOFF.md)
- [`vercel-labs/skills` at `1682051d48c34f5eb135e6475c1a965dce05e820`](https://github.com/vercel-labs/skills/tree/1682051d48c34f5eb135e6475c1a965dce05e820)
- [`plugins.sh` 0.3.1 distribution](https://registry.npmjs.org/plugins.sh/-/plugins.sh-0.3.1.tgz)

Vendor documentation and public source claims are bounded to the captures collected on 2026-09-07 and 2026-09-08. They are not refreshed current-product guarantees.

The source analysis inspected and independently verified the retained public source bytes against dotfiles commit `42d8f2595e362b036161ff0030297bcb54fa7fcb`. It did not execute source or tests, render templates, decrypt inputs, inspect credentials, or inspect either host.

## Verification status

Completed for this decision record:

- all confirmed operator decisions and final source/writer dispositions were consolidated;
- the decision prose received a final fidelity review;
- retained public source bytes and Git blobs were checked against the frozen dotfiles commit; and
- source claims were separated from unexecuted test assertions and runtime claims.

Not performed:

- schema or composer implementation;
- deterministic contract-vector execution;
- two-host fixture execution;
- current vendor-document refresh;
- private input inspection;
- runtime adapter or source-resolver qualification;
- installation, fitting, migration, or rollback;
- credential or secret-provider access;
- live host inspection;
- Provingkit release qualification; or
- production-readiness validation.

The documented acceptance matrix is a requirement for later implementation and adoption evidence, not a claim that those checks currently pass.