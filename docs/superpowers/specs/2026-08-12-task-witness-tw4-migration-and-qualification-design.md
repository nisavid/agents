# Task Witness TW4 Migration And Qualification Design

Status: proposed written checkpoint; product choices accepted 2026-08-12,
pending explicit written-spec approval

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

The only authorized, stageable edges are `F5 -> B1` and `B1 -> TW4`.
`F5 -> TW4` rejects during preparation because exact F5 cannot parse the
current 27-contract candidate policy. No other predecessor may use B1's
migration surface.

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

B1's tree construction is closed. Its policy and client are the exact F5
bytes, preserving the installed 26-contract epoch and old receipt consumer.
Its controller starts from the current SourceEvidence-capable controller and
adds only the closed F5 inbound, bridge-transition, and retained-history
behavior in this design. These nine paths remain byte-identical across F5,
B1, and the current candidate:

- `.claude-plugin/plugin.json`;
- `.codex-plugin/plugin.json`;
- `client/task_witness_shim.sh.in`;
- `launcher/task_witness_launch.py`;
- `runtime/bundle_io.py`;
- `runtime/canonical.py`;
- `runtime/task_witness.py`;
- `runtime/trust.py`; and
- `smoke/task_witness_smoke_validator.py`.

No 26/27-contract union policy or dual-schema client is introduced. B1's
controller alone treats its installed policy and receipt as the exact F5 epoch
while separately validating the current 27-contract TW4 candidate epoch.

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
Witness's public `DeploymentAuthorizationFacts` issues detached, one-use,
transaction-bound `task-witness-bridge-transition-authorization-v1` records
after B1, the final TW4 release, and the detached release manifest all have
immutable identities. Each record has an exact execution class of either
`isolated-rehearsal` or `live-migration`. The exact B1 version of
`plugins/task-witness/controller/task_witness_deploy.py` alone validates the raw
transition contract during stage, activation, and recovery and projects it
into the successful TW4 receipt. Preparation validates the candidate, manifest,
and strict endpoint projection, then returns the plan and authorization facts
that the external deployer must bind. The TW4 controller and client validate
only the retained transition projection. `validate_task_witness.py` does not
consume or validate transition authorization. The controller, client, bridge,
and candidate never issue authorization. This is a new purpose under the
existing external deployer authority, not a signer or trust root.

The transition authorization binds exact B1 active-receipt, controller,
policy, source, and retained-chain identities; exact TW4 commit, tree,
source-evidence, control-set, and public-release identities; the exact
validated release-manifest digest; the deployment plan and authorization-facts
digests; the expected active-receipt core digest; the execution class; and the
one-use transaction digest. The core is the exact deterministic unsigned
active-receipt projection with both the new `migration` projection and the
final `content_sha256` omitted. An
`isolated-rehearsal` authorization additionally binds a canonical endpoint
projection containing the target, deployment-root path, device, inode, owner,
mode, starting B1 active-receipt digest, retained-chain digest, platform-
profile digest, and runtime-closure digest. Its common release-manifest digest
transitively binds both qualified host receipts without introducing a receipt-
authorization cycle. A `live-migration` authorization additionally binds the
completed rehearsal's endpoint-projection digest, transaction digest, exact
retained terminal-result digest, and exact resulting TW4 active-receipt digest. B1
rejects a missing, reused, wrong-class, wrong-purpose, or disagreeing
authorization before stage creation. A merely current-schema-compatible
candidate is never sufficient.

Tests may construct canonical, explicitly test-owned manifest and transition-
authorization fixtures to exercise this closed parser and transaction program
before release evidence exists. Their raw fixture bytes and claimed authority
are never promoted into a host receipt or detached release manifest and cannot
authorize a live installation. A host receipt does record the ordinary bounded
test result for each migration vertical; that result qualifies the frozen
program and crash matrix, not an exact-final deployment transaction. After the
real B1 identity, final TW4 identity, host receipts, canonical review evidence,
and detached release manifest have all frozen, the external deployer first
issues one exact-final rehearsal authorization for an isolated endpoint. Only
after validating its exact successful retained result and resulting active
receipt does it issue the distinct live authorization that binds that completed
rehearsal. The exact-final rehearsal is a post-qualification deployment gate;
it is not retroactively inserted into the already validated release manifest.
This separates executable TDD, release qualification, and live deployment
authority without creating a second issuer or trust root.

