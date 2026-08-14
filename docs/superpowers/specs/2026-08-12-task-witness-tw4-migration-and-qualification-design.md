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
2. Require a product-attested ChatGPT Codex child running Sol High for the
   independent acceptance review. Rolecasting preserves separately attested
   target, model, topology, authority, and execution-result facts; Tricritical
   owns review completeness, independent adjudication, and terminal
   verification. No current Rolecasting producer or issuer can yet make this
   publication-grade claim, so release remains fail-closed.
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

The qualification program has three immutable control-set identities. No
Task Witness installation exists yet, so these are release and recovery test
artifacts rather than a migration of an operator's machine:

- **F5**: exact Freeze 5, the frozen internal predecessor used to prove the
  first recovery boundary.
- **B1**: the hidden one-purpose immutable migration bridge. It is a complete
  artifact for qualification but is never a marketplace or terminal release.
- **TW4**: the current-schema target and the only one of the three intended for
  the first public installation.

F5 is version `0.1.0` and is fixed at commit
`96608a9b91d4dcf3f468a4fab1f0e008c9c32b36`, controller
`8dc51b2a644e30d1f7c4f3b71711698b4130b43f1517e9f5361c6d1a0f7d6cfe`,
policy `23e84f210ba69ef79e02bfc3039b2c8be3b91153d7649009b3a22850f5086245`,
and client `778186f6a460655a8b390c831e05c233171236898663ad4155bd45695597c6cf`.
B1 is version `0.1.1`. TW4 is version `1.0.0`, the first public Task Witness
release. B1 and TW4 receive exact byte identities only when their
implementation bytes freeze; no mutable branch or release label can substitute
for them.

The only authorized, stageable edges are `F5 -> B1` and `B1 -> TW4`.
`F5 -> TW4` rejects during preparation because exact F5 cannot parse TW4's
root-Agent-Plugins source shape and cannot parse its current 27-contract
candidate policy. No other predecessor may use B1's
migration surface. Proving both authorized edges is release-qualification
evidence; it does not imply that either F5 or B1 was installed by the operator.

### Receipt epochs

F5 and B1 retain deployment-receipt schema integer `1`, contract
`task-witness-deployment-receipt-v1`, the flattened harness-snapshot source
projection, and exact Claude plus Codex manifest digests. TW4 uses deployment
receipt schema integer `2`, contract `task-witness-deployment-receipt-v2`, and
the current source projection with exact `agent_plugin_manifest_sha256` and
`claude_manifest_sha256`. A current candidate must contain a root Agent Plugins
v1 `plugin.json`, its exact `.claude-plugin/plugin.json` projection, and no
current `.codex-plugin/plugin.json`.

An ordinary TW4 receipt has no `migration` field. The receipt produced by the
exact `B1 -> TW4` edge additionally contains the closed bridge-migration
projection defined below. Its retained history is exactly current v2 TW4,
legacy v1 B1, then legacy v1 F5. The TW4 controller and client may select the
legacy parser only while traversing backward from that independently validated
current bridge-migration projection and only for the exact frozen B1/F5
allowlist. A top-level legacy receipt, an arbitrary v1 predecessor, or a
key-sniffed schema switch fails closed. Manual rollback across v2 and v1
receipt contracts rejects before staging or live mutation.

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

B1's tree construction is closed. Its policy is the exact F5 policy, preserving
the installed 26-contract epoch. Its client is a distinct transition-only B1
client: the inbound surface accepts only the exact F5/B1 v1 state and ordinary
F5-authored activation journals, while the outbound rollback surface accepts
only an exact class-shaped `B1 -> TW4` bridge journal, verified stage and
transition evidence, and the bound v2-candidate/v1-B1/v1-F5 receipt chain. The
outbound surface validates the restored B1 v1 active unit without rewriting or
projecting the bridge journal. It never accepts a generic v1 receipt or selects
a parser by optional-key sniffing. Its controller starts from the current
SourceEvidence-capable controller and adds only the closed F5 inbound,
bridge-transition, and retained-history behavior in this design. F5 and B1
retain identity-specific Claude and Codex plugin manifests because Task
Witness binds each release version to one immutable subtree. TW4 instead has
an identity-specific root Agent Plugins manifest and exact Claude projection.
F5, B1, and TW4 therefore carry the distinct manifest versions `0.1.0`,
`0.1.1`, and `1.0.0`, respectively.

These seven paths remain byte-identical across F5, B1, and the current
candidate:

- `client/task_witness_shim.sh.in`;
- `launcher/task_witness_launch.py`;
- `runtime/bundle_io.py`;
- `runtime/canonical.py`;
- `runtime/task_witness.py`;
- `runtime/trust.py`; and
- `smoke/task_witness_smoke_validator.py`.

No generic 26/27-contract union policy or permissive dual-schema parser is
introduced. B1's controller and transition-only client treat its installed
policy and receipt as the exact F5 epoch while separately validating the
current 27-contract TW4 candidate epoch. TW4's controller and client retain
only the closed historical v1 path described in Receipt epochs; they do not
admit v1 as an ordinary current receipt profile.

The first bridge supports the proven `harness_snapshot` source mode. Publisher
channel and exact-release migrations require an independently designed bridge;
they do not fall back to harness sentinels.

B1 is transition-only. It remains absent from ordinary marketplace discovery
and cannot serve as the terminal production release.

Current TW4 retains an exact, closed B1/F5 allowlist for historical validation.
The allowlist admits only the frozen identities. A well-formed but unlisted
legacy receipt, bridge, controller, or policy fails closed.

The TW4 candidate owns the single reviewable allowlist source at
`release/task-witness/tw4-bridge-identity.json`, with contract
`task-witness-tw4-bridge-identity-v1`. Its root has exactly
`schema_version`, `contract`, `freeze5`, `bridge`, `allowed_edges`,
`provenance_sha256`, and `content_sha256`. `schema_version` is integer `1`;
`provenance_sha256` is SHA-256 of the exact raw provenance-proof file defined
below; and `content_sha256` is the digest of the canonical object with that
field omitted. Each identity object
has exactly `repository_id`, `commit_sha1`, `tree_sha1`,
`plugin_subtree_sha256`, `controller_sha256`, `policy_sha256`,
`client_sha256`, and `source_mode`. `repository_id` is exactly
`nisavid/agents`; `source_mode` is exactly `harness_snapshot`; commit and tree
values are 40-character lowercase Git object IDs; and every SHA-256 is 64
lowercase hex characters. `plugin_subtree_sha256` uses the
`task-witness-plugin-subtree-v1` framing: SHA-256 of canonical JSON for an
object containing exactly `contract` and `entries`, with `contract` equal to
`task-witness-plugin-subtree-v1`. `entries` contains every descendant of the
plugin root exactly once, excludes the root itself, and is strictly sorted by
`path`. A directory entry contains exactly
`{"kind":"directory","path":RELATIVE}`. A file entry contains exactly
`{"kind":"file","length":N,"path":RELATIVE,"sha256":DIGEST}`, where `N`
is the nonnegative non-Boolean raw byte length and `DIGEST` is SHA-256 of those
exact raw bytes. `RELATIVE` is a nonempty slash-separated plugin-root-relative
path with no empty, `.` or `..` component. Symlinks and special files are
forbidden. Canonical JSON uses UTF-8, sorted object keys, no insignificant
whitespace, no trailing newline, no duplicate keys, and no nonfinite value.
Controller, policy, and client digests are SHA-256 of their exact raw file
bytes. `allowed_edges` is exactly
`[{"from":"freeze5","source_mode":"harness_snapshot","to":"bridge"}]`.
Exact identity values freeze only after B1 freezes. The record contains no TW4
commit, tree, or artifact digest and therefore creates no self-reference.

The immutable TW4 qualification candidate also owns the exact historical bytes
needed to execute both migration verticals. They appear at these fixed paths:

