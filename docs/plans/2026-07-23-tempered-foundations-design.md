# Tempered Foundations design

Status: approved for implementation planning

Owner: Ivan D Vasin

Repository: `nisavid/agents`

Plugin slug: `tempered-foundations`

## Purpose

Tempered Foundations finds, furnishes, and proves a software project.

It turns a new project, an extraction from another repository, or an existing
repository into an implementation-ready state. Readiness means more than files
being present: applicable local and remote controls pass, artifacts are proven,
initial findings are dispositioned, and the result is bound to independently
verifiable evidence.

The plugin captures the complete foundation that a project should receive
before feature implementation begins. The reference case is CQMgr: repository
creation, issue migration, skill and design tooling, Serena, human and agent
documentation, Cocogitto, GitHub protections, security and quality workflows,
initial alert cleanup, and the Python quality and artifact baseline that was
added in a later foundation PR. Relevant public evidence includes
[CQMgr initialization](https://github.com/nisavid/cqmgr/pull/17),
[security gates](https://github.com/nisavid/cqmgr/pull/18), and the
[Python quality baseline](https://github.com/nisavid/cqmgr/pull/45).

## Name

The display name is **Tempered Foundations** and the normalized plugin slug is
`tempered-foundations`.

The name states the technical outcome directly: project foundations that have
survived checks and operational proof. Its secondary meaning comes from the
Believers of the Source, whose Great Foundry makes tempering and testing part
of transformation, and from Numenera's Wrights, who build durable
infrastructure from plans, assessed components, and disciplined work. The name
uses the themes without adopting a franchise proper noun.

Public skill names remain literal and routable. Internal terminology may use
metallurgical words such as temper, assay, quench, or proof only when the first
meaning is technically exact. Theme never replaces a contract or imports a
franchise proper noun.

## Product boundary

Tempered Foundations is one plugin in `nisavid/agents`, not a standalone
repository and not a family of nested lifecycle plugins.

It exposes exactly two public skills:

- `auditing-project-readiness` performs target-read-only assessment.
- `initializing-projects` creates or reconciles a project through protected
  integration and final certification.

The skills are the supported v1 interface. Deterministic scripts remain plugin
operations; v1 does not reserve a global `temper` command.

### Supported project origins

- `new`: create the local project, Git repository, selected GitHub repository,
  default branch, protected integration path, and foundation.
- `extract`: found a project from material owned by another repository while
  preserving provenance and migrating declared tracker state.
- `retrofit`: assess and reconcile an existing repository without assuming
  that every difference is drift.

### Supported destinations

- `local`: a local Git repository with a locally proven foundation.
- `github`: the local profile plus GitHub controls and remote evidence.

GitLab, Forgejo, Bitbucket, and other forges are explicit `unsupported`
capabilities in v1. They are adapter seams, not silently approximated by the
GitHub provider.

### Supported project shapes

V1 supports focused repositories containing one application or package.

The certified language and interface matrix is:

| Interface | Python | TypeScript | Go | Rust |
| --- | --- | --- | --- | --- |
| CLI | supported | supported | supported | supported |
| TUI | supported | unsupported | supported | supported |
| Web UI | unsupported | supported | unsupported | unsupported |

Unsupported combinations remain visible in the capability model and may gain
providers later. V1 does not support desktop applications, mobile
applications, multi-product suites, or federated suites.

### Non-goals

Tempered Foundations does not:

- invent product behavior;
- publish an inaugural product release;
- hide missing evidence behind a readiness score;
- mutate a target during a readiness audit;
- infer permission for a different repository, owner, visibility, identity, or
  mutation class;
- treat unavailable third-party services as if they passed;
- copy a live sibling repository as an implicit design dependency;
- reimplement Git, PR, review, or third-party-adoption domains owned by sibling
  plugins.

## Domain model

### Project identity

Names are separate typed values with conventional derivations:

- display name;
- repository slug;
- distribution or package name;
- import or module namespace;
- executable command;
- platform application identifier when applicable.

Each name is validated against its owning platform and checked for collisions.
One name never silently substitutes for another.

### Facts, intent, and differences

- A **fact** is observed target state.
- **Intent** is effective policy resolved by Agent Equipment Config.
- A **difference** is a fact that does not equal intent.
- **Drift** is a difference that policy says must converge.
- An **adoption** changes intent to accept a reviewed fact.
- An **exception** permits a scoped requirement not to pass while preserving
  its unmet status and review obligations.

The system never labels a difference as drift until intent and authority are
settled.

### Readiness profiles

`local` readiness proves:

- repository and history policy;
- language and interface foundation;
- tests, typing, linting, formatting, and architecture constraints;
- documentation and agent equipment;
- packaging or build behavior;
- isolated artifact installation and inert entry points;
- local evidence and durable policy.

`github` readiness adds:

- repository metadata and settings;
- protected integration;
- approval and review state;
- required GitHub checks;
- security and supply-chain features;
- tracker state;
- alert and dependency-update disposition;
- remote evidence bound to the merged default branch.

A local target is not a GitHub target with remote checks marked not applicable.
It has a distinct readiness profile.

### Terminal states

Every audit and initialization ends in one of six states:

- `ready`;
- `ready_with_exceptions`;
- `not_ready`;
- `blocked`;
- `unsupported`;
- `indeterminate`.

The state is always qualified by readiness profile and evidence digest.

## Architecture

### Deep modules and seams

The public skills sit over four small internal interfaces:

```text
assess(target, effective_policy) -> assessment
plan(assessment, authority)      -> initialization_plan
apply(initialization_plan)       -> certification
verify(receipt, target)          -> verification
```

Assessment and reconciliation are a deep in-process module. They inventory
facts, compare effective intent, classify differences, and construct a
dependency-ordered plan without exposing their rule graph to callers.

The real variation seams have multiple adapters:

1. **Policy resolution** accepts the rich Agent Equipment Config adapter or a
   resolved, digested plain-handoff adapter.
2. **Foundation providers** implement fixture-backed language, interface,
   documentation, equipment, tracker, security, CI, packaging, and artifact
   contracts.
3. **Destination adapters** implement local Git and GitHub behavior with exact
   preconditions, idempotent writes, post-checks, and capability discovery.
4. **Evidence adapters** separate mutation-capable production from the public,
   independently runnable verifier.

Journal storage, durable policy, exceptions, proof references, evidence
digests, invalidation, and terminal status remain inside the evidence and
certification module. They are not caller-managed orchestration.

### Sibling plugin composition

Tempered Foundations imports operations from the companion plugin architecture:

- **Rolecasting** selects bounded agent and reviewer topology.
- **Versionkeeping** owns Git, worktree, ref, reconciliation, push, and cleanup
  safety.
- **Mergecraft** owns reviewer-facing PR publication, feedback, readiness, and
  protected merge.
- **Tricritical** supplies independent intent, runtime, and structure critics;
  adjudication; and the sole-writer revision loop.
- **Artifact Customs** assesses, adopts, pins, installs, and maintains
  third-party skills and tools.

Tempered Foundations remains the sole inception lifecycle coordinator. Imported
operations return terminal results; they do not start nested lifecycle
coordinators.

### Agent Equipment Config

Agent Equipment Config is the sole editable project-policy source. Tempered
Foundations is its first production external consumer.

Before integration, the narrow upstream tranche must provide:

- a portable, checkout-independent distribution;
- external declarative fragment registration instead of a hard-coded fragment
  registry;
- installed documentation routing that stays inside shipped runtime roots;
- safe concurrent writes with containment, durability, and stale-writer
  protection;
- machine-readable compatibility and support status.

The project policy is stored as an Agent Equipment Config fragment. A resolved,
digested plain handoff may be used when the rich Config integration is
unavailable. The handoff is not a second editable manifest.

Fields should be declarative and flat enough for Config to explain provenance
and locks. Tempered Foundations owns semantic dependencies between fields.

### External design profile

UI projects use a pinned design-profile repository selected by the
dotfiles-managed equipment config. That repository will extend beyond MASTIC's
base colors, materials, theme, styles, and language.

The profile repository is pending external materialization. Tempered
Foundations must model the missing profile as a blocked provider dependency. It
must not read `../mastic/DESIGN.md`, vendor a guessed snapshot, or embed a
machine-local path.

### Plugin structure

The intended installed structure is:

```text
plugins/tempered-foundations/
├── .claude-plugin/plugin.json
├── .codex-plugin/plugin.json
├── skills/
│   ├── auditing-project-readiness/
│   │   ├── SKILL.md
│   │   └── agents/openai.yaml
│   └── initializing-projects/
│       ├── SKILL.md
│       └── agents/openai.yaml
├── references/
│   ├── configuration.md
│   ├── evidence.md
│   ├── github-policy.md
│   ├── lifecycle.md
│   ├── providers.md
│   └── reconciliation.md
├── scripts/
│   └── ... deterministic operations ...
└── assets/
    └── ... provider templates actually copied into projects ...
```

Each `SKILL.md` stays concise, imperative, and below the skill context budget.
It links directly to one-level references for variant-specific contracts. It
does not duplicate provider matrices, schemas, or long examples. Safety-
critical and repeatedly generated mechanics are scripts, not prose.

Tests, evals, fixtures, source evidence, and release evidence remain outside
the installed plugin root. A topology manifest makes owners, imports, access,
authority, and call edges machine-checkable.

## Configuration and interaction

### Orthogonal axes

The interaction model has three axes:

```text
preference_source = interview | effective-config
plan_authority    = confirm | full-afk
existing_state    = review | adopt | converge | refuse
```

Named presets are:

- `new`;
- `new-afk`;
- `assume-nothing`;
- `reconcile-review`;
- `reconcile-adopt`;
- `reconcile-converge`;
- `reconcile-refuse`.

Ivan's default is effective config plus one exact-plan confirmation. Existing
repositories default to reviewing differences.

`assume-nothing` asks every preference question one at a time. An effective-
config run asks nothing that already has a valid, unlocked answer, but still
requires plan confirmation unless `full-afk` was explicitly selected.

### Preference dimensions

The fragment models at least:

- origin and destination;
- owner, GitHub identity, Git author, committer, signing identity, and branch
  prefix;
- visibility, license, default branch, and merge methods;
- display, repository, distribution, module, executable, and app names;
- project scale, language, interface, and artifact kind;
- runtime support window and exact provider locks;
- formatter, linter, type checker, test runner, coverage, architecture, build,
  package, install, and offline-smoke policies;
- skills, tools, Serena, Cocogitto, and design-profile selection;
- human and agent documentation;
- tracker labels, templates, and migration source;
- GitHub review, ruleset, security, quality, update, and alert policies;
- evidence retention, exception, merge authority, and handoff behavior.

Unsupported combinations fail with `unsupported`; they never select a nearest
provider.

## Public skill contracts

### `auditing-project-readiness`

The audit may:

- read target files, Git objects, GitHub state, and public documentation;
- perform network reads;
- create disposable out-of-tree verifier or cache state;
- run already-declared project checks in a way that does not alter the target;
- emit reports and receipts outside the target.

The audit may not:

- edit target files or project environments;
- change the index, worktree, refs, remotes, hooks, or Git configuration;
- change GitHub state;
- install tools into the target or user configuration;
- turn missing authority into an exception.

Checks that ordinarily create caches, coverage files, locks, or build output
run against a disposable verifier checkout or with every output redirected
outside the target.

It returns the inventory, effective intent and provenance, differences,
findings, evidence references, compatibility status, and terminal result.

### `initializing-projects`

Initialization begins with the same read-only inventory. It then produces an
exact plan containing targets, expected preconditions, mutation classes,
coherent PR topology, review and merge behavior, evidence obligations, and
compensating actions.

Confirmation of that plan authorizes its declared local and remote mutations
through protected merge. A changed owner, repository, visibility, identity,
policy, mutation class, or material scope invalidates authority and requires a
new confirmation.

Initialization never bypasses a missing operator-only capability. GitHub App
installation, credential selection, exception approval, signing setup, and
other human gates pause without automatic timeout.

## Lifecycle

### New local project

1. Resolve and validate project identities and provider support.
2. Verify Git author, committer, and signing configuration.
3. Create the project root and local Git repository.
4. Create the proof scaffold and project policy.
5. Install and pin equipment through Artifact Customs.
6. Initialize Serena and prove repository-scoped health.
7. Run provider, documentation, security, packaging, and artifact checks.
8. Create cohesive signed checkpoints through Versionkeeping.
9. Re-audit the immutable commit and issue local certification.

### New GitHub project

1. Complete new-local preflight through a signed empty root commit.
2. Create the exact GitHub repository with confirmed owner, visibility,
   license, description, and default branch.
3. Establish initial PR and approval protection without requiring checks that
   cannot exist yet.
4. Publish an adaptive stack of one to three coherent PRs:
   - policy, equipment, documentation, and repository foundation;
   - language, interface, CI, packaging, and artifact baseline;
   - security and final readiness tightening when separate review is useful.
5. Discover the real required-check names and approving reviewer path.
6. Tighten the final ruleset without weakening any pre-existing protection.
7. Resolve initial Dependabot, CodeQL, Scorecard, dependency-license, and other
   findings.
8. Pass Tricritical and remote feedback loops.
9. Merge through the protected path using Mergecraft.
10. Re-audit the merged default branch and issue GitHub certification.

The empty signed root commit ensures that all source-bearing foundation changes
can travel through review after the branch exists.

### Extraction

Extraction requires an authoritative source declaration. It records source
repository, paths, source commit, selected history policy, licenses, issue
mappings, and ownership. It never mints provenance for material that cannot be
traced.

Tracker migration preserves source URLs, original authorship in body metadata,
labels, hierarchy, relationships, state, and a deterministic source-to-target
mapping. Re-running reconciles instead of duplicating.

### Retrofit

Retrofit inventories current facts and defaults to `review`. The operator may
adopt, converge, or refuse each policy-relevant difference. Current protections
are never weakened merely to simplify reconciliation. Existing coherent
toolchains may be adopted only when a certified provider supports them;
otherwise the result is `unsupported` or a plan to converge.

### PR topology

The PR topology is adaptive but bounded. It uses the smallest coherent stack
that preserves meaningful review, with no more than three initialization PRs
by default. Language and artifact foundations land before readiness is claimed.

## Recovery and concurrency

The recovery model is resumable-forward.

Every mutation has:

- an exact target;
- an expected precondition and fingerprint;
- an idempotency key;
- an atomic or least-race write strategy;
- a post-condition check;
- a journal transition written durably after adoption;
- a known compensating action when one exists.

On failure, the run stops with classified observed state. The next run compares
the journal to live truth and either adopts the completed mutation, retries an
idempotent operation, or blocks on divergence. It never assumes that the last
process crashed before or after a write.

Automatic destructive rollback is forbidden. Repository deletion, history
rewrites, settings removal, and other destructive compensations require
separate exact authority.

Filesystem operations must resist symlink substitution, path escape, stale
writers, concurrent writers, partial writes, and lost directory entries.
Remote operations must resist stale refs, changed repository identity,
pagination omissions, rate limits, retry duplication, and late rule changes.

## Ivan's initial profile

The initial implementation is configurable, but its implemented affordances
and default values focus on Ivan's personal preferences. Alternative values are
implemented first when they are trivial ablations of the same contract.

### Identity and history

- Personal GitHub owner: `nisavid`.
- Personal Git identity: `Ivan D Vasin <ivan@nisavid.io>`.
- Branch prefix: `ivan/`.
- Operator and agent commits use the selected personal identity.
- Explicitly trusted bots may enter protected history.
- Protected history requires verified commit signatures.
- Conventional Commits are enforced by Cocogitto locally and in CI.
- The personal profile refuses Systalyze identities, credentials, tooling, and
  repository conventions unless a different explicit project profile selects
  them.

### License

- Public personal repositories default to MIT.
- Private and local-only repositories remain unlicensed unless policy selects a
  license.
- Visibility and target owner remain explicit plan inputs even when a profile
  supplies defaults.

### GitHub rules

The final default-branch ruleset requires:

- pull requests;
- one approval at the current head from a human or allowlisted approving app;
- CodeRabbit as Ivan's default approving app;
- stale approval dismissal;
- approval of the latest push;
- resolved review conversations;
- strict, current required checks;
- applicable CodeQL severity thresholds;
- linear history;
- verified signatures;
- no force pushes;
- no branch deletion;
- no merge commits.

Rebase and squash merges are allowed. Rebase is preferred operationally.

### Security and supply chain

The profile enables every applicable capability, including:

- advanced CodeQL;
- dependency review;
- dependency graph and Dependabot alerts;
- secret scanning and push protection;
- private vulnerability reporting;
- OpenSSF Scorecard where meaningful;
- exact action commit pins and minimal workflow permissions;
- versioned GitHub-hosted runner generations;
- ecosystem vulnerability and dependency-license checks;
- release SBOMs and artifact attestations when releases exist.

Unavailable or inapplicable capabilities require explicit classification. An
available failing check cannot be waived by calling it unavailable.

The dependency-license default permits the reviewed permissive CQMgr set:

- Apache-2.0;
- BSD-2-Clause;
- BSD-3-Clause;
- HPND-Markus-Kuhn;
- MIT;
- MIT-0;
- MPL-2.0;
- PSF-2.0.

Unknown metadata fails closed. A dependency exception binds the exact package,
version, resolved artifact, reviewed license evidence, owner, and review
trigger.

### Dependency maintenance

GitHub projects receive weekly grouped Dependabot updates for language
dependencies and GitHub Actions plus immediate security updates. Updates follow
the normal review and check gates. Auto-merge is disabled by default.

Initialization resolves or dispositions every initial update PR and alert that
the foundation creates.

### Tracker

The selected tracker receives the standard Matt Pocock labels and repository
templates. When a source tracker is declared, provenance-preserving migration
is required before certification.

### Agent equipment

Every project receives exact, content-locked installations of:

- `mattpocock/skills`;
- `obra/superpowers`;
- repository-scoped Serena;
- Cocogitto hooks and CI.

After installing the Matt Pocock collection, initialization invokes
`setup-matt-pocock-skills` and verifies its repository result. Serena is
initialized as one repository-scoped project, keeps checkout identity in
ignored local state, and must pass its real health and memory checks.
Cocogitto installs the declared hooks and a pinned GitHub Actions workflow,
then proves commit-policy enforcement with positive and negative fixtures.

UI projects additionally run Impeccable installation and initialization. The
configured external design profile materializes its base before remaining
project-specific Impeccable decisions are asked. Artifact Customs owns
assessment, locks, installation, and later maintenance. A tool's upstream
default branch or mutable release channel is never a durable lock.

### Documentation

The documentation baseline is shape-aware.

Every project has:

- a verified human-facing `README.md` that routes by user goal using Diataxis;
- `LICENSE` when licensed;
- `SECURITY.md`;
- `CONTRIBUTING.md` for contribution-capable repositories;
- concise executable agent instructions;
- agent-facing domain context.

The README workflow invokes `applying-diataxis`; it does not merely copy a
static four-section template. Commands and behavioral claims are verified
against the proof scaffold and selected provider.

`PRODUCT.md`, `DESIGN.md`, a context map, and deeper tutorial, how-to,
reference, or explanation documents are required only when their concepts have
real content. The initializer never generates empty ceremonial documents.

## Certified provider contracts

### Common provider rules

Each provider must:

- resolve a maintained support window to exact runtime and tool locks;
- have deterministic fixtures for every claimed language/interface/artifact
  combination;
- produce only a minimal proof scaffold with no product behavior;
- run formatting, linting, typing, tests, coverage, vulnerability, license,
  build, package, install, and inert-entry-point checks as applicable;
- prove the built distribution rather than only the source tree;
- keep help and version paths free from credentials, keyrings, cloud SDKs,
  application adapters, network calls, or other runtime side effects;
- expose machine-readable compatibility and support status.

The default coverage floor is 90 percent for both line and branch coverage over
behavior-bearing first-party source. Critical behavior also needs contract or
property tests. Generated or trivial glue exclusions require reviewed
rationale.

The complete quality suite runs on one versioned primary CI environment.
Distributable CLI and TUI artifacts then build, install, and run help/version
smoke tests on Linux, macOS, and Windows. Web providers use targeted browser
proof. This tiered matrix avoids repeating every static check on every runner
without reducing artifact compatibility to a claim.

### Python

The Python provider generalizes CQMgr's proven baseline:

- CPython maintained window resolved to three supported minors;
- PEP 621 metadata;
- uv and `uv_build`;
- a committed `uv.lock` and exact required uv version;
- Ruff with `ALL` plus narrow explained conflicts;
- strict Pyrefly;
- pytest and branch coverage;
- Hypothesis when behavior admits useful properties;
- Import Linter when architectural layers exist;
- dependency-license and installed-distribution audits;
- Click for CLI;
- Textual for TUI.

The proof installs the built wheel or source distribution into an isolated
environment and exercises the installed executable. It tests supported
platform and Python combinations named by the provider.

### TypeScript

The TypeScript provider uses an exact compatible Vite+ version for both CLI and
React web projects. Vite+'s upstream beta status is preserved in compatibility
metadata; the provider's own fixtures determine whether a version is certified.

The provider uses Vite+ for:

- managed Node and package-manager behavior;
- install and dependency tasks;
- Oxfmt and Oxlint;
- type-aware rules and type checks;
- Vitest and coverage;
- Vite and Rolldown web builds;
- tsdown package builds;
- `vp pack` CLI artifacts.

CLI proof installs or executes the packed artifact in isolation. Web proof
builds the production application, runs accessibility checks, and exercises a
targeted browser matrix. The provider records every Vite+ limitation needed to
keep the claimed configuration inside its certified subset.

### Go

The Go provider uses:

- the current and previous supported Go releases resolved to exact toolchains;
- Go modules;
- explicit strict `golangci-lint` v2 configuration;
- `gofumpt` and import formatting;
- tests with race detection, shuffling, and coverage;
- vulnerability and dependency-license checks;
- Cobra for CLI;
- Bubble Tea v2 for TUI;
- GoReleaser snapshot builds and cross-platform artifact proof.

Cobra help and version must not initialize application runtime. Bubble Tea
proof uses renderer- and signal-independent lifecycle tests in addition to
rendered behavior.

### Rust

The Rust provider uses:

- an exact `rust-toolchain.toml`;
- a declared MSRV plus current stable CI;
- rustfmt;
- Clippy with warnings denied;
- Nextest;
- LLVM coverage;
- cargo-deny for advisories, licenses, bans, and sources;
- Clap for CLI with parser-schema tests;
- Ratatui for TUI with buffer assertions and terminal restoration tests;
- `dist` machine-readable release plans and artifact builds.

Artifacts are installed or executed outside the source tree. Release machinery
is proven but no release is published during initialization.

## GitHub bootstrap details

GitHub protection must be staged because required check contexts do not exist
before their workflows land.

The bootstrap may temporarily have fewer required checks only when the branch
has no workflow-defined contexts yet. It must still require PR integration and
the configured approval path as soon as the signed root branch exists. The
final foundation PR proves the fully tightened ruleset from a fresh head.

The approving-reviewer contract requires an approval on the current head SHA
from an allowlisted principal. Copilot or Greptile comments do not satisfy the
approval gate. Earlier change requests must be superseded or dismissed by the
reviewer's later state according to GitHub semantics.

The initializer discovers exact check names from real runs and refuses to
configure ambiguous or missing contexts. It validates the installed ruleset
through the GitHub API after every mutation and again after merge.

## Evidence and certification

### Persisted policy

The repository commits reproducible intent:

- project identities and shape;
- selected readiness profile;
- provider IDs and exact locks;
- required documentation, equipment, security, and tracker policy;
- approved durable exceptions.

It does not commit transient GitHub observations or run journals.

### Run journal

The secret-safe journal records:

- target and immutable starting state;
- resolved configuration and provenance digest;
- plan and authority digest;
- step preconditions and fingerprints;
- attempted mutations and idempotency keys;
- post-check results;
- evidence references;
- divergence and recovery decisions.

The journal lives outside tracked source. Local retention is policy-controlled.

### Final receipt

The final receipt binds:

- repository identity and default-branch commit;
- readiness profile and terminal state;
- effective-config digest;
- provider and compatibility digests;
- plan and authority digest;
- local and remote evidence references;
- observed timestamps;
- exception records;
- verifier version.

For GitHub, PRs, reviews, check runs, alerts, rulesets, and merged commits provide
durable remote evidence. A receipt never includes credentials or sensitive
environment values.

### Exceptions

An exception records:

- the exact unmet requirement;
- applicability or availability classification;
- rationale and supporting evidence;
- approving owner;
- project and provider scope;
- expiry or review trigger;
- receipt binding.

Any active exception yields `ready_with_exceptions`. Expired or invalidated
exceptions yield `not_ready`, `blocked`, or `indeterminate` according to the
observable condition.

### Invalidation

Certification becomes stale when any bound value changes, including:

- default-branch commit;
- effective policy;
- provider compatibility;
- required workflow or ruleset state;
- dependency lock;
- design-profile pin;
- approving reviewer state when still relevant;
- exception validity.

The read-only audit can reproduce the verifier side without trusting the
original mutation-capable producer.

## Implementation strategy

Implementation proceeds as a dependency-ordered stack.

1. Freeze the approved product contracts and CQMgr source evidence.
2. Implement the narrow Agent Equipment Config external-consumer tranche.
3. Add the plugin manifests, topology, public skill shells, schemas, and failing
   contract tests.
4. Build the read-only inventory, reconciliation, evidence, and certification
   core.
5. Add local repository, documentation, equipment, and tracker foundations.
6. Add providers one at a time: Python, TypeScript, Go, then Rust.
7. Add GitHub discovery, staged genesis, policy reconciliation, PR integration,
   alert closure, and certification.
8. Integrate the external design-profile repository after it publishes a
   stable contract and pin.
9. Dogfood through explicit local and personal-GitHub test targets.
10. Obtain independent Tricritical and external review before GA claims.

The implementation must use a clean `nisavid/agents` worktree. It must not
modify the dirty worktree where the companion five-plugin architecture is still
being completed.

## Validation strategy

Validation includes:

- skill and plugin manifest validation;
- topology and runtime-root validation;
- source, installed, and runtime separation;
- trigger and behavior evals in each supported harness;
- exact pin, content lock, and compatibility validation;
- configuration provenance and lock tests;
- unsupported-combination tests;
- symlink, traversal, and containment attacks;
- secret-redaction and hostile-output tests;
- concurrent writer and stale fingerprint tests;
- crash injection before and after every durable transition;
- retry, resume, compensation, and divergence tests;
- GitHub pagination, rate-limit, permission, and partial-failure tests;
- branch bootstrap and final ruleset tests;
- current-head reviewer tests;
- alert, issue, and migration reconciliation tests;
- deterministic fixtures for every certified provider combination;
- isolated distribution and entry-point tests;
- private-producer and public-verifier receipt tests;
- live adoption evidence from exact authorized test targets.

Behavioral and trigger evals follow the current harness's observable contracts.
Codex evals use isolated agents only when authorized, inherit the configured
model by default, and do not treat Claude-specific trigger evidence as Codex
evidence.

## External dependencies and gates

Implementation is gated by:

- the companion plugin architecture becoming available on an integration base;
- the Agent Equipment Config external-consumer tranche;
- the external design-profile repository for UI certification;
- explicit authority for named live GitHub test targets;
- verified personal commit signing and trusted-bot compatibility.

The design-profile dependency blocks only UI certification. Local non-UI
providers and the read-only core can progress independently once their owning
integration base is available.

## Acceptance criteria

Tempered Foundations v1 is complete only when:

1. Both public skills route and behave correctly in supported harnesses.
2. The audit makes no target mutation and emits independently verifiable
   categorical results.
3. Initialization can create and certify a new local project for every supported
   provider combination.
4. Initialization can create and certify an explicitly authorized GitHub
   project through its final protected ruleset.
5. Extraction preserves declared source and tracker provenance.
6. Retrofit can review, adopt, converge, and refuse differences without
   weakening current protections.
7. Crash and concurrency tests prove resumable-forward behavior.
8. Exact locks, signed history, review approval, checks, security features,
   artifacts, and initial alert disposition are verified at the merged commit.
9. Approved exceptions remain visible, scoped, expiring, and receipt-bound.
10. The plugin can be installed from the `nisavid/agents` marketplace without a
    mutable source checkout.
11. No machine-local path, credential, private task identifier, or unrelated
    project convention appears in installed or committed public artifacts.