Staging descriptor-reads and copies the exact canonical manifest and transition
authorization into private, create-new stage files named
`bridge-transition-manifest.json` and
`bridge-transition-authorization.json`. The plan, authorization facts, stage
receipt, activation intent, and journal use an acyclic binding order: the plan
and authorization facts bind the manifest digest, strict endpoint projection,
and expected active-receipt core; the external transition authorization binds
those exact facts, plan, and transaction; then the stage receipt, activation
intent, and journal bind the authorization's raw digest as well. The final
active receipt joins the unchanged unsigned core with the exact class-shaped
migration projection, including the authorization digest, and only then
computes the ordinary `content_sha256` over every unsigned final field. No
preauthorization fact binds the final content or receipt digest. Activation
reopens and revalidates those exact staged bytes before journal creation.

Recovery reads only the staged copies. It accepts the authorization's same
transaction digest as continuation of that exact in-progress journal; this is
not a second use. The same authorization with another transaction, plan, stage,
candidate, or active B1 identity is reuse and rejects before mutation. A
successful TW4 deployment receipt retains a closed migration projection with
the F5/B1 edge, manifest digest, authorization digest,
`expected_active_receipt_core_sha256`, purpose, execution class, transaction,
and exact endpoint identities. The retained core digest must equal the core
recomputed from the final receipt after removing `migration` and
`content_sha256`. For `live-migration`, the projection also retains the exact
rehearsal endpoint-projection, transaction, terminal-result, and resulting-
active-receipt digests from the authorization. For `isolated-rehearsal`, those
prior-rehearsal fields are forbidden and its exact endpoint projection is
required. Later controller and client validation requires the exact class-
shaped retained projection and core equality without reopening the external
stage, manifest, host receipts, or rehearsal root.

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

### Public bridge-transition seam

B1 adds one closed public carrier without changing ordinary deployment APIs:

```python
@dataclass(frozen=True)
class BridgeTransitionRequest:
    deployment: DeploymentRequest
    release_manifest_path: Path
    endpoint_projection_raw: bytes
    execution_class: str
```

`execution_class` is exactly `isolated-rehearsal` or `live-migration`.
`release_manifest_path` is an absolute regular non-symlink path outside both
Git trees. `endpoint_projection_raw` is exact canonical JSON for the
class-specific endpoint projection. B1 exposes these functions:

```python
def prepare_bridge_transition(
    request: BridgeTransitionRequest,
    staging_root: Path,
) -> PreparedBridgeTransition: ...

def stage_bridge_transition(
    request: BridgeTransitionRequest,
    deployment_authorization_raw: bytes,
    transition_authorization_path: Path,
    staging_root: Path,
) -> StagedDeployment: ...
```

`PreparedBridgeTransition` contains the ordinary routine or complete-control
plan, ordinary `DeploymentAuthorizationFacts`, and these closed facts:

```python
@dataclass(frozen=True)
class BridgeTransitionAuthorizationFacts:
    canonical_root: Path
    staging_root: Path
    effective_uid: int
    plan_sha256: str
    deployment_authorization_facts_sha256: str
    maintenance_transaction_sha256: str
    expected_active_receipt_core_sha256: str
    active_endpoint_sha256: str
    candidate_endpoint_sha256: str
    release_manifest_sha256: str
    endpoint_projection_sha256: str
    execution_class: str

@dataclass(frozen=True)
class PreparedBridgeTransition:
    plan: RoutineDeploymentPlan | ControlSetDeploymentPlan
    authorization_facts: DeploymentAuthorizationFacts
    transition_authorization_facts: BridgeTransitionAuthorizationFacts
```

The active and candidate endpoint digests cover every exact identity named by
the transition-authorization contract. The expected active-receipt core is the
canonical exact unsigned receipt projection with both `migration` and
`content_sha256` omitted. Controller and client later remove those two fields
from the final receipt and recompute that core, validate the authorization-
backed migration projection independently, and validate the ordinary final
`content_sha256` over the complete unsigned receipt. Bridge preparation
normalizes and validates the prospective absolute `staging_root` without
creating it, derives the exact rollback receipt and every other stage-root-
dependent active-receipt field, and binds both the root and resulting core in
the returned facts. It descriptor-reads the manifest once but does not accept
transition authorization. Staging requires its `staging_root` argument to
equal the authorization-bound preparation root, reparses the ordinary deployer
authorization, descriptor-reads and validates both external files, requires
the manifest digest and active-receipt core observed during preparation, and
copies their exact bytes create-new into the private stage.