- `release/task-witness/migration/freeze5/controller/task_witness_deploy.py`;
- `release/task-witness/migration/freeze5/controller/policy.json`;
- `release/task-witness/migration/freeze5/client/task_witness_client.py`;
- `release/task-witness/migration/freeze5/.claude-plugin/plugin.json`;
- `release/task-witness/migration/freeze5/.codex-plugin/plugin.json`;
- `release/task-witness/migration/bridge/controller/task_witness_deploy.py`;
- `release/task-witness/migration/bridge/client/task_witness_client.py`;
- `release/task-witness/migration/bridge/.claude-plugin/plugin.json`; and
- `release/task-witness/migration/bridge/.codex-plugin/plugin.json`.

The F5 snapshot files are exact raw copies of the frozen F5 controller, policy,
client, and both plugin manifests. The bridge snapshots are the exact frozen B1
controller, transition-only client, and both plugin manifests; B1 reuses only
the F5 policy. The candidate's source-shape record, package closure, suite
driver, both host
receipts, canonical review, and detached release manifest bind all nine raw
snapshot files. The bridge snapshots are added only after B1 freezes and before
the TW4 qualification candidate freezes.

B1 is an untagged, non-marketplace side commit carried durably by the
candidate-owned provenance proof; no remote ref or release object is required.
Its raw commit remains public review evidence, but it is not a terminal release
or an ancestor requirement for the TW4 branch. This keeps its identity
independent of later rebase or merge strategy.

The B1 Git tree starts from the complete F5 tree and changes exactly four
paths: the B1 controller at mode `100755`, the transition-only B1 client at
mode `100755`, and the legacy Claude and Codex manifests at mode `100644` with
only their version changed to `0.1.1`. The F5 policy and the seven stable
package paths remain byte- and mode-identical. B1 contains no root
`plugin.json` and no TW4 identity or provenance record.

The B1 commit payload is exact bytes with these headers in this order and no
additional header:

```text
tree TREE_SHA1
parent 96608a9b91d4dcf3f468a4fab1f0e008c9c32b36
author Ivan D Vasin <ivan@nisavid.io> 1786517677 -0400
committer Ivan D Vasin <ivan@nisavid.io> 1786517677 -0400

feat(task-witness/b1): freeze transition bridge
```

The payload ends with the shown single LF. `TREE_SHA1` is the lowercase SHA-1
of the exact B1 root tree constructed above. The one-second successor to F5's
author and committer timestamp provides deterministic monotonic metadata
without pretending B1 has an independent publication time. The commit has
exactly one parent and forbids `encoding`, `gpgsig`, `mergetag`, or any other
header. Construction uses a private temporary index or equivalent exact tree
builder, and validation requires the real worktree and index to remain
byte-for-byte unchanged. A temporary private ref may protect the object only
while the proof is assembled; it is removed after the candidate-owned raw
commit and tree proof is written and verified.

The same candidate owns the canonical provenance proof at
`release/task-witness/tw4-bridge-provenance.json`, with contract
`task-witness-tw4-bridge-provenance-v1`. Its root has exactly
`schema_version`, `contract`, `repository_id`, `freeze5`, `bridge`, `objects`,
and `content_sha256`. `schema_version` is integer `1`; `repository_id` is
exactly `nisavid/agents`; `freeze5` and `bridge` each contain exactly
`commit_sha1` and `tree_sha1` and equal the corresponding identity-record
values; and `content_sha256` hashes the canonical object with that field
omitted. `objects` is a strictly SHA-1-sorted, unique list of exact objects
`{"type":TYPE,"sha1":SHA1,"raw_base64":BASE64}`. `TYPE` is exactly `commit`
or `tree`; `BASE64` is canonical padded RFC 4648 base64 without whitespace;
and `SHA1` equals SHA-1 of ASCII `TYPE`, one space, the decimal decoded byte
length, one NUL byte, and the decoded raw payload.

The proof contains exactly the two named commit objects and the minimal union
of tree objects needed to walk each commit's root through
`plugins/task-witness` and recursively enumerate every directory below that
plugin root. It contains no blob or unrelated tree object. A commit payload has
exactly one `tree` header naming its recorded root tree. The bridge commit has
exactly one `parent` header naming the exact F5 commit. A tree payload is the
unambiguous repetition of ASCII octal mode, one space, a nonempty basename,
one NUL byte, and a 20-byte object ID, in Git tree order. Each traversed
directory uses mode `40000`; every plugin file uses `100644` or `100755`;
symlink, submodule, duplicate, slash-containing, dot, and dot-dot names reject.

For every plugin file entry, the validator selects the one candidate-bound raw
file from the seven stable paths and the identity-specific role snapshots,
computes SHA-1 over ASCII `blob`, one space, its decimal byte length, one NUL,
and the exact bytes, and requires that object ID and file mode to match the tree
entry. It then recomputes the path/kind/length/SHA-256 subtree projection and
requires the identity record's `plugin_subtree_sha256`. This proves each
recorded commit-to-root-tree-to-plugin-tree-to-file binding without an ambient
object database. The source-stage validator, suite driver, native runner, and
final-release validator independently parse the same proof and require its raw
SHA-256 to equal the identity record. The proof contains no TW4 identity or
identity-record digest and introduces no cycle.

The suite driver reconstructs a historical plugin root only inside its private
temporary directory. It copies the seven byte-stable paths above from the
unchanged current candidate, then overlays the five identity-specific role
files selected by the fixed identity: the F5 controller, policy, client, and
two plugin manifests for the F5 root; or the B1 controller, transition-only
client, and two plugin manifests plus the exact F5 policy for the B1 root. It
admits no other input path and follows no symlink. Before executing either root,
it uses the same
bounded candidate-tree projection to require the exact
`plugin_subtree_sha256`, controller, policy, and client digests from the bridge
identity record. Qualification therefore needs no ambient Git object, network
fetch, branch, tag, archive, or unreviewed historical checkout. Each native
host validates the candidate-bound provenance proof before executing only the
exact reconstructed bytes.

The external deployer and final validator consume that canonical record from
the immutable TW4 candidate. Generated controller and client constants must
exactly reproduce its F5/B1 identity projections and raw record digest during
source-stage and package validation. Runtime controller and client validation
uses only those compiled constants; it never reopens a checkout or record. The
record is not B1 authority, cannot authorize either migration edge, and is not
review evidence. The detached release manifest and final TW4 receipt migration
projection both bind its raw digest.

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
source-evidence, control-set, and public-release identities; the exact bridge-
identity-record and validated release-manifest digests; the deployment plan,
expected ordinary deployment-authorization, and active-receipt core digests;
the execution class; and the one-use maintenance-transaction digest. The core
is the exact
deterministic unsigned active-receipt projection with both the new `migration`
projection and the final `content_sha256` omitted. An
`isolated-rehearsal` authorization additionally binds a canonical endpoint
projection containing the target, deployment-root path, device, inode, owner,
mode, starting B1 active-receipt digest, exact ordered retained-receipt and
retained-result digest lists, platform-profile digest, and runtime-closure
digest. Its common release-manifest digest
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

Those two files are stage-only evidence, never `StagedArtifact` values and
never candidates for installation under the canonical root. Bridge stage JSON
adds exactly one `transition_evidence` object containing exactly `manifest` and
`authorization`. Each value is an exact staged-file binding with
`relative_path`, absolute `path`, `length`, `sha256`, current owner, and integer
mode `384` (`0o600`). The relative paths are exactly the two names above. A
bridge stage
omitting, adding, swapping, or aliasing either binding rejects; a non-bridge
stage containing `transition_evidence` rejects.

`StagedDeployment` and `VerifiedDeploymentStage` expose the two values as an
ordered tuple of frozen `StagedTransitionEvidence` objects. Independent stage
verification descriptor-opens each file beneath the private stage root,
requires a regular non-symlink current-owner single-link `0600` file with exact
bytes, rechecks its identity, and includes it in the closed private-stage
inventory. It does not synthesize an `installed` binding. Activation, recovery,
and reconciliation accept transition evidence only from this verified tuple.

