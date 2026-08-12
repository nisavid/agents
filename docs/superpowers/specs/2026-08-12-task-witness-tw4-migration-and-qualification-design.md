# Task Witness TW4 Migration And Qualification Design

Status: accepted design checkpoint, 2026-08-12

This document amends the Task Witness canonical client design for the TW4
cross-version migration and release-qualification boundary. The canonical
client design remains authoritative for deployment, recovery, validation, and
rollback behavior that this document does not change.

## Decisions

TW4 uses these three decisions:

1. Migrate from exact Freeze 5 through one immutable bridge release. Preserve
   recovery through the exact staged prior controller at both hops.
2. Use native Sol High for the independent acceptance review. Rolecasting's
   native adapter authenticates model selection, dispatch topology, and each
   execution result; Tricritical owns review completeness, independent
   adjudication, and terminal verification.
3. Split qualification evidence into one candidate-owned suite inventory, one
   external receipt from each required native host, and one detached release
   manifest.

These decisions do not create a generic legacy decoder, a new signing root, a
single-hop recovery exception, or a candidate-authored release disposition.

## Goals

- Install the current TW4 control set from Freeze 5 without weakening the
  staged-prior-controller recovery rule.
- Prove the same complete suite on actual macOS arm64 and Linux x86_64 hosts
  under disposable, passwd-backed, unprivileged users.
- Bind each run to an externally qualified CPython 3.13+ closure and the
  literal rendered Task Witness shim.
- Require a fresh, independently authenticated Sol High review of the exact
  unchanged candidate.
- Keep `production_eligible` false and Task Witness undiscoverable until a
  detached manifest closes the two host receipts, canonical review evidence,
  and final public-release identity.

## Non-Goals

- Translating arbitrary historical receipt or policy schemas.
- Rewriting retained receipts, stages, or journals.
- Recovering through candidate code or a newly staged controller.
- Reading a disappeared external candidate during recovery.
- Treating containers, emulation, mocked platforms, or architecture labels as
  native-host qualification.
- Claiming cryptographic signatures for cooperative native-harness receipts.
- Qualifying retained-history garbage collection or a new platform family.

## Migration Domain

The migration has three immutable control-set identities:

- **F5**: exact Freeze 5, the currently supported predecessor.
- **B1**: the one-purpose immutable migration bridge.
- **TW4**: the current-schema target.

F5 is fixed at commit
`96608a9b91d4dcf3f468a4fab1f0e008c9c32b36`, controller
`8dc51b2a644e30d1f7c4f3b71711698b4130b43f1517e9f5361c6d1a0f7d6cfe`,
policy `23e84f210ba69ef79e02bfc3039b2c8be3b91153d7649009b3a22850f5086245`,
and client `778186f6a460655a8b390c831e05c233171236898663ad4155bd45695597c6cf`.
B1 and TW4 receive exact identities only when their implementation bytes
freeze; no mutable branch or release label can substitute for them.

The only allowed edges are `F5 -> B1` and `B1 -> TW4`. `F5 -> TW4` rejects
before staging or live mutation. No other predecessor may use B1's migration
surface.

### Bridge identity

B1 is a complete Task Witness release, not a patch or runtime adapter. Its
frozen identity binds:

- repository, revision, and subtree identity;
- controller, client, launcher, policy, smoke, runtime, and rendered-shim
  identities;
- the exact F5 predecessor controller, policy, receipt, and control-surface
  schema identities;
- the exact legacy inbound and current outbound request/stage contracts; and
- the exact migration purpose and supported source mode.

The first bridge supports the proven `harness_snapshot` source mode. Publisher
channel and exact-release migrations require an independently designed bridge;
they do not fall back to harness sentinels.

B1 is transition-only. It remains absent from ordinary marketplace discovery
and cannot serve as the terminal production release.

Current TW4 retains an exact, closed B1/F5 allowlist for historical validation.
The allowlist admits only the frozen identities. A well-formed but unlisted
legacy receipt, bridge, controller, or policy fails closed.

B1 does not hardcode its successor digest. That would create a digest cycle
because TW4 must retain B1's exact identity. Instead, the external deployer
that already produces `task-witness-deployer-authorization-v1` from Task
Witness's public `DeploymentAuthorizationFacts` issues one detached,
transaction-bound `task-witness-bridge-transition-authorization-v1` after B1,
the final TW4 release, and the detached release manifest all have immutable
identities. `plugins/task-witness/controller/task_witness_deploy.py` validates
both contracts and projects them into receipts; the client validates the
retained projection. The controller, client, bridge, and candidate never issue
authorization. This is a new purpose under the existing external deployer
authority, not a signer or trust root.