`ActivationRequest.deployment` admits `BridgeTransitionRequest`; its
`authorization_raw` remains the ordinary deployer authorization. Activation,
recovery, and result reconciliation read the manifest and transition
authorization only from the verified stage. They never reread either external
path. `ActivationRequest`, `RecoveryRequest`, and
`ResultReconciliationRequest` keep their existing public names and shapes.
No bridge field is added to `DeploymentRequest`, and `stage_deployment` never
accepts bridge-transition authority.

The inbound recovery adapter is named `Freeze5RecoveryRequest`. It exists only
inside exact B1 recovery dispatch and carries F5's primitive
`source_selection_raw`, `manager_binding_raw`, `manager_receipt_raw`,
`runtime_qualification_raw`, transaction digest, and expected active-receipt
digest. It is not a public installation request or a legacy-schema registry.

## Migration Protocol

### Hop 1: F5 to B1

1. Exact F5 read-only prepares B1 under F5-compatible policy and receipt
   schemas. Because F5 is immutable and predates B1's frozen digest, this step
   proves compatibility, not exact-successor exclusivity.
2. The existing external deployer compares the prepared plan, every planned
   artifact digest, source and policy projections, and authorization facts to
   the frozen exact B1 identity. It issues ordinary
   `task-witness-deployer-authorization-v1` bytes only on exact equality. F5
   reparses those bytes against the same plan and facts before creating the
   stage. A modified, reissued, or wrong-predecessor candidate receives no
   authorization and cannot create a stage.
3. F5 executes the existing complete-control replacement program.
4. Every process-loss cut recovers through a freshly loaded exact staged F5
   controller. The live B1 controller may dispatch recovery, but it rebuilds
   the exact F5-module request from primitive legacy fields and delegates the
   bound old-schema stage to F5.
5. Acceptance leaves an exact old-schema B1 active receipt and a complete F5
   rollback preimage. Rejection restores exact F5 and removes only
   transaction-owned B1 authority.

### Hop 2: B1 to TW4

1. Exact B1 prepares the exact TW4 and one prospective private stage root
   through its current-shaped outbound surface, validating the detached release
   manifest and strict endpoint projection and returning the plan and
   authorization facts. The external deployer then issues the fresh bridge-
   transition authorization, which B1 validates for that same root before
   creating the stage.
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
- an unlisted predecessor requesting ordinary deployer authorization for B1;
- a modified or reissued bridge requesting ordinary F5 authorization;
- B1 preparing an unsupported legacy, publisher-channel, or exact-release
  migration through its inbound surface;
- B1 preparing any current-schema candidate without the exact detached release
  manifest or strict endpoint projection;
- B1 staging or activating any current-schema candidate without the fresh
  one-use bridge-transition authorization issued from that exact plan and
  authorization facts;
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
- one exact selector into a reviewed candidate-owned suite driver, with no
  inventory-selected script, module, executable, or shell interpolation;
- target applicability from the exact set `macos-arm64` and `linux-x86_64`;
- phase, expected terminal, and exact expected test or scenario count; and
- ordered aggregate entry and count identities.

The ordered suite IDs are fixed as follows; selectors and exact expected
counts freeze with the implementation bytes:

1. `client-common`
2. `deployment-common`
3. `package-contract`
4. `qualification-runner-contract`
5. `task-witness-source-stage`
6. `public-release-source-stage`
7. `forward-update`
8. `authorized-downgrade-and-manual-rollback`
9. `candidate-rejection-rollback`
10. `candidate-source-disappearance`
11. `provider-cache-deletion-and-movement`
12. `literal-rendered-shim`
13. `migration-freeze5-to-bridge`
14. `migration-bridge-to-tw4`
15. `macos-acl`
16. `linux-process-supervision`