Recovery reads only the staged copies. It accepts the authorization's same
transaction digest as continuation of that exact in-progress journal; this is
not a second use. The same authorization with another transaction, plan, stage,
candidate, or active B1 identity is reuse and rejects before mutation. A
successful TW4 deployment receipt retains a closed migration projection with
the F5/B1 edge, `bridge_identity_sha256`, manifest digest, authorization digest,
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
  26-contract control inventory. B1's client validates this surface exactly as
  F5 does; its distinct file identity does not widen the accepted documents.
- Its **outbound TW4 surface** understands the current `SourceEvidence`,
  authorization, receipt, stage, and recovery contracts. B1 can install TW4
  and can serve as TW4's staged prior controller during recovery. This surface
  validates TW4's 27-contract inventory, including `source_evidence`. If
  candidate smoke rejects, the restored B1 client validates rollback smoke
  from the same unmodified bridge journal and transaction ID; it admits that
  surface only after independently binding the bridge stage, transition
  evidence, rejected v2 candidate, and exact retained v1 history.

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
`release_manifest_path` is an absolute regular non-symlink path disjoint from
the candidate root, canonical deployment root, and prospective staging root.
B1 can enforce those runtime boundaries; final-release validation separately
proves the detached manifest is outside both immutable Git trees.
`endpoint_projection_raw` is exact canonical JSON for the class-specific
endpoint projection. B1 exposes these functions:

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
    maintenance_transaction_sha256: str
    expected_deployment_authorization_sha256: str
    expected_active_receipt_core_sha256: str
    bridge_identity_sha256: str
    release_manifest_sha256: str
    endpoint_projection_sha256: str
    execution_class: str

@dataclass(frozen=True)
class PreparedBridgeTransition:
    plan: RoutineDeploymentPlan | ControlSetDeploymentPlan
    authorization_facts: DeploymentAuthorizationFacts
    transition_authorization_facts: BridgeTransitionAuthorizationFacts

@dataclass(frozen=True)
class StagedTransitionEvidence:
    role: str
    relative_path: str
    staged_path: Path
    raw: bytes
    staged: Mapping[str, Any]