The transition authorization binds exact B1 active-receipt, controller,
policy, source, and retained-chain identities; exact TW4 commit, tree,
source-evidence, control-set, and public-release identities; the exact
validated release-manifest digest; the deployment plan and authorization-facts
digests; and the one-use transaction digest. B1 rejects a missing, reused,
wrong-purpose, or disagreeing authorization before stage creation. A merely
current-schema-compatible candidate is never sufficient.

Staging descriptor-reads and copies the exact canonical manifest and transition
authorization into private, create-new stage files named
`bridge-transition-manifest.json` and
`bridge-transition-authorization.json`. The plan, authorization facts, stage
receipt, activation intent, and journal use an acyclic binding order: the plan
and authorization facts bind the manifest digest and strict endpoint
projection; the external transition authorization binds those exact facts,
plan, and transaction; then the stage receipt, activation intent, and journal
bind the authorization's raw digest as well. Activation reopens and revalidates
those exact staged bytes before journal creation.

Recovery reads only the staged copies. It accepts the authorization's same
transaction digest as continuation of that exact in-progress journal; this is
not a second use. The same authorization with another transaction, plan, stage,
candidate, or active B1 identity is reuse and rejects before mutation. A
successful TW4 deployment receipt retains a closed migration projection with
the F5/B1 edge, manifest digest, authorization digest, purpose, transaction,
and exact endpoint identities. Later controller and client validation uses
that retained projection without reopening the external stage or manifest.

### B1's two surfaces

B1 has two explicit, disjoint surfaces:

- Its **inbound F5 surface** is byte-compatible with F5's persistent policy,
  request, receipt, stage, and journal contracts. F5 can install B1 without
  understanding current `SourceEvidence` objects. This surface retains F5's
  26-contract control inventory.
- Its **outbound TW4 surface** understands the current `SourceEvidence`,
  authorization, receipt, stage, and recovery contracts. B1 can install TW4
  and can serve as TW4's staged prior controller during recovery. This surface
  validates TW4's 27-contract inventory, including `source_evidence`.

B1 does not expose a general schema union. Its inbound adapter accepts only the
exact F5 identity and primitive legacy harness fields. Its ordinary public
deployment request remains current-shaped for `B1 -> TW4`.

The inbound recovery adapter is named `Freeze5RecoveryRequest`. It exists only
inside exact B1 recovery dispatch and carries F5's primitive
`source_selection_raw`, `manager_binding_raw`, `manager_receipt_raw`,
`runtime_qualification_raw`, transaction digest, and expected active-receipt
digest. It is not a public installation request or a legacy-schema registry.

## Migration Protocol

### Hop 1: F5 to B1

1. Exact F5 prepares and stages B1 under F5-compatible policy and receipt
   schemas.
2. The candidate is proven to be exact B1, including its frozen predecessor
   allowlist and dual-surface controls.
3. F5 executes the existing complete-control replacement program.
4. Every process-loss cut recovers through a freshly loaded exact staged F5
   controller. The live B1 controller may dispatch recovery, but it rebuilds
   the exact F5-module request from primitive legacy fields and delegates the
   bound old-schema stage to F5.
5. Acceptance leaves an exact old-schema B1 active receipt and a complete F5
   rollback preimage. Rejection restores exact F5 and removes only
   transaction-owned B1 authority.

### Hop 2: B1 to TW4

1. Exact B1 validates the detached release manifest and fresh bridge-transition
   authorization, then prepares and stages the exact authorized TW4 through
   its current-shaped outbound surface.
2. B1 validates the old active B1 epoch and the current candidate TW4 epoch
   independently. Only the exact bridge transition may cross this schema
   boundary.
3. B1 executes the existing complete-control replacement program.
4. Every process-loss cut recovers through a freshly loaded exact staged B1
   controller. Recovery reconstructs B1-module `SourceEvidence` variants and
   transition authority from exact staged primitive bytes and never reads the
   external candidate, manifest, or authorization source.
5. Acceptance leaves a current-schema TW4 active receipt and the retained
   F5/B1/TW4 chain. Rejection restores exact B1 and removes only
   transaction-owned TW4 authority.

### Direct-upgrade and bridge misuse rejection

The following fail before stage creation or live mutation:

- F5 preparing or activating a current-schema TW4 candidate;
- an unlisted predecessor preparing B1;
- F5 preparing a modified or reissued bridge identity;
- B1 preparing an unsupported legacy, publisher-channel, or exact-release
  migration through its inbound surface;
- B1 preparing any current-schema candidate without the exact detached
  release manifest and one-use bridge-transition authorization;
- a caller passing a dataclass instance across a freshly loaded module
  boundary; and
- a recovery path that selects the live, candidate, or current controller in
  place of the exact staged prior controller.

### Crash and recovery proof

Both hops reuse the complete-control crash program and prove the same cuts:

- prepared, frozen, drained, and every additive installation;
- every controller, policy, launcher, client, smoke, selector, deployment, and
  shim temporary write, rename, and parent synchronization;