The root has exactly `schema_version`, `contract`, `entries`, and `aggregates`.
`schema_version` is integer `1`; `contract` is
`task-witness-tw4-suite-inventory-v1`; `entries` is the exact 16-element
ordered list; and `aggregates` has exactly `counts_sha256`, `entries_sha256`,
`entry_count`, and `expected_count_total`. `entry_count` is integer `16`.
The runner exposes the pure public seam
`parse_suite_inventory(value: object) -> dict[str, Any]` beside its existing
platform-profile and runtime-closure parsers. Adding this parser does not make
receipt publication reachable; `main()` stays at the explicit unsettled
suite/receipt failure until every entry selector, count, and vertical freezes.

Each entry has exactly these fields:

```json
{
  "argv": [
    "-I",
    "-B",
    "scripts/run_task_witness_qualification_suite.py",
    "--suite",
    "client-common"
  ],
  "executor": {"kind": "qualified-cpython"},
  "expected_count": 1,
  "expected_terminal": "passed",
  "id": "client-common",
  "phase": "common",
  "targets": ["macos-arm64", "linux-x86_64"]
}
```

Every executor object is exactly:

```json
{"kind": "qualified-cpython"}
```

It forbids every extra field. For entry `ID`, `argv` is exactly
`["-I", "-B", "scripts/run_task_witness_qualification_suite.py",
"--suite", ID]`. The ID must equal the entry's `id`. No other token, option,
path, module, environment assignment, or argument is representable. The runner
also owns the complete ordered ID-to-`argv`, phase, and target projection and
rejects disagreement rather than trusting the inventory to define it.

The candidate-owned `scripts/run_task_witness_qualification_suite.py` is part
of the reviewed and source-shaped closure. Its CLI accepts exactly `--suite`
plus one fixed ID and dispatches through one closed in-code ID table. The table
owns each exact test selector, validator, migration scenario, platform
vertical, and expected result-counting adapter. It exposes no generic path,
module, command, tool, or environment input. It invokes Python-backed work
through its already-qualified `sys.executable` without a shell. The
`literal-rendered-shim` branch alone directly executes the exact candidate
rendered shim in the driver-owned fixed install scenario; the inventory cannot
name or alter that path. The platform profile still records
`environment-clearer`, `git`, and `posix-shell` as outer-runner qualification
evidence, but no suite entry or driver selector can dispatch through them.

On success, the suite driver writes exactly one canonical JSON object to
stdout, no trailing newline, and nothing to stderr. The object has exactly:

```json
{
  "contract": "task-witness-tw4-suite-result-v1",
  "detail_stderr_length": 0,
  "detail_stderr_sha256": "<sha256>",
  "detail_stdout_length": 0,
  "detail_stdout_sha256": "<sha256>",
  "id": "client-common",
  "observed_count": 1,
  "schema_version": 1,
  "terminal": "passed"
}
```

The driver captures underlying stdout and stderr within fixed byte bounds and
binds their lengths and digests in this result. It emits no successful result
and exits nonzero if dispatch, execution, counting, output bounds, or terminal
validation fails. The outer runner parses the canonical result, requires its
ID and `observed_count` to match the inventory entry and `expected_count`, and
records both the result projection and the driver's raw exit/stdout/stderr
identity in the host receipt.

`expected_count` is a positive integer. The exact phase vocabulary is
`common`, `portable-vertical`, and `platform-vertical`; entries 1–6, 7–14, and
15–16 respectively use those values. The only terminal value in v1 is
`passed`. A target list is a nonempty unique subsequence of the fixed order
`macos-arm64`, `linux-x86_64`; entries 1–14 select both, entry 15 selects only
macOS, and entry 16 selects only Linux.

`entries_sha256` is SHA-256 of canonical JSON bytes for the exact ordered
`entries` list. `counts_sha256` is SHA-256 of canonical JSON bytes for the
ordered list of objects `{"expected_count": N, "id": ID}` projected from those
entries. `entry_count` equals the list length, and `expected_count_total` is the
exact sum of all entry counts. Canonical JSON uses UTF-8, sorted object keys,
no insignificant whitespace, no duplicate keys, and no nonfinite number. The
candidate file ends with no newline because the canonical byte encoder does
not emit one.

The executor selector is closed to the qualified CPython executable and the
single reviewed suite driver. Entries cannot choose an environment, working
directory, credential, network authority, system-tool dispatcher, shell,
script, module, command, or unregistered executable.