```

For bridge stages only, both `StagedDeployment` and `VerifiedDeploymentStage`
add `transition_evidence: tuple[StagedTransitionEvidence, ...]`; its exact role
order is `manifest`, `authorization`. Ordinary values expose the empty tuple.

The ordinary deployment plan and authorization bind the active and candidate
deployment identities. The expected active-receipt core is the canonical exact
unsigned receipt projection with both `migration` and
`content_sha256` omitted. Controller and client later remove those two fields
from the final receipt and recompute that core, validate the authorization-
backed migration projection independently, and validate the ordinary final
`content_sha256` over the complete unsigned receipt. Bridge preparation
normalizes and validates the prospective absolute `staging_root` without
creating it, derives the exact rollback receipt and every other stage-root-
dependent active-receipt field, and binds both the root and resulting core in
the returned facts. The ordinary authorization purpose is selected exactly as
follows. An approval-required source transition whose reason is `downgrade`,
`exact-release-pin`, or `source-authority` uses `source-boundary-change`, even
when the plan is complete-control. Otherwise a `ControlSetDeploymentPlan` uses
`complete-control-set-maintenance`, and a `RoutineDeploymentPlan` uses
`routine-compatible-forward`. No other plan, outcome, reason, or purpose is
admitted. The ordinary authorization document is fully deterministic from
`DeploymentAuthorizationFacts` and that selected purpose: preparation
canonically renders that one expected document in memory, records only its raw
SHA-256, and neither returns nor writes the bytes. This prediction does not
authorize anything. Staging recomputes the same purpose from the verified plan
and classification rather than trusting either authorization to choose it. The
predicted raw and content digests supply the ordinary `authorization` binding
inside the expected active-receipt core. Preparation
descriptor-reads the manifest once but does not accept transition
authorization. Staging requires its `staging_root` argument to
equal the authorization-bound preparation root, strictly parses the externally
supplied ordinary deployer authorization, requires its raw bytes and SHA-256 to
equal the predicted document, descriptor-reads and validates both external
files, requires the manifest digest and active-receipt core observed during
preparation, and copies their exact bytes create-new into the private stage.

`ActivationRequest.deployment` admits `BridgeTransitionRequest`; its
`authorization_raw` remains the ordinary deployer authorization. Activation,
recovery, and result reconciliation read the manifest and transition
authorization only from the verified stage. They never reread either external
path. `ActivationRequest`, `RecoveryRequest`, and
`ResultReconciliationRequest` keep their existing public names and shapes.
No bridge field is added to `DeploymentRequest`, and `stage_deployment` never
accepts bridge-transition authority.

### Closed transition documents

All documents in this section use canonical JSON and reject every omitted,
extra, duplicate, mistyped, noncanonical, or nonfinite value. Every digest is
64 lowercase hex; every UID, device, inode, mode, sequence, and length is a
nonnegative non-Boolean integer.

The endpoint document has contract
`task-witness-bridge-endpoint-projection-v1` and exactly
`schema_version`, `contract`, `execution_class`, `target`, `deployment_root`,
`device`, `inode`, `owner`, `mode`, `starting_active_receipt_sha256`,
`retained_receipts`, `retained_results`, `platform_profile_sha256`,
`runtime_closure_sha256`, and `content_sha256`. `schema_version` is `1`;
`execution_class` matches the request; `target` is exactly `macos-arm64` or
`linux-x86_64` and matches the platform profile; `deployment_root` is the
canonical deployment root; and `mode` is integer `448` (`0o700`).
`retained_receipts` is the complete oldest-to-newest deployment-receipt chain
validated by the immutable B1 controller's strict retained-chain parser. It is
a nonempty list of exact objects `{"sequence":N,"sha256":DIGEST}` with first
sequence `1`, each next sequence exactly one greater, and last sequence equal
to the starting active receipt's sequence. The first receipt has no prior
digest; every later receipt's `prior_receipt_sha256` equals the preceding list
digest; and the last digest is the starting active receipt. The parser requires
exact raw bytes for every named deployment receipt and paired rollback receipt
under the private canonical-root-relative `receipts` directory, a directory
inventory of exactly twice the final sequence, unique receipt and rollback
digests, no cycles, and complete class-specific authorization, policy, source,
selector, control-preimage, runtime, trust-history, and manual-target ancestry
coherence at every edge. `DIGEST` is SHA-256 of the exact deployment-receipt
bytes. The endpoint projection is rejected unless a second strict parse yields
the identical complete list; no suffix or singleton substitution is admitted.
`retained_results` is the path-sorted list of exact objects
`{"path":RELATIVE,"sha256":DIGEST}` from the closed retained transaction-result
baseline. If the canonical-root-relative `transaction-results` directory is
absent, the list is empty. If present, it is a real current-owner integer-mode
`448` (`0o700`) directory with no permissive ACL and is nonempty. Every entry
is exactly `sha256-TRANSACTION.json`, where `TRANSACTION` is 64 lowercase hex,
and therefore has path `transaction-results/sha256-TRANSACTION.json`. It is a
current-owner regular non-symlink single-link integer-mode `384` (`0o600`) file
of at most 1,048,576 raw bytes. Its exact raw bytes must pass the immutable B1
controller's strict class-discriminated activation-journal parser. The parsed
journal's contract is `task-witness-activation-transaction-v1`; transaction
class is exactly `routine-payload`, `control-set-maintenance`, or
`manual-exact-target-rollback`; transaction ID equals `TRANSACTION`; canonical
root and effective UID equal the endpoint; phase is `terminal`; terminal result
is present; and outcome is not `recovery-required`. The parser also recomputes
the journal's ordinary `content_sha256`, activation-intent transaction ID,
class-specific inner bindings, phase coherence, and terminal-result coherence;
no common-envelope-only parse is sufficient. `DIGEST` is SHA-256 of the exact
raw journal bytes.
Temporary names, extra entries, empty published directories, aliases, and
invalid terminal journals reject. The inventory is captured twice with stable
directory and file identities before the endpoint projection is accepted.
`content_sha256` hashes the canonical object with that field omitted.

The externally issued transition authorization has contract
`task-witness-bridge-transition-authorization-v1`. Its common field set is
exactly `schema_version`, `contract`, `purpose`, `execution_class`,
`canonical_root`, `staging_root`, `effective_uid`, `plan_sha256`,
`maintenance_transaction_sha256`, `deployment_authorization_sha256`,
`expected_active_receipt_core_sha256`, `bridge_identity_sha256`,
`release_manifest_sha256`, `endpoint_projection_sha256`, and
`content_sha256`. The `isolated-rehearsal` variant contains exactly that set;
the `live-migration` variant contains exactly that set plus
`prior_rehearsal`. `schema_version` is integer `1`; `purpose` is exactly
`bridge-transition`; `execution_class` names the variant; `canonical_root` and
`staging_root` are normalized absolute path strings equal to preparation facts;
and `content_sha256` hashes the complete class-shaped object with that field
omitted. `maintenance_transaction_sha256` is the one-use pre-stage transaction
identity already supplied by `DeploymentRequest`; the later activation
transaction ID is derived normally from the verified stage and is not
preauthorized.

The live variant's `prior_rehearsal` is an exact
object containing `endpoint_projection_sha256`, `transaction_sha256`,
`terminal_result_sha256`, and `active_receipt_sha256` from the completed
isolated rehearsal. The isolated variant forbids that key. The
authorization's `deployment_authorization_sha256` must equal both the predicted
ordinary authorization digest in preparation facts and the exact raw ordinary
authorization supplied at staging.

The final deployment receipt's `migration` object has contract
`task-witness-bridge-migration-projection-v1`. Its common field set is exactly
`schema_version`, `contract`, `edge`, `purpose`, `execution_class`,
`maintenance_transaction_sha256`, `deployment_authorization_sha256`,
`transition_authorization_sha256`, `expected_active_receipt_core_sha256`,
`bridge_identity_sha256`, `release_manifest_sha256`, and
`endpoint_projection_sha256`. The isolated variant contains exactly that set;
the live variant contains exactly that set plus the exact `prior_rehearsal`
object from its authorization. `schema_version` is integer `1`; `edge` is
exactly `{"from":"freeze5","to":"tw4","via":"bridge"}`; `purpose` is
exactly `bridge-transition`; and `execution_class` names the variant. The
isolated variant forbids `prior_rehearsal`. The receipt's ordinary
`authorization.sha256` must equal
`deployment_authorization_sha256`, and the recomputed unsigned nonmigration
core must equal `expected_active_receipt_core_sha256`.

A bridge stage classification is exactly
`{"outcome":"authorized-bridge-transition","reason":"exact-bridge-transition-authorization"}`
and otherwise uses the complete-control artifact/preimage program. Its
activation intent keeps transaction class `control-set-maintenance` and adds
exactly one class-shaped `bridge_transition` object. Its common field set is
exactly `execution_class`,
`maintenance_transaction_sha256`, `deployment_authorization_sha256`,
`transition_authorization_sha256`, `expected_active_receipt_core_sha256`,
`bridge_identity_sha256`, `release_manifest_sha256`, and
`endpoint_projection_sha256`. The isolated variant contains exactly that set
and forbids `prior_rehearsal`; the live variant contains exactly that set plus
the authorization's exact `prior_rehearsal`. The activation transaction ID
covers that object.
Every bridge journal generation carries it unchanged. Ordinary complete-control
intents and journals forbid it. Recovery requires equality among the original
bridge request, verified stage evidence, receipt projection, intent, and live
journal before mutation.

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
   controller. Recovery reconstructs B1-module `DeploymentRequest`,
   `SourceEvidence`, and `BridgeTransitionRequest` objects from the original
   `ActivationRequest`'s exact primitive raw bytes without dereferencing its
   candidate or external paths. It obtains manifest and transition authority
   only from the verified stage and never reads the external candidate,
   manifest, or authorization source.
5. Acceptance leaves a schema-2 `task-witness-deployment-receipt-v2` TW4 active
   receipt with the closed bridge-migration projection and the exact retained
   v2-TW4/v1-B1/v1-F5 chain. Rejection restores exact B1 and removes only
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
- terminal result retention, journal unlink, and exact retained-result
  reconciliation; and
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

Canonical JSON is exactly the UTF-8 result of `json.dumps(value,
allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)`;
it has no trailing newline. Decoding rejects duplicate keys, nonfinite numbers,
floating-point values, strings that cannot be re-encoded without a surrogate,
and any raw spelling that differs from those bytes. Every schema integer is a
nonnegative non-Boolean integer no greater than `18446744073709551615` unless
this document imposes a smaller or positive range. Every SHA-256 is 64
lowercase hexadecimal characters.

A raw-artifact digest is SHA-256 of the exact raw bytes. A projection digest is
SHA-256 of the canonical JSON bytes for the named projection. A document's
`content_sha256` is SHA-256 of the canonical root object after removing only
`content_sha256`. The complete raw digest of a receipt or manifest is SHA-256
of the final canonical document including `content_sha256`; that digest is
used by later artifacts and is not written into the document itself. The
existing `path-utf8-nul-sha256-hex-nul-v1`, Git-object, and
`task-witness-plugin-subtree-v1` framings remain unchanged where their
contracts name them.

The platform profile and runtime-closure evidence are each at most 1,048,576
raw bytes. The runtime document retains the live parser maxima of 16 roots,
100,000 entries, 32 directory levels, 1,073,741,824 bytes per observed regular
file, 4,294,967,296 aggregate regular-file bytes, a 1,023-byte symlink target,
256 target components, and 32 followed symlink hops. There are exactly three
system tools, 16 suite-inventory entries, and 15 applicable receipt results per
target. Every absolute input or retained live path is at most 4,096 UTF-8 bytes
before JSON escaping.

Admission includes encoded-size preflight, not merely successful JSON decode.
The runner constructs retained projections incrementally with checked
addition, and rejects before suite execution if any limit would be exceeded.
In particular:

- the complete canonical system-tool observation array is at most 8,388,608
  bytes;
- the complete canonical runtime-closure observation object is at most
  41,200,000 bytes;
- each canonical candidate, bridge-history, and suite-inventory object under
  `observations.inputs` is at most 65,536 bytes;
- the canonical platform and runtime objects under `observations.inputs` are
  at most 9,500,000 and 41,300,000 bytes respectively;
- the complete canonical `observations` value is at most 51,500,000 bytes;
- canonical `rendered_shim` and `suite_results` values are each at most 65,536
  bytes; and
- the complete host receipt is at most 67,108,864 bytes (64 MiB).

The runtime-observation limit covers every parser-admitted v1 runtime evidence,
not just expected installations. With all nine `stat` scalars at the maximum
20-digit unsigned value, the largest fixed entry encoding is 400 bytes and a
root binding is 282 bytes. The 100,000 entries contribute at most 40,000,000
bytes, 16 roots at most 4,512 bytes, array separators at most 100,014 bytes,
and every nonempty encoded path and symlink-target payload appears once in the
at-most-1,048,576-byte evidence. Allowing another 512 bytes for the observation
contract and member framing yields 41,153,614 bytes, below the 41,200,000-byte
cap. Filesystem-dependent system-tool resolution has no equivalent bound in
the profile document, so its 8,388,608-byte projection cap is a deliberate
admission condition: crossing it rejects before a suite or receipt can be
created.

The complete-receipt cap is also compositional. The encoder checks these
canonical root-value budgets: 4,096 bytes each for
`qualification_candidate` and `candidate_closure`; 16,384 for
`bridge_history`; 4,096 for `suite_inventory`; 2,113,536 for `platform` (the
profile plus the one duplicated credential projection); 1,114,112 for
`runtime`; 65,536 each for `rendered_shim` and `suite_results`; and 51,500,000
for `observations`. All remaining fixed scalar values, root keys, separators,
and `content_sha256` receive a 16,384-byte allowance. Their conservative sum is
54,903,776 bytes, leaving 12,205,088 bytes beneath the 64-MiB receipt cap. Thus
every input admitted through projection preflight has room for a canonical
receipt; the encoder still independently enforces both the member caps and the
whole-document cap.

A detached manifest remains at most 1,048,576 raw bytes. Every raw-input and
output parser enforces its byte limit before unbounded allocation or recursive
validation. A component that crosses its cap is a qualification failure, not a
truncated projection, omitted observation, or permission to raise the cap at
runtime.

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
`task-witness-tw4-host-qualification-receipt-v1`. Its exact invocation is:

```text
run_task_witness_qualification.py \
  --candidate-root CANDIDATE_ROOT \
  --runtime-executable RUNTIME_EXECUTABLE \
  --runtime-closure-evidence RUNTIME_CLOSURE_EVIDENCE \
  --platform-profile PLATFORM_PROFILE \
  --receipt-output RECEIPT_OUTPUT