- candidate smoke before return and after durable acceptance;
- reverse replacement after rejection;
- rollback smoke before return and after durable acceptance;
- rollback-receipt and candidate-receipt cleanup;
- terminal result retention, journal unlink, and K.1 reconciliation; and
- recovery interrupted at each recovery mutation.

At every cut, the test proves the exact executor module identity, journal and
stage stability, selector prefix, retained chain, smoke count, result bytes,
and transaction-owned cleanup.

Hop-two tests additionally cut after each transition-evidence stage write and
parent synchronization; remove or replace each staged file; replay the same
authorization for the same live journal; attempt reuse with another transaction
or endpoint; and prove the successful retained migration projection remains
client- and controller-valid after the external stage disappears.

## Qualification Artifacts

All JSON artifacts use canonical UTF-8 JSON, reject duplicate keys and
nonfinite values, have exact schemas, and bind raw bytes by SHA-256. Unknown,
missing, duplicated, reordered where order is semantic, or extra values fail
closed.

### Candidate suite inventory

The immutable candidate owns
`release/task-witness/tw4-suite-inventory.json` with contract
`task-witness-tw4-suite-inventory-v1`.

It contains:

- `schema_version` and `contract`;
- one ordered, uniquely identified entry per suite or platform vertical;
- a closed executor selector and literal argument vector with no shell
  interpolation;
- target applicability from the exact set `macos-arm64` and `linux-x86_64`;
- phase, expected terminal, and exact expected test or scenario count; and
- ordered aggregate entry and count identities.

The executor selector is closed to the qualified CPython executable, a
recorded canonical system tool, or the literal rendered shim. Entries cannot
choose an environment, working directory, credential, network authority, or
unregistered executable.

The inventory contains the complete common process, envelope, drift,
activation, deployment, recovery, rollback, package, and release-validation
suites. It also contains named verticals for forward update, authorized
downgrade/manual rollback, candidate-rejection rollback, candidate-source
disappearance, provider-cache deletion or movement, literal rendered-shim
execution, and the two migration hops. The same complete applicable set runs
on both required targets.

The inventory owns no host observation, runtime provenance, reviewer result,
release disposition, or final candidate identity. This avoids a digest cycle.

### Native-host qualification receipt

The fixed five-input runner reads the suite inventory from the candidate root.
On success it creates exactly one external canonical receipt with contract
`task-witness-tw4-host-qualification-receipt-v1`. Failure creates no receipt
and preserves any preexisting output.

The candidate root is an exact Git checkout owned outside the qualification
user's write authority. The runner derives its commit and tree with the
recorded canonical Git tool, proves the reviewed closure, and revalidates the
root, Git identity, inventory, and every executed input before and after the
suite. A dirty, replaced, writable, or non-Git candidate fails before receipt
publication.

Each receipt binds:

- the frozen qualification-candidate commit, tree, source-shape record, and
  computed control/source/test closure;
- the raw suite-inventory digest and ordered aggregate identities;
- exactly one target, either macOS arm64 or Linux x86_64;
- the raw platform-profile and runtime-closure evidence digests;
- the stable externally qualified CPython executable and complete closure;
- the disposable passwd identity, home, groups, and clean credential state;
- the literal rendered-shim identity;
- each suite's expected and observed count, exit status, bounded stdout and
  stderr digests and lengths, and terminal result; and
- one terminal `qualified` disposition only when every required entry passes
  on unchanged candidate, platform, runtime, and tool bindings.

A host receipt makes no claim about the other host, review independence,
public-release eligibility, or final promotion identity.

### Native Sol High review evidence

The accepted cooperative trust model uses existing owners:

- Rolecasting owns model and topology authority. Its `choosing-agent-models`
  skill supplies the policy for selecting `gpt-5.6-sol` at `high`, but does not
  issue or authenticate a receipt;
- Rolecasting's native adapter applies that policy, authenticates the selected
  executor, freezes the dispatch plan, and alone issues the authoritative
  model, topology, and one execution-result receipt per dispatch;
- Tricritical `review` proves the required independent critic set and exact raw
  reports;
- Tricritical `adjudicate` independently grades and disposes every finding;
  and
- the Tricritical loop leader verifies the unchanged candidate and terminal
  disposition.

The evidence bundle binds the frozen review input and candidate before and
after every execution, distinct sessions and contexts, bounded read-only
authority, denied subdelegation and external action, raw reports and failures,
completeness, adjudication, limitations, and final verification.

Current native review records may bootstrap implementation review. They do not
open public release. After Rolecasting and Tricritical's canonical issuer and
validator chain is installed, a fresh canonical native Sol High re-review of
the same unchanged qualification candidate is mandatory. A degraded,
incomplete, drifted, substituted-model, or self-adjudicated review cannot enter
the release manifest.

### Detached release manifest