The inventory contains the complete common process, envelope, drift,
activation, deployment, recovery, rollback, package, and release-validation
suites. It also contains named verticals for forward update, authorized
downgrade/manual rollback, candidate-rejection rollback, candidate-source
disappearance, provider-cache deletion or movement, literal rendered-shim
execution, and the two migration hops. The same complete applicable set runs
on both required targets.

The inventory owns no host observation, runtime provenance, reviewer result,
release disposition, or final candidate identity. This avoids a digest cycle.

The suite driver's `qualification-runner-contract` branch names an explicit
nonrecursive parser/preflight unit-test set. It must not select a test that
invokes a successful qualification runner, directly or through discovery.
End-to-end runner success is exercised only by the outer native-host
qualification and is not recursively selected from inside its own inventory.

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
- each host receipt's exact successful `migration-freeze5-to-bridge` and
  `migration-bridge-to-tw4` entry result, including its expected and observed
  count, exit status, bounded stdout digest and length, bounded stderr digest
  and length, and terminal result; these are migration-program qualification
  results, not exact-final rehearsal authority;
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
- add the exact Task Witness marketplace route.

The v1 promotion delta has exactly those two changed paths:
`release/task-witness/public-release-registration.json` and
`.claude-plugin/marketplace.json`. No generated release record is rewritten or
added. The candidate's source-shape record remains byte-identical; final mode
first validates that candidate record against the candidate tree, then applies
the closed two-path projection and rejects every other final-tree difference.

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
semantic bytes, stale manifest, or a manifest inside the candidate tree.

Separately, exact B1 rejects a stale or disagreeing transition authorization
before staging or recovery mutation. TW4 controller and client reject a stale,
unknown, or inconsistent retained migration projection. Neither operation is a
final-release-validator responsibility.

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
3. Build B1's current outbound stage and prove its parser, stage, state-program,
   and recovery behavior with canonical test-owned manifest and transition-
   authorization fixtures. These are TDD inputs, not release evidence.
4. Freeze B1's complete bytes, record its exact immutable identities, replace
   identity-shape fixtures with exact-value regressions.
5. Implement the TW4 candidate's exact B1/F5 retained allowlist, fixed suite-
   inventory parser, single reviewed suite driver, closed ID-to-selector table,
   suite-result parser, host-receipt parser, detached-manifest validator, and
   metadata-only promotion validator. Keep receipt emission deliberately
   unreachable at this step.
6. Add exact retained F5/B1/TW4 controller and client validation plus K.1
   coexistence. Every recovery audit must understand the policy epoch and
   receipt schema for the retained suffix before mutation.
7. Run both hops' full crash and recovery matrices with test-owned fixture
   authority, then prove manual rollback across the retained chain as a focused
   successor slice. The tests exercise the production contract but make no
   release claim.
8. Freeze the suite-inventory parser, exact ordered IDs, suite-driver bytes,
   closed ID-to-selector table, suite-result parser, exact counts, and every
   required vertical before enabling the runner terminal and create-new
   receipt publication.
9. Rebaseline source-shape and package inventories, then freeze the complete
   ineligible TW4 qualification candidate. No candidate byte changes after
   this point without invalidating every dependent artifact.
10. Emit and independently verify one create-new receipt on each native host.
11. Run the bootstrap review, install the canonical Rolecasting/Tricritical
   issuer chain, and rerun the required fresh unchanged-candidate review.
12. Construct the deterministic metadata-only final successor and create and
   validate the real detached release manifest from the two host receipts and
   canonical review. Revalidate stale-candidate, evidence, promotion-delta,
   final-identity, plan, transaction, and endpoint rejection.
13. In the bound isolated deployment root, complete `F5 -> B1`, select the
   prospective private stage root, prepare TW4 through active B1, and obtain
   that endpoint's exact plan and authorization facts. The external deployer
   then issues one transaction-bound
   `isolated-rehearsal` authorization from those facts; stage and activate
   `B1 -> TW4`; and validate its retained terminal result, resulting TW4 active
   receipt, endpoint profile, and unchanged release identities. Next validate
   public-release promotion. In the live deployment root, independently
   complete `F5 -> B1`, select its separate prospective private stage root,
   prepare TW4 through active B1, and obtain the live plan and facts. The
   external deployer then issues the distinct transaction-bound
   `live-migration` authorization from those facts and the completed rehearsal
   evidence; stage and activate `B1 -> TW4` through the exact staged B1
   controller.

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