```

Each flag occurs exactly once in that order. Abbreviations, joined
`--option=value` forms, duplicate flags, positionals, omitted inputs, and extra
inputs reject. Every operand is an absolute normalized path with one leading
slash and no `..` component. The four inputs exist with the required kind and
no symlink component. `RECEIPT_OUTPUT` is an absent file beneath an existing
descriptor-opened directory and is disjoint from the candidate and runtime
closure.

The candidate root is an exact Git checkout owned outside the qualification
user's write authority. The runner derives its commit and tree with the
recorded canonical Git tool, proves the reviewed closure, and revalidates the
root, Git identity, inventory, and every executed input before and after the
suite. A dirty, replaced, writable, or non-Git candidate fails before receipt
publication.

Each receipt binds:

- the frozen qualification-candidate commit, tree, source-shape record, and
  computed control/source/test closure;
- one exact `bridge_history` projection, defined below;
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

The receipt root contains exactly:

```text
schema_version
contract
qualification_candidate
candidate_closure
bridge_history
suite_inventory
target
platform
runtime
rendered_shim
observations
suite_results
disposition
content_sha256
```

`schema_version` is integer `1`; `contract` is
`task-witness-tw4-host-qualification-receipt-v1`; `target` is exactly
`macos-arm64` or `linux-x86_64`; and `disposition` is exactly `qualified`.

`qualification_candidate` contains exactly `repository_id`, `commit_sha1`,
`tree_sha1`, `plugin_subtree_sha256`, and `suite_inventory_sha256`. It is the
same projection later copied into the detached release manifest.
`repository_id` is `nisavid/agents`; commit and tree are the exact lowercase
40-character Git object IDs; `plugin_subtree_sha256` uses the existing
`task-witness-plugin-subtree-v1` framing; and `suite_inventory_sha256` is the
raw digest of the exact candidate-owned inventory.

`candidate_closure` contains exactly `contract`, `entry_count`,
`projection_sha256`, and `source_shape_sha256`. `contract` is
`task-witness-qualification-candidate-closure-v1`.
`source_shape_sha256` is the raw digest of
`release/task-witness/source-shape-review.json`. `projection_sha256` is the
digest of canonical JSON for an object containing exactly `contract` and
`entries`, with the same contract value. The path-sorted `entries` list is the
recursively expanded effective Task Witness release closure. Its roots are
exactly the complete `plugins/task-witness` root,
`release/public-release-runtime-packages.json`,
`release/task-witness/public-release-registration.json`, the
convention-derived `scripts/validate_task_witness.py`, the 13 registered
Task Witness support roots below, and the one automatic shared dependency
`scripts/agent_plugins_standard.py`:

- `docs/superpowers/specs/2026-07-27-task-witness-canonical-client-design.md`;
- `docs/superpowers/specs/2026-08-12-task-witness-tw4-migration-and-qualification-design.md`;
- `release/task-witness/migration`;
- `release/task-witness/source-shape-review.json`;
- `release/task-witness/tw4-bridge-identity.json`;
- `release/task-witness/tw4-bridge-provenance.json`;
- `release/task-witness/tw4-suite-inventory.json`;
- `scripts/run_task_witness_qualification.py`;
- `scripts/run_task_witness_qualification_suite.py`;
- `tests/plugins/task_witness_client`;
- `tests/plugins/task_witness_deployment`;
- `tests/test_task_witness_package.py`; and
- `tests/test_task_witness_qualification.py`.

No other generic public-release support path is part of `candidate_closure`.
In particular, the unrelated `COMMON_SUPPORT_PATHS` and Phase 7 closure are
excluded. Each directory root contributes its own directory entry and every
descendant exactly once; each file root contributes its file entry exactly
once. A directory entry contains exactly `kind` and repository-relative
`path`. A file entry contains exactly `kind`, `length`, Git `mode`,
repository-relative `path`, and raw `sha256`. Kinds are `directory` and
`file`; file modes are exactly `100644` or `100755`; symlinks, submodules,
special files, aliases, overlapping roots, duplicate paths, and unregistered
descendants reject. `entry_count` is the exact list length. This external
projection binds the complete Task Witness control, source, and test closure
without placing a self-digest in the candidate.

`bridge_history` is an object containing exactly
`bridge_identity_sha256`, `bridge_provenance_sha256`, `freeze5`, and `bridge`.
The two digest fields are SHA-256 of the exact raw candidate-owned bridge
identity record and provenance proof. `bridge_provenance_sha256` also equals
the identity record's `provenance_sha256`. `freeze5` and `bridge` each contain
exactly `repository_id`, `commit_sha1`, `tree_sha1`,
`plugin_subtree_sha256`, `controller_sha256`, `policy_sha256`,
`client_sha256`, and `source_mode`; each is byte-for-byte the canonical JSON
projection of the correspondingly named identity object in the validated
bridge identity record. The runner independently validates the record, proof,
historical snapshots, and reconstructed plugin subtrees before copying this
projection into the receipt. No TW4 identity or receipt digest appears inside
`bridge_history`.

`suite_inventory` contains exactly `path`, `length`, `sha256`,
`counts_sha256`, `entries_sha256`, `entry_count`, and
`expected_count_total`. `path` is exactly
`release/task-witness/tw4-suite-inventory.json`; `sha256` is the raw inventory
digest; and every remaining value equals the independently parsed inventory.

`platform` contains exactly `profile_sha256`, `profile`, `credential_state`,
and `system_tool_observation_sha256`. `profile` is the complete parsed
`task-witness-platform-profile-v1` object, including its own
`content_sha256`; `profile_sha256` is the raw digest of its exact canonical
bytes. The profile's target equals the receipt target. Its passwd user, home,
groups, filesystem semantics, native evidence, and ordered system-tool
inventory are retained without projection loss.

`credential_state` is target-discriminated. For macOS it contains exactly
`kind`, `real_uid`, `effective_uid`, `real_gid`, `effective_gid`,
`supplementary_gids`, and `issetugid`; `kind` is
`darwin-issetugid-v1` and `issetugid` is false. For Linux it contains exactly
`kind`, `real_uid`, `effective_uid`, `saved_uid`, `real_gid`, `effective_gid`,
`saved_gid`, `supplementary_gids`, and `capabilities`; `kind` is
`linux-res-id-capabilities-v1`; and `capabilities` contains exactly `ambient`,
`bounding`, `effective`, `inheritable`, and `permitted`, all integer zero. All
observed identities equal the effective passwd profile, the UID is nonzero,
and the supplementary group list is sorted and unique.

Every live `stat` object used below contains exactly `device`, `inode`, `mode`,
`uid`, `gid`, `nlink`, `size`, `mtime_ns`, and `ctime_ns`. Its `mode` is the
full integer `st_mode`; all values are nonnegative non-Boolean integers. A live
regular-file binding contains exactly `path`, `stat`, `length`, and `sha256`.
A live directory binding contains exactly `path` and `stat`. Binding paths are
normalized absolute paths with no symlink component. A regular-file binding's
`stat` describes a regular file, `stat.size` equals `length`, and `sha256` is
the digest of exactly those bytes. A directory binding's `stat` describes a
directory.

`system_tool_observation_sha256` is the digest of the canonical bare array of
the profile's three tools in profile order. Each item contains exactly `id`,
`invoked_path`, `resolved_path`, `resolution`, and `file`. `resolution` is the
absolute-path-sorted unique array of objects containing exactly `path` and
`stat` for every observed pathname component and symlink hop. `file` is the
live regular-file binding for `resolved_path`. The array IDs and paths equal
the profile, and the file's length, digest, UID, GID, and permission bits equal
the profile's corresponding tool fields. The complete array is retained as
`observations.inputs.platform.system_tools` below. The independently captured
first and last arrays must be byte-identical.

`runtime` contains exactly `evidence_sha256`, `evidence`,
`main_executable_observation`, and `closure_observation_sha256`. `evidence` is
the complete parsed `task-witness-runtime-closure-evidence-v1` object,
including its own `content_sha256`; `evidence_sha256` is the raw digest of its
exact canonical bytes. `main_executable_observation` contains exactly `path`,
`length`, `sha256`, `uid`, `gid`, `mode`, and `nlink` and equals the evidence's
main-executable `path`, `length`, `sha256`, `uid`, `gid`, and `mode` fields,
with the observed positive link count added. The evidence separately retains
the implementation and exact version. The runtime CLI operand equals that
path.
`closure_observation_sha256` is the digest of canonical JSON for an object
containing exactly `contract`, `roots`, and `entries`; `contract` is
`task-witness-runtime-closure-observation-v1`. `roots` is the evidence-order
array of live directory bindings. `entries` is evidence order, which is
absolute-path order. A regular-file item contains exactly `kind`, `path`,
`stat`, `length`, and `sha256`; a directory item contains exactly `kind`,
`path`, and `stat`; and a symlink item contains exactly `kind`, `path`, `stat`,
and `target`. Kinds are exactly `regular-file`, `directory`, and `symlink`.
Every path, kind, length, digest, permission mode, owner, and symlink target
equals the corresponding evidence entry. The complete observed inventory
equals the evidence inventory and remains outside the qualification user's
write authority. The complete object is retained as
`observations.inputs.runtime.closure_observation` below.

`observations` contains exactly `contract`, `inputs`, `before`, and `after`.
`contract` is `task-witness-tw4-host-input-stability-v1`. `inputs` retains one
complete canonical copy of the state that the runner independently captures
before and after suite execution. It contains exactly `candidate`,
`bridge_history`, `suite_inventory`, `platform`, and `runtime`:

- `candidate` contains exactly `contract`, `root_path`, `root`,
  `qualification_candidate`, `candidate_closure`, and `worktree`. Its contract
  is `task-witness-tw4-candidate-observation-v1`; `root_path` is the normalized
  candidate-root operand; `root` is its live directory binding; the two
  identity objects are byte-identical to the receipt objects of those names;
  and `worktree` contains exactly `tracked` and `untracked`, with values
  `clean` and `none`.
- `bridge_history` contains exactly `contract`, `bridge_history`,
  `identity_file`, and `provenance_file`. Its contract is
  `task-witness-tw4-bridge-history-observation-v1`; its history is
  byte-identical to the receipt object; and its two live regular-file bindings
  have the exact absolute candidate-root paths ending in
  `release/task-witness/tw4-bridge-identity.json` and
  `release/task-witness/tw4-bridge-provenance.json` respectively. Their lengths
  and digests equal the candidate files' exact raw bytes.
- `suite_inventory` contains exactly `contract`, `file`, and
  `suite_inventory`. Its contract is
  `task-witness-tw4-suite-inventory-observation-v1`; its live regular-file
  binding has the exact absolute candidate-root path ending in
  `release/task-witness/tw4-suite-inventory.json`; and its summary is
  byte-identical to the receipt's `suite_inventory`. The file length and digest
  equal the canonical candidate inventory bytes and receipt summary.
- `platform` contains exactly `contract`, `profile_file`, `home`,
  `credential_state`, and `system_tools`. Its contract is
  `task-witness-tw4-platform-observation-v1`; `profile_file` is the live
  regular-file binding for the normalized external platform-profile operand;
  `home` is the live directory binding for the profile's passwd home;
  `credential_state` is byte-identical to the receipt value; and `system_tools`
  is the complete ordered observation array defined above. The profile-file
  bytes are exactly the canonical embedded `platform.profile`, so its path,
  length, and digest independently close the otherwise-external input.
- `runtime` contains exactly `contract`, `evidence_file`,
  `main_executable_observation`, and `closure_observation`. Its contract is
  `task-witness-tw4-runtime-observation-v1`; `evidence_file` is the live
  regular-file binding for the normalized external runtime-evidence operand;
  `main_executable_observation` is byte-identical to the receipt value; and
  `closure_observation` is the complete object defined above. The evidence-file
  bytes are exactly the canonical embedded `runtime.evidence`, so its path,
  length, and digest independently close the otherwise-external input.

`before` and `after` each contain exactly `candidate_sha256`,
`bridge_history_sha256`, `suite_inventory_sha256`, `platform_sha256`, and
`runtime_sha256`. Every value is SHA-256 of the canonical corresponding object
under `inputs`. The runner constructs a complete before object, independently
constructs a complete after object after every suite finishes, requires their
canonical bytes to be identical, retains that one common value as `inputs`,
and records the five digests computed separately from each capture in `before`
and `after`. Both digest objects must be byte-identical and every digest must
recompute from `inputs`; an asserted digest without its input object rejects.

Additionally, `platform.system_tool_observation_sha256` equals the digest of
`inputs.platform.system_tools`, and `runtime.closure_observation_sha256` equals
the digest of `inputs.runtime.closure_observation`. The embedded platform and
runtime documents canonically reproduce their raw digest fields and external
file bindings. The candidate root reproduces the candidate closure, bridge
files, and inventory bindings. The final validator can therefore recompute
every host-input observation digest from the receipt and its explicit
candidate root without access to the former external profile or evidence
paths. Candidate observations bind the no-replacement Git identity, clean
tracked and untracked state, immutable root mapping, plugin subtree, source
shape, and qualification closure. A recheck count or unbound Boolean claim
cannot substitute for the two independently captured states.

`rendered_shim` contains exactly `contract`, `template`,
`runtime_executable_path`, `client`, and `shim`. `contract` is
`task-witness-rendered-shim-observation-v1`. `template` contains exactly
`path`, `length`, and `sha256`, with path
`plugins/task-witness/client/task_witness_shim.sh.in`. `client` and `shim`
each contain exactly `path`, `length`, `sha256`, `uid`, `gid`, `mode`, and
`nlink`. Their paths are the exact absolute paths in the fixed install
scenario; both are current-user, single-link files with mode `0500`; the client
digest equals the candidate client; and `runtime_executable_path` equals the
qualified runtime. The runner recomputes the rendered bytes from those three
inputs and requires the result to equal `shim`.

Receipt `length`, UID, GID, permission `mode`, link-count, exit-status, and
count fields are nonnegative non-Boolean integers. Permission `mode` fields are
`stat.S_IMODE` values, so the fixed installed client and shim mode is integer
`320` (`0o500`); Git-tree modes remain the strings `100644` and `100755`.

The runner creates one private workspace and passes its absolute path to the
suite driver through the fixed runner-owned
`TASK_WITNESS_QUALIFICATION_WORKSPACE` environment variable. The inventory
cannot select or alter this value. The `literal-rendered-shim` branch uses only
the passwd profile's canonical
`HOME/.local/libexec/task-witness/client/task_witness_client.py` and
`HOME/.local/libexec/task-witness/task-witness` installed paths, directly
executes that shim, and leaves the fixed scenario until the outer runner
completes its independent observation. It writes the same canonical
`rendered_shim` object create-new at
`literal-rendered-shim-observation.json` beneath the runner workspace and to
its captured detail stdout. The outer runner descriptor-reads the sidecar,
independently reconstructs the object from the candidate, runtime, passwd
root, and observed installed files, and requires all three canonical byte
identities to agree. It then requires the object's length and digest to equal
that suite result's `detail_stdout_length` and `detail_stdout_sha256`, removes
the fixed scenario, and removes the private workspace after receipt publication
or failure. The workspace is an internal observation channel, not a sixth
runner input, an alternative installation root, or an inventory-owned
environment choice.

`suite_results` is the exact target-applicable subsequence of the 16 inventory
entries in inventory order. It has 15 items on each target. Each item contains
exactly `id`, `expected_count`, `expected_terminal`, `process`, and `result`.
The expected values equal the inventory. `process` contains exactly
`exit_status`, `stdout_length`, `stdout_sha256`, `stderr_length`, and
`stderr_sha256`; successful values require exit status zero, exact canonical
suite-result bytes on stdout, and zero-byte stderr. `result` is the exact
parsed `task-witness-tw4-suite-result-v1` object. Its ID, count, and terminal
equal the inventory; its bounded detail stream lengths and digests are retained
unchanged. A missing, extra, reordered, wrong-target, reused, skipped,
non-successful, noncanonical, or count-disagreeing result rejects.

A host receipt makes no claim about the other host, review independence,
public-release eligibility, or final promotion identity.

### Product-attested Sol High review evidence

The accepted cooperative trust model uses existing owners:

- Rolecasting owns model-selection and dispatch-topology policy. Its portable
  vocabulary separates product family and surface, relationship, ownership,
  transport, and assurance from model choice;
- initial portable support covers ChatGPT Codex and Codex CLI/TUI as distinct
  Codex surfaces. Claude Code and Claude Desktop follow together, then Cursor
  and Cursor Agent;
- Rolecasting's registered validator preserves product-attested,
  controller-observed, and self-reported evidence without promotion. A
  surface-specific adapter may issue receipts only after its exact owning
  integration is qualified and registered;
- every dispatch freezes a minimum for target, model, topology, authority, and
  execution-result assurance before launch, and observed evidence below any
  corresponding minimum rejects;
- this publication profile accepts only a ChatGPT Codex child using the native
  tool transport with product-attested target, model, topology, effective
  authority, and execution result. Controller-observed evidence remains useful
  for ordinary delegation but cannot satisfy this gate;
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

Current controller-observed or bootstrap review records may inform
implementation review. They do not open public release. After Rolecasting and
Tricritical's canonical issuer and validator chain is installed, a fresh
product-attested ChatGPT Codex Sol High re-review of the same unchanged
qualification candidate is mandatory. A degraded,
incomplete, drifted, substituted-model, or self-adjudicated review cannot enter
the release manifest.

### Detached release manifest

The release coordinator creates an external canonical manifest with contract
`task-witness-tw4-release-manifest-v1`. `validate_task_witness.py` consumes it
only in final-release mode.

The root contains exactly:

```text
schema_version
contract
qualification_candidate
targets
bridge_history
canonical_review_evidence_sha256
final_public_release
migration_edge
promotion_delta_sha256
disposition
content_sha256
```

`schema_version` is integer `1`; `contract` is
`task-witness-tw4-release-manifest-v1`; and `disposition` is
`release-qualified`. `qualification_candidate` is the exact five-field
projection from both host receipts. `targets` contains exactly
`linux-x86_64` and `macos-arm64`; each value is the raw complete-receipt digest
for one distinct, independently validated receipt whose target equals the map
key. `bridge_history` is byte-identical in the two receipts and manifest.
`final_public_release` contains exactly `commit_sha1` and `tree_sha1`.
`migration_edge` contains exactly `from`, `source_mode`, `to`, and `successor`
and equals
`{"from":"freeze5","source_mode":"harness_snapshot","to":"bridge","successor":"tw4"}`.

`canonical_review_evidence_sha256` is the raw digest of the one canonical
Rolecasting/Tricritical evidence bundle. The manifest does not copy or invent
that authority's model, topology, execution, adjudication, or verification
schema; the final validator passes the exact bundle to its canonical validator
and requires every accepted identity and terminal claim. The two target
digests transitively bind the exact migration suite records. The final
validator extracts and compares those records rather than duplicating them in
the manifest. No review subdocument, suite result, host observation, future
transition authorization, rehearsal result, or final deployment receipt is an
additional manifest field.

The manifest binds:

- the frozen qualification-candidate identity and suite-inventory digest;
- a closed target map containing exactly one macOS arm64 receipt digest and one
  Linux x86_64 receipt digest;
- one exact `bridge_history` projection that is byte-identical in both host
  receipts and in the manifest and that the final validator independently
  validates against the candidate-owned identity record, provenance proof,
  historical snapshots, and reconstructed F5/B1 plugin subtrees;
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

`promotion_delta_sha256` is the digest of canonical JSON for an object
containing exactly `contract` and `entries`. `contract` is
`task-witness-tw4-promotion-delta-v1`. `entries` is this exact path-sorted
projection:

```json
[
  {
    "after_mode": "100644",
    "after_sha256": "<final marketplace sha256>",
    "before_mode": "100644",
    "before_sha256": "<candidate marketplace sha256>",
    "path": ".claude-plugin/marketplace.json"
  },
  {
    "after_mode": "100644",
    "after_sha256": "<final registration sha256>",
    "before_mode": "100644",
    "before_sha256": "<candidate registration sha256>",
    "path": "release/task-witness/public-release-registration.json"
  }
]
```

The validator derives all four byte digests and both modes from the two Git
trees. It also parses both before/after documents and requires exactly the
declared eligibility flip and marketplace addition; hashes alone do not
authorize an arbitrary two-file change.

No executable, policy, test, suite inventory, source shape input, documentation
contract, or other semantic byte may change. The final validator independently
reconstructs the allowed delta, verifies both Git identities and every bound
artifact, and rejects any extra change. A semantic repair creates a new
qualification candidate and invalidates both host receipts, review evidence,
the detached manifest, and every unconsumed transition authorization derived
from it. Any qualification-candidate, promotion-delta, or final-release
identity change requires fresh dependent artifacts and a fresh transaction
identity. No digest may be patched forward.

### Evidence creation and create-new publication

Evidence is created in this acyclic order:

1. Freeze the ineligible, undiscoverable qualification-candidate commit and
   tree, source shape, suite inventory, bridge evidence, runner, and driver.
2. Freeze each external native platform profile and runtime-closure evidence.
3. Run and independently validate the macOS receipt create-new.
4. Run and independently validate the Linux receipt create-new.
5. Produce and validate canonical Rolecasting/Tricritical evidence for the
   unchanged qualification candidate.
6. Construct the exact two-path public-release successor commit and tree.
7. Create the detached manifest from those already immutable candidate, host,
   review, promotion, and final identities.
8. Run final-release validation without changing state.
9. Use the validated manifest for exact-final isolated rehearsal and only then
   for live transition authorization.

No artifact binds a future digest. The host receipts contain no review or final
identity; the review contains no final identity; the manifest contains no
transition authorization or final deployment receipt; and neither exact-final
authorization is inserted back into an already validated manifest.

Receipt and manifest publication descriptor-opens the external parent without
symlink traversal, creates a private mode-`0600` temporary regular file with
`O_CREAT|O_EXCL|O_NOFOLLOW`, writes and synchronizes the complete canonical
bytes, verifies its identity, and creates the absent final name with one
create-new hard link. It then removes the temporary name, synchronizes the
parent, reopens the final file without following a symlink, and requires exact
bytes, current ownership, mode `0600`, and link count one. A concurrent or
preexisting final name rejects without replacement.

No validation or other prepublication failure creates the final path. A crash,
unlink failure, parent-synchronization failure, or final-recheck failure after
the create-new link may leave a complete but unaccepted output. Any nonzero
invocation makes every output from that invocation unaccepted and requires
explicit validation or cleanup; the runner never reports that such bytes are a
receipt. This is the portable create-new guarantee and does not claim
impossible rollback after the final link becomes visible.

## Validation And Failure Rules

The package-only forms validate only an ineligible Task Witness candidate or
package and do not consume TW4 evidence:

```text
validate_task_witness.py
validate_task_witness.py ROOT
```

They are invalid for a promoted, production-eligible Task Witness tree. A
promoted tree must use the candidate-aware `--final-release` form below; the
ordinary package form intentionally rejects it.

The validator has these three exact release-evidence modes:

```text
validate_task_witness.py CANDIDATE_ROOT --source-stage
validate_task_witness.py CANDIDATE_ROOT --qualification HOST_RECEIPT
validate_task_witness.py FINAL_ROOT --final-release \
  --candidate-root CANDIDATE_ROOT \
  --release-manifest MANIFEST \
  --macos-receipt MACOS_RECEIPT \
  --linux-receipt LINUX_RECEIPT \
  --review-evidence REVIEW_EVIDENCE