The release coordinator creates an external canonical manifest with contract
`task-witness-tw4-release-manifest-v1`. `validate_task_witness.py` consumes it
only in final-release mode.

The manifest binds:

- the frozen qualification-candidate identity and suite-inventory digest;
- a closed target map containing exactly one macOS arm64 receipt digest and one
  Linux x86_64 receipt digest;
- the canonical review-evidence bundle digest plus its model, topology,
  execution, adjudication, and unchanged-verification identities;
- the exact final public-release commit and tree;
- the exact F5/B1/TW4 migration edge and B1 retained-history identity;
- the complete promotion delta from the qualified candidate; and
- the terminal `release-qualified` disposition.

The manifest is detached because an in-candidate manifest cannot bind the Git
tree that contains itself. It neither observes hosts nor creates review facts.
It only closes already-authoritative evidence.

## Public-Release Promotion

The qualification candidate remains `production_eligible: false` and absent
from marketplace discovery. Both host receipts and canonical review evidence
bind that exact candidate.

After all evidence is complete, the final public-release identity may differ
only by the mechanically checked promotion delta:

- set the Task Witness registration's `production_eligible` value to `true`;
- add the exact Task Witness marketplace route; and
- update only generated release records whose values are deterministic
  consequences of those two changes.

No executable, policy, test, suite inventory, source shape input, documentation
contract, or other semantic byte may change. The final validator independently
reconstructs the allowed delta, verifies both Git identities and every bound
artifact, and rejects any extra change. A semantic repair creates a new
qualification candidate and invalidates both host receipts, review evidence,
the detached manifest, and every unconsumed transition authorization derived
from it. Any qualification-candidate, promotion-delta, or final-release
identity change requires fresh dependent artifacts and a fresh transaction
identity. No digest may be patched forward.

## Validation And Failure Rules

`validate_task_witness.py` has three distinct modes:

- source-stage validation checks the candidate, suite inventory, schemas, and
  ineligible registration without requiring external receipts;
- qualification validation checks one host receipt against the exact candidate
  and never changes eligibility; and
- final-release validation requires the detached manifest, two distinct native
  receipts, canonical Sol High evidence, and exact promotion delta.

The validator rejects target duplication, missing target, receipt reuse,
candidate or inventory disagreement, runtime/profile/shim drift, incomplete or
degraded review, noncanonical artifacts, unknown bridge identity, changed
semantic bytes, stale manifest or transition authorization, or a manifest
inside the candidate tree.

Qualification and manifest validation are read-only. They do not install,
publish, mutate Git, contact a provider, create users, or infer missing
evidence.

## Implementation And TDD Order

1. Freeze direct `F5 -> TW4` rejection plus B1 identity-shape, predecessor,
   schema-surface, allowlist, detached-manifest endpoint, and bridge-transition
   authorization REDs. Do not assert a not-yet-built digest. At this stage B1
   validates only the authorization-bound manifest identity and endpoint; the
   final validator later owns the host and review evidence inside the manifest.
2. Build B1's F5-compatible inbound stage and prove bounded `F5 -> B1`
   success and rejection through staged F5.
3. Build B1's current outbound stage and prove bounded `B1 -> TW4` success and
   rejection through staged B1.
4. Freeze B1's complete bytes, record its exact immutable identities, replace
   identity-shape fixtures with exact-value regressions.
5. Add exact retained F5/B1/TW4 controller and client validation plus K.1
   coexistence. Every recovery audit must understand the policy epoch and
   receipt schema for the retained suffix before mutation.
6. Run both hops' full crash and recovery matrices, then prove manual rollback
   across the retained chain as a focused successor slice.
7. Freeze the suite-inventory parser and closed execution selector before
   enabling the runner terminal.
8. Emit one create-new host receipt and verify it independently.
9. Add detached-manifest parsing, canonical review-evidence validation, and the
   exact public-release promotion check. Pin rejection of a manifest or
   transition authorization made stale by any candidate, evidence,
   promotion-delta, final-identity, plan, or transaction change.
10. Rebaseline source-shape and package inventories only after all preceding
   bytes freeze.
11. Run bootstrap Sol High review, both native host qualifications, install and
   validate the canonical Rolecasting/Tricritical issuer chain, run its fresh
   re-review, validate the final manifest, and perform public-release promotion
   in that order.

## Acceptance

TW4 is release-qualified only when:

- both migration hops and their full crash matrices are green;
- the complete candidate suite and package/source-shape validators are green;
- the macOS arm64 and Linux x86_64 receipts independently validate;
- the canonical native Sol High review is complete and clean on unchanged
  bytes;
- the detached manifest validates the two receipts, review evidence, and exact
  promotion successor; and
- final-release validation changes no state and reports one exact immutable
  public-release identity.

Until then, Task Witness remains ineligible and undiscoverable.