```

Argument order and spelling are exact. Abbreviations, joined option values,
duplicates, mixed modes, omitted operands, and extra operands reject. Every
evidence path is absolute, external, descriptor-opened without symlink
components, and a bounded canonical regular file. Candidate and final roots
are distinct immutable Git checkouts; the manifest is outside both trees.

- source-stage validation checks the ineligible copied snapshot, suite
  inventory, schemas, registration, and the exact registered path-and-byte
  inventory without requiring external receipts. The generic source-stage
  snapshot has no Git metadata, so this mode establishes neither candidate
  commit or tree identity nor file modes for `candidate_closure`. Native-host
  qualification independently derives the same registered closure from the
  frozen candidate Git tree and binds its exact Git identities and modes;
- qualification validation checks one host receipt against the exact candidate
  and never changes eligibility; and
- final-release validation requires the detached manifest, two distinct native
  receipts, canonical Sol High evidence, and exact promotion delta.

The prepared generic public-release validator accepts and forwards the five
Task Witness final-release operands. When Task Witness is production-eligible,
its production contract dispatch is the exact final-release invocation above,
not the ordinary package-only `VALIDATOR ROOT` form. Source-stage dispatch
remains the candidate-root plus `--source-stage` form. Generic source-stage
mode rejects final evidence, and generic production mode requires all five
Task Witness operands together. This keeps the unchanged candidate source-shape
record authoritative while validating the two-path final successor.

The five generic public-release operands are exactly:

```text
--task-witness-candidate-root CANDIDATE_ROOT
--task-witness-release-manifest MANIFEST
--task-witness-macos-receipt MACOS_RECEIPT
--task-witness-linux-receipt LINUX_RECEIPT
--task-witness-review-evidence REVIEW_EVIDENCE
```

They occur once in that order among the prepared production arguments and are
forwarded without path rewriting. Their option names are unavailable in the
Task Witness package-only and source-stage validator grammars.

The validator rejects target duplication, missing target, receipt reuse,
candidate or inventory disagreement, runtime/profile/shim drift, incomplete or
degraded review, noncanonical artifacts, unknown bridge identity, changed
semantic bytes, an incomplete or disagreeing Git-object provenance proof, stale
manifest, or a manifest inside the candidate tree.

Separately, exact B1 rejects a stale or disagreeing transition authorization
before staging or recovery mutation. TW4 controller and client reject a stale,
unknown, or inconsistent retained migration projection. Neither operation is a
final-release-validator responsibility.

Qualification and manifest validation are read-only. They do not install,
publish, mutate Git, contact a provider, create users, or infer missing
evidence.

The first public REDs establish these interfaces without claiming an unfrozen
release digest:

1. The host-receipt parser accepts one complete exact-shape v1 fixture and
   rejects every missing, extra, mistyped, Boolean-integer, noncanonical, and
   content-digest mutation. It also rejects an omitted or mutated retained
   observation input, any before/after digest not recomputed from that input,
   unequal independently captured digest sets, every component one byte over
   its canonical cap, and a receipt one byte over 64 MiB. Boundary fixtures
   prove checked-addition behavior and successful encoding at each exact cap.
2. Candidate-closure projection tests reject path, byte, mode, registration,
   source-shape, suite-inventory, plugin-subtree, Git-replacement, dirty-state,
   and immutability drift.
3. Target/profile/credential tests reject every cross-platform variant,
   passwd, group, retained-credential, capability, filesystem, and system-tool
   disagreement, including profile-file path/stat/byte drift and any mutation
   of the retained complete system-tool observation array.
4. Runtime and platform tests require their embedded canonical documents, raw
   digests, external-file path/stat bindings, passwd home, credential state,
   executable binding, retained complete observed closure, per-domain capture
   digests, and before/after identities to agree. The qualification validator
   recomputes every observation digest without reopening either external input.
   Maximal-entry/root arithmetic proves every parser-admitted runtime document
   fits the closure-observation cap; an oversized filesystem-derived system-tool
   projection rejects during preflight before any suite executes.
5. Suite-record tests require the exact target-applicable ordered subsequence,
   inventory counts, zero exit status, canonical result stdout, empty process
   stderr, and bounded captured-detail identities.
6. The literal-shim test requires the fixed private-workspace channel, exact
   installed client and runtime, reproducible rendered bytes, direct execution,
   and equality between the canonical observation and captured detail digest.
7. Receipt publication tests preserve preexisting and concurrent outputs,
   reject unsafe parent or temporary state, and never accept output from a
   nonzero invocation.
8. The detached-manifest parser accepts the exact B1-owned v1 shape and rejects
   receipt reuse, target mismatch, bridge disagreement, review-bundle drift,
   migration-result drift, stale candidate or final identity, and every
   promotion path, mode, byte, or semantic change beyond the exact two.
9. CLI and prepared-release dispatch tests reject abbreviations, duplicates,
   mixed modes, incomplete evidence sets, ordinary package dispatch for the
   final Task Witness tree, and final evidence supplied to source-stage mode.

End-to-end successful host receipt emission remains unreachable until those
parser and invariant REDs, all suite selectors and counts, the suite driver,
and both platform verticals have frozen.

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
4. Freeze B1's complete bytes, create the canonical candidate-owned bridge-
   identity record, minimal Git-object provenance proof, and nine historical
   snapshot files from its and F5's exact immutable identities, and replace
   identity-shape fixtures with exact-value, commit/tree proof, and
   reconstructed-subtree regressions.
5. Implement the TW4 candidate's record-derived B1/F5 compiled allowlist and
   exact record and snapshot parity checks, fixed suite-
   inventory parser, single reviewed suite driver, closed ID-to-selector table,
   suite-result parser, host-receipt parser, detached-manifest validator, and
   metadata-only promotion validator. Keep receipt emission deliberately
   unreachable at this step.
6. Add exact retained F5/B1/TW4 controller and client validation plus closed
   retained-result coexistence. Every recovery audit must understand the policy
   epoch and receipt schema for the retained suffix before mutation.
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
